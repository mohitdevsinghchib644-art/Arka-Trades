"""
arka_ai.py — Arka AI Trading Companion
Brain: Gemini 2.5 Flash | Memory: Pinecone | Voice: Web Speech API
v2 — Ruthless Evaluator + 3-Month Live Chart (Mode 3)
"""
import streamlit as st
import json, time, hashlib, requests
from datetime import datetime, timedelta

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

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# ── Secrets ──────────────────────────────────────────────
GEMINI_KEY   = st.secrets.get("GEMINI_KEY",   "")
PINECONE_KEY = st.secrets.get("PINECONE_KEY", "")
INDEX_NAME   = "arka-trading-rules"

# ── Colors ────────────────────────────────────────────────
GOLD   = "#C8A96A"
GREEN  = "#00B37A"
RED    = "#E84545"
DARK   = "#04080F"
DARK2  = "#060D1A"
DARK3  = "#091525"
BORDER = "#0F2040"
T2     = "#8A9AB5"
IVORY  = "#F7EBE0"

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

# ── UPGRADED SYSTEM PROMPT — RUTHLESS MODE ──────────────
SYSTEM_PROMPT = """You are Arka AI — a ruthless, zero-emotion institutional risk manager and chart auditor.

CORE DIRECTIVE:
You are NOT a cheerleader. Your job is to DESTROY weak setups, not validate them.
If a setup is degrading, choppy, or forced — say so explicitly with exact evidence.
If evidence is missing, you PENALISE the score. Do NOT fill gaps with optimism.

ANALYSIS RULES:
- Never say "looks good", "nice", "great", "interesting", "promising"
- Reference exact visual locations: "candle at X%, volume bar at bottom-left"
- Apply user's personal rules from memory with zero compromise
- If a rule is partially met, mark it VIOLATED — partial compliance = failure
- Volume is law: no volume = no conviction = flag it
- Overlapping candles = structural decay = red flag every time
- Forced breakouts (breakout on low volume, no follow-through) = automatic INVALID flag
- NOT SEBI registered — educational only

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no preamble:
{
  "verdict": "VALID | INVALID | FLAGGED",
  "score": 6,
  "voice_summary": "2-3 sentence ruthless voice response",
  "detailed_analysis": "Full clinical breakdown — no fluff",
  "good_points": [
    "Specific confluence 1 with exact chart evidence",
    "Specific confluence 2 with exact chart evidence"
  ],
  "bad_points": [
    "Specific friction/decay point 1 with exact chart evidence",
    "Specific friction/decay point 2 with exact chart evidence"
  ],
  "rules_matched": ["rule name if fully satisfied"],
  "rules_violated": ["rule name + WHY it failed"],
  "risk_verdict": "GO | NO-GO | WAIT",
  "draw_boxes": [{"x":100,"y":200,"w":80,"h":40,"color":"#00B8CC","label":"Zone"}],
  "draw_arrows": [{"x1":150,"y1":300,"x2":200,"y2":250,"color":"#FF4444","label":"SL"}]
}

SCORING GUIDE (be brutal):
10 — Perfect textbook setup, all rules matched, strong volume
8-9 — Strong setup, minor friction
6-7 — Borderline, real concerns present
4-5 — More red flags than green flags
1-3 — Structurally broken, do not trade
0 — Catastrophic setup / active trap

If good_points list is empty, score CANNOT be above 3.
If bad_points list has 3+ items, score CANNOT be above 6.
"""

# ══════════════════════════════════════════
# 2. EMBEDDING
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
        return "No custom rules in memory. Apply strict general TA principles — no leniency."
    lines = ["=== YOUR TRADING RULES (apply with ZERO compromise) ==="]
    for r in rules:
        lines.append(f"\n[{r['rule_type']}] {r['rule_name']} ({r['score']:.2f})")
        lines.append(f"  → {r['rule_text']}")
    lines.append("\n=== END RULES — partial match = VIOLATED ===")
    return "\n".join(lines)

# ══════════════════════════════════════════
# 4. CHART VISION
# ══════════════════════════════════════════
def generate_chart_notes(img, user_note: str = "") -> str:
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
        return {
            "verdict": "ERROR", "score": 0,
            "voice_summary": "Gemini offline.",
            "detailed_analysis": "Check GEMINI_KEY.",
            "good_points": [], "bad_points": [],
            "rules_matched": [], "rules_violated": [],
            "risk_verdict": "NO-GO",
            "draw_boxes": [], "draw_arrows": []
        }

    rules_ctx = build_rules_context(user_note or "trading entry setup validation")

    click_info = ""
    if click_x and click_y:
        pct_x = round(click_x / img.width * 100, 1)
        pct_y = round(click_y / img.height * 100, 1)
        click_info = (
            f"\n\nUSER CLICKED: pixel ({click_x},{click_y}) = {pct_x}% left, {pct_y}% top. "
            f"Focus PRIMARY analysis on the candlestick at this exact location."
        )

    candle_zone_end     = int(img.height * 0.70)
    volume_zone_start   = int(img.height * 0.70)
    volume_zone_end     = int(img.height * 0.88)
    price_scale_start   = int(img.width  * 0.88)

    prompt = (
        f"{rules_ctx}{click_info}\n\n"
        f"CHART LAYOUT:\n"
        f"- CANDLE ZONE: Y 0 to {candle_zone_end}px\n"
        f"- VOLUME ZONE: Y {volume_zone_start} to {volume_zone_end}px\n"
        f"- PRICE SCALE: X {price_scale_start} to {img.width}px (no annotations here)\n"
        f"- Total image: {img.width}x{img.height}px\n\n"
        f"CRITICAL INSTRUCTION:\n"
        f"You MUST populate BOTH good_points AND bad_points arrays.\n"
        f"Each point must cite specific visual evidence from the chart.\n"
        f"If you cannot find at least 1 good point, good_points = [] and score <= 3.\n"
        f"If you find 3+ bad points, score <= 6 regardless of good points.\n"
        f"Do NOT be agreeable. Do NOT force a match. If the setup is broken, say it.\n\n"
        f"TASK: Ruthlessly audit this chart against rules above.\n"
        f"{f'USER NOTE: {user_note}' if user_note else ''}\n"
        f"Return ONLY valid JSON. No markdown. No preamble."
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
        result = json.loads(resp.text.strip())
        # Enforce score caps
        good_pts = result.get("good_points", [])
        bad_pts  = result.get("bad_points",  [])
        score    = result.get("score", 5)
        if len(good_pts) == 0:
            score = min(score, 3)
        if len(bad_pts) >= 3:
            score = min(score, 6)
        result["score"] = score
        return result
    except Exception as e:
        return {
            "verdict": "ERROR", "score": 0,
            "voice_summary": str(e)[:100],
            "detailed_analysis": str(e),
            "good_points": [], "bad_points": [],
            "rules_matched": [], "rules_violated": [],
            "risk_verdict": "NO-GO",
            "draw_boxes": [], "draw_arrows": []
        }

def draw_annotations(img, analysis: dict):
    if not HAS_PIL:
        return img
    draw = ImageDraw.Draw(img, "RGBA")
    for box in analysis.get("draw_boxes", []):
        try:
            x, y, w, h = int(box["x"]), int(box["y"]), int(box.get("w", 60)), int(box.get("h", 30))
            c = box.get("color", "#00B8CC")
            r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            draw.rectangle([x, y, x+w, y+h], outline=(r,g,b,255), width=2, fill=(r,g,b,35))
            if box.get("label"):
                draw.text((x+3, y-14), box["label"], fill=(r,g,b,255))
        except:
            pass
    for arrow in analysis.get("draw_arrows", []):
        try:
            x1, y1, x2, y2 = int(arrow["x1"]), int(arrow["y1"]), int(arrow["x2"]), int(arrow["y2"])
            c = arrow.get("color", "#FF4444")
            r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            draw.line([x1, y1, x2, y2], fill=(r,g,b,255), width=2)
            draw.polygon([x2, y2, x2-8, y2-5, x2-8, y2+5], fill=(r,g,b,200))
            if arrow.get("label"):
                draw.text((x2+4, y2-10), arrow["label"], fill=(r,g,b,255))
        except:
            pass
    return img

# ══════════════════════════════════════════
# 5. PDF
# ══════════════════════════════════════════
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

# ══════════════════════════════════════════
# 6. VOICE
# ══════════════════════════════════════════
def speak(text: str):
    clean = text.replace('"', "'").replace('\n', ' ').replace('\\', ' ')[:500]
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
def verdict_badge(verdict: str, score: int, risk_verdict: str = "") -> str:
    colors       = {"VALID": GREEN, "INVALID": RED, "FLAGGED": GOLD, "ERROR": T2}
    risk_colors  = {"GO": GREEN, "NO-GO": RED, "WAIT": GOLD}
    c  = colors.get(verdict, T2)
    rc = risk_colors.get(risk_verdict, T2)
    bw = int((score / 10) * 100)
    bar_color = GREEN if score >= 7 else GOLD if score >= 4 else RED

    risk_badge = ""
    if risk_verdict:
        risk_badge = (
            f'<div style="background:{rc}22;border:1px solid {rc};border-radius:8px;'
            f'padding:5px 14px;font-family:JetBrains Mono,monospace;font-weight:700;'
            f'font-size:13px;letter-spacing:2px;color:{rc};">{risk_verdict}</div>'
        )

    return (
        f'<div style="background:{DARK2};border:1px solid {c};border-radius:14px;'
        f'padding:20px;margin-bottom:16px;">'
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap;">'
        f'<div style="background:{c};color:{DARK};font-family:Inter,sans-serif;font-weight:900;'
        f'font-size:14px;letter-spacing:2px;padding:6px 16px;border-radius:8px;">{verdict}</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:22px;font-weight:700;color:{c};">'
        f'{score}/10</div>'
        f'{risk_badge}'
        f'</div>'
        f'<div style="background:{BORDER};border-radius:4px;height:6px;width:100%;">'
        f'<div style="background:{bar_color};width:{bw}%;height:6px;border-radius:4px;'
        f'transition:width 0.6s ease;"></div>'
        f'</div></div>'
    )

# ══════════════════════════════════════════
# 8. GOOD / BAD POINTS PANEL — NEW
# ══════════════════════════════════════════
def render_evaluation_panel(result: dict):
    good_pts = result.get("good_points", [])
    bad_pts  = result.get("bad_points",  [])

    col_good, col_bad = st.columns(2)

    with col_good:
        st.markdown(
            f'<div style="background:{DARK3};border:1px solid {GREEN}55;border-radius:12px;'
            f'padding:16px;height:100%;">'
            f'<div style="font-family:Bebas Neue,sans-serif;font-size:16px;letter-spacing:4px;'
            f'color:{GREEN};margin-bottom:12px;display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:18px;">✓</span> CONFLUENCES</div>'
            + (
                "".join([
                    f'<div style="display:flex;gap:10px;margin-bottom:10px;align-items:flex-start;">'
                    f'<div style="min-width:20px;height:20px;background:{GREEN}22;border:1px solid {GREEN};'
                    f'border-radius:4px;display:flex;align-items:center;justify-content:center;'
                    f'font-size:10px;color:{GREEN};font-weight:700;margin-top:1px;">{i+1}</div>'
                    f'<div style="font-size:12px;color:{IVORY};line-height:1.7;">{pt}</div></div>'
                    for i, pt in enumerate(good_pts)
                ])
                if good_pts else
                f'<div style="font-size:12px;color:{T2};font-style:italic;padding:8px 0;">'
                f'No confluences detected. Setup lacks conviction.</div>'
            )
            + '</div>',
            unsafe_allow_html=True
        )

    with col_bad:
        st.markdown(
            f'<div style="background:{DARK3};border:1px solid {RED}55;border-radius:12px;'
            f'padding:16px;height:100%;">'
            f'<div style="font-family:Bebas Neue,sans-serif;font-size:16px;letter-spacing:4px;'
            f'color:{RED};margin-bottom:12px;display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:18px;">✗</span> FRICTION / DECAY</div>'
            + (
                "".join([
                    f'<div style="display:flex;gap:10px;margin-bottom:10px;align-items:flex-start;">'
                    f'<div style="min-width:20px;height:20px;background:{RED}22;border:1px solid {RED};'
                    f'border-radius:4px;display:flex;align-items:center;justify-content:center;'
                    f'font-size:10px;color:{RED};font-weight:700;margin-top:1px;">{i+1}</div>'
                    f'<div style="font-size:12px;color:{IVORY};line-height:1.7;">{pt}</div></div>'
                    for i, pt in enumerate(bad_pts)
                ])
                if bad_pts else
                f'<div style="font-size:12px;color:{T2};font-style:italic;padding:8px 0;">'
                f'No friction detected. Proceed with standard risk rules.</div>'
            )
            + '</div>',
            unsafe_allow_html=True
        )

# ══════════════════════════════════════════
# 9. MODE 1 — LIVE ANALYSIS (UPGRADED)
# ══════════════════════════════════════════
def render_mode1():
    st.markdown(
        f'<div style="background:{DARK2};border:1px solid {GOLD}44;border-radius:16px;'
        f'padding:20px 24px;margin-bottom:20px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:22px;letter-spacing:5px;color:{GOLD};">'
        f'MODE 1 — LIVE CHART ANALYSIS</div>'
        f'<div style="font-size:13px;color:{T2};margin-top:4px;">'
        f'Upload chart → click candle → ruthless AI audit vs your rules</div></div>',
        unsafe_allow_html=True
    )

    uploaded = st.file_uploader("Upload chart (PNG/JPG)", type=["png", "jpg", "jpeg"], key="m1_upload")
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
            with st.spinner("Gemini auditing chart..."):
                result = analyze_chart(img, click_x, click_y, user_note)

            st.markdown(
                verdict_badge(
                    result.get("verdict", "FLAGGED"),
                    result.get("score", 5),
                    result.get("risk_verdict", "")
                ),
                unsafe_allow_html=True
            )

            if auto_voice:
                speak(result.get("voice_summary", "Analysis complete."))

            if result.get("draw_boxes") or result.get("draw_arrows"):
                annotated = draw_annotations(img.copy(), result)
                with col_chart:
                    st.image(annotated, caption="AI Annotated", use_container_width=True)

    # ── Full-width evaluation panel below columns ──────────
    if "m1_analyze" in st.session_state or result if "result" in dir() else False:
        pass  # handled below after button press

    # Persist result in session state for rendering below columns
    if st.button("ANALYZE CHART", type="primary", use_container_width=True, key="m1_analyze_2"):
        pass  # dummy — real button is above

    # ── Show evaluation if result exists ──────────────────
    if "m1_last_result" in st.session_state:
        result = st.session_state["m1_last_result"]
        st.markdown(f'<div style="height:1px;background:{BORDER};margin:20px 0;"></div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-family:Bebas Neue,sans-serif;font-size:18px;letter-spacing:4px;'
            f'color:{IVORY};margin-bottom:14px;">RISK MANAGER REPORT</div>',
            unsafe_allow_html=True
        )
        render_evaluation_panel(result)

        if result.get("rules_matched") or result.get("rules_violated"):
            col_rm, col_rv = st.columns(2)
            with col_rm:
                if result.get("rules_matched"):
                    st.markdown(f'<div style="color:{GREEN};font-size:13px;font-weight:700;margin-top:14px;">Rules Matched</div>', unsafe_allow_html=True)
                    for r in result["rules_matched"]:
                        st.markdown(f'<div style="color:{GREEN};font-size:12px;margin-left:8px;padding:4px 0;">✓ {r}</div>', unsafe_allow_html=True)
            with col_rv:
                if result.get("rules_violated"):
                    st.markdown(f'<div style="color:{RED};font-size:13px;font-weight:700;margin-top:14px;">Rules Violated</div>', unsafe_allow_html=True)
                    for r in result["rules_violated"]:
                        st.markdown(f'<div style="color:{RED};font-size:12px;margin-left:8px;padding:4px 0;">✗ {r}</div>', unsafe_allow_html=True)

        with st.expander("Full Clinical Analysis", expanded=False):
            st.markdown(
                f'<div style="background:{DARK3};border-radius:10px;padding:16px;'
                f'font-size:13px;color:{IVORY};line-height:1.9;">'
                + result.get("detailed_analysis", "").replace("\n", "<br>")
                + "</div>",
                unsafe_allow_html=True
            )


# Monkey-patch to store result in session state properly
_original_render_mode1 = render_mode1

def render_mode1():
    st.markdown(
        f'<div style="background:{DARK2};border:1px solid {GOLD}44;border-radius:16px;'
        f'padding:20px 24px;margin-bottom:20px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:22px;letter-spacing:5px;color:{GOLD};">'
        f'MODE 1 — LIVE CHART ANALYSIS</div>'
        f'<div style="font-size:13px;color:{T2};margin-top:4px;">'
        f'Upload chart → click candle → ruthless AI audit vs your rules</div></div>',
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
            with st.spinner("Gemini auditing chart..."):
                result = analyze_chart(img, click_x, click_y, user_note)
            st.session_state["m1_last_result"] = result
            st.session_state["m1_annotated_img"] = None

            st.markdown(
                verdict_badge(
                    result.get("verdict", "FLAGGED"),
                    result.get("score", 5),
                    result.get("risk_verdict", "")
                ),
                unsafe_allow_html=True
            )
            if auto_voice:
                speak(result.get("voice_summary", "Analysis complete."))

            if result.get("draw_boxes") or result.get("draw_arrows"):
                annotated = draw_annotations(img.copy(), result)
                st.session_state["m1_annotated_img"] = annotated

    # Show annotated image if available
    if st.session_state.get("m1_annotated_img") is not None:
        with col_chart:
            st.image(st.session_state["m1_annotated_img"],
                     caption="AI Annotated", use_container_width=True)

    # Full-width report below columns
    if "m1_last_result" in st.session_state:
        result = st.session_state["m1_last_result"]
        st.markdown(f'<div style="height:1px;background:{BORDER};margin:20px 0;"></div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-family:Bebas Neue,sans-serif;font-size:18px;letter-spacing:4px;'
            f'color:{IVORY};margin-bottom:14px;">RISK MANAGER REPORT</div>',
            unsafe_allow_html=True
        )

        # ── GOOD / BAD points panel ──────────────
        render_evaluation_panel(result)

        # ── Rules matched / violated ──────────────
        if result.get("rules_matched") or result.get("rules_violated"):
            col_rm, col_rv = st.columns(2)
            with col_rm:
                if result.get("rules_matched"):
                    st.markdown(
                        f'<div style="color:{GREEN};font-size:13px;font-weight:700;margin-top:14px;">'
                        f'Rules Matched</div>',
                        unsafe_allow_html=True
                    )
                    for r in result["rules_matched"]:
                        st.markdown(
                            f'<div style="color:{GREEN};font-size:12px;margin-left:8px;padding:4px 0;">'
                            f'✓ {r}</div>',
                            unsafe_allow_html=True
                        )
            with col_rv:
                if result.get("rules_violated"):
                    st.markdown(
                        f'<div style="color:{RED};font-size:13px;font-weight:700;margin-top:14px;">'
                        f'Rules Violated</div>',
                        unsafe_allow_html=True
                    )
                    for r in result["rules_violated"]:
                        st.markdown(
                            f'<div style="color:{RED};font-size:12px;margin-left:8px;padding:4px 0;">'
                            f'✗ {r}</div>',
                            unsafe_allow_html=True
                        )

        with st.expander("Full Clinical Analysis", expanded=False):
            st.markdown(
                f'<div style="background:{DARK3};border-radius:10px;padding:16px;'
                f'font-size:13px;color:{IVORY};line-height:1.9;">'
                + result.get("detailed_analysis", "").replace("\n", "<br>")
                + "</div>",
                unsafe_allow_html=True
            )

# ══════════════════════════════════════════
# 10. MODE 2 — TRAINING (unchanged)
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
                    img_for_notes = Image.open(train_file).convert("RGB")
                    ai_notes = generate_chart_notes(img_for_notes, user_note=setup_notes.strip())
                    combined = ""
                    if setup_notes.strip():
                        combined += f"USER NOTES:\n{setup_notes.strip()}\n\n"
                    if ai_notes:
                        combined += f"AI VISUAL OBSERVATIONS:\n{ai_notes}"
                    if not combined.strip():
                        combined = f"Visual setup: {setup_name}"
                    if tx:
                        combined += f"\n\nCOORDINATE: ({tx},{ty}) = {round(tx/orig_w*100,1)}% X, {round(ty/orig_h*100,1)}% Y"
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
                            + ai_notes.replace("\n", "<br>")
                            + "</div></div>",
                            unsafe_allow_html=True
                        )
                    st.success(f"Taught: {setup_name} — saved with AI visual notes!")
                    speak(f"Understood. I have learned the {setup_name} setup.")
                    st.rerun()

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
                sc = GREEN if r["score"] > 0.8 else GOLD if r["score"] > 0.6 else T2
                st.markdown(
                    f'<div style="background:{DARK3};border:1px solid {BORDER};'
                    f'border-left:3px solid {sc};border-radius:10px;'
                    f'padding:14px 16px;margin-bottom:8px;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'<span style="font-family:Inter,sans-serif;font-weight:800;font-size:13px;color:{IVORY};">'
                    f'{r["rule_name"]}</span>'
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:11px;color:{sc};">'
                    f'{r["score"]:.2f}</span>'
                    f'</div>'
                    f'<div style="font-size:12px;color:{T2};line-height:1.7;">'
                    + r["rule_text"][:350] + ("..." if len(r["rule_text"]) > 350 else "")
                    + "</div></div>",
                    unsafe_allow_html=True
                )

# ══════════════════════════════════════════
# 11. MODE 3 — 3-MONTH LIVE CHART (NEW)
# ══════════════════════════════════════════

# Common NSE suffixes for auto-appending
NSE_SUFFIX_KEYWORDS = [
    "nifty", "bank", "sensex", "bse", "nse"
]

def resolve_ticker(raw: str) -> str:
    """
    Auto-append .NS for Indian tickers if no suffix present.
    Handles plain names like RELIANCE → RELIANCE.NS
    Also handles index tickers like ^NSEI
    """
    raw = raw.strip().upper()
    # Already has suffix or is an index
    if "." in raw or raw.startswith("^"):
        return raw
    # Common indices
    index_map = {
        "NIFTY50": "^NSEI", "NIFTY": "^NSEI",
        "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN",
        "NIFTYBANK": "^NSEBANK",
        "MIDCAP": "^NSEMDCP50",
    }
    if raw in index_map:
        return index_map[raw]
    # Default: append .NS
    return f"{raw}.NS"

def format_volume(v: float) -> str:
    if v >= 1_00_00_000:
        return f"{v/1_00_00_000:.1f}Cr"
    elif v >= 1_00_000:
        return f"{v/1_00_000:.1f}L"
    elif v >= 1000:
        return f"{v/1000:.1f}K"
    return str(int(v))

def fetch_3m_data(ticker: str):
    """Fetch 3 months daily OHLCV from yfinance."""
    if not HAS_YFINANCE:
        return None, "yfinance not installed. Run: pip install yfinance"
    try:
        resolved = resolve_ticker(ticker)
        end_date   = datetime.today()
        start_date = end_date - timedelta(days=92)  # ~3 months
        df = yf.download(
            resolved,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=True
        )
        if df.empty:
            return None, f"No data found for '{resolved}'. Check ticker symbol."
        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        df = df.reset_index()
        df.columns = [str(c).strip() for c in df.columns]
        return df, resolved
    except Exception as e:
        return None, str(e)

def build_candlestick_chart(df, ticker_label: str):
    """Build a Plotly candlestick + volume chart with dark theme."""
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.72, 0.28]
    )

    # ── Candles ───────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=df["Date"] if "Date" in df.columns else df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="Price",
            increasing_line_color=GREEN,
            decreasing_line_color=RED,
            increasing_fillcolor=GREEN,
            decreasing_fillcolor=RED,
            line_width=1,
        ),
        row=1, col=1
    )

    # ── 20 EMA ────────────────────────────────
    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    fig.add_trace(
        go.Scatter(
            x=df["Date"] if "Date" in df.columns else df.index,
            y=ema20,
            name="EMA 20",
            line=dict(color=GOLD, width=1.2, dash="dot"),
            opacity=0.85
        ),
        row=1, col=1
    )

    # ── 50 EMA ────────────────────────────────
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    fig.add_trace(
        go.Scatter(
            x=df["Date"] if "Date" in df.columns else df.index,
            y=ema50,
            name="EMA 50",
            line=dict(color="#7B8FCC", width=1.2, dash="dot"),
            opacity=0.75
        ),
        row=1, col=1
    )

    # ── Volume bars ───────────────────────────
    vol_colors = [
        GREEN if (df["Close"].iloc[i] >= df["Open"].iloc[i]) else RED
        for i in range(len(df))
    ]
    fig.add_trace(
        go.Bar(
            x=df["Date"] if "Date" in df.columns else df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=vol_colors,
            marker_opacity=0.65,
            showlegend=False
        ),
        row=2, col=1
    )

    # ── Average volume line ───────────────────
    avg_vol = df["Volume"].rolling(20).mean()
    fig.add_trace(
        go.Scatter(
            x=df["Date"] if "Date" in df.columns else df.index,
            y=avg_vol,
            name="Vol MA20",
            line=dict(color=GOLD, width=1, dash="dot"),
            opacity=0.7,
            showlegend=False
        ),
        row=2, col=1
    )

    # ── Layout ────────────────────────────────
    last_close  = df["Close"].iloc[-1]
    prev_close  = df["Close"].iloc[-2] if len(df) > 1 else last_close
    price_chg   = last_close - prev_close
    price_pct   = (price_chg / prev_close) * 100
    chg_color   = GREEN if price_chg >= 0 else RED
    chg_sign    = "+" if price_chg >= 0 else ""

    fig.update_layout(
        title=dict(
            text=(
                f"<b>{ticker_label}</b>  "
                f"<span style='color:{chg_color}'>{chg_sign}{price_chg:.2f} ({chg_sign}{price_pct:.2f}%)</span>  "
                f"<span style='font-size:14px;color:#8A9AB5'>3M Daily · {len(df)} candles</span>"
            ),
            font=dict(size=18, color=IVORY),
            x=0.01
        ),
        paper_bgcolor=DARK2,
        plot_bgcolor=DARK3,
        font=dict(color=T2, family="JetBrains Mono, monospace", size=11),
        xaxis_rangeslider_visible=False,
        legend=dict(
            bgcolor=DARK2,
            bordercolor=BORDER,
            borderwidth=1,
            font=dict(size=10),
            orientation="h",
            x=0, y=1.05
        ),
        margin=dict(l=10, r=10, t=70, b=10),
        height=600,
        hovermode="x unified",
    )

    # ── Axis styling ──────────────────────────
    axis_style = dict(
        gridcolor=BORDER,
        gridwidth=0.5,
        linecolor=BORDER,
        tickcolor=T2,
        tickfont=dict(size=10),
        showgrid=True,
        zeroline=False,
    )
    fig.update_xaxes(**axis_style)
    fig.update_yaxes(**axis_style)
    fig.update_yaxes(title_text="Price", row=1, col=1,
                     title_font=dict(size=10, color=T2))
    fig.update_yaxes(title_text="Volume", row=2, col=1,
                     title_font=dict(size=10, color=T2))

    return fig

def compute_stats(df) -> dict:
    """Compute quick stats for the stats strip."""
    import pandas as pd
    close  = df["Close"]
    volume = df["Volume"]
    high   = df["High"]
    low    = df["Low"]

    high_3m    = high.max()
    low_3m     = low.min()
    last_close = close.iloc[-1]
    chg_3m_pct = ((last_close - close.iloc[0]) / close.iloc[0]) * 100
    avg_vol    = volume.mean()
    last_vol   = volume.iloc[-1]
    vol_ratio  = last_vol / avg_vol if avg_vol > 0 else 1

    return {
        "last":       last_close,
        "high_3m":    high_3m,
        "low_3m":     low_3m,
        "chg_3m_pct": chg_3m_pct,
        "avg_vol":    avg_vol,
        "last_vol":   last_vol,
        "vol_ratio":  vol_ratio,
    }

def render_stats_strip(stats: dict, ticker_label: str):
    chg_color = GREEN if stats["chg_3m_pct"] >= 0 else RED
    sign      = "+" if stats["chg_3m_pct"] >= 0 else ""
    vr_color  = GREEN if stats["vol_ratio"] >= 1.5 else GOLD if stats["vol_ratio"] >= 1.0 else RED

    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;">'
        # LTP
        f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 18px;flex:1;min-width:120px;">'
        f'<div style="font-size:10px;color:{T2};letter-spacing:2px;">LTP</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:20px;color:{IVORY};font-weight:700;">'
        f'₹{stats["last"]:.2f}</div></div>'
        # 3M Change
        f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 18px;flex:1;min-width:120px;">'
        f'<div style="font-size:10px;color:{T2};letter-spacing:2px;">3M RETURN</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:20px;color:{chg_color};font-weight:700;">'
        f'{sign}{stats["chg_3m_pct"]:.1f}%</div></div>'
        # 3M High
        f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 18px;flex:1;min-width:120px;">'
        f'<div style="font-size:10px;color:{T2};letter-spacing:2px;">3M HIGH</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:20px;color:{GREEN};font-weight:700;">'
        f'₹{stats["high_3m"]:.2f}</div></div>'
        # 3M Low
        f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 18px;flex:1;min-width:120px;">'
        f'<div style="font-size:10px;color:{T2};letter-spacing:2px;">3M LOW</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:20px;color:{RED};font-weight:700;">'
        f'₹{stats["low_3m"]:.2f}</div></div>'
        # Volume vs Avg
        f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 18px;flex:1;min-width:140px;">'
        f'<div style="font-size:10px;color:{T2};letter-spacing:2px;">VOL vs AVG</div>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:20px;color:{vr_color};font-weight:700;">'
        f'{stats["vol_ratio"]:.1f}x</div>'
        f'<div style="font-size:10px;color:{T2};">{format_volume(stats["last_vol"])} '
        f'/ avg {format_volume(stats["avg_vol"])}</div></div>'
        f'</div>',
        unsafe_allow_html=True
    )

def render_mode3():
    import pandas as pd

    st.markdown(
        f'<div style="background:{DARK2};border:1px solid #4A90D955;border-radius:16px;'
        f'padding:20px 24px;margin-bottom:20px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:22px;letter-spacing:5px;color:#4A90D9;">'
        f'MODE 3 — 3-MONTH LIVE CHART</div>'
        f'<div style="font-size:13px;color:{T2};margin-top:4px;">'
        f'Type any NSE/BSE ticker → instant 3-month daily chart. No upload needed.</div></div>',
        unsafe_allow_html=True
    )

    if not HAS_YFINANCE:
        st.error("yfinance not installed. Add `yfinance` to requirements.txt")
        return
    if not HAS_PLOTLY:
        st.error("plotly not installed. Add `plotly` to requirements.txt")
        return

    # ── Input row ─────────────────────────────────────────
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        ticker_raw = st.text_input(
            "Stock / Index",
            placeholder="e.g. RELIANCE, TCS, HDFCBANK, NIFTY50, BANKNIFTY...",
            key="m3_ticker",
            label_visibility="collapsed"
        )
    with col_btn:
        fetch_btn = st.button("LOAD CHART", type="primary",
                              use_container_width=True, key="m3_fetch")

    # ── Quick picks ───────────────────────────────────────
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
                st.session_state["m3_ticker"] = qp
                st.session_state["m3_load_ticker"] = qp
                st.rerun()

    # Determine what to load
    load_ticker = None
    if fetch_btn and ticker_raw.strip():
        load_ticker = ticker_raw.strip()
    elif "m3_load_ticker" in st.session_state:
        load_ticker = st.session_state.pop("m3_load_ticker")

    if not load_ticker:
        st.markdown(
            f'<div style="background:{DARK3};border:1px solid {BORDER};border-radius:12px;'
            f'padding:40px;text-align:center;margin-top:16px;">'
            f'<div style="font-family:Bebas Neue,sans-serif;font-size:28px;letter-spacing:6px;'
            f'color:{BORDER};margin-bottom:8px;">ENTER TICKER</div>'
            f'<div style="font-size:12px;color:{T2};">'
            f'Type ticker above or tap a quick pick · NSE suffix (.NS) auto-applied</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        return

    # ── Fetch & render ────────────────────────────────────
    with st.spinner(f"Fetching 3-month data for {load_ticker.upper()}..."):
        df, result_ticker = fetch_3m_data(load_ticker)

    if df is None:
        st.error(f"Failed: {result_ticker}")
        st.markdown(
            f'<div style="background:{DARK3};border:1px solid {RED}44;border-radius:10px;'
            f'padding:14px 16px;margin-top:8px;font-size:12px;color:{T2};">'
            f'<b style="color:{IVORY};">Tips:</b><br>'
            f'• Indian stocks: use plain name — RELIANCE, TCS, HDFCBANK (auto-appends .NS)<br>'
            f'• BSE stocks: add .BO suffix — RELIANCE.BO<br>'
            f'• Indices: NIFTY50, BANKNIFTY, SENSEX<br>'
            f'• US stocks: plain symbol — AAPL, MSFT<br>'
            f'• Verify ticker on NSE/BSE website first'
            f'</div>',
            unsafe_allow_html=True
        )
        return

    # Ensure Date column is datetime
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])

    # ── Stats strip ───────────────────────────────────────
    stats = compute_stats(df)
    render_stats_strip(stats, result_ticker)

    # ── Chart ─────────────────────────────────────────────
    fig = build_candlestick_chart(df, result_ticker)
    st.plotly_chart(fig, use_container_width=True, config={
        "displayModeBar": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "toImageButtonOptions": {
            "format": "png",
            "filename": f"arka_{result_ticker}_3m",
            "scale": 2
        }
    })

    # ── Data table (collapsible) ──────────────────────────
    with st.expander("Raw OHLCV Data", expanded=False):
        display_df = df.copy()
        if "Date" in display_df.columns:
            display_df["Date"] = display_df["Date"].dt.strftime("%d %b %Y")
        # Format numbers
        for col in ["Open", "High", "Low", "Close"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].round(2)
        if "Volume" in display_df.columns:
            display_df["Volume"] = display_df["Volume"].apply(lambda x: format_volume(x))
        st.dataframe(
            display_df[["Date","Open","High","Low","Close","Volume"]].iloc[::-1],
            use_container_width=True,
            hide_index=True
        )

# ══════════════════════════════════════════
# 12. MAIN ENTRY POINT
# ══════════════════════════════════════════
def render_arka_ai():
    with st.expander("API Status", expanded=False):
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Gemini Key",    "SET"  if GEMINI_KEY   else "MISSING")
        c2.metric("Pinecone Key",  "SET"  if PINECONE_KEY else "MISSING")
        c3.metric("Gemini Lib",    "OK"   if HAS_GEMINI   else "MISSING")
        c4.metric("Pinecone Lib",  "OK"   if HAS_PINECONE else "MISSING")
        c5.metric("yfinance",      "OK"   if HAS_YFINANCE else "MISSING")
        c6.metric("Plotly",        "OK"   if HAS_PLOTLY   else "MISSING")

    st.markdown(
        f'<div style="text-align:center;margin-bottom:24px;">'
        f'<div style="font-family:Bebas Neue,sans-serif;font-size:36px;letter-spacing:8px;color:{GOLD};">'
        f'ARKA AI</div>'
        f'<div style="font-size:11px;letter-spacing:4px;color:{T2};text-transform:uppercase;margin-top:2px;">'
        f'Zero-Emotion Chart Companion · Gemini 2.5 Flash · Pinecone Memory</div>'
        f'<div style="font-size:11px;color:{T2};margin-top:6px;font-style:italic;">'
        f'Not SEBI registered. Educational use only.</div></div>',
        unsafe_allow_html=True
    )

    mode = st.radio(
        "Select Mode",
        ["Mode 1 — Live Analysis", "Mode 2 — Train AI", "Mode 3 — Live Chart"],
        horizontal=True,
        key="ai_mode_sel"
    )
    st.markdown(f'<div style="height:1px;background:{BORDER};margin:16px 0;"></div>',
                unsafe_allow_html=True)

    if mode == "Mode 1 — Live Analysis":
        render_mode1()
    elif mode == "Mode 2 — Train AI":
        render_mode2()
    else:
        render_mode3()
