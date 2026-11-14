"""
Point-in-time history panels (dates x sectors). Every value at date t uses data up to and including t only.
These panels feed the state machine, the signal tests and the screens, so live labels and tested labels
come from the same numbers.

build() returns a dict H:
  levels, bench, basket            sector level used for measurement, benchmark, equal-weight member basket
  abs_{h}, rel_{h}                 return and relative return (pp) for h in 5,10,20,40,60,120,252
  ratio, ratio_chg_10, ratio_chg_20, rs_accel, ratio_sma50, rs_sma_slope, ratio_ema20
  pctile_20, pctile_60, n_ranked   percentile of rel_h across every sector with a value (100 = strongest)
  ema50, ema200, dist50, ext_pctile
  pct20, pct50, pct200, n_bar, new_low20, new_high20, breakout20, breakdown20, adv, dec     (breadth)
  up_share5, down_share5, rel_activity, turnover_accel, traded_value                      (activity)
  coverage                         dict sector -> {members, with_prices, missing}
  breadth_panels, activity_panels  dict sector -> full per-sector panel (for the detail screen)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calc import activity, breadth
from .config import HORIZONS


def _ret(x, h):
    return (x / x.shift(h) - 1) * 100


def equal_weight_level(close: pd.DataFrame, members) -> pd.Series:
    """Daily-rebalanced equal-weight basket. A member without a bar is left out that day. Nothing is filled."""
    cols = [m for m in members if m in close.columns]
    if not cols:
        return pd.Series(np.nan, index=close.index)
    C = close[cols]
    r = C / C.shift(1) - 1
    valid = r.notna().sum(axis=1) > 0
    return (1 + r.mean(axis=1, skipna=True).where(valid, 0.0)).cumprod().where(valid.cummax()) * 100


def build(close: pd.DataFrame, volume: pd.DataFrame, members: dict, bench: pd.Series,
          levels: pd.DataFrame | None = None, traded_value: pd.DataFrame | None = None) -> dict:
    b = bench.dropna()
    idx = close.index.intersection(b.index)
    if len(idx) < 60:
        raise ValueError(f"only {len(idx)} common sessions between prices and benchmark")
    C, V, b = close.loc[idx], volume.reindex(idx), b.loc[idx]
    TVp = traded_value.reindex(idx) if traded_value is not None else None
    sectors = list(members) if levels is None else list(levels.columns)
    have = {s: [m for m in members.get(s, []) if m in C.columns and C[m].notna().any()] for s in sectors}
    basket = pd.DataFrame({s: equal_weight_level(C, have[s]) for s in sectors}, index=idx)
    L = basket if levels is None else levels.reindex(idx)[sectors]
    L = L.loc[:, L.notna().sum() >= 60]
    sectors = list(L.columns)
    H = {"levels": L, "bench": b, "basket": basket[sectors]}

    for h in HORIZONS:
        H[f"abs_{h}"] = _ret(L, h)
        H[f"rel_{h}"] = H[f"abs_{h}"].sub(_ret(b, h), axis=0)
    ratio = L.div(b, axis=0)
    H["ratio"] = ratio
    H["ratio_chg_10"], H["ratio_chg_20"] = _ret(ratio, 10), _ret(ratio, 20)
    H["rs_accel"] = H["ratio_chg_20"] - H["ratio_chg_20"].shift(20)
    H["ratio_sma50"] = ratio.rolling(50, min_periods=50).mean()
    H["rs_sma_slope"] = H["ratio_sma50"] - H["ratio_sma50"].shift(20)
    H["ratio_ema20"] = ratio.ewm(span=20, adjust=False, min_periods=20).mean()
    for h in (20, 60):
        H[f"pctile_{h}"] = H[f"rel_{h}"].rank(axis=1, pct=True) * 100
    H["n_ranked"] = H["rel_20"].notna().sum(axis=1)
    H["ema50"] = L.ewm(span=50, adjust=False, min_periods=50).mean()
    H["ema200"] = L.ewm(span=200, adjust=False, min_periods=200).mean()
    H["dist50"] = (L / H["ema50"] - 1) * 100
    H["ext_pctile"] = H["dist50"].rolling(252, min_periods=120).rank(pct=True) * 100

    bcols = {"pct20": "pct_above_ema20", "pct50": "pct_above_ema50", "pct200": "pct_above_ema200", "n_bar": "n_with_bar",
             "new_low20": "new_low_20", "new_high20": "new_high_20", "breakout20": "breakout_20",
             "breakdown20": "breakdown_20", "adv": "adv", "dec": "dec"}
    acols = {"up_share5": "up_close_tv_share_5", "down_share5": "down_close_tv_share_5", "rel_activity": "relative_activity",
             "turnover_accel": "turnover_accel", "traded_value": "traded_value"}
    bp, ap = {}, {}
    for s in sectors:
        if have[s]:
            bp[s] = breadth.breadth_panel(C, have[s])
            ap[s] = activity.activity_panel(C, V, have[s], TVp)
    nan = pd.Series(np.nan, index=idx)
    for k, src in bcols.items():
        H[k] = pd.DataFrame({s: (bp[s][src] if s in bp else nan) for s in sectors}, index=idx).astype(float)
    for k, src in acols.items():
        H[k] = pd.DataFrame({s: (ap[s][src] if s in ap else nan) for s in sectors}, index=idx).astype(float)
    H["breadth_panels"], H["activity_panels"] = bp, ap
    H["value_basis"] = "exchange-reported" if traded_value is not None else "approximate: close x volume"
    H["coverage"] = {s: {"members": len(members.get(s, [])), "with_prices": len(have[s]),
                         "missing": [m for m in members.get(s, []) if m not in have[s]]} for s in sectors}
    H["sectors"], H["asof"] = sectors, str(idx[-1].date())
    return H
