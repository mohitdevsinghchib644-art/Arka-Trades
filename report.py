"""
Latest-session tables built from the history panels: the sector ranking table, the constituent table,
and the structured payload for the AI interpretation layer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import state as st
from .calc import conditions
from .calc import relative_strength as rs
from .config import MIN_SECTOR_COVERAGE, MODEL_VERSION


def _last(H, k, lag=0):
    df = H[k]
    return df.iloc[-1 - lag] if len(df) > lag else pd.Series(np.nan, index=df.columns)


def sector_table(H: dict, cl: dict) -> pd.DataFrame:
    """One row per sector, latest completed session. Every column is a measurement or a stated rule output."""
    tr = st.transitions(cl["state"])
    t = cl["state"].index[-1]
    rows = []
    for s in H["sectors"]:
        ev = st.evidence(H, cl, s, t)
        cov = H["coverage"][s]
        share = cov["with_prices"] / cov["members"] if cov["members"] else np.nan
        g = lambda k, lag=0: _last(H, k, lag)[s]
        ew60 = (H["basket"][s].iloc[-1] / H["basket"][s].iloc[-61] - 1) * 100 if len(H["basket"]) > 60 else np.nan
        ewgap = ew60 - g("abs_60") if pd.notna(ew60) and pd.notna(g("abs_60")) else np.nan
        d50 = cl["m"]["_d50_stocks"].at[t, s]
        desc = conditions.describe({"abs_20": g("abs_20"), "abs_60": g("abs_60"), "rel_20": g("rel_20"), "rel_60": g("rel_60"),
                                    "bench": H.get("bench_name", "benchmark"), "pct_above_ema50": g("pct50"),
                                    "breadth_chg20_stocks": d50, "n_stocks": g("n_bar"), "ext_pctile": g("ext_pctile"),
                                    "ew_minus_index_60": ewgap if abs(ewgap) > 1e-9 else np.nan})
        trend = ("up" if cl["m"]["rs_trend_up"].at[t, s] == 1 else "down" if cl["m"]["rs_trend_down"].at[t, s] == 1
                 else None if pd.isna(cl["m"]["rs_trend_up"].at[t, s]) else "mixed")
        pl = _last(H, "pctile_20", 20)[s]
        rows.append({
            "sector": s, "state": ev["state"], "previous": tr.at[s, "previous"] or None, "since": tr.at[s, "since"],
            "sessions": tr.at[s, "sessions"], "confidence": ev["confidence"], "n_support": ev["n_support"],
            "n_contra": ev["n_contra"], "n_missing": ev["n_missing"], "raw_today": ev["raw_today"],
            "abs_5": g("abs_5"), "abs_20": g("abs_20"), "abs_60": g("abs_60"), "abs_120": g("abs_120"), "abs_252": g("abs_252"),
            "rel_5": g("rel_5"), "rel_20": g("rel_20"), "rel_60": g("rel_60"), "rel_120": g("rel_120"), "rel_252": g("rel_252"),
            "pctile_20": g("pctile_20"), "pctile_chg_20": g("pctile_20") - pl, "pctile_60": g("pctile_60"),
            "rs_trend": trend, "rs_accel": g("rs_accel"),
            "leadership": rs.leadership_status(g("pctile_20"), pl, float((H["pctile_20"][s].tail(60) >= 100 * 2 / 3).mean())
                                               if H["pctile_20"][s].tail(60).notna().sum() >= 60 else np.nan),
            "pct20": g("pct20"), "pct50": g("pct50"), "pct200": g("pct200"), "d50_stocks_20": d50,
            "d20_stocks_10": cl["m"]["_d20_stocks"].at[t, s], "new_high20": g("new_high20"), "new_low20": g("new_low20"),
            "breakout20": g("breakout20"), "adv": g("adv"), "dec": g("dec"), "n_bar": g("n_bar"),
            "up_share5": g("up_share5"), "down_share5": g("down_share5"), "rel_activity": g("rel_activity"),
            "turnover_accel": g("turnover_accel"), "traded_value_cr": g("traded_value") / 1e7,
            "dist50": g("dist50"), "ext_pctile": g("ext_pctile"), "ew_minus_level_60": ewgap,
            "members": cov["members"], "with_prices": cov["with_prices"], "coverage": share,
            "coverage_status": "INCOMPLETE" if pd.notna(share) and share < MIN_SECTOR_COVERAGE else ("NO MEMBERS" if not cov["members"] else "ok"),
            "situations": [x["name"] for x in desc["situations"]], "contradictions": desc["contradictions"],
            "evidence": ev,
        })
    return pd.DataFrame(rows)


def stock_table(close: pd.DataFrame, high: pd.DataFrame, volume: pd.DataFrame, members, bench: pd.Series,
                sector_level: pd.Series) -> pd.DataFrame:
    """Constituents of one sector. Research inputs only: a strong sector does not make every member a good trade."""
    cols = [m for m in members if m in close.columns]
    idx = close.index.intersection(bench.dropna().index)
    C, V, b, sl = close.loc[idx, cols], volume.reindex(idx)[cols], bench.loc[idx], sector_level.reindex(idx)
    r = lambda x, h: (x.iloc[-1] / x.iloc[-1 - h] - 1) * 100 if len(x) > h else np.nan
    ema = {n: C.ewm(span=n, adjust=False, min_periods=n).mean().iloc[-1] for n in (20, 50, 200)}
    hi252 = C.rolling(252, min_periods=120).max().iloc[-1]
    prior20 = C.rolling(20, min_periods=20).max().shift(1)
    broke = (C > prior20) & prior20.notna()
    vavg = V.shift(1).rolling(20, min_periods=20).mean().iloc[-1]
    tv20 = (C * V).tail(20).mean() / 1e7
    vol20 = (C / C.shift(1) - 1).tail(20).std() * 100
    rng20 = (C.tail(20).max() / C.tail(20).min() - 1) * 100
    rows = []
    for s in cols:
        last = C[s].iloc[-1]
        if pd.isna(last):
            rows.append({"symbol": s, "setup": "no bar on the latest session"})
            continue
        a20, a60 = r(C[s], 20), r(C[s], 60)
        ab = {n: (None if pd.isna(ema[n][s]) else bool(last > ema[n][s])) for n in (20, 50, 200)}
        recent = broke[s].tail(10)
        lvl = prior20[s][recent[recent].index[-1]] if recent.any() else np.nan
        if recent.tail(5).any() and last > lvl:
            setup = "Breakout: closed above its prior 20-session high in the last 5 sessions"
        elif recent.any() and last <= lvl:
            setup = "Failed breakout: back below the level it broke in the last 10 sessions"
        elif ab[50] and ab[200] and ab[20] is False:
            setup = "Pullback in an established trend: above 50 and 200-EMA, below 20-EMA"
        elif ab[20] and ab[50] and ab[200] and pd.notna(rng20[s]) and rng20[s] < 8:
            setup = "Tight consolidation: 20-session range under 8%, above all three EMAs"
        elif ab[20] and ab[50] and ab[200]:
            setup = "Momentum continuation: above 20, 50 and 200-EMA"
        elif ab[50] is False and ab[200] is False:
            setup = "Below 50 and 200-EMA"
        else:
            setup = "No defined setup"
        rows.append({"symbol": s, "price": float(last), "ret_20": a20, "ret_60": a60,
                     "rel_bench_20": a20 - r(b, 20), "rel_bench_60": a60 - r(b, 60),
                     "rel_sector_20": a20 - r(sl, 20), "rel_sector_60": a60 - r(sl, 60),
                     "above_20": ab[20], "above_50": ab[50], "above_200": ab[200],
                     "from_52w_high": (last / hi252[s] - 1) * 100 if pd.notna(hi252[s]) else np.nan,
                     "rel_volume": V[s].iloc[-1] / vavg[s] if pd.notna(vavg[s]) and vavg[s] > 0 else np.nan,
                     "avg_value_cr": tv20[s], "volatility_20": vol20[s], "range_20": rng20[s], "setup": setup})
    out = pd.DataFrame(rows)
    return out.sort_values("rel_bench_60", ascending=False, na_position="last").reset_index(drop=True) if "rel_bench_60" in out else out


def ai_payload(row: dict, asof: str, bench: str, source: str) -> dict:
    """Structured, timestamped input for the AI interpretation layer. AI may explain these; it may not add numbers."""
    num = lambda k: None if row.get(k) is None or pd.isna(row.get(k)) else round(float(row[k]), 2)
    facts = {k: num(k) for k in ("abs_20", "abs_60", "rel_20", "rel_60", "rel_120", "pctile_20", "pctile_chg_20", "pct20", "pct50",
                                 "pct200", "d50_stocks_20", "new_high20", "new_low20", "up_share5", "rel_activity", "dist50",
                                 "ext_pctile", "ew_minus_level_60")}
    ev = row["evidence"]
    return {"sector": row["sector"], "asof": asof, "benchmark": bench, "price_source": source, "model_version": MODEL_VERSION,
            "FACT": {"measurements": {k: v for k, v in facts.items() if v is not None}, "state": row["state"],
                     "state_since": row["since"], "previous_state": row["previous"], "rs_trend": row["rs_trend"],
                     "supporting": ev["supporting"], "contradicting": ev["contradicting"],
                     "situations": row["situations"], "contradictions": row["contradictions"],
                     "coverage": f'{row["with_prices"]} of {row["members"]} members with prices'},
            "INFERENCE_RULES": "State and situations follow the written rules in arka_flow/state.py and calc/conditions.py. "
                               "They describe the present. They are not forecasts.",
            "UNKNOWN": [k for k, v in facts.items() if v is None] + ev["missing"] +
                       ["who bought or sold", "delivery participation", "institutional positioning (see FPI view, fortnightly, lagged)",
                        "fundamentals and earnings revisions", "macro drivers"]}


def explain(row: dict, bench: str) -> str:
    """Rule-written explanation (no AI call). Markdown."""
    ev = row["evidence"]
    f = lambda k, fmt="+.1f": "n/a" if row.get(k) is None or pd.isna(row.get(k)) else format(row[k], fmt)
    out = [f"**State: {row['state']}** since {row['since']} ({row['sessions']} sessions)"
           + (f", previously {row['previous']}" if row["previous"] else "") + f". Confidence: {ev['confidence']}."]
    if ev["raw_today"] != row["state"]:
        out.append(f"Today's raw reading is {ev['raw_today']}; the displayed state changes only after 3 sessions in a row.")
    out.append(f"**FACT.** Return {f('abs_20')}% over 20 sessions and {f('abs_60')}% over 60. Versus {bench}: {f('rel_20')} pp and "
               f"{f('rel_60')} pp. {f('pct50', '.0f')}% of {f('n_bar', '.0f')} stocks are above their 50-EMA "
               f"({f('d50_stocks_20', '+.0f')} stocks in 20 sessions).")
    if ev["supporting"]:
        out.append("**Supports the state:** " + "; ".join(ev["supporting"]) + ".")
    if ev["contradicting"]:
        out.append("**Contradicts it:** " + "; ".join(ev["contradicting"]) + ".")
    for c in row["contradictions"]:
        out.append(f"**Contradiction: {c['name']}.** {c['numbers']}.")
    if row["situations"]:
        out.append("**INFERENCE (rule-based description):** " + "; ".join(row["situations"]) + ".")
    unk = ev["missing"] + ["who bought or sold", "delivery participation", "fundamentals"]
    out.append("**UNKNOWN:** " + "; ".join(unk) + ".")
    return "\n\n".join(out)
