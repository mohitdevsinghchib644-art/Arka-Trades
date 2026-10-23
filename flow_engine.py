"""
flow_engine.py — Arka Money Flow analytics (v1)

Takes the constituents of ONE index (symbols + optional free-float weights from
NSE) and computes everything from ~1 year of daily prices (Yahoo, batched):

  per stock   returns 1D/1W/1M/3M, relative strength vs a benchmark, position vs
              20/50/200 DMA, relative volume, traded-value ratio, close location,
              BUYING-PRESSURE and SELLING-PRESSURE scores, contribution to the
              index move
  per index   % advancing, % above 20/50/200 DMA (today AND as a 120-day series),
              weighted index proxy return, participation signal
              (BROAD / NARROW / HEAVYWEIGHTS DRAGGING / BROAD SELLING),
              breadth-deterioration flag

Honest labelling: "pressure" scores are price + volume + close-location evidence.
They do NOT identify who bought. Traded value is not net buying.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

from data_engine import _parse_download

BENCHMARKS = {"NIFTY 50": "^NSEI", "NIFTY 500": "^CRSLDX"}
DEFAULT_BENCH = "NIFTY 50"
CHUNK = 80


# ───────────────────────── download ─────────────────────────
def _download_frames(tickers: list, period="1y") -> dict:
    out = {}
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        try:
            df = yf.download(chunk, period=period, interval="1d", group_by="ticker",
                             threads=True, progress=False, auto_adjust=False)
        except Exception:
            continue
        out.update(_parse_download(df, chunk))
    return out


def _clean_symbol(s) -> str:
    return str(s).strip().upper()


# ───────────────────────── scoring ─────────────────────────
def pressure_scores(r1d, relvol, close_loc, rs_1w, rs_1m, above50):
    """
    Transparent 0-100 scores (evidence of buying / selling PRESSURE, not identity):
      price direction today ........ 20     volume expansion (>=1.5x: 25, >=1.2x: 12)
      close location (top/bottom) .. 20     short-term RS (1W) ........... 15
      medium-term RS (1M) .......... 10     trend (vs 50DMA) ............. 10
    """
    def f(x):
        return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)

    r1d, relvol, close_loc = f(r1d), f(relvol), f(close_loc)
    rs_1w, rs_1m = f(rs_1w), f(rs_1m)
    vol_pts = 0
    if relvol is not None:
        vol_pts = 25 if relvol >= 1.5 else 12 if relvol >= 1.2 else 0
    buy = sell = 0
    if r1d is not None:
        buy += 20 if r1d > 0 else 0
        sell += 20 if r1d < 0 else 0
    if r1d is not None and r1d > 0:
        buy += vol_pts
    if r1d is not None and r1d < 0:
        sell += vol_pts
    if close_loc is not None:
        buy += 20 if close_loc >= 0.7 else 0
        sell += 20 if close_loc <= 0.3 else 0
    if rs_1w is not None:
        buy += 15 if rs_1w > 0 else 0
        sell += 15 if rs_1w < 0 else 0
    if rs_1m is not None:
        buy += 10 if rs_1m > 0 else 0
        sell += 10 if rs_1m < 0 else 0
    if above50 is True:
        buy += 10
    elif above50 is False:
        sell += 10
    return int(buy), int(sell)


def _label(buy, sell):
    if buy >= 70 and buy > sell:
        return "BUYING PRESSURE"
    if sell >= 70 and sell > buy:
        return "SELLING PRESSURE"
    return "NEUTRAL"


def participation_signal(idx_1d, adv_pct):
    if pd.isna(idx_1d) or pd.isna(adv_pct):
        return "UNAVAILABLE"
    if idx_1d > 0 and adv_pct < 40:
        return "NARROW LEADERSHIP"
    if idx_1d > 0 and adv_pct >= 60:
        return "BROAD PARTICIPATION"
    if idx_1d < 0 and adv_pct >= 60:
        return "HEAVYWEIGHTS DRAGGING"
    if idx_1d < 0 and adv_pct < 40:
        return "BROAD SELLING"
    return "MIXED"


# ───────────────────────── core ─────────────────────────
def analyse_frames(frames: dict, symbols: list, weights: dict | None, bench_ticker: str) -> dict:
    """Pure function (no network) — all analytics from already-downloaded frames."""
    syms = [s for s in symbols if f"{s}.NS" in frames]
    if len(syms) < 3:
        raise ValueError("not enough price history returned")

    close = pd.DataFrame({s: frames[f"{s}.NS"]["Close"].astype(float) for s in syms}).sort_index()
    vol = pd.DataFrame({s: frames[f"{s}.NS"]["Volume"].astype(float) for s in syms}).reindex(close.index)
    close = close.ffill()
    vol = vol.fillna(0)
    last_rows = {s: frames[f"{s}.NS"].dropna(subset=["Close"]).iloc[-1] for s in syms}

    def ret(n):
        if len(close) <= n:
            return pd.Series(np.nan, index=close.columns)
        return (close.iloc[-1] / close.iloc[-1 - n] - 1) * 100

    r1d, r1w, r1m, r3m = ret(1), ret(5), ret(21), ret(63)

    bench = frames.get(bench_ticker)
    b = {k: np.nan for k in ("1d", "1w", "1m", "3m")}
    if bench is not None and not bench.empty:
        bc = bench["Close"].astype(float).dropna()
        for k, n in (("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63)):
            if len(bc) > n:
                b[k] = (bc.iloc[-1] / bc.iloc[-1 - n] - 1) * 100

    sma = {n: close.rolling(n).mean() for n in (20, 50, 200)}
    above = {n: (close.iloc[-1] > sma[n].iloc[-1]).where(sma[n].iloc[-1].notna()) for n in (20, 50, 200)}

    relvol = vol.iloc[-1] / vol.iloc[-21:-1].mean().replace(0, np.nan) if len(vol) > 21 else pd.Series(np.nan, index=close.columns)
    tv = close * vol
    tv_ratio = tv.iloc[-1] / tv.iloc[-21:-1].mean().replace(0, np.nan) if len(tv) > 21 else pd.Series(np.nan, index=close.columns)

    w = pd.Series({s: (weights or {}).get(s, np.nan) for s in syms}, dtype=float)
    if w.notna().sum() < max(3, len(syms) // 2):
        w = pd.Series(1.0, index=syms)          # equal-weight fallback
        weight_basis = "equal-weight (free-float weights unavailable)"
    else:
        w = w.fillna(w.median())
        weight_basis = "free-float market cap (NSE ffmc)"
    w = w / w.sum()

    rows = []
    for s in syms:
        lr = last_rows[s]
        hi, lo, cl = float(lr["High"]), float(lr["Low"]), float(lr["Close"])
        loc = (cl - lo) / (hi - lo) if hi > lo else np.nan
        a50 = above[50].get(s)
        a50 = None if pd.isna(a50) else bool(a50)
        rs1w = r1w[s] - b["1w"] if pd.notna(b["1w"]) else np.nan
        rs1m = r1m[s] - b["1m"] if pd.notna(b["1m"]) else np.nan
        rs3m = r3m[s] - b["3m"] if pd.notna(b["3m"]) else np.nan
        buy, sell = pressure_scores(r1d[s], relvol[s], loc, rs1w, rs1m, a50)
        rows.append({
            "symbol": s, "price": cl, "weight": float(w[s]) * 100,
            "r1d": r1d[s], "r1w": r1w[s], "r1m": r1m[s], "r3m": r3m[s],
            "rs_1w": rs1w, "rs_1m": rs1m, "rs_3m": rs3m,
            "above20": above[20].get(s), "above50": above[50].get(s), "above200": above[200].get(s),
            "relvol": relvol[s], "tv_ratio": tv_ratio[s], "close_loc": loc,
            "buy_score": buy, "sell_score": sell, "pressure": _label(buy, sell),
            "contrib": float(w[s]) * r1d[s] if pd.notna(r1d[s]) else np.nan,
        })
    table = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)

    # ---- index-level summary --------------------------------------------------
    valid_1d = table.r1d.dropna()
    adv_pct = float((valid_1d > 0).sum() / len(valid_1d) * 100) if len(valid_1d) else np.nan

    def wret(col):
        t = table.dropna(subset=[col])
        return float((t[col] * t.weight).sum() / t.weight.sum()) if len(t) else np.nan

    idx_1d, idx_1w, idx_1m = wret("r1d"), wret("r1w"), wret("r1m")

    def pct_above(col):
        s = table[col].dropna()
        return float(s.astype(bool).mean() * 100) if len(s) else np.nan

    # ---- breadth time series (last 120 sessions) --------------------------------
    series = pd.DataFrame(index=close.index)
    for n in (20, 50, 200):
        valid = sma[n].notna().sum(axis=1).replace(0, np.nan)
        series[f"above{n}"] = (close > sma[n]).where(sma[n].notna()).sum(axis=1) / valid * 100
    chg = close.pct_change()
    series["advancing"] = (chg > 0).sum(axis=1) / chg.notna().sum(axis=1).replace(0, np.nan) * 100
    norm = close.bfill().div(close.bfill().iloc[0])
    series["proxy"] = (norm * w).sum(axis=1)
    series = series.tail(120)

    deterioration = False
    if len(series) > 60 and series.above50.notna().iloc[-11:].all():
        near_high = series.proxy.iloc[-1] >= 0.98 * series.proxy.iloc[-60:].max()
        fell = series.above50.iloc[-1] <= series.above50.iloc[-11] - 10
        deterioration = bool(near_high and fell)

    summary = {
        "stocks": len(syms), "missing": [s for s in symbols if s not in syms],
        "adv_pct": adv_pct, "idx_1d": idx_1d, "idx_1w": idx_1w, "idx_1m": idx_1m,
        "above20": pct_above("above20"), "above50": pct_above("above50"), "above200": pct_above("above200"),
        "signal": participation_signal(idx_1d, adv_pct), "deterioration": deterioration,
        "weight_basis": weight_basis, "bench_ret": b,
        "asof": str(close.index[-1])[:10],
    }
    return {"table": table, "series": series.reset_index().rename(columns={"index": "date", "Date": "date"}), "summary": summary}


@st.cache_data(ttl=600, show_spinner=False, max_entries=24)
def _analyse_cached(symbols: tuple, weights_items: tuple, bench_name: str) -> dict:
    syms = [_clean_symbol(s) for s in symbols]
    bench_ticker = BENCHMARKS.get(bench_name, "^NSEI")
    tickers = [f"{s}.NS" for s in syms] + [bench_ticker]
    frames = _download_frames(tickers)
    if bench_ticker not in frames and bench_ticker != "^NSEI":
        frames.update(_download_frames(["^NSEI"]))
        bench_ticker = "^NSEI"
    return analyse_frames(frames, syms, dict(weights_items), bench_ticker)


def analyse_index(symbols, weights: dict | None = None, bench_name: str = DEFAULT_BENCH):
    """Cached (10 min). Returns dict(table, series, summary) or None on failure."""
    try:
        items = tuple(sorted((k, float(v)) for k, v in (weights or {}).items() if pd.notna(v)))
        return _analyse_cached(tuple(_clean_symbol(s) for s in symbols), items, bench_name)
    except Exception:
        return None
