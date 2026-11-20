"""
Arka Trades — quant_ui.py: shared look for the Regime and Backtest pages (app design system: Inter, sentence case,
soft cards) and dependency-free SVG charts that render the same on phone and desktop.
"""
from __future__ import annotations

import html
import math

import numpy as np
import pandas as pd
import streamlit as st

SANS = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"
TX, TX2, TX3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
GREEN, RED, AMBER, BLUE, PURPLE, CYAN, YELLOW = "#30D158", "#FF453A", "#FF9F0A", "#4FA3FF", "#BF5AF2", "#5AC8FA", "#FFD60A"
STATE_COLOR = {"Confirmed uptrend": GREEN, "Under pressure": YELLOW, "Rally attempt": BLUE, "Correction": RED}
TONE_COLOR = {"good": GREEN, "warn": YELLOW, "bad": RED, "info": BLUE}


def e(x) -> str:
    return html.escape(str(x))


def phone() -> bool:
    try:
        return bool(st.session_state.get("_ak_phone"))
    except Exception:
        return False


def css():
    st.markdown(f"""<style>
    .q-root,.q-card,.q-kpis,.q-hero{{font-family:{SANS};font-variant-numeric:tabular-nums;color:var(--tx,{TX})}}
    .q-head{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin:4px 2px 10px;font-family:{SANS}}}
    .q-head b{{font-size:21px;font-weight:650;letter-spacing:-.2px;color:var(--tx,{TX})}} .q-head span{{font-size:12.5px;color:var(--tx3,{TX3})}}
    .q-card{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:16px;padding:14px 16px;margin:0 0 12px}}
    .q-ch{{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:14px;font-weight:600;margin-bottom:10px}}
    .q-ch em{{font-style:normal;font-size:12px;font-weight:500;color:var(--tx3,{TX3})}}
    .q-sub{{font-size:12.5px;line-height:1.55;color:var(--tx2,{TX2})}}
    .q-hero{{border-radius:18px;padding:18px 20px;margin:0 0 12px;border:1px solid color-mix(in srgb,var(--c) 35%,#1E1E21);
            background:linear-gradient(135deg,color-mix(in srgb,var(--c) 14%,#0E0E10),#0E0E10 60%)}}
    .q-hero .st{{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--c)}}
    .q-hero .st i{{width:9px;height:9px;border-radius:50%;background:var(--c);box-shadow:0 0 0 4px color-mix(in srgb,var(--c) 22%,transparent)}}
    .q-hero h2{{font-size:28px;font-weight:700;letter-spacing:-.4px;margin:6px 0 4px;color:#fff;font-family:{SANS};padding:0}}
    .q-hero p{{margin:0;font-size:13.5px;line-height:1.55;color:var(--tx2,{TX2})}}
    .q-hgrid{{display:grid;grid-template-columns:1.4fr 1fr;gap:18px;align-items:center}}
    @media (max-width:760px){{.q-hgrid{{grid-template-columns:1fr}} .q-hero h2{{font-size:24px}}}}
    .q-gauge{{position:relative;height:10px;border-radius:10px;background:linear-gradient(90deg,{RED} 0%,{RED} 40%,{YELLOW} 40%,{YELLOW} 65%,{GREEN} 65%,{GREEN} 100%);opacity:.85}}
    .q-gauge i{{position:absolute;top:-5px;width:6px;height:20px;border-radius:3px;background:#fff;transform:translateX(-3px);box-shadow:0 0 0 3px rgba(0,0,0,.5)}}
    .q-gl{{display:flex;justify-content:space-between;font-size:11.5px;color:var(--tx3,{TX3});margin-top:6px}}
    .q-big{{font-size:34px;font-weight:700;color:#fff;letter-spacing:-.5px}} .q-big small{{font-size:14px;color:var(--tx3,{TX3});font-weight:500}}
    .q-kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:0 0 12px}}
    .q-kp{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:12px;padding:11px 13px;min-width:0}}
    .q-kp .l{{font-size:12px;color:var(--tx3,{TX3})}} .q-kp .v{{font-size:19px;font-weight:650;margin-top:3px;white-space:nowrap}}
    .q-kp .s{{font-size:11.5px;color:var(--tx3,{TX3});margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .q-rs{{display:flex;gap:10px;align-items:flex-start;padding:7px 0;font-size:13px;line-height:1.5;color:var(--tx2,{TX2});border-bottom:1px solid #17171B}}
    .q-rs:last-child{{border-bottom:0}} .q-rs i{{flex:0 0 auto;width:8px;height:8px;border-radius:50%;margin-top:6px;background:var(--c)}}
    .q-comp{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}}
    .q-cp{{background:#121215;border:1px solid #1E1E23;border-radius:12px;padding:10px 12px}}
    .q-cp .n{{display:flex;justify-content:space-between;font-size:12.5px;font-weight:600}} .q-cp .n em{{font-style:normal;font-weight:500;color:var(--tx3,{TX3})}}
    .q-bar{{height:6px;border-radius:6px;background:#202024;margin:8px 0 6px;overflow:hidden}} .q-bar i{{display:block;height:100%;border-radius:6px}}
    .q-cp .d{{font-size:11.5px;line-height:1.45;color:var(--tx3,{TX3})}}
    table.q-t{{width:100%;border-collapse:collapse;font-size:12.5px;font-family:{SANS};font-variant-numeric:tabular-nums}}
    .q-t th{{text-align:right;font-weight:550;color:var(--tx3,{TX3});padding:7px 8px;border-bottom:1px solid #1E1E22;white-space:nowrap}}
    .q-t td{{text-align:right;padding:8px;border-bottom:1px solid #17171B;color:#DADADF;white-space:nowrap}}
    .q-t th:first-child,.q-t td:first-child{{text-align:left}} .q-t tr:last-child td{{border-bottom:0}}
    .q-tw{{overflow-x:auto}}
    .q-pill{{--c:#8A8A90;display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600;
            color:var(--c);background:color-mix(in srgb,var(--c) 13%,transparent);white-space:nowrap}}
    .q-pill i{{width:6px;height:6px;border-radius:50%;background:var(--c)}}
    .q-leg{{display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:var(--tx3,{TX3});margin-top:6px}}
    .q-leg span{{display:inline-flex;align-items:center;gap:6px}} .q-leg i{{width:14px;height:3px;border-radius:2px;background:var(--c)}}
    .q-leg i.blk{{width:10px;height:10px;border-radius:3px;opacity:.6}}
    .q-verdict{{border-radius:16px;padding:16px 18px;margin:4px 0 12px;border:1px solid color-mix(in srgb,var(--c) 40%,#1E1E21);
               background:linear-gradient(135deg,color-mix(in srgb,var(--c) 13%,#0E0E10),#0E0E10 65%);font-family:{SANS}}}
    .q-verdict b{{display:block;font-size:19px;font-weight:650;color:#fff}} .q-verdict p{{margin:6px 0 0;font-size:13.5px;line-height:1.55;color:var(--tx2,{TX2})}}
    .q-note{{font-size:12px;line-height:1.6;color:var(--tx3,{TX3});font-family:{SANS};margin:6px 2px}}
    .q-hm td{{text-align:center !important;font-weight:600}}
    .q-two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} @media (max-width:760px){{.q-two{{grid-template-columns:1fr}}}}
    .q-three{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}} @media (max-width:980px){{.q-three{{grid-template-columns:1fr}}}}
    div[class*="st-key-q_ctrl"]{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:16px;padding:12px 14px 4px;margin-bottom:12px}}
    </style>""", unsafe_allow_html=True)


def kpi(label, value, sub="", color=None) -> str:
    st_ = f' style="color:{color}"' if color else ""
    return f'<div class="q-kp"><div class="l">{e(label)}</div><div class="v"{st_}>{value}</div><div class="s">{sub}</div></div>'


def pill(text, color) -> str:
    return f'<span class="q-pill" style="--c:{color}"><i></i>{e(text)}</span>'


def table(df: pd.DataFrame, fmt: dict | None = None, color: dict | None = None, cls="q-t") -> str:
    """Small HTML table. fmt: col -> format callable; color: col -> callable(value) -> css colour."""
    fmt, color = fmt or {}, color or {}
    head = "".join(f"<th>{e(c)}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            txt = fmt[c](v) if c in fmt else (e(v) if not isinstance(v, float) else (f"{v:,.2f}" if np.isfinite(v) else "—"))
            col = color[c](v) if c in color else None
            cells.append(f'<td{f" style=color:{col}" if col else ""}>{txt}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="q-tw"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def signed_color(v):
    try:
        return GREEN if v > 0 else RED if v < 0 else None
    except Exception:
        return None


# ═════════════════════════ SVG charts ═════════════════════════
def _nice_ticks(lo, hi, n=4):
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    start = math.ceil(lo / step) * step
    return [start + i * step for i in range(int((hi - start) / step) + 1)]


def _fmt_tick(v):
    a = abs(v)
    if a >= 1e5:
        return f"{v / 1e5:,.1f}L"
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:.0f}"
    return f"{v:.1f}".rstrip("0").rstrip(".") if a >= 1 or v == 0 else f"{v:.2f}"


def line_chart(dates, series: list, height=240, bands=None, hlines=None, y_suffix="", log=False, area_first=False, w=None) -> str:
    """series: [(values, colour, width, label)]. bands: [(i0, i1, colour)] shaded columns. hlines: [(y, colour, label)]."""
    n = len(dates)
    w = w or (430 if phone() else 1000)            # phones: a narrower canvas keeps axis text readable
    if n < 2:
        return '<div class="q-note">Not enough data to draw.</div>'
    pad_l, pad_r, pad_t, pad_b = 8, 52, 10, 22
    W, H = w, height
    vals = np.concatenate([np.asarray(s[0], dtype=float) for s in series] + ([np.array([h[0] for h in hlines], dtype=float)] if hlines else []))
    vals = vals[np.isfinite(vals)]
    if not len(vals):
        return '<div class="q-note">No data.</div>'
    lo, hi = float(vals.min()), float(vals.max())
    if log and lo > 0:
        tf = np.log
        lo_t, hi_t = math.log(lo), math.log(hi)
    else:
        tf = lambda x: x
        lo_t, hi_t = lo, hi
    span = (hi_t - lo_t) or 1.0
    lo_t, hi_t = lo_t - span * 0.06, hi_t + span * 0.06
    X = lambda i: pad_l + i / (n - 1) * (W - pad_l - pad_r)
    Y = lambda v: pad_t + (1 - (tf(v) - lo_t) / (hi_t - lo_t)) * (H - pad_t - pad_b)
    parts = []
    for i0, i1, col in bands or []:
        x0, x1 = X(max(0, i0 - 0.5)), X(min(n - 1, i1 + 0.5))
        parts.append(f'<rect x="{x0:.1f}" y="{pad_t}" width="{max(0.6, x1 - x0):.1f}" height="{H - pad_t - pad_b}" fill="{col}" opacity=".13"/>')
    for t in _nice_ticks(lo if not log else lo, hi, 4):
        if log and t <= 0:
            continue
        y = Y(t)
        if pad_t - 1 <= y <= H - pad_b + 1:
            parts.append(f'<line x1="{pad_l}" x2="{W - pad_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="#1E1E22" stroke-width="1"/>'
                         f'<text x="{W - pad_r + 6}" y="{y + 4:.1f}" fill="#6B6B70" font-size="11" font-family="Inter,sans-serif">{_fmt_tick(t)}{y_suffix}</text>')
    d = pd.DatetimeIndex(dates)
    yrs = d.year
    step_years = max(1, int(round((yrs[-1] - yrs[0] + 1) / 8)))
    if yrs[-1] - yrs[0] >= 2:
        for k in range(1, n):
            if yrs[k] != yrs[k - 1] and (yrs[k] - yrs[0]) % step_years == 0:
                x = X(k)
                parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{H - pad_b}" stroke="#18181B" stroke-width="1"/>'
                             f'<text x="{x + 3:.1f}" y="{H - 6}" fill="#6B6B70" font-size="11" font-family="Inter,sans-serif">{yrs[k]}</text>')
    else:
        months = d.month
        for k in range(1, n):
            if months[k] != months[k - 1] and (months[k] % (1 if n < 90 else 2 if n < 300 else 3) == 0):
                x = X(k)
                parts.append(f'<text x="{x + 3:.1f}" y="{H - 6}" fill="#6B6B70" font-size="11" font-family="Inter,sans-serif">{d[k]:%b}</text>'
                             f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{H - pad_b}" stroke="#18181B" stroke-width="1"/>')
    for y0, col, lab in hlines or []:
        y = Y(y0)
        parts.append(f'<line x1="{pad_l}" x2="{W - pad_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="{col}" stroke-dasharray="4 4" stroke-width="1" opacity=".7"/>')
    for k, (v, col, wd, lab) in enumerate(series):
        v = np.asarray(v, dtype=float)
        pts, segs = [], []
        for i in range(n):
            if np.isfinite(v[i]) and (not log or v[i] > 0):
                pts.append(f"{X(i):.1f},{Y(v[i]):.1f}")
            elif pts:
                segs.append(pts)
                pts = []
        if pts:
            segs.append(pts)
        for sg in segs:
            if k == 0 and area_first and len(sg) > 1:
                base = H - pad_b
                parts.append(f'<polygon points="{sg[0].split(",")[0]},{base} {" ".join(sg)} {sg[-1].split(",")[0]},{base}" fill="{col}" opacity=".10"/>')
            parts.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{col}" stroke-width="{wd}" stroke-linejoin="round" stroke-linecap="round"/>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="none" style="display:block;height:{H}px">{"".join(parts)}</svg>'


def bar_chart(dates, values, colors, height=120, w=None, zero=True) -> str:
    w = w or (430 if phone() else 1000)
    n = len(values)
    if n < 2:
        return ""
    v = np.asarray(values, dtype=float)
    lo, hi = min(0.0, np.nanmin(v)), max(0.0, np.nanmax(v))
    span = (hi - lo) or 1
    pad_r, pad_t, pad_b = 52, 6, 6
    X = lambda i: i / n * (w - pad_r)
    Y = lambda x: pad_t + (1 - (x - lo) / span) * (height - pad_t - pad_b)
    bw = max(0.6, (w - pad_r) / n * 0.8)
    parts = [f'<line x1="0" x2="{w - pad_r}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#2A2A2F"/>']
    for i in range(n):
        if not np.isfinite(v[i]) or v[i] == 0:
            continue
        y0, y1 = sorted((Y(0), Y(v[i])))
        parts.append(f'<rect x="{X(i):.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(0.6, y1 - y0):.1f}" fill="{colors[i] if isinstance(colors, (list, np.ndarray)) else colors}" opacity=".85"/>')
    for t, lab in ((hi, _fmt_tick(hi)), (lo, _fmt_tick(lo))):
        parts.append(f'<text x="{w - pad_r + 6}" y="{Y(t) + 4:.1f}" fill="#6B6B70" font-size="11" font-family="Inter,sans-serif">{lab}</text>')
    return f'<svg viewBox="0 0 {w} {height}" width="100%" preserveAspectRatio="none" style="display:block;height:{height}px">{"".join(parts)}</svg>'


def histogram(values, bins=24, color=BLUE, height=130, w=None, marks=None, x_suffix="") -> str:
    w = w or (400 if phone() else 600)
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 5:
        return ""
    cnt, edges = np.histogram(v, bins=bins)
    mx = cnt.max() or 1
    pad_b = 20
    bw = w / bins
    parts = []
    for i, c in enumerate(cnt):
        hgt = c / mx * (height - pad_b - 6)
        parts.append(f'<rect x="{i * bw + 1:.1f}" y="{height - pad_b - hgt:.1f}" width="{bw - 2:.1f}" height="{hgt:.1f}" rx="2" fill="{color}" opacity=".75"/>')
    X = lambda x: (x - edges[0]) / (edges[-1] - edges[0] or 1) * w
    for x0, col, lab in marks or []:
        x = X(x0)
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height - pad_b}" stroke="{col}" stroke-width="1.5" stroke-dasharray="3 3"/>'
                     f'<text x="{min(w - 60, x + 4):.1f}" y="12" fill="{col}" font-size="11" font-family="Inter,sans-serif">{e(lab)}</text>')
    for x0 in (edges[0], (edges[0] + edges[-1]) / 2, edges[-1]):
        parts.append(f'<text x="{min(w - 40, max(0, X(x0) - 10)):.1f}" y="{height - 4}" fill="#6B6B70" font-size="11" font-family="Inter,sans-serif">{x0:.0f}{x_suffix}</text>')
    return f'<svg viewBox="0 0 {w} {height}" width="100%" preserveAspectRatio="none" style="display:block;height:{height}px">{"".join(parts)}</svg>'


def legend(items) -> str:
    return '<div class="q-leg">' + "".join(f'<span><i class="{"blk" if blk else ""}" style="--c:{c};background:{c}"></i>{e(t)}</span>' for t, c, blk in items) + "</div>"


def state_bands(states: np.ndarray) -> list:
    out, s0 = [], 0
    for i in range(1, len(states) + 1):
        if i == len(states) or states[i] != states[s0]:
            out.append((s0, i - 1, STATE_COLOR.get(states[s0], "#444")))
            s0 = i
    return out


def choice(label, options, key, fmt=None, pills=False, where=None):
    """Segmented control / pills on newer Streamlit, else a select box. Never returns None."""
    where = where or st
    fmt = fmt or (lambda o: o)
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]

    def _keep():
        if st.session_state.get(key) not in options:
            st.session_state[key] = options[0]
    fn = getattr(where, "pills" if pills else "segmented_control", None) or getattr(where, "segmented_control", None)
    if fn is not None:
        try:
            v = fn(label, options, key=key, format_func=fmt, label_visibility="collapsed", on_change=_keep)
            return v if v in options else options[0]
        except TypeError:
            pass
    return where.selectbox(label, options, key=key, format_func=fmt, label_visibility="collapsed")
