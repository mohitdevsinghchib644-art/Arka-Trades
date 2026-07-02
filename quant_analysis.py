"""
quant_analysis.py — Arka Trades | Quant Decision Terminal v6
============================================================
app.py: from quant_analysis import render_quant_analysis
Requires: streamlit yfinance pandas numpy scipy plotly scikit-learn

ONE DECISION ENGINE. THREE VIEWS.
MODE 1 · TERMINAL     stock -> engines + 3-model ML ensemble ->
                      BUY / SELL / NO TRADE + ATR stop & target +
                      Kelly position size + max safe order size (impact model)
MODE 2 · BACKTESTER   symbol + dates only. Replays the EXACT Mode 1
                      engine on history. No knobs.
MODE 3 · STRESS TEST  Aladdin-style crash replay (2008/2013/2016/2020/2022)
                      + global sentiment reader (what moves India today)
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
AMBER  = "#F59E0B"
TEAL   = "#14B8A6"
MONO   = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"
TRADING_DAYS = 252

# ── Engine constants (fixed — the strategy IS these numbers) ────────────────
BUY_THRESHOLD   = 35      # composite score >= +35 -> BUY
SELL_THRESHOLD  = -35     # composite score <= -35 -> SELL/AVOID
STOP_ATR_MULT   = 2.0     # stop  = entry - 2.0 * ATR
TARGET_ATR_MULT = 3.0     # target= entry + 3.0 * ATR  (1.5 R:R)
MAX_HOLD_BARS   = 15      # time exit
KELLY_CAP       = 0.20    # never size beyond 20% even if Kelly says more


# ════════════════════════════════════════════════════════════════════════════
# UI PRIMITIVES
# ════════════════════════════════════════════════════════════════════════════

def _score_rating(score: int) -> tuple[str, str]:
    if score >= 80: return GREEN, "STRONG"
    if score >= 65: return ACCENT, "GOOD"
    if score >= 45: return "#E67E22", "FAIR"
    return RED, "WEAK"


def _circle_svg(score: int, label: str = "SCORE",
                size: int = 120, font_score: int = 22) -> str:
    color, rating = _score_rating(score)
    r     = size // 2 - 10
    circ  = 2 * 3.14159 * r
    filled= circ * min(abs(score),100) / 100
    gap   = circ - filled
    uid   = label.replace(" ", "_")
    return f"""
<div style="display:flex;flex-direction:column;align-items:center;gap:3px;">
<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <circle cx="{size//2}" cy="{size//2}" r="{r}"
    fill="none" stroke="{BORDER}" stroke-width="8"/>
  <circle cx="{size//2}" cy="{size//2}" r="{r}"
    fill="none" stroke="{color}" stroke-width="8"
    stroke-dasharray="{filled:.1f} {gap:.1f}"
    stroke-dashoffset="{circ*0.25:.1f}" stroke-linecap="round"/>
  <text x="{size//2}" y="{size//2-3}" text-anchor="middle"
    font-family="monospace" font-size="{font_score}" font-weight="800"
    fill="{color}">{score}</text>
  <text x="{size//2}" y="{size//2+14}" text-anchor="middle"
    font-family="monospace" font-size="8" fill="{MUTE2}">/100</text>
</svg>
<div style="font-family:{MONO};font-size:8px;color:{MUTE2};
  letter-spacing:1.5px;text-align:center;">{label}</div>
</div>"""


def _layout(fig: go.Figure, title: str, height: int = 400) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL2,
        title=dict(text=title, font=dict(size=12, color=ACCENT, family="monospace")),
        height=height, margin=dict(l=8, r=8, t=42, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER, zeroline=False),
        yaxis=dict(gridcolor=BORDER, zeroline=False))
    return fig


def _sec(label: str, accent: str = ACCENT) -> str:
    return (f"<div style='display:flex;align-items:center;gap:10px;margin:18px 0 8px;'>"
            f"<div style='width:3px;height:16px;border-radius:2px;"
            f"background:{accent};'></div>"
            f"<div style='font-family:{MONO};font-size:11px;font-weight:700;"
            f"color:{accent};letter-spacing:1.8px;'>{label}</div>"
            f"<div style='flex:1;height:1px;background:{BORDER};'></div></div>")


def _mrow(cells: list):
    cols = st.columns(len(cells))
    for col, (lbl, val) in zip(cols, cells):
        col.metric(lbl, val)


def _premium_kpi(label: str, value: str, sub: str, color: str) -> str:
    return (f"<div style='background:linear-gradient(135deg,{PANEL3},{PANEL2});"
            f"border:1px solid {color}28;border-top:2px solid {color};"
            f"border-radius:10px;padding:14px 16px;'>"
            f"<div style='font-family:{MONO};font-size:9px;font-weight:700;"
            f"color:{MUTE};letter-spacing:1.8px;margin-bottom:6px;'>{label}</div>"
            f"<div style='font-family:{MONO};font-size:20px;font-weight:800;"
            f"color:{color};line-height:1;margin-bottom:3px;'>{value}</div>"
            f"<div style='font-size:10px;color:{MUTE2};'>{sub}</div></div>")


# ════════════════════════════════════════════════════════════════════════════
# DATA FETCH
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock(symbol: str, period: str = "2y") -> pd.DataFrame:
    import yfinance as yf
    sym = symbol.strip().upper()
    if "^" not in sym and not sym.endswith(".NS"):
        sym += ".NS"
    for p in [period, "3y", "max"]:
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
    """NIFTY above 200-DMA = risk-on. Below = block new longs."""
    import yfinance as yf
    try:
        idx = yf.Ticker("^NSEI").history(period="max", interval="1d")["Close"]
        idx.index = pd.to_datetime(idx.index).tz_localize(None)
        return (idx > idx.rolling(200).mean()).rename("risk_on")
    except Exception:
        return pd.Series(dtype=bool)


# ════════════════════════════════════════════════════════════════════════════
# GLOBAL SENTIMENT READER — what moves the Indian market today
# ════════════════════════════════════════════════════════════════════════════

GLOBAL_MARKETS = [
    # (name, ticker, direction for India: +1 good when up / -1 bad when up, why)
    ("S&P 500",     "^GSPC",    +1, "US risk appetite drives FII flows into India"),
    ("NASDAQ",      "^IXIC",    +1, "US tech sentiment → Indian IT (TCS, INFY, WIPRO)"),
    ("DOW JONES",   "^DJI",     +1, "Global growth sentiment"),
    ("VIX (FEAR)",  "^VIX",     -1, "VIX spike = FIIs sell emerging markets first"),
    ("CRUDE OIL",   "CL=F",     -1, "India imports ~85% of oil — up = inflation + CAD pressure"),
    ("USD/INR",     "USDINR=X", -1, "Weak rupee = FII outflows + imported inflation"),
    ("US 10Y YIELD","^TNX",     -1, "High US yields pull FII money out of India"),
    ("GOLD",        "GC=F",     -1, "Safe-haven bid = global risk-off"),
    ("NIKKEI 225",  "^N225",    +1, "Asia session risk sentiment"),
    ("HANG SENG",   "^HSI",     +1, "China/EM flow proxy — India competes for same FII money"),
]

@st.cache_data(ttl=900, show_spinner=False)
def fetch_global_sentiment() -> dict:
    import yfinance as yf
    rows, impacts = [], []
    for name, tkr, direction, why in GLOBAL_MARKETS:
        try:
            h = yf.Ticker(tkr).history(period="1mo")["Close"].dropna()
            if len(h) < 6: continue
            last  = float(h.iloc[-1])
            chg1  = float(h.iloc[-1]/h.iloc[-2]-1)*100
            chg5  = float(h.iloc[-1]/h.iloc[-6]-1)*100
            impact = float(np.clip(chg5*direction/2.0, -1, 1))   # -1..+1
            impacts.append(impact)
            rows.append({"Market": name, "Last": round(last,2),
                         "1D %": round(chg1,2), "5D %": round(chg5,2),
                         "India Impact": ("🟢 POSITIVE" if impact>0.15 else
                                          "🔴 NEGATIVE" if impact<-0.15 else "⚪ NEUTRAL"),
                         "Why It Matters": why})
        except Exception:
            continue
    if not rows:
        return {"error": "Could not fetch global data."}
    score = int(round(float(np.mean(impacts))*100))
    verdict = ("🟢 GLOBAL RISK-ON — tailwind for Indian equities" if score >= 20 else
               "🔴 GLOBAL RISK-OFF — headwind, FII selling likely" if score <= -20 else
               "⚪ GLOBAL NEUTRAL — local factors dominate")
    return {"table": pd.DataFrame(rows), "score": score, "verdict": verdict}


# ════════════════════════════════════════════════════════════════════════════
# INDICATORS — every column the decision engine scores (vectorized, no lookahead)
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, v = d["Close"], d["Volume"]
    d["ma20"]  = c.rolling(20).mean()
    d["ma50"]  = c.rolling(50).mean()
    d["ma200"] = c.rolling(200, min_periods=120).mean()
    delta = c.diff()
    g = delta.clip(lower=0).rolling(14).mean()
    l = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100 - 100/(1 + g/l.replace(0, np.nan))
    d["vol_ratio"] = v / v.rolling(20).mean()
    tp = (d["High"] + d["Low"] + c) / 3
    d["vwap20"] = (tp*v).rolling(20).sum() / v.rolling(20).sum()
    d["vwap60"] = (tp*v).rolling(60).sum() / v.rolling(60).sum()
    hl = d["High"]-d["Low"]; hc=(d["High"]-c.shift()).abs(); lc=(d["Low"]-c.shift()).abs()
    d["atr"] = pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(14).mean()
    # rolling VPIN proxy (order-flow toxicity)
    r   = np.log(c/c.shift(1))
    sig = r.rolling(60).std().replace(0, np.nan)
    pb  = pd.Series(norm.cdf((r/sig).fillna(0)), index=d.index)
    buy_v, sell_v = v*pb, v*(1-pb)
    d["vpin"] = (buy_v-sell_v).rolling(50).sum().abs() / v.rolling(50).sum()
    d["vpin_pct"] = d["vpin"].rolling(252, min_periods=100).apply(
        lambda x: float((x <= x[-1]).mean()*100), raw=True)
    # volatility regime percentile
    vol21 = r.rolling(21).std()
    d["vol_pct"] = vol21.rolling(252, min_periods=100).apply(
        lambda x: float((x <= x[-1]).mean()*100), raw=True)
    return d.dropna(subset=["ma50","rsi","atr","vwap20","vol_ratio"])


# ════════════════════════════════════════════════════════════════════════════
# ENSEMBLE ML — 3 models voting (GradientBoosting + RandomForest + Ridge)
# ════════════════════════════════════════════════════════════════════════════

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, v = d["Close"], d["Volume"]
    r = np.log(c/c.shift(1))
    f = pd.DataFrame(index=d.index)
    for lb in (1, 2, 3, 5, 10, 21):
        f[f"ret_{lb}d"] = c.pct_change(lb)
    for lb in (5, 10, 21, 63):
        f[f"vol_{lb}d"] = r.rolling(lb).std()
    f["vol_regime"] = f["vol_5d"]/f["vol_63d"]
    for lb in (5, 21, 63):
        f[f"volu_{lb}d"] = v/v.rolling(lb).mean()
    delta = c.diff()
    g = delta.clip(lower=0).rolling(14).mean()
    l = (-delta.clip(upper=0)).rolling(14).mean()
    f["rsi"] = 100-100/(1+g/l.replace(0,np.nan))
    for lb in (20, 50):
        f[f"dist_ma{lb}"] = c/c.rolling(lb).mean()-1
    f["hl_range"] = (d["High"]-d["Low"])/c
    f["gap"]      = d["Open"]/c.shift(1)-1
    f["mom_6m"]   = c.pct_change(126)
    f["hi52"]     = c/c.rolling(252, min_periods=60).max()-1
    f["target"]   = c.shift(-5)/c - 1
    return f


def _make_models():
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge
    return [
        ("GradientBoost", GradientBoostingRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=42)),
        ("RandomForest",  RandomForestRegressor(
            n_estimators=300, max_depth=5, min_samples_leaf=10,
            random_state=42, n_jobs=-1)),
        ("Ridge Linear",  Ridge(alpha=1.0)),
    ]


@st.cache_data(ttl=1800, show_spinner=False)
def ensemble_ml(df: pd.DataFrame, n_splits: int = 4, embargo: int = 5) -> dict:
    """3 models vote. Purged walk-forward CV. Honest IC or it says NO EDGE."""
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return {"error": "Run: pip install scikit-learn"}
    f = build_features(df)
    cols = [c for c in f.columns if c != "target"]
    data = f.dropna()
    if len(data) < 300:
        return {"error": "Need 300+ clean bars — use 2y+ history."}
    X, y = data[cols].values, data["target"].values
    n = len(data); test_size = n // (n_splits + 1)
    oos_p, oos_t = [], []
    for k in range(n_splits):
        t0 = n - (n_splits-k)*test_size
        tr_end = t0 - embargo
        if tr_end < 150: continue
        fold_preds = []
        for _, m in _make_models():
            m.fit(X[:tr_end], y[:tr_end])
            fold_preds.append(m.predict(X[t0:t0+test_size]))
        oos_p += list(np.mean(fold_preds, axis=0))      # ensemble = mean vote
        oos_t += list(y[t0:t0+test_size])
    if len(oos_p) < 50:
        return {"error": "Not enough OOS samples."}
    op, ot = np.array(oos_p), np.array(oos_t)
    ic  = float(spearmanr(op, ot).correlation)
    hit = float(np.mean(np.sign(op) == np.sign(ot))*100)
    # live vote: train all 3 on everything, each votes on latest bar
    live_row = f[cols].dropna().iloc[[-1]].values
    votes, preds = [], []
    for name, m in _make_models():
        m.fit(X, y)
        p = float(m.predict(live_row)[0])
        preds.append(p)
        votes.append({"Model": name, "5d Forecast %": round(p*100, 2),
                      "Vote": "🟢 UP" if p > 0 else "🔴 DOWN"})
    mean_pred = float(np.mean(preds))*100
    n_up = sum(1 for p in preds if p > 0)
    agreement = ("3/3 UNANIMOUS" if n_up in (0,3) else "2/3 MAJORITY")
    direction = "UP" if n_up >= 2 else "DOWN"
    verdict = ("✅ REAL SIGNAL" if ic >= 0.08 else
               "⚠️ WEAK — filter only" if ic >= 0.03 else
               "❌ NO EDGE on this stock")
    return {"pred_5d_pct": round(mean_pred, 2), "ic": round(ic, 4),
            "hit_rate": round(hit, 1), "votes": pd.DataFrame(votes),
            "n_up": n_up, "agreement": agreement, "direction": direction,
            "verdict": verdict}


@st.cache_data(ttl=1800, show_spinner=False)
def ml_walkforward_series(df: pd.DataFrame) -> pd.Series:
    """Expanding-window retrain every 126 bars -> honest ML column for the
    backtester. Bars before first training window get no ML contribution."""
    try:
        from sklearn.ensemble import GradientBoostingRegressor
    except ImportError:
        return pd.Series(dtype=float)
    f = build_features(df)
    cols = [c for c in f.columns if c != "target"]
    d = f.dropna(subset=cols)
    preds = pd.Series(np.nan, index=df.index)
    if len(d) < 300: return preds
    step, start = 126, 250
    for t0 in range(start, len(d), step):
        train = d.iloc[:t0].dropna(subset=["target"])
        if len(train) < 200: continue
        m = GradientBoostingRegressor(n_estimators=150, max_depth=3,
                                      learning_rate=0.05, subsample=0.8,
                                      random_state=42)
        m.fit(train[cols].values, train["target"].values)
        block = d.iloc[t0:t0+step]
        preds.loc[block.index] = m.predict(block[cols].values)
    return preds


# ════════════════════════════════════════════════════════════════════════════
# THE DECISION ENGINE — one scoring function used by Mode 1 AND Mode 2
# Composite score -100..+100 from 8 components. Fixed weights. No knobs.
# ════════════════════════════════════════════════════════════════════════════

def _score_components(row) -> dict:
    p = {}
    c = float(row["Close"])
    # TREND (max ±20): above/below 50MA and 200MA
    t = 10 if c > float(row["ma50"]) else -10
    if pd.notna(row.get("ma200")):
        t += 10 if c > float(row["ma200"]) else -10
    p["TREND"] = t
    # MOMENTUM (max ±10): RSI positioning
    rsi = float(row["rsi"])
    p["MOMENTUM (RSI)"] = (10 if 55 <= rsi <= 70 else 3 if 45 <= rsi < 55 else
                           -5 if rsi > 70 else -3 if 35 <= rsi < 45 else -10)
    # VOLUME FLOW (max ±15): institutional surge with direction
    vr   = float(row["vol_ratio"])
    bull = c > float(row["Open"])
    p["VOLUME FLOW"] = ((15 if vr >= 2.5 else 8) * (1 if bull else -1)
                        if vr >= 1.5 else 0)
    # VWAP (max ±10): institutional benchmark 20d
    p["VWAP 20D"] = 10 if c > float(row["vwap20"]) else -10
    # COST BASIS (max ±10): 60d institutional cost basis
    p["INST COST BASIS 60D"] = (10 if pd.notna(row.get("vwap60"))
                                and c > float(row["vwap60"]) else -10)
    # VPIN (max ±10): informed flow confirms trend direction
    vp = row.get("vpin_pct")
    p["VPIN ORDER FLOW"] = ((10 if p["TREND"] > 0 else -10)
                            if pd.notna(vp) and float(vp) > 70 else 0)
    # VOL REGIME (max ±10): low vol = good entry, high vol = danger
    vpc = row.get("vol_pct")
    p["VOL REGIME"] = (10 if pd.notna(vpc) and float(vpc) < 25 else
                       -10 if pd.notna(vpc) and float(vpc) > 75 else 0)
    return p


def _ml_points(pred_5d: float | None) -> int:
    """ML ensemble contribution (max ±15). pred_5d is a fraction."""
    if pred_5d is None or pd.isna(pred_5d): return 0
    if pred_5d >=  0.005: return 15
    if pred_5d >   0:     return 7
    if pred_5d <= -0.005: return -15
    return -7


def score_bar(row, ml_pred=None, global_adj: int = 0) -> tuple[int, dict]:
    """Full composite: rules (±85) + ML ensemble (±15) + global (±10), clamped ±100."""
    parts = _score_components(row)
    parts["ML ENSEMBLE"] = _ml_points(ml_pred)
    if global_adj:
        parts["GLOBAL SENTIMENT"] = global_adj
    total = int(np.clip(sum(parts.values()), -100, 100))
    return total, parts

# ══════════════ END OF PART 1 — PASTE PART 2 DIRECTLY BELOW ══════════════
# ════════════════════════════════════════════════════════════════════════════
# ENGINE BACKTEST — replays the EXACT decision engine bar by bar. No knobs.
# Fixed costs: 15bps commission, 5bps slippage. Entry at next open.
# Intrabar ATR stops/targets. Exit when score dies or time stop.
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def engine_backtest(df: pd.DataFrame, use_regime: bool = True,
                    use_ml: bool = True) -> dict:
    if df.empty or len(df) < 120:
        return {"error": "Need 120+ bars. Use a longer date range."}
    ind = compute_indicators(df)
    if len(ind) < 60:
        return {"error": "Not enough clean data after indicators."}
    ml_series = ml_walkforward_series(df) if use_ml else pd.Series(dtype=float)
    regime = fetch_regime() if use_regime else pd.Series(dtype=bool)

    capital0 = 1_000_000
    commission, slippage, pos_pct = 0.0015, 0.0005, 0.20
    equity, trades, curve, signals = capital0, [], [], []
    position, hold = None, 0
    rows = list(ind.iterrows())

    for i, (date, row) in enumerate(rows):
        curve.append({"date": date, "equity": equity})
        ml_p = ml_series.get(date, np.nan) if len(ml_series) else np.nan
        score, _ = score_bar(row, ml_p)

        if position is None:
            risk_on = True
            if len(regime):
                rr_ = regime.reindex([date], method="ffill")
                if not rr_.isna().all(): risk_on = bool(rr_.iloc[0])
            if i < len(rows)-1 and risk_on and score >= BUY_THRESHOLD:
                nd, nrow = rows[i+1]
                ep  = float(nrow["Open"])*(1+slippage)
                atr = float(row["atr"])
                position = {"ep": ep, "shares": (equity*pos_pct)/ep, "date": nd,
                            "signal_date": date, "score": score,
                            "cost_in": equity*pos_pct*commission,
                            "stop": ep - STOP_ATR_MULT*atr,
                            "target": ep + TARGET_ATR_MULT*atr}
                hold = 0
                signals.append({"date": date, "side": "BUY SIGNAL",
                                "price": round(float(row["Close"]),2),
                                "detail": f"engine score {score:+d}"})
        else:
            if date <= position["date"]: continue
            hold += 1
            lo, hi, close = float(row["Low"]), float(row["High"]), float(row["Close"])
            xp, reason = None, None
            if lo <= position["stop"]:
                xp, reason = position["stop"], "ATR Stop 🛑"
            elif hi >= position["target"]:
                xp, reason = position["target"], "ATR Target 🎯"
            elif score <= 0:
                xp, reason = close, "Score Died 📉"
            elif hold >= MAX_HOLD_BARS or i == len(rows)-1:
                xp, reason = close, "Time ⏱"
            if xp is not None:
                xp *= (1-slippage)
                ep  = position["ep"]
                pnl = (position["shares"]*(xp-ep) - position["cost_in"]
                       - position["shares"]*xp*commission)
                equity += pnl
                trades.append({
                    "signal_date": str(position["signal_date"])[:10],
                    "entry_date": str(position["date"])[:10],
                    "exit_date": str(date)[:10],
                    "entry": round(ep,2), "exit": round(xp,2),
                    "stop_lvl": round(position["stop"],2),
                    "target_lvl": round(position["target"],2),
                    "score": position["score"],
                    "pnl": round(pnl,2), "pnl_pct": round((xp/ep-1)*100,2),
                    "hold_days": hold, "exit_reason": reason,
                    "outcome": "WIN" if pnl>0 else "LOSS"})
                signals.append({"date": date, "side": "SELL SIGNAL",
                                "price": round(xp,2), "detail": reason})
                position, hold = None, 0

    if not trades:
        return {"error": "Engine gave no BUY signals in this period "
                "(score never reached +35 in risk-on conditions)."}
    tdf   = pd.DataFrame(trades)
    eq_s  = pd.DataFrame(curve).set_index("date")["equity"]
    rets  = eq_s.pct_change().dropna()
    wins, losses = tdf[tdf.outcome=="WIN"], tdf[tdf.outcome=="LOSS"]
    wr = len(wins)/len(tdf)*100
    pf = (abs(wins.pnl.sum()/losses.pnl.sum())
          if len(losses) and losses.pnl.sum()!=0 else 99.0)
    ann_r = float(rets.mean()*TRADING_DAYS)
    ann_v = float(rets.std()*np.sqrt(TRADING_DAYS))
    dd    = (eq_s-eq_s.cummax())/eq_s.cummax()*100
    tot_r = (equity-capital0)/capital0*100
    bh    = (float(ind["Close"].iloc[-1])/float(ind["Close"].iloc[0])-1)*100
    avg_w = round(wins["pnl_pct"].mean(),2)   if len(wins)   else 0.0
    avg_l = round(losses["pnl_pct"].mean(),2) if len(losses) else 0.0
    rr    = round(abs(avg_w/avg_l),2) if avg_l else TARGET_ATR_MULT/STOP_ATR_MULT
    return {"trades": tdf, "signals": pd.DataFrame(signals),
            "equity_curve": eq_s.to_frame(), "drawdown": dd,
            "total_trades": len(tdf), "win_rate": round(wr,1),
            "profit_factor": round(pf,2),
            "sharpe": round(ann_r/ann_v,3) if ann_v>0 else 0.0,
            "max_dd": round(float(dd.min()),2), "total_ret": round(tot_r,2),
            "buy_hold_ret": round(bh,2), "alpha_vs_bh": round(tot_r-bh,2),
            "avg_win": avg_w, "avg_loss": avg_l, "rr": rr,
            "final_equity": round(equity,2)}


# ════════════════════════════════════════════════════════════════════════════
# KELLY SIZING + JPM-STYLE MARKET IMPACT MODEL
# ════════════════════════════════════════════════════════════════════════════

def kelly_sizing(bt: dict) -> dict:
    """Kelly fraction from THIS stock's engine backtest. Half-Kelly recommended."""
    if "error" in bt or bt["total_trades"] < 5:
        return {"kelly_pct": 0.0, "half_kelly_pct": 5.0,
                "note": "Too few historical trades — default 5% size."}
    w  = bt["win_rate"]/100
    rr = max(bt["rr"], 0.1)
    kelly = w - (1-w)/rr
    kelly = float(np.clip(kelly, 0, KELLY_CAP))
    return {"kelly_pct": round(kelly*100,1),
            "half_kelly_pct": round(kelly*50,1),
            "note": (f"From {bt['total_trades']} engine trades: "
                     f"{bt['win_rate']}% WR, {rr:.2f} R:R. "
                     "Half-Kelly recommended (full Kelly = violent drawdowns).")}


def impact_model(df: pd.DataFrame) -> dict:
    """Square-root market impact: impact = 0.1 * sigma_daily * sqrt(order/ADV).
    Max safe size = larger order moves the price against you > 10bps."""
    c, v = df["Close"], df["Volume"]
    adv_val = float((c*v).rolling(20).mean().iloc[-1])          # ₹ traded/day
    sigma_d = float(np.log(c/c.shift(1)).dropna().tail(60).std())
    max_impact = 0.0010                                          # 10 bps budget
    q_impact = adv_val * (max_impact/(0.1*max(sigma_d,1e-6)))**2
    q_partic = 0.05 * adv_val                                    # 5% ADV cap
    max_safe = min(q_impact, q_partic)
    rows = []
    for size in [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]:
        imp_bps = 10_000 * 0.1 * sigma_d * np.sqrt(size/adv_val)
        rows.append({"Order Size": f"₹{size:,.0f}",
                     "Est. Impact": f"{imp_bps:.1f} bps",
                     "% of Daily Vol": f"{size/adv_val*100:.2f}%",
                     "Safe?": "✅" if size <= max_safe else "⚠️ moves the price"})
    return {"adv_rs": adv_val, "max_safe_rs": max_safe,
            "table": pd.DataFrame(rows)}


# ════════════════════════════════════════════════════════════════════════════
# THE DECISION — Mode 1's single answer: BUY / SELL / NO TRADE + levels
# ════════════════════════════════════════════════════════════════════════════

def make_decision(df: pd.DataFrame, symbol: str) -> dict:
    ind = compute_indicators(df)
    if ind.empty: return {"error": "Not enough data."}
    last  = ind.iloc[-1]
    price = float(last["Close"])
    atr   = float(last["atr"])
    ml = ensemble_ml(df)
    ml_pred = (ml["pred_5d_pct"]/100) if "error" not in ml else None
    gs = fetch_global_sentiment()
    g_adj = int(np.clip(gs["score"]/10, -10, 10)) if "error" not in gs else 0
    score, parts = score_bar(last, ml_pred, g_adj)
    regime = fetch_regime()
    risk_on = bool(regime.iloc[-1]) if len(regime) else True

    if score >= BUY_THRESHOLD and risk_on:
        verdict = "BUY"
        stop, target = price - STOP_ATR_MULT*atr, price + TARGET_ATR_MULT*atr
    elif score >= BUY_THRESHOLD and not risk_on:
        verdict = "NO TRADE"
        stop = target = None
    elif score <= SELL_THRESHOLD:
        verdict = "SELL / AVOID"
        stop, target = price + STOP_ATR_MULT*atr, price - TARGET_ATR_MULT*atr
    else:
        verdict = "NO TRADE"
        stop = target = None

    bt = engine_backtest(df)
    return {"symbol": symbol.upper(), "price": price, "score": score,
            "parts": parts, "verdict": verdict, "atr": atr,
            "stop": round(stop,2) if stop else None,
            "target": round(target,2) if target else None,
            "rr": TARGET_ATR_MULT/STOP_ATR_MULT,
            "ml": ml, "global": gs, "risk_on": risk_on,
            "kelly": kelly_sizing(bt), "impact": impact_model(df),
            "backtest": bt}


# ════════════════════════════════════════════════════════════════════════════
# STRESS TEST — Aladdin-style crash replay on your position
# ════════════════════════════════════════════════════════════════════════════

SCENARIOS = [
    ("2008 GLOBAL FINANCIAL CRISIS", -60, "Lehman collapse — NIFTY fell 60% peak to trough"),
    ("2020 COVID CRASH",             -38, "Feb-Mar 2020 — fastest 38% NIFTY drop in history"),
    ("2022 FED RATE SHOCK",          -18, "Aggressive US hikes — FII exodus from EM"),
    ("2013 TAPER TANTRUM",           -16, "Fed taper talk — rupee crashed, FIIs fled India"),
    ("HYPOTHETICAL: VIX 40 SPIKE",   -15, "Sudden global fear event — EM sell-first"),
    ("2016 DEMONETIZATION",          -12, "Domestic liquidity shock — Nov 2016"),
]

@st.cache_data(ttl=900, show_spinner=False)
def stress_test(symbol: str, capital: float) -> dict:
    import yfinance as yf
    df = fetch_stock(symbol, "2y")
    if df.empty: return {"error": f"No data for {symbol}."}
    try:
        nifty = yf.Ticker("^NSEI").history(period="2y")["Close"]
        nifty.index = pd.to_datetime(nifty.index).tz_localize(None)
    except Exception:
        return {"error": "Could not fetch NIFTY for beta."}
    rs = np.log(df["Close"]/df["Close"].shift(1)).dropna()
    rn = np.log(nifty/nifty.shift(1)).dropna()
    joined = pd.concat([rs, rn], axis=1, join="inner").dropna()
    joined.columns = ["stock","nifty"]
    if len(joined) < 100: return {"error": "Not enough overlapping data."}
    beta = float(joined["stock"].cov(joined["nifty"]) / joined["nifty"].var())
    price = float(df["Close"].iloc[-1])
    ind = compute_indicators(df)
    atr = float(ind["atr"].iloc[-1]) if len(ind) else price*0.02
    stop_pct = STOP_ATR_MULT*atr/price*100
    rows = []
    for name, shock, desc in SCENARIOS:
        loss_pct = float(np.clip(beta*shock, -95, 0))
        loss_rs  = capital*loss_pct/100
        prot_rs  = -capital*stop_pct/100
        rows.append({"Scenario": name, "NIFTY Shock": f"{shock}%",
                     "Your Stock (β-adj)": f"{loss_pct:.1f}%",
                     "Unprotected Loss": f"₹{abs(loss_rs):,.0f}",
                     "With 2-ATR Stop*": f"₹{abs(min(prot_rs, loss_rs) if abs(prot_rs)<abs(loss_rs) else loss_rs):,.0f}",
                     "_loss": loss_pct})
    return {"beta": round(beta,2), "price": price,
            "stop_pct": round(stop_pct,1),
            "table": pd.DataFrame(rows), "capital": capital}


# ════════════════════════════════════════════════════════════════════════════
# CHARTS
# ════════════════════════════════════════════════════════════════════════════

def chart_decision(df, dec):
    d = compute_indicators(df).tail(120)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor="rgba(31,185,122,0.25)",
        decreasing_fillcolor="rgba(232,85,78,0.25)", showlegend=False))
    fig.add_trace(go.Scatter(x=d.index, y=d["ma50"], name="MA50",
        line=dict(color=BLUE, width=1.3)))
    fig.add_trace(go.Scatter(x=d.index, y=d["vwap20"], name="VWAP20",
        line=dict(color=ACCENT, width=1.3, dash="dot")))
    if dec["stop"]:
        fig.add_hline(y=dec["price"], line_color=IVORY, line_dash="dot",
                      annotation_text=f"ENTRY ₹{dec['price']:,.1f}",
                      annotation_font_color=IVORY)
        fig.add_hline(y=dec["stop"], line_color=RED, line_dash="dash",
                      annotation_text=f"STOP ₹{dec['stop']:,.1f}",
                      annotation_font_color=RED)
        fig.add_hline(y=dec["target"], line_color=GREEN, line_dash="dash",
                      annotation_text=f"TARGET ₹{dec['target']:,.1f}",
                      annotation_font_color=GREEN)
    fig.update_layout(xaxis_rangeslider_visible=False, showlegend=True,
                      legend=dict(orientation="h", y=1.04, x=0))
    return _layout(fig, f"{dec['symbol']} · PRICE + ENGINE LEVELS", 480)


def chart_bt_price(df, bt):
    tdf = bt["trades"]
    d = df.copy()
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor="rgba(31,185,122,0.2)",
        decreasing_fillcolor="rgba(232,85,78,0.2)", showlegend=False))
    bx, by, wx, wy, lx, ly = [], [], [], [], [], []
    for _, t in tdf.iterrows():
        ed, xd = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
        if ed in d.index: bx.append(ed); by.append(float(t["entry"])*0.99)
        if xd in d.index:
            (wx if t["outcome"]=="WIN" else lx).append(xd)
            (wy if t["outcome"]=="WIN" else ly).append(float(t["exit"])*1.008)
    if bx: fig.add_trace(go.Scatter(x=bx, y=by, mode="markers+text",
        marker=dict(color=GREEN, size=12, symbol="triangle-up"),
        text=["BUY"]*len(bx), textposition="bottom center",
        textfont=dict(color=GREEN, size=9), name="Entry"))
    if wx: fig.add_trace(go.Scatter(x=wx, y=wy, mode="markers+text",
        marker=dict(color=ACCENT, size=11, symbol="circle"),
        text=["WIN"]*len(wx), textposition="top center",
        textfont=dict(color=ACCENT, size=9), name="Exit WIN"))
    if lx: fig.add_trace(go.Scatter(x=lx, y=ly, mode="markers+text",
        marker=dict(color=RED, size=11, symbol="x"),
        text=["LOSS"]*len(lx), textposition="top center",
        textfont=dict(color=RED, size=9), name="Exit LOSS"))
    fig.update_layout(xaxis_rangeslider_visible=False, showlegend=True,
                      legend=dict(orientation="h", y=1.04, x=0))
    return _layout(fig, "ENGINE BACKTEST · EVERY SIGNAL IT GAVE", 520)


def chart_equity(bt):
    eq = bt["equity_curve"]["equity"]
    fig = go.Figure(go.Scatter(x=eq.index, y=eq.values, fill="tozeroy",
        fillcolor="rgba(201,162,39,0.07)", line=dict(color=ACCENT, width=2)))
    fig.add_hline(y=1_000_000, line_color=MUTE, line_dash="dash")
    return _layout(fig, "EQUITY CURVE (₹10L start)", 260)


def chart_drawdown(bt):
    dd = bt["drawdown"]
    fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy",
        fillcolor="rgba(232,85,78,0.12)", line=dict(color=RED, width=1.5)))
    return _layout(fig, "DRAWDOWN (%)", 220)


def chart_stress(stress):
    t = stress["table"]
    fig = go.Figure(go.Bar(
        x=t["_loss"], y=t["Scenario"], orientation="h",
        marker_color=[RED if v < -25 else AMBER if v < -12 else "#E67E22"
                      for v in t["_loss"]],
        text=[f"{v:.1f}%" for v in t["_loss"]], textposition="outside"))
    fig.update_layout(yaxis=dict(autorange="reversed"))
    return _layout(fig, "STRESS TEST · YOUR STOCK'S LOSS PER SCENARIO (β-ADJUSTED)", 340)


# ════════════════════════════════════════════════════════════════════════════
# MODE 1 · TERMINAL
# ════════════════════════════════════════════════════════════════════════════

def render_mode1():
    c1, c2 = st.columns([3,1])
    symbol = c1.text_input("NSE Symbol", "RELIANCE")
    run    = c2.button("🔍 ANALYSE", type="primary", use_container_width=True)
    if not run:
        st.info("Enter a stock. The engine runs trend, momentum, volume flow, VWAP, "
                "cost basis, VPIN, vol regime, 3-model ML vote and global sentiment, "
                "then gives ONE answer: BUY / SELL / NO TRADE with exact levels.")
        return
    with st.spinner(f"Running decision engine on {symbol.upper()}..."):
        df = fetch_stock(symbol, "3y")
        if df.empty: st.error("No data. Try RELIANCE, TCS, HDFCBANK."); return
        dec = make_decision(df, symbol)
    if "error" in dec: st.error(dec["error"]); return

    v = dec["verdict"]
    v_c = GREEN if v == "BUY" else RED if "SELL" in v else MUTE
    conf = min(abs(dec["score"]), 100)
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {v_c}40;border-left:6px solid {v_c};
    border-radius:12px;padding:22px 26px;margin:12px 0;display:flex;gap:24px;
    align-items:center;flex-wrap:wrap;'>
      <div style='flex:2;min-width:240px;'>
        <div style='font-family:{MONO};font-size:9px;color:{MUTE2};letter-spacing:2px;'>
          ENGINE DECISION · {dec["symbol"]} · ₹{dec["price"]:,.2f}</div>
        <div style='font-family:{MONO};font-size:38px;font-weight:800;color:{v_c};
          margin:6px 0;'>{v}</div>
        <div style='font-family:{MONO};font-size:11px;color:{IVORY};'>
          Composite score <b style='color:{v_c};'>{dec["score"]:+d}</b> / ±100
          &nbsp;·&nbsp; Market regime:
          <b>{"RISK-ON ✅" if dec["risk_on"] else "RISK-OFF ⛔ (blocks BUYs)"}</b>
          &nbsp;·&nbsp; ML: <b>{dec["ml"].get("agreement","n/a") if "error" not in dec["ml"] else "n/a"}
          {dec["ml"].get("direction","") if "error" not in dec["ml"] else ""}</b></div>
      </div>
      <div>{_circle_svg(conf, "CONVICTION", 120, 24)}</div>
    </div>""", unsafe_allow_html=True)

    if dec["stop"]:
        risk_ps = abs(dec["price"]-dec["stop"])
        k = dec["kelly"]
        st.markdown(_sec("TRADE PLAN"), unsafe_allow_html=True)
        g1, g2, g3, g4 = st.columns(4)
        g1.markdown(_premium_kpi("ENTRY", f"₹{dec['price']:,.1f}", "current price", IVORY),
                    unsafe_allow_html=True)
        g2.markdown(_premium_kpi("STOP LOSS", f"₹{dec['stop']:,.1f}",
                    f"2.0 × ATR (₹{dec['atr']:,.1f})", RED), unsafe_allow_html=True)
        g3.markdown(_premium_kpi("TARGET", f"₹{dec['target']:,.1f}",
                    f"3.0 × ATR · R:R {dec['rr']:.1f}", GREEN), unsafe_allow_html=True)
        g4.markdown(_premium_kpi("POSITION SIZE", f"{k['half_kelly_pct']}%",
                    f"half-Kelly (full: {k['kelly_pct']}%)", ACCENT),
                    unsafe_allow_html=True)
        st.caption(f"💡 {k['note']} Risk per share: ₹{risk_ps:,.2f}.")
    else:
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:10px;
        padding:14px 20px;font-family:{MONO};font-size:12px;color:{MUTE};'>
        No trade levels — the engine only issues stops/targets when conviction
        crosses ±{BUY_THRESHOLD}. Sitting out IS a position.</div>""",
        unsafe_allow_html=True)

    st.markdown(_sec("SIGNAL BREAKDOWN · WHAT THE ENGINE SAW"), unsafe_allow_html=True)
    pdf = pd.DataFrame([{"Component": k_, "Points": v_,
                         "Read": "🟢 Bullish" if v_ > 0 else "🔴 Bearish" if v_ < 0 else "⚪ Neutral"}
                        for k_, v_ in dec["parts"].items()])
    st.dataframe(pdf, use_container_width=True, hide_index=True)

    ml = dec["ml"]
    if "error" not in ml:
        st.markdown(_sec("ML ENSEMBLE · 3 MODELS VOTING", TEAL), unsafe_allow_html=True)
        _mrow([("ENSEMBLE 5D FORECAST", f"{ml['pred_5d_pct']:+.2f}%"),
               ("VOTE", f"{ml['agreement']} {ml['direction']}"),
               ("MODEL IC (OOS)", f"{ml['ic']:.4f}"),
               ("MODEL QUALITY", ml["verdict"].split("—")[0].strip())])
        st.dataframe(ml["votes"], use_container_width=True, hide_index=True)

    gs = dec["global"]
    if "error" not in gs:
        g_c = GREEN if gs["score"] >= 20 else RED if gs["score"] <= -20 else MUTE
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {g_c}35;border-left:4px solid {g_c};
        border-radius:10px;padding:12px 18px;margin:10px 0;font-family:{MONO};
        font-size:12px;color:{IVORY};'>🌍 {gs["verdict"]} · score {gs["score"]:+d}
        <span style='color:{MUTE2};font-size:10px;'>(full detail in Mode 3)</span></div>""",
        unsafe_allow_html=True)

    st.plotly_chart(chart_decision(df, dec), use_container_width=True)

    imp = dec["impact"]
    st.markdown(_sec("EXECUTION · MAX SAFE ORDER SIZE (IMPACT MODEL)", BLUE),
                unsafe_allow_html=True)
    _mrow([("AVG DAILY VALUE", f"₹{imp['adv_rs']/1e7:.1f} Cr"),
           ("MAX SAFE ORDER", f"₹{imp['max_safe_rs']:,.0f}"),
           ("RULE", "≤10bps impact & ≤5% ADV")])
    st.dataframe(imp["table"], use_container_width=True, hide_index=True)

    bt = dec["backtest"]
    if "error" not in bt:
        a_c = GREEN if bt["alpha_vs_bh"] >= 0 else RED
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:10px;
        padding:12px 18px;font-family:{MONO};font-size:11px;color:{IVORY};'>
        📜 This exact engine on {dec["symbol"]}'s history: {bt["total_trades"]} trades ·
        WR <b>{bt["win_rate"]}%</b> · PF <b>{bt["profit_factor"]}</b> ·
        Alpha vs buy-hold <b style='color:{a_c};'>{bt["alpha_vs_bh"]:+.1f}%</b>
        — full replay in Mode 2.</div>""", unsafe_allow_html=True)
    st.caption("Educational analysis · Not SEBI-registered investment advice")


# ════════════════════════════════════════════════════════════════════════════
# MODE 2 · BACKTESTER — symbol + dates. Nothing else. Replays the engine.
# ════════════════════════════════════════════════════════════════════════════

def render_mode2():
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-left:3px solid {TEAL};
    border-radius:8px;padding:14px 18px;margin-bottom:14px;'>
      <div style='font-family:{MONO};font-size:11px;font-weight:700;color:{TEAL};
        letter-spacing:1.5px;margin-bottom:6px;'>MODE 2 · ENGINE BACKTESTER</div>
      <div style='font-size:12.5px;color:{IVORY};line-height:1.8;'>
      No parameters to tune. This replays the <b>exact Mode 1 decision engine</b>
      bar by bar: BUY when score ≥ +{BUY_THRESHOLD} in a risk-on market, 2-ATR stop,
      3-ATR target, exit when the score dies. ML uses walk-forward retraining
      (no lookahead). What you see is what the engine would have told you.
      </div>
    </div>""", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([2,1,1,1])
    symbol = c1.text_input("NSE Symbol", "RELIANCE")
    start  = c2.date_input("From", datetime.date(2021,1,1))
    end    = c3.date_input("To", datetime.date.today())
    run    = c4.button("▶ REPLAY ENGINE", type="primary", use_container_width=True)
    if not run: return
    if start >= end: st.error("From must be before To."); return
    with st.spinner("Replaying the decision engine on history..."):
        df = fetch_range(symbol, str(start), str(end))
        if df.empty: st.error("No data in that range."); return
        bt = engine_backtest(df)
    if "error" in bt: st.error(bt["error"]); return
    a_c = GREEN if bt["alpha_vs_bh"] >= 0 else RED
    ok  = bt["profit_factor"] >= 1.3 and bt["win_rate"] >= 45
    sv_c = GREEN if ok else AMBER if bt["profit_factor"] >= 1.0 else RED
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {sv_c}40;border-left:5px solid {sv_c};
    border-radius:10px;padding:18px 24px;margin:12px 0;'>
      <div style='font-family:{MONO};font-size:20px;font-weight:800;color:{sv_c};'>
        {"✅ ENGINE VALIDATED" if ok else "⚠️ MARGINAL" if bt["profit_factor"]>=1.0 else "❌ ENGINE FAILED"}
        · {symbol.upper()} · {start} → {end}</div>
      <div style='font-family:{MONO};font-size:11px;color:{IVORY};margin-top:6px;'>
        {bt["total_trades"]} trades · WR <b>{bt["win_rate"]}%</b> ·
        PF <b>{bt["profit_factor"]}</b> · Sharpe <b>{bt["sharpe"]}</b> ·
        Engine <b>{bt["total_ret"]:+.1f}%</b> vs Buy&Hold <b>{bt["buy_hold_ret"]:+.1f}%</b>
        → Alpha <b style='color:{a_c};'>{bt["alpha_vs_bh"]:+.1f}%</b> ·
        MaxDD <b style='color:{RED};'>{bt["max_dd"]:.1f}%</b></div>
    </div>""", unsafe_allow_html=True)
    st.plotly_chart(chart_bt_price(df, bt), use_container_width=True)
    st.markdown(_sec("EVERY SIGNAL THE ENGINE GAVE"), unsafe_allow_html=True)
    sig = bt["signals"].copy()
    sig["date"] = pd.to_datetime(sig["date"]).dt.strftime("%Y-%m-%d")
    sig.columns = ["Date","Signal","Price","Detail"]
    st.dataframe(sig, use_container_width=True, hide_index=True)
    st.markdown(_sec("TRADE LOG"), unsafe_allow_html=True)
    st.dataframe(bt["trades"], use_container_width=True, hide_index=True)
    l, r = st.columns(2)
    with l: st.plotly_chart(chart_equity(bt), use_container_width=True)
    with r: st.plotly_chart(chart_drawdown(bt), use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# MODE 3 · STRESS TEST + GLOBAL SENTIMENT
# ════════════════════════════════════════════════════════════════════════════

def render_mode3():
    st.markdown(_sec("🌍 GLOBAL SENTIMENT · WHAT MOVES INDIA TODAY"), unsafe_allow_html=True)
    with st.spinner("Reading global markets..."):
        gs = fetch_global_sentiment()
    if "error" in gs:
        st.warning(gs["error"])
    else:
        g_c = GREEN if gs["score"] >= 20 else RED if gs["score"] <= -20 else MUTE
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {g_c}40;border-left:5px solid {g_c};
        border-radius:10px;padding:16px 22px;margin-bottom:10px;'>
          <div style='font-family:{MONO};font-size:18px;font-weight:800;color:{g_c};'>
            {gs["verdict"]}</div>
          <div style='font-family:{MONO};font-size:11px;color:{MUTE};margin-top:4px;'>
            Composite score {gs["score"]:+d}/±100 across 10 global markets ·
            fed into Mode 1's decision score automatically</div>
        </div>""", unsafe_allow_html=True)
        st.dataframe(gs["table"], use_container_width=True, hide_index=True)

    st.markdown(_sec("🏦 ALADDIN-STYLE STRESS TEST · CRASH REPLAY", RED), unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2,1,1])
    symbol  = c1.text_input("NSE Symbol", "RELIANCE")
    capital = c2.number_input("Position Size (₹)", value=500_000, step=50_000,
                              min_value=10_000)
    run     = c3.button("💥 RUN STRESS TEST", type="primary", use_container_width=True)
    if not run: return
    with st.spinner("Replaying historical crashes on your position..."):
        stx = stress_test(symbol, float(capital))
    if "error" in stx: st.error(stx["error"]); return
    _mrow([("BETA vs NIFTY", f"{stx['beta']:.2f}"),
           ("POSITION", f"₹{capital:,.0f}"),
           ("2-ATR STOP DISTANCE", f"{stx['stop_pct']:.1f}%"),
           ("WORST SCENARIO", stx["table"].iloc[0]["Your Stock (β-adj)"])])
    st.plotly_chart(chart_stress(stx), use_container_width=True)
    st.dataframe(stx["table"].drop(columns="_loss"),
                 use_container_width=True, hide_index=True)
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
    padding:12px 18px;font-size:11.5px;color:{MUTE};line-height:1.8;'>
    *Stop protection assumes the market lets you out at your stop. In a 2008 or
    COVID-style gap crash, stocks open far below stops — the "Unprotected Loss"
    column is your true tail risk. This is why Aladdin exists: firms size positions
    off the <b>stress loss</b>, not the average day. If the worst-case number here
    scares you, your position is too big.</div>""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ════════════════════════════════════════════════════════════════════════════

def render_quant_analysis():
    st.markdown(f"""
    <style>
    [data-testid="metric-container"] {{
      background:linear-gradient(135deg,{PANEL3},{PANEL2}) !important;
      border:1px solid {BORDER2} !important;border-radius:10px !important;
      padding:14px 16px !important;}}
    [data-testid="stMetricLabel"] p {{font-family:{MONO} !important;
      font-size:10px !important;color:{MUTE} !important;letter-spacing:1.5px !important;}}
    [data-testid="stMetricValue"] {{font-family:{MONO} !important;
      font-size:19px !important;color:{IVORY} !important;}}
    </style>""", unsafe_allow_html=True)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{PANEL2},{PANEL});
    border:1px solid {BORDER2};border-radius:10px;padding:14px 22px;
    margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;'>
      <div>
        <div style='font-family:{MONO};font-size:14px;font-weight:700;
          color:{ACCENT};letter-spacing:2.5px;'>ARKA · QUANT DECISION TERMINAL</div>
        <div style='font-family:{MONO};font-size:9px;color:{MUTE2};margin-top:3px;
          letter-spacing:1px;'>ONE ENGINE · ENSEMBLE ML · KELLY · IMPACT MODEL ·
          STRESS TEST · GLOBAL SENTIMENT</div>
      </div>
      <div style='font-family:{MONO};font-size:10px;color:{MUTE};'>{ts} · NSE INDIA</div>
    </div>""", unsafe_allow_html=True)
    mode = st.radio("mode", ["🖥 MODE 1 · TERMINAL",
                             "🎯 MODE 2 · BACKTESTER",
                             "🌍 MODE 3 · STRESS TEST & GLOBAL"],
                    horizontal=True, label_visibility="collapsed")
    if "MODE 1" in mode: render_mode1()
    elif "MODE 2" in mode: render_mode2()
    else: render_mode3()


render_quant_options_page = render_quant_analysis

if __name__ == "__main__":
    st.set_page_config(page_title="ARKA · Quant Decision Terminal",
                       layout="wide", initial_sidebar_state="collapsed")
    render_quant_analysis()
