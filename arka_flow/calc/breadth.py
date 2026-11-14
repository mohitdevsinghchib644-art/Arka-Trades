"""
Breadth from constituent closes. Pure functions.

Input `close`: dates x stocks, NaN where a stock has no bar that day (never forward-filled).

Per session, for one sector (n = stocks with a bar AND enough history for that measure):
  pct_above_ema{N}   stocks with close > EMA(N) / n * 100          N in 10, 20, 50, 100, 200
  pct_above_sma{N}   stocks with close > SMA(N) / n * 100          N in 20, 50, 200
  adv / dec / unch   stocks whose close is above / below / equal to their previous bar
  ad_ratio           adv / (adv + dec)
  new_high_{W}       stocks whose close is the highest close of the last W sessions   W in 20, 252
  new_low_{W}        stocks whose close is the lowest close of the last W sessions
  breakout_20        stocks closing above the highest close of the PREVIOUS 20 sessions
  breakdown_20       stocks closing below the lowest close of the PREVIOUS 20 sessions
  thrust             Zweig-style: 10-session EMA of ad_ratio above 0.615 after being below 0.40 within
                     the previous 10 sessions. Defined for whole markets; at sector level it is unvalidated.
  n_with_bar, eligible_{measure}    the denominators, always returned so a % is never read without its n

Level and direction are separate: snapshot() returns each % with its change over 1 / 5 / 10 / 20 sessions,
in points and in number of stocks, plus the size of one stock in points.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import BREADTH_DELTAS, EMA_SPANS, SMA_SPANS


def breadth_panel(close: pd.DataFrame, members) -> pd.DataFrame:
    cols = [m for m in members if m in close.columns]
    C = close[cols].astype(float)
    has = C.notna()
    out = pd.DataFrame(index=C.index)
    out["n_members"] = len(list(members))
    out["n_with_bar"] = has.sum(axis=1)

    def pct(flag, ok, name):
        n = ok.sum(axis=1)
        out[f"eligible_{name}"] = n
        out[f"pct_{name}"] = (flag & ok).sum(axis=1) / n.replace(0, np.nan) * 100

    for n in EMA_SPANS:
        e = C.ewm(span=n, adjust=False, min_periods=n).mean()
        pct(C > e, has & e.notna(), f"above_ema{n}")
    for n in SMA_SPANS:
        s = C.rolling(n, min_periods=n).mean()
        pct(C > s, has & s.notna(), f"above_sma{n}")

    prev = C.shift(1)
    ok = has & prev.notna()
    out["adv"] = ((C > prev) & ok).sum(axis=1)
    out["dec"] = ((C < prev) & ok).sum(axis=1)
    out["unch"] = ((C == prev) & ok).sum(axis=1)
    out["ad_ratio"] = out["adv"] / (out["adv"] + out["dec"]).replace(0, np.nan)

    for w in (20, 252):
        hi = C.rolling(w, min_periods=w).max()
        lo = C.rolling(w, min_periods=w).min()
        out[f"eligible_{w}"] = (has & hi.notna()).sum(axis=1)
        out[f"new_high_{w}"] = ((C >= hi) & has & hi.notna()).sum(axis=1)
        out[f"new_low_{w}"] = ((C <= lo) & has & lo.notna()).sum(axis=1)
    phi = C.rolling(20, min_periods=20).max().shift(1)
    plo = C.rolling(20, min_periods=20).min().shift(1)
    out["breakout_20"] = ((C > phi) & has & phi.notna()).sum(axis=1)
    out["breakdown_20"] = ((C < plo) & has & plo.notna()).sum(axis=1)

    ema10 = out["ad_ratio"].ewm(span=10, adjust=False, min_periods=10).mean()
    out["ad_ratio_ema10"] = ema10
    out["thrust"] = (ema10 > 0.615) & (ema10.shift(1).rolling(10, min_periods=10).min() < 0.40)
    return out


def snapshot(panel: pd.DataFrame, deltas=BREADTH_DELTAS) -> dict:
    """Latest level + change over each delta for every pct_ column. NaN where history is too short."""
    if len(panel) == 0:
        return {}
    last = panel.iloc[-1]
    out = {"n_members": int(last["n_members"]), "n_with_bar": int(last["n_with_bar"]),
           "one_stock_points": (100.0 / last["n_with_bar"]) if last["n_with_bar"] else np.nan}
    for c in panel.columns:
        if c.startswith("pct_"):
            out[c] = float(last[c]) if pd.notna(last[c]) else np.nan
            n = last.get("eligible_" + c[4:], np.nan)
            for k in deltas:
                d = float(last[c] - panel[c].iloc[-1 - k]) if len(panel) > k and pd.notna(panel[c].iloc[-1 - k]) and pd.notna(last[c]) else np.nan
                out[f"{c}_chg{k}"] = d
                out[f"{c}_chg{k}_stocks"] = d / 100 * n if pd.notna(d) and pd.notna(n) else np.nan
    for c in ("adv", "dec", "unch", "new_high_20", "new_low_20", "new_high_252", "new_low_252",
              "breakout_20", "breakdown_20", "eligible_20", "eligible_252"):
        out[c] = int(last[c])
    out["ad_ratio"] = float(last["ad_ratio"]) if pd.notna(last["ad_ratio"]) else np.nan
    out["thrust"] = bool(last["thrust"])
    return out
