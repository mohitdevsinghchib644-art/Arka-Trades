"""
arka_flow.rotation - where to be, and when. Pure pandas / numpy, no Streamlit.

WHY THIS EXISTS
  The state machine (state.py) describes each sector. It does not answer the question a portfolio has to answer every
  few weeks: "which few sectors should my money be in now, and when do I move?". This module answers it the way
  systematic managers do: one cross-sectional score built from published, tested effects, a trend filter, a market-regime
  filter, rank buffers against churn, and a walk-forward test that shows whether the score has actually worked on YOUR data.

THE SCORE (each component is a cross-sectional z-score on that date, clipped at ±3, so sectors are only ever compared
with each other on the same day; weights are fixed in advance, not fitted to the data)

  momentum    35%  Relative momentum, volatility-adjusted. For h = 63, 126, 252 sessions:
                   (sector/benchmark ratio change over h) / (annualised volatility of the ratio's daily log changes, 252 s).
                   z-scored per horizon and averaged 25 / 40 / 35.
                   Why: industry momentum is one of the strongest, most replicated effects in equities (Moskowitz &
                   Grinblatt 1999). NSE's own momentum indices rank on 6- and 12-month returns divided by volatility
                   (Normalised Momentum Score). The 3-month leg adds timeliness for rotation.
  rotation    20%  RS-Momentum: the rate of change of RS-Ratio, as on a Relative Rotation Graph (de Kempenaer;
                   Bloomberg RRG <GO>). RS-Ratio = 100 x EMA10(ratio) / SMA50(ratio); RS-Momentum = 100 x RS-Ratio /
                   RS-Ratio 10 sessions ago. Rewards relative strength that is accelerating: this is the "right time" part.
  breadth     20%  Participation: % of members above their 50-EMA (level) and its change over 20 sessions, 50 / 50.
                   A move carried by many stocks is more durable than one carried by a few (breadth confirmation).
  consistency 15%  Share of up days minus share of down days in the sector's RELATIVE return over 126 sessions.
                   Why: momentum built from many small steady gains persists longer than momentum from a few jumps
                   ("frog in the pan", Da, Gurun & Warachka 2014).
  activity    10%  Share of traded value in stocks that closed up, 5 sessions, smoothed (EMA 10). Turnover on up days.
  A component that cannot be computed for a sector (e.g. breadth for an index without a constituent file) is left out and
  the other weights are scaled up for that sector; the number of components used is reported.

FILTERS (not part of the score)
  trend       sector level above its 200-session average (Faber 2007: the 10-month trend rule; time-series momentum,
              Moskowitz, Ooi & Pedersen 2012). A sector below it can rank high on RELATIVE strength while still falling;
              it is not given a slot.
  regime      the market: benchmark vs its 200-session average, % of all stocks above their 200-EMA, and a STRESS flag
              (benchmark 10%+ below its 1-year high AND 20-session volatility in the top 20% of its last year). Momentum
              strategies suffer their worst losses in exactly those "panic" states (Daniel & Moskowitz 2016), so in
              RISK-OFF or STRESS only half the slots are filled and the rest stays in cash.

MODEL PORTFOLIO
  Hold the top N sectors by score that pass the trend filter, equal weight, reviewed every K sessions. A held sector stays
  until its rank falls below N + buffer (default buffer = N): a rank buffer stops a sector that wobbles between #5 and #6
  from being traded every review. Signal at the close, trade at the NEXT close, costs charged on every change.

PROOF (walk-forward, no look-ahead: every value on date t uses data up to t)
  * Information coefficient (IC): rank correlation between today's score and each sector's NEXT h-session return in excess
    of the benchmark, at every review date. Mean IC, its t-stat and % of positive ICs. The standard quant test of a score.
  * Quintile spread: forward excess return of the top-fifth vs bottom-fifth of sectors by score. It should rise with the score.
  * Equity curves and statistics of the model portfolio against the benchmark, an equal-weight of all sectors and a plain
    6-month momentum ranking (if the extra parts of the score add nothing, the plain ranking will match it).
  * The same test on the first 60% and last 40% of the history, to see whether a result is stable.

LIMITS (shown on the screen next to the numbers)
  ARKA groups are built from TODAY's members (survivorship bias flatters history). Sector baskets cannot be bought directly.
  Yahoo prices are unofficial. Costs are an assumption. A positive past result is evidence, not a promise.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

ROT_VERSION = "1.0.0"
WEIGHTS = {"momentum": 0.35, "rotation": 0.20, "breadth": 0.20, "consistency": 0.15, "activity": 0.10}
FACTOR_LABEL = {"momentum": "Relative momentum (risk-adjusted)", "rotation": "Rotation speed (RS-Momentum)",
                "breadth": "Breadth (participation)", "consistency": "Trend consistency", "activity": "Up-day turnover"}
MOM_H = {63: 0.25, 126: 0.40, 252: 0.35}
VOL_WIN, VOL_MIN = 252, 120
RRG_FAST, RRG_SLOW, RRG_MOM = 10, 50, 10
CONS_WIN = 126
TREND_SMA = 200
Z_CLIP = 3.0
MIN_CROSS = 5                       # fewer sectors than this on a date -> no z-score
QUADRANTS = ("Leading", "Weakening", "Lagging", "Improving")
REGIME_SLOTS = {"Risk-on": 1.0, "Neutral": 1.0, "Risk-off": 0.5, "Stress": 0.5}


# ═════════════════════════ helpers ═════════════════════════
def zscore(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score per date, clipped at ±3. NaN on dates with fewer than MIN_CROSS values."""
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=0).replace(0, np.nan)
    n = df.notna().sum(axis=1)
    z = df.sub(mu, axis=0).div(sd, axis=0).clip(-Z_CLIP, Z_CLIP)
    return z.where(n >= MIN_CROSS, np.nan)


def blend(parts: dict, weights: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weighted mean of the available panels per cell; weights of missing parts are spread over the rest.
    -> (blended panel, number of parts used per cell)."""
    num = den = cnt = None
    for k, p in parts.items():
        w = weights[k]
        ok = p.notna()
        a = p.fillna(0.0) * w
        b = ok.astype(float) * w
        num = a if num is None else num + a
        den = b if den is None else den + b
        cnt = ok.astype(int) if cnt is None else cnt + ok.astype(int)
    return num / den.replace(0, np.nan), cnt


def _ratio(H):
    return H["levels"].div(H["bench"], axis=0)


# ═════════════════════════ factors ═════════════════════════
def factors(H: dict) -> dict:
    """All score panels (dates x sectors). Every value at date t uses data up to t only."""
    L = H["levels"]
    ratio = _ratio(H)
    dlr = np.log(ratio).diff()
    vol = dlr.rolling(VOL_WIN, min_periods=VOL_MIN).std() * math.sqrt(252)
    F = {"ratio": ratio, "vol": vol}

    mom_raw = {h: (ratio / ratio.shift(h) - 1) / vol for h in MOM_H}
    for h, p in mom_raw.items():
        F[f"mom_raw_{h}"] = p
    momentum, _ = blend({h: zscore(p) for h, p in mom_raw.items()}, MOM_H)

    fast = ratio.ewm(span=RRG_FAST, adjust=False, min_periods=RRG_FAST).mean()
    slow = ratio.rolling(RRG_SLOW, min_periods=RRG_SLOW).mean()
    rs_ratio = 100 * fast / slow
    rs_mom = 100 * rs_ratio / rs_ratio.shift(RRG_MOM)
    F["rs_ratio"], F["rs_mom"] = rs_ratio, rs_mom
    rotation = zscore(rs_mom - 100)

    pct50 = H.get("pct50")
    if pct50 is not None and pct50.notna().any().any():
        d50 = pct50 - pct50.shift(20)
        breadth, _ = blend({"lvl": zscore(pct50), "chg": zscore(d50)}, {"lvl": 0.5, "chg": 0.5})
        breadth = breadth.where(pct50.notna())
        F["pct50"], F["d50"] = pct50, d50
    else:
        breadth = pd.DataFrame(np.nan, index=L.index, columns=L.columns)

    up = (dlr > 0).astype(float).where(dlr.notna())
    dn = (dlr < 0).astype(float).where(dlr.notna())
    cnt = dlr.notna().astype(float).rolling(CONS_WIN, min_periods=CONS_WIN // 2).sum()
    cons_raw = (up.rolling(CONS_WIN, min_periods=CONS_WIN // 2).sum() - dn.rolling(CONS_WIN, min_periods=CONS_WIN // 2).sum()) / cnt
    F["cons_raw"] = cons_raw * 100
    consistency = zscore(cons_raw)

    up5 = H.get("up_share5")
    if up5 is not None and up5.notna().any().any():
        act_raw = up5.ewm(span=10, adjust=False, min_periods=5).mean()
        F["act_raw"] = act_raw
        activity = zscore(act_raw)
    else:
        activity = pd.DataFrame(np.nan, index=L.index, columns=L.columns)

    comps = {"momentum": momentum, "rotation": rotation, "breadth": breadth, "consistency": consistency, "activity": activity}
    for k, v in comps.items():
        F[k] = v
    score, used = blend(comps, WEIGHTS)
    score = score.where(momentum.notna())                    # no score without the main component
    F["score"], F["n_parts"] = score, used.where(momentum.notna())
    F["rank_pct"] = score.rank(axis=1, pct=True) * 100
    F["rank"] = score.rank(axis=1, ascending=False, method="first")
    sma = L.rolling(TREND_SMA, min_periods=int(TREND_SMA * 0.75)).mean()
    F["trend_sma"] = sma
    F["trend_ok"] = (L > sma).astype(float).where(sma.notna() & L.notna())
    F["dist_trend"] = (L / sma - 1) * 100
    F["quadrant"] = quadrant(rs_ratio, rs_mom)
    F["momentum_only"] = (ratio / ratio.shift(126) - 1) * 100       # the plain baseline: 6-month relative return
    return F


def quadrant(rs_ratio: pd.DataFrame, rs_mom: pd.DataFrame) -> pd.DataFrame:
    q = np.select([(rs_ratio >= 100) & (rs_mom >= 100), (rs_ratio >= 100) & (rs_mom < 100),
                   (rs_ratio < 100) & (rs_mom < 100), (rs_ratio < 100) & (rs_mom >= 100)], list(QUADRANTS), default="")
    out = pd.DataFrame(q, index=rs_ratio.index, columns=rs_ratio.columns)
    return out.where(rs_ratio.notna() & rs_mom.notna(), "")


# ═════════════════════════ market regime ═════════════════════════
def regime(H: dict, close: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per date: bench vs 200-session average, market breadth (% of stocks above 200-EMA), stress flag, regime label."""
    b = H["bench"]
    sma = b.rolling(TREND_SMA, min_periods=int(TREND_SMA * 0.75)).mean()
    if close is not None and close.shape[1] >= 20:
        C = close.reindex(b.index)
        e = C.ewm(span=200, adjust=False, min_periods=200).mean()
        ok = C.notna() & e.notna()
        brd = ((C > e) & ok).sum(axis=1) / ok.sum(axis=1).replace(0, np.nan) * 100
        brd = brd.where(ok.sum(axis=1) >= 20)
    else:
        w = H["n_bar"].fillna(0)
        brd = (H["pct200"] * w).sum(axis=1, min_count=1) / w.where(H["pct200"].notna()).sum(axis=1).replace(0, np.nan)
    r = b.pct_change()
    vol20 = r.rolling(20, min_periods=20).std() * math.sqrt(252)
    vol_pct = vol20.rolling(252, min_periods=120).rank(pct=True) * 100
    dd = (b / b.rolling(252, min_periods=60).max() - 1) * 100
    above = b > sma
    stress = (dd <= -10) & (vol_pct >= 80)
    lab = np.where(stress, "Stress", np.where(above & (brd >= 50), "Risk-on", np.where(~above & (brd < 40), "Risk-off", "Neutral")))
    out = pd.DataFrame({"bench": b, "sma200": sma, "above_sma200": above.where(sma.notna()), "breadth200": brd, "vol20": vol20 * 100,
                        "vol_pct": vol_pct, "drawdown": dd, "stress": stress, "regime": lab}, index=b.index)
    out.loc[sma.isna(), "regime"] = "Unknown"
    return out


# ═════════════════════════ selection ═════════════════════════
def select(score_row: pd.Series, trend_row: pd.Series, held: list, n: int, buffer: int, slots: int | None = None) -> list:
    """Top-n by score among sectors passing the trend filter (unknown trend counts as passing), with a rank buffer:
    a held sector is kept while its rank is within n + buffer and its trend is not broken."""
    slots = n if slots is None else slots
    s = score_row.dropna().sort_values(ascending=False)
    rank = pd.Series(np.arange(1, len(s) + 1), index=s.index)
    ok = lambda x: not (x in trend_row.index and trend_row[x] == 0)
    keep = [x for x in held if x in rank.index and rank[x] <= n + buffer and ok(x)]
    keep = sorted(keep, key=lambda x: rank[x])[:slots]
    for x in s.index:
        if len(keep) >= slots:
            break
        if x not in keep and ok(x):
            keep.append(x)
    return keep


# ═════════════════════════ walk-forward model portfolio ═════════════════════════
def _metrics(r: pd.Series, bench: pd.Series | None = None) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = r.std() * math.sqrt(252)
    dd = (eq / eq.cummax() - 1).min()
    out = {"total": (eq.iloc[-1] - 1) * 100, "cagr": cagr * 100, "vol": vol * 100, "sharpe": (r.mean() * 252) / vol if vol > 0 else np.nan,
           "max_dd": dd * 100, "calmar": (cagr / abs(dd)) if dd < 0 else np.nan}
    if bench is not None:
        b = bench.reindex(r.index).fillna(0.0)
        ex = r - b
        te = ex.std() * math.sqrt(252)
        out["excess_cagr"] = out["cagr"] - _metrics(b)["cagr"] if len(b) >= 20 else np.nan
        out["info_ratio"] = (ex.mean() * 252) / te if te > 0 else np.nan
        g = pd.DataFrame({"s": r, "b": b})
        try:
            m = g.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        except (ValueError, TypeError):
            m = g.resample("M").apply(lambda x: (1 + x).prod() - 1)
        out["months_beat"] = float((m["s"] > m["b"]).mean() * 100) if len(m) else np.nan
    return out


def run_portfolio(H: dict, F: dict, reg: pd.DataFrame | None = None, n: int = 5, every: int = 21, buffer: int | None = None,
                  cost_pct: float = 0.30, lag: int = 1, use_regime: bool = True, score_key: str = "score",
                  use_trend: bool = True, start=None, end=None) -> dict:
    """Walk-forward model portfolio.
    Every `every` sessions the selection is made at the close with data up to that close, traded at the close `lag` sessions
    later, and held (equal weight per slot, unfilled slots in cash at 0%) until the next change.
    cost_pct = round-trip cost in % of value traded; charged on every change in weights (half per side)."""
    buffer = n if buffer is None else buffer
    L = H["levels"]
    ret = L.pct_change()
    bret = H["bench"].pct_change()
    S = F[score_key]
    trend = F["trend_ok"] if use_trend else pd.DataFrame(1.0, index=L.index, columns=L.columns)
    idx = L.index
    valid = S.notna().sum(axis=1) >= max(MIN_CROSS, n + 1)
    if start is not None:
        valid &= idx >= pd.Timestamp(start)
    if end is not None:
        valid &= idx <= pd.Timestamp(end)
    pos = np.flatnonzero(valid.to_numpy())
    if len(pos) == 0:
        return {"ok": False, "why": "not enough history to compute the score"}
    first, last = int(pos[0]), int(pos[-1])
    reviews = list(range(first, last + 1, every))
    W = pd.DataFrame(0.0, index=idx, columns=L.columns)
    held, log, missing_days = [], [], 0
    w_prev = pd.Series(0.0, index=L.columns)
    cost = pd.Series(0.0, index=idx)
    for k, p in enumerate(reviews):
        t = idx[p]
        slots = n
        rg = None
        if use_regime and reg is not None and t in reg.index:
            rg = reg.at[t, "regime"]
            slots = max(1, int(round(n * REGIME_SLOTS.get(rg, 1.0))))
        new = select(S.iloc[p], trend.iloc[p], held, n, buffer, slots)
        w = pd.Series(0.0, index=L.columns)
        if new:
            w[new] = 1.0 / n
        q = p + lag
        if q >= len(idx):
            log.append({"date": t, "trade_date": None, "holdings": new, "in": sorted(set(new) - set(held)),
                        "out": sorted(set(held) - set(new)), "regime": rg, "slots": slots, "pending": True})
            held = new
            break
        end_q = (reviews[k + 1] + lag) if k + 1 < len(reviews) else len(idx)
        W.iloc[q:end_q] = w.to_numpy()
        turn = float((w - w_prev).abs().sum())
        cost.iloc[min(q + 1, len(idx) - 1)] += turn * cost_pct / 2 / 100
        log.append({"date": t, "trade_date": idx[q], "holdings": new, "in": sorted(set(new) - set(held)), "out": sorted(set(held) - set(new)),
                    "regime": rg, "slots": slots, "turnover": turn, "pending": False})
        held, w_prev = new, w
    held_prev = W.shift(1)
    r = ret.fillna(0.0)
    missing_days = int(((held_prev > 0) & ret.isna()).to_numpy().sum())
    port = (held_prev * r).sum(axis=1) - cost
    t0 = idx[first + lag + 1] if first + lag + 1 < len(idx) else idx[-1]
    port = port.loc[t0:]
    if end is not None:
        port = port.loc[:pd.Timestamp(end)]
    b = bret.loc[port.index].fillna(0.0)
    ew = ret.loc[port.index].mean(axis=1).fillna(0.0)
    turns = [x["turnover"] for x in log if not x["pending"]]
    yrs = len(port) / 252
    return {"ok": True, "returns": port, "bench_returns": b, "ew_returns": ew, "weights": W.loc[port.index],
            "log": log, "held": held, "metrics": _metrics(port, b), "bench_metrics": _metrics(b), "ew_metrics": _metrics(ew, b),
            "turnover_per_year": (sum(turns) / 2 / yrs) if yrs > 0 else np.nan, "reviews": len(log),
            "missing_bar_days": missing_days, "params": {"n": n, "every": every, "buffer": buffer, "cost_pct": cost_pct, "lag": lag,
                                                         "use_regime": use_regime, "score": score_key, "use_trend": use_trend}}


# ═════════════════════════ does the score predict? ═════════════════════════
def forward_excess(H: dict, h: int, lag: int = 1) -> pd.DataFrame:
    """Return from close t+lag to close t+lag+h, sector minus benchmark (pp), aligned to signal date t."""
    L, b = H["levels"], H["bench"]
    a = (L.shift(-(lag + h)) / L.shift(-lag) - 1) * 100
    br = (b.shift(-(lag + h)) / b.shift(-lag) - 1) * 100
    return a.sub(br, axis=0)


def _spearman(x: pd.Series, y: pd.Series) -> float:
    m = x.notna() & y.notna()
    if m.sum() < MIN_CROSS:
        return np.nan
    return float(np.corrcoef(x[m].rank(), y[m].rank())[0, 1])


def score_test(H: dict, F: dict, h: int = 21, every: int | None = None, keys=None, start=None, end=None) -> dict:
    """IC and quintile spread for the score and each component, on non-overlapping review dates."""
    every = every or h
    fwd = forward_excess(H, h)
    keys = keys or ["score", "momentum", "rotation", "breadth", "consistency", "activity", "momentum_only"]
    S = F["score"]
    ok = S.notna().sum(axis=1) >= MIN_CROSS
    if start is not None:
        ok &= S.index >= pd.Timestamp(start)
    if end is not None:
        ok &= S.index <= pd.Timestamp(end)
    dates = [d for d in S.index[ok][::every] if fwd.loc[d].notna().sum() >= MIN_CROSS]
    ic_rows, quint = [], {q: [] for q in range(1, 6)}
    for k in keys:
        P = F.get(k)
        if P is None:
            continue
        ics = pd.Series([_spearman(P.loc[d], fwd.loc[d]) for d in dates], index=dates, dtype=float).dropna()
        n = len(ics)
        sd = ics.std(ddof=1) if n > 1 else np.nan
        ic_rows.append({"factor": k, "dates": n, "mean_ic": float(ics.mean()) if n else np.nan,
                        "t_stat": float(ics.mean() / (sd / math.sqrt(n))) if n > 2 and sd and sd > 0 else np.nan,
                        "pct_positive": float((ics > 0).mean() * 100) if n else np.nan, "series": ics})
    for d in dates:
        s, f = S.loc[d], fwd.loc[d]
        m = s.notna() & f.notna()
        if m.sum() < 5:
            continue
        qb = pd.qcut(s[m].rank(method="first"), 5, labels=False) + 1
        for q in range(1, 6):
            quint[q].append(float(f[m][qb == q].mean()))
    qt = pd.DataFrame({"quintile": [f"Q{q}" + (" (top)" if q == 5 else " (bottom)" if q == 1 else "") for q in range(1, 6)],
                       "mean_excess": [np.mean(quint[q]) if quint[q] else np.nan for q in range(1, 6)],
                       "beat_pct": [float(np.mean(np.array(quint[q]) > 0) * 100) if quint[q] else np.nan for q in range(1, 6)],
                       "dates": [len(quint[q]) for q in range(1, 6)]})
    spread = (np.array(quint[5]) - np.array(quint[1])) if quint[5] and len(quint[5]) == len(quint[1]) else np.array([])
    return {"ic": pd.DataFrame([{k: v for k, v in r.items() if k != "series"} for r in ic_rows]),
            "ic_series": {r["factor"]: r["series"] for r in ic_rows}, "quintiles": qt, "h": h, "dates": len(dates),
            "spread_mean": float(spread.mean()) if len(spread) else np.nan,
            "spread_beat": float((spread > 0).mean() * 100) if len(spread) else np.nan}


# ═════════════════════════ today ═════════════════════════
def snapshot(H: dict, F: dict, reg: pd.DataFrame | None, pf: dict | None, n: int, buffer: int | None = None) -> pd.DataFrame:
    """One row per sector on the latest date: score, rank, components, RRG, trend, and what the model would do today."""
    buffer = n if buffer is None else buffer
    t = F["score"].index[-1]
    held = pf["held"] if pf and pf.get("ok") else []
    rg = reg["regime"].iloc[-1] if reg is not None and len(reg) else None
    slots = max(1, int(round(n * REGIME_SLOTS.get(rg, 1.0))))
    target = select(F["score"].loc[t], F["trend_ok"].loc[t], held, n, buffer, slots)
    lag20 = F["rank"].shift(20).loc[t]
    rows = []
    for s in H["levels"].columns:
        g = lambda k, lag=0: F[k][s].iloc[-1 - lag] if len(F[k]) > lag else np.nan
        q_now, q_prev = F["quadrant"][s].iloc[-1], (F["quadrant"][s].iloc[-11] if len(F["quadrant"]) > 10 else "")
        rows.append({"sector": s, "score": g("score"), "rank": g("rank"), "rank_20_ago": lag20.get(s, np.nan), "rank_pct": g("rank_pct"),
                     **{k: g(k) for k in WEIGHTS}, "n_parts": g("n_parts"), "rs_ratio": g("rs_ratio"), "rs_mom": g("rs_mom"),
                     "quadrant": q_now, "quadrant_10_ago": q_prev, "trend_ok": g("trend_ok"), "dist_trend": g("dist_trend"),
                     "rel_63": (g("ratio") / g("ratio", 63) - 1) * 100 if len(F["ratio"]) > 63 else np.nan,
                     "rel_126": (g("ratio") / g("ratio", 126) - 1) * 100 if len(F["ratio"]) > 126 else np.nan,
                     "rel_21": (g("ratio") / g("ratio", 21) - 1) * 100 if len(F["ratio"]) > 21 else np.nan,
                     "pct50": g("pct50") if "pct50" in F else np.nan, "d50": g("d50") if "d50" in F else np.nan,
                     "consistency_raw": g("cons_raw"), "vol": g("vol") * 100 if pd.notna(g("vol")) else np.nan,
                     "held": s in held, "target": s in target})
    df = pd.DataFrame(rows)
    df["rank_change_20"] = df["rank_20_ago"] - df["rank"]
    df["action"] = np.select([df["held"] & df["target"], ~df["held"] & df["target"], df["held"] & ~df["target"]],
                             ["Hold", "Rotate in", "Rotate out"], default="")
    early = (~df["target"]) & (df["quadrant"] == "Improving") & (df["rank_change_20"] >= max(3, len(df) * 0.15)) & (df["trend_ok"] != 0)
    df.loc[early & (df["action"] == ""), "action"] = "Watch: improving"
    return df.sort_values("rank").reset_index(drop=True)


def contributions(row: pd.Series) -> dict:
    """Each component's share of the final score (weight x z, rescaled for missing parts)."""
    have = {k: row[k] for k in WEIGHTS if pd.notna(row.get(k))}
    wsum = sum(WEIGHTS[k] for k in have) or 1.0
    return {k: WEIGHTS[k] / wsum * v for k, v in have.items()}


def explain(row: pd.Series, n_sectors: int, bench: str, regime_label: str | None = None) -> list:
    """Plain sentences, each built from the numbers in `row`. No adjectives without a number behind them."""
    f = lambda v, fmt="+.1f": "n/a" if v is None or pd.isna(v) else format(float(v), fmt)
    out = []
    if pd.notna(row["rank"]):
        rc = row.get("rank_change_20")
        move = "" if pd.isna(rc) else (f", up {int(rc)} places in 20 sessions" if rc > 0 else f", down {int(-rc)} places in 20 sessions" if rc < 0 else ", unchanged in 20 sessions")
        out.append(f"Ranked {int(row['rank'])} of {n_sectors} by rotation score{move}.")
    out.append(f"Relative to {bench}: {f(row['rel_21'])}% over 1 month, {f(row['rel_63'])}% over 3 months, {f(row['rel_126'])}% over 6 months "
               f"(momentum z {f(row['momentum'], '+.2f')}).")
    if pd.notna(row["rs_ratio"]):
        q = row["quadrant"] or "n/a"
        prev = row.get("quadrant_10_ago") or ""
        turn = f", moved from {prev} 10 sessions ago" if prev and prev != q else ""
        out.append(f"Rotation map: {q} (RS-Ratio {f(row['rs_ratio'], '.1f')}, RS-Momentum {f(row['rs_mom'], '.1f')}){turn}.")
    if pd.notna(row.get("pct50")):
        out.append(f"Breadth: {f(row['pct50'], '.0f')}% of members above their 50-EMA, {f(row['d50'], '+.0f')} points in 20 sessions.")
    else:
        out.append("Breadth: NOT AVAILABLE for this sector (no member list), so the score uses the other components.")
    if pd.notna(row.get("consistency_raw")):
        out.append(f"Consistency: up days minus down days vs {bench} over 6 months = {f(row['consistency_raw'], '+.0f')} points.")
    tr = row.get("trend_ok")
    out.append("Trend filter: " + ("PASS, level above its 200-session average" if tr == 1 else
                                   "FAIL, level below its 200-session average: no slot even if the rank is high" if tr == 0 else "not enough history")
               + (f" ({f(row['dist_trend'])}%)." if pd.notna(row.get("dist_trend")) else "."))
    act = row.get("action") or ""
    if act:
        why = {"Rotate in": "it is in the top slots today and was not held", "Rotate out": "it fell below the rank buffer or failed the trend filter",
               "Hold": "it is still inside the rank buffer and above trend", "Watch: improving": "it is in the Improving quadrant and climbed the ranking, but is not in the top slots yet"}[act]
        out.append(f"Model: {act.upper()}, because {why}" + (f" (market regime: {regime_label})." if regime_label else "."))
    return out
