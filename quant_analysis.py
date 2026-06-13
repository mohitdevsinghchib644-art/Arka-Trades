"""
quant_analysis.py — Institutional Single-Asset Quant Terminal (PART 1 of 2)
===========================================================================
Paste Part 1 then Part 2 into the SAME file, in order.
For backtesting / validating discretionary strategies. Educational; not advice.

Methods (institutional only):
  - AFML (López de Prado): fractional differentiation (Ch.5),
    non-overlapping triple-barrier (Ch.3), purged walk-forward (Ch.7),
    Probabilistic & Deflated Sharpe (Ch.8).
  - GARCH(1,1)-t conditional vol (arch; EWMA fallback).
  - Factor attribution with Newey-West (HAC) standard errors.
  - Monte-Carlo (Student-t) & historical VaR / Expected Shortfall, stress.
  - Multi-horizon alpha-decay scan (best OOS Deflated Sharpe).
  - Stationary-block bootstrap CIs; turnover / break-even cost.
  - Vol-target + fractional-Kelly sizing.

Deps: pandas numpy scipy statsmodels yfinance streamlit ; optional: arch, google-generativeai
"""

import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats as scistats
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

# ── Professional terminal palette ──
BG, PANEL, PANEL2, BORDER = "#0B0E13", "#11151D", "#161C26", "#222B38"
ACCENT, IVORY, MUTE = "#C9A227", "#E8EDF2", "#8593A3"
GREEN, RED, BLUE = "#1FB97A", "#E8554E", "#4C8DD6"
MONO = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"
TRADING_DAYS = 252
HORIZONS = [5, 10, 21, 63]   # multi-horizon alpha-decay scan
DEFAULT_HORIZON = 5

FACTOR_PROXIES = {"MKT": "^NSEI", "LARGE": "^CNX100", "MID": "^CNXMIDCAP"}


# ════════════════════════════════════════════════════════════
# DATA
# ════════════════════════════════════════════════════════════

def fetch_history(symbol: str, period: str = "5y", interval: str = "1d"):
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym += ".NS"
    tk = yf.Ticker(sym)
    df = tk.history(period=period, interval=interval)
    if df.empty:
        return df, sym, {}
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    try:
        info = tk.info or {}
    except Exception:
        info = {}
    return df, sym, info


def fetch_series(yahoo_symbol: str, period: str = "5y"):
    try:
        return yf.Ticker(yahoo_symbol).history(period=period, interval="1d")["Close"].dropna()
    except Exception:
        return pd.Series(dtype=float)


def fetch_factor_proxies(period: str = "5y"):
    return {k: fetch_series(s, period) for k, s in FACTOR_PROXIES.items()}


def _rets(s):
    return np.log(s / s.shift(1)).dropna()


# ════════════════════════════════════════════════════════════
# 1. FRACTIONAL DIFFERENTIATION (AFML Ch.5)
# ════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════
# 2. GARCH CONDITIONAL VOL (+ EWMA fallback)
# ════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════
# 3. FACTOR ATTRIBUTION with NEWEY-WEST (HAC) SEs
# ════════════════════════════════════════════════════════════

def _newey_west_se(X, resid, lags):
    n, k = X.shape
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
    Xraw = data.drop(columns=["y"]).values
    X = np.column_stack([np.ones(len(Xraw)), Xraw])
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
    return {"betas": betas, "tstats": tstats, "alpha_ann": alpha_ann,
            "r2": float(r2), "n": int(len(data))}


# ════════════════════════════════════════════════════════════
# 4. FACTOR BATTERY
# ════════════════════════════════════════════════════════════

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
            "autocorr1": ac1, "beta": beta, "alpha": (alpha * 100) if np.isfinite(alpha) else np.nan}


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
            "profit_margin": (g("profitMargins") or 0) * 100 if g("profitMargins") is not None else None,
            "wk52_high": g("fiftyTwoWeekHigh"), "wk52_low": g("fiftyTwoWeekLow"), "beta_info": g("beta")}


# ════════════════════════════════════════════════════════════
# 5. RISK: MC + HISTORICAL VaR/ES + STRESS
# ════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════
# 6. SKILL STATISTICS (AFML Ch.8) + BOOTSTRAP
# ════════════════════════════════════════════════════════════

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
    """Stationary block bootstrap CI (handles serial dependence)."""
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
    lo = float(np.percentile(stats, (1 - ci) / 2 * 100))
    hi = float(np.percentile(stats, (1 + ci) / 2 * 100))
    return (lo, hi)


# ════════════════════════════════════════════════════════════
# 7. TRIPLE-BARRIER + PURGED WF + MULTI-HORIZON SCAN + TURNOVER
# ════════════════════════════════════════════════════════════

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
    cost = cost_bps / 1e4; rets = []; i = 0
    while i < len(px) - vbar:
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            i += 1; continue
        rets.append(_barrier_outcome(px, i, s, side, pt, sl, vbar, cost)); i += vbar
    rets = np.array(rets)
    if len(rets) == 0:
        return {"expectancy": 0.0, "psr": 0.5, "dsr": 0.5, "win_rate": 0.0, "n": 0,
                "rets": rets, "exp_ci": (np.nan, np.nan), "turnover": 0.0, "breakeven_bps": 0.0}
    psr, _ = probabilistic_sharpe_ratio(rets, 0.0)
    dsr = deflated_sharpe(rets, n_trials=2)
    exp_ci = bootstrap_ci(rets, lambda x: x.mean())
    turnover = TRADING_DAYS / vbar  # round-trips per year (non-overlapping)
    gross_edge = rets.mean() + cost   # add back the modeled cost to get gross
    breakeven_bps = max(0.0, gross_edge * 1e4)  # bps per trade the edge can absorb
    return {"expectancy": float(rets.mean()), "psr": psr, "dsr": dsr,
            "win_rate": float((rets > 0).mean() * 100), "n": int(len(rets)),
            "rets": rets, "exp_ci": exp_ci, "turnover": float(turnover),
            "breakeven_bps": float(breakeven_bps)}


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
    """Alpha-decay scan: pick horizon with best OUT-OF-SAMPLE Deflated Sharpe."""
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
    """Verdict gated PURELY on Deflated Sharpe (no magic score)."""
    dom = bt_long if bt_long["dsr"] >= bt_short["dsr"] else bt_short
    side = 1 if dom is bt_long else -1
    verdict = ("LONG" if side == 1 else "SHORT") if (dom["dsr"] >= 0.60 and dom["expectancy"] > 0) else "NO EDGE"
    return verdict, (side if verdict != "NO EDGE" else 0), dom

# ── END PART 1 ──
# ════════════════════════════════════════════════════════════
# PART 2 of 2 — UI + AI EXPLAINER  (paste below Part 1)
# ════════════════════════════════════════════════════════════

# ── Metric glossary: what it is + how quants / banks / IB use it ──
GLOSSARY = {
    "Deflated Sharpe": "Probability the strategy's Sharpe is REAL after correcting for how many strategies you tested (luck-adjusted). Quant funds (AQR, Two Sigma) use it as the go/no-go gate. Read: ≥0.60 = credible edge; <0.60 = likely noise, do not trade.",
    "Prob Sharpe": "Probability the Sharpe ratio is above zero given skew, fat tails and sample size. Banks use it to judge if a track record is statistically meaningful vs short-sample luck. Read: closer to 1.0 = more reliable.",
    "OOS Deflated Sharpe": "Deflated Sharpe measured on data the model never saw (purged walk-forward). This is the number that actually matters — in-sample always looks good. Read: trust this over in-sample.",
    "Expectancy/trade": "Average net profit per trade after costs, in %. Discretionary + systematic traders use it to size and compare setups. Read: must be positive AND its confidence interval should not straddle zero.",
    "Expectancy CI": "Bootstrap 95% confidence band for expectancy. If it includes 0, the edge is not statistically distinguishable from random. Quant desks reject setups whose CI crosses zero.",
    "Win Rate": "% of trades that closed positive. Useful but secondary — a 40% win rate with big winners can beat 60% with small ones. Read alongside expectancy, never alone.",
    "Turnover": "Round-trip trades per year implied by the horizon. Banks care because turnover drives transaction costs and capacity. Read: higher turnover needs a bigger gross edge to survive.",
    "Break-even bps": "How many basis points of cost per trade the gross edge can absorb before it dies. If your real broker/slippage cost exceeds this, the edge is fictional. This is THE reality check.",
    "Beta": "Sensitivity to the market (NIFTY). 1.0 = moves with market, >1 amplifies, <1 dampens. Used everywhere for hedging and risk budgeting.",
    "Factor Alpha": "Annual return left over AFTER stripping out market/size/momentum exposure. This is 'true' skill vs just riding factors. AQR/Barra-style attribution is built on this.",
    "t-stat": "How many standard errors a beta/alpha is from zero (Newey-West, robust to overlap). Read: |t|>2 ≈ statistically significant; below that, ignore the number.",
    "R²": "Fraction of the stock's moves explained by the factors. High R² = it's mostly factor-driven (little idiosyncratic edge); low R² = more stock-specific behavior.",
    "VaR 95%": "Worst loss NOT exceeded 95% of the time over the horizon. Regulatory + risk-desk standard at every bank. Read: a -8% VaR means 1-in-20 periods lose more than 8%.",
    "Expected Shortfall": "Average loss in the worst 5% of cases (the tail beyond VaR). Banks prefer it to VaR post-2008 because it captures tail severity. Read: always worse than VaR.",
    "Monte Carlo VaR": "VaR from simulating thousands of fat-tailed (Student-t) return paths instead of just history. Used when history is short or you want forward-looking tails.",
    "Stress Scenario": "What the position loses if a historical crash (2008/COVID) repeats, propagated through beta. Mandatory at banks for capital planning. Read: can you survive these?",
    "GARCH σ": "Volatility forecast that reacts to recent shocks and clusters (vol begets vol). The desk standard for vol forecasting; EWMA is the simpler fallback.",
    "Vol Regime": "Whether current vol is COMPRESSED / NORMAL / STRESSED vs its own 1yr history. Drives position sizing and conviction. Read: trim size in STRESSED.",
    "Frac-diff d": "Minimum differencing that makes price stationary while keeping memory (López de Prado). Lets ML/stat models work without throwing away predictive info. Read: lower d achieving ADF<0.05 = more memory kept.",
    "Suggested Weight": "Position size from vol-targeting × half-Kelly × statistical confidence. NOT advice — a disciplined upper bound. Read: scales down automatically when edge is weak.",
}


def gemini_research_note(sym, fund, fac, vol, stat, ex, dom, wf, size, factor,
                         mc, hist, stress, scan_best, verdict, api_key):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are a buy-side quant strategist writing for a profitable discretionary trader "
            "who is backtesting/validating his own strategy. Summarize ONLY the supplied computed "
            "statistics. For EACH metric you cite, briefly state what it is and how quant funds, "
            "investment banks and systematic traders use it, in plain language. Do NOT invent numbers, "
            "no chart-pattern talk, no advice. Lead with Deflated Sharpe and out-of-sample evidence. "
            "If OOS Deflated Sharpe < 0.60, state plainly there is no statistically reliable edge. "
            "Educational only; not SEBI registered."
        ),
    )

    def f(v, dp=2):
        try:
            return f"{float(v):.{dp}f}"
        except Exception:
            return "n/a"

    betas = ", ".join(f"{k} {f(v)} (t={f(factor.get('tstats',{}).get(k))})"
                      for k, v in factor.get("betas", {}).items()) or "n/a"
    stress_txt = "; ".join(f"{k}: {f(v['pct'],1)}%" for k, v in stress.items())
    sb = scan_best or {}
    prompt = f"""Quant validation note — {sym} ({fund['name']}, {fund['sector']}). Summarize ONLY these.

DECISION
- Verdict {verdict} (gated purely on Deflated Sharpe ≥ 0.60)
- In-sample: expectancy {f(dom['expectancy']*100)}%/trade [95% CI {f(dom['exp_ci'][0]*100)}..{f(dom['exp_ci'][1]*100)}%], PSR {f(dom['psr'])}, DSR {f(dom['dsr'])}, win {f(dom['win_rate'],0)}%, n={dom['n']}
- Out-of-sample (purged WF): expectancy {f((wf['oos_expectancy'] or 0)*100)}%/trade, OOS-DSR {f(wf['oos_dsr'])}, win {f(wf['oos_win_rate'],0)}%, n={wf['oos_n']}
- Best horizon by OOS-DSR: {sb.get('horizon','?')}d {sb.get('side','?')} (OOS-DSR {f(sb.get('oos_dsr'))})
- Costs: turnover {f(dom['turnover'],0)} trades/yr, break-even {f(dom['breakeven_bps'],0)} bps/trade
- Sizing: weight {size['weight']} (vol-target {size['vol_target_w']} × half-Kelly {size['kelly_half']} × conf {size['confidence']})

FACTOR ATTRIBUTION (proxy-based, Newey-West t-stats)
- Betas: {betas} | Factor alpha {f(factor.get('alpha_ann'),1)}%/yr | R² {f(factor.get('r2'))}

RISK
- Hist VaR {f(hist['var'])}% / ES {f(hist['es'])}% | MC VaR {f(mc['var'])}% / ES {f(mc['es'])}% (t df {f(mc['t_df'],1)})
- MaxDD {f(fac['max_dd'],1)}% | Beta {f(fac['beta'])} | Stress {stress_txt}
- Vol regime {vol['regime']} ({f(vol['percentile'],0)}th pct), ann {f(vol['sigma_annual']*100,1)}%
- Frac-diff d={f(stat['d'])} (ADF p={f(stat['adf_pvalue'],4)}); Sharpe {f(fac['sharpe'])}, Sortino {f(fac['sortino'])}

STRUCTURE (bold headers, <320 words):
**Verdict & Why** — DSR/OOS reasoning; if OOS-DSR<0.60 say NO RELIABLE EDGE.
**Cost Reality** — does break-even bps clear realistic Indian costs (~10-20 bps)? explain turnover.
**Factor Exposure** — what betas/t-stats/alpha mean and how desks use them.
**Risk** — VaR/ES/stress/regime + suggested weight, plain language.
**For Your Strategy** — how to use these stats to validate his discretionary setup."""
    return model.generate_content(prompt).text


# ── UI primitives ──
def _label(text):
    g = GLOSSARY.get(text)
    return f"{text} ⓘ" if g else text


def _grid(cells, cols=None):
    cols = cols or len(cells)
    html = (f"<div style='display:grid;grid-template-columns:repeat({cols},1fr);gap:1px;"
            f"background:{BORDER};border:1px solid {BORDER};border-radius:6px;overflow:hidden;'>")
    for label, value, color in cells:
        tip = GLOSSARY.get(label, "")
        mark = " <span style='color:%s;font-size:9px;'>ⓘ</span>" % MUTE if tip else ""
        title = f" title=\"{tip}\"" if tip else ""
        html += (f"<div style='background:{PANEL};padding:10px 12px;'{title}>"
                 f"<div style='font-size:9.5px;letter-spacing:.8px;color:{MUTE};text-transform:uppercase;'>{label}{mark}</div>"
                 f"<div style='font-family:{MONO};font-size:16px;font-weight:600;color:{color};margin-top:3px;'>{value}</div></div>")
    return html + "</div>"


def _sec(title):
    return (f"<div style='font-family:{MONO};font-size:11px;font-weight:600;color:{ACCENT};"
            f"letter-spacing:1.5px;margin:16px 0 8px;border-bottom:1px solid {BORDER};padding-bottom:5px;'>{title}</div>")


def _fmt(v, suffix="", dp=2, dash="—"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return f"{v:,.{dp}f}{suffix}"


def render_quant_analysis():
    import streamlit as st
    from datetime import datetime, timezone

    _hist = st.cache_data(ttl=900, show_spinner=False)(fetch_history)
    _proxy = st.cache_data(ttl=900, show_spinner=False)(fetch_factor_proxies)

    # Spaced-out tab labels + terminal styling
    st.markdown(f"""
    <style>
      .stTabs [data-baseweb="tab-list"] {{ gap: 34px; border-bottom:1px solid {BORDER}; }}
      .stTabs [data-baseweb="tab"] {{
        font-family:{MONO}; font-size:12px; letter-spacing:2px; color:{MUTE};
        padding:8px 4px; }}
      .stTabs [aria-selected="true"] {{ color:{ACCENT}; }}
      .block-container {{ padding-top:1.2rem; }}
    </style>""", unsafe_allow_html=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;padding:12px 18px;
    margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;'>
      <div style='font-family:{MONO};font-size:13px;font-weight:700;color:{ACCENT};letter-spacing:2px;'>ARKA · QUANT TERMINAL</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>{ts} · NSE · BACKTEST MODE</div>
    </div>""", unsafe_allow_html=True)

    with st.form("qt"):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        symbol = c1.text_input("NSE Symbol", value="RELIANCE", label_visibility="collapsed",
                               placeholder="NSE symbol e.g. RELIANCE")
        cost_bps = c2.number_input("Friction (bps)", value=15.0, min_value=0.0, step=1.0)
        ptsl = c3.selectbox("Barrier σ", ["2.0 / 1.5", "1.5 / 1.0", "3.0 / 2.0"])
        horizon = c4.selectbox("Horizon (d)", HORIZONS, index=0)
        run = st.form_submit_button("RUN", type="primary", use_container_width=True)

    if not run:
        st.info("Enter an NSE symbol and press RUN. Hover any ⓘ to learn what a metric means.")
        return
    if not symbol.strip():
        st.error("Enter a symbol."); return
    pt, sl = [float(x) for x in ptsl.split("/")]

    with st.spinner("Running institutional pipeline..."):
        df, sym, info = _hist(symbol)
        if df.empty or len(df) < 400:
            st.error(f"Insufficient data for {sym} (need ~2y+)."); return
        close = df["Close"]; price = float(close.iloc[-1])
        proxies = _proxy(); bench = proxies.get("MKT", pd.Series(dtype=float))

        stat = stationarity_analysis(close)
        sigma, vol_model, fwd = garch_vol(close, horizon)
        vol = volatility_regime(sigma)
        fac = factor_battery(close, bench)
        factor = factor_attribution(close, proxies)
        fund = fundamentals(info)
        hist = historical_var_es(close, horizon); mc = monte_carlo_var(close, horizon)
        stress = stress_scenarios(price, fac["beta"])
        bt_long = triple_barrier_nonoverlap(close, sigma, 1, pt, sl, horizon, cost_bps)
        bt_short = triple_barrier_nonoverlap(close, sigma, -1, pt, sl, horizon, cost_bps)
        verdict, side, dom = disposition(bt_long, bt_short)
        ex_side = side if side != 0 else 1
        ex = execution_matrix(price, vol["sigma_daily"], ex_side, pt, sl, cost_bps)
        wf = purged_walk_forward(close, sigma, ex_side, pt, sl, horizon, cost_bps)
        size = position_sizing(dom, vol["sigma_annual"])
        scan_rows, scan_best = horizon_scan(close, pt, sl, cost_bps)

    vcolor = {"LONG": GREEN, "SHORT": RED, "NO EDGE": ACCENT}[verdict]
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{PANEL2},{PANEL});border:1px solid {BORDER};
    border-left:4px solid {vcolor};border-radius:8px;padding:16px 20px;margin:6px 0 12px;
    display:flex;justify-content:space-between;align-items:center;'>
      <div>
        <div style='font-family:{MONO};font-size:12px;color:{MUTE};letter-spacing:1px;'>{sym} · {fund['name']}</div>
        <div style='font-size:11px;color:{MUTE};'>{fund['sector']} · {fund['industry']}</div>
        <div style='font-family:{MONO};font-size:24px;font-weight:700;color:{IVORY};margin-top:5px;'>Rs {price:,.2f}</div>
      </div>
      <div style='text-align:right;'>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1px;'>DISPOSITION · DEFLATED SHARPE GATED</div>
        <div style='font-family:{MONO};font-size:30px;font-weight:700;color:{vcolor};line-height:1.1;'>{verdict}</div>
        <div style='font-family:{MONO};font-size:13px;color:{IVORY};'>DSR {dom['dsr']:.2f} · OOS-DSR {_fmt(wf['oos_dsr'],"",2)}</div>
      </div>
    </div>""", unsafe_allow_html=True)

    tabs = st.tabs(["OVERVIEW", "FACTOR ATTRIBUTION", "RISK", "BACKTEST / SKILL", "STATIONARITY", "RESEARCH"])

    with tabs[0]:
        st.markdown(_sec("EXECUTION MATRIX"), unsafe_allow_html=True)
        st.markdown(_grid([("Entry", f"{ex['entry']:,.2f}", IVORY), ("Target", f"{ex['target']:,.2f}", GREEN),
                           ("Stop", f"{ex['stop']:,.2f}", RED), ("R:R", f"{ex['rr']} : 1", BLUE)]), unsafe_allow_html=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown(_grid([("Suggested Weight", f"{size['weight']*100:.1f}%", ACCENT),
                           ("Turnover", f"{dom['turnover']:.0f}/yr", IVORY),
                           ("Break-even bps", f"{dom['breakeven_bps']:.0f}", GREEN if dom['breakeven_bps'] > cost_bps else RED),
                           ("Vol Regime", vol['regime'], ACCENT)]), unsafe_allow_html=True)
        st.markdown(_sec("RETURN / RISK BATTERY"), unsafe_allow_html=True)
        st.markdown(_grid([("Ann Return", _fmt(fac['ann_ret'], "%", 1), GREEN if fac['ann_ret'] > 0 else RED),
                           ("Ann Vol", _fmt(fac['ann_vol'], "%", 1), IVORY), ("Sharpe", _fmt(fac['sharpe'], "", 2), IVORY),
                           ("Sortino", _fmt(fac['sortino'], "", 2), IVORY), ("Max DD", _fmt(fac['max_dd'], "%", 1), RED)]), unsafe_allow_html=True)
        st.caption("Hover any ⓘ for a plain-English definition and how desks use it.")

    with tabs[1]:
        st.markdown(_sec("FACTOR REGRESSION · NEWEY-WEST t-STATS · PROXY-BASED"), unsafe_allow_html=True)
        st.caption("India factor proxies from Yahoo indices (MKT=^NSEI, SIZE=MID−LARGE, MOM=market momentum). "
                   "Proxies, not licensed Barra/Fama-French data.")
        betas = factor.get("betas", {}); ts_ = factor.get("tstats", {})
        if betas:
            cells = [(k, f"{_fmt(v,'',2)} (t={_fmt(ts_.get(k),'',1)})", BLUE if v >= 0 else RED) for k, v in betas.items()]
            cells += [("Factor Alpha", _fmt(factor['alpha_ann'], "%/yr", 1), GREEN if (factor['alpha_ann'] or 0) > 0 else RED),
                      ("R²", _fmt(factor['r2'], "", 2), IVORY)]
            st.markdown(_grid(cells), unsafe_allow_html=True)
        else:
            st.warning("Insufficient overlapping data for factor regression.")
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown(_grid([("Beta", _fmt(fac['beta'], "", 2), IVORY), ("CAPM Alpha", _fmt(fac['alpha'], "%/yr", 1), GREEN if (fac['alpha'] or 0) > 0 else RED),
                           ("Skew", _fmt(fac['skew'], "", 2), IVORY), ("Kurtosis", _fmt(fac['kurt'], "", 2), IVORY)]), unsafe_allow_html=True)

    with tabs[2]:
        st.markdown(_sec(f"VaR / EXPECTED SHORTFALL · {horizon}D · 95%"), unsafe_allow_html=True)
        st.markdown(_grid([("VaR 95%", _fmt(hist['var'], "%", 2), RED), ("Expected Shortfall", _fmt(hist['es'], "%", 2), RED),
                           ("Monte Carlo VaR", _fmt(mc['var'], "%", 2), RED), ("Expected Shortfall ", _fmt(mc['es'], "%", 2), RED),
                           ("t df", _fmt(mc['t_df'], "", 1), MUTE)]), unsafe_allow_html=True)
        st.markdown(_sec("STRESS SCENARIOS · BETA-PROPAGATED"), unsafe_allow_html=True)
        st.markdown(_grid([(n, f"{v['pct']:+.1f}% → {v['price']:,.0f}", RED if v['pct'] < 0 else GREEN) for n, v in stress.items()], cols=2), unsafe_allow_html=True)
        st.markdown(_sec(f"CONDITIONAL VOLATILITY · {vol_model}"), unsafe_allow_html=True)
        st.markdown(_grid([("GARCH σ", _fmt(vol['sigma_daily']*100, "% d", 2), IVORY), ("σ annual", _fmt(vol['sigma_annual']*100, "%", 1), IVORY),
                           (f"σ {horizon}d fwd", _fmt(fwd*100, "%", 2), ACCENT), ("Percentile", _fmt(vol['percentile'], "th", 0), BLUE)]), unsafe_allow_html=True)
        st.line_chart((sigma.tail(250) * np.sqrt(TRADING_DAYS) * 100).rename("Annualized σ %"))

    with tabs[3]:
        st.markdown(_sec("IN-SAMPLE SKILL"), unsafe_allow_html=True)
        lo, hi = dom['exp_ci']
        st.markdown(_grid([("Expectancy/trade", f"{dom['expectancy']*100:+.2f}%", GREEN if dom['expectancy'] > 0 else RED),
                           ("Expectancy CI", f"{lo*100:+.2f}..{hi*100:+.2f}%", IVORY),
                           ("Prob Sharpe", _fmt(dom['psr'], "", 2), IVORY),
                           ("Deflated Sharpe", _fmt(dom['dsr'], "", 2), GREEN if dom['dsr'] >= 0.6 else RED),
                           ("Win Rate", f"{dom['win_rate']:.0f}% (n={dom['n']})", IVORY)]), unsafe_allow_html=True)
        st.markdown(_sec("OUT-OF-SAMPLE · PURGED WALK-FORWARD"), unsafe_allow_html=True)
        st.markdown(_grid([("OOS Deflated Sharpe", _fmt(wf['oos_dsr'], "", 2) if wf['oos_n'] else "—", GREEN if (wf['oos_dsr'] or 0) >= 0.6 else RED),
                           ("OOS Expectancy", _fmt((wf['oos_expectancy'] or 0)*100, "%", 2) if wf['oos_n'] else "—", GREEN if (wf['oos_expectancy'] or 0) > 0 else RED),
                           ("OOS Win", _fmt(wf['oos_win_rate'], "%", 0) if wf['oos_n'] else "—", IVORY),
                           ("OOS n", f"{wf['oos_n']}", MUTE)]), unsafe_allow_html=True)
        st.markdown(_sec("ALPHA-DECAY · MULTI-HORIZON OOS SCAN"), unsafe_allow_html=True)
        sdf = pd.DataFrame(scan_rows)
        if not sdf.empty:
            sdf = sdf.assign(oos_dsr=sdf["oos_dsr"].round(2),
                             oos_exp=(sdf["oos_exp"].astype(float) * 100).round(2))
            sdf.columns = ["Horizon(d)", "Side", "OOS-DSR", "OOS-Exp %", "OOS n"]
            st.dataframe(sdf, use_container_width=True, hide_index=True)
        if scan_best:
            st.success(f"Best horizon by OOS Deflated Sharpe: **{scan_best['horizon']}d {scan_best['side']}** "
                       f"(OOS-DSR {scan_best['oos_dsr']:.2f}).")
        if dom['dsr'] < 0.60:
            st.warning("Deflated Sharpe < 0.60 → no statistically reliable edge after multiple-testing.")

    with tabs[4]:
        st.markdown(_sec("FRACTIONAL DIFFERENTIATION · AFML CH.5"), unsafe_allow_html=True)
        st.markdown(_grid([("Frac-diff d", _fmt(stat['d'], "", 2), BLUE),
                           ("ADF p-value", _fmt(stat['adf_pvalue'], "", 4), GREEN if (stat['adf_pvalue'] or 1) < 0.05 else RED),
                           ("Memory Retained", _fmt(stat['memory_retained'], "", 3), GREEN),
                           ("Memory Gain", _fmt(stat['memory_gain'], "", 3), BLUE)]), unsafe_allow_html=True)
        st.caption("Lowest d achieving ADF p<0.05 keeps the most memory while making the series stationary — "
                   "so ML/stat models work without discarding predictive structure.")

    with tabs[5]:
        st.markdown(_sec("QUANT STRATEGIST NOTE · EXPLAINS EACH METRIC"), unsafe_allow_html=True)
        try:
            api_key = st.secrets.get("GEMINI_KEY", "")
        except Exception:
            api_key = ""
        if not api_key:
            st.warning("GEMINI_KEY not in secrets — AI note disabled.")
        else:
            with st.spinner("Generating note..."):
                try:
                    note = gemini_research_note(sym, fund, fac, vol, stat, ex, dom, wf, size,
                                                factor, mc, hist, stress, scan_best, verdict, api_key)
                    st.markdown(f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
                                f"padding:16px 20px;color:{IVORY};line-height:1.7;font-size:13.5px;'>{note}</div>",
                                unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Note failed: {e}")
        with st.expander("Metric glossary — what each term means & how desks use it"):
            for k, v in GLOSSARY.items():
                st.markdown(f"**{k}** — {v}")
        st.caption("AI summarizes computed numbers only; it adds no independent analysis or advice.")

    st.markdown(f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-top:14px;'>"
                "Backtesting / educational tool · Not investment advice · Statistical edge does not "
                "guarantee future returns · Validate against your own execution costs</div>", unsafe_allow_html=True)
