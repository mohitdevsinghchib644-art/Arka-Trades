"""
smart_scan_page.py  —  Arka Trades Smart Screener (Spyder-X style)
====================================================================
How it works:
  1. SAVE A SETUP   : upload a reference chart image + describe the setup in
                      PLAIN ENGLISH ("similar to this image, price 100-1000,
                      RSI above 55, volume spike..."). Gemini parses your text
                      into math filters automatically — no sliders needed.
  2. TAP & SCAN     : pick a setup card, hit Run Scan. Fast pandas engine
                      filters the NSE universe with your parsed rules.
  3. AI VISION      : Gemini compares every shortlisted chart against your
                      reference image and ranks by pattern similarity.

Supabase table 'scan_setups' — same columns as before, no migration needed.
"""

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import base64, io, json, time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

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

# ── Theme (ChartX) ───────────────────────────────────────────────────────────
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

MODEL_NAME = "gemini-2.5-flash"

# ── NSE Universe ─────────────────────────────────────────────────────────────
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
# NATURAL LANGUAGE → FILTERS  (the Spyder-X magic)
# ══════════════════════════════════════════════════════════════════════════════

_PARSE_PROMPT = """You are a trading-rule parser. The user describes a stock setup
in plain English. Extract any NUMERIC / TECHNICAL conditions into this exact JSON.
Use defaults when a condition is not mentioned. Return ONLY the JSON, no markdown.

{
  "price_min": 0,
  "price_max": 99999,
  "rsi_min": 0,
  "rsi_max": 100,
  "volume_multiplier": 0,
  "roc_min": -999,
  "require_above_sma20": false,
  "require_above_sma50": false,
  "require_below_sma20": false,
  "require_breakout": false
}

Mapping hints:
- "price between 100 and 1000" -> price_min 100, price_max 1000
- "RSI above 55" -> rsi_min 55 | "RSI below 40 / oversold" -> rsi_max 40
- "volume spike / high volume / 2x volume" -> volume_multiplier 1.5 (or stated number)
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
    if not text.strip() or not HAS_GEMINI or not gemini_key:
        return defaults
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(MODEL_NAME)
        resp  = model.generate_content(_PARSE_PROMPT + text.strip())
        raw   = resp.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        out = dict(defaults)
        for k in defaults:
            if k in parsed and parsed[k] is not None:
                out[k] = bool(parsed[k]) if isinstance(defaults[k], bool) else float(parsed[k])
        return out
    except Exception:
        return defaults


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
    try:
        sid = data.pop("id", None)
        if sid:
            supabase.table("scan_setups").update(data).eq("id", sid).execute()
        else:
            supabase.table("scan_setups").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Save error: {e}")
        return False


def _delete_setup(supabase, setup_id) -> bool:
    try:
        supabase.table("scan_setups").delete().eq("id", setup_id).execute()
        return True
    except Exception as e:
        st.error(f"Delete error: {e}")
        return False


def _upload_image(supabase, file_bytes: bytes, filename: str) -> str:
    try:
        path = f"setups/{filename}"
        supabase.storage.from_("setup-images").upload(
            path, file_bytes,
            file_options={"content-type": "image/png", "upsert": "true"})
        return supabase.storage.from_("setup-images").get_public_url(path)
    except Exception as e:
        st.error(f"Image upload error: {e}")
        return ""


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


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_bulk(symbols_tuple: tuple, period: str = "60d") -> dict:
    symbols = list(symbols_tuple)
    ns_syms = [s + ".NS" for s in symbols]
    results = {}
    BATCH   = 150
    for start in range(0, len(ns_syms), BATCH):
        batch_ns    = ns_syms[start:start + BATCH]
        batch_plain = symbols[start:start + BATCH]
        if len(batch_ns) == 1:
            try:
                df = yf.download(batch_ns[0], period=period, interval="1d",
                                 auto_adjust=True, progress=False)
                if not df.empty and len(df) >= 20:
                    results[batch_plain[0]] = df
            except Exception:
                pass
            continue
        try:
            raw = yf.download(batch_ns, period=period, interval="1d",
                              auto_adjust=True, progress=False, threads=True)
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                available = raw.columns.get_level_values(1).unique().tolist()
                for sym, ns in zip(batch_plain, batch_ns):
                    if ns not in available:
                        continue
                    try:
                        df = raw.xs(ns, level=1, axis=1).dropna(how="all")
                        if not df.empty and len(df) >= 20 and "Close" in df.columns:
                            results[sym] = df
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(0.3)
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
    if progress_cb:
        progress_cb(0.0, f"Downloading data for {len(symbols)} stocks...")
    data_dict = _fetch_bulk(tuple(symbols), "60d")
    if progress_cb:
        progress_cb(0.35, f"Data ready for {len(data_dict)} stocks — applying your rules...")
    shortlist = []
    total = len(data_dict) or 1
    for i, (sym, df) in enumerate(data_dict.items()):
        try:
            result = _apply_filter(_calculate_indicators(df), setup, sym)
            if result:
                shortlist.append(result)
        except Exception:
            pass
        if progress_cb and i % 15 == 0:
            progress_cb(0.35 + 0.55 * (i / total), f"Scanning... {i}/{total}")
    failed = [s for s in symbols if s not in set(data_dict.keys())]
    if progress_cb:
        progress_cb(0.95, f"Rules filter done — {len(shortlist)} candidates")
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
    fig.savefig(buf, format="png", dpi=130, facecolor=DARK, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI VISION — PATTERN SIMILARITY AUDIT
# ══════════════════════════════════════════════════════════════════════════════

_PROMPT_TEMPLATE = """You are a professional technical analyst for NSE Indian equities.

You are given:
1. A REFERENCE PATTERN IMAGE — the user's ideal setup (if provided above).
2. A live 60-day daily candlestick chart for: {symbol}
   (Top panel: candles + SMA-20 blue + SMA-50 purple. Bottom: volume + 20-day avg.)

USER'S SETUP DESCRIPTION:
{visual_rules}

TASK: How closely does the live chart's CURRENT structure (the most recent
candles matter most) match the reference pattern and description?
Judge: price structure, trend shape, consolidation/base shape, breakout
position, volume behaviour. Ignore colors, watermark, timeframe labels.

Respond in EXACTLY this format (one value per line):

VERDICT: STRONG MATCH | PARTIAL MATCH | NO MATCH
SIMILARITY: [0-10]
PATTERN: [pattern name you see on the live chart]
KEY_FINDING: [one sentence — strongest similarity or mismatch vs the reference]
VISUAL_ANALYSIS: [2-3 sentences: structure, key levels, how it compares to reference]
RISK: [main risk visible on the live chart]
ACTION: [specific note e.g. Entry above X, stop Y — or "No trade"]

Be direct. Base everything ONLY on the charts."""


def _audit_one(symbol: str, chart_bytes: bytes, visual_rules: str,
               ref_image_url: str, gemini_key: str) -> dict:
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(MODEL_NAME)
        prompt = _PROMPT_TEMPLATE.format(
            symbol=symbol,
            visual_rules=visual_rules.strip() or "General strong technical setup.")

        content = []
        if ref_image_url and HAS_PIL and HAS_REQUESTS:
            try:
                r = _requests.get(ref_image_url, timeout=8)
                if r.status_code == 200:
                    ref_img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
                    content.append("REFERENCE PATTERN IMAGE (the user's ideal setup):")
                    content.append(ref_img)
            except Exception:
                pass

        if HAS_PIL:
            content.append(f"LIVE CHART for {symbol}:")
            content.append(PILImage.open(io.BytesIO(chart_bytes)).convert("RGB"))
        else:
            content.append({"mime_type": "image/png",
                            "data": base64.b64encode(chart_bytes).decode("utf-8")})
        content.append(prompt)

        response = model.generate_content(content)
        return _parse_audit(symbol, response.text.strip())
    except Exception as exc:
        return {"symbol": symbol, "verdict": "ERROR", "score": 0,
                "key_finding": str(exc)[:120], "pattern": "N/A",
                "visual_analysis": "", "risk": "", "action": "", "raw": str(exc)}


def _parse_audit(symbol: str, text: str) -> dict:
    result = dict(symbol=symbol, verdict="UNKNOWN", score=5.0,
                  pattern="N/A", key_finding="", visual_analysis="",
                  risk="", action="", raw=text)
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().upper(), val.strip()
        if key == "VERDICT":
            v = val.upper()
            if "STRONG"  in v: result["verdict"] = "STRONG MATCH"
            elif "PARTIAL" in v: result["verdict"] = "PARTIAL MATCH"
            elif "NO"    in v: result["verdict"] = "NO MATCH"
        elif key in ("SIMILARITY", "SCORE"):
            try: result["score"] = float(val.split("/")[0])
            except: pass
        elif key == "PATTERN":         result["pattern"] = val
        elif key == "KEY_FINDING":     result["key_finding"] = val
        elif key == "VISUAL_ANALYSIS": result["visual_analysis"] = val
        elif key == "RISK":            result["risk"] = val
        elif key == "ACTION":          result["action"] = val
    return result


def run_ai_audit(candidates, setup, gemini_key, max_stocks=15, progress_cb=None):
    top = candidates[:max_stocks]
    visual_rules = setup.get("visual_rules", "")
    ref_url = setup.get("reference_image_url", "") or ""
    results = []

    def _process(candidate):
        sym = candidate["symbol"]
        try:
            chart_bytes = _make_chart_image(sym, candidate["df"])
            audit = _audit_one(sym, chart_bytes, visual_rules, ref_url, gemini_key)
        except Exception as exc:
            audit = {"symbol": sym, "verdict": "ERROR", "score": 0,
                     "key_finding": str(exc)[:120], "pattern": "N/A",
                     "visual_analysis": "", "risk": "", "action": "", "raw": ""}
        return {**candidate, **audit}

    with ThreadPoolExecutor(max_workers=4) as ex:
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
# SETUP MANAGER  (image + plain English — AI extracts the rules)
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
            Upload a screenshot of your ideal setup. The AI will look for charts
            that visually match this pattern.</div>""", unsafe_allow_html=True)
        img_col1, img_col2 = st.columns([2, 1])
        with img_col1:
            uploaded_img = st.file_uploader("Upload setup image",
                                            type=["png", "jpg", "jpeg"],
                                            key=f"img_{prefix}",
                                            label_visibility="collapsed")
        with img_col2:
            if is_edit and existing.get("reference_image_url"):
                st.image(existing["reference_image_url"], width=140)
                st.caption("Current image")

        st.markdown(f"""
        <div style="font-size:12px;font-weight:700;letter-spacing:1px;color:{BLUE};
             text-transform:uppercase;margin:14px 0 6px;">Describe Your Setup — Plain English</div>
        <div style="font-size:12px;color:{T2};margin-bottom:8px;">
            Write everything in one place: the pattern AND any number rules.
            The AI automatically extracts price, RSI, volume and trend filters
            from your sentence — no sliders needed.</div>""", unsafe_allow_html=True)
        visual_rules = st.text_area(
            "Setup description", label_visibility="collapsed",
            value=existing.get("visual_rules", "") if is_edit else "",
            height=150, key=f"vr_{prefix}",
            placeholder=("Example:\n"
                         "Find stocks that look like this image — a tight flag after a strong "
                         "up move. Price should be between 100 and 1000. RSI above 55. "
                         "Volume at least 1.5x average. Stock must be above the 20 SMA "
                         "and breaking out above the previous day high."))

        submitted = st.form_submit_button(btn_lbl, use_container_width=True, type="primary")

        if submitted:
            if not name.strip():
                st.error("Setup Name is required.")
                return
            if not visual_rules.strip() and not uploaded_img and not (is_edit and existing.get("reference_image_url")):
                st.error("Add a description or a reference image (ideally both).")
                return

            ref_url = existing.get("reference_image_url", "") if is_edit else ""
            if uploaded_img is not None:
                img_bytes = uploaded_img.read()
                filename  = f"{name.strip().replace(' ','_')}_{int(time.time())}.png"
                new_url   = _upload_image(supabase, img_bytes, filename)
                if new_url:
                    ref_url = new_url

            with st.spinner("AI is reading your rules..."):
                filters = parse_rules_with_ai(visual_rules, gemini_key)

            payload = dict(
                name=name.strip(),
                description=_filters_summary(filters)[:120],
                reference_image_url=ref_url,
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
            image for visual pattern matching during scans.
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
        with st.expander(f"{setup['name']}  ·  {setup.get('description','')}", expanded=False):
            img_col, form_col = st.columns([1, 3])
            with img_col:
                if setup.get("reference_image_url"):
                    st.image(setup["reference_image_url"], use_container_width=True)
                    st.caption("Reference pattern")
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
    vc, vbg, vbd = _verdict_colors(verdict)
    chg = res.get("chg_pct", 0)
    cc  = GREEN if chg >= 0 else RED
    arr = "▲" if chg >= 0 else "▼"

    label = f"{res['symbol']}  ·  {verdict}  ·  Similarity {score:.0f}/10  ·  {res.get('pattern','')}"
    with st.expander(label, expanded=(verdict == "STRONG MATCH")):
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

            if setup.get("reference_image_url"):
                rcol1, rcol2 = st.columns([1, 2])
                with rcol1:
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
            Pick a setup, hit Run Scan. Your rules filter the universe, then AI vision
            compares every shortlisted chart against your reference image.
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

            if setup.get("reference_image_url"):
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

    _section(f"Scan With: {selected_setup['name']}")
    c1, c2 = st.columns([2, 1])
    with c1:
        universe_opt = st.selectbox(
            "Scan Universe",
            ["Full NSE Universe", "Your Watchlist", "Arka Watchlist"],
            key="scan_universe")
    with c2:
        max_ai = st.number_input("Max AI Comparisons", 3, 25, 12, 1, key="scan_max_ai",
                                 help="Top N candidates sent to Gemini Vision after the rules filter")

    if universe_opt.startswith("Full"):
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
        st.warning("GEMINI_KEY not found in secrets — AI vision comparison will be skipped.")

    if st.button("Run Scan", type="primary", use_container_width=True, key="run_scan"):
        for k in ("scan_math_results", "scan_ai_results"):
            st.session_state.pop(k, None)

        prog = st.progress(0.0)
        stat = st.empty()

        def _prog(pct, msg):
            prog.progress(min(float(pct), 1.0))
            stat.markdown(f"**{msg}**")

        shortlist, failed = run_math_scan(universe, selected_setup, _prog)

        if not shortlist:
            prog.progress(1.0); stat.empty()
            st.warning("No stocks passed your rules. Edit the setup description to relax them.")
            if failed:
                with st.expander(f"{len(failed)} symbols had no data"):
                    st.write(", ".join(failed[:60]))
            st.stop()

        st.session_state["scan_math_results"] = shortlist
        stat.markdown(f"Rules filter: **{len(shortlist)} candidates** from {len(universe)} scanned")

        if gemini_key:
            ai_prog = st.progress(0.0)
            ai_stat = st.empty()

            def _ai_prog(pct, msg):
                ai_prog.progress(min(float(pct), 1.0))
                ai_stat.markdown(f"**{msg}**")

            ai_results = run_ai_audit(shortlist, selected_setup, gemini_key,
                                      int(max_ai), _ai_prog)
            ai_prog.empty(); ai_stat.empty()
            st.session_state["scan_ai_results"] = ai_results
            strong = sum(1 for r in ai_results if r.get("verdict") == "STRONG MATCH")
            st.success(f"Vision comparison complete — {len(ai_results)} charts compared, "
                       f"{strong} strong matches found.")
        prog.empty(); stat.empty()
        time.sleep(0.3)
        st.rerun()

    math_results = st.session_state.get("scan_math_results")
    ai_results   = st.session_state.get("scan_ai_results")
    if math_results is None:
        return

    _section("Scan Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Universe Scanned", len(universe))
    m2.metric("Passed Your Rules", len(math_results))
    m3.metric("AI Compared", len(ai_results) if ai_results else "—")
    if ai_results:
        strong = sum(1 for r in ai_results if r.get("verdict") == "STRONG MATCH")
        m4.metric("Strong Matches", strong)
    else:
        m4.metric("AI Status", "Skipped")

    if ai_results:
        _section("Pattern Match Results")
        sort_opt = st.selectbox("Sort by",
            ["Similarity", "RSI (oversold first)", "Volume Ratio", "% Change"],
            key="sort_results")
        filt_v = st.radio("Show",
            ["All", "Strong Match only", "Partial Match only", "No Match only"],
            horizontal=True, key="filt_verdict")

        ordered = ai_results[:]
        if sort_opt == "Similarity":        ordered.sort(key=lambda x: x.get("score", 0), reverse=True)
        elif "RSI" in sort_opt:             ordered.sort(key=lambda x: x.get("rsi", 50))
        elif "Volume" in sort_opt:          ordered.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)
        else:                               ordered.sort(key=lambda x: x.get("chg_pct", 0), reverse=True)

        if filt_v == "Strong Match only":   ordered = [r for r in ordered if r.get("verdict") == "STRONG MATCH"]
        elif filt_v == "Partial Match only":ordered = [r for r in ordered if r.get("verdict") == "PARTIAL MATCH"]
        elif filt_v == "No Match only":     ordered = [r for r in ordered if r.get("verdict") == "NO MATCH"]

        if not ordered:
            st.info("No results match this filter.")
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
