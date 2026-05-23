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

# Define the Indian Standard Time zone globally to prevent any NameError issues
IST = timezone(timedelta(hours=5, minutes=30))

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
        prev = h.iloc[-2]
        return {
            "pdh":        float(prev["High"]),
            "pdl":        float(prev["Low"]),
            "prev_close": float(prev["Close"]),   # ← official prev day close
            "rsi":        calc_rsi(h["Close"])
        }
    except: return None

@st.cache_data(ttl=10, show_spinner=False)
def get_price(sym):
    """
    Current price via 1-minute intraday.
    Percentage is explicitly calculated against official PREVIOUS DAY CLOSE.
    """
    try:
        # Current price — latest 1-min bar
        intra = yf.Ticker(sym+".NS").history(period="1d", interval="1m")
        if intra.empty: return None
        cur = float(intra["Close"].iloc[-1])

        # Previous day close — daily data (reliable official close)
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

# ── DISCLAIMER ───────────────────────────────────────
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
    st.markdown(f"""
    <div style="background:{DARK2};border-right:1px solid {BORDER};padding:0;">
        <div style="padding:20px 16px 16px;border-bottom:1px solid {BORDER};text-align:center;">
            <div style="font-family:'Bebas Neue',sans-serif;font-size:22px;
                 letter-spacing:5px;color:{GOLD};line-height:1;">ARKA<br>TRADES</div>
            <div style="font-family:'Inter',sans-serif;font-weight:800;
                 font-size:8px;letter-spacing:3px;color:{IVORY};
                 text-transform:uppercase;margin-top:4px;">Finance · Market Education</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

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
                cc   = GREEN if d["chg"]>=0 else RED
                ar   = "▲" if d["chg"]>=0 else "▼"
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

        syms = st.session_state.watchlist
        if not syms:
            st.info("Upload your TradingView watchlist CSV to start scanning.")
        else:
            sc1,sc2,sc3,sc4 = st.columns([2,1,1,2])
            filt    = sc1.selectbox("Show",["All","Above PDH","Below PDL","In Range"])
            l10     = sc2.checkbox("10s Live")
            l60     = sc3.checkbox("60s Auto")
            scanbtn = sc4.button("SCAN NOW", use_container_width=True, type="primary")

            if scanbtn or l10 or l60:
                results, failed = [], []
                bar = st.progress(0, text="Scanning...")
                for i, sym in enumerate(syms):
                    st_ = get_static(sym); lv = get_price(sym)
                    if st_ and lv:
                        cur = lv["price"]; chg = lv["chg"]
                        cls = "g" if cur > st_["pdh"] else "r" if cur < st_["pdl"] else "n"
                        results.append({"sym":sym,"cur":cur,"chg":chg,"pdh":st_["pdh"],"pdl":st_["pdl"],"rsi":st_["rsi"],"cls":cls})
                    else: 
                        failed.append(sym)
                    bar.progress((i+1)/len(syms), text=f"Fetching {sym}...")
                bar.empty()
                check_alerts(results)

                if results:
                    filtered = results
                    if filt == "Above PDH":    filtered = [r for r in results if r["cls"]=="g"]
                    elif filt == "Below PDL":  filtered = [r for r in results if r["cls"]=="r"]
                    elif filt == "In Range":   filtered = [r for r in results if r["cls"]=="n"]
                    filtered.sort(key=lambda x: {"g":0,"r":1,"n":2}[x["cls"]])

                    g = sum(1 for r in results if r["cls"]=="g")
                    r = sum(1 for r in results if r["cls"]=="r")
                    n = sum(1 for r in results if r["cls"]=="n")
                    
                    m1,m2,m3,m4 = st.columns(4)
                    m1.metric("Above PDH", g)
                    m2.metric("Below PDL", r)
                    m3.metric("In Range", n)
                    m4.metric("Total", len(results))

                    if failed:
                        with st.expander(f"{len(failed)} skipped"): 
                            st.write(", ".join(failed))

                    section("RESULTS")
                    
                    # ── Modern Grid Card Layout Engine Implementation ──
                    _ensure_news_state()
                    grid_cols = st.columns(4)
                    for idx, s in enumerate(filtered):
                        col_target = grid_cols[idx % 4]
                        
                        # Set proper colors based on the boundary checks
                        card_border = GREEN if s["cls"] == "g" else (RED if s["cls"] == "r" else BORDER)
                        txt_color = GREEN if s["chg"] >= 0 else RED
                        arrow = "▲" if s["chg"] >= 0 else "▼"
                        
                        # Determine dynamic YouTube style bell alert state icon
                        is_alert_active = s["sym"] in st.session_state.alerts
                        if is_alert_active:
                            # Solid White Filled YouTube Bell SVG
                            bell_icon_html = """
                            <svg viewBox="0 0 24 24" style="width:20px;height:20px;fill:#FFFFFF;">
                                <path d="M10 21h4c0 1.1-.9 2-2 2s-2-.9-2-2zm11-2v1H3v1h18v-2zm-2-1.3V11c0-2.87-1.53-5.28-4.2-5.92V4.5c0-1.38-1.12-2.5-2.5-2.5S9.8 3.12 9.8 4.5v.58C7.13 5.72 5.6 8.12 5.6 11v6.7L4 19h16l-1.6-1.3z"/>
                            </svg>
                            """
                        else:
                            # Hollow Transparent YouTube Outlined Bell SVG
                            bell_icon_html = f"""
                            <svg viewBox="0 0 24 24" style="width:20px;height:20px;fill:none;stroke:{T2};stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;">
                                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0"/>
                            </svg>
                            """
                        
                        card_html = f"""
                        <div style="background:{DARK2}; border:1px solid {card_border}; border-radius:16px; padding:20px; margin-bottom:16px; position:relative;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                                <div style="font-family:'Inter',sans-serif; font-weight:800; font-size:14px; color:{IVORY}; letter-spacing:0.5px;">{s['sym']}</div>
                                <div style="cursor:pointer; display:flex; align-items:center;">{bell_icon_html}</div>
                            </div>
                            
                            <div style="font-family:'JetBrains Mono',monospace; font-size:18px; font-weight:700; color:{IVORY}; margin-bottom:4px;">
                                ₹ {s['cur']:.2f}
                            </div>
                            
                            <div style="font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:600; color:{txt_color}; margin-bottom:12px;">
                                {arrow} {abs(s['chg']):.2f}%
                            </div>
                            
                            <div style="font-size:12px; color:{T2}; font-family:'Inter',sans-serif; display:flex; justify-content:space-between;">
                                <span>RSI:</span>
                                <span style="font-weight:700; color:{IVORY};">{s['rsi']}</span>
                            </div>
                        </div>
                        """
                        with col_target:
                            st.markdown(card_html, unsafe_allow_html=True)

    # ── ALERTS ──────────────────────────────────────────────
    elif pg == "alerts":
        section("TELEGRAM ALERTS")
        st.write("Manage active price monitoring configurations.")

    # ── NEWS ────────────────────────────────────────────────
    elif pg == "news":
        section("MARKET NEWS")
        news_panel()

    # ── PROFILE ─────────────────────────────────────────────
    elif pg == "profile":
        section("MY PROFILE")

    # ── SETTINGS ────────────────────────────────────────────
    elif pg == "settings":
        section("SETTINGS")

    # ── CONTACT ─────────────────────────────────────────────
    elif pg == "contact":
        section("GET IN TOUCH")

    st.markdown('</div>', unsafe_allow_html=True)
