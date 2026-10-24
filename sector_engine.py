"""
sector_engine.py — Arka stock-universe SECTOR analytics (pure functions, no network).

Input : {"SYM.NS": OHLCV DataFrame, "^NSEI": benchmark}  +  meta DataFrame[symbol, industry, company]
Output: per-sector table (ranking / turnover A-D / turnover trend / Chaikin money flow / relative
        strength history), per-stock table, and 60-day series for charts.

Definitions (all computed from the supplied daily bars):
  turnover      close x volume of the latest session, in Rs crore (Yahoo volume; an approximation of NSE value)
  % change      equal-weight average of the sector's stocks (D = 1 session, W = 5 sessions)
  share %       sector turnover / total universe turnover;  delta share = change in percentage points
  A/D net       (advancers - decliners) / stocks
  turnover A/D  turnover of stocks closing up / turnover of stocks closing down
  % > 20EMA     share of stocks closing above their 20-EMA
  % BO 10d      share of stocks that closed above their prior 20-day high at least once in the last 10 sessions
  Chaikin CMF   sum(money-flow-volume, 20) / sum(volume, 20); money-flow multiplier = ((C-L)-(H-C))/(H-L)
  RS history    percentile (0-99) of the sector's 20-session performance vs the benchmark among sectors,
                at T-30, T-15, T-5 and today. Sector index = equal-weight mean of its stocks' daily returns.

Honest limits: "sector" is the NSE industry classification from the index constituent files;
turnover is not exchange-reported value; CMF/turnover are evidence of activity, not of who traded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RS_OFFSETS = (30, 15, 5, 0)
MIN_STOCKS_PER_SECTOR = 3


def _panel(frames: dict, syms: list, col: str, index) -> pd.DataFrame:
    return pd.DataFrame({s: frames[f"{s}.NS"][col] for s in syms}).reindex(index)


def _pct_rank(row: pd.Series) -> pd.Series:
    return (row.rank(pct=True) * 99).round()


def analyse_universe(frames: dict, meta: pd.DataFrame, bench: str = "^NSEI") -> dict:
    meta = meta.drop_duplicates("symbol")
    usable = [s for s in meta["symbol"] if f"{s}.NS" in frames and len(frames[f"{s}.NS"]) >= 60]
    if len(usable) < 30:
        raise ValueError(f"only {len(usable)} stocks returned usable history (need 30+)")
    all_dates = sorted(set().union(*[set(frames[f"{s}.NS"].index) for s in usable]))
    idx = pd.DatetimeIndex(all_dates)
    last_date = idx[-1]
    # drop stale tickers (no bar in the last 4 calendar days)
    syms = [s for s in usable if (last_date - frames[f"{s}.NS"].index[-1]).days <= 4]
    stale = sorted(set(usable) - set(syms))
    if len(syms) < 30:
        raise ValueError("too many stale tickers")

    close = _panel(frames, syms, "Close", idx).ffill()
    high = _panel(frames, syms, "High", idx).ffill()
    low = _panel(frames, syms, "Low", idx).ffill()
    vol = _panel(frames, syms, "Volume", idx).fillna(0)
    ind = meta.set_index("symbol")["industry"].reindex(syms).fillna("Unclassified").astype(str)
    counts = ind.value_counts()
    keep_ind = counts[counts >= MIN_STOCKS_PER_SECTOR].index
    ind = ind[ind.isin(keep_ind)]
    syms = ind.index.tolist()
    close, high, low, vol = close[syms], high[syms], low[syms], vol[syms]

    turn = close * vol
    ret1 = close.pct_change() * 100
    ret5 = (close / close.shift(5) - 1) * 100
    ema20 = close.ewm(span=20, adjust=False).mean()
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).where(~((dn == 0) & up.notna()), 100.0)
    brk = close > high.rolling(20).max().shift(1)
    bo10 = brk.tail(10).any()
    rng = (high - low).replace(0, np.nan)
    mfv = ((((close - low) - (high - close)) / rng).fillna(0)) * vol
    cmf_stock = mfv.rolling(20).sum() / vol.rolling(20).sum().replace(0, np.nan)

    grp = lambda df: df.T.groupby(ind).sum().T          # dates x sectors, summed across stocks
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

    # sector index (equal-weight) and RS-rank history vs benchmark
    sec_ret = ret1.T.groupby(ind).mean().T / 100
    sec_idx = (1 + sec_ret.fillna(0)).cumprod()
    b = frames.get(bench)
    rs_hist = pd.DataFrame(np.nan, index=sec_idx.columns, columns=[f"rs_t{o}" for o in RS_OFFSETS])
    rel1w = rel1m = pd.Series(np.nan, index=sec_idx.columns)
    if b is not None and len(b) > 25:
        bc = b["Close"].astype(float).reindex(idx).ffill()
        rel = sec_idx.div(sec_idx.shift(20)).div(bc.div(bc.shift(20)), axis=0)
        for o in RS_OFFSETS:
            pos = len(rel) - 1 - o
            if pos >= 0:
                rs_hist[f"rs_t{o}"] = _pct_rank(rel.iloc[pos])
        rel1w = ((sec_idx.iloc[-1] / sec_idx.iloc[-6]) / (bc.iloc[-1] / bc.iloc[-6]) - 1) * 100 if len(sec_idx) > 6 else rel1w
        rel1m = ((sec_idx.iloc[-1] / sec_idx.iloc[-22]) / (bc.iloc[-1] / bc.iloc[-22]) - 1) * 100 if len(sec_idx) > 22 else rel1m

    # leaders: top 3 by 1W return among the sector's more liquid names
    leaders = {}
    tl = turn.iloc[-1]
    for sec, members in ind.groupby(ind).groups.items():
        m = list(members)
        liquid = [s for s in m if tl[s] >= tl[m].quantile(.25)]
        pick = r5_last[liquid].dropna().sort_values(ascending=False).head(3).index.tolist()
        leaders[sec] = ", ".join(pick)

    sectors = pd.DataFrame({
        "sector": n.index, "stocks": n.values,
        "turnover_cr": (t_last / 1e7).reindex(n.index).values,
        "turn_avg20_cr": (t_avg20 / 1e7).reindex(n.index).values,
        "turn_vs20": (t_last / t_avg20.replace(0, np.nan)).reindex(n.index).values,
        "r1d": r1_last.groupby(ind).mean().reindex(n.index).values,
        "r1w": r5_last.groupby(ind).mean().reindex(n.index).values,
        "share": share.iloc[-1].reindex(n.index).values,
        "dshare_d": (share.iloc[-1] - share.iloc[-2]).reindex(n.index).values,
        "dshare_w": (share.iloc[-1] - share.iloc[-6]).reindex(n.index).values,
        "adv": adv.reindex(n.index).values, "dec": dec.reindex(n.index).values,
        "ad_net": ((adv - dec) / n).reindex(n.index).values,
        "avg_rsi": rsi.iloc[-1].groupby(ind).mean().reindex(n.index).values,
        "pct_above20": (close.iloc[-1] > ema20.iloc[-1]).groupby(ind).mean().reindex(n.index).values * 100,
        "pct_bo10": bo10.groupby(ind).mean().reindex(n.index).values * 100,
        "up_turn_cr": (up_turn / 1e7).reindex(n.index).values,
        "dn_turn_cr": (dn_turn / 1e7).reindex(n.index).values,
        "turn_ad": (up_turn / dn_turn.replace(0, np.nan)).reindex(n.index).values,
        "pct_turn_adv": (up_turn / (up_turn + dn_turn).replace(0, np.nan) * 100).reindex(n.index).values,
        "cmf": cmf_sec.iloc[-1].reindex(n.index).values,
        "cmf_5d": cmf_sec.iloc[-6].reindex(n.index).values if len(cmf_sec) > 6 else np.nan,
        "rs_1w": rel1w.reindex(n.index).values, "rs_1m": rel1m.reindex(n.index).values,
        "leaders": [leaders.get(s, "") for s in n.index],
    })
    for o in RS_OFFSETS:
        sectors[f"rs_t{o}"] = rs_hist[f"rs_t{o}"].reindex(n.index).values
    sectors = sectors.replace([np.inf, -np.inf], np.nan).sort_values("turnover_cr", ascending=False).reset_index(drop=True)

    stocks = pd.DataFrame({
        "symbol": syms, "sector": ind.values, "price": close.iloc[-1].values,
        "turnover_cr": (turn.iloc[-1] / 1e7).values, "r1d": r1_last.values, "r1w": r5_last.values,
        "rsi": rsi.iloc[-1].values, "above20": (close.iloc[-1] > ema20.iloc[-1]).values,
        "bo10": bo10.values, "cmf": cmf_stock.iloc[-1].values,
    }).replace([np.inf, -np.inf], np.nan)

    return {"sectors": sectors, "stocks": stocks,
            "cmf_series": cmf_sec.tail(60), "share_series": share.tail(60),
            "asof": str(last_date)[:10], "n_stocks": len(syms), "stale": stale,
            "unmapped": sorted(set(meta["symbol"]) - set(usable))}
