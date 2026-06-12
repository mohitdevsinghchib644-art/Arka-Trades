"""
ui_theme.py — ChartX-style design system for Arka Trades
Import this in app.py, news_feed.py, arka_ai.py, smart_scan_page.py
"""
import streamlit as st
import json

# ── ChartX Palette (slate/zinc dark, not pitch black) ─────────
DARK   = "#0B0F17"   # page background
DARK2  = "#0F1522"   # card background
DARK3  = "#151D2E"   # input / nested background
BORDER = "#1E293B"   # micro-borders (slate-800)
IVORY  = "#E2E8F0"   # primary text
T2     = "#94A3B8"   # secondary text
NAVY   = "#101A33"   # deep panel
GOLD   = "#4F8DFD"   # ← brand accent is now ELECTRIC BLUE (keeps old var name)
BLUE   = "#4F8DFD"
GREEN  = "#10B981"   # emerald
RED    = "#EF4444"   # crimson
PURPLE = "#8B5CF6"

FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"

def inject_css():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
    html,body,.stApp{{background:{DARK} !important;color:{IVORY} !important;font-family:{FONT} !important;}}
    header[data-testid="stHeader"],section[data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"]{{display:none !important;}}
    .block-container{{padding:0 !important;max-width:1440px !important;}}

    /* Inputs */
    .stTextInput input,.stTextArea textarea,[data-testid="stSelectbox"]>div>div,
    .stNumberInput input{{
        background:{DARK3} !important;color:{IVORY} !important;
        border:1px solid {BORDER} !important;border-radius:10px !important;
        font-family:{FONT} !important;font-size:14px !important;
        transition:border-color .15s ease !important;
    }}
    .stTextInput input:focus,.stTextArea textarea:focus{{
        border-color:{BLUE} !important;box-shadow:0 0 0 3px rgba(79,141,253,.15) !important;
    }}
    .stTextInput label,.stTextArea label,.stNumberInput label{{
        color:{T2} !important;font-size:12px !important;font-weight:600 !important;
    }}

    /* Cards / metrics */
    [data-testid="metric-container"]{{
        background:{DARK2} !important;border:1px solid {BORDER} !important;
        border-radius:12px !important;padding:16px !important;
        box-shadow:0 1px 3px rgba(0,0,0,.3) !important;
    }}
    [data-testid="stMetricLabel"] p{{font-size:12px !important;font-weight:600 !important;color:{T2} !important;}}
    [data-testid="stMetricValue"]{{font-family:{MONO} !important;font-size:22px !important;color:{IVORY} !important;}}

    /* Buttons */
    .stButton>button{{
        background:{DARK3} !important;color:{IVORY} !important;
        border:1px solid {BORDER} !important;border-radius:10px !important;
        font-family:{FONT} !important;font-weight:600 !important;font-size:14px !important;
        transition:all .15s ease !important;
    }}
    .stButton>button:hover{{border-color:{BLUE} !important;color:{BLUE} !important;
        transform:translateY(-1px);box-shadow:0 4px 12px rgba(79,141,253,.15) !important;}}
    .stButton>button[kind="primary"]{{
        background:{BLUE} !important;color:#fff !important;border:none !important;
        box-shadow:0 2px 8px rgba(79,141,253,.35) !important;
    }}
    .stButton>button[kind="primary"]:hover{{background:#3B7BF0 !important;color:#fff !important;}}

    /* Tabs */
    .stTabs [data-baseweb="tab-list"]{{background:{DARK2};border:1px solid {BORDER};
        border-radius:12px;padding:4px;gap:4px;}}
    .stTabs [data-baseweb="tab"]{{color:{T2};font-weight:600;border-radius:8px;}}
    .stTabs [aria-selected="true"]{{background:{DARK3} !important;color:{BLUE} !important;}}

    hr{{border-color:{BORDER} !important;}}
    .stProgress>div>div{{background:{BLUE} !important;}}
    .stCheckbox label,.stRadio label{{color:{IVORY} !important;}}

    /* Nav buttons (left rail) */
    .nav-btn .stButton>button{{width:100% !important;text-align:left !important;
        background:transparent !important;border:none !important;color:{T2} !important;
        padding:9px 14px !important;font-size:14px !important;box-shadow:none !important;}}
    .nav-btn .stButton>button:hover{{background:{DARK3} !important;color:{IVORY} !important;transform:none;}}
    .nav-btn-active .stButton>button{{background:rgba(79,141,253,.1) !important;
        color:{BLUE} !important;border-left:3px solid {BLUE} !important;
        border-radius:0 10px 10px 0 !important;}}

    @keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(16,185,129,.4);}}
        50%{{opacity:.7;box-shadow:0 0 0 6px rgba(16,185,129,0);}}}}
    .pulse-dot{{width:8px;height:8px;border-radius:50%;background:{GREEN};
        display:inline-block;animation:pulse 2s infinite;}}
    @keyframes fadeUp{{from{{opacity:0;transform:translateY(12px);}}to{{opacity:1;transform:none;}}}}
    .fade-up{{animation:fadeUp .5s ease both;}}
    </style>
    """, unsafe_allow_html=True)

# ── Components ────────────────────────────────────────────────
def section(title: str):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;margin:36px 0 18px;">
        <div style="font-family:{FONT};font-size:18px;font-weight:800;
             color:{IVORY};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)

def card(html_inner: str, accent: str = None, pad: str = "24px"):
    top = f"border-top:2px solid {accent};" if accent else ""
    st.markdown(f"""
    <div class="fade-up" style="background:{DARK2};border:1px solid {BORDER};{top}
         border-radius:14px;padding:{pad};box-shadow:0 1px 3px rgba(0,0,0,.3);
         margin-bottom:12px;">{html_inner}</div>""", unsafe_allow_html=True)

def change_pill(chg: float) -> str:
    """Colored 24h-change pill, ChartX style."""
    c, bg = (GREEN, "rgba(16,185,129,.12)") if chg >= 0 else (RED, "rgba(239,68,68,.12)")
    arrow = "▲" if chg >= 0 else "▼"
    return (f'<span style="background:{bg};color:{c};font-family:{MONO};'
            f'font-size:12px;font-weight:700;padding:3px 10px;border-radius:20px;'
            f'border:1px solid {c}33;">{arrow} {abs(chg):.2f}%</span>')

def impact_tag(level: str) -> str:
    m = {"High": (RED, "rgba(239,68,68,.12)"), "Medium": (GOLD, "rgba(79,141,253,.12)"),
         "Low": (T2, "rgba(148,163,184,.12)")}
    c, bg = m.get(level, m["Low"])
    return (f'<span style="background:{bg};color:{c};font-size:10px;font-weight:700;'
            f'letter-spacing:1px;padding:2px 8px;border-radius:6px;text-transform:uppercase;">{level}</span>')

def sparkline(values: list, color: str = None, w: int = 110, h: int = 32) -> str:
    """Inline SVG sparkline for table rows — no JS needed."""
    if not values or len(values) < 2:
        return ""
    color = color or (GREEN if values[-1] >= values[0] else RED)
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i/(len(values)-1)*w:.1f},{h-2-((v-lo)/rng)*(h-6):.1f}"
                   for i, v in enumerate(values))
    return (f'<svg width="{w}" height="{h}" style="display:block;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')

def hero():
    """ChartX-style landing hero — call on the login page."""
    st.markdown(f"""
    <div class="fade-up" style="text-align:center;padding:90px 24px 56px;">
        <div style="display:inline-flex;align-items:center;gap:8px;background:{DARK2};
             border:1px solid {BORDER};border-radius:30px;padding:6px 16px;margin-bottom:28px;">
            <span class="pulse-dot"></span>
            <span style="font-size:12px;font-weight:600;color:{T2};">Live NSE market data</span>
        </div>
        <h1 style="font-family:{FONT};font-size:54px;font-weight:800;line-height:1.1;
             color:{IVORY};max-width:840px;margin:0 auto 20px;letter-spacing:-1.5px;">
            Next-Generation<br>
            <span style="background:linear-gradient(90deg,{BLUE},{PURPLE});
                 -webkit-background-clip:text;-webkit-text-fill-color:transparent;">
                 Market Analytics Infrastructure</span>
        </h1>
        <p style="font-size:17px;color:{T2};max-width:560px;margin:0 auto 8px;line-height:1.7;">
            Automated breakout scans, AI chart analysis and instant Telegram alerts —
            built for traders who value clarity and control.
        </p>
        <p style="font-size:12px;color:{T2};opacity:.6;">Not SEBI registered · Educational use only</p>
    </div>""", unsafe_allow_html=True)

def roadmap():
    """3-column onboarding timeline."""
    steps = [
        ("DAY 1", "Connection & Import", "Log in and upload your TradingView watchlist. Cloud sync via Supabase is instant.", BLUE),
        ("DAY 7", "Arka AI Training", "Teach the AI your personal setups, rules and reference charts. It remembers forever.", PURPLE),
        ("DAY 14", "Live Scans & Alerts", "Automated breakout scans and Telegram alerts go live across your full universe.", GREEN),
    ]
    cols = st.columns(3)
    for col, (day, title, desc, c) in zip(cols, steps):
        with col:
            card(f"""
            <div style="font-family:{MONO};font-size:11px;font-weight:700;color:{c};
                 letter-spacing:2px;margin-bottom:10px;">{day}</div>
            <div style="font-size:16px;font-weight:800;color:{IVORY};margin-bottom:8px;">{title}</div>
            <div style="font-size:13px;color:{T2};line-height:1.7;">{desc}</div>
            """, accent=c)

def comparison_card():
    """Traditional vs Smart Screener — value comparison block."""
    rows = [
        ("Time per scan", "2–3 hours manual charting", "Under 90 seconds"),
        ("Coverage", "20–30 stocks max", "420+ NSE stocks"),
        ("Pattern detection", "Eye-balling, inconsistent", "AI vision, rule-based"),
        ("Alerts", "Missed moves", "Instant Telegram push"),
    ]
    body = "".join(
        f'<tr><td style="padding:10px 14px;color:{T2};font-size:13px;border-top:1px solid {BORDER};">{a}</td>'
        f'<td style="padding:10px 14px;color:{RED};font-size:13px;border-top:1px solid {BORDER};">✕ {b}</td>'
        f'<td style="padding:10px 14px;color:{GREEN};font-size:13px;border-top:1px solid {BORDER};">✓ {c}</td></tr>'
        for a, b, c in rows)
    card(f"""
    <table style="width:100%;border-collapse:collapse;">
        <tr>
            <th style="text-align:left;padding:10px 14px;font-size:12px;color:{T2};"></th>
            <th style="text-align:left;padding:10px 14px;font-size:13px;color:{IVORY};">Traditional Screening</th>
            <th style="text-align:left;padding:10px 14px;font-size:13px;color:{BLUE};">Smart Screener Automation</th>
        </tr>{body}
    </table>""", accent=BLUE, pad="12px")
