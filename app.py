import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import requests
from supabase import create_client, Client
from news_feed import news_panel, get_news_dot, _ensure_news_state
from arka_ai import render_arka_ai

# ── Supabase Config ──────────────────────────────────────────
SUPABASE_URL = "https://vpxagxjgtonynblhddwh.supabase.co"
SUPABASE_KEY = "sb_publishable_J709kk-CNgm4GVkd5jemEg_XZb5wPDA"

@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

# ── Supabase Helpers ─────────────────────────────────────────
def db_save_watchlist(symbols: list):
    try:
        supabase.table("watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        if rows:
            supabase.table("watchlist").insert(rows).execute()
        st.session_state.watchlist = symbols
        st.session_state.db_loaded = False
        return True
    except Exception as e:
        st.error(f"Save error: {e}")
        return False

def db_load_watchlist() -> list:
    try:
        res = supabase.table("watchlist").select("symbol").execute()
        return [r["symbol"] for r in res.data] if res.data else []
    except:
        return []

def db_save_alert(symbol: str, alert_type: str, price: float):
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute()
        supabase.table("alerts").insert({
            "symbol": symbol, "alert_type": alert_type,
            "price": price, "active": True
        }).execute()
        return True
    except Exception as e:
        st.error(f"Alert save error: {e}")
        return False

def db_delete_alert(symbol: str):
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute()
        return True
    except:
        return False

def db_load_alerts() -> dict:
    try:
        res = supabase.table("alerts").select("*").eq("active", True).execute()
        return {r["symbol"]: {"type": r["alert_type"], "price": float(r["price"]), "active": True}
                for r in res.data} if res.data else {}
    except:
        return {}

def db_save_admin_watchlist(symbols: list):
    try:
        supabase.table("admin_watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        if rows:
            supabase.table("admin_watchlist").insert(rows).execute()
        st.session_state.admin_watchlist = symbols
        return True
    except Exception as e:
        st.error(f"Admin save error: {e}")
        return False

def db_load_admin_watchlist() -> list:
    try:
        res = supabase.table("admin_watchlist").select("symbol").execute()
        return [r["symbol"] for r in res.data] if res.data else []
    except:
        return []

st.set_page_config(
    page_title="Arka Trades",
    layout="wide",
    page_icon="📈",
    initial_sidebar_state="collapsed"
)

# ── Telegram ─────────────────────────────────────────────────
BOT_TOKEN = "8720913228:AAEJEpA30KiJ5H0XwIdqxfOA5YSjxW3cfK8"
CHAT_ID   = "1987688902"

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except:
        pass

# ── Color Palette ─────────────────────────────────────────────
BG      = "#0A1D4B"   # Deep Regal Navy
CARD    = "#122A5E"   # Polished Mid-Navy
CARD2   = "#0D2050"   # Slightly darker card
GOLD    = "#F5C518"   # Executive Gold
WHITE   = "#FFFFFF"   # Crisp White
BORDER  = "rgba(123,159,255,0.2)"
BORDER2 = "rgba(123,159,255,0.4)"
GREEN   = "#00C896"   # Emerald
RED     = "#FF4D6D"   # Crimson
T2      = "#7B9FFF"   # Muted Blue
NAVY    = "#091840"   # Deep Navy

# ── Session State ─────────────────────────────────────────────
for k, v in {
    "logged_in":       False,
    "disclaimer_done": False,
    "page":            "home",
    "profile":         {"name": "Trader", "email": "", "phone": ""},
    "profile_photo":   None,
    "watchlist":       [],
    "admin_watchlist": [],
    "alerts":          {},
    "alert_fired":     set(),
    "db_loaded":       False,
    "is_admin":        False,
    "menu_open":       True,
    "show_settings":   False,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# Load DB once
if not st.session_state.db_loaded:
    wl = db_load_watchlist()
    if wl: st.session_state.watchlist = wl
    awl = db_load_admin_watchlist()
    if awl: st.session_state.admin_watchlist = awl
    al = db_load_alerts()
    if al: st.session_state.alerts = al
    st.session_state.db_loaded = True

name     = st.session_state.profile.get("name", "Trader") or "Trader"
initial  = name[0].upper()
IS_ADMIN = st.session_state.get("is_admin", False)

# ── Global CSS ────────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Rajdhani:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600;700&display=swap');

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

html, body, .stApp {{
    background: {BG} !important;
    color: {WHITE} !important;
    font-family: 'Rajdhani', sans-serif !important;
}}
header[data-testid="stHeader"] {{ display: none !important; }}
[data-testid="stSidebarCollapsedControl"] {{ display: none !important; }}
section[data-testid="stSidebar"] {{ display: none !important; }}
.block-container {{ padding: 0 !important; max-width: 100% !important; }}

.stTextInput input {{
    background: {CARD2} !important; color: {WHITE} !important;
    border: 1px solid {BORDER2} !important; border-radius: 8px !important;
    font-family: 'Rajdhani', sans-serif !important; font-size: 15px !important;
    font-weight: 600 !important;
}}
.stTextInput label {{
    color: {T2} !important; font-size: 11px !important;
    font-weight: 700 !important; letter-spacing: 2px !important;
    text-transform: uppercase !important;
}}
.stTextArea textarea {{
    background: {CARD2} !important; color: {WHITE} !important;
    border: 1px solid {BORDER2} !important; border-radius: 8px !important;
    font-family: 'Rajdhani', sans-serif !important;
}}
[data-testid="stForm"] {{ background: transparent !important; border: none !important; padding: 0 !important; }}
[data-testid="metric-container"] {{
    background: {CARD} !important; border: 1px solid {BORDER} !important;
    border-radius: 10px !important; padding: 14px !important;
}}
[data-testid="stMetricLabel"] p {{
    font-size: 10px !important; letter-spacing: 2px !important;
    text-transform: uppercase !important; color: {T2} !important;
    font-family: 'Rajdhani', sans-serif !important; font-weight: 700 !important;
}}
[data-testid="stMetricValue"] {{
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 18px !important; color: {WHITE} !important;
}}
.stCheckbox label {{ color: {WHITE} !important; font-family: 'Rajdhani', sans-serif !important; font-size: 14px !important; font-weight: 600 !important; }}
[data-testid="stSelectbox"] > div > div {{
    background: {CARD2} !important; border: 1px solid {BORDER2} !important;
    color: {WHITE} !important; border-radius: 8px !important;
}}
hr {{ border-color: {BORDER} !important; }}
.stProgress > div > div {{ background: {GOLD} !important; }}
.stRadio label {{ color: {WHITE} !important; font-family: 'Rajdhani', sans-serif !important; font-weight: 600 !important; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{
    background: {CARD} !important;
    border-radius: 10px !important;
    padding: 4px !important;
    border: 1px solid {BORDER} !important;
    gap: 4px !important;
}}
.stTabs [data-baseweb="tab"] {{
    color: {T2} !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    font-size: 14px !important;
    background: transparent !important;
    border-radius: 8px !important;
    padding: 6px 16px !important;
}}
.stTabs [aria-selected="true"] {{
    background: {BG} !important;
    color: {GOLD} !important;
}}
.stTabs [data-baseweb="tab-panel"] {{
    padding-top: 8px !important;
}}

/* Primary buttons */
.stButton > button[kind="primary"] {{
    background: linear-gradient(135deg, {GOLD} 0%, #D4A500 100%) !important;
    color: {NAVY} !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 800 !important;
    font-size: 14px !important;
    letter-spacing: 2px !important;
    border: none !important;
    border-radius: 8px !important;
    text-transform: uppercase !important;
}}
.stButton > button[kind="primary"]:hover {{
    background: linear-gradient(135deg, #FFD740 0%, {GOLD} 100%) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 20px rgba(245,197,24,0.3) !important;
}}

/* Secondary buttons */
.stButton > button:not([kind="primary"]) {{
    background: {CARD} !important;
    color: {WHITE} !important;
    border: 1px solid {BORDER2} !important;
    border-radius: 8px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    font-size: 13px !important;
    transition: all 0.15s !important;
}}
.stButton > button:not([kind="primary"]):hover {{
    border-color: {GOLD} !important;
    color: {GOLD} !important;
    background: rgba(245,197,24,0.06) !important;
}}

/* Nav buttons */
.nav-btn .stButton > button {{
    width: 100% !important;
    text-align: left !important;
    background: transparent !important;
    color: {WHITE} !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    padding: 10px 14px !important;
    margin-bottom: 1px !important;
    letter-spacing: 0.5px !important;
    transition: all 0.15s !important;
}}
.nav-btn .stButton > button:hover {{
    background: rgba(123,159,255,0.1) !important;
    color: {GOLD} !important;
}}
.nav-btn-active .stButton > button {{
    background: rgba(245,197,24,0.1) !important;
    color: {GOLD} !important;
    border-left: 3px solid {GOLD} !important;
    border-radius: 0 8px 8px 0 !important;
    font-weight: 700 !important;
}}

/* Ticker tape */
@keyframes ticker-scroll {{
    0%   {{ transform: translateX(0); }}
    100% {{ transform: translateX(-50%); }}
}}
.ticker-wrap {{
    position: fixed;
    bottom: 0; left: 0; right: 0;
    background: {NAVY};
    border-top: 1px solid {BORDER2};
    overflow: hidden;
    height: 34px;
    z-index: 9999;
    display: flex;
    align-items: center;
}}
.ticker-inner {{
    display: inline-flex;
    white-space: nowrap;
    animation: ticker-scroll 50s linear infinite;
}}
.ticker-item {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    padding: 0 28px 0 0;
    color: {WHITE};
}}

/* Scrollbar */
::-webkit-scrollbar {{ width: 3px; }}
::-webkit-scrollbar-track {{ background: {BG}; }}
::-webkit-scrollbar-thumb {{ background: {BORDER2}; border-radius: 2px; }}

/* Info / warning */
.stAlert {{ background: {CARD} !important; border-radius: 10px !important; border-color: {BORDER2} !important; color: {WHITE} !important; }}

/* File uploader */
[data-testid="stFileUploader"] {{
    background: {CARD2} !important;
    border: 1px dashed {BORDER2} !important;
    border-radius: 10px !important;
    padding: 8px !important;
}}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────
def parse_csv(file):
    try: df = pd.read_csv(file, header=None)
    except: return []
    syms = []
    for v in df.iloc[:, 0].astype(str):
        v = v.strip()
        if ':' in v: v = v.split(':')[1]
        v = v.split(',')[0].strip()
        if v and v.lower() != 'nan': syms.append(v.upper())
    return list(dict.fromkeys(syms))

def calc_rsi(close, period=14):
    d = close.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, float('nan'))
    v = (100 - 100 / (1 + rs)).iloc[-1]
    return round(float(v), 1) if pd.notna(v) else 0.0

def make_sparkline(prices, color, width=130, height=44):
    """Generate SVG sparkline with gradient fill from price list."""
    if not prices or len(prices) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    mn, mx = min(prices), max(prices)
    rng = mx - mn if mx != mn else 1.0
    pad = 3
    n = len(prices)
    pts = []
    for i, p in enumerate(prices):
        x = pad + (i / (n - 1)) * (width - 2 * pad)
        y = (height - pad) - ((p - mn) / rng) * (height - 2 * pad)
        pts.append((x, y))

    line_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    fill_d = (line_d
              + f" L {pts[-1][0]:.1f},{height - pad:.1f}"
              + f" L {pts[0][0]:.1f},{height - pad:.1f} Z")

    gid = abs(hash(color + str(prices[-1]))) % 99999

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="sg{gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="{color}" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="{color}" stop-opacity="0.0"/>
    </linearGradient>
  </defs>
  <path d="{fill_d}" fill="url(#sg{gid})"/>
  <path d="{line_d}" fill="none" stroke="{color}" stroke-width="1.8"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

@st.cache_data(ttl=14400, show_spinner=False)
def get_static(sym):
    try:
        h = yf.Ticker(sym + ".NS").history(period="60d", interval="1d")
        if len(h) < 16: return None
        prev      = h.iloc[-2]
        sma20     = h["Close"].rolling(20).mean().iloc[-1]
        cur_close = float(h["Close"].iloc[-1])
        sma_pct   = ((cur_close - float(sma20)) / float(sma20) * 100) if pd.notna(sma20) and float(sma20) != 0 else 0.0
        avg_vol   = h["Volume"].rolling(20).mean().iloc[-2]
        vol_dry   = float(avg_vol) / 1_000_000 if pd.notna(avg_vol) else 0.0
        recent    = h["Close"].iloc[-20:].tolist()
        return {
            "pdh":          float(prev["High"]),
            "pdl":          float(prev["Low"]),
            "prev_close":   float(prev["Close"]),
            "rsi":          calc_rsi(h["Close"]),
            "sma_pct":      round(sma_pct, 2),
            "vol_dry":      round(vol_dry, 2),
            "recent_closes": recent,
        }
    except:
        return None

@st.cache_data(ttl=10, show_spinner=False)
def get_price(sym):
    try:
        intra = yf.Ticker(sym + ".NS").history(period="1d", interval="1m")
        if intra.empty: return None
        cur   = float(intra["Close"].iloc[-1])
        daily = yf.Ticker(sym + ".NS").history(period="5d", interval="1d")
        if len(daily) < 2: return None
        prev_close = float(daily["Close"].iloc[-2])
        chg = ((cur - prev_close) / prev_close) * 100
        return {"price": cur, "chg": chg, "prev_close": prev_close}
    except:
        return None

@st.cache_data(ttl=60, show_spinner=False)
def get_index(sym):
    try:
        h = yf.Ticker(sym).history(period="5d", interval="1d")
        if h.empty or len(h) < 2: return None
        cur = float(h["Close"].iloc[-1])
        pc  = float(h["Close"].iloc[-2])
        return {"price": cur, "chg": ((cur - pc) / pc) * 100, "pts": cur - pc}
    except:
        return None

def check_alerts(results):
    for s in results:
        sym = s["sym"]
        if sym not in st.session_state.alerts: continue
        a = st.session_state.alerts[sym]
        if not a.get("active"): continue
        if sym in st.session_state.alert_fired: continue
        cur = s["cur"]; ap = a["price"]; at = a["type"]
        fired = False; msg = ""
        if at == "pdh" and cur >= ap:
            fired = True; msg = f"🔔 <b>{sym}</b> crossed PDH!\nPrice: ₹{cur:.2f} | PDH: ₹{ap:.2f}"
        elif at == "pdl" and cur <= ap:
            fired = True; msg = f"🔔 <b>{sym}</b> broke PDL!\nPrice: ₹{cur:.2f} | PDL: ₹{ap:.2f}"
        elif at == "custom" and cur >= ap:
            fired = True; msg = f"🔔 <b>{sym}</b> hit target!\nPrice: ₹{cur:.2f} | Target: ₹{ap:.2f}"
        if fired:
            send_telegram(msg)
            st.session_state.alert_fired.add(sym)

def section(title):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;margin:28px 0 14px;">
        <div style="flex:1;height:1px;background:{BORDER};"></div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:15px;
             letter-spacing:6px;color:{GOLD};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)

def ticker_tape(results):
    if not results: return
    items = []
    for r in results:
        cc = GREEN if r["chg"] >= 0 else RED
        ar = "▲" if r["chg"] >= 0 else "▼"
        items.append(
            f'<span class="ticker-item">'
            f'<span style="color:{WHITE};font-weight:700;">{r["sym"]}</span>'
            f'&nbsp;<span style="color:{cc};">{ar}{abs(r["chg"]):.2f}%</span>'
            f'&nbsp;<span style="color:{T2};">₹{r["cur"]:.1f}</span>'
            f'</span>'
        )
    full = "".join(items)
    st.markdown(f"""
    <div class="ticker-wrap">
        <div class="ticker-inner">{full}{full}</div>
    </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════
# LOGIN
# ════════════════════════════════════════════════════
if not st.session_state.logged_in:
    st.markdown(f"""
    <style>
    .stApp {{
        background: radial-gradient(ellipse at 20% 50%, rgba(26,50,120,0.8) 0%, {BG} 60%) !important;
    }}
    </style>
    <div style="text-align:center;padding:80px 20px 40px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:96px;
             letter-spacing:14px;color:{GOLD};line-height:1;
             text-shadow:0 0 60px rgba(245,197,24,0.3);">ARKA TRADES</div>
        <div style="font-family:'Rajdhani',sans-serif;font-weight:700;font-size:13px;
             letter-spacing:8px;color:{WHITE};text-transform:uppercase;
             margin-top:10px;opacity:0.7;">
             Finance &nbsp;·&nbsp; Market Education</div>
        <div style="font-size:11px;letter-spacing:4px;
             color:rgba(245,197,24,0.4);text-transform:uppercase;margin-top:28px;">
             ↓ &nbsp; Login Below &nbsp; ↓</div>
    </div>""", unsafe_allow_html=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown(f"""
        <div style="background:{CARD};border:1px solid {BORDER2};border-radius:16px;
             padding:28px 32px 20px;text-align:center;margin-bottom:16px;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:26px;
                 letter-spacing:8px;color:{GOLD};margin-bottom:4px;">SECURE LOGIN</div>
            <div style="font-size:11px;color:{T2};letter-spacing:2px;">
                 ARKA TRADES PLATFORM</div>
        </div>""", unsafe_allow_html=True)
        with st.form("lf"):
            u = st.text_input("Username", placeholder="Enter username")
            p = st.text_input("Password", placeholder="Enter password", type="password")
            ok = st.form_submit_button("LOGIN →", use_container_width=True, type="primary")
            ph = st.empty()
            if ok:
                if u.strip() == "ADMIN4477MAX" and p.strip() == "MOHIT1":
                    ph.success("Welcome, Admin!")
                    time.sleep(1.2)
                    st.session_state.logged_in = True
                    st.session_state.is_admin  = True
                    st.rerun()
                elif u.strip().lower() == "max trades" and p.strip().lower() == "max":
                    ph.success("Login Successful — Welcome to Arka Trades!")
                    time.sleep(1.2)
                    st.session_state.logged_in = True
                    st.session_state.is_admin  = False
                    st.rerun()
                else:
                    ph.error("Invalid username or password.")
        st.markdown(f"""<div style="text-align:center;font-size:11px;color:{T2};
             margin-top:12px;font-style:italic;letter-spacing:1px;">
             Not SEBI registered · Educational use only</div>""",
             unsafe_allow_html=True)
    st.stop()

# ════════════════════════════════════════════════════
# DISCLAIMER
# ════════════════════════════════════════════════════
if not st.session_state.disclaimer_done:
    _, col, _ = st.columns([1, 3, 1])
    with col:
        st.markdown(f"""
        <div style="padding:48px 0 20px;text-align:center;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:34px;
                 letter-spacing:6px;color:{GOLD};">DISCLAIMER &amp; TERMS</div>
            <div style="font-size:11px;letter-spacing:3px;color:{T2};
                 text-transform:uppercase;margin-top:6px;margin-bottom:24px;">
                 Read carefully before continuing</div>
        </div>
        <div style="background:{CARD};border:1px solid {BORDER};border-radius:14px;
             padding:28px;font-size:14px;color:{T2};line-height:2;
             max-height:260px;overflow-y:auto;margin-bottom:20px;
             font-family:'Rajdhani',sans-serif;font-weight:500;">
            <strong style="color:{GOLD}">1. No Financial Advice</strong><br>
            Arka Trades does not provide financial or investment advice. Educational only.<br><br>
            <strong style="color:{GOLD}">2. Not SEBI Registered</strong><br>
            We are not registered with SEBI as investment advisor or research analyst.<br><br>
            <strong style="color:{GOLD}">3. Personal Responsibility</strong><br>
            All trading decisions are yours. You bear full responsibility for profits or losses.<br><br>
            <strong style="color:{GOLD}">4. Data Accuracy</strong><br>
            Market data may be delayed. We do not guarantee accuracy of any data shown.<br><br>
            <strong style="color:{GOLD}">5. Personal Use Only</strong><br>
            For personal educational use only. Not for commercial distribution.
        </div>""", unsafe_allow_html=True)
        t1 = st.checkbox("I understand this platform is for educational use only")
        t2 = st.checkbox("I acknowledge Arka Trades is not SEBI registered")
        t3 = st.checkbox("I accept full responsibility for my own trading decisions")
        t4 = st.checkbox("I agree to the Terms and Conditions above")
        all_ok = t1 and t2 and t3 and t4
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Cancel", use_container_width=True):
                st.session_state.logged_in = False
                st.rerun()
        with c2:
            if st.button("Accept and Enter →", use_container_width=True, type="primary", disabled=not all_ok):
                st.session_state.disclaimer_done = True
                st.toast(f"Welcome, {name}!", icon="👋")
                st.rerun()
        if not all_ok:
            st.caption("Accept all 4 terms above to continue")
    st.stop()

# ════════════════════════════════════════════════════
# MAIN LAYOUT
# ════════════════════════════════════════════════════
if st.session_state.menu_open:
    left, right = st.columns([1, 4.5])
else:
    left, right = st.columns([0.001, 1])

# ── LEFT SIDEBAR ──────────────────────────────────────────────
with left:
    if st.session_state.menu_open:
        st.markdown(f"""
        <div style="background:{CARD};border-right:1px solid {BORDER};min-height:100vh;">
            <div style="padding:22px 16px 16px;border-bottom:1px solid {BORDER};
                 text-align:center;background:linear-gradient(180deg,{NAVY},{CARD});">
                <div style="font-family:'Bebas Neue',sans-serif;font-size:28px;
                     letter-spacing:5px;color:{GOLD};line-height:1.1;
                     text-shadow:0 0 30px rgba(245,197,24,0.3);">ARKA<br>TRADERS</div>
                <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                     font-size:8px;letter-spacing:3px;color:{WHITE};
                     text-transform:uppercase;margin-top:6px;opacity:0.6;">
                     Finance · Market Education</div>
            </div>
        </div>""", unsafe_allow_html=True)

        # Avatar
        photo = st.session_state.get("profile_photo")
        if photo:
            st.image(photo, width=60)
        else:
            st.markdown(f"""
            <div style="text-align:center;padding:16px 0 8px;">
                <div style="width:52px;height:52px;border-radius:10px;
                     background:linear-gradient(135deg,{NAVY} 0%,rgba(245,197,24,0.6) 100%);
                     border:2px solid rgba(245,197,24,0.35);
                     display:flex;align-items:center;justify-content:center;
                     font-weight:900;font-size:20px;color:{WHITE};
                     margin:0 auto 6px;
                     box-shadow:0 0 20px rgba(245,197,24,0.15);">{initial}</div>
                <div style="font-family:'Rajdhani',sans-serif;font-size:12px;
                     color:{T2};font-weight:600;letter-spacing:1px;">{name}</div>
            </div>
            <div style="height:1px;background:{BORDER};margin:0 10px 8px;"></div>
            """, unsafe_allow_html=True)

        # Section label
        st.markdown(f"""<div style="padding:8px 14px 4px;font-family:'Bebas Neue',sans-serif;
             font-size:11px;letter-spacing:4px;color:{GOLD};opacity:0.7;">SERVICES</div>""",
             unsafe_allow_html=True)

        pg_nav = st.session_state.page

        def nav_btn(label, key, icon=""):
            active = (pg_nav == key)
            css = "nav-btn-active" if active else "nav-btn"
            st.markdown(f'<div class="{css}">', unsafe_allow_html=True)
            if st.button(f"{icon}  {label}", key=f"nav_{key}", use_container_width=True):
                st.session_state.page = key
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        nav_btn("Home",     "home",     "🏠")
        nav_btn("Scanner",  "scanner",  "🔍")
        nav_btn("Alerts",   "alerts",   "🔔")
        nav_btn("News",     "news",     "📰")
        nav_btn("@Arka AI", "analysis", "🤖")

        st.markdown(f"""<div style="padding:12px 14px 4px;font-family:'Bebas Neue',sans-serif;
             font-size:11px;letter-spacing:4px;color:{T2};opacity:0.5;">COMING SOON</div>""",
             unsafe_allow_html=True)
        nav_btn("Heatmap",     "heatmap",   "🗺️")
        nav_btn("Auto Alerts", "autoalert", "⚡")

        st.markdown(f"""<div style="padding:12px 14px 4px;font-family:'Bebas Neue',sans-serif;
             font-size:11px;letter-spacing:4px;color:{GOLD};opacity:0.7;">ACCOUNT</div>""",
             unsafe_allow_html=True)
        nav_btn("Profile",    "profile",  "👤")
        nav_btn("Settings",   "settings", "⚙️")
        nav_btn("Contact Us", "contact",  "📬")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:0 10px 8px;'></div>",
                    unsafe_allow_html=True)

        # Interactive profile card at bottom
        st.markdown(f"""
        <div style="background:{CARD2};border:1px solid {BORDER};border-radius:10px;
             margin:4px 8px 4px;padding:10px 12px;">
            <div style="font-family:'Rajdhani',sans-serif;font-size:11px;
                 color:{T2};letter-spacing:1px;margin-bottom:2px;">LOGGED IN AS</div>
            <div style="font-weight:700;font-size:13px;color:{WHITE};">{name}</div>
        </div>""", unsafe_allow_html=True)

        st.markdown('<div class="nav-btn">', unsafe_allow_html=True)
        if st.button(f"⚙  Account Settings", key="profile_toggle", use_container_width=True):
            st.session_state.show_settings = not st.session_state.show_settings
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        # Settings flyout
        if st.session_state.show_settings:
            st.markdown(f"""
            <div style="background:{NAVY};border:1px solid {BORDER2};border-radius:10px;
                 padding:14px;margin:2px 8px 6px;font-size:12px;">
                <div style="color:{GOLD};font-family:'Bebas Neue',sans-serif;
                     font-size:13px;letter-spacing:3px;margin-bottom:10px;">ACCOUNT</div>
                <div style="color:{T2};line-height:2.3;font-family:'Rajdhani',sans-serif;
                     font-size:13px;font-weight:600;">
                    <div>👤 &nbsp;{name}</div>
                    <div>🔔 &nbsp;<span style="color:{WHITE}">{len(st.session_state.alerts)} alerts active</span></div>
                    <div>📋 &nbsp;<span style="color:{WHITE}">{len(st.session_state.watchlist)} stocks</span></div>
                    <div>👑 &nbsp;<span style="color:{WHITE}">{len(st.session_state.admin_watchlist)} Arka stocks</span></div>
                    <div>📡 &nbsp;<span style="color:{GREEN}">DB Connected</span></div>
                    <div>🔑 &nbsp;<span style="color:{'#FFD740' if IS_ADMIN else T2};">{'Admin' if IS_ADMIN else 'Member'}</span></div>
                </div>
            </div>""", unsafe_allow_html=True)
            st.markdown('<div class="nav-btn">', unsafe_allow_html=True)
            if st.button("⚙️  Go to Settings", key="go_settings_btn", use_container_width=True):
                st.session_state.page = "settings"
                st.session_state.show_settings = False
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="nav-btn">', unsafe_allow_html=True)
        if st.button("🚪  Logout", use_container_width=True, key="logout_btn"):
            for k in ["logged_in", "disclaimer_done"]:
                st.session_state[k] = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.empty()

# ── RIGHT CONTENT ─────────────────────────────────────────────
with right:
    pg = st.session_state.page

    # Top navbar
    n1, n2, n3 = st.columns([1, 6, 2])
    with n1:
        if st.button("☰", key="menu_toggle_btn"):
            st.session_state.menu_open = not st.session_state.menu_open
            st.rerun()
    with n2:
        st.markdown(f"""
        <div style="padding:10px 0 8px;border-bottom:1px solid {BORDER};">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:30px;
                 letter-spacing:8px;color:{GOLD};line-height:1;">ARKA TRADES</div>
            <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                 font-size:10px;letter-spacing:4px;color:{WHITE};
                 text-transform:uppercase;margin-top:1px;opacity:0.6;">
                 Finance &nbsp;·&nbsp; Market Education</div>
        </div>""", unsafe_allow_html=True)
    with n3:
        IST_TZ = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST_TZ)
        mkt_open = (
            now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
            <= now_ist
            <= now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        )
        mkt_color = GREEN if mkt_open else RED
        mkt_dot   = "●" if mkt_open else "●"
        mkt_label = "MARKET OPEN" if mkt_open else "MARKET CLOSED"
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:flex-end;
             gap:10px;height:66px;padding-right:4px;">
            <div style="text-align:right;">
                <div style="font-size:10px;color:{mkt_color};font-weight:700;
                     letter-spacing:1px;font-family:'Rajdhani',sans-serif;">
                     <span style="font-size:8px;">{mkt_dot}</span> {mkt_label}</div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:11px;
                     color:{T2};margin-top:1px;">{now_ist.strftime('%H:%M IST')}</div>
            </div>
            <div style="width:38px;height:38px;border-radius:10px;
                 background:linear-gradient(135deg,{NAVY},{CARD});
                 border:2px solid rgba(245,197,24,0.4);
                 display:flex;align-items:center;justify-content:center;
                 font-family:'Rajdhani',sans-serif;font-weight:900;
                 font-size:16px;color:{WHITE};
                 box-shadow:0 0 16px rgba(245,197,24,0.2);">{initial}</div>
            <div style="font-family:'Rajdhani',sans-serif;font-size:13px;
                 color:{WHITE};font-weight:600;">Hello, {name.split()[0]}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:1px;background:{BORDER};margin-bottom:6px;'></div>",
                unsafe_allow_html=True)

    # Index bar (home page only)
    def show_idx(col, label, sym, color):
        d = get_index(sym)
        with col:
            if d:
                cc  = GREEN if d["chg"] >= 0 else RED
                ar  = "▲" if d["chg"] >= 0 else "▼"
                pts = abs(d["pts"])
                st.markdown(f"""
                <div style="background:{CARD};border:1px solid {BORDER};
                     border-top:3px solid {color};border-radius:10px;
                     padding:12px 14px;margin:3px 2px;">
                    <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                         font-size:9px;letter-spacing:3px;color:{T2};
                         text-transform:uppercase;margin-bottom:5px;">{label}</div>
                    <div style="font-family:'JetBrains Mono',monospace;font-weight:700;
                         font-size:17px;color:{WHITE};line-height:1;">{d['price']:,.2f}</div>
                    <div style="display:flex;gap:8px;margin-top:4px;align-items:center;">
                        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;
                              font-weight:700;color:{cc};">{ar}{abs(d['chg']):.2f}%</span>
                        <span style="font-size:10px;color:{T2};opacity:0.8;">{ar}{pts:,.1f}</span>
                    </div>
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background:{CARD};border:1px solid {BORDER};
                     border-top:3px solid {color};border-radius:10px;
                     padding:12px 14px;margin:3px 2px;opacity:0.4;">
                    <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                         font-size:9px;letter-spacing:3px;color:{T2};
                         text-transform:uppercase;margin-bottom:5px;">{label}</div>
                    <div style="font-family:'JetBrains Mono',monospace;
                         font-size:17px;color:{T2};">—</div>
                </div>""", unsafe_allow_html=True)

    if pg == "home":
        r1a, r1b = st.columns(2)
        show_idx(r1a, "NIFTY 50",     "^NSEI",               GOLD)
        show_idx(r1b, "BANK NIFTY",   "^NSEBANK",            GREEN)
        r2a, r2b = st.columns(2)
        show_idx(r2a, "MIDCAP 100",   "NIFTY_MIDCAP_100.NS", "#A78BFA")
        show_idx(r2b, "SMALLCAP 100", "^CNXSMALLCAP",        "#7B9FFF")
        _, r3c, _ = st.columns([1, 2, 1])
        show_idx(r3c, "SENSEX", "^BSESN", "#FF8C42")
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:8px 0 14px;'></div>",
                    unsafe_allow_html=True)

    # ── PAGE CONTENT ─────────────────────────────────────────
    st.markdown('<div style="padding:0 6px 60px;">', unsafe_allow_html=True)

    # ── HOME ─────────────────────────────────────────────────
    if pg == "home":
        h1, h2 = st.columns(2)
        with h1:
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,{NAVY} 0%,{CARD} 100%);
                 border:1px solid {BORDER};border-radius:14px 0 0 14px;
                 padding:40px 32px;min-height:210px;
                 box-shadow:inset 0 0 60px rgba(245,197,24,0.04);">
                <div style="font-family:'Bebas Neue',sans-serif;font-size:52px;
                     letter-spacing:6px;color:{GOLD};line-height:1;margin-bottom:10px;
                     text-shadow:0 0 40px rgba(245,197,24,0.25);">ARKA<br>TRADES</div>
                <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                     font-size:11px;letter-spacing:5px;color:{WHITE};
                     text-transform:uppercase;opacity:0.7;">
                     Finance · Market Education</div>
            </div>""", unsafe_allow_html=True)
        with h2:
            st.markdown(f"""
            <div style="background:{CARD};border:1px solid {BORDER};
                 border-radius:0 14px 14px 0;padding:40px 32px;min-height:210px;">
                <div style="font-family:'Rajdhani',sans-serif;font-weight:600;
                     font-size:15px;color:{WHITE};line-height:1.9;
                     margin-bottom:14px;opacity:0.9;">
                    Trade smarter with<br>
                    <strong style="color:{GOLD}">precision-based alerts.</strong><br>
                    Real-time breakout insights and watchlist<br>
                    analysis — built for traders who value<br>
                    <strong style="color:{GOLD}">clarity and control.</strong>
                </div>
                <div style="font-size:11px;color:{T2};font-style:italic;letter-spacing:1px;">
                    Not SEBI registered. Educational use only.</div>
            </div>""", unsafe_allow_html=True)

        section("TODAY AT A GLANCE")
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        mkt = (now.replace(hour=9, minute=15, second=0, microsecond=0)
               <= now
               <= now.replace(hour=15, minute=30, second=0, microsecond=0))
        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Market Status", "OPEN" if mkt else "CLOSED")
        g2.metric("Date",   now.strftime("%d %b %Y"))
        g3.metric("Time",   now.strftime("%H:%M:%S"))
        g4.metric("Refresh", "10 Seconds")

        section("PLATFORM FEATURES")
        w1, w2, w3 = st.columns(3)
        for col, icon, title, color, desc in [
            (w1, "🔍", "Watchlist Scanner", GOLD,
             "Upload your TradingView watchlist. Instantly see which stocks moved above or below yesterday's high/low range."),
            (w2, "🔔", "Telegram Alerts",   GREEN,
             "Set alerts for PDH, PDL or custom price. Get instant Telegram notifications when your stock hits the level."),
            (w3, "📊", "Arka AI Analysis",  "#A78BFA",
             "AI-powered chart analysis, sector heatmaps, top movers, and market breadth — powered by intelligence."),
        ]:
            with col:
                st.markdown(f"""
                <div style="background:{CARD};border:1px solid {BORDER};
                     border-top:3px solid {color};border-radius:14px;
                     padding:22px;min-height:175px;margin-bottom:8px;
                     transition:all 0.2s;">
                    <div style="font-size:26px;margin-bottom:10px;">{icon}</div>
                    <div style="font-family:'Rajdhani',sans-serif;font-weight:800;
                         font-size:11px;letter-spacing:2px;color:{color};
                         text-transform:uppercase;margin-bottom:8px;">{title}</div>
                    <div style="font-size:13px;color:{T2};line-height:1.7;
                         font-family:'Rajdhani',sans-serif;font-weight:500;">{desc}</div>
                </div>""", unsafe_allow_html=True)

    # ── SCANNER ──────────────────────────────────────────────
    elif pg == "scanner":
        if not st.session_state.admin_watchlist:
            awl = db_load_admin_watchlist()
            if awl: st.session_state.admin_watchlist = awl
        if not st.session_state.watchlist:
            wl = db_load_watchlist()
            if wl: st.session_state.watchlist = wl

        def render_scan_results(syms, key_prefix=""):
            total = len(syms)
            # Dashboard banner
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,{CARD} 0%,{CARD2} 100%);
                 border:1px solid {BORDER};border-radius:12px;
                 padding:14px 18px;margin-bottom:14px;
                 display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                <div>
                    <div style="font-family:'Bebas Neue',sans-serif;font-size:18px;
                         letter-spacing:4px;color:{WHITE};">
                         &#9819; ARKA WATCHLIST DASHBOARD</div>
                    <div style="font-size:12px;color:{T2};font-family:'Rajdhani',sans-serif;
                         font-weight:500;margin-top:2px;">
                         PDH · PDL · RSI · SMA · Volume Dry-Up</div>
                </div>
                <div style="background:rgba(245,197,24,0.12);border:1px solid rgba(245,197,24,0.4);
                     padding:5px 14px;border-radius:20px;font-size:11px;font-weight:700;
                     color:{GOLD};letter-spacing:2px;font-family:'Rajdhani',sans-serif;">
                     {total} STOCKS CURATED</div>
            </div>""", unsafe_allow_html=True)

            sc1, sc2, sc3, sc4 = st.columns([2, 1, 1, 2])
            filt    = sc1.selectbox("Show", ["All", "Above PDH", "Below PDL", "In Range"], key=f"filt_{key_prefix}")
            l10     = sc2.checkbox("10s Live", key=f"l10_{key_prefix}")
            l60     = sc3.checkbox("60s Auto", key=f"l60_{key_prefix}")
            scanbtn = sc4.button("SCAN NOW", use_container_width=True, type="primary", key=f"scan_{key_prefix}")

            if scanbtn:
                results, failed = [], []
                bar = st.progress(0, text="Scanning...")
                for i, sym in enumerate(syms):
                    st_  = get_static(sym)
                    lv   = get_price(sym)
                    if st_ and lv:
                        cur = lv["price"]; chg = lv["chg"]
                        cls = "g" if cur > st_["pdh"] else "r" if cur < st_["pdl"] else "n"
                        results.append({
                            "sym": sym, "cur": cur, "chg": chg,
                            "pdh": st_["pdh"], "pdl": st_["pdl"],
                            "rsi": st_["rsi"], "cls": cls,
                            "sma_pct":      st_["sma_pct"],
                            "vol_dry":      st_["vol_dry"],
                            "recent_closes": st_["recent_closes"],
                        })
                    else:
                        failed.append(sym)
                    bar.progress((i + 1) / len(syms), text=f"Fetching {sym}...")
                bar.empty()
                check_alerts(results)
                st.session_state[f"results_{key_prefix}"] = results
                st.session_state[f"failed_{key_prefix}"]  = failed

            results = st.session_state.get(f"results_{key_prefix}", [])
            failed  = st.session_state.get(f"failed_{key_prefix}",  [])

            if results:
                filtered = results
                if filt == "Above PDH":  filtered = [r for r in results if r["cls"] == "g"]
                elif filt == "Below PDL": filtered = [r for r in results if r["cls"] == "r"]
                elif filt == "In Range":  filtered = [r for r in results if r["cls"] == "n"]
                filtered.sort(key=lambda x: {"g": 0, "r": 1, "n": 2}[x["cls"]])

                g = sum(1 for r in results if r["cls"] == "g")
                r = sum(1 for r in results if r["cls"] == "r")
                n = sum(1 for r in results if r["cls"] == "n")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Above PDH", g)
                m2.metric("Below PDL", r)
                m3.metric("In Range",  n)
                m4.metric("Total",     len(results))

                if failed:
                    with st.expander(f"⚠ {len(failed)} symbols skipped"):
                        st.write(", ".join(failed))

                section("SCAN RESULTS")

                # 3-column card grid — matches screenshot style
                COLS = 3
                for row_i in range(0, len(filtered), COLS):
                    row_items = filtered[row_i: row_i + COLS]
                    cols = st.columns(COLS)
                    for j, s in enumerate(row_items):
                        if s["cls"] == "g":
                            top_c  = GREEN
                            brd_c  = "rgba(0,200,150,0.45)"
                            bg_css = f"linear-gradient(160deg,{CARD} 0%,{CARD2} 55%,rgba(0,200,150,0.06) 100%)"
                        elif s["cls"] == "r":
                            top_c  = RED
                            brd_c  = "rgba(255,77,109,0.45)"
                            bg_css = f"linear-gradient(160deg,{CARD} 0%,{CARD2} 55%,rgba(255,77,109,0.06) 100%)"
                        else:
                            top_c  = T2
                            brd_c  = BORDER
                            bg_css = CARD

                        cc  = GREEN if s["chg"] >= 0 else RED
                        arr = "▲" if s["chg"] >= 0 else "▼"
                        rc  = GREEN if s["rsi"] < 35 else (RED if s["rsi"] > 65 else T2)
                        ha  = (s["sym"] in st.session_state.alerts
                               and st.session_state.alerts[s["sym"]].get("active"))
                        nd  = get_news_dot(s["sym"])

                        news_badge = (
                            f'<span style="background:rgba(245,197,24,0.2);'
                            f'border:1px solid rgba(245,197,24,0.5);'
                            f'color:{GOLD};font-size:9px;font-weight:700;'
                            f'padding:1px 5px;border-radius:4px;'
                            f'margin-left:4px;letter-spacing:1px;">NEWS</span>'
                            if nd else ""
                        )
                        bell_icon = (
                            f'<svg width="12" height="12" viewBox="0 0 24 24" fill="{GOLD}">'
                            f'<path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>'
                            f'</svg>'
                            if ha else ""
                        )

                        spark = make_sparkline(s.get("recent_closes", []), top_c)
                        sma_c = GREEN if s["sma_pct"] >= 0 else RED

                        with cols[j]:
                            st.markdown(f"""
                            <div style="background:{bg_css};border:1px solid {brd_c};
                                 border-top:3px solid {top_c};border-radius:12px;
                                 padding:14px 14px 12px;margin-bottom:10px;">

                                <!-- Header: Symbol + % -->
                                <div style="display:flex;justify-content:space-between;
                                     align-items:flex-start;margin-bottom:4px;">
                                    <div>
                                        <span style="font-family:'Rajdhani',sans-serif;
                                              font-weight:900;font-size:15px;color:{WHITE};
                                              letter-spacing:0.5px;">{s['sym']}</span>
                                        {bell_icon}{news_badge}
                                    </div>
                                    <span style="font-family:'JetBrains Mono',monospace;
                                          font-size:12px;font-weight:700;color:{cc};">
                                          {arr}{abs(s['chg']):.2f}%</span>
                                </div>

                                <!-- Price -->
                                <div style="font-family:'JetBrains Mono',monospace;
                                     font-weight:700;font-size:21px;color:{WHITE};
                                     line-height:1;margin-bottom:10px;
                                     letter-spacing:-0.5px;">₹{s['cur']:,.2f}</div>

                                <!-- Metrics row + Sparkline -->
                                <div style="display:flex;justify-content:space-between;
                                     align-items:flex-end;gap:6px;">
                                    <table style="font-size:11px;border-collapse:collapse;
                                           font-family:'Rajdhani',sans-serif;font-weight:600;
                                           line-height:1.9;">
                                        <tr>
                                            <td style="color:{T2};padding-right:8px;">RSI</td>
                                            <td style="color:{rc};font-family:'JetBrains Mono',monospace;
                                                font-weight:700;font-size:11px;">{s['rsi']:.1f}%</td>
                                        </tr>
                                        <tr>
                                            <td style="color:{T2};padding-right:8px;">SMA</td>
                                            <td style="color:{sma_c};font-family:'JetBrains Mono',monospace;
                                                font-weight:700;font-size:11px;">{s['sma_pct']:+.2f}%</td>
                                        </tr>
                                        <tr>
                                            <td style="color:{T2};padding-right:8px;white-space:nowrap;">Vol Dry</td>
                                            <td style="color:{WHITE};font-family:'JetBrains Mono',monospace;
                                                font-weight:700;font-size:11px;">{s['vol_dry']:.2f}M</td>
                                        </tr>
                                    </table>
                                    <div style="flex-shrink:0;">{spark}</div>
                                </div>
                            </div>""", unsafe_allow_html=True)

                IST_scan = timezone(timedelta(hours=5, minutes=30))
                st.caption(f"Scanned: {datetime.now(IST_scan).strftime('%d %b %Y  %H:%M:%S')}  "
                           f"·  % vs prev close  ·  10s price cache  ·  SMA vs 20d")
                ticker_tape(results)
                if l10:   time.sleep(10);  st.cache_data.clear(); st.rerun()
                elif l60: time.sleep(60); st.cache_data.clear(); st.rerun()

        # Scanner: main + news side panel
        scan_col, news_col = st.columns([3, 1])

        with scan_col:
            tab1, tab2 = st.tabs(["👑 Arka Watchlist", "📋 Your Watchlist"])

            with tab1:
                admin_syms = st.session_state.admin_watchlist
                st.markdown(f"""
                <div style="background:{CARD};border:1px solid {BORDER};
                     border-left:4px solid {GOLD};border-radius:12px;
                     padding:12px 18px;margin:10px 0;">
                    <div style="font-family:'Bebas Neue',sans-serif;font-size:15px;
                         letter-spacing:4px;color:{GOLD};margin-bottom:2px;">
                         👑 ARKA WATCHLIST</div>
                    <div style="font-size:12px;color:{T2};font-family:'Rajdhani',sans-serif;font-weight:600;">
                        {f"{len(admin_syms)} stocks · Curated by Arka Trades" if admin_syms else "No admin watchlist yet"}
                    </div>
                </div>""", unsafe_allow_html=True)

                if IS_ADMIN:
                    uploaded_admin = st.file_uploader(
                        "Upload Arka Watchlist", type=["csv", "txt"], key="admin_upload"
                    )
                    if uploaded_admin:
                        syms = parse_csv(uploaded_admin)
                        if not syms:
                            st.error("No symbols found.")
                        else:
                            if db_save_admin_watchlist(syms):
                                st.success(f"✅ Arka Watchlist updated — {len(syms)} stocks!")
                                st.session_state.admin_watchlist = syms
                                st.rerun()

                if not admin_syms:
                    st.info("Arka Watchlist not available yet.")
                else:
                    render_scan_results(admin_syms, key_prefix="admin")

            with tab2:
                your_syms = st.session_state.watchlist
                st.markdown(f"""
                <div style="background:{CARD};border:1px solid {BORDER};
                     border-left:4px solid {GREEN};border-radius:12px;
                     padding:12px 18px;margin:10px 0;">
                    <div style="font-family:'Bebas Neue',sans-serif;font-size:15px;
                         letter-spacing:4px;color:{GREEN};margin-bottom:2px;">
                         📋 YOUR WATCHLIST</div>
                    <div style="font-size:12px;color:{T2};font-family:'Rajdhani',sans-serif;font-weight:600;">
                        {f"{len(your_syms)} stocks saved in cloud" if your_syms else "No watchlist uploaded yet"}
                    </div>
                </div>""", unsafe_allow_html=True)

                uploaded_yours = st.file_uploader(
                    "Upload Your Watchlist (CSV or TXT)", type=["csv", "txt"], key="your_upload"
                )
                if uploaded_yours:
                    syms = parse_csv(uploaded_yours)
                    if not syms:
                        st.error("No symbols found.")
                    else:
                        if db_save_watchlist(syms):
                            st.success(f"✅ {len(syms)} stocks loaded and saved!")
                            st.session_state.watchlist = syms

                if not your_syms:
                    st.info("Upload your TradingView watchlist above to start scanning.")
                else:
                    render_scan_results(your_syms, key_prefix="yours")

        with news_col:
            st.markdown(f"""
            <div style="background:{CARD};border:1px solid {BORDER};
                 border-radius:12px;padding:14px 12px;margin-top:2px;">
                <div style="font-family:'Bebas Neue',sans-serif;font-size:16px;
                     letter-spacing:4px;color:{GOLD};margin-bottom:10px;
                     border-bottom:1px solid {BORDER};padding-bottom:8px;">
                     TODAY'S NEWS</div>
            </div>""", unsafe_allow_html=True)
            all_syms = list(dict.fromkeys(
                (st.session_state.admin_watchlist or []) + (st.session_state.watchlist or [])
            ))
            if all_syms:
                _ensure_news_state()
                news_panel(all_syms)
            else:
                st.info("Load a watchlist to see news.")

    # ── ALERTS ───────────────────────────────────────────────
    elif pg == "alerts":
        section("TELEGRAM ALERTS")

        def render_alert_cards(watchlist, key_suffix=""):
            st.markdown(f"""
            <div style="background:{CARD};border:1px solid {BORDER};
                 border-left:4px solid {GOLD};border-radius:12px;
                 padding:14px 18px;margin-bottom:18px;">
                <div style="font-size:13px;color:{T2};line-height:1.8;
                     font-family:'Rajdhani',sans-serif;font-weight:600;">
                    Tap <strong style="color:{WHITE}">Set</strong> next to any stock.
                    Gold bell = alert ON. Outline = alert OFF.
                    You'll receive a Telegram notification when price hits the level.
                </div>
            </div>""", unsafe_allow_html=True)

            COLS = 4
            rows = [watchlist[i:i + COLS] for i in range(0, len(watchlist), COLS)]
            for row in rows:
                cols = st.columns(COLS)
                for j, sym in enumerate(row):
                    has_alert = (sym in st.session_state.alerts
                                 and st.session_state.alerts[sym].get("active", False))
                    alert_info = ""
                    if has_alert:
                        a = st.session_state.alerts[sym]
                        alert_info = f"{a['type'].upper()}<br>₹{a['price']:.2f}"

                    bell_on  = f'<svg width="24" height="24" viewBox="0 0 24 24" fill="{GOLD}"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>'
                    bell_off = f'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{T2}" stroke-width="1.5"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>'
                    card_bd = "rgba(245,197,24,0.5)" if has_alert else BORDER
                    card_bg = "rgba(245,197,24,0.06)" if has_alert else CARD

                    with cols[j]:
                        st.markdown(f"""
                        <div style="background:{card_bg};border:1px solid {card_bd};
                             border-radius:12px;padding:14px 10px;
                             text-align:center;margin-bottom:8px;">
                            <div style="font-family:'Rajdhani',sans-serif;font-weight:900;
                                 font-size:14px;color:{WHITE};margin-bottom:8px;
                                 letter-spacing:0.5px;">{sym}</div>
                            <div style="display:flex;justify-content:center;margin-bottom:6px;">
                                 {bell_on if has_alert else bell_off}</div>
                            <div style="font-size:11px;color:{GOLD};line-height:1.6;
                                 font-family:'JetBrains Mono',monospace;font-weight:600;">
                                 {alert_info if alert_info
                                   else f"<span style='color:{T2};font-family:Rajdhani,sans-serif;'>No alert</span>"}</div>
                        </div>""", unsafe_allow_html=True)

                        ba, bb = st.columns(2)
                        with ba:
                            if st.button("Set", key=f"sa_{sym}_{key_suffix}", use_container_width=True):
                                st.session_state[f"open_{sym}_{key_suffix}"] = True
                        with bb:
                            if has_alert:
                                if st.button("Off", key=f"rm_{sym}_{key_suffix}", use_container_width=True):
                                    del st.session_state.alerts[sym]
                                    db_delete_alert(sym)
                                    if sym in st.session_state.alert_fired:
                                        st.session_state.alert_fired.remove(sym)
                                    st.rerun()

                        if st.session_state.get(f"open_{sym}_{key_suffix}"):
                            st_ = get_static(sym)
                            alert_type = st.radio(
                                "Type", ["PDH", "PDL", "Custom"],
                                key=f"at_{sym}_{key_suffix}", horizontal=True
                            )
                            cp = 0.0
                            if alert_type == "Custom":
                                cp = st.number_input(
                                    "Price", key=f"cp_{sym}_{key_suffix}",
                                    min_value=0.0, step=0.5
                                )
                            bc1, bc2 = st.columns(2)
                            with bc1:
                                if st.button("Cancel", key=f"can_{sym}_{key_suffix}"):
                                    st.session_state[f"open_{sym}_{key_suffix}"] = False
                                    st.rerun()
                            with bc2:
                                if st.button("OK", key=f"ok_{sym}_{key_suffix}", type="primary"):
                                    if st_:
                                        if alert_type == "PDH":   price = st_["pdh"]; atype = "pdh"
                                        elif alert_type == "PDL": price = st_["pdl"]; atype = "pdl"
                                        else:                     price = cp;         atype = "custom"
                                        st.session_state.alerts[sym] = {
                                            "type": atype, "price": price, "active": True
                                        }
                                        db_save_alert(sym, atype, price)
                                        if sym in st.session_state.alert_fired:
                                            st.session_state.alert_fired.remove(sym)
                                        send_telegram(
                                            f"Alert set!\n{sym} · {atype.upper()} · ₹{price:.2f}"
                                        )
                                        st.session_state[f"open_{sym}_{key_suffix}"] = False
                                        st.success(f"Alert set for {sym}!")
                                        st.rerun()

        alert_tab1, alert_tab2 = st.tabs(["👑 Arka Watchlist", "📋 Your Watchlist"])
        with alert_tab1:
            wl = st.session_state.get("admin_watchlist", [])
            if not wl: st.warning("Arka Watchlist not available yet.")
            else: render_alert_cards(wl, key_suffix="admin")
        with alert_tab2:
            wl = st.session_state.get("watchlist", [])
            if not wl: st.warning("Upload your watchlist in Scanner first.")
            else: render_alert_cards(wl, key_suffix="yours")

    # ── NEWS ─────────────────────────────────────────────────
    elif pg == "news":
        section("STOCK NEWS")
        all_syms = list(dict.fromkeys(
            (st.session_state.get("admin_watchlist", []) or [])
            + (st.session_state.get("watchlist", []) or [])
        ))
        if not all_syms:
            st.warning("Go to Scanner first and upload your watchlist.")
        else:
            _ensure_news_state()
            news_panel(all_syms)

    # ── ARKA AI / COMING SOON ────────────────────────────────
    elif pg in ["analysis", "heatmap", "autoalert"]:
        if pg == "analysis":
            render_arka_ai()
        else:
            section("COMING SOON")
            labels = {"heatmap": "Market Heatmap", "autoalert": "Auto Smart Alerts"}
            st.markdown(f"""
            <div style="background:{CARD};border:1px dashed {BORDER2};border-radius:16px;
                 padding:100px 20px;text-align:center;margin:20px 0;">
                <div style="font-size:48px;margin-bottom:16px;">🚧</div>
                <div style="font-family:'Bebas Neue',sans-serif;font-size:34px;
                     letter-spacing:5px;color:{T2};margin-bottom:12px;">
                     {labels.get(pg, 'Coming Soon')}</div>
                <div style="font-size:14px;color:{T2};opacity:.6;
                     font-family:'Rajdhani',sans-serif;font-weight:600;letter-spacing:1px;">
                     This feature is currently under development</div>
            </div>""", unsafe_allow_html=True)

    # ── PROFILE ──────────────────────────────────────────────
    elif pg == "profile":
        section("MY PROFILE")
        p1, p2 = st.columns([1, 2])
        with p1:
            photo = st.session_state.get("profile_photo")
            if photo:
                st.image(photo, width=110)
                st.caption(name)
            else:
                st.markdown(f"""
                <div style="width:96px;height:96px;border-radius:14px;
                     background:linear-gradient(135deg,{NAVY} 0%,rgba(245,197,24,0.6) 100%);
                     border:3px solid rgba(245,197,24,0.35);
                     display:flex;align-items:center;justify-content:center;
                     font-weight:900;font-size:36px;color:{WHITE};
                     margin-bottom:12px;
                     box-shadow:0 0 30px rgba(245,197,24,0.2);">{initial}</div>
                <div style="font-family:'Bebas Neue',sans-serif;font-size:20px;
                     letter-spacing:3px;color:{GOLD};">{name}</div>
                <div style="font-size:11px;color:{T2};letter-spacing:2px;
                     text-transform:uppercase;margin-top:3px;
                     font-family:'Rajdhani',sans-serif;font-weight:700;">
                     {'Admin' if IS_ADMIN else 'Arka Trades Member'}</div>
                """, unsafe_allow_html=True)
        with p2:
            with st.form("pf"):
                a, b = st.columns(2)
                nn = a.text_input("Full Name",      value=st.session_state.profile["name"])
                np = b.text_input("Contact Number", value=st.session_state.profile["phone"])
                ne = st.text_input("Email Address", value=st.session_state.profile["email"])
                ph = st.file_uploader("Upload Profile Photo", type=["jpg", "jpeg", "png"])
                if st.form_submit_button("Save Profile", use_container_width=True):
                    st.session_state.profile.update({"name": nn, "phone": np, "email": ne})
                    if ph: st.session_state["profile_photo"] = ph
                    st.success(f"Saved! Welcome, {nn}!")
                    st.rerun()

    # ── SETTINGS ─────────────────────────────────────────────
    elif pg == "settings":
        section("SETTINGS")
        st.markdown(f"#### 🎨 Theme")
        t1, t2 = st.columns(2)
        with t1:
            st.markdown(f"""
            <div style="background:{CARD};border:2px solid {GOLD};border-radius:12px;
                 padding:20px;text-align:center;">
                <div style="font-size:28px;margin-bottom:8px;">🌊</div>
                <div style="font-family:'Rajdhani',sans-serif;font-weight:800;
                     font-size:14px;color:{GOLD};letter-spacing:2px;">NAVY DARK</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Currently Active</div>
            </div>""", unsafe_allow_html=True)
        with t2:
            st.markdown(f"""
            <div style="background:{CARD};border:2px solid {BORDER};border-radius:12px;
                 padding:20px;text-align:center;opacity:0.5;">
                <div style="font-size:28px;margin-bottom:8px;">☀️</div>
                <div style="font-family:'Rajdhani',sans-serif;font-weight:800;
                     font-size:14px;color:{WHITE};letter-spacing:2px;">LIGHT MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Coming Soon</div>
            </div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 🔔 Telegram Notifications")
        st.info(f"Bot connected · Chat ID: {CHAT_ID}")
        if st.button("Send Test Notification", use_container_width=True):
            send_telegram("✅ <b>Arka Trades</b>\nTest notification successful!")
            st.success("Test sent to Telegram!")
        st.divider()
        st.markdown("#### 📡 Broker API Integration")
        st.markdown(f"""
        <div style="background:{CARD};border:1px dashed {BORDER2};border-radius:10px;
             padding:20px;text-align:center;opacity:0.6;">
            <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                 color:{T2};font-size:14px;letter-spacing:2px;">COMING SOON</div>
        </div>""", unsafe_allow_html=True)

    # ── CONTACT ──────────────────────────────────────────────
    elif pg == "contact":
        section("CONTACT US")
        c1, c2 = st.columns([1, 1])
        with c1:
            st.markdown(f"""
            <div style="background:{CARD};border:1px solid {BORDER};
                 border-left:4px solid {GOLD};border-radius:14px;padding:26px;">
                <div style="font-family:'Rajdhani',sans-serif;font-weight:800;
                     font-size:12px;letter-spacing:3px;color:{GOLD};
                     text-transform:uppercase;margin-bottom:14px;">GET IN TOUCH</div>
                <div style="font-size:14px;color:{T2};line-height:2;margin-bottom:18px;
                     font-family:'Rajdhani',sans-serif;font-weight:500;">
                    Questions, feedback or suggestions?<br>We'd love to hear from you.
                </div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:12px;
                     color:{GOLD};font-weight:700;word-break:break-all;">
                     Mohitdevsinghchib644@gmail.com</div>
                <div style="font-size:12px;color:{T2};margin-top:10px;
                     font-family:'Rajdhani',sans-serif;font-weight:500;">
                     Mention ARKA TRADES in subject line.<br>Reply within 24 hours.</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            with st.form("cf"):
                n = st.text_input("Your Name")
                e = st.text_input("Your Email")
                m = st.text_area("Message", height=120)
                if st.form_submit_button("Send Message →", use_container_width=True):
                    if n and m:
                        st.success("Please email: Mohitdevsinghchib644@gmail.com")
                    else:
                        st.warning("Fill name and message.")

    st.markdown('</div>', unsafe_allow_html=True)
