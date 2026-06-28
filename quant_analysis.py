"""
quant_analysis.py — Arka Trades Quant Analysis
================================================
Self-contained module. app.py calls:
    from quant_analysis import render_quant_analysis
    render_quant_analysis()

MODULE 1 : Advanced Options Analytics & Market Microstructure
  · 3D IV Surface · 0DTE Smile · Theta Decay · GEX · Vanna · Order Flow
  · Per-tab mini summaries (what is this telling us RIGHT NOW)
  · Combined BUY/SELL/STRONG BUY etc verdict from all signals
  · Gemini AI narrative (with pure-Python fallback if no key)

MODULE 2 : Options-Signal-Based Backtesting Engine
  · Strategy derived from live options signals (GEX levels, Vanna, IV regime)
  · Tests whether the current options setup has historically worked
  · Full performance dashboard + verdict on strategy validity

Deps : streamlit numpy pandas scipy plotly yfinance
       optional: google-generativeai (for AI narrative)
"""

from __future__ import annotations
import warnings
import datetime
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import norm
from scipy.optimize import brentq

warnings.filterwarnings("ignore")

# ── Palette ──────────────────────────────────────────────────────────────────
BG     = "#0B0E13"
PANEL  = "#11151D"
PANEL2 = "#161C26"
BORDER = "#222B38"
ACCENT = "#C9A227"
IVORY  = "#E8EDF2"
MUTE   = "#8593A3"
GREEN  = "#1FB97A"
RED    = "#E8554E"
BLUE   = "#4C8DD6"
PURPLE = "#9B59B6"
ORANGE = "#E67E22"
AMBER  = "#F59E0B"
MONO   = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"

TRADING_DAYS = 252
NSE_LOT      = 50
NSE_SPOT_REF = 24_500.0

VERDICT_CONFIG = {
    "STRONG BUY" : {"color": "#1FB97A", "bg": "rgba(31,185,122,0.15)", "icon": "🟢"},
    "BUY"        : {"color": "#1FB97A", "bg": "rgba(31,185,122,0.10)", "icon": "🟩"},
    "WEAK BUY"   : {"color": "#5ed29c", "bg": "rgba(94,210,156,0.10)", "icon": "🔼"},
    "NEUTRAL"    : {"color": "#8593A3", "bg": "rgba(133,147,163,0.10)", "icon": "⬜"},
    "WEAK SELL"  : {"color": "#E67E22", "bg": "rgba(230,126,34,0.10)",  "icon": "🔽"},
    "SELL"       : {"color": "#E8554E", "bg": "rgba(232,85,78,0.10)",   "icon": "🟥"},
    "STRONG SELL": {"color": "#E8554E", "bg": "rgba(232,85,78,0.15)",   "icon": "🔴"},
}


# ════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _layout(fig: go.Figure, title: str, height: int = 420) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=title, font=dict(size=12, color=ACCENT)),
        paper_bgcolor=PANEL, plot_bgcolor=PANEL, height=height,
        margin=dict(l=8, r=8, t=40, b=8),
        font=dict(family="monospace", size=11, color=IVORY),
        xaxis=dict(gridcolor=BORDER, showgrid=True),
        yaxis=dict(gridcolor=BORDER, showgrid=True),
    )
    return fig


def _sec(label: str) -> str:
    return (
        f"<div style='font-family:{MONO};font-size:11px;font-weight:600;"
        f"color:{ACCENT};letter-spacing:1.5px;margin:14px 0 6px;"
        f"border-bottom:1px solid {BORDER};padding-bottom:5px;'>{label}</div>"
    )


def _cap(text: str):
    st.caption(text)


def _mrow(cells: list):
    cols = st.columns(len(cells))
    for col, (lbl, val) in zip(cols, cells):
        col.metric(lbl, val)


def _fmt(v, suffix="", dp=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:,.{dp}f}{suffix}"


def _signal_box(title: str, summary: str, verdict: str, score: int):
    """Renders a mini signal summary card inside a tab."""
    cfg   = VERDICT_CONFIG.get(verdict, VERDICT_CONFIG["NEUTRAL"])
    color = cfg["color"]
    bg    = cfg["bg"]
    icon  = cfg["icon"]
    st.markdown(f"""
    <div style='background:{bg};border:1px solid {color}44;border-left:4px solid {color};
    border-radius:10px;padding:14px 18px;margin:12px 0;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
        <span style='font-family:{MONO};font-size:11px;font-weight:700;color:{color};
          letter-spacing:1.5px;'>{icon} {title}</span>
        <span style='font-family:{MONO};font-size:13px;font-weight:800;color:{color};'>{verdict}</span>
      </div>
      <div style='font-size:13px;color:{IVORY};line-height:1.7;'>{summary}</div>
      <div style='margin-top:8px;'>
        {''.join(["<span style='display:inline-block;width:14px;height:6px;border-radius:3px;"
                  f"background:{color};margin-right:3px;opacity:0.9;'></span>" 
                  for _ in range(min(abs(score), 5))])}
        <span style='font-family:{MONO};font-size:10px;color:{MUTE};margin-left:6px;'>
          Signal strength: {abs(score)}/5</span>
      </div>
    </div>""", unsafe_allow_html=True)


def _big_verdict_banner(verdict: str, index_name: str, total_score: int,
                         composite: dict, ai_note: str = ""):
    """Renders the big combined verdict banner."""
    cfg   = VERDICT_CONFIG.get(verdict, VERDICT_CONFIG["NEUTRAL"])
    color = cfg["color"]
    bg    = cfg["bg"]
    icon  = cfg["icon"]

    scores_html = ""
    labels = {"GEX": composite.get("gex",0), "Vanna": composite.get("vanna",0),
              "Theta": composite.get("theta",0), "Flow": composite.get("flow",0),
              "IV Skew": composite.get("iv_skew",0)}
    for lbl, sc in labels.items():
        bar_color = GREEN if sc > 0 else RED if sc < 0 else MUTE
        width = min(abs(sc) * 20, 100)
        direction = "→ Bullish" if sc > 0 else "← Bearish" if sc < 0 else "Neutral"
        scores_html += f"""
        <div style='display:flex;align-items:center;gap:10px;margin-bottom:5px;'>
          <span style='font-family:{MONO};font-size:10px;color:{MUTE};width:60px;'>{lbl}</span>
          <div style='flex:1;height:6px;background:{BORDER};border-radius:3px;'>
            <div style='width:{width}%;height:6px;background:{bar_color};border-radius:3px;'></div>
          </div>
          <span style='font-family:{MONO};font-size:10px;color:{bar_color};width:80px;'>{direction}</span>
        </div>"""

    ai_section = f"""
    <div style='margin-top:14px;padding-top:12px;border-top:1px solid {BORDER};
    font-size:13px;color:{IVORY};line-height:1.8;'>
      <span style='font-family:{MONO};font-size:10px;color:{ACCENT};letter-spacing:1px;'>
        🤖 AI ANALYSIS</span><br>{ai_note}
    </div>""" if ai_note else ""

    st.markdown(f"""
    <div style='background:{bg};border:1px solid {color}55;border-left:5px solid {color};
    border-radius:12px;padding:20px 24px;margin:16px 0;'>
      <div style='display:flex;justify-content:space-between;align-items:flex-start;'>
        <div>
          <div style='font-family:{MONO};font-size:11px;color:{MUTE};letter-spacing:1.5px;
            margin-bottom:6px;'>COMBINED OPTIONS SIGNAL · {index_name}</div>
          <div style='font-family:{MONO};font-size:34px;font-weight:800;color:{color};
            line-height:1;'>{icon} {verdict}</div>
          <div style='font-family:{MONO};font-size:12px;color:{IVORY};margin-top:6px;'>
            Composite score: {total_score:+d} / 10 &nbsp;·&nbsp;
            {"Bullish bias confirmed across multiple signals" if total_score > 0
             else "Bearish bias confirmed across multiple signals" if total_score < 0
             else "Mixed signals — no clear directional bias"}</div>
        </div>
        <div style='min-width:260px;'>
          <div style='font-family:{MONO};font-size:10px;color:{MUTE};
            letter-spacing:1px;margin-bottom:8px;'>SIGNAL BREAKDOWN</div>
          {scores_html}
        </div>
      </div>
      {ai_section}
    </div>""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# SIGNAL SCORING ENGINE (pure Python — always available)
# ════════════════════════════════════════════════════════════════════════════

def _score_gex(gex: pd.DataFrame, levels: dict, spot: float) -> tuple[int, str]:
    """Score GEX signal. Returns (score -3 to +3, summary string)."""
    total_pos = levels.get("total_pos_gex", 0)
    total_neg = levels.get("total_neg_gex", 0)
    call_res  = levels.get("call_resistance")
    put_sup   = levels.get("put_support")
    net       = total_pos + total_neg   # neg already negative

    score = 0
    lines = []

    if net > 0:
        score += 2
        lines.append(f"✅ Net GEX is <b>positive (₹{net:.1f}M)</b> — market makers are net-long gamma. "
                     "They sell into rallies and buy dips, suppressing volatility. Conditions favour range-bound or upward drift.")
    else:
        score -= 2
        lines.append(f"⚠️ Net GEX is <b>negative (₹{net:.1f}M)</b> — market makers are net-short gamma. "
                     "They must chase moves in the same direction, amplifying volatility. Expect larger swings.")

    if call_res and put_sup:
        rng  = call_res - put_sup
        pos  = (spot - put_sup) / rng if rng > 0 else 0.5
        lines.append(f"📍 Spot is at <b>{pos*100:.0f}%</b> of the GEX range "
                     f"(Support ₹{put_sup:,.0f} → Resistance ₹{call_res:,.0f}).")
        if pos > 0.75:
            score -= 1
            lines.append("🔴 Spot is near the <b>call resistance wall</b> — upside may be capped. Watch for rejection.")
        elif pos < 0.25:
            score += 1
            lines.append("🟢 Spot is near the <b>put support wall</b> — downside cushion is strong here.")
        else:
            lines.append("⬜ Spot is mid-range — no immediate GEX wall pressure in either direction.")

    verdict = _score_to_verdict(score, max_score=3)
    return score, " ".join(lines), verdict


def _score_vanna(vf: pd.DataFrame, spot: float) -> tuple[int, str, str]:
    """Score Vanna signal."""
    above = float(vf[vf["strike"] > spot]["net_vanna"].sum())
    below = float(vf[vf["strike"] < spot]["net_vanna"].sum())
    score = 0
    lines = []

    if above > 0:
        score += 1
        lines.append(f"🟢 <b>Positive Vanna above spot ({above:.1f})</b> — if IV expands, dealers must "
                     "BUY the underlying to re-hedge. This creates a natural tailwind during vol spikes.")
    else:
        score -= 1
        lines.append(f"🔴 <b>Negative Vanna above spot ({above:.1f})</b> — dealers would SELL into spot rallies "
                     "when IV rises. This creates headwinds for upward moves.")

    if below < 0:
        score -= 1
        lines.append(f"⚠️ <b>Negative Vanna below spot ({below:.1f})</b> — dealers sell into declines when "
                     "IV expands, amplifying downside. Put buyers beware of rapid unwinds.")
    else:
        score += 1
        lines.append(f"✅ <b>Positive Vanna below spot ({below:.1f})</b> — dealers would buy if spot drops, "
                     "providing a cushion against sharp selloffs.")

    dn_total = float(vf["net_delta_notional"].sum())
    lines.append(f"📊 Total net delta notional: <b>₹{dn_total:.0f}M</b> — "
                 f"{'bullish positioning dominates' if dn_total > 0 else 'bearish positioning dominates'}.")

    verdict = _score_to_verdict(score, max_score=2)
    return score, " ".join(lines), verdict


def _score_theta(chain: pd.DataFrame, spot: float) -> tuple[int, str, str]:
    """Score Theta/IV signal."""
    atm_0dte = chain[(chain["type"]=="call") & (chain["dte"]==0) &
                     (chain["strike"]==round(spot/50)*50)]["iv"]
    atm_28d  = chain[(chain["type"]=="call") & (chain["dte"]==28)]["iv"]

    iv_0dte = float(atm_0dte.values[0]) * 100 if not atm_0dte.empty else 18.0
    iv_28d  = float(atm_28d.mean()) * 100      if not atm_28d.empty  else 15.0
    premium = iv_0dte - iv_28d

    put_iv  = chain[(chain["type"]=="put")  & (chain["dte"]==0)]["iv"].mean()
    call_iv = chain[(chain["type"]=="call") & (chain["dte"]==0)]["iv"].mean()
    skew    = (float(put_iv) - float(call_iv)) * 100 if not np.isnan(put_iv) else 0

    score = 0
    lines = []

    if iv_0dte > 20:
        score -= 1
        lines.append(f"⚠️ <b>0DTE IV is elevated at {iv_0dte:.1f}%</b> — traders are paying a premium for "
                     "same-day protection. This signals <b>fear or expected intraday volatility</b>.")
    elif iv_0dte < 14:
        score += 1
        lines.append(f"✅ <b>0DTE IV is calm at {iv_0dte:.1f}%</b> — complacency in the options market. "
                     "Low premium suggests the market expects a quiet session — slightly bullish.")
    else:
        lines.append(f"⬜ <b>0DTE IV is neutral at {iv_0dte:.1f}%</b> — normal premium, no extreme fear or complacency.")

    if premium > 2.5:
        score -= 1
        lines.append(f"🔴 <b>0DTE premium over 28d IV: +{premium:.1f}%</b> — significant fear premium in "
                     "near-term options. Market is pricing heightened intraday risk.")
    elif premium < 0.5:
        score += 1
        lines.append(f"🟢 <b>Low term-structure premium: +{premium:.1f}%</b> — little extra fear baked "
                     "into near-term options. Calm conditions support upside momentum.")
    else:
        lines.append(f"📊 Term-structure premium: +{premium:.1f}% (28d IV: {iv_28d:.1f}%) — within normal range.")

    if skew > 1.5:
        score -= 1
        lines.append(f"🔴 <b>Put skew elevated at {skew:.2f}%</b> — institutions are paying up for downside "
                     "protection. Smart money is hedging — treat as a caution signal.")
    elif skew < 0.5:
        score += 1
        lines.append(f"🟢 <b>Put skew flat at {skew:.2f}%</b> — no heavy put buying detected. "
                     "Market not positioning defensively — bullish undertone.")
    else:
        lines.append(f"📊 Put-call IV skew: {skew:.2f}% — moderate, within expected range.")

    verdict = _score_to_verdict(score, max_score=3)
    return score, " ".join(lines), verdict


def _score_order_flow(ladder: pd.DataFrame) -> tuple[int, str, str]:
    """Score order flow signal."""
    call_net = int(ladder["call_flow"].sum())
    put_net  = int(ladder["put_flow"].sum())
    score    = 0
    lines    = []

    if call_net > 0:
        score += 1
        lines.append(f"🟢 <b>Net call buying: {call_net:+,d} contracts</b> — participants are net buyers "
                     "of calls at the current expiry. Directional bullish positioning or hedging.")
    else:
        score -= 1
        lines.append(f"🔴 <b>Net call selling: {call_net:+,d} contracts</b> — call sellers dominate, "
                     "suggesting participants expect the market to stay below resistance.")

    if put_net < 0:
        score += 1
        lines.append(f"✅ <b>Net put selling: {put_net:+,d} contracts</b> — participants are selling puts, "
                     "implying they expect prices to hold above current levels. Bullish signal.")
    else:
        score -= 1
        lines.append(f"⚠️ <b>Net put buying: {put_net:+,d} contracts</b> — active downside hedging. "
                     "Participants are buying protection — defensive or bearish positioning.")

    atm_rows = ladder[ladder["is_atm"]]
    if not atm_rows.empty:
        atm_call = int(atm_rows["call_flow"].values[0])
        atm_put  = int(atm_rows["put_flow"].values[0])
        lines.append(f"📍 <b>ATM flow</b>: Calls {atm_call:+,d} / Puts {atm_put:+,d} — "
                     f"{'call-side dominance near ATM is a short-term bullish cue.' if atm_call > atm_put else 'put-side dominance near ATM signals near-term caution.'}")

    verdict = _score_to_verdict(score, max_score=2)
    return score, " ".join(lines), verdict


def _score_iv_skew(chain: pd.DataFrame) -> tuple[int, str]:
    """Overall IV skew across term structure."""
    put_iv_all  = chain[chain["type"]=="put"]["iv"].mean()
    call_iv_all = chain[chain["type"]=="call"]["iv"].mean()
    skew = (float(put_iv_all) - float(call_iv_all)) * 100
    score = -1 if skew > 1.5 else (1 if skew < 0.5 else 0)
    return score, f"Overall put-call skew: {skew:.2f}%"


def _score_to_verdict(score: int, max_score: int = 3) -> str:
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.8:   return "STRONG BUY"
    elif ratio >= 0.4: return "BUY"
    elif ratio >= 0.1: return "WEAK BUY"
    elif ratio > -0.1: return "NEUTRAL"
    elif ratio > -0.4: return "WEAK SELL"
    elif ratio > -0.8: return "SELL"
    else:              return "STRONG SELL"


def _combined_verdict(gex_s, vanna_s, theta_s, flow_s, iv_s) -> tuple[str, int]:
    total = gex_s + vanna_s + theta_s + flow_s + iv_s
    if total >= 6:    verdict = "STRONG BUY"
    elif total >= 3:  verdict = "BUY"
    elif total >= 1:  verdict = "WEAK BUY"
    elif total == 0:  verdict = "NEUTRAL"
    elif total >= -2: verdict = "WEAK SELL"
    elif total >= -5: verdict = "SELL"
    else:             verdict = "STRONG SELL"
    return verdict, total


# ════════════════════════════════════════════════════════════════════════════
# GEMINI AI NARRATIVE (optional — falls back to rule-based if no key)
# ════════════════════════════════════════════════════════════════════════════

def _gemini_narrative(index_name: str, spot: float, verdict: str, total_score: int,
                       gex_summary: str, vanna_summary: str, theta_summary: str,
                       flow_summary: str, api_key: str) -> str:
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "You are a senior NSE derivatives desk analyst. "
                "Write crisp, professional, data-driven analysis in plain English. "
                "No fluff. Be direct. Use bullet points where helpful. "
                "Max 200 words. Educational only — not SEBI investment advice."
            )
        )
        prompt = f"""
Analyze {index_name} options market. Spot: {spot:,.0f}. Overall verdict: {verdict} (score {total_score:+d}/10).

GEX Signal: {gex_summary[:300]}
Vanna Signal: {vanna_summary[:300]}
IV/Theta Signal: {theta_summary[:300]}
Order Flow: {flow_summary[:300]}

Write a 3-4 sentence professional narrative explaining:
1. What the options market structure is telling us right now
2. The key risk or opportunity based on these signals
3. What a trader should watch for next

Keep it under 150 words. Plain text only, no markdown headers.
"""
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return ""   # silent fallback to rule-based


def _rule_based_narrative(verdict: str, total_score: int, index_name: str,
                           levels: dict, spot: float,
                           gex_s: int, vanna_s: int, theta_s: int, flow_s: int) -> str:
    """Pure Python narrative when no Gemini key."""
    strongest = max(
        [("GEX", gex_s), ("Vanna", vanna_s), ("IV/Theta", theta_s), ("Order Flow", flow_s)],
        key=lambda x: abs(x[1])
    )
    direction = "bullish" if total_score > 0 else "bearish" if total_score < 0 else "neutral"
    call_res  = levels.get("call_resistance")
    put_sup   = levels.get("put_support")

    parts = [
        f"The {index_name} options market is currently showing a <b>{direction}</b> structure "
        f"with a composite score of <b>{total_score:+d}/10</b>.",

        f"The strongest signal comes from <b>{strongest[0]}</b> "
        f"({'supporting upside' if strongest[1] > 0 else 'signalling downside risk'}).",
    ]

    if call_res and put_sup:
        parts.append(
            f"Key levels to watch: <b>₹{put_sup:,.0f}</b> (GEX put support) and "
            f"<b>₹{call_res:,.0f}</b> (GEX call resistance) — "
            f"a break {'above resistance would be significant' if total_score > 0 else 'below support would accelerate the move'}."
        )

    if verdict in ("STRONG BUY", "BUY"):
        parts.append("Options positioning favours longs — consider entries on dips toward the put support wall.")
    elif verdict in ("STRONG SELL", "SELL"):
        parts.append("Options positioning favours shorts — watch for rejection at the call resistance wall.")
    elif verdict in ("WEAK BUY", "WEAK SELL"):
        parts.append("Signals are mixed. Wait for confirmation — avoid large directional positions until alignment improves.")
    else:
        parts.append("No clear edge from options signals. Best to remain flat or trade the GEX range with tight stops.")

    parts.append("<br><span style='font-size:11px;color:#8593A3;'>Educational only · Not SEBI investment advice.</span>")
    return " ".join(parts)


# ════════════════════════════════════════════════════════════════════════════
# BLACK-SCHOLES ENGINE
# ════════════════════════════════════════════════════════════════════════════

def bs_price(S, K, T, r, sigma, option_type="call"):
    if T <= 1e-6 or sigma <= 1e-6:
        return max(S-K, 0.0) if option_type=="call" else max(K-S, 0.0)
    d1 = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    d2 = d1 - sigma*np.sqrt(T)
    if option_type == "call":
        return S*norm.cdf(d1) - K*np.exp(-r*T)*norm.cdf(d2)
    return K*np.exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)


def bs_greeks(S, K, T, r, sigma, option_type="call"):
    if T <= 1e-6 or sigma <= 1e-6:
        return dict(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, vanna=0.0)
    d1  = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    d2  = d1 - sigma*np.sqrt(T)
    nd1 = norm.pdf(d1)
    gamma = nd1 / (S*sigma*np.sqrt(T))
    vega  = S*nd1*np.sqrt(T)/100.0
    vanna = -nd1*d2/sigma
    if option_type == "call":
        delta = norm.cdf(d1)
        theta = (-S*nd1*sigma/(2.0*np.sqrt(T)) - r*K*np.exp(-r*T)*norm.cdf(d2)) / TRADING_DAYS
    else:
        delta = norm.cdf(d1) - 1.0
        theta = (-S*nd1*sigma/(2.0*np.sqrt(T)) + r*K*np.exp(-r*T)*norm.cdf(-d2)) / TRADING_DAYS
    return dict(delta=delta, gamma=gamma, theta=theta, vega=vega, vanna=vanna)


# ════════════════════════════════════════════════════════════════════════════
# MOCK OPTIONS CHAIN
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def generate_mock_options_chain(spot=NSE_SPOT_REF, r=0.065, index_name="NIFTY"):
    rng      = np.random.default_rng(42)
    today    = datetime.date.today()
    dte_list = [0, 7, 14, 28, 56, 91, 180]
    expiries = [(today + datetime.timedelta(days=d)) for d in dte_list]
    atm      = round(spot/50)*50
    strikes  = np.arange(atm - 10*50, atm + 11*50, 50)
    rows     = []
    for exp_date, dte in zip(expiries, dte_list):
        T = max(dte, 0.5) / TRADING_DAYS
        for K in strikes:
            m  = np.log(K/spot)
            iv = float(np.clip(
                0.15 + 0.02*abs(m) - 0.08*m + 0.12*m**2
                + 0.03*np.exp(-dte/30) + rng.normal(0, 0.003),
                0.05, 0.90))
            for otype in ["call", "put"]:
                price  = bs_price(spot, K, T, r, iv, otype)
                greeks = bs_greeks(spot, K, T, r, iv, otype)
                oi     = max(int(rng.lognormal(8 - 4*abs(m), 0.4)*10), 100)
                vol    = int(oi * rng.uniform(0.1, 0.6))
                ba     = price * rng.uniform(0.01, 0.04)
                rows.append({
                    "expiry": exp_date, "dte": dte, "strike": float(K),
                    "type": otype, "spot": spot, "iv": round(iv, 4),
                    "price": round(price, 2),
                    "bid": round(price - ba/2, 2), "ask": round(price + ba/2, 2),
                    "oi": oi, "volume": vol,
                    "delta": round(greeks["delta"], 4),
                    "gamma": round(greeks["gamma"], 6),
                    "theta": round(greeks["theta"], 4),
                    "vega":  round(greeks["vega"],  4),
                    "vanna": round(greeks["vanna"],  6),
                    "moneyness": round(m, 4),
                })
    df = pd.DataFrame(rows)
    df["net_flow"] = (df["volume"]
        * np.where(np.random.default_rng(7).uniform(0,1,len(df)) > 0.5, 1, -1)
        * np.random.default_rng(8).uniform(0.2, 1.0, len(df))).astype(int)
    return df


# ════════════════════════════════════════════════════════════════════════════
# ANALYTICS FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def build_iv_surface(chain, max_strikes=20, max_expiries=12):
    calls       = chain[chain["type"]=="call"].copy()
    all_strikes = sorted(calls["strike"].unique())
    step        = max(1, len(all_strikes)//max_strikes)
    strikes_sel = all_strikes[::step][:max_strikes]
    all_dte     = sorted(calls["dte"].unique())[:max_expiries]
    iv_calls    = np.full((len(all_dte), len(strikes_sel)), np.nan)
    iv_puts     = np.full((len(all_dte), len(strikes_sel)), np.nan)
    for i, dte in enumerate(all_dte):
        sub   = calls[calls["dte"]==dte]
        sub_p = chain[(chain["type"]=="put") & (chain["dte"]==dte)]
        for j, K in enumerate(strikes_sel):
            r = sub[sub["strike"]==K]; rp = sub_p[sub_p["strike"]==K]
            if not r.empty:  iv_calls[i,j] = r.iloc[0]["iv"]
            if not rp.empty: iv_puts[i,j]  = rp.iloc[0]["iv"]
    return np.array(strikes_sel), np.array(all_dte), iv_calls, iv_puts


@st.cache_data(ttl=300, show_spinner=False)
def build_0dte_smile(chain):
    z = chain[chain["dte"]==0].copy()
    z["iv_pct"] = z["iv"]*100
    return z.sort_values("strike")


@st.cache_data(ttl=300, show_spinner=False)
def compute_gex(chain, lot_size=NSE_LOT):
    sub = chain[chain["dte"]==chain["dte"].min()].copy()
    sub["gex_unit"] = sub["gamma"]*sub["oi"]*lot_size*sub["spot"]
    calls = sub[sub["type"]=="call"][["strike","gex_unit"]].rename(columns={"gex_unit":"call_gex"})
    puts  = sub[sub["type"]=="put"][["strike","gex_unit"]].rename(columns={"gex_unit":"put_gex"})
    gex   = pd.merge(calls, puts, on="strike", how="outer").fillna(0)
    gex["net_gex"]  = (gex["call_gex"] - gex["put_gex"])/1e6
    gex["call_gex"] =  gex["call_gex"]/1e6
    gex["put_gex"]  = -gex["put_gex"]/1e6
    return gex.sort_values("strike").reset_index(drop=True)


def find_gex_levels(gex, spot):
    above = gex[gex["strike"]>spot]; below = gex[gex["strike"]<spot]
    call_wall = (above.loc[above["net_gex"].idxmax(),"strike"]
                 if not above.empty and above["net_gex"].max()>0 else None)
    put_wall  = (below.loc[below["net_gex"].idxmin(),"strike"]
                 if not below.empty and below["net_gex"].min()<0 else None)
    pos = gex[gex["net_gex"]>0]; neg = gex[gex["net_gex"]<0]
    return {"call_resistance": call_wall, "put_support": put_wall,
            "total_pos_gex": float(pos["net_gex"].sum()),
            "total_neg_gex": float(neg["net_gex"].sum())}


@st.cache_data(ttl=300, show_spinner=False)
def compute_vanna_flow(chain, lot_size=NSE_LOT):
    sub = chain[chain["dte"]==chain["dte"].min()].copy()
    sub["delta_notional"] = sub["delta"]*sub["oi"]*lot_size*sub["spot"]/1e6
    sub["vanna_exp"]      = sub["vanna"]*sub["oi"]*lot_size/1e3
    calls = sub[sub["type"]=="call"][["strike","delta_notional","vanna_exp"]].rename(
        columns={"delta_notional":"call_dn","vanna_exp":"call_vanna"})
    puts  = sub[sub["type"]=="put"][["strike","delta_notional","vanna_exp"]].rename(
        columns={"delta_notional":"put_dn","vanna_exp":"put_vanna"})
    vf = pd.merge(calls, puts, on="strike", how="outer").fillna(0)
    vf["net_delta_notional"] = vf["call_dn"] + vf["put_dn"]
    vf["net_vanna"]          = vf["call_vanna"] + vf["put_vanna"]
    return vf.sort_values("strike").reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner=False)
def theta_decay_curves(spot=NSE_SPOT_REF, r=0.065, sigma=0.15, horizons=None):
    if horizons is None: horizons = [90, 60, 30, 7]
    rows = []
    for s in horizons:
        for dte in range(s, 0, -1):
            T = dte/TRADING_DAYS
            g = bs_greeks(spot, spot, T, r, sigma, "call")
            rows.append({"start_dte":s,"dte":dte,
                          "theta":g["theta"],"theta_pct":abs(g["theta"])/spot*100})
    return pd.DataFrame(rows)


def generate_order_flow_ladder(chain, spot, n_strikes=12):
    sub         = chain[chain["dte"]==chain["dte"].min()].copy()
    atm         = round(spot/50)*50
    sel_strikes = sorted(sub["strike"].unique(), key=lambda k: abs(k-atm))[:n_strikes]
    rows = []
    for K in sorted(sel_strikes):
        cr = sub[(sub["strike"]==K)&(sub["type"]=="call")]
        pr = sub[(sub["strike"]==K)&(sub["type"]=="put")]
        rows.append({
            "strike":K, "is_atm": abs(K-atm)<=25,
            "call_flow": int(cr["net_flow"].values[0]) if not cr.empty else 0,
            "put_flow":  int(pr["net_flow"].values[0]) if not pr.empty else 0,
            "call_oi":   int(cr["oi"].values[0])       if not cr.empty else 0,
            "put_oi":    int(pr["oi"].values[0])        if not pr.empty else 0,
            "call_iv":   round(float(cr["iv"].values[0])*100,1) if not cr.empty else 0,
            "put_iv":    round(float(pr["iv"].values[0])*100,1) if not pr.empty else 0,
        })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════════
# PLOTLY CHARTS — MODULE 1
# ════════════════════════════════════════════════════════════════════════════

def chart_iv_surface_3d(strikes, dte, iv_calls, iv_puts):
    fig = go.Figure()
    fig.add_trace(go.Surface(x=strikes, y=dte, z=iv_calls*100,
        colorscale=[[0,"#1a2a4a"],[0.5,"#4C8DD6"],[1,"#C9A227"]],
        name="Call IV", opacity=0.85, showscale=True,
        colorbar=dict(x=1.02, title=dict(text="IV %", font=dict(color=ACCENT,size=10)),
                      tickfont=dict(color=IVORY,size=9))))
    fig.add_trace(go.Surface(x=strikes, y=dte, z=iv_puts*100,
        colorscale=[[0,"#1a2a3a"],[0.5,"#E8554E"],[1,"#ff9999"]],
        name="Put IV", opacity=0.55, showscale=False))
    fig.update_layout(template="plotly_dark",
        title=dict(text="3D IV SURFACE · CALLS (gold) / PUTS (red)",
                   font=dict(size=12, color=ACCENT)),
        paper_bgcolor=PANEL, height=520,
        scene=dict(
            xaxis=dict(title="Strike", gridcolor=BORDER, color=IVORY),
            yaxis=dict(title="DTE",    gridcolor=BORDER, color=IVORY),
            zaxis=dict(title="IV %",   gridcolor=BORDER, color=IVORY),
            bgcolor=BG, camera=dict(eye=dict(x=1.5,y=-1.8,z=0.8))),
        font=dict(family="monospace", size=10, color=IVORY),
        margin=dict(l=0,r=0,t=50,b=0))
    return fig


def chart_0dte_smile(smile_df, spot):
    calls = smile_df[smile_df["type"]=="call"]
    puts  = smile_df[smile_df["type"]=="put"]
    fig   = go.Figure()
    fig.add_trace(go.Scatter(x=calls["strike"], y=calls["iv_pct"],
        mode="lines+markers", name="Call IV",
        line=dict(color=GREEN,width=2), marker=dict(size=5)))
    fig.add_trace(go.Scatter(x=puts["strike"], y=puts["iv_pct"],
        mode="lines+markers", name="Put IV",
        line=dict(color=RED,width=2), marker=dict(size=5)))
    fig.add_vline(x=spot, line_color=ACCENT, line_dash="dash", line_width=1.5,
                  annotation_text=f"Spot {spot:,.0f}", annotation_font_color=ACCENT)
    fig.update_layout(showlegend=True, legend=dict(orientation="h",y=1.05,x=0))
    return _layout(fig, "0DTE IV SMILE / SMIRK", height=340)


def chart_theta_decay(theta_df):
    fig    = go.Figure()
    colors = [ACCENT, BLUE, GREEN, PURPLE]
    for i, (start, grp) in enumerate(theta_df.groupby("start_dte")):
        fig.add_trace(go.Scatter(x=grp["dte"], y=grp["theta_pct"],
            mode="lines", name=f"{int(start)}DTE",
            line=dict(color=colors[i%len(colors)], width=2)))
    fig.update_xaxes(autorange="reversed")
    fig.update_layout(showlegend=True, legend=dict(orientation="h",y=1.05,x=0))
    return _layout(fig, "NON-LINEAR THETA DECAY · ATM OPTION (% spot / day)", height=340)


def chart_gex_profile(gex, spot, levels):
    colors = [GREEN if v>=0 else RED for v in gex["net_gex"]]
    fig    = go.Figure()
    fig.add_trace(go.Bar(y=gex["strike"].astype(str), x=gex["net_gex"],
        orientation="h", marker_color=colors, opacity=0.85,
        text=[f"{v:+.1f}M" for v in gex["net_gex"]],
        textposition="outside", textfont=dict(size=9,color=IVORY)))
    all_s = [str(int(k)) for k in sorted(gex["strike"].unique())]
    def _si(k):
        s=str(int(k)); return all_s.index(s) if s in all_s else None
    if levels.get("call_resistance"):
        idx = _si(levels["call_resistance"])
        if idx is not None:
            fig.add_shape(type="line",y0=idx-.4,y1=idx+.4,x0=-50,x1=50,
                xref="x",yref="y",line=dict(color=RED,dash="dot",width=1.5))
            fig.add_annotation(x=50,y=idx,text="Call Resistance",
                showarrow=False,font=dict(color=RED,size=9),xanchor="right")
    if levels.get("put_support"):
        idx = _si(levels["put_support"])
        if idx is not None:
            fig.add_shape(type="line",y0=idx-.4,y1=idx+.4,x0=-50,x1=50,
                xref="x",yref="y",line=dict(color=GREEN,dash="dot",width=1.5))
            fig.add_annotation(x=50,y=idx,text="Put Support",
                showarrow=False,font=dict(color=GREEN,size=9),xanchor="right")
    si = _si(round(spot/50)*50)
    if si is not None:
        fig.add_shape(type="line",y0=si-.5,y1=si+.5,x0=-200,x1=200,
            xref="x",yref="y",line=dict(color=ACCENT,width=2))
        fig.add_annotation(x=-200,y=si,text=f"Spot {spot:,.0f}",
            showarrow=False,font=dict(color=ACCENT,size=9),xanchor="left")
    fig.add_vline(x=0, line_color=MUTE, line_width=1)
    fig.update_layout(showlegend=False)
    return _layout(fig, "NET GAMMA EXPOSURE · ₹M · +Green=Support / -Red=Resistance", height=460)


def chart_vanna_flow(vf, spot):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
        subplot_titles=["Net Delta Notional (₹M)", "Net Vanna Exposure"],
        vertical_spacing=0.08)
    dn_c = [GREEN if v>=0 else RED    for v in vf["net_delta_notional"]]
    vn_c = [PURPLE if v>=0 else ORANGE for v in vf["net_vanna"]]
    fig.add_trace(go.Bar(x=vf["strike"],y=vf["net_delta_notional"],
        marker_color=dn_c,opacity=0.8,name="ΔNotional"), row=1,col=1)
    fig.add_trace(go.Bar(x=vf["strike"],y=vf["net_vanna"],
        marker_color=vn_c,opacity=0.8,name="Vanna"), row=2,col=1)
    for r in [1,2]:
        fig.add_vline(x=spot,line_color=ACCENT,line_dash="dash",line_width=1.5,row=r,col=1)
    fig.update_layout(template="plotly_dark",paper_bgcolor=PANEL,plot_bgcolor=PANEL,
        height=440,showlegend=False,margin=dict(l=8,r=8,t=50,b=8),
        font=dict(family="monospace",size=11,color=IVORY),
        xaxis=dict(gridcolor=BORDER),yaxis=dict(gridcolor=BORDER),
        xaxis2=dict(gridcolor=BORDER),yaxis2=dict(gridcolor=BORDER),
        title=dict(text="VANNA FLOW · Dealer Rehedge on Vol Expansion/Compression",
                   font=dict(size=12,color=ACCENT)))
    return fig


def chart_order_flow_ladder(ladder, spot):
    fig = make_subplots(rows=1, cols=2,
        subplot_titles=["← PUT FLOW","CALL FLOW →"],
        shared_yaxes=True, horizontal_spacing=0.02)
    for _, row in ladder.iterrows():
        K = row["strike"]
        c = ACCENT if row["is_atm"] else (GREEN if row["call_flow"]>0 else RED)
        fig.add_trace(go.Bar(y=[K],x=[row["call_flow"]],orientation="h",
            marker_color=c,opacity=0.85,showlegend=False,
            text=[f"{row['call_flow']:+,d}"],textposition="outside",
            textfont=dict(size=9)), row=1,col=2)
    for _, row in ladder.iterrows():
        K = row["strike"]
        c = ACCENT if row["is_atm"] else (GREEN if row["put_flow"]>0 else RED)
        fig.add_trace(go.Bar(y=[K],x=[-row["put_flow"]],orientation="h",
            marker_color=c,opacity=0.85,showlegend=False,
            text=[f"{row['put_flow']:+,d}"],textposition="outside",
            textfont=dict(size=9)), row=1,col=1)
    fig.update_layout(template="plotly_dark",paper_bgcolor=PANEL,plot_bgcolor=PANEL,
        height=460,barmode="overlay",margin=dict(l=8,r=8,t=50,b=8),
        font=dict(family="monospace",size=11,color=IVORY),
        yaxis=dict(gridcolor=BORDER),
        title=dict(text="ORDER FLOW LADDER · Net Contracts (Bought - Sold)",
                   font=dict(size=12,color=ACCENT)))
    return fig


# ════════════════════════════════════════════════════════════════════════════
# OPTIONS-SIGNAL-BASED BACKTESTING ENGINE
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def fetch_ohlcv(symbol, start, end):
    try:
        import yfinance as yf
        sym = symbol.strip().upper()
        if not sym.endswith(".NS") and "^" not in sym:
            sym += ".NS"
        df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty: return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open","High","Low","Close","Volume"]].dropna()
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        st.warning(f"Data fetch error: {e}")
        return pd.DataFrame()


def _compute_indicators(df):
    c = df["Close"].copy(); df = df.copy()
    for p in [20,50,200]: df[f"MA{p}"] = c.rolling(p).mean()
    df["EMA20"] = c.ewm(span=20,adjust=False).mean()
    delta = c.diff(); gain = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI"] = 100 - (100/(1 + gain/loss.replace(0,np.nan)))
    mid = c.rolling(20).mean(); std = c.rolling(20).std()
    df["BB_upper"]=mid+2*std; df["BB_lower"]=mid-2*std; df["BB_mid"]=mid
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    df["MACD"]=ema12-ema26; df["MACD_signal"]=df["MACD"].ewm(span=9,adjust=False).mean()
    hl=(df["High"]-df["Low"]); hc=(df["High"]-df["Close"].shift()).abs()
    lc=(df["Low"]-df["Close"].shift()).abs()
    df["ATR"] = pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(14).mean()
    df["daily_ret"] = np.log(c/c.shift(1))
    df["vol_20"]    = df["daily_ret"].rolling(20).std()*np.sqrt(TRADING_DAYS)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def run_options_backtest(
    df_raw, gex_support, gex_resistance, vanna_bias, iv_regime,
    verdict, initial_cap=1_000_000.0, commission=0.0015,
    slippage=0.0005, stop_pct=0.03, target_pct=0.06,
    max_hold=15, position_pct=0.10
):
    """
    Backtests a strategy derived from the CURRENT options signals:
    - GEX support/resistance as key price levels
    - Vanna bias as directional filter
    - IV regime to size entries
    - Overall verdict to pick long/short
    """
    if df_raw.empty or len(df_raw) < 60:
        return {"error": "Need 60+ bars of data."}

    df = _compute_indicators(df_raw)
    df = df.dropna(subset=["MA50","RSI","BB_upper","MACD_signal"])
    if len(df) < 30:
        return {"error": "Too few bars after indicators — widen date range."}

    # Determine strategy direction from verdict
    direction = "long"
    if verdict in ("SELL","STRONG SELL","WEAK SELL"): direction = "short"
    elif verdict == "NEUTRAL": direction = "both"

    equity = initial_cap; trades = []; equity_curve = []
    position = None; holding_days = 0

    for i, (date, row) in enumerate(df.iterrows()):
        equity_curve.append({"date": date, "equity": equity})
        price = float(row["Close"])

        if position is None:
            sig = 0

            # Long signals: near GEX support OR RSI oversold + bullish vanna
            near_support = (gex_support is not None and
                            abs(price - gex_support)/price < 0.02 and
                            price > gex_support * 0.98)
            near_resist  = (gex_resistance is not None and
                            abs(price - gex_resistance)/price < 0.02 and
                            price < gex_resistance * 1.02)

            vanna_bull  = vanna_bias == "BULLISH"
            vanna_bear  = vanna_bias == "BEARISH"
            iv_calm     = iv_regime  == "CALM"
            rsi_os      = float(row["RSI"]) < 38
            rsi_ob      = float(row["RSI"]) > 62
            ma_bull     = float(row["MA20"]) > float(row["MA50"])
            ma_bear     = float(row["MA20"]) < float(row["MA50"])
            macd_bull   = float(row["MACD"]) > float(row["MACD_signal"])
            macd_bear   = float(row["MACD"]) < float(row["MACD_signal"])

            # Long entry conditions
            long_conditions = [
                near_support and vanna_bull,
                rsi_os and ma_bull and vanna_bull,
                near_support and macd_bull,
                ma_bull and macd_bull and iv_calm,
            ]
            # Short entry conditions
            short_conditions = [
                near_resist and vanna_bear,
                rsi_ob and ma_bear and vanna_bear,
                near_resist and macd_bear,
                ma_bear and macd_bear,
            ]

            if direction in ("long","both")  and any(long_conditions):  sig = 1
            if direction in ("short","both") and any(short_conditions): sig = -1
            if direction == "long"  and sig == -1: sig = 0
            if direction == "short" and sig ==  1: sig = 0

            if sig != 0:
                exec_price = price * (1 + sig*slippage)
                invest     = equity * position_pct
                shares     = invest / exec_price
                position   = {
                    "side": sig, "entry_price": exec_price,
                    "entry_date": date, "shares": shares,
                    "cost_in": invest*commission,
                }
                holding_days = 0
        else:
            holding_days += 1
            side = position["side"]
            ep   = position["entry_price"]
            # Exit: stop, target, GEX flip, time
            at_stop   = (side==1 and price<=ep*(1-stop_pct)) or (side==-1 and price>=ep*(1+stop_pct))
            at_target = (side==1 and price>=ep*(1+target_pct)) or (side==-1 and price<=ep*(1-target_pct))
            at_gex_flip = (
                (side==1 and gex_resistance and price >= gex_resistance*0.998) or
                (side==-1 and gex_support    and price <= gex_support*1.002)
            )
            at_time   = holding_days >= max_hold
            ma_flip   = (side==1 and float(row["MA20"])<float(row["MA50"])) or \
                        (side==-1 and float(row["MA20"])>float(row["MA50"]))

            if at_stop or at_target or at_gex_flip or at_time or ma_flip or i==len(df)-1:
                exit_price = price*(1-side*slippage)
                cost_out   = position["shares"]*exit_price*commission
                net_pnl    = position["shares"]*(exit_price - ep)*side - (position["cost_in"]+cost_out)
                pnl_pct    = side*(exit_price/ep - 1)
                equity    += net_pnl
                exit_reason = ("Stop" if at_stop else "Target" if at_target else
                               "GEX Level" if at_gex_flip else "MA Flip" if ma_flip else "Time")
                trades.append({
                    "entry_date":  position["entry_date"],
                    "exit_date":   date,
                    "side":        "LONG" if side==1 else "SHORT",
                    "entry_price": round(ep, 2),
                    "exit_price":  round(exit_price, 2),
                    "shares":      round(position["shares"], 2),
                    "net_pnl":     round(net_pnl, 2),
                    "pnl_pct":     round(pnl_pct*100, 3),
                    "hold_days":   holding_days,
                    "exit_reason": exit_reason,
                    "outcome":     "WIN" if net_pnl>0 else "LOSS",
                })
                position = None; holding_days = 0

    if not trades:
        return {"error": "No trades generated — the GEX levels may not have been tested in this period. Try a wider date range."}

    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_ser= eq_df["equity"]
    tdf   = pd.DataFrame(trades)
    rets  = eq_ser.pct_change().dropna()

    wins   = tdf[tdf["outcome"]=="WIN"]; losses = tdf[tdf["outcome"]=="LOSS"]
    wr     = len(wins)/len(tdf)*100 if len(tdf) else 0
    avg_w  = wins["pnl_pct"].mean()   if len(wins)   else 0.0
    avg_l  = losses["pnl_pct"].mean() if len(losses) else 0.0
    pf     = abs(wins["net_pnl"].sum()/losses["net_pnl"].sum()) \
             if len(losses) and losses["net_pnl"].sum()!=0 else 0.0
    rr     = abs(avg_w/avg_l) if avg_l!=0 else 0.0
    peak   = eq_ser.cummax(); dd = (eq_ser-peak)/peak*100
    max_dd = float(dd.min())
    dd_dur = cur = 0
    for v in dd.values:
        if v<0: cur+=1; dd_dur=max(dd_dur,cur)
        else: cur=0
    ann_r  = float(rets.mean()*TRADING_DAYS)
    ann_v  = float(rets.std()*np.sqrt(TRADING_DAYS))
    dv     = rets[rets<0].std()*np.sqrt(TRADING_DAYS)
    sharpe = ann_r/ann_v  if ann_v>0  else 0.0
    sortino= ann_r/dv     if dv>0     else 0.0
    tot_r  = (equity-initial_cap)/initial_cap*100
    max_cl = cl = 0
    for o in tdf["outcome"].values:
        if o=="LOSS": cl+=1; max_cl=max(max_cl,cl)
        else: cl=0
    monthly = tdf.copy()
    monthly["month"] = pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    monthly_pnl = monthly.groupby("month")["net_pnl"].sum()

    # Strategy validity verdict
    strat_verdict = (
        "STRATEGY VALIDATED ✅" if (pf>=1.2 and wr>=45 and sharpe>=0.3) else
        "MARGINAL EDGE ⚠️"       if (pf>=1.0 and wr>=40) else
        "STRATEGY FAILED ❌"
    )

    return {
        "equity_curve":    eq_df,
        "trade_log":       tdf,
        "monthly_pnl":     monthly_pnl,
        "drawdown_series": dd,
        "total_trades":    len(tdf),
        "win_rate":        round(wr,1),
        "avg_win":         round(avg_w,2),
        "avg_loss":        round(avg_l,2),
        "rr_ratio":        round(rr,2),
        "profit_factor":   round(pf,2),
        "max_dd":          round(max_dd,2),
        "max_dd_dur":      dd_dur,
        "sharpe":          round(sharpe,3),
        "sortino":         round(sortino,3),
        "ann_ret":         round(ann_r*100,2),
        "ann_vol":         round(ann_v*100,2),
        "total_ret":       round(tot_r,2),
        "final_equity":    round(equity,2),
        "max_consec_loss": max_cl,
        "expectancy":      round(tdf["net_pnl"].mean(),2),
        "strat_verdict":   strat_verdict,
        "direction":       direction,
    }


# ════════════════════════════════════════════════════════════════════════════
# BACKTEST CHARTS
# ════════════════════════════════════════════════════════════════════════════

def chart_equity_curve(bt):
    eq  = bt["equity_curve"]["equity"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index,y=eq.values,
        line=dict(color=ACCENT,width=2),fill="tozeroy",
        fillcolor="rgba(201,162,39,0.07)",name="Equity"))
    fig.add_hline(y=eq.iloc[0],line_color=MUTE,line_dash="dash",line_width=1,
                  annotation_text="Initial Capital",annotation_font_color=MUTE)
    return _layout(fig,"EQUITY CURVE (₹)",height=320)


def chart_drawdown(bt):
    dd  = bt["drawdown_series"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dd.index,y=dd.values,
        line=dict(color=RED,width=1.5),fill="tozeroy",
        fillcolor="rgba(232,85,78,0.15)",name="DD%"))
    return _layout(fig,"STRATEGY DRAWDOWN (%)",height=240)


def chart_trade_bars(bt):
    tdf    = bt["trade_log"]
    colors = [GREEN if o=="WIN" else RED for o in tdf["outcome"]]
    fig    = go.Figure(go.Bar(x=tdf["exit_date"].astype(str),y=tdf["pnl_pct"],
        marker_color=colors,opacity=0.85,
        text=[f"{v:+.1f}%" for v in tdf["pnl_pct"]],
        textposition="outside",textfont=dict(size=8)))
    fig.add_hline(y=0,line_color=MUTE,line_width=1)
    m = tdf["pnl_pct"].mean()
    fig.add_hline(y=m,line_color=ACCENT,line_dash="dash",line_width=1.5,
                  annotation_text=f"Avg {m:+.2f}%",annotation_font_color=ACCENT)
    fig.update_layout(xaxis_tickangle=-45,showlegend=False)
    return _layout(fig,f"TRADE-BY-TRADE P&L · {len(tdf)} TRADES",height=320)


def chart_monthly_pnl(bt):
    mp     = bt["monthly_pnl"]
    colors = [GREEN if v>=0 else RED for v in mp.values]
    fig    = go.Figure(go.Bar(x=[str(p) for p in mp.index],y=mp.values,
        marker_color=colors,opacity=0.85,
        text=[f"₹{v:,.0f}" for v in mp.values],
        textposition="outside",textfont=dict(size=8)))
    fig.add_hline(y=0,line_color=MUTE,line_width=1)
    fig.update_layout(xaxis_tickangle=-45,showlegend=False)
    return _layout(fig,"MONTHLY NET P&L (₹)",height=280)


def chart_exit_reasons(bt):
    tdf   = bt["trade_log"]
    if "exit_reason" not in tdf.columns:
        return go.Figure()
    er    = tdf.groupby(["exit_reason","outcome"]).size().unstack(fill_value=0)
    fig   = go.Figure()
    for outcome, color in [("WIN",GREEN),("LOSS",RED)]:
        if outcome in er.columns:
            fig.add_trace(go.Bar(name=outcome,x=er.index,y=er[outcome],
                marker_color=color,opacity=0.85))
    fig.update_layout(barmode="stack",showlegend=True,
                      legend=dict(orientation="h",y=1.05,x=0))
    return _layout(fig,"EXIT REASON BREAKDOWN",height=260)


# ════════════════════════════════════════════════════════════════════════════
# UI: MODULE 1 — OPTIONS ANALYTICS WITH SIGNALS
# ════════════════════════════════════════════════════════════════════════════

def _render_options_analytics(api_key: str = ""):
    st.markdown(
        f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};"
        f"letter-spacing:1px;margin-bottom:8px;'>"
        f"📡 DATA: Simulated NSE chain (SVI-lite smile, realistic OI) · "
        f"Swap <code>generate_mock_options_chain()</code> for a live broker feed</div>",
        unsafe_allow_html=True)

    c1, c2, c3 = st.columns([2,1,1])
    index_name = c1.selectbox("Index", ["NIFTY","BANKNIFTY","FINNIFTY"])
    spot_map   = {"NIFTY":24_500.0,"BANKNIFTY":52_000.0,"FINNIFTY":23_000.0}
    spot       = spot_map[index_name]
    r_pct      = c2.number_input("Risk-Free Rate %", value=6.5, step=0.25, min_value=0.0)/100
    c3.button("🔄 REFRESH", type="primary", use_container_width=True)

    with st.spinner("Generating options chain..."):
        chain = generate_mock_options_chain(spot=spot, r=r_pct, index_name=index_name)

    # ── Pre-compute all signals ──────────────────────────────────────────
    with st.spinner("Scoring signals..."):
        gex         = compute_gex(chain)
        levels      = find_gex_levels(gex, spot)
        vf          = compute_vanna_flow(chain)
        ladder      = generate_order_flow_ladder(chain, spot)
        theta_df    = theta_decay_curves(spot=spot, r=r_pct)
        smile_df    = build_0dte_smile(chain)

        gex_s,  gex_txt,   gex_v   = _score_gex(gex, levels, spot)
        van_s,  van_txt,   van_v   = _score_vanna(vf, spot)
        th_s,   th_txt,    th_v    = _score_theta(chain, spot)
        fl_s,   fl_txt,    fl_v    = _score_order_flow(ladder)
        iv_s,   _                  = _score_iv_skew(chain)

        overall_v, total_score = _combined_verdict(gex_s, van_s, th_s, fl_s, iv_s)
        composite = {"gex":gex_s,"vanna":van_s,"theta":th_s,"flow":fl_s,"iv_skew":iv_s}

        vanna_bias = "BULLISH" if van_s > 0 else "BEARISH" if van_s < 0 else "NEUTRAL"
        iv_regime  = "ELEVATED" if th_s < 0 else "CALM"

    # ── AI narrative ────────────────────────────────────────────────────
    ai_note = ""
    if api_key:
        with st.spinner("🤖 Generating AI narrative..."):
            ai_note = _gemini_narrative(
                index_name, spot, overall_v, total_score,
                gex_txt, van_txt, th_txt, fl_txt, api_key)
    if not ai_note:
        ai_note = _rule_based_narrative(
            overall_v, total_score, index_name, levels, spot,
            gex_s, van_s, th_s, fl_s)

    # Store for backtest module
    st.session_state["_qa_signals"] = {
        "gex_support":    levels.get("put_support"),
        "gex_resistance": levels.get("call_resistance"),
        "vanna_bias":     vanna_bias,
        "iv_regime":      iv_regime,
        "verdict":        overall_v,
        "total_score":    total_score,
        "index_name":     index_name,
        "spot":           spot,
    }

    # ── Big verdict banner ───────────────────────────────────────────────
    _big_verdict_banner(overall_v, index_name, total_score, composite, ai_note)

    # ── 5 tabs ───────────────────────────────────────────────────────────
    t0, t1, t2, t3, t4 = st.tabs([
        "📈 IV SURFACE & SMILE",
        "📊 THETA DECAY",
        "🟩 NET GEX",
        "🌊 VANNA FLOW",
        "📋 ORDER FLOW",
    ])

    # IV Surface & Smile
    with t0:
        st.markdown(_sec("3D IMPLIED VOLATILITY SURFACE"), unsafe_allow_html=True)
        _cap("Downsampled 20×12 grid. Gold = Calls / Red = Puts. Drag to rotate.")
        strikes_arr, dte_arr, iv_calls, iv_puts = build_iv_surface(chain)
        st.plotly_chart(chart_iv_surface_3d(strikes_arr, dte_arr, iv_calls, iv_puts),
                        use_container_width=True)
        st.markdown(_sec("0DTE IV SMILE / SMIRK"), unsafe_allow_html=True)
        st.plotly_chart(chart_0dte_smile(smile_df, spot), use_container_width=True)
        atm_iv_val = chain[(chain["type"]=="call")&(chain["dte"]==0)&
                           (chain["strike"]==round(spot/50)*50)]["iv"]
        atm_iv = float(atm_iv_val.values[0])*100 if not atm_iv_val.empty else 0
        iv_28d = chain[(chain["dte"]==28)&(chain["type"]=="call")]["iv"].mean()*100
        _mrow([("0DTE ATM IV",f"{atm_iv:.1f}%"),
               ("Strikes (0DTE)",f"{len(smile_df)//2}"),
               ("Put−Call Skew",f"{(smile_df[smile_df['type']=='put']['iv'].mean()-smile_df[smile_df['type']=='call']['iv'].mean())*100:+.1f}%"),
               ("0DTE vs 28d Premium",f"+{atm_iv-iv_28d:.1f}%")])
        _signal_box("IV SURFACE READING", th_txt, th_v, th_s)

    # Theta Decay
    with t1:
        st.markdown(_sec("NON-LINEAR THETA DECAY CURVES"), unsafe_allow_html=True)
        _cap("ATM call theta accelerates toward 0DTE — square-root-of-time effect.")
        st.plotly_chart(chart_theta_decay(theta_df), use_container_width=True)
        cols = st.columns(4)
        for col, dte_sel in zip(cols, [90,30,7,1]):
            row_t = theta_df[(theta_df["start_dte"]==dte_sel)&
                             (theta_df["dte"]==max(dte_sel//2,1))].head(1)
            tv = row_t["theta_pct"].values[0] if not row_t.empty else 0
            col.metric(f"Theta DTE≈{dte_sel//2}", f"{tv:.4f}%/day")
        theta_summary = (
            f"Theta burn is {'rapid' if atm_iv>18 else 'moderate' if atm_iv>14 else 'slow'} "
            f"with 0DTE IV at {atm_iv:.1f}%. "
            f"{'Option sellers have an edge in this regime — theta decays fast, ideal for short premium strategies.' if atm_iv>16 else 'Low theta burn — option buyers have more time, premium is cheap.'}"
        )
        _signal_box("THETA SIGNAL", theta_summary,
                    "WEAK SELL" if atm_iv>18 else "NEUTRAL" if atm_iv>14 else "WEAK BUY",
                    -1 if atm_iv>18 else 0 if atm_iv>14 else 1)

    # GEX
    with t2:
        st.markdown(_sec("NET GAMMA EXPOSURE (GEX) PROFILE"), unsafe_allow_html=True)
        _cap("+GEX (green) → MM long gamma → sells rallies, buys dips → vol suppression. "
             "−GEX (red) → MM short gamma → chases moves → vol amplification.")
        st.plotly_chart(chart_gex_profile(gex, spot, levels), use_container_width=True)
        _mrow([
            ("Call Resistance", f"₹{levels['call_resistance']:,.0f}" if levels["call_resistance"] else "—"),
            ("Put Support",     f"₹{levels['put_support']:,.0f}"     if levels["put_support"]     else "—"),
            ("Total +GEX",      f"₹{levels['total_pos_gex']:.1f}M"),
            ("Total −GEX",      f"₹{levels['total_neg_gex']:.1f}M"),
        ])
        with st.expander("GEX Data Table"):
            st.dataframe(gex[["strike","call_gex","put_gex","net_gex"]].rename(columns={
                "strike":"Strike","call_gex":"Call GEX(M₹)",
                "put_gex":"Put GEX(M₹)","net_gex":"Net GEX(M₹)"}).round(2),
                use_container_width=True, hide_index=True)
        _signal_box("GEX SIGNAL", gex_txt, gex_v, gex_s)

    # Vanna
    with t3:
        st.markdown(_sec("VANNA FLOW — DEALER REHEDGE MECHANICS"), unsafe_allow_html=True)
        _cap("Vanna (dΔ/dσ): shows how dealer delta hedges move when IV changes. "
             "+Vanna above spot = buy pressure when vol rises.")
        st.plotly_chart(chart_vanna_flow(vf, spot), use_container_width=True)
        _mrow([
            ("Total Δ-Notional",   f"₹{vf['net_delta_notional'].sum():.1f}M"),
            ("Vanna (above spot)", f"{vf[vf['strike']>spot]['net_vanna'].sum():.1f}"),
            ("Vanna (below spot)", f"{vf[vf['strike']<spot]['net_vanna'].sum():.1f}"),
            ("Dominant Bias",      vanna_bias),
        ])
        _signal_box("VANNA SIGNAL", van_txt, van_v, van_s)

    # Order Flow
    with t4:
        st.markdown(_sec("LIVE OPTIONS ORDER FLOW LADDER"), unsafe_allow_html=True)
        _cap("Net contracts bought vs sold per strike at nearest expiry.")
        st.plotly_chart(chart_order_flow_ladder(ladder, spot), use_container_width=True)
        st.markdown(_sec("OPTIONS CHAIN SNAPSHOT"), unsafe_allow_html=True)
        d2 = ladder.copy()
        d2["CALL Flow"] = d2["call_flow"].apply(lambda x: f"{x:+,d}")
        d2["CALL OI"]   = d2["call_oi"].apply(lambda x: f"{x:,d}")
        d2["CALL IV%"]  = d2["call_iv"].apply(lambda x: f"{x:.1f}%")
        d2["STRIKE"]    = d2.apply(lambda r: f"{'★ ' if r['is_atm'] else ''}{r['strike']:,.0f}",axis=1)
        d2["PUT IV%"]   = d2["put_iv"].apply(lambda x: f"{x:.1f}%")
        d2["PUT OI"]    = d2["put_oi"].apply(lambda x: f"{x:,d}")
        d2["PUT Flow"]  = d2["put_flow"].apply(lambda x: f"{x:+,d}")
        st.dataframe(d2[["CALL Flow","CALL OI","CALL IV%","STRIKE","PUT IV%","PUT OI","PUT Flow"]],
                     use_container_width=True, hide_index=True)
        _cap("★ = ATM · Flow = net contracts (+ bought / − sold)")
        _signal_box("ORDER FLOW SIGNAL", fl_txt, fl_v, fl_s)

    return st.session_state["_qa_signals"]


# ════════════════════════════════════════════════════════════════════════════
# UI: MODULE 2 — OPTIONS-SIGNAL BACKTEST
# ════════════════════════════════════════════════════════════════════════════

def _render_options_backtest():
    sigs = st.session_state.get("_qa_signals", {})

    # ── Info banner ──────────────────────────────────────────────────────
    verdict    = sigs.get("verdict","NEUTRAL")
    index_name = sigs.get("index_name","NIFTY")
    spot       = sigs.get("spot", NSE_SPOT_REF)
    gex_sup    = sigs.get("gex_support")
    gex_res    = sigs.get("gex_resistance")
    vanna_bias = sigs.get("vanna_bias","NEUTRAL")
    iv_regime  = sigs.get("iv_regime","CALM")
    total_score= sigs.get("total_score",0)

    cfg   = VERDICT_CONFIG.get(verdict, VERDICT_CONFIG["NEUTRAL"])
    color = cfg["color"]

    st.markdown(f"""
    <div style='background:{PANEL2};border:1px solid {color}44;border-left:4px solid {color};
    border-radius:10px;padding:16px 20px;margin-bottom:16px;'>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};letter-spacing:1px;
        margin-bottom:6px;'>STRATEGY DERIVED FROM CURRENT OPTIONS SIGNALS</div>
      <div style='font-size:14px;color:{IVORY};line-height:1.9;'>
        Current verdict: <b style='color:{color};'>{verdict}</b> on <b>{index_name}</b>
        (score {total_score:+d}/10)<br>
        GEX Support: <b>₹{gex_sup:,.0f}</b> &nbsp;·&nbsp;
        GEX Resistance: <b>₹{gex_res:,.0f}</b><br>
        Vanna Bias: <b>{vanna_bias}</b> &nbsp;·&nbsp; IV Regime: <b>{iv_regime}</b><br>
        The backtest enters <b>{'LONG near GEX support' if verdict in ('BUY','STRONG BUY','WEAK BUY') else 'SHORT near GEX resistance' if verdict in ('SELL','STRONG SELL','WEAK SELL') else 'BOTH sides'}</b>
        and exits at GEX flip levels, MA reversals, or time stops.
      </div>
    </div>""".replace("₹None","—").replace("None","—"), unsafe_allow_html=True)

    if not sigs:
        st.warning("⚠️ Run Module 1 first — the backtest uses signals from the options analytics.")
        return

    # ── Config form ──────────────────────────────────────────────────────
    with st.form("opts_bt_form"):
        st.markdown(_sec("ASSET & DATE RANGE"), unsafe_allow_html=True)
        rc1, rc2 = st.columns(2)
        symbol     = rc1.text_input("NSE Symbol to Backtest",
                         value="^NSEI" if index_name=="NIFTY" else
                               "^NSEBANK" if index_name=="BANKNIFTY" else "NIFTY.NS",
                         help="Use ^NSEI for Nifty index, ^NSEBANK for Bank Nifty")
        direction  = rc2.selectbox("Force Direction",
                         ["From Options Signal","Long Only","Short Only","Both Sides"])

        dc1, dc2 = st.columns(2)
        start_date = dc1.date_input("Start Date", value=datetime.date(2022,1,1),
                         min_value=datetime.date(2010,1,1))
        end_date   = dc2.date_input("End Date", value=datetime.date.today())

        st.markdown(_sec("RISK PARAMETERS"), unsafe_allow_html=True)
        p1, p2, p3, p4 = st.columns(4)
        initial_cap  = p1.number_input("Capital (₹)", value=1_000_000, step=100_000, min_value=10_000)
        position_pct = p2.slider("Position Size %", 2, 40, 10)/100
        stop_pct     = p3.slider("Stop Loss %",     1, 15,  3)/100
        target_pct   = p4.slider("Target %",        2, 30,  6)/100

        p5, p6, p7 = st.columns(3)
        commission   = p5.number_input("Commission (bps)", value=15.0, step=1.0)/10_000
        slippage_val = p6.number_input("Slippage (bps)",   value=5.0,  step=1.0)/10_000
        max_hold     = p7.number_input("Max Hold Days",    value=15, min_value=1, max_value=60)

        run = st.form_submit_button("▶ BACKTEST THIS OPTIONS STRATEGY",
                                    type="primary", use_container_width=True)

    if not run:
        st.info("The backtest uses GEX levels as entry/exit triggers and Vanna bias as direction filter. "
                "Press the button above to run.")
        return

    if start_date >= end_date:
        st.error("Start date must be before end date."); return

    dir_override = {
        "From Options Signal": verdict,
        "Long Only":           "BUY",
        "Short Only":          "SELL",
        "Both Sides":          "NEUTRAL",
    }[direction]

    with st.spinner(f"Fetching {symbol.upper()} data..."):
        df_raw = fetch_ohlcv(symbol, str(start_date), str(end_date))

    if df_raw.empty:
        st.error(f"No data for '{symbol}'. Try ^NSEI for Nifty or RELIANCE for a stock."); return

    st.markdown(
        f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-bottom:4px;'>"
        f"Loaded {len(df_raw)} bars · "
        f"{df_raw.index[0].strftime('%Y-%m-%d')} → {df_raw.index[-1].strftime('%Y-%m-%d')}</div>",
        unsafe_allow_html=True)

    with st.spinner("Running options-signal backtest..."):
        bt = run_options_backtest(
            df_raw=df_raw,
            gex_support=gex_sup, gex_resistance=gex_res,
            vanna_bias=vanna_bias, iv_regime=iv_regime,
            verdict=dir_override,
            initial_cap=float(initial_cap),
            commission=commission, slippage=slippage_val,
            stop_pct=stop_pct, target_pct=target_pct,
            max_hold=int(max_hold), position_pct=position_pct,
        )

    if "error" in bt:
        st.error(bt["error"]); return

    # ── Strategy validity verdict ────────────────────────────────────────
    sv     = bt["strat_verdict"]
    sv_clr = GREEN if "✅" in sv else AMBER if "⚠️" in sv else RED
    st.markdown(f"""
    <div style='background:{"rgba(31,185,122,0.12)" if "✅" in sv else "rgba(245,158,11,0.12)" if "⚠️" in sv else "rgba(232,85,78,0.12)"};
    border:1px solid {sv_clr}44;border-left:5px solid {sv_clr};
    border-radius:10px;padding:16px 22px;margin:12px 0;'>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};margin-bottom:4px;'>
        OPTIONS-SIGNAL STRATEGY VALIDATION</div>
      <div style='font-family:{MONO};font-size:22px;font-weight:800;color:{sv_clr};'>{sv}</div>
      <div style='font-family:{MONO};font-size:13px;color:{IVORY};margin-top:6px;'>
        {symbol.upper()} · {start_date} → {end_date} ·
        Total Return: {bt['total_ret']:+.2f}% ·
        {bt['total_trades']} trades ·
        Sharpe: {bt['sharpe']} · Max DD: {bt['max_dd']:.1f}%
      </div>
    </div>""", unsafe_allow_html=True)

    # ── Metrics ──────────────────────────────────────────────────────────
    st.markdown(_sec("PERFORMANCE METRICS"), unsafe_allow_html=True)
    _mrow([("Total Trades",str(bt["total_trades"])),("Win Rate",f"{bt['win_rate']}%"),
           ("Avg Win",f"{bt['avg_win']:+.2f}%"),("Avg Loss",f"{bt['avg_loss']:+.2f}%")])
    _mrow([("R:R Ratio",f"{bt['rr_ratio']:.2f}"),("Profit Factor",f"{bt['profit_factor']:.2f}"),
           ("Expectancy",f"₹{bt['expectancy']:,.0f}"),("Max Consec. Loss",str(bt["max_consec_loss"]))])
    _mrow([("Max Drawdown",f"{bt['max_dd']:.1f}%"),("Max DD Dur.",f"{bt['max_dd_dur']} days"),
           ("Sharpe",f"{bt['sharpe']:.2f}"),("Sortino",f"{bt['sortino']:.2f}")])
    _mrow([("Ann. Return",f"{bt['ann_ret']:+.2f}%"),("Ann. Vol",f"{bt['ann_vol']:.2f}%"),
           ("Total Return",f"{bt['total_ret']:+.2f}%"),("Final Equity",f"₹{bt['final_equity']:,.0f}")])

    # ── Charts ───────────────────────────────────────────────────────────
    st.markdown(_sec("EQUITY CURVE"), unsafe_allow_html=True)
    st.plotly_chart(chart_equity_curve(bt), use_container_width=True)
    st.plotly_chart(chart_drawdown(bt),     use_container_width=True)

    ca, cb = st.columns(2)
    with ca: st.plotly_chart(chart_trade_bars(bt),    use_container_width=True)
    with cb: st.plotly_chart(chart_exit_reasons(bt),  use_container_width=True)
    st.plotly_chart(chart_monthly_pnl(bt), use_container_width=True)

    # ── Trade ledger ─────────────────────────────────────────────────────
    st.markdown(_sec("INDIVIDUAL TRADE LEDGER"), unsafe_allow_html=True)
    tdf = bt["trade_log"].copy()
    tdf["entry_date"] = pd.to_datetime(tdf["entry_date"]).dt.strftime("%Y-%m-%d")
    tdf["exit_date"]  = pd.to_datetime(tdf["exit_date"]).dt.strftime("%Y-%m-%d")
    tdf["net_pnl"]    = tdf["net_pnl"].apply(lambda x: f"₹{x:,.0f}")
    tdf["pnl_pct"]    = tdf["pnl_pct"].apply(lambda x: f"{x:+.2f}%")
    tdf.columns       = [c.replace("_"," ").title() for c in tdf.columns]
    st.dataframe(tdf, use_container_width=True, hide_index=True, height=300)
    st.caption(
        "Strategy enters based on GEX levels + Vanna bias + MA/RSI confirmation. "
        "Exits at GEX flip levels, MA reversal, target, stop, or max hold time. "
        "Educational only · Not SEBI investment advice.")


# ════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def render_quant_analysis():
    """Called by app.py: from quant_analysis import render_quant_analysis"""

    # Get Gemini key if available
    try:    api_key = st.secrets.get("GEMINI_KEY","")
    except: api_key = ""

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;
    padding:12px 18px;margin-bottom:10px;display:flex;
    justify-content:space-between;align-items:center;'>
      <div style='font-family:{MONO};font-size:13px;font-weight:700;
        color:{ACCENT};letter-spacing:2px;'>ARKA · QUANT OPTIONS TERMINAL</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>
        {ts} · NSE INDIA ·
        {"🤖 AI ACTIVE" if api_key else "📐 RULE-BASED MODE"}</div>
    </div>""", unsafe_allow_html=True)

    mod1, mod2 = st.tabs([
        "📊 MODULE 1 · OPTIONS ANALYTICS & SIGNAL",
        "⚙️  MODULE 2 · OPTIONS-STRATEGY BACKTEST",
    ])
    with mod1:
        _render_options_analytics(api_key=api_key)
    with mod2:
        _render_options_backtest()


# Alias for standalone run
render_quant_options_page = render_quant_analysis

if __name__ == "__main__":
    st.set_page_config(
        page_title="ARKA · Quant Analysis",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    render_quant_analysis()
