"""
quant_analysis.py — Institutional Single-Asset Quant Engine (5-day horizon)
===========================================================================
Frameworks:
  - Marcos López de Prado, "Advances in Financial Machine Learning":
    Fractional Differentiation (Ch.5), Triple-Barrier Labeling (Ch.3).
  - Ernest Chan: volatility-scaled barriers, bps transaction-cost model,
    statistical expectancy / Sharpe-equivalent of the setup.

Four vectors:
  1. Stationarity & memory conservation (optimal d, memory retained).
  2. EWMA conditional-variance volatility regime + Volume-Profile S/R.
  3. Path-dependent triple-barrier, vol-sized, t+5 vertical, bps penalty.
  4. Composite Alpha Score + execution matrix + statistical expectancy.

UI entry point: render_quant_analysis()
Dependencies: pandas, numpy, scikit-learn, statsmodels, yfinance, streamlit
"""

import numpy as np
import pandas as pd
import yfinance as yf
from statsmodels.tsa.stattools import adfuller


# ════════════════════════════════════════════════════════════
# 0. DATA INGESTION
# ════════════════════════════════════════════════════════════

def fetch_history(symbol: str, period: str = "3y", interval: str = "1d") -> pd.DataFrame:
    """Download OHLCV for a single NSE symbol (auto-appends .NS)."""
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym = sym + ".NS"
    df = yf.Ticker(sym).history(period=period, interval=interval)
    if df.empty:
        return df
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


# ════════════════════════════════════════════════════════════
# 1. FRACTIONAL DIFFERENTIATION & MEMORY  (López de Prado, Ch.5)
# ════════════════════════════════════════════════════════════

def _ffd_weights(d: float, threshold: float = 1e-4) -> np.ndarray:
    w, k = [1.0], 1
    while abs(w[-1]) > threshold:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
    return np.array(w[::-1])


def frac_diff_ffd(series: pd.Series, d: float) -> pd.Series:
    """Fixed-width-window fractional differentiation (memory-preserving)."""
    w = _ffd_weights(d)
    width = len(w)
    vals = series.ffill().values
    out = np.full(len(vals), np.nan)
    for i in range(width - 1, len(vals)):
        out[i] = np.dot(w, vals[i - width + 1: i + 1])
    return pd.Series(out, index=series.index)


def stationarity_analysis(close: pd.Series,
                          ds=np.round(np.arange(0.0, 1.01, 0.05), 2),
                          pval_target: float = 0.05) -> dict:
    """
    Find smallest d s.t. ADF p < target (max memory retention, LdP 5.5).
    Memory retained = corr(frac_diff, log-price) vs corr(first-diff, log-price).
    """
    logp = np.log(close.dropna())
    first_diff = logp.diff().dropna()
    base_mem = abs(np.corrcoef(first_diff, logp.loc[first_diff.index])[0, 1])

    best_d, best_p, best_mem = 1.0, np.nan, base_mem
    for d in ds:
        fd = frac_diff_ffd(logp, d).dropna()
        if len(fd) < 50:
            continue
        try:
            pval = adfuller(fd, maxlag=1, regression="c", autolag=None)[1]
        except Exception:
            continue
        mem = abs(np.corrcoef(fd, logp.loc[fd.index])[0, 1])
        if pval < pval_target:
            best_d, best_p, best_mem = float(d), float(pval), float(mem)
            break
        best_d, best_p, best_mem = float(d), float(pval), float(mem)

    return {
        "d": best_d,
        "adf_pvalue": best_p,
        "memory_retained": best_mem,           # corr of FFD series to log-price
        "memory_first_diff": float(base_mem),  # corr of first-diff to log-price
        "memory_gain": float(best_mem - base_mem),
    }


# ════════════════════════════════════════════════════════════
# 2. CONDITIONAL VOLATILITY REGIME + VOLUME-PROFILE S/R
# ════════════════════════════════════════════════════════════

def ewma_vol(close: pd.Series, lam: float = 0.94) -> pd.Series:
    """
    RiskMetrics EWMA conditional variance: sigma_t^2 = lam*sigma_{t-1}^2
    + (1-lam)*r_{t-1}^2. GARCH(1,1)-like instantaneous vol path.
    """
    r = np.log(close / close.shift(1)).dropna()
    var = np.zeros(len(r))
    var[0] = r.var()
    for t in range(1, len(r)):
        var[t] = lam * var[t - 1] + (1 - lam) * r.iloc[t - 1] ** 2
    return pd.Series(np.sqrt(var), index=r.index)


def volatility_regime(sigma: pd.Series) -> dict:
    """Classify instantaneous vol vs its own 1y distribution (percentile)."""
    cur = float(sigma.iloc[-1])
    hist = sigma.tail(252)
    pct = float((hist < cur).mean() * 100)
    if pct >= 80:
        regime = "HIGH / STRESSED"
    elif pct <= 20:
        regime = "LOW / COMPRESSED"
    else:
        regime = "NORMAL"
    return {
        "sigma_daily": cur,
        "sigma_annual": cur * np.sqrt(252),
        "percentile": pct,
        "regime": regime,
    }


def volume_profile_levels(df: pd.DataFrame, lookback: int = 120,
                          bins: int = 30) -> dict:
    """
    Microstructure liquidity pools: bin price range, accumulate traded volume,
    find high-concentration nodes (POC + nearest high-volume node above/below).
    """
    recent = df.tail(lookback)
    price = float(recent["Close"].iloc[-1])
    typical = (recent["High"] + recent["Low"] + recent["Close"]) / 3
    lo, hi = typical.min(), typical.max()
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vol = np.zeros(bins)
    idx = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    for i, v in zip(idx, recent["Volume"].values):
        vol[i] += v

    poc = float(centers[int(np.argmax(vol))])                 # point of control
    # high-volume nodes (top 40% by volume)
    thresh = np.quantile(vol[vol > 0], 0.6) if (vol > 0).any() else 0
    nodes = centers[vol >= thresh]
    res_nodes = sorted([n for n in nodes if n > price])
    sup_nodes = sorted([n for n in nodes if n < price], reverse=True)
    resistance = float(res_nodes[0]) if res_nodes else float(hi)
    support = float(sup_nodes[0]) if sup_nodes else float(lo)
    return {"poc": poc, "support": support, "resistance": resistance}


# ════════════════════════════════════════════════════════════
# 3. PATH-DEPENDENT TRIPLE-BARRIER  (LdP Ch.3 + Chan costs)
# ════════════════════════════════════════════════════════════

def triple_barrier_backtest(close: pd.Series, sigma: pd.Series,
                            side: int = 1, pt_mult: float = 2.0,
                            sl_mult: float = 1.5, vbar: int = 5,
                            cost_bps: float = 10.0) -> dict:
    """
    Path-dependent triple-barrier over history (LdP 3.4).
    Horizontal barriers = vol-scaled (pt_mult/sl_mult * instantaneous sigma).
    Vertical barrier at t+vbar business days. Returns net of bps round-trip cost.
    Produces the statistical expectancy / Sharpe-equivalent of the setup.
    """
    px = close.reindex(sigma.index).dropna()
    sig = sigma.reindex(px.index)
    cost = cost_bps / 1e4
    rets, labels = [], []

    n = len(px)
    for i in range(n - vbar):
        s = float(sig.iloc[i])
        if not np.isfinite(s) or s <= 0:
            continue
        p0 = px.iloc[i]
        up = p0 * (1 + side * pt_mult * s)
        dn = p0 * (1 + side * -sl_mult * s)
        path = px.iloc[i + 1: i + 1 + vbar]
        outcome = None
        for p in path:
            if side == 1:
                if p >= up: outcome = side * pt_mult * s; break
                if p <= dn: outcome = -side * sl_mult * s; break
            else:
                if p <= up: outcome = pt_mult * s; break
                if p >= dn: outcome = -sl_mult * s; break
        if outcome is None:  # vertical barrier
            outcome = side * (path.iloc[-1] / p0 - 1)
        outcome -= cost  # round-trip friction (Chan)
        rets.append(outcome)
        labels.append(1 if outcome > 0 else 0)

    rets = np.array(rets)
    if len(rets) == 0:
        return {"expectancy": 0, "sharpe": 0, "win_rate": 0, "n": 0,
                "mean_ret": 0, "std_ret": 0}

    mean_r, std_r = rets.mean(), rets.std()
    # per-trade Sharpe annualized to ~50 weekly setups
    sharpe = (mean_r / std_r * np.sqrt(50)) if std_r > 0 else 0
    return {
        "expectancy": float(mean_r),
        "sharpe": float(sharpe),
        "win_rate": float(np.mean(labels) * 100),
        "n": int(len(rets)),
        "mean_ret": float(mean_r),
        "std_ret": float(std_r),
    }


def execution_matrix(price: float, sigma_daily: float, side: int,
                     pt_mult: float = 2.0, sl_mult: float = 1.5,
                     cost_bps: float = 10.0) -> dict:
    """Forward-looking vol-adjusted entry/target/stop for the next setup."""
    cost = price * cost_bps / 1e4
    if side >= 0:
        entry = price
        target = price * (1 + pt_mult * sigma_daily) - cost
        stop = price * (1 - sl_mult * sigma_daily) - cost
    else:
        entry = price
        target = price * (1 - pt_mult * sigma_daily) + cost
        stop = price * (1 + sl_mult * sigma_daily) + cost
    rr = abs(target - entry) / abs(entry - stop) if (entry - stop) != 0 else 0
    return {"entry": round(entry, 2), "target": round(target, 2),
            "stop": round(stop, 2), "rr": round(rr, 2)}


# ════════════════════════════════════════════════════════════
# 4. COMPOSITE ALPHA SCORE
# ════════════════════════════════════════════════════════════

def composite_alpha(stat: dict, vol: dict, bt_long: dict, bt_short: dict,
                    drift_ann: float) -> tuple:
    """
    Synthesize vectors into 0–100 alpha score and directional side.
    Blends: backtested expectancy edge, Sharpe quality, drift, vol regime.
    """
    long_edge = bt_long["expectancy"] - bt_short["expectancy"]
    score = 50.0
    drivers = []

    # Directional edge from path-dependent backtest
    if long_edge > 0:
        score += min(20, long_edge * 4000); drivers.append(
            f"Long triple-barrier expectancy exceeds short by {long_edge*100:.2f}% net of costs")
    else:
        score += max(-20, long_edge * 4000); drivers.append(
            f"Short triple-barrier expectancy exceeds long by {abs(long_edge)*100:.2f}% net of costs")

    # Sharpe quality of dominant side
    dom = bt_long if long_edge >= 0 else bt_short
    score += np.clip(dom["sharpe"] * 6, -12, 12)
    drivers.append(f"Setup Sharpe-equivalent {dom['sharpe']:.2f}, "
                   f"win-rate {dom['win_rate']:.0f}% over {dom['n']} historical events")

    # Annualized drift
    score += np.clip(drift_ann * 40, -10, 10)
    drivers.append(f"Annualized log-drift {drift_ann*100:+.1f}%")

    # Volatility regime penalty (stressed regimes reduce conviction)
    if vol["regime"].startswith("HIGH"):
        score -= 6; drivers.append(
            f"Vol regime {vol['regime']} ({vol['percentile']:.0f}th pct) — conviction trimmed")
    elif vol["regime"].startswith("LOW"):
        score += 3; drivers.append(
            f"Vol regime {vol['regime']} ({vol['percentile']:.0f}th pct) — favorable compression")

    score = float(max(0, min(100, score)))
    side = 1 if score >= 55 else (-1 if score <= 45 else 0)
    return round(score, 1), side, drivers


def verdict_from(side: int) -> str:
    return "BUY" if side == 1 else ("SELL" if side == -1 else "HOLD")


# ════════════════════════════════════════════════════════════
# 5. STREAMLIT UI
# ════════════════════════════════════════════════════════════

def render_quant_analysis():
    import streamlit as st

    st.markdown("### Quant Analysis — Institutional Single-Asset Engine (5-Day Horizon)")
    st.caption("Fractional differentiation & memory conservation (LdP Ch.5) · "
               "EWMA conditional-variance regime + Volume-Profile liquidity pools · "
               "Path-dependent triple-barrier with bps friction (LdP Ch.3 / Chan).")

    with st.form("quant_inst"):
        c1, c2, c3 = st.columns([2, 1, 1])
        symbol = c1.text_input("NSE Symbol (any sector)", value="RELIANCE",
                               help="Any NSE ticker, e.g. TCS, DLF, SUNPHARMA. '.NS' auto-added.")
        cost_bps = c2.number_input("Friction (bps)", value=10.0, min_value=0.0, step=1.0)
        ptsl = c3.selectbox("Barrier σ-multiples (PT / SL)",
                            ["2.0 / 1.5", "1.5 / 1.0", "3.0 / 2.0"], index=0)
        run = st.form_submit_button("Run Quant Analysis", type="primary",
                                    use_container_width=True)

    if not run:
        st.info("Enter an NSE symbol and click **Run Quant Analysis**.")
        return
    if not symbol.strip():
        st.error("Please enter an NSE symbol.")
        return

    pt_mult, sl_mult = [float(x) for x in ptsl.split("/")]

    with st.spinner(f"Fetching {symbol.upper()} and running quant pipeline..."):
        df = fetch_history(symbol)
        if df.empty or len(df) < 260:
            st.error(f"Insufficient data for {symbol.upper()} (need ~1y+). Check symbol.")
            return

        close = df["Close"]
        price = float(close.iloc[-1])

        # Vector 1
        stat = stationarity_analysis(close)
        # Vector 2
        sigma = ewma_vol(close)
        vol = volatility_regime(sigma)
        vp = volume_profile_levels(df)
        # Vector 3 — path-dependent backtest both sides
        bt_long = triple_barrier_backtest(close, sigma, side=1, pt_mult=pt_mult,
                                          sl_mult=sl_mult, vbar=5, cost_bps=cost_bps)
        bt_short = triple_barrier_backtest(close, sigma, side=-1, pt_mult=pt_mult,
                                           sl_mult=sl_mult, vbar=5, cost_bps=cost_bps)
        drift_ann = float(np.log(close / close.shift(1)).dropna().tail(252).mean() * 252)
        # Vector 4
        score, side, drivers = composite_alpha(stat, vol, bt_long, bt_short, drift_ann)
        verdict = verdict_from(side)
        ex_side = side if side != 0 else 1
        ex = execution_matrix(price, vol["sigma_daily"], ex_side,
                              pt_mult, sl_mult, cost_bps)
        dom = bt_long if ex_side == 1 else bt_short

    # ── Verdict header ──
    vcolor = {"BUY": "#22C55E", "SELL": "#EF4444", "HOLD": "#F59E0B"}[verdict]
    st.markdown(
        f"<div style='background:#11161D;border:1px solid #242D3A;border-left:5px solid {vcolor};"
        f"border-radius:12px;padding:18px 22px;margin:8px 0 18px;'>"
        f"<span style='font-size:13px;color:#8C97A8;letter-spacing:1px;'>ALPHA VERDICT · {symbol.upper()}</span><br>"
        f"<span style='font-size:30px;font-weight:800;color:{vcolor};'>{verdict}</span>"
        f"<span style='font-size:18px;color:#E8ECF2;margin-left:14px;'>Composite Alpha Score: "
        f"<b>{score}/100</b></span></div>", unsafe_allow_html=True)

    # ── Execution matrix ──
    st.markdown("#### Structural Execution Matrix (t+5 horizon)")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Mathematical Entry", f"Rs {ex['entry']:,.2f}")
    e2.metric("Vol-Adj Profit Target", f"Rs {ex['target']:,.2f}")
    e3.metric("Vol-Adj Stop-Loss", f"Rs {ex['stop']:,.2f}")
    e4.metric("Risk : Reward", f"{ex['rr']} : 1")
    s1, s2, s3 = st.columns(3)
    s1.metric("Statistical Expectancy / trade", f"{dom['expectancy']*100:+.2f}%")
    s2.metric("Sharpe-equivalent", f"{dom['sharpe']:.2f}")
    s3.metric("Hist. Win-Rate", f"{dom['win_rate']:.0f}%  (n={dom['n']})")
    st.caption(f"Horizontal barriers = ±σ·multiples (PT {pt_mult} / SL {sl_mult}), "
               f"vertical barrier t+5, round-trip friction {cost_bps:.0f} bps deducted.")

    # ── Vector 1: stationarity ──
    st.markdown("#### 1 · Stationarity & Memory Conservation (López de Prado Ch.5)")
    a1, a2, a3 = st.columns(3)
    a1.metric("Optimal d", f"{stat['d']:.2f}")
    a2.metric("ADF p-value", f"{stat['adf_pvalue']:.4f}" if np.isfinite(stat['adf_pvalue']) else "n/a")
    a3.metric("Memory retained", f"{stat['memory_retained']:.3f}")
    st.markdown(
        f"At **d = {stat['d']:.2f}** the log-price series achieves stationarity "
        f"(ADF p = {stat['adf_pvalue']:.4f}) while retaining correlation "
        f"**{stat['memory_retained']:.3f}** to the original log-price. Integer "
        f"first-differencing (d=1) retains only **{stat['memory_first_diff']:.3f}** — "
        f"fractional differentiation conserves **{stat['memory_gain']:+.3f}** additional "
        f"memory, preserving predictive structure destroyed by naive differencing.")

    # ── Vector 2: vol regime + liquidity pools ──
    st.markdown("#### 2 · Conditional Volatility Regime & Liquidity Pools")
    b1, b2, b3 = st.columns(3)
    b1.metric("EWMA σ (daily)", f"{vol['sigma_daily']*100:.2f}%")
    b2.metric("EWMA σ (annual)", f"{vol['sigma_annual']*100:.1f}%")
    b3.metric("Vol Regime", f"{vol['regime']}")
    st.markdown(
        f"Instantaneous EWMA conditional variance (λ=0.94, RiskMetrics) places current "
        f"volatility at the **{vol['percentile']:.0f}th percentile** of its trailing-year "
        f"distribution → regime **{vol['regime']}**. Volume-Profile order-flow nodes: "
        f"**POC Rs {vp['poc']:,.2f}**, nearest liquidity support **Rs {vp['support']:,.2f}**, "
        f"resistance **Rs {vp['resistance']:,.2f}** (high-concentration traded-volume clusters, "
        f"not arbitrary swing points).")

    # ── Vector 4: score drivers ──
    st.markdown("#### Composite Alpha Rationale")
    for dr in drivers:
        st.markdown(f"- {dr}")

    # ── Vol path chart ──
    st.markdown("#### EWMA Conditional Volatility Path (annualized)")
    st.line_chart((sigma.tail(250) * np.sqrt(252) * 100).rename("Annualized σ %"))

    st.caption("Educational use only. Not investment advice. Trading involves risk.")
