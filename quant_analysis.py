"""
quant_analysis.py — Arka Trades Institutional Terminal v4
=========================================================
Self-contained. app.py calls: from quant_analysis import render_quant_analysis

MODULE 1 : Advanced Options Analytics & Market Signal
  · 3D IV Surface · 0DTE Smile · Theta Decay · Net GEX · Vanna · Order Flow
  · Per-tab "What This Is Telling Us" insight cards
  · Combined Signal: STRONG BUY / BUY / WEAK BUY / NEUTRAL / WEAK SELL / SELL / STRONG SELL
  · Gemini AI narrative OR pure-Python rule-based fallback

MODULE 2 : Institutional Backtesting Engine (4 Quant Strategies)
  · ROOT CAUSE FIX: Options signal = regime filter ONLY (direction bias)
  · Technical signals = actual entry/exit on real price data
  · Strategy 1 — TREND RIDER: EMA stack + ADX + Volume + MACD
  · Strategy 2 — DONCHIAN CTA: N-day channel breakout (classic CTA/Jane Street)
  · Strategy 3 — MEAN REVERSION PRO: RSI + Bollinger + Vol filter
  · Strategy 4 — GEX-ADAPTIVE: regime-switch (trend in -GEX, mean-revert in +GEX)
  · Risk-based position sizing (% portfolio at risk per trade, not fixed %)
  · ATR dynamic stops + Trailing stop activation
  · Full dashboard: Sharpe, Sortino, Calmar, Max DD, Profit Factor
  · DEPLOY / PAPER TRADE / OPTIMIZE / AVOID verdict

Deps : streamlit numpy pandas scipy plotly yfinance
       optional: google-generativeai
"""

from __future__ import annotations
import warnings, datetime
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import norm
from scipy.optimize import brentq

warnings.filterwarnings("ignore")

# ── Palette ──────────────────────────────────────────────────────────────────
BG,PANEL,PANEL2,BORDER = "#0B0E13","#11151D","#161C26","#222B38"
ACCENT,IVORY,MUTE      = "#C9A227","#E8EDF2","#8593A3"
GREEN,RED,BLUE         = "#1FB97A","#E8554E","#4C8DD6"
PURPLE,ORANGE,AMBER    = "#9B59B6","#E67E22","#F59E0B"
MONO = "'IBM Plex Mono','JetBrains Mono','SF Mono',monospace"
TRADING_DAYS = 252

# ── NSE instrument registry ───────────────────────────────────────────────────
NSE_INDEXES = {
    "NIFTY":      {"spot":24500,"lot":50, "step":50, "iv":0.14,"dte":[0,7,14,28,56,91,180]},
    "BANKNIFTY":  {"spot":52000,"lot":30, "step":100,"iv":0.17,"dte":[0,7,14,28,56,91,180]},
    "FINNIFTY":   {"spot":23000,"lot":40, "step":50, "iv":0.16,"dte":[0,7,14,28,56,91,180]},
    "MIDCPNIFTY": {"spot":12200,"lot":75, "step":25, "iv":0.18,"dte":[0,7,28,56,91]},
    "SENSEX":     {"spot":81500,"lot":20, "step":100,"iv":0.13,"dte":[0,7,28,56,91]},
}
NSE_STOCKS = {
    "RELIANCE":  {"spot":2900, "lot":250, "step":50, "iv":0.28,"dte":[28,56,91]},
    "TCS":       {"spot":3800, "lot":150, "step":50, "iv":0.25,"dte":[28,56,91]},
    "HDFCBANK":  {"spot":1600, "lot":550, "step":20, "iv":0.27,"dte":[28,56,91]},
    "INFY":      {"spot":1750, "lot":300, "step":20, "iv":0.30,"dte":[28,56,91]},
    "ICICIBANK": {"spot":1200, "lot":700, "step":20, "iv":0.30,"dte":[28,56,91]},
    "WIPRO":     {"spot":480,  "lot":1500,"step":10, "iv":0.32,"dte":[28,56,91]},
    "BAJFINANCE":{"spot":7000, "lot":125, "step":100,"iv":0.35,"dte":[28,56,91]},
    "HCLTECH":   {"spot":1600, "lot":350, "step":20, "iv":0.28,"dte":[28,56,91]},
    "AXISBANK":  {"spot":1200, "lot":625, "step":20, "iv":0.32,"dte":[28,56,91]},
    "KOTAKBANK": {"spot":1800, "lot":400, "step":20, "iv":0.30,"dte":[28,56,91]},
    "LT":        {"spot":3700, "lot":175, "step":50, "iv":0.29,"dte":[28,56,91]},
    "TATAMOTORS":{"spot":950,  "lot":1425,"step":10, "iv":0.40,"dte":[28,56,91]},
    "TATASTEEL": {"spot":170,  "lot":2625,"step":5,  "iv":0.42,"dte":[28,56,91]},
    "MARUTI":    {"spot":12500,"lot":100, "step":200,"iv":0.26,"dte":[28,56,91]},
    "SUNPHARMA": {"spot":1700, "lot":700, "step":20, "iv":0.28,"dte":[28,56,91]},
    "HINDUNILVR":{"spot":2400, "lot":300, "step":50, "iv":0.22,"dte":[28,56,91]},
    "SBIN":      {"spot":800,  "lot":1500,"step":10, "iv":0.34,"dte":[28,56,91]},
    "BHARTIARTL":{"spot":1700, "lot":950, "step":20, "iv":0.30,"dte":[28,56,91]},
    "ITC":       {"spot":470,  "lot":3200,"step":10, "iv":0.28,"dte":[28,56,91]},
    "ADANIENT":  {"spot":3100, "lot":625, "step":50, "iv":0.48,"dte":[28,56,91]},
    "ADANIPORTS":{"spot":1400, "lot":1250,"step":20, "iv":0.42,"dte":[28,56,91]},
    "TITAN":     {"spot":3600, "lot":375, "step":50, "iv":0.30,"dte":[28,56,91]},
    "DRREDDY":   {"spot":6500, "lot":125, "step":100,"iv":0.28,"dte":[28,56,91]},
    "CIPLA":     {"spot":1600, "lot":650, "step":20, "iv":0.30,"dte":[28,56,91]},
    "NTPC":      {"spot":380,  "lot":3375,"step":5,  "iv":0.30,"dte":[28,56,91]},
    "ONGC":      {"spot":280,  "lot":1925,"step":5,  "iv":0.35,"dte":[28,56,91]},
    "POWERGRID": {"spot":330,  "lot":3250,"step":5,  "iv":0.28,"dte":[28,56,91]},
    "ULTRACEMCO":{"spot":11000,"lot":100, "step":200,"iv":0.28,"dte":[28,56,91]},
    "JSWSTEEL":  {"spot":950,  "lot":1250,"step":10, "iv":0.38,"dte":[28,56,91]},
    "TECHM":     {"spot":1500, "lot":600, "step":20, "iv":0.32,"dte":[28,56,91]},
    "ASIANPAINT":{"spot":2800, "lot":200, "step":50, "iv":0.27,"dte":[28,56,91]},
    "ZOMATO":    {"spot":240,  "lot":4500,"step":5,  "iv":0.50,"dte":[28,56,91]},
    "NESTLEIND": {"spot":2400, "lot":50,  "step":50, "iv":0.22,"dte":[28,56,91]},
    "DMART":     {"spot":4500, "lot":200, "step":50, "iv":0.26,"dte":[28,56,91]},
    "BAJAJFINSV":{"spot":1800, "lot":500, "step":20, "iv":0.33,"dte":[28,56,91]},
    "IRCTC":     {"spot":850,  "lot":1250,"step":10, "iv":0.40,"dte":[28,56,91]},
    "PIDILITIND":{"spot":3000, "lot":250, "step":50, "iv":0.24,"dte":[28,56,91]},
    "POLYCAB":   {"spot":6000, "lot":150, "step":100,"iv":0.32,"dte":[28,56,91]},
    "TATACONSUM":{"spot":1100, "lot":900, "step":20, "iv":0.30,"dte":[28,56,91]},
}
ALL_INSTRUMENTS = {**NSE_INDEXES, **NSE_STOCKS}
INDEX_NAMES = list(NSE_INDEXES.keys())
STOCK_NAMES = sorted(NSE_STOCKS.keys())

STRATEGY_META = {
    "Trend Rider":         {"icon":"🚀","style":"TREND",       "color":GREEN,  "best":"Nifty, large-cap stocks in bull trend"},
    "Donchian CTA":        {"icon":"📐","style":"BREAKOUT",    "color":BLUE,   "best":"Any liquid NSE stock, index"},
    "Mean Reversion Pro":  {"icon":"🔄","style":"MEAN REVERT", "color":ORANGE, "best":"Range-bound, mid-cap, post-result dips"},
    "GEX-Adaptive Regime": {"icon":"⚡","style":"ADAPTIVE",    "color":ACCENT, "best":"Index, switches mode based on options flow"},
}

VERDICT_CONFIG = {
    "STRONG BUY" :{"color":"#1FB97A","bg":"rgba(31,185,122,0.15)","icon":"🟢"},
    "BUY"        :{"color":"#1FB97A","bg":"rgba(31,185,122,0.10)","icon":"🟩"},
    "WEAK BUY"   :{"color":"#5ed29c","bg":"rgba(94,210,156,0.10)","icon":"🔼"},
    "NEUTRAL"    :{"color":"#8593A3","bg":"rgba(133,147,163,0.10)","icon":"⬜"},
    "WEAK SELL"  :{"color":"#E67E22","bg":"rgba(230,126,34,0.10)","icon":"🔽"},
    "SELL"       :{"color":"#E8554E","bg":"rgba(232,85,78,0.10)","icon":"🟥"},
    "STRONG SELL":{"color":"#E8554E","bg":"rgba(232,85,78,0.15)","icon":"🔴"},
}

# ════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _layout(fig,title,height=420):
    fig.update_layout(template="plotly_dark",
        title=dict(text=title,font=dict(size=12,color=ACCENT)),
        paper_bgcolor=PANEL,plot_bgcolor=PANEL,height=height,
        margin=dict(l=8,r=8,t=40,b=8),
        font=dict(family="monospace",size=11,color=IVORY),
        xaxis=dict(gridcolor=BORDER),yaxis=dict(gridcolor=BORDER))
    return fig

def _sec(label):
    return (f"<div style='font-family:{MONO};font-size:11px;font-weight:600;"
            f"color:{ACCENT};letter-spacing:1.5px;margin:14px 0 6px;"
            f"border-bottom:1px solid {BORDER};padding-bottom:5px;'>{label}</div>")

def _mrow(cells):
    cols=st.columns(len(cells))
    for col,(lbl,val) in zip(cols,cells): col.metric(lbl,val)

def _fmt(v,suffix="",dp=2):
    if v is None or (isinstance(v,float) and not np.isfinite(v)): return "—"
    return f"{v:,.{dp}f}{suffix}"

def _signal_card(title,text,verdict,strength):
    cfg=VERDICT_CONFIG.get(verdict,VERDICT_CONFIG["NEUTRAL"])
    c,bg,ico=cfg["color"],cfg["bg"],cfg["icon"]
    dots="".join([f"<span style='display:inline-block;width:12px;height:6px;"
                  f"border-radius:3px;background:{c};margin-right:3px;'></span>"
                  for _ in range(min(abs(strength),5))])
    st.markdown(f"""
    <div style='background:{bg};border:1px solid {c}44;border-left:4px solid {c};
    border-radius:10px;padding:14px 18px;margin:14px 0;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;'>
        <span style='font-family:{MONO};font-size:11px;font-weight:700;color:{c};
          letter-spacing:1.5px;'>{ico} {title}</span>
        <span style='font-family:{MONO};font-size:13px;font-weight:800;color:{c};'>{verdict}</span>
      </div>
      <div style='font-size:13px;color:{IVORY};line-height:1.75;'>{text}</div>
      <div style='margin-top:10px;'>{dots}
        <span style='font-family:{MONO};font-size:10px;color:{MUTE};margin-left:8px;'>
          Strength {abs(strength)}/5</span>
      </div>
    </div>""",unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════
# MODULE 1 SIGNAL SCORING ENGINE
# ════════════════════════════════════════════════════════════════════════════

def _s2v(score,mx=3):
    r=score/mx if mx>0 else 0
    if r>=0.8:   return "STRONG BUY"
    elif r>=0.4: return "BUY"
    elif r>=0.1: return "WEAK BUY"
    elif r>-0.1: return "NEUTRAL"
    elif r>-0.4: return "WEAK SELL"
    elif r>-0.8: return "SELL"
    else:        return "STRONG SELL"

def _score_gex(gex,levels,spot):
    net=levels.get("total_pos_gex",0)+levels.get("total_neg_gex",0)
    cr=levels.get("call_resistance"); ps=levels.get("put_support")
    score=0; lines=[]
    if net>0:
        score+=2
        lines.append(f"✅ Net GEX <b>+₹{net:.1f}M</b> — MMs net-long gamma. They sell rallies, buy dips. "
                     "Volatility will be suppressed → range-bound or slow upward drift favoured.")
    else:
        score-=2
        lines.append(f"⚠️ Net GEX <b>₹{net:.1f}M</b> (negative) — MMs net-short gamma. They chase moves, "
                     "amplifying volatility. Expect larger swings in either direction.")
    if cr and ps:
        rng=cr-ps; pos=(spot-ps)/rng if rng>0 else 0.5
        lines.append(f"📍 Spot is at <b>{pos*100:.0f}%</b> of GEX range "
                     f"(Support ₹{ps:,.0f} → Resistance ₹{cr:,.0f}).")
        if pos>0.75:   score-=1; lines.append("🔴 Spot approaching <b>call resistance wall</b> — upside capped.")
        elif pos<0.25: score+=1; lines.append("🟢 Spot near <b>put support wall</b> — strong downside cushion.")
        else: lines.append("⬜ Spot mid-range, no immediate GEX wall pressure.")
    return score," ".join(lines),_s2v(score,3)

def _score_vanna(vf,spot):
    above=float(vf[vf["strike"]>spot]["net_vanna"].sum())
    below=float(vf[vf["strike"]<spot]["net_vanna"].sum())
    score=0; lines=[]
    if above>0: score+=1; lines.append(f"🟢 <b>+Vanna above spot ({above:.1f})</b> — IV expansion → dealers BUY → natural tailwind.")
    else:       score-=1; lines.append(f"🔴 <b>−Vanna above spot ({above:.1f})</b> — IV expansion → dealers SELL → headwind on rallies.")
    if below<0: score-=1; lines.append(f"⚠️ <b>−Vanna below spot ({below:.1f})</b> — dealers sell into declines when vol rises → downside amplified.")
    else:       score+=1; lines.append(f"✅ <b>+Vanna below spot ({below:.1f})</b> — dealers buy on drops → cushion against selloffs.")
    dn=float(vf["net_delta_notional"].sum())
    lines.append(f"📊 Net delta notional <b>₹{dn:.0f}M</b> → {'bullish' if dn>0 else 'bearish'} dealer book.")
    return score," ".join(lines),_s2v(score,2)

def _score_theta_iv(chain,spot):
    atm=round(spot/50)*50
    iv0=chain[(chain["type"]=="call")&(chain["dte"]==0)&(chain["strike"]==atm)]["iv"]
    iv28=chain[(chain["type"]=="call")&(chain["dte"]==28)]["iv"]
    iv0v  = float(iv0.values[0])*100  if not iv0.empty  else 18.0
    iv28v = float(iv28.mean())*100    if not iv28.empty else 15.0
    put_iv=chain[(chain["type"]=="put") &(chain["dte"]==0)]["iv"].mean()
    call_iv=chain[(chain["type"]=="call")&(chain["dte"]==0)]["iv"].mean()
    skew=(float(put_iv)-float(call_iv))*100 if not np.isnan(put_iv) else 0
    score=0; lines=[]
    prem=iv0v-iv28v
    if iv0v>20:   score-=1; lines.append(f"⚠️ <b>0DTE IV elevated {iv0v:.1f}%</b> — fear / expected intraday vol. Option sellers earn but risk is high.")
    elif iv0v<14: score+=1; lines.append(f"✅ <b>0DTE IV calm {iv0v:.1f}%</b> — complacency. Market expects quiet session → mildly bullish.")
    else:         lines.append(f"⬜ <b>0DTE IV neutral {iv0v:.1f}%</b> — no extreme, balanced risk/reward.")
    if prem>2.5:  score-=1; lines.append(f"🔴 Near-term fear premium <b>+{prem:.1f}%</b> over 28d IV ({iv28v:.1f}%) — elevated intraday risk.")
    elif prem<0.5:score+=1; lines.append(f"🟢 Low term-structure premium <b>+{prem:.1f}%</b> — calm near-term conditions → supports upside.")
    else:         lines.append(f"📊 Term premium +{prem:.1f}% (28d {iv28v:.1f}%) — within normal range.")
    if skew>1.5:  score-=1; lines.append(f"🔴 Put skew <b>{skew:.2f}%</b> — institutions buying downside protection. Treat as caution signal.")
    elif skew<0.5:score+=1; lines.append(f"🟢 Flat put skew <b>{skew:.2f}%</b> — no heavy put buying → bullish undertone.")
    else:         lines.append(f"📊 Put-call skew {skew:.2f}% — moderate, expected range.")
    iv_regime="ELEVATED" if iv0v>18 else "CALM"
    return score," ".join(lines),_s2v(score,3),iv0v,iv_regime

def _score_flow(ladder):
    call_net=int(ladder["call_flow"].sum()); put_net=int(ladder["put_flow"].sum())
    score=0; lines=[]
    if call_net>0: score+=1; lines.append(f"🟢 <b>Net call buying {call_net:+,d}</b> — directional bullish positioning.")
    else:          score-=1; lines.append(f"🔴 <b>Net call selling {call_net:+,d}</b> — participants expect market to stay below resistance.")
    if put_net<0:  score+=1; lines.append(f"✅ <b>Net put selling {put_net:+,d}</b> — put sellers expect prices to hold → bullish.")
    else:          score-=1; lines.append(f"⚠️ <b>Net put buying {put_net:+,d}</b> — active downside hedging → defensive.")
    atm=ladder[ladder["is_atm"]]
    if not atm.empty:
        ac=int(atm["call_flow"].values[0]); ap=int(atm["put_flow"].values[0])
        lines.append(f"📍 ATM flow: Calls {ac:+,d} / Puts {ap:+,d} → "
                     f"{'call-side dominance = short-term bullish cue' if ac>ap else 'put-side dominance = near-term caution'}.")
    return score," ".join(lines),_s2v(score,2)

def _combined_verdict(gs,vs,ts,fs):
    total=gs+vs+ts+fs
    if total>=6:   v="STRONG BUY"
    elif total>=3: v="BUY"
    elif total>=1: v="WEAK BUY"
    elif total==0: v="NEUTRAL"
    elif total>=-2:v="WEAK SELL"
    elif total>=-5:v="SELL"
    else:          v="STRONG SELL"
    return v,total

# ════════════════════════════════════════════════════════════════════════════
# BIG VERDICT BANNER (Module 1)
# ════════════════════════════════════════════════════════════════════════════

def _big_verdict_banner(verdict,name,total,comp,ai_note=""):
    cfg=VERDICT_CONFIG.get(verdict,VERDICT_CONFIG["NEUTRAL"])
    c,bg,ico=cfg["color"],cfg["bg"],cfg["icon"]
    labels={"GEX":comp.get("gex",0),"Vanna":comp.get("vanna",0),
            "IV/Theta":comp.get("theta",0),"Flow":comp.get("flow",0)}
    bars="".join(f"""
    <div style='display:flex;align-items:center;gap:10px;margin-bottom:4px;'>
      <span style='font-family:{MONO};font-size:10px;color:{MUTE};width:65px;'>{lbl}</span>
      <div style='flex:1;height:5px;background:{BORDER};border-radius:3px;'>
        <div style='width:{min(abs(sc)*20,100)}%;height:5px;
          background:{GREEN if sc>0 else RED if sc<0 else MUTE};border-radius:3px;'></div>
      </div>
      <span style='font-family:{MONO};font-size:10px;
        color:{GREEN if sc>0 else RED if sc<0 else MUTE};width:80px;'>
        {"→ Bullish" if sc>0 else "← Bearish" if sc<0 else "Neutral"}</span>
    </div>""" for lbl,sc in labels.items())
    ai_sec=(f"<div style='margin-top:14px;padding-top:12px;border-top:1px solid {BORDER};"
            f"font-size:13px;color:{IVORY};line-height:1.8;'>"
            f"<span style='font-family:{MONO};font-size:10px;color:{ACCENT};letter-spacing:1px;'>"
            f"🤖 AI ANALYSIS</span><br>{ai_note}</div>") if ai_note else ""
    st.markdown(f"""
    <div style='background:{bg};border:1px solid {c}55;border-left:5px solid {c};
    border-radius:12px;padding:20px 24px;margin:16px 0;'>
      <div style='display:flex;justify-content:space-between;align-items:flex-start;gap:20px;'>
        <div style='flex:1;'>
          <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1.5px;
            margin-bottom:6px;'>COMBINED OPTIONS SIGNAL · {name}</div>
          <div style='font-family:{MONO};font-size:36px;font-weight:800;color:{c};
            line-height:1;'>{ico} {verdict}</div>
          <div style='font-family:{MONO};font-size:12px;color:{IVORY};margin-top:8px;'>
            Composite score: <b style='color:{c};'>{total:+d} / 8</b> &nbsp;·&nbsp;
            {"Multiple bullish signals aligned" if total>0
             else "Multiple bearish signals aligned" if total<0
             else "Mixed signals — no clear directional edge"}
          </div>
        </div>
        <div style='min-width:280px;'>
          <div style='font-family:{MONO};font-size:10px;color:{MUTE};
            letter-spacing:1px;margin-bottom:8px;'>SIGNAL BREAKDOWN</div>
          {bars}
        </div>
      </div>{ai_sec}
    </div>""",unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════
# GEMINI AI (optional)
# ════════════════════════════════════════════════════════════════════════════

def _gemini_note(name,spot,verdict,total,gex_txt,van_txt,th_txt,fl_txt,key):
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        m=genai.GenerativeModel("gemini-2.5-flash",
            system_instruction=("Senior NSE derivatives analyst. Crisp, professional, "
                                "data-driven. Max 150 words. No markdown headers. "
                                "Educational only, not SEBI advice."))
        p=(f"Analyze {name} options. Spot {spot:,.0f}. Signal: {verdict} ({total:+d}/8).\n"
           f"GEX: {gex_txt[:200]}\nVanna: {van_txt[:200]}\nIV: {th_txt[:200]}\nFlow: {fl_txt[:200]}\n"
           f"Write 3 sentences: (1) structure, (2) key risk/opportunity, (3) what to watch.")
        return m.generate_content(p).text.strip()
    except: return ""

def _rule_note(verdict,total,name,levels,spot,gs,vs,ts,fs):
    strongest=max([("GEX",gs),("Vanna",vs),("IV/Theta",ts),("Flow",fs)],key=lambda x:abs(x[1]))
    dir="bullish" if total>0 else "bearish" if total<0 else "neutral"
    cr=levels.get("call_resistance"); ps=levels.get("put_support")
    parts=[
        f"The {name} options market shows a <b>{dir}</b> structure (score {total:+d}/8). "
        f"Strongest signal: <b>{strongest[0]}</b> ({'bullish' if strongest[1]>0 else 'bearish'}).",
    ]
    if cr and ps:
        parts.append(f"GEX key levels: <b>₹{ps:,.0f}</b> (put support) and <b>₹{cr:,.0f}</b> (call resistance).")
    parts.append({
        "STRONG BUY":"Multiple signals strongly aligned long — consider longs on dips toward put support.",
        "BUY":"Bullish bias — entries on pullbacks, exits near call resistance wall.",
        "WEAK BUY":"Mild bullish — wait for stronger confirmation before committing size.",
        "NEUTRAL":"No edge — flat or range trade the GEX pin zone with tight stops.",
        "WEAK SELL":"Mild bearish lean — reduce longs, avoid buying breakouts.",
        "SELL":"Bearish bias — consider shorts near call resistance with stops above it.",
        "STRONG SELL":"Strong bearish — put support likely to be tested.",
    }.get(verdict,"Await clearer signal alignment."))
    parts.append("<br><span style='font-size:11px;color:#8593A3;'>Educational only · Not SEBI investment advice.</span>")
    return " ".join(parts)

# ════════════════════════════════════════════════════════════════════════════
# BLACK-SCHOLES ENGINE
# ════════════════════════════════════════════════════════════════════════════

def bs_price(S,K,T,r,sigma,t="call"):
    if T<=1e-6 or sigma<=1e-6: return max(S-K,0.) if t=="call" else max(K-S,0.)
    d1=(np.log(S/K)+(r+.5*sigma**2)*T)/(sigma*np.sqrt(T)); d2=d1-sigma*np.sqrt(T)
    return S*norm.cdf(d1)-K*np.exp(-r*T)*norm.cdf(d2) if t=="call" else K*np.exp(-r*T)*norm.cdf(-d2)-S*norm.cdf(-d1)

def bs_greeks(S,K,T,r,sigma,t="call"):
    if T<=1e-6 or sigma<=1e-6: return dict(delta=0.,gamma=0.,theta=0.,vega=0.,vanna=0.)
    d1=(np.log(S/K)+(r+.5*sigma**2)*T)/(sigma*np.sqrt(T)); d2=d1-sigma*np.sqrt(T)
    nd1=norm.pdf(d1)
    gamma=nd1/(S*sigma*np.sqrt(T)); vega=S*nd1*np.sqrt(T)/100.; vanna=-nd1*d2/sigma
    if t=="call":
        delta=norm.cdf(d1); theta=(-S*nd1*sigma/(2.*np.sqrt(T))-r*K*np.exp(-r*T)*norm.cdf(d2))/TRADING_DAYS
    else:
        delta=norm.cdf(d1)-1.; theta=(-S*nd1*sigma/(2.*np.sqrt(T))+r*K*np.exp(-r*T)*norm.cdf(-d2))/TRADING_DAYS
    return dict(delta=delta,gamma=gamma,theta=theta,vega=vega,vanna=vanna)

# ════════════════════════════════════════════════════════════════════════════
# MOCK OPTIONS CHAIN
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300,show_spinner=False)
def generate_mock_chain(spot=24500.,r=0.065,name="NIFTY",base_iv=0.14,step=50,dte_tuple=(0,7,14,28,56,91,180)):
    rng=np.random.default_rng(hash(name)%2**31)
    today=datetime.date.today()
    atm=round(spot/step)*step; n=10
    strikes=np.arange(atm-n*step,atm+(n+1)*step,step)
    rows=[]
    for dte in dte_tuple:
        T=max(dte,.5)/TRADING_DAYS
        for K in strikes:
            m=np.log(K/spot)
            iv=float(np.clip(base_iv+.02*abs(m)-.08*m+.12*m**2+.03*np.exp(-dte/30)+rng.normal(0,.003),.05,.90))
            for ot in ["call","put"]:
                p=bs_price(spot,K,T,r,iv,ot); g=bs_greeks(spot,K,T,r,iv,ot)
                oi=max(int(rng.lognormal(8-4*abs(m),.4)*10),100); vol=int(oi*rng.uniform(.1,.6))
                ba=p*rng.uniform(.01,.04)
                rows.append({"dte":dte,"strike":float(K),"type":ot,"spot":spot,"iv":round(iv,4),
                    "price":round(p,2),"bid":round(p-ba/2,2),"ask":round(p+ba/2,2),
                    "oi":oi,"volume":vol,"delta":round(g["delta"],4),"gamma":round(g["gamma"],6),
                    "theta":round(g["theta"],4),"vega":round(g["vega"],4),"vanna":round(g["vanna"],6),
                    "moneyness":round(m,4)})
    df=pd.DataFrame(rows)
    rng2=np.random.default_rng(hash(name+"f")%2**31)
    df["net_flow"]=(df["volume"]*rng2.choice([-1,1],len(df),p=[.45,.55])*rng2.uniform(.2,1.,len(df))).astype(int)
    return df

# ════════════════════════════════════════════════════════════════════════════
# OPTIONS ANALYTICS
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300,show_spinner=False)
def build_iv_surface(chain,max_s=20,max_e=12):
    calls=chain[chain["type"]=="call"].copy()
    ss=sorted(calls["strike"].unique()); step=max(1,len(ss)//max_s)
    sel_s=ss[::step][:max_s]; sel_e=sorted(calls["dte"].unique())[:max_e]
    ivc=np.full((len(sel_e),len(sel_s)),np.nan); ivp=np.full((len(sel_e),len(sel_s)),np.nan)
    for i,dte in enumerate(sel_e):
        sc=calls[calls["dte"]==dte]; sp=chain[(chain["type"]=="put")&(chain["dte"]==dte)]
        for j,K in enumerate(sel_s):
            r=sc[sc["strike"]==K]; rp=sp[sp["strike"]==K]
            if not r.empty:  ivc[i,j]=r.iloc[0]["iv"]
            if not rp.empty: ivp[i,j]=rp.iloc[0]["iv"]
    return np.array(sel_s),np.array(sel_e),ivc,ivp

@st.cache_data(ttl=300,show_spinner=False)
def build_smile(chain,dte_val=0):
    av=sorted(chain["dte"].unique()); dv=dte_val if dte_val in av else av[0]
    df=chain[chain["dte"]==dv].copy(); df["iv_pct"]=df["iv"]*100
    return df.sort_values("strike"),dv

@st.cache_data(ttl=300,show_spinner=False)
def compute_gex(chain,lot=50):
    sub=chain[chain["dte"]==chain["dte"].min()].copy()
    sub["gu"]=sub["gamma"]*sub["oi"]*lot*sub["spot"]
    c=sub[sub["type"]=="call"][["strike","gu"]].rename(columns={"gu":"cg"})
    p=sub[sub["type"]=="put"][["strike","gu"]].rename(columns={"gu":"pg"})
    g=pd.merge(c,p,on="strike",how="outer").fillna(0)
    g["net_gex"]=(g["cg"]-g["pg"])/1e6; g["cg"]/=1e6; g["pg"]=-g["pg"]/1e6
    return g.sort_values("strike").reset_index(drop=True)

def find_gex_levels(gex,spot):
    ab=gex[gex["strike"]>spot]; bw=gex[gex["strike"]<spot]
    cw=ab.loc[ab["net_gex"].idxmax(),"strike"] if not ab.empty and ab["net_gex"].max()>0 else None
    pw=bw.loc[bw["net_gex"].idxmin(),"strike"] if not bw.empty and bw["net_gex"].min()<0 else None
    pos=gex[gex["net_gex"]>0]; neg=gex[gex["net_gex"]<0]
    return {"call_resistance":cw,"put_support":pw,
            "total_pos_gex":float(pos["net_gex"].sum()),
            "total_neg_gex":float(neg["net_gex"].sum())}

@st.cache_data(ttl=300,show_spinner=False)
def compute_vanna(chain,lot=50):
    sub=chain[chain["dte"]==chain["dte"].min()].copy()
    sub["dn"]=sub["delta"]*sub["oi"]*lot*sub["spot"]/1e6
    sub["ve"]=sub["vanna"]*sub["oi"]*lot/1e3
    c=sub[sub["type"]=="call"][["strike","dn","ve"]].rename(columns={"dn":"cdn","ve":"cv"})
    p=sub[sub["type"]=="put"][["strike","dn","ve"]].rename(columns={"dn":"pdn","ve":"pv"})
    vf=pd.merge(c,p,on="strike",how="outer").fillna(0)
    vf["net_delta_notional"]=vf["cdn"]+vf["pdn"]; vf["net_vanna"]=vf["cv"]+vf["pv"]
    return vf.sort_values("strike").reset_index(drop=True)

@st.cache_data(ttl=600,show_spinner=False)
def theta_curves(spot=24500,r=0.065,sigma=0.15,horizons=None):
    if horizons is None: horizons=[90,60,30,7]
    rows=[]
    for s in horizons:
        for dte in range(s,0,-1):
            T=dte/TRADING_DAYS; g=bs_greeks(spot,spot,T,r,sigma,"call")
            rows.append({"start_dte":s,"dte":dte,"theta":g["theta"],"theta_pct":abs(g["theta"])/spot*100})
    return pd.DataFrame(rows)

def flow_ladder(chain,spot,n=12,step=50):
    sub=chain[chain["dte"]==chain["dte"].min()].copy()
    atm=round(spot/step)*step
    sel=sorted(sub["strike"].unique(),key=lambda k:abs(k-atm))[:n]
    rows=[]
    for K in sorted(sel):
        cr=sub[(sub["strike"]==K)&(sub["type"]=="call")]
        pr=sub[(sub["strike"]==K)&(sub["type"]=="put")]
        rows.append({"strike":K,"is_atm":abs(K-atm)<=step/2,
            "call_flow":int(cr["net_flow"].values[0]) if not cr.empty else 0,
            "put_flow": int(pr["net_flow"].values[0]) if not pr.empty else 0,
            "call_oi":  int(cr["oi"].values[0])       if not cr.empty else 0,
            "put_oi":   int(pr["oi"].values[0])        if not pr.empty else 0,
            "call_iv":  round(float(cr["iv"].values[0])*100,1) if not cr.empty else 0,
            "put_iv":   round(float(pr["iv"].values[0])*100,1) if not pr.empty else 0})
    return pd.DataFrame(rows)

# ════════════════════════════════════════════════════════════════════════════
# MODULE 1 CHARTS
# ════════════════════════════════════════════════════════════════════════════

def ch_surface(strikes,dte,ivc,ivp):
    fig=go.Figure()
    fig.add_trace(go.Surface(x=strikes,y=dte,z=ivc*100,
        colorscale=[[0,"#1a2a4a"],[.5,"#4C8DD6"],[1,"#C9A227"]],
        name="Call IV",opacity=.85,showscale=True,
        colorbar=dict(x=1.02,title=dict(text="IV%",font=dict(color=ACCENT,size=10)))))
    fig.add_trace(go.Surface(x=strikes,y=dte,z=ivp*100,
        colorscale=[[0,"#1a2a3a"],[.5,"#E8554E"],[1,"#ff9999"]],
        name="Put IV",opacity=.55,showscale=False))
    fig.update_layout(template="plotly_dark",paper_bgcolor=PANEL,height=500,
        title=dict(text="3D IV SURFACE · Calls (gold) / Puts (red)",font=dict(size=12,color=ACCENT)),
        scene=dict(xaxis=dict(title="Strike",gridcolor=BORDER,color=IVORY),
                   yaxis=dict(title="DTE",gridcolor=BORDER,color=IVORY),
                   zaxis=dict(title="IV%",gridcolor=BORDER,color=IVORY),
                   bgcolor=BG,camera=dict(eye=dict(x=1.5,y=-1.8,z=0.8))),
        font=dict(family="monospace",size=10,color=IVORY),margin=dict(l=0,r=0,t=50,b=0))
    return fig

def ch_smile(df,spot,dte_val):
    calls=df[df["type"]=="call"]; puts=df[df["type"]=="put"]
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=calls["strike"],y=calls["iv_pct"],mode="lines+markers",
        name="Call IV",line=dict(color=GREEN,width=2),marker=dict(size=5)))
    fig.add_trace(go.Scatter(x=puts["strike"],y=puts["iv_pct"],mode="lines+markers",
        name="Put IV",line=dict(color=RED,width=2),marker=dict(size=5)))
    fig.add_vline(x=spot,line_color=ACCENT,line_dash="dash",line_width=1.5,
                  annotation_text=f"Spot {spot:,.0f}",annotation_font_color=ACCENT)
    fig.update_layout(showlegend=True,legend=dict(orientation="h",y=1.05,x=0))
    label="0DTE" if dte_val==0 else f"{dte_val}DTE"
    return _layout(fig,f"{label} IV SMILE / SMIRK",340)

def ch_theta(tdf):
    fig=go.Figure(); cols=[ACCENT,BLUE,GREEN,PURPLE]
    for i,(s,grp) in enumerate(tdf.groupby("start_dte")):
        fig.add_trace(go.Scatter(x=grp["dte"],y=grp["theta_pct"],mode="lines",
            name=f"{int(s)}DTE",line=dict(color=cols[i%4],width=2)))
    fig.update_xaxes(autorange="reversed")
    fig.update_layout(showlegend=True,legend=dict(orientation="h",y=1.05,x=0))
    return _layout(fig,"NON-LINEAR THETA DECAY · ATM % spot/day",340)

def ch_gex(gex,spot,levels):
    colors=[GREEN if v>=0 else RED for v in gex["net_gex"]]
    fig=go.Figure()
    fig.add_trace(go.Bar(y=gex["strike"].astype(str),x=gex["net_gex"],orientation="h",
        marker_color=colors,opacity=.85,text=[f"{v:+.1f}M" for v in gex["net_gex"]],
        textposition="outside",textfont=dict(size=9,color=IVORY)))
    all_s=[str(int(k)) for k in sorted(gex["strike"].unique())]
    def si(k): s=str(int(k)); return all_s.index(s) if s in all_s else None
    if levels.get("call_resistance"):
        idx=si(levels["call_resistance"])
        if idx is not None:
            fig.add_shape(type="line",y0=idx-.4,y1=idx+.4,x0=-50,x1=50,
                xref="x",yref="y",line=dict(color=RED,dash="dot",width=1.5))
            fig.add_annotation(x=50,y=idx,text="Call Resistance",showarrow=False,
                font=dict(color=RED,size=9),xanchor="right")
    if levels.get("put_support"):
        idx=si(levels["put_support"])
        if idx is not None:
            fig.add_shape(type="line",y0=idx-.4,y1=idx+.4,x0=-50,x1=50,
                xref="x",yref="y",line=dict(color=GREEN,dash="dot",width=1.5))
            fig.add_annotation(x=50,y=idx,text="Put Support",showarrow=False,
                font=dict(color=GREEN,size=9),xanchor="right")
    si_=si(round(spot/50)*50)
    if si_ is not None:
        fig.add_shape(type="line",y0=si_-.5,y1=si_+.5,x0=-200,x1=200,
            xref="x",yref="y",line=dict(color=ACCENT,width=2))
    fig.add_vline(x=0,line_color=MUTE,line_width=1)
    fig.update_layout(showlegend=False)
    return _layout(fig,"NET GAMMA EXPOSURE · ₹M · +Green=Support / −Red=Resistance",460)

def ch_vanna(vf,spot):
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,
        subplot_titles=["Net Delta Notional (₹M)","Net Vanna Exposure"],vertical_spacing=.08)
    dc=[GREEN if v>=0 else RED for v in vf["net_delta_notional"]]
    vc=[PURPLE if v>=0 else ORANGE for v in vf["net_vanna"]]
    fig.add_trace(go.Bar(x=vf["strike"],y=vf["net_delta_notional"],marker_color=dc,opacity=.8),row=1,col=1)
    fig.add_trace(go.Bar(x=vf["strike"],y=vf["net_vanna"],marker_color=vc,opacity=.8),row=2,col=1)
    for r in[1,2]: fig.add_vline(x=spot,line_color=ACCENT,line_dash="dash",line_width=1.5,row=r,col=1)
    fig.update_layout(template="plotly_dark",paper_bgcolor=PANEL,plot_bgcolor=PANEL,
        height=440,showlegend=False,margin=dict(l=8,r=8,t=50,b=8),
        font=dict(family="monospace",size=11,color=IVORY),
        xaxis=dict(gridcolor=BORDER),yaxis=dict(gridcolor=BORDER),
        xaxis2=dict(gridcolor=BORDER),yaxis2=dict(gridcolor=BORDER),
        title=dict(text="VANNA FLOW · Dealer Rehedge on Vol Changes",font=dict(size=12,color=ACCENT)))
    return fig

def ch_flow(ladder,spot):
    fig=make_subplots(rows=1,cols=2,subplot_titles=["← PUT FLOW","CALL FLOW →"],
        shared_yaxes=True,horizontal_spacing=.02)
    for _,row in ladder.iterrows():
        K=row["strike"]; c=ACCENT if row["is_atm"] else(GREEN if row["call_flow"]>0 else RED)
        fig.add_trace(go.Bar(y=[K],x=[row["call_flow"]],orientation="h",marker_color=c,
            opacity=.85,showlegend=False,text=[f"{row['call_flow']:+,d}"],
            textposition="outside",textfont=dict(size=9)),row=1,col=2)
    for _,row in ladder.iterrows():
        K=row["strike"]; c=ACCENT if row["is_atm"] else(GREEN if row["put_flow"]>0 else RED)
        fig.add_trace(go.Bar(y=[K],x=[-row["put_flow"]],orientation="h",marker_color=c,
            opacity=.85,showlegend=False,text=[f"{row['put_flow']:+,d}"],
            textposition="outside",textfont=dict(size=9)),row=1,col=1)
    fig.update_layout(template="plotly_dark",paper_bgcolor=PANEL,plot_bgcolor=PANEL,
        height=460,barmode="overlay",margin=dict(l=8,r=8,t=50,b=8),
        font=dict(family="monospace",size=11,color=IVORY),yaxis=dict(gridcolor=BORDER),
        title=dict(text="ORDER FLOW LADDER · Net Contracts Bought vs Sold",font=dict(size=12,color=ACCENT)))
    return fig

# ════════════════════════════════════════════════════════════════════════════
# MODULE 2 — INSTITUTIONAL BACKTEST ENGINE
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600,show_spinner=False)
def fetch_ohlcv(symbol,start,end):
    try:
        import yfinance as yf
        sym=symbol.strip().upper()
        if not sym.endswith(".NS") and "^" not in sym: sym+=".NS"
        df=yf.download(sym,start=start,end=end,progress=False,auto_adjust=True)
        if df.empty: return pd.DataFrame()
        if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        df=df[["Open","High","Low","Close","Volume"]].dropna()
        df.index=pd.to_datetime(df.index)
        return df
    except Exception as e:
        st.warning(f"Fetch error: {e}"); return pd.DataFrame()


def _indicators(df):
    """Full institutional indicator suite — ADX, ATR, Donchian, OBV, Momentum, Bollinger."""
    c=df["Close"].copy(); h=df["High"].copy(); l=df["Low"].copy()
    v=df["Volume"].copy() if "Volume" in df.columns else pd.Series(1,index=df.index)
    df=df.copy()

    # EMAs / SMAs
    for p in [5,10,20,50,100,200]:
        df[f"EMA{p}"]=c.ewm(span=p,adjust=False).mean()
        df[f"SMA{p}"]=c.rolling(p).mean()

    # ATR (True Range)
    tr=pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    df["ATR14"]=tr.ewm(span=14,adjust=False).mean()
    df["ATR20"]=tr.rolling(20).mean()
    df["ATR_ratio"]=df["ATR14"]/df["ATR20"].replace(0,np.nan)  # >1 = expanding vol

    # ADX
    up=(h-h.shift(1)).clip(lower=0); dn=(l.shift(1)-l).clip(lower=0)
    dmp=np.where(up>dn,up,0.); dmm=np.where(dn>up,dn,0.)
    dmp_s=pd.Series(dmp,index=df.index).ewm(span=14,adjust=False).mean()
    dmm_s=pd.Series(dmm,index=df.index).ewm(span=14,adjust=False).mean()
    atr14=df["ATR14"].replace(0,np.nan)
    dip=100*dmp_s/atr14; dim=100*dmm_s/atr14
    dx=100*(dip-dim).abs()/(dip+dim).replace(0,np.nan)
    df["ADX"]=dx.ewm(span=14,adjust=False).mean()
    df["DI_Plus"]=dip; df["DI_Minus"]=dim

    # RSI
    delta=c.diff(); gain=delta.clip(lower=0).ewm(span=14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(span=14,adjust=False).mean()
    df["RSI"]=100-(100/(1+gain/loss.replace(0,np.nan)))

    # MACD
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean()
    df["MACD"]=e12-e26; df["MACD_sig"]=df["MACD"].ewm(span=9,adjust=False).mean()
    df["MACD_hist"]=df["MACD"]-df["MACD_sig"]

    # Bollinger Bands
    mid=c.rolling(20).mean(); std=c.rolling(20).std()
    df["BB_upper"]=mid+2*std; df["BB_lower"]=mid-2*std; df["BB_mid"]=mid
    df["BB_pct"]=(c-df["BB_lower"])/(df["BB_upper"]-df["BB_lower"]).replace(0,np.nan)
    df["BB_width"]=(df["BB_upper"]-df["BB_lower"])/mid.replace(0,np.nan)

    # Donchian Channels (shifted 1 to avoid lookahead)
    for n in [10,20,55]:
        df[f"DC_H{n}"]=h.rolling(n).max().shift(1)
        df[f"DC_L{n}"]=l.rolling(n).min().shift(1)

    # Volume
    df["Vol_MA20"]=v.rolling(20).mean()
    df["Vol_ratio"]=v/df["Vol_MA20"].replace(0,np.nan)
    df["OBV"]=(np.sign(c.diff())*v).fillna(0).cumsum()
    df["OBV_EMA"]=df["OBV"].ewm(span=20,adjust=False).mean()

    # Momentum / Rate-of-Change
    for n in [5,10,21,63]: df[f"ROC{n}"]=c.pct_change(n)*100

    # Historical Volatility
    lr=np.log(c/c.shift(1))
    df["HV20"]=lr.rolling(20).std()*np.sqrt(TRADING_DAYS)*100
    df["HV60"]=lr.rolling(60).std()*np.sqrt(TRADING_DAYS)*100
    df["HV_ratio"]=df["HV20"]/df["HV60"].replace(0,np.nan)

    # Stochastic %K
    low14=l.rolling(14).min(); high14=h.rolling(14).max()
    df["STOCH_K"]=100*(c-low14)/(high14-low14).replace(0,np.nan)
    df["STOCH_D"]=df["STOCH_K"].rolling(3).mean()

    return df


def _risk_size(equity,risk_pct,atr14,price,max_pos_pct=0.25):
    """Risk-based position sizing: risk exactly risk_pct% of equity per trade."""
    risk_amt=equity*risk_pct
    if atr14<=0 or price<=0: return 0.
    shares=risk_amt/atr14
    max_shares=equity*max_pos_pct/price
    return min(shares,max_shares)


@st.cache_data(ttl=300,show_spinner=False)
def run_backtest(
    df_raw, strategy="Trend Rider", direction="both",
    initial_cap=1_000_000., risk_per_trade=0.01,
    atr_stop=2.0, atr_target=4.0, atr_trail=2.5,
    max_hold=30, commission=0.0015, slippage=0.0005,
    options_verdict="NEUTRAL", gex_regime="POSITIVE",
    long_only_from_signal=True,
):
    """
    Institutional 4-strategy backtester.
    OPTIONS SIGNAL = regime/direction FILTER only. NOT used as price target.
    Technical signals generate actual entry/exit.
    """
    if df_raw.empty or len(df_raw)<120:
        return {"error":"Need 120+ bars. Try a wider date range (e.g. 3+ years)."}

    df=_indicators(df_raw.copy())
    req=["ATR14","ADX","RSI","EMA20","EMA50","EMA200","BB_pct","DC_H20","DC_L20","MACD"]
    df=df.dropna(subset=req)
    if len(df)<60:
        return {"error":"Too few bars after indicators. Use 3+ years of data."}

    # Direction bias from options signal
    opt_long  = options_verdict in ("STRONG BUY","BUY","WEAK BUY","NEUTRAL")
    opt_short = options_verdict in ("STRONG SELL","SELL","WEAK SELL","NEUTRAL")
    if long_only_from_signal:
        if options_verdict in ("STRONG BUY","BUY"): opt_short=False
        if options_verdict in ("STRONG SELL","SELL"): opt_long=False

    # GEX regime determines strategy sub-mode for GEX-Adaptive
    gex_pos = gex_regime=="POSITIVE"

    equity=initial_cap; trades=[]; eq_curve=[]; position=None; hold=0

    for i,(date,row) in enumerate(df.iterrows()):
        eq_curve.append({"date":date,"equity":equity})
        if position is None:
            atr=float(row["ATR14"]); price=float(row["Close"])
            if atr<=0 or price<=0: continue
            sig=0  # 0=flat, 1=long, -1=short

            # ── STRATEGY SIGNALS ────────────────────────────────────────
            if strategy=="Trend Rider":
                # Long: EMA stack + ADX + RSI not overbought + MACD cross + volume
                ema_bull=(float(row["EMA20"])>float(row["EMA50"]) and
                          float(row["EMA50"])>float(row["EMA200"]) and
                          price>float(row["EMA200"]))
                ema_bear=(float(row["EMA20"])<float(row["EMA50"]) and
                          float(row["EMA50"])<float(row["EMA200"]) and
                          price<float(row["EMA200"]))
                adx_ok=float(row["ADX"])>22
                vol_ok=float(row["Vol_ratio"])>0.7
                macd_bull=float(row["MACD_hist"])>0 and float(row.get("MACD_hist",0))>float(df["MACD_hist"].shift(1).iloc[i] if i>0 else 0)
                macd_bear=float(row["MACD_hist"])<0
                rsi_long=40<float(row["RSI"])<72
                rsi_short=28<float(row["RSI"])<60
                # Pullback entry: EMA stack aligned, price pulling to EMA20
                near_ema20_long = price<float(row["EMA20"])*1.015
                near_ema20_short= price>float(row["EMA20"])*0.985
                if ema_bull and adx_ok and rsi_long and macd_bull and vol_ok: sig=1
                if ema_bear and adx_ok and rsi_short and macd_bear and vol_ok: sig=-1

            elif strategy=="Donchian CTA":
                # Breakout of 20-day channel with volume + ADX trend filter
                dc20h=float(row["DC_H20"]); dc20l=float(row["DC_L20"])
                adx_ok=float(row["ADX"])>15
                vol_ok=float(row["Vol_ratio"])>1.15
                if price>dc20h and adx_ok and vol_ok and float(row["DI_Plus"])>float(row["DI_Minus"]): sig=1
                if price<dc20l and adx_ok and vol_ok and float(row["DI_Minus"])>float(row["DI_Plus"]): sig=-1

            elif strategy=="Mean Reversion Pro":
                # Oversold/overbought with BB + RSI + trend filter (trade WITH the trend)
                in_uptrend =price>float(row["EMA100"])
                in_downtrend=price<float(row["EMA100"])
                bb_pct=float(row["BB_pct"]) if np.isfinite(float(row["BB_pct"])) else 0.5
                rsi=float(row["RSI"])
                # Long: deeply oversold in an uptrend
                if rsi<33 and bb_pct<0.15 and in_uptrend: sig=1
                # Short: deeply overbought in a downtrend
                if rsi>67 and bb_pct>0.85 and in_downtrend: sig=-1

            elif strategy=="GEX-Adaptive Regime":
                # +GEX regime → mean reversion (market makers pin price)
                # -GEX regime → trend following (market makers amplify moves)
                if gex_pos:
                    # Mean reversion mode
                    in_uptrend=price>float(row["EMA100"])
                    in_downtrend=price<float(row["EMA100"])
                    bb_pct=float(row["BB_pct"]) if np.isfinite(float(row["BB_pct"])) else 0.5
                    rsi=float(row["RSI"])
                    if rsi<35 and bb_pct<0.18 and in_uptrend: sig=1
                    if rsi>65 and bb_pct>0.82 and in_downtrend: sig=-1
                else:
                    # Trend following mode
                    ema_bull=(float(row["EMA20"])>float(row["EMA50"]) and price>float(row["EMA200"]))
                    ema_bear=(float(row["EMA20"])<float(row["EMA50"]) and price<float(row["EMA200"]))
                    adx_ok=float(row["ADX"])>20
                    if ema_bull and adx_ok and float(row["MACD_hist"])>0: sig=1
                    if ema_bear and adx_ok and float(row["MACD_hist"])<0: sig=-1

            # Apply options direction filter
            if sig==1 and not opt_long: sig=0
            if sig==-1 and not opt_short: sig=0
            if direction=="long"  and sig==-1: sig=0
            if direction=="short" and sig==1:  sig=0

            if sig!=0:
                ep=price*(1+sig*slippage)
                shares=_risk_size(equity,risk_per_trade,atr,ep)
                if shares<=0: continue
                stop=ep-sig*atr_stop*atr
                target=ep+sig*atr_target*atr
                position={"side":sig,"ep":ep,"shares":shares,"stop":stop,
                          "target":target,"trail_stop":stop,"trail_active":False,
                          "cost":shares*ep*commission,"atr_entry":atr,
                          "entry_date":date,"strategy":strategy}
                hold=0

        else:
            hold+=1; side=position["side"]; ep=position["ep"]
            price=float(row["Close"]); atr=float(row["ATR14"])

            # Update trailing stop
            if side==1:
                new_trail=price-atr_trail*atr
                if not position["trail_active"] and price>=ep+1.5*position["atr_entry"]:
                    position["trail_active"]=True
                if position["trail_active"]:
                    position["trail_stop"]=max(position["trail_stop"],new_trail)
            else:
                new_trail=price+atr_trail*atr
                if not position["trail_active"] and price<=ep-1.5*position["atr_entry"]:
                    position["trail_active"]=True
                if position["trail_active"]:
                    position["trail_stop"]=min(position["trail_stop"],new_trail)

            # Exit conditions
            at_hard_stop   = (side==1 and price<=position["stop"]) or (side==-1 and price>=position["stop"])
            at_trail_stop  = position["trail_active"] and ((side==1 and price<=position["trail_stop"]) or (side==-1 and price>=position["trail_stop"]))
            at_target      = (side==1 and price>=position["target"]) or (side==-1 and price<=position["target"])
            at_time        = hold>=max_hold
            # Trend reversal exit for trend strategies
            at_reversal    = False
            if strategy in ("Trend Rider","GEX-Adaptive Regime") and not gex_pos:
                ema20=float(row["EMA20"]); ema50=float(row["EMA50"])
                at_reversal=(side==1 and ema20<ema50) or (side==-1 and ema20>ema50)
            # Mean reversion profit exit
            at_mr_exit=False
            if strategy in ("Mean Reversion Pro","GEX-Adaptive Regime") and gex_pos:
                bb_mid=float(row["BB_mid"])
                at_mr_exit=(side==1 and price>=bb_mid*0.99) or (side==-1 and price<=bb_mid*1.01)

            reason="Holding"
            if at_hard_stop:   reason="Hard Stop"
            elif at_trail_stop:reason="Trailing Stop"
            elif at_target:    reason="Target Hit"
            elif at_time:      reason="Time Stop"
            elif at_reversal:  reason="Trend Reversal"
            elif at_mr_exit:   reason="Mean Exit"

            if reason!="Holding" or i==len(df)-1:
                xp=price*(1-side*slippage)
                cost_x=position["shares"]*xp*commission
                pnl=position["shares"]*(xp-ep)*side-(position["cost"]+cost_x)
                equity+=pnl
                pnl_pct=side*(xp/ep-1)*100
                trades.append({"entry_date":position["entry_date"],"exit_date":date,
                    "side":"LONG" if side==1 else "SHORT",
                    "entry":round(ep,2),"exit":round(xp,2),"shares":round(position["shares"],2),
                    "pnl":round(pnl,2),"pnl_pct":round(pnl_pct,3),"hold_days":hold,
                    "exit_reason":reason,"outcome":"WIN" if pnl>0 else "LOSS"})
                position=None; hold=0

    if not trades:
        return {"error":"No trades generated. Try 'Both Sides', wider dates, or a different strategy."}

    eq=pd.DataFrame(eq_curve).set_index("date")["equity"]
    tdf=pd.DataFrame(trades)
    rets=eq.pct_change().dropna()
    wins=tdf[tdf["outcome"]=="WIN"]; losses=tdf[tdf["outcome"]=="LOSS"]
    wr=len(wins)/len(tdf)*100
    avg_w=wins["pnl_pct"].mean() if len(wins) else 0.
    avg_l=losses["pnl_pct"].mean() if len(losses) else 0.
    pf=abs(wins["pnl"].sum()/losses["pnl"].sum()) if len(losses) and losses["pnl"].sum()!=0 else 0.
    rr=abs(avg_w/avg_l) if avg_l!=0 else 0.
    peak=eq.cummax(); dd=(eq-peak)/peak*100; max_dd=float(dd.min())
    dd_dur=cur_d=0
    for dv in dd.values:
        if dv<0: cur_d+=1; dd_dur=max(dd_dur,cur_d)
        else: cur_d=0
    ann_r=float(rets.mean()*TRADING_DAYS)
    ann_v=float(rets.std()*np.sqrt(TRADING_DAYS))
    dv=rets[rets<0].std()*np.sqrt(TRADING_DAYS)
    sharpe=ann_r/ann_v if ann_v>0 else 0.
    sortino=ann_r/dv if dv>0 else 0.
    calmar=ann_r/abs(max_dd/100) if max_dd!=0 else 0.
    tot_r=(equity-initial_cap)/initial_cap*100
    max_cl=cl=0
    for o in tdf["outcome"].values:
        if o=="LOSS": cl+=1; max_cl=max(max_cl,cl)
        else: cl=0
    monthly=tdf.copy()
    monthly["month"]=pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    monthly_pnl=monthly.groupby("month")["pnl"].sum()

    # Strategy verdict
    if pf>=1.5 and wr>=48 and sharpe>=0.5 and tot_r>0:
        sv="DEPLOY ✅"; sc=GREEN
    elif pf>=1.2 and wr>=43 and sharpe>=0.25 and tot_r>0:
        sv="PAPER TRADE 🟡"; sc=AMBER
    elif pf>=1.0 and tot_r>0:
        sv="OPTIMIZE ⚠️"; sc=ORANGE
    else:
        sv="AVOID ❌"; sc=RED

    return {"equity_curve":pd.DataFrame({"equity":eq}),
            "trade_log":tdf,"monthly_pnl":monthly_pnl,
            "drawdown":dd,"total_trades":len(tdf),"win_rate":round(wr,1),
            "avg_win":round(avg_w,2),"avg_loss":round(avg_l,2),
            "rr":round(rr,2),"profit_factor":round(pf,2),
            "max_dd":round(max_dd,2),"dd_dur":dd_dur,
            "sharpe":round(sharpe,3),"sortino":round(sortino,3),"calmar":round(calmar,3),
            "ann_ret":round(ann_r*100,2),"ann_vol":round(ann_v*100,2),
            "total_ret":round(tot_r,2),"final_equity":round(equity,2),
            "max_cl":max_cl,"expectancy":round(tdf["pnl"].mean(),2),
            "strat_verdict":sv,"verdict_color":sc,
            "exit_reasons":tdf["exit_reason"].value_counts().to_dict()}

# ════════════════════════════════════════════════════════════════════════════
# MODULE 2 CHARTS
# ════════════════════════════════════════════════════════════════════════════

def ch_equity(bt):
    eq=bt["equity_curve"]["equity"]
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=eq.index,y=eq.values,line=dict(color=ACCENT,width=2),
        fill="tozeroy",fillcolor="rgba(201,162,39,0.07)"))
    fig.add_hline(y=eq.iloc[0],line_color=MUTE,line_dash="dash",line_width=1,
                  annotation_text="Initial Capital",annotation_font_color=MUTE)
    return _layout(fig,"EQUITY CURVE (₹)",310)

def ch_dd(bt):
    dd=bt["drawdown"]
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=dd.index,y=dd.values,line=dict(color=RED,width=1.5),
        fill="tozeroy",fillcolor="rgba(232,85,78,0.15)"))
    return _layout(fig,"STRATEGY DRAWDOWN (%)",230)

def ch_trades(bt):
    tdf=bt["trade_log"]; colors=[GREEN if o=="WIN" else RED for o in tdf["outcome"]]
    fig=go.Figure(go.Bar(x=tdf["exit_date"].astype(str),y=tdf["pnl_pct"],
        marker_color=colors,opacity=.85,
        text=[f"{v:+.1f}%" for v in tdf["pnl_pct"]],textposition="outside",textfont=dict(size=8)))
    fig.add_hline(y=0,line_color=MUTE,line_width=1)
    m=tdf["pnl_pct"].mean()
    fig.add_hline(y=m,line_color=ACCENT,line_dash="dash",line_width=1.5,
                  annotation_text=f"Avg {m:+.2f}%",annotation_font_color=ACCENT)
    fig.update_layout(xaxis_tickangle=-45,showlegend=False)
    return _layout(fig,f"TRADE-BY-TRADE P&L · {len(tdf)} TRADES",310)

def ch_monthly(bt):
    mp=bt["monthly_pnl"]; colors=[GREEN if v>=0 else RED for v in mp.values]
    fig=go.Figure(go.Bar(x=[str(p) for p in mp.index],y=mp.values,marker_color=colors,opacity=.85,
        text=[f"₹{v:,.0f}" for v in mp.values],textposition="outside",textfont=dict(size=8)))
    fig.add_hline(y=0,line_color=MUTE,line_width=1)
    fig.update_layout(xaxis_tickangle=-45,showlegend=False)
    return _layout(fig,"MONTHLY NET P&L (₹)",270)

def ch_hist(bt):
    tdf=bt["trade_log"]
    fig=go.Figure()
    fig.add_trace(go.Histogram(x=tdf["pnl_pct"],nbinsx=30,marker_color=BLUE,opacity=.8))
    fig.add_vline(x=tdf["pnl_pct"].mean(),line_color=GREEN,line_width=2,
                  annotation_text=f"Avg {tdf['pnl_pct'].mean():+.2f}%",annotation_font_color=GREEN)
    return _layout(fig,"RETURN DISTRIBUTION (%)",250)

def ch_exit_pie(bt):
    er=bt.get("exit_reasons",{})
    if not er: return go.Figure()
    fig=go.Figure(go.Pie(labels=list(er.keys()),values=list(er.values()),hole=.45,
        marker=dict(colors=[GREEN,RED,ACCENT,BLUE,ORANGE,PURPLE][:len(er)]),
        textfont=dict(family="monospace",size=10)))
    fig.update_layout(template="plotly_dark",paper_bgcolor=PANEL,height=250,
        margin=dict(l=0,r=0,t=30,b=0),
        title=dict(text="EXIT REASONS",font=dict(size=11,color=ACCENT)),
        font=dict(family="monospace",size=10,color=IVORY),showlegend=True,
        legend=dict(orientation="v",x=1,y=.5,font=dict(size=9)))
    return fig

# ════════════════════════════════════════════════════════════════════════════
# MODULE 1 RENDERER
# ════════════════════════════════════════════════════════════════════════════

def _render_options_analytics(api_key=""):
    st.markdown(f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};"
                f"letter-spacing:1px;margin-bottom:8px;'>📡 DATA: Simulated NSE chain · "
                f"Swap generate_mock_chain() for live nsepython feed</div>",
                unsafe_allow_html=True)

    s1,s2,s3,s4=st.columns([1,2,1,1])
    mode=s1.radio("Type",["📈 Index","🏢 Stock"],horizontal=True,label_visibility="collapsed")
    inst_list=INDEX_NAMES if "Index" in mode else STOCK_NAMES
    name=s2.selectbox("Instrument",inst_list,label_visibility="collapsed")
    cfg=ALL_INSTRUMENTS[name]
    spot=float(cfg["spot"]); lot=int(cfg["lot"]); step=int(cfg["step"])
    base_iv=float(cfg["iv"]); dte_list=cfg["dte"]
    r_pct=s3.number_input("RFR %",value=6.5,min_value=0.,step=0.25)/100
    s4.write(""); s4.write("")
    s4.button("🔄 REFRESH",type="primary",use_container_width=True)

    with st.spinner(f"Generating {name} options chain..."):
        chain=generate_mock_chain(spot,r_pct,name,base_iv,step,tuple(dte_list))

    with st.spinner("Computing signals..."):
        gex=compute_gex(chain,lot)
        levels=find_gex_levels(gex,spot)
        vf=compute_vanna(chain,lot)
        ladder=flow_ladder(chain,spot,step=step)
        tdf=theta_curves(spot,r_pct,base_iv)
        smile_df,nearest_dte=build_smile(chain,dte_list[0] if dte_list else 0)

        gs,gt,gv=_score_gex(gex,levels,spot)
        vs,vt,vv=_score_vanna(vf,spot)
        ts,tt,tv,atm_iv,iv_regime=_score_theta_iv(chain,spot)
        fs,ft,fv=_score_flow(ladder)
        overall,total=_combined_verdict(gs,vs,ts,fs)
        comp={"gex":gs,"vanna":vs,"theta":ts,"flow":fs}
        vanna_bias="BULLISH" if vs>0 else "BEARISH" if vs<0 else "NEUTRAL"
        gex_regime="POSITIVE" if levels["total_pos_gex"]+levels["total_neg_gex"]>0 else "NEGATIVE"

    ai_note=""
    if api_key:
        with st.spinner("🤖 AI analysing..."): ai_note=_gemini_note(name,spot,overall,total,gt,vt,tt,ft,api_key)
    if not ai_note: ai_note=_rule_note(overall,total,name,levels,spot,gs,vs,ts,fs)

    # Store for Module 2
    st.session_state["_qa"]={"verdict":overall,"total":total,"gex_regime":gex_regime,
        "vanna_bias":vanna_bias,"iv_regime":iv_regime,"atm_iv":atm_iv,
        "levels":levels,"name":name,"spot":spot}

    _big_verdict_banner(overall,name,total,comp,ai_note)

    t0,t1,t2,t3,t4=st.tabs(["📈 IV SURFACE & SMILE","📊 THETA DECAY","🟩 NET GEX","🌊 VANNA FLOW","📋 ORDER FLOW"])

    with t0:
        st.markdown(_sec("3D IMPLIED VOLATILITY SURFACE"),unsafe_allow_html=True)
        st.caption("Downsampled 20×12 grid. Gold=Calls / Red=Puts. Drag to rotate.")
        sarr,darr,ivc,ivp=build_iv_surface(chain)
        st.plotly_chart(ch_surface(sarr,darr,ivc,ivp),use_container_width=True)
        st.markdown(_sec(f"{'0DTE' if nearest_dte==0 else str(nearest_dte)+'DTE'} IV SMILE / SMIRK"),unsafe_allow_html=True)
        st.plotly_chart(ch_smile(smile_df,spot,nearest_dte),use_container_width=True)
        iv28=chain[(chain["type"]=="call")&(chain["dte"]==28)]["iv"].mean()*100
        skew=(smile_df[smile_df["type"]=="put"]["iv"].mean()-smile_df[smile_df["type"]=="call"]["iv"].mean())*100
        _mrow([("ATM IV",f"{atm_iv:.1f}%"),("Put−Call Skew",f"{skew:+.2f}%"),
               ("0DTE vs 28d",f"{atm_iv-iv28:+.1f}%"),("Strikes",f"{len(smile_df)//2}")])
        _signal_card("IV SURFACE & SMILE SIGNAL",tt,tv,ts)

    with t1:
        st.markdown(_sec("NON-LINEAR THETA DECAY"),unsafe_allow_html=True)
        st.caption("ATM call theta accelerates sharply toward 0DTE.")
        st.plotly_chart(ch_theta(tdf),use_container_width=True)
        cols=st.columns(4)
        for col,dte_s in zip(cols,[90,30,7,1]):
            rw=tdf[(tdf["start_dte"]==dte_s)&(tdf["dte"]==max(dte_s//2,1))].head(1)
            tv2=rw["theta_pct"].values[0] if not rw.empty else 0
            col.metric(f"Theta DTE≈{dte_s//2}",f"{tv2:.4f}%/day")
        theta_txt=(f"ATM IV {atm_iv:.1f}% — {'High premium zone: option sellers favored, theta decay rapid. Sell strangles/iron condors on weekly strikes.' if atm_iv>18 else 'Low premium: option buyers have edge. Theta burn is slow. Buy directional plays.' if atm_iv<14 else 'Neutral zone: balanced risk/reward for buyers and sellers.'}")
        _signal_card("THETA REGIME",theta_txt,
            "WEAK SELL" if atm_iv>18 else "NEUTRAL" if atm_iv>14 else "WEAK BUY",
            -1 if atm_iv>18 else 0 if atm_iv>14 else 1)

    with t2:
        st.markdown(_sec("NET GAMMA EXPOSURE (GEX)"),unsafe_allow_html=True)
        st.caption("+GEX (green) = MM long gamma → pins price. −GEX (red) = MM short gamma → amplifies moves.")
        st.plotly_chart(ch_gex(gex,spot,levels),use_container_width=True)
        _mrow([("Call Resistance",f"₹{levels['call_resistance']:,.0f}" if levels["call_resistance"] else "—"),
               ("Put Support",f"₹{levels['put_support']:,.0f}" if levels["put_support"] else "—"),
               ("Total +GEX",f"₹{levels['total_pos_gex']:.1f}M"),
               ("Total −GEX",f"₹{levels['total_neg_gex']:.1f}M")])
        with st.expander("GEX Raw Data"):
            st.dataframe(gex[["strike","cg","pg","net_gex"]].rename(
                columns={"strike":"Strike","cg":"Call GEX(M₹)","pg":"Put GEX(M₹)","net_gex":"Net GEX(M₹)"}).round(2),
                use_container_width=True,hide_index=True)
        _signal_card("GEX REGIME SIGNAL",gt,gv,gs)

    with t3:
        st.markdown(_sec("VANNA FLOW — DEALER REHEDGE"),unsafe_allow_html=True)
        st.caption("Vanna = dΔ/dσ: how dealer delta hedges move when IV changes.")
        st.plotly_chart(ch_vanna(vf,spot),use_container_width=True)
        _mrow([("Net Δ-Notional",f"₹{vf['net_delta_notional'].sum():.1f}M"),
               ("Vanna above spot",f"{vf[vf['strike']>spot]['net_vanna'].sum():.1f}"),
               ("Vanna below spot",f"{vf[vf['strike']<spot]['net_vanna'].sum():.1f}"),
               ("Vanna Bias",vanna_bias)])
        _signal_card("VANNA FLOW SIGNAL",vt,vv,vs)

    with t4:
        st.markdown(_sec("ORDER FLOW LADDER"),unsafe_allow_html=True)
        st.caption("Net contracts bought vs sold per strike at nearest expiry. ★ = ATM")
        st.plotly_chart(ch_flow(ladder,spot),use_container_width=True)
        d2=ladder.copy()
        d2["CALL Flow"]=d2["call_flow"].apply(lambda x:f"{x:+,d}")
        d2["CALL OI"]=d2["call_oi"].apply(lambda x:f"{x:,d}")
        d2["CALL IV%"]=d2["call_iv"].apply(lambda x:f"{x:.1f}%")
        d2["STRIKE"]=d2.apply(lambda r:f"{'★ ' if r['is_atm'] else ''}{r['strike']:,.0f}",axis=1)
        d2["PUT IV%"]=d2["put_iv"].apply(lambda x:f"{x:.1f}%")
        d2["PUT OI"]=d2["put_oi"].apply(lambda x:f"{x:,d}")
        d2["PUT Flow"]=d2["put_flow"].apply(lambda x:f"{x:+,d}")
        st.dataframe(d2[["CALL Flow","CALL OI","CALL IV%","STRIKE","PUT IV%","PUT OI","PUT Flow"]],
                     use_container_width=True,hide_index=True)
        _signal_card("ORDER FLOW SIGNAL",ft,fv,fs)

# ════════════════════════════════════════════════════════════════════════════
# MODULE 2 RENDERER
# ════════════════════════════════════════════════════════════════════════════

def _render_backtest():
    qa=st.session_state.get("_qa",{})
    verdict=qa.get("verdict","NEUTRAL"); gex_regime=qa.get("gex_regime","POSITIVE")
    vanna_bias=qa.get("vanna_bias","NEUTRAL"); iv_regime=qa.get("iv_regime","CALM")
    opt_name=qa.get("name","NIFTY"); opt_total=qa.get("total",0)

    cfg_vc=VERDICT_CONFIG.get(verdict,VERDICT_CONFIG["NEUTRAL"])
    vc=cfg_vc["color"]; vbg=cfg_vc["bg"]; vic=cfg_vc["icon"]

    # Module 1 signal summary
    st.markdown(f"""
    <div style='background:{vbg};border:1px solid {vc}44;border-left:4px solid {vc};
    border-radius:10px;padding:14px 18px;margin-bottom:16px;'>
      <div style='font-family:{MONO};font-size:10px;color:{MUTE};letter-spacing:1px;'>
        CURRENT OPTIONS SIGNAL FROM MODULE 1 (used as direction filter)</div>
      <div style='font-family:{MONO};font-size:22px;font-weight:800;color:{vc};margin:4px 0;'>
        {vic} {verdict} on {opt_name}</div>
      <div style='font-size:13px;color:{IVORY};'>
        Score {opt_total:+d}/8 · GEX regime: <b>{gex_regime}</b> ·
        Vanna: <b>{vanna_bias}</b> · IV: <b>{iv_regime}</b><br>
        <span style='color:{MUTE};font-size:11px;'>
        ⚡ This signal filters trade direction only. Actual entry/exit uses real technical signals on price data.</span>
      </div>
    </div>""",unsafe_allow_html=True)

    # Strategy cards
    st.markdown(_sec("SELECT YOUR STRATEGY"),unsafe_allow_html=True)
    sc1,sc2,sc3,sc4=st.columns(4)
    for col,(strat,meta) in zip([sc1,sc2,sc3,sc4],STRATEGY_META.items()):
        with col:
            st.markdown(f"""
            <div style='background:{PANEL2};border:1px solid {meta["color"]}44;
            border-radius:8px;padding:12px;text-align:center;height:120px;'>
              <div style='font-size:22px;'>{meta["icon"]}</div>
              <div style='font-family:{MONO};font-size:11px;font-weight:700;
                color:{meta["color"]};margin:4px 0;'>{strat}</div>
              <div style='font-size:10px;color:{MUTE};'>{meta["style"]}</div>
              <div style='font-size:10px;color:{MUTE};margin-top:4px;'>{meta["best"]}</div>
            </div>""",unsafe_allow_html=True)

    with st.form("bt_form"):
        st.markdown(_sec("CONFIGURATION"),unsafe_allow_html=True)
        f1,f2,f3=st.columns([2,2,2])
        symbol=f1.text_input("NSE Symbol",value="RELIANCE",
            help="RELIANCE, TCS, ^NSEI (Nifty Index), HDFCBANK, etc.")
        strategy=f2.selectbox("Strategy",list(STRATEGY_META.keys()))
        direction=f3.selectbox("Direction Override",
            ["From Options Signal","Long Only","Short Only","Both Sides"])

        f4,f5=st.columns(2)
        start_date=f4.date_input("Start Date",value=datetime.date(2020,1,1),
                                  min_value=datetime.date(2010,1,1))
        end_date=f5.date_input("End Date",value=datetime.date.today())

        st.markdown(_sec("RISK & SIZING"),unsafe_allow_html=True)
        r1,r2,r3,r4=st.columns(4)
        initial_cap=r1.number_input("Capital (₹)",value=1_000_000,step=100_000,min_value=10_000)
        risk_pt=r2.slider("Risk per Trade %",0.3,3.0,1.0,step=0.1)/100
        atr_stop=r3.slider("ATR Stop Multiplier",1.0,4.0,2.0,step=0.25)
        atr_target=r4.slider("ATR Target Multiplier",2.0,8.0,4.0,step=0.5)

        r5,r6,r7=st.columns(3)
        atr_trail=r5.slider("ATR Trail Multiplier",1.0,4.0,2.5,step=0.25)
        max_hold=r6.number_input("Max Hold Days",value=30,min_value=5,max_value=120)
        commission=r7.number_input("Commission (bps)",value=15.,step=1.)/10_000

        use_opt_filter=st.checkbox("Apply Options Signal Direction Filter",value=True,
            help="When ON: options signal from Module 1 biases trade direction.")
        run=st.form_submit_button("▶ RUN INSTITUTIONAL BACKTEST",
                                   type="primary",use_container_width=True)

    if not run:
        st.markdown(f"""
        <div style='background:{PANEL2};border:1px solid {BORDER};border-radius:8px;
        padding:16px;margin-top:12px;'>
          <div style='font-family:{MONO};font-size:11px;color:{ACCENT};margin-bottom:8px;'>
            HOW THE NEW STRATEGY ENGINE WORKS</div>
          <div style='font-size:13px;color:{IVORY};line-height:1.85;'>
            🔧 <b>Root cause of old strategy failure:</b> NIFTY GEX levels (₹24,200) were compared to
            RELIANCE price (₹2,900) — those levels can never trigger.<br><br>
            ✅ <b>New approach (institutional standard):</b><br>
            • <b>Options Signal</b> = <i>regime filter only</i> — tells us LONG/SHORT/BOTH bias<br>
            • <b>Technical signals</b> = actual entry (EMA stack, ADX, Donchian, RSI + BB)<br>
            • <b>ATR-based stops</b> = dynamic, volatility-adaptive (not fixed %)<br>
            • <b>Trailing stop</b> = activates after 1.5× ATR in your favor — locks in profits<br>
            • <b>Risk-based sizing</b> = risk exactly X% of portfolio per trade (hedge fund standard)
          </div>
        </div>""",unsafe_allow_html=True)
        return

    if start_date>=end_date: st.error("Start must be before end date."); return

    dir_map={"From Options Signal":verdict,"Long Only":"BUY","Short Only":"SELL","Both Sides":"NEUTRAL"}
    eff_verdict=dir_map[direction]

    with st.spinner(f"Fetching {symbol.upper()} data..."):
        df_raw=fetch_ohlcv(symbol,str(start_date),str(end_date))

    if df_raw.empty:
        st.error(f"No data for '{symbol}'. Try: RELIANCE, TCS, INFY, ^NSEI (Nifty), ^NSEBANK (BankNifty).")
        return

    st.markdown(f"<div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-bottom:4px;'>"
                f"Loaded <b>{len(df_raw)}</b> bars · "
                f"{df_raw.index[0].strftime('%Y-%m-%d')} → {df_raw.index[-1].strftime('%Y-%m-%d')}</div>",
                unsafe_allow_html=True)

    with st.spinner(f"Running {strategy} backtest..."):
        bt=run_backtest(
            df_raw=df_raw,strategy=strategy,direction="both",
            initial_cap=float(initial_cap),risk_per_trade=risk_pt,
            atr_stop=atr_stop,atr_target=atr_target,atr_trail=atr_trail,
            max_hold=int(max_hold),commission=commission,slippage=0.0005,
            options_verdict=eff_verdict if use_opt_filter else "NEUTRAL",
            gex_regime=gex_regime,long_only_from_signal=use_opt_filter,
        )

    if "error" in bt:
        st.error(bt["error"]); return

    # Strategy verdict banner
    sv=bt["strat_verdict"]; sc=bt["verdict_color"]
    emoji="✅" if "DEPLOY" in sv else "🟡" if "PAPER" in sv else "⚠️" if "OPTIM" in sv else "❌"
    st.markdown(f"""
    <div style='background:{"rgba(31,185,122,0.12)" if "DEPLOY" in sv else "rgba(245,158,11,0.12)" if "PAPER" in sv else "rgba(230,126,34,0.10)" if "OPTIM" in sv else "rgba(232,85,78,0.12)"};
    border:2px solid {sc}55;border-left:6px solid {sc};
    border-radius:12px;padding:18px 24px;margin:12px 0;'>
      <div style='font-family:{MONO};font-size:10px;color:{MUTE};margin-bottom:4px;'>
        STRATEGY VALIDATION · {strategy} · {symbol.upper()}</div>
      <div style='font-family:{MONO};font-size:28px;font-weight:900;color:{sc};'>{sv}</div>
      <div style='font-family:{MONO};font-size:13px;color:{IVORY};margin-top:6px;'>
        Total Return <b style='color:{GREEN if bt["total_ret"]>0 else RED};'>{bt["total_ret"]:+.2f}%</b>
        &nbsp;·&nbsp; {bt["total_trades"]} trades
        &nbsp;·&nbsp; Win Rate <b>{bt["win_rate"]}%</b>
        &nbsp;·&nbsp; Sharpe <b>{bt["sharpe"]}</b>
        &nbsp;·&nbsp; Profit Factor <b>{bt["profit_factor"]}</b>
        &nbsp;·&nbsp; Max DD <b style='color:{RED};'>{bt["max_dd"]:.1f}%</b>
      </div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};margin-top:4px;'>
        Options filter: {eff_verdict} ({"applied" if use_opt_filter else "off"}) ·
        ATR Stop {atr_stop}× · ATR Target {atr_target}× · Trail {atr_trail}× ·
        Risk/trade {risk_pt*100:.1f}%
      </div>
    </div>""",unsafe_allow_html=True)

    # Metrics
    st.markdown(_sec("PERFORMANCE METRICS"),unsafe_allow_html=True)
    _mrow([("Total Trades",str(bt["total_trades"])),("Win Rate",f"{bt['win_rate']}%"),
           ("Avg Win",f"{bt['avg_win']:+.2f}%"),("Avg Loss",f"{bt['avg_loss']:+.2f}%")])
    _mrow([("R:R Ratio",f"{bt['rr']:.2f}x"),("Profit Factor",f"{bt['profit_factor']:.2f}"),
           ("Expectancy",f"₹{bt['expectancy']:,.0f}"),("Max Consec. Loss",str(bt["max_cl"]))])
    _mrow([("Max Drawdown",f"{bt['max_dd']:.1f}%"),("DD Duration",f"{bt['dd_dur']} days"),
           ("Sharpe",f"{bt['sharpe']:.2f}"),("Sortino",f"{bt['sortino']:.2f}")])
    _mrow([("Calmar",f"{bt['calmar']:.2f}"),("Ann. Return",f"{bt['ann_ret']:+.2f}%"),
           ("Ann. Vol",f"{bt['ann_vol']:.2f}%"),("Final Equity",f"₹{bt['final_equity']:,.0f}")])

    # Charts
    st.markdown(_sec("EQUITY CURVE & DRAWDOWN"),unsafe_allow_html=True)
    st.plotly_chart(ch_equity(bt),use_container_width=True)
    st.plotly_chart(ch_dd(bt),use_container_width=True)

    ca,cb=st.columns(2)
    with ca: st.plotly_chart(ch_trades(bt),use_container_width=True)
    with cb: st.plotly_chart(ch_hist(bt),use_container_width=True)

    cc,cd=st.columns([2,1])
    with cc: st.plotly_chart(ch_monthly(bt),use_container_width=True)
    with cd: st.plotly_chart(ch_exit_pie(bt),use_container_width=True)

    # Trade ledger
    st.markdown(_sec("TRADE LEDGER"),unsafe_allow_html=True)
    tdf=bt["trade_log"].copy()
    tdf["entry_date"]=pd.to_datetime(tdf["entry_date"]).dt.strftime("%Y-%m-%d")
    tdf["exit_date"] =pd.to_datetime(tdf["exit_date"]).dt.strftime("%Y-%m-%d")
    tdf["pnl"]       =tdf["pnl"].apply(lambda x:f"₹{x:,.0f}")
    tdf["pnl_pct"]   =tdf["pnl_pct"].apply(lambda x:f"{x:+.2f}%")
    tdf.columns=[c.replace("_"," ").title() for c in tdf.columns]
    st.dataframe(tdf,use_container_width=True,hide_index=True,height=300)
    st.caption("Risk-based sizing (risk% per trade not fixed %). "
               "ATR stops auto-adapt to volatility. Trailing stop locks in profits. "
               "Educational only · Not SEBI investment advice.")

# ════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def render_quant_analysis():
    try:    api_key=st.secrets.get("GEMINI_KEY","")
    except: api_key=""

    ts=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div style='background:{PANEL};border:1px solid {BORDER};border-radius:8px;
    padding:12px 18px;margin-bottom:10px;display:flex;
    justify-content:space-between;align-items:center;'>
      <div style='font-family:{MONO};font-size:13px;font-weight:700;
        color:{ACCENT};letter-spacing:2px;'>ARKA · QUANT TERMINAL v4</div>
      <div style='font-family:{MONO};font-size:11px;color:{MUTE};'>
        {ts} · NSE INDIA · {"🤖 AI ACTIVE" if api_key else "📐 RULE-BASED MODE"}</div>
    </div>""",unsafe_allow_html=True)

    m1,m2=st.tabs([
        "📊 MODULE 1 · OPTIONS ANALYTICS & SIGNAL",
        "⚙️  MODULE 2 · INSTITUTIONAL BACKTEST",
    ])
    with m1: _render_options_analytics(api_key)
    with m2: _render_backtest()

render_quant_options_page=render_quant_analysis

if __name__=="__main__":
    st.set_page_config(page_title="ARKA · Quant v4",layout="wide",
                       initial_sidebar_state="collapsed")
    render_quant_analysis()
