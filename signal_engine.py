"""
Arka Trades · Signal engine v3 (signal_engine.py)

Runs in GitHub Actions every evening (workflow: signals.yml), not inside the app. It rebuilds eleven years of NSE cash
history, replays every rule trade by trade, moves the two live books forward by one session and writes small files to
arka_data/signals/ that the app only has to read.

What it trades, and why (everything was tested on real NSE prices in November 2026; the Evidence tab shows the numbers,
recomputed on every run):
  LEADERS   the trend book. Hold the 20 strongest stocks by 12-month strength among the 350 most traded. Checked once
            a week; a holding is kept while it stays in the top 20% and among the 500 most traded. This is the only trend
            idea that beat the average stock in every period tested.
  SWING     buy a strong stock after a sharp fall, hold a few days. About two trades in three win; most of the gain is
            the market bouncing after a sell-off.
  INDEX     buy NIFTY 50 / BANK NIFTY after a 2.5% fall inside an uptrend, hold a few days.
Tested and rejected (shown as context only): 52-week-high breakouts, delivery accumulation, heavy-volume surge days,
stops and market-timing rules on the leaders book. None beat simply owning the average liquid stock.

Data
  history   the public repository github.com/BennyThadikaran/eod2_data (NSE end-of-day files with delivery, adjusted for
            splits and bonuses, updated weekly). The workflow clones it.
  top-up    sessions after that repository's last date come straight from NSE's daily file sec_bhavdata_full, chained on
            with NSE's own adjusted previous close, so a split or bonus cannot fake a move.
  universe  on each day, the 350 most traded ordinary shares (60-day median turnover; ETFs and trade-to-trade days are
            left out). Decided day by day, never with hindsight.

Execution assumed everywhere: the signal is known after the close, you trade at the NEXT session's open, stops are
checked against the day's low (a gap below the stop sells at the open), cost 0.15% a side for stocks.

The live books are kept in arka_data/signals/state.json and only ever move forward, so what the desk told you
yesterday stays true even if the history files are revised. Every order is also written to log.csv on the day it is
issued: that file is the real record. The history leaves out companies that were later delisted, so old results are
flattered. Nothing here is investment advice.

  python signal_engine.py --eod eod2_data [--send]
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
OUT = Path("arka_data") / "signals"
FNO = Path("arka_data") / "fno"
START, TOP, COST = "2014-06-01", 350, 0.0015
SPLIT = "2022-01-01"                                   # every record is shown for before and after this date
LEAD_N, LEAD_BUY, LEAD_KEEP = 20, 0.90, 0.80          # hold 20 · buy from the top 10% · keep while in the top 20%
KEEP_TOP = 500                                        # a stock already held is kept while it is among the 500 most traded
SW_RANK, SW_FALL, SW_STOP, SW_DAYS = 0.70, 3.0, 3.0, 7
IDX_FALL, IDX_DAYS, IDX_COST = 0.025, 7, 0.0005
INDEXES = {"NIFTY": "nifty 50", "BANKNIFTY": "nifty bank"}
INDEX_NAME = {"NIFTY": "NIFTY 50", "BANKNIFTY": "BANK NIFTY"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEAD = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/all-reports"}

RULES = {
    "leaders": {"name": "Leaders", "book": "Trend",
                "rule": "Hold the 20 strongest stocks by 12-month strength (price a month ago against a year ago) among the 350 most traded. "
                        "Checked after the first session of each week.",
                "exit": "Sell at the next open when a holding is no longer in the top 20% by strength, or is no longer among the 500 most traded. No stop: every stop tested made this book worse.",
                "size": "One twentieth of the book in each stock."},
    "swing": {"name": "Leader pullback", "book": "Swing",
              "rule": "A stock in the top 30% by 12-month strength, above its 200-day average with the 50-day above the 200-day, falls 3 ATR or more in 5 sessions.",
              "exit": "Sell at the next open after a close above the 5-day average, or after 7 sessions. Stop 3 ATR below the entry.",
              "size": "Risk a fixed part of capital between entry and stop."},
    "index": {"name": "Dip in an uptrend", "book": "Index",
              "rule": "The index is above its 200-day average and has fallen 2.5% or more in 5 sessions.",
              "exit": "Sell at the next open after a close above the 5-day average, or after 7 sessions. No stop was tested: use a spread or small size.",
              "size": "Futures, or a call spread that risks only its cost."},
}


# ───────────── small helpers ─────────────
def _f(x, nd=None):
    try:
        x = float(x)
        if x != x or math.isinf(x):
            return None
        return round(x, nd) if nd is not None else x
    except Exception:
        return None


def _clean(o):
    """Make anything safe for JSON: no NaN, no numpy types."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        v = float(o)
        return None if (v != v or math.isinf(v)) else v
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()[:10]
    return o


def _ordinary(sym: str, s2i: dict) -> bool:
    """Ordinary shares only: ETFs, liquid funds, REITs and DVRs carry a different ISIN pattern."""
    isin = s2i.get(sym.upper(), "")
    if isin:
        return isin.startswith("INE") and isin[7:9] == "01"
    return not re.search(r"BEES|ETF|LIQUID|NIFTY|SENSEX", sym.upper())


# ───────────── history ─────────────
def build_panels(eod_dir: str, say=print) -> dict:
    """Open / high / low / close / volume / delivered quantity / trades for every ordinary share that was ever among the TOP most traded."""
    root = Path(eod_dir)
    src = root / "daily"
    files = sorted(glob.glob(str(src / "*.csv")))
    if not files:
        raise SystemExit(f"No price files found in {src}. Clone https://github.com/BennyThadikaran/eod2_data first.")
    try:
        s2i = {k.upper(): v for k, v in json.loads((root / "isin_symbol_map.json").read_text())["sym2isin"].items()}
    except Exception:
        s2i = {}
    to = {}
    for f in files:
        name = os.path.basename(f)[:-4]
        if " " in name or name.startswith("nifty") or not _ordinary(name, s2i):
            continue
        try:
            d = pd.read_csv(f, usecols=["Date", "Close", "Volume"], parse_dates=["Date"])
        except Exception:
            continue
        d = d[d["Date"] >= START]
        if len(d) >= 250:
            to[name] = pd.Series((d["Close"] * d["Volume"]).to_numpy(dtype="float64"), index=d["Date"])
    T = pd.DataFrame(to).sort_index()
    T = T[~T.index.duplicated(keep="last")]
    elig = T.rolling(60, min_periods=40).median().rank(axis=1, ascending=False) <= TOP
    keep = [c for c in T.columns if elig[c].any()]
    say(f"{T.shape[1]} ordinary shares with history, {len(keep)} ever among the {TOP} most traded, {len(T)} sessions to {T.index[-1].date()}")
    cols = {"O": "Open", "H": "High", "L": "Low", "C": "Close", "V": "Volume", "DQ": "DLV_QTY", "NT": "TOTAL_TRADES"}
    P = {k: {} for k in list(cols) + ["EQ"]}
    for s in keep:
        d = pd.read_csv(src / f"{s}.csv", parse_dates=["Date"])
        d = d[d["Date"] >= START].drop_duplicates("Date", keep="last").set_index("Date")
        for k, c in cols.items():
            P[k][s.upper()] = pd.to_numeric(d[c], errors="coerce").astype("float64") if c in d else pd.Series(dtype="float64")
        P["EQ"][s.upper()] = (d["Series"].astype(str).str.strip() == "EQ") if "Series" in d else pd.Series(True, index=d.index)
    out = {k: pd.DataFrame(v).reindex(T.index) for k, v in P.items()}
    out["EQ"] = out["EQ"].fillna(False).astype(bool)
    out["T"] = T[keep].rename(columns=str.upper)
    out["ISIN"] = s2i
    out["IDX"] = {}
    for key, fn in list(INDEXES.items()) + [("N500", "nifty 500"), ("VIX", "india vix")]:
        try:
            d = pd.read_csv(src / f"{fn}.csv", parse_dates=["Date"]).drop_duplicates("Date", keep="last").set_index("Date")
            out["IDX"][key] = d[["Open", "High", "Low", "Close"]].astype(float).sort_index()
        except Exception:
            pass
    return out


def parse_bhav(text: str) -> pd.DataFrame:
    """NSE sec_bhavdata_full for one day -> one row per symbol: series, prev, o, h, l, c, v, nt, dq (EQ preferred over BE / BZ)."""
    df = pd.read_csv(io.StringIO(text), dtype=str, skipinitialspace=True)
    df.columns = [c.strip().upper() for c in df.columns]
    need = {"SYMBOL": "sym", "SERIES": "series", "PREV_CLOSE": "prev", "OPEN_PRICE": "o", "HIGH_PRICE": "h", "LOW_PRICE": "l", "CLOSE_PRICE": "c",
            "TTL_TRD_QNTY": "v", "NO_OF_TRADES": "nt", "DELIV_QTY": "dq"}
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"bhavcopy format changed, missing {miss}")
    df = df[list(need)].rename(columns=need)
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    df = df[df["series"].isin(["EQ", "BE", "BZ"])].copy()
    for c in ("prev", "o", "h", "l", "c", "v", "nt", "dq"):
        df[c] = pd.to_numeric(df[c].where(~df[c].isin(["-", "", "nan"])), errors="coerce")
    df["_k"] = (df["series"] != "EQ").astype(int)
    return df.sort_values("_k").drop_duplicates("sym").drop(columns="_k").set_index("sym")


def add_day(P: dict, day, bhav: pd.DataFrame) -> int:
    """Chain one NSE day onto the adjusted history. NSE's previous close is already adjusted for splits and bonuses on the
    ex-date, so close / previous close is the true move; open, high and low are scaled by the same factor."""
    day = pd.Timestamp(day)
    if day in P["C"].index or bhav is None or bhav.empty:
        return 0
    last = P["C"].ffill().iloc[-1]
    row = {k: {} for k in ("O", "H", "L", "C", "V", "DQ", "NT", "EQ")}
    n = 0
    for s in P["C"].columns:
        if s not in bhav.index:
            continue
        b = bhav.loc[s]
        lp = last.get(s, np.nan)
        if not (b["c"] > 0 and b["prev"] > 0 and lp == lp):
            continue
        adj = float(lp) * b["c"] / b["prev"]
        k = adj / b["c"]
        row["C"][s], row["O"][s], row["H"][s], row["L"][s] = adj, b["o"] * k, b["h"] * k, b["l"] * k
        row["V"][s], row["DQ"][s], row["NT"][s] = b["v"] / k, (b["dq"] / k if b["dq"] == b["dq"] else np.nan), b["nt"]
        row["EQ"][s] = b["series"] == "EQ"
        n += 1
    if n < 100:
        return 0
    for k in ("O", "H", "L", "C", "V", "DQ", "NT"):
        P[k].loc[day] = pd.Series(row[k], dtype="float64")
    P["EQ"].loc[day] = pd.Series(row["EQ"], dtype="object").reindex(P["EQ"].columns).fillna(False).astype(bool)
    P["T"].loc[day] = (P["C"].loc[day] * P["V"].loc[day]).reindex(P["T"].columns)
    return n


def parse_indices(text: str) -> dict:
    df = pd.read_csv(io.StringIO(text), dtype=str, skipinitialspace=True)
    df.columns = [c.strip().lower() for c in df.columns]
    name = next((c for c in df.columns if c.startswith("index name")), None)
    pick = lambda w: next((c for c in df.columns if w in c and "change" not in c), None)
    co, ch, cl, cc = pick("open"), pick("high"), pick("low"), pick("clos")
    if not (name and cc):
        return {}
    want = {"nifty 50": "NIFTY", "nifty bank": "BANKNIFTY", "nifty 500": "N500", "india vix": "VIX"}
    out = {}
    for _, r in df.iterrows():
        k = want.get(str(r[name]).strip().lower())
        if k:
            f = lambda c: pd.to_numeric(r[c], errors="coerce") if c else np.nan
            out[k] = {"Open": f(co), "High": f(ch), "Low": f(cl), "Close": f(cc)}
    return out


def fno_index_closes() -> dict:
    """Index closes kept by the F&O store: a fallback when NSE's index file cannot be read."""
    out = {}
    try:
        for f in sorted(glob.glob(str(FNO / "u" / "*.csv")))[-2:]:
            d = pd.read_csv(f, usecols=["date", "sym", "kind", "spot"])
            d = d[(d["kind"] == "IDX") & d["sym"].isin(list(INDEXES))]
            for _, r in d.iterrows():
                if r["spot"] == r["spot"]:
                    out.setdefault(str(r["sym"]), {})[str(r["date"])] = float(r["spot"])
    except Exception:
        pass
    return out


def top_up(P: dict, today: date, fetch, say=print) -> list:
    """Add the sessions NSE has published after the history's last date. `fetch(url)` returns the text or None."""
    added, d = [], P["C"].index[-1].date() + timedelta(days=1)
    while d <= today:
        txt = fetch(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
        if txt and "SYMBOL" in txt[:400].upper():
            try:
                if add_day(P, d, parse_bhav(txt)):
                    added.append(d.isoformat())
                    it = fetch(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv")
                    got = parse_indices(it) if it else {}
                    for k, v in got.items():
                        if k in P["IDX"] and v["Close"] == v["Close"]:
                            P["IDX"][k].loc[pd.Timestamp(d)] = pd.Series(v)
            except Exception as e:
                say(f"  {d}: could not be added ({type(e).__name__}: {str(e)[:80]})")
        d += timedelta(days=1)
    return added


def finish(P: dict) -> dict:
    """Sort, fill short gaps (a stock that misses up to ten sessions keeps its last price; after that it is treated as gone)
    and patch missing index closes from the F&O store."""
    for k in ("O", "H", "L", "C", "V", "DQ", "NT", "T", "EQ"):
        P[k] = P[k].sort_index()
    P["C"] = P["C"].ffill(limit=10)
    P["EQ"] = P["EQ"].astype(bool)
    spots = fno_index_closes()
    for k in INDEXES:
        d = P["IDX"].get(k)
        if d is None:
            continue
        d = d.sort_index()
        for iso, v in sorted((spots.get(k) or {}).items()):
            ts = pd.Timestamp(iso)
            if ts > d.index[-1]:
                d.loc[ts] = pd.Series({"Open": float(d["Close"].iloc[-1]), "High": v, "Low": v, "Close": v})
        P["IDX"][k] = d.sort_index()
    return P


def http_fetch(url: str):
    import requests
    for host in ("nsearchives.nseindia.com", "archives.nseindia.com"):
        try:
            r = requests.get(url.replace("nsearchives.nseindia.com", host), headers=HEAD, timeout=25)
            if r.status_code == 200 and len(r.text) > 200:
                return r.text
            if r.status_code == 404:
                return None
        except Exception:
            continue
    return None


# ───────────── measures ─────────────
def indicators(P: dict) -> dict:
    H, L, C, V, DQ, NT = (P[k] for k in ("H", "L", "C", "V", "DQ", "NT"))
    trank = P["T"].rolling(60, min_periods=40).median().rank(axis=1, ascending=False).reindex(columns=C.columns)
    I = {"elig": (trank <= TOP) & P["EQ"], "wide": (trank <= KEEP_TOP) & P["EQ"]}
    el = I["elig"]
    pc = C.shift(1)
    tr = np.fmax(np.fmax((H - L).to_numpy(), (H - pc).abs().to_numpy()), (L - pc).abs().to_numpy())
    I["atr"] = pd.DataFrame(tr, index=C.index, columns=C.columns).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / C
    for k in (5, 50, 200):
        I[f"ma{k}"] = C.rolling(k, min_periods=k).mean()
    I["hi252"] = H.rolling(252, min_periods=252).max().shift(1)
    I["up"] = (C > I["ma200"]) & (I["ma50"] > I["ma200"])
    m12 = C.shift(21) / C.shift(252) - 1
    I["rs12"] = m12.where(el).rank(axis=1, pct=True)
    # the same strength mark for stocks just outside the 350 (they can be held, not bought): where they would stand among the 350
    mv, ev, wv, keep = m12.to_numpy(float), el.to_numpy(), I["wide"].to_numpy(), I["rs12"].to_numpy(float).copy()
    for t in range(len(C)):
        extra = wv[t] & ~ev[t] & ~np.isnan(mv[t])
        if extra.any():
            base = np.sort(mv[t][ev[t] & ~np.isnan(mv[t])])
            if len(base) >= 100:
                keep[t, extra] = np.searchsorted(base, mv[t][extra], side="right") / len(base)
    I["keep"] = pd.DataFrame(keep, index=C.index, columns=C.columns)
    I["r5"], I["r10"], I["r1"] = C / C.shift(5) - 1, C / C.shift(10) - 1, C / pc - 1
    I["acc10"] = DQ.rolling(10, min_periods=8).sum() / (DQ.rolling(60, min_periods=45).sum().shift(10) / 6)
    I["dqx"] = DQ / DQ.rolling(20, min_periods=15).mean().shift(1)
    I["volx"] = V / V.rolling(50, min_periods=30).mean().shift(1)
    I["dshare"] = DQ / V
    ats = V / NT
    I["atx"] = ats / ats.rolling(50, min_periods=30).mean().shift(1)
    I["rng"] = (C - L) / (H - L).replace(0, np.nan)
    cnt = el.sum(axis=1).clip(lower=1)
    I["b50"], I["b200"] = ((C > I["ma50"]) & el).sum(axis=1) / cnt, ((C > I["ma200"]) & el).sum(axis=1) / cnt
    I["highs"] = ((C >= C.rolling(252, min_periods=200).max()) & el).sum(axis=1)
    I["lows"] = ((C <= C.rolling(252, min_periods=200).min()) & el).sum(axis=1)
    base = P["IDX"].get("N500", P["IDX"].get("NIFTY"))
    I["n500"] = base["Close"].reindex(C.index).ffill()
    I["avg"] = (C / pc - 1).where(el.shift(1, fill_value=False)).mean(axis=1).fillna(0.0)        # the average liquid stock, one day
    return I


def bundle(P: dict, I: dict) -> dict:
    C = P["C"]
    iso = [d.date().isoformat() for d in C.index]
    wk = C.index.isocalendar()
    code = pd.Series(wk["year"].to_numpy().astype(int) * 100 + wk["week"].to_numpy().astype(int))
    up = I["up"]
    entry = ((I["rs12"] >= SW_RANK) & up & (I["r5"] <= -SW_FALL * I["atr"]) & I["elig"]).fillna(False)
    return {"o": P["O"].to_numpy(float), "l": P["L"].to_numpy(float), "c": C.to_numpy(float), "atr": I["atr"].to_numpy(float), "ma5": I["ma5"].to_numpy(float),
            "rk": I["rs12"].to_numpy(float), "kr": I["keep"].to_numpy(float), "eq": P["EQ"].to_numpy(), "entry": entry.to_numpy(), "sym": list(C.columns), "jx": {s: j for j, s in enumerate(C.columns)}, "iso": iso,
            "ix": {d: t for t, d in enumerate(iso)}, "n": len(iso), "pos": code.groupby(code).cumcount().to_numpy(), "wlen": code.groupby(code).transform("size").to_numpy(),
            "bi": np.cumprod(1 + I["avg"].to_numpy(float))}


def check_days(X: dict, k: int = 0) -> np.ndarray:
    """The weekly check: after the first session of each week (k = 0). Other k are only used to show how much the weekday matters."""
    return X["pos"] == np.minimum(k, X["wlen"] - 1)


# ───────────── the leaders book ─────────────
def leaders_run(X: dict, chk, st: dict | None = None, t0: int = 0, t1: int | None = None):
    """Move the book from session t0 to t1. Returns the state, the book's value after each session and the trades it closed."""
    o, c, rk, kr, sym, jx, iso, ix = X["o"], X["c"], X["rk"], X["kr"], X["sym"], X["jx"], X["iso"], X["ix"]
    t1 = X["n"] if t1 is None else t1
    st = st or {"cash": 100.0, "held": {}, "buy": [], "sell": {}, "last": None}
    held, eq, trades = st["held"], np.full(max(t1 - t0, 0), np.nan), []

    def close(s_, h_, px, t, d, why):
        st["cash"] += h_["sh"] * px * (1 - COST)
        t_in = ix.get(h_["since"])
        trades.append({"sym": s_, "in": h_["since"], "out": d, "p0": h_["p0"], "p1": float(px), "ret": float(px * (1 - COST) / (h_["p0"] * (1 + COST)) - 1),
                       "bars": (t - t_in) if t_in is not None else None, "why": why, "t_in": t_in, "t_out": t})

    for t in range(t0, t1):
        d = iso[t]
        if st["sell"] or st["buy"]:                                  # yesterday's orders, at today's open
            sold, bought = [], []
            for s_, why in list(st["sell"].items()):
                h_ = held.pop(s_, None)
                if h_ is None:
                    continue
                j = jx.get(s_)
                px = o[t, j] if (j is not None and o[t, j] == o[t, j] and o[t, j] > 0) else h_["px"]
                close(s_, h_, px, t, d, why)
                sold.append({"sym": s_, "px": float(px), "ret": trades[-1]["ret"], "why": why})
            equity = st["cash"] + sum(h_["sh"] * h_["px"] for h_ in held.values())
            for s_ in st["buy"]:
                j = jx.get(s_)
                if len(held) >= LEAD_N or j is None or s_ in held or not (o[t, j] == o[t, j] and o[t, j] > 0):
                    continue
                alloc = min(st["cash"], equity / LEAD_N)
                if alloc < equity * 0.005:
                    continue
                px = float(o[t, j])
                held[s_] = {"sh": alloc / (px * (1 + COST)), "p0": px, "px": px, "since": d}
                st["cash"] -= alloc
                bought.append({"sym": s_, "px": px})
            st["buy"], st["sell"] = [], {}
            if sold or bought:
                st["last"] = {"date": d, "bought": bought, "sold": sold}
        for s_, h_ in list(held.items()):                            # mark to the close
            j = jx.get(s_)
            if j is None or c[t, j] != c[t, j]:
                held.pop(s_)
                close(s_, h_, h_["px"], t, d, "No longer trading")
                st["notes"] = (st.get("notes") or [])[-5:] + [{"date": d, "sym": s_, "px": float(h_["px"]), "ret": trades[-1]["ret"],
                                                               "text": "left the price files (renamed, merged or delisted) and was taken out of the book at its last price. Check it by hand."}]
            else:
                h_["px"] = float(c[t, j])
        eq[t - t0] = st["cash"] + sum(h_["sh"] * h_["px"] for h_ in held.values())
        if chk[t]:
            r = rk[t]
            for s_ in held:
                v = kr[t, jx[s_]]
                if not (v == v):
                    st["sell"][s_] = "No longer among the 500 most traded" if X["eq"][t, jx[s_]] else "Moved to the trade-to-trade segment"
                elif v < LEAD_KEEP:
                    st["sell"][s_] = "Fell out of the top 20% by strength"
            free = LEAD_N - (len(held) - len(st["sell"]))
            if free > 0:
                for j in np.argsort(-np.nan_to_num(r, nan=-1.0), kind="stable"):
                    if free <= 0 or not (r[j] == r[j]) or r[j] < LEAD_BUY:
                        break
                    if sym[j] in held:
                        continue
                    st["buy"].append(sym[j])
                    free -= 1
        st["asof"] = d
    return st, eq, trades


# ───────────── the swing book ─────────────
def swing_run(X: dict, st: dict | None = None, t0: int = 0, t1: int | None = None):
    """Every leader-pullback trade, one per stock at a time. Buy at the next open; the stop is checked against the low;
    close-based exits sell at the following open."""
    o, l, c, atr, ma5, entry, rk, sym, jx, iso, ix = (X[k] for k in ("o", "l", "c", "atr", "ma5", "entry", "rk", "sym", "jx", "iso", "ix"))
    t1 = X["n"] if t1 is None else t1
    st = st or {"open": {}, "buy": [], "sell": {}, "done": []}
    op, trades = st["open"], []

    def close(s_, tr, px, t, d, why):
        t_in = ix.get(tr["since"])
        row = {"sym": s_, "in": tr["since"], "out": d, "p0": tr["p0"], "p1": float(px), "ret": float(px / tr["p0"] - 1 - 2 * COST), "bars": (t - t_in) if t_in is not None else None,
               "why": why, "t_in": t_in, "t_out": t}
        trades.append(row)
        st["done"].append({k: row[k] for k in ("sym", "in", "out", "p0", "p1", "ret", "bars", "why")})

    for t in range(t0, t1):
        d = iso[t]
        st["done"] = []
        for s_, why in list(st["sell"].items()):
            tr = op.pop(s_, None)
            if tr is None:
                continue
            j = jx.get(s_)
            px = o[t, j] if (j is not None and o[t, j] == o[t, j] and o[t, j] > 0) else tr["px"]
            close(s_, tr, px, t, d, why)
        st["sell"] = {}
        for s_ in st["buy"]:
            j = jx.get(s_)
            if j is None or s_ in op or t == 0:
                continue
            px, v = o[t, j], atr[t - 1, j]
            if px == px and px > 0 and v == v and v > 0:
                op[s_] = {"p0": float(px), "px": float(px), "stop": float(px * (1 - SW_STOP * v)), "since": d}
        st["buy"] = []
        for s_, tr in list(op.items()):
            j = jx.get(s_)
            if j is None or c[t, j] != c[t, j]:
                op.pop(s_)
                close(s_, tr, tr["px"], t, d, "No longer trading")
                continue
            e = ix.get(tr["since"], t)
            if l[t, j] <= tr["stop"]:
                px = tr["stop"] if (t == e or not (o[t, j] == o[t, j]) or o[t, j] >= tr["stop"]) else o[t, j]
                op.pop(s_)
                close(s_, tr, px, t, d, "Stop hit")
                continue
            tr["px"] = float(c[t, j])
            if ma5[t, j] == ma5[t, j] and c[t, j] > ma5[t, j]:
                st["sell"][s_] = "Closed above the 5-day average"
            elif t - e + 1 >= SW_DAYS:
                st["sell"][s_] = "Time limit"
        r = rk[t]
        new = [sym[j] for j in np.nonzero(entry[t])[0] if sym[j] not in op]
        st["buy"] = sorted(new, key=lambda s_: -(r[jx[s_]] if r[jx[s_]] == r[jx[s_]] else 0))
        st["asof"] = d
    return st, trades


# ───────────── the index setup ─────────────
def index_frame(d: pd.DataFrame) -> dict:
    d = d[d.index >= "2012-01-01"].dropna(subset=["Close"])
    C = d["Close"]
    ma5, ma200 = C.rolling(5).mean(), C.rolling(200).mean()
    r5 = C / C.shift(5) - 1
    hi20 = C >= C.rolling(20).max()
    return {"iso": [x.date().isoformat() for x in d.index], "o": d["Open"].to_numpy(float), "c": C.to_numpy(float), "ma5": ma5.to_numpy(float), "ma200": ma200.to_numpy(float),
            "r5": r5.to_numpy(float), "sig": ((C > ma200) & (r5 <= -IDX_FALL)).to_numpy(), "xit": (C > ma5).to_numpy(),
            "hi": ((C > ma200) & hi20 & ~hi20.shift(1, fill_value=False)).to_numpy(), "n": len(d)}


def index_run(F: dict, st: dict | None = None, t0: int = 200, t1: int | None = None, sig: str = "sig", use_exit: bool = True, hold: int = IDX_DAYS):
    o, c, iso = F["o"], F["c"], F["iso"]
    ix = {d: t for t, d in enumerate(iso)}
    t1 = F["n"] if t1 is None else t1
    st = st or {"pos": None, "pend": None, "done": None}
    trades = []
    for t in range(t0, t1):
        d = iso[t]
        st["done"] = None
        px = o[t] if (o[t] == o[t] and o[t] > 0) else c[t - 1]
        if st["pend"] == "buy":
            st["pos"] = {"since": d, "entry": float(px)}
        elif st["pend"] and st["pos"]:
            p = st["pos"]
            e = ix.get(p["since"], t)
            row = {"in": p["since"], "out": d, "p0": p["entry"], "p1": float(px), "ret": float(px / p["entry"] - 1 - 2 * IDX_COST), "bars": t - e, "why": st["pend"]}
            trades.append(row)
            st["done"], st["pos"] = row, None
        st["pend"] = None
        if st["pos"]:
            e = ix.get(st["pos"]["since"], t)
            if use_exit and F["xit"][t]:
                st["pend"] = "Closed above the 5-day average"
            elif t - e + 1 >= hold:
                st["pend"] = "Time limit"
        elif F[sig][t]:
            st["pend"] = "buy"
        st["asof"] = d
    return st, trades


# ───────────── records ─────────────
def tstats(rets, bars=None) -> dict:
    r = np.asarray([x for x in rets if x is not None], dtype=float)
    if len(r) < 5:
        return {"n": int(len(r))}
    w, lo = r[r > 0], r[r <= 0]
    run = worst = 0
    for v in r:
        run = run + 1 if v <= 0 else 0
        worst = max(worst, run)
    out = {"n": int(len(r)), "win": float(len(w) / len(r)), "avg_win": float(w.mean()) if len(w) else 0.0, "avg_loss": float(lo.mean()) if len(lo) else 0.0,
           "exp": float(r.mean()), "pf": float(min(w.sum() / -lo.sum(), 99.0)) if len(lo) and lo.sum() < 0 else 99.0,
           "t": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if r.std() > 0 else 0.0, "best": float(r.max()), "worst": float(r.min()), "losing_run": int(worst)}
    if bars is not None:
        b = [x for x in bars if x is not None]
        out["bars"] = float(np.mean(b)) if b else None
    return out


def _tmean(x) -> dict:
    x = np.asarray(x, dtype=float)
    if len(x) < 5 or x.std() == 0:
        return {"edge": None, "t": None}
    return {"edge": float(x.mean()), "t": float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))}


def windows(n: int, iso: list, t_start: int) -> list:
    cut = int(np.searchsorted(np.array(iso), SPLIT))
    y3 = (date.fromisoformat(iso[-1]) - timedelta(days=3 * 365)).isoformat()
    t3 = int(np.searchsorted(np.array(iso), y3))
    return [("all", f"Since {iso[t_start][:4]}", t_start, n), ("early", f"{iso[t_start][:4]} to {int(SPLIT[:4]) - 1}", t_start, cut), ("late", f"{SPLIT[:4]} to now", cut, n),
            ("last3", "Last 3 years", t3, n)]


def trade_record(trades: list, W: list, bi=None, base=None) -> dict:
    """Per-trade record for each window. `bi`: value of the average liquid stock (for the stock-selection edge). `base`: average
    one-session move of the index (for the edge over simply holding)."""
    out = {}
    for key, label, a, b in W:
        sub = [t for t in trades if t.get("t_in") is not None and a <= t["t_in"] < b]
        s = tstats([t["ret"] for t in sub], [t["bars"] for t in sub])
        s["label"] = label
        if bi is not None and sub:
            s["pick"] = _tmean([t["ret"] - (bi[t["t_out"]] / bi[max(t["t_in"] - 1, 0)] - 1 - 2 * COST) for t in sub])
        if base is not None and sub:
            s["over_hold"] = _tmean([t["ret"] - base * t["bars"] for t in sub])
        out[key] = s
    yr = {}
    for t in trades:
        yr.setdefault(int(t["in"][:4]), []).append(t["ret"])
    out["years"] = [{"y": y, "n": len(v), "exp": float(np.mean(v)), "win": float((np.array(v) > 0).mean())} for y, v in sorted(yr.items())]
    return out


def gate(rec: dict, min_n: int = 30, pf: float = 1.2, edge_key: str | None = None) -> str:
    """on: worked before and after the split and in the last three years. trial: positive overall, one window is weak. off: otherwise."""
    a, e, la, l3 = (rec.get(k, {}) for k in ("all", "early", "late", "last3"))
    ok = lambda x, p, n: x.get("n", 0) >= n and x.get("exp", -1) > 0 and x.get("pf", 0) >= p
    edge_ok = True
    if edge_key:
        edge_ok = ((a.get(edge_key) or {}).get("t") or 0) >= 2.0
    if ok(a, pf, min_n) and ok(e, pf, min_n // 3) and ok(la, pf, min_n // 3) and l3.get("exp", -1) > 0 and a.get("t", 0) >= 2.5 and edge_ok:
        return "on"
    if ok(a, 1.1, min_n) and la.get("exp", -1) > 0:
        return "trial"
    return "off"


def cstats(v) -> dict:
    v = np.asarray(v, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 60:
        return {}
    d = v[1:] / v[:-1] - 1
    return {"cagr": float((v[-1] / v[0]) ** (252 / (len(v) - 1)) - 1), "dd": float((v / np.maximum.accumulate(v) - 1).min()),
            "sharpe": float(d.mean() / d.std() * math.sqrt(252)) if d.std() > 0 else 0.0, "total": float(v[-1] / v[0] - 1)}


def thin(iso, *series, n=240):
    idx = np.unique(np.linspace(0, len(iso) - 1, min(n, len(iso))).astype(int))
    return [iso[i] for i in idx], [[round(float(s[i]), 4) for i in idx] for s in series]


def year_rows(iso: list, **series) -> list:
    """Calendar-year returns of each value series (aligned with iso)."""
    yrs, rows = sorted({d[:4] for d in iso}), []
    arr = np.array([d[:4] for d in iso])
    for y in yrs:
        w = np.nonzero(arr == y)[0]
        a, b = max(w[0] - 1, 0), w[-1]
        row = {"y": int(y), "part": bool(w[0] == 0 or y == yrs[-1])}
        for k, v in series.items():
            row[k] = _f(v[b] / v[a] - 1, 4) if v[a] == v[a] and v[a] > 0 else None
        rows.append(row)
    return rows


def leaders_record(X: dict, I: dict) -> dict:
    """The full replay of the leaders book, with the same rule checked on each of the five weekdays to show how much luck is in the number."""
    rk = X["rk"]
    t_start = int(np.argmax((~np.isnan(rk)).sum(axis=1) >= 200))
    while not check_days(X, 0)[t_start]:
        t_start += 1
    iso = X["iso"][t_start:]
    W = windows(len(iso), iso, 0)
    n5 = I["n500"].to_numpy(float)[t_start:]
    avg = X["bi"][t_start:]
    runs = []
    for k in range(5):
        st, eq, tr = leaders_run(X, check_days(X, k), None, t_start)
        runs.append((st, eq, tr))
    st, eq, trades = runs[0]
    rec = {"start": iso[0], "windows": [], "years": year_rows(iso, book=eq, index=n5, avg=avg)}
    for key, label, a, b in W:
        row = {"key": key, "label": label, "book": cstats(eq[a:b]), "index": cstats(n5[a:b]), "avg": cstats(avg[a:b])}
        alt = [cstats(r[1][a:b]).get("cagr") for r in runs]
        row["range"] = [min(alt), max(alt)] if all(v is not None for v in alt) else None
        rec["windows"].append(row)
    alt_years = [year_rows(iso, book=r[1]) for r in runs]
    for i, y in enumerate(rec["years"]):
        vals = [a[i]["book"] for a in alt_years if a[i]["book"] is not None]
        y["lo"], y["hi"] = (min(vals), max(vals)) if vals else (None, None)
    closed = trades
    s = tstats([t["ret"] for t in closed], [t["bars"] for t in closed])
    r = np.array([t["ret"] for t in closed])
    s["big"] = int((r >= 0.5).sum())
    s["per_year"] = float(len(closed) / (len(iso) / 252))
    rec["trades"] = s
    rec["winners"] = [{k: t[k] for k in ("sym", "in", "out", "ret", "bars")} for t in sorted(closed, key=lambda t: -t["ret"])[:12]]
    rec["losers"] = [{k: t[k] for k in ("sym", "in", "out", "ret", "bars")} for t in sorted(closed, key=lambda t: t["ret"])[:6]]
    rec["recent"] = [{k: t[k] for k in ("sym", "in", "out", "ret", "bars", "why")} for t in sorted(closed, key=lambda t: t["out"])[-12:]][::-1]
    dts, (b_, i_, a_) = thin(iso, eq / eq[0] - 1, n5 / n5[0] - 1, avg / avg[0] - 1)
    rec["curve"] = {"date": dts, "book": b_, "index": i_, "avg": a_}
    peak = np.maximum.accumulate(eq)
    rec["dd_now"] = float(eq[-1] / peak[-1] - 1)
    dd = eq / peak - 1
    dts2, (d_,) = thin(iso, dd)
    rec["dd_curve"] = {"date": dts2, "dd": d_}
    by = {w["key"]: w for w in rec["windows"]}
    beat = lambda k, ref: (by[k]["book"].get("cagr") or -9) > (by[k][ref].get("cagr") or 9)
    wins = sum(beat(k, "avg") and beat(k, "index") for k in ("early", "late", "last3"))
    rec["status"] = "on" if wins == 3 else "trial" if wins == 2 else "off"
    return {"record": rec, "state": st, "t_start": t_start}


def slot_curve(X: dict, trades: list, slots: int = 10) -> np.ndarray:
    """The trades followed with a money limit: a tenth of capital each, at most `slots` at once, strongest first. Daily returns."""
    c, rk, jx, n = X["c"], X["rk"], X["jx"], X["n"]
    by = {}
    for tr in trades:
        if tr.get("t_in") is not None and tr["sym"] in jx:
            by.setdefault(tr["t_in"], []).append(tr)
    open_, daily = [], np.zeros(n)
    for t in range(n):
        for tr in list(open_):
            j = jx[tr["sym"]]
            if t == tr["t_out"]:
                prev = c[t - 1, j] if t > tr["t_in"] else tr["p0"]
                daily[t] += (tr["p1"] / prev - 1 - COST) / slots
                open_.remove(tr)
            elif t > tr["t_in"] and c[t, j] == c[t, j] and c[t - 1, j] == c[t - 1, j]:
                daily[t] += (c[t, j] / c[t - 1, j] - 1) / slots
        key = lambda x: -(rk[max(x["t_in"] - 1, 0), jx[x["sym"]]] if rk[max(x["t_in"] - 1, 0), jx[x["sym"]]] == rk[max(x["t_in"] - 1, 0), jx[x["sym"]]] else 0)
        for tr in sorted(by.get(t, []), key=key):
            if len(open_) < slots and not any(q["sym"] == tr["sym"] for q in open_):
                j = jx[tr["sym"]]
                if tr["t_out"] > t:
                    open_.append(tr)
                    daily[t] += (c[t, j] / tr["p0"] - 1 - COST) / slots
                else:
                    daily[t] += (tr["p1"] / tr["p0"] - 1 - 2 * COST) / slots
    return daily


# ───────────── ideas that were tested and rejected (kept here so the numbers stay current) ─────────────
def sim(P: dict, I: dict, entry: pd.DataFrame, k_init=2.5, k_trail=3.0, max_hold=None, below_ma=None) -> list:
    o, l, c = (P[k].to_numpy(dtype=float) for k in ("O", "L", "C"))
    en, at = entry.to_numpy(), I["atr"].to_numpy()
    bma = I[f"ma{below_ma}"].to_numpy() if below_ma else None
    n, m = c.shape
    trades = []
    for j in range(m):
        nxt = 0
        for s in np.nonzero(en[:n - 1, j])[0]:
            t = s + 1
            if t < nxt:
                continue
            p0, v = o[t, j], at[s, j]
            if not (p0 == p0 and v == v and v > 0 and p0 > 0):
                continue
            stop, hi, out, pend, e = p0 * (1 - k_init * v), p0, None, False, t
            while t < n:
                if c[t, j] != c[t, j]:
                    out, t = (c[t - 1, j] if c[t - 1, j] == c[t - 1, j] else p0), t - 1
                    break
                if pend:
                    out = o[t, j] if o[t, j] == o[t, j] else c[t - 1, j]
                    break
                if l[t, j] <= stop:
                    out = stop if (t == e or not (o[t, j] == o[t, j]) or o[t, j] >= stop) else o[t, j]
                    break
                hi = max(hi, c[t, j])
                if k_trail:
                    stop = max(stop, hi * (1 - k_trail * v))
                if (bma is not None and bma[t, j] == bma[t, j] and c[t, j] < bma[t, j]) or (max_hold and t - e + 1 >= max_hold):
                    pend = True
                t += 1
            if out is None:
                break                                                 # still open at the end: not part of the record
            trades.append({"t_in": int(e), "t_out": int(t), "ret": float(out / p0 - 1 - 2 * COST), "bars": int(t - e), "in": ""})
            nxt = t + 1
    return trades


def rejected(P: dict, I: dict, X: dict, W: list, t_start: int) -> list:
    C, el = P["C"], I["elig"]
    above = C > I["hi252"]
    acc = I["acc10"] >= 2
    ideas = [("52-week-high breakout", "First close above the 252-session high, in an uptrend. Trail 3.5 ATR, out below the 50-day average.",
              above & ~above.shift(1, fill_value=False) & I["up"], dict(k_init=2.5, k_trail=3.5, below_ma=50)),
             ("Delivery accumulation near highs", "Ten-day delivered quantity at twice normal, within 10% of the 52-week high. Same exits.",
              acc & ~acc.shift(1, fill_value=False) & I["up"] & (C >= 0.9 * I["hi252"]) & (I["r10"] > 0), dict(k_init=2.5, k_trail=3.5, below_ma=50)),
             ("Heavy-volume surge day", "Up 5% or more on three times normal volume, closing in the top third of the day. Hold 40 sessions, stop 3 ATR.",
              (I["r1"] >= 0.05) & (I["volx"] >= 3) & (I["rng"] >= 0.67) & (C > I["ma200"]), dict(k_init=3.0, k_trail=0.0, max_hold=40))]
    out = []
    for name, rule, e, kw in ideas:
        tr = [t for t in sim(P, I, (e & el).fillna(False), **kw) if t["t_in"] >= t_start]
        for t in tr:
            t["in"] = X["iso"][t["t_in"]]
        rec = trade_record(tr, [(k, lab, a + t_start, b + t_start) for k, lab, a, b in W], bi=X["bi"])
        a = rec["all"]
        pick = a.get("pick") or {}
        out.append({"name": name, "rule": rule, "n": a.get("n"), "win": a.get("win"), "exp": a.get("exp"), "bars": a.get("bars"), "pick": pick.get("edge"), "pick_t": pick.get("t"),
                    "late_pick": (rec["late"].get("pick") or {}).get("edge"), "verdict": "Rejected" if (pick.get("t") or 0) < 2.5 else "Worth a look"})
    return out


def deciles(P: dict, I: dict, t_start: int, step: int = 21) -> dict:
    """Average move over the next 21 sessions (open to open) for ten strength groups, weakest to strongest."""
    o, rk, n = P["O"].to_numpy(float), I["rs12"].to_numpy(float), len(P["C"])
    y3 = int(np.searchsorted(P["C"].index.values, np.datetime64(P["C"].index[-1] - pd.Timedelta(days=3 * 365))))
    out = {}
    for key, a in (("all", t_start), ("last3", y3)):
        acc, cnt = np.zeros(10), 0
        for t in range(a, n - step - 1, step):
            r = rk[t]
            ok = ~np.isnan(r)
            f = o[t + 1 + step] / o[t + 1] - 1
            ok &= ~np.isnan(f)
            if ok.sum() < 100:
                continue
            g = np.minimum((r[ok] * 10).astype(int), 9)
            m = np.array([f[ok][g == q].mean() if (g == q).any() else np.nan for q in range(10)])
            if not np.isnan(m).any():
                acc += m
                cnt += 1
        out[key] = [round(float(v), 5) for v in acc / max(cnt, 1)]
        out[key + "_n"] = cnt
    return out


# ───────────── F&O context and big-player footprints ─────────────
def fno_frames():
    files = sorted(glob.glob(str(FNO / "u" / "*.csv")))[-2:]
    if not files:
        return None
    try:
        return pd.concat(pd.read_csv(f) for f in files).sort_values(["sym", "date"])
    except Exception:
        return None


def fno_context(U) -> tuple:
    """(per-stock futures context, index rows, build-up lists) from the F&O store."""
    if U is None or U.empty:
        return {}, {}, {}
    try:
        d = U[U["kind"] == "STK"].copy()
        g = d.groupby("sym")
        d["p5"], d["oi5"] = d["fut"] / g["fut"].shift(5) - 1, d["fut_oi"] / g["fut_oi"].shift(5) - 1
        last = d[d["date"] == d["date"].max()]
        stocks, lists = {}, {"Long build-up": [], "Short build-up": [], "Short covering": [], "Long unwinding": []}
        for _, r in last.iterrows():
            p5, oi5, bu = r["p5"], r["oi5"], ""
            if p5 == p5 and oi5 == oi5 and abs(p5) >= 0.02 and abs(oi5) >= 0.05:
                bu = ("Long build-up" if p5 > 0 else "Short build-up") if oi5 > 0 else ("Short covering" if p5 > 0 else "Long unwinding")
                lists[bu].append({"sym": str(r["sym"]), "p5": _f(p5, 4), "oi5": _f(oi5, 4), "fut": _f(r["fut"], 2)})
            stocks[str(r["sym"]).upper()] = {"fut": _f(r["fut"], 2), "lot": _f(r["lot"], 0), "oi5": _f(oi5, 4), "p5": _f(p5, 4), "buildup": bu,
                                            "pcr": _f(r["pe_oi"] / r["ce_oi"], 2) if r["ce_oi"] else None, "iv": _f(r["iv"], 4)}
        for k in lists:
            lists[k] = sorted(lists[k], key=lambda x: -abs(x["oi5"] or 0))[:8]
        idx = {}
        for _, r in U[(U["kind"] == "IDX") & (U["date"] == U["date"].max()) & U["sym"].isin(list(INDEXES))].iterrows():
            idx[str(r["sym"])] = {k: _f(r.get(k)) for k in ("spot", "fut", "lot", "dte", "max_pain", "call_wall", "put_wall", "iv", "straddle")}
            idx[str(r["sym"])].update({"exp": str(r.get("m_exp")), "pcr": _f(r["pe_oi"] / r["ce_oi"], 2) if r["ce_oi"] else None, "date": str(r["date"])})
        return stocks, idx, {"date": str(d["date"].max()), "lists": lists}
    except Exception:
        return {}, {}, {}


def option_plan(sym: str, spot: float, asof: str) -> dict | None:
    """A call spread for the index dip trade from the stored chain: buy the at-the-money call, sell the call about 2% higher.
    Priced only when the stored chain is from the same days as the prices and its expiry leaves at least 7 days; otherwise the strikes alone."""
    try:
        ch = pd.read_csv(FNO / "chain_latest.csv")
        ch = ch[ch["sym"] == sym]
        if ch.empty or not spot:
            return None
        try:
            cdate = str(json.loads((FNO / "meta.json").read_text()).get("chain_date") or "")
            fresh = abs((date.fromisoformat(cdate) - date.fromisoformat(asof)).days) <= 4
        except Exception:
            fresh = False
        exps = sorted(ch["exp"].unique())
        days = lambda e: (date.fromisoformat(str(e)) - date.fromisoformat(asof)).days
        good = [e for e in exps if days(e) >= 7]
        exp = good[0] if good else exps[-1]
        c = ch[ch["exp"] == exp].sort_values("strike")
        ks = c["strike"].to_numpy(float)
        k1 = float(ks[np.argmin(np.abs(ks - spot))])
        up = ks[ks > k1]
        if abs(k1 / spot - 1) > 0.015 or len(up) == 0:
            return None                                   # the stored strikes do not cover today's price
        k2 = float(up[np.argmin(np.abs(up - spot * 1.02))])
        out = {"exp": str(exp), "days": int(days(exp)), "buy": k1, "sell": k2, "priced": False}
        if fresh and good:
            p1, p2 = float(c.loc[c["strike"] == k1, "ce_px"].iloc[0]), float(c.loc[c["strike"] == k2, "ce_px"].iloc[0])
            if p1 > p2 > 0 and (k2 - k1) > (p1 - p2):
                out.update({"priced": True, "cost": round(p1 - p2, 2), "max_gain": round((k2 - k1) - (p1 - p2), 2), "breakeven": round(k1 + p1 - p2, 2)})
        return out
    except Exception:
        return None


def participants() -> dict | None:
    p = FNO / "participants.csv"
    if not p.exists():
        return None
    try:
        raw = pd.read_csv(p)
        days = sorted(raw["date"].unique())
        if len(days) < 6:
            return None
        rows, fii_hist = [], None
        for who, name in (("FII", "FIIs"), ("Pro", "Prop desks"), ("Client", "Retail clients"), ("DII", "DIIs")):
            a = raw[raw["who"].astype(str).str.upper() == who.upper()].set_index("date").sort_index()
            if a.empty or days[-1] not in a.index:
                continue
            share = a["fi_long"] / (a["fi_long"] + a["fi_short"]).clip(lower=1)
            net = a["fi_long"] - a["fi_short"]
            stk = a["fs_long"] - a["fs_short"]
            opt = (a["oi_call_long"] - a["oi_call_short"]) - (a["oi_put_long"] - a["oi_put_short"])
            y = share.iloc[-250:]
            rows.append({"who": name, "long_share": _f(share.iloc[-1], 4), "share_pct": _f((y <= share.iloc[-1]).mean(), 3), "net": _f(net.iloc[-1], 0), "d1": _f(net.diff().iloc[-1], 0),
                         "d5": _f(net.diff(5).iloc[-1], 0), "stk": _f(stk.iloc[-1], 0), "stk_d5": _f(stk.diff(5).iloc[-1], 0), "opt": _f(opt.iloc[-1], 0), "opt_d1": _f(opt.diff().iloc[-1], 0)})
            if who == "FII":
                h = share.iloc[-120:]
                fii_hist = {"date": [str(x) for x in h.index], "share": [round(float(v), 4) for v in h.to_numpy()]}
        return {"date": str(days[-1]), "rows": rows, "fii": fii_hist}
    except Exception:
        return None


def footprints(P: dict, I: dict) -> dict:
    """Where unusually large or unusually sticky trading showed up today. Context: none of these beat the average stock when tested as a signal."""
    el, C = I["elig"].iloc[-1], P["C"].iloc[-1]

    def table(score, minimum, k=10):
        s = score.iloc[-1].where(el).dropna()
        s = s[s >= minimum].sort_values(ascending=False).head(k)
        return [{"sym": sym, "x": _f(v, 2), "price": _f(C[sym], 2), "d1": _f(I["r1"].iloc[-1][sym], 4), "d10": _f(I["r10"].iloc[-1][sym], 4),
                 "dshare": _f(I["dshare"].iloc[-1][sym], 3), "rank": _f(I["rs12"].iloc[-1][sym], 3)} for sym, v in s.items()]
    acc = table(I["acc10"], 1.5, 16)
    return {"accumulation": [r for r in acc if (r["d10"] or 0) > 0][:8], "distribution": [r for r in acc if (r["d10"] or 0) < 0][:8],
            "delivery_spike": table(I["dqx"], 2.5, 8), "big_tickets": table(I["atx"], 1.6, 8), "volume": table(I["volx"], 3.0, 8)}


def fetch_deals(fetch) -> list:
    """Today's bulk and block deals from NSE (who bought or sold a large stake). Best effort: an empty list if NSE refuses."""
    out = []
    for kind, url in (("Bulk", "https://nsearchives.nseindia.com/content/equities/bulk.csv"), ("Block", "https://nsearchives.nseindia.com/content/equities/block.csv")):
        try:
            txt = fetch(url)
            if not txt:
                continue
            df = pd.read_csv(io.StringIO(txt), dtype=str, skipinitialspace=True)
            df.columns = [c.strip().lower() for c in df.columns]
            col = lambda *w: next((c for c in df.columns if all(x in c for x in w)), None)
            cs, cn, cb, cq, cp, cd = col("symbol"), col("client"), col("buy"), col("quantity"), col("price"), col("date")
            if not (cs and cn and cb and cq):
                continue
            for _, r in df.iterrows():
                q = pd.to_numeric(str(r[cq]).replace(",", ""), errors="coerce")
                p = pd.to_numeric(str(r[cp]).replace(",", ""), errors="coerce") if cp else np.nan
                if q == q and str(r[cs]).strip() and "NO RECORDS" not in str(r[cs]).upper():
                    out.append({"kind": kind, "date": str(r[cd]).strip() if cd else "", "sym": str(r[cs]).strip().upper(), "who": str(r[cn]).strip()[:60],
                                "side": str(r[cb]).strip().upper()[:4], "qty": float(q), "price": _f(p, 2), "value_cr": _f(q * p / 1e7, 2) if p == p else None})
        except Exception:
            continue
    return out


# ───────────── keeping saved books in step with the price files ─────────────
def heal(saved: dict | None, X: dict, isin: dict, say=print) -> dict | None:
    """The saved books carry prices and share counts. Two things can move under them between runs:
      a symbol is renamed            -> the old name is gone from the files; the same ISIN now trades under a new name
      a split or bonus is adjusted   -> the whole history of that stock is re-based by a factor
    Both are detected (the stored last close no longer matches the files on that day) and the book is carried over, with no trade."""
    if not saved:
        return saved
    st = json.loads(json.dumps(saved))
    jx, ix, c = X["jx"], X["ix"], X["c"]
    by_isin = {}
    for s_ in X["sym"]:
        if isin.get(s_):
            by_isin.setdefault(isin[s_], s_)
    for book, key in (("leaders", "held"), ("swing", "open")):
        B = st.get(book) or {}
        pos = B.get(key) or {}
        t = ix.get(B.get("asof"))
        for s_ in list(pos):
            if s_ not in jx:
                new = by_isin.get(isin.get(s_, ""))
                if new and new not in pos:
                    pos[new] = pos.pop(s_)
                    for lst in ("buy",):
                        B[lst] = [new if x == s_ else x for x in B.get(lst) or []]
                    if s_ in (B.get("sell") or {}):
                        B["sell"][new] = B["sell"].pop(s_)
                    say(f"  {book}: {s_} now trades as {new}; the position was carried over")
                    s_ = new
                else:
                    continue
            if t is None:
                continue
            now, old = c[t, jx[s_]], pos[s_].get("px")
            if now == now and old and old > 0 and abs(now / old - 1) > 1e-3:
                k = float(now / old)
                h_ = pos[s_]
                h_["p0"], h_["px"] = h_["p0"] * k, float(now)
                if "sh" in h_:
                    h_["sh"] = h_["sh"] / k
                if "stop" in h_:
                    h_["stop"] = h_["stop"] * k
                say(f"  {book}: {s_} history was re-based by {k:.4f} (split or bonus); the position was carried over")
    return st


# ───────────── the run ─────────────
def run(P: dict, deals: list | None = None, state: dict | None = None, say=print) -> dict:
    I = indicators(P)
    X = bundle(P, I)
    state = heal(state, X, P.get("ISIN") or {}, say)
    C, iso, n = P["C"], X["iso"], X["n"]
    last, cols, jx = n - 1, X["sym"], X["jx"]
    U = fno_frames()
    fno, fidx, buildups = fno_context(U)
    px = lambda v: _f(v, 2)

    def info(s):
        j = jx[s]
        return {"sym": s, "price": px(C.iat[last, j]), "d1": _f(I["r1"].iat[last, j], 4), "rank": _f(I["rs12"].iat[last, j], 3), "atr": _f(I["atr"].iat[last, j], 4),
                "acc": _f(I["acc10"].iat[last, j], 2), "dshare": _f(I["dshare"].iat[last, j], 3), "fno": fno.get(s)}

    # 1. leaders: the full replay for the record, then the live book moved forward from the saved state
    L = leaders_record(X, I)
    rec, t_start = L["record"], L["t_start"]
    say(f"  leaders   {rec['status']:<5} " + " · ".join(f"{w['label']}: book {w['book'].get('cagr', 0) * 100:+.1f}% a year, index {w['index'].get('cagr', 0) * 100:+.1f}%, "
                                                        f"average stock {w['avg'].get('cagr', 0) * 100:+.1f}%" for w in rec["windows"]))
    W = windows(n - t_start, iso[t_start:], 0)
    Wabs = [(k, lab, a + t_start, b + t_start) for k, lab, a, b in W]
    saved = (state or {}).get("leaders")
    drift = False
    if saved and saved.get("asof") in X["ix"]:
        live, _, _ = leaders_run(X, check_days(X, 0), json.loads(json.dumps(saved)), X["ix"][saved["asof"]] + 1)
        drift = set(live["held"]) != set(L["state"]["held"])
    else:
        live = L["state"]
    r_now, k_now = X["rk"][last], X["kr"][last]
    held_rows = []
    book_value = live["cash"] + sum(h_["sh"] * h_["px"] for h_ in live["held"].values())
    for s, h_ in live["held"].items():
        if s not in jx:
            continue
        v = k_now[jx[s]]
        t_in = X["ix"].get(h_["since"])
        flag = "sell" if s in live["sell"] else ("risk" if (not (v == v) or v < LEAD_KEEP) else "")
        held_rows.append({**info(s), "rank": _f(v, 3), "since": h_["since"], "entry": px(h_["p0"]), "pnl": _f(h_["px"] / h_["p0"] - 1, 4), "bars": (last - t_in) if t_in is not None else None,
                          "w": _f(h_["sh"] * h_["px"] / max(book_value, 1e-9), 4), "flag": flag, "why": live["sell"].get(s, "")})
    held_rows.sort(key=lambda r: -(r["pnl"] or 0))
    order = [cols[j] for j in np.argsort(-np.nan_to_num(r_now, nan=-1.0), kind="stable") if r_now[j] == r_now[j] and r_now[j] >= LEAD_BUY]
    leaders = {**RULES["leaders"], "status": rec["status"], "check_today": bool(check_days(X, 0)[last]), "slots": LEAD_N,
               "buy": [info(s) for s in live["buy"] if s in jx], "sell": [r for r in held_rows if r["flag"] == "sell"], "holdings": held_rows,
               "candidates": [info(s) for s in order if s not in live["held"] and s not in live["buy"]][:10],
               "last_change": live.get("last"), "notes": live.get("notes") or [], "cash": _f(live["cash"] / max(book_value, 1e-9), 3),
               "record": rec, "drift": drift}

    # 2. swing
    sw_full, s_trades = swing_run(X, None, t_start)
    s_rec = trade_record(s_trades, Wabs, bi=X["bi"])
    s_status = gate(s_rec, 30, 1.2)
    say(f"  swing     {s_status:<5} trades {s_rec['all'].get('n')} · won {s_rec['all'].get('win', 0) * 100:.0f}% · per trade {s_rec['all'].get('exp', 0) * 100:+.2f}% · "
        f"before {SPLIT[:4]} {s_rec['early'].get('exp', 0) * 100:+.2f}% / after {s_rec['late'].get('exp', 0) * 100:+.2f}% / last 3 years {s_rec['last3'].get('exp', 0) * 100:+.2f}%")
    saved = (state or {}).get("swing")
    if saved and saved.get("asof") in X["ix"]:
        sw, _ = swing_run(X, json.loads(json.dumps(saved)), X["ix"][saved["asof"]] + 1)
    else:
        sw = sw_full
    daily = slot_curve(X, s_trades)[t_start:]
    dts, (b_,) = thin(iso[t_start:], np.cumprod(1 + daily) - 1)
    s_open = []
    for s, tr in sw["open"].items():
        if s not in jx:
            continue
        t_in = X["ix"].get(tr["since"], last)
        s_open.append({**info(s), "since": tr["since"], "entry": px(tr["p0"]), "pnl": _f(tr["px"] / tr["p0"] - 1, 4), "bars": last - t_in, "stop": px(tr["stop"]),
                       "left": max(SW_DAYS - (last - t_in + 1), 0), "sell": sw["sell"].get(s, "")})
    s_new = []
    for s in sw["buy"]:
        j = jx[s]
        a = I["atr"].iat[last, j]
        s_new.append({**info(s), "stop": px(C.iat[last, j] * (1 - SW_STOP * a)), "risk": _f(SW_STOP * a, 4), "r5": _f(I["r5"].iat[last, j], 4)})
    swing = {**RULES["swing"], "status": s_status, "new": s_new, "open": sorted(s_open, key=lambda r: -(r["pnl"] or 0)), "done": sw.get("done") or [],
             "record": s_rec, "recent": [{k: t[k] for k in ("sym", "in", "out", "p0", "p1", "ret", "bars", "why")} for t in s_trades[-14:]][::-1],
             "curve": {"date": dts, "book": b_}, "curve_stats": cstats(np.cumprod(1 + daily))}

    # 3. index
    index, i_state = [], {}
    for sym in INDEXES:
        d = P["IDX"].get(sym)
        if d is None or len(d) < 400:
            continue
        F = index_frame(d)
        ist_full, tr = index_run(F)
        ixm = {x: t for t, x in enumerate(F["iso"])}
        for t in tr:
            t["t_in"], t["t_out"] = ixm[t["in"]], ixm[t["out"]]
        Wi = windows(F["n"] - 200, F["iso"][200:], 0)
        base = float(np.nanmean(F["c"][201:] / F["c"][200:-1] - 1))
        i_rec = trade_record(tr, [(k, lab, a + 200, b + 200) for k, lab, a, b in Wi], base=base)
        status = gate(i_rec, 30, 1.3, "over_hold")
        saved = ((state or {}).get("index") or {}).get(sym)
        if saved and saved.get("asof") in ixm:
            ist, _ = index_run(F, json.loads(json.dumps(saved)), ixm[saved["asof"]] + 1)
        elif saved and str(saved.get("asof", "")) > F["iso"][-1]:
            ist = saved                                   # the saved book is ahead of today's data: leave it alone
        else:
            ist = ist_full
        i_state[sym] = ist
        t = F["n"] - 1
        pos = ist["pos"]
        action = "buy" if ist["pend"] == "buy" else "sell" if (ist["pend"] and pos) else "hold" if pos else "none"
        e = ixm.get(pos["since"], t) if pos else None
        _, hi_tr = index_run(F, None, 200, None, "hi", False, 10)
        hi_rec = _tmean([x["ret"] - base * x["bars"] for x in hi_tr])
        row = {**RULES["index"], "sym": sym, "name": INDEX_NAME[sym], "setup": RULES["index"]["name"], "status": status, "asof": F["iso"][-1], "action": action, "why": ist["pend"] if action == "sell" else "",
               "last": px(F["c"][t]), "ma200": px(F["ma200"][t]), "ma5": px(F["ma5"][t]), "r5": _f(F["r5"][t], 4), "above200": bool(F["c"][t] > F["ma200"][t]),
               "trigger": px(F["c"][t - 4] * (1 - IDX_FALL)) if t >= 4 else None, "record": i_rec, "base": base,
               "pos": ({"since": pos["since"], "entry": px(pos["entry"]), "pnl": _f(F["c"][t] / pos["entry"] - 1, 4), "bars": t - e, "left": max(IDX_DAYS - (t - e + 1), 0)} if pos else None),
               "done": ist.get("done"), "recent": [{k: x[k] for k in ("in", "out", "p0", "p1", "ret", "bars", "why")} for x in tr[-8:]][::-1],
               "fno": fidx.get(sym), "plan": option_plan(sym, float(F["c"][t]), F["iso"][-1]),
               "high20": {"n": len(hi_tr), "exp": _f(np.mean([x["ret"] for x in hi_tr]), 5) if hi_tr else None, **hi_rec}}
        index.append(row)
        say(f"  {sym:<9} {status:<5} trades {i_rec['all'].get('n')} · won {i_rec['all'].get('win', 0) * 100:.0f}% · per trade {i_rec['all'].get('exp', 0) * 100:+.2f}% · "
            f"over holding t {((i_rec['all'].get('over_hold') or {}).get('t') or 0):.1f} · today: {action}")

    # 4. context
    weak = [cols[j] for j in np.argsort(np.nan_to_num(r_now, nan=9.0), kind="stable") if r_now[j] == r_now[j] and r_now[j] <= 0.10 and C.iat[last, j] < I["ma200"].iat[last, j]][:15]
    avoid = [{**info(s), "below200": _f(C.iat[last, jx[s]] / I["ma200"].iat[last, jx[s]] - 1, 4)} for s in weak]
    vix = P["IDX"].get("VIX")
    n5 = I["n500"]
    market = {"n500": px(n5.iloc[-1]), "n500_d1": _f(n5.iloc[-1] / n5.iloc[-2] - 1, 4), "above200": bool(n5.iloc[-1] > n5.rolling(200).mean().iloc[-1]),
              "b50": _f(I["b50"].iloc[-1], 3), "b200": _f(I["b200"].iloc[-1], 3), "highs": int(I["highs"].iloc[-1]), "lows": int(I["lows"].iloc[-1]),
              "vix": px(vix["Close"].iloc[-1]) if vix is not None and len(vix) else None, "universe": int(I["elig"].iloc[-1].sum())}
    ev = {"deciles": deciles(P, I, t_start), "rejected": rejected(P, I, X, W, t_start)}
    res = {"v": 3, "asof": iso[-1], "built": datetime.now(IST).isoformat(timespec="minutes"), "first": iso[t_start], "split": SPLIT, "cost": COST, "top": TOP,
           "market": market, "leaders": leaders, "swing": swing, "index": index, "avoid": avoid, "footprints": footprints(P, I), "buildups": buildups,
           "participants": participants(), "deals": deals or [], "evidence": ev, "fno_date": buildups.get("date") if buildups else None}

    # small price histories for the stocks on the board, and the indexes
    want = list(dict.fromkeys([r["sym"] for r in held_rows] + [r["sym"] for r in leaders["buy"]] + [r["sym"] for r in s_new] + [r["sym"] for r in swing["open"]]))[:48]
    w = slice(max(n - 110, 0), n)
    r2 = lambda v: None if v != v else (round(float(v), 1) if v >= 1000 else round(float(v), 2))
    charts = {"d": iso[w], "s": {}, "i": {}}
    for s in want:
        j = jx[s]
        charts["s"][s] = {**{k.lower(): [r2(v) for v in P[k].iloc[w, j]] for k in ("O", "H", "L")}, "c": [r2(v) for v in C.iloc[w, j]], "m": [r2(v) for v in I["ma50"].iloc[w, j]]}
    for sym in INDEXES:
        d = P["IDX"].get(sym)
        if d is not None and len(d) > 260:
            cc = d["Close"]
            ww = slice(len(cc) - 250, len(cc))
            charts["i"][sym] = {"d": [x.date().isoformat() for x in cc.index[ww]], "c": [r2(v) for v in cc.iloc[ww]], "m5": [r2(v) for v in cc.rolling(5).mean().iloc[ww]],
                                "m200": [r2(v) for v in cc.rolling(200).mean().iloc[ww]]}
    live_since = (state or {}).get("live_since") or iso[-1]
    res["live_since"] = live_since
    new_state = {"v": 1, "asof": iso[-1], "live_since": live_since, "leaders": live, "swing": sw, "index": i_state}
    return {"signals": _clean(res), "charts": _clean(charts), "state": _clean(new_state)}


def orders(S: dict) -> list:
    """Today's orders as rows for the log: [date, book, action, symbol, close on the signal day, stop, note]."""
    rows, d = [], S["asof"]
    for r in S["leaders"]["buy"]:
        rows.append([d, "leaders", "BUY", r["sym"], r["price"], "", "next open"])
    for r in S["leaders"]["sell"]:
        rows.append([d, "leaders", "SELL", r["sym"], r["price"], "", r.get("why", "")])
    for r in S["swing"]["new"]:
        rows.append([d, "swing", "BUY", r["sym"], r["price"], r["stop"], "next open"])
    for r in S["swing"]["open"]:
        if r.get("sell"):
            rows.append([d, "swing", "SELL", r["sym"], r["price"], "", r["sell"]])
    for r in S["swing"]["done"]:
        if r.get("why") == "Stop hit":
            rows.append([r["out"], "swing", "STOPPED", r["sym"], r["p1"], "", "stop hit during the session"])
    for x in S["index"]:
        if x["action"] in ("buy", "sell"):
            rows.append([x["asof"], "index", x["action"].upper(), x["sym"], x["last"], "", x.get("why") or "next open"])
    return rows


def save(out: dict, folder=OUT) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dump = lambda o: json.dumps(o, separators=(",", ":"), allow_nan=False)
    (folder / "signals.json").write_text(dump(out["signals"]))
    (folder / "charts.json").write_text(dump(out["charts"]))
    (folder / "state.json").write_text(dump(out["state"]))
    head = ["date", "book", "action", "sym", "price", "stop", "note"]
    log = folder / "log.csv"
    old = pd.read_csv(log, dtype=str).fillna("") if log.exists() else pd.DataFrame(columns=head)
    seen = set(zip(old["date"], old["book"], old["action"], old["sym"]))
    rows = [[str(v) for v in r] for r in orders(out["signals"]) if (str(r[0]), r[1], r[2], r[3]) not in seen]
    if rows or not log.exists():
        pd.concat([old, pd.DataFrame(rows, columns=head)], ignore_index=True).to_csv(log, index=False)
    deals = out["signals"].get("deals") or []
    if deals:
        dl = folder / "deals.csv"
        new = pd.DataFrame(deals)
        if dl.exists():
            new = pd.concat([pd.read_csv(dl), new], ignore_index=True).drop_duplicates(["kind", "date", "sym", "who", "side", "qty"])
        new.tail(1500).to_csv(dl, index=False)


def load_state(folder=OUT) -> dict | None:
    try:
        return json.loads((Path(folder) / "state.json").read_text())
    except Exception:
        return None


def telegram_text(S: dict) -> str:
    L = [f"<b>Arka signals · {S['asof']}</b>"]
    ld, sw = S["leaders"], S["swing"]
    if ld["buy"] or ld["sell"]:
        L.append("\n<b>Leaders</b> (weekly check, trade at the next open)")
        if ld["sell"]:
            L.append("Sell: " + ", ".join(f"{r['sym']} ({(r['pnl'] or 0) * 100:+.0f}%)" for r in ld["sell"]))
        if ld["buy"]:
            L.append("Buy: " + ", ".join(r["sym"] for r in ld["buy"]))
    else:
        L.append("\n<b>Leaders</b>: no change" + ("" if ld["check_today"] else " (next check after the first session of next week)"))
    lc = ld.get("last_change") or {}
    if lc.get("date") == S["asof"] and (lc.get("bought") or lc.get("sold")):
        L.append("Model book at today's open: " + "; ".join(x for x in ("bought " + ", ".join(f"{b['sym']} {b['px']:,.2f}" for b in lc.get("bought") or []) if lc.get("bought") else "",
                                                                     "sold " + ", ".join(f"{b['sym']} {b['px']:,.2f}" for b in lc.get("sold") or []) if lc.get("sold") else "") if x))
    sells = [r for r in sw["open"] if r.get("sell")]
    stopped = [r for r in sw["done"] if r.get("why") == "Stop hit"]
    if sw["new"] or sells or stopped:
        L.append("\n<b>Swing</b>" + ("" if sw["status"] == "on" else " (on trial)" if sw["status"] == "trial" else " (switched off: for information)"))
        L += [f"Buy {r['sym']} at the next open · ref {r['price']:,.2f} · stop about {r['stop']:,.2f}" for r in sw["new"][:10]]
        L += [f"Sell {r['sym']} at the next open · {r['sell']} · {(r['pnl'] or 0) * 100:+.1f}%" for r in sells[:10]]
        L += [f"Stopped out: {r['sym']} {r['ret'] * 100:+.1f}%" for r in stopped[:10]]
    else:
        L.append("\n<b>Swing</b>: nothing new")
    for x in S["index"]:
        if x["action"] == "buy":
            L.append(f"\n<b>{x['name']}</b>: dip in an uptrend · BUY at the next open (ref {x['last']:,.0f})")
        elif x["action"] == "sell":
            L.append(f"\n<b>{x['name']}</b>: SELL at the next open · {x['why']}")
    L.append("\nRecords, stops and sizing: F&O page in the app. Not advice.")
    return "\n".join(L)


def send_telegram(text: str) -> str:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return "Telegram secrets not set: no message sent."
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=30)
        return "Telegram message sent." if r.status_code == 200 else f"Telegram refused the message (HTTP {r.status_code})."
    except Exception as e:
        return f"Telegram could not be reached ({type(e).__name__})."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eod", default="eod2_data", help="folder holding a clone of github.com/BennyThadikaran/eod2_data")
    ap.add_argument("--send", action="store_true", help="send today's orders to Telegram (only when the data moved to a new session)")
    ap.add_argument("--no-topup", action="store_true", help="do not ask NSE for the newest sessions")
    ap.add_argument("--force", action="store_true", help="write the files even if nothing changed since the last run")
    a = ap.parse_args(argv)
    P = build_panels(a.eod)
    if not a.no_topup:
        added = top_up(P, datetime.now(IST).date(), http_fetch)
        print(f"added from NSE: {', '.join(added) if added else 'no newer session published'}")
    finish(P)
    deals = [] if a.no_topup else fetch_deals(http_fetch)
    state = load_state()
    print(f"data to {P['C'].index[-1].date()} · deals read: {len(deals)} · saved books: {'to ' + state['asof'] if state else 'none, starting from the replay'}")
    if state and str(state.get("asof", "")) > P["C"].index[-1].date().isoformat():
        print("The saved books are newer than the prices that could be read today (NSE did not answer?). Nothing was changed.")
        return 0
    out = run(P, deals, state)
    S = out["signals"]
    mark = lambda Z: (Z.get("v"), Z.get("asof"), Z.get("fno_date"), tuple(x.get("asof") for x in Z.get("index") or []), len(Z.get("deals") or []))
    try:
        old = json.loads((OUT / "signals.json").read_text())
    except Exception:
        old = None
    if old and not a.force and mark(old) == mark(S):
        print("Nothing changed since the last run: the files were left as they are.")
        return 0
    save(out)
    print(f"leaders: buy {len(S['leaders']['buy'])}, sell {len(S['leaders']['sell'])}, holding {len(S['leaders']['holdings'])} · swing: new {len(S['swing']['new'])}, "
          f"open {len(S['swing']['open'])} · saved to {OUT}")
    if a.send:
        fresh = not state or state.get("asof") != S["asof"]
        print(send_telegram(telegram_text(S)) if fresh else "Same session as the last run: no Telegram message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
