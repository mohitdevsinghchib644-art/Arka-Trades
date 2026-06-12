"""
arka_ai.py  —  Arka AI Trading Companion
==========================================
Brain     : Gemini 2.5 Flash  (vision + text)
Memory    : Pinecone           (vector database)
Voice     : Web Speech API     (browser TTS)
Drawing   : Data-coordinate annotation engine (precise placement)
PDF Parse : pdfplumber
"""

import streamlit as st
import streamlit.components.v1 as components
import base64, io, json, time, traceback
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

try:
    import requests
    from io import BytesIO
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Keys from Streamlit Secrets ────────────────────────────
GEMINI_KEY   = st.secrets.get("GEMINI_KEY",   "")
PINECONE_KEY = st.secrets.get("PINECONE_KEY", "")
INDEX_NAME   = "arka-trading-rules"

# ── Colors (ChartX theme) ──────────────────────────────────
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
IVORY  = "#E2E8F0"
NAVY   = "#101A33"
FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"

# ══════════════════════════════════════════════════════════
# 1. GEMINI CLIENT
# ══════════════════════════════════════════════════════════

@st.cache_resource
def get_gemini():
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    try:
        genai.configure(api_key=GEMINI_KEY)
        return genai.GenerativeModel(
            model_name="gemini-2.5-flash",

            system_instruction="""You are Arka AI — an elite, zero-emotion technical analysis companion.

CORE RULES:
- Strip ALL emotional phrases. Be precise, surgical, and direct.
- Frame everything around the user's personal rules stored in your memory.
- You are NOT SEBI registered — educational analysis only.
- Voice summaries: punchy, clear, under 120 words.
- When flagging issues, be brutally honest about execution blind spots.

You receive BOTH a chart image AND the exact OHLC data table (candle index, date,
open, high, low, close, volume). Use the NUMBERS in the table for all price levels
and candle positions — the image is only for visual pattern confirmation.

ANNOTATION RULES (critical):
- All annotations use DATA coordinates: candle index (column "i" in the table) and PRICE.
- Never invent prices — every level must come from actual highs/lows/closes in the table.
- "levels"  = horizontal support/resistance lines at an exact price.
- "zones"   = rectangles spanning candle_from..candle_to and price_bottom..price_top
              (e.g. entry zone, consolidation base, supply zone).
- "marks"   = a single point of interest at (candle, price) with direction "up" or "down"
              (e.g. breakout candle, entry trigger, stop level).

OUTPUT FORMAT — return ONLY valid JSON, no markdown:
{
  "verdict": "VALID | INVALID | FLAGGED",
  "score": 7,
  "voice_summary": "2-3 sentence direct summary",
  "detailed_analysis": "Full breakdown referencing exact prices and dates",
  "rules_matched": ["rule name — why it matched"],
  "rules_violated": ["rule name — why it failed"],
  "annotations": {
    "levels": [
      {"price": 2941.50, "color": "#10B981", "label": "PDH Resistance"}
    ],
    "zones": [
      {"candle_from": 60, "candle_to": 75, "price_top": 2950.0, "price_bottom": 2890.0,
       "color": "#4F8DFD", "label": "Consolidation Base"}
    ],
    "marks": [
      {"candle": 82, "price": 2955.0, "direction": "up", "color": "#10B981", "label": "Breakout"}
    ]
  }
}"""
        )
    except Exception:
        return None


# ══════════════════════════════════════════════════════════
# 2. PINECONE VECTOR MEMORY
# ══════════════════════════════════════════════════════════

@st.cache_resource
def get_pinecone_index():
    if not HAS_PINECONE or not PINECONE_KEY:
        return None
    try:
        pc = Pinecone(api_key=PINECONE_KEY)
        existing = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in existing:
            pc.create_index(
                name=INDEX_NAME, dimension=768, metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            time.sleep(2)
        return pc.Index(INDEX_NAME)
    except Exception:
        return None


def get_embedding(text: str) -> list:
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    if not text or not text.strip():
        return None
    try:
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text.strip()
        )
        embedding = result.get("embedding", [])
        if not embedding or len(embedding) != 768:
            return None
        if not any(abs(v) > 1e-7 for v in embedding):
            return None
        return embedding
    except Exception:
        traceback.print_exc()
        return None


def save_rule_to_memory(rule_type: str, rule_name: str, rule_text: str, tags: list = None):
    idx = get_pinecone_index()
    if not idx:
        st.error("Pinecone index not available. Check PINECONE_KEY in secrets.")
        return False
    if not rule_text or len(rule_text.strip()) < 5:
        st.error("Rule description is too short (minimum 5 characters).")
        return False
    try:
        full_text = f"{rule_type}: {rule_name}\n{rule_text}".strip()
        time.sleep(2)
        embedding = get_embedding(full_text)
        if embedding is None:
            st.error(f"Embedding failed for '{rule_name}'. Check GEMINI_KEY.")
            return False
        vector_id = f"rule_{int(time.time())}_{rule_name[:20].replace(' ','_').upper()}"
        idx.upsert(vectors=[{
            "id": vector_id, "values": embedding,
            "metadata": {
                "rule_type": rule_type, "rule_name": rule_name,
                "rule_text": rule_text, "tags": json.dumps(tags or []),
                "saved_at": datetime.now().isoformat()
            }
        }])
        return True
    except Exception as e:
        st.error(f"Save error: {e}")
        traceback.print_exc()
        return False


def search_memory(query: str, top_k: int = 5) -> list[dict]:
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        results = idx.query(vector=embedding, top_k=top_k, include_metadata=True)
        return [
            {"score": m.score,
             "rule_type": m.metadata.get("rule_type", ""),
             "rule_name": m.metadata.get("rule_name", ""),
             "rule_text": m.metadata.get("rule_text", ""),
             "tags": json.loads(m.metadata.get("tags", "[]"))}
            for m in results.matches if m.score > 0.5
        ]
    except Exception as e:
        st.error(f"Search error: {e}")
        return []


def build_rules_context(query: str = "trading setup entry exit rules") -> str:
    rules = search_memory(query, top_k=8)
    if not rules:
        return ("No custom rules found in memory. Analyze using general technical "
                "analysis principles and state clearly that no saved setups were checked.")
    lines = ["=== USER'S PERSONAL TRADING RULES (from memory) ===",
             "Check the chart against EVERY rule below. For each, state explicitly",
             "whether the setup is PRESENT or NOT PRESENT on this chart."]
    for r in rules:
        lines.append(f"\n[{r['rule_type']}] {r['rule_name']} (relevance: {r['score']:.2f})")
        lines.append(f"  -> {r['rule_text']}")
    lines.append("\n=== END OF RULES ===")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 3. CHART IMAGE + AXIS GEOMETRY (the key to accurate drawing)
# ══════════════════════════════════════════════════════════

def get_chart_screenshot(ticker: str, period: str = "3mo"):
    """
    Build the candlestick chart AND capture exact axis geometry so that
    (candle index, price) can be converted to precise pixel positions later.
    Returns (PIL.Image, meta dict) or (None, None).
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

        end   = datetime.today()
        start = end - timedelta(days=150)

        hist = yf.Ticker(yf_ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True, raise_errors=False
        )

        if hist is None or hist.empty:
            st.error(f"No data for {ticker}")
            return None, None

        hist = hist.tail(90).copy()
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
        ax.set_xticklabels([hist.index[i].strftime("%d %b") for i in positions],
                           rotation=45, ha="right", fontsize=8)
        ax.set_xlim(-1, n)

        price_min = float(hist["Low"].min())
        price_max = float(hist["High"].max())
        pad = (price_max - price_min) * 0.06
        ax.set_ylim(price_min - pad, price_max + pad)

        last_date = hist.index[-1]
        date_str  = last_date.strftime("%d %b %Y") if hasattr(last_date, "strftime") else str(last_date)[:10]
        ax.set_title(f"{clean}  ·  NSE Daily  ·  {date_str}",
                     color=BLUE, fontsize=11, fontweight="bold",
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


def _hex_rgb(color: str, default=(79, 141, 253)):
    try:
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return default


def draw_annotations(img: Image.Image, meta: dict, analysis: dict) -> Image.Image:
    """Draw AI annotations (levels, zones, marks) at exact data positions."""
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
            r, g, b = _hex_rgb(lv.get("color", BLUE))
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

def fetch_lw_ohlc(ticker: str, limit: int = 90) -> list:
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
        start = end - timedelta(days=150)
        hist = yf.Ticker(yf_sym).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True)
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


def render_lw_chart(ticker: str):
    with st.spinner(f"Loading chart for {ticker.upper()}..."):
        data = fetch_lw_ohlc(ticker)
    if not data:
        st.error(f"Chart unavailable for {ticker}. Try a different ticker spelling.")
        return

    last = data[-1]
    prev = data[-2] if len(data) > 1 else last
    chg  = last["close"] - prev["close"]
    chg_pct   = (chg / prev["close"]) * 100 if prev["close"] else 0
    chg_color = GREEN if chg >= 0 else RED
    chg_sign  = "+" if chg >= 0 else ""
    json_data = json.dumps(data)

    html = f"""<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:{DARK}; font-family:'JetBrains Mono',monospace; overflow:hidden; }}
  #hdr {{ padding:10px 16px 6px; display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }}
  .tk {{ font-size:14px; font-weight:800; color:{BLUE}; letter-spacing:2px; }}
  .pr {{ font-size:22px; font-weight:700; color:{IVORY}; }}
  .ch {{ font-size:13px; font-weight:600; color:{chg_color}; }}
  #chart {{ width:100%; }}
</style></head>
<body>
<div id="hdr">
  <span class="tk">{ticker.upper()} &middot; NSE &middot; Daily</span>
  <span class="pr">&#8377;{last["close"]:,.2f}</span>
  <span class="ch">{chg_sign}{chg:.2f} ({chg_sign}{chg_pct:.2f}%)</span>
</div>
<div id="chart"></div>
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
<script>
const rawData = {json_data};
const H = 400;
const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
  width: document.documentElement.clientWidth, height: H,
  layout: {{ background: {{ type:'solid', color:'{DARK}' }}, textColor:'{T2}', fontSize:11 }},
  grid: {{ vertLines: {{ color:'{BORDER}', style:1 }}, horzLines: {{ color:'{BORDER}', style:1 }} }},
  crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: {{ color:'{BLUE}55', labelBackgroundColor:'{NAVY}' }},
    horzLine: {{ color:'{BLUE}55', labelBackgroundColor:'{NAVY}' }} }},
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
    st.components.v1.html(html, height=470, scrolling=False)


# ══════════════════════════════════════════════════════════
# 4. ANALYSIS ENGINE
# ══════════════════════════════════════════════════════════

def image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def analyze_chart(img: Image.Image, meta: dict, user_note: str = "", ticker: str = "") -> dict:
    """Send chart image + exact OHLC table + saved rules to Gemini."""
    model = get_gemini()
    if model is None:
        return {"verdict": "ERROR", "score": 0,
                "voice_summary": "Gemini not configured. Check GEMINI_KEY in secrets.",
                "detailed_analysis": "Gemini not configured.",
                "rules_matched": [], "rules_violated": [], "annotations": {}}

    rules_ctx = build_rules_context(user_note or f"trading entry setup validation for {ticker}")

    # Compact OHLC table so the AI works with real numbers
    rows = meta.get("ohlc", [])
    table = "i,date,open,high,low,close,volume\n" + "\n".join(
        f'{r["i"]},{r["date"]},{r["o"]},{r["h"]},{r["l"]},{r["c"]},{r["v"]}' for r in rows)

    prompt = f"""{rules_ctx}

TASK: Analyze this {ticker} daily chart. Check it against EVERY saved rule above and
state for each whether the setup is PRESENT or NOT on this chart right now.

EXACT OHLC DATA (use these numbers for all levels and candle indexes):
{table}

{f'USER NOTE: {user_note}' if user_note else ''}

Latest candle index is {meta.get("n", 90) - 1}. All annotation prices must be inside
the range {meta["ylim"][0]:.2f} to {meta["ylim"][1]:.2f}, and candle indexes between 0
and {meta.get("n", 90) - 1}.

Return ONLY the JSON object. No markdown, no preamble."""

    try:
        time.sleep(1)
        img_data = {"mime_type": "image/png", "data": image_to_base64(img)}
        response = model.generate_content([prompt, img_data])
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {"verdict": "FLAGGED", "score": 5,
                "voice_summary": "Analysis complete. Check the detailed section.",
                "detailed_analysis": response.text if 'response' in dir() else "Error parsing response.",
                "rules_matched": [], "rules_violated": [], "annotations": {}}
    except Exception as e:
        return {"verdict": "ERROR", "score": 0, "voice_summary": str(e),
                "detailed_analysis": str(e), "rules_matched": [],
                "rules_violated": [], "annotations": {}}


# ══════════════════════════════════════════════════════════
# 5. PDF PARSER
# ══════════════════════════════════════════════════════════

def extract_pdf_rules(pdf_file) -> list[str]:
    try:
        chunks = []
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    for i in range(0, len(text), 500):
                        chunk = text[i:i+500].strip()
                        if len(chunk) > 50:
                            chunks.append(chunk)
        return chunks
    except Exception as e:
        st.error(f"PDF error: {e}")
        return []


# ══════════════════════════════════════════════════════════
# 6. VOICE OUTPUT
# ══════════════════════════════════════════════════════════

def speak(text: str, rate: float = 0.95, pitch: float = 1.0):
    clean = text.replace('"', "'").replace('\n', ' ').replace('\\', '')
    st.markdown(f"""
    <script>
    (function() {{
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        var u = new SpeechSynthesisUtterance("{clean}");
        u.rate = {rate}; u.pitch = {pitch}; u.lang = "en-IN";
        var voices = window.speechSynthesis.getVoices();
        var pref = voices.find(v => v.lang === "en-IN") ||
                   voices.find(v => v.lang.startsWith("en"));
        if (pref) u.voice = pref;
        window.speechSynthesis.speak(u);
    }})();
    </script>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 7. VERDICT BADGE
# ══════════════════════════════════════════════════════════

def verdict_badge(verdict: str, score) -> str:
    colors = {"VALID": GREEN, "INVALID": RED, "FLAGGED": "#F5C518", "ERROR": T2}
    c = colors.get(verdict, T2)
    try:
        bar_w = int((float(score) / 10) * 100)
    except Exception:
        bar_w = 50
    return f"""
<div style="background:{DARK2};border:1px solid {c};border-radius:14px;padding:20px;margin:16px 0;">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div style="background:{c};color:{DARK};font-family:{FONT};
             font-weight:800;font-size:14px;letter-spacing:2px;padding:6px 16px;
             border-radius:8px;">{verdict}</div>
        <div style="font-family:{MONO};font-size:22px;font-weight:700;color:{c};">{score}/10</div>
    </div>
    <div style="background:{DARK3};border-radius:4px;height:6px;width:100%;">
        <div style="background:{c};width:{bar_w}%;height:6px;border-radius:4px;"></div>
    </div>
</div>"""


# ══════════════════════════════════════════════════════════
# 8. MODE 1 — AUTO CHART ANALYSIS
# ══════════════════════════════════════════════════════════

def render_mode1():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {BLUE};
         border-radius:14px;padding:18px 24px;margin-bottom:20px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};">Auto Chart Analysis</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;">
            Enter a stock name. The AI checks the full chart against every setup saved
            in your memory and draws the levels, zones and triggers directly on the chart.
        </div>
    </div>""", unsafe_allow_html=True)

    if "m1_ticker" not in st.session_state:
        st.session_state.m1_ticker = ""
    if "m1_chart_img" not in st.session_state:
        st.session_state.m1_chart_img = None
    if "m1_chart_meta" not in st.session_state:
        st.session_state.m1_chart_meta = None
    if "m1_chart_fetched" not in st.session_state:
        st.session_state.m1_chart_fetched = False

    col_input, col_btn = st.columns([4, 1])
    with col_input:
        ticker_input = st.text_input(
            "Stock / Index",
            placeholder="e.g. RELIANCE, TCS, HDFCBANK, NIFTY50, BANKNIFTY...",
            value=st.session_state.m1_ticker,
            key="m1_ticker_input", label_visibility="collapsed")
        st.session_state.m1_ticker = ticker_input
    with col_btn:
        fetch_btn = st.button("Fetch Chart", type="primary",
                              use_container_width=True, key="m1_fetch")

    st.markdown(f'<div style="font-size:11px;color:{T2};margin:4px 0 6px;">Quick picks:</div>',
                unsafe_allow_html=True)
    qp_cols = st.columns(8)
    quick_picks = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "HDFCBANK",
                   "INFY", "ICICIBANK", "TATASTEEL"]
    for i, qp in enumerate(quick_picks):
        with qp_cols[i % 8]:
            if st.button(qp, key=f"qp_{qp}", use_container_width=True):
                st.session_state.m1_ticker = qp
                st.rerun()

    # Keep chart visible across reruns; refetch only when ticker changes
    load_ticker = None
    if fetch_btn and ticker_input.strip():
        load_ticker = ticker_input.strip()
        st.session_state.m1_chart_fetched = False
    elif st.session_state.m1_ticker:
        load_ticker = st.session_state.m1_ticker

    if load_ticker and st.session_state.get("m1_loaded_ticker") != load_ticker.upper():
        st.session_state.m1_chart_fetched = False
        st.session_state["m1_loaded_ticker"] = load_ticker.upper()
        st.session_state.pop("m1_last_result", None)
        st.session_state.pop("m1_annotated_img", None)

    if not load_ticker:
        st.markdown(f"""
        <div style="background:{DARK3};border:1px solid {BORDER};border-radius:12px;
             padding:48px;text-align:center;margin-top:16px;">
            <div style="font-size:18px;font-weight:800;color:{T2};margin-bottom:6px;">Enter a ticker</div>
            <div style="font-size:12px;color:{T2};opacity:.7;">Type a stock name above or tap a quick pick</div>
        </div>""", unsafe_allow_html=True)
        return

    if not st.session_state.m1_chart_fetched:
        with st.spinner(f"Preparing analysis data for {load_ticker}..."):
            chart_img, meta = get_chart_screenshot(load_ticker, period="3mo")
        st.session_state.m1_chart_img  = chart_img
        st.session_state.m1_chart_meta = meta
        st.session_state.m1_chart_fetched = True

    # Interactive chart + control panel
    col_chart, col_panel = st.columns([5, 2])
    with col_chart:
        render_lw_chart(load_ticker)
    with col_panel:
        user_note  = st.text_area("Add context (optional)",
                                  placeholder="e.g. Is my breakout setup present here?",
                                  height=80, key="m1_note")
        auto_voice = st.toggle("Auto-speak analysis", value=True, key="m1_voice")

        if st.button("Analyze Against My Setups", type="primary",
                     use_container_width=True, key="m1_analyze"):
            img  = st.session_state.get("m1_chart_img")
            meta = st.session_state.get("m1_chart_meta")
            if img is None or meta is None:
                st.error("Chart not ready. Tap Fetch Chart again.")
            else:
                with st.spinner("Arka AI is checking your setups..."):
                    result = analyze_chart(img, meta, user_note, load_ticker)
                st.session_state["m1_last_result"] = result
                try:
                    st.session_state["m1_annotated_img"] = draw_annotations(img, meta, result)
                except Exception:
                    st.session_state["m1_annotated_img"] = img
                if auto_voice:
                    speak(result.get("voice_summary", "Analysis complete."))

    # Results (persist across reruns)
    if st.session_state.get("m1_last_result"):
        result = st.session_state["m1_last_result"]

        st.markdown(verdict_badge(result.get("verdict", "FLAGGED"),
                                  result.get("score", 5)), unsafe_allow_html=True)

        # Annotated chart with AI drawings at exact positions
        if st.session_state.get("m1_annotated_img") is not None:
            st.markdown(f"""
            <div style="font-size:13px;font-weight:800;color:{IVORY};margin:4px 0 8px;">
                AI-Annotated Chart
                <span style="font-weight:600;color:{T2};font-size:11px;">
                    · levels, zones and triggers drawn from your setup rules</span>
            </div>""", unsafe_allow_html=True)
            st.image(st.session_state["m1_annotated_img"], use_container_width=True)

        rc1, rc2 = st.columns(2)
        with rc1:
            if result.get("rules_matched"):
                st.markdown(f"<div style='color:{GREEN};font-size:13px;font-weight:800;margin-top:8px;'>Setups Present</div>",
                            unsafe_allow_html=True)
                for r in result["rules_matched"]:
                    st.markdown(f"<div style='color:{GREEN};font-size:12px;margin:4px 0 0 8px;line-height:1.6;'>+ {r}</div>",
                                unsafe_allow_html=True)
        with rc2:
            if result.get("rules_violated"):
                st.markdown(f"<div style='color:{RED};font-size:13px;font-weight:800;margin-top:8px;'>Setups Not Present / Violated</div>",
                            unsafe_allow_html=True)
                for r in result["rules_violated"]:
                    st.markdown(f"<div style='color:{RED};font-size:12px;margin:4px 0 0 8px;line-height:1.6;'>- {r}</div>",
                                unsafe_allow_html=True)

        with st.expander("Full Analysis", expanded=True):
            st.markdown(f"""
            <div style="background:{DARK3};border-radius:10px;padding:16px;
                 font-size:13px;color:{IVORY};line-height:1.9;">
            {result.get('detailed_analysis','').replace(chr(10),'<br>')}
            </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 9. MODE 2 — TRAINING MODE
# ══════════════════════════════════════════════════════════

def render_mode2():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {GREEN};
         border-radius:14px;padding:18px 24px;margin-bottom:20px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};">Train Your AI</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;">
            Teach Arka AI your setups. Stored permanently in vector memory and checked
            on every future chart analysis.
        </div>
    </div>""", unsafe_allow_html=True)

    tab_manual, tab_chart, tab_pdf, tab_memory = st.tabs([
        "Add Rule", "Annotate Chart", "Upload PDF", "View Memory"])

    # ── TAB 1: Manual Rule Entry ───────────────────────────
    with tab_manual:
        st.markdown(f"<div style='font-size:13px;color:{T2};margin:12px 0 16px;'>Add individual trading rules to AI memory</div>",
                    unsafe_allow_html=True)
        if "m2_rule_name" not in st.session_state: st.session_state.m2_rule_name = ""
        if "m2_rule_text" not in st.session_state: st.session_state.m2_rule_text = ""

        rule_name = st.text_input("Rule Name",
            placeholder="e.g. PDH Breakout Confirmation", key="m2_rule_name")
        rule_text = st.text_area("Exact Conditions",
            placeholder="e.g. Price must close above PDH on breakout candle. Volume must be 1.5x average. RSI above 55.",
            height=130, key="m2_rule_text")

        if st.button("Save to Memory", use_container_width=True,
                     type="primary", key="m2_save_btn"):
            if rule_name.strip() and rule_text.strip():
                with st.spinner("Saving to memory..."):
                    ok = save_rule_to_memory("", rule_name.strip(), rule_text.strip(), [])
                if ok:
                    st.session_state.m2_rule_name = ""
                    st.session_state.m2_rule_text = ""
                    st.success(f"Learned: {rule_name} — applied in every future analysis.")
                    speak(f"Rule saved. I have permanently learned your {rule_name} setup.")
                    st.rerun()
                else:
                    st.error("Save failed. Check Pinecone connection in Streamlit Secrets.")
            else:
                st.warning("Fill in both Rule Name and Conditions.")

    # ── TAB 2: Chart Annotation ────────────────────────────
    with tab_chart:
        st.markdown(f"<div style='font-size:13px;color:{T2};margin:12px 0;'>Upload a setup chart. Click on the key candle. AI learns the visual pattern.</div>",
                    unsafe_allow_html=True)
        train_img_file = st.file_uploader("Upload example setup chart",
                                          type=["png","jpg","jpeg"], key="train_chart")
        tx, ty = None, None
        if train_img_file:
            raw_img = Image.open(train_img_file).convert("RGB")
            orig_w, orig_h = raw_img.size
            if HAS_IMG_COORDS:
                st.markdown(f"<div style='font-size:11px;color:{BLUE};margin-bottom:6px;'>Click on a key candle or zone to target it</div>",
                            unsafe_allow_html=True)
                train_coords = streamlit_image_coordinates(raw_img, key="train_click",
                                                           use_column_width=True)
                if train_coords and train_coords.get("x") is not None:
                    tx, ty = train_coords["x"], train_coords["y"]
                    st.markdown(f"<div style='color:{BLUE};font-size:12px;font-family:monospace;margin-top:6px;'>Target locked: ({tx}, {ty}) — {round(tx/orig_w*100,1)}% H · {round(ty/orig_h*100,1)}% V</div>",
                                unsafe_allow_html=True)
            else:
                st.image(raw_img, use_container_width=True)
                st.caption("Tip: install streamlit-image-coordinates for click targeting")

        if "m2_setup_name" not in st.session_state: st.session_state.m2_setup_name = ""
        if "m2_setup_rules" not in st.session_state: st.session_state.m2_setup_rules = ""

        setup_name = st.text_input("Setup Name",
            placeholder="e.g. Low Volume Handle — Cup and Handle", key="m2_setup_name")
        setup_rules = st.text_area("What should AI learn from this chart?",
            placeholder="e.g. Volume drops 40% during consolidation. Entry on breakout above left cup rim.",
            height=100, key="m2_setup_rules")

        if st.button("Teach This Setup", type="primary",
                     use_container_width=True, key="m2_teach_btn"):
            if setup_name.strip() and setup_rules.strip():
                full_rule = setup_rules.strip()
                if tx and train_img_file:
                    full_rule += (f"\n[Chart coordinate reference: click at ({tx},{ty}) = "
                                  f"{round(tx/orig_w*100,1)}% X, {round(ty/orig_h*100,1)}% Y]")
                with st.spinner("Teaching Arka AI..."):
                    ok = save_rule_to_memory("", setup_name.strip(), full_rule, ["chart-trained"])
                if ok:
                    st.session_state.m2_setup_name = ""
                    st.session_state.m2_setup_rules = ""
                    st.success(f"Taught: {setup_name}")
                    speak(f"Understood. I have learned the {setup_name} setup.")
                    st.rerun()
                else:
                    st.error("Save failed. Check Pinecone connection.")
            else:
                st.warning("Enter setup name and description.")

    # ── TAB 3: PDF Upload ──────────────────────────────────
    with tab_pdf:
        st.markdown(f"<div style='font-size:13px;color:{T2};margin:12px 0;'>Upload trading rules as PDF. AI reads and stores everything.</div>",
                    unsafe_allow_html=True)
        pdf_file = st.file_uploader("Upload PDF", type=["pdf"], key="pdf_upload")
        if pdf_file and st.button("Extract and Learn from PDF", type="primary",
                                  use_container_width=True, key="pdf_learn_btn"):
            with st.spinner("Reading PDF..."):
                chunks = extract_pdf_rules(pdf_file)
            if not chunks:
                st.error("No text found in PDF.")
            else:
                st.info(f"Found {len(chunks)} rule segments. Saving to memory...")
                progress = st.progress(0)
                saved = 0
                for i, chunk in enumerate(chunks):
                    time.sleep(2)
                    ok = save_rule_to_memory("", f"{pdf_file.name} — Chunk {i+1}",
                                             chunk, ["pdf-trained", pdf_file.name[:30]])
                    if ok: saved += 1
                    progress.progress((i+1)/len(chunks))
                st.success(f"Learned {saved}/{len(chunks)} rule segments from {pdf_file.name}")
                speak(f"PDF processed. I have learned {saved} trading rules.")

    # ── TAB 4: Memory Viewer ───────────────────────────────
    with tab_memory:
        st.markdown(f"<div style='font-size:13px;color:{T2};margin:12px 0;'>Browse everything Arka AI has learned</div>",
                    unsafe_allow_html=True)
        query = st.text_input("Search memory", placeholder="e.g. volume breakout", key="mem_search")
        if query:
            with st.spinner("Searching..."):
                results = search_memory(query, top_k=10)
            if not results:
                st.info("No matching rules found.")
            else:
                st.success(f"Found {len(results)} matching rules:")
                for r in results:
                    sc = GREEN if r["score"] > 0.8 else BLUE if r["score"] > 0.6 else T2
                    st.markdown(f"""
                    <div style="background:{DARK3};border:1px solid {BORDER};
                         border-left:3px solid {sc};border-radius:10px;
                         padding:14px 16px;margin-bottom:8px;">
                        <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                            <span style="font-weight:800;font-size:13px;color:{IVORY};">{r['rule_name']}</span>
                            <span style="font-family:{MONO};font-size:11px;color:{sc};">{r['score']:.2f} match</span>
                        </div>
                        <div style="font-size:13px;color:{T2};line-height:1.7;">
                            {r['rule_text'][:300]}...</div>
                    </div>""", unsafe_allow_html=True)
        else:
            with st.spinner("Loading memory..."):
                all_rules = search_memory("trading setup rule entry exit", top_k=20)
            if all_rules:
                st.markdown(f"<div style='font-size:12px;color:{T2};margin-bottom:12px;'>Showing {len(all_rules)} stored rules:</div>",
                            unsafe_allow_html=True)
                for r in all_rules:
                    st.markdown(f"""
                    <div style="background:{DARK3};border:1px solid {BORDER};
                         border-left:3px solid {BLUE};border-radius:10px;
                         padding:12px 16px;margin-bottom:6px;">
                        <div style="font-weight:800;font-size:13px;color:{IVORY};margin-bottom:4px;">{r['rule_name']}</div>
                        <div style="font-size:13px;color:{T2};line-height:1.6;">
                            {r['rule_text'][:250]}...</div>
                    </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='color:{T2};font-size:13px;text-align:center;padding:40px;'>No rules stored yet. Use Add Rule or Annotate Chart to teach Arka AI.</div>",
                            unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 10. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════

def render_arka_ai():
    """Main Arka AI page — call from app.py page router."""

    with st.expander("API Status", expanded=False):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("GEMINI_KEY",   "SET" if GEMINI_KEY   else "MISSING")
        col2.metric("PINECONE_KEY", "SET" if PINECONE_KEY else "MISSING")
        col3.metric("Gemini lib",   "OK"  if HAS_GEMINI   else "MISSING")
        col4.metric("Pinecone lib", "OK"  if HAS_PINECONE else "MISSING")
        if not GEMINI_KEY:
            st.error("GEMINI_KEY is not set in Streamlit secrets.")
        if not PINECONE_KEY:
            st.error("PINECONE_KEY is not set in Streamlit secrets.")

    st.markdown(f"""
    <div style="text-align:center;margin-bottom:24px;">
        <div style="font-size:30px;font-weight:800;color:{IVORY};letter-spacing:-0.5px;">
            Arka <span style="background:linear-gradient(90deg,{BLUE},{PURPLE});
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;">AI</span></div>
        <div style="font-size:11px;letter-spacing:3px;color:{T2};
             text-transform:uppercase;margin-top:4px;">
             Zero-Emotion Chart Companion · Powered by Gemini 2.5 Flash</div>
    </div>""", unsafe_allow_html=True)

    mode = st.radio("Select Mode", ["Mode 1 — Auto Analysis", "Mode 2 — Train AI"],
                    horizontal=True, key="ai_mode")

    st.markdown(f"<div style='height:1px;background:{BORDER};margin:16px 0;'></div>",
                unsafe_allow_html=True)

    if mode == "Mode 1 — Auto Analysis":
        render_mode1()
    else:
        render_mode2()
