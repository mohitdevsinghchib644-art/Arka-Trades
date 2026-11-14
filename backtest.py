"""
Signal tests: what followed each state on the loaded history.

Execution model: a state is known after the close of session t. Entry is the close of t+1 (next session),
exit is the close of t+1+h. Nothing is assumed to trade at the close that produced the signal.

Measures per state and horizon h
  n               number of observations
  mean / median   forward return of the sector minus the benchmark's over the same sessions (pp)
  beat_pct        share of observations with a positive excess return
  abs_mean        forward return of the sector itself (%)
  mae / mfe       average worst / best point reached during the holding window, vs the entry (%)
  t_stat          mean / (std / sqrt(n)). Observations from different sectors on the same date are correlated,
                  so this OVERSTATES significance. Read it as a rough guide only.

Sampling: "entries" = the first session of each new state. "all" = every h-th session while in the state
(non-overlapping windows), which avoids counting one move many times.

Limits that the screen must show next to these numbers
  * Custom groups use today's member list for the whole history: survivorship bias flatters results.
  * Sector baskets are not directly tradable. Costs are shown as a separate round-trip figure.
  * The rules were written before seeing the user's data and are not tuned to it; the two halves of the
    history are shown separately to check that a result is stable, not to pick parameters.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONS = (5, 10, 20, 40, 60)


def forward_panels(levels: pd.DataFrame, bench: pd.Series, h: int, lag: int = 1):
    """-> (excess pp, absolute %, mae %, mfe %) aligned to the SIGNAL date t."""
    entry, exit_ = levels.shift(-lag), levels.shift(-(lag + h))
    be, bx = bench.shift(-lag), bench.shift(-(lag + h))
    absr = (exit_ / entry - 1) * 100
    exc = absr.sub((bx / be - 1) * 100, axis=0)
    rev = levels.iloc[::-1]
    fmin = rev.rolling(h, min_periods=h).min().iloc[::-1].shift(-(lag + 1))     # min over t+lag+1 .. t+lag+h
    fmax = rev.rolling(h, min_periods=h).max().iloc[::-1].shift(-(lag + 1))
    return exc, absr, (fmin / entry - 1) * 100, (fmax / entry - 1) * 100


def _sample_mask(state: pd.DataFrame, h: int, mode: str) -> pd.DataFrame:
    if mode == "entries":
        m = state.ne(state.shift(1))
        m.iloc[0] = False
        return m
    m = pd.DataFrame(False, index=state.index, columns=state.columns)
    m.iloc[::h] = True
    return m


def _stats(x: np.ndarray) -> dict:
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": np.nan, "median": np.nan, "beat_pct": np.nan, "t_stat": np.nan}
    sd = x.std(ddof=1) if n > 1 else np.nan
    return {"n": n, "mean": float(x.mean()), "median": float(np.median(x)), "beat_pct": float((x > 0).mean() * 100),
            "t_stat": float(x.mean() / (sd / np.sqrt(n))) if n > 2 and sd and sd > 0 else np.nan}


def state_table(state: pd.DataFrame, levels: pd.DataFrame, bench: pd.Series, horizons=HORIZONS, mode: str = "entries",
                start=None, end=None, states=None) -> pd.DataFrame:
    rows = []
    sl = slice(start, end)
    for h in horizons:
        exc, absr, mae, mfe = forward_panels(levels, bench, h)
        mask = _sample_mask(state, h, mode).loc[sl]
        st = state.loc[sl]
        for name in (states or sorted(set(np.unique(st.to_numpy())))):
            sel = (mask & st.eq(name)).to_numpy()
            e = exc.loc[sl].to_numpy()[sel]
            s = _stats(e)
            ok = ~np.isnan(e)
            g = lambda p: float(np.nanmean(p.loc[sl].to_numpy()[sel][ok])) if ok.any() else np.nan
            rows.append({"state": name, "horizon": h, **s, "abs_mean": g(absr), "mae": g(mae), "mfe": g(mfe)})
    return pd.DataFrame(rows)


def baseline_table(H: dict, state: pd.DataFrame, h: int = 20, start=None, end=None) -> pd.DataFrame:
    """Simple alternatives on the same non-overlapping dates, to see whether the states add anything."""
    exc, _, _, _ = forward_panels(H["levels"], H["bench"], h)
    sl = slice(start, end)
    dates = exc.loc[sl].index[::h]
    e = exc.loc[dates]
    top = lambda p: p.loc[dates].rank(axis=1, pct=True) >= 2 / 3
    d50 = (H["pct50"] - H["pct50"].shift(20)).loc[dates]
    groups = {
        "All sectors (no selection)": pd.DataFrame(True, index=dates, columns=e.columns),
        "Top third by 20-session relative return": top(H["rel_20"]),
        "Top third by 60-session relative return": top(H["rel_60"]),
        "50-EMA breadth higher than 20 sessions ago": d50 > 0,
        "Top third by 20-session RS and 60%+ above 50-EMA": top(H["rel_20"]) & (H["pct50"].loc[dates] >= 60),
    }
    for s in sorted(set(np.unique(state.loc[dates].to_numpy()))):
        groups[f"State: {s}"] = state.loc[dates].eq(s)
    rows = []
    for name, mask in groups.items():
        rows.append({"selection": name, **_stats(e.to_numpy()[mask.to_numpy()])})
    return pd.DataFrame(rows)


def state_stats(state: pd.DataFrame) -> pd.DataFrame:
    """How often each state occurs, how long it lasts, how many entries per sector per year."""
    vals = state.to_numpy()
    chg = state.ne(state.shift(1))
    chg.iloc[0] = False
    yrs = len(state) / 252 * state.shape[1]
    rows = []
    for s in sorted(set(np.unique(vals))):
        days = int((vals == s).sum())
        entries = int((chg & state.eq(s)).to_numpy().sum())
        rows.append({"state": s, "share_of_time_pct": days / vals.size * 100, "entries": entries,
                     "avg_sessions": days / entries if entries else np.nan,
                     "entries_per_sector_year": entries / yrs if yrs else np.nan})
    return pd.DataFrame(rows)


def split_point(index: pd.DatetimeIndex, frac: float = 0.6):
    return index[int(len(index) * frac)]


def round_trip_cost(brokerage_pct: float = 0.0, slippage_pct: float = 0.05, stt_pct: float = 0.1,
                    stamp_buy_pct: float = 0.015, exch_pct: float = 0.00297, sebi_pct: float = 0.0001, gst: float = 0.18) -> dict:
    """Delivery-equity round trip as % of trade value. DEFAULTS ARE ASSUMPTIONS: check them against a contract
    note. Per side: brokerage, exchange charge, SEBI fee (+ GST on those three), STT, slippage; stamp duty on the buy."""
    side = brokerage_pct + exch_pct + sebi_pct
    total = 2 * (side * (1 + gst) + stt_pct + slippage_pct) + stamp_buy_pct
    return {"total_pct": total, "stt": 2 * stt_pct, "stamp": stamp_buy_pct, "brokerage_exchange_sebi_gst": 2 * side * (1 + gst),
            "slippage": 2 * slippage_pct}
