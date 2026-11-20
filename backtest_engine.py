"""
Arka Trades — backtest_engine.py (v2): your own rules, on the stocks you choose.

v2
  * RULE BUILDER: a strategy is a list of conditions you pick and tune (trend, breakout, momentum, pullback, candle,
    volume, volatility, market). Nothing is applied that is not on your list, except a one-year warm-up.
  * STOCK PICKER: signals can be limited to the stocks you select; RS rating is still ranked against the whole universe.
  * Entry at the next open, or only if the next day trades above the signal day's high (buy-stop).
  * Stops: signal-day low, lowest low of the last N days, ATR multiple or percent. Trailing exit on any EMA / SMA.
  * Audit fixes: a slot freed by an intraday stop can no longer be reused at that morning's open; trades that cross a
    suspect candle (a one-day move beyond -35% / +50%, usually an unadjusted corporate action) are removed and counted.

Realism rules
  * A signal uses only data up to that day's close. Entry is the NEXT session. Gap-ups bigger than a limit are skipped.
  * Stops are checked before targets inside a day (worst case). A gap below the stop fills at the open, not the stop.
  * Trailing exits are decided on the close and filled at the next open.
  * Round-trip costs (STT, stamp duty, exchange, GST, DP charges) plus slippage are taken off every trade.
  * The portfolio has real constraints: capital, risk per trade, maximum positions, position size and cash. When more
    signals fire than free slots, the highest RS rating is taken first.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import quant_data as qd

COST_DEFAULT = 0.35     # % per round trip: ~0.25% statutory (STT 0.1% x2, stamp, exchange, GST, DP) + ~0.1% slippage
WARMUP = 252
STATES = ["Confirmed uptrend", "Under pressure", "Rally attempt", "Correction"]


# ═════════════════════════ indicators on demand ═════════════════════════
def ma(p: dict, kind: str, n: int) -> np.ndarray:
    n = int(n)
    k = f"{'ema' if kind == 'EMA' else 'sma'}{n}"
    if k not in p:
        c = p["c"].astype(np.float64)
        p[k] = np.asarray(qd.ema(c, n) if kind == "EMA" else qd.sma(c, n), dtype=np.float32)
    return p[k]


def _cached(p, key, fn):
    if key not in p:
        p[key] = np.asarray(fn(), dtype=np.float32)
    return p[key]


def prior_low(p, n):
    return _cached(p, f"pl{int(n)}", lambda: qd.shift(qd.roll_min(p["l"].astype(np.float64), int(n)), 1))


def high_n(p, n):
    return _cached(p, f"hn{int(n)}", lambda: qd.roll_max(p["h"].astype(np.float64), int(n)))


def low_n(p, n):
    """Lowest low of the last n sessions including today (used for stops; a missing candle is simply ignored)."""
    return _cached(p, f"ln{int(n)}", lambda: pd.DataFrame(p["l"].astype(np.float64)).rolling(int(n), min_periods=1).min().to_numpy())


def rsi(p, n):
    return _cached(p, f"rsi{int(n)}", lambda: qd.rsi(p["c"].astype(np.float64), int(n)))


# ═════════════════════════ the rule catalogue ═════════════════════════
# param spec: (key, label, default, min, max, step)   or   (key, label, default, [choices])
K = ("kind", "Average", "EMA", ["EMA", "SMA"])


def _idx_ma(p, kind, n):
    k = f"idx_{kind}{int(n)}"
    if k not in p:
        x = p["idx_c"][:, None].astype(np.float64)
        p[k] = np.asarray((qd.ema(x, int(n)) if kind == "EMA" else qd.sma(x, int(n)))[:, 0], dtype=np.float32)
    return p[k]


RULES = {
    # ── trend ──
    "close_above_ma": ("Trend", "Close above a moving average", [K, ("n", "Length", 50, 2, 400, 1)],
                       lambda p, a, s: p["c"] > ma(p, a["kind"], a["n"])),
    "ma_above_ma": ("Trend", "Fast average above slow average", [K, ("fast", "Fast", 21, 2, 400, 1), ("slow", "Slow", 50, 2, 400, 1)],
                    lambda p, a, s: ma(p, a["kind"], a["fast"]) > ma(p, a["kind"], a["slow"])),
    "ma_rising": ("Trend", "Moving average is rising", [K, ("n", "Length", 50, 2, 400, 1), ("days", "Higher than N days ago", 20, 1, 120, 1)],
                  lambda p, a, s: ma(p, a["kind"], a["n"]) > qd.shift(ma(p, a["kind"], a["n"]), int(a["days"]))),
    "near_52w_high": ("Trend", "Within % of the 52-week high", [("pct", "Within %", 15.0, 0.0, 80.0, 1.0)],
                      lambda p, a, s: p["c"] >= p["hh52"] * (1 - a["pct"] / 100)),
    "above_52w_low": ("Trend", "At least % above the 52-week low", [("pct", "At least %", 30.0, 0.0, 500.0, 5.0)],
                      lambda p, a, s: p["c"] >= p["ll52"] * (1 + a["pct"] / 100)),
    # ── breakout ──
    "breakout_n": ("Breakout", "Close above the highest high of the previous N days", [("n", "N days", 20, 2, 260, 1)],
                   lambda p, a, s: p["c"] > qd.prior_high(p, int(a["n"]))),
    "new_52w_high": ("Breakout", "New 52-week high today", [],
                     lambda p, a, s: p["h"] >= qd.prior_high(p, 252)),
    "close_above_prev_high": ("Breakout", "Close above the previous day's high", [],
                              lambda p, a, s: p["c"] > qd.shift(p["h"], 1)),
    "gap_up": ("Breakout", "Opened with a gap up of at least %", [("pct", "Gap %", 1.0, 0.1, 20.0, 0.25)],
               lambda p, a, s: p["o"] >= qd.shift(p["c"], 1) * (1 + a["pct"] / 100)),
    # ── momentum ──
    "rs_min": ("Momentum", "RS rating at least", [("x", "RS rating", 80, 1, 99, 1)],
               lambda p, a, s: p["rs"] >= a["x"]),
    "ret_n": ("Momentum", "Return over N days at least %", [("n", "N days", 63, 1, 260, 1), ("pct", "Return %", 20.0, -90.0, 500.0, 1.0)],
              lambda p, a, s: (p["c"] / qd.shift(p["c"], int(a["n"])) - 1) * 100 >= a["pct"]),
    "day_change": ("Momentum", "Today's change between % and %", [("lo", "From %", 2.0, -20.0, 20.0, 0.25), ("hi", "To %", 6.0, -20.0, 20.0, 0.25)],
                   lambda p, a, s: _between((p["c"] / qd.shift(p["c"], 1) - 1) * 100, a["lo"], a["hi"])),
    "rsi_above": ("Momentum", "RSI above", [("n", "RSI length", 14, 2, 50, 1), ("x", "Above", 55.0, 1.0, 99.0, 1.0)],
                  lambda p, a, s: rsi(p, a["n"]) > a["x"]),
    "rsi_below": ("Momentum", "RSI below", [("n", "RSI length", 14, 2, 50, 1), ("x", "Below", 75.0, 1.0, 99.0, 1.0)],
                  lambda p, a, s: rsi(p, a["n"]) < a["x"]),
    # ── pullback ──
    "touch_ma": ("Pullback", "Low came within % of a moving average in the last N days",
                 [K, ("n", "Length", 21, 2, 400, 1), ("pct", "Within %", 1.5, 0.0, 10.0, 0.25), ("days", "In the last N days", 3, 1, 20, 1)],
                 lambda p, a, s: qd.roll_min((p["l"] / ma(p, a["kind"], a["n"])).astype(np.float64), int(a["days"])) <= 1 + a["pct"] / 100),
    "pullback_depth": ("Pullback", "Pulled back between % and % from the N-day high",
                       [("n", "N days", 20, 3, 260, 1), ("lo", "At least %", 3.0, 0.0, 60.0, 0.5), ("hi", "At most %", 12.0, 0.0, 80.0, 0.5)],
                       lambda p, a, s: _between((1 - p["c"] / high_n(p, a["n"])) * 100, a["lo"], a["hi"])),
    # ── candle ──
    "green": ("Candle", "Green candle (close above open)", [], lambda p, a, s: p["c"] > p["o"]),
    "close_top": ("Candle", "Close in the top % of the day's range", [("pct", "Top %", 30.0, 1.0, 100.0, 5.0)],
                  lambda p, a, s: (p["c"] - p["l"]) >= (1 - a["pct"] / 100) * (p["h"] - p["l"])),
    "inside_day": ("Candle", "Inside day (range within the previous day's)", [],
                   lambda p, a, s: (p["h"] <= qd.shift(p["h"], 1)) & (p["l"] >= qd.shift(p["l"], 1))),
    "nr7": ("Candle", "Narrowest range of the last 7 days (NR7)", [],
            lambda p, a, s: (p["h"] - p["l"]) <= qd.roll_min((p["h"] - p["l"]).astype(np.float64), 7)),
    # ── volume ──
    "vol_mult": ("Volume", "Volume at least × the 50-day average", [("x", "Times average", 1.5, 0.1, 20.0, 0.1)],
                 lambda p, a, s: p["v"] >= a["x"] * p["vavg50"]),
    "vol_dry": ("Volume", "Volume dried up: at most × average on each of the N days before today",
                [("x", "Times average", 0.8, 0.1, 5.0, 0.05), ("days", "N days", 3, 1, 20, 1)],
                lambda p, a, s: qd.shift(qd.roll_max((p["v"] / p["vavg50"]).astype(np.float64), int(a["days"])), 1) <= a["x"]),
    "turnover_min": ("Volume", "Trades at least ₹ crore a day (20-day average)", [("x", "₹ crore", 5.0, 0.0, 5000.0, 1.0)],
                     lambda p, a, s: p["turn_cr"] >= a["x"]),
    "price_min": ("Volume", "Price at least ₹", [("x", "₹", 20.0, 0.0, 100000.0, 5.0)],
                  lambda p, a, s: p["c"] >= a["x"]),
    # ── volatility / extension ──
    "atr_contract": ("Volatility", "Volatility contracted: ATR 14 ÷ ATR 50 at most (before today)", [("x", "Ratio", 0.80, 0.2, 2.0, 0.05)],
                     lambda p, a, s: qd.shift((p["atr14"] / p["atr50"]).astype(np.float64), 1) <= a["x"]),
    "range_tight": ("Volatility", "Tight base: range of the previous N days at most % of price",
                    [("n", "N days", 15, 3, 120, 1), ("pct", "Range %", 12.0, 1.0, 80.0, 0.5)],
                    lambda p, a, s: (qd.prior_high(p, int(a["n"])) - prior_low(p, a["n"])) <= a["pct"] / 100 * p["c"]),
    "not_extended": ("Volatility", "Not extended: at most % above a moving average", [K, ("n", "Length", 21, 2, 400, 1), ("pct", "At most %", 8.0, 0.0, 100.0, 0.5)],
                     lambda p, a, s: (p["c"] / ma(p, a["kind"], a["n"]) - 1) * 100 <= a["pct"]),
    # ── market ──
    "nifty_above_ma": ("Market", "NIFTY 50 above its moving average", [K, ("n", "Length", 50, 2, 400, 1)],
                       lambda p, a, s: np.broadcast_to((p["idx_c"] > _idx_ma(p, a["kind"], a["n"]))[:, None], p["c"].shape)),
    "regime_in": ("Market", "Market regime is one of", [("states", "States", ["Confirmed uptrend", "Under pressure"], STATES)],
                  lambda p, a, s: np.broadcast_to(np.isin(s, list(a["states"]))[:, None], p["c"].shape) if s is not None
                  else np.ones(p["c"].shape, dtype=bool)),
}


def _between(x, lo, hi):
    return (x >= lo) & (x <= hi)


def new_rule(rtype: str, **over) -> dict:
    r = {"type": rtype}
    for spec in RULES[rtype][2]:
        r[spec[0]] = spec[2] if not isinstance(spec[2], list) else list(spec[2])
    r.update(over)
    return r


def describe(rule: dict) -> str:
    grp, label, specs, _ = RULES[rule["type"]]
    if not specs:
        return label
    vals = ", ".join(f"{sp[1]} {', '.join(rule[sp[0]]) if isinstance(rule[sp[0]], (list, tuple)) else rule[sp[0]]}" for sp in specs)
    return f"{label} ({vals})"


TEMPLATES = {
    "Breakout": [new_rule("breakout_n", n=20), new_rule("vol_mult", x=1.5), new_rule("rs_min", x=80), new_rule("close_above_ma", n=50),
                 new_rule("close_above_ma", kind="SMA", n=200), new_rule("not_extended", n=21, pct=8.0), new_rule("turnover_min", x=5.0)],
    "Pullback to 21 EMA": [new_rule("ma_above_ma", fast=21, slow=50), new_rule("close_above_ma", kind="SMA", n=200), new_rule("near_52w_high", pct=15.0),
                           new_rule("rs_min", x=80), new_rule("touch_ma", n=21, pct=1.5, days=3), new_rule("close_above_ma", n=21),
                           new_rule("green"), new_rule("close_above_prev_high"), new_rule("turnover_min", x=5.0)],
    "52-week high": [new_rule("new_52w_high"), new_rule("vol_mult", x=1.3), new_rule("rs_min", x=85), new_rule("close_above_ma", n=50),
                     new_rule("not_extended", n=21, pct=10.0), new_rule("turnover_min", x=5.0)],
    "Volatility contraction (VCP)": [new_rule("atr_contract", x=0.80), new_rule("range_tight", n=15, pct=12.0), new_rule("breakout_n", n=15),
                                     new_rule("vol_mult", x=1.5), new_rule("rs_min", x=75), new_rule("close_above_ma", n=50),
                                     new_rule("close_above_ma", kind="SMA", n=200), new_rule("turnover_min", x=5.0)],
    "Start from nothing": [],
}


def rule_mask(p: dict, rule: dict, state=None) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        m = RULES[rule["type"]][3](p, rule, state)
    return np.asarray(m, dtype=bool)


def signals(p: dict, rules: list, state=None, cols=None, masks=None) -> np.ndarray:
    """Boolean (T, N): every condition true at that day's close. cols: indices of the stocks to trade (None = all)."""
    sig = ~np.isnan(p["c"])
    for i, r in enumerate(rules):
        sig &= masks[i] if masks is not None else rule_mask(p, r, state)
    sig[:WARMUP] = False                      # 52-week and 200-day measures need a year
    if cols is not None:
        keep = np.zeros(sig.shape[1], dtype=bool)
        keep[list(cols)] = True
        sig[:, ~keep] = False
    return sig


# ═════════════════════════ trades (each signal on its own) ═════════════════════════
EXIT_DEFAULT = {"entry": "Next day's open", "stop": "Signal-day low", "stop_n": 5, "stop_atr": 1.5, "stop_pct": 7.0, "max_risk": 10.0,
                "trail_kind": "EMA", "trail_n": 21, "target_r": 0.0, "max_days": 40, "breakeven_r": 0.0, "gap_max": 4.0}
ENTRIES = ["Next day's open", "Next day, only above the signal day's high"]
STOPS = ["Signal-day low", "Lowest low of the last N days", "ATR multiple", "Percent"]
SUSPECT_DOWN, SUSPECT_UP = -0.35, 0.50


def _ffill(a):
    a = a.copy()
    for i in range(1, len(a)):
        if not np.isfinite(a[i]):
            a[i] = a[i - 1]
    return a


def make_trades(p: dict, sig: np.ndarray, ex: dict, cost_pct: float = COST_DEFAULT) -> pd.DataFrame:
    """Simulate every signal as an independent trade (one open trade per stock at a time)."""
    O, H, L, C = p["o"], p["h"], p["l"], p["c"]
    T, N = C.shape
    trail = int(ex.get("trail_n") or 0)
    ema_t = ma(p, ex.get("trail_kind", "EMA"), trail) if trail else None
    cost = cost_pct / 100.0
    gap_max = ex.get("gap_max", 4.0) / 100.0
    max_days = int(ex.get("max_days") or 60)
    tgt_r = float(ex.get("target_r") or 0)
    be_r = float(ex.get("breakeven_r") or 0)
    max_risk = ex.get("max_risk", 10.0) / 100.0
    buy_stop = ex.get("entry", ENTRIES[0]) == ENTRIES[1]
    stop_low = low_n(p, ex.get("stop_n", 5)) if ex["stop"] == STOPS[1] else None
    rows, dropped = [], 0
    for j in np.flatnonzero(sig.any(0)):
        idxs = np.flatnonzero(sig[:, j])
        o, h, l, c = (_ffill(x[:, j].astype(np.float64)) for x in (O, H, L, C))
        with np.errstate(invalid="ignore", divide="ignore"):
            rr = c[1:] / c[:-1] - 1
        sus = np.r_[0, np.cumsum((rr < SUSPECT_DOWN) | (rr > SUSPECT_UP))]
        et = ema_t[:, j].astype(np.float64) if trail else None
        busy_until = -1
        for t in idxs:
            ei = t + 1
            if ei >= T or ei <= busy_until or not np.isfinite(O[ei, j]):
                continue
            if o[ei] > c[t] * (1 + gap_max):
                continue                                           # chasing a big gap: skip
            if buy_stop:
                trig = h[t]
                if not h[ei] > trig:
                    continue                                       # never traded above the signal day's high
                entry = max(o[ei], trig)
            else:
                entry = o[ei]
            if ex["stop"] == STOPS[0]:
                stop = l[t]
            elif ex["stop"] == STOPS[1]:
                stop = float(stop_low[t, j])
            elif ex["stop"] == STOPS[2]:
                stop = entry - ex.get("stop_atr", 1.5) * float(p["atr14"][t, j])
            else:
                stop = entry * (1 - ex.get("stop_pct", 7.0) / 100)
            if not np.isfinite(stop) or stop >= entry * 0.998:
                continue                                           # opened at / below the stop: no trade
            stop = max(stop, entry * (1 - max_risk))
            risk = entry - stop
            end = min(T - 1, ei + max_days - 1)
            lo, hi, op, cl = l[ei:end + 1], h[ei:end + 1], o[ei:end + 1], c[ei:end + 1]
            n = len(lo)
            cand = []                                               # (offset, priority, price, reason)
            kb = n
            if be_r > 0:
                w = np.flatnonzero(hi >= entry + be_r * risk)
                kb = int(w[0]) if len(w) else n
            s1 = np.flatnonzero(lo <= stop)
            s1 = int(s1[0]) if len(s1) else n
            if s1 <= kb:
                if s1 < n:
                    cand.append((s1, 1, min(op[s1], stop) if s1 > 0 else stop, "Stop"))
            else:
                s2 = np.flatnonzero(lo[kb + 1:] <= entry)
                if len(s2):
                    k = kb + 1 + int(s2[0])
                    cand.append((k, 1, min(op[k], entry), "Breakeven stop"))
            if tgt_r > 0:
                g = np.flatnonzero(hi >= entry + tgt_r * risk)
                if len(g):
                    k = int(g[0])
                    cand.append((k, 2, max(op[k], entry + tgt_r * risk) if k > 0 else entry + tgt_r * risk, "Target"))
            if trail:
                tr = np.flatnonzero(cl < et[ei:end + 1])
                if len(tr):
                    k = int(tr[0])
                    nm = f"Close below {trail} {ex.get('trail_kind', 'EMA')}"
                    if ei + k + 1 < T:
                        cand.append((k + 1, 0, o[ei + k + 1], nm))
                    else:
                        cand.append((k, 3, cl[k], nm))
            cand.append((n - 1, 4, cl[n - 1], "Time stop" if end < T - 1 else "Open at end"))
            k, pri, px, why = min(cand, key=lambda x: (x[0], x[1]))
            xi = ei + k
            if sus[xi] - sus[t] > 0:
                dropped += 1                                       # a suspect candle inside the trade: do not trust it
                busy_until = xi
                continue
            last = xi - 1 if (pri == 0 and xi > ei) else xi                # exited at the open: that day's range never happened to us
            seg_lo, seg_hi = l[ei:last + 1], h[ei:last + 1]
            r_g = (px - entry) / risk
            mae = (seg_lo.min() - entry) / risk if len(seg_lo) else min(0.0, r_g)
            mfe = (seg_hi.max() - entry) / risk if len(seg_hi) else max(0.0, r_g)
            if "top" in why:
                mae = max(mae, r_g)
            mae, mfe = min(mae, r_g, 0.0), max(mfe, r_g, 0.0)
            r_net = (px - entry - cost * entry) / risk
            rows.append((j, t, ei, xi, entry, stop, px, why, r_g, r_net, (px / entry - 1 - cost) * 100, xi - ei + 1, mae, mfe,
                         float(p["rs"][t, j]), float((C[t, j] / p["ema21"][t, j] - 1) * 100), risk / entry * 100, pri == 0))
            busy_until = xi
    df = pd.DataFrame(rows, columns=["j", "sig_i", "entry_i", "exit_i", "entry", "stop", "exit", "reason", "r_gross", "r", "ret_pct",
                                     "days", "mae", "mfe", "rs", "ext21", "risk_pct", "exit_open"])
    if not df.empty:
        df["symbol"] = [p["symbols"][j] for j in df["j"]]
        df["sector"] = [p["industry"][j] for j in df["j"]]
        df["entry_date"] = p["dates"][df["entry_i"].to_numpy()]
        df["exit_date"] = p["dates"][df["exit_i"].to_numpy()]
        df["open_at_end"] = df["reason"] == "Open at end"
    df.attrs["dropped_suspect"] = dropped
    return df


# ═════════════════════════ portfolio ═════════════════════════
EXPOSURE = {"Confirmed uptrend": 1.0, "Under pressure": 0.5, "Rally attempt": 0.25, "Correction": 0.0}


@dataclass
class Book:
    capital: float = 1_000_000.0
    risk_pct: float = 1.0
    max_pos: int = 8
    max_pos_pct: float = 25.0
    regime_mode: str = "Off"            # Off | Skip corrections | Scale by regime


def portfolio(p: dict, trades: pd.DataFrame, book: Book, start_i: int, end_i: int | None = None,
              regime_state: np.ndarray | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Day-by-day book. Returns (equity curve, trades actually taken with quantity and ₹ P&L).
    Order inside a day: exits filled at the open → new entries → exits that happen during the day or at the close."""
    T = len(p["dates"])
    end_i = T - 1 if end_i is None else end_i
    C = p["c"]
    cash = float(book.capital)
    open_pos, taken, last_px = [], [], {}
    eq = np.full(T, np.nan)
    if trades is None or trades.empty:
        by_day = {}
    else:
        tr = trades[(trades["entry_i"] >= start_i) & (trades["entry_i"] <= end_i)].sort_values(["entry_i", "rs"], ascending=[True, False])
        by_day = {k: g for k, g in tr.groupby("entry_i")}
    equity = cash

    def close_out(ps):
        nonlocal cash
        fee = ps["qty"] * ps["entry"] * ps["cost"]
        cash += ps["qty"] * ps["exit"] - fee
        ps["pnl"] = ps["qty"] * (ps["exit"] - ps["entry"]) - fee
        taken.append(ps)

    for d in range(start_i, end_i + 1):
        still = []
        for ps in open_pos:                                           # 1. exits at this morning's open
            if ps["exit_i"] == d and ps["exit_open"]:
                close_out(ps)
            else:
                still.append(ps)
        open_pos = still
        g = by_day.get(d)
        if g is not None:                                             # 2. new entries
            scale = 1.0
            if regime_state is not None and book.regime_mode != "Off":
                stt = regime_state[d - 1] if d - 1 >= 0 else None
                if book.regime_mode == "Skip corrections":
                    scale = 0.0 if stt == "Correction" else 1.0
                else:
                    scale = EXPOSURE.get(stt, 1.0) if stt else 1.0
            slots = book.max_pos - len(open_pos)
            held = {ps["j"] for ps in open_pos}
            for r in g.itertuples(index=False):
                if slots <= 0 or scale <= 0:
                    break
                if r.j in held:
                    continue
                qty = math.floor(equity * book.risk_pct / 100 * scale / max(1e-9, r.entry - r.stop))
                qty = min(qty, math.floor(equity * book.max_pos_pct / 100 / r.entry), math.floor(cash / r.entry))
                if qty < 1:
                    continue
                ps = r._asdict()
                ps.update(qty=qty, cost=(r.r_gross - r.r) * (r.entry - r.stop) / r.entry, value=qty * r.entry, equity_at_entry=equity)
                cash -= qty * r.entry
                open_pos.append(ps)
                held.add(r.j)
                slots -= 1
        still = []
        for ps in open_pos:                                           # 3. stops, targets and time stops during the day
            if ps["exit_i"] == d and (not ps["exit_open"] or ps["entry_i"] == d):
                close_out(ps)
            else:
                still.append(ps)
        open_pos = still
        mtm = 0.0
        for ps in open_pos:
            px = C[d, ps["j"]]
            if np.isfinite(px):
                last_px[ps["j"]] = px
            mtm += ps["qty"] * last_px.get(ps["j"], ps["entry"])
        equity = cash + mtm
        eq[d] = equity
    for ps in open_pos:                                               # still open at the end: mark to market
        px = last_px.get(ps["j"], ps["entry"])
        ps["pnl"] = ps["qty"] * (px - ps["entry"])
        ps["exit"], ps["reason"] = px, "Open at end"
        taken.append(ps)
    curve = pd.Series(eq[start_i:end_i + 1], index=p["dates"][start_i:end_i + 1]).ffill()
    tk = pd.DataFrame(taken)
    if not tk.empty:
        tk["pnl_pct_of_equity"] = tk["pnl"] / tk["equity_at_entry"] * 100
        tk = tk.sort_values("entry_i").reset_index(drop=True)
    return curve, tk


# ═════════════════════════ statistics ═════════════════════════
def trade_stats(r: pd.Series | np.ndarray, days=None) -> dict:
    r = pd.Series(r, dtype=float).dropna()
    n = len(r)
    if n == 0:
        return {"n": 0}
    w, ls = r[r > 0], r[r <= 0]
    sd = r.std(ddof=1) if n > 1 else float("nan")
    streak = best = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    out = {"n": n, "win": len(w) / n * 100, "avg_win": w.mean() if len(w) else 0.0, "avg_loss": ls.mean() if len(ls) else 0.0,
           "exp": r.mean(), "median": r.median(), "pf": (w.sum() / abs(ls.sum())) if ls.sum() < 0 else float("inf"),
           "sqn": math.sqrt(min(n, 100)) * r.mean() / sd if sd and sd > 0 else float("nan"),
           "t": math.sqrt(n) * r.mean() / sd if sd and sd > 0 else float("nan"), "total_r": r.sum(),
           "best": r.max(), "worst": r.min(), "max_losing_streak": best}
    if days is not None:
        out["avg_days"] = float(pd.Series(days).mean())
    return out


def curve_stats(curve: pd.Series) -> dict:
    curve = curve.dropna()
    if len(curve) < 2:
        return {}
    yrs = max(1e-9, (curve.index[-1] - curve.index[0]).days / 365.25)
    tot = curve.iloc[-1] / curve.iloc[0] - 1
    dd = curve / curve.cummax() - 1
    rets = curve.pct_change().dropna()
    cagr = (1 + tot) ** (1 / yrs) - 1 if tot > -1 else -1
    trough = dd.idxmin()
    peak = curve.loc[:trough].idxmax()
    return {"total": tot * 100, "cagr": cagr * 100, "maxdd": dd.min() * 100, "years": yrs,
            "calmar": (cagr / abs(dd.min())) if dd.min() < 0 else float("nan"),
            "sharpe": rets.mean() / rets.std() * math.sqrt(252) if rets.std() > 0 else float("nan"),
            "dd_peak": peak, "dd_trough": trough, "dd_series": dd}


def monte_carlo(r: np.ndarray, risk_pct: float, n_trades: int | None = None, sims: int = 3000, seed: int = 11) -> dict:
    """Bootstrap the trade sequence: the range of drawdowns, losing streaks and outcomes you should be ready for."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 10:
        return {}
    n = n_trades or len(r)
    rng = np.random.default_rng(seed)
    seq = rng.choice(r, size=(sims, n), replace=True)
    f = risk_pct / 100
    eq = np.cumprod(1 + f * seq, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((sims, 1)), eq], 1), axis=1)[:, 1:]
    mdd = (eq / peak - 1).min(1) * 100
    final = (eq[:, -1] - 1) * 100
    loss = seq <= 0
    streak = np.zeros(sims, dtype=int)
    cur = np.zeros(sims, dtype=int)
    for k in range(n):
        cur = np.where(loss[:, k], cur + 1, 0)
        streak = np.maximum(streak, cur)
    return {"n": n, "sims": sims, "dd_med": float(np.median(mdd)), "dd_p90": float(np.percentile(mdd, 10)), "dd_p95": float(np.percentile(mdd, 5)),
            "ret_med": float(np.median(final)), "ret_p05": float(np.percentile(final, 5)), "ret_p95": float(np.percentile(final, 95)),
            "p_loss": float((final < 0).mean() * 100), "streak_med": float(np.median(streak)), "streak_p90": float(np.percentile(streak, 90)),
            "mdd_hist": mdd}


def stop_study(tr: pd.DataFrame) -> dict:
    """What the MAE / MFE of these trades say about the stop and the exit."""
    if tr is None or len(tr) < 20:
        return {}
    win = tr[tr["r"] > 0]
    out = {"win_mae_share_05": float((win["mae"] > -0.5).mean() * 100) if len(win) else float("nan"),
           "giveback": float((win["mfe"] - win["r_gross"]).mean()) if len(win) else float("nan"),
           "losers_hit_1r": float(((tr["r"] <= 0) & (tr["mfe"] >= 1)).mean() * 100)}
    best = None
    base = tr["r"].mean()
    for x in (0.5, 0.6, 0.75):
        alt = np.where(tr["mae"] <= -x, -x - (tr["r_gross"] - tr["r"]), tr["r"])
        e = float(np.mean(alt))
        if best is None or e > best[1]:
            best = (x, e)
    out["tight_stop"] = best
    out["base_exp"] = float(base)
    return out


def breakdown(tr: pd.DataFrame, by: str, order=None) -> pd.DataFrame:
    if tr is None or tr.empty or by not in tr:
        return pd.DataFrame()
    g = tr.groupby(by, observed=True)["r"]
    out = pd.DataFrame({"trades": g.size(), "win": g.apply(lambda s: (s > 0).mean() * 100), "exp": g.mean(), "total": g.sum()})
    if order is not None:
        out = out.reindex([o for o in order if o in out.index])
    return out


def add_buckets(tr: pd.DataFrame, regime_state: np.ndarray | None = None) -> pd.DataFrame:
    if tr is None or tr.empty:
        return tr
    tr = tr.copy()
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    tr["rs_bucket"] = pd.cut(tr["rs"], [0, 69.5, 79.5, 89.5, 100], labels=["Under 70", "70 to 79", "80 to 89", "90 to 99"])
    tr["ext_bucket"] = pd.cut(tr["ext21"], [-100, 0, 2, 5, 8, 1000], labels=["Below 21 EMA", "0 to 2%", "2 to 5%", "5 to 8%", "Over 8%"])
    if regime_state is not None:
        tr["regime"] = [regime_state[i] if 0 <= i < len(regime_state) and regime_state[i] else "Unknown" for i in tr["sig_i"]]
    return tr


# ═════════════════════════ runs ═════════════════════════
def _start_i(p, start_year=None):
    s = WARMUP
    if start_year:
        s = max(s, int(np.searchsorted(p["dates"], pd.Timestamp(f"{start_year}-01-01"))))
    return s


def run_single(p, rules, ex, book, cost_pct, state=None, cols=None, start_year=None):
    """Your exact rules, no tuning."""
    sig = signals(p, rules, state, cols)
    tr = make_trades(p, sig, ex, cost_pct)
    dropped = tr.attrs.get("dropped_suspect", 0)
    s0 = _start_i(p, start_year)
    tr = tr[tr["entry_i"] >= s0] if not tr.empty else tr
    curve, taken = portfolio(p, tr, book, s0, None, state)
    return {"mode": "single", "signals": int(sig[s0:].sum()), "trades": tr, "curve": curve, "taken": taken, "start_i": s0,
            "variants": 1, "dropped": dropped}


def halves(tr: pd.DataFrame) -> dict:
    """First half vs second half of the test period: an edge that exists in only one half is fragile."""
    if tr is None or len(tr) < 20:
        return {}
    mid = (tr["entry_i"].min() + tr["entry_i"].max()) // 2
    a, b = tr[tr["entry_i"] <= mid]["r"], tr[tr["entry_i"] > mid]["r"]
    return {"a_n": len(a), "a_exp": float(a.mean()) if len(a) else np.nan, "b_n": len(b), "b_exp": float(b.mean()) if len(b) else np.nan,
            "mid": mid}


def sensitivity(p, rules, ex, cost_pct, state=None, cols=None, progress=None) -> pd.DataFrame:
    """Move each numeric setting of your rules one step down and up (about 25%), one at a time, and re-test.
    A robust strategy stays positive; one that flips when a number moves a little was fitted to noise."""
    masks = [rule_mask(p, r, state) for r in rules]
    jobs = []
    for i, r in enumerate(rules):
        for spec in RULES[r["type"]][2]:
            if isinstance(spec[2], (list, str)):
                continue
            key, label, _, lo, hi, step = spec
            cur = r[key]
            delta = max(step, abs(cur) * 0.25)
            vals = []
            for v in (cur - delta, cur + delta):
                v = min(hi, max(lo, round(round(v / step) * step, 6)))
                v = int(v) if isinstance(spec[2], int) else float(v)
                vals.append(v if v != cur else None)
            jobs.append((i, key, label, cur, vals))
    rows = []
    for k, (i, key, label, cur, vals) in enumerate(jobs):
        row = {"rule": RULES[rules[i]["type"]][1], "setting": label, "yours": cur}
        for side, v in zip(("lower", "higher"), vals):
            if v is None:
                row[side], row[f"{side}_exp"], row[f"{side}_n"] = None, np.nan, 0
                continue
            alt = dict(rules[i]); alt[key] = v
            ms = list(masks); ms[i] = rule_mask(p, alt, state)
            tr = make_trades(p, signals(p, rules, state, cols, ms), ex, cost_pct)
            tr = tr[tr["entry_i"] >= WARMUP] if not tr.empty else tr
            row[side], row[f"{side}_exp"], row[f"{side}_n"] = v, (float(tr["r"].mean()) if len(tr) else np.nan), len(tr)
        rows.append(row)
        if progress:
            progress((k + 1) / max(1, len(jobs)), f"Checked nearby setting {k + 1} of {len(jobs)}")
    return pd.DataFrame(rows)


def combos(grid: dict) -> list:
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*[grid[k] for k in keys])]


def apply_combo(rules, ex, cmb):
    """grid keys: (rule index, param) for a rule setting, or ('exit', key) for an exit setting."""
    rules2, ex2 = [dict(r) for r in rules], dict(ex)
    for (a, b), v in cmb.items():
        if a == "exit":
            ex2[b] = v
        else:
            rules2[a][b] = v
    return rules2, ex2


def run_walk_forward(p, rules, ex, book, cost_pct, grid: dict, train_years=3, test_months=6, state=None, cols=None,
                     min_train_trades=20, progress=None):
    """Optional: try the alternative values YOU listed on each block of past years, then trade the best on the next unseen months.
    If no alternative made money in the training years, that window is not traded."""
    T = len(p["dates"])
    allc = combos(grid)
    lists, dropped = [], 0
    for k, cmb in enumerate(allc):
        r2, x2 = apply_combo(rules, ex, cmb)
        tr = make_trades(p, signals(p, r2, state, cols), x2, cost_pct)
        dropped = max(dropped, tr.attrs.get("dropped_suspect", 0))
        lists.append(tr)
        if progress:
            progress((k + 1) / len(allc) * 0.9, f"Tested variant {k + 1} of {len(allc)}")
    train, test = int(train_years * 252), int(test_months * 21)
    s0 = WARMUP
    windows, oos = [], []
    ts = s0 + train
    while ts < T - 5:
        te = min(T - 1, ts + test - 1)
        best, best_obj, best_is = None, -1e18, None
        for k, tr in enumerate(lists):
            if tr.empty:
                continue
            m = (tr["entry_i"] >= ts - train) & (tr["exit_i"] < ts)
            n = int(m.sum())
            if n < min_train_trades:
                continue
            e = float(tr.loc[m, "r"].mean())
            if e * math.sqrt(n) > best_obj:
                best, best_obj, best_is = k, e * math.sqrt(n), (e, n)
        w = {"train_from": p["dates"][ts - train], "test_from": p["dates"][ts], "test_to": p["dates"][te], "chosen": None,
             "is_exp": np.nan, "is_n": 0, "oos_exp": np.nan, "oos_n": 0, "note": "too few trades in the training years"}
        if best is not None and best_is[0] <= 0:
            w.update(is_exp=best_is[0], is_n=best_is[1], note="no variant made money in the training years: stood aside")
        elif best is not None:
            tr = lists[best]
            sel = tr[(tr["entry_i"] >= ts) & (tr["entry_i"] <= te)]
            oos.append(sel.assign(window=len(windows)))
            w.update(chosen=allc[best], is_exp=best_is[0], is_n=best_is[1], oos_exp=float(sel["r"].mean()) if len(sel) else np.nan,
                     oos_n=len(sel), note="")
        windows.append(w)
        ts = te + 1
    cols_ = lists[0].columns if lists else []
    oos_tr = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame(columns=cols_)
    start_i = s0 + train
    curve, taken = portfolio(p, oos_tr, book, start_i, None, state)
    full = []
    for cmb, tr in zip(allc, lists):
        tr2 = tr[tr["entry_i"] >= s0] if not tr.empty else tr
        full.append({"combo": cmb, "trades": len(tr2), "exp": float(tr2["r"].mean()) if len(tr2) else np.nan,
                     "win": float((tr2["r"] > 0).mean() * 100) if len(tr2) else np.nan})
    wdf = pd.DataFrame(windows)
    traded = wdf[wdf["chosen"].notna()] if len(wdf) else wdf
    is_exp = float(traded["is_exp"].mean()) if len(traded) else np.nan
    oos_exp = float(oos_tr["r"].mean()) if len(oos_tr) else np.nan
    return {"mode": "walk", "trades": oos_tr, "curve": curve, "taken": taken, "start_i": start_i, "windows": wdf, "variants": len(allc),
            "grid_results": full, "is_exp": is_exp, "oos_exp": oos_exp, "dropped": dropped,
            "wfe": (oos_exp / is_exp) if np.isfinite(is_exp) and is_exp > 0 and np.isfinite(oos_exp) else np.nan}


def per_stock(tr: pd.DataFrame) -> pd.DataFrame:
    if tr is None or tr.empty:
        return pd.DataFrame()
    g = tr.groupby("symbol")["r"]
    out = pd.DataFrame({"trades": g.size(), "win": g.apply(lambda s: (s > 0).mean() * 100), "exp": g.mean(), "total": g.sum(),
                        "best": g.max(), "worst": g.min()})
    return out.sort_values("total", ascending=False)


def verdict(res: dict) -> tuple[str, str, str]:
    """(tone, headline, explanation) in plain words."""
    tr = res["trades"]
    st_ = trade_stats(tr["r"]) if tr is not None and len(tr) else {"n": 0}
    n = st_.get("n", 0)
    if n < 30:
        return ("warn", "Not enough trades to judge",
                f"Only {n} trades. You need roughly 100 before win rate and expectancy settle, so treat every number below as a rough hint. "
                "Test more stocks or more years, or relax a condition.")
    e, t = st_["exp"], st_.get("t", 0)
    wf = res.get("mode") == "walk"
    wfe = res.get("wfe", np.nan)
    small = " With under 100 trades the estimate is still loose." if n < 100 else ""
    if e > 0.15 and t >= 2 and (not wf or (np.isfinite(wfe) and wfe >= 0.5)):
        return ("good", "Edge holds" + (" on unseen data" if wf else " in this test"),
                f"{e:+.2f}R per trade over {n} trades (t = {t:.1f})" + (f", walk-forward efficiency {wfe:.2f}." if wf else ".")
                + " Statistically this is unlikely to be luck." + small + " Check the first-half / second-half and nearby-settings tables before trusting it.")
    if e > 0 and t >= 1:
        return ("warn", "Small or uncertain edge",
                f"{e:+.2f}R per trade over {n} trades (t = {t:.1f})" + (f", walk-forward efficiency {wfe:.2f}" if wf and np.isfinite(wfe) else "")
                + ". Positive but not convincing." + small + " Check the regime split: it may work only in uptrends.")
    return ("bad", "No reliable edge",
            f"{e:+.2f}R per trade over {n} trades after costs. Trading these rules as they stand would most likely lose money or tread water.")
