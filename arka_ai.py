"""
arka_ai.py  —  Arka AI Trading Companion
==========================================
Brain     : Gemini 2.5 Flash  (vision + text)
Memory    : Pinecone           (vector database)
Voice     : Web Speech API     (browser TTS)
Click     : streamlit-image-coordinates
PDF Parse : pdfplumber
"""
 
import streamlit as st
import base64, io, json, time, hashlib, traceback
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
    from PIL import Image, ImageDraw
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
    import plotly.graph_objects as go
    HAS_YFINANCE = True
    HAS_PLOTLY = True
except ImportError:
    HAS_YFINANCE = False
    HAS_PLOTLY = False

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
 
# ── Colors ─────────────────────────────────────────────────
GOLD   = "#C8A96A"
GREEN  = "#00B37A"
RED    = "#E84545"
DARK   = "#04080F"
DARK2  = "#060D1A"
DARK3  = "#091525"
BORDER = "#0F2040"
T2     = "#8A9AB5"
IVORY  = "#F7EBE0"
NAVY   = "#0A1D4B"
 
# ══════════════════════════════════════════════════════════
# 1. GEMINI CLIENT
# ══════════════════════════════════════════════════════════
 
@st.cache_resource
def get_gemini():
    if not HAS_GEMINI:
        return None
    if not GEMINI_KEY:
        return None
    try:
        genai.configure(api_key=GEMINI_KEY)
        return genai.GenerativeModel(
            model_name="gemini-2.5-flash-preview-05-20",
            system_instruction="""You are Arka AI — an elite, zero-emotion technical analysis companion.
 
CORE RULES:
- Strip ALL emotional phrases: no "Great setup!", "Nice trade!", "Good job!"
- Be precise, surgical, and direct
- Always reference exact visual locations on the chart
- Frame everything around the user's personal rules stored in your memory
- You are NOT SEBI registered — educational analysis only
- Keep voice-optimized responses: punchy, clear, under 150 words unless asked for detail
- When flagging issues, be brutally honest about execution blind spots
 
OUTPUT FORMAT (always return valid JSON):
{
  "verdict": "VALID | INVALID | FLAGGED",
  "score": 7,
  "voice_summary": "Short punchy 2-3 sentence voice response",
  "detailed_analysis": "Full breakdown",
  "rules_matched": ["rule1", "rule2"],
  "rules_violated": ["rule3"],
  "draw_boxes": [
    {"x": 100, "y": 200, "w": 80, "h": 40, "color": "#00B8CC", "label": "Entry Zone"}
  ],
  "draw_arrows": [
    {"x1": 150, "y1": 300, "x2": 200, "y2": 250, "color": "#FF4444", "label": "Stop Loss"}
  ]
}"""
        )
    except Exception as e:
        return None
  
  
# ══════════════════════════════════════════════════════════
# 2. PINECONE VECTOR MEMORY
# ══════════════════════════════════════════════════════════
 
@st.cache_resource
def get_pinecone_index():
    if not HAS_PINECONE:
        return None
    if not PINECONE_KEY:
        return None
    try:
        pc = Pinecone(api_key=PINECONE_KEY)
        existing = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in existing:
            pc.create_index(
                name=INDEX_NAME,
                dimension=768,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            time.sleep(2)
        return pc.Index(INDEX_NAME)
    except Exception as e:
        return None
  
  
def get_embedding(text: str) -> list:
    """Get text embedding via Gemini embedding model with debug logging."""
    if not HAS_GEMINI or not GEMINI_KEY:
        print("❌ GEMINI not available or GEMINI_KEY not set")
        return None
    
    if not text or not text.strip():
        print(f"❌ Empty text provided to embed")
        return None
    
    try:
        print(f"🔹 Attempting to embed text: {text[:60]}...")
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text.strip()
        )
        
        embedding = result.get("embedding", [])
        print(f"🔹 Received embedding with {len(embedding)} dimensions")
        
        if not embedding:
            print(f"❌ Empty embedding returned from API")
            return None
        
        if len(embedding) != 768:
            print(f"❌ Wrong embedding dimension: {len(embedding)} (expected 768)")
            return None
        
        # Check if all values are zero or near-zero
        non_zero_count = sum(1 for v in embedding if abs(v) > 1e-7)
        print(f"🔹 Non-zero dimensions: {non_zero_count}/768")
        
        if non_zero_count == 0:
            print(f"❌ All-zero embedding detected!")
            return None
        
        print(f"✅ Valid embedding generated with {non_zero_count} non-zero values")
        return embedding
        
    except Exception as e:
        print(f"❌ Embedding API error: {str(e)}")
        traceback.print_exc()
        return None
  
  
def save_rule_to_memory(rule_type: str, rule_name: str, rule_text: str, tags: list = None):
    """Save a trading rule into Pinecone vector memory."""
    idx = get_pinecone_index()
    if not idx:
        st.error("❌ Pinecone index not available. Check PINECONE_KEY in secrets.")
        return False
    
    # Validate inputs
    if not rule_text or not rule_text.strip():
        st.error("❌ Cannot save rule: description is empty")
        return False
    
    if len(rule_text.strip()) < 5:
        st.error("❌ Rule description is too short (minimum 5 characters)")
        return False
    
    try:
        full_text = f"{rule_type}: {rule_name}\n{rule_text}".strip()
        print(f"\n📝 Preparing to save rule: {rule_name}")
        print(f"   Full text: {full_text[:100]}...")
        
        time.sleep(2)  # Rate limit protection
        embedding = get_embedding(full_text)
        
        # CRITICAL: Reject None or invalid embeddings
        if embedding is None:
            st.error(f"❌ Embedding failed for '{rule_name}'. Gemini API may be having issues or GEMINI_KEY is invalid.")
            print(f"❌ Embedding failed - returning False")
            return False
        
        vector_id = f"rule_{int(time.time())}_{rule_name[:20].replace(' ','_').upper()}"
        print(f"📤 Upserting to Pinecone with ID: {vector_id}")
        
        idx.upsert(vectors=[{
            "id":     vector_id,
            "values": embedding,
            "metadata": {
                "rule_type":   rule_type,
                "rule_name":   rule_name,
                "rule_text":   rule_text,
                "tags":        json.dumps(tags or []),
                "saved_at":    datetime.now().isoformat()
            }
        }])
        
        st.success(f"✅ Rule saved to Pinecone: {rule_name}")
        print(f"✅ Successfully saved {rule_name} to Pinecone")
        return True
        
    except Exception as e:
        error_msg = str(e)
        st.error(f"❌ Save error: {error_msg}")
        print(f"❌ Exception during save: {error_msg}")
        traceback.print_exc()
        return False
  
  
def search_memory(query: str, top_k: int = 5) -> list[dict]:
    """Search Pinecone for relevant rules matching the query."""
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        results = idx.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True
        )
        return [
            {
                "score":     m.score,
                "rule_type": m.metadata.get("rule_type", ""),
                "rule_name": m.metadata.get("rule_name", ""),
                "rule_text": m.metadata.get("rule_text", ""),
                "tags":      json.loads(m.metadata.get("tags", "[]")),
            }
            for m in results.matches
            if m.score > 0.5
        ]
    except Exception as e:
        st.error(f"Search error: {e}")
        return []
  
  
def build_rules_context(query: str = "trading setup entry exit rules") -> str:
    """Pull relevant rules from memory and format as system context."""
    rules = search_memory(query, top_k=8)
    if not rules:
        return "No custom rules found in memory. Analyzing based on general technical analysis principles."
    lines = ["=== USER'S PERSONAL TRADING RULES (from memory) ==="]
    for r in rules:
        lines.append(f"\n[{r['rule_type']}] {r['rule_name']} (relevance: {r['score']:.2f})")
        lines.append(f"  → {r['rule_text']}")
    lines.append("\n=== END OF RULES ===")
    return "\n".join(lines)
  
  
# ══════════════════════════════════════════════════════════
# 3. FETCH CHART FROM TRADINGVIEW / YFINANCE
# ══════════════════════════════════════════════════════════

def get_chart_screenshot(ticker: str, period: str = "3mo") -> Image.Image:
    """Fetch historical data and create a candlestick chart image using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        st.error("❌ matplotlib not installed")
        return None

    if not HAS_YFINANCE:
        st.error("❌ yfinance not installed")
        return None

    try:
        if "." not in ticker:
            if ticker.upper() in ["NIFTY50", "BANKNIFTY"]:
                ticker = f"^{ticker.upper()}"
            else:
                ticker = f"{ticker.upper()}.NS"

        print(f"📊 Fetching {ticker} data...")
        hist = yf.Ticker(ticker).history(period=period, interval="1d")

        if hist.empty:
            st.error(f"❌ No data for {ticker}")
            return None

        fig, ax = plt.subplots(figsize=(14, 6), facecolor="#04080F")
        ax.set_facecolor("#04080F")
        for spine in ax.spines.values():
            spine.set_color("#0F2040")
        ax.tick_params(colors="#8A9AB5", labelsize=9)

        for i, (idx, row) in enumerate(hist.iterrows()):
            o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
            color = "#00B37A" if c >= o else "#E84545"
            ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
            body_bottom = min(o, c)
            body_height = abs(c - o) if abs(c - o) > 0 else 0.01
            rect = mpatches.FancyBboxPatch(
                (i - 0.3, body_bottom), 0.6, body_height,
                boxstyle="square,pad=0",
                facecolor=color, edgecolor=color,
                linewidth=0.5, zorder=2
            )
            ax.add_patch(rect)

        step = max(1, len(hist) // 10)
        tick_positions = list(range(0, len(hist), step))
        tick_labels = [hist.index[i].strftime("%d %b") for i in tick_positions]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, rotation=45, ha="right")
        ax.set_xlim(-1, len(hist))
        ax.set_title(f"{ticker} — {period.upper()} Daily Chart",
                     color="#C8A96A", fontsize=13, fontweight="bold", pad=12)
        ax.grid(axis="y", color="#0F2040", linewidth=0.5, linestyle="--", alpha=0.7)
        ax.grid(axis="x", color="#0F2040", linewidth=0.3, linestyle="--", alpha=0.4)
        plt.tight_layout(pad=1.5)

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150, facecolor="#04080F", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        img = Image.open(buf).convert("RGB")
        print(f"✅ Chart screenshot created: {img.size}")
        return img

    except Exception as e:
        st.error(f"❌ Error fetching chart: {str(e)}")
        traceback.print_exc()
        return None
  
# ══════════════════════════════════════════════════════════
# 4. CHART ANALYSIS ENGINE
# ══════════════════════════════════════════════════════════
 
def image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
  
  
def draw_annotations(img: Image.Image, analysis: dict) -> Image.Image:
    """Draw bounding boxes and arrows from AI response onto chart image."""
    draw   = ImageDraw.Draw(img, "RGBA")
    width  = img.width
    height = img.height
  
    for box in analysis.get("draw_boxes", []):
        try:
            x, y  = int(box["x"]), int(box["y"])
            w, h  = int(box.get("w", 60)), int(box.get("h", 30))
            color = box.get("color", "#00B8CC")
            label = box.get("label", "")
            r,g,b = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
            draw.rectangle([x, y, x+w, y+h], outline=(r,g,b,255), width=2,
                           fill=(r,g,b,40))
            if label:
                draw.text((x+3, y-14), label, fill=(r,g,b,255))
        except: pass
  
    for arrow in analysis.get("draw_arrows", []):
        try:
            x1,y1 = int(arrow["x1"]), int(arrow["y1"])
            x2,y2 = int(arrow["x2"]), int(arrow["y2"])
            color  = arrow.get("color", "#FF4444")
            label  = arrow.get("label", "")
            r,g,b  = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
            draw.line([x1,y1,x2,y2], fill=(r,g,b,255), width=2)
            draw.polygon([x2,y2, x2-8,y2-5, x2-8,y2+5],
                         fill=(r,g,b,200))
            if label:
                draw.text((x2+4, y2-10), label, fill=(r,g,b,255))
        except: pass
  
    return img
  
  
def analyze_chart(img: Image.Image, click_x: int = None,
                  click_y: int = None, user_note: str = "", ticker: str = "") -> dict:
    """
    Send chart image + click coordinates + rules context to Gemini.
    Returns parsed analysis dict.
    """
    model = get_gemini()
  
    # Build context from memory
    rules_ctx = build_rules_context(user_note or f"trading entry setup validation for {ticker}")
  
    # Build prompt
    click_info = ""
    if click_x is not None and click_y is not None:
        # Normalize coords to percentage of image
        pct_x = round((click_x / img.width)  * 100, 1)
        pct_y = round((click_y / img.height) * 100, 1)
        click_info = (
            f"\n\nUSER CLICKED AT: pixel ({click_x}, {click_y}) "
            f"= {pct_x}% from left, {pct_y}% from top.\n"
            f"Focus your primary analysis on the candlestick/area at this exact location."
        )
  
    prompt = f"""{rules_ctx}
{click_info}
TASK: Analyze this trading chart for {ticker}. {'Focus on the clicked area.' if click_x else 'Provide overall structure analysis.'}
{f'USER NOTE: {user_note}' if user_note else ''}
 
Return ONLY a valid JSON object matching the specified format. No markdown, no preamble.
Coordinates in draw_boxes/draw_arrows must be valid pixel positions matching the chart image size ({img.width}x{img.height}).
"""
  
    try:
        time.sleep(1)  # Rate limit protection
        img_data = {"mime_type": "image/png",
                    "data": image_to_base64(img)}
        response = model.generate_content([prompt, img_data])
        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {
            "verdict":          "FLAGGED",
            "score":            5,
            "voice_summary":    "Analysis complete. Check detailed section.",
            "detailed_analysis": response.text if 'response' in dir() else "Error parsing response.",
            "rules_matched":    [],
            "rules_violated":   [],
            "draw_boxes":       [],
            "draw_arrows":      []
        }
    except Exception as e:
        return {"verdict":"ERROR","score":0,"voice_summary":str(e),
                "detailed_analysis":str(e),"rules_matched":[],
                "rules_violated":[],"draw_boxes":[],"draw_arrows":[]}
  
  
# ══════════════════════════════════════════════════════════
# 5. PDF PARSER
# ══════════════════════════════════════════════════════════
 
def extract_pdf_rules(pdf_file) -> list[str]:
    """Extract text chunks from uploaded PDF."""
    try:
        chunks = []
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    # Split into chunks of ~500 chars
                    for i in range(0, len(text), 500):
                        chunk = text[i:i+500].strip()
                        if len(chunk) > 50:
                            chunks.append(chunk)
        return chunks
    except Exception as e:
        st.error(f"PDF error: {e}")
        return []
  
  
# ══════════════════════════════════════════════════════════
# 6. VOICE OUTPUT (Web Speech API)
# ══════════════════════════════════════════════════════════
 
def speak(text: str, rate: float = 0.95, pitch: float = 1.0):
    """Inject browser-side TTS via Web Speech API."""
    clean = text.replace('"', "'").replace('\n', ' ').replace('\\', '')
    st.markdown(f"""
    <script>
    (function() {{
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        var u = new SpeechSynthesisUtterance("{clean}");
        u.rate  = {rate};
        u.pitch = {pitch};
        u.lang  = "en-IN";
        var voices = window.speechSynthesis.getVoices();
        var pref   = voices.find(v => v.lang === "en-IN") ||
                     voices.find(v => v.lang.startsWith("en"));
        if (pref) u.voice = pref;
        window.speechSynthesis.speak(u);
    }})();
    </script>
    """, unsafe_allow_html=True)
  
  
# ══════════════════════════════════════════════════════════
# 7. VERDICT BADGE HTML
# ══════════════════════════════════════════════════════════
 
def verdict_badge(verdict: str, score: int) -> str:
    colors = {"VALID":"#00B37A","INVALID":"#E84545","FLAGGED":"#F5C518","ERROR":"#8A9AB5"}
    c = colors.get(verdict, "#8A9AB5")
    bar_w = int((score / 10) * 100)
    return f"""
<div style="background:#060D1A;border:1px solid {c};border-radius:14px;padding:20px;margin-bottom:16px;">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div style="background:{c};color:#04080F;font-family:Inter,sans-serif;
             font-weight:900;font-size:14px;letter-spacing:2px;padding:6px 16px;
             border-radius:8px;">{verdict}</div>
        <div style="font-family:JetBrains Mono,monospace;font-size:22px;
             font-weight:700;color:{c};">{score}/10</div>
    </div>
    <div style="background:#0F2040;border-radius:4px;height:6px;width:100%;">
        <div style="background:{c};width:{bar_w}%;height:6px;border-radius:4px;
             transition:width .5s;"></div>
    </div>
</div>"""
  
  
# ══════════════════════════════════════════════════════════
# 8. MAIN UI — MODE 1: AUTO CHART FETCHING & ANALYSIS
# ══════════════════════════════════════════════════════════
 
def render_mode1():
    st.markdown(f"""
    <div style="background:#060D1A;border:1px solid {GOLD}44;border-radius:16px;
         padding:20px 24px;margin-bottom:20px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:22px;
             letter-spacing:5px;color:{GOLD};">MODE 1 — AUTO CHART ANALYSIS</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;">
            Enter stock name → Auto fetch chart → AI analyzes it instantly
        </div>
    </div>
    """, unsafe_allow_html=True)
  
    # Initialize session state
    if "m1_ticker" not in st.session_state:
        st.session_state.m1_ticker = ""
    if "m1_chart_img" not in st.session_state:
        st.session_state.m1_chart_img = None
    if "m1_chart_fetched" not in st.session_state:
        st.session_state.m1_chart_fetched = False

    # Input row
    col_input, col_btn = st.columns([4, 1])
    
    with col_input:
        ticker_input = st.text_input(
            "Stock / Index",
            placeholder="e.g. RELIANCE, TCS, HDFCBANK, NIFTY50, BANKNIFTY...",
            value=st.session_state.m1_ticker,
            key="m1_ticker_input",
            label_visibility="collapsed"
        )
        st.session_state.m1_ticker = ticker_input
    
    with col_btn:
        fetch_btn = st.button("FETCH & ANALYZE", type="primary",
                             use_container_width=True, key="m1_fetch")

    # Quick picks
    st.markdown(
        f'<div style="font-size:11px;color:{T2};margin-bottom:6px;margin-top:4px;">'
        f'Quick picks:</div>',
        unsafe_allow_html=True
    )
    qp_cols = st.columns(8)
    quick_picks = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "HDFCBANK",
                   "INFY", "ICICIBANK", "TATASTEEL"]
    for i, qp in enumerate(quick_picks):
        with qp_cols[i % 8]:
            if st.button(qp, key=f"qp_{qp}", use_container_width=True):
                st.session_state.m1_ticker = qp
                st.rerun()

    # Determine what to load
    load_ticker = None
    if fetch_btn and ticker_input.strip():
        load_ticker = ticker_input.strip()
        st.session_state.m1_chart_fetched = False
    elif st.session_state.m1_ticker and not st.session_state.m1_chart_fetched:
        load_ticker = st.session_state.m1_ticker

    if not load_ticker:
        st.markdown(
            f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:12px;'
            f'padding:40px;text-align:center;margin-top:16px;">'
            f'<div style="font-family:Bebas Neue,sans-serif;font-size:28px;letter-spacing:6px;'
            f'color:{BORDER};margin-bottom:8px;">ENTER TICKER</div>'
            f'<div style="font-size:12px;color:{T2};">'
            f'Type ticker above or tap a quick pick</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        return

    # Fetch chart
    if not st.session_state.m1_chart_fetched:
        with st.spinner(f"📊 Fetching {load_ticker} chart..."):
            chart_img = get_chart_screenshot(load_ticker, period="3mo")
        
        if chart_img:
            st.session_state.m1_chart_img = chart_img
            st.session_state.m1_chart_fetched = True
        else:
            st.error(f"❌ Could not fetch chart for {load_ticker}")
            return

    if not st.session_state.m1_chart_img:
        return

    img = st.session_state.m1_chart_img

    # Display chart and analysis side by side
    col_chart, col_panel = st.columns([3, 2])

    with col_chart:
        st.markdown(f"<div style='font-size:12px;color:{T2};margin-bottom:6px;'>Click on any candle to analyze that specific area</div>",
                    unsafe_allow_html=True)

        # Clickable image
        coords = streamlit_image_coordinates(img, key="chart_click")
        click_x = coords["x"] if coords else None
        click_y = coords["y"] if coords else None

        if click_x:
            st.markdown(f"""
            <div style="background:{DARK3};border:1px solid {GOLD}44;border-radius:8px;
                 padding:8px 14px;margin-top:8px;font-family:JetBrains Mono,monospace;
                 font-size:12px;color:{GOLD};">
                Clicked: ({click_x}, {click_y}) px &nbsp;|&nbsp;
                {round((click_x/img.width)*100,1)}% X · {round((click_y/img.height)*100,1)}% Y
            </div>""", unsafe_allow_html=True)

    with col_panel:
        user_note = st.text_area("Add context (optional)",
                                 placeholder="e.g. 'Is this a valid breakout?'",
                                 height=80, key="m1_note")
        auto_voice = st.toggle("Auto-speak analysis", value=True, key="m1_voice")

        if st.button("Analyze Chart", type="primary", use_container_width=True, key="m1_analyze"):
            with st.spinner("🤖 Gemini is analyzing..."):
                result = analyze_chart(img, click_x, click_y, user_note, load_ticker)

            # Verdict badge
            st.markdown(verdict_badge(result.get("verdict","FLAGGED"),
                                      result.get("score", 5)),
                        unsafe_allow_html=True)

            # Voice
            if auto_voice:
                speak(result.get("voice_summary","Analysis complete."))

            # Draw annotations on chart
            if result.get("draw_boxes") or result.get("draw_arrows"):
                annotated = draw_annotations(img.copy(), result)
                with col_chart:
                    st.image(annotated, caption="AI Annotated Chart", use_container_width=True)

            # Rules matched/violated
            if result.get("rules_matched"):
                st.markdown(f"<div style='color:{GREEN};font-size:13px;font-weight:700;margin-top:8px;'>✅ Rules Matched</div>",
                            unsafe_allow_html=True)
                for r in result["rules_matched"]:
                    st.markdown(f"<div style='color:{GREEN};font-size:12px;margin-left:8px;'>· {r}</div>",
                                unsafe_allow_html=True)

            if result.get("rules_violated"):
                st.markdown(f"<div style='color:{RED};font-size:13px;font-weight:700;margin-top:8px;'>❌ Rules Violated</div>",
                            unsafe_allow_html=True)
                for r in result["rules_violated"]:
                    st.markdown(f"<div style='color:{RED};font-size:12px;margin-left:8px;'>· {r}</div>",
                                unsafe_allow_html=True)

            # Detailed analysis
            with st.expander("Full Analysis", expanded=True):
                st.markdown(f"""
                <div style="background:{DARK3};border-radius:10px;padding:16px;
                     font-size:13px;color:{IVORY};line-height:1.9;">
                {result.get('detailed_analysis','').replace(chr(10),'<br>')}
                </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 9. MAIN UI — MODE 2: TRAINING MODE
# ══════════════════════════════════════════════════════════
 
def render_mode2():
    st.markdown(f"""
    <div style="background:#060D1A;border:1px solid {GREEN}44;border-radius:16px;
         padding:20px 24px;margin-bottom:20px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:22px;
             letter-spacing:5px;color:{GREEN};">MODE 2 — TRAIN YOUR AI</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;">
            Teach Arka AI your setups. It remembers forever in vector memory.
        </div>
    </div>
    """, unsafe_allow_html=True)
  
    tab_manual, tab_chart, tab_pdf, tab_memory = st.tabs([
        "✍️ Add Rule", "📊 Annotate Chart", "📄 Upload PDF", "🧠 View Memory"
    ])
  
    # ── TAB 1: Manual Rule Entry ───────────────────────────
    with tab_manual:
        st.markdown(
            f"<div style='font-size:13px;color:{T2};margin-bottom:16px;'>"
            f"Add individual trading rules to AI memory</div>",
            unsafe_allow_html=True
        )
  
        # Use session state keys for auto-clear
        if "m2_rule_name" not in st.session_state: st.session_state.m2_rule_name = ""
        if "m2_rule_text" not in st.session_state: st.session_state.m2_rule_text = ""
  
        rule_name = st.text_input(
            "Rule Name",
            placeholder="e.g. PDH Breakout Confirmation",
            key="m2_rule_name"
        )
        rule_text = st.text_area(
            "Exact Conditions",
            placeholder="e.g. Price must close above PDH on breakout candle. Volume must be 1.5x average. RSI above 55.",
            height=130,
            key="m2_rule_text"
        )
  
        if st.button("SAVE TO MEMORY", use_container_width=True,
                     type="primary", key="m2_save_btn"):
            if rule_name.strip() and rule_text.strip():
                with st.spinner("Saving to Pinecone..."):
                    ok = save_rule_to_memory("", rule_name.strip(), rule_text.strip(), [])
                if ok:
                    # Auto-clear fields
                    st.session_state.m2_rule_name = ""
                    st.session_state.m2_rule_text = ""
                    st.success(f"Learned: **{rule_name}** — Applied in every future analysis.")
                    speak(f"Rule saved. I have permanently learned your {rule_name} setup.")
                    st.rerun()
                else:
                    st.error("Save failed. Check Pinecone connection in Streamlit Secrets.")
            else:
                st.warning("Fill in both Rule Name and Conditions.")
  
    # ── TAB 2: Chart Annotation ────────────────────────────
    with tab_chart:
        st.markdown(
            f"<div style='font-size:13px;color:{T2};margin-bottom:12px;'>"
            f"Upload a setup chart. Click on the key candle. AI learns the visual pattern.</div>",
            unsafe_allow_html=True
        )
  
        train_img_file = st.file_uploader(
            "Upload example setup chart",
            type=["png","jpg","jpeg"],
            key="train_chart"
        )
  
        tx, ty = None, None
  
        if train_img_file:
            raw_img    = Image.open(train_img_file).convert("RGB")
            orig_w, orig_h = raw_img.size
  
            # ── Render image at natural aspect ratio — no squishing
            if HAS_IMG_COORDS:
                st.markdown(
                    f"<div style='font-size:11px;color:{GOLD};margin-bottom:6px;'>"
                    f"Click on a key candle or zone to target it</div>",
                    unsafe_allow_html=True
                )
                # Pass the PIL image directly — no resizing, no height cap
                train_coords = streamlit_image_coordinates(
                    raw_img,
                    key="train_click",
                    use_column_width=True
                )
                if train_coords and train_coords.get("x") is not None:
                    tx = train_coords["x"]
                    ty = train_coords["y"]
                    st.markdown(
                        f"<div style='color:{GOLD};font-size:12px;"
                        f"font-family:monospace;margin-top:6px;'>"
                        f"Target locked: ({tx}, {ty}) — "
                        f"{round(tx/orig_w*100,1)}% H · {round(ty/orig_h*100,1)}% V</div>",
                        unsafe_allow_html=True
                    )
            else:
                st.image(raw_img, use_container_width=True)
                st.caption("Tip: install streamlit-image-coordinates for click targeting")
  
        # Input fields — outside image block so they always show
        if "m2_setup_name" not in st.session_state: st.session_state.m2_setup_name = ""
        if "m2_setup_rules" not in st.session_state: st.session_state.m2_setup_rules = ""
  
        setup_name  = st.text_input(
            "Setup Name",
            placeholder="e.g. Low Volume Handle — Cup and Handle",
            key="m2_setup_name"
        )
        setup_rules = st.text_area(
            "What should AI learn from this chart?",
            placeholder="e.g. Volume drops 40% during consolidation. Entry on breakout above left cup rim.",
            height=100,
            key="m2_setup_rules"
        )
  
        if st.button("TEACH THIS SETUP", type="primary",
                     use_container_width=True, key="m2_teach_btn"):
            if setup_name.strip() and setup_rules.strip():
                full_rule = setup_rules.strip()
                if tx and train_img_file:
                    full_rule += (
                        f"\n[Chart coordinate reference: click at ({tx},{ty}) = "
                        f"{round(tx/orig_w*100,1)}% X, {round(ty/orig_h*100,1)}% Y]"
                    )
                with st.spinner("Teaching Arka AI..."):
                    ok = save_rule_to_memory("", setup_name.strip(), full_rule, ["chart-trained"])
                if ok:
                    # Auto-clear
                    st.session_state.m2_setup_name  = ""
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
        st.markdown(
            f"<div style='font-size:13px;color:{T2};margin-bottom:12px;'>"
            f"Upload trading rules as PDF. AI reads and stores everything.</div>",
            unsafe_allow_html=True
        )
        pdf_file = st.file_uploader("Upload PDF", type=["pdf"], key="pdf_upload")
  
        if pdf_file and st.button("Extract & Learn from PDF", type="primary",
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
                    # Add delay between chunks to avoid rate limiting
                    time.sleep(2)
                    ok = save_rule_to_memory(
                        "",
                        f"{pdf_file.name} — Chunk {i+1}",
                        chunk,
                        ["pdf-trained", pdf_file.name[:30]]
                    )
                    if ok: saved += 1
                    progress.progress((i+1)/len(chunks))
                st.success(f"Learned {saved}/{len(chunks)} rule segments from {pdf_file.name}")
                speak(f"PDF processed. I have learned {saved} trading rules.")
  
    # ── TAB 4: Memory Viewer ───────────────────────────────
    with tab_memory:
        st.markdown(
            f"<div style='font-size:13px;color:{T2};margin-bottom:12px;'>"
            f"Browse everything Arka AI has learned</div>",
            unsafe_allow_html=True
        )
        query = st.text_input("Search memory", placeholder="e.g. volume breakout", key="mem_search")
        if query:
            with st.spinner("Searching..."):
                results = search_memory(query, top_k=10)
            if not results:
                st.info("No matching rules found.")
            else:
                st.success(f"Found {len(results)} matching rules:")
                for r in results:
                    sc = GREEN if r["score"] > 0.8 else GOLD if r["score"] > 0.6 else T2
                    st.markdown(f"""
                    <div style="background:{DARK3};border:1px solid {BORDER};
                         border-left:3px solid {sc};border-radius:10px;
                         padding:14px 16px;margin-bottom:8px;">
                        <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                            <span style="font-family:Inter,sans-serif;font-weight:800;
                                 font-size:13px;color:{IVORY};">{r['rule_name']}</span>
                            <span style="font-family:JetBrains Mono,monospace;font-size:11px;
                                 color:{sc};">{r['score']:.2f} match</span>
                        </div>
                        <div style="font-size:13px;color:{T2};line-height:1.7;">
                            {r['rule_text'][:300]}...</div>
                    </div>""", unsafe_allow_html=True)
        else:
            # Show all recent rules when no search query
            with st.spinner("Loading memory..."):
                all_rules = search_memory("trading setup rule entry exit", top_k=20)
            if all_rules:
                st.markdown(f"<div style='font-size:12px;color:{T2};margin-bottom:12px;'>"
                            f"Showing {len(all_rules)} stored rules:</div>", unsafe_allow_html=True)
                for r in all_rules:
                    st.markdown(f"""
                    <div style="background:{DARK3};border:1px solid {BORDER};
                         border-left:3px solid {GOLD};border-radius:10px;
                         padding:12px 16px;margin-bottom:6px;">
                        <div style="font-family:Inter,sans-serif;font-weight:800;
                             font-size:13px;color:{IVORY};margin-bottom:4px;">{r['rule_name']}</div>
                        <div style="font-size:13px;color:{T2};line-height:1.6;">
                            {r['rule_text'][:250]}...</div>
                    </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='color:{T2};font-size:13px;text-align:center;"
                            f"padding:40px;'>No rules stored yet. Use Add Rule or Annotate Chart to teach Arka AI.</div>",
                            unsafe_allow_html=True)

 
# ══════════════════════════════════════════════════════════
# 10. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════
  
def render_arka_ai():
    """Main Arka AI page — call from app.py page router."""
  
    # DEBUG: Show API key status
    with st.expander("🔧 API Status (Click to expand)", expanded=False):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("GEMINI_KEY", "✅ SET" if GEMINI_KEY else "❌ NOT SET")
        with col2:
            st.metric("PINECONE_KEY", "✅ SET" if PINECONE_KEY else "❌ NOT SET")
        with col3:
            st.metric("HAS_GEMINI", "✅ YES" if HAS_GEMINI else "❌ NO")
        with col4:
            st.metric("HAS_PINECONE", "✅ YES" if HAS_PINECONE else "❌ NO")
        
        if not GEMINI_KEY:
            st.error("⚠️ GEMINI_KEY is not set in Streamlit secrets!")
        if not PINECONE_KEY:
            st.error("⚠️ PINECONE_KEY is not set in Streamlit secrets!")
  
    st.markdown(f"""
    <div style="text-align:center;margin-bottom:24px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:36px;
             letter-spacing:8px;color:{GOLD};">ARKA AI</div>
        <div style="font-size:11px;letter-spacing:4px;color:{T2};
             text-transform:uppercase;margin-top:2px;">
             Zero-Emotion Chart Companion · Powered by Gemini 2.5 Flash</div>
    </div>
    """, unsafe_allow_html=True)
  
    mode = st.radio("Select Mode", ["Mode 1 — Auto Analysis", "Mode 2 — Train AI"],
                    horizontal=True, key="ai_mode")
  
    st.markdown(f"<div style='height:1px;background:{BORDER};margin:16px 0;'></div>",
                unsafe_allow_html=True)
  
    if mode == "Mode 1 — Auto Analysis":
        render_mode1()
    else:
        render_mode2()
