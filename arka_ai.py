"""
arka_ai.py  —  Arka AI Trading Companion (v8 — Setup Intelligence)
=============================================================
Brain     : Gemini 2.5 Flash  (vision + text)
Memory    : Pinecone           (vector database)
Voice     : Web Speech API     (browser TTS)
Drawing   : Data-coordinate annotation engine (precise placement)
PDF Parse : pdfplumber

v7 CHANGES FROM v6 — and WHY each one exists:

  1. RULE RETRIEVAL — FIXED, not tuned.
     v6's build_rules_context() ran a top_k=8 Pinecone *similarity search*
     scored against a generic query string ("trading entry setup
     validation for {ticker}"). That means: if you had saved 15 setups,
     analysis could silently check against a random-ish 8 of them —
     whichever happened to score highest against that generic phrase —
     and drop the rest below the 0.5 threshold with no warning shown to
     you. For a tool whose entire job is "does this chart match EVERY
     rule I've saved," that's not an edge case, it's the core promise
     being broken silently.
     FIX: get_all_rules() now paginates Pinecone's list+fetch API to
     retrieve every stored vector's metadata, full stop — no similarity
     scoring, no top_k cutoff, no silent drops. This is deliberately
     "accuracy over token cost" per your explicit call — fine at your
     current rule count; if you ever cross ~100 long rules, the prompt
     will need chunking, and get_all_rules() returning a plain list
     makes that a small future change, not a rewrite.
     search_memory() (similarity search) is KEPT for the Memory Viewer's
     search box, where similarity search is the right tool — it's just
     no longer used to decide what the AI checks a chart against.

  2. PER-RULE CHECKLIST — structured, not prose.
     v6 asked Gemini for two loose string lists (rules_matched /
     rules_violated) with no guarantee every saved rule appears in
     either. v7's prompt explicitly enumerates every retrieved rule by
     ID and demands a verdict object for EACH ONE — PRESENT / NOT_PRESENT
     / UNCLEAR, plus a 0-100 confidence and a one-line reason. The UI
     renders this as a real checklist (see render_rule_checklist) so you
     can see at a glance which of your setups fired and which didn't —
     not just read a paragraph and trust it covered everything.

  3. MULTI-TIMEFRAME CONFLUENCE.
     v6 sent one chart (daily, 90 candles). v7 fetches BOTH daily and
     weekly OHLC + screenshots, sends both images plus both OHLC tables
     to Gemini in one call, and requires the response to state a
     confluence verdict (do the two timeframes agree). Annotations are
     still drawn on the daily chart only, since that's the one you trade
     off — the weekly chart displays alongside as context, unannotated.

  4. JSON ROBUSTNESS — one bounded repair pass, not a retry loop.
     v6 had a single json.loads() with only a "strip triple-backticks"
     defense; any malformed output produced a hard ERROR verdict with
     zero salvaged content. v7 tries strict parsing, then one repair
     pass (regex-extract the outermost {...} block and retry) before
     giving up. This is NOT the full schema-enforced auto-retry-with-
     re-prompt loop you declined — that would re-call Gemini and cost
     another request/latency round trip. This is a zero-cost local
     parse fallback: same single API call, just less likely to throw
     away a good response over a stray character.

  5. LAYOUT — Bloomberg multi-panel, single screen.
     v6 was a linear top-to-bottom scroll: input → quick picks → chart
     → notes → button → results below the fold. v7's Mode 1 is a fixed
     three-column workspace — chart+timeframe strip (left, widest),
     live rule checklist (center), verdict+detail (right) — all visible
     without scrolling past the fold on a normal desktop width, per your
     explicit "single screen, multiple panels side-by-side" call. Mode 2
     (training) keeps its tab layout since it's a sequential workflow
     (add rule -> teach chart -> upload PDF -> browse memory), not a
     dashboard you want all at once.

  UNCHANGED: Gemini client config, Pinecone index setup/embedding
  pipeline, PDF chunk extraction, voice synthesis, annotation pixel-math
  (_to_px, draw_annotations), Lightweight Charts JS panel. None of these
  were the problem.
"""

# V8 NOTE: This file intentionally contains ONE implementation of each public
# function. There is no appended override/shadowing section.

import re
import streamlit as st
import streamlit.components.v1 as components
import base64, io, json, time, traceback, tempfile, os, math
from datetime import datetime, timezone, timedelta

# ── Safe imports ────────────────────────────────────────────
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from pinecone import Pinecone, ServerlessSpec
    HAS_PINECONE = True
except ImportError:
    HAS_PINECONE = False

try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
    HAS_IMG_COORDS = True
except ImportError:
    HAS_IMG_COORDS = False

try:
    import yfinance as yf
    import pandas as pd
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

from io import BytesIO

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Keys from Streamlit Secrets ────────────────────────────
GEMINI_KEY   = st.secrets.get("GEMINI_KEY",   "")
PINECONE_KEY = st.secrets.get("PINECONE_KEY", "")
INDEX_NAME   = "arka-trading-rules"

# ── Colors (Bloomberg-style terminal theme) ────────────────
# Moved off the blue/purple ChartX palette from v6 toward the
# amber-on-black terminal identity used elsewhere in the app
# (research_page.py) so Arka AI reads as the same product, not a
# differently-branded module bolted on.
AMBER  = "#FFB000"
BLUE   = "#4F8DFD"
GREEN  = "#00C853"
RED    = "#FF3B30"
PURPLE = "#8B5CF6"
DARK   = "#000000"
DARK2  = "#0A0A0A"
DARK3  = "#111111"
BORDER = "#262626"
T2     = "#A8A8A0"
T3     = "#6B6B65"
IVORY  = "#E8E6E0"
NAVY   = "#101A33"
FONT   = "'IBM Plex Sans','Inter',sans-serif"
MONO   = "'IBM Plex Mono','JetBrains Mono',monospace"

# ══════════════════════════════════════════════════════════
# 1. GEMINI CLIENT
# ══════════════════════════════════════════════════════════

@st.cache_resource

# ══════════════════════════════════════════════════════════
# CLEAN V8 CHART / ANNOTATION FOUNDATION
# (shared deterministic rendering helpers from V7; no duplicate AI/UI functions)
# ══════════════════════════════════════════════════════════

def get_chart_screenshot(ticker: str, period: str = "3mo", interval: str = "1d"):
    """
    Build the candlestick chart AND capture exact axis geometry so that
    (candle index, price) can be converted to precise pixel positions later.
    Returns (PIL.Image, meta dict) or (None, None).

    NEW v7: `interval` param added so this can build a weekly chart
    (interval="1wk") for multi-timeframe confluence, not just daily.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from datetime import datetime, timedelta

    try:
        clean = ticker.upper().strip()
        if clean in ["NIFTY50", "NIFTY", "^NSEI"]:
            clean, yf_ticker = "NIFTY", "^NSEI"
        elif clean in ["BANKNIFTY", "^NSEBANK"]:
            clean, yf_ticker = "BANKNIFTY", "^NSEBANK"
        elif clean in ["SENSEX", "^BSESN"]:
            clean, yf_ticker = "SENSEX", "^BSESN"
        elif clean.endswith(".NS"):
            yf_ticker = clean; clean = clean.replace(".NS", "")
        elif clean.startswith("^"):
            yf_ticker = clean; clean = clean[1:]
        else:
            yf_ticker = clean + ".NS"

        end = datetime.today()
        lookback_days = 730 if interval == "1wk" else 150
        start = end - timedelta(days=lookback_days)

        hist = yf.Ticker(yf_ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=interval, auto_adjust=True, raise_errors=False
        )

        if hist is None or hist.empty:
            st.error(f"No data for {ticker} ({interval})")
            return None, None

        tail_n = 90
        hist = hist.tail(tail_n).copy()
        hist = hist[hist.index.notnull()]
        n    = len(hist)

        DPI   = 110
        fig_w = max(13, n * 0.16)
        fig   = plt.figure(figsize=(fig_w, 5), dpi=DPI, facecolor=DARK)
        ax    = fig.add_subplot(111)
        ax.set_facecolor(DARK)

        for spine in ax.spines.values():
            spine.set_color(BORDER)
        ax.tick_params(colors=T2, labelsize=8)

        candle_w = 0.6
        for i, (_, row) in enumerate(hist.iterrows()):
            o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
            color = GREEN if c >= o else RED
            ax.plot([i, i], [l, h], color=color, linewidth=0.9, zorder=1)
            rect = mpatches.Rectangle(
                (i - candle_w/2, min(o, c)), candle_w,
                max(abs(c - o), (h - l) * 0.01),
                facecolor=color, edgecolor=color, linewidth=0, zorder=2)
            ax.add_patch(rect)

        step = max(1, n // 10)
        positions = list(range(0, n, step))
        if (n - 1) not in positions:
            positions.append(n - 1)
        ax.set_xticks(positions)
        date_fmt = "%b %Y" if interval == "1wk" else "%d %b"
        ax.set_xticklabels([hist.index[i].strftime(date_fmt) for i in positions],
                           rotation=45, ha="right", fontsize=8)
        ax.set_xlim(-1, n)

        price_min = float(hist["Low"].min())
        price_max = float(hist["High"].max())
        pad = (price_max - price_min) * 0.06
        ax.set_ylim(price_min - pad, price_max + pad)

        last_date = hist.index[-1]
        date_str  = last_date.strftime("%d %b %Y") if hasattr(last_date, "strftime") else str(last_date)[:10]
        tf_label = "Weekly" if interval == "1wk" else "Daily"
        ax.set_title(f"{clean}  ·  NSE {tf_label}  ·  {date_str}",
                     color=AMBER, fontsize=11, fontweight="bold",
                     fontfamily="monospace", pad=10)
        ax.grid(axis="y", color=BORDER, linewidth=0.5, linestyle="--", alpha=0.8)
        ax.grid(axis="x", color=BORDER, linewidth=0.3, linestyle="--", alpha=0.4)
        plt.tight_layout(pad=1.2)

        # CRITICAL: draw the canvas, then capture exact axis geometry.
        # No bbox_inches='tight' — that would invalidate the transform.
        fig.canvas.draw()
        bbox = ax.get_window_extent()
        img_w, img_h = fig.canvas.get_width_height()

        meta = {
            "xlim": list(ax.get_xlim()),
            "ylim": list(ax.get_ylim()),
            "px_left":   float(bbox.x0),
            "px_right":  float(bbox.x1),
            "px_top":    float(img_h - bbox.y1),
            "px_bottom": float(img_h - bbox.y0),
            "img_w": img_w, "img_h": img_h, "n": n,
            "interval": interval,
            "ohlc": [
                {"i": i,
                 "date": idx.strftime("%Y-%m-%d"),
                 "o": round(float(r["Open"]), 2),
                 "h": round(float(r["High"]), 2),
                 "l": round(float(r["Low"]), 2),
                 "c": round(float(r["Close"]), 2),
                 "v": int(r.get("Volume") or 0)}
                for i, (idx, r) in enumerate(hist.iterrows())
            ],
        }

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, facecolor=DARK)
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf).convert("RGB"), meta

    except Exception as e:
        st.error(f"Error fetching chart: {e}")
        traceback.print_exc()
        return None, None


def _to_px(meta: dict, candle_i: float, price: float):
    """Convert (candle index, price) -> exact pixel coordinates on the chart image."""
    x0, x1 = meta["xlim"]; y0, y1 = meta["ylim"]
    px = meta["px_left"] + (candle_i - x0) / (x1 - x0) * (meta["px_right"] - meta["px_left"])
    py = meta["px_top"]  + (y1 - price)   / (y1 - y0) * (meta["px_bottom"] - meta["px_top"])
    return px, py


def _hex_rgb(color: str, default=(255, 176, 0)):
    try:
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return default


def draw_annotations(img: Image.Image, meta: dict, analysis: dict) -> Image.Image:
    """Draw AI annotations (levels, zones, marks) at exact data positions.
    Applies to the DAILY chart only — see module docstring."""
    img  = img.copy().convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    ann  = analysis.get("annotations", {}) or {}

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 13)
    except Exception:
        font = ImageFont.load_default()

    L, R = meta["px_left"], meta["px_right"]

    # Horizontal levels (dashed)
    for lv in ann.get("levels", []):
        try:
            price = float(lv["price"])
            r, g, b = _hex_rgb(lv.get("color", AMBER))
            _, py = _to_px(meta, 0, price)
            x = L
            while x < R:
                draw.line([x, py, min(x + 9, R), py], fill=(r, g, b, 230), width=2)
                x += 15
            label = f'{lv.get("label","Level")}  {price:,.2f}'
            draw.rectangle([L + 6, py - 20, L + 6 + 8 * len(label), py - 4],
                           fill=(r, g, b, 60))
            draw.text((L + 10, py - 19), label, fill=(r, g, b, 255), font=font)
        except Exception:
            pass

    # Zones (rectangles in candle/price space)
    for z in ann.get("zones", []):
        try:
            x1p, y1p = _to_px(meta, float(z["candle_from"]) - 0.4, float(z["price_top"]))
            x2p, y2p = _to_px(meta, float(z["candle_to"]) + 0.4, float(z["price_bottom"]))
            r, g, b = _hex_rgb(z.get("color", BLUE))
            draw.rectangle([x1p, y1p, x2p, y2p], outline=(r, g, b, 255),
                           width=2, fill=(r, g, b, 38))
            if z.get("label"):
                draw.text((x1p + 4, y1p - 18), z["label"], fill=(r, g, b, 255), font=font)
        except Exception:
            pass

    # Marks (triangles pointing at a candle/price)
    for m in ann.get("marks", []):
        try:
            px, py = _to_px(meta, float(m["candle"]), float(m["price"]))
            r, g, b = _hex_rgb(m.get("color", GREEN))
            s = 9
            if str(m.get("direction", "up")).lower() == "up":
                draw.polygon([(px, py - 4), (px - s, py + s + 4), (px + s, py + s + 4)],
                             fill=(r, g, b, 235))
                ty = py + s + 8
            else:
                draw.polygon([(px, py + 4), (px - s, py - s - 4), (px + s, py - s - 4)],
                             fill=(r, g, b, 235))
                ty = py - s - 24
            if m.get("label"):
                draw.text((px + 10, ty), m["label"], fill=(r, g, b, 255), font=font)
        except Exception:
            pass

    return img.convert("RGB")


# ══════════════════════════════════════════════════════════
# 3b. LIGHTWEIGHT CHARTS (interactive display)
# ══════════════════════════════════════════════════════════

def fetch_lw_ohlc(ticker: str, limit: int = 90, interval: str = "1d") -> list:
    from datetime import datetime, timedelta
    clean = ticker.upper().strip()
    sym_map = {"NIFTY50": "^NSEI", "NIFTY": "^NSEI",
               "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
    if clean in sym_map:
        yf_sym = sym_map[clean]
    elif clean.startswith("^") or clean.endswith(".NS"):
        yf_sym = clean
    else:
        yf_sym = clean + ".NS"
    try:
        end = datetime.today()
        lookback_days = 730 if interval == "1wk" else 150
        start = end - timedelta(days=lookback_days)
        hist = yf.Ticker(yf_sym).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=interval, auto_adjust=True)
        if hist is None or hist.empty:
            return []
        data = []
        for ts, row in hist.iterrows():
            try:
                data.append({
                    "time": ts.strftime("%Y-%m-%d"),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": round(float(row.get("Volume") or 0), 0)})
            except Exception:
                continue
        data.sort(key=lambda x: x["time"])
        return data[-limit:]
    except Exception:
        return []


def render_lw_chart(ticker: str, interval: str = "1d", height: int = 380):
    tf_label = "Weekly" if interval == "1wk" else "Daily"
    with st.spinner(f"Loading {tf_label.lower()} chart for {ticker.upper()}..."):
        data = fetch_lw_ohlc(ticker, interval=interval)
    if not data:
        st.error(f"{tf_label} chart unavailable for {ticker}.")
        return

    last = data[-1]
    prev = data[-2] if len(data) > 1 else last
    chg  = last["close"] - prev["close"]
    chg_pct   = (chg / prev["close"]) * 100 if prev["close"] else 0
    chg_color = GREEN if chg >= 0 else RED
    chg_sign  = "+" if chg >= 0 else ""
    json_data = json.dumps(data)
    chart_id  = f"chart_{interval}_{ticker.upper()}"

    html = f"""<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:{DARK}; font-family:'JetBrains Mono',monospace; overflow:hidden; }}
  #hdr {{ padding:8px 14px 4px; display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }}
  .tk {{ font-size:12px; font-weight:800; color:{AMBER}; letter-spacing:1.5px; }}
  .pr {{ font-size:18px; font-weight:700; color:{IVORY}; }}
  .ch {{ font-size:12px; font-weight:600; color:{chg_color}; }}
  #chart {{ width:100%; }}
</style></head>
<body>
<div id="hdr">
  <span class="tk">{ticker.upper()} &middot; {tf_label}</span>
  <span class="pr">&#8377;{last["close"]:,.2f}</span>
  <span class="ch">{chg_sign}{chg:.2f} ({chg_sign}{chg_pct:.2f}%)</span>
</div>
<div id="{chart_id}"></div>
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
<script>
const rawData = {json_data};
const H = {height};
const chart = LightweightCharts.createChart(document.getElementById('{chart_id}'), {{
  width: document.documentElement.clientWidth, height: H,
  layout: {{ background: {{ type:'solid', color:'{DARK}' }}, textColor:'{T2}', fontSize:10 }},
  grid: {{ vertLines: {{ color:'{BORDER}', style:1 }}, horzLines: {{ color:'{BORDER}', style:1 }} }},
  crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: {{ color:'{AMBER}55', labelBackgroundColor:'{NAVY}' }},
    horzLine: {{ color:'{AMBER}55', labelBackgroundColor:'{NAVY}' }} }},
  rightPriceScale: {{ borderColor:'{BORDER}' }},
  timeScale: {{ borderColor:'{BORDER}', timeVisible:true, fixLeftEdge:true, fixRightEdge:true }},
  handleScroll:true, handleScale:true,
}});
const volSeries = chart.addHistogramSeries({{ priceScaleId:'vol',
  scaleMargins: {{ top:0.82, bottom:0 }} }});
volSeries.priceScale().applyOptions({{ scaleMargins: {{ top:0.82, bottom:0 }} }});
volSeries.setData(rawData.map(d => ({{ time:d.time, value:d.volume,
  color: d.close >= d.open ? '{GREEN}2A' : '{RED}2A' }})));
const candleSeries = chart.addCandlestickSeries({{
  upColor:'{GREEN}', downColor:'{RED}', borderUpColor:'{GREEN}',
  borderDownColor:'{RED}', wickUpColor:'{GREEN}', wickDownColor:'{RED}' }});
candleSeries.setData(rawData);
chart.timeScale().fitContent();
new ResizeObserver(() => {{
  chart.resize(document.documentElement.clientWidth, H);
}}).observe(document.body);
</script></body></html>"""
    st.components.v1.html(html, height=height + 70, scrolling=False)


# ══════════════════════════════════════════════════════════
# 4. ANALYSIS ENGINE
# ══════════════════════════════════════════════════════════



def image_to_base64(img: Image.Image) -> str:
    """Encode a PIL image for the embedded Lightweight Charts component."""
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ══════════════════════════════════════════════════════════
# ARKA AI V8 — SINGLE IMPLEMENTATION
# ══════════════════════════════════════════════════════════

def get_gemini():
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    try:
        genai.configure(api_key=GEMINI_KEY)
        return genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction="""
You are Arka AI V8, a setup-analysis engine.

SOURCE OF TRUTH:
The ACTIVE USER SETUP is the only trading strategy you may evaluate. Do not
invent a strategy. Do not introduce RSI, MACD, moving averages, patterns,
volume rules, or other criteria unless the active setup explicitly uses them.
Contextual observations can be reported separately but MUST NOT change the
setup verdict.

NUMERICAL TRUTH:
Python supplies OHLC, derived indicators, swing levels and other market facts.
Use those supplied values. Never invent prices, dates, candle indices, volumes,
indicator values or levels. If evidence is missing, return UNCLEAR.

CHART MAPPING:
Never output pixel coordinates. Return exact candle index/date and exact price.
Python converts those data coordinates into pixels. This is mandatory.

SETUP UNDERSTANDING:
When processing a training PDF, inspect text, images, diagrams, tables and
examples. Extract testable conditions, trigger, confirmations, invalidation,
exclusions and valid/invalid examples. Preserve the author's terminology.
Ambiguity must be recorded rather than guessed.
"""
        )
    except Exception:
        return None


def _v8_extract_json(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    a, b = text.find("{"), text.rfind("}")
    return text[a:b+1] if a >= 0 and b > a else text


def _v8_call_json(prompt, images=None, uploaded_file=None):
    model = get_gemini()
    if model is None:
        raise RuntimeError("Gemini is not configured. Check GEMINI_KEY.")
    content = [prompt]
    if uploaded_file is not None:
        content.append(uploaded_file)
    for img in (images or []):
        content.append({"mime_type": "image/png", "data": image_to_base64(img)})
    response = model.generate_content(content)
    raw = (getattr(response, "text", "") or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return json.loads(_v8_extract_json(raw))


# ------------------------- Structured setups ---------------------
@st.cache_resource
def get_pinecone_index():
    if not HAS_PINECONE or not PINECONE_KEY:
        return None
    try:
        pc = Pinecone(api_key=PINECONE_KEY)
        existing = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in existing:
            pc.create_index(
                name=INDEX_NAME,
                dimension=768,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
        return pc.Index(INDEX_NAME)
    except Exception:
        return None


def get_embedding(text):
    if not HAS_GEMINI or not GEMINI_KEY or not text or not str(text).strip():
        return None
    try:
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=str(text).strip(),
        )
        emb = result.get("embedding", [])
        if len(emb) != 768 or not any(abs(v) > 1e-7 for v in emb):
            return None
        return emb
    except Exception:
        return None


def _v8_vector_ids(idx):
    try:
        ids = []
        for batch in idx.list(limit=100):
            ids.extend(batch)
        return ids
    except Exception:
        return []


def _v8_fetch_all_setup_vectors():
    idx = get_pinecone_index()
    if not idx:
        return []
    ids = _v8_vector_ids(idx)
    setups = []
    for start in range(0, len(ids), 200):
        try:
            fetched = idx.fetch(ids=ids[start:start+200])
            vectors = getattr(
                fetched,
                "vectors",
                fetched.get("vectors", {}) if isinstance(fetched, dict) else {},
            )
            for vector_id, vector in vectors.items():
                meta = vector.metadata if hasattr(vector, "metadata") else vector.get("metadata", {})
                if meta.get("memory_kind") != "setup":
                    continue
                try:
                    setup = json.loads(meta.get("setup_json", "{}"))
                except Exception:
                    continue
                setup["setup_id"] = meta.get("setup_id", vector_id)
                setup["active"] = bool(meta.get("active", False))
                setup["saved_at"] = meta.get("saved_at", "")
                setup["source_name"] = meta.get("source_name", setup.get("source_name", ""))
                setups.append(setup)
        except Exception:
            continue
    return sorted(setups, key=lambda x: x.get("saved_at", ""), reverse=True)


def get_all_setups():
    return _v8_fetch_all_setup_vectors()


def _v8_deactivate_other_setups(active_id, idx):
    for setup in _v8_fetch_all_setup_vectors():
        sid = setup.get("setup_id")
        if sid and sid != active_id:
            try:
                idx.update(id=sid, set_metadata={"active": False})
            except Exception:
                pass


def activate_setup(setup_id):
    idx = get_pinecone_index()
    if not idx or not setup_id:
        return False
    _v8_deactivate_other_setups(setup_id, idx)
    try:
        idx.update(id=setup_id, set_metadata={"active": True})
        st.session_state["v8_active_setup_id"] = setup_id
        return True
    except Exception:
        return False


def get_active_setup():
    setups = get_all_setups()
    selected = st.session_state.get("v8_active_setup_id")
    if selected:
        for setup in setups:
            if setup.get("setup_id") == selected:
                return setup
    for setup in setups:
        if setup.get("active"):
            st.session_state["v8_active_setup_id"] = setup.get("setup_id")
            return setup
    return None


def save_setup_to_memory(setup, source_text="", tags=None, activate=True):
    idx = get_pinecone_index()
    if not idx:
        st.error("Pinecone index not available. Check PINECONE_KEY.")
        return False
    name = str(setup.get("setup_name", "Unnamed Setup")).strip()
    if not name:
        return False
    try:
        setup = dict(setup)
        setup["setup_name"] = name
        setup_id = "setup_" + str(int(time.time() * 1000))
        compact = json.dumps(setup, ensure_ascii=False, separators=(",", ":"))
        embedding = get_embedding(
            "SETUP " + name + "\n" + str(setup.get("summary", "")) + "\n" + compact
        )
        if not embedding:
            st.error("Embedding failed. Check GEMINI_KEY.")
            return False
        if activate:
            _v8_deactivate_other_setups(setup_id, idx)
        metadata = {
            "memory_kind": "setup",
            "setup_id": setup_id,
            "setup_name": name,
            "setup_json": compact,
            "source_name": str(setup.get("source_name", "")),
            "source_text": str(source_text or "")[:12000],
            "tags": json.dumps(tags or []),
            "active": bool(activate),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "version": V8_VERSION,
        }
        idx.upsert(vectors=[{"id": setup_id, "values": embedding, "metadata": metadata}])
        if activate:
            st.session_state["v8_active_setup_id"] = setup_id
        return True
    except Exception as exc:
        st.error(f"Setup save error: {exc}")
        return False


def save_rule_to_memory(rule_type, rule_name, rule_text, tags=None):
    # Backward compatibility: V7 manual rule becomes a V8 single-condition setup.
    setup = {
        "setup_name": rule_name,
        "summary": rule_text,
        "conditions": [{
            "id": "R01", "name": rule_name, "type": "other", "required": True,
            "condition": rule_text, "evidence_hint": "Use supplied chart/data evidence."
        }],
        "trigger": {"description": rule_text, "type": "manual"},
        "confirmation": [], "invalidation": [], "exclusions": [], "examples": [],
        "confidence": "MEDIUM", "source_name": "manual",
    }
    return save_setup_to_memory(setup, source_text=rule_text, tags=tags, activate=True)


def search_memory(query, top_k=5):
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        emb = get_embedding(query)
        if not emb:
            return []
        result = idx.query(vector=emb, top_k=top_k, include_metadata=True)
        out = []
        for match in result.matches:
            meta = match.metadata or {}
            out.append({
                "id": match.id,
                "score": match.score,
                "rule_name": meta.get("setup_name", meta.get("rule_name", "")),
                "rule_text": meta.get("source_text", meta.get("rule_text", "")),
                "memory_kind": meta.get("memory_kind", "legacy"),
            })
        return out
    except Exception as exc:
        st.error(f"Search error: {exc}")
        return []


def build_setup_prompt(setup):
    return json.dumps(
        {k: v for k, v in setup.items() if k not in {"active", "saved_at"}},
        ensure_ascii=False,
        indent=2,
    )


# ------------------------- PDF understanding --------------------
def extract_pdf_text(pdf_file):
    if not HAS_PDF:
        return ""
    try:
        parts = []
        pdf_file.seek(0)
        with pdfplumber.open(pdf_file) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(f"[Page {page_number}]\n{text.strip()}")
        pdf_file.seek(0)
        return "\n\n".join(parts)
    except Exception:
        return ""


def understand_pdf_setup(pdf_file, filename="setup.pdf"):
    if not HAS_GEMINI or not GEMINI_KEY:
        return {"error": "Gemini is not configured. Check GEMINI_KEY."}
    tmp = None
    try:
        data = pdf_file.getvalue()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(data)
        tmp.flush()
        tmp.close()
        uploaded = genai.upload_file(
            path=tmp.name,
            mime_type="application/pdf",
            display_name=filename,
        )
        prompt = """
Read this trading-strategy PDF as a training document for Arka AI.
Understand the entire document, including prose, screenshots, diagrams, tables,
examples, arrows and annotations. Do not merely summarize it.

Convert it into a structured, testable trading setup. Preserve the strategy
author's terminology. Do not invent conditions. If something is ambiguous,
put it in ambiguities.

Return ONLY JSON:
{
  "setup_name":"...",
  "summary":"...",
  "market_context":"...",
  "conditions":[
    {"id":"R01","name":"...","type":"structure|price|volume|indicator|context|other","required":true,"condition":"testable condition","evidence_hint":"what proves it"}
  ],
  "trigger":{"description":"...","type":"..."},
  "confirmation":[{"id":"C01","description":"...","required":true}],
  "invalidation":[{"id":"I01","description":"...","price_logic":"..."}],
  "exclusions":["..."],
  "examples":[{"label":"VALID|INVALID|UNCLEAR","description":"...","source_page":1}],
  "source_pages":[1,2],
  "ambiguities":["..."],
  "confidence":"HIGH|MEDIUM|LOW"
}

Atomic conditions are preferred. Keep entry/trigger logic separate from
confirmation and invalidation. A generic indicator is NOT a rule unless the
source actually uses it.
"""
        result = _v8_call_json(prompt, uploaded_file=uploaded)
        result["source_name"] = filename
        return result
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass


# ------------------------- V8 deterministic engine ---------------
def _v8_df(meta):
    if not HAS_YFINANCE or not meta or not meta.get("ohlc"):
        return None
    try:
        frame = pd.DataFrame(meta["ohlc"])
        return frame.rename(columns={"o":"open", "h":"high", "l":"low", "c":"close", "v":"volume"})
    except Exception:
        return None


def calculate_market_state(meta):
    """Calculate market facts in Python. Gemini only interprets them."""
    frame = _v8_df(meta)
    if frame is None or len(frame) < 5:
        return {"status": "INSUFFICIENT_DATA"}
    out = {
        "status": "OK",
        "latest_date": str(frame.iloc[-1]["date"]),
        "close": float(frame.iloc[-1]["close"]),
    }
    for period in (20, 50, 100, 200):
        out[f"sma{period}"] = float(frame.close.rolling(period).mean().iloc[-1]) if len(frame) >= period else None
    delta = frame.close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    out["rsi14"] = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None
    tr = pd.concat([
        frame.high-frame.low,
        (frame.high-frame.close.shift()).abs(),
        (frame.low-frame.close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    out["atr14"] = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else None
    out["atr_pct"] = out["atr14"] / out["close"] * 100 if out.get("atr14") else None
    avg_volume = frame.volume.rolling(20).mean().iloc[-1]
    out["volume"] = float(frame.volume.iloc[-1])
    out["avg_volume20"] = float(avg_volume) if pd.notna(avg_volume) else None
    out["volume_ratio20"] = out["volume"] / out["avg_volume20"] if out.get("avg_volume20") else None
    out["pd_high"] = float(frame.high.iloc[-2]) if len(frame) > 1 else None
    out["pd_low"] = float(frame.low.iloc[-2]) if len(frame) > 1 else None
    out["recent_high20"] = float(frame.high.tail(20).max())
    out["recent_low20"] = float(frame.low.tail(20).min())
    if len(frame) >= 6:
        out["roc5_pct"] = (float(frame.close.iloc[-1]) / float(frame.close.iloc[-6]) - 1) * 100
    highs, lows = [], []
    for i in range(2, len(frame)-2):
        if frame.high.iloc[i] >= frame.high.iloc[i-2:i+3].max():
            highs.append(i)
        if frame.low.iloc[i] <= frame.low.iloc[i-2:i+3].min():
            lows.append(i)
    out["swing_highs"] = [{"i":i,"date":str(frame.date.iloc[i]),"price":float(frame.high.iloc[i])} for i in highs[-8:]]
    out["swing_lows"] = [{"i":i,"date":str(frame.date.iloc[i]),"price":float(frame.low.iloc[i])} for i in lows[-8:]]
    if out.get("sma20") and out.get("sma50"):
        out["ma_structure"] = (
            "BULLISH" if out["close"] > out["sma20"] > out["sma50"]
            else "BEARISH" if out["close"] < out["sma20"] < out["sma50"]
            else "MIXED"
        )
    return out


def _v8_relevant_market_evidence(setup, daily, weekly):
    joined = json.dumps(setup, ensure_ascii=False).lower()
    evidence = []
    # Only expose specialized calculations when the setup actually references them.
    if "volume" in joined or "turnover" in joined:
        evidence += [
            {"claim":"Latest volume","value":daily.get("volume"),"source":"daily"},
            {"claim":"20D volume ratio","value":daily.get("volume_ratio20"),"source":"daily"},
        ]
    if "rsi" in joined:
        evidence.append({"claim":"RSI14","value":daily.get("rsi14"),"source":"daily"})
    if any(x in joined for x in ["moving average","dma","sma","ema"]):
        for p in (20,50,100,200):
            evidence.append({"claim":f"SMA{p}","value":daily.get(f"sma{p}"),"source":"daily"})
    if any(x in joined for x in ["support","resistance","breakout","breakdown"]):
        evidence += [
            {"claim":"Previous day high","value":daily.get("pd_high"),"source":"daily"},
            {"claim":"Previous day low","value":daily.get("pd_low"),"source":"daily"},
            {"claim":"20-bar high","value":daily.get("recent_high20"),"source":"daily"},
            {"claim":"20-bar low","value":daily.get("recent_low20"),"source":"daily"},
            {"claim":"Recent swing highs","value":daily.get("swing_highs"),"source":"daily"},
            {"claim":"Recent swing lows","value":daily.get("swing_lows"),"source":"daily"},
        ]
    return evidence


def _v8_ohlc_table(meta):
    rows = (meta or {}).get("ohlc", [])
    return "i,date,open,high,low,close,volume\n" + "\n".join(
        f'{r["i"]},{r["date"]},{r["o"]},{r["h"]},{r["l"]},{r["c"]},{r["v"]}' for r in rows
    )


def _v8_valid_mark(mark, meta):
    try:
        candle = int(mark["candle"])
        price = float(mark["price"])
        n = int(meta.get("n", 0))
        lo, hi = meta.get("ylim", [-math.inf, math.inf])
        if candle < 0 or candle >= n or price < lo or price > hi:
            return None
        row = meta["ohlc"][candle]
        # A point must be close to that candle unless it is explicitly a derived level.
        candle_span = abs(float(row["h"]) - float(row["l"]))
        tolerance = max(candle_span * 1.5, (hi-lo) * 0.008)
        near_candle = any(abs(price-float(row[k])) <= tolerance for k in ("h","l","c"))
        if not near_candle and mark.get("type") not in {"level","breakout","support","resistance","invalidation"}:
            return None
        return {
            "candle": candle,
            "price": price,
            "direction": str(mark.get("direction", "up")),
            "label": str(mark.get("label", ""))[:80],
            "color": mark.get("color", GREEN),
        }
    except Exception:
        return None


def _v8_normalise_annotations(raw, meta):
    result = {"levels": [], "zones": [], "marks": []}
    if not isinstance(raw, dict):
        return result
    lo, hi = meta.get("ylim", [-math.inf, math.inf])
    for level in raw.get("levels", []):
        try:
            price = float(level["price"])
            if lo <= price <= hi:
                result["levels"].append({
                    "price": price,
                    "label": str(level.get("label", ""))[:80],
                    "color": level.get("color", AMBER),
                })
        except Exception:
            pass
    for mark in raw.get("marks", []):
        clean = _v8_valid_mark(mark, meta)
        if clean:
            result["marks"].append(clean)
    for zone in raw.get("zones", []):
        try:
            cf, ct = int(zone["candle_from"]), int(zone["candle_to"])
            top, bottom = float(zone["price_top"]), float(zone["price_bottom"])
            if 0 <= cf < ct < meta.get("n", 0) and lo <= bottom < top <= hi:
                result["zones"].append({
                    "candle_from": cf, "candle_to": ct,
                    "price_top": top, "price_bottom": bottom,
                    "label": str(zone.get("label", ""))[:80],
                    "color": zone.get("color", BLUE),
                })
        except Exception:
            pass
    return result


def analyze_chart(daily_img, daily_meta, weekly_img, weekly_meta, setup, user_note="", ticker=""):
    if not setup:
        return {
            "verdict":"ERROR", "score":0, "setup_state":"NO_SETUP",
            "voice_summary":"No active setup.",
            "detailed_analysis":"Train and activate a setup in Mode 2 first.",
            "rule_checks":[], "confluence":{"status":"INSUFFICIENT_DATA","score":0},
            "annotations":{"levels":[],"zones":[],"marks":[]}, "evidence":[], "invalidation":{}
        }
    try:
        daily_state = calculate_market_state(daily_meta)
        weekly_state = calculate_market_state(weekly_meta or {})
        relevant = _v8_relevant_market_evidence(setup, daily_state, weekly_state)
        prompt = f"""
ACTIVE SETUP — ONLY STRATEGY TO EVALUATE:
{build_setup_prompt(setup)}

DETERMINISTIC DAILY STATE:
{json.dumps(daily_state, ensure_ascii=False, default=str)}

DETERMINISTIC WEEKLY STATE:
{json.dumps(weekly_state, ensure_ascii=False, default=str)}

RELEVANT SETUP EVIDENCE:
{json.dumps(relevant, ensure_ascii=False, default=str)}

DAILY OHLC:
{_v8_ohlc_table(daily_meta)}

WEEKLY OHLC:
{_v8_ohlc_table(weekly_meta) if weekly_meta else 'UNAVAILABLE'}

USER NOTE: {user_note or 'None'}

TASK:
Evaluate ticker {ticker} ONLY against the active setup. Return exactly one
rule_check for every item in setup.conditions and setup.confirmation. Do not
add rules. PRESENT requires evidence. NOT_PRESENT requires contradictory or
missing required evidence. UNCLEAR means the supplied evidence cannot decide.

For annotations, use exact daily candle indices and prices from the supplied
daily table. Never output pixel coordinates. Annotate only evidence that matters
to the active setup. Prefer the exact breakout/trigger candle, setup zone,
resistance/support, and invalidation level when the setup defines them.

The confluence score is an EVIDENCE ALIGNMENT score, NOT a probability of
profit or success.

Return ONLY JSON:
{
 "verdict":"VALID|INVALID|FLAGGED",
 "score":0,
 "setup_state":"WATCH|FORMING|TRIGGERED|CONFIRMED|EXTENDED|INVALIDATED|READY|NOT_READY",
 "setup_summary":"...",
 "confluence":{
   "status":"ALIGNED|MIXED|CONFLICT|INSUFFICIENT_DATA",
   "score":0,
   "weekly_bias":"BULLISH|BEARISH|MIXED|UNKNOWN",
   "daily_bias":"BULLISH|BEARISH|MIXED|UNKNOWN",
   "structure":"ALIGNED|MIXED|CONFLICT|UNKNOWN",
   "evidence":["..."]
 },
 "market_state":{"trend":"...","momentum":"...","volume":"...","regime":"..."},
 "rule_checks":[
   {"rule_id":"R01","rule_name":"...","status":"PRESENT|NOT_PRESENT|UNCLEAR","confidence":0,"reason":"exact date/price/data evidence"}
 ],
 "invalidation":{"primary_level":null,"condition":"...","what_changes_view":"..."},
 "evidence":[
   {"claim":"...","data":"...","calculation":"...","conclusion":"...","date":"..."}
 ],
 "annotations":{
   "levels":[{"price":0,"label":"..."}],
   "zones":[{"candle_from":0,"candle_to":0,"price_top":0,"price_bottom":0,"label":"..."}],
   "marks":[{"candle":0,"price":0,"direction":"up","label":"...","type":"breakout"}]
 },
 "voice_summary":"...",
 "detailed_analysis":"..."
}
"""
        result = _v8_call_json(prompt, images=[daily_img] + ([weekly_img] if weekly_img else []))
        result["annotations"] = _v8_normalise_annotations(result.get("annotations", {}), daily_meta)
        result["deterministic"] = {
            "daily": daily_state,
            "weekly": weekly_state,
            "relevant_evidence": relevant,
        }
        return result
    except Exception as exc:
        return {
            "verdict":"ERROR", "score":0, "setup_state":"ERROR",
            "voice_summary":"Analysis failed.", "detailed_analysis":str(exc),
            "rule_checks":[], "confluence":{"status":"INSUFFICIENT_DATA","score":0},
            "annotations":{"levels":[],"zones":[],"marks":[]}, "evidence":[], "invalidation":{}
        }


# ------------------------- V8 PDF compatibility ------------------
def extract_pdf_rules(pdf_file):
    # Kept for imports from older code. V8 does NOT learn blind chunks.
    text = extract_pdf_text(pdf_file)
    return [text[i:i+1200] for i in range(0, len(text), 1200) if len(text[i:i+1200].strip()) > 50]


# ------------------------- V8 UI ---------------------------------
def _v8_escape(value):
    return _html.escape(str(value))


def verdict_badge(verdict, score, confluence=None, confluence_note=""):
    colors = {"VALID":GREEN, "INVALID":RED, "FLAGGED":AMBER, "ERROR":T2}
    color = colors.get(verdict, T2)
    try:
        width = max(0, min(100, int(float(score) / 10 * 100)))
    except Exception:
        width = 0
    cf = confluence if isinstance(confluence, dict) else {}
    status = cf.get("status", "INSUFFICIENT_DATA")
    cf_color = {"ALIGNED":GREEN, "MIXED":AMBER, "CONFLICT":RED, "INSUFFICIENT_DATA":T3}.get(status, T3)
    return f"""
<div style="background:{DARK2};border:1px solid {color};padding:14px;margin-bottom:10px;font-family:{FONT};">
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="background:{color};color:{DARK};font-family:{MONO};font-weight:800;font-size:12px;letter-spacing:1.5px;padding:5px 12px;">{_v8_escape(verdict)}</span>
    <span style="font-family:{MONO};font-size:18px;font-weight:700;color:{color};">{score}/10</span>
  </div>
  <div style="height:4px;background:{DARK3};margin-top:9px;"><div style="height:4px;background:{color};width:{width}%;"></div></div>
  <div style="margin-top:10px;border-top:1px solid {BORDER};padding-top:8px;font-family:{MONO};font-size:10px;">
    <span style="color:{T3};letter-spacing:1px;">MTF CONFLUENCE</span>
    <b style="color:{cf_color};margin-left:7px;">{_v8_escape(status)}</b>
  </div>
</div>"""


def render_confluence_panel(confluence):
    cf = confluence or {}
    status = cf.get("status", "INSUFFICIENT_DATA")
    color = {"ALIGNED":GREEN, "MIXED":AMBER, "CONFLICT":RED, "INSUFFICIENT_DATA":T3}.get(status, T3)
    rows = [
        ("WEEKLY", cf.get("weekly_bias", "UNKNOWN")),
        ("DAILY", cf.get("daily_bias", "UNKNOWN")),
        ("STRUCTURE", cf.get("structure", "UNKNOWN")),
        ("ALIGNMENT", f"{cf.get('score', 0)} / 100"),
    ]
    cells = "".join(
        f'<div style="display:flex;justify-content:space-between;border-bottom:1px solid {BORDER};padding:6px 0;font-family:{MONO};font-size:10px;"><span style="color:{T3};">{k}</span><b style="color:{IVORY};">{_v8_escape(v)}</b></div>'
        for k, v in rows
    )
    evidence = " ".join(str(x) for x in cf.get("evidence", [])[:3])
    return f"""
<div style="border:1px solid {BORDER};background:{DARK2};padding:11px;margin-bottom:10px;">
  <div style="font-family:{MONO};font-size:10px;letter-spacing:1px;color:{AMBER};">MULTI-TIMEFRAME CONFLUENCE</div>
  <div style="font-family:{MONO};font-size:13px;font-weight:800;color:{color};margin:5px 0 7px;">{_v8_escape(status)}</div>
  {cells}
  <div style="font-size:10px;color:{T2};line-height:1.5;margin-top:7px;">{_v8_escape(evidence)}</div>
</div>"""


def render_rule_checklist(rule_checks):
    if not rule_checks:
        st.markdown(f'<div style="border:1px dashed {BORDER};padding:16px;text-align:center;color:{T3};font-family:{MONO};font-size:10px;">NO RULE CHECKS RETURNED</div>', unsafe_allow_html=True)
        return
    styles = {
        "PRESENT": (GREEN, "✓ PRESENT"),
        "NOT_PRESENT": (RED, "✕ NOT PRESENT"),
        "UNCLEAR": (AMBER, "◐ UNCLEAR"),
    }
    rows = []
    for rule in rule_checks:
        color, label = styles.get(rule.get("status"), (T3, "UNKNOWN"))
        try:
            confidence = max(0, min(100, int(rule.get("confidence", 0))))
        except Exception:
            confidence = 0
        rows.append(f"""
<div style="border:1px solid {BORDER};border-left:3px solid {color};background:{DARK2};padding:9px 11px;margin-bottom:5px;">
 <div style="display:flex;justify-content:space-between;gap:8px;"><b style="font-size:11px;color:{IVORY};">{_v8_escape(rule.get('rule_name','Rule'))}</b><span style="font-family:{MONO};font-size:9px;color:{color};">{label}</span></div>
 <div style="font-size:10px;color:{T2};line-height:1.45;margin-top:5px;">{_v8_escape(rule.get('reason',''))}</div>
 <div style="height:2px;background:{DARK3};margin-top:5px;"><div style="height:2px;background:{color};width:{confidence}%;"></div></div>
</div>""")
    st.markdown("".join(rows), unsafe_allow_html=True)


def speak(text, rate=0.95, pitch=1.0):
    payload = json.dumps(str(text or "").replace("\n", " ")[:1200])
    st.markdown(f"""
<script>
(function() {{
 if (!window.speechSynthesis) return;
 window.speechSynthesis.cancel();
 var u=new SpeechSynthesisUtterance({payload});
 u.rate={rate}; u.pitch={pitch}; u.lang='en-IN';
 window.speechSynthesis.speak(u);
}})();
</script>""", unsafe_allow_html=True)


def _v8_setup_card(setup):
    color = GREEN if setup.get("active") else BORDER
    status = "ACTIVE" if setup.get("active") else "STORED"
    st.markdown(f"""
<div style="border:1px solid {color};background:{DARK2};padding:10px 12px;margin-bottom:6px;">
 <b style="color:{IVORY};">{_v8_escape(setup.get('setup_name','Unnamed'))}</b>
 <span style="float:right;font-family:{MONO};font-size:9px;color:{GREEN if setup.get('active') else T3};">{status}</span>
 <div style="font-size:10px;color:{T2};margin-top:4px;">{_v8_escape(str(setup.get('summary',''))[:260])}</div>
 <div style="font-family:{MONO};font-size:9px;color:{T3};margin-top:5px;">{len(setup.get('conditions',[]))} conditions · {len(setup.get('confirmation',[]))} confirmations · {len(setup.get('examples',[]))} examples · confidence {_v8_escape(setup.get('confidence',''))}</div>
</div>""", unsafe_allow_html=True)


def render_mode2():
    st.markdown(f"""
<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {GREEN};padding:12px 16px;margin-bottom:12px;">
 <div style="font-size:14px;font-weight:800;color:{IVORY};">Train AI — Setup Library</div>
 <div style="font-size:11px;color:{T2};margin-top:3px;">Import a strategy PDF, let Arka understand the complete setup, review the structured interpretation, then approve it. Only the active setup is used by Mode 1.</div>
</div>""", unsafe_allow_html=True)
    tabs = st.tabs(["SETUP LIBRARY", "IMPORT PDF", "MANUAL SETUP", "EXAMPLE CHART", "MEMORY"])
    setups = get_all_setups()
    active = get_active_setup()

    with tabs[0]:
        if not setups:
            st.info("No V8 setup exists yet. Import your strategy PDF first.")
        for setup in setups:
            _v8_setup_card(setup)
            c1, c2 = st.columns([1, 4])
            with c1:
                if st.button("ACTIVATE", key=f"v8_activate_{setup.get('setup_id')}"):
                    if activate_setup(setup.get("setup_id")):
                        st.success("Setup activated.")
                        st.rerun()
            with c2:
                with st.expander("VIEW STRUCTURED SETUP"):
                    st.json({k:v for k,v in setup.items() if k not in {"setup_id","active","saved_at"}})

    with tabs[1]:
        pdf = st.file_uploader("Trading strategy PDF", type=["pdf"], key="v8_pdf_upload")
        if pdf and st.button("UNDERSTAND PDF", type="primary", use_container_width=True, key="v8_understand_pdf"):
            with st.spinner("Arka is reading the complete PDF — text, diagrams, screenshots and examples..."):
                understood = understand_pdf_setup(pdf, pdf.name)
                source_text = extract_pdf_text(pdf)
            st.session_state["v8_pending_setup"] = understood
            st.session_state["v8_pending_source_text"] = source_text
        pending = st.session_state.get("v8_pending_setup")
        if pending:
            if pending.get("error"):
                st.error(pending["error"])
            else:
                st.markdown(f"""
<div style="border:1px solid {AMBER};background:{DARK2};padding:11px;margin-top:10px;">
 <b style="color:{IVORY};">{_v8_escape(pending.get('setup_name','Unnamed Setup'))}</b>
 <div style="font-size:10px;color:{T2};margin-top:4px;">{_v8_escape(pending.get('summary',''))}</div>
 <div style="font-family:{MONO};font-size:9px;color:{T3};margin-top:5px;">{len(pending.get('conditions',[]))} conditions · {len(pending.get('confirmation',[]))} confirmations · {len(pending.get('invalidation',[]))} invalidations · {len(pending.get('examples',[]))} examples · confidence {_v8_escape(pending.get('confidence',''))}</div>
</div>""", unsafe_allow_html=True)
                with st.expander("AI UNDERSTANDING — REVIEW BEFORE ACTIVATION", expanded=True):
                    st.json(pending)
                if pending.get("ambiguities"):
                    st.warning("Arka found ambiguities. Review them before activation: " + " | ".join(map(str, pending["ambiguities"])))
                if st.button("APPROVE & ACTIVATE SETUP", type="primary", use_container_width=True, key="v8_approve_setup"):
                    ok = save_setup_to_memory(
                        pending,
                        source_text=st.session_state.get("v8_pending_source_text", ""),
                        tags=["pdf-trained"],
                        activate=True,
                    )
                    if ok:
                        st.success("Setup approved, saved and activated. Mode 1 will use ONLY this setup.")
                        st.session_state.pop("v8_pending_setup", None)
                        st.rerun()

    with tabs[2]:
        name = st.text_input("Setup name", placeholder="Momentum Burst", key="v8_manual_name")
        summary = st.text_area("Setup description", height=70, key="v8_manual_summary")
        conditions = st.text_area("Conditions — one condition per line", height=140, key="v8_manual_conditions", placeholder="Price consolidates below resistance\nBreakout closes above resistance\nVolume expands on breakout")
        trigger = st.text_input("Trigger", key="v8_manual_trigger", placeholder="Breakout close above resistance")
        invalidation = st.text_input("Invalidation", key="v8_manual_invalidation", placeholder="Close back below breakout level")
        if st.button("CREATE & ACTIVATE", type="primary", use_container_width=True, key="v8_create_manual"):
            if not name.strip() or not conditions.strip():
                st.warning("Enter a setup name and at least one condition.")
            else:
                condition_rows = []
                for i, line in enumerate(conditions.splitlines(), 1):
                    if line.strip():
                        condition_rows.append({"id":f"R{i:02d}","name":line.strip()[:80],"type":"other","required":True,"condition":line.strip(),"evidence_hint":"Use supplied chart/data evidence."})
                setup = {
                    "setup_name": name.strip(), "summary": summary.strip(), "conditions": condition_rows,
                    "trigger":{"description":trigger.strip(),"type":"manual"},
                    "confirmation":[],
                    "invalidation":[{"id":"I01","description":invalidation.strip()}] if invalidation.strip() else [],
                    "exclusions":[],"examples":[],"confidence":"HIGH","source_name":"manual",
                }
                if save_setup_to_memory(setup, source_text=summary, tags=["manual"], activate=True):
                    st.success("Setup created and activated.")
                    st.rerun()

    with tabs[3]:
        st.info("V8 accepts visual examples as part of PDF understanding. The next backend phase will persist standalone example images; for now, keep visual examples inside the strategy PDF so the PDF vision pass sees them together with the written rules.")

    with tabs[4]:
        query = st.text_input("Search memory", placeholder="breakout volume", key="v8_memory_search")
        if query:
            for result in search_memory(query, 10):
                st.markdown(f"""
<div style="border:1px solid {BORDER};padding:9px;margin-bottom:5px;background:{DARK2};">
 <b style="color:{IVORY};">{_v8_escape(result.get('rule_name',''))}</b>
 <span style="color:{T3};font-family:{MONO};font-size:9px;">{result.get('score',0):.2f}</span>
 <div style="font-size:10px;color:{T2};margin-top:4px;">{_v8_escape(str(result.get('rule_text',''))[:300])}</div>
</div>""", unsafe_allow_html=True)


def render_mode1():
    active = get_active_setup()
    st.markdown(f"""
<div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {AMBER};padding:12px 16px;margin-bottom:12px;">
 <div style="font-size:14px;font-weight:800;color:{IVORY};">Setup Analysis</div>
 <div style="font-size:11px;color:{T2};margin-top:3px;">V8 checks the current security ONLY against the active trained setup. Market calculations are deterministic; chart coordinates are mapped from price/time data.</div>
</div>""", unsafe_allow_html=True)
    if not active:
        st.warning("No active trained setup. Go to Mode 2 → Import PDF, review Arka's understanding, and activate the setup.")
        return

    st.markdown(f"""
<div style="border:1px solid {GREEN};background:{DARK2};padding:9px 12px;margin-bottom:10px;">
 <span style="font-family:{MONO};font-size:9px;color:{T3};letter-spacing:1px;">ACTIVE SETUP</span>
 <b style="color:{IVORY};margin-left:10px;">{_v8_escape(active.get('setup_name','Unnamed'))}</b>
 <span style="float:right;font-family:{MONO};font-size:9px;color:{GREEN};">ACTIVE</span>
</div>""", unsafe_allow_html=True)

    if "m1_ticker_v8" not in st.session_state:
        st.session_state["m1_ticker_v8"] = ""
    col_input, col_load = st.columns([4, 1])
    with col_input:
        ticker = st.text_input("Security", placeholder="RELIANCE, TCS, HDFCBANK, NIFTY50...", key="m1_ticker_v8")
    with col_load:
        load = st.button("LOAD", type="primary", use_container_width=True, key="m1_load_v8")
    if load and ticker.strip():
        ticker = ticker.strip().upper()
        st.session_state["m1_loaded_ticker_v8"] = ticker
        st.session_state["m1_chart_fetched_v8"] = False
        st.session_state.pop("m1_result_v8", None)
        st.session_state.pop("m1_annotated_v8", None)
    loaded = st.session_state.get("m1_loaded_ticker_v8", ticker.strip().upper() if ticker.strip() else "")
    if not loaded:
        st.info("Enter a security to begin.")
        return

    if not st.session_state.get("m1_chart_fetched_v8", False):
        with st.spinner(f"Preparing {loaded} daily + weekly data..."):
            daily_img, daily_meta = get_chart_screenshot(loaded, interval="1d")
            weekly_img, weekly_meta = get_chart_screenshot(loaded, interval="1wk")
        st.session_state.update(
            m1_daily_img_v8=daily_img, m1_daily_meta_v8=daily_meta,
            m1_weekly_img_v8=weekly_img, m1_weekly_meta_v8=weekly_meta,
            m1_chart_fetched_v8=True,
        )
    daily_img = st.session_state.get("m1_daily_img_v8")
    daily_meta = st.session_state.get("m1_daily_meta_v8")
    weekly_img = st.session_state.get("m1_weekly_img_v8")
    weekly_meta = st.session_state.get("m1_weekly_meta_v8")
    if daily_img is None or daily_meta is None:
        st.error("Daily chart unavailable.")
        return

    chart_col, rule_col, result_col = st.columns([3.5, 2.1, 2.4])
    with chart_col:
        tab_d, tab_w = st.tabs(["DAILY", "WEEKLY"])
        with tab_d:
            render_lw_chart(loaded, interval="1d", height=320)
        with tab_w:
            render_lw_chart(loaded, interval="1wk", height=320)
        note = st.text_area("Context (optional)", placeholder="Ask about this setup only…", height=55, key="m1_note_v8")
        if st.button("ANALYZE ACTIVE SETUP", type="primary", use_container_width=True, key="m1_analyze_v8"):
            with st.spinner("Mapping setup → deterministic market evidence → AI reasoning..."):
                result = analyze_chart(daily_img, daily_meta, weekly_img, weekly_meta, active, note, loaded)
            st.session_state["m1_result_v8"] = result
            try:
                st.session_state["m1_annotated_v8"] = draw_annotations(daily_img, daily_meta, result)
            except Exception:
                st.session_state["m1_annotated_v8"] = daily_img
            st.rerun()
        if st.session_state.get("m1_annotated_v8") is not None:
            st.markdown(f'<div style="font-family:{MONO};font-size:9px;color:{AMBER};letter-spacing:1px;margin:8px 0 5px;">DATA-MAPPED ANNOTATIONS</div>', unsafe_allow_html=True)
            st.image(st.session_state["m1_annotated_v8"], use_container_width=True)

    with rule_col:
        st.markdown(f'<div style="font-family:{MONO};font-size:10px;color:{AMBER};letter-spacing:1px;margin-bottom:7px;">ACTIVE SETUP RULES</div>', unsafe_allow_html=True)
        rules = active.get("conditions", []) + active.get("confirmation", [])
        for rule in rules:
            st.markdown(f"""
<div style="border-bottom:1px solid {BORDER};padding:7px 0;">
 <b style="font-family:{MONO};font-size:9px;color:{AMBER};">{_v8_escape(rule.get('id','R'))}</b>
 <span style="font-size:10px;color:{IVORY};margin-left:5px;">{_v8_escape(rule.get('name',rule.get('description','Condition')))}</span>
 <div style="font-size:9px;color:{T3};margin-top:3px;">{_v8_escape(rule.get('condition',rule.get('description','')))}</div>
</div>""", unsafe_allow_html=True)
        result = st.session_state.get("m1_result_v8")
        if result:
            st.markdown(f'<div style="font-family:{MONO};font-size:10px;color:{AMBER};letter-spacing:1px;margin:12px 0 7px;">RULE CHECKS</div>', unsafe_allow_html=True)
            render_rule_checklist(result.get("rule_checks", []))

    with result_col:
        st.markdown(f'<div style="font-family:{MONO};font-size:10px;color:{AMBER};letter-spacing:1px;margin-bottom:7px;">VERDICT</div>', unsafe_allow_html=True)
        result = st.session_state.get("m1_result_v8")
        if not result:
            st.markdown(f'<div style="border:1px dashed {BORDER};padding:22px;text-align:center;color:{T3};font-family:{MONO};font-size:9px;">RUN ANALYSIS TO SEE SETUP VERDICT</div>', unsafe_allow_html=True)
        else:
            st.markdown(verdict_badge(result.get("verdict","FLAGGED"), result.get("score",0), result.get("confluence",{})), unsafe_allow_html=True)
            st.markdown(render_confluence_panel(result.get("confluence",{})), unsafe_allow_html=True)
            st.markdown(f"""
<div style="border:1px solid {BORDER};background:{DARK2};padding:10px;margin-bottom:8px;">
 <div style="font-family:{MONO};font-size:10px;color:{AMBER};letter-spacing:1px;">SETUP STATE</div>
 <div style="font-family:{MONO};font-size:14px;font-weight:800;color:{IVORY};margin-top:5px;">{_v8_escape(result.get('setup_state','UNKNOWN'))}</div>
</div>""", unsafe_allow_html=True)
            invalidation = result.get("invalidation", {}) or {}
            st.markdown(f"""
<div style="border:1px solid {BORDER};background:{DARK2};padding:10px;margin-bottom:8px;">
 <div style="font-family:{MONO};font-size:10px;color:{AMBER};letter-spacing:1px;">INVALIDATION</div>
 <div style="font-size:11px;color:{IVORY};margin-top:5px;">Level: {_v8_escape(invalidation.get('primary_level','N/A'))}</div>
 <div style="font-size:10px;color:{T2};margin-top:4px;">{_v8_escape(invalidation.get('what_changes_view',invalidation.get('condition','')))}</div>
</div>""", unsafe_allow_html=True)
            with st.expander("EVIDENCE LEDGER", expanded=True):
                for item in result.get("evidence", []):
                    st.markdown(f"**{_v8_escape(item.get('claim',''))}** — {_v8_escape(item.get('data',''))} → {_v8_escape(item.get('conclusion',''))}")
            with st.expander("FULL ANALYSIS", expanded=False):
                st.write(result.get("detailed_analysis", ""))


def render_arka_ai():
    with st.expander("API Status", expanded=False):
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("GEMINI_KEY", "SET" if GEMINI_KEY else "MISSING")
        c2.metric("PINECONE_KEY", "SET" if PINECONE_KEY else "MISSING")
        c3.metric("Gemini lib", "OK" if HAS_GEMINI else "MISSING")
        c4.metric("Pinecone lib", "OK" if HAS_PINECONE else "MISSING")
    st.markdown(f"""
<div style="text-align:center;margin-bottom:12px;font-family:{FONT};">
 <div style="font-size:24px;font-weight:800;color:{IVORY};">Arka <span style="color:{AMBER};">AI</span> <span style="font-family:{MONO};font-size:11px;color:{T3};">V8</span></div>
 <div style="font-size:9px;letter-spacing:2px;color:{T3};text-transform:uppercase;font-family:{MONO};margin-top:3px;">Setup-first · Evidence-first · Coordinate-safe</div>
</div>""", unsafe_allow_html=True)
    mode = st.radio("Select Mode", ["Mode 1 — Setup Analysis", "Mode 2 — Train AI"], horizontal=True, key="ai_mode_v8")
    st.markdown(f'<div style="height:1px;background:{BORDER};margin:10px 0 12px;"></div>', unsafe_allow_html=True)
    if mode == "Mode 1 — Setup Analysis":
        render_mode1()
    else:
        render_mode2()
