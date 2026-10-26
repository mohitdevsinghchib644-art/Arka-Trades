"""
sector_engine.py — Arka stock-universe SECTOR analytics v3 (pure functions, no network).

Input : {"SYM.NS": OHLCV DataFrame, "^NSEI": benchmark}  +  meta DataFrame[symbol, industry, company]
        optional benches = {"NIFTY 50": Series, "MIDCAP 100": Series, "SMALLCAP 100": Series} (daily closes)
Output: per-sector table, per-stock table, sector index series, benchmark series, breadth series,
        data-quality report, and helpers for benchmark comparison, rotation detection and AUDIT.

WHAT CHANGED IN v3 (why sector numbers could be wrong in v2)
-------------------------------------------------------------
1. LATEST-SESSION COVERAGE GUARD.  v2 used the newest date found in ANY ticker. When only part of the
   universe has published today's bar, every other stock was forward-filled as a 0.00% day, so the sector
   index barely moved while the real market did (tested: true +2.5% day shown as +0.25%). Sectors that had
   actually rallied above their 10/20-EMA still printed red. v3 only analyses sessions where at least
   60% of the usual universe has a real bar, drops the incomplete tail, and reports `quality`.
2. NO-BAR != 0% RETURN.  A stock with no bar on a date is now EXCLUDED from that day's sector average
   instead of contributing a fake 0.00%.
3. BAD-TICK / SPLIT REPAIR.  One bad print in one of 40 stocks moved a v2 sector index by +236% in testing.
   v3 detects (a) bad ticks that revert next day -> repaired; (b) split/bonus-like persistent steps
   (x0.5, x1/3, x0.25, x0.2, x0.1, x2/3 and reverse) -> history back-adjusted; (c) any other > 60% jump ->
   excluded from the average. Remaining daily returns are clipped to +/-25% (NSE circuit scale).
   Every repair is listed in quality["events"].
4. LIQUIDITY-WEIGHTED SECTOR INDEX (default).  Official NSE sector indices are cap-weighted, so large names
   lead them. An equal-weight index of every listed stock (incl. micro-caps) can sit below its 10-EMA while
   the official index is above it. v3 weights each stock by its prior 20-day average traded value
   (capped at max(25%, 2/n) per stock) — a close proxy for free-float cap. weighting="equal" restores v2 behaviour.
5. TRADINGVIEW-PARITY INDICATORS.  EMA and Wilder-RSI are SMA-seeded exactly like TradingView's ta.ema / ta.rsi
   (v2 seeded with the first value, which biased the EMA-200 by up to ~8% on a 1-year window).
6. AUDIT TOOLS.  self_check(res) re-derives every flag/distance and reports mismatches;
   explain_sector(res, sector) returns the raw numbers behind a row (index level, each EMA, last closes,
   per-stock contribution today) so any value can be checked against a chart in seconds.

Definitions (unchanged)
  RSI(14) Wilder (daily + weekly) of the sector index · EMA 10/20/50/200 flags + % distance
  Turnover = close x volume of the latest session (Rs crore, Yahoo volume ≈ NSE value)
  Share % = sector turnover / universe turnover · Chaikin CMF(20) · % stocks > 20-EMA (breadth)
  Rotation (RRG-style, simplified): RS-Ratio = 100 x RS / SMA(RS,50); RS-Momentum = 100 x ratio / ratio[10 sessions ago]
  Lanes LEADING · FADING · WEAK · IMPROVING;  score 0-100 (trend 30, momentum 20, relative 25, breadth 15, flow 10)
  Stages ROTATING IN · LEADING · EXTENDED · IMPROVING · FADING · WEAK

Honest limits: "sector" = NSE industry classification from the index constituent files; turnover is not
exchange-reported value; scores and stages are evidence from price, breadth and volume - not forecasts.
With only ~1 year of history the EMA-200 is approximate (quality["ema200_reliable"] tells you).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_STOCKS_PER_SECTOR = 3
EMA_SPANS = (10, 20, 50, 200)
RS_TREND, RS_MOM = 50, 10
TRAIL_STEP, TRAIL_LEN = 5, 6
EXTENDED_PCT = 6.0
EXTENDED_RSI = 72.0
MARKET = "MARKET AVG"        # equal-weight average of every stock in the universe: the fair yardstick for sector rotation
BENCH_ORDER = (MARKET, "NIFTY 50", "MIDCAP 100", "SMALLCAP 100")
COMPARE = ("NIFTY 50", "MIDCAP 100", "SMALLCAP 100")      # benchmarks shown in the comparison charts / tables
BK = {MARKET: "mkt", "NIFTY 50": "n50", "MIDCAP 100": "m100", "SMALLCAP 100": "s100"}
STAGES = ("ROTATING IN", "LEADING", "EXTENDED", "IMPROVING", "FADING", "WEAK")
LANE_VALUE = {"LEADING": 3, "IMPROVING": 2, "FADING": 1, "WEAK": 0}

# data-quality controls
MIN_COVERAGE = 0.60          # a session counts only if >= 60% of the usual universe has a real bar
CLIP_PCT = 25.0              # daily-return clip used for index construction
DROP_PCT = 60.0              # single-day moves beyond this are never treated as real returns
SPLIT_RATIOS = (0.5, 1 / 3, 0.25, 0.2, 0.1, 2 / 3, 2.0, 3.0, 4.0, 5.0, 10.0)
SPLIT_TOL = 0.04
SPLIT_MIN_MOVE = 30.0
REVERT_TOL = 0.08
WEIGHT_CAP_MIN = 0.25
ST_BAD_TICK, ST_CORP, ST_EXCLUDED = "bad tick (repaired)", "split/bonus-like (history adjusted)", "unexplained jump (excluded)"


# ───────────────────────── small helpers ─────────────────────────
def norm_series(s) -> pd.Series:
    """Daily close series -> float, tz-naive, midnight-normalised, unique, sorted."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s = s.copy()
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index().astype(float)


def _seeded_ewm(df: pd.DataFrame, n: int, alpha: float) -> pd.DataFrame:
    """Recursive average seeded with the SMA of the first n valid values (TradingView's ta.ema / ta.rma)."""
    cnt = df.notna().cumsum()
    seed = df.rolling(n, min_periods=n).mean()
    x = df.where(cnt > n).where(cnt != n, seed)
    return x.ewm(alpha=alpha, adjust=False).mean()


def ema_df(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return _seeded_ewm(df, n, 2.0 / (n + 1))


def ema_series(s: pd.Series, n: int) -> pd.Series:
    return ema_df(s.to_frame("x"), n)["x"]


def rsi_series(close, n: int = 14):
    """Wilder RSI, SMA-seeded like TradingView (works on a Series or a DataFrame)."""
    is_s = isinstance(close, pd.Series)
    d = close.to_frame("x") if is_s else close
    delta = d.diff()
    up = _seeded_ewm(delta.clip(lower=0).where(delta.notna()), n, 1.0 / n)
    dn = _seeded_ewm((-delta.clip(upper=0)).where(delta.notna()), n, 1.0 / n)
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out = out.where(~((dn == 0) & up.notna()), 100.0)
    return out["x"] if is_s else out


def crossed_up(a: pd.Series, b, lookback: int) -> bool:
    """True if `a` moved from <= b to > b within the last `lookback` sessions and is above b now."""
    if not isinstance(b, pd.Series):
        b = pd.Series(float(b), index=a.index)
    d = (a - b).dropna()
    if len(d) < lookback + 1:
        return False
    w = d.iloc[-(lookback + 1):]
    return bool(w.iloc[-1] > 0 and (w.iloc[:-1] <= 0).any())


def _last(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else np.nan


def _b(v):
    """True / False / None from a possibly-NaN flag."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return bool(v)


def _f(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ───────────────────────── technicals on one index series ─────────────────────────
def tech_block(c: pd.Series) -> dict:
    """RSI, EMA 10/20/50/200 flags + distances, and fresh-turn events for one price series."""
    keys = (["rsi_d", "rsi_d_5", "rsi_w", "e10_gt_20", "e20_gt_50", "x20_up", "x20_dn", "rsi50_up", "e1020_up", "idx_last"]
            + [f"ab{s}" for s in EMA_SPANS] + [f"dist{s}" for s in EMA_SPANS] + [f"ema{s}" for s in EMA_SPANS])
    out = {k: np.nan for k in keys}
    c = c.dropna()
    n = len(c)
    if n < 30:
        return out
    last = float(c.iloc[-1])
    out["idx_last"] = last
    ema = {s: ema_series(c, s) for s in EMA_SPANS}
    for s in EMA_SPANS:
        if n >= s and pd.notna(ema[s].iloc[-1]):
            e = float(ema[s].iloc[-1])
            out[f"ema{s}"] = e
            out[f"ab{s}"] = last > e
            out[f"dist{s}"] = (last / e - 1) * 100
    out["e10_gt_20"] = bool(ema[10].iloc[-1] > ema[20].iloc[-1])
    if n >= 50:
        out["e20_gt_50"] = bool(ema[20].iloc[-1] > ema[50].iloc[-1])
    r = rsi_series(c)
    out["rsi_d"] = _last(r)
    if len(r) > 6 and pd.notna(r.iloc[-6]):
        out["rsi_d_5"] = float(r.iloc[-6])
    w = c.resample("W-FRI").last().dropna()
    if len(w) > 15:
        out["rsi_w"] = _last(rsi_series(w))
    out["x20_up"] = crossed_up(c, ema[20], 5)
    out["x20_dn"] = crossed_up(ema[20], c, 5)
    out["rsi50_up"] = crossed_up(r, 50, 5)
    out["e1020_up"] = crossed_up(ema[10], ema[20], 10)
    return out


# ───────────────────────── data cleaning ─────────────────────────
def _prep_frame(df: pd.DataFrame) -> pd.DataFrame:
    """tz-naive normalised daily index, unique, sorted, positive prices only."""
    d = df[["High", "Low", "Close", "Volume"]].copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    for c in d.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[(d["Close"] > 0) & (d["High"] > 0) & (d["Low"] > 0)]
    return d


def _split_like(ret_pct: pd.DataFrame) -> pd.DataFrame:
    ratio = 1 + ret_pct / 100
    hit = pd.DataFrame(False, index=ret_pct.index, columns=ret_pct.columns)
    for f in SPLIT_RATIOS:
        hit |= ((ratio / f) - 1).abs() < SPLIT_TOL
    return hit & (ret_pct.abs() > SPLIT_MIN_MOVE)


def _repair_prices(close, high, low, ret_pct):
    """Repair bad ticks and split-like steps. Returns (close, high, low, excluded_mask, events)."""
    split = _split_like(ret_pct).to_numpy()
    suspect = (split | (ret_pct.abs() > DROP_PCT).fillna(False).to_numpy())
    C, H, L = close.to_numpy(copy=True), high.to_numpy(copy=True), low.to_numpy(copy=True)
    excluded = np.zeros(C.shape, dtype=bool)
    events = []
    ii, jj = np.where(suspect)
    for k in np.argsort(ii, kind="stable"):
        i, j = ii[k], jj[k]
        if i == 0:
            continue
        prev, cur = C[i - 1, j], C[i, j]
        if not (prev > 0 and cur > 0):
            continue
        ratio = cur / prev
        nxt = C[i + 1, j] if i + 1 < len(C) else np.nan
        if np.isfinite(nxt) and abs(nxt / prev - 1) < REVERT_TOL:          # spike that reverted: bad tick
            C[i, j] = H[i, j] = L[i, j] = prev
            kind = ST_BAD_TICK
        elif split[i, j]:                                                  # persistent step: corporate action
            C[:i, j] *= ratio
            H[:i, j] *= ratio
            L[:i, j] *= ratio
            kind = ST_CORP
        else:
            excluded[i, j] = True
            kind = ST_EXCLUDED
        events.append({"symbol": close.columns[j], "date": str(close.index[i])[:10], "move_pct": float((ratio - 1) * 100), "action": kind})
    mk = lambda a: pd.DataFrame(a, index=close.index, columns=close.columns)
    return mk(C), mk(H), mk(L), mk(excluded), events


# ───────────────────────── universe analysis ─────────────────────────
def analyse_universe(frames: dict, meta: pd.DataFrame, bench: str = "^NSEI", benches: dict | None = None,
                     weighting: str = "liquidity", min_coverage: float = MIN_COVERAGE) -> dict:
    meta = meta.drop_duplicates("symbol")
    clean = {}
    for s in meta["symbol"]:
        key = f"{s}.NS"
        if key in frames and frames[key] is not None and len(frames[key]) >= 60:
            try:
                d = _prep_frame(frames[key])
            except Exception:
                continue
            if len(d) >= 60:
                clean[s] = d
    usable = list(clean)
    if len(usable) < 30:
        raise ValueError(f"only {len(usable)} stocks returned usable history (need 30+)")

    idx_all = pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in clean.values()])))
    has_all = pd.DataFrame({s: pd.Series(True, index=clean[s].index) for s in usable}).reindex(idx_all).fillna(False).astype(bool)
    cover_all = has_all.sum(axis=1) / len(usable)

    # 1) coverage guard: only analyse sessions with (near) complete data
    thr = min_coverage * float(cover_all.tail(30).median())
    good = cover_all.index[cover_all >= thr]
    last_date = good.max()
    dropped_tail = [str(d)[:10] for d in idx_all[idx_all > last_date]]
    idx = idx_all[idx_all <= last_date]
    cover_last = float(cover_all.loc[last_date])

    syms = [s for s in usable if (last_date - clean[s].index[-1]).days <= 4 or clean[s].index[-1] > last_date]
    stale = sorted(set(usable) - set(syms))
    if len(syms) < 30:
        raise ValueError("too many stale tickers")

    pan = lambda col: pd.DataFrame({s: clean[s][col] for s in syms}).reindex(idx)
    close_raw = pan("Close")
    has_bar = close_raw.notna()
    close, high, low = close_raw.ffill(), pan("High").ffill(), pan("Low").ffill()
    vol = pan("Volume").fillna(0)

    ind = meta.set_index("symbol")["industry"].reindex(syms).fillna("Unclassified").astype(str)
    counts = ind.value_counts()
    ind = ind[ind.isin(counts[counts >= MIN_STOCKS_PER_SECTOR].index)]
    syms = ind.index.tolist()
    close, high, low, vol, has_bar = close[syms], high[syms], low[syms], vol[syms], has_bar[syms]

    # 3) repair bad ticks / splits, then returns (no bar => no return)
    ret_pre = ((close / close.shift(1) - 1) * 100).where(has_bar)
    close, high, low, excluded, events = _repair_prices(close, high, low, ret_pre)
    ret1_raw = ((close / close.shift(1) - 1) * 100).where(has_bar)            # honest daily % change of each stock
    ret1 = ret1_raw.mask(excluded).clip(-CLIP_PCT, CLIP_PCT)                   # used for index construction
    ret5 = (close / close.shift(5) - 1) * 100
    ret21 = (close / close.shift(21) - 1) * 100

    turn = close * vol
    emaS = {s: ema_df(close, s) for s in EMA_SPANS}
    ema20 = emaS[20]
    rsi = rsi_series(close)
    brk = close > high.rolling(20).max().shift(1)
    bo10 = brk.tail(10).any()
    rng = (high - low).replace(0, np.nan)
    mfv = ((((close - low) - (high - close)) / rng).fillna(0)) * vol
    cmf_stock = mfv.rolling(20).sum() / vol.rolling(20).sum().replace(0, np.nan)

    grp = lambda df: df.T.groupby(ind).sum().T                  # dates x sectors, summed across stocks
    n = ind.groupby(ind).size()

    # 4) sector index: liquidity-weighted (default) or equal-weight; weights lagged one session, capped
    def sector_return(weights: pd.DataFrame) -> pd.DataFrame:
        valid = ret1.notna()
        num = grp((weights * ret1).where(valid, 0.0))
        den = grp(weights.where(valid, 0.0))
        return (num / den.replace(0, np.nan)) / 100

    eq_w = pd.DataFrame(1.0, index=idx, columns=syms)
    eq_w = eq_w.div(grp(eq_w)[ind.values].set_axis(syms, axis=1))
    if weighting == "equal":
        wn = eq_w
    else:
        w = turn.rolling(20, min_periods=1).mean().shift(1).clip(lower=1.0).fillna(1.0)
        wn = w.div(grp(w)[ind.values].set_axis(syms, axis=1))
        cap = pd.Series(np.maximum(WEIGHT_CAP_MIN, 2.0 / ind.map(n).astype(float)).values, index=syms)
        wn = wn.clip(upper=cap, axis=1)
        wn = wn.div(grp(wn)[ind.values].set_axis(syms, axis=1))
    sec_ret = sector_return(wn)
    sec_idx = (1 + sec_ret.fillna(0)).cumprod() * 100
    sec_idx_eq = (1 + sector_return(eq_w).fillna(0)).cumprod() * 100

    # today's breadth / turnover split use the honest daily change
    r1_last, r5_last = ret1_raw.iloc[-1], ret5.iloc[-1]
    t_sec = grp(turn)
    share = t_sec.div(t_sec.sum(axis=1).replace(0, np.nan), axis=0) * 100
    cmf_sec = grp(mfv).rolling(20).sum() / grp(vol).rolling(20).sum().replace(0, np.nan)
    adv = (r1_last > 0).groupby(ind).sum()
    dec = (r1_last < 0).groupby(ind).sum()
    up_turn = turn.iloc[-1].where(r1_last > 0, 0).groupby(ind).sum()
    dn_turn = turn.iloc[-1].where(r1_last < 0, 0).groupby(ind).sum()
    t_last = t_sec.iloc[-1]
    t_avg20 = t_sec.iloc[-21:-1].mean()
    ab20_ser = (close > ema20).where(ema20.notna()).astype(float).T.groupby(ind).mean().T * 100
    cnt = close.count()
    flag, dist = {}, {}
    for s in EMA_SPANS:
        e = emaS[s].iloc[-1]
        ok = (cnt >= s) & e.notna()
        flag[s] = (close.iloc[-1] > e).where(ok)
        dist[s] = ((close.iloc[-1] / e - 1) * 100).where(ok)

    # benchmarks
    bser = {}
    if benches:
        for k, v in benches.items():
            s_ = norm_series(v)
            if len(s_) > 30:
                bser[k] = s_
    elif frames.get(bench) is not None:
        bser["NIFTY 50"] = norm_series(frames[bench]["Close"])
    bser[MARKET] = (1 + (ret1.mean(axis=1) / 100).fillna(0)).cumprod() * 100
    bench_idx = pd.DataFrame({k: bser[k].reindex(idx.union(bser[k].index)).ffill().reindex(idx)
                              for k in BENCH_ORDER if k in bser}, index=idx)

    def sret(k):
        return (sec_idx.iloc[-1] / sec_idx.iloc[-1 - k] - 1) * 100 if len(sec_idx) > k else pd.Series(np.nan, index=sec_idx.columns)

    leaders, tl = {}, turn.iloc[-1]
    for sec, members in ind.groupby(ind).groups.items():
        m = list(members)
        liquid = [s for s in m if tl[s] >= tl[m].quantile(.25)]
        leaders[sec] = ", ".join(r5_last[liquid].dropna().sort_values(ascending=False).head(3).index.tolist())

    f = lambda sr: sr.reindex(n.index).values
    sectors = pd.DataFrame({
        "sector": n.index, "stocks": n.values,
        "turnover_cr": f(t_last / 1e7), "turn_avg20_cr": f(t_avg20 / 1e7),
        "turn_vs20": f(t_last / t_avg20.replace(0, np.nan)),
        "r1d": f(sec_ret.iloc[-1] * 100), "r1w": f(sret(5)), "r1m": f(sret(21)), "r3m": f(sret(63)),
        "share": f(share.iloc[-1]),
        "dshare_d": f(share.iloc[-1] - share.iloc[-2]), "dshare_w": f(share.iloc[-1] - share.iloc[-6]),
        "adv": f(adv), "dec": f(dec), "ad_net": f((adv - dec) / n),
        "avg_rsi": f(rsi.iloc[-1].groupby(ind).mean()),
        "pct_above20": f(ab20_ser.iloc[-1]), "pct_above20_5": f(ab20_ser.iloc[-6]) if len(ab20_ser) > 6 else np.nan,
        "pct_above50": f(flag[50].astype(float).groupby(ind).mean() * 100),
        "pct_above200": f(flag[200].astype(float).groupby(ind).mean() * 100),
        "pct_bo10": f(bo10.groupby(ind).mean() * 100),
        "up_turn_cr": f(up_turn / 1e7), "dn_turn_cr": f(dn_turn / 1e7),
        "turn_ad": f(up_turn / dn_turn.replace(0, np.nan)),
        "pct_turn_adv": f(up_turn / (up_turn + dn_turn).replace(0, np.nan) * 100),
        "cmf": f(cmf_sec.iloc[-1]), "cmf_5d": f(cmf_sec.iloc[-6]) if len(cmf_sec) > 6 else np.nan,
        "leaders": [leaders.get(s, "") for s in n.index],
    })
    tech = pd.DataFrame([{**tech_block(sec_idx[s]), "sector": s} for s in n.index if s in sec_idx.columns])
    sectors = sectors.merge(tech, on="sector", how="left")
    sectors = sectors.replace([np.inf, -np.inf], np.nan).sort_values("turnover_cr", ascending=False).reset_index(drop=True)

    stocks = pd.DataFrame({
        "symbol": syms, "sector": ind.values, "price": close.iloc[-1].values,
        "turnover_cr": (turn.iloc[-1] / 1e7).values, "r1d": r1_last.values, "r1w": r5_last.values, "r1m": ret21.iloc[-1].values,
        "rsi": rsi.iloc[-1].values, "bo10": bo10.values, "cmf": cmf_stock.iloc[-1].values,
        **{f"ab{s}": flag[s].values for s in EMA_SPANS}, **{f"dist{s}": dist[s].values for s in EMA_SPANS},
    }).replace([np.inf, -np.inf], np.nan)

    contrib = pd.DataFrame({"symbol": syms, "sector": ind.values, "weight": wn.iloc[-1].values * 100,
                            "ret1": ret1.iloc[-1].values, "has_bar": has_bar.iloc[-1].values})
    contrib["contrib_pp"] = contrib["weight"] * contrib["ret1"].fillna(0) / 100

    sparks = {}
    for s in sec_idx.columns:
        t = sec_idx[s].dropna().tail(40)
        if len(t) > 5:
            sparks[s] = (t / t.iloc[0] * 100).round(2).tolist()

    quality = {
        "asof": str(last_date)[:10],
        "coverage": cover_last,                                   # share of the universe with a REAL bar on the last session
        "coverage_ok": bool(cover_last >= 0.85),
        "dropped_partial_sessions": dropped_tail,
        "no_bar_today": int((~has_bar.iloc[-1]).sum()),
        "events": events[:60], "n_events": len(events),
        "weighting": weighting,
        "history_sessions": int(len(idx)),
        "ema200_reliable": bool(len(idx) >= 400),
        "method": ("Liquidity-weighted (prior 20-day traded value, per-stock cap max(25%, 2/n))" if weighting != "equal" else "Equal-weight")
                  + ", daily-rebalanced; no-bar days excluded; bad-tick/split repair; returns clipped ±25%; TradingView-style EMA/RSI seeding.",
    }
    return {"sectors": sectors, "stocks": stocks, "sparks": sparks,
            "sector_index": sec_idx, "sector_index_equal": sec_idx_eq, "bench_index": bench_idx, "ab20_series": ab20_ser,
            "cmf_series": cmf_sec.tail(60), "share_series": share.tail(60),
            "contrib_last": contrib, "quality": quality,
            "asof": str(last_date)[:10], "n_stocks": len(syms), "stale": stale,
            "unmapped": sorted(set(meta["symbol"]) - set(usable))}


# ───────────────────────── audit tools ─────────────────────────
def self_check(res: dict, tol: float = 1e-6) -> list:
    """Re-derive every EMA flag / distance from the sector index and compare with the table. Empty list = consistent."""
    problems = []
    si, sec = res["sector_index"], res["sectors"]
    for rec in sec.to_dict("records"):
        s = rec["sector"]
        if s not in si.columns:
            continue
        t = tech_block(si[s])
        for n_ in EMA_SPANS:
            a, b = _b(rec.get(f"ab{n_}")), _b(t.get(f"ab{n_}"))
            if a != b:
                problems.append(f"{s}: EMA{n_} flag table={a} recomputed={b}")
            d1, d2 = _f(rec.get(f"dist{n_}")), _f(t.get(f"dist{n_}"))
            if (d1 is None) != (d2 is None) or (d1 is not None and abs(d1 - d2) > tol):
                problems.append(f"{s}: EMA{n_} distance table={d1} recomputed={d2}")
            if d1 is not None and a is not None and (d1 > 0) != a:
                problems.append(f"{s}: EMA{n_} chip colour contradicts distance ({d1:+.2f}%)")
    return problems


def explain_sector(res: dict, sector: str, last_n: int = 8) -> dict:
    """Raw numbers behind one sector row — compare these with a chart to verify any value."""
    si = res["sector_index"][sector].dropna()
    ema = {n_: ema_series(si, n_) for n_ in EMA_SPANS}
    last = float(si.iloc[-1])
    rows = []
    for d in si.index[-last_n:]:
        rows.append({"date": str(d)[:10], "index": float(si.loc[d]), **{f"ema{n_}": float(ema[n_].loc[d]) if pd.notna(ema[n_].loc[d]) else None for n_ in EMA_SPANS}})
    c = res["contrib_last"]
    c = c[c["sector"] == sector].sort_values("contrib_pp", ascending=False)
    q = res["quality"]
    return {
        "sector": sector, "asof": res["asof"], "method": q["method"], "coverage_last_session": q["coverage"],
        "index_last": last,
        "ema": {n_: (float(ema[n_].iloc[-1]) if pd.notna(ema[n_].iloc[-1]) else None) for n_ in EMA_SPANS},
        "distance_pct": {n_: ((last / float(ema[n_].iloc[-1]) - 1) * 100 if pd.notna(ema[n_].iloc[-1]) else None) for n_ in EMA_SPANS},
        "above": {n_: (last > float(ema[n_].iloc[-1]) if pd.notna(ema[n_].iloc[-1]) else None) for n_ in EMA_SPANS},
        "recent": pd.DataFrame(rows),
        "equal_weight_distance_pct_ema10": float((res["sector_index_equal"][sector].iloc[-1] / ema_series(res["sector_index_equal"][sector].dropna(), 10).iloc[-1] - 1) * 100),
        "top_contributors": c.head(5)[["symbol", "weight", "ret1", "contrib_pp"]],
        "top_detractors": c.tail(5)[["symbol", "weight", "ret1", "contrib_pp"]].iloc[::-1],
        "stocks_with_bar_today": int(c["has_bar"].sum()), "stocks_total": int(len(c)),
    }


# ───────────────────────── benchmark comparison ─────────────────────────
def _cmp(res: dict) -> pd.DataFrame:
    """Benchmarks used for the comparison charts and tables (NIFTY 50 / MIDCAP 100 / SMALLCAP 100)."""
    bi = res["bench_index"]
    return bi[[b for b in COMPARE if b in bi.columns]]


def returns_vs_benchmarks(res: dict):
    """Sector returns (1W/1M/3M) and excess return in pp versus every benchmark.
    Returns (DataFrame[sector, r1w, r1m, r3m, x1w_n50, x1m_m100 ...], {period: {bench: return}})."""
    si, bi = res["sector_index"], _cmp(res)
    out = pd.DataFrame(index=si.columns)
    bench_ret = {}
    for p, k in (("1w", 5), ("1m", 21), ("3m", 63)):
        rs = (si.iloc[-1] / si.iloc[-1 - k] - 1) * 100 if len(si) > k else pd.Series(np.nan, index=si.columns)
        out[f"r{p}"] = rs
        bench_ret[p] = {}
        for b in bi.columns:
            rb = float((bi[b].iloc[-1] / bi[b].iloc[-1 - k] - 1) * 100) if len(bi) > k and pd.notna(bi[b].iloc[-1 - k]) else np.nan
            bench_ret[p][b] = rb
            out[f"x{p}_{BK[b]}"] = rs - rb
    out.index.name = "sector"
    return out.reset_index(), bench_ret


def rebased(res: dict, sector: str, sessions: int) -> pd.DataFrame:
    """Sector + NIFTY 50 / MIDCAP 100 / SMALLCAP 100 rebased to 100 at the start of the window."""
    si, bi = res["sector_index"], _cmp(res)
    df = pd.concat([si[[sector]], bi], axis=1).tail(sessions + 1).ffill().bfill()
    return df / df.iloc[0] * 100


def ratios(res: dict, sector: str, sessions: int) -> pd.DataFrame:
    """sector / benchmark rebased to 100: rising line = sector outperforming that benchmark."""
    si, bi = res["sector_index"], _cmp(res)
    cols = {}
    for b in bi.columns:
        r = (si[sector] / bi[b]).tail(sessions + 1).ffill().bfill()
        cols[b] = r / r.iloc[0] * 100
    return pd.DataFrame(cols)


# ───────────────────────── rotation detection ─────────────────────────
def _quadrant(x, y) -> str:
    if x is None or y is None or np.isnan(x) or np.isnan(y):
        return "UNAVAILABLE"
    return ("LEADING" if y >= 100 else "FADING") if x >= 100 else ("IMPROVING" if y >= 100 else "WEAK")


def _stage(quad, ab20, rsi, dist20, score, fresh) -> str:
    ext = (dist20 is not None and dist20 > EXTENDED_PCT) or (rsi is not None and rsi > EXTENDED_RSI)
    confirmed = bool(ab20) and rsi is not None and rsi >= 50
    if quad in ("LEADING", "IMPROVING"):
        if fresh and confirmed and not ext and score >= 55:
            return "ROTATING IN"
        if quad == "LEADING":
            return "EXTENDED" if ext else "LEADING"
        return "IMPROVING"
    if quad == "FADING":
        return "FADING"
    if quad == "WEAK":
        return "WEAK"
    return "—"


def _score(r: dict, x, y, rs_above, bench: str):
    """Transparent 0-100 rotation score + plain-English reasons / cautions."""
    pts, why, warn = 0, [], []
    ab20, ab50, ab200 = _b(r.get("ab20")), _b(r.get("ab50")), _b(r.get("ab200"))
    rsi, rsi5 = _f(r.get("rsi_d")), _f(r.get("rsi_d_5"))
    dist20, cmf = _f(r.get("dist20")), _f(r.get("cmf"))
    a20, a20_5 = _f(r.get("pct_above20")), _f(r.get("pct_above20_5"))
    dsh = _f(r.get("dshare_w"))
    # trend 30
    if ab20: pts += 10
    if ab50: pts += 10
    if _b(r.get("e10_gt_20")): pts += 5
    if _b(r.get("e20_gt_50")): pts += 5
    # momentum 20
    if rsi is not None and rsi > 50: pts += 10
    if rsi is not None and rsi5 is not None and rsi > rsi5: pts += 5
    if rsi is not None and rsi <= 75: pts += 5
    # relative 25
    if x is not None and not np.isnan(x) and x >= 100: pts += 10
    if y is not None and not np.isnan(y) and y >= 100: pts += 10
    if rs_above: pts += 5
    # breadth 15
    if a20 is not None and a20 >= 50: pts += 8
    if a20 is not None and a20_5 is not None and a20 > a20_5: pts += 7
    # flow 10
    if cmf is not None and cmf > 0: pts += 5
    if dsh is not None and dsh > 0: pts += 5

    if _b(r.get("x20_up")): why.append("Closed back above its 20-EMA in the last 5 sessions")
    if _b(r.get("rsi50_up")): why.append("RSI(14) crossed back above 50")
    if _b(r.get("e1020_up")): why.append("10-EMA crossed above the 20-EMA")
    if ab20 and ab50 and ab200: why.append("Above the 20, 50 and 200-EMA: established uptrend")
    if x is not None and not np.isnan(x) and y is not None and not np.isnan(y):
        if x < 100 <= y: why.append(f"Relative strength vs {bench} is accelerating from a low base")
        elif x >= 100 and y >= 100: why.append(f"Outperforming {bench} and still accelerating")
    if a20 is not None and a20 >= 50:
        d5 = f" ({a20 - a20_5:+.0f} pts in 5 sessions)" if a20_5 is not None else ""
        why.append(f"{a20:.0f}% of stocks above their 20-EMA{d5}")
    if cmf is not None and cmf > 0.05: why.append(f"Chaikin money flow positive ({cmf:+.2f})")
    if dsh is not None and dsh > 0.15: why.append(f"Turnover share up {dsh:+.2f} pts this week")

    if dist20 is not None and dist20 > EXTENDED_PCT: warn.append(f"Stretched {dist20:.1f}% above its 20-EMA: chasing risk")
    if rsi is not None and rsi > EXTENDED_RSI: warn.append(f"RSI {rsi:.0f} is overheated")
    if ab50 is False and ab200 is False: warn.append("Below the 50 and 200-EMA: still in a downtrend")
    if cmf is not None and cmf < -0.05: warn.append(f"Distribution-like money flow ({cmf:+.2f})")
    return int(pts), why, warn


def rotation_table(res: dict, bench_name: str = "NIFTY 50"):
    """One row per sector: RS-Ratio / RS-Momentum vs `bench_name`, quadrant, score, stage, signals, reasons.
    Returns (DataFrame, {sector: [(x, y), ...] oldest -> newest})."""
    si, bi = res["sector_index"], res["bench_index"]
    if bench_name not in bi.columns:
        raise KeyError(f"benchmark {bench_name} unavailable")
    sec = res["sectors"]
    b = bi[bench_name].reindex(si.index).ffill()
    rs = si.div(b, axis=0)
    ratio = rs / rs.rolling(RS_TREND, min_periods=RS_TREND).mean() * 100
    mom = ratio / ratio.shift(RS_MOM) * 100
    rs_e20 = ema_df(rs, 20)

    trails, rows = {}, []
    for rec in sec.to_dict("records"):
        s = rec["sector"]
        x, y = _last(ratio[s]), _last(mom[s])
        pts = []
        for k in range(TRAIL_LEN - 1, -1, -1):
            pos = len(ratio) - 1 - k * TRAIL_STEP
            if pos >= 0 and pd.notna(ratio[s].iloc[pos]) and pd.notna(mom[s].iloc[pos]):
                pts.append((float(ratio[s].iloc[pos]), float(mom[s].iloc[pos])))
        trails[s] = pts
        rs_above = bool(rs[s].iloc[-1] > rs_e20[s].iloc[-1]) if pd.notna(rs_e20[s].iloc[-1]) else False
        rs_xup = crossed_up(rs[s], rs_e20[s], 10)
        ex = {}
        for tag, k in (("ex1w", 5), ("ex2w", 10), ("ex1m", 21)):
            ex[tag] = (float(((si[s].iloc[-1] / si[s].iloc[-1 - k]) / (b.iloc[-1] / b.iloc[-1 - k]) - 1) * 100)
                       if len(si) > k and pd.notna(b.iloc[-1 - k]) else np.nan)
        score, why, warn = _score(rec, x, y, rs_above, bench_name)
        quad = _quadrant(x, y)
        q5 = _quadrant(*pts[-2]) if len(pts) >= 2 else quad
        q10 = _quadrant(*pts[-3]) if len(pts) >= 3 else q5
        moved_from, moved_when = "", 0
        if q5 not in (quad, "UNAVAILABLE"):
            moved_from, moved_when = q5, 5
        elif q10 not in (quad, "UNAVAILABLE"):
            moved_from, moved_when = q10, 10
        dx5 = pts[-1][0] - pts[-2][0] if len(pts) >= 2 else 0.0
        rs_dir = "up" if dx5 > 0.2 else "down" if dx5 < -0.2 else "flat"
        fresh = bool(_b(rec.get("x20_up")) or _b(rec.get("rsi50_up")) or _b(rec.get("e1020_up")) or rs_xup)
        stage = _stage(quad, _b(rec.get("ab20")), _f(rec.get("rsi_d")), _f(rec.get("dist20")), score, fresh)
        signals = []
        if _b(rec.get("x20_up")): signals.append("20-EMA reclaim")
        if _b(rec.get("rsi50_up")): signals.append("RSI > 50")
        if _b(rec.get("e1020_up")): signals.append("10/20 cross")
        if rs_xup: signals.append("RS turning up")
        if _b(rec.get("x20_dn")): signals.append("lost 20-EMA")
        rows.append({**rec, "rrg_x": x, "rrg_y": y, "quadrant": quad, "rs_above": rs_above, "rs_xup": rs_xup,
                     "ex1w": ex["ex1w"], "ex2w": ex["ex2w"], "ex1m": ex["ex1m"],
                     "moved_from": moved_from, "moved_when": moved_when, "rs_dir": rs_dir,
                     "moved_up": LANE_VALUE.get(quad, -1) > LANE_VALUE.get(moved_from, 99) if moved_from else False,
                     "score": score, "stage": stage, "fresh": fresh,
                     "signals": signals, "why": why, "warn": warn})
    out = pd.DataFrame(rows)
    out = out.sort_values(["score", "r1w"], ascending=[False, False], na_position="last").reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out, trails


def trend_label(ab20, ab50, ab200) -> str:
    a, b, c = _b(ab20), _b(ab50), _b(ab200)
    if a and b and c: return "STRONG UPTREND"
    if a and b: return "UPTREND"
    if b and not a: return "PULLBACK"
    if a and not b: return "RECOVERING"
    if a is None and b is None: return "—"
    return "DOWNTREND"
