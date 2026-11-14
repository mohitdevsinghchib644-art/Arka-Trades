"""
Engine: turns validated price panels into one evidence table per sector, with every number traceable.

compute_sector_report(...) returns, per sector:
  facts         relative strength (all horizons, vs the chosen benchmark), dynamics, leadership status,
                breadth snapshot, activity snapshot, equal-weight vs index gap, extension percentile
  description   conditions.describe(): situations + contradictions + missing inputs
  trace         for each headline number: function, formula, inputs, source, as-of date, model version
  coverage      members requested / with prices / missing, and whether the sector is INCOMPLETE

There is no action label. Phase 5 (state machine) will be built on these facts and Phase 6 will test it.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .calc import activity, breadth, conditions
from .calc import relative_strength as rs
from .config import MIN_SECTOR_COVERAGE, MODEL_VERSION
from .data.validation import IST

TRACE = {
    "rel_20": ("calc.relative_strength.rs_table", "sector return over 20 sessions minus benchmark return (pp)"),
    "rel_60": ("calc.relative_strength.rs_table", "sector return over 60 sessions minus benchmark return (pp)"),
    "ratio_chg_20": ("calc.relative_strength.rs_table", "(sector/benchmark)[t] / (sector/benchmark)[t-20] - 1 (%)"),
    "pctile_20": ("calc.relative_strength.rs_table", "percentile of rel_20 across all ranked sectors (100 = strongest)"),
    "rs_trend": ("calc.relative_strength.rs_dynamics", "ratio vs its 50-session SMA and that SMA's 20-session slope"),
    "pct_above_ema50": ("calc.breadth.breadth_panel", "members with close > EMA(50) / members with a bar and 50 sessions of history"),
    "up_close_tv_share_5": ("calc.activity.activity_panel", "5-session traded value in up-closing members / all traded value"),
    "ew_minus_index_60": ("engine.compute_sector_report", "equal-weight member basket 60-session return minus official index return (pp)"),
}


def equal_weight_level(close: pd.DataFrame, members) -> pd.Series:
    """Daily-rebalanced equal-weight basket. A member without a bar is left out of that day; nothing is filled."""
    cols = [m for m in members if m in close.columns]
    C = close[cols]
    r = C / C.shift(1) - 1
    daily = r.mean(axis=1, skipna=True)
    valid = r.notna().sum(axis=1) > 0
    return (1 + daily.where(valid, 0.0)).cumprod().where(valid.cummax()) * 100


def extension_percentile(level: pd.Series, span: int = 50, window: int = 252) -> float:
    s = level.dropna()
    if len(s) < window + span:
        return np.nan
    e = s.ewm(span=span, adjust=False, min_periods=span).mean()
    d = (s / e - 1).tail(window)
    return float((d <= d.iloc[-1]).mean() * 100)


def compute_sector_report(levels: pd.DataFrame, bench: pd.Series, bench_name: str, members: dict,
                          close: pd.DataFrame, volume: pd.DataFrame, traded_value: pd.DataFrame | None = None,
                          deliv_qty: pd.DataFrame | None = None, sources: dict | None = None) -> dict:
    """
    levels   dates x sector_id: the level series each sector is measured by (official index, or a basket)
    members  sector_id -> list of stock symbols
    close / volume / traded_value / deliv_qty   dates x stock symbol, NaN where there is no bar
    sources  sector_id -> text describing where `levels` came from (shown in the trace)
    """
    computed_at = datetime.now(IST).isoformat(timespec="seconds")
    table = rs.rs_table(levels, bench)
    dyn = rs.rs_dynamics(levels, bench)
    cons = rs.leadership_consistency(levels, bench)
    L, b = rs.common_sessions(levels, bench)
    asof = str(L.index[-1].date()) if len(L) else None
    out = {"asof": asof, "benchmark": bench_name, "model_version": MODEL_VERSION, "computed_at": computed_at,
           "n_sectors_ranked_20": int(table["n_ranked_20"].iloc[0]) if len(table) else 0, "sectors": {}}
    for sid in levels.columns:
        mem = members.get(sid, [])
        have = [m for m in mem if m in close.columns and close[m].dropna().size > 0]
        cov = len(have) / len(mem) if mem else np.nan
        bsnap = breadth.snapshot(breadth.breadth_panel(close, have)) if have else {}
        asnap = activity.snapshot(activity.activity_panel(close, volume, have, traded_value, deliv_qty)) if have else {}
        ew = equal_weight_level(close, have) if have else None
        ew60 = (float(rs.horizon_return(ew.reindex(L.index), 60).iloc[-1]) if ew is not None and len(L) > 60 else np.nan)
        idx60 = table.at[sid, "abs_60"]
        facts = {**table.loc[sid].to_dict(), **dyn.loc[sid].to_dict(),
                 "consistency_60": float(cons.get(sid, np.nan)) if pd.notna(cons.get(sid, np.nan)) else np.nan,
                 "ew_return_60": ew60, "ew_minus_index_60": (ew60 - idx60) if pd.notna(ew60) and pd.notna(idx60) else np.nan,
                 "ext_pctile": extension_percentile(levels[sid]),
                 **{f"breadth_{k}": v for k, v in bsnap.items()}, **{f"act_{k}": v for k, v in asnap.items()}}
        facts["leadership"] = rs.leadership_status(facts.get("pctile_now"), facts.get("pctile_lag"), facts.get("consistency_60"))
        stocks = bsnap.get("n_with_bar")
        desc = conditions.describe({
            "abs_20": facts.get("abs_20"), "abs_60": facts.get("abs_60"), "rel_20": facts.get("rel_20"),
            "rel_60": facts.get("rel_60"), "bench": bench_name, "pct_above_ema50": bsnap.get("pct_above_ema50"),
            "breadth_chg20_stocks": bsnap.get("pct_above_ema50_chg20_stocks"), "n_stocks": stocks,
            "ext_pctile": facts["ext_pctile"], "ew_minus_index_60": facts["ew_minus_index_60"]})
        src = (sources or {}).get(sid, "unspecified")
        out["sectors"][sid] = {
            "facts": facts, "description": desc,
            "coverage": {"members": len(mem), "with_prices": len(have), "missing": [m for m in mem if m not in have],
                         "share": cov, "status": "INCOMPLETE" if (pd.notna(cov) and cov < MIN_SECTOR_COVERAGE) else "ok"},
            "trace": {k: {"function": f, "formula": fm, "value": facts.get(k, facts.get("breadth_" + k, facts.get("act_" + k))),
                          "level_source": src, "benchmark": bench_name, "asof": asof, "model_version": MODEL_VERSION}
                      for k, (f, fm) in TRACE.items()},
            "activity_value_basis": asnap.get("value_basis", "NOT AVAILABLE"),
        }
    return out
