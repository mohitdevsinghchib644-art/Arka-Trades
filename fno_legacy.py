"""
Arka Trades · the earlier F&O positioning views (fno_legacy.py)

This is fno_page.py v2, kept under a new name. The signal desk (fno_page.py v3) draws its "Options map" tab with the
chain, gamma, history and stock-table views below, and the Research page still uses the single-stock view. The verdicts
and signals of the v2 model are no longer shown anywhere: on five years of real data that model had no forecasting skill.

Original description (v2): a research desk built on fno_engine.py. Six views:
  Overview      the verdict for NIFTY 50 and BANK NIFTY, how much to trust it, the written desk note, market internals
  NIFTY 50      the model and its record on unseen days, a test strategy against buy and hold, what is driving the forecast,
  BANK NIFTY    the past days most like today, the option chain (open interest, fresh positions, volatility smile, dealer
                gamma, max pain), six months of history, levels, an option structure, and every tested reading
  Stocks        one model ranking all F&O stocks, its record, the price / open-interest map, a filterable table and a full
                page for any single stock
  Participants  who holds what: FIIs, retail clients, pro desks and DIIs, today and over six months
  Method        how every number is made and tested, and the model's weights

Nothing here calls an outside service or an AI model. The data comes from the files the "F&O data" workflow saves in
arka_data/fno/. Charts are drawn as plain SVG so the page stays fast on a phone.

Public API:  render_fno(supabase=None, on_open=None)   ·   home_rows()
"""
from __future__ import annotations

import html
import math
from datetime import date

import streamlit as st

import fno_engine as E

GRADE = {"proven": ("Proven", "g"), "leaning": ("Leaning", "a"), "unproven": ("Not proven", "n"), "thin": ("Too few", "n")}
TONE = {"up": "up", "dn": "dn", "flat": "fl"}
GREEN, RED, AMBER, BLUE, GREY = "var(--green,#22C55E)", "var(--red,#EF4444)", "var(--amber,#F59E0B)", "var(--c,#3B82F6)", "var(--tx3)"
BU_NAME = {0: "No pattern", 1: "Long build-up", 2: "Short build-up", 3: "Short covering", 4: "Long unwinding"}
BU_COL = {0: "var(--s3)", 1: "#22C55E", 2: "#EF4444", 3: "#86EFAC", 4: "#FCA5A5"}

CSS = """<style>
.fo{font-family:var(--sans,'Inter',system-ui,sans-serif);font-variant-numeric:tabular-nums;container-type:inline-size;width:100%;color:var(--tx);}
.fo *{box-sizing:border-box;}
.fo .up{color:var(--green,#22C55E);} .fo .dn{color:var(--red,#EF4444);} .fo .fl{color:var(--tx2);} .fo .am{color:var(--amber,#F59E0B);} .fo .mu{color:var(--tx3);}
.fo-chips{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;}
.fo-chip{display:inline-flex;gap:6px;align-items:center;padding:5px 10px;border-radius:7px;background:var(--s1);border:1px solid var(--line);font:500 12px var(--sans);color:var(--tx2);white-space:nowrap;}
.fo-chip b{font-weight:600;color:var(--tx);}
.fo-g{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;margin-bottom:12px;}
.fo-card{background:var(--s1);border:1px solid var(--line);border-radius:10px;min-width:0;display:flex;flex-direction:column;}
.fo-ct{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line);}
.fo-ct b{font:600 13px var(--sans);color:var(--tx);} .fo-ct span{font:400 11.5px var(--sans);color:var(--tx3);text-align:right;}
.fo-cb{padding:14px;flex:1;min-width:0;}
.fo-sec{display:flex;align-items:baseline;flex-wrap:wrap;gap:2px 10px;margin:18px 0 9px;padding-left:9px;border-left:3px solid var(--amber,#F59E0B);}
.fo-sec b{font:650 14.5px var(--sans);white-space:nowrap;} .fo-sec span{font:400 12px var(--sans);color:var(--tx3);}
.fo-vd{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.fo-vd b{font:650 25px/1.15 var(--sans);letter-spacing:-.01em;} .fo-vd span{font:500 12px var(--sans);color:var(--tx3);}
.fo-conf{display:inline-flex;align-items:center;gap:6px;margin-left:auto;padding:4px 9px;border-radius:7px;border:1px solid var(--line2);font:500 11.5px var(--sans);color:var(--tx2);white-space:nowrap;}
.fo-conf i{width:7px;height:7px;border-radius:50%;background:var(--s3);} .fo-conf i.on{background:var(--amber,#F59E0B);}
.fo-meter{position:relative;height:6px;border-radius:3px;margin:12px 0 4px;background:linear-gradient(90deg,rgba(239,68,68,.6),rgba(113,113,122,.3) 40%,rgba(113,113,122,.3) 60%,rgba(34,197,94,.6));}
.fo-meter i{position:absolute;top:-4px;width:3px;height:14px;border-radius:2px;background:var(--tx);transform:translateX(-50%);}
.fo-scale{display:flex;justify-content:space-between;font:400 11px var(--sans);color:var(--tx3);}
.fo-tx{font:400 13px/1.55 var(--sans);color:var(--tx2);margin-top:10px;}
.fo-tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border:1px solid var(--line);border-radius:9px;overflow:hidden;gap:1px;background:var(--s1);margin-top:12px;}
.fo-tile{padding:9px 11px;min-width:0;box-shadow:0 0 0 1px var(--line);}
.fo-tile small{display:block;font:500 11px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fo-tile b{display:block;font:600 15.5px/1.3 var(--sans);color:var(--tx);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fo-tile em{display:block;font:400 11.5px var(--sans);font-style:normal;color:var(--tx3);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fo-note2{display:grid;gap:11px;}
.fo-note2 div{font:400 13px/1.6 var(--sans);color:var(--tx2);} .fo-note2 small{display:block;font:600 11.5px var(--sans);color:var(--tx);margin-bottom:1px;}
.fo-bar{position:relative;height:30px;margin:4px 0 2px;}
.fo-bar .tr{position:absolute;left:0;right:0;top:13px;height:4px;border-radius:2px;background:var(--s3);}
.fo-bar .bd{position:absolute;top:9px;height:12px;border-radius:3px;background:rgba(161,161,170,.18);border:1px solid var(--line2);}
.fo-bar i{position:absolute;top:5px;width:3px;height:20px;border-radius:2px;transform:translateX(-50%);}
.fo-leg{display:flex;gap:6px 14px;flex-wrap:wrap;font:400 11.5px var(--sans);color:var(--tx3);margin-bottom:6px;} .fo-leg i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;}
.fo-leg b{font-weight:600;color:var(--tx2);}
.fo-ch{position:relative;width:100%;}
.fo-ch svg{position:absolute;left:0;top:0;width:calc(100% - 54px);height:100%;overflow:visible;}
.fo-ch .yl{position:absolute;right:0;width:50px;text-align:right;font:400 10.5px var(--sans);color:var(--tx3);transform:translateY(-50%);white-space:nowrap;}
.fo-xl{display:flex;justify-content:space-between;margin:4px 54px 0 0;font:400 10.5px var(--sans);color:var(--tx3);}
.fo-drv{display:grid;gap:5px;}
.fo-drv .r{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr) 54px;gap:10px;align-items:center;font:400 12.5px var(--sans);}
.fo-drv .r>span{color:var(--tx);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;} .fo-drv .r>span small{color:var(--tx3);font-size:11.5px;margin-left:6px;}
.fo-drv .t{position:relative;height:10px;background:var(--s2);border-radius:2px;} .fo-drv .t:before{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--line2);}
.fo-drv .t i{position:absolute;top:0;bottom:0;border-radius:2px;} .fo-drv .r b{text-align:right;font-weight:600;font-size:12px;}
.fo-strip{position:relative;height:44px;margin:6px 4px 0;}
.fo-strip .ax{position:absolute;left:0;right:0;top:21px;height:2px;background:var(--s3);}
.fo-strip i{position:absolute;top:15px;width:9px;height:14px;border-radius:2px;transform:translateX(-50%);opacity:.85;}
.fo-strip .z{position:absolute;top:6px;bottom:6px;width:1px;background:var(--tx3);} .fo-strip .m{position:absolute;top:2px;bottom:2px;width:2px;background:var(--amber,#F59E0B);}
.fo-quad{position:relative;height:400px;border:1px solid var(--line);border-radius:8px;background:var(--s2);overflow:hidden;}
.fo-quad .h,.fo-quad .v{position:absolute;background:var(--line2);} .fo-quad .h{left:0;right:0;height:1px;} .fo-quad .v{top:0;bottom:0;width:1px;}
.fo-quad i{position:absolute;width:8px;height:8px;border-radius:50%;transform:translate(-50%,-50%);opacity:.9;}
.fo-quad em{position:absolute;font:500 10px var(--sans);font-style:normal;color:var(--tx2);transform:translate(7px,-50%);white-space:nowrap;}
.fo-quad .q{position:absolute;font:600 11px var(--sans);color:var(--tx3);padding:8px 10px;}
.fo-bu{display:flex;gap:2px;margin-top:4px;} .fo-bu i{flex:1;height:16px;border-radius:2px;}
.fo-legs{display:grid;gap:6px;margin-top:10px;}
.fo-legs div{display:flex;justify-content:space-between;gap:10px;padding:7px 10px;border-radius:8px;background:var(--s2);font:500 13px var(--sans);}
.fo-legs div b{font-weight:600;} .fo-legs div span{color:var(--tx3);}
.fo-tag{font:500 11px var(--sans);color:var(--tx3);border:1px solid var(--line2);border-radius:6px;padding:2px 7px;margin-left:8px;white-space:nowrap;}
.fo-tw{overflow:auto;max-height:600px;}
.fo-t{width:100%;border-collapse:collapse;font:400 12.5px var(--sans);}
.fo-t th{position:sticky;top:0;z-index:1;background:var(--s1);text-align:right;font:600 11px var(--sans);color:var(--tx3);padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap;}
.fo-t td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap;color:var(--tx2);}
.fo-t th:first-child,.fo-t td:first-child{text-align:left;padding-left:14px;} .fo-t th:last-child,.fo-t td:last-child{padding-right:14px;}
.fo-t td:first-child{color:var(--tx);font-weight:600;}
.fo-t td.l,.fo-t th.l{text-align:left;} .fo-t td.w{white-space:normal;min-width:150px;} .fo-t td.b{color:var(--tx);font-weight:600;}
.fo-t tr:last-child td{border-bottom:0;} .fo-t tr.on td:first-child{box-shadow:inset 3px 0 0 var(--c,#3B82F6);} .fo-t tr.now td{background:var(--s2);}
.fo-t small{display:block;font:400 11.5px var(--sans);color:var(--tx3);font-weight:400;white-space:normal;}
.fo-pill{display:inline-block;padding:2px 8px;border-radius:6px;font:600 11px var(--sans);white-space:nowrap;}
.fo-pill.g{background:rgba(34,197,94,.14);color:var(--green,#22C55E);} .fo-pill.r{background:rgba(239,68,68,.14);color:var(--red,#EF4444);}
.fo-pill.a{background:rgba(245,158,11,.14);color:var(--amber,#F59E0B);} .fo-pill.n{background:var(--s3);color:var(--tx3);}
.fo-note{font:400 12px/1.6 var(--sans);color:var(--tx3);margin-top:10px;}
.fo-empty{padding:16px;font:400 13px/1.6 var(--sans);color:var(--tx2);}
.fo-mini{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;}
.fo-stack{display:grid;gap:12px;align-content:start;min-width:0;} .fo-tiles.two{grid-template-columns:repeat(2,minmax(0,1fr)) !important;}
.fo .xw{display:none;}
@container (min-width:620px){.fo-tiles{grid-template-columns:repeat(3,minmax(0,1fr));} .fo-tiles.four{grid-template-columns:repeat(4,minmax(0,1fr));} .fo .xw{display:table-cell;}
  .fo-t td.w{min-width:170px;} .fo-mini{grid-template-columns:repeat(2,minmax(0,1fr));}}
@container (min-width:900px){.fo-g.two{grid-template-columns:repeat(2,minmax(0,1fr));} .fo-g.three{grid-template-columns:repeat(3,minmax(0,1fr));}
  .fo-g.s57{grid-template-columns:minmax(0,5fr) minmax(0,7fr);} .fo-g.s75{grid-template-columns:minmax(0,7fr) minmax(0,5fr);} .fo-vd b{font-size:28px;}
  .fo-tiles.six{grid-template-columns:repeat(6,minmax(0,1fr));} .fo-mini.four{grid-template-columns:repeat(4,minmax(0,1fr));}}
@container (max-width:619px){.fo-cb{padding:12px;} .fo-conf{margin-left:0;} .fo-quad{height:280px;} .fo-drv .r{grid-template-columns:minmax(0,1.3fr) minmax(0,.8fr) 46px;}}
</style>"""


# ───────────── formatting ─────────────
def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _ok(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and x == x and not math.isinf(x)


def _num(x, d=0) -> str:
    return f"{x:,.{d}f}" if _ok(x) else "—"


def _px(x) -> str:
    return "—" if not _ok(x) else f"{x:,.0f}" if abs(x) >= 5000 else f"{x:,.1f}" if abs(x) >= 100 else f"{x:,.2f}"


def _pct(x, d=1, sign=True) -> str:
    return "—" if not _ok(x) else f"{x * 100:{'+' if sign else ''}.{d}f}%"


def _cls(x) -> str:
    return "fl" if not _ok(x) or abs(x) < 1e-9 else "up" if x > 0 else "dn"


def _col(x, d=1) -> str:
    return f'<span class="{_cls(x)}">{_pct(x, d)}</span>'


def _qty(x) -> str:
    """Contracts and shares the Indian way: lakh and crore."""
    if not _ok(x):
        return "—"
    a, s = abs(x), "-" if x < 0 else ""
    return f"{s}{a / 1e7:,.0f} Cr" if a >= 1e9 else f"{s}{a / 1e7:.2f} Cr" if a >= 1e7 else f"{s}{a / 1e5:.1f} L" if a >= 1e5 else f"{s}{a / 1e3:.0f} K" if a >= 1e3 else f"{s}{a:.0f}"


def _rank(p) -> str:
    return "not enough history" if not _ok(p) else f"higher than {p * 100:.0f}% of the last year"


def _day(iso, year=True) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%b %Y' if year else '%b')}"
    except Exception:
        return "—"


def _card(title, right, body, flush=False) -> str:
    return (f'<div class="fo-card"><div class="fo-ct"><b>{_e(title)}</b><span>{right}</span></div>'
            f'<div class="{"" if flush else "fo-cb"}">{body}</div></div>')


def _tile(label, val, sub="") -> str:
    return f'<div class="fo-tile"><small>{_e(label)}</small><b>{val}</b>{f"<em>{sub}</em>" if sub else ""}</div>'


def _sec(title, sub="") -> str:
    return f'<div class="fo-sec"><b>{_e(title)}</b><span>{_e(sub)}</span></div>'


def _grid(cls, *cards) -> str:
    return f'<div class="fo-g {cls}">{"".join(cards)}</div>'


def _legend(items) -> str:
    return '<div class="fo-leg">' + "".join(f'<span><i style="background:{c}"></i>{_e(n)}{f" <b>{v}</b>" if v else ""}</span>' for n, c, v in items) + '</div>'


# ───────────── charts (plain SVG, text kept as HTML so it stays sharp at any width) ─────────────
def _nice(lo, hi, n=4):
    span = (hi - lo) or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((1, 2, 2.5, 5, 10), key=lambda m: abs(m * mag - raw)) * mag
    t, out = math.ceil(lo / step) * step, []
    while t <= hi + 1e-12:
        out.append(t)
        t += step
    return out


def _lines(dates, series, h=170, fmt=_px, zero=False, legend=True) -> str:
    """Line chart. series = [(name, colour, values, last-value text or '')]."""
    vals = [v for _, _, ys, *_ in series for v in ys if _ok(v)]
    if len(vals) < 3:
        return '<div class="fo-empty">Not enough history to draw this yet.</div>'
    lo, hi = min(vals), max(vals)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    n = max(len(ys) for _, _, ys, *_ in series)
    X = lambda i: i / max(n - 1, 1) * 1000
    Y = lambda v: (hi - v) / (hi - lo) * h
    ticks = _nice(lo, hi)
    grid = "".join(f'<line x1="0" x2="1000" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{"var(--tx3)" if (zero and abs(t) < 1e-12) else "var(--line)"}" '
                   f'stroke-width="1" vector-effect="non-scaling-stroke"/>' for t in ticks)
    paths = ""
    for _, colr, ys, *_ in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys) if _ok(v))
        paths += f'<polyline points="{pts}" fill="none" stroke="{colr}" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>'
    yl = "".join(f'<span class="yl" style="top:{Y(t) / h * 100:.1f}%">{fmt(t)}</span>' for t in ticks)
    xl = ""
    if dates:
        idx = sorted({int(round(k * (len(dates) - 1) / 4)) for k in range(5)})
        xl = '<div class="fo-xl">' + "".join(f"<span>{_day(dates[i], False)}</span>" for i in idx) + "</div>"
    leg = _legend([(nm, colr, (rest[0] if rest else "")) for nm, colr, _, *rest in series]) if legend else ""
    return (f'{leg}<div class="fo-ch" style="height:{h}px"><svg viewBox="0 0 1000 {h}" preserveAspectRatio="none">{grid}{paths}</svg>{yl}</div>{xl}')


def _bars(labels, series, h=170, mark=None, diverge=False, fmt=_qty, legend=True, mark_name="Price", xlabels=None) -> str:
    """Bar chart across strikes. series = [(name, colour, values)]; mark = a price to draw as a vertical line."""
    n = len(labels)
    vals = [abs(v) for _, _, ys in series for v in ys if _ok(v)]
    if n < 3 or not vals or max(vals) <= 0:
        return '<div class="fo-empty">Nothing stored for this expiry.</div>'
    m, slot, k = max(vals), 10.0, len(series)
    W = n * slot
    zero_y, scale = (h / 2, (h / 2 - 4) / m) if diverge else (h - 1.0, (h - 8) / m)
    bw = 8.0 / k
    rects = ""
    for j, (_, colr, ys) in enumerate(series):
        for i, v in enumerate(ys):
            if _ok(v) and v != 0:
                hh = abs(v) * scale
                rects += f'<rect x="{i * slot + 1 + j * bw:.2f}" y="{(zero_y - hh) if v > 0 else zero_y:.1f}" width="{bw * 0.92:.2f}" height="{hh:.1f}" fill="{colr}" opacity=".85"/>'
    extra = f'<line x1="0" x2="{W}" y1="{zero_y:.1f}" y2="{zero_y:.1f}" stroke="var(--line2)" stroke-width="1" vector-effect="non-scaling-stroke"/>'
    if _ok(mark) and labels[0] < mark < labels[-1]:
        pos = 0.0
        for i in range(n - 1):
            if labels[i] <= mark <= labels[i + 1]:
                pos = (i + (mark - labels[i]) / ((labels[i + 1] - labels[i]) or 1)) * slot + slot / 2
                break
        extra += f'<line x1="{pos:.1f}" x2="{pos:.1f}" y1="0" y2="{h}" stroke="var(--tx)" stroke-width="1.2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>'
    yl = f'<span class="yl" style="top:6%">{fmt(m)}</span><span class="yl" style="top:{zero_y / h * 100:.0f}%">0</span>'
    if diverge:
        yl += f'<span class="yl" style="top:94%">{fmt(-m)}</span>'
    idx = sorted({int(round(q * (n - 1) / 5)) for q in range(6)})
    xl = '<div class="fo-xl">' + "".join(f"<span>{_e(x)}</span>" for x in (xlabels or [_px(labels[i]) for i in idx])) + "</div>"
    items = [(nm, colr, "") for nm, colr, _ in series] + ([(f"{mark_name} {_px(mark)}", "var(--tx)", "")] if _ok(mark) else [])
    return (f'{_legend(items) if legend else ""}<div class="fo-ch" style="height:{h}px"><svg viewBox="0 0 {W:.0f} {h}" preserveAspectRatio="none">{extra}{rects}</svg>{yl}</div>{xl}')


def _level_bar(lv: dict) -> str:
    fut, mv = lv.get("fut"), lv.get("move10")
    marks = [(lv.get("put_wall"), GREEN, "Put wall"), (lv.get("max_pain"), AMBER, "Max pain"), (fut, "var(--tx)", "Price"), (lv.get("call_wall"), RED, "Call wall")]
    pts = [m[0] for m in marks if _ok(m[0]) and m[0] > 0]
    if not _ok(fut) or len(pts) < 2:
        return ""
    lo, hi = min(pts + ([fut - mv] if _ok(mv) else [])), max(pts + ([fut + mv] if _ok(mv) else []))
    pad = (hi - lo) * 0.06 or fut * 0.01
    lo, hi = lo - pad, hi + pad
    pos = lambda v: max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))
    band = f'<div class="bd" style="left:{pos(fut - mv):.1f}%;width:{pos(fut + mv) - pos(fut - mv):.1f}%"></div>' if _ok(mv) else ""
    ticks = "".join(f'<i style="left:{pos(v):.1f}%;background:{c}" title="{_e(n)} {_px(v)}"></i>' for v, c, n in marks if _ok(v) and v > 0)
    items = [(f"{n} {_px(v)}", c, "") for v, c, n in marks if _ok(v) and v > 0]
    if _ok(mv):
        items.append((f"Priced 10-session range ±{_px(mv)}", "rgba(161,161,170,.35)", ""))
    return f'<div class="fo-bar"><div class="tr"></div>{band}{ticks}</div>{_legend(items)}'


# ───────────── shared blocks ─────────────
def _conf(M) -> str:
    n = (M or {}).get("conf_n", 0)
    name = (M or {}).get("conf", "Untested")
    dots = "".join(f'<i class="{"on" if i < n else ""}"></i>' for i in range(3))
    return f'<span class="fo-conf" title="How well the model\'s forecasts matched what happened on days it had not seen">Confidence {dots} {_e(name)}</span>'


def _verdict(x: dict) -> str:
    M = x.get("model") or {}
    q, n = M.get("pct"), M.get("conf_n", 0)
    if not _ok(q) or n == 0:
        q = 0.5                                      # a model without a record does not move the needle
    elif n == 1:
        q = max(0.22, min(0.78, q))                  # a thin record may lean, not reach the strong calls
    pos = q * 100
    return (f'<div class="fo-vd"><b class="{TONE[x["tone"]]}">{_e(x["verdict"])}</b><span>next {E.MAIN_H} sessions</span>{_conf(M)}</div>'
            f'<div class="fo-meter"><i style="left:{pos:.1f}%"></i></div>'
            '<div class="fo-scale"><span>Correction risk</span><span>No edge</span><span>Rally setup</span></div>')


def _model_tiles(x: dict, cls="") -> str:
    M, lv = x.get("model") or {}, x["levels"]
    fc = M.get("forecast")
    st_ = (M.get("by") or {}).get(x["level"])
    pu = st_["hit"] if st_ else None
    t = [_tile("Model forecast", _col(fc, 1) if _ok(fc) else "—", f'normal {_pct(M.get("drift"))} · {E.MAIN_H} sessions' if _ok(M.get("drift")) else "needs two years of data"),
         _tile("Ended higher after this verdict", f'{pu * 100:.0f}%' if _ok(pu) else "—", f'normal {M["base_up"] * 100:.0f}%' if _ok(M.get("base_up")) else ""),
         _tile("Forecast rank", f'{M["pct"] * 100:.0f} of 100' if _ok(M.get("pct")) else "—", "against its own history"),
         _tile("Future", _px(lv.get("fut")), f'{_col(lv.get("r1"), 2)} today · {_col(lv.get("r5"))} 5 sessions'),
         _tile("Priced 10-session range", f'{_px(lv["fut"] - lv["move10"])} – {_px(lv["fut"] + lv["move10"])}' if _ok(lv.get("move10")) else "—",
               f'implied volatility {_pct(lv.get("iv"), 1, False)}'),
         _tile("Put wall · max pain · call wall", f'{_px(lv.get("put_wall"))} · {_px(lv.get("max_pain"))} · {_px(lv.get("call_wall"))}',
               f'expiry {_day(lv.get("m_exp"), False)} · {lv.get("dte")} days' if lv.get("dte") is not None else "")]
    return f'<div class="fo-tiles {cls}">{"".join(t)}</div>'


def _drivers(drv: list, limit=None, unit="%") -> str:
    drv = [d for d in drv if d.get("x") is not None][: (limit or 99)]
    if not drv:
        return '<div class="fo-empty">The model has no inputs for today yet.</div>'
    m = max(abs(d["contrib"]) for d in drv) or 1.0
    rows = []
    for d in drv:
        w = abs(d["contrib"]) / m * 50
        colr = GREEN if d["contrib"] > 0 else RED
        left = 50 if d["contrib"] > 0 else 50 - w
        rows.append(f'<div class="r"><span title="{_e(d["text"])}">{_e(d["label"])}<small>{_e(d["text"])}</small></span>'
                    f'<div class="t"><i style="left:{left:.1f}%;width:{w:.1f}%;background:{colr}"></i></div>'
                    f'<b class="{_cls(d["contrib"])}">{d["contrib"] * 100:+.2f}{unit}</b></div>')
    return f'<div class="fo-drv">{"".join(rows)}</div>'


def _analogs(A: dict | None, who: str) -> str:
    if not A or not A.get("h", {}).get(E.MAIN_H):
        return '<div class="fo-empty">Not enough history yet to find past days that look like today.</div>'
    a = A["h"][E.MAIN_H]
    f = [v for v in A.get("f", []) if _ok(v)]
    lo, hi = min(f + [0.0]), max(f + [0.0])
    pad = (hi - lo) * 0.05 or 0.01
    lo, hi = lo - pad, hi + pad
    pos = lambda v: (v - lo) / (hi - lo) * 100
    dots = "".join(f'<i style="left:{pos(v):.1f}%;background:{GREEN if v > 0 else RED}" title="{_pct(v)}"></i>' for v in f)
    strip = (f'<div class="fo-strip"><div class="ax"></div><div class="z" style="left:{pos(0):.1f}%"></div>{dots}'
             f'<div class="m" style="left:{pos(a["median"]):.1f}%" title="Median {_pct(a["median"])}"></div></div>'
             f'<div class="fo-xl" style="margin-right:0"><span>{_pct(lo)}</span><span>each bar is one past day · amber line is the median</span><span>{_pct(hi)}</span></div>')
    tiles = ('<div class="fo-tiles four">' + _tile("Median move", _col(a["median"]), f'normal {_pct(a["base"])}')
             + _tile("Ended higher", f'{a["up"] * 100:.0f}%', f'normal {a["base_up"] * 100:.0f}%') + _tile("Worst", _col(a["worst"]), f'{E.MAIN_H} sessions later')
             + _tile("Best", _col(a["best"]), f'{E.MAIN_H} sessions later') + '</div>')
    rows = "".join(f'<tr><td>{_day(r["date"])}</td><td>{_col(r.get("f5"))}</td><td class="b">{_col(r.get("f10"))}</td><td>{_col(r.get("f20"))}</td></tr>' for r in A["rows"])
    table = (f'<div class="fo-tw" style="max-height:230px;margin-top:10px"><table class="fo-t"><thead><tr><th>Closest past days</th><th>5 sessions</th>'
             f'<th>{E.MAIN_H} sessions</th><th>20 sessions</th></tr></thead><tbody>{rows}</tbody></table></div>')
    return (f'<div class="fo-tx" style="margin-top:0">The {A["n"]} past days when {who} looked most like today on every input, at least {E.MAIN_H} sessions apart, '
            f'and what the price did next.</div>{strip}{tiles}{table}')


def _note(note: list) -> str:
    return '<div class="fo-note2">' + "".join(f'<div><small>{_e(n["h"])}</small>{_e(n["t"])}</div>' for n in note) + '</div>'


def _hist_charts(hist: dict, with_oi=True, four=True) -> str:
    d = hist.get("date") or []
    last = lambda k, f: (f(next((v for v in reversed(hist.get(k) or []) if _ok(v)), None)))
    parts = [_lines(d, [("Future price", "var(--tx)", hist.get("px") or [], last("px", _px))], h=130),
             _lines(d, [("Put-call ratio", BLUE, hist.get("pcr") or [], last("pcr", lambda v: _num(v, 2)))], h=130, fmt=lambda v: f"{v:.2f}"),
             _lines(d, [("Implied volatility", AMBER, hist.get("iv") or [], last("iv", lambda v: _pct(v, 1, False)))], h=130, fmt=lambda v: f"{v * 100:.0f}%")]
    if with_oi:
        parts.append(_lines(d, [("Futures open interest", "#A78BFA", hist.get("oi") or [], last("oi", _qty))], h=130, fmt=_qty))
    return f'<div class="fo-mini {"four" if four else ""}">' + "".join(f"<div>{p}</div>" for p in parts) + "</div>"


def _chain_cards(c: dict | None, F) -> str:
    if not c:
        return _grid("", _card("Option chain", "", '<div class="fo-empty">Strike-by-strike data for this expiry is not stored yet. It arrives with the next run of the F&O data workflow.</div>', True))
    K = c["strike"]
    keep = [i for i, k in enumerate(K) if abs(k / F - 1) <= 0.07] if _ok(F) else list(range(len(K)))
    if len(keep) < 8:
        keep = list(range(len(K)))
    sub = lambda key: [c[key][i] for i in keep]
    ks = [K[i] for i in keep]
    oi = _bars(ks, [("Call open interest", RED, sub("ce_oi")), ("Put open interest", GREEN, sub("pe_oi"))], mark=F)
    chg = _bars(ks, [("Calls added today", RED, sub("ce_chg")), ("Puts added today", GREEN, sub("pe_chg"))], mark=F, diverge=True)
    tiles = ('<div class="fo-tiles two">' + _tile("Max pain", _px(c.get("max_pain")), f'expiry {_day(c["exp"], False)} · {c["dte"]} days')
             + _tile("Put wall · call wall", f'{_px(c.get("put_wall"))} · {_px(c.get("call_wall"))}', "most open interest")
             + _tile("Fresh writing today", f'{_px(c.get("put_add"))} · {_px(c.get("call_add"))}', "puts · calls, biggest additions")
             + _tile("Put-call ratio", _num(c.get("pcr"), 2), "this expiry, stored strikes") + '</div>')
    right = [_card("Open interest by strike", f'{_day(c["exp"], False)} expiry', oi + tiles), _card("Positions added or closed today", "change in open interest", chg)]
    third = ""
    if c.get("gex") and any(abs(v) > 0 for v in c["gex"]):
        gex = _bars(ks, [("Estimated dealer gamma", BLUE, sub("gex"))], mark=F, diverge=True)
        sign = "positive" if c["net_gex"] > 0 else "negative"
        txt = ("Positive gamma: dealers trade against moves, which tends to hold price in a range." if c["net_gex"] > 0
               else "Negative gamma: dealers trade with moves, which tends to stretch them.")
        flip = f' It changes sign near <b style="color:var(--tx)">{_px(c["flip"])}</b>.' if _ok(c.get("flip")) else ""
        iv = [v for v in sub("iv")]
        smile = _lines([], [("Implied volatility by strike", AMBER, iv, "")], h=110, fmt=lambda v: f"{v * 100:.1f}%")
        xl = '<div class="fo-xl">' + "".join(f"<span>{_px(ks[i])}</span>" for i in sorted({int(round(q * (len(ks) - 1) / 5)) for q in range(6)})) + "</div>"
        third = _card("Dealer gamma and the volatility smile", f"net gamma {sign}", gex + f'<div class="fo-tx">{txt}{flip}</div><div style="height:12px"></div>' + smile + xl
                      + '<div class="fo-note">Gamma is an estimate: it assumes dealers have sold the puts and bought the calls that customers hold. The true split is not published.</div>')
    return _grid("three" if third else "two", *right, third)


def _idea(idea: dict) -> str:
    if not idea or not idea.get("name"):
        return f'<div class="fo-tx" style="margin-top:0">{_e((idea or {}).get("why") or "No structure today.")}</div>'
    out = [f'<div><b style="font:650 16px var(--sans)">{_e(idea["name"])}</b><span class="fo-tag">Rule-based · not backtested</span></div>',
           f'<div class="fo-tx">{_e(idea["why"])}</div>']
    if idea.get("legs"):
        rows = "".join(f'<div><b class="{"dn" if l["act"] == "Sell" else "up"}">{l["act"]} {_px(l["strike"])} {"call" if l["typ"] == "CE" else "put"}</b>'
                       f'<span>{("at " + _num(l["px"], 2)) if _ok(l.get("px")) else "price not stored"}</span></div>' for l in idea["legs"])
        out.append(f'<div class="fo-legs">{rows}</div>')
    if _ok(idea.get("max_gain")):
        be = " and ".join(_px(b) for b in idea["breakeven"])
        lot = f'per lot of {idea["lot"]:,.0f}' if _ok(idea.get("lot")) else "per unit"
        out.append('<div class="fo-tiles">' + _tile("Most you can make", f'<span class="up">₹{_num(idea["max_gain"])}</span>', lot)
                   + _tile("Most you can lose", f'<span class="dn">₹{_num(idea["max_loss"])}</span>', lot)
                   + _tile("Breakeven", be, f'{"receive" if idea["net"] > 0 else "pay"} {_num(abs(idea["net"]), 2)} a unit') + '</div>')
    if idea.get("note"):
        out.append(f'<div class="fo-note">{_e(idea["note"])}</div>')
    if idea.get("legs"):
        out.append('<div class="fo-note">Premiums are the last stored closing prices, not live quotes. Check the live chain, margin and costs before placing anything.</div>')
    return "".join(out)


def _evidence(signals: list, relative: bool) -> str:
    order = {"proven": 0, "leaning": 1, "unproven": 2, "thin": 3}
    sig = sorted(signals, key=lambda s: (not s.get("on"), order[s["grade"]], -(abs(s["stats"][E.MAIN_H]["t"]) if s["stats"].get(E.MAIN_H) else 0)))
    cell = lambda st_: f'<td class="xw">{_col(st_["edge"], 2)}</td>' if st_ else '<td class="xw">—</td>'
    rows = []
    for s in sig:
        m = s["stats"].get(E.MAIN_H)
        lab, cl = GRADE[s["grade"]]
        hit = f'{m["hit"] * 100:.0f}% <span class="mu">vs {m["base_hit"] * 100:.0f}%</span>' if m else "—"
        main = f'<td class="b">{_col(m["edge"], 2)}</td>' if m else "<td>—</td>"
        today = '<b class="up">On</b>' if s.get("on") else '<span class="mu">Off</span>'
        rows.append(f'<tr class="{"on" if s.get("on") else ""}"><td class="w">{_e(s["label"])}<small>{_e(s["note"])}</small></td>{"" if relative else f"<td>{today}</td>"}'
                    f'{cell(s["stats"].get(5))}{main}{cell(s["stats"].get(20))}<td class="xw">{hit}</td>'
                    f'<td class="xw">{m["neff"] if m else "—"}</td><td><span class="fo-pill {cl}">{lab}</span></td></tr>')
    vs = "against other F&O stocks" if relative else "against a normal day"
    head = (f'<tr><th>Reading</th>{"" if relative else "<th>Today</th>"}<th class="xw">5 sessions</th><th>{E.MAIN_H} sessions</th><th class="xw">20 sessions</th>'
            f'<th class="xw">{"Beat the rest" if relative else "Ended higher"}</th><th class="xw">Episodes</th><th>Status</th></tr>')
    return (f'<div class="fo-tw" style="max-height:440px"><table class="fo-t"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="fo-note" style="padding:0 14px 12px">The extra move after each reading, {vs}, acting at the next day\'s close. Proven: a large edge against its noise, '
            'at least 15 separate episodes, and the same direction in both halves of the history. Too few: it has not happened often enough to judge.</div>')


# ───────────── overview ─────────────
def _overview(res: dict) -> str:
    idx = list(res["indexes"].values())
    cards = []
    for x in idx:
        M = x.get("model") or {}
        body = _verdict(x) + _model_tiles(x) + '<div style="height:14px"></div>' + _legend([("What is moving the forecast (share of the move, in %)", "transparent", "")]) \
            + _drivers(M.get("drivers") or [], limit=6)
        cards.append(_card(x["name"], f'{x["days"]:,} sessions of history', body))
    notes = [_card(f'Desk note · {x["name"]}', "written from the numbers by fixed rules", _note(x["note"])) for x in idx]
    return _grid("two", *cards) + _sec("Desk note", "What the data says, in order") + _grid("two", *notes) + _internals(res)


def _internals(res: dict) -> str:
    m = res.get("market") or {}
    t = []
    if _ok(m.get("fii_ls")):
        t.append(_tile("FII long share, index futures", _pct(m["fii_ls"], 0, False), _rank(m.get("fii_ls_pct"))))
    if _ok(m.get("fii_ls_d5")):
        t.append(_tile("FII change, 5 sessions", f'<span class="{_cls(m["fii_ls_d5"])}">{m["fii_ls_d5"] * 100:+.1f} pts</span>', "of their long share"))
    if _ok(m.get("cli_ls")):
        t.append(_tile("Retail long share", _pct(m["cli_ls"], 0, False), _rank(m.get("cli_ls_pct"))))
    if _ok(m.get("fii_opt")):
        t.append(_tile("FII index options, net", f'<span class="{_cls(m["fii_opt"])}">{_qty(m["fii_opt"])}</span>', "contracts · calls less puts"))
    if _ok(m.get("br_above")):
        t.append(_tile("F&O stocks in uptrends", _pct(m["br_above"], 0, False), "above their 50-day average"))
    if _ok(m.get("br_flow")):
        t.append(_tile("Build-up balance", f'<span class="{_cls(m["br_flow"])}">{m["br_flow"] * 100:+.0f} pts</span>', "long less short build-up"))
    st_ = res.get("stocks") or []
    top = " · ".join(_e(s["sym"]) for s in st_[:6])
    bot = " · ".join(_e(s["sym"]) for s in st_[-6:][::-1])
    body = (f'<div class="fo-tiles six" style="margin-top:0">{"".join(t)}</div>' if t else '<div class="fo-empty">Participant positions are not stored yet.</div>')
    if st_:
        body += (f'<div class="fo-tiles" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">'
                 + _tile("Top of the stock model", f'<span class="up" style="font-size:13.5px">{top}</span>', "ranked highest for the next 10 sessions")
                 + _tile("Bottom of the stock model", f'<span class="dn" style="font-size:13.5px">{bot}</span>', "ranked lowest for the next 10 sessions") + '</div>')
    return _sec("Market internals", "Who is positioned how, and where the stock model points") + _grid("", _card("Positioning and breadth", "end of day", body))


# ───────────── index deep dive ─────────────
def _record_table(M: dict, level: int) -> str:
    by = M.get("by") or {}
    if not by:
        return ('<div class="fo-empty">No record yet. The model is only graded on sessions after the first two years of history, so it is never scored on data it learnt from.</div>')
    rows = ""
    for L in (2, 1, 0, -1, -2):
        s = by.get(L)
        name = E.VERDICTS[L][0]
        cl = "g" if L > 0 else "r" if L < 0 else "n"
        if s:
            rows += (f'<tr class="{"now" if L == level else ""}"><td><span class="fo-pill {cl}">{name}</span></td><td class="xw">{s["n"]:,}</td><td class="xw">{s["neff"]}</td>'
                     f'<td class="b">{_col(s["mean"])}</td><td>{s["hit"] * 100:.0f}%</td><td class="xw">{_col(s["edge"])}</td></tr>')
        else:
            rows += f'<tr><td><span class="fo-pill {cl}">{name}</span></td><td class="xw">—</td><td class="xw">—</td><td>—</td><td>—</td><td class="xw">—</td></tr>'
    head = f'<tr><th>Verdict given</th><th class="xw">Sessions</th><th class="xw">Episodes</th><th>Next {E.MAIN_H} sessions</th><th>Ended higher</th><th class="xw">Against normal</th></tr>'
    ic = M.get("ic")
    foot = (f'On {M["oos_days"]:,} sessions the model had not seen, its forecasts and the outcomes had a rank correlation of <b style="color:var(--tx2)">{ic:+.2f}</b> '
            f'(0 is chance, above 0.10 is useful). A healthy record steps down from the top row to the bottom row.') if _ok(ic) else ""
    return f'<div class="fo-tw"><table class="fo-t"><thead>{head}</thead><tbody>{rows}</tbody></table></div><div class="fo-note" style="padding:0 14px 12px">{foot}</div>'


def _strategy(M: dict) -> str:
    c, s = M.get("curve"), M.get("stats") or {}
    if not c or not s.get("model"):
        return '<div class="fo-empty">The test strategy needs about two and a half years of stored history.</div>'
    a, b = s["model"], s["hold"]
    chart = _lines(c["date"], [("Follow the model", AMBER, c["model"], _pct(a.get("total"), 0)), ("Buy and hold the future", GREY, c["hold"], _pct(b.get("total"), 0))],
                   h=190, fmt=lambda v: f"{v * 100:+.0f}%", zero=True)
    row = lambda n, k, f: f'<tr><td class="w" style="min-width:110px">{n}</td><td class="b">{f(a.get(k))}</td><td>{f(b.get(k))}</td></tr>'
    table = ('<table class="fo-t" style="margin-top:10px"><thead><tr><th>Since ' + _day(s.get("from")) + '</th><th>Model</th><th>Buy and hold</th></tr></thead><tbody>'
             + row("Return a year", "cagr", _pct) + row("Worst fall from a peak", "dd", _pct) + row("Return for the risk taken (Sharpe)", "sharpe", lambda v: _num(v, 2))
             + f'<tr><td>Time in the market</td><td class="b">{s["in_market"] * 100:.0f}%</td><td>100%</td></tr>'
             + f'<tr><td>Times it switched</td><td class="b">{s["switches"]}</td><td>0</td></tr></tbody></table>')
    return (chart + table + '<div class="fo-note">The rule: hold the index future, step out when the forecast falls into the bottom 40% of what the model has said before. '
            'Decided after the close, acted on at the next close, 0.05% charged per switch. Only sessions the model had not seen. Taxes and margin interest are not included.</div>')


def _index_view(x: dict, exp_pick: str) -> str:
    M, lv = x.get("model") or {}, x["levels"]
    c = x["counts"]
    head = _card(x["name"], f'{x["days"]:,} sessions of history', _verdict(x) + _model_tiles(x, "six"))
    rec = _card("The model's record, verdict by verdict", "sessions it had not seen", _record_table(M, (M.get("level") if M else 0)), True)
    strat = _card("Would following it have paid?", "test strategy against buy and hold", _strategy(M))
    drv = _card("What is moving the forecast", "each input's share of the forecast, in %", _drivers(M.get("drivers") or []))
    ana = _card("Days like today", "nearest past days across every input", _analogs(M.get("analogs"), x["name"]))
    ch = (x.get("chain") or {}).get("w" if exp_pick == "Weekly" else "m")
    hist = _card("Six months", "end of day", _hist_charts(x.get("hist") or {}))
    lvl = _card("Levels into expiry", f'expiry {_day(lv.get("m_exp"), False)}', _level_bar(lv) + _levels(lv))
    idea = _card("Option structure that fits this view", "defined risk only", _idea(x.get("idea")))
    ev = _card("Every reading and its record", f'{c.get("proven", 0)} proven · {c.get("leaning", 0)} leaning of {sum(c.values())}', _evidence(x["signals"], False), True)
    left = f'<div class="fo-stack">{rec}{drv}{lvl}</div>'
    right = f'<div class="fo-stack">{strat}{ana}{idea}</div>'
    return (_grid("", head) + _sec("The model", "Tested only on sessions that came after the data it learnt from") + _grid("two", left, right)
            + '<!--chain-->' + _sec("Option chain", f'{exp_pick} expiry · where the open interest sits and what was added today') + _chain_cards(ch, lv.get("fut"))
            + _sec("History") + _grid("", hist) + _sec("Tested readings", "Simple rules, each with its own backtest") + _grid("", ev))


def _levels(lv: dict) -> str:
    fut = lv.get("fut")
    t = [_tile("Put-call ratio", _num(lv.get("pcr"), 2), _rank(lv.get("pcr_pct"))), _tile("Implied volatility", _pct(lv.get("iv"), 1, False), _rank(lv.get("iv_pct"))),
         _tile("Futures open interest", _col(lv.get("oi5")), "change over 5 sessions")]
    if _ok(lv.get("move5")) and _ok(fut):
        t.append(_tile("Priced move, 5 sessions", f'±{_px(lv["move5"])}', f'{_px(fut - lv["move5"])} to {_px(fut + lv["move5"])}'))
    if _ok(lv.get("move_exp")) and _ok(fut):
        t.append(_tile("Priced move to expiry", f'±{_px(lv["move_exp"])}', f'{_px(fut - lv["move_exp"])} to {_px(fut + lv["move_exp"])}'))
    if _ok(lv.get("roll")):
        t.append(_tile("Rolled to next month", _pct(lv["roll"], 0, False), "share of futures open interest"))
    if _ok(lv.get("cal")):
        t.append(_tile("Next-month premium", _pct(lv["cal"], 2), "next future against this one"))
    if _ok(lv.get("skew")):
        t.append(_tile("Put skew", f'{lv["skew"] * 100:+.1f} pts', "put volatility less call volatility"))
    if lv.get("w_exp") and _ok(lv.get("w_max_pain")):
        t.append(_tile(f'Weekly max pain · {_day(lv["w_exp"], False)}', _px(lv["w_max_pain"]), f'walls {_px(lv.get("w_put_wall"))} · {_px(lv.get("w_call_wall"))}'))
    return f'<div class="fo-tiles">{"".join(t)}</div>'


# ───────────── stocks ─────────────
FILTERS = ["All", "Top fifth", "Bottom fifth", "Long build-up", "Short build-up", "Short covering", "Long unwinding", "Options expensive", "Options cheap",
           "Open interest up", "Open interest down", "My watchlist"]


def _mine() -> set:
    try:
        raw = list(st.session_state.get("watchlist", [])) + list(st.session_state.get("admin_watchlist", []))
        return {str(s).upper().replace(".NS", "").replace(".BO", "").strip() for s in raw}
    except Exception:
        return set()


def _pick(stocks: list, flt: str) -> list:
    if flt == "Top fifth":
        return [s for s in stocks if s["level"] == 2]
    if flt == "Bottom fifth":
        return [s for s in stocks if s["level"] == -2][::-1]
    if flt in ("Long build-up", "Short build-up", "Short covering", "Long unwinding"):
        return [s for s in stocks if s["buildup"] == flt]
    if flt == "Options expensive":
        return sorted([s for s in stocks if "iv_hi" in s["on"]], key=lambda s: -(s["levels"].get("iv_pct") or 0))
    if flt == "Options cheap":
        return sorted([s for s in stocks if "iv_lo" in s["on"]], key=lambda s: (s["levels"].get("iv_pct") or 0))
    if flt == "Open interest up":
        return sorted([s for s in stocks if _ok(s["levels"].get("oi5"))], key=lambda s: -s["levels"]["oi5"])[:40]
    if flt == "Open interest down":
        return sorted([s for s in stocks if _ok(s["levels"].get("oi5"))], key=lambda s: s["levels"]["oi5"])[:40]
    if flt == "My watchlist":
        mine = _mine()
        return [s for s in stocks if s["sym"] in mine]
    return stocks


def _fifth(s) -> str:
    cl = "g" if s["level"] > 0 else "r" if s["level"] < 0 else "n"
    return f'<span class="fo-pill {cl}">{_e(s["verdict"])}</span>'


def _stock_table(rows: list) -> str:
    if not rows:
        return '<div class="fo-empty">No stock matches this filter today.</div>'
    out = []
    for s in rows:
        lv, bu = s["levels"], s["buildup"]
        bcl = "up" if bu in ("Long build-up", "Short covering") else "dn" if bu else "mu"
        ivp = lv.get("iv_pct")
        out.append(f'<tr><td>{_e(s["sym"])}</td><td class="xw mu">{s.get("rank", "")}</td><td class="l">{_fifth(s)}</td><td class="xw">{_col(s.get("score"), 2)}</td>'
                   f'<td>{_px(lv.get("fut"))}</td><td>{_col(lv.get("r1"))}</td><td class="xw">{_col(lv.get("r5"))}</td><td class="xw">{_col(lv.get("oi5"))}</td>'
                   f'<td class="l {bcl}">{_e(bu) if bu else "—"}</td><td class="xw">{_num(lv.get("pcr"), 2)}</td>'
                   f'<td class="xw">{_pct(lv.get("iv"), 0, False)}</td><td class="xw">{f"{ivp * 100:.0f}" if _ok(ivp) else "—"}</td>'
                   f'<td class="xw">{_pct(lv.get("roll"), 0, False)}</td><td class="xw">{_px(lv.get("max_pain"))}</td></tr>')
    head = ('<tr><th>Stock</th><th class="xw">Rank</th><th class="l">Model</th><th class="xw">Forecast against the rest</th><th>Future</th><th>Today</th>'
            '<th class="xw">5 sessions</th><th class="xw">Open interest, 5 sessions</th><th class="l">Build-up</th><th class="xw">Put-call</th>'
            '<th class="xw">Volatility</th><th class="xw">Vol rank</th><th class="xw">Rolled</th><th class="xw">Max pain</th></tr>')
    return f'<div class="fo-tw"><table class="fo-t"><thead>{head}</thead><tbody>{"".join(out)}</tbody></table></div>'


def _quadrant(stocks: list) -> str:
    pts = [(s, s["levels"].get("r5"), s["levels"].get("oi5")) for s in stocks]
    pts = [(s, x, y) for s, x, y in pts if _ok(x) and _ok(y)]
    if len(pts) < 10:
        return '<div class="fo-empty">Needs a week of stored data.</div>'
    mx = max(0.04, sorted(abs(x) for _, x, _ in pts)[int(len(pts) * 0.97)])
    my = max(0.05, sorted(abs(y) for _, _, y in pts)[int(len(pts) * 0.97)])
    px_ = lambda x: 50 + max(-1, min(1, x / mx)) * 46
    py_ = lambda y: 50 - max(-1, min(1, y / my)) * 44
    far = {id(s) for s, _, _ in sorted(pts, key=lambda p: -((p[1] / mx) ** 2 + (p[2] / my) ** 2))[:14]}
    dots = ""
    for s, x, y in pts:
        colr = "#22C55E" if s["level"] > 0 else "#EF4444" if s["level"] < 0 else "#71717A"
        dots += f'<i style="left:{px_(x):.1f}%;top:{py_(y):.1f}%;background:{colr}" title="{_e(s["sym"])} · price {_pct(x)} · open interest {_pct(y)} · {_e(s["verdict"])}"></i>'
        if id(s) in far:
            dots += f'<em style="left:{px_(x):.1f}%;top:{py_(y):.1f}%">{_e(s["sym"])}</em>'
    q = ('<span class="q" style="right:0;top:0;color:#22C55E">Long build-up</span><span class="q" style="left:0;top:0;color:#EF4444">Short build-up</span>'
         '<span class="q" style="right:0;bottom:0">Short covering</span><span class="q" style="left:0;bottom:0">Long unwinding</span>')
    n = {k: sum(1 for s in stocks if s["buildup"] == k) for k in ("Long build-up", "Short build-up", "Short covering", "Long unwinding")}
    tiles = '<div class="fo-tiles four">' + "".join(_tile(k, str(v), "stocks today") for k, v in n.items()) + '</div>'
    return (f'<div class="fo-quad"><div class="h" style="top:50%"></div><div class="v" style="left:50%"></div>{q}{dots}</div>'
            f'<div class="fo-xl" style="margin-right:0"><span>price fell {_pct(-mx, 0)}</span><span>left to right: price over 5 sessions · bottom to top: open interest over 5 sessions</span>'
            f'<span>price rose {_pct(mx, 0)}</span></div>{_legend([("Model: upper two fifths", "#22C55E", ""), ("Middle", "#71717A", ""), ("Lower two fifths", "#EF4444", "")])}{tiles}')


def _stock_model(res: dict) -> str:
    SM = res.get("stock_model")
    if not SM:
        return '<div class="fo-empty">The stock model needs about two years of stored history before it can be fitted and tested.</div>'
    head = (f'<div class="fo-vd"><b style="font-size:19px">One model, every F&O stock</b>{_conf(SM)}</div>'
            f'<div class="fo-tx">Each day it ranks all stocks on thirteen positioning inputs and forecasts which will beat or lag the rest over {E.MAIN_H} sessions. '
            'It is graded only on sessions it had not seen.</div>')
    if not _ok(SM.get("ic")):
        return head + '<div class="fo-note">No record yet: the first two years of history are used for learning only.</div>'
    q = SM.get("quint") or []
    bars = _bars([1, 2, 3, 4, 5], [("Average result of each fifth, against the average stock", BLUE, q)], h=120, diverge=True, fmt=lambda v: f"{v * 100:+.2f}%",
                 xlabels=["Bottom fifth", "Lower", "Middle", "Upper", "Top fifth"])
    spread = (q[4] - q[0]) if (len(q) == 5 and _ok(q[0]) and _ok(q[4])) else None
    s = SM.get("stats") or {}
    tiles = ('<div class="fo-tiles">' + _tile("Top fifth less bottom fifth", _col(spread, 2), f"per {E.MAIN_H} sessions")
             + _tile("Rank correlation", f'{SM["ic"]:+.3f}', "0 is chance · 0.03 is useful")
             + _tile("Unseen sessions tested", f'{SM["oos_days"]:,}', f'since {_day(s.get("from"))}' if s.get("from") else "") + '</div>')
    curve = ""
    if SM.get("curve"):
        c = SM["curve"]
        curve = ('<div style="height:14px"></div>' + _lines(c["date"], [("Top fifth less bottom fifth", AMBER, c["ls"], _pct((s.get("ls") or {}).get("total"), 0)),
                                                                       ("Top fifth less the average stock", BLUE, c["top"], _pct((s.get("top") or {}).get("total"), 0))],
                                                            h=170, fmt=lambda v: f"{v * 100:+.0f}%", zero=True)
                 + '<div class="fo-note">Rebalanced daily at the next close, before costs. A real portfolio would trade less often and pay costs, so read the shape, not the level.</div>')
    return head + '<div style="height:12px"></div>' + bars + tiles + curve


def _stock_detail(d: dict) -> str:
    lv = d["levels"]
    head = (f'<div class="fo-vd"><b class="{TONE[d["tone"]]}">{_e(d["verdict"])}</b><span>rank {d.get("rank", "—")} · forecast {_pct(d.get("score"), 2)} against the average F&O stock, '
            f'{E.MAIN_H} sessions</span></div><div class="fo-tx">{_e(d["line"])}</div>')
    t = ('<div class="fo-tiles six">' + _tile("Future", _px(lv.get("fut")), f'{_col(lv.get("r1"), 2)} today') + _tile("5 sessions", _col(lv.get("r5")), f'20 sessions {_pct(lv.get("r20"))}')
         + _tile("Open interest, 5 sessions", _col(lv.get("oi5")), _e(d["buildup"] or "no build-up pattern")) + _tile("Put-call ratio", _num(lv.get("pcr"), 2), _rank(lv.get("pcr_pct")))
         + _tile("Implied volatility", _pct(lv.get("iv"), 1, False), _rank(lv.get("iv_pct")))
         + _tile("Put wall · max pain · call wall", f'{_px(lv.get("put_wall"))} · {_px(lv.get("max_pain"))} · {_px(lv.get("call_wall"))}', f'expiry {_day(lv.get("m_exp"), False)}') + '</div>')
    out = _grid("", _card(d["sym"], "single-stock view", head + t))
    bu = (d.get("hist") or {}).get("bu") or []
    strip = ""
    if bu:
        strip = ('<div class="fo-note" style="margin-top:14px">Build-up pattern, last ' + str(len(bu)) + ' sessions (oldest left)</div><div class="fo-bu">'
                 + "".join(f'<i style="background:{BU_COL.get(v, BU_COL[0])}" title="{BU_NAME.get(v, "")}"></i>' for v in bu) + '</div>'
                 + _legend([(BU_NAME[k], BU_COL[k], "") for k in (1, 2, 3, 4)]))
    out += _grid("two", _card("Why the model ranks it here", "each input's share, in % against the rest", _drivers(d.get("drivers") or []) + strip),
                 _card("Days like today, this stock", "its own history", _analogs(d.get("analogs"), d["sym"])))
    if d.get("hist"):
        out += _grid("", _card("Six months", "end of day", _hist_charts(d["hist"])))
    c = d.get("chain")
    if c:
        out += _chain_cards(c, lv.get("fut"))
    on = [s for s in d.get("signals", []) if s.get("on")]
    if on:
        out += _grid("", _card("Tested readings on today", "against other F&O stocks", _evidence(on, True), True))
    return out


# ───────────── participants ─────────────
def _participants(res: dict) -> str:
    P, H = res.get("participants"), res.get("part_hist") or {}
    if not P or not P.get("rows"):
        return _grid("", _card("Who holds what", "", '<div class="fo-empty">Participant positions are not stored yet. They arrive with the next run of the F&O data workflow.</div>', True))
    d2 = lambda v: f'<span class="{_cls(v)}">{_qty(v)}</span>'
    rows = ""
    for r in P["rows"]:
        rows += (f'<tr><td>{_e(r["who"])}</td><td class="b">{r["ls"] * 100:.0f}%</td><td>{d2(r["idx_fut"])}</td><td class="xw">{d2(r["d_idx_fut"])}</td>'
                 f'<td>{d2(r["idx_call"])}</td><td>{d2(r["idx_put"])}</td><td class="xw">{d2(r["stk_fut"])}</td><td class="xw">{d2(r["d_stk_fut"])}</td></tr>')
    head = ('<tr><th>Group</th><th>Long share, index futures</th><th>Index futures, net</th><th class="xw">5-session change</th><th>Index calls, net</th>'
            '<th>Index puts, net</th><th class="xw">Stock futures, net</th><th class="xw">5-session change</th></tr>')
    table = (f'<div class="fo-tw"><table class="fo-t"><thead>{head}</thead><tbody>{rows}</tbody></table></div><div class="fo-note" style="padding:0 14px 12px">Net is contracts held long '
             'less contracts held short. Green is net long, red is net short. Net long puts is a bearish position; net short puts is a bullish one.</div>')
    out = _grid("", _card("Who holds what", f'NSE participant data · {_day(P["date"])}', table, True))
    d = H.get("date") or []
    if len(d) > 5:
        c1 = _lines(d, [("FIIs", AMBER, H.get("fii_ls") or [], ""), ("Retail clients", BLUE, H.get("cli_ls") or [], ""), ("Pro desks", "#A78BFA", H.get("pro_ls") or [], "")],
                    h=190, fmt=lambda v: f"{v * 100:.0f}%")
        c2 = _lines(d, [("FII index futures, net contracts", AMBER, H.get("fii_net") or [], "")], h=190, fmt=_qty, zero=True)
        c3 = _lines(d, [("FII index options, net (calls less puts)", BLUE, H.get("fii_opt") or [], "")], h=190, fmt=_qty, zero=True)
        out += _grid("three", _card("Long share of index futures", "six months", c1), _card("FII index futures", "six months", c2), _card("FII index options", "six months", c3))
    return out


# ───────────── method ─────────────
def _method(res: dict) -> str:
    notes = "".join(f"<div>· {_e(n)}</div>" for n in res.get("notes", []))
    P = [("What it reads", "The daily NSE files saved in your repository: futures prices and open interest, option open interest by strike, closing premiums, and the "
                           "positions NSE publishes for FIIs, retail clients, pro desks and DIIs. End of day only."),
         ("The model", "A ridge regression, the plainest forecasting model there is: the next 10 sessions' return explained by about twenty measured inputs, with a penalty "
                       "that stops it learning noise. One model per index, one shared model for all stocks. For stocks it forecasts the return against the average F&O stock."),
         ("How it is tested", "Walk-forward. Every quarter the model is re-learnt using only data from before that day, then used for the next quarter. All records, tables and "
                              "curves on this page come from those unseen sessions. Trades are assumed at the next day's close, never the same close."),
         ("When it gets a say", "The verdict is taken from the model only if its forecasts lined up with outcomes on unseen sessions. If they did not, the page says so and "
                                "shows 'No proven edge' while still showing the positioning."),
         ("Days like today", "The past days closest to today across all inputs, weighted by how much each input matters to the model, kept at least 10 sessions apart."),
         ("Tested readings", "Simple yes-or-no rules (long build-up, put-call ratio very high, and so on), each with its own backtest. With about thirty under test, roughly "
                             "one useless rule in a hundred can pass as proven by luck."),
         ("Dealer gamma", "An estimate from open interest and option prices, assuming dealers sold the puts and bought the calls customers hold. The real split is not published."),
         ("What is not in it", "Costs beyond the 0.05% per switch in the index test, taxes, margin, intraday moves, news and results dates. A verdict is odds, not a promise. "
                               "This is analysis of public data, not investment advice.")]
    body = '<div class="fo-note2">' + "".join(f"<div><small>{_e(a)}</small>{_e(b)}</div>" for a, b in P) + f'</div><div class="fo-note">{notes}</div>'
    cards = [_card("How this desk works", "", body)]
    wt = []
    for x in res["indexes"].values():
        M = x.get("model")
        if M and M.get("betas"):
            wt.append((x["name"], M["betas"]))
    SM = res.get("stock_model")
    if SM and SM.get("betas"):
        wt.append(("F&O stocks", SM["betas"]))
    if wt:
        keys = []
        for _, bs in wt:
            keys += [b["key"] for b in bs if b["key"] not in keys]
        look = [{b["key"]: b for b in bs} for _, bs in wt]
        rows = ""
        for k in keys:
            lab = next(l[k]["label"] for l in look if k in l)
            rows += f"<tr><td>{_e(lab)}</td>" + "".join(f'<td>{_col(l[k]["beta"], 2) if k in l else "—"}</td>' for l in look) + "</tr>"
        table = ('<div class="fo-tw"><table class="fo-t"><thead><tr><th>Input</th>' + "".join(f"<th>{_e(n)}</th>" for n, _ in wt) + f'</tr></thead><tbody>{rows}</tbody></table></div>'
                 '<div class="fo-note" style="padding:0 14px 12px">The effect on the 10-session forecast when an input is one notch (one standard deviation) above normal. '
                 'Green: a high reading has been followed by gains. Red: by losses.</div>')
        cards.append(_card("The model's weights today", "learnt from the full history", table, True))
    return _grid("two" if len(cards) > 1 else "", *cards)


# ───────────── signals ─────────────
TIER = {"on": ("Approved", "g"), "trial": ("On trial", "a"), "off": ("Switched off", "n")}


def _tier_of(S_, trusted) -> str:
    return "off" if (not S_ or not trusted) else S_.get("tier", "off")


def _rec_row(name, S_, trusted) -> str:
    st_ = (S_ or {}).get("stats") or {}
    lab, cl = TIER[_tier_of(S_, trusted)]
    if not st_.get("n"):
        return f'<tr><td>{_e(name)}</td><td class="l"><span class="fo-pill n">No record yet</span></td>' + "<td>—</td>" * 4 + '<td class="xw">—</td>' * 4 + "</tr>"
    return (f'<tr><td>{_e(name)}</td><td class="l"><span class="fo-pill {cl}">{lab}</span></td><td>{st_["n"]}</td><td>{st_["win"] * 100:.0f}%</td>'
            f'<td class="b">{_col(st_["exp"], 2)}</td><td>{st_["pf"]:.2f}</td><td class="xw">{_col(st_.get("edge"), 2)}</td><td class="xw">{_col(st_["avg_win"])} / {_col(st_["avg_loss"])}</td>'
            f'<td class="xw">{_col(st_["worst"])}</td><td class="xw">{st_["losing_run"]} in a row</td></tr>')


def _sig_status(res: dict) -> str:
    rows = ""
    for x in res["indexes"].values():
        M = x.get("model") or {}
        T, tr = M.get("trade") or {}, M.get("conf_n", 0) >= 1
        rows += _rec_row(f'{x["name"]} · buy', T.get("long"), tr) + _rec_row(f'{x["name"]} · short', T.get("short"), tr)
    ST = res.get("stock_trade")
    rows += _rec_row("Stocks · buy (ten-stock portfolio)", ST, bool(ST and ST.get("trusted")))
    head = ('<tr><th>Signal</th><th class="l">Status</th><th>Past trades</th><th>Won</th><th>Per trade, after costs</th><th>Money won ÷ lost</th>'
            '<th class="xw">Against just holding</th><th class="xw">Average win / loss</th><th class="xw">Worst trade</th><th class="xw">Longest losing run</th></tr>')
    return (f'<div class="fo-tw"><table class="fo-t"><thead>{head}</thead><tbody>{rows}</tbody></table></div><div class="fo-note" style="padding:0 14px 12px">'
            '<b style="color:var(--tx2)">Approved</b>: on sessions the model had never seen, this signal made money after costs, won at least 1.2 times what it lost, and beat simply '
            'holding by more than luck explains. <b style="color:var(--tx2)">On trial</b>: it made money and beat holding, but not by enough to rule out luck; paper trade it or use '
            'very small size. <b style="color:var(--tx2)">Switched off</b>: it failed, or the model behind it has no record. Switched-off signals are never shown as a trade.</div>')


def _plan_tiles(c: dict, lot=None, cap=None, risk=None) -> str:
    t = []
    if _ok(c.get("entry")):
        t.append(_tile("Entry" if c.get("since") else "Entry reference", _px(c["entry"]), f'since {_day(c["since"], False)}' if c.get("since") else "last close · act at the next close"))
    if _ok(c.get("stop")):
        t.append(_tile("Stop (closing basis)", f'<span class="dn">{_px(c["stop"])}</span>', "exit if a session closes beyond this"))
    if _ok(c.get("target")):
        t.append(_tile("Target", f'<span class="up">{_px(c["target"])}</span>', "book the trade here"))
    if _ok(c.get("pnl")):
        t.append(_tile("Result so far", _col(c["pnl"], 2), f'{c.get("bars", 0)} sessions held'))
    if c.get("left") is not None and c.get("since"):
        t.append(_tile("Time limit", f'{c["left"]} sessions left', "after that the signal must still be on to stay in"))
    if _ok(c.get("entry")) and _ok(c.get("stop")) and cap and risk and not c.get("since"):
        per = abs(c["entry"] - c["stop"])
        q = (cap * risk / 100) / per if per > 0 else 0
        if _ok(lot) and lot > 0:
            t.append(_tile("Size for your risk", f'{int(q // lot)} lot{"" if int(q // lot) == 1 else "s"}', f'lot {lot:,.0f} · one lot risks ₹{per * lot:,.0f}'))
        else:
            t.append(_tile("Size for your risk", f'{int(q):,} shares', f'risks ₹{cap * risk / 100:,.0f}'))
    return f'<div class="fo-tiles">{"".join(t)}</div>' if t else ""


def _index_signal(x: dict, cap, risk) -> str:
    c, M = x.get("call") or {}, x.get("model") or {}
    tier = c.get("tier")
    tag = f'<span class="fo-pill {TIER[tier][1]}" style="margin-left:auto">{TIER[tier][0]}</span>' if tier else ""
    head = f'<div class="fo-vd"><b class="{TONE.get(c.get("tone", "flat"), "fl")}">{_e(c.get("word", "No signal"))}</b><span>{_e(x["name"])} future</span>{tag}</div>'
    body = head + f'<div class="fo-tx">{_e(c.get("why", ""))}</div>' + _plan_tiles(c, x["levels"].get("lot"), cap, risk)
    if tier == "trial":
        body += '<div class="fo-note"><b class="am">On trial.</b> This signal has not yet beaten luck in testing. Do not put meaningful money on it.</div>'
    T = M.get("trade") or {}
    S_ = T.get(c.get("side") or "long")
    if S_ and S_["stats"].get("n"):
        cv = S_["curve"]
        nm = "Buy signals only" if S_["side"] == 1 else "Short signals only"
        body += ('<div style="height:14px"></div>' + _lines(cv["date"], [(nm, AMBER, cv["sys"], _pct(cv["sys"][-1], 0)), ("Always in the position", GREY, cv["hold"], _pct(cv["hold"][-1], 0))],
                                                            h=150, fmt=lambda v: f"{v * 100:+.0f}%", zero=True))
        rows = "".join(f'<tr><td>{_day(t["in"])}</td><td class="xw">{_px(t["in_px"])}</td><td>{_day(t["out"], False)}</td><td class="xw">{_px(t["out_px"])}</td>'
                       f'<td class="b">{_col(t["ret"], 2)}</td><td class="l xw">{_e(t["why"])}</td></tr>' for t in S_["trades"][:8])
        body += (f'<div class="fo-tw" style="max-height:250px;margin-top:10px"><table class="fo-t"><thead><tr><th>Entered</th><th class="xw">At</th><th>Exited</th><th class="xw">At</th>'
                 f'<th>Result after costs</th><th class="l xw">Why it ended</th></tr></thead><tbody>{rows}</tbody></table></div>')
    return _card(f'{x["name"]} signal', "end of day · act at the next close", body)


def _stock_signals(res: dict, cap, risk) -> str:
    ST = res.get("stock_trade")
    if not ST:
        return _grid("", _card("Stock signals", "", '<div class="fo-empty">Stock signals need about two and a half years of stored history before they can be replayed and judged.</div>', True))
    tier = _tier_of(ST, ST.get("trusted"))
    lab, cl = TIER[tier]
    st_, pf_, bn = ST["stats"], ST.get("port") or {}, ST.get("bench") or {}
    rec = ('<div class="fo-tiles six" style="margin-top:0">' + _tile("Status", f'<span class="fo-pill {cl}">{lab}</span>', f'tested since {_day(ST.get("from"))}')
           + _tile("Past trades", f'{st_.get("n", 0):,}', f'{st_.get("win", 0) * 100:.0f}% won · held {st_.get("bars") or 0:.0f} sessions')
           + _tile("Per trade, after costs", _col(st_.get("exp"), 2), f'money won ÷ lost {st_.get("pf", 0):.2f}')
           + _tile("Portfolio, a year", _col(pf_.get("cagr")), f'average F&O stock {_pct(bn.get("cagr"))}')
           + _tile("Worst fall from a peak", _col(pf_.get("dd")), f'average F&O stock {_pct(bn.get("dd"))}')
           + _tile("Money at work", f'{ST.get("invested", 0) * 100:.0f}%', f'of {ST["slots"]} equal slots, on average') + '</div>')
    cv = ST["curve"]
    chart = _lines(cv["date"], [("Signal portfolio", AMBER, cv["sys"], _pct(cv["sys"][-1], 0)), ("Average F&O stock", GREY, cv["bench"], _pct(cv["bench"][-1], 0))],
                   h=170, fmt=lambda v: f"{v * 100:+.0f}%", zero=True)
    out = _grid("", _card("Stock buy signals: the record", "ten-stock test portfolio on unseen sessions, after costs", rec + '<div style="height:14px"></div>' + chart))
    if tier == "off":
        why = ("the stock model has not shown skill on unseen sessions" if not ST.get("trusted") else "the replay did not make enough money after costs, or did not beat holding the average stock")
        out += _grid("", _card("Buy · Hold · Sell", "switched off", f'<div class="fo-empty"><b>No stock buy signals.</b> They are switched off because {why}. '
                                                                 'The stay-away list below is still shown, as a caution list.</div>', True))
    else:
        warn = '<div class="fo-note" style="padding:0 14px 10px"><b class="am">On trial.</b> These signals have not yet beaten luck in testing. Paper trade them or use very small size.</div>' if tier == "trial" else ""

        def size(b):
            per = abs(b["entry"] - b["stop"])
            if not (cap and risk and per > 0 and b["entry"] > 0):
                return "—"
            return f'{int(min((cap * risk / 100) / per, cap / E.SLOTS / b["entry"])):,}'          # the smaller of: your risk limit, a tenth of your capital
        buy = "".join(f'<tr><td>{_e(b["sym"])}</td><td>{_px(b["entry"])}</td><td class="dn">{_px(b["stop"])}</td><td class="up">{_px(b["target"])}</td>'
                      f'<td class="xw">{(b["rank"] or 0) * 100:.0f}</td><td class="xw">{_pct(-E.SIG_STOP * b["swing"])} / {_pct(E.SIG_TARGET * b["swing"])}</td><td>{size(b)}</td></tr>' for b in ST["buy"])
        buy = (f'<div class="fo-tw"><table class="fo-t"><thead><tr><th>Buy at the next close</th><th>Reference</th><th>Stop</th><th>Target</th><th class="xw">Model rank</th>'
               f'<th class="xw">Risk / reward</th><th>Shares for your risk</th></tr></thead><tbody>{buy}</tbody></table></div>' if ST["buy"]
               else '<div class="fo-empty">No new buy today' + ("." if ST.get("market_ok") else ": the market verdict is negative, so no new stock is bought.") + '</div>')
        hold = [h for h in ST["hold"] if not h["sell"]]
        sell = [h for h in ST["hold"] if h["sell"]]
        hrow = lambda h: (f'<tr><td>{_e(h["sym"])}</td><td class="xw">{_day(h["since"], False)}</td><td class="xw">{_px(h["entry"])}</td><td>{_px(h["price"])}</td><td class="b">{_col(h["pnl"])}</td>'
                          f'<td class="dn">{_px(h["stop"])}</td><td class="up">{_px(h["target"])}</td><td class="xw">{(h["rank"] or 0) * 100:.0f}</td><td class="xw">{h["left"]}</td></tr>')
        hh = ('<tr><th>Hold</th><th class="xw">Bought</th><th class="xw">At</th><th>Now</th><th>Result</th><th>Stop</th><th>Target</th><th class="xw">Model rank</th>'
              '<th class="xw">Sessions left</th></tr>')
        holdt = (f'<div class="fo-tw"><table class="fo-t"><thead>{hh}</thead><tbody>{"".join(hrow(h) for h in hold)}</tbody></table></div>' if hold
                 else '<div class="fo-empty">No open positions.</div>')
        srows = "".join(f'<tr><td>{_e(h["sym"])}</td><td>Sell at the next close</td><td class="b">{_col(h["pnl"])}</td><td class="l">{_e(h["sell"])}</td></tr>' for h in sell)
        srows += "".join(f'<tr><td>{_e(t["sym"])}</td><td>Sold at today\'s close</td><td class="b">{_col(t["ret"])}</td><td class="l">{_e(t["why"])}</td></tr>' for t in ST["sold"])
        sellt = (f'<div class="fo-tw"><table class="fo-t"><thead><tr><th>Sell</th><th>When</th><th>Result</th><th class="l">Why</th></tr></thead><tbody>{srows}</tbody></table></div>'
                 if srows else '<div class="fo-empty">Nothing to sell today.</div>')
        n_buy, n_sell = len(ST["buy"]), len(sell) + len(ST["sold"])
        out += _grid("two", f'<div class="fo-stack">{_card("Buy", str(n_buy) + " new today", buy + warn, True)}{_card("Sell", str(n_sell) + " today", sellt, True)}</div>',
                     _card("Hold", f'{len(hold)} of {ST["slots"]} slots in use', holdt, True))
    av = "".join(f'<tr><td>{_e(a["sym"])}</td><td>{_px(a["price"])}</td><td>{(a["rank"] or 0) * 100:.0f}</td><td class="l">Bottom fifth of the model and below its 50-day average</td></tr>' for a in ST["avoid"])
    q = (res.get("stock_model") or {}).get("quint") or []
    note = (f'On unseen sessions the bottom fifth moved {_pct(q[0], 2)} against the average F&O stock per {E.MAIN_H} sessions.' if (q and _ok(q[0])) else "")
    avt = (f'<div class="fo-tw" style="max-height:320px"><table class="fo-t"><thead><tr><th>Stay away</th><th>Price</th><th>Model rank</th><th class="l">Why</th></tr></thead><tbody>{av}</tbody></table></div>'
           if av else '<div class="fo-empty">No stock is in the bottom fifth and in a downtrend today.</div>')
    out += _grid("", _card("Stay away", "do not buy · if you hold one, tighten your stop", avt + f'<div class="fo-note" style="padding:0 14px 12px">{note} '
                           'This is a caution list, not a short-selling signal.</div>', True))
    return out


def _sig_rules() -> str:
    R = [("Index: when to buy", f"The model's verdict is Stay invested or Rally setup, and no trade is open. You act at the next session's close."),
         ("Index: where the stop and target go", f"Stop: {E.SIG_STOP:.0f} normal 10-session swing below your entry. Target: {E.SIG_TARGET:.0f} swings above. The swing is measured from the last 60 sessions. "
                                                "Both are checked on the closing price, so a spike during the day does not count."),
         ("Index: when to sell", f"The first of: a close below the stop; a close above the target; the verdict turns to Reduce risk or Correction risk; or {E.SIG_HOLD} sessions have passed "
                                 "and the verdict is no longer positive. Short signals are the mirror image."),
         ("Stocks: what gets bought", f"Top fifth of the stock model, above its 50-day average, and the NIFTY verdict is not negative. At most {E.SLOTS} stocks, a tenth of the money in each, "
                                      "best rank first."),
         ("Stocks: when to sell", f"The first of: a close below the stop; a close above the target; model rank falls below 40 of 100; or {E.SIG_HOLD} sessions have passed and it has left the top fifth."),
         ("Costs in the test", f"{E.IDX_COST * 100:.2f}% a side on the index future and {E.STK_COST * 100:.2f}% a side on stocks, for brokerage, taxes and slippage."),
         ("How much to trade", "Decide what you can lose on one trade (1% of capital is a common ceiling). Size = that amount ÷ (entry − stop), and for stocks never more than a tenth of your "
                              "capital in one name. The tables do this sum for the numbers you enter; the numbers stay in your browser session."),
         ("What this cannot promise", "The test covers a few years and a few dozen index trades. Losing runs happen even to systems that work; the table shows the longest one in testing. "
                                      "Prices are futures closes; your fills will differ. This is analysis of public data, not investment advice.")]
    return '<div class="fo-note2">' + "".join(f"<div><small>{_e(a)}</small>{_e(b)}</div>" for a, b in R) + "</div>"


def _signals_tab(res: dict):
    idx = list(res["indexes"].values())
    _html(_grid("", _card("Which signals are switched on", "each kind of signal replayed trade by trade on sessions the model had never seen", _sig_status(res), True)))
    c1, c2, _ = st.columns([1, 1, 2])
    with c1:
        cap = st.number_input("Your trading capital (₹)", min_value=0, value=int(st.session_state.get("fo_cap", 100000)), step=10000, key="fo_cap")
    with c2:
        risk = st.number_input("Most you will lose on one trade (%)", min_value=0.1, max_value=5.0, value=float(st.session_state.get("fo_risk", 1.0)), step=0.1, key="fo_risk")
    _html(_sec("Index", "One instruction per index, with the plan") + _grid("two", *[_index_signal(x, cap, risk) for x in idx])
          + _sec("Stocks", "What to buy, what to hold, what to sell, what to stay away from") + _stock_signals(res, cap, risk)
          + _sec("The rules", "Exactly what the signals do, so you can check them yourself") + _grid("", _card("How every signal is made", "fixed rules, no judgement calls", _sig_rules())))



# ───────────── page ─────────────
def _html(body: str):
    st.markdown(f'<div class="fo">{body}</div>', unsafe_allow_html=True)


def render_fno(supabase=None, on_open=None):
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        with st.spinner("Reading the stored F&O history…"):
            res = E.run_cached()
    except Exception as e:
        _html(f'<div class="fo-card"><div class="fo-empty"><b>The F&O history could not be read.</b><br>{_e(type(e).__name__)}: {_e(str(e)[:200])}</div></div>')
        return
    if not res.get("ok") or not res.get("indexes"):
        msg = " ".join(res.get("notes") or []) or "No F&O history found yet."
        _html('<div class="fo-card"><div class="fo-empty"><b>No F&O data to analyse yet.</b><br>' + _e(msg)
              + '<br>In GitHub open Actions, choose "F&O data", press Run workflow, type 5 in the years box and wait for the green tick. '
                'The page fills in on its own after that.</div></div>')
        return

    idx = list(res["indexes"].values())
    SM = res.get("stock_model") or {}
    chips = (f'<span class="fo-chip">Data to <b>{_day(res["asof"])}</b></span><span class="fo-chip"><b>{res["days"]:,}</b> sessions of history</span>'
             f'<span class="fo-chip"><b>{res.get("n_stocks", 0)}</b> F&O stocks</span>'
             + "".join(f'<span class="fo-chip">{_e(x["name"])} model <b>{_e((x.get("model") or {}).get("conf", "untested").lower())}</b></span>' for x in idx)
             + (f'<span class="fo-chip">Stock model <b>{_e(SM.get("conf", "untested").lower())}</b></span>' if SM else "")
             + '<span class="fo-chip">End of day · updates after 8 pm</span>')
    _html(f'<div class="fo-chips">{chips}</div>')

    tabs = ["Signals", "Overview"] + [x["name"] for x in idx] + ["Stocks", "Participants", "Method"]
    tab = st.pills("View", tabs, default="Signals", key="fo_tab", label_visibility="collapsed") or "Signals"

    if tab == "Signals":
        _signals_tab(res)
    elif tab == "Overview":
        _html(_overview(res))
    elif tab in [x["name"] for x in idx]:
        x = next(v for v in idx if v["name"] == tab)
        has_w = bool((x.get("chain") or {}).get("w"))
        exp_pick = "Monthly"
        body = _index_view(x, "Monthly")
        top, rest = body.split("<!--chain-->", 1)
        _html(top)
        if has_w:
            exp_pick = st.pills("Expiry", ["Monthly", "Weekly"], default="Monthly", key=f"fo_exp_{x['sym']}", label_visibility="collapsed") or "Monthly"
            if exp_pick == "Weekly":
                rest = _index_view(x, "Weekly").split("<!--chain-->", 1)[1]
        _html(rest)
    elif tab == "Stocks":
        stocks = res.get("stocks", [])
        _html(_grid("two", _card("The stock model and its record", "sessions it had not seen", _stock_model(res)),
                    _card("Price and open interest map", f'{len(stocks)} stocks · last 5 sessions', _quadrant(stocks))))
        flt = st.pills("Stocks", FILTERS, default="All", key="fo_flt", label_visibility="collapsed") or "All"
        rows = _pick(stocks, flt)
        _html(_grid("", _card("F&O stocks", f"{len(rows)} shown · ranked by the model", _stock_table(rows), True)))
        c1, c2 = st.columns([3, 1])
        with c1:
            sel = st.selectbox("Open one stock", sorted(s["sym"] for s in stocks), index=None, placeholder="Open one stock for the full picture", key="fo_sym",
                               label_visibility="collapsed")
        with c2:
            if sel and on_open and st.button("Open in research", key="fo_open", use_container_width=True):
                try:
                    on_open(sel)
                except Exception:
                    pass
        if sel:
            d = E.stock_detail(res, sel)
            if d:
                _html(_stock_detail(d))
        _html(_sec("Tested readings for stocks", "Simple rules, each with its own backtest across all stocks")
              + _grid("", _card("Every reading and its record · F&O stocks", "against other F&O stocks, day by day", _evidence(res.get("stock_signals", []), True), True)))
    elif tab == "Participants":
        _html(_participants(res) + _internals(res))
    else:
        _html(_method(res))


# ───────────── compact rows for Home ─────────────
def home_rows(wait: float = 2.0) -> list | None:
    """What Home shows: one entry per index. Waits a moment for the engine; returns None if it is still reading the history."""
    import time
    res, t0 = E.run_soon(), time.time()
    while res is None and time.time() - t0 < wait:
        time.sleep(0.1)
        res = E.run_soon()
    if res is None:
        return None
    out = []
    for x in (res.get("indexes") or {}).values():
        lv, M = x["levels"], x.get("model") or {}
        top = next((d for d in (M.get("drivers") or []) if d.get("x") is not None), None)
        out.append({"name": x["name"], "verdict": x["verdict"], "tone": x["tone"], "asof": res.get("asof"), "pcr": lv.get("pcr"), "iv": lv.get("iv"),
                    "max_pain": lv.get("max_pain"), "put_wall": lv.get("put_wall"), "call_wall": lv.get("call_wall"), "move5": lv.get("move5"), "fut": lv.get("fut"),
                    "proven": x["counts"].get("proven", 0), "reason": (top["label"] if top else ""), "forecast": M.get("forecast"), "conf": M.get("conf", "Untested"),
                    "p_up": M.get("p_up"), "signal": (x.get("call") or {}).get("word", "")})
    return out
