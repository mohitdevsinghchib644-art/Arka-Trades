"""
quant_analysis.py — Arka Trades | Institutional Swing Analytics
================================================================
Self-contained. app.py calls:
    from quant_analysis import render_quant_analysis
    render_quant_analysis()

What real firms (Jane Street, Citadel) use for swing edge — adapted for NSE:

MODULE 1 — INSTITUTIONAL FOOTPRINT SCANNER (real yfinance data)
  · Volume Surge Detection   — 3x+ avg vol = big player entry signal
  · Volume Profile           — Point of Control, Value Area (where money sits)
  · VWAP Analysis            — institutional benchmark price
  · VPIN (Order Flow Toxicity) — are INFORMED players entering?
  · GARCH(1,1) Volatility    — real vol forecast, regime detection
  · Large Candle Analysis    — institution-sized directional moves
  · Accumulation/Distribution zones

MODULE 2 — MONTE CARLO RISK DASHBOARD
  · 1000 simulated price paths (Student-t fat tails)
  · Value at Risk (VaR 95% + 99%) in ₹ and %
  · Expected Shortfall (CVaR) — average of worst 5% outcomes
  · Probability of profit, upside/downside targets

MODULE 3 — SWING BACKTEST (1-2 week holds)
  · Entry: Volume surge + bullish close + trend + RSI filter
  · Exit: Target / Stop / Volume dry-up / MA flip / Time
  · Full performance: WR, PF, Sharpe, MDD, monthly P&L

Deps: streamlit numpy pandas scipy plotly yfinance
"""

from __future__ import annotations
import warnings, datetime
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import norm

warnings.filterwarnings("ignore")

# ── Palette ──────────────────────────────────────────────────────────────────
BG     = "#0B0E13"
PANEL  = "#11151D"
PANEL2 = "#161C26"
BORDER = "#222B38"
ACCENT = "#C9A227"
IVORY  = "#E8EDF2"
MUTE   = "#8593A3"
GREEN  = "#1FB97A"
RED    = "#E8554E"
BLUE   = "#4C8DD6"
PURPLE = "#9B59B6"
ORANGE = "#E67E22"
AMBER  = "#F59E0B"
TEAL   = "#14B8A6"
MONO   = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"

TRADING_DAYS = 252

SIGNAL_CFG = {
    "STRONG BUY" : {"color": GREEN,  "bg": "rgba(31,185,122,0.14)", "icon": "🟢"},
    "BUY"        : {"color": GREEN,  "bg": "rgba(31,185,122,0.09)", "icon": "🟩"},
    "WEAK BUY"   : {"color": TEAL,   "bg": "rgba(20,184,166,0.09)", "icon": "🔼"},
    "NEUTRAL"    : {"color": MUTE,   "bg": "rgba(133,147,163,0.09)","icon": "⬜"},
    "WEAK SELL"  : {"color": ORANGE, "bg": "rgba(230,126,34,0.09)", "icon": "🔽"},
    "SELL"       : {"color": RED,    "bg": "rgba(232,85,78,0.09)",  "icon": "🟥"},
    "STRONG SELL": {"color": RED,    "bg": "rgba(232,85,78,0.14)",  "icon": "🔴"},
}


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _layout(fig, title, height=400):
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        title=dict(text=title, font=dict(size=12, color=ACCENT)),
        height=height, margin=dict(l=8, r=8, t=40, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
    )
    return fig

def _sec(label):
    return (f"<div style='font-family:{MONO};font-size:11px;font-weight:600;"
            f"color:{ACCENT};letter-spacing:1.5px;margin:14px 0 6px;"
            f"border-bottom:1px solid {BORDER};padding-bottom:5px;'>{label}</div>")

def _mrow(cells):
    cols = st.columns(len(cells))
    for col, (lbl, val) in zip(cols, cells):
        col.metric(lbl, val)

def _fmt(v, suffix="", dp=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)): return "—"
    return f"{v:,.{dp}f}{suffix}"

def _signal_card(title, verdict, summary, score):
    cfg = SIGNAL_CFG.get(verdict, SIGNAL_CFG["NEUTRAL"])
    c, bg = cfg["color"], cfg["bg"]
    bars = "".join([f"<span style='display:inline-block;width:12px;height:5px;"
                    f"border-radius:2px;background:{c};margin-right:2px;'></span>"
                    for _ in range(min(abs(score), 5))])
    st.markdown(f"""
    <div style='background:{bg};border:1px solid {c}44;border-left:4px solid {c};
    border-radius:10px;padding:14px 18px;margin:10px 0;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
        <span style='font-family:{MONO};font-size:11px;font-weight:700;color:{c};
          letter-spacing:1.5px;'>{cfg["icon"]} {title}</span>
        <span style='font-family:{MONO};font-size:14px;font-weight:800;color:{c};'>{verdict}</span>
      </div>
      <div style='font-size:13px;color:{IVORY};line-height:1.7;margin-bottom:8px;'>{summary}</div>
      <div>{bars}<span style='font-family:{MONO};font-size:10px;color:{MUTE};margin-left:6px;'>
        Strength {abs(score)}/5</span></div>
    </div>""", unsafe_allow_html=True)

def _score_to_verdict(score, mx=5):
    r = score / mx if mx else 0
    if r >= 0.7: return "STRONG BUY"
    if r >= 0.35: return "BUY"
    if r > 0.1: return "WEAK BUY"
    if r > -0.1: return "NEUTRAL"
    if r > -0.35: return "WEAK SELL"
    if r > -0.7: return "SELL"
    return "STRONG SELL"


# ════════════════════════════════════════════════════════════════
# DATA FETCH
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock(symbol: str, period: str = "1y") -> pd.DataFrame:
    import yfinance as yf
    sym = symbol.strip().upper()
    if "^" not in sym and not sym.endswith(".NS"):
        sym += ".NS"
    for p in [period, "2y", "max"]:
        df = yf.Ticker(sym).history(period=p, interval="1d")
        if not df.empty and len(df) >= 60:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Open","High","Low","Close","Volume"]].dropna()
            df.index = pd.to_datetime(df.index)
            return df
    return pd.DataFrame()

@st.cache_data(ttl=900, show_spinner=False)
def fetch_range(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    sym = symbol.strip().upper()
    if "^" not in sym and not sym.endswith(".NS"):
        sym += ".NS"
    df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty: return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open","High","Low","Close","Volume"]].dropna()
    df.index = pd.to_datetime(df.index)
    return df


# ════════════════════════════════════════════════════════════════
# ENGINE 1: GARCH(1,1) VOLATILITY
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def garch_analysis(close: pd.Series, forecast_h: int = 10) -> dict:
    r = np.log(close / close.shift(1)).dropna().values
    omega = max(np.var(r) * 0.05, 1e-8)

    # Grid search MLE for alpha/beta
    best_ll, best_a, best_b = -np.inf, 0.09, 0.90
    for a in [0.05, 0.07, 0.09, 0.11, 0.13, 0.15]:
        for b in [0.80, 0.84, 0.87, 0.90, 0.92, 0.94]:
            if a + b >= 1.0: continue
            s2 = np.zeros(len(r)); s2[0] = np.var(r)
            for t in range(1, len(r)):
                s2[t] = omega + a * r[t-1]**2 + b * s2[t-1]
            ll = -0.5 * np.sum(np.log(s2[1:] + 1e-12) + r[1:]**2 / (s2[1:] + 1e-12))
            if ll > best_ll:
                best_ll = ll; best_a = a; best_b = b

    # Final pass
    s2 = np.zeros(len(r)); s2[0] = np.var(r)
    for t in range(1, len(r)):
        s2[t] = omega + best_a * r[t-1]**2 + best_b * s2[t-1]

    # Forecast
    fwd_var = [s2[-1]]
    for _ in range(forecast_h - 1):
        fwd_var.append(omega + (best_a + best_b) * fwd_var[-1])

    sigma_series  = pd.Series(np.sqrt(s2), index=close.index[1:])
    daily_vol     = float(np.sqrt(s2[-1]))
    annual_vol    = daily_vol * np.sqrt(TRADING_DAYS) * 100
    hist_avg_vol  = float(np.mean(np.sqrt(s2))) * np.sqrt(TRADING_DAYS) * 100
    pct_rank      = float(np.mean(np.sqrt(s2) <= np.sqrt(s2[-1])) * 100)

    regime = ("HIGH-VOL ⚠️" if pct_rank > 75 else
              "LOW-VOL 😴"  if pct_rank < 25 else "NORMAL 📊")

    return {
        "sigma_series" : sigma_series,
        "daily_vol_pct": round(daily_vol * 100, 3),
        "annual_vol_pct": round(annual_vol, 1),
        "hist_avg_annual": round(hist_avg_vol, 1),
        "percentile": round(pct_rank, 1),
        "regime": regime,
        "alpha": best_a, "beta": best_b,
        "forecast_daily": [round(float(np.sqrt(v)) * 100, 3) for v in fwd_var],
        "persistence": round(best_a + best_b, 3),
    }


# ════════════════════════════════════════════════════════════════
# ENGINE 2: MONTE CARLO RISK
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def monte_carlo_risk(price: float, daily_vol: float, mu: float = 0.0003,
                     horizon: int = 10, n_sims: int = 1000,
                     capital: float = 100_000, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    df_t = 5
    shocks = rng.standard_t(df_t, size=(n_sims, horizon))
    shocks /= np.sqrt(df_t / (df_t - 2))
    paths  = price * np.exp(np.cumsum(mu + daily_vol * shocks, axis=1))
    final  = paths[:, -1]
    pnl_rs = (final - price) / price * capital

    var95  = float(np.percentile(pnl_rs, 5))
    es95   = float(pnl_rs[pnl_rs <= var95].mean())
    var99  = float(np.percentile(pnl_rs, 1))
    es99   = float(pnl_rs[pnl_rs <= var99].mean())

    return {
        "paths"        : paths,
        "final"        : final,
        "var95_rs"     : round(var95, 0),
        "es95_rs"      : round(es95, 0),
        "var99_rs"     : round(var99, 0),
        "es99_rs"      : round(es99, 0),
        "var95_pct"    : round(float(np.percentile((final - price)/price, 5)) * 100, 2),
        "var99_pct"    : round(float(np.percentile((final - price)/price, 1)) * 100, 2),
        "prob_up_pct"  : round(float((final > price).mean()) * 100, 1),
        "median_price" : round(float(np.median(final)), 2),
        "p90_upside"   : round(float(np.percentile(final, 90)), 2),
        "p10_downside" : round(float(np.percentile(final, 10)), 2),
        "p50"          : round(float(np.percentile(final, 50)), 2),
        "horizon"      : horizon,
        "n_sims"       : n_sims,
        "capital"      : capital,
    }


# ════════════════════════════════════════════════════════════════
# ENGINE 3: VOLUME PROFILE
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def volume_profile(df: pd.DataFrame, n_bins: int = 28) -> dict:
    lo_p = float(df["Low"].min()); hi_p = float(df["High"].max())
    if hi_p <= lo_p: hi_p = lo_p + 1
    bins = np.linspace(lo_p, hi_p, n_bins + 1)
    vol_bins = np.zeros(n_bins)
    for _, row in df.iterrows():
        lo, hi, vol = row["Low"], row["High"], row["Volume"]
        rng = hi - lo if hi != lo else 1e-8
        for i in range(n_bins):
            overlap = max(0, min(hi, bins[i+1]) - max(lo, bins[i]))
            vol_bins[i] += vol * overlap / rng

    mid = (bins[:-1] + bins[1:]) / 2
    poc_idx = int(np.argmax(vol_bins))
    poc = float(mid[poc_idx])

    # Value Area (70% of volume)
    total = vol_bins.sum(); cum = vol_bins[poc_idx]
    lo_i = poc_idx; hi_i = poc_idx
    while cum < total * 0.70:
        can_lo = lo_i > 0; can_hi = hi_i < n_bins - 1
        if can_lo and can_hi:
            if vol_bins[lo_i-1] >= vol_bins[hi_i+1]:
                lo_i -= 1; cum += vol_bins[lo_i]
            else:
                hi_i += 1; cum += vol_bins[hi_i]
        elif can_lo: lo_i -= 1; cum += vol_bins[lo_i]
        elif can_hi: hi_i += 1; cum += vol_bins[hi_i]
        else: break

    # High-Volume Nodes (HVN) and Low-Volume Nodes (LVN)
    threshold_hvn = np.percentile(vol_bins, 75)
    threshold_lvn = np.percentile(vol_bins, 25)
    hvn = [float(mid[i]) for i in range(n_bins) if vol_bins[i] >= threshold_hvn]
    lvn = [float(mid[i]) for i in range(n_bins) if vol_bins[i] <= threshold_lvn]

    return {
        "poc"       : poc,
        "va_low"    : float(bins[lo_i]),
        "va_high"   : float(bins[hi_i + 1]),
        "vol_bins"  : vol_bins,
        "price_mid" : mid,
        "hvn"       : hvn,
        "lvn"       : lvn,
    }


# ════════════════════════════════════════════════════════════════
# ENGINE 4: VWAP
# ════════════════════════════════════════════════════════════════

def compute_vwap(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    df = df.copy()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_tv = (tp * df["Volume"]).rolling(lookback).sum()
    cum_v  = df["Volume"].rolling(lookback).sum()
    df["vwap"] = cum_tv / cum_v.replace(0, np.nan)
    # VWAP bands (1 std)
    dev = (tp - df["vwap"]).rolling(lookback).std()
    df["vwap_upper"] = df["vwap"] + dev
    df["vwap_lower"] = df["vwap"] - dev
    return df


# ════════════════════════════════════════════════════════════════
# ENGINE 5: VPIN (Order Flow Toxicity)
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def compute_vpin(df: pd.DataFrame, n_buckets: int = 50) -> dict:
    close = df["Close"].values
    volume = df["Volume"].values
    r = np.diff(np.log(np.where(close > 0, close, 1e-8)))
    sigma = max(np.std(r), 1e-8)

    buy_v = []; sell_v = []
    for i in range(len(r)):
        pb = float(norm.cdf(r[i] / sigma))
        v = float(volume[i+1])
        buy_v.append(v * pb); sell_v.append(v * (1 - pb))

    vpin_vals = []
    w = min(n_buckets, len(buy_v))
    for i in range(w, len(buy_v)):
        bv = sum(buy_v[i-w:i]); sv = sum(sell_v[i-w:i]); tv = bv + sv
        vpin_vals.append(abs(bv - sv) / tv if tv > 0 else 0)

    cur = float(vpin_vals[-1]) if vpin_vals else 0.0
    avg = float(np.mean(vpin_vals)) if vpin_vals else 0.0
    pct = float(np.mean(np.array(vpin_vals) <= cur) * 100) if vpin_vals else 50.0
    series = pd.Series(vpin_vals, index=df.index[w+1:len(vpin_vals)+w+1])

    toxicity = "HIGH 🔥" if pct > 70 else ("MODERATE ⚠️" if pct > 40 else "LOW ✅")
    return {
        "current": round(cur, 4), "avg": round(avg, 4),
        "percentile": round(pct, 1), "toxicity": toxicity,
        "series": series,
        "meaning": (
            "INFORMED traders dominating order flow — expect a BIG DIRECTIONAL move soon. "
            "Smart money is taking positions. This is the Jane Street signal."
            if pct > 70 else
            "Mixed order flow. Some informed activity but no clear conviction yet. "
            "Wait for VPIN to rise further before acting."
            if pct > 40 else
            "Order flow dominated by NOISE traders (retail). "
            "Low conviction move. Big players are not engaged here."
        ),
    }


# ════════════════════════════════════════════════════════════════
# ENGINE 6: INSTITUTIONAL FOOTPRINT
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def institutional_footprint(df: pd.DataFrame,
                             vol_mult: float = 2.5,
                             lookback: int = 20) -> dict:
    avg_vol = df["Volume"].rolling(lookback).mean()
    avg_rng = (df["High"] - df["Low"]).rolling(lookback).mean()
    df2 = df.copy()
    df2["vol_ratio"] = df2["Volume"] / avg_vol
    df2["avg_vol"]   = avg_vol
    df2["rng_ratio"] = (df2["High"] - df2["Low"]) / avg_rng.replace(0, np.nan)
    df2 = df2.dropna()

    events = []
    for i in range(len(df2)):
        row  = df2.iloc[i]
        vr   = float(row["vol_ratio"])
        bull = row["Close"] > row["Open"]
        rng  = row["High"] - row["Low"]
        body = abs(row["Close"] - row["Open"])
        br   = body / rng if rng > 0 else 0
        pc   = abs(row["Close"] - row["Open"]) / row["Open"] if row["Open"] > 0 else 0
        strength = min(5, int(vr / vol_mult * 2 + row["rng_ratio"]))

        if vr >= vol_mult:
            if bull and br > 0.4:
                etype = "BULL SURGE 🐂"
            elif not bull and br > 0.4:
                etype = "BEAR SURGE 🐻"
            elif pc < 0.003 and bull:
                etype = "ACCUMULATION 📦"
            elif pc < 0.003 and not bull:
                etype = "DISTRIBUTION 📤"
            else:
                etype = "SURGE (direction unclear) ❓"
            events.append({
                "date"       : df2.index[i],
                "type"       : etype,
                "price"      : round(float(row["Close"]), 2),
                "vol_ratio"  : round(vr, 2),
                "body_ratio" : round(br, 2),
                "bullish"    : bull,
                "strength"   : strength,
                "volume"     : int(row["Volume"]),
                "avg_volume" : int(row["avg_vol"]),
            })

    events_df = pd.DataFrame(events)
    recent    = events_df.tail(5) if len(events_df) else pd.DataFrame()

    # Directional bias from last 3 signals
    if len(events_df) >= 3:
        last3 = events_df.tail(3)
        bull_count = last3["bullish"].sum()
        bias_score = int(bull_count * 2 - 3)   # -3 to +3
    else:
        bias_score = 0

    return {
        "events"     : events_df,
        "recent"     : recent,
        "n_events"   : len(events_df),
        "bias_score" : bias_score,
        "verdict"    : _score_to_verdict(bias_score, mx=3),
        "vol_series" : df2["vol_ratio"],
        "avg_vol"    : float(avg_vol.iloc[-1]) if not avg_vol.empty else 0,
        "last_ratio" : float(df2["vol_ratio"].iloc[-1]) if len(df2) else 0,
    }


# ════════════════════════════════════════════════════════════════
# ENGINE 7: SWING BACKTEST
# ════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def swing_backtest(df: pd.DataFrame, vol_mult: float = 2.5,
                   lookback: int = 20, hold_days: int = 7,
                   stop_pct: float = 0.04, target_pct: float = 0.08,
                   pos_pct: float = 0.20, commission: float = 0.0015,
                   slippage: float = 0.0005) -> dict:
    if df.empty or len(df) < 60:
        return {"error": "Need at least 60 bars. Try a longer date range."}

    df2 = df.copy()
    df2["avg_vol"] = df2["Volume"].rolling(lookback).mean()
    df2["vol_ratio"] = df2["Volume"] / df2["avg_vol"]
    df2["ma20"] = df2["Close"].rolling(20).mean()
    df2["ma50"] = df2["Close"].rolling(50).mean()
    df2["ema10"] = df2["Close"].ewm(span=10, adjust=False).mean()
    delta = df2["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df2["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    hl = df2["High"] - df2["Low"]
    hc = (df2["High"] - df2["Close"].shift()).abs()
    lc = (df2["Low"] - df2["Close"].shift()).abs()
    df2["atr"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    df2 = df2.dropna()
    if len(df2) < 30:
        return {"error": "Too many NaN — try a wider date range (2+ years)."}

    equity = 1_000_000; trades = []; equity_curve = []
    position = None; hold_count = 0

    for i, (date, row) in enumerate(df2.iterrows()):
        price = float(row["Close"])
        equity_curve.append({"date": date, "equity": equity})

        if position is None:
            vr     = float(row["vol_ratio"])
            bull   = row["Close"] > row["Open"]
            trend  = price > float(row["ma50"])
            rsi_ok = 35 < float(row["rsi"]) < 75
            # Entry: volume surge + bullish + above 50MA + RSI healthy
            if vr >= vol_mult and bull and trend and rsi_ok:
                ep     = price * (1 + slippage)
                shares = (equity * pos_pct) / ep
                position = {"ep": ep, "shares": shares, "date": date,
                            "cost_in": equity * pos_pct * commission}
                hold_count = 0
        else:
            hold_count += 1
            ep = position["ep"]
            at_stop   = price <= ep * (1 - stop_pct)
            at_target = price >= ep * (1 + target_pct)
            at_time   = hold_count >= hold_days
            vol_dry   = float(row["vol_ratio"]) < 0.55 and hold_count >= 3
            ma_flip   = price < float(row["ma20"]) and hold_count >= 2

            if at_stop or at_target or at_time or vol_dry or ma_flip or i == len(df2)-1:
                xp      = price * (1 - slippage)
                cost_out = position["shares"] * xp * commission
                pnl     = position["shares"] * (xp - ep) - position["cost_in"] - cost_out
                equity  += pnl
                reason  = ("Target 🎯" if at_target else "Stop 🛑" if at_stop else
                           "VolDry 📉" if vol_dry else "MAFlip 🔄" if ma_flip else "Time ⏱")
                trades.append({
                    "entry_date": str(position["date"])[:10],
                    "exit_date" : str(date)[:10],
                    "entry"     : round(ep, 2),
                    "exit"      : round(xp, 2),
                    "pnl"       : round(pnl, 2),
                    "pnl_pct"   : round((xp/ep - 1)*100, 2),
                    "hold_days" : hold_count,
                    "exit_reason": reason,
                    "outcome"   : "WIN" if pnl > 0 else "LOSS",
                })
                position = None; hold_count = 0

    if not trades:
        return {"error": f"No trades triggered. "
                f"Try lowering vol_mult (currently {vol_mult}x) or using a longer date range."}

    tdf  = pd.DataFrame(trades)
    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_ser = eq_df["equity"]
    rets = eq_ser.pct_change().dropna()
    wins = tdf[tdf["outcome"]=="WIN"]; losses = tdf[tdf["outcome"]=="LOSS"]
    wr   = len(wins)/len(tdf)*100
    pf   = (abs(wins["pnl"].sum() / losses["pnl"].sum())
            if len(losses) and losses["pnl"].sum() != 0 else 99.0)
    ann_r = float(rets.mean() * TRADING_DAYS)
    ann_v = float(rets.std() * np.sqrt(TRADING_DAYS))
    dv    = rets[rets < 0].std() * np.sqrt(TRADING_DAYS)
    sharpe  = ann_r / ann_v if ann_v > 0 else 0
    sortino = ann_r / dv    if dv   > 0 else 0
    dd = (eq_ser - eq_ser.cummax()) / eq_ser.cummax() * 100
    tot_r = (equity - 1_000_000) / 1_000_000 * 100
    max_cl = cl = 0
    for o in tdf["outcome"].values:
        if o=="LOSS": cl+=1; max_cl=max(max_cl,cl)
        else: cl=0
    monthly = tdf.copy()
    monthly["month"] = pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    mpnl = monthly.groupby("month")["pnl"].sum()

    strat_ok = pf >= 1.3 and wr >= 45 and sharpe >= 0.3
    strat_ok2 = pf >= 1.0 and wr >= 40
    verdict = ("✅ STRATEGY VALIDATED" if strat_ok else
               "⚠️ MARGINAL EDGE"      if strat_ok2 else
               "❌ STRATEGY FAILED")

    return {
        "trades": tdf, "equity_curve": eq_df, "drawdown": dd, "monthly_pnl": mpnl,
        "total_trades": len(tdf), "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2), "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3), "max_dd": round(float(dd.min()), 2),
        "total_ret": round(tot_r, 2), "final_equity": round(equity, 2),
        "avg_win": round(wins["pnl_pct"].mean(), 2) if len(wins) else 0,
        "avg_loss": round(losses["pnl_pct"].mean(), 2) if len(losses) else 0,
        "rr": round(abs(wins["pnl_pct"].mean()/losses["pnl_pct"].mean()), 2)
              if len(losses) and losses["pnl_pct"].mean() != 0 else 0,
        "max_consec_loss": max_cl, "strat_verdict": verdict,
        "ann_ret": round(ann_r*100, 2), "ann_vol": round(ann_v*100, 2),
    }


# ════════════════════════════════════════════════════════════════
# AI NARRATIVE
# ════════════════════════════════════════════════════════════════

def _ai_summary(symbol, price, vpin, garch, footprint, vp, api_key):
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "You are a senior NSE swing trading analyst. "
                "Give direct, actionable analysis in under 120 words. "
                "No fluff. Educational only — not SEBI advice."
            )
        )
        prompt = f"""
 Stock: {symbol}  Price: ₹{price:.2f}
 VPIN toxicity: {vpin['toxicity']} ({vpin['current']:.3f}, {vpin['percentile']:.0f}th pct)
 GARCH vol: {garch['daily_vol_pct']:.2f}%/day  Annual: {garch['annual_vol_pct']:.1f}%  Regime: {garch['regime']}
 Institutional events (last 5): {footprint['n_events']} surges detected
 Bias score: {footprint['bias_score']:+d}/3  Verdict: {footprint['verdict']}
 Volume Profile POC: ₹{vp['poc']:.0f}  Value Area: ₹{vp['va_low']:.0f}–₹{vp['va_high']:.0f}

 In 3-4 sentences: What is the smart money doing? Is this a good swing trade setup? What levels matter?
 """
        return model.generate_content(prompt).text.strip()
    except:
        return ""


def _rule_narrative(symbol, price, vpin, garch, footprint, vp):
    lines = []
    # VPIN
    if vpin["percentile"] > 70:
        lines.append(f"⚡ <b>Informed money is active in {symbol}</b> — VPIN at {vpin['percentile']:.0f}th percentile signals institutional order flow. A large directional move is likely immin")
    elif vpin["percentile"] < 30:
        lines.append(f"😴 Order flow in {symbol} is dominated by retail noise. No institutional conviction visible yet — avoid chasing.")
    else:
        lines.append(f"📊 {symbol} shows mixed order flow — some informed activity but no strong institutional conviction yet.")

    # Vol regime
    if "HIGH" in garch["regime"]:
        lines.append(f"⚠️ Volatility is elevated ({garch['annual_vol_pct']:.0f}% annual, {garch['percentile']:.0f}th pct) — size positions smaller, widen stops.")
    elif "LOW" in garch["regime"]:
        lines.append(f"📉 Volatility is compressed ({garch['annual_vol_pct']:.0f}% annual) — often precedes a sharp move. Watch for a vol expansion trigger.")
    else:
        lines.append(f"📊 Volatility normal at {garch['annual_vol_pct']:.0f}% annual — standard position sizing applies.")

    # Footprint
    if footprint["n_events"] > 0 and footprint["bias_score"] > 0:
        lines.append(f"🐂 Institutional footprint is <b>bullish</b> — {footprint['n_events']} volume surges detected, majority bullish. POC at ₹{vp['poc']:,.0f} is the key support level.")
    elif footprint["n_events"] > 0 and footprint["bias_score"] < 0:
        lines.append(f"🐻 Institutional footprint is <b>bearish</b> — distribution detected. Resistance expected near ₹{vp['va_high']:,.0f} (Value Area High).")
    else:
        lines.append(f"Value Area ₹{vp['va_low']:,.0f}–₹{vp['va_high']:,.0f} · POC ₹{vp['poc']:,.0f}.")

    lines.append(f"<span style='font-size:11px;color:{MUTE};'>Educational analysis · Not SEBI investment advice</span>")
    return " ".join(lines)


# ════════════════════════════════════════════════════════════════
# PLOTLY CHARTS
# ════════════════════════════════════════════════════════════════

def chart_price_vwap(df, vwap_df, vp, footprint, symbol):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)
    d = df.tail(120)
    vd = vwap_df.tail(120)

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        name="Price", showlegend=False), row=1, col=1)

    # VWAP + bands
    fig.add_trace(go.Scatter(x=vd.index, y=vd["vwap"], line=dict(color=ACCENT, width=1.8, dash="dot"),
                             name="VWAP"), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=list(vd.index) + list(vd.index[::-1]),
        y=list(vd["vwap_upper"]) + list(vd["vwap_lower"][::-1]),
        fill="toself", fillcolor=f"rgba(201,162,39,0.07)",
        line=dict(color="rgba(0,0,0,0)"), name="VWAP ±1σ"), row=1, col=1)

    # POC and Value Area
    fig.add_hline(y=vp["poc"], line_color=PURPLE, line_dash="dash", line_width=1.5,
                  annotation_text=f"POC ₹{vp['poc']:,.0f}", annotation_font_color=PURPLE,
                  row=1, col=1)
    fig.add_hrect(y0=vp["va_low"], y1=vp["va_high"],
                  fillcolor="rgba(75,139,214,0.07)", line_width=0, row=1, col=1)

    # Volume surge markers
    ev = footprint["events"]
    if not ev.empty:
        recent_ev = ev[ev["date"].isin(d.index)]
        if not recent_ev.empty:
            for _, e in recent_ev.iterrows():
                c = GREEN if e["bullish"] else RED
                if e["date"] in d.index:
                    fig.add_trace(go.Scatter(
                        x=[e["date"]], y=[d.loc[e["date"], "Low"] * 0.995 if e["bullish"]
                                          else d.loc[e["date"], "High"] * 1.005],
                        mode="markers",
                        marker=dict(color=c, size=10,
                                    symbol="triangle-up" if e["bullish"] else "triangle-down"),
                        name="Surge", showlegend=False), row=1, col=1)

    # Volume bars
    v_colors = [GREEN if d["Close"].iloc[i] >= d["Open"].iloc[i] else RED for i in range(len(d))]
    avg_v = d["Volume"].mean()
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=v_colors,
                         opacity=0.6, name="Volume", showlegend=False), row=2, col=1)
    fig.add_hline(y=avg_v*2.5, line_color=ACCENT, line_dash="dot", line_width=1,
                  annotation_text="Surge threshold", annotation_font_color=ACCENT, row=2, col=1)

    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        height=560, margin=dict(l=8, r=8, t=40, b=8),
        title=dict(text=f"{symbol} · PRICE · VWAP · VOLUME PROFILE · SURGE EVENTS",
                   font=dict(size=12, color=ACCENT)),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis2=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        yaxis2=dict(gridcolor=BORDER), xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", y=1.02, x=0, font=dict(size=10)),
    )
    return fig


def chart_volume_profile(vp):
    fig = go.Figure()
    colors = [ACCENT if abs(p - vp["poc"]) < (vp["va_high"] - vp["va_low"]) * 0.05
              else BLUE if vp["va_low"] <= p <= vp["va_high"]
              else MUTE for p in vp["price_mid"]]
    fig.add_trace(go.Bar(
        y=[f"₹{p:,.0f}" for p in vp["price_mid"]],
        x=vp["vol_bins"], orientation="h",
        marker_color=colors, opacity=0.85, name="Volume at Price"))
    fig.add_vline(x=0, line_color=BORDER, line_width=1)
    return _layout(fig, "VOLUME PROFILE · Gold=POC · Blue=Value Area · Gray=Outside", height=480)


def chart_garch(garch, close):
    sigma = garch["sigma_series"].tail(250) * np.sqrt(TRADING_DAYS) * 100
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=["Close Price", "GARCH Conditional Volatility (Annualised %)"]
