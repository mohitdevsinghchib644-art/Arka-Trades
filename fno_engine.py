"""
Arka Trades · F&O engine (fno_engine.py)

Reads the futures and options history that fno_data.py stores in arka_data/fno/ and answers one question for NIFTY 50,
BANK NIFTY and every F&O stock: what does the positioning data say, and has that reading actually meant anything in the past?

How it stays honest
  1. Every reading (a "signal") is a fixed rule on stored data: open-interest build-up, put-call ratio, implied volatility,
     skew, futures premium, max pain, open-interest walls, who holds what (FII, clients), breadth and trend.
  2. Every signal is tested on the full history: after it fired, what did the price do over the next 5, 10 and 20 trading
     days, compared with a normal day? The test assumes you act at the NEXT day's close, never the same close.
  3. A signal only counts as "proven" when the edge is large against its noise, was seen in enough separate episodes, and
     points the same way in the first half and the second half of the history. Everything else is shown but adds nothing.
  4. The verdict is the sum of the proven signals that are on today. The verdict itself is then checked "walk-forward":
     for every past day it is rebuilt using only data from before that day, and its record is shown next to it.
  5. No random numbers, no AI model, no outside service. Same data in, same answer out.

Used by fno_page.py (the page) and home_page.py (a compact card). Run `python fno_engine.py` to print the report.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent / "arka_data" / "fno"      # next to this file, wherever the app is started from
HORIZONS = (5, 10, 20)
MAIN_H = 10                      # the verdict looks 10 trading days ahead
INDEXES = ("NIFTY", "BANKNIFTY")  # the only index contracts liquid enough to analyse
NAMES = {"NIFTY": "NIFTY 50", "BANKNIFTY": "BANK NIFTY"}
YEAR = 252

# what makes a signal count
PROVEN_T, PROVEN_N = 2.5, 15
LEAN_T, LEAN_N = 2.0, 10
# verdict cut-offs, as a share of the normal 10-day swing
IDX_MILD, IDX_STRONG = 0.15, 0.35
STK_MILD, STK_STRONG = 0.06, 0.15
WF_START, WF_STEP = 2 * YEAR, YEAR // 2      # walk-forward: first test after 2 years, rules re-learnt every 6 months

USE = ["date", "sym", "kind", "spot", "fut", "fut_next", "m_exp", "dte", "lot", "fut_oi", "fut_oi_near", "fut_oi_next", "ce_oi", "pe_oi",
       "max_pain", "call_wall", "put_wall", "iv", "iv_dte", "straddle", "iv_put", "iv_call", "w_exp", "w_max_pain", "w_call_wall", "w_put_wall"]

# key, group, label, what it means, who it applies to ("all" or "idx")
SIGNALS = [
    ("trend_up", "Trend", "Above its 50-day average", "Price is above the average of the last 50 sessions.", "all"),
    ("trend_dn", "Trend", "Below its 50-day average", "Price is below the average of the last 50 sessions.", "all"),
    ("sharp_up", "Trend", "Sharp 5-day rise", "An unusually large gain over the last five sessions.", "all"),
    ("sharp_dn", "Trend", "Sharp 5-day fall", "An unusually large loss over the last five sessions.", "all"),
    ("bu_long", "Open interest", "Long build-up", "Price up and futures open interest up over five sessions: new buyers.", "all"),
    ("bu_short", "Open interest", "Short build-up", "Price down and futures open interest up: new sellers.", "all"),
    ("bu_cover", "Open interest", "Short covering", "Price up while open interest falls: sellers closing out.", "all"),
    ("bu_unwind", "Open interest", "Long unwinding", "Price down while open interest falls: buyers closing out.", "all"),
    ("pcr_hi", "Put-call ratio", "Put-call ratio very high", "Put open interest is unusually large against calls, compared with its own last year.", "all"),
    ("pcr_lo", "Put-call ratio", "Put-call ratio very low", "Call open interest is unusually large against puts, compared with its own last year.", "all"),
    ("iv_hi", "Volatility", "Options expensive", "Implied volatility is in the top 15% of its own last year.", "all"),
    ("iv_lo", "Volatility", "Options cheap", "Implied volatility is in the bottom 15% of its own last year.", "all"),
    ("iv_spike", "Volatility", "Volatility jumped", "Implied volatility rose 20% or more in five sessions.", "all"),
    ("skew_hi", "Volatility", "Puts costly against calls", "Downside protection is unusually expensive against upside calls.", "all"),
    ("skew_lo", "Volatility", "Calls costly against puts", "Upside calls are unusually expensive against downside puts.", "all"),
    ("cal_hi", "Futures premium", "Next-month premium rich", "The next-month future trades at an unusually high premium to the current month.", "all"),
    ("cal_lo", "Futures premium", "Next-month premium thin", "The next-month future trades at an unusually low premium, or a discount.", "all"),
    ("above_pain", "Expiry levels", "Well above max pain", "With expiry near, price sits well above the level where option buyers lose the most.", "all"),
    ("below_pain", "Expiry levels", "Well below max pain", "With expiry near, price sits well below the level where option buyers lose the most.", "all"),
    ("at_call_wall", "Expiry levels", "Just under the call wall", "Price is just below the strike with the most call open interest.", "all"),
    ("at_put_wall", "Expiry levels", "Just above the put wall", "Price is just above the strike with the most put open interest.", "all"),
    ("fii_short", "Who holds what", "FIIs heavily short", "Foreign investors' long share of index futures is in the bottom 15% of the last year.", "idx"),
    ("fii_long", "Who holds what", "FIIs heavily long", "Foreign investors' long share of index futures is in the top 15% of the last year.", "idx"),
    ("fii_add", "Who holds what", "FIIs adding longs", "Foreign investors' long share of index futures rose 4 points or more in five sessions.", "idx"),
    ("fii_cut", "Who holds what", "FIIs adding shorts", "Foreign investors' long share of index futures fell 4 points or more in five sessions.", "idx"),
    ("cli_long", "Who holds what", "Retail heavily long", "Clients' long share of index futures is in the top 15% of the last year.", "idx"),
    ("cli_short", "Who holds what", "Retail heavily short", "Clients' long share of index futures is in the bottom 15% of the last year.", "idx"),
    ("br_hi", "Breadth", "Most F&O stocks in uptrends", "Three in four F&O stocks, or more, are above their 50-day average.", "idx"),
    ("br_lo", "Breadth", "Most F&O stocks in downtrends", "One in four F&O stocks, or fewer, is above its 50-day average.", "idx"),
    ("flow_pos", "Breadth", "Buyers building across stocks", "Far more F&O stocks show long build-up than short build-up.", "idx"),
    ("flow_neg", "Breadth", "Sellers building across stocks", "Far more F&O stocks show short build-up than long build-up.", "idx"),
]
# The model's inputs. key, label, used for the indexes, used for stocks
FACTORS = [
    ("trend", "Trend", 1, 1), ("mom", "20-day momentum", 1, 1), ("rev", "5-day move", 1, 1), ("oiflow", "Open interest with price", 1, 1),
    ("oi", "Open-interest change", 1, 1), ("pcr", "Put-call ratio", 1, 1), ("pcr_d", "Put-call ratio change", 1, 1), ("iv", "Implied volatility", 1, 1),
    ("iv_d", "Volatility change", 1, 1), ("skew", "Put skew", 1, 1), ("cal", "Next-month premium", 1, 1), ("pain", "Distance from max pain", 1, 1),
    ("roll", "Rollover", 0, 1), ("fii", "FII long share", 1, 0), ("fii_d", "FII positioning change", 1, 0), ("cli", "Retail long share", 1, 0),
    ("fii_opt", "FII index options", 1, 0), ("breadth", "Breadth", 1, 0), ("flow", "Build-up balance", 1, 0)]
F_LABEL = {f[0]: f[1] for f in FACTORS}
IDX_F = [f[0] for f in FACTORS if f[2]]
STK_F = [f[0] for f in FACTORS if f[3]]
IDX_LAM, STK_LAM = 1.0, 0.1        # how hard the model is held back from over-fitting (ridge penalty)
MODEL_STEP = 63                    # the model is re-learnt every quarter, only from data before that day
COST = 0.0005                      # cost charged each time the test strategy enters or leaves the market

SIG_KEYS = [s[0] for s in SIGNALS]
SIG_INFO = {s[0]: {"group": s[1], "label": s[2], "note": s[3], "scope": s[4]} for s in SIGNALS}
BUILDUP = {"bu_long": "Long build-up", "bu_short": "Short build-up", "bu_cover": "Short covering", "bu_unwind": "Long unwinding"}

VERDICTS = {2: ("Rally setup", "up"), 1: ("Stay invested", "up"), 0: ("No proven edge", "flat"), -1: ("Reduce risk", "dn"), -2: ("Correction risk", "dn")}
STOCK_VERDICTS = {2: ("Top fifth", "up"), 1: ("Upper", "up"), 0: ("Middle", "flat"), -1: ("Lower", "dn"), -2: ("Bottom fifth", "dn")}


# ───────────── loading ─────────────
def data_signature(root=ROOT) -> tuple:
    """Changes whenever the stored data changes; used to know when to recompute."""
    root = Path(root)
    files = sorted((root / "u").glob("*.csv")) + [p for p in (root / "participants.csv", root / "chain_latest.csv") if p.exists()]
    return tuple((f.name, f.stat().st_size, int(f.stat().st_mtime)) for f in files)


def load_history(root=ROOT) -> pd.DataFrame:
    files = sorted((Path(root) / "u").glob("*.csv"))
    parts = []
    for f in files:
        try:
            parts.append(pd.read_csv(f, usecols=lambda c: c in USE, low_memory=False))
        except Exception:
            continue
    if not parts:
        return pd.DataFrame(columns=USE)
    df = pd.concat(parts, ignore_index=True)
    for c in USE:
        if c not in df.columns:
            df[c] = np.nan
    for c in ("date", "m_exp", "w_exp"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in USE:
        if c not in ("date", "m_exp", "w_exp", "sym", "kind"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64" if c in ("fut", "fut_next") else "float32")   # small, to stay light on memory
    df["sym"] = df["sym"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["date", "fut"])
    df = df[df["fut"] > 0]
    # index contracts other than NIFTY and BANK NIFTY trade too thinly for their numbers to mean anything
    df = df[(df["kind"] != "IDX") | df["sym"].isin(INDEXES)]
    df = df.drop_duplicates(["sym", "date"], keep="last").sort_values(["sym", "date"]).reset_index(drop=True)
    df["sym"], df["kind"] = df["sym"].astype("category"), df["kind"].astype("category")      # names stored once, not on every row
    return df


def load_participants(root=ROOT) -> pd.DataFrame:
    """One row per day: each group's long share of index futures and the FII net position in index options."""
    p = Path(root) / "participants.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(p)
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
        raw = raw.dropna(subset=["date"])
        out = {}
        for who, tag in (("FII", "fii"), ("Client", "cli"), ("Pro", "pro"), ("DII", "dii")):
            w = raw[raw["who"].astype(str).str.upper() == who.upper()].drop_duplicates("date", keep="last").set_index("date")
            tot = (w["fi_long"] + w["fi_short"]).replace(0, np.nan)
            out[f"{tag}_ls"] = w["fi_long"] / tot
            out[f"{tag}_net"] = w["fi_long"] - w["fi_short"]
            if tag == "fii":
                out["fii_opt"] = (w["oi_call_long"] - w["oi_call_short"]) - (w["oi_put_long"] - w["oi_put_short"])
        P = pd.DataFrame(out).sort_index()
        for tag in ("fii", "cli"):
            P[f"{tag}_ls_pct"] = P[f"{tag}_ls"].rolling(YEAR, min_periods=120).rank(pct=True)
            P[f"{tag}_ls_d5"] = P[f"{tag}_ls"] - P[f"{tag}_ls"].shift(5)
        return P
    except Exception:
        return pd.DataFrame()


def load_chain(root=ROOT) -> pd.DataFrame:
    p = Path(root) / "chain_latest.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        c = pd.read_csv(p)
        c["exp"] = pd.to_datetime(c["exp"], errors="coerce")
        return c
    except Exception:
        return pd.DataFrame()


# ───────────── features ─────────────
def _grp_roll(D, by, col, n, minp, how):
    r = D.groupby(by, sort=False, observed=True)[col].rolling(n, min_periods=minp)
    r = r.rank(pct=True) if how == "rank" else r.mean() if how == "mean" else r.std() if how == "std" else r.sum()
    return r.reset_index(level=0, drop=True).reindex(D.index)


def prepare(df: pd.DataFrame, P: pd.DataFrame | None = None) -> pd.DataFrame:
    """Adds a clean return series, forward returns, every measure and every signal column (s_<key>) to the history."""
    D = df                                  # worked on in place: the history is large and a second copy would double the memory
    idx = (D["kind"] == "IDX").to_numpy()
    g = D.groupby("sym", sort=False, observed=True)
    pf, pn, pe, pdte, poi = g["fut"].shift(1), g["fut_next"].shift(1), g["m_exp"].shift(1), g["date"].shift(1), g["fut_oi"].shift(1)
    rolled = (D["m_exp"] != pe) & pe.notna()
    # the day after expiry the "near" contract is a new one: compare it with yesterday's price of that same contract
    base = np.where(rolled & (pn > 0), pn, pf)
    ret = D["fut"] / base - 1
    gap = (D["date"] - pdte).dt.days
    # a one-day move this large in a future is a bonus, split or demerger, not a market move: the series is cut there
    cut = (ret.abs() > np.where(idx, 0.12, 0.20)) & ~(gap > 12)
    brk = pf.isna() | (gap > 12) | cut
    D["cut"] = cut.fillna(False)
    D["ret"] = ret.where(~brk)
    D["sid"] = brk.cumsum()
    D["lr"] = np.log1p(D["ret"].fillna(0.0))
    s = D.groupby("sid", sort=False)
    by_sym = lambda c: D.groupby("sym", sort=False, observed=True)[c]
    D["lpx"] = s["lr"].cumsum()
    D["px"] = np.exp(D["lpx"])
    for h in HORIZONS:
        f = np.exp(s["lpx"].shift(-(1 + h)) - s["lpx"].shift(-1)) - 1
        late = (s["date"].shift(-(1 + h)) - D["date"]).dt.days > (2 * h + 12)
        D[f"f{h}"] = f.where(~late)
    D["r5"] = np.exp(D["lpx"] - s["lpx"].shift(5)) - 1
    D["r20"] = np.exp(D["lpx"] - s["lpx"].shift(20)) - 1
    D["ma50"] = _grp_roll(D, "sid", "px", 50, 50, "mean")
    D["swing"] = _grp_roll(D, "sid", "ret", 60, 30, "std") * math.sqrt(10)        # a normal 10-session move, from the last 60 sessions
    # futures open interest: day-to-day change, ignoring the day the contract rolls and any cut day
    doi = np.log(D["fut_oi"].where(D["fut_oi"] > 0) / poi.where(poi > 0))
    D["doi"] = doi.where(~(rolled | brk), 0.0).clip(-1, 1).fillna(0.0)
    D["oi5"] = _grp_roll(D, "sid", "doi", 5, 5, "sum")
    D["pcr"] = (D["pe_oi"] / D["ce_oi"].where(D["ce_oi"] > 0)).where(lambda x: (x > 0.02) & (x < 20))
    D["pcr_pct"] = _grp_roll(D, "sym", "pcr", YEAR, 120, "rank")
    D["iv"] = D["iv"].where((D["iv"] > 0.04) & (D["iv"] < np.where(idx, 0.8, 1.5)))
    D["iv_pct"] = _grp_roll(D, "sym", "iv", YEAR, 120, "rank")
    D["iv_d5"] = D["iv"] / by_sym("iv").shift(5) - 1
    ok_w = lambda c: D[c].where((D[c] > 0.04) & (D[c] < 2.0))
    D["skew"] = ok_w("iv_put") - ok_w("iv_call")
    D["skew_pct"] = _grp_roll(D, "sym", "skew", YEAR, 120, "rank")
    D["cal"] = (D["fut_next"] / D["fut"] - 1).where(D["fut_next"] > 0).where(lambda x: x.abs() < 0.05)
    D["cal_pct"] = _grp_roll(D, "sym", "cal", YEAR, 120, "rank")
    D["pain_gap"] = ((D["fut"] - D["max_pain"]) / D["fut"]).where(D["max_pain"] > 0)
    D["roll"] = (D["fut_oi_next"] / (D["fut_oi_near"] + D["fut_oi_next"]).where(lambda x: x > 0))

    r5t, oit, sharp = np.where(idx, 0.01, 0.02), np.where(idx, 0.04, 0.05), np.where(idx, 0.03, 0.06)
    paint, wallt = np.where(idx, 0.015, 0.04), np.where(idx, 0.0075, 0.015)
    S = {}
    S["trend_up"], S["trend_dn"] = D["px"] > D["ma50"], D["px"] < D["ma50"]
    S["sharp_up"], S["sharp_dn"] = D["r5"] >= sharp, D["r5"] <= -sharp
    S["bu_long"] = (D["r5"] >= r5t) & (D["oi5"] >= oit)
    S["bu_short"] = (D["r5"] <= -r5t) & (D["oi5"] >= oit)
    S["bu_cover"] = (D["r5"] >= r5t) & (D["oi5"] <= -oit)
    S["bu_unwind"] = (D["r5"] <= -r5t) & (D["oi5"] <= -oit)
    S["pcr_hi"], S["pcr_lo"] = D["pcr_pct"] >= 0.85, D["pcr_pct"] <= 0.15
    S["iv_hi"], S["iv_lo"], S["iv_spike"] = D["iv_pct"] >= 0.85, D["iv_pct"] <= 0.15, D["iv_d5"] >= 0.20
    S["skew_hi"], S["skew_lo"] = D["skew_pct"] >= 0.85, D["skew_pct"] <= 0.15
    S["cal_hi"], S["cal_lo"] = D["cal_pct"] >= 0.85, D["cal_pct"] <= 0.15
    near = (D["dte"] >= 2) & (D["dte"] <= 12)
    S["above_pain"], S["below_pain"] = near & (D["pain_gap"] >= paint), near & (D["pain_gap"] <= -paint)
    cw, pw = (D["call_wall"] - D["fut"]) / D["fut"], (D["fut"] - D["put_wall"]) / D["fut"]
    live = D["dte"] >= 2
    S["at_call_wall"] = live & (D["call_wall"] > 0) & (cw >= 0) & (cw <= wallt)
    S["at_put_wall"] = live & (D["put_wall"] > 0) & (pw >= 0) & (pw <= wallt)

    # market-wide series, used only on the index rows
    stk = D[~idx]
    by = stk.assign(a=(stk["px"] > stk["ma50"]).astype(float).where(stk["ma50"].notna()), bl=S["bu_long"][~idx].astype(float),
                    bs=S["bu_short"][~idx].astype(float)).groupby("date").agg(above=("a", "mean"), bl=("bl", "mean"), bs=("bs", "mean"), n=("a", "count"))
    by.loc[by["n"] < 20, "above"] = np.nan
    D["br_above"] = D["date"].map(by["above"])
    D["br_flow"] = D["date"].map(by["bl"] - by["bs"])
    S["br_hi"], S["br_lo"] = D["br_above"] >= 0.75, D["br_above"] <= 0.25
    S["flow_pos"], S["flow_neg"] = D["br_flow"] >= 0.10, D["br_flow"] <= -0.10
    for c in ("fii_ls", "fii_ls_pct", "fii_ls_d5", "cli_ls", "cli_ls_pct", "fii_opt", "pro_ls", "dii_ls"):
        D[c] = D["date"].map(P[c]) if (P is not None and len(P) and c in P.columns) else np.nan
    S["fii_short"], S["fii_long"] = D["fii_ls_pct"] <= 0.15, D["fii_ls_pct"] >= 0.85
    S["fii_add"], S["fii_cut"] = D["fii_ls_d5"] >= 0.04, D["fii_ls_d5"] <= -0.04
    S["cli_long"], S["cli_short"] = D["cli_ls_pct"] >= 0.85, D["cli_ls_pct"] <= 0.15
    for k in SIG_KEYS:
        v = np.asarray(S[k].fillna(False) if hasattr(S[k], "fillna") else S[k], dtype=bool)
        if SIG_INFO[k]["scope"] == "idx":
            v = v & idx
        D["s_" + k] = v

    # everything from here on only needs four significant digits: halve the memory
    D.drop(columns=[c for c in ("ce_oi", "pe_oi", "iv_put", "iv_call", "straddle", "iv_dte", "fut_oi_near", "fut_oi_next", "lr", "doi") if c in D.columns], inplace=True)
    for c in D.columns:
        if D[c].dtype == "float64" and c not in ("fut", "fut_next", "lpx", "px", "ret"):
            D[c] = D[c].astype("float32")

    # the model's inputs, as continuous measures. For stocks each is ranked against the other stocks on the same day;
    # for an index it is compared with that index's own last year. Both use only information available on the day.
    D["n2"] = s["ret"].shift(-2)                      # the return earned the day after acting at the next close
    lp = np.log(D["pcr"])
    D["lp"] = lp
    V = {"trend": D["px"] / D["ma50"] - 1, "mom": D["r20"], "rev": D["r5"], "oiflow": np.sign(D["r5"]) * D["oi5"], "oi": D["oi5"], "pcr": lp,
         "pcr_d": lp - by_sym("lp").shift(5), "iv": D["iv"], "iv_d": D["iv_d5"], "skew": D["skew"], "cal": D["cal"],
         "pain": D["pain_gap"], "roll": D["roll"], "fii": D["fii_ls"], "fii_d": D["fii_ls_d5"], "cli": D["cli_ls"], "fii_opt": D["fii_opt"],
         "breadth": D["br_above"], "flow": D["br_flow"]}
    stk_dates = D.loc[~idx, "date"]
    import warnings
    warnings.simplefilter("ignore", pd.errors.PerformanceWarning)     # columns are added one by one on purpose: it avoids copying the whole table
    for k, v in V.items():
        v = v.replace([np.inf, -np.inf], np.nan).astype("float32")
        D["v_" + k] = v
        x = pd.Series(np.nan, index=D.index, dtype="float32")
        if k in STK_F and (~idx).any():
            x[~idx] = ((v[~idx].groupby(stk_dates).rank(pct=True) - 0.5) * 3.464).astype("float32")
        if k in IDX_F:
            for sym in INDEXES:
                m = (D["sym"] == sym).to_numpy()
                if m.any():
                    r = v[m].rolling(YEAR, min_periods=120)
                    x[m] = ((v[m] - r.mean()) / r.std().replace(0, np.nan)).clip(-3, 3).astype("float32")
        D["x_" + k] = x
    return D


# ───────────── testing a signal ─────────────
def _blocks(pos, h) -> int:
    """How many separate h-day episodes these days fall into (overlapping days are not independent evidence)."""
    return int(len(np.unique(np.asarray(pos) // h))) if len(pos) else 0


def _t(edge, sd, neff):
    return float(edge / (sd / math.sqrt(neff))) if (sd and sd > 0 and neff > 1) else 0.0


def stat_series(sig, fwd, ok, pos, h, half) -> dict | None:
    """One underlying through time: what followed the signal against what followed a normal day."""
    s = sig & ok
    n = int(s.sum())
    if n < 5 or int(ok.sum()) < 60:
        return None
    x, allx = fwd[s], fwd[ok]
    base = float(allx.mean())
    edge = float(x.mean() - base)
    neff = _blocks(pos[s], h)
    halves = []
    for m in (pos < half, pos >= half):
        halves.append(float(fwd[s & m].mean() - fwd[ok & m].mean()) if int((s & m).sum()) >= 3 else float("nan"))
    return {"n": n, "neff": neff, "edge": edge, "mean": float(x.mean()), "base": base, "hit": float((x > 0).mean()),
            "base_hit": float((allx > 0).mean()), "t": _t(edge, float(x.std(ddof=1)) if n > 1 else 0.0, neff), "e1": halves[0], "e2": halves[1]}


def pooled_base(fwd, ok, dcode, nd) -> dict:
    cnt = np.bincount(dcode[ok], minlength=nd).astype(float)
    sm = np.bincount(dcode[ok], weights=fwd[ok], minlength=nd)
    mean = np.divide(sm, cnt, out=np.full(nd, np.nan), where=cnt > 0)
    return {"cnt": cnt, "mean": mean}


def stat_pooled(sig, fwd, ok, dcode, nd, h, half, U) -> dict | None:
    """All stocks together, day by day: did the stocks showing the signal beat the other F&O stocks over the same days?
    Comparing within the same day removes the market's own move; counting days, not rows, keeps the sample honest."""
    s = sig & ok
    n = int(s.sum())
    if n < 30:
        return None
    cnt = np.bincount(dcode[s], minlength=nd).astype(float)
    sm = np.bincount(dcode[s], weights=fwd[s], minlength=nd)
    use = (cnt >= 2) & (U["cnt"] >= 20)
    if int(use.sum()) < 10:
        return None
    d = sm[use] / cnt[use] - U["mean"][use]
    days = np.nonzero(use)[0]
    edge = float(d.mean())
    neff = _blocks(days, h)
    halves = [float(d[m].mean()) if int(m.sum()) >= 3 else float("nan") for m in (days < half, days >= half)]
    ex = fwd[s] - U["mean"][dcode[s]]
    return {"n": n, "neff": neff, "edge": edge, "mean": float(fwd[s].mean()), "base": float(np.nanmean(U["mean"][use])), "hit": float((ex > 0).mean()),
            "base_hit": 0.5, "t": _t(edge, float(d.std(ddof=1)), neff), "e1": halves[0], "e2": halves[1], "days": int(use.sum())}


def grade(st: dict | None) -> str:
    """proven · leaning · unproven · thin (too few cases to say anything)."""
    if not st:
        return "thin"
    same = (not math.isnan(st["e1"])) and (not math.isnan(st["e2"])) and st["e1"] * st["edge"] > 0 and st["e2"] * st["edge"] > 0
    if st["neff"] >= PROVEN_N and abs(st["t"]) >= PROVEN_T and same:
        return "proven"
    if st["neff"] >= LEAN_N and abs(st["t"]) >= LEAN_T and same:
        return "leaning"
    return "thin" if st["neff"] < LEAN_N else "unproven"


def _weights(stats: dict) -> tuple[np.ndarray, np.ndarray]:
    """What each signal adds to the score: its tested edge if proven, half of it if leaning, nothing otherwise.
    Also returns each signal's tier (2 proven, 1 leaning, 0 neither)."""
    w, tier = np.zeros(len(SIG_KEYS)), np.zeros(len(SIG_KEYS), dtype=np.int8)
    for i, k in enumerate(SIG_KEYS):
        g = grade(stats.get(k))
        if g in ("proven", "leaning"):
            tier[i] = 2 if g == "proven" else 1
            w[i] = stats[k]["edge"] * (1.0 if g == "proven" else 0.5)
    return w, tier


def _score(M: np.ndarray, wt: tuple) -> np.ndarray:
    """Sum of the edges of the signals that are on, damped because signals overlap (divided by the root of their count).
    A single "leaning" signal on its own is not enough to move the verdict: it takes one proven signal or two leaning ones."""
    w, tier = wt
    k = (M & (tier > 0)).sum(axis=1)
    gate = ((M & (tier == 2)).sum(axis=1) >= 1) | ((M & (tier == 1)).sum(axis=1) >= 2)
    return np.where(gate, (M @ w) / np.sqrt(np.maximum(k, 1)), 0.0)


def _level(z: float, mild: float, strong: float) -> int:
    if z != z:
        return 0
    return 2 if z >= strong else 1 if z >= mild else -2 if z <= -strong else -1 if z <= -mild else 0


class Tester:
    """Runs every signal over one group of rows: a single index through time, or all stocks pooled day by day."""

    def __init__(self, G: pd.DataFrame, pooled: bool):
        self.G, self.pooled = G, pooled
        self.M = G[["s_" + k for k in SIG_KEYS]].to_numpy(dtype=bool)
        self.F = {h: G[f"f{h}"].to_numpy(dtype=float) for h in HORIZONS}
        if pooled:
            codes, self.dates = pd.factorize(G["date"], sort=True)
            self.pos, self.nd = codes.astype(np.int64), len(self.dates)
        else:
            self.pos, self.nd = np.arange(len(G), dtype=np.int64), len(G)
            self.dates = pd.DatetimeIndex(G["date"])
        self.half = self.nd // 2

    def stats(self, h: int, before: int | None = None) -> tuple[dict, float]:
        """Every signal's record at horizon h. With `before`, only windows that had fully closed before that day count."""
        fwd = self.F[h]
        ok = ~np.isnan(fwd)
        half = self.half
        if before is not None:
            ok = ok & (self.pos + 1 + h < before)
            half = before // 2
        out = {}
        if self.pooled:
            U = pooled_base(fwd, ok, self.pos, self.nd)
            ex = fwd[ok] - U["mean"][self.pos[ok]]
            sd = float(np.nanstd(ex)) if ok.any() else float("nan")
            for i, k in enumerate(SIG_KEYS):
                if SIG_INFO[k]["scope"] == "all":
                    out[k] = stat_pooled(self.M[:, i], fwd, ok, self.pos, self.nd, h, half, U)
        else:
            sd = float(np.nanstd(fwd[ok])) if ok.any() else float("nan")
            for i, k in enumerate(SIG_KEYS):
                out[k] = stat_series(self.M[:, i], fwd, ok, self.pos, h, half)
        return out, sd

    def walk_forward(self, h: int, mild: float, strong: float) -> dict:
        """Rebuilds the verdict for every past day from data before that day only, then reports how each verdict did."""
        lvl = np.zeros(len(self.G), dtype=np.int8)
        seen = np.zeros(len(self.G), dtype=bool)
        for cut in range(WF_START, self.nd, WF_STEP):
            st, sd = self.stats(h, before=cut)
            w = _weights(st)
            rows = (self.pos >= cut) & (self.pos < cut + WF_STEP)
            if not rows.any() or not sd or sd != sd:
                continue
            z = _score(self.M[rows], w) / sd
            lvl[rows] = [_level(v, mild, strong) for v in z]
            seen[rows] = True
        fwd = self.F[h]
        ok = seen & ~np.isnan(fwd)
        out = {"tested_days": int(len(np.unique(self.pos[ok]))) if ok.any() else 0, "by": {}}
        if not ok.any():
            return out
        U = pooled_base(fwd, ok, self.pos, self.nd) if self.pooled else None
        half = (WF_START + self.nd) // 2
        for L in (2, 1, 0, -1, -2):
            m = lvl == L
            st = stat_pooled(m, fwd, ok, self.pos, self.nd, h, half, U) if self.pooled else stat_series(m, fwd, ok, self.pos, h, half)
            if st:
                out["by"][L] = st
        # the simple question: did "up" verdicts do better than "down" verdicts on days the model had never seen?
        up, dn = (lvl > 0) & ok, (lvl < 0) & ok
        ref = U["mean"][self.pos] if self.pooled else 0.0
        if up.sum() >= 20 and dn.sum() >= 20:
            out["spread"] = float(np.nanmean((fwd - ref)[up]) - np.nanmean((fwd - ref)[dn]))
        return out


# ───────────── today's reading ─────────────
def _f(x, nd=None):
    try:
        x = float(x)
        if x != x or math.isinf(x):
            return None
        return round(x, nd) if nd is not None else x
    except Exception:
        return None


def _d(x):
    try:
        return pd.Timestamp(x).date().isoformat() if pd.notna(x) else None
    except Exception:
        return None


# ───────────── the forecasting model ─────────────
def _fit(X, y, lam):
    """Ridge regression: next-10-session return explained by the inputs, held back so that noise is not learnt as signal."""
    mu = float(y.mean())
    n, k = X.shape
    b = np.linalg.solve(X.T @ X + lam * n * np.eye(k), X.T @ (y - mu))
    return b, mu


def _rank_corr(a, b) -> float:
    if len(a) < 20:
        return float("nan")
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    c = np.corrcoef(ra, rb)[0, 1]
    return float(c) if c == c else float("nan")


def _expanding_pct(p):
    """Where each forecast sits among the forecasts made before it (0 to 1). Never looks forward."""
    import bisect
    out, seen = np.full(len(p), np.nan), []
    for i, v in enumerate(p):
        if v == v:
            if len(seen) >= 60:
                out[i] = bisect.bisect_left(seen, v) / len(seen)
            bisect.insort(seen, v)
    return out


def _pct_level(q):
    return 0 if q != q else 2 if q >= 0.8 else 1 if q >= 0.6 else -2 if q <= 0.2 else -1 if q <= 0.4 else 0


def _curve_stats(r) -> dict:
    r = np.asarray(r, dtype=float)
    if len(r) < 40:
        return {}
    eq = np.cumprod(1 + r)
    sd = float(r.std())
    return {"total": float(eq[-1] - 1), "cagr": float(eq[-1] ** (YEAR / len(r)) - 1), "dd": float((eq / np.maximum.accumulate(eq) - 1).min()),
            "sharpe": float(r.mean() / sd * math.sqrt(YEAR)) if sd > 0 else 0.0}


def _thin(dates, *series, n=160):
    """A curve cut down to at most n points for drawing."""
    idx = np.unique(np.linspace(0, len(dates) - 1, min(n, len(dates))).astype(int)) if len(dates) else []
    return [pd.Timestamp(dates[i]).date().isoformat() for i in idx], [[_f(s_[i], 5) for i in idx] for s_ in series]


def _confidence(t) -> tuple:
    if t != t:
        return "Untested", 0
    return ("High", 3) if t >= 2.5 else ("Medium", 2) if t >= 1.5 else ("Low", 1) if t >= 1.0 else ("None shown", 0)


def _fval(k, r) -> str:
    """One input said in words, with today's number."""
    v = _f(r.get("v_" + k))
    if v is None:
        return "no reading"
    p = v * 100
    T = {"trend": lambda: f"{p:+.1f}% from the 50-day average", "mom": lambda: f"{p:+.1f}% in 20 sessions", "rev": lambda: f"{p:+.1f}% in 5 sessions",
         "oiflow": lambda: "open interest rising with price" if v > 0 else "open interest rising against price" if v < 0 else "open interest flat",
         "oi": lambda: f"open interest {p:+.1f}% in 5 sessions", "pcr": lambda: f"put-call ratio {math.exp(v):.2f}",
         "pcr_d": lambda: f"put-call ratio {'up' if v > 0 else 'down'} {abs(math.expm1(v)) * 100:.0f}% in 5 sessions",
         "iv": lambda: f"implied volatility {p:.1f}%", "iv_d": lambda: f"volatility {p:+.0f}% in 5 sessions", "skew": lambda: f"puts {p:+.1f} vol points over calls",
         "cal": lambda: f"next month at {p:+.2f}% to this month", "pain": lambda: f"{p:+.1f}% from max pain", "roll": lambda: f"{p:.0f}% rolled to next month",
         "fii": lambda: f"FIIs {p:.0f}% long", "fii_d": lambda: f"FII long share {p:+.1f} pts in 5 sessions", "cli": lambda: f"retail {p:.0f}% long",
         "fii_opt": lambda: f"FII index options net {v:+,.0f} contracts", "breadth": lambda: f"{p:.0f}% of F&O stocks in uptrends",
         "flow": lambda: f"build-up balance {p:+.0f} pts"}
    try:
        return T[k]()
    except Exception:
        return "no reading"


def _drivers(keys, b, xrow, row) -> list:
    out = [{"key": k, "label": F_LABEL[k], "x": _f(xrow[i], 2), "beta": float(b[i]), "contrib": float(b[i] * xrow[i]), "text": _fval(k, row)} for i, k in enumerate(keys)]
    return sorted(out, key=lambda d: -abs(d["contrib"]))


def analogs(X, F, dates, i, w, has, k=24, h=MAIN_H) -> dict | None:
    """The past days that looked most like today across every input, and what the price did next. Days are kept at least
    h sessions apart so that one episode is not counted many times."""
    n = len(X)
    cand = np.nonzero(~np.isnan(F[h]) & (np.arange(n) < i - h) & has)[0]
    if len(cand) < 80:
        return None
    ww = np.sqrt(np.abs(w)) + 1e-9
    ww = ww / ww.sum()
    d = np.sqrt((((X[cand] - X[i]) ** 2) * ww).sum(axis=1))
    order = np.argsort(d)[:600]
    picks = []
    for j in order:
        c = int(cand[j])
        if all(abs(c - p_) >= h for p_, _ in picks):
            picks.append((c, float(d[j])))
            if len(picks) >= k:
                break
    if len(picks) < 8:
        return None
    ix = np.array([p_ for p_, _ in picks])
    out = {"n": len(picks), "h": {}, "rows": [], "f": [_f(v, 5) for v in F[h][ix]]}
    for hh in HORIZONS:
        v, allv = F[hh][ix], F[hh][cand]
        v = v[~np.isnan(v)]
        if len(v):
            out["h"][hh] = {"median": float(np.median(v)), "mean": float(v.mean()), "up": float((v > 0).mean()), "worst": float(v.min()), "best": float(v.max()),
                            "base_up": float((allv[~np.isnan(allv)] > 0).mean()), "base": float(np.nanmean(allv))}
    for c, dist in picks[:10]:
        out["rows"].append({"date": pd.Timestamp(dates[c]).date().isoformat(), "dist": dist, **{f"f{hh}": _f(F[hh][c], 5) for hh in HORIZONS}})
    return out


def index_model(G: pd.DataFrame) -> dict | None:
    """Forecast of the next 10 sessions for one index, with its record on days it had not seen and a simple test strategy."""
    h, n = MAIN_H, len(G)
    cols = ["x_" + k for k in IDX_F]
    Xn = G[cols].to_numpy(dtype=float)
    has = (~np.isnan(Xn)).sum(axis=1) >= 4
    X = np.nan_to_num(Xn)
    y = G[f"f{h}"].to_numpy(dtype=float)
    pos = np.arange(n)
    okf = ~np.isnan(y) & has
    if int(okf.sum()) < 200:
        return None
    pred = np.full(n, np.nan)
    for cut in range(WF_START, n, MODEL_STEP):
        tr = okf & (pos + 1 + h < cut)
        if int(tr.sum()) < 150:
            continue
        b, mu = _fit(X[tr], y[tr], IDX_LAM)
        rows = slice(cut, min(cut + MODEL_STEP, n))
        pred[rows] = np.where(has[rows], X[rows] @ b + mu, np.nan)
    b, mu = _fit(X[okf], y[okf], IDX_LAM)
    full = np.where(has, X @ b + mu, np.nan)
    out = {"forecast": _f(full[-1]), "drift": mu, "swing": float(np.nanstd(y)), "betas": [{"key": k, "label": F_LABEL[k], "beta": float(b[i])} for i, k in enumerate(IDX_F)],
           "drivers": _drivers(IDX_F, b, X[-1], G.iloc[-1]), "oos_days": 0, "by": {}, "curve": None, "stats": {}}
    fv = full[~np.isnan(full)]
    q = float((fv < full[-1]).mean()) if full[-1] == full[-1] and len(fv) else float("nan")
    out["pct"], out["level"] = _f(q, 3), _pct_level(q)
    # record on unseen days
    v = ~np.isnan(pred) & ~np.isnan(y)
    out["oos_days"] = int(v.sum())
    if int(v.sum()) >= 120:
        ic = _rank_corr(pred[v], y[v])
        neff = max(int(v.sum()) / h, 3)
        out["ic"], out["ic_t"] = ic, float(ic * math.sqrt(neff - 2) / math.sqrt(max(1 - ic * ic, 1e-9))) if ic == ic else float("nan")
        pq = _expanding_pct(pred)
        lvl = np.array([_pct_level(x) for x in pq])
        ok = v & ~np.isnan(pq)
        half = int(np.median(pos[ok])) if ok.any() else n // 2
        for L in (2, 1, 0, -1, -2):
            st_ = stat_series(lvl == L, y, ok, pos, h, half)
            if st_:
                out["by"][L] = st_
        # the test strategy: hold the index future unless the forecast is in the bottom 40% of what the model has said before
        i0 = int(np.argmax(~np.isnan(pq))) if (~np.isnan(pq)).any() else None
        if i0 is not None and n - i0 > 60:
            r = np.nan_to_num(G["n2"].to_numpy(dtype=float)[i0:])
            inm = np.where(np.isnan(pq[i0:]), 1.0, (pq[i0:] > 0.4).astype(float))
            sw = np.abs(np.diff(np.concatenate([[1.0], inm])))
            strat = inm * r - sw * COST
            d_, (c1, c2) = _thin(G["date"].to_numpy()[i0:], np.cumprod(1 + strat) - 1, np.cumprod(1 + r) - 1)
            out["curve"] = {"date": d_, "model": c1, "hold": c2}
            out["trade"] = {"long": index_signals(G, lvl, i0, 1), "short": index_signals(G, lvl, i0, -1)}
            out["_wf"] = {pd.Timestamp(d): int(v) for d, v in zip(G["date"].to_numpy()[i0:], lvl[i0:])}
            out["stats"] = {"model": _curve_stats(strat), "hold": _curve_stats(r), "in_market": float(inm.mean()), "switches": int(sw.sum()),
                            "from": pd.Timestamp(G["date"].iloc[i0]).date().isoformat()}
    out["conf"], out["conf_n"] = _confidence(out.get("ic_t", float("nan")))
    st_ = out["by"].get(out["level"])
    out["p_up"] = st_["hit"] if st_ else None
    out["base_up"] = float((y[~np.isnan(y)] > 0).mean())
    out["analogs"] = analogs(X, {hh: G[f"f{hh}"].to_numpy(dtype=float) for hh in HORIZONS}, G["date"].to_numpy(), n - 1, b, has)
    return out


def stock_model(K: pd.DataFrame, last) -> dict | None:
    """One model for all stocks: which stocks should beat or lag the other F&O stocks over the next 10 sessions.
    Tested by ranking the stocks every day on days the model had not seen, and following the top fifth against the bottom fifth."""
    h = MAIN_H
    cols = ["x_" + k for k in STK_F]
    Xn = K[cols].to_numpy(dtype=np.float32)
    has = (~np.isnan(Xn)).sum(axis=1) >= 4
    X = np.nan_to_num(Xn).astype(np.float64)
    codes, dates = pd.factorize(K["date"], sort=True)
    nd = len(dates)
    f = K[f"f{h}"].to_numpy(dtype=float)
    okf0 = ~np.isnan(f)
    U = pooled_base(f, okf0, codes, nd)
    y = f - U["mean"][codes]                          # return against the average F&O stock on the same day
    okf = okf0 & has
    if int(okf.sum()) < 3000:
        return None
    pred = np.full(len(K), np.nan)
    for cut in range(WF_START, nd, MODEL_STEP):
        tr = okf & (codes + 1 + h < cut)
        if int(tr.sum()) < 3000:
            continue
        b, mu = _fit(X[tr], y[tr], STK_LAM)
        rows = (codes >= cut) & (codes < cut + MODEL_STEP) & has
        pred[rows] = X[rows] @ b + mu
    b, mu = _fit(X[okf], y[okf], STK_LAM)
    out = {"betas": [{"key": k, "label": F_LABEL[k], "beta": float(b[i])} for i, k in enumerate(STK_F)], "b": b, "mu": mu, "oos_days": 0,
           "swing": float(np.nanstd(y[okf])), "pred": pred}
    v = ~np.isnan(pred) & ~np.isnan(y)
    if int(v.sum()) > 3000:
        d = codes[v]
        T = pd.DataFrame({"d": d, "p": pred[v], "y": y[v], "n2": np.nan_to_num(K["n2"].to_numpy(dtype=float)[v])})
        T["rp"] = T.groupby("d")["p"].rank(pct=True)
        T["ry"] = T.groupby("d")["y"].rank(pct=True)
        cnt = T.groupby("d")["p"].transform("count")
        T = T[cnt >= 30]
        a_, b_ = T["rp"] - T.groupby("d")["rp"].transform("mean"), T["ry"] - T.groupby("d")["ry"].transform("mean")
        g_ = pd.DataFrame({"d": T["d"], "ab": a_ * b_, "aa": a_ * a_, "bb": b_ * b_}).groupby("d").sum()
        ics = (g_["ab"] / np.sqrt(g_["aa"] * g_["bb"])).dropna()
        T["q"] = np.minimum((T["rp"] * 5).apply(np.ceil).astype(int), 5).clip(1, 5)
        qm = T.groupby(["d", "q"])["y"].mean().unstack()
        n2 = T.groupby(["d", "q"])["n2"].mean().unstack()
        out["oos_days"] = int(len(ics))
        if len(ics) >= 60:
            neff = max(len(ics) / h, 3)
            out["ic"], out["ic_t"] = float(ics.mean()), float(ics.mean() / (ics.std() / math.sqrt(neff))) if ics.std() > 0 else 0.0
            out["quint"] = [float(qm[q].mean()) if q in qm else None for q in (1, 2, 3, 4, 5)]
            if 1 in n2 and 5 in n2:
                ls = (n2[5] - n2[1]).fillna(0.0).to_numpy()
                top = (n2[5] - n2.mean(axis=1)).fillna(0.0).to_numpy()
                d_, (c1, c2) = _thin(dates[n2.index.to_numpy()], np.cumprod(1 + ls) - 1, np.cumprod(1 + top) - 1)
                out["curve"] = {"date": d_, "ls": c1, "top": c2}
                out["stats"] = {"ls": _curve_stats(ls), "top": _curve_stats(top), "from": d_[0]}
    out["conf"], out["conf_n"] = _confidence(out.get("ic_t", float("nan")))
    return out


# ───────────── trade signals ─────────────
# A signal is a complete trade: when to enter, where the stop and target are, and what ends it. Each kind of signal
# (index buy, index short, stock buy) is replayed over every session the model had not seen, trade by trade, after costs.
# A kind of signal is only switched on if that replay made money.
SIG_STOP, SIG_TARGET, SIG_HOLD = 1.0, 2.0, 10        # stop and target in "normal 10-session swings"; time limit in sessions
IDX_COST, STK_COST = 0.0005, 0.0015                  # cost per side: index future, stock (brokerage, taxes and slippage)
SLOTS = 10                                           # the stock test portfolio holds at most ten stocks, a tenth of the money each
MIN_PF = 1.2                                         # money won must be at least 1.2 times money lost
MIN_TRADES_IDX, MIN_TRADES_STK = 12, 40


def _trade_stats(rets, bars=None) -> dict:
    r = np.asarray(rets, dtype=float)
    if not len(r):
        return {"n": 0}
    w, l = r[r > 0], r[r <= 0]
    eq = np.cumprod(1 + r)
    streak = worst = 0
    for v in r:
        streak = streak + 1 if v <= 0 else 0
        worst = max(worst, streak)
    return {"n": int(len(r)), "win": float(len(w) / len(r)), "avg_win": float(w.mean()) if len(w) else 0.0, "avg_loss": float(l.mean()) if len(l) else 0.0,
            "exp": float(r.mean()), "pf": float(w.sum() / -l.sum()) if len(l) and l.sum() < 0 else (99.0 if len(w) else 0.0), "worst": float(r.min()),
            "best": float(r.max()), "total": float(eq[-1] - 1), "dd": float((eq / np.maximum.accumulate(eq) - 1).min()), "losing_run": int(worst),
            "bars": float(np.mean(bars)) if bars is not None and len(bars) else None}


def _live(st: dict, need: int, t_need: float) -> bool:
    """A kind of signal is switched on only if its replay made money after costs, won clearly more than it lost, AND did
    better than simply being in the market over the same days by more than luck would explain."""
    return bool(st.get("n", 0) >= need and st["exp"] > 0 and st["pf"] >= MIN_PF and st.get("edge", 0) > 0 and st.get("t", 0) >= t_need)


def _tier(st: dict, need: int, t_need: float) -> str:
    """on: passed the full test. trial: made money and beat simply holding, but not by enough to rule out luck. off: failed."""
    if _live(st, need, t_need):
        return "on"
    if st.get("n", 0) >= need and st["exp"] > 0 and st["pf"] >= MIN_PF and st.get("edge", 0) > 0:
        return "trial"
    return "off"


def _t_mean(x) -> float:
    x = np.asarray(x, dtype=float)
    sd = x.std(ddof=1) if len(x) > 2 else 0.0
    return float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 0 else 0.0


def index_signals(G: pd.DataFrame, lvl, i0: int, side: int) -> dict:
    """Replays one kind of index trade (buy when the verdict is positive, or short when it is negative) over the unseen
    sessions. Entry at the next close after the signal; stop and target checked on closing prices."""
    px, fut, dates = G["px"].to_numpy(dtype=float), G["fut"].to_numpy(dtype=float), G["date"].to_numpy()
    sw = G["swing"].to_numpy(dtype=float)
    n = len(px)
    L = np.nan_to_num(np.asarray(lvl, dtype=float)) * side
    trades, daily = [], np.zeros(n)
    cur = pend_in = pend_out = None

    def close(t, why):
        nonlocal cur
        ret = side * (px[t] / cur["p"] - 1) - 2 * IDX_COST
        trades.append({"in": _d(dates[cur["e"]]), "out": _d(dates[t]), "in_px": float(fut[cur["e"]]), "out_px": float(fut[t]), "ret": float(ret), "why": why,
                       "bars": t - cur["e"], "x": t})
        daily[t] -= IDX_COST
        cur = None

    for t in range(i0, n):
        if cur is not None and t > cur["e"]:
            daily[t] += side * (px[t] / px[t - 1] - 1)
        if pend_out and cur is not None:
            close(t, pend_out)
        pend_out = None
        if pend_in is not None and cur is None:
            cur = {"e": t, "p": px[t], "stop": px[t] * (1 - side * SIG_STOP * pend_in), "target": px[t] * (1 + side * SIG_TARGET * pend_in), "sw": pend_in}
            daily[t] -= IDX_COST
            pend_in = None
        elif cur is not None and t > cur["e"]:
            if side * (px[t] - cur["stop"]) <= 0:
                close(t, "Stop hit")
            elif side * (px[t] - cur["target"]) >= 0:
                close(t, "Target hit")
            elif L[t] <= -1:
                pend_out = "Verdict turned against it"
            elif t - cur["e"] >= SIG_HOLD and L[t] < 1:
                pend_out = "Time limit reached"
        pend_in = None if cur is not None else pend_in
        if cur is None and pend_in is None and L[t] >= 1 and sw[t] == sw[t] and sw[t] > 0:
            pend_in = float(sw[t])
    st_ = _trade_stats([x["ret"] for x in trades], [x["bars"] for x in trades])
    hold_r = np.nan_to_num(G["ret"].to_numpy(dtype=float))[i0:] * side
    drift = float(hold_r.mean()) if len(hold_r) else 0.0
    if trades:                                            # each trade against what simply sitting in the position for as many days would have earned
        ex = [x["ret"] - drift * x["bars"] for x in trades]
        st_["edge"], st_["t"] = float(np.mean(ex)), _t_mean(ex)
    last = n - 1
    f, s_now = float(fut[last]), (float(sw[last]) if sw[last] == sw[last] else None)
    state = {"action": "flat"}
    if cur is not None:
        k = f / px[last]                                  # turns levels on the continuous series into today's contract prices
        state = {"action": "exit_next" if pend_out else "hold", "why": pend_out, "since": _d(dates[cur["e"]]), "entry": float(fut[cur["e"]]), "bars": last - cur["e"],
                 "pnl": float(side * (px[last] / cur["p"] - 1)), "stop": float(cur["stop"] * k), "target": float(cur["target"] * k), "left": max(SIG_HOLD - (last - cur["e"]), 0)}
    elif trades and trades[-1]["x"] == last:
        state = {"action": "closed_today", "why": trades[-1]["why"], "since": trades[-1]["in"], "entry": trades[-1]["in_px"], "pnl": trades[-1]["ret"]}
    elif pend_in is not None and s_now:
        state = {"action": "new", "entry": f, "stop": f * (1 - side * SIG_STOP * s_now), "target": f * (1 + side * SIG_TARGET * s_now), "swing": s_now}
    hold = np.nan_to_num(G["ret"].to_numpy(dtype=float))[i0:] * side
    d_, (c1, c2) = _thin(dates[i0:], np.cumprod(1 + daily[i0:]) - 1, np.cumprod(1 + hold) - 1)
    return {"side": side, "stats": st_, "live": _live(st_, MIN_TRADES_IDX, 1.5), "tier": _tier(st_, MIN_TRADES_IDX, 1.5), "state": state, "trades": [{k: v for k, v in x.items() if k != "x"} for x in trades[-10:]][::-1],
            "curve": {"date": d_, "sys": c1, "hold": c2}, "from": _d(dates[i0])}


def stock_signals(K: pd.DataFrame, pred, mkt: dict) -> dict | None:
    """Replays the stock buy signal as a ten-stock portfolio over the unseen sessions. A stock is bought when the model ranks
    it in the top fifth, it is in an uptrend and the market verdict is not negative. It is sold on its stop, its target,
    when its rank falls below 40 of 100, or after ten sessions if it has left the top fifth."""
    dcode, dates = pd.factorize(K["date"], sort=True)
    scode, syms = pd.factorize(K["sym"].astype(str), sort=True)
    nd, ns = len(dates), len(syms)

    def mat(col, fill=np.nan, dtype=float):
        m = np.full((nd, ns), fill, dtype=dtype)
        m[dcode, scode] = col
        return m
    R, PR, FUT, SW = mat(K["ret"].to_numpy(dtype=float)), mat(np.asarray(pred, dtype=float)), mat(K["fut"].to_numpy(dtype=float)), mat(K["swing"].to_numpy(dtype=float))
    TU = mat(K["s_trend_up"].to_numpy(dtype=float), 0.0) > 0
    have = ~np.isnan(PR)
    days = np.nonzero(have.sum(axis=1) >= 30)[0]
    if len(days) < 120:
        return None
    d0 = int(days[0])
    RP = pd.DataFrame(PR).rank(axis=1, pct=True).to_numpy()
    mk = np.array([mkt.get(pd.Timestamp(d), 0) for d in dates], dtype=float)
    pos, trades, pend_in, pend_out = {}, [], [], {}
    port, bench, inv = np.zeros(nd), np.zeros(nd), np.zeros(nd)

    def close(j, t, why):
        p = pos.pop(j)
        trades.append({"sym": syms[j], "in": _d(dates[p["e"]]), "out": _d(dates[t]), "in_px": float(FUT[p["e"], j]), "out_px": float(FUT[t, j]) if FUT[t, j] == FUT[t, j] else None,
                       "ret": float(p["f"] - 1 - 2 * STK_COST), "why": why, "bars": t - p["e"], "x": t})
        port[t] -= STK_COST / SLOTS

    for t in range(d0, nd):
        r = R[t]
        ok = ~np.isnan(r)
        bench[t] = float(r[ok].mean()) if ok.any() else 0.0
        inv[t] = sum(1 for j in pos if pos[j]["e"] < t) / SLOTS
        for j in list(pos):
            if pos[j]["e"] == t:
                continue
            if not ok[j]:
                close(j, t, "Series broke (corporate action)")
            else:
                pos[j]["f"] *= 1 + r[j]
                port[t] += r[j] / SLOTS
        for j, why in pend_out.items():
            if j in pos:
                close(j, t, why)
        for j, s_ in pend_in:
            if len(pos) < SLOTS and j not in pos and ok[j]:
                pos[j] = {"e": t, "f": 1.0, "sw": s_}
                port[t] -= STK_COST / SLOTS
        pend_in, pend_out = [], {}
        for j in list(pos):
            p = pos[j]
            if p["e"] == t:
                continue
            rp = RP[t, j]
            if p["f"] <= 1 - SIG_STOP * p["sw"]:
                close(j, t, "Stop hit")
            elif p["f"] >= 1 + SIG_TARGET * p["sw"]:
                close(j, t, "Target hit")
            elif rp != rp or rp < 0.4:
                pend_out[j] = "Rank fell below 40"
            elif t - p["e"] >= SIG_HOLD and rp < 0.8:
                pend_out[j] = "Time limit reached"
        free = SLOTS - len(pos) + len(pend_out)
        if free > 0 and mk[t] > -1:
            c = np.nonzero((RP[t] >= 0.8) & TU[t] & (SW[t] > 0) & ok)[0]
            c = [j for j in c[np.argsort(-RP[t, c])] if j not in pos][:free]
            pend_in = [(int(j), float(SW[t, j])) for j in c]
    last = nd - 1
    st_ = _trade_stats([x["ret"] for x in trades], [x["bars"] for x in trades])
    ex = (port - bench * inv)[d0:]                        # each day against holding the average F&O stock with the same amount of money
    st_["edge"], st_["t"] = float(ex.mean() * (st_.get("bars") or 0) * SLOTS / max(inv[d0:].mean() * SLOTS, 1e-9)) if st_.get("n") else 0.0, _t_mean(ex)
    d_, (c1, c2) = _thin(dates[d0:], np.cumprod(1 + port[d0:]) - 1, np.cumprod(1 + bench[d0:]) - 1)
    hold = []
    for j, p in pos.items():
        f = float(FUT[last, j]) if FUT[last, j] == FUT[last, j] else None
        hold.append({"sym": syms[j], "since": _d(dates[p["e"]]), "entry": float(FUT[p["e"], j]), "price": f, "pnl": float(p["f"] - 1), "bars": last - p["e"],
                     "stop": f * (1 - SIG_STOP * p["sw"]) / p["f"] if f else None, "target": f * (1 + SIG_TARGET * p["sw"]) / p["f"] if f else None,
                     "rank": _f(RP[last, j], 3), "sell": pend_out.get(j), "left": max(SIG_HOLD - (last - p["e"]), 0)})
    buy = [{"sym": syms[j], "entry": float(FUT[last, j]), "stop": float(FUT[last, j] * (1 - SIG_STOP * s_)), "target": float(FUT[last, j] * (1 + SIG_TARGET * s_)),
            "rank": _f(RP[last, j], 3), "swing": s_} for j, s_ in pend_in]
    sold = [{k: v for k, v in x.items() if k != "x"} for x in trades if x["x"] == last]
    avoid_ix = np.nonzero((RP[last] <= 0.2) & ~TU[last] & ~np.isnan(R[last]))[0]
    avoid = [{"sym": syms[j], "rank": _f(RP[last, j], 3), "price": _f(FUT[last, j])} for j in avoid_ix[np.argsort(RP[last, avoid_ix])][:15]]
    return {"stats": st_, "live": _live(st_, MIN_TRADES_STK, 2.0), "tier": _tier(st_, MIN_TRADES_STK, 2.0), "from": _d(dates[d0]), "invested": float(inv[d0:].mean()), "curve": {"date": d_, "sys": c1, "bench": c2},
            "port": _curve_stats(port[d0:]), "bench": _curve_stats(bench[d0:]), "buy": buy, "hold": sorted(hold, key=lambda h: -h["pnl"]), "sold": sold, "avoid": avoid,
            "trades": [{k: v for k, v in x.items() if k != "x"} for x in trades[-12:]][::-1], "market_ok": bool(mk[last] > -1), "slots": SLOTS}


def index_call(x: dict) -> dict:
    """Today's instruction for one index, in one word, with the reason. Buy and short signals are only passed on when the
    model has a record on unseen days AND that kind of trade made money in the replay."""
    M = x.get("model") or {}
    T = M.get("trade") or {}
    if not T:
        return {"word": "No signal", "tone": "flat", "why": "There is not enough stored history yet to test trade signals for this index."}
    trusted = M.get("conf_n", 0) >= 1
    for key, name, tone in (("long", "Buy", "up"), ("short", "Short", "dn")):
        S_ = T[key]
        st_, a = S_["state"], S_["state"]["action"]
        if a == "flat":
            continue
        rec = f"{S_['stats'].get('n', 0)} past trades, {S_['stats'].get('win', 0) * 100:.0f}% won, money won to money lost {S_['stats'].get('pf', 0):.2f}"
        if not (trusted and S_["tier"] != "off"):
            reason = ("the model has not shown skill on unseen days" if not trusted else f"this kind of trade did not pass its test ({rec})")
            if a == "new":
                return {"word": "Stay out", "tone": "flat", "side": key, "blocked": True, "why": f"The model points to a {name.lower()} but {reason}. No trade."}
            continue
        st_ = {**st_, "tier": S_["tier"]}
        if a == "new":
            return {"word": name, "tone": tone, "side": key, **st_, "why": f"New {name.lower()} signal. Act at the next session's close. Record of this signal: {rec}."}
        if a == "hold":
            return {"word": "Hold" if key == "long" else "Hold short", "tone": tone, "side": key, **st_,
                    "why": f"A {name.lower()} signal from {st_['since']} is still open. Keep the stop where it is. Record of this signal: {rec}."}
        if a == "exit_next":
            return {"word": "Sell" if key == "long" else "Cover", "tone": "dn" if key == "long" else "up", "side": key, **st_,
                    "why": f"Close the {name.lower()} trade from {st_['since']} at the next session's close: {str(st_['why']).lower()}."}
        if a == "closed_today":
            return {"word": "Sell now" if key == "long" else "Cover now", "tone": "dn" if key == "long" else "up", "side": key, **st_,
                    "why": f"The {name.lower()} trade from {st_['since']} ended at today's close: {str(st_['why']).lower()}."}
    if not trusted:
        return {"word": "Stay out", "tone": "flat", "off": True, "why": "Signals for this index are switched off: the model has not shown skill on sessions it had not seen."}
    if all(T[k]["tier"] == "off" for k in ("long", "short")):
        return {"word": "Stay out", "tone": "flat", "off": True, "why": "Signals for this index are switched off: neither buying nor shorting on the model's verdict passed its test."}
    return {"word": "Stay out", "tone": "flat", "why": "No trade is open and the model is not giving a new signal today."}


# ───────────── option chain analytics ─────────────
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _b76(F, K, T, v, call):
    if T <= 0 or v <= 0:
        return max(F - K, 0.0) if call else max(K - F, 0.0)
    d1 = (math.log(F / K) + 0.5 * v * v * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    return F * _ncdf(d1) - K * _ncdf(d2) if call else K * _ncdf(-d2) - F * _ncdf(-d1)


def _iv(px, F, K, T, call):
    if not (px and px > 0 and F > 0 and K > 0 and T > 0) or px <= (max(F - K, 0.0) if call else max(K - F, 0.0)) + 1e-9:
        return None
    lo, hi = 0.01, 4.0
    if _b76(F, K, T, hi, call) < px:
        return None
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if _b76(F, K, T, mid, call) > px:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _gamma(F, K, T, v):
    if not (v and v > 0 and T > 0):
        return 0.0
    d1 = (math.log(F / K) + 0.5 * v * v * T) / (v * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (F * v * math.sqrt(T))


def chain_view(sym: str, exp, F: float, asof, chain: pd.DataFrame) -> dict | None:
    """Everything read off one expiry's option chain: open interest and its change by strike, the volatility smile, max
    pain, and an estimate of dealer gamma (assuming, as is standard, that dealers have sold the puts and bought the calls
    that customers hold; the true split is not published, so treat the gamma numbers as an estimate)."""
    if not len(chain) or not exp or not F:
        return None
    ch = chain[(chain["sym"] == sym) & (chain["exp"] == pd.Timestamp(exp))].sort_values("strike")
    if len(ch) < 6:
        return None
    T = max((pd.Timestamp(exp) - pd.Timestamp(asof)).days, 0.5) / 365
    K = ch["strike"].to_numpy(dtype=float)
    g = lambda c: np.nan_to_num(ch[c].to_numpy(dtype=float))
    co, po, cc, pc, cp, pp = g("ce_oi"), g("pe_oi"), g("ce_chg"), g("pe_chg"), g("ce_px"), g("pe_px")
    iv = [(_iv(pp[i], F, K[i], T, False) if K[i] < F else _iv(cp[i], F, K[i], T, True)) for i in range(len(K))]
    known = [v for v in iv if v]
    fill = float(np.median(known)) if known else None
    ivf = [v or fill for v in iv]
    out = {"sym": sym, "exp": _d(exp), "dte": int(round(T * 365)), "strike": K.tolist(), "ce_oi": co.tolist(), "pe_oi": po.tolist(), "ce_chg": cc.tolist(),
           "pe_chg": pc.tolist(), "iv": [_f(v, 4) for v in iv], "F": F, "pcr": float(po.sum() / co.sum()) if co.sum() > 0 else None}
    pain = [float((np.maximum(k - K, 0) * co).sum() + (np.maximum(K - k, 0) * po).sum()) for k in K]
    out["pain"], out["max_pain"] = pain, float(K[int(np.argmin(pain))])
    out["call_wall"], out["put_wall"] = float(K[int(np.argmax(co))]), float(K[int(np.argmax(po))])
    out["call_add"], out["put_add"] = float(K[int(np.argmax(cc))]), float(K[int(np.argmax(pc))])
    if fill:
        def net_gamma(S):
            return sum((co[i] - po[i]) * _gamma(S, K[i], T, ivf[i]) for i in range(len(K))) * S * S * 0.01
        out["gex"] = [float((co[i] - po[i]) * _gamma(F, K[i], T, ivf[i]) * F * F * 0.01) for i in range(len(K))]
        out["net_gex"] = float(sum(out["gex"]))
        grid = np.linspace(K.min(), K.max(), 81)
        prof = [net_gamma(float(S)) for S in grid]
        flips = [float(grid[i] + (grid[i + 1] - grid[i]) * (0 - prof[i]) / (prof[i + 1] - prof[i])) for i in range(len(grid) - 1) if prof[i] * prof[i + 1] < 0]
        out["flip"] = min(flips, key=lambda x: abs(x - F)) if flips else None
    return out


def _levels(r) -> dict:
    """The numbers a trader reads off the chain, plus the move the option market is pricing."""
    fut, iv, dte = _f(r["fut"]), _f(r["iv"]), _f(r["dte"])
    out = {"fut": fut, "spot": _f(r.get("spot")), "m_exp": _d(r["m_exp"]), "dte": int(dte) if dte is not None else None, "lot": _f(r.get("lot")),
           "max_pain": _f(r["max_pain"]), "call_wall": _f(r["call_wall"]), "put_wall": _f(r["put_wall"]), "pcr": _f(r["pcr"], 2), "pcr_pct": _f(r["pcr_pct"], 3),
           "iv": iv, "iv_pct": _f(r["iv_pct"], 3), "skew": _f(r["skew"], 4), "cal": _f(r["cal"], 5), "roll": _f(r["roll"], 3),
           "r1": _f(r["ret"], 5), "r5": _f(r["r5"], 5), "r20": _f(r["r20"], 5), "oi5": _f(r["oi5"], 4), "fut_oi": _f(r["fut_oi"]),
           "above50": bool(r["px"] > r["ma50"]) if pd.notna(r["ma50"]) else None,
           "w_exp": _d(r.get("w_exp")), "w_max_pain": _f(r.get("w_max_pain")), "w_call_wall": _f(r.get("w_call_wall")), "w_put_wall": _f(r.get("w_put_wall"))}
    if fut and iv:
        out["move5"] = fut * iv * math.sqrt(7 / 365)            # one normal swing over the next five sessions
        out["move10"] = fut * iv * math.sqrt(14 / 365)
        if dte and dte >= 1:
            out["move_exp"] = fut * iv * math.sqrt(dte / 365)
    return out


def _signal_table(row, stats_by_h: dict) -> list:
    out = []
    for k in SIG_KEYS:
        st = {h: stats_by_h[h].get(k) for h in HORIZONS}
        out.append({"key": k, **SIG_INFO[k], "on": bool(row["s_" + k]), "grade": grade(st[MAIN_H]), "stats": st})
    return out


def _reasons(sig_table: list, who: str, relative: bool) -> list:
    """Plain sentences for the signals that are on and carry weight, biggest first."""
    out = []
    for s in sig_table:
        st = s["stats"].get(MAIN_H)
        if not (s["on"] and s["grade"] in ("proven", "leaning") and st):
            continue
        e = st["edge"] * 100
        tail = "than other F&O stocks" if relative else "than on a normal day"
        out.append((abs(st["edge"]) * (1 if s["grade"] == "proven" else 0.5),
                    {"key": s["key"], "label": s["label"], "grade": s["grade"], "edge": st["edge"], "dir": "up" if e > 0 else "dn",
                     "text": f"{s['label']}: after this, {who} did {abs(e):.1f}% {'better' if e > 0 else 'worse'} {tail} over the next {MAIN_H} sessions "
                             f"({st['neff']} separate episodes)."}))
    return [x[1] for x in sorted(out, key=lambda x: -x[0])]


def _index_line(level: int, lv: dict, reasons: list, name: str) -> str:
    trend = "in an uptrend" if lv.get("above50") else "in a downtrend" if lv.get("above50") is False else "without a clear trend"
    if level == 0:
        why = ("None of the readings that have a tested record is on today." if not reasons
               else "The proven readings that are on today pull in opposite directions and cancel out.")
        return f"{name} is {trend}. {why} Nothing in the positioning data argues for changing your plan."
    lead = {2: f"The data points to more upside for {name}", 1: f"The data mildly favours staying invested in {name}",
            -1: f"The data mildly favours cutting risk in {name}", -2: f"The data points to a pullback in {name}"}[level]
    if level == 2 and lv.get("above50") is False:
        lead = f"The data points to a bounce in {name}"
    return f"{lead}. It is {trend}; the readings behind this view are listed below with their record."


def _stock_line(level: int, mk: int) -> str:
    rel = {2: "ranks in the top fifth of F&O stocks", 1: "ranks above the middle", 0: "ranks in the middle",
           -1: "ranks below the middle", -2: "ranks in the bottom fifth of F&O stocks"}[level]
    mkt = {2: "the market view is positive", 1: "the market view is mildly positive", 0: "the market view is neutral",
           -1: "the market view is mildly negative", -2: "the market view is negative"}[mk]
    return f"On the model's inputs this stock {rel} for the next {MAIN_H} sessions; {mkt}."


# ───────────── option structures ─────────────
def _nearest(strikes, x):
    return min(strikes, key=lambda k: abs(k - x)) if strikes else None


def option_idea(sym: str, lv: dict, level: int, chain: pd.DataFrame) -> dict:
    """A defined-risk option structure that fits the view and how expensive options are. Strikes come from the open-interest
    walls and the move the market is pricing; money numbers come from the stored closing premiums when they exist.
    The structure follows fixed rules; unlike the signals, its profit record is NOT backtested."""
    fut, iv, ivp, dte, lot = lv.get("fut"), lv.get("iv"), lv.get("iv_pct"), lv.get("dte"), lv.get("lot")
    out = {"sym": sym, "name": None, "legs": [], "why": "", "note": ""}
    if not fut or not iv:
        out["why"] = "No reliable implied volatility for this contract today."
        return out
    rich = ivp is not None and ivp >= 0.6
    if level == 0 and not (ivp is not None and ivp >= 0.7):
        out["name"] = "No trade"
        out["why"] = "There is no proven edge on direction and options are not expensive enough to be worth selling."
        return out
    if dte is None or dte < 5:
        out["name"] = "Wait for the new series"
        out["why"] = f"The monthly contract expires in {dte if dte is not None else 'a few'} days: too little time for a spread to work. Look again after expiry."
        return out
    ch = chain[(chain["sym"] == sym) & (chain["exp"] == pd.Timestamp(lv["m_exp"]))] if len(chain) else pd.DataFrame()
    strikes = sorted(float(k) for k in ch["strike"].dropna().unique()) if len(ch) else []
    sd = fut * iv * math.sqrt(dte / 365)
    if len(strikes) < 6:
        out["note"] = "Strike prices are not stored for this contract, so only the shape of the trade is shown."
        step = 50.0 if fut > 5000 else max(round(fut * 0.01, 0), 1.0)
        strikes = [round((fut + i * step) / step) * step for i in range(-40, 41)]
    step = float(np.median(np.diff(strikes))) if len(strikes) > 1 else 1.0
    width = max(2 * step, round(0.01 * fut / step) * step)
    pwall, cwall = lv.get("put_wall"), lv.get("call_wall")
    lo = pwall if (pwall and fut - 1.5 * sd <= pwall <= fut - 0.5 * sd) else fut - sd
    hi = cwall if (cwall and fut + 0.5 * sd <= cwall <= fut + 1.5 * sd) else fut + sd
    atm, k_lo, k_hi = _nearest(strikes, fut), _nearest(strikes, lo), _nearest(strikes, hi)

    def px(k, typ):
        if not len(ch):
            return None
        r = ch[ch["strike"] == k]
        v = _f(r["ce_px" if typ == "CE" else "pe_px"].iloc[0]) if len(r) else None
        return v if v and v > 0 else None

    def leg(act, typ, k):
        return {"act": act, "typ": typ, "strike": k, "px": px(k, typ)}

    def pair(k, d):
        """The strike to sell and its protective wing one width further out (d = -1 below, +1 above)."""
        w = _nearest([x for x in strikes if (x - k) * d > 0] or [k], k + d * width)
        if w == k:                      # the stored chain ends here: move the pair one width inward
            return _nearest([x for x in strikes if (x - k) * d < 0] or [k], k - d * width), k
        return k, w

    exp_txt = f"expiring {pd.Timestamp(lv['m_exp']).strftime('%d %b')}"
    if level > 0 and rich:
        out["name"], legs = "Bull put spread", [leg("Sell", "PE", pair(k_lo, -1)[0]), leg("Buy", "PE", pair(k_lo, -1)[1])]
        out["why"] = f"The view is positive and options are expensive, so it pays to sell a put spread below support, {exp_txt}."
    elif level > 0:
        out["name"], legs = "Bull call spread", [leg("Buy", "CE", atm), leg("Sell", "CE", k_hi if k_hi > atm else _nearest(strikes, atm + width))]
        out["why"] = f"The view is positive and options are not expensive, so buying a call spread costs little, {exp_txt}."
    elif level < 0 and rich:
        out["name"], legs = "Bear call spread", [leg("Sell", "CE", pair(k_hi, 1)[0]), leg("Buy", "CE", pair(k_hi, 1)[1])]
        out["why"] = f"The view is negative and options are expensive, so it pays to sell a call spread above resistance, {exp_txt}."
    elif level < 0:
        out["name"], legs = "Bear put spread", [leg("Buy", "PE", atm), leg("Sell", "PE", k_lo if k_lo < atm else _nearest(strikes, atm - width))]
        out["why"] = f"The view is negative and options are not expensive, so buying a put spread costs little, {exp_txt}."
    else:
        out["name"] = "Iron condor"
        legs = [leg("Sell", "PE", pair(k_lo, -1)[0]), leg("Buy", "PE", pair(k_lo, -1)[1]), leg("Sell", "CE", pair(k_hi, 1)[0]), leg("Buy", "CE", pair(k_hi, 1)[1])]
        out["why"] = f"No edge on direction, but options are expensive: sell both sides outside the range the market is pricing, {exp_txt}."
    if any(l["act"] == "Sell" and ((l["typ"] == "PE" and l["strike"] >= fut) or (l["typ"] == "CE" and l["strike"] <= fut)) for l in legs) \
            and out["name"] in ("Bull put spread", "Bear call spread", "Iron condor"):
        out["note"] = "The stored strikes do not reach far enough from the price to build this safely today. Check the live chain."
        return out
    out["legs"] = legs
    if all(l["px"] for l in legs) and len({(l["typ"], l["strike"]) for l in legs}) == len(legs):
        net = sum(l["px"] if l["act"] == "Sell" else -l["px"] for l in legs)      # premium received (+) or paid (-), per unit
        widths = [abs(legs[i]["strike"] - legs[i + 1]["strike"]) for i in range(0, len(legs), 2)]
        w = max(widths)
        if net > 0:
            gain, loss = net, w - net
            be = ([legs[0]["strike"] - net, legs[2]["strike"] + net] if len(legs) == 4
                  else [legs[0]["strike"] - net] if legs[0]["typ"] == "PE" else [legs[0]["strike"] + net])
        else:
            gain, loss = w + net, -net
            be = [legs[0]["strike"] - net] if legs[0]["typ"] == "CE" else [legs[0]["strike"] + net]
        if gain > 0 and loss > 0:
            q = lot or 1
            out.update({"net": net, "max_gain": gain * q, "max_loss": loss * q, "breakeven": be, "lot": lot, "rr": gain / loss})
    elif not out["note"]:
        out["note"] = "One of these strikes did not trade at the close, so profit and loss are not shown."
    return out


# ───────────── the written read ─────────────
def _pp(x, d=1):
    return "n/a" if x is None or x != x else f"{x * 100:+.{d}f}%"


def _n0(x):
    return "n/a" if x is None or x != x else f"{x:,.0f}"


def desk_note(name: str, lv: dict, M: dict | None, cm: dict | None, mk: dict, reasons: list, ma50_level) -> list:
    """The analyst's note: the same numbers as the page, put in order. Each line is built from the data by fixed rules."""
    N = []
    if M and M.get("forecast") is not None:
        trusted = M["conf_n"] >= 1
        shown = M["level"] if M["conf_n"] >= 2 else max(-1, min(1, M["level"]))
        verdict = VERDICTS[shown][0] if trusted else f"No tested edge. The model leans towards '{VERDICTS[M['level']][0].lower()}' but has not earned a say"
        N.append({"h": "View", "t": f"{verdict}. The model expects {name} to move {_pp(M['forecast'])} over the next {MAIN_H} sessions, against {_pp(M['drift'])} on a normal day. "
                                    f"That forecast is higher than {(M['pct'] or 0) * 100:.0f}% of the readings it has given over the stored history."})
        st_ = M["by"].get(M["level"])
        if M.get("ic") is not None and M["ic"] == M["ic"]:
            skill = (f"On {M['oos_days']:,} sessions it had not seen, its forecasts lined up with what happened next with a rank correlation of {M['ic']:+.2f} "
                     f"(confidence: {M['conf'].lower()}).")
            if st_:
                skill += (f" When it gave this verdict before ({st_['n']:,} sessions, {st_['neff']} separate episodes) the index moved {_pp(st_['mean'])} on average "
                          f"and ended higher {st_['hit'] * 100:.0f}% of the time, against {st_['base_hit'] * 100:.0f}% normally.")
            if M["conf_n"] == 1:
                skill += " With a record this thin the model is only allowed to lean, not to make its strong calls."
            if M["conf_n"] == 0:
                skill += " That is no better than chance, so the page shows the positioning but takes no view from it."
            N.append({"h": "Record", "t": skill})
        up = [d for d in M["drivers"] if d["contrib"] > 0 and d["x"] is not None][:3]
        dn = [d for d in M["drivers"] if d["contrib"] < 0 and d["x"] is not None][:3]
        t = ""
        if up:
            t += "Pulling the forecast up: " + "; ".join(f"{d['label']} ({d['text']})" for d in up) + ". "
        if dn:
            t += "Pulling it down: " + "; ".join(f"{d['label']} ({d['text']})" for d in dn) + "."
        if t:
            N.append({"h": "Drivers", "t": t.strip()})
    else:
        N.append({"h": "View", "t": f"There is not yet enough stored history to fit and test a model for {name}. The levels and positioning below are still live."})
    o = []
    if lv.get("pcr") is not None:
        o.append(f"Put-call ratio {lv['pcr']:.2f}" + (f", higher than {lv['pcr_pct'] * 100:.0f}% of the last year" if lv.get("pcr_pct") is not None else ""))
    if lv.get("iv") is not None:
        o.append(f"implied volatility {lv['iv'] * 100:.1f}%" + (f" (higher than {lv['iv_pct'] * 100:.0f}% of the last year)" if lv.get("iv_pct") is not None else ""))
    if lv.get("move10") and lv.get("fut"):
        o.append(f"which prices a normal {MAIN_H}-session range of {_n0(lv['fut'] - lv['move10'])} to {_n0(lv['fut'] + lv['move10'])}")
    t = ", ".join(o) + "." if o else ""
    if lv.get("put_wall") and lv.get("call_wall"):
        t += f" Heaviest put open interest sits at {_n0(lv['put_wall'])} and heaviest call open interest at {_n0(lv['call_wall'])}; max pain is {_n0(lv.get('max_pain'))}."
    if cm and cm.get("net_gex") is not None:
        if cm["net_gex"] > 0:
            t += " Estimated dealer gamma is positive, which tends to damp moves and pin price between the walls"
        else:
            t += " Estimated dealer gamma is negative, which tends to make moves run further in either direction"
        t += (f"; it changes sign near {_n0(cm['flip'])}." if cm.get("flip") else ".")
    if t:
        N.append({"h": "Options market", "t": t[0].upper() + t[1:]})
    p = []
    if mk.get("fii_ls") is not None:
        p.append(f"FIIs hold {mk['fii_ls'] * 100:.0f}% of their index futures long" + (f" ({mk['fii_ls_d5'] * 100:+.1f} pts in 5 sessions)" if mk.get("fii_ls_d5") is not None else ""))
    if mk.get("cli_ls") is not None:
        p.append(f"retail clients {mk['cli_ls'] * 100:.0f}% long")
    if mk.get("br_above") is not None:
        p.append(f"{mk['br_above'] * 100:.0f}% of F&O stocks are above their 50-day average")
    if p:
        N.append({"h": "Positioning", "t": (", ".join(p) + ".")[0].upper() + (", ".join(p) + ".")[1:]})
    A = M.get("analogs") if M else None
    if A and A["h"].get(MAIN_H):
        a = A["h"][MAIN_H]
        N.append({"h": "Days like today", "t": f"The {A['n']} past days closest to today across every input were followed, over {MAIN_H} sessions, by a median move of {_pp(a['median'])}; "
                                               f"{a['up'] * 100:.0f}% ended higher (normal {a['base_up'] * 100:.0f}%), the worst lost {_pp(a['worst'])} and the best gained {_pp(a['best'])}."})
    if reasons:
        N.append({"h": "Tested readings on today", "t": " ".join(r["text"] for r in reasons[:3])})
    w = []
    if lv.get("put_wall") and lv.get("fut") and lv["put_wall"] < lv["fut"]:
        w.append(f"a close below {_n0(lv['put_wall'])}, the put wall, means put writers are losing and support has failed")
    if ma50_level:
        w.append(f"the 50-day average is near {_n0(ma50_level)}")
    if lv.get("call_wall") and lv.get("fut") and lv["call_wall"] > lv["fut"]:
        w.append(f"a close above {_n0(lv['call_wall'])}, the call wall, forces call writers to cover")
    if w:
        N.append({"h": "Levels that change the picture", "t": ("; ".join(w) + ".")[0].upper() + ("; ".join(w) + ".")[1:]})
    return N


def participants_table(root=ROOT) -> dict | None:
    """Net positions of each group today and five sessions ago, in contracts."""
    p = Path(root) / "participants.csv"
    if not p.exists():
        return None
    try:
        raw = pd.read_csv(p)
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
        raw = raw.dropna(subset=["date"])
        days = np.sort(raw["date"].unique())
        if not len(days):
            return None
        now, then = days[-1], days[max(len(days) - 6, 0)]
        rows = []
        for who, name in (("FII", "FIIs"), ("Client", "Retail clients"), ("Pro", "Pro desks"), ("DII", "DIIs")):
            a = raw[(raw["who"].astype(str).str.upper() == who.upper())]
            x, y = a[a["date"] == now], a[a["date"] == then]
            if x.empty:
                continue
            f = lambda d: {"idx_fut": float(d["fi_long"].iloc[0] - d["fi_short"].iloc[0]), "stk_fut": float(d["fs_long"].iloc[0] - d["fs_short"].iloc[0]),
                           "idx_call": float(d["oi_call_long"].iloc[0] - d["oi_call_short"].iloc[0]), "idx_put": float(d["oi_put_long"].iloc[0] - d["oi_put_short"].iloc[0]),
                           "ls": float(d["fi_long"].iloc[0] / max(d["fi_long"].iloc[0] + d["fi_short"].iloc[0], 1))}
            cur = f(x)
            prev = f(y) if not y.empty else cur
            rows.append({"who": name, **cur, **{"d_" + k: cur[k] - prev[k] for k in cur}})
        return {"date": pd.Timestamp(now).date().isoformat(), "rows": rows}
    except Exception:
        return None


# ───────────── the full run ─────────────
def run(root=ROOT) -> dict:
    df = load_history(root)
    res = {"ok": False, "asof": None, "days": 0, "indexes": {}, "stocks": [], "notes": []}
    if df.empty:
        res["notes"].append("No F&O history found yet. Run the F&O data workflow in GitHub Actions first.")
        return res
    P = load_participants(root)
    chain = load_chain(root)
    D = prepare(df, P)
    D = D.drop(columns=[c for c in ("lr", "lpx", "doi", "fut_oi_near", "fut_oi_next", "iv_put", "iv_call", "straddle", "iv_dte", "ce_oi", "pe_oi", "sid", "lp") if c in D.columns])
    dates = np.sort(D["date"].unique())
    last = pd.Timestamp(dates[-1])
    res.update({"ok": True, "asof": last.date().isoformat(), "first": pd.Timestamp(dates[0]).date().isoformat(), "days": int(len(dates)),
                "cuts": int(D["cut"].sum()), "rows": int(len(D)), "horizons": list(HORIZONS), "main_h": MAIN_H, "_chain": chain})
    if len(dates) < YEAR:
        res["notes"].append(f"Only {len(dates)} trading days are stored. The model needs about two years before it can be tested; "
                            "run the F&O data workflow with 5 in the years box.")
    res["participants"] = participants_table(root)
    if len(P):
        tail = P.iloc[-130:]
        res["part_hist"] = {"date": [d.date().isoformat() for d in tail.index], **{k: [_f(v, 4) for v in tail[k]] for k in ("fii_ls", "cli_ls", "pro_ls", "dii_ls", "fii_net", "fii_opt") if k in tail}}

    # indexes: each one modelled and tested on its own history
    market_level = 0
    for sym in INDEXES:
        G = D[D["sym"] == sym].reset_index(drop=True)
        if G.empty or pd.Timestamp(G["date"].iloc[-1]) != last:
            continue
        T = Tester(G, pooled=False)
        by_h = {h: T.stats(h)[0] for h in HORIZONS}
        row = G.iloc[-1]
        lv = _levels(row)
        table = _signal_table(row, by_h)
        reasons = _reasons(table, NAMES[sym], relative=False)
        M = index_model(G)
        # a model with no record on unseen days gets no say; one with a thin record may only lean, never make the strong call
        level = 0 if not M or M["conf_n"] == 0 else M["level"] if M["conf_n"] >= 2 else max(-1, min(1, M["level"]))
        mk = {"br_above": _f(row["br_above"], 3), "br_flow": _f(row["br_flow"], 3),
              **{k: _f(row[k], 4) for k in ("fii_ls", "fii_ls_pct", "fii_ls_d5", "cli_ls", "cli_ls_pct", "pro_ls", "dii_ls", "fii_opt")}}
        cm = chain_view(sym, row["m_exp"], lv["fut"], last, chain)
        cw = chain_view(sym, row["w_exp"], lv["fut"], last, chain) if pd.notna(row.get("w_exp")) else None
        ma50_level = _f(row["fut"] * row["ma50"] / row["px"]) if pd.notna(row["ma50"]) and row["px"] else None
        tail = G.iloc[-130:]
        res["indexes"][sym] = {
            "sym": sym, "name": NAMES[sym], "level": level, "verdict": VERDICTS[level][0], "tone": VERDICTS[level][1], "model": M,
            "reasons": reasons, "levels": lv, "ma50": ma50_level, "signals": table, "days": int(len(G)),
            "counts": {g: sum(1 for s in table if s["grade"] == g) for g in ("proven", "leaning", "unproven", "thin")},
            "chain": {"m": cm, "w": cw}, "idea": option_idea(sym, lv, level, chain),
            "note": desk_note(NAMES[sym], lv, M, cm, mk, reasons, ma50_level), "swing10": _f(row.get("swing"), 5),
            "hist": {"date": [d.date().isoformat() for d in tail["date"]], **{k: [_f(v, 4) for v in tail[c]] for k, c in
                     (("px", "fut"), ("pcr", "pcr"), ("iv", "iv"), ("oi", "fut_oi"), ("fii", "fii_ls"), ("breadth", "br_above"))}}}
        res["indexes"][sym]["call"] = index_call(res["indexes"][sym])
        if sym == "NIFTY":
            market_level, res["market"] = level, mk
    res["market_level"] = market_level

    # stocks: one model for all of them, each day against the other F&O stocks
    is_stk = (D["kind"] != "IDX").to_numpy()
    Tday = D[is_stk & (D["date"] == last).to_numpy()].reset_index(drop=True)          # today's stock rows, with every column
    slim_cols = (["date", "sym", "kind", "fut", "fut_oi", "pcr", "iv", "m_exp", "n2", "ret", "swing"] + ["s_" + k for k in SIG_KEYS] + ["x_" + k for k in STK_F]
                 + [f"f{h}" for h in HORIZONS])
    K = D.loc[is_stk, slim_cols].reset_index(drop=True)                                # the history, cut down to what the tests need
    del D
    import gc
    gc.collect()
    if len(K):
        T = Tester(K, pooled=True)
        by_h = {h: T.stats(h)[0] for h in HORIZONS}
        res["stock_signals"] = [{"key": k, **SIG_INFO[k], "grade": grade(by_h[MAIN_H].get(k)), "stats": {h: by_h[h].get(k) for h in HORIZONS}}
                                for k in SIG_KEYS if SIG_INFO[k]["scope"] == "all"]
        SM = stock_model(K, last)
        res["stock_model"] = {k: v for k, v in (SM or {}).items() if k not in ("b", "pred")} if SM else None
        nifty = (res["indexes"].get("NIFTY") or {}).get("model") or {}
        try:
            res["stock_trade"] = stock_signals(K, SM["pred"], nifty.get("_wf") or {}) if SM else None
        except Exception as e:
            res["stock_trade"] = None
            res["notes"].append(f"Stock signals could not be replayed ({type(e).__name__}).")
        if res.get("stock_trade") is not None:
            res["stock_trade"]["trusted"] = bool(SM.get("conf_n", 0) >= 1)
        today = np.nonzero((K["date"] == last).to_numpy())[0]
        Xt = np.nan_to_num(Tday[["x_" + k for k in STK_F]].to_numpy(dtype=float))
        score = (Xt @ SM["b"] + SM["mu"]) if SM else np.zeros(len(today))
        rk = pd.Series(score).rank(pct=True).to_numpy() if SM else np.full(len(today), 0.5)
        bu_code = np.zeros(len(K), dtype=np.int8)
        for c, k in enumerate(BUILDUP, start=1):
            bu_code[K["s_" + k].to_numpy()] = c
        K["bu"] = bu_code
        for j, i in enumerate(today):
            row = Tday.iloc[j]
            level = _pct_level(float(rk[j])) if SM else 0
            on = [k for jj, k in enumerate(SIG_KEYS) if T.M[i, jj] and SIG_INFO[k]["scope"] == "all"]
            lv = _levels(row)
            res["stocks"].append({"sym": row["sym"], "level": level, "verdict": STOCK_VERDICTS[level][0], "tone": STOCK_VERDICTS[level][1], "score": float(score[j]),
                                  "pct": float(rk[j]), "on": on, "buildup": next((BUILDUP[k] for k in BUILDUP if k in on), ""), "levels": lv,
                                  "doi": _f(row.get("oi5"), 4), "drivers": _drivers(STK_F, SM["b"], Xt[j], row)[:6] if SM else [],
                                  "line": _stock_line(level, market_level)})
        res["stocks"].sort(key=lambda x: -x["score"])
        for n_, s_ in enumerate(res["stocks"], start=1):
            s_["rank"] = n_
        res["n_stocks"] = len(res["stocks"])
        keep = ["date", "sym", "fut", "fut_oi", "pcr", "iv", "bu", "m_exp"] + ["x_" + k for k in STK_F] + [f"f{h}" for h in HORIZONS]
        slim = K[keep].copy()
        slim["sym"] = slim["sym"].astype("category")
        res["_K"], res["_b"] = slim, (SM["b"] if SM else None)
    if res.get("cuts"):
        res["notes"].append(f"{res['cuts']} one-day jumps of 20% or more (bonuses, splits, demergers) were cut out of the price series so they cannot fake a signal.")
    return res


def stock_detail(res: dict, sym: str) -> dict | None:
    """Everything shown when one stock is opened: its drivers, history, days like today, option chain and tested readings."""
    s = next((x for x in res.get("stocks", []) if x["sym"] == sym), None)
    if not s:
        return None
    out = dict(s)
    table = [{**sig, "on": sig["key"] in s["on"]} for sig in res.get("stock_signals", [])]
    out["signals"], out["reasons"] = table, _reasons(table, "stocks like this", relative=True)
    K = res.get("_K")
    if K is not None:
        g = K[K["sym"] == sym]
        tail = g.iloc[-130:]
        out["hist"] = {"date": [d.date().isoformat() for d in tail["date"]], **{k: [_f(v, 4) for v in tail[c]] for k, c in (("px", "fut"), ("oi", "fut_oi"), ("pcr", "pcr"), ("iv", "iv"))},
                       "bu": [int(v) for v in g["bu"].iloc[-40:]]}
        if res.get("_b") is not None and len(g) > 120:
            Xn = g[["x_" + k for k in STK_F]].to_numpy(dtype=float)
            has = (~np.isnan(Xn)).sum(axis=1) >= 4
            out["analogs"] = analogs(np.nan_to_num(Xn), {h: g[f"f{h}"].to_numpy(dtype=float) for h in HORIZONS}, g["date"].to_numpy(), len(g) - 1, res["_b"], has, k=16)
        out["chain"] = chain_view(sym, g["m_exp"].iloc[-1], s["levels"]["fut"], res["asof"], res.get("_chain", pd.DataFrame()))
    return out


_CACHE: dict = {}
_LOCK = threading.Lock()


def run_cached(root=ROOT) -> dict:
    """The engine's answer, recomputed only when the stored data changes."""
    sig = data_signature(root)
    with _LOCK:
        if _CACHE.get("sig") != sig:
            _CACHE["res"], _CACHE["sig"] = run(root), sig
            import gc
            gc.collect()                 # the working tables are large; hand the memory back straight away
        return _CACHE["res"]


def run_soon(root=ROOT) -> dict | None:
    """For pages that must paint at once (Home): the answer if it is ready, otherwise None while it is worked out in the background."""
    try:
        sig = data_signature(root)
    except Exception:
        return None
    if _CACHE.get("sig") == sig:
        return _CACHE.get("res")
    if not _CACHE.get("busy"):
        _CACHE["busy"] = True

        def work():
            try:
                run_cached(root)
            except Exception:
                pass
            finally:
                _CACHE["busy"] = False
        threading.Thread(target=work, daemon=True).start()
    return None


# ───────────── text report ─────────────
def report(res: dict) -> str:
    if not res.get("ok"):
        return "\n".join(res.get("notes") or ["No data."])
    L = [f"F&O engine · data to {res['asof']} · {res['days']} trading days from {res['first']} · {res.get('n_stocks', 0)} stocks", ""]
    for x in res["indexes"].values():
        L.append(f"{x['name']}: {x['verdict']}")
        L += [f"  {n['h']}: {n['t']}" for n in x["note"]]
        M = x.get("model") or {}
        for lvl, st in (M.get("by") or {}).items():
            L.append(f"  record '{VERDICTS[lvl][0]}': {st['n']} days, {st['neff']} episodes, next {MAIN_H} sessions {_pp(st['mean'])}, up {st['hit'] * 100:.0f}%")
        if M.get("stats"):
            a, b = M["stats"]["model"], M["stats"]["hold"]
            L.append(f"  test strategy since {M['stats']['from']}: {_pp(a.get('cagr'))} a year, worst fall {_pp(a.get('dd'))} | buy and hold {_pp(b.get('cagr'))}, worst fall {_pp(b.get('dd'))}")
        L.append("")
    SM = res.get("stock_model")
    if SM and SM.get("ic") is not None:
        L.append(f"Stock model: rank correlation {SM['ic']:+.3f} (t {SM['ic_t']:+.1f}) on {SM['oos_days']} unseen days; fifths {[round(q * 100, 2) if q is not None else None for q in SM.get('quint', [])]}")
    if res.get("stocks"):
        L.append("Top 5: " + ", ".join(s["sym"] for s in res["stocks"][:5]) + " | Bottom 5: " + ", ".join(s["sym"] for s in res["stocks"][-5:]))
    return "\n".join(L + [""] + res.get("notes", []))


if __name__ == "__main__":
    import sys
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    print(report(r))
