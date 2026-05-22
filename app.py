import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import requests
from supabase import create_client, Client
from news_feed import news_panel, get_news_dot, _ensure_news_state

# ── Supabase Config ─────────────────────────────────────────
SUPABASE_URL = "https://vpxagxjgtonynblhddwh.supabase.co"
SUPABASE_KEY = "sb_publishable_J709kk-CNgm4GVkd5jemEg_XZb5wPDA"

@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

# ── Supabase Helpers ─────────────────────────────────────────
def db_save_watchlist(symbols: list):
    """Save watchlist to Supabase — clears old and inserts new."""
    try:
        supabase.table("watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        supabase.table("watchlist").insert(rows).execute()
        return True
    except Exception as e:
        st.error(f"Save error: {e}")
        return False

def db_load_watchlist() -> list:
    """Load watchlist from Supabase."""
    try:
        res = supabase.table("watchlist").select("symbol").execute()
        return [r["symbol"] for r in res.data] if res.data else []
    except:
        return []

def db_save_alert(symbol: str, alert_type: str, price: float):
    """Save or update an alert."""
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
    """Remove an alert."""
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute()
        return True
    except:
        return False

def db_load_alerts() -> dict:
    """Load all active alerts as dict."""
    try:
        res = supabase.table("alerts").select("*").eq("active", True).execute()
        return {r["symbol"]: {"type": r["alert_type"], "price": float(r["price"]), "active": True}
                for r in res.data} if res.data else {}
    except:
        return {}

st.set_page_config(page_title="Arka Trades", layout="wide", page_icon="📈", initial_sidebar_state="collapsed")

# ── Telegram ───────────────────────────────────────────────
BOT_TOKEN = "8720913228:AAEJEpA30KiJ5H0XwIdqxfOA5YSjxW3cfK8"
CHAT_ID   = "1987688902"

def send_telegram(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"}, timeout=5)
    except: pass

# ── Colors ──────────────────────────────────────────────────
NAVY   = "#0A1D4B"
IVORY  = "#F7EBE0"
GOLD   = "#C8A96A"
GREEN  = "#00B37A"
RED    = "#E84545"
DARK   = "#04080F"
DARK2  = "#060D1A"
DARK3  = "#091525"
BORDER = "#0F2040"
T2     = "#8A9AB5"

# ── Session State ────────────────────────────────────────────
for k, v in {
    "logged_in":       False,
    "disclaimer_done": False,
    "page":            "home",
    "profile":         {"name":"Trader","email":"","phone":""},
    "profile_photo":   None,
    "watchlist":       [],
    "alerts":          {},
    "alert_fired":     set(),
    "db_loaded":       False,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# Load from Supabase once per session
if not st.session_state.db_loaded:
    wl = db_load_watchlist()
    if wl:
        st.session_state.watchlist = wl
    al = db_load_alerts()
    if al:
        st.session_state.alerts = al
    st.session_state.db_loaded = True

name    = st.session_state.profile.get("name","Trader") or "Trader"
initial = name[0].upper()

# ── Global CSS ───────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600;700&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body,.stApp{{
    background:{DARK} !important;
    color:{IVORY} !important;
    font-family:'Inter',sans-serif !important;
}}
header[data-testid="stHeader"]{{display:none !important;}}
[data-testid="stSidebarCollapsedControl"]{{display:none !important;}}
section[data-testid="stSidebar"]{{display:none !important;}}
.block-container{{padding:0 !important;max-width:100% !important;}}
.stTextInput input{{
    background:{DARK3} !important;color:{IVORY} !important;
    border:1px solid {BORDER} !important;border-radius:10px !important;
    font-family:'Inter',sans-serif !important;font-size:15px !important;
}}
.stTextInput label{{
    color:{T2} !important;font-size:11px !important;
    font-weight:700 !important;letter-spacing:2px !important;
    text-transform:uppercase !important;
}}
.stTextArea textarea{{
    background:{DARK3} !important;color:{IVORY} !important;
    border:1px solid {BORDER} !important;border-radius:10px !important;
}}
[data-testid="stForm"]{{background:transparent !important;border:none !important;padding:0 !important;}}
[data-testid="metric-container"]{{
    background:{DARK2} !important;border:1px solid {BORDER} !important;
    border-radius:12px !important;padding:16px !important;
}}
[data-testid="stMetricLabel"] p{{font-size:11px !important;letter-spacing:2px !important;text-transform:uppercase !important;color:{T2} !important;}}
[data-testid="stMetricValue"]{{font-family:'JetBrains Mono',monospace !important;font-size:20px !important;color:{IVORY} !important;}}
.stCheckbox label{{color:{IVORY} !important;}}
[data-testid="stSelectbox"]>div>div{{background:{DARK3} !important;border:1px solid {BORDER} !important;color:{IVORY} !important;}}
hr{{border-color:{BORDER} !important;}}
.stProgress>div>div{{background:{GOLD} !important;}}
.stRadio label{{color:{IVORY} !important;}}

/* Nav buttons */
.nav-btn .stButton>button{{
    width:100% !important;
    text-align:left !important;
    background:transparent !important;
    color:{IVORY} !important;
    border:none !important;
    border-radius:10px !important;
    font-family:'Inter',sans-serif !important;
    font-size:14px !important;
    font-weight:600 !important;
    padding:10px 14px !important;
    margin-bottom:2px !important;
    transition:all .15s !important;
}}
.nav-btn .stButton>button:hover{{
    background:{DARK3} !important;
    color:{GOLD} !important;
}}
.nav-btn-active .stButton>button{{
    background:{NAVY} !important;
    color:{GOLD} !important;
    border-left:3px solid {GOLD} !important;
    border-radius:0 10px 10px 0 !important;
}}
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
        return {"pdh":float(h.iloc[-2]["High"]),"pdl":float(h.iloc[-2]["Low"]),"rsi":calc_rsi(h["Close"])}
    except: return None

@st.cache_data(ttl=10, show_spinner=False)
def get_price(sym):
    try:
        h = yf.Ticker(sym+".NS").history(period="2d", interval="1m")
        if h.empty: return None
        cur = float(h["Close"].iloc[-1]); pc = float(h["Close"].iloc[0])
        return {"price":cur,"chg":((cur-pc)/pc)*100}
    except: return None

@st.cache_data(ttl=60, show_spinner=False)
def get_index(sym):
    try:
        h = yf.Ticker(sym).history(period="5d", interval="1d")
        if h.empty or len(h)<2: return None
        cur = float(h["Close"].iloc[-1]); pc = float(h["Close"].iloc[-2])
        return {"price":cur,"chg":((cur-pc)/pc)*100,"pts":cur-pc}
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
    <div style="display:flex;align-items:center;gap:16px;margin:32px 0 18px;">
        <div style="flex:1;height:1px;background:{BORDER};"></div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:17px;
             letter-spacing:5px;color:{GOLD};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════
# LOGIN
# ════════════════════════════════════════════════════
if not st.session_state.logged_in:
    st.markdown(f"""
    <style>.stApp{{background:linear-gradient(135deg,{NAVY} 0%,{DARK} 70%) !important;}}</style>
    <div style="text-align:center;padding:80px 20px 40px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:100px;
             letter-spacing:14px;color:{GOLD};line-height:1;">ARKA TRADES</div>
        <div style="font-family:'Inter',sans-serif;font-weight:900;font-size:14px;
             letter-spacing:8px;color:{IVORY};text-transform:uppercase;margin-top:8px;">
             Finance &nbsp;&middot;&nbsp; Market Education</div>
        <div style="font-size:12px;letter-spacing:4px;color:rgba(200,169,106,0.5);
             text-transform:uppercase;margin-top:32px;">&#8595; Scroll down to login &#8595;</div>
    </div>""", unsafe_allow_html=True)

    _, col, _ = st.columns([1,2,1])
    with col:
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:20px;
             padding:36px;text-align:center;margin-bottom:16px;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:28px;
                 letter-spacing:8px;color:{GOLD};">LOGIN</div>
        </div>""", unsafe_allow_html=True)
        with st.form("lf"):
            u = st.text_input("Username", placeholder="Enter username")
            p = st.text_input("Password", placeholder="Enter password", type="password")
            ok = st.form_submit_button("LOGIN", use_container_width=True, type="primary")
            ph = st.empty()
            if ok:
                if u.strip().lower()=="max trades" and p.strip().lower()=="max":
                    ph.success("Login Successful — Welcome to Arka Trades!")
                    time.sleep(1.2)
                    st.session_state.logged_in = True; st.rerun()
                else:
                    ph.error("Invalid username or password.")
        st.markdown(f"<div style='text-align:center;font-size:11px;color:{T2};margin-top:12px;font-style:italic;'>Not SEBI registered · Educational use only</div>", unsafe_allow_html=True)
    st.stop()

# ════════════════════════════════════════════════════
# DISCLAIMER
# ════════════════════════════════════════════════════
if not st.session_state.disclaimer_done:
    _, col, _ = st.columns([1,3,1])
    with col:
        st.markdown(f"""
        <div style="padding:48px 0 20px;text-align:center;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:36px;
                 letter-spacing:6px;color:{GOLD};">DISCLAIMER &amp; TERMS</div>
            <div style="font-size:11px;letter-spacing:3px;color:{T2};
                 text-transform:uppercase;margin-top:6px;margin-bottom:24px;">
                Read all terms carefully before continuing</div>
        </div>
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:16px;
             padding:28px;font-size:13px;color:{T2};line-height:2;
             max-height:260px;overflow-y:auto;margin-bottom:20px;">
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

# ════════════════════════════════════════════════════
# MAIN LAYOUT: Left Nav | Right Content
# ════════════════════════════════════════════════════
left, right = st.columns([1, 4])

# ── LEFT NAV PANEL ────────────────────────────────────────────
with left:
    # Brand section
    st.markdown(f"""
    <div style="background:{DARK2};border-right:1px solid {BORDER};padding:0;">
        <div style="padding:20px 16px 16px;border-bottom:1px solid {BORDER};text-align:center;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:22px;
                 letter-spacing:5px;color:{GOLD};line-height:1;">ARKA<br>TRADES</div>
            <div style="font-family:'Inter',sans-serif;font-weight:800;
                 font-size:8px;letter-spacing:3px;color:{IVORY};
                 text-transform:uppercase;margin-top:4px;">Finance · Market Education</div>
        </div>
        <div style="padding:14px 16px;border-bottom:1px solid {BORDER};text-align:center;">
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Profile section
    photo = st.session_state.get("profile_photo")
    if photo:
        st.image(photo, width=70)
    else:
        st.markdown(f"""
        <div style="text-align:center;padding:14px 0 8px;">
            <div style="width:64px;height:64px;border-radius:12px;
                 background:linear-gradient(135deg,{NAVY},{GOLD});
                 border:2px solid rgba(200,169,106,0.4);
                 display:flex;align-items:center;justify-content:center;
                 font-family:'Inter',sans-serif;font-weight:900;
                 font-size:24px;color:{DARK};margin:0 auto 8px;">{initial}</div>
            <div style="font-family:'Inter',sans-serif;font-weight:700;
                 font-size:13px;color:{T2};">Welcome back,</div>
            <div style="font-family:'Inter',sans-serif;font-weight:900;
                 font-size:16px;color:{GOLD};line-height:1.2;">{name}</div>
        </div>
        <div style="height:1px;background:{BORDER};margin-bottom:4px;"></div>
        """, unsafe_allow_html=True)

    st.markdown(f"""
        <div style="padding:14px 12px 4px;font-family:'Bebas Neue',sans-serif;
             font-size:13px;letter-spacing:3px;color:{GOLD};">SERVICES</div>
    </div>
    """, unsafe_allow_html=True)

    pg = st.session_state.page

    def nav_btn(label, key, icon=""):
        active = pg == key
        css_class = "nav-btn-active" if active else "nav-btn"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        if st.button(f"{icon}  {label}", key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    nav_btn("Home",      "home",     "🏠")
    nav_btn("Scanner",   "scanner",  "📋")
    nav_btn("Alerts",    "alerts",   "🔔")
    nav_btn("News",      "news",     "📰")

    st.markdown(f"""
    <div style="padding:14px 12px 4px;font-family:'Bebas Neue',sans-serif;
         font-size:13px;letter-spacing:3px;color:{T2};">COMING SOON</div>
    """, unsafe_allow_html=True)
    nav_btn("Analysis",     "analysis", "📊")
    nav_btn("Heatmap",      "heatmap",  "🗺️")
    nav_btn("Auto Alerts",  "autoalert","⚡")

    st.markdown(f"""
    <div style="padding:14px 12px 4px;font-family:'Bebas Neue',sans-serif;
         font-size:13px;letter-spacing:3px;color:{GOLD};">ACCOUNT</div>
    """, unsafe_allow_html=True)
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

    # ── TOP NAVBAR ──────────────────────────────────────────
    n1, n2, n3 = st.columns([3,4,1])
    with n1:
        photo = st.session_state.get("profile_photo")
        if photo:
            c_a,c_b = st.columns([1,3])
            with c_a: st.image(photo, width=60)
            with c_b:
                st.markdown(f"""
                <div style="padding:6px 0;">
                    <div style="font-size:12px;color:{T2};">Welcome back,</div>
                    <div style="font-family:'Inter',sans-serif;font-weight:900;
                         font-size:18px;color:{GOLD};">{name}</div>
                </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:12px;padding:8px 0;">
                <div style="width:60px;height:60px;border-radius:12px;
                     background:linear-gradient(135deg,{NAVY},{GOLD});
                     border:2px solid rgba(200,169,106,0.4);
                     display:flex;align-items:center;justify-content:center;
                     font-family:'Inter',sans-serif;font-weight:900;
                     font-size:22px;color:{DARK};">{initial}</div>
                <div>
                    <div style="font-size:12px;color:{T2};">Welcome back,</div>
                    <div style="font-family:'Inter',sans-serif;font-weight:900;
                         font-size:18px;color:{GOLD};">{name}</div>
                </div>
            </div>""", unsafe_allow_html=True)

    with n2:
        st.markdown(f"""
        <div style="text-align:center;padding:12px 0;
             border-bottom:1px solid {BORDER};margin-bottom:4px;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:36px;
                 letter-spacing:9px;color:{GOLD};line-height:1;">ARKA TRADES</div>
            <div style="font-family:'Inter',sans-serif;font-weight:900;
                 font-size:12px;letter-spacing:4px;color:{IVORY};
                 text-transform:uppercase;margin-top:2px;">Finance &nbsp;&middot;&nbsp; Market Education</div>
        </div>""", unsafe_allow_html=True)

    with n3:
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:flex-end;
             height:78px;padding-right:8px;">
            <div style="font-family:'Inter',sans-serif;font-weight:700;
                 font-size:10px;letter-spacing:2px;color:{GREEN};
                 border:1px solid rgba(0,179,122,0.4);padding:4px 10px;
                 border-radius:20px;">LIVE</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:1px;background:{BORDER};margin-bottom:8px;'></div>", unsafe_allow_html=True)

    # ── INDEX BAR ─────────────────────────────────────────────
    def show_idx(col, label, sym, color):
        d = get_index(sym)
        with col:
            if d:
                cc  = GREEN if d["chg"]>=0 else RED
                ar  = "▲" if d["chg"]>=0 else "▼"
                pts = abs(d["pts"])
                st.markdown(f"""
                <div style="background:{DARK};border:1px solid {BORDER};
                     border-top:3px solid {color};border-radius:12px;
                     padding:14px;margin:4px 2px;">
                    <div style="font-family:'Inter',sans-serif;font-weight:800;
                         font-size:9px;letter-spacing:3px;color:{T2};
                         text-transform:uppercase;margin-bottom:8px;">{label}</div>
                    <div style="font-family:'JetBrains Mono',monospace;
                         font-weight:700;font-size:20px;color:{IVORY};
                         line-height:1;">{d['price']:,.2f}</div>
                    <div style="font-family:'JetBrains Mono',monospace;
                         font-size:12px;font-weight:600;color:{cc};margin-top:5px;">
                         {ar} {pts:,.2f} pts</div>
                    <div style="font-size:11px;color:{cc};margin-top:2px;">
                         {abs(d['chg']):.2f}%</div>
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background:{DARK};border:1px solid {BORDER};
                     border-top:3px solid {color};border-radius:12px;
                     padding:14px;margin:4px 2px;opacity:0.5;">
                    <div style="font-family:'Inter',sans-serif;font-weight:800;
                         font-size:9px;letter-spacing:3px;color:{T2};
                         text-transform:uppercase;margin-bottom:8px;">{label}</div>
                    <div style="font-family:'JetBrains Mono',monospace;
                         font-size:20px;color:{T2};">--</div>
                    <div style="font-size:11px;color:{T2};margin-top:4px;">No data</div>
                </div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:3px;background:linear-gradient(90deg,{NAVY} 50%,{IVORY} 50%);border-radius:2px;margin-bottom:8px;'></div>", unsafe_allow_html=True)
    r1a,r1b = st.columns(2)
    show_idx(r1a,"NIFTY 50",   "^NSEI",    GOLD)
    show_idx(r1b,"BANK NIFTY", "^NSEBANK", GREEN)
    r2a,r2b = st.columns(2)
    show_idx(r2a,"MIDCAP 100",   "NIFTY_MIDCAP_100.NS", "#A78BFA")
    show_idx(r2b,"SMALLCAP 250", "NIFTYSMLCAP250.NS",   "#7B9FFF")
    _,r3c,_ = st.columns([1,2,1])
    show_idx(r3c,"SENSEX","^BSESN","#FF8C42")

    st.markdown(f"<div style='height:1px;background:{BORDER};margin:8px 0 16px;'></div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # PAGES
    # ══════════════════════════════════════════════════════════
    st.markdown(f'<div style="padding:0 8px 80px;">', unsafe_allow_html=True)

    # ── HOME ────────────────────────────────────────────────
    if pg == "home":
        h1,h2 = st.columns(2)
        with h1:
            st.markdown(f"""
            <div style="background:{NAVY};border-radius:16px 0 0 16px;
                 padding:44px 36px;min-height:240px;">
                <div style="font-family:'Bebas Neue',sans-serif;font-size:56px;
                     letter-spacing:6px;color:{GOLD};line-height:1;margin-bottom:10px;">
                     ARKA<br>TRADES</div>
                <div style="font-family:'Inter',sans-serif;font-weight:900;
                     font-size:11px;letter-spacing:5px;color:{IVORY};
                     text-transform:uppercase;">Finance · Market Education</div>
            </div>""", unsafe_allow_html=True)
        with h2:
            st.markdown(f"""
            <div style="background:{IVORY};border-radius:0 16px 16px 0;
                 padding:44px 36px;min-height:240px;">
                <div style="font-family:'Inter',sans-serif;font-weight:700;
                     font-size:15px;color:{NAVY};line-height:1.9;margin-bottom:14px;">
                    Trade smarter with<br><strong>precision-based alerts.</strong><br>
                    Real-time breakout insights and watchlist<br>
                    analysis — built for traders who value<br>
                    <strong>clarity and control.</strong>
                </div>
                <div style="font-size:11px;color:#888;font-style:italic;">
                    Not SEBI registered. Educational use only.</div>
            </div>""", unsafe_allow_html=True)

        section("TODAY AT A GLANCE")
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        mkt = now.replace(hour=9,minute=15,second=0,microsecond=0) <= now <= now.replace(hour=15,minute=30,second=0,microsecond=0)
        g1,g2,g3,g4 = st.columns(4)
        g1.metric("Market Status", "OPEN" if mkt else "CLOSED")
        g2.metric("Date", now.strftime("%d %b %Y"))
        g3.metric("Time", now.strftime("%H:%M:%S"))
        g4.metric("Refresh", "10 Seconds")

        section("WHAT YOU GET")
        w1,w2,w3 = st.columns(3)
        for col,icon,title,color,desc in [
            (w1,"📋","Watchlist Scanner",GOLD,"Upload your TradingView watchlist. Instantly see which stocks moved above or below yesterday's range."),
            (w2,"🔔","Telegram Alerts",GREEN,"Set alerts for PDH, PDL or custom price. Get instant Telegram notifications when your stock hits the level."),
            (w3,"📊","Analysis (Soon)",RED,"Sector heatmaps, top movers, volume analysis and market breadth — coming soon."),
        ]:
            with col:
                st.markdown(f"""
                <div style="background:{DARK2};border:1px solid {BORDER};
                     border-top:3px solid {color};border-radius:16px;
                     padding:24px;min-height:180px;margin-bottom:8px;">
                    <div style="font-size:28px;margin-bottom:12px;">{icon}</div>
                    <div style="font-family:'Inter',sans-serif;font-weight:900;
                         font-size:11px;letter-spacing:2px;color:{color};
                         text-transform:uppercase;margin-bottom:8px;">{title}</div>
                    <div style="font-size:13px;color:{T2};line-height:1.8;">{desc}</div>
                </div>""", unsafe_allow_html=True)

    # ── SCANNER ─────────────────────────────────────────────
    elif pg == "scanner":
        section("WATCHLIST SCANNER")

        # Auto-load from Supabase if not in session
        if not st.session_state.watchlist:
            wl = db_load_watchlist()
            if wl:
                st.session_state.watchlist = wl

        with st.expander("How to export from TradingView"):
            st.write("TradingView → Watchlist → three-dot menu → Export data → save CSV → Upload below")

        uploaded = st.file_uploader("Upload new watchlist CSV (optional)", type=["csv","txt"], label_visibility="collapsed")
        if uploaded:
            syms = parse_csv(uploaded)
            if not syms:
                st.error("No symbols found.")
            else:
                st.session_state.watchlist = syms
                if db_save_watchlist(syms):
                    st.success(f"✅ {len(syms)} stocks loaded and saved to cloud!")

        # Show watchlist if available
        syms = st.session_state.watchlist
        if not syms:
            st.info("Upload your TradingView watchlist CSV to start scanning.")
        else:
            st.success(f"✅ {len(syms)} stocks in your watchlist")
            sc1,sc2,sc3,sc4 = st.columns([1,1,1,2])
            filt    = sc1.selectbox("Show",["All","Above PDH","Below PDL","In Range"])
            l10     = sc2.checkbox("10s Live")
            l60     = sc3.checkbox("60s Auto")
            scanbtn = sc4.button("SCAN NOW", use_container_width=True, type="primary")

            if scanbtn or l10 or l60:
                    results,failed = [],[]
                    bar = st.progress(0, text="Scanning...")
                    for i,sym in enumerate(syms):
                        st_ = get_static(sym); lv = get_price(sym)
                        if st_ and lv:
                            cur=lv["price"]; chg=lv["chg"]
                            cls="g" if cur>st_["pdh"] else "r" if cur<st_["pdl"] else "n"
                            results.append({"sym":sym,"cur":cur,"chg":chg,"pdh":st_["pdh"],"pdl":st_["pdl"],"rsi":st_["rsi"],"cls":cls})
                        else: failed.append(sym)
                        bar.progress((i+1)/len(syms), text=f"Fetching {sym}...")
                    bar.empty()
                    check_alerts(results)

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

                        section("RESULTS")
                        cols5 = st.columns(5)
                        for i,s in enumerate(filtered):
                            if s["cls"]=="g":   bg=f"linear-gradient(160deg,{DARK},{GREEN}15)"; bd=f"rgba(0,179,122,0.35)"; top=GREEN
                            elif s["cls"]=="r": bg=f"linear-gradient(160deg,{DARK},{RED}15)";   bd=f"rgba(232,69,69,0.35)";  top=RED
                            else:               bg=DARK2; bd=BORDER; top=BORDER
                            cc=GREEN if s["chg"]>=0 else RED; arr="▲" if s["chg"]>=0 else "▼"
                            rc=GREEN if s["rsi"]<35 else RED if s["rsi"]>65 else T2
                            ha=s["sym"] in st.session_state.alerts and st.session_state.alerts[s["sym"]].get("active")
                            bell_on=f'<svg width="14" height="14" viewBox="0 0 24 24" fill="{IVORY}"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>'
                            bell_off=f'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{T2}" stroke-width="2"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>'
                            news_dot = get_news_dot(s["sym"])
                            with cols5[i%5]:
                                st.markdown(f"""
                                <div style="background:{bg};border:1px solid {bd};
                                     border-top:3px solid {top};border-radius:14px;
                                     padding:18px 10px;text-align:center;margin-bottom:8px;">
                                    <div style="font-family:'Inter',sans-serif;font-weight:900;
                                         font-size:13px;color:{IVORY};margin-bottom:6px;">
                                         {s['sym']} {news_dot} {bell_on if ha else bell_off}</div>
                                    <div style="font-family:'JetBrains Mono',monospace;
                                         font-weight:700;font-size:16px;color:{IVORY};">
                                         Rs {s['cur']:.2f}</div>
                                    <div style="font-family:'JetBrains Mono',monospace;
                                         font-size:12px;font-weight:600;
                                         color:{cc};margin-top:4px;">{arr} {abs(s['chg']):.2f}%</div>
                                    <div style="font-family:'JetBrains Mono',monospace;
                                         font-size:12px;font-weight:700;
                                         color:{rc};margin-top:6px;">RSI {s['rsi']}</div>
                                    <div style="font-family:'JetBrains Mono',monospace;
                                         font-size:10px;color:{T2};margin-top:4px;">
                                         H {s['pdh']:.1f} | L {s['pdl']:.1f}</div>
                                </div>""", unsafe_allow_html=True)

                        st.caption(f"Scanned: {datetime.now(IST).strftime('%d %b %Y  %H:%M:%S')}  ·  10s cache")
                        if l10: time.sleep(10); st.cache_data.clear(); st.rerun()
                        elif l60: time.sleep(60); st.cache_data.clear(); st.rerun()

        # ── News Panel (auto-refreshes independently)
        if syms:
            _ensure_news_state()
            news_panel(syms)

    # ── ALERTS ──────────────────────────────────────────────
    elif pg == "alerts":
        section("TELEGRAM ALERTS")
        watchlist = st.session_state.get("watchlist",[])
        if not watchlist:
            st.warning("Go to Scanner first and upload your watchlist.")
        else:
            st.markdown(f"""
            <div style="background:{DARK2};border:1px solid {BORDER};border-left:4px solid {GOLD};
                 border-radius:14px;padding:16px 20px;margin-bottom:20px;">
                <div style="font-size:13px;color:{T2};line-height:1.8;">
                    Tap <strong style="color:{IVORY}">Set</strong> next to any stock.
                    White bell = alert ON. Outline bell = alert OFF.
                    You get a Telegram notification when price hits the level.
                </div>
            </div>""", unsafe_allow_html=True)

            COLS=4; rows=[watchlist[i:i+COLS] for i in range(0,len(watchlist),COLS)]
            for row in rows:
                cols=st.columns(COLS)
                for j,sym in enumerate(row):
                    has_alert=sym in st.session_state.alerts and st.session_state.alerts[sym].get("active",False)
                    alert_info=""
                    if has_alert:
                        a=st.session_state.alerts[sym]
                        alert_info=f"{a['type'].upper()}<br>Rs {a['price']:.2f}"
                    bell_on=f'<svg width="26" height="26" viewBox="0 0 24 24" fill="{IVORY}"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>'
                    bell_off=f'<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="{T2}" stroke-width="1.5"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>'
                    card_bd=GOLD if has_alert else BORDER
                    card_bg=f"rgba(200,169,106,0.06)" if has_alert else DARK2
                    with cols[j]:
                        st.markdown(f"""
                        <div style="background:{card_bg};border:1px solid {card_bd};
                             border-radius:14px;padding:16px 12px;text-align:center;margin-bottom:8px;">
                            <div style="font-family:'Inter',sans-serif;font-weight:900;
                                 font-size:13px;color:{IVORY};margin-bottom:8px;">{sym}</div>
                            <div style="display:flex;justify-content:center;margin-bottom:6px;">
                                 {bell_on if has_alert else bell_off}</div>
                            <div style="font-size:11px;color:{GOLD};line-height:1.5;">
                                 {alert_info if alert_info else f"<span style='color:{T2}'>No alert</span>"}</div>
                        </div>""", unsafe_allow_html=True)
                        ba,bb = st.columns(2)
                        with ba:
                            if st.button("Set",key=f"sa_{sym}",use_container_width=True):
                                st.session_state[f"open_{sym}"]=True
                        with bb:
                            if has_alert:
                                if st.button("Off",key=f"rm_{sym}",use_container_width=True):
                                    del st.session_state.alerts[sym]
                                    db_delete_alert(sym)
                                    if sym in st.session_state.alert_fired: st.session_state.alert_fired.remove(sym)
                                    st.rerun()
                        if st.session_state.get(f"open_{sym}"):
                            st_=get_static(sym)
                            alert_type=st.radio("Type",["PDH","PDL","Custom"],key=f"at_{sym}",horizontal=True)
                            cp=0.0
                            if alert_type=="Custom": cp=st.number_input("Price",key=f"cp_{sym}",min_value=0.0,step=0.5)
                            bc1,bc2=st.columns(2)
                            with bc1:
                                if st.button("Cancel",key=f"can_{sym}"):
                                    st.session_state[f"open_{sym}"]=False; st.rerun()
                            with bc2:
                                if st.button("OK",key=f"ok_{sym}",type="primary"):
                                    if st_:
                                        if alert_type=="PDH":   price=st_["pdh"]; atype="pdh"
                                        elif alert_type=="PDL": price=st_["pdl"]; atype="pdl"
                                        else:                   price=cp; atype="custom"
                                        st.session_state.alerts[sym]={"type":atype,"price":price,"active":True}
                                        db_save_alert(sym, atype, price)
                                        if sym in st.session_state.alert_fired: st.session_state.alert_fired.remove(sym)
                                        send_telegram(f"Alert set!\n{sym} · {atype.upper()} · Rs{price:.2f}")
                                        st.session_state[f"open_{sym}"]=False
                                        st.success(f"Alert set for {sym}!"); st.rerun()

    # ── NEWS PAGE ────────────────────────────────────────────
    elif pg == "news":
        section("STOCK NEWS")
        watchlist = st.session_state.get("watchlist", [])
        if not watchlist:
            st.warning("Go to Scanner first and upload your watchlist.")
        else:
            _ensure_news_state()
            news_panel(watchlist)

    # ── ANALYSIS / COMING SOON ──────────────────────────────
    elif pg in ["analysis","heatmap","autoalert"]:
        section("COMING SOON")
        labels = {"analysis":"Analytics Dashboard","heatmap":"Market Heatmap","autoalert":"Auto Smart Alerts"}
        st.markdown(f"""
        <div style="background:{DARK2};border:1px dashed {BORDER};border-radius:20px;
             padding:100px 20px;text-align:center;margin:20px 0;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:36px;
                 letter-spacing:5px;color:{T2};margin-bottom:12px;">{labels.get(pg,'Coming Soon')}</div>
            <div style="font-size:15px;color:{T2};opacity:.6;">This feature is under development</div>
        </div>""", unsafe_allow_html=True)

    # ── PROFILE ─────────────────────────────────────────────
    elif pg == "profile":
        section("MY PROFILE")
        p1,p2 = st.columns([1,2])
        with p1:
            photo=st.session_state.get("profile_photo")
            if photo:
                st.image(photo,width=120); st.caption(name)
            else:
                st.markdown(f"""
                <div style="width:100px;height:100px;border-radius:14px;
                     background:linear-gradient(135deg,{NAVY},{GOLD});
                     border:3px solid rgba(200,169,106,0.4);
                     display:flex;align-items:center;justify-content:center;
                     font-family:'Inter',sans-serif;font-weight:900;
                     font-size:38px;color:{DARK};margin-bottom:12px;">{initial}</div>
                <div style="font-family:'Bebas Neue',sans-serif;font-size:22px;
                     letter-spacing:3px;color:{GOLD};">{name}</div>
                <div style="font-size:11px;color:{T2};letter-spacing:2px;
                     text-transform:uppercase;margin-top:4px;">Arka Trades Member</div>
                """, unsafe_allow_html=True)
        with p2:
            with st.form("pf"):
                a,b=st.columns(2)
                nn=a.text_input("Full Name",      value=st.session_state.profile["name"])
                np=b.text_input("Contact Number", value=st.session_state.profile["phone"])
                ne=st.text_input("Email Address", value=st.session_state.profile["email"])
                ph=st.file_uploader("Upload Profile Photo",type=["jpg","jpeg","png"])
                if st.form_submit_button("Save Profile",use_container_width=True):
                    st.session_state.profile.update({"name":nn,"phone":np,"email":ne})
                    if ph: st.session_state["profile_photo"]=ph
                    st.success(f"Saved! Welcome, {nn}!"); st.rerun()

    # ── SETTINGS ────────────────────────────────────────────
    elif pg == "settings":
        section("SETTINGS")
        st.markdown("#### 🎨 Background Theme")
        t1,t2=st.columns(2)
        with t1:
            st.markdown(f"""
            <div style="background:{DARK2};border:2px solid {GOLD};border-radius:14px;
                 padding:20px;text-align:center;">
                <div style="font-size:28px;margin-bottom:8px;">🌙</div>
                <div style="font-family:'Inter',sans-serif;font-weight:800;
                     font-size:14px;color:{GOLD};">DARK MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Currently Active</div>
            </div>""", unsafe_allow_html=True)
        with t2:
            st.markdown(f"""
            <div style="background:#F7EBE0;border:2px solid #c0b0a0;border-radius:14px;
                 padding:20px;text-align:center;">
                <div style="font-size:28px;margin-bottom:8px;">☀️</div>
                <div style="font-family:'Inter',sans-serif;font-weight:800;
                     font-size:14px;color:{NAVY};">LIGHT MODE</div>
                <div style="font-size:12px;color:#666;margin-top:4px;">Coming Soon</div>
            </div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 🔔 Telegram")
        st.info(f"Bot connected · Chat ID: {CHAT_ID}")
        if st.button("Send Test Notification",use_container_width=True):
            send_telegram(f"✅ <b>Arka Trades</b>\nTest successful!")
            st.success("Test sent to Telegram!")
        st.divider()
        st.markdown("#### 📡 Broker API — Coming Soon")

    # ── CONTACT ─────────────────────────────────────────────
    elif pg == "contact":
        section("CONTACT US")
        c1,c2=st.columns([1,1])
        with c1:
            st.markdown(f"""
            <div style="background:{DARK2};border:1px solid {BORDER};
                 border-left:4px solid {GOLD};border-radius:16px;padding:28px;">
                <div style="font-family:'Inter',sans-serif;font-weight:800;
                     font-size:12px;letter-spacing:2px;color:{GOLD};
                     text-transform:uppercase;margin-bottom:14px;">GET IN TOUCH</div>
                <div style="font-size:14px;color:{T2};line-height:2;margin-bottom:18px;">
                    Questions, feedback or suggestions?<br>We would love to hear from you.
                </div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:13px;
                     color:{GOLD};font-weight:700;word-break:break-all;">
                    Mohitdevsinghchib644@gmail.com</div>
                <div style="font-size:12px;color:{T2};margin-top:10px;">
                    Mention ARKA TRADES in subject line.<br>Reply within 24 hours.</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            with st.form("cf"):
                n=st.text_input("Your Name")
                e=st.text_input("Your Email")
                m=st.text_area("Message",height=120)
                if st.form_submit_button("Send Message",use_container_width=True):
                    if n and m: st.success("Please email: Mohitdevsinghchib644@gmail.com")
                    else: st.warning("Fill name and message.")

    st.markdown('</div>', unsafe_allow_html=True)
