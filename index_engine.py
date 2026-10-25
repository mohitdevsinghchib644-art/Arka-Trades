"""
index_engine.py — Arka "Nifty Indices" analytics v2 (pure functions, no network).

Input : {index name -> OHLC DataFrame (Open, High, Low, Close; daily; ~1 year+)}
Output: one row per index with
    price, 1D / 1W / 1M %, daily RSI(14), weekly RSI(14),
    price vs DAILY EMA 10 / 20 / 50 / 200  (flag ab10.. = price above, dist10.. = % distance),
    price vs DAILY and WEEKLY classic pivots (previous completed bar).

v2: the RS-rank history (percentile ratings) was removed in favour of RSI + EMA position.
Honest limits: EMA-200 needs 200 daily bars (flag is NaN without them). Everything is computed from the
supplied closes; nothing is estimated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EMA_SPANS = (10, 20, 50, 200)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    d = df[["Open", "High", "Low", "Close"]].astype(float).copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d.dropna(subset=["Close"])
    d["High"] = d["High"].fillna(d["Close"])
    d["Low"] = d["Low"].fillna(d["Close"])
    d["Open"] = d["Open"].fillna(d["Close"])
    d = d[d["Close"] > 0]
    return d[~d.index.duplicated(keep="last")].sort_index()


def weekly(d: pd.DataFrame) -> pd.DataFrame:
    w = d.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    return w.dropna(subset=["Close"])


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder-style RSI (EWM seeded from the first observation; within ~1 point of TradingView after warm-up)."""
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(~((dn == 0) & up.notna()), 100.0)


def pivot_label(price, high, low, close) -> str:
    """Where price sits versus the classic pivot ladder built from one completed bar."""
    if any(pd.isna(x) for x in (price, high, low, close)):
        return "—"
    pp = (high + low + close) / 3
    r1, s1 = 2 * pp - low, 2 * pp - high
    r2, s2 = pp + (high - low), pp - (high - low)
    if price >= r2: return "≥R2"
    if price >= r1: return "R1-R2"
    if price >= pp: return "PP-R1"
    if price >= s1: return "S1-PP"
    if price >= s2: return "S2-S1"
    return "<S2"


def analyse_histories(hists: dict, base: str = "NIFTY 50") -> pd.DataFrame:
    """One row per index that has enough history (30+ daily bars). `base` is kept for compatibility."""
    clean = {}
    for name, df in (hists or {}).items():
        try:
            d = _clean(df)
        except Exception:
            continue
        if len(d) >= 30:
            clean[name] = d
    if not clean:
        raise ValueError("no index has usable history")

    rows = []
    for name, d in clean.items():
        c = d["Close"]
        n = len(c)
        w = weekly(d)
        wc = w["Close"]
        last = float(c.iloc[-1])
        row = {
            "index": name, "price": last,
            "r1d": (last / float(c.iloc[-2]) - 1) * 100 if n >= 2 else np.nan,
            "r1w": (last / float(c.iloc[-6]) - 1) * 100 if n >= 6 else np.nan,
            "r1m": (last / float(c.iloc[-22]) - 1) * 100 if n >= 22 else np.nan,
            "d_rsi": float(rsi(c).iloc[-1]) if n > 15 else np.nan,
            "w_rsi": float(rsi(wc).iloc[-1]) if len(wc) > 15 else np.nan,
            "d_pivot": pivot_label(last, *(d.iloc[-2][["High", "Low", "Close"]].tolist())) if n >= 2 else "—",
            "w_pivot": pivot_label(last, *(w.iloc[-2][["High", "Low", "Close"]].tolist())) if len(w) >= 2 else "—",
            "asof": str(c.index[-1])[:10],
        }
        for s in EMA_SPANS:
            if n >= s:
                e = float(c.ewm(span=s, adjust=False).mean().iloc[-1])
                row[f"ab{s}"] = last > e
                row[f"dist{s}"] = (last / e - 1) * 100
            else:
                row[f"ab{s}"] = np.nan
                row[f"dist{s}"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)
