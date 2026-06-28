"""
quant_options.py — Institutional Options Analytics & Backtesting Engine
========================================================================
MODULE 1 : Advanced Options Analytics & Market Microstructure
  · 3D IV Surface (downsampled for Streamlit Cloud RAM)
  · 0DTE IV Smile / Smirk isolation
  · Non-linear Theta Decay curves
  · Net Gamma Exposure (GEX) Profile
  · Vanna Flow & Delta-Notional models
  · Live-style Options Order Flow Ladder (simulated)

MODULE 2 : Institutional-Grade Backtesting Engine
  · Flexible event-driven backtester
  · Entry/Exit rules: MA, RSI, BB, MACD, price-action
  · Realistic slippage, commission, compounding
  · Full performance dashboard: Sharpe, Sortino, MDD, Profit Factor
  · Equity curve, drawdown, monthly P&L heatmap

Data    : Robust mock/simulated NSE options chain generator
          Modular fetch_options_chain() for easy live-feed swap
          Historical OHLCV via yfinance (NSE symbols)
Caching : st.cache_data on all heavy computations
Memory  : 3D surface downsampled (20×12 grid max)

Deps    : streamlit numpy pandas scipy plotly yfinance
"""

from __future__ import annotations
import warnings
import datetime
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from scipy.stats import norm
from scipy.optimize import brentq

warnings.filterwarnings("ignore")

# ── Palette (matches parent quant_analysis.py) ──────────────────────────────
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
MONO   = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"

TRADING_DAYS = 252
NSE_LOT      = 50          # Nifty lot size
NSE_SPOT_REF = 24_500.0   # approximate Nifty spot for sim


# ════════════════════════════════════════════════════════════════════════════
# SECTION 0 — SHARED HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _layout(fig: go.Figure, title: str, height: int = 420) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=title, font=dict(size=12, color=ACCENT)),
        paper_bgcolor=PANEL, plot_bgcolor=PANEL, height=height,
        margin=dict(l=8, r=8, t=40, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER, showgrid=True),
        yaxis=dict(gridcolor=BORDER, showgrid=True),
    )
    return fig


def _sec(label: str) -> str:
    return (
        f"<div style='font-family:{MONO};font-size:11px;font-weight:600;"
        f"color:{ACCENT};letter-spacing:1.5px;margin:14px 0 6px;"
        f"border-bottom:1px solid {BORDER};padding-bottom:5px;'>{label}</div>"
    )


def _cap(text: str):
    st.caption(text)


def _mrow(cells: list[tuple[str, str]]):
    cols = st.columns(len(cells))
    for col, (lbl, val) in zip(cols, cells):
        col.metric(lbl, val)


def _fmt(v, suffix="", dp=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:,.{dp}f}{suffix}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 1 — BLACK-SCHOLES ENGINE
# ════════════════════════════════════════════════════════════════════════════

def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str = "call") -> float:
    """Black-Scholes European option price."""
    if T <= 1e-6 or sigma <= 1e-6:
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "call":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float,
              option_type: str = "call") -> dict:
    """Full Greeks: delta, gamma, theta, vega, vanna."""
    if T <= 1e-6 or sigma <= 1e-6:
        return dict(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, vanna=0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    nd1 = norm.pdf(d1)
    gamma = nd1 / (S * sigma * np.sqrt(T))
    vega  = S * nd1 * np.sqrt(T) / 100.0          # per 1% vol move
    vanna = -nd1 * d2 / sigma                       # dDelta/dVol
    if option_type == "call":
        delta = norm.cdf(d1)
        theta = (
            -S * nd1 * sigma / (2.0 * np.sqrt(T))
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        ) / TRADING_DAYS
    else:
        delta = norm.cdf(d1) - 1.0
        theta = (
            -S * nd1 * sigma / (2.0 * np.sqrt(T))
            + r * K * np.exp(-r * T) * norm.cdf(-d2)
        ) / TRADING_DAYS
    return dict(delta=delta, gamma=gamma, theta=theta, vega=vega, vanna=vanna)


def implied_vol(market_price: float, S: float, K: float, T: float,
                r: float, option_type: str = "call") -> float:
    """Brent-method IV solver."""
    if T <= 1e-6 or market_price <= 0:
        return np.nan
    intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if market_price < intrinsic:
        return np.nan
    try:
        fn = lambda sigma: bs_price(S, K, T, r, sigma, option_type) - market_price
        return float(brentq(fn, 1e-4, 10.0, xtol=1e-6, maxiter=200))
    except Exception:
        return np.nan


# ════════════════════════════════════════════════════════════════════════════
# SECTION 2 — MOCK OPTIONS CHAIN GENERATOR
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def generate_mock_options_chain(
    spot: float      = NSE_SPOT_REF,
    r: float         = 0.065,
    index_name: str  = "NIFTY",
) -> pd.DataFrame:
    """
    Generates a realistic NSE-style options chain with simulated:
      - Multiple expiries (0DTE, weekly, monthly, quarterly)
      - Volatility smile / skew (SVI-lite parameterisation)
      - Open Interest shaped around ATM
      - Volume and bid/ask spreads
      - Greeks computed via Black-Scholes

    Returns a tidy DataFrame with one row per (expiry, strike, option_type).
    Swap this function for a live nsepython / broker API call later.
    """
    rng = np.random.default_rng(42)
    today = datetime.date.today()

    # Expiries: 0DTE, 7d, 14d, 28d, 56d, 91d, 180d
    dte_list = [0, 7, 14, 28, 56, 91, 180]
    expiries = [(today + datetime.timedelta(days=d)) for d in dte_list]

    # Strikes: ATM ± 10 strikes at 50-point intervals
    atm = round(spot / 50) * 50
    n_wings = 10
    strikes = np.arange(atm - n_wings * 50, atm + (n_wings + 1) * 50, 50)

    rows = []
    for exp_date, dte in zip(expiries, dte_list):
        T = max(dte, 0.5) / TRADING_DAYS  # ensure >0 for 0DTE
        for K in strikes:
            moneyness = np.log(K / spot)
            # SVI-lite smile: base IV + skew + smile curvature
            base_iv    = 0.15 + 0.02 * abs(moneyness)
            skew_term  = -0.08 * moneyness          # put skew
            smile_term =  0.12 * moneyness ** 2
            dte_premium = 0.03 * np.exp(-dte / 30)  # 0DTE spike
            iv_base    = base_iv + skew_term + smile_term + dte_premium
            iv_base    = float(np.clip(iv_base + rng.normal(0, 0.003), 0.05, 0.90))

            for otype in ["call", "put"]:
                price = bs_price(spot, K, T, r, iv_base, otype)
                greeks = bs_greeks(spot, K, T, r, iv_base, otype)
                # OI: large near ATM, tapering away
                oi_base = int(rng.lognormal(
                    mean=8 - 4 * abs(moneyness),
                    sigma=0.4
                ) * 10)
                oi_base = max(oi_base, 100)
                volume  = int(oi_base * rng.uniform(0.1, 0.6))
                bid_ask = price * rng.uniform(0.01, 0.04)

                rows.append({
                    "expiry"     : exp_date,
                    "dte"        : dte,
                    "strike"     : float(K),
                    "type"       : otype,
                    "spot"       : spot,
                    "iv"         : round(iv_base, 4),
                    "price"      : round(price, 2),
                    "bid"        : round(price - bid_ask / 2, 2),
                    "ask"        : round(price + bid_ask / 2, 2),
                    "oi"         : oi_base,
                    "volume"     : volume,
                    "delta"      : round(greeks["delta"], 4),
                    "gamma"      : round(greeks["gamma"], 6),
                    "theta"      : round(greeks["theta"], 4),
                    "vega"       : round(greeks["vega"], 4),
                    "vanna"      : round(greeks["vanna"], 6),
                    "moneyness"  : round(moneyness, 4),
                })

    df = pd.DataFrame(rows)
    # Simulate net flow: positive = net bought, negative = net sold
    df["net_flow"] = (df["volume"] * np.where(
        np.random.default_rng(7).uniform(0, 1, len(df)) > 0.5, 1, -1
    ) * np.random.default_rng(8).uniform(0.2, 1.0, len(df))).astype(int)
    return df


# ════════════════════════════════════════════════════════════════════════════
# SECTION 3 — IV SURFACE BUILDER
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def build_iv_surface(chain: pd.DataFrame,
                     max_strikes: int = 20,
                     max_expiries: int = 12) -> tuple:
    """
    Build downsampled IV surface grid for 3D Plotly chart.
    Limits memory: max_strikes × max_expiries grid.
    Returns (strikes_arr, dte_arr, iv_matrix_calls, iv_matrix_puts)
    """
    calls = chain[chain["type"] == "call"].copy()
    # Downsample strikes: pick evenly spaced
    all_strikes = sorted(calls["strike"].unique())
    step = max(1, len(all_strikes) // max_strikes)
    strikes_sel = all_strikes[::step][:max_strikes]
    # Downsample expiries
    all_dte = sorted(calls["dte"].unique())[:max_expiries]

    iv_calls = np.full((len(all_dte), len(strikes_sel)), np.nan)
    iv_puts  = np.full((len(all_dte), len(strikes_sel)), np.nan)

    for i, dte in enumerate(all_dte):
        sub = calls[calls["dte"] == dte]
        for j, K in enumerate(strikes_sel):
            row = sub[sub["strike"] == K]
            if not row.empty:
                iv_calls[i, j] = row.iloc[0]["iv"]
        sub_p = chain[(chain["type"] == "put") & (chain["dte"] == dte)]
        for j, K in enumerate(strikes_sel):
            row = sub_p[sub_p["strike"] == K]
            if not row.empty:
                iv_puts[i, j] = row.iloc[0]["iv"]

    return np.array(strikes_sel), np.array(all_dte), iv_calls, iv_puts


@st.cache_data(ttl=300, show_spinner=False)
def build_0dte_smile(chain: pd.DataFrame) -> pd.DataFrame:
    """Extract 0DTE IV smile (calls + puts)."""
    zero = chain[chain["dte"] == 0].copy()
    zero["iv_pct"] = zero["iv"] * 100
    return zero.sort_values("strike")


# ════════════════════════════════════════════════════════════════════════════
# SECTION 4 — GEX CALCULATOR
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def compute_gex(chain: pd.DataFrame, lot_size: int = NSE_LOT) -> pd.DataFrame:
    """
    Net Gamma Exposure by strike.
    Convention (retail-flow model):
      - Retail buys calls → Market Maker (MM) sold calls → MM is short gamma on calls
        MM must BUY underlying as spot rises → Negative (destabilising) GEX for calls
        We flip the sign: MM short calls = POSITIVE net GEX (support below ATM)
      - Retail buys puts → MM short puts → MM must SELL underlying as spot falls
        = NEGATIVE GEX (amplifying down moves)
    Net GEX per strike = (Call GEX - Put GEX) × lot_size × 100 (notional)
    Positive = vol-suppressing support / Negative = vol-amplifying resistance
    """
    nearest_expiry = chain["dte"].min()
    sub = chain[chain["dte"] == nearest_expiry].copy()
    sub["gex_unit"] = sub["gamma"] * sub["oi"] * lot_size * sub["spot"]

    calls = sub[sub["type"] == "call"][["strike", "gex_unit"]].rename(columns={"gex_unit": "call_gex"})
    puts  = sub[sub["type"] == "put"][["strike", "gex_unit"]].rename(columns={"gex_unit": "put_gex"})

    gex = pd.merge(calls, puts, on="strike", how="outer").fillna(0)
    gex["net_gex"]  = (gex["call_gex"] - gex["put_gex"]) / 1e6   # in ₹ Millions
    gex["call_gex"] = gex["call_gex"] / 1e6
    gex["put_gex"]  = -gex["put_gex"] / 1e6                       # flip for viz
    return gex.sort_values("strike").reset_index(drop=True)


def find_gex_levels(gex: pd.DataFrame, spot: float) -> dict:
    """Find key GEX levels: zero-cross, max positive, max negative."""
    pos = gex[gex["net_gex"] > 0]
    neg = gex[gex["net_gex"] < 0]
    above = gex[gex["strike"] > spot]
    below = gex[gex["strike"] < spot]

    call_wall = (
        above.loc[above["net_gex"].idxmax(), "strike"]
        if not above.empty and above["net_gex"].max() > 0
        else None
    )
    put_wall = (
        below.loc[below["net_gex"].idxmin(), "strike"]
        if not below.empty and below["net_gex"].min() < 0
        else None
    )
    # Zero-GEX crossing (flip zone)
    sign_changes = gex[gex["net_gex"] * gex["net_gex"].shift(1) < 0]["strike"].tolist()
    return {
        "call_resistance": call_wall,
        "put_support"    : put_wall,
        "zero_gex_zones" : sign_changes,
        "total_pos_gex"  : float(pos["net_gex"].sum()),
        "total_neg_gex"  : float(neg["net_gex"].sum()),
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 5 — VANNA FLOW
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def compute_vanna_flow(chain: pd.DataFrame, lot_size: int = NSE_LOT) -> pd.DataFrame:
    """
    Vanna exposure = dDelta/dVol × OI × lot_size × spot
    When IV compresses → dealers unwind delta hedges (buy spot)
    When IV expands   → dealers add delta hedges (sell spot)
    Net Vanna Flow shows directional pressure from vol moves.
    Delta Notional = delta × OI × lot_size × spot
    """
    nearest = chain["dte"].min()
    sub = chain[chain["dte"] == nearest].copy()
    sub["delta_notional"] = sub["delta"] * sub["oi"] * lot_size * sub["spot"] / 1e6
    sub["vanna_exp"]      = sub["vanna"] * sub["oi"] * lot_size / 1e3

    calls = sub[sub["type"] == "call"][["strike", "delta_notional", "vanna_exp"]].rename(
        columns={"delta_notional": "call_dn", "vanna_exp": "call_vanna"}
    )
    puts = sub[sub["type"] == "put"][["strike", "delta_notional", "vanna_exp"]].rename(
        columns={"delta_notional": "put_dn", "vanna_exp": "put_vanna"}
    )
    vf = pd.merge(calls, puts, on="strike", how="outer").fillna(0)
    vf["net_delta_notional"] = vf["call_dn"] + vf["put_dn"]
    vf["net_vanna"]          = vf["call_vanna"] + vf["put_vanna"]
    return vf.sort_values("strike").reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 6 — THETA DECAY CURVES
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def theta_decay_curves(
    spot: float   = NSE_SPOT_REF,
    r: float      = 0.065,
    sigma: float  = 0.15,
    horizons: list = None,
) -> pd.DataFrame:
    """Compute theta (daily) for ATM option from t=horizon down to 0DTE."""
    if horizons is None:
        horizons = [90, 60, 30, 7]
    rows = []
    for start_dte in horizons:
        for dte in range(start_dte, 0, -1):
            T = dte / TRADING_DAYS
            g = bs_greeks(spot, spot, T, r, sigma, "call")
            rows.append({
                "start_dte" : start_dte,
                "dte"       : dte,
                "theta"     : g["theta"],
                "theta_pct" : abs(g["theta"]) / spot * 100,
            })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 7 — ORDER FLOW LADDER
# ════════════════════════════════════════════════════════════════════════════

def generate_order_flow_ladder(
    chain: pd.DataFrame,
    spot: float,
    n_strikes: int = 12,
) -> pd.DataFrame:
    """
    Build a live-style order flow ladder showing net contracts bought vs sold
    for the nearest expiry, calls and puts by strike.
    """
    nearest = chain["dte"].min()
    sub = chain[chain["dte"] == nearest].copy()

    atm = round(spot / 50) * 50
    sel_strikes = sorted(
        sub["strike"].unique(),
        key=lambda k: abs(k - atm)
    )[:n_strikes]
    sel_strikes = sorted(sel_strikes)

    rows = []
    for K in sel_strikes:
        cr = sub[(sub["strike"] == K) & (sub["type"] == "call")]
        pr = sub[(sub["strike"] == K) & (sub["type"] == "put")]
        call_flow = int(cr["net_flow"].values[0]) if not cr.empty else 0
        put_flow  = int(pr["net_flow"].values[0]) if not pr.empty else 0
        call_oi   = int(cr["oi"].values[0]) if not cr.empty else 0
        put_oi    = int(pr["oi"].values[0]) if not pr.empty else 0
        call_iv   = float(cr["iv"].values[0]) * 100 if not cr.empty else 0
        put_iv    = float(pr["iv"].values[0]) * 100 if not pr.empty else 0
        rows.append({
            "strike"   : K,
            "is_atm"   : abs(K - atm) <= 25,
            "call_flow": call_flow,
            "put_flow" : put_flow,
            "call_oi"  : call_oi,
            "put_oi"   : put_oi,
            "call_iv"  : round(call_iv, 1),
            "put_iv"   : round(put_iv, 1),
        })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 8 — BACKTESTING ENGINE
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def fetch_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV from yfinance. Adds .NS suffix for NSE."""
    try:
        import yfinance as yf
        sym = symbol.strip().upper()
        if not sym.endswith(".NS") and "^" not in sym:
            sym += ".NS"
        df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        st.warning(f"Data fetch error: {e}")
        return pd.DataFrame()


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicators used by entry/exit rules."""
    c = df["Close"].copy()
    df = df.copy()

    # Moving averages
    for p in [20, 50, 200]:
        df[f"MA{p}"] = c.rolling(p).mean()
    df["EMA20"]  = c.ewm(span=20, adjust=False).mean()

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # Bollinger Bands
    mid   = c.rolling(20).mean()
    std   = c.rolling(20).std()
    df["BB_upper"] = mid + 2 * std
    df["BB_lower"] = mid - 2 * std
    df["BB_mid"]   = mid

    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["MACD"]        = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"]   = df["MACD"] - df["MACD_signal"]

    # ATR
    hl  = df["High"] - df["Low"]
    hc  = (df["High"] - df["Close"].shift()).abs()
    lc  = (df["Low"]  - df["Close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    # Volatility
    df["daily_ret"] = np.log(c / c.shift(1))
    df["vol_20"]    = df["daily_ret"].rolling(20).std() * np.sqrt(TRADING_DAYS)

    return df


def _entry_signal(row, rules: dict) -> int:
    """Return +1 (long), -1 (short), 0 (no signal) based on rules."""
    sig_long  = []
    sig_short = []

    if rules.get("ma_cross"):
        sig_long.append(row["MA20"]  > row["MA50"])
        sig_short.append(row["MA20"] < row["MA50"])

    if rules.get("ema_cross"):
        sig_long.append(row["EMA20"]  > row["MA50"])
        sig_short.append(row["EMA20"] < row["MA50"])

    if rules.get("rsi_ob_os"):
        sig_long.append(row["RSI"]  < 35)
        sig_short.append(row["RSI"] > 65)

    if rules.get("bb_squeeze"):
        sig_long.append(row["Close"]  < row["BB_lower"])
        sig_short.append(row["Close"] > row["BB_upper"])

    if rules.get("macd_cross"):
        sig_long.append(row["MACD"]  > row["MACD_signal"])
        sig_short.append(row["MACD"] < row["MACD_signal"])

    if rules.get("ma200_trend"):
        sig_long.append(row["Close"]  > row["MA200"])
        sig_short.append(row["Close"] < row["MA200"])

    if not sig_long:
        return 0
    if all(sig_long):
        return 1
    if all(sig_short):
        return -1
    return 0


def _exit_signal(row, entry_price: float, side: int,
                 rules: dict, holding_days: int, max_hold: int,
                 stop_pct: float, target_pct: float) -> bool:
    """True = exit position."""
    # Fixed stop / target
    if side == 1:
        if row["Close"] <= entry_price * (1 - stop_pct):
            return True
        if row["Close"] >= entry_price * (1 + target_pct):
            return True
    else:
        if row["Close"] >= entry_price * (1 + stop_pct):
            return True
        if row["Close"] <= entry_price * (1 - target_pct):
            return True

    # Time exit
    if holding_days >= max_hold:
        return True

    # Rule-based exit
    if rules.get("ma_cross"):
        if side ==  1 and row["MA20"] < row["MA50"]: return True
        if side == -1 and row["MA20"] > row["MA50"]: return True
    if rules.get("rsi_ob_os"):
        if side ==  1 and row["RSI"] > 55: return True
        if side == -1 and row["RSI"] < 45: return True

    return False


@st.cache_data(ttl=300, show_spinner=False)
def run_backtest(
    df_raw       : pd.DataFrame,
    entry_rules  : dict,
    initial_cap  : float = 1_000_000.0,
    commission   : float = 0.0015,       # 15 bps per side
    slippage     : float = 0.0005,       # 5 bps
    position_pct : float = 0.10,         # 10% of equity per trade
    max_hold_days: int   = 20,
    stop_pct     : float = 0.05,
    target_pct   : float = 0.10,
    direction    : str   = "both",       # "long", "short", "both"
) -> dict:
    """
    Event-driven backtesting engine.
    Returns complete metrics + trade log + equity curve.
    """
    if df_raw.empty or len(df_raw) < 60:
        return {"error": "Insufficient data — need 60+ bars."}

    df = _compute_indicators(df_raw)
    df = df.dropna(subset=["MA50", "RSI", "BB_upper", "MACD_signal"])
    if len(df) < 30:
        return {"error": "Too many NaN after indicators — try a longer date range."}

    equity  = initial_cap
    trades  = []
    equity_curve = []
    position = None      # dict: {side, entry_price, entry_date, shares, cost}
    holding_days = 0

    for i, (date, row) in enumerate(df.iterrows()):
        equity_curve.append({"date": date, "equity": equity})

        if position is None:
            sig = _entry_signal(row, entry_rules)
            if direction == "long"  and sig == -1: sig = 0
            if direction == "short" and sig ==  1: sig = 0

            if sig != 0:
                # Slippage: buy slightly above / sell slightly below
                exec_price = row["Close"] * (1 + sig * slippage)
                invest     = equity * position_pct
                shares     = invest / exec_price
                cost       = invest * commission
                position   = {
                    "side"       : sig,
                    "entry_price": exec_price,
                    "entry_date" : date,
                    "shares"     : shares,
                    "cost_in"    : cost,
                }
                holding_days = 0
        else:
            holding_days += 1
            should_exit = _exit_signal(
                row, position["entry_price"], position["side"],
                entry_rules, holding_days, max_hold_days, stop_pct, target_pct
            )
            if should_exit or i == len(df) - 1:
                side       = position["side"]
                exit_price = row["Close"] * (1 - side * slippage)
                pnl_pct    = side * (exit_price / position["entry_price"] - 1)
                cost_out   = position["shares"] * exit_price * commission
                net_pnl    = position["shares"] * (exit_price - position["entry_price"]) * side
                net_pnl   -= (position["cost_in"] + cost_out)
                equity    += net_pnl

                trades.append({
                    "entry_date" : position["entry_date"],
                    "exit_date"  : date,
                    "side"       : "LONG" if side == 1 else "SHORT",
                    "entry_price": round(position["entry_price"], 2),
                    "exit_price" : round(exit_price, 2),
                    "shares"     : round(position["shares"], 2),
                    "net_pnl"    : round(net_pnl, 2),
                    "pnl_pct"    : round(pnl_pct * 100, 3),
                    "hold_days"  : holding_days,
                    "outcome"    : "WIN" if net_pnl > 0 else "LOSS",
                })
                position     = None
                holding_days = 0

    if not trades:
        return {"error": "No trades generated — try adjusting entry rules or date range."}

    # ── Performance Metrics ──
    eq_df  = pd.DataFrame(equity_curve).set_index("date")
    eq_ser = eq_df["equity"]
    tdf    = pd.DataFrame(trades)
    rets   = eq_ser.pct_change().dropna()

    total_trades  = len(tdf)
    wins          = tdf[tdf["outcome"] == "WIN"]
    losses        = tdf[tdf["outcome"] == "LOSS"]
    win_rate      = len(wins) / total_trades * 100
    avg_win       = wins["pnl_pct"].mean()   if len(wins)   else 0.0
    avg_loss      = losses["pnl_pct"].mean() if len(losses) else 0.0
    profit_factor = (
        abs(wins["net_pnl"].sum() / losses["net_pnl"].sum())
        if len(losses) and losses["net_pnl"].sum() != 0 else 0.0
    )
    rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

    # Drawdown
    peak    = eq_ser.cummax()
    dd      = (eq_ser - peak) / peak * 100
    max_dd  = float(dd.min())
    # Drawdown duration
    dd_dur  = 0
    cur_dur = 0
    in_dd   = False
    for v in dd.values:
        if v < 0:
            in_dd    = True
            cur_dur += 1
            dd_dur   = max(dd_dur, cur_dur)
        else:
            in_dd    = False
            cur_dur  = 0

    # Sharpe / Sortino
    ann_ret   = float(rets.mean()  * TRADING_DAYS)
    ann_vol   = float(rets.std()   * np.sqrt(TRADING_DAYS))
    down_vol  = rets[rets < 0].std() * np.sqrt(TRADING_DAYS)
    sharpe    = ann_ret / ann_vol   if ann_vol  > 0 else 0.0
    sortino   = ann_ret / down_vol  if down_vol > 0 else 0.0
    total_ret = (equity - initial_cap) / initial_cap * 100

    # Max consecutive losses
    max_cl = cl = 0
    for o in tdf["outcome"].values:
        if o == "LOSS": cl += 1; max_cl = max(max_cl, cl)
        else: cl = 0

    # Monthly P&L
    monthly = tdf.copy()
    monthly["month"] = pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    monthly_pnl = monthly.groupby("month")["net_pnl"].sum()

    return {
        "equity_curve"   : eq_df,
        "trade_log"      : tdf,
        "monthly_pnl"    : monthly_pnl,
        "total_trades"   : total_trades,
        "win_rate"       : round(win_rate, 1),
        "avg_win"        : round(avg_win, 2),
        "avg_loss"       : round(avg_loss, 2),
        "rr_ratio"       : round(rr_ratio, 2),
        "profit_factor"  : round(profit_factor, 2),
        "max_dd"         : round(max_dd, 2),
        "max_dd_dur"     : dd_dur,
        "sharpe"         : round(sharpe, 3),
        "sortino"        : round(sortino, 3),
        "ann_ret"        : round(ann_ret * 100, 2),
        "ann_vol"        : round(ann_vol * 100, 2),
        "total_ret"      : round(total_ret, 2),
        "final_equity"   : round(equity, 2),
        "max_consec_loss": max_cl,
        "expectancy"     : round(tdf["net_pnl"].mean(), 2),
        "drawdown_series": dd,
    }


# ════════════════════════════════════════════════════════════════════════════
# SECTION 9 — PLOTLY CHARTS (MODULE 1)
# ════════════════════════════════════════════════════════════════════════════

def chart_iv_surface_3d(
    strikes: np.ndarray, dte: np.ndarray,
    iv_calls: np.ndarray, iv_puts: np.ndarray
) -> go.Figure:
    """3D IV surface — calls (gold) and puts (blue) overlaid."""
    fig = go.Figure()
    fig.add_trace(go.Surface(
        x=strikes, y=dte, z=iv_calls * 100,
        colorscale=[[0, "#1a2a4a"], [0.5, "#4C8DD6"], [1, "#C9A227"]],
        name="Call IV",
        opacity=0.85,
        showscale=True,
        colorbar=dict(
            x=1.02,
            title=dict(text="IV %", font=dict(color=ACCENT, size=10)),
            tickfont=dict(color=IVORY, size=9),
        ),
    ))
    fig.add_trace(go.Surface(
        x=strikes, y=dte, z=iv_puts * 100,
        colorscale=[[0, "#1a2a3a"], [0.5, "#E8554E"], [1, "#ff9999"]],
        name="Put IV",
        opacity=0.55,
        showscale=False,
    ))
    fig.update_layout(
        template="plotly_dark",
        title=dict(text="3D IMPLIED VOLATILITY SURFACE · CALLS (gold) / PUTS (red)",
                   font=dict(size=12, color=ACCENT)),
        paper_bgcolor=PANEL, height=540,
        scene=dict(
            xaxis=dict(title="Strike", gridcolor=BORDER, color=IVORY),
            yaxis=dict(title="DTE (days)", gridcolor=BORDER, color=IVORY),
            zaxis=dict(title="IV %", gridcolor=BORDER, color=IVORY),
            bgcolor=BG,
            camera=dict(eye=dict(x=1.5, y=-1.8, z=0.8)),
        ),
        font=dict(family="monospace", size=10, color=IVORY),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    return fig


def chart_0dte_smile(smile_df: pd.DataFrame, spot: float) -> go.Figure:
    """0DTE IV smile for calls and puts."""
    calls = smile_df[smile_df["type"] == "call"]
    puts  = smile_df[smile_df["type"] == "put"]
    fig   = go.Figure()
    fig.add_trace(go.Scatter(
        x=calls["strike"], y=calls["iv_pct"],
        mode="lines+markers", name="Call IV",
        line=dict(color=GREEN, width=2), marker=dict(size=5),
    ))
    fig.add_trace(go.Scatter(
        x=puts["strike"], y=puts["iv_pct"],
        mode="lines+markers", name="Put IV",
        line=dict(color=RED, width=2), marker=dict(size=5),
    ))
    fig.add_vline(x=spot, line_color=ACCENT, line_dash="dash", line_width=1.5,
                  annotation_text=f"Spot {spot:,.0f}", annotation_font_color=ACCENT)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.05, x=0))
    return _layout(fig, "0DTE IV SMILE / SMIRK · INTRADAY EXPIRY", height=360)


def chart_theta_decay(theta_df: pd.DataFrame) -> go.Figure:
    """Non-linear theta decay curves for multiple starting DTEs."""
    fig    = go.Figure()
    colors = [ACCENT, BLUE, GREEN, PURPLE]
    for i, (start, grp) in enumerate(theta_df.groupby("start_dte")):
        fig.add_trace(go.Scatter(
            x=grp["dte"], y=grp["theta_pct"],
            mode="lines", name=f"{int(start)}DTE start",
            line=dict(color=colors[i % len(colors)], width=2),
        ))
    fig.update_xaxes(autorange="reversed")
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.05, x=0))
    return _layout(fig, "NON-LINEAR THETA DECAY · ATM OPTION (% of spot / day)", height=360)


def chart_gex_profile(gex: pd.DataFrame, spot: float, levels: dict) -> go.Figure:
    """Horizontal GEX bar chart by strike."""
    colors = [GREEN if v >= 0 else RED for v in gex["net_gex"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=gex["strike"].astype(str),
        x=gex["net_gex"],
        orientation="h",
        marker_color=colors,
        opacity=0.85,
        name="Net GEX",
        text=[f"{v:+.1f}M" for v in gex["net_gex"]],
        textposition="outside",
        textfont=dict(size=9, color=IVORY),
    ))
    # Annotate key levels as shapes (avoids categorical-axis hline issues)
    all_strikes_str = [str(int(k)) for k in sorted(gex["strike"].unique())]
    def _strike_idx(k):
        s = str(int(k))
        return all_strikes_str.index(s) if s in all_strikes_str else None

    if levels["call_resistance"]:
        idx = _strike_idx(levels["call_resistance"])
        if idx is not None:
            fig.add_shape(type="line", y0=idx-0.4, y1=idx+0.4, x0=-50, x1=50,
                          xref="x", yref="y",
                          line=dict(color=RED, dash="dot", width=1.5))
            fig.add_annotation(x=50, y=idx, text="Call Resistance",
                               showarrow=False, font=dict(color=RED, size=9),
                               xanchor="right")
    if levels["put_support"]:
        idx = _strike_idx(levels["put_support"])
        if idx is not None:
            fig.add_shape(type="line", y0=idx-0.4, y1=idx+0.4, x0=-50, x1=50,
                          xref="x", yref="y",
                          line=dict(color=GREEN, dash="dot", width=1.5))
            fig.add_annotation(x=50, y=idx, text="Put Support",
                               showarrow=False, font=dict(color=GREEN, size=9),
                               xanchor="right")
    spot_idx = _strike_idx(round(spot / 50) * 50)
    if spot_idx is not None:
        fig.add_shape(type="line", y0=spot_idx-0.5, y1=spot_idx+0.5, x0=-200, x1=200,
                      xref="x", yref="y",
                      line=dict(color=ACCENT, width=2))
        fig.add_annotation(x=-200, y=spot_idx, text=f"Spot {spot:,.0f}",
                           showarrow=False, font=dict(color=ACCENT, size=9),
                           xanchor="left")
    fig.add_vline(x=0, line_color=MUTE, line_width=1)
    fig.update_layout(showlegend=False)
    return _layout(fig, "NET GAMMA EXPOSURE (GEX) · ₹ Millions · +Green=Support / -Red=Resistance",
                   height=480)


def chart_vanna_flow(vf: pd.DataFrame, spot: float) -> go.Figure:
    """Vanna flow + delta notional chart."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=["Net Delta Notional (₹M)", "Net Vanna Exposure"],
                        vertical_spacing=0.08)
    dn_colors = [GREEN if v >= 0 else RED for v in vf["net_delta_notional"]]
    vn_colors = [PURPLE if v >= 0 else ORANGE for v in vf["net_vanna"]]

    fig.add_trace(go.Bar(
        x=vf["strike"], y=vf["net_delta_notional"],
        marker_color=dn_colors, opacity=0.8, name="ΔNotional",
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=vf["strike"], y=vf["net_vanna"],
        marker_color=vn_colors, opacity=0.8, name="Vanna",
    ), row=2, col=1)
    for r in [1, 2]:
        fig.add_vline(x=spot, line_color=ACCENT, line_dash="dash",
                      line_width=1.5, row=r, col=1)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        height=460, showlegend=False, margin=dict(l=8, r=8, t=50, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER), yaxis=dict(gridcolor=BORDER),
        xaxis2=dict(gridcolor=BORDER), yaxis2=dict(gridcolor=BORDER),
        title=dict(
            text="VANNA FLOW · dDelta/dVol — Dealer Rehedge Direction on Vol Expansion/Compression",
            font=dict(size=12, color=ACCENT)
        ),
    )
    return fig


def chart_order_flow_ladder(ladder: pd.DataFrame, spot: float) -> go.Figure:
    """Vertically aligned order flow ladder: calls and puts net flow."""
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["← PUT FLOW (Net Contracts)", "CALL FLOW (Net Contracts) →"],
        shared_yaxes=True, horizontal_spacing=0.02,
    )
    for _, row in ladder.iterrows():
        K = row["strike"]
        color = ACCENT if row["is_atm"] else (GREEN if row["call_flow"] > 0 else RED)
        fig.add_trace(go.Bar(
            y=[K], x=[row["call_flow"]],
            orientation="h", marker_color=color, opacity=0.85,
            name=f"C {K}", showlegend=False,
            text=[f"{row['call_flow']:+,d}"], textposition="outside",
            textfont=dict(size=9),
        ), row=1, col=2)
    for _, row in ladder.iterrows():
        K = row["strike"]
        color = ACCENT if row["is_atm"] else (GREEN if row["put_flow"] > 0 else RED)
        fig.add_trace(go.Bar(
            y=[K], x=[-row["put_flow"]],          # flip for left-side display
            orientation="h", marker_color=color, opacity=0.85,
            name=f"P {K}", showlegend=False,
            text=[f"{row['put_flow']:+,d}"], textposition="outside",
            textfont=dict(size=9),
        ), row=1, col=1)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        height=480, barmode="overlay", margin=dict(l=8, r=8, t=50, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        title=dict(text="REAL-TIME ORDER FLOW LADDER · Net Contracts (Bought - Sold)",
                   font=dict(size=12, color=ACCENT)),
        yaxis=dict(gridcolor=BORDER),
    )
    return fig


# ════════════════════════════════════════════════════════════════════════════
# SECTION 10 — PLOTLY CHARTS (MODULE 2 — BACKTEST)
# ════════════════════════════════════════════════════════════════════════════

def chart_equity_curve(bt: dict) -> go.Figure:
    """Equity curve with fill."""
    eq = bt["equity_curve"]["equity"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=eq.index, y=eq.values,
        line=dict(color=ACCENT, width=2), fill="tozeroy",
        fillcolor="rgba(201,162,39,0.07)", name="Equity",
    ))
    fig.add_hline(
        y=eq.iloc[0], line_color=MUTE, line_dash="dash", line_width=1,
        annotation_text="Initial Capital", annotation_font_color=MUTE
    )
    return _layout(fig, "EQUITY CURVE (₹)", height=340)


def chart_drawdown(bt: dict) -> go.Figure:
    """Drawdown chart."""
    dd = bt["drawdown_series"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values,
        line=dict(color=RED, width=1.5), fill="tozeroy",
        fillcolor="rgba(232,85,78,0.15)", name="Drawdown %",
    ))
    return _layout(fig, "STRATEGY DRAWDOWN (%)", height=260)


def chart_trade_bars(bt: dict) -> go.Figure:
    """Trade-by-trade P&L bar chart."""
    tdf    = bt["trade_log"]
    colors = [GREEN if o == "WIN" else RED for o in tdf["outcome"]]
    fig = go.Figure(go.Bar(
        x=tdf["exit_date"].astype(str),
        y=tdf["pnl_pct"],
        marker_color=colors,
        opacity=0.85,
        text=[f"{v:+.1f}%" for v in tdf["pnl_pct"]],
        textposition="outside",
        textfont=dict(size=8),
    ))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    mean_pnl = tdf["pnl_pct"].mean()
    fig.add_hline(y=mean_pnl, line_color=ACCENT, line_dash="dash",
                  line_width=1.5,
                  annotation_text=f"Avg {mean_pnl:+.2f}%",
                  annotation_font_color=ACCENT)
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    return _layout(fig, f"TRADE-BY-TRADE P&L · {len(tdf)} TRADES", height=360)


def chart_monthly_pnl(bt: dict) -> go.Figure:
    """Monthly P&L bar chart."""
    mp     = bt["monthly_pnl"]
    colors = [GREEN if v >= 0 else RED for v in mp.values]
    fig    = go.Figure(go.Bar(
        x=[str(p) for p in mp.index],
        y=mp.values,
        marker_color=colors, opacity=0.85,
        text=[f"₹{v:,.0f}" for v in mp.values],
        textposition="outside", textfont=dict(size=8),
    ))
    fig.add_hline(y=0, line_color=MUTE, line_width=1)
    fig.update_layout(xaxis_tickangle=-45, showlegend=False)
    return _layout(fig, "MONTHLY NET P&L (₹)", height=300)


def chart_return_histogram(bt: dict) -> go.Figure:
    """Return distribution histogram."""
    tdf = bt["trade_log"]
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=tdf["pnl_pct"], nbinsx=30,
        marker_color=BLUE, opacity=0.8, name="Trade Returns",
    ))
    fig.add_vline(x=tdf["pnl_pct"].mean(), line_color=GREEN, line_width=2,
                  annotation_text=f"Mean {tdf['pnl_pct'].mean():+.2f}%",
                  annotation_font_color=GREEN)
    return _layout(fig, "DISTRIBUTION OF TRADE RETURNS (%)", height=280)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 11 — STREAMLIT UI LAYOUT
# ════════════════════════════════════════════════════════════════════════════

def render_options_analytics():
    """MODULE 1: Options Analytics Dashboard."""
    st.markdown(f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};"
                f"letter-spacing:1px;margin-bottom:8px;'>"
                f"📡 DATA SOURCE: Simulated NSE options chain (swap fetch fn for live feed)</div>",
                unsafe_allow_html=True)

    # ── Controls ──────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns([2, 1, 1])
    index_name = c1.selectbox("Index", ["NIFTY", "BANKNIFTY", "FINNIFTY"],
                              help="Simulated data — spot adjusts per index")
    spot_map   = {"NIFTY": 24_500.0, "BANKNIFTY": 52_000.0, "FINNIFTY": 23_000.0}
    spot       = spot_map[index_name]
    r_pct      = c2.number_input("Risk-Free Rate %", value=6.5, step=0.25, min_value=0.0) / 100
    run_btn    = c3.button("🔄 REFRESH CHAIN", type="primary", use_container_width=True)

    with st.spinner("Generating options chain..."):
        chain = generate_mock_options_chain(spot=spot, r=r_pct, index_name=index_name)

    # ── Tabs ──────────────────────────────────────────────────────────────
    t0, t1, t2, t3, t4 = st.tabs([
        "📈 IV SURFACE & SMILE",
        "📊 THETA DECAY",
        "🟩 NET GEX",
        "🌊 VANNA FLOW",
        "📋 ORDER FLOW",
    ])

    # ── IV Surface ────────────────────────────────────────────────────────
    with t0:
        st.markdown(_sec("3D IMPLIED VOLATILITY SURFACE"), unsafe_allow_html=True)
        _cap("Downsampled 20×12 grid for performance. Gold=Calls / Red=Puts. Drag to rotate.")
        with st.spinner("Building IV surface..."):
            strikes_arr, dte_arr, iv_calls, iv_puts = build_iv_surface(chain)
        st.plotly_chart(chart_iv_surface_3d(strikes_arr, dte_arr, iv_calls, iv_puts),
                        use_container_width=True)

        st.markdown(_sec("0DTE IV SMILE / SMIRK"), unsafe_allow_html=True)
        _cap("IV by strike for same-day expiry. Smirk = put side elevated (typical for index).")
        smile_df = build_0dte_smile(chain)
        st.plotly_chart(chart_0dte_smile(smile_df, spot), use_container_width=True)

        # Metrics
        atm_call_iv = chain[(chain["type"] == "call") & (chain["dte"] == 0) &
                            (chain["strike"] == round(spot / 50) * 50)]["iv"]
        atm_iv = atm_call_iv.values[0] * 100 if not atm_call_iv.empty else 0
        _mrow([
            ("0DTE ATM IV", f"{atm_iv:.1f}%"),
            ("Strikes (0DTE)", f"{len(smile_df)//2}"),
            ("IV Skew (25δ put−call)", f"{(smile_df[smile_df['type']=='put']['iv'].mean() - smile_df[smile_df['type']=='call']['iv'].mean())*100:+.1f}%"),
            ("Vol Premium (0DTE vs 28d)", f"+{atm_iv - chain[chain['dte']==28][chain['type']=='call']['iv'].mean()*100:.1f}%"),
        ])

    # ── Theta Decay ──────────────────────────────────────────────────────
    with t1:
        st.markdown(_sec("NON-LINEAR THETA DECAY CURVES"), unsafe_allow_html=True)
        _cap("ATM call theta accelerates sharply as DTE → 0 (square-root of time). 0DTE burn is fastest.")
        with st.spinner("Computing theta curves..."):
            theta_df = theta_decay_curves(spot=spot, r=r_pct)
        st.plotly_chart(chart_theta_decay(theta_df), use_container_width=True)

        # Selected DTE row
        cols = st.columns(4)
        for col, dte_sel in zip(cols, [90, 30, 7, 1]):
            row = theta_df[(theta_df["start_dte"] == dte_sel) &
                           (theta_df["dte"] == max(dte_sel // 2, 1))].head(1)
            theta_val = row["theta_pct"].values[0] if not row.empty else 0
            col.metric(f"Theta (DTE≈{dte_sel//2})", f"{theta_val:.4f}% / day")
        _cap("Theta % = daily time-decay as % of spot price for ATM option at that DTE.")

    # ── GEX ──────────────────────────────────────────────────────────────
    with t2:
        st.markdown(_sec("NET GAMMA EXPOSURE (GEX) PROFILE"), unsafe_allow_html=True)
        _cap("+GEX (green) = market makers are net-long gamma → they sell into rallies & buy dips → vol suppression. "
             "−GEX (red) = net-short gamma → amplifies moves.")
        with st.spinner("Computing GEX..."):
            gex    = compute_gex(chain)
            levels = find_gex_levels(gex, spot)
        st.plotly_chart(chart_gex_profile(gex, spot, levels), use_container_width=True)
        _mrow([
            ("Call Resistance", f"₹{levels['call_resistance']:,.0f}" if levels["call_resistance"] else "—"),
            ("Put Support",     f"₹{levels['put_support']:,.0f}"     if levels["put_support"]     else "—"),
            ("Total +GEX",      f"₹{levels['total_pos_gex']:.1f}M"),
            ("Total −GEX",      f"₹{levels['total_neg_gex']:.1f}M"),
        ])
        with st.expander("GEX Data Table"):
            st.dataframe(
                gex[["strike", "call_gex", "put_gex", "net_gex"]].rename(columns={
                    "strike": "Strike", "call_gex": "Call GEX (M₹)",
                    "put_gex": "Put GEX (M₹)", "net_gex": "Net GEX (M₹)"
                }).round(2),
                use_container_width=True, hide_index=True,
            )

    # ── Vanna ────────────────────────────────────────────────────────────
    with t3:
        st.markdown(_sec("VANNA FLOW — DEALER REHEDGE MECHANICS"), unsafe_allow_html=True)
        _cap("Vanna (dΔ/dσ): as IV rises, dealers must adjust delta hedges. "
             "+Vanna above spot → dealers BUY as IV expands (bullish). "
             "−Vanna below spot → dealers SELL as IV expands (bearish).")
        with st.spinner("Computing Vanna..."):
            vf = compute_vanna_flow(chain)
        st.plotly_chart(chart_vanna_flow(vf, spot), use_container_width=True)
        _mrow([
            ("Total Net Δ-Notional", f"₹{vf['net_delta_notional'].sum():.1f}M"),
            ("Net Vanna (above spot)", f"{vf[vf['strike'] > spot]['net_vanna'].sum():.1f}"),
            ("Net Vanna (below spot)", f"{vf[vf['strike'] < spot]['net_vanna'].sum():.1f}"),
            ("Dominant Vanna Bias",
             "BULLISH" if vf[vf["strike"] > spot]["net_vanna"].sum() > 0 else "BEARISH"),
        ])

    # ── Order Flow ───────────────────────────────────────────────────────
    with t4:
        st.markdown(_sec("LIVE OPTIONS ORDER FLOW LADDER"), unsafe_allow_html=True)
        _cap("Net contracts bought vs sold per strike. Refreshes with chain data. "
             "Large net buys on calls → institutional accumulation signal.")
        ladder = generate_order_flow_ladder(chain, spot)
        st.plotly_chart(chart_order_flow_ladder(ladder, spot), use_container_width=True)

        # Tabular ladder (styled)
        st.markdown(_sec("OPTIONS CHAIN SNAPSHOT (NEAREST EXPIRY)"), unsafe_allow_html=True)
        display_df = ladder.copy()
        display_df["CALL Flow"] = display_df["call_flow"].apply(lambda x: f"{x:+,d}")
        display_df["CALL OI"]   = display_df["call_oi"].apply(lambda x: f"{x:,d}")
        display_df["CALL IV%"]  = display_df["call_iv"].apply(lambda x: f"{x:.1f}%")
        display_df["STRIKE"]    = display_df["strike"].apply(lambda x: f"{'★ ' if display_df[display_df['strike']==x]['is_atm'].values[0] else ''}{x:,.0f}")
        display_df["PUT IV%"]   = display_df["put_iv"].apply(lambda x: f"{x:.1f}%")
        display_df["PUT OI"]    = display_df["put_oi"].apply(lambda x: f"{x:,d}")
        display_df["PUT Flow"]  = display_df["put_flow"].apply(lambda x: f"{x:+,d}")
        st.dataframe(
            display_df[["CALL Flow", "CALL OI", "CALL IV%", "STRIKE", "PUT IV%", "PUT OI", "PUT Flow"]],
            use_container_width=True, hide_index=True,
        )
        _cap("★ = ATM strike · Flow = net contracts (+ = net bought, − = net sold)")


# ════════════════════════════════════════════════════════════════════════════

def render_backtesting_engine():
    """MODULE 2: Institutional Backtesting Engine."""

    # ── Input Panel ───────────────────────────────────────────────────────
    with st.form("bt_form"):
        st.markdown(_sec("CONFIGURATION"), unsafe_allow_html=True)
        r1c1, r1c2 = st.columns([2, 2])
        symbol = r1c1.text_input(
            "Asset Symbol (NSE)",
            value="RELIANCE",
            placeholder="e.g. RELIANCE, NIFTY50, TCS",
            help="Enter NSE ticker. .NS appended automatically.",
        )
        direction = r1c2.selectbox("Trade Direction", ["Long Only", "Short Only", "Both"],
                                   help="Allow long, short, or both sides.")
        direction_map = {"Long Only": "long", "Short Only": "short", "Both": "both"}

        r2c1, r2c2 = st.columns(2)
        start_date = r2c1.date_input(
            "Start Date",
            value=datetime.date(2020, 1, 1),
            min_value=datetime.date(2010, 1, 1),
        )
        end_date = r2c2.date_input(
            "End Date",
            value=datetime.date.today(),
        )

        st.markdown(_sec("ENTRY RULES (all selected must trigger together)"), unsafe_allow_html=True)
        ec1, ec2, ec3 = st.columns(3)
        rule_ma_cross  = ec1.checkbox("MA20/MA50 Crossover", value=True)
        rule_ema_cross = ec1.checkbox("EMA20/MA50 Crossover")
        rule_rsi       = ec2.checkbox("RSI Oversold/Overbought (35/65)", value=True)
        rule_bb        = ec2.checkbox("Bollinger Band Breakout")
        rule_macd      = ec3.checkbox("MACD Signal Crossover")
        rule_ma200     = ec3.checkbox("Price vs MA200 Filter")

        st.markdown(_sec("RISK & SIZING"), unsafe_allow_html=True)
        rc1, rc2, rc3, rc4 = st.columns(4)
        initial_cap   = rc1.number_input("Initial Capital (₹)", value=1_000_000, step=100_000, min_value=10_000)
        position_pct  = rc2.slider("Position Size %", 2, 50, 10) / 100
        stop_pct      = rc3.slider("Stop Loss %", 1, 20, 5) / 100
        target_pct    = rc4.slider("Target %", 2, 50, 10) / 100

        rc5, rc6, rc7 = st.columns(3)
        commission    = rc5.number_input("Commission (bps)", value=15.0, step=1.0) / 10_000
        slippage      = rc6.number_input("Slippage (bps)", value=5.0, step=1.0) / 10_000
        max_hold      = rc7.number_input("Max Hold Days", value=20, min_value=1, max_value=252)

        run = st.form_submit_button("▶ RUN BACKTEST", type="primary", use_container_width=True)

    if not run:
        st.info("Configure parameters above and press **RUN BACKTEST**. "
                "Results include equity curve, drawdown, monthly P&L, and full performance metrics.")
        return

    if not symbol.strip():
        st.error("Please enter a valid NSE symbol."); return
    if start_date >= end_date:
        st.error("Start date must be before end date."); return

    entry_rules = {
        "ma_cross"  : rule_ma_cross,
        "ema_cross" : rule_ema_cross,
        "rsi_ob_os" : rule_rsi,
        "bb_squeeze": rule_bb,
        "macd_cross": rule_macd,
        "ma200_trend": rule_ma200,
    }

    # ── Data Fetch ────────────────────────────────────────────────────────
    with st.spinner(f"Fetching {symbol.upper()} OHLCV from NSE via yfinance..."):
        df_raw = fetch_ohlcv(symbol, str(start_date), str(end_date))

    if df_raw.empty:
        st.error(f"No data returned for '{symbol}'. "
                 "Check the ticker (NSE symbols: RELIANCE, TCS, INFY, ^NSEI for Nifty).")
        return

    st.markdown(
        f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-bottom:4px;'>"
        f"Loaded {len(df_raw)} bars · {df_raw.index[0].strftime('%Y-%m-%d')} → "
        f"{df_raw.index[-1].strftime('%Y-%m-%d')}</div>",
        unsafe_allow_html=True
    )

    # ── Run Backtest ──────────────────────────────────────────────────────
    with st.spinner("Running event-driven backtest..."):
        bt = run_backtest(
            df_raw       = df_raw,
            entry_rules  = entry_rules,
            initial_cap  = float(initial_cap),
            commission   = commission,
            slippage     = slippage,
            position_pct = position_pct,
            max_hold_days= int(max_hold),
            stop_pct     = stop_pct,
            target_pct   = target_pct,
            direction    = direction_map[direction],
        )

    if "error" in bt:
        st.error(bt["error"]); return

    # ── Results ───────────────────────────────────────────────────────────
    # Verdict banner
    verdict_color = GREEN if bt["total_ret"] > 0 else RED
    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {BORDER};border-left:4px solid {verdict_color};
    border-radius:8px;padding:14px 20px;margin:10px 0;
    display:flex;justify-content:space-between;align-items:center;'>
      <div>
        <div style='font-family:{MONO};font-size:11px;color:{MUTE};letter-spacing:1px;'>
          BACKTEST RESULT · {symbol.upper()} · {str(start_date)} → {str(end_date)}</div>
        <div style='font-family:{MONO};font-size:24px;font-weight:700;color:{verdict_color};'>
          Total Return: {bt['total_ret']:+.2f}%</div>
        <div style='font-family:{MONO};font-size:12px;color:{IVORY};'>
          ₹{bt['final_equity']:,.0f} final equity on ₹{initial_cap:,.0f} capital</div>
      </div>
      <div style='text-align:right;font-family:{MONO};'>
        <div style='font-size:11px;color:{MUTE};'>Trades: {bt['total_trades']} &nbsp;|&nbsp;
        Win Rate: {bt['win_rate']}% &nbsp;|&nbsp; Profit Factor: {bt['profit_factor']}</div>
        <div style='font-size:11px;color:{MUTE};'>Sharpe: {bt['sharpe']} &nbsp;|&nbsp;
        Sortino: {bt['sortino']} &nbsp;|&nbsp; Max DD: {bt['max_dd']:.1f}%</div>
      </div>
    </div>""", unsafe_allow_html=True)

    # ── Metric Grid ───────────────────────────────────────────────────────
    st.markdown(_sec("PERFORMANCE METRICS"), unsafe_allow_html=True)
    _mrow([
        ("Total Trades",  str(bt["total_trades"])),
        ("Win Rate",      f"{bt['win_rate']}%"),
        ("Avg Win",       f"{bt['avg_win']:+.2f}%"),
        ("Avg Loss",      f"{bt['avg_loss']:+.2f}%"),
    ])
    _mrow([
        ("R:R Ratio",     f"{bt['rr_ratio']:.2f}"),
        ("Profit Factor", f"{bt['profit_factor']:.2f}"),
        ("Expectancy",    f"₹{bt['expectancy']:,.0f}"),
        ("Max Consec. Loss", str(bt["max_consec_loss"])),
    ])
    _mrow([
        ("Max Drawdown",  f"{bt['max_dd']:.1f}%"),
        ("Max DD Dur.",   f"{bt['max_dd_dur']} days"),
        ("Sharpe",        f"{bt['sharpe']:.2f}"),
        ("Sortino",       f"{bt['sortino']:.2f}"),
    ])
    _mrow([
        ("Ann. Return",   f"{bt['ann_ret']:+.2f}%"),
        ("Ann. Vol",      f"{bt['ann_vol']:.2f}%"),
        ("Total Return",  f"{bt['total_ret']:+.2f}%"),
        ("Final Equity",  f"₹{bt['final_equity']:,.0f}"),
    ])

    # ── Charts ────────────────────────────────────────────────────────────
    st.markdown(_sec("EQUITY CURVE"), unsafe_allow_html=True)
    st.plotly_chart(chart_equity_curve(bt), use_container_width=True)

    st.plotly_chart(chart_drawdown(bt), use_container_width=True)
    _cap("Peak-to-trough drawdown. Max DD shown in metrics above.")

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(chart_trade_bars(bt), use_container_width=True)
    with col_b:
        st.plotly_chart(chart_return_histogram(bt), use_container_width=True)

    st.plotly_chart(chart_monthly_pnl(bt), use_container_width=True)

    # ── Trade Ledger ──────────────────────────────────────────────────────
    st.markdown(_sec("INDIVIDUAL TRADE LEDGER"), unsafe_allow_html=True)
    tdf = bt["trade_log"].copy()
    tdf["entry_date"] = pd.to_datetime(tdf["entry_date"]).dt.strftime("%Y-%m-%d")
    tdf["exit_date"]  = pd.to_datetime(tdf["exit_date"]).dt.strftime("%Y-%m-%d")
    tdf["net_pnl"]    = tdf["net_pnl"].apply(lambda x: f"₹{x:,.0f}")
    tdf["pnl_pct"]    = tdf["pnl_pct"].apply(lambda x: f"{x:+.2f}%")
    tdf.columns = [c.replace("_", " ").title() for c in tdf.columns]
    st.dataframe(tdf, use_container_width=True, hide_index=True, height=320)

    st.caption(
        "Backtesting engine: event-driven, bar-by-bar. Costs include commission + slippage. "
        "Past performance does not guarantee future returns. Educational tool only."
    )


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT — call from your main app
# ════════════════════════════════════════════════════════════════════════════

def render_quant_options_page():
    """
    Top-level render function.
    Call this from your main Streamlit app:

        from quant_options import render_quant_options_page
        render_quant_options_page()
    """
    import streamlit as st

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;
    padding:12px 18px;margin-bottom:10px;display:flex;
    justify-content:space-between;align-items:center;'>
      <div style='font-family:{MONO};font-size:13px;font-weight:700;
        color:{ACCENT};letter-spacing:2px;'>ARKA · OPTIONS & BACKTEST ENGINE</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>
        {ts} · NSE INDIA · SIMULATED OPTIONS DATA</div>
    </div>""", unsafe_allow_html=True)

    module_tab, bt_tab = st.tabs([
        "📊 MODULE 1 · OPTIONS ANALYTICS",
        "⚙️ MODULE 2 · BACKTESTING ENGINE",
    ])

    with module_tab:
        render_options_analytics()

    with bt_tab:
        render_backtesting_engine()


# ── Stand-alone run (streamlit run quant_options.py) ────────────────────────
if __name__ == "__main__":
    st.set_page_config(
        page_title="ARKA · Options Analytics & Backtest",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(f"""
    <style>
      .stTabs [data-baseweb="tab-list"] {{ gap:28px; border-bottom:1px solid {BORDER}; }}
      .stTabs [data-baseweb="tab"] {{
        font-family:{MONO}; font-size:11px; letter-spacing:1.5px;
        color:{MUTE}; padding:8px 4px; }}
      .stTabs [aria-selected="true"] {{ color:{ACCENT}; }}
      .block-container {{ padding-top:1.2rem; max-width:1400px; }}
      body {{ background-color:{BG}; }}
    </style>""", unsafe_allow_html=True)

    render_quant_options_page()
