"""
Arka Trades — regime_engine.py (v1.1)

v1.1 fixes (audit): distribution-day volume compares only stocks that traded on both days (a stock with a missing
candle can no longer fake a volume jump); a rally that grinds higher for 10+ sessions without a textbook
follow-through day is no longer stuck in 'Rally attempt'.

Market regime from price, breadth, distribution and volatility, computed for every day of the panel so the
dashboard can show today's state AND prove how each state behaved historically.

Every input is known at that day's close (no look-ahead). Components, each scored 0-100:

  Trend 25%        NIFTY 50 vs its 21 / 50-day EMAs and 200-day SMA, and the slope of the 50 EMA
  Breadth 25%      % of stocks above their 50-day EMA and 200-day SMA
  Highs/lows 10%   new 52-week highs vs new lows (10-day average share of highs)
  McClellan 10%    momentum of the advance / decline ratio (EMA19 - EMA39 of (A-D)/(A+D) x 1000)
  Distribution 15% IBD distribution days on NIFTY 50: down 0.2%+ on higher volume than the day before; volume is
                   the universe's total turnover (Yahoo's index volume is unreliable). A day expires after 25
                   sessions or once NIFTY closes 5% above it.
  Volatility 10%   India VIX percentile over the past year (low and falling = calm) and its 10-day change
  Appetite 5%      average stock (equal-weight universe) vs NIFTY 50: rising = traders are taking risk

States (with memory, like IBD's market pulse):
  Correction        NIFTY below its 50 EMA with a weak score, or 10%+ below its 63-day high
  Rally attempt     after a Correction, until a follow-through day: day 4+ of the attempt, NIFTY +1.2% on higher
                    volume, without undercutting the attempt's low
  Confirmed uptrend score 65+ and fewer than 5 distribution days
  Under pressure    everything in between
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import quant_data as qd

STATES = ["Confirmed uptrend", "Under pressure", "Rally attempt", "Correction"]
EXPOSURE = {"Confirmed uptrend": 1.0, "Under pressure": 0.5, "Rally attempt": 0.25, "Correction": 0.0}
GUIDE = {"Confirmed uptrend": "Full size. Take A and B setups, up to your maximum positions.",
         "Under pressure": "Half size. Only A+ setups, tighter stops, take partial profits sooner.",
         "Rally attempt": "Pilot positions only (about a quarter size). Add only if new breakouts follow through.",
         "Correction": "Mostly cash. Protect capital, build the watchlist of stocks holding up best."}
WEIGHTS = {"trend": 0.25, "breadth": 0.25, "highs": 0.10, "mcclellan": 0.10, "distribution": 0.15, "volatility": 0.10, "appetite": 0.05}


def _lin(x, lo, hi):
    return np.clip((np.asarray(x, dtype=np.float64) - lo) / (hi - lo) * 100.0, 0, 100)


def _ema1(x, n):
    return qd.ema(np.asarray(x, dtype=np.float64)[:, None], n)[:, 0]


def compute(p: dict) -> pd.DataFrame:
    """One row per trading day with every component, the score and the state."""
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _compute(p)


def _compute(p: dict) -> pd.DataFrame:
    c = p["c"].astype(np.float64)
    h = p["h"].astype(np.float64)
    l = p["l"].astype(np.float64)
    v = p["v"].astype(np.float64)
    ic = p["idx_c"].astype(np.float64)
    T = len(ic)
    valid = ~np.isnan(c)
    n_valid = np.maximum(1, valid.sum(1))

    def share(mask, base):
        b = base.sum(1)
        return np.where(b > 0, mask.sum(1) / np.maximum(1, b) * 100.0, np.nan)

    e50, s200 = p["ema50"].astype(np.float64), p["sma200"].astype(np.float64)
    e21 = p["ema21"].astype(np.float64)
    pct20 = share(valid & ~np.isnan(e21) & (c > e21), valid & ~np.isnan(e21))
    pct50 = share(valid & ~np.isnan(e50) & (c > e50), valid & ~np.isnan(e50))
    pct200 = share(valid & ~np.isnan(s200) & (c > s200), valid & ~np.isnan(s200))

    prior_hh = qd.shift(qd.roll_max(h, 252), 1)
    prior_ll = qd.shift(qd.roll_min(l, 252), 1)
    nh = np.nansum((h >= prior_hh) & ~np.isnan(prior_hh), 1).astype(float)
    nl = np.nansum((l <= prior_ll) & ~np.isnan(prior_ll), 1).astype(float)
    hl_share = pd.Series(nh).rolling(10, min_periods=1).sum() / (pd.Series(nh + nl).rolling(10, min_periods=1).sum()).replace(0, np.nan)
    hl_share = hl_share.fillna(0.5).to_numpy() * 100

    pc = qd.shift(c, 1)
    adv = np.nansum(c > pc, 1).astype(float)
    dec = np.nansum(c < pc, 1).astype(float)
    rana = np.where(adv + dec > 0, (adv - dec) / np.maximum(1, adv + dec) * 1000, 0.0)
    mcc = _ema1(rana, 19) - _ema1(rana, 39)
    ad_line = np.cumsum(adv - dec)

    # equal-weight universe index (average daily return of stocks that traded both days)
    r = c / pc - 1
    r = np.where(np.isfinite(r) & (np.abs(r) < 0.35), r, np.nan)
    ew_ret = np.nan_to_num(np.nanmean(np.where(np.isnan(r), np.nan, r), 1))
    ew = np.cumprod(1 + ew_ret) * 100
    idx_ret = np.r_[0, ic[1:] / ic[:-1] - 1]
    idx_ret = np.nan_to_num(idx_ret)
    ratio = ew / np.where(ic > 0, ic, np.nan) * ic[np.argmax(ic > 0)] / 100
    ratio_e50 = _ema1(ratio, 50)

    # trend
    ie21, ie50, is200 = (p[k].astype(np.float64) for k in ("idx_ema21", "idx_ema50", "idx_sma200"))
    slope50 = ie50 / qd.shift(ie50[:, None], 20)[:, 0] - 1
    trend = (25.0 * (ic > ie21) + 25.0 * (ic > ie50) + 25.0 * (ic > is200) + 25.0 * (slope50 > 0))
    trend = np.where(np.isnan(is200), 50.0 * (ic > ie50) + 50.0 * (slope50 > 0), trend)

    breadth = 0.6 * _lin(pct50, 25, 70) + 0.4 * _lin(pct200, 25, 70)

    # distribution days (turnover of the whole universe as volume)
    cv = np.where(valid & np.isfinite(v), c * v, np.nan)
    turn = np.nansum(cv, 1)
    both = ~np.isnan(cv[1:]) & ~np.isnan(cv[:-1])                       # like-for-like: stocks with a candle on both days
    t_now = np.r_[np.nan, np.where(both, cv[1:], 0.0).sum(1)]
    t_prev = np.r_[np.nan, np.where(both, cv[:-1], 0.0).sum(1)]
    vol_up = t_now > t_prev
    dd_flag = (idx_ret <= -0.002) & vol_up
    dd_count = np.zeros(T)
    active = []
    for t in range(T):
        active = [d for d in active if t - d < 25 and ic[t] < ic[d] * 1.05]
        if dd_flag[t]:
            active.append(t)
        dd_count[t] = len(active)
    dist = np.select([dd_count <= 2, dd_count == 3, dd_count == 4, dd_count == 5], [100, 70, 50, 25], 0).astype(float)

    vix = p["vix"].astype(np.float64)
    vix_pct = pd.Series(vix).rolling(252, min_periods=60).rank(pct=True).to_numpy() * 100
    vix_chg = vix / qd.shift(vix[:, None], 10)[:, 0] - 1
    vol = np.where(np.isnan(vix_pct), 50.0, 100 - vix_pct) - np.where(vix_chg > 0.15, 20, 0)
    vol = np.clip(vol, 0, 100)

    appetite = np.where(ratio > ratio_e50, 100.0, 30.0)
    mc_s = _lin(mcc, -80, 80)
    comp = {"trend": trend, "breadth": np.nan_to_num(breadth, nan=50), "highs": hl_share, "mcclellan": mc_s,
            "distribution": dist, "volatility": vol, "appetite": appetite}
    score = sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)

    # states with memory + follow-through days
    hi63 = pd.Series(ic).rolling(63, min_periods=1).max().to_numpy()
    dd_from_hi = ic / hi63 - 1
    state = np.empty(T, dtype=object)
    ftd = np.zeros(T, dtype=bool)
    prev = "Under pressure"
    att_low, att_day = None, 0
    for t in range(T):
        corr = (ic[t] < ie50[t] and score[t] < 45) or dd_from_hi[t] <= -0.10
        if prev in ("Correction", "Rally attempt"):
            if att_low is None or l_idx(p, t) < att_low:           # new low: the attempt restarts
                att_low, att_day = l_idx(p, t), 0
            elif idx_ret[t] > 0 or att_day > 0:
                att_day += 1
            if att_day >= 4 and idx_ret[t] >= 0.012 and vol_up[t]:
                ftd[t] = True
                s = "Confirmed uptrend" if score[t] >= 50 else "Under pressure"
                att_low, att_day = None, 0
            elif corr and att_day == 0:
                s = "Correction"
            else:
                s = "Rally attempt" if att_day > 0 else "Correction"
                if score[t] >= 70 and ic[t] > ie50[t] and dd_count[t] < 5:     # strong recovery without a textbook FTD
                    s, att_low, att_day = "Confirmed uptrend", None, 0
                elif att_day >= 10 and not corr and score[t] >= 55 and ic[t] > ie50[t]:
                    s, att_low, att_day = "Under pressure", None, 0           # slow grind higher: stop waiting for an FTD
        elif corr:
            s, att_low, att_day = "Correction", l_idx(p, t), 0
        elif score[t] >= 65 and dd_count[t] < 5:
            s = "Confirmed uptrend"
        elif prev == "Confirmed uptrend" and score[t] >= 52 and dd_count[t] < 6 and ic[t] > ie50[t] * 0.985:
            s = "Confirmed uptrend"                                   # hysteresis: one soft day does not flip the state
        else:
            s = "Under pressure"
        state[t] = s
        prev = s

    df = pd.DataFrame({
        "close": ic, "ema21": ie21, "ema50": ie50, "sma200": is200, "ret": idx_ret, "ew": ew, "ew_ret": ew_ret, "ratio": ratio,
        "pct20": pct20, "pct50": pct50, "pct200": pct200, "nh": nh, "nl": nl, "hl_share": hl_share, "adv": adv, "dec": dec,
        "ad_line": ad_line, "mcclellan": mcc, "dd_flag": dd_flag, "dd_count": dd_count, "vix": vix, "vix_pct": vix_pct,
        "vix_chg": vix_chg, "turnover": turn, "dd_from_hi": dd_from_hi, "ftd": ftd, "score": score, "state": state,
        **{f"c_{k}": comp[k] for k in comp}}, index=p["dates"])
    df["n_stocks"] = n_valid
    warm = max(260, int(np.argmax(~np.isnan(is200))) if np.any(~np.isnan(is200)) else 260)
    return df.iloc[min(warm, len(df) - 1):]


def l_idx(p, t):
    v = p["idx_l"][t]
    return float(v) if np.isfinite(v) else float(p["idx_c"][t])


def forward_table(df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    """How NIFTY and the average stock behaved over the next `horizon` sessions after each state (the proof)."""
    fwd_i = df["close"].shift(-horizon) / df["close"] - 1
    fwd_e = df["ew"].shift(-horizon) / df["ew"] - 1
    worst = (df["close"].rolling(horizon).min().shift(-horizon) / df["close"] - 1)
    rows = []
    for s in STATES:
        m = (df["state"] == s) & fwd_i.notna()
        if m.sum() == 0:
            rows.append({"state": s, "days": 0})
            continue
        rows.append({"state": s, "days": int(m.sum()), "share": m.mean() * 100,
                     "nifty_avg": fwd_i[m].mean() * 100, "nifty_up": (fwd_i[m] > 0).mean() * 100,
                     "ew_avg": fwd_e[m].mean() * 100, "ew_up": (fwd_e[m] > 0).mean() * 100, "worst_avg": worst[m].mean() * 100})
    return pd.DataFrame(rows)


def episodes(df: pd.DataFrame) -> list:
    """Contiguous runs of the same state: (state, start, end, days, nifty move %)."""
    out = []
    st_ = df["state"].to_numpy()
    if not len(st_):
        return out
    s0 = 0
    for i in range(1, len(st_) + 1):
        if i == len(st_) or st_[i] != st_[s0]:
            a, b = df.index[s0], df.index[i - 1]
            out.append((st_[s0], a, b, i - s0, (df["close"].iloc[i - 1] / df["close"].iloc[s0] - 1) * 100))
            s0 = i
    return out


def reasons(row: pd.Series, df: pd.DataFrame) -> list:
    """Plain-language lines for today's reading: (tone, text). tone in {'good','warn','bad','info'}."""
    out = []
    above = [n for n, k in (("21 EMA", "ema21"), ("50 EMA", "ema50"), ("200 DMA", "sma200")) if row["close"] > row[k]]
    below = [n for n, k in (("21 EMA", "ema21"), ("50 EMA", "ema50"), ("200 DMA", "sma200")) if row["close"] <= row[k]]
    if not below:
        out.append(("good", "NIFTY 50 is above its 21 EMA, 50 EMA and 200 DMA."))
    elif not above:
        out.append(("bad", "NIFTY 50 is below its 21 EMA, 50 EMA and 200 DMA."))
    else:
        out.append(("warn", f"NIFTY 50 is above its {', '.join(above)} but below its {', '.join(below)}."))
    p50, p200 = row["pct50"], row["pct200"]
    tone = "good" if p50 >= 60 else "bad" if p50 < 35 else "warn"
    out.append((tone, f"{p50:.0f}% of stocks are above their 50 EMA and {p200:.0f}% above their 200 DMA."))
    near_high = row["dd_from_hi"] > -0.02
    if near_high and p50 < 50:
        out.append(("warn", "Narrow market: NIFTY is near its high but fewer than half the stocks are in short-term uptrends."))
    if row["pct50"] < 20:
        out.append(("info", "Breadth is washed out (under 20% above the 50 EMA). Historically this is closer to bottoms than tops."))
    dd = int(row["dd_count"])
    out.append(("good" if dd <= 2 else "warn" if dd <= 4 else "bad",
                f"{dd} distribution day{'s' if dd != 1 else ''} in the last 25 sessions" + (" (heavy institutional selling)." if dd >= 5 else ".")))
    nh, nl = int(row["nh"]), int(row["nl"])
    out.append(("good" if nh > nl * 2 else "bad" if nl > nh * 2 else "info", f"{nh} new 52-week highs vs {nl} new lows today."))
    if np.isfinite(row["vix"]):
        out.append(("bad" if row["vix_chg"] > 0.15 else "good" if row["vix_pct"] < 40 else "info",
                    f"India VIX {row['vix']:.1f}, higher than {row['vix_pct']:.0f}% of the past year"
                    + (f", up {row['vix_chg'] * 100:.0f}% in 10 days." if row["vix_chg"] > 0.15 else ".")))
    recent_ftd = df["ftd"].iloc[-15:]
    if recent_ftd.any():
        out.append(("good", f"Follow-through day on {recent_ftd[recent_ftd].index[-1]:%d %b}: the new rally was confirmed on volume."))
    out.append(("good" if row["c_appetite"] >= 100 else "warn",
                "The average stock is beating NIFTY 50 (risk appetite is healthy)." if row["c_appetite"] >= 100
                else "The average stock is lagging NIFTY 50 (money is hiding in large caps)."))
    return out
