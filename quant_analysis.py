"""
quant_analysis.py — Arka Trades | Institutional Swing Analytics
================================================================
Self-contained. app.py calls:
    from quant_analysis import render_quant_analysis
    render_quant_analysis()
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
    if r >= 0.7:   return "STRONG BUY"
    if r >= 0.35:  return "BUY"
    if r > 0.1:    return "WEAK BUY"
    if r > -0.1:   return "NEUTRAL"
    if r > -0.35:  return "WEAK SELL"
    if r > -0.7:   return "SELL"
    return "STRONG SELL"


@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock(symbol: str, period: str = "1y") -> pd.DataFrame:
    import yfinance as yf
    sym = symbol.strip().upper()
    if "^" not in sym and not sym.endswith(".NS"):
        sym += ".NS"
    for p in [period, "2y", "max"]:
        try:
            df = yf.Ticker(sym).history(period=p, interval="1d")
            if not df.empty and len(df) >= 60:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
                df = df[cols].dropna()
                df.index = pd.to_datetime(df.index).tz_localize(None)
                return df
        except Exception:
            continue
    return pd.DataFrame()

@st.cache_data(ttl=900, show_spinner=False)
def fetch_range(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    sym = symbol.strip().upper()
    if "^" not in sym and not sym.endswith(".NS"):
        sym += ".NS"
    try:
        df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty: return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
        df = df[cols].dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def garch_analysis(close: pd.Series, forecast_h: int = 10) -> dict:
    r = np.log(close / close.shift(1)).dropna().values
    omega = max(np.var(r) * 0.05, 1e-8)
    best_ll, best_a, best_b = -np.inf, 0.09, 0.90
    for a in [0.05, 0.07, 0.09, 0.11, 0.13, 0.15]:
        for b in [0.80, 0.84, 0.87, 0.90, 0.92, 0.94]:
            if a + b >= 1.0: continue
            s2 = np.zeros(len(r)); s2[0] = max(np.var(r), 1e-10)
            for t in range(1, len(r)):
                s2[t] = omega + a * r[t-1]**2 + b * s2[t-1]
            ll = -0.5 * np.sum(np.log(s2[1:] + 1e-12) + r[1:]**2 / (s2[1:] + 1e-12))
            if np.isfinite(ll) and ll > best_ll:
                best_ll = ll; best_a = a; best_b = b
    s2 = np.zeros(len(r)); s2[0] = max(np.var(r), 1e-10)
    for t in range(1, len(r)):
        s2[t] = omega + best_a * r[t-1]**2 + best_b * s2[t-1]
    fwd_var = [s2[-1]]
    for _ in range(forecast_h - 1):
        fwd_var.append(omega + (best_a + best_b) * fwd_var[-1])
    sigma_series = pd.Series(np.sqrt(s2), index=close.index[1:])
    daily_vol    = float(np.sqrt(max(s2[-1], 1e-10)))
    annual_vol   = daily_vol * np.sqrt(TRADING_DAYS) * 100
    hist_avg_vol = float(np.mean(np.sqrt(np.maximum(s2, 1e-10)))) * np.sqrt(TRADING_DAYS) * 100
    pct_rank     = float(np.mean(np.sqrt(s2) <= np.sqrt(s2[-1])) * 100)
    regime = ("HIGH-VOL ⚠️" if pct_rank > 75 else "LOW-VOL 😴" if pct_rank < 25 else "NORMAL 📊")
    return {
        "sigma_series":   sigma_series,
        "daily_vol_pct":  round(daily_vol * 100, 3),
        "annual_vol_pct": round(annual_vol, 1),
        "hist_avg_annual":round(hist_avg_vol, 1),
        "percentile":     round(pct_rank, 1),
        "regime":         regime,
        "alpha":          best_a,
        "beta":           best_b,
        "forecast_daily": [round(float(np.sqrt(max(v,1e-10))) * 100, 3) for v in fwd_var],
        "persistence":    round(best_a + best_b, 3),
    }


@st.cache_data(ttl=900, show_spinner=False)
def monte_carlo_risk(price: float, daily_vol: float, mu: float = 0.0003,
                     horizon: int = 10, n_sims: int = 1000,
                     capital: float = 100_000, seed: int = 42) -> dict:
    rng    = np.random.default_rng(seed)
    df_t   = 5
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
        "paths":        paths,
        "final":        final,
        "var95_rs":     round(var95, 0),
        "es95_rs":      round(es95, 0),
        "var99_rs":     round(var99, 0),
        "es99_rs":      round(es99, 0),
        "var95_pct":    round(float(np.percentile((final - price)/price, 5)) * 100, 2),
        "var99_pct":    round(float(np.percentile((final - price)/price, 1)) * 100, 2),
        "prob_up_pct":  round(float((final > price).mean()) * 100, 1),
        "median_price": round(float(np.median(final)), 2),
        "p90_upside":   round(float(np.percentile(final, 90)), 2),
        "p10_downside": round(float(np.percentile(final, 10)), 2),
        "p50":          round(float(np.percentile(final, 50)), 2),
        "horizon":      horizon,
        "n_sims":       n_sims,
        "capital":      capital,
    }


@st.cache_data(ttl=900, show_spinner=False)
def volume_profile(df: pd.DataFrame, n_bins: int = 28) -> dict:
    lo_p = float(df["Low"].min()); hi_p = float(df["High"].max())
    if hi_p <= lo_p: hi_p = lo_p + 1
    bins = np.linspace(lo_p, hi_p, n_bins + 1)
    vol_bins = np.zeros(n_bins)
    for _, row in df.iterrows():
        lo, hi, vol = float(row["Low"]), float(row["High"]), float(row["Volume"])
        rng = hi - lo if hi != lo else 1e-8
        for i in range(n_bins):
            overlap = max(0.0, min(hi, bins[i+1]) - max(lo, bins[i]))
            vol_bins[i] += vol * overlap / rng
    mid = (bins[:-1] + bins[1:]) / 2
    poc_idx = int(np.argmax(vol_bins))
    poc = float(mid[poc_idx])
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
    threshold_hvn = np.percentile(vol_bins, 75)
    threshold_lvn = np.percentile(vol_bins, 25)
    hvn = [float(mid[i]) for i in range(n_bins) if vol_bins[i] >= threshold_hvn]
    lvn = [float(mid[i]) for i in range(n_bins) if vol_bins[i] <= threshold_lvn]
    return {
        "poc":       poc,
        "va_low":    float(bins[lo_i]),
        "va_high":   float(bins[min(hi_i + 1, n_bins)]),
        "vol_bins":  vol_bins,
        "price_mid": mid,
        "hvn":       hvn,
        "lvn":       lvn,
    }


def compute_vwap(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    df = df.copy()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_tv = (tp * df["Volume"]).rolling(lookback).sum()
    cum_v  = df["Volume"].rolling(lookback).sum()
    df["vwap"] = cum_tv / cum_v.replace(0, np.nan)
    dev = (tp - df["vwap"]).rolling(lookback).std()
    df["vwap_upper"] = df["vwap"] + dev
    df["vwap_lower"] = df["vwap"] - dev
    return df


@st.cache_data(ttl=900, show_spinner=False)
def compute_vpin(df: pd.DataFrame, n_buckets: int = 50) -> dict:
    close  = df["Close"].values.astype(float)
    volume = df["Volume"].values.astype(float)
    safe   = np.where(close > 0, close, 1e-8)
    r      = np.diff(np.log(safe))
    sigma  = max(float(np.std(r)), 1e-8)
    buy_v  = []; sell_v = []
    for i in range(len(r)):
        pb = float(norm.cdf(r[i] / sigma))
        v  = float(volume[i+1])
        buy_v.append(v * pb); sell_v.append(v * (1.0 - pb))
    vpin_vals = []
    w = min(n_buckets, len(buy_v))
    for i in range(w, len(buy_v)):
        bv = sum(buy_v[i-w:i]); sv = sum(sell_v[i-w:i]); tv = bv + sv
        vpin_vals.append(abs(bv - sv) / tv if tv > 0 else 0.0)
    cur = float(vpin_vals[-1]) if vpin_vals else 0.0
    avg = float(np.mean(vpin_vals)) if vpin_vals else 0.0
    pct = float(np.mean(np.array(vpin_vals) <= cur) * 100) if vpin_vals else 50.0
    idx_start = w + 1
    idx_end   = len(vpin_vals) + w + 1
    safe_idx  = df.index[idx_start:min(idx_end, len(df.index))]
    safe_vals = vpin_vals[:len(safe_idx)]
    series = pd.Series(safe_vals, index=safe_idx)
    toxicity = "HIGH 🔥" if pct > 70 else ("MODERATE ⚠️" if pct > 40 else "LOW ✅")
    meaning = (
        "INFORMED traders dominating order flow — expect a BIG DIRECTIONAL move soon. "
        "Smart money is taking positions. This is the Jane Street signal."
        if pct > 70 else
        "Mixed order flow. Some informed activity but no clear conviction yet. "
        "Wait for VPIN to rise further before acting."
        if pct > 40 else
        "Order flow dominated by NOISE traders (retail). "
        "Low conviction move. Big players are not engaged here."
    )
    return {
        "current":    round(cur, 4),
        "avg":        round(avg, 4),
        "percentile": round(pct, 1),
        "toxicity":   toxicity,
        "series":     series,
        "meaning":    meaning,
    }


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
        row      = df2.iloc[i]
        vr       = float(row["vol_ratio"])
        bull     = float(row["Close"]) > float(row["Open"])
        rng_val  = float(row["High"]) - float(row["Low"])
        body     = abs(float(row["Close"]) - float(row["Open"]))
        br       = body / rng_val if rng_val > 0 else 0.0
        pc       = body / float(row["Open"]) if float(row["Open"]) > 0 else 0.0
        strength = min(5, int(vr / vol_mult * 2 + float(row["rng_ratio"])))
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
                etype = "SURGE (unclear) ❓"
            events.append({
                "date":       df2.index[i],
                "type":       etype,
                "price":      round(float(row["Close"]), 2),
                "vol_ratio":  round(vr, 2),
                "body_ratio": round(br, 2),
                "bullish":    bull,
                "strength":   strength,
                "volume":     int(row["Volume"]),
                "avg_volume": int(row["avg_vol"]),
            })
    events_df = pd.DataFrame(events) if events else pd.DataFrame()
    recent    = events_df.tail(5) if len(events_df) else pd.DataFrame()
    if len(events_df) >= 3:
        last3      = events_df.tail(3)
        bull_count = int(last3["bullish"].sum())
        bias_score = bull_count * 2 - 3
    else:
        bias_score = 0
    return {
        "events":     events_df,
        "recent":     recent,
        "n_events":   len(events_df),
        "bias_score": bias_score,
        "verdict":    _score_to_verdict(bias_score, mx=3),
        "vol_series": df2["vol_ratio"],
        "avg_vol":    float(avg_vol.iloc[-1]) if not avg_vol.empty else 0.0,
        "last_ratio": float(df2["vol_ratio"].iloc[-1]) if len(df2) else 0.0,
    }


@st.cache_data(ttl=300, show_spinner=False)
def swing_backtest(df: pd.DataFrame, vol_mult: float = 2.5,
                   lookback: int = 20, hold_days: int = 7,
                   stop_pct: float = 0.04, target_pct: float = 0.08,
                   pos_pct: float = 0.20, commission: float = 0.0015,
                   slippage: float = 0.0005) -> dict:
    if df.empty or len(df) < 60:
        return {"error": "Need at least 60 bars. Try a longer date range."}
    df2 = df.copy()
    df2["avg_vol"]   = df2["Volume"].rolling(lookback).mean()
    df2["vol_ratio"] = df2["Volume"] / df2["avg_vol"]
    df2["ma20"]      = df2["Close"].rolling(20).mean()
    df2["ma50"]      = df2["Close"].rolling(50).mean()
    delta = df2["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df2["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    hl = df2["High"] - df2["Low"]
    hc = (df2["High"] - df2["Close"].shift()).abs()
    lc = (df2["Low"]  - df2["Close"].shift()).abs()
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
            bull   = float(row["Close"]) > float(row["Open"])
            trend  = price > float(row["ma50"])
            rsi_ok = 35 < float(row["rsi"]) < 75
            if vr >= vol_mult and bull and trend and rsi_ok:
                ep     = price * (1.0 + slippage)
                shares = (equity * pos_pct) / ep
                position = {
                    "ep": ep, "shares": shares, "date": date,
                    "cost_in": equity * pos_pct * commission,
                }
                hold_count = 0
        else:
            hold_count += 1
            ep        = position["ep"]
            at_stop   = price <= ep * (1.0 - stop_pct)
            at_target = price >= ep * (1.0 + target_pct)
            at_time   = hold_count >= hold_days
            vol_dry   = float(row["vol_ratio"]) < 0.55 and hold_count >= 3
            ma_flip   = price < float(row["ma20"]) and hold_count >= 2
            if at_stop or at_target or at_time or vol_dry or ma_flip or i == len(df2)-1:
                xp       = price * (1.0 - slippage)
                cost_out = position["shares"] * xp * commission
                pnl      = position["shares"] * (xp - ep) - position["cost_in"] - cost_out
                equity  += pnl
                reason   = ("Target 🎯" if at_target else "Stop 🛑" if at_stop else
                            "VolDry 📉" if vol_dry else "MAFlip 🔄" if ma_flip else "Time ⏱")
                trades.append({
                    "entry_date":  str(position["date"])[:10],
                    "exit_date":   str(date)[:10],
                    "entry":       round(ep, 2),
                    "exit":        round(xp, 2),
                    "pnl":         round(pnl, 2),
                    "pnl_pct":     round((xp/ep - 1)*100, 2),
                    "hold_days":   hold_count,
                    "exit_reason": reason,
                    "outcome":     "WIN" if pnl > 0 else "LOSS",
                })
                position = None; hold_count = 0
    if not trades:
        return {"error": (f"No trades triggered. Try lowering vol_mult "
                          f"(currently {vol_mult}x) or use a longer date range.")}
    tdf    = pd.DataFrame(trades)
    eq_df  = pd.DataFrame(equity_curve).set_index("date")
    eq_ser = eq_df["equity"]
    rets   = eq_ser.pct_change().dropna()
    wins   = tdf[tdf["outcome"]=="WIN"]; losses = tdf[tdf["outcome"]=="LOSS"]
    wr     = len(wins)/len(tdf)*100
    pf     = (abs(wins["pnl"].sum() / losses["pnl"].sum())
              if len(losses) and losses["pnl"].sum() != 0 else 99.0)
    ann_r  = float(rets.mean() * TRADING_DAYS)
    ann_v  = float(rets.std()  * np.sqrt(TRADING_DAYS))
    dv     = rets[rets < 0].std() * np.sqrt(TRADING_DAYS)
    sharpe  = ann_r / ann_v if ann_v > 0 else 0.0
    sortino = ann_r / dv    if dv   > 0 else 0.0
    dd      = (eq_ser - eq_ser.cummax()) / eq_ser.cummax() * 100
    tot_r   = (equity - 1_000_000) / 1_000_000 * 100
    max_cl  = cl = 0
    for o in tdf["outcome"].values:
        if o == "LOSS": cl += 1; max_cl = max(max_cl, cl)
        else: cl = 0
    monthly      = tdf.copy()
    monthly["month"] = pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    mpnl         = monthly.groupby("month")["pnl"].sum()
    strat_ok     = pf >= 1.3 and wr >= 45 and sharpe >= 0.3
    strat_ok2    = pf >= 1.0 and wr >= 40
    verdict      = ("✅ STRATEGY VALIDATED" if strat_ok else
                    "⚠️ MARGINAL EDGE"      if strat_ok2 else
                    "❌ STRATEGY FAILED")
    avg_win_pct  = round(wins["pnl_pct"].mean(),   2) if len(wins)   else 0.0
    avg_loss_pct = round(losses["pnl_pct"].mean(), 2) if len(losses) else 0.0
    rr = (round(abs(avg_win_pct / avg_loss_pct), 2)
          if avg_loss_pct != 0 else 0.0)
    return {
        "trades": tdf, "equity_curve": eq_df, "drawdown": dd, "monthly_pnl": mpnl,
        "total_trades": len(tdf), "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2), "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3), "max_dd": round(float(dd.min()), 2),
        "total_ret": round(tot_r, 2), "final_equity": round(equity, 2),
        "avg_win": avg_win_pct, "avg_loss": avg_loss_pct, "rr": rr,
        "max_consec_loss": max_cl, "strat_verdict": verdict,
        "ann_ret": round(ann_r*100, 2), "ann_vol": round(ann_v*100, 2),
    }


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
        prompt = (
            f"Stock: {symbol}  Price: ₹{price:.2f}\n"
            f"VPIN toxicity: {vpin['toxicity']} ({vpin['current']:.3f}, {vpin['percentile']:.0f}th pct)\n"
            f"GARCH vol: {garch['daily_vol_pct']:.2f}%/day  Annual: {garch['annual_vol_pct']:.1f}%  "
            f"Regime: {garch['regime']}\n"
            f"Institutional events: {footprint['n_events']} surges  "
            f"Bias: {footprint['bias_score']:+d}/3  Verdict: {footprint['verdict']}\n"
            f"Volume Profile POC: ₹{vp['poc']:.0f}  "
            f"Value Area: ₹{vp['va_low']:.0f}–₹{vp['va_high']:.0f}\n\n"
            "In 3-4 sentences: What is the smart money doing? "
            "Is this a good swing trade setup? What levels matter?"
        )
        return model.generate_content(prompt).text.strip()
    except Exception:
        return ""


def _rule_narrative(symbol, price, vpin, garch, footprint, vp):
    lines = []
    if vpin["percentile"] > 70:
        lines.append(
            f"⚡ <b>Informed money is active in {symbol}</b> — VPIN at "
            f"{vpin['percentile']:.0f}th percentile signals institutional order flow. "
            "A large directional move is likely imminent."
        )
    elif vpin["percentile"] < 30:
        lines.append(
            f"😴 Order flow in {symbol} is dominated by retail noise. "
            "No institutional conviction visible yet — avoid chasing."
        )
    else:
        lines.append(
            f"📊 {symbol} shows mixed order flow — some informed activity "
            "but no strong institutional conviction yet."
        )
    if "HIGH" in garch["regime"]:
        lines.append(
            f"⚠️ Volatility is elevated ({garch['annual_vol_pct']:.0f}% annual, "
            f"{garch['percentile']:.0f}th pct) — size positions smaller, widen stops."
        )
    elif "LOW" in garch["regime"]:
        lines.append(
            f"📉 Volatility is compressed ({garch['annual_vol_pct']:.0f}% annual) "
            "— often precedes a sharp move. Watch for a vol expansion trigger."
        )
    else:
        lines.append(
            f"📊 Volatility normal at {garch['annual_vol_pct']:.0f}% annual "
            "— standard position sizing applies."
        )
    if footprint["n_events"] > 0 and footprint["bias_score"] > 0:
        lines.append(
            f"🐂 Institutional footprint is <b>bullish</b> — "
            f"{footprint['n_events']} volume surges detected, majority bullish. "
            f"POC at ₹{vp['poc']:,.0f} is the key support level."
        )
    elif footprint["n_events"] > 0 and footprint["bias_score"] < 0:
        lines.append(
            f"🐻 Institutional footprint is <b>bearish</b> — distribution detected. "
            f"Resistance expected near ₹{vp['va_high']:,.0f} (Value Area High)."
        )
    else:
        lines.append(
            f"Value Area ₹{vp['va_low']:,.0f}–₹{vp['va_high']:,.0f} · "
            f"POC ₹{vp['poc']:,.0f}."
        )
    lines.append(
        f"<span style='font-size:11px;color:{MUTE};'>"
        "Educational analysis · Not SEBI investment advice</span>"
    )
    return " ".join(lines)


def chart_price_vwap(df, vwap_df, vp, footprint, symbol):
    d  = df.tail(120).copy()
    vd = vwap_df.tail(120).copy()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        name="Price", showlegend=False), row=1, col=1)
    if "vwap" in vd.columns:
        fig.add_trace(go.Scatter(
            x=vd.index, y=vd["vwap"],
            line=dict(color=ACCENT, width=1.8, dash="dot"), name="VWAP"), row=1, col=1)
        if "vwap_upper" in vd.columns and "vwap_lower" in vd.columns:
            fig.add_trace(go.Scatter(
                x=list(vd.index) + list(vd.index[::-1]),
                y=list(vd["vwap_upper"]) + list(vd["vwap_lower"][::-1]),
                fill="toself", fillcolor="rgba(201,162,39,0.07)",
                line=dict(color="rgba(0,0,0,0)"), name="VWAP ±1σ"), row=1, col=1)
    fig.add_hline(y=vp["poc"], line_color=PURPLE, line_dash="dash", line_width=1.5,
                  annotation_text=f"POC ₹{vp['poc']:,.0f}",
                  annotation_font_color=PURPLE, row=1, col=1)
    fig.add_hrect(y0=vp["va_low"], y1=vp["va_high"],
                  fillcolor="rgba(75,139,214,0.07)", line_width=0, row=1, col=1)
    ev = footprint["events"]
    if not ev.empty:
        d_index_set = set(d.index)
        recent_ev   = ev[ev["date"].isin(d_index_set)]
        for _, e in recent_ev.iterrows():
            ed = e["date"]
            if ed in d.index:
                yval = (float(d.loc[ed, "Low"]) * 0.995 if e["bullish"]
                        else float(d.loc[ed, "High"]) * 1.005)
                fig.add_trace(go.Scatter(
                    x=[ed], y=[yval], mode="markers",
                    marker=dict(color=GREEN if e["bullish"] else RED, size=10,
                                symbol="triangle-up" if e["bullish"] else "triangle-down"),
                    showlegend=False), row=1, col=1)
    v_colors = [GREEN if float(d["Close"].iloc[i]) >= float(d["Open"].iloc[i]) else RED
                for i in range(len(d))]
    avg_v = float(d["Volume"].mean())
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=v_colors,
                         opacity=0.6, name="Volume", showlegend=False), row=2, col=1)
    fig.add_hline(y=avg_v * 2.5, line_color=ACCENT, line_dash="dot", line_width=1,
                  annotation_text="Surge threshold",
                  annotation_font_color=ACCENT, row=2, col=1)
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
    colors = [
        ACCENT if abs(p - vp["poc"]) < (vp["va_high"] - vp["va_low"]) * 0.05
        else BLUE if vp["va_low"] <= p <= vp["va_high"]
        else MUTE
        for p in vp["price_mid"]
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=[f"₹{p:,.0f}" for p in vp["price_mid"]],
        x=vp["vol_bins"], orientation="h",
        marker_color=colors, opacity=0.85))
    return _layout(fig, "VOLUME PROFILE · Gold=POC · Blue=Value Area · Gray=Outside", height=480)


def chart_garch(garch, close):
    sigma = garch["sigma_series"].tail(250) * np.sqrt(TRADING_DAYS) * 100
    fig   = make_subplots(rows=2, cols=1, shared_xaxes=True,
                          subplot_titles=["Close Price",
                                          "GARCH Conditional Volatility (Annualised %)"],
                          vertical_spacing=0.08)
    fig.add_trace(go.Scatter(x=close.index[-250:], y=close.tail(250).values,
                             line=dict(color=IVORY, width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=sigma.index, y=sigma.values,
                             line=dict(color=AMBER, width=2), fill="tozeroy",
                             fillcolor="rgba(245,158,11,0.10)"), row=2, col=1)
    fig.add_hline(y=garch["hist_avg_annual"], line_color=MUTE, line_dash="dash",
                  annotation_text=f"Avg {garch['hist_avg_annual']:.1f}%",
                  annotation_font_color=MUTE, row=2, col=1)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        height=420, showlegend=False, margin=dict(l=8, r=8, t=40, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        xaxis2=dict(gridcolor=BORDER), yaxis2=dict(gridcolor=BORDER),
        title=dict(text="GARCH(1,1) VOLATILITY MODEL", font=dict(size=12, color=ACCENT)),
    )
    return fig


def chart_monte_carlo(mc, price, symbol):
    paths      = mc["paths"]
    rng_sample = np.random.default_rng(0)
    idx_sample = rng_sample.choice(len(paths), size=min(200, len(paths)), replace=False)
    fig = go.Figure()
    for i in idx_sample:
        fig.add_trace(go.Scatter(
            y=paths[i], mode="lines",
            line=dict(color="rgba(76,141,214,0.06)", width=1),
            showlegend=False))
    p10 = np.percentile(paths, 10, axis=0)
    p50 = np.percentile(paths, 50, axis=0)
    p90 = np.percentile(paths, 90, axis=0)
    x   = list(range(1, mc["horizon"] + 1))
    fig.add_trace(go.Scatter(x=x, y=p90, line=dict(color=GREEN, width=2, dash="dash"), name="P90 (best)"))
    fig.add_trace(go.Scatter(x=x, y=p50, line=dict(color=ACCENT, width=2.5),           name="P50 (median)"))
    fig.add_trace(go.Scatter(x=x, y=p10, line=dict(color=RED,   width=2, dash="dash"), name="P10 (worst)"))
    fig.add_hline(y=price, line_color=MUTE, line_dash="dot", line_width=1.5,
                  annotation_text=f"Current ₹{price:,.0f}", annotation_font_color=MUTE)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.05, x=0))
    return _layout(fig,
                   f"MONTE CARLO · {mc['n_sims']:,} PATHS · "
                   f"{mc['horizon']}-DAY HORIZON · {symbol}",
                   height=400)


def chart_vpin_series(vpin_data):
    s = vpin_data["series"]
    if s.empty: return go.Figure()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.index, y=s.values, fill="tozeroy",
                             fillcolor="rgba(155,89,182,0.12)",
                             line=dict(color=PURPLE, width=2), name="VPIN"))
    fig.add_hline(y=float(s.quantile(0.70)), line_color=RED, line_dash="dash",
                  annotation_text="70th pct (High Toxicity)", annotation_font_color=RED)
    fig.add_hline(y=float(s.quantile(0.30)), line_color=GREEN, line_dash="dash",
                  annotation_text="30th pct (Low Toxicity)", annotation_font_color=GREEN)
    return _layout(fig, "VPIN — ORDER FLOW TOXICITY (Jane Street Signal)", height=300)


def chart_vol_surge_history(footprint):
    ev = footprint["events"]
    if ev.empty: return _layout(go.Figure(), "VOLUME SURGE HISTORY", 320)
    bull = ev[ev["bullish"]]
    bear = ev[~ev["bullish"]]
    fig  = go.Figure()
    if not bull.empty:
        fig.add_trace(go.Scatter(
            x=bull["date"], y=bull["vol_ratio"], mode="markers",
            marker=dict(color=GREEN, size=(bull["strength"]*3+6).tolist(), symbol="triangle-up"),
            name="Bull Surge",
            text=[f"{r:.1f}x avg" for r in bull["vol_ratio"]]))
    if not bear.empty:
        fig.add_trace(go.Scatter(
            x=bear["date"], y=bear["vol_ratio"], mode="markers",
            marker=dict(color=RED, size=(bear["strength"]*3+6).tolist(), symbol="triangle-down"),
            name="Bear Surge",
            text=[f"{r:.1f}x avg" for r in bear["vol_ratio"]]))
    fig.add_hline(y=2.5, line_color=ACCENT, line_dash="dot",
                  annotation_text="Surge threshold", annotation_font_color=ACCENT)
    fig.update_layout(showlegend=True)
    return _layout(fig, "INSTITUTIONAL VOLUME SURGE HISTORY · Size = Signal Strength", 320)


def chart_equity(bt):
    eq  = bt["equity_curve"]["equity"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, fill="tozeroy",
                             fillcolor="rgba(201,162,39,0.07)",
                             line=dict(color=ACCENT, width=2)))
    fig.add_hline(y=1_000_000, line_color=MUTE, line_dash="dash", line_width=1,
                  annotation_text="Initial Capital", annotation_font_color=MUTE)
    return _layout(fig, "EQUITY CURVE (₹10L initial)", height=300)


def chart_drawdown(bt):
    dd  = bt["drawdown"]
    fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy",
                               fillcolor="rgba(232,85,78,0.14)",
                               line=dict(color=RED, width=1.5)))
    return _layout(fig, "DRAWDOWN (%)", height=240)


def chart_trade_bars(bt):
    tdf    = bt["trades"]
    colors = [GREEN if o == "WIN" else RED for o in tdf["outcome"]]
    fig    = go.Figure(go.Bar(
        x=tdf["exit_date"].astype(str), y=tdf["pnl_pct"],
        marker_color=colors, opacity=0.85,
        text=[f"{v:+.1f}%" for v in tdf["pnl_pct"]],
        textposition="outside", textfont=dict(size=8)))
    m = float(tdf["pnl_pct"].mean())
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    fig.add_hline(y=m, line_color=ACCENT, line_dash="dash",
                  annotation_text=f"Avg {m:+.2f}%", annotation_font_color=ACCENT)
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    return _layout(fig, f"TRADE P&L · {len(tdf)} TRADES", height=300)


def chart_monthly_pnl(bt):
    mp     = bt["monthly_pnl"]
    colors = [GREEN if v >= 0 else RED for v in mp.values]
    fig    = go.Figure(go.Bar(
        x=[str(p) for p in mp.index], y=mp.values,
        marker_color=colors, opacity=0.85,
        text=[f"₹{v:,.0f}" for v in mp.values],
        textposition="outside", textfont=dict(size=8)))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    return _layout(fig, "MONTHLY NET P&L (₹)", height=280)


def chart_exit_breakdown(bt):
    tdf = bt["trades"]
    if "exit_reason" not in tdf.columns: return go.Figure()
    er  = tdf.groupby(["exit_reason","outcome"]).size().unstack(fill_value=0)
    fig = go.Figure()
    for o, c in [("WIN", GREEN), ("LOSS", RED)]:
        if o in er.columns:
            fig.add_trace(go.Bar(name=o, x=er.index, y=er[o],
                                 marker_color=c, opacity=0.85))
    fig.update_layout(barmode="stack", showlegend=True,
                      legend=dict(orientation="h", y=1.05, x=0))
    return _layout(fig, "EXIT REASON BREAKDOWN", height=260)


def render_quant_analysis():
    try:    api_key = st.secrets.get("GEMINI_KEY", "")
    except: api_key = ""

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;
    padding:12px 20px;margin-bottom:12px;display:flex;
    justify-content:space-between;align-items:center;'>
      <div>
        <div style='font-family:{MONO};font-size:14px;font-weight:700;
          color:{ACCENT};letter-spacing:2px;'>ARKA · INSTITUTIONAL SWING TERMINAL</div>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-top:2px;'>
          GARCH vol · VPIN toxicity · Volume profile · Monte Carlo risk · Swing backtest</div>
      </div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};text-align:right;'>
        {ts}<br>{"🤖 AI ON" if api_key else "📐 RULE-BASED"} · NSE INDIA</div>
    </div>""", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([3, 1, 1])
    symbol = c1.text_input("NSE Symbol", value="RELIANCE",
                            placeholder="e.g. RELIANCE, TCS, HDFCBANK, ^NSEI",
                            label_visibility="collapsed")
    period = c2.selectbox("Period", ["1y","2y","3y","5y"], label_visibility="collapsed")
    run    = c3.button("🔍 ANALYSE", type="primary", use_container_width=True)

    if not run:
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:12px;
        padding:32px;text-align:center;margin:20px 0;'>
          <div style='font-family:{MONO};font-size:16px;color:{ACCENT};margin-bottom:12px;'>
            What this terminal shows you</div>
          <div style='font-size:13px;color:{IVORY};line-height:2.2;max-width:700px;margin:0 auto;'>
            📦 <b>Institutional Footprint</b> — Where big players entered (3x+ volume surges)<br>
            📊 <b>VPIN Signal</b> — Are informed traders active right now?<br>
            🗺️ <b>Volume Profile</b> — Point of Control + Value Area (real support/resistance)<br>
            📈 <b>VWAP</b> — The institutional benchmark price<br>
            📉 <b>GARCH Volatility</b> — Real vol regime + 10-day forecast<br>
            🎲 <b>Monte Carlo Risk</b> — 1000 paths, VaR, Expected Shortfall in ₹<br>
            ⚙️ <b>Swing Backtest</b> — Did this signal historically lead to 1-2 week moves?
          </div>
        </div>""", unsafe_allow_html=True)
        return

    if not symbol.strip():
        st.error("Enter a symbol."); return

    with st.spinner(f"Loading {symbol.upper()} data..."):
        df = fetch_stock(symbol, period)

    if df.empty or len(df) < 60:
        st.error(f"Could not load data for '{symbol}'. "
                 "Try: RELIANCE, TCS, INFY, HDFCBANK, ^NSEI, ^NSEBANK")
        return

    price = float(df["Close"].iloc[-1])
    prev  = float(df["Close"].iloc[-2])
    chg   = (price - prev) / prev * 100
    chg_c = GREEN if chg >= 0 else RED

    st.markdown(f"""
    <div style='display:flex;align-items:center;gap:20px;background:{PANEL2};
    border:1px solid {BORDER};border-radius:10px;padding:14px 20px;margin-bottom:10px;'>
      <div style='font-family:{MONO};font-size:28px;font-weight:700;color:{IVORY};'>
        {symbol.upper()}</div>
      <div style='font-family:{MONO};font-size:28px;font-weight:700;color:{IVORY};'>
        ₹{price:,.2f}</div>
      <div style='font-family:{MONO};font-size:16px;font-weight:700;color:{chg_c};'>
        {chg:+.2f}%</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};margin-left:auto;'>
        {len(df)} bars · {str(df.index[0])[:10]} → {str(df.index[-1])[:10]}</div>
    </div>""", unsafe_allow_html=True)

    with st.spinner("Running engines..."):
        garch_res  = garch_analysis(df["Close"])
        vpin_d     = compute_vpin(df)
        vp         = volume_profile(df)
        footprint  = institutional_footprint(df)
        vwap_df    = compute_vwap(df)
        log_rets   = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        mc = monte_carlo_risk(
            price, garch_res["daily_vol_pct"] / 100,
            mu=float(log_rets.mean()),
            horizon=10, n_sims=1000, capital=100_000,
        )

    ai_note = ""
    if api_key:
        with st.spinner("🤖 AI generating insight..."):
            ai_note = _ai_summary(symbol, price, vpin_d, garch_res, footprint, vp, api_key)
    if not ai_note:
        ai_note = _rule_narrative(symbol, price, vpin_d, garch_res, footprint, vp)

    fp_v   = footprint["verdict"]
    fp_cfg = SIGNAL_CFG.get(fp_v, SIGNAL_CFG["NEUTRAL"])
    fp_c   = fp_cfg["color"]
    tox_c  = RED if "HIGH" in vpin_d["toxicity"] else AMBER if "MOD" in vpin_d["toxicity"] else GREEN
    vol_c  = RED if "HIGH" in garch_res["regime"] else BLUE if "LOW" in garch_res["regime"] else MUTE

    st.markdown(f"""
    <div style='background:{fp_cfg["bg"]};border:1px solid {fp_c}55;
    border-left:5px solid {fp_c};border-radius:12px;padding:20px 24px;margin:12px 0;'>
      <div style='display:flex;justify-content:space-between;align-items:flex-start;
        flex-wrap:wrap;gap:16px;'>
        <div>
          <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:2px;
            margin-bottom:6px;'>INSTITUTIONAL SIGNAL · {symbol.upper()}</div>
          <div style='font-family:{MONO};font-size:32px;font-weight:800;
            color:{fp_c};line-height:1;'>{fp_cfg["icon"]} {fp_v}</div>
          <div style='font-family:{MONO};font-size:12px;color:{IVORY};margin-top:6px;'>
            Bias {footprint["bias_score"]:+d}/3 ·
            <span style='color:{tox_c};'>VPIN: {vpin_d["toxicity"]}</span> ·
            <span style='color:{vol_c};'>Vol: {garch_res["regime"]}</span>
          </div>
        </div>
        <div style='min-width:280px;'>
          <div style='font-size:13px;color:{IVORY};line-height:1.8;'>{ai_note}</div>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

    t0, t1, t2, t3, t4 = st.tabs([
        "📦 FOOTPRINT & VWAP",
        "⚡ VPIN & VOL SURGE",
        "📉 GARCH VOLATILITY",
        "🎲 MONTE CARLO RISK",
        "⚙️ SWING BACKTEST",
    ])

    with t0:
        st.plotly_chart(chart_price_vwap(df, vwap_df, vp, footprint, symbol.upper()),
                        use_container_width=True)
        st.markdown("""
        <div style='font-size:12px;color:#8593A3;line-height:1.8;'>
        <b>How to read:</b> 🔺/🔻 = institutional volume surges.
        <b>POC</b> (purple dash) = strongest support/resistance.
        <b>Value Area</b> (blue) = 70% of volume zone.
        <b>VWAP</b> (gold dot) = institutional benchmark.
        </div>""", unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(_sec("VOLUME PROFILE"), unsafe_allow_html=True)
            st.plotly_chart(chart_volume_profile(vp), use_container_width=True)
        with col2:
            st.markdown(_sec("KEY LEVELS"), unsafe_allow_html=True)
            curr_vwap = float(vwap_df["vwap"].iloc[-1]) if "vwap" in vwap_df.columns else 0.0
            _mrow([("POC",    f"₹{vp['poc']:,.0f}"),
                   ("VA Low", f"₹{vp['va_low']:,.0f}"),
                   ("VA High",f"₹{vp['va_high']:,.0f}"),
                   ("VWAP",   f"₹{curr_vwap:,.0f}")])
            pos_vs_poc  = (price - vp["poc"]) / vp["poc"] * 100
            pos_vs_vwap = (price - curr_vwap) / curr_vwap * 100 if curr_vwap else 0.0
            poc_txt = (
                f"Price is <b>₹{abs(price-vp['poc']):,.0f} ({abs(pos_vs_poc):.1f}%) "
                f"{'above' if price >= vp['poc'] else 'below'} the POC</b>. "
                f"{'POC acts as support — bullish.' if price >= vp['poc'] else 'POC is resistance — caution.'}"
            )
            vwap_txt = (
                f"Price is <b>{'above' if pos_vs_vwap >= 0 else 'below'} VWAP "
                f"by {abs(pos_vs_vwap):.1f}%</b>. "
                f"{'Institutions in profit — trend up.' if pos_vs_vwap >= 0 else 'Below VWAP — potential selling pressure.'}"
            )
            _signal_card("VOLUME PROFILE SIGNAL",
                         _score_to_verdict(2 if price >= vp["poc"] else -2, 3),
                         poc_txt + " " + vwap_txt,
                         2 if price >= vp["poc"] else -2)
            if footprint["n_events"] > 0 and not footprint["recent"].empty:
                st.markdown(_sec("RECENT INSTITUTIONAL EVENTS"), unsafe_allow_html=True)
                disp = footprint["recent"][["date","type","price","vol_ratio","strength"]].copy()
                disp["date"]      = pd.to_datetime(disp["date"]).dt.strftime("%Y-%m-%d")
                disp["vol_ratio"] = disp["vol_ratio"].apply(lambda x: f"{x:.1f}x avg")
                disp.columns      = ["Date","Type","Price","Vol Ratio","Strength"]
                st.dataframe(disp, use_container_width=True, hide_index=True)
        bs = footprint["bias_score"]
        bias_txt = ("Smart money predominantly buying — bullish footprint." if bs > 0
                    else "Smart money predominantly selling — bearish footprint." if bs < 0
                    else "Mixed institutional activity — no clear directional bias.")
        _signal_card("INSTITUTIONAL FOOTPRINT", footprint["verdict"],
                     f"{footprint['n_events']} volume surge events detected. "
                     f"Last 3 bias: {bs:+d}/3. {bias_txt} "
                     f"Current vol ratio: {footprint['last_ratio']:.1f}x avg.",
                     bs)

    with t1:
        st.markdown(_sec("VPIN — ORDER FLOW TOXICITY"), unsafe_allow_html=True)
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
        padding:14px 18px;margin-bottom:10px;'>
          <div style='font-size:13px;color:{IVORY};line-height:1.8;'>
          <b>VPIN</b> detects if <b>informed institutional traders</b> are entering.
          When VPIN rises above the 70th percentile, market makers widen spreads
          because a big move is coming. <b>This is your earliest warning signal.</b>
          </div>
        </div>""", unsafe_allow_html=True)
        _mrow([("Current VPIN",  f"{vpin_d['current']:.4f}"),
               ("Average VPIN",  f"{vpin_d['avg']:.4f}"),
               ("Percentile",    f"{vpin_d['percentile']:.0f}th"),
               ("Toxicity",      vpin_d["toxicity"])])
        st.plotly_chart(chart_vpin_series(vpin_d), use_container_width=True)
        pct = vpin_d["percentile"]
        _signal_card("VPIN SIGNAL",
                     _score_to_verdict(3 if pct > 70 else 0 if pct > 40 else -1, 3),
                     vpin_d["meaning"],
                     3 if pct > 70 else 0 if pct > 40 else -1)
        st.markdown(_sec("VOLUME SURGE HISTORY"), unsafe_allow_html=True)
        st.plotly_chart(chart_vol_surge_history(footprint), use_container_width=True)
        st.caption("🔺 Bull surge = institutional buying · 🔻 Bear surge = institutional selling · Size = signal strength")

    with t2:
        st.markdown(_sec("GARCH(1,1) VOLATILITY MODEL"), unsafe_allow_html=True)
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
        padding:14px 18px;margin-bottom:10px;'>
          <div style='font-size:13px;color:{IVORY};line-height:1.8;'>
          <b>GARCH</b> captures <b>volatility clustering</b> — high-vol days follow high-vol days.
          This tells you when to <b>size up</b> (low vol) and when to <b>cut size</b> (high vol).
          </div>
        </div>""", unsafe_allow_html=True)
        _mrow([("Daily Vol",      f"{garch_res['daily_vol_pct']:.2f}%"),
               ("Annual Vol",     f"{garch_res['annual_vol_pct']:.1f}%"),
               ("Hist Avg",       f"{garch_res['hist_avg_annual']:.1f}%"),
               ("Percentile",     f"{garch_res['percentile']:.0f}th"),
               ("Regime",         garch_res["regime"]),
               ("Persistence",    f"{garch_res['persistence']:.3f}")])
        st.plotly_chart(chart_garch(garch_res, df["Close"]), use_container_width=True)
        st.markdown(_sec("10-DAY FORWARD VOL FORECAST"), unsafe_allow_html=True)
        fwd    = garch_res["forecast_daily"]
        fwd_df = pd.DataFrame({
            "Day":          range(1, len(fwd)+1),
            "Daily Vol %":  [round(v, 3) for v in fwd],
            "Annual %":     [round(v * np.sqrt(252), 1) for v in fwd],
        })
        st.dataframe(fwd_df.set_index("Day"), use_container_width=True)
        gp = garch_res["percentile"]
        _signal_card(
            "GARCH SIGNAL",
            "WEAK SELL" if "HIGH" in garch_res["regime"] else
            "WEAK BUY"  if "LOW"  in garch_res["regime"] else "NEUTRAL",
            (f"Vol at {gp:.0f}th percentile. "
             f"Persistence α+β={garch_res['persistence']:.3f} — "
             f"{'vol shocks persistent.' if garch_res['persistence'] > 0.95 else 'vol reverts moderately.'} "
             f"{'Reduce size — high vol.' if gp > 75 else 'Compressed vol — build positions.' if gp < 25 else 'Normal vol — standard stops apply.'}"),
            -2 if gp > 75 else 2 if gp < 25 else 0,
        )

    with t3:
        st.markdown(_sec("MONTE CARLO RISK SIMULATION"), unsafe_allow_html=True)
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
        padding:14px 18px;margin-bottom:10px;'>
          <div style='font-size:13px;color:{IVORY};line-height:1.8;'>
          1,000 simulated price paths using <b>Student-t distribution</b> (fat tails).
          Shows your realistic worst/median/best case for a 10-day swing position.
          </div>
        </div>""", unsafe_allow_html=True)
        cap_col, _ = st.columns([1, 3])
        capital = cap_col.number_input("Position Size (₹)", value=100_000,
                                        step=10_000, min_value=10_000)
        if capital != 100_000:
            mc = monte_carlo_risk(
                price, garch_res["daily_vol_pct"] / 100,
                mu=float(log_rets.mean()),
                horizon=10, n_sims=1000, capital=capital,
            )
        st.plotly_chart(chart_monte_carlo(mc, price, symbol.upper()), use_container_width=True)
        st.markdown(_sec("RISK METRICS"), unsafe_allow_html=True)
        _mrow([("Prob of Profit",   f"{mc['prob_up_pct']:.1f}%"),
               ("Median Exit",      f"₹{mc['p50']:,.0f}"),
               ("P90 Upside",       f"₹{mc['p90_upside']:,.0f}"),
               ("P10 Downside",     f"₹{mc['p10_downside']:,.0f}")])
        _mrow([("VaR 95% (₹)",      f"₹{mc['var95_rs']:,.0f}"),
               ("ES 95% (₹)",       f"₹{mc['es95_rs']:,.0f}"),
               ("VaR 99% (₹)",      f"₹{mc['var99_rs']:,.0f}"),
               ("ES 99% (₹)",       f"₹{mc['es99_rs']:,.0f}")])
        var_c = RED if mc["var95_pct"] < -5 else AMBER if mc["var95_pct"] < -3 else GREEN
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {var_c}44;border-left:4px solid {var_c};
        border-radius:10px;padding:14px 18px;margin:10px 0;'>
          <div style='font-size:13px;color:{IVORY};line-height:1.8;'>
          On a ₹{capital:,.0f} position in <b>{symbol.upper()}</b> held 10 days:<br>
          • 5% chance of losing more than <b>₹{abs(mc['var95_rs']):,.0f}</b> (VaR 95%)<br>
          • Worst 5% average loss = <b>₹{abs(mc['es95_rs']):,.0f}</b> (Expected Shortfall)<br>
          • Prob of profit: {mc['prob_up_pct']:.1f}% · Median exit: ₹{mc['p50']:,.2f}<br>
          • Best case (P90): ₹{mc['p90_upside']:,.0f} · Worst (P10): ₹{mc['p10_downside']:,.0f}
          </div>
        </div>""", unsafe_allow_html=True)

    with t4:
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
        padding:14px 18px;margin-bottom:12px;'>
          <div style='font-size:13px;color:{IVORY};line-height:1.8;'>
          <b>Entry:</b> Volume ≥ X× avg AND bullish close AND above 50MA AND RSI 35–75.<br>
          <b>Exit:</b> Target · Stop · Volume dry-up · MA flip · Max hold days.
          </div>
        </div>""", unsafe_allow_html=True)
        with st.form("swing_bt"):
            st.markdown(_sec("PARAMETERS"), unsafe_allow_html=True)
            p1, p2, p3, p4 = st.columns(4)
            start_d  = p1.date_input("Start", value=datetime.date(2020, 1, 1))
            end_d    = p2.date_input("End",   value=datetime.date.today())
            vol_mult = p3.slider("Vol Surge ×", 1.5, 5.0, 2.5, 0.5)
            hold_d   = p4.slider("Max Hold Days", 3, 21, 7)
            p5, p6, p7, p8 = st.columns(4)
            stop_p = p5.slider("Stop %",     1, 10, 4)
            tgt_p  = p6.slider("Target %",   2, 20, 8)
            pos_p  = p7.slider("Position %", 5, 40, 20)
            comm   = p8.number_input("Commission (bps)", value=15.0, step=1.0) / 10_000
            run_bt = st.form_submit_button("▶ RUN BACKTEST", type="primary",
                                           use_container_width=True)
        if not run_bt:
            st.info("Set parameters above and press RUN BACKTEST.")
        else:
            if start_d >= end_d:
                st.error("Start must be before End."); st.stop()
            with st.spinner("Running backtest..."):
                df_bt = fetch_range(symbol, str(start_d), str(end_d))
                if df_bt.empty:
                    st.error(f"No data for {symbol.upper()} in that range."); st.stop()
                bt = swing_backtest(df_bt, vol_mult=vol_mult, hold_days=hold_d,
                                    stop_pct=stop_p/100, target_pct=tgt_p/100,
                                    pos_pct=pos_p/100, commission=comm)
            if "error" in bt:
                st.error(bt["error"]); st.stop()
            sv    = bt["strat_verdict"]
            sv_c  = GREEN if "✅" in sv else AMBER if "⚠️" in sv else RED
            bg_sv = ("rgba(31,185,122,0.12)" if "✅" in sv else
                     "rgba(245,158,11,0.12)" if "⚠️" in sv else
                     "rgba(232,85,78,0.12)")
            st.markdown(f"""
            <div style='background:{bg_sv};border:1px solid {sv_c}55;
            border-left:5px solid {sv_c};border-radius:10px;padding:16px 22px;margin:10px 0;'>
              <div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-bottom:4px;'>
                BACKTEST · {symbol.upper()} · {start_d} → {end_d}</div>
              <div style='font-family:{MONO};font-size:22px;font-weight:800;color:{sv_c};'>{sv}</div>
              <div style='font-family:{MONO};font-size:12px;color:{IVORY};margin-top:6px;'>
                {bt["total_trades"]} trades · WR {bt["win_rate"]}% ·
                PF {bt["profit_factor"]} · Sharpe {bt["sharpe"]} ·
                Return {bt["total_ret"]:+.2f}% · Max DD {bt["max_dd"]:.1f}%
              </div>
            </div>""", unsafe_allow_html=True)
            st.markdown(_sec("PERFORMANCE METRICS"), unsafe_allow_html=True)
            _mrow([("Trades",  str(bt["total_trades"])), ("Win Rate",f"{bt['win_rate']}%"),
                   ("Avg Win", f"{bt['avg_win']:+.2f}%"),("Avg Loss",f"{bt['avg_loss']:+.2f}%")])
            _mrow([("R:R",           f"{bt['rr']:.2f}"),
                   ("Profit Factor", f"{bt['profit_factor']:.2f}"),
                   ("Sharpe",        f"{bt['sharpe']:.2f}"),
                   ("Sortino",       f"{bt['sortino']:.2f}")])
            _mrow([("Total Return",  f"{bt['total_ret']:+.2f}%"),
                   ("Ann. Return",   f"{bt['ann_ret']:+.2f}%"),
                   ("Ann. Vol",      f"{bt['ann_vol']:.2f}%"),
                   ("Max DD",        f"{bt['max_dd']:.1f}%")])
            st.plotly_chart(chart_equity(bt),    use_container_width=True)
            st.plotly_chart(chart_drawdown(bt),  use_container_width=True)
            ca, cb = st.columns(2)
            with ca: st.plotly_chart(chart_trade_bars(bt),    use_container_width=True)
            with cb: st.plotly_chart(chart_exit_breakdown(bt),use_container_width=True)
            st.plotly_chart(chart_monthly_pnl(bt), use_container_width=True)
            st.markdown(_sec("TRADE LEDGER"), unsafe_allow_html=True)
            tdf = bt["trades"].copy()
            tdf["pnl"]     = tdf["pnl"].apply(lambda x: f"₹{x:,.0f}")
            tdf["pnl_pct"] = tdf["pnl_pct"].apply(lambda x: f"{x:+.2f}%")
            tdf.columns    = [c.replace("_"," ").title() for c in tdf.columns]
            st.dataframe(tdf, use_container_width=True, hide_index=True, height=300)
            st.caption("Real NSE data · Educational only · Not SEBI investment advice.")


render_quant_options_page = render_quant_analysis

if __name__ == "__main__":
    st.set_page_config(page_title="ARKA · Swing Analytics",
                       layout="wide", initial_sidebar_state="collapsed")
    render_quant_analysis()
