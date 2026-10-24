"""
index_engine.py — Arka "Nifty Indices" analytics (pure functions, no network).

Input : {index name -> OHLC DataFrame (Open, High, Low, Close; daily; ~1 year+)}
Output: one row per index with
    price, 1D %, 1W %, weekly RSI(14), price vs weekly EMA 10/20/50 and daily EMA 200,
    % from weekly 20-EMA, price vs DAILY and WEEKLY classic pivots (previous completed bar),
    RS rank history (percentile among the loaded indices vs the benchmark at T-30/-15/-5/T0).

RS definition: 20-session relative performance of the index vs the benchmark
    RS(t) = [close(t)/close(t-20)] / [bench(t)/bench(t-20)]
and the rank is the percentile (0-99) of that RS among all loaded indices on that day.

Honest limits: weekly EMA-200 needs ~4 years of weekly bars, so the 200 marker here is the
200-DAY EMA. Everything is computed from the supplied closes; nothing is estimated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RS_OFFSETS = (30, 15, 5, 0)
RS_LOOKBACK = 20


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


def _chip(flag) -> str:
    return "—" if flag is None or (isinstance(flag, float) and np.isnan(flag)) else ("🟢" if flag else "🔴")


def analyse_histories(hists: dict, base: str = "NIFTY 50") -> pd.DataFrame:
    """One row per index that has enough history. Raises ValueError if the benchmark is missing."""
    clean = {}
    for name, df in (hists or {}).items():
        try:
            d = _clean(df)
        except Exception:
            continue
        if len(d) >= 30:
            clean[name] = d
    if base not in clean:
        raise ValueError(f"benchmark {base} has no usable history")

    # ---- RS rank history (percentile of 20-session relative performance) ----
    closes = pd.DataFrame({n: d["Close"] for n, d in clean.items()}).sort_index().ffill()
    bench = closes[base]
    rel = closes.div(closes.shift(RS_LOOKBACK)).div(bench.div(bench.shift(RS_LOOKBACK)), axis=0)
    rel = rel.drop(columns=[base])
    ranks = {}
    for off in RS_OFFSETS:
        pos = len(rel) - 1 - off
        if pos < 0:
            ranks[off] = pd.Series(np.nan, index=rel.columns)
            continue
        row = rel.iloc[pos]
        ranks[off] = (row.rank(pct=True) * 99).round()

    rows = []
    for name, d in clean.items():
        c = d["Close"]
        w = weekly(d)
        wc = w["Close"]
        last = float(c.iloc[-1])
        r1d = (last / float(c.iloc[-2]) - 1) * 100 if len(c) >= 2 else np.nan
        r1w = (last / float(c.iloc[-6]) - 1) * 100 if len(c) >= 6 else np.nan
        r1m = (last / float(c.iloc[-22]) - 1) * 100 if len(c) >= 22 else np.nan
        w_rsi = float(rsi(wc).iloc[-1]) if len(wc) > 14 else np.nan
        ema = {n: wc.ewm(span=n, adjust=False).mean().iloc[-1] for n in (10, 20, 50)} if len(wc) >= 10 else {}
        d200 = c.ewm(span=200, adjust=False).mean().iloc[-1] if len(c) >= 200 else np.nan
        flags = {
            "w10": (last > ema[10]) if 10 in ema and len(wc) >= 10 else None,
            "w20": (last > ema[20]) if 20 in ema and len(wc) >= 20 else None,
            "w50": (last > ema[50]) if 50 in ema and len(wc) >= 50 else None,
            "d200": (last > d200) if pd.notna(d200) else None,
        }
        pct_w20 = (last / ema[20] - 1) * 100 if 20 in ema and len(wc) >= 20 else np.nan
        dpiv = pivot_label(last, *(d.iloc[-2][["High", "Low", "Close"]].tolist())) if len(d) >= 2 else "—"
        wpiv = pivot_label(last, *(w.iloc[-2][["High", "Low", "Close"]].tolist())) if len(w) >= 2 else "—"
        row = {"index": name, "price": last, "r1d": r1d, "r1w": r1w, "r1m": r1m, "w_rsi": w_rsi,
               "pct_w20": pct_w20, "d_pivot": dpiv, "w_pivot": wpiv,
               "ema_chips": f"10{_chip(flags['w10'])} 20{_chip(flags['w20'])} 50{_chip(flags['w50'])} D200{_chip(flags['d200'])}",
               "asof": str(c.index[-1])[:10]}
        for off in RS_OFFSETS:
            row[f"rs_t{off}"] = float(ranks[off].get(name, np.nan)) if name != base else np.nan
        rows.append(row)
    return pd.DataFrame(rows)
