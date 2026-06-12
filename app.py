import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import requests
from supabase import create_client, Client
from news_feed import news_panel, get_news_dot, _ensure_news_state
from arka_ai import render_arka_ai

# ── Supabase Config (uses secrets if available, falls back to defaults) ──
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "https://vpxagxjgtonynblhddwh.supabase.co")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "sb_publishable_J709kk-CNgm4GVkd5jemEg_XZb5wPDA")

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

st.set_page_config(page_title="Arka Trades", layout="wide", page_icon="📈", initial_sidebar_state="collapsed")

# ── Telegram ───────────────────────────────────────────────
BOT_TOKEN = st.secrets.get("BOT_TOKEN", "8720913228:AAEJEpA30KiJ5H0XwIdqxfOA5YSjxW3cfK8")
CHAT_ID   = st.secrets.get("CHAT_ID", "1987688902")

def send_telegram(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"}, timeout=5)
    except: pass

# ── ChartX Palette (same variable names — other files stay compatible) ──
DARK   = "#0B0F17"   # page background (soft slate, not pitch black)
DARK2  = "#0F1522"   # card background
DARK3  = "#151D2E"   # input / nested background
BORDER = "#1E293B"   # micro-borders
IVORY  = "#E2E8F0"   # primary text
T2     = "#94A3B8"   # secondary text
NAVY   = "#101A33"   # deep panel
GOLD   = "#4F8DFD"   # brand accent → electric blue
BLUE   = "#4F8DFD"
GREEN  = "#10B981"   # emerald
RED    = "#EF4444"   # crimson
PURPLE = "#8B5CF6"
FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"

# ── Session State ────────────────────────────────────────────
for k, v in {
    "logged_in":       False,
    "disclaimer_done": False,
    "page":            "home",
    "profile":         {"name":"Trader","email":"","phone":""},
    "profile_photo":   None,
    "watchlist":       [],
    "admin_watchlist": [],
    "alerts":          {},
    "alert_fired":     set(),
    "db_loaded":       False,
    "is_admin":        False,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

if not st.session_state.db_loaded:
    wl = db_load_watchlist()
    if wl: st.session_state.watchlist = wl
    awl = db_load_admin_watchlist()
    if awl: st.session_state.admin_watchlist = awl
    al = db_load_alerts()
    if al: st.session_state.alerts = al
    st.session_state.db_loaded = True

name     = st.session_state.profile.get("name","Trader") or "Trader"
initial  = name[0].upper()
IS_ADMIN = st.session_state.get("is_admin", False)

# ── Global CSS (ChartX design system) ───────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body,.stApp{{
    background:{DARK} !important;
    color:{IVORY} !important;
    font-family:{FONT} !important;
}}
header[data-testid="stHeader"]{{display:none !important;}}
[data-testid="stSidebarCollapsedControl"]{{display:none !important;}}
section[data-testid="stSidebar"]{{display:none !important;}}
.block-container{{padding:0 16px !important;max-width:1500px !important;}}

.stTextInput input,.stNumberInput input{{
    background:{DARK3} !important;color:{IVORY} !important;
    border:1px solid {BORDER} !important;border-radius:10px !important;
    font-family:{FONT} !important;font-size:14px !important;
    transition:border-color .15s ease !important;
}}
.stTextInput input:focus{{
    border-color:{BLUE} !important;
    box-shadow:0 0 0 3px rgba(79,141,253,0.15) !important;
}}
.stTextInput label,.stTextArea label,.stNumberInput label{{
    color:{T2} !important;font-size:12px !important;font-weight:600 !important;
}}
.stTextArea textarea{{
    background:{DARK3} !important;color:{IVORY} !important;
    border:1px solid {BORDER} !important;border-radius:10px !important;
}}
[data-testid="stForm"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;
    border-radius:16px !important;padding:24px !important;
    box-shadow:0 1px 3px rgba(0,0,0,.3) !important;}}
[data-testid="metric-container"]{{
    background:{DARK2} !important;border:1px solid {BORDER} !important;
    border-radius:12px !important;padding:16px !important;
    box-shadow:0 1px 3px rgba(0,0,0,.3) !important;
}}
[data-testid="stMetricLabel"] p{{font-size:12px !important;font-weight:600 !important;color:{T2} !important;}}
[data-testid="stMetricValue"]{{font-family:{MONO} !important;font-size:20px !important;color:{IVORY} !important;}}

.stButton>button{{
    background:{DARK3} !important;color:{IVORY} !important;
    border:1px solid {BORDER} !important;border-radius:10px !important;
    font-family:{FONT} !important;font-weight:600 !important;font-size:14px !important;
    transition:all .15s ease !important;box-shadow:none !important;
}}
.stButton>button:hover{{border-color:{BLUE} !important;color:{BLUE} !important;
    transform:translateY(-1px);box-shadow:0 4px 12px rgba(79,141,253,.15) !important;}}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{{
    background:{BLUE} !important;color:#FFFFFF !important;border:none !important;
    box-shadow:0 2px 8px rgba(79,141,253,.35) !important;
}}
.stButton>button[kind="primary"]:hover{{background:#3B7BF0 !important;color:#fff !important;}}

.stTabs [data-baseweb="tab-list"]{{background:{DARK2};border:1px solid {BORDER};
    border-radius:12px;padding:4px;gap:4px;}}
.stTabs [data-baseweb="tab"]{{color:{T2};font-weight:600;border-radius:8px;}}
.stTabs [aria-selected="true"]{{background:{DARK3} !important;color:{BLUE} !important;}}

.stCheckbox label,.stRadio label{{color:{IVORY} !important;}}
[data-testid="stSelectbox"]>div>div{{background:{DARK3} !important;border:1px solid {BORDER} !important;color:{IVORY} !important;border-radius:10px !important;}}
hr{{border-color:{BORDER} !important;}}
.stProgress>div>div{{background:{BLUE} !important;}}

/* Nav buttons */
.nav-btn .stButton>button{{
    width:100% !important;text-align:left !important;
    background:transparent !important;color:{T2} !important;
    border:none !important;border-radius:10px !important;
    font-size:14px !important;font-weight:600 !important;
    padding:9px 14px !important;margin-bottom:2px !important;
}}
.nav-btn .stButton>button:hover{{background:{DARK3} !important;color:{IVORY} !important;transform:none;box-shadow:none !important;}}
.nav-btn-active .stButton>button{{
    background:rgba(79,141,253,0.10) !important;color:{BLUE} !important;
    border-left:3px solid {BLUE} !important;border-radius:0 10px 10px 0 !important;
}}

@keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(16,185,129,.4);}}50%{{box-shadow:0 0 0 6px rgba(16,185,129,0);}}}}
.pulse-dot{{width:8px;height:8px;border-radius:50%;background:{GREEN};display:inline-block;animation:pulse 2s infinite;}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(12px);}}to{{opacity:1;transform:none;}}}}
.fade-up{{animation:fadeUp .5s ease both;}}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────
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
    v  = (100 - 100/(1+rs)).iloc[-1]
    return int(v) if pd.notna(v) else 0

@st.cache_data(ttl=14400, show_spinner=False)
def get_static(sym):
    try:
        h = yf.Ticker(sym+".NS").history(period="30d", interval="1d")
        if len(h) < 16: return None
        prev = h.iloc[-2]
        return {
            "pdh":        float(prev["High"]),
            "pdl":        float(prev["Low"]),
            "prev_close": float(prev["Close"]),
            "rsi":        calc_rsi(h["Close"]),
            "spark":      [float(x) for x in h["Close"].tail(12).tolist()],
        }
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
        chg = ((cur - prev_close) / prev_close) * 100
        return {"price": cur, "chg": chg, "prev_close": prev_close}
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
        if at=="pdh" and cur>=ap:   fired=True; msg=f"🔔 <b>{sym}</b> crossed PDH!\nPrice: Rs{cur:.2f} | PDH: Rs{ap:.2f}"
        elif at=="pdl" and cur<=ap: fired=True; msg=f"🔔 <b>{sym}</b> broke PDL!\nPrice: Rs{cur:.2f} | PDL: Rs{ap:.2f}"
        elif at=="custom" and cur>=ap: fired=True; msg=f"🔔 <b>{sym}</b> hit target!\nPrice: Rs{cur:.2f} | Target: Rs{ap:.2f}"
        if fired:
            send_telegram(msg)
            st.session_state.alert_fired.add(sym)

def section(title):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;margin:36px 0 18px;">
        <div style="font-family:{FONT};font-size:18px;font-weight:800;
             color:{IVORY};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)

def change_pill(chg: float) -> str:
    c, bg = (GREEN, "rgba(16,185,129,.12)") if chg >= 0 else (RED, "rgba(239,68,68,.12)")
    arrow = "▲" if chg >= 0 else "▼"
    return (f'<span style="background:{bg};color:{c};font-family:{MONO};'
            f'font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px;'
            f'border:1px solid {c}33;">{arrow} {abs(chg):.2f}%</span>')

def sparkline(values, color=None, w=110, h=30) -> str:
    if not values or len(values) < 2: return ""
    color = color or (GREEN if values[-1] >= values[0] else RED)
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i/(len(values)-1)*w:.1f},{h-2-((v-lo)/rng)*(h-6):.1f}"
                   for i, v in enumerate(values))
    return (f'<svg width="{w}" height="{h}" style="display:block;margin:0 auto;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')

# ════════════════════════════════════════════════════════
# LOGIN — ChartX-style landing hero
# ════════════════════════════════════════════════════════
if not st.session_state.logged_in:
    st.markdown(f"""
    <div class="fade-up" style="text-align:center;padding:80px 24px 48px;">
        <div style="display:inline-flex;align-items:center;gap:8px;background:{DARK2};
             border:1px solid {BORDER};border-radius:30px;padding:6px 16px;margin-bottom:28px;">
            <span class="pulse-dot"></span>
            <span style="font-size:12px;font-weight:600;color:{T2};">Live NSE market data · Automated scans · AI analysis</span>
        </div>
        <h1 style="font-family:{FONT};font-size:52px;font-weight:800;line-height:1.12;
             color:{IVORY};max-width:860px;margin:0 auto 20px;letter-spacing:-1.5px;">
            Next-Generation<br>
            <span style="background:linear-gradient(90deg,{BLUE},{PURPLE});
                 -webkit-background-clip:text;-webkit-text-fill-color:transparent;">
                 Market Analytics Infrastructure</span>
        </h1>
        <p style="font-size:17px;color:{T2};max-width:580px;margin:0 auto 6px;line-height:1.7;">
            Arka Trades automates breakout scanning, AI chart analysis and instant
            Telegram alerts — built for traders who value clarity and control.
        </p>
        <p style="font-size:12px;color:{T2};opacity:.6;margin-bottom:10px;">
            Not SEBI registered · Educational use only</p>
    </div>""", unsafe_allow_html=True)

    f1, f2, f3 = st.columns(3)
    for col, ic, t, d in [
        (f1,"📋","Watchlist Scanner","Instant PDH/PDL breakout detection across your full watchlist."),
        (f2,"🤖","Arka AI Vision","Gemini-powered chart analysis trained on your personal rules."),
        (f3,"🔔","Telegram Alerts","Conditional price alerts pushed to your phone in real time."),
    ]:
        with col:
            st.markdown(f"""
            <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                 border-radius:14px;padding:22px;margin-bottom:24px;
                 box-shadow:0 1px 3px rgba(0,0,0,.3);">
                <div style="font-size:24px;margin-bottom:10px;">{ic}</div>
                <div style="font-size:14px;font-weight:800;color:{IVORY};margin-bottom:6px;">{t}</div>
                <div style="font-size:13px;color:{T2};line-height:1.7;">{d}</div>
            </div>""", unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.3, 1])
    with col:
        with st.form("lf"):
            st.markdown(f"<div style='font-size:18px;font-weight:800;color:{IVORY};margin-bottom:12px;'>Sign in to your terminal</div>", unsafe_allow_html=True)
            u = st.text_input("Username", placeholder="Enter username")
            p = st.text_input("Password", placeholder="Enter password", type="password")
            ok = st.form_submit_button("Get Started →", use_container_width=True, type="primary")
            ph = st.empty()
            if ok:
                if u.strip()=="ADMIN4477MAX" and p.strip()=="MOHIT1":
                    ph.success("Welcome, Admin!")
                    time.sleep(1.0)
                    st.session_state.logged_in = True
                    st.session_state.is_admin = True
                    st.rerun()
                elif u.strip().lower()=="max trades" and p.strip().lower()=="max":
                    ph.success("Login successful — welcome to Arka Trades!")
                    time.sleep(1.0)
                    st.session_state.logged_in = True
                    st.session_state.is_admin = False
                    st.rerun()
                else:
                    ph.error("Invalid username or password.")
        st.markdown(f"<div style='text-align:center;font-size:11px;color:{T2};margin-top:12px;'>Not SEBI registered · Educational use only</div>", unsafe_allow_html=True)
    st.stop()

# ════════════════════════════════════════════════════════
# DISCLAIMER
# ════════════════════════════════════════════════════════
if not st.session_state.disclaimer_done:
    _, col, _ = st.columns([1,3,1])
    with col:
        st.markdown(f"""
        <div style="padding:48px 0 20px;text-align:center;">
            <div style="font-size:30px;font-weight:800;color:{IVORY};letter-spacing:-0.5px;">Disclaimer &amp; Terms</div>
            <div style="font-size:13px;color:{T2};margin-top:6px;margin-bottom:24px;">
                Read all terms carefully before continuing</div>
        </div>
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:16px;
             padding:28px;font-size:13px;color:{T2};line-height:2;
             max-height:260px;overflow-y:auto;margin-bottom:20px;
             box-shadow:0 1px 3px rgba(0,0,0,.3);">
            <strong style="color:{BLUE}">1. No Financial Advice</strong><br>
            Arka Trades does not provide financial or investment advice. Educational only.<br><br>
            <strong style="color:{BLUE}">2. Not SEBI Registered</strong><br>
            We are not registered with SEBI as investment advisor or research analyst.<br><br>
            <strong style="color:{BLUE}">3. Personal Responsibility</strong><br>
            All trading decisions are yours. You bear full responsibility for profits or losses.<br><br>
            <strong style="color:{BLUE}">4. Data Accuracy</strong><br>
            Market data may be delayed. We do not guarantee accuracy of any data shown.<br><br>
            <strong style="color:{BLUE}">5. Personal Use Only</strong><br>
            For personal educational use only. Not for commercial distribution.
        </div>""", unsafe_allow_html=True)
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
                st.toast(f"Welcome back, {name}!", icon="👋"); st.rerun()
        if not all_ok:
            st.caption("Accept all 4 terms above to continue")
    st.stop()

# ════════════════════════════════════════════════════════
# MAIN LAYOUT
# ════════════════════════════════════════════════════════
left, right = st.columns([1, 4])

# ── LEFT NAV ────────────────────────────────────────────────
with left:
    st.markdown(f"""
    <div style="padding:20px 16px 14px;border-bottom:1px solid {BORDER};text-align:center;">
        <div style="font-family:{FONT};font-size:20px;font-weight:800;
             letter-spacing:1px;color:{IVORY};line-height:1.2;">ARKA<span style="color:{BLUE}">·</span>TRADES</div>
        <div style="font-size:9px;letter-spacing:2px;color:{T2};
             text-transform:uppercase;margin-top:4px;">Market Analytics Platform</div>
    </div>
    """, unsafe_allow_html=True)

    photo = st.session_state.get("profile_photo")
    if photo:
        st.image(photo, width=70)
    else:
        st.markdown(f"""
        <div style="text-align:center;padding:14px 0 8px;">
            <div style="width:56px;height:56px;border-radius:14px;
                 background:linear-gradient(135deg,{BLUE},{PURPLE});
                 display:flex;align-items:center;justify-content:center;
                 font-weight:800;font-size:22px;color:#fff;margin:0 auto 8px;">{initial}</div>
            <div style="font-size:12px;color:{T2};">Welcome back,</div>
            <div style="font-weight:800;font-size:15px;color:{IVORY};">{name}</div>
        </div>
        <div style="height:1px;background:{BORDER};margin-bottom:4px;"></div>
        """, unsafe_allow_html=True)

    st.markdown(f"<div style='padding:14px 12px 4px;font-size:11px;font-weight:700;letter-spacing:2px;color:{T2};text-transform:uppercase;'>Product Suite</div>", unsafe_allow_html=True)

    pg = st.session_state.page

    def nav_btn(label, key, icon=""):
        active = pg == key
        css_class = "nav-btn-active" if active else "nav-btn"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        if st.button(f"{icon}  {label}", key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    nav_btn("Home",      "home",       "🏠")
    nav_btn("Scanner",   "scanner",    "📋")
    nav_btn("Alerts",    "alerts",     "🔔")
    nav_btn("News",      "news",       "📰")
    nav_btn("Arka AI",   "analysis",   "🤖")
    nav_btn("Screener",  "smart_scan", "🔍")

    st.markdown(f"<div style='padding:14px 12px 4px;font-size:11px;font-weight:700;letter-spacing:2px;color:{T2};text-transform:uppercase;'>Coming Soon</div>", unsafe_allow_html=True)
    nav_btn("Heatmap",      "heatmap",  "🗺️")
    nav_btn("Auto Alerts",  "autoalert","⚡")

    st.markdown(f"<div style='padding:14px 12px 4px;font-size:11px;font-weight:700;letter-spacing:2px;color:{T2};text-transform:uppercase;'>Account</div>", unsafe_allow_html=True)
    nav_btn("Profile",    "profile",  "👤")
    nav_btn("Settings",   "settings", "⚙️")
    nav_btn("Contact Us", "contact",  "📬")

    st.markdown("<br>", unsafe_allow_html=True)
    st.divider()
    st.markdown('<div class="nav-btn">', unsafe_allow_html=True)
    if st.button("🚪  Logout", use_container_width=True):
        for k in ["logged_in","disclaimer_done"]: st.session_state[k]=False
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ── RIGHT CONTENT ────────────────────────────────────────────
with right:
    pg = st.session_state.page

    # Top bar
    n1, n2 = st.columns([5,1])
    with n1:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:12px;padding:14px 0 10px;">
            <div style="font-size:22px;font-weight:800;color:{IVORY};letter-spacing:-0.5px;">
                Arka Trades <span style="color:{T2};font-weight:500;font-size:14px;">/ {pg.replace('_',' ').title()}</span>
            </div>
        </div>""", unsafe_allow_html=True)
    with n2:
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:flex-end;height:56px;padding-right:8px;">
            <div style="display:inline-flex;align-items:center;gap:7px;font-weight:700;
                 font-size:11px;letter-spacing:1px;color:{GREEN};
                 border:1px solid rgba(16,185,129,0.35);padding:5px 12px;border-radius:20px;
                 background:rgba(16,185,129,0.08);"><span class="pulse-dot"></span>LIVE</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:1px;background:{BORDER};margin-bottom:12px;'></div>", unsafe_allow_html=True)

    # ── INDEX BAR ─────────────────────────────────────────────
    def show_idx(col, label, sym, color):
        d = get_index(sym)
        with col:
            if d:
                cc  = GREEN if d["chg"]>=0 else RED
                spark = sparkline(d.get("spark", []), color=cc, w=120, h=26)
                st.markdown(f"""
                <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                     border-radius:12px;padding:14px;margin:4px 2px;
                     box-shadow:0 1px 3px rgba(0,0,0,.3);">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <span style="font-size:11px;font-weight:700;color:{T2};">{label}</span>
                        {change_pill(d['chg'])}
                    </div>
                    <div style="font-family:{MONO};font-weight:700;font-size:20px;
                         color:{IVORY};line-height:1;margin-bottom:6px;">{d['price']:,.2f}</div>
                    {spark}
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background:{DARK2};border:1px solid {BORDER};border-radius:12px;
                     padding:14px;margin:4px 2px;opacity:0.5;">
                    <div style="font-size:11px;font-weight:700;color:{T2};margin-bottom:8px;">{label}</div>
                    <div style="font-family:{MONO};font-size:20px;color:{T2};">--</div>
                    <div style="font-size:11px;color:{T2};margin-top:4px;">No data</div>
                </div>""", unsafe_allow_html=True)

    if pg == "home":
        r1a,r1b,r1c = st.columns(3)
        show_idx(r1a,"NIFTY 50",   "^NSEI",    BLUE)
        show_idx(r1b,"BANK NIFTY", "^NSEBANK", GREEN)
        show_idx(r1c,"SENSEX",     "^BSESN",   PURPLE)
        r2a,r2b = st.columns(2)
        show_idx(r2a,"MIDCAP 100",   "NIFTY_MIDCAP_100.NS", "#A78BFA")
        show_idx(r2b,"SMALLCAP 100", "^CNXSMALLCAP", "#7B9FFF")
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:12px 0 16px;'></div>", unsafe_allow_html=True)

    st.markdown('<div style="padding:0 8px 80px;">', unsafe_allow_html=True)

    # ── HOME ────────────────────────────────────────────────
    if pg == "home":
        h1,h2 = st.columns([1.2,1])
        with h1:
            st.markdown(f"""
            <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                 border-radius:16px;padding:40px 36px;min-height:240px;
                 box-shadow:0 1px 3px rgba(0,0,0,.3);">
                <div style="font-size:34px;font-weight:800;color:{IVORY};
                     line-height:1.2;letter-spacing:-1px;margin-bottom:14px;">
                     Trade smarter with<br>
                     <span style="background:linear-gradient(90deg,{BLUE},{PURPLE});
                          -webkit-background-clip:text;-webkit-text-fill-color:transparent;">
                          precision-based alerts.</span></div>
                <div style="font-size:14px;color:{T2};line-height:1.8;">
                    Real-time breakout insights and watchlist analysis —
                    built for traders who value clarity and control.</div>
            </div>""", unsafe_allow_html=True)
        with h2:
            st.markdown(f"""
            <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                 border-top:2px solid {BLUE};border-radius:16px;padding:32px;
                 min-height:240px;box-shadow:0 1px 3px rgba(0,0,0,.3);">
                <div style="font-size:12px;font-weight:700;letter-spacing:2px;
                     color:{BLUE};text-transform:uppercase;margin-bottom:18px;">Platform includes</div>
                <div style="font-size:14px;color:{IVORY};line-height:2.4;">
                    <span style="color:{GREEN};">✓</span> &nbsp;PDH / PDL breakout scanning<br>
                    <span style="color:{GREEN};">✓</span> &nbsp;AI chart analysis with custom rules<br>
                    <span style="color:{GREEN};">✓</span> &nbsp;Instant Telegram alerts<br>
                    <span style="color:{GREEN};">✓</span> &nbsp;Live stock news feed
                </div>
            </div>""", unsafe_allow_html=True)

        section("Today at a Glance")
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        mkt = now.replace(hour=9,minute=15,second=0,microsecond=0) <= now <= now.replace(hour=15,minute=30,second=0,microsecond=0)
        g1,g2,g3,g4 = st.columns(4)
        g1.metric("Market Status", "OPEN" if mkt else "CLOSED")
        g2.metric("Date", now.strftime("%d %b %Y"))
        g3.metric("Time", now.strftime("%H:%M:%S"))
        g4.metric("Refresh", "10 Seconds")

        section("What You Get")
        w1,w2,w3 = st.columns(3)
        for col,icon,title,color,desc in [
            (w1,"📋","Watchlist Scanner",BLUE,"Upload your TradingView watchlist. Instantly see which stocks moved above or below yesterday's range."),
            (w2,"🔔","Telegram Alerts",GREEN,"Set alerts for PDH, PDL or custom price. Get instant Telegram notifications when your stock hits the level."),
            (w3,"📊","Analysis (Soon)",PURPLE,"Sector heatmaps, top movers, volume analysis and market breadth — coming soon."),
        ]:
            with col:
                st.markdown(f"""
                <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                     border-top:2px solid {color};border-radius:14px;
                     padding:24px;min-height:170px;margin-bottom:8px;
                     box-shadow:0 1px 3px rgba(0,0,0,.3);">
                    <div style="font-size:26px;margin-bottom:12px;">{icon}</div>
                    <div style="font-size:13px;font-weight:800;color:{IVORY};margin-bottom:8px;">{title}</div>
                    <div style="font-size:13px;color:{T2};line-height:1.8;">{desc}</div>
                </div>""", unsafe_allow_html=True)

        section("Onboarding Roadmap")
        rm1, rm2, rm3 = st.columns(3)
        for col,(day,title,desc,c) in zip([rm1,rm2,rm3],[
            ("DAY 1","Connection & Import","Log in and upload your TradingView watchlist. Cloud sync via Supabase is instant.",BLUE),
            ("DAY 7","Arka AI Training","Teach the AI your personal setups, rules and reference charts. It remembers forever.",PURPLE),
            ("DAY 14","Live Scans & Alerts","Automated breakout scans and Telegram alerts go live across your full universe.",GREEN),
        ]):
            with col:
                st.markdown(f"""
                <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                     border-top:2px solid {c};border-radius:14px;padding:24px;
                     box-shadow:0 1px 3px rgba(0,0,0,.3);">
                    <div style="font-family:{MONO};font-size:11px;font-weight:700;
                         color:{c};letter-spacing:2px;margin-bottom:10px;">{day}</div>
                    <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:8px;">{title}</div>
                    <div style="font-size:13px;color:{T2};line-height:1.7;">{desc}</div>
                </div>""", unsafe_allow_html=True)

    # ── SCANNER ─────────────────────────────────────────────
    elif pg == "scanner":
        section("Watchlist Scanner")

        if not st.session_state.admin_watchlist:
            awl = db_load_admin_watchlist()
            if awl: st.session_state.admin_watchlist = awl
        if not st.session_state.watchlist:
            wl = db_load_watchlist()
            if wl: st.session_state.watchlist = wl

        def render_scan_results(syms, key_prefix=""):
            sc1,sc2,sc3,sc4 = st.columns([1,1,1,2])
            filt    = sc1.selectbox("Show",["All","Above PDH","Below PDL","In Range"], key=f"filt_{key_prefix}")
            l10     = sc2.checkbox("10s Live", key=f"l10_{key_prefix}")
            l60     = sc3.checkbox("60s Auto", key=f"l60_{key_prefix}")
            scanbtn = sc4.button("SCAN NOW", use_container_width=True, type="primary", key=f"scan_{key_prefix}")

            if scanbtn:
                results,failed = [],[]
                bar = st.progress(0, text="Scanning...")
                for i,sym in enumerate(syms):
                    st_ = get_static(sym); lv = get_price(sym)
                    if st_ and lv:
                        cur=lv["price"]; chg=lv["chg"]
                        cls="g" if cur>st_["pdh"] else "r" if cur<st_["pdl"] else "n"
                        results.append({"sym":sym,"cur":cur,"chg":chg,"pdh":st_["pdh"],"pdl":st_["pdl"],
                                        "rsi":st_["rsi"],"cls":cls,"spark":st_.get("spark",[])})
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

                g=sum(1 for r in results if r["cls"]=="g")
                r=sum(1 for r in results if r["cls"]=="r")
                n=sum(1 for r in results if r["cls"]=="n")
                m1,m2,m3,m4=st.columns(4)
                m1.metric("Above PDH",g); m2.metric("Below PDL",r)
                m3.metric("In Range",n);  m4.metric("Total",len(results))

                if failed:
                    with st.expander(f"{len(failed)} skipped"): st.write(", ".join(failed))

                section("Results")
                cols7 = st.columns(5)
                for i, s in enumerate(filtered):
                    if s["cls"] == "g":
                        bd="rgba(16,185,129,0.4)"; top=GREEN
                    elif s["cls"] == "r":
                        bd="rgba(239,68,68,0.4)"; top=RED
                    else:
                        bd=BORDER; top=BORDER

                    cc  = GREEN if s["chg"] >= 0 else RED
                    rc  = GREEN if s["rsi"] < 35 else RED if s["rsi"] > 65 else T2
                    ha  = s["sym"] in st.session_state.alerts and st.session_state.alerts[s["sym"]].get("active")
                    nd  = get_news_dot(s["sym"])
                    dot = '<span style="color:#F5C518;font-size:9px;margin:0 2px;">&#9679;</span>' if nd else ""
                    bell = "🔔" if ha else ""
                    spark = sparkline(s.get("spark", []), color=cc, w=95, h=24)

                    card = (
                        f'<div style="background:{DARK2};border:1px solid {bd};border-top:2px solid {top};'
                        f'border-radius:12px;padding:12px 8px 10px;text-align:center;margin-bottom:6px;'
                        f'box-shadow:0 1px 3px rgba(0,0,0,.3);">'
                        f'<div style="display:flex;align-items:center;justify-content:center;'
                        f'gap:4px;margin-bottom:4px;">'
                        f'<span style="font-weight:800;font-size:13px;color:{IVORY};white-space:nowrap;">{s["sym"]}</span>'
                        f'{dot}<span style="font-size:11px;">{bell}</span></div>'
                        f'<div style="margin-bottom:5px;">{change_pill(s["chg"])}</div>'
                        f'<div style="font-family:{MONO};font-weight:700;font-size:14px;'
                        f'color:{IVORY};line-height:1;margin-bottom:5px;">&#8377;{s["cur"]:.2f}</div>'
                        f'{spark}'
                        f'<div style="font-family:{MONO};font-size:11px;font-weight:700;'
                        f'color:{rc};margin-top:4px;">RSI {s["rsi"]}</div>'
                        f'</div>'
                    )
                    with cols7[i % 5]:
                        st.markdown(card, unsafe_allow_html=True)

                IST = timezone(timedelta(hours=5, minutes=30))
                st.caption(f"Scanned: {datetime.now(IST).strftime('%d %b %Y  %H:%M:%S')}  ·  % vs prev close  ·  Price: 10s cache")
                if l10: time.sleep(10); st.cache_data.clear(); st.rerun()
                elif l60: time.sleep(60); st.cache_data.clear(); st.rerun()

        tab1, tab2 = st.tabs(["👑 Arka Watchlist", "📋 Your Watchlist"])

        with tab1:
            admin_syms = st.session_state.admin_watchlist
            st.markdown(f"""
            <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {BLUE};
                 border-radius:12px;padding:16px 24px;margin:16px 0;">
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:4px;">👑 Arka Watchlist</div>
                <div style="font-size:12px;color:{T2};">
                    {f"{len(admin_syms)} stocks · Curated by Arka Trades" if admin_syms else "No admin watchlist yet"}
                </div>
            </div>""", unsafe_allow_html=True)

            if IS_ADMIN:
                uploaded_admin = st.file_uploader("Upload Arka Watchlist", type=["csv","txt"], key="admin_upload")
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
                _ensure_news_state()
                news_panel(admin_syms)

        with tab2:
            your_syms = st.session_state.watchlist
            st.markdown(f"""
            <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {GREEN};
                 border-radius:12px;padding:16px 24px;margin:16px 0;">
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:4px;">📋 Your Watchlist</div>
                <div style="font-size:12px;color:{T2};">
                    {f"{len(your_syms)} stocks saved in cloud" if your_syms else "No watchlist uploaded yet"}
                </div>
            </div>""", unsafe_allow_html=True)

            uploaded_yours = st.file_uploader("Upload Your Watchlist (CSV or TXT)", type=["csv","txt"], key="your_upload")
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
                _ensure_news_state()
                news_panel(your_syms)

    # ── ALERTS ──────────────────────────────────────────────
    elif pg == "alerts":
        section("Telegram Alerts")

        def render_alert_cards(watchlist, key_suffix=""):
            st.markdown(f"""
            <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {BLUE};
                 border-radius:12px;padding:16px 20px;margin-bottom:20px;">
                <div style="font-size:13px;color:{T2};line-height:1.8;">
                    Tap <strong style="color:{IVORY}">Set</strong> next to any stock.
                    🔔 = alert ON. You get a Telegram notification when price hits the level.
                </div>
            </div>""", unsafe_allow_html=True)
            COLS=4
            rows=[watchlist[i:i+COLS] for i in range(0,len(watchlist),COLS)]
            for row in rows:
                cols=st.columns(COLS)
                for j,sym in enumerate(row):
                    has_alert=sym in st.session_state.alerts and st.session_state.alerts[sym].get("active",False)
                    alert_info=""
                    if has_alert:
                        a=st.session_state.alerts[sym]
                        alert_info=f"{a['type'].upper()}<br>Rs {a['price']:.2f}"
                    card_bd = "rgba(79,141,253,0.5)" if has_alert else BORDER
                    card_bg = "rgba(79,141,253,0.06)" if has_alert else DARK2
                    bell_ic = "🔔" if has_alert else "🔕"
                    pulse   = '<span class="pulse-dot" style="margin-right:5px;"></span>' if has_alert else ""
                    with cols[j]:
                        st.markdown(f"""
                        <div style="background:{card_bg};border:1px solid {card_bd};
                             border-radius:12px;padding:16px 12px;text-align:center;margin-bottom:8px;
                             box-shadow:0 1px 3px rgba(0,0,0,.3);">
                            <div style="font-weight:800;font-size:13px;color:{IVORY};margin-bottom:8px;">{pulse}{sym}</div>
                            <div style="font-size:22px;margin-bottom:6px;">{bell_ic}</div>
                            <div style="font-size:11px;color:{BLUE};line-height:1.5;">
                                 {alert_info if alert_info else f"<span style='color:{T2}'>No alert</span>"}</div>
                        </div>""", unsafe_allow_html=True)
                        ba,bb = st.columns(2)
                        with ba:
                            if st.button("Set",key=f"sa_{sym}_{key_suffix}",use_container_width=True):
                                st.session_state[f"open_{sym}_{key_suffix}"]=True
                        with bb:
                            if has_alert:
                                if st.button("Off",key=f"rm_{sym}_{key_suffix}",use_container_width=True):
                                    del st.session_state.alerts[sym]
                                    db_delete_alert(sym)
                                    if sym in st.session_state.alert_fired:
                                        st.session_state.alert_fired.remove(sym)
                                    st.rerun()
                        if st.session_state.get(f"open_{sym}_{key_suffix}"):
                            st_=get_static(sym)
                            alert_type=st.radio("Type",["PDH","PDL","Custom"],key=f"at_{sym}_{key_suffix}",horizontal=True)
                            cp=0.0
                            if alert_type=="Custom":
                                cp=st.number_input("Price",key=f"cp_{sym}_{key_suffix}",min_value=0.0,step=0.5)
                            bc1,bc2=st.columns(2)
                            with bc1:
                                if st.button("Cancel",key=f"can_{sym}_{key_suffix}"):
                                    st.session_state[f"open_{sym}_{key_suffix}"]=False
                                    st.rerun()
                            with bc2:
                                if st.button("OK",key=f"ok_{sym}_{key_suffix}",type="primary"):
                                    if st_:
                                        if alert_type=="PDH":   price=st_["pdh"]; atype="pdh"
                                        elif alert_type=="PDL": price=st_["pdl"]; atype="pdl"
                                        else:                   price=cp; atype="custom"
                                        st.session_state.alerts[sym]={"type":atype,"price":price,"active":True}
                                        db_save_alert(sym, atype, price)
                                        if sym in st.session_state.alert_fired:
                                            st.session_state.alert_fired.remove(sym)
                                        send_telegram(f"Alert set!\n{sym} · {atype.upper()} · Rs{price:.2f}")
                                        st.session_state[f"open_{sym}_{key_suffix}"]=False
                                        st.success(f"Alert set for {sym}!")
                                        st.rerun()

        alert_tab1, alert_tab2 = st.tabs(["👑 Arka Watchlist", "📋 Your Watchlist"])

        with alert_tab1:
            watchlist = st.session_state.get("admin_watchlist", [])
            if not watchlist:
                st.warning("Arka Watchlist not available yet.")
            else:
                render_alert_cards(watchlist, key_suffix="admin")

        with alert_tab2:
            watchlist = st.session_state.get("watchlist", [])
            if not watchlist:
                st.warning("Upload your watchlist in Scanner first.")
            else:
                render_alert_cards(watchlist, key_suffix="yours")

    # ── NEWS ────────────────────────────────────────────────
    elif pg == "news":
        section("Stock News Terminal")
        watchlist = st.session_state.get("watchlist", [])
        if not watchlist:
            st.warning("Go to Scanner first and upload your watchlist.")
        else:
            _ensure_news_state()
            news_panel(watchlist)

    # ── ARKA AI / COMING SOON ───────────────────────────────
    elif pg in ["analysis","heatmap","autoalert"]:
        if pg == "analysis":
            render_arka_ai()
        else:
            section("Coming Soon")
            labels = {"heatmap":"Market Heatmap","autoalert":"Auto Smart Alerts"}
            st.markdown(f"""
            <div style="background:{DARK2};border:1px dashed {BORDER};border-radius:16px;
                 padding:100px 20px;text-align:center;margin:20px 0;">
                <div style="font-size:28px;font-weight:800;color:{T2};margin-bottom:12px;">{labels.get(pg,'Coming Soon')}</div>
                <div style="font-size:14px;color:{T2};opacity:.6;">This feature is under development</div>
            </div>""", unsafe_allow_html=True)

    elif pg == "smart_scan":
        from smart_scan_page import render_smart_scanner
        render_smart_scanner(supabase)

    # ── PROFILE ─────────────────────────────────────────────
    elif pg == "profile":
        section("My Profile")
        p1,p2 = st.columns([1,2])
        with p1:
            photo=st.session_state.get("profile_photo")
            if photo:
                st.image(photo,width=120); st.caption(name)
            else:
                st.markdown(f"""
                <div style="width:96px;height:96px;border-radius:16px;
                     background:linear-gradient(135deg,{BLUE},{PURPLE});
                     display:flex;align-items:center;justify-content:center;
                     font-weight:800;font-size:36px;color:#fff;margin-bottom:12px;">{initial}</div>
                <div style="font-size:20px;font-weight:800;color:{IVORY};">{name}</div>
                <div style="font-size:11px;color:{T2};letter-spacing:1px;
                     text-transform:uppercase;margin-top:4px;">Arka Trades Member</div>
                """, unsafe_allow_html=True)
        with p2:
            with st.form("pf"):
                a,b=st.columns(2)
                nn=a.text_input("Full Name",      value=st.session_state.profile["name"])
                np_=b.text_input("Contact Number", value=st.session_state.profile["phone"])
                ne=st.text_input("Email Address", value=st.session_state.profile["email"])
                ph=st.file_uploader("Upload Profile Photo",type=["jpg","jpeg","png"])
                if st.form_submit_button("Save Profile",use_container_width=True,type="primary"):
                    st.session_state.profile.update({"name":nn,"phone":np_,"email":ne})
                    if ph: st.session_state["profile_photo"]=ph
                    st.success(f"Saved! Welcome, {nn}!"); st.rerun()

    # ── SETTINGS ────────────────────────────────────────────
    elif pg == "settings":
        section("Settings")
        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{IVORY};margin-bottom:10px;'>🎨 Theme</div>", unsafe_allow_html=True)
        t1,t2=st.columns(2)
        with t1:
            st.markdown(f"""
            <div style="background:{DARK2};border:2px solid {BLUE};border-radius:14px;
                 padding:20px;text-align:center;">
                <div style="font-size:26px;margin-bottom:8px;">🌙</div>
                <div style="font-weight:800;font-size:14px;color:{BLUE};">DARK MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Currently Active</div>
            </div>""", unsafe_allow_html=True)
        with t2:
            st.markdown(f"""
            <div style="background:{DARK3};border:1px solid {BORDER};border-radius:14px;
                 padding:20px;text-align:center;opacity:.6;">
                <div style="font-size:26px;margin-bottom:8px;">☀️</div>
                <div style="font-weight:800;font-size:14px;color:{T2};">LIGHT MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Coming Soon</div>
            </div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{IVORY};margin-bottom:10px;'>🔔 Telegram</div>", unsafe_allow_html=True)
        st.info(f"Bot connected · Chat ID: {CHAT_ID}")
        if st.button("Send Test Notification",use_container_width=True):
            send_telegram("✅ <b>Arka Trades</b>\nTest successful!")
            st.success("Test sent to Telegram!")
        st.divider()
        st.markdown(f"<div style='font-size:15px;font-weight:800;color:{IVORY};'>📡 Broker API — Coming Soon</div>", unsafe_allow_html=True)

    # ── CONTACT ─────────────────────────────────────────────
    elif pg == "contact":
        section("Contact Us")
        c1,c2=st.columns([1,1])
        with c1:
            st.markdown(f"""
            <div style="background:{DARK2};border:1px solid {BORDER};
                 border-left:3px solid {BLUE};border-radius:14px;padding:28px;
                 box-shadow:0 1px 3px rgba(0,0,0,.3);">
                <div style="font-weight:800;font-size:13px;letter-spacing:1px;color:{BLUE};
                     text-transform:uppercase;margin-bottom:14px;">Get in Touch</div>
                <div style="font-size:14px;color:{T2};line-height:2;margin-bottom:18px;">
                    Questions, feedback or suggestions?<br>We would love to hear from you.
                </div>
                <div style="font-family:{MONO};font-size:13px;
                     color:{BLUE};font-weight:700;word-break:break-all;">
                    Mohitdevsinghchib644@gmail.com</div>
                <div style="font-size:12px;color:{T2};margin-top:10px;">
                    Mention ARKA TRADES in subject line.<br>Reply within 24 hours.</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            with st.form("cf"):
                n=st.text_input("Your Name")
                e=st.text_input("Your Email")
                m=st.text_area("Message",height=120)
                if st.form_submit_button("Send Message",use_container_width=True,type="primary"):
                    if n and m: st.success("Please email: Mohitdevsinghchib644@gmail.com")
                    else: st.warning("Fill name and message.")

    st.markdown('</div>', unsafe_allow_html=True)
