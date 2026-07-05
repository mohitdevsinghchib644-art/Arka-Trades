"""
smart_scan_page.py  —  Arka Trades Smart Screener
====================================================================
How it works:
  1. SAVE A SETUP   : upload a reference chart image + describe the setup in
                      plain English. AI parses your text into filters.
  2. TAP & SCAN     : pick a setup, set live overrides (price / RSI / volume),
                      hit Run Scan. Single fast parallel scan across NSE.
  3. AI VISION      : Gemini STRICTLY compares every shortlisted chart against
                      your reference image + rules and ranks by real similarity.
                      Weak matches are auto-hidden so you only see true setups.

--------------------------------------------------------------------------
FIXES IN THIS VERSION — read once, then ignore
--------------------------------------------------------------------------
1) "Matches" showing up that weren't remotely similar
   Root cause: the reference image was only ever stored as a Supabase Storage
   *public URL*, then re-downloaded over plain HTTP at scan time. Any bucket
   permission hiccup (you've hit RLS 403s on this exact bucket before) made
   that download fail *silently* — so Gemini was judging blind off the text
   rules alone, and a blind guess could still score high enough to clear the
   strictness floor. Fixed by storing the reference image as base64 directly
   on the `scan_setups` row, so matching never depends on a network fetch
   succeeding. If a reference image genuinely isn't available, the result is
   now hard-capped at <=4/10 and clearly labeled TEXT-ONLY instead of quietly
   scoring like a real visual match.

   Also migrated off the `google-generativeai` SDK (deprecated Nov 2025,
   increasingly unstable) to the current `google-genai` SDK, and switched
   Gemini's response to strict structured JSON output instead of hand-parsed
   text — this removes an entire class of "the AI's answer didn't parse the
   way the code expected" bugs.

   >>> ONE-TIME ACTION NEEDED — run once in the Supabase SQL editor:
       alter table scan_setups add column if not exists reference_image_b64 text;

2) "ALL NSE Stocks" only ever scanning ~190 stocks
   Root cause: the official symbol list was fetched from archives.nseindia.com,
   which NSE has since moved to nsearchives.nseindia.com. The old URL failed
   every single time, silently, and the code fell back to the small ~190-stock
   hardcoded list while still labeling the scan "ALL NSE Stocks (~2000)".
   Fixed with the corrected URL, a real browser session with retries, a
   transparent status line showing exactly what was loaded and from where, an
   optional Supabase-backed cache so a good list survives NSE outages, and a
   manual CSV upload you can use if NSE ever blocks the server outright.
--------------------------------------------------------------------------
"""

import base64
import io
import json
import time
from datetime import datetime, timezone
from typing import Literal

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    BaseModel = object  # placeholder so the class defs below don't crash on import

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Theme ────────────────────────────────────────────────────────────────────
NAVY   = "#101A33"
IVORY  = "#E2E8F0"
GOLD   = "#4F8DFD"
BLUE   = "#4F8DFD"
GREEN  = "#10B981"
RED    = "#EF4444"
PURPLE = "#8B5CF6"
DARK   = "#0B0F17"
DARK2  = "#0F1522"
DARK3  = "#151D2E"
BORDER = "#1E293B"
T2     = "#94A3B8"
FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"

# Gemini model: using the "-latest" flash alias rather than a pinned version.
# Google has been shutting down/replacing flash models every few months
# (2.0-flash died June 2026, 2.5-flash is slated for Oct 16 2026) — pinning an
# exact version here is exactly what silently broke things last time via the
# deprecated SDK. This alias always points at Google's current GA flash model.
# If you'd rather pin an exact version, swap this for e.g. "gemini-2.5-flash".
MODEL_NAME = "gemini-flash-latest"

# Strictness: hide anything the AI scores below this out of 10.
MIN_SIMILARITY_FLOOR = 6

# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURED SCHEMAS FOR GEMINI (forces valid JSON every time — no text parsing)
# ══════════════════════════════════════════════════════════════════════════════

class SetupFilters(BaseModel):
    price_min: float = 0.0
    price_max: float = 99999.0
    rsi_min: float = 0.0
    rsi_max: float = 100.0
    volume_multiplier: float = 0.0
    roc_min: float = -999.0
    require_above_sma20: bool = False
    require_above_sma50: bool = False
    require_below_sma20: bool = False
    require_breakout: bool = False


if HAS_PYDANTIC:
    class PatternAudit(BaseModel):
        verdict: Literal["STRONG MATCH", "PARTIAL MATCH", "NO MATCH"]
        similarity: float = Field(ge=0, le=10)
        pattern: str
        key_finding: str
        visual_analysis: str
        risk: str
        action: str
else:
    class PatternAudit(BaseModel):
        pass


def _call_gemini_structured(gemini_key: str, contents, schema, temperature: float = 0.15, retries: int = 1):
    """Call Gemini via the current google-genai SDK, forcing structured JSON output
    that matches `schema` (a pydantic BaseModel class). Returns (parsed_instance, error).
    Never raises — failures come back as (None, exception) so callers can degrade cleanly."""
    last_exc = None
    for _attempt in range(retries + 1):
        try:
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=temperature,
                ),
            )
            parsed = response.parsed
            if parsed is None and getattr(response, "text", None):
                parsed = schema.model_validate_json(response.text)
            if parsed is not None:
                return parsed, None
            last_exc = RuntimeError("Empty response from Gemini")
        except Exception as exc:
            last_exc = exc
        time.sleep(0.8)
    return None, last_exc


# ── Liquid NSE Universe (final-resort fallback if everything else fails) ────
NSE_UNIVERSE = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN",
    "BHARTIARTL","KOTAKBANK","BAJFINANCE","LT","WIPRO","HCLTECH","ASIANPAINT",
    "AXISBANK","MARUTI","NESTLEIND","SUNPHARMA","ULTRACEMCO","TITAN","TECHM",
    "POWERGRID","NTPC","ONGC","BAJAJFINSV","TATAMOTORS","DIVISLAB","DRREDDY",
    "CIPLA","ADANIPORTS","INDUSINDBK","JSWSTEEL","TATASTEEL","HINDALCO",
    "COALINDIA","BRITANNIA","GRASIM","BPCL","HEROMOTOCO","IOC","EICHERMOT",
    "SHREECEM","APOLLOHOSP","BAJAJ-AUTO","TATACONSUM","M&M",
    "BANDHANBNK","FEDERALBNK","IDFCFIRSTB","PNB","BANKBARODA","CANBK",
    "UNIONBANK","AUBANK","DCBBANK","CHOLAFIN","MUTHOOTFIN","MANAPPURAM",
    "SBICARD","HDFCLIFE","ICICIPRULI","HDFCAMC","LICHSGFIN","PNBHOUSING",
    "NAUKRI","MPHASIS","COFORGE","PERSISTENT","LTIM","LTTS","KPITTECH",
    "HAPPSTMNDS","TATAELXSI","CYIENT","BIRLASOFT","SONATSOFTW",
    "ALKEM","AUROPHARMA","IPCALAB","LALPATHLAB","BIOCON","GLENMARK",
    "ERIS","AJANTPHARM","GRANULES","LAURUSLABS","LUPIN","TORNTPHARM",
    "FORTIS","MAXHEALTH","NARAYANA","KIMS",
    "TVSMOTOR","ASHOKLEY","ESCORTS","MRF","APOLLOTYRE","CEAT",
    "BALKRISIND","BOSCHLTD","MOTHERSON","SONACOMS","MINDAIND",
    "ACC","AMBUJACEM","RAMCOCEM","JKCEMENT","DALMIABL",
    "NCC","IRCON","RVNL","HGINFRA","PNCINFRA",
    "TATAPOWER","TORNTPOWER","CESC","NHPC","SJVN","RECLTD","PFC",
    "ADANIGREEN","ADANIENT","JIOFIN",
    "VEDL","NMDC","SAIL","HINDZINC","NATIONALUM",
    "ITC","EMAMILTD","RADICO","COLPAL","PIDILITIND","GODREJCP",
    "MARICO","DABUR","BERGEPAINT","HAVELLS","VOLTAS","TRENT","DMART",
    "SRF","PIIND","FINEORG","VINATI","DEEPAKNTR","NAVINFLUOR",
    "HAL","BEL","BHEL","SIEMENS","ABB","CUMMINSIND","THERMAX",
    "DLF","GODREJPROP","OBEROIRLTY","PRESTIGE","BRIGADE","LODHA",
    "IRCTC","ZOMATO","NYKAA","DELHIVERY","PVRINOX",
    "IRFC","GMRINFRA","HUDCO",
]
NSE_UNIVERSE = list(dict.fromkeys(NSE_UNIVERSE))


# ══════════════════════════════════════════════════════════════════════════════
# FULL NSE UNIVERSE  (official list, ~2000 stocks) — live fetch, cache, upload
# ══════════════════════════════════════════════════════════════════════════════

# NSE moved this file from archives.nseindia.com -> nsearchives.nseindia.com.
# The old host is kept as a last-resort mirror in case that ever changes back.
_NSE_EQUITY_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
]

_NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_UNIVERSE_CACHE_BUCKET = "setup-images"      # reuses the bucket that already exists/works
_UNIVERSE_CACHE_PATH   = "system/nse_universe_cache.json"


def _parse_universe_csv_bytes(file_bytes: bytes) -> list:
    """Parse an EQUITY_L.csv-shaped file (from NSE or manually uploaded) into a symbol list."""
    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
        df.columns = [c.strip() for c in df.columns]
        if "SERIES" in df.columns:
            df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
        col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[0]
        syms = df[col].astype(str).str.strip().str.upper().dropna().unique().tolist()
        return [s for s in syms if s and s.isascii()]
    except Exception:
        return []


def _fetch_live_nse_universe() -> tuple:
    """Try to download the current official NSE equity list. Returns (symbols, status_message)."""
    if not HAS_REQUESTS:
        return [], "the `requests` package isn't available"

    last_err = "unknown error"
    try:
        session = _requests.Session()
        session.headers.update(_NSE_HEADERS)
        try:
            # Best-effort cookie warm-up. The static archive host usually doesn't need
            # it, but NSE's dynamic API endpoints do, so this is cheap insurance in
            # case the archive endpoint gets the same bot-protection treatment later.
            session.get("https://www.nseindia.com", timeout=10)
        except Exception:
            pass

        for url in _NSE_EQUITY_URLS:
            for _attempt in range(2):
                try:
                    r = session.get(url, timeout=15)
                    if r.status_code == 200 and len(r.content) > 1000:
                        syms = _parse_universe_csv_bytes(r.content)
                        if len(syms) > 500:
                            host = url.split("/")[2]
                            return syms, f"Live NSE list ({host})"
                        last_err = f"{url} → only parsed {len(syms)} symbols"
                    else:
                        last_err = f"{url} → HTTP {r.status_code}"
                except Exception as exc:
                    last_err = f"{url} → {str(exc)[:80]}"
                time.sleep(1.2)
    except Exception as exc:
        last_err = str(exc)[:150]
    return [], last_err


def _cache_universe_to_supabase(supabase, symbols: list) -> bool:
    """Best-effort: stash a working universe list in Supabase Storage so a recent
    real list survives even if NSE blocks the next few attempts. Never raises."""
    if supabase is None:
        return False
    try:
        payload = json.dumps({
            "symbols": symbols,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }).encode("utf-8")
        supabase.storage.from_(_UNIVERSE_CACHE_BUCKET).upload(
            _UNIVERSE_CACHE_PATH, payload,
            file_options={"content-type": "application/json", "upsert": "true"})
        return True
    except Exception:
        return False


def _load_universe_from_supabase_cache(supabase):
    """Best-effort read of the cached universe list. Returns (symbols, cached_at) or None."""
    if supabase is None:
        return None
    try:
        raw = supabase.storage.from_(_UNIVERSE_CACHE_BUCKET).download(_UNIVERSE_CACHE_PATH)
        data = json.loads(raw.decode("utf-8"))
        syms = data.get("symbols", [])
        if syms:
            return syms, data.get("cached_at", "")
        return None
    except Exception:
        return None


@st.cache_data(ttl=21600, show_spinner=False)
def get_full_nse_universe(_supabase=None) -> dict:
    """Returns {"symbols": [...], "source": "...", "count": N, "live": bool}.
    Priority: live NSE fetch -> Supabase cached copy -> tiny hardcoded fallback.
    (Leading underscore on _supabase tells st.cache_data not to try hashing it.)"""
    syms, msg = _fetch_live_nse_universe()
    if syms:
        _cache_universe_to_supabase(_supabase, syms)   # best effort, ignored if it fails
        return {"symbols": syms, "source": f"✅ {msg}", "count": len(syms), "live": True}

    cached = _load_universe_from_supabase_cache(_supabase)
    if cached and len(cached[0]) > 500:
        syms2, cached_at = cached
        nice_date = cached_at[:10] if cached_at else "an earlier session"
        return {"symbols": syms2,
                "source": f"⚠️ Live NSE fetch failed ({msg}) — using cached list from {nice_date}",
                "count": len(syms2), "live": False}

    return {"symbols": NSE_UNIVERSE,
            "source": f"⚠️ Live NSE fetch failed ({msg}) — using the built-in {len(NSE_UNIVERSE)}-stock fallback list",
            "count": len(NSE_UNIVERSE), "live": False}


# ══════════════════════════════════════════════════════════════════════════════
# NATURAL LANGUAGE → FILTERS
# ══════════════════════════════════════════════════════════════════════════════

_PARSE_PROMPT = """You are a trading-rule parser. The user describes a stock setup
in plain English. Extract any NUMERIC / TECHNICAL conditions mentioned and leave
everything else at its default.

Mapping hints:
- "price between 100 and 1000" -> price_min 100, price_max 1000
- "under 250" / "below 250" -> price_max 250
- "RSI above 55" -> rsi_min 55 | "RSI below 40 / oversold" -> rsi_max 40
- "volume spike / high volume / 2x volume" -> volume_multiplier 1.5 (or the stated number)
- "above 20 SMA / 50 SMA" -> require_above_sma20/50 true
- "below 20 SMA / in pullback under 20sma" -> require_below_sma20 true
- "breaking out / closing above previous high / PDH breakout" -> require_breakout true
- "momentum / strong move last week" -> roc_min 3

USER DESCRIPTION:
"""


def parse_rules_with_ai(text: str, gemini_key: str) -> dict:
    """Convert plain-English setup description into structured math filters."""
    defaults = {
        "price_min": 0.0, "price_max": 99999.0,
        "rsi_min": 0.0, "rsi_max": 100.0,
        "volume_multiplier": 0.0, "roc_min": -999.0,
        "require_above_sma20": False, "require_above_sma50": False,
        "require_below_sma20": False, "require_breakout": False,
    }
    if not text.strip() or not (HAS_GEMINI and HAS_PYDANTIC) or not gemini_key:
        return defaults
    parsed, err = _call_gemini_structured(gemini_key, _PARSE_PROMPT + text.strip(),
                                          SetupFilters, temperature=0.0)
    if err is not None or parsed is None:
        return defaults
    out = dict(defaults)
    for k in defaults:
        v = getattr(parsed, k, None)
        if v is not None:
            out[k] = bool(v) if isinstance(defaults[k], bool) else float(v)
    return out


def _filters_summary(s: dict) -> str:
    """Human-readable chips of the parsed filters."""
    parts = []
    pmin, pmax = float(s.get("price_min") or 0), float(s.get("price_max") or 99999)
    if pmin > 0 or pmax < 99999:
        parts.append(f"Price {pmin:,.0f}-{pmax:,.0f}")
    rmin, rmax = float(s.get("rsi_min") or 0), float(s.get("rsi_max") or 100)
    if rmin > 0:  parts.append(f"RSI > {rmin:.0f}")
    if rmax < 100: parts.append(f"RSI < {rmax:.0f}")
    vm = float(s.get("volume_multiplier") or 0)
    if vm > 0: parts.append(f"Vol {vm:.1f}x avg")
    if s.get("require_above_sma20"): parts.append("Above SMA20")
    if s.get("require_above_sma50"): parts.append("Above SMA50")
    if s.get("require_below_sma20"): parts.append("Below SMA20")
    if s.get("require_breakout"):    parts.append("PDH Breakout")
    rmn = float(s.get("roc_min") or -999)
    if rmn > -999: parts.append(f"5D ROC > {rmn:.0f}%")
    return " · ".join(parts) if parts else "No numeric filters — AI vision only"


# ══════════════════════════════════════════════════════════════════════════════
# SUPABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_setups(supabase) -> list:
    try:
        res = (supabase.table("scan_setups").select("*")
               .order("created_at", desc=True).execute())
        return res.data or []
    except Exception as e:
        st.error(f"Load error: {e}")
        return []


def _save_setup(supabase, data: dict) -> bool:
    sid = data.pop("id", None)
    try:
        if sid:
            supabase.table("scan_setups").update(data).eq("id", sid).execute()
        else:
            supabase.table("scan_setups").insert(data).execute()
        return True
    except Exception as e:
        msg = str(e)
        # Graceful path if the one-time migration hasn't been run yet: save everything
        # else and tell the person exactly what to run, instead of losing the setup.
        if "reference_image_b64" in data and (
            "reference_image_b64" in msg or "42703" in msg or "column" in msg.lower()
        ):
            st.warning(
                "Your `scan_setups` table doesn't have a `reference_image_b64` column yet, "
                "so the image wasn't saved this time. Run this once in the Supabase SQL "
                "editor, then re-save this setup:\n\n"
                "```sql\nalter table scan_setups add column if not exists reference_image_b64 text;\n```"
            )
            data.pop("reference_image_b64", None)
            try:
                if sid:
                    supabase.table("scan_setups").update(data).eq("id", sid).execute()
                else:
                    supabase.table("scan_setups").insert(data).execute()
                return True
            except Exception as e2:
                st.error(f"Save error: {e2}")
                return False
        st.error(f"Save error: {e}")
        return False


def _delete_setup(supabase, setup_id) -> bool:
    try:
        supabase.table("scan_setups").delete().eq("id", setup_id).execute()
        return True
    except Exception as e:
        st.error(f"Delete error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# REFERENCE IMAGE HELPERS — stored as base64 on the row, never a fetch-dependent URL
# ══════════════════════════════════════════════════════════════════════════════

def _prepare_reference_image_b64(file_bytes: bytes, max_dim: int = 1000) -> str:
    """Resize/compress an uploaded reference image and return base64 PNG text."""
    if not HAS_PIL:
        return base64.b64encode(file_bytes).decode("utf-8")
    try:
        img = PILImage.open(io.BytesIO(file_bytes)).convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return base64.b64encode(file_bytes).decode("utf-8")


def _decode_reference_image(setup: dict):
    """Return a PIL.Image for a setup's saved reference image, or None if unavailable."""
    b64 = (setup or {}).get("reference_image_b64") or ""
    if not b64 or not HAS_PIL:
        return None
    try:
        return PILImage.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# MATH ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _ema(close, period): return close.ewm(span=period, adjust=False).mean()
def _sma(close, period): return close.rolling(period).mean()

def _atr(high, low, close, period=14):
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── ONE-PASS PARALLEL FETCH (fast, with a light retry per batch) ────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_bulk(symbols_tuple: tuple, period: str = "60d") -> dict:
    """Download all symbols in parallel batches. One network pass, one retry per batch."""
    symbols = list(symbols_tuple)
    ns_syms = [s + ".NS" for s in symbols]
    results = {}
    BATCH   = 250

    def _download_batch(batch_idx):
        start       = batch_idx * BATCH
        batch_ns    = ns_syms[start:start + BATCH]
        batch_plain = symbols[start:start + BATCH]
        out = {}
        if not batch_ns:
            return out
        for attempt in range(2):
            try:
                raw = yf.download(batch_ns, period=period, interval="1d",
                                  auto_adjust=True, progress=False, threads=True)
                if raw.empty:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    return out
                if isinstance(raw.columns, pd.MultiIndex):
                    available = set(raw.columns.get_level_values(1))
                    for sym, ns in zip(batch_plain, batch_ns):
                        if ns not in available:
                            continue
                        try:
                            df = raw.xs(ns, level=1, axis=1).dropna(how="all")
                            if not df.empty and len(df) >= 20 and "Close" in df.columns:
                                out[sym] = df
                        except Exception:
                            pass
                else:
                    if not raw.empty and len(raw) >= 20:
                        out[batch_plain[0]] = raw
                return out
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return out
        return out

    n_batches = (len(ns_syms) + BATCH - 1) // BATCH
    with ThreadPoolExecutor(max_workers=min(6, n_batches or 1)) as ex:
        for batch_result in ex.map(_download_batch, range(n_batches)):
            results.update(batch_result)
    return results


def _calculate_indicators(df):
    df = df.copy()
    df["rsi"]       = _rsi(df["Close"], 14)
    df["sma_20"]    = _sma(df["Close"], 20)
    df["sma_50"]    = _sma(df["Close"], 50)
    df["ema_20"]    = _ema(df["Close"], 20)
    df["atr"]       = _atr(df["High"], df["Low"], df["Close"], 14)
    df["vol_avg20"] = df["Volume"].rolling(20).mean()
    df["vol_ratio"] = df["Volume"] / df["vol_avg20"].replace(0, np.nan)
    df["roc_5"]     = df["Close"].pct_change(5) * 100
    return df


def _apply_filter(df, setup, symbol):
    if df is None or len(df) < 22:
        return None
    last, prev = df.iloc[-1], df.iloc[-2]

    def safe(col):
        v = last.get(col, np.nan)
        return float(v) if pd.notna(v) else np.nan

    rsi, sma_20, sma_50 = safe("rsi"), safe("sma_20"), safe("sma_50")
    vol_ratio, roc_5, atr = safe("vol_ratio"), safe("roc_5"), safe("atr")
    close, prev_close = float(last["Close"]), float(prev["Close"])
    prev_high, prev_low = float(prev["High"]), float(prev["Low"])

    if np.isnan(rsi):
        return None
    if not (float(setup.get("price_min") or 0) <= close <= float(setup.get("price_max") or 99999)):
        return None
    if not (float(setup.get("rsi_min") or 0) <= rsi <= float(setup.get("rsi_max") or 100)):
        return None
    vol_min = float(setup.get("volume_multiplier") or 0)
    if vol_min > 0 and not np.isnan(vol_ratio) and vol_ratio < vol_min:
        return None
    if setup.get("require_above_sma20") and not np.isnan(sma_20) and close <= sma_20:
        return None
    if setup.get("require_above_sma50") and not np.isnan(sma_50) and close <= sma_50:
        return None
    if setup.get("require_below_sma20") and not np.isnan(sma_20) and close >= sma_20:
        return None
    if setup.get("require_breakout") and close <= prev_high:
        return None
    roc_min = float(setup.get("roc_min") or -999)
    if not np.isnan(roc_5) and roc_5 < roc_min:
        return None

    chg_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
    atr_pct = (atr / close * 100) if (not np.isnan(atr) and close > 0) else 0
    return {
        "symbol": symbol, "close": round(close, 2), "chg_pct": round(chg_pct, 2),
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2) if not np.isnan(vol_ratio) else 0.0,
        "roc_5": round(roc_5, 2) if not np.isnan(roc_5) else 0.0,
        "atr_pct": round(atr_pct, 2),
        "pdh": round(prev_high, 2), "pdl": round(prev_low, 2),
        "sma_20": round(sma_20, 2) if not np.isnan(sma_20) else 0.0,
        "sma_50": round(sma_50, 2) if not np.isnan(sma_50) else 0.0,
        "df": df,
    }


def run_math_scan(symbols, setup, progress_cb=None):
    """One parallel download pass, then apply rules (price filter is built in)."""
    if progress_cb:
        progress_cb(0.10, f"Downloading {len(symbols)} stocks in parallel...")
    data_dict = _fetch_bulk(tuple(symbols), "60d")
    if progress_cb:
        progress_cb(0.55, f"Data ready for {len(data_dict)} — applying your rules...")
    shortlist = []
    total = len(data_dict) or 1
    for i, (sym, df) in enumerate(data_dict.items()):
        try:
            result = _apply_filter(_calculate_indicators(df), setup, sym)
            if result:
                shortlist.append(result)
        except Exception:
            pass
        if progress_cb and i % 25 == 0:
            progress_cb(0.55 + 0.40 * (i / total), f"Scanning... {i}/{total}")
    failed = [s for s in symbols if s not in set(data_dict.keys())]
    if progress_cb:
        progress_cb(0.97, f"Rules filter done — {len(shortlist)} candidates")
    shortlist.sort(key=lambda x: x["rsi"])
    return shortlist, failed


# ══════════════════════════════════════════════════════════════════════════════
# CHART GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def _make_chart_image(symbol: str, df: pd.DataFrame) -> bytes:
    df_plot = df.tail(60).copy()
    n = len(df_plot)
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(max(14, n * 0.20), 5.5),
        gridspec_kw={"height_ratios": [3, 1]}, facecolor=DARK)
    for ax in (ax1, ax2):
        ax.set_facecolor(DARK)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        ax.tick_params(colors=T2, labelsize=8)
        ax.grid(axis="y", color=BORDER, linewidth=0.5, linestyle="--", alpha=0.7)

    cw = 0.55
    for i, (_, row) in enumerate(df_plot.iterrows()):
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        col = GREEN if c >= o else RED
        ax1.plot([i, i], [l, h], color=col, linewidth=0.9, zorder=1)
        ax1.add_patch(mpatches.Rectangle(
            (i - cw/2, min(o, c)), cw, max(abs(c - o), (h - l) * 0.008),
            facecolor=col, edgecolor=col, linewidth=0, zorder=2))

    xs = list(range(n))
    if "sma_20" in df_plot.columns:
        ax1.plot(xs, df_plot["sma_20"].values, color=BLUE, linewidth=1.1,
                 alpha=0.85, label="SMA 20")
    if "sma_50" in df_plot.columns:
        ax1.plot(xs, df_plot["sma_50"].values, color=PURPLE, linewidth=1.1,
                 alpha=0.85, label="SMA 50")

    ax1.set_xlim(-1, n + 1)
    pmin, pmax = df_plot["Low"].min(), df_plot["High"].max()
    pad = (pmax - pmin) * 0.05
    ax1.set_ylim(pmin - pad, pmax + pad)
    step = max(1, n // 8)
    ticks = list(range(0, n, step))
    ax1.set_xticks(ticks)
    ax1.set_xticklabels([df_plot.index[i].strftime("%d %b") for i in ticks],
                        rotation=45, ha="right", fontsize=7)
    ax1.legend(fontsize=7, framealpha=0.3, labelcolor=T2)
    ax1.set_title(f"{symbol}  ·  NSE Daily  ·  60 Days", color=BLUE,
                  fontsize=10, fontweight="bold", fontfamily="monospace", pad=6)

    vol_colors = [GREEN if c >= o else RED
                  for c, o in zip(df_plot["Close"], df_plot["Open"])]
    ax2.bar(xs, df_plot["Volume"].values, color=vol_colors, alpha=0.65, width=0.7)
    if "vol_avg20" in df_plot.columns:
        ax2.plot(xs, df_plot["vol_avg20"].values, color=T2, linewidth=0.9, linestyle="--")
    ax2.set_xlim(-1, n + 1)
    ax2.set_xticks([])
    ax2.set_ylabel("Vol", color=T2, fontsize=7)

    plt.tight_layout(pad=1.0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=DARK, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI VISION — STRICT PATTERN SIMILARITY AUDIT
# ══════════════════════════════════════════════════════════════════════════════

def _build_audit_prompt(symbol: str, visual_rules: str, has_reference: bool) -> str:
    rules_txt = visual_rules.strip() or "(No written rules provided — judge purely on visual structure vs the reference image.)"
    if has_reference:
        reference_clause = ("A REFERENCE PATTERN IMAGE is attached above — this is the user's ideal "
                             "setup and your ONLY ground truth for what a match looks like.")
    else:
        reference_clause = (
            "NO REFERENCE IMAGE was provided for this setup — there is nothing to visually compare "
            "against. Because of that you MUST NOT return STRONG MATCH or PARTIAL MATCH no matter how "
            "well the written rules check out. Return verdict NO MATCH, similarity between 0 and 3, and "
            "say plainly in key_finding that there was no reference image to compare against.")

    return f"""You are a STRICT technical-pattern matching judge for NSE Indian equities.
Your ONLY job is to decide if the live chart for {symbol} truly matches the user's setup.

{reference_clause}

USER'S WRITTEN RULES:
{rules_txt}

The live chart is a 60-day daily candlestick chart (candles + SMA-20 blue + SMA-50 purple on top,
volume + 20-day average below). The MOST RECENT candles matter most.

HOW TO JUDGE (be harsh — most stocks should FAIL):
- The live chart must reproduce the SAME visual structure as the reference image: same kind of
  trend, same base/consolidation shape, same pullback/breakout location, similar volume behaviour.
- "Generally bullish" but not a structural match to the reference = NO MATCH. Vague similarity is
  not enough.
- Every written rule you can verify on the chart must hold; if a clear rule is broken, it cannot be
  a STRONG MATCH.
- Ignore colors, watermarks, timeframe labels, and ticker text.

SCORING (0-10, be strict):
9-10 = nearly identical structure, all rules satisfied (STRONG MATCH)
7-8  = same pattern with minor differences, rules mostly satisfied (STRONG MATCH)
5-6  = related but clearly different in structure, or a rule fails (PARTIAL MATCH)
0-4  = different pattern / rules broken / no reference to compare against (NO MATCH)
When unsure, score LOWER, not higher.

Fill in every field. Base everything ONLY on the image(s) shown above — never invent data you
cannot see."""


def _audit_one(symbol: str, chart_bytes: bytes, visual_rules: str,
               ref_image_b64: str, gemini_key: str) -> dict:
    if not (HAS_GEMINI and HAS_PYDANTIC and HAS_PIL):
        return {"symbol": symbol, "verdict": "ERROR", "score": 0.0,
                "key_finding": "google-genai / pydantic / Pillow not available in this environment.",
                "pattern": "N/A", "visual_analysis": "", "risk": "", "action": "",
                "has_reference": False}

    ref_img = None
    has_reference = False
    if ref_image_b64:
        try:
            ref_img = PILImage.open(io.BytesIO(base64.b64decode(ref_image_b64))).convert("RGB")
            has_reference = True
        except Exception:
            ref_img = None
            has_reference = False

    try:
        live_img = PILImage.open(io.BytesIO(chart_bytes)).convert("RGB")
    except Exception as exc:
        return {"symbol": symbol, "verdict": "ERROR", "score": 0.0,
                "key_finding": f"Could not read live chart image: {exc}", "pattern": "N/A",
                "visual_analysis": "", "risk": "", "action": "", "has_reference": False}

    prompt = _build_audit_prompt(symbol, visual_rules, has_reference)
    contents = []
    if has_reference:
        contents += ["REFERENCE PATTERN IMAGE (the user's ideal setup — this is the ground truth):", ref_img]
    contents += [f"LIVE CHART to judge for {symbol}:", live_img, prompt]

    parsed, err = _call_gemini_structured(gemini_key, contents, PatternAudit, temperature=0.15)
    if err is not None or parsed is None:
        return {"symbol": symbol, "verdict": "ERROR", "score": 0.0,
                "key_finding": f"Audit failed: {str(err)[:150] if err else 'no response'}",
                "pattern": "N/A", "visual_analysis": "", "risk": "", "action": "",
                "has_reference": has_reference}

    score = float(parsed.similarity)
    verdict = parsed.verdict
    key_finding = parsed.key_finding

    # Hard server-side safety net: a result can NEVER count as a visual match without
    # a real reference image actually being compared, no matter what the model claims.
    # This is what stops "matches" that are really just guesses off the text rules.
    if not has_reference:
        score = min(score, 4.0)
        verdict = "NO MATCH" if score < 5 else "PARTIAL MATCH"
        key_finding = "No reference image on file for this setup — text-only rule check, not a verified visual match. " + key_finding

    return {
        "symbol": symbol, "verdict": verdict, "score": score,
        "pattern": parsed.pattern, "key_finding": key_finding,
        "visual_analysis": parsed.visual_analysis, "risk": parsed.risk,
        "action": parsed.action, "has_reference": has_reference,
    }


def run_ai_audit(candidates, setup, gemini_key, max_stocks=15, progress_cb=None):
    top = candidates[:max_stocks]
    visual_rules = setup.get("visual_rules", "")
    ref_b64 = setup.get("reference_image_b64", "") or ""
    results = []

    def _process(candidate):
        sym = candidate["symbol"]
        try:
            chart_bytes = _make_chart_image(sym, candidate["df"])
            audit = _audit_one(sym, chart_bytes, visual_rules, ref_b64, gemini_key)
        except Exception as exc:
            audit = {"symbol": sym, "verdict": "ERROR", "score": 0.0,
                     "key_finding": str(exc)[:120], "pattern": "N/A",
                     "visual_analysis": "", "risk": "", "action": "", "has_reference": False}
        return {**candidate, **audit}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_process, c): c for c in top}
        done = 0
        for future in as_completed(futures):
            done += 1
            if progress_cb:
                progress_cb(done / len(top), f"AI comparing charts to your setup... {done}/{len(top)}")
            try:
                results.append(future.result())
            except Exception:
                pass
    if progress_cb:
        progress_cb(1.0, f"Vision audit complete — {len(results)} charts compared")
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _section(title: str):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;margin:28px 0 14px;">
        <div style="font-family:{FONT};font-size:16px;font-weight:800;
             color:{IVORY};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)


def _verdict_colors(verdict: str):
    if verdict == "STRONG MATCH":
        return GREEN, "rgba(16,185,129,0.07)", "rgba(16,185,129,0.4)"
    elif verdict == "PARTIAL MATCH":
        return BLUE, "rgba(79,141,253,0.06)", "rgba(79,141,253,0.35)"
    else:
        return RED, "rgba(239,68,68,0.06)", "rgba(239,68,68,0.35)"


# ══════════════════════════════════════════════════════════════════════════════
# SETUP MANAGER
# ══════════════════════════════════════════════════════════════════════════════

def _render_setup_form(supabase, gemini_key: str, existing: dict = None):
    is_edit = existing is not None
    prefix  = f"edit_{existing.get('id','x')}" if is_edit else "new"
    btn_lbl = "Update Setup" if is_edit else "Save Setup"

    with st.form(f"form_{prefix}", clear_on_submit=not is_edit):
        name = st.text_input(
            "Setup Name *",
            value=existing.get("name", "") if is_edit else "",
            placeholder="e.g. Bull Flag Breakout", key=f"name_{prefix}")

        st.markdown(f"""
        <div style="font-size:12px;font-weight:700;letter-spacing:1px;color:{BLUE};
             text-transform:uppercase;margin:14px 0 6px;">Reference Chart Image</div>
        <div style="font-size:12px;color:{T2};margin-bottom:8px;">
            Upload a screenshot of your ideal setup. It's stored directly with this setup
            (not just a link), so AI matching keeps working even if storage settings change.</div>""",
            unsafe_allow_html=True)
        img_col1, img_col2 = st.columns([2, 1])
        with img_col1:
            uploaded_img = st.file_uploader("Upload setup image",
                                            type=["png", "jpg", "jpeg"],
                                            key=f"img_{prefix}",
                                            label_visibility="collapsed")
        with img_col2:
            if is_edit:
                cur_img = _decode_reference_image(existing)
                if cur_img is not None:
                    st.image(cur_img, width=140)
                    st.caption("Current image")
                elif existing.get("reference_image_url"):
                    st.image(existing["reference_image_url"], width=140)
                    st.caption("⚠ Legacy image — re-upload to migrate")

        st.markdown(f"""
        <div style="font-size:12px;font-weight:700;letter-spacing:1px;color:{BLUE};
             text-transform:uppercase;margin:14px 0 6px;">Describe Your Setup — Plain English</div>
        <div style="font-size:12px;color:{T2};margin-bottom:8px;">
            Write the pattern AND any number rules. The AI extracts price, RSI, volume
            and trend filters from your words, and matches the chart shape to your image.</div>""",
            unsafe_allow_html=True)
        visual_rules = st.text_area(
            "Setup description", label_visibility="collapsed",
            value=existing.get("visual_rules", "") if is_edit else "",
            height=150, key=f"vr_{prefix}",
            placeholder=("Example:\n"
                         "Find stocks that look like this image — a tight flag after a strong "
                         "up move. Price should be between 50 and 250. RSI above 55. "
                         "Volume at least 1.5x average. Stock must be above the 20 SMA "
                         "and breaking out above the previous day high."))

        submitted = st.form_submit_button(btn_lbl, use_container_width=True, type="primary")

        if submitted:
            if not name.strip():
                st.error("Setup Name is required.")
                return
            has_existing_image = is_edit and (existing.get("reference_image_b64") or existing.get("reference_image_url"))
            if not visual_rules.strip() and not uploaded_img and not has_existing_image:
                st.error("Add a description or a reference image (ideally both).")
                return

            ref_b64 = existing.get("reference_image_b64", "") if is_edit else ""
            if uploaded_img is not None:
                img_bytes = uploaded_img.read()
                ref_b64 = _prepare_reference_image_b64(img_bytes)

            with st.spinner("AI is reading your rules..."):
                filters = parse_rules_with_ai(visual_rules, gemini_key)

            payload = dict(
                name=name.strip(),
                description=_filters_summary(filters)[:120],
                reference_image_b64=ref_b64,
                visual_rules=visual_rules.strip(),
                price_min=filters["price_min"], price_max=filters["price_max"],
                rsi_min=filters["rsi_min"], rsi_max=filters["rsi_max"],
                volume_multiplier=filters["volume_multiplier"],
                roc_min=filters["roc_min"],
                require_above_sma20=filters["require_above_sma20"],
                require_above_sma50=filters["require_above_sma50"],
                require_below_sma20=filters["require_below_sma20"],
                require_breakout=filters["require_breakout"],
            )
            if is_edit:
                payload["id"] = existing["id"]

            if _save_setup(supabase, payload):
                st.success(f"{'Updated' if is_edit else 'Saved'}: {name}")
                st.markdown(f"""
                <div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;
                     padding:12px 16px;margin-top:8px;">
                    <div style="font-size:11px;font-weight:700;color:{BLUE};
                         letter-spacing:1px;margin-bottom:4px;">AI EXTRACTED THESE FILTERS</div>
                    <div style="font-size:13px;color:{IVORY};">{_filters_summary(filters)}</div>
                </div>""", unsafe_allow_html=True)
                time.sleep(1.5)
                st.rerun()


def _render_setup_manager(supabase, gemini_key: str):
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};
         border-left:3px solid {BLUE};border-radius:14px;
         padding:16px 22px;margin-bottom:20px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};margin-bottom:4px;">Setup Manager</div>
        <div style="font-size:12px;color:{T2};line-height:1.7;">
            Save unlimited setups. Each one = a reference chart image + a plain-English
            description. The AI extracts the numeric rules from your words and uses the
            image as the ground truth for strict visual pattern matching during scans.
        </div>
    </div>""", unsafe_allow_html=True)

    setups = _load_setups(supabase)

    with st.expander("Create New Setup", expanded=len(setups) == 0):
        _render_setup_form(supabase, gemini_key)

    if not setups:
        st.info("No setups yet — create your first one above.")
        return

    _section(f"{len(setups)} Saved Setups")

    for setup in setups:
        ref_img_obj = _decode_reference_image(setup)
        legacy_only = ref_img_obj is None and bool(setup.get("reference_image_url"))
        title_extra = "  ·  ⚠ legacy image, please re-upload" if legacy_only else ""
        with st.expander(f"{setup['name']}  ·  {setup.get('description','')}{title_extra}", expanded=False):
            img_col, form_col = st.columns([1, 3])
            with img_col:
                if ref_img_obj is not None:
                    st.image(ref_img_obj, use_container_width=True)
                    st.caption("Reference pattern")
                elif setup.get("reference_image_url"):
                    st.image(setup["reference_image_url"], use_container_width=True)
                    st.caption("⚠ Legacy storage — re-upload to enable AI matching")
                else:
                    st.markdown(f"<div style='background:{DARK3};border:1px dashed {BORDER};border-radius:8px;padding:24px;text-align:center;font-size:11px;color:{T2};'>No image</div>",
                                unsafe_allow_html=True)
            with form_col:
                _render_setup_form(supabase, gemini_key, existing=setup)

            st.markdown(f"<div style='font-size:11px;color:{T2};margin-top:4px;'>Active filters: {_filters_summary(setup)}</div>",
                        unsafe_allow_html=True)
            if st.button("Delete Setup", key=f"del_{setup['id']}"):
                if _delete_setup(supabase, setup["id"]):
                    st.success("Deleted")
                    time.sleep(0.4)
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# RESULT CARDS
# ══════════════════════════════════════════════════════════════════════════════

def _render_result_card(res: dict, setup: dict):
    verdict = res.get("verdict", "UNKNOWN")
    score   = res.get("score", 0)
    has_ref = res.get("has_reference", True)
    vc, vbg, vbd = _verdict_colors(verdict)
    chg = res.get("chg_pct", 0)
    cc  = GREEN if chg >= 0 else RED
    arr = "▲" if chg >= 0 else "▼"

    label = f"{res['symbol']}  ·  {verdict}  ·  Similarity {score:.0f}/10  ·  {res.get('pattern','')}"
    if not has_ref:
        label += "  ·  ⚠ TEXT-ONLY"
    with st.expander(label, expanded=(verdict == "STRONG MATCH" and has_ref)):
        if not has_ref:
            st.markdown(f"""
            <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.35);
                 border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:{IVORY};">
                ⚠ <strong>No reference image on file</strong> for this setup — this is a text-rule
                check only, not a verified visual pattern match. Edit the setup and upload a
                reference chart to enable real AI matching.
            </div>""", unsafe_allow_html=True)

        left, right = st.columns([1, 2])
        with left:
            st.markdown(f"""
            <div style="background:{vbg};border:1px solid {vbd};border-radius:12px;padding:16px 14px;">
                <div style="font-size:15px;font-weight:800;letter-spacing:1px;color:{vc};margin-bottom:6px;">{verdict}</div>
                <div style="font-family:{MONO};font-size:26px;font-weight:700;color:{IVORY};
                     line-height:1.1;margin-bottom:6px;">Rs {res.get('close',0):,.2f}</div>
                <div style="font-size:13px;color:{cc};margin-bottom:10px;">
                     {arr} {abs(chg):.2f}%  ·  RSI {res.get('rsi',0):.0f}</div>
                <div style="font-size:10px;letter-spacing:2px;color:{T2};margin-bottom:2px;">SIMILARITY</div>
                <div style="font-family:{MONO};font-size:28px;font-weight:800;color:{vc};line-height:1;">
                     {score:.0f}<span style="font-size:16px;color:{T2};">/10</span></div>
                <hr style="border-color:{BORDER};margin:10px 0;">
                <div style="font-size:11px;color:{T2};line-height:1.9;">
                    Vol ratio: <strong style="color:{IVORY};">{res.get('vol_ratio',0):.1f}x</strong><br>
                    5D ROC: <strong style="color:{cc};">{res.get('roc_5',0):.1f}%</strong><br>
                    ATR: <strong style="color:{IVORY};">{res.get('atr_pct',0):.2f}%</strong><br>
                    PDH: <strong style="color:{IVORY};">Rs {res.get('pdh',0):,.2f}</strong>
                </div>
            </div>""", unsafe_allow_html=True)

        with right:
            def _row(color, heading, body):
                st.markdown(
                    f"<div style='font-size:10px;letter-spacing:2px;color:{color};font-weight:700;margin-top:10px;'>{heading}</div>"
                    f"<div style='font-size:13px;color:{IVORY};line-height:1.6;margin-bottom:2px;'>{body}</div>",
                    unsafe_allow_html=True)

            ref_img_obj = _decode_reference_image(setup)
            if ref_img_obj is not None or setup.get("reference_image_url"):
                rcol1, rcol2 = st.columns([1, 2])
                with rcol1:
                    if ref_img_obj is not None:
                        st.image(ref_img_obj, caption="Your reference", width=130)
                    else:
                        st.image(setup["reference_image_url"], caption="Your reference", width=130)
                with rcol2:
                    _row(BLUE, "KEY FINDING", res.get("key_finding", "-"))
                    _row(BLUE, "VISUAL COMPARISON", res.get("visual_analysis", "-"))
            else:
                _row(BLUE, "KEY FINDING", res.get("key_finding", "-"))
                _row(BLUE, "VISUAL COMPARISON", res.get("visual_analysis", "-"))
            _row(RED,   "RISK",   res.get("risk", "-"))
            _row(GREEN, "ACTION", res.get("action", "-"))

        if "df" in res:
            try:
                st.image(_make_chart_image(res["symbol"], res["df"]), use_container_width=True)
            except Exception:
                st.caption("Chart unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
# SCAN PAGE
# ══════════════════════════════════════════════════════════════════════════════

def _render_scan_page(supabase, gemini_key: str):
    setups = _load_setups(supabase)
    if not setups:
        st.warning("No setups found. Go to the Manage Setups tab and create one first.")
        return

    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};
         border-left:3px solid {BLUE};border-radius:14px;
         padding:14px 20px;margin-bottom:18px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};margin-bottom:2px;">Smart Scan</div>
        <div style="font-size:12px;color:{T2};">
            Pick a setup, tune the live overrides (price / RSI / volume), hit Run Scan.
            Your rules filter the universe, then strict AI vision keeps ONLY the charts
            that truly match your reference setup.
        </div>
    </div>""", unsafe_allow_html=True)

    _section("Your Setups — Tap to Select")
    cols = st.columns(min(len(setups), 3))
    selected_key = st.session_state.get("selected_setup_id")

    for i, setup in enumerate(setups):
        with cols[i % 3]:
            is_sel = str(setup["id"]) == str(selected_key)
            bd = BLUE if is_sel else BORDER
            bg = "rgba(79,141,253,0.08)" if is_sel else DARK2
            sel_txt = "SELECTED" if is_sel else "TAP TO SELECT"
            sel_col = BLUE if is_sel else T2

            ref_img_obj = _decode_reference_image(setup)
            if ref_img_obj is not None:
                st.image(ref_img_obj, use_container_width=True)
            elif setup.get("reference_image_url"):
                st.image(setup["reference_image_url"], use_container_width=True)

            st.markdown(f"""
            <div style="background:{bg};border:1px solid {bd};
                 border-radius:12px;padding:14px;margin-bottom:8px;text-align:center;">
                <div style="font-size:14px;font-weight:800;color:{IVORY};margin-bottom:6px;">{setup['name']}</div>
                <div style="font-size:10px;color:{T2};line-height:1.8;">{_filters_summary(setup)}</div>
                <div style="font-size:9px;letter-spacing:2px;color:{sel_col};
                     margin-top:8px;font-weight:700;">{sel_txt}</div>
            </div>""", unsafe_allow_html=True)

            if st.button("Select", key=f"sel_{setup['id']}", use_container_width=True):
                st.session_state["selected_setup_id"] = str(setup["id"])
                st.rerun()

    selected_setup = None
    if selected_key:
        selected_setup = next((s for s in setups if str(s["id"]) == str(selected_key)), None)
    if not selected_setup:
        st.info("Select a setup above to start scanning.")
        return

    # ── LIVE OVERRIDES — price range + RSI / volume (per-scan only) ───────────
    _section("Price Range — Pick Before Scanning")

    base_pmin = float(selected_setup.get("price_min") or 0)
    base_pmax = float(selected_setup.get("price_max") or 99999)

    PRESETS = {
        "Use setup's range": (base_pmin, base_pmax),
        "100 - 250":         (100, 250),
        "250 - 500":         (250, 500),
        "500 - 750":         (500, 750),
        "750 - 1000":        (750, 1000),
        "1000 - 1500":       (1000, 1500),
        "1500 - 2000":       (1500, 2000),
        "Custom":            None,
    }
    pcol1, pcol2, pcol3 = st.columns([2, 1, 1])
    with pcol1:
        preset = st.selectbox("Price band (Rs)", list(PRESETS.keys()),
                              key="price_preset",
                              help="Override the saved setup's price range just for this scan.")
    if preset == "Custom":
        with pcol2:
            ov_pmin = st.number_input("Min price (Rs)", 0.0, 99999.0,
                                      value=max(base_pmin, 0.0), step=10.0, key="ov_pmin")
        with pcol3:
            ov_pmax = st.number_input("Max price (Rs)", 0.0, 99999.0,
                                      value=min(base_pmax, 99999.0), step=10.0, key="ov_pmax")
    else:
        ov_pmin, ov_pmax = PRESETS[preset]
        with pcol2:
            st.metric("Min", f"Rs {ov_pmin:,.0f}")
        with pcol3:
            st.metric("Max", f"Rs {ov_pmax:,.0f}")

    if ov_pmin > ov_pmax:
        st.error("Min price is greater than Max price — fix the range before scanning.")

    _section("Quick Filters — Live Overrides (RSI / Volume)")
    st.caption("Temporarily tighten or loosen rules without editing your saved setup. "
               "Leave a control OFF to keep the setup's own value.")

    qcol1, qcol2 = st.columns(2)
    with qcol1:
        rsi_override_on = st.toggle("Override RSI range", value=False, key="rsi_ov_on")
        if rsi_override_on:
            ov_rsi_min, ov_rsi_max = st.slider(
                "RSI between", 0, 100,
                (int(float(selected_setup.get("rsi_min") or 0)),
                 int(float(selected_setup.get("rsi_max") or 100))),
                key="ov_rsi")
        else:
            ov_rsi_min = float(selected_setup.get("rsi_min") or 0)
            ov_rsi_max = float(selected_setup.get("rsi_max") or 100)
    with qcol2:
        vol_override_on = st.toggle("Override volume rule", value=False, key="vol_ov_on")
        if vol_override_on:
            ov_vol = st.slider("Min volume (x 20-day avg)", 0.0, 5.0,
                               float(selected_setup.get("volume_multiplier") or 0.0),
                               0.1, key="ov_vol")
        else:
            ov_vol = float(selected_setup.get("volume_multiplier") or 0.0)

    scan_setup = dict(selected_setup)
    scan_setup["price_min"] = float(ov_pmin)
    scan_setup["price_max"] = float(ov_pmax)
    scan_setup["rsi_min"]   = float(ov_rsi_min)
    scan_setup["rsi_max"]   = float(ov_rsi_max)
    scan_setup["volume_multiplier"] = float(ov_vol)

    badges = []
    if (ov_pmin, ov_pmax) != (base_pmin, base_pmax):
        badges.append(f"Price {ov_pmin:,.0f}-{ov_pmax:,.0f}")
    if rsi_override_on:
        badges.append(f"RSI {ov_rsi_min:.0f}-{ov_rsi_max:.0f}")
    if vol_override_on and ov_vol > 0:
        badges.append(f"Vol ≥ {ov_vol:.1f}x")
    if badges:
        chips = "".join(
            f"<span style='display:inline-block;background:rgba(79,141,253,0.12);"
            f"border:1px solid rgba(79,141,253,0.4);color:{BLUE};font-size:11px;"
            f"font-weight:700;border-radius:20px;padding:3px 10px;margin:2px 4px 2px 0;'>{b}</span>"
            for b in badges)
        st.markdown(f"<div style='margin:6px 0 2px;'><span style='font-size:11px;color:{T2};'>"
                    f"Active overrides for this scan: </span>{chips}</div>",
                    unsafe_allow_html=True)

    _section(f"Scan With: {selected_setup['name']}")
    c1, c2 = st.columns([2, 1])
    with c1:
        universe_opt = st.selectbox(
            "Scan Universe",
            ["ALL NSE Stocks (~2000, slower)", "Liquid NSE (~180, fast)",
             "Your Watchlist", "Arka Watchlist"],
            key="scan_universe")
    with c2:
        max_ai = st.number_input("Max AI Comparisons", 3, 25, 12, 1, key="scan_max_ai",
                                 help="Top N candidates sent to Gemini Vision after the rules filter")

    if universe_opt.startswith("ALL"):
        with st.expander("NSE universe source & refresh", expanded=False):
            st.caption("Tries a live download from NSE first, falls back to a cached copy, then a "
                       "small built-in list. Upload the CSV yourself if NSE keeps blocking requests.")
            info = get_full_nse_universe(supabase)
            st.markdown(f"**Current source:** {info['source']}  ·  **{info['count']} symbols**")
            ref_col1, ref_col2 = st.columns([1, 1])
            with ref_col1:
                if st.button("🔄 Force refresh from NSE", use_container_width=True):
                    get_full_nse_universe.clear()
                    st.session_state.pop("manual_universe", None)
                    st.rerun()
                if st.session_state.get("manual_universe"):
                    if st.button("Clear manual upload, use auto source", use_container_width=True):
                        st.session_state.pop("manual_universe", None)
                        st.rerun()
            with ref_col2:
                uploaded_universe_csv = st.file_uploader(
                    "Upload EQUITY_L.csv manually", type=["csv"], key="universe_csv_upload",
                    help="Download nsearchives.nseindia.com/content/equities/EQUITY_L.csv in your "
                         "own browser if the live fetch above keeps failing, then upload it here.")
                if uploaded_universe_csv is not None:
                    parsed_syms = _parse_universe_csv_bytes(uploaded_universe_csv.read())
                    if len(parsed_syms) > 500:
                        st.session_state["manual_universe"] = parsed_syms
                        _cache_universe_to_supabase(supabase, parsed_syms)
                        st.success(f"Loaded {len(parsed_syms)} symbols from your upload.")
                    else:
                        st.error(f"Only found {len(parsed_syms)} symbols in that file — check it's the right CSV.")

    # Strictness control: only show matches at/above this score.
    strict_min = st.slider("Match strictness (hide anything below this score)",
                           0, 10, MIN_SIMILARITY_FLOOR, 1, key="strict_min",
                           help="6 = balanced. 8 = only near-identical setups. "
                                "Set to 0 to see everything.")

    if universe_opt.startswith("ALL"):
        manual = st.session_state.get("manual_universe")
        if manual:
            universe = manual
            st.caption(f"📄 Using your manually uploaded list · {len(universe)} stocks")
        else:
            info = get_full_nse_universe(supabase)
            universe = info["symbols"]
            st.caption(f"{info['source']} · {info['count']} stocks · scanned in one fast parallel pass")
    elif universe_opt.startswith("Liquid"):
        universe = NSE_UNIVERSE
    elif universe_opt == "Your Watchlist":
        universe = st.session_state.get("watchlist", [])
        if not universe:
            st.warning("Upload your watchlist in Scanner first.")
            return
    else:
        universe = st.session_state.get("admin_watchlist", [])
        if not universe:
            st.warning("Arka Watchlist not available yet.")
            return

    if not gemini_key:
        st.warning("GEMINI_KEY not found in secrets — AI vision comparison will be skipped, "
                   "so strict matching cannot run. Results will be rules-only.")
    elif not (HAS_GEMINI and HAS_PYDANTIC and HAS_PIL):
        missing = []
        if not HAS_GEMINI: missing.append("google-genai")
        if not HAS_PYDANTIC: missing.append("pydantic")
        if not HAS_PIL: missing.append("Pillow")
        st.warning(f"Missing package(s) in this environment: {', '.join(missing)} — add them to "
                   f"requirements.txt to enable strict AI vision matching. Results will be rules-only.")

    run_disabled = ov_pmin > ov_pmax
    if st.button("Run Scan", type="primary", use_container_width=True,
                 key="run_scan", disabled=run_disabled):
        for k in ("scan_math_results", "scan_ai_results", "scan_strict_min"):
            st.session_state.pop(k, None)

        prog = st.progress(0.0)
        stat = st.empty()

        def _prog(pct, msg):
            prog.progress(min(float(pct), 1.0))
            stat.markdown(f"**{msg}**")

        # ── Single fast parallel scan (price filter built into rules) ────
        _prog(0.05, f"Scanning {len(universe)} NSE stocks in one parallel pass...")
        shortlist, failed = run_math_scan(universe, scan_setup, _prog)

        if not shortlist:
            prog.progress(1.0); stat.empty()
            st.warning("No stocks passed your rules. Loosen the live overrides or edit the setup.")
            if failed:
                with st.expander(f"{len(failed)} symbols had no data"):
                    st.write(", ".join(failed[:60]))
            st.stop()

        st.session_state["scan_math_results"] = shortlist
        st.session_state["scan_strict_min"]   = int(strict_min)
        stat.markdown(f"Rules filter: **{len(shortlist)} candidates** from {len(universe)} scanned")

        # ── Strict AI vision comparison ──────────────────────────────────
        if gemini_key and HAS_GEMINI and HAS_PYDANTIC and HAS_PIL:
            ai_prog = st.progress(0.0)
            ai_stat = st.empty()

            def _ai_prog(pct, msg):
                ai_prog.progress(min(float(pct), 1.0))
                ai_stat.markdown(f"**{msg}**")

            ai_results = run_ai_audit(shortlist, scan_setup, gemini_key,
                                      int(max_ai), _ai_prog)
            ai_prog.empty(); ai_stat.empty()
            st.session_state["scan_ai_results"] = ai_results
            kept = sum(1 for r in ai_results if r.get("score", 0) >= strict_min)
            st.success(f"Vision comparison complete — {len(ai_results)} compared, "
                       f"{kept} truly match your setup (score ≥ {strict_min}/10).")
        prog.empty(); stat.empty()
        time.sleep(0.3)
        st.rerun()

    math_results = st.session_state.get("scan_math_results")
    ai_results   = st.session_state.get("scan_ai_results")
    used_strict  = st.session_state.get("scan_strict_min", MIN_SIMILARITY_FLOOR)
    if math_results is None:
        return

    _section("Scan Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Universe Selected", len(universe))
    m2.metric("Passed Your Rules", len(math_results))
    m3.metric("AI Compared", len(ai_results) if ai_results else "—")
    if ai_results:
        kept = sum(1 for r in ai_results if r.get("score", 0) >= used_strict)
        m4.metric(f"True Matches (≥{used_strict})", kept)
    else:
        m4.metric("AI Status", "Skipped")

    if ai_results:
        unverified = sum(1 for r in ai_results if not r.get("has_reference", True))
        if unverified:
            st.caption(f"⚠ {unverified} of {len(ai_results)} compared had no reference image on "
                       f"file, so they were capped low and marked TEXT-ONLY rather than treated "
                       f"as verified visual matches.")

        _section("Pattern Match Results")

        fcol1, fcol2, fcol3 = st.columns([1.4, 1.4, 1.4])
        with fcol1:
            min_sim = st.slider("Min similarity", 0, 10, int(used_strict), 1, key="min_sim",
                                help="Hide any AI match scoring below this. "
                                     "8+ = only near-identical setups.")
        with fcol2:
            sort_opt = st.selectbox("Sort by",
                ["Similarity", "RSI (oversold first)", "Volume Ratio", "% Change"],
                key="sort_results")
        with fcol3:
            filt_v = st.radio("Show",
                ["All", "Strong", "Partial", "No Match"],
                horizontal=True, key="filt_verdict")

        ordered = [r for r in ai_results if r.get("score", 0) >= min_sim]

        if sort_opt == "Similarity":        ordered.sort(key=lambda x: x.get("score", 0), reverse=True)
        elif "RSI" in sort_opt:             ordered.sort(key=lambda x: x.get("rsi", 50))
        elif "Volume" in sort_opt:          ordered.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)
        else:                               ordered.sort(key=lambda x: x.get("chg_pct", 0), reverse=True)

        if filt_v == "Strong":      ordered = [r for r in ordered if r.get("verdict") == "STRONG MATCH"]
        elif filt_v == "Partial":   ordered = [r for r in ordered if r.get("verdict") == "PARTIAL MATCH"]
        elif filt_v == "No Match":  ordered = [r for r in ordered if r.get("verdict") == "NO MATCH"]

        hidden = len(ai_results) - len(ordered)
        st.caption(f"Showing {len(ordered)} of {len(ai_results)} — only setups scoring "
                   f"≥ {min_sim}/10 are shown ({hidden} weaker ones hidden).")

        if not ordered:
            st.info("No charts matched your setup closely enough. "
                    "Lower 'Min similarity', or your setup may be rare in this price band.")
        else:
            for res in ordered:
                _render_result_card(res, selected_setup)

    _section(f"Rules Shortlist ({len(math_results)} stocks)")
    rows = []
    for r in math_results:
        chg = r["chg_pct"]
        rows.append({
            "Symbol": r["symbol"],
            "Price": f"Rs {r['close']:,.2f}",
            "Chg %": f"{'▲' if chg>=0 else '▼'} {abs(chg):.2f}%",
            "RSI": f"{r['rsi']:.0f}",
            "Vol Ratio": f"{r['vol_ratio']:.2f}x",
            "5D ROC": f"{r['roc_5']:.1f}%",
            "ATR %": f"{r['atr_pct']:.2f}%",
            "PDH": f"Rs {r['pdh']:,.2f}",
            "PDL": f"Rs {r['pdl']:,.2f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True,
                 hide_index=True, height=320)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def render_smart_scanner(supabase):
    """Call this from app.py when page == 'smart_scan'."""
    gemini_key = st.secrets.get("GEMINI_KEY", "")

    scan_tab, setup_tab = st.tabs(["Run Scan", "Manage Setups"])
    with scan_tab:
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        _render_scan_page(supabase, gemini_key)
    with setup_tab:
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        _render_setup_manager(supabase, gemini_key)
