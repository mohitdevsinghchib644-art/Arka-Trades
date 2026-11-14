"""
Sector rotation state machine. Rules are written out here in full; nothing is a hidden score.

MEASUREMENTS (each is true / false / missing for a sector on a date; b = benchmark)
  rel20_pos       20-session return minus b's is above 0
  rel60_pos       60-session return minus b's is above 0
  rs_trend_up     sector/b ratio is above its 50-session average AND that average is higher than 20 sessions ago
  rs_trend_down   ratio below its 50-session average AND that average is lower than 20 sessions ago
  rs_turn         ratio above its 20-session EMA AND ratio higher than 10 sessions ago
  price_up        sector level above its own 50-session EMA
  broad / weak    60% or more / 40% or fewer of members above their 50-EMA
  b_imp           members above the 20-EMA up by 2+ stocks in 10 sessions, or above the 50-EMA up by 2+ in 20
  b_det           members above the 50-EMA down by 2+ stocks in 20 sessions
  extended        distance from the 50-EMA in the top 10% of the sector's own last 252 sessions
  decel           20-session ratio change is lower than it was 20 sessions ago
  lows_falling    share of members at a 20-session low is lower than 10 sessions ago
  act_up          more than half of 5-session traded value was in members that closed up

STATES (first match wins, in this order)
  MATURE LEADERSHIP            rel60_pos and price_up and not rs_trend_down, and at least two of:
                               extended, decel, b_det, rel20 not positive
  LEADERSHIP                   rel60_pos and price_up and not rs_trend_down and not weak
  DETERIORATION                rel20 not positive and rs_trend_down and (b_det or weak or price not up)
  PARTICIPATION CONFIRMATION   rel60 not positive, rs_turn, price_up, rel20_pos, b_imp, not weak
  RECOVERY                     rel60 not positive, rs_turn, price NOT up, and (b_imp or lows_falling; breadth may be missing)
  EARLY IMPROVEMENT            rel60 not positive, rs_turn, price_up, and (b_imp; breadth may be missing)
  NO CLEAR STATE               none of the above
  INSUFFICIENT HISTORY         rel_60 or the 50-EMA cannot be computed yet

A sector that is extended or slowing while still ahead over 60 sessions is MATURE LEADERSHIP, never
"deterioration". Losing 20-session relative strength alone does not end leadership.

PERSISTENCE  a new state is shown only after it has held for PERSIST consecutive completed sessions.

Every number above (60/40, 2 stocks, top 10%, PERSIST) is PROVISIONAL. The SIGNAL TESTS screen shows what
each state was followed by on your own loaded history. Treat a state as a description, not an instruction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BREADTH_BROAD, BREADTH_NARROW, EXTENDED_PCTILE, MIN_BREADTH_MOVE_STOCKS

PERSIST = 3
STATES = ("EARLY IMPROVEMENT", "PARTICIPATION CONFIRMATION", "LEADERSHIP", "MATURE LEADERSHIP", "DETERIORATION", "RECOVERY")
NONE, NOHIST = "NO CLEAR STATE", "INSUFFICIENT HISTORY"
ALL_STATES = STATES + (NONE,)

# state -> {measurement: expected value}. Used only to sort evidence into supporting / contradicting / missing.
EXPECT = {
    "LEADERSHIP": {"rel60_pos": 1, "rel20_pos": 1, "rs_trend_up": 1, "price_up": 1, "broad": 1, "b_det": 0},
    "MATURE LEADERSHIP": {"rel60_pos": 1, "price_up": 1, "extended": 1, "decel": 1, "b_det": 1, "rel20_pos": 0},
    "DETERIORATION": {"rel20_pos": 0, "rs_trend_down": 1, "b_det": 1, "weak": 1, "price_up": 0, "act_up": 0},
    "RECOVERY": {"rs_turn": 1, "price_up": 0, "b_imp": 1, "lows_falling": 1, "rel60_pos": 0},
    "EARLY IMPROVEMENT": {"rs_turn": 1, "rel60_pos": 0, "price_up": 1, "b_imp": 1, "act_up": 1},
    "PARTICIPATION CONFIRMATION": {"rel20_pos": 1, "rs_turn": 1, "b_imp": 1, "price_up": 1, "act_up": 1, "weak": 0},
}


def _flag(cond: pd.DataFrame, *needs: pd.DataFrame) -> pd.DataFrame:
    """1.0 / 0.0, NaN wherever any input is missing."""
    ok = needs[0].notna()
    for n in needs[1:]:
        ok &= n.notna()
    return cond.astype(float).where(ok)


def measurements(H: dict) -> dict:
    L, r = H["levels"], H["ratio"]
    d20 = (H["pct20"] - H["pct20"].shift(10)) / 100 * H["n_bar"]
    d50 = (H["pct50"] - H["pct50"].shift(20)) / 100 * H["n_bar"]
    low = H["new_low20"] / H["n_bar"].replace(0, np.nan)
    m = {
        "rel20_pos": _flag(H["rel_20"] > 0, H["rel_20"]),
        "rel60_pos": _flag(H["rel_60"] > 0, H["rel_60"]),
        "rs_trend_up": _flag((r > H["ratio_sma50"]) & (H["rs_sma_slope"] > 0), H["ratio_sma50"], H["rs_sma_slope"]),
        "rs_trend_down": _flag((r < H["ratio_sma50"]) & (H["rs_sma_slope"] < 0), H["ratio_sma50"], H["rs_sma_slope"]),
        "rs_turn": _flag((r > H["ratio_ema20"]) & (H["ratio_chg_10"] > 0), H["ratio_ema20"], H["ratio_chg_10"]),
        "price_up": _flag(L > H["ema50"], H["ema50"], L),
        "broad": _flag(H["pct50"] >= BREADTH_BROAD, H["pct50"]),
        "weak": _flag(H["pct50"] <= BREADTH_NARROW, H["pct50"]),
        "b_imp": _flag((d20 >= MIN_BREADTH_MOVE_STOCKS) | (d50 >= MIN_BREADTH_MOVE_STOCKS), d20, d50),
        "b_det": _flag(d50 <= -MIN_BREADTH_MOVE_STOCKS, d50),
        "extended": _flag(H["ext_pctile"] >= EXTENDED_PCTILE, H["ext_pctile"]),
        "decel": _flag(H["rs_accel"] < 0, H["rs_accel"]),
        "lows_falling": _flag(low < low.shift(10), low, low.shift(10)),
        "act_up": _flag(H["up_share5"] > 50, H["up_share5"]),
    }
    m["_d20_stocks"], m["_d50_stocks"] = d20, d50
    return m


def raw_states(m: dict) -> pd.DataFrame:
    T = lambda k: m[k] == 1
    F = lambda k: m[k] == 0
    NA = lambda k: m[k].isna()
    base = T("rel60_pos") & T("price_up") & ~T("rs_trend_down")
    flags = T("extended").astype(int) + T("decel").astype(int) + T("b_det").astype(int) + F("rel20_pos").astype(int)
    mature = base & (flags >= 2)
    lead = base & ~mature & ~T("weak")
    det = ~base & F("rel20_pos") & T("rs_trend_down") & (T("b_det") | T("weak") | F("price_up"))
    turning = F("rel60_pos") & T("rs_turn")
    part = turning & T("price_up") & T("rel20_pos") & T("b_imp") & ~T("weak")
    recov = turning & F("price_up") & (T("b_imp") | T("lows_falling") | NA("b_imp"))
    early = turning & T("price_up") & ~part & (T("b_imp") | NA("b_imp"))
    ref = m["rel60_pos"]
    out = np.select([mature, lead, det, part, recov, early],
                    ["MATURE LEADERSHIP", "LEADERSHIP", "DETERIORATION", "PARTICIPATION CONFIRMATION", "RECOVERY", "EARLY IMPROVEMENT"],
                    default=NONE)
    out = pd.DataFrame(out, index=ref.index, columns=ref.columns)
    return out.where(m["rel60_pos"].notna() & m["price_up"].notna(), NOHIST)


def persist(raw: pd.DataFrame, k: int = PERSIST) -> pd.DataFrame:
    """Displayed state: changes only after the new raw state has held k sessions in a row."""
    out = raw.copy()
    for c in raw.columns:
        col = raw[c].to_numpy()
        cur, cand, n, res = None, None, 0, []
        for v in col:
            if v == NOHIST:
                res.append(NOHIST if cur is None else cur)
                continue
            if cur is None:
                cur = v
            elif v == cur:
                cand, n = None, 0
            else:
                n = n + 1 if v == cand else 1
                cand = v
                if n >= k:
                    cur, cand, n = v, None, 0
            res.append(cur)
        out[c] = res
    return out


def classify(H: dict, k: int = PERSIST) -> dict:
    m = measurements(H)
    raw = raw_states(m)
    return {"m": m, "raw": raw, "state": persist(raw, k), "persist": k}


def transitions(state: pd.DataFrame) -> pd.DataFrame:
    """Per sector: current state, previous state, date the current state began, sessions in it."""
    rows = []
    for c in state.columns:
        s = state[c]
        cur = s.iloc[-1]
        chg = s.ne(s.shift(1))
        start = chg[chg].index[-1]
        prev = s.loc[:start].iloc[-2] if (s.index.get_loc(start) > 0) else None
        rows.append({"sector": c, "state": cur, "previous": prev if prev not in (None, NOHIST) else "",
                     "since": str(start.date()), "sessions": int((s.index >= start).sum())})
    return pd.DataFrame(rows).set_index("sector")


def _txt(H, m, s, t):
    g = lambda k: H[k].at[t, s] if k in H else np.nan
    f = lambda v, fmt: "n/a" if pd.isna(v) else format(v, fmt)
    n = g("n_bar")
    nn = "?" if pd.isna(n) else str(int(n))
    return {
        "rel20_pos": f"20-session return vs benchmark {f(g('rel_20'), '+.1f')} pp",
        "rel60_pos": f"60-session return vs benchmark {f(g('rel_60'), '+.1f')} pp",
        "rs_trend_up": f"relative-strength line {f((g('ratio') / g('ratio_sma50') - 1) * 100, '+.1f')}% vs its 50-session average, average rising",
        "rs_trend_down": f"relative-strength line {f((g('ratio') / g('ratio_sma50') - 1) * 100, '+.1f')}% vs its 50-session average, average falling",
        "rs_turn": f"relative-strength line above its 20-session EMA and {f(g('ratio_chg_10'), '+.1f')}% over 10 sessions",
        "price_up": f"sector level {f(g('dist50'), '+.1f')}% vs its 50-EMA",
        "broad": f"{f(g('pct50'), '.0f')}% of {nn} stocks above their 50-EMA (broad = 60%+)",
        "weak": f"{f(g('pct50'), '.0f')}% of {nn} stocks above their 50-EMA (weak = 40% or less)",
        "b_imp": f"stocks above 20-EMA {f(m['_d20_stocks'].at[t, s], '+.0f')} in 10 sessions, above 50-EMA {f(m['_d50_stocks'].at[t, s], '+.0f')} in 20",
        "b_det": f"stocks above 50-EMA {f(m['_d50_stocks'].at[t, s], '+.0f')} in 20 sessions",
        "extended": f"distance from 50-EMA at the {f(g('ext_pctile'), '.0f')}th percentile of its own last year",
        "decel": f"relative-strength acceleration {f(g('rs_accel'), '+.1f')} pp",
        "lows_falling": f"{f(g('new_low20'), '.0f')} of {nn} stocks at a 20-session low",
        "act_up": f"{f(g('up_share5'), '.0f')}% of 5-session traded value in up-closing stocks",
    }


def evidence(H: dict, cl: dict, sector: str, t=None) -> dict:
    """Supporting / contradicting / missing measurements for the sector's displayed state, with their numbers."""
    t = t if t is not None else cl["state"].index[-1]
    st = cl["state"].at[t, sector]
    txt = _txt(H, cl["m"], sector, t)
    sup, con, mis = [], [], []
    for k, want in EXPECT.get(st, {}).items():
        v = cl["m"][k].at[t, sector]
        if pd.isna(v):
            mis.append(txt[k].split(" n/a")[0] + ": NOT AVAILABLE" if "n/a" in txt[k] else txt[k] + ": NOT AVAILABLE")
        elif int(v) == want:
            sup.append(txt[k])
        else:
            con.append(txt[k])
    tot = len(sup) + len(con)
    if st in (NONE, NOHIST) or tot == 0:
        conf = "—"
    else:
        share = len(sup) / tot
        conf = "HIGH" if share >= 0.8 and not mis else "MEDIUM" if share >= 0.6 else "LOW"
    return {"state": st, "raw_today": cl["raw"].at[t, sector], "supporting": sup, "contradicting": con, "missing": mis,
            "confidence": conf, "n_support": len(sup), "n_contra": len(con), "n_missing": len(mis)}
