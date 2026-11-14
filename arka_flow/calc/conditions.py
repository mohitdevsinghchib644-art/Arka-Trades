"""
Descriptive conditions and contradictions for one sector. NOT trade signals.

describe(facts) turns measurements into plain statements, each carrying the numbers it rests on.
It never says buy, sell, avoid, exit, fatigue or distribution. Conflicting evidence is listed, not hidden.

Definitions (b = the benchmark named in `facts`)
  price        rising   abs_20 > 0 and abs_60 > 0
               falling  abs_20 < 0 and abs_60 < 0
               mixed    anything else
  relative     outperforming            rel_20 > 0 and rel_60 > 0
               losing relative strength rel_60 > 0 but rel_20 <= 0
               relative strength improving  rel_20 > 0 but rel_60 <= 0
               underperforming          rel_20 <= 0 and rel_60 <= 0
  breadth level      broad >= 60% above the 50-EMA, narrow <= 40%   (PROVISIONAL)
  breadth direction  change in the number of stocks above the 50-EMA over 20 sessions:
                     improving >= +2 stocks, deteriorating <= -2 stocks, steady otherwise   (PROVISIONAL)
  extended     distance from the 50-EMA is in the top 10% of the sector's own last 252 sessions   (PROVISIONAL)

A sector can be extended without being weak, can lose relative strength without falling, and can rise
while breadth deteriorates. These are reported as separate situations.
"""
from __future__ import annotations

import math

from ..config import BREADTH_BROAD, BREADTH_NARROW, EXTENDED_PCTILE, MIN_BREADTH_MOVE_STOCKS

REQUIRED = ("abs_20", "abs_60", "rel_20", "rel_60")


def _ok(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def describe(f: dict) -> dict:
    """
    f: abs_20, abs_60, rel_20, rel_60, bench (name), pct_above_ema50, breadth_chg20_stocks, n_stocks,
       ext_pctile, ew_minus_index_60, new_low_20, new_high_20.  Missing keys are allowed.
    -> {"price", "relative", "breadth_level", "breadth_direction", "extended",
        "situations": [{"name", "because"}], "contradictions": [{"name", "numbers"}], "missing": [...]}
    """
    g = lambda k: f.get(k) if _ok(f.get(k)) else None
    bench = f.get("bench", "benchmark")
    out = {"price": None, "relative": None, "breadth_level": None, "breadth_direction": None, "extended": None,
           "situations": [], "contradictions": [], "missing": [k for k in REQUIRED if g(k) is None]}
    a20, a60, r20, r60 = g("abs_20"), g("abs_60"), g("rel_20"), g("rel_60")
    p50, dstk, n = g("pct_above_ema50"), g("breadth_chg20_stocks"), g("n_stocks")
    ext, ewx = g("ext_pctile"), g("ew_minus_index_60")
    if p50 is None:
        out["missing"].append("pct_above_ema50")
    if dstk is None:
        out["missing"].append("breadth_chg20_stocks")

    if a20 is not None and a60 is not None:
        out["price"] = "rising" if a20 > 0 and a60 > 0 else "falling" if a20 < 0 and a60 < 0 else "mixed"
    if r20 is not None and r60 is not None:
        out["relative"] = ("outperforming" if r20 > 0 and r60 > 0 else "losing relative strength" if r60 > 0
                           else "relative strength improving" if r20 > 0 else "underperforming")
    if p50 is not None:
        out["breadth_level"] = "broad" if p50 >= BREADTH_BROAD else "narrow" if p50 <= BREADTH_NARROW else "middling"
    if dstk is not None:
        out["breadth_direction"] = ("improving" if dstk >= MIN_BREADTH_MOVE_STOCKS else
                                    "deteriorating" if dstk <= -MIN_BREADTH_MOVE_STOCKS else "steady")
    if ext is not None:
        out["extended"] = bool(ext >= EXTENDED_PCTILE)

    price, rel, bl, bd = out["price"], out["relative"], out["breadth_level"], out["breadth_direction"]
    px = f"20-session return {a20:+.1f}%, 60-session {a60:+.1f}%" if price else ""
    rs = f"vs {bench}: {r20:+.1f} pp over 20 sessions, {r60:+.1f} pp over 60" if rel else ""
    br = (f"{p50:.0f}% of {int(n) if n else '?'} stocks above the 50-EMA" if p50 is not None else "") + \
         (f", {dstk:+.0f} stocks in 20 sessions" if dstk is not None else "")
    add = lambda name, *why: out["situations"].append({"name": name, "because": "; ".join(w for w in why if w)})

    if price == "rising":
        if rel == "outperforming" and bl == "broad" and bd != "deteriorating":
            add("Rising with strong and broad participation", px, rs, br)
        elif rel == "outperforming":
            add("Rising and strengthening", px, rs)
        if rel in ("losing relative strength", "underperforming"):
            add("Rising but losing relative strength", px, rs)
        if bd == "deteriorating":
            add("Rising but breadth is deteriorating", px, br)
        if out["extended"] and rel == "outperforming":
            add("Extended but still strongly trending", px, rs,
                f"distance from the 50-EMA is at the {ext:.0f}th percentile of its own last year")
    elif price == "mixed":
        if rel in ("outperforming", "relative strength improving"):
            add("Consolidating while relative strength improves", px, rs)
    elif price == "falling":
        if rel == "relative strength improving" or bd == "improving":
            add("Falling but showing early improvement", px, rs, br)
        elif rel == "underperforming" and bd != "improving":
            add("Falling and deteriorating further", px, rs, br)
    if not out["situations"] and price:
        add("No defined situation", px, rs, br)

    con = lambda name, numbers: out["contradictions"].append({"name": name, "numbers": numbers})
    if price == "rising" and r20 is not None and r20 <= 0:
        con("Price rising, relative strength falling", f"20-session return {a20:+.1f}% but {r20:+.1f} pp vs {bench}")
    if price == "rising" and bd == "deteriorating":
        con("Price rising, breadth falling", f"60-session return {a60:+.1f}% while {dstk:+.0f} stocks moved relative to their 50-EMA in 20 sessions")
    if price == "falling" and bd == "improving":
        con("Price falling, breadth improving", f"60-session return {a60:+.1f}% while {dstk:+.0f} stocks reclaimed their 50-EMA in 20 sessions")
    # the sector's level rose, but an equal-weight basket of its members kept less than half of that gain (PROVISIONAL)
    if ewx is not None and a60 is not None and a60 > 0 and (a60 + ewx) < 0.5 * a60:
        con("Strong index performance, weak equal-weight performance",
            f"index {a60:+.1f}% over 60 sessions, equal-weight basket of its members {a60 + ewx:+.1f}% ({ewx:+.1f} pp gap)")
    return out
