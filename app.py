"""
Arka Trades — Market Terminal v9.5

v9.5: Screener and Arka AI are admin-only. Members do not see them in the tabs, the phone menu, the search codes or the
      stock page, and a direct link or old session lands on Home with a short notice.

v9.4.1: with the news column hidden, Home uses the full width (no empty side strip); "Show news" is a small button on the right.

v9.4: the Arka mark (arka_icon.png) sits next to the wordmark in the header, on the opening and sign-in screens, and is
      the browser-tab / home-screen icon. Pages no longer fade to grey while Streamlit re-runs (smoother tab switches).

v9.3: two new pages. Regime (market regime and breadth dashboard) and Backtest (walk-forward backtester and expectancy
      engine). Desktop tabs, phone "More" menu and the command bar (REG, BT) open them. Needs quant_data.py, quant_ui.py,
      regime_engine.py, regime_page.py, backtest_engine.py and backtest_page.py next to app.py.

v9.2: premium buttons everywhere: soft depth, press-in on tap, glow on primary actions, a tap ripple and a light haptic
      tick on Android (from auth.py). Respects "reduce motion".

v9.1 (after testing on a phone)
  * Phone layout rebuilt like a trading app: slim top bar with the ARKA TRADES wordmark, a fixed bottom tab bar
    (Home, Watchlist, Portfolio, Research, More) and a phone version of the Home page. Navigation no longer uses
    st.radio (Streamlit Cloud rendered it as wrapped letters); it is plain buttons in flex rows.
  * Desktop: wordmark + search + status on one line, a pill tab bar below, wider pages.
  * Logo: arka_logo.png = white ARKA TRADES wordmark (transparent); arka_icon.png = home-screen / tab icon.
  * Streamlit Cloud's floating badges are hidden for visitors; empty style blocks no longer add top spacing.

What changed vs v8.1
--------------------
SIGN-IN THAT STAYS
  * "Keep me signed in" now actually works on Streamlit Community Cloud. v8.1 relied on a cookie the Cloud proxy
    strips, so every visit asked for the password again. auth.py v2 keeps a signed 30-day token in the browser and
    renews it on every visit, so a device you use regularly stays signed in. Sign out removes it from that device.
  * While the browser answers (well under a second) a short "Opening Arka" screen shows instead of the login page.

YOUR LOGO
  * arka_logo.png (repo root) is the browser-tab icon, the iPhone / Android home-screen icon and the logo on the
    landing and sign-in pages. Without the file the Arka mark is drawn automatically.

REDESIGN
  * One calm design system: Inter for text, monospace only for numbers, sentence-case labels, 8 px corners,
    fewer borders, colour kept for meaning (up / down / alerts) instead of decoration.
  * New header, navigation bar, search, page headers, landing page, sign-in card and a single ACCOUNT page
    (profile, this device, display, data, contact) instead of three menu pages.
  * Phones get their own layout, chosen from the real screen width reported by the browser: a slim header, a
    navigation strip pinned to the top that scrolls sideways, full-width search, no side news rail, no stacked
    function cards, 16 px inputs (no iOS zoom). ACCOUNT → Display lets you force Phone or Desktop.

Unchanged: every workspace module and its public function, function codes in the search box, Supabase storage.
"""
from __future__ import annotations

import importlib
import inspect
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from html import escape as _esc
from pathlib import Path

import streamlit as st

import auth                                                             # noqa: E402  (no Streamlit calls at import)

st.set_page_config(page_title="Arka Trades", layout="wide", page_icon=auth.page_icon(), initial_sidebar_state="collapsed")

import user_data as ud                                                  # noqa: E402  (after set_page_config)
from mobile import MOBILE_CSS, PHONE_CSS                                # noqa: E402
from data_engine import warm_security, to_float                         # noqa: E402

try:
    from supabase import create_client
except Exception:  # pragma: no cover
    create_client = None

IST = timezone(timedelta(hours=5, minutes=30))
APP_VERSION = "v9.6"


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


def _db_symbols(table: str) -> list:
    if supabase is None:
        return []
    try:
        res = supabase.table(table).select("symbol").execute()
        return list(dict.fromkeys(r["symbol"] for r in res.data)) if res.data else []
    except Exception:
        return []


# Logins live ONLY in Streamlit Cloud → Settings → Secrets:
#   ADMIN_USER, ADMIN_PASS, MEMBER_USER, MEMBER_PASS, AUTH_SECRET (any long random string)
_ADMIN_USER = str(_secret("ADMIN_USER", ""))
_ADMIN_PASS = str(_secret("ADMIN_PASS", ""))
_MEMBER_USER = str(_secret("MEMBER_USER", ""))
_MEMBER_PASS = str(_secret("MEMBER_PASS", ""))
_AUTH_KEY = auth.derive_key(_secret("AUTH_SECRET", ""), _ADMIN_USER, _ADMIN_PASS, _MEMBER_USER, _MEMBER_PASS)


# ═══════════════════════════════ DESIGN TOKENS ═══════════════════════════════
BG, S1, S2, S3 = "#0B0B0C", "#111113", "#161618", "#1C1C1F"
LINE, LINE2 = "#232326", "#2E2E33"
IVORY, T2, T3 = "#EDEDEF", "#A1A1AA", "#71717A"
AMBER, AMBER2, CYAN, GREEN, RED, PURPLE = "#FF9F0A", "#FFB340", "#5AC8FA", "#30D158", "#FF453A", "#BF5AF2"
FONT = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif"
MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace"

TERM_TOKENS = {"dark": "#000000", "panel": S1, "panel2": S2, "border": LINE2, "ivory": IVORY, "t2": T2, "t3": T3, "row_alt": "#0F0F10",
               "amber": AMBER, "cyan": CYAN, "green": GREEN, "red": RED, "purple": PURPLE, "font": FONT, "mono": MONO}

_ICON_PATHS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "trend": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "briefcase": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "research": '<path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 15l3-4 3 2 4-6"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
    "news": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h10M7 16h6"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
}


def icon(name, size=18, color=None, stroke=1.8):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color or AMBER}" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICON_PATHS.get(name, "")}</svg>')


# ═══════════════════════════════ SESSION STATE ═══════════════════════════════
for k, v in {"logged_in": False, "show_login": False, "page": "home", "is_admin": False,
             "profile": {"name": "Trader", "email": "", "phone": ""}, "profile_photo": None,
             "watchlist": [], "admin_watchlist": [], "db_loaded": False, "layout_pref": "Auto",
             "active_news_source": "admin", "show_news_rail": True, "active_security": "", "recent_symbols": [],
             "alerts": {}, "alert_fired": set()}.items():          # alerts keys kept empty only for older modules
    if k not in st.session_state:
        st.session_state[k] = v

if not st.session_state.db_loaded:
    st.session_state.watchlist = _db_symbols("watchlist") or st.session_state.watchlist
    st.session_state.admin_watchlist = _db_symbols("admin_watchlist") or st.session_state.admin_watchlist
    st.session_state.db_loaded = True

# Restore this device's sign-in (and set the site icon) BEFORE deciding what to show. Must run on every rerun.
def _screen_state() -> str:
    """"page|SYMBOL" for the screen on show. Sent to the browser, which turns every change into a history entry
    (so the phone's back swipe goes back inside the app) and remembers it (so the app reopens where you left it)."""
    ss = st.session_state
    if not ss.get("logged_in") or not ss.get("_ak_url_done"):
        return ""                                         # nothing until the last screen has been restored
    pg_ = ss.get("page", "home")
    if pg_ == "security":
        sym_ = ss.get("active_security") or ""
        if not sym_:
            pg_ = "home"
    elif pg_ == "research":
        sym_ = ss.get("research_last_query") or ss.get("active_security") or ""
    elif pg_ == "analysis":
        sym_ = ss.get("m1_ticker") or ss.get("active_security") or ""
    else:
        sym_ = ""
    return f"{pg_}|{str(sym_).strip().upper()}"


with st.container(key="akbridge"):
    try:
        auth.bridge(_AUTH_KEY, nav=_screen_state(), back=st.session_state.get("_ak_backtag"))
    except TypeError:                                     # an older auth.py without screen history
        try:
            auth.bridge(_AUTH_KEY, nav=_screen_state())
        except TypeError:
            auth.bridge(_AUTH_KEY)

name = st.session_state.profile.get("name", "Trader") or "Trader"
initial = name[0].upper()
IS_ADMIN = st.session_state.get("is_admin", False)


def _is_phone() -> bool:
    """Phone layout: forced in ACCOUNT → Display, else the browser's real width, else the user agent."""
    pref = st.session_state.get("layout_pref", "Auto")
    if pref in ("Phone", "Desktop"):
        return pref == "Phone"
    w = auth.viewport_width()
    if w:
        return w < 820
    try:
        ua = st.context.headers.get("User-Agent", "") or ""
    except Exception:
        ua = ""
    return bool(re.search(r"iPhone|iPod|Android.+Mobile|Windows Phone|Mobile Safari", ua))


IS_PHONE = _is_phone()
st.session_state["_ak_phone"] = IS_PHONE          # read by home_page v2 for its phone layout


# ═══════════════════════════════ DESIGN SYSTEM ═══════════════════════════════
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{
  --bg:#0B0B0C;--s1:#111113;--s2:#161618;--s3:#1C1C1F;--line:#232326;--line2:#2E2E33;
  --tx:#EDEDEF;--tx2:#A1A1AA;--tx3:#71717A;--tx4:#52525B;
  --amber:#FF9F0A;--amber2:#FFB340;--green:#30D158;--red:#FF453A;--cyan:#5AC8FA;--purple:#BF5AF2;
  --sans:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
  --r:8px;--r-sm:6px;color-scheme:dark;
}
*,*::before,*::after{box-sizing:border-box;}
html,body,.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:var(--bg) !important;color:var(--tx) !important;
  font-family:var(--sans) !important;-webkit-font-smoothing:antialiased;}
*{scrollbar-width:thin;scrollbar-color:#2a2a2e transparent;}
::-webkit-scrollbar{width:8px;height:8px;} ::-webkit-scrollbar-thumb{background:#27272a;border-radius:4px;} ::-webkit-scrollbar-track{background:transparent;}
::selection{background:rgba(255,159,10,.28);color:#fff;}

header[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],#MainMenu,footer,.stDeployButton,
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],[data-testid="stStatusWidget"]{display:none !important;}
.block-container{padding:0 24px 40px !important;max-width:1680px !important;}
[data-testid="stVerticalBlock"]{gap:.85rem;}
/* the sign-in bridge and style-only blocks are invisible and take no space */
div[class*="st-key-akbridge"]{position:absolute !important;width:0 !important;height:0 !important;overflow:hidden !important;margin:0 !important;padding:0 !important;}
[data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > style:only-child),
.element-container:has([data-testid="stMarkdownContainer"] > style:only-child){display:none !important;}
[class*="viewerBadge"],[class*="profileContainer"],[data-testid="stAppDeployButton"]{display:none !important;}
[data-testid="stElementContainer"]:has(iframe[title*="arka_bridge"]),.element-container:has(iframe[title*="arka_bridge"]){
  position:absolute !important;width:0 !important;height:0 !important;overflow:hidden !important;margin:0 !important;padding:0 !important;}
[data-testid="stElementContainer"]:has(iframe[height="0"]){display:none !important;}

h1,h2,h3,h4,h5{font-family:var(--sans) !important;letter-spacing:-.01em;color:var(--tx) !important;}
a{color:var(--amber);text-decoration:none;} a:hover{color:var(--amber2);}
[data-testid="stCaptionContainer"],.stCaption{color:var(--tx3) !important;font-size:12px !important;line-height:1.55 !important;}
[data-testid="stWidgetLabel"] p,[data-testid="stWidgetLabel"] label{font:500 12.5px var(--sans) !important;letter-spacing:0 !important;
  text-transform:none !important;color:var(--tx2) !important;}
hr{border-color:var(--line) !important;} iframe{border:none;}

/* buttons: soft depth, a quick press-in on tap, a glow on the primary action (tap ripple + haptic come from auth.py) */
.stButton>button,.stDownloadButton>button,[data-testid^="stBaseButton-secondary"],[data-testid="stFormSubmitButton"]>button,[data-testid="stPopover"]>div>button,
[data-testid="stPopover"] button{
  background:linear-gradient(180deg,#1C1C1F 0%,#161618 100%) !important;color:var(--tx) !important;border:1px solid var(--line2) !important;
  border-radius:10px !important;font:500 13.5px var(--sans) !important;letter-spacing:0;min-height:38px !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 1px 2px rgba(0,0,0,.35) !important;
  transition:transform .14s cubic-bezier(.2,.8,.2,1),background .18s ease,border-color .18s ease,box-shadow .2s ease,color .15s ease !important;
  -webkit-tap-highlight-color:transparent;touch-action:manipulation;user-select:none;-webkit-user-select:none;}
.stButton>button:hover,.stDownloadButton>button:hover,[data-testid="stPopover"] button:hover{border-color:#4A4A52 !important;
  background:linear-gradient(180deg,#232327 0%,#1B1B1E 100%) !important;color:#fff !important;}
.stButton>button:active,.stDownloadButton>button:active,[data-testid="stFormSubmitButton"]>button:active,[data-testid="stPopover"] button:active{
  transform:scale(.965) !important;box-shadow:inset 0 2px 6px rgba(0,0,0,.45) !important;transition-duration:.06s !important;}
.stButton>button:focus-visible{outline:2px solid var(--amber) !important;outline-offset:2px;}
.stButton>button:disabled{opacity:.4 !important;cursor:not-allowed;transform:none !important;}
.stButton>button[kind="primary"],button[data-testid^="stBaseButton-primary"],[data-testid="stFormSubmitButton"]>button[kind="primary"]{
  background:linear-gradient(180deg,#FFB547 0%,#FF9F0A 55%,#F59200 100%) !important;color:#1A1200 !important;border:1px solid rgba(255,190,90,.6) !important;
  font-weight:600 !important;box-shadow:inset 0 1px 0 rgba(255,255,255,.38),0 1px 2px rgba(0,0,0,.4),0 10px 26px -12px rgba(255,159,10,.85) !important;}
.stButton>button[kind="primary"] p,button[data-testid^="stBaseButton-primary"] p{color:#1A1200 !important;font-weight:600 !important;}
.stButton>button[kind="primary"]:hover,button[data-testid^="stBaseButton-primary"]:hover{filter:brightness(1.06);color:#1A1200 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.42),0 1px 2px rgba(0,0,0,.4),0 14px 32px -12px rgba(255,159,10,.95) !important;}
.stButton>button[kind="primary"]:active,button[data-testid^="stBaseButton-primary"]:active{transform:scale(.965) !important;filter:brightness(.97);
  box-shadow:inset 0 2px 8px rgba(120,60,0,.45) !important;}
.ak-ripple{position:absolute;border-radius:50%;pointer-events:none;background:rgba(255,255,255,.16);transform:scale(0);animation:akrip .55s ease-out forwards;}
@keyframes akrip{to{transform:scale(1);opacity:0;}}
@media (prefers-reduced-motion:reduce){.stButton>button,.stButton>button:active{transition:none !important;transform:none !important;}.ak-ripple{display:none;}}

/* inputs */
.stTextInput input,.stNumberInput input,.stDateInput input,.stTextArea textarea{
  background:var(--s1) !important;color:var(--tx) !important;border:1px solid var(--line2) !important;border-radius:var(--r-sm) !important;font:14px var(--sans) !important;}
.stTextInput input,.stNumberInput input{height:38px !important;}
.stTextInput input:focus,.stNumberInput input:focus,.stTextArea textarea:focus{border-color:var(--amber) !important;box-shadow:0 0 0 3px rgba(255,159,10,.15) !important;}
input::placeholder,textarea::placeholder{color:#5c5c66 !important;}
[data-baseweb="select"]>div{background:var(--s1) !important;border:1px solid var(--line2) !important;border-radius:var(--r-sm) !important;color:var(--tx) !important;min-height:38px;}
[data-baseweb="select"]>div:hover{border-color:#45454d !important;}
[data-baseweb="select"] input{color:var(--tx) !important;}
[data-baseweb="popover"] ul,[data-baseweb="menu"]{background:var(--s2) !important;border:1px solid var(--line2) !important;border-radius:var(--r-sm) !important;}
[data-baseweb="menu"] li:hover,[role="option"]:hover{background:var(--s3) !important;}
[data-baseweb="tag"]{background:rgba(255,159,10,.12) !important;border:1px solid rgba(255,159,10,.3) !important;border-radius:4px !important;}
[data-baseweb="tag"] span{color:var(--amber) !important;font:500 12px var(--sans) !important;}
[data-testid="stFileUploaderDropzone"]{background:var(--s1) !important;border:1px dashed var(--line2) !important;border-radius:var(--r) !important;}
[data-testid="stForm"]{border:1px solid var(--line) !important;border-radius:var(--r) !important;background:var(--s1);padding:16px !important;}
[data-testid="stCheckbox"] label p,[data-testid="stToggle"] label p{font:14px var(--sans) !important;color:var(--tx) !important;}

/* radio = segmented control (the view switcher in every workspace); works with any Streamlit radio markup */
[data-testid="stRadio"] [role="radiogroup"]{gap:6px !important;flex-wrap:wrap;}
[data-testid="stRadio"] [role="radiogroup"]>label{flex:0 0 auto;margin:0 !important;padding:5px 12px 5px 8px;border:1px solid var(--line2);border-radius:999px;
  white-space:nowrap;transition:border-color .12s ease,background .12s ease;accent-color:var(--amber);}
[data-testid="stRadio"] [role="radiogroup"]>label:hover{border-color:#45454d;}
[data-testid="stRadio"] [role="radiogroup"]>label:has(input:checked){border-color:rgba(255,159,10,.6);background:rgba(255,159,10,.08);}
[data-testid="stRadio"] [role="radiogroup"]>label p{font:500 13px var(--sans) !important;color:var(--tx2) !important;white-space:nowrap !important;}
[data-testid="stRadio"] [role="radiogroup"]>label:has(input:checked) p{color:var(--tx) !important;}

/* tabs */
.stTabs [data-baseweb="tab-list"]{background:transparent !important;border:0 !important;border-bottom:1px solid var(--line) !important;gap:4px !important;}
.stTabs [data-baseweb="tab"]{background:transparent !important;border-radius:0 !important;font:500 13px var(--sans) !important;color:var(--tx3) !important;
  padding:10px 14px !important;height:auto !important;}
.stTabs [data-baseweb="tab"]:hover{color:var(--tx) !important;}
.stTabs [aria-selected="true"]{color:var(--tx) !important;}
.stTabs [data-baseweb="tab-highlight"]{background:var(--amber) !important;height:2px !important;}
.stTabs [data-baseweb="tab-border"]{background:var(--line) !important;}

/* metrics / expanders / alerts / toasts */
[data-testid="stMetric"]{background:var(--s1) !important;border:1px solid var(--line) !important;border-radius:var(--r) !important;padding:12px 14px !important;}
[data-testid="stMetricLabel"] p{font:500 12px var(--sans) !important;color:var(--tx3) !important;}
[data-testid="stMetricValue"]{font:600 20px var(--mono) !important;font-variant-numeric:tabular-nums;}
[data-testid="stExpander"]{border:1px solid var(--line) !important;border-radius:var(--r) !important;background:var(--s1) !important;}
[data-testid="stExpander"] summary{font:500 13px var(--sans) !important;color:var(--tx) !important;}
[data-testid="stAlert"]{background:var(--s1) !important;border:1px solid var(--line2) !important;border-left:3px solid var(--cyan) !important;border-radius:var(--r-sm) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]){border-left-color:var(--amber) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]){border-left-color:var(--red) !important;}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]){border-left-color:var(--green) !important;}
[data-testid="stToast"]{background:var(--s2) !important;border:1px solid var(--line2) !important;border-radius:var(--r) !important;}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:var(--r);}
.stProgress>div>div>div>div{background:var(--amber) !important;}

/* ═════ shell ═════ */
.ak-hdr{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:56px;}
.ak-id{display:flex;align-items:center;gap:12px;min-width:0;}
.ak-meta{display:flex;align-items:center;justify-content:flex-end;gap:14px;font:500 12.5px var(--sans);color:var(--tx3);white-space:nowrap;min-height:56px;}
.ak-mkt{display:inline-flex;align-items:center;gap:7px;color:var(--tx2);}
.ak-mkt i{width:7px;height:7px;border-radius:50%;background:var(--tx4);display:inline-block;}
.ak-mkt.live i{background:var(--green);box-shadow:0 0 0 3px rgba(48,209,88,.15);} .ak-mkt.live{color:var(--tx);}
.ak-mkt.pre i{background:var(--amber);}
.ak-av{width:30px;height:30px;border-radius:50%;background:var(--s3);border:1px solid var(--line2);display:inline-flex;align-items:center;justify-content:center;
  font:600 12.5px var(--sans);color:var(--tx);flex-shrink:0;}
div[class*="st-key-toprow"] [data-testid="stHorizontalBlock"]{align-items:center;}
div[class*="st-key-toprow"]{border-bottom:1px solid var(--line);margin:0 -24px;padding:0 24px;}

/* row of buttons (desktop tabs, phone tab bar): forced into one flex row whatever the Streamlit version */
div[class*="st-key-tabs"],div[class*="st-key-tabs"] [data-testid="stVerticalBlock"],div[class*="st-key-tabs"] [data-testid="stHorizontalBlock"],
div[class*="st-key-tabbar"],div[class*="st-key-tabbar"] [data-testid="stVerticalBlock"],div[class*="st-key-tabbar"] [data-testid="stHorizontalBlock"]{
  display:flex !important;flex-direction:row !important;flex-wrap:nowrap !important;align-items:center !important;}
div[class*="st-key-tabs"] [data-testid="stElementContainer"],div[class*="st-key-tabs"] .element-container,div[class*="st-key-tabs"] .stButton{width:auto !important;flex:0 0 auto !important;}
div[class*="st-key-tabs"]{gap:4px !important;overflow-x:auto;scrollbar-width:none;padding:10px 0 !important;border-bottom:1px solid var(--line);}
div[class*="st-key-tabs"] [data-testid="stVerticalBlock"]{gap:4px !important;}
div[class*="st-key-tabs"] button{background:transparent !important;border:0 !important;border-radius:8px !important;box-shadow:none !important;filter:none !important;min-height:36px !important;padding:0 14px !important;
  color:var(--tx3) !important;font:500 14px var(--sans) !important;width:auto !important;}
div[class*="st-key-tabs"] button p{font:500 14px var(--sans) !important;white-space:nowrap !important;}
div[class*="st-key-tabs"] button:hover{background:var(--s1) !important;color:var(--tx) !important;}
div[class*="st-key-tabs"] button[kind="primary"],div[class*="st-key-tabs"] button[data-testid="stBaseButton-primary"]{background:var(--s2) !important;color:#fff !important;box-shadow:inset 0 1px 0 rgba(255,255,255,.05) !important;border:0 !important;}
div[class*="st-key-tabs"] button[kind="primary"] p,div[class*="st-key-tabs"] button[data-testid="stBaseButton-primary"] p{color:#fff !important;}
div[class*="st-key-search"] [data-baseweb="select"]>div{min-height:40px;background:var(--s1) !important;border-radius:10px !important;}
div[class*="st-key-search"] [data-baseweb="select"] input,div[class*="st-key-search"] .stTextInput input{font:500 14px var(--sans) !important;}

/* page header */
.ak-ph{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;padding:18px 0 12px;}
.ak-ph .t{font:600 22px var(--sans);letter-spacing:-.4px;color:var(--tx);}
.ak-ph .d{font:14px var(--sans);color:var(--tx3);margin-top:4px;max-width:820px;line-height:1.5;}
.ak-ph .r{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
.ak-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--line2);border-radius:999px;font:500 12px var(--sans);
  color:var(--tx2);white-space:nowrap;}
.ak-chip b{font:600 12px var(--mono);color:var(--tx);}

/* cards / sections shared by pages */
.ak-card{background:var(--s1);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;}
.ak-card h4{font:600 15px var(--sans) !important;margin:0 0 4px;} .ak-card p{font:14px var(--sans);color:var(--tx3);line-height:1.6;margin:0;}
.ak-sec{font:600 15px var(--sans);color:var(--tx);margin:26px 0 10px;}
.ak-sec span{font:400 13px var(--sans);color:var(--tx3);margin-left:8px;}
.ak-kv{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--line);font:14px var(--sans);color:var(--tx3);}
.ak-kv:last-child{border-bottom:0;} .ak-kv b{font-weight:500;color:var(--tx);}
.ak-err{border:1px solid rgba(255,69,58,.3);border-left:3px solid var(--red);border-radius:var(--r-sm);background:rgba(255,69,58,.05);padding:12px 14px;margin:10px 0;
  font:13.5px var(--sans);color:var(--tx2);line-height:1.7;}
.ak-err b{color:var(--red);font-weight:600;} .ak-err code{color:var(--tx);background:#18181b;padding:1px 6px;border-radius:4px;}
.ak-empty{padding:18px 14px;font:14px var(--sans);color:var(--tx3);line-height:1.7;} .ak-empty b{color:var(--tx2);}

/* security page */
.ak-sh{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;background:var(--s1);border:1px solid var(--line);border-radius:var(--r) var(--r) 0 0;
  padding:18px 20px;margin-top:8px;}
.ak-sh .sym{font:600 12px var(--mono);color:var(--amber);letter-spacing:.4px;}
.ak-sh .nm{font:600 24px var(--sans);letter-spacing:-.4px;color:var(--tx);margin-top:4px;}
.ak-sh .tg{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;}
.ak-tag{font:500 12px var(--sans);color:var(--tx2);background:var(--s3);padding:3px 9px;border-radius:999px;}
.ak-sh .px{text-align:right;}
.ak-sh .px .v{font:600 30px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);letter-spacing:-.5px;}
.ak-sh .px .c{display:inline-block;margin-top:6px;font:600 13px var(--mono);padding:3px 9px;border-radius:999px;}
.ak-up{color:var(--green);background:rgba(48,209,88,.10);} .ak-dn{color:var(--red);background:rgba(255,69,58,.10);}
.ak-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(116px,1fr));background:var(--s1);border:1px solid var(--line);border-top:0;}
.ak-st{padding:11px 16px;border-right:1px solid var(--line);min-width:0;}
.ak-st .l{font:500 12px var(--sans);color:var(--tx3);}
.ak-st .v{font:600 14px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:3px;white-space:nowrap;}
.ak-rng{background:var(--s1);border:1px solid var(--line);border-top:0;border-radius:0 0 var(--r) var(--r);padding:12px 18px 16px;}
.ak-rng .t{display:flex;justify-content:space-between;gap:10px;font:500 12px var(--sans);color:var(--tx3);margin-bottom:8px;}
.ak-rng .b{position:relative;height:6px;background:linear-gradient(90deg,rgba(255,69,58,.4),#2a2a2e 50%,rgba(48,209,88,.4));border-radius:3px;}
.ak-rng .b i{position:absolute;top:-4px;width:4px;height:14px;background:var(--amber);border-radius:2px;transform:translateX(-2px);}
.ak-ov{background:var(--s1);border:1px solid var(--line);border-radius:var(--r);padding:4px 14px;}
.ak-desc{background:var(--s1);border:1px solid var(--line);border-radius:var(--r);padding:14px;font:13.5px var(--sans);color:var(--tx2);line-height:1.7;}
.ak-pt2{font:600 14px var(--sans);color:var(--tx);padding:14px 0 8px;}
/* ── stock screen (security workspace) v2 ── */
div[class*="st-key-swback"]{margin:6px 0 2px;}
div[class*="st-key-swback"] button{min-height:34px !important;padding:4px 14px 4px 10px !important;border-radius:999px !important;background:transparent !important;
  border:1px solid var(--line2) !important;color:var(--tx2) !important;font:500 13px var(--sans) !important;}
div[class*="st-key-swback"] button:hover{border-color:#4a4a52 !important;color:var(--tx) !important;background:var(--s2) !important;}
div[class*="st-key-swback"] button p{font:500 13px var(--sans) !important;}
.sw-hero{position:relative;overflow:hidden;background:linear-gradient(180deg,#141416,#101012);border:1px solid var(--line);border-radius:16px;padding:22px 24px 0;margin-top:8px;}
.sw-hero::before{content:"";position:absolute;inset:0 0 auto 0;height:120px;pointer-events:none;
  background:radial-gradient(600px 120px at 12% 0,rgba(255,159,10,.10),transparent 70%);}
.sw-top{position:relative;display:flex;justify-content:space-between;align-items:flex-start;gap:24px;padding-bottom:20px;}
.sw-id{display:flex;gap:16px;align-items:flex-start;min-width:0;}
.sw-av{flex:0 0 auto;width:52px;height:52px;border-radius:14px;display:flex;align-items:center;justify-content:center;font:650 18px var(--sans);letter-spacing:.3px;
  color:var(--amber);background:rgba(255,159,10,.10);border:1px solid rgba(255,159,10,.28);}
.sw-nm{min-width:0;} .sw-nm .s{font:650 26px var(--sans);letter-spacing:-.5px;color:var(--tx);line-height:1.1;}
.sw-nm .n{font:15px var(--sans);color:var(--tx2);margin-top:5px;overflow:hidden;text-overflow:ellipsis;}
.sw-tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px;}
.sw-tag{font:500 12px var(--sans);color:var(--tx2);background:var(--s3);border:1px solid var(--line);padding:3px 10px;border-radius:999px;white-space:nowrap;}
.sw-px{text-align:right;flex:0 0 auto;}
.sw-px .v{font:650 36px var(--sans);font-variant-numeric:tabular-nums;letter-spacing:-.9px;color:var(--tx);line-height:1.05;}
.sw-chg{display:inline-flex;align-items:center;gap:10px;margin-top:9px;font:600 15px var(--sans);font-variant-numeric:tabular-nums;}
.sw-chg b{font:600 13px var(--sans);padding:4px 10px;border-radius:999px;}
.sw-chg.up{color:var(--green);} .sw-chg.up b{background:rgba(48,209,88,.12);} .sw-chg.dn{color:var(--red);} .sw-chg.dn b{background:rgba(255,69,58,.12);}
.sw-px .a{font:12.5px var(--sans);color:var(--tx3);margin-top:8px;}
.sw-rets{position:relative;display:grid;grid-template-columns:repeat(6,1fr);border-top:1px solid var(--line);margin:0 -24px;}
.sw-rt{padding:13px 8px 14px;text-align:center;border-right:1px solid var(--line);} .sw-rt:last-child{border-right:0;}
.sw-rt small{display:block;font:500 12px var(--sans);color:var(--tx3);} .sw-rt b{display:block;font:600 14.5px var(--sans);font-variant-numeric:tabular-nums;margin-top:4px;}
.sw-hero .up,.sw-card .up{color:var(--green);} .sw-hero .dn,.sw-card .dn{color:var(--red);}
div[class*="st-key-swact"]{margin:12px 0 4px;}
div[class*="st-key-swact"] button{min-height:44px !important;border-radius:12px !important;}
.sw-sec{font:600 15px var(--sans);color:var(--tx);padding:18px 0 10px;}
.sw-two{display:grid;grid-template-columns:1.25fr 1fr;gap:14px;margin-top:10px;align-items:stretch;}
.sw-stack{display:flex;flex-direction:column;gap:14px;margin-top:14px;} .sw-stack.side{margin-top:46px;}
.sw-card{background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:16px 18px;min-width:0;}
.sw-h{display:flex;justify-content:space-between;align-items:center;gap:10px;font:600 14.5px var(--sans);color:var(--tx);margin-bottom:12px;}
.sw-h span{font:500 12px var(--sans);color:var(--tx3);}
.sw-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px 18px;} .sw-grid.two{grid-template-columns:repeat(2,1fr);margin-top:14px;padding-top:14px;border-top:1px solid var(--line);}
.sw-st{min-width:0;} .sw-st small{display:block;font:500 12px var(--sans);color:var(--tx3);line-height:1.35;}
.sw-st b{display:block;font:600 15px var(--sans);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.sw-rg{margin-top:16px;} .sw-rg .t{display:flex;justify-content:space-between;gap:10px;font:500 12px var(--sans);color:var(--tx3);margin-bottom:8px;font-variant-numeric:tabular-nums;}
.sw-rg .t .m{color:var(--tx2);}
.sw-rg .b{position:relative;height:6px;background:var(--s3);border-radius:3px;} .sw-rg .b u{position:absolute;left:0;top:0;bottom:0;border-radius:3px;
  background:linear-gradient(90deg,rgba(255,159,10,.25),rgba(255,159,10,.75));}
.sw-rg .b i{position:absolute;top:-4px;width:14px;height:14px;border-radius:50%;background:var(--amber);border:3px solid var(--s1);transform:translateX(-7px);}
.sw-ck{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);font:13.5px var(--sans);color:var(--tx2);}
.sw-ck:last-of-type{border-bottom:0;}
.sw-ck i{flex:0 0 auto;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:700 11px var(--sans);font-style:normal;}
.sw-ck.y i{color:var(--green);background:rgba(48,209,88,.13);} .sw-ck.n i{color:var(--red);background:rgba(255,69,58,.13);}
.sw-ck span{flex:1;min-width:0;} .sw-ck b{font:500 12.5px var(--sans);font-variant-numeric:tabular-nums;color:var(--tx3);white-space:nowrap;}
.sw-score{padding:3px 10px;border-radius:999px;font:600 12px var(--sans) !important;}
.sw-score.up{color:var(--green) !important;background:rgba(48,209,88,.12);} .sw-score.dn{color:var(--red) !important;background:rgba(255,69,58,.12);}
.sw-score.mid{color:var(--amber) !important;background:rgba(255,159,10,.12);}
.sw-note{font:12px var(--sans);color:var(--tx3);margin-top:12px;line-height:1.5;}
.sw-lv{display:grid;grid-template-columns:1fr auto 62px;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);font:13.5px var(--sans);color:var(--tx2);}
.sw-lv:last-child{border-bottom:0;} .sw-lv b{font:600 13.5px var(--sans);font-variant-numeric:tabular-nums;color:var(--tx);}
.sw-lv em{font:500 12.5px var(--sans);font-style:normal;font-variant-numeric:tabular-nums;text-align:right;color:var(--tx3);}
.sw-lv.cur{margin:4px -18px;padding:9px 18px;background:rgba(255,159,10,.08);border-top:1px solid rgba(255,159,10,.3);border-bottom:1px solid rgba(255,159,10,.3);
  color:var(--amber);font-weight:600;} .sw-lv.cur b{color:var(--amber);}
.sw-about{font:14px var(--sans);color:var(--tx2);line-height:1.7;}
@media (max-width:820px){
  .sw-hero{padding:16px 16px 0;border-radius:14px;} .sw-top{flex-direction:column;gap:14px;padding-bottom:16px;}
  .sw-av{width:44px;height:44px;border-radius:12px;font-size:16px;} .sw-nm .s{font-size:22px;} .sw-nm .n{font-size:14px;}
  .sw-px{text-align:left;} .sw-px .v{font-size:32px;}
  .sw-rets{grid-template-columns:repeat(3,1fr);margin:0 -16px;} .sw-rt:nth-child(3){border-right:0;} .sw-rt:nth-child(-n+3){border-bottom:1px solid var(--line);}
  .sw-two{grid-template-columns:1fr;} .sw-grid{grid-template-columns:repeat(2,1fr);} .sw-card{padding:14px 15px;} .sw-lv.cur{margin:4px -15px;padding:9px 15px;}
  .sw-stack.side{margin-top:14px;}
  div[class*="st-key-swact"] [data-testid="stHorizontalBlock"]{display:grid !important;grid-template-columns:1fr 1fr;gap:8px !important;}
  div[class*="st-key-swact"] [data-testid="stColumn"],div[class*="st-key-swact"] [data-testid="column"]{width:auto !important;min-width:0 !important;flex:none !important;}
}

/* news rail + footer */
.news-rail-collapsed{padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--s1);font:500 12.5px var(--sans);color:var(--tx3);}
.ak-pt{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--s1);}
.ak-pt b{font:600 13px var(--sans);color:var(--tx);}
.pulse-dot{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;}
.ak-status{margin-top:32px;padding:14px 0;border-top:1px solid var(--line);font:12px var(--sans);color:var(--tx3);display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;}
.ak-status b{color:var(--tx2);font-weight:500;}
.term-panel{background:var(--s1);border:1px solid var(--line);border-radius:var(--r);padding:16px;}
.panel-title{font:600 13px var(--sans);color:var(--tx);border-bottom:1px solid var(--line);padding:0 0 8px;margin-bottom:8px;}
</style>
"""

LANDING_CSS = """
<style>
.stApp{background:var(--bg) !important;}
.block-container{max-width:1160px !important;padding-left:24px !important;padding-right:24px !important;}
[data-testid="stVerticalBlock"]{gap:.5rem;}
.lp-nav{display:flex;align-items:center;justify-content:space-between;height:64px;border-bottom:1px solid var(--line);}
.lp-nav .id{display:flex;align-items:center;gap:10px;font:600 16px var(--sans);color:var(--tx);}
.lp-nav .id img{width:28px;height:28px;border-radius:7px;}
.lp-nav .links{display:flex;gap:26px;align-items:center;}
.lp-nav .links a{font:500 14px var(--sans);color:var(--tx2);} .lp-nav .links a:hover{color:var(--tx);}
.lp-nav .links a.go{color:#111;background:var(--amber);padding:8px 16px;border-radius:var(--r-sm);font-weight:600;}
.lp-hero{padding:96px 0 36px;text-align:center;}
.lp-eyebrow{font:500 13px var(--sans);color:var(--amber);}
.lp-h1{font:700 clamp(34px,5.6vw,60px)/1.06 var(--sans);letter-spacing:-.035em;color:#fff;margin:18px auto 0;max-width:860px;}
.lp-sub{font:clamp(15px,1.6vw,18px)/1.65 var(--sans);color:var(--tx2);max-width:640px;margin:20px auto 0;}
div[class*="st-key-cta"]{display:flex;justify-content:center;}
div[class*="st-key-cta_bottom"]{margin-top:14px;}
div[class*="st-key-cta"] button{padding:0 30px !important;min-height:46px !important;font:600 15px var(--sans) !important;}
.lp-shot{margin:44px auto 0;max-width:1000px;border:1px solid var(--line2);border-radius:12px;background:var(--s1);overflow:hidden;}
.lp-shot .bar{display:flex;gap:6px;padding:12px 14px;border-bottom:1px solid var(--line);}
.lp-shot .bar i{width:10px;height:10px;border-radius:50%;background:#2a2a2e;}
.lp-shot .grid{display:grid;grid-template-columns:1.4fr 1fr;gap:1px;background:var(--line);}
.lp-shot .grid>div{background:var(--s1);padding:18px 20px;min-height:150px;}
.lp-shot .k{font:500 12px var(--sans);color:var(--tx3);} .lp-shot .v{font:600 30px var(--sans);color:#fff;margin-top:6px;letter-spacing:-.5px;}
.lp-shot .c{font:500 13px var(--sans);color:var(--green);margin-top:4px;}
.lp-shot svg{margin-top:12px;display:block;width:100%;}
.lp-shot .row{display:flex;justify-content:space-between;font:13px var(--sans);color:var(--tx2);padding:8px 0;border-bottom:1px solid var(--line);}
.lp-shot .row:last-child{border-bottom:0;} .lp-shot .row b{font:500 13px var(--sans);}
@media(max-width:720px){.lp-shot .grid{grid-template-columns:1fr;}}
.lp-h{margin:96px 0 28px;} .lp-h .e{font:500 13px var(--sans);color:var(--amber);}
.lp-h .t{font:700 30px var(--sans);letter-spacing:-.6px;color:var(--tx);margin-top:8px;line-height:1.2;max-width:640px;}
.lp-feats{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;}
.lp-card{background:var(--s1);border:1px solid var(--line);border-radius:var(--r);padding:22px;}
.lp-card .ic{width:36px;height:36px;border-radius:9px;background:rgba(255,159,10,.10);display:flex;align-items:center;justify-content:center;}
.lp-card .t{font:600 16px var(--sans);color:var(--tx);margin:16px 0 6px;} .lp-card .d{font:14px/1.65 var(--sans);color:var(--tx3);}
.lp-steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;}
.lp-step{border-top:1px solid var(--line2);padding:18px 2px 0;} .lp-step .n{font:600 13px var(--mono);color:var(--amber);}
.lp-step .t{font:600 16px var(--sans);color:var(--tx);margin:8px 0 6px;} .lp-step .d{font:14px/1.65 var(--sans);color:var(--tx3);}
.lp-end{margin:96px 0 0;padding:44px 20px 28px;text-align:center;border:1px solid var(--line);border-radius:12px;background:var(--s1);}
.lp-end .t{font:700 28px var(--sans);letter-spacing:-.6px;color:var(--tx);} .lp-end .s{font:15px var(--sans);color:var(--tx3);margin-top:8px;}
.lp-foot{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:40px 0 36px;margin-top:40px;border-top:1px solid var(--line);
  font:13px/1.7 var(--sans);color:var(--tx3);}
/* sign-in */
div[class*="st-key-signin"]{max-width:420px;margin:56px auto 0;}
div[class*="st-key-signin"] [data-testid="stForm"]{padding:22px !important;}
.si-h{text-align:center;margin-bottom:18px;} .si-h img{width:44px;height:44px;border-radius:11px;}
.si-h .t{font:600 22px var(--sans);letter-spacing:-.3px;color:var(--tx);margin-top:14px;} .si-h .s{font:14px var(--sans);color:var(--tx3);margin-top:4px;}
.si-foot{text-align:center;font:12.5px/1.6 var(--sans);color:var(--tx3);margin-top:14px;}
/* opening screen */
/* v9.4: no grey-out while a page re-runs */
[data-stale="true"],.element-container[data-stale="true"],div[data-testid="stElementContainer"][data-stale="true"]{opacity:1 !important;transition:none !important;}
.sp img{animation:spbreath 1.6s ease-in-out infinite;}
@keyframes spbreath{0%,100%{transform:scale(1);box-shadow:0 0 0 0 rgba(247,148,29,.45)}50%{transform:scale(1.05);box-shadow:0 0 0 14px rgba(247,148,29,0)}}
.sp{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:62vh;gap:16px;}
.sp img{width:56px;height:56px;border-radius:14px;}
.sp .t{font:500 15px var(--sans);color:var(--tx2);}
.sp .bar{width:140px;height:3px;border-radius:2px;background:#1f1f22;overflow:hidden;position:relative;}
.sp .bar i{position:absolute;left:-40%;top:0;bottom:0;width:40%;background:var(--amber);border-radius:2px;animation:spbar 1.1s ease-in-out infinite;}
@keyframes spbar{to{left:100%}}
div[class*="st-key-spskip"]{display:flex;justify-content:center;}
div[class*="st-key-spskip"] button{background:transparent !important;border:0 !important;color:var(--tx3) !important;font-size:13px !important;}
</style>
"""

st.markdown(APP_CSS, unsafe_allow_html=True)
st.markdown(MOBILE_CSS, unsafe_allow_html=True)
if IS_PHONE:
    st.markdown(PHONE_CSS, unsafe_allow_html=True)


# ═══════════════════════════════ HELPERS ═══════════════════════════════
def _market_status():
    """NSE session (label, css class, detail) — holiday-aware via home_page.nse_session."""
    try:
        from home_page import nse_session
        S = nse_session()
        return S["label"], S["cls"], S["detail"]
    except Exception:
        now = datetime.now(IST)
        t = now.hour * 60 + now.minute
        if now.weekday() >= 5:
            return "Market closed", "closed", "Weekend"
        if 9 * 60 <= t < 9 * 60 + 15:
            return "Pre-open", "pre", "Opens 09:15"
        if 9 * 60 + 15 <= t <= 15 * 60 + 30:
            return "Market open", "live", "Closes 15:30"
        return "Market closed", "closed", "Opens 09:15" if t < 9 * 60 else "Closed 15:30"


def _nice(label: str) -> str:
    """'MARKET OPEN' -> 'Market open'."""
    return label[:1].upper() + label[1:].lower() if label.isupper() else label


def _is_streamlit_control(e: BaseException) -> bool:
    return type(e).__module__.startswith("streamlit.runtime.scriptrunner")


def _show_module_error(module: str, e: BaseException):
    if isinstance(e, SyntaxError):
        where = f"<code>{_esc(Path(str(e.filename or module)).name)}</code> line <code>{e.lineno}</code>"
        what = f"{type(e).__name__}: {_esc(str(e.msg))}"
        fix = "Open that file on GitHub, check the indentation on that line and the lines above it (spaces only), then commit."
    else:
        where = f"<code>{_esc(module)}.py</code>"
        what = f"{type(e).__name__}: {_esc(str(e))[:400]}"
        fix = "Check that the file exists in the repo root and that its imports are listed in requirements.txt."
    st.markdown(f'<div class="ak-err"><b>{_esc(module)} could not load.</b> {what}<br>Where: {where}. {fix}<br>'
                '<span style="color:var(--tx3)">Every other workspace keeps working.</span></div>', unsafe_allow_html=True)
    if IS_ADMIN:
        with st.expander("Technical details (admin only)"):
            st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)), language="text")


def _load(module: str, attr: str):
    """Import `attr` from `module` only when needed. Returns None (and explains why) on failure."""
    try:
        return getattr(importlib.import_module(module), attr)
    except BaseException as e:
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


LOGO = auth.logo_data_uri(128)
def _brand(mark: int = 26, text: int = 17) -> str:
    """Logo mark + wordmark."""
    img = (f'<img src="{LOGO}" alt="" style="height:{mark}px;width:{mark}px;border-radius:{max(5, mark // 4)}px;display:block;flex:0 0 auto">'
           if LOGO else "")
    return f'<span style="display:inline-flex;align-items:center;gap:{max(8, mark // 3)}px">{img}{auth.wordmark_html(text)}</span>'


WM = _brand(26, 17)


# ═══════════════════════════════ OPENING SCREEN · LANDING · SIGN-IN ═══════════════════════════════
def _render_opening():
    """Shown for the fraction of a second the browser needs to hand back a saved sign-in."""
    st.markdown(f'<div class="sp">{"<img src=" + chr(34) + LOGO + chr(34) + " alt=" + chr(34) + chr(34) + ">" if LOGO else ""}{auth.wordmark_html(22)}<div class="bar"><i></i></div></div>',
                unsafe_allow_html=True)
    with st.container(key="spskip"):
        if st.button("Go to sign in", key="sp_skip"):
            st.session_state["_ak_ready"] = True
            st.session_state.show_login = True
            st.rerun()

    @st.fragment(run_every=1.0)
    def _wait():
        if auth.waited() > auth.WAIT_S:
            st.rerun()
    _wait()


def _lp_nav():
    st.markdown(f'<div class="lp-nav"><div class="id">{WM}</div>'
                '<div class="links"><a href="#features">Features</a><a href="#how">How it works</a>'
                '<a class="go" href="?login=1" target="_self">Sign in</a></div></div>', unsafe_allow_html=True)


def _render_login():
    _lp_nav()
    with st.container(key="signin"):
        st.markdown(f'<div class="si-h"><div style="display:flex;justify-content:center">{_brand(30, 19)}</div><div class="t">Sign in</div>'
                    '<div class="s">Your watchlists, portfolio and research, synced.</div></div>', unsafe_allow_html=True)
        with st.form("lf"):
            u = st.text_input("Username", placeholder="Username", autocomplete="username")
            p = st.text_input("Password", placeholder="Password", type="password", autocomplete="current-password")
            remember = st.checkbox("Keep me signed in on this device", value=True,
                                   help="Stays signed in for 30 days and renews every time you visit. Use Sign out on shared devices.")
            ok = st.form_submit_button("Sign in", use_container_width=True, type="primary")
            if ok:
                role = None
                if _ADMIN_USER and _ADMIN_PASS and auth.same(u.strip(), _ADMIN_USER) and auth.same(p.strip(), _ADMIN_PASS):
                    role = "admin"
                elif _MEMBER_USER and _MEMBER_PASS and auth.same(u.strip().lower(), _MEMBER_USER.lower()) and auth.same(p.strip().lower(), _MEMBER_PASS.lower()):
                    role = "member"
                if role:
                    st.session_state.logged_in, st.session_state.is_admin = True, role == "admin"
                    if remember:
                        auth.remember(role, _AUTH_KEY)
                    else:
                        auth.forget()
                        st.session_state.logged_in, st.session_state.is_admin = True, role == "admin"
                    st.session_state.show_login = False
                    st.rerun()
                elif not (_ADMIN_PASS or _MEMBER_PASS):
                    st.error("Sign-in is not configured yet: add ADMIN_USER / ADMIN_PASS (and MEMBER_*) to Streamlit secrets.")
                else:
                    st.error("That username and password don't match.")
        if st.button("Back", use_container_width=True, key="back_home"):
            st.session_state.show_login = False
            st.rerun()
        st.markdown('<div class="si-foot">Educational market analytics · not investment advice</div>', unsafe_allow_html=True)


def _landing_preview() -> str:
    """A static product preview drawn in HTML (no screenshot files, no fake performance claims)."""
    pts = [44, 46, 45, 49, 48, 52, 51, 55, 54, 58, 57, 61, 60, 63, 66, 64, 68, 71, 70, 74]
    lo, hi = min(pts), max(pts)
    xy = " ".join(f"{i / (len(pts) - 1) * 600:.0f},{90 - (p - lo) / (hi - lo) * 80:.0f}" for i, p in enumerate(pts))
    chart = (f'<svg viewBox="0 0 600 96" preserveAspectRatio="none" height="96"><polyline points="{xy}" fill="none" stroke="{GREEN}" '
             'stroke-width="2" vector-effect="non-scaling-stroke"/></svg>')
    rows = "".join(f'<div class="row"><span>{n}</span><b style="color:{c}">{v}</b></div>' for n, v, c in (
        ("Capital goods", "Rotating in", GREEN), ("Private banks", "Leading", "#8AD66B"), ("IT services", "Watch", CYAN), ("Metals", "Weakening", AMBER)))
    return (f'<div class="lp-shot"><div class="bar"><i></i><i></i><i></i></div><div class="grid">'
            f'<div><div class="k">NIFTY 50 · example</div><div class="v">24,812.40</div><div class="c">▲ 0.84%</div>{chart}</div>'
            f'<div><div class="k">Sector rotation · example</div>{rows}</div></div></div>')


def _render_landing():
    _lp_nav()
    st.markdown('<div class="lp-hero"><div class="lp-eyebrow">Market research terminal for NSE</div>'
                '<div class="lp-h1">See where money is moving before you decide.</div>'
                '<div class="lp-sub">Sector rotation, watchlists, portfolio analytics, company research and chart analysis in one '
                'workspace built for Indian markets.</div></div>', unsafe_allow_html=True)
    with st.container(key="cta_top"):
        if st.button("Sign in", type="primary", key="cta_main"):
            st.session_state.show_login = True
            st.rerun()
    st.markdown(_landing_preview(), unsafe_allow_html=True)

    feats = [("activity", "Money flow", "Every sector ranked by trend, momentum, relative strength and breadth, with backtests of each signal."),
             ("list", "Watchlists", "Full quotes with today's events: Open=High, gaps, 52-week highs, volume spikes, results and news."),
             ("briefcase", "Portfolio", "Live P&L, XIRR, sector exposure, tax and risk, split-adjusted and synced to the cloud."),
             ("research", "Research", "Financials, valuation, deliveries, peers, quant and filings for any NSE stock."),
             ("search", "Screener", "Describe a setup in plain English, test it on past data, then scan the whole market."),
             ("spark", "Arka AI", "Supply and demand zones, levels and a trade plan checked against your own playbook.")]
    st.markdown('<div id="features" class="lp-h"><div class="e">Features</div><div class="t">One terminal instead of six tabs.</div></div>'
                '<div class="lp-feats">' + "".join(f'<div class="lp-card"><div class="ic">{icon(ic, 18)}</div><div class="t">{t}</div>'
                                                   f'<div class="d">{d}</div></div>' for ic, t, d in feats) + '</div>', unsafe_allow_html=True)
    st.markdown('<div id="how" class="lp-h"><div class="e">How it works</div><div class="t">Set up once, then open it every morning.</div></div>'
                '<div class="lp-steps">'
                '<div class="lp-step"><div class="n">01</div><div class="t">Sign in</div><div class="d">Your device stays signed in, on your phone and laptop.</div></div>'
                '<div class="lp-step"><div class="n">02</div><div class="t">Add your stocks</div><div class="d">Import a TradingView watchlist and your holdings in a minute.</div></div>'
                '<div class="lp-step"><div class="n">03</div><div class="t">Read the market</div><div class="d">Start on the market monitor, drill into any stock from the search bar.</div></div>'
                '</div>', unsafe_allow_html=True)
    st.markdown('<div class="lp-end"><div class="t">Open your terminal</div><div class="s">Your lists and portfolio are where you left them.</div></div>',
                unsafe_allow_html=True)
    with st.container(key="cta_bottom"):
        if st.button("Sign in", type="primary", key="cta_end"):
            st.session_state.show_login = True
            st.rerun()
    st.markdown('<div class="lp-foot"><span>Arka Trades · market education and research</span>'
                '<span>Not SEBI registered. Information only, not investment advice. Trading involves risk.</span></div>', unsafe_allow_html=True)


if not st.session_state.logged_in:
    if st.query_params.get("login") == "1":
        st.session_state.show_login = True
        st.query_params.clear()
    st.markdown(LANDING_CSS, unsafe_allow_html=True)
    if not auth.ready():
        _render_opening()
    elif st.session_state.show_login:
        _render_login()
    else:
        _render_landing()
    st.stop()

IS_ADMIN = st.session_state.get("is_admin", False)       # the bridge may have just signed this device in
ADMIN_PAGES = {"smart_scan": "Screener", "analysis": "Arka AI"}   # pages only admins can open


def _allowed(page: str) -> bool:
    return IS_ADMIN or page not in ADMIN_PAGES


# ═══════════════════════════════ TERMINAL SHELL ═══════════════════════════════
NAV = [("Home", "home"), ("Regime", "regime"), ("Money flow", "flow"), ("Watchlist", "watchlist"), ("Portfolio", "portfolio"), ("News", "news"),
       ("Screener", "smart_scan"), ("Backtest", "backtest"), ("Research", "research"), ("Arka AI", "analysis"), ("Account", "account")]
NAV_LABEL = {t: l for l, t in NAV}
LEGACY_PAGES = {"profile": "account", "settings": "account", "contact": "account"}

FUNCTION_CODES = {"HOME": "home", "MAIN": "home", "MF": "flow", "FLOW": "flow", "WL": "watchlist", "PORT": "portfolio",
                  "NEWS": "news", "N": "news", "EQS": "smart_scan", "SCRN": "smart_scan", "RES": "research", "AI": "analysis",
                  "ACCT": "account", "SET": "account", "REG": "regime", "MKT": "regime", "BT": "backtest", "BACK": "backtest"}
FUNCTION_MENU = ["MF · Money flow", "RES · Research", "PORT · Portfolio", "WL · Watchlist", "NEWS · News", "EQS · Screener",
                 "AI · Arka AI", "REG · Market regime", "BT · Backtest lab", "HOME · Market monitor", "ACCT · Account"]
TICKER_FUNCS = {"RES": "research", "AI": "analysis", "DES": "security", "GP": "security"}
if not IS_ADMIN:                                          # members: admin pages disappear from every menu
    NAV = [(l, t) for l, t in NAV if _allowed(t)]
    FUNCTION_CODES = {k: v for k, v in FUNCTION_CODES.items() if _allowed(v)}
    FUNCTION_MENU = [m for m in FUNCTION_MENU if not m.startswith(("EQS ", "AI "))]
    TICKER_FUNCS = {k: v for k, v in TICKER_FUNCS.items() if _allowed(v)}

PAGE_META = {
    "home": ("Market monitor", "India first, global context and your book."),
    "flow": ("Money flow", "Where money is moving across sectors, how strong it is, and whether following it has worked."),
    "watchlist": ("Watchlist", "Your stocks with full quotes and today's events."),
    "portfolio": ("Portfolio", "Holdings, live P&L, exposure, tax and risk."),
    "smart_scan": ("Screener", "Describe a setup, test it on the past, scan the market."),
    "news": ("News", "Market wire, company news and exchange filings, tagged to your stocks."),
    "research": ("Research", "Financials, valuation, ownership, quant and filings for one company."),
    "analysis": ("Arka AI", "Chart analysis against your own trading rules."),
    "account": ("Account", "Profile, this device, display and data."),
    "regime": ("Market regime", "Trend, breadth, distribution and volatility: how hard to press right now."),
    "backtest": ("Backtest lab", "Walk-forward test of your setups on years of NSE data, with expectancy and drawdown odds."),
}
st.session_state.page = LEGACY_PAGES.get(st.session_state.page, st.session_state.page)
if st.session_state.page not in PAGE_META and st.session_state.page != "security":
    st.session_state.page = "home"
if not _allowed(st.session_state.page):                  # a member reached an admin page (old session, another module's button)
    st.session_state["_ak_denied"] = ADMIN_PAGES[st.session_state.page]
    st.session_state.page = "home"


def _nav_cb(target):
    target = LEGACY_PAGES.get(target, target)
    if not _allowed(target):
        st.session_state["_ak_denied"] = ADMIN_PAGES[target]
        target = "home"
    st.session_state.page = target
    sym = st.session_state.get("active_security")
    if sym and target != "home":
        st.session_state["research_last_query"] = sym
        st.session_state["m1_ticker"] = sym


def _nav_radio_cb(key):
    label = st.session_state.get(key)
    target = next((t for l, t in NAV if l == label), None)
    if target:
        _nav_cb(target)


def _sign_out_cb():
    auth.forget()
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


# ── screen history, tap-to-open and resume ──────────────────────────────────
_SYM_OK = re.compile(r"^[A-Z0-9][A-Z0-9&._\-]{0,24}$")
RESUME_HOURS = 6                                          # reopen the last screen if it was used within this time


def _apply_screen(state) -> bool:
    """Show the screen described by "page|SYMBOL" (from the back button, the URL or this device's last visit)."""
    if not isinstance(state, str) or "|" not in state:
        return False
    p_, _, s_ = state.partition("|")
    p_ = LEGACY_PAGES.get(p_.strip().lower(), p_.strip().lower())
    s_ = s_.strip().upper()
    if s_ and not _SYM_OK.match(s_):
        s_ = ""
    if (p_ not in PAGE_META and p_ != "security") or not _allowed(p_):
        return False
    ss = st.session_state
    if p_ == "security":
        if not s_:
            p_ = "home"
        else:
            _goto_symbol(s_)
            return True
    if s_ and p_ in ("research", "analysis"):
        if ss.get("research_last_query") != s_:
            ss.pop("research_data", None)
        ss["active_security"] = ss["research_last_query"] = ss["m1_ticker"] = s_
    ss.page = p_
    return True


def _resume_target():
    """Where this session should start: a link (?p=&s=), the address bar after a reload, or the last screen used."""
    try:
        qp = st.query_params.get("p")
        if qp:
            return f"{qp}|{st.query_params.get('s', '')}"
    except Exception:
        pass
    boot = st.session_state.get("_ak_boot") or {}
    if isinstance(boot.get("loc"), str):
        return boot["loc"]
    saved = boot.get("saved")
    try:
        if isinstance(saved, dict) and isinstance(saved.get("s"), str) \
                and time.time() * 1000 - float(saved.get("t") or 0) < RESUME_HOURS * 3600 * 1000:
            return saved["s"]
    except Exception:
        pass
    return None


_ss = st.session_state
if not _ss.get("_ak_url_done"):
    # wait for the browser's answer (it arrives with the sign-in check) unless there is no bridge at all
    if _ss.get("_ak_boot") is not None or getattr(auth, "_bridge_component", None) is None or auth.waited() > 4 \
            or st.query_params.get("p"):
        _ss["_ak_url_done"] = True
        _t = _resume_target()
        if _t and _ss.page in ("home", "") and _t.split("|")[0] != "home":
            _apply_screen(_t)
        st.rerun()                                        # registers the first screen with the browser before drawing
_nav_in = _ss.pop("_ak_nav_in", None)                    # back / forward in the browser
if _nav_in is not None:
    if _nav_in != _screen_state() and _apply_screen(_nav_in):
        _trail = _ss.get("_ak_trail") or []
        if _screen_state() in _trail:                     # a step (or jump) back: keep the in-app trail in step
            _at = len(_trail) - 1 - _trail[::-1].index(_screen_state())
            _ss["_ak_trail"] = _trail[:_at]
            _ss["_ak_trail_skip"] = True
        st.rerun()
_open_in = _ss.pop("_ak_open_in", None)                  # a stock was tapped anywhere in the app
if _open_in:
    _open_in = str(_open_in).strip().upper()
    if _SYM_OK.match(_open_in):
        _open_security(_open_in)

# the trail of screens behind the one on show (used by the Back button on the stock screen)
if _ss.get("_ak_url_done"):
    _now = _screen_state()
    if _now and _now != _ss.get("_ak_trail_last"):
        _prev = _ss.get("_ak_trail_last")
        if _prev and not _ss.pop("_ak_trail_skip", False):
            _ss["_ak_trail"] = ((_ss.get("_ak_trail") or []) + [_prev])[-30:]
        _ss["_ak_trail_last"] = _now
    _ss.pop("_ak_trail_skip", None)


def _back_target() -> str:
    trail = st.session_state.get("_ak_trail") or []
    return trail[-1] if trail else "home|"


def _back_cb():
    """The Back button: return to the screen that was open before this one."""
    ss = st.session_state
    trail = ss.get("_ak_trail") or []
    target = trail.pop() if trail else "home|"
    ss["_ak_trail"] = trail
    if _apply_screen(target):
        ss["_ak_trail_skip"] = True
        ss["_ak_backtag"] = f"b{time.time():.3f}"        # tells the browser to step back rather than add a screen
    else:
        ss.page = "home"


def _screen_label(state: str) -> str:
    p_, _, s_ = (state or "home|").partition("|")
    if p_ == "security" and s_:
        return s_
    return (PAGE_META.get(p_) or ("Home",))[0] if p_ != "home" else "Home"


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
    if re.fullmatch(r"[A-Z0-9&\-]{1,20}", q):
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
    """Search box: function code (MF, PORT …), ticker + function (TCS RES), ticker, or company name."""
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


def _btn(label, key, icon=None, primary=False, on_click=None, args=(), full=False):
    kw = {"key": key, "type": "primary" if primary else "secondary", "on_click": on_click, "args": args}
    if full:
        kw["use_container_width"] = True
    if icon:
        try:
            return st.button(label, icon=icon, **kw)
        except TypeError:                                  # Streamlit without button icons
            pass
    return st.button(label, **kw)


def _hbox(key):
    """A container whose children sit in one row (native horizontal containers when available, CSS otherwise)."""
    try:
        return st.container(key=key, horizontal=True)
    except TypeError:
        return st.container(key=key)


def _status_html(with_clock=True):
    label, cls, detail = _market_status()
    clock = datetime.now(IST).strftime("%a %d %b, %H:%M")
    return (f'<span class="ak-mkt {cls}" title="{_esc(detail)}"><i></i>{_esc(_nice(label))}</span>'
            + (f'<span class="clk">{clock}</span>' if with_clock else "")
            + f'<span class="ak-av" title="{_esc(name)} · {"admin" if IS_ADMIN else "member"}">{_esc(initial)}</span>')


def _render_search():
    with st.container(key="search"):
        try:
            st.selectbox("Search", _security_candidates(), index=None, key="ak_cmd", on_change=_cmd_cb, args=("ak_cmd",),
                         accept_new_options=True, label_visibility="collapsed", placeholder="Search stocks, or type MF, PORT, TCS RES")
        except TypeError:                                  # Streamlit older than accept_new_options
            st.text_input("Search", key="ak_cmd_txt", on_change=_cmd_cb, args=("ak_cmd_txt",), label_visibility="collapsed",
                          placeholder="Search a stock and press Enter")


TABS_DESKTOP = [("Home", "home"), ("Regime", "regime"), ("Money flow", "flow"), ("Watchlist", "watchlist"), ("Portfolio", "portfolio"),
                ("News", "news"), ("Screener", "smart_scan"), ("Backtest", "backtest"), ("Research", "research"), ("Arka AI", "analysis"),
                ("Account", "account")]
TABS_PHONE = [("Home", "home", ":material/home:"), ("Watchlist", "watchlist", ":material/bookmark:"),
              ("Portfolio", "portfolio", ":material/pie_chart:"), ("Research", "research", ":material/query_stats:")]
MORE_PHONE = [("Market regime", "regime", ":material/monitor_heart:"), ("Backtest lab", "backtest", ":material/science:"),
              ("Money flow", "flow", ":material/swap_vert:"), ("News", "news", ":material/newspaper:"),
              ("Screener", "smart_scan", ":material/filter_alt:"), ("Arka AI", "analysis", ":material/auto_awesome:"),
              ("Account", "account", ":material/person:")]
if not IS_ADMIN:
    TABS_DESKTOP = [x for x in TABS_DESKTOP if _allowed(x[1])]
    TABS_PHONE = [x for x in TABS_PHONE if _allowed(x[1])]
    MORE_PHONE = [x for x in MORE_PHONE if _allowed(x[1])]


def _render_desktop_top():
    pg = st.session_state.page
    with st.container(key="toprow"):
        a, b, c = st.columns([1.3, 2.2, 1.5], gap="medium")
        with a:
            st.markdown(f'<div class="ak-hdr"><div class="ak-id">{WM}</div></div>', unsafe_allow_html=True)
        with b:
            _render_search()
        with c:
            st.markdown(f'<div class="ak-meta">{_status_html()}</div>', unsafe_allow_html=True)
    with _hbox("tabs"):
        for label, target in TABS_DESKTOP:
            _btn(label, f"tab_{target}", primary=(pg == target), on_click=_nav_cb, args=(target,))


def _render_phone_top():
    pg = st.session_state.page
    st.markdown(f'<div class="ak-hdr ak-phdr"><div class="ak-id">{_brand(22, 15)}</div>'
                f'<div class="ak-meta">{_status_html(with_clock=False)}</div></div>', unsafe_allow_html=True)
    if pg in ("home", "watchlist", "research", "security", "news", "analysis"):
        _render_search()


def _render_phone_tabbar():
    pg = st.session_state.page
    more_pages = {t for _, t, _ in MORE_PHONE}
    with _hbox("tabbar"):
        for label, target, ic in TABS_PHONE:
            _btn(label, f"tb_{target}", icon=ic, primary=(pg == target), on_click=_nav_cb, args=(target,))
        kw = {"use_container_width": True}
        try:
            pop = st.popover("More", icon=":material/menu:", **kw)
        except TypeError:
            pop = st.popover("More", **kw)
        with pop:
            for label, target, ic in MORE_PHONE:
                _btn(label, f"tbm_{target}", icon=ic, primary=(pg == target), on_click=_nav_cb, args=(target,), full=True)
    if pg in more_pages:
        st.markdown('<style>div[class*="st-key-tabbar"] [data-testid="stPopover"] button{color:#fff !important;}</style>', unsafe_allow_html=True)


def _render_shell_top():
    if IS_PHONE:
        _render_phone_top()
    else:
        _render_desktop_top()


def _page_header(pg_key):
    title, desc = PAGE_META.get(pg_key, (pg_key.title(), ""))
    sym = st.session_state.get("active_security")
    ctx = f'<span class="ak-chip">Active <b>{_esc(sym)}</b></span>' if sym and pg_key in ("research", "analysis", "news") else ""
    if IS_PHONE:
        desc = ""
    st.markdown(f'<div class="ak-ph"><div><div class="t">{_esc(title)}</div><div class="d">{_esc(desc)}</div></div>'
                f'<div class="r">{ctx}</div></div>', unsafe_allow_html=True)


def _status_bar():
    label, _, detail = _market_status()
    st.markdown(f'<div class="ak-status"><span><b>Arka Trades {APP_VERSION}</b> · {_esc(_nice(label))} · {_esc(detail)}</span>'
                '<span>Data: Yahoo Finance, NSE, BSE, Screener.in, NSDL · delayed, for information only</span>'
                '<span>Not SEBI registered · not investment advice</span></div>', unsafe_allow_html=True)


# ═══════════════════════════════ PAGES ═══════════════════════════════
def _fmt_num(value, prefix="", suffix=""):
    if value in (None, "", "—", "-"):
        return "—"
    return f"{prefix}{value}{suffix}"


def _sw_metrics(hist):
    """Everything the stock screen shows that comes from daily candles. Returns {} when there is no usable history."""
    try:
        if hist is None or len(hist) < 2:
            return {}
        h = hist.dropna(subset=["Close"])
        if len(h) < 2:
            return {}
        c, hi, lo = h["Close"].astype(float), h["High"].astype(float), h["Low"].astype(float)
        vol = h["Volume"].astype(float) if "Volume" in h else None
        px, prev = float(c.iloc[-1]), float(c.iloc[-2])
        n = len(c)
        m = {"price": px, "prev": prev, "chg_abs": px - prev, "chg": (px - prev) / prev * 100 if prev else None,
             "open": float(h["Open"].iloc[-1]) if "Open" in h else None, "day_hi": float(hi.iloc[-1]), "day_lo": float(lo.iloc[-1]),
             "pdh": float(hi.iloc[-2]), "pdl": float(lo.iloc[-2]), "n": n}
        try:
            m["date"] = h.index[-1].strftime("%d %b %Y")
        except Exception:
            m["date"] = ""
        y = h.tail(252)
        m["hi52"], m["lo52"] = float(y["High"].max()), float(y["Low"].min())
        if n >= 21:
            m["hi20"], m["lo20"] = float(hi.iloc[-21:-1].max()), float(lo.iloc[-21:-1].min())      # the 20 sessions before today
        rets = {}
        for lab, k in (("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252)):
            if n > k and float(c.iloc[-1 - k]):
                rets[lab] = (px / float(c.iloc[-1 - k]) - 1) * 100
        try:
            yr = c[c.index.year == c.index[-1].year]
            before = c[c.index.year < c.index[-1].year]
            base = float(before.iloc[-1]) if len(before) else float(yr.iloc[0])
            if base:
                rets["YTD"] = (px / base - 1) * 100
        except Exception:
            pass
        m["rets"] = rets
        for lab, k, kind in (("ema10", 10, "e"), ("ema21", 21, "e"), ("sma50", 50, "s"), ("sma200", 200, "s")):
            if n >= k:
                m[lab] = float(c.ewm(span=k, adjust=False).mean().iloc[-1]) if kind == "e" else float(c.rolling(k).mean().iloc[-1])
        if n >= 15:
            d = c.diff()
            up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            last_dn = float(dn.iloc[-1])
            m["rsi"] = 100.0 if last_dn == 0 else float(100 - 100 / (1 + float(up.iloc[-1]) / last_dn))
            pc = c.shift(1)
            tr = (hi - lo).combine((hi - pc).abs(), max).combine((lo - pc).abs(), max)
            atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
            m["atr"], m["atr_pct"] = atr, atr / px * 100 if px else None
        if vol is not None and n >= 21:
            v, avg = float(vol.iloc[-1]), float(vol.iloc[-21:-1].mean())
            m["vol"], m["vol_avg"] = v, avg
            m["rel_vol"] = v / avg if avg else None
        return m
    except Exception:
        return {}


def _sw_qty(v):
    if v is None or v != v:
        return "—"
    for div, suf in ((1e7, " Cr"), (1e5, " L"), (1e3, " K")):
        if abs(v) >= div:
            return f"{v / div:,.2f}{suf}"
    return f"{v:,.0f}"


def _sw_range(lo, hi, px, left, right, mid):
    """A low-to-high bar with a marker at the current price."""
    if lo is None or hi is None or px is None or hi <= lo:
        return ""
    pos = max(0.0, min(100.0, (px - lo) / (hi - lo) * 100))
    return (f'<div class="sw-rg"><div class="t"><span>{left}</span><span class="m">{mid.format(pos=pos)}</span><span>{right}</span></div>'
            f'<div class="b"><u style="width:{pos:.1f}%"></u><i style="left:{pos:.1f}%"></i></div></div>')


def _render_security_workspace(symbol: str):
    symbol = symbol.upper().strip()
    with st.spinner(f"Loading {symbol}…"):
        B, hist = warm_security(symbol)
    B = B or {}
    s = B.get("summary") or {}
    sec = B.get("sector") or {}
    name_ = B.get("name") or symbol
    m = _sw_metrics(hist)
    price = m.get("price")
    if price is None and to_float(s.get("current_price")):
        price = to_float(s.get("current_price"))
    chg = m.get("chg")
    hi52 = m.get("hi52") or to_float(s.get("year_high"))
    lo52 = m.get("lo52") or to_float(s.get("year_low"))
    f = lambda v: f"₹{v:,.2f}" if v is not None else "—"
    cls = lambda v: "up" if (v is not None and v >= 0) else "dn"
    pct = lambda v, d=2: "—" if v is None else f"{v:+.{d}f}%"

    # ── back and breadcrumb ──
    back_to = _back_target()
    with _hbox("swback"):
        _btn(f"Back to {_screen_label(back_to)}", "sw_back", icon=":material/arrow_back:", on_click=_back_cb)

    # ── quote header ──
    sector_txt = sec.get("Sector", sec.get("Broad Sector", "")) or ""
    industry_txt = sec.get("Industry", sec.get("Broad Industry", "")) or ""
    tags = "".join(f'<span class="sw-tag">{_esc(t)}</span>' for t in dict.fromkeys(["NSE", sector_txt, industry_txt]) if t and t != "—")
    if chg is not None:
        chg_html = (f'<div class="sw-chg {cls(chg)}"><span>{m["chg_abs"]:+,.2f}</span><b>{"▲" if chg >= 0 else "▼"} {abs(chg):.2f}%</b></div>')
    else:
        chg_html = ""
    _, mk_cls, _ = _market_status()
    asof = f'{"Last price" if mk_cls == "live" else "Closing price"} · {m["date"]}' if m.get("date") else ""
    rets = m.get("rets") or {}
    ret_html = "".join(f'<div class="sw-rt"><small>{k}</small><b class="{cls(v)}">{pct(v, 1)}</b></div>' for k, v in rets.items())
    st.markdown(
        f'<div class="sw-hero"><div class="sw-top"><div class="sw-id"><div class="sw-av">{_esc(symbol[:2])}</div>'
        f'<div class="sw-nm"><div class="s">{_esc(symbol)}</div><div class="n">{_esc(name_)}</div><div class="sw-tags">{tags}</div></div></div>'
        f'<div class="sw-px"><div class="v">{f(price)}</div>{chg_html}<div class="a">{_esc(asof)}</div></div></div>'
        + (f'<div class="sw-rets">{ret_html}</div>' if ret_html else "") + '</div>', unsafe_allow_html=True)
    if not m:
        st.caption("Price history could not be loaded right now, so ranges and trend are missing. Reopen the stock in a minute to retry.")
    if not B.get("resolved"):
        st.caption("Fundamentals from Screener could not be loaded right now; the chart and technicals still work. Reopen the stock in a minute to retry.")

    # ── actions ──
    in_wl = symbol in (st.session_state.get("watchlist") or [])
    msg = None
    with st.container(key="swact"):
        cols = st.columns(4 if IS_ADMIN else 3, gap="small")
        if cols[0].button("In watchlist" if in_wl else "Add to watchlist", key=f"sec_wl_{symbol}", use_container_width=True,
                          icon=":material/check:" if in_wl else ":material/add:"):
            try:
                added, err = ud.add_symbols(supabase, [symbol], ud.W_TABLE)
            except Exception as e:
                added, err = False, str(e)
            msg = ("success", f"{symbol} added to your watchlist") if added else ("info", f"{symbol} is already in your watchlist")
            if err:
                msg = ("warning" if added else "error", err)
        if cols[1].button("Add to portfolio", key=f"sec_pf_{symbol}", use_container_width=True, icon=":material/work:"):
            st.session_state.page = "portfolio"
            st.rerun()
        if IS_ADMIN and cols[2].button("Arka AI analysis", key=f"sec_ai_{symbol}", use_container_width=True, icon=":material/auto_awesome:"):
            if hasattr(ud, "go_analysis"):
                ud.go_analysis(symbol)
            st.session_state.page = "analysis"
            st.rerun()
        if cols[-1].button("Full research", key=f"sec_rs_{symbol}", use_container_width=True, type="primary", icon=":material/query_stats:"):
            st.session_state.page = "research"
            st.rerun()
    if msg:
        getattr(st, msg[0])(msg[1])

    # ── panels (built once, placed differently on phone and desktop) ──
    def stat(k, v, c=""):
        return f'<div class="sw-st"><small>{k}</small><b class="{c}">{v}</b></div>'

    rv = m.get("rel_vol")
    session = "".join([
        stat("Open", f(m.get("open"))), stat("Previous close", f(m.get("prev"))),
        stat("Day high", f(m.get("day_hi"))), stat("Day low", f(m.get("day_lo"))),
        stat("Volume", _sw_qty(m.get("vol"))),
        stat("Volume vs 20-day average", f"{rv:.2f}×" if rv is not None else "—", "up" if rv is not None and rv >= 1.5 else ""),
    ])
    day_rng = _sw_range(m.get("day_lo"), m.get("day_hi"), price, f'Low {f(m.get("day_lo"))}', f'High {f(m.get("day_hi"))}', "Today's range")
    yr_rng = _sw_range(lo52, hi52, price, f"Low {f(lo52)}", f"High {f(hi52)}", "52-week range · {pos:.0f}%")
    session_html = (f'<div class="sw-card"><div class="sw-h">Session<span>{_esc(m.get("date", ""))}</span></div>'
                    f'<div class="sw-grid">{session}</div>{day_rng}{yr_rng}</div>') if m else ""

    funda = "".join([
        stat("Market cap", _esc(_fmt_num(s.get("market_cap"), "₹", " Cr"))), stat("P/E", _esc(_fmt_num(s.get("pe_ratio"), "", "×"))),
        stat("ROE", _esc(_fmt_num(s.get("roe"), "", "%"))), stat("ROCE", _esc(_fmt_num(s.get("roce"), "", "%"))),
        stat("Book value", _esc(_fmt_num(s.get("book_value"), "₹", ""))), stat("Dividend yield", _esc(_fmt_num(s.get("dividend_yield"), "", "%"))),
    ])
    funda_html = f'<div class="sw-card"><div class="sw-h">Fundamentals<span>Screener.in</span></div><div class="sw-grid">{funda}</div></div>'

    # trend check: plain facts a swing trader looks at first
    checks = []

    def above(lab, key):
        v = m.get(key)
        if v is None or price is None:
            return
        d = (price / v - 1) * 100 if v else None
        checks.append((price >= v, f"Price {'above' if price >= v else 'below'} {lab}", f"{f(v)} · {pct(d, 1)}"))
    above("10-day EMA", "ema10"); above("21-day EMA", "ema21"); above("50-day average", "sma50"); above("200-day average", "sma200")
    if m.get("sma50") is not None and m.get("sma200") is not None:
        ok = m["sma50"] >= m["sma200"]
        checks.append((ok, f"50-day average {'above' if ok else 'below'} 200-day", f'{pct((m["sma50"] / m["sma200"] - 1) * 100, 1)}'))
    if price is not None and hi52:
        off = (price / hi52 - 1) * 100
        checks.append((off >= -15, "Within 15% of 52-week high" if off >= -15 else "More than 15% below 52-week high", pct(off, 1)))
    passed = sum(1 for ok, _, _ in checks if ok)
    rows = "".join(f'<div class="sw-ck {"y" if ok else "n"}"><i>{"✓" if ok else "✕"}</i><span>{_esc(t)}</span><b>{_esc(v)}</b></div>' for ok, t, v in checks)
    extra = []
    if m.get("rsi") is not None:
        r = m["rsi"]
        extra.append(stat("RSI 14", f"{r:.0f}", "dn" if r >= 75 else ""))
    if m.get("atr_pct") is not None:
        extra.append(stat("Average daily range (ATR 14)", f'{m["atr_pct"]:.1f}% · {f(m["atr"])}'))
    trend_html = ""
    if checks:
        tone = "up" if passed >= len(checks) - 1 else "dn" if passed <= 1 else "mid"
        trend_html = (f'<div class="sw-card"><div class="sw-h">Trend check<span class="sw-score {tone}">{passed} of {len(checks)}</span></div>{rows}'
                      + (f'<div class="sw-grid two">{"".join(extra)}</div>' if extra else "")
                      + '<div class="sw-note">Facts from daily closing prices. Not a buy or sell signal.</div></div>')

    # key levels ladder: every level sorted by price, with today's price in its place
    levels = [("52-week high", hi52), ("20-day high", m.get("hi20")), ("Previous day high", m.get("pdh")), ("10-day EMA", m.get("ema10")),
              ("21-day EMA", m.get("ema21")), ("50-day average", m.get("sma50")), ("200-day average", m.get("sma200")),
              ("Previous day low", m.get("pdl")), ("20-day low", m.get("lo20")), ("52-week low", lo52)]
    levels = sorted([(k, v) for k, v in levels if v is not None], key=lambda x: -x[1])
    ladder_html = ""
    if levels and price is not None:
        out, placed = [], False
        for k, v in levels:
            if not placed and v < price:
                out.append(f'<div class="sw-lv cur"><span>Price now</span><b>{f(price)}</b><em></em></div>')
                placed = True
            d = (v / price - 1) * 100 if price else None
            out.append(f'<div class="sw-lv {"r" if v >= price else "s"}"><span>{k}</span><b>{f(v)}</b><em>{pct(d, 1)}</em></div>')
        if not placed:
            out.append(f'<div class="sw-lv cur"><span>Price now</span><b>{f(price)}</b><em></em></div>')
        ladder_html = (f'<div class="sw-card"><div class="sw-h">Key levels<span>distance from price</span></div>{"".join(out)}</div>')

    about = (B.get("about") or "").strip()
    desc = about[:520] + ("…" if len(about) > 520 else "") if about else f"{name_} is listed on NSE{', classified under ' + sector_txt if sector_txt else ''}."
    about_html = f'<div class="sw-card"><div class="sw-h">About</div><div class="sw-about">{_esc(desc)}</div></div>'

    def chart():
        st.markdown('<div class="sw-sec">Price chart</div>', unsafe_allow_html=True)
        _run("chart_engine", _load("chart_engine", "render_pro_chart"), symbol, height=480 if IS_PHONE else 640)

    if IS_PHONE:
        chart()
        st.markdown(f'<div class="sw-stack">{trend_html}{ladder_html}{session_html}{funda_html}{about_html}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="sw-two">{session_html}{funda_html}</div>', unsafe_allow_html=True)
        chart_col, info_col = st.columns([3.1, 1.5], gap="medium")
        with chart_col:
            chart()
        with info_col:
            st.markdown(f'<div class="sw-stack side">{trend_html}{ladder_html}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="sw-stack">{about_html}</div>', unsafe_allow_html=True)

    st.markdown('<div class="sw-sec" style="margin-top:18px">Research</div>', unsafe_allow_html=True)
    panel = _load("research_page", "render_research_panel")
    news_fn = _load("news_feed", "_fetch_news_for_stock")
    if panel is not None:
        kwargs = {"key": "sec"}
        try:
            if "show_quote" in inspect.signature(panel).parameters:
                kwargs["show_quote"] = False
        except (TypeError, ValueError):
            pass
        _run("research_page", panel, symbol, TERM_TOKENS, news_fn, **kwargs)


def _page_account():
    role = "Admin" if IS_ADMIN else "Member"
    left, right = (st.container(), st.container()) if IS_PHONE else st.columns([1.15, 1], gap="large")
    with left:
        st.markdown('<div class="ak-sec">Profile</div>', unsafe_allow_html=True)
        with st.form("acct_profile"):
            a, b = st.columns(2)
            nn = a.text_input("Name", value=st.session_state.profile["name"])
            np_ = b.text_input("Phone", value=st.session_state.profile["phone"])
            ne = st.text_input("Email", value=st.session_state.profile["email"])
            ph = st.file_uploader("Profile photo", type=["jpg", "jpeg", "png"], key="profile_photo_v9")
            if st.form_submit_button("Save profile", type="primary"):
                st.session_state.profile.update({"name": nn.strip() or "Trader", "phone": np_, "email": ne})
                if ph:
                    st.session_state.profile_photo = ph
                st.toast("Profile saved")
                st.rerun()
        st.markdown('<div class="ak-sec">Display</div>', unsafe_allow_html=True)
        st.radio("Layout", ["Auto", "Phone", "Desktop"], key="layout_pref", horizontal=True,
                 help="Auto picks the phone layout on screens narrower than 820 px.")
        st.toggle("Show the news column next to Home and stock pages (desktop)", key="show_news_rail")
        st.markdown('<div class="ak-sec">Data</div>', unsafe_allow_html=True)
        st.caption("Quotes refresh every 30–60 s while NSE is open and every 15 min when it is closed; fundamentals every 6 h.")
        if st.button("Clear cached market data", key="set_clear"):
            st.cache_data.clear()
            st.toast("Cached data cleared. Fresh data loads on the next view.")
    with right:
        st.markdown('<div class="ak-sec">This device</div>', unsafe_allow_html=True)
        w = auth.viewport_width()
        st.markdown('<div class="ak-card">'
                    f'<div class="ak-kv"><span>Signed in as</span><b>{_esc(st.session_state.profile.get("name") or "Trader")} · {role}</b></div>'
                    f'<div class="ak-kv"><span>Stay signed in</span><b>{_esc(auth.device_status())}</b></div>'
                    f'<div class="ak-kv"><span>Layout</span><b>{"Phone" if IS_PHONE else "Desktop"}{f" · {w}px wide" if w else ""}</b></div>'
                    '</div>', unsafe_allow_html=True)
        st.button("Sign out on this device", key="ak_signout", use_container_width=True, on_click=_sign_out_cb)
        st.caption("To sign out every device at once, change AUTH_SECRET in Streamlit secrets.")
        st.markdown('<div class="ak-sec">Add Arka to your home screen</div>', unsafe_allow_html=True)
        st.markdown('<div class="ak-card"><p><b style="color:var(--tx)">iPhone:</b> open this page in Safari, tap Share, then <i>Add to Home Screen</i>.<br>'
                    '<b style="color:var(--tx)">Android:</b> open it in Chrome, tap ⋮, then <i>Add to home screen</i>.<br>'
                    'The Arka icon is used, and you stay signed in.</p></div>', unsafe_allow_html=True)
        st.markdown('<div class="ak-sec">Contact</div>', unsafe_allow_html=True)
        st.markdown('<div class="ak-card"><p>Questions or feedback: reach the Arka Trades desk through the configured support email.</p></div>',
                    unsafe_allow_html=True)
        st.caption(f"Arka Trades {APP_VERSION} · Streamlit {st.__version__}")


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
    if st.button("Hide news" if st.session_state.show_news_rail else "Show news", key="toggle_news_rail_fast", use_container_width=True):
        st.session_state.show_news_rail = not st.session_state.show_news_rail
        st.rerun()
    if not st.session_state.show_news_rail:
        st.markdown('<div class="news-rail-collapsed">News hidden</div>', unsafe_allow_html=True)
        return
    st.markdown('<div class="ak-pt"><b>Market news</b><span class="pulse-dot"></span></div>', unsafe_allow_html=True)
    watchlist_for_news, rail_label = _news_watchlist_for_rail()
    if not watchlist_for_news:
        st.markdown('<div class="ak-empty">Market news below. Add stocks in <b>Watchlist</b> for company news.</div>', unsafe_allow_html=True)
    render_news_rail = _load("news_feed", "render_news_rail")
    _run("news_feed", render_news_rail, watchlist_for_news, label=rail_label,
         current_security=st.session_state.get("active_security") if st.session_state.get("page") == "security" else None)


# ═══════════════════════════════ ROUTER ═══════════════════════════════
pg = st.session_state.page
_render_shell_top()
if pg == "security":
    _run("home_page", _load("home_page", "render_tape"))
elif pg in PAGE_META and pg != "home":
    _page_header(pg)

rail = (not IS_PHONE) and pg in {"home", "security"}
if rail and st.session_state.show_news_rail:
    center, right_rail = st.columns([4.55, 1.15], gap="medium")
elif rail:                                   # news hidden: full width, a small "Show news" button on the right
    _sp, _bt = st.columns([8.6, 1.1], gap="small")
    with _bt:
        try:
            _show = st.button("Show news", key="toggle_news_rail_show", use_container_width=True, icon=":material/newspaper:")
        except TypeError:                                      # Streamlit without button icons
            _show = st.button("Show news", key="toggle_news_rail_show", use_container_width=True)
        if _show:
            st.session_state.show_news_rail = True
            st.rerun()
    center, right_rail = st.container(), None
else:
    center, right_rail = st.container(), None

with center:
    _denied = st.session_state.pop("_ak_denied", None)
    if _denied:
        try:
            st.toast(f"{_denied} is available to admin accounts only.")
        except Exception:
            st.info(f"{_denied} is available to admin accounts only.")
    if pg == "home" or (pg == "security" and not st.session_state.get("active_security")):
        _home = _load("home_page", "render_home")
        try:
            _compact_ok = _home is not None and "compact" in inspect.signature(_home).parameters
        except (TypeError, ValueError):
            _compact_ok = False
        if _compact_ok:
            _run("home_page", _home, supabase, on_open=_open_security, nav=_nav_cb, compact=IS_PHONE)
        else:
            _run("home_page", _home, supabase, on_open=_open_security, nav=_nav_cb)
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
    elif pg in ADMIN_PAGES and not IS_ADMIN:               # last line of defence
        st.info(f"{ADMIN_PAGES[pg]} is available to admin accounts only.")
    elif pg == "analysis":
        if st.session_state.get("active_security"):
            st.session_state["m1_ticker"] = st.session_state["active_security"]
        _run("arka_ai", _load("arka_ai", "render_arka_ai"), supabase)
    elif pg == "smart_scan":
        _run("smart_scan_page", _load("smart_scan_page", "render_smart_scanner"), supabase)
    elif pg == "news":
        _run("news_feed", _load("news_feed", "render_news_terminal"),
             list(dict.fromkeys(st.session_state.get("watchlist", []) + st.session_state.get("admin_watchlist", []))),
             on_open=_open_security, current_security=st.session_state.get("active_security") or None)
    elif pg == "flow":
        _run("money_flow", _load("money_flow", "render_money_flow"), on_open=_open_security)
    elif pg == "regime":
        _run("regime_page", _load("regime_page", "render_regime"), on_open=_open_security)
    elif pg == "backtest":
        _run("backtest_page", _load("backtest_page", "render_backtest"), on_open=_open_security)
    elif pg == "account":
        _page_account()
    _status_bar()

if right_rail is not None:
    with right_rail:
        with st.container(key="newsrail"):
            _news_rail_fragment()

if IS_PHONE:
    _render_phone_tabbar()
