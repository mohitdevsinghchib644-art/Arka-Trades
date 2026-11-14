"""
Price loading from Yahoo Finance in batches, with a retry pass and a per-ticker failure list.
No Streamlit here. Nothing is filled, repaired or substituted: bad data is reported.

load_prices(symbols, index_tickers, period) -> dict
  close, high, low, volume     dates x NSE symbol (adjusted OHLC, raw volume), NaN where there is no bar
  index                        dates x index ticker (close)
  failures                     {ticker: reason}       never silently dropped
  stale                        symbols whose last bar is more than 5 sessions behind the newest date (excluded, listed)
  jumps                        [{symbol, date, move_pct}] one-session moves above 25% (listed for review, not changed)
  duplicates_dropped, non_positive, loaded_at, source, adjusted
"""
from __future__ import annotations

import time
from datetime import datetime

import numpy as np
import pandas as pd

from .config import JUMP_PCT
from .data.validation import IST

FIELDS = ("Close", "High", "Low", "Volume")


def _split(raw, tickers) -> dict:
    out = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        lv0 = set(raw.columns.get_level_values(0))
        if "Close" in lv0 and not (set(tickers) & lv0):
            raw = raw.swaplevel(axis=1).sort_index(axis=1)
        top = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in top:
                f = raw[t].dropna(how="all")
                if "Close" in f and f["Close"].notna().sum() > 0:
                    out[t] = f
    elif len(tickers) == 1 and "Close" in raw:
        out[tickers[0]] = raw.dropna(how="all")
    return out


def _yahoo(tickers, period):
    import yfinance as yf
    return yf.download(list(tickers), period=period, interval="1d", group_by="ticker", auto_adjust=True,
                       progress=False, threads=True)


def download(tickers, period="3y", chunk=100, pause=0.5, fetch=_yahoo, progress=None):
    """-> ({ticker: OHLCV frame}, {ticker: reason}). Second pass retries every missing ticker in small batches."""
    tickers = list(dict.fromkeys(tickers))
    got, why = {}, {}

    def run(batch, size):
        for i in range(0, len(batch), size):
            part = batch[i:i + size]
            try:
                got.update(_split(fetch(part, period), part))
                for t in part:
                    if t not in got:
                        why[t] = "no data returned"
            except Exception as exc:
                for t in part:
                    why[t] = f"{type(exc).__name__}: {exc}"[:160]
            if progress:
                progress(len(got), len(tickers))
            time.sleep(pause)

    run(tickers, chunk)
    missing = [t for t in tickers if t not in got]
    if missing:
        time.sleep(pause * 4)
        run(missing, 20)
    return got, {t: why.get(t, "no data returned") for t in tickers if t not in got}


def _norm(f: pd.DataFrame):
    idx = pd.to_datetime(f.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    f = f.copy()
    f.index = idx.normalize()
    dup = int(f.index.duplicated(keep="last").sum())
    f = f[~f.index.duplicated(keep="last")].sort_index()
    return f, dup


def to_panels(frames: dict, symbols, index_tickers=(), suffix=".NS") -> dict:
    cols = {k: {} for k in FIELDS}
    dups = nonpos = 0
    for s in symbols:
        f = frames.get(f"{s}{suffix}")
        if f is None:
            continue
        f, d = _norm(f)
        dups += d
        c = pd.to_numeric(f["Close"], errors="coerce")
        bad = c <= 0
        nonpos += int(bad.sum())
        c = c.mask(bad)
        cols["Close"][s] = c
        for k in ("High", "Low", "Volume"):
            cols[k][s] = pd.to_numeric(f[k], errors="coerce").where(c.notna()) if k in f else pd.Series(np.nan, index=c.index)
    P = {k.lower(): (pd.DataFrame(v).sort_index() if v else pd.DataFrame()) for k, v in cols.items()}
    ix = {}
    for t in index_tickers:
        if t in frames:
            f, d = _norm(frames[t])
            dups += d
            ix[t] = pd.to_numeric(f["Close"], errors="coerce").where(lambda x: x > 0)
    P["index"] = pd.DataFrame(ix).sort_index() if ix else pd.DataFrame()

    close = P["close"]
    stale, jumps = [], []
    if len(close):
        newest = close.index[-1]
        lastv = close.apply(lambda c: c.last_valid_index())
        cutoff = close.index[-6] if len(close) > 6 else close.index[0]
        stale = sorted(s for s in close.columns if lastv[s] is None or lastv[s] < cutoff)
        for k in ("close", "high", "low", "volume"):
            P[k] = P[k].drop(columns=[s for s in stale if s in P[k].columns])
        r = (P["close"] / P["close"].shift(1) - 1) * 100
        big = r.abs() > JUMP_PCT
        for s in big.columns[big.any()]:
            for d, v in r[s][big[s]].items():
                jumps.append({"symbol": s, "date": str(d.date()), "move_pct": round(float(v), 1)})
    P.update({"stale": stale, "jumps": jumps, "duplicates_dropped": dups, "non_positive": nonpos})
    return P


def load_prices(symbols, index_tickers, period="3y", fetch=_yahoo, progress=None) -> dict:
    tickers = [f"{s}.NS" for s in symbols] + list(index_tickers)
    frames, failures = download(tickers, period, fetch=fetch, progress=progress)
    P = to_panels(frames, symbols, index_tickers)
    P.update({"failures": failures, "loaded_at": datetime.now(IST).isoformat(timespec="seconds"),
              "source": "Yahoo Finance daily bars (unofficial, delayed)", "adjusted": "adjusted by Yahoo; not cross-checked",
              "period": period, "requested": len(symbols)})
    return P


def drop_partial_session(P: dict, last_complete: str) -> tuple[dict, list]:
    """Remove bars dated after the last complete session, so a part-day is never compared with full days."""
    cut = pd.Timestamp(last_complete)
    dropped = []
    for k in ("close", "high", "low", "volume", "index"):
        df = P.get(k)
        if df is not None and len(df):
            late = df.index[df.index > cut]
            if len(late):
                dropped = sorted({str(d.date()) for d in late} | set(dropped))
                P[k] = df.loc[df.index <= cut]
    return P, dropped
