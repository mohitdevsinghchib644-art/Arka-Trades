"""
quant_analysis.py — Arka Trades | Institutional Quant Terminal v5
=================================================================
app.py: from quant_analysis import render_quant_analysis
Requires: streamlit yfinance pandas numpy scipy plotly scikit-learn

MODE 1 · TERMINAL       Footprint, VPIN, Vol Profile, VWAP, GARCH,
                        Monte Carlo, Score 0-100, bias-free backtest,
                        regime filter, walk-forward validation
MODE 2 · BACKTESTER     Symbols + date range -> every BUY/SELL signal
MODE 3 · ALPHA SCANNER  Cross-sectional factor ranking (fund-style)
MODE 4 · ML LAB         Gradient boosting, purged CV, 5-day forecast
MODE 5 · LIVE SCREENER  Scan 50 NSE names -> active signals today
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
PANEL3 = "#1A2130"
BORDER = "#222B38"
BORDER2= "#2D3A4A"
ACCENT = "#C9A227"
IVORY  = "#E8EDF2"
MUTE   = "#8593A3"
MUTE2  = "#5A6880"
GREEN  = "#1FB97A"
RED    = "#E8554E"
BLUE   = "#4C8DD6"
PURPLE = "#9B59B6"
ORANGE = "#E67E22"
AMBER  = "#F59E0B"
TEAL   = "#14B8A6"
MONO   = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"
SANS   = "'Inter','Plus Jakarta Sans',sans-serif"
TRADING_DAYS = 252

GREEN_27 = "rgba(31,185,122,0.27)"
GREEN_20 = "rgba(31,185,122,0.20)"
RED_27   = "rgba(232,85,78,0.27)"
RED_20   = "rgba(232,85,78,0.20)"


# ════════════════════════════════════════════════════════════════════════════
# SCORE ENGINE
# ════════════════════════════════════════════════════════════════════════════

def compute_scores(vpin_pct, garch_pct, fp_bias, fp_n_events,
                   price, poc, va_low, va_high, vwap,
                   mc_prob_up, mc_var95_pct) -> dict:
    fp_event_pts = min(15, fp_n_events / 5 * 15)
    fp_bias_pts  = (fp_bias + 3) / 6 * 10
    fp_score     = round(min(25, fp_event_pts + fp_bias_pts))
    vp_score     = round(min(25, vpin_pct / 100 * 25))
    if garch_pct < 25:   g_score = 22
    elif garch_pct < 50: g_score = 16
    elif garch_pct < 75: g_score = 10
    else:                g_score = 4
    garch_score = g_score
    mc_pts   = (mc_prob_up / 100) * 15
    var_pts  = max(0, min(10, 10 + mc_var95_pct))
    mc_score = round(min(25, mc_pts + var_pts))
    total    = fp_score + vp_score + garch_score + mc_score
    return {"footprint": fp_score, "vpin": vp_score, "garch": garch_score,
            "monte_carlo": mc_score, "total": min(100, total)}


def _score_rating(score: int) -> tuple[str, str]:
    if score >= 80: return "#1FB97A", "STRONG"
    if score >= 65: return "#C9A227", "GOOD"
    if score >= 45: return "#E67E22", "FAIR"
    return "#E8554E", "WEAK"


def _circle_svg(score: int, label: str = "SCORE",
                size: int = 120, font_score: int = 22) -> str:
    color, rating = _score_rating(score)
    r     = size // 2 - 10
    circ  = 2 * 3.14159 * r
    filled= circ * score / 100
    gap   = circ - filled
    offset= circ * 0.25
    uid   = label.replace(" ", "_")
    return f"""
<div style="display:flex;flex-direction:column;align-items:center;gap:3px;">
<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <defs>
    <filter id="glow_{uid}">
      <feGaussianBlur stdDeviation="2.5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <circle cx="{size//2}" cy="{size//2}" r="{r}"
    fill="none" stroke="{BORDER}" stroke-width="8"/>
  <circle cx="{size//2}" cy="{size//2}" r="{r}"
    fill="none" stroke="{color}" stroke-width="8"
    stroke-dasharray="{filled:.1f} {gap:.1f}"
    stroke-dashoffset="{offset:.1f}" stroke-linecap="round"
    filter="url(#glow_{uid})"/>
  <text x="{size//2}" y="{size//2-5}" text-anchor="middle"
    font-family="monospace" font-size="{font_score}" font-weight="800"
    fill="{color}">{score}</text>
  <text x="{size//2}" y="{size//2+9}" text-anchor="middle"
    font-family="monospace" font-size="8" fill="{MUTE2}">/100</text>
  <text x="{size//2}" y="{size//2+21}" text-anchor="middle"
    font-family="monospace" font-size="7" font-weight="700"
    fill="{color}">{rating}</text>
</svg>
<div style="font-family:{MONO};font-size:8px;color:{MUTE2};
  letter-spacing:1.5px;text-align:center;">{label}</div>
</div>"""


# ════════════════════════════════════════════════════════════════════════════
# UI PRIMITIVES
# ════════════════════════════════════════════════════════════════════════════

def _layout(fig: go.Figure, title: str, height: int = 400) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL2,
        title=dict(text=title, font=dict(size=12, color=ACCENT, family="monospace")),
        height=height, margin=dict(l=8, r=8, t=42, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER, showgrid=True, zeroline=False),
        yaxis=dict(gridcolor=BORDER, showgrid=True, zeroline=False),
    )
    return fig


def _sec(label: str, accent: str = ACCENT) -> str:
    return (f"<div style='display:flex;align-items:center;gap:10px;"
            f"margin:18px 0 8px;'>"
            f"<div style='width:3px;height:16px;border-radius:2px;"
            f"background:{accent};flex-shrink:0;'></div>"
            f"<div style='font-family:{MONO};font-size:11px;font-weight:700;"
            f"color:{accent};letter-spacing:1.8px;'>{label}</div>"
            f"<div style='flex:1;height:1px;background:{BORDER};'></div>"
            f"</div>")


def _mrow(cells: list):
    cols = st.columns(len(cells))
    for col, (lbl, val) in zip(cols, cells):
        col.metric(lbl, val)


def _score_to_verdict(score: int, mx: int = 5) -> str:
    r = score / mx if mx else 0
    if r >= 0.7:  return "STRONG BUY"
    if r >= 0.35: return "BUY"
    if r > 0.1:   return "WEAK BUY"
    if r > -0.1:  return "NEUTRAL"
    if r > -0.35: return "WEAK SELL"
    if r > -0.7:  return "SELL"
    return "STRONG SELL"


VERDICT_CFG = {
    "STRONG BUY" : {"c": GREEN,  "bg": "rgba(31,185,122,0.10)",  "ic": "🟢"},
    "BUY"        : {"c": GREEN,  "bg": "rgba(31,185,122,0.07)",  "ic": "🟩"},
    "WEAK BUY"   : {"c": TEAL,   "bg": "rgba(20,184,166,0.07)",  "ic": "🔼"},
    "NEUTRAL"    : {"c": MUTE,   "bg": "rgba(133,147,163,0.07)", "ic": "⬜"},
    "WEAK SELL"  : {"c": ORANGE, "bg": "rgba(230,126,34,0.07)",  "ic": "🔽"},
    "SELL"       : {"c": RED,    "bg": "rgba(232,85,78,0.07)",   "ic": "🟥"},
    "STRONG SELL": {"c": RED,    "bg": "rgba(232,85,78,0.10)",   "ic": "🔴"},
}


def _signal_card(title: str, verdict: str, body: str, score: int,
                 section_score: int | None = None):
    cfg  = VERDICT_CFG.get(verdict, VERDICT_CFG["NEUTRAL"])
    c    = cfg["c"]; bg = cfg["bg"]
    bars = "".join([
        f"<span style='display:inline-block;width:11px;height:4px;"
        f"border-radius:2px;background:{c};margin-right:2px;opacity:0.9;'></span>"
        for _ in range(min(abs(score), 5))
    ])
    sc_html = ""
    if section_score is not None:
        sc_c, sc_r = _score_rating(section_score)
        sc_html = (f"<div style='font-family:{MONO};font-size:18px;font-weight:800;"
                   f"color:{sc_c};'>{section_score}<span style='font-size:10px;"
                   f"color:{MUTE2};'>/25</span></div>"
                   f"<div style='font-family:{MONO};font-size:8px;color:{sc_c};"
                   f"letter-spacing:1px;'>{sc_r}</div>")
    st.markdown(f"""
    <div style='background:{bg};border:1px solid {c}30;border-left:3px solid {c};
    border-radius:10px;padding:16px 20px;margin:10px 0;
    box-shadow:0 2px 12px rgba(0,0,0,0.3);'>
      <div style='display:flex;justify-content:space-between;align-items:flex-start;
        margin-bottom:10px;'>
        <div>
          <div style='font-family:{MONO};font-size:10px;font-weight:700;color:{c};
            letter-spacing:2px;margin-bottom:4px;'>{cfg["ic"]} {title}</div>
          <div style='font-family:{MONO};font-size:15px;font-weight:800;color:{c};'>{verdict}</div>
        </div>
        <div style='text-align:center;'>{sc_html}</div>
      </div>
      <div style='font-size:12.5px;color:{IVORY};line-height:1.75;
        border-top:1px solid {BORDER};padding-top:10px;'>{body}</div>
      <div style='margin-top:10px;'>{bars}
        <span style='font-family:{MONO};font-size:9px;color:{MUTE2};margin-left:6px;'>
          signal strength {abs(score)}/5</span>
      </div>
    </div>""", unsafe_allow_html=True)


def _premium_kpi(label: str, value: str, sub: str, color: str, icon: str = ""):
    return (f"<div style='background:linear-gradient(135deg,{PANEL3},{PANEL2});"
            f"border:1px solid {color}28;border-top:2px solid {color};"
            f"border-radius:10px;padding:16px 18px;position:relative;overflow:hidden;'>"
            f"<div style='position:absolute;top:-6px;right:8px;font-size:32px;opacity:0.07;'>{icon}</div>"
            f"<div style='font-family:{MONO};font-size:9px;font-weight:700;"
            f"color:{MUTE};letter-spacing:1.8px;margin-bottom:6px;'>{label}</div>"
            f"<div style='font-family:{MONO};font-size:22px;font-weight:800;"
            f"color:{color};line-height:1;margin-bottom:3px;'>{value}</div>"
            f"<div style='font-size:10px;color:{MUTE2};'>{sub}</div></div>")


# ════════════════════════════════════════════════════════════════════════════
# DATA FETCH + REGIME
# ════════════════════════════════════════════════════════════════════════════

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


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_regime() -> pd.Series:
    """NIFTY above 200-DMA = risk-on. Below = risk-off (block new longs)."""
    import yfinance as yf
    try:
        idx = yf.Ticker("^NSEI").history(period="max", interval="1d")["Close"]
        idx.index = pd.to_datetime(idx.index).tz_localize(None)
        return (idx > idx.rolling(200).mean()).rename("risk_on")
    except Exception:
        return pd.Series(dtype=bool)


# ════════════════════════════════════════════════════════════════════════════
# ANALYTICS ENGINES
# ════════════════════════════════════════════════════════════════════════════

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
                s2[t] = omega + a*r[t-1]**2 + b*s2[t-1]
            ll = -0.5*np.sum(np.log(s2[1:]+1e-12) + r[1:]**2/(s2[1:]+1e-12))
            if np.isfinite(ll) and ll > best_ll:
                best_ll = ll; best_a = a; best_b = b
    s2 = np.zeros(len(r)); s2[0] = max(np.var(r), 1e-10)
    for t in range(1, len(r)):
        s2[t] = omega + best_a*r[t-1]**2 + best_b*s2[t-1]
    fwd = [s2[-1]]
    for _ in range(forecast_h-1):
        fwd.append(omega + (best_a+best_b)*fwd[-1])
    sigma_s   = pd.Series(np.sqrt(s2), index=close.index[1:])
    daily_vol = float(np.sqrt(max(s2[-1], 1e-10)))
    ann_vol   = daily_vol * np.sqrt(TRADING_DAYS) * 100
    avg_ann   = float(np.mean(np.sqrt(np.maximum(s2, 1e-10)))) * np.sqrt(TRADING_DAYS) * 100
    pct       = float(np.mean(np.sqrt(s2) <= np.sqrt(s2[-1]))*100)
    regime    = "HIGH-VOL ⚠️" if pct>75 else "LOW-VOL 😴" if pct<25 else "NORMAL 📊"
    return {"sigma_series": sigma_s, "daily_vol_pct": round(daily_vol*100, 3),
            "annual_vol_pct": round(ann_vol, 1), "hist_avg_annual": round(avg_ann, 1),
            "percentile": round(pct, 1), "regime": regime,
            "alpha": best_a, "beta": best_b,
            "forecast_daily": [round(float(np.sqrt(max(v,1e-10)))*100, 3) for v in fwd],
            "persistence": round(best_a+best_b, 3)}


@st.cache_data(ttl=900, show_spinner=False)
def monte_carlo_risk(price: float, daily_vol: float, mu: float = 0.0003,
                     horizon: int = 10, n_sims: int = 1000,
                     capital: float = 100_000, seed: int = 42) -> dict:
    rng    = np.random.default_rng(seed)
    shocks = rng.standard_t(5, size=(n_sims, horizon)) / np.sqrt(5/(5-2))
    paths  = price * np.exp(np.cumsum(mu + daily_vol*shocks, axis=1))
    final  = paths[:, -1]
    pnl    = (final - price)/price * capital
    var95  = float(np.percentile(pnl, 5))
    es95   = float(pnl[pnl <= var95].mean())
    var99  = float(np.percentile(pnl, 1))
    es99   = float(pnl[pnl <= var99].mean())
    return {"paths": paths, "final": final,
            "var95_rs": round(var95, 0), "es95_rs": round(es95, 0),
            "var99_rs": round(var99, 0), "es99_rs": round(es99, 0),
            "var95_pct": round(float(np.percentile((final-price)/price,5))*100, 2),
            "var99_pct": round(float(np.percentile((final-price)/price,1))*100, 2),
            "prob_up_pct": round(float((final>price).mean())*100, 1),
            "p10": round(float(np.percentile(final,10)), 2),
            "p50": round(float(np.percentile(final,50)), 2),
            "p90": round(float(np.percentile(final,90)), 2),
            "horizon": horizon, "n_sims": n_sims, "capital": capital}


@st.cache_data(ttl=900, show_spinner=False)
def volume_profile(df: pd.DataFrame, n_bins: int = 28) -> dict:
    lo_p = float(df["Low"].min()); hi_p = float(df["High"].max())
    if hi_p <= lo_p: hi_p = lo_p + 1
    bins = np.linspace(lo_p, hi_p, n_bins+1)
    vb   = np.zeros(n_bins)
    for _, row in df.iterrows():
        lo, hi, vol = float(row["Low"]), float(row["High"]), float(row["Volume"])
        rng = hi - lo if hi != lo else 1e-8
        for i in range(n_bins):
            overlap = max(0.0, min(hi, bins[i+1]) - max(lo, bins[i]))
            vb[i]  += vol * overlap / rng
    mid     = (bins[:-1]+bins[1:])/2
    poc_idx = int(np.argmax(vb)); poc = float(mid[poc_idx])
    total   = vb.sum(); cum = vb[poc_idx]
    lo_i = poc_idx; hi_i = poc_idx
    while cum < total*0.70:
        cl = lo_i>0; ch = hi_i<n_bins-1
        if cl and ch:
            if vb[lo_i-1] >= vb[hi_i+1]: lo_i-=1; cum+=vb[lo_i]
            else: hi_i+=1; cum+=vb[hi_i]
        elif cl: lo_i-=1; cum+=vb[lo_i]
        elif ch: hi_i+=1; cum+=vb[hi_i]
        else: break
    return {"poc": poc, "va_low": float(bins[lo_i]),
            "va_high": float(bins[min(hi_i+1, n_bins)]),
            "vol_bins": vb, "price_mid": mid,
            "hvn": [float(mid[i]) for i in range(n_bins) if vb[i]>=np.percentile(vb,75)],
            "lvn": [float(mid[i]) for i in range(n_bins) if vb[i]<=np.percentile(vb,25)]}


def compute_vwap(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    df  = df.copy()
    tp  = (df["High"]+df["Low"]+df["Close"])/3
    ctv = (tp*df["Volume"]).rolling(lookback).sum()
    cv  = df["Volume"].rolling(lookback).sum()
    df["vwap"] = ctv / cv.replace(0, np.nan)
    dev = (tp - df["vwap"]).rolling(lookback).std()
    df["vwap_upper"] = df["vwap"] + dev
    df["vwap_lower"] = df["vwap"] - dev
    return df


@st.cache_data(ttl=900, show_spinner=False)
def compute_vpin(df: pd.DataFrame, n_buckets: int = 50) -> dict:
    close  = df["Close"].values.astype(float)
    volume = df["Volume"].values.astype(float)
    safe   = np.where(close>0, close, 1e-8)
    r      = np.diff(np.log(safe))
    sigma  = max(float(np.std(r)), 1e-8)
    buy_v  = []; sell_v = []
    for i in range(len(r)):
        pb = float(norm.cdf(r[i]/sigma)); v = float(volume[i+1])
        buy_v.append(v*pb); sell_v.append(v*(1-pb))
    vals = []
    w    = min(n_buckets, len(buy_v))
    for i in range(w, len(buy_v)):
        bv=sum(buy_v[i-w:i]); sv=sum(sell_v[i-w:i]); tv=bv+sv
        vals.append(abs(bv-sv)/tv if tv>0 else 0.0)
    cur = float(vals[-1]) if vals else 0.0
    avg = float(np.mean(vals)) if vals else 0.0
    pct = float(np.mean(np.array(vals)<=cur)*100) if vals else 50.0
    idx0 = w+1; idx1 = len(vals)+w+1
    si   = df.index[idx0:min(idx1, len(df.index))]
    sv_  = vals[:len(si)]
    tox  = "HIGH 🔥" if pct>70 else "MODERATE ⚠️" if pct>40 else "LOW ✅"
    return {"current": round(cur,4), "avg": round(avg,4),
            "percentile": round(pct,1), "toxicity": tox,
            "series": pd.Series(sv_, index=si),
            "meaning": (
                "INFORMED traders dominating — big directional move imminent. Jane Street signal."
                if pct>70 else
                "Mixed flow — some informed activity but no full conviction yet."
                if pct>40 else
                "Retail/noise flow — big players not engaged. Low conviction move.")}


@st.cache_data(ttl=900, show_spinner=False)
def institutional_footprint(df: pd.DataFrame, vol_mult: float = 2.5,
                            lookback: int = 20) -> dict:
    av  = df["Volume"].rolling(lookback).mean()
    ar  = (df["High"]-df["Low"]).rolling(lookback).mean()
    df2 = df.copy()
    df2["vol_ratio"] = df2["Volume"]/av
    df2["avg_vol"]   = av
    df2["rng_ratio"] = (df2["High"]-df2["Low"])/ar.replace(0,np.nan)
    df2 = df2.dropna()
    events = []
    for i in range(len(df2)):
        row  = df2.iloc[i]
        vr   = float(row["vol_ratio"])
        bull = float(row["Close"])>float(row["Open"])
        rng  = float(row["High"])-float(row["Low"])
        body = abs(float(row["Close"])-float(row["Open"]))
        br   = body/rng if rng>0 else 0.0
        pc   = body/float(row["Open"]) if float(row["Open"])>0 else 0.0
        strength = min(5, int(vr/vol_mult*2 + float(row["rng_ratio"])))
        if vr >= vol_mult:
            if bull and br>0.4:         etype = "BULL SURGE 🐂"
            elif not bull and br>0.4:   etype = "BEAR SURGE 🐻"
            elif pc<0.003 and bull:     etype = "ACCUMULATION 📦"
            elif pc<0.003 and not bull: etype = "DISTRIBUTION 📤"
            else:                       etype = "SURGE ❓"
            events.append({"date": df2.index[i], "type": etype,
                           "price": round(float(row["Close"]),2),
                           "vol_ratio": round(vr,2), "bullish": bull,
                           "strength": strength, "volume": int(row["Volume"]),
                           "avg_vol": int(row["avg_vol"])})
    ev_df = pd.DataFrame(events) if events else pd.DataFrame()
    if len(ev_df)>=3:
        bc = int(ev_df.tail(3)["bullish"].sum())
        bias = bc*2-3
    else:
        bias = 0
    return {"events": ev_df,
            "recent": ev_df.tail(5) if len(ev_df) else pd.DataFrame(),
            "n_events": len(ev_df), "bias_score": bias,
            "verdict": _score_to_verdict(bias, mx=3),
            "vol_series": df2["vol_ratio"],
            "last_ratio": float(df2["vol_ratio"].iloc[-1]) if len(df2) else 0.0}


# ════════════════════════════════════════════════════════════════════════════
# BIAS-FREE BACKTEST ENGINE
#   Signal on bar t -> entry at OPEN of t+1 · intrabar stops · regime aware
# ════════════════════════════════════════════════════════════════════════════

def prepare_indicators(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    d = df.copy()
    d["avg_vol"]   = d["Volume"].rolling(lookback).mean()
    d["vol_ratio"] = d["Volume"]/d["avg_vol"]
    d["ma20"]      = d["Close"].rolling(20).mean()
    d["ma50"]      = d["Close"].rolling(50).mean()
    delta = d["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100-100/(1+gain/loss.replace(0,np.nan))
    hl=(d["High"]-d["Low"])
    hc=(d["High"]-d["Close"].shift()).abs()
    lc=(d["Low"]-d["Close"].shift()).abs()
    d["atr"] = pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(14).mean()
    return d.dropna()


def _entry_signal(row, vol_mult: float) -> bool:
    return (float(row["vol_ratio"]) >= vol_mult
            and float(row["Close"]) > float(row["Open"])
            and float(row["Close"]) > float(row["ma50"])
            and 35 < float(row["rsi"]) < 75)


@st.cache_data(ttl=300, show_spinner=False)
def swing_backtest(df: pd.DataFrame, vol_mult: float = 2.5,
                   lookback: int = 20, hold_days: int = 7,
                   stop_pct: float = 0.04, target_pct: float = 0.08,
                   pos_pct: float = 0.20, commission: float = 0.0015,
                   slippage: float = 0.0005,
                   regime: pd.Series | None = None) -> dict:
    if df.empty or len(df) < 60:
        return {"error": "Need 60+ bars. Try a longer date range."}
    d = prepare_indicators(df, lookback)
    if len(d) < 30:
        return {"error": "Too many NaN. Try 2+ years of data."}
    capital0 = 1_000_000
    equity, trades, curve, signals = capital0, [], [], []
    position, hold = None, 0
    rows = list(d.iterrows())
    for i, (date, row) in enumerate(rows):
        curve.append({"date": date, "equity": equity})
        if position is None:
            risk_on = True
            if regime is not None and len(regime):
                rr = regime.reindex([date], method="ffill")
                if not rr.isna().all():
                    risk_on = bool(rr.iloc[0])
            if i < len(rows)-1 and risk_on and _entry_signal(row, vol_mult):
                nd, nrow = rows[i+1]
                ep     = float(nrow["Open"])*(1+slippage)
                shares = (equity*pos_pct)/ep
                position = {"ep": ep, "shares": shares, "date": nd,
                            "signal_date": date,
                            "cost_in": equity*pos_pct*commission,
                            "target": ep*(1+target_pct),
                            "stop":   ep*(1-stop_pct)}
                hold = 0
                signals.append({"date": date, "side": "BUY SIGNAL",
                                "price": round(float(row["Close"]),2),
                                "detail": f"{float(row['vol_ratio']):.1f}x vol surge"})
        else:
            if date <= position["date"]:
                continue
            hold += 1
            lo, hi, close = float(row["Low"]), float(row["High"]), float(row["Close"])
            xp, reason = None, None
            if lo <= position["stop"]:
                xp, reason = position["stop"], "Stop 🛑"
            elif hi >= position["target"]:
                xp, reason = position["target"], "Target 🎯"
            elif float(row["vol_ratio"]) < 0.55 and hold >= 3:
                xp, reason = close, "VolDry 📉"
            elif close < float(row["ma20"]) and hold >= 2:
                xp, reason = close, "MAFlip 🔄"
            elif hold >= hold_days or i == len(rows)-1:
                xp, reason = close, "Time ⏱"
            if xp is not None:
                xp *= (1-slippage)
                ep  = position["ep"]
                pnl = (position["shares"]*(xp-ep)
                       - position["cost_in"] - position["shares"]*xp*commission)
                equity += pnl
                trades.append({
                    "signal_date": str(position["signal_date"])[:10],
                    "entry_date":  str(position["date"])[:10],
                    "exit_date":   str(date)[:10],
                    "entry": round(ep,2), "exit": round(xp,2),
                    "target_lvl": round(position["target"],2),
                    "stop_lvl":   round(position["stop"],2),
                    "pnl": round(pnl,2),
                    "pnl_pct": round((xp/ep-1)*100,2),
                    "hold_days": hold, "exit_reason": reason,
                    "outcome": "WIN" if pnl>0 else "LOSS"})
                signals.append({"date": date, "side": "SELL SIGNAL",
                                "price": round(xp,2), "detail": reason})
                position, hold = None, 0
    if not trades:
        return {"error": f"No trades triggered at {vol_mult}x vol surge. "
                "Try lowering the multiplier or using a longer date range."}
    tdf   = pd.DataFrame(trades)
    eq_df = pd.DataFrame(curve).set_index("date")
    eq_s  = eq_df["equity"]
    rets  = eq_s.pct_change().dropna()
    wins  = tdf[tdf["outcome"]=="WIN"]; losses = tdf[tdf["outcome"]=="LOSS"]
    wr    = len(wins)/len(tdf)*100
    pf    = (abs(wins["pnl"].sum()/losses["pnl"].sum())
             if len(losses) and losses["pnl"].sum()!=0 else 99.0)
    ann_r = float(rets.mean()*TRADING_DAYS)
    ann_v = float(rets.std()*np.sqrt(TRADING_DAYS))
    dv    = rets[rets<0].std()*np.sqrt(TRADING_DAYS)
    sharpe  = ann_r/ann_v if ann_v>0 else 0.0
    sortino = ann_r/dv    if dv and dv>0 else 0.0
    dd      = (eq_s-eq_s.cummax())/eq_s.cummax()*100
    tot_r   = (equity-capital0)/capital0*100
    bh_ret  = (float(d["Close"].iloc[-1])/float(d["Close"].iloc[0])-1)*100
    max_cl=cl=0
    for o in tdf["outcome"].values:
        if o=="LOSS": cl+=1; max_cl=max(max_cl,cl)
        else: cl=0
    monthly=tdf.copy()
    monthly["month"]=pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    mpnl=monthly.groupby("month")["pnl"].sum()
    strat_ok  = pf>=1.3 and wr>=45 and sharpe>=0.3
    strat_ok2 = pf>=1.0 and wr>=40
    verdict   = ("✅ STRATEGY VALIDATED" if strat_ok else
                 "⚠️ MARGINAL EDGE" if strat_ok2 else "❌ STRATEGY FAILED")
    avg_w = round(wins["pnl_pct"].mean(),2)   if len(wins)   else 0.0
    avg_l = round(losses["pnl_pct"].mean(),2) if len(losses) else 0.0
    rr    = round(abs(avg_w/avg_l),2) if avg_l!=0 else 0.0
    return {"trades":tdf,"signals":pd.DataFrame(signals),
            "equity_curve":eq_df,"drawdown":dd,"monthly_pnl":mpnl,
            "total_trades":len(tdf),"win_rate":round(wr,1),
            "profit_factor":round(pf,2),"sharpe":round(sharpe,3),
            "sortino":round(sortino,3),"max_dd":round(float(dd.min()),2),
            "total_ret":round(tot_r,2),"final_equity":round(equity,2),
            "buy_hold_ret":round(bh_ret,2),"alpha_vs_bh":round(tot_r-bh_ret,2),
            "avg_win":avg_w,"avg_loss":avg_l,"rr":rr,
            "max_consec_loss":max_cl,"strat_verdict":verdict,
            "ann_ret":round(ann_r*100,2),"ann_vol":round(ann_v*100,2)}


def walk_forward(df: pd.DataFrame, vol_mults=(2.0, 2.5, 3.0, 3.5),
                 n_folds: int = 3, **kw) -> dict:
    """Optimize vol_mult on train window, evaluate on the NEXT unseen window."""
    n = len(df); fold = n // (n_folds + 1)
    if fold < 120:
        return {"error": "Need more history for walk-forward (use 3+ years)."}
    results = []
    for f in range(n_folds):
        train = df.iloc[: fold*(f+1)]
        test  = df.iloc[fold*(f+1): fold*(f+2)]
        best_vm, best_pf = None, -np.inf
        for vm in vol_mults:
            r = swing_backtest(train, vol_mult=vm, **kw)
            if "error" not in r and r["profit_factor"] > best_pf:
                best_pf, best_vm = r["profit_factor"], vm
        if best_vm is None: continue
        oos = swing_backtest(test, vol_mult=best_vm, **kw)
        results.append({"Fold": f+1, "Best Vol×": best_vm,
                        "IS PF": round(best_pf, 2),
                        "OOS PF": oos.get("profit_factor", 0) if "error" not in oos else 0,
                        "OOS WR %": oos.get("win_rate", 0) if "error" not in oos else 0,
                        "OOS Ret %": oos.get("total_ret", 0) if "error" not in oos else 0,
                        "OOS Trades": oos.get("total_trades", 0) if "error" not in oos else 0})
    if not results:
        return {"error": "No valid folds. Widen the date range."}
    rdf = pd.DataFrame(results)
    avg_oos = float(rdf["OOS PF"].mean())
    verdict = ("✅ ROBUST — edge survives out-of-sample" if avg_oos >= 1.2 else
               "⚠️ FRAGILE — edge weakens out-of-sample" if avg_oos >= 1.0 else
               "❌ OVERFIT — edge disappears on unseen data")
    return {"table": rdf, "avg_oos_pf": round(avg_oos, 2), "verdict": verdict}

# ══════════════ END OF PART 1 — PASTE PART 2 DIRECTLY BELOW ══════════════
# ════════════════════════════════════════════════════════════════════════════
# ML PREDICTION ENGINE — gradient boosting, 5-day forward returns,
# purged walk-forward CV (no leakage)
# ════════════════════════════════════════════════════════════════════════════

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, v = d["Close"], d["Volume"]
    r = np.log(c / c.shift(1))
    f = pd.DataFrame(index=d.index)
    for lb in (1, 2, 3, 5, 10, 21):
        f[f"ret_{lb}d"] = c.pct_change(lb)
    for lb in (5, 10, 21, 63):
        f[f"vol_{lb}d"] = r.rolling(lb).std()
    f["vol_regime"] = f["vol_5d"] / f["vol_63d"]
    for lb in (5, 21, 63):
        f[f"volu_{lb}d"] = v / v.rolling(lb).mean()
    delta = c.diff()
    g = delta.clip(lower=0).rolling(14).mean()
    l = (-delta.clip(upper=0)).rolling(14).mean()
    f["rsi"] = 100 - 100 / (1 + g / l.replace(0, np.nan))
    for lb in (20, 50):
        f[f"dist_ma{lb}"] = c / c.rolling(lb).mean() - 1
    f["hl_range"] = (d["High"] - d["Low"]) / c
    f["gap"]      = d["Open"] / c.shift(1) - 1
    hl = d["High"]-d["Low"]; hc=(d["High"]-c.shift()).abs(); lc=(d["Low"]-c.shift()).abs()
    f["atr_pct"]  = pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(14).mean() / c
    f["mom_6m"]   = c.pct_change(126)
    f["hi52"]     = c / c.rolling(252, min_periods=60).max() - 1
    f["target"]   = c.shift(-5) / c - 1          # 5-day forward return
    return f


@st.cache_data(ttl=1800, show_spinner=False)
def ml_engine(df: pd.DataFrame, n_splits: int = 5, embargo: int = 5) -> dict:
    try:
        from sklearn.ensemble import GradientBoostingRegressor
        from scipy.stats import spearmanr
    except ImportError:
        return {"error": "Run: pip install scikit-learn"}
    f = build_features(df)
    feat_cols = [c for c in f.columns if c != "target"]
    data = f.dropna()
    if len(data) < 300:
        return {"error": "Need 300+ clean bars — use 2y+ of data."}
    X, y = data[feat_cols].values, data["target"].values
    n = len(data); test_size = n // (n_splits + 1)
    oos_p, oos_t = [], []
    for k in range(n_splits):                     # purged walk-forward CV
        t0 = n - (n_splits - k) * test_size
        tr_end = t0 - embargo                     # embargo gap = no leakage
        if tr_end < 150: continue
        m = GradientBoostingRegressor(n_estimators=200, max_depth=3,
                                      learning_rate=0.05, subsample=0.8,
                                      random_state=42)
        m.fit(X[:tr_end], y[:tr_end])
        oos_p += list(m.predict(X[t0:t0+test_size]))
        oos_t += list(y[t0:t0+test_size])
    if len(oos_p) < 50:
        return {"error": "Not enough OOS samples — use longer history."}
    op, ot = np.array(oos_p), np.array(oos_t)
    ic  = float(spearmanr(op, ot).correlation)
    hit = float(np.mean(np.sign(op) == np.sign(ot)) * 100)
    k5  = max(1, len(op)//5)
    order = np.argsort(op)
    q_spread = float((ot[order[-k5:]].mean() - ot[order[:k5]].mean()) * 100)
    final = GradientBoostingRegressor(n_estimators=200, max_depth=3,
                                      learning_rate=0.05, subsample=0.8,
                                      random_state=42)
    final.fit(X, y)
    live = f[feat_cols].dropna()
    pred_5d = float(final.predict(live.iloc[[-1]].values)[0]) * 100
    imp = (pd.DataFrame({"feature": feat_cols,
                         "importance": final.feature_importances_})
           .sort_values("importance", ascending=False).head(12))
    verdict = ("✅ REAL PREDICTIVE SIGNAL" if ic >= 0.08 else
               "⚠️ WEAK SIGNAL — use as filter only" if ic >= 0.03 else
               "❌ NO EDGE — model is noise for this stock")
    return {"pred_5d_pct": round(pred_5d, 2), "ic": round(ic, 4),
            "hit_rate": round(hit, 1), "q_spread": round(q_spread, 2),
            "n_oos": len(op), "importances": imp, "verdict": verdict}


def render_ml_lab():
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-left:3px solid {TEAL};
    border-radius:8px;padding:14px 18px;margin-bottom:14px;'>
      <div style='font-family:{MONO};font-size:11px;font-weight:700;color:{TEAL};
        letter-spacing:1.5px;margin-bottom:6px;'>MODE 4 · ML PREDICTION LAB</div>
      <div style='font-size:12.5px;color:{IVORY};line-height:1.8;'>
      Gradient boosting on <b>26 engineered features</b> predicting <b>5-day forward
      returns</b>, validated with <b>purged walk-forward CV</b>. Judge it by <b>IC</b>
      (rank correlation of predictions vs reality): 0.03+ is usable, 0.08+ is a real
      edge. Most stocks show none — that honesty is the feature.
      </div>
    </div>""", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2,1,1])
    sym    = c1.text_input("Symbol", "RELIANCE")
    period = c2.selectbox("History", ["2y","3y","5y"], index=1)
    run    = c3.button("🤖 TRAIN MODEL", type="primary", use_container_width=True)
    if not run: return
    with st.spinner(f"Training on {sym.upper()}..."):
        df = fetch_stock(sym, period)
        if df.empty: st.error("No data."); return
        ml = ml_engine(df)
    if "error" in ml: st.error(ml["error"]); return
    v_c = GREEN if "✅" in ml["verdict"] else AMBER if "⚠️" in ml["verdict"] else RED
    p_c = GREEN if ml["pred_5d_pct"] > 0 else RED
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {v_c}40;border-left:5px solid {v_c};
    border-radius:10px;padding:18px 24px;margin:12px 0;'>
      <div style='font-family:{MONO};font-size:20px;font-weight:800;color:{v_c};
        margin-bottom:6px;'>{ml["verdict"]}</div>
      <div style='font-family:{MONO};font-size:13px;color:{IVORY};'>
        Model forecast next 5 days: <b style='color:{p_c};font-size:17px;'>
        {ml["pred_5d_pct"]:+.2f}%</b>
        <span style='font-size:10px;color:{MUTE2};'>
        (only act on this if IC ≥ 0.03)</span></div>
    </div>""", unsafe_allow_html=True)
    _mrow([("IC (RANK CORR)", f"{ml['ic']:.4f}"),
           ("HIT RATE OOS",   f"{ml['hit_rate']}%"),
           ("Q5-Q1 SPREAD",   f"{ml['q_spread']:+.2f}%"),
           ("OOS SAMPLES",    str(ml["n_oos"]))])
    imp = ml["importances"]
    fig = go.Figure(go.Bar(x=imp["importance"], y=imp["feature"],
                           orientation="h", marker_color=TEAL, opacity=0.85))
    fig.update_layout(yaxis=dict(autorange="reversed"))
    st.plotly_chart(_layout(fig, "WHAT THE MODEL LEARNED · FEATURE IMPORTANCE", 380),
                    use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# MODE 3 — CROSS-SECTIONAL ALPHA SCANNER
# ════════════════════════════════════════════════════════════════════════════

NIFTY_UNIVERSE = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN",
    "BHARTIARTL","ITC","LT","HINDUNILVR","AXISBANK","KOTAKBANK","BAJFINANCE",
    "MARUTI","TITAN","SUNPHARMA","TATAMOTORS","NTPC","POWERGRID","TATASTEEL",
    "M&M","ULTRACEMCO","ASIANPAINT","WIPRO","JSWSTEEL"]

@st.cache_data(ttl=1800, show_spinner=False)
def alpha_scan(symbols: tuple, period: str = "1y") -> pd.DataFrame:
    rows = []
    for s in symbols:
        df = fetch_stock(s, period)
        if df.empty or len(df) < 140:
            continue
        c = df["Close"]; v = df["Volume"]
        skip = 21
        lb   = min(126, len(c)-skip-1)
        mom  = float(c.iloc[-skip] / c.iloc[-(lb+skip)] - 1) * 100
        vol60 = float(np.log(c/c.shift(1)).dropna().tail(60).std()
                      * np.sqrt(TRADING_DAYS)) * 100
        vtrend = float(v.tail(20).mean() / v.tail(100).mean())
        hi52   = float(c.iloc[-1] / c.tail(252).max()) * 100
        delta  = c.diff()
        g = delta.clip(lower=0).rolling(14).mean()
        l = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = float((100-100/(1+g/l.replace(0,np.nan))).iloc[-1])
        rows.append({"Symbol": s, "Price": round(float(c.iloc[-1]),2),
                     "Mom6m %": round(mom,1), "VolTrend": round(vtrend,2),
                     "AnnVol %": round(vol60,1), "52wHi %": round(hi52,1),
                     "RSI": round(rsi,1)})
    if not rows:
        return pd.DataFrame()
    sdf = pd.DataFrame(rows)
    def z(col, invert=False):
        s = (sdf[col]-sdf[col].mean())/(sdf[col].std() or 1)
        return -s if invert else s
    sdf["ALPHA"] = (0.35*z("Mom6m %") + 0.20*z("VolTrend")
                    + 0.15*z("AnnVol %", invert=True) + 0.20*z("52wHi %")
                    + 0.10*(-abs(sdf["RSI"]-55)/20)).round(3)
    return sdf.sort_values("ALPHA", ascending=False).reset_index(drop=True)


def render_mode3():
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-left:3px solid {PURPLE};
    border-radius:8px;padding:14px 18px;margin-bottom:14px;'>
      <div style='font-family:{MONO};font-size:11px;font-weight:700;color:{PURPLE};
        letter-spacing:1.5px;margin-bottom:6px;'>MODE 3 · CROSS-SECTIONAL ALPHA SCANNER</div>
      <div style='font-size:12.5px;color:{IVORY};line-height:1.8;'>
      This is how funds pick WHAT to trade before deciding WHEN. The universe is ranked
      on <b>momentum (35%)</b>, <b>volume trend (20%)</b>, <b>52w-high proximity (20%)</b>,
      <b>low volatility (15%)</b> and <b>RSI positioning (10%)</b>, z-scored cross-sectionally.
      Feed the top ranks into Mode 1 or Mode 2.
      </div>
    </div>""", unsafe_allow_html=True)
    c1, c2 = st.columns([3,1])
    syms = c1.text_input("Universe (comma-separated, blank = NIFTY 25)", "")
    run  = c2.button("🧠 SCAN UNIVERSE", type="primary", use_container_width=True)
    if not run:
        st.info("Press SCAN to rank the universe."); return
    universe = tuple(s.strip().upper() for s in syms.split(",") if s.strip()) \
               or tuple(NIFTY_UNIVERSE)
    with st.spinner(f"Scanning {len(universe)} symbols..."):
        sdf = alpha_scan(universe)
    if sdf.empty:
        st.error("No data returned. Check symbols."); return
    st.markdown(_sec("ALPHA RANKING · TOP = STRONGEST", PURPLE), unsafe_allow_html=True)
    st.dataframe(sdf, use_container_width=True, hide_index=True)
    top = sdf.head(5)["Symbol"].tolist()
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {GREEN}35;border-left:4px solid {GREEN};
    border-radius:10px;padding:14px 20px;font-family:{MONO};font-size:12px;color:{IVORY};'>
      🎯 TOP 5 CANDIDATES: <b style='color:{GREEN};'>{", ".join(top)}</b><br>
      <span style='font-size:10px;color:{MUTE2};'>Paste these into MODE 2 to backtest
      the strategy on today's strongest names.</span>
    </div>""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# MODE 5 — LIVE SCREENER
# ════════════════════════════════════════════════════════════════════════════

SCREENER_UNIVERSE = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN",
 "BHARTIARTL","ITC","LT","HINDUNILVR","AXISBANK","KOTAKBANK","BAJFINANCE",
 "MARUTI","TITAN","SUNPHARMA","TATAMOTORS","NTPC","POWERGRID","TATASTEEL",
 "M&M","ULTRACEMCO","ASIANPAINT","WIPRO","JSWSTEEL","ADANIENT","ADANIPORTS",
 "BAJAJFINSV","CIPLA","COALINDIA","DIVISLAB","DRREDDY","EICHERMOT","GRASIM",
 "HCLTECH","HDFCLIFE","HEROMOTOCO","HINDALCO","INDUSINDBK","NESTLEIND",
 "ONGC","SBILIFE","SHRIRAMFIN","TATACONSUM","TECHM","APOLLOHOSP","BPCL",
 "BRITANNIA","DLF","VEDL"]

@st.cache_data(ttl=900, show_spinner=False)
def screen_universe(symbols: tuple, vol_mult: float = 2.0,
                    use_regime: bool = True) -> pd.DataFrame:
    regime = fetch_regime() if use_regime else pd.Series(dtype=bool)
    risk_on = bool(regime.iloc[-1]) if len(regime) else True
    rows = []
    for s in symbols:
        try:
            df = fetch_stock(s, "1y")
            if df.empty or len(df) < 130: continue
            d = prepare_indicators(df)
            if d.empty: continue
            last = d.iloc[-1]
            c = d["Close"]
            vr, rsi = float(last["vol_ratio"]), float(last["rsi"])
            above50 = float(last["Close"]) > float(last["ma50"])
            bull    = float(last["Close"]) > float(last["Open"])
            active  = (vr >= vol_mult and bull and above50
                       and 35 < rsi < 75 and risk_on)
            setup   = (not active and vr >= 1.4 and above50
                       and 35 < rsi < 75 and risk_on)
            mom  = float(c.iloc[-1]/c.iloc[-min(126,len(c)-1)]-1)*100
            hi52 = float(c.iloc[-1]/c.tail(252).max())*100
            chg  = float(c.iloc[-1]/c.iloc[-2]-1)*100
            rows.append({"Symbol": s, "Price": round(float(c.iloc[-1]),2),
                         "Chg %": round(chg,2),
                         "Status": "🟢 ACTIVE BUY" if active else
                                   "👀 SETUP" if setup else "—",
                         "VolRatio": round(vr,2), "RSI": round(rsi,1),
                         ">50MA": "✅" if above50 else "❌",
                         "Mom6m %": round(mom,1), "52wHi %": round(hi52,1),
                         "_rank": (2 if active else 1 if setup else 0)})
        except Exception:
            continue
    if not rows: return pd.DataFrame()
    sdf = pd.DataFrame(rows)
    z = lambda col: (sdf[col]-sdf[col].mean())/(sdf[col].std() or 1)
    sdf["Alpha"] = (0.4*z("Mom6m %") + 0.3*z("52wHi %") + 0.3*z("VolRatio")).round(2)
    sdf = sdf.sort_values(["_rank","Alpha"], ascending=False).drop(columns="_rank")
    return sdf.reset_index(drop=True)


def render_screener():
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-left:3px solid {GREEN};
    border-radius:8px;padding:14px 18px;margin-bottom:14px;'>
      <div style='font-family:{MONO};font-size:11px;font-weight:700;color:{GREEN};
        letter-spacing:1.5px;margin-bottom:6px;'>MODE 5 · LIVE MARKET SCREENER</div>
      <div style='font-size:12.5px;color:{IVORY};line-height:1.8;'>
      Scans <b>50 liquid NSE names</b> and flags which have an <b>ACTIVE BUY signal
      right now</b> (vol surge + trend + RSI + market regime), which are <b>setting up</b>,
      and ranks everything by alpha score. Optionally runs the <b>ML engine</b> on
      flagged names for a 5-day forecast. This is your morning desk view.
      </div>
    </div>""", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    vol_mult   = c1.slider("Signal Vol ×", 1.5, 4.0, 2.0, 0.5)
    use_regime = c2.checkbox("🛡 Regime Filter", value=True)
    run_ml     = c3.checkbox("🤖 ML on flagged", value=False)
    run        = c4.button("📡 SCAN MARKET", type="primary", use_container_width=True)
    if not run: return
    with st.spinner(f"Scanning {len(SCREENER_UNIVERSE)} stocks..."):
        sdf = screen_universe(tuple(SCREENER_UNIVERSE), vol_mult, use_regime)
    if sdf.empty: st.error("Scan failed — no data."); return
    active = sdf[sdf["Status"].str.contains("ACTIVE")]
    setup  = sdf[sdf["Status"].str.contains("SETUP")]
    if run_ml and (len(active) + len(setup)):
        flagged = (list(active["Symbol"]) + list(setup["Symbol"]))[:10]
        preds = {}
        prog = st.progress(0.0, "Training ML on flagged names...")
        for j, s in enumerate(flagged):
            ml = ml_engine(fetch_stock(s, "3y"))
            preds[s] = (f"{ml['pred_5d_pct']:+.2f}% (IC {ml['ic']:.3f})"
                        if "error" not in ml else "n/a")
            prog.progress((j+1)/len(flagged))
        prog.empty()
        sdf["ML 5d Forecast"] = sdf["Symbol"].map(preds).fillna("")
    reg = fetch_regime()
    risk_on = (not use_regime) or (len(reg) and bool(reg.iloc[-1]))
    _mrow([("SCANNED", str(len(sdf))),
           ("🟢 ACTIVE SIGNALS", str(len(active))),
           ("👀 SETUPS BUILDING", str(len(setup))),
           ("MARKET REGIME", "RISK-ON ✅" if risk_on else "RISK-OFF ⛔")])
    if len(active):
        st.markdown(_sec("🟢 ACTIVE BUY SIGNALS · TODAY", GREEN), unsafe_allow_html=True)
        st.dataframe(active, use_container_width=True, hide_index=True)
    else:
        st.info("No active signals today. Patience is a position — firms sit out most days.")
    if len(setup):
        st.markdown(_sec("👀 SETUPS BUILDING · WATCHLIST", AMBER), unsafe_allow_html=True)
        st.dataframe(setup, use_container_width=True, hide_index=True)
    st.markdown(_sec("FULL UNIVERSE · RANKED BY ALPHA"), unsafe_allow_html=True)
    st.dataframe(sdf, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════════════════
# AI / RULE NARRATIVE
# ════════════════════════════════════════════════════════════════════════════

def _ai_note(symbol, price, vpin, garch, fp, vp, api_key):
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "Senior NSE swing analyst. Direct, data-driven, under 100 words. "
                "No fluff. Educational only, not SEBI advice."))
        prompt = (
            f"{symbol} @ ₹{price:.2f} | VPIN: {vpin['toxicity']} ({vpin['percentile']:.0f}th pct) | "
            f"GARCH: {garch['annual_vol_pct']:.0f}% ann, {garch['regime']} | "
            f"Footprint: {fp['n_events']} surges, bias {fp['bias_score']:+d}/3 | "
            f"POC ₹{vp['poc']:.0f} VA ₹{vp['va_low']:.0f}–₹{vp['va_high']:.0f}\n"
            "3 sentences: smart money activity, setup quality, key levels to watch.")
        return m.generate_content(prompt).text.strip()
    except Exception:
        return ""


def _rule_note(symbol, price, vpin, garch, fp, vp):
    parts = []
    if vpin["percentile"] > 70:
        parts.append(f"⚡ <b>Informed money active</b> — VPIN at {vpin['percentile']:.0f}th pct. Big move likely.")
    elif vpin["percentile"] < 30:
        parts.append(f"😴 Retail-dominated flow in {symbol}. No institutional conviction yet.")
    else:
        parts.append(f"📊 Mixed order flow. Some institutional activity, no full conviction.")
    if "HIGH" in garch["regime"]:
        parts.append(f"⚠️ Vol elevated ({garch['annual_vol_pct']:.0f}%) — reduce size, widen stops.")
    elif "LOW" in garch["regime"]:
        parts.append(f"📉 Vol compressed ({garch['annual_vol_pct']:.0f}%) — breakout setup building.")
    else:
        parts.append(f"📊 Normal vol at {garch['annual_vol_pct']:.0f}% annual.")
    if fp["bias_score"] > 0:
        parts.append(f"🐂 Bullish footprint. POC ₹{vp['poc']:,.0f} = key support.")
    elif fp["bias_score"] < 0:
        parts.append(f"🐻 Bearish footprint. VA High ₹{vp['va_high']:,.0f} = resistance.")
    else:
        parts.append(f"VA ₹{vp['va_low']:,.0f}–₹{vp['va_high']:,.0f} · POC ₹{vp['poc']:,.0f}.")
    parts.append(f"<span style='font-size:10px;color:{MUTE2};'>Educational · Not SEBI advice</span>")
    return " ".join(parts)


# ════════════════════════════════════════════════════════════════════════════
# PLOTLY CHARTS
# ════════════════════════════════════════════════════════════════════════════

def chart_price_vwap(df, vwap_df, vp, fp, symbol):
    d  = df.tail(120).copy()
    vd = vwap_df.tail(120).copy()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75,0.25], vertical_spacing=0.02)
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor=GREEN_27, decreasing_fillcolor=RED_27,
        name="Price", showlegend=False), row=1, col=1)
    if "vwap" in vd.columns:
        fig.add_trace(go.Scatter(x=vd.index, y=vd["vwap"],
            line=dict(color=ACCENT,width=1.8,dash="dot"), name="VWAP"), row=1,col=1)
        if "vwap_upper" in vd.columns:
            fig.add_trace(go.Scatter(
                x=list(vd.index)+list(vd.index[::-1]),
                y=list(vd["vwap_upper"])+list(vd["vwap_lower"][::-1]),
                fill="toself", fillcolor="rgba(201,162,39,0.06)",
                line=dict(color="rgba(0,0,0,0)"), name="VWAP Band"), row=1,col=1)
    fig.add_hline(y=vp["poc"], line_color=PURPLE, line_dash="dash", line_width=1.5,
                  annotation_text=f"POC ₹{vp['poc']:,.0f}",
                  annotation_font_color=PURPLE, row=1,col=1)
    fig.add_hrect(y0=vp["va_low"], y1=vp["va_high"],
                  fillcolor="rgba(76,141,214,0.06)", line_width=0, row=1,col=1)
    ev = fp["events"]
    if not ev.empty:
        d_set = set(d.index)
        for _, e in ev[ev["date"].isin(d_set)].iterrows():
            ed = e["date"]
            if ed in d.index:
                yv = float(d.loc[ed,"Low"])*0.994 if e["bullish"] else float(d.loc[ed,"High"])*1.006
                sym_marker = "triangle-up" if e["bullish"] else "triangle-down"
                fig.add_trace(go.Scatter(x=[ed], y=[yv], mode="markers",
                    marker=dict(color=GREEN if e["bullish"] else RED,
                                size=12, symbol=sym_marker),
                    showlegend=False, hovertext=e["type"]), row=1,col=1)
    vc = [GREEN if float(d["Close"].iloc[i])>=float(d["Open"].iloc[i]) else RED
          for i in range(len(d))]
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=vc, opacity=0.55,
                         showlegend=False), row=2,col=1)
    avg_v = float(d["Volume"].mean())
    fig.add_hline(y=avg_v*2.5, line_color=ACCENT, line_dash="dot", line_width=1,
                  annotation_text="2.5× threshold",
                  annotation_font_color=ACCENT, row=2,col=1)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL2,
        height=560, margin=dict(l=8,r=8,t=44,b=8),
        title=dict(text=f"{symbol} · PRICE + VWAP + VOLUME PROFILE + SURGE EVENTS",
                   font=dict(size=12,color=ACCENT)),
        font=dict(family="monospace",size=11,color=IVORY),
        xaxis2=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        yaxis2=dict(gridcolor=BORDER), xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h",y=1.03,x=0,font=dict(size=10)))
    return fig


def chart_volume_profile(vp):
    colors = [ACCENT if abs(p-vp["poc"])<(vp["va_high"]-vp["va_low"])*0.05
              else BLUE if vp["va_low"]<=p<=vp["va_high"] else MUTE2
              for p in vp["price_mid"]]
    fig = go.Figure(go.Bar(
        y=[f"₹{p:,.0f}" for p in vp["price_mid"]],
        x=vp["vol_bins"], orientation="h",
        marker_color=colors, opacity=0.85))
    return _layout(fig, "VOLUME PROFILE · Gold=POC · Blue=Value Area", height=460)


def chart_garch(garch, close):
    sig = garch["sigma_series"].tail(250)*np.sqrt(TRADING_DAYS)*100
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=["Price","GARCH Vol (Annual %)"],
                        vertical_spacing=0.06)
    fig.add_trace(go.Scatter(x=close.index[-250:], y=close.tail(250).values,
        line=dict(color=IVORY,width=1.4)), row=1,col=1)
    fig.add_trace(go.Scatter(x=sig.index, y=sig.values,
        line=dict(color=AMBER,width=2), fill="tozeroy",
        fillcolor="rgba(245,158,11,0.09)"), row=2,col=1)
    fig.add_hline(y=garch["hist_avg_annual"], line_color=MUTE2, line_dash="dash",
                  annotation_text=f"Avg {garch['hist_avg_annual']:.1f}%",
                  annotation_font_color=MUTE2, row=2,col=1)
    fig.update_layout(template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL2,
        height=420, showlegend=False, margin=dict(l=8,r=8,t=44,b=8),
        font=dict(family="monospace",size=11,color=IVORY),
        xaxis=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        xaxis2=dict(gridcolor=BORDER), yaxis2=dict(gridcolor=BORDER),
        title=dict(text="GARCH(1,1) CONDITIONAL VOLATILITY",
                   font=dict(size=12,color=ACCENT)))
    return fig


def chart_monte_carlo(mc, price, symbol):
    paths = mc["paths"]
    idx   = np.random.default_rng(0).choice(len(paths), size=min(150,len(paths)), replace=False)
    fig   = go.Figure()
    for i in idx:
        fig.add_trace(go.Scatter(y=paths[i], mode="lines",
            line=dict(color="rgba(76,141,214,0.05)",width=1), showlegend=False))
    x = list(range(1, mc["horizon"]+1))
    p10 = np.percentile(paths,10,axis=0)
    p50 = np.percentile(paths,50,axis=0)
    p90 = np.percentile(paths,90,axis=0)
    fig.add_trace(go.Scatter(x=x,y=p90,line=dict(color=GREEN,width=2,dash="dash"),name="P90"))
    fig.add_trace(go.Scatter(x=x,y=p50,line=dict(color=ACCENT,width=2.5),name="P50 Median"))
    fig.add_trace(go.Scatter(x=x,y=p10,line=dict(color=RED,width=2,dash="dash"),name="P10"))
    fig.add_hline(y=price, line_color=MUTE, line_dash="dot",
                  annotation_text=f"Current ₹{price:,.0f}", annotation_font_color=MUTE)
    fig.update_layout(showlegend=True, legend=dict(orientation="h",y=1.05,x=0))
    return _layout(fig, f"MONTE CARLO · {mc['n_sims']:,} PATHS · {mc['horizon']}-DAY · {symbol}", height=400)


def chart_vpin(vpin_data):
    s = vpin_data["series"]
    if s.empty: return go.Figure()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.index, y=s.values, fill="tozeroy",
        fillcolor="rgba(155,89,182,0.10)",
        line=dict(color=PURPLE,width=2), name="VPIN"))
    fig.add_hline(y=float(s.quantile(0.70)), line_color=RED, line_dash="dash",
                  annotation_text="70th · High Toxicity", annotation_font_color=RED)
    fig.add_hline(y=float(s.quantile(0.30)), line_color=GREEN, line_dash="dash",
                  annotation_text="30th · Low Toxicity", annotation_font_color=GREEN)
    return _layout(fig, "VPIN — ORDER FLOW TOXICITY", height=300)


def chart_surge_history(fp):
    ev = fp["events"]
    if ev.empty: return _layout(go.Figure(), "SURGE HISTORY", 300)
    bull = ev[ev["bullish"]]; bear = ev[~ev["bullish"]]
    fig  = go.Figure()
    if not bull.empty:
        fig.add_trace(go.Scatter(x=bull["date"],y=bull["vol_ratio"],mode="markers",
            marker=dict(color=GREEN,size=(bull["strength"]*3+7).tolist(),symbol="triangle-up"),
            name="Bull Surge",text=[f"{r:.1f}×" for r in bull["vol_ratio"]]))
    if not bear.empty:
        fig.add_trace(go.Scatter(x=bear["date"],y=bear["vol_ratio"],mode="markers",
            marker=dict(color=RED,size=(bear["strength"]*3+7).tolist(),symbol="triangle-down"),
            name="Bear Surge",text=[f"{r:.1f}×" for r in bear["vol_ratio"]]))
    fig.add_hline(y=2.5, line_color=ACCENT, line_dash="dot",
                  annotation_text="Surge threshold", annotation_font_color=ACCENT)
    fig.update_layout(showlegend=True)
    return _layout(fig, "INSTITUTIONAL VOLUME SURGE HISTORY · Size = Strength", 320)


def chart_backtest_price(df, bt):
    tdf = bt["trades"]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75,0.25], vertical_spacing=0.02)
    d = df.copy()
    vc = [GREEN if float(d["Close"].iloc[i])>=float(d["Open"].iloc[i]) else RED
          for i in range(len(d))]
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor=GREEN_20, decreasing_fillcolor=RED_20,
        name="Price", showlegend=False), row=1,col=1)
    buy_x=[]; buy_y=[]; win_x=[]; win_y=[]; los_x=[]; los_y=[]
    for _, t in tdf.iterrows():
        ed = pd.Timestamp(t["entry_date"]); xd = pd.Timestamp(t["exit_date"])
        if ed in d.index:
            buy_x.append(ed); buy_y.append(float(t["entry"])*0.993)
        if xd in d.index:
            if t["outcome"]=="WIN": win_x.append(xd); win_y.append(float(t["exit"])*1.005)
            else: los_x.append(xd); los_y.append(float(t["exit"])*1.005)
    if buy_x:
        fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode="markers+text",
            marker=dict(color=GREEN, size=12, symbol="triangle-up",
                        line=dict(color=IVORY,width=1)),
            text=["BUY"]*len(buy_x), textposition="bottom center",
            textfont=dict(color=GREEN,size=9), name="Entry"), row=1,col=1)
    if win_x:
        fig.add_trace(go.Scatter(x=win_x, y=win_y, mode="markers+text",
            marker=dict(color=ACCENT, size=12, symbol="circle",
                        line=dict(color=IVORY,width=1)),
            text=["WIN"]*len(win_x), textposition="top center",
            textfont=dict(color=ACCENT,size=9), name="Exit WIN"), row=1,col=1)
    if los_x:
        fig.add_trace(go.Scatter(x=los_x, y=los_y, mode="markers+text",
            marker=dict(color=RED, size=12, symbol="x",
                        line=dict(color=IVORY,width=1)),
            text=["LOSS"]*len(los_x), textposition="top center",
            textfont=dict(color=RED,size=9), name="Exit LOSS"), row=1,col=1)
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"],
                         marker_color=vc, opacity=0.5, showlegend=False), row=2,col=1)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL2,
        height=580, margin=dict(l=8,r=8,t=44,b=8),
        title=dict(text="BACKTEST · PRICE CHART WITH BUY/WIN/LOSS MARKERS",
                   font=dict(size=12,color=ACCENT)),
        font=dict(family="monospace",size=11,color=IVORY),
        xaxis2=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        yaxis2=dict(gridcolor=BORDER), xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h",y=1.03,x=0,font=dict(size=10)))
    return fig


def chart_equity(bt):
    eq = bt["equity_curve"]["equity"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, fill="tozeroy",
        fillcolor="rgba(201,162,39,0.07)", line=dict(color=ACCENT,width=2)))
    fig.add_hline(y=1_000_000, line_color=MUTE, line_dash="dash",
                  annotation_text="Initial ₹10L", annotation_font_color=MUTE)
    return _layout(fig, "EQUITY CURVE", height=280)


def chart_drawdown(bt):
    dd = bt["drawdown"]
    fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy",
        fillcolor="rgba(232,85,78,0.12)", line=dict(color=RED,width=1.5)))
    return _layout(fig, "DRAWDOWN (%)", height=220)


def chart_monthly_pnl(bt):
    mp = bt["monthly_pnl"]
    fig = go.Figure(go.Bar(
        x=[str(p) for p in mp.index], y=mp.values,
        marker_color=[GREEN if v>=0 else RED for v in mp.values],
        opacity=0.85, text=[f"₹{v:,.0f}" for v in mp.values],
        textposition="outside", textfont=dict(size=8)))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    return _layout(fig, "MONTHLY P&L (₹)", height=260)


def chart_exit_breakdown(bt):
    tdf = bt["trades"]
    if "exit_reason" not in tdf.columns: return go.Figure()
    er  = tdf.groupby(["exit_reason","outcome"]).size().unstack(fill_value=0)
    fig = go.Figure()
    for o, c in [("WIN",GREEN),("LOSS",RED)]:
        if o in er.columns:
            fig.add_trace(go.Bar(name=o, x=er.index, y=er[o],
                                 marker_color=c, opacity=0.85))
    fig.update_layout(barmode="stack", showlegend=True,
                      legend=dict(orientation="h",y=1.05,x=0))
    return _layout(fig, "EXIT REASON BREAKDOWN", height=240)

# ══════════════ END OF PART 2 — PASTE PART 3 DIRECTLY BELOW ══════════════
# ════════════════════════════════════════════════════════════════════════════
# MODE 2 — BATCH SIGNAL BACKTESTER
# ════════════════════════════════════════════════════════════════════════════

def render_mode2():
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-left:3px solid {TEAL};
    border-radius:8px;padding:14px 18px;margin-bottom:14px;'>
      <div style='font-family:{MONO};font-size:11px;font-weight:700;color:{TEAL};
        letter-spacing:1.5px;margin-bottom:6px;'>MODE 2 · SIGNAL BACKTESTER</div>
      <div style='font-size:12.5px;color:{IVORY};line-height:1.8;'>
      Type one or more NSE symbols, pick a date range, and the terminal backtests the
      <b>main strategy</b> showing <b>every BUY and SELL signal date</b>, the full
      trade log, and <b>alpha vs buy-and-hold</b> per stock.
      </div>
    </div>""", unsafe_allow_html=True)

    with st.form("mode2_form"):
        c1, c2, c3 = st.columns([2,1,1])
        syms  = c1.text_input("Symbols (comma-separated)", "RELIANCE, TCS, HDFCBANK")
        start = c2.date_input("From", datetime.date(2021,1,1))
        end   = c3.date_input("To", datetime.date.today())
        c4, c5, c6, c7 = st.columns(4)
        vol_mult = c4.slider("Vol Surge ×", 1.5, 5.0, 2.5, 0.5)
        hold_d   = c5.slider("Max Hold Days", 3, 21, 7)
        stop_p   = c6.slider("Stop Loss %", 1, 10, 4)
        tgt_p    = c7.slider("Target %", 2, 20, 8)
        use_reg  = st.checkbox("🛡 NIFTY 200-DMA Regime Filter", value=True)
        run = st.form_submit_button("▶ RUN MODE 2", type="primary", use_container_width=True)
    if not run:
        st.info("Enter symbols and date range, then press RUN MODE 2."); return
    if start >= end:
        st.error("From date must be before To date."); return

    regime  = fetch_regime() if use_reg else None
    summary = []
    symbols = [s.strip().upper() for s in syms.split(",") if s.strip()]
    if not symbols:
        st.error("Enter at least one symbol."); return

    for sym in symbols:
        with st.spinner(f"Backtesting {sym}..."):
            df = fetch_range(sym, str(start), str(end))
            if df.empty:
                st.warning(f"{sym}: no data in that range."); continue
            bt = swing_backtest(df, vol_mult=vol_mult, hold_days=hold_d,
                                stop_pct=stop_p/100, target_pct=tgt_p/100,
                                regime=regime)
        if "error" in bt:
            st.warning(f"{sym}: {bt['error']}"); continue

        summary.append({"Symbol": sym, "Trades": bt["total_trades"],
                        "Win %": bt["win_rate"], "PF": bt["profit_factor"],
                        "Sharpe": bt["sharpe"], "Strategy %": bt["total_ret"],
                        "Buy&Hold %": bt["buy_hold_ret"],
                        "Alpha %": bt["alpha_vs_bh"], "MaxDD %": bt["max_dd"]})

        a_c = GREEN if bt["alpha_vs_bh"] >= 0 else RED
        with st.expander(f"📊 {sym} — {bt['total_trades']} trades · "
                         f"{bt['win_rate']}% WR · alpha {bt['alpha_vs_bh']:+.1f}% vs buy-hold",
                         expanded=(len(symbols)==1)):
            st.markdown(f"""
            <div style='font-family:{MONO};font-size:11px;color:{IVORY};margin-bottom:8px;'>
              {bt["strat_verdict"]} &nbsp;·&nbsp;
              Strategy <b style='color:{GREEN if bt["total_ret"]>=0 else RED};'>{bt["total_ret"]:+.1f}%</b>
              vs Buy&Hold <b>{bt["buy_hold_ret"]:+.1f}%</b>
              → Alpha <b style='color:{a_c};'>{bt["alpha_vs_bh"]:+.1f}%</b>
            </div>""", unsafe_allow_html=True)
            st.plotly_chart(chart_backtest_price(df, bt), use_container_width=True)
            st.markdown(_sec("EVERY BUY / SELL SIGNAL", TEAL), unsafe_allow_html=True)
            sig = bt["signals"].copy()
            sig["date"] = pd.to_datetime(sig["date"]).dt.strftime("%Y-%m-%d")
            sig.columns = ["Date","Signal","Price","Detail"]
            st.dataframe(sig, use_container_width=True, hide_index=True)
            st.markdown(_sec("COMPLETED TRADES", TEAL), unsafe_allow_html=True)
            st.dataframe(bt["trades"], use_container_width=True, hide_index=True)

    if summary:
        st.markdown(_sec("PORTFOLIO SUMMARY · ALL SYMBOLS"), unsafe_allow_html=True)
        sdf = pd.DataFrame(summary)
        st.dataframe(sdf, use_container_width=True, hide_index=True)
        avg_alpha = sdf["Alpha %"].mean()
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
        padding:12px 18px;font-family:{MONO};font-size:12px;color:{IVORY};'>
          Average alpha across {len(sdf)} symbols:
          <b style='color:{GREEN if avg_alpha>=0 else RED};'>{avg_alpha:+.2f}%</b>
          &nbsp;·&nbsp; {'✅ Strategy adds value over buy-and-hold' if avg_alpha>0
          else '❌ Buy-and-hold beat the strategy in this period'}
        </div>""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ════════════════════════════════════════════════════════════════════════════

def render_quant_analysis():
    try:    api_key = st.secrets.get("GEMINI_KEY","")
    except: api_key = ""

    st.markdown(f"""
    <style>
    [data-testid="metric-container"] {{
      background:linear-gradient(135deg,{PANEL3},{PANEL2}) !important;
      border:1px solid {BORDER2} !important;
      border-top:2px solid {BORDER2} !important;
      border-radius:10px !important;
      padding:14px 16px !important;
    }}
    [data-testid="stMetricLabel"] p {{
      font-family:{MONO} !important; font-size:10px !important;
      color:{MUTE} !important; letter-spacing:1.5px !important;
    }}
    [data-testid="stMetricValue"] {{
      font-family:{MONO} !important; font-size:20px !important;
      color:{IVORY} !important;
    }}
    .stTabs [data-baseweb="tab-list"] {{
      gap:4px; background:{PANEL2};
      border:1px solid {BORDER};
      border-radius:10px; padding:4px;
    }}
    .stTabs [data-baseweb="tab"] {{
      font-family:{MONO}; font-size:10px; letter-spacing:1px;
      color:{MUTE}; border-radius:7px; padding:7px 14px;
    }}
    .stTabs [aria-selected="true"] {{
      background:{PANEL3} !important; color:{ACCENT} !important;
    }}
    </style>""", unsafe_allow_html=True)

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{PANEL2},{PANEL});
    border:1px solid {BORDER2};border-radius:10px;
    padding:14px 22px;margin-bottom:14px;
    display:flex;justify-content:space-between;align-items:center;'>
      <div>
        <div style='font-family:{MONO};font-size:14px;font-weight:700;
          color:{ACCENT};letter-spacing:2.5px;'>
          ARKA · INSTITUTIONAL QUANT TERMINAL</div>
        <div style='font-family:{MONO};font-size:9px;color:{MUTE2};
          margin-top:3px;letter-spacing:1px;'>
          GARCH · VPIN · ML · SCREENER · ALPHA · WALK-FORWARD · BIAS-FREE</div>
      </div>
      <div style='text-align:right;'>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};'>{ts}</div>
        <div style='font-family:{MONO};font-size:10px;color:{"#1FB97A" if api_key else MUTE};
          margin-top:2px;'>{"🤖 AI ACTIVE" if api_key else "📐 RULE-BASED"} · NSE INDIA</div>
      </div>
    </div>""", unsafe_allow_html=True)

    # ── MODE SWITCH ──────────────────────────────────────────────────────────
    mode = st.radio("mode", ["🖥 MODE 1 · TERMINAL",
                             "🎯 MODE 2 · BACKTESTER",
                             "🧠 MODE 3 · ALPHA SCANNER",
                             "🤖 MODE 4 · ML LAB",
                             "📡 MODE 5 · LIVE SCREENER"],
                    horizontal=True, label_visibility="collapsed")
    if "MODE 2" in mode: render_mode2(); return
    if "MODE 3" in mode: render_mode3(); return
    if "MODE 4" in mode: render_ml_lab(); return
    if "MODE 5" in mode: render_screener(); return

    c1, c2, c3 = st.columns([3,1,1])
    symbol = c1.text_input("", value="RELIANCE",
                            placeholder="NSE symbol — e.g. RELIANCE, TCS, HDFCBANK, ^NSEI",
                            label_visibility="collapsed")
    period = c2.selectbox("", ["1y","2y","3y","5y"], label_visibility="collapsed")
    run    = c3.button("🔍 ANALYSE", type="primary", use_container_width=True)

    if not run:
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:12px;
        padding:36px 40px;text-align:center;margin:24px 0;'>
          <div style='font-family:{MONO};font-size:13px;color:{ACCENT};
            letter-spacing:2px;margin-bottom:16px;'>WHAT THIS TERMINAL SHOWS YOU</div>
          <div style='display:grid;grid-template-columns:repeat(2,1fr);
            gap:14px;max-width:640px;margin:0 auto;text-align:left;'>
            {''.join([f"<div style='background:{PANEL3};border:1px solid {BORDER};border-radius:8px;padding:12px 16px;'><div style='font-family:{MONO};font-size:10px;color:{ACCENT};margin-bottom:4px;letter-spacing:1px;'>{t}</div><div style='font-size:12px;color:{MUTE};line-height:1.6;'>{d}</div></div>" for t,d in [
              ("📦 INSTITUTIONAL FOOTPRINT","Where big players entered using 3×+ volume surges"),
              ("⚡ VPIN TOXICITY","Informed-trader signal — big move detector"),
              ("🗺️ VOLUME PROFILE","POC + Value Area = real support & resistance"),
              ("📉 GARCH VOLATILITY","Regime detection + 10-day forward vol forecast"),
              ("🎲 MONTE CARLO RISK","1000 fat-tail paths, VaR & Expected Shortfall in ₹"),
              ("🤖 ML LAB (MODE 4)","Gradient boosting 5-day forecast, purged CV, honest IC"),
              ("📡 LIVE SCREENER (MODE 5)","50-stock scan — which names have a BUY signal today"),
              ("⚙️ BIAS-FREE BACKTEST","Next-open entries, intrabar stops, walk-forward, alpha"),
            ]])}
          </div>
        </div>""", unsafe_allow_html=True)
        return

    if not symbol.strip():
        st.error("Enter a symbol."); return

    with st.spinner(f"Loading {symbol.upper()}..."):
        df = fetch_stock(symbol, period)

    if df.empty or len(df)<60:
        st.error(f"No data for '{symbol}'. Try: RELIANCE, TCS, INFY, ^NSEI, ^NSEBANK"); return

    price = float(df["Close"].iloc[-1])
    prev  = float(df["Close"].iloc[-2])
    chg   = (price-prev)/prev*100
    chg_c = GREEN if chg>=0 else RED

    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{PANEL3},{PANEL2});
    border:1px solid {BORDER2};border-radius:10px;
    padding:16px 22px;margin-bottom:12px;
    display:flex;align-items:center;gap:24px;'>
      <div style='font-family:{MONO};font-size:30px;font-weight:800;color:{IVORY};'>
        {symbol.upper()}</div>
      <div style='font-family:{MONO};font-size:30px;font-weight:800;color:{IVORY};'>
        ₹{price:,.2f}</div>
      <div style='font-family:{MONO};font-size:18px;font-weight:700;color:{chg_c};'>
        {chg:+.2f}%</div>
      <div style='margin-left:auto;text-align:right;'>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};'>
          {len(df)} bars</div>
        <div style='font-family:{MONO};font-size:10px;color:{MUTE};'>
          {str(df.index[0])[:10]} → {str(df.index[-1])[:10]}</div>
      </div>
    </div>""", unsafe_allow_html=True)

    with st.spinner("Running all engines..."):
        garch_r   = garch_analysis(df["Close"])
        vpin_d    = compute_vpin(df)
        vp        = volume_profile(df)
        fp        = institutional_footprint(df)
        vwap_df   = compute_vwap(df)
        log_rets  = np.log(df["Close"]/df["Close"].shift(1)).dropna()
        mc        = monte_carlo_risk(price, garch_r["daily_vol_pct"]/100,
                                     mu=float(log_rets.mean()),
                                     horizon=10, n_sims=1000, capital=100_000)
        curr_vwap = float(vwap_df["vwap"].iloc[-1]) if "vwap" in vwap_df.columns else price
        scores    = compute_scores(
            vpin_d["percentile"], garch_r["percentile"], fp["bias_score"],
            fp["n_events"], price, vp["poc"], vp["va_low"], vp["va_high"],
            curr_vwap, mc["prob_up_pct"], mc["var95_pct"])

    note = ""
    if api_key:
        with st.spinner("🤖 AI insight..."):
            note = _ai_note(symbol, price, vpin_d, garch_r, fp, vp, api_key)
    if not note:
        note = _rule_note(symbol, price, vpin_d, garch_r, fp, vp)

    fp_v   = fp["verdict"]
    fp_cfg = VERDICT_CFG.get(fp_v, VERDICT_CFG["NEUTRAL"])
    fp_c   = fp_cfg["c"]
    total  = scores["total"]

    big_svg   = _circle_svg(total, "OVERALL", size=130, font_score=26)
    small_svgs = [
        _circle_svg(scores["footprint"],   "FOOTPRINT",   82, 17),
        _circle_svg(scores["vpin"],        "VPIN",        82, 17),
        _circle_svg(scores["garch"],       "GARCH",       82, 17),
        _circle_svg(scores["monte_carlo"], "MONTE CARLO", 82, 17)]
    small_row = "".join([f"<div style='flex:1;'>{s}</div>" for s in small_svgs])

    tox_c = RED if "HIGH" in vpin_d["toxicity"] else AMBER if "MOD" in vpin_d["toxicity"] else GREEN
    vol_c = RED if "HIGH" in garch_r["regime"] else BLUE if "LOW" in garch_r["regime"] else MUTE

    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{fp_cfg["bg"].replace("0.10","0.08")},{PANEL2});
    border:1px solid {fp_c}35;border-left:4px solid {fp_c};
    border-radius:12px;padding:22px 26px;margin:10px 0;
    box-shadow:0 4px 24px rgba(0,0,0,0.4);'>
      <div style='display:flex;justify-content:space-between;align-items:flex-start;
        gap:20px;flex-wrap:wrap;'>
        <div style='flex:2;min-width:220px;'>
          <div style='font-family:{MONO};font-size:9px;color:{MUTE2};
            letter-spacing:2px;margin-bottom:6px;'>
            INSTITUTIONAL SIGNAL · {symbol.upper()}</div>
          <div style='font-family:{MONO};font-size:30px;font-weight:800;
            color:{fp_c};line-height:1;margin-bottom:8px;'>
            {fp_cfg["ic"]} {fp_v}</div>
          <div style='font-family:{MONO};font-size:11px;color:{IVORY};
            margin-bottom:12px;'>
            Bias {fp["bias_score"]:+d}/3 &nbsp;·&nbsp;
            <span style='color:{tox_c};'>VPIN: {vpin_d["toxicity"]}</span> &nbsp;·&nbsp;
            <span style='color:{vol_c};'>Vol: {garch_r["regime"]}</span>
          </div>
          <div style='font-size:12.5px;color:{IVORY};line-height:1.8;
            border-top:1px solid {BORDER};padding-top:12px;'>{note}</div>
        </div>
        <div style='flex:1;min-width:200px;'>
          <div style='display:flex;justify-content:center;margin-bottom:12px;'>
            {big_svg}
          </div>
          <div style='display:flex;gap:4px;justify-content:center;'>{small_row}</div>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

    t0, t1, t2, t3, t4 = st.tabs([
        "📦 FOOTPRINT & VWAP", "⚡ VPIN & SURGE", "📉 GARCH VOLATILITY",
        "🎲 MONTE CARLO", "⚙️ SWING BACKTEST"])

    with t0:
        st.plotly_chart(chart_price_vwap(df, vwap_df, vp, fp, symbol.upper()),
                        use_container_width=True)
        col1, col2 = st.columns([1,1])
        with col1:
            st.markdown(_sec("VOLUME PROFILE"), unsafe_allow_html=True)
            st.plotly_chart(chart_volume_profile(vp), use_container_width=True)
        with col2:
            st.markdown(_sec("KEY LEVELS & READING"), unsafe_allow_html=True)
            kpi_html = "".join([
                _premium_kpi("POINT OF CONTROL", f"₹{vp['poc']:,.0f}", "Highest volume level", PURPLE, "🎯"),
                "<div style='height:8px;'></div>",
                _premium_kpi("VALUE AREA LOW",   f"₹{vp['va_low']:,.0f}", "Lower 70% vol boundary", BLUE, "🔽"),
                "<div style='height:8px;'></div>",
                _premium_kpi("VALUE AREA HIGH",  f"₹{vp['va_high']:,.0f}", "Upper 70% vol boundary", BLUE, "🔼"),
                "<div style='height:8px;'></div>",
                _premium_kpi("VWAP (20d)",        f"₹{curr_vwap:,.0f}", "Institutional benchmark", ACCENT, "📈")])
            st.markdown(kpi_html, unsafe_allow_html=True)
            pos_poc  = (price - vp["poc"])/vp["poc"]*100
            pos_vwap = (price - curr_vwap)/curr_vwap*100 if curr_vwap else 0
            body = (
                f"Price is <b>{'above' if pos_poc>=0 else 'below'} POC by {abs(pos_poc):.1f}%</b>. "
                f"{'POC acting as support — bullish structure.' if pos_poc>=0 else 'POC is overhead resistance — caution.'} "
                f"Price is <b>{'above' if pos_vwap>=0 else 'below'} VWAP by {abs(pos_vwap):.1f}%</b>. "
                f"{'Institutions in profit — trend intact.' if pos_vwap>=0 else 'Below VWAP — potential institutional selling pressure.'}")
            _signal_card("VOLUME PROFILE SIGNAL",
                         _score_to_verdict(2 if price>=vp["poc"] else -2, 3),
                         body, 2 if price>=vp["poc"] else -2,
                         section_score=scores["footprint"])
        if fp["n_events"]>0 and not fp["recent"].empty:
            st.markdown(_sec("RECENT INSTITUTIONAL EVENTS"), unsafe_allow_html=True)
            disp = fp["recent"][["date","type","price","vol_ratio","strength"]].copy()
            disp["date"]      = pd.to_datetime(disp["date"]).dt.strftime("%Y-%m-%d")
            disp["vol_ratio"] = disp["vol_ratio"].apply(lambda x: f"{x:.1f}× avg")
            disp["strength"]  = disp["strength"].apply(lambda x: "★"*x)
            disp.columns      = ["Date","Event","Price","Volume","Strength"]
            st.dataframe(disp, use_container_width=True, hide_index=True)
        bs = fp["bias_score"]
        _signal_card("INSTITUTIONAL FOOTPRINT", fp["verdict"],
                     f"{fp['n_events']} volume surge events detected. "
                     f"Last 3 trades bias: <b>{bs:+d}/3</b>. "
                     f"Current vol ratio: <b>{fp['last_ratio']:.1f}× average.</b>",
                     abs(bs), section_score=scores["footprint"])

    with t1:
        kpi_vals = [("CURRENT VPIN", f"{vpin_d['current']:.4f}"),
                    ("AVERAGE",      f"{vpin_d['avg']:.4f}"),
                    ("PERCENTILE",   f"{vpin_d['percentile']:.0f}th"),
                    ("TOXICITY",     vpin_d['toxicity'])]
        for col, (lbl, val) in zip(st.columns(4), kpi_vals):
            col.metric(lbl, val)
        st.plotly_chart(chart_vpin(vpin_d), use_container_width=True)
        pct = vpin_d["percentile"]
        _signal_card("VPIN SIGNAL",
                     _score_to_verdict(3 if pct>70 else 0 if pct>40 else -1, 3),
                     vpin_d["meaning"],
                     3 if pct>70 else 0 if pct>40 else -1,
                     section_score=scores["vpin"])
        st.markdown(_sec("VOLUME SURGE HISTORY"), unsafe_allow_html=True)
        st.plotly_chart(chart_surge_history(fp), use_container_width=True)

    with t2:
        _mrow([("DAILY VOL",   f"{garch_r['daily_vol_pct']:.2f}%"),
               ("ANNUAL VOL",  f"{garch_r['annual_vol_pct']:.1f}%"),
               ("HIST AVG",    f"{garch_r['hist_avg_annual']:.1f}%"),
               ("PERCENTILE",  f"{garch_r['percentile']:.0f}th"),
               ("REGIME",      garch_r["regime"]),
               ("PERSISTENCE", f"{garch_r['persistence']:.3f}")])
        st.plotly_chart(chart_garch(garch_r, df["Close"]), use_container_width=True)
        st.markdown(_sec("10-DAY FORWARD VOLATILITY FORECAST"), unsafe_allow_html=True)
        fwd = garch_r["forecast_daily"]
        fdf = pd.DataFrame({"Day": range(1, len(fwd)+1),
                            "Daily Vol %":[round(v,3) for v in fwd],
                            "Annual %":   [round(v*np.sqrt(252),1) for v in fwd],
                            "1σ Move ₹":  [round(price*v/100, 1) for v in fwd]})
        st.dataframe(fdf.set_index("Day"), use_container_width=True)
        gp = garch_r["percentile"]
        _signal_card("GARCH SIGNAL",
                     "WEAK SELL" if "HIGH" in garch_r["regime"] else
                     "WEAK BUY"  if "LOW"  in garch_r["regime"] else "NEUTRAL",
                     (f"Vol at <b>{gp:.0f}th percentile</b>. "
                      f"Persistence α+β=<b>{garch_r['persistence']:.3f}</b>. "
                      f"{'⚠️ Reduce size — elevated vol.' if gp>75 else '✅ Compressed vol — breakout setup.' if gp<25 else '📊 Normal vol.'}"),
                     -2 if gp>75 else 2 if gp<25 else 0,
                     section_score=scores["garch"])

    with t3:
        cap_col, _, __ = st.columns([1,1,2])
        capital = cap_col.number_input("Position Size (₹)", value=100_000,
                                       step=10_000, min_value=10_000)
        if capital != 100_000:
            mc = monte_carlo_risk(price, garch_r["daily_vol_pct"]/100,
                                  mu=float(log_rets.mean()),
                                  horizon=10, n_sims=1000, capital=capital)
        st.plotly_chart(chart_monte_carlo(mc, price, symbol.upper()), use_container_width=True)
        st.markdown(_sec("RISK METRICS"), unsafe_allow_html=True)
        _mrow([("PROB OF PROFIT",f"{mc['prob_up_pct']:.1f}%"),
               ("MEDIAN EXIT",   f"₹{mc['p50']:,.0f}"),
               ("P90 UPSIDE",    f"₹{mc['p90']:,.0f}"),
               ("P10 DOWNSIDE",  f"₹{mc['p10']:,.0f}")])
        _mrow([("VaR 95% (₹)",   f"₹{mc['var95_rs']:,.0f}"),
               ("ES 95% (₹)",    f"₹{mc['es95_rs']:,.0f}"),
               ("VaR 99% (₹)",   f"₹{mc['var99_rs']:,.0f}"),
               ("ES 99% (₹)",    f"₹{mc['es99_rs']:,.0f}")])
        _signal_card("MONTE CARLO SIGNAL",
                     "BUY"       if mc["prob_up_pct"]>58 and mc["var95_pct"]>-6 else
                     "WEAK BUY"  if mc["prob_up_pct"]>52 else
                     "NEUTRAL"   if mc["prob_up_pct"]>48 else
                     "WEAK SELL" if mc["prob_up_pct"]>42 else "SELL",
                     (f"Probability of profit: <b>{mc['prob_up_pct']:.1f}%</b>. "
                      f"VaR 95% = <b>{mc['var95_pct']:.1f}%</b>. "
                      f"Expected shortfall if wrong: <b>₹{abs(mc['es95_rs']):,.0f}</b>."),
                     2 if mc["prob_up_pct"]>58 else 1 if mc["prob_up_pct"]>52 else
                     -1 if mc["prob_up_pct"]<48 else 0,
                     section_score=scores["monte_carlo"])

    with t4:
        with st.form("swing_form"):
            st.markdown(_sec("BACKTEST PARAMETERS"), unsafe_allow_html=True)
            r1c1, r1c2, r1c3, r1c4 = st.columns(4)
            start_d  = r1c1.date_input("Start Date", value=datetime.date(2020,1,1))
            end_d    = r1c2.date_input("End Date",   value=datetime.date.today())
            vol_mult = r1c3.slider("Vol Surge ×", 1.5, 5.0, 2.5, 0.5)
            hold_d   = r1c4.slider("Max Hold Days", 3, 21, 7)
            r2c1, r2c2, r2c3, r2c4 = st.columns(4)
            stop_p   = r2c1.slider("Stop Loss %",  1, 10, 4)
            tgt_p    = r2c2.slider("Target %",     2, 20, 8)
            pos_p    = r2c3.slider("Position %",   5, 40, 20)
            comm_bps = r2c4.number_input("Commission (bps)", value=15.0, step=1.0)
            use_regime = st.checkbox("🛡 NIFTY 200-DMA Regime Filter", value=True)
            run_bt   = st.form_submit_button("▶ RUN BACKTEST", type="primary",
                                             use_container_width=True)
        if not run_bt:
            st.info("Set parameters above and press RUN BACKTEST.")
        else:
            if start_d >= end_d:
                st.error("Start must be before End."); st.stop()
            with st.spinner("Fetching data and running backtest..."):
                df_bt = fetch_range(symbol, str(start_d), str(end_d))
                if df_bt.empty:
                    st.error(f"No data for {symbol.upper()} in that range."); st.stop()
                regime = fetch_regime() if use_regime else None
                bt = swing_backtest(df_bt, vol_mult=vol_mult, hold_days=hold_d,
                                    stop_pct=stop_p/100, target_pct=tgt_p/100,
                                    pos_pct=pos_p/100, commission=comm_bps/10_000,
                                    regime=regime)
            if "error" in bt:
                st.error(bt["error"]); st.stop()
            sv   = bt["strat_verdict"]
            sv_c = GREEN if "✅" in sv else AMBER if "⚠️" in sv else RED
            a_c  = GREEN if bt["alpha_vs_bh"] >= 0 else RED
            st.markdown(f"""
            <div style='border:1px solid {sv_c}40;border-left:5px solid {sv_c};
            border-radius:10px;padding:18px 24px;margin:12px 0;background:{PANEL2};'>
              <div style='font-family:{MONO};font-size:22px;font-weight:800;
                color:{sv_c};margin-bottom:6px;'>{sv}</div>
              <div style='font-family:{MONO};font-size:11px;color:{IVORY};'>
                {bt["total_trades"]} trades · WR <b>{bt["win_rate"]}%</b> ·
                PF <b>{bt["profit_factor"]}</b> · Sharpe <b>{bt["sharpe"]}</b> ·
                Return <b>{bt["total_ret"]:+.2f}%</b> ·
                Buy&Hold <b>{bt["buy_hold_ret"]:+.2f}%</b> ·
                Alpha <b style='color:{a_c};'>{bt["alpha_vs_bh"]:+.2f}%</b> ·
                Max DD <b style='color:{RED};'>{bt["max_dd"]:.1f}%</b>
              </div>
            </div>""", unsafe_allow_html=True)
            st.markdown(_sec("PERFORMANCE METRICS"), unsafe_allow_html=True)
            _mrow([("TRADES", str(bt["total_trades"])),("WIN RATE",f"{bt['win_rate']}%"),
                   ("AVG WIN",f"{bt['avg_win']:+.2f}%"),("AVG LOSS",f"{bt['avg_loss']:+.2f}%")])
            _mrow([("R:R RATIO",f"{bt['rr']:.2f}"),("PROFIT FACTOR",f"{bt['profit_factor']:.2f}"),
                   ("SHARPE",f"{bt['sharpe']:.2f}"),("SORTINO",f"{bt['sortino']:.2f}")])
            _mrow([("TOTAL RETURN",f"{bt['total_ret']:+.2f}%"),("BUY & HOLD",f"{bt['buy_hold_ret']:+.2f}%"),
                   ("ALPHA",f"{bt['alpha_vs_bh']:+.2f}%"),("MAX DRAWDOWN",f"{bt['max_dd']:.1f}%")])
            st.plotly_chart(chart_backtest_price(df_bt, bt), use_container_width=True)
            st.markdown(_sec("SIGNAL LOG · EVERY BUY / SELL"), unsafe_allow_html=True)
            sig = bt["signals"].copy()
            sig["date"] = pd.to_datetime(sig["date"]).dt.strftime("%Y-%m-%d")
            sig.columns = ["Date","Signal","Price","Detail"]
            st.dataframe(sig, use_container_width=True, hide_index=True)
            st.markdown(_sec("TRADE LOG"), unsafe_allow_html=True)
            st.dataframe(bt["trades"], use_container_width=True, hide_index=True)
            r_left, r_right = st.columns(2)
            with r_left:
                st.plotly_chart(chart_equity(bt), use_container_width=True)
                st.plotly_chart(chart_monthly_pnl(bt), use_container_width=True)
            with r_right:
                st.plotly_chart(chart_drawdown(bt), use_container_width=True)
                st.plotly_chart(chart_exit_breakdown(bt), use_container_width=True)
            st.markdown(_sec("WALK-FORWARD VALIDATION · OVERFIT TEST"), unsafe_allow_html=True)
            with st.spinner("Running walk-forward folds..."):
                wf = walk_forward(df_bt, hold_days=hold_d, stop_pct=stop_p/100,
                                  target_pct=tgt_p/100, pos_pct=pos_p/100,
                                  commission=comm_bps/10_000)
            if "error" in wf:
                st.warning(wf["error"])
            else:
                wf_c = GREEN if "✅" in wf["verdict"] else AMBER if "⚠️" in wf["verdict"] else RED
                st.markdown(f"""
                <div style='background:{PANEL2};border:1px solid {wf_c}40;
                border-left:4px solid {wf_c};border-radius:10px;padding:14px 20px;
                margin-bottom:8px;font-family:{MONO};font-size:13px;font-weight:700;
                color:{wf_c};'>{wf["verdict"]} · Avg OOS Profit Factor: {wf["avg_oos_pf"]}</div>""",
                unsafe_allow_html=True)
                st.dataframe(wf["table"], use_container_width=True, hide_index=True)


render_quant_options_page = render_quant_analysis

if __name__ == "__main__":
    st.set_page_config(page_title="ARKA · Quant Terminal",
                       layout="wide", initial_sidebar_state="collapsed")
    render_quant_analysis()
