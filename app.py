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
import user_data as ud                              # NEW — shared watchlist/portfolio storage helpers
from news_feed import render_news_rail, get_news_dot, _ensure_news_state, refresh_news, _fetch_news_for_stock
from arka_ai import render_arka_ai
from research_page import render_research_page, render_research_panel
from screener_scraper import resolve_symbol
from data_engine import (get_indices, get_quotes, scan_batch, get_static, get_history,
                         warm_security, to_float)
from chart_engine import render_pro_chart
from money_flow import render_money_flow
# watchlist_page / portfolio_page are imported lazily inside their page branches
# (see the router near the bottom) so a problem in either file can never stop
# the rest of the terminal from loading.

# ═══════════════════════════════════════════════════════════════════
# v5 — WATCHLIST + PORTFOLIO MODULES WIRED IN
#
# What changed vs v4:
#   1. NEW modules: WATCHLIST (watchlist_page.py) and PORTFOLIO
#      (portfolio_page.py). Both appear in the Module Directory, have a
#      compact header with a back button, and are routed at the bottom.
#   2. Both modules share user_data.py, so the watchlist you edit there is the
#      same list the Scanner, Alerts, dashboard and news rail already use.
#   3. "ADD TO WATCHLIST" on a security page now INSERTS one symbol instead of
#      deleting and re-inserting the whole table (the old way could wipe the
#      list if the insert failed halfway).
#   4. News rail: while you are on the Portfolio page it follows your holdings.
#   5. FIX: scanning the Arka list used to set the news source to "admin_v4",
#      which the news rail never matched, so it silently showed the wrong list.
#   6. FIX: st.set_page_config now runs first, and st.secrets is read through a
#      safe helper (no crash when secrets.toml is missing locally).
#   7. Bare `except:` blocks replaced with `except Exception:` so Streamlit's
#      own rerun/stop signals are never swallowed.
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(page_title="Arka Trades", layout="wide", page_icon="📈", initial_sidebar_state="collapsed")


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
        # dict.fromkeys dedupes while preserving order — a stale duplicate
        # row in Supabase (e.g. from a non-atomic save) must never reach
        # the UI, since sym-keyed widgets downstream (alerts page) will
        # crash with StreamlitDuplicateElementKey on a repeated symbol.
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

# ════════════════ DESIGN SYSTEM — TERMINAL v3 ════════════════════
DARK   = "#0B0F14"
DARK2  = "#10161D"
DARK3  = "#151D26"
BORDER = "#293442"
BORDER2 = "#202A35"
IVORY  = "#E6EBF1"
T2     = "#9AA7B5"
T3     = "#667484"
NAVY   = "#0B1118"

AMBER  = "#D9A85B"
CYAN   = "#68A7C8"
GREEN  = "#38B889"
RED    = "#E16B70"
INDIGO = "#7E9BCB"
PURPLE = "#A78AC7"
PINK   = "#D98B9A"

BLUE   = AMBER
GOLD   = AMBER

GRAD_BRAND = f"linear-gradient(135deg,{AMBER},{AMBER})"
GRAD_AI    = f"linear-gradient(135deg,{PURPLE},{CYAN})"
GRAD_TEXT  = f"linear-gradient(90deg,{IVORY},{AMBER})"

FONT = "'Plus Jakarta Sans','Inter',sans-serif"
MONO = "'JetBrains Mono',monospace"

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
    c = color or AMBER
    return (f'<div style="width:{size}px;height:{size}px;border-radius:2px;background:{c}14;'
            f'border:1px solid {c}33;display:flex;align-items:center;justify-content:center;'
            f'margin-bottom:10px;">{icon(name, 16, c)}</div>')

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

# ── Global CSS — ARKA TERMINAL v5 ─────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{{color-scheme:dark;}}
*,*::before,*::after{{box-sizing:border-box;}}
html,body,.stApp{{background:#0B0F14 !important;color:{IVORY} !important;font-family:'Inter',sans-serif !important;}}
.stApp{{background-image:linear-gradient(180deg,rgba(104,167,200,.025),transparent 240px) !important;}}
[data-testid="stVerticalBlock"]{{gap:.55rem;}}
[data-testid="stHorizontalBlock"]{{gap:.55rem;}}
[data-testid="stMainBlockContainer"]{{padding-top:.35rem !important;}}
::selection{{background:{AMBER}55;color:#fff;}}
header[data-testid="stHeader"]{{display:none !important;}}
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{{display:none !important;}}
 .block-container{{padding:0 14px 28px !important;max-width:1920px !important;margin:0 auto !important;}}
@media(max-width:800px){{.block-container{{padding:0 7px 20px !important;}}}}
.stButton>button{{background:#111821 !important;color:#C7D0DA !important;border:1px solid #2B3745 !important;border-radius:4px !important;font-family:'Inter',sans-serif !important;font-size:10px !important;font-weight:650 !important;min-height:32px !important;box-shadow:none !important;letter-spacing:.2px !important;}}
.stButton>button[kind="secondary"]{{background:#10161D !important;}}
.stButton>button:disabled{{opacity:.42 !important;}}
.stButton>button:hover{{border-color:{AMBER} !important;color:#F0C982 !important;background:#18202A !important;}}
.stButton>button[kind="primary"]{{background:{AMBER} !important;color:#10151B !important;border-color:{AMBER} !important;font-weight:750 !important;}}
.stTextInput input{{background:#10161D !important;color:#E6EBF1 !important;border:1px solid #344151 !important;border-radius:4px !important;font-family:'Inter',sans-serif !important;font-size:13px !important;height:38px !important;}}
.stTextInput input:focus{{border-color:{AMBER} !important;box-shadow:0 0 0 1px {AMBER} !important;}}
.stTextInput label,.stSelectbox label,.stRadio label{{font-size:9px !important;color:#777 !important;text-transform:uppercase !important;letter-spacing:1px !important;}}
[data-testid="stSelectbox"]>div>div{{background:#10161D !important;border:1px solid #344151 !important;border-radius:4px !important;color:#ddd !important;}}
.stTabs [data-baseweb="tab-list"]{{background:#0E141B !important;border:1px solid #293442 !important;border-radius:4px !important;gap:2px !important;padding:3px !important;}}
.stTabs [data-baseweb="tab"]{{font-family:'JetBrains Mono',monospace !important;font-size:10px !important;color:#8D9AA8 !important;text-transform:uppercase !important;border-radius:3px !important;}}
.stTabs [aria-selected="true"]{{color:{AMBER} !important;background:#101010 !important;box-shadow:inset 0 -2px 0 {AMBER};}}
[data-testid="stMetric"]{{background:linear-gradient(145deg,#121922,#0E141B) !important;border:1px solid #293442 !important;border-radius:5px !important;padding:10px 12px !important;}}
[data-testid="stMetricLabel"] p{{font-size:8px !important;color:#777 !important;letter-spacing:1px !important;text-transform:uppercase !important;}}
[data-testid="stMetricValue"]{{font-family:'JetBrains Mono',monospace !important;font-size:16px !important;}}
hr{{border-color:#293442 !important;}}
[data-testid="stDataFrame"]{{border:1px solid #293442;border-radius:4px;overflow:hidden;}}
[data-testid="stAlert"]{{border-radius:4px !important;}}
[data-testid="stExpander"]{{border:1px solid #293442 !important;border-radius:4px !important;background:#0F151C !important;}}
iframe{{border:none;}}
.pulse-dot{{width:5px;height:5px;border-radius:50%;background:{GREEN};display:inline-block;box-shadow:0 0 7px {GREEN};}}
.terminal-shell{{background:#0D131A;border:1px solid #293442;border-top:2px solid {AMBER};border-radius:5px 5px 0 0;overflow:hidden;}}
.terminal-brandbar{{height:45px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;border-bottom:1px solid #293442;background:#0D131A;}}
.brand-left{{display:flex;align-items:center;gap:9px;}}
.brand-mark{{width:25px;height:25px;background:{AMBER};display:flex;align-items:center;justify-content:center;border-radius:3px;}}
.brand-name{{font-size:12px;font-weight:700;letter-spacing:1.3px;color:#eee;}}
.brand-sub{{font-family:'JetBrains Mono',monospace;font-size:7px;letter-spacing:1.5px;color:#666;margin-top:2px;}}
.brand-status{{font-family:'JetBrains Mono',monospace;font-size:8px;letter-spacing:1px;color:{GREEN};display:flex;align-items:center;gap:6px;}}
.brand-status-wide{{justify-content:flex-end;height:34px;}}
.module-top-title{{height:38px;display:flex;align-items:center;justify-content:center;gap:9px;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;letter-spacing:1.2px;color:#E6EBF1;border-bottom:1px solid #293442;background:#0D131A;}}
.module-top-title small{{font-size:7px;color:#555;letter-spacing:1px;font-weight:500;}}
.module-directory-spacer{{height:22px;border-bottom:1px solid #181818;margin-bottom:10px;}}
.module-directory-title{{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:1.4px;color:{AMBER};border-top:1px solid #292929;border-bottom:1px solid #202020;padding:10px 7px;margin-bottom:7px;}}
.module-directory-card{{background:#111821;border:1px solid #293442;border-top:2px solid #354252;padding:11px 12px;min-height:158px;margin-bottom:8px;border-radius:4px;}}
.module-dir-code{{font-family:'JetBrains Mono',monospace;color:{AMBER};font-size:9px;font-weight:700;}}
.module-dir-label{{font-family:'JetBrains Mono',monospace;color:#eee;font-size:11px;font-weight:700;letter-spacing:.5px;margin:4px 0 7px;}}
.module-dir-purpose{{font-size:10px;color:#888;line-height:1.55;min-height:48px;}}
.module-dir-meta{{display:flex;justify-content:space-between;border-top:1px solid #191919;padding-top:7px;margin-top:7px;font-family:'JetBrains Mono',monospace;font-size:8px;color:#555;}}
.module-dir-meta b{{color:{AMBER};font-weight:700;}}
.module-dir-use{{margin-top:6px;font-size:9px;color:#aaa;line-height:1.5;}}
.module-dir-use span{{font-family:'JetBrains Mono',monospace;color:#555;margin-right:7px;font-size:8px;}}

.market-strip{{border-bottom:1px solid #202020;background:#050505;}}
.market-row{{display:grid;grid-template-columns:82px repeat(6,minmax(145px,1fr));min-height:43px;border-bottom:1px solid #161616;}}
.market-row:last-child{{border-bottom:0;}}
.market-row-label{{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;color:#666;display:flex;align-items:center;padding:0 8px;letter-spacing:1px;text-transform:uppercase;}}
.market-cell{{border-left:1px solid #161616;padding:7px 10px;display:flex;align-items:center;justify-content:space-between;gap:6px;min-width:0;}}
.market-cell .m-name{{font-family:'JetBrains Mono',monospace;font-size:10px;color:#8a8a8a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.market-cell .m-price{{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;color:#e6e6e6;white-space:nowrap;}}
.market-cell .m-chg{{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;white-space:nowrap;}}
.command-wrap{{padding:9px 0 7px;background:#050505;border-bottom:1px solid #202020;}}
.command-label{{font-family:'JetBrains Mono',monospace;font-size:8px;color:{AMBER};letter-spacing:1.4px;margin:0 0 4px 4px;text-transform:uppercase;}}
.command-hint{{font-family:'JetBrains Mono',monospace;font-size:8px;color:#555;text-align:right;margin-top:-24px;margin-right:9px;pointer-events:none;}}
.security-head{{display:flex;justify-content:space-between;align-items:center;background:#090909;border:1px solid #262626;border-top:2px solid {AMBER};padding:11px 13px;margin-bottom:8px;}}
.security-kicker{{font-family:'JetBrains Mono',monospace;color:#666;font-size:8px;letter-spacing:1.2px;}}
.security-title{{font-size:17px;font-weight:700;color:#eee;margin-top:3px;}}
.security-symbol{{font-family:'JetBrains Mono',monospace;color:#777;font-size:9px;margin-top:3px;}}
.security-quote{{text-align:right;font-family:'JetBrains Mono',monospace;}}
.quote-price{{font-size:20px;color:#eee;font-weight:600;}}
.panel-title{{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;letter-spacing:1.3px;color:{AMBER};border-bottom:1px solid #242424;padding:8px 0 6px;margin-bottom:6px;text-transform:uppercase;}}
.overview-box{{background:#090909;border:1px solid #242424;}}
.overview-row{{display:flex;justify-content:space-between;padding:6px 9px;border-bottom:1px solid #181818;font-size:9px;}}
.overview-row:last-child{{border-bottom:0;}}
.overview-row span{{color:#777;}}
.overview-row b{{font-family:'JetBrains Mono',monospace;color:#ddd;font-weight:500;}}
.company-desc{{background:#090909;border:1px solid #242424;padding:9px;font-size:9px;color:#858585;line-height:1.6;}}
.module-launcher{{margin-top:9px;border-top:1px solid #242424;background:#070707;}}
.module-launcher-head{{height:28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1d1d1d;padding:0 8px;}}
.module-launcher-title{{font-family:'JetBrains Mono',monospace;font-size:8px;letter-spacing:1.3px;color:#777;text-transform:uppercase;}}
.module-launcher-context{{font-family:'JetBrains Mono',monospace;font-size:8px;color:{AMBER};}}
.module-cell{{padding:8px 9px;border-right:1px solid #222;min-height:48px;}}
.module-code{{font-family:'JetBrains Mono',monospace;color:{AMBER};font-size:8px;font-weight:700;}}
.module-label{{font-size:9px;color:#cfcfcf;margin-top:3px;}}
.monitor-grid{{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:8px;margin-top:8px;}}
.monitor-panel{{background:#10161D;border:1px solid #293442;min-height:0;border-radius:4px;}}
.monitor-head{{height:38px;border-bottom:1px solid #293442;padding:0 10px;display:flex;align-items:center;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:9px;color:{AMBER};letter-spacing:.8px;background:#141C25;}}
.monitor-row{{display:grid;grid-template-columns:minmax(0,1fr) 88px 70px;gap:7px;padding:8px 10px;border-bottom:1px solid #202A35;font-family:'JetBrains Mono',monospace;font-size:10px;min-height:33px;}}
.monitor-row span:first-child{{color:#aaa;}}
.monitor-row span:nth-child(2){{text-align:right;color:#ddd;}}
.monitor-row span:last-child{{text-align:right;}}
.small-positive{{color:{GREEN};}} .small-negative{{color:{RED};}}
.news-rail{{background:#0F151C;border-left:1px solid #293442;min-height:100%;padding-left:8px;}}
.home-monitor-grid{{grid-template-columns:minmax(0,1.1fr) minmax(0,1.1fr) minmax(210px,.8fr);align-items:stretch;}}
.monitor-list{{max-height:410px;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#394554 #10161D;}}
.monitor-list::-webkit-scrollbar{{width:6px;}}.monitor-list::-webkit-scrollbar-thumb{{background:#394554;border-radius:5px;}}
.monitor-list .monitor-row:last-child{{border-bottom:0;}}
.module-nav-wrap{{padding:5px 6px 7px;background:#0A1016;border-bottom:1px solid #293442;}}
.module-nav-wrap [data-testid="stHorizontalBlock"]{{gap:4px !important;}}
.module-nav-wrap .stButton>button{{min-height:30px !important;font-size:9px !important;letter-spacing:.3px !important;}}
.page-kicker{{font:9px 'JetBrains Mono',monospace;color:#8D9AA8;letter-spacing:1.2px;text-transform:uppercase;}}
.monitor-panel{{min-width:0;overflow:hidden;}}
.monitor-row{{grid-template-columns:minmax(0,1fr) minmax(70px,88px) minmax(62px,70px);}}
.stButton>button{{transition:border-color .12s ease,color .12s ease,background .12s ease;}}
.stButton>button:focus-visible{{outline:1px solid {AMBER} !important;outline-offset:1px;}}
@media(max-width:1300px){{.home-monitor-grid{{grid-template-columns:1fr 1fr;}}.home-monitor-grid .monitor-panel:last-child{{grid-column:1/-1;}}}}
@media(max-width:800px){{.home-monitor-grid{{grid-template-columns:1fr;}}.home-monitor-grid .monitor-panel:last-child{{grid-column:auto;}}}}
@media(max-width:1100px){{.market-row{{grid-template-columns:70px repeat(6,minmax(120px,1fr));overflow-x:auto;}}.monitor-grid{{grid-template-columns:1fr;}}}}
</style>
""", unsafe_allow_html=True)

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

# Market data functions (get_static, get_price batch, get_index, history) now
# live in data_engine.py — parallel, batched and cached. Nothing to define here.

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

def section(title, accent=None):
    a = accent or AMBER
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin:26px 0 12px;">
        <div style="width:3px;height:14px;background:{a};"></div>
        <div style="font-family:{MONO};font-size:12px;font-weight:700;color:{IVORY};letter-spacing:1.5px;text-transform:uppercase;white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div></div>""", unsafe_allow_html=True)

def change_pill(chg):
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
    .block-container{{padding-top:0 !important;max-width:1500px !important;padding-left:16px !important;padding-right:16px !important;}}
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
        ("DAY 7","AI Strategy Training","Teach Arka your setups, rules and reference charts.",L_PURPLE),
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

# ════════════════ MAIN APP ═══════════════════════════════════
# Terms are presented on the public landing page; authenticated users go
# directly into the terminal. This avoids a session-reset disclaimer loop.

# ════════════════ MAIN APP — ARKA TERMINAL v5 ════════════════
# The authenticated workspace is a single terminal rather than a
# permanent left-sidebar application. Search is the primary entry point;
# the selected security becomes the context for chart/research/AI tools.
# The right-side MARKET NEWS rail is preserved.


def _fmt_num(value, prefix="", suffix=""):
    if value in (None, "", "—", "-"):
        return "—"
    return f"{prefix}{value}{suffix}"


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


def _market_cell(label, data):
    if data:
        c = GREEN if data["chg"] >= 0 else RED
        arrow = "▲" if data["chg"] >= 0 else "▼"
        return f'<div class="market-cell"><span class="m-name">{label}</span><span class="m-price">{data["price"]:,.2f}</span><span class="m-chg" style="color:{c}">{arrow}{abs(data["chg"]):.2f}%</span></div>'
    return f'<div class="market-cell"><span class="m-name">{label}</span><span class="m-price">—</span><span class="m-chg" style="color:#555">—</span></div>'



MODULE_INFO = [
    # NEW — personal modules first
    ("WATCHLIST", "Add NSE stocks by symbol or company name. Saved automatically, with live price, RSI and PDH/PDL status for each name.", "High", "Your permanent list of stocks to track — open, analyse or remove in one click."),
    ("PORTFOLIO", "Record buy lots, track live P&L, day P&L and allocation, with a yellow news light on holdings that have fresh headlines.", "Core", "Know where you stand and when one of your holdings is in the news."),
    ("WATCHLIST SCANNER", "Scan your saved universe for PDH/PDL breaks, momentum, RSI and volume conditions.", "High", "Fast daily trade discovery from your own symbols."),
    ("ALERTS", "Monitor price, PDH and PDL conditions and deliver configured Telegram notifications.", "High", "Prevents you from having to watch every level manually."),
    ("RESEARCH", "Deep company workspace covering financials, earnings, valuation, ownership, peers, risk, news and technicals.", "Core", "Use before making a research decision; keeps company context in one place."),
    ("ARKA AI", "AI-assisted chart and market analysis that works with the currently selected security context.", "Advanced", "Turns terminal data and your trading rules into an analysis workflow."),
    ("SMART SCREENER", "Build rule-based screens using price, trend, RSI, volume and fundamental conditions.", "High", "Finds candidates across a universe instead of checking stocks one by one."),
    ("MARKET BREADTH", "See advancing/declining participation, breadth ratios and market-level internals.", "Core", "Provides market context before interpreting an individual stock signal."),
    ("MONEY FLOW", "Sector ranking, rotation quadrant, index advance/decline and FII/DII flows across NSE indices.", "Core", "See where money is moving before picking stocks."),
]


def _go_home():
    st.session_state.page = "home"
    st.rerun()


def _render_compact_module_header(title, code=""):
    """Persistent terminal navigation: jump directly between workspaces."""
    st.markdown(
        f'<div class="module-top-title" style="justify-content:space-between;padding:0 10px;">'
        f'<span>{title.upper()} <small>ARKA MARKET TERMINAL</small></span>'
        f'<span style="color:{GREEN};font-size:8px;">● WORKSPACE</span></div>',
        unsafe_allow_html=True,
    )
    nav_items = [
        ("HOME", "home"), ("WATCHLIST", "watchlist"), ("PORTFOLIO", "portfolio"),
        ("SCANNER", "scanner"), ("ALERTS", "alerts"), ("RESEARCH", "research"),
        ("ARKA AI", "analysis"), ("SCREENER", "smart_scan"),
        ("BREADTH", "breadth"), ("MONEY FLOW", "flow"),
    ]
    st.markdown('<div class="module-nav-wrap">', unsafe_allow_html=True)
    for start in range(0, len(nav_items), 5):
        nav_cols = st.columns(5)
        for col, (label, target) in zip(nav_cols, nav_items[start:start + 5]):
            with col:
                active = st.session_state.get("page") == target
                if st.button(label, key=f"global_nav_{target}_{title}",
                             use_container_width=True, type="primary" if active else "secondary"):
                    if target == "home":
                        _go_home()
                    else:
                        st.session_state.page = target
                        st.session_state["show_news_rail"] = False
                        sym = st.session_state.get("active_security")
                        if sym:
                            st.session_state["research_last_query"] = sym
                            st.session_state["m1_ticker"] = sym
                        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def _render_module_directory():
    st.markdown('<div class="module-directory-spacer"></div>', unsafe_allow_html=True)
    st.markdown('<div class="module-directory-title">TERMINAL MODULE DIRECTORY · PURPOSE / IMPORTANCE / USE</div>', unsafe_allow_html=True)
    targets = {
        "WATCHLIST": "watchlist",              # NEW
        "PORTFOLIO": "portfolio",              # NEW
        "WATCHLIST SCANNER": "scanner",
        "ALERTS": "alerts",
        "RESEARCH": "research",
        "ARKA AI": "analysis",
        "SMART SCREENER": "smart_scan",
        "MARKET BREADTH": "breadth",
        "MONEY FLOW": "flow",
    }
    cols = st.columns(3)
    for i, (label, purpose, importance, use) in enumerate(MODULE_INFO):
        target = targets[label]
        with cols[i % 3]:
            if st.button(label, key=f"module_directory_{target}", use_container_width=True):
                st.session_state.page = target
                sym = st.session_state.get("active_security")
                if sym:
                    st.session_state["research_last_query"] = sym
                    st.session_state["m1_ticker"] = sym
                st.rerun()
            st.markdown(
                f"""<div class=\"module-directory-card module-directory-card-body\">
                    <div class=\"module-dir-purpose\">{purpose}</div>
                    <div class=\"module-dir-meta\"><span>IMPORTANCE</span><b>{importance}</b></div>
                    <div class=\"module-dir-use\"><span>USE FOR</span>{use}</div>
                </div>""", unsafe_allow_html=True)


def _render_terminal_brandbar():
    c1, c2 = st.columns([2.2, 7.8])
    with c1:
        if st.button("▲  ARKA TRADES", key="brand_home_terminal", use_container_width=True):
            _go_home()
    with c2:
        st.markdown('<div class="brand-status brand-status-wide"><span class="pulse-dot"></span> MARKET TERMINAL · WORKSPACE NAVIGATION</div>', unsafe_allow_html=True)
    nav_items = [
        ("HOME", "home"), ("WATCHLIST", "watchlist"), ("PORTFOLIO", "portfolio"),
        ("SCANNER", "scanner"), ("ALERTS", "alerts"), ("RESEARCH", "research"),
        ("ARKA AI", "analysis"), ("SCREENER", "smart_scan"),
        ("BREADTH", "breadth"), ("MONEY FLOW", "flow"),
    ]
    st.markdown('<div class="module-nav-wrap">', unsafe_allow_html=True)
    for start in range(0, len(nav_items), 5):
        nav_cols = st.columns(5)
        for col, (label, target) in zip(nav_cols, nav_items[start:start + 5]):
            with col:
                active = st.session_state.get("page") == target
                if st.button(label, key=f"top_nav_{target}", use_container_width=True,
                             type="primary" if active else "secondary"):
                    if target == "home":
                        _go_home()
                    else:
                        st.session_state.page = target
                        st.session_state["show_news_rail"] = False
                        sym = st.session_state.get("active_security")
                        if sym:
                            st.session_state["research_last_query"] = sym
                            st.session_state["m1_ticker"] = sym
                        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


@st.fragment(run_every=60)
def _index_strips():
    """Self-refreshing every 60s. Runs on its own so it never blocks other clicks."""
    data = get_indices(INDIA_ITEMS + GLOBAL_ITEMS)
    for title, items in (("INDIA", INDIA_ITEMS), ("GLOBAL", GLOBAL_ITEMS)):
        cells = ''.join(_market_cell(label, data.get(label)) for label, _ in items)
        st.markdown(f'<div class="market-strip"><div class="market-row"><div class="market-row-label">{title}</div>{cells}</div></div>', unsafe_allow_html=True)


def _render_terminal_header(show_indices=True):
    st.markdown('<div class="terminal-shell">', unsafe_allow_html=True)
    _render_terminal_brandbar()
    if show_indices:
        _index_strips()
    st.markdown('<div class="command-wrap"><div class="command-label">SECURITY / INDEX / COMPANY SEARCH</div>', unsafe_allow_html=True)
    with st.form("security_command_v9", clear_on_submit=False):
        q = st.text_input("Security search", value=st.session_state.get("security_search_v9", ""), placeholder="Type ticker or company name · e.g. RELIANCE", label_visibility="collapsed")
        suggestions = _security_candidates(q)
        sc1, sc2 = st.columns([5.7, 1])
        with sc1:
            labels = [f"{s} · NSE EQUITY" for s in suggestions]
            selected = st.selectbox("Matches", labels, index=0 if labels else None, placeholder="Select a matching security…", label_visibility="collapsed", key="security_dropdown_v9") if labels else None
        with sc2:
            search_clicked = st.form_submit_button("LOAD", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)
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


def _render_module_page_header(title, code=""):
    _render_compact_module_header(title)


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
    chg_c = GREEN if (chg or 0) >= 0 else RED
    price_txt = f"₹{price:,.2f}" if price is not None else "—"
    chg_txt = (f"{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%") if chg is not None else ""
    sector_txt = sec.get('Sector', '') or sec.get('Industry', '') or 'EQUITY'

    st.markdown(f"""
    <div class="security-head">
      <div>
        <div class="security-kicker">SECURITY WORKSPACE · NSE</div>
        <div class="security-title">{name_}</div>
        <div class="security-symbol">{symbol} · {sector_txt}</div>
      </div>
      <div class="security-quote">
        <div class="quote-price">{price_txt}</div>
        <div style="color:{chg_c};">{chg_txt}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    if not B.get("resolved"):
        st.caption("Screener fundamentals could not be loaded right now — price chart and technicals still work. Reopen the security in a minute to retry.")

    chart_col, info_col = st.columns([3.2, 1.55])
    with chart_col:
        st.markdown('<div class="panel-title">PRICE CHART · CANDLES · VOLUME · MA · RSI 14 (+MA 14)</div>', unsafe_allow_html=True)
        render_pro_chart(symbol, height=660)

        cc1, cc2, cc3, cc4 = st.columns(4)
        if hist is not None and len(hist) >= 2:
            h252 = hist.tail(252)
            hi52, lo52 = float(h252["High"].max()), float(h252["Low"].min())
            pdh, pdl = float(hist["High"].iloc[-2]), float(hist["Low"].iloc[-2])
        else:
            hi52 = to_float(s.get("year_high")); lo52 = to_float(s.get("year_low")); pdh = pdl = None
        f = lambda v: f"₹{v:,.2f}" if v is not None else "—"
        with cc1: st.caption(f"52W HIGH\n{f(hi52)}")
        with cc2: st.caption(f"52W LOW\n{f(lo52)}")
        with cc3: st.caption(f"PDH\n{f(pdh)}")
        with cc4: st.caption(f"PDL\n{f(pdl)}")

    with info_col:
        st.markdown('<div class="panel-title">COMPANY OVERVIEW</div>', unsafe_allow_html=True)
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
        rows = "".join(f'<div class="overview-row"><span>{k}</span><b>{v}</b></div>' for k, v in overview_items)
        st.markdown(f'<div class="overview-box">{rows}</div>', unsafe_allow_html=True)
        st.markdown('<div class="panel-title company-desc-title">COMPANY DESCRIPTION</div>', unsafe_allow_html=True)
        about = (B.get("about") or "").strip()
        desc = about[:420] + ("…" if len(about) > 420 else "") if about else (
            f"{name_} is listed on the Indian equity market, classified under {sector_txt}. "
            "Full fundamentals are in the research panel below.")
        st.markdown(f'<div class="company-desc">{desc}</div>', unsafe_allow_html=True)
        if st.button("ADD TO WATCHLIST", use_container_width=True, key=f"sec_wl_{symbol}"):
            # Inserts just this symbol (no delete-everything-then-reinsert).
            added, err = ud.add_symbols(supabase, [symbol], ud.W_TABLE)
            if added:
                st.success(f"{symbol} added to watchlist")
            elif not err:
                st.info(f"{symbol} is already in your watchlist")
            if err:
                (st.warning if added else st.error)(err)

    # Full research panel (lazy, fragment-isolated: switching sections does not rerun the chart)
    st.markdown('<div class="panel-title" style="margin-top:14px">RESEARCH WORKSPACE</div>', unsafe_allow_html=True)
    render_research_panel(symbol, TERM_TOKENS, _fetch_news_for_stock, key="sec")


def _render_dashboard():
    """Home monitor: personal watchlist and portfolio holdings at a glance."""
    wl = list(dict.fromkeys(st.session_state.get("watchlist", []) or []))
    try:
        holdings = list(dict.fromkeys(ud.holding_symbols() or []))
        holdings_error = ""
    except Exception as exc:
        holdings = []
        holdings_error = str(exc)
    admin_wl = list(st.session_state.get("admin_watchlist", []) or [])
    all_syms = list(dict.fromkeys(admin_wl + wl + holdings))
    # Quote retrieval is batched/cached by data_engine; keep the full lists visible.
    quotes = get_quotes(tuple(all_syms)) if all_syms else {}

    def _rows(symbols, empty_message):
        result = []
        for sym in symbols:
            d = quotes.get(sym)
            if not d:
                result.append(
                    f'<div class="monitor-row"><span>{sym}</span><span>—</span>'
                    f'<span style="color:#666">NO DATA</span></div>'
                )
                continue
            c = GREEN if d["chg"] >= 0 else RED
            result.append(
                f'<div class="monitor-row"><span>{sym}</span>'
                f'<span>₹{d["price"]:,.2f}</span>'
                f'<span style="color:{c}">{d["chg"]:+.2f}%</span></div>'
            )
        return "".join(result) or (
            f'<div style="padding:14px 11px;color:#777;font-size:10px;'
            f'font-family:JetBrains Mono,monospace;line-height:1.7;">{empty_message}</div>'
        )

    wl_html = '<div class="monitor-list">' + (_rows(wl, "NO PERSONAL WATCHLIST YET · OPEN WATCHLIST TO ADD SYMBOLS")) + '</div>'
    if holdings_error:
        holdings_empty = "PORTFOLIO STORAGE COULD NOT BE READ · OPEN PORTFOLIO TO CHECK CONNECTION"
    elif not holdings:
        holdings_empty = "NO POSITIONS RETURNED BY PORTFOLIO STORAGE · ADD OR IMPORT A POSITION IN PORTFOLIO"
    else:
        holdings_empty = "NO HOLDINGS YET · ADD A POSITION IN PORTFOLIO"
    holdings_html = '<div class="monitor-list">' + _rows(holdings, holdings_empty) + '</div>'
    adv = sum(1 for s in all_syms if quotes.get(s) and quotes[s]["chg"] > 0.05)
    dec = sum(1 for s in all_syms if quotes.get(s) and quotes[s]["chg"] < -0.05)
    flat = sum(1 for s in all_syms if quotes.get(s) and abs(quotes[s]["chg"]) <= 0.05)
    ratio = (adv / dec) if dec else (float(adv) if adv else 0)

    html = (
        '<div class="monitor-grid home-monitor-grid">'
        '<div class="monitor-panel"><div class="monitor-head"><span>YOUR WATCHLIST</span>'
        f'<span>{len(wl)} SYMBOLS</span></div>{wl_html}</div>'
        '<div class="monitor-panel"><div class="monitor-head"><span>PORTFOLIO HOLDINGS</span>'
        f'<span>{len(holdings)} POSITIONS</span></div>{holdings_html}</div>'
        '<div class="monitor-panel"><div class="monitor-head"><span>MARKET INTERNALS</span>'
        f'<span>{len(all_syms)} SYMBOL SAMPLE</span></div>'
        f'<div class="monitor-row"><span>ADVANCING</span><span>{adv}</span><span class="small-positive">▲</span></div>'
        f'<div class="monitor-row"><span>DECLINING</span><span>{dec}</span><span class="small-negative">▼</span></div>'
        f'<div class="monitor-row"><span>UNCHANGED</span><span>{flat}</span><span style="color:#777">—</span></div>'
        f'<div class="monitor-row"><span>A/D RATIO</span><span>{ratio:.2f}</span><span style="color:#888">RATIO</span></div>'
        '</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)

    st.markdown('<div class="panel-title" style="margin-top:14px;">QUICK WORKSPACE ACCESS</div>', unsafe_allow_html=True)
    quick = [
        ("WATCHLIST", "watchlist"), ("PORTFOLIO", "portfolio"), ("SCANNER", "scanner"),
        ("ALERTS", "alerts"), ("RESEARCH", "research"), ("ARKA AI", "analysis"),
        ("SMART SCREENER", "smart_scan"), ("MARKET BREADTH", "breadth"), ("MONEY FLOW", "flow"),
    ]
    qcols = st.columns(5)
    for i, (label, target) in enumerate(quick):
        with qcols[i % len(qcols)]:
            if st.button(label, key=f"home_quick_{target}", use_container_width=True):
                st.session_state.page = target
                st.session_state["show_news_rail"] = False
                sym = st.session_state.get("active_security")
                if sym:
                    st.session_state["research_last_query"] = sym
                    st.session_state["m1_ticker"] = sym
                st.rerun()

def _news_watchlist_for_rail():
    # NEW: on the Portfolio page the rail follows your holdings.
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

# ── Global terminal state ──────────────────────────────────────
if "active_security" not in st.session_state:
    st.session_state["active_security"] = ""

# The market header belongs to the terminal/home/security workspace only.
# Modules deliberately switch to a compact header so the user can navigate back
# without carrying the index strip through every workspace.
pg = st.session_state.page
if pg in {"home", "security"}:
    _render_terminal_header(show_indices=True)
else:
    module_titles = {"scanner":"WATCHLIST SCANNER","alerts":"ALERTS","research":"RESEARCH","analysis":"ARKA AI","smart_scan":"SMART SCREENER","breadth":"MARKET BREADTH",
                     "flow":"MONEY FLOW",
                     "watchlist":"WATCHLIST","portfolio":"PORTFOLIO"}      # NEW
    if pg in module_titles:
        _render_module_page_header(module_titles[pg])

# ── Workspace layout ────────────────────────────────────────────────
# Home/security retain the contextual news rail. Dedicated modules use the full
# viewport so tables and controls are not squeezed into a narrow center column.
if pg in {"home", "security"}:
    center, right_rail = st.columns([4.55, 1.15], gap="small")
else:
    center = st.container()
    right_rail = None

@st.fragment
def _news_rail_fragment():
    toggle_label = "HIDE NEWS ▸" if st.session_state.show_news_rail else "◂ SHOW NEWS"
    if st.button(toggle_label, key="toggle_news_rail_fast", use_container_width=True):
        st.session_state.show_news_rail = not st.session_state.show_news_rail
        st.rerun(scope="fragment")

    if not st.session_state.show_news_rail:
        st.markdown(f'<div class="news-rail-collapsed">MARKET NEWS <span>HIDDEN</span></div>', unsafe_allow_html=True)
        return

    st.markdown(f"""<div style="position:sticky;top:8px;" class="news-rail" id="arka-news-rail-col">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 4px 8px;border-bottom:1px solid {BORDER};margin-bottom:8px;">
            <span style="font-family:{MONO};font-size:11px;font-weight:800;color:{AMBER};letter-spacing:1.5px;">MARKET NEWS</span><span class="pulse-dot"></span>
        </div>""", unsafe_allow_html=True)
    watchlist_for_news, rail_label = _news_watchlist_for_rail()
    if not watchlist_for_news:
        st.markdown(f'<div style="font-size:11px;color:{T2};padding:8px 4px;">Macro/global news updates below. Add stocks in the Watchlist module for stock-specific news.</div>',unsafe_allow_html=True)
    render_news_rail(watchlist_for_news, label=rail_label)
    st.markdown("</div>", unsafe_allow_html=True)

# Render the workspace first. The news fragment is independent of it.
with center:
    if pg == "home":
        _render_dashboard()
        _render_module_directory()
    elif pg == "security":
        active = st.session_state.get("active_security", "")
        if not active:
            _render_dashboard()
        else:
            _render_security_workspace(active)

    # ───────────── NEW: WATCHLIST ─────────────
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

    # ───────────── NEW: PORTFOLIO ─────────────
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
                # FIX: the news rail only understands "admin" / "yours". The raw key_prefix
                # ("admin_v4" / "yours_v4") never matched, so the rail showed the wrong list.
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
                    bd=f"{GREEN}66" if s["cls"]=="g" else f"{RED}66" if s["cls"]=="r" else BORDER
                    top=GREEN if s["cls"]=="g" else RED if s["cls"]=="r" else BORDER
                    rc=GREEN if s["rsi"]<35 else RED if s["rsi"]>65 else T2
                    with cols7[i%4]:
                        st.markdown(f'<div style="background:{DARK2};border:1px solid {bd};border-top:2px solid {top};padding:10px;text-align:center;margin-bottom:4px;"><b style="color:{IVORY};">{s["sym"]}</b><br>{change_pill(s["chg"])}<div style="font-family:{MONO};color:{IVORY};margin:6px 0;">₹{s["cur"]:.2f}</div><div style="font-family:{MONO};font-size:10px;color:{rc};">RSI {s["rsi"]}</div></div>',unsafe_allow_html=True)
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
            else: st.markdown(f'<div style="width:88px;height:88px;background:{DARK3};border:1px solid {BORDER};display:flex;align-items:center;justify-content:center;font-size:32px;color:{AMBER};">{initial}</div><div style="font-size:18px;font-weight:800;color:{IVORY};margin-top:12px;">{name}</div>',unsafe_allow_html=True)
        with p2:
            with st.form("pf_v4"):
                a,b=st.columns(2); nn=a.text_input("Full Name",value=st.session_state.profile["name"]); np_=b.text_input("Contact Number",value=st.session_state.profile["phone"]); ne=st.text_input("Email Address",value=st.session_state.profile["email"]); ph=st.file_uploader("Upload Profile Photo",type=["jpg","jpeg","png"],key="profile_photo_v4")
                if st.form_submit_button("Save Profile",use_container_width=True,type="primary"):
                    st.session_state.profile.update({"name":nn,"phone":np_,"email":ne});
                    if ph: st.session_state.profile_photo=ph
                    st.rerun()
    elif pg == "settings":
        st.markdown(f'<div class="panel-title">SETTINGS</div>',unsafe_allow_html=True)
        st.info("Terminal appearance is currently locked to the Bloomberg-style dark theme.")
        st.markdown(f'<div class="term-panel"><b style="color:{AMBER};">Telegram</b><br><span style="color:{T2};">Bot connected · Chat ID configured in Streamlit Secrets.</span></div>',unsafe_allow_html=True)
    elif pg == "contact":
        st.markdown(f'<div class="term-panel"><div class="panel-title">CONTACT</div><div style="color:{T2};line-height:1.8;">Questions, feedback or suggestions?<br>Contact the Arka Trades desk through the configured support email.</div></div>',unsafe_allow_html=True)

if right_rail is not None:
    with right_rail:
        _news_rail_fragment()
