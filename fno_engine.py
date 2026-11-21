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
SIG_KEYS = [s[0] for s in SIGNALS]
SIG_INFO = {s[0]: {"group": s[1], "label": s[2], "note": s[3], "scope": s[4]} for s in SIGNALS}
BUILDUP = {"bu_long": "Long build-up", "bu_short": "Short build-up", "bu_cover": "Short covering", "bu_unwind": "Long unwinding"}

VERDICTS = {2: ("Rally setup", "up"), 1: ("Stay invested", "up"), 0: ("No proven edge", "flat"), -1: ("Reduce risk", "dn"), -2: ("Correction risk", "dn")}
STOCK_VERDICTS = {2: ("Strong setup", "up"), 1: ("Leaning up", "up"), 0: ("Neutral", "flat"), -1: ("Leaning down", "dn"), -2: ("Weak setup", "dn")}


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
    return df.drop_duplicates(["sym", "date"], keep="last").sort_values(["sym", "date"]).reset_index(drop=True)


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
    r = D.groupby(by, sort=False)[col].rolling(n, min_periods=minp)
    r = r.rank(pct=True) if how == "rank" else (r.mean() if how == "mean" else r.sum())
    return r.reset_index(level=0, drop=True).reindex(D.index)


def prepare(df: pd.DataFrame, P: pd.DataFrame | None = None) -> pd.DataFrame:
    """Adds a clean return series, forward returns, every measure and every signal column (s_<key>) to the history."""
    D = df                                  # worked on in place: the history is large and a second copy would double the memory
    idx = (D["kind"] == "IDX").to_numpy()
    g = D.groupby("sym", sort=False)
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
    D["lpx"] = s["lr"].cumsum()
    D["px"] = np.exp(D["lpx"])
    for h in HORIZONS:
        f = np.exp(s["lpx"].shift(-(1 + h)) - s["lpx"].shift(-1)) - 1
        late = (s["date"].shift(-(1 + h)) - D["date"]).dt.days > (2 * h + 12)
        D[f"f{h}"] = f.where(~late)
    D["r5"] = np.exp(D["lpx"] - s["lpx"].shift(5)) - 1
    D["r20"] = np.exp(D["lpx"] - s["lpx"].shift(20)) - 1
    D["ma50"] = _grp_roll(D, "sid", "px", 50, 50, "mean")
    # futures open interest: day-to-day change, ignoring the day the contract rolls and any cut day
    doi = np.log(D["fut_oi"].where(D["fut_oi"] > 0) / poi.where(poi > 0))
    D["doi"] = doi.where(~(rolled | brk), 0.0).clip(-1, 1).fillna(0.0)
    D["oi5"] = _grp_roll(D, "sid", "doi", 5, 5, "sum")
    D["pcr"] = (D["pe_oi"] / D["ce_oi"].where(D["ce_oi"] > 0)).where(lambda x: (x > 0.02) & (x < 20))
    D["pcr_pct"] = _grp_roll(D, "sym", "pcr", YEAR, 120, "rank")
    D["iv"] = D["iv"].where((D["iv"] > 0.04) & (D["iv"] < np.where(idx, 0.8, 1.5)))
    D["iv_pct"] = _grp_roll(D, "sym", "iv", YEAR, 120, "rank")
    D["iv_d5"] = D["iv"] / g["iv"].shift(5) - 1
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
    rel = {2: "is set up to beat other F&O stocks", 1: "leans stronger than other F&O stocks", 0: "has no proven edge either way",
           -1: "leans weaker than other F&O stocks", -2: "is set up to lag other F&O stocks"}[level]
    mkt = {2: "the market view is positive", 1: "the market view is mildly positive", 0: "the market view is neutral",
           -1: "the market view is mildly negative", -2: "the market view is negative"}[mk]
    return f"On its own positioning this stock {rel}; {mkt}."


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
    D = D.drop(columns=[c for c in ("lr", "lpx", "doi", "fut_oi_near", "fut_oi_next", "iv_put", "iv_call", "straddle", "iv_dte", "ce_oi", "pe_oi", "sid") if c in D.columns])
    dates = np.sort(D["date"].unique())
    last = pd.Timestamp(dates[-1])
    res.update({"ok": True, "asof": last.date().isoformat(), "first": pd.Timestamp(dates[0]).date().isoformat(), "days": int(len(dates)),
                "cuts": int(D["cut"].sum()), "rows": int(len(D)), "horizons": list(HORIZONS), "main_h": MAIN_H})
    if len(dates) < YEAR:
        res["notes"].append(f"Only {len(dates)} trading days are stored. Signals need about a year before they can be tested; "
                            "run the F&O data workflow with 5 in the years box.")

    # indexes: each one tested on its own history
    market_level = 0
    for sym in INDEXES:
        G = D[D["sym"] == sym].reset_index(drop=True)
        if G.empty or pd.Timestamp(G["date"].iloc[-1]) != last:
            continue
        T = Tester(G, pooled=False)
        by_h = {h: T.stats(h)[0] for h in HORIZONS}
        st, sd = T.stats(MAIN_H)
        w = _weights(st)
        z = float(_score(T.M[-1:], w)[0] / sd) if sd and sd == sd else 0.0
        level = _level(z, IDX_MILD, IDX_STRONG)
        row = G.iloc[-1]
        lv = _levels(row)
        table = _signal_table(row, by_h)
        reasons = _reasons(table, NAMES[sym], relative=False)
        res["indexes"][sym] = {
            "sym": sym, "name": NAMES[sym], "level": level, "verdict": VERDICTS[level][0], "tone": VERDICTS[level][1], "z": z,
            "tilt": float(_score(T.M[-1:], w)[0]), "swing": sd, "line": _index_line(level, lv, reasons, NAMES[sym]), "reasons": reasons,
            "levels": lv, "signals": table, "track": T.walk_forward(MAIN_H, IDX_MILD, IDX_STRONG), "days": int(len(G)),
            "counts": {g: sum(1 for s in table if s["grade"] == g) for g in ("proven", "leaning", "unproven", "thin")},
            "idea": option_idea(sym, lv, level, chain),
            "hist": {"date": [d.date().isoformat() for d in G["date"].iloc[-130:]], "px": [_f(v, 2) for v in G["fut"].iloc[-130:]],
                     "pcr": [_f(v, 3) for v in G["pcr"].iloc[-130:]], "iv": [_f(v, 4) for v in G["iv"].iloc[-130:]],
                     "oi": [_f(v) for v in G["fut_oi"].iloc[-130:]]}}
        if sym == "NIFTY":
            market_level = level
            res["market"] = {"br_above": _f(row["br_above"], 3), "br_flow": _f(row["br_flow"], 3),
                             **{k: _f(row[k], 4) for k in ("fii_ls", "fii_ls_pct", "fii_ls_d5", "cli_ls", "cli_ls_pct", "pro_ls", "dii_ls", "fii_opt")}}
    res["market_level"] = market_level

    # stocks: tested together, each day against the other F&O stocks
    K = D[D["kind"] != "IDX"].reset_index(drop=True)
    if len(K):
        T = Tester(K, pooled=True)
        by_h = {h: T.stats(h)[0] for h in HORIZONS}
        st, sd = T.stats(MAIN_H)
        w = _weights(st)
        today = np.nonzero((K["date"] == last).to_numpy())[0]
        sc = _score(T.M[today], w)
        res["stock_signals"] = [{"key": k, **SIG_INFO[k], "grade": grade(by_h[MAIN_H].get(k)), "stats": {h: by_h[h].get(k) for h in HORIZONS}}
                                for k in SIG_KEYS if SIG_INFO[k]["scope"] == "all"]
        res["stock_track"] = T.walk_forward(MAIN_H, STK_MILD, STK_STRONG)
        res["stock_swing"] = sd
        for i, s_ in zip(today, sc):
            row = K.iloc[i]
            z = float(s_ / sd) if sd and sd == sd else 0.0
            level = _level(z, STK_MILD, STK_STRONG)
            on = [k for j, k in enumerate(SIG_KEYS) if T.M[i, j] and SIG_INFO[k]["scope"] == "all"]
            lv = _levels(row)
            res["stocks"].append({"sym": row["sym"], "level": level, "verdict": STOCK_VERDICTS[level][0], "tone": STOCK_VERDICTS[level][1], "z": z, "tilt": float(s_),
                                  "on": on, "buildup": next((BUILDUP[k] for k in BUILDUP if k in on), ""), "levels": lv, "line": _stock_line(level, market_level)})
        res["stocks"].sort(key=lambda x: -x["z"])
        res["n_stocks"] = len(res["stocks"])
    if res.get("cuts"):
        res["notes"].append(f"{res['cuts']} one-day jumps of 20% or more (bonuses, splits, demergers) were cut out of the price series so they cannot fake a signal.")
    return res


def stock_detail(res: dict, sym: str) -> dict | None:
    """Everything shown when one stock is opened: its readings, each with the pooled record."""
    s = next((x for x in res.get("stocks", []) if x["sym"] == sym), None)
    if not s:
        return None
    table = [{**sig, "on": sig["key"] in s["on"]} for sig in res.get("stock_signals", [])]
    return {**s, "signals": table, "reasons": _reasons(table, "stocks like this", relative=True)}


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
def _p(x, d=1):
    return "n/a" if x is None or x != x else f"{x * 100:+.{d}f}%"


def report(res: dict) -> str:
    if not res.get("ok"):
        return "\n".join(res.get("notes") or ["No data."])
    L = [f"F&O engine · data to {res['asof']} · {res['days']} trading days from {res['first']} · {res.get('n_stocks', 0)} stocks", ""]
    for sym, x in res["indexes"].items():
        lv = x["levels"]
        L += [f"{x['name']}: {x['verdict']}  (score {x['z']:+.2f} of a normal {MAIN_H}-day swing)", "  " + x["line"],
              f"  future {lv['fut']:,.1f} · PCR {lv['pcr']} · IV {(lv['iv'] or 0) * 100:.1f}% · max pain {lv['max_pain']} · walls {lv['put_wall']} / {lv['call_wall']}",
              f"  signals: {x['counts']}"]
        for r in x["reasons"]:
            L.append("  - " + r["text"])
        tr = x["track"]
        for lvl, st in tr.get("by", {}).items():
            L.append(f"  record when it said '{VERDICTS[lvl][0]}': {st['n']} days, {st['neff']} episodes, next {MAIN_H} sessions {_p(st['mean'])}, up {st['hit'] * 100:.0f}% of the time")
        if x["idea"].get("name"):
            L.append(f"  option structure: {x['idea']['name']} · {x['idea']['why']}")
        L.append("")
    if res.get("stock_signals"):
        L.append("Stock signals (against other F&O stocks, next 10 sessions):")
        for s in res["stock_signals"]:
            st = s["stats"].get(MAIN_H)
            if st:
                L.append(f"  {s['label']:<32} {s['grade']:<9} edge {_p(st['edge'], 2)}  t {st['t']:+.1f}  episodes {st['neff']}")
        L.append("")
        L.append("Top 5: " + ", ".join(f"{s['sym']} ({s['verdict']})" for s in res["stocks"][:5]))
        L.append("Bottom 5: " + ", ".join(f"{s['sym']} ({s['verdict']})" for s in res["stocks"][-5:]))
    L += [""] + res.get("notes", [])
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    print(report(r))
    if "--json" in sys.argv:
        print(json.dumps({k: v for k, v in r.items() if k not in ("stocks",)}, default=str)[:4000])
