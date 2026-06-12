import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import requests
from supabase import create_client, Client
from news_feed import news_panel, get_news_dot, _ensure_news_state
from arka_ai import render_arka_ai

# ── Supabase ─────────────────────────────────────────────────
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "https://vpxagxjgtonynblhddwh.supabase.co")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "sb_publishable_J709kk-CNgm4GVkd5jemEg_XZb5wPDA")

@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

def db_save_watchlist(symbols: list):
    try:
        supabase.table("watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        if rows: supabase.table("watchlist").insert(rows).execute()
        st.session_state.watchlist = symbols
        st.session_state.db_loaded = False
        return True
    except Exception as e:
        st.error(f"Save error: {e}"); return False

def db_load_watchlist() -> list:
    try:
        res = supabase.table("watchlist").select("symbol").execute()
        return [r["symbol"] for r in res.data] if res.data else []
    except: return []

def db_save_alert(symbol: str, alert_type: str, price: float):
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute()
        supabase.table("alerts").insert({"symbol": symbol, "alert_type": alert_type,
            "price": price, "active": True}).execute()
        return True
    except Exception as e:
        st.error(f"Alert save error: {e}"); return False

def db_delete_alert(symbol: str):
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute(); return True
    except: return False

def db_load_alerts() -> dict:
    try:
        res = supabase.table("alerts").select("*").eq("active", True).execute()
        return {r["symbol"]: {"type": r["alert_type"], "price": float(r["price"]), "active": True}
                for r in res.data} if res.data else {}
    except: return {}

def db_save_admin_watchlist(symbols: list):
    try:
        supabase.table("admin_watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        if rows: supabase.table("admin_watchlist").insert(rows).execute()
        st.session_state.admin_watchlist = symbols
        return True
    except Exception as e:
        st.error(f"Admin save error: {e}"); return False

def db_load_admin_watchlist() -> list:
    try:
        res = supabase.table("admin_watchlist").select("symbol").execute()
        return [r["symbol"] for r in res.data] if res.data else []
    except: return []

st.set_page_config(page_title="Arka Trades", layout="wide", page_icon="📈", initial_sidebar_state="collapsed")

BOT_TOKEN = st.secrets.get("BOT_TOKEN", "8720913228:AAEJEpA30KiJ5H0XwIdqxfOA5YSjxW3cfK8")
CHAT_ID   = st.secrets.get("CHAT_ID", "1987688902")

def send_telegram(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"}, timeout=5)
    except: pass

# ════════════════ DESIGN SYSTEM (app interior: professional slate/blue) ═══
DARK   = "#0B0F14"
DARK2  = "#11161D"
DARK3  = "#1A212B"
BORDER = "#242D3A"
IVORY  = "#E8ECF2"
T2     = "#8C97A8"
NAVY   = "#0E141C"

INDIGO = "#3B82F6"
CYAN   = "#06B6D4"
GREEN  = "#22C55E"
RED    = "#EF4444"
AMBER  = "#F59E0B"
PURPLE = "#8B5CF6"
PINK   = "#EC4899"

BLUE   = INDIGO
GOLD   = INDIGO

GRAD_BRAND = f"linear-gradient(135deg,{INDIGO},{CYAN})"
GRAD_AI    = f"linear-gradient(135deg,{PURPLE},{INDIGO})"
GRAD_TEXT  = f"linear-gradient(90deg,{CYAN},{INDIGO},{PURPLE})"

FONT = "'Plus Jakarta Sans','Inter',sans-serif"
MONO = "'JetBrains Mono',monospace"

_ICON_PATHS = {
    "chart":'<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "bell":'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "search":'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "zap":'<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "news":'<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-4 0V11"/><path d="M18 14h-8M15 18h-5M10 6h8v4h-8V6Z"/>',
    "trend":'<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "layers":'<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "shield":'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1 1 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "user":'<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "mail":'<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "brain":'<path d="M12 2a4 4 0 0 0-4 4 4 4 0 0 0-3 6.5A4 4 0 0 0 7 20a4 4 0 0 0 5 1 4 4 0 0 0 5-1 4 4 0 0 0 2-7.5A4 4 0 0 0 16 6a4 4 0 0 0-4-4Z"/><path d="M12 2v19"/>',
    "clock":'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "check":'<polyline points="20 6 9 17 4 12"/>',
    "gauge":'<path d="M12 14l4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
}

def icon(name, size=18, color=None):
    c = color or INDIGO
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{c}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'style="vertical-align:middle;">{_ICON_PATHS.get(name,"")}</svg>')

def icon_box(name, color=None, size=38):
    c = color or INDIGO
    return (f'<div style="width:{size}px;height:{size}px;border-radius:10px;background:{c}1C;'
            f'border:1px solid {c}38;display:flex;align-items:center;justify-content:center;'
            f'margin-bottom:12px;">{icon(name, 19, c)}</div>')

for k, v in {"logged_in":False,"disclaimer_done":False,"show_login":False,"page":"home",
    "profile":{"name":"Trader","email":"","phone":""},"profile_photo":None,"watchlist":[],
    "admin_watchlist":[],"alerts":{},"alert_fired":set(),"db_loaded":False,"is_admin":False}.items():
    if k not in st.session_state: st.session_state[k] = v

if not st.session_state.db_loaded:
    wl = db_load_watchlist()
    if wl: st.session_state.watchlist = wl
    awl = db_load_admin_watchlist()
    if awl: st.session_state.admin_watchlist = awl
    al = db_load_alerts()
    if al: st.session_state.alerts = al
    st.session_state.db_loaded = True

name    = st.session_state.profile.get("name","Trader") or "Trader"
initial = name[0].upper()
IS_ADMIN = st.session_state.get("is_admin", False)

# ── Global CSS ───────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body,.stApp{{background:{DARK} !important;color:{IVORY} !important;font-family:{FONT} !important;}}
header[data-testid="stHeader"]{{display:none !important;}}
[data-testid="stSidebarCollapsedControl"]{{display:none !important;}}
section[data-testid="stSidebar"]{{display:none !important;}}
.block-container{{padding:0 16px !important;max-width:1500px !important;}}
.stTextInput input,.stNumberInput input{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:10px !important;font-family:{FONT} !important;font-size:14px !important;}}
.stTextInput input:focus{{border-color:{INDIGO} !important;box-shadow:0 0 0 3px rgba(59,130,246,0.18) !important;}}
.stTextInput label,.stTextArea label,.stNumberInput label{{color:{T2} !important;font-size:12px !important;font-weight:600 !important;}}
.stTextArea textarea{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:10px !important;}}
[data-testid="stForm"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;border-radius:16px !important;padding:24px !important;}}
[data-testid="metric-container"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;border-radius:12px !important;padding:16px !important;}}
[data-testid="stMetricLabel"] p{{font-size:12px !important;font-weight:600 !important;color:{T2} !important;}}
[data-testid="stMetricValue"]{{font-family:{MONO} !important;font-size:20px !important;color:{IVORY} !important;}}
.stButton>button{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:10px !important;font-family:{FONT} !important;font-weight:600 !important;font-size:14px !important;transition:all .15s ease !important;}}
.stButton>button:hover{{border-color:{INDIGO} !important;color:{INDIGO} !important;transform:translateY(-1px);}}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{{background:{GRAD_BRAND} !important;color:#fff !important;border:none !important;}}
.stButton>button[kind="primary"]:hover{{filter:brightness(1.12);color:#fff !important;}}
.stTabs [data-baseweb="tab-list"]{{background:{DARK2};border:1px solid {BORDER};border-radius:12px;padding:4px;gap:4px;}}
.stTabs [data-baseweb="tab"]{{color:{T2};font-weight:600;border-radius:8px;}}
.stTabs [aria-selected="true"]{{background:{DARK3} !important;color:{INDIGO} !important;}}
.stCheckbox label,.stRadio label{{color:{IVORY} !important;}}
[data-testid="stSelectbox"]>div>div{{background:{DARK3} !important;border:1px solid {BORDER} !important;color:{IVORY} !important;border-radius:10px !important;}}
hr{{border-color:{BORDER} !important;}}
.stProgress>div>div{{background:{GRAD_BRAND} !important;}}
.nav-btn .stButton>button{{width:100% !important;text-align:left !important;background:transparent !important;color:{T2} !important;border:none !important;border-radius:10px !important;font-size:14px !important;font-weight:600 !important;padding:9px 14px !important;margin-bottom:2px !important;}}
.nav-btn .stButton>button:hover{{background:{DARK3} !important;color:{IVORY} !important;transform:none;}}
.nav-btn-active .stButton>button{{background:rgba(59,130,246,0.12) !important;color:{INDIGO} !important;border-left:3px solid {INDIGO} !important;border-radius:0 10px 10px 0 !important;}}
@keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(34,197,94,.4);}}50%{{box-shadow:0 0 0 6px rgba(34,197,94,0);}}}}
.pulse-dot{{width:8px;height:8px;border-radius:50%;background:{GREEN};display:inline-block;animation:pulse 2s infinite;}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(12px);}}to{{opacity:1;transform:none;}}}}
.fade-up{{animation:fadeUp .5s ease both;}}
</style>
""", unsafe_allow_html=True)

# ── Helpers ──────────────────────────────────────────────────
def parse_csv(file):
    try: df = pd.read_csv(file, header=None)
    except: return []
    syms = []
    for v in df.iloc[:,0].astype(str):
        v = v.strip()
        if ':' in v: v = v.split(':')[1]
        v = v.split(',')[0].strip()
        if v and v.lower() != 'nan': syms.append(v.upper())
    return list(dict.fromkeys(syms))

def calc_rsi(close, period=14):
    d = close.diff(); g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, float('nan'))
    v = (100 - 100/(1+rs)).iloc[-1]
    return int(v) if pd.notna(v) else 0

@st.cache_data(ttl=14400, show_spinner=False)
def get_static(sym):
    try:
        h = yf.Ticker(sym+".NS").history(period="30d", interval="1d")
        if len(h) < 16: return None
        prev = h.iloc[-2]
        return {"pdh": float(prev["High"]), "pdl": float(prev["Low"]),
                "prev_close": float(prev["Close"]), "rsi": calc_rsi(h["Close"]),
                "spark": [float(x) for x in h["Close"].tail(12).tolist()]}
    except: return None

@st.cache_data(ttl=10, show_spinner=False)
def get_price(sym):
    try:
        intra = yf.Ticker(sym+".NS").history(period="1d", interval="1m")
        if intra.empty: return None
        cur = float(intra["Close"].iloc[-1])
        daily = yf.Ticker(sym+".NS").history(period="5d", interval="1d")
        if len(daily) < 2: return None
        prev_close = float(daily["Close"].iloc[-2])
        return {"price": cur, "chg": ((cur-prev_close)/prev_close)*100, "prev_close": prev_close}
    except: return None

@st.cache_data(ttl=60, show_spinner=False)
def get_index(sym):
    try:
        h = yf.Ticker(sym).history(period="5d", interval="1d")
        if h.empty or len(h)<2: return None
        cur = float(h["Close"].iloc[-1]); pc = float(h["Close"].iloc[-2])
        return {"price":cur,"chg":((cur-pc)/pc)*100,"pts":cur-pc,
                "spark":[float(x) for x in h["Close"].tolist()]}
    except: return None

def check_alerts(results):
    for s in results:
        sym = s["sym"]
        if sym not in st.session_state.alerts: continue
        a = st.session_state.alerts[sym]
        if not a.get("active"): continue
        if sym in st.session_state.alert_fired: continue
        cur=s["cur"]; ap=a["price"]; at=a["type"]
        fired=False; msg=""
        if at=="pdh" and cur>=ap: fired=True; msg=f"<b>{sym}</b> crossed PDH!\nPrice: Rs{cur:.2f} | PDH: Rs{ap:.2f}"
        elif at=="pdl" and cur<=ap: fired=True; msg=f"<b>{sym}</b> broke PDL!\nPrice: Rs{cur:.2f} | PDL: Rs{ap:.2f}"
        elif at=="custom" and cur>=ap: fired=True; msg=f"<b>{sym}</b> hit target!\nPrice: Rs{cur:.2f} | Target: Rs{ap:.2f}"
        if fired:
            send_telegram(msg)
            st.session_state.alert_fired.add(sym)

def section(title, accent=None):
    a = accent or INDIGO
    st.markdown(f"""<div style="display:flex;align-items:center;gap:14px;margin:36px 0 18px;">
        <div style="width:4px;height:18px;border-radius:2px;background:{a};"></div>
        <div style="font-family:{FONT};font-size:18px;font-weight:800;color:{IVORY};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div></div>""", unsafe_allow_html=True)

def change_pill(chg):
    c, bg = (GREEN, "rgba(34,197,94,.12)") if chg >= 0 else (RED, "rgba(239,68,68,.12)")
    arrow = "▲" if chg >= 0 else "▼"
    return (f'<span style="background:{bg};color:{c};font-family:{MONO};font-size:11px;font-weight:700;'
            f'padding:2px 9px;border-radius:20px;border:1px solid {c}33;">{arrow} {abs(chg):.2f}%</span>')

def sparkline(values, color=None, w=110, h=30):
    if not values or len(values) < 2: return ""
    color = color or (GREEN if values[-1] >= values[0] else RED)
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i/(len(values)-1)*w:.1f},{h-2-((v-lo)/rng)*(h-6):.1f}" for i, v in enumerate(values))
    return (f'<svg width="{w}" height="{h}" style="display:block;margin:0 auto;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')

def checkline(text, c=None):
    return (f'<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:12px;">'
            f'<span style="flex-shrink:0;margin-top:2px;">{icon("check", 16, c or GREEN)}</span>'
            f'<span style="font-size:14px;color:{IVORY};line-height:1.6;">{text}</span></div>')

# ════════════════════════════════════════════════════════════
# LANDING PAGE — green hero theme, seamless on scroll
# ════════════════════════════════════════════════════════════
if not st.session_state.logged_in:

    # Landing palette overrides (safe: landing ends with st.stop())
    DARK="#070b0a"; DARK2="#0d1512"; DARK3="#13201b"; BORDER="#1d2f27"
    IVORY="#e9f5ef"; T2="#8aa79a"
    INDIGO="#5ed29c"; CYAN="#2dd4bf"; GREEN="#34d399"; PURPLE="#7dd3c0"; PINK="#5eead4"
    GRAD_BRAND=f"linear-gradient(135deg,{INDIGO},{CYAN})"
    GRAD_TEXT=f"linear-gradient(90deg,{INDIGO},{CYAN},{PURPLE})"

    if st.query_params.get("login") == "1":
        st.session_state.show_login = True
        st.query_params.clear()

    st.markdown(f"""
    <style>
    .stApp{{background:radial-gradient(ellipse 80% 50% at 50% -10%, rgba(94,210,156,0.07), transparent), #070b0a !important;}}
    .block-container{{padding-top:0 !important;}}
    [data-testid="stVerticalBlock"]{{gap:0.4rem;}}
    iframe{{display:block;border:none;}}
    .stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{{background:#5ed29c !important;color:#070b0a !important;border:none !important;border-radius:9999px !important;font-weight:800 !important;letter-spacing:1px !important;}}
    div[class*="st-key-cta_main"]{{display:flex;justify-content:center;margin-top:-150px;position:relative;z-index:10;}}
    div[class*="st-key-cta_main"] button{{padding:14px 44px !important;text-transform:uppercase !important;font-size:13px !important;}}
    </style>""", unsafe_allow_html=True)

    # ─── LOGIN VIEW ───
    if st.session_state.show_login:
        st.markdown(f"""
        <div style="text-align:center;padding:70px 0 10px;">
            <div style="display:inline-flex;align-items:center;gap:10px;">
                <div style="width:38px;height:38px;border-radius:10px;background:{GRAD_BRAND};display:flex;align-items:center;justify-content:center;">{icon("trend", 19, "#070b0a")}</div>
                <div style="text-align:left;">
                    <div style="font-size:18px;font-weight:800;color:{IVORY};letter-spacing:1px;">ARKA TRADES</div>
                    <div style="font-size:9px;letter-spacing:2px;color:{T2};text-transform:uppercase;">Market Analytics Platform</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)
        _, login_col, _ = st.columns([1, 1.1, 1])
        with login_col:
            with st.form("lf"):
                st.markdown(f"""<div style="margin-bottom:14px;text-align:center;">
                    <div style="font-size:20px;font-weight:800;color:{IVORY};">Member Login</div>
                    <div style="font-size:12px;color:{T2};margin-top:4px;">Sign in to access your terminal</div></div>""", unsafe_allow_html=True)
                u = st.text_input("Username", placeholder="Enter username")
                p = st.text_input("Password", placeholder="Enter password", type="password")
                ok = st.form_submit_button("Sign In", use_container_width=True, type="primary")
                ph = st.empty()
                if ok:
                    if u.strip()=="ADMIN4477MAX" and p.strip()=="MOHIT1":
                        ph.success("Welcome, Admin!"); time.sleep(0.8)
                        st.session_state.logged_in = True; st.session_state.is_admin = True; st.rerun()
                    elif u.strip().lower()=="max trades" and p.strip().lower()=="max":
                        ph.success("Login successful."); time.sleep(0.8)
                        st.session_state.logged_in = True; st.session_state.is_admin = False; st.rerun()
                    else:
                        ph.error("Invalid username or password.")
            if st.button("← Back to home", use_container_width=True, key="back_home"):
                st.session_state.show_login = False; st.rerun()
        st.stop()

    # ─── HERO (video, no CTA inside — real button overlaid below) ───
    hero_html = """
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&family=Plus+Jakarta+Sans:wght@700;800&family=Instrument+Serif:ital@1&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;}html,body{height:100%;}
body{background:#070b0a;font-family:'Inter',sans-serif;overflow:hidden;}
.hero{position:relative;width:100%;height:100%;min-height:700px;overflow:hidden;}
#bgvid{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0.6;}
.ov-left{position:absolute;inset:0;background:linear-gradient(to right,#070b0a 0%,transparent 60%);}
.ov-bottom{position:absolute;inset:0;background:linear-gradient(to top,#070b0a 0%,transparent 50%);}
.gridline{position:absolute;top:0;bottom:0;width:1px;background:rgba(255,255,255,0.1);display:none;}
@media(min-width:768px){.gridline{display:block;}}
.g25{left:25%;}.g50{left:50%;}.g75{left:75%;}
.glow{position:absolute;top:4%;left:50%;transform:translateX(-50%);pointer-events:none;}
nav{position:absolute;top:0;left:0;right:0;z-index:50;display:flex;align-items:center;justify-content:space-between;padding:24px 40px;}
.logo{display:flex;align-items:center;gap:10px;color:#fff;font-weight:800;font-family:'Plus Jakarta Sans';letter-spacing:1px;font-size:15px;}
.logo svg{width:26px;height:26px;}
.menu{display:none;gap:36px;}
@media(min-width:768px){.menu{display:flex;}}
.menu span{color:#fff;font-size:15px;font-weight:600;cursor:default;}
.content{position:relative;z-index:10;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:60px 20px 120px;}
.glass{position:relative;width:300px;height:220px;border-radius:22px;transform:translateY(-25px);background:rgba(255,255,255,0.01);background-blend-mode:luminosity;backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);box-shadow:inset 0 1px 1px rgba(255,255,255,0.1);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:24px;}
.glass::before{content:'';position:absolute;inset:0;border-radius:22px;padding:1.4px;background:linear-gradient(180deg,rgba(255,255,255,0.5),rgba(255,255,255,0.05));-webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none;}
.glass .tag{font-size:17px;color:#5ed29c;font-family:'JetBrains Mono',monospace;letter-spacing:3px;}
.glass h3{font-size:24px;color:#fff;font-weight:700;line-height:1.3;}
.glass h3 em{font-family:'Instrument Serif',serif;font-style:italic;font-weight:400;}
.glass p{font-size:13px;color:rgba(255,255,255,0.65);line-height:1.6;max-width:240px;}
.eyebrow{font-family:'Plus Jakarta Sans';font-weight:700;font-size:11px;letter-spacing:3px;color:#5ed29c;text-transform:uppercase;margin-bottom:18px;}
h1{font-family:'Inter';font-weight:800;text-transform:uppercase;letter-spacing:-0.02em;color:#fff;font-size:38px;line-height:1.05;max-width:900px;}
@media(min-width:768px){h1{font-size:66px;}}
h1 .dot{color:#5ed29c;}
.desc{font-size:14px;color:rgba(255,255,255,0.7);max-width:512px;line-height:1.7;margin:22px 0 0;}
</style></head><body>
<div class="hero">
  <video id="bgvid" autoplay muted loop playsinline></video>
  <div class="ov-left"></div><div class="ov-bottom"></div>
  <div class="gridline g25"></div><div class="gridline g50"></div><div class="gridline g75"></div>
  <svg class="glow" width="900" height="400" viewBox="0 0 900 400">
    <defs><filter id="b25" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="25"/></filter></defs>
    <ellipse cx="450" cy="160" rx="380" ry="90" fill="#0e3b2e" opacity="0.85" filter="url(#b25)"/>
    <ellipse cx="450" cy="160" rx="220" ry="50" fill="#22d3a0" opacity="0.25" filter="url(#b25)"/>
  </svg>
  <nav>
    <div class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>ARKA TRADES</div>
    <div class="menu"><span>SCANNER</span><span>ALERTS</span><span>ARKA AI</span></div>
  </nav>
  <div class="content">
    <div class="glass">
      <div class="tag">[ 2026 ]</div>
      <h3>Built for <em>Serious</em><br>Market Traders</h3>
      <p>AI scanning, instant alerts and chart intelligence in one terminal.</p>
    </div>
    <div class="eyebrow">AI-Powered Market Analytics</div>
    <h1>Launch Your Trading Edge<span class="dot">.</span></h1>
    <p class="desc">Save your trading setups once. Arka's AI analyzes charts, scans the entire NSE universe for matches, and alerts you the moment your conditions trigger.</p>
  </div>
</div>
<script>
const v=document.getElementById('bgvid');
const s='https://stream.mux.com/tLkHO1qZoaaQOUeVWo8hEBeGQfySP02EPS02BmnNFyXys.m3u8';
if(window.Hls&&Hls.isSupported()){const h=new Hls({enableWorker:false});h.loadSource(s);h.attachMedia(v);}
else if(v.canPlayType('application/vnd.apple.mpegurl')){v.src=s;}
</script></body></html>
"""
    components.html(hero_html, height=720, scrolling=False)

    # Real working CTA (native button overlaid into hero bottom)
    if st.button("Get Started →", type="primary", key="cta_main"):
        st.session_state.show_login = True
        st.rerun()

    # ─── Stats Strip ───
    st.markdown("<div style='height:40px;'></div>", unsafe_allow_html=True)
    s1, s2, s3, s4 = st.columns(4)
    for col, num, label, c in [(s1,"2000+","NSE stocks covered",CYAN),(s2,"<90s","Scan time after pre-filter",INDIGO),
        (s3,"10s","Live price refresh",GREEN),(s4,"24/7","AI memory of your setups",PURPLE)]:
        with col:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:12px;padding:20px;text-align:center;">
                <div style="font-family:{MONO};font-size:26px;font-weight:700;color:{c};margin-bottom:4px;">{num}</div>
                <div style="font-size:12px;color:{T2};font-weight:600;">{label}</div></div>""", unsafe_allow_html=True)

    # ─── Feature 1 ───
    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    fa1, fa2 = st.columns([1, 1])
    with fa1:
        st.markdown(f"""<div class="fade-up" style="padding:24px 8px;">{icon_box("brain", PURPLE)}
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{PURPLE};text-transform:uppercase;margin-bottom:10px;">AI Chart Analysis</div>
            <div style="font-size:28px;font-weight:800;color:{IVORY};letter-spacing:-0.5px;line-height:1.25;margin-bottom:16px;">Teach the AI your setups.<br>It never forgets.</div>
            {checkline("Save your personal trading rules, entry conditions and reference charts once")}
            {checkline("Gemini-powered vision analyzes any chart against <strong>your</strong> rules")}
            {checkline("Get a verdict, score and rule-by-rule breakdown in seconds")}
            {checkline("Vector memory stores every setup permanently")}</div>""", unsafe_allow_html=True)
    with fa2:
        st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {PURPLE};border-radius:16px;padding:24px;margin-top:24px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                <span style="font-size:13px;font-weight:700;color:{IVORY};">RELIANCE · Daily</span>
                <span style="background:rgba(52,211,153,.12);color:{GREEN};font-size:11px;font-weight:700;padding:4px 12px;border-radius:20px;border:1px solid {GREEN}33;">VALID · 8/10</span></div>
            <div style="background:{DARK3};border-radius:10px;padding:16px;font-family:{MONO};font-size:12px;color:{T2};line-height:2;">
                <span style="color:{GREEN};">+ Rule matched:</span> Close above PDH on breakout candle<br>
                <span style="color:{GREEN};">+ Rule matched:</span> Volume 1.8x vs 20-day average<br>
                <span style="color:{GREEN};">+ Rule matched:</span> RSI 61 — within momentum zone<br>
                <span style="color:{RED};">- Flagged:</span> Overhead supply at 2,980 level</div>
            <div style="font-size:12px;color:{T2};margin-top:12px;line-height:1.7;">"Structure is clean. Entry valid above 2,941 with stop at 2,896."</div></div>""", unsafe_allow_html=True)

    # ─── Feature 2 ───
    st.markdown("<div style='height:48px;'></div>", unsafe_allow_html=True)
    fb1, fb2 = st.columns([1, 1])
    with fb1:
        st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {GREEN};border-radius:16px;padding:24px;margin-top:24px;">
            <div style="font-size:13px;font-weight:700;color:{IVORY};margin-bottom:14px;">Scan: "Bull Flag + Volume Surge" · Full NSE</div>
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
                <tr style="color:{T2};text-align:left;"><th style="padding:6px 8px;">Symbol</th><th style="padding:6px 8px;">Price</th><th style="padding:6px 8px;">Signal</th><th style="padding:6px 8px;">Score</th></tr>
                <tr><td style="padding:8px;color:{IVORY};font-weight:700;border-top:1px solid {BORDER};">TATAMOTORS</td><td style="padding:8px;font-family:{MONO};color:{IVORY};border-top:1px solid {BORDER};">1,024.50</td><td style="padding:8px;border-top:1px solid {BORDER};"><span style="color:{GREEN};font-weight:700;">STRONG MATCH</span></td><td style="padding:8px;font-family:{MONO};color:{GREEN};border-top:1px solid {BORDER};">9/10</td></tr>
                <tr><td style="padding:8px;color:{IVORY};font-weight:700;border-top:1px solid {BORDER};">CHOLAFIN</td><td style="padding:8px;font-family:{MONO};color:{IVORY};border-top:1px solid {BORDER};">1,388.20</td><td style="padding:8px;border-top:1px solid {BORDER};"><span style="color:{GREEN};font-weight:700;">STRONG MATCH</span></td><td style="padding:8px;font-family:{MONO};color:{GREEN};border-top:1px solid {BORDER};">8/10</td></tr>
                <tr><td style="padding:8px;color:{IVORY};font-weight:700;border-top:1px solid {BORDER};">PERSISTENT</td><td style="padding:8px;font-family:{MONO};color:{IVORY};border-top:1px solid {BORDER};">4,832.00</td><td style="padding:8px;border-top:1px solid {BORDER};"><span style="color:{CYAN};font-weight:700;">PARTIAL</span></td><td style="padding:8px;font-family:{MONO};color:{CYAN};border-top:1px solid {BORDER};">7/10</td></tr>
            </table></div>""", unsafe_allow_html=True)
    with fb2:
        st.markdown(f"""<div class="fade-up" style="padding:24px 8px;">{icon_box("search", GREEN)}
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{GREEN};text-transform:uppercase;margin-bottom:10px;">AI Smart Scanner</div>
            <div style="font-size:28px;font-weight:800;color:{IVORY};letter-spacing:-0.5px;line-height:1.25;margin-bottom:16px;">Your setups, scanned across<br>the entire market.</div>
            {checkline("Describe your setup in plain English — AI extracts the rules")}
            {checkline("Price pre-filter across all ~2000 NSE stocks, then deep scan")}
            {checkline("Gemini Vision compares charts against your reference image")}
            {checkline("Ranked similarity verdicts with entry and risk notes")}</div>""", unsafe_allow_html=True)

    # ─── Trading styles ───
    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{CYAN};text-transform:uppercase;margin-bottom:8px;">Built for your style</div>
        <div style="font-size:28px;font-weight:800;color:{IVORY};">Momentum. Swing. Positional.</div></div>""", unsafe_allow_html=True)
    t1, t2, t3 = st.columns(3)
    for col, ic, ic_c, title, items in [
        (t1,"zap",AMBER,"Momentum Traders",["PDH / PDL breakout detection in real time","10-second live price refresh","Volume spike flags vs 20-day average","Instant Telegram push when levels break"]),
        (t2,"trend",CYAN,"Swing Traders",["Multi-day setup scanning: flags, bases, ranges","RSI and ROC filters across your watchlist","AI pattern matching vs saved reference charts","Daily structure analysis with SMA 20/50"]),
        (t3,"layers",PURPLE,"Positional Traders",["Curated Arka Watchlist maintained by the desk","Today-only news feed per stock","Live index dashboard for market breadth","Cloud-synced watchlists on any device"])]:
        with col:
            checks = "".join(checkline(i, ic_c) for i in items)
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {ic_c};border-radius:14px;padding:26px;min-height:300px;">
                {icon_box(ic, ic_c)}<div style="font-size:16px;font-weight:800;color:{IVORY};margin-bottom:16px;">{title}</div>{checks}</div>""", unsafe_allow_html=True)

    # ─── Roadmap ───
    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{CYAN};text-transform:uppercase;margin-bottom:8px;">Onboarding roadmap</div>
        <div style="font-size:28px;font-weight:800;color:{IVORY};">Live in two weeks.</div></div>""", unsafe_allow_html=True)
    rm1, rm2, rm3 = st.columns(3)
    for col,(day,title,desc,c) in zip([rm1,rm2,rm3],[
        ("DAY 1","Connection & Import","Sign in and upload your TradingView watchlist. Cloud sync is instant.",CYAN),
        ("DAY 7","AI Strategy Training","Teach Arka AI your setups, rules and reference charts.",PURPLE),
        ("DAY 14","Automated Scans Live","Full-universe scans and Telegram alerts on your exact conditions.",GREEN)]):
        with col:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:14px;padding:24px;">
                <div style="font-family:{MONO};font-size:11px;font-weight:700;color:{c};letter-spacing:2px;margin-bottom:10px;">{day}</div>
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:8px;">{title}</div>
                <div style="font-size:13px;color:{T2};line-height:1.7;">{desc}</div></div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="text-align:center;padding:56px 0 40px;">
        <div style="font-size:13px;color:{T2};margin-bottom:6px;">Arka Trades · Finance &amp; Market Education</div>
        <div style="font-size:11px;color:{T2};opacity:.6;">Not SEBI registered. All content is for educational purposes only.
        Trading involves risk — decisions and outcomes are entirely your own.</div></div>""", unsafe_allow_html=True)
    st.stop()

# ════════════════ DISCLAIMER ═════════════════════════════════
if not st.session_state.disclaimer_done:
    _, col, _ = st.columns([1,3,1])
    with col:
        st.markdown(f"""<div style="padding:48px 0 20px;text-align:center;">
            <div style="font-size:30px;font-weight:800;color:{IVORY};">Disclaimer &amp; Terms</div>
            <div style="font-size:13px;color:{T2};margin-top:6px;margin-bottom:24px;">Read all terms carefully before continuing</div></div>
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:16px;padding:28px;font-size:13px;color:{T2};line-height:2;max-height:260px;overflow-y:auto;margin-bottom:20px;">
            <strong style="color:{INDIGO}">1. No Financial Advice</strong><br>Arka Trades does not provide financial or investment advice. Educational only.<br><br>
            <strong style="color:{INDIGO}">2. Not SEBI Registered</strong><br>We are not registered with SEBI as investment advisor or research analyst.<br><br>
            <strong style="color:{INDIGO}">3. Personal Responsibility</strong><br>All trading decisions are yours. You bear full responsibility for profits or losses.<br><br>
            <strong style="color:{INDIGO}">4. Data Accuracy</strong><br>Market data may be delayed. We do not guarantee accuracy of any data shown.<br><br>
            <strong style="color:{INDIGO}">5. Personal Use Only</strong><br>For personal educational use only. Not for commercial distribution.</div>""", unsafe_allow_html=True)
        t1 = st.checkbox("I understand this platform is for educational use only")
        t2 = st.checkbox("I acknowledge Arka Trades is not SEBI registered")
        t3 = st.checkbox("I accept full responsibility for my own trading decisions")
        t4 = st.checkbox("I agree to the Terms and Conditions above")
        all_ok = t1 and t2 and t3 and t4
        c1,c2 = st.columns(2)
        with c1:
            if st.button("Cancel", use_container_width=True):
                st.session_state.logged_in = False; st.rerun()
        with c2:
            if st.button("Accept and Enter", use_container_width=True, type="primary", disabled=not all_ok):
                st.session_state.disclaimer_done = True
                st.toast(f"Welcome back, {name}!"); st.rerun()
        if not all_ok:
            st.caption("Accept all 4 terms above to continue")
    st.stop()

# ════════════════ MAIN APP ═══════════════════════════════════
left, right = st.columns([1, 4])
PAGE_ACCENTS = {"home":INDIGO,"scanner":CYAN,"alerts":AMBER,"news":RED,"analysis":PURPLE,
    "smart_scan":GREEN,"quant":PINK,"heatmap":T2,"autoalert":T2,"profile":INDIGO,
    "settings":INDIGO,"contact":CYAN}

with left:
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:20px 12px 14px;border-bottom:1px solid {BORDER};">
        <div style="width:32px;height:32px;border-radius:8px;background:{GRAD_BRAND};display:flex;align-items:center;justify-content:center;">{icon("trend", 16, "#fff")}</div>
        <div><div style="font-size:15px;font-weight:800;color:{IVORY};line-height:1;">ARKA TRADES</div>
        <div style="font-size:8px;letter-spacing:2px;color:{T2};text-transform:uppercase;margin-top:3px;">Analytics Platform</div></div></div>""", unsafe_allow_html=True)

    photo = st.session_state.get("profile_photo")
    if photo:
        st.image(photo, width=70)
    else:
        st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:14px 12px;">
            <div style="width:40px;height:40px;border-radius:10px;background:{GRAD_AI};display:flex;align-items:center;justify-content:center;font-weight:800;font-size:16px;color:#fff;">{initial}</div>
            <div><div style="font-size:11px;color:{T2};">Signed in as</div>
            <div style="font-weight:800;font-size:14px;color:{IVORY};">{name}</div></div></div>
        <div style="height:1px;background:{BORDER};"></div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='padding:14px 12px 4px;font-size:10px;font-weight:700;letter-spacing:2px;color:{INDIGO};text-transform:uppercase;'>Product Suite</div>", unsafe_allow_html=True)
    pg = st.session_state.page

    def nav_btn(label, key):
        active = pg == key
        css_class = "nav-btn-active" if active else "nav-btn"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        if st.button(label, key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    nav_btn("Dashboard","home"); nav_btn("Scanner","scanner"); nav_btn("Alerts","alerts")
    nav_btn("News Terminal","news"); nav_btn("Arka AI","analysis")
    nav_btn("Smart Screener","smart_scan"); nav_btn("Quant Analysis","quant")
    st.markdown(f"<div style='padding:14px 12px 4px;font-size:10px;font-weight:700;letter-spacing:2px;color:{AMBER};text-transform:uppercase;'>Coming Soon</div>", unsafe_allow_html=True)
    nav_btn("Heatmap","heatmap"); nav_btn("Auto Alerts","autoalert")
    st.markdown(f"<div style='padding:14px 12px 4px;font-size:10px;font-weight:700;letter-spacing:2px;color:{PURPLE};text-transform:uppercase;'>Account</div>", unsafe_allow_html=True)
    nav_btn("Profile","profile"); nav_btn("Settings","settings"); nav_btn("Contact","contact")
    st.markdown("<br>", unsafe_allow_html=True)
    st.divider()
    st.markdown('<div class="nav-btn">', unsafe_allow_html=True)
    if st.button("Sign Out", use_container_width=True):
        for k in ["logged_in","disclaimer_done","show_login"]: st.session_state[k]=False
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    pg = st.session_state.page
    accent = PAGE_ACCENTS.get(pg, INDIGO)
    page_titles = {"home":"Dashboard","scanner":"Watchlist Scanner","alerts":"Alerts Manager",
        "news":"News Terminal","analysis":"Arka AI","smart_scan":"Smart Screener",
        "quant":"Quant Analysis","heatmap":"Heatmap","autoalert":"Auto Alerts",
        "profile":"Profile","settings":"Settings","contact":"Contact"}

    n1, n2 = st.columns([5,1])
    with n1:
        st.markdown(f"""<div style="display:flex;align-items:center;gap:12px;padding:16px 0 10px;">
            <div style="width:5px;height:34px;border-radius:3px;background:{accent};"></div>
            <div><div style="font-size:21px;font-weight:800;color:{IVORY};">{page_titles.get(pg,"Dashboard")}</div>
            <div style="font-size:12px;color:{T2};margin-top:2px;">Arka Trades · Market Analytics Platform</div></div></div>""", unsafe_allow_html=True)
    with n2:
        st.markdown(f"""<div style="display:flex;align-items:center;justify-content:flex-end;height:60px;padding-right:8px;">
            <div style="display:inline-flex;align-items:center;gap:7px;font-weight:700;font-size:11px;letter-spacing:1px;color:{GREEN};border:1px solid rgba(34,197,94,0.35);padding:5px 12px;border-radius:20px;background:rgba(34,197,94,0.08);"><span class="pulse-dot"></span>LIVE</div></div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:1px;background:{BORDER};margin-bottom:12px;'></div>", unsafe_allow_html=True)

    def show_idx(col, label, sym, c):
        d = get_index(sym)
        with col:
            if d:
                cc = GREEN if d["chg"]>=0 else RED
                spark = sparkline(d.get("spark", []), color=cc, w=120, h=26)
                st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:12px;padding:14px;margin:4px 2px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:11px;font-weight:700;color:{T2};">{label}</span>{change_pill(d['chg'])}</div>
                    <div style="font-family:{MONO};font-weight:700;font-size:20px;color:{IVORY};line-height:1;margin-bottom:6px;">{d['price']:,.2f}</div>{spark}</div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:12px;padding:14px;margin:4px 2px;opacity:0.5;">
                    <div style="font-size:11px;font-weight:700;color:{T2};margin-bottom:8px;">{label}</div>
                    <div style="font-family:{MONO};font-size:20px;color:{T2};">--</div>
                    <div style="font-size:11px;color:{T2};margin-top:4px;">No data</div></div>""", unsafe_allow_html=True)

    if pg == "home":
        r1a,r1b,r1c = st.columns(3)
        show_idx(r1a,"NIFTY 50","^NSEI",INDIGO)
        show_idx(r1b,"BANK NIFTY","^NSEBANK",CYAN)
        show_idx(r1c,"SENSEX","^BSESN",AMBER)
        r2a,r2b = st.columns(2)
        show_idx(r2a,"MIDCAP 100","NIFTY_MIDCAP_100.NS",PURPLE)
        show_idx(r2b,"SMALLCAP 100","^CNXSMALLCAP",PINK)
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:12px 0 16px;'></div>", unsafe_allow_html=True)

    st.markdown('<div style="padding:0 8px 80px;">', unsafe_allow_html=True)

    if pg == "home":
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        mkt = now.replace(hour=9,minute=15,second=0,microsecond=0) <= now <= now.replace(hour=15,minute=30,second=0,microsecond=0)
        mkt_color = GREEN if mkt else RED
        mkt_label = "MARKET OPEN" if mkt else "MARKET CLOSED"
        g1,g2,g3 = st.columns([1.2, 1, 1])
        with g1:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-radius:14px;padding:24px;min-height:130px;">
                <div style="display:inline-flex;align-items:center;gap:8px;background:{mkt_color}14;border:1px solid {mkt_color}33;border-radius:20px;padding:5px 14px;margin-bottom:14px;">
                <span style="width:7px;height:7px;border-radius:50%;background:{mkt_color};display:inline-block;"></span>
                <span style="font-size:11px;font-weight:700;letter-spacing:1px;color:{mkt_color};">{mkt_label}</span></div>
                <div style="font-size:13px;color:{T2};">NSE trading hours · 09:15 to 15:30 IST</div>
                <div style="font-family:{MONO};font-size:13px;color:{IVORY};margin-top:6px;">{now.strftime("%d %b %Y · %H:%M:%S IST")}</div></div>""", unsafe_allow_html=True)
        with g2:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-radius:14px;padding:24px;min-height:130px;">
                {icon_box("layers", CYAN, 34)}<div style="font-family:{MONO};font-size:22px;font-weight:700;color:{IVORY};">{len(st.session_state.watchlist)}</div>
                <div style="font-size:12px;color:{T2};">Stocks in your watchlist</div></div>""", unsafe_allow_html=True)
        with g3:
            active_alerts = sum(1 for a in st.session_state.alerts.values() if a.get("active"))
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-radius:14px;padding:24px;min-height:130px;">
                {icon_box("bell", AMBER, 34)}<div style="font-family:{MONO};font-size:22px;font-weight:700;color:{IVORY};">{active_alerts}</div>
                <div style="font-size:12px;color:{T2};">Active price alerts</div></div>""", unsafe_allow_html=True)

        section("Platform Modules", INDIGO)
        w1,w2,w3,w4 = st.columns(4)
        for col,ic,c,title,desc,target in [
            (w1,"brain",PURPLE,"AI Chart Analysis","Arka AI checks any chart against your saved rules and returns a scored verdict.","analysis"),
            (w2,"search",GREEN,"Smart Screener","Scan all NSE stocks with plain-English rules and AI vision matching.","smart_scan"),
            (w3,"gauge",PINK,"Quant Analysis","Upload any chart image. Deep quant breakdown: score, upside, risk.","quant"),
            (w4,"bell",AMBER,"Breakout Alerts","PDH, PDL and custom price alerts delivered to Telegram instantly.","alerts")]:
            with col:
                st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:14px;padding:22px;min-height:195px;margin-bottom:8px;">
                    {icon_box(ic, c)}<div style="font-size:13px;font-weight:800;color:{IVORY};margin-bottom:8px;">{title}</div>
                    <div style="font-size:12px;color:{T2};line-height:1.7;">{desc}</div></div>""", unsafe_allow_html=True)
                if st.button("Open module", key=f"go_{target}", use_container_width=True):
                    st.session_state.page = target; st.rerun()

    elif pg == "scanner":
        if not st.session_state.admin_watchlist:
            awl = db_load_admin_watchlist()
            if awl: st.session_state.admin_watchlist = awl
        if not st.session_state.watchlist:
            wl = db_load_watchlist()
            if wl: st.session_state.watchlist = wl

        def render_scan_results(syms, key_prefix=""):
            sc1,sc2,sc3,sc4 = st.columns([1,1,1,2])
            filt = sc1.selectbox("Show",["All","Above PDH","Below PDL","In Range"], key=f"filt_{key_prefix}")
            l10  = sc2.checkbox("10s Live", key=f"l10_{key_prefix}")
            l60  = sc3.checkbox("60s Auto", key=f"l60_{key_prefix}")
            scanbtn = sc4.button("Run Scan", use_container_width=True, type="primary", key=f"scan_{key_prefix}")
            if scanbtn:
                results,failed = [],[]
                bar = st.progress(0, text="Scanning...")
                for i,sym in enumerate(syms):
                    st_ = get_static(sym); lv = get_price(sym)
                    if st_ and lv:
                        cur=lv["price"]; chg=lv["chg"]
                        cls="g" if cur>st_["pdh"] else "r" if cur<st_["pdl"] else "n"
                        results.append({"sym":sym,"cur":cur,"chg":chg,"pdh":st_["pdh"],"pdl":st_["pdl"],"rsi":st_["rsi"],"cls":cls,"spark":st_.get("spark",[])})
                    else: failed.append(sym)
                    bar.progress((i+1)/len(syms), text=f"Fetching {sym}...")
                bar.empty()
                check_alerts(results)
                st.session_state[f"results_{key_prefix}"] = results
                st.session_state[f"failed_{key_prefix}"] = failed
            results = st.session_state.get(f"results_{key_prefix}", [])
            failed  = st.session_state.get(f"failed_{key_prefix}", [])
            if results:
                filtered=results
                if filt=="Above PDH":  filtered=[r for r in results if r["cls"]=="g"]
                elif filt=="Below PDL":filtered=[r for r in results if r["cls"]=="r"]
                elif filt=="In Range": filtered=[r for r in results if r["cls"]=="n"]
                filtered.sort(key=lambda x:{"g":0,"r":1,"n":2}[x["cls"]])
                g=sum(1 for r in results if r["cls"]=="g"); r=sum(1 for r in results if r["cls"]=="r"); n=sum(1 for r in results if r["cls"]=="n")
                m1,m2,m3,m4=st.columns(4)
                m1.metric("Above PDH",g); m2.metric("Below PDL",r); m3.metric("In Range",n); m4.metric("Total",len(results))
                if failed:
                    with st.expander(f"{len(failed)} skipped"): st.write(", ".join(failed))
                section("Results", CYAN)
                cols7 = st.columns(5)
                for i, s in enumerate(filtered):
                    if s["cls"]=="g":   bd="rgba(34,197,94,0.4)"; top=GREEN
                    elif s["cls"]=="r": bd="rgba(239,68,68,0.4)"; top=RED
                    else:               bd=BORDER; top=BORDER
                    cc = GREEN if s["chg"] >= 0 else RED
                    rc = GREEN if s["rsi"] < 35 else RED if s["rsi"] > 65 else T2
                    ha = s["sym"] in st.session_state.alerts and st.session_state.alerts[s["sym"]].get("active")
                    nd = get_news_dot(s["sym"])
                    dot = f'<span style="color:{AMBER};font-size:9px;margin:0 2px;">&#9679;</span>' if nd else ""
                    bell = icon("bell", 12, AMBER) if ha else ""
                    spark = sparkline(s.get("spark", []), color=cc, w=95, h=24)
                    card = (f'<div style="background:{DARK2};border:1px solid {bd};border-top:2px solid {top};border-radius:12px;padding:12px 8px 10px;text-align:center;margin-bottom:6px;">'
                        f'<div style="display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;">'
                        f'<span style="font-weight:800;font-size:13px;color:{IVORY};white-space:nowrap;">{s["sym"]}</span>{dot}{bell}</div>'
                        f'<div style="margin-bottom:5px;">{change_pill(s["chg"])}</div>'
                        f'<div style="font-family:{MONO};font-weight:700;font-size:14px;color:{IVORY};line-height:1;margin-bottom:5px;">&#8377;{s["cur"]:.2f}</div>'
                        f'{spark}<div style="font-family:{MONO};font-size:11px;font-weight:700;color:{rc};margin-top:4px;">RSI {s["rsi"]}</div></div>')
                    with cols7[i % 5]:
                        st.markdown(card, unsafe_allow_html=True)
                IST = timezone(timedelta(hours=5, minutes=30))
                st.caption(f"Scanned: {datetime.now(IST).strftime('%d %b %Y  %H:%M:%S')}  ·  % vs prev close  ·  Price: 10s cache")
                if l10: time.sleep(10); st.cache_data.clear(); st.rerun()
                elif l60: time.sleep(60); st.cache_data.clear(); st.rerun()

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        tab1, tab2 = st.tabs(["Arka Watchlist", "Your Watchlist"])
        with tab1:
            admin_syms = st.session_state.admin_watchlist
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {CYAN};border-radius:12px;padding:16px 24px;margin:16px 0;">
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:4px;">Arka Watchlist</div>
                <div style="font-size:12px;color:{T2};">{f"{len(admin_syms)} stocks · Curated by the Arka Trades desk" if admin_syms else "No curated watchlist published yet"}</div></div>""", unsafe_allow_html=True)
            if IS_ADMIN:
                uploaded_admin = st.file_uploader("Upload Arka Watchlist", type=["csv","txt"], key="admin_upload")
                if uploaded_admin:
                    syms = parse_csv(uploaded_admin)
                    if not syms: st.error("No symbols found.")
                    elif db_save_admin_watchlist(syms):
                        st.success(f"Arka Watchlist updated — {len(syms)} stocks.")
                        st.session_state.admin_watchlist = syms; st.rerun()
            if not admin_syms:
                st.info("Arka Watchlist not available yet.")
            else:
                render_scan_results(admin_syms, key_prefix="admin")
                _ensure_news_state(); news_panel(admin_syms)
        with tab2:
            your_syms = st.session_state.watchlist
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {GREEN};border-radius:12px;padding:16px 24px;margin:16px 0;">
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:4px;">Your Watchlist</div>
                <div style="font-size:12px;color:{T2};">{f"{len(your_syms)} stocks · Synced to cloud" if your_syms else "No watchlist uploaded yet"}</div></div>""", unsafe_allow_html=True)
            uploaded_yours = st.file_uploader("Upload Your Watchlist (CSV or TXT)", type=["csv","txt"], key="your_upload")
            if uploaded_yours:
                syms = parse_csv(uploaded_yours)
                if not syms: st.error("No symbols found.")
                elif db_save_watchlist(syms):
                    st.success(f"{len(syms)} stocks loaded and saved.")
                    st.session_state.watchlist = syms
            if not your_syms:
                st.info("Upload your TradingView watchlist above to start scanning.")
            else:
                render_scan_results(your_syms, key_prefix="yours")
                _ensure_news_state(); news_panel(your_syms)

    elif pg == "alerts":
        active_alerts = {s: a for s, a in st.session_state.alerts.items() if a.get("active")}
        a1, a2, a3 = st.columns(3)
        a1.metric("Active Alerts", len(active_alerts))
        a2.metric("Triggered Today", len(st.session_state.alert_fired))
        a3.metric("Delivery Channel", "Telegram")
        st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {AMBER};border-radius:12px;padding:14px 20px;margin:16px 0 8px;">
            <div style="font-size:13px;color:{T2};line-height:1.7;">Create conditional alerts on any stock in your watchlists. When the price crosses your level, a notification is pushed to Telegram instantly.</div></div>""", unsafe_allow_html=True)

        def render_alert_rows(watchlist, key_suffix=""):
            st.markdown(f"""<div style="display:grid;grid-template-columns:2fr 1.2fr 1.5fr 1.2fr;gap:8px;padding:10px 16px;font-size:10px;font-weight:700;letter-spacing:1.5px;color:{T2};text-transform:uppercase;border-bottom:1px solid {BORDER};">
                <span>Symbol</span><span>Status</span><span>Condition</span><span>Level</span></div>""", unsafe_allow_html=True)
            for sym in watchlist:
                has_alert = sym in st.session_state.alerts and st.session_state.alerts[sym].get("active", False)
                a = st.session_state.alerts.get(sym, {})
                cond  = a.get("type", "").upper() if has_alert else "—"
                level = f"Rs {a['price']:,.2f}" if has_alert else "—"
                if has_alert:
                    status = (f'<span style="display:inline-flex;align-items:center;gap:6px;background:rgba(245,158,11,.12);color:{AMBER};font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;border:1px solid {AMBER}33;"><span class="pulse-dot" style="background:{AMBER};"></span>ACTIVE</span>')
                else:
                    status = (f'<span style="background:{DARK3};color:{T2};font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;border:1px solid {BORDER};">INACTIVE</span>')
                rc1, rc2 = st.columns([4, 1.4])
                with rc1:
                    st.markdown(f"""<div style="display:grid;grid-template-columns:2fr 1.2fr 1.5fr 1.2fr;gap:8px;align-items:center;background:{DARK2};border:1px solid {BORDER};border-radius:10px;padding:12px 16px;margin-bottom:6px;">
                        <span style="font-weight:800;font-size:13px;color:{IVORY};">{sym}</span><span>{status}</span>
                        <span style="font-family:{MONO};font-size:12px;color:{T2};">{cond}</span>
                        <span style="font-family:{MONO};font-size:12px;color:{IVORY};">{level}</span></div>""", unsafe_allow_html=True)
                with rc2:
                    bA, bB = st.columns(2)
                    with bA:
                        if st.button("Set", key=f"sa_{sym}_{key_suffix}", use_container_width=True):
                            st.session_state[f"open_{sym}_{key_suffix}"] = True; st.rerun()
                    with bB:
                        if has_alert:
                            if st.button("Off", key=f"rm_{sym}_{key_suffix}", use_container_width=True):
                                del st.session_state.alerts[sym]
                                db_delete_alert(sym)
                                if sym in st.session_state.alert_fired:
                                    st.session_state.alert_fired.remove(sym)
                                st.rerun()
                if st.session_state.get(f"open_{sym}_{key_suffix}"):
                    st.markdown(f"""<div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;padding:4px 16px;margin-bottom:8px;">
                        <div style="font-size:12px;font-weight:700;color:{AMBER};padding:8px 0 0;">Configure alert · {sym}</div></div>""", unsafe_allow_html=True)
                    alert_type = st.radio("Condition", ["PDH","PDL","Custom"], key=f"at_{sym}_{key_suffix}", horizontal=True)
                    cp = 0.0
                    if alert_type == "Custom":
                        cp = st.number_input("Trigger price", key=f"cp_{sym}_{key_suffix}", min_value=0.0, step=0.5)
                    bc1, bc2, _ = st.columns([1,1,3])
                    with bc1:
                        if st.button("Cancel", key=f"can_{sym}_{key_suffix}", use_container_width=True):
                            st.session_state[f"open_{sym}_{key_suffix}"] = False; st.rerun()
                    with bc2:
                        if st.button("Confirm", key=f"ok_{sym}_{key_suffix}", type="primary", use_container_width=True):
                            price = None; atype = None
                            if alert_type == "Custom":
                                if cp > 0: price, atype = cp, "custom"
                                else: st.error("Enter a trigger price above 0.")
                            else:
                                st_data = get_static(sym)
                                if st_data:
                                    price = st_data["pdh"] if alert_type=="PDH" else st_data["pdl"]
                                    atype = alert_type.lower()
                                else:
                                    st.error(f"Could not fetch data for {sym}. Try again.")
                            if price is not None:
                                st.session_state.alerts[sym] = {"type": atype, "price": price, "active": True}
                                db_save_alert(sym, atype, price)
                                if sym in st.session_state.alert_fired:
                                    st.session_state.alert_fired.remove(sym)
                                send_telegram(f"Alert set!\n{sym} · {atype.upper()} · Rs{price:.2f}")
                                st.session_state[f"open_{sym}_{key_suffix}"] = False
                                st.success(f"Alert active for {sym} at Rs{price:.2f}")
                                time.sleep(0.6); st.rerun()

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        alert_tab1, alert_tab2 = st.tabs(["Arka Watchlist", "Your Watchlist"])
        with alert_tab1:
            watchlist = st.session_state.get("admin_watchlist", [])
            if not watchlist: st.warning("Arka Watchlist not available yet.")
            else: render_alert_rows(watchlist, key_suffix="admin")
        with alert_tab2:
            watchlist = st.session_state.get("watchlist", [])
            if not watchlist: st.warning("Upload your watchlist in Scanner first.")
            else: render_alert_rows(watchlist, key_suffix="yours")

    elif pg == "news":
        watchlist = st.session_state.get("watchlist", [])
        if not watchlist:
            st.warning("Go to Scanner first and upload your watchlist.")
        else:
            _ensure_news_state(); news_panel(watchlist)

    elif pg in ["analysis","heatmap","autoalert"]:
        if pg == "analysis":
            render_arka_ai()
        else:
            labels = {"heatmap":"Market Heatmap","autoalert":"Auto Smart Alerts"}
            st.markdown(f"""<div style="background:{DARK2};border:1px dashed {BORDER};border-radius:16px;padding:100px 20px;text-align:center;margin:20px 0;">
                <div style="margin-bottom:16px;">{icon("clock", 32, T2)}</div>
                <div style="font-size:26px;font-weight:800;color:{T2};margin-bottom:10px;">{labels.get(pg,'Coming Soon')}</div>
                <div style="font-size:14px;color:{T2};opacity:.6;">This module is under development</div></div>""", unsafe_allow_html=True)

    elif pg == "smart_scan":
        from smart_scan_page import render_smart_scanner
        render_smart_scanner(supabase)

    elif pg == "quant":
        from quant_analysis import render_quant_analysis
        render_quant_analysis()

    elif pg == "profile":
        p1,p2 = st.columns([1,2])
        with p1:
            photo=st.session_state.get("profile_photo")
            if photo:
                st.image(photo,width=120); st.caption(name)
            else:
                st.markdown(f"""<div style="width:96px;height:96px;border-radius:16px;background:{GRAD_AI};display:flex;align-items:center;justify-content:center;font-weight:800;font-size:36px;color:#fff;margin-bottom:12px;">{initial}</div>
                <div style="font-size:20px;font-weight:800;color:{IVORY};">{name}</div>
                <div style="font-size:11px;color:{T2};letter-spacing:1px;text-transform:uppercase;margin-top:4px;">Arka Trades Member</div>""", unsafe_allow_html=True)
        with p2:
            with st.form("pf"):
                a,b=st.columns(2)
                nn=a.text_input("Full Name", value=st.session_state.profile["name"])
                np_=b.text_input("Contact Number", value=st.session_state.profile["phone"])
                ne=st.text_input("Email Address", value=st.session_state.profile["email"])
                ph=st.file_uploader("Upload Profile Photo",type=["jpg","jpeg","png"])
                if st.form_submit_button("Save Profile",use_container_width=True,type="primary"):
                    st.session_state.profile.update({"name":nn,"phone":np_,"email":ne})
                    if ph: st.session_state["profile_photo"]=ph
                    st.success(f"Saved! Welcome, {nn}!"); st.rerun()

    elif pg == "settings":
        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{IVORY};margin:8px 0 10px;'>Appearance</div>", unsafe_allow_html=True)
        t1,t2=st.columns(2)
        with t1:
            st.markdown(f"""<div style="background:{DARK2};border:2px solid {INDIGO};border-radius:14px;padding:20px;text-align:center;">
                <div style="margin-bottom:10px;">{icon("shield", 24, INDIGO)}</div>
                <div style="font-weight:800;font-size:14px;color:{INDIGO};">DARK MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Currently active</div></div>""", unsafe_allow_html=True)
        with t2:
            st.markdown(f"""<div style="background:{DARK3};border:1px solid {BORDER};border-radius:14px;padding:20px;text-align:center;opacity:.6;">
                <div style="margin-bottom:10px;">{icon("clock", 24, T2)}</div>
                <div style="font-weight:800;font-size:14px;color:{T2};">LIGHT MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Coming soon</div></div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{IVORY};margin-bottom:10px;'>Telegram Notifications</div>", unsafe_allow_html=True)
        st.info(f"Bot connected · Chat ID: {CHAT_ID}")
        if st.button("Send Test Notification",use_container_width=True):
            send_telegram("<b>Arka Trades</b>\nTest notification successful.")
            st.success("Test sent to Telegram.")
        st.divider()
        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{IVORY};'>Broker API — Coming Soon</div>", unsafe_allow_html=True)

    elif pg == "contact":
        c1,c2=st.columns([1,1])
        with c1:
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {CYAN};border-radius:14px;padding:28px;">
                <div style="margin-bottom:12px;">{icon("mail", 24, CYAN)}</div>
                <div style="font-weight:800;font-size:13px;letter-spacing:1px;color:{CYAN};text-transform:uppercase;margin-bottom:14px;">Get in Touch</div>
                <div style="font-size:14px;color:{T2};line-height:2;margin-bottom:18px;">Questions, feedback or suggestions?<br>We would love to hear from you.</div>
                <div style="font-family:{MONO};font-size:13px;color:{CYAN};font-weight:700;word-break:break-all;">Mohitdevsinghchib644@gmail.com</div>
                <div style="font-size:12px;color:{T2};margin-top:10px;">Mention ARKA TRADES in subject line.<br>Reply within 24 hours.</div></div>""", unsafe_allow_html=True)
        with c2:
            with st.form("cf"):
                n=st.text_input("Your Name")
                e=st.text_input("Your Email")
                m=st.text_area("Message",height=120)
                if st.form_submit_button("Send Message",use_container_width=True,type="primary"):
                    if n and m: st.success("Please email: Mohitdevsinghchib644@gmail.com")
                    else: st.warning("Fill name and message.")

    st.markdown('</div>', unsafe_allow_html=True)

