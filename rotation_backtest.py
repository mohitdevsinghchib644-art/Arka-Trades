"""
rotation_backtest.py — Arka sector-rotation research & backtest engine (v1).

Pure pandas / numpy: no network, no Streamlit, so every number can be unit-tested.

What it does
------------
1. SIGNALS   compute_signals() turns a panel of sector index levels (dates x sectors) + a benchmark into
             point-in-time signals: trailing momentum, the Arka 0-100 rotation score, RRG-style lanes
             (LEADING / IMPROVING / FADING / WEAK), the stage (ROTATING IN / LEADING / EXTENDED / ...) and a
             market regime flag. Every value at date t uses ONLY data up to and including t.
2. BACKTEST  run_backtest() rebalances on a schedule (weekly / fortnightly / monthly), trades on the NEXT
             close after the signal (exec_lag=1 by default, so there is no look-ahead), lets weights drift
             between rebalances, charges transaction costs on every rupee traded, and parks unused slots in
             cash (earning a cash yield) or in the benchmark.
3. METRICS   CAGR, volatility, Sharpe, Sortino, max drawdown (+ duration), Calmar, alpha/beta, tracking
             error, information ratio, monthly hit-rate vs benchmark, turnover.
4. ROBUSTNESS parameter sweep, walk-forward (re-optimise on the past N years, test on the next year only),
             random-selection baseline (is the result better than luck?), cost and delay sensitivity.
5. SIGNAL TESTS information coefficient (does a higher score today mean a better next month?), forward
             excess return by stage and by score bucket — this is what tells you if the radar labels work.
6. LENSES    momentum table, weekly rank heatmap, cyclical-vs-defensive risk appetite, sector dispersion.
7. FPI FLOWS parser for NSDL's fortnightly sector-wise FPI investment report (the only public
             sector-level record of actual institutional money in/out of Indian sectors).

Honest limits (shown in the UI as well)
  * Sector indices built from TODAY's constituent lists carry survivorship bias (dead / delisted stocks are
    missing), which flatters any backtest. Official NSE indices avoid this; prefer them for backtests.
  * Price indices exclude dividends (applies equally to strategy and benchmark).
  * Past rotation behaviour is not a forecast. A backtest is evidence, not proof.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from io import StringIO

import numpy as np
import pandas as pd

TD = 252
EXTENDED_PCT, EXTENDED_RSI, ROT_MIN_SCORE = 6.0, 72.0, 55.0
RS_TREND, RS_MOM = 50, 10
LANES = ("LEADING", "IMPROVING", "FADING", "WEAK")
STAGE_ORDER = ("ROTATING IN", "LEADING", "EXTENDED", "IMPROVING", "FADING", "WEAK")

STRATEGIES = {
    "MOMENTUM": "Hold the top-N sectors by trailing return (relative ranking only, always invested).",
    "DUAL MOMENTUM": "Top-N by trailing return, but only sectors whose own trailing return is positive; empty slots go defensive.",
    "ARKA SCORE": "Top-N by the Arka 0-100 rotation score (only sectors at or above the score threshold); empty slots go defensive.",
    "RRG LEADING": "Sectors in the LEADING (optionally also IMPROVING) lane vs the benchmark, strongest first.",
    "ROTATING IN": "Only sectors carrying the ROTATING IN stage at the rebalance date; empty slots go defensive.",
    "EQUAL WEIGHT": "Every eligible sector in equal weight (the 'no skill' sector baseline).",
}


@dataclass(frozen=True)
class BTConfig:
    strategy: str = "MOMENTUM"
    top_n: int = 3
    lookback: int = 63          # sessions used for momentum
    skip: int = 0               # skip the most recent N sessions (e.g. 5 = ignore last week's noise)
    rebalance: str = "M"        # "W" weekly, "2W" fortnightly, "M" monthly
    cost_bps: float = 15.0      # per side, brokerage + STT + impact (sector ETF ~10-25 bps)
    exec_lag: int = 1           # 1 = signal at close t, trade at close t+1 (realistic EOD); 0 = same close
    regime_filter: bool = False  # go defensive when the benchmark is below its 200-EMA
    defensive: str = "CASH"     # "CASH" (earns cash_yield) or "BENCH"
    cash_yield: float = 6.0     # % a year, liquid-fund proxy; also the Sharpe risk-free rate
    min_score: float = ROT_MIN_SCORE
    include_improving: bool = True

    def label(self) -> str:
        bits = [self.strategy, f"top {self.top_n}", f"{self.lookback}d lookback" + (f" (skip {self.skip})" if self.skip else ""),
                {"W": "weekly", "2W": "fortnightly", "M": "monthly"}.get(self.rebalance, self.rebalance),
                f"{self.cost_bps:g} bps", f"lag {self.exec_lag}"]
        if self.regime_filter:
            bits.append("regime filter")
        return " · ".join(bits)


# ═════════════════════════ indicators (vectorised, point-in-time) ═════════════════════════
def _ema(df, n):
    return df.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(df, n=14):
    d = df.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return r.where(~((dn == 0) & up.notna()), 100.0)


def _recent_cross_up(a, b, k):
    """True where `a` crossed above `b` within the last k sessions and is still above it."""
    above = a > b
    prev_below = a.shift(1) <= (b.shift(1) if isinstance(b, (pd.DataFrame, pd.Series)) else b)
    cross = (above & prev_below).astype(float)
    return (cross.rolling(k, min_periods=1).max() > 0) & above


def tidy_prices(prices: pd.DataFrame, spike=0.20, revert=0.08):
    """tz-naive daily panel, positive values, isolated bad ticks (spike that reverts next day) repaired.
    Returns (clean panel, number of repaired ticks)."""
    P = prices.copy()
    idx = pd.to_datetime(P.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    P.index = idx.normalize()
    P = P[~P.index.duplicated(keep="last")].sort_index()
    P = P.apply(pd.to_numeric, errors="coerce").where(lambda x: x > 0)
    prev = P.ffill().shift(1)
    nxt = P.bfill().shift(-1)
    bad = ((P / prev - 1).abs() > spike) & ((nxt / prev - 1).abs() < revert)
    n_bad = int(bad.sum().sum())
    P = P.mask(bad)
    return _ffill_inside(P), n_bad


def _ffill_inside(P):
    """Forward-fill gaps but never past a column's last real value (no fake flat tail)."""
    lv = P.apply(lambda c: c.last_valid_index())
    F = P.ffill()
    for c in F.columns:
        if lv[c] is not None:
            F.loc[F.index > lv[c], c] = np.nan
    return F


def compute_signals(prices: pd.DataFrame, bench: pd.Series, breadth: pd.DataFrame | None = None,
                    cmf: pd.DataFrame | None = None, share: pd.DataFrame | None = None,
                    lookback: int = 63, skip: int = 0) -> dict:
    """Point-in-time signal panels (dates x sectors). Missing optional inputs simply drop their score legs."""
    P = _ffill_inside(prices.sort_index())
    B = bench.reindex(P.index.union(bench.index)).ffill().reindex(P.index)
    e10, e20, e50 = _ema(P, 10), _ema(P, 20), _ema(P, 50)
    rsi = _rsi(P)
    rs = P.div(B, axis=0)
    ratio = 100 * rs / rs.rolling(RS_TREND, min_periods=RS_TREND).mean()
    rmom = 100 * ratio / ratio.shift(RS_MOM)
    rs_e20 = _ema(rs, 20)

    legs = [((P > e20), 10), ((P > e50), 10), ((e10 > e20), 5), ((e20 > e50), 5),
            ((rsi > 50), 10), ((rsi > rsi.shift(5)), 5), ((rsi <= 75), 5),
            ((ratio >= 100), 10), ((rmom >= 100), 10), ((rs > rs_e20), 5)]
    if breadth is not None:
        br = breadth.reindex(index=P.index, columns=P.columns)
        legs += [((br >= 50), 8), ((br > br.shift(5)), 7)]
    if cmf is not None:
        cm = cmf.reindex(index=P.index, columns=P.columns)
        legs += [((cm > 0), 5)]
    if share is not None:
        sh = share.reindex(index=P.index, columns=P.columns)
        legs += [((sh > sh.shift(5)), 5)]
    max_pts = sum(w for _, w in legs)
    pts = sum(c.astype(float) * w for c, w in legs)
    warm = e50.notna() & rmom.notna() & rsi.notna()
    score = (pts / max_pts * 100).where(warm)

    x, y = ratio, rmom
    quad = pd.DataFrame(np.select([(x >= 100) & (y >= 100), (x >= 100) & (y < 100), (x < 100) & (y >= 100), (x < 100) & (y < 100)],
                                  ["LEADING", "FADING", "IMPROVING", "WEAK"], default=""), index=P.index, columns=P.columns)
    dist20 = (P / e20 - 1) * 100
    fresh = (_recent_cross_up(P, e20, 5) | _recent_cross_up(rsi, 50, 5) |
             _recent_cross_up(e10, e20, 10) | _recent_cross_up(rs, rs_e20, 10))
    ext = (dist20 > EXTENDED_PCT) | (rsi > EXTENDED_RSI)
    confirmed = (P > e20) & (rsi >= 50)
    lead, imp = quad.eq("LEADING"), quad.eq("IMPROVING")
    rot_in = (lead | imp) & fresh & confirmed & ~ext & (score >= ROT_MIN_SCORE)
    stage = pd.DataFrame(np.select([rot_in, lead & ext, lead, imp, quad.eq("FADING"), quad.eq("WEAK")],
                                   ["ROTATING IN", "EXTENDED", "LEADING", "IMPROVING", "FADING", "WEAK"], default=""),
                         index=P.index, columns=P.columns)

    bema = _ema(B.to_frame("b"), 200)["b"]
    regime = (B > bema).where(bema.notna()).fillna(True).astype(bool)
    sig = {"P": P, "B": B, "score": score, "quad": quad, "stage": stage, "ratio": ratio, "rmom": rmom,
           "rsi": rsi, "dist20": dist20, "regime": regime, "max_pts": max_pts}
    return with_lookback(sig, lookback, skip)


def with_lookback(sig: dict, lookback: int, skip: int = 0) -> dict:
    """Same signals, momentum recomputed for another lookback (cheap; used by the sweeps)."""
    P = sig["P"]
    out = dict(sig)
    out["mom"] = (P.shift(skip) / P.shift(skip + lookback) - 1) * 100
    out["lookback"], out["skip"] = lookback, skip
    return out


# ═════════════════════════ selection + engine ═════════════════════════
def rebalance_dates(dates, freq: str) -> pd.DatetimeIndex:
    d = pd.DatetimeIndex(dates)
    s = pd.Series(d, index=d)
    g = s.groupby(d.to_period("W-FRI" if freq in ("W", "2W") else "M")).max()
    out = list(g.values)
    if freq == "2W":
        out = out[::2]
    return pd.DatetimeIndex(out)


def pick_sectors(cfg: BTConfig, sig: dict, t, rng=None) -> list:
    mom = sig["mom"].loc[t]
    s = cfg.strategy
    if s == "EQUAL WEIGHT":
        return mom.dropna().index.tolist()
    if s == "RANDOM":
        pool = mom.dropna().index.tolist()
        return list(rng.choice(pool, size=min(cfg.top_n, len(pool)), replace=False)) if pool else []
    if s == "MOMENTUM":
        c = mom.dropna().sort_values(ascending=False)
    elif s == "DUAL MOMENTUM":
        c = mom[mom > 0].sort_values(ascending=False)
    elif s == "ARKA SCORE":
        sc = sig["score"].loc[t]
        ok = sc[sc >= cfg.min_score].dropna()
        c = pd.DataFrame({"s": ok, "m": mom.reindex(ok.index).fillna(-1e9)}).sort_values(["s", "m"], ascending=False)["s"]
    elif s == "RRG LEADING":
        q = sig["quad"].loc[t]
        lanes = ["LEADING"] + (["IMPROVING"] if cfg.include_improving else [])
        ok = q[q.isin(lanes)].index
        c = ((sig["ratio"].loc[t, ok] - 100) + (sig["rmom"].loc[t, ok] - 100)).dropna().sort_values(ascending=False)
    elif s == "ROTATING IN":
        stg = sig["stage"].loc[t]
        ok = stg[stg == "ROTATING IN"].index
        c = sig["score"].loc[t, ok].dropna().sort_values(ascending=False)
    else:
        raise ValueError(f"unknown strategy {s}")
    return list(c.index[:cfg.top_n])


def _target(cfg, sig, t, cols, rng):
    tgt = np.zeros(len(cols))
    if cfg.regime_filter and not bool(sig["regime"].get(t, True)):
        return tgt, 1.0, []
    names = pick_sectors(cfg, sig, t, rng)
    if names:
        slot = 1.0 / len(names) if cfg.strategy == "EQUAL WEIGHT" else 1.0 / max(cfg.top_n, 1)
        for n in names:
            tgt[cols.index(n)] = slot
    return tgt, 1.0 - tgt.sum(), names


def run_backtest(sig: dict, cfg: BTConfig, start=None, end=None, seed=None) -> dict:
    """Daily simulation. Returns returns / equity / weights / trades / benchmark returns over the window."""
    P, B = sig["P"], sig["B"]
    dates = P.index
    R = np.nan_to_num((P / P.shift(1) - 1).to_numpy(), nan=0.0)
    rbv = np.nan_to_num((B / B.shift(1) - 1).to_numpy(), nan=0.0)
    cash_d = (1 + cfg.cash_yield / 100) ** (1 / TD) - 1
    def_r = rbv if cfg.defensive == "BENCH" else np.full(len(dates), cash_d)
    m = np.ones(len(dates), dtype=bool)
    if start is not None:
        m &= dates >= pd.Timestamp(start)
    if end is not None:
        m &= dates <= pd.Timestamp(end)
    pos = np.where(m)[0]
    if len(pos) < 30:
        raise ValueError("window has fewer than 30 sessions")
    rebal = set(rebalance_dates(dates[pos], cfg.rebalance)) | {dates[pos[0]]}
    cols = list(P.columns)
    rng = np.random.default_rng(seed) if cfg.strategy == "RANDOM" else None
    w, wd = np.zeros(len(cols)), 1.0
    pending = None
    out = np.zeros(len(pos))
    W = np.zeros((len(pos), len(cols)))
    WD = np.zeros(len(pos))
    trades = []
    for j, i in enumerate(pos):
        pr = 0.0
        if j > 0:
            g, gd = w * R[i], wd * def_r[i]
            pr = g.sum() + gd
            w, wd = (w + g) / (1 + pr), (wd + gd) / (1 + pr)
        cost = 0.0
        if dates[i] in rebal:
            tgt, tdef, names = _target(cfg, sig, dates[i], cols, rng)
            pending = (i + cfg.exec_lag, tgt, tdef, names, dates[i])
        if pending is not None and pending[0] == i:
            _, tgt, tdef, names, sdate = pending
            traded = float(np.abs(tgt - w).sum() + (abs(tdef - wd) if cfg.defensive == "BENCH" else 0.0))
            cost = traded * cfg.cost_bps / 1e4
            if traded > 1e-9 or not trades:
                trades.append({"signal_date": sdate, "trade_date": dates[i], "holdings": names, "turnover": float(np.abs(tgt - w).sum()),
                               "defensive_pct": tdef * 100})
            w, wd, pending = tgt.copy(), tdef, None
        out[j] = (1 + pr) * (1 - cost) - 1
        W[j], WD[j] = w, wd
    idx = dates[pos]
    r = pd.Series(out, index=idx, name="strategy")
    br = pd.Series(rbv[pos], index=idx, name="benchmark")
    br.iloc[0] = 0.0
    years = len(pos) / TD
    wdf = pd.DataFrame(W, index=idx, columns=cols)
    return {"returns": r, "equity": (1 + r).cumprod(), "bench_returns": br, "weights": wdf, "defensive": pd.Series(WD, index=idx),
            "trades": trades, "turnover_yr": sum(t["turnover"] for t in trades) / max(years, 1e-9) * 100,
            "avg_holdings": float((wdf > 1e-9).sum(axis=1).mean()), "exposure": float(wdf.sum(axis=1).mean() * 100), "cfg": cfg}


def current_picks(sig: dict, cfg: BTConfig) -> dict:
    t = sig["P"].index[-1]
    risk_on = bool(sig["regime"].get(t, True))
    names = [] if (cfg.regime_filter and not risk_on) else pick_sectors(cfg, sig, t, np.random.default_rng(0))
    return {"date": t, "holdings": names, "risk_on": risk_on}


# ═════════════════════════ metrics ═════════════════════════
def metrics(r: pd.Series, bench_r: pd.Series | None = None, cash_yield: float = 6.0) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / TD
    rf = (1 + cash_yield / 100) ** (1 / TD) - 1
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    sd = r.std()
    down = np.sqrt(np.mean(np.minimum(r - rf, 0) ** 2))
    dd = eq / eq.cummax() - 1
    uw = (dd < 0).astype(int)
    longest = int(uw.groupby((uw == 0).cumsum()).sum().max()) if len(uw) else 0
    out = {"CAGR %": cagr * 100, "Total %": (eq.iloc[-1] - 1) * 100, "Vol %": sd * math.sqrt(TD) * 100,
           "Sharpe": (r.mean() - rf) / sd * math.sqrt(TD) if sd else np.nan,
           "Sortino": (r.mean() - rf) / down * math.sqrt(TD) if down else np.nan,
           "Max DD %": dd.min() * 100, "Longest DD (days)": longest,
           "Calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan, "Years": yrs}
    if bench_r is not None:
        b = bench_r.reindex(r.index).fillna(0)
        ex = r - b
        te = ex.std() * math.sqrt(TD)
        vb = b.var()
        beta = r.cov(b) / vb if vb else np.nan
        bc = (1 + b).prod() ** (1 / yrs) - 1
        mr, mb = (1 + r).resample("ME").prod(), (1 + b).resample("ME").prod()
        out.update({"Bench CAGR %": bc * 100, "Excess CAGR %": (cagr - bc) * 100, "Beta": beta,
                    "Alpha %/yr": (r.mean() - beta * b.mean()) * TD * 100 if beta == beta else np.nan,
                    "Tracking err %": te * 100, "Info ratio": ex.mean() * TD / te if te else np.nan,
                    "Months beating bench %": float((mr > mb).mean() * 100)})
    return out


def yearly_returns(series: dict) -> pd.DataFrame:
    df = pd.DataFrame({k: (1 + v).groupby(v.index.year).prod() - 1 for k, v in series.items()}) * 100
    df.index.name = "Year"
    return df


def rolling_excess(r: pd.Series, b: pd.Series, window=TD) -> pd.Series:
    a = (1 + r).rolling(window).apply(np.prod, raw=True)
    c = (1 + b.reindex(r.index).fillna(0)).rolling(window).apply(np.prod, raw=True)
    return (a / c - 1) * 100


# ═════════════════════════ robustness ═════════════════════════
def param_sweep(sig, cfg, lookbacks=(21, 42, 63, 126, 252), top_ns=(1, 2, 3, 4, 5), start=None, end=None) -> pd.DataFrame:
    rows = []
    for lb in lookbacks:
        s2 = with_lookback(sig, lb, cfg.skip)
        for n in top_ns:
            c = replace(cfg, lookback=lb, top_n=n)
            try:
                res = run_backtest(s2, c, start, end)
            except ValueError:
                continue
            rows.append({"lookback": lb, "top_n": n, **metrics(res["returns"], res["bench_returns"], c.cash_yield),
                         "Turnover %/yr": res["turnover_yr"]})
    return pd.DataFrame(rows)


def walk_forward(sig, cfg, train_years=3, lookbacks=(21, 63, 126, 252), top_ns=(2, 3, 4), pick_by="Sharpe") -> dict:
    """Each calendar year: choose lookback/top-N using ONLY the previous `train_years` years, then trade that year.
    The stitched out-of-sample record is the honest estimate of what the rule would have delivered."""
    dates = sig["P"].index
    first_ok = sig["mom"].dropna(how="all").index.min()
    if first_ok is None or pd.isna(first_ok):
        return {}
    years = sorted(set(dates.year))
    chunks, bchunks, log = [], [], []
    for y in years:
        tr_s, tr_e = pd.Timestamp(y - train_years, 1, 1), pd.Timestamp(y - 1, 12, 31)
        if tr_s < first_ok - pd.Timedelta(days=10):
            continue
        grid = param_sweep(sig, cfg, lookbacks, top_ns, tr_s, tr_e)
        if grid.empty or pick_by not in grid:
            continue
        best = grid.sort_values(pick_by, ascending=False).iloc[0]
        c = replace(cfg, lookback=int(best["lookback"]), top_n=int(best["top_n"]))
        try:
            res = run_backtest(with_lookback(sig, c.lookback, c.skip), c, pd.Timestamp(y, 1, 1), pd.Timestamp(y, 12, 31))
        except ValueError:
            continue
        chunks.append(res["returns"])
        bchunks.append(res["bench_returns"])
        log.append({"Year": y, "Chosen lookback": c.lookback, "Chosen top N": c.top_n, f"In-sample {pick_by}": float(best[pick_by]),
                    "Out-of-sample %": float(((1 + res["returns"]).prod() - 1) * 100),
                    "Benchmark %": float(((1 + res["bench_returns"]).prod() - 1) * 100)})
    if not chunks:
        return {}
    r, b = pd.concat(chunks), pd.concat(bchunks)
    lg = pd.DataFrame(log)
    lg["Excess pp"] = lg["Out-of-sample %"] - lg["Benchmark %"]
    return {"returns": r, "bench_returns": b, "log": lg, "metrics": metrics(r, b, cfg.cash_yield)}


def random_baseline(sig, cfg, runs=100, start=None, end=None, seed=11) -> pd.DataFrame:
    """Same schedule, same N, same costs — but sectors picked at random. Shows what luck alone delivers."""
    c = replace(cfg, strategy="RANDOM", regime_filter=False)
    rows = []
    for k in range(runs):
        try:
            res = run_backtest(sig, c, start, end, seed=seed + k)
        except ValueError:
            continue
        m = metrics(res["returns"], res["bench_returns"], c.cash_yield)
        rows.append({"CAGR %": m.get("CAGR %"), "Sharpe": m.get("Sharpe"), "Max DD %": m.get("Max DD %")})
    return pd.DataFrame(rows)


def sensitivity(sig, cfg, field: str, values, start=None, end=None) -> pd.DataFrame:
    rows = []
    for v in values:
        c = replace(cfg, **{field: v})
        try:
            res = run_backtest(sig, c, start, end)
        except ValueError:
            continue
        m = metrics(res["returns"], res["bench_returns"], c.cash_yield)
        rows.append({field: v, "CAGR %": m.get("CAGR %"), "Excess CAGR %": m.get("Excess CAGR %"), "Sharpe": m.get("Sharpe"),
                     "Max DD %": m.get("Max DD %")})
    return pd.DataFrame(rows)


# ═════════════════════════ signal tests ═════════════════════════
def forward_excess(sig, h=21) -> pd.DataFrame:
    """Return of each sector over the NEXT h sessions minus the benchmark's, in percentage points."""
    P, B = sig["P"], sig["B"]
    return ((P.shift(-h) / P - 1).sub(B.shift(-h) / B - 1, axis=0)) * 100


def test_dates(sig, h=21, start=None):
    """Non-overlapping test dates (every h sessions) so the statistics are not inflated by overlap."""
    idx = sig["score"].dropna(how="all").index
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    idx = idx[: max(len(idx) - h, 0)]
    return idx[::h]


def information_coefficient(signal: pd.DataFrame, fwd: pd.DataFrame, dates) -> tuple:
    vals = {}
    for t in dates:
        a, b = signal.loc[t], fwd.loc[t]
        ok = a.notna() & b.notna()
        if ok.sum() >= 5 and a[ok].nunique() > 1:
            vals[t] = a[ok].rank().corr(b[ok].rank())
    ic = pd.Series(vals, dtype=float)
    n = len(ic)
    summ = {"Mean IC": ic.mean() if n else np.nan, "IC t-stat": ic.mean() / ic.std() * math.sqrt(n) if n > 2 and ic.std() else np.nan,
            "% periods IC > 0": float((ic > 0).mean() * 100) if n else np.nan, "Periods": n}
    return ic, summ


def stage_forward_table(sig, fwd, dates) -> pd.DataFrame:
    st_ = sig["stage"].loc[dates].stack()
    fw = fwd.loc[dates].stack()
    df = pd.DataFrame({"stage": st_, "fwd": fw}).dropna()
    df = df[df["stage"] != ""]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("stage")["fwd"]
    out = pd.DataFrame({"Avg next-period excess (pp)": g.mean(), "Median (pp)": g.median(),
                        "Beat benchmark %": g.apply(lambda s: (s > 0).mean() * 100), "Observations": g.size()})
    return out.reindex([s for s in STAGE_ORDER if s in out.index])


def score_bucket_table(sig, fwd, dates, bins=(0, 40, 55, 70, 85, 100.01)) -> pd.DataFrame:
    sc = sig["score"].loc[dates].stack()
    fw = fwd.loc[dates].stack()
    df = pd.DataFrame({"score": sc, "fwd": fw}).dropna()
    if df.empty:
        return pd.DataFrame()
    labels = [f"{int(bins[i])}–{int(min(bins[i + 1], 100))}" for i in range(len(bins) - 1)]
    df["bucket"] = pd.cut(df["score"], bins=list(bins), labels=labels, right=False)
    g = df.groupby("bucket", observed=True)["fwd"]
    return pd.DataFrame({"Avg next-period excess (pp)": g.mean(), "Beat benchmark %": g.apply(lambda s: (s > 0).mean() * 100),
                         "Observations": g.size()})


# ═════════════════════════ rotation lenses ═════════════════════════
CYCLICAL_RX = re.compile(r"\b(METAL|AUTO|REALTY|BANK|FIN|INFRA|ENERGY|OIL|CAPITAL|CONSTRUCTION|PSE|PSU|COMMODIT|MINING|CHEMICAL|DURABLE|MEDIA)")
DEFENSIVE_RX = re.compile(r"\b(FMCG|PHARMA|HEALTH|IT\b|INFORMATION|FAST MOVING|TELECOM|UTILIT|POWER|CONSUM)")


def classify(name: str) -> str:
    n = str(name).upper()
    if CYCLICAL_RX.search(n):
        return "CYCLICAL"
    if DEFENSIVE_RX.search(n):
        return "DEFENSIVE"
    return "OTHER"


def momentum_table(sig) -> pd.DataFrame:
    P, B = sig["P"], sig["B"]
    rows = []
    for s in P.columns:
        c = P[s].dropna()
        if len(c) < 64:
            continue
        b = B.reindex(c.index)
        f = lambda k: (c.iloc[-1] / c.iloc[-1 - k] - 1) * 100 if len(c) > k else np.nan
        fb = lambda k: (b.iloc[-1] / b.iloc[-1 - k] - 1) * 100 if len(b) > k else np.nan
        vol = c.pct_change().tail(126).std() * math.sqrt(TD) * 100
        r6 = f(126)
        rows.append({"sector": s, "group": classify(s), "1M %": f(21), "3M %": f(63), "6M %": r6,
                     "12-1M %": ((c.iloc[-22] / c.iloc[-253] - 1) * 100) if len(c) > 253 else np.nan,
                     "3M vs bench pp": f(63) - fb(63), "Vol %": vol, "6M ÷ vol": r6 / vol if vol and r6 == r6 else np.nan})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    ranks = df[["1M %", "3M %", "6M %", "12-1M %"]].rank(ascending=False)
    df["Composite rank"] = ranks.mean(axis=1).rank(method="min")
    return df.sort_values("Composite rank").reset_index(drop=True)


def rank_history(sig, lookback=21, weeks=26) -> pd.DataFrame:
    """Sector rank (1 = strongest relative return over `lookback` sessions) at each week end: sectors x weeks."""
    P, B = sig["P"], sig["B"]
    rel = (P / P.shift(lookback)).div(B / B.shift(lookback), axis=0) - 1
    wk = rel.groupby(rel.index.to_period("W-FRI")).tail(1).tail(weeks)
    rk = wk.rank(axis=1, ascending=False)
    rk.index = [d.strftime("%d %b") for d in wk.index]
    order = rk.iloc[-1].sort_values().index
    return rk.T.loc[order]


def risk_appetite(sig) -> pd.DataFrame:
    """Equal-weight cyclical sectors ÷ equal-weight defensive sectors (rising = money taking more risk)."""
    P = sig["P"]
    grp = {c: classify(c) for c in P.columns}
    cyc = [c for c, g in grp.items() if g == "CYCLICAL"]
    dfn = [c for c, g in grp.items() if g == "DEFENSIVE"]
    if not cyc or not dfn:
        return pd.DataFrame()
    r = P / P.shift(1) - 1
    ic = (1 + r[cyc].mean(axis=1).fillna(0)).cumprod()
    idf = (1 + r[dfn].mean(axis=1).fillna(0)).cumprod()
    ratio = ic / idf * 100
    out = pd.DataFrame({"ratio": ratio, "sma50": ratio.rolling(50).mean(), "sma200": ratio.rolling(200).mean()})
    out.attrs["cyclical"], out.attrs["defensive"] = cyc, dfn
    return out


def dispersion(sig, window=21) -> pd.Series:
    """Cross-sector spread of `window`-session returns (pp). High = sectors moving apart = rotation pays more."""
    P = sig["P"]
    return ((P / P.shift(window) - 1) * 100).std(axis=1).dropna()


def snapshot(sig) -> pd.DataFrame:
    """Latest-session row per sector. Keys match mf_ui (quadrant, stage, score, rsi_d, r1w, ab10.., dist10..)."""
    P = sig["P"]
    t = P.index[-1]
    last = P.iloc[-1]
    df = pd.DataFrame({"sector": P.columns, "stage": sig["stage"].loc[t].values, "quadrant": sig["quad"].loc[t].values,
                       "score": sig["score"].loc[t].values, "rrg_x": sig["ratio"].loc[t].values, "rrg_y": sig["rmom"].loc[t].values,
                       "rsi_d": sig["rsi"].loc[t].values, "mom": sig["mom"].loc[t].values,
                       "r1d": ((last / P.iloc[-2] - 1) * 100).values if len(P) > 1 else np.nan,
                       "r1w": ((last / P.iloc[-6] - 1) * 100).values if len(P) > 5 else np.nan,
                       "r1m": ((last / P.iloc[-22] - 1) * 100).values if len(P) > 21 else np.nan})
    for n in (10, 20, 50, 200):
        e = _ema(P, n).iloc[-1]
        df[f"ab{n}"] = (last > e).where(e.notna()).values
        df[f"dist{n}"] = ((last / e - 1) * 100).values
    df["stage"] = df["stage"].replace("", "—")
    return df.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)


# ═════════════════════════ NSDL fortnightly sector-wise FPI data ═════════════════════════
NSDL_BASE = "https://www.fpi.nsdl.co.in/web/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/"


def nsdl_report_dates(n=24, today=None) -> list:
    """Fortnight end dates (15th and month end), newest first, at least 3 days old."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out, m = [], today.to_period("M")
    while len(out) < n:
        me = m.to_timestamp(how="end").normalize()
        for d in (me, pd.Timestamp(m.year, m.month, 15)):
            if d <= today - pd.Timedelta(days=3) and len(out) < n:
                out.append(d)
        m -= 1
    return out


def nsdl_urls(d) -> list:
    d = pd.Timestamp(d)
    v = [d.strftime("%B"), d.strftime("%b"), d.strftime("%b").upper(), d.strftime("%B").upper()]
    if d.month == 9:
        v += ["Sept", "SEPT"]
    return [f"{NSDL_BASE}FIIInvestSector_{m}{d.day:02d}{d.year}.html" for m in dict.fromkeys(v)]


def _parse_mdy(mon, day, year):
    mon = mon.strip().rstrip(".")
    mon = {"Sept": "Sep", "SEPT": "SEP"}.get(mon, mon)
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return pd.to_datetime(f"{mon.title()} {int(day)} {year}", format=fmt)
        except ValueError:
            continue
    return None


def parse_nsdl_sector_html(text: str) -> pd.DataFrame:
    """Long frame [date, sector, kind ('net' | 'auc'), value (INR crore, equity)] from one NSDL report."""
    tables = []
    for kw in ({}, {"header": [0, 1, 2]}):
        try:
            tables = pd.read_html(StringIO(text), **kw)
        except Exception:
            tables = []
        recs = []
        for t in tables:
            cols = [" | ".join(str(x) for x in (c if isinstance(c, tuple) else (c,))) for c in t.columns]
            sec = next((c for c in cols if "sector" in c.lower()), None)
            if sec is None:
                continue
            t = t.copy()
            t.columns = cols
            for c in cols:
                parts = [p.strip() for p in c.split(" | ")]
                if parts[-1].lower() != "equity" or not any("inr" in p.lower() for p in parts):
                    continue
                head = parts[0]
                mm = re.search(r"Net\s+Investment\s+([A-Za-z]+)\.?\s+\d+\s*-\s*(\d+)\s*,?\s*(\d{4})", head, re.I)
                kind = "net"
                if not mm:
                    mm = re.search(r"AUC\s+as\s+on\s+([A-Za-z]+)\.?\s+(\d+)\s*,?\s*(\d{4})", head, re.I)
                    kind = "auc"
                if not mm:
                    continue
                dt = _parse_mdy(mm.group(1), mm.group(2), mm.group(3))
                if dt is None:
                    continue
                vals = pd.to_numeric(t[c].astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")
                for s_, v in zip(t[sec].astype(str), vals):
                    s_ = s_.strip()
                    if s_ and s_.lower() != "nan" and pd.notna(v):
                        recs.append({"date": dt, "sector": s_, "kind": kind, "value": float(v)})
        if recs:
            return pd.DataFrame(recs).drop_duplicates(["date", "sector", "kind"], keep="last")
    return pd.DataFrame(columns=["date", "sector", "kind", "value"])


def nsdl_panels(parsed: list) -> tuple:
    """Combine parsed reports -> (net flows dates x sectors, AUC dates x sectors), INR crore."""
    frames = [p for p in parsed if p is not None and not p.empty]
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.concat(frames).drop_duplicates(["date", "sector", "kind"], keep="first")
    df = df[~df["sector"].str.contains(r"total|sovereign|others", case=False, regex=True)]
    net = df[df.kind == "net"].pivot_table(index="date", columns="sector", values="value", aggfunc="last").sort_index()
    auc = df[df.kind == "auc"].pivot_table(index="date", columns="sector", values="value", aggfunc="last").sort_index()
    return net, auc


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())
