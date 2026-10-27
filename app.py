import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests
import re
import json
import math
from pathlib import Path
from supabase import create_client, Client
import user_data as ud
from news_feed import render_news_rail, get_news_dot, _ensure_news_state, refresh_news, _fetch_news_for_stock
from arka_ai import render_arka_ai
from research_page import render_research_page, render_research_panel
from screener_scraper import resolve_symbol
from data_engine import (get_indices, get_quotes, scan_batch, get_static, get_history,
                         warm_security, to_float)
from chart_engine import render_pro_chart
from money_flow import render_money_flow
# watchlist_page / portfolio_page are imported lazily inside their page branches
# so a problem in either file can never stop the rest of the terminal from loading.

# ═══════════════════════════════════════════════════════════════════
# v6 — PROFESSIONAL INTERFACE
#
# Design direction (researched against Bloomberg-style terminals, TradingView, Mercury,
# Stripe tables and Streamlit's own theming guidance):
#   * Near-black canvas (#050505) with stepped surfaces, 1px hairline borders, ONE accent
#     (amber). Green / red are reserved for direction and always paired with an arrow, so
#     meaning never depends on colour alone.
#   * Inter for interface text, JetBrains Mono with tabular numerals for every figure.
#   * One persistent, sticky top bar on every page: brand, workspace navigation, live
#     market-session status, user and sign-out. One page header per workspace.
#   * Home: KPI strip, watchlist / holdings / internals panels and a launcher grid of
#     workspace cards. Security page: header with 52-week range, stat ribbon, one-click
#     actions (watchlist, portfolio, Arka AI, research).
#   * Landing + login re-skinned to the same palette (they used a separate green theme).
#   * Copy `.streamlit/config.toml` (delivered with this file) for native dark theming.
#
# Fixes carried in this version
#   * Navigation no longer flips the news rail off, so it never comes back collapsed.
#   * Navigation uses on_click callbacks (one rerun instead of two).
#   * st.secrets read through a safe helper; set_page_config runs first.
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(page_title="Arka Trades", layout="wide", page_icon="📈", initial_sidebar_state="collapsed")

IST = timezone(timedelta(hours=5, minutes=30))


def _secret(name: str, default=""):
    """st.secrets.get() that never raises when no secrets file exists."""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# ── Supabase ─────────────────────────────────────────────────
SUPABASE_URL = _secret("SUPABASE_URL", "")
SUPABASE_KEY = _secret("SUPABASE_KEY", "")

@st.cache_resource(show_spinner=False)
def get_supabase() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

def db_save_watchlist(symbols: list):
    if supabase is None:
        st.session_state.watchlist = symbols
        return True
    try:
        supabase.table("watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        if rows: supabase.table("watchlist").insert(rows).execute()
        st.session_state.watchlist = symbols
        return True
    except Exception as e:
        st.error(f"Save error: {e}"); return False

def db_load_watchlist() -> list:
    if supabase is None:
        return []
    try:
        res = supabase.table("watchlist").select("symbol").execute()
        # dict.fromkeys dedupes while preserving order — a stale duplicate row must never
        # reach the UI (sym-keyed widgets downstream crash on a repeated symbol).
        return list(dict.fromkeys(r["symbol"] for r in res.data)) if res.data else []
    except Exception: return []

def db_save_alert(symbol: str, alert_type: str, price: float):
    if supabase is None:
        return True
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute()
        supabase.table("alerts").insert({"symbol": symbol, "alert_type": alert_type,
            "price": price, "active": True}).execute()
        return True
    except Exception as e:
        st.error(f"Alert save error: {e}"); return False

def db_delete_alert(symbol: str):
    if supabase is None:
        return True
    try:
        supabase.table("alerts").delete().eq("symbol", symbol).execute(); return True
    except Exception: return False

def db_load_alerts() -> dict:
    if supabase is None:
        return {}
    try:
        res = supabase.table("alerts").select("*").eq("active", True).execute()
        return {r["symbol"]: {"type": r["alert_type"], "price": float(r["price"]), "active": True}
                for r in res.data} if res.data else {}
    except Exception: return {}

def db_save_admin_watchlist(symbols: list):
    if supabase is None:
        st.session_state.admin_watchlist = symbols
        return True
    try:
        supabase.table("admin_watchlist").delete().neq("id", 0).execute()
        rows = [{"symbol": s} for s in symbols]
        if rows: supabase.table("admin_watchlist").insert(rows).execute()
        st.session_state.admin_watchlist = symbols
        return True
    except Exception as e:
        st.error(f"Admin save error: {e}"); return False

def db_load_admin_watchlist() -> list:
    if supabase is None:
        return []
    try:
        res = supabase.table("admin_watchlist").select("symbol").execute()
        return list(dict.fromkeys(r["symbol"] for r in res.data)) if res.data else []
    except Exception: return []

BOT_TOKEN = _secret("BOT_TOKEN", "")
CHAT_ID   = _secret("CHAT_ID", "")

# Logins: set these in Streamlit Cloud -> Settings -> Secrets, then delete the
# fallback strings below so no password lives in your GitHub repo.
_ADMIN_USER  = _secret("ADMIN_USER",  "ADMIN4477MAX")
_ADMIN_PASS  = _secret("ADMIN_PASS",  "MOHIT1")
_MEMBER_USER = _secret("MEMBER_USER", "max trades")
_MEMBER_PASS = _secret("MEMBER_PASS", "max")

def send_telegram(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"}, timeout=5)
    except Exception: pass

# ════════════════ DESIGN TOKENS ════════════════════
BG, S1, S2, S3 = "#050505", "#0A0A0A", "#0F0F0F", "#151515"
LINE, LINE2 = "#1F1F1F", "#2B2B2B"
DARK, DARK2, DARK3, BORDER = "#000000", S1, "#111111", LINE2
BORDER2 = LINE
IVORY, T2, T3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
NAVY = S1

AMBER  = "#FF9F0A"
AMBER2 = "#FFB340"
CYAN   = "#5AC8FA"
GREEN  = "#30D158"
RED    = "#FF453A"
INDIGO = "#5E8CFF"
PURPLE = "#BF5AF2"
PINK   = "#FF6482"

BLUE   = AMBER
GOLD   = AMBER

FONT = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif"
MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace"

TERM_TOKENS = {
    "dark": DARK, "panel": S1, "panel2": DARK3, "border": LINE2,
    "ivory": IVORY, "t2": T2, "t3": T3, "row_alt": "#0F0F0F",
    "amber": AMBER, "cyan": CYAN, "green": GREEN, "red": RED, "purple": PURPLE,
    "font": FONT, "mono": MONO,
}

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
    "research":'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M11 8v6M8 11h6"/>',
    "home":'<path d="M3 9.5 12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "star":'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "briefcase":'<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "activity":'<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "bars":'<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "filter":'<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    "scan":'<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><line x1="7" y1="12" x2="17" y2="12"/>',
    "logout":'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "globe":'<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "arrow":'<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    "lock":'<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
}

def icon(name, size=18, color=None):
    c = color or AMBER
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{c}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'style="vertical-align:middle;">{_ICON_PATHS.get(name,"")}</svg>')

def icon_box(name, color=None, size=32):
    c = color or AMBER
    return (f'<div style="width:{size}px;height:{size}px;border-radius:4px;background:{c}14;'
            f'border:1px solid {c}33;display:flex;align-items:center;justify-content:center;'
            f'flex-shrink:0;">{icon(name, int(size * 0.5), c)}</div>')

for k, v in {"logged_in":False,"disclaimer_done":True,"show_login":False,"page":"home",
    "profile":{"name":"Trader","email":"","phone":""},"profile_photo":None,"watchlist":[],
    "admin_watchlist":[],"alerts":{},"alert_fired":set(),"db_loaded":False,"is_admin":False,
    "active_news_source":"admin","show_news_rail":True,"active_security":""}.items():
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

# ════════════════ GLOBAL DESIGN SYSTEM (CSS) ════════════════
# Plain string (not an f-string): every colour is a CSS variable, so other modules can reuse
# var(--amber), var(--line) … in their own HTML.
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{
  --bg:#050505;--s1:#0A0A0A;--s2:#0F0F0F;--s3:#151515;--line:#1F1F1F;--line2:#2B2B2B;
  --tx:#ECECEC;--tx2:#A3A3A3;--tx3:#6B6B6B;--tx4:#454545;
  --amber:#FF9F0A;--amber2:#FFB340;--green:#30D158;--red:#FF453A;--cyan:#5AC8FA;--purple:#BF5AF2;
  --sans:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  --mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
  color-scheme:dark;
}
*,*::before,*::after{box-sizing:border-box;}
html,body,.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:var(--bg) !important;color:var(--tx) !important;font-family:var(--sans) !important;}
*{scrollbar-width:thin;scrollbar-color:#2a2a2a transparent;}
::-webkit-scrollbar{width:8px;height:8px;}
::-webkit-scrollbar-thumb{background:#262626;border-radius:4px;}
::-webkit-scrollbar-thumb:hover{background:#3a3a3a;}
::-webkit-scrollbar-track{background:transparent;}
::selection{background:rgba(255,159,10,.28);color:#fff;}

/* ── chrome we do not want ── */
header[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],#MainMenu,footer,.stDeployButton,
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none !important;}
.block-container{padding:0 18px 56px !important;max-width:100% !important;}
[data-testid="stVerticalBlock"]{gap:.6rem;}

/* ── typography ── */
h1,h2,h3,h4,h5{font-family:var(--sans) !important;letter-spacing:-.01em;color:var(--tx) !important;}
a{color:var(--amber);text-decoration:none;} a:hover{color:var(--amber2);}
[data-testid="stCaptionContainer"],.stCaption{color:var(--tx3) !important;font-size:11px !important;line-height:1.55 !important;}
[data-testid="stWidgetLabel"] p,[data-testid="stWidgetLabel"] label{font:700 9px var(--mono) !important;letter-spacing:1px !important;text-transform:uppercase !important;color:var(--tx3) !important;}
hr{border-color:var(--line) !important;}
iframe{border:none;}

/* ── buttons ── */
.stButton>button,.stDownloadButton>button,[data-testid^="stBaseButton-secondary"],[data-testid="stFormSubmitButton"]>button{
  background:var(--s2) !important;color:var(--tx2) !important;border:1px solid var(--line2) !important;border-radius:4px !important;
  font:600 11px var(--sans) !important;letter-spacing:.3px;min-height:32px !important;box-shadow:none !important;
  transition:border-color .12s ease,color .12s ease,background .12s ease,transform .06s ease;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--amber) !important;color:var(--amber) !important;background:var(--s3) !important;}
.stButton>button:active{transform:translateY(1px);}
.stButton>button:focus-visible{outline:1px solid var(--amber) !important;outline-offset:2px;}
.stButton>button:disabled{opacity:.38 !important;cursor:not-allowed;}
.stButton>button[kind="primary"],button[data-testid^="stBaseButton-primary"],[data-testid="stFormSubmitButton"]>button[kind="primary"]{
  background:var(--amber) !important;color:#000 !important;border-color:var(--amber) !important;font-weight:700 !important;}
.stButton>button[kind="primary"]:hover,button[data-testid^="stBaseButton-primary"]:hover{background:var(--amber2) !important;border-color:var(--amber2) !important;color:#000 !important;}

/* ── inputs ── */
.stTextInput input,.stNumberInput input,.stDateInput input,.stTextArea textarea{
  background:var(--s1) !important;color:var(--tx) !important;border:1px solid var(--line2) !important;border-radius:4px !important;
  font:13px var(--sans) !important;}
.stTextInput input,.stNumberInput input{height:40px !important;}
.stTextInput input:focus,.stNumberInput input:focus,.stDateInput input:focus,.stTextArea textarea:focus{border-color:var(--amber) !important;box-shadow:0 0 0 1px var(--amber) !important;}
input::placeholder,textarea::placeholder{color:#5a5a5a !important;}
[data-baseweb="select"]>div{background:var(--s1) !important;border:1px solid var(--line2) !important;border-radius:4px !important;color:var(--tx) !important;min-height:38px;}
[data-baseweb="select"]>div:hover{border-color:var(--tx3) !important;}
[data-baseweb="select"] input{color:var(--tx) !important;}
[data-baseweb="popover"] ul,[data-baseweb="menu"]{background:var(--s2) !important;border:1px solid var(--line2) !important;}
[data-baseweb="menu"] li:hover,[role="option"]:hover{background:var(--s3) !important;}
[data-baseweb="tag"]{background:rgba(255,159,10,.12) !important;border:1px solid rgba(255,159,10,.35) !important;border-radius:3px !important;}
[data-baseweb="tag"] span{color:var(--amber) !important;font:600 10px var(--mono) !important;}
[data-testid="stFileUploaderDropzone"]{background:var(--s1) !important;border:1px dashed var(--line2) !important;border-radius:4px !important;}
[data-testid="stForm"]{border:1px solid var(--line) !important;border-radius:4px !important;background:var(--s1);padding:12px !important;}
div[class*="st-key-cmdbar"] [data-testid="stForm"]{border:0 !important;background:transparent !important;padding:0 !important;}

/* ── radio as segmented pills ── */
[data-testid="stRadio"] [role="radiogroup"]{gap:6px !important;flex-wrap:wrap;}
[data-testid="stRadio"] label[data-baseweb="radio"]{background:var(--s1);border:1px solid var(--line2);border-radius:4px;padding:5px 11px 5px 9px;margin:0 !important;transition:border-color .12s ease,background .12s ease;}
[data-testid="stRadio"] label[data-baseweb="radio"]:hover{border-color:var(--tx3);}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked){border-color:var(--amber);background:rgba(255,159,10,.10);}
[data-testid="stRadio"] label[data-baseweb="radio"] p{font:700 10px var(--mono) !important;letter-spacing:.7px;text-transform:uppercase;color:var(--tx2) !important;}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) p{color:var(--amber) !important;}

/* ── tabs: underline style ── */
.stTabs [data-baseweb="tab-list"]{background:transparent !important;border:0 !important;border-bottom:1px solid var(--line) !important;gap:2px !important;}
.stTabs [data-baseweb="tab"]{background:transparent !important;border-radius:0 !important;font:700 10px var(--mono) !important;letter-spacing:.9px;text-transform:uppercase;color:var(--tx3) !important;padding:11px 16px !important;height:auto !important;}
.stTabs [data-baseweb="tab"]:hover{color:var(--tx) !important;background:rgba(255,255,255,.03) !important;}
.stTabs [aria-selected="true"]{color:var(--amber) !important;background:transparent !important;}
.stTabs [data-baseweb="tab-highlight"]{background:var(--amber) !important;height:2px !important;}
.stTabs [data-baseweb="tab-border"]{background:var(--line) !important;}

/* ── metrics / expanders / alerts / toasts ── */
[data-testid="stMetric"]{background:var(--s1) !important;border:1px solid var(--line) !important;border-top:2px solid var(--line2) !important;border-radius:0 !important;padding:11px 13px !important;}
[data-testid="stMetricLabel"] p{font:700 9px var(--mono) !important;letter-spacing:1px;text-transform:uppercase;color:var(--tx3) !important;}
[data-testid="stMetricValue"]{font:600 18px var(--mono) !important;font-variant-numeric:tabular-nums;}
[data-testid="stMetricDelta"]{font:600 11px var(--mono) !important;}
[data-testid="stExpander"]{border:1px solid var(--line) !important;border-radius:0 !important;background:var(--s1) !important;}
[data-testid="stExpander"] summary{font:700 10px var(--mono) !important;letter-spacing:.8px;text-transform:uppercase;color:var(--tx2) !important;}
[data-testid="stAlert"]{background:var(--s1) !important;border:1px solid var(--line2) !important;border-left:3px solid var(--cyan) !important;border-radius:0 !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]){border-left-color:var(--amber) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]){border-left-color:var(--red) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]){border-left-color:var(--green) !important;}
[data-testid="stToast"]{background:var(--s2) !important;border:1px solid var(--line2) !important;border-left:3px solid var(--amber) !important;border-radius:4px !important;}
[data-testid="stDataFrame"]{border:1px solid var(--line);}
.stProgress>div>div>div>div{background:var(--amber) !important;}

/* ═════════ application shell ═════════ */
div[class*="st-key-topbar"]{position:sticky;top:0;z-index:100;background:rgba(5,5,5,.97);backdrop-filter:blur(8px);border-top:2px solid var(--amber);border-bottom:1px solid var(--line);margin:0 -18px 0;padding:0 18px;}
.ak-brand{display:flex;align-items:center;gap:10px;height:46px;}
.ak-mark{width:26px;height:26px;background:var(--amber);border-radius:4px;display:flex;align-items:center;justify-content:center;}
.ak-bn{font:800 12px var(--sans);letter-spacing:1.6px;color:var(--tx);}
.ak-bs{font:600 7px var(--mono);letter-spacing:1.6px;color:var(--tx3);margin-top:2px;}
div[class*="st-key-topnav"] .stButton>button{background:transparent !important;border:0 !important;border-bottom:2px solid transparent !important;border-radius:0 !important;color:var(--tx3) !important;font:700 10px var(--mono) !important;letter-spacing:.9px;min-height:46px !important;text-transform:uppercase;padding:0 4px !important;}
div[class*="st-key-topnav"] .stButton>button:hover{color:var(--tx) !important;background:rgba(255,255,255,.035) !important;border-bottom-color:var(--line2) !important;}
div[class*="st-key-topnav"] .stButton>button[kind="primary"],div[class*="st-key-topnav"] button[data-testid^="stBaseButton-primary"]{background:transparent !important;color:var(--amber) !important;border-bottom:2px solid var(--amber) !important;}
div[class*="st-key-topnav"] [data-testid="stHorizontalBlock"]{gap:0 !important;}
div[class*="st-key-topuser"] .stButton>button{min-height:28px !important;font:700 9px var(--mono) !important;letter-spacing:.8px;}
.ak-chips{display:flex;align-items:center;justify-content:flex-end;gap:7px;height:46px;}
.ak-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border:1px solid var(--line2);border-radius:999px;font:700 9px var(--mono);letter-spacing:.8px;color:var(--tx2);white-space:nowrap;}
.ak-chip.live{color:var(--green);border-color:rgba(48,209,88,.35);background:rgba(48,209,88,.07);}
.ak-chip.pre{color:var(--amber);border-color:rgba(255,159,10,.35);background:rgba(255,159,10,.07);}
.ak-chip.closed{color:var(--tx3);}
.ak-dot{width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block;}
.ak-chip.live .ak-dot{box-shadow:0 0 8px var(--green);animation:akpulse 2s ease-in-out infinite;}
@keyframes akpulse{0%,100%{opacity:1}50%{opacity:.35}}
.ak-av{width:22px;height:22px;border-radius:50%;background:var(--s3);border:1px solid var(--line2);display:inline-flex;align-items:center;justify-content:center;font:700 10px var(--sans);color:var(--amber);}

.ak-ph{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;padding:18px 2px 14px;border-bottom:1px solid var(--line);margin-bottom:12px;}
.ak-ph .k{font:700 9px var(--mono);letter-spacing:1.6px;color:var(--amber);}
.ak-ph .t{font:800 24px var(--sans);letter-spacing:-.4px;color:var(--tx);margin-top:4px;}
.ak-ph .d{font:12px var(--sans);color:var(--tx3);margin-top:3px;}
.ak-ph .r{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;}

/* index strips */
.ak-strips{border:1px solid var(--line);background:var(--s1);margin:12px 0 0;}
.ak-strip{display:grid;grid-template-columns:78px repeat(6,minmax(0,1fr));border-bottom:1px solid var(--line);}
.ak-strip:last-child{border-bottom:0;}
.ak-sl{display:flex;align-items:center;padding:0 12px;font:700 9px var(--mono);letter-spacing:1.2px;color:var(--tx3);background:var(--s2);}
.ak-ix{position:relative;padding:9px 12px 11px;border-left:1px solid var(--line);min-width:0;}
.ak-ix .n{font:600 9px var(--mono);letter-spacing:.6px;color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ak-ix .p{font:600 14px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:3px;white-space:nowrap;}
.ak-ix .c{position:absolute;right:10px;top:10px;font:700 10px var(--mono);font-variant-numeric:tabular-nums;padding:1px 6px;border-radius:3px;white-space:nowrap;}
.ak-ix .m{position:absolute;left:0;bottom:0;height:2px;opacity:.8;}
.ak-up{color:var(--green);background:rgba(48,209,88,.10);} .ak-dn{color:var(--red);background:rgba(255,69,58,.10);} .ak-fl{color:var(--tx3);background:rgba(255,255,255,.04);}

/* command bar */
.ak-cmd-h{display:flex;justify-content:space-between;align-items:baseline;margin:14px 2px 6px;}
.ak-cmd-h b{font:700 9px var(--mono);letter-spacing:1.4px;color:var(--amber);}
.ak-cmd-h span{font:10px var(--sans);color:var(--tx3);}

/* KPI cards */
.ak-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(176px,1fr));gap:8px;margin:12px 0;}
.ak-kpi{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--a,var(--line2));padding:12px 14px;min-width:0;}
.ak-kpi .l{font:700 8px var(--mono);letter-spacing:1.1px;color:var(--tx3);text-transform:uppercase;}
.ak-kpi .v{font:700 20px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:6px;white-space:nowrap;}
.ak-kpi .s{font:10px var(--mono);color:var(--tx3);margin-top:4px;}

/* panels */
.ak-grid3{display:grid;grid-template-columns:1.1fr 1.1fr .8fr;gap:8px;margin-top:4px;}
@media(max-width:1300px){.ak-grid3{grid-template-columns:1fr 1fr;}.ak-grid3>.ak-panel:last-child{grid-column:1/-1;}}
@media(max-width:800px){.ak-grid3{grid-template-columns:1fr;}.ak-strip{grid-template-columns:60px repeat(3,minmax(0,1fr));}.ak-strip .ak-ix:nth-child(n+5){display:none;}}
.ak-panel{background:var(--s1);border:1px solid var(--line);min-width:0;overflow:hidden;}
.ak-pt{height:38px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:var(--s2);}
.ak-pt b{font:700 9px var(--mono);letter-spacing:1.2px;color:var(--amber);}
.ak-pt span{font:600 9px var(--mono);color:var(--tx3);}
.ak-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:9px 12px;border-bottom:1px solid #141414;font:11px var(--mono);}
.ak-row:last-child{border-bottom:0;}
.ak-row:hover{background:rgba(255,255,255,.02);}
.ak-row .s{font:700 11px var(--mono);color:var(--tx);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ak-row .p{font-variant-numeric:tabular-nums;color:var(--tx2);text-align:right;}
.ak-row .c{font-weight:700;font-variant-numeric:tabular-nums;min-width:68px;text-align:right;padding:2px 7px;border-radius:3px;}
.ak-empty{padding:18px 14px;font:11px var(--sans);color:var(--tx3);line-height:1.7;}
.ak-empty b{color:var(--tx2);}
.ak-bar{display:flex;height:6px;margin:12px;background:#151515;}
.ak-bar i{display:block;height:100%;}

/* section titles */
.ak-sec{display:flex;align-items:center;gap:12px;margin:26px 0 12px;}
.ak-sec:before{content:"";width:3px;height:14px;background:var(--amber);}
.ak-sec b{font:700 11px var(--mono);letter-spacing:1.6px;color:var(--tx);text-transform:uppercase;white-space:nowrap;}
.ak-sec span{font:11px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ak-sec i{flex:1;height:1px;background:var(--line);}

/* workspace cards */
div[class*="st-key-ak_mod_"]{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:14px 14px 4px;transition:border-color .14s ease,background .14s ease;gap:.2rem;}
div[class*="st-key-ak_mod_"]:hover{background:var(--s2);border-color:var(--line2);border-top-color:var(--amber);}
div[class*="st-key-ak_mod_"] .stButton>button{background:transparent !important;border:0 !important;border-top:1px solid var(--line) !important;border-radius:0 !important;color:var(--amber) !important;justify-content:flex-start !important;padding:10px 0 6px !important;font:700 10px var(--mono) !important;letter-spacing:1.1px;min-height:36px !important;}
div[class*="st-key-ak_mod_"] .stButton>button:hover{color:var(--amber2) !important;}
.ak-mc{display:flex;gap:12px;align-items:flex-start;}
.ak-mc .t{font:700 13px var(--sans);color:var(--tx);display:flex;align-items:center;gap:8px;}
.ak-mc .t em{font:700 8px var(--mono);letter-spacing:.9px;font-style:normal;color:var(--tx3);border:1px solid var(--line2);padding:1px 6px;border-radius:3px;}
.ak-mc .d{font:11px var(--sans);color:var(--tx3);line-height:1.6;margin-top:5px;min-height:36px;}

/* security page */
.ak-sh{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--amber);padding:16px 18px;}
.ak-sh .sym{display:inline-block;font:800 10px var(--mono);letter-spacing:1px;color:#000;background:var(--amber);padding:2px 8px;border-radius:3px;}
.ak-sh .nm{font:800 22px var(--sans);letter-spacing:-.3px;color:var(--tx);margin-top:8px;}
.ak-sh .tg{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;}
.ak-tag{font:600 9px var(--mono);letter-spacing:.7px;color:var(--tx2);border:1px solid var(--line2);padding:2px 8px;border-radius:3px;text-transform:uppercase;}
.ak-sh .px{text-align:right;}
.ak-sh .px .v{font:700 30px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);}
.ak-sh .px .c{display:inline-block;margin-top:6px;font:700 12px var(--mono);padding:3px 9px;border-radius:3px;}
.ak-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));border:1px solid var(--line);border-top:0;background:var(--s1);}
.ak-st{padding:10px 14px;border-right:1px solid var(--line);min-width:0;}
.ak-st:last-child{border-right:0;}
.ak-st .l{font:700 8px var(--mono);letter-spacing:1px;color:var(--tx3);text-transform:uppercase;}
.ak-st .v{font:600 13px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:4px;white-space:nowrap;}
.ak-rng{margin:12px 2px 2px;}
.ak-rng .t{display:flex;justify-content:space-between;font:600 9px var(--mono);color:var(--tx3);letter-spacing:.6px;margin-bottom:6px;}
.ak-rng .b{position:relative;height:5px;background:linear-gradient(90deg,rgba(255,69,58,.35),#262626 50%,rgba(48,209,88,.35));border-radius:3px;}
.ak-rng .b i{position:absolute;top:-4px;width:3px;height:13px;background:var(--amber);border-radius:2px;transform:translateX(-1px);box-shadow:0 0 8px rgba(255,159,10,.6);}
.ak-ov{background:var(--s1);border:1px solid var(--line);}
.ak-ov div{display:flex;justify-content:space-between;padding:8px 12px;border-bottom:1px solid #141414;font-size:11px;}
.ak-ov div:last-child{border-bottom:0;}
.ak-ov span{color:var(--tx3);} .ak-ov b{font:500 11px var(--mono);color:var(--tx);}
.ak-desc{background:var(--s1);border:1px solid var(--line);padding:12px;font-size:11px;color:var(--tx3);line-height:1.7;}
.ak-pt2{font:700 9px var(--mono);letter-spacing:1.3px;color:var(--amber);border-bottom:1px solid var(--line);padding:10px 0 7px;margin:4px 0 8px;text-transform:uppercase;}

/* scanner cards */
.ak-sc{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--c,var(--line2));padding:12px;text-align:center;margin-bottom:4px;}
.ak-sc b{color:var(--tx);font-size:13px;}
.ak-sc .px{font:600 14px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin:8px 0 4px;}
.ak-sc .rs{font:600 10px var(--mono);}

/* news rail + footer + misc */
.news-rail{background:var(--s1);border:1px solid var(--line);}
.news-rail-collapsed{padding:9px 12px;border:1px solid var(--line);background:var(--s1);font:700 9px var(--mono);letter-spacing:1px;color:var(--tx3);}
.news-rail-collapsed span{color:var(--tx4);margin-left:6px;}
.ak-foot{margin-top:34px;padding:14px 2px 0;border-top:1px solid var(--line);font:10px var(--sans);color:var(--tx3);line-height:1.7;display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;}
.term-panel{background:var(--s1);border:1px solid var(--line);padding:14px;}
.panel-title{font:700 9px var(--mono);letter-spacing:1.3px;color:var(--amber);border-bottom:1px solid var(--line);padding:9px 0 7px;margin-bottom:8px;text-transform:uppercase;}
.pulse-dot{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;box-shadow:0 0 8px var(--green);}
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)

# ── Navigation transition only: cover stale Streamlit content during reruns ──
# UI-only fix: no changes to authentication, routing, data or module code.
def _navigation_transition():
    components.html("""
    <script>
    (function () {
      try {
        const w = window.parent;
        const d = w.document;
        const ID = 'arka-navigation-loading-overlay';
        // Keep the cover in place briefly after the destination page has rendered.
        // Removing it immediately can expose Streamlit's previous DOM while React
        // finishes replacing the old page with the new one.
        const old = d.getElementById(ID);
        if (old) {
          if (old.dataset.arkaRemovalScheduled !== '1') {
            old.dataset.arkaRemovalScheduled = '1';
            w.setTimeout(() => {
              const current = d.getElementById(ID);
              if (current) current.remove();
            }, 650);
          }
        }

        if (!w.__arkaNavigationTransitionInstalled) {
          w.__arkaNavigationTransitionInstalled = true;
          const show = () => {
            let overlay = d.getElementById(ID);
            if (!overlay) {
              overlay = d.createElement('div');
              overlay.id = ID;
              overlay.innerHTML = '<div class="arka-loading-mark">A</div><div class="arka-loading-title">ARKA TRADES</div><div class="arka-loading-sub">Opening workspace…</div><div class="arka-loading-line"><i></i></div>';
              overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#050505;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:Inter,system-ui,sans-serif;color:#ececec;';
              const style = d.createElement('style');
              style.textContent = '#'+ID+' .arka-loading-mark{width:38px;height:38px;background:#ff9f0a;color:#000;border-radius:4px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:20px;margin-bottom:14px}#'+ID+' .arka-loading-title{font-weight:800;font-size:12px;letter-spacing:2px}#'+ID+' .arka-loading-sub{font-size:11px;color:#777;margin-top:9px}#'+ID+' .arka-loading-line{width:150px;height:2px;background:#1f1f1f;margin-top:20px;overflow:hidden}#'+ID+' .arka-loading-line i{display:block;width:40%;height:100%;background:#ff9f0a;animation:arkaLoad 1s ease-in-out infinite alternate}@keyframes arkaLoad{from{transform:translateX(-10%)}to{transform:translateX(160%)}}';
              overlay.appendChild(style);
              d.body.appendChild(overlay);
            }
          };
          const isNavigation = (el) => {
            const b = el && el.closest ? el.closest('button') : null;
            if (!b) return false;
            const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toUpperCase();
            return /^(HOME|MONEY FLOW|BREADTH|WATCHLIST|PORTFOLIO|ALERTS|SCANNER|SCREENER|RESEARCH|ARKA AI|SIGN OUT|SIGN IN|GET STARTED →|SIGN IN TO ARKA →|← BACK TO HOME)$/.test(t);
          };
          d.addEventListener('click', function (e) {
            if (isNavigation(e.target)) show();
          }, true);
          d.addEventListener('submit', function (e) {
            const f = e.target;
            if (f && f.closest && f.closest('[class*="st-key-loginwrap"]')) show();
          }, true);
        }
      } catch (e) {
        // If the browser isolates component frames, normal Streamlit navigation still works.
      }
    })();
    </script>
    """, height=0, width=0)

# ── Helpers ──────────────────────────────────────────────────
def parse_csv(file):
    """
    Strips trailing exchange suffixes (.NS/.BO/.NSE/.BSE) so
    TradingView-style exports don't get double-suffixed downstream.
    """
    try: df = pd.read_csv(file, header=None)
    except Exception: return []
    syms = []
    for v in df.iloc[:,0].astype(str):
        v = v.strip()
        if ':' in v: v = v.split(':')[1]
        v = v.split(',')[0].strip()
        if v and v.lower() != 'nan':
            v = v.upper()
            for suffix in ('.NS', '.BO', '.NSE', '.BSE'):
                if v.endswith(suffix):
                    v = v[: -len(suffix)]
                    break
            syms.append(v)
    return list(dict.fromkeys(syms))

# Market data functions live in data_engine.py — parallel, batched and cached.

# Index tickers. Each entry is (label, (primary, fallback, fallback...)).
MIDCAP_CANDIDATES = ["NIFTYMDCP100.NS", "^NSEMDCP50", "NIFTY_MIDCAP_100.NS", "^CRSMID"]
SMALLCAP_CANDIDATES = ["NIFTYSMLCAP250.NS", "NIFTYSMCP100.NS", "^CNXSC", "^CNXSMALLCAP"]
SP500_CANDIDATES    = ["^GSPC"]
DOWJONES_CANDIDATES = ["^DJI"]
GOLD_CANDIDATES     = ["GC=F"]

INDIA_ITEMS = (("NIFTY 50", ("^NSEI",)), ("BANK NIFTY", ("^NSEBANK",)), ("SENSEX", ("^BSESN",)),
               ("NIFTY IT", ("^CNXIT",)), ("NIFTY AUTO", ("^CNXAUTO",)), ("MIDCAP 100", tuple(MIDCAP_CANDIDATES)))
GLOBAL_ITEMS = (("S&P 500", ("^GSPC",)), ("NASDAQ", ("^IXIC",)), ("DOW", ("^DJI",)),
                ("DAX", ("^GDAXI",)), ("FTSE", ("^FTSE",)), ("NIKKEI", ("^N225",)))

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

def section(title, sub="", accent=None):
    """Section heading used across the app: accent bar · TITLE · muted note · hairline."""
    note = f"<span>{sub}</span>" if sub else ""
    st.markdown(f'<div class="ak-sec"><b>{title}</b>{note}<i></i></div>', unsafe_allow_html=True)

def change_pill(chg):
    cls = "ak-up" if chg >= 0 else "ak-dn"
    arrow = "▲" if chg >= 0 else "▼"
    return (f'<span class="{cls}" style="font-family:{MONO};font-size:11px;font-weight:700;'
            f'padding:2px 7px;border-radius:3px;">{arrow} {abs(chg):.2f}%</span>')

def sparkline(values, color=None, w=110, h=30):
    if not values or len(values) < 2: return ""
    color = color or (GREEN if values[-1] >= values[0] else RED)
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i/(len(values)-1)*w:.1f},{h-2-((v-lo)/rng)*(h-6):.1f}" for i, v in enumerate(values))
    return (f'<svg width="{w}" height="{h}" style="display:block;margin:0 auto;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')

def checkline(text, c=None):
    return (f'<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:12px;">'
            f'<span style="flex-shrink:0;margin-top:2px;">{icon("check", 16, c or GREEN)}</span>'
            f'<span style="font-size:14px;color:{IVORY};line-height:1.6;">{text}</span></div>')

def _inr_short(x):
    """₹1.25 Cr / ₹3.40 L / ₹45,200 — Indian compact format."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    a, sign = abs(x), ("-" if x < 0 else "")
    if a >= 1e7: return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5: return f"{sign}₹{a / 1e5:.2f} L"
    return f"{sign}₹{a:,.0f}"

def _market_status():
    """NSE cash-market session status in IST (public holidays are not accounted for)."""
    now = datetime.now(IST)
    t = now.hour * 60 + now.minute
    stamp = now.strftime("%H:%M IST")
    if now.weekday() >= 5:
        return "MARKET CLOSED", "closed", f"Weekend · {stamp}"
    if 9 * 60 <= t < 9 * 60 + 15:
        return "PRE-OPEN", "pre", f"Opens 09:15 · {stamp}"
    if 9 * 60 + 15 <= t <= 15 * 60 + 30:
        return "MARKET OPEN", "live", f"Closes 15:30 · {stamp}"
    return "MARKET CLOSED", "closed", f"{'Opens 09:15' if t < 9 * 60 else 'Closed 15:30'} · {stamp}"

# ═══════════════════════════════════════════════════════════════════
# MARKET MOOD INDEX (MMI)
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
    pattern = re.compile(
        r'(\d{1,3}\.\d{1,2})\s*(?:<[^>]+>\s*)*Updated',
        re.MULTILINE
    )
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
                "fetched_at_ist": fetched.astimezone(IST)}
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
        resp = session.get(_MMI_URL, timeout=4)          # short timeout: the home page must never wait on this
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
# LANDING + LOGIN — same palette as the terminal (amber on graphite)
# ════════════════════════════════════════════════════════════
LANDING_CSS = """
<style>
.stApp{background:radial-gradient(ellipse 70% 38% at 50% -6%,rgba(255,159,10,.08),transparent),#050505 !important;}
.block-container{padding-top:0 !important;max-width:1440px !important;padding-left:20px !important;padding-right:20px !important;}
[data-testid="stVerticalBlock"]{gap:.4rem;}
iframe{display:block;border:none;}
div[class*="st-key-cta_main"],div[class*="st-key-cta_bottom"]{display:flex;justify-content:center;}
div[class*="st-key-cta_main"]{margin-top:-128px;position:relative;z-index:10;}
div[class*="st-key-cta_main"] button,div[class*="st-key-cta_bottom"] button{padding:0 44px !important;min-height:48px !important;text-transform:uppercase !important;font:800 12px var(--sans) !important;letter-spacing:1.3px !important;border-radius:4px !important;}
div[class*="st-key-loginwrap"] [data-testid="stForm"]{padding:26px !important;background:var(--s1) !important;border:1px solid var(--line2) !important;border-top:2px solid var(--amber) !important;border-radius:4px !important;}

.lp-nav{position:sticky;top:0;z-index:60;display:flex;align-items:center;justify-content:space-between;height:58px;margin:0 -20px;padding:0 28px;background:rgba(5,5,5,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);}
.lp-brand{display:flex;align-items:center;gap:10px;}
.lp-mark{width:28px;height:28px;border-radius:5px;background:var(--amber);display:flex;align-items:center;justify-content:center;}
.lp-bn{font:800 13px var(--sans);letter-spacing:1.8px;color:var(--tx);}
.lp-links{display:flex;gap:26px;align-items:center;}
.lp-links a{font:600 12px var(--sans);color:var(--tx2);} .lp-links a:hover{color:var(--tx);}
.lp-links a.go{font:800 10px var(--mono);letter-spacing:1.1px;color:#000;background:var(--amber);padding:8px 16px;border-radius:4px;text-transform:uppercase;}
.lp-links a.go:hover{background:var(--amber2);color:#000;}
@media(max-width:800px){.lp-links a:not(.go){display:none;}}

.lp-band{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));border:1px solid var(--line);background:var(--s1);margin:34px 0 0;}
.lp-stat{padding:22px 24px;border-right:1px solid var(--line);}
.lp-stat:last-child{border-right:0;}
.lp-stat .n{font:700 28px var(--mono);color:var(--amber);font-variant-numeric:tabular-nums;}
.lp-stat .l{font:600 12px var(--sans);color:var(--tx3);margin-top:4px;}

.lp-h{text-align:center;margin:84px auto 30px;max-width:760px;}
.lp-h .e{font:700 10px var(--mono);letter-spacing:2.2px;color:var(--amber);text-transform:uppercase;}
.lp-h .t{font:800 34px var(--sans);letter-spacing:-.8px;color:var(--tx);margin-top:10px;line-height:1.15;}
.lp-h .s{font:14px var(--sans);color:var(--tx3);margin-top:12px;line-height:1.7;}
.lp-feats{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:10px;}
.lp-card{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:22px;transition:border-color .15s ease,background .15s ease;}
.lp-card:hover{border-top-color:var(--amber);background:var(--s2);}
.lp-card .t{font:700 15px var(--sans);color:var(--tx);margin:14px 0 8px;}
.lp-card .d{font:13px var(--sans);color:var(--tx3);line-height:1.7;}

.lp-dd .e{font:700 10px var(--mono);letter-spacing:2px;color:var(--amber);text-transform:uppercase;margin-bottom:10px;}
.lp-dd .t{font:800 30px var(--sans);letter-spacing:-.7px;color:var(--tx);line-height:1.2;margin-bottom:18px;}
.lp-mock{background:var(--s1);border:1px solid var(--line2);border-top:2px solid var(--amber);border-radius:2px;overflow:hidden;}
.lp-mh{display:flex;align-items:center;gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--s2);}
.lp-mh i{width:8px;height:8px;border-radius:50%;background:#2e2e2e;display:inline-block;}
.lp-mh span{font:700 9px var(--mono);letter-spacing:1.2px;color:var(--tx3);margin-left:8px;flex:1;}
.lp-mh em{font:700 8px var(--mono);letter-spacing:1px;font-style:normal;color:var(--tx4);border:1px solid var(--line2);padding:1px 7px;border-radius:3px;}
.lp-mb{padding:14px;}
.lp-lanes{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.lp-lane{border:1px solid var(--line);border-top:2px solid var(--c);padding:10px 12px;background:#080808;min-height:92px;}
.lp-lane b{font:800 9px var(--mono);letter-spacing:1px;color:var(--c);}
.lp-lane small{display:block;font:10px var(--sans);color:var(--tx3);margin:3px 0 8px;}
.lp-pill{display:inline-block;margin:0 5px 5px 0;padding:4px 9px;border:1px solid var(--line2);border-left:3px solid var(--c);border-radius:4px;font:600 11px var(--sans);color:var(--tx);}
.lp-pill em{font:800 8px var(--mono);font-style:normal;color:var(--green);margin-left:6px;}
.lp-k3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;}
.lp-k{border:1px solid var(--line);border-top:2px solid var(--line2);padding:10px 12px;background:#080808;}
.lp-k .l{font:700 8px var(--mono);letter-spacing:1px;color:var(--tx3);}
.lp-k .v{font:700 16px var(--mono);color:var(--tx);margin-top:5px;}
.lp-bars div{display:flex;align-items:center;gap:10px;margin:7px 0;font:11px var(--sans);color:var(--tx2);}
.lp-bars span{width:130px;flex-shrink:0;}
.lp-bars i{display:block;height:6px;background:var(--amber);}
.lp-bars em{font:600 10px var(--mono);font-style:normal;color:var(--tx3);}
.lp-rules{background:#080808;border:1px solid var(--line);padding:14px;font:12px var(--mono);color:var(--tx3);line-height:2.05;}
.lp-rules .y{color:var(--green);} .lp-rules .n{color:var(--red);}

.lp-style{border:1px solid var(--line);border-top:2px solid var(--c);background:var(--s1);padding:24px;min-height:268px;}
.lp-style .t{font:800 16px var(--sans);color:var(--tx);margin:14px 0 16px;}
.lp-steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;}
.lp-step{border:1px solid var(--line);background:var(--s1);padding:24px;position:relative;}
.lp-step .no{font:700 11px var(--mono);letter-spacing:2px;color:var(--amber);}
.lp-step .t{font:800 16px var(--sans);color:var(--tx);margin:10px 0 8px;}
.lp-step .d{font:13px var(--sans);color:var(--tx3);line-height:1.7;}
.lp-cta{margin:84px 0 18px;text-align:center;padding:52px 20px 34px;border:1px solid var(--line2);border-top:2px solid var(--amber);background:var(--s1);}
.lp-cta .t{font:800 32px var(--sans);letter-spacing:-.7px;color:var(--tx);}
.lp-cta .s{font:14px var(--sans);color:var(--tx3);margin-top:10px;}
.lp-foot{text-align:center;padding:40px 0 34px;font:12px var(--sans);color:var(--tx3);line-height:1.8;}
.lp-foot small{display:block;font-size:11px;opacity:.7;margin-top:6px;}

.lg-left{padding:30px 6px 0;}
.lg-left .t{font:800 36px var(--sans);letter-spacing:-.9px;color:var(--tx);line-height:1.12;margin:26px 0 14px;}
.lg-left .s{font:14px var(--sans);color:var(--tx3);line-height:1.7;max-width:420px;}
.lg-pt{display:flex;gap:12px;align-items:flex-start;margin-top:20px;}
.lg-pt b{display:block;font:700 13px var(--sans);color:var(--tx);} .lg-pt span{font:12px var(--sans);color:var(--tx3);line-height:1.6;}
.lg-card-h{margin-bottom:14px;} .lg-card-h b{font:800 20px var(--sans);color:var(--tx);} .lg-card-h span{display:block;font:12px var(--sans);color:var(--tx3);margin-top:4px;}
</style>
"""

if not st.session_state.logged_in:

    if st.query_params.get("login") == "1":
        st.session_state.show_login = True
        st.query_params.clear()

    st.markdown(LANDING_CSS, unsafe_allow_html=True)

    def _lp_nav():
        st.markdown(
            f'<div class="lp-nav"><div class="lp-brand"><div class="lp-mark">{icon("trend", 16, "#000")}</div>'
            '<div class="lp-bn">ARKA TRADES</div></div>'
            '<div class="lp-links"><a href="#lp-platform">Platform</a><a href="#lp-rotation">Sector rotation</a>'
            '<a href="#lp-start">Get started</a><a class="go" href="?login=1" target="_self">Sign in</a></div></div>',
            unsafe_allow_html=True)

    # ───────────── login ─────────────
    if st.session_state.show_login:
        _lp_nav()
        _, wrap, _ = st.columns([0.35, 3.3, 0.35])
        with wrap:
            left, right = st.columns([1.15, 1], gap="large")
            with left:
                st.markdown(f"""<div class="lg-left">
                    <div class="lp-brand"><div class="lp-mark">{icon("trend", 16, "#000")}</div><div class="lp-bn">ARKA TRADES</div></div>
                    <div class="t">Your market terminal,<br>one sign-in away.</div>
                    <div class="s">Pick up where you left off: your watchlist, portfolio, alerts and sector radar are synced to the cloud.</div>
                    <div class="lg-pt">{icon_box("activity", AMBER, 34)}<div><b>Sector rotation radar</b><span>See which sectors are turning up, with RSI, EMA position and relative strength.</span></div></div>
                    <div class="lg-pt">{icon_box("briefcase", CYAN, 34)}<div><b>Portfolio analytics</b><span>Live P&amp;L, sector exposure, risk and an automatic health check.</span></div></div>
                    <div class="lg-pt">{icon_box("bell", GREEN, 34)}<div><b>Alerts on Telegram</b><span>PDH, PDL and price triggers delivered the moment they fire.</span></div></div>
                </div>""", unsafe_allow_html=True)
            with right:
                with st.container(key="loginwrap"):
                    with st.form("lf"):
                        st.markdown('<div class="lg-card-h"><b>Member login</b><span>Sign in to access your terminal</span></div>', unsafe_allow_html=True)
                        u = st.text_input("Username", placeholder="Enter username")
                        p = st.text_input("Password", placeholder="Enter password", type="password")
                        ok = st.form_submit_button("Sign in", use_container_width=True, type="primary")
                        ph = st.empty()
                        if ok:
                            if u.strip()==_ADMIN_USER and p.strip()==_ADMIN_PASS:
                                ph.success("Welcome, Admin!")
                                st.session_state.logged_in = True; st.session_state.is_admin = True; st.rerun()
                            elif u.strip().lower()==str(_MEMBER_USER).lower() and p.strip().lower()==str(_MEMBER_PASS).lower():
                                ph.success("Login successful.")
                                st.session_state.logged_in = True; st.session_state.is_admin = False; st.rerun()
                            else:
                                ph.error("Invalid username or password.")
                    if st.button("← Back to home", use_container_width=True, key="back_home"):
                        st.session_state.show_login = False; st.rerun()
                    st.caption("Educational market analytics. Not SEBI registered. Not investment advice.")
        _navigation_transition()
        st.stop()

    # ───────────── landing ─────────────
    _lp_nav()
    hero_html = """
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;}html,body{height:100%;}
body{background:#050505;font-family:'Inter',sans-serif;overflow:hidden;color:#fff;}
.hero{position:relative;width:100%;height:100%;min-height:640px;overflow:hidden;}
#bgvid{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.34;filter:grayscale(.4);}
.ov{position:absolute;inset:0;background:radial-gradient(ellipse 70% 55% at 50% 36%,rgba(255,159,10,.12),transparent 70%),linear-gradient(to bottom,rgba(5,5,5,.6) 0%,rgba(5,5,5,.15) 45%,#050505 100%);}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:64px 64px;-webkit-mask-image:radial-gradient(ellipse 80% 70% at 50% 38%,#000 25%,transparent 80%);mask-image:radial-gradient(ellipse 80% 70% at 50% 38%,#000 25%,transparent 80%);}
.content{position:relative;z-index:10;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:40px 22px 150px;}
.pill{display:inline-flex;align-items:center;gap:9px;padding:6px 14px;border:1px solid rgba(255,159,10,.35);background:rgba(255,159,10,.07);border-radius:999px;font:700 10px 'JetBrains Mono',monospace;letter-spacing:1.6px;color:#FF9F0A;text-transform:uppercase;margin-bottom:26px;}
.pill i{width:6px;height:6px;border-radius:50%;background:#30D158;box-shadow:0 0 8px #30D158;}
h1{font-weight:800;letter-spacing:-.035em;color:#fff;font-size:clamp(36px,6.4vw,76px);line-height:1.02;max-width:980px;}
h1 span{color:#FF9F0A;}
.sub{font-size:clamp(14px,1.6vw,17px);color:rgba(255,255,255,.66);max-width:640px;line-height:1.7;margin:24px 0 0;}
.chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:28px;}
.chips span{padding:6px 13px;border:1px solid rgba(255,255,255,.14);border-radius:4px;background:rgba(255,255,255,.035);font:600 11px 'Inter',sans-serif;color:rgba(255,255,255,.78);}
</style></head><body>
<div class="hero">
  <video id="bgvid" autoplay muted loop playsinline></video>
  <div class="ov"></div><div class="grid"></div>
  <div class="content">
    <div class="pill"><i></i>NSE · Live market terminal</div>
    <h1>Trade with context,<br>not <span>guesswork.</span></h1>
    <p class="sub">Sector rotation radar, portfolio analytics, watchlists, scanners and AI chart analysis in one fast, cloud-synced workspace built for Indian traders.</p>
    <div class="chips"><span>Sector rotation radar</span><span>Portfolio analytics</span><span>AI chart analysis</span><span>Telegram alerts</span></div>
  </div>
</div>
<script>
const v=document.getElementById('bgvid');
const s='https://stream.mux.com/tLkHO1qZoaaQOUeVWo8hEBeGQfySP02EPS02BmnNFyXys.m3u8';
if(window.Hls&&Hls.isSupported()){const h=new Hls({enableWorker:false});h.loadSource(s);h.attachMedia(v);}
else if(v.canPlayType('application/vnd.apple.mpegurl')){v.src=s;}
</script></body></html>
"""
    components.html(hero_html, height=680, scrolling=False)
    if st.button("Get started →", type="primary", key="cta_main"):
        st.session_state.show_login = True
        st.rerun()

    # stats band (figures as originally published for the product)
    st.markdown(
        '<div class="lp-band">'
        '<div class="lp-stat"><div class="n">2000+</div><div class="l">NSE stocks covered</div></div>'
        '<div class="lp-stat"><div class="n">&lt;90s</div><div class="l">Scan time after pre-filter</div></div>'
        '<div class="lp-stat"><div class="n">10s</div><div class="l">Live price refresh</div></div>'
        '<div class="lp-stat"><div class="n">24/7</div><div class="l">Cloud-synced workspace</div></div></div>',
        unsafe_allow_html=True)

    # platform grid
    st.markdown('<div id="lp-platform"></div><div class="lp-h"><div class="e">The platform</div>'
                '<div class="t">Everything a market desk needs, in one terminal.</div>'
                '<div class="s">Nine connected workspaces share one watchlist, one portfolio and one search, so context follows you from idea to analysis.</div></div>',
                unsafe_allow_html=True)
    feats = [
        ("activity", AMBER, "Money Flow", "Sector rotation radar with RSI, EMA position and relative strength against the market, mid-caps and small-caps."),
        ("briefcase", CYAN, "Portfolio", "Record buy lots and get live P&amp;L, XIRR, sector exposure, risk metrics and an automatic health check."),
        ("star", AMBER, "Watchlist", "Track NSE stocks with live price, RSI and PDH / PDL status. Saved permanently and synced everywhere."),
        ("filter", PURPLE, "Scanner &amp; Screener", "Scan your own lists or build rule-based screens across the NSE universe to find candidates fast."),
        ("brain", PURPLE, "Arka AI", "Vision-based chart analysis checked against your own trading rules and reference charts."),
        ("bell", GREEN, "Alerts", "PDH, PDL and custom price alerts pushed to Telegram the moment your level breaks."),
    ]
    st.markdown('<div class="lp-feats">' + "".join(
        f'<div class="lp-card">{icon_box(ic, c, 38)}<div class="t">{t}</div><div class="d">{d}</div></div>' for ic, c, t, d in feats) + '</div>',
        unsafe_allow_html=True)

    # deep dive 1 — sector rotation
    st.markdown('<div id="lp-rotation" style="height:70px"></div>', unsafe_allow_html=True)
    dd1, dd2 = st.columns([1, 1.05], gap="large")
    with dd1:
        st.markdown(f"""<div class="lp-dd" style="padding-top:18px;"><div class="e">Sector rotation</div>
            <div class="t">See which sectors are turning up, before the crowd.</div>
            {checkline("Every sector is placed in a plain-English lane: leading, improving, fading or weak")}
            {checkline("Transparent 0-100 score built from trend, momentum, relative strength, breadth and flow")}
            {checkline("RSI plus green / red EMA 10, 20, 50, 200 chips and distance from each average")}
            {checkline("Compare any sector with the average stock, NIFTY 50, Midcap 100 and Smallcap 100")}</div>""", unsafe_allow_html=True)
    with dd2:
        st.markdown("""<div class="lp-mock"><div class="lp-mh"><i></i><i></i><i></i><span>MONEY FLOW · ROTATION BOARD</span><em>ILLUSTRATIVE</em></div>
            <div class="lp-mb"><div class="lp-lanes">
              <div class="lp-lane" style="--c:#5AC8FA"><b>IMPROVING</b><small>Behind, but catching up</small><span class="lp-pill" style="--c:#30D158">Capital Goods<em>★ ROTATING IN</em></span><span class="lp-pill" style="--c:#5AC8FA">Realty</span></div>
              <div class="lp-lane" style="--c:#30D158"><b>LEADING</b><small>Beating the market, speeding up</small><span class="lp-pill" style="--c:#30D158">Banks</span><span class="lp-pill" style="--c:#30D158">Insurance</span></div>
              <div class="lp-lane" style="--c:#FF453A"><b>WEAK</b><small>Behind and falling further</small><span class="lp-pill" style="--c:#FF453A">Utilities</span><span class="lp-pill" style="--c:#FF453A">Textiles</span></div>
              <div class="lp-lane" style="--c:#FF9F0A"><b>FADING</b><small>Ahead, but losing steam</small><span class="lp-pill" style="--c:#FF9F0A">Metals &amp; Mining</span></div>
            </div></div></div>""", unsafe_allow_html=True)

    # deep dive 2 — portfolio
    st.markdown('<div style="height:56px"></div>', unsafe_allow_html=True)
    pd1, pd2 = st.columns([1.05, 1], gap="large")
    with pd1:
        st.markdown("""<div class="lp-mock"><div class="lp-mh"><i></i><i></i><i></i><span>PORTFOLIO · REVIEW</span><em>ILLUSTRATIVE</em></div>
            <div class="lp-mb"><div class="lp-k3">
              <div class="lp-k"><div class="l">VALUE</div><div class="v">₹12.40 L</div></div>
              <div class="lp-k"><div class="l">XIRR</div><div class="v" style="color:#30D158">+18.2%</div></div>
              <div class="lp-k"><div class="l">HEALTH</div><div class="v" style="color:#FF9F0A">72 / 100</div></div></div>
              <div class="lp-bars">
                <div><span>Financial Services</span><i style="width:62%"></i><em>34%</em></div>
                <div><span>Information Technology</span><i style="width:40%"></i><em>22%</em></div>
                <div><span>Energy</span><i style="width:26%"></i><em>14%</em></div>
                <div><span>Healthcare</span><i style="width:18%"></i><em>10%</em></div>
              </div></div></div>""", unsafe_allow_html=True)
    with pd2:
        st.markdown(f"""<div class="lp-dd" style="padding-top:18px;"><div class="e">Portfolio analytics</div>
            <div class="t">A review that reads like a research desk, not a broker app.</div>
            {checkline("Sector and industry exposure, market-cap mix and concentration at a glance")}
            {checkline("Growth of your money versus a NIFTY 50 investment with the same cash flows")}
            {checkline("Volatility, drawdown, beta, one-day VaR and a correlation map")}
            {checkline("Plain-English health check that flags concentration and deep losers")}</div>""", unsafe_allow_html=True)

    # deep dive 3 — AI
    st.markdown('<div style="height:56px"></div>', unsafe_allow_html=True)
    ad1, ad2 = st.columns([1, 1.05], gap="large")
    with ad1:
        st.markdown(f"""<div class="lp-dd" style="padding-top:18px;"><div class="e">Arka AI</div>
            <div class="t">Teach the AI your setups. It never forgets.</div>
            {checkline("Save your personal trading rules, entry conditions and reference charts once")}
            {checkline("Vision analysis checks any chart against <strong>your</strong> rules")}
            {checkline("Get a verdict, score and rule-by-rule breakdown in seconds")}
            {checkline("Vector memory keeps every setup permanently")}</div>""", unsafe_allow_html=True)
    with ad2:
        st.markdown("""<div class="lp-mock"><div class="lp-mh"><i></i><i></i><i></i><span>ARKA AI · SETUP ANALYSIS</span><em>ILLUSTRATIVE</em></div>
            <div class="lp-mb"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
              <span style="font:700 13px 'Inter',sans-serif;color:#ECECEC">RELIANCE · Daily</span>
              <span style="font:700 10px 'JetBrains Mono',monospace;color:#30D158;background:rgba(48,209,88,.1);border:1px solid rgba(48,209,88,.3);padding:3px 10px;border-radius:3px">VALID · 8/10</span></div>
              <div class="lp-rules"><span class="y">+ Rule matched:</span> Close above PDH on breakout candle<br><span class="y">+ Rule matched:</span> Volume 1.8x vs 20-day average<br><span class="y">+ Rule matched:</span> RSI 61, within momentum zone<br><span class="n">- Flagged:</span> Overhead supply near a prior high</div>
            </div></div>""", unsafe_allow_html=True)

    # styles
    st.markdown('<div class="lp-h"><div class="e">Built for your style</div><div class="t">Momentum. Swing. Positional.</div></div>', unsafe_allow_html=True)
    sty = [("zap", AMBER, "Momentum traders", ["PDH / PDL breakout detection", "Live price and RSI on every watchlist row", "Volume spike flags versus the 20-day average", "Instant Telegram push when levels break"]),
           ("trend", CYAN, "Swing traders", ["Sector rotation radar to find the next leaders", "RSI and EMA position across your lists", "AI pattern matching against saved reference charts", "Distance from the 10 / 20 EMA for pullback entries"]),
           ("layers", PURPLE, "Positional investors", ["Portfolio health, XIRR and sector exposure", "Curated Arka desk watchlist", "News light on holdings with fresh headlines", "Cloud-synced lists on any device"])]
    sc = st.columns(3, gap="small")
    for col, (ic, c, title, items) in zip(sc, sty):
        with col:
            st.markdown(f'<div class="lp-style" style="--c:{c}">{icon_box(ic, c, 38)}<div class="t">{title}</div>'
                        + "".join(checkline(i, c) for i in items) + '</div>', unsafe_allow_html=True)

    # steps
    st.markdown('<div id="lp-start"></div><div class="lp-h"><div class="e">Onboarding</div><div class="t">Live in three steps.</div></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="lp-steps">'
        '<div class="lp-step"><div class="no">STEP 01 · DAY 1</div><div class="t">Connect and import</div><div class="d">Sign in and upload your TradingView watchlist. Cloud sync is instant.</div></div>'
        '<div class="lp-step"><div class="no">STEP 02 · DAY 7</div><div class="t">Teach your strategy</div><div class="d">Give Arka your setups, rules and reference charts.</div></div>'
        '<div class="lp-step"><div class="no">STEP 03 · DAY 14</div><div class="t">Automate the routine</div><div class="d">Full-universe scans and Telegram alerts on your exact conditions.</div></div></div>',
        unsafe_allow_html=True)

    # closing CTA
    st.markdown('<div class="lp-cta"><div class="t">Ready to open your terminal?</div>'
                '<div class="s">Sign in to your workspace. Your lists, portfolio and alerts are waiting.</div></div>', unsafe_allow_html=True)
    if st.button("Sign in to Arka →", type="primary", key="cta_bottom"):
        st.session_state.show_login = True
        st.rerun()

    st.markdown('<div class="lp-foot">Arka Trades · Finance &amp; Market Education'
                '<small>Not SEBI registered. All content is for educational purposes only. Trading involves risk: decisions and outcomes are entirely your own. '
                'Examples shown are illustrative and not recommendations.</small></div>', unsafe_allow_html=True)
    _navigation_transition()
    st.stop()
  # ════════════════ APPLICATION SHELL ═══════════════════════════════════
from html import escape as _esc

NAV = [("HOME", "home"), ("MONEY FLOW", "flow"), ("BREADTH", "breadth"),
       ("WATCHLIST", "watchlist"), ("PORTFOLIO", "portfolio"), ("ALERTS", "alerts"),
       ("SCANNER", "scanner"), ("SCREENER", "smart_scan"), ("RESEARCH", "research"), ("ARKA AI", "analysis")]

PAGE_META = {
    "home":       ("TERMINAL", "Market overview, your watchlist and your portfolio at a glance.", "home"),
    "flow":       ("MONEY FLOW", "Sector rotation radar, index breadth and institutional flows.", "activity"),
    "breadth":    ("MARKET BREADTH", "Advance / decline participation and market internals.", "bars"),
    "watchlist":  ("WATCHLIST", "Your tracked stocks with live price, RSI and PDH / PDL status.", "star"),
    "portfolio":  ("PORTFOLIO", "Holdings, live P&L, sector exposure, risk and an automatic health check.", "briefcase"),
    "alerts":     ("ALERTS", "Price, PDH and PDL alerts delivered to Telegram.", "bell"),
    "scanner":    ("WATCHLIST SCANNER", "Scan your lists for PDH / PDL breaks, RSI and volume conditions.", "scan"),
    "smart_scan": ("SMART SCREENER", "Rule-based screens across the NSE universe.", "filter"),
    "research":   ("RESEARCH", "Financials, ownership, peers, valuation, risk and news in one place.", "research"),
    "analysis":   ("ARKA AI", "AI chart analysis against your own trading rules.", "brain"),
    "profile":    ("PROFILE", "Your account details.", "user"),
    "settings":   ("SETTINGS", "Terminal preferences and integrations.", "gauge"),
    "contact":    ("CONTACT", "Reach the Arka Trades desk.", "mail"),
}

MODULES = [
    ("WATCHLIST", "watchlist", "star", "MY BOOK", "Track NSE stocks with live price, RSI and PDH / PDL status."),
    ("PORTFOLIO", "portfolio", "briefcase", "MY BOOK", "Buy lots, live P&amp;L, sector exposure, risk and a health check."),
    ("ALERTS", "alerts", "bell", "MY BOOK", "Price, PDH and PDL alerts delivered to Telegram."),
    ("MONEY FLOW", "flow", "activity", "MARKETS", "Sector rotation radar, index breadth and FII / DII flows."),
    ("MARKET BREADTH", "breadth", "bars", "MARKETS", "Advance / decline participation and market internals."),
    ("WATCHLIST SCANNER", "scanner", "scan", "TOOLS", "Scan your lists for PDH / PDL breaks, RSI and volume."),
    ("SMART SCREENER", "smart_scan", "filter", "TOOLS", "Rule-based screens across the NSE universe."),
    ("RESEARCH", "research", "research", "INTEL", "Financials, ownership, peers, valuation and news."),
    ("ARKA AI", "analysis", "brain", "INTEL", "AI chart analysis against your own trading rules."),
]


def _fmt_num(value, prefix="", suffix=""):
    if value in (None, "", "—", "-"):
        return "—"
    return f"{prefix}{value}{suffix}"


def _goto(target: str):
    """on_click callback: switch workspace (Streamlit reruns once, no st.rerun needed)."""
    st.session_state.page = target
    if target != "home":
        sym = st.session_state.get("active_security")
        if sym:
            st.session_state["research_last_query"] = sym
            st.session_state["m1_ticker"] = sym


def _signout():
    for k in ("logged_in", "is_admin", "show_login"):
        st.session_state[k] = False
    st.session_state.page = "home"
    st.session_state["active_security"] = ""


def _go_home():
    st.session_state.page = "home"
    st.rerun()


def _security_candidates(query: str):
    """Build a small fast dropdown from known/active symbols.
    Exact/near matches are offered in the UI; unknown symbols are resolved
    through Screener when the user presses LOAD.
    """
    base = [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
        "TCS", "INFY", "WIPRO", "LT", "ITC", "BHARTIARTL", "TATAMOTORS",
        "M&M", "MARUTI", "SUNPHARMA", "HINDUNILVR", "BAJFINANCE", "ADANIENT",
        "ADANIPORTS", "TATASTEEL", "JSWSTEEL", "NTPC", "POWERGRID", "ONGC",
        "COALINDIA", "BEL", "HAL", "RVNL", "IRFC", "TRENT", "PERSISTENT",
    ]
    # Watchlists and portfolio holdings are also searchable.
    for source in (st.session_state.get("watchlist", []),
                   st.session_state.get("admin_watchlist", []),
                   ud.holding_symbols()):
        for x in source:
            if x and x.upper() not in base:
                base.append(x.upper())
    q = (query or "").strip().upper()
    if not q:
        return base[:18]
    starts = [x for x in base if x.startswith(q)]
    contains = [x for x in base if q in x and x not in starts]
    return (starts + contains)[:12]


def _open_security(symbol: str):
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return
    st.session_state["active_security"] = symbol
    st.session_state["research_last_query"] = symbol
    st.session_state["m1_ticker"] = symbol
    st.session_state.pop("research_data", None)
    st.session_state.page = "security"
    st.rerun()


# ───────────── top bar (sticky, on every page) ─────────────
def _render_topbar(pg):
    label, cls, detail = _market_status()
    role = "ADMIN" if IS_ADMIN else "MEMBER"
    with st.container(key="topbar"):
        c1, c2, c3 = st.columns([1.35, 8.4, 2.6], gap="small")
        with c1:
            st.markdown(f'<div class="ak-brand"><div class="ak-mark">{icon("trend", 16, "#000")}</div>'
                        '<div><div class="ak-bn">ARKA TRADES</div><div class="ak-bs">MARKET TERMINAL</div></div></div>',
                        unsafe_allow_html=True)
        with c2:
            with st.container(key="topnav"):
                cols = st.columns(len(NAV), gap="small")
                for col, (lab, tgt) in zip(cols, NAV):
                    active = (pg == tgt) or (tgt == "home" and pg == "security")
                    with col:
                        st.button(lab, key=f"nav_{tgt}", use_container_width=True,
                                  type="primary" if active else "secondary", on_click=_goto, args=(tgt,))
        with c3:
            with st.container(key="topuser"):
                a, b = st.columns([2.7, 1], gap="small")
                with a:
                    st.markdown(f'<div class="ak-chips"><span class="ak-chip {cls}" title="{_esc(detail)}"><i class="ak-dot"></i>{label}</span>'
                                f'<span class="ak-chip"><span class="ak-av">{_esc(initial)}</span>{role}</span></div>', unsafe_allow_html=True)
                with b:
                    st.button("SIGN OUT", key="ak_signout", on_click=_signout, use_container_width=True)


def _render_page_header(pg):
    kicker, desc, ic = PAGE_META.get(pg, ("WORKSPACE", "", "layers"))
    now = datetime.now(IST)
    title = kicker
    if pg == "home":
        hour = now.hour
        title = f'{"Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"}, {_esc(name)}'
    sym = st.session_state.get("active_security")
    chips = ""
    if sym:
        chips += f'<span class="ak-chip"><i class="ak-dot" style="color:{AMBER}"></i>ACTIVE · {_esc(sym)}</span>'
    chips += f'<span class="ak-chip">{now.strftime("%a %d %b %Y")}</span>'
    st.markdown(f'<div class="ak-ph"><div style="display:flex;gap:14px;align-items:center;">{icon_box(ic, AMBER, 42)}'
                f'<div><div class="k">ARKA TERMINAL · {"OVERVIEW" if pg == "home" else "WORKSPACE"}</div><div class="t">{title}</div>'
                f'<div class="d">{desc}</div></div></div><div class="r">{chips}</div></div>', unsafe_allow_html=True)


# ───────────── market strips + command bar ─────────────
def _ix_cell(label, data):
    if data:
        chg = data["chg"]
        cls = "ak-up" if chg > 0.005 else "ak-dn" if chg < -0.005 else "ak-fl"
        arrow = "▲" if chg > 0.005 else "▼" if chg < -0.005 else "●"
        col = GREEN if chg >= 0 else RED
        w = min(100.0, abs(chg) / 3 * 100)
        return (f'<div class="ak-ix"><div class="n">{label}</div><div class="p">{data["price"]:,.2f}</div>'
                f'<span class="c {cls}">{arrow} {abs(chg):.2f}%</span><i class="m" style="width:{w:.0f}%;background:{col}"></i></div>')
    return (f'<div class="ak-ix"><div class="n">{label}</div><div class="p" style="color:#444">—</div>'
            f'<span class="c ak-fl">—</span></div>')


@st.fragment(run_every=10)
def _index_strips():
    """Refresh the index strip every 10 seconds without rerunning the whole app."""
    # get_indices is cached in data_engine.py; clear its cache so this is a real refresh.
    try:
        get_indices.clear()
    except Exception:
        pass
    data = get_indices(INDIA_ITEMS + GLOBAL_ITEMS)
    rows = ""
    for title, items in (("INDIA", INDIA_ITEMS), ("GLOBAL", GLOBAL_ITEMS)):
        cells = "".join(_ix_cell(label, data.get(label)) for label, _ in items)
        rows += f'<div class="ak-strip"><div class="ak-sl">{title}</div>{cells}</div>'
    st.markdown(f'<div class="ak-strips">{rows}</div>', unsafe_allow_html=True)


def _render_command_bar():
    st.markdown('<div class="ak-cmd-h"><b>SEARCH · SECURITY / INDEX / COMPANY</b>'
                '<span>Type a ticker or company name, pick a match and press LOAD</span></div>', unsafe_allow_html=True)
    with st.container(key="cmdbar"):
        with st.form("security_command_v9", clear_on_submit=False):
            q = st.text_input("Security search", value=st.session_state.get("security_search_v9", ""),
                              placeholder="Type ticker or company name · e.g. RELIANCE", label_visibility="collapsed")
            suggestions = _security_candidates(q)
            sc1, sc2 = st.columns([5.7, 1])
            with sc1:
                labels = [f"{s} · NSE EQUITY" for s in suggestions]
                selected = st.selectbox("Matches", labels, index=0 if labels else None,
                                        placeholder="Select a matching security…", label_visibility="collapsed",
                                        key="security_dropdown_v9") if labels else None
            with sc2:
                search_clicked = st.form_submit_button("LOAD", type="primary", use_container_width=True)
    if search_clicked:
        raw = (q or "").strip()
        chosen = selected.split(" · ")[0].strip() if selected else raw
        if not chosen:
            st.warning("Enter a ticker or company name first.")
        else:
            # Exact typed tickers always take precedence over a stale dropdown selection.
            raw_norm = raw.upper()
            known = set(_security_candidates(""))
            if raw_norm in known:
                candidate = raw_norm
            else:
                candidate = ""
                try:
                    resolved = resolve_symbol(raw)
                except Exception:
                    resolved = None
                if resolved:
                    url = str(resolved.get("url", ""))
                    m = re.search(r"/company/([^/]+)", url, flags=re.I)
                    candidate = m.group(1).upper() if m else ""
                if not candidate:
                    candidate = chosen.upper() if chosen else raw_norm
            _open_security(candidate)


def _render_market_header():
    _index_strips()
    _render_command_bar()


# ───────────── security workspace ─────────────
def _render_security_workspace(symbol: str):
    symbol = symbol.upper().strip()
    # Screener fundamentals + 5y price history load IN PARALLEL, then stay cached.
    with st.spinner(f"Loading {symbol}…"):
        B, hist = warm_security(symbol)

    s = B.get("summary") or {}
    sec = B.get("sector") or {}
    name_ = B.get("name") or symbol

    price = prev = None
    if hist is not None and len(hist) >= 2:
        price = float(hist["Close"].iloc[-1]); prev = float(hist["Close"].iloc[-2])
    elif to_float(s.get("current_price")):
        price = to_float(s.get("current_price"))
    chg = ((price - prev) / prev * 100) if (price is not None and prev) else None
    price_txt = f"₹{price:,.2f}" if price is not None else "—"
    if chg is None:
        chg_html = ""
    else:
        cls = "ak-up" if chg >= 0 else "ak-dn"
        chg_html = f'<span class="c {cls}">{"▲" if chg >= 0 else "▼"} {abs(chg):.2f}%</span>'
    sector_txt = sec.get("Sector", "") or sec.get("Broad Sector", "") or "Equity"
    industry_txt = sec.get("Industry", "") or sec.get("Broad Industry", "")

    if hist is not None and len(hist) >= 2:
        h252 = hist.tail(252)
        hi52, lo52 = float(h252["High"].max()), float(h252["Low"].min())
        pdh, pdl = float(hist["High"].iloc[-2]), float(hist["Low"].iloc[-2])
    else:
        hi52 = to_float(s.get("year_high")); lo52 = to_float(s.get("year_low")); pdh = pdl = None
    money = lambda v: f"₹{v:,.2f}" if v is not None else "—"

    tags = f'<span class="ak-tag">NSE</span><span class="ak-tag">{_esc(sector_txt)}</span>'
    if industry_txt and industry_txt != sector_txt:
        tags += f'<span class="ak-tag">{_esc(industry_txt)}</span>'
    st.markdown(f'<div class="ak-sh"><div><span class="sym">{_esc(symbol)}</span><div class="nm">{_esc(name_)}</div>'
                f'<div class="tg">{tags}</div></div><div class="px"><div class="v">{price_txt}</div>{chg_html}</div></div>',
                unsafe_allow_html=True)

    stats = [("52W LOW", money(lo52)), ("52W HIGH", money(hi52)), ("PDL", money(pdl)), ("PDH", money(pdh)),
             ("MKT CAP", _fmt_num(s.get("market_cap"), "₹", " Cr")), ("P/E", _fmt_num(s.get("pe_ratio"), "", "x")),
             ("ROE", _fmt_num(s.get("roe"), "", "%")), ("ROCE", _fmt_num(s.get("roce"), "", "%")),
             ("DIV YIELD", _fmt_num(s.get("dividend_yield"), "", "%"))]
    st.markdown('<div class="ak-stats">' + "".join(f'<div class="ak-st"><div class="l">{k}</div><div class="v">{v}</div></div>' for k, v in stats) + '</div>',
                unsafe_allow_html=True)
    if price is not None and hi52 and lo52 and hi52 > lo52:
        pos = max(0.0, min(1.0, (price - lo52) / (hi52 - lo52)))
        st.markdown(f'<div class="ak-rng"><div class="t"><span>52-WEEK RANGE</span><span>{pos * 100:.0f}% OF RANGE</span></div>'
                    f'<div class="b"><i style="left:{pos * 100:.1f}%"></i></div></div>', unsafe_allow_html=True)
    if not B.get("resolved"):
        st.caption("Screener fundamentals could not be loaded right now — price chart and technicals still work. Reopen the security in a minute to retry.")

    a1, a2, a3, a4 = st.columns(4, gap="small")
    add_clicked = a1.button("＋ ADD TO WATCHLIST", use_container_width=True, key=f"sec_wl_{symbol}")
    a2.button("＋ ADD TO PORTFOLIO", use_container_width=True, key=f"sec_pf_{symbol}", on_click=_goto, args=("portfolio",))
    ai_clicked = a3.button("◆ ARKA AI ANALYSIS", use_container_width=True, key=f"sec_ai_{symbol}", type="primary")
    a4.button("OPEN RESEARCH PAGE", use_container_width=True, key=f"sec_rs_{symbol}", on_click=_goto, args=("research",))
    if add_clicked:
        # Inserts just this symbol (no delete-everything-then-reinsert).
        added, err = ud.add_symbols(supabase, [symbol], ud.W_TABLE)
        if added:
            st.success(f"{symbol} added to watchlist")
        elif not err:
            st.info(f"{symbol} is already in your watchlist")
        if err:
            (st.warning if added else st.error)(err)
    if ai_clicked:
        try:
            ud.go_analysis(symbol)
        except Exception:
            st.session_state.page = "analysis"; st.rerun()

    chart_col, info_col = st.columns([3.2, 1.55], gap="small")
    with chart_col:
        st.markdown('<div class="ak-pt2">PRICE CHART · CANDLES · VOLUME · MA · RSI 14 (+MA 14)</div>', unsafe_allow_html=True)
        render_pro_chart(symbol, height=660)
    with info_col:
        st.markdown('<div class="ak-pt2">COMPANY OVERVIEW</div>', unsafe_allow_html=True)
        overview_items = [
            ("Sector", sec.get("Sector", sec.get("Broad Sector", "—"))),
            ("Industry", sec.get("Industry", sec.get("Broad Industry", "—"))),
            ("Market Cap", _fmt_num(s.get("market_cap"), "₹", " Cr")),
            ("P/E", _fmt_num(s.get("pe_ratio"), "", "x")),
            ("Book Value", _fmt_num(s.get("book_value"), "₹", "")),
            ("Dividend Yield", _fmt_num(s.get("dividend_yield"), "", "%")),
            ("ROCE", _fmt_num(s.get("roce"), "", "%")),
            ("ROE", _fmt_num(s.get("roe"), "", "%")),
        ]
        rows = "".join(f'<div><span>{k}</span><b>{_esc(v)}</b></div>' for k, v in overview_items)
        st.markdown(f'<div class="ak-ov">{rows}</div>', unsafe_allow_html=True)
        st.markdown('<div class="ak-pt2" style="margin-top:14px">ABOUT</div>', unsafe_allow_html=True)
        about = (B.get("about") or "").strip()
        desc = about[:420] + ("…" if len(about) > 420 else "") if about else (
            f"{name_} is listed on the Indian equity market, classified under {sector_txt}. "
            "Full fundamentals are in the research panel below.")
        st.markdown(f'<div class="ak-desc">{_esc(desc)}</div>', unsafe_allow_html=True)

    st.markdown('<div class="ak-pt2" style="margin-top:16px">RESEARCH WORKSPACE</div>', unsafe_allow_html=True)
    render_research_panel(symbol, TERM_TOKENS, _fetch_news_for_stock, key="sec")


# ───────────── home dashboard ─────────────
def _kpi(label, value, sub="", color=None, accent=None):
    vstyle = f' style="color:{color}"' if color else ""
    return (f'<div class="ak-kpi" style="--a:{accent or LINE2}"><div class="l">{label}</div>'
            f'<div class="v"{vstyle}>{value}</div><div class="s">{sub}</div></div>')


@st.fragment(run_every=10)
def _render_dashboard_live():
    """Refresh Home KPIs, watchlist, portfolio and market internals every 10 seconds."""
    if not st.session_state.get("_home_pf_loaded"):          # make sure holdings are loaded once per session
        try:
            ud.load_portfolio(supabase)
        except Exception:
            pass
        st.session_state["_home_pf_loaded"] = True
    wl = list(dict.fromkeys(st.session_state.get("watchlist", [])))[:12]
    lots = st.session_state.get("pf_lots", []) or []
    holdings = list(dict.fromkeys(l["symbol"] for l in lots if l.get("symbol")))[:12]
    all_syms = list(dict.fromkeys(st.session_state.get("admin_watchlist", []) + wl + holdings))[:40]
    # get_quotes is cached in data_engine.py; clear its cache so the 10s fragment
    # fetches updated values instead of repeatedly showing the old result.
    try:
        get_quotes.clear()
    except Exception:
        pass
    quotes = get_quotes(tuple(all_syms)) if all_syms else {}

    # portfolio roll-up (only lots that currently have a price)
    inv_v = val = day = 0.0
    for l in lots:
        q = quotes.get(l.get("symbol")) or {}
        px = q.get("price")
        if px is None:
            continue
        inv_v += l["quantity"] * l["buy_price"]
        val += l["quantity"] * px
        pc = q.get("prev_close")
        if pc:
            day += l["quantity"] * (px - pc)
    has_pf = val > 0
    pnl = val - inv_v
    sgn = lambda x: GREEN if x >= 0 else RED
    adv = sum(1 for s in wl if quotes.get(s) and quotes[s]["chg"] > 0.05)
    dec = sum(1 for s in wl if quotes.get(s) and quotes[s]["chg"] < -0.05)
    active_alerts = sum(1 for a in st.session_state.alerts.values() if a.get("active"))
    mmi = get_mmi()

    k = []
    if has_pf:
        k.append(_kpi("Portfolio value", _inr_short(val), f"Invested {_inr_short(inv_v)} · {len(holdings)} holdings", accent=CYAN))
        k.append(_kpi("Day P&amp;L", _inr_short(day), f"{day / (val - day) * 100:+.2f}% today" if (val - day) else "", sgn(day), accent=sgn(day)))
        k.append(_kpi("Total P&amp;L", _inr_short(pnl), f"{pnl / inv_v * 100:+.2f}% overall" if inv_v else "", sgn(pnl), accent=sgn(pnl)))
    else:
        for lab in ("Portfolio value", "Day P&amp;L", "Total P&amp;L"):
            k.append(_kpi(lab, "—", "Add holdings in Portfolio", T3, accent=LINE2))
    k.append(_kpi("Watchlist breadth", f'<span style="color:{GREEN}">▲ {adv}</span> <span style="color:{T3}">·</span> <span style="color:{RED}">▼ {dec}</span>',
                  f"{len(wl)} symbols tracked", accent=AMBER))
    k.append(_kpi("Active alerts", str(active_alerts), f"{len(st.session_state.alert_fired)} triggered this session", accent=PURPLE))
    if mmi.get("status") in ("live", "stale"):
        zc = mmi_zone_color(mmi["zone"])
        k.append(_kpi("Market mood", f'{mmi["score"]:.0f}', f'{mmi["zone"]}{" · cached" if mmi["status"] == "stale" else ""}', zc, accent=zc))
    else:
        k.append(_kpi("Market mood", "—", "Unavailable right now", T3, accent=LINE2))
    st.markdown('<div class="ak-kpis">' + "".join(k) + '</div>', unsafe_allow_html=True)

    def _rows(symbols, empty_html):
        out = []
        for sym in symbols:
            d = quotes.get(sym)
            if not d:
                out.append(f'<div class="ak-row"><span class="s">{_esc(sym)}</span><span class="p">—</span><span class="c ak-fl">NO DATA</span></div>')
                continue
            c = d["chg"]
            cls = "ak-up" if c > 0.005 else "ak-dn" if c < -0.005 else "ak-fl"
            arrow = "▲" if c > 0.005 else "▼" if c < -0.005 else "●"
            out.append(f'<div class="ak-row"><span class="s">{_esc(sym)}</span><span class="p">₹{d["price"]:,.2f}</span>'
                       f'<span class="c {cls}">{arrow} {abs(c):.2f}%</span></div>')
        return "".join(out) or empty_html

    wl_html = _rows(wl, '<div class="ak-empty"><b>No personal watchlist yet.</b><br>Open the Watchlist workspace to add NSE symbols by ticker or company name.</div>')
    pf_html = _rows(holdings, '<div class="ak-empty"><b>No holdings yet.</b><br>Add a buy lot in Portfolio to see live P&amp;L and sector analytics.</div>')

    sample = [s for s in all_syms if quotes.get(s)]
    flat = sum(1 for s in sample if abs(quotes[s]["chg"]) <= 0.05)
    a_all = sum(1 for s in sample if quotes[s]["chg"] > 0.05)
    d_all = sum(1 for s in sample if quotes[s]["chg"] < -0.05)
    tot = max(1, a_all + d_all + flat)
    ratio = (a_all / d_all) if d_all else (float(a_all) if a_all else 0)
    best = max(sample, key=lambda s: quotes[s]["chg"], default=None)
    worst = min(sample, key=lambda s: quotes[s]["chg"], default=None)
    mv = lambda s: (f'<div class="ak-row"><span class="s">{_esc(s)}</span><span class="p"></span>'
                    f'<span class="c {"ak-up" if quotes[s]["chg"] >= 0 else "ak-dn"}">{quotes[s]["chg"]:+.2f}%</span></div>') if s else ""
    internals = (
        f'<div class="ak-bar"><i style="width:{a_all / tot * 100:.1f}%;background:{GREEN}"></i>'
        f'<i style="width:{flat / tot * 100:.1f}%;background:#3a3a3a"></i><i style="width:{d_all / tot * 100:.1f}%;background:{RED}"></i></div>'
        f'<div class="ak-row"><span class="s">ADVANCING</span><span class="p">{a_all}</span><span class="c ak-up">▲</span></div>'
        f'<div class="ak-row"><span class="s">DECLINING</span><span class="p">{d_all}</span><span class="c ak-dn">▼</span></div>'
        f'<div class="ak-row"><span class="s">UNCHANGED</span><span class="p">{flat}</span><span class="c ak-fl">●</span></div>'
        f'<div class="ak-row"><span class="s">A/D RATIO</span><span class="p">{ratio:.2f}</span><span class="c ak-fl">RATIO</span></div>'
        f'{mv(best)}{mv(worst)}')
    st.markdown(
        '<div class="ak-grid3">'
        f'<div class="ak-panel"><div class="ak-pt"><b>YOUR WATCHLIST</b><span>{len(wl)} SYMBOLS</span></div>{wl_html}</div>'
        f'<div class="ak-panel"><div class="ak-pt"><b>PORTFOLIO HOLDINGS</b><span>{len(holdings)} POSITIONS</span></div>{pf_html}</div>'
        f'<div class="ak-panel"><div class="ak-pt"><b>MARKET INTERNALS</b><span>{len(sample)} SYMBOL SAMPLE</span></div>{internals}</div></div>',
        unsafe_allow_html=True)

def _render_workspace_launcher():
    """Navigation controls stay outside the timed fragment so button clicks work normally."""
    section("WORKSPACES", "jump straight into any module")
    for r in range(0, len(MODULES), 3):
        cols = st.columns(3, gap="small")
        for col, (label, tgt, ic, tag, desc) in zip(cols, MODULES[r:r + 3]):
            with col:
                with st.container(key=f"ak_mod_{tgt}"):
                    st.markdown(f'<div class="ak-mc">{icon_box(ic, AMBER, 38)}<div><div class="t">{label}<em>{tag}</em></div>'
                                f'<div class="d">{desc}</div></div></div>', unsafe_allow_html=True)
                    st.button("OPEN WORKSPACE  →", key=f"ak_open_{tgt}", use_container_width=True, on_click=_goto, args=(tgt,))



def _render_dashboard():
    """Render live price panels plus the non-refreshing workspace launcher."""
    _render_dashboard_live()
    _render_workspace_launcher()


# ───────────── news rail ─────────────
def _news_watchlist_for_rail():
    # On the Portfolio page the rail follows your holdings.
    if st.session_state.get("page") == "portfolio":
        held = ud.holding_symbols()
        if held:
            return held, "YOUR PORTFOLIO"
    source_key = st.session_state.get("active_news_source", "admin")
    primary_key = "admin_watchlist" if source_key == "admin" else "watchlist"
    wl = st.session_state.get(primary_key, [])
    if not wl:
        wl = st.session_state.get("admin_watchlist", []) or st.session_state.get("watchlist", [])
    label = "ARKA WATCHLIST" if (wl and wl == st.session_state.get("admin_watchlist")) else "YOUR WATCHLIST"
    return wl, label


@st.fragment
def _news_rail_fragment():
    toggle_label = "HIDE NEWS ▸" if st.session_state.show_news_rail else "◂ SHOW NEWS"
    if st.button(toggle_label, key="toggle_news_rail_fast", use_container_width=True):
        st.session_state.show_news_rail = not st.session_state.show_news_rail
        st.rerun(scope="fragment")

    if not st.session_state.show_news_rail:
        st.markdown('<div class="news-rail-collapsed">MARKET NEWS <span>HIDDEN</span></div>', unsafe_allow_html=True)
        return

    st.markdown('<div class="ak-pt" style="border:1px solid var(--line);border-bottom:0;"><b>MARKET NEWS</b>'
                '<span><i class="pulse-dot"></i>&nbsp; LIVE</span></div>', unsafe_allow_html=True)
    watchlist_for_news, rail_label = _news_watchlist_for_rail()
    if not watchlist_for_news:
        st.markdown('<div class="ak-empty">Macro and global news update below. Add stocks in the <b>Watchlist</b> workspace for stock-specific news.</div>',
                    unsafe_allow_html=True)
    render_news_rail(watchlist_for_news, label=rail_label)


def _render_footer():
    st.markdown('<div class="ak-foot"><span>ARKA TRADES · Market data via Yahoo Finance and NSE, delayed as per source. Educational use only.</span>'
                '<span>Not SEBI registered · Not investment advice · Trading involves risk</span></div>', unsafe_allow_html=True)


# ════════════════ SHELL + ROUTER ═══════════════════════════════════
if "active_security" not in st.session_state:
    st.session_state["active_security"] = ""

pg = st.session_state.page
_render_topbar(pg)
if pg == "home":
    _render_page_header("home")
    _render_market_header()
elif pg == "security":
    _render_market_header()
else:
    _render_page_header(pg)

# Home/security keep the contextual news rail; dedicated modules use the full viewport so
# tables and controls are not squeezed into a narrow centre column.
if pg in {"home", "security"}:
    center, right_rail = st.columns([4.55, 1.15], gap="small")
else:
    center = st.container()
    right_rail = None

with center:
    if pg == "home":
        _render_dashboard()
    elif pg == "security":
        active = st.session_state.get("active_security", "")
        if not active:
            _render_dashboard()
        else:
            _render_security_workspace(active)

    elif pg == "watchlist":
        try:
            from watchlist_page import render_watchlist
        except Exception as e:
            st.error(f"Watchlist module failed to load: {e}")
        else:
            try:
                render_watchlist(supabase, on_open=_open_security, is_admin=IS_ADMIN)
            except Exception as e:
                st.error(f"Watchlist module error: {e}")

    elif pg == "portfolio":
        try:
            from portfolio_page import render_portfolio
        except Exception as e:
            st.error(f"Portfolio module failed to load: {e}")
        else:
            try:
                render_portfolio(supabase, on_open=_open_security)
            except Exception as e:
                st.error(f"Portfolio module error: {e}")

    elif pg == "scanner":
        if not st.session_state.admin_watchlist:
            awl = db_load_admin_watchlist()
            if awl: st.session_state.admin_watchlist = awl
        if not st.session_state.watchlist:
            wl = db_load_watchlist()
            if wl: st.session_state.watchlist = wl
        def render_scan_results(syms, key_prefix=""):
            sc1,sc2,sc3,sc4=st.columns([1,1,1,2])
            filt=sc1.selectbox("Show",["All","Above PDH","Below PDL","In Range"],key=f"filt_v4_{key_prefix}")
            fresh=sc2.checkbox("Force fresh data",key=f"fresh_{key_prefix}")
            sc3.caption("Batched · cached 30s")
            scanbtn=sc4.button("Run Scan",use_container_width=True,type="primary",key=f"scan_v4_{key_prefix}")
            if scanbtn:
                # The news rail only understands "admin" / "yours".
                st.session_state["active_news_source"]="admin" if key_prefix.startswith("admin") else "yours"
                if fresh: scan_batch.clear()
                with st.spinner(f"Scanning {len(syms)} stocks…"):
                    data = scan_batch(tuple(syms))      # ONE batched download, not 2 calls per stock
                results,failed=[],[]
                for sym in syms:
                    d = data.get(str(sym).upper().strip())
                    if d:
                        cur=d["cur"]; chg=d["chg"]
                        cls="g" if cur>d["pdh"] else "r" if cur<d["pdl"] else "n"
                        results.append({"sym":sym,"cur":cur,"chg":chg,"pdh":d["pdh"],"pdl":d["pdl"],"rsi":d["rsi"],"cls":cls,"spark":d.get("spark",[])})
                    else: failed.append(sym)
                check_alerts(results)
                st.session_state[f"results_{key_prefix}"]=results; st.session_state[f"failed_{key_prefix}"]=failed
            results=st.session_state.get(f"results_{key_prefix}",[]); failed=st.session_state.get(f"failed_{key_prefix}",[])
            if results:
                filtered=results
                if filt=="Above PDH": filtered=[r for r in results if r["cls"]=="g"]
                elif filt=="Below PDL": filtered=[r for r in results if r["cls"]=="r"]
                elif filt=="In Range": filtered=[r for r in results if r["cls"]=="n"]
                filtered.sort(key=lambda x:{"g":0,"r":1,"n":2}[x["cls"]])
                g=sum(r["cls"]=="g" for r in results); rr=sum(r["cls"]=="r" for r in results); n=sum(r["cls"]=="n" for r in results)
                m1,m2,m3,m4=st.columns(4); m1.metric("Above PDH",g); m2.metric("Below PDL",rr); m3.metric("In Range",n); m4.metric("Total",len(results))
                cols7=st.columns(4)
                for i,s in enumerate(filtered):
                    top=GREEN if s["cls"]=="g" else RED if s["cls"]=="r" else LINE2
                    rc=GREEN if s["rsi"]<35 else RED if s["rsi"]>65 else T2
                    with cols7[i%4]:
                        st.markdown(f'<div class="ak-sc" style="--c:{top}"><b>{_esc(s["sym"])}</b><br>{change_pill(s["chg"])}<div class="px">₹{s["cur"]:,.2f}</div><div class="rs" style="color:{rc}">RSI {s["rsi"]}</div></div>',unsafe_allow_html=True)
                        if st.button("OPEN",key=f"open_scan_{key_prefix}_{s['sym']}",use_container_width=True): _open_security(s["sym"])
                if failed: st.caption(f"Skipped (no data returned): {', '.join(failed)}")
        tab1,tab2=st.tabs(["Arka Watchlist","Your Watchlist"])
        with tab1:
            if IS_ADMIN:
                uploaded_admin=st.file_uploader("Upload Arka Watchlist (replaces the current Arka list)",type=["csv","txt"],key="admin_upload_v4")
                if uploaded_admin:
                    syms=parse_csv(uploaded_admin)
                    if syms and db_save_admin_watchlist(syms): st.success(f"Arka Watchlist updated — {len(syms)} stocks.")
                st.caption("To add or remove single stocks, use the WATCHLIST module → ARKA DESK LIST tab.")
            syms=st.session_state.admin_watchlist
            if syms: render_scan_results(syms,"admin_v4")
            else: st.info("Arka Watchlist not available yet.")
        with tab2:
            uploaded_yours=st.file_uploader("Upload Your Watchlist (CSV or TXT) — replaces your current saved list",type=["csv","txt"],key="your_upload_v4")
            if uploaded_yours:
                syms=parse_csv(uploaded_yours)
                if syms and db_save_watchlist(syms): st.success(f"{len(syms)} stocks loaded and saved.")
            st.caption("To add or remove single stocks, use the WATCHLIST module.")
            syms=st.session_state.watchlist
            if syms: render_scan_results(syms,"yours_v4")
            else: st.info("Add stocks in the WATCHLIST module (or upload a TradingView file above) to start scanning.")
    elif pg == "alerts":
        active_alerts={s:a for s,a in st.session_state.alerts.items() if a.get("active")}
        a1,a2,a3=st.columns(3); a1.metric("Active Alerts",len(active_alerts)); a2.metric("Triggered Today",len(st.session_state.alert_fired)); a3.metric("Delivery","Telegram")
        tabs=st.tabs(["Arka Watchlist","Your Watchlist"])
        def alert_block(watchlist,suffix):
            for sym in list(dict.fromkeys(watchlist)):
                a=st.session_state.alerts.get(sym,{}); active=bool(a.get("active"))
                c1,c2,c3,c4=st.columns([2,1.2,1.4,1])
                c1.markdown(f"**{sym}**"); c2.write("ACTIVE" if active else "INACTIVE"); c3.write(a.get("type","—").upper() if active else "—")
                if c4.button("OFF" if active else "SET",key=f"alertact_{suffix}_{sym}"):
                    if active:
                        del st.session_state.alerts[sym]; db_delete_alert(sym); st.rerun()
                    else:
                        st.session_state[f"alert_open_{suffix}_{sym}"]=True; st.rerun()
                if st.session_state.get(f"alert_open_{suffix}_{sym}"):
                    typ=st.radio("Condition",["PDH","PDL","Custom"],horizontal=True,key=f"atype_{suffix}_{sym}")
                    cp=st.number_input("Trigger price",min_value=0.0,step=0.5,key=f"acp_{suffix}_{sym}") if typ=="Custom" else 0.0
                    x,y=st.columns(2)
                    if x.button("Cancel",key=f"acan_{suffix}_{sym}"): st.session_state[f"alert_open_{suffix}_{sym}"]=False; st.rerun()
                    if y.button("Confirm",key=f"acon_{suffix}_{sym}",type="primary"):
                        if typ=="Custom" and cp<=0: st.error("Enter a valid price.")
                        else:
                            sd=get_static(sym); price=cp if typ=="Custom" else (sd["pdh"] if typ=="PDH" else sd["pdl"]) if sd else None
                            if price:
                                at=typ.lower(); st.session_state.alerts[sym]={"type":at,"price":price,"active":True}; db_save_alert(sym,at,price); st.session_state[f"alert_open_{suffix}_{sym}"]=False; st.rerun()
        with tabs[0]: alert_block(st.session_state.get("admin_watchlist",[]),"admin")
        with tabs[1]: alert_block(st.session_state.get("watchlist",[]),"yours")
    elif pg == "research":
        if st.session_state.get("active_security"): st.session_state["research_last_query"]=st.session_state["active_security"]
        render_research_page(TERM_TOKENS,news_fetch_fn=_fetch_news_for_stock)
    elif pg == "analysis":
        if st.session_state.get("active_security"): st.session_state["m1_ticker"]=st.session_state["active_security"]
        render_arka_ai()
    elif pg == "smart_scan":
        from smart_scan_page import render_smart_scanner
        render_smart_scanner(supabase)
    elif pg == "breadth":
        try:
            from breadth_page import render_market_breadth
            render_market_breadth()
        except Exception as e: st.error(f"Market Breadth module failed to load: {e}")
    elif pg == "flow":
        try:
            render_money_flow(on_open=_open_security)
        except Exception as e: st.error(f"Money Flow module failed to load: {e}")
    elif pg == "profile":
        p1,p2=st.columns([1,2])
        with p1:
            photo=st.session_state.get("profile_photo")
            if photo: st.image(photo,width=110); st.caption(name)
            else: st.markdown(f'<div style="width:88px;height:88px;background:{DARK3};border:1px solid {LINE2};display:flex;align-items:center;justify-content:center;font-size:32px;color:{AMBER};">{_esc(initial)}</div><div style="font-size:18px;font-weight:800;color:{IVORY};margin-top:12px;">{_esc(name)}</div>',unsafe_allow_html=True)
        with p2:
            with st.form("pf_v4"):
                a,b=st.columns(2); nn=a.text_input("Full Name",value=st.session_state.profile["name"]); np_=b.text_input("Contact Number",value=st.session_state.profile["phone"]); ne=st.text_input("Email Address",value=st.session_state.profile["email"]); ph=st.file_uploader("Upload Profile Photo",type=["jpg","jpeg","png"],key="profile_photo_v4")
                if st.form_submit_button("Save Profile",use_container_width=True,type="primary"):
                    st.session_state.profile.update({"name":nn,"phone":np_,"email":ne});
                    if ph: st.session_state.profile_photo=ph
                    st.rerun()
    elif pg == "settings":
        st.info("Terminal appearance is currently locked to the dark terminal theme.")
        st.markdown(f'<div class="term-panel"><b style="color:{AMBER};">Telegram</b><br><span style="color:{T2};">Bot connected · Chat ID configured in Streamlit Secrets.</span></div>',unsafe_allow_html=True)
    elif pg == "contact":
        st.markdown(f'<div class="term-panel"><div style="color:{T2};line-height:1.8;">Questions, feedback or suggestions?<br>Contact the Arka Trades desk through the configured support email.</div></div>',unsafe_allow_html=True)

if right_rail is not None:
    with right_rail:
        _news_rail_fragment()

_render_footer()

_navigation_transition()
