import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import requests
import re
import json
import math
from pathlib import Path
from supabase import create_client, Client
from arka_ai import render_arka_ai
from research_page import render_research_page

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
        # FIX: session_state is the source of truth for this run and every
        # run until the next explicit reload. We do NOT flip db_loaded to
        # False here anymore — doing so used to force an immediate Supabase
        # re-fetch on the very next rerun (e.g. right after clicking "Run
        # Scan"), and if that SELECT raced the INSERT above and returned
        # before Supabase had committed it, it would silently overwrite the
        # watchlist we just saved with the old/empty one. Since we already
        # set st.session_state.watchlist directly below, there is nothing
        # left to "sync" from the DB right away.
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
        # FIX: same reasoning as db_save_watchlist above — set state directly,
        # don't force a racy reload flag.
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

# ════════════════ DESIGN SYSTEM — TERMINAL RESKIN ═══════════════════
# Reskinned from the prior rounded-card / gradient system to a dense,
# flat, Bloomberg-style language:
#   - corners: 12-16px -> 0-2px (flat panels, not cards)
#   - padding: generous -> dense
#   - accents: multi-gradient -> single amber + functional green/red
#   - badges: rounded pills -> square tags, text-only where possible
# Landing page below (pre-login) intentionally keeps its own separate
# green theme — that page was never part of this reskin request.
DARK   = "#000000"
DARK2  = "#0A0A0A"
DARK3  = "#111111"
BORDER = "#262626"
IVORY  = "#E8E8E8"
T2     = "#8A8A8A"
T3     = "#5A5A5A"
NAVY   = "#0A0A0A"

AMBER  = "#FF9F0A"   # primary terminal accent (replaces INDIGO as the "brand" color)
CYAN   = "#5AC8FA"
GREEN  = "#30D158"
RED    = "#FF453A"
INDIGO = "#5E8CFF"   # kept for compatibility with any external module still passing this in
PURPLE = "#BF5AF2"
PINK   = "#FF6482"

BLUE   = AMBER
GOLD   = AMBER

GRAD_BRAND = f"linear-gradient(135deg,{AMBER},{CYAN})"
GRAD_AI    = f"linear-gradient(135deg,{PURPLE},{AMBER})"
GRAD_TEXT  = f"linear-gradient(90deg,{CYAN},{AMBER},{PURPLE})"

FONT = "'Plus Jakarta Sans','Inter',sans-serif"
MONO = "'JetBrains Mono',monospace"

# Shared design-token dict handed to research_page.py so both files
# draw from one palette instead of maintaining two copies of it.
TERM_TOKENS = {
    "dark": DARK, "panel": DARK2, "panel2": DARK3, "border": BORDER,
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
}

def icon(name, size=18, color=None):
    c = color or AMBER
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{c}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'style="vertical-align:middle;">{_ICON_PATHS.get(name,"")}</svg>')

def icon_box(name, color=None, size=32):
    # Square, not rounded — terminal language drops the soft icon chip.
    c = color or AMBER
    return (f'<div style="width:{size}px;height:{size}px;border-radius:2px;background:{c}14;'
            f'border:1px solid {c}33;display:flex;align-items:center;justify-content:center;'
            f'margin-bottom:10px;">{icon(name, 16, c)}</div>')

for k, v in {"logged_in":False,"disclaimer_done":False,"show_login":False,"page":"home",
    "profile":{"name":"Trader","email":"","phone":""},"profile_photo":None,"watchlist":[],
    "admin_watchlist":[],"alerts":{},"alert_fired":set(),"db_loaded":False,"is_admin":False,
    "active_news_source":"admin"}.items():
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
.stTextInput input,.stNumberInput input{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:2px !important;font-family:{FONT} !important;font-size:13px !important;}}
.stTextInput input:focus{{border-color:{AMBER} !important;box-shadow:0 0 0 2px rgba(255,159,10,0.15) !important;}}
.stTextInput label,.stTextArea label,.stNumberInput label{{color:{T2} !important;font-size:11px !important;font-weight:600 !important;}}
.stTextArea textarea{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:2px !important;}}
[data-testid="stForm"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;border-radius:2px !important;padding:20px !important;}}
[data-testid="metric-container"]{{background:{DARK2} !important;border:1px solid {BORDER} !important;border-radius:2px !important;padding:12px !important;}}
[data-testid="stMetricLabel"] p{{font-size:10px !important;font-weight:600 !important;color:{T2} !important;letter-spacing:0.5px;}}
[data-testid="stMetricValue"]{{font-family:{MONO} !important;font-size:18px !important;color:{IVORY} !important;}}
.stButton>button{{background:{DARK3} !important;color:{IVORY} !important;border:1px solid {BORDER} !important;border-radius:2px !important;font-family:{FONT} !important;font-weight:600 !important;font-size:13px !important;transition:all .1s ease !important;}}
.stButton>button:hover{{border-color:{AMBER} !important;color:{AMBER} !important;transform:none;}}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{{background:{AMBER} !important;color:#000 !important;border:none !important;font-weight:700 !important;}}
.stButton>button[kind="primary"]:hover{{filter:brightness(1.1);color:#000 !important;}}
.stTabs [data-baseweb="tab-list"]{{background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:2px;gap:2px;}}
.stTabs [data-baseweb="tab"]{{color:{T2};font-weight:600;border-radius:1px;font-size:13px;}}
.stTabs [aria-selected="true"]{{background:{DARK3} !important;color:{AMBER} !important;}}
.stCheckbox label,.stRadio label{{color:{IVORY} !important;font-size:13px !important;}}
[data-testid="stSelectbox"]>div>div{{background:{DARK3} !important;border:1px solid {BORDER} !important;color:{IVORY} !important;border-radius:2px !important;}}
hr{{border-color:{BORDER} !important;}}
.stProgress>div>div{{background:{AMBER} !important;}}
.nav-btn .stButton>button{{width:100% !important;text-align:left !important;background:transparent !important;color:{T2} !important;border:none !important;border-radius:0 !important;font-size:13px !important;font-weight:600 !important;padding:7px 12px !important;margin-bottom:0px !important;}}
.nav-btn .stButton>button:hover{{background:{DARK3} !important;color:{IVORY} !important;transform:none;}}
.nav-btn-active .stButton>button{{background:rgba(255,159,10,0.10) !important;color:{AMBER} !important;border-left:2px solid {AMBER} !important;border-radius:0 !important;}}
@keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(48,209,88,.4);}}50%{{box-shadow:0 0 0 5px rgba(48,209,88,0);}}}}
.pulse-dot{{width:6px;height:6px;border-radius:50%;background:{GREEN};display:inline-block;animation:pulse 2s infinite;}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(6px);}}to{{opacity:1;transform:none;}}}}
.fade-up{{animation:fadeUp .3s ease both;}}

/* ── Fixed bottom-left news panel (terminal-style corner dock) ── */
#term-news-dock{{
    position:fixed; left:12px; bottom:12px; width:320px; max-height:260px;
    background:{DARK2}; border:1px solid {BORDER}; border-top:2px solid {AMBER};
    z-index:9999; display:flex; flex-direction:column; box-shadow:0 4px 24px rgba(0,0,0,.6);
}}
#term-news-dock .dock-head{{
    padding:7px 10px; border-bottom:1px solid {BORDER}; display:flex; align-items:center;
    justify-content:space-between; flex-shrink:0;
}}
#term-news-dock .dock-body{{ overflow-y:auto; padding:4px 0; }}
#term-news-dock .dock-body::-webkit-scrollbar{{ width:5px; }}
#term-news-dock .dock-body::-webkit-scrollbar-thumb{{ background:{BORDER}; }}
@media (max-width: 900px){{
    #term-news-dock{{ display:none; }} /* avoid covering nav on narrow/mobile layouts */
}}
</style>
""", unsafe_allow_html=True)

# ── Helpers ──────────────────────────────────────────────────
def parse_csv(file):
    """
    Parses an uploaded watchlist file (CSV or newline TXT) into bare
    NSE symbols.

    FIX: previously this kept whatever exchange suffix the export
    already had (e.g. TradingView-style "ARKADE.NS"), then every
    downstream price call appended its OWN ".NS" on top
    (yf.Ticker(sym+".NS")), producing "ARKADE.NS.NS" — not a real
    ticker, so every single row failed silently and the scanner
    returned zero results with no visible error. This strip step
    normalizes to a bare symbol so downstream ".NS"-appending code
    works exactly as it already assumes.
    """
    try: df = pd.read_csv(file, header=None)
    except: return []
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

# ═══════════════════════════════════════════════════════════════════
# INDEX FETCHING — rewritten for three fixes:
#   1. MIDCAP 100 / SMALLCAP 100 were showing "No data". Root cause for
#      SMALLCAP: the ticker "^CNXSMALLCAP" is not valid; the correct
#      Yahoo symbol is "^CNXSC". MIDCAP's own ticker was fine but had
#      no fallback if Yahoo had a data gap on a given day — it now has
#      one (^CRSMID, ^NIFTYMIDCAP100).
#   2. GIFT NIFTY showed "$nan" / "nan%". GIFT Nifty is a futures
#      contract on NSE IX (GIFT City) that requires a paid exchange
#      data subscription — there is no free public ticker for it on
#      Yahoo Finance or elsewhere (confirmed: multiple financial sites
#      that display "GIFT Nifty" are substituting the regular NIFTY 50
#      spot index with a disclaimer, not showing a real live GIFT
#      Nifty futures feed). Rather than guess a third wrong ticker,
#      this card is REMOVED entirely — showing nothing is more honest
#      than showing a number that isn't actually GIFT Nifty.
#   3. S&P 500 / DOW JONES also showed "$nan" — NOT a missing-data
#      case like MIDCAP/SMALLCAP (those returned a clean "no data"
#      before). This was a data returned, but containing NaN inside it
#      that slipped past the old check, which only tested
#      `h.empty or len(h) < 2` and never checked whether the actual
#      Close values were valid numbers. `float(nan)` doesn't raise, so
#      it sailed straight through and rendered as "$nan". Every index
#      fetch below now explicitly checks math.isfinite() on every
#      number before returning it as valid — this applies to ALL
#      cards, not just the two that happened to break this time.
# ═══════════════════════════════════════════════════════════════════

def _values_are_sane(cur, pc):
    """
    The core sanity gate. Rejects NaN, inf, zero/negative prices (an
    index price of 0 or below is never real), and a previous-close of
    zero (which would make the % change calculation divide by zero and
    produce inf/nan downstream). Returns True only if BOTH values are
    real, finite, positive numbers.
    """
    try:
        if cur is None or pc is None:
            return False
        cur, pc = float(cur), float(pc)
        if not (math.isfinite(cur) and math.isfinite(pc)):
            return False
        if cur <= 0 or pc <= 0:
            return False
        return True
    except (TypeError, ValueError):
        return False

def _fetch_index_history(sym):
    try:
        h = yf.Ticker(sym).history(period="5d", interval="1d")
        if h.empty or len(h) < 2:
            return None
        return h
    except:
        return None

@st.cache_data(ttl=60, show_spinner=False)
def get_index(sym, fallback_syms=None):
    """
    fallback_syms: optional list of additional tickers to try, in
    order, if `sym` fails OR returns data that doesn't pass the sanity
    check. Every candidate is checked with the same rule, so a "looks
    like it worked but the numbers are garbage" result from a fallback
    ticker can't leak through either.
    """
    candidates = [sym] + (fallback_syms or [])
    for candidate in candidates:
        h = _fetch_index_history(candidate)
        if h is None:
            continue
        try:
            cur = float(h["Close"].iloc[-1])
            pc = float(h["Close"].iloc[-2])
        except Exception:
            continue
        if not _values_are_sane(cur, pc):
            continue
        spark_raw = [float(x) for x in h["Close"].tolist()]
        spark = [x for x in spark_raw if math.isfinite(x)]
        if len(spark) < 2:
            continue
        return {
            "price": cur, "chg": ((cur - pc) / pc) * 100, "pts": cur - pc,
            "spark": spark, "ticker_used": candidate,
        }
    return None

# ── Global indexes: candidate ticker lists, first-success-wins ──────
MIDCAP_CANDIDATES = ["NIFTY_MIDCAP_100.NS", "^CRSMID", "^NIFTYMIDCAP100"]
SMALLCAP_CANDIDATES = ["^CNXSC", "^CNXSMALLCAP", "NIFTYSMLCAP100.NS"]
SP500_CANDIDATES    = ["^GSPC"]
DOWJONES_CANDIDATES = ["^DJI"]
GOLD_CANDIDATES     = ["GC=F"]

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
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin:26px 0 12px;">
        <div style="width:3px;height:14px;background:{a};"></div>
        <div style="font-family:{MONO};font-size:12px;font-weight:700;color:{IVORY};letter-spacing:1.5px;text-transform:uppercase;white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div></div>""", unsafe_allow_html=True)

def change_pill(chg):
    # Square tag, not rounded pill — text-forward, minimal chrome.
    c = GREEN if chg >= 0 else RED
    arrow = "▲" if chg >= 0 else "▼"
    return (f'<span style="color:{c};font-family:{MONO};font-size:11px;font-weight:700;'
            f'border:1px solid {c}44;padding:1px 6px;">{arrow} {abs(chg):.2f}%</span>')

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
# LANDING PAGE — green hero theme, seamless on scroll
# (Intentionally UNCHANGED — separate design system from the app
# interior, not part of the terminal reskin.)
# ════════════════════════════════════════════════════════════
if not st.session_state.logged_in:

    L_DARK="#070b0a"; L_DARK2="#0d1512"; L_DARK3="#13201b"; L_BORDER="#1d2f27"
    L_IVORY="#e9f5ef"; L_T2="#8aa79a"
    L_INDIGO="#5ed29c"; L_CYAN="#2dd4bf"; L_GREEN="#34d399"; L_PURPLE="#7dd3c0"; L_PINK="#5eead4"
    L_GRAD_BRAND=f"linear-gradient(135deg,{L_INDIGO},{L_CYAN})"
    L_GRAD_TEXT=f"linear-gradient(90deg,{L_INDIGO},{L_CYAN},{L_PURPLE})"

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
                <div style="width:38px;height:38px;border-radius:10px;background:{L_GRAD_BRAND};display:flex;align-items:center;justify-content:center;">{icon("trend", 19, "#070b0a")}</div>
                <div style="text-align:left;">
                    <div style="font-size:18px;font-weight:800;color:{L_IVORY};letter-spacing:1px;">ARKA TRADES</div>
                    <div style="font-size:9px;letter-spacing:2px;color:{L_T2};text-transform:uppercase;">Market Analytics Platform</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)
        _, login_col, _ = st.columns([1, 1.1, 1])
        with login_col:
            with st.form("lf"):
                st.markdown(f"""<div style="margin-bottom:14px;text-align:center;">
                    <div style="font-size:20px;font-weight:800;color:{L_IVORY};">Member Login</div>
                    <div style="font-size:12px;color:{L_T2};margin-top:4px;">Sign in to access your terminal</div></div>""", unsafe_allow_html=True)
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
    for col, num, label, c in [(s1,"2000+","NSE stocks covered",L_CYAN),(s2,"<90s","Scan time after pre-filter",L_INDIGO),
        (s3,"10s","Live price refresh",L_GREEN),(s4,"24/7","AI memory of your setups",L_PURPLE)]:
        with col:
            st.markdown(f"""<div class="fade-up" style="background:{L_DARK2};border:1px solid {L_BORDER};border-top:2px solid {c};border-radius:12px;padding:20px;text-align:center;">
                <div style="font-family:{MONO};font-size:26px;font-weight:700;color:{c};margin-bottom:4px;">{num}</div>
                <div style="font-size:12px;color:{L_T2};font-weight:600;">{label}</div></div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    fa1, fa2 = st.columns([1, 1])
    with fa1:
        st.markdown(f"""<div class="fade-up" style="padding:24px 8px;">{icon_box("brain", L_PURPLE, 38)}
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{L_PURPLE};text-transform:uppercase;margin-bottom:10px;">AI Chart Analysis</div>
            <div style="font-size:28px;font-weight:800;color:{L_IVORY};letter-spacing:-0.5px;line-height:1.25;margin-bottom:16px;">Teach the AI your setups.<br>It never forgets.</div>
            {checkline("Save your personal trading rules, entry conditions and reference charts once")}
            {checkline("Gemini-powered vision analyzes any chart against <strong>your</strong> rules")}
            {checkline("Get a verdict, score and rule-by-rule breakdown in seconds")}
            {checkline("Vector memory stores every setup permanently")}</div>""", unsafe_allow_html=True)
    with fa2:
        st.markdown(f"""<div class="fade-up" style="background:{L_DARK2};border:1px solid {L_BORDER};border-top:2px solid {L_PURPLE};border-radius:16px;padding:24px;margin-top:24px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                <span style="font-size:13px;font-weight:700;color:{L_IVORY};">RELIANCE · Daily</span>
                <span style="background:rgba(52,211,153,.12);color:{L_GREEN};font-size:11px;font-weight:700;padding:4px 12px;border-radius:20px;border:1px solid {L_GREEN}33;">VALID · 8/10</span></div>
            <div style="background:{L_DARK3};border-radius:10px;padding:16px;font-family:{MONO};font-size:12px;color:{L_T2};line-height:2;">
                <span style="color:{L_GREEN};">+ Rule matched:</span> Close above PDH on breakout candle<br>
                <span style="color:{L_GREEN};">+ Rule matched:</span> Volume 1.8x vs 20-day average<br>
                <span style="color:{L_GREEN};">+ Rule matched:</span> RSI 61 — within momentum zone<br>
                <span style="color:{RED};">- Flagged:</span> Overhead supply at 2,980 level</div>
            <div style="font-size:12px;color:{L_T2};margin-top:12px;line-height:1.7;">"Structure is clean. Entry valid above 2,941 with stop at 2,896."</div></div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:48px;'></div>", unsafe_allow_html=True)
    fb1, fb2 = st.columns([1, 1])
    with fb1:
        st.markdown(f"""<div class="fade-up" style="background:{L_DARK2};border:1px solid {L_BORDER};border-top:2px solid {L_GREEN};border-radius:16px;padding:24px;margin-top:24px;">
            <div style="font-size:13px;font-weight:700;color:{L_IVORY};margin-bottom:14px;">Scan: "Bull Flag + Volume Surge" · Full NSE</div>
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
                <tr style="color:{L_T2};text-align:left;"><th style="padding:6px 8px;">Symbol</th><th style="padding:6px 8px;">Price</th><th style="padding:6px 8px;">Signal</th><th style="padding:6px 8px;">Score</th></tr>
                <tr><td style="padding:8px;color:{L_IVORY};font-weight:700;border-top:1px solid {L_BORDER};">TATAMOTORS</td><td style="padding:8px;font-family:{MONO};color:{L_IVORY};border-top:1px solid {L_BORDER};">1,024.50</td><td style="padding:8px;border-top:1px solid {L_BORDER};"><span style="color:{L_GREEN};font-weight:700;">STRONG MATCH</span></td><td style="padding:8px;font-family:{MONO};color:{L_GREEN};border-top:1px solid {L_BORDER};">9/10</td></tr>
                <tr><td style="padding:8px;color:{L_IVORY};font-weight:700;border-top:1px solid {L_BORDER};">CHOLAFIN</td><td style="padding:8px;font-family:{MONO};color:{L_IVORY};border-top:1px solid {L_BORDER};">1,388.20</td><td style="padding:8px;border-top:1px solid {L_BORDER};"><span style="color:{L_GREEN};font-weight:700;">STRONG MATCH</span></td><td style="padding:8px;font-family:{MONO};color:{L_GREEN};border-top:1px solid {L_BORDER};">8/10</td></tr>
                <tr><td style="padding:8px;color:{L_IVORY};font-weight:700;border-top:1px solid {L_BORDER};">PERSISTENT</td><td style="padding:8px;font-family:{MONO};color:{L_IVORY};border-top:1px solid {L_BORDER};">4,832.00</td><td style="padding:8px;border-top:1px solid {L_BORDER};"><span style="color:{L_CYAN};font-weight:700;">PARTIAL</span></td><td style="padding:8px;font-family:{MONO};color:{L_CYAN};border-top:1px solid {L_BORDER};">7/10</td></tr>
            </table></div>""", unsafe_allow_html=True)
    with fb2:
        st.markdown(f"""<div class="fade-up" style="padding:24px 8px;">{icon_box("search", L_GREEN, 38)}
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{L_GREEN};text-transform:uppercase;margin-bottom:10px;">AI Smart Scanner</div>
            <div style="font-size:28px;font-weight:800;color:{L_IVORY};letter-spacing:-0.5px;line-height:1.25;margin-bottom:16px;">Your setups, scanned across<br>the entire market.</div>
            {checkline("Describe your setup in plain English — AI extracts the rules")}
            {checkline("Price pre-filter across all ~2000 NSE stocks, then deep scan")}
            {checkline("Gemini Vision compares charts against your reference image")}
            {checkline("Ranked similarity verdicts with entry and risk notes")}</div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{L_CYAN};text-transform:uppercase;margin-bottom:8px;">Built for your style</div>
        <div style="font-size:28px;font-weight:800;color:{L_IVORY};">Momentum. Swing. Positional.</div></div>""", unsafe_allow_html=True)
    t1, t2, t3 = st.columns(3)
    for col, ic, ic_c, title, items in [
        (t1,"zap",AMBER,"Momentum Traders",["PDH / PDL breakout detection in real time","10-second live price refresh","Volume spike flags vs 20-day average","Instant Telegram push when levels break"]),
        (t2,"trend",L_CYAN,"Swing Traders",["Multi-day setup scanning: flags, bases, ranges","RSI and ROC filters across your watchlist","AI pattern matching vs saved reference charts","Daily structure analysis with SMA 20/50"]),
        (t3,"layers",L_PURPLE,"Positional Traders",["Curated Arka Watchlist maintained by the desk","Today-only news feed per stock","Live index dashboard for market breadth","Cloud-synced watchlists on any device"])]:
        with col:
            checks = "".join(checkline(i, ic_c) for i in items)
            st.markdown(f"""<div class="fade-up" style="background:{L_DARK2};border:1px solid {L_BORDER};border-top:2px solid {ic_c};border-radius:14px;padding:26px;min-height:300px;">
                {icon_box(ic, ic_c, 38)}<div style="font-size:16px;font-weight:800;color:{L_IVORY};margin-bottom:16px;">{title}</div>{checks}</div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:56px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{L_CYAN};text-transform:uppercase;margin-bottom:8px;">Onboarding roadmap</div>
        <div style="font-size:28px;font-weight:800;color:{L_IVORY};">Live in two weeks.</div></div>""", unsafe_allow_html=True)
    rm1, rm2, rm3 = st.columns(3)
    for col,(day,title,desc,c) in zip([rm1,rm2,rm3],[
        ("DAY 1","Connection & Import","Sign in and upload your TradingView watchlist. Cloud sync is instant.",L_CYAN),
        ("DAY 7","AI Strategy Training","Teach Arka AI your setups, rules and reference charts.",L_PURPLE),
        ("DAY 14","Automated Scans Live","Full-universe scans and Telegram alerts on your exact conditions.",L_GREEN)]):
        with col:
            st.markdown(f"""<div class="fade-up" style="background:{L_DARK2};border:1px solid {L_BORDER};border-top:2px solid {c};border-radius:14px;padding:24px;">
                <div style="font-family:{MONO};font-size:11px;font-weight:700;color:{c};letter-spacing:2px;margin-bottom:10px;">{day}</div>
                <div style="font-size:15px;font-weight:800;color:{L_IVORY};margin-bottom:8px;">{title}</div>
                <div style="font-size:13px;color:{L_T2};line-height:1.7;">{desc}</div></div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="text-align:center;padding:56px 0 40px;">
        <div style="font-size:13px;color:{L_T2};margin-bottom:6px;">Arka Trades · Finance &amp; Market Education</div>
        <div style="font-size:11px;color:{L_T2};opacity:.6;">Not SEBI registered. All content is for educational purposes only.
        Trading involves risk — decisions and outcomes are entirely your own.</div></div>""", unsafe_allow_html=True)
    st.stop()

# ════════════════ DISCLAIMER ═════════════════════════════════
if not st.session_state.disclaimer_done:
    _, col, _ = st.columns([1,3,1])
    with col:
        st.markdown(f"""<div style="padding:48px 0 20px;text-align:center;">
            <div style="font-size:30px;font-weight:800;color:{IVORY};">Disclaimer &amp; Terms</div>
            <div style="font-size:13px;color:{T2};margin-top:6px;margin-bottom:24px;">Read all terms carefully before continuing</div></div>
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:2px;padding:24px;font-size:13px;color:{T2};line-height:2;max-height:260px;overflow-y:auto;margin-bottom:20px;">
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
    "settings":AMBER,"contact":CYAN,"research":AMBER}

with left:
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:16px 12px 12px;border-bottom:1px solid {BORDER};">
        <div style="width:28px;height:28px;border-radius:2px;background:{AMBER};display:flex;align-items:center;justify-content:center;">{icon("trend", 15, "#000")}</div>
        <div><div style="font-size:14px;font-weight:800;color:{IVORY};line-height:1;letter-spacing:0.5px;">ARKA TRADES</div>
        <div style="font-size:8px;letter-spacing:2px;color:{T2};text-transform:uppercase;margin-top:3px;">Analytics Platform</div></div></div>""", unsafe_allow_html=True)

    photo = st.session_state.get("profile_photo")
    if photo:
        st.image(photo, width=60)
    else:
        st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:12px;">
            <div style="width:34px;height:34px;border-radius:2px;background:{DARK3};border:1px solid {BORDER};display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;color:{AMBER};">{initial}</div>
            <div><div style="font-size:10px;color:{T2};">Signed in as</div>
            <div style="font-weight:800;font-size:13px;color:{IVORY};">{name}</div></div></div>
        <div style="height:1px;background:{BORDER};"></div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='padding:12px 12px 3px;font-size:9px;font-weight:700;letter-spacing:1.5px;color:{AMBER};text-transform:uppercase;'>Product Suite</div>", unsafe_allow_html=True)
    pg = st.session_state.page

    def nav_btn(label, key):
        active = pg == key
        css_class = "nav-btn-active" if active else "nav-btn"
        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
        if st.button(label, key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # NOTE: "News Terminal" nav item REMOVED — news now lives in the
    # fixed bottom-left dock (rendered once, globally, further below)
    # instead of being its own page. "Research" is new.
    nav_btn("Dashboard","home"); nav_btn("Scanner","scanner"); nav_btn("Alerts","alerts")
    nav_btn("Research","research"); nav_btn("Arka AI","analysis")
    nav_btn("Smart Screener","smart_scan"); nav_btn("Market Breadth","breadth")
    st.markdown(f"<div style='padding:12px 12px 3px;font-size:9px;font-weight:700;letter-spacing:1.5px;color:{T2};text-transform:uppercase;'>Coming Soon</div>", unsafe_allow_html=True)
    nav_btn("Heatmap","heatmap"); nav_btn("Auto Alerts","autoalert")
    st.markdown(f"<div style='padding:12px 12px 3px;font-size:9px;font-weight:700;letter-spacing:1.5px;color:{T2};text-transform:uppercase;'>Account</div>", unsafe_allow_html=True)
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
    accent = PAGE_ACCENTS.get(pg, AMBER)
    page_titles = {"home":"Dashboard","scanner":"Watchlist Scanner","alerts":"Alerts Manager",
        "research":"Research Terminal","analysis":"Arka AI","smart_scan":"Smart Screener",
        "breadth":"Market Breadth","heatmap":"Heatmap","autoalert":"Auto Alerts",
        "profile":"Profile","settings":"Settings","contact":"Contact"}

    n1, n2 = st.columns([5,1])
    with n1:
        st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;padding:14px 0 8px;">
            <div style="width:4px;height:28px;background:{accent};"></div>
            <div><div style="font-size:18px;font-weight:800;color:{IVORY};">{page_titles.get(pg,"Dashboard")}</div>
            <div style="font-size:11px;color:{T2};margin-top:2px;">Arka Trades · Market Analytics Platform</div></div></div>""", unsafe_allow_html=True)
    with n2:
        st.markdown(f"""<div style="display:flex;align-items:center;justify-content:flex-end;height:56px;padding-right:8px;">
            <div style="display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:10px;letter-spacing:1px;color:{GREEN};border:1px solid {GREEN}44;padding:4px 10px;"><span class="pulse-dot"></span>LIVE</div></div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='height:1px;background:{BORDER};margin-bottom:12px;'></div>", unsafe_allow_html=True)

    def show_idx(col, label, sym, c, fallback_syms=None, currency=""):
        d = get_index(sym, fallback_syms)
        with col:
            if d:
                cc = GREEN if d["chg"]>=0 else RED
                pts_sign = "+" if d["pts"] >= 0 else ""
                spark = sparkline(d.get("spark", []), color=cc, w=120, h=26)
                st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};padding:12px;margin:3px 1px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;">
                    <span style="font-size:10px;font-weight:700;color:{T2};letter-spacing:0.5px;">{label}</span>{change_pill(d['chg'])}</div>
                    <div style="font-family:{MONO};font-weight:700;font-size:18px;color:{IVORY};line-height:1;margin-bottom:4px;">{currency}{d['price']:,.2f}</div>
                    <div style="font-family:{MONO};font-size:11px;font-weight:600;color:{cc};margin-bottom:5px;">{pts_sign}{d['pts']:,.2f} pts</div>{spark}</div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};padding:12px;margin:3px 1px;opacity:0.5;">
                    <div style="font-size:10px;font-weight:700;color:{T2};margin-bottom:7px;">{label}</div>
                    <div style="font-family:{MONO};font-size:18px;color:{T2};">--</div>
                    <div style="font-size:10px;color:{T2};margin-top:4px;">No data</div></div>""", unsafe_allow_html=True)

    if pg == "home":
        r1a,r1b,r1c = st.columns(3)
        show_idx(r1a,"NIFTY 50","^NSEI",AMBER)
        show_idx(r1b,"BANK NIFTY","^NSEBANK",CYAN)
        show_idx(r1c,"SENSEX","^BSESN",AMBER)
        r2a,r2b = st.columns(2)
        show_idx(r2a,"MIDCAP 100", MIDCAP_CANDIDATES[0], PURPLE, fallback_syms=MIDCAP_CANDIDATES[1:])
        show_idx(r2b,"SMALLCAP 100", SMALLCAP_CANDIDATES[0], PINK, fallback_syms=SMALLCAP_CANDIDATES[1:])
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:10px 0 14px;'></div>", unsafe_allow_html=True)

        st.markdown(f"""<div style="font-size:10px;font-weight:700;letter-spacing:1.5px;color:{T2};
            text-transform:uppercase;margin-bottom:7px;">Global Markets</div>""", unsafe_allow_html=True)
        gi1, gi2, gi3 = st.columns(3)
        show_idx(gi1,"S&P 500", SP500_CANDIDATES[0], CYAN, fallback_syms=SP500_CANDIDATES[1:], currency="$")
        show_idx(gi2,"DOW JONES", DOWJONES_CANDIDATES[0], AMBER, fallback_syms=DOWJONES_CANDIDATES[1:], currency="$")
        show_idx(gi3,"GOLD (USD)", GOLD_CANDIDATES[0], "#FFD700", fallback_syms=GOLD_CANDIDATES[1:], currency="$")

        # ── Stock Search (new — quick jump into Research Terminal) ──
        st.markdown(f"<div style='height:1px;background:{BORDER};margin:14px 0 14px;'></div>", unsafe_allow_html=True)
        st.markdown(f"""<div style="font-size:10px;font-weight:700;letter-spacing:1.5px;color:{T2};
            text-transform:uppercase;margin-bottom:7px;">Stock Research</div>""", unsafe_allow_html=True)
        hs1, hs2 = st.columns([4,1])
        with hs1:
            home_query = st.text_input("Search", placeholder="Search any NSE stock — news, results, shareholding, sector...",
                                        label_visibility="collapsed", key="home_search_box")
        with hs2:
            home_go = st.button("GO", use_container_width=True, type="primary", key="home_search_go")
        if home_go and home_query.strip():
            st.session_state["research_last_query"] = home_query.strip()
            st.session_state.pop("research_data", None)
            st.session_state.page = "research"
            st.rerun()

        mmi = get_mmi()
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

        if mmi["status"] == "live":
            zc = mmi_zone_color(mmi["zone"])
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};
                border-top:2px solid {zc};padding:14px 16px;margin:3px 1px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-size:10px;font-weight:700;color:{T2};text-transform:uppercase;margin-bottom:5px;letter-spacing:0.5px;">
                            Market Mood Index (MMI)</div>
                        <div style="display:flex;align-items:baseline;gap:10px;">
                            <span style="font-family:{MONO};font-weight:700;font-size:22px;color:{IVORY};">{mmi['score']}</span>
                            <span style="color:{zc};font-size:11px;font-weight:700;
                                border:1px solid {zc}55;padding:1px 8px;">{mmi['zone']}</span>
                        </div>
                    </div>
                    <div style="text-align:right;font-size:10px;color:{T2};">Updated {mmi['fetched_at_ist'].strftime('%d %b %Y, %I:%M%p')}<br>
                        <span style="opacity:.7;">Source: Tickertape</span></div>
                </div></div>""", unsafe_allow_html=True)

        elif mmi["status"] == "stale":
            zc = mmi_zone_color(mmi["zone"])
            age = mmi["age"]
            hrs = int(age.total_seconds() // 3600)
            age_label = f"{hrs}h ago" if hrs < 48 else f"{hrs // 24}d ago"
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {AMBER}55;
                border-top:2px solid {AMBER};padding:14px 16px;margin:3px 1px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
                            <span style="font-size:10px;font-weight:700;color:{T2};text-transform:uppercase;letter-spacing:0.5px;">
                                Market Mood Index (MMI)</span>
                            <span style="color:{AMBER};font-size:9px;font-weight:700;
                                border:1px solid {AMBER}55;padding:1px 6px;">⚠ STALE DATA</span>
                        </div>
                        <div style="display:flex;align-items:baseline;gap:10px;">
                            <span style="font-family:{MONO};font-weight:700;font-size:22px;color:{IVORY};opacity:.75;">{mmi['score']}</span>
                            <span style="color:{zc};font-size:11px;font-weight:700;
                                border:1px solid {zc}55;padding:1px 8px;opacity:.85;">{mmi['zone']}</span>
                        </div>
                    </div>
                    <div style="text-align:right;font-size:10px;color:{AMBER};">Live fetch failed<br>
                        <span style="opacity:.8;">Last known value · {age_label}</span></div>
                </div></div>""", unsafe_allow_html=True)

        else:
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {T2};
                padding:14px 16px;margin:3px 1px;opacity:0.6;">
                <div style="font-size:10px;font-weight:700;color:{T2};text-transform:uppercase;margin-bottom:5px;letter-spacing:0.5px;">
                    Market Mood Index (MMI)</div>
                <div style="font-size:11px;color:{T2};">Unavailable — Tickertape's page couldn't be read and no
                    cached value exists yet. This will populate automatically once a scan succeeds.</div></div>""", unsafe_allow_html=True)

        st.markdown(f"<div style='height:1px;background:{BORDER};margin:14px 0 14px;'></div>", unsafe_allow_html=True)

    st.markdown('<div style="padding:0 8px 80px;">', unsafe_allow_html=True)

    if pg == "home":
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        mkt = now.replace(hour=9,minute=15,second=0,microsecond=0) <= now <= now.replace(hour=15,minute=30,second=0,microsecond=0)
        mkt_color = GREEN if mkt else RED
        mkt_label = "MARKET OPEN" if mkt else "MARKET CLOSED"
        g1,g2,g3 = st.columns([1.2, 1, 1])
        with g1:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};padding:20px;min-height:120px;">
                <div style="display:inline-flex;align-items:center;gap:7px;background:{mkt_color}14;border:1px solid {mkt_color}33;padding:4px 12px;margin-bottom:12px;">
                <span style="width:6px;height:6px;border-radius:50%;background:{mkt_color};display:inline-block;"></span>
                <span style="font-size:10px;font-weight:700;letter-spacing:0.5px;color:{mkt_color};">{mkt_label}</span></div>
                <div style="font-size:12px;color:{T2};">NSE trading hours · 09:15 to 15:30 IST</div>
                <div style="font-family:{MONO};font-size:12px;color:{IVORY};margin-top:5px;">{now.strftime("%d %b %Y · %H:%M:%S IST")}</div></div>""", unsafe_allow_html=True)
        with g2:
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};padding:20px;min-height:120px;">
                {icon_box("layers", CYAN, 30)}<div style="font-family:{MONO};font-size:20px;font-weight:700;color:{IVORY};">{len(st.session_state.watchlist)}</div>
                <div style="font-size:11px;color:{T2};">Stocks in your watchlist</div></div>""", unsafe_allow_html=True)
        with g3:
            active_alerts = sum(1 for a in st.session_state.alerts.values() if a.get("active"))
            st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};padding:20px;min-height:120px;">
                {icon_box("bell", AMBER, 30)}<div style="font-family:{MONO};font-size:20px;font-weight:700;color:{IVORY};">{active_alerts}</div>
                <div style="font-size:11px;color:{T2};">Active price alerts</div></div>""", unsafe_allow_html=True)

        section("Platform Modules", AMBER)
        w1,w2,w3,w4 = st.columns(4)
        for col,ic,c,title,desc,target in [
            (w1,"brain",PURPLE,"AI Chart Analysis","Arka AI checks any chart against your saved rules and returns a scored verdict.","analysis"),
            (w2,"search",GREEN,"Smart Screener","Scan all NSE stocks with plain-English rules and AI vision matching.","smart_scan"),
            (w3,"trend",PINK,"Market Breadth","See how many NSE stocks are actually confirming the move — not just the index.","breadth"),
            (w4,"bell",AMBER,"Breakout Alerts","PDH, PDL and custom price alerts delivered to Telegram instantly.","alerts")]:
            with col:
                st.markdown(f"""<div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {c};padding:18px;min-height:180px;margin-bottom:6px;">
                    {icon_box(ic, c, 30)}<div style="font-size:12px;font-weight:800;color:{IVORY};margin-bottom:7px;">{title}</div>
                    <div style="font-size:11px;color:{T2};line-height:1.6;">{desc}</div></div>""", unsafe_allow_html=True)
                if st.button("Open module", key=f"go_{target}", use_container_width=True):
                    st.session_state.page = target; st.rerun()

    elif pg == "scanner":
        # NOTE: we intentionally do NOT snapshot admin_watchlist / watchlist
        # into local variables before the uploader blocks run below. That
        # ordering was the root cause of both reported bugs:
        #   - Admin tab: "no Scan option after adding a new watchlist"
        #   - Your tab: "Scan Now appears, but scanning finds no stocks"
        # A Streamlit script reruns top-to-bottom on every interaction
        # (including a file upload). If we read st.session_state into a
        # local variable BEFORE the uploader block, and the uploader block
        # then updates st.session_state a few lines later in that SAME
        # run, the local variable is left pointing at the stale value —
        # the part of the script that decides what to render (the "not
        # watchlist" check and the call into render_scan_results) never
        # sees the fresh upload until a second, unrelated rerun happens.
        # The fix is simple: read st.session_state.<key> fresh, at the
        # exact point of use, AFTER the upload block has had a chance to
        # update it in this same run.
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
                # Mark this watchlist as the active source for the fixed
                # news dock, so the dock reflects whatever was last scanned.
                st.session_state["active_news_source"] = key_prefix
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
                    if s["cls"]=="g":   bd=f"{GREEN}66"; top=GREEN
                    elif s["cls"]=="r": bd=f"{RED}66"; top=RED
                    else:               bd=BORDER; top=BORDER
                    cc = GREEN if s["chg"] >= 0 else RED
                    rc = GREEN if s["rsi"] < 35 else RED if s["rsi"] > 65 else T2
                    ha = s["sym"] in st.session_state.alerts and st.session_state.alerts[s["sym"]].get("active")
                    nd = get_news_dot(s["sym"])
                    dot = f'<span style="color:{AMBER};font-size:9px;margin:0 2px;">&#9679;</span>' if nd else ""
                    bell = icon("bell", 11, AMBER) if ha else ""
                    spark = sparkline(s.get("spark", []), color=cc, w=95, h=24)
                    card = (f'<div style="background:{DARK2};border:1px solid {bd};border-top:2px solid {top};padding:10px 8px 9px;text-align:center;margin-bottom:5px;">'
                        f'<div style="display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;">'
                        f'<span style="font-weight:800;font-size:12px;color:{IVORY};white-space:nowrap;">{s["sym"]}</span>{dot}{bell}</div>'
                        f'<div style="margin-bottom:5px;">{change_pill(s["chg"])}</div>'
                        f'<div style="font-family:{MONO};font-weight:700;font-size:13px;color:{IVORY};line-height:1;margin-bottom:5px;">&#8377;{s["cur"]:.2f}</div>'
                        f'{spark}<div style="font-family:{MONO};font-size:10px;font-weight:700;color:{rc};margin-top:4px;">RSI {s["rsi"]}</div></div>')
                    with cols7[i % 5]:
                        st.markdown(card, unsafe_allow_html=True)
                IST = timezone(timedelta(hours=5, minutes=30))
                st.caption(f"Scanned: {datetime.now(IST).strftime('%d %b %Y  %H:%M:%S')}  ·  % vs prev close  ·  Price: 10s cache")
                if l10: time.sleep(10); st.cache_data.clear(); st.rerun()
                elif l60: time.sleep(60); st.cache_data.clear(); st.rerun()

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        tab1, tab2 = st.tabs(["Arka Watchlist", "Your Watchlist"])
        with tab1:
            # FIX: read admin_watchlist AFTER the uploader has had a chance
            # to run and update session_state in this same rerun — not
            # before it, which is what caused "no Scan option after
            # uploading" on the Admin tab.
            if IS_ADMIN:
                uploaded_admin = st.file_uploader("Upload Arka Watchlist", type=["csv","txt"], key="admin_upload")
                if uploaded_admin:
                    syms = parse_csv(uploaded_admin)
                    if not syms: st.error("No symbols found.")
                    elif db_save_admin_watchlist(syms):
                        st.success(f"Arka Watchlist updated — {len(syms)} stocks.")
                        # db_save_admin_watchlist already set session_state
                        # directly, no rerun needed to see it below.
            admin_syms = st.session_state.admin_watchlist
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:2px solid {CYAN};padding:14px 20px;margin:14px 0;">
                <div style="font-size:13px;font-weight:800;color:{IVORY};margin-bottom:3px;">Arka Watchlist</div>
                <div style="font-size:11px;color:{T2};">{f"{len(admin_syms)} stocks · Curated by the Arka Trades desk" if admin_syms else "No curated watchlist published yet"}</div></div>""", unsafe_allow_html=True)
            if not admin_syms:
                st.info("Arka Watchlist not available yet.")
            else:
                render_scan_results(admin_syms, key_prefix="admin")
        with tab2:
            # FIX: same pattern — the uploader block runs FIRST (updating
            # session_state directly), then we read watchlist fresh from
            # session_state for both the "no watchlist" check and the
            # render_scan_results call. Previously `your_syms` was captured
            # before this block ran, so right after an upload the scanner
            # was being built from the OLD list, and db_save_watchlist's
            # old `db_loaded = False` flag could additionally overwrite the
            # freshly-saved list with a stale Supabase read on the next
            # rerun (that flag has been removed — see db_save_watchlist).
            uploaded_yours = st.file_uploader("Upload Your Watchlist (CSV or TXT)", type=["csv","txt"], key="your_upload")
            if uploaded_yours:
                syms = parse_csv(uploaded_yours)
                if not syms: st.error("No symbols found.")
                elif db_save_watchlist(syms):
                    st.success(f"{len(syms)} stocks loaded and saved.")
                    # db_save_watchlist already set session_state directly.
            your_syms = st.session_state.watchlist
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:2px solid {GREEN};padding:14px 20px;margin:14px 0;">
                <div style="font-size:13px;font-weight:800;color:{IVORY};margin-bottom:3px;">Your Watchlist</div>
                <div style="font-size:11px;color:{T2};">{f"{len(your_syms)} stocks · Synced to cloud" if your_syms else "No watchlist uploaded yet"}</div></div>""", unsafe_allow_html=True)
            if not your_syms:
                st.info("Upload your TradingView watchlist above to start scanning.")
            else:
                render_scan_results(your_syms, key_prefix="yours")

        # NOTE: news_panel(...) calls REMOVED from both tabs above — news
        # is no longer rendered inline per-tab. It now lives once, globally,
        # in the fixed bottom-left dock (see TERMINAL NEWS DOCK section
        # near the end of this file), driven by st.session_state
        # ["active_news_source"], which "Run Scan" above updates.

    elif pg == "alerts":
        active_alerts = {s: a for s, a in st.session_state.alerts.items() if a.get("active")}
        a1, a2, a3 = st.columns(3)
        a1.metric("Active Alerts", len(active_alerts))
        a2.metric("Triggered Today", len(st.session_state.alert_fired))
        a3.metric("Delivery Channel", "Telegram")
        st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:2px solid {AMBER};padding:12px 18px;margin:14px 0 8px;">
            <div style="font-size:12px;color:{T2};line-height:1.6;">Create conditional alerts on any stock in your watchlists. When the price crosses your level, a notification is pushed to Telegram instantly.</div></div>""", unsafe_allow_html=True)

        def render_alert_rows(watchlist, key_suffix=""):
            st.markdown(f"""<div style="display:grid;grid-template-columns:2fr 1.2fr 1.5fr 1.2fr;gap:8px;padding:8px 14px;font-size:9px;font-weight:700;letter-spacing:1px;color:{T2};text-transform:uppercase;border-bottom:1px solid {BORDER};">
                <span>Symbol</span><span>Status</span><span>Condition</span><span>Level</span></div>""", unsafe_allow_html=True)
            for sym in watchlist:
                has_alert = sym in st.session_state.alerts and st.session_state.alerts[sym].get("active", False)
                a = st.session_state.alerts.get(sym, {})
                cond  = a.get("type", "").upper() if has_alert else "—"
                level = f"Rs {a['price']:,.2f}" if has_alert else "—"
                if has_alert:
                    status = (f'<span style="display:inline-flex;align-items:center;gap:5px;color:{AMBER};font-size:10px;font-weight:700;border:1px solid {AMBER}44;padding:2px 8px;"><span class="pulse-dot" style="background:{AMBER};"></span>ACTIVE</span>')
                else:
                    status = (f'<span style="color:{T2};font-size:10px;font-weight:700;border:1px solid {BORDER};padding:2px 8px;">INACTIVE</span>')
                rc1, rc2 = st.columns([4, 1.4])
                with rc1:
                    st.markdown(f"""<div style="display:grid;grid-template-columns:2fr 1.2fr 1.5fr 1.2fr;gap:8px;align-items:center;background:{DARK2};border:1px solid {BORDER};padding:10px 14px;margin-bottom:5px;">
                        <span style="font-weight:800;font-size:12px;color:{IVORY};">{sym}</span><span>{status}</span>
                        <span style="font-family:{MONO};font-size:11px;color:{T2};">{cond}</span>
                        <span style="font-family:{MONO};font-size:11px;color:{IVORY};">{level}</span></div>""", unsafe_allow_html=True)
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
                    st.markdown(f"""<div style="background:{DARK3};border:1px solid {BORDER};padding:4px 14px;margin-bottom:8px;">
                        <div style="font-size:11px;font-weight:700;color:{AMBER};padding:8px 0 0;">Configure alert · {sym}</div></div>""", unsafe_allow_html=True)
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

    elif pg == "research":
        # New: Bloomberg-style single-security research page (news,
        # quarterly/yearly results, shareholding, sector classification
        # from Screener.in; peer comparison / sector P/E clearly marked
        # as unavailable — see research_page.py / screener_scraper.py
        # docstrings for why).
        render_research_page(TERM_TOKENS, news_fetch_fn=_fetch_news_for_stock)

    elif pg in ["analysis","heatmap","autoalert"]:
        if pg == "analysis":
            render_arka_ai()
        else:
            labels = {"heatmap":"Market Heatmap","autoalert":"Auto Smart Alerts"}
            st.markdown(f"""<div style="background:{DARK2};border:1px dashed {BORDER};padding:80px 20px;text-align:center;margin:20px 0;">
                <div style="margin-bottom:14px;">{icon("clock", 28, T2)}</div>
                <div style="font-size:22px;font-weight:800;color:{T2};margin-bottom:8px;">{labels.get(pg,'Coming Soon')}</div>
                <div style="font-size:13px;color:{T2};opacity:.6;">This module is under development</div></div>""", unsafe_allow_html=True)

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
                st.image(photo,width=110); st.caption(name)
            else:
                st.markdown(f"""<div style="width:88px;height:88px;border-radius:2px;background:{DARK3};border:1px solid {BORDER};display:flex;align-items:center;justify-content:center;font-weight:800;font-size:32px;color:{AMBER};margin-bottom:12px;">{initial}</div>
                <div style="font-size:18px;font-weight:800;color:{IVORY};">{name}</div>
                <div style="font-size:10px;color:{T2};letter-spacing:1px;text-transform:uppercase;margin-top:4px;">Arka Trades Member</div>""", unsafe_allow_html=True)
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
        st.markdown(f"<div style='font-size:14px;font-weight:800;color:{IVORY};margin:8px 0 10px;'>Appearance</div>", unsafe_allow_html=True)
        t1,t2=st.columns(2)
        with t1:
            st.markdown(f"""<div style="background:{DARK2};border:2px solid {AMBER};padding:18px;text-align:center;">
                <div style="margin-bottom:8px;">{icon("shield", 22, AMBER)}</div>
                <div style="font-weight:800;font-size:13px;color:{AMBER};">DARK MODE</div>
                <div style="font-size:11px;color:{T2};margin-top:4px;">Currently active</div></div>""", unsafe_allow_html=True)
        with t2:
            st.markdown(f"""<div style="background:{DARK3};border:1px solid {BORDER};padding:18px;text-align:center;opacity:.6;">
                <div style="margin-bottom:8px;">{icon("clock", 22, T2)}</div>
                <div style="font-weight:800;font-size:13px;color:{T2};">LIGHT MODE</div>
                <div style="font-size:11px;color:{T2};margin-top:4px;">Coming soon</div></div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:14px;font-weight:800;color:{IVORY};margin-bottom:10px;'>Telegram Notifications</div>", unsafe_allow_html=True)
        st.info(f"Bot connected · Chat ID: {CHAT_ID}")
        if st.button("Send Test Notification",use_container_width=True):
            send_telegram("<b>Arka Trades</b>\nTest notification successful.")
            st.success("Test sent to Telegram.")
        st.divider()
        st.markdown(f"<div style='font-size:14px;font-weight:800;color:{IVORY};'>Broker API — Coming Soon</div>", unsafe_allow_html=True)

    elif pg == "contact":
        c1,c2=st.columns([1,1])
        with c1:
            st.markdown(f"""<div style="background:{DARK2};border:1px solid {BORDER};border-left:2px solid {CYAN};padding:24px;">
                <div style="margin-bottom:10px;">{icon("mail", 22, CYAN)}</div>
                <div style="font-weight:800;font-size:12px;letter-spacing:1px;color:{CYAN};text-transform:uppercase;margin-bottom:12px;">Get in Touch</div>
                <div style="font-size:13px;color:{T2};line-height:1.8;margin-bottom:16px;">Questions, feedback or suggestions?<br>We would love to hear from you.</div>
                <div style="font-family:{MONO};font-size:12px;color:{CYAN};font-weight:700;word-break:break-all;">Mohitdevsinghchib644@gmail.com</div>
                <div style="font-size:11px;color:{T2};margin-top:10px;">Mention ARKA TRADES in subject line.<br>Reply within 24 hours.</div></div>""", unsafe_allow_html=True)
        with c2:
            with st.form("cf"):
                n=st.text_input("Your Name")
                e=st.text_input("Your Email")
                m=st.text_area("Message",height=120)
                if st.form_submit_button("Send Message",use_container_width=True,type="primary"):
                    if n and m: st.success("Please email: Mohitdevsinghchib644@gmail.com")
                    else: st.warning("Fill name and message.")

    st.markdown('</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# TERMINAL NEWS DOCK — fixed bottom-left panel, rendered once globally
# ═══════════════════════════════════════════════════════════════════
# Replaces the old per-tab news_panel() calls and the standalone "News
# Terminal" page. Always visible (desktop widths — see the CSS media
# query above that hides it under 900px so it doesn't cover the nav on
# narrow layouts). Driven by st.session_state["active_news_source"],
# which "Run Scan" in the Scanner page sets to "admin" or "yours" —
# defaults to "admin" so the dock has content even before any scan.
#
# NOTE ON THE @st.fragment APPROACH: news_feed.py's news_panel() is a
# full tabbed UI built for a wide inline area (up to 15 st.tabs, each a
# full column-based article layout) — it isn't shaped for a 320px-wide
# fixed box. Rather than force that tabbed layout into a cramped corner
# (which the earlier conversation flagged as a real fit problem), this
# renders a flat reverse-chronological list instead, reusing the same
# underlying fetch/cache functions from news_feed.py (_ensure_news_state,
# refresh_news, the _news_cache session-state dict) so there is exactly
# ONE fetch/cache layer for news in the whole app — this block only
# changes how that cached data is DISPLAYED, not how it's fetched.
@st.fragment(run_every=10)
def _render_news_dock():
    _ensure_news_state()
    source_key = st.session_state.get("active_news_source", "admin")
    watchlist = st.session_state.get(
        "admin_watchlist" if source_key == "admin" else "watchlist", []
    )
    # Fall back to admin list if the "active" one is empty (e.g. user
    # hasn't uploaded their own watchlist yet).
    if not watchlist:
        watchlist = st.session_state.get("admin_watchlist", [])

    if not watchlist:
        st.markdown(f"""
        <div id="term-news-dock">
            <div class="dock-head">
                <span style="font-family:{MONO};font-size:10px;font-weight:700;color:{AMBER};letter-spacing:1px;">NEWS</span>
                <span style="font-size:9px;color:{T3};">no watchlist</span>
            </div>
            <div class="dock-body" style="padding:14px;font-size:11px;color:{T2};">
                Upload a watchlist in Scanner to see news here.
            </div>
        </div>""", unsafe_allow_html=True)
        return

    refresh_news(watchlist)
    cache = st.session_state.get("_news_cache", {})

    # Flatten all articles across the watchlist into one reverse-
    # chronological list — this is the "flat list, not tabs" decision
    # for the cramped fixed-box context.
    flat = []
    for sym in watchlist:
        for art in cache.get(sym, []):
            flat.append({**art, "sym": sym})
    flat.sort(key=lambda a: a.get("pub_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    label = "ARKA WATCHLIST" if source_key == "admin" else "YOUR WATCHLIST"
    if not flat:
        st.markdown(f"""
        <div id="term-news-dock">
            <div class="dock-head">
                <span style="font-family:{MONO};font-size:10px;font-weight:700;color:{AMBER};letter-spacing:1px;">NEWS · {label}</span>
                <span class="pulse-dot"></span>
            </div>
            <div class="dock-body" style="padding:14px;font-size:11px;color:{T2};">
                No news today across {len(watchlist)} stocks.
            </div>
        </div>""", unsafe_allow_html=True)
        return

    rows = ""
    for art in flat[:20]:
        rows += (
            f'<a href="{art["link"]}" target="_blank" style="display:block;padding:6px 10px;'
            f'border-bottom:1px solid {BORDER};text-decoration:none;">'
            f'<div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline;">'
            f'<span style="font-family:{MONO};font-size:9px;font-weight:700;color:{AMBER};white-space:nowrap;">{art["sym"]}</span>'
            f'<span style="font-family:{MONO};font-size:9px;color:{T3};white-space:nowrap;">{art["time_str"]}</span></div>'
            f'<div style="font-size:11px;color:{IVORY};line-height:1.4;margin-top:2px;">{art["title"]}</div>'
            f'</a>'
        )

    st.markdown(f"""
    <div id="term-news-dock">
        <div class="dock-head">
            <span style="font-family:{MONO};font-size:10px;font-weight:700;color:{AMBER};letter-spacing:1px;">NEWS · {label}</span>
            <span class="pulse-dot"></span>
        </div>
        <div class="dock-body">{rows}</div>
    </div>""", unsafe_allow_html=True)

_render_news_dock()
