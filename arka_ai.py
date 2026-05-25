"""arka_ai.py  —  Arka AI Trading Companion
==========================================
Brain     : Gemini 2.5 Flash  (vision + text)
Memory    : Pinecone           (vector database)
Voice     : Web Speech API     (browser TTS)
Click     : streamlit-image-coordinates
PDF Parse : pdfplumber
"""
import streamlit as st
import base64, io, json, time, hashlib, requests
from datetime import datetime

# ── Safe imports ────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types
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

# ── Keys from Streamlit Secrets ────────────────────────────
GEMINI_KEY    = st.secrets.get("GEMINI_KEY",    "")
PINECONE_KEY  = st.secrets.get("PINECONE_KEY",  "")
PINECONE_HOST = st.secrets.get("PINECONE_HOST", "")
INDEX_NAME    = "arka-trading-rules"

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
@st.cache_resourced
def get_gemini_client():
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    try:
        return genai.Client(api_key=GEMINI_KEY)
    except Exception as e:
        return None

def get_system_instruction() -> str:
    return """You are Arka AI — an elite, zero-emotion technical analysis companion.
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

# ══════════════════════════════════════════════════════════
# 2. EMBEDDING — Direct Native 768-Dim REST Engine
# ══════════════════════════════════════════════════════════
def get_embedding(text: str) -> list:
    """Safely fetch a 768-dimension vector to prevent Pinecone dimension rejections."""
    if not GEMINI_KEY or not text or not text.strip():
        return None
        
    cleaned_text = text.strip()[:2000]
    
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/embedding-001:embedContent?key={GEMINI_KEY}"
        payload = {
            "model": "models/embedding-001",
            "contents": {"parts": [{"text": cleaned_text}]}
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            res_json = resp.json()
            if "embedding" in res_json and "values" in res_json["embedding"]:
                return res_json["embedding"]["values"]
        print(f"❌ REST Embedding Request Failed with status: {resp.status_code}")
    except Exception as e:
        print(f"❌ Direct embedding exception: {str(e)}")
        
    return None

# ══════════════════════════════════════════════════════════
# 3. PINECONE VECTOR MEMORY
# ══════════════════════════════════════════════════════════
@st.cache_resourced
def get_pinecone_index():
    if not HAS_PINECONE or not PINECONE_KEY:
        return None
    try:
        pc = Pinecone(api_key=PINECONE_KEY)
        if PINECONE_HOST:
            return pc.Index(name=INDEX_NAME, host=PINECONE_HOST.strip())
        existing = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in existing:
            pc.create_index(
                name=INDEX_NAME,
                dimension=768,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            time.sleep(3)
        return pc.Index(INDEX_NAME)
    except Exception as e:
        return None

def save_rule_to_memory(rule_type: str, rule_name: str, rule_text: str, tags: list = None) -> bool:
    idx = get_pinecone_index()
    if not idx:
        st.error("Pinecone not connected. Check PINECONE_KEY in Streamlit Secrets.")
        return False
    if not rule_text or not rule_text.strip():
        st.error("Cannot save empty rule.")
        return False
    try:
        full_text = f"{rule_name}\n{rule_text}".strip()
        embedding = get_embedding(full_text)
        if embedding is None or len(embedding) == 0:
            st.error("Engine returned an empty or invalid vector embedding framework.")
            return False
        vector_id = f"rule_{int(time.time())}_{hashlib.md5(rule_name.encode()).hexdigest()[:8]}"
        idx.upsert(vectors=[{
            "id":     vector_id,
            "values": embedding,
            "metadata": {
                "rule_type": rule_type or "",
                "rule_name": rule_name,
                "rule_text": rule_text,
                "tags":      json.dumps(tags or []),
                "saved_at":  datetime.now().isoformat()
            }
        }])
        return True
    except Exception as e:
        st.error(f"Save error: {str(e)}")
        return False

def search_memory(query: str, top_k: int = 5) -> list:
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        results = idx.query(vector=embedding, top_k=top_k, include_metadata=True)
        return [
            {
                "score":     m.score,
                "rule_type": m.metadata.get("rule_type", ""),
                "rule_name": m.metadata.get("rule_name", ""),
                "rule_text": m.metadata.get("rule_text", ""),
            }
            for m in results.matches if m.score > 0.4
        ]
    except Exception as e:
        return []

def build_rules_context(query: str = "trading setup entry exit rules") -> str:
    rules = search_memory(query, top_k=8)
    if not rules:
        return "No custom rules in memory. Analyzing on general technical analysis principles."
    lines = ["=== USER TRADING RULES (from memory) ==="]
    for r in rules:
        lines.append(f"\n[{r['rule_type']}] {r['rule_name']} (match: {r['score']:.2f})")
        lines.append(f"  → {r['rule_text']}")
    lines.append("\n=== END RULES ===")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════
# 4. AI CHART VISION — auto-extract notes from image
# ══════════════════════════════════════════════════════════
def generate_chart_notes(img: "Image.Image", user_note: str = "") -> str:
    client = get_gemini_client()
    if not client or not HAS_PIL:
        return ""
    try:
        prompt = (
            "You are analyzing a stock chart image for training purposes. "
            "Describe ONLY what you visually observe in this chart — "
            "candlestick patterns, volume behavior, trend structure, "
            "indicator readings, key price levels, and any notable anomalies. "
            "Be precise and factual. 4-6 bullet points maximum. "
            "No advice, no recommendations, no emotional language."
            + (f"\n\nUser's own observation: {user_note}" if user_note else "")
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[img, prompt]
        )
        return response.text.strip()
    except Exception as e:
        return ""

def analyze_chart(img: "Image.Image", click_x: int = None,                  click_y: int = None, user_note: str = "") -> dict:
    client = get_gemini_client()
    if not client:
        return {
            "verdict": "ERROR", "score": 0,
            "voice_summary": "Gemini client offline.",
            "detailed_analysis": "Check GEMINI_KEY in Streamlit Secrets.",
            "rules_matched": [], "rules_violated": [],
            "draw_boxes": [], "draw_arrows": []
        }
    rules_ctx = build_rules_context(user_note or "trading entry setup validation")
    click_info = ""
    if click_x is not None and click_y is not None:
        pct_x = round((click_x / img.width) * 100, 1)
        pct_y = round((click_y / img.height) * 100, 1)
        click_info = (
            f"\n\nUSER CLICKED AT: pixel ({click_x}, {click_y}) "
            f"= {pct_x}% from left, {pct_y}% from top.\n"
            f"Focus PRIMARY analysis on the candlestick at this exact location."
        )
    prompt = (
        f"{rules_ctx}{click_info}\n\n"
        f"TASK: Audit this chart against the user's rules above.\n"
        f"{'Focus on the clicked candle.' if click_x else 'Give overall structure audit.'}\n"
        f"{f'USER NOTE: {user_note}' if user_note else ''}\n\n"
        f"Return ONLY valid JSON. No markdown, no preamble.\n"
        f"Pixel coordinates in draw_boxes/draw_arrows must fit within {img.width}x{img.height}."
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[img, prompt],
            config=types.GenerateContentConfig(
                system_instruction=get_system_instruction(),
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text.strip())
    except Exception as e:
        return {
            "verdict": "ERROR", "score": 0,
            "voice_summary": str(e)[:100],
            "detailed_analysis": str(e),
            "rules_matched": [], "rules_violated": [],
            "draw_boxes": [], "draw_arrows": []
        }

def draw_annotations(img: "Image.Image", analysis: dict) -> "Image.Image":
    if not HAS_PIL:
        return img
    draw = ImageDraw.Draw(img, "RGBA")
    for box in analysis.get("draw_boxes", []):
        try:
            x, y = int(box["x"]), int(box["y"])
            w, h = int(box.get("w", 60)), int(box.get("h", 30))
            color = box.get("color", "#00B8CC")
            label = box.get("label", "")
            r, g, b = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
            draw.rectangle([x,y,x+w,y+h], outline=(r,g,b,255), width=2, fill=(r,g,b,35))
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
            draw.polygon([x2,y2, x2-8,y2-5, x2-8,y2+5], fill=(r,g,b,200))
            if label:
                draw.text((x2+4, y2-10), label, fill=(r,g,b,255))
        except: pass
    return img

# ══════════════════════════════════════════════════════════
# 5. PDF PARSER
# ══════════════════════════════════════════════════════════
def extract_pdf_rules(pdf_file) -> list:
    if not HAS_PDF:
        return []
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
    clean = text.replace('"', "'").replace('\n', ' ').replace('\\', '')[:500]
    st.markdown(f"""
    <script>
    (function() {{
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        var u = new SpeechSynthesisUtterance("{clean}");
        u.rate={rate}; u.pitch={pitch}; u.lang="en-IN";
        var v=window.speechSynthesis.getVoices();
        var p=v.find(x=>x.lang==="en-IN")||v.find(x=>x.lang.startsWith("en"));
        if(p) u.voice=p;
        window.speechSynthesis.speak(u);
    }})();
    </script>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════
# 7. VERDICT BADGE
# ══════════════════════════════════════════════════════════
def verdict_badge(verdict: str, score: int) -> str:
    colors = {"VALID": GREEN, "INVALID": RED, "FLAGGED": GOLD, "ERROR": T2}
    c = colors.get(verdict, T2)
    bar_w = int((score / 10) * 100)
    return f"""<div style="background:{DARK2};border:1px solid {c};border-radius:14px; 
    padding:20px;margin-bottom:16px;">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div style="background:{c};color:{DARK};font-family:Inter,sans-serif; 
             font-weight:900;font-size:14px;letter-spacing:2px; 
             padding:6px 16px;border-radius:8px;">{verdict}</div>
        <div style="font-family:JetBrains Mono,monospace;font-size:22px; 
             font-weight:700;color:{c};">{score}/10</div>
    </div>
    <div style="background:{BORDER};border-radius:4px;height:6px;width:100%;">
        <div style="background:{c};width:{bar_w}%;height:6px;border-radius:4px;"></div>
    </div>
</div>"""

# ══════════════════════════════════════════════════════════
# 8. MODE 1 — LIVE CHART ANALYSIS
# ══════════════════════════════════════════════════════════
def render_mode1():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {GOLD}44;border-radius:16px; 
         padding:20px 24px;margin-bottom:20px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:22px; 
             letter-spacing:5px;color:{GOLD};">MODE 1 — LIVE CHART ANALYSIS</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;">
            Upload chart → click on any candle → AI audits against your rules
        </div>
    </div>""", unsafe_allow_html=True)
    uploaded = st.file_uploader(
        "Upload chart screenshot (PNG/JPG)",
        type=["png","jpg","jpeg"], key="m1_upload"
    )
    if not uploaded:
        st.info("Upload a chart screenshot to begin analysis.")
        return
    img = Image.open(uploaded).convert("RGB")
    col_chart, col_panel = st.columns([3, 2])
    with col_chart:
        st.markdown(
            f"<div style='font-size:12px;color:{T2};margin-bottom:6px;'>"
            f"Click on any candle to focus the analysis on that exact area</div>",
            unsafe_allow_html=True
        )
        if HAS_IMG_COORDS:
            coords  = streamlit_image_coordinates(img, key="chart_click", use_column_width=True)
            click_x = coords["x"] if coords else None
            click_y = coords["y"] if coords else None
        else:
            st.image(img, use_container_width=True)
            click_x = click_y = None
        if click_x:
            st.markdown(f"""
            <div style="background:{DARK3};border:1px solid {GOLD}44;border-radius:8px; 
                 padding:8px 14px;margin-top:8px;font-family:JetBrains Mono,monospace; 
                 font-size:12px;color:{GOLD};">
                 Target: ({click_x}, {click_y}) px &nbsp;·&nbsp; 
                 {round((click_x/img.width)*100,1)}% X &nbsp;·&nbsp; 
                 {round((click_y/img.height)*100,1)}% Y
            </div>""", unsafe_allow_html=True)
    with col_panel:
        user_note  = st.text_area(            "Add context (optional)",            placeholder="e.g. Is this a valid PDH breakout?",            height=80, key="m1_note"        )        auto_voice = st.toggle("Auto-speak analysis", value=True, key="m1_voice")        if st.button("ANALYZE CHART", type="primary",                     use_container_width=True, key="m1_analyze"):            with st.spinner("Gemini is reading your chart..."):                result = analyze_chart(img, click_x, click_y, user_note)            st.markdown(                verdict_badge(result.get("verdict","FLAGGED"), result.get("score", 5)),                unsafe_allow_html=True            )            if auto_voice:                speak(result.get("voice_summary", "Analysis complete."))            if result.get("draw_boxes") or result.get("draw_arrows"):                annotated = draw_annotations(img.copy(), result)                with col_chart:                    st.image(annotated, caption="AI Annotated Chart",                             use_container_width=True)            if result.get("rules_matched"):                st.markdown(                    f"<div style='color:{GREEN};font-size:13px;font-weight:700;"                    f"margin-top:8px;'>✅ Rules Matched</div>", unsafe_allow_html=True                )                for r in result["rules_matched"]:                    st.markdown(                        f"<div style='color:{GREEN};font-size:12px;margin-left:8px;'>"                        f"· {r}</div>", unsafe_allow_html=True                    )            if result.get("rules_violated"):                st.markdown(                    f"<div style='color:{RED};font-size:13px;font-weight:700;"                    f"margin-top:8px;'>❌ Rules Violated</div>", unsafe_allow_html=True                )                for r in result["rules_violated"]:                    st.markdown(                        f"<div style='color:{RED};font-size:12px;margin-left:8px;'>"                        f"· {r}</div>", unsafe_allow_html=True                    )            with st.expander("Full Detailed Analysis", expanded=True):                st.markdown(f"""                <div style="background:{DARK3};border-radius:10px;padding:16px; 
                     font-size:13px;color:{IVORY};line-height:1.9;">
                {result.get('detailed_analysis','').replace(chr(10),'<br>')}
                </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════
# 9. MODE 2 — TRAINING MODE
# ══════════════════════════════════════════════════════════
def render_mode2():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {GREEN}44;border-radius:16px; 
         padding:20px 24px;margin-bottom:20px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:22px; 
             letter-spacing:5px;color:{GREEN};">MODE 2 — TRAIN YOUR AI</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;">
            Teach Arka AI your setups. It remembers forever in vector memory.
        </div>
    </div>""", unsafe_allow_html=True)
    tab_manual, tab_chart, tab_pdf, tab_memory = st.tabs([
        "✍️ Add Rule", "📊 Annotate Chart", "📄 Upload PDF", "🧠 View Memory"
    ])
    
    # ── TAB 1: MANUAL RULE ────────────────────────────────
    with tab_manual:
        st.markdown(            f"<div style='font-size:13px;color:{T2};margin-bottom:16px;'>"            f"Add individual trading rules to AI memory</div>",            unsafe_allow_html=True        )        rule_name = st.text_input(            "Rule Name",            placeholder="e.g. PDH Breakout Confirmation",            key="m2_manual_rule_name"        )        rule_text = st.text_area(            "Exact Conditions",            placeholder="e.g. Price must close above PDH. Volume must be 1.5x average. RSI above 55.",            height=130,            key="m2_manual_rule_text"        )        if st.button("SAVE TO MEMORY", use_container_width=True,                     type="primary", key="m2_save_btn"):            if rule_name.strip() and rule_text.strip():                with st.spinner("Saving to Pinecone..."):                    ok = save_rule_to_memory("Manual", rule_name.strip(),                                             rule_text.strip(), [])                if ok:                    st.success(f"Learned: **{rule_name}**")                    speak(f"Rule saved. I have learned your {rule_name} setup.")                    st.rerun()            else:                st.warning("Fill in both Rule Name and Conditions.")
                
    # ── TAB 2: ANNOTATE CHART ─────────────────────────────
    with tab_chart:
        st.markdown(            f"<div style='font-size:13px;color:{T2};margin-bottom:12px;'>"            f"Upload a setup chart. AI auto-reads the image AND your notes. "            f"Click on key zones to add coordinate context.</div>",            unsafe_allow_html=True        )        train_img_file = st.file_uploader(            "Upload example setup chart",            type=["png","jpg","jpeg"],            key="train_chart"        )        tx, ty, orig_w, orig_h = None, None, 800, 600        if train_img_file:            raw_img = Image.open(train_img_file).convert("RGB")            orig_w, orig_h = raw_img.size            if HAS_IMG_COORDS:                st.markdown(                    f"<div style='font-size:11px;color:{GOLD};margin-bottom:4px;'>"                    f"Click on key candle or zone to lock coordinates</div>",                    unsafe_allow_html=True                )                train_coords = streamlit_image_coordinates(                    raw_img, key="train_click", use_column_width=True                )                if train_coords and train_coords.get("x") is not None:                    tx = train_coords["x"]                    ty = train_coords["y"]                    st.markdown(                        f"<div style='color:{GOLD};font-size:12px;"                        f"font-family:monospace;margin-top:6px;'>"                        f"Target locked: ({tx}, {ty}) — "                        f"{round(tx/orig_w*100,1)}% H · {round(ty/orig_h*100,1)}% V</div>",                        unsafe_allow_html=True                    )            else:                st.image(raw_img, use_container_width=True)        setup_name = st.text_input(            "Setup Name",            placeholder="e.g. Low Volume Consolidation Handle",            key="m2_setup_name_input"        )        setup_rules = st.text_area(            "Your observations (optional — AI will also read the chart itself)",            placeholder="e.g. Volume drops 40% during consolidation. Entry on breakout above left cup rim.",            height=100,            key="m2_setup_rules_input"        )        if st.button("TEACH THIS SETUP", type="primary",                     use_container_width=True, key="m2_teach_btn"):            if setup_name.strip():                if not train_img_file:                    st.warning("Upload a chart image first.")                else:                    with st.spinner("AI reading chart image and saving to memory..."):                        raw_img_for_notes = Image.open(train_img_file).convert("RGB")                        ai_notes = generate_chart_notes(                            raw_img_for_notes,                            user_note=setup_rules.strip()                        )                        combined = ""                        if setup_rules.strip():                            combined += f"USER NOTES:\n{setup_rules.strip()}\n\n"                        if ai_notes:                            combined += f"AI VISUAL OBSERVATIONS:\n{ai_notes}"                        if not combined.strip():                            combined = f"Visual setup: {setup_name}"                        if tx:                            combined += (                                f"\n\nCHART COORDINATE: ({tx},{ty}) = "                                f"{round(tx/orig_w*100,1)}% X, "                                f"{round(ty/orig_h*100,1)}% Y"                            )                        ok = save_rule_to_memory(                            "Visual-Pattern",                            setup_name.strip(),                            combined,                            ["chart-trained"]                        )                    if ok:                        if ai_notes:                            st.markdown(f"""                            <div style="background:{DARK3};border:1px solid {GREEN}44; 
                                 border-left:3px solid {GREEN};border-radius:10px; 
                                 padding:14px 16px;margin-bottom:8px;">
                                <div style="font-family:Inter,sans-serif;font-weight:800; 
                                     font-size:12px;color:{GREEN};letter-spacing:1px; 
                                     margin-bottom:8px;">AI AUTO-EXTRACTED NOTES</div>
                                <div style="font-size:12px;color:{IVORY};line-height:1.8;">
                                {ai_notes.replace(chr(10),'<br>')}
                                </div>
                            </div>""", unsafe_allow_html=True)                        st.success(f"Taught: {setup_name} — saved to memory with AI visual notes!")                        speak(f"Understood. I have learned the {setup_name} setup including my own visual observations.")                        st.rerun()            else:                st.warning("Enter a Setup Name.")

    # ── TAB 3: PDF UPLOAD ─────────────────────────────────
    with tab_pdf:
        st.markdown(            f"<div style='font-size:13px;color:{T2};margin-bottom:12px;'>"            f"Upload trading rules as PDF. AI reads and stores everything.</div>",            unsafe_allow_html=True        )        pdf_file = st.file_uploader("Upload PDF", type=["pdf"], key="pdf_upload")        if pdf_file and st.button("Extract & Learn from PDF", type="primary",                                  use_container_width=True, key="pdf_learn_btn"):            with st.spinner("Reading PDF..."):                chunks = extract_pdf_rules(pdf_file)            if not chunks:                st.error("No text found in PDF.")            else:                progress = st.progress(0)                saved = 0                for i, chunk in enumerate(chunks):                    ok = save_rule_to_memory(                        "PDF-Source",                        f"{pdf_file.name[:15]}—Ch.{i+1}",                        chunk, ["pdf-trained"]                    )                    if ok: saved += 1                    progress.progress((i+1)/len(chunks))                st.success(f"Learned {saved}/{len(chunks)} rule segments.")                speak(f"PDF processed. I have learned {saved} rules.")                
    # ── TAB 4: VIEW MEMORY ────────────────────────────────
    with tab_memory:
        st.markdown(            f"<div style='font-size:13px;color:{T2};margin-bottom:12px;'>"            f"Browse everything Arka AI has learned</div>",            unsafe_allow_html=True        )        query = st.text_input(            "Search memory",            placeholder="e.g. volume breakout consolidation",            key="mem_search"        )        search_query = query if query else "trading setup rule entry exit"        with st.spinner("Loading..."):            results = search_memory(search_query, top_k=15)        if not results:            st.markdown(                f"<div style='color:{T2};font-size:13px;text-align:center;padding:40px;'>"                f"No rules stored yet. Use Add Rule or Annotate Chart.</div>",                unsafe_allow_html=True            )        else:            st.markdown(                f"<div style='font-size:12px;color:{T2};margin-bottom:12px;'>"                f"Showing {len(results)} rules:</div>",                unsafe_allow_html=True            )            for r in results:                sc = GREEN if r["score"] > 0.8 else GOLD if r["score"] > 0.6 else T2                st.markdown(f"""                <div style="background:{DARK3};border:1px solid {BORDER}; 
                     border-left:3px solid {sc};border-radius:10px; 
                     padding:14px 16px;margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                        <span style="font-family:Inter,sans-serif;font-weight:800; 
                             font-size:13px;color:{IVORY};">{r['rule_name']}</span>
                        <span style="font-family:JetBrains Mono,monospace; 
                             font-size:11px;color:{sc};">{r['score']:.2f}</span>
                    </div>
                    <div style="font-size:12px;color:{T2};line-height:1.7;">
                        {r['rule_text'][:350]}{'...' if len(r['rule_text'])>350 else ''}
                    </div>
                </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════
# 10. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════
def render_arka_ai():
    """Main Arka AI page — called from app.py page router."""
    with st.expander("API Status", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Gemini Key",    "SET" if GEMINI_KEY    else "MISSING")
        c2.metric("Pinecone Key",  "SET" if PINECONE_KEY  else "MISSING")
        c3.metric("Gemini Lib",    "OK"  if HAS_GEMINI    else "NOT INSTALLED")
        c4.metric("Pinecone Lib",  "OK"  if HAS_PINECONE  else "NOT INSTALLED")
        
    st.markdown(f"""
    <div style="text-align:center;margin-bottom:24px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:36px; 
             letter-spacing:8px;color:{GOLD};">ARKA AI</div>
        <div style="font-size:11px;letter-spacing:4px;color:{T2}; 
             text-transform:uppercase;margin-top:2px;">
             Zero-Emotion Chart Companion · Gemini 2.5 Flash · Pinecone Memory</div>
        <div style="font-size:11px;color:{T2};margin-top:6px;font-style:italic;">
             Not SEBI registered. Educational use only.
        </div>
    </div>""", unsafe_allow_html=True)
    
    mode = st.radio(
        "Select Mode",
        ["Mode 1 — Live Analysis", "Mode 2 — Train AI"],
        horizontal=True, key="ai_mode"
    )
    st.markdown(
        f"<div style='height:1px;background:{BORDER};margin:16px 0;'></div>",
        unsafe_allow_html=True
    )
    if mode == "Mode 1 — Live Analysis":
        render_mode1()
    else:
        render_mode2()
