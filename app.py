"""
Arka Trades — Market Terminal v8

What changed vs v7
------------------
NEW HOME · MARKET MONITOR (home_page.py — add this new file to the repo)
  * Bloomberg Launchpad-style monitor: scrolling cross-asset tape, NIFTY 50 hero with 3-month chart, day and 52-week
    ranges, 50 / 200-DMA and RSI; rule-based market regime; NSE session with the 2026 holiday calendar; Market Mood
    Index + India VIX percentile; world equity indices (WEI); cross-asset monitor (commodities, INR crosses, US
    yields in bp, volatility, crypto); NIFTY 50 heatmap by sector with breadth; NSE sector heat matrix; NIFTY 50
    movers; your book (same split-adjusted FIFO engine as Portfolio); Money Flow rotation snapshot; world market
    clock; function launcher.
  * Market panels refresh themselves every 60 s while NSE is open, without rerunning the rest of the page.

COMMAND LINE · FUNCTION CODES
  * Type a code and press Enter: MF (Money Flow), RES (Research), PORT (Portfolio), WL (Watchlist), NEWS / N,
    EQS (Screener), AI (Arka AI), HOME. "TCS RES" opens TCS in Research, "TCS AI" in Arka AI, "TCS" its security page.

REMOVED
  * SCANNER and BREADTH workspaces (and their code). NIFTY 50 breadth now lives on HOME; scanner-style PDH / PDL
    and RSI signals live in WATCHLIST.

FIXES
  * Market status is holiday-aware (NSE 2026 calendar, Muhurat session) — it said "OPEN" on exchange holidays.
  * Home portfolio P&L now matches the Portfolio page (split / bonus adjusted, sells deducted).
  * "MIDCAP 100" tile could show NIFTY MIDCAP 50 (a fallback ticker of a different index). Fallbacks now stay
    within the same index.
  * Top-bar labels no longer truncate ("MONEY FL…", "T · ME…").
"""
from __future__ import annotations

import importlib
import inspect
import re
import traceback
from datetime import datetime, timedelta, timezone
from html import escape as _esc
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Arka Trades · Market Terminal", layout="wide", page_icon="📈",
                   initial_sidebar_state="collapsed")

import user_data as ud                                                  # noqa: E402  (after set_page_config)
from data_engine import warm_security, to_float   # noqa: E402

try:
    from supabase import create_client
except Exception:  # pragma: no cover
    create_client = None

IST = timezone(timedelta(hours=5, minutes=30))
APP_VERSION = "v8"


def _secret(name: str, default=""):
    """st.secrets.get() that never raises when no secrets file exists."""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# ═══════════════════════════════ SUPABASE ═══════════════════════════════
SUPABASE_URL = _secret("SUPABASE_URL", "")
SUPABASE_KEY = _secret("SUPABASE_KEY", "")


@st.cache_resource(show_spinner=False)
def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY or create_client is None:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


supabase = get_supabase()


def db_load_watchlist() -> list:
    if supabase is None:
        return []
    try:
        res = supabase.table("watchlist").select("symbol").execute()
        return list(dict.fromkeys(r["symbol"] for r in res.data)) if res.data else []
    except Exception:
        return []


def db_load_admin_watchlist() -> list:
    if supabase is None:
        return []
    try:
        res = supabase.table("admin_watchlist").select("symbol").execute()
        return list(dict.fromkeys(r["symbol"] for r in res.data)) if res.data else []
    except Exception:
        return []


# Logins: set ADMIN_USER / ADMIN_PASS / MEMBER_USER / MEMBER_PASS in Streamlit Cloud → Settings → Secrets,
# then delete the fallback strings below so no password lives in your GitHub repository.
_ADMIN_USER = _secret("ADMIN_USER", "ADMIN4477MAX")
_ADMIN_PASS = _secret("ADMIN_PASS", "MOHIT1")
_MEMBER_USER = _secret("MEMBER_USER", "max trades")
_MEMBER_PASS = _secret("MEMBER_PASS", "max")


# ═══════════════════════════════ DESIGN TOKENS ═══════════════════════════════
BG, S1, S2, S3 = "#050505", "#0A0A0A", "#0F0F0F", "#151515"
LINE, LINE2 = "#1F1F1F", "#2B2B2B"
DARK, DARK3 = "#000000", "#111111"
IVORY, T2, T3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
AMBER, AMBER2, CYAN, GREEN, RED, PURPLE = "#FF9F0A", "#FFB340", "#5AC8FA", "#30D158", "#FF453A", "#BF5AF2"
FONT = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif"
MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace"

TERM_TOKENS = {
    "dark": DARK, "panel": S1, "panel2": DARK3, "border": LINE2,
    "ivory": IVORY, "t2": T2, "t3": T3, "row_alt": "#0F0F0F",
    "amber": AMBER, "cyan": CYAN, "green": GREEN, "red": RED, "purple": PURPLE,
    "font": FONT, "mono": MONO,
}

_ICON_PATHS = {
    "chart": '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "trend": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "brain": '<path d="M12 2a4 4 0 0 0-4 4 4 4 0 0 0-3 6.5A4 4 0 0 0 7 20a4 4 0 0 0 5 1 4 4 0 0 0 5-1 4 4 0 0 0 2-7.5A4 4 0 0 0 16 6a4 4 0 0 0-4-4Z"/><path d="M12 2v19"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "research": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M11 8v6M8 11h6"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "briefcase": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "bars": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><line x1="7" y1="12" x2="17" y2="12"/>',
    "flask": '<path d="M9 3h6"/><path d="M10 3v6L4 20a1 1 0 0 0 .9 1.5h14.2A1 1 0 0 0 20 20l-6-11V3"/><path d="M7 15h10"/>',
}


def icon(name, size=18, color=None):
    c = color or AMBER
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;">{_ICON_PATHS.get(name, "")}</svg>')


def icon_box(name, color=None, size=32):
    c = color or AMBER
    return (f'<div style="width:{size}px;height:{size}px;border-radius:4px;background:{c}14;border:1px solid {c}33;'
            f'display:flex;align-items:center;justify-content:center;flex-shrink:0;">{icon(name, int(size * 0.5), c)}</div>')


# ═══════════════════════════════ SESSION STATE ═══════════════════════════════
for k, v in {"logged_in": False, "show_login": False, "page": "home", "is_admin": False,
             "profile": {"name": "Trader", "email": "", "phone": ""}, "profile_photo": None,
             "watchlist": [], "admin_watchlist": [], "db_loaded": False,
             "active_news_source": "admin", "show_news_rail": True, "active_security": "", "recent_symbols": [],
             "alerts": {}, "alert_fired": set()}.items():          # alerts keys kept empty only for older modules
    if k not in st.session_state:
        st.session_state[k] = v

if not st.session_state.db_loaded:
    wl = db_load_watchlist()
    if wl:
        st.session_state.watchlist = wl
    awl = db_load_admin_watchlist()
    if awl:
        st.session_state.admin_watchlist = awl
    st.session_state.db_loaded = True

name = st.session_state.profile.get("name", "Trader") or "Trader"
initial = name[0].upper()
IS_ADMIN = st.session_state.get("is_admin", False)


# ═══════════════════════════════ GLOBAL DESIGN SYSTEM ═══════════════════════════════
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
::-webkit-scrollbar{width:8px;height:8px;} ::-webkit-scrollbar-thumb{background:#262626;border-radius:4px;}
::-webkit-scrollbar-thumb:hover{background:#3a3a3a;} ::-webkit-scrollbar-track{background:transparent;}
::selection{background:rgba(255,159,10,.28);color:#fff;}

header[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],#MainMenu,footer,.stDeployButton,
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none !important;}
.block-container{padding:0 18px 40px !important;max-width:100% !important;}
[data-testid="stVerticalBlock"]{gap:.8rem;}

h1,h2,h3,h4,h5{font-family:var(--sans) !important;letter-spacing:-.01em;color:var(--tx) !important;}
a{color:var(--amber);text-decoration:none;} a:hover{color:var(--amber2);}
[data-testid="stCaptionContainer"],.stCaption{color:var(--tx3) !important;font-size:11px !important;line-height:1.55 !important;}
[data-testid="stWidgetLabel"] p,[data-testid="stWidgetLabel"] label{font:700 9px var(--mono) !important;letter-spacing:1px !important;text-transform:uppercase !important;color:var(--tx3) !important;}
hr{border-color:var(--line) !important;} iframe{border:none;}

/* buttons */
.stButton>button,.stDownloadButton>button,[data-testid^="stBaseButton-secondary"],[data-testid="stFormSubmitButton"]>button,[data-testid="stPopover"]>div>button{
  background:var(--s2) !important;color:var(--tx2) !important;border:1px solid var(--line2) !important;border-radius:3px !important;
  font:600 11px var(--sans) !important;letter-spacing:.3px;min-height:32px !important;box-shadow:none !important;
  transition:border-color .12s ease,color .12s ease,background .12s ease;}
.stButton>button:hover,.stDownloadButton>button:hover,[data-testid="stPopover"]>div>button:hover{border-color:var(--amber) !important;color:var(--amber) !important;background:var(--s3) !important;}
.stButton>button:focus-visible{outline:1px solid var(--amber) !important;outline-offset:2px;}
.stButton>button:disabled{opacity:.38 !important;cursor:not-allowed;}
.stButton>button[kind="primary"],button[data-testid^="stBaseButton-primary"],[data-testid="stFormSubmitButton"]>button[kind="primary"]{
  background:var(--amber) !important;color:#000 !important;border-color:var(--amber) !important;font-weight:700 !important;}
.stButton>button[kind="primary"]:hover,button[data-testid^="stBaseButton-primary"]:hover{background:var(--amber2) !important;border-color:var(--amber2) !important;color:#000 !important;}

/* inputs */
.stTextInput input,.stNumberInput input,.stDateInput input,.stTextArea textarea{
  background:var(--s1) !important;color:var(--tx) !important;border:1px solid var(--line2) !important;border-radius:3px !important;font:13px var(--sans) !important;}
.stTextInput input,.stNumberInput input{height:38px !important;}
.stTextInput input:focus,.stNumberInput input:focus,.stTextArea textarea:focus{border-color:var(--amber) !important;box-shadow:0 0 0 1px var(--amber) !important;}
input::placeholder,textarea::placeholder{color:#5a5a5a !important;}
[data-baseweb="select"]>div{background:var(--s1) !important;border:1px solid var(--line2) !important;border-radius:3px !important;color:var(--tx) !important;min-height:36px;}
[data-baseweb="select"]>div:hover{border-color:var(--tx3) !important;}
[data-baseweb="select"] input{color:var(--tx) !important;}
[data-baseweb="popover"] ul,[data-baseweb="menu"]{background:var(--s2) !important;border:1px solid var(--line2) !important;}
[data-baseweb="menu"] li:hover,[role="option"]:hover{background:var(--s3) !important;}
[data-baseweb="tag"]{background:rgba(255,159,10,.12) !important;border:1px solid rgba(255,159,10,.35) !important;border-radius:3px !important;}
[data-baseweb="tag"] span{color:var(--amber) !important;font:600 10px var(--mono) !important;}
[data-testid="stFileUploaderDropzone"]{background:var(--s1) !important;border:1px dashed var(--line2) !important;border-radius:3px !important;}
[data-testid="stForm"]{border:1px solid var(--line) !important;border-radius:3px !important;background:var(--s1);padding:12px !important;}

/* radio = segmented pills (used as the view switcher everywhere) */
[data-testid="stRadio"] [role="radiogroup"]{gap:5px !important;flex-wrap:wrap;}
[data-testid="stRadio"] label[data-baseweb="radio"]{background:var(--s1);border:1px solid var(--line2);border-radius:3px;padding:5px 11px 5px 9px;margin:0 !important;transition:border-color .12s ease,background .12s ease;}
[data-testid="stRadio"] label[data-baseweb="radio"]:hover{border-color:var(--tx3);}
[data-testid="stRadio"] label[data-baseweb="radio"]>div:first-child{display:none !important;}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked){border-color:var(--amber);background:rgba(255,159,10,.10);}
[data-testid="stRadio"] label[data-baseweb="radio"] p{font:700 10px var(--mono) !important;letter-spacing:.7px;text-transform:uppercase;color:var(--tx2) !important;}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) p{color:var(--amber) !important;}

/* tabs (still used inside some modules) */
.stTabs [data-baseweb="tab-list"]{background:transparent !important;border:0 !important;border-bottom:1px solid var(--line) !important;gap:2px !important;}
.stTabs [data-baseweb="tab"]{background:transparent !important;border-radius:0 !important;font:700 10px var(--mono) !important;letter-spacing:.9px;text-transform:uppercase;color:var(--tx3) !important;padding:11px 16px !important;height:auto !important;}
.stTabs [data-baseweb="tab"]:hover{color:var(--tx) !important;background:rgba(255,255,255,.03) !important;}
.stTabs [aria-selected="true"]{color:var(--amber) !important;}
.stTabs [data-baseweb="tab-highlight"]{background:var(--amber) !important;height:2px !important;}
.stTabs [data-baseweb="tab-border"]{background:var(--line) !important;}

/* metrics / expanders / alerts / toasts */
[data-testid="stMetric"]{background:var(--s1) !important;border:1px solid var(--line) !important;border-top:2px solid var(--line2) !important;border-radius:0 !important;padding:11px 13px !important;}
[data-testid="stMetricLabel"] p{font:700 9px var(--mono) !important;letter-spacing:1px;text-transform:uppercase;color:var(--tx3) !important;}
[data-testid="stMetricValue"]{font:600 18px var(--mono) !important;font-variant-numeric:tabular-nums;}
[data-testid="stExpander"]{border:1px solid var(--line) !important;border-radius:0 !important;background:var(--s1) !important;}
[data-testid="stExpander"] summary{font:700 10px var(--mono) !important;letter-spacing:.8px;text-transform:uppercase;color:var(--tx2) !important;}
[data-testid="stAlert"]{background:var(--s1) !important;border:1px solid var(--line2) !important;border-left:3px solid var(--cyan) !important;border-radius:0 !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]){border-left-color:var(--amber) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]){border-left-color:var(--red) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]){border-left-color:var(--green) !important;}
[data-testid="stToast"]{background:var(--s2) !important;border:1px solid var(--line2) !important;border-left:3px solid var(--amber) !important;border-radius:3px !important;}
[data-testid="stDataFrame"]{border:1px solid var(--line);}
.stProgress>div>div>div>div{background:var(--amber) !important;}

/* ═════ shell: top bar ═════ */
div[class*="st-key-topbar"]{position:sticky;top:0;z-index:100;background:rgba(5,5,5,.97);backdrop-filter:blur(8px);border-top:2px solid var(--amber);border-bottom:1px solid var(--line);margin:0 -18px;padding:0 18px;}
div[class*="st-key-topbar"] [data-testid="stHorizontalBlock"]{align-items:center;}
.ak-brand{display:flex;align-items:center;gap:10px;height:46px;}
.ak-mark{width:26px;height:26px;background:var(--amber);border-radius:4px;display:flex;align-items:center;justify-content:center;}
.ak-bn{font:800 12px var(--sans);letter-spacing:1.6px;color:var(--tx);}
.ak-bs{font:600 7px var(--mono);letter-spacing:1.6px;color:var(--tx3);margin-top:2px;}
div[class*="st-key-topnav"] .stButton>button{background:transparent !important;border:0 !important;border-bottom:2px solid transparent !important;border-radius:0 !important;color:var(--tx3) !important;font:700 10px var(--mono) !important;letter-spacing:.9px;min-height:46px !important;text-transform:uppercase;padding:0 4px !important;}
div[class*="st-key-topnav"] .stButton>button:hover{color:var(--tx) !important;background:rgba(255,255,255,.035) !important;border-bottom-color:var(--line2) !important;}
div[class*="st-key-topnav"] .stButton>button[kind="primary"],div[class*="st-key-topnav"] button[data-testid^="stBaseButton-primary"]{background:transparent !important;color:var(--amber) !important;border-bottom:2px solid var(--amber) !important;}
div[class*="st-key-topnav"] [data-testid="stHorizontalBlock"]{gap:0 !important;}
div[class*="st-key-topuser"] [data-testid="stPopover"]>div>button{min-height:28px !important;font:700 9px var(--mono) !important;letter-spacing:.8px;border-radius:999px !important;}
.ak-chips{display:flex;align-items:center;justify-content:flex-end;gap:7px;height:46px;}
.ak-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border:1px solid var(--line2);border-radius:999px;font:700 9px var(--mono);letter-spacing:.8px;color:var(--tx2);white-space:nowrap;}
.ak-chip.live{color:var(--green);border-color:rgba(48,209,88,.35);background:rgba(48,209,88,.07);}
.ak-chip.pre{color:var(--amber);border-color:rgba(255,159,10,.35);background:rgba(255,159,10,.07);}
.ak-chip.closed{color:var(--tx3);}
.ak-dot{width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block;}
.ak-chip.live .ak-dot{box-shadow:0 0 8px var(--green);animation:akpulse 2s ease-in-out infinite;}
@keyframes akpulse{0%,100%{opacity:1}50%{opacity:.35}}
.ak-av{width:22px;height:22px;border-radius:50%;background:var(--s3);border:1px solid var(--line2);display:inline-flex;align-items:center;justify-content:center;font:700 10px var(--sans);color:var(--amber);}
.ak-menu-h{font:700 13px var(--sans);color:var(--tx);} .ak-menu-s{font:9px var(--mono);letter-spacing:1px;color:var(--tx3);margin-bottom:8px;}

/* command line */
div[class*="st-key-cmdrow"]{margin:10px 0 2px;}
div[class*="st-key-cmdrow"] [data-baseweb="select"]>div{min-height:40px;border-color:rgba(255,159,10,.35) !important;background:#000 !important;}
div[class*="st-key-cmdrow"] [data-baseweb="select"] input,div[class*="st-key-cmdrow"] .stTextInput input{font:600 13px var(--mono) !important;color:var(--amber) !important;letter-spacing:.6px;}
div[class*="st-key-cmdrow"] .stTextInput input{background:#000 !important;border-color:rgba(255,159,10,.35) !important;height:40px !important;}
.ak-prompt{display:flex;align-items:center;gap:8px;height:40px;font:800 11px var(--mono);letter-spacing:1.2px;color:var(--amber);}
div[class*="st-key-recent"] .stButton>button{min-height:26px !important;font:700 9px var(--mono) !important;letter-spacing:.6px;padding:0 9px !important;border-radius:999px !important;}

/* page header */
.ak-ph{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;padding:14px 2px 12px;border-bottom:1px solid var(--line);margin-bottom:10px;}
.ak-ph .k{font:700 9px var(--mono);letter-spacing:1.6px;color:var(--amber);}
.ak-ph .t{font:800 22px var(--sans);letter-spacing:-.4px;color:var(--tx);margin-top:4px;}
.ak-ph .d{font:12px var(--sans);color:var(--tx3);margin-top:3px;}
.ak-ph .r{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;}

/* index strips */
.ak-strips{border:1px solid var(--line);background:var(--s1);margin:10px 0 0;}
.ak-strip{display:grid;grid-template-columns:78px repeat(6,minmax(0,1fr));border-bottom:1px solid var(--line);}
.ak-strip:last-child{border-bottom:0;}
.ak-sl{display:flex;align-items:center;padding:0 12px;font:700 9px var(--mono);letter-spacing:1.2px;color:var(--tx3);background:var(--s2);}
.ak-ix{position:relative;padding:9px 12px 11px;border-left:1px solid var(--line);min-width:0;}
.ak-ix .n{font:600 9px var(--mono);letter-spacing:.6px;color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ak-ix .p{font:600 14px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:3px;white-space:nowrap;}
.ak-ix .c{position:absolute;right:10px;top:10px;font:700 10px var(--mono);font-variant-numeric:tabular-nums;padding:1px 6px;border-radius:3px;white-space:nowrap;}
.ak-ix .m{position:absolute;left:0;bottom:0;height:2px;opacity:.8;}
.ak-up{color:var(--green);background:rgba(48,209,88,.10);} .ak-dn{color:var(--red);background:rgba(255,69,58,.10);} .ak-fl{color:var(--tx3);background:rgba(255,255,255,.04);}

/* KPI cards */
.ak-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin:12px 0;}
.ak-kpi{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--a,var(--line2));padding:12px 14px;min-width:0;}
.ak-kpi .l{font:700 8px var(--mono);letter-spacing:1.1px;color:var(--tx3);text-transform:uppercase;}
.ak-kpi .v{font:700 20px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:6px;white-space:nowrap;}
.ak-kpi .s{font:10px var(--mono);color:var(--tx3);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}

/* panels */
.ak-grid3{display:grid;grid-template-columns:1.1fr 1.1fr .9fr;gap:8px;margin-top:4px;}
@media(max-width:1300px){.ak-grid3{grid-template-columns:1fr 1fr;}.ak-grid3>.ak-panel:last-child{grid-column:1/-1;}}
@media(max-width:800px){.ak-grid3{grid-template-columns:1fr;}.ak-strip{grid-template-columns:60px repeat(3,minmax(0,1fr));}.ak-strip .ak-ix:nth-child(n+5){display:none;}}
.ak-panel{background:var(--s1);border:1px solid var(--line);min-width:0;overflow:hidden;}
.ak-pt{height:38px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:var(--s2);}
.ak-pt b{font:700 9px var(--mono);letter-spacing:1.2px;color:var(--amber);}
.ak-pt span{font:600 9px var(--mono);color:var(--tx3);}
.ak-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:8px 12px;border-bottom:1px solid #141414;font:11px var(--mono);}
.ak-row:last-child{border-bottom:0;}
.ak-row:hover{background:rgba(255,255,255,.02);}
.ak-row .s{font:700 11px var(--mono);color:var(--tx);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ak-row .p{font-variant-numeric:tabular-nums;color:var(--tx2);text-align:right;}
.ak-row .c{font-weight:700;font-variant-numeric:tabular-nums;min-width:68px;text-align:right;padding:2px 7px;border-radius:3px;}
.ak-empty{padding:18px 14px;font:11px var(--sans);color:var(--tx3);line-height:1.7;}
.ak-empty b{color:var(--tx2);}
.ak-bar{display:flex;height:6px;margin:12px;background:#151515;}
.ak-bar i{display:block;height:100%;}
.ak-sub{font:700 8px var(--mono);letter-spacing:1.2px;color:var(--tx3);padding:9px 12px 3px;}

/* section titles */
.ak-sec{display:flex;align-items:center;gap:12px;margin:24px 0 12px;}
.ak-sec:before{content:"";width:3px;height:14px;background:var(--amber);}
.ak-sec b{font:700 11px var(--mono);letter-spacing:1.6px;color:var(--tx);text-transform:uppercase;white-space:nowrap;}
.ak-sec span{font:11px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ak-sec i{flex:1;height:1px;background:var(--line);}

/* workspace cards */
div[class*="st-key-ak_mod_"]{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:14px 14px 4px;transition:border-color .14s ease,background .14s ease;gap:.2rem;}
div[class*="st-key-ak_mod_"]:hover{background:var(--s2);border-color:var(--line2);border-top-color:var(--amber);}
div[class*="st-key-ak_mod_"] .stButton>button{background:transparent !important;border:0 !important;border-top:1px solid var(--line) !important;border-radius:0 !important;color:var(--amber) !important;justify-content:flex-start !important;padding:10px 0 6px !important;font:700 10px var(--mono) !important;letter-spacing:1.1px;min-height:36px !important;}
.ak-mc{display:flex;gap:12px;align-items:flex-start;}
.ak-mc .t{font:700 13px var(--sans);color:var(--tx);display:flex;align-items:center;gap:8px;}
.ak-mc .t em{font:700 8px var(--mono);letter-spacing:.9px;font-style:normal;color:var(--tx3);border:1px solid var(--line2);padding:1px 6px;border-radius:3px;}
.ak-mc .d{font:11px var(--sans);color:var(--tx3);line-height:1.6;margin-top:5px;min-height:36px;}

/* security page */
.ak-sh{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--amber);padding:16px 18px;margin-top:10px;}
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

/* scanner table */
.ak-tw{overflow:auto;border:1px solid var(--line);background:var(--s1);max-height:640px;}
.ak-t{width:100%;border-collapse:separate;border-spacing:0;font:12px var(--sans);}
.ak-t th{position:sticky;top:0;background:var(--s2);color:var(--tx3);font:700 9px var(--mono);letter-spacing:.9px;text-transform:uppercase;text-align:right;padding:10px;border-bottom:1px solid var(--line2);white-space:nowrap;}
.ak-t th:first-child,.ak-t td:first-child{text-align:left;}
.ak-t td{padding:8px 10px;border-bottom:1px solid #141414;text-align:right;font:11px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx2);white-space:nowrap;}
.ak-t td b{color:var(--tx);font-weight:700;}
.ak-t tbody tr:hover td{background:rgba(255,159,10,.04);}
.ak-st-tag{display:inline-block;padding:2px 8px;border-radius:999px;font:800 8px var(--mono);letter-spacing:.8px;border:1px solid currentColor;}

/* news rail + footer + misc (kept for other modules) */
.news-rail{background:var(--s1);border:1px solid var(--line);}
.news-rail-collapsed{padding:9px 12px;border:1px solid var(--line);background:var(--s1);font:700 9px var(--mono);letter-spacing:1px;color:var(--tx3);}
.news-rail-collapsed span{color:var(--tx4);margin-left:6px;}
.ak-status{margin-top:28px;padding:10px 2px;border-top:1px solid var(--line);font:600 9px var(--mono);letter-spacing:.6px;color:var(--tx3);display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;}
.ak-status b{color:var(--tx2);font-weight:700;}
.term-panel{background:var(--s1);border:1px solid var(--line);padding:14px;}
.panel-title{font:700 9px var(--mono);letter-spacing:1.3px;color:var(--amber);border-bottom:1px solid var(--line);padding:9px 0 7px;margin-bottom:8px;text-transform:uppercase;}
.pulse-dot{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;box-shadow:0 0 8px var(--green);}
.ak-err{border:1px solid rgba(255,69,58,.35);border-left:3px solid var(--red);background:rgba(255,69,58,.05);padding:12px 14px;margin:10px 0;font:12px var(--sans);color:var(--tx2);line-height:1.7;}
.ak-err b{color:var(--red);font:700 11px var(--mono);letter-spacing:.6px;}
.ak-err code{color:var(--tx);background:#111;padding:1px 6px;border-radius:3px;}
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)


# ═══════════════════════════════ HELPERS ═══════════════════════════════
def checkline(text, c=None):
    return (f'<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:12px;">'
            f'<span style="flex-shrink:0;margin-top:2px;">{icon("check", 16, c or GREEN)}</span>'
            f'<span style="font-size:14px;color:{IVORY};line-height:1.6;">{text}</span></div>')


def _market_status():
    """NSE session (label, css class, detail) — holiday-aware via home_page.nse_session."""
    try:
        from home_page import nse_session
        S = nse_session()
        return S["label"], S["cls"], S["detail"]
    except Exception:
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


# ── safe, lazy module loading ─────────────────────────────────
def _is_streamlit_control(e: BaseException) -> bool:
    return type(e).__module__.startswith("streamlit.runtime.scriptrunner")


def _show_module_error(module: str, e: BaseException):
    if isinstance(e, SyntaxError):          # includes IndentationError — point at the exact line
        where = f"<code>{_esc(Path(str(e.filename or module)).name)}</code> line <code>{e.lineno}</code>"
        what = f"{type(e).__name__}: {_esc(str(e.msg))}"
        fix = "Open that file on GitHub, check the indentation on that line and the lines just above it (spaces only, no tabs), then commit."
    else:
        where = f"<code>{_esc(module)}.py</code>"
        what = f"{type(e).__name__}: {_esc(str(e))[:400]}"
        fix = "Check that the file exists in the repo root and that its imports are listed in requirements.txt."
    st.markdown(f'<div class="ak-err"><b>MODULE COULD NOT LOAD · {_esc(module.upper())}</b><br>{what}<br>Where: {where}<br>'
                f'{fix}<br><span style="color:var(--tx3)">Every other workspace keeps working.</span></div>', unsafe_allow_html=True)
    if IS_ADMIN:
        with st.expander("Technical details (admin only)"):
            st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)), language="text")


def _load(module: str, attr: str):
    """Import `attr` from `module` only when needed. Returns None (and explains why) on failure."""
    try:
        return getattr(importlib.import_module(module), attr)
    except BaseException as e:            # SyntaxError / ImportError / anything raised at import time
        if _is_streamlit_control(e):
            raise
        _show_module_error(module, e)
        return None


def _run(module: str, fn, *args, **kwargs):
    """Run a page renderer; a runtime error stays inside that page."""
    if fn is None:
        return
    try:
        fn(*args, **kwargs)
    except Exception as e:
        if _is_streamlit_control(e):
            raise
        _show_module_error(module, e)


# ═══════════════════════════════ LANDING + LOGIN ═══════════════════════════════
LANDING_CSS = """
<style>
.stApp{background:radial-gradient(ellipse 70% 40% at 50% -8%,rgba(255,159,10,.10),transparent),#050505 !important;}
.block-container{padding-top:0 !important;max-width:1400px !important;padding-left:22px !important;padding-right:22px !important;}
[data-testid="stVerticalBlock"]{gap:.4rem;}
div[class*="st-key-cta_"]{display:flex;justify-content:center;}
div[class*="st-key-cta_"] button{padding:0 46px !important;min-height:48px !important;text-transform:uppercase !important;font:800 12px var(--sans) !important;letter-spacing:1.3px !important;}
div[class*="st-key-loginwrap"] [data-testid="stForm"]{padding:26px !important;border:1px solid var(--line2) !important;border-top:2px solid var(--amber) !important;}

.lp-nav{position:sticky;top:0;z-index:60;display:flex;align-items:center;justify-content:space-between;height:58px;margin:0 -22px;padding:0 30px;background:rgba(5,5,5,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);}
.lp-brand{display:flex;align-items:center;gap:10px;}
.lp-mark{width:28px;height:28px;border-radius:5px;background:var(--amber);display:flex;align-items:center;justify-content:center;}
.lp-bn{font:800 13px var(--sans);letter-spacing:1.8px;color:var(--tx);}
.lp-links{display:flex;gap:26px;align-items:center;}
.lp-links a{font:600 12px var(--sans);color:var(--tx2);} .lp-links a:hover{color:var(--tx);}
.lp-links a.go{font:800 10px var(--mono);letter-spacing:1.1px;color:#000;background:var(--amber);padding:8px 16px;border-radius:3px;text-transform:uppercase;}
@media(max-width:800px){.lp-links a:not(.go){display:none;}}

.lp-hero{position:relative;overflow:hidden;margin:0 -22px;padding:96px 22px 46px;text-align:center;border-bottom:1px solid var(--line);}
.lp-hero:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:56px 56px;-webkit-mask-image:radial-gradient(ellipse 75% 70% at 50% 35%,#000 20%,transparent 78%);mask-image:radial-gradient(ellipse 75% 70% at 50% 35%,#000 20%,transparent 78%);animation:lpgrid 24s linear infinite;}
.lp-hero:after{content:"";position:absolute;width:620px;height:620px;left:50%;top:-260px;transform:translateX(-50%);background:radial-gradient(circle,rgba(255,159,10,.16),transparent 62%);pointer-events:none;}
@keyframes lpgrid{from{background-position:0 0,0 0}to{background-position:0 56px,56px 0}}
.lp-in{position:relative;z-index:2;max-width:980px;margin:0 auto;}
.lp-pill{display:inline-flex;align-items:center;gap:9px;padding:6px 14px;border:1px solid rgba(255,159,10,.35);background:rgba(255,159,10,.07);border-radius:999px;font:700 10px var(--mono);letter-spacing:1.6px;color:var(--amber);text-transform:uppercase;}
.lp-pill i{width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);}
.lp-h1{font:800 clamp(36px,6.2vw,74px)/1.03 var(--sans);letter-spacing:-.035em;color:#fff;margin:26px 0 0;}
.lp-h1 span{color:var(--amber);}
.lp-sub{font:clamp(14px,1.6vw,17px)/1.7 var(--sans);color:rgba(255,255,255,.66);max-width:660px;margin:22px auto 0;}
.lp-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:26px 0 30px;}
.lp-chips span{padding:6px 13px;border:1px solid rgba(255,255,255,.14);border-radius:3px;background:rgba(255,255,255,.035);font:600 11px var(--sans);color:rgba(255,255,255,.8);}
.lp-tape{position:relative;overflow:hidden;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:var(--s1);margin:0 -22px;height:36px;}
.lp-tape div{display:inline-flex;gap:34px;white-space:nowrap;padding:10px 0;animation:lptape 40s linear infinite;font:700 10px var(--mono);letter-spacing:1.4px;color:var(--tx3);}
.lp-tape b{color:var(--amber);font-weight:700;}
@keyframes lptape{from{transform:translateX(0)}to{transform:translateX(-50%)}}

.lp-band{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));border:1px solid var(--line);background:var(--s1);margin:34px 0 0;}
.lp-stat{padding:22px 24px;border-right:1px solid var(--line);}
.lp-stat:last-child{border-right:0;}
.lp-stat .n{font:700 28px var(--mono);color:var(--amber);font-variant-numeric:tabular-nums;}
.lp-stat .l{font:600 12px var(--sans);color:var(--tx3);margin-top:4px;}
.lp-h{text-align:center;margin:80px auto 28px;max-width:760px;}
.lp-h .e{font:700 10px var(--mono);letter-spacing:2.2px;color:var(--amber);text-transform:uppercase;}
.lp-h .t{font:800 34px var(--sans);letter-spacing:-.8px;color:var(--tx);margin-top:10px;line-height:1.15;}
.lp-h .s{font:14px var(--sans);color:var(--tx3);margin-top:12px;line-height:1.7;}
.lp-feats{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;}
.lp-card{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:22px;transition:border-color .15s ease,background .15s ease;}
.lp-card:hover{border-top-color:var(--amber);background:var(--s2);}
.lp-card .t{font:700 15px var(--sans);color:var(--tx);margin:14px 0 8px;}
.lp-card .d{font:13px var(--sans);color:var(--tx3);line-height:1.7;}
.lp-dd .e{font:700 10px var(--mono);letter-spacing:2px;color:var(--amber);text-transform:uppercase;margin-bottom:10px;}
.lp-dd .t{font:800 30px var(--sans);letter-spacing:-.7px;color:var(--tx);line-height:1.2;margin-bottom:18px;}
.lp-mock{background:var(--s1);border:1px solid var(--line2);border-top:2px solid var(--amber);overflow:hidden;}
.lp-mh{display:flex;align-items:center;gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--s2);}
.lp-mh i{width:8px;height:8px;border-radius:50%;background:#2e2e2e;display:inline-block;}
.lp-mh span{font:700 9px var(--mono);letter-spacing:1.2px;color:var(--tx3);margin-left:8px;flex:1;}
.lp-mh em{font:700 8px var(--mono);letter-spacing:1px;font-style:normal;color:var(--tx4);border:1px solid var(--line2);padding:1px 7px;border-radius:3px;}
.lp-mb{padding:14px;}
.lp-lanes{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.lp-lane{border:1px solid var(--line);border-top:2px solid var(--c);padding:10px 12px;background:#080808;min-height:92px;}
.lp-lane b{font:800 9px var(--mono);letter-spacing:1px;color:var(--c);}
.lp-lane small{display:block;font:10px var(--sans);color:var(--tx3);margin:3px 0 8px;}
.lp-p{display:inline-block;margin:0 5px 5px 0;padding:4px 9px;border:1px solid var(--line2);border-left:3px solid var(--c);border-radius:3px;font:600 11px var(--sans);color:var(--tx);}
.lp-p em{font:800 8px var(--mono);font-style:normal;color:var(--green);margin-left:6px;}
.lp-k3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;}
.lp-k{border:1px solid var(--line);border-top:2px solid var(--line2);padding:10px 12px;background:#080808;}
.lp-k .l{font:700 8px var(--mono);letter-spacing:1px;color:var(--tx3);}
.lp-k .v{font:700 16px var(--mono);color:var(--tx);margin-top:5px;}
.lp-bars div{display:flex;align-items:center;gap:10px;margin:7px 0;font:11px var(--sans);color:var(--tx2);}
.lp-bars span{width:150px;flex-shrink:0;} .lp-bars i{display:block;height:6px;background:var(--amber);} .lp-bars em{font:600 10px var(--mono);font-style:normal;color:var(--tx3);}
.lp-steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;}
.lp-step{border:1px solid var(--line);background:var(--s1);padding:24px;}
.lp-step .no{font:700 11px var(--mono);letter-spacing:2px;color:var(--amber);}
.lp-step .t{font:800 16px var(--sans);color:var(--tx);margin:10px 0 8px;}
.lp-step .d{font:13px var(--sans);color:var(--tx3);line-height:1.7;}
.lp-cta{margin:80px 0 16px;text-align:center;padding:50px 20px 30px;border:1px solid var(--line2);border-top:2px solid var(--amber);background:var(--s1);}
.lp-cta .t{font:800 32px var(--sans);letter-spacing:-.7px;color:var(--tx);}
.lp-cta .s{font:14px var(--sans);color:var(--tx3);margin-top:10px;}
.lp-foot{text-align:center;padding:38px 0 30px;font:12px var(--sans);color:var(--tx3);line-height:1.8;}
.lp-foot small{display:block;font-size:11px;opacity:.7;margin-top:6px;}
.lg-left{padding:30px 6px 0;}
.lg-left .t{font:800 36px var(--sans);letter-spacing:-.9px;color:var(--tx);line-height:1.12;margin:26px 0 14px;}
.lg-left .s{font:14px var(--sans);color:var(--tx3);line-height:1.7;max-width:420px;}
.lg-pt{display:flex;gap:12px;align-items:flex-start;margin-top:20px;}
.lg-pt b{display:block;font:700 13px var(--sans);color:var(--tx);} .lg-pt span{font:12px var(--sans);color:var(--tx3);line-height:1.6;}
.lg-card-h{margin-bottom:14px;} .lg-card-h b{font:800 20px var(--sans);color:var(--tx);} .lg-card-h span{display:block;font:12px var(--sans);color:var(--tx3);margin-top:4px;}
</style>
"""


def _lp_nav():
    st.markdown(
        f'<div class="lp-nav"><div class="lp-brand"><div class="lp-mark">{icon("trend", 16, "#000")}</div>'
        '<div class="lp-bn">ARKA TRADES</div></div>'
        '<div class="lp-links"><a href="#lp-platform">Platform</a><a href="#lp-rotation">Sector rotation</a>'
        '<a href="#lp-start">Get started</a><a class="go" href="?login=1" target="_self">Sign in</a></div></div>',
        unsafe_allow_html=True)


def _render_login():
    _lp_nav()
    _, wrap, _ = st.columns([0.35, 3.3, 0.35])
    with wrap:
        left, right = st.columns([1.15, 1], gap="large")
        with left:
            st.markdown(f"""<div class="lg-left">
                <div class="lp-brand"><div class="lp-mark">{icon("trend", 16, "#000")}</div><div class="lp-bn">ARKA TRADES</div></div>
                <div class="t">Your market terminal,<br>one sign-in away.</div>
                <div class="s">Pick up where you left off: your watchlists, portfolio and sector radar are synced to the cloud.</div>
                <div class="lg-pt">{icon_box("activity", AMBER, 34)}<div><b>Sector rotation radar</b><span>See which sectors money is moving into, with RSI, EMA position and relative strength.</span></div></div>
                <div class="lg-pt">{icon_box("flask", PURPLE, 34)}<div><b>Rotation Lab</b><span>Backtest rotation rules after costs, with walk-forward and random-pick checks.</span></div></div>
                <div class="lg-pt">{icon_box("briefcase", CYAN, 34)}<div><b>Portfolio analytics</b><span>Live P&amp;L, sector exposure, risk and an automatic health check.</span></div></div>
            </div>""", unsafe_allow_html=True)
        with right:
            with st.container(key="loginwrap"):
                with st.form("lf"):
                    st.markdown('<div class="lg-card-h"><b>Member login</b><span>Sign in to access your terminal</span></div>', unsafe_allow_html=True)
                    u = st.text_input("Username", placeholder="Enter username")
                    p = st.text_input("Password", placeholder="Enter password", type="password")
                    ok = st.form_submit_button("Sign in", use_container_width=True, type="primary")
                    if ok:
                        if u.strip() == _ADMIN_USER and p.strip() == _ADMIN_PASS:
                            st.session_state.logged_in, st.session_state.is_admin = True, True
                            st.rerun()
                        elif u.strip().lower() == str(_MEMBER_USER).lower() and p.strip().lower() == str(_MEMBER_PASS).lower():
                            st.session_state.logged_in, st.session_state.is_admin = True, False
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")
                if st.button("← Back to home", use_container_width=True, key="back_home"):
                    st.session_state.show_login = False
                    st.rerun()
                st.caption("Educational market analytics. Not SEBI registered. Not investment advice.")


def _render_landing():
    _lp_nav()
    st.markdown(
        '<div class="lp-hero"><div class="lp-in">'
        '<div class="lp-pill"><i></i>NSE · Market research terminal</div>'
        '<div class="lp-h1">Trade with context,<br>not <span>guesswork.</span></div>'
        '<div class="lp-sub">Sector rotation radar with backtests, portfolio analytics, watchlists, scanners, research and AI chart analysis '
        'in one fast, cloud-synced workspace built for Indian markets.</div>'
        '<div class="lp-chips"><span>Sector rotation radar</span><span>Rotation backtests</span><span>Portfolio analytics</span>'
        '<span>Fundamental research</span><span>AI chart analysis</span></div></div></div>', unsafe_allow_html=True)
    if st.button("Get started →", type="primary", key="cta_main"):
        st.session_state.show_login = True
        st.rerun()
    tape = " ".join(f"<span><b>{n}</b> · {d}</span>" for n, d in (
        ("MONEY FLOW", "sector rotation"), ("ROTATION LAB", "walk-forward backtests"), ("FPI FLOWS", "NSDL sector data"),
        ("RESEARCH", "financials · peers · valuation"), ("PORTFOLIO", "P&L · tax · risk"), ("WATCHLIST", "PDH / PDL · RSI"),
        ("SCREENER", "rule-based screens"), ("ARKA AI", "chart analysis")))
    st.markdown(f'<div class="lp-tape"><div>{tape}{tape}</div></div>', unsafe_allow_html=True)

    st.markdown(
        '<div class="lp-band">'
        '<div class="lp-stat"><div class="n">2000+</div><div class="l">NSE stocks covered</div></div>'
        '<div class="lp-stat"><div class="n">20+</div><div class="l">Sectors tracked for rotation</div></div>'
        '<div class="lp-stat"><div class="n">30s</div><div class="l">Quote refresh in market hours</div></div>'
        '<div class="lp-stat"><div class="n">24/7</div><div class="l">Cloud-synced workspace</div></div></div>',
        unsafe_allow_html=True)

    st.markdown('<div id="lp-platform"></div><div class="lp-h"><div class="e">The platform</div>'
                '<div class="t">Everything a market desk needs, in one terminal.</div>'
                '<div class="s">Seven connected workspaces share one watchlist, one portfolio and one command line, so context follows you from idea to analysis.</div></div>',
                unsafe_allow_html=True)
    feats = [("activity", AMBER, "Money Flow", "Sector rotation radar with RSI, EMA position and relative strength against the market, mid-caps and small-caps."),
             ("flask", PURPLE, "Rotation Lab", "Backtest rotation rules after costs, with walk-forward tests, random-pick baselines and real FPI sector flows."),
             ("briefcase", CYAN, "Portfolio", "Record buy lots and get live P&amp;L, XIRR, sector exposure, risk metrics and an automatic health check."),
             ("research", CYAN, "Research", "Financials, ownership, peers, valuation bands, health score, technicals and news for any NSE stock."),
             ("filter", GREEN, "Watchlist &amp; Screener", "Live lists with PDH / PDL breaks and RSI, plus rule-based screens across the NSE universe."),
             ("brain", PURPLE, "Arka AI", "Vision-based chart analysis checked against your own trading rules and reference charts.")]
    st.markdown('<div class="lp-feats">' + "".join(
        f'<div class="lp-card">{icon_box(ic, c, 38)}<div class="t">{t}</div><div class="d">{d}</div></div>' for ic, c, t, d in feats) + '</div>',
        unsafe_allow_html=True)

    st.markdown('<div id="lp-rotation" style="height:64px"></div>', unsafe_allow_html=True)
    d1, d2 = st.columns([1, 1.05], gap="large")
    with d1:
        st.markdown(f"""<div class="lp-dd" style="padding-top:18px;"><div class="e">Sector rotation</div>
            <div class="t">See where money is moving, and whether following it has paid.</div>
            {checkline("Every sector placed in a plain-English lane: leading, improving, fading or weak")}
            {checkline("Transparent 0-100 score built from trend, momentum, relative strength, breadth and flow")}
            {checkline("Backtests with costs, walk-forward re-optimisation and a random-pick baseline")}
            {checkline("NSDL fortnightly FPI flows by sector next to the price signals")}</div>""", unsafe_allow_html=True)
    with d2:
        st.markdown("""<div class="lp-mock"><div class="lp-mh"><i></i><i></i><i></i><span>MONEY FLOW · ROTATION BOARD</span><em>ILLUSTRATIVE</em></div>
            <div class="lp-mb"><div class="lp-lanes">
              <div class="lp-lane" style="--c:#5AC8FA"><b>IMPROVING</b><small>Behind, but catching up</small><span class="lp-p" style="--c:#30D158">Capital Goods<em>★ ROTATING IN</em></span><span class="lp-p" style="--c:#5AC8FA">Realty</span></div>
              <div class="lp-lane" style="--c:#30D158"><b>LEADING</b><small>Beating the market, speeding up</small><span class="lp-p" style="--c:#30D158">Banks</span><span class="lp-p" style="--c:#30D158">Insurance</span></div>
              <div class="lp-lane" style="--c:#FF453A"><b>WEAK</b><small>Behind and falling further</small><span class="lp-p" style="--c:#FF453A">Utilities</span><span class="lp-p" style="--c:#FF453A">Textiles</span></div>
              <div class="lp-lane" style="--c:#FF9F0A"><b>FADING</b><small>Ahead, but losing steam</small><span class="lp-p" style="--c:#FF9F0A">Metals &amp; Mining</span></div>
            </div></div></div>""", unsafe_allow_html=True)

    st.markdown('<div style="height:52px"></div>', unsafe_allow_html=True)
    p1, p2 = st.columns([1.05, 1], gap="large")
    with p1:
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
    with p2:
        st.markdown(f"""<div class="lp-dd" style="padding-top:18px;"><div class="e">Portfolio analytics</div>
            <div class="t">A review that reads like a research desk, not a broker app.</div>
            {checkline("Sector and industry exposure, market-cap mix and concentration at a glance")}
            {checkline("Growth of your money versus a NIFTY 50 investment with the same cash flows")}
            {checkline("Volatility, drawdown, beta, one-day VaR and a correlation map")}
            {checkline("Plain-English health check that flags concentration and deep losers")}</div>""", unsafe_allow_html=True)

    st.markdown('<div id="lp-start"></div><div class="lp-h"><div class="e">Onboarding</div><div class="t">Live in three steps.</div></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="lp-steps">'
        '<div class="lp-step"><div class="no">STEP 01</div><div class="t">Sign in and import</div><div class="d">Upload your TradingView watchlist and add your holdings. Everything syncs to the cloud instantly.</div></div>'
        '<div class="lp-step"><div class="no">STEP 02</div><div class="t">Find where money is rotating</div><div class="d">Read the sector radar, check FPI flows and test any rotation rule in the Rotation Lab.</div></div>'
        '<div class="lp-step"><div class="no">STEP 03</div><div class="t">Research and decide</div><div class="d">Open any stock for financials, valuation, health score, technicals and AI chart analysis.</div></div></div>',
        unsafe_allow_html=True)
    st.markdown('<div class="lp-cta"><div class="t">Ready to open your terminal?</div>'
                '<div class="s">Sign in to your workspace. Your lists and portfolio are waiting.</div></div>', unsafe_allow_html=True)
    if st.button("Sign in to Arka →", type="primary", key="cta_bottom"):
        st.session_state.show_login = True
        st.rerun()
    st.markdown('<div class="lp-foot">Arka Trades · Finance &amp; Market Education'
                '<small>Not SEBI registered. All content is for educational purposes only. Trading involves risk: decisions and outcomes are entirely your own. '
                'Examples shown are illustrative and not recommendations.</small></div>', unsafe_allow_html=True)


if not st.session_state.logged_in:
    if st.query_params.get("login") == "1":
        st.session_state.show_login = True
        st.query_params.clear()
    st.markdown(LANDING_CSS, unsafe_allow_html=True)
    if st.session_state.show_login:
        _render_login()
    else:
        _render_landing()
    st.stop()


# ═══════════════════════════════ TERMINAL SHELL ═══════════════════════════════
NAV = [("HOME", "home"), ("MONEY FLOW", "flow"), ("WATCHLIST", "watchlist"), ("PORTFOLIO", "portfolio"),
       ("NEWS", "news"), ("SCREENER", "smart_scan"), ("RESEARCH", "research"), ("ARKA AI", "analysis")]

# Bloomberg-style function codes for the command line (type the code, press Enter)
FUNCTION_CODES = {"HOME": "home", "MAIN": "home", "MF": "flow", "FLOW": "flow", "WL": "watchlist", "PORT": "portfolio",
                  "NEWS": "news", "N": "news", "EQS": "smart_scan", "SCRN": "smart_scan", "RES": "research", "AI": "analysis"}
FUNCTION_MENU = ["MF · Money Flow", "RES · Research", "PORT · Portfolio", "WL · Watchlist", "NEWS · News desk",
                 "EQS · Screener", "AI · Arka AI", "HOME · Market monitor"]
TICKER_FUNCS = {"RES": "research", "AI": "analysis", "DES": "security", "GP": "security"}

PAGE_META = {
    "home": ("MARKET MONITOR", "India first, global context, your book."),
    "flow": ("MONEY FLOW", "Sector rotation radar, Rotation Lab backtests, FPI sector flows, index breadth and FII / DII."),
    "watchlist": ("WATCHLIST", "Your saved NSE stocks with live price, RSI and PDH / PDL status."),
    "portfolio": ("PORTFOLIO", "Holdings, live P&L, sector exposure, risk and an automatic health check."),
    "smart_scan": ("SMART SCREENER", "Rule-based screens across the NSE universe."),
    "news": ("NEWS DESK", "One wire from ET, Business Standard, Mint, Moneycontrol, BusinessLine, NDTV Profit, Google News and BSE filings — tagged to your stocks, scored and clustered."),
    "research": ("RESEARCH", "Financials, ownership, peers, valuation, risk and news in one place."),
    "analysis": ("ARKA AI", "AI chart analysis against your own trading rules."),
    "profile": ("PROFILE", "Your account details."),
    "settings": ("SETTINGS", "Terminal preferences and data controls."),
    "contact": ("CONTACT", "Reach the Arka Trades desk."),
}
if st.session_state.page not in PAGE_META and st.session_state.page != "security":
    st.session_state.page = "home"          # e.g. a saved session still pointing at a removed page (ALERTS, SCANNER, BREADTH)


def _nav_cb(target):
    st.session_state.page = target
    sym = st.session_state.get("active_security")
    if sym and target != "home":
        st.session_state["research_last_query"] = sym
        st.session_state["m1_ticker"] = sym


def _sign_out_cb():
    for k in ("logged_in", "is_admin", "show_login"):
        st.session_state[k] = False
    st.session_state.page = "home"
    st.session_state["active_security"] = ""


def _goto_symbol(symbol: str):
    """State change only (safe inside widget callbacks)."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return
    st.session_state["active_security"] = symbol
    st.session_state["research_last_query"] = symbol
    st.session_state["m1_ticker"] = symbol
    st.session_state.pop("research_data", None)
    st.session_state.page = "security"
    recent = [s for s in st.session_state.get("recent_symbols", []) if s != symbol]
    st.session_state.recent_symbols = ([symbol] + recent)[:6]


def _open_security(symbol: str):
    """Used by workspace modules (on_open). Navigates immediately."""
    _goto_symbol(symbol)
    st.rerun()


def _security_candidates():
    base = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "TCS", "INFY", "WIPRO", "HCLTECH",
            "LT", "ITC", "BHARTIARTL", "TMPV", "M&M", "MARUTI", "SUNPHARMA", "HINDUNILVR", "BAJFINANCE",
            "ADANIENT", "ADANIPORTS", "TATASTEEL", "JSWSTEEL", "NTPC", "POWERGRID", "ONGC", "COALINDIA", "BEL",
            "HAL", "RVNL", "IRFC", "TRENT", "PERSISTENT", "DIXON", "ETERNAL", "DMART", "BSE"]
    try:
        held = ud.holding_symbols()
    except Exception:
        held = []
    out = []
    for source in (st.session_state.get("recent_symbols", []), st.session_state.get("watchlist", []),
                   st.session_state.get("admin_watchlist", []), held, base):
        for x in source:
            if x and x.upper() not in out:
                out.append(x.upper())
    return out + FUNCTION_MENU


def _resolve_query(raw: str) -> str:
    q = (raw or "").split(" · ")[0].strip().upper()
    if not q:
        return ""
    if re.fullmatch(r"[A-Z0-9&\-]{1,20}", q):            # looks like a ticker: no network round-trip
        return q
    try:
        from screener_scraper import resolve_symbol
        res = resolve_symbol(raw)
    except Exception:
        res = None
    if res:
        m = re.search(r"/company/([^/]+)", str(res.get("url", "")), flags=re.I)
        if m:
            return m.group(1).upper()
    return q.replace(" ", "")


def _cmd_cb(key):
    """Command line: function code (MF, PORT …), ticker + function (TCS RES), ticker, or company name."""
    raw = str(st.session_state.get(key) or "")
    st.session_state[key] = None if key == "ak_cmd" else ""
    q = re.sub(r"<\s*GO\s*>|\bGO$", "", raw.split(" · ")[0].strip().upper()).strip()
    if not q:
        return
    if q in FUNCTION_CODES:
        _nav_cb(FUNCTION_CODES[q])
        return
    parts = q.split()
    if len(parts) == 2 and parts[1] in TICKER_FUNCS:
        sym = _resolve_query(parts[0])
        if sym:
            _goto_symbol(sym)
            if TICKER_FUNCS[parts[1]] != "security":
                _nav_cb(TICKER_FUNCS[parts[1]])
        return
    sym = _resolve_query(re.sub(r"<\s*GO\s*>", "", raw, flags=re.I).strip())
    if sym:
        _goto_symbol(sym)


def _render_topbar():
    pg_now = st.session_state.get("page", "home")
    label, cls, detail = _market_status()
    role = "ADMIN" if IS_ADMIN else "MEMBER"
    clock = datetime.now(IST).strftime("%d %b · %H:%M IST")
    with st.container(key="topbar"):
        c1, c2, c3 = st.columns([1.5, 8.0, 3.6], gap="small")
        with c1:
            st.markdown(f'<div class="ak-brand"><div class="ak-mark">{icon("trend", 15, "#000")}</div>'
                        '<div><div class="ak-bn">ARKA TRADES</div><div class="ak-bs">MARKET TERMINAL</div></div></div>', unsafe_allow_html=True)
        with c2:
            with st.container(key="topnav"):
                cols = st.columns(len(NAV), gap="small")
                for col, (lbl, target) in zip(cols, NAV):
                    with col:
                        active = (pg_now == target) or (pg_now == "security" and target == "home")
                        st.button(lbl, key=f"nav_{target}", use_container_width=True,
                                  type="primary" if active else "secondary", on_click=_nav_cb, args=(target,))
        with c3:
            with st.container(key="topuser"):
                a, b = st.columns([2.0, 1.45], gap="small")
                a.markdown(f'<div class="ak-chips"><span class="ak-chip {cls}" title="{_esc(detail)}"><i class="ak-dot"></i>{label}</span>'
                           f'<span class="ak-chip">{clock}</span></div>', unsafe_allow_html=True)
                with b:
                    if hasattr(st, "popover"):
                        with st.popover(f"{initial} · {role}", use_container_width=True):
                            st.markdown(f'<div class="ak-menu-h">{_esc(name)}</div><div class="ak-menu-s">{role} ACCOUNT</div>', unsafe_allow_html=True)
                            st.button("Profile", key="menu_profile", use_container_width=True, on_click=_nav_cb, args=("profile",))
                            st.button("Settings", key="menu_settings", use_container_width=True, on_click=_nav_cb, args=("settings",))
                            st.button("Contact", key="menu_contact", use_container_width=True, on_click=_nav_cb, args=("contact",))
                            st.button("Sign out", key="ak_signout", use_container_width=True, type="primary", on_click=_sign_out_cb)
                    else:
                        st.button("SIGN OUT", key="ak_signout", use_container_width=True, on_click=_sign_out_cb)


def _render_command_row():
    """Bloomberg-style command line on every page: type a ticker or company, press Enter."""
    with st.container(key="cmdrow"):
        recent = [s for s in st.session_state.get("recent_symbols", []) if s != st.session_state.get("active_security")][:5]
        c0, c1, c2 = st.columns([0.55, 6.2, 3.6] if recent else [0.55, 9.8, 0.01], gap="small")
        c0.markdown(f'<div class="ak-prompt">{icon("search", 14)} GO</div>', unsafe_allow_html=True)
        with c1:
            try:
                st.selectbox("Command", _security_candidates(), index=None, key="ak_cmd", on_change=_cmd_cb, args=("ak_cmd",),
                             accept_new_options=True, label_visibility="collapsed",
                             placeholder="Ticker, company or function code  ·  e.g. RELIANCE  ·  tata motors  ·  TCS RES  ·  MF  ·  PORT  ·  NEWS")
            except TypeError:       # Streamlit older than accept_new_options
                st.text_input("Command", key="ak_cmd_txt", on_change=_cmd_cb, args=("ak_cmd_txt",), label_visibility="collapsed",
                              placeholder="Type a ticker or company name and press Enter  ·  e.g. RELIANCE")
        if recent:
            with c2:
                with st.container(key="recent"):
                    rc = st.columns(len(recent), gap="small")
                    for col, s in zip(rc, recent):
                        col.button(s, key=f"recent_{s}", use_container_width=True, on_click=_goto_symbol, args=(s,))


def _page_header(pg_key):
    title, desc = PAGE_META.get(pg_key, (pg_key.upper(), ""))
    sym = st.session_state.get("active_security")
    ctx = f'<span class="ak-chip" title="Active security">ACTIVE · {_esc(sym)}</span>' if sym else ""
    st.markdown(f'<div class="ak-ph"><div><div class="k">ARKA MARKET TERMINAL</div><div class="t">{_esc(title)}</div>'
                f'<div class="d">{_esc(desc)}</div></div><div class="r">{ctx}</div></div>', unsafe_allow_html=True)


def _status_bar():
    label, _, detail = _market_status()
    st.markdown(f'<div class="ak-status"><span><b>ARKA TRADES {APP_VERSION}</b> · {label} · {_esc(detail)}</span>'
                '<span>Data: Yahoo Finance · NSE public endpoints · Screener.in · NSDL — delayed, for information only</span>'
                '<span>Educational use · Not SEBI registered · Not investment advice</span></div>', unsafe_allow_html=True)


# ═══════════════════════════════ PAGES ═══════════════════════════════
def _fmt_num(value, prefix="", suffix=""):
    if value in (None, "", "—", "-"):
        return "—"
    return f"{prefix}{value}{suffix}"


def _render_security_workspace(symbol: str):
    symbol = symbol.upper().strip()
    with st.spinner(f"Loading {symbol}…"):
        B, hist = warm_security(symbol)
    s = B.get("summary") or {}
    sec = B.get("sector") or {}
    name_ = B.get("name") or symbol

    price = prev = None
    if hist is not None and len(hist) >= 2:
        price, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
    elif to_float(s.get("current_price")):
        price = to_float(s.get("current_price"))
    chg = ((price - prev) / prev * 100) if (price is not None and prev) else None
    if hist is not None and len(hist) >= 2:
        h252 = hist.tail(252)
        hi52, lo52 = float(h252["High"].max()), float(h252["Low"].min())
        pdh, pdl = float(hist["High"].iloc[-2]), float(hist["Low"].iloc[-2])
    else:
        hi52, lo52, pdh, pdl = to_float(s.get("year_high")), to_float(s.get("year_low")), None, None
    f = lambda v: f"₹{v:,.2f}" if v is not None else "—"

    sector_txt = sec.get("Sector", sec.get("Broad Sector", "")) or ""
    industry_txt = sec.get("Industry", sec.get("Broad Industry", "")) or ""
    price_txt = f"₹{price:,.2f}" if price is not None else "—"
    chg_html = ""
    if chg is not None:
        cc = "ak-up" if chg >= 0 else "ak-dn"
        chg_html = f'<span class="c {cc}">{"▲" if chg >= 0 else "▼"} {abs(chg):.2f}% today</span>'
    tags = "".join(f'<span class="ak-tag">{_esc(t)}</span>' for t in dict.fromkeys([sector_txt, industry_txt, "NSE"]) if t and t != "—")
    st.markdown(f'<div class="ak-sh"><div><span class="sym">{_esc(symbol)}</span><div class="nm">{_esc(name_)}</div>'
                f'<div class="tg">{tags}</div></div><div class="px"><div class="v">{price_txt}</div>{chg_html}</div></div>', unsafe_allow_html=True)
    stats = [("52W HIGH", f(hi52)), ("52W LOW", f(lo52)), ("PDH", f(pdh)), ("PDL", f(pdl)),
             ("MKT CAP", _fmt_num(s.get("market_cap"), "₹", " Cr")), ("P/E", _fmt_num(s.get("pe_ratio"), "", "x")),
             ("ROE", _fmt_num(s.get("roe"), "", "%")), ("ROCE", _fmt_num(s.get("roce"), "", "%")),
             ("DIV YIELD", _fmt_num(s.get("dividend_yield"), "", "%"))]
    st.markdown('<div class="ak-stats">' + "".join(f'<div class="ak-st"><div class="l">{k}</div><div class="v">{v}</div></div>' for k, v in stats) + '</div>',
                unsafe_allow_html=True)
    if price is not None and hi52 and lo52 and hi52 > lo52:
        pos = max(0.0, min(100.0, (price - lo52) / (hi52 - lo52) * 100))
        st.markdown('<div style="background:var(--s1);border:1px solid var(--line);border-top:0;padding:10px 16px 14px;">'
                    f'<div class="ak-rng"><div class="t"><span>52W LOW {f(lo52)}</span><span>{pos:.0f}% OF 52-WEEK RANGE</span><span>52W HIGH {f(hi52)}</span></div>'
                    f'<div class="b"><i style="left:{pos:.1f}%"></i></div></div></div>', unsafe_allow_html=True)
    if not B.get("resolved"):
        st.caption("Screener fundamentals could not be loaded right now — the chart and technicals still work. Reopen the security in a minute to retry.")

    a1, a2, a3, a4 = st.columns(4, gap="small")
    msg = None
    if a1.button("★  ADD TO WATCHLIST", key=f"sec_wl_{symbol}", use_container_width=True):
        try:
            added, err = ud.add_symbols(supabase, [symbol], ud.W_TABLE)
        except Exception as e:
            added, err = False, str(e)
        if added:
            msg = ("success", f"{symbol} added to watchlist")
        elif not err:
            msg = ("info", f"{symbol} is already in your watchlist")
        if err:
            msg = ("warning" if added else "error", err)
    if a2.button("＋  ADD TO PORTFOLIO", key=f"sec_pf_{symbol}", use_container_width=True):
        st.session_state.page = "portfolio"
        st.rerun()
    if a3.button("◆  ARKA AI ANALYSIS", key=f"sec_ai_{symbol}", use_container_width=True):
        if hasattr(ud, "go_analysis"):
            ud.go_analysis(symbol)
        st.session_state.page = "analysis"
        st.rerun()
    if a4.button("▤  FULL RESEARCH PAGE", key=f"sec_rs_{symbol}", use_container_width=True):
        st.session_state.page = "research"
        st.rerun()
    if msg:
        getattr(st, msg[0])(msg[1])

    chart_col, info_col = st.columns([3.2, 1.55], gap="small")
    with chart_col:
        st.markdown('<div class="ak-pt2">PRICE CHART · CANDLES · VOLUME · MA · RSI 14</div>', unsafe_allow_html=True)
        render_pro_chart = _load("chart_engine", "render_pro_chart")
        _run("chart_engine", render_pro_chart, symbol, height=640)
    with info_col:
        st.markdown('<div class="ak-pt2">COMPANY OVERVIEW</div>', unsafe_allow_html=True)
        overview_items = [("Sector", sec.get("Sector", sec.get("Broad Sector", "—"))), ("Industry", sec.get("Industry", sec.get("Broad Industry", "—"))),
                          ("Market Cap", _fmt_num(s.get("market_cap"), "₹", " Cr")), ("P/E", _fmt_num(s.get("pe_ratio"), "", "x")),
                          ("Book Value", _fmt_num(s.get("book_value"), "₹", "")), ("Dividend Yield", _fmt_num(s.get("dividend_yield"), "", "%")),
                          ("ROCE", _fmt_num(s.get("roce"), "", "%")), ("ROE", _fmt_num(s.get("roe"), "", "%"))]
        st.markdown('<div class="ak-ov">' + "".join(f'<div><span>{k}</span><b>{_esc(v)}</b></div>' for k, v in overview_items) + '</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="ak-pt2">ABOUT</div>', unsafe_allow_html=True)
        about = (B.get("about") or "").strip()
        desc = about[:420] + ("…" if len(about) > 420 else "") if about else (
            f"{name_} is listed on the Indian equity market, classified under {sector_txt or 'equity'}. Full fundamentals are in the research workspace below.")
        st.markdown(f'<div class="ak-desc">{_esc(desc)}</div>', unsafe_allow_html=True)

    st.markdown('<div class="ak-pt2" style="margin-top:14px">RESEARCH WORKSPACE</div>', unsafe_allow_html=True)
    panel = _load("research_page", "render_research_panel")
    news_fn = _load("news_feed", "_fetch_news_for_stock")
    if panel is not None:
        kwargs = {"key": "sec"}
        try:
            if "show_quote" in inspect.signature(panel).parameters:
                kwargs["show_quote"] = False            # the security header already shows the price
        except (TypeError, ValueError):
            pass
        _run("research_page", panel, symbol, TERM_TOKENS, news_fn, **kwargs)


def _page_profile():
    p1, p2 = st.columns([1, 2])
    with p1:
        photo = st.session_state.get("profile_photo")
        if photo:
            st.image(photo, width=110)
            st.caption(name)
        else:
            st.markdown(f'<div style="width:88px;height:88px;background:{DARK3};border:1px solid {LINE2};display:flex;align-items:center;justify-content:center;'
                        f'font-size:32px;color:{AMBER};">{_esc(initial)}</div><div style="font-size:18px;font-weight:800;color:{IVORY};margin-top:12px;">{_esc(name)}</div>',
                        unsafe_allow_html=True)
    with p2:
        with st.form("pf_v7"):
            a, b = st.columns(2)
            nn = a.text_input("Full Name", value=st.session_state.profile["name"])
            np_ = b.text_input("Contact Number", value=st.session_state.profile["phone"])
            ne = st.text_input("Email Address", value=st.session_state.profile["email"])
            ph = st.file_uploader("Upload Profile Photo", type=["jpg", "jpeg", "png"], key="profile_photo_v7")
            if st.form_submit_button("Save Profile", use_container_width=True, type="primary"):
                st.session_state.profile.update({"name": nn, "phone": np_, "email": ne})
                if ph:
                    st.session_state.profile_photo = ph
                st.rerun()


def _page_settings():
    st.markdown(f'<div class="term-panel"><div class="panel-title">DATA</div><div style="color:{T2};font-size:12px;line-height:1.8">'
                'Quotes refresh every 30 s while NSE is open and every 15 min when it is closed. Price history refreshes every 2 min '
                '(1 h when closed); fundamentals every 6 h. Use the button below if anything looks stuck.</div></div>', unsafe_allow_html=True)
    if st.button("CLEAR CACHED MARKET DATA", key="set_clear"):
        st.cache_data.clear()
        st.toast("Cached market data cleared. Fresh data loads on the next view.")
    news_on = st.toggle("Show the market news rail on Home and Security pages", value=st.session_state.show_news_rail, key="set_news")
    st.session_state.show_news_rail = news_on
    st.markdown(f'<div class="term-panel" style="margin-top:10px"><div class="panel-title">ABOUT</div><div style="color:{T2};font-size:12px;line-height:1.8">'
                f'Arka Trades {APP_VERSION} · Streamlit {st.__version__} · theme locked to the dark terminal palette.</div></div>', unsafe_allow_html=True)


def _page_contact():
    st.markdown(f'<div class="term-panel"><div style="color:{T2};line-height:1.8;">Questions, feedback or suggestions?<br>'
                'Contact the Arka Trades desk through the configured support email.</div></div>', unsafe_allow_html=True)


def _news_watchlist_for_rail():
    if st.session_state.get("page") == "portfolio":
        try:
            held = ud.holding_symbols()
        except Exception:
            held = []
        if held:
            return held, "YOUR PORTFOLIO"
    primary_key = "admin_watchlist" if st.session_state.get("active_news_source", "admin") == "admin" else "watchlist"
    wl = st.session_state.get(primary_key, []) or st.session_state.get("admin_watchlist", []) or st.session_state.get("watchlist", [])
    label = "ARKA WATCHLIST" if (wl and wl == st.session_state.get("admin_watchlist")) else "YOUR WATCHLIST"
    return wl, label


@st.fragment
def _news_rail_fragment():
    toggle_label = "HIDE NEWS ▸" if st.session_state.show_news_rail else "◂ SHOW NEWS"
    if st.button(toggle_label, key="toggle_news_rail_fast", use_container_width=True):
        st.session_state.show_news_rail = not st.session_state.show_news_rail
        st.rerun()
    if not st.session_state.show_news_rail:
        st.markdown('<div class="news-rail-collapsed">MARKET NEWS <span>HIDDEN</span></div>', unsafe_allow_html=True)
        return
    st.markdown('<div class="ak-pt news-rail" style="border-bottom:1px solid var(--line);"><b>MARKET NEWS</b><span class="pulse-dot"></span></div>', unsafe_allow_html=True)
    watchlist_for_news, rail_label = _news_watchlist_for_rail()
    if not watchlist_for_news:
        st.markdown('<div class="ak-empty">Macro / global news below. Add stocks in the <b>Watchlist</b> workspace for stock-specific news.</div>', unsafe_allow_html=True)
    render_news_rail = _load("news_feed", "render_news_rail")
    _run("news_feed", render_news_rail, watchlist_for_news, label=rail_label,
         current_security=st.session_state.get("active_security") if st.session_state.get("page") == "security" else None)


# ═══════════════════════════════ ROUTER ═══════════════════════════════
pg = st.session_state.page
_render_topbar()
_render_command_row()
if pg == "security":
    _run("home_page", _load("home_page", "render_tape"))          # cross-asset tape above the security page
elif pg in PAGE_META and pg != "home":                            # HOME draws its own compact header
    _page_header(pg)

if pg in {"home", "security"} and st.session_state.show_news_rail:
    center, right_rail = st.columns([4.55, 1.15], gap="small")
elif pg in {"home", "security"}:
    center, right_rail = st.columns([5.6, 0.45], gap="small")
else:
    center, right_rail = st.container(), None

with center:
    if pg == "home" or (pg == "security" and not st.session_state.get("active_security")):
        _run("home_page", _load("home_page", "render_home"), supabase, on_open=_open_security, nav=_nav_cb)
    elif pg == "security":
        _render_security_workspace(st.session_state["active_security"])
    elif pg == "watchlist":
        _run("watchlist_page", _load("watchlist_page", "render_watchlist"), supabase, on_open=_open_security, is_admin=IS_ADMIN)
    elif pg == "portfolio":
        _run("portfolio_page", _load("portfolio_page", "render_portfolio"), supabase, on_open=_open_security)
    elif pg == "research":
        if st.session_state.get("active_security"):
            st.session_state["research_last_query"] = st.session_state["active_security"]
        _run("research_page", _load("research_page", "render_research_page"), TERM_TOKENS,
             news_fetch_fn=_load("news_feed", "_fetch_news_for_stock"))
    elif pg == "analysis":
        if st.session_state.get("active_security"):
            st.session_state["m1_ticker"] = st.session_state["active_security"]
        _run("arka_ai", _load("arka_ai", "render_arka_ai"))
    elif pg == "smart_scan":
        _run("smart_scan_page", _load("smart_scan_page", "render_smart_scanner"), supabase)
    elif pg == "news":
        _run("news_feed", _load("news_feed", "render_news_terminal"),
             list(dict.fromkeys(st.session_state.get("watchlist", []) + st.session_state.get("admin_watchlist", []))),
             on_open=_open_security, current_security=st.session_state.get("active_security") or None)
    elif pg == "flow":
        _run("money_flow", _load("money_flow", "render_money_flow"), on_open=_open_security)
    elif pg == "profile":
        _page_profile()
    elif pg == "settings":
        _page_settings()
    elif pg == "contact":
        _page_contact()
    _status_bar()

if right_rail is not None:
    with right_rail:
        _news_rail_fragment()
