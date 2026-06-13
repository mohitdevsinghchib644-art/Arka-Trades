"""
quant_analysis.py — Single-Stock Swing-Trade Quant Engine (1-week horizon)
==========================================================================
Frameworks:
  - Marcos López de Prado, "Advances in Financial Machine Learning":
    fractional differentiation (Ch.5), triple-barrier labeling (Ch.3).
  - Ernest Chan, "Quantitative Trading": ATR-based barriers, transaction
    costs in bps, explicit stop-loss / profit-target risk management.
  - Gemini Vision cross-check of an uploaded chart image vs. the math.

UI entry point: render_quant_analysis()

Dependencies: pandas, numpy, scikit-learn, statsmodels, yfinance,
              google-generativeai, Pillow, streamlit
"""

import io
import numpy as np
import pandas as pd
import yfinance as yf


# ════════════════════════════════════════════════════════════
# 1. DATA INGESTION
# ════════════════════════════════════════════════════════════

def fetch_history(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """Download OHLCV for a single NSE symbol (auto-appends .NS)."""
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym = sym + ".NS"
    df = yf.Ticker(sym).history(period=period, interval=interval)
    if df.empty:
        return df
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df


# ════════════════════════════════════════════════════════════
# 2. FRACTIONAL DIFFERENTIATION  (López de Prado, Ch.5)
# ════════════════════════════════════════════════════════════

def _ffd_weights(d: float, threshold: float = 1e-4) -> np.ndarray:
    """Fixed-width window fractional-differentiation weights."""
    w, k = [1.0], 1
    while abs(w[-1]) > threshold:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
    return np.array(w[::-1])


def frac_diff_ffd(series: pd.Series, d: float) -> pd.Series:
    """Memory-preserving stationary transform of a price series."""
    w = _ffd_weights(d)
    width = len(w)
    vals = series.ffill().values
    out = np.full(len(vals), np.nan)
    for i in range(width - 1, len(vals)):
        out[i] = np.dot(w, vals[i - width + 1: i + 1])
    return pd.Series(out, index=series.index)


def optimal_d(series: pd.Series, ds=np.arange(0.0, 1.01, 0.1),
              pval_target: float = 0.05):
    """
    Find the smallest d that makes the series stationary (ADF p < target),
    preserving maximum memory (López de Prado 5.5).
    Returns (best_d, adf_pvalue, frac_diff_series).
    """
    from statsmodels.tsa.stattools import adfuller
    logp = np.log(series.dropna())
    best = None
    for d in ds:
        fd = frac_diff_ffd(logp, d).dropna()
        if len(fd) < 30:
            continue
        try:
            pval = adfuller(fd, maxlag=1, regression="c", autolag=None)[1]
        except Exception:
            continue
        if best is None:
            best = (d, pval, fd)
        if pval < pval_target:
            return d, pval, fd
    return best if best else (1.0, np.nan, frac_diff_ffd(logp, 1.0).dropna())


# ════════════════════════════════════════════════════════════
# 3. FEATURE ENGINEERING  (Technical & Structural)
# ════════════════════════════════════════════════════════════

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).rolling(period).mean()
    loss = (-d.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def support_resistance(df: pd.DataFrame, window: int = 10, lookback: int = 120):
    """Structural levels from recent swing highs/lows."""
    recent = df.tail(lookback)
    highs = recent["High"]
    lows = recent["Low"]
    # local swing points
    swing_high = highs[(highs == highs.rolling(window, center=True).max())]
    swing_low = lows[(lows == lows.rolling(window, center=True).min())]
    price = df["Close"].iloc[-1]
    res_levels = sorted([h for h in swing_high.dropna().unique() if h > price])
    sup_levels = sorted([l for l in swing_low.dropna().unique() if l < price],
                        reverse=True)
    resistance = res_levels[0] if res_levels else float(highs.max())
    support = sup_levels[0] if sup_levels else float(lows.min())
    return float(support), float(resistance)


def build_features(df: pd.DataFrame) -> dict:
    close = df["Close"]
    feats = {}
    feats["price"] = float(close.iloc[-1])
    feats["rsi"] = float(rsi(close).iloc[-1])
    feats["atr"] = float(atr(df).iloc[-1])
    feats["atr_pct"] = float(feats["atr"] / feats["price"] * 100)
    feats["roc_10"] = float((close.iloc[-1] / close.iloc[-11] - 1) * 100) if len(close) > 11 else 0.0
    feats["daily_vol"] = float(close.pct_change().rolling(20).std().iloc[-1] * 100)
    feats["sma20"] = float(close.rolling(20).mean().iloc[-1])
    feats["sma50"] = float(close.rolling(50).mean().iloc[-1])
    feats["sma200"] = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else float("nan")
    sup, res = support_resistance(df)
    feats["support"] = sup
    feats["resistance"] = res
    return feats


# ════════════════════════════════════════════════════════════
# 4. TRIPLE-BARRIER SWING SETUP  (Chan ATR barriers + costs)
# ════════════════════════════════════════════════════════════

def triple_barrier_levels(feats: dict, side: int = 1,
                          pt_mult: float = 2.0, sl_mult: float = 1.5,
                          cost_bps: float = 10.0) -> dict:
    """
    1-week swing setup (max hold = 5 business days).
    Profit-target and stop-loss as ATR multiples (Chan), net of bps costs.
    side = +1 for long setup, -1 for short.
    """
    price = feats["price"]
    a = feats["atr"]
    cost = price * cost_bps / 1e4

    if side >= 0:  # long
        entry = price
        target = price + pt_mult * a - cost
        stop = price - sl_mult * a - cost
    else:          # short
        entry = price
        target = price - pt_mult * a + cost
        stop = price + sl_mult * a + cost

    rr = abs(target - entry) / abs(entry - stop) if (entry - stop) != 0 else 0
    return {
        "entry": round(entry, 2),
        "target": round(target, 2),
        "stop": round(stop, 2),
        "rr": round(rr, 2),
        "max_hold_days": 5,
        "cost_bps": cost_bps,
    }


# ════════════════════════════════════════════════════════════
# 5. COMPOSITE QUANT SCORE + VERDICT
# ════════════════════════════════════════════════════════════

def composite_score(feats: dict) -> tuple:
    """
    0–100 score blending trend, momentum, and mean-reversion posture.
    Returns (score, side, drivers).
    """
    price = feats["price"]
    score = 50.0
    drivers = []

    # Trend (SMA stack)
    if not np.isnan(feats["sma200"]):
        if price > feats["sma50"] > feats["sma200"]:
            score += 15; drivers.append("Uptrend: price > SMA50 > SMA200")
        elif price < feats["sma50"] < feats["sma200"]:
            score -= 15; drivers.append("Downtrend: price < SMA50 < SMA200")
    if price > feats["sma20"]:
        score += 7; drivers.append("Price above SMA20 (short-term bullish)")
    else:
        score -= 7; drivers.append("Price below SMA20 (short-term bearish)")

    # Momentum (RSI)
    r = feats["rsi"]
    if r < 30:
        score += 10; drivers.append(f"RSI {r:.0f}: oversold, mean-reversion long")
    elif r > 70:
        score -= 10; drivers.append(f"RSI {r:.0f}: overbought, mean-reversion short")
    elif 50 <= r <= 65:
        score += 6; drivers.append(f"RSI {r:.0f}: healthy momentum zone")

    # ROC
    if feats["roc_10"] > 0:
        score += 5; drivers.append(f"ROC(10) +{feats['roc_10']:.1f}% positive")
    else:
        score -= 5; drivers.append(f"ROC(10) {feats['roc_10']:.1f}% negative")

    score = max(0, min(100, score))
    side = 1 if score >= 55 else (-1 if score <= 45 else 0)
    return round(score, 1), side, drivers


def verdict_from(score: float, side: int) -> str:
    if side == 1:
        return "BUY"
    if side == -1:
        return "SELL"
    return "HOLD"


# ════════════════════════════════════════════════════════════
# 6. GEMINI VISION CROSS-CHECK
# ════════════════════════════════════════════════════════════

def gemini_chart_analysis(image_bytes: bytes, symbol: str, feats: dict, api_key: str) -> str:
    """Send chart image + computed features to Gemini for visual cross-check."""
    import google.generativeai as genai
    from PIL import Image

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    img = Image.open(io.BytesIO(image_bytes))

    prompt = f"""You are an institutional quant analyst doing a VISUAL cross-check of a stock chart
for a 1-week swing trade on {symbol} (NSE).

Computed mathematical features:
- Price: {feats['price']:.2f}
- RSI(14): {feats['rsi']:.1f}
- ATR(14): {feats['atr']:.2f} ({feats['atr_pct']:.2f}% of price)
- ROC(10): {feats['roc_10']:.2f}%
- 20d daily volatility: {feats['daily_vol']:.2f}%
- SMA20/50/200: {feats['sma20']:.2f} / {feats['sma50']:.2f} / {feats['sma200']:.2f}
- Support / Resistance: {feats['support']:.2f} / {feats['resistance']:.2f}

From the CHART IMAGE, identify:
1. Visible chart pattern (flag, triangle, double top/bottom, breakout, range, etc.)
2. Macro trend direction (up / down / sideways)
3. Notable candlestick setup near the latest candle
4. DIVERGENCES: does the visual picture agree or disagree with the math above?

Be concise (max ~150 words), use bullet points, and end with a one-line visual bias:
VISUAL BIAS: BULLISH / BEARISH / NEUTRAL."""

    resp = model.generate_content([prompt, img])
    return resp.text


# ════════════════════════════════════════════════════════════
# 7. STREAMLIT UI
# ════════════════════════════════════════════════════════════

def render_quant_analysis():
    import streamlit as st

    st.markdown("### Quant Analysis — Single-Stock Swing Engine (1-Week Horizon)")
    st.caption("López de Prado fractional differentiation + triple-barrier · "
               "Ernest Chan ATR risk management + costs · Gemini Vision cross-check.")

    with st.form("quant_single"):
        c1, c2 = st.columns([2, 1])
        symbol = c1.text_input("NSE Symbol (any sector)", value="RELIANCE",
                               help="Type any NSE ticker, e.g. TCS, DLF, SUNPHARMA. "
                                    "'.NS' is added automatically.")
        cost_bps = c2.number_input("Transaction cost (bps)", value=10.0,
                                   min_value=0.0, step=1.0)
        chart_img = st.file_uploader("Upload chart screenshot (from Downloads)",
                                     type=["png", "jpg", "jpeg"])
        run = st.form_submit_button("Run Quant Analysis", type="primary",
                                    use_container_width=True)

    if not run:
        st.info("Enter an NSE symbol, optionally upload its chart screenshot, "
                "then click **Run Quant Analysis**.")
        return

    if not symbol.strip():
        st.error("Please enter an NSE symbol.")
        return

    # ── Data ──
    with st.spinner(f"Fetching {symbol.upper()} history..."):
        df = fetch_history(symbol)
    if df.empty or len(df) < 60:
        st.error(f"Not enough data for {symbol.upper()}. Check the symbol and try again.")
        return

    # ── Math pipeline ──
    feats = build_features(df)
    d_opt, d_pval, _ = optimal_d(df["Close"])
    score, side, drivers = composite_score(feats)
    # if neutral, default barrier orientation to long for level display
    barrier_side = side if side != 0 else 1
    levels = triple_barrier_levels(feats, side=barrier_side, cost_bps=cost_bps)
    verdict = verdict_from(score, side)

    # ── Header verdict ──
    vcolor = {"BUY": "#22C55E", "SELL": "#EF4444", "HOLD": "#F59E0B"}[verdict]
    st.markdown(
        f"<div style='background:#11161D;border:1px solid #242D3A;border-left:5px solid {vcolor};"
        f"border-radius:12px;padding:18px 22px;margin:8px 0 18px;'>"
        f"<span style='font-size:13px;color:#8C97A8;letter-spacing:1px;'>VERDICT · {symbol.upper()}</span><br>"
        f"<span style='font-size:30px;font-weight:800;color:{vcolor};'>{verdict}</span>"
        f"<span style='font-size:18px;color:#E8ECF2;margin-left:14px;'>Composite Quant Score: "
        f"<b>{score}/100</b></span></div>", unsafe_allow_html=True)

    # ── Execution levels ──
    st.markdown("#### Execution Levels (1-Week Swing)")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Entry", f"Rs {levels['entry']:,.2f}")
    e2.metric("Profit Target", f"Rs {levels['target']:,.2f}")
    e3.metric("Stop-Loss", f"Rs {levels['stop']:,.2f}")
    e4.metric("Risk:Reward", f"{levels['rr']} : 1")
    st.caption(f"Max holding: {levels['max_hold_days']} business days · "
               f"ATR-based barriers · costs {levels['cost_bps']:.0f} bps included.")

    # ── Quant features ──
    st.markdown("#### Quantitative Features")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Price", f"Rs {feats['price']:,.2f}")
    f2.metric("RSI(14)", f"{feats['rsi']:.0f}")
    f3.metric("ATR(14)", f"{feats['atr']:.2f} ({feats['atr_pct']:.1f}%)")
    f4.metric("ROC(10)", f"{feats['roc_10']:+.1f}%")
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Daily Vol (20d)", f"{feats['daily_vol']:.2f}%")
    g2.metric("SMA 20 / 50", f"{feats['sma20']:.0f} / {feats['sma50']:.0f}")
    g3.metric("Support", f"Rs {feats['support']:,.2f}")
    g4.metric("Resistance", f"Rs {feats['resistance']:,.2f}")

    # ── Stationarity ──
    pval_txt = f"{d_pval:.4f}" if not np.isnan(d_pval) else "n/a"
    st.markdown("#### Stationarity (López de Prado, Ch.5)")
    st.write(f"Optimal fractional-differentiation **d = {d_opt:.2f}** "
             f"(ADF p-value = {pval_txt}). Lower d preserves more memory while staying stationary.")

    # ── Score drivers ──
    st.markdown("#### Score Drivers")
    for dr in drivers:
        st.markdown(f"- {dr}")

    # ── Price + SMA chart ──
    st.markdown("#### Price & Moving Averages")
    chart_df = pd.DataFrame({
        "Close": df["Close"],
        "SMA20": df["Close"].rolling(20).mean(),
        "SMA50": df["Close"].rolling(50).mean(),
    }).tail(250)
    st.line_chart(chart_df)

    # ── Gemini Vision cross-check ──
    st.markdown("#### Gemini Vision Cross-Check")
    if chart_img is None:
        st.info("Upload a chart screenshot above to enable the visual cross-check.")
    else:
        st.image(chart_img, caption=f"{symbol.upper()} — uploaded chart", use_column_width=True)
        api_key = st.secrets.get("GEMINI_KEY", "")
        if not api_key:
            st.warning("GEMINI_KEY not found in secrets. Add it to enable vision analysis.")
        else:
            with st.spinner("Gemini analyzing the chart vs. the math..."):
                try:
                    visual = gemini_chart_analysis(
                        chart_img.getvalue(), symbol.upper(), feats, api_key)
                    st.markdown(visual)
                except Exception as e:
                    st.error(f"Gemini vision failed: {e}")

    st.caption("Educational use only. Not investment advice. Trading involves risk.")
