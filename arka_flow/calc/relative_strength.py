"""
Relative strength. Pure functions on price levels (dates x sectors) and one benchmark series.

Formulas (h = horizon in trading sessions, t = latest common session)
  absolute return      abs_h   = level[t] / level[t-h] - 1                       (%)
  benchmark return     bench_h = bench[t] / bench[t-h] - 1                       (%)
  RELATIVE RETURN      rel_h   = abs_h - bench_h                                 (percentage points)
  relative price ratio ratio   = level / bench
  RATIO CHANGE         ratio_chg_h = ratio[t] / ratio[t-h] - 1                   (%)
        rel_h and ratio_chg_h are different numbers: sector +10%, benchmark +5% gives
        rel = +5.00 pp but ratio change = 1.10 / 1.05 - 1 = +4.76%. Both are reported.
  RS momentum          rs_mom   = ratio_chg_h
  RS acceleration      rs_accel = rs_mom[t] - rs_mom[t-lag]                      (pp)
  RS trend             "up"   if ratio > SMA(ratio, 50) and that SMA is higher than `lag` sessions ago
                       "down" if ratio < SMA(ratio, 50) and that SMA is lower
                       "mixed" otherwise
  percentile rank      rank of rel_h across EVERY sector that has a value, 100 = strongest.
                       n_ranked is returned beside it so a rank is never quoted without its universe size.

Sessions: sector and benchmark are aligned on the dates where the benchmark has a value. Nothing is
forward-filled. A horizon without enough history returns NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import HORIZONS, LEADER_CONSISTENCY, LEADER_PCTILE, RANK_MOVE


def _check_index(obj, name):
    if obj.index.has_duplicates:
        raise ValueError(f"{name}: duplicate dates in the index")
    if not obj.index.is_monotonic_increasing:
        raise ValueError(f"{name}: dates are not sorted")


def common_sessions(levels: pd.DataFrame, bench: pd.Series):
    """Rows where the benchmark has a value. No filling."""
    _check_index(levels, "levels")
    _check_index(bench, "benchmark")
    b = bench.dropna()
    idx = levels.index.intersection(b.index)
    return levels.loc[idx], b.loc[idx]


def horizon_return(x, h: int):
    """% change over h rows. NaN where there is not enough history or either end is missing."""
    return (x / x.shift(h) - 1) * 100


def relative_panels(levels: pd.DataFrame, bench: pd.Series, h: int):
    """-> (abs, bench, rel, ratio_chg) for horizon h. abs/rel/ratio_chg are dates x sectors, bench is a Series."""
    L, b = common_sessions(levels, bench)
    a, br = horizon_return(L, h), horizon_return(b, h)
    ratio = L.div(b, axis=0)
    return a, br, a.sub(br, axis=0), horizon_return(ratio, h)


def rs_table(levels: pd.DataFrame, bench: pd.Series, horizons=HORIZONS) -> pd.DataFrame:
    """Latest-session table, one row per sector: abs_h, bench_h, rel_h, ratio_chg_h, pctile_h, n_ranked_h."""
    out = pd.DataFrame(index=levels.columns)
    for h in horizons:
        a, br, rel, rc = relative_panels(levels, bench, h)
        if len(a) == 0:
            for k in ("abs", "bench", "rel", "ratio_chg", "pctile"):
                out[f"{k}_{h}"] = np.nan
            out[f"n_ranked_{h}"] = 0
            continue
        out[f"abs_{h}"] = a.iloc[-1]
        out[f"bench_{h}"] = float(br.iloc[-1]) if pd.notna(br.iloc[-1]) else np.nan
        out[f"rel_{h}"] = rel.iloc[-1]
        out[f"ratio_chg_{h}"] = rc.iloc[-1]
        out[f"pctile_{h}"] = rel.iloc[-1].rank(pct=True) * 100
        out[f"n_ranked_{h}"] = int(rel.iloc[-1].notna().sum())
    out.index.name = "sector"
    return out


def rank_panel(levels: pd.DataFrame, bench: pd.Series, h: int = 20) -> pd.DataFrame:
    """Daily percentile rank (100 = strongest) of rel_h across the whole universe."""
    _, _, rel, _ = relative_panels(levels, bench, h)
    return rel.rank(axis=1, pct=True) * 100


def rs_dynamics(levels: pd.DataFrame, bench: pd.Series, h: int = 20, lag: int = 20, trend_span: int = 50) -> pd.DataFrame:
    """Latest-session momentum, acceleration, trend and rank change, one row per sector."""
    L, b = common_sessions(levels, bench)
    ratio = L.div(b, axis=0)
    mom = horizon_return(ratio, h)
    accel = mom - mom.shift(lag)
    sma = ratio.rolling(trend_span, min_periods=trend_span).mean()
    slope = sma - sma.shift(lag)
    rank = rank_panel(levels, bench, h)
    out = pd.DataFrame(index=levels.columns)
    last = lambda df: df.iloc[-1] if len(df) else pd.Series(np.nan, index=levels.columns)
    out["rs_mom"], out["rs_accel"] = last(mom), last(accel)
    out["ratio_vs_sma_pct"] = (last(ratio) / last(sma) - 1) * 100
    r, s, sl = last(ratio), last(sma), last(slope)
    trend = []
    for c in levels.columns:
        if pd.isna(r[c]) or pd.isna(s[c]) or pd.isna(sl[c]):
            trend.append(None)
        elif r[c] > s[c] and sl[c] > 0:
            trend.append("up")
        elif r[c] < s[c] and sl[c] < 0:
            trend.append("down")
        else:
            trend.append("mixed")
    out["rs_trend"] = trend
    out["pctile_now"] = last(rank)
    out["pctile_lag"] = rank.iloc[-1 - lag] if len(rank) > lag else np.nan
    out["pctile_change"] = out["pctile_now"] - out["pctile_lag"]
    out.index.name = "sector"
    return out


def leadership_consistency(levels: pd.DataFrame, bench: pd.Series, h: int = 20, window: int = 60,
                           top_pctile: float = LEADER_PCTILE) -> pd.Series:
    """Share of the last `window` sessions a sector spent in the top third by rel_h. NaN without a full window."""
    rank = rank_panel(levels, bench, h).tail(window)
    full = rank.notna().sum() >= window
    return (rank >= top_pctile).mean().where(full)


def leadership_status(pctile_now, pctile_lag, consistency, top: float = LEADER_PCTILE,
                      persist: float = LEADER_CONSISTENCY, move: float = RANK_MOVE):
    """
    Plain description of rank behaviour (PROVISIONAL cut-offs, see config.py). Returns None when inputs are missing.
      persistent leadership  top third now and at the lag, and in the top third on >= 60% of the last 60 sessions
      new leadership         top third now, not at the lag
      weakening leadership   top third now, but rank fell >= 15 points
      leadership             top third now (none of the above)
      lost leadership        top third at the lag, not now
      recovering             bottom third at the lag, out of it now
      improving              rank rose >= 15 points (not yet top third)
      no leadership          everything else
    """
    vals = (pctile_now, pctile_lag)
    if any(v is None or pd.isna(v) for v in vals):
        return None
    now_top, lag_top = pctile_now >= top, pctile_lag >= top
    chg = pctile_now - pctile_lag
    if now_top and not lag_top:
        return "new leadership"
    if now_top and chg <= -move:
        return "weakening leadership"
    if now_top and consistency is not None and pd.notna(consistency) and consistency >= persist:
        return "persistent leadership"
    if now_top:
        return "leadership"
    if lag_top:
        return "lost leadership"
    if pctile_lag <= 100 - top and pctile_now > 100 - top:
        return "recovering"
    if chg >= move:
        return "improving"
    return "no leadership"
