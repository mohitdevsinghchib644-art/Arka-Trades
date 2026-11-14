"""
Trading activity. Pure functions. These measure HOW MUCH traded and on which kind of day.
They do not show who bought or sold, or why.

Inputs: dates x stocks panels, NaN where there is no bar. `traded_value` should be the exchange-reported
rupee value (NSE bhavcopy). If it is not supplied, close x volume is used and `value_basis` says so.
`deliv_qty` is optional; without it every delivery field is NaN (NOT AVAILABLE), never zero.

Per session, for one sector
  traded_value            sum of members' traded value
  traded_qty              sum of members' traded quantity
  tv_5 / tv_20            mean traded value over the last 5 / 20 sessions
  relative_activity       traded_value / mean of the PREVIOUS 20 sessions (today excluded from its own baseline)
  turnover_accel          tv_5 / tv_20
  up_close_tv_share       traded value in stocks that closed above their previous close / traded_value   (1 session)
  down_close_tv_share     same for stocks that closed lower
  up_close_tv_share_5     the same shares over 5 sessions (sum over sum)
  deliverable_qty_ratio   sum of deliverable quantity / sum of traded quantity (members with delivery data)
  n_up_price_up_deliv     members up on the day whose own delivery ratio is above its previous-20 mean
  n_down_price_high_vol   members down on the day whose volume is above its previous-20 mean

cmf_standard(): conventional Chaikin Money Flow for single stocks,
  CMF_n = sum_n( ((C - L) - (H - C)) / (H - L) * V ) / sum_n(V),  multiplier taken as 0 when H == L.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def activity_panel(close: pd.DataFrame, volume: pd.DataFrame, members, traded_value: pd.DataFrame | None = None,
                   deliv_qty: pd.DataFrame | None = None) -> pd.DataFrame:
    cols = [m for m in members if m in close.columns and m in volume.columns]
    C, V = close[cols].astype(float), volume[cols].astype(float)
    if traded_value is not None:
        TV = traded_value.reindex(index=C.index, columns=cols).astype(float)
        basis = "exchange-reported"
    else:
        TV = C * V
        basis = "approximate: close x volume"
    prev = C.shift(1)
    ok = C.notna() & prev.notna() & TV.notna()
    out = pd.DataFrame(index=C.index)
    tv = TV.sum(axis=1, min_count=1)
    out["traded_value"], out["traded_qty"] = tv, V.sum(axis=1, min_count=1)
    out["tv_5"] = tv.rolling(5, min_periods=5).mean()
    out["tv_20"] = tv.rolling(20, min_periods=20).mean()
    out["relative_activity"] = tv / tv.shift(1).rolling(20, min_periods=20).mean()
    out["turnover_accel"] = out["tv_5"] / out["tv_20"]

    base = TV.where(ok).sum(axis=1, min_count=1)          # value in stocks whose direction is known
    up = TV.where(ok & (C > prev), 0.0).where(ok).sum(axis=1, min_count=1)
    dn = TV.where(ok & (C < prev), 0.0).where(ok).sum(axis=1, min_count=1)
    out["up_close_tv_share"] = up / base.replace(0, np.nan) * 100
    out["down_close_tv_share"] = dn / base.replace(0, np.nan) * 100
    b5 = base.rolling(5, min_periods=5).sum().replace(0, np.nan)
    out["up_close_tv_share_5"] = up.rolling(5, min_periods=5).sum() / b5 * 100
    out["down_close_tv_share_5"] = dn.rolling(5, min_periods=5).sum() / b5 * 100

    vavg = V.shift(1).rolling(20, min_periods=20).mean()
    out["n_down_price_high_vol"] = ((C < prev) & (V > vavg) & ok & vavg.notna()).sum(axis=1)
    if deliv_qty is not None:
        D = deliv_qty.reindex(index=C.index, columns=cols).astype(float)
        have = D.notna() & V.notna()
        out["deliverable_qty_ratio"] = D.where(have).sum(axis=1, min_count=1) / V.where(have).sum(axis=1, min_count=1).replace(0, np.nan) * 100
        out["n_with_delivery"] = have.sum(axis=1)
        dr = D / V.replace(0, np.nan)
        dravg = dr.shift(1).rolling(20, min_periods=20).mean()
        out["n_up_price_up_deliv"] = ((C > prev) & (dr > dravg) & ok & dravg.notna()).sum(axis=1).astype(float)
    else:
        out["deliverable_qty_ratio"] = np.nan
        out["n_with_delivery"] = 0
        out["n_up_price_up_deliv"] = np.nan
    out.attrs["value_basis"] = basis
    return out


def snapshot(panel: pd.DataFrame) -> dict:
    if len(panel) == 0:
        return {}
    last = panel.iloc[-1]
    out = {k: (float(last[k]) if pd.notna(last[k]) else np.nan) for k in panel.columns}
    out["value_basis"] = panel.attrs.get("value_basis", "unknown")
    return out


def cmf_standard(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Conventional Chaikin Money Flow per stock. NaN until n sessions with complete bars exist."""
    rng = high - low
    mfm = (((close - low) - (high - close)) / rng.where(rng > 0)).where(rng != 0, 0.0)
    mfv = mfm * volume
    return mfv.rolling(n, min_periods=n).sum() / volume.rolling(n, min_periods=n).sum().replace(0, np.nan)
