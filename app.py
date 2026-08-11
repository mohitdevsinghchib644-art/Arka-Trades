import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import requests
import re
import json
from pathlib import Path
from supabase import create_client, Client

# CHANGED: data layer now lives in nse_data.py — nsepythonserver
# primary, yfinance automatic fallback on any failure. See that
# file's module docstring for the full reasoning. Same function
# names/shapes as before, so nothing downstream here changed except
# this import line and the two get_index() calls that now pass
# index_label to enable the NSE-first path (see show_idx below).
from nse_data import (
    get_static, get_price, get_index,
    MIDCAP_CANDIDATES, SMALLCAP_CANDIDATES,
    SP500_CANDIDATES, DOWJONES_CANDIDATES, GOLD_CANDIDATES,
)
# CHANGED: news_panel (per-symbol tabs) replaced with news_box (one
# combined persistent feed). get_news_dot kept — still used by
# Scanner result cards for the per-stock amber dot marker.
from news_feed import news_box, get_news_dot, _ensure_news_state
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

# ════════════════ DESIGN SYSTEM — BLOOMBERG TERMINAL PASS ═════════
# CHANGED: full re-skin toward the reference desk photo — true black
# base (not slate), amber/orange as primary accent (Bloomberg's
# signature color) alongside the functional green/red for price
# direction, tighter borders, monospace-forward data display,
# reduced border-radius (terminals are square-cornered, not rounded
# cards), a solid black top bar. Page routing/logic below is
# unchanged from before — this section only touches color tokens and
# global CSS.
DARK   = "#000000"
DARK2  = "#0A0A0A"
DARK3  = "#141414"
BORDER = "#2A2A2A"
IVORY  = "#E8E8E8"
T2     = "#8A8A8A"
NAVY   = "#050505"

AMBER  = "#FF9500"   # primary Bloomberg accent — replaces INDIGO as the dominant brand color
INDIGO = "#5B8FD9"   # demoted to a secondary/info accent, kept for continuity in existing calls
CYAN   = "#4DD0E1"
GREEN  = "#00C853"
RED    = "#FF3B30"
PURPLE = "#B388FF"
PINK   = "#FF6FA5"

BLUE   = INDIGO
GOLD   = AMBER

GRAD_BRAND = f"linear-gradient(135deg,{AMBER},#FFB347)"
GRAD_AI    = f"linear-gradient(135deg,{PURPLE},{INDIGO})"
GRAD_TEXT  = f"linear-gradient(90deg,{AMBER},{CYAN},{PURPLE})"

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
    c = color or AMBER
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{c}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'style="vertical-align:middle;">{_ICON_PATHS.get(name,"")}</svg>')

def icon_box(name, color=None, size=38):
    c = color or AMBER
    # CHANGED: border-radius reduced 10px -> 4px across icon_box and
    # most cards below — terminal panels are square, not rounded.
    return (f'<div style="width:{size}px;height:{size}px;border-radius:4px;background:{c}1C;'
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

# ── Global CSS — Bloomberg terminal pass ────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body,.stApp{{background:{DARK} !important;color:{IVORY} !important;font-family:{FONT} !important;}}
header[data-testid="stHeader"]{{display:none !important;}}
[data-testid="stSidebarCollapsedControl"]{{display:none !important;}}
section[data-testid="stSidebar"]{{display:none !important;}}
/* CHANGED: right margin reserved for the persistent news box column
   built into the page layout below — content no longer runs full-
   width on the interior pages so the news panel has a fixed home. */
.block-container{{padding:0 16px !important;max-width:1600px !important;}}
.stTextInput input,.stNumberInput input{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:2px !important;font-family:{MONO} !important;font-size:13px !important;}}
.stTextInput input:focus{{border-color:{AMBER} !important;box-shadow:0 0 0 2px rgba(255,149,0,0.18) !important;}}
.stTextInput label,.stTextArea label,.stNumberInput label{{color:{T2} !important;font-size:11px !important;font-weight:600 !important;text-transform:uppercase !important;letter-spacing:0.5px !important;}}
.stTextArea textarea{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:2px !important;}}
[data-testid="stForm"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;border-radius:4px !important;padding:24px !important;}}
[data-testid="metric-container"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;border-radius:2px !important;padding:14px !important;}}
[data-testid="stMetricLabel"] p{{font-size:10px !important;font-weight:700 !important;color:{T2} !important;text-transform:uppercase !important;letter-spacing:0.5px !important;}}
[data-testid="stMetricValue"]{{font-family:{MONO} !important;font-size:19px !important;color:{IVORY} !important;}}
.stButton>button{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:2px !important;font-family:{FONT} !important;font-weight:600 !important;font-size:13px !important;transition:all .12s ease !important;}}
.stButton>button:hover{{border-color:{AMBER} !important;color:{AMBER} !important;}}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{{background:{AMBER} !important;color:#000 !important;border:none !important;font-weight:800 !important;}}
.stButton>button[kind="primary"]:hover{{filter:brightness(1.1);color:#000 !important;}}
.stTabs [data-baseweb="tab-list"]{{background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:3px;gap:3px;}}
.stTabs [data-baseweb="tab"]{{color:{T2};font-weight:700;border-radius:2px;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;}}
.stTabs [aria-selected="true"]{{background:{DARK3} !important;color:{AMBER} !important;}}
.stCheckbox label,.stRadio label{{color:{IVORY} !important;}}
[data-testid="stSelectbox"]>div>div{{background:{DARK3} !important;border:1px solid {BORDER} !important;color:{IVORY} !important;border-radius:2px !important;}}
hr{{border-color:{BORDER} !important;}}
.stProgress>div>div{{background:{AMBER} !important;}}
.nav-btn .stButton>button{{width:100% !important;text-align:left !important;background:transparent !important;color:{T2} !important;border:none !important;border-radius:0 !important;font-size:13px !important;font-weight:600 !important;padding:8px 14px !important;margin-bottom:1px !important;}}
.nav-btn .stButton>button:hover{{background:{DARK3} !important;color:{IVORY} !important;}}
.nav-btn-active .stButton>button{{background:rgba(255,149,0,0.12) !important;color:{AMBER} !important;border-left:3px solid {AMBER} !important;border-radius:0 !important;}}
@keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(0,200,83,.4);}}50%{{box-shadow:0 0 0 6px rgba(0,200,83,0);}}}}
.pulse-dot{{width:8px;height:8px;border-radius:50%;background:{GREEN};display:inline-block;animation:pulse 2s infinite;}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(8px);}}to{{opacity:1;transform:none;}}}}
.fade-up{{animation:fadeUp .35s ease both;}}
/* CHANGED: sticky bottom-left news box wrapper — fixed positioning,
   persistent across all interior pages regardless of nav selection.
   Width tuned to sit comfortably alongside the left nav rail without
   overlapping main content on typical desktop viewports. */
#arka-news-fixed{{position:fixed;bottom:0;left:0;width:340px;height:280px;
    z-index:999;padding:10px;background:{DARK};border-top:1px solid {BORDER};
    border-right:1px solid {BORDER};}}
#arka-news-scroll::-webkit-scrollbar{{width:4px;}}
#arka-news-scroll::-webkit-scrollbar-thumb{{background:{BORDER};border-radius:2px;}}
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
    a = accent or AMBER
    st.markdown(f"""<div style="display:flex;align-items:center;gap:14px;margin:30px 0 14px;">
        <div style="width:4px;height:16px;border-radius:0;background:{a};"></div>
        <div style="font-family:{FONT};font-size:15px;font-weight:800;color:{IVORY};white-space:nowrap;text-transform:uppercase;letter-spacing:0.5px;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div></div>""", unsafe_allow_html=True)

def change_pill(chg):
    c, bg = (GREEN, "rgba(0,200,83,.12)") if chg >= 0 else (RED, "rgba(255,59,48,.12)")
    arrow = "▲" if chg >= 0 else "▼"
    return (f'<span style="background:{bg};color:{c};font-family:{MONO};font-size:11px;font-weight:700;'
            f'padding:2px 8px;border-radius:0;border:1px solid {c}33;">{arrow} {abs(chg):.2f}%</span>')

def sparkline(values, color=None, w=110, h=30):
    if not values or len(values) < 2: return ""
    color = color or (GREEN if values[-1] >= values[0] else RED)
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i/(len(values)-1)*w:.1f},{h-2-((v-lo)/rng)*(h-6):.1f}" for i, v in enumerate(values))
    return (f'<svg width="{w}" height="{h}" style="display:block;margin:0 auto;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')

def checkline(text, c=None):
    return (f'<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:12px;">'
            f'<span style="flex-shrink:0;margin-top:2px;">{icon("check", 16, c or GREEN)}</span>'
            f'<span style="font-size:14px;color:{IVORY};line-height:1.6;">{text}</span></div>')

# ═══════════════════════════════════════════════════════════════════
# MARKET MOOD INDEX (MMI) — unchanged from original, no data-source
# or layout changes requested for this section.
# ═══════════════════════════════════════════════════════════════════

_MMI_URL = "https://www.tickertape.in/market-mood-index"
_MMI_CACHE_DIR = Path(".cache")
_MMI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_MMI_CACHE_FILE = _MMI_CACHE_DIR / "mmi_last_known.json"
_MMI_ZONES = [
    (0, 30, "Extreme Fear"),
    (30, 50, "Fear"),
    (50, 70, "Greed"),
    (70, 100.0001, "Extreme Greed"),
]

def _mmi_zone_for_score(score: float) -> str:
    for lo, hi, label in _MMI_ZONES:
        if lo <= score < hi:
            return label
    return "Unknown"

def mmi_zone_color(zone: str) -> str:
    return {
        "Extreme Fear":  RED,
        "Fear":          AMBER,
        "Greed":         "#84CC16",
        "Extreme Greed": GREEN,
    }.get(zone, T2)

def _mmi_parse_score(html_or_text: str):
    pattern = re.compile(r'(\d{1,3}\.\d{1,2})\s*(?:<[^>]+>\s*)*Updated', re.MULTILINE)
    for m in pattern.finditer(html_or_text):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if 0 <= val <= 100:
            return val
    return None

def _mmi_save_last_known(score: float, zone: str):
    try:
        _MMI_CACHE_FILE.write_text(json.dumps({
            "score": score, "zone": zone,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception:
        pass

def _mmi_load_last_known():
    if not _MMI_CACHE_FILE.exists():
        return None
    try:
        d = json.loads(_MMI_CACHE_FILE.read_text())
        fetched = datetime.fromisoformat(d["fetched_at_utc"])
        age = datetime.now(timezone.utc) - fetched
        return {"score": d["score"], "zone": d["zone"], "age": age,
                "fetched_at_ist": fetched.astimezone(timezone(timedelta(hours=5, minutes=30)))}
    except Exception:
        return None

@st.cache_data(ttl=1800, show_spinner=False)
def _mmi_fetch_live():
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        resp = session.get(_MMI_URL, timeout=10)
        if resp.status_code != 200:
            return None
        score = _mmi_parse_score(resp.text)
        if score is None:
            return None
        zone = _mmi_zone_for_score(score)
        return {"score": round(score, 2), "zone": zone}
    except Exception:
        return None

def get_mmi():
    IST = timezone(timedelta(hours=5, minutes=30))
    live = _mmi_fetch_live()
    if live:
        _mmi_save_last_known(live["score"], live["zone"])
        return {"status": "live", "score": live["score"], "zone": live["zone"],
                "fetched_at_ist": datetime.now(IST)}
    cached = _mmi_load_last_known()
    if cached:
        return {"status": "stale", "score": cached["score"], "zone": cached["zone"],
                "age": cached["age"], "fetched_at_ist": cached["fetched_at_ist"]}
    return {"status": "unavailable"}

# ════════════════════════════════════════════════════════════
# LANDING PAGE
# NOTE: left as-is intentionally. The redesign request was scoped to
# the app's interior (post-login) interface to match the Bloomberg
# terminal reference — the marketing landing page has its own
# separate green hero theme that wasn't part of what was asked to
# change. Flag if this should also move to the amber/black system.
# ════════════════════════════════════════════════════════════
if not st.session_state.logged_in:

    _DARK="#070b0a"; _DARK2="#0d1512"; _DARK3="#13201b"; _BORDER="#1d2f27"
    _IVORY="#e9f5ef"; _T2="#8aa79a"
    _INDIGO="#5ed29c"; _CYAN="#2dd4bf"; _GREEN="#34d399"; _PURPLE="#7dd3c0"; _PINK="#5eead4"
    _GRAD_BRAND=f"linear-gradient(135deg,{_INDIGO},{_CYAN})"

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

    if st.session_state.show_login:
        st.markdown(f"""
        <div style="text-align:center;padding:70px 0 10px;">
            <div style="display:inline-flex;align-items:center;gap:10px;">
                <div style="width:38px;height:38px;border-radius:10px;background:{_GRAD_BRAND};display:flex;align-items:center;justify-content:center;">{icon("trend", 19, "#070b0a")}</div>
                <div style="text-align:left;">
                    <div style="font-size:18px;font-weight:800;color:{_IVORY};letter-spacing:1px;">ARKA TRADES</div>
                    <div style="font-size:9px;letter-spacing:2px;color:{_T2};text-transform:uppercase;">Market Analytics Platform</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)
        _, login_col, _ = st.columns([1, 1.1, 1])
        with login_col:
            with st.form("lf"):
                st.markdown(f"""<div style="margin-bottom:14px;text-align:center;">
                    <div style="font-size:20px;font-weight:800;color:{_IVORY};">Member Login</div>
                    <div style="font-size:12px;color:{_T2};margin-top:4px;">Sign in to access your terminal</div></div>""", unsafe_allow_html=True)
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

    if st.button("Get Started →", type="primary", key="cta_main"):
        st.session_state.show_login = True
        st.rerun()

    st.markdown("<div style='height:40px;'></div>", unsafe_allow_html=True)
    s1, s2, s3, s4 = st.columns(4)
    for col, num, label, c in [(s1,"2000+","NSE stocks covered",_CYAN),(s2,"<90s","Scan time after pre-filter",_INDIGO),
        (s3,"30s","Live price refresh",_GREEN),(s4,"24/7","AI memory of your setups",_PURPLE)]:
        with col:
            st.markdown(f"""<div class="fade-up" style="background:{_DARK2};border:1px solid {_BORDER};border-top:2px solid {c};border-radius:12px;padding:20px;text-align:center;">
                <div style="font-family:{MONO};font-size:26px;font-weight:700;color:{c};margin-bottom:4px;">{num}</div>
                <div style="font-size:12px;color:{_T2};font-weight:600;">{label}</div></div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    fa1, fa2 = st.columns([1, 1])
    with fa1:
        st.markdown(f"""<div class="fade-up" style="padding:24px 8px;">{icon_box("brain", _PURPLE)}
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{_PURPLE};text-transform:uppercase;margin-bottom:10px;">AI Chart Analysis</div>
            <div style="font-size:28px;font-weight:800;color:{_IVORY};letter-spacing:-0.5px;line-height:1.25;margin-bottom:16px;">Teach the AI your setups.<br>It never forgets.</div>
            {checkline("Save your personal trading rules, entry conditions and reference charts once")}
            {checkline("Gemini-powered vision analyzes any chart against <strong>your</strong> rules")}
            {checkline("Get a verdict, score and rule-by-rule breakdown in seconds")}
            {checkline("Vector memory stores every setup permanently")}</div>""", unsafe_allow_html=True)
    with fa2:
        st.markdown(f"""<div class="fade-up" style="background:{_DARK2};border:1px solid {_BORDER};border-top:2px solid {_PURPLE};border-radius:16px;padding:24px;margin-top:24px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                <span style="font-size:13px;font-weight:700;color:{_IVORY};">RELIANCE · Daily</span>
                <span style="background:rgba(52,211,153,.12);color:{_GREEN};font-size:11px;font-weight:700;padding:4px 12px;border-radius:20px;border:1px solid {_GREEN}33;">VALID · 8/10</span></div>
            <div style="background:{_DARK3};border-radius:10px;padding:16px;font-family:{MONO};font-size:12px;color:{_T2};line-height:2;">
                <span style="color:{_GREEN};">+ Rule matched:</span> Close above PDH on breakout candle<br>
                <span style="color:{_GREEN};">+ Rule matched:</span> Volume 1.8x vs 20-day average<br>
                <span style="color:{_GREEN};">+ Rule matched:</span> RSI 61 — within momentum zone<br>
                <span style="color:{RED};">- Flagged:</span> Overhead supply at 2,980 level</div>
            <div style="font-size:12px;color:{_T2};margin-top:12px;line-height:1.7;">"Structure is clean. Entry valid above 2,941 with stop at 2,896."</div></div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:48px;'></div>", unsafe_allow_html=True)
    fb1, fb2 = st.columns([1, 1])
    with fb1:
        st.markdown(f"""<div class="fade-up" style="background:{_DARK2};border:1px solid {_BORDER};border-top:2px solid {_GREEN};border-radius:16px;padding:24px;margin-top:24px;">
            <div style="font-size:13px;font-weight:700;color:{_IVORY};margin-bottom:14px;">Scan: "Bull Flag + Volume Surge" · Full NSE</div>
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
                <tr style="color:{_T2};text-align:left;"><th style="padding:6px 8px;">Symbol</th><th style="padding:6px 8px;">Price</th><th style="padding:6px 8px;">Signal</th><th style="padding:6px 8px;">Score</th></tr>
                <tr><td style="padding:8px;color:{_IVORY};font-weight:700;border-top:1px solid {_BORDER};">TATAMOTORS</td><td style="padding:8px;font-family:{MONO};color:{_IVORY};border-top:1px solid {_BORDER};">1,024.50</td><td style="padding:8px;border-top:1px solid {_BORDER};"><span style="color:{_GREEN};font-weight:700;">STRONG MATCH</span></td><td style="padding:8px;font-family:{MONO};color:{_GREEN};border-top:1px solid {_BORDER};">9/10</td></tr>
                <tr><td style="padding:8px;color:{_IVORY};font-weight:700;border-top:1px solid {_BORDER};">CHOLAFIN</td><td style="padding:8px;font-family:{MONO};color:{_IVORY};border-top:1px solid {_BORDER};">1,388.20</td><td style="padding:8px;border-top:1px solid {_BORDER};"><span style="color:{_GREEN};font-weight:700;">STRONG MATCH</span></td><td style="padding:8px;font-family:{MONO};color:{_GREEN};border-top:1px solid {_BORDER};">8/10</td></tr>
                <tr><td style="padding:8px;color:{_IVORY};font-weight:700;border-top:1px solid {_BORDER};">PERSISTENT</td><td style="padding:8px;font-family:{MONO};color:{_IVORY};border-top:1px solid {_BORDER};">4,832.00</td><td style="padding:8px;border-top:1px solid {_BORDER};"><span style="color:{_CYAN};font-weight:700;">PARTIAL</span></td><td style="padding:8px;font-family:{MONO};color:{_CYAN};border-top:1px solid {_BORDER};">7/10</td></tr>
            </table></div>""", unsafe_allow_html=True)
    with fb2:
        st.markdown(f"""<div class="fade-up" style="padding:24px 8px;">{icon_box("search", _GREEN)}
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{_GREEN};text-transform:uppercase;margin-bottom:10px;">AI Smart Scanner</div>
            <div style="font-size:28px;font-weight:800;color:{_IVORY};letter-spacing:-0.5px;line-height:1.25;margin-bottom:16px;">Your setups, scanned across<br>the entire market.</div>
            {checkline("Describe your setup in plain English — AI extracts the rules")}
            {checkline("Price pre-filter across all ~2000 NSE stocks, then deep scan")}
            {checkline("Gemini Vision compares charts against your reference image")}
            {checkline("Ranked similarity verdicts with entry and risk notes")}</div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{_CYAN};text-transform:uppercase;margin-bottom:8px;">Built for your style</div>
        <div style="font-size:28px;font-weight:800;color:{_IVORY};">Momentum. Swing. Positional.</div></div>""", unsafe_allow_html=True)
    t1, t2, t3 = st.columns(3)
    for col, ic, ic_c, title, items in [
        (t1,"zap",AMBER,"Momentum Traders",["PDH / PDL breakout detection in real time","30-second live price refresh","Volume spike flags vs 20-day average","Instant Telegram push when levels break"]),
        (t2,"trend",_CYAN,"Swing Traders",["Multi-day setup scanning: flags, bases, ranges","RSI and ROC filters across your watchlist","AI pattern matching vs saved reference charts","Daily structure analysis with SMA 20/50"]),
        (t3,"layers",_PURPLE,"Positional Traders",["Curated Arka Watchlist maintained by the desk","Persistent combined news feed","Live index dashboard for market breadth","Cloud-synced watchlists on any device"])]:
        with col:
            checks = "".join(checkline(i, ic_c) for i in items)
            st.markdown(f"""<div class="fade-up" style="background:{_DARK2};border:1px solid {_BORDER};border-top:2px solid {ic_c};border-radius:14px;padding:26px;min-height:300px;">
                {icon_box(ic, ic_c)}<div style="font-size:16px;font-weight:800;color:{_IVORY};margin-bottom:16px;">{title}</div>{checks}</div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{_CYAN};text-transform:uppercase;margin-bottom:8px;">Onboarding roadmap</div>
        <div style="font-size:28px;font-weight:800;color:{_IVORY};">Live in two weeks.</div></div>""", unsafe_allow_html=True)
    rm1, rm2, rm3 = st.columns(3)
    for col,(day,title,desc,c) in zip([rm1,rm2,rm3],[
        ("DAY 1","Connection & Import","Sign in and upload your TradingView watchlist. Cloud sync is instant.",_CYAN),
        ("DAY 7","AI Strategy Training","Teach Arka AI your setups, rules and reference charts.",_PURPLE),
        ("DAY 14","Automated Scans Live","Full-universe scans and Telegram alerts on your exact conditions.",_GREEN)]):
        with col:
            st.markdown(f"""<div class="fade-up" style="background:{_DARK2};border:1px solid {_BORDER};border-top:2px solid {c};border-radius:14px;padding:24px;">
                <div style="font-family:{MONO};font-size:11px;font-weight:700;color:{c};letter-spacing:2px;margin-bottom:10px;">{day}</div>
                <div style="font-size:15px;font-weight:800;color:{_IVORY};margin-bottom:8px;">{title}</div>
                <div style="font-size:13px;color:{_T2};line-height:1.7;">{desc}</div></div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="text-align:center;padding:56px 0 40px;">
        <div style="font-size:13px;color:{_T2};margin-bottom:6px;">Arka Trades · Finance &amp; Market Education</div>
        <div style="font-size:11px;color:{_T2};opacity:.6;">Not SEBI registered. All content is for educational purposes only.
        Trading involves risk — decisions and outcomes are entirely your own.</div></div>""", unsafe_allow_html=True)
    st.stop()

# ════════════════ DISCLAIMER ═════════════════════════════════
if not st.session_state.disclaimer_done:
    _, col, _ = st.columns([1,3,1])
    with col:
        st.markdown(f"""<div style="padding:48px 0 20px;text-align:center;">
            <div style="font-size:30px;font-weight:800;color:{IVORY};">Disclaimer &amp; Terms</div>
            <div style="font-size:13px;color:{T2};margin-top:6px;margin-bottom:24px;">Read all terms carefully before continuing</div></div>
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:4px;padding:28px;font-size:13px;color:{T2};line-height:2;max-height:260px;overflow-y:auto;margin-bottom:20px;">
            <strong style="color:{AMBER}">1. No Financial Advice</strong><br>Arka Trades does not provide financial or investment advice. Educational only.<br><br>
            <strong style="color:{AMBER}">2. Not SEBI Registered</strong><br>We are not registered with SEBI as investment advisor or research analyst.<br><br>
            <strong style="color:{AMBER}">3. Personal Responsibility</strong><br>All trading decisions are yours. You bear full responsibility for profits or losses.<br><br>
            <strong style="color:{AMBER}">4. Data Accuracy</strong><br>Market data may be delayed. We do not guarantee accuracy of any data shown.<br><br>
            <strong style="color:{AMBER}">5. Personal Use Only</strong><br>For personal educational use only. Not for commercial distribution.</div>""", unsafe_allow_html=True)
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
PAGE_ACCENTS = {"home":AMBER,"scanner":CYAN,"alerts":AMBER,"analysis":PURPLE,
    "smart_scan":GREEN,"breadth":PINK,"heatmap":T2,"autoalert":T2,"profile":AMBER,
    "settings":AMBER,"contact":CYAN}
# NOTE: "news" removed from PAGE_ACCENTS and the nav list below — the
# standalone News Terminal page is gone per the request to remove the
# separate news sub-group; news now lives only in the persistent box.

with left:
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:20px 12px 14px;border-bottom:1px solid {BORDER};">
        <div style="width:32px;height:32px;border-radius:4px;background:{GRAD_BRAND};display:flex;align-items:center;justify-content:center;">{icon("trend", 16, "#000")}</div>
        <div><div style="font-size:15px;font-weight:800;color:{IVORY};line-height:1;">ARKA TRADES</div>
        <div style="font-size:8px;letter-spacing:2px;color:{T2};text-transform:uppercase;margin-top:3px;">Analytics Platform</div></div></div>""", unsafe_allow_html=True)

    photo = st.session_state.get("profile_photo")
    if photo:
        st.image(photo, width=70)
    else:
        st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:14px 12px;">
            <div style="width:40px;height:40px;border-radius:4px;background:{GRAD_AI};display:flex;align-items:center;justify-content:center;font-weight:800;font-size:16px;color:#fff;">{initial}</div>
            <div><div style="font-size:11px;color:{T2};">Signed in as</div>
            <div style="font-weight:800;font-size:14px;color:{IVORY};">{name}</div></div></div>
        <div style="height:1px;background:{BORDER};"></div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='padding:14px 12px 4px;font-size:10px;font-weight:700;letter-spacing:2px;color:{AMBER};text-transform:uppercase;'>Product Suite</div>", unsafe_allow_html=True)
    pg = st.session_state.page

    def nav_btn(label, key):
        active = pg == key
        css_class = "nav-btn-active" if active else "nav-btn"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        if st.button(label, key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # CHANGED: "News Terminal" nav entry removed — persistent box
    # replaces the standalone page.
    nav_btn("Dashboard","home"); nav_btn("Scanner","scanner"); nav_btn("Alerts","alerts")
    nav_btn("Arka AI","analysis")
    nav_btn("Smart Screener","smart_scan"); nav_btn("Market Breadth","breadth")
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

    # CHANGED: persistent news box lives here, at the bottom of the
    # left nav column — this places it visually bottom-left of the
    # whole app frame, present regardless of which page is selected.
    # It's called exactly once per script run (not per-page), so it
    # doesn't reset or duplicate state when switching pages.
    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    _ensure_news_state()
    # Combine both watchlists so the box reflects everything the
    # person actually tracks, not just one list.
    _combined_watchlist = list(dict.fromkeys(
        st.session_state.get("watchlist", []) + st.session_state.get("admin_watchlist", [])
    ))
    news_box(_combined_watchlist)

with right:
    pg = st.session_state.page
    accent = PAGE_ACCENTS.get(pg, AMBER)
    page_titles = {"home":"Dashboard","scanner":"Watchlist Scanner","alerts":"Alerts Manager",
        "analysis":"Arka AI","smart_scan":"Smart Screener",
        "breadth":"Market Breadth","heatmap":"Heatmap","autoalert":"Auto Alerts",
        "profile":"Profile","settings":"Settings","contact":"Contact"}

    n1, n2 = st.columns([5,1])
    with n1:
        st.markdown(f"""<div style="display:flex;align-items:center;gap:12px;padding:16px 0 10px;">
            <div style="width:5px;height:34px;border-radius:0;background:{accent};"></div>
            <div><div style="font-size:21px;font-weight:800;color:{IVORY};">{page_titles.get(pg,"Dashboard")}</div>
            <div style="font-size:12px;color:{T2};margin-top:2px;">Arka Trades · Market Analytics Platform</div></div></div>""", unsafe_allow_html=True)
    with n2:
        st.markdown(f"""<div style="display:flex;align-items:center;justify-content:flex-end;height:60px;padding-right:8px;">
            <div style="display:inline-flex;align-items:center;gap:7px;font-weight:700;font-size:11px;letter-spacing:1px;color:{GREEN};border:1px solid rgba(0,200,83,0.35);padding:5px 12px;border-radius:2px;background:rgba(0,200,83,0.08);"><span class="pulse-dot"></span>LIVE</div></div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:1px;background:{BORDER};margin-bottom:12px;'></div>", unsafe_allow_html=True)

    def show_idx(col, label, sym, c, fallback_syms=None, currency="", index_label=None):
        # CHANGED: index_label passed through to get_index() so
        # NSE-domestic indices (NIFTY 50, BANK NIFTY, MIDCAP,
        # SMALLCAP) try nsepythonserver first. Indices without a
        # matching index_label (SENSEX, S&P 500, DOW, GOLD) go
        # straight to the yfinance path exactly as before.
        d = get_index(sym, fallback_syms, index_label=index_label)
        with col:
            if d:
                cc = GREEN if d["chg"]>=0 else RED
                pts_sign = "+" if d["pts"] >= 0 else ""
                spark = sparkline(d.get("spark", []), color=cc, w=120, h=26)
                st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:2px;padding:14px;margin:4px 2px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:11px;font-weight:700;color:{T2};text-transform:uppercase;letter-spacing:0.5px;">{label}</span>{change_pill(d['chg'])}</div>
                    <div style="font-family:{MONO};font-weight:700;font-size:20px;color:{IVORY};line-height:1;margin-bottom:4px;">{currency}{d['price']:,.2f}</div>
                    <div style="font-family:{MONO};font-size:12px;font-weight:600;color:{cc};margin-bottom:6px;">{pts_sign}{d['pts']:,.2f} pts</div>{spark}</div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:2px;padding:14px;margin:4px 2px;opacity:0.5;">
                    <div style="font-size:11px;font-weight:700;color:{T2};margin-bottom:8px;text-transform:uppercase;">{label}</div>
                    <div style="font-family:{MONO};font-size:20px;color:{T2};">--</div>
                    <div style="font-size:11px;color:{T2};margin-top:4px;">No data</div></div>""", unsafe_allow_html=True)

    if pg == "home":
        r1a,r1b,r1c = st.columns(3)
        show_idx(r1a,"NIFTY 50","^NSEI",AMBER, index_label="NIFTY 50")
        show_idx(r1b,"BANK NIFTY","^NSEBANK",CYAN, index_label="BANK NIFTY")
        show_idx(r1c,"SENSEX","^BSESN",AMBER)  # BSE index — no NSE path, unchanged
        r2a,r2b = st.columns(2)
        show_idx(r2a,"MIDCAP 100", MIDCAP_CANDIDATES[0], PURPLE, fallback_syms=MIDCAP_CANDIDATES[1:], index_label="MIDCAP 100")
        show_idx(r2b,"SMALLCAP 100", SMALLCAP_CANDIDATES[0], PINK, fallback_syms=SMALLCAP_CANDIDATES[1:], index_label="SMALLCAP 100")
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:12px 0 16px;'></div>", unsafe_allow_html=True)

        st.markdown(f"""<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{T2};
            text-transform:uppercase;margin-bottom:8px;">Global Markets</div>""", unsafe_allow_html=True)
        gi1, gi2, gi3 = st.columns(3)
        show_idx(gi1,"S&P 500", SP500_CANDIDATES[0], CYAN, fallback_syms=SP500_CANDIDATES[1:], currency="$")
        show_idx(gi2,"DOW JONES", DOWJONES_CANDIDATES[0], AMBER, fallback_syms=DOWJONES_CANDIDATES[1:], currency="$")
        show_idx(gi3,"GOLD (USD)", GOLD_CANDIDATES[0], "#FFD700", fallback_syms=GOLD_CANDIDATES[1:], currency="$")

        mmi = get_mmi()
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

        if mmi["status"] == "live":
            zc = mmi_zone_color(mmi["zone"])
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                border-top:2px solid {zc};border-radius:2px;padding:16px 18px;margin:4px 2px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-size:11px;font-weight:700;color:{T2};text-transform:uppercase;margin-bottom:6px;">
                            Market Mood Index (MMI)</div>
                        <div style="display:flex;align-items:baseline;gap:10px;">
                            <span style="font-family:{MONO};font-weight:700;font-size:24px;color:{IVORY};">{mmi['score']}</span>
                            <span style="background:{zc}22;color:{zc};font-size:12px;font-weight:700;
                                padding:3px 12px;border-radius:2px;border:1px solid {zc}55;">{mmi['zone']}</span>
                        </div>
                    </div>
                    <div style="text-align:right;font-size:11px;color:{T2};">Updated {mmi['fetched_at_ist'].strftime('%d %b %Y, %I:%M%p')}<br>
                        <span style="opacity:.7;">Source: Tickertape</span></div>
                </div></div>""", unsafe_allow_html=True)

        elif mmi["status"] == "stale":
            zc = mmi_zone_color(mmi["zone"])
            age = mmi["age"]
            hrs = int(age.total_seconds() // 3600)
            age_label = f"{hrs}h ago" if hrs < 48 else f"{hrs // 24}d ago"
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {AMBER}55;
                border-top:2px solid {AMBER};border-radius:2px;padding:16px 18px;margin:4px 2px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                            <span style="font-size:11px;font-weight:700;color:{T2};text-transform:uppercase;">
                                Market Mood Index (MMI)</span>
                            <span style="background:{AMBER}22;color:{AMBER};font-size:10px;font-weight:700;
                                padding:2px 8px;border-radius:2px;border:1px solid {AMBER}55;">⚠ STALE DATA</span>
                        </div>
                        <div style="display:flex;align-items:baseline;gap:10px;">
                            <span style="font-family:{MONO};font-weight:700;font-size:24px;color:{IVORY};opacity:.75;">{mmi['score']}</span>
                            <span style="background:{zc}22;color:{zc};font-size:12px;font-weight:700;
                                padding:3px 12px;border-radius:2px;border:1px solid {zc}55;opacity:.85;">{mmi['zone']}</span>
                        </div>
                    </div>
                    <div style="text-align:right;font-size:11px;color:{AMBER};">Live fetch failed<br>
                        <span style="opacity:.8;">Last known value · {age_label}</span></div>
                </div></div>""", unsafe_allow_html=True)

        else:
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {T2};
                border-radius:2px;padding:16px 18px;margin:4px 2px;opacity:0.6;">
                <div style="font-size:11px;font-weight:700;color:{T2};text-transform:uppercase;margin-bottom:6px;">
                    Market Mood Index (MMI)</div>
                <div style="font-size:12px;color:{T2};">Unavailable — Tickertape's page couldn't be read and no
                    cached value exists yet. This will populate automatically once a scan succeeds.</div></div>""", unsafe_allow_html=True)

        st.markdown(f"<div style='height:1px;background:{BORDER};margin:16px 0 16px;'></div>", unsafe_allow_html=True)

    # CHANGED: bottom padding reduced from 80px to a value that
    # accounts for the fixed news box height (280px) so page content
    # doesn't hide behind it. This is a rough clearance, not a
    # precise calculation — verify visually and adjust
    # #arka-news-fixed's height or this padding if content still
    # gets covered near the bottom of a long page.
    st.markdown('<div style="padding:0 8px 300px;">', unsafe_allow_html=True)

    if pg == "home":
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        mkt = now.replace(hour=9,minute=15,second=0,microsecond=0) <= now <= now.replace(hour=15,minute=30,second=0,microsecond=0)
        mkt_color = GREEN if mkt else RED
        mkt_label = "MARKET OPEN" if mkt else "MARKET CLOSED"
        g1,g2,g3 = st.columns([1.2, 1, 1])
        with g1:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:24px;min-height:130px;">
                <div style="display:inline-flex;align-items:center;gap:8px;background:{mkt_color}14;border:1px solid {mkt_color}33;border-radius:2px;padding:5px 14px;margin-bottom:14px;">
                <span style="width:7px;height:7px;border-radius:50%;background:{mkt_color};display:inline-block;"></span>
                <span style="font-size:11px;font-weight:700;letter-spacing:1px;color:{mkt_color};">{mkt_label}</span></div>
                <div style="font-size:13px;color:{T2};">NSE trading hours · 09:15 to 15:30 IST</div>
                <div style="font-family:{MONO};font-size:13px;color:{IVORY};margin-top:6px;">{now.strftime("%d %b %Y · %H:%M:%S IST")}</div></div>""", unsafe_allow_html=True)
        with g2:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:24px;min-height:130px;">
                {icon_box("layers", CYAN, 34)}<div style="font-family:{MONO};font-size:22px;font-weight:700;color:{IVORY};">{len(st.session_state.watchlist)}</div>
                <div style="font-size:12px;color:{T2};">Stocks in your watchlist</div></div>""", unsafe_allow_html=True)
        with g3:
            active_alerts = sum(1 for a in st.session_state.alerts.values() if a.get("active"))
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:24px;min-height:130px;">
                {icon_box("bell", AMBER, 34)}<div style="font-family:{MONO};font-size:22px;font-weight:700;color:{IVORY};">{active_alerts}</div>
                <div style="font-size:12px;color:{T2};">Active price alerts</div></div>""", unsafe_allow_html=True)

        section("Platform Modules", AMBER)
        w1,w2,w3,w4 = st.columns(4)
        for col,ic,c,title,desc,target in [
            (w1,"brain",PURPLE,"AI Chart Analysis","Arka AI checks any chart against your saved rules and returns a scored verdict.","analysis"),
            (w2,"search",GREEN,"Smart Screener","Scan all NSE stocks with plain-English rules and AI vision matching.","smart_scan"),
            (w3,"trend",PINK,"Market Breadth","See how many NSE stocks are actually confirming the move — not just the index.","breadth"),
            (w4,"bell",AMBER,"Breakout Alerts","PDH, PDL and custom price alerts delivered to Telegram instantly.","alerts")]:
            with col:
                st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};border-radius:2px;padding:22px;min-height:195px;margin-bottom:8px;">
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
            # CHANGED: label updated from "10s Live" to "30s Live" to
            # match the new cache floor in nse_data.py — the checkbox
            # still triggers the same sleep-then-rerun loop, just at
            # the safer interval.
            l30  = sc2.checkbox("30s Live", key=f"l30_{key_prefix}")
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
                    if s["cls"]=="g":   bd="rgba(0,200,83,0.4)"; top=GREEN
                    elif s["cls"]=="r": bd="rgba(255,59,48,0.4)"; top=RED
                    else:               bd=BORDER; top=BORDER
                    cc = GREEN if s["chg"] >= 0 else RED
                    rc = GREEN if s["rsi"] < 35 else RED if s["rsi"] > 65 else T2
                    ha = s["sym"] in st.session_state.alerts and st.session_state.alerts[s["sym"]].get("active")
                    nd = get_news_dot(s["sym"])
                    dot = f'<span style="color:{AMBER};font-size:9px;margin:0 2px;">&#9679;</span>' if nd else ""
                    bell = icon("bell", 12, AMBER) if ha else ""
                    spark = sparkline(s.get("spark", []), color=cc, w=95, h=24)
                    card = (f'<div style="background:{DARK2};border:1px solid {bd};border-top:2px solid {top};border-radius:2px;padding:12px 8px 10px;text-align:center;margin-bottom:6px;">'
                        f'<div style="display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;">'
                        f'<span style="font-weight:800;font-size:13px;color:{IVORY};white-space:nowrap;">{s["sym"]}</span>{dot}{bell}</div>'
                        f'<div style="margin-bottom:5px;">{change_pill(s["chg"])}</div>'
                        f'<div style="font-family:{MONO};font-weight:700;font-size:14px;color:{IVORY};line-height:1;margin-bottom:5px;">&#8377;{s["cur"]:.2f}</div>'
                        f'{spark}<div style="font-family:{MONO};font-size:11px;font-weight:700;color:{rc};margin-top:4px;">RSI {s["rsi"]}</div></div>')
                    with cols7[i % 5]:
                        st.markdown(card, unsafe_allow_html=True)
                IST = timezone(timedelta(hours=5, minutes=30))
                st.caption(f"Scanned: {datetime.now(IST).strftime('%d %b %Y  %H:%M:%S')}  ·  % vs prev close  ·  Price: 30s cache")
                if l30: time.sleep(30); st.cache_data.clear(); st.rerun()
                elif l60: time.sleep(60); st.cache_data.clear(); st.rerun()

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        tab1, tab2 = st.tabs(["Arka Watchlist", "Your Watchlist"])
        with tab1:
            if IS_ADMIN:
                uploaded_admin = st.file_uploader("Upload Arka Watchlist", type=["csv","txt"], key="admin_upload")
                if uploaded_admin:
                    syms = parse_csv(uploaded_admin)
                    if not syms: st.error("No symbols found.")
                    elif db_save_admin_watchlist(syms):
                        st.success(f"Arka Watchlist updated — {len(syms)} stocks.")
            admin_syms = st.session_state.admin_watchlist
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {CYAN};border-radius:2px;padding:16px 24px;margin:16px 0;">
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:4px;">Arka Watchlist</div>
                <div style="font-size:12px;color:{T2};">{f"{len(admin_syms)} stocks · Curated by the Arka Trades desk" if admin_syms else "No curated watchlist published yet"}</div></div>""", unsafe_allow_html=True)
            if not admin_syms:
                st.info("Arka Watchlist not available yet.")
            else:
                render_scan_results(admin_syms, key_prefix="admin")
                # CHANGED: news_panel(admin_syms) call removed — the
                # persistent news_box in the left column already
                # covers this watchlist as part of the combined feed.
        with tab2:
            uploaded_yours = st.file_uploader("Upload Your Watchlist (CSV or TXT)", type=["csv","txt"], key="your_upload")
            if uploaded_yours:
                syms = parse_csv(uploaded_yours)
                if not syms: st.error("No symbols found.")
                elif db_save_watchlist(syms):
                    st.success(f"{len(syms)} stocks loaded and saved.")
            your_syms = st.session_state.watchlist
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {GREEN};border-radius:2px;padding:16px 24px;margin:16px 0;">
                <div style="font-size:15px;font-weight:800;color:{IVORY};margin-bottom:4px;">Your Watchlist</div>
                <div style="font-size:12px;color:{T2};">{f"{len(your_syms)} stocks · Synced to cloud" if your_syms else "No watchlist uploaded yet"}</div></div>""", unsafe_allow_html=True)
            if not your_syms:
                st.info("Upload your TradingView watchlist above to start scanning.")
            else:
                render_scan_results(your_syms, key_prefix="yours")
                # CHANGED: news_panel(your_syms) call removed — same
                # reasoning as the admin tab above.

    elif pg == "alerts":
        active_alerts = {s: a for s, a in st.session_state.alerts.items() if a.get("active")}
        a1, a2, a3 = st.columns(3)
        a1.metric("Active Alerts", len(active_alerts))
        a2.metric("Triggered Today", len(st.session_state.alert_fired))
        a3.metric("Delivery Channel", "Telegram")
        st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {AMBER};border-radius:2px;padding:14px 20px;margin:16px 0 8px;">
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
                    status = (f'<span style="display:inline-flex;align-items:center;gap:6px;background:rgba(255,149,0,.12);color:{AMBER};font-size:11px;font-weight:700;padding:3px 10px;border-radius:2px;border:1px solid {AMBER}33;"><span class="pulse-dot" style="background:{AMBER};"></span>ACTIVE</span>')
                else:
                    status = (f'<span style="background:{DARK3};color:{T2};font-size:11px;font-weight:700;padding:3px 10px;border-radius:2px;border:1px solid {BORDER};">INACTIVE</span>')
                rc1, rc2 = st.columns([4, 1.4])
                with rc1:
                    st.markdown(f"""<div style="display:grid;grid-template-columns:2fr 1.2fr 1.5fr 1.2fr;gap:8px;align-items:center;background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:12px 16px;margin-bottom:6px;">
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
                    st.markdown(f"""<div style="background:{DARK3};border:1px solid {BORDER};border-radius:2px;padding:4px 16px;margin-bottom:8px;">
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

    # CHANGED: `elif pg == "news":` standalone page block removed
    # entirely — persistent news_box replaces it.

    elif pg in ["analysis","heatmap","autoalert"]:
        if pg == "analysis":
            render_arka_ai()
        else:
            labels = {"heatmap":"Market Heatmap","autoalert":"Auto Smart Alerts"}
            st.markdown(f"""<div style="background:{DARK2};border:1px dashed {BORDER};border-radius:2px;padding:100px 20px;text-align:center;margin:20px 0;">
                <div style="margin-bottom:16px;">{icon("clock", 32, T2)}</div>
                <div style="font-size:26px;font-weight:800;color:{T2};margin-bottom:10px;">{labels.get(pg,'Coming Soon')}</div>
                <div style="font-size:14px;color:{T2};opacity:.6;">This module is under development</div></div>""", unsafe_allow_html=True)

    elif pg == "smart_scan":
        from smart_scan_page import render_smart_scanner
        render_smart_scanner(supabase)

    elif pg == "breadth":
        try:
            from breadth_page import render_market_breadth
            render_market_breadth()
        except Exception as e:
            import traceback
            st.error(f"Market Breadth module failed to load: {e}")
            st.code(traceback.format_exc())

    elif pg == "profile":
        p1,p2 = st.columns([1,2])
        with p1:
            photo=st.session_state.get("profile_photo")
            if photo:
                st.image(photo,width=120); st.caption(name)
            else:
                st.markdown(f"""<div style="width:96px;height:96px;border-radius:4px;background:{GRAD_AI};display:flex;align-items:center;justify-content:center;font-weight:800;font-size:36px;color:#fff;margin-bottom:12px;">{initial}</div>
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
            st.markdown(f"""<div style="background:{DARK2};border:2px solid {AMBER};border-radius:2px;padding:20px;text-align:center;">
                <div style="margin-bottom:10px;">{icon("shield", 24, AMBER)}</div>
                <div style="font-weight:800;font-size:14px;color:{AMBER};">DARK MODE</div>
                <div style="font-size:12px;color:{T2};margin-top:4px;">Currently active</div></div>""", unsafe_allow_html=True)
        with t2:
            st.markdown(f"""<div style="background:{DARK3};border:1px solid {BORDER};border-radius:2px;padding:20px;text-align:center;opacity:.6;">
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
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {CYAN};border-radius:2px;padding:28px;">
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
