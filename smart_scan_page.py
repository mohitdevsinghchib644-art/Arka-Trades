# --------------------------------------------------------------
# smart_scan_page.py  —  Arka Trades Smart Screener  (Sept 2026 rev)
#
# THREE CHANGES THIS REVISION:
#
# 1. BUG FIX — "can't find the right stocks for my setup":
#    _calculate_indicators() was already computing atr_pct (the
#    stock's average daily range as a % of price — i.e. its
#    volatility) and roc_5 for every candidate, but _passes_filters()
#    never checked either one. The setup schema only had price / RSI
#    / volume fields. So a setup like "stocks that move 2-3% up or
#    down" had NO field capable of expressing it — the AI rule parser
#    had nowhere to put that intent, and the scanner was filtering on
#    criteria that had nothing to do with what was actually asked.
#    Fixed by adding two new filter bands, both using the existing
#    "0 = no filter" convention volume_multiplier already used:
#      - atr_pct_min / atr_pct_max   → the stock's TYPICAL daily
#        volatility (a rolling average — use this for "usually moves
#        2-3% a day").
#      - move_pct_min / move_pct_max → TODAY's move, magnitude only
#        (abs(chg_pct), direction-agnostic — use this for "moved
#        2-3% today, up or down").
#    Threaded through: the setup schema, the AI plain-English parser
#    prompt, the manual setup-builder form, the live scan-page
#    overrides, _passes_filters(), and _filters_summary(). Old setups
#    saved before this revision have neither field — .get(...) or 0
#    means they just behave exactly as before (no-op).
#
# 2. Reskinned to the same black/amber terminal look as the rest of
#    the app (was its own blue "ChartX"-style palette before).
#
# 3. NEW — Chartink-style hover charts: the "Rules Shortlist" table
#    used to be a bare st.dataframe with zero visuals. Hovering a
#    symbol now pops up a mini price chart. This uses a cheap
#    pure-SVG line (no matplotlib) built straight from the OHLCV data
#    each candidate already carries in memory (see _calculate_
#    indicators — "df" was already being kept per candidate), so it
#    costs nothing extra to fetch and stays fast even for a couple
#    hundred rows. The AI-vision match cards already show a real
#    rendered candlestick+volume chart per card, which is strictly
#    more detail than a hover preview, so those are untouched.
#
# NOTE ON THE SUPABASE `setups` TABLE: the 4 new filter columns need
# to exist there before they'll persist. Run this once:
#
#   alter table setups
#     add column if not exists atr_pct_min double precision default 0,
#     add column if not exists atr_pct_max double precision default 0,
#     add column if not exists move_pct_min double precision default 0,
#     add column if not exists move_pct_max double precision default 0;
#
# _save_setup() below degrades gracefully if you haven't run that yet
# — it retries without the new fields and tells you why, rather than
# failing the whole save.
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Theme — Bloomberg-style terminal (matches app.py) ────────
DARK    = "#000000"
DARK2   = "#0A0A0A"
DARK3   = "#111111"
BORDER  = "#262626"
IVORY   = "#E8E8E8"
T2      = "#8A8A8A"
GREEN   = "#30D158"
RED     = "#FF453A"
PURPLE  = "#BF5AF2"
AMBER   = "#FF9F0A"
INDIGO  = "#5AC8FA"   # secondary/informational accent (cyan) — "partial
                       # match" and secondary headers, kept visually
                       # distinct from the primary amber brand color
BLUE    = AMBER        # primary brand accent, kept under the old name
                        # so every existing "BLUE" reference in this
                        # file picks up the terminal's amber
FONT    = "'Plus Jakarta Sans','Inter',sans-serif"
MONO    = "'JetBrains Mono',monospace"

GEMINI_TEXT_MODEL     = "gemini-2.5-flash"
GEMINI_VISION_MODEL   = "gemini-2.5-flash"
MIN_SIMILARITY_FLOOR  = 6
BATCH_SIZE            = 150   # symbols per yf.download call


def _section(title, accent=None):
    a = accent or BLUE
    st.markdown(f"""<div style="display:flex;align-items:center;gap:14px;margin:28px 0 14px;">
        <div style="width:4px;height:16px;border-radius:2px;background:{a};"></div>
        <div style="font-family:{FONT};font-size:15px;font-weight:800;color:{IVORY};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div></div>""", unsafe_allow_html=True)


_HOVER_CSS = f"""
<style>
.hc-table-wrap{{ overflow-x:auto; overflow-y:visible; border:1px solid {BORDER}; }}
.hc-table{{ border-collapse:collapse; width:100%; font-family:{MONO}; font-size:12px; }}
.hc-table th{{ text-align:right; padding:8px 10px; font-size:10px; color:{T2}; font-weight:700;
    letter-spacing:0.5px; border-bottom:1px solid {BORDER}; background:{DARK3}; white-space:nowrap; }}
.hc-table th:first-child, .hc-table td:first-child{{ text-align:left; }}
.hc-table td{{ text-align:right; padding:7px 10px; color:{IVORY}; border-bottom:1px solid {BORDER}; white-space:nowrap; }}
.hc-table tr:hover{{ background:{DARK2}; }}
.hc-wrap{{ position:relative; display:inline-block; font-weight:800; color:{AMBER}; cursor:default; }}
.hc-pop{{
    display:none; position:absolute; top:100%; left:0; margin-top:6px; width:240px;
    background:{DARK2}; border:1px solid {BORDER}; border-top:2px solid {AMBER};
    padding:10px; z-index:500; box-shadow:0 8px 24px rgba(0,0,0,.65);
}}
.hc-wrap:hover .hc-pop{{ display:block; }}
.hc-pop-head{{ font-size:10px; font-weight:700; color:{T2}; letter-spacing:0.5px; margin-bottom:6px; }}
.hc-pop-foot{{ display:flex; justify-content:space-between; font-size:11px; font-family:{MONO}; margin-top:6px; color:{IVORY}; }}
</style>
"""


# ════════════════════════════════════════════════════════════
# UNIVERSE — full NSE list, with resilient tiered fallback
# ════════════════════════════════════════════════════════════

NSE_LIQUID_UNIVERSE = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN",
    "BHARTIARTL","ITC","KOTAKBANK","LT","AXISBANK","BAJFINANCE","ASIANPAINT",
    "MARUTI","HCLTECH","SUNPHARMA","TITAN","ULTRACEMCO","WIPRO","NESTLEIND",
    "ADANIENT","ADANIPORTS","POWERGRID","NTPC","M&M","TATAMOTORS","TATASTEEL",
    "JSWSTEEL","BAJAJFINSV","ONGC","COALINDIA","INDUSINDBK","GRASIM","HDFCLIFE",
    "SBILIFE","DRREDDY","CIPLA","EICHERMOT","BRITANNIA","DIVISLAB","APOLLOHOSP",
    "HEROMOTOCO","BPCL","TECHM","UPL","HINDALCO","TATACONSUM","BAJAJ-AUTO",
    "SHREECEM","VEDL","GODREJCP","DABUR","PIDILITIND","SIEMENS","AMBUJACEM",
    "BANKBARODA","CANBK","PNB","IDFCFIRSTB","FEDERALBNK","AUBANK","BANDHANBNK",
    "CHOLAFIN","LICHSGFIN","MUTHOOTFIN","PFC","RECLTD","IRFC","IEX",
    "ZOMATO","NYKAA","PAYTM","POLICYBZR","DMART","TRENT","JUBLFOOD",
    "PERSISTENT","COFORGE","LTIM","MPHASIS","OFSS","LTTS",
    "DLF","GODREJPROP","OBEROIRLTY","PHOENIXLTD",
    "PGHH","COLPAL","MARICO","EMAMILTD","VBL",
    "TVSMOTOR","ASHOKLEY","BALKRISIND","MOTHERSON","BOSCHLTD",
]
NSE_LIQUID_UNIVERSE = list(dict.fromkeys(NSE_LIQUID_UNIVERSE))

NSE_ARCHIVE_URLS = [
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
]


def _nse_session():
    """NSE blocks bare requests with no referer/cookies. Visit the homepage
    first to pick up session cookies, then hit the CSV endpoint with them."""
    s = _requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    try:
        s.get("https://www.nseindia.com", timeout=10)
    except Exception:
        pass
    return s


@st.cache_data(ttl=86400, show_spinner=False)
def get_full_nse_universe():
    """
    Returns (symbols, source_label). Tries, in order:
      1. Live NSE fetch (session + cookies, multiple mirror URLs)
      2. A locally bundled nse_universe.csv, if you add one to the repo
      3. The small hardcoded liquid list (last resort)
    source_label always says exactly which tier was used, so the UI never
    silently shows a shrunken list while implying it's the full universe.
    """
    if HAS_REQUESTS:
        try:
            s = _nse_session()
            for url in NSE_ARCHIVE_URLS:
                try:
                    r = s.get(url, timeout=15)
                    r.raise_for_status()
                    df = pd.read_csv(io.StringIO(r.text))
                    df.columns = [c.strip() for c in df.columns]
                    if "SERIES" in df.columns:
                        df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
                    syms = (df["SYMBOL"].astype(str).str.strip().str.upper()
                            .dropna().unique().tolist())
                    syms = [x for x in syms if x and x.isascii()]
                    if len(syms) > 1000:
                        return syms, f"NSE live · {len(syms)} symbols"
                except Exception:
                    continue
        except Exception:
            pass

    try:
        local = pd.read_csv("nse_universe.csv")
        col = "SYMBOL" if "SYMBOL" in local.columns else local.columns[0]
        syms = local[col].astype(str).str.strip().str.upper().dropna().unique().tolist()
        if len(syms) > 500:
            return syms, f"local nse_universe.csv · {len(syms)} symbols"
    except Exception:
        pass

    return (NSE_LIQUID_UNIVERSE,
            f"liquid fallback · {len(NSE_LIQUID_UNIVERSE)} symbols (live NSE fetch unavailable)")


# ════════════════════════════════════════════════════════════
# SETUPS — persistence in Supabase
# ════════════════════════════════════════════════════════════
# Expects a `setups` table. SQL to create it if it doesn't exist yet:
#
#   create table if not exists setups (
#     id bigint generated always as identity primary key,
#     name text not null,
#     price_min double precision default 0,
#     price_max double precision default 99999,
#     rsi_min double precision default 0,
#     rsi_max double precision default 100,
#     volume_multiplier double precision default 0,
#     atr_pct_min double precision default 0,
#     atr_pct_max double precision default 0,
#     move_pct_min double precision default 0,
#     move_pct_max double precision default 0,
#     visual_rules text,
#     reference_image_b64 text,
#     created_at timestamptz default now()
#   );
#
# Already have the table from before this revision? Just run:
#
#   alter table setups
#     add column if not exists atr_pct_min double precision default 0,
#     add column if not exists atr_pct_max double precision default 0,
#     add column if not exists move_pct_min double precision default 0,
#     add column if not exists move_pct_max double precision default 0;

def _load_setups(supabase):
    try:
        res = supabase.table("setups").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        st.error(f"Could not load setups: {e}")
        return []


def _save_setup(supabase, name, price_min, price_max, rsi_min, rsi_max,
                 volume_multiplier, atr_pct_min, atr_pct_max,
                 move_pct_min, move_pct_max, visual_rules,
                 reference_image_b64=None):
    row = {
        "name": name,
        "price_min": float(price_min),
        "price_max": float(price_max),
        "rsi_min": float(rsi_min),
        "rsi_max": float(rsi_max),
        "volume_multiplier": float(volume_multiplier),
        "atr_pct_min": float(atr_pct_min),
        "atr_pct_max": float(atr_pct_max),
        "move_pct_min": float(move_pct_min),
        "move_pct_max": float(move_pct_max),
        "visual_rules": visual_rules,
    }
    if reference_image_b64:
        row["reference_image_b64"] = reference_image_b64
    try:
        supabase.table("setups").insert(row).execute()
        return True
    except Exception as e:
        # Most likely cause: the live `setups` table predates this
        # revision and doesn't have the 4 new columns yet (see the
        # ALTER TABLE snippet in this module's docstring). Retry once
        # with only the original columns so the setup still saves —
        # just without the new volatility/move filters — instead of
        # failing outright.
        new_fields = {"atr_pct_min", "atr_pct_max", "move_pct_min", "move_pct_max"}
        trimmed = {k: v for k, v in row.items() if k not in new_fields}
        try:
            supabase.table("setups").insert(trimmed).execute()
            st.warning(
                "Saved, but without the ATR%/Move% filters — your Supabase "
                "`setups` table needs 4 new columns first. See the ALTER "
                "TABLE snippet at the top of smart_scan_page.py, then "
                "re-save this setup to include them."
            )
            return True
        except Exception as e2:
            st.error(f"Could not save setup: {e2}")
            return False


def _delete_setup(supabase, setup_id):
    try:
        supabase.table("setups").delete().eq("id", setup_id).execute()
        return True
    except Exception as e:
        st.error(f"Could not delete setup: {e}")
        return False


def _filters_summary(setup):
    pmin = setup.get("price_min") or 0
    pmax = setup.get("price_max") or 0
    rmin = setup.get("rsi_min") or 0
    rmax = setup.get("rsi_max") or 100
    vol = setup.get("volume_multiplier") or 0
    amin = setup.get("atr_pct_min") or 0
    amax = setup.get("atr_pct_max") or 0
    mmin = setup.get("move_pct_min") or 0
    mmax = setup.get("move_pct_max") or 0
    parts = [f"Rs {pmin:,.0f}-{pmax:,.0f}", f"RSI {rmin:.0f}-{rmax:.0f}"]
    if vol > 0:
        parts.append(f"Vol >= {vol:.1f}x")
    if amax > 0:
        parts.append(f"ATR {amin:.1f}-{amax:.1f}%")
    if mmax > 0:
        parts.append(f"Move {mmin:.1f}-{mmax:.1f}% either way")
    return " · ".join(parts)


# ════════════════════════════════════════════════════════════
# AI RULE PARSING — plain English -> structured numeric filters
# ════════════════════════════════════════════════════════════

_PARSE_PROMPT = """You convert a trader's plain-English setup description into
strict numeric filters. Respond with ONLY a JSON object, no markdown, no prose:

{{
  "price_min": <number>,
  "price_max": <number>,
  "rsi_min": <number 0-100>,
  "rsi_max": <number 0-100>,
  "volume_multiplier": <number, 0 if not mentioned>,
  "atr_pct_min": <number, 0 if not mentioned>,
  "atr_pct_max": <number, 0 if not mentioned — 0 means no volatility filter>,
  "move_pct_min": <number, 0 if not mentioned>,
  "move_pct_max": <number, 0 if not mentioned — 0 means no today's-move filter>
}}

Rules:
- If the user gives no price range, use price_min=0, price_max=99999.
- If the user gives no RSI range, use rsi_min=0, rsi_max=100.
- If the user doesn't mention volume, use volume_multiplier=0.
- atr_pct_min/atr_pct_max = the stock's TYPICAL daily trading range as a
  percent of price (volatility, a rolling average) — use this when the
  trader describes how volatile or choppy a stock usually is, e.g.
  "moves 2-3% a day", "high volatility names", "ATR around 2%".
- move_pct_min/move_pct_max = TODAY's price change, magnitude only, up or
  down doesn't matter — use this when the trader describes what just
  happened today, e.g. "up or down 2-3% today", "moved at least 3% today
  either way", "2-3% up/down side".
- If you genuinely can't tell whether they mean typical volatility or
  today's specific move, set move_pct_min/max (today's move is the more
  common intent for a screener) and leave atr_pct at 0.
- Never invent numbers the user didn't imply — leave both bounds at 0 for
  any filter category the trader didn't mention.

Trader's description:
\"\"\"{description}\"\"\"
"""


def parse_rules_with_ai(description, gemini_key):
    """Returns a dict of numeric filters, or None if parsing fails
    (caller should fall back to manual number inputs — never guess)."""
    if not HAS_GEMINI or not gemini_key or not description.strip():
        return None
    try:
        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=_PARSE_PROMPT.format(description=description),
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        data = json.loads(resp.text)
        return {
            "price_min": float(data.get("price_min", 0)),
            "price_max": float(data.get("price_max", 99999)),
            "rsi_min": float(data.get("rsi_min", 0)),
            "rsi_max": float(data.get("rsi_max", 100)),
            "volume_multiplier": float(data.get("volume_multiplier", 0)),
            "atr_pct_min": float(data.get("atr_pct_min", 0)),
            "atr_pct_max": float(data.get("atr_pct_max", 0)),
            "move_pct_min": float(data.get("move_pct_min", 0)),
            "move_pct_max": float(data.get("move_pct_max", 0)),
        }
    except Exception as e:
        st.warning(f"AI rule parsing failed ({e}) — enter filters manually below.")
        return None


# ════════════════════════════════════════════════════════════
# INDICATORS
# ════════════════════════════════════════════════════════════

def _rsi(close, period=14):
    d = close.diff()
    gain = d.clip(lower=0).rolling(period).mean()
    loss = (-d.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _sma(series, period):
    return series.rolling(period).mean()


def _atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ════════════════════════════════════════════════════════════
# BULK FETCH — batched yf.download instead of one Ticker() per symbol
# ════════════════════════════════════════════════════════════

def _fetch_bulk(symbols, batch_size=BATCH_SIZE, period="60d", progress_cb=None):
    """
    Fetches OHLCV for many symbols using yf.download with a ticker list per
    batch — one HTTP round-trip per ~150 symbols instead of one per symbol.
    Returns (dict[sym] -> DataFrame, list of symbols with no usable data).
    """
    out = {}
    failed = []
    batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]

    for bi, batch in enumerate(batches):
        tickers = " ".join(f"{s}.NS" for s in batch)
        try:
            data = yf.download(tickers, period=period, interval="1d",
                                group_by="ticker", threads=True,
                                progress=False, auto_adjust=False)
        except Exception:
            failed.extend(batch)
            if progress_cb:
                progress_cb((bi + 1) / len(batches), f"Batch {bi + 1}/{len(batches)} failed")
            continue

        for sym in batch:
            key = f"{sym}.NS"
            try:
                if len(batch) == 1:
                    df = data
                else:
                    df = data[key] if key in data.columns.get_level_values(0) else None
                if df is None or df.empty or df["Close"].dropna().shape[0] < 20:
                    failed.append(sym)
                    continue
                out[sym] = df.dropna(how="all")
            except Exception:
                failed.append(sym)

        if progress_cb:
            progress_cb((bi + 1) / len(batches),
                        f"Fetched batch {bi + 1}/{len(batches)} · {len(out)} usable so far")

    return out, failed


# ════════════════════════════════════════════════════════════
# NUMERIC SCAN
# ════════════════════════════════════════════════════════════

def _calculate_indicators(sym, df):
    if df is None or len(df) < 20:
        return None
    close = df["Close"]
    vol = df["Volume"]
    cur_close = float(close.iloc[-1])
    if cur_close <= 0:
        return None
    prev = df.iloc[-2]
    rsi_val = _rsi(close).iloc[-1]
    atr_val = _atr(df).iloc[-1]
    vol_avg20 = vol.rolling(20).mean().iloc[-1]
    vol_ratio = float(vol.iloc[-1] / vol_avg20) if vol_avg20 and vol_avg20 > 0 else 0.0
    roc_5 = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) > 6 else 0.0
    chg_pct = float((cur_close - float(prev["Close"])) / float(prev["Close"]) * 100)

    return {
        "symbol": sym,
        "close": cur_close,
        "chg_pct": chg_pct,
        "rsi": float(rsi_val) if pd.notna(rsi_val) else 50.0,
        "atr_pct": float(atr_val / cur_close * 100) if pd.notna(atr_val) else 0.0,
        "vol_ratio": vol_ratio,
        "roc_5": roc_5,
        "pdh": float(prev["High"]),
        "pdl": float(prev["Low"]),
        "df": df,
    }


def _passes_filters(ind, setup):
    """
    THE FIX: atr_pct and move (abs(chg_pct)) are now real filter
    criteria, not just numbers computed and displayed but never
    checked. Both use the same "0 = no filter" convention
    volume_multiplier already used, so an old setup that never set
    them behaves exactly as it did before (pure no-op).
    """
    pmin = float(setup.get("price_min") or 0)
    pmax = float(setup.get("price_max") or 99999)
    rmin = float(setup.get("rsi_min") or 0)
    rmax = float(setup.get("rsi_max") or 100)
    vmin = float(setup.get("volume_multiplier") or 0)
    amin = float(setup.get("atr_pct_min") or 0)
    amax = float(setup.get("atr_pct_max") or 0)
    mmin = float(setup.get("move_pct_min") or 0)
    mmax = float(setup.get("move_pct_max") or 0)

    if not (pmin <= ind["close"] <= pmax):
        return False
    if not (rmin <= ind["rsi"] <= rmax):
        return False
    if vmin > 0 and ind["vol_ratio"] < vmin:
        return False
    if amax > 0 and not (amin <= ind["atr_pct"] <= amax):
        return False
    if mmax > 0 and not (mmin <= abs(ind["chg_pct"]) <= mmax):
        return False
    return True


def run_math_scan(universe, setup, progress_cb=None):
    dfs, fetch_failed = _fetch_bulk(universe, progress_cb=progress_cb)
    shortlist = []
    failed = list(fetch_failed)
    for sym, df in dfs.items():
        ind = _calculate_indicators(sym, df)
        if ind is None:
            failed.append(sym)
            continue
        if _passes_filters(ind, setup):
            shortlist.append(ind)
        # symbols that fetched fine but didn't pass the filter are simply
        # excluded — they're not "failed", they just didn't match
    shortlist.sort(key=lambda x: x["vol_ratio"], reverse=True)
    return shortlist, failed


# ════════════════════════════════════════════════════════════
# CHART IMAGE — full rendered chart for Gemini vision comparison
# ════════════════════════════════════════════════════════════

def _make_chart_image(sym, df, lookback=60):
    d = df.tail(lookback).copy()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6, 4.2), dpi=110,
                                    gridspec_kw={"height_ratios": [3, 1]},
                                    sharex=True)
    fig.patch.set_facecolor(DARK)
    for ax in (ax1, ax2):
        ax.set_facecolor(DARK)
        ax.tick_params(colors=T2, labelsize=6)
        for spine in ax.spines.values():
            spine.set_color(BORDER)

    x = np.arange(len(d))
    up = (d["Close"] >= d["Open"]).to_numpy()
    op, cl = d["Open"].to_numpy(), d["Close"].to_numpy()
    hi, lo = d["High"].to_numpy(), d["Low"].to_numpy()

    ax1.bar(x[up], (cl - op)[up], bottom=op[up], width=0.6, color=GREEN)
    ax1.bar(x[~up], (op - cl)[~up], bottom=cl[~up], width=0.6, color=RED)
    ax1.vlines(x, lo, hi, color=T2, linewidth=0.6)
    ax1.set_title(f"{sym} · {lookback}d", color=IVORY, fontsize=9, loc="left")

    colors = np.where(up, GREEN, RED)
    ax2.bar(x, d["Volume"].to_numpy(), width=0.6, color=colors, alpha=0.7)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ════════════════════════════════════════════════════════════
# HOVER MINI-CHART — cheap pure-SVG line, no matplotlib. Built
# straight from the OHLCV each candidate already carries in memory
# (_calculate_indicators keeps "df"), so generating one of these for
# every row in a results table costs no extra fetch and is fast even
# for a couple hundred rows — unlike _make_chart_image above, which
# is comparatively expensive and only worth paying for the handful
# of AI-audited cards.
# ════════════════════════════════════════════════════════════

def _svg_mini_chart(df, width=220, height=88):
    try:
        closes = [float(c) for c in df["Close"].tail(40).tolist() if pd.notna(c)]
    except Exception:
        closes = []
    if len(closes) < 2:
        return (f'<div style="font-size:10px;color:{T2};padding:28px 0;'
                f'text-align:center;">No chart data</div>')
    color = GREEN if closes[-1] >= closes[0] else RED
    lo, hi = min(closes), max(closes)
    rng = (hi - lo) or 1
    pad = 6
    n = len(closes)
    pts = " ".join(
        f"{pad + i / (n - 1) * (width - 2*pad):.1f},"
        f"{height - pad - ((c - lo) / rng) * (height - 2*pad):.1f}"
        for i, c in enumerate(closes)
    )
    area = f"{pad},{height-pad} {pts} {width-pad},{height-pad}"
    return (f'<svg width="{width}" height="{height-10}" viewBox="0 0 {width} {height}">'
            f'<polygon points="{area}" fill="{color}22"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _render_hover_shortlist(results, max_rows=80):
    shown = results[:max_rows]
    rows_html = []
    for r in shown:
        chg = r["chg_pct"]
        cc = GREEN if chg >= 0 else RED
        chart_svg = _svg_mini_chart(r.get("df"))
        rows_html.append(f"""<tr>
          <td>
            <span class="hc-wrap">{r['symbol']}
              <span class="hc-pop">
                <div class="hc-pop-head">{r['symbol']} · 60D</div>
                {chart_svg}
                <div class="hc-pop-foot">
                  <span>Rs {r['close']:,.2f}</span>
                  <span style="color:{cc};">{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%</span>
                </div>
              </span>
            </span>
          </td>
          <td>Rs {r['close']:,.2f}</td>
          <td style="color:{cc};">{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%</td>
          <td>{r['rsi']:.0f}</td>
          <td>{r['vol_ratio']:.2f}x</td>
          <td>{r['roc_5']:.1f}%</td>
          <td>{r['atr_pct']:.2f}%</td>
          <td>Rs {r['pdh']:,.2f}</td>
          <td>Rs {r['pdl']:,.2f}</td>
        </tr>""")

    header = ("<tr><th>SYMBOL</th><th>PRICE</th><th>CHG%</th><th>RSI</th>"
              "<th>VOL RATIO</th><th>5D ROC</th><th>ATR%</th><th>PDH</th><th>PDL</th></tr>")
    st.markdown(
        f'<div class="hc-table-wrap"><table class="hc-table"><thead>{header}</thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table></div>',
        unsafe_allow_html=True,
    )
    if len(results) > max_rows:
        st.caption(f"Showing top {max_rows} of {len(results)} by the sort above — "
                    f"narrow your filters or export the full CSV to see the rest.")


# ════════════════════════════════════════════════════════════
# AI VISION AUDIT — strict rubric, structured JSON, image-to-image
# ════════════════════════════════════════════════════════════

_AUDIT_PROMPT = """You are a skeptical technical-analysis reviewer. Compare the
CANDIDATE chart against the REFERENCE chart, which is the trader's saved
example of the setup they are looking for.

Trader's own description of what makes this setup valid:
\"\"\"{visual_rules}\"\"\"

Score how structurally similar the candidate is to the reference, using this
rubric. Default to skepticism — most candidates will NOT be a good match:
  0-2  = no meaningful resemblance
  3-5  = same rough sector/volatility but different structure
  6-7  = same broad pattern type, some real differences remain
  8-10 = the same setup — reserve this only for close structural matches

Evaluate specifically: overall trend/shape, candle formation, position of
highs/lows relative to recent price action, and volume behavior versus the
reference. Do not give a high score just because both charts show *a* trend
or *a* pattern in general — the structure has to actually match.

Respond with ONLY this JSON object, no markdown, no other text:
{{
  "score": <integer 0-10>,
  "matches": [<short phrases, specific criteria that matched, max 4>],
  "mismatches": [<short phrases, specific criteria that did NOT match, max 4>],
  "caption": "<one sentence, plain language, explaining the verdict>"
}}
"""


def _parse_audit(raw_text):
    """A parse failure must never produce a passing score — default to 0
    so it gets excluded by the strictness floor instead of leaking through."""
    try:
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        data = json.loads(text)
        score = data.get("score", 0)
        score = int(score) if isinstance(score, (int, float)) else 0
        score = max(0, min(10, score))
        return {
            "score": score,
            "matches": list(data.get("matches", []))[:4],
            "mismatches": list(data.get("mismatches", []))[:4],
            "caption": str(data.get("caption", "")).strip(),
        }
    except Exception:
        return {"score": 0, "matches": [], "mismatches": ["Could not parse AI response"], "caption": ""}


def _audit_one(sym, chart_png_bytes, visual_rules, ref_image_b64, gemini_key):
    if not HAS_GEMINI or not gemini_key:
        return {"score": 0, "matches": [], "mismatches": ["Gemini not configured"], "caption": ""}
    try:
        client = genai.Client(api_key=gemini_key)
        parts = [_AUDIT_PROMPT.format(visual_rules=visual_rules or "(no description given)")]
        if ref_image_b64:
            parts.append(genai_types.Part.from_bytes(
                data=base64.b64decode(ref_image_b64), mime_type="image/png"))
        parts.append(genai_types.Part.from_bytes(data=chart_png_bytes, mime_type="image/png"))

        resp = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=parts,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        return _parse_audit(resp.text)
    except Exception as e:
        return {"score": 0, "matches": [], "mismatches": [f"AI call failed: {e}"], "caption": ""}


def run_ai_audit(candidates, setup, gemini_key, max_stocks=15,
                  strict_min=MIN_SIMILARITY_FLOOR, progress_cb=None):
    top = candidates[:max_stocks]
    visual_rules = setup.get("visual_rules", "")
    ref_b64 = setup.get("reference_image_b64", "")
    results = []

    def _process(candidate):
        sym = candidate["symbol"]
        chart_bytes = _make_chart_image(sym, candidate["df"])
        audit = _audit_one(sym, chart_bytes, visual_rules, ref_b64, gemini_key)
        merged = {k: v for k, v in candidate.items() if k != "df"}
        merged.update(audit)
        merged["chart_png"] = chart_bytes
        return merged

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_process, c): c for c in top}
        for i, fut in enumerate(as_completed(futures), 1):
            if progress_cb:
                progress_cb(i / len(top), f"AI comparing charts… ({i}/{len(top)})")
            try:
                res = fut.result()
                if res.get("score", 0) >= strict_min:
                    results.append(res)
            except Exception as exc:
                st.error(f"AI audit failed for {futures[fut]['symbol']}: {exc}")

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    for r in results:
        r["verdict"] = ("STRONG MATCH" if r["score"] >= 8 else
                         "PARTIAL MATCH" if r["score"] >= 6 else "NO MATCH")
    return results


# ════════════════════════════════════════════════════════════
# RESULT CARD
# ════════════════════════════════════════════════════════════

def _render_result_card(res, setup):
    score = res.get("score", 0)
    verdict = res.get("verdict", "NO MATCH")
    vc = GREEN if verdict == "STRONG MATCH" else INDIGO if verdict == "PARTIAL MATCH" else T2
    chg = res.get("chg_pct", 0)
    cc = GREEN if chg >= 0 else RED

    c1, c2 = st.columns([1, 2])
    with c1:
        if res.get("chart_png"):
            st.image(res["chart_png"], use_container_width=True)
    with c2:
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {vc};
             padding:16px 20px;margin-bottom:10px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <span style="font-size:15px;font-weight:800;color:{IVORY};">{res['symbol']}</span>
            <span style="background:{vc}1C;color:{vc};border:1px solid {vc}44;font-size:11px;
                  font-weight:700;padding:3px 12px;">{verdict} · {score}/10</span>
          </div>
          <div style="font-family:{MONO};font-size:13px;color:{IVORY};margin-bottom:4px;">
              Rs {res.get('close', 0):,.2f}
              <span style="color:{cc};margin-left:8px;">{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%</span>
          </div>
          <div style="font-size:13px;color:{T2};line-height:1.6;margin:8px 0;">{res.get('caption', '')}</div>
        """, unsafe_allow_html=True)
        for m in res.get("matches", []):
            st.markdown(f"<div style='font-size:12px;color:{GREEN};margin:2px 0;'>+ {m}</div>", unsafe_allow_html=True)
        for m in res.get("mismatches", []):
            st.markdown(f"<div style='font-size:12px;color:{RED};margin:2px 0;'>- {m}</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════
# SETUP MANAGER
# ════════════════════════════════════════════════════════════

def _render_setup_manager(supabase, gemini_key):
    _section("Create a New Setup", PURPLE)
    st.caption("Upload a chart of the pattern you're looking for, describe it in "
               "plain English, and Arka AI will extract numeric filters — including "
               "volatility and today's move — before you save. You can always adjust "
               "them by hand.")

    with st.form("new_setup_form", clear_on_submit=True):
        name = st.text_input("Setup name", placeholder="e.g. Bull Flag + Volume Surge")
        ref_img = st.file_uploader("Reference chart image", type=["png", "jpg", "jpeg"])
        description = st.text_area(
            "Describe the setup in plain English",
            placeholder="e.g. Price between 200 and 1000, RSI between 45 and 70, "
                        "volume at least 1.5x the 20-day average, moving 2-3% up or "
                        "down today, breaking out above a tight consolidation range.",
            height=100)

        ai_col, _sp = st.columns([1, 3])
        with ai_col:
            use_ai = st.form_submit_button("Let AI suggest filters →", use_container_width=True)

        if use_ai:
            parsed = parse_rules_with_ai(description, gemini_key)
            if parsed:
                st.session_state["_pending_parsed_filters"] = parsed
                st.success("AI extracted filters below — review and adjust before saving.")

        defaults = st.session_state.get("_pending_parsed_filters", {})
        f1, f2 = st.columns(2)
        with f1:
            price_min = st.number_input("Min price (Rs)", 0.0, 99999.0,
                                        value=float(defaults.get("price_min", 0)), step=10.0)
            rsi_min = st.number_input("Min RSI", 0.0, 100.0,
                                      value=float(defaults.get("rsi_min", 0)))
        with f2:
            price_max = st.number_input("Max price (Rs)", 0.0, 99999.0,
                                        value=float(defaults.get("price_max", 99999)), step=10.0)
            rsi_max = st.number_input("Max RSI", 0.0, 100.0,
                                      value=float(defaults.get("rsi_max", 100)))
        volume_multiplier = st.number_input("Min volume (x 20-day avg, 0 = no filter)",
                                            0.0, 10.0, value=float(defaults.get("volume_multiplier", 0)), step=0.1)

        st.markdown(f"<div style='font-size:11px;color:{T2};margin:6px 0 2px;'>"
                    f"Volatility &amp; today's move (0 = no filter on either bound)</div>",
                    unsafe_allow_html=True)
        f3, f4 = st.columns(2)
        with f3:
            atr_pct_min = st.number_input("Min typical daily volatility — ATR %", 0.0, 50.0,
                                          value=float(defaults.get("atr_pct_min", 0)), step=0.1)
            move_pct_min = st.number_input("Min move TODAY, either direction %", 0.0, 50.0,
                                           value=float(defaults.get("move_pct_min", 0)), step=0.1)
        with f4:
            atr_pct_max = st.number_input("Max typical daily volatility — ATR %", 0.0, 50.0,
                                          value=float(defaults.get("atr_pct_max", 0)), step=0.1)
            move_pct_max = st.number_input("Max move TODAY, either direction %", 0.0, 50.0,
                                           value=float(defaults.get("move_pct_max", 0)), step=0.1)

        submitted = st.form_submit_button("Save Setup", type="primary", use_container_width=True)
        if submitted:
            if not name.strip():
                st.error("Give the setup a name.")
            elif price_min > price_max:
                st.error("Min price is greater than max price.")
            else:
                ref_b64 = None
                if ref_img is not None:
                    ref_b64 = base64.b64encode(ref_img.read()).decode("utf-8")
                ok = _save_setup(supabase, name.strip(), price_min, price_max,
                                 rsi_min, rsi_max, volume_multiplier,
                                 atr_pct_min, atr_pct_max, move_pct_min, move_pct_max,
                                 description.strip(), ref_b64)
                if ok:
                    st.session_state.pop("_pending_parsed_filters", None)
                    st.success(f"Setup '{name}' saved.")
                    time.sleep(0.6)
                    st.rerun()

    _section("Your Saved Setups", INDIGO)
    setups = _load_setups(supabase)
    if not setups:
        st.info("No setups yet — create one above.")
        return

    for setup in setups:
        with st.expander(f"{setup['name']}  ·  {_filters_summary(setup)}"):
            cc1, cc2 = st.columns([1, 2])
            with cc1:
                if setup.get("reference_image_b64"):
                    st.image(base64.b64decode(setup["reference_image_b64"]), use_container_width=True)
            with cc2:
                st.markdown(f"**Description:** {setup.get('visual_rules') or '(none)'}")
                st.markdown(f"**Filters:** {_filters_summary(setup)}")
                if st.button("Delete this setup", key=f"del_{setup['id']}"):
                    if _delete_setup(supabase, setup["id"]):
                        st.rerun()


# ════════════════════════════════════════════════════════════
# SCAN PAGE
# ════════════════════════════════════════════════════════════

def _render_scan_page(supabase, gemini_key):
    setups = _load_setups(supabase)
    if not setups:
        st.warning("No setups found. Go to the Manage Setups tab and create one first.")
        return

    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {BLUE};
         padding:14px 20px;margin-bottom:18px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};margin-bottom:2px;">Smart Scan</div>
        <div style="font-size:12px;color:{T2};">
            Pick a setup, tune the live overrides, hit Run Scan. Your numeric rules —
            price, RSI, volume, volatility, and today's move — filter the universe
            first, then strict AI vision keeps only the charts that genuinely match
            your reference setup.
        </div>
    </div>""", unsafe_allow_html=True)

    _section("Your Setups — Tap to Select")
    cols = st.columns(min(len(setups), 3))
    selected_key = st.session_state.get("selected_setup_id")

    for i, setup in enumerate(setups):
        with cols[i % 3]:
            is_sel = str(setup["id"]) == str(selected_key)
            bd = BLUE if is_sel else BORDER
            bg = "rgba(255,159,10,0.08)" if is_sel else DARK2
            sel_txt = "SELECTED" if is_sel else "TAP TO SELECT"
            sel_col = BLUE if is_sel else T2

            if setup.get("reference_image_b64"):
                st.image(base64.b64decode(setup["reference_image_b64"]), use_container_width=True)

            st.markdown(f"""
            <div style="background:{bg};border:1px solid {bd};padding:14px;
                 margin-bottom:8px;text-align:center;">
                <div style="font-size:14px;font-weight:800;color:{IVORY};margin-bottom:6px;">{setup['name']}</div>
                <div style="font-size:10px;color:{T2};line-height:1.8;">{_filters_summary(setup)}</div>
                <div style="font-size:9px;letter-spacing:2px;color:{sel_col};margin-top:8px;font-weight:700;">{sel_txt}</div>
            </div>""", unsafe_allow_html=True)

            if st.button("Select", key=f"sel_{setup['id']}", use_container_width=True):
                st.session_state["selected_setup_id"] = str(setup["id"])
                st.rerun()

    selected_setup = next((s for s in setups if str(s["id"]) == str(selected_key)), None) if selected_key else None
    if not selected_setup:
        st.info("Select a setup above to start scanning.")
        return

    _section("Price Range — Pick Before Scanning")
    base_pmin = float(selected_setup.get("price_min") or 0)
    base_pmax = float(selected_setup.get("price_max") or 99999)
    PRESETS = {
        "Use setup's range": (base_pmin, base_pmax),
        "100 - 250": (100, 250), "250 - 500": (250, 500),
        "500 - 750": (500, 750), "750 - 1000": (750, 1000),
        "1000 - 1500": (1000, 1500), "1500 - 2000": (1500, 2000),
        "Custom": None,
    }
    pcol1, pcol2, pcol3 = st.columns([2, 1, 1])
    with pcol1:
        preset = st.selectbox("Price band (Rs)", list(PRESETS.keys()), key="price_preset")
    if preset == "Custom":
        with pcol2:
            ov_pmin = st.number_input("Min price (Rs)", 0.0, 99999.0, value=max(base_pmin, 0.0), step=10.0, key="ov_pmin")
        with pcol3:
            ov_pmax = st.number_input("Max price (Rs)", 0.0, 99999.0, value=min(base_pmax, 99999.0), step=10.0, key="ov_pmax")
    else:
        ov_pmin, ov_pmax = PRESETS[preset]
        with pcol2:
            st.metric("Min", f"Rs {ov_pmin:,.0f}")
        with pcol3:
            st.metric("Max", f"Rs {ov_pmax:,.0f}")

    if ov_pmin > ov_pmax:
        st.error("Min price is greater than max price — fix the range before scanning.")
        return

    _section("Quick Filters — Live Overrides")
    qcol1, qcol2 = st.columns(2)
    with qcol1:
        rsi_override_on = st.toggle("Override RSI range", value=False, key="rsi_ov_on")
        if rsi_override_on:
            ov_rsi_min, ov_rsi_max = st.slider("RSI between", 0, 100,
                (int(float(selected_setup.get("rsi_min") or 0)), int(float(selected_setup.get("rsi_max") or 100))),
                key="ov_rsi")
        else:
            ov_rsi_min = float(selected_setup.get("rsi_min") or 0)
            ov_rsi_max = float(selected_setup.get("rsi_max") or 100)
    with qcol2:
        vol_override_on = st.toggle("Override volume rule", value=False, key="vol_ov_on")
        if vol_override_on:
            ov_vol = st.slider("Min volume (x 20-day avg)", 0.0, 5.0,
                float(selected_setup.get("volume_multiplier") or 0.0), 0.1, key="ov_vol")
        else:
            ov_vol = float(selected_setup.get("volume_multiplier") or 0.0)

    qcol3, qcol4 = st.columns(2)
    with qcol3:
        atr_override_on = st.toggle("Override volatility (ATR%) range", value=False, key="atr_ov_on")
        base_atr_min = float(selected_setup.get("atr_pct_min") or 0)
        base_atr_max = float(selected_setup.get("atr_pct_max") or 0)
        if atr_override_on:
            ov_atr_min, ov_atr_max = st.slider(
                "ATR% between — stock's typical daily range", 0.0, 20.0,
                (base_atr_min, base_atr_max if base_atr_max > 0 else 5.0),
                step=0.1, key="ov_atr")
        else:
            ov_atr_min, ov_atr_max = base_atr_min, base_atr_max
    with qcol4:
        move_override_on = st.toggle("Override today's move % range", value=False, key="move_ov_on")
        base_move_min = float(selected_setup.get("move_pct_min") or 0)
        base_move_max = float(selected_setup.get("move_pct_max") or 0)
        if move_override_on:
            ov_move_min, ov_move_max = st.slider(
                "Move % today, either direction, between", 0.0, 20.0,
                (base_move_min, base_move_max if base_move_max > 0 else 3.0),
                step=0.1, key="ov_move")
        else:
            ov_move_min, ov_move_max = base_move_min, base_move_max

    scan_setup = dict(selected_setup)
    scan_setup["price_min"] = float(ov_pmin)
    scan_setup["price_max"] = float(ov_pmax)
    scan_setup["rsi_min"] = float(ov_rsi_min)
    scan_setup["rsi_max"] = float(ov_rsi_max)
    scan_setup["volume_multiplier"] = float(ov_vol)
    scan_setup["atr_pct_min"] = float(ov_atr_min)
    scan_setup["atr_pct_max"] = float(ov_atr_max)
    scan_setup["move_pct_min"] = float(ov_move_min)
    scan_setup["move_pct_max"] = float(ov_move_max)

    _section(f"Scan With: {selected_setup['name']}")
    st.caption(f"Active filters this run: {_filters_summary(scan_setup)}")
    c1, c2 = st.columns([2, 1])
    with c1:
        universe_opt = st.selectbox("Scan Universe",
            ["ALL NSE Stocks", "Liquid NSE (fast)", "Your Watchlist", "Arka Watchlist"],
            key="scan_universe")
    with c2:
        max_ai = st.number_input("Max AI Comparisons", 3, 40, 15, 1, key="scan_max_ai")

    strict_min = st.slider("Match strictness (hide anything below this score)",
        0, 10, MIN_SIMILARITY_FLOOR, 1, key="strict_min",
        help="6 = balanced. 8 = only near-identical setups. 0 = show everything (not recommended).")

    if universe_opt == "ALL NSE Stocks":
        with st.spinner("Loading NSE symbol list…"):
            universe, universe_source = get_full_nse_universe()
        st.caption(f"Using: {universe_source}")
    elif universe_opt == "Liquid NSE (fast)":
        universe = NSE_LIQUID_UNIVERSE
        universe_source = f"liquid list · {len(universe)} symbols"
        st.caption(f"Using: {universe_source}")
    elif universe_opt == "Your Watchlist":
        universe = st.session_state.get("watchlist", [])
        universe_source = f"your watchlist · {len(universe)} symbols"
        if not universe:
            st.warning("Upload your watchlist in the Scanner tab first.")
            return
    else:
        universe = st.session_state.get("admin_watchlist", [])
        universe_source = f"Arka watchlist · {len(universe)} symbols"
        if not universe:
            st.warning("Arka Watchlist not available yet.")
            return

    if not gemini_key:
        st.warning("GEMINI_KEY not found in secrets — AI vision will be skipped; only rule-based results shown.")

    if st.button("Run Scan", type="primary", use_container_width=True, key="run_scan"):
        prog = st.progress(0.0)
        stat = st.empty()

        def _prog(pct, msg):
            prog.progress(min(float(pct), 1.0))
            stat.markdown(f"**{msg}**")

        _prog(0.02, f"Scanning {len(universe)} symbols from {universe_source}…")
        shortlist, failed = run_math_scan(universe, scan_setup, _prog)

        if not shortlist:
            prog.progress(1.0)
            stat.empty()
            st.warning("No stocks passed your numeric filters. Widen the price/RSI/volume/"
                       "volatility/move range — active filters were: " + _filters_summary(scan_setup))
            if failed:
                with st.expander(f"{len(failed)} symbols had no usable data"):
                    st.write(", ".join(failed[:80]))
            return

        st.session_state["scan_math_results"] = shortlist
        st.session_state["scan_strict_min"] = int(strict_min)
        st.session_state["scan_universe_used"] = universe_source
        stat.markdown(f"{len(shortlist)} candidates passed the rule filter")

        if HAS_GEMINI and gemini_key:
            ai_prog = st.progress(0.0)
            ai_stat = st.empty()

            def _ai_prog(pct, msg):
                ai_prog.progress(min(float(pct), 1.0))
                ai_stat.markdown(f"**{msg}**")

            ai_results = run_ai_audit(shortlist, scan_setup, gemini_key,
                max_stocks=int(max_ai), strict_min=int(strict_min), progress_cb=_ai_prog)
            ai_prog.empty()
            ai_stat.empty()
            st.session_state["scan_ai_results"] = ai_results
            st.success(f"Vision step complete — {len(ai_results)} stocks meet similarity >= {strict_min}/10")
        else:
            st.session_state["scan_ai_results"] = None
            st.info("Gemini key missing — skipping AI vision; only rule-based results shown.")
        prog.empty()
        stat.empty()
        st.rerun()

    math_results = st.session_state.get("scan_math_results")
    ai_results = st.session_state.get("scan_ai_results")
    used_strict = st.session_state.get("scan_strict_min", MIN_SIMILARITY_FLOOR)
    used_universe = st.session_state.get("scan_universe_used", "")

    if math_results is None:
        return

    _section("Scan Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Universe", used_universe.split(" · ")[-1] if used_universe else "—")
    m2.metric("Passed Your Rules", len(math_results))
    m3.metric("AI Compared", len(ai_results) if ai_results else "—")
    m4.metric(f"True Matches (>={used_strict})", len(ai_results) if ai_results else "Skipped")

    if ai_results:
        _section("Pattern Match Results")
        fcol1, fcol2, fcol3 = st.columns([1.4, 1.4, 1.4])
        with fcol1:
            min_sim = st.slider("Min similarity", 0, 10, int(used_strict), 1, key="min_sim")
        with fcol2:
            sort_opt = st.selectbox("Sort by",
                ["Similarity", "RSI (oversold first)", "Volume Ratio", "% Change"], key="sort_results")
        with fcol3:
            filt_v = st.radio("Show", ["All", "Strong", "Partial"], horizontal=True, key="filt_verdict")

        ordered = [r for r in ai_results if r.get("score", 0) >= min_sim]
        if sort_opt == "Similarity":
            ordered.sort(key=lambda x: x.get("score", 0), reverse=True)
        elif "RSI" in sort_opt:
            ordered.sort(key=lambda x: x.get("rsi", 50))
        elif "Volume" in sort_opt:
            ordered.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)
        else:
            ordered.sort(key=lambda x: x.get("chg_pct", 0), reverse=True)

        if filt_v == "Strong":
            ordered = [r for r in ordered if r.get("verdict") == "STRONG MATCH"]
        elif filt_v == "Partial":
            ordered = [r for r in ordered if r.get("verdict") == "PARTIAL MATCH"]

        hidden = len(ai_results) - len(ordered)
        st.caption(f"Showing {len(ordered)} of {len(ai_results)} — {hidden} weaker results hidden.")

        if not ordered:
            st.info("No charts satisfied the current filters. Lower the similarity slider.")
        else:
            for res in ordered:
                _render_result_card(res, selected_setup)

    _section(f"Rules Shortlist ({len(math_results)} stocks)")
    sort_col, dl_col = st.columns([3, 1])
    with sort_col:
        shortlist_sort = st.selectbox(
            "Sort by", ["Volume Ratio", "RSI", "Price", "Chg %", "ATR %", "5D ROC"],
            key="shortlist_sort")
    sort_key_map = {
        "Volume Ratio": lambda r: r["vol_ratio"],
        "RSI": lambda r: r["rsi"],
        "Price": lambda r: r["close"],
        "Chg %": lambda r: r["chg_pct"],
        "ATR %": lambda r: r["atr_pct"],
        "5D ROC": lambda r: r["roc_5"],
    }
    sorted_results = sorted(math_results, key=sort_key_map[shortlist_sort], reverse=True)
    with dl_col:
        st.markdown("<div style='height:26px;'></div>", unsafe_allow_html=True)
        csv_bytes = pd.DataFrame(
            [{k: v for k, v in r.items() if k != "df"} for r in math_results]
        ).to_csv(index=False).encode("utf-8")
        st.download_button("Export CSV", csv_bytes, file_name="arka_shortlist.csv",
                           mime="text/csv", use_container_width=True)

    st.caption("Hover a symbol for a quick 60-day chart.")
    _render_hover_shortlist(sorted_results)


# ════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════

def render_smart_scanner(supabase):
    """Call this from app.py when page == 'smart_scan'."""
    st.markdown(_HOVER_CSS, unsafe_allow_html=True)
    gemini_key = st.secrets.get("GEMINI_KEY", "")

    scan_tab, setup_tab = st.tabs(["Run Scan", "Manage Setups"])
    with scan_tab:
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        _render_scan_page(supabase, gemini_key)
    with setup_tab:
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        _render_setup_manager(supabase, gemini_key)
