"""
rotation_engine.py — Arka sector-rotation engine v4 (pure pandas / numpy: no network, no Streamlit)

Replaces sector_engine.py for MONEY FLOW. What it answers, for ~50 granular sectors (sector_map.py):

  1. RELATIVE STRENGTH   Is the sector beating the market over 1W / 1M / 3M / 6M?
  2. ROTATION (RRG)      Weekly Relative Rotation Graph, JdK-style: RS-Ratio and RS-Momentum, each normalised
                         around 100 with a z-score, so every sector sits on the same map. Phase, weeks in
                         phase, the phase it came from and the direction it is heading.
  3. BREADTH             % of the sector's stocks above their 20 / 50 / 200-EMA (and the 5-day change), A/D,
                         % that broke out of a 20-day high in the last 10 sessions, new 52-week highs.
  4. MONEY               Turnover (₹), share of all market turnover and its change, how much of the turnover
                         went to rising vs falling stocks, and value-weighted Chaikin Money Flow.
  5. ACTION              One plain label per sector — ROTATING IN / LEADING / STRETCHED / WATCH / WEAKENING /
                         LAGGING — with the reasons and warnings that produced it.

Construction notes (the things that made the old numbers look "wrong")
  * Sector index = EQUAL-WEIGHT average of member stocks' daily returns (one heavyweight cannot hide what
    the rest of the group is doing). Optional sqrt-turnover weighting.
  * Default benchmark = MARKET, the whole universe built the same way, so an equal-weight sector is compared
    with an equal-weight market. NIFTY 50 / NIFTY 500 are available but are cap-weighted.
  * Money flow is VALUE-weighted (₹ turnover). The old CMF summed share volume, so ₹5 stocks that trade
    crores of shares (YESBANK, IDEA …) decided the reading for their whole sector.
  * Illiquid stocks (median turnover under MIN_TURNOVER_CR) are excluded: they add noise, not information.
  * Sessions with less than 60 % of the universe reporting (a half-loaded latest day) are dropped.

All numbers are price, volume and breadth evidence. They do not show who bought or sold, and they are not
forecasts or investment advice.
"""
from __future__ import annotations

import math
import re
from io import StringIO

import numpy as np
import pandas as pd

MIN_STOCKS = 3
MIN_COVERAGE = 0.60
CLIP_PCT = 20.0                  # NSE's widest price band; anything bigger in one day is clipped for index maths
SPIKE, REVERT = 0.25, 0.06       # bad tick = >25 % jump that reverts within 6 % the next day
MIN_TURNOVER_CR = 0.5            # median daily turnover a stock needs to count (₹ crore)
LAGS = (("1M ago", 21), ("2W ago", 10), ("1W ago", 5), ("Today", 0))
RRG_CFG = {"W": {"smooth": 3, "window": 12, "tail": 8}, "D": {"smooth": 5, "window": 20, "tail": 12}}
STRETCH_PCT, STRETCH_RSI = 8.0, 72.0
W_RS, W_BREADTH, W_MONEY, W_ACCEL = 0.35, 0.25, 0.25, 0.15
PHASES = ("LEADING", "WEAKENING", "LAGGING", "IMPROVING")
ACTIONS = ("ROTATING IN", "LEADING", "STRETCHED", "WATCH", "WEAKENING", "LAGGING")
PERIODS = {"r1d": 1, "r1w": 5, "r2w": 10, "r1m": 21, "r3m": 63, "r6m": 126}


# ═════════════════════════ helpers ═════════════════════════
def _ema(df, n):
    return df.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(df, n=14):
    d = df.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return r.where(~((dn == 0) & up.notna()), 100.0)


def _chg(x, n):
    return (x / x.shift(n) - 1) * 100


def _last(df, lag=0):
    if len(df) > lag:
        return df.iloc[-1 - lag]
    return pd.Series(np.nan, index=df.columns)


def _f(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def norm_series(s) -> pd.Series:
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s = s.copy()
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index().astype(float)


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    for c in ("High", "Low", "Close", "Volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce") if c in d else np.nan
    d = d[d["Close"] > 0]
    d["High"] = d["High"].where(d["High"] > 0, d["Close"])
    d["Low"] = d["Low"].where(d["Low"] > 0, d["Close"])
    d["Volume"] = d["Volume"].fillna(0.0)
    return d[["High", "Low", "Close", "Volume"]]


def compass(deg) -> str:
    deg = _f(deg)
    if deg is None:
        return "·"
    deg %= 360
    return "↗" if deg < 90 else "↖" if deg < 180 else "↙" if deg < 270 else "↘"


def quadrant(x, y) -> str:
    x, y = _f(x), _f(y)
    if x is None or y is None:
        return "—"
    if x >= 100:
        return "LEADING" if y >= 100 else "WEAKENING"
    return "IMPROVING" if y >= 100 else "LAGGING"


# ═════════════════════════ 1 · panels ═════════════════════════
def build_panels(frames: dict, meta: pd.DataFrame, min_turnover_cr: float = MIN_TURNOVER_CR) -> dict:
    """frames {"SYM.NS": OHLCV} + meta[symbol, company, nse_sector, group, family] -> aligned daily panels."""
    meta = meta.drop_duplicates("symbol").set_index("symbol")
    clean, missing = {}, []
    for s in meta.index:
        f = frames.get(f"{s}.NS")
        if f is None or len(f) < 60:
            missing.append(s)
            continue
        try:
            d = _frame(f)
        except Exception:
            missing.append(s)
            continue
        if len(d) >= 60:
            clean[s] = d
        else:
            missing.append(s)
    if len(clean) < 30:
        raise ValueError(f"only {len(clean)} stocks returned usable price history (need 30+)")

    has_all = pd.concat({s: pd.Series(1.0, index=d.index) for s, d in clean.items()}, axis=1).sort_index()
    cover = has_all.notna().sum(axis=1) / len(clean)
    ref = float(cover.tail(30).median())
    good = cover.index[cover >= MIN_COVERAGE * ref]
    last = good.max()
    dropped = [str(d.date()) for d in cover.index[cover.index > last]]
    idx = cover.index[cover.index <= last]
    stale = sorted(s for s, d in clean.items() if d.index[-1] < last - pd.Timedelta(days=6))
    syms = [s for s in clean if s not in stale]

    def pan(col):
        return pd.concat({s: clean[s][col] for s in syms}, axis=1).reindex(idx)

    C0 = pan("Close")
    has = C0.notna()
    C, H, L = C0.ffill(), pan("High").ffill(), pan("Low").ffill()
    V = pan("Volume").fillna(0.0).where(has, 0.0)

    prev, nxt = C.shift(1), C.shift(-1)
    bad = ((C / prev - 1).abs() > SPIKE) & ((nxt / prev - 1).abs() < REVERT) & has
    n_bad = int(bad.to_numpy().sum())
    if n_bad:
        C, H, L = C.mask(bad, prev), H.mask(bad, prev), L.mask(bad, prev)

    R = ((C / C.shift(1) - 1) * 100).where(has)
    TV = C * V
    med = TV.where(has).tail(20).median()
    keep = med.index[med >= min_turnover_cr * 1e7].tolist()
    if len(keep) < 30:
        keep = list(med.index)
    illiquid = sorted(set(syms) - set(keep))

    m = meta.reindex(keep)
    grp = m["group"].fillna("Unclassified").astype(str)
    fam = m["family"].fillna("Other").astype(str)
    cnt = grp.value_counts()
    small = set(cnt[cnt < MIN_STOCKS].index)
    grp = grp.where(~grp.isin(small), fam + " · other")
    cnt = grp.value_counts()
    excluded = sorted(cnt[cnt < MIN_STOCKS].index)
    members = grp[~grp.isin(excluded) & (grp != "Unclassified")]

    sub = lambda df: df[keep]
    return {"C": sub(C), "H": sub(H), "L": sub(L), "V": sub(V), "TV": sub(TV), "R": sub(R), "has": sub(has),
            "group": grp, "family": fam, "members": members, "meta": m,
            "quality": {"asof": str(last.date()), "coverage": float(cover.loc[last]), "stocks_requested": int(len(meta)),
                        "stocks_loaded": len(clean), "stocks_used": len(keep), "in_sectors": int(len(members)),
                        "missing": missing, "stale": stale, "illiquid": illiquid, "dropped_sessions": dropped,
                        "bad_ticks": n_bad, "folded_groups": sorted(small), "excluded_groups": excluded,
                        "sessions": int(len(idx)), "min_turnover_cr": min_turnover_cr}}


# ═════════════════════════ 2 · relative rotation graph ═════════════════════════
def rrg(levels: pd.DataFrame, bench: pd.Series, freq: str = "W"):
    """JdK-style RS-Ratio / RS-Momentum (z-score normalised around 100). freq 'W' weekly or 'D' daily."""
    cfg = RRG_CFG[freq]
    lv = levels.astype(float)
    bb = bench.reindex(lv.index).ffill()
    if freq == "W":
        lv, bb = lv.resample("W-FRI").last(), bb.resample("W-FRI").last()
    rs = 100 * lv.div(bb, axis=0)
    rs_s = rs.ewm(span=cfg["smooth"], adjust=False).mean()
    m = cfg["window"]
    ratio = 100 + (rs_s - rs_s.rolling(m).mean()) / rs_s.rolling(m).std().replace(0, np.nan)
    roc = (ratio / ratio.shift(1) - 1) * 100
    roc_s = roc.ewm(span=cfg["smooth"], adjust=False).mean()
    mom = 100 + (roc_s - roc_s.rolling(m).mean()) / roc_s.rolling(m).std().replace(0, np.nan)
    return ratio, mom


def rrg_summary(ratio: pd.DataFrame, mom: pd.DataFrame, tail: int):
    """Per column: current point, phase, periods in phase, previous phase, heading (degrees) and tail."""
    out, tails = {}, {}
    for s in ratio.columns:
        pts = pd.concat([ratio[s], mom[s]], axis=1).dropna()
        if len(pts) < 2:
            continue
        xy = [(float(a), float(b)) for a, b in pts.itertuples(index=False)]
        tails[s] = xy[-tail:]
        q = [quadrant(x, y) for x, y in xy]
        cur, n = q[-1], 0
        for v in reversed(q):
            if v != cur:
                break
            n += 1
        prev = q[-1 - n] if n < len(q) else "—"
        x0, y0 = xy[-3] if len(xy) >= 3 else xy[0]
        x1, y1 = xy[-1]
        head = math.degrees(math.atan2(y1 - y0, x1 - x0)) if (x1 != x0 or y1 != y0) else np.nan
        out[s] = {"rrg_x": x1, "rrg_y": y1, "phase": cur, "in_phase": n, "phase_prev": prev, "heading": head,
                  "rrg_dist": math.hypot(x1 - 100, y1 - 100), "rrg_speed": math.hypot(x1 - x0, y1 - y0)}
    return out, tails


# ═════════════════════════ 3 · classification ═════════════════════════
def classify_row(r: dict, bench: str = "MARKET"):
    """-> (action, reasons, warnings) for one sector row."""
    ph = r.get("phase")
    head, score = _f(r.get("heading")), _f(r.get("score")) or 0.0
    d20, d50, rsi_i = _f(r.get("dist20")), _f(r.get("dist50")), _f(r.get("idx_rsi"))
    p20, dp20 = _f(r.get("pct20")), _f(r.get("dpct20_5"))
    up1, up5, dsw = _f(r.get("up_pct1d")), _f(r.get("up_pct5")), _f(r.get("dshare_w"))
    cmf, dcmf = _f(r.get("cmf")), _f(r.get("dcmf5"))
    rel, r1d, s2w = _f(r.get("rel_turn")), _f(r.get("r1d")), _f(r.get("score_2w"))
    e1, e3 = _f(r.get("ex1m")), _f(r.get("ex3m"))

    ne = head is not None and -25 <= head <= 115
    stretched = (d20 is not None and d20 > STRETCH_PCT) or (rsi_i is not None and rsi_i > STRETCH_RSI)
    dist_day = rel is not None and rel >= 1.3 and up1 is not None and up1 < 40 and (r1d or 0) < 0
    dist_trend = cmf is not None and cmf < -0.05 and (dcmf or 0) < 0
    breadth_drop = dp20 is not None and dp20 <= -20
    distribution = dist_day or dist_trend or breadth_drop
    inflow = up5 is not None and up5 >= 55 and ((cmf or 0) > 0 or (dcmf or 0) > 0)
    breadth_ok = p20 is not None and p20 >= 50 and ((dp20 or 0) >= 0 or p20 >= 65)
    above20 = d20 is not None and d20 > 0
    holding = s2w is None or score >= s2w - 5

    if (ph in ("IMPROVING", "LEADING") and ne and above20 and breadth_ok and inflow and score >= 55 and holding
            and not stretched and not distribution):
        act = "ROTATING IN"
    elif ph == "LEADING":
        act = "STRETCHED" if stretched else "LEADING"
    elif ph == "IMPROVING":
        act = "WATCH"
    elif ph == "LAGGING" and ne and (dp20 or 0) >= 10 and (up5 or 0) >= 55:
        act = "WATCH"                                   # bottoming: still lagging but turning up with participation
    elif ph == "WEAKENING":
        act = "WEAKENING"
    else:
        act = "LAGGING"

    why = []
    if e1 is not None:
        why.append(f"{'Beating' if e1 >= 0 else 'Trailing'} {bench} by {abs(e1):.1f} pp over 1 month"
                   + (f" ({e3:+.1f} pp over 3 months)" if e3 is not None else ""))
    if p20 is not None:
        why.append(f"{p20:.0f}% of its stocks above the 20-EMA" + (f" ({dp20:+.0f} pts in 5 sessions)" if dp20 is not None else ""))
    if up5 is not None:
        why.append(f"{up5:.0f}% of 5-day turnover went into rising stocks"
                   + (f"; share of market turnover {dsw:+.2f} pts this week" if dsw is not None else ""))
    if ph not in (None, "—"):
        prev = r.get("phase_prev")
        why.append(f"Rotation map: {ph.lower()} for {int(r.get('in_phase') or 0)} wk"
                   + (f" (from {str(prev).lower()})" if prev not in (None, "—", ph) else "") + f", heading {compass(head)}")
    if cmf is not None:
        why.append(f"Money flow (CMF 20, ₹-weighted) {cmf:+.2f}"
                   + (", rising" if (dcmf or 0) > 0.02 else ", falling" if (dcmf or 0) < -0.02 else ""))

    warn = []
    if stretched:
        warn.append(f"Stretched: {d20:+.1f}% from its 20-EMA" + (f", RSI {rsi_i:.0f}" if rsi_i and rsi_i > 70 else "")
                    + " — late buyers often get shaken out first")
    if dist_day:
        warn.append(f"Turnover {rel:.1f}× normal on a down day: selling into volume")
    if dist_trend:
        warn.append(f"Money flow negative and falling (CMF {cmf:+.2f})")
    if breadth_drop:
        warn.append(f"Participation collapsing: {dp20:+.0f} pts of stocks above the 20-EMA in 5 sessions")
    if act in ("LEADING", "STRETCHED", "ROTATING IN") and d50 is not None and d50 < 0:
        warn.append("Sector index is below its 50-EMA")
    return act, why, warn


def money_signal(dshare_w, up5) -> str:
    dsw, up = _f(dshare_w), _f(up5)
    if dsw is None or up is None:
        return "—"
    if dsw > 0 and up >= 55:
        return "BUYING ON VOLUME"
    if dsw > 0 and up <= 45:
        return "SELLING ON VOLUME"
    if up >= 55:
        return "QUIET BUYING"
    if up <= 45:
        return "QUIET SELLING"
    return "MIXED"


def cmf_trend(now, w1, w2) -> str:
    now, w1, w2 = _f(now), _f(w1), _f(w2)
    if now is None:
        return "—"
    if now > 0.05 and (w1 is None or now >= w1 - 0.01):
        return "ACCUMULATING"
    if now < -0.05 and (w1 is None or now <= w1 + 0.01):
        return "DISTRIBUTING"
    if w2 is not None and now > w2 + 0.03:
        return "IMPROVING"
    if w2 is not None and now < w2 - 0.03:
        return "WEAKENING"
    return "NEUTRAL"


# ═════════════════════════ 4 · analysis ═════════════════════════
def analyse(pan: dict, benches: dict | None = None, bench_name: str = "MARKET", weighting: str = "equal") -> dict:
    C, H, L, V, TV, R, has = (pan[k] for k in ("C", "H", "L", "V", "TV", "R", "has"))
    members = pan["members"]
    mcols = members.index.tolist()
    if len(members.unique()) < 3:
        raise ValueError("fewer than 3 sectors have enough stocks")
    idx = C.index
    RC = R.clip(-CLIP_PCT, CLIP_PCT)

    def gb(df, how):
        return getattr(df[mcols].T.groupby(members.values), how)().T

    # sector + market index (same construction for both, so the comparison is like-for-like)
    if weighting == "turnover":
        w = np.sqrt(TV.rolling(20, min_periods=1).mean().shift(1)).where(RC.notna())
        sec_ret = gb((w * RC).fillna(0), "sum") / gb(w.fillna(0), "sum").replace(0, np.nan)
        mw = w[mcols]
        mret = (mw * RC[mcols]).sum(axis=1) / mw.sum(axis=1).replace(0, np.nan)
    else:
        sec_ret = gb(RC, "mean")
        mret = RC[mcols].mean(axis=1)
    sec_idx = (1 + sec_ret.fillna(0) / 100).cumprod() * 100
    market = (1 + mret.fillna(0) / 100).cumprod() * 100

    B = {"MARKET": market}
    for k, s in (benches or {}).items():
        try:
            s = norm_series(s)
        except Exception:
            continue
        s = s.reindex(idx.union(s.index)).ffill().reindex(idx)
        if s.notna().sum() >= 120:
            B[k] = s
    if bench_name not in B:
        bench_name = "MARKET"
    b = B[bench_name].ffill()

    sret = {k: _chg(sec_idx, n) for k, n in PERIODS.items()}
    bret = {k: _chg(b, n) for k, n in PERIODS.items()}
    ex = {k: sret[k].sub(bret[k], axis=0) for k in PERIODS}

    # stock-level building blocks
    e20, e50, e200 = _ema(C, 20), _ema(C, 50), _ema(C, 200)
    ab = {n: (C > e).astype(float).where(e.notna()) for n, e in ((20, e20), (50, e50), (200, e200))}
    rsi = _rsi(C)
    hi = C.rolling(252, min_periods=120).max()
    nh5 = ((C >= hi) & has).astype(float).where(hi.notna()).rolling(5, min_periods=1).max()
    near = (C >= 0.95 * hi).astype(float).where(hi.notna())
    prior = H.rolling(20, min_periods=20).max().shift(1)
    bo10 = (C > prior).astype(float).where(prior.notna()).rolling(10, min_periods=1).max()
    rng = H - L
    mfm = (((C - L) - (H - C)) / rng.where(rng > 0)).fillna(0.0).where(has, 0.0)

    # sector aggregates (dates x sectors)
    pct20, pct50, pct200 = (gb(ab[n], "mean") * 100 for n in (20, 50, 200))
    avg_rsi = gb(rsi, "mean")
    T = gb(TV, "sum")
    share = T.div(TV[mcols].sum(axis=1).replace(0, np.nan), axis=0) * 100
    up, dn = gb(TV.where(R > 0, 0.0), "sum"), gb(TV.where(R < 0, 0.0), "sum")
    up_pct1d = up / (up + dn).replace(0, np.nan) * 100
    up5, dn5 = up.rolling(5).sum(), dn.rolling(5).sum()
    up_pct5 = up5 / (up5 + dn5).replace(0, np.nan) * 100
    cmf = gb(mfm * TV, "sum").rolling(20).sum() / T.rolling(20).sum().replace(0, np.nan)
    advn, decn = gb((R > 0).astype(float), "sum"), gb((R < 0).astype(float), "sum")
    nbar = gb(has.astype(float), "sum").replace(0, np.nan)
    ad_net = (advn - decn) / nbar
    ad_net5 = (advn.rolling(5).sum() - decn.rolling(5).sum()) / nbar.rolling(5).sum()
    bo10p, nearp = gb(bo10, "mean") * 100, gb(near, "mean") * 100
    nh5c = gb(nh5.fillna(0), "sum")
    rel_turn = T / T.rolling(20).mean().shift(1).replace(0, np.nan)
    share5 = share.rolling(5).mean()
    dshare_d, dshare_w = share - share.shift(1), share5 - share5.shift(5)
    dpct20_5 = pct20 - pct20.shift(5)
    dcmf5 = cmf - cmf.shift(5)

    # sector index technicals
    se20, se50, se200 = _ema(sec_idx, 20), _ema(sec_idx, 50), _ema(sec_idx, 200)
    dist20, dist50, dist200 = ((sec_idx / e - 1) * 100 for e in (se20, se50, se200))
    idx_rsi = _rsi(sec_idx)
    dd52 = (sec_idx / sec_idx.rolling(252, min_periods=60).max() - 1) * 100
    rs_line = sec_idx.div(b, axis=0)
    ratio_d = 100 * rs_line / rs_line.rolling(50, min_periods=50).mean()
    mom_d = 100 * ratio_d / ratio_d.shift(10)

    # composite score: cross-sectional percentile ranks, so it always says which sectors are relatively best
    pr = lambda df: df.rank(axis=1, pct=True)
    rs_c = 0.2 * pr(ex["r1w"]) + 0.4 * pr(ex["r1m"]) + 0.4 * pr(ex["r3m"])
    br_c = 0.5 * pr(pct50) + 0.3 * pr(pct20) + 0.2 * pr(dpct20_5)
    mo_c = 0.4 * pr(up_pct5) + 0.3 * pr(dshare_w) + 0.3 * pr(cmf)
    score = 100 * (W_RS * rs_c + W_BREADTH * br_c + W_MONEY * mo_c + W_ACCEL * pr(mom_d))

    groups = list(sec_idx.columns)
    n_members = members.value_counts()
    fam_of = pan["family"].reindex(mcols).groupby(members.values).agg(lambda s: s.mode().iat[0] if len(s) else "Other")
    tbl = pd.DataFrame({
        "family": fam_of.reindex(groups), "stocks": n_members.reindex(groups),
        "score": _last(score), "score_1w": _last(score, 5), "score_2w": _last(score, 10), "score_1m": _last(score, 21),
        **{k: _last(sret[k]) for k in PERIODS},
        "ex1w": _last(ex["r1w"]), "ex2w": _last(ex["r2w"]), "ex1m": _last(ex["r1m"]), "ex3m": _last(ex["r3m"]), "ex6m": _last(ex["r6m"]),
        "pct20": _last(pct20), "pct50": _last(pct50), "pct200": _last(pct200), "dpct20_5": _last(dpct20_5),
        "dpct50_20": _last(pct50) - _last(pct50, 20), "avg_rsi": _last(avg_rsi),
        "turnover_cr": _last(T) / 1e7, "turn_avg20_cr": T.tail(20).mean() / 1e7, "share": _last(share),
        "dshare_d": _last(dshare_d), "dshare_w": _last(dshare_w), "rel_turn": _last(rel_turn),
        "up_pct1d": _last(up_pct1d), "up_pct5": _last(up_pct5), "cmf": _last(cmf), "cmf_1w": _last(cmf, 5),
        "cmf_2w": _last(cmf, 10), "dcmf5": _last(dcmf5), "adv": _last(advn), "dec": _last(decn),
        "ad_net": _last(ad_net), "ad_net5": _last(ad_net5), "bo10": _last(bo10p), "nh5": _last(nh5c), "near_hi": _last(nearp),
        "dist20": _last(dist20), "dist50": _last(dist50), "dist200": _last(dist200), "idx_rsi": _last(idx_rsi),
        "dd52": _last(dd52), "rs_rank": _last(rs_c.rank(axis=1, ascending=False)),
        "rs_rank_2w": _last(rs_c.rank(axis=1, ascending=False), 10),
    }, index=groups)
    tbl.index.name = "sector"

    rrg_out = {}
    for fq in ("W", "D"):
        summ, tails = rrg_summary(*rrg(sec_idx, b, fq), RRG_CFG[fq]["tail"])
        rrg_out[fq] = (summ, tails)
    for k, dflt in (("rrg_x", np.nan), ("rrg_y", np.nan), ("phase", "—"), ("in_phase", 0), ("phase_prev", "—"),
                    ("heading", np.nan), ("rrg_dist", np.nan), ("rrg_speed", np.nan)):
        tbl[k] = [rrg_out["W"][0].get(g, {}).get(k, dflt) for g in groups]
    tbl["phase_d"] = [rrg_out["D"][0].get(g, {}).get("phase", "—") for g in groups]

    # leaders: strongest liquid members (1-month return), preferring stocks above their 50-EMA
    r1m_s, tv20, ab50 = _chg(C, 21).iloc[-1], TV.tail(20).mean(), ab[50].iloc[-1]
    leaders = {}
    for g, syms in members.groupby(members.values).groups.items():
        syms = list(syms)
        liq = tv20.reindex(syms)
        cand = [s for s in syms if liq[s] >= liq.median()] or syms
        strong = [s for s in cand if ab50.get(s) == 1.0]
        ranked = r1m_s.reindex(cand).dropna().sort_values(ascending=False).index.tolist()
        leaders[g] = list(dict.fromkeys([s for s in ranked if s in strong] + ranked))[:3]
    tbl["leaders"] = [", ".join(leaders.get(g, [])) for g in groups]

    acts, whys, warns = [], [], []
    for g, row in tbl.iterrows():
        a, w_, x_ = classify_row(row.to_dict(), bench_name)
        acts.append(a)
        whys.append(w_)
        warns.append(x_)
    tbl["action"], tbl["why"], tbl["warn"] = acts, whys, warns
    tbl["money_signal"] = [money_signal(a, b_) for a, b_ in zip(tbl["dshare_w"], tbl["up_pct5"])]
    tbl["cmf_trend"] = [cmf_trend(a, b_, c_) for a, b_, c_ in zip(tbl["cmf"], tbl["cmf_1w"], tbl["cmf_2w"])]
    tbl = tbl.sort_values("score", ascending=False, na_position="last").reset_index()

    snap = lambda df: pd.DataFrame({lab: _last(df, lag) for lab, lag in LAGS})
    hist = {"score": snap(score), "rs_rank": snap(rs_c.rank(axis=1, ascending=False)), "cmf": snap(cmf),
            "pct20": snap(pct20), "share": snap(share5), "up5": snap(up_pct5)}
    wk = score.resample("W-FRI").last().dropna(how="all").tail(12)
    rank_w = wk.rank(axis=1, ascending=False).T
    rank_w.columns = [d.strftime("%d %b") for d in wk.index]

    uni_up = TV[mcols].where(R[mcols] > 0, 0.0).sum(axis=1)
    uni_dn = TV[mcols].where(R[mcols] < 0, 0.0).sum(axis=1)
    universe = pd.DataFrame({
        "above20": ab[20][mcols].mean(axis=1) * 100, "above50": ab[50][mcols].mean(axis=1) * 100,
        "above200": ab[200][mcols].mean(axis=1) * 100,
        "adv": (R[mcols] > 0).sum(axis=1), "dec": (R[mcols] < 0).sum(axis=1),
        "up_turn5": uni_up.rolling(5).sum() / (uni_up + uni_dn).rolling(5).sum().replace(0, np.nan) * 100,
        "market": market}).tail(250)

    meta = pan["meta"]
    vavg = V.shift(1).rolling(20, min_periods=10).mean().iloc[-1]
    stocks = pd.DataFrame({
        "symbol": C.columns, "company": meta["company"].reindex(C.columns).fillna("").values,
        "sector": pan["group"].reindex(C.columns).values, "price": C.iloc[-1].values,
        "r1d": R.iloc[-1].values, "r1w": _chg(C, 5).iloc[-1].values, "r1m": _chg(C, 21).iloc[-1].values,
        "r3m": _chg(C, 63).iloc[-1].values, "r6m": _chg(C, 126).iloc[-1].values, "rsi": rsi.iloc[-1].values,
        "ab20": ab[20].iloc[-1].values, "ab50": ab[50].iloc[-1].values, "ab200": ab[200].iloc[-1].values,
        "dist20": ((C / e20 - 1) * 100).iloc[-1].values, "dist50": ((C / e50 - 1) * 100).iloc[-1].values,
        "from_hi": ((C / hi - 1) * 100).iloc[-1].values, "vol_x": (V.iloc[-1] / vavg.replace(0, np.nan)).values,
        "turnover_cr": (TV.iloc[-1] / 1e7).values, "avg_turn_cr": (TV.tail(20).mean() / 1e7).values,
        "bo10": bo10.iloc[-1].values, "nh5": nh5.iloc[-1].values,
        "cmf": ((mfm * V).rolling(20).sum() / V.rolling(20).sum().replace(0, np.nan)).iloc[-1].values,
    })
    mr = {k: float(_chg(market, n).iloc[-1]) for k, n in (("r1m", 21), ("r3m", 63))}
    stocks["ex1m"], stocks["ex3m"] = stocks["r1m"] - mr["r1m"], stocks["r3m"] - mr["r3m"]
    raw = 0.4 * stocks["r3m"].fillna(stocks["r1m"]) + 0.4 * stocks["r1m"] + 0.2 * stocks["r6m"].fillna(stocks["r3m"])
    stocks["rs_rating"] = (raw.rank(pct=True) * 99).round()
    stocks["trend"] = [trend_label(a, b_, c_) for a, b_, c_ in zip(stocks["ab20"], stocks["ab50"], stocks["ab200"])]
    stocks = stocks.replace([np.inf, -np.inf], np.nan)

    quality = dict(pan["quality"])
    quality.update({"weighting": weighting, "bench": bench_name, "sectors": len(groups)})
    return {"table": tbl.replace([np.inf, -np.inf], np.nan), "hist": hist, "rank_weeks": rank_w, "rrg": rrg_out,
            "stocks": stocks, "sector_index": sec_idx, "bench": pd.DataFrame(B), "bench_name": bench_name,
            "share": share, "universe": universe, "asof": quality["asof"], "quality": quality}


def trend_label(ab20, ab50, ab200) -> str:
    a, b, c = (None if _f(x) is None else bool(x) for x in (ab20, ab50, ab200))
    if a and b and c:
        return "STRONG UPTREND"
    if b and c:
        return "PULLBACK IN UPTREND" if not a else "UPTREND"
    if a and b:
        return "UPTREND"
    if a and not b:
        return "RECOVERING"
    if a is None and b is None:
        return "—"
    return "DOWNTREND"


# ═════════════════════════ 5 · official NSE indices ═════════════════════════
def index_view(closes: pd.DataFrame, bench: pd.Series, bench_label: str = "NIFTY 50"):
    """Official sector indices -> (table, weekly RRG tails). Same phase logic as the stock-built sectors."""
    P = pd.DataFrame({c: norm_series(closes[c]) for c in closes.columns}).sort_index().ffill()
    P = P.loc[:, P.notna().sum() >= 120]
    b = norm_series(bench).reindex(P.index.union(norm_series(bench).index)).ffill().reindex(P.index)
    if P.shape[1] < 3 or b.notna().sum() < 120:
        raise ValueError("not enough index history")
    summ, tails = rrg_summary(*rrg(P, b, "W"), RRG_CFG["W"]["tail"])
    rsi, e = _rsi(P).iloc[-1], {n: _ema(P, n).iloc[-1] for n in (10, 20, 50, 200)}
    last = P.iloc[-1]
    rows = []
    for c in P.columns:
        s = summ.get(c, {})
        row = {"index": c, "price": float(last[c]), "rsi_d": _f(rsi[c]), "phase": s.get("phase", "—"),
               "in_phase": s.get("in_phase", 0), "heading": s.get("heading"), "rrg_x": s.get("rrg_x"), "rrg_y": s.get("rrg_y")}
        for k, n in (("r1d", 1), ("r1w", 5), ("r1m", 21), ("r3m", 63)):
            row[k] = _f(_chg(P[c], n).iloc[-1])
            if k != "r1d":
                row["ex" + k[1:]] = None if row[k] is None else row[k] - _f(_chg(b, n).iloc[-1])
        for n in (10, 20, 50, 200):
            ok = pd.notna(e[n][c])
            row[f"ab{n}"] = bool(last[c] > e[n][c]) if ok else np.nan
            row[f"dist{n}"] = float((last[c] / e[n][c] - 1) * 100) if ok else np.nan
        rows.append(row)
    return pd.DataFrame(rows), tails


# ═════════════════════════ 6 · NSDL fortnightly sector-wise FPI data ═════════════════════════
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
    mon = {"Sept": "Sep", "SEPT": "SEP"}.get(mon.strip().rstrip("."), mon.strip().rstrip("."))
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return pd.to_datetime(f"{mon.title()} {int(day)} {year}", format=fmt)
        except ValueError:
            continue
    return None


def parse_nsdl_sector_html(text: str) -> pd.DataFrame:
    """Long frame [date, sector, kind ('net' | 'auc'), value (₹ crore, equity)] from one NSDL report."""
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
