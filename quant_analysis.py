"""
quant_analysis.py — Institutional Quant Terminal (5-day swing horizon)
======================================================================
Methodology grounded in:
  - López de Prado (AFML): fractional differentiation (Ch.5),
    triple-barrier labeling (Ch.3), purged walk-forward CV (Ch.7),
    Probabilistic & Deflated Sharpe (Ch.8).
  - Ernest Chan: vol-scaled barriers, bps cost model, Kelly sizing.
  - Standard desk factor battery: beta/alpha vs NIFTY, Sharpe, Sortino,
    max drawdown, VaR/CVaR (95%), Hurst, skew/kurtosis, drift & vol.
  - EWMA conditional variance regime + Volume-Profile S/R.

UI entry point: render_quant_analysis()
Deps: pandas, numpy, scipy, statsmodels, yfinance, google-generativeai, streamlit
"""

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats as scistats
from statsmodels.tsa.stattools import adfuller

# ── Terminal palette ──
BG, PANEL, PANEL2, BORDER = "#0A0E14", "#0F141C", "#141B26", "#1F2A38"
AMBER, IVORY, MUTE = "#FFA600", "#E6EDF3", "#7D8DA0"
GREEN, RED, BLUE = "#26D07C", "#FF4D4D", "#3DA5FF"
MONO = "'JetBrains Mono','SF Mono',monospace"
TRADING_DAYS = 252
HORIZON = 5  # business-day swing horizon


# ════════════════════════════════════════════════════════════
# DATA  (cached to avoid rate limits)
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


def fetch_benchmark(period: str = "5y"):
    try:
        return yf.Ticker("^NSEI").history(period=period, interval="1d")["Close"].dropna()
    except Exception:
        return pd.Series(dtype=float)


# ════════════════════════════════════════════════════════════
# 1. FRACTIONAL DIFFERENTIATION  (AFML Ch.5)
# ════════════════════════════════════════════════════════════

def _ffd_weights(d, threshold=1e-4):
    w, k = [1.0], 1
    while abs(w[-1]) > threshold:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
    return np.array(w[::-1])


def frac_diff_ffd(series, d):
    w = _ffd_weights(d)
    width = len(w)
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
# 2. VOLATILITY REGIME + VOLUME PROFILE
# ════════════════════════════════════════════════════════════

def ewma_vol(close, lam=0.94):
    """Causal EWMA daily vol: each value uses only past returns (no look-ahead)."""
    r = np.log(close / close.shift(1)).dropna()
    var = np.zeros(len(r))
    var[0] = r.var()
    for t in range(1, len(r)):
        var[t] = lam * var[t - 1] + (1 - lam) * r.iloc[t - 1] ** 2
    return pd.Series(np.sqrt(var), index=r.index)


def volatility_regime(sigma):
    cur = float(sigma.iloc[-1]); hist = sigma.tail(TRADING_DAYS)
    pct = float((hist < cur).mean() * 100)
    regime = "HIGH / STRESSED" if pct >= 80 else "LOW / COMPRESSED" if pct <= 20 else "NORMAL"
    return {"sigma_daily": cur, "sigma_annual": cur * np.sqrt(TRADING_DAYS),
            "percentile": pct, "regime": regime}


def volume_profile_levels(df, lookback=120, bins=30):
    recent = df.tail(lookback)
    price = float(recent["Close"].iloc[-1])
    typ = (recent["High"] + recent["Low"] + recent["Close"]) / 3
    lo, hi = typ.min(), typ.max()
    edges = np.linspace(lo, hi, bins + 1); centers = (edges[:-1] + edges[1:]) / 2
    vol = np.zeros(bins)
    idx = np.clip(np.digitize(typ, edges) - 1, 0, bins - 1)
    for i, v in zip(idx, recent["Volume"].values):
        vol[i] += v
    poc = float(centers[int(np.argmax(vol))])
    thr = np.quantile(vol[vol > 0], 0.6) if (vol > 0).any() else 0
    nodes = centers[vol >= thr]
    res = sorted([n for n in nodes if n > price]); sup = sorted([n for n in nodes if n < price], reverse=True)
    return {"poc": poc, "support": float(sup[0]) if sup else float(lo),
            "resistance": float(res[0]) if res else float(hi)}


# ════════════════════════════════════════════════════════════
# 3. FACTOR BATTERY
# ════════════════════════════════════════════════════════════

def hurst_exponent(ts, max_lag=40):
    ts = np.asarray(ts, dtype=float)
    lags = range(2, min(max_lag, len(ts) // 2))
    tau = [np.std(ts[lag:] - ts[:-lag]) for lag in lags]
    tau = [t if t > 0 else 1e-10 for t in tau]
    return float(np.polyfit(np.log(list(lags)), np.log(tau), 1)[0])


def factor_battery(close, bench):
    r = np.log(close / close.shift(1)).dropna()
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
        br = np.log(bench / bench.shift(1)).dropna()
        j = pd.concat([r, br], axis=1, join="inner").dropna()
        j.columns = ["a", "b"]
        if len(j) > 30 and j["b"].var() > 0:
            beta = float(np.cov(j["a"], j["b"])[0, 1] / j["b"].var())
            alpha = float((j["a"].mean() - beta * j["b"].mean()) * TRADING_DAYS)
    return {"ann_ret": ann_ret * 100, "ann_vol": ann_vol * 100, "sharpe": sharpe,
            "sortino": sortino, "max_dd": mdd, "var95": var95, "cvar95": cvar95,
            "skew": skew, "kurt": kurt, "autocorr1": ac1, "hurst": hurst,
            "beta": beta, "alpha": (alpha * 100) if np.isfinite(alpha) else np.nan,
            "daily_ret_mean": float(r.mean()), "daily_ret_std": float(r.std())}


def fundamentals(info):
    def g(k):
        v = info.get(k)
        return v if isinstance(v, (int, float)) and np.isfinite(v) else None
    mc = g("marketCap")
    return {
        "name": info.get("longName") or info.get("shortName") or "—",
        "sector": info.get("sector") or "—",
        "industry": info.get("industry") or "—",
        "mcap_cr": (mc / 1e7) if mc else None,
        "pe": g("trailingPE"), "fwd_pe": g("forwardPE"), "pb": g("priceToBook"),
        "roe": (g("returnOnEquity") or 0) * 100 if g("returnOnEquity") is not None else None,
        "profit_margin": (g("profitMargins") or 0) * 100 if g("profitMargins") is not None else None,
        "div_yield": (g("dividendYield") or 0) * 100 if g("dividendYield") is not None else None,
        "wk52_high": g("fiftyTwoWeekHigh"), "wk52_low": g("fiftyTwoWeekLow"),
        "beta_info": g("beta"),
    }


# ════════════════════════════════════════════════════════════
# 4. STATISTICS OF SKILL  (AFML Ch.8)
# ════════════════════════════════════════════════════════════

def probabilistic_sharpe_ratio(returns, sr_benchmark=0.0):
    """PSR: probability the observed (per-period) Sharpe exceeds a benchmark,
    correcting for skew/kurtosis and sample size. Returns prob in [0,1]."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
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
    """Deflate PSR for multiple testing (e.g., long vs short = 2 trials).
    Benchmark SR rises with the number of independent trials."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 8 or r.std(ddof=1) == 0 or n_trials < 1:
        return 0.5
    # expected max Sharpe of N trials under the null (variance of trial SRs ~ 1/n)
    sr_var = 1.0 / n
    emc = 0.5772156649
    e_max = np.sqrt(sr_var) * ((1 - emc) * scistats.norm.ppf(1 - 1.0 / n_trials)
                               + emc * scistats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    dsr, _ = probabilistic_sharpe_ratio(r, sr_benchmark=e_max)
    return float(dsr)


# ════════════════════════════════════════════════════════════
# 5. TRIPLE-BARRIER  (look-ahead-free, non-overlapping, purged WF)
# ════════════════════════════════════════════════════════════

def _barrier_outcome(px, i, sig_i, side, pt, sl, vbar, cost):
    """Outcome of one trade opened at bar i. sig_i must be known at time i."""
    p0 = px.iloc[i]
    up = p0 * (1 + side * pt * sig_i)
    dn = p0 * (1 + side * -sl * sig_i)
    path = px.iloc[i + 1: i + 1 + vbar]
    for p in path:
        if side == 1:
            if p >= up: return pt * sig_i - cost
            if p <= dn: return -sl * sig_i - cost
        else:
            if p <= up: return pt * sig_i - cost
            if p >= dn: return -sl * sig_i - cost
    return side * (path.iloc[-1] / p0 - 1) - cost


def triple_barrier_nonoverlap(close, sigma, side=1, pt=2.0, sl=1.5,
                              vbar=HORIZON, cost_bps=10.0):
    """Non-overlapping samples: after each trade we jump forward vbar bars.
    sigma is causal, so sig at bar i uses only data up to i (no leak)."""
    px = close.reindex(sigma.index).dropna()
    sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4
    rets = []
    i = 0
    while i < len(px) - vbar:
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            i += 1; continue
        rets.append(_barrier_outcome(px, i, s, side, pt, sl, vbar, cost))
        i += vbar  # non-overlapping -> independent observations
    rets = np.array(rets)
    if len(rets) == 0:
        return {"expectancy": 0.0, "psr": 0.5, "dsr": 0.5, "sr_period": 0.0,
                "win_rate": 0.0, "n": 0, "rets": rets}
    psr, sr = probabilistic_sharpe_ratio(rets, 0.0)
    dsr = deflated_sharpe(rets, n_trials=2)  # long & short both tested
    return {"expectancy": float(rets.mean()), "psr": psr, "dsr": dsr,
            "sr_period": sr, "win_rate": float((rets > 0).mean() * 100),
            "n": int(len(rets)), "rets": rets}


def purged_walk_forward(close, sigma, side, pt, sl, vbar=HORIZON,
                        cost_bps=10.0, folds=5):
    """Walk-forward out-of-sample expectancy with purging of the embargo
    window between train/test so overlapping labels can't leak (AFML Ch.7)."""
    px = close.reindex(sigma.index).dropna()
    sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4
    n = len(px)
    if n < (folds + 1) * (vbar + 20):
        return {"oos_expectancy": np.nan, "oos_win_rate": np.nan, "oos_n": 0}
    fold_size = n // (folds + 1)
    oos = []
    for f in range(1, folds + 1):
        test_start = f * fold_size
        test_end = min((f + 1) * fold_size, n - vbar)
        i = test_start + vbar  # purge: skip first vbar bars to avoid overlap leak
        while i < test_end:
            s = float(sig.iloc[i])
            if np.isfinite(s) and s > 0:
                oos.append(_barrier_outcome(px, i, s, side, pt, sl, vbar, cost))
                i += vbar
            else:
                i += 1
    oos = np.array(oos)
    if len(oos) == 0:
        return {"oos_expectancy": np.nan, "oos_win_rate": np.nan, "oos_n": 0}
    return {"oos_expectancy": float(oos.mean()),
            "oos_win_rate": float((oos > 0).mean() * 100), "oos_n": int(len(oos))}


def execution_matrix(price, sig_d, side, pt=2.0, sl=1.5, cost_bps=10.0):
    cost = price * cost_bps / 1e4
    if side >= 0:
        entry, tgt, stp = price, price * (1 + pt * sig_d) - cost, price * (1 - sl * sig_d) - cost
    else:
        entry, tgt, stp = price, price * (1 - pt * sig_d) + cost, price * (1 + sl * sig_d) + cost
    rr = abs(tgt - entry) / abs(entry - stp) if (entry - stp) != 0 else 0
    return {"entry": round(entry, 2), "target": round(tgt, 2),
            "stop": round(stp, 2), "rr": round(rr, 2)}


# ════════════════════════════════════════════════════════════
# 6. POSITION SIZING  (vol-target + fractional Kelly)
# ════════════════════════════════════════════════════════════

def position_sizing(setup, sigma_annual, target_vol=0.15, kelly_fraction=0.5, max_w=1.0):
    """Vol-target weight scaled by a half-Kelly edge estimate.
    Kelly uses the setup's per-trade expectancy & variance (non-overlapping)."""
    rets = setup.get("rets", np.array([]))
    vol_target_w = min(max_w, target_vol / sigma_annual) if sigma_annual > 0 else 0.0
    kelly = 0.0
    if len(rets) > 8 and rets.var() > 0:
        kelly = float(rets.mean() / rets.var())  # f* = mu / var
    kelly = max(0.0, min(max_w, kelly * kelly_fraction))
    # final weight: blend, gated by statistical confidence (DSR)
    confidence = setup.get("dsr", 0.5)
    weight = vol_target_w * kelly * (confidence)  # all in [0, max_w]
    return {"vol_target_w": round(vol_target_w, 3), "kelly_half": round(kelly, 3),
            "confidence": round(confidence, 3), "weight": round(min(max_w, weight), 3)}


# ════════════════════════════════════════════════════════════
# 7. COMPOSITE SCORE  (driven by DSR, not arbitrary weights)
# ════════════════════════════════════════════════════════════

def composite_alpha(fac, vol, bt_long, bt_short):
    """Score is anchored on Deflated Sharpe (probability of genuine skill),
    then nudged by regime & asset quality. Range 0-100 = P(edge is real)-led."""
    long_dsr, short_dsr = bt_long["dsr"], bt_short["dsr"]
    if long_dsr >= short_dsr:
        dom, side_lbl, conf = bt_long, 1, long_dsr
    else:
        dom, side_lbl, conf = bt_short, -1, short_dsr

    score = 100.0 * conf  # DSR is already a probability of skill
    drivers = [f"Dominant side DSR={conf:.2f} (prob. edge is real after multiple-testing),"
               f" PSR={dom['psr']:.2f}, win-rate {dom['win_rate']:.0f}% on n={dom['n']} "
               f"non-overlapping trades"]

    # regime & asset-quality nudges (small, capped)
    if vol["regime"].startswith("HIGH"):
        score -= 8; drivers.append(f"Vol regime {vol['regime']} — confidence trimmed")
    elif vol["regime"].startswith("LOW"):
        score += 4; drivers.append(f"Vol regime {vol['regime']} — favorable compression")
    if fac["hurst"] > 0.55:
        score += 4; drivers.append(f"Hurst {fac['hurst']:.2f} → trending")
    elif fac["hurst"] < 0.45:
        score -= 2; drivers.append(f"Hurst {fac['hurst']:.2f} → mean-reverting")
    drivers.append(f"Asset Sharpe {fac['sharpe']:.2f}, Sortino {fac['sortino']:.2f}, "
                   f"MaxDD {fac['max_dd']:.1f}%")

    score = float(max(0, min(100, score)))
    # decision needs BOTH a directional lean AND statistical confidence
    if conf >= 0.60 and score >= 55:
        side = side_lbl
    else:
        side = 0
    return round(score, 1), side, drivers, dom


# ════════════════════════════════════════════════════════════
# 8. GEMINI RESEARCH NOTE
# ════════════════════════════════════════════════════════════

def gemini_research_note(sym, fund, fac, vol, stat, ex, dom, wf, size, score, verdict, api_key):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are a sell-side equity research analyst at an investment bank. "
            "Write crisp institutional desk notes grounded strictly in the supplied "
            "quantitative evidence. Emphasize statistical confidence (Deflated Sharpe) and "
            "out-of-sample evidence. No retail TA fluff. You are NOT SEBI registered — "
            "educational analysis only."
        ),
    )

    def fmt(v, dp=2):
        try:
            return f"{float(v):.{dp}f}"
        except Exception:
            return "n/a"

    prompt = f"""Concise institutional research note for a {HORIZON}-business-day swing on
{sym} ({fund['name']}, {fund['sector']} / {fund['industry']}). Use ONLY these metrics.

EVIDENCE
- Composite (DSR-led) score {score}/100 | Model verdict: {verdict}
- Triple-barrier (non-overlapping): expectancy {fmt(dom['expectancy']*100)}%/trade,
  PSR {fmt(dom['psr'])}, Deflated Sharpe {fmt(dom['dsr'])}, win-rate {fmt(dom['win_rate'],0)}%, n={dom['n']}
- Purged walk-forward OOS: expectancy {fmt((wf['oos_expectancy'] or 0)*100)}%/trade,
  win-rate {fmt(wf['oos_win_rate'],0)}%, n={wf['oos_n']}
- Sizing: vol-target w {size['vol_target_w']}, half-Kelly {size['kelly_half']}, final weight {size['weight']}
- Frac-diff d={fmt(stat['d'])} (ADF p={fmt(stat['adf_pvalue'],4)}), memory {fmt(stat['memory_retained'],3)}
- Ann ret {fmt(fac['ann_ret'],1)}%, vol {fmt(fac['ann_vol'],1)}%, Sharpe {fmt(fac['sharpe'])}, Sortino {fmt(fac['sortino'])}
- Beta {fmt(fac['beta'])}, Alpha {fmt(fac['alpha'],1)}%, MaxDD {fmt(fac['max_dd'],1)}%
- VaR95 {fmt(fac['var95'])}%, CVaR95 {fmt(fac['cvar95'])}%, Skew {fmt(fac['skew'])}, Kurt {fmt(fac['kurt'])}, Hurst {fmt(fac['hurst'])}
- EWMA vol regime {vol['regime']} ({fmt(vol['percentile'],0)}th pct), ann {fmt(vol['sigma_annual']*100,1)}%
- Execution: entry {ex['entry']}, target {ex['target']}, stop {ex['stop']}, R:R {ex['rr']}
- Fundamentals: P/E {fund['pe']}, Fwd P/E {fund['fwd_pe']}, P/B {fund['pb']}, ROE {fund['roe']}%, Margin {fund['profit_margin']}%, MCap Rs {fund['mcap_cr']} cr

STRUCTURE (bold these headers):
**Thesis** — directional edge + statistical basis (cite DSR & OOS explicitly).
**Statistical Confidence** — what PSR/DSR and walk-forward imply; flag if edge is weak.
**Risk Profile** — VaR/CVaR, drawdown, beta, vol regime, suggested position weight.
**Valuation Context** — fundamentals vs the setup (1-2 sentences).
**Recommendation** — verdict with execution levels restated. If DSR<0.6, recommend NO TRADE.

Under 300 words."""
    return model.generate_content(prompt).text


# ════════════════════════════════════════════════════════════
# 9. UI HELPERS
# ════════════════════════════════════════════════════════════

def _stat_row(cells):
    html = (f"<div style='display:grid;grid-template-columns:repeat({len(cells)},1fr);"
            f"gap:1px;background:{BORDER};border:1px solid {BORDER};border-radius:8px;overflow:hidden;'>")
    for label, value, color in cells:
        html += (f"<div style='background:{PANEL};padding:12px 14px;'>"
                 f"<div style='font-size:10px;letter-spacing:1px;color:{MUTE};text-transform:uppercase;'>{label}</div>"
                 f"<div style='font-family:{MONO};font-size:18px;font-weight:700;color:{color};margin-top:4px;'>{value}</div></div>")
    return html + "</div>"


def _fmt(v, suffix="", dp=2, dash="—"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return f"{v:,.{dp}f}{suffix}"


# ════════════════════════════════════════════════════════════
# 10. STREAMLIT UI
# ════════════════════════════════════════════════════════════

def render_quant_analysis():
    import streamlit as st

    # cache wrappers (avoid yfinance rate limits)
    _hist = st.cache_data(ttl=900, show_spinner=False)(fetch_history)
    _bench = st.cache_data(ttl=900, show_spinner=False)(fetch_benchmark)

    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-left:4px solid {AMBER};
    border-radius:10px;padding:16px 20px;margin-bottom:14px;'>
    <div style='font-family:{MONO};font-size:11px;letter-spacing:3px;color:{AMBER};'>ARKA QUANT TERMINAL</div>
    <div style='font-size:20px;font-weight:800;color:{IVORY};margin-top:2px;'>Institutional Single-Asset Analytics · {HORIZON}-Day Horizon</div>
    <div style='font-size:12px;color:{MUTE};margin-top:4px;'>Frac-diff · EWMA variance · non-overlapping triple-barrier · purged walk-forward · Deflated Sharpe · Kelly sizing</div>
    </div>""", unsafe_allow_html=True)

    with st.form("qt"):
        c1, c2, c3 = st.columns([2, 1, 1])
        symbol = c1.text_input("NSE Symbol", value="RELIANCE")
        cost_bps = c2.number_input("Friction (bps)", value=10.0, min_value=0.0, step=1.0)
        ptsl = c3.selectbox("Barrier σ (PT/SL)", ["2.0 / 1.5", "1.5 / 1.0", "3.0 / 2.0"])
        run = st.form_submit_button("Run Analysis", type="primary", use_container_width=True)

    if not run:
        st.info("Enter an NSE symbol and click **Run Analysis**.")
        return
    if not symbol.strip():
        st.error("Enter a symbol."); return

    pt, sl = [float(x) for x in ptsl.split("/")]

    with st.spinner("Running institutional quant pipeline..."):
        df, sym, info = _hist(symbol)
        if df.empty or len(df) < 400:
            st.error(f"Insufficient data for {sym} (need ~2y+ for walk-forward)."); return
        bench = _bench()
        close = df["Close"]; price = float(close.iloc[-1])

        stat = stationarity_analysis(close)
        sigma = ewma_vol(close)
        vol = volatility_regime(sigma)
        vp = volume_profile_levels(df)
        fac = factor_battery(close, bench)
        fund = fundamentals(info)
        bt_long = triple_barrier_nonoverlap(close, sigma, 1, pt, sl, HORIZON, cost_bps)
        bt_short = triple_barrier_nonoverlap(close, sigma, -1, pt, sl, HORIZON, cost_bps)
        score, side, drivers, dom = composite_alpha(fac, vol, bt_long, bt_short)
        verdict = "BUY" if side == 1 else "SELL" if side == -1 else "NO TRADE"
        ex_side = side if side != 0 else 1
        ex = execution_matrix(price, vol["sigma_daily"], ex_side, pt, sl, cost_bps)
        wf = purged_walk_forward(close, sigma, ex_side, pt, sl, HORIZON, cost_bps)
        size = position_sizing(dom, vol["sigma_annual"])

    vcolor = {"BUY": GREEN, "SELL": RED, "NO TRADE": AMBER}[verdict]

    # Header
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{PANEL2},{PANEL});border:1px solid {BORDER};
    border-left:5px solid {vcolor};border-radius:12px;padding:20px 24px;margin:6px 0 16px;
    display:flex;justify-content:space-between;align-items:center;'>
      <div>
        <div style='font-size:13px;color:{MUTE};letter-spacing:1px;'>{sym} · {fund['name']}</div>
        <div style='font-size:12px;color:{MUTE};'>{fund['sector']} · {fund['industry']}</div>
        <div style='font-family:{MONO};font-size:26px;font-weight:800;color:{IVORY};margin-top:6px;'>Rs {price:,.2f}</div>
      </div>
      <div style='text-align:right;'>
        <div style='font-size:11px;color:{MUTE};letter-spacing:1px;'>MODEL VERDICT</div>
        <div style='font-size:34px;font-weight:800;color:{vcolor};line-height:1.1;'>{verdict}</div>
        <div style='font-family:{MONO};font-size:14px;color:{IVORY};'>Confidence {score}/100</div>
      </div>
    </div>""", unsafe_allow_html=True)

    # Execution
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:8px 0;'>STRUCTURAL EXECUTION MATRIX · t+{HORIZON}</div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Entry", f"Rs {ex['entry']:,.2f}", IVORY),
        ("Target", f"Rs {ex['target']:,.2f}", GREEN),
        ("Stop-Loss", f"Rs {ex['stop']:,.2f}", RED),
        ("Risk:Reward", f"{ex['rr']} : 1", BLUE),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Suggested Weight", f"{size['weight']*100:.1f}%", AMBER),
        ("Vol-Target W", f"{size['vol_target_w']*100:.1f}%", IVORY),
        ("Half-Kelly", f"{size['kelly_half']*100:.1f}%", IVORY),
        ("Stat. Confidence", f"{size['confidence']:.2f}", BLUE),
    ]), unsafe_allow_html=True)

    # Skill statistics (the heart of the upgrade)
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>SKILL STATISTICS · IN-SAMPLE vs OUT-OF-SAMPLE</div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Expectancy/trade", f"{dom['expectancy']*100:+.2f}%", GREEN if dom['expectancy'] > 0 else RED),
        ("Prob. Sharpe (PSR)", f"{dom['psr']:.2f}", IVORY),
        ("Deflated Sharpe", f"{dom['dsr']:.2f}", GREEN if dom['dsr'] >= 0.6 else RED),
        ("Win-Rate / n", f"{dom['win_rate']:.0f}% / {dom['n']}", IVORY),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("OOS Expectancy", _fmt((wf['oos_expectancy'] or 0) * 100, "%", 2) if wf['oos_n'] else "—",
         GREEN if (wf['oos_expectancy'] or 0) > 0 else RED),
        ("OOS Win-Rate", _fmt(wf['oos_win_rate'], "%", 0) if wf['oos_n'] else "—", IVORY),
        ("OOS Sample (n)", f"{wf['oos_n']}", MUTE),
        ("Trials Deflated", "2 (L/S)", MUTE),
    ]), unsafe_allow_html=True)

    # Factor battery
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>QUANTITATIVE FACTOR BATTERY</div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Ann. Return", _fmt(fac['ann_ret'], "%", 1), GREEN if fac['ann_ret'] > 0 else RED),
        ("Ann. Vol", _fmt(fac['ann_vol'], "%", 1), IVORY),
        ("Sharpe", _fmt(fac['sharpe'], "", 2), IVORY),
        ("Sortino", _fmt(fac['sortino'], "", 2), IVORY),
        ("Max DD", _fmt(fac['max_dd'], "%", 1), RED),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Beta (NIFTY)", _fmt(fac['beta'], "", 2), IVORY),
        ("Alpha", _fmt(fac['alpha'], "%", 1), GREEN if (fac['alpha'] or 0) > 0 else RED),
        ("VaR 95%", _fmt(fac['var95'], "%", 2), RED),
        ("CVaR 95%", _fmt(fac['cvar95'], "%", 2), RED),
        ("Hurst", _fmt(fac['hurst'], "", 2), BLUE),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Skew", _fmt(fac['skew'], "", 2), IVORY),
        ("Kurtosis", _fmt(fac['kurt'], "", 2), IVORY),
        ("Autocorr(1)", _fmt(fac['autocorr1'], "", 3), IVORY),
        ("EWMA σ ann.", _fmt(vol['sigma_annual']*100, "%", 1), IVORY),
        ("Vol Regime", vol['regime'], AMBER),
    ]), unsafe_allow_html=True)

    # Fundamentals
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>RELATIVE VALUATION & FUNDAMENTALS</div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Market Cap", f"Rs {fund['mcap_cr']:,.0f} cr" if fund['mcap_cr'] else "—", IVORY),
        ("P/E (TTM)", _fmt(fund['pe'], "", 1), IVORY),
        ("Fwd P/E", _fmt(fund['fwd_pe'], "", 1), IVORY),
        ("P/B", _fmt(fund['pb'], "", 2), IVORY),
        ("ROE", _fmt(fund['roe'], "%", 1), GREEN if (fund['roe'] or 0) > 15 else IVORY),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Profit Margin", _fmt(fund['profit_margin'], "%", 1), IVORY),
        ("Div Yield", _fmt(fund['div_yield'], "%", 2), IVORY),
        ("52W High", _fmt(fund['wk52_high'], "", 1), GREEN),
        ("52W Low", _fmt(fund['wk52_low'], "", 1), RED),
        ("Info Beta", _fmt(fund['beta_info'], "", 2), IVORY),
    ]), unsafe_allow_html=True)

    # Stationarity + liquidity
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>STATIONARITY · LIQUIDITY POOLS</div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Optimal d", _fmt(stat['d'], "", 2), BLUE),
        ("ADF p-value", _fmt(stat['adf_pvalue'], "", 4), IVORY),
        ("Memory Retained", _fmt(stat['memory_retained'], "", 3), GREEN),
        ("Vol POC", f"Rs {vp['poc']:,.2f}", AMBER),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Liquidity Support", f"Rs {vp['support']:,.2f}", GREEN),
        ("Liquidity Resistance", f"Rs {vp['resistance']:,.2f}", RED),
        ("Memory vs 1st-diff", _fmt(stat['memory_gain'], "", 3), BLUE),
        ("EWMA σ daily", _fmt(vol['sigma_daily']*100, "%", 2), IVORY),
    ]), unsafe_allow_html=True)

    # AI note
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>INSTITUTIONAL RESEARCH NOTE</div>", unsafe_allow_html=True)
    api_key = st.secrets.get("GEMINI_KEY", "")
    if not api_key:
        st.warning("GEMINI_KEY not in secrets — research note disabled.")
    else:
        with st.spinner("Generating research note..."):
            try:
                note = gemini_research_note(sym, fund, fac, vol, stat, ex, dom, wf, size, score, verdict, api_key)
                st.markdown(f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:10px;"
                            f"padding:18px 22px;color:{IVORY};line-height:1.7;font-size:14px;'>{note}</div>",
                            unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Research note failed: {e}")

    with st.expander("Composite score — model drivers"):
        for d in drivers:
            st.markdown(f"- {d}")

    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>EWMA CONDITIONAL VOLATILITY PATH</div>", unsafe_allow_html=True)
    st.line_chart((sigma.tail(250) * np.sqrt(TRADING_DAYS) * 100).rename("Annualized σ %"))

    st.caption("Educational use only. Not investment advice. Past statistical edge does not guarantee future returns. Trading involves risk of capital loss.")
