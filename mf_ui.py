"""
mf_ui.py — Arka Money Flow UI kit.

* html_table(): dense, sticky-header terminal tables with heat-tinted % cells, EMA chips
  (green = price above that EMA, red = below), stage pills, score bars and sparklines.
* kpi_strip(), pick_card(): summary cards for the rotation radar.
* plotly charts (RRG with trails, sector-vs-benchmark lines, ratio lines, small multiples).
  If plotly is missing the chart functions return None and the caller falls back to st.line_chart.

Pure presentation: no network, no analytics (those live in sector_engine / index_engine).
"""
from __future__ import annotations

import html
import math

import numpy as np
import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:  # pragma: no cover
    go = None
    make_subplots = None

GREEN, RED, AMBER, CYAN, PURPLE, YELLOW = "#30D158", "#FF453A", "#FF9F0A", "#5AC8FA", "#BF5AF2", "#FFD60A"
IVORY, T2, T3, BORDER = "#E8E8E8", "#8A8A8A", "#5A5A5A", "#262626"
BENCH_COLORS = {"NIFTY 50": "#D0D0D0", "MIDCAP 100": CYAN, "SMALLCAP 100": PURPLE}
STAGE_COLORS = {"ROTATING IN": GREEN, "LEADING": "#8AD66B", "EXTENDED": YELLOW, "IMPROVING": CYAN,
                "FADING": AMBER, "WEAK": RED, "—": T3}
QUAD_COLORS = {"LEADING": "48,209,88", "FADING": "255,159,10", "WEAK": "255,69,58", "IMPROVING": "90,200,250"}
EXTENDED_PCT = 6.0

CSS = """
<style>
.mf-tw{overflow:auto;border:1px solid #1f1f1f;background:#070707;margin:6px 0 12px;max-width:100%;}
.mf-t{width:100%;border-collapse:separate;border-spacing:0;font:12px 'Inter',sans-serif;color:#e0e0e0;}
.mf-t th{position:sticky;top:0;z-index:2;background:#0c0c0c;color:#777;font:700 9px 'JetBrains Mono',monospace;letter-spacing:.9px;text-transform:uppercase;text-align:left;padding:10px;border-bottom:1px solid #252525;white-space:nowrap;}
.mf-t th.r,.mf-t td.r{text-align:right;}
.mf-t th.c,.mf-t td.c{text-align:center;}
.mf-t td{padding:9px 10px;border-bottom:1px solid #141414;vertical-align:middle;white-space:nowrap;}
.mf-t tbody tr:hover td{background:rgba(48,209,88,.055);}
.mf-t td.rk{color:#555;font:600 11px 'JetBrains Mono',monospace;width:26px;}
.mf-t td.num{font:11px 'JetBrains Mono',monospace;text-align:right;color:#cfcfcf;}
.mf-t td.sec b{color:#f1f1f1;font-weight:700;}
.mf-t td.sec .sub{display:block;color:#5f5f5f;font-size:9px;margin-top:2px;}
.mf-t td.muted{color:#8a8a8a;font-size:10px;max-width:200px;overflow:hidden;text-overflow:ellipsis;}
.mf-t td.wrap{white-space:normal;min-width:150px;}
.mf-pct{display:inline-block;min-width:60px;text-align:right;padding:2px 7px;border-radius:4px;font:600 11px 'JetBrains Mono',monospace;}
.mf-na{color:#444;font:11px 'JetBrains Mono',monospace;}
.mf-chip{display:inline-block;min-width:32px;text-align:center;padding:3px 7px;margin-right:4px;border-radius:6px;font:600 11px 'JetBrains Mono',monospace;border:1px solid transparent;}
.mf-chip.up{background:rgba(48,209,88,.13);color:#30D158;border-color:rgba(48,209,88,.32);}
.mf-chip.dn{background:rgba(255,69,58,.13);color:#FF453A;border-color:rgba(255,69,58,.32);}
.mf-chip.na{background:rgba(255,255,255,.04);color:#555;border-color:#262626;}
.mf-stage{display:inline-block;padding:3px 9px;border-radius:999px;font:800 9px 'JetBrains Mono',monospace;letter-spacing:.8px;border:1px solid var(--sc);color:var(--sc);background:rgba(255,255,255,.02);}
.mf-stage.in{background:rgba(48,209,88,.14);box-shadow:0 0 12px -3px var(--sc);}
.mf-sc{display:inline-flex;align-items:center;gap:7px;font:700 11px 'JetBrains Mono',monospace;color:#ddd;}
.mf-sc .b{display:inline-block;width:56px;height:5px;background:#1a1a1a;}
.mf-sc .b i{display:block;height:100%;}
.mf-tag{display:inline-block;margin:1px 4px 1px 0;padding:1px 6px;border-radius:4px;font:600 9px 'JetBrains Mono',monospace;background:rgba(90,200,250,.10);color:#5AC8FA;border:1px solid rgba(90,200,250,.25);}
.mf-tag.bad{background:rgba(255,69,58,.10);color:#FF6B63;border-color:rgba(255,69,58,.25);}
.mf-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:8px 0 12px;}
.mf-k{border:1px solid #222;border-top:2px solid var(--a);background:#090909;padding:10px 12px;}
.mf-k .l{font:700 8px 'JetBrains Mono',monospace;letter-spacing:1px;color:#666;text-transform:uppercase;}
.mf-k .v{font:700 20px 'JetBrains Mono',monospace;color:#eee;margin-top:5px;}
.mf-k .s{font:10px 'Inter',sans-serif;color:#7a7a7a;margin-top:3px;line-height:1.5;}
.mf-picks{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:10px;margin:8px 0 14px;}
.mf-pick{border:1px solid #222;border-top:2px solid var(--sc);background:#090909;padding:13px 14px;}
.mf-pick .h{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
.mf-pick .n{font:800 15px 'Inter',sans-serif;color:#f1f1f1;}
.mf-pick ul{margin:9px 0 0 16px;padding:0;color:#b5b5b5;font-size:11px;line-height:1.65;}
.mf-pick ul.w{color:#c9a24a;}
.mf-pick .e{margin-top:9px;padding:7px 9px;border-left:2px solid #3a3a3a;background:rgba(255,255,255,.02);font-size:10px;color:#8f8f8f;line-height:1.6;}
.mf-pick .l{margin-top:8px;font:10px 'JetBrains Mono',monospace;color:#6f6f6f;}
.mf-pick .l b{color:#FF9F0A;}

.mf-board{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0 14px;}
@media(max-width:900px){.mf-board{grid-template-columns:1fr;}}
.mf-lane{border:1px solid #1f1f1f;border-top:2px solid var(--c);background:#080808;padding:11px 13px;min-height:112px;}
.mf-lh{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;}
.mf-lh b{font:800 11px 'JetBrains Mono',monospace;letter-spacing:1px;color:var(--c);}
.mf-lh span{font:10px 'Inter',sans-serif;color:#7d7d7d;flex:1;}
.mf-lh i{font:700 11px 'JetBrains Mono',monospace;font-style:normal;color:#aaa;background:rgba(255,255,255,.06);padding:1px 9px;border-radius:10px;}
.mf-lb{display:flex;flex-wrap:wrap;gap:7px;}
.mf-sp{border:1px solid #242424;border-left:3px solid var(--pc);background:rgba(255,255,255,.02);padding:7px 10px;border-radius:5px;min-width:138px;}
.mf-sp .n{display:block;font:700 12px 'Inter',sans-serif;color:#eee;}
.mf-sp .m{display:block;font:10px 'JetBrains Mono',monospace;color:#8a8a8a;margin-top:3px;}
.mf-sp .t{display:inline-block;margin:5px 4px 0 0;font:800 8px 'JetBrains Mono',monospace;letter-spacing:.6px;padding:2px 6px;border-radius:3px;}
.mf-sp.glow{background:rgba(48,209,88,.07);box-shadow:0 0 14px -4px var(--pc);}
.mf-how{border:1px solid #222;border-left:3px solid #FF9F0A;background:#090909;padding:11px 15px;margin:6px 0 10px;}
.mf-how .h{font:800 9px 'JetBrains Mono',monospace;letter-spacing:1.2px;color:#FF9F0A;margin-bottom:6px;}
.mf-how ol{margin:0 0 0 18px;padding:0;color:#b8b8b8;font-size:12px;line-height:1.75;}
.mf-how b{color:#eaeaea;}
.mf-sum{border:1px solid #222;background:#0b0b0b;padding:11px 15px;margin:8px 0 10px;font-size:13px;color:#cfcfcf;line-height:1.75;}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


# ───────────────────────── formatting primitives ─────────────────────────
def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _na(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _heat(v, scale, rgb=None):
    a = min(0.30, abs(v) / scale * 0.30)
    c = rgb or ("48,209,88" if v >= 0 else "255,69,58")
    return f"rgba({c},{a:.3f})"


def rsi_color(v) -> str:
    if v >= 70: return AMBER
    if v >= 50: return GREEN
    if v >= 40: return "#FF8A80"
    return RED


def rsi_zone(v) -> str:
    if _na(v): return "—"
    v = float(v)
    if v >= 70: return "OVERBOUGHT"
    if v >= 60: return "STRONG"
    if v >= 50: return "BULLISH"
    if v >= 40: return "WEAK"
    if v >= 30: return "BEARISH"
    return "OVERSOLD"


def spark_svg(values, w=72, h=22) -> str:
    if not values or len(values) < 3:
        return '<span class="mf-na">—</span>'
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(values) - 1) * w:.1f},{h - 2 - ((v - lo) / rng) * (h - 5):.1f}" for i, v in enumerate(values))
    c = GREEN if values[-1] >= values[0] else RED
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="vertical-align:middle">'
            f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def ema_chips(row: dict, spans=(10, 20, 50, 200), dist_prefix="dist") -> str:
    """Green = price above that EMA, red = below, grey = not enough history."""
    out = ""
    for s in spans:
        v = row.get(f"ab{s}")
        d = row.get(f"{dist_prefix}{s}")
        if _na(v):
            cls, tip = "na", f"EMA {s}: not enough history"
        else:
            up = bool(v)
            cls = "up" if up else "dn"
            tip = f"Price is {'above' if up else 'below'} the {s}-EMA" + ("" if _na(d) else f" ({float(d):+.2f}%)")
        out += f'<span class="mf-chip {cls}" title="{esc(tip)}">{s}</span>'
    return out


def stage_pill(stage: str) -> str:
    c = STAGE_COLORS.get(stage, T3)
    cls = "mf-stage in" if stage == "ROTATING IN" else "mf-stage"
    return f'<span class="{cls}" style="--sc:{c}">{esc(stage)}</span>'


def score_bar(v) -> str:
    if _na(v):
        return '<span class="mf-na">—</span>'
    v = int(v)
    c = GREEN if v >= 70 else AMBER if v >= 50 else RED
    return f'<span class="mf-sc"><span class="b"><i style="width:{v}%;background:{c}"></i></span>{v}</span>'


def tags(items, bad=False) -> str:
    if not items:
        return '<span class="mf-na">—</span>'
    cls = "mf-tag bad" if bad else "mf-tag"
    return "".join(f'<span class="{cls}">{esc(i)}</span>' for i in items)


# ───────────────────────── table ─────────────────────────
def C(h, k=None, t="text", **kw):
    """Column spec. t: rank text sector num int pct dist rsi ema stage score spark tags html bar muted"""
    return {"h": h, "k": k, "t": t, **kw}


def _cell(c: dict, r: dict, i: int) -> str:
    t, k = c.get("t", "text"), c.get("k")
    v = r.get(k) if k else None
    if t == "rank":
        return f'<td class="rk">{i}</td>'
    if t == "ema":
        return f'<td>{ema_chips(r, c.get("spans", (10, 20, 50, 200)), c.get("dist_prefix", "dist"))}</td>'
    if t == "html":
        return f'<td class="{c.get("al", "")}">{"" if v is None else v}</td>'
    if t == "stage":
        return f'<td>{stage_pill(str(v))}</td>'
    if t == "score":
        return f'<td>{score_bar(v)}</td>'
    if t == "spark":
        return f'<td>{spark_svg(v)}</td>'
    if t == "tags":
        return f'<td class="wrap">{tags(v, c.get("bad", False))}</td>'
    if t == "sector":
        sub = ""
        if c.get("sub") and not _na(r.get(c["sub"])):
            sub = f'<span class="sub">{esc(c.get("sub_fmt", "{}").format(r[c["sub"]]))}</span>'
        return f'<td class="sec"><b>{esc(v)}</b>{sub}</td>'
    if t == "text":
        return f'<td>{esc(v)}</td>'
    if t == "muted":
        return f'<td class="muted" title="{esc(v)}">{esc(v) if v else "—"}</td>'
    if _na(v):
        return f'<td class="{"r" if t != "text" else ""}"><span class="mf-na">—</span></td>'
    v = float(v)
    d = c.get("d", 2)
    if t == "num":
        return f'<td class="num">{c.get("pre", "")}{v:,.{d}f}{c.get("suf", "")}</td>'
    if t == "int":
        return f'<td class="num">{int(v):,}</td>'
    if t == "pct":
        col = GREEN if v >= 0 else RED
        return f'<td class="r"><span class="mf-pct" style="color:{col};background:{_heat(v, c.get("scale", 3.0))}">{v:+.{d}f}%</span></td>'
    if t == "dist":
        ext = c.get("ext", EXTENDED_PCT)
        if v > ext:
            col, bg = AMBER, _heat(v, c.get("scale", 8.0), "255,159,10")
        else:
            col, bg = (GREEN if v >= 0 else RED), _heat(v, c.get("scale", 8.0))
        return f'<td class="r"><span class="mf-pct" style="color:{col};background:{bg}">{v:+.1f}%</span></td>'
    if t == "rsi":
        return f'<td class="r"><span class="mf-pct" style="color:{rsi_color(v)};background:transparent">{v:.0f}</span></td>'
    if t == "bar":
        mx = c.get("max", 100.0)
        pct = max(0.0, min(100.0, v / mx * 100))
        col = c.get("color", AMBER)
        return (f'<td><span class="mf-sc"><span class="b" style="width:70px"><i style="width:{pct:.0f}%;background:{col}"></i></span>'
                f'{v:.0f}{c.get("suf", "")}</span></td>')
    return f'<td>{esc(v)}</td>'


def html_table(cols: list, rows: list, max_height: int | None = None) -> str:
    if not rows:
        return '<div class="arka-mf-note">No rows to show.</div>'
    head = "".join(f'<th class="{c.get("al", "r" if c.get("t") in ("num", "int", "pct", "dist", "rsi") else "")}" '
                   f'title="{esc(c.get("tip", ""))}">{esc(c["h"])}</th>' for c in cols)
    body = "".join("<tr>" + "".join(_cell(c, r, i) for c in cols) + "</tr>" for i, r in enumerate(rows, 1))
    style = f' style="max-height:{max_height}px"' if max_height else ""
    return f'<div class="mf-tw"{style}><table class="mf-t"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def show_table(cols: list, rows: list, max_height: int | None = None):
    st.markdown(html_table(cols, rows, max_height), unsafe_allow_html=True)


# ───────────────────────── cards ─────────────────────────
def kpi_strip(items) -> str:
    """items: (label, value, sub, accent)"""
    cards = "".join(f'<div class="mf-k" style="--a:{a}"><div class="l">{esc(l)}</div><div class="v">{v}</div>'
                    f'<div class="s">{s}</div></div>' for l, v, s, a in items)
    return f'<div class="mf-kpis">{cards}</div>'


def entry_hint(d10, d20, stage) -> str:
    if _na(d20):
        return ""
    d20 = float(d20)
    d10 = None if _na(d10) else float(d10)
    away = f"{d10:+.1f}% from the 10-EMA · {d20:+.1f}% from the 20-EMA" if d10 is not None else f"{d20:+.1f}% from the 20-EMA"
    if d20 > EXTENDED_PCT or stage == "EXTENDED":
        return f"Stretched: {away}. Moves this far from the averages often pause or pull back first."
    if d20 < 0:
        return f"Still below the 20-EMA ({away}). Evidence improves with a close back above it."
    return f"Close to its averages ({away}), so a risk level can be referenced just below the 20-EMA."


def pick_card(r: dict, bench: str) -> str:
    c = STAGE_COLORS.get(r.get("stage"), T3)
    why = "".join(f"<li>{esc(x)}</li>" for x in (r.get("why") or [])[:4]) or "<li>No standout confirmations yet</li>"
    warn = "".join(f"<li>{esc(x)}</li>" for x in (r.get("warn") or [])[:2])
    warn_html = f'<ul class="w">{warn}</ul>' if warn else ""
    leaders = r.get("leaders") or ""
    lead_html = f'<div class="l">LEADERS · <b>{esc(leaders)}</b></div>' if leaders else ""
    return (f'<div class="mf-pick" style="--sc:{c}"><div class="h"><div><div class="n">{esc(r["sector"])}</div>'
            f'<div style="margin-top:5px">{stage_pill(r.get("stage"))} {score_bar(r.get("score"))}</div></div>'
            f'<div>{spark_svg(r.get("spark"), 84, 28)}</div></div>'
            f'<div style="margin-top:8px">{ema_chips(r)}</div><ul>{why}</ul>{warn_html}'
            f'<div class="e">{esc(entry_hint(r.get("dist10"), r.get("dist20"), r.get("stage")))}</div>{lead_html}</div>')


# ───────────────────────── charts ─────────────────────────
def _short(s, n=15) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _dark(fig, h=340, legend=True, margin=(8, 8, 30, 8)):
    fig.update_layout(
        height=h, margin=dict(l=margin[0], r=margin[1], t=margin[2], b=margin[3]),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=legend,
        font=dict(family="Inter, sans-serif", size=11, color=T2),
        legend=dict(orientation="h", y=1.07, x=0, font=dict(size=10)),
        hoverlabel=dict(bgcolor="#0A0A0A", bordercolor=BORDER, font=dict(family="JetBrains Mono, monospace", size=11, color=IVORY)))
    return fig


def fig_rrg(rot, trails, highlight=(), label_sectors=None):
    """Rotation map. x = trend vs benchmark, y = momentum (both centred on 100).
    Dot = where a sector is today (bigger = higher score), arrow = where it moved over the last ~10 sessions,
    full trail only for `highlight`, names only for `label_sectors` (None = every sector)."""
    if go is None:
        return None
    recs = {r["sector"]: r for r in rot.to_dict("records")}
    now = {s: t[-1] for s, t in trails.items() if t and s in recs}
    if not now:
        return None
    hl = {s for s in (highlight or []) if s in now}
    labelled = (set(now) if label_sectors is None else set(label_sectors)) | hl
    devx = [abs(p[0] - 100) for p in now.values()]
    devy = [abs(p[1] - 100) for p in now.values()]
    for s in hl:
        devx += [abs(p[0] - 100) for p in trails[s]]
        devy += [abs(p[1] - 100) for p in trails[s]]
    dx = max(1.5, max(devx) * 1.18)
    dy = max(1.2, max(devy) * 1.18)

    fig = go.Figure()
    for name, x0, x1, y0, y1, xa, ya in (("IMPROVING", 100 - dx, 100, 100, 100 + dy, "left", "top"),
                                         ("LEADING", 100, 100 + dx, 100, 100 + dy, "right", "top"),
                                         ("WEAK", 100 - dx, 100, 100 - dy, 100, "left", "bottom"),
                                         ("FADING", 100, 100 + dx, 100 - dy, 100, "right", "bottom")):
        rgb = QUAD_COLORS[name]
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=f"rgba({rgb},0.06)", line_width=0, layer="below")
        fig.add_annotation(x=x0 if xa == "left" else x1, y=y1 if ya == "top" else y0, xanchor=xa, yanchor=ya,
                           showarrow=False, align=xa, opacity=0.9, font=dict(size=12, color=f"rgb({rgb})"),
                           text=f"<b>{name}</b><br><span style='font-size:10px'>{LANE_INFO[name][0]}</span>")

    for s, p in now.items():                                  # arrows: direction of travel over ~10 sessions
        t = trails[s]
        if len(t) >= 2:
            prev = t[-3] if len(t) >= 3 else t[0]
            fig.add_annotation(x=p[0], y=p[1], ax=prev[0], ay=prev[1], xref="x", yref="y", axref="x", ayref="y",
                               showarrow=True, arrowhead=2, arrowsize=1.1, arrowwidth=1.3, standoff=7, text="",
                               arrowcolor=STAGE_COLORS.get(recs[s].get("stage"), T2), opacity=0.55)
    for s in hl:                                              # full trail only for the sectors you pick
        t = trails[s]
        if len(t) >= 2:
            col = STAGE_COLORS.get(recs[s].get("stage"), T2)
            fig.add_trace(go.Scatter(x=[q[0] for q in t], y=[q[1] for q in t], mode="lines+markers", showlegend=False,
                                     hoverinfo="skip", line=dict(color=col, width=2),
                                     marker=dict(size=[5] * (len(t) - 1) + [2], color=col)))

    order = sorted(now, key=lambda s: now[s][0])
    cycle = ["top center", "bottom center", "middle right", "middle left"]
    xs, ys, texts, tpos, cols, sizes, hovers = [], [], [], [], [], [], []
    for i, s in enumerate(order):
        r, p = recs[s], now[s]
        sc = r.get("score")
        has_sc = not _na(sc) and 0 <= float(sc) <= 100
        xs.append(p[0]); ys.append(p[1])
        texts.append(_short(s, 18) if s in labelled else "")
        tpos.append(cycle[i % 4])
        cols.append(STAGE_COLORS.get(r.get("stage"), T2))
        sizes.append(9 + (float(sc) / 100 * 11 if has_sc else 3))
        hovers.append(f"<b>{esc(s)}</b><br>{esc(r.get('stage'))}" + (f" · score {int(float(sc))}" if has_sc else "")
                      + f"<br>Trend {p[0]:.1f} · Momentum {p[1]:.1f}")
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text", text=texts, textposition=tpos, showlegend=False,
                             textfont=dict(size=10, color=cols), hovertext=hovers, hoverinfo="text",
                             marker=dict(size=sizes, color=cols, line=dict(color="#000", width=1))))
    _dark(fig, 620, legend=False, margin=(8, 8, 8, 8))
    fig.update_xaxes(range=[100 - dx, 100 + dx], gridcolor="#151515", zeroline=False,
                     title="◀ weaker than the benchmark          TREND          stronger than the benchmark ▶")
    fig.update_yaxes(range=[100 - dy, 100 + dy], gridcolor="#151515", zeroline=False,
                     title="◀ slowing down       MOMENTUM       speeding up ▶")
    fig.add_vline(x=100, line_color="#3a3a3a", line_width=1)
    fig.add_hline(y=100, line_color="#3a3a3a", line_width=1)
    return fig


def fig_lines(df: pd.DataFrame, highlight: str, h=330, base_line=False):
    if go is None:
        return None
    fig = go.Figure()
    for col in df.columns:
        main = col == highlight
        color = AMBER if main else BENCH_COLORS.get(col, T2)
        fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col, mode="lines",
                                 line=dict(color=color, width=2.8 if main else 1.5, dash="solid" if main else "solid"),
                                 hovertemplate="%{y:.1f}<extra>" + esc(col) + "</extra>"))
    if base_line:
        fig.add_hline(y=100, line_color="#444", line_width=1, line_dash="dot")
    _dark(fig, h)
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(showgrid=False, linecolor=BORDER)
    fig.update_yaxes(gridcolor="#161616", zeroline=False)
    return fig


def fig_multiples(frames: dict, cols: int = 4):
    """frames: {sector: DataFrame(rebased: sector column + benchmark columns)}"""
    if go is None or make_subplots is None or not frames:
        return None
    names = list(frames)
    rows = math.ceil(len(names) / cols)
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[_short(n, 24) for n in names],
                        vertical_spacing=0.06, horizontal_spacing=0.035)
    first = True
    for i, s in enumerate(names):
        r, c = divmod(i, cols)
        df = frames[s]
        for col in df.columns:
            main = col == s
            fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name="Sector" if main else col,
                                     legendgroup="Sector" if main else col, showlegend=first,
                                     line=dict(color=AMBER if main else BENCH_COLORS.get(col, T2), width=2.2 if main else 1.1),
                                     hovertemplate=f"{esc(col if not main else s)}: " + "%{y:.1f}<extra></extra>"),
                          row=r + 1, col=c + 1)
        first = False
    _dark(fig, rows * 178 + 70, True, (6, 6, 40, 6))
    fig.update_annotations(font_size=10, font_color=IVORY)
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(gridcolor="#141414", tickfont=dict(size=8), zeroline=False)
    return fig


def fig_hbar(labels, values, colors, h=None):
    if go is None:
        return None
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker=dict(color=colors),
                           text=[f"{v:+.1f}%" for v in values], textposition="outside", cliponaxis=False,
                           textfont=dict(size=9, color=T2), hovertemplate="<b>%{y}</b> %{x:+.2f}%<extra></extra>"))
    _dark(fig, h or max(220, 22 * len(labels) + 40), False, (4, 44, 6, 4))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=10, color=IVORY))
    fig.update_xaxes(gridcolor="#161616", zeroline=True, zerolinecolor="#444")
    return fig


# ───────────────────────── rotation board (plain-English lanes) ─────────────────────────
LANE_INFO = {"IMPROVING": ("Behind, but starting to catch up", CYAN),
             "LEADING": ("Beating the market and speeding up", GREEN),
             "WEAK": ("Behind and falling further behind", RED),
             "FADING": ("Still ahead, but losing steam", AMBER)}
LANE_ORDER = ("IMPROVING", "LEADING", "WEAK", "FADING")          # same layout as the map: top row, then bottom row
LANE_VALUE = {"LEADING": 3, "IMPROVING": 2, "FADING": 1, "WEAK": 0}


def _lane_pill(r: dict, name_key: str, quad_key: str, show_score: bool) -> str:
    stage = r.get("stage") or r.get(quad_key)
    c = STAGE_COLORS.get(stage, LANE_INFO.get(r.get(quad_key), ("", T2))[1])
    bits = []
    w = r.get("r1w")
    if not _na(w):
        w = float(w)
        bits.append(f'<span style="color:{GREEN if w >= 0 else RED}">{"▲" if w >= 0 else "▼"} {abs(w):.1f}% 1W</span>')
    rsi = r.get("rsi_d")
    if not _na(rsi):
        bits.append(f'<span style="color:{rsi_color(float(rsi))}">RSI {float(rsi):.0f}</span>')
    if r.get("meta_extra"):
        bits.append(esc(r["meta_extra"]))
    tags_html = ""
    if stage == "ROTATING IN":
        tags_html += f'<span class="t" style="background:rgba(48,209,88,.18);color:{GREEN}">★ ROTATING IN</span>'
    elif stage == "EXTENDED":
        tags_html += f'<span class="t" style="background:rgba(255,214,10,.14);color:{YELLOW}">STRETCHED</span>'
    src = r.get("moved_from")
    if src:
        up = bool(r.get("moved_up"))
        tags_html += (f'<span class="t" style="background:rgba(255,255,255,.06);color:{GREEN if up else RED}">'
                      f'{"↑" if up else "↓"} from {esc(src)}</span>')
    tip = esc(f'{r.get(name_key)} · {stage}' + (f' · score {int(float(r["score"]))}' if show_score and not _na(r.get("score")) else ""))
    glow = " glow" if stage == "ROTATING IN" else ""
    return (f'<div class="mf-sp{glow}" style="--pc:{c}" title="{tip}"><span class="n">{esc(r.get(name_key))}</span>'
            f'<span class="m">{" · ".join(bits)}</span>{tags_html}</div>')


def rotation_board(rows: list, name_key="sector", quad_key="quadrant", show_score=True) -> str:
    """Four lanes laid out like the rotation map: IMPROVING | LEADING on top, WEAK | FADING below."""
    lanes = {k: [] for k in LANE_ORDER}
    for r in rows:
        if r.get(quad_key) in lanes:
            lanes[r[quad_key]].append(r)
    for k in lanes:
        lanes[k].sort(key=lambda r: -(float(r["score"]) if show_score and not _na(r.get("score")) else (float(r["r1w"]) if not _na(r.get("r1w")) else -1e9)))
    cells = ""
    for k in LANE_ORDER:
        desc, c = LANE_INFO[k]
        items = "".join(_lane_pill(r, name_key, quad_key, show_score) for r in lanes[k]) or '<span class="mf-na">none</span>'
        cells += (f'<div class="mf-lane" style="--c:{c}"><div class="mf-lh"><b>{k}</b><span>{esc(desc)}</span>'
                  f'<i>{len(lanes[k])}</i></div><div class="mf-lb">{items}</div></div>')
    return f'<div class="mf-board">{cells}</div>'


def how_to_read(bench: str) -> str:
    return ('<div class="mf-how"><div class="h">HOW TO READ THIS IN 10 SECONDS</div><ol>'
            f'<li>Every sector is compared with <b>{esc(bench)}</b>. <b>Right = beating it</b>, left = trailing it.</li>'
            '<li><b>Up = getting stronger</b>, down = slowing down. The arrow shows where the sector moved in the last 10 sessions.</li>'
            '<li>Sectors usually travel <b>IMPROVING → LEADING → FADING → WEAK → IMPROVING</b>. '
            'The best window is early: a sector climbing out of WEAK into IMPROVING, then turning LEADING (marked <b>★ ROTATING IN</b>). '
            'LEADING sectors that are stretched far above their averages often pause first.</li></ol></div>')
