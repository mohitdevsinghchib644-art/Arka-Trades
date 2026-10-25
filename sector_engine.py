"""
sector_engine.py — Arka stock-universe SECTOR analytics v2 (pure functions, no network).

Input : {"SYM.NS": OHLCV DataFrame, "^NSEI": benchmark}  +  meta DataFrame[symbol, industry, company]
        optional benches = {"NIFTY 50": Series, "MIDCAP 100": Series, "SMALLCAP 100": Series} (daily closes)
Output: per-sector table, per-stock table, sector index series, benchmark series, breadth series,
        and helper functions for benchmark comparison and ROTATION DETECTION.

Sector index = equal-weight, daily-rebalanced average of the sector's stocks (rebased to 100).
Everything technical (RSI, EMAs, crosses) is computed on that index, so a sector is read like a chart.

Definitions
  RSI(14)        Wilder RSI of the sector index (daily and weekly)
  EMA 10/20/50/200  price (sector index) above = green chip, below = red chip; distance = % from the EMA
  Turnover       close x volume of the latest session in Rs crore (Yahoo volume, an approximation of NSE value)
  Share %        sector turnover / total universe turnover; delta in percentage points
  Chaikin CMF    sum(money-flow-volume, 20) / sum(volume, 20)
  % > 20EMA      share of the sector's stocks closing above their own 20-EMA (breadth)

Rotation detection (RRG-style, simplified - not the licensed JdK RRG)
  RS line        sector index / benchmark index
  RS-Ratio  (x)  100 x RS / SMA(RS, 50)        > 100 = outperforming its own 50-session trend
  RS-Momentum(y) 100 x RS-Ratio / RS-Ratio[10 sessions ago]   > 100 = relative trend accelerating
  Quadrants      LEADING (x>=100,y>=100) · WEAKENING (x>=100,y<100) · LAGGING (x<100,y<100) · IMPROVING (x<100,y>=100)

  Rotation score (0-100, transparent)
     Trend 30      above 20-EMA 10 · above 50-EMA 10 · 10-EMA over 20-EMA 5 · 20-EMA over 50-EMA 5
     Momentum 20   RSI > 50: 10 · RSI rising vs 5 sessions ago: 5 · RSI not overheated (<= 75): 5
     Relative 25   RS-Ratio >= 100: 10 · RS-Momentum >= 100: 10 · RS line above its 20-EMA: 5
     Breadth 15    >= 50% of stocks above 20-EMA: 8 · that share rising over 5 sessions: 7
     Flow 10       Chaikin CMF > 0: 5 · turnover share up over the week: 5

  Stages
     ROTATING IN   LEADING/IMPROVING quadrant + a fresh turn (20-EMA reclaim, RSI back over 50, 10/20 EMA cross or RS
                   line crossing up within 5-10 sessions) + above 20-EMA + RSI >= 50 + not extended + score >= 55
     LEADING       leading quadrant, trend intact          EXTENDED   leading but > 6% above 20-EMA or RSI > 72
     IMPROVING     relative momentum turning up, not yet confirmed by price
     WEAKENING     outperforming trend but momentum fading   LAGGING   underperforming and fading

Honest limits: "sector" is the NSE industry classification from the index constituent files; turnover is not
exchange-reported value; scores and stages are evidence from price, breadth and volume - not forecasts.
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
BENCH_ORDER = ("NIFTY 50", "MIDCAP 100", "SMALLCAP 100")
BK = {"NIFTY 50": "n50", "MIDCAP 100": "m100", "SMALLCAP 100": "s100"}
STAGES = ("ROTATING IN", "LEADING", "EXTENDED", "IMPROVING", "WEAKENING", "LAGGING")


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


def rsi_series(close, n: int = 14):
    """Wilder RSI (works on a Series or a DataFrame)."""
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(~((dn == 0) & up.notna()), 100.0)


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
    keys = (["rsi_d", "rsi_d_5", "rsi_w", "e10_gt_20", "e20_gt_50", "x20_up", "x20_dn", "rsi50_up", "e1020_up"]
            + [f"ab{s}" for s in EMA_SPANS] + [f"dist{s}" for s in EMA_SPANS])
    out = {k: np.nan for k in keys}
    c = c.dropna()
    n = len(c)
    if n < 30:
        return out
    last = float(c.iloc[-1])
    ema = {s: c.ewm(span=s, adjust=False).mean() for s in EMA_SPANS}
    for s in EMA_SPANS:
        if n >= s:
            e = float(ema[s].iloc[-1])
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


# ───────────────────────── universe analysis ─────────────────────────
def _panel(frames: dict, syms: list, col: str, index) -> pd.DataFrame:
    return pd.DataFrame({s: frames[f"{s}.NS"][col] for s in syms}).reindex(index)


def analyse_universe(frames: dict, meta: pd.DataFrame, bench: str = "^NSEI", benches: dict | None = None) -> dict:
    meta = meta.drop_duplicates("symbol")
    usable = [s for s in meta["symbol"] if f"{s}.NS" in frames and len(frames[f"{s}.NS"]) >= 60]
    if len(usable) < 30:
        raise ValueError(f"only {len(usable)} stocks returned usable history (need 30+)")
    all_dates = sorted(set().union(*[set(frames[f"{s}.NS"].index) for s in usable]))
    idx = pd.DatetimeIndex(all_dates)
    last_date = idx[-1]
    syms = [s for s in usable if (last_date - frames[f"{s}.NS"].index[-1]).days <= 4]      # drop stale tickers
    stale = sorted(set(usable) - set(syms))
    if len(syms) < 30:
        raise ValueError("too many stale tickers")

    close = _panel(frames, syms, "Close", idx).ffill()
    high = _panel(frames, syms, "High", idx).ffill()
    low = _panel(frames, syms, "Low", idx).ffill()
    vol = _panel(frames, syms, "Volume", idx).fillna(0)
    ind = meta.set_index("symbol")["industry"].reindex(syms).fillna("Unclassified").astype(str)
    counts = ind.value_counts()
    ind = ind[ind.isin(counts[counts >= MIN_STOCKS_PER_SECTOR].index)]
    syms = ind.index.tolist()
    close, high, low, vol = close[syms], high[syms], low[syms], vol[syms]

    turn = close * vol
    ret1 = (close / close.shift(1) - 1) * 100
    ret5 = (close / close.shift(5) - 1) * 100
    ret21 = (close / close.shift(21) - 1) * 100
    emaS = {s: close.ewm(span=s, adjust=False).mean() for s in EMA_SPANS}
    ema20 = emaS[20]
    rsi = rsi_series(close)
    brk = close > high.rolling(20).max().shift(1)
    bo10 = brk.tail(10).any()
    rng = (high - low).replace(0, np.nan)
    mfv = ((((close - low) - (high - close)) / rng).fillna(0)) * vol
    cmf_stock = mfv.rolling(20).sum() / vol.rolling(20).sum().replace(0, np.nan)

    grp = lambda df: df.T.groupby(ind).sum().T                  # dates x sectors, summed across stocks
    t_sec = grp(turn)
    share = t_sec.div(t_sec.sum(axis=1).replace(0, np.nan), axis=0) * 100
    cmf_sec = grp(mfv).rolling(20).sum() / grp(vol).rolling(20).sum().replace(0, np.nan)

    r1_last, r5_last = ret1.iloc[-1], ret5.iloc[-1]
    adv = (r1_last > 0).groupby(ind).sum()
    dec = (r1_last < 0).groupby(ind).sum()
    n = ind.groupby(ind).size()
    up_turn = turn.iloc[-1].where(r1_last > 0, 0).groupby(ind).sum()
    dn_turn = turn.iloc[-1].where(r1_last < 0, 0).groupby(ind).sum()
    t_last = t_sec.iloc[-1]
    t_avg20 = t_sec.iloc[-21:-1].mean()

    # breadth over time: share of each sector's stocks above their own 20-EMA
    ab20_ser = (close > ema20).where(ema20.notna()).astype(float).T.groupby(ind).mean().T * 100
    cnt = close.count()
    flag = {}
    dist = {}
    for s in EMA_SPANS:
        e = emaS[s].iloc[-1]
        ok = (cnt >= s) & e.notna()
        flag[s] = (close.iloc[-1] > e).where(ok)
        dist[s] = ((close.iloc[-1] / e - 1) * 100).where(ok)

    # sector index (equal-weight, daily rebalanced) + benchmarks
    sec_ret = ret1.T.groupby(ind).mean().T / 100
    sec_idx = (1 + sec_ret.fillna(0)).cumprod() * 100
    bser = {}
    if benches:
        for k, v in benches.items():
            s = norm_series(v)
            if len(s) > 30:
                bser[k] = s
    elif frames.get(bench) is not None:
        bser["NIFTY 50"] = norm_series(frames[bench]["Close"])
    bench_idx = pd.DataFrame({k: bser[k].reindex(idx.union(bser[k].index)).ffill().reindex(idx)
                              for k in BENCH_ORDER if k in bser}, index=idx)

    def sret(k):
        return (sec_idx.iloc[-1] / sec_idx.iloc[-1 - k] - 1) * 100 if len(sec_idx) > k else pd.Series(np.nan, index=sec_idx.columns)

    # leaders: top 3 by 1W return among the sector's more liquid names
    leaders = {}
    tl = turn.iloc[-1]
    for sec, members in ind.groupby(ind).groups.items():
        m = list(members)
        liquid = [s for s in m if tl[s] >= tl[m].quantile(.25)]
        pick = r5_last[liquid].dropna().sort_values(ascending=False).head(3).index.tolist()
        leaders[sec] = ", ".join(pick)

    f = lambda sr: sr.reindex(n.index).values
    sectors = pd.DataFrame({
        "sector": n.index, "stocks": n.values,
        "turnover_cr": f(t_last / 1e7), "turn_avg20_cr": f(t_avg20 / 1e7),
        "turn_vs20": f(t_last / t_avg20.replace(0, np.nan)),
        "r1d": f(r1_last.groupby(ind).mean()), "r1w": f(sret(5)), "r1m": f(sret(21)), "r3m": f(sret(63)),
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

    sparks = {}
    for s in sec_idx.columns:
        t = sec_idx[s].dropna().tail(40)
        if len(t) > 5:
            sparks[s] = (t / t.iloc[0] * 100).round(2).tolist()

    return {"sectors": sectors, "stocks": stocks, "sparks": sparks,
            "sector_index": sec_idx, "bench_index": bench_idx, "ab20_series": ab20_ser,
            "cmf_series": cmf_sec.tail(60), "share_series": share.tail(60),
            "asof": str(last_date)[:10], "n_stocks": len(syms), "stale": stale,
            "unmapped": sorted(set(meta["symbol"]) - set(usable))}


# ───────────────────────── benchmark comparison ─────────────────────────
def returns_vs_benchmarks(res: dict):
    """Sector returns (1W/1M/3M) and excess return in pp versus every benchmark.
    Returns (DataFrame[sector, r1w, r1m, r3m, x1w_n50, x1m_m100 ...], {period: {bench: return}})."""
    si, bi = res["sector_index"], res["bench_index"]
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
    """Sector + all benchmarks rebased to 100 at the start of the window."""
    si, bi = res["sector_index"], res["bench_index"]
    df = pd.concat([si[[sector]], bi], axis=1).tail(sessions + 1).ffill().bfill()
    return df / df.iloc[0] * 100


def ratios(res: dict, sector: str, sessions: int) -> pd.DataFrame:
    """sector / benchmark rebased to 100: rising line = sector outperforming that benchmark."""
    si, bi = res["sector_index"], res["bench_index"]
    cols = {}
    for b in bi.columns:
        r = (si[sector] / bi[b]).tail(sessions + 1).ffill().bfill()
        cols[b] = r / r.iloc[0] * 100
    return pd.DataFrame(cols)


# ───────────────────────── rotation detection ─────────────────────────
def _quadrant(x, y) -> str:
    if x is None or y is None or np.isnan(x) or np.isnan(y):
        return "UNAVAILABLE"
    return ("LEADING" if y >= 100 else "WEAKENING") if x >= 100 else ("IMPROVING" if y >= 100 else "LAGGING")


def _stage(quad, ab20, rsi, dist20, score, fresh) -> str:
    ext = (dist20 is not None and dist20 > EXTENDED_PCT) or (rsi is not None and rsi > EXTENDED_RSI)
    confirmed = bool(ab20) and rsi is not None and rsi >= 50
    if quad in ("LEADING", "IMPROVING"):
        if fresh and confirmed and not ext and score >= 55:
            return "ROTATING IN"
        if quad == "LEADING":
            return "EXTENDED" if ext else "LEADING"
        return "IMPROVING"
    if quad == "WEAKENING":
        return "WEAKENING"
    if quad == "LAGGING":
        return "LAGGING"
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
    rs_e20 = rs.ewm(span=20, adjust=False).mean()

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
        for tag, k in (("ex1w", 5), ("ex1m", 21)):
            ex[tag] = (float(((si[s].iloc[-1] / si[s].iloc[-1 - k]) / (b.iloc[-1] / b.iloc[-1 - k]) - 1) * 100)
                       if len(si) > k and pd.notna(b.iloc[-1 - k]) else np.nan)
        score, why, warn = _score(rec, x, y, rs_above, bench_name)
        quad = _quadrant(x, y)
        fresh = bool(_b(rec.get("x20_up")) or _b(rec.get("rsi50_up")) or _b(rec.get("e1020_up")) or rs_xup)
        stage = _stage(quad, _b(rec.get("ab20")), _f(rec.get("rsi_d")), _f(rec.get("dist20")), score, fresh)
        signals = []
        if _b(rec.get("x20_up")): signals.append("20-EMA reclaim")
        if _b(rec.get("rsi50_up")): signals.append("RSI > 50")
        if _b(rec.get("e1020_up")): signals.append("10/20 cross")
        if rs_xup: signals.append("RS turning up")
        if _b(rec.get("x20_dn")): signals.append("lost 20-EMA")
        rows.append({**rec, "rrg_x": x, "rrg_y": y, "quadrant": quad, "rs_above": rs_above, "rs_xup": rs_xup,
                     "ex1w": ex["ex1w"], "ex1m": ex["ex1m"], "score": score, "stage": stage, "fresh": fresh,
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
