quant_analysis.py — Institutional Quant Terminal v2.0
======================================================
Methods: AFML frac-diff (Ch.5), non-overlapping triple-barrier (Ch.3),
purged walk-forward (Ch.7), Probabilistic & Deflated Sharpe (Ch.8),
GARCH(1,1)-t vol, factor attribution w/ Newey-West SEs, MC & historical
VaR/ES, stress tests, multi-horizon alpha-decay scan, bootstrap CIs,
turnover/break-even cost, vol-target + fractional-Kelly sizing,
Transaction Cost Modeling, IC/ICIR, Factor Decay, HMM, Meta-Labeling,
AI Score (Gemini), AI Tab Summaries, BUY/SELL/AVOID verdict.

Deps: pandas numpy scipy statsmodels yfinance plotly streamlit
      optional: arch, google-generativeai
"""

import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats as scistats
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

# ── Palette ──
BG, PANEL, PANEL2, BORDER = "#0B0E13", "#11151D", "#161C26", "#222B38"
ACCENT, IVORY, MUTE = "#C9A227", "#E8EDF2", "#8593A3"
GREEN, RED, BLUE = "#1FB97A", "#E8554E", "#4C8DD6"
PURPLE, ORANGE = "#9B59B6", "#E67E22"
MONO = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"
TRADING_DAYS = 252
HORIZONS = [5, 10, 21, 63]
DEFAULT_HORIZON = 5
FACTOR_PROXIES = {"MKT": "^NSEI", "LARGE": "^CNX100", "MID": "^CNXMIDCAP"}
PERIODS_TO_TRY = ["5y", "3y", "2y", "1y"]
MIN_BARS = 252


# ════════════════════════ DATA ════════════════════════
def fetch_history(symbol: str, interval: str = "1d"):
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym += ".NS"
    tk = yf.Ticker(sym)
    for period in PERIODS_TO_TRY:
        df = tk.history(period=period, interval=interval)
        if not df.empty and len(df) >= MIN_BARS:
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            try:
                info = tk.info or {}
            except Exception:
                info = {}
            return df, sym, info, period
    df_last = tk.history(period="max", interval=interval)
    if not df_last.empty:
        df_last = df_last[["Open", "High", "Low", "Close", "Volume"]].dropna()
    try:
        info = tk.info or {}
    except Exception:
        info = {}
    return df_last, sym, info, "max"


def fetch_series(yahoo_symbol: str, period: str = "5y"):
    try:
        return yf.Ticker(yahoo_symbol).history(period=period, interval="1d")["Close"].dropna()
    except Exception:
        return pd.Series(dtype=float)


def fetch_factor_proxies(period: str = "5y"):
    return {k: fetch_series(s, period) for k, s in FACTOR_PROXIES.items()}


def _rets(s):
    return np.log(s / s.shift(1)).dropna()


# ════════════════════════ 1. FRAC-DIFF ════════════════════════
def _ffd_weights(d, threshold=1e-4):
    w, k = [1.0], 1
    while abs(w[-1]) > threshold:
        w.append(-w[-1] * (d - k + 1) / k); k += 1
    return np.array(w[::-1])


def frac_diff_ffd(series, d):
    w = _ffd_weights(d); width = len(w)
    vals = series.ffill().values
    out = np.full(len(vals), np.nan)
    for i in range(width - 1, len(vals)):
        out[i] = np.dot(w, vals[i - width + 1: i + 1])
    return pd.Series(out, index=series.index)


def stationarity_analysis(close, ds=np.round(np.arange(0, 1.01, 0.05), 2), target=0.05):
    logp = np.log(close.dropna())
    fd1 = logp.diff().dropna()
    base_mem = abs(np.corrcoef(fd1, logp.loc[fd1.index])[0, 1])
    best = (1.0, np.nan, base_mem)
    for d in ds:
        fd = frac_diff_ffd(logp, d).dropna()
        if len(fd) < 50:
            continue
        try:
            p = adfuller(fd, maxlag=1, regression="c", autolag=None)[1]
        except Exception:
            continue
        mem = abs(np.corrcoef(fd, logp.loc[fd.index])[0, 1])
        best = (float(d), float(p), float(mem))
        if p < target:
            break
    d, p, mem = best
    return {"d": d, "adf_pvalue": p, "memory_retained": mem,
            "memory_first_diff": float(base_mem), "memory_gain": float(mem - base_mem)}


# ════════════════════════ 2. GARCH VOL ════════════════════════
def ewma_vol(close, lam=0.94):
    r = _rets(close); var = np.zeros(len(r)); var[0] = r.var()
    for t in range(1, len(r)):
        var[t] = lam * var[t - 1] + (1 - lam) * r.iloc[t - 1] ** 2
    return pd.Series(np.sqrt(var), index=r.index)


def garch_vol(close, horizon=DEFAULT_HORIZON):
    r = _rets(close) * 100.0
    try:
        from arch import arch_model
        res = arch_model(r, vol="Garch", p=1, q=1, mean="Constant", dist="t").fit(disp="off")
        sigma = pd.Series(res.conditional_volatility.values / 100.0, index=r.index)
        fc = res.forecast(horizon=horizon, reindex=False)
        fwd = float(np.sqrt(fc.variance.values[-1].sum())) / 100.0
        return sigma, "GARCH(1,1)-t", fwd
    except Exception:
        sigma = ewma_vol(close)
        return sigma, "EWMA(0.94)", float(sigma.iloc[-1] * np.sqrt(horizon))


def volatility_regime(sigma):
    cur = float(sigma.iloc[-1]); hist = sigma.tail(TRADING_DAYS)
    pct = float((hist < cur).mean() * 100)
    regime = "STRESSED" if pct >= 80 else "COMPRESSED" if pct <= 20 else "NORMAL"
    return {"sigma_daily": cur, "sigma_annual": cur * np.sqrt(TRADING_DAYS),
            "percentile": pct, "regime": regime}


# ════════════════════════ 3. FACTOR ATTRIBUTION ════════════════════════
def _newey_west_se(X, resid, lags):
    XtX_inv = np.linalg.pinv(X.T @ X)
    S = (X * resid[:, None]).T @ (X * resid[:, None])
    for l in range(1, lags + 1):
        w = 1.0 - l / (lags + 1.0)
        G = (X[l:] * resid[l:, None]).T @ (X[:-l] * resid[:-l, None])
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    return np.sqrt(np.maximum(np.diag(cov), 0))


def factor_attribution(close, proxies):
    y = _rets(close).rename("y")
    mkt = _rets(proxies.get("MKT", pd.Series(dtype=float)))
    large = _rets(proxies.get("LARGE", pd.Series(dtype=float)))
    mid = _rets(proxies.get("MID", pd.Series(dtype=float)))
    cols = {"MKT": mkt}
    if len(large) and len(mid):
        cols["SIZE"] = (mid - large).dropna().rename("SIZE")
    if len(mkt):
        cols["MOM"] = mkt.rolling(60).mean().dropna().rename("MOM")
    data = pd.concat([y] + list(cols.values()), axis=1, join="inner").dropna()
    if len(data) < 60:
        return {"betas": {}, "tstats": {}, "alpha_ann": np.nan, "r2": np.nan, "n": len(data)}
    Y = data["y"].values
    X = np.column_stack([np.ones(len(data)), data.drop(columns=["y"]).values])
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ coef
    se = _newey_west_se(X, resid, lags=5)
    tstat = coef / np.where(se > 0, se, np.nan)
    ss_res = float((resid ** 2).sum()); ss_tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    names = ["ALPHA"] + list(data.drop(columns=["y"]).columns)
    betas = {n: float(c) for n, c in zip(names, coef)}
    tstats = {n: float(t) for n, t in zip(names, tstat)}
    alpha_ann = betas.pop("ALPHA") * TRADING_DAYS * 100
    return {"betas": betas, "tstats": tstats, "alpha_ann": alpha_ann, "r2": float(r2), "n": int(len(data))}


# ════════════════════════ 4. FACTOR BATTERY ════════════════════════
def factor_battery(close, bench):
    r = _rets(close)
    ann_ret = float(r.mean() * TRADING_DAYS); ann_vol = float(r.std() * np.sqrt(TRADING_DAYS))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    downside = r[r < 0].std() * np.sqrt(TRADING_DAYS)
    sortino = ann_ret / downside if downside > 0 else 0.0
    eq = (1 + r).cumprod(); mdd = float((eq / eq.cummax() - 1).min() * 100)
    var95 = float(np.percentile(r, 5) * 100); cvar95 = float(r[r <= np.percentile(r, 5)].mean() * 100)
    skew = float(r.skew()); kurt = float(r.kurtosis()); ac1 = float(r.autocorr(1)) if len(r) > 2 else 0.0
    beta = alpha = np.nan
    if len(bench) > 10:
        br = _rets(bench); j = pd.concat([r, br], axis=1, join="inner").dropna(); j.columns = ["a", "b"]
        if len(j) > 30 and j["b"].var() > 0:
            beta = float(np.cov(j["a"], j["b"])[0, 1] / j["b"].var())
            alpha = float((j["a"].mean() - beta * j["b"].mean()) * TRADING_DAYS)
    return {"ann_ret": ann_ret * 100, "ann_vol": ann_vol * 100, "sharpe": sharpe, "sortino": sortino,
            "max_dd": mdd, "var95": var95, "cvar95": cvar95, "skew": skew, "kurt": kurt,
            "autocorr1": ac1, "beta": beta, "alpha": (alpha * 100) if np.isfinite(alpha) else np.nan,
            "ret_series": r}


def fundamentals(info):
    def g(k):
        v = info.get(k)
        return v if isinstance(v, (int, float)) and np.isfinite(v) else None
    mc = g("marketCap")
    return {"name": info.get("longName") or info.get("shortName") or "—",
            "sector": info.get("sector") or "—", "industry": info.get("industry") or "—",
            "mcap_cr": (mc / 1e7) if mc else None, "pe": g("trailingPE"),
            "fwd_pe": g("forwardPE"), "pb": g("priceToBook"),
            "roe": (g("returnOnEquity") or 0) * 100 if g("returnOnEquity") is not None else None,
            "wk52_high": g("fiftyTwoWeekHigh"), "wk52_low": g("fiftyTwoWeekLow"), "beta_info": g("beta")}


# ════════════════════════ 5. RISK ════════════════════════
def historical_var_es(close, horizon=DEFAULT_HORIZON, alpha=0.05):
    r = _rets(close); hz = r.rolling(horizon).sum().dropna()
    var = float(np.percentile(hz, alpha * 100) * 100)
    es = float(hz[hz <= np.percentile(hz, alpha * 100)].mean() * 100)
    return {"var": var, "es": es}


def monte_carlo_var(close, horizon=DEFAULT_HORIZON, n_sims=20000, alpha=0.05, seed=7):
    r = _rets(close); mu, sd = r.mean(), r.std()
    k = max(4.5, 6.0 / max(scistats.kurtosis(r, fisher=True), 1e-3) + 4)
    rng = np.random.default_rng(seed)
    z = scistats.t.rvs(df=k, size=(n_sims, horizon), random_state=rng) / np.sqrt(k / (k - 2))
    sims = (mu + sd * z).sum(axis=1)
    var = float(np.percentile(sims, alpha * 100) * 100)
    es = float(sims[sims <= np.percentile(sims, alpha * 100)].mean() * 100)
    return {"var": var, "es": es, "t_df": float(k)}


def stress_scenarios(price, beta):
    if not np.isfinite(beta):
        beta = 1.0
    shocks = {"GFC 2008 (-50% mkt)": -0.50, "COVID Mar-2020 (-38%)": -0.38,
              "Rate Shock (-12%)": -0.12, "Flash Crash (-8%)": -0.08}
    return {n: {"pct": beta * m * 100, "price": price * (1 + beta * m)} for n, m in shocks.items()}


# ════════════════════════ 6. SKILL STATS + BOOTSTRAP ════════════════════════
def probabilistic_sharpe_ratio(returns, sr_benchmark=0.0):
    r = np.asarray(returns, float); n = len(r)
    if n < 8 or r.std(ddof=1) == 0:
        return 0.5, 0.0
    sr = r.mean() / r.std(ddof=1)
    g3 = scistats.skew(r); g4 = scistats.kurtosis(r, fisher=True) + 3.0
    denom = np.sqrt(1 - g3 * sr + ((g4 - 1) / 4) * sr ** 2)
    if denom <= 0:
        return 0.5, float(sr)
    return float(scistats.norm.cdf((sr - sr_benchmark) * np.sqrt(n - 1) / denom)), float(sr)


def deflated_sharpe(returns, n_trials=2):
    r = np.asarray(returns, float); n = len(r)
    if n < 8 or r.std(ddof=1) == 0 or n_trials < 1:
        return 0.5
    sr_var = 1.0 / n; emc = 0.5772156649
    e_max = np.sqrt(sr_var) * ((1 - emc) * scistats.norm.ppf(1 - 1.0 / n_trials)
                               + emc * scistats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    return float(probabilistic_sharpe_ratio(r, sr_benchmark=e_max)[0])


def bootstrap_ci(rets, fn, n_boot=2000, ci=0.95, block=5, seed=11):
    rets = np.asarray(rets, float); n = len(rets)
    if n < 10:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed); stats = []
    for _ in range(n_boot):
        out = []
        while len(out) < n:
            start = rng.integers(0, n); ln = rng.geometric(1.0 / block)
            out.extend(rets[start:start + ln])
        stats.append(fn(np.array(out[:n])))
    return (float(np.percentile(stats, (1 - ci) / 2 * 100)),
            float(np.percentile(stats, (1 + ci) / 2 * 100)))


# ════════════════════════ 7. TRIPLE-BARRIER + WF + SCAN ════════════════════════
def _barrier_outcome(px, i, sig_i, side, pt, sl, vbar, cost):
    p0 = px.iloc[i]; up = p0 * (1 + side * pt * sig_i); dn = p0 * (1 + side * -sl * sig_i)
    path = px.iloc[i + 1: i + 1 + vbar]
    for p in path:
        if side == 1:
            if p >= up: return pt * sig_i - cost
            if p <= dn: return -sl * sig_i - cost
        else:
            if p <= up: return pt * sig_i - cost
            if p >= dn: return -sl * sig_i - cost
    return side * (path.iloc[-1] / p0 - 1) - cost


def triple_barrier_nonoverlap(close, sigma, side=1, pt=2.0, sl=1.5, vbar=DEFAULT_HORIZON, cost_bps=10.0):
    px = close.reindex(sigma.index).dropna(); sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4; rets = []; dates = []; outcomes = []; i = 0
    while i < len(px) - vbar:
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            i += 1; continue
        r = _barrier_outcome(px, i, s, side, pt, sl, vbar, cost)
        rets.append(r); dates.append(px.index[i])
        outcomes.append("WIN" if r > 0 else "LOSS")
        i += vbar
    rets = np.array(rets)
    if len(rets) == 0:
        return {"expectancy": 0.0, "psr": 0.5, "dsr": 0.5, "win_rate": 0.0, "n": 0,
                "rets": rets, "dates": [], "outcomes": [], "equity": pd.Series(dtype=float),
                "exp_ci": (np.nan, np.nan), "turnover": 0.0, "breakeven_bps": 0.0,
                "date_range": "—", "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
                "max_consec_loss": 0, "avg_hold": vbar}
    psr, _ = probabilistic_sharpe_ratio(rets, 0.0)
    dsr = deflated_sharpe(rets, n_trials=2)
    exp_ci = bootstrap_ci(rets, lambda x: x.mean())
    equity = pd.Series(np.cumprod(1 + rets), index=pd.to_datetime(dates))
    turnover = TRADING_DAYS / vbar
    breakeven_bps = max(0.0, (rets.mean() + cost) * 1e4)
    date_range = f"{pd.to_datetime(dates[0]).strftime('%Y-%m-%d')} → {pd.to_datetime(dates[-1]).strftime('%Y-%m-%d')}"
    wins = rets[rets > 0]; losses = rets[rets < 0]
    avg_win = float(wins.mean() * 100) if len(wins) else 0.0
    avg_loss = float(losses.mean() * 100) if len(losses) else 0.0
    profit_factor = abs(wins.sum() / losses.sum()) if len(losses) and losses.sum() != 0 else 0.0
    # max consecutive losses
    max_cl = cl = 0
    for o in outcomes:
        if o == "LOSS": cl += 1; max_cl = max(max_cl, cl)
        else: cl = 0
    return {"expectancy": float(rets.mean()), "psr": psr, "dsr": dsr,
            "win_rate": float((rets > 0).mean() * 100), "n": int(len(rets)), "rets": rets,
            "dates": dates, "outcomes": outcomes, "equity": equity, "exp_ci": exp_ci,
            "turnover": float(turnover), "breakeven_bps": float(breakeven_bps),
            "date_range": date_range, "avg_win": avg_win, "avg_loss": avg_loss,
            "profit_factor": float(profit_factor), "max_consec_loss": max_cl, "avg_hold": vbar}


def purged_walk_forward(close, sigma, side, pt, sl, vbar=DEFAULT_HORIZON, cost_bps=10.0, folds=5):
    px = close.reindex(sigma.index).dropna(); sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4; n = len(px)
    if n < (folds + 1) * (vbar + 20):
        return {"oos_expectancy": np.nan, "oos_win_rate": np.nan, "oos_dsr": np.nan, "oos_n": 0}
    fold = n // (folds + 1); oos = []
    for f in range(1, folds + 1):
        ts = f * fold; te = min((f + 1) * fold, n - vbar); i = ts + vbar
        while i < te:
            s = float(sig.iloc[i])
            if np.isfinite(s) and s > 0:
                oos.append(_barrier_outcome(px, i, s, side, pt, sl, vbar, cost)); i += vbar
            else:
                i += 1
    oos = np.array(oos)
    if len(oos) == 0:
        return {"oos_expectancy": np.nan, "oos_win_rate": np.nan, "oos_dsr": np.nan, "oos_n": 0}
    return {"oos_expectancy": float(oos.mean()), "oos_win_rate": float((oos > 0).mean() * 100),
            "oos_dsr": deflated_sharpe(oos, n_trials=2), "oos_n": int(len(oos))}


def horizon_scan(close, pt, sl, cost_bps, horizons=HORIZONS):
    rows = []
    for h in horizons:
        sig, _, _ = garch_vol(close, h)
        for side, lbl in [(1, "LONG"), (-1, "SHORT")]:
            wf = purged_walk_forward(close, sig, side, pt, sl, h, cost_bps)
            rows.append({"horizon": h, "side": lbl, "oos_dsr": wf["oos_dsr"],
                         "oos_exp": wf["oos_expectancy"], "oos_n": wf["oos_n"]})
    valid = [r for r in rows if np.isfinite(r["oos_dsr"] or np.nan)]
    best = max(valid, key=lambda r: r["oos_dsr"]) if valid else None
    return rows, best


def execution_matrix(price, sig_d, side, pt=2.0, sl=1.5, cost_bps=10.0):
    cost = price * cost_bps / 1e4
    if side >= 0:
        entry, tgt, stp = price, price * (1 + pt * sig_d) - cost, price * (1 - sl * sig_d) - cost
    else:
        entry, tgt, stp = price, price * (1 - pt * sig_d) + cost, price * (1 + sl * sig_d) + cost
    rr = abs(tgt - entry) / abs(entry - stp) if (entry - stp) != 0 else 0
    return {"entry": round(entry, 2), "target": round(tgt, 2), "stop": round(stp, 2), "rr": round(rr, 2)}


def position_sizing(setup, sigma_annual, target_vol=0.15, kelly_fraction=0.5, max_w=1.0):
    rets = setup.get("rets", np.array([]))
    vt = min(max_w, target_vol / sigma_annual) if sigma_annual > 0 else 0.0
    kelly = 0.0
    if len(rets) > 8 and rets.var() > 0:
        kelly = float(rets.mean() / rets.var())
    kelly = max(0.0, min(max_w, kelly * kelly_fraction)); conf = setup.get("dsr", 0.5)
    return {"vol_target_w": round(vt, 3), "kelly_half": round(kelly, 3),
            "confidence": round(conf, 3), "weight": round(min(max_w, vt * kelly * conf), 3)}


def disposition(bt_long, bt_short):
    dom = bt_long if bt_long["dsr"] >= bt_short["dsr"] else bt_short
    side = 1 if dom is bt_long else -1
    verdict = ("LONG" if side == 1 else "SHORT") if (dom["dsr"] >= 0.60 and dom["expectancy"] > 0) else "NO EDGE"
    return verdict, (side if verdict != "NO EDGE" else 0), dom


# ════════════════════════ TRANSACTION COST MODEL ════════════════════════
def transaction_cost_model(close, turnover_per_year, cost_bps=15.0, market_impact_bps=5.0,
                           slippage_bps=3.0, holding_days=5):
    r = _rets(close)
    ann_vol = float(r.std() * np.sqrt(TRADING_DAYS))
    gross_edge = float(r.mean() * TRADING_DAYS * 100)
    commission_bps = cost_bps
    impact_bps = market_impact_bps
    slip_bps = slippage_bps
    total_rt_bps = commission_bps + impact_bps + slip_bps
    annual_cost_bps = total_rt_bps * turnover_per_year
    annual_cost_pct = annual_cost_bps / 100.0
    be_gross_alpha_pct = annual_cost_pct
    net_alpha_pct = gross_edge - annual_cost_pct
    cost_to_vol_ratio = annual_cost_pct / max(ann_vol * 100, 1e-6)
    sebi_stt = 0.1; sebi_stamp = 0.015; exchange_charges = 0.00335
    regulatory_total = sebi_stt + sebi_stamp + exchange_charges
    return {
        "commission_bps": round(commission_bps, 1), "impact_bps": round(impact_bps, 1),
        "slippage_bps": round(slip_bps, 1), "regulatory_bps": round(regulatory_total, 3),
        "total_rt_bps": round(total_rt_bps, 1), "annual_cost_bps": round(annual_cost_bps, 1),
        "annual_cost_pct": round(annual_cost_pct, 2), "net_alpha_pct": round(net_alpha_pct, 2),
        "cost_to_vol_ratio": round(cost_to_vol_ratio, 3),
        "be_gross_alpha_pct": round(be_gross_alpha_pct, 2),
        "turnover": round(turnover_per_year, 1), "viable": net_alpha_pct > 0,
    }


# ════════════════════════ INFORMATION COEFFICIENT ════════════════════════
def information_coefficient(close, sigma, horizons=None, n_lags=10):
    if horizons is None:
        horizons = [5]
    r = _rets(close)
    signal = -sigma
    results = {}
    for h in horizons:
        fwd_ret = r.rolling(h).sum().shift(-h)
        df = pd.DataFrame({"signal": signal, "fwd": fwd_ret}).dropna()
        if len(df) < 30:
            results[h] = {"ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan, "n": 0}
            continue
        ic_series = df["signal"].rolling(60).apply(
            lambda w: scistats.spearmanr(w, df["fwd"].loc[w.index])[0], raw=False
        )
        ic_vals = ic_series.dropna().values
        ic_mean = float(np.nanmean(ic_vals))
        ic_std = float(np.nanstd(ic_vals))
        icir = ic_mean / ic_std if ic_std > 0 else 0.0
        results[h] = {"ic_mean": round(ic_mean, 3), "ic_std": round(ic_std, 3),
                      "icir": round(icir, 3), "n": len(ic_vals), "ic_series": ic_series}
    decay = []
    for lag in range(1, n_lags + 1):
        fwd_ret = r.shift(-lag)
        df = pd.DataFrame({"signal": signal, "fwd": fwd_ret}).dropna()
        if len(df) < 30:
            decay.append({"lag": lag, "ic": np.nan}); continue
        ic_at_lag = float(scistats.spearmanr(df["signal"], df["fwd"])[0])
        decay.append({"lag": lag, "ic": round(ic_at_lag, 4)})
    return {"by_horizon": results, "decay": decay}


# ════════════════════════ FACTOR DECAY ════════════════════════
def factor_decay_analysis(close, proxies, max_lag=20):
    r = _rets(close)
    decay_rows = []
    for factor_name, proxy_series in proxies.items():
        fr = _rets(proxy_series)
        common = pd.concat([r, fr], axis=1, join="inner").dropna()
        common.columns = ["stock", "factor"]
        if len(common) < 60:
            continue
        betas_at_lag = []
        for lag in range(0, max_lag + 1):
            shifted_factor = common["factor"].shift(lag)
            df = pd.DataFrame({"s": common["stock"], "f": shifted_factor}).dropna()
            if len(df) < 30:
                betas_at_lag.append(np.nan); continue
            if df["f"].var() > 0:
                b = float(np.cov(df["s"], df["f"])[0, 1] / df["f"].var())
            else:
                b = np.nan
            betas_at_lag.append(b)
        if betas_at_lag and np.isfinite(betas_at_lag[0]) and abs(betas_at_lag[0]) > 1e-6:
            norm_decay = [b / abs(betas_at_lag[0]) if np.isfinite(b) else np.nan for b in betas_at_lag]
        else:
            norm_decay = betas_at_lag
        decay_rows.append({"factor": factor_name, "lags": list(range(0, max_lag + 1)),
                           "betas": betas_at_lag, "normalized": norm_decay,
                           "half_life": _half_life(norm_decay)})
    return decay_rows


def _half_life(norm_series):
    for i, v in enumerate(norm_series):
        if np.isfinite(v) and abs(v) <= 0.5:
            return i
    return len(norm_series)


# ════════════════════════ HMM ════════════════════════
def hmm_regime_detection(close, n_states=2, n_iter=200):
    r = _rets(close).values
    n = len(r)
    if n < 100:
        return {"error": "Need 100+ return observations for HMM."}
    half = len(r) // 2
    mu = np.array([r[:half].mean(), r[half:].mean()])
    sigma_arr = np.array([max(r[:half].std(), 1e-6), max(r[half:].std(), 1e-6)])
    if sigma_arr[0] > sigma_arr[1]:
        mu = mu[::-1]; sigma_arr = sigma_arr[::-1]
    A = np.array([[0.95, 0.05], [0.05, 0.95]])
    pi = np.array([0.5, 0.5])

    def gauss(x, m, s):
        return np.exp(-0.5 * ((x - m) / s) ** 2) / (s * np.sqrt(2 * np.pi)) + 1e-300

    for _ in range(n_iter):
        alpha_fw = np.zeros((n, n_states))
        alpha_fw[0] = pi * np.array([gauss(r[0], mu[k], sigma_arr[k]) for k in range(n_states)])
        scale = np.zeros(n); scale[0] = alpha_fw[0].sum(); alpha_fw[0] /= scale[0]
        for t in range(1, n):
            b = np.array([gauss(r[t], mu[k], sigma_arr[k]) for k in range(n_states)])
            alpha_fw[t] = (alpha_fw[t - 1] @ A) * b
            scale[t] = alpha_fw[t].sum()
            if scale[t] > 0: alpha_fw[t] /= scale[t]
        beta_bw = np.ones((n, n_states))
        for t in range(n - 2, -1, -1):
            b = np.array([gauss(r[t + 1], mu[k], sigma_arr[k]) for k in range(n_states)])
            beta_bw[t] = (A @ (b * beta_bw[t + 1]))
            if scale[t + 1] > 0: beta_bw[t] /= scale[t + 1]
        gamma = alpha_fw * beta_bw
        gamma_sum = gamma.sum(axis=1, keepdims=True)
        gamma_sum = np.where(gamma_sum > 0, gamma_sum, 1.0)
        gamma /= gamma_sum
        xi = np.zeros((n - 1, n_states, n_states))
        for t in range(n - 1):
            b_next = np.array([gauss(r[t + 1], mu[k], sigma_arr[k]) for k in range(n_states)])
            xi[t] = alpha_fw[t][:, None] * A * b_next[None, :] * beta_bw[t + 1][None, :]
            xi_s = xi[t].sum()
            if xi_s > 0: xi[t] /= xi_s
        A = xi.sum(axis=0); A_row = A.sum(axis=1, keepdims=True)
        A = np.where(A_row > 0, A / A_row, 1.0 / n_states)
        pi = gamma[0]
        for k in range(n_states):
            w = gamma[:, k]; w_sum = w.sum()
            if w_sum > 0:
                mu[k] = (w * r).sum() / w_sum
                sigma_arr[k] = max(np.sqrt((w * (r - mu[k]) ** 2).sum() / w_sum), 1e-6)
    viterbi = np.zeros(n, dtype=int)
    delta = np.log(pi + 1e-300) + np.array([np.log(gauss(r[0], mu[k], sigma_arr[k])) for k in range(n_states)])
    psi = np.zeros((n, n_states), dtype=int)
    log_A = np.log(A + 1e-300)
    for t in range(1, n):
        log_b = np.array([np.log(gauss(r[t], mu[k], sigma_arr[k])) for k in range(n_states)])
        for k in range(n_states):
            trans = delta + log_A[:, k]
            psi[t, k] = int(np.argmax(trans))
            delta[k] = trans[psi[t, k]] + log_b[k]
    viterbi[-1] = int(np.argmax(delta))
    for t in range(n - 2, -1, -1):
        viterbi[t] = psi[t + 1, viterbi[t + 1]]
    if sigma_arr[0] > sigma_arr[1]:
        viterbi = 1 - viterbi; mu = mu[::-1]; sigma_arr = sigma_arr[::-1]; A = A[::-1, :][:, ::-1]
    current_state = int(viterbi[-1])
    streak = 1
    for i in range(n - 2, -1, -1):
        if viterbi[i] == current_state: streak += 1
        else: break
    state_labels = ["LOW-VOL / BULL", "HIGH-VOL / BEAR"]
    state_rets = [float(r[viterbi == k].mean() * TRADING_DAYS * 100) for k in range(n_states)]
    state_vols = [float(sigma_arr[k] * np.sqrt(TRADING_DAYS) * 100) for k in range(n_states)]
    return {"states": viterbi, "returns": r, "state_labels": state_labels,
            "transition_matrix": A.tolist(), "state_means_ann_pct": state_rets,
            "state_vols_ann_pct": state_vols, "current_state": current_state,
            "current_state_label": state_labels[current_state], "days_in_state": streak,
            "prob_stay": round(float(A[current_state, current_state]), 3), "n": n}


# ════════════════════════ META-LABELING ════════════════════════
def meta_labeling(close, sigma, side=1, pt=2.0, sl=1.5, vbar=DEFAULT_HORIZON, cost_bps=10.0, n_features=4):
    px = close.reindex(sigma.index).dropna()
    sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4
    r = _rets(px)
    labels = []; features = []; dates = []; i = 0
    while i < len(px) - vbar - 60:
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            i += 1; continue
        outcome = _barrier_outcome(px, i, s, side, pt, sl, vbar, cost)
        binary = 1 if outcome > 0 else 0
        win = r.iloc[max(0, i - 60): i]
        if len(win) < 20:
            i += vbar; continue
        vol_rank = float((win.std() < sig.iloc[max(0, i - 60): i].mean()).mean())
        momentum = float(win.tail(20).mean() / (win.std() + 1e-8))
        autocorr = float(win.autocorr(1)) if len(win) > 5 else 0.0
        vol_of_vol = float(sig.iloc[max(0, i - 60): i].std() / (sig.iloc[i] + 1e-8))
        features.append([vol_rank, momentum, autocorr, vol_of_vol])
        labels.append(binary); dates.append(px.index[i]); i += vbar
    if len(labels) < 20:
        return {"error": "Insufficient trades for meta-labeling (need 20+).",
                "meta_accuracy": np.nan, "meta_precision": np.nan,
                "meta_recall": np.nan, "f1": np.nan, "n_trades": len(labels)}
    X = np.array(features); y = np.array(labels); n = len(y)
    mu_f = X.mean(axis=0); std_f = np.where(X.std(axis=0) > 0, X.std(axis=0), 1.0)
    X_sc = (X - mu_f) / std_f

    def sigmoid(z): return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
    w = np.zeros(X_sc.shape[1]); b = 0.0; lr = 0.05
    for _ in range(300):
        p = sigmoid(X_sc @ w + b)
        dw = X_sc.T @ (p - y) / n; db = float((p - y).mean())
        w -= lr * dw; b -= lr * db
    proba = sigmoid(X_sc @ w + b)
    preds = (proba >= 0.5).astype(int)
    acc = float((preds == y).mean())
    tp = float(((preds == 1) & (y == 1)).sum()); fp = float(((preds == 1) & (y == 0)).sum())
    fn = float(((preds == 0) & (y == 1)).sum())
    precision = tp / (tp + fp + 1e-8); recall = tp / (tp + fn + 1e-8)
    f1 = 2 * precision * recall / (precision + recall + 1e-8)
    importance = {name: round(float(abs(w[i]) * std_f[i]), 4)
                  for i, name in enumerate(["vol_rank", "momentum", "autocorr", "vol_of_vol"])}
    conf_series = pd.Series(proba, index=pd.to_datetime(dates))
    return {"meta_accuracy": round(acc, 3), "meta_precision": round(precision, 3),
            "meta_recall": round(recall, 3), "f1": round(f1, 3), "n_trades": n,
            "feature_importance": importance, "confidence_series": conf_series,
            "weights": w.tolist(), "bias": float(b)}


# ════════════════════════ GLOSSARY ════════════════════════
GLOSSARY = {
    "Deflated Sharpe": "Probability the Sharpe is REAL after adjusting for how many strategies you tested. ≥0.60 = credible edge; <0.60 = likely noise.",
    "Prob Sharpe": "Probability the Sharpe is above zero given skew, fat tails, sample size. Closer to 1.0 = more reliable.",
    "OOS Deflated Sharpe": "Deflated Sharpe on data the model never saw (purged walk-forward). Trust it over in-sample.",
    "Expectancy/trade": "Average net profit per trade after costs (%). Must be positive AND CI should not cross zero.",
    "Expectancy CI": "Bootstrap 95% band for expectancy. If it includes 0, the edge is statistically indistinguishable from random.",
    "Win Rate": "% of trades closing positive. Secondary to expectancy — high payoff can beat high win rate.",
    "Turnover": "Round-trip trades per year. Higher turnover needs a bigger gross edge to survive costs.",
    "Break-even bps": "Cost per trade the gross edge can absorb. If your real cost exceeds this, the edge is fictional.",
    "Beta": "Sensitivity to NIFTY. 1.0 = moves with market, >1 amplifies.",
    "CAPM Alpha": "Annual return above what beta-to-market explains.",
    "Factor Alpha": "Annual return left after stripping market/size/momentum exposure.",
    "R²": "Fraction of moves explained by factors. High = factor-driven; low = stock-specific.",
    "VaR 95%": "Worst loss not exceeded 95% of the time over the horizon.",
    "Expected Shortfall": "Average loss in the worst 5% of cases (tail beyond VaR).",
    "Monte Carlo VaR": "VaR from simulating fat-tailed (Student-t) paths.",
    "GARCH σ": "Volatility forecast that reacts to recent shocks. Desk standard for vol.",
    "Vol Regime": "Current vol vs its own 1yr history: COMPRESSED / NORMAL / STRESSED.",
    "Frac-diff d": "Minimum differencing for stationarity while keeping memory (AFML).",
    "Suggested Weight": "Vol-target × half-Kelly × statistical confidence. Upper bound, not advice.",
    "Max DD": "Largest peak-to-trough equity loss.",
    "Sharpe": "Annual return per unit of total volatility.",
    "Sortino": "Like Sharpe but penalizes only downside volatility.",
    "IC (Information Coefficient)": "Spearman rank-correlation between signal and future return. IC>0.05 = useful; ICIR>0.5 = consistent.",
    "ICIR": "IC mean / IC std. Measures signal consistency over time. >0.5 indicates a reliable signal.",
    "IC Decay": "How fast signal-to-return correlation decays as forecast horizon lengthens. Faster decay = shorter optimal trade horizon.",
    "Factor Decay Half-Life": "Lag (days) at which factor beta drops to 50% of its spot value. Informs optimal hold period.",
    "HMM State": "Hidden Markov Model inferred market regime: LOW-VOL/BULL vs HIGH-VOL/BEAR. Size up in bull state, reduce in bear.",
    "P(stay)": "Probability of remaining in current HMM regime tomorrow. High P(stay) = regime momentum; trade with it.",
    "Meta-Label Accuracy": "How well the secondary logistic classifier predicts whether the primary signal's trade wins. >60% is useful.",
    "Meta-Label F1": "Harmonic mean of precision & recall for meta-label wins. Better than accuracy on imbalanced trade sets.",
    "Annual Cost Drag": "Total round-trip cost × turnover expressed as % per year. Subtract from gross alpha to get net alpha.",
    "Cost-to-Vol Ratio": "Annual cost drag / annual vol. >0.20 means costs consume >20% of one standard deviation of returns.",
    "Profit Factor": "Gross winning PnL / Gross losing PnL. >1.5 = good; >2.0 = excellent.",
    "Setup Score": "AI-generated score 0-100 rating overall trade quality across all metrics.",
}


# ════════════════════════ GEMINI — FULL AI ANALYSIS ════════════════════════
def gemini_full_analysis(sym, fund, fac, vol, stat, ex, dom, wf, size, factor,
                         mc, hist, stress, scan_best, verdict, tc, ic_res, hmm, meta, api_key):
    """
    Calls Gemini to:
    1. Score the setup 0-100
    2. Give BUY / SELL / AVOID
    3. Summarize every tab in plain English
    4. Give a full quant note
    Returns dict with score, action, tab_summaries, full_note
    """
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are a senior buy-side quant analyst at a top hedge fund. "
            "You receive raw metrics from a quantitative backtest terminal for Indian NSE stocks. "
            "Your job: score the setup, give a clear action, explain every section in plain English, "
            "and write a professional research note. Be direct, honest, and data-driven. "
            "Never give vague answers. If the data is weak, say so clearly. "
            "Educational only — not SEBI registered investment advice."
        ),
    )

    def f(v, dp=2):
        try: return f"{float(v):.{dp}f}"
        except: return "n/a"

    betas_str = ", ".join(f"{k} {f(v)} (t={f(factor.get('tstats',{}).get(k))})"
                          for k, v in factor.get("betas", {}).items()) or "n/a"
    stress_txt = "; ".join(f"{k}: {f(v['pct'],1)}%" for k, v in stress.items())
    sb = scan_best or {}
    ic_h = ic_res.get("by_horizon", {})
    ic_5 = ic_h.get(5, {})
    hmm_s = (f"state={hmm.get('current_state_label','?')}, P(stay)={f(hmm.get('prob_stay'))}, "
             f"days_in={hmm.get('days_in_state','?')}") if "error" not in hmm else "insufficient data"
    meta_s = f"accuracy={f(meta.get('meta_accuracy'))}, F1={f(meta.get('f1'))}, n={meta.get('n_trades','?')}"
    tc_s = f"total_rt={tc.get('total_rt_bps','?')}bps, annual_drag={tc.get('annual_cost_pct','?')}%, net_alpha={tc.get('net_alpha_pct','?')}%"

    prompt = f"""
You are analyzing {sym} ({fund['name']}, {fund['sector']}) on NSE.

=== RAW METRICS ===
VERDICT: {verdict} | DSR: {f(dom['dsr'])} | OOS-DSR: {f(wf['oos_dsr'])}
BACKTEST [{dom.get('date_range','—')}]: expectancy {f(dom['expectancy']*100)}%/trade [CI {f(dom['exp_ci'][0]*100)}..{f(dom['exp_ci'][1]*100)}%], win {f(dom['win_rate'],0)}%, n={dom['n']}, profit_factor={f(dom.get('profit_factor',0))}, avg_win={f(dom.get('avg_win',0))}%, avg_loss={f(dom.get('avg_loss',0))}%, max_consec_loss={dom.get('max_consec_loss',0)}
OOS: exp {f((wf['oos_expectancy'] or 0)*100)}%, OOS-DSR {f(wf['oos_dsr'])}, n={wf['oos_n']}
COSTS: {tc_s}
IC(5d): mean={f(ic_5.get('ic_mean'))}, ICIR={f(ic_5.get('icir'))}
HMM: {hmm_s}
META-LABEL: {meta_s}
FACTOR: {betas_str} | alpha {f(factor.get('alpha_ann'),1)}%/yr | R²={f(factor.get('r2'))}
RISK: Hist VaR {f(hist['var'])}%/ES {f(hist['es'])}% | MC VaR {f(mc['var'])}% | MaxDD {f(fac['max_dd'],1)}% | Beta {f(fac['beta'])} | Sharpe {f(fac['sharpe'])} | Sortino {f(fac['sortino'])}
STRESS: {stress_txt}
VOL: {vol['regime']} | sigma_ann {f(vol['sigma_annual']*100,1)}% | frac-diff d={f(stat['d'])}
BEST HORIZON: {sb.get('horizon','?')}d {sb.get('side','?')} OOS-DSR={f(sb.get('oos_dsr'))}

=== YOUR TASKS ===

TASK 1 — SCORE (integer 0-100):
Rate this setup holistically. Consider DSR, OOS-DSR, IC, HMM regime, meta F1, cost viability, profit factor, drawdown. 
A score above 60 needs DSR≥0.60 AND OOS-DSR≥0.60. Score below 40 if no real edge.
Output ONLY: SCORE: [number]

TASK 2 — ACTION:
Based on all metrics, give ONE word: BUY or SELL or AVOID
If verdict is NO EDGE or OOS-DSR < 0.60, output AVOID.
Output ONLY: ACTION: [word]

TASK 3 — TAB SUMMARIES (plain English, 2-3 sentences each):
Explain what each section means for a trader who doesn't know quant:
OVERVIEW_SUMMARY: [explain the key signals, verdict, weight suggestion]
CHARTS_SUMMARY: [explain what the price chart, equity curve and drawdown tell us]
FACTOR_SUMMARY: [explain beta, alpha, R² in plain words]
RISK_SUMMARY: [explain VaR, stress scenarios in plain words]
BACKTEST_SUMMARY: [explain DSR, OOS, expectancy, win rate, profit factor in plain words]
COST_SUMMARY: [explain if costs kill the edge or not]
IC_SUMMARY: [explain IC/ICIR and what it means for timing]
HMM_SUMMARY: [explain current regime and what trader should do]
META_SUMMARY: [explain meta-label F1 and whether to filter trades]
DECAY_SUMMARY: [explain factor decay and optimal hold period]
STAT_SUMMARY: [explain frac-diff d and stationarity in plain words]

TASK 4 — RESEARCH NOTE (<400 words, use **bold headers**):
**Verdict & Edge Quality**
**Signal Quality (IC/ICIR)**
**Market Regime (HMM)**
**Meta-Label Confidence**
**Cost Reality**
**Factor Exposure & Risk**
**Sizing Recommendation**
**Final Call**
"""

    response = model.generate_content(prompt).text

    # Parse the response
    score = 50  # default
    action = "AVOID"
    tab_summaries = {}
    full_note = response

    lines = response.split('\n')
    note_start = 0
    for i, line in enumerate(lines):
        line_s = line.strip()
        if line_s.startswith("SCORE:"):
            try:
                score = int(''.join(filter(str.isdigit, line_s.split("SCORE:")[1].strip().split()[0])))
                score = max(0, min(100, score))
            except: pass
        elif line_s.startswith("ACTION:"):
            raw = line_s.split("ACTION:")[1].strip().upper()
            if "BUY" in raw: action = "BUY"
            elif "SELL" in raw: action = "SELL"
            else: action = "AVOID"
        for key in ["OVERVIEW_SUMMARY", "CHARTS_SUMMARY", "FACTOR_SUMMARY", "RISK_SUMMARY",
                    "BACKTEST_SUMMARY", "COST_SUMMARY", "IC_SUMMARY", "HMM_SUMMARY",
                    "META_SUMMARY", "DECAY_SUMMARY", "STAT_SUMMARY"]:
            if line_s.startswith(key + ":"):
                tab_summaries[key] = line_s.split(key + ":")[1].strip()
        if line_s.startswith("**Verdict"):
            note_start = i
            break

    if note_start > 0:
        full_note = '\n'.join(lines[note_start:])

    return {"score": score, "action": action, "tab_summaries": tab_summaries, "full_note": full_note}


# ════════════════════════ HELPERS ════════════════════════
def _metric_row(cells):
    import streamlit as st
    cols = st.columns(len(cells))
    for col, (label, value) in zip(cols, cells):
        col.metric(label, value, help=GLOSSARY.get(label.strip()))


def _sec(title):
    return (f"<div style='font-family:{MONO};font-size:11px;font-weight:600;color:{ACCENT};"
            f"letter-spacing:1.5px;margin:14px 0 6px;border-bottom:1px solid {BORDER};"
            f"padding-bottom:5px;'>{title}</div>")


def _fmt(v, suffix="", dp=2, dash="—"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return f"{v:,.{dp}f}{suffix}"


def _plot_layout(fig, title, height=320):
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=title, font=dict(size=13, color=ACCENT)),
        paper_bgcolor=PANEL, plot_bgcolor=PANEL, height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        showlegend=False,
    )
    return fig


def _ai_box(text, color=BLUE):
    return (f"<div style='background:{PANEL2};border-left:3px solid {color};"
            f"border-radius:6px;padding:10px 14px;margin:8px 0;"
            f"font-family:{MONO};font-size:12px;color:{IVORY};line-height:1.6;'>"
            f"🤖 <b>AI:</b> {text}</div>")


def _score_gauge_html(score):
    """Render a colorful SVG circular gauge for the score."""
    if score >= 81: color = GREEN; label = "STRONG"
    elif score >= 61: color = BLUE; label = "GOOD"
    elif score >= 41: color = ORANGE; label = "WEAK"
    else: color = RED; label = "POOR"

    # SVG arc math
    radius = 40
    circumference = 2 * 3.14159 * radius
    filled = circumference * score / 100
    gap = circumference - filled

    return f"""
    <div style='display:flex;flex-direction:column;align-items:center;justify-content:center;'>
      <svg width="110" height="110" viewBox="0 0 110 110">
        <defs>
          <filter id="glow">
            <feGaussianBlur stdDeviation="2.5" result="coloredBlur"/>
            <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>
        <!-- Background ring -->
        <circle cx="55" cy="55" r="{radius}" fill="none" stroke="{BORDER}" stroke-width="10"/>
        <!-- Score arc -->
        <circle cx="55" cy="55" r="{radius}" fill="none" stroke="{color}" stroke-width="10"
          stroke-dasharray="{filled:.1f} {gap:.1f}"
          stroke-dashoffset="{circumference * 0.25:.1f}"
          stroke-linecap="round" filter="url(#glow)"
          style="transition: stroke-dasharray 0.5s ease;"/>
        <!-- Score text -->
        <text x="55" y="50" text-anchor="middle" font-family="monospace"
          font-size="20" font-weight="bold" fill="{color}">{score}</text>
        <text x="55" y="66" text-anchor="middle" font-family="monospace"
          font-size="9" fill="{MUTE}">/100</text>
        <text x="55" y="80" text-anchor="middle" font-family="monospace"
          font-size="8" font-weight="600" fill="{color}">{label}</text>
      </svg>
    </div>"""


def _cap(text):
    import streamlit as st
    st.caption(text)


# ════════════════════════ CHARTS ════════════════════════
def chart_price_setup(df, ex, verdict, sigma, trade_dates):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    d = df.tail(180)
    sig_d = sigma.reindex(d.index).ffill()
    close_d = d["Close"]
    upper_band = close_d * (1 + 2 * sig_d)
    lower_band = close_d * (1 - 2 * sig_d)
    d_idx_set = set(d.index.normalize())
    entry_markers = [dt for dt in trade_dates
                     if hasattr(dt, 'normalize') and dt.normalize() in d_idx_set]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED, name="Price", showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=list(d.index) + list(d.index[::-1]),
        y=list(upper_band) + list(lower_band[::-1]),
        fill="toself", fillcolor="rgba(76,141,214,0.08)",
        line=dict(color="rgba(0,0,0,0)"), name="GARCH ±2σ", showlegend=True,
    ), row=1, col=1)
    for y, c, lbl in [(ex["entry"], BLUE, "Entry"), (ex["target"], GREEN, "Target"), (ex["stop"], RED, "Stop")]:
        fig.add_hline(y=y, line_color=c, line_dash="dot", line_width=1,
                      annotation_text=f"{lbl} {y:,.0f}", annotation_font_color=c,
                      annotation_position="right", row=1, col=1)
    fig.add_hrect(y0=ex["stop"], y1=ex["entry"], fillcolor="rgba(232,85,78,0.06)", line_width=0, row=1, col=1)
    fig.add_hrect(y0=ex["entry"], y1=ex["target"], fillcolor="rgba(31,185,122,0.06)", line_width=0, row=1, col=1)
    if entry_markers:
        marker_prices = [df.loc[dt, "Close"] if dt in df.index else np.nan for dt in entry_markers]
        fig.add_trace(go.Scatter(
            x=entry_markers, y=marker_prices, mode="markers",
            marker=dict(color=ACCENT, size=7, symbol="triangle-up"),
            name="BT Entries", showlegend=True,
        ), row=1, col=1)
    colors = [GREEN if d["Close"].iloc[i] >= d["Open"].iloc[i] else RED for i in range(len(d))]
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=colors, opacity=0.6,
                         name="Volume", showlegend=False), row=2, col=1)
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=f"PRICE · SETUP LEVELS · VOL BAND · {verdict}", font=dict(size=13, color=ACCENT)),
        paper_bgcolor=PANEL, plot_bgcolor=PANEL, height=520,
        margin=dict(l=10, r=10, t=40, b=10),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis2=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        yaxis2=dict(gridcolor=BORDER, title="Volume"),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02, x=0, font=dict(size=10)),
    )
    return fig


def chart_equity(dom):
    import plotly.graph_objects as go
    eq = dom.get("equity", pd.Series(dtype=float))
    fig = go.Figure()
    if len(eq):
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values,
                                 line=dict(color=ACCENT, width=2), fill="tozeroy",
                                 fillcolor="rgba(201,162,39,0.08)"))
        fig.add_hline(y=1.0, line_color=MUTE, line_dash="dash", line_width=1)
    return _plot_layout(fig, f"BACKTEST EQUITY CURVE · {dom.get('date_range','')}")


def chart_drawdown(dom):
    import plotly.graph_objects as go
    eq = dom.get("equity", pd.Series(dtype=float))
    fig = go.Figure()
    if len(eq):
        dd = (eq / eq.cummax() - 1) * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values,
                                 line=dict(color=RED, width=1.5), fill="tozeroy",
                                 fillcolor="rgba(232,85,78,0.15)"))
    return _plot_layout(fig, "STRATEGY DRAWDOWN (%)")


def chart_trade_log(dom):
    """Detailed trade-by-trade bar chart (Tardezilla style)."""
    import plotly.graph_objects as go
    rets = dom.get("rets", np.array([]))
    dates = dom.get("dates", [])
    outcomes = dom.get("outcomes", [])
    if len(rets) == 0:
        return _plot_layout(go.Figure(), "TRADE LOG")
    colors = [GREEN if o == "WIN" else RED for o in outcomes]
    text = [f"{'▲' if o=='WIN' else '▼'} {r*100:+.2f}%" for r, o in zip(rets, outcomes)]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[pd.Timestamp(d).strftime('%Y-%m-%d') for d in dates],
        y=rets * 100,
        marker_color=colors,
        text=text,
        textposition="outside",
        textfont=dict(size=9, color=IVORY),
        opacity=0.9,
        name="Trade P&L %",
    ))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    fig.add_hline(y=float(np.mean(rets) * 100), line_color=ACCENT, line_dash="dash",
                  line_width=1.5, annotation_text=f"Avg {np.mean(rets)*100:+.2f}%",
                  annotation_font_color=ACCENT)
    fig.update_layout(showlegend=False, xaxis_tickangle=-45)
    return _plot_layout(fig, f"TRADE-BY-TRADE P&L · {len(rets)} TRADES", height=380)


def chart_trade_hist(dom, hist_var):
    import plotly.graph_objects as go
    r = dom.get("rets", np.array([])) * 100
    fig = go.Figure()
    if len(r):
        fig.add_trace(go.Histogram(x=r, nbinsx=40, marker_color=BLUE, opacity=0.8))
        fig.add_vline(x=float(np.mean(r)), line_color=GREEN, line_width=2,
                      annotation_text=f"Expectancy {np.mean(r):+.2f}%", annotation_font_color=GREEN)
        fig.add_vline(x=hist_var, line_color=RED, line_dash="dot", line_width=2,
                      annotation_text=f"VaR95 {hist_var:.2f}%", annotation_font_color=RED)
    return _plot_layout(fig, "DISTRIBUTION OF TRADE RETURNS (%)")


def chart_monthly_pnl(dom):
    """Monthly P&L heatmap-style bar chart."""
    import plotly.graph_objects as go
    rets = dom.get("rets", np.array([]))
    dates = dom.get("dates", [])
    if len(rets) == 0:
        return _plot_layout(go.Figure(), "MONTHLY P&L")
    s = pd.Series(rets, index=pd.to_datetime(dates))
    monthly = s.resample("ME").sum() * 100
    colors = [GREEN if v >= 0 else RED for v in monthly.values]
    fig = go.Figure(go.Bar(
        x=[d.strftime('%b %Y') for d in monthly.index],
        y=monthly.values,
        marker_color=colors,
        opacity=0.85,
        text=[f"{v:+.1f}%" for v in monthly.values],
        textposition="outside",
        textfont=dict(size=9),
    ))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    return _plot_layout(fig, "MONTHLY P&L (%)", height=300)


def chart_horizon_scan(scan_rows):
    import plotly.graph_objects as go
    sdf = pd.DataFrame(scan_rows)
    fig = go.Figure()
    if not sdf.empty:
        for side, color in [("LONG", GREEN), ("SHORT", RED)]:
            s = sdf[sdf["side"] == side]
            fig.add_trace(go.Bar(x=[f"{h}d" for h in s["horizon"]], y=s["oos_dsr"],
                                 name=side, marker_color=color, opacity=0.85))
        fig.add_hline(y=0.60, line_color=ACCENT, line_dash="dash", line_width=1.5,
                      annotation_text="0.60 edge gate", annotation_font_color=ACCENT)
        fig.update_layout(showlegend=True, barmode="group")
    return _plot_layout(fig, "ALPHA-DECAY · OOS DEFLATED SHARPE BY HORIZON")


def chart_garch(sigma, vol_model):
    import plotly.graph_objects as go
    s = (sigma.tail(250) * np.sqrt(TRADING_DAYS) * 100)
    fig = go.Figure(go.Scatter(x=s.index, y=s.values, line=dict(color=BLUE, width=1.5)))
    return _plot_layout(fig, f"CONDITIONAL VOLATILITY · {vol_model} (annualized %)")


def chart_ic_decay(ic_res):
    import plotly.graph_objects as go
    decay = ic_res.get("decay", [])
    if not decay:
        return _plot_layout(go.Figure(), "IC DECAY")
    lags = [d["lag"] for d in decay]
    ics = [d["ic"] for d in decay]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=lags, y=ics,
                         marker_color=[GREEN if v > 0 else RED for v in ics], opacity=0.8))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    return _plot_layout(fig, "IC DECAY BY LAG (days forward)")


def chart_hmm_states(hmm):
    import plotly.graph_objects as go
    if "error" in hmm:
        return _plot_layout(go.Figure(), "HMM REGIME")
    states = hmm["states"]; r = hmm["returns"]
    n = min(len(states), len(r), 500)
    states_plot = states[-n:]; r_plot = r[-n:]
    colors = [GREEN if s == 0 else RED for s in states_plot]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=list(range(n)), y=r_plot * 100,
                         marker_color=colors, opacity=0.7, name="Returns"))
    return _plot_layout(fig, f"HMM REGIME · {hmm['current_state_label']} (last {n} obs)", 280)


def chart_meta_confidence(meta):
    import plotly.graph_objects as go
    fig = go.Figure()
    if "error" not in meta and "confidence_series" in meta:
        cs = meta["confidence_series"]
        fig.add_trace(go.Scatter(x=cs.index, y=cs.values,
                                 line=dict(color=PURPLE, width=1.5), fill="tozeroy",
                                 fillcolor="rgba(155,89,182,0.10)"))
        fig.add_hline(y=0.5, line_color=ACCENT, line_dash="dash", line_width=1,
                      annotation_text="0.5 threshold", annotation_font_color=ACCENT)
    return _plot_layout(fig, "META-LABEL CONFIDENCE P(WIN) PER TRADE", 280)


def chart_factor_decay(decay_rows):
    import plotly.graph_objects as go
    fig = go.Figure()
    colors_map = {"MKT": BLUE, "SIZE": GREEN, "MOM": ACCENT}
    for row in decay_rows:
        fname = row["factor"]
        fig.add_trace(go.Scatter(x=row["lags"], y=row["normalized"], name=fname,
                                 line=dict(color=colors_map.get(fname, MUTE), width=2)))
    fig.add_hline(y=0.5, line_color=MUTE, line_dash="dash", line_width=1,
                  annotation_text="half-life", annotation_font_color=MUTE)
    fig.update_layout(showlegend=True)
    return _plot_layout(fig, "FACTOR DECAY · NORMALIZED BETA vs LAG (days)")


# ════════════════════════ ENTRY POINT ════════════════════════
def render_quant_analysis():
    import streamlit as st
    from datetime import datetime, timezone

    _hist = st.cache_data(ttl=900, show_spinner=False)(fetch_history)
    _proxy = st.cache_data(ttl=900, show_spinner=False)(fetch_factor_proxies)

    st.markdown(f"""
    <style>
      .stTabs [data-baseweb="tab-list"] {{ gap: 28px; border-bottom:1px solid {BORDER}; }}
      .stTabs [data-baseweb="tab"] {{ font-family:{MONO}; font-size:11px; letter-spacing:1.5px;
        color:{MUTE}; padding:8px 4px; }}
      .stTabs [aria-selected="true"] {{ color:{ACCENT}; }}
      .block-container {{ padding-top:1.2rem; }}
    </style>""", unsafe_allow_html=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;
    padding:12px 18px;margin-bottom:10px;display:flex;
    justify-content:space-between;align-items:center;'>
      <div style='font-family:{MONO};font-size:13px;font-weight:700;
        color:{ACCENT};letter-spacing:2px;'>ARKA · QUANT TERMINAL v2</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>
        {ts} · NSE · BACKTEST MODE</div>
    </div>""", unsafe_allow_html=True)

    with st.form("qt"):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        symbol = c1.text_input("NSE Symbol", value="RELIANCE",
                               label_visibility="collapsed",
                               placeholder="NSE symbol e.g. RELIANCE")
        cost_bps = c2.number_input("Friction (bps)", value=15.0, min_value=0.0, step=1.0)
        ptsl = c3.selectbox("Barrier σ", ["2.0 / 1.5", "1.5 / 1.0", "3.0 / 2.0"])
        horizon = c4.selectbox("Horizon (d)", HORIZONS, index=0)
        run = st.form_submit_button("RUN ANALYSIS", type="primary", use_container_width=True)

    if not run:
        st.info("Enter an NSE symbol and press RUN ANALYSIS. Hover ⓘ on any metric for a definition.")
        return
    if not symbol.strip():
        st.error("Enter a symbol."); return
    pt, sl = [float(x) for x in ptsl.split("/")]

    with st.spinner("Running institutional quant pipeline..."):
        df, sym, info, period_used = _hist(symbol)
        if df.empty or len(df) < 60:
            st.error(f"Insufficient data for {sym} — need at least 60 bars."); return

        short_history = len(df) < MIN_BARS * 2
        if short_history:
            st.warning(f"⚠ Only {len(df)} trading days ({period_used}) — sample is short. Need 500+ bars.")

        actual_start = df.index[0].strftime("%Y-%m-%d")
        actual_end = df.index[-1].strftime("%Y-%m-%d")
        total_bars = len(df)

        close = df["Close"]; price = float(close.iloc[-1])
        proxies = _proxy(); bench = proxies.get("MKT", pd.Series(dtype=float))
        stat = stationarity_analysis(close)
        sigma, vol_model, fwd = garch_vol(close, horizon)
        vol = volatility_regime(sigma)
        fac = factor_battery(close, bench)
        factor = factor_attribution(close, proxies)
        fund = fundamentals(info)
        hist = historical_var_es(close, horizon)
        mc = monte_carlo_var(close, horizon)
        stress = stress_scenarios(price, fac["beta"])
        bt_long = triple_barrier_nonoverlap(close, sigma, 1, pt, sl, horizon, cost_bps)
        bt_short = triple_barrier_nonoverlap(close, sigma, -1, pt, sl, horizon, cost_bps)
        verdict, side, dom = disposition(bt_long, bt_short)
        ex_side = side if side != 0 else 1
        ex = execution_matrix(price, vol["sigma_daily"], ex_side, pt, sl, cost_bps)
        wf = purged_walk_forward(close, sigma, ex_side, pt, sl, horizon, cost_bps)
        size = position_sizing(dom, vol["sigma_annual"])
        scan_rows, scan_best = horizon_scan(close, pt, sl, cost_bps)
        tc = transaction_cost_model(close, dom["turnover"], cost_bps,
                                    market_impact_bps=5.0, slippage_bps=3.0, holding_days=horizon)
        ic_res = information_coefficient(close, sigma, horizons=[5, 10, 21])
        fd_rows = factor_decay_analysis(close, proxies, max_lag=20)
        hmm = hmm_regime_detection(close)
        meta = meta_labeling(close, sigma, side=ex_side, pt=pt, sl=sl,
                             vbar=horizon, cost_bps=cost_bps)

    # ── AI ANALYSIS (auto-run if key present) ──
    ai_result = None
    try:
        api_key = st.secrets.get("GEMINI_KEY", "")
    except Exception:
        api_key = ""

    if api_key:
        with st.spinner("🤖 AI analyzing all metrics..."):
            try:
                ai_result = gemini_full_analysis(
                    sym, fund, fac, vol, stat, ex, dom, wf, size, factor,
                    mc, hist, stress, scan_best, verdict, tc, ic_res, hmm, meta, api_key
                )
            except Exception as e:
                st.warning(f"AI analysis failed: {e}")

    ai_score = ai_result["score"] if ai_result else None
    ai_action = ai_result["action"] if ai_result else None
    ai_tabs = ai_result["tab_summaries"] if ai_result else {}

    # ── HEADER CARD ──
    vcolor = {"LONG": GREEN, "SHORT": RED, "NO EDGE": MUTE}[verdict]
    action_color = {"BUY": GREEN, "SELL": RED, "AVOID": ORANGE}.get(ai_action, MUTE)
    score_html = _score_gauge_html(ai_score) if ai_score is not None else ""

    gauge_col_html = f"""
    <div style='display:flex;flex-direction:column;align-items:center;gap:4px;'>
      {score_html}
      <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1px;'>AI SCORE</div>
    </div>""" if score_html else ""

    action_html = f"""
    <div style='text-align:center;margin-top:4px;'>
      <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1px;'>AI ACTION</div>
      <div style='font-family:{MONO};font-size:22px;font-weight:800;color:{action_color};
        letter-spacing:3px;'>{ai_action or "—"}</div>
    </div>""" if ai_action else ""

    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{PANEL2},{PANEL});
    border:1px solid {BORDER};border-left:4px solid {vcolor};
    border-radius:10px;padding:18px 22px;margin:6px 0 12px;
    display:flex;justify-content:space-between;align-items:center;gap:16px;'>
      <!-- LEFT: stock info -->
      <div style='flex:2;'>
        <div style='font-family:{MONO};font-size:12px;color:{MUTE};letter-spacing:1px;'>
          {sym} · {fund['name']}</div>
        <div style='font-size:11px;color:{MUTE};margin-bottom:4px;'>{fund['sector']} · {fund['industry']}</div>
        <div style='font-family:{MONO};font-size:26px;font-weight:700;color:{IVORY};'>
          ₹{price:,.2f}</div>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-top:2px;'>
          Data: {actual_start} → {actual_end} · {total_bars} bars · {period_used}</div>
      </div>
      <!-- CENTER: gauge -->
      <div style='flex:1;display:flex;flex-direction:column;align-items:center;'>
        {gauge_col_html}
        {action_html}
      </div>
      <!-- RIGHT: verdict -->
      <div style='flex:2;text-align:right;'>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1px;'>
          DISPOSITION · DSR-GATED</div>
        <div style='font-family:{MONO};font-size:28px;font-weight:700;
          color:{vcolor};line-height:1.1;'>{verdict}</div>
        <div style='font-family:{MONO};font-size:12px;color:{IVORY};margin-top:4px;'>
          DSR {dom['dsr']:.2f} · OOS-DSR {_fmt(wf['oos_dsr'],"",2)}</div>
        <div style='font-family:{MONO};font-size:11px;color:{MUTE};margin-top:2px;'>
          HMM: {hmm.get('current_state_label','—') if 'error' not in hmm else '—'}</div>
        <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>
          Win: {dom['win_rate']:.0f}% · PF: {_fmt(dom.get('profit_factor',0),"",2)} · n={dom['n']}</div>
      </div>
    </div>""", unsafe_allow_html=True)

    tabs = st.tabs([
        "OVERVIEW", "CHARTS", "FACTOR", "RISK",
        "BACKTEST", "COST MODEL", "IC & SIGNAL",
        "HMM REGIME", "META-LABEL", "FACTOR DECAY", "STATIONARITY", "AI RESEARCH"
    ])

    # ── TAB 0: OVERVIEW ──
    with tabs[0]:
        if ai_tabs.get("OVERVIEW_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["OVERVIEW_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("EXECUTION MATRIX"), unsafe_allow_html=True)
        _metric_row([("Entry", f"₹{ex['entry']:,.2f}"), ("Target", f"₹{ex['target']:,.2f}"),
                     ("Stop", f"₹{ex['stop']:,.2f}"), ("R:R", f"{ex['rr']} : 1")])
        st.markdown(_sec("EDGE & COST REALITY"), unsafe_allow_html=True)
        _metric_row([("Suggested Weight", f"{size['weight']*100:.1f}%"),
                     ("Turnover", f"{dom['turnover']:.0f}/yr"),
                     ("Break-even bps", f"{dom['breakeven_bps']:.0f}"),
                     ("Vol Regime", vol['regime'])])
        _cap("Break-even bps must exceed real RT cost (Indian equities ≈ 15-20 bps).")
        st.markdown(_sec("SIGNAL QUALITY SNAPSHOT"), unsafe_allow_html=True)
        ic_5d = ic_res.get("by_horizon", {}).get(5, {})
        hmm_lbl = hmm.get("current_state_label", "—") if "error" not in hmm else "—"
        _metric_row([("IC (5d)", _fmt(ic_5d.get("ic_mean"), "", 3)),
                     ("ICIR (5d)", _fmt(ic_5d.get("icir"), "", 3)),
                     ("HMM State", hmm_lbl),
                     ("Meta-Label F1", _fmt(meta.get("f1"), "", 3))])
        st.markdown(_sec("RETURN / RISK BATTERY"), unsafe_allow_html=True)
        _metric_row([("Sharpe", _fmt(fac['sharpe'], "", 2)), ("Sortino", _fmt(fac['sortino'], "", 2)),
                     ("Max DD", _fmt(fac['max_dd'], "%", 1)), ("Beta", _fmt(fac['beta'], "", 2))])
        st.markdown(_sec("FUNDAMENTALS"), unsafe_allow_html=True)
        cells = []
        if fund['mcap_cr']: cells.append(("Mkt Cap", f"₹{fund['mcap_cr']:,.0f}Cr"))
        if fund['pe']: cells.append(("P/E", _fmt(fund['pe'], "", 1)))
        if fund['pb']: cells.append(("P/B", _fmt(fund['pb'], "", 2)))
        if fund['roe']: cells.append(("ROE", _fmt(fund['roe'], "%", 1)))
        if cells: _metric_row(cells)

    # ── TAB 1: CHARTS ──
    with tabs[1]:
        if ai_tabs.get("CHARTS_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["CHARTS_SUMMARY"]), unsafe_allow_html=True)
        trade_dates = [pd.Timestamp(d) for d in dom.get("dates", [])]
        st.plotly_chart(chart_price_setup(df, ex, verdict, sigma, trade_dates), use_container_width=True)
        _cap("Candlesticks (last 180d) + GARCH ±2σ band + volume + entry dots (▲) + target/stop zones.")
        st.plotly_chart(chart_equity(dom), use_container_width=True)
        _cap(f"Equity curve [{dom.get('date_range','')}]. {dom['n']} trades.")
        st.plotly_chart(chart_drawdown(dom), use_container_width=True)
        _cap("Peak-to-trough drawdown of the strategy.")
        st.plotly_chart(chart_trade_hist(dom, hist['var']), use_container_width=True)
        _cap("Return distribution. Green = expectancy line. Red = VaR95.")
        st.plotly_chart(chart_horizon_scan(scan_rows), use_container_width=True)
        _cap("OOS Deflated Sharpe by holding horizon. Gold = 0.60 credibility gate.")
        st.plotly_chart(chart_garch(sigma, vol_model), use_container_width=True)
        _cap("Annualized GARCH volatility. Rising = size down.")

    # ── TAB 2: FACTOR ──
    with tabs[2]:
        if ai_tabs.get("FACTOR_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["FACTOR_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("FACTOR REGRESSION · NEWEY-WEST t-STATS"), unsafe_allow_html=True)
        _cap("Proxies: MKT=^NSEI, SIZE=MID−LARGE, MOM=rolling market mean. |t|>2 ≈ significant.")
        betas = factor.get("betas", {}); ts_ = factor.get("tstats", {})
        if betas:
            cells = [(k, f"{_fmt(v,'',2)} (t={_fmt(ts_.get(k),'',1)})") for k, v in betas.items()]
            cells += [("Factor Alpha", _fmt(factor['alpha_ann'], "%/yr", 1)),
                      ("R²", _fmt(factor['r2'], "", 2))]
            _metric_row(cells)
        else:
            st.warning("Insufficient overlapping data for factor regression.")
        st.markdown(_sec("MARKET-RELATIVE"), unsafe_allow_html=True)
        _metric_row([("Beta", _fmt(fac['beta'], "", 2)), ("CAPM Alpha", _fmt(fac['alpha'], "%/yr", 1)),
                     ("Skew", _fmt(fac['skew'], "", 2)), ("Excess Kurt", _fmt(fac['kurt'], "", 2))])

    # ── TAB 3: RISK ──
    with tabs[3]:
        if ai_tabs.get("RISK_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["RISK_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec(f"VaR / ES · {horizon}D HORIZON · 95% CONFIDENCE"), unsafe_allow_html=True)
        _metric_row([("VaR 95%", _fmt(hist['var'], "%", 2)),
                     ("Expected Shortfall", _fmt(hist['es'], "%", 2)),
                     ("Monte Carlo VaR", _fmt(mc['var'], "%", 2)),
                     ("t-dist df", _fmt(mc['t_df'], "", 1))])
        st.markdown(_sec("STRESS SCENARIOS · BETA-PROPAGATED"), unsafe_allow_html=True)
        _metric_row([(n.split(" (")[0], f"{v['pct']:+.1f}% → ₹{v['price']:,.0f}") for n, v in stress.items()])
        _cap("Estimated loss if each historical crash recurs, scaled by beta to NIFTY.")
        st.markdown(_sec(f"CONDITIONAL VOLATILITY · {vol_model}"), unsafe_allow_html=True)
        _metric_row([("GARCH σ daily", _fmt(vol['sigma_daily']*100, "%", 2)),
                     ("σ annual", _fmt(vol['sigma_annual']*100, "%", 1)),
                     (f"σ {horizon}d fwd", _fmt(fwd*100, "%", 2)),
                     ("Vol Regime", vol['regime'])])

    # ── TAB 4: BACKTEST ──
    with tabs[4]:
        if ai_tabs.get("BACKTEST_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["BACKTEST_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-bottom:6px;'>"
            f"Tested {dom.get('date_range','—')} · {dom['n']} trades · "
            f"Horizon {horizon}d · Barrier {pt}/{sl}σ · Cost {cost_bps}bps</div>",
            unsafe_allow_html=True
        )
        st.markdown(_sec("IN-SAMPLE SKILL"), unsafe_allow_html=True)
        lo, hi = dom['exp_ci']
        _metric_row([("Expectancy/trade", f"{dom['expectancy']*100:+.2f}%"),
                     ("Expectancy CI", f"{lo*100:+.2f}% .. {hi*100:+.2f}%"),
                     ("Prob Sharpe", _fmt(dom['psr'], "", 2)),
                     ("Deflated Sharpe", _fmt(dom['dsr'], "", 2))])
        _metric_row([("Win Rate", f"{dom['win_rate']:.0f}%"),
                     ("Avg Win", f"{dom.get('avg_win',0):+.2f}%"),
                     ("Avg Loss", f"{dom.get('avg_loss',0):+.2f}%"),
                     ("Profit Factor", _fmt(dom.get('profit_factor',0), "", 2))])
        _metric_row([("Max Consec. Loss", f"{dom.get('max_consec_loss',0)} trades"),
                     ("Avg Hold", f"{dom.get('avg_hold',horizon)}d"),
                     ("Turnover", f"{dom['turnover']:.0f}/yr"),
                     ("Break-even", f"{dom['breakeven_bps']:.0f} bps")])
        st.markdown(_sec("OUT-OF-SAMPLE · PURGED WALK-FORWARD"), unsafe_allow_html=True)
        _metric_row([("OOS Deflated Sharpe", _fmt(wf['oos_dsr'], "", 2) if wf['oos_n'] else "—"),
                     ("OOS Expectancy", _fmt((wf['oos_expectancy'] or 0)*100, "%", 2) if wf['oos_n'] else "—"),
                     ("OOS Win Rate", _fmt(wf['oos_win_rate'], "%", 0) if wf['oos_n'] else "—"),
                     ("OOS Trades", f"{wf['oos_n']}")])
        if dom['dsr'] < 0.60:
            st.warning("⚠ Deflated Sharpe < 0.60 → no statistically reliable in-sample edge.")
        if wf['oos_n'] > 0 and (wf['oos_dsr'] or 0) < 0.60:
            st.error("⛔ OOS Deflated Sharpe < 0.60 → edge does NOT survive out-of-sample. Do not trade.")

        # Trade log chart
        st.markdown(_sec("TRADE-BY-TRADE LOG"), unsafe_allow_html=True)
        st.plotly_chart(chart_trade_log(dom), use_container_width=True)
        st.plotly_chart(chart_monthly_pnl(dom), use_container_width=True)

        st.markdown(_sec("ALPHA-DECAY · MULTI-HORIZON OOS SCAN"), unsafe_allow_html=True)
        sdf = pd.DataFrame(scan_rows)
        if not sdf.empty:
            sdf2 = sdf.copy()
            sdf2["oos_dsr"] = sdf2["oos_dsr"].round(2)
            sdf2["oos_exp"] = (sdf2["oos_exp"].astype(float) * 100).round(2)
            sdf2.columns = ["Horizon(d)", "Side", "OOS-DSR", "OOS-Exp %", "OOS n"]
            st.dataframe(sdf2, use_container_width=True, hide_index=True)
        if scan_best:
            st.success(f"Best horizon: {scan_best['horizon']}d {scan_best['side']} "
                       f"(OOS-DSR {scan_best['oos_dsr']:.2f})")

        # Detailed trade table
        if dom['n'] > 0:
            st.markdown(_sec("INDIVIDUAL TRADE LEDGER"), unsafe_allow_html=True)
            trade_df = pd.DataFrame({
                "Date": [pd.Timestamp(d).strftime('%Y-%m-%d') for d in dom['dates']],
                "Outcome": dom['outcomes'],
                "Return %": [f"{r*100:+.2f}%" for r in dom['rets']],
                "Equity": [f"{e:.4f}" for e in dom['equity'].values],
            })
            st.dataframe(trade_df, use_container_width=True, hide_index=True, height=280)

    # ── TAB 5: COST MODEL ──
    with tabs[5]:
        if ai_tabs.get("COST_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["COST_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("TRANSACTION COST DECOMPOSITION"), unsafe_allow_html=True)
        _cap("Round-trip cost: brokerage + market impact (Kyle λ) + slippage + regulatory.")
        _metric_row([("Commission", f"{tc['commission_bps']:.1f} bps"),
                     ("Market Impact", f"{tc['impact_bps']:.1f} bps"),
                     ("Slippage", f"{tc['slippage_bps']:.1f} bps"),
                     ("Regulatory", f"{tc['regulatory_bps']:.3f} bps")])
        _metric_row([("Total RT Cost", f"{tc['total_rt_bps']:.1f} bps"),
                     ("Turnover/yr", f"{tc['turnover']:.0f}x"),
                     ("Annual Cost Drag", f"{tc['annual_cost_pct']:.2f}%"),
                     ("Cost/Vol Ratio", _fmt(tc['cost_to_vol_ratio'], "", 3))])
        st.markdown(_sec("NET ALPHA AFTER COSTS"), unsafe_allow_html=True)
        net_color = GREEN if tc['viable'] else RED
        st.markdown(
            f"<div style='font-family:{MONO};font-size:16px;color:{net_color};margin:8px 0;'>"
            f"Net Alpha = {tc['net_alpha_pct']:+.2f}% / year &nbsp;·&nbsp; "
            f"{'✅ VIABLE' if tc['viable'] else '❌ NOT VIABLE — costs exceed gross alpha'}</div>",
            unsafe_allow_html=True
        )
        _cap("Indian equity delivery RT ≈ 15-20 bps. Intraday ≈ 5-10 bps.")

    # ── TAB 6: IC & SIGNAL ──
    with tabs[6]:
        if ai_tabs.get("IC_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["IC_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("INFORMATION COEFFICIENT · SIGNAL QUALITY"), unsafe_allow_html=True)
        _cap("IC = Spearman rank-correlation of vol signal vs future returns. ICIR > 0.5 = consistent.")
        ic_by_h = ic_res.get("by_horizon", {})
        ic_cells = []
        for h in [5, 10, 21]:
            d_ = ic_by_h.get(h, {})
            ic_cells.append((f"IC ({h}d)", _fmt(d_.get("ic_mean"), "", 3)))
            ic_cells.append((f"ICIR ({h}d)", _fmt(d_.get("icir"), "", 3)))
        if ic_cells:
            _metric_row(ic_cells[:4])
            if len(ic_cells) > 4: _metric_row(ic_cells[4:])
        st.plotly_chart(chart_ic_decay(ic_res), use_container_width=True)
        _cap("IC decay: steep drop = short optimal hold period.")

    # ── TAB 7: HMM ──
    with tabs[7]:
        if ai_tabs.get("HMM_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["HMM_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("HIDDEN MARKOV MODEL · 2-STATE REGIME"), unsafe_allow_html=True)
        if "error" in hmm:
            st.warning(hmm["error"])
        else:
            state_color = GREEN if hmm["current_state"] == 0 else RED
            st.markdown(
                f"<div style='font-family:{MONO};font-size:14px;color:{state_color};margin:8px 0;'>"
                f"Current Regime: <b>{hmm['current_state_label']}</b> · "
                f"{hmm['days_in_state']} days in state · P(stay)={hmm['prob_stay']:.3f}</div>",
                unsafe_allow_html=True
            )
            _metric_row([("State 0 (BULL) Ann Ret", f"{hmm['state_means_ann_pct'][0]:+.1f}%"),
                         ("State 0 Ann Vol", f"{hmm['state_vols_ann_pct'][0]:.1f}%"),
                         ("State 1 (BEAR) Ann Ret", f"{hmm['state_means_ann_pct'][1]:+.1f}%"),
                         ("State 1 Ann Vol", f"{hmm['state_vols_ann_pct'][1]:.1f}%")])
            st.markdown(_sec("TRANSITION MATRIX"), unsafe_allow_html=True)
            A = hmm["transition_matrix"]
            tdf = pd.DataFrame(A, index=["From BULL", "From BEAR"], columns=["→ BULL", "→ BEAR"])
            st.dataframe(tdf.round(3), use_container_width=True)
            _cap("Diagonal = P(stay). High diagonal = regime persistence.")
            st.plotly_chart(chart_hmm_states(hmm), use_container_width=True)

    # ── TAB 8: META-LABEL ──
    with tabs[8]:
        if ai_tabs.get("META_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["META_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("META-LABELING · SECONDARY CLASSIFIER"), unsafe_allow_html=True)
        _cap("Primary: triple-barrier direction. Secondary: logistic model predicts P(win). F1 > 0.55 = useful filter.")
        if "error" in meta:
            st.warning(meta["error"])
        else:
            _metric_row([("Accuracy", _fmt(meta['meta_accuracy'], "", 3)),
                         ("Precision", _fmt(meta['meta_precision'], "", 3)),
                         ("Recall", _fmt(meta['meta_recall'], "", 3)),
                         ("F1 Score", _fmt(meta['f1'], "", 3))])
            st.markdown(_sec("FEATURE IMPORTANCE"), unsafe_allow_html=True)
            fi = meta.get("feature_importance", {})
            if fi:
                fi_df = pd.DataFrame(list(fi.items()), columns=["Feature", "Importance"])
                fi_df = fi_df.sort_values("Importance", ascending=False)
                st.dataframe(fi_df, use_container_width=True, hide_index=True)
            st.plotly_chart(chart_meta_confidence(meta), use_container_width=True)
            _cap("Trades above 0.5 threshold = high confidence. Consider filtering below 0.55.")

    # ── TAB 9: FACTOR DECAY ──
    with tabs[9]:
        if ai_tabs.get("DECAY_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["DECAY_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("FACTOR DECAY · NORMALIZED BETA vs LAG"), unsafe_allow_html=True)
        _cap("Half-life = days until factor beta drops to 50%. Short half-life = trade fast.")
        if fd_rows:
            hl_cells = [(row["factor"], f"{row['half_life']}d half-life") for row in fd_rows]
            _metric_row(hl_cells)
            st.plotly_chart(chart_factor_decay(fd_rows), use_container_width=True)
        else:
            st.warning("Insufficient factor proxy data.")

    # ── TAB 10: STATIONARITY ──
    with tabs[10]:
        if ai_tabs.get("STAT_SUMMARY"):
            st.markdown(_ai_box(ai_tabs["STAT_SUMMARY"]), unsafe_allow_html=True)
        st.markdown(_sec("FRACTIONAL DIFFERENTIATION · AFML CH.5"), unsafe_allow_html=True)
        _metric_row([("Frac-diff d", _fmt(stat['d'], "", 2)),
                     ("ADF p-value", _fmt(stat['adf_pvalue'], "", 4)),
                     ("Memory Retained", _fmt(stat['memory_retained'], "", 3)),
                     ("Memory Gain", _fmt(stat['memory_gain'], "", 3))])
        _cap("Lowest d achieving ADF p<0.05 — stationarity with maximum memory retention.")

    # ── TAB 11: AI RESEARCH ──
    with tabs[11]:
        st.markdown(_sec("AI QUANT RESEARCH NOTE"), unsafe_allow_html=True)
        if not api_key:
            st.warning("Add GEMINI_KEY to Streamlit secrets to enable AI analysis.")
        elif ai_result:
            # Score + Action banner
            score_val = ai_result["score"]
            act = ai_result["action"]
            act_c = {"BUY": GREEN, "SELL": RED, "AVOID": ORANGE}.get(act, MUTE)
            sc_c = GREEN if score_val >= 61 else (ORANGE if score_val >= 41 else RED)
            st.markdown(f"""
            <div style='display:flex;gap:16px;margin-bottom:12px;'>
              <div style='flex:1;background:{PANEL2};border:1px solid {sc_c};border-radius:8px;
                padding:14px;text-align:center;'>
                <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>SETUP SCORE</div>
                <div style='font-family:{MONO};font-size:36px;font-weight:800;color:{sc_c};'>{score_val}</div>
                <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>/100</div>
              </div>
              <div style='flex:1;background:{PANEL2};border:1px solid {act_c};border-radius:8px;
                padding:14px;text-align:center;'>
                <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>AI RECOMMENDATION</div>
                <div style='font-family:{MONO};font-size:36px;font-weight:800;color:{act_c};'>{act}</div>
                <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>based on all metrics</div>
              </div>
            </div>""", unsafe_allow_html=True)

            # Full research note
            st.markdown(
                f"<div style='background:{PANEL};border:1px solid {BORDER};"
                f"border-radius:8px;padding:18px 22px;color:{IVORY};"
                f"line-height:1.8;font-size:13.5px;'>{ai_result['full_note']}</div>",
                unsafe_allow_html=True
            )
        else:
            st.error("AI analysis failed. Check GEMINI_KEY and try again.")

        with st.expander("📖 Full Metric Glossary"):
            for k, v in GLOSSARY.items():
                st.markdown(f"**{k}** — {v}")
        _cap("AI summarizes computed numbers only. Educational tool — not SEBI-registered investment advice.")

    st.markdown(
        f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-top:14px;text-align:center;'>"
        "Backtesting / educational tool · Not investment advice · "
        "Statistical edge does not guarantee future returns · "
        "Always validate against your own execution costs and risk tolerance</div>",
        unsafe_allow_html=True
    )
