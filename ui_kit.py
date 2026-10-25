"""
ui_kit.py — Arka Trades design system (one source of truth for the look of the whole site)

Principles (researched from Bloomberg Terminal / trading-terminal design systems)
-------------------------------------------------------------------------------
1. Density WITH hierarchy: compact 11-13px data, but primary / secondary / tertiary text tones,
   thin 1px rules and spatial grouping keep it scannable instead of noisy.
2. Two fonts only: Inter for interface text, JetBrains Mono for numbers; tabular numerals everywhere
   so columns of figures line up.
3. Colour is semantic, never decorative: amber = brand / selection, green = up, red = down,
   cyan = info. Colour is never the ONLY signal: +/- signs, arrows and labels always accompany it
   (colour-vision-deficiency safe).
4. Theme = tokens. Everything below reads CSS variables; changing the look means changing :root only.
5. One way to do each thing: page header, section title, KPI card, notice, badge, table, chips.

Public API
----------
inject()                       global CSS (call once per run, first thing)
page_header / section / notice / kpis / badge / delta / status_chips / empty / card
nav(items, active)             top navigation row (buttons)           -> clicked target | None
segmented(label, options, key) pill-style radio                       -> selected option
table(columns, rows, ...)      sortable, sticky-header terminal table (iframe)
spark(values)                  inline SVG sparkline
"""
from __future__ import annotations

import html as _html
import math

import streamlit as st
import streamlit.components.v1 as components

# ───────────────────────── tokens ─────────────────────────
ACCENT, UP, DOWN, INFO, VIOLET = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2"
TEXT1, TEXT2, TEXT3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
LINE, LINE2 = "#1F1F1F", "#2C2C2C"
S0, S1, S2, S3 = "#050505", "#0A0A0A", "#101010", "#161616"
FONT_UI = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif"
FONT_DATA = "'JetBrains Mono','SF Mono',Consolas,monospace"

TONES = {"up": UP, "down": DOWN, "accent": ACCENT, "info": INFO, "violet": VIOLET, "muted": TEXT3, "": TEXT1}


def e(x) -> str:
    return _html.escape("" if x is None else str(x))


def _isnull(v) -> bool:
    if v is None:
        return True
    try:
        return isinstance(v, (float, int)) and isinstance(v, float) and math.isnan(v)
    except Exception:
        return False


def _num(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


# ───────────────────────── global CSS ─────────────────────────
_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{
  --bg:#050505; --s1:#0A0A0A; --s2:#101010; --s3:#161616; --line:#1F1F1F; --line2:#2C2C2C;
  --t1:#ECECEC; --t2:#A3A3A3; --t3:#6B6B6B;
  --accent:#FF9F0A; --accent-dim:rgba(255,159,10,.12); --up:#30D158; --down:#FF453A; --info:#5AC8FA; --violet:#BF5AF2;
  --ui:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif; --data:'JetBrains Mono','SF Mono',Consolas,monospace;
  color-scheme:dark;
}
*,*::before,*::after{box-sizing:border-box}
html,body,.stApp{background:var(--bg)!important;color:var(--t1)!important;font-family:var(--ui)!important;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
header[data-testid="stHeader"],[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],[data-testid="stToolbar"],#MainMenu,footer{display:none!important}
.block-container{padding:0 14px 36px!important;max-width:100%!important}
h1,h2,h3,h4{font-family:var(--ui)!important;color:var(--t1)!important;letter-spacing:-.01em}
p,li,label,span{font-family:var(--ui)}
a{color:var(--info)}
hr{border:0!important;border-top:1px solid var(--line)!important;margin:14px 0!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:var(--s1)}
::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:0}::-webkit-scrollbar-thumb:hover{background:#3a3a3a}
iframe{border:0}

/* ── buttons ── */
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{
  background:var(--s2)!important;color:var(--t2)!important;border:1px solid var(--line2)!important;border-radius:2px!important;
  font:600 11px/1 var(--ui)!important;letter-spacing:.35px;min-height:32px;padding:6px 12px;box-shadow:none!important;
  transition:border-color .12s,color .12s,background .12s}
.stButton>button:hover,.stDownloadButton>button:hover,.stFormSubmitButton>button:hover{border-color:var(--accent)!important;color:var(--accent)!important;background:var(--s3)!important}
.stButton>button:focus-visible,.stFormSubmitButton>button:focus-visible{outline:1px solid var(--accent)!important;outline-offset:2px}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{background:var(--accent)!important;color:#000!important;border-color:var(--accent)!important;font-weight:700!important}
.stButton>button[kind="primary"]:hover{background:#ffb338!important;color:#000!important}
.stButton>button:disabled{opacity:.4}

/* ── top navigation (container key=topnav) ── */
div[class*="st-key-topnav"] div[data-testid="stHorizontalBlock"]{gap:0!important;border-bottom:1px solid var(--line)}
div[class*="st-key-topnav"] .stButton>button{background:transparent!important;border:0!important;border-bottom:2px solid transparent!important;border-radius:0!important;
  color:var(--t3)!important;font:700 10px/1 var(--data)!important;letter-spacing:1.2px;text-transform:uppercase;min-height:40px;padding:0 6px}
div[class*="st-key-topnav"] .stButton>button:hover{color:var(--t1)!important;background:var(--s2)!important}
div[class*="st-key-topnav"] .stButton>button[kind="primary"]{color:var(--accent)!important;border-bottom-color:var(--accent)!important;background:var(--accent-dim)!important}

/* ── segmented control (container key=seg-*) ── */
div[class*="st-key-seg-"] div[role="radiogroup"]{gap:0!important;flex-wrap:wrap}
div[class*="st-key-seg-"] label{background:var(--s1);border:1px solid var(--line2);margin:0 -1px 0 0!important;padding:6px 12px!important;cursor:pointer;min-height:30px}
div[class*="st-key-seg-"] label>div:first-child{display:none!important}
div[class*="st-key-seg-"] label p{font:700 10px/1 var(--data)!important;letter-spacing:.8px;color:var(--t2)!important;text-transform:uppercase;white-space:nowrap}
div[class*="st-key-seg-"] label:hover{background:var(--s3)}
div[class*="st-key-seg-"] label:has(input:checked){background:var(--accent-dim);border-color:var(--accent);position:relative;z-index:1}
div[class*="st-key-seg-"] label:has(input:checked) p{color:var(--accent)!important}

/* ── inputs ── */
.stTextInput input,.stNumberInput input,.stTextArea textarea,.stDateInput input{background:var(--s1)!important;color:var(--t1)!important;border:1px solid var(--line2)!important;border-radius:2px!important;font:500 13px var(--ui)!important}
.stTextInput input:focus,.stNumberInput input:focus,.stTextArea textarea:focus{border-color:var(--accent)!important;box-shadow:0 0 0 1px var(--accent)!important}
.stTextInput label,.stSelectbox label,.stMultiSelect label,.stNumberInput label,.stDateInput label,.stTextArea label,.stRadio>label,.stCheckbox label,.stSlider label{
  font:600 9px/1.3 var(--data)!important;color:var(--t3)!important;text-transform:uppercase;letter-spacing:1px}
[data-testid="stSelectbox"]>div>div,[data-baseweb="select"]>div{background:var(--s1)!important;border:1px solid var(--line2)!important;border-radius:2px!important;color:var(--t1)!important}
[data-baseweb="select"]>div:hover{border-color:#444!important}
div[data-baseweb="popover"]>div,ul[role="listbox"]{background:var(--s2)!important;border:1px solid var(--line2)!important;border-radius:2px!important}
li[role="option"]{font:500 12px var(--ui)!important;color:var(--t1)!important}li[role="option"]:hover,li[aria-selected="true"]{background:var(--accent-dim)!important}
[data-baseweb="tag"]{background:var(--accent-dim)!important;border:1px solid rgba(255,159,10,.35)!important;border-radius:2px!important}
[data-baseweb="tag"] span{color:var(--accent)!important;font:600 10px var(--data)!important}
[data-testid="stFileUploaderDropzone"]{background:var(--s1)!important;border:1px dashed var(--line2)!important;border-radius:2px!important}
.stCheckbox span[data-baseweb="checkbox"]>div{border-radius:2px!important}

/* ── tabs ── */
[data-baseweb="tab-list"]{gap:0!important;background:transparent!important;border-bottom:1px solid var(--line)!important}
button[data-baseweb="tab"]{background:transparent!important;color:var(--t3)!important;font:700 10px/1 var(--data)!important;letter-spacing:1.1px;text-transform:uppercase;padding:13px 16px!important;border-radius:0!important}
button[data-baseweb="tab"]:hover{color:var(--t1)!important;background:var(--s2)!important}
button[data-baseweb="tab"][aria-selected="true"]{color:var(--accent)!important}
[data-baseweb="tab-highlight"]{background:var(--accent)!important;height:2px!important}
[data-baseweb="tab-border"]{background:var(--line)!important}

/* ── native widgets restyled ── */
[data-testid="stMetric"]{background:var(--s1)!important;border:1px solid var(--line)!important;border-radius:2px!important;padding:10px 12px!important}
[data-testid="stMetricLabel"] p{font:600 9px var(--data)!important;color:var(--t3)!important;letter-spacing:1px;text-transform:uppercase}
[data-testid="stMetricValue"]{font:700 18px var(--data)!important;color:var(--t1)!important}
[data-testid="stDataFrame"]{border:1px solid var(--line)!important;border-radius:2px}
[data-testid="stExpander"]{background:var(--s1)!important;border:1px solid var(--line)!important;border-radius:2px!important}
[data-testid="stExpander"] summary{font:700 10px var(--data)!important;letter-spacing:1px;text-transform:uppercase;color:var(--t2)!important}
[data-testid="stAlert"]{background:var(--s1)!important;border:1px solid var(--line2)!important;border-left:3px solid var(--info)!important;border-radius:2px!important;color:var(--t1)!important}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]){border-left-color:var(--accent)!important}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]){border-left-color:var(--down)!important}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]){border-left-color:var(--up)!important}
[data-testid="stAlert"] p{font:400 12px/1.6 var(--ui)!important}
[data-testid="stProgress"]>div>div{background:var(--accent)!important}
[data-testid="stCaptionContainer"],.stCaption{font:400 11px/1.5 var(--ui)!important;color:var(--t3)!important}
[data-testid="stToast"]{background:var(--s2)!important;border:1px solid var(--line2)!important;border-radius:2px!important}
code{font-family:var(--data)!important;background:var(--s2)!important;color:var(--accent)!important;border-radius:2px!important}
div[data-testid="stForm"]{border:0!important;padding:0!important}

/* ── components (ak-*) ── */
.ak-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:14px 0 12px;margin:0 0 12px;border-bottom:1px solid var(--line)}
.ak-kicker{font:700 9px/1 var(--data);letter-spacing:1.8px;color:var(--t3);text-transform:uppercase}
.ak-title{font:800 24px/1.15 var(--ui);color:var(--t1);letter-spacing:-.02em;margin-top:6px}
.ak-sub{font:400 12px/1.5 var(--ui);color:var(--t2);margin-top:4px;max-width:880px}
.ak-head-r{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.ak-sec{display:flex;align-items:center;gap:10px;margin:22px 0 10px}
.ak-sec i{width:3px;height:13px;background:var(--accent);display:block}
.ak-sec b{font:700 11px/1 var(--data);letter-spacing:1.4px;color:var(--t1);text-transform:uppercase;white-space:nowrap}
.ak-sec em{font:400 11px var(--ui);color:var(--t3);font-style:normal;white-space:nowrap}
.ak-sec span{flex:1;height:1px;background:var(--line)}
.ak-note{font:400 11px/1.65 var(--ui);color:var(--t2);border-left:2px solid var(--line2);padding:8px 12px;background:var(--s1);margin:8px 0 12px}
.ak-note b{color:var(--t1)}
.ak-notice{border:1px solid var(--line2);border-left:3px solid var(--info);background:var(--s1);padding:11px 14px;margin:10px 0;font:400 12px/1.65 var(--ui);color:var(--t2)}
.ak-notice b{font:700 10px var(--data);letter-spacing:1px;text-transform:uppercase;margin-right:8px}
.ak-notice.warn{border-left-color:var(--accent)}.ak-notice.warn b{color:var(--accent)}
.ak-notice.error{border-left-color:var(--down)}.ak-notice.error b{color:var(--down)}
.ak-notice.ok{border-left-color:var(--up)}.ak-notice.ok b{color:var(--up)}
.ak-notice.info b{color:var(--info)}
.ak-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:8px;margin:6px 0 4px}
.ak-kpi{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:11px 13px 10px;min-width:0}
.ak-kpi.up{border-top-color:var(--up)}.ak-kpi.down{border-top-color:var(--down)}.ak-kpi.accent{border-top-color:var(--accent)}.ak-kpi.info{border-top-color:var(--info)}.ak-kpi.violet{border-top-color:var(--violet)}
.ak-kpi .l{font:600 9px/1.2 var(--data);letter-spacing:1px;color:var(--t3);text-transform:uppercase}
.ak-kpi .v{font:700 22px/1.1 var(--data);color:var(--t1);margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ak-kpi .s{font:400 10.5px/1.3 var(--ui);color:var(--t3);margin-top:5px}
.ak-badge{display:inline-flex;align-items:center;gap:5px;font:700 9px/1 var(--data);letter-spacing:.8px;padding:4px 7px;border:1px solid var(--line2);color:var(--t2);background:var(--s2);text-transform:uppercase;white-space:nowrap}
.ak-badge.up{color:var(--up);border-color:rgba(48,209,88,.4);background:rgba(48,209,88,.08)}
.ak-badge.down{color:var(--down);border-color:rgba(255,69,58,.4);background:rgba(255,69,58,.08)}
.ak-badge.accent{color:var(--accent);border-color:rgba(255,159,10,.4);background:var(--accent-dim)}
.ak-badge.info{color:var(--info);border-color:rgba(90,200,250,.4);background:rgba(90,200,250,.08)}
.ak-dot{width:6px;height:6px;border-radius:50%;display:inline-block;background:var(--t3)}
.ak-dot.up{background:var(--up);box-shadow:0 0 6px var(--up)}.ak-dot.down{background:var(--down)}
.ak-empty{border:1px dashed var(--line2);background:var(--s1);padding:34px 20px;text-align:center;margin:10px 0}
.ak-empty .t{font:700 12px var(--data);letter-spacing:1.2px;color:var(--t2);text-transform:uppercase}
.ak-empty .d{font:400 12px/1.6 var(--ui);color:var(--t3);margin-top:8px;max-width:520px;margin-left:auto;margin-right:auto}
.ak-card{background:var(--s1);border:1px solid var(--line);padding:0;min-width:0}
.ak-card .h{display:flex;justify-content:space-between;align-items:center;padding:0 12px;height:36px;border-bottom:1px solid var(--line);font:700 9px var(--data);letter-spacing:1.2px;color:var(--accent);text-transform:uppercase}
.ak-card .h em{font-style:normal;color:var(--t3);font-weight:500}
.ak-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);font:500 11px var(--data)}
.ak-row:last-child{border-bottom:0}.ak-row:hover{background:var(--s2)}
.ak-row .n{color:var(--t1);font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ak-row .p{color:var(--t2);text-align:right}
.ak-up{color:var(--up)}.ak-down{color:var(--down)}.ak-flat{color:var(--t3)}
.ak-lbl{font:600 9px var(--data);letter-spacing:1px;color:var(--t3);text-transform:uppercase;margin:0 0 4px}
.ak-chips{display:flex;gap:6px;flex-wrap:wrap}
.ak-chip{display:inline-flex;align-items:center;gap:6px;font:600 9px/1 var(--data);letter-spacing:.8px;padding:5px 8px;border:1px solid var(--line2);background:var(--s1);color:var(--t2);text-transform:uppercase}
.ak-chip.ok{color:var(--up);border-color:rgba(48,209,88,.35)}.ak-chip.bad{color:var(--down);border-color:rgba(255,69,58,.35)}
.ak-tile{background:var(--s1);border:1px solid var(--line);padding:9px 11px;min-width:0}
.ak-tile .a{font:600 9px var(--data);letter-spacing:.9px;color:var(--t3);text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ak-tile .b{font:700 15px var(--data);color:var(--t1);margin-top:5px}
.ak-tile .c{font:700 10px var(--data);margin-top:3px}
.ak-modcard{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:14px 15px 12px;min-height:150px;transition:border-color .12s,background .12s}
.ak-modcard:hover{border-top-color:var(--accent);background:var(--s2)}
.ak-modcard .k{font:700 9px var(--data);letter-spacing:1.4px;color:var(--accent)}
.ak-modcard .t{font:700 14px var(--ui);color:var(--t1);margin:7px 0 6px}
.ak-modcard .d{font:400 11.5px/1.55 var(--ui);color:var(--t2)}
.ak-modcard .m{display:flex;justify-content:space-between;margin-top:11px;padding-top:8px;border-top:1px solid var(--line);font:600 9px var(--data);letter-spacing:.9px;color:var(--t3);text-transform:uppercase}
@media(max-width:900px){.ak-title{font-size:20px}.ak-kpis{grid-template-columns:repeat(2,1fr)}}
"""


def inject() -> None:
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


# ───────────────────────── small components ─────────────────────────
def page_header(kicker: str, title: str, subtitle: str = "", right: str = "") -> None:
    sub = f'<div class="ak-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(f'<div class="ak-head"><div><div class="ak-kicker">{e(kicker)}</div><div class="ak-title">{e(title)}</div>{sub}</div>'
                f'<div class="ak-head-r">{right}</div></div>', unsafe_allow_html=True)


def section(title: str, sub: str = "") -> None:
    s = f"<em>{e(sub)}</em>" if sub else ""
    st.markdown(f'<div class="ak-sec"><i></i><b>{e(title)}</b>{s}<span></span></div>', unsafe_allow_html=True)


def note(html_text: str) -> None:
    st.markdown(f'<div class="ak-note">{html_text}</div>', unsafe_allow_html=True)


def notice(text: str, kind: str = "info", title: str | None = None) -> None:
    """kind: info | warn | error | ok. `text` may contain trusted HTML."""
    t = title or {"info": "Info", "warn": "Heads up", "error": "Problem", "ok": "Done"}.get(kind, "Info")
    st.markdown(f'<div class="ak-notice {kind}"><b>{e(t)}</b>{text}</div>', unsafe_allow_html=True)


def empty(title: str, text: str = "") -> None:
    st.markdown(f'<div class="ak-empty"><div class="t">{e(title)}</div><div class="d">{text}</div></div>', unsafe_allow_html=True)


def badge(text: str, tone: str = "") -> str:
    return f'<span class="ak-badge {tone}">{e(text)}</span>'


def delta(v, digits: int = 2, suffix: str = "%", arrow: bool = True) -> str:
    """Signed value with arrow + colour (never colour alone)."""
    f = _num(v)
    if f is None:
        return '<span class="ak-flat">—</span>'
    cls = "ak-up" if f > 0 else "ak-down" if f < 0 else "ak-flat"
    ar = ("▲ " if f > 0 else "▼ " if f < 0 else "") if arrow else ""
    return f'<span class="{cls}">{ar}{f:+.{digits}f}{suffix}</span>'


def kpis(items: list) -> None:
    """items: [dict(label, value, sub='', tone='')] — tone: up|down|accent|info|violet."""
    cards = "".join(f'<div class="ak-kpi {i.get("tone", "")}"><div class="l">{e(i["label"])}</div><div class="v">{i["value"]}</div>'
                    f'<div class="s">{i.get("sub", "")}</div></div>' for i in items)
    st.markdown(f'<div class="ak-kpis">{cards}</div>', unsafe_allow_html=True)


def status_chips(items: list) -> str:
    """items: [(label, ok: bool|None, tip)] -> HTML string (use in page_header right=)."""
    out = []
    for label, ok, tip in items:
        cls = "ok" if ok else "bad" if ok is False else ""
        dot = "up" if ok else "down" if ok is False else ""
        out.append(f'<span class="ak-chip {cls}" title="{e(tip)}"><span class="ak-dot {dot}"></span>{e(label)}</span>')
    return "".join(out)


def spark(values, w: int = 70, h: int = 20, color: str | None = None) -> str:
    vals = [float(v) for v in (values or []) if _num(v) is not None]
    if len(vals) < 2:
        return ""
    color = color or (UP if vals[-1] >= vals[0] else DOWN)
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i / (len(vals) - 1) * w:.1f},{h - 2 - ((v - lo) / rng) * (h - 4):.1f}" for i, v in enumerate(vals))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def nav(items: list, active: str, key: str = "topnav"):
    """Top navigation. items: [(label, target)]. Returns the clicked target (or None)."""
    clicked = None
    with st.container(key=key):
        cols = st.columns(len(items), gap="small")
        for col, (label, target) in zip(cols, items):
            with col:
                if st.button(label, key=f"{key}_{target}", use_container_width=True,
                             type="primary" if target == active else "secondary"):
                    clicked = target
    return clicked


def segmented(label: str, options: list, key: str, index: int = 0, help: str | None = None):
    """Pill-style single choice (a horizontal st.radio wrapped so the CSS above can restyle it)."""
    if label:
        st.markdown(f'<div class="ak-lbl">{e(label)}</div>', unsafe_allow_html=True)
    with st.container(key=f"seg-{key}"):
        return st.radio(label or key, options, index=index, horizontal=True, key=key,
                        label_visibility="collapsed", help=help)


# ───────────────────────── terminal table ─────────────────────────
def C(key: str, label: str, kind: str = "text", **kw) -> dict:
    """Column spec helper. kinds: rank name text num pct dist rsi bar tag chips html."""
    return {"key": key, "label": label, "kind": kind, **kw}


_TABLE_CSS = r"""
*{box-sizing:border-box;margin:0}html,body{background:#050505;color:#ECECEC;font-family:'Inter',system-ui,sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-height:%MAXH%px;overflow:auto;border:1px solid #1F1F1F;background:#0A0A0A}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:11.5px}
th,td{padding:0 11px;height:%ROWH%px;white-space:nowrap;border-bottom:1px solid #161616}
thead th{position:sticky;background:#0D0D0D;font:700 9px 'JetBrains Mono',monospace;letter-spacing:1px;color:#6B6B6B;text-transform:uppercase;border-bottom:1px solid #2C2C2C;z-index:3;cursor:pointer;user-select:none}
thead tr.g th{top:0;height:24px;text-align:center;color:#FF9F0A;border-right:1px solid #1F1F1F;cursor:default;font-size:8.5px;letter-spacing:1.3px}
thead tr.h th{top:%HTOP%px}
thead th:hover{color:#ECECEC}
thead th.asc::after{content:' ▲';color:#FF9F0A}thead th.desc::after{content:' ▼';color:#FF9F0A}
tbody tr:nth-child(even){background:#0C0C0C}tbody tr:hover{background:#151515}
td{font:500 11.5px 'JetBrains Mono',monospace;color:#A3A3A3}
td.l{text-align:left}td.r{text-align:right}td.c{text-align:center}
td.rk{color:#6B6B6B;text-align:center;width:34px}
td.nm{font:700 12px 'Inter',sans-serif;color:#ECECEC}td.nm small{display:block;font:400 9.5px 'JetBrains Mono',monospace;color:#6B6B6B;margin-top:1px}
.up{color:#30D158}.dn{color:#FF453A}.fl{color:#6B6B6B}.am{color:#FF9F0A}
.cp{display:inline-block;min-width:30px;text-align:center;padding:4px 7px;margin-right:4px;font:700 10px 'JetBrains Mono',monospace;border:1px solid}
.cp.g{color:#30D158;background:rgba(48,209,88,.13);border-color:rgba(48,209,88,.38)}
.cp.r{color:#FF453A;background:rgba(255,69,58,.12);border-color:rgba(255,69,58,.38)}
.cp.n{color:#6B6B6B;background:#101010;border-color:#2C2C2C}
.tg{display:inline-block;padding:4px 8px;font:700 9px 'JetBrains Mono',monospace;letter-spacing:.8px;border:1px solid;text-transform:uppercase}
.bar{position:relative;height:16px;min-width:96px;background:#141414;border:1px solid #1F1F1F}
.bar i{position:absolute;left:0;top:0;bottom:0;display:block;opacity:.55}.bar b{position:relative;display:block;text-align:center;font:700 10px/16px 'JetBrains Mono',monospace;color:#ECECEC}
.rs{display:inline-block;min-width:44px;text-align:center;padding:3px 6px;font:700 11px 'JetBrains Mono',monospace}
.why{white-space:normal;line-height:1.35;font:400 10.5px 'Inter',sans-serif;color:#A3A3A3;min-width:260px;padding:6px 11px}
"""

_TABLE_JS = r"""
(function(){
var tb=document.getElementById('tb');var ths=[].slice.call(document.querySelectorAll('#hd th'));var cur=-1,dir=1;
function v(td){var x=td.getAttribute('data-v');if(x===null||x==='')return null;var n=Number(x);return isNaN(n)?String(x).toLowerCase():n;}
function renum(){var r=tb.rows;for(var i=0;i<r.length;i++){var c=r[i].querySelector('.rk');if(c)c.textContent=i+1;}}
function sortBy(i,d){var rows=[].slice.call(tb.rows);rows.sort(function(a,b){var x=v(a.cells[i]),y=v(b.cells[i]);
 if(x===null&&y===null)return 0;if(x===null)return 1;if(y===null)return -1;
 if(typeof x==='number'&&typeof y==='number')return (x-y)*d;return String(x).localeCompare(String(y))*d;});
 rows.forEach(function(r){tb.appendChild(r);});renum();ths.forEach(function(t,j){t.classList.remove('asc','desc');if(j===i)t.classList.add(d>0?'asc':'desc');});}
ths.forEach(function(th,i){if(th.getAttribute('data-s')==='0')return;th.addEventListener('click',function(){
 if(cur===i){dir=-dir;}else{dir=(th.getAttribute('data-d')==='asc')?1:-1;}cur=i;sortBy(i,dir);});});
var init=%INIT%;if(init){cur=init[0];dir=init[1];sortBy(cur,dir);}else{renum();}
})();
"""


def _tone_of(v: float, up_good: bool = True) -> str:
    if v > 0:
        return "up" if up_good else "dn"
    if v < 0:
        return "dn" if up_good else "up"
    return "fl"


def _cell(col: dict, row: dict):
    """-> (td_class, inner_html, data_v)"""
    kind = col.get("kind", "text")
    v = row.get(col["key"])
    if kind == "rank":
        return "rk", "", ""
    if kind == "html":
        return col.get("align", "l"), str(v if v is not None else ""), ""
    if kind == "name":
        sub = row.get(col.get("sub", ""), "")
        s = f"<small>{e(sub)}</small>" if sub not in (None, "") and not _isnull(sub) else ""
        return "nm l", f"{e(v)}{s}", str(v or "").lower()
    if kind == "text":
        if v is None or _isnull(v) or v == "":
            return "l", '<span class="fl">—</span>', ""
        cls = "why l" if col.get("wrap") else "l"
        return cls, e(v), str(v).lower()
    if kind == "tag":
        if v in (None, "") or _isnull(v):
            return "l", '<span class="fl">—</span>', ""
        c = (col.get("colors") or {}).get(str(v), "#A3A3A3")
        return "l", f'<span class="tg" style="color:{c};border-color:{c}66;background:{c}14">{e(v)}</span>', str(v).lower()
    if kind == "chips":
        chips = v if isinstance(v, list) else []
        if not chips:
            return "l", '<span class="fl">—</span>', ""
        out = []
        for ch in chips:
            s = ch.get("s")
            cls = "g" if s is True else "r" if s is False else "n"
            out.append(f'<span class="cp {cls}" title="{e(ch.get("t", ""))}">{e(ch.get("l", ""))}</span>')
        score = sum(1 for ch in chips if ch.get("s") is True)
        return "l", "".join(out), str(score)
    f = _num(v)
    if f is None:
        return "r", '<span class="fl">—</span>', ""
    if kind == "num":
        return "r", e(col.get("fmt", "{:,.2f}").format(f)), repr(f)
    if kind in ("pct", "dist"):
        fmt = col.get("fmt", "{:+.2f}%" if kind == "pct" else "{:+.1f}%")
        tone = _tone_of(f, col.get("up_good", True))
        warn = col.get("warn")
        if kind == "dist" and warn is not None and f >= warn:
            tone = "am"
        return "r", f'<span class="{tone}">{e(fmt.format(f))}</span>', repr(f)
    if kind == "rsi":
        tone = "am" if f >= 70 else "up" if f >= 55 else "dn" if f < 45 else "fl"
        pct = max(0.0, min(100.0, f))
        return "r", (f'<span class="rs {tone}" style="background:linear-gradient(90deg,rgba(255,159,10,.10) {pct:.0f}%,transparent {pct:.0f}%)">'
                     f'{f:.0f}</span>'), repr(f)
    if kind == "bar":
        mx = col.get("max", 100)
        pct = max(0.0, min(100.0, f / mx * 100))
        color = UP if pct >= 65 else ACCENT if pct >= 45 else DOWN
        return "c", f'<div class="bar"><i style="width:{pct:.0f}%;background:{color}"></i><b>{f:.0f}</b></div>', repr(f)
    return "r", e(f), repr(f)


def table(columns: list, rows: list, *, height: int = 560, row_h: int = 31, sort: str | None = None, desc: bool = True,
          empty_text: str = "Nothing to show yet.") -> None:
    """Sortable, sticky-header terminal table rendered in an iframe. `rows` = list of dicts."""
    if not rows:
        empty("No data", empty_text)
        return
    groups = []
    for c in columns:
        g = c.get("group", "")
        if groups and groups[-1][0] == g:
            groups[-1][1] += 1
        else:
            groups.append([g, 1])
    has_groups = any(g for g, _ in groups)
    head_top = 24 if has_groups else 0
    gh = ""
    if has_groups:
        gh = '<tr class="g">' + "".join(f'<th colspan="{n}">{e(g)}</th>' for g, n in groups) + "</tr>"
    ths = []
    for c in columns:
        kind = c.get("kind", "text")
        al = "left" if kind in ("name", "text", "tag", "chips") else "center" if kind in ("rank", "bar") else "right"
        d = "asc" if kind in ("name", "text", "tag") else "desc"
        s = ' data-s="0"' if kind in ("html",) and not c.get("sortable") else ""
        w = f' style="text-align:{al};min-width:{c["width"]}px"' if c.get("width") else f' style="text-align:{al}"'
        tip = f' title="{e(c["tip"])}"' if c.get("tip") else ""
        ths.append(f'<th data-d="{d}"{s}{w}{tip}>{e(c["label"])}</th>')
    body = []
    for r in rows:
        tds = []
        for c in columns:
            cls, inner, dv = _cell(c, r)
            tds.append(f'<td class="{cls}" data-v="{e(dv)}">{inner}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    init = "null"
    if sort:
        idx = next((i for i, c in enumerate(columns) if c["key"] == sort), None)
        if idx is not None:
            init = f"[{idx},{-1 if desc else 1}]"
    css = _TABLE_CSS.replace("%MAXH%", str(height)).replace("%ROWH%", str(row_h)).replace("%HTOP%", str(head_top))
    js = _TABLE_JS.replace("%INIT%", init)
    doc = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap">'
           f'<style>{css}</style></head><body><div class="wrap"><table><thead>{gh}<tr class="h" id="hd">{"".join(ths)}</tr></thead>'
           f'<tbody id="tb">{"".join(body)}</tbody></table></div><script>{js}</script></body></html>')
    h = min(height, head_top + 36 + row_h * len(rows) + 6) + 4
    components.html(doc, height=h, scrolling=False)
