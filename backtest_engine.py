"""
Arka Trades — backtest_engine.py (v1): walk-forward backtester and expectancy engine for swing / momentum setups.

Realism rules (the ones most home-made backtests break)
  * A signal uses only data up to that day's close. Entry is the NEXT day's open. Gap-ups bigger than a limit are skipped.
  * Stops are checked before targets inside a day (worst case). A gap below the stop fills at the open, not the stop.
  * Trailing exits are decided on the close and filled at the next open.
  * Indian round-trip costs (STT, stamp duty, exchange, GST, DP charges) plus slippage are taken off every trade.
  * The portfolio is simulated with real constraints: capital, risk per trade, maximum positions, maximum position
    size and cash. When more signals fire than free slots, the highest RS rating is taken first.

Walk-forward
  For each window: pick the parameter set with the best (expectancy x sqrt(trades)) using only trades that ENTERED
  AND EXITED inside the training years, then trade that set on the next, unseen months. All unseen months are
  stitched into one out-of-sample result. Walk-forward efficiency = out-of-sample expectancy / in-sample expectancy.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import quant_data as qd

# ═════════════════════════ setups ═════════════════════════
SETUPS = {
    "Breakout": {
        "about": "Close above the highest high of the last N sessions on volume, in a stock with strong relative strength "
                 "that is above its 50 EMA and 200 DMA and not already stretched from its 21 EMA.",
        "params": {"lookback": 20, "vol_mult": 1.5, "rs_min": 80, "ext_max": 8.0},
        "grid": {"lookback": [20, 50], "vol_mult": [1.3, 1.8], "rs_min": [70, 85]},
    },
    "Pullback to 21 EMA": {
        "about": "A leader in an uptrend (21 EMA > 50 EMA > 200 DMA, within 15% of its 52-week high) dips to its 21 EMA "
                 "within the last 3 sessions, then prints a green candle that closes above the previous day's high.",
        "params": {"touch": 1.5, "rs_min": 80, "max_off_high": 15.0},
        "grid": {"touch": [1.0, 2.5], "rs_min": [70, 85]},
    },
    "52-week high": {
        "about": "A new 52-week high on above-average volume in a stock with strong relative strength, above its 50 EMA.",
        "params": {"vol_mult": 1.3, "rs_min": 85, "ext_max": 10.0},
        "grid": {"vol_mult": [1.0, 1.6], "rs_min": [75, 90]},
    },
    "Volatility contraction (VCP)": {
        "about": "Volatility dries up (14-day ATR well below the 50-day ATR, a tight 15-day range), then price breaks the "
                 "15-day high on volume, in an uptrend with strong relative strength.",
        "params": {"contraction": 0.80, "base_max": 12.0, "vol_mult": 1.5, "rs_min": 75},
        "grid": {"contraction": [0.7, 0.85], "base_max": [10.0, 15.0], "vol_mult": [1.3, 1.8]},
    },
}
PARAM_LABEL = {"lookback": "Breakout of the last N days", "vol_mult": "Volume ≥ × 50-day average", "rs_min": "RS rating at least",
               "ext_max": "Skip if more than % above 21 EMA", "touch": "Low within % of the 21 EMA", "max_off_high": "Within % of 52-week high",
               "contraction": "ATR 14 / ATR 50 at most", "base_max": "15-day range at most %", "trail": "Trailing EMA",
               "target_r": "Profit target (R)", "max_days": "Time stop (days)"}

EXIT_DEFAULT = {"stop": "Signal-day low", "stop_atr": 1.5, "stop_pct": 7.0, "max_risk": 10.0, "trail": 21, "target_r": 0.0,
                "max_days": 40, "breakeven_r": 0.0, "gap_max": 4.0}
COST_DEFAULT = 0.35     # % per round trip: ~0.25% statutory (STT 0.1% x2, stamp, exchange, GST, DP) + ~0.1% slippage
MIN_PRICE, MIN_TURN_CR = 20.0, 5.0


def signals(p: dict, setup: str, prm: dict, min_turn: float = MIN_TURN_CR) -> np.ndarray:
    """Boolean (T, N): the setup triggered at that day's close."""
    c, o, h, l, v = p["c"], p["o"], p["h"], p["l"], p["v"]
    e21, e50, s200, rs = p["ema21"], p["ema50"], p["sma200"], p["rs"]
    with np.errstate(invalid="ignore", divide="ignore"):
        liquid = (c >= MIN_PRICE) & (p["turn_cr"] >= min_turn)
        trend200 = np.where(np.isnan(s200), True, c > s200)
        volok = lambda m: v >= m * p["vavg50"]
        ext = (c / e21 - 1) * 100
        if setup == "Breakout":
            ph = qd.prior_high(p, int(prm["lookback"]))
            sig = (c > ph) & volok(prm["vol_mult"]) & (rs >= prm["rs_min"]) & (c > e50) & trend200 & (ext <= prm["ext_max"])
        elif setup == "Pullback to 21 EMA":
            touch = qd.roll_min((l / e21).astype(np.float64), 3) <= 1 + prm["touch"] / 100
            up = (e21 > e50) & np.where(np.isnan(s200), True, e50 > s200)
            near = c >= p["hh52"] * (1 - prm["max_off_high"] / 100)
            sig = touch & up & near & (rs >= prm["rs_min"]) & (c > e21) & (c > o) & (c > qd.shift(h, 1))
        elif setup == "52-week high":
            prior52 = qd.prior_high(p, 252)
            sig = (h >= prior52) & (c > e50) & volok(prm["vol_mult"]) & (rs >= prm["rs_min"]) & (ext <= prm["ext_max"])
        elif setup == "Volatility contraction (VCP)":
            ph15 = qd.prior_high(p, 15)
            k = "pl15"
            if k not in p:
                p[k] = np.asarray(qd.shift(qd.roll_min(p["l"].astype(np.float64), 15), 1), dtype=np.float32)
            tight = (ph15 - p[k]) / c * 100 <= prm["base_max"]
            contr = qd.shift((p["atr14"] / p["atr50"]).astype(np.float64), 1) <= prm["contraction"]
            sig = (c > ph15) & tight & contr & volok(prm["vol_mult"]) & (rs >= prm["rs_min"]) & (c > e50) & trend200
        else:
            raise ValueError(setup)
        sig = sig & liquid & ~np.isnan(c)
    sig[:252] = False                     # warm-up: 52-week and 200-day measures need a year
    return sig


# ═════════════════════════ trades (each signal on its own) ═════════════════════════
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
    trail = int(ex.get("trail") or 0)
    ema_t = p.get(f"ema{trail}") if trail else None
    if trail and ema_t is None:
        ema_t = p[f"ema{trail}"] = np.asarray(qd.ema(C.astype(np.float64), trail), dtype=np.float32)
    cost = cost_pct / 100.0
    gap_max = ex.get("gap_max", 4.0) / 100.0
    max_days = int(ex.get("max_days") or 60)
    tgt_r = float(ex.get("target_r") or 0)
    be_r = float(ex.get("breakeven_r") or 0)
    max_risk = ex.get("max_risk", 10.0) / 100.0
    rows = []
    cols = {}
    for j in np.flatnonzero(sig.any(0)):
        idxs = np.flatnonzero(sig[:, j])
        if j not in cols:
            cols[j] = (_ffill(O[:, j].astype(np.float64)), _ffill(H[:, j].astype(np.float64)),
                       _ffill(L[:, j].astype(np.float64)), _ffill(C[:, j].astype(np.float64)))
        o, h, l, c = cols[j]
        et = ema_t[:, j].astype(np.float64) if trail else None
        busy_until = -1
        for t in idxs:
            ei = t + 1
            if ei >= T or ei <= busy_until or not np.isfinite(O[ei, j]):
                continue
            entry = o[ei]
            if entry > c[t] * (1 + gap_max):
                continue                                           # chasing a big gap: skip
            if ex["stop"] == "Signal-day low":
                stop = l[t]
            elif ex["stop"] == "ATR multiple":
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
                    if ei + k + 1 < T:
                        cand.append((k + 1, 0, o[ei + k + 1], f"Close below {trail} EMA"))
                    else:
                        cand.append((k, 3, cl[k], f"Close below {trail} EMA"))
            cand.append((n - 1, 4, cl[n - 1], "Time stop" if end < T - 1 else "Open at end"))
            k, pri, px, why = min(cand, key=lambda x: (x[0], x[1]))
            xi = ei + k
            last = xi - 1 if (pri == 0 and xi > ei) else xi                # exited at the open: that day's range never happened to us
            seg_lo, seg_hi = l[ei:last + 1], h[ei:last + 1]
            r_g = (px - entry) / risk
            mae = (seg_lo.min() - entry) / risk if len(seg_lo) else min(0.0, r_g)
            mfe = (seg_hi.max() - entry) / risk if len(seg_hi) else max(0.0, r_g)
            if "top" in why:                                               # stopped: the worst we lived through is the fill
                mae = max(mae, r_g)
            mae, mfe = min(mae, r_g, 0.0), max(mfe, r_g, 0.0)
            r_net = (px - entry - cost * entry) / risk
            rows.append((j, t, ei, xi, entry, stop, px, why, r_g, r_net, (px / entry - 1 - cost) * 100, xi - ei + 1, mae, mfe,
                         float(p["rs"][t, j]), float((C[t, j] / p["ema21"][t, j] - 1) * 100), risk / entry * 100))
            busy_until = xi
    df = pd.DataFrame(rows, columns=["j", "sig_i", "entry_i", "exit_i", "entry", "stop", "exit", "reason", "r_gross", "r", "ret_pct",
                                     "days", "mae", "mfe", "rs", "ext21", "risk_pct"])
    if not df.empty:
        df["symbol"] = [p["symbols"][j] for j in df["j"]]
        df["sector"] = [p["industry"][j] for j in df["j"]]
        df["entry_date"] = p["dates"][df["entry_i"].to_numpy()]
        df["exit_date"] = p["dates"][df["exit_i"].to_numpy()]
        df["open_at_end"] = df["reason"] == "Open at end"
    return df


# ═════════════════════════ portfolio ═════════════════════════
@dataclass
class Book:
    capital: float = 1_000_000.0
    risk_pct: float = 1.0
    max_pos: int = 8
    max_pos_pct: float = 25.0
    regime_mode: str = "Off"            # Off | Skip corrections | Scale by regime


def portfolio(p: dict, trades: pd.DataFrame, book: Book, start_i: int, end_i: int | None = None,
              regime_state: np.ndarray | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Day-by-day book. Returns (equity curve, trades actually taken with quantity and ₹ P&L)."""
    from regime_engine import EXPOSURE
    T = len(p["dates"])
    end_i = T - 1 if end_i is None else end_i
    C = p["c"]
    cash = float(book.capital)
    open_pos = []                                                     # dicts
    taken = []
    eq = np.full(T, np.nan)
    last_px = {}
    if trades is None or trades.empty:
        tr = pd.DataFrame(columns=["entry_i"])
    else:
        tr = trades[(trades["entry_i"] >= start_i) & (trades["entry_i"] <= end_i)].sort_values(["entry_i", "rs"], ascending=[True, False])
    by_day = {k: g for k, g in tr.groupby("entry_i")} if len(tr) else {}
    equity = cash
    for d in range(start_i, end_i + 1):
        still = []
        for ps in open_pos:
            if ps["exit_i"] == d:
                cash += ps["qty"] * ps["exit"] - ps["qty"] * ps["entry"] * ps["cost"]
                ps["pnl"] = ps["qty"] * (ps["exit"] - ps["entry"]) - ps["qty"] * ps["entry"] * ps["cost"]
                taken.append(ps)
            else:
                still.append(ps)
        open_pos = still
        g = by_day.get(d)
        if g is not None:
            scale = 1.0
            if regime_state is not None and book.regime_mode != "Off":
                stt = regime_state[d - 1] if d - 1 >= 0 else None
                if book.regime_mode == "Skip corrections":
                    scale = 0.0 if stt in ("Correction",) else 1.0
                else:
                    scale = EXPOSURE.get(stt, 1.0) if stt else 1.0
            slots = book.max_pos - len(open_pos)
            held = {ps["j"] for ps in open_pos}
            for r in g.itertuples(index=False):
                if slots <= 0 or scale <= 0:
                    break
                if r.j in held:
                    continue
                risk_amt = equity * book.risk_pct / 100 * scale
                qty = math.floor(risk_amt / max(1e-9, r.entry - r.stop))
                qty = min(qty, math.floor(equity * book.max_pos_pct / 100 / r.entry), math.floor(cash / r.entry))
                if qty < 1:
                    continue
                c_rt = (r.r_gross - r.r) * (r.entry - r.stop) / r.entry          # cost fraction used for this trade
                cash -= qty * r.entry
                ps = r._asdict()
                ps.update(qty=qty, cost=c_rt, value=qty * r.entry, equity_at_entry=equity)
                open_pos.append(ps)
                held.add(r.j)
                slots -= 1
        # same-day exits for positions opened today
        still = []
        for ps in open_pos:
            if ps["exit_i"] == d and ps["entry_i"] == d:
                cash += ps["qty"] * ps["exit"] - ps["qty"] * ps["entry"] * ps["cost"]
                ps["pnl"] = ps["qty"] * (ps["exit"] - ps["entry"]) - ps["qty"] * ps["entry"] * ps["cost"]
                taken.append(ps)
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
def combos(grid: dict) -> list:
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*[grid[k] for k in keys])]


def run_single(p, setup, prm, ex, book, cost_pct, regime_state=None, start_year=None, min_turn=MIN_TURN_CR):
    sig = signals(p, setup, prm, min_turn)
    tr = make_trades(p, sig, ex, cost_pct)
    start_i = 252
    if start_year:
        start_i = max(start_i, int(np.searchsorted(p["dates"], pd.Timestamp(f"{start_year}-01-01"))))
    tr = tr[tr["entry_i"] >= start_i] if not tr.empty else tr
    curve, taken = portfolio(p, tr, book, start_i, None, regime_state)
    return {"mode": "single", "signals": int(sig.sum()), "trades": tr, "curve": curve, "taken": taken, "start_i": start_i,
            "params": dict(prm), "variants": 1}


def run_walk_forward(p, setup, prm, ex, book, cost_pct, grid: dict, train_years=3, test_months=6, regime_state=None,
                     min_train_trades=20, progress=None, min_turn=MIN_TURN_CR):
    """Tune on train years, trade the next unseen months, roll forward. Grid keys may include 'trail'."""
    T = len(p["dates"])
    allc = combos(grid)
    exit_keys = {"trail", "target_r", "max_days", "breakeven_r"}
    cache_sig, lists = {}, []
    for k, cmb in enumerate(allc):
        ep = {**prm, **{a: b for a, b in cmb.items() if a not in exit_keys}}
        xx = {**ex, **{a: b for a, b in cmb.items() if a in exit_keys}}
        key = tuple(sorted((a, b) for a, b in ep.items()))
        if key not in cache_sig:
            cache_sig[key] = signals(p, setup, ep, min_turn)
        lists.append(make_trades(p, cache_sig[key], xx, cost_pct))
        if progress:
            progress((k + 1) / len(allc) * 0.85, f"Tested variant {k + 1} of {len(allc)}")
    train, test = int(train_years * 252), int(test_months * 21)
    s0 = 252
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
            obj = e * math.sqrt(n)
            if obj > best_obj:
                best, best_obj, best_is = k, obj, (e, n)
        if best is None:
            windows.append({"train_from": p["dates"][ts - train], "test_from": p["dates"][ts], "test_to": p["dates"][te], "chosen": None,
                            "is_exp": np.nan, "is_n": 0, "oos_exp": np.nan, "oos_n": 0})
        else:
            tr = lists[best]
            mo = (tr["entry_i"] >= ts) & (tr["entry_i"] <= te)
            sel = tr[mo]
            oos.append(sel.assign(window=len(windows)))
            windows.append({"train_from": p["dates"][ts - train], "test_from": p["dates"][ts], "test_to": p["dates"][te], "chosen": allc[best],
                            "is_exp": best_is[0], "is_n": best_is[1], "oos_exp": float(sel["r"].mean()) if len(sel) else np.nan, "oos_n": len(sel)})
        ts = te + 1
    oos_tr = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame(columns=lists[0].columns if lists else [])
    start_i = s0 + train
    if progress:
        progress(0.92, "Simulating the out-of-sample portfolio")
    curve, taken = portfolio(p, oos_tr, book, start_i, None, regime_state)
    full = []
    for cmb, tr in zip(allc, lists):
        tr2 = tr[tr["entry_i"] >= s0] if not tr.empty else tr
        st_ = trade_stats(tr2["r"]) if len(tr2) else {"n": 0}
        full.append({**cmb, "trades": st_.get("n", 0), "exp": st_.get("exp", np.nan), "win": st_.get("win", np.nan), "pf": st_.get("pf", np.nan)})
    wdf = pd.DataFrame(windows)
    is_exp = float(np.nanmean(wdf["is_exp"])) if len(wdf) else np.nan
    oos_exp = float(oos_tr["r"].mean()) if len(oos_tr) else np.nan
    return {"mode": "walk", "trades": oos_tr, "curve": curve, "taken": taken, "start_i": start_i, "windows": wdf,
            "variants": len(allc), "grid_results": pd.DataFrame(full), "is_exp": is_exp, "oos_exp": oos_exp,
            "wfe": (oos_exp / is_exp) if is_exp and np.isfinite(is_exp) and is_exp > 0 else np.nan, "params": dict(prm)}


def sensitivity(p, setup, prm, ex, cost_pct, axes: dict, min_turn=MIN_TURN_CR) -> pd.DataFrame:
    """Expectancy on a small grid around the chosen settings (is it a plateau or a lucky peak?)."""
    rows = []
    exit_keys = {"trail", "target_r", "max_days", "breakeven_r"}
    for cmb in combos(axes):
        ep = {**prm, **{a: b for a, b in cmb.items() if a not in exit_keys}}
        xx = {**ex, **{a: b for a, b in cmb.items() if a in exit_keys}}
        tr = make_trades(p, signals(p, setup, ep, min_turn), xx, cost_pct)
        tr = tr[tr["entry_i"] >= 252] if not tr.empty else tr
        rows.append({**cmb, "trades": len(tr), "exp": float(tr["r"].mean()) if len(tr) else np.nan})
    return pd.DataFrame(rows)


def verdict(res: dict) -> tuple[str, str, str]:
    """(tone, headline, explanation) in plain words."""
    tr = res["trades"]
    st_ = trade_stats(tr["r"]) if tr is not None and len(tr) else {"n": 0}
    n = st_.get("n", 0)
    if n < 30:
        return ("warn", "Not enough trades to judge",
                f"Only {n} trades. You need roughly 100 before win rate and expectancy settle. Widen the universe, relax a filter or test more years.")
    e, t = st_["exp"], st_.get("t", 0)
    wf = res.get("mode") == "walk"
    wfe = res.get("wfe", np.nan)
    if e > 0.15 and t >= 2 and (not wf or (np.isfinite(wfe) and wfe >= 0.5)):
        return ("good", "Edge holds" + (" on unseen data" if wf else ""),
                f"{e:+.2f}R per trade over {n} trades (t = {t:.1f})" + (f", walk-forward efficiency {wfe:.2f}." if wf else ".")
                + " Statistically this is unlikely to be luck. Size it with the Monte Carlo drawdown in mind.")
    if e > 0 and t >= 1:
        return ("warn", "Small or uncertain edge",
                f"{e:+.2f}R per trade over {n} trades (t = {t:.1f})" + (f", walk-forward efficiency {wfe:.2f}" if wf and np.isfinite(wfe) else "")
                + ". Positive but not convincing. Check the regime split: it may work only in uptrends.")
    return ("bad", "No reliable edge",
            f"{e:+.2f}R per trade over {n} trades after costs. Trading this rule as it stands would most likely lose money or tread water.")
