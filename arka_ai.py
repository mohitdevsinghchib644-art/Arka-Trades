"""
arka_ai.py — Arka AI Trading Companion
Brain: Gemini 2.5 Flash | Memory: Pinecone | Voice: Web Speech API
"""
import streamlit as st
import json, time, hashlib, requests
from datetime import datetime

# ── Safe imports ─────────────────────────────────────────
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

# ── Secrets ──────────────────────────────────────────────
GEMINI_KEY   = st.secrets.get("GEMINI_KEY",   "")
PINECONE_KEY = st.secrets.get("PINECONE_KEY", "")
INDEX_NAME   = "arka-trading-rules"

# ── Colors ────────────────────────────────────────────────
GOLD = "#C8A96A"; GREEN = "#00B37A"; RED = "#E84545"
DARK = "#04080F"; DARK2 = "#060D1A"; DARK3 = "#091525"
BORDER = "#0F2040"; T2 = "#8A9AB5"; IVORY = "#F7EBE0"

# ══════════════════════════════════════════
# 1. GEMINI CLIENT
# ══════════════════════════════════════════
@st.cache_resource
def get_gemini_client():
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    try:
        return genai.Client(api_key=GEMINI_KEY)
    except:
        return None

SYSTEM_PROMPT = """You are Arka AI — an elite, zero-emotion technical analysis companion.
RULES:
- No emotional phrases. No "Great!", "Nice!", "Good job!"
- Be precise, direct, surgical
- Reference exact visual locations on chart
- Apply user's personal rules from memory
- NOT SEBI registered — educational only
- Responses must be voice-optimized: punchy, under 150 words

OUTPUT FORMAT — return ONLY valid JSON:
{
  "verdict": "VALID | INVALID | FLAGGED",
  "score": 7,
  "voice_summary": "2-3 sentence voice response",
  "detailed_analysis": "Full breakdown",
  "rules_matched": ["rule1"],
  "rules_violated": ["rule2"],
  "draw_boxes": [{"x":100,"y":200,"w":80,"h":40,"color":"#00B8CC","label":"Zone"}],
  "draw_arrows": [{"x1":150,"y1":300,"x2":200,"y2":250,"color":"#FF4444","label":"SL"}]
}"""

# ══════════════════════════════════════════
# 2. EMBEDDING — REST API (correct payload)
# ══════════════════════════════════════════
def get_embedding(text: str) -> list:
    if not GEMINI_KEY or not text or not text.strip():
        return None
    try:
        client = get_gemini_client()
        if not client:
            return None
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text.strip()[:2000]
        )
        return result.embeddings[0].values
    except Exception as e:
        st.error(f"Embedding error: {e}")
        return None

# ══════════════════════════════════════════
# 3. PINECONE
# ══════════════════════════════════════════
@st.cache_resource
def get_pinecone_index():
    if not HAS_PINECONE or not PINECONE_KEY:
        return None
    try:
        pc = Pinecone(api_key=PINECONE_KEY)
        existing = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in existing:
            pc.create_index(
                name=INDEX_NAME, dimension=3072, metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            time.sleep(3)
        return pc.Index(INDEX_NAME)
    except Exception as e:
        st.error(f"Pinecone init error: {e}")
        return None

def save_rule_to_memory(rule_type: str, rule_name: str, rule_text: str, tags: list = None) -> bool:
    idx = get_pinecone_index()
    if not idx:
        st.error("Pinecone not connected.")
        return False
    if not rule_text.strip():
        st.error("Empty rule text.")
        return False
    try:
        embedding = get_embedding(f"{rule_name}\n{rule_text}")
        if not embedding:
            return False
        vid = f"rule_{int(time.time())}_{hashlib.md5(rule_name.encode()).hexdigest()[:8]}"
        idx.upsert(vectors=[{
            "id": vid,
            "values": embedding,
            "metadata": {
                "rule_type": rule_type or "",
                "rule_name": rule_name,
                "rule_text": rule_text,
                "tags": json.dumps(tags or []),
                "saved_at": datetime.now().isoformat()
            }
        }])
        return True
    except Exception as e:
        st.error(f"Save error: {e}")
        return False

def search_memory(query: str, top_k: int = 8) -> list:
    idx = get_pinecone_index()
    if not idx:
        return []
    try:
        emb = get_embedding(query)
        if not emb:
            return []
        res = idx.query(vector=emb, top_k=top_k, include_metadata=True)
        return [
            {
                "score":     m.score,
                "rule_type": m.metadata.get("rule_type", ""),
                "rule_name": m.metadata.get("rule_name", ""),
                "rule_text": m.metadata.get("rule_text", ""),
            }
            for m in res.matches if m.score > 0.3
        ]
    except:
        return []

def build_rules_context(query: str = "trading setup entry exit rules") -> str:
    rules = search_memory(query, top_k=8)
    if not rules:
        return "No custom rules in memory. Using general TA principles."
    lines = ["=== YOUR TRADING RULES ==="]
    for r in rules:
        lines.append(f"\n[{r['rule_type']}] {r['rule_name']} ({r['score']:.2f})")
        lines.append(f"  → {r['rule_text']}")
    lines.append("\n=== END RULES ===")
    return "\n".join(lines)

# ══════════════════════════════════════════
# 4. CHART VISION
# ══════════════════════════════════════════
def generate_chart_notes(img, user_note: str = "") -> str:
    """AI reads chart image and auto-extracts observations."""
    client = get_gemini_client()
    if not client or not HAS_PIL:
        return ""
    try:
        prompt = (
            "Analyze this stock chart image. Describe ONLY what you visually observe: "
            "candlestick patterns, volume behavior, trend structure, indicator readings, "
            "key price levels, and notable anomalies. "
            "4-6 precise bullet points. No advice. No emotional language."
            + (f"\n\nUser observation to confirm or contrast: {user_note}" if user_note else "")
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash", contents=[img, prompt]
        )
        return resp.text.strip()
    except Exception as e:
        return f"[AI vision error: {e}]"

def analyze_chart(img, click_x=None, click_y=None, user_note="") -> dict:
    client = get_gemini_client()
    if not client:
        return {"verdict":"ERROR","score":0,"voice_summary":"Gemini offline.",
                "detailed_analysis":"Check GEMINI_KEY.","rules_matched":[],"rules_violated":[],
                "draw_boxes":[],"draw_arrows":[]}
    rules_ctx = build_rules_context(user_note or "trading entry setup validation")
    click_info = ""
    if click_x and click_y:
        pct_x = round(click_x/img.width*100, 1)
        pct_y = round(click_y/img.height*100, 1)
        click_info = (
            f"\n\nUSER CLICKED: pixel ({click_x},{click_y}) = {pct_x}% left, {pct_y}% top. "
            f"Focus PRIMARY analysis on the candlestick at this exact location."
        )
    prompt = (
        f"{rules_ctx}{click_info}\n\n"
        f"TASK: Audit this chart against the rules above.\n"
        f"{f'USER NOTE: {user_note}' if user_note else ''}\n"
        f"Return ONLY valid JSON. No markdown. No preamble.\n"
        f"Coordinates must fit within {img.width}x{img.height}."
    )
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[img, prompt],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json"
            )
        )
        return json.loads(resp.text.strip())
    except Exception as e:
        return {"verdict":"ERROR","score":0,"voice_summary":str(e)[:100],
                "detailed_analysis":str(e),"rules_matched":[],"rules_violated":[],
                "draw_boxes":[],"draw_arrows":[]}

def draw_annotations(img, analysis: dict):
    if not HAS_PIL:
        return img
    draw = ImageDraw.Draw(img, "RGBA")
    for box in analysis.get("draw_boxes", []):
        try:
            x,y,w,h = int(box["x"]),int(box["y"]),int(box.get("w",60)),int(box.get("h",30))
            c = box.get("color","#00B8CC")
            r,g,b = int(c[1:3],16),int(c[3:5],16),int(c[5:7],16)
            draw.rectangle([x,y,x+w,y+h], outline=(r,g,b,255), width=2, fill=(r,g,b,35))
            if box.get("label"): draw.text((x+3,y-14), box["label"], fill=(r,g,b,255))
        except: pass
    for arrow in analysis.get("draw_arrows", []):
        try:
            x1,y1,x2,y2 = int(arrow["x1"]),int(arrow["y1"]),int(arrow["x2"]),int(arrow["y2"])
            c = arrow.get("color","#FF4444")
            r,g,b = int(c[1:3],16),int(c[3:5],16),int(c[5:7],16)
            draw.line([x1,y1,x2,y2], fill=(r,g,b,255), width=2)
            draw.polygon([x2,y2,x2-8,y2-5,x2-8,y2+5], fill=(r,g,b,200))
            if arrow.get("label"): draw.text((x2+4,y2-10), arrow["label"], fill=(r,g,b,255))
        except: pass
    return img

# ══════════════════════════════════════════
# 5. PDF
# ══════════════════════════════════════════
def extract_pdf_rules(pdf_file) -> list:
    if not HAS_PDF: return []
    try:
        chunks = []
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    for i in range(0, len(text), 500):
                        chunk = text[i:i+500].strip()
                        if len(chunk) > 50: chunks.append(chunk)
        return chunks
    except Exception as e:
        st.error(f"PDF error: {e}"); return []

# ══════════════════════════════════════════
# 6. VOICE
# ══════════════════════════════════════════
def speak(text: str):
    clean = text.replace('"',"'").replace('\n',' ').replace('\\',' ')[:500]
    st.markdown(f"""<script>
    (function(){{
        if(!window.speechSynthesis)return;
        window.speechSynthesis.cancel();
        var u=new SpeechSynthesisUtterance("{clean}");
        u.rate=0.95;u.pitch=1.0;u.lang="en-IN";
        var v=window.speechSynthesis.getVoices();
        var p=v.find(x=>x.lang==="en-IN")||v.find(x=>x.lang.startsWith("en"));
        if(p)u.voice=p;
        window.speechSynthesis.speak(u);
    }})();
    </script>""", unsafe_allow_html=True)

# ══════════════════════════════════════════
# 7. VERDICT BADGE
# ══════════════════════════════════════════
def verdict_badge(verdict: str, score: int) -> str:
    colors = {"VALID":GREEN,"INVALID":RED,"FLAGGED":GOLD,"ERROR":T2}
    c = colors.get(verdict, T2)
    bw = int((score/10)*100)
    return (
        f'<div style="background:{DARK2};border:1px solid {c};border-radius:14px;padding:20px;margin-bottom:16px;">'
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">'
        f'<div style="background:{c};color:{DARK};font-family:Inter,sans-serif;font-weight:900;'
        f'font-size:14px;letter-spacing:2px;padding:6px 16px;border-radius:8px;">{verdict}</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:22px;font-weight:700;color:{c};">{score}/10</div>'
        f'</div>'
        f'<div style="background:{BORDER};border-radius:4px;height:6px;width:100%;">'
        f'<div style="background:{c};width:{bw}%;height:6px;border-radius:4px;"></div>'
        f'</div></div>'
    )

# ══════════════════════════════════════════
# 8. MODE 1 — LIVE ANALYSIS
# ══════════════════════════════════════════
def render_mode1():
    st.markdown(
        f'<div style="background:{DARK2};border:1px solid {GOLD}44;border-radius:16px;'
        f'padding:20px 24px;margin-bottom:20px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:22px;letter-spacing:5px;color:{GOLD};">'
        f'MODE 1 — LIVE CHART ANALYSIS</div>'
        f'<div style="font-size:13px;color:{T2};margin-top:4px;">'
        f'Upload chart → click candle → AI audits against your rules</div></div>',
        unsafe_allow_html=True
    )

    uploaded = st.file_uploader("Upload chart (PNG/JPG)", type=["png","jpg","jpeg"], key="m1_upload")
    if not uploaded:
        st.info("Upload a chart screenshot to begin.")
        return

    img = Image.open(uploaded).convert("RGB")
    col_chart, col_panel = st.columns([3, 2])
    click_x = click_y = None

    with col_chart:
        st.markdown(
            f'<div style="font-size:12px;color:{T2};margin-bottom:6px;">'
            f'Click on any candle to target it for analysis</div>',
            unsafe_allow_html=True
        )
        if HAS_IMG_COORDS:
            coords = streamlit_image_coordinates(img, key="chart_click", use_column_width=True)
            if coords and coords.get("x") is not None:
                click_x, click_y = coords["x"], coords["y"]
                st.markdown(
                    f'<div style="background:{DARK3};border:1px solid {GOLD}44;'
                    f'border-radius:8px;padding:8px 14px;margin-top:8px;'
                    f'font-family:JetBrains Mono,monospace;font-size:12px;color:{GOLD};">'
                    f'Target: ({click_x},{click_y}) px · '
                    f'{round(click_x/img.width*100,1)}% X · {round(click_y/img.height*100,1)}% Y'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.image(img, use_container_width=True)

    with col_panel:
        user_note  = st.text_area("Context (optional)",
                                   placeholder="e.g. Is this a valid PDH breakout?",
                                   height=80, key="m1_note")
        auto_voice = st.toggle("Auto-speak result", value=True, key="m1_voice")

        if st.button("ANALYZE CHART", type="primary", use_container_width=True, key="m1_analyze"):
            with st.spinner("Gemini reading chart..."):
                result = analyze_chart(img, click_x, click_y, user_note)

            st.markdown(verdict_badge(result.get("verdict","FLAGGED"), result.get("score",5)),
                        unsafe_allow_html=True)
            if auto_voice:
                speak(result.get("voice_summary","Analysis complete."))

            if result.get("draw_boxes") or result.get("draw_arrows"):
                annotated = draw_annotations(img.copy(), result)
                with col_chart:
                    st.image(annotated, caption="AI Annotated", use_container_width=True)

            if result.get("rules_matched"):
                st.markdown(f'<div style="color:{GREEN};font-size:13px;font-weight:700;margin-top:8px;">Rules Matched</div>', unsafe_allow_html=True)
                for r in result["rules_matched"]:
                    st.markdown(f'<div style="color:{GREEN};font-size:12px;margin-left:8px;">· {r}</div>', unsafe_allow_html=True)

            if result.get("rules_violated"):
                st.markdown(f'<div style="color:{RED};font-size:13px;font-weight:700;margin-top:8px;">Rules Violated</div>', unsafe_allow_html=True)
                for r in result["rules_violated"]:
                    st.markdown(f'<div style="color:{RED};font-size:12px;margin-left:8px;">· {r}</div>', unsafe_allow_html=True)

            with st.expander("Full Analysis", expanded=True):
                st.markdown(
                    f'<div style="background:{DARK3};border-radius:10px;padding:16px;'
                    f'font-size:13px;color:{IVORY};line-height:1.9;">'
                    + result.get("detailed_analysis","").replace("\n","<br>")
                    + "</div>",
                    unsafe_allow_html=True
                )

# ══════════════════════════════════════════
# 9. MODE 2 — TRAINING
# ══════════════════════════════════════════
def render_mode2():
    st.markdown(
        f'<div style="background:{DARK2};border:1px solid {GREEN}44;border-radius:16px;'
        f'padding:20px 24px;margin-bottom:20px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:22px;letter-spacing:5px;color:{GREEN};">'
        f'MODE 2 — TRAIN YOUR AI</div>'
        f'<div style="font-size:13px;color:{T2};margin-top:4px;">'
        f'Teach Arka AI your setups. It remembers forever in vector memory.</div></div>',
        unsafe_allow_html=True
    )

    tab_manual, tab_chart, tab_pdf, tab_memory = st.tabs([
        "Add Rule", "Annotate Chart", "Upload PDF", "View Memory"
    ])

    # ── TAB 1: MANUAL RULE ────────────────
    with tab_manual:
        rule_name = st.text_input("Rule Name",
                                   placeholder="e.g. PDH Breakout Confirmation",
                                   key="m2_rule_name")
        rule_text = st.text_area("Exact Conditions",
                                  placeholder="e.g. Price must close above PDH. Volume 1.5x average. RSI above 55.",
                                  height=130, key="m2_rule_text")
        if st.button("SAVE TO MEMORY", type="primary", use_container_width=True, key="m2_save"):
            if rule_name.strip() and rule_text.strip():
                with st.spinner("Generating embedding and saving..."):
                    ok = save_rule_to_memory("Manual", rule_name.strip(), rule_text.strip(), [])
                if ok:
                    st.success(f"Learned: {rule_name}")
                    speak(f"Rule saved. I have learned {rule_name}.")
                    st.rerun()
            else:
                st.warning("Fill Rule Name and Conditions.")

    # ── TAB 2: ANNOTATE CHART ─────────────
    with tab_chart:
        st.markdown(
            f'<div style="font-size:13px;color:{T2};margin-bottom:12px;">'
            f'Upload a setup chart. AI auto-reads the image AND your notes combined.</div>',
            unsafe_allow_html=True
        )
        train_file = st.file_uploader("Upload setup chart",
                                       type=["png","jpg","jpeg"], key="train_chart")
        tx, ty, orig_w, orig_h = None, None, 800, 600

        if train_file:
            raw_img = Image.open(train_file).convert("RGB")
            orig_w, orig_h = raw_img.size
            if HAS_IMG_COORDS:
                st.markdown(
                    f'<div style="font-size:11px;color:{GOLD};margin-bottom:4px;">'
                    f'Click on key candle/zone to lock coordinates</div>',
                    unsafe_allow_html=True
                )
                tc = streamlit_image_coordinates(raw_img, key="train_click", use_column_width=True)
                if tc and tc.get("x") is not None:
                    tx, ty = tc["x"], tc["y"]
                    st.markdown(
                        f'<div style="color:{GOLD};font-size:12px;font-family:monospace;margin-top:6px;">'
                        f'Target: ({tx},{ty}) — {round(tx/orig_w*100,1)}% H · {round(ty/orig_h*100,1)}% V</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.image(raw_img, use_container_width=True)

        setup_name  = st.text_input("Setup Name",
                                     placeholder="e.g. Low Volume Handle Consolidation",
                                     key="m2_setup_name")
        setup_notes = st.text_area("Your observations (AI will ALSO read chart itself)",
                                    placeholder="e.g. Volume drops 40% during consolidation. Entry on breakout above cup rim.",
                                    height=100, key="m2_setup_notes")

        if st.button("TEACH THIS SETUP", type="primary", use_container_width=True, key="m2_teach"):
            if not setup_name.strip():
                st.warning("Enter a Setup Name.")
            elif not train_file:
                st.warning("Upload a chart image first.")
            else:
                with st.spinner("AI reading chart + saving to memory..."):
                    # Step 1: AI auto-extracts visual notes from image
                    img_for_notes = Image.open(train_file).convert("RGB")
                    ai_notes = generate_chart_notes(img_for_notes, user_note=setup_notes.strip())

                    # Step 2: Combine user notes + AI observations
                    combined = ""
                    if setup_notes.strip():
                        combined += f"USER NOTES:\n{setup_notes.strip()}\n\n"
                    if ai_notes:
                        combined += f"AI VISUAL OBSERVATIONS:\n{ai_notes}"
                    if not combined.strip():
                        combined = f"Visual setup: {setup_name}"
                    if tx:
                        combined += f"\n\nCOORDINATE: ({tx},{ty}) = {round(tx/orig_w*100,1)}% X, {round(ty/orig_h*100,1)}% Y"

                    # Step 3: Save to Pinecone
                    ok = save_rule_to_memory("Visual-Pattern", setup_name.strip(), combined, ["chart-trained"])

                if ok:
                    if ai_notes:
                        st.markdown(
                            f'<div style="background:{DARK3};border:1px solid {GREEN}44;'
                            f'border-left:3px solid {GREEN};border-radius:10px;'
                            f'padding:14px 16px;margin-bottom:8px;">'
                            f'<div style="font-family:Inter,sans-serif;font-weight:800;'
                            f'font-size:12px;color:{GREEN};letter-spacing:1px;margin-bottom:8px;">'
                            f'AI AUTO-EXTRACTED NOTES</div>'
                            f'<div style="font-size:12px;color:{IVORY};line-height:1.8;">'
                            + ai_notes.replace("\n","<br>")
                            + "</div></div>",
                            unsafe_allow_html=True
                        )
                    st.success(f"Taught: {setup_name} — saved with AI visual notes!")
                    speak(f"Understood. I have learned the {setup_name} setup.")
                    st.rerun()

    # ── TAB 3: PDF ────────────────────────
    with tab_pdf:
        pdf_file = st.file_uploader("Upload PDF", type=["pdf"], key="pdf_upload")
        if pdf_file and st.button("Extract & Learn", type="primary",
                                   use_container_width=True, key="pdf_learn"):
            with st.spinner("Reading PDF..."):
                chunks = extract_pdf_rules(pdf_file)
            if not chunks:
                st.error("No text found.")
            else:
                progress = st.progress(0)
                saved = 0
                for i, chunk in enumerate(chunks):
                    if save_rule_to_memory("PDF", f"{pdf_file.name[:15]}-{i+1}", chunk, ["pdf"]):
                        saved += 1
                    progress.progress((i+1)/len(chunks))
                st.success(f"Learned {saved}/{len(chunks)} rule segments.")
                speak(f"PDF processed. I have learned {saved} rules.")

    # ── TAB 4: MEMORY ─────────────────────
    with tab_memory:
        query = st.text_input("Search memory", placeholder="e.g. volume breakout", key="mem_q")
        search_q = query.strip() if query.strip() else "trading setup rule entry exit"
        with st.spinner("Loading..."):
            results = search_memory(search_q, top_k=15)

        if not results:
            st.markdown(
                f'<div style="color:{T2};font-size:13px;text-align:center;padding:40px;">'
                f'No rules stored yet. Use Add Rule or Annotate Chart.</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(f'<div style="font-size:12px;color:{T2};margin-bottom:12px;">Showing {len(results)} rules</div>', unsafe_allow_html=True)
            for r in results:
                sc = GREEN if r["score"]>0.8 else GOLD if r["score"]>0.6 else T2
                st.markdown(
                    f'<div style="background:{DARK3};border:1px solid {BORDER};'
                    f'border-left:3px solid {sc};border-radius:10px;'
                    f'padding:14px 16px;margin-bottom:8px;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'<span style="font-family:Inter,sans-serif;font-weight:800;font-size:13px;color:{IVORY};">{r["rule_name"]}</span>'
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:11px;color:{sc};">{r["score"]:.2f}</span>'
                    f'</div>'
                    f'<div style="font-size:12px;color:{T2};line-height:1.7;">'
                    + r["rule_text"][:350] + ("..." if len(r["rule_text"])>350 else "")
                    + "</div></div>",
                    unsafe_allow_html=True
                )

# ══════════════════════════════════════════
# 10. MAIN ENTRY POINT
# ══════════════════════════════════════════
def render_arka_ai():
    with st.expander("API Status", expanded=False):
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Gemini Key",   "SET" if GEMINI_KEY   else "MISSING")
        c2.metric("Pinecone Key", "SET" if PINECONE_KEY else "MISSING")
        c3.metric("Gemini Lib",   "OK"  if HAS_GEMINI   else "MISSING")
        c4.metric("Pinecone Lib", "OK"  if HAS_PINECONE else "MISSING")

    st.markdown(
        f'<div style="text-align:center;margin-bottom:24px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:36px;letter-spacing:8px;color:{GOLD};">ARKA AI</div>'
        f'<div style="font-size:11px;letter-spacing:4px;color:{T2};text-transform:uppercase;margin-top:2px;">'
        f'Zero-Emotion Chart Companion · Gemini 2.5 Flash · Pinecone Memory</div>'
        f'<div style="font-size:11px;color:{T2};margin-top:6px;font-style:italic;">'
        f'Not SEBI registered. Educational use only.</div></div>',
        unsafe_allow_html=True
    )
    mode = st.radio("Select Mode", ["Mode 1 — Live Analysis","Mode 2 — Train AI"],
                    horizontal=True, key="ai_mode_sel")
    st.markdown(f'<div style="height:1px;background:{BORDER};margin:16px 0;"></div>',
                unsafe_allow_html=True)

    if mode == "Mode 1 — Live Analysis":
        render_mode1()
    else:
        render_mode2()
