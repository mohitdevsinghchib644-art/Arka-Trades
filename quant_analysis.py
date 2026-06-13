"""
quant_analysis.py — Institutional Quant Terminal (5-day swing horizon)
======================================================================
Engine combines:
  - López de Prado (AFML): fractional differentiation + memory conservation
    (Ch.5), path-dependent triple-barrier labeling (Ch.3).
  - Ernest Chan: volatility-scaled barriers, bps transaction-cost model,
    statistical expectancy / Sharpe-equivalent.
  - Institutional factor battery: beta/alpha vs NIFTY, Sharpe, Sortino,
    max drawdown, VaR/CVaR (95%), Hurst exponent, skew/kurtosis,
    annualized drift & vol, return autocorrelation.
  - Fundamentals: P/E, P/B, ROE, margins, market cap, 52w range (yfinance).
  - EWMA conditional-variance volatility regime + Volume-Profile S/R.
  - Gemini-written institutional research note (no image upload).

UI entry point: render_quant_analysis()
Dependencies: pandas, numpy, scikit-learn, statsmodels, yfinance,
              google-generativeai, streamlit
"""

import numpy as np
import pandas as pd
import yfinance as yf
from statsmodels.tsa.stattools import adfuller

# ── Terminal palette (Bloomberg-style) ──
BG     = "#0A0E14"
PANEL  = "#0F141C"
PANEL2 = "#141B26"
BORDER = "#1F2A38"
AMBER  = "#FFA600"
IVORY  = "#E6EDF3"
MUTE   = "#7D8DA0"
GREEN  = "#26D07C"
RED    = "#FF4D4D"
BLUE   = "#3DA5FF"
MONO   = "'JetBrains Mono','SF Mono',monospace"


# ════════════════════════════════════════════════════════════
# DATA
# ════════════════════════════════════════════════════════════

def fetch_history(symbol: str, period: str = "3y", interval: str = "1d"):
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym += ".NS"
    tk = yf.Ticker(sym)
    df = tk.history(period=period, interval=interval)
    if df.empty:
        return df, sym, {}
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    try:
        info = tk.info
    except Exception:
        info = {}
    return df, sym, info


def fetch_benchmark(period: str = "3y"):
    try:
        b = yf.Ticker("^NSEI").history(period=period, interval="1d")["Close"].dropna()
        return b
    except Exception:
        return pd.Series(dtype=float)


# ════════════════════════════════════════════════════════════
# 1. FRACTIONAL DIFFERENTIATION & MEMORY  (AFML Ch.5)
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
    r = np.log(close / close.shift(1)).dropna()
    var = np.zeros(len(r)); var[0] = r.var()
    for t in range(1, len(r)):
        var[t] = lam * var[t - 1] + (1 - lam) * r.iloc[t - 1] ** 2
    return pd.Series(np.sqrt(var), index=r.index)


def volatility_regime(sigma):
    cur = float(sigma.iloc[-1]); hist = sigma.tail(252)
    pct = float((hist < cur).mean() * 100)
    regime = "HIGH / STRESSED" if pct >= 80 else "LOW / COMPRESSED" if pct <= 20 else "NORMAL"
    return {"sigma_daily": cur, "sigma_annual": cur * np.sqrt(252),
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
# 3. INSTITUTIONAL FACTOR BATTERY
# ════════════════════════════════════════════════════════════

def hurst_exponent(ts, max_lag=40):
    ts = np.asarray(ts, dtype=float)
    lags = range(2, min(max_lag, len(ts) // 2))
    tau = [np.std(ts[lag:] - ts[:-lag]) for lag in lags]
    tau = [t if t > 0 else 1e-10 for t in tau]
    return float(np.polyfit(np.log(list(lags)), np.log(tau), 1)[0])


def factor_battery(close, bench):
    r = np.log(close / close.shift(1)).dropna()
    ann_ret = float(r.mean() * 252)
    ann_vol = float(r.std() * np.sqrt(252))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    downside = r[r < 0].std() * np.sqrt(252)
    sortino = ann_ret / downside if downside > 0 else 0
    eq = (1 + r).cumprod()
    mdd = float((eq / eq.cummax() - 1).min() * 100)
    var95 = float(np.percentile(r, 5) * 100)
    cvar95 = float(r[r <= np.percentile(r, 5)].mean() * 100)
    skew = float(r.skew()); kurt = float(r.kurtosis())
    ac1 = float(r.autocorr(1)) if len(r) > 2 else 0
    hurst = hurst_exponent(np.log(close.dropna().values))

    beta = alpha = np.nan
    if len(bench) > 10:
        br = np.log(bench / bench.shift(1)).dropna()
        j = pd.concat([r, br], axis=1, join="inner").dropna()
        j.columns = ["a", "b"]
        if len(j) > 30 and j["b"].var() > 0:
            beta = float(np.cov(j["a"], j["b"])[0, 1] / j["b"].var())
            alpha = float((j["a"].mean() - beta * j["b"].mean()) * 252)
    return {"ann_ret": ann_ret * 100, "ann_vol": ann_vol * 100, "sharpe": sharpe,
            "sortino": sortino, "max_dd": mdd, "var95": var95, "cvar95": cvar95,
            "skew": skew, "kurt": kurt, "autocorr1": ac1, "hurst": hurst,
            "beta": beta, "alpha": (alpha * 100) if np.isfinite(alpha) else np.nan}


def fundamentals(info):
    def g(k):
        v = info.get(k)
        return v if isinstance(v, (int, float)) and np.isfinite(v) else None
    mc = g("marketCap")
    return {
        "name": info.get("longName") or info.get("shortName") or "—",
        "sector": info.get("sector") or "—",
        "industry": info.get("industry") or "—",
        "mcap_cr": (mc / 1e7) if mc else None,  # INR crore
        "pe": g("trailingPE"), "fwd_pe": g("forwardPE"), "pb": g("priceToBook"),
        "roe": (g("returnOnEquity") or 0) * 100 if g("returnOnEquity") is not None else None,
        "profit_margin": (g("profitMargins") or 0) * 100 if g("profitMargins") is not None else None,
        "div_yield": (g("dividendYield") or 0) * 100 if g("dividendYield") is not None else None,
        "wk52_high": g("fiftyTwoWeekHigh"), "wk52_low": g("fiftyTwoWeekLow"),
        "beta_info": g("beta"),
    }


# ════════════════════════════════════════════════════════════
# 4. PATH-DEPENDENT TRIPLE-BARRIER (AFML Ch.3 + Chan costs)
# ════════════════════════════════════════════════════════════

def triple_barrier_backtest(close, sigma, side=1, pt=2.0, sl=1.5, vbar=5, cost_bps=10.0):
    px = close.reindex(sigma.index).dropna()
    sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4
    rets, labels = [], []
    for i in range(len(px) - vbar):
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            continue
        p0 = px.iloc[i]
        up = p0 * (1 + side * pt * s); dn = p0 * (1 + side * -sl * s)
        path = px.iloc[i + 1: i + 1 + vbar]
        out = None
        for p in path:
            if side == 1:
                if p >= up: out = pt * s; break
                if p <= dn: out = -sl * s; break
            else:
                if p <= up: out = pt * s; break
                if p >= dn: out = -sl * s; break
        if out is None:
            out = side * (path.iloc[-1] / p0 - 1)
        out -= cost
        rets.append(out); labels.append(1 if out > 0 else 0)
    rets = np.array(rets)
    if len(rets) == 0:
        return {"expectancy": 0, "sharpe": 0, "win_rate": 0, "n": 0}
    m, sd = rets.mean(), rets.std()
    return {"expectancy": float(m), "sharpe": float(m / sd * np.sqrt(50)) if sd > 0 else 0,
            "win_rate": float(np.mean(labels) * 100), "n": int(len(rets))}


def execution_matrix(price, sig_d, side, pt=2.0, sl=1.5, cost_bps=10.0):
    cost = price * cost_bps / 1e4
    if side >= 0:
        entry, tgt, stp = price, price * (1 + pt * sig_d) - cost, price * (1 - sl * sig_d) - cost
    else:
        entry, tgt, stp = price, price * (1 - pt * sig_d) + cost, price * (1 + sl * sig_d) + cost
    rr = abs(tgt - entry) / abs(entry - stp) if (entry - stp) != 0 else 0
    return {"entry": round(entry, 2), "target": round(tgt, 2), "stop": round(stp, 2), "rr": round(rr, 2)}


# ════════════════════════════════════════════════════════════
# 5. COMPOSITE ALPHA
# ════════════════════════════════════════════════════════════

def composite_alpha(fac, vol, bt_long, bt_short):
    edge = bt_long["expectancy"] - bt_short["expectancy"]
    score, drivers = 50.0, []
    if edge >= 0:
        score += min(20, edge * 4000); drivers.append(
            f"Long triple-barrier expectancy beats short by {edge*100:.2f}% net of costs")
    else:
        score += max(-20, edge * 4000); drivers.append(
            f"Short triple-barrier expectancy beats long by {abs(edge)*100:.2f}% net of costs")
    dom = bt_long if edge >= 0 else bt_short
    score += np.clip(dom["sharpe"] * 6, -12, 12)
    drivers.append(f"Setup Sharpe-equivalent {dom['sharpe']:.2f}, win-rate {dom['win_rate']:.0f}% (n={dom['n']})")
    score += np.clip(fac["sharpe"] * 5, -8, 8)
    drivers.append(f"Asset Sharpe {fac['sharpe']:.2f}, Sortino {fac['sortino']:.2f}")
    if fac["hurst"] > 0.55:
        score += 4; drivers.append(f"Hurst {fac['hurst']:.2f} → trending (momentum-friendly)")
    elif fac["hurst"] < 0.45:
        drivers.append(f"Hurst {fac['hurst']:.2f} → mean-reverting regime")
    if vol["regime"].startswith("HIGH"):
        score -= 6; drivers.append(f"Vol regime {vol['regime']} — conviction trimmed")
    elif vol["regime"].startswith("LOW"):
        score += 3; drivers.append(f"Vol regime {vol['regime']} — favorable compression")
    score = float(max(0, min(100, score)))
    side = 1 if score >= 55 else (-1 if score <= 45 else 0)
    return round(score, 1), side, drivers


# ════════════════════════════════════════════════════════════
# 6. GEMINI RESEARCH NOTE (text only, no image)
# ════════════════════════════════════════════════════════════

def gemini_research_note(sym, fund, fac, vol, stat, ex, dom, score, verdict, api_key):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""You are a sell-side equity research analyst at an investment bank writing a concise
institutional research note for a 1-week (5 business day) swing trade on {sym} ({fund['name']},
{fund['sector']} / {fund['industry']}).

Use ONLY the quantitative evidence below. Write like a professional desk note: crisp, no fluff,
no generic 'RSI is high' retail commentary. Reference the actual statistics.

COMPUTED METRICS
- Composite Alpha Score: {score}/100 | Model verdict: {verdict}
- Fractional differentiation optimal d={stat['d']:.2f} (ADF p={stat['adf_pvalue']:.4f}), memory retained {stat['memory_retained']:.3f} vs first-diff {stat['memory_first_diff']:.3f}
- Annualized return {fac['ann_ret']:.1f}%, vol {fac['ann_vol']:.1f}%, Sharpe {fac['sharpe']:.2f}, Sortino {fac['sortino']:.2f}
- Beta {fac['beta']:.2f}, Alpha {fac['alpha']:.1f}% (vs NIFTY), Max DD {fac['max_dd']:.1f}%
- VaR95 {fac['var95']:.2f}%, CVaR95 {fac['cvar95']:.2f}%, Skew {fac['skew']:.2f}, Kurt {fac['kurt']:.2f}, Hurst {fac['hurst']:.2f}
- EWMA vol regime: {vol['regime']} ({vol['percentile']:.0f}th pct), annualized {vol['sigma_annual']*100:.1f}%
- Triple-barrier setup: expectancy {dom['expectancy']*100:+.2f}%/trade, Sharpe-eq {dom['sharpe']:.2f}, win-rate {dom['win_rate']:.0f}%
- Execution: entry {ex['entry']}, target {ex['target']}, stop {ex['stop']}, R:R {ex['rr']}
- Fundamentals: P/E {fund['pe']}, Fwd P/E {fund['fwd_pe']}, P/B {fund['pb']}, ROE {fund['roe']}%, Margin {fund['profit_margin']}%, MCap Rs {fund['mcap_cr']} cr

STRUCTURE (use these exact short headers):
**Thesis** — 2-3 sentences on the directional edge and statistical basis.
**Risk Profile** — tail risk (VaR/CVaR), drawdown, beta, vol regime.
**Valuation Context** — fundamentals relative to the setup (1-2 sentences).
**Catalysts & Path Dependency** — what the triple-barrier evidence implies over t+5.
**Recommendation** — verdict with the execution levels restated.

Keep total under 280 words."""
    return model.generate_content(prompt).text


# ════════════════════════════════════════════════════════════
# 7. UI HELPERS (Bloomberg terminal styling)
# ════════════════════════════════════════════════════════════

def _stat_row(cells):
    """cells = list of (label, value, color)."""
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
# 8. STREAMLIT UI
# ════════════════════════════════════════════════════════════

def render_quant_analysis():
    import streamlit as st

    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-left:4px solid {AMBER};
    border-radius:10px;padding:16px 20px;margin-bottom:14px;'>
    <div style='font-family:{MONO};font-size:11px;letter-spacing:3px;color:{AMBER};'>ARKA QUANT TERMINAL</div>
    <div style='font-size:20px;font-weight:800;color:{IVORY};margin-top:2px;'>Institutional Single-Asset Analytics · 5-Day Horizon</div>
    <div style='font-size:12px;color:{MUTE};margin-top:4px;'>Fractional differentiation · EWMA conditional variance · path-dependent triple-barrier · factor battery · AI research note</div>
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
        df, sym, info = fetch_history(symbol)
        if df.empty or len(df) < 260:
            st.error(f"Insufficient data for {sym} (need ~1y+)."); return
        bench = fetch_benchmark()
        close = df["Close"]; price = float(close.iloc[-1])

        stat = stationarity_analysis(close)
        sigma = ewma_vol(close)
        vol = volatility_regime(sigma)
        vp = volume_profile_levels(df)
        fac = factor_battery(close, bench)
        fund = fundamentals(info)
        bt_long = triple_barrier_backtest(close, sigma, 1, pt, sl, 5, cost_bps)
        bt_short = triple_barrier_backtest(close, sigma, -1, pt, sl, 5, cost_bps)
        score, side, drivers = composite_alpha(fac, vol, bt_long, bt_short)
        verdict = "BUY" if side == 1 else "SELL" if side == -1 else "HOLD"
        ex_side = side if side != 0 else 1
        ex = execution_matrix(price, vol["sigma_daily"], ex_side, pt, sl, cost_bps)
        dom = bt_long if ex_side == 1 else bt_short

    vcolor = {"BUY": GREEN, "SELL": RED, "HOLD": AMBER}[verdict]

    # ── Header banner ──
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
        <div style='font-size:11px;color:{MUTE};letter-spacing:1px;'>ALPHA VERDICT</div>
        <div style='font-size:34px;font-weight:800;color:{vcolor};line-height:1.1;'>{verdict}</div>
        <div style='font-family:{MONO};font-size:14px;color:{IVORY};'>Score {score}/100</div>
      </div>
    </div>""", unsafe_allow_html=True)

    # ── Execution matrix ──
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:8px 0;'>STRUCTURAL EXECUTION MATRIX · t+5</div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Entry", f"Rs {ex['entry']:,.2f}", IVORY),
        ("Target", f"Rs {ex['target']:,.2f}", GREEN),
        ("Stop-Loss", f"Rs {ex['stop']:,.2f}", RED),
        ("Risk:Reward", f"{ex['rr']} : 1", BLUE),
    ]), unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(_stat_row([
        ("Expectancy/trade", f"{dom['expectancy']*100:+.2f}%", GREEN if dom['expectancy'] > 0 else RED),
        ("Sharpe-equivalent", f"{dom['sharpe']:.2f}", IVORY),
        ("Hist Win-Rate", f"{dom['win_rate']:.0f}%", IVORY),
        ("Sample (n)", f"{dom['n']}", MUTE),
    ]), unsafe_allow_html=True)

    # ── Quant factor battery ──
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

    # ── Fundamentals ──
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

    # ── Stationarity + liquidity ──
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

    # ── AI research note ──
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>INSTITUTIONAL RESEARCH NOTE</div>", unsafe_allow_html=True)
    api_key = st.secrets.get("GEMINI_KEY", "")
    if not api_key:
        st.warning("GEMINI_KEY not in secrets — research note disabled.")
    else:
        with st.spinner("Generating research note..."):
            try:
                note = gemini_research_note(sym, fund, fac, vol, stat, ex, dom, score, verdict, api_key)
                st.markdown(f"<div style='background:{PANEL};border:1px solid {BORDER};border-radius:10px;"
                            f"padding:18px 22px;color:{IVORY};line-height:1.7;font-size:14px;'>{note}</div>",
                            unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Research note failed: {e}")

    # ── Model drivers ──
    with st.expander("Composite Alpha — model drivers"):
        for d in drivers:
            st.markdown(f"- {d}")

    # ── Charts ──
    st.markdown(f"<div style='font-size:13px;font-weight:700;color:{AMBER};letter-spacing:1px;margin:18px 0 8px;'>EWMA CONDITIONAL VOLATILITY PATH</div>", unsafe_allow_html=True)
    st.line_chart((sigma.tail(250) * np.sqrt(252) * 100).rename("Annualized σ %"))

    st.caption("Educational use only. Not investment advice. Trading involves risk.")
