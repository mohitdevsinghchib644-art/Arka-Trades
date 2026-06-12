"""
quant_analysis.py  —  Arka Trades Quant Analysis Module
=========================================================
Upload any chart image. Gemini Vision runs a deep quantitative-style
breakdown: rating 1-100, upside/downside, risk-reward, pros/cons,
scenario probabilities and key levels — rendered as a pro dashboard.
"""

import streamlit as st
import base64, io, json, traceback

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

GEMINI_KEY = st.secrets.get("GEMINI_KEY", "")
MODEL_NAME = "gemini-2.5-flash"

# ── Theme ────────────────────────────────────────────────────
IVORY  = "#E2E8F0"
BLUE   = "#4F8DFD"
GREEN  = "#10B981"
RED    = "#EF4444"
PURPLE = "#8B5CF6"
AMBER  = "#F5C518"
DARK   = "#0B0F17"
DARK2  = "#0F1522"
DARK3  = "#151D2E"
BORDER = "#1E293B"
T2     = "#94A3B8"
FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"


# ══════════════════════════════════════════════════════════════
# AI ENGINE
# ══════════════════════════════════════════════════════════════

_QUANT_PROMPT = """You are an elite quantitative analyst. Analyze this chart image
using a full quant framework: trend regime, momentum, mean reversion, volatility
structure, volume profile, support/resistance, risk-reward asymmetry.

Read every visible element: candles, trend, consolidations, breakout points,
volume bars, indicators, price axis values, timeframe. If the user provided
context, incorporate it.

{user_context}

Be brutally objective. No hype. Estimate numeric values from the visible price
axis. If price values are not readable, use percentage estimates instead.

Return ONLY valid JSON in exactly this structure (no markdown, no preamble):
{{
  "asset_name": "name/ticker if visible, else 'Unknown'",
  "timeframe": "e.g. Daily / 1H / Weekly, if visible",
  "overall_score": 72,
  "verdict": "BULLISH | BEARISH | NEUTRAL",
  "conviction": "HIGH | MEDIUM | LOW",
  "summary": "3-4 sentence executive summary of the quant read",
  "upside": {{"target": "price or %", "potential_pct": 12.5, "reasoning": "1-2 sentences"}},
  "downside": {{"stop": "price or %", "risk_pct": 5.0, "reasoning": "1-2 sentences"}},
  "risk_reward": 2.5,
  "trend": {{"direction": "UP | DOWN | SIDEWAYS", "strength": 78, "note": "1 sentence"}},
  "momentum": {{"state": "ACCELERATING | STEADY | FADING | OVERSOLD | OVERBOUGHT", "score": 65, "note": "1 sentence"}},
  "volatility": {{"state": "EXPANDING | CONTRACTING | STABLE", "note": "1 sentence"}},
  "volume_read": "1-2 sentences on what volume shows",
  "key_levels": [
    {{"type": "RESISTANCE", "level": "price/zone", "importance": "HIGH"}},
    {{"type": "SUPPORT", "level": "price/zone", "importance": "HIGH"}}
  ],
  "pros": [
    {{"point": "specific positive factor visible on chart", "weight": 85}},
    {{"point": "another", "weight": 70}}
  ],
  "cons": [
    {{"point": "specific risk factor visible on chart", "weight": 80}},
    {{"point": "another", "weight": 55}}
  ],
  "scenarios": {{
    "bull": {{"probability": 45, "path": "1 sentence"}},
    "base": {{"probability": 35, "path": "1 sentence"}},
    "bear": {{"probability": 20, "path": "1 sentence"}}
  }},
  "detailed_analysis": "Full quant breakdown, 6-10 sentences, referencing exact visible structures and levels"
}}"""


def _img_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def run_quant_analysis(img, user_context: str = "") -> dict:
    if not HAS_GEMINI or not GEMINI_KEY:
        return {"error": "Gemini not configured. Check GEMINI_KEY in Streamlit secrets."}
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel(MODEL_NAME)
        ctx = f"USER CONTEXT: {user_context.strip()}" if user_context.strip() else ""
        prompt = _QUANT_PROMPT.format(user_context=ctx)
        response = model.generate_content(
            [prompt, {"mime_type": "image/png", "data": _img_b64(img)}])
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {"error": "Could not parse AI response. Try again.",
                "raw": response.text if 'response' in dir() else ""}
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════
# UI COMPONENTS
# ══════════════════════════════════════════════════════════════

def _score_color(score: float) -> str:
    if score >= 70: return GREEN
    if score >= 45: return AMBER
    return RED


def _gauge(score: float) -> str:
    c = _score_color(score)
    return f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-radius:16px;
         padding:28px 24px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.3);">
        <div style="font-size:11px;font-weight:700;letter-spacing:2px;color:{T2};
             text-transform:uppercase;margin-bottom:14px;">Quant Score</div>
        <div style="position:relative;width:150px;height:150px;margin:0 auto;">
            <svg width="150" height="150" viewBox="0 0 150 150">
                <circle cx="75" cy="75" r="64" fill="none" stroke="{DARK3}" stroke-width="11"/>
                <circle cx="75" cy="75" r="64" fill="none" stroke="{c}" stroke-width="11"
                        stroke-linecap="round"
                        stroke-dasharray="{score/100*402:.0f} 402"
                        transform="rotate(-90 75 75)"/>
            </svg>
            <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);">
                <div style="font-family:{MONO};font-size:38px;font-weight:800;color:{c};
                     line-height:1;">{score:.0f}</div>
                <div style="font-size:11px;color:{T2};">/ 100</div>
            </div>
        </div>
    </div>"""


def _stat_card(title, value, sub, color):
    return f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {color};
         border-radius:14px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.3);min-height:120px;">
        <div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{T2};
             text-transform:uppercase;margin-bottom:10px;">{title}</div>
        <div style="font-family:{MONO};font-size:24px;font-weight:800;color:{color};
             line-height:1.1;margin-bottom:6px;">{value}</div>
        <div style="font-size:12px;color:{T2};line-height:1.6;">{sub}</div>
    </div>"""


def _weight_bar(point: str, weight: float, color: str) -> str:
    return f"""
    <div style="margin-bottom:12px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:5px;">
            <span style="font-size:13px;color:{IVORY};line-height:1.5;flex:1;
                 padding-right:10px;">{point}</span>
            <span style="font-family:{MONO};font-size:12px;font-weight:700;
                 color:{color};white-space:nowrap;">{weight:.0f}/100</span>
        </div>
        <div style="background:{DARK3};border-radius:4px;height:5px;">
            <div style="background:{color};width:{min(weight,100):.0f}%;height:5px;
                 border-radius:4px;"></div>
        </div>
    </div>"""


def _scenario_row(name, prob, path, color):
    return f"""
    <div style="margin-bottom:14px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:5px;">
            <span style="font-size:12px;font-weight:800;letter-spacing:1px;color:{color};">{name}</span>
            <span style="font-family:{MONO};font-size:13px;font-weight:700;color:{color};">{prob:.0f}%</span>
        </div>
        <div style="background:{DARK3};border-radius:4px;height:7px;margin-bottom:5px;">
            <div style="background:{color};width:{min(prob,100):.0f}%;height:7px;border-radius:4px;"></div>
        </div>
        <div style="font-size:12px;color:{T2};line-height:1.5;">{path}</div>
    </div>"""


# ══════════════════════════════════════════════════════════════
# MAIN PAGE
# ══════════════════════════════════════════════════════════════

def render_quant_analysis():
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {PURPLE};
         border-radius:14px;padding:18px 24px;margin-bottom:20px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};">Quant Analysis</div>
        <div style="font-size:13px;color:{T2};margin-top:4px;line-height:1.7;">
            Upload any chart image. The AI runs a full quantitative breakdown:
            score 1-100, upside potential, downside risk, risk-reward, weighted
            pros and cons, and probability-based scenarios.
        </div>
    </div>""", unsafe_allow_html=True)

    up_col, ctx_col = st.columns([1.4, 1])
    with up_col:
        uploaded = st.file_uploader("Upload chart image",
                                    type=["png", "jpg", "jpeg", "webp"],
                                    key="quant_upload")
    with ctx_col:
        user_ctx = st.text_area("Context (optional)",
                                placeholder="e.g. RELIANCE daily chart. I am thinking of a swing entry here.",
                                height=100, key="quant_ctx")

    if uploaded is None:
        st.markdown(f"""
        <div style="background:{DARK3};border:1px dashed {BORDER};border-radius:14px;
             padding:56px;text-align:center;margin-top:12px;">
            <div style="font-size:16px;font-weight:800;color:{T2};margin-bottom:6px;">
                Drop a chart screenshot above</div>
            <div style="font-size:12px;color:{T2};opacity:.7;">
                Any chart works: TradingView, broker app, or a screenshot from your downloads</div>
        </div>""", unsafe_allow_html=True)
        return

    img = Image.open(uploaded).convert("RGB")
    st.image(img, use_container_width=True)

    if st.button("Run Quant Analysis", type="primary", use_container_width=True,
                 key="quant_run"):
        with st.spinner("Running quantitative analysis..."):
            result = run_quant_analysis(img, user_ctx)
        st.session_state["quant_result"] = result

    result = st.session_state.get("quant_result")
    if not result:
        return
    if result.get("error"):
        st.error(result["error"])
        return

    # ── Header: gauge + verdict + summary ──────────────────────
    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    score   = float(result.get("overall_score", 50))
    verdict = result.get("verdict", "NEUTRAL")
    conv    = result.get("conviction", "MEDIUM")
    vcol    = GREEN if verdict == "BULLISH" else RED if verdict == "BEARISH" else AMBER

    g1, g2 = st.columns([1, 2.2])
    with g1:
        st.markdown(_gauge(score), unsafe_allow_html=True)
    with g2:
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:16px;
             padding:24px;box-shadow:0 1px 3px rgba(0,0,0,.3);min-height:206px;">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap;">
                <span style="background:{vcol};color:{DARK};font-weight:800;font-size:13px;
                     letter-spacing:2px;padding:5px 16px;border-radius:8px;">{verdict}</span>
                <span style="border:1px solid {BORDER};color:{T2};font-weight:700;font-size:11px;
                     letter-spacing:1px;padding:4px 12px;border-radius:20px;">CONVICTION: {conv}</span>
                <span style="border:1px solid {BORDER};color:{T2};font-weight:600;font-size:11px;
                     padding:4px 12px;border-radius:20px;">{result.get('asset_name','Unknown')} · {result.get('timeframe','')}</span>
            </div>
            <div style="font-size:14px;color:{IVORY};line-height:1.9;">{result.get('summary','')}</div>
        </div>""", unsafe_allow_html=True)

    # ── Upside / Downside / R:R ────────────────────────────────
    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    up  = result.get("upside", {}) or {}
    dn  = result.get("downside", {}) or {}
    rr  = result.get("risk_reward", 0)
    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown(_stat_card("Upside Potential",
            f"+{float(up.get('potential_pct',0)):.1f}%",
            f"Target: {up.get('target','-')} — {up.get('reasoning','')}", GREEN),
            unsafe_allow_html=True)
    with s2:
        st.markdown(_stat_card("Downside Risk",
            f"-{float(dn.get('risk_pct',0)):.1f}%",
            f"Stop: {dn.get('stop','-')} — {dn.get('reasoning','')}", RED),
            unsafe_allow_html=True)
    with s3:
        rr_c = GREEN if float(rr or 0) >= 2 else AMBER if float(rr or 0) >= 1 else RED
        st.markdown(_stat_card("Risk : Reward",
            f"1 : {float(rr or 0):.1f}",
            "Above 1:2 is favourable for a positive expectancy", rr_c),
            unsafe_allow_html=True)

    # ── Trend / Momentum / Volatility ──────────────────────────
    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    tr = result.get("trend", {}) or {}
    mo = result.get("momentum", {}) or {}
    vo = result.get("volatility", {}) or {}
    q1, q2, q3 = st.columns(3)
    with q1:
        st.markdown(_stat_card("Trend",
            f"{tr.get('direction','-')} · {float(tr.get('strength',0)):.0f}/100",
            tr.get("note",""), BLUE), unsafe_allow_html=True)
    with q2:
        st.markdown(_stat_card("Momentum",
            f"{mo.get('state','-')} · {float(mo.get('score',0)):.0f}/100",
            mo.get("note",""), PURPLE), unsafe_allow_html=True)
    with q3:
        st.markdown(_stat_card("Volatility",
            vo.get("state","-"), vo.get("note",""), AMBER), unsafe_allow_html=True)

    # ── Pros & Cons with weights ───────────────────────────────
    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    p1, p2 = st.columns(2)
    with p1:
        pros_html = "".join(_weight_bar(p.get("point",""), float(p.get("weight",50)), GREEN)
                            for p in (result.get("pros") or []))
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {GREEN};
             border-radius:14px;padding:22px;box-shadow:0 1px 3px rgba(0,0,0,.3);">
            <div style="font-size:13px;font-weight:800;color:{GREEN};letter-spacing:1px;
                 margin-bottom:16px;">WHAT WORKS — PROS</div>
            {pros_html or f'<div style="font-size:12px;color:{T2};">None identified</div>'}
        </div>""", unsafe_allow_html=True)
    with p2:
        cons_html = "".join(_weight_bar(c.get("point",""), float(c.get("weight",50)), RED)
                            for c in (result.get("cons") or []))
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {RED};
             border-radius:14px;padding:22px;box-shadow:0 1px 3px rgba(0,0,0,.3);">
            <div style="font-size:13px;font-weight:800;color:{RED};letter-spacing:1px;
                 margin-bottom:16px;">WHAT HURTS — CONS</div>
            {cons_html or f'<div style="font-size:12px;color:{T2};">None identified</div>'}
        </div>""", unsafe_allow_html=True)

    # ── Scenarios + Key Levels ─────────────────────────────────
    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    sc1, sc2 = st.columns(2)
    sc = result.get("scenarios", {}) or {}
    with sc1:
        bull = sc.get("bull", {}) or {}
        base = sc.get("base", {}) or {}
        bear = sc.get("bear", {}) or {}
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {BLUE};
             border-radius:14px;padding:22px;box-shadow:0 1px 3px rgba(0,0,0,.3);">
            <div style="font-size:13px;font-weight:800;color:{BLUE};letter-spacing:1px;
                 margin-bottom:16px;">SCENARIO PROBABILITIES</div>
            {_scenario_row("BULL CASE", float(bull.get("probability",0)), bull.get("path",""), GREEN)}
            {_scenario_row("BASE CASE", float(base.get("probability",0)), base.get("path",""), AMBER)}
            {_scenario_row("BEAR CASE", float(bear.get("probability",0)), bear.get("path",""), RED)}
        </div>""", unsafe_allow_html=True)
    with sc2:
        levels = result.get("key_levels") or []
        rows = "".join(
            f'<tr><td style="padding:9px 12px;border-top:1px solid {BORDER};">'
            f'<span style="color:{GREEN if l.get("type")=="SUPPORT" else RED};'
            f'font-weight:700;font-size:11px;letter-spacing:1px;">{l.get("type","")}</span></td>'
            f'<td style="padding:9px 12px;border-top:1px solid {BORDER};font-family:{MONO};'
            f'font-size:13px;color:{IVORY};">{l.get("level","")}</td>'
            f'<td style="padding:9px 12px;border-top:1px solid {BORDER};font-size:11px;'
            f'color:{T2};">{l.get("importance","")}</td></tr>'
            for l in levels)
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {PURPLE};
             border-radius:14px;padding:22px;box-shadow:0 1px 3px rgba(0,0,0,.3);">
            <div style="font-size:13px;font-weight:800;color:{PURPLE};letter-spacing:1px;
                 margin-bottom:12px;">KEY LEVELS</div>
            <table style="width:100%;border-collapse:collapse;">
                <tr style="font-size:10px;color:{T2};text-align:left;letter-spacing:1px;">
                    <th style="padding:6px 12px;">TYPE</th>
                    <th style="padding:6px 12px;">LEVEL</th>
                    <th style="padding:6px 12px;">IMPORTANCE</th></tr>
                {rows or ''}
            </table>
        </div>""", unsafe_allow_html=True)

    # ── Volume read + full breakdown ───────────────────────────
    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    with st.expander("Full Quant Breakdown", expanded=True):
        st.markdown(f"""
        <div style="background:{DARK3};border-radius:10px;padding:18px;
             font-size:13px;color:{IVORY};line-height:1.9;">
            <div style="font-size:11px;font-weight:700;color:{BLUE};letter-spacing:1px;
                 margin-bottom:6px;">VOLUME READ</div>
            <div style="margin-bottom:14px;color:{T2};">{result.get('volume_read','')}</div>
            <div style="font-size:11px;font-weight:700;color:{BLUE};letter-spacing:1px;
                 margin-bottom:6px;">DETAILED ANALYSIS</div>
            {result.get('detailed_analysis','').replace(chr(10),'<br>')}
        </div>""", unsafe_allow_html=True)

    st.caption("Quant estimates are derived from the visible chart image only. "
               "Not SEBI registered — educational analysis, not investment advice.")
