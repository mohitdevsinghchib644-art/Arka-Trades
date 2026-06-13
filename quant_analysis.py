"""
quant_analysis.py — Institutional Single-Asset Quant Terminal (PART 1 of 2)
===========================================================================
Paste Part 1 then Part 2 into the SAME file, in order.

Methodology (institutional only — no retail TA):
  - AFML (López de Prado): fractional differentiation (Ch.5),
    non-overlapping triple-barrier (Ch.3), purged walk-forward (Ch.7),
    Probabilistic & Deflated Sharpe (Ch.8).
  - GARCH(1,1) conditional volatility (arch lib; EWMA fallback).
  - Factor attribution vs market/size/momentum proxy indices.
  - Monte Carlo & historical VaR / Expected Shortfall, stress scenarios.
  - Vol-target + fractional-Kelly sizing.

Deps: pandas, numpy, scipy, statsmodels, yfinance, streamlit
      optional: arch (GARCH), google-generativeai (AI note)
"""

import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats as scistats
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

# ── Professional terminal palette ──
BG      = "#0B0E13"
PANEL   = "#11151D"
PANEL2  = "#161C26"
BORDER  = "#222B38"
ACCENT  = "#C9A227"   # muted gold, not loud amber
IVORY   = "#E8EDF2"
MUTE    = "#8593A3"
GREEN   = "#1FB97A"
RED     = "#E8554E"
BLUE    = "#4C8DD6"
MONO    = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"
TRADING_DAYS = 252
HORIZON = 5

# Factor proxy universe available on Yahoo (no bulk download needed)
FACTOR_PROXIES = {
    "MKT": "^NSEI",        # market
    "LARGE": "^CNX100",    # large-cap tilt
    "MID": "^CNXMIDCAP",   # size (mid vs large) proxy
}


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
        s = yf.Ticker(yahoo_symbol).history(period=period, interval="1d")["Close"].dropna()
        return s
    except Exception:
        return pd.Series(dtype=float)


def fetch_factor_proxies(period: str = "5y"):
    out = {}
    for k, sym in FACTOR_PROXIES.items():
        out[k] = fetch_series(sym, period)
    return out


# ════════════════════════════════════════════════════════════
# 1. FRACTIONAL DIFFERENTIATION (AFML Ch.5)
# ════════════════════════════════════════════════════════════

def _ffd_weights(d, threshold=1e-4):
    w, k = [1.0], 1
    while abs(w[-1]) > threshold:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
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
# 2. GARCH CONDITIONAL VOLATILITY (+ EWMA fallback)
# ════════════════════════════════════════════════════════════

def ewma_vol(close, lam=0.94):
    r = np.log(close / close.shift(1)).dropna()
    var = np.zeros(len(r)); var[0] = r.var()
    for t in range(1, len(r)):
        var[t] = lam * var[t - 1] + (1 - lam) * r.iloc[t - 1] ** 2
    return pd.Series(np.sqrt(var), index=r.index)


def garch_vol(close):
    """GARCH(1,1) conditional vol via arch; EWMA fallback. Returns (series, model_used)."""
    r = np.log(close / close.shift(1)).dropna() * 100.0  # arch prefers %-returns
    try:
        from arch import arch_model
        am = arch_model(r, vol="Garch", p=1, q=1, mean="Constant", dist="t")
        res = am.fit(disp="off")
        cond = res.conditional_volatility / 100.0  # back to decimal daily
        sigma = pd.Series(cond.values, index=r.index)
        # 5-day-ahead forecast
        fc = res.forecast(horizon=HORIZON, reindex=False)
        fvar = fc.variance.values[-1] / (100.0 ** 2)
        fwd = float(np.sqrt(fvar.sum()))  # cumulative 5d sigma
        return sigma, "GARCH(1,1)-t", fwd
    except Exception:
        sigma = ewma_vol(close)
        fwd = float(sigma.iloc[-1] * np.sqrt(HORIZON))
        return sigma, "EWMA(0.94)", fwd


def volatility_regime(sigma):
    cur = float(sigma.iloc[-1]); hist = sigma.tail(TRADING_DAYS)
    pct = float((hist < cur).mean() * 100)
    regime = "STRESSED" if pct >= 80 else "COMPRESSED" if pct <= 20 else "NORMAL"
    return {"sigma_daily": cur, "sigma_annual": cur * np.sqrt(TRADING_DAYS),
            "percentile": pct, "regime": regime}


# ════════════════════════════════════════════════════════════
# 3. FACTOR ATTRIBUTION (vs market / size / momentum proxies)
# ════════════════════════════════════════════════════════════

def _rets(s):
    return np.log(s / s.shift(1)).dropna()


def factor_attribution(close, proxies):
    """OLS of stock excess returns on factor-proxy returns.
    SIZE = MID - LARGE (small-minus-big proxy). MOM = market 12-1 momentum proxy."""
    y = _rets(close).rename("y")
    mkt = _rets(proxies.get("MKT", pd.Series(dtype=float)))
    large = _rets(proxies.get("LARGE", pd.Series(dtype=float)))
    mid = _rets(proxies.get("MID", pd.Series(dtype=float)))

    cols = {"MKT": mkt}
    if len(large) and len(mid):
        size = (mid - large).dropna().rename("SIZE")
        cols["SIZE"] = size
    # momentum proxy: trailing 60d market momentum sign-scaled return
    if len(mkt):
        mom = mkt.rolling(60).mean().dropna().rename("MOM")
        cols["MOM"] = mom

    data = pd.concat([y] + list(cols.values()), axis=1, join="inner").dropna()
    if len(data) < 60:
        return {"betas": {}, "alpha_ann": np.nan, "r2": np.nan, "n": len(data)}
    Y = data["y"].values
    X = data.drop(columns=["y"]).values
    X = np.column_stack([np.ones(len(X)), X])
    try:
        coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    except Exception:
        return {"betas": {}, "alpha_ann": np.nan, "r2": np.nan, "n": len(data)}
    resid = Y - X @ coef
    ss_res = float((resid ** 2).sum()); ss_tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    names = ["ALPHA"] + list(data.drop(columns=["y"]).columns)
    betas = {n: float(c) for n, c in zip(names, coef)}
    alpha_ann = betas.pop("ALPHA") * TRADING_DAYS * 100
    return {"betas": betas, "alpha_ann": alpha_ann, "r2": float(r2), "n": int(len(data))}


# ════════════════════════════════════════════════════════════
# 4. FACTOR BATTERY
# ════════════════════════════════════════════════════════════

def hurst_exponent(ts, max_lag=40):
    ts = np.asarray(ts, dtype=float)
    lags = range(2, min(max_lag, len(ts) // 2))
    tau = [np.std(ts[lag:] - ts[:-lag]) for lag in lags]
    tau = [t if t > 0 else 1e-10 for t in tau]
    return float(np.polyfit(np.log(list(lags)), np.log(tau), 1)[0])


def factor_battery(close, bench):
    r = _rets(close)
    ann_ret = float(r.mean() * TRADING_DAYS)
    ann_vol = float(r.std() * np.sqrt(TRADING_DAYS))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    downside = r[r < 0].std() * np.sqrt(TRADING_DAYS)
    sortino = ann_ret / downside if downside > 0 else 0.0
    eq = (1 + r).cumprod()
    mdd = float((eq / eq.cummax() - 1).min() * 100)
    var95 = float(np.percentile(r, 5) * 100)
    cvar95 = float(r[r <= np.percentile(r, 5)].mean() * 100)
    skew = float(r.skew()); kurt = float(r.kurtosis())
    ac1 = float(r.autocorr(1)) if len(r) > 2 else 0.0
    hurst = hurst_exponent(np.log(close.dropna().values))
    beta = alpha = np.nan
    if len(bench) > 10:
        br = _rets(bench)
        j = pd.concat([r, br], axis=1, join="inner").dropna(); j.columns = ["a", "b"]
        if len(j) > 30 and j["b"].var() > 0:
            beta = float(np.cov(j["a"], j["b"])[0, 1] / j["b"].var())
            alpha = float((j["a"].mean() - beta * j["b"].mean()) * TRADING_DAYS)
    return {"ann_ret": ann_ret * 100, "ann_vol": ann_vol * 100, "sharpe": sharpe,
            "sortino": sortino, "max_dd": mdd, "var95": var95, "cvar95": cvar95,
            "skew": skew, "kurt": kurt, "autocorr1": ac1, "hurst": hurst,
            "beta": beta, "alpha": (alpha * 100) if np.isfinite(alpha) else np.nan,
            "daily_mean": float(r.mean()), "daily_std": float(r.std())}


def fundamentals(info):
    def g(k):
        v = info.get(k)
        return v if isinstance(v, (int, float)) and np.isfinite(v) else None
    mc = g("marketCap")
    return {"name": info.get("longName") or info.get("shortName") or "—",
            "sector": info.get("sector") or "—", "industry": info.get("industry") or "—",
            "mcap_cr": (mc / 1e7) if mc else None,
            "pe": g("trailingPE"), "fwd_pe": g("forwardPE"), "pb": g("priceToBook"),
            "roe": (g("returnOnEquity") or 0) * 100 if g("returnOnEquity") is not None else None,
            "profit_margin": (g("profitMargins") or 0) * 100 if g("profitMargins") is not None else None,
            "div_yield": (g("dividendYield") or 0) * 100 if g("dividendYield") is not None else None,
            "wk52_high": g("fiftyTwoWeekHigh"), "wk52_low": g("fiftyTwoWeekLow"),
            "beta_info": g("beta")}


# ════════════════════════════════════════════════════════════
# 5. RISK: MONTE CARLO + HISTORICAL VaR/ES + STRESS
# ════════════════════════════════════════════════════════════

def historical_var_es(close, horizon=HORIZON, alpha=0.05):
    r = _rets(close)
    hz = r.rolling(horizon).sum().dropna()  # overlapping h-day log returns
    var = float(np.percentile(hz, alpha * 100) * 100)
    es = float(hz[hz <= np.percentile(hz, alpha * 100)].mean() * 100)
    return {"var": var, "es": es}


def monte_carlo_var(close, horizon=HORIZON, n_sims=20000, alpha=0.05, seed=7):
    r = _rets(close)
    mu, sd = r.mean(), r.std()
    # Student-t innovations to capture fat tails (df estimated by kurtosis)
    k = max(4.5, 6.0 / max(scistats.kurtosis(r, fisher=True), 1e-3) + 4)
    rng = np.random.default_rng(seed)
    z = scistats.t.rvs(df=k, size=(n_sims, horizon), random_state=rng)
    z = z / np.sqrt(k / (k - 2))  # unit variance
    sims = (mu + sd * z).sum(axis=1)
    var = float(np.percentile(sims, alpha * 100) * 100)
    es = float(sims[sims <= np.percentile(sims, alpha * 100)].mean() * 100)
    return {"var": var, "es": es, "t_df": float(k)}


def stress_scenarios(price, beta):
    """Apply historical market shocks through the stock's beta."""
    if not np.isfinite(beta):
        beta = 1.0
    shocks = {"GFC 2008 (-50% mkt)": -0.50, "COVID Mar-2020 (-38%)": -0.38,
              "Rate Shock (-12%)": -0.12, "Flash Crash (-8%)": -0.08}
    out = {}
    for name, mshock in shocks.items():
        stock_move = beta * mshock
        out[name] = {"pct": stock_move * 100, "price": price * (1 + stock_move)}
    return out


# ════════════════════════════════════════════════════════════
# 6. SKILL STATISTICS (AFML Ch.8)
# ════════════════════════════════════════════════════════════

def probabilistic_sharpe_ratio(returns, sr_benchmark=0.0):
    r = np.asarray(returns, dtype=float); n = len(r)
    if n < 8 or r.std(ddof=1) == 0:
        return 0.5, 0.0
    sr = r.mean() / r.std(ddof=1)
    g3 = scistats.skew(r); g4 = scistats.kurtosis(r, fisher=True) + 3.0
    denom = np.sqrt(1 - g3 * sr + ((g4 - 1) / 4) * sr ** 2)
    if denom <= 0:
        return 0.5, float(sr)
    psr = scistats.norm.cdf((sr - sr_benchmark) * np.sqrt(n - 1) / denom)
    return float(psr), float(sr)


def deflated_sharpe(returns, n_trials=2):
    r = np.asarray(returns, dtype=float); n = len(r)
    if n < 8 or r.std(ddof=1) == 0 or n_trials < 1:
        return 0.5
    sr_var = 1.0 / n; emc = 0.5772156649
    e_max = np.sqrt(sr_var) * ((1 - emc) * scistats.norm.ppf(1 - 1.0 / n_trials)
                               + emc * scistats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    dsr, _ = probabilistic_sharpe_ratio(r, sr_benchmark=e_max)
    return float(dsr)


# ════════════════════════════════════════════════════════════
# 7. TRIPLE-BARRIER (look-ahead-free, non-overlapping) + PURGED WF
# ════════════════════════════════════════════════════════════

def _barrier_outcome(px, i, sig_i, side, pt, sl, vbar, cost):
    p0 = px.iloc[i]
    up = p0 * (1 + side * pt * sig_i); dn = p0 * (1 + side * -sl * sig_i)
    path = px.iloc[i + 1: i + 1 + vbar]
    for p in path:
        if side == 1:
            if p >= up: return pt * sig_i - cost
            if p <= dn: return -sl * sig_i - cost
        else:
            if p <= up: return pt * sig_i - cost
            if p >= dn: return -sl * sig_i - cost
    return side * (path.iloc[-1] / p0 - 1) - cost


def triple_barrier_nonoverlap(close, sigma, side=1, pt=2.0, sl=1.5, vbar=HORIZON, cost_bps=10.0):
    px = close.reindex(sigma.index).dropna(); sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4; rets = []; i = 0
    while i < len(px) - vbar:
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            i += 1; continue
        rets.append(_barrier_outcome(px, i, s, side, pt, sl, vbar, cost)); i += vbar
    rets = np.array(rets)
    if len(rets) == 0:
        return {"expectancy": 0.0, "psr": 0.5, "dsr": 0.5, "sr_period": 0.0,
                "win_rate": 0.0, "n": 0, "rets": rets}
    psr, sr = probabilistic_sharpe_ratio(rets, 0.0)
    dsr = deflated_sharpe(rets, n_trials=2)
    return {"expectancy": float(rets.mean()), "psr": psr, "dsr": dsr, "sr_period": sr,
            "win_rate": float((rets > 0).mean() * 100), "n": int(len(rets)), "rets": rets}


def purged_walk_forward(close, sigma, side, pt, sl, vbar=HORIZON, cost_bps=10.0, folds=5):
    px = close.reindex(sigma.index).dropna(); sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4; n = len(px)
    if n < (folds + 1) * (vbar + 20):
        return {"oos_expectancy": np.nan, "oos_win_rate": np.nan, "oos_n": 0}
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
        return {"oos_expectancy": np.nan, "oos_win_rate": np.nan, "oos_n": 0}
    return {"oos_expectancy": float(oos.mean()), "oos_win_rate": float((oos > 0).mean() * 100),
            "oos_n": int(len(oos))}


def execution_matrix(price, sig_d, side, pt=2.0, sl=1.5, cost_bps=10.0):
    cost = price * cost_bps / 1e4
    if side >= 0:
        entry, tgt, stp = price, price * (1 + pt * sig_d) - cost, price * (1 - sl * sig_d) - cost
    else:
        entry, tgt, stp = price, price * (1 - pt * sig_d) + cost, price * (1 + sl * sig_d) + cost
    rr = abs(tgt - entry) / abs(entry - stp) if (entry - stp) != 0 else 0
    return {"entry": round(entry, 2), "target": round(tgt, 2), "stop": round(stp, 2), "rr": round(rr, 2)}


# ════════════════════════════════════════════════════════════
# 8. SIZING + COMPOSITE
# ════════════════════════════════════════════════════════════

def position_sizing(setup, sigma_annual, target_vol=0.15, kelly_fraction=0.5, max_w=1.0):
    rets = setup.get("rets", np.array([]))
    vt = min(max_w, target_vol / sigma_annual) if sigma_annual > 0 else 0.0
    kelly = 0.0
    if len(rets) > 8 and rets.var() > 0:
        kelly = float(rets.mean() / rets.var())
    kelly = max(0.0, min(max_w, kelly * kelly_fraction))
    conf = setup.get("dsr", 0.5)
    weight = min(max_w, vt * kelly * conf)
    return {"vol_target_w": round(vt, 3), "kelly_half": round(kelly, 3),
            "confidence": round(conf, 3), "weight": round(weight, 3)}


def composite_alpha(fac, vol, bt_long, bt_short, factor):
    long_dsr, short_dsr = bt_long["dsr"], bt_short["dsr"]
    if long_dsr >= short_dsr:
        dom, side_lbl, conf = bt_long, 1, long_dsr
    else:
        dom, side_lbl, conf = bt_short, -1, short_dsr
    score = 100.0 * conf
    drivers = [f"Dominant-side Deflated Sharpe {conf:.2f} (prob. edge survives multiple-testing); "
               f"PSR {dom['psr']:.2f}, win-rate {dom['win_rate']:.0f}% on n={dom['n']} non-overlapping trades"]
    if vol["regime"] == "STRESSED":
        score -= 8; drivers.append("Vol regime STRESSED — confidence trimmed")
    elif vol["regime"] == "COMPRESSED":
        score += 4; drivers.append("Vol regime COMPRESSED — favorable")
    if fac["hurst"] > 0.55:
        score += 4; drivers.append(f"Hurst {fac['hurst']:.2f} → trending")
    elif fac["hurst"] < 0.45:
        score -= 2; drivers.append(f"Hurst {fac['hurst']:.2f} → mean-reverting")
    if np.isfinite(factor.get("alpha_ann", np.nan)) and factor["alpha_ann"] > 0:
        drivers.append(f"Factor-model alpha {factor['alpha_ann']:.1f}% (R²={factor['r2']:.2f})")
    drivers.append(f"Asset Sharpe {fac['sharpe']:.2f}, Sortino {fac['sortino']:.2f}, MaxDD {fac['max_dd']:.1f}%")
    score = float(max(0, min(100, score)))
    side = side_lbl if (conf >= 0.60 and score >= 55) else 0
    return round(score, 1), side, drivers, dom

# ── END PART 1 ── (Part 2 next: GARCH-aware UI tabs + AI note + render_quant_analysis)
# ════════════════════════════════════════════════════════════
# PART 2 of 2 — PROFESSIONAL TERMINAL UI
# Paste below Part 1 in the same file.
# ════════════════════════════════════════════════════════════

# ── AI research note (summary of computed metrics ONLY) ──
def gemini_research_note(sym, fund, fac, vol, stat, ex, dom, wf, size,
                         factor, mc, hist, stress, score, verdict, api_key):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are a buy-side quant strategist. Summarize ONLY the supplied computed "
            "statistics into an institutional desk note. Do NOT invent numbers, do NOT add "
            "technical-analysis commentary, do NOT give financial advice. Emphasize statistical "
            "confidence (Deflated Sharpe) and out-of-sample evidence. If Deflated Sharpe < 0.60, "
            "state plainly there is no statistically reliable edge. Educational only; not SEBI registered."
        ),
    )

    def f(v, dp=2):
        try:
            return f"{float(v):.{dp}f}"
        except Exception:
            return "n/a"

    betas = ", ".join(f"{k} {f(v)}" for k, v in factor.get("betas", {}).items()) or "n/a"
    stress_txt = "; ".join(f"{k}: {f(v['pct'],1)}%" for k, v in stress.items())

    prompt = f"""Institutional quant note — {HORIZON}-day horizon — {sym} ({fund['name']}, {fund['sector']}).
Summarize ONLY these computed metrics.

SIGNAL
- Composite (DSR-led) score {score}/100 | Verdict {verdict}
- Triple-barrier (non-overlapping): expectancy {f(dom['expectancy']*100)}%/trade, PSR {f(dom['psr'])}, Deflated Sharpe {f(dom['dsr'])}, win {f(dom['win_rate'],0)}%, n={dom['n']}
- Purged walk-forward OOS: expectancy {f((wf['oos_expectancy'] or 0)*100)}%/trade, win {f(wf['oos_win_rate'],0)}%, n={wf['oos_n']}
- Sizing: vol-target {size['vol_target_w']}, half-Kelly {size['kelly_half']}, final weight {size['weight']}

FACTOR ATTRIBUTION
- Betas: {betas} | Factor alpha {f(factor.get('alpha_ann'),1)}%/yr | R² {f(factor.get('r2'))}

RISK
- Hist VaR {f(hist['var'])}% / ES {f(hist['es'])}% ({HORIZON}d) | MC VaR {f(mc['var'])}% / ES {f(mc['es'])}% (t df {f(mc['t_df'],1)})
- MaxDD {f(fac['max_dd'],1)}% | Beta {f(fac['beta'])} | Stress: {stress_txt}
- GARCH/EWMA regime {vol['regime']} ({f(vol['percentile'],0)}th pct), ann {f(vol['sigma_annual']*100,1)}%

STATIONARITY / QUALITY
- Frac-diff d={f(stat['d'])} (ADF p={f(stat['adf_pvalue'],4)}), memory {f(stat['memory_retained'],3)}
- Sharpe {f(fac['sharpe'])}, Sortino {f(fac['sortino'])}, Hurst {f(fac['hurst'])}, Skew {f(fac['skew'])}, Kurt {f(fac['kurt'])}

EXECUTION: entry {ex['entry']}, target {ex['target']}, stop {ex['stop']}, R:R {ex['rr']}
FUNDAMENTALS: P/E {fund['pe']}, Fwd P/E {fund['fwd_pe']}, P/B {fund['pb']}, ROE {fund['roe']}%, MCap Rs {fund['mcap_cr']} cr

STRUCTURE (bold headers, <300 words total):
**Signal Quality** — DSR/PSR + OOS; explicitly say if edge is unreliable (DSR<0.60).
**Factor Exposure** — what the betas/alpha/R² imply about return drivers.
**Risk Profile** — VaR/ES, drawdown, beta, stress, regime, suggested weight.
**Valuation Context** — fundamentals in one or two sentences.
**Disposition** — verdict + execution levels; if DSR<0.60 say NO RELIABLE EDGE."""
    return model.generate_content(prompt).text


# ── UI primitives (dense terminal grids) ──
def _grid(cells, cols=None):
    cols = cols or len(cells)
    html = (f"<div style='display:grid;grid-template-columns:repeat({cols},1fr);"
            f"gap:1px;background:{BORDER};border:1px solid {BORDER};border-radius:6px;overflow:hidden;'>")
    for label, value, color in cells:
        html += (f"<div style='background:{PANEL};padding:10px 12px;'>"
                 f"<div style='font-size:9.5px;letter-spacing:.8px;color:{MUTE};text-transform:uppercase;'>{label}</div>"
                 f"<div style='font-family:{MONO};font-size:16px;font-weight:600;color:{color};margin-top:3px;'>{value}</div></div>")
    return html + "</div>"


def _sec(title):
    return (f"<div style='font-family:{MONO};font-size:11px;font-weight:600;color:{ACCENT};"
            f"letter-spacing:1.5px;margin:16px 0 8px;border-bottom:1px solid {BORDER};padding-bottom:5px;'>{title}</div>")


def _fmt(v, suffix="", dp=2, dash="—"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return f"{v:,.{dp}f}{suffix}"


# ── Entry point ──
def render_quant_analysis():
    import streamlit as st
    from datetime import datetime, timezone

    _hist = st.cache_data(ttl=900, show_spinner=False)(fetch_history)
    _proxy = st.cache_data(ttl=900, show_spinner=False)(fetch_factor_proxies)
    _bench = st.cache_data(ttl=900, show_spinner=False)(fetch_series)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Command-bar header
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;
    padding:12px 18px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;'>
      <div style='font-family:{MONO};font-size:13px;font-weight:700;color:{ACCENT};letter-spacing:2px;'>
        ARKA · QUANT TERMINAL</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>{ts} · NSE · {HORIZON}D HORIZON</div>
    </div>""", unsafe_allow_html=True)

    with st.form("qt"):
        c1, c2, c3 = st.columns([2, 1, 1])
        symbol = c1.text_input("NSE Symbol", value="RELIANCE", label_visibility="collapsed",
                               placeholder="NSE symbol e.g. RELIANCE")
        cost_bps = c2.number_input("Friction (bps)", value=10.0, min_value=0.0, step=1.0)
        ptsl = c3.selectbox("Barrier σ (PT/SL)", ["2.0 / 1.5", "1.5 / 1.0", "3.0 / 2.0"])
        run = st.form_submit_button("RUN", type="primary", use_container_width=True)

    if not run:
        st.info("Enter an NSE symbol and press RUN.")
        return
    if not symbol.strip():
        st.error("Enter a symbol."); return

    pt, sl = [float(x) for x in ptsl.split("/")]

    with st.spinner("Running institutional pipeline..."):
        df, sym, info = _hist(symbol)
        if df.empty or len(df) < 400:
            st.error(f"Insufficient data for {sym} (need ~2y+)."); return
        close = df["Close"]; price = float(close.iloc[-1])
        proxies = _proxy()
        bench = proxies.get("MKT", _bench("^NSEI"))

        stat = stationarity_analysis(close)
        sigma, vol_model, fwd5 = garch_vol(close)
        vol = volatility_regime(sigma)
        fac = factor_battery(close, bench)
        factor = factor_attribution(close, proxies)
        fund = fundamentals(info)
        hist = historical_var_es(close)
        mc = monte_carlo_var(close)
        stress = stress_scenarios(price, fac["beta"])
        bt_long = triple_barrier_nonoverlap(close, sigma, 1, pt, sl, HORIZON, cost_bps)
        bt_short = triple_barrier_nonoverlap(close, sigma, -1, pt, sl, HORIZON, cost_bps)
        score, side, drivers, dom = composite_alpha(fac, vol, bt_long, bt_short, factor)
        verdict = "LONG" if side == 1 else "SHORT" if side == -1 else "NO EDGE"
        ex_side = side if side != 0 else 1
        ex = execution_matrix(price, vol["sigma_daily"], ex_side, pt, sl, cost_bps)
        wf = purged_walk_forward(close, sigma, ex_side, pt, sl, HORIZON, cost_bps)
        size = position_sizing(dom, vol["sigma_annual"])

    vcolor = {"LONG": GREEN, "SHORT": RED, "NO EDGE": ACCENT}[verdict]

    # Verdict banner
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
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1px;'>MODEL DISPOSITION</div>
        <div style='font-family:{MONO};font-size:30px;font-weight:700;color:{vcolor};line-height:1.1;'>{verdict}</div>
        <div style='font-family:{MONO};font-size:13px;color:{IVORY};'>Confidence {score}/100 · DSR {dom['dsr']:.2f}</div>
      </div>
    </div>""", unsafe_allow_html=True)

    tabs = st.tabs(["OVERVIEW", "FACTOR ATTRIBUTION", "RISK", "BACKTEST / SKILL",
                    "STATIONARITY", "RESEARCH"])

    # ── OVERVIEW ──
    with tabs[0]:
        st.markdown(_sec("EXECUTION MATRIX"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("Entry", f"{ex['entry']:,.2f}", IVORY),
            ("Target", f"{ex['target']:,.2f}", GREEN),
            ("Stop", f"{ex['stop']:,.2f}", RED),
            ("R:R", f"{ex['rr']} : 1", BLUE),
        ]), unsafe_allow_html=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown(_grid([
            ("Suggested Weight", f"{size['weight']*100:.1f}%", ACCENT),
            ("Vol-Target W", f"{size['vol_target_w']*100:.1f}%", IVORY),
            ("Half-Kelly", f"{size['kelly_half']*100:.1f}%", IVORY),
            ("Stat Confidence", f"{size['confidence']:.2f}", BLUE),
        ]), unsafe_allow_html=True)

        st.markdown(_sec("RETURN / RISK FACTOR BATTERY"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("Ann Return", _fmt(fac['ann_ret'], "%", 1), GREEN if fac['ann_ret'] > 0 else RED),
            ("Ann Vol", _fmt(fac['ann_vol'], "%", 1), IVORY),
            ("Sharpe", _fmt(fac['sharpe'], "", 2), IVORY),
            ("Sortino", _fmt(fac['sortino'], "", 2), IVORY),
            ("Max DD", _fmt(fac['max_dd'], "%", 1), RED),
        ]), unsafe_allow_html=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown(_grid([
            ("Hurst", _fmt(fac['hurst'], "", 2), BLUE),
            ("Skew", _fmt(fac['skew'], "", 2), IVORY),
            ("Kurtosis", _fmt(fac['kurt'], "", 2), IVORY),
            ("Autocorr(1)", _fmt(fac['autocorr1'], "", 3), IVORY),
            ("Vol Regime", f"{vol['regime']}", ACCENT),
        ]), unsafe_allow_html=True)

        st.markdown(_sec("VALUATION & FUNDAMENTALS"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("Market Cap", f"Rs {fund['mcap_cr']:,.0f} cr" if fund['mcap_cr'] else "—", IVORY),
            ("P/E", _fmt(fund['pe'], "", 1), IVORY),
            ("Fwd P/E", _fmt(fund['fwd_pe'], "", 1), IVORY),
            ("P/B", _fmt(fund['pb'], "", 2), IVORY),
            ("ROE", _fmt(fund['roe'], "%", 1), GREEN if (fund['roe'] or 0) > 15 else IVORY),
        ]), unsafe_allow_html=True)

    # ── FACTOR ATTRIBUTION ──
    with tabs[1]:
        st.markdown(_sec("FACTOR REGRESSION (PROXY-BASED)"), unsafe_allow_html=True)
        st.caption("India factor proxies from Yahoo indices. MKT=^NSEI, SIZE=MID−LARGE, "
                   "MOM=market momentum. Proxies, not licensed Barra/Fama-French data.")
        betas = factor.get("betas", {})
        if betas:
            cells = [(k, _fmt(v, "", 2), BLUE if v >= 0 else RED) for k, v in betas.items()]
            cells.append(("Factor Alpha", _fmt(factor['alpha_ann'], "%/yr", 1),
                          GREEN if (factor['alpha_ann'] or 0) > 0 else RED))
            cells.append(("R²", _fmt(factor['r2'], "", 2), IVORY))
            st.markdown(_grid(cells), unsafe_allow_html=True)
        else:
            st.warning("Insufficient overlapping data for factor regression.")
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown(_grid([
            ("Beta vs NIFTY", _fmt(fac['beta'], "", 2), IVORY),
            ("CAPM Alpha", _fmt(fac['alpha'], "%/yr", 1), GREEN if (fac['alpha'] or 0) > 0 else RED),
            ("Info Beta", _fmt(fund['beta_info'], "", 2), MUTE),
            ("Obs (n)", f"{factor.get('n', 0)}", MUTE),
        ]), unsafe_allow_html=True)

    # ── RISK ──
    with tabs[2]:
        st.markdown(_sec(f"VALUE-AT-RISK / EXPECTED SHORTFALL · {HORIZON}D · 95%"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("Hist VaR", _fmt(hist['var'], "%", 2), RED),
            ("Hist ES", _fmt(hist['es'], "%", 2), RED),
            ("MC VaR (t)", _fmt(mc['var'], "%", 2), RED),
            ("MC ES (t)", _fmt(mc['es'], "%", 2), RED),
            ("t df", _fmt(mc['t_df'], "", 1), MUTE),
        ]), unsafe_allow_html=True)
        st.markdown(_sec("STRESS SCENARIOS (BETA-PROPAGATED)"), unsafe_allow_html=True)
        scells = []
        for name, v in stress.items():
            scells.append((name, f"{v['pct']:+.1f}% → {v['price']:,.0f}", RED if v['pct'] < 0 else GREEN))
        st.markdown(_grid(scells, cols=2), unsafe_allow_html=True)
        st.markdown(_sec(f"CONDITIONAL VOLATILITY · {vol_model}"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("σ daily", _fmt(vol['sigma_daily']*100, "%", 2), IVORY),
            ("σ annual", _fmt(vol['sigma_annual']*100, "%", 1), IVORY),
            (f"σ {HORIZON}d fwd", _fmt(fwd5*100, "%", 2), ACCENT),
            ("Percentile", _fmt(vol['percentile'], "th", 0), BLUE),
            ("Regime", vol['regime'], ACCENT),
        ]), unsafe_allow_html=True)
        st.line_chart((sigma.tail(250) * np.sqrt(TRADING_DAYS) * 100).rename("Annualized σ %"))

    # ── BACKTEST / SKILL ──
    with tabs[3]:
        st.markdown(_sec("SKILL STATISTICS · IN-SAMPLE"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("Expectancy/trade", f"{dom['expectancy']*100:+.2f}%", GREEN if dom['expectancy'] > 0 else RED),
            ("Prob Sharpe", _fmt(dom['psr'], "", 2), IVORY),
            ("Deflated Sharpe", _fmt(dom['dsr'], "", 2), GREEN if dom['dsr'] >= 0.6 else RED),
            ("Win / n", f"{dom['win_rate']:.0f}% / {dom['n']}", IVORY),
        ]), unsafe_allow_html=True)
        st.markdown(_sec("PURGED WALK-FORWARD · OUT-OF-SAMPLE"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("OOS Expectancy", _fmt((wf['oos_expectancy'] or 0)*100, "%", 2) if wf['oos_n'] else "—",
             GREEN if (wf['oos_expectancy'] or 0) > 0 else RED),
            ("OOS Win", _fmt(wf['oos_win_rate'], "%", 0) if wf['oos_n'] else "—", IVORY),
            ("OOS n", f"{wf['oos_n']}", MUTE),
            ("Trials Deflated", "2 (L/S)", MUTE),
        ]), unsafe_allow_html=True)
        if dom['dsr'] < 0.60:
            st.warning("Deflated Sharpe < 0.60 → no statistically reliable edge after multiple-testing. "
                       "Disposition defaults to NO EDGE.")
        with st.expander("Composite score — model drivers"):
            for d in drivers:
                st.markdown(f"- {d}")

    # ── STATIONARITY ──
    with tabs[4]:
        st.markdown(_sec("FRACTIONAL DIFFERENTIATION (AFML CH.5)"), unsafe_allow_html=True)
        st.markdown(_grid([
            ("Optimal d", _fmt(stat['d'], "", 2), BLUE),
            ("ADF p-value", _fmt(stat['adf_pvalue'], "", 4), GREEN if (stat['adf_pvalue'] or 1) < 0.05 else RED),
            ("Memory Retained", _fmt(stat['memory_retained'], "", 3), GREEN),
            ("Memory vs 1st-diff", _fmt(stat['memory_gain'], "", 3), BLUE),
        ]), unsafe_allow_html=True)
        st.caption("Lower d that achieves ADF p<0.05 keeps more memory while making the series stationary.")

    # ── RESEARCH ──
    with tabs[5]:
        st.markdown(_sec("QUANT STRATEGIST NOTE (SUMMARY OF COMPUTED METRICS)"), unsafe_allow_html=True)
        api_key = ""
        try:
            api_key = st.secrets.get("GEMINI_KEY", "")
        except Exception:
            api_key = ""
        if not api_key:
            st.warning("GEMINI_KEY not in secrets — AI summary disabled.")
        else:
            with st.spinner("Generating note..."):
                try:
                    note = gemini_research_note(sym, fund, fac, vol, stat, ex, dom, wf, size,
                                                factor, mc, hist, stress, score, verdict, api_key)
                    st.markdown(f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
                                f"padding:16px 20px;color:{IVORY};line-height:1.7;font-size:13.5px;'>{note}</div>",
                                unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Note failed: {e}")
        st.caption("AI summarizes computed numbers only; it adds no independent analysis or advice.")

    st.markdown(f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-top:14px;'>"
                "Educational use only · Not investment advice · Statistical edge does not guarantee "
                "future returns · Trading risks capital loss</div>", unsafe_allow_html=True)
