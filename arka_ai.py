"""
arka_ai.py  —  Arka AI Trading Companion (v7 — Beast Mode)
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

import re
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
- You are given a numbered list of the user's OWN saved trading rules, each
  with a unique rule_id. You MUST evaluate the chart against EVERY SINGLE
  rule in that list — not a subset, not "the most relevant ones." If a rule
  is irrelevant to what's visible on this chart, say so explicitly with
  status "UNCLEAR" rather than omitting it.
- You are NOT SEBI registered — educational analysis only.
- Voice summaries: punchy, clear, under 120 words.
- When flagging issues, be brutally honest about execution blind spots.

You receive TWO charts (daily and weekly) as images, PLUS the exact OHLC
data table for each (candle index, date, open, high, low, close, volume).
Use the NUMBERS in the tables for all price levels and candle positions —
images are only for visual pattern confirmation. State explicitly whether
daily and weekly timeframes CONFLUENCE (agree) or CONFLICT.

ANNOTATION RULES (critical, apply to the DAILY chart only):
- All annotations use DATA coordinates: candle index (column "i" in the
  daily table) and PRICE.
- Never invent prices — every level must come from actual highs/lows/closes
  in the daily table.
- "levels"  = horizontal support/resistance lines at an exact price.
- "zones"   = rectangles spanning candle_from..candle_to and
              price_bottom..price_top (e.g. entry zone, consolidation base).
- "marks"   = a single point of interest at (candle, price) with direction
              "up" or "down" (e.g. breakout candle, entry trigger, stop).

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no preamble:
{
  "verdict": "VALID | INVALID | FLAGGED",
  "score": 7,
  "confluence": "AGREE | CONFLICT | INSUFFICIENT_DATA",
  "confluence_note": "one sentence on how daily and weekly relate",
  "voice_summary": "2-3 sentence direct summary",
  "detailed_analysis": "Full breakdown referencing exact prices and dates",
  "rule_checks": [
    {"rule_id": "the exact rule_id given to you", "rule_name": "name",
     "status": "PRESENT | NOT_PRESENT | UNCLEAR",
     "confidence": 82, "reason": "one line citing the actual price/candle evidence"}
  ],
  "annotations": {
    "levels": [
      {"price": 2941.50, "color": "#00C853", "label": "PDH Resistance"}
    ],
    "zones": [
      {"candle_from": 60, "candle_to": 75, "price_top": 2950.0, "price_bottom": 2890.0,
       "color": "#4F8DFD", "label": "Consolidation Base"}
    ],
    "marks": [
      {"candle": 82, "price": 2955.0, "direction": "up", "color": "#00C853", "label": "Breakout"}
    ]
  }
}

rule_checks MUST contain exactly one entry per rule_id you were given — no
more, no fewer."""
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
    """
    Similarity search — KEPT for the Memory Viewer's manual search box,
    where "find rules related to X" is exactly the right tool. This is
    NOT used to decide what a chart analysis checks against anymore;
    see get_all_rules() for that.
    """
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        results = idx.query(vector=embedding, top_k=top_k, include_metadata=True)
        return [
            {"id": m.id, "score": m.score,
             "rule_type": m.metadata.get("rule_type", ""),
             "rule_name": m.metadata.get("rule_name", ""),
             "rule_text": m.metadata.get("rule_text", ""),
             "tags": json.loads(m.metadata.get("tags", "[]"))}
            for m in results.matches if m.score > 0.5
        ]
    except Exception as e:
        st.error(f"Search error: {e}")
        return []


def get_all_rules() -> list[dict]:
    """
    NEW v7 — replaces similarity-search-limited retrieval for analysis
    purposes. Paginates Pinecone's list() (vector IDs) + fetch() (full
    metadata) to return EVERY saved rule, deterministically, with
    nothing silently dropped by a top_k cutoff or score threshold.

    This is the accuracy-over-cost tradeoff you explicitly chose: if
    your rule count grows large enough to blow the Gemini prompt budget,
    the fix is chunking this list before it's inlined into the prompt —
    a small change here, not a redesign, since this already returns a
    flat list.
    """
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        all_ids = []
        for id_batch in idx.list(limit=100):
            all_ids.extend(id_batch)
        if not all_ids:
            return []

        rules = []
        # fetch() accepts up to 1000 ids per call on Pinecone serverless;
        # batch at 200 to stay well clear of that regardless of plan.
        for i in range(0, len(all_ids), 200):
            batch_ids = all_ids[i:i + 200]
            fetched = idx.fetch(ids=batch_ids)
            vectors = getattr(fetched, "vectors", fetched.get("vectors", {}) if isinstance(fetched, dict) else {})
            for vec_id, vec in vectors.items():
                meta = vec.metadata if hasattr(vec, "metadata") else vec.get("metadata", {})
                rules.append({
                    "id": vec_id,
                    "rule_type": meta.get("rule_type", ""),
                    "rule_name": meta.get("rule_name", "Unnamed rule"),
                    "rule_text": meta.get("rule_text", ""),
                    "tags": json.loads(meta.get("tags", "[]")) if meta.get("tags") else [],
                    "saved_at": meta.get("saved_at", ""),
                })
        rules.sort(key=lambda r: r.get("saved_at", ""))
        return rules
    except Exception as e:
        st.error(f"Could not load full rule memory: {e}")
        traceback.print_exc()
        return []


def build_full_rules_prompt(rules: list[dict]) -> str:
    """Renders every rule with an explicit rule_id so Gemini's
    rule_checks response can be matched back to each one deterministically."""
    if not rules:
        return ("No custom rules found in memory. Analyze using general technical "
                "analysis principles, return an empty rule_checks list, and state "
                "clearly in detailed_analysis that no saved setups exist to check.")
    lines = [f"=== USER'S FULL SAVED RULEBOOK ({len(rules)} rules — check EVERY one) ==="]
    for r in rules:
        lines.append(f"\nrule_id: {r['id']}")
        lines.append(f"name: {r['rule_name']}")
        lines.append(f"conditions: {r['rule_text']}")
    lines.append("\n=== END OF RULEBOOK — rule_checks must have exactly one entry per rule_id above ===")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 3. CHART IMAGE + AXIS GEOMETRY (the key to accurate drawing)
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
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _ohlc_table(meta: dict) -> str:
    rows = meta.get("ohlc", [])
    return "i,date,open,high,low,close,volume\n" + "\n".join(
        f'{r["i"]},{r["date"]},{r["o"]},{r["h"]},{r["l"]},{r["c"]},{r["v"]}' for r in rows)


def _extract_json_block(raw: str) -> str:
    """Repair pass for a response that isn't strictly parseable JSON on
    its own: strip code fences if present, then fall back to grabbing
    the outermost {...} span. One bounded local attempt, no re-call to
    Gemini — see module docstring point 4."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return text[first:last + 1]
    return text


def _empty_result(verdict: str, message: str) -> dict:
    return {
        "verdict": verdict, "score": 0, "confluence": "INSUFFICIENT_DATA",
        "confluence_note": "", "voice_summary": message,
        "detailed_analysis": message, "rule_checks": [], "annotations": {},
    }


def analyze_chart(daily_img: Image.Image, daily_meta: dict,
                   weekly_img: Image.Image | None, weekly_meta: dict | None,
                   rules: list[dict], user_note: str = "", ticker: str = "") -> dict:
    """
    Send daily (+ weekly, if available) chart images and OHLC tables,
    plus the user's FULL rulebook (not a similarity-search subset), to
    Gemini. Returns a dict matching the schema in get_gemini()'s
    system_instruction, with rule_checks covering every rule_id given.
    """
    model = get_gemini()
    if model is None:
        return _empty_result("ERROR", "Gemini not configured. Check GEMINI_KEY in secrets.")

    rules_ctx = build_full_rules_prompt(rules)
    daily_table = _ohlc_table(daily_meta)
    weekly_table = _ohlc_table(weekly_meta) if weekly_meta else "Weekly data unavailable."

    prompt = f"""{rules_ctx}

TASK: Analyze this {ticker} chart on BOTH timeframes provided. Check it against
EVERY rule_id in the rulebook above and return a rule_checks entry for each one.

DAILY OHLC DATA (image 1 — annotate against THIS table):
{daily_table}

WEEKLY OHLC DATA (image 2, context only — do not annotate against this):
{weekly_table}

{f'USER NOTE: {user_note}' if user_note else ''}

Latest daily candle index is {daily_meta.get("n", 90) - 1}. All annotation prices
must be inside the range {daily_meta["ylim"][0]:.2f} to {daily_meta["ylim"][1]:.2f},
and candle indexes between 0 and {daily_meta.get("n", 90) - 1}.

State confluence between daily and weekly explicitly. Return ONLY the JSON object."""

    response = None
    try:
        time.sleep(1)
        content = [prompt, {"mime_type": "image/png", "data": image_to_base64(daily_img)}]
        if weekly_img is not None:
            content.append({"mime_type": "image/png", "data": image_to_base64(weekly_img)})
        response = model.generate_content(content)
        raw = response.text.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            repaired = _extract_json_block(raw)
            return json.loads(repaired)
    except json.JSONDecodeError:
        fallback_text = response.text if response is not None else "No response text captured."
        result = _empty_result("FLAGGED", "Analysis complete but response formatting was irregular — see full text below.")
        result["detailed_analysis"] = fallback_text
        result["score"] = 5
        return result
    except Exception as e:
        return _empty_result("ERROR", str(e))


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
# 7. VERDICT BADGE + RULE CHECKLIST
# ══════════════════════════════════════════════════════════

def verdict_badge(verdict: str, score, confluence: str = None, confluence_note: str = "") -> str:
    colors = {"VALID": GREEN, "INVALID": RED, "FLAGGED": AMBER, "ERROR": T2}
    c = colors.get(verdict, T2)
    try:
        bar_w = int((float(score) / 10) * 100)
    except Exception:
        bar_w = 50

    confluence_html = ""
    if confluence:
        cf_colors = {"AGREE": GREEN, "CONFLICT": RED, "INSUFFICIENT_DATA": T3}
        cf_c = cf_colors.get(confluence, T3)
        confluence_html = f"""
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid {BORDER};
             display:flex;align-items:baseline;gap:8px;">
            <span style="font-family:{MONO};font-size:10px;color:{T3};letter-spacing:1px;">D/W CONFLUENCE:</span>
            <span style="font-family:{MONO};font-size:11px;font-weight:700;color:{cf_c};">{confluence}</span>
            <span style="font-size:11px;color:{T2};">{confluence_note}</span>
        </div>"""

    return f"""
<div style="background:{DARK2};border:1px solid {c};padding:16px;margin:0 0 14px;font-family:{FONT};">
    <div style="display:flex;align-items:center;gap:12px;">
        <div style="background:{c};color:{DARK};font-family:{MONO};
             font-weight:800;font-size:13px;letter-spacing:2px;padding:5px 14px;">{verdict}</div>
        <div style="font-family:{MONO};font-size:20px;font-weight:700;color:{c};">{score}/10</div>
    </div>
    <div style="background:{DARK3};height:5px;width:100%;margin-top:10px;">
        <div style="background:{c};width:{bar_w}%;height:5px;"></div>
    </div>
    {confluence_html}
</div>"""


def render_rule_checklist(rule_checks: list[dict]):
    """
    NEW v7 — per-rule checklist UI. Replaces v6's two loose prose lists
    (rules_matched / rules_violated) with an explicit row per saved
    rule: status badge, confidence bar, one-line evidence.
    """
    if not rule_checks:
        st.markdown(f"""<div style="border:1px dashed {BORDER};padding:20px 14px;text-align:center;">
            <div style="font-size:11px;color:{T3};font-family:{MONO};">NO RULE CHECKS RETURNED.</div>
        </div>""", unsafe_allow_html=True)
        return

    status_style = {
        "PRESENT":     (GREEN, "✓ PRESENT"),
        "NOT_PRESENT": (RED,   "✕ NOT PRESENT"),
        "UNCLEAR":     (AMBER, "◐ UNCLEAR"),
    }

    rows_html = []
    for rc in rule_checks:
        status = rc.get("status", "UNCLEAR")
        color, label = status_style.get(status, (T3, status))
        conf = rc.get("confidence", 0)
        try:
            conf = int(conf)
        except Exception:
            conf = 0
        rows_html.append(f"""
        <div style="border:1px solid {BORDER};border-left:3px solid {color};background:{DARK2};
             padding:10px 12px;margin-bottom:6px;">
            <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">
                <span style="font-size:12px;font-weight:700;color:{IVORY};font-family:{FONT};">{rc.get('rule_name','Unnamed rule')}</span>
                <span style="font-family:{MONO};font-size:10px;font-weight:700;color:{color};white-space:nowrap;">{label}</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;gap:8px;">
                <span style="font-size:11px;color:{T2};line-height:1.5;flex:1;">{rc.get('reason','')}</span>
                <span style="font-family:{MONO};font-size:10px;color:{T3};white-space:nowrap;">{conf}%</span>
            </div>
            <div style="background:{DARK3};height:3px;width:100%;margin-top:6px;">
                <div style="background:{color};width:{conf}%;height:3px;"></div>
            </div>
        </div>""")

    present_n = sum(1 for rc in rule_checks if rc.get("status") == "PRESENT")
    st.markdown(f"""<div style="font-size:10px;color:{T3};letter-spacing:1px;margin-bottom:8px;font-family:{MONO};">
        {present_n} / {len(rule_checks)} SETUPS PRESENT</div>""", unsafe_allow_html=True)
    st.markdown("".join(rows_html), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 8. MODE 1 — AUTO CHART ANALYSIS (Bloomberg multi-panel)
# ══════════════════════════════════════════════════════════

def render_mode1():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {AMBER};
         padding:14px 20px;margin-bottom:16px;font-family:{FONT};">
        <div style="font-size:15px;font-weight:800;color:{IVORY};">Auto Chart Analysis</div>
        <div style="font-size:12px;color:{T2};margin-top:4px;">
            Checks the chart against your FULL saved rulebook — every rule, every time —
            across daily and weekly timeframes for confluence.
        </div>
    </div>""", unsafe_allow_html=True)

    for key, default in [("m1_ticker", ""), ("m1_daily_img", None), ("m1_daily_meta", None),
                          ("m1_weekly_img", None), ("m1_weekly_meta", None),
                          ("m1_chart_fetched", False)]:
        if key not in st.session_state:
            st.session_state[key] = default

    col_input, col_btn = st.columns([4, 1])
    with col_input:
        ticker_input = st.text_input(
            "Stock / Index",
            placeholder="e.g. RELIANCE, TCS, HDFCBANK, NIFTY50, BANKNIFTY...",
            value=st.session_state.m1_ticker,
            key="m1_ticker_input", label_visibility="collapsed")
        st.session_state.m1_ticker = ticker_input
    with col_btn:
        fetch_btn = st.button("Fetch Charts", type="primary",
                              use_container_width=True, key="m1_fetch")

    st.markdown(f'<div style="font-size:10px;color:{T3};margin:4px 0 6px;font-family:{MONO};letter-spacing:1px;">QUICK PICKS</div>',
                unsafe_allow_html=True)
    qp_cols = st.columns(8)
    quick_picks = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "HDFCBANK",
                   "INFY", "ICICIBANK", "TATASTEEL"]
    for i, qp in enumerate(quick_picks):
        with qp_cols[i % 8]:
            if st.button(qp, key=f"qp_{qp}", use_container_width=True):
                st.session_state.m1_ticker = qp
                st.rerun()

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
        <div style="background:{DARK3};border:1px solid {BORDER};
             padding:44px;text-align:center;margin-top:14px;">
            <div style="font-size:16px;font-weight:800;color:{T2};margin-bottom:6px;">Enter a ticker</div>
            <div style="font-size:12px;color:{T3};">Type a stock name above or tap a quick pick</div>
        </div>""", unsafe_allow_html=True)
        return

    if not st.session_state.m1_chart_fetched:
        with st.spinner(f"Preparing daily + weekly data for {load_ticker}..."):
            d_img, d_meta = get_chart_screenshot(load_ticker, period="3mo", interval="1d")
            w_img, w_meta = get_chart_screenshot(load_ticker, period="2y", interval="1wk")
        st.session_state.m1_daily_img, st.session_state.m1_daily_meta = d_img, d_meta
        st.session_state.m1_weekly_img, st.session_state.m1_weekly_meta = w_img, w_meta
        st.session_state.m1_chart_fetched = True

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)

    # ── Bloomberg-style 3-panel workspace: chart / checklist / verdict ──
    col_chart, col_checklist, col_verdict = st.columns([3.4, 2.3, 2.3])

    with col_chart:
        tf_tab_daily, tf_tab_weekly = st.tabs(["Daily", "Weekly"])
        with tf_tab_daily:
            render_lw_chart(load_ticker, interval="1d", height=320)
        with tf_tab_weekly:
            render_lw_chart(load_ticker, interval="1wk", height=320)

        if st.session_state.get("m1_annotated_img") is not None:
            st.markdown(f"""<div style="font-size:11px;font-weight:800;color:{IVORY};margin:10px 0 6px;font-family:{FONT};">
                AI-Annotated Daily Chart</div>""", unsafe_allow_html=True)
            st.image(st.session_state["m1_annotated_img"], use_container_width=True)

        user_note = st.text_area("Add context (optional)",
                                  placeholder="e.g. Is my breakout setup present here?",
                                  height=70, key="m1_note")
        cA, cB = st.columns([2, 1])
        with cA:
            run_btn = st.button("Analyze Against My Rulebook", type="primary",
                                 use_container_width=True, key="m1_analyze")
        with cB:
            auto_voice = st.toggle("Voice", value=True, key="m1_voice")

        if run_btn:
            d_img = st.session_state.get("m1_daily_img")
            d_meta = st.session_state.get("m1_daily_meta")
            w_img = st.session_state.get("m1_weekly_img")
            w_meta = st.session_state.get("m1_weekly_meta")
            if d_img is None or d_meta is None:
                st.error("Daily chart not ready. Tap Fetch Charts again.")
            else:
                with st.spinner("Loading your full rulebook..."):
                    rules = get_all_rules()
                with st.spinner(f"Checking {len(rules)} saved setups against this chart..."):
                    result = analyze_chart(d_img, d_meta, w_img, w_meta, rules, user_note, load_ticker)
                st.session_state["m1_last_result"] = result
                try:
                    st.session_state["m1_annotated_img"] = draw_annotations(d_img, d_meta, result)
                except Exception:
                    st.session_state["m1_annotated_img"] = d_img
                if auto_voice:
                    speak(result.get("voice_summary", "Analysis complete."))
                st.rerun()

    with col_checklist:
        st.markdown(f"""<div style="font-family:{MONO};font-size:11px;font-weight:700;color:{AMBER};
             letter-spacing:1px;margin-bottom:10px;">RULE CHECKLIST</div>""", unsafe_allow_html=True)
        result = st.session_state.get("m1_last_result")
        if result:
            render_rule_checklist(result.get("rule_checks", []))
        else:
            st.markdown(f"""<div style="border:1px dashed {BORDER};padding:20px 14px;text-align:center;">
                <div style="font-size:11px;color:{T3};font-family:{MONO};">RUN AN ANALYSIS TO SEE YOUR RULEBOOK CHECKED HERE.</div>
            </div>""", unsafe_allow_html=True)

    with col_verdict:
        st.markdown(f"""<div style="font-family:{MONO};font-size:11px;font-weight:700;color:{AMBER};
             letter-spacing:1px;margin-bottom:10px;">VERDICT</div>""", unsafe_allow_html=True)
        result = st.session_state.get("m1_last_result")
        if result:
            st.markdown(verdict_badge(result.get("verdict", "FLAGGED"), result.get("score", 5),
                                       result.get("confluence"), result.get("confluence_note", "")),
                        unsafe_allow_html=True)
            with st.expander("Full Analysis", expanded=True):
                st.markdown(f"""
                <div style="background:{DARK3};padding:14px;
                     font-size:12.5px;color:{IVORY};line-height:1.8;font-family:{FONT};">
                {result.get('detailed_analysis','').replace(chr(10),'<br>')}
                </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div style="border:1px dashed {BORDER};padding:20px 14px;text-align:center;">
                <div style="font-size:11px;color:{T3};font-family:{MONO};">VERDICT APPEARS HERE AFTER ANALYSIS.</div>
            </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 9. MODE 2 — TRAINING MODE
# ══════════════════════════════════════════════════════════

def render_mode2():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {GREEN};
         padding:14px 20px;margin-bottom:16px;font-family:{FONT};">
        <div style="font-size:15px;font-weight:800;color:{IVORY};">Train Your AI</div>
        <div style="font-size:12px;color:{T2};margin-top:4px;">
            Teach Arka AI your setups. Stored permanently in vector memory and checked
            IN FULL on every future chart analysis — nothing gets left out.
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
                st.markdown(f"<div style='font-size:11px;color:{AMBER};margin-bottom:6px;'>Click on a key candle or zone to target it</div>",
                            unsafe_allow_html=True)
                train_coords = streamlit_image_coordinates(raw_img, key="train_click",
                                                           use_column_width=True)
                if train_coords and train_coords.get("x") is not None:
                    tx, ty = train_coords["x"], train_coords["y"]
                    st.markdown(f"<div style='color:{AMBER};font-size:12px;font-family:monospace;margin-top:6px;'>Target locked: ({tx}, {ty}) — {round(tx/orig_w*100,1)}% H · {round(ty/orig_h*100,1)}% V</div>",
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
        st.markdown(f"<div style='font-size:13px;color:{T2};margin:12px 0;'>Browse everything Arka AI has learned. This view uses similarity search (type to filter); analysis always checks your FULL rulebook regardless of what's shown here.</div>",
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
                    sc = GREEN if r["score"] > 0.8 else AMBER if r["score"] > 0.6 else T2
                    st.markdown(f"""
                    <div style="background:{DARK3};border:1px solid {BORDER};
                         border-left:3px solid {sc};
                         padding:14px 16px;margin-bottom:8px;">
                        <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                            <span style="font-weight:800;font-size:13px;color:{IVORY};">{r['rule_name']}</span>
                            <span style="font-family:{MONO};font-size:11px;color:{sc};">{r['score']:.2f} match</span>
                        </div>
                        <div style="font-size:13px;color:{T2};line-height:1.7;">
                            {r['rule_text'][:300]}...</div>
                    </div>""", unsafe_allow_html=True)
        else:
            with st.spinner("Loading full rulebook..."):
                all_rules = get_all_rules()
            if all_rules:
                st.markdown(f"<div style='font-size:12px;color:{T2};margin-bottom:12px;'>{len(all_rules)} rules stored — this is the FULL set checked on every analysis:</div>",
                            unsafe_allow_html=True)
                for r in all_rules:
                    st.markdown(f"""
                    <div style="background:{DARK3};border:1px solid {BORDER};
                         border-left:3px solid {AMBER};
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
    <div style="text-align:center;margin-bottom:20px;font-family:{FONT};">
        <div style="font-size:26px;font-weight:800;color:{IVORY};letter-spacing:-0.5px;">
            Arka <span style="color:{AMBER};">AI</span></div>
        <div style="font-size:10px;letter-spacing:3px;color:{T3};
             text-transform:uppercase;margin-top:4px;font-family:{MONO};">
             Zero-Emotion Chart Companion · Full Rulebook · Multi-Timeframe</div>
    </div>""", unsafe_allow_html=True)

    mode = st.radio("Select Mode", ["Mode 1 — Auto Analysis", "Mode 2 — Train AI"],
                    horizontal=True, key="ai_mode")

    st.markdown(f"<div style='height:1px;background:{BORDER};margin:14px 0;'></div>",
                unsafe_allow_html=True)

    if mode == "Mode 1 — Auto Analysis":
        render_mode1()
    else:
        render_mode2()
