"""
Arka Trades · F&O and signal desk (fno_page.py) v3

A dense, numbers-first desk. It does no heavy work: everything on it is read from the small files that the "Signals"
workflow writes every evening into arka_data/signals/ (see signal_engine.py for the rules and the tests behind them).

  Desk         orders for the next open, the state of each book, market and big-player snapshot
  Leaders      the trend book: 20 strongest stocks, weekly changes, full record against NIFTY 500, charts
  Swing        leader-pullback trades: new signals with stops and sizes, open trades, record
  Index        NIFTY 50 and BANK NIFTY: dip signal, record, futures and option levels, open interest by strike
  Big players  FII / prop / retail / DII positions, futures build-ups, delivery footprints, bulk and block deals
  Options map  the earlier positioning views (option chain, gamma, history, F&O stock table) from fno_legacy.py
  Evidence     every idea that was tested, kept or rejected, with its numbers; rules; limits; the live log

Sizes are worked out from the capital and risk you type on the page; those numbers stay in your browser session.
Nothing on this page is investment advice.

Other pages still import a few things from here: home_rows() and home_desk() (Home), signal_for() (Research), and CSS /
_stock_detail, which are passed through from fno_legacy.py when that file is installed.
"""
from __future__ import annotations

import csv
import html
import json
import math
from datetime import date
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE / "arka_data" / "signals"
FNO = HERE / "arka_data" / "fno"
GREEN, RED, AMBER, BLUE, VIOLET = "var(--green,#30D158)", "var(--red,#FF453A)", "var(--amber,#FF9F0A)", "var(--cyan,#5AC8FA)", "var(--purple,#BF5AF2)"
STATUS = {"on": ("Approved", "g"), "trial": ("On trial", "a"), "off": ("Switched off", "n")}
TABS = ["Desk", "Leaders", "Swing", "Index", "Big players", "Options map", "Evidence"]
_LEGACY = ("CSS", "_stock_detail")


def __getattr__(name):
    """CSS and _stock_detail belong to the earlier page (now fno_legacy.py); the Research page still asks for them here."""
    if name in _LEGACY:
        try:
            import fno_legacy
        except ImportError:
            raise AttributeError(name) from None
        return getattr(fno_legacy, name)
    raise AttributeError(name)


CSS3 = """<style>
.fd{font-family:var(--sans,'Inter',system-ui,sans-serif);font-variant-numeric:tabular-nums;container-type:inline-size;width:100%;color:var(--tx);font-size:12.5px;line-height:1.35;}
.fd *{box-sizing:border-box;}
.fd .up{color:var(--green,#30D158);} .fd .dn{color:var(--red,#FF453A);} .fd .mu{color:var(--tx3);} .fd .am{color:var(--amber,#FF9F0A);} .fd .fl{color:var(--tx2);}
.fd-tk{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border:1px solid var(--line);border-radius:6px;background:var(--s1);overflow:hidden;margin-bottom:8px;}
.fd-tk>div{padding:6px 11px;box-shadow:0 0 0 .5px var(--line);min-width:0;}
.fd-tk small{display:block;font:500 10.5px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd-tk b{display:block;font:600 13px/1.3 var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd-g{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;margin-bottom:8px;align-items:stretch;}
.fd-col{display:flex;flex-direction:column;gap:8px;min-width:0;}
.fd-p{background:var(--s1);border:1px solid var(--line);border-radius:6px;min-width:0;display:flex;flex-direction:column;overflow:hidden;}
.fd-p>header{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:5px 10px;border-bottom:1px solid var(--line);background:var(--s2);min-height:28px;}
.fd-p>header b{font:600 12px var(--sans);color:var(--tx);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd-p>header b::before{content:"";display:inline-block;width:6px;height:6px;border-radius:1px;background:var(--amber,#FF9F0A);margin-right:7px;vertical-align:1px;}
.fd-p>header span{font:400 11px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;}
.fd-b{padding:10px;flex:1;min-width:0;}
.fd-b.f{padding:0;}
.fd-sc{overflow:auto;max-height:470px;}
.fd-t{width:100%;border-collapse:collapse;font:400 12px var(--sans);margin:0;}
.fd-t th{position:sticky;top:0;z-index:1;background:var(--s1);text-align:right;font:500 10.5px var(--sans);color:var(--tx3);padding:5px 8px;border:0;border-bottom:1px solid var(--line);white-space:nowrap;}
.fd-t td{text-align:right;padding:4px 8px;border:0;border-bottom:1px solid var(--line);white-space:nowrap;color:var(--tx2);vertical-align:middle;}
.fd-t th:first-child,.fd-t td:first-child{padding-left:10px;} .fd-t th:last-child,.fd-t td:last-child{padding-right:10px;}
.fd-t tr:last-child td{border-bottom:0;} .fd-t tbody tr:hover td{background:rgba(127,127,127,.06);}
.fd-t .l{text-align:left;} .fd-t td.b{color:var(--tx);font-weight:600;} .fd-t td.w{white-space:normal;min-width:130px;}
.fd .s{display:inline-flex;align-items:baseline;gap:6px;max-width:100%;min-width:0;} .fd .s b{color:var(--tx);font-weight:600;}
.fd .s small{display:none;font:400 10.5px var(--sans);color:var(--tx3);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.fd .s2{display:block;text-align:left;} .fd .s2 b{display:block;color:var(--tx);font-weight:600;} .fd .s2 small{display:block;font:400 10.5px var(--sans);color:var(--tx3);white-space:nowrap;}
.fd .s.w small{max-width:250px;}
.fd .dot{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:7px;}
.fd-oc{display:block;} .fd-oc>div{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:2px 9px;align-items:center;padding:7px 10px;border-bottom:1px solid var(--line);}
.fd-oc>div:last-child{border-bottom:0;} .fd-oc b{font:600 13px var(--sans);color:var(--tx);} .fd-oc .v{font:600 13px var(--sans);color:var(--tx);text-align:right;}
.fd-oc small{grid-column:2 / 4;font:400 11px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd .wide{display:none;}
.fd-t tr.dim td{opacity:.55;}
.fd-s{display:inline-flex;align-items:center;gap:5px;padding:1px 7px;border-radius:10px;font:500 10.5px var(--sans);border:1px solid var(--line2);color:var(--tx2);white-space:nowrap;}
.fd-s i{width:6px;height:6px;border-radius:50%;background:var(--tx4,#52525B);} .fd-s.g i{background:var(--green,#30D158);} .fd-s.a i{background:var(--amber,#FF9F0A);}
.fd-a{display:inline-block;min-width:40px;text-align:center;padding:1px 6px;border-radius:4px;font:600 10.5px var(--sans);background:var(--s3);color:var(--tx2);}
.fd-a.b{background:rgba(48,209,88,.14);color:var(--green,#30D158);} .fd-a.s{background:rgba(255,69,58,.14);color:var(--red,#FF453A);} .fd-a.w{background:rgba(255,159,10,.14);color:var(--amber,#FF9F0A);}
.fd-ks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border:1px solid var(--line);border-radius:6px;background:var(--s1);overflow:hidden;margin-bottom:8px;}
.fd-ks.in{border:0;border-radius:0;margin:0;background:none;}
.fd-k{padding:7px 10px;box-shadow:0 0 0 .5px var(--line);min-width:0;}
.fd-k small{display:block;font:500 10.5px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd-k b{display:block;font:600 15.5px/1.3 var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd-k em{display:block;font:400 10.5px var(--sans);font-style:normal;color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fd-big{font:650 19px/1.2 var(--sans);letter-spacing:-.01em;}
.fd-n{font:400 11.5px/1.5 var(--sans);color:var(--tx3);} .fd-n b{color:var(--tx2);font-weight:600;}
.fd-n+.fd-n{margin-top:6px;}
.fd-e{padding:14px 12px;font:400 12.5px/1.55 var(--sans);color:var(--tx2);} .fd-e b{color:var(--tx);}
.fd-leg{display:flex;gap:4px 12px;flex-wrap:wrap;font:400 11px var(--sans);color:var(--tx3);margin-bottom:6px;} .fd-leg i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;}
.fd-leg b{font-weight:600;color:var(--tx2);}
.fd-ch{position:relative;width:100%;} .fd-ch svg{position:absolute;left:0;top:0;width:calc(100% - 50px);height:100%;overflow:visible;}
.fd-ch .yl{position:absolute;right:0;width:46px;text-align:right;font:400 10px var(--sans);color:var(--tx3);transform:translateY(-50%);white-space:nowrap;}
.fd-ch .hl{position:absolute;left:4px;font:500 10px var(--sans);transform:translateY(-110%);white-space:nowrap;padding:0 3px;border-radius:3px;background:var(--s1);}
.fd-xl{display:flex;justify-content:space-between;margin:3px 50px 0 0;font:400 10px var(--sans);color:var(--tx3);}
.fd-bar{display:inline-block;width:54px;height:5px;border-radius:3px;background:var(--s3);vertical-align:middle;overflow:hidden;margin-left:6px;} .fd-bar i{display:block;height:100%;border-radius:3px;background:var(--amber,#FF9F0A);}
.fd-vb{display:flex;align-items:flex-end;gap:3px;height:120px;padding-top:14px;}
.fd-vb div{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;}
.fd-vb i{display:block;width:100%;border-radius:2px 2px 0 0;background:var(--amber,#FF9F0A);min-height:1px;}
.fd-vb span{font:500 9.5px var(--sans);color:var(--tx2);margin-bottom:2px;white-space:nowrap;}
.fd-mix div{display:grid;grid-template-columns:minmax(0,1fr) 90px 34px;gap:8px;align-items:center;padding:3px 0;font:400 11.5px var(--sans);color:var(--tx2);}
.fd-mix div>span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;} .fd-mix u{display:block;height:5px;border-radius:3px;background:var(--s3);overflow:hidden;text-decoration:none;}
.fd-mix u i{display:block;height:100%;background:var(--cyan,#5AC8FA);border-radius:3px;} .fd-mix div>span:last-child{text-align:right;color:var(--tx);font-weight:600;}
.fd-rule{display:grid;grid-template-columns:54px minmax(0,1fr);gap:5px 10px;font:400 11.5px/1.5 var(--sans);color:var(--tx2);} .fd-rule span{color:var(--tx3);}
.fd-list{margin:0;padding:0;list-style:none;} .fd-list li{position:relative;padding:3px 0 3px 12px;font:400 11.5px/1.5 var(--sans);color:var(--tx2);}
.fd-list li::before{content:"";position:absolute;left:0;top:10px;width:4px;height:4px;border-radius:50%;background:var(--tx3);}
.fd .x,.fd .xx{display:none;}
@container (min-width:560px){.fd-tk{grid-template-columns:repeat(4,minmax(0,1fr));} .fd-ks{grid-template-columns:repeat(4,minmax(0,1fr));}}
@container (min-width:720px){
  .fd-g{grid-template-columns:repeat(12,minmax(0,1fr));}
  .fd .wide{display:block;} .fd .narrow{display:none;} .fd .s small{display:inline;}
  .fd .t4{grid-column:span 4;} .fd .t6{grid-column:span 6;} .fd .t12{grid-column:span 12;}
  .fd .x{display:revert;}
}
@container (min-width:1100px){
  .fd-tk{grid-template-columns:repeat(8,minmax(0,1fr));} .fd-ks{grid-template-columns:repeat(var(--n,8),minmax(0,1fr));} .fd-ks.in{grid-template-columns:repeat(var(--n,4),minmax(0,1fr));}
  .fd .c2{grid-column:span 2;} .fd .c3{grid-column:span 3;} .fd .c4{grid-column:span 4;} .fd .c5{grid-column:span 5;} .fd .c6{grid-column:span 6;} .fd .c7{grid-column:span 7;}
  .fd .c8{grid-column:span 8;} .fd .c9{grid-column:span 9;} .fd .c12{grid-column:span 12;}
  .fd .xx{display:revert;}
}
</style>"""


# ───────────── reading the files ─────────────
def _stamp(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False, max_entries=4)
def _read_json(path: str, stamp: float):
    return json.loads(Path(path).read_text())


@st.cache_data(show_spinner=False, max_entries=4)
def _read_csv(path: str, stamp: float, keep: tuple = ()):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if not keep or r.get("sym") in keep]


def signals():
    p = ROOT / "signals.json"
    try:
        return _read_json(str(p), _stamp(p)) if p.exists() else None
    except Exception:
        return None


def _charts():
    p = ROOT / "charts.json"
    try:
        return _read_json(str(p), _stamp(p)) if p.exists() else {}
    except Exception:
        return {}


def _names() -> dict:
    try:
        import watchlist_page
        return {k: v[0] for k, v in (watchlist_page.equity_names() or {}).items()}
    except Exception:
        return {}


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


def _sg(x, d=1) -> str:
    return f'<span class="{_cls(x)}">{_pct(x, d)}</span>'


def _qty(x) -> str:
    if not _ok(x):
        return "—"
    a, s = abs(x), "-" if x < 0 else ""
    return f"{s}{a / 1e7:,.0f} Cr" if a >= 1e9 else f"{s}{a / 1e7:.2f} Cr" if a >= 1e7 else f"{s}{a / 1e5:.1f} L" if a >= 1e5 else f"{s}{a / 1e3:.0f} K" if a >= 1e3 else f"{s}{a:.0f}"


def _rs(x) -> str:
    """Rupees the Indian way."""
    if not _ok(x):
        return "—"
    return f"₹{x / 1e7:.2f} Cr" if abs(x) >= 1e7 else f"₹{x / 1e5:.2f} L" if abs(x) >= 1e5 else f"₹{x:,.0f}"


def _day(iso, year=True) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%b %Y' if year else '%b')}"
    except Exception:
        return "—"


def _dshort(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%b')} {d.strftime('%y')}"
    except Exception:
        return "—"


# ───────────── building blocks ─────────────
def _html(body: str):
    st.markdown(f'<div class="fd">{body}</div>', unsafe_allow_html=True)


def _pan(title, meta, body, cls="", flush=False) -> str:
    return f'<section class="fd-p {cls}"><header><b>{_e(title)}</b><span>{meta}</span></header><div class="fd-b{" f" if flush else ""}">{body}</div></section>'


def _grid(*panels) -> str:
    return f'<div class="fd-g">{"".join(panels)}</div>'


def _k(label, val, sub="") -> str:
    return f'<div class="fd-k"><small>{_e(label)}</small><b>{val}</b>{f"<em>{sub}</em>" if sub else ""}</div>'


def _ks(cells, n=8, inner=False) -> str:
    return f'<div class="fd-ks{" in" if inner else ""}" style="--n:{n}">{"".join(cells)}</div>'


def _pill(status) -> str:
    name, c = STATUS.get(status, STATUS["off"])
    return f'<span class="fd-s {c}"><i></i>{name}</span>'


def _act(word, kind="") -> str:
    return f'<span class="fd-a {kind}">{_e(word)}</span>'


def _bar(frac, colour="") -> str:
    w = max(0.0, min(1.0, frac if _ok(frac) else 0.0)) * 100
    style = f"width:{w:.0f}%" + (f";background:{colour}" if colour else "")
    return f'<span class="fd-bar"><i style="{style}"></i></span>'


def _sym(sym, names, sub="") -> str:
    nm = sub or names.get(sym, "")
    return f'<b>{_e(sym)}</b>' + (f'<small>{_e(nm)}</small>' if nm else "")


def _t(head, rows, scroll=True, cls="") -> str:
    """head = [(label, classes)], rows = [[cell, ...]] or [([cells], row class)]. Classes: l left · x hidden on phones · xx only on wide screens · b bold."""
    th = "".join(f'<th class="{c}">{_e(h)}</th>' for h, c in head)
    body = ""
    for r in rows:
        cells, rc = (r if isinstance(r, tuple) else (r, ""))
        body += f'<tr class="{rc}">' + "".join(f'<td class="{head[i][1]}">{c}</td>' for i, c in enumerate(cells)) + "</tr>"
    t = f'<table class="fd-t {cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
    return f'<div class="fd-sc">{t}</div>' if scroll else t


def _empty(msg) -> str:
    return f'<div class="fd-e">{msg}</div>'


def _legend(items) -> str:
    return '<div class="fd-leg">' + "".join(f'<span><i style="background:{c}"></i>{_e(n)}{f" <b>{v}</b>" if v else ""}</span>' for n, c, v in items) + '</div>'


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


def _line(dates, series, h=150, fmt=_px, zero=False, marks=()) -> str:
    """Line chart. series = [(name, colour, values, last-value text)]. marks = [(value, label, colour)] drawn as dotted levels."""
    vals = [v for _, _, ys, *_ in series for v in ys if _ok(v)] + [m[0] for m in marks if _ok(m[0])]
    if len(vals) < 3:
        return _empty("Not enough history to draw this yet.")
    lo, hi = min(vals), max(vals)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = (hi - lo) * 0.07 or abs(hi) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    n = max(len(ys) for _, _, ys, *_ in series)
    X = lambda i: i / max(n - 1, 1) * 1000
    Y = lambda v: (hi - v) / (hi - lo) * h
    ticks = _nice(lo, hi)
    grid = "".join(f'<line x1="0" x2="1000" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{"var(--tx3)" if (zero and abs(t) < 1e-12) else "var(--line)"}" stroke-width="1" '
                   f'vector-effect="non-scaling-stroke"/>' for t in ticks)
    paths, labels = "", ""
    for _, colr, ys, *_ in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys) if _ok(v))
        paths += f'<polyline points="{pts}" fill="none" stroke="{colr}" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>'
    for v, lab, colr in marks:
        if _ok(v):
            paths += f'<line x1="0" x2="1000" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{colr}" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>'
            labels += f'<span class="hl" style="top:{Y(v) / h * 100:.1f}%;color:{colr}">{_e(lab)}</span>'
    yl = "".join(f'<span class="yl" style="top:{Y(t) / h * 100:.1f}%">{fmt(t)}</span>' for t in ticks)
    xl = ""
    if dates:
        idx = sorted({int(round(k * (len(dates) - 1) / 4)) for k in range(5)})
        xl = '<div class="fd-xl">' + "".join(f"<span>{_dshort(dates[i])}</span>" for i in idx) + "</div>"
    leg = _legend([(nm, colr, (rest[0] if rest else "")) for nm, colr, _, *rest in series])
    return f'{leg}<div class="fd-ch" style="height:{h}px"><svg viewBox="0 0 1000 {h}" preserveAspectRatio="none">{grid}{paths}</svg>{yl}{labels}</div>{xl}'


def _strikes(labels, series, h=150, mark=None) -> str:
    """Bars across strikes. series = [(name, colour, values)]; mark = the price, drawn as a dashed line."""
    n = len(labels)
    vals = [abs(v) for _, _, ys in series for v in ys if _ok(v)]
    if n < 3 or not vals or max(vals) <= 0:
        return _empty("No strikes stored for this expiry.")
    m, slot, k = max(vals), 10.0, len(series)
    W, bw = n * slot, 8.0 / k
    rects = ""
    for j, (_, colr, ys) in enumerate(series):
        for i, v in enumerate(ys):
            if _ok(v) and v > 0:
                hh = v / m * (h - 8)
                rects += f'<rect x="{i * slot + 1 + j * bw:.2f}" y="{h - 1 - hh:.1f}" width="{bw * 0.92:.2f}" height="{hh:.1f}" fill="{colr}" opacity=".85"/>'
    extra = ""
    if _ok(mark) and labels[0] < mark < labels[-1]:
        for i in range(n - 1):
            if labels[i] <= mark <= labels[i + 1]:
                pos = (i + (mark - labels[i]) / ((labels[i + 1] - labels[i]) or 1)) * slot + slot / 2
                extra = f'<line x1="{pos:.1f}" x2="{pos:.1f}" y1="0" y2="{h}" stroke="var(--tx)" stroke-width="1.2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>'
                break
    idx = sorted({int(round(q * (n - 1) / 5)) for q in range(6)})
    xl = '<div class="fd-xl">' + "".join(f"<span>{_px(labels[i])}</span>" for i in idx) + "</div>"
    items = [(nm, colr, "") for nm, colr, _ in series] + ([(f"Price {_px(mark)}", "var(--tx)", "")] if _ok(mark) else [])
    return (f'{_legend(items)}<div class="fd-ch" style="height:{h}px"><svg viewBox="0 0 {W:.0f} {h}" preserveAspectRatio="none">{extra}{rects}</svg>'
            f'<span class="yl" style="top:6%">{_qty(m)}</span><span class="yl" style="top:98%">0</span></div>{xl}')


def _vbars(labels, values, fmt=lambda v: f"{v * 100:.1f}", colour=None) -> str:
    vals = [v for v in values if _ok(v)]
    if not vals:
        return ""
    m = max(abs(v) for v in vals) or 1.0
    cols = "".join(f'<div><span>{fmt(v)}</span><i style="height:{max(abs(v) / m * 100, 1):.0f}%;background:{colour(i, v) if colour else AMBER}"></i></div>' for i, v in enumerate(values))
    return f'<div class="fd-vb">{cols}</div><div class="fd-xl" style="margin-right:0">' + "".join(f"<span>{_e(x)}</span>" for x in labels) + "</div>"


def _candles(d, s, marks=(), h=260) -> str:
    """Candlestick chart as plain SVG (used when Plotly is not available). Hover a candle for its numbers."""
    n = len(d)
    vals = [v for k in ("h", "l") for v in s[k] if _ok(v)] + [m[0] for m in marks if _ok(m[0])]
    if n < 5 or not vals:
        return _empty("No price history stored for this stock yet.")
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    W, slot = 1000.0, 1000.0 / n
    Y = lambda v: (hi - v) / (hi - lo) * h
    ticks = _nice(lo, hi)
    out = "".join(f'<line x1="0" x2="{W}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>' for t in ticks)
    for i in range(n):
        o, hh, ll, c = s["o"][i], s["h"][i], s["l"][i], s["c"][i]
        if not all(_ok(v) for v in (o, hh, ll, c)):
            continue
        colr = GREEN if c >= o else RED
        x = i * slot + slot / 2
        top, bot = Y(max(o, c)), Y(min(o, c))
        tip = f"{_day(d[i])} · open {_px(o)} · high {_px(hh)} · low {_px(ll)} · close {_px(c)}"
        out += (f'<g><title>{tip}</title><line x1="{x:.1f}" x2="{x:.1f}" y1="{Y(hh):.1f}" y2="{Y(ll):.1f}" stroke="{colr}" stroke-width="1" vector-effect="non-scaling-stroke"/>'
                f'<rect x="{x - slot * 0.32:.1f}" y="{top:.1f}" width="{slot * 0.64:.1f}" height="{max(bot - top, 0.8):.1f}" fill="{colr}"/></g>')
    pts = " ".join(f"{i * slot + slot / 2:.1f},{Y(v):.1f}" for i, v in enumerate(s.get("m") or []) if _ok(v))
    if pts:
        out += f'<polyline points="{pts}" fill="none" stroke="{AMBER}" stroke-width="1.3" vector-effect="non-scaling-stroke"/>'
    labels = ""
    for v, lab, colr in marks:
        if _ok(v):
            out += f'<line x1="0" x2="{W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{colr}" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>'
            labels += f'<span class="hl" style="top:{Y(v) / h * 100:.1f}%;color:{colr}">{_e(lab)} {_px(v)}</span>'
    yl = "".join(f'<span class="yl" style="top:{Y(t) / h * 100:.1f}%">{_px(t)}</span>' for t in ticks)
    idx = sorted({int(round(k * (n - 1) / 5)) for k in range(6)})
    xl = '<div class="fd-xl">' + "".join(f"<span>{_dshort(d[i])}</span>" for i in idx) + "</div>"
    return f'<div class="fd-ch" style="height:{h}px"><svg viewBox="0 0 {W:.0f} {h}" preserveAspectRatio="none">{out}</svg>{yl}{labels}</div>{xl}'


# ───────────── sizing (typed on the page, kept in the browser session only) ─────────────
def _sizing() -> dict:
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1:
        cap_l = st.number_input("Money for the Leaders book (₹)", min_value=0, max_value=1_000_000_000, value=int(ss.get("_fd_cap_l", 200000)), step=10000, key="fd_cap_l")
    with c2:
        cap_s = st.number_input("Money for swing and index trades (₹)", min_value=0, max_value=1_000_000_000, value=int(ss.get("_fd_cap_s", 100000)), step=10000, key="fd_cap_s")
    with c3:
        risk = st.number_input("Risk on one swing trade (% of that money)", min_value=0.1, max_value=5.0, value=float(ss.get("_fd_risk", 1.0)), step=0.1, key="fd_risk")
    ss["_fd_cap_l"], ss["_fd_cap_s"], ss["_fd_risk"] = cap_l, cap_s, risk
    return {"cap_l": float(cap_l or 0), "cap_s": float(cap_s or 0), "risk": float(risk or 1.0) / 100}


def _saved_sizing() -> dict:
    ss = st.session_state
    return {"cap_l": float(ss.get("_fd_cap_l", 200000)), "cap_s": float(ss.get("_fd_cap_s", 100000)), "risk": float(ss.get("_fd_risk", 1.0)) / 100}


def _slot_qty(price, sz, slots=20) -> str:
    if not _ok(price) or price <= 0 or sz["cap_l"] <= 0:
        return "—"
    q = int(sz["cap_l"] / slots // price)
    return f"{q:,}" if q > 0 else "0"


def _risk_qty(price, stop, sz):
    """Shares so that the gap between entry and stop costs the chosen share of capital; never more than a quarter of capital in one trade."""
    if not (_ok(price) and _ok(stop)) or price <= stop or sz["cap_s"] <= 0:
        return None
    q = int(sz["cap_s"] * sz["risk"] // (price - stop))
    return max(min(q, int(sz["cap_s"] * 0.25 // price)), 0)


# ───────────── orders ─────────────
def order_rows(S: dict, sz: dict) -> list:
    """Everything to do at the next open, across the books."""
    out = []
    ld, sw = S["leaders"], S["swing"]
    for r in ld.get("sell") or []:
        out.append({"book": "Leaders", "act": "Sell", "k": "s", "sym": r["sym"], "ref": r["price"], "stop": None, "qty": "all", "note": r.get("why") or "", "status": ld["status"], "pnl": r.get("pnl")})
    for r in ld.get("buy") or []:
        out.append({"book": "Leaders", "act": "Buy", "k": "b", "sym": r["sym"], "ref": r["price"], "stop": None, "qty": _slot_qty(r["price"], sz, ld.get("slots", 20)),
                    "note": "one twentieth of the book", "status": ld["status"], "pnl": None})
    if sw["status"] != "off":
        for r in sw.get("open") or []:
            if r.get("sell"):
                out.append({"book": "Swing", "act": "Sell", "k": "s", "sym": r["sym"], "ref": r["price"], "stop": None, "qty": "all", "note": r["sell"], "status": sw["status"], "pnl": r.get("pnl")})
        for r in sw.get("new") or []:
            q = _risk_qty(r["price"], r["stop"], sz)
            out.append({"book": "Swing", "act": "Buy", "k": "b", "sym": r["sym"], "ref": r["price"], "stop": r["stop"], "qty": (f"{q:,}" if q is not None else "—"),
                        "note": f'stop {_pct(-(r.get("risk") or 0), 1)} below entry', "status": sw["status"], "pnl": None})
    for x in S.get("index") or []:
        if x["status"] == "off":
            continue
        if x["action"] == "buy":
            out.append({"book": "Index", "act": "Buy", "k": "b", "sym": x["name"], "ref": x["last"], "stop": None, "qty": "futures or call spread", "note": "dip in an uptrend", "status": x["status"], "pnl": None})
        elif x["action"] == "sell":
            out.append({"book": "Index", "act": "Sell", "k": "s", "sym": x["name"], "ref": x["last"], "stop": None, "qty": "all", "note": x.get("why") or "", "status": x["status"],
                        "pnl": (x.get("pos") or {}).get("pnl")})
    return out


def _orders_table(rows, names) -> str:
    head = [("Book", "l"), ("Action", "l"), ("Symbol", "l"), ("Last close", ""), ("Stop", ""), ("Quantity", ""), ("Open P&L", "x"), ("Why", "l xx"), ("Status", "l x")]
    body = [[_e(r["book"]), _act(r["act"], r["k"]), f'<span class="s">{_sym(r["sym"], names)}</span>', _px(r["ref"]), _px(r["stop"]) if _ok(r["stop"]) else '<span class="mu">none</span>',
             _e(r["qty"]), _sg(r["pnl"]) if _ok(r["pnl"]) else "", _e(r["note"]), _pill(r["status"])] for r in rows]
    return _t(head, body)


def _order_cards(rows) -> str:
    """The same orders as stacked rows, for phones."""
    out = ""
    for r in rows:
        bits = [r["book"]] + ([f'stop {_px(r["stop"])}'] if _ok(r["stop"]) else []) + ([f'qty {r["qty"]}'] if r["qty"] not in ("", "—") else []) + ([_pct(r["pnl"], 1)] if _ok(r["pnl"]) else []) \
            + ([r["note"]] if r["k"] == "s" and r["note"] else []) + ([STATUS[r["status"]][0].lower()] if r["status"] != "on" else [])
        out += f'<div>{_act(r["act"], r["k"])}<b>{_e(r["sym"])}</b><span class="v">{_px(r["ref"])}</span><small>{_e(" · ".join(bits))}</small></div>'
    return f'<div class="fd-oc">{out}</div>'


def _watch_rows(S, names) -> list:
    """What could turn into an order in the next few sessions."""
    rows = []
    for x in S.get("index") or []:
        if x["status"] == "off":
            continue
        if x["action"] == "none" and x.get("above200") and _ok(x.get("trigger")) and _ok(x.get("last")):
            rows.append(["Index", f'<b style="color:var(--tx)">{_e(x["name"])}</b>', "Buy trigger", _px(x["trigger"]), _sg(x["trigger"] / x["last"] - 1, 1), "a close at or below this level buys the dip"])
        elif x["action"] == "hold" and _ok(x.get("ma5")):
            rows.append(["Index", f'<b style="color:var(--tx)">{_e(x["name"])}</b>', "Exit level", _px(x["ma5"]), _sg(x["ma5"] / x["last"] - 1, 1), "a close above the 5-day average sells"])
    for r in (S["swing"].get("open") or [])[:6]:
        if not r.get("sell") and _ok(r.get("stop")) and _ok(r.get("price")):
            rows.append(["Swing", f'<span class="s">{_sym(r["sym"], names)}</span>', "Stop", _px(r["stop"]), _sg(r["stop"] / r["price"] - 1, 1), f'{r.get("left", 0)} sessions left'])
    for h in S["leaders"]["holdings"]:
        if h["flag"] == "risk":
            rows.append(["Leaders", f'<span class="s">{_sym(h["sym"], names)}</span>', "Likely to go", _px(h["price"]), "", f'strength {_num((h.get("rank") or 0) * 100, 0)}: sold at the next check unless it recovers'])
    for r in (S["leaders"].get("candidates") or [])[:3]:
        rows.append(["Leaders", f'<span class="s">{_sym(r["sym"], names)}</span>', "Next in line", _px(r["price"]), "", f'strength {_num((r.get("rank") or 0) * 100, 0)}: bought when a slot opens'])
    return rows


def _next_check(S) -> str:
    return "checked today" if S["leaders"].get("check_today") else "next check after the first session of next week"


# ───────────── desk ─────────────
def _ticker(S) -> str:
    m = S["market"]
    try:
        age = (date.today() - date.fromisoformat(S["asof"])).days
    except Exception:
        age = 0
    cell = lambda lab, val: f"<div><small>{lab}</small><b>{val}</b></div>"
    first = cell(f'Prices to <span class="am">· {age} days old</span>' if age > 4 else "Prices to", _day(S["asof"]))
    return ('<div class="fd-tk">' + first + cell("NIFTY 500", f'{_px(m.get("n500"))} {_sg(m.get("n500_d1"), 2)}')
            + cell("Long-term trend", '<span class="up">Above 200-day</span>' if m.get("above200") else '<span class="dn">Below 200-day</span>')
            + cell("Stocks above 50-day · 200-day", f'{_pct(m.get("b50"), 0, False)} · {_pct(m.get("b200"), 0, False)}')
            + cell("52-week highs · lows", f'<span class="up">{m.get("highs", 0)}</span> · <span class="dn">{m.get("lows", 0)}</span>')
            + cell("India VIX", _num(m.get("vix"), 1)) + cell("Universe", f'{m.get("universe", 0)} most traded') + cell("F&O data to", _day(S.get("fno_date"), False)) + '</div>')


def _books(S) -> str:
    ld, sw = S["leaders"], S["swing"]
    w = {x["key"]: x for x in ld["record"]["windows"]}
    a = w["all"]
    two = lambda top, sub: f'<span class="s2"><b>{top}</b><small>{sub}</small></span>'
    num = lambda top, sub: f'<span class="s2" style="text-align:right"><b>{top}</b><small>{sub}</small></span>'
    rows = [[two("Leaders", "trend · 20 strongest stocks"), _pill(ld["status"]), num(f'<span class="{_cls(a["book"].get("cagr"))}">{_pct(a["book"].get("cagr"))}</span> a year', f'NIFTY 500 {_pct(a["index"].get("cagr"))}'),
             num(f'{len(ld["holdings"])} held', _next_check(S).replace("after the first session of next week", "next week"))]]
    s = sw["record"]["all"]
    rows.append([two("Swing", "leader pullback · a few days"), _pill(sw["status"]), num(f'<span class="{_cls(s.get("exp"))}">{_pct(s.get("exp"), 2)}</span> a trade', f'{_pct(s.get("win"), 0, False)} of {s.get("n", 0)} won'),
                 num(f'{len(sw["open"])} open', f'{len(sw["new"])} new today')])
    for x in S.get("index") or []:
        r = x["record"]["all"]
        state = {"buy": "Buy at open", "sell": "Sell at open", "hold": "In a trade", "none": "No signal"}[x["action"]]
        rows.append([two(_e(x["name"]), "dip in an uptrend"), _pill(x["status"]), num(f'<span class="{_cls(r.get("exp"))}">{_pct(r.get("exp"), 2)}</span> a trade', f'{_pct(r.get("win"), 0, False)} of {r.get("n", 0)} won'),
                     num(state, f'5 sessions {_pct(x.get("r5"), 1)}')])
    foot = _ks([_k("Leaders from peak", _sg(ld["record"].get("dd_now"), 1), "model book"), _k("Stay-away list", f'{len(S.get("avoid") or [])} stocks', "weakest tenth, below 200-day")], 2, True)
    return _t([("Book", "l"), ("Status", "l"), ("Record", ""), ("Now", "x")], rows, scroll=False) + f'<div style="border-top:1px solid var(--line)">{foot}</div>'


def _lead_mini(S, names, limit=10) -> str:
    rows = []
    for h in S["leaders"]["holdings"][:limit]:
        flag = _act("Sell", "s") if h["flag"] == "sell" else _act("Watch", "w") if h["flag"] == "risk" else _act("Hold")
        rows.append([f'<span class="s">{_sym(h["sym"], names)}</span>', _px(h["price"]), _sg(h["d1"], 1), _sg(h["pnl"], 0), _pct(h.get("w"), 0, False), flag])
    return _t([("Symbol", "l"), ("Close", ""), ("Day", "x"), ("P&L", ""), ("Weight", "x"), ("", "")], rows, scroll=False)


def _lead_curve(S, h=150) -> str:
    c = S["leaders"]["record"]["curve"]
    mult = lambda v: f"{1 + v:,.1f}×"
    return _line(c["date"], [("Leaders book", AMBER, c["book"], mult(c["book"][-1])), ("NIFTY 500", "var(--tx2)", c["index"], mult(c["index"][-1])),
                             ("Average liquid stock", BLUE, c["avg"], mult(c["avg"][-1]))], h=h, fmt=mult)


def _swing_mini(S, names) -> str:
    sw = S["swing"]
    rows = []
    for r in sw["new"][:6]:
        rows.append([f'<span class="s"><b>{_e(r["sym"])}</b></span>', _act("Buy", "b"), _px(r["stop"]), '<span class="mu">new</span>'])
    for r in sw["open"][:8]:
        rows.append([f'<span class="s"><b>{_e(r["sym"])}</b></span>', _act("Sell", "s") if r.get("sell") else _act("Hold"), _px(r["stop"]), _sg(r["pnl"], 1)])
    if not rows:
        return _empty("No swing signal and no open swing trade. Signals come in clusters, on sharp market sell-offs.")
    return _t([("Symbol", "l"), ("", "l"), ("Stop", ""), ("P&L", "")], rows, scroll=False)


def _index_card(x) -> str:
    word, col = {"buy": ("Buy at the next open", "up"), "sell": ("Sell at the next open", "dn"), "hold": ("In a dip trade: hold", "am"), "none": ("No signal", "fl")}[x["action"]]
    p = x.get("pos") or {}
    sub = (f'since {_day(p.get("since"), False)} · {_sg(p.get("pnl"), 2)} · {p.get("left", 0)} sessions left' if p else
           (f'buys on a close at or below <b>{_px(x.get("trigger"))}</b>' if x.get("above200") else "below its 200-day average: the setup is off"))
    f = x.get("fno") or {}
    cells = [_k("Last close", _px(x["last"]), f'5 sessions {_pct(x.get("r5"))}'), _k("200-day average", _px(x.get("ma200")), "above" if x.get("above200") else "below"),
             _k("Put-call ratio", _num(f.get("pcr"), 2), f'max pain {_px(f.get("max_pain"))}'), _k("Put wall · call wall", f'{_px(f.get("put_wall"))} · {_px(f.get("call_wall"))}', f'expiry {_day(f.get("exp"), False)}')]
    return f'<div style="padding:9px 10px 8px"><div class="fd-big {col}">{word}</div><div class="fd-n" style="margin-top:2px">{sub}</div></div>' + _ks(cells, 2, True)


def _fii_card(S) -> str:
    P = S.get("participants")
    if not P or not P.get("rows"):
        return _empty("Participant positions are not stored yet.")
    rows = []
    for r in P["rows"]:
        rows.append([f'<b style="color:var(--tx)">{_e(r["who"])}</b>', _pct(r.get("long_share"), 0, False) + _bar(r.get("long_share")), f'<span class="{_cls(r.get("d5"))}">{_qty(r.get("d5"))}</span>'])
    return _t([("Index futures", "l"), ("Long share", ""), ("Net, 5 sessions", "")], rows, scroll=False)


def _foot_card(S, names) -> str:
    F, B = S.get("footprints") or {}, (S.get("buildups") or {}).get("lists") or {}
    rows = []
    for lab, lst, colr in (("Long build-up", B.get("Long build-up"), GREEN), ("Short build-up", B.get("Short build-up"), RED), ("Delivery accumulation", F.get("accumulation"), GREEN),
                           ("Delivery distribution", F.get("distribution"), RED)):
        syms = ", ".join(_e(r["sym"]) for r in (lst or [])[:4]) or '<span class="mu">none</span>'
        rows.append([f'<i class="dot" style="background:{colr}"></i><span style="color:var(--tx)">{_e(lab)}</span>', syms])
    return _t([("Footprint", "l"), ("Stocks", "l w")], rows, scroll=False)


def _desk(S, C, sz, names):
    rows = order_rows(S, sz)
    blot = (f'<div class="wide">{_orders_table(rows, names)}</div><div class="narrow">{_order_cards(rows)}</div>') if rows else _empty(f'<b>No orders for the next open.</b> Leaders: {_next_check(S)}. Swing and index signals appear on the day they trigger.')
    watch = _watch_rows(S, names)
    if watch:
        blot += ('<div style="border-top:1px solid var(--line)"><div style="padding:6px 10px 2px;font:500 10.5px var(--sans);color:var(--tx3)">On watch for the next sessions</div>'
                 + _t([("Book", "l"), ("", "l"), ("What", "l x"), ("Level", ""), ("Away", ""), ("", "l xx")], watch[:8], scroll=False) + '</div>')
    n_sw = sum(r["book"] == "Swing" and r["k"] == "b" for r in rows)
    if n_sw > 4:
        blot += (f'<div style="padding:7px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b>{n_sw} swing buys on one day are one bet.</b> They come from the same sell-off and move together. '
                 'They are listed strongest first; the tested portfolio never held more than ten at a tenth of capital each.</div></div>')
    top = _grid(_pan("Orders for the next open", f'{len(rows)} to place · signals from {_day(S["asof"], False)} close' if rows else f'signals from {_day(S["asof"], False)} close', blot, "c8 t12", True),
                _pan("Books", "record on real prices, after costs", _books(S), "c4 t12", True))
    ld = S["leaders"]
    mid = _grid(_pan("Leaders: holdings", f'top {min(10, len(ld["holdings"]))} of {len(ld["holdings"])} by profit', _lead_mini(S, names), "c5 t6", True),
                _pan("Leaders against NIFTY 500", f'growth of 1 since {ld["record"]["start"][:4]}', _lead_curve(S), "c4 t6"),
                _pan("Swing: signals and open trades", _pill(S["swing"]["status"]), _swing_mini(S, names), "c3 t12", True))
    idx = "".join(_pan(x["name"] + " · dip in an uptrend", _pill(x["status"]), _index_card(x), "c3 t6", True) for x in S.get("index") or [])
    P = S.get("participants") or {}
    bot = _grid(idx, _pan("Who holds index futures", f'{_day(P.get("date"), False)}', _fii_card(S), "c3 t6", True),
                _pan("Footprints today", "context, not signals", _foot_card(S, names), "c3 t6", True))
    _html(top + mid + bot)


# ───────────── leaders ─────────────
def _lead_kpis(S) -> str:
    ld = S["leaders"]
    R = ld["record"]
    w = {x["key"]: x for x in R["windows"]}
    a, l3, t = w["all"], w["last3"], R["trades"]
    rng = a.get("range") or [None, None]
    cells = [_k("Status", _pill(ld["status"]), "beat both benchmarks" if ld["status"] == "on" else "one period is weak"),
             _k(f'A year, since {R["start"][:4]}', f'<span class="{_cls(a["book"].get("cagr"))}">{_pct(a["book"].get("cagr"))}</span>', f'NIFTY 500 {_pct(a["index"].get("cagr"))}'),
             _k("Other check days", f'{_pct(rng[0], 0)} to {_pct(rng[1], 0)}', "same rule, other check day"),
             _k("Last 3 years", f'<span class="{_cls(l3["book"].get("cagr"))}">{_pct(l3["book"].get("cagr"))}</span>', f'NIFTY 500 {_pct(l3["index"].get("cagr"))}'),
             _k("Worst fall", f'<span class="dn">{_pct(a["book"].get("dd"), 0)}</span>', f'NIFTY 500 {_pct(a["index"].get("dd"), 0)}'),
             _k("From its peak now", _sg(R.get("dd_now"), 1), "model book"),
             _k("Trades won", _pct(t.get("win"), 0, False), f'avg {_pct(t.get("avg_win"), 0)} · lost {_pct(t.get("avg_loss"), 0)}'),
             _k("Trades a year", _num(t.get("per_year"), 0), f'{t.get("big", 0)} gained 50% or more')]
    return _ks(cells, 8)


def _lead_holdings(S, names, sz) -> str:
    ld = S["leaders"]
    rows = []
    for h in ld["holdings"]:
        flag = _act("Sell", "s") if h["flag"] == "sell" else _act("Watch", "w") if h["flag"] == "risk" else _act("Hold")
        f = h.get("fno") or {}
        rows.append([f'<span class="s">{_sym(h["sym"], names)}</span>', flag, _dshort(h["since"]), _px(h["entry"]), _px(h["price"]), _sg(h["d1"], 1), _sg(h["pnl"], 0),
                     _pct(h.get("w"), 0, False), _num((h.get("rank") or 0) * 100, 0) + _bar(h.get("rank")), str(h.get("bars") if h.get("bars") is not None else "—"),
                     _slot_qty(h["price"], sz, ld.get("slots", 20)), (f'lot {_num(f.get("lot"))}' if f else '<span class="mu">cash only</span>')])
    head = [("Symbol", "l"), ("", "l"), ("Since", "x"), ("Entry", "x"), ("Close", ""), ("Day", "x"), ("P&L", ""), ("Weight", "xx"), ("Strength", "x"), ("Sessions", "xx"), ("Qty for your slot", "xx"), ("F&O", "l xx")]
    return _t(head, rows, scroll=False)


def _lead_changes(S, names, sz) -> str:
    ld = S["leaders"]
    out = ""
    if ld.get("buy") or ld.get("sell"):
        cards = ""
        for r in ld["sell"]:
            cards += f'<div>{_act("Sell", "s")}<b>{_e(r["sym"])}</b><span class="v">{_px(r["price"])}</span><small>{_e(r.get("why") or "")} · {_pct(r.get("pnl"), 0)} since {_dshort(r.get("since"))}</small></div>'
        for r in ld["buy"]:
            q = _slot_qty(r["price"], sz, ld.get("slots", 20))
            cards += f'<div>{_act("Buy", "b")}<b>{_e(r["sym"])}</b><span class="v">{_px(r["price"])}</span><small>strength {_num((r.get("rank") or 0) * 100, 0)} of 100 · {q} shares for your slot</small></div>'
        out += f'<div class="fd-oc">{cards}</div>'
    else:
        out += _empty('<b>No change this session.</b> ' + ("It was checked today." if ld.get("check_today") else "Next check: after the first session of next week; orders go in at the following open."))
    for nt in (ld.get("notes") or [])[-3:]:
        out += (f'<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b class="am">{_e(nt.get("sym", ""))}, {_day(nt.get("date"))}:</b> {_e(nt.get("text", ""))} '
                f'Result in the model book {_pct(nt.get("ret"), 0)}.</div></div>')
    lc = ld.get("last_change")
    if lc:
        b = ", ".join(f'{_e(x["sym"])} at {_px(x["px"])}' for x in lc.get("bought") or []) or "nothing"
        s_ = ", ".join(f'{_e(x["sym"])} at {_px(x["px"])} ({_pct(x.get("ret"), 0)})' for x in lc.get("sold") or []) or "nothing"
        out += f'<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b>Last change, {_day(lc["date"])} open.</b> Bought {b}. Sold {s_}.</div></div>'
    return out


def _years_table(R) -> str:
    rows = []
    for y in reversed(R["years"]):
        diff = (y["book"] - y["index"]) if (_ok(y.get("book")) and _ok(y.get("index"))) else None
        rows.append([f'<b style="color:var(--tx)">{y["y"]}</b>' + (' <span class="mu">part</span>' if y.get("part") else ""), _sg(y.get("book"), 0), f'{_pct(y.get("lo"), 0)} to {_pct(y.get("hi"), 0)}',
                     _sg(y.get("index"), 0), _sg(y.get("avg"), 0), _sg(diff, 0)])
    return _t([("Year", "l"), ("Book", ""), ("Other check days", "x"), ("NIFTY 500", ""), ("Avg stock", "x"), ("Against index", "")], rows)


def _trade_list(trs, names, why=False) -> str:
    rows = [[f'<span class="s">{_sym(t["sym"], names)}</span>', _dshort(t["in"]), _dshort(t["out"]), _sg(t["ret"], 0 if abs(t["ret"]) >= 0.1 else 1), str(t.get("bars") if t.get("bars") is not None else "—")]
            + ([_e(t.get("why") or "")] if why else []) for t in trs]
    return _t([("Symbol", "l"), ("In", ""), ("Out", "x"), ("Result", ""), ("Sessions", "x")] + ([("Why it left", "l xx")] if why else []), rows)


def _sector_mix(S, names) -> str:
    try:
        import sector_map
        counts = {}
        for h in S["leaders"]["holdings"]:
            g = sector_map.classify(h["sym"], names.get(h["sym"], ""))
            g = (g[0] if isinstance(g, (tuple, list)) else g) or "Other"
            try:
                g = sector_map.family(g) or g
            except Exception:
                pass
            counts[g] = counts.get(g, 0.0) + (h.get("w") or 0)
    except Exception:
        return ""
    if not counts:
        return ""
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    m = max(v for _, v in top) or 1
    return '<div class="fd-mix">' + "".join(f'<div><span>{_e(k)}</span><u><i style="width:{v / m * 100:.0f}%"></i></u><span>{v * 100:.0f}%</span></div>' for k, v in top) + '</div>'


def _rule(key, S) -> str:
    r = S["leaders"] if key == "leaders" else S["swing"] if key == "swing" else (S["index"][0] if S.get("index") else {})
    return (f'<div class="fd-rule"><span>Enter</span><div>{_e(r.get("rule", ""))}</div><span>Exit</span><div>{_e(r.get("exit", ""))}</div><span>Size</span><div>{_e(r.get("size", ""))}</div>'
            f'<span>Fill</span><div>Signals are known after the close. Orders go in at the next session\'s open.</div></div>')


def _leaders(S, C, sz, names, on_open=None):
    ld = S["leaders"]
    R = ld["record"]
    n_sell, n_risk = sum(h["flag"] == "sell" for h in ld["holdings"]), sum(h["flag"] == "risk" for h in ld["holdings"])
    meta = f'{len(ld["holdings"])} of {ld.get("slots", 20)} slots' + (f' · {n_sell} to sell' if n_sell else "") + (f' · {n_risk} on watch' if n_risk else "")
    dd = R.get("dd_curve") or {}
    sm = _sector_mix(S, names)
    cand = [[f'<span class="s">{_sym(r["sym"], names)}</span>', _px(r["price"]), _sg(r["d1"], 1), _num((r.get("rank") or 0) * 100, 0) + _bar(r.get("rank"))] for r in ld.get("candidates") or []]
    nxt = _pan("Next in line", "bought when a slot opens", _t([("Symbol", "l"), ("Close", ""), ("Day", ""), ("Strength", "")], cand, scroll=False) if cand else _empty("No candidates."), "", True)
    left = _pan("This week's changes", _next_check(S), _lead_changes(S, names, sz), "", True) + f'<div class="wide">{nxt}</div>'
    body = (_lead_kpis(S)
            + _grid(f'<div class="fd-col c4 t12">{left}</div>', _pan("Holdings", meta, _lead_holdings(S, names, sz), "c8 t12", True), f'<div class="narrow">{nxt}</div>')
            + _grid(_pan("Growth of 1", f'since {_day(R["start"])} · after costs', _lead_curve(S, 190), "c7 t12"),
                    _pan("Fall from the peak", "how deep it has gone", _line(dd.get("date") or [], [("Leaders book", RED, dd.get("dd") or [], _pct(R.get("dd_now"), 0))], h=190,
                                                                             fmt=lambda v: f"{v * 100:.0f}%", zero=True), "c5 t12"))
            + _grid(_pan("Year by year", "book · range on other check days · benchmarks", _years_table(R), "c5 t12", True),
                    _pan("Biggest winners", f'{R["trades"].get("big", 0)} trades gained 50% or more', _trade_list(R["winners"], names), "c4 t6", True),
                    _pan("Sector mix", "share of the book", sm or _empty("Sector map not available."), "c3 t6"))
            + _grid(_pan("Latest exits", "closed trades", _trade_list(R.get("recent") or [], names, True), "c7 t12", True),
                    _pan("The rule", "fixed, no judgement", _rule("leaders", S) + '<div class="fd-n" style="margin-top:8px"><b>Starting today?</b> Buy the holdings in equal amounts, skip any marked Watch, '
                         'then follow the weekly changes. <b>Why no stop:</b> a 4 ATR trail, a 20 to 25% stop and a 200-day exit were all tested; each lowered the return without reducing the worst fall.</div>', "c5 t12")))
    _html(body)
    _chart_picker(S, C, [h["sym"] for h in ld["holdings"]] + [r["sym"] for r in ld.get("buy") or []], "fd_chart_l",
                  lambda s: [(next((h["entry"] for h in ld["holdings"] if h["sym"] == s), None), "Entry", "var(--tx2)")], on_open)


# ───────────── swing ─────────────
def _period_table(rec, extra=None) -> str:
    rows = []
    for k in ("all", "early", "late", "last3"):
        r = rec.get(k) or {}
        e = (r.get(extra) or {}) if extra else {}
        rows.append([f'<b style="color:var(--tx)">{_e(r.get("label", k))}</b>', str(r.get("n", 0)), _pct(r.get("win"), 0, False), _sg(r.get("exp"), 2), _pct(r.get("avg_win"), 1), _pct(r.get("avg_loss"), 1),
                     _num(r.get("pf"), 2), _num(r.get("t"), 1)] + ([_sg(e.get("edge"), 2), _num(e.get("t"), 1)] if extra else []))
    head = [("Period", "l"), ("Trades", ""), ("Won", ""), ("Per trade", ""), ("Avg win", "x"), ("Avg loss", "x"), ("Won ÷ lost", "x"), ("t", "xx")]
    if extra:
        head += [("Stock pick" if extra == "pick" else "Over holding", ""), ("t", "xx")]
    return _t(head, rows, scroll=False)


def _swing(S, C, sz, names, on_open=None):
    sw = S["swing"]
    a = sw["record"]["all"]
    pick = (a.get("pick") or {})
    l3 = sw["record"].get("last3") or {}
    cells = [_k("Status", _pill(sw["status"]), "positive before and after 2022"), _k("Trades tested", str(a.get("n", 0)), f'about {_num(a.get("n", 0) / 11.4, 0)} a year, in clusters'),
             _k("Won", _pct(a.get("win"), 0, False), f'last 3 years {_pct(l3.get("win"), 0, False)}'), _k("Per trade, after costs", _sg(a.get("exp"), 2), f'last 3 years {_pct(l3.get("exp"), 2)}'),
             _k("Avg win · avg loss", f'{_pct(a.get("avg_win"), 1)} · {_pct(a.get("avg_loss"), 1)}', f'held {_num(a.get("bars"), 1)} sessions'),
             _k("Worst losing run", f'{a.get("losing_run", 0)} in a row', f'worst trade {_pct(a.get("worst"), 0)}'),
             _k("From stock picking", _sg(pick.get("edge"), 2), "the rest is the market's bounce"), _k("10% a trade, 10 at once", _pct((sw.get("curve_stats") or {}).get("cagr")) + " a year", f'worst fall {_pct((sw.get("curve_stats") or {}).get("dd"), 0)}')]
    new = []
    for r in sw["new"]:
        q = _risk_qty(r["price"], r["stop"], sz)
        f = r.get("fno") or {}
        lots = ""
        if f and _ok(f.get("lot")) and f["lot"] > 0:
            lots = f'lot {_num(f["lot"])} · risk {_rs(f["lot"] * (r["price"] - r["stop"]))}'
        new.append([f'<span class="s">{_sym(r["sym"], names)}</span>', _px(r["price"]), _sg(r.get("r5"), 1), _px(r["stop"]), _pct(r.get("risk"), 1, False), f"{q:,}" if q is not None else "—",
                    _rs(q * r["price"]) if q else "—", _num((r.get("rank") or 0) * 100, 0), lots or '<span class="mu">cash only</span>'])
    opn = []
    for r in sw["open"]:
        opn.append([f'<span class="s">{_sym(r["sym"], names)}</span>', _act("Sell", "s") if r.get("sell") else _act("Hold"), _dshort(r["since"]), _px(r["entry"]), _px(r["price"]), _sg(r["pnl"], 1), _px(r["stop"]),
                    _e(r.get("sell") or f'{r.get("left", 0)} sessions left')])
    done = "".join(f'<div class="fd-n"><b>{_e(d["sym"])}</b> closed today at {_px(d["p1"])}: {_pct(d["ret"], 1)} · {_e(d["why"])}</div>' for d in sw.get("done") or [])
    yrs = sw["record"].get("years") or []
    ybars = _vbars([str(y["y"])[2:] for y in yrs], [y["exp"] for y in yrs], fmt=lambda v: f"{v * 100:+.1f}", colour=lambda i, v: GREEN if v >= 0 else RED)
    cv = sw.get("curve") or {}
    body = (_ks(cells, 8)
            + _grid(_pan("New signals: buy at the next open", f'{len(new)} · stop 3 ATR below entry', _t([("Symbol", "l"), ("Close", ""), ("5 sessions", ""), ("Stop", ""), ("Stop gap", "x"), ("Quantity", ""), ("Value", "x"),
                                                                                                        ("Strength", "xx"), ("Futures", "l xx")], new) if new else
                         _empty("<b>No new signal.</b> A strong stock has to fall 3 ATR in 5 sessions; that mostly happens on market-wide sell-offs, so signals arrive in clusters."), "c7 t12", True),
                    _pan("Open trades", f'{len(opn)} open', (_t([("Symbol", "l"), ("", "l"), ("Since", "x"), ("Entry", "x"), ("Close", ""), ("P&L", ""), ("Stop", ""), ("Plan", "l x")], opn) if opn else _empty("No open swing trade."))
                         + (f'<div style="padding:8px 10px;border-top:1px solid var(--line)">{done}</div>' if done else ""), "c5 t12", True))
            + _grid(_pan("Record by period", "every signal, one trade per stock", _period_table(sw["record"], "pick"), "c6 t12", True),
                    _pan("Average trade by year", "% after costs", ybars, "c3 t6"), _pan("The rule", "fixed, no judgement", _rule("swing", S), "c3 t6"))
            + _grid(_pan("Latest trades", "most recent first", _trade_list(sw.get("recent") or [], names, True), "c6 t12", True),
                    _pan("If you took them with a money limit", "a tenth of capital a trade, at most ten at once", _line(cv.get("date") or [], [("Swing book", AMBER, cv.get("book") or [],
                         _pct((cv.get("book") or [0])[-1], 0))], h=170, fmt=lambda v: f"{v * 100:.0f}%", zero=True), "c6 t12")))
    _html(body)
    stops = {r["sym"]: r["stop"] for r in sw["new"] + sw["open"]}
    ents = {r["sym"]: r.get("entry") for r in sw["open"]}
    _chart_picker(S, C, [r["sym"] for r in sw["new"]] + [r["sym"] for r in sw["open"]], "fd_chart_s", lambda s: [(ents.get(s), "Entry", "var(--tx2)"), (stops.get(s), "Stop", RED)], on_open)


# ───────────── index ─────────────
def _chain(sym: str):
    p = FNO / "chain_latest.csv"
    if not p.exists():
        return None
    try:
        rows = _read_csv(str(p), _stamp(p), ("NIFTY", "BANKNIFTY"))
        rows = [r for r in rows if r["sym"] == sym]
        if not rows:
            return None
        exp = min(r["exp"] for r in rows)
        f = lambda v: float(v) if v not in ("", None) else 0.0
        rows = sorted([r for r in rows if r["exp"] == exp], key=lambda r: f(r["strike"]))
        return {"exp": exp, "k": [f(r["strike"]) for r in rows], "ce": [f(r["ce_oi"]) for r in rows], "pe": [f(r["pe_oi"]) for r in rows]}
    except Exception:
        return None


def _index_col(x, I, sz) -> str:
    word, col = {"buy": ("Buy at the next open", "up"), "sell": ("Sell at the next open", "dn"), "hold": ("In a dip trade: hold", "am"), "none": ("No signal today", "fl")}[x["action"]]
    p, f, a = x.get("pos") or {}, x.get("fno") or {}, x["record"]["all"]
    oh = a.get("over_hold") or {}
    if p:
        sub = f'Bought {_day(p.get("since"), False)} at {_px(p.get("entry"))} · now {_sg(p.get("pnl"), 2)} · {_e(x.get("why") or (str(p.get("left", 0)) + " sessions left"))}'
    elif x.get("above200"):
        sub = f'Uptrend intact. A close at or below <b style="color:var(--tx)">{_px(x.get("trigger"))}</b> next session would trigger a buy.'
    else:
        sub = "Below its 200-day average: the setup only works in an uptrend, so it is switched off for now."
    head = (f'<div style="padding:10px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap"><div class="fd-big {col}">{word}</div>{_pill(x["status"])}</div>'
            f'<div class="fd-n" style="margin-top:3px">{sub}</div></div>'
            + _ks([_k("Last close", _px(x["last"]), f'to {_day(x["asof"], False)}'), _k("5 sessions", _sg(x.get("r5"), 2), "needs −2.5% or worse"), _k("200-day average", _px(x.get("ma200")), "above" if x.get("above200") else "below"),
                   _k("Trades tested", str(a.get("n", 0)), f'{_pct(a.get("win"), 0, False)} won'), _k("Per trade", _sg(a.get("exp"), 2), f'held {_num(a.get("bars"), 1)} sessions'),
                   _k("More than just holding", _sg(oh.get("edge"), 2), f't {_num(oh.get("t"), 1)}'), _k("Worst trade", _pct(a.get("worst"), 1), f'best {_pct(a.get("best"), 1)}'),
                   _k("Worst losing run", f'{a.get("losing_run", 0)} in a row', "no stop in the test")], 4, True))
    out = _pan(x["name"] + " · dip in an uptrend", f'since 2012 · {a.get("n", 0)} trades', head, "", True)
    ch = I.get(x["sym"]) or {}
    if ch:
        marks = [(x.get("trigger"), "Buy trigger", GREEN)] if (x.get("above200") and not p) else [(p.get("entry"), "Entry", "var(--tx2)")] if p else []
        out += _pan("One year", "close · 5-day and 200-day averages", _line(ch["d"], [("Close", "var(--tx)", ch["c"], _px(ch["c"][-1])), ("5-day", BLUE, ch["m5"], ""), ("200-day", AMBER, ch["m200"], "")], h=190, marks=marks))
    out += _pan("Record by period", "after 0.05% a side", _period_table(x["record"], "over_hold"), "", True)
    lot = f.get("lot")
    plan = x.get("plan") or {}
    cells = [_k("Future", _px(f.get("fut")), f'lot {_num(lot)} · {_rs((lot or 0) * (f.get("fut") or 0))} a lot'), _k("Expiry", _day(f.get("exp"), False), f'{_num(f.get("dte"))} days'),
             _k("Put-call ratio", _num(f.get("pcr"), 2), "open interest, all strikes"), _k("Implied volatility", _pct(f.get("iv"), 1, False), f'straddle {_pct(f.get("straddle"), 1, False)} of price'),
             _k("Max pain", _px(f.get("max_pain")), "this expiry"), _k("Put wall", _px(f.get("put_wall")), "most put open interest"), _k("Call wall", _px(f.get("call_wall")), "most call open interest"),
             _k("A 1% move, one lot", _rs((lot or 0) * (f.get("fut") or 0) * 0.01), "futures profit or loss")]
    note = ""
    if plan:
        if plan.get("priced"):
            note = (f'<b>Call spread for a dip trade:</b> buy the {_px(plan["buy"])} call, sell the {_px(plan["sell"])} call, {_day(plan["exp"], False)} expiry. Cost about {_px(plan["cost"])} points '
                    f'({_rs(plan["cost"] * (lot or 0))} a lot), which is the most it can lose; best case {_px(plan["max_gain"])} points; breaks even at {_px(plan["breakeven"])}.')
        else:
            note = (f'<b>Call spread for a dip trade:</b> buy the {_px(plan["buy"])} call, sell the {_px(plan["sell"])} call, in an expiry at least 7 days away. '
                    f'The stored chain expires in {plan.get("days", 0)} days, so price it at your broker; the cost is the most it can lose.')
    out += _pan("Futures and options levels", f'F&O data to {_day(f.get("date"), False)}', _ks(cells, 4, True) + (f'<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n">{note}</div></div>' if note else ""), "", True)
    c = _chain(x["sym"])
    if c and _ok(f.get("spot")):
        keep = [i for i, k in enumerate(c["k"]) if abs(k / f["spot"] - 1) <= 0.06]
        if len(keep) >= 8:
            out += _pan("Open interest by strike", f'{_day(c["exp"], False)} expiry', _strikes([c["k"][i] for i in keep], [("Calls", RED, [c["ce"][i] for i in keep]), ("Puts", GREEN, [c["pe"][i] for i in keep])], mark=f["spot"]))
    rec = [[_dshort(t["in"]), _dshort(t["out"]), _px(t["p0"]), _px(t["p1"]), _sg(t["ret"], 2), str(t["bars"]), _e(t["why"])] for t in x.get("recent") or []]
    out += _pan("Latest trades", "most recent first", _t([("In", "l"), ("Out", "l"), ("Entry", "x"), ("Exit", "x"), ("Result", ""), ("Sessions", "x"), ("Why it left", "l xx")], rec, scroll=False), "", True)
    return f'<div class="fd-col c6 t12">{out}</div>'


def _index(S, C, sz):
    xs = S.get("index") or []
    if not xs:
        _html(_empty("Index history was not available on the last run."))
        return
    _html(_grid(*[_index_col(x, C.get("i") or {}, sz) for x in xs])
          + '<div class="fd-n">The index trade has no stop in its test: its worst trade is shown above. A call spread caps the loss at its cost; with futures, size for a fall of that worst trade. '
            'A "new 20-day high" rule was also tested on both indexes and made no more than simply holding, so it is not used.</div>')


# ───────────── big players ─────────────
def _mini_list(rows, names, cols) -> str:
    if not rows:
        return _empty("Nothing unusual today.")
    return _t([("Symbol", "l")] + [(h, c) for h, _, c in cols], [[f'<span class="s">{_sym(r["sym"], names)}</span>'] + [fn(r) for _, fn, _ in cols] for r in rows], scroll=False)


def _players(S, names):
    P = S.get("participants") or {}
    rows = []
    for r in P.get("rows") or []:
        rows.append([f'<b style="color:var(--tx)">{_e(r["who"])}</b>', _pct(r.get("long_share"), 0, False) + _bar(r.get("long_share")), f'{_pct(r.get("share_pct"), 0, False)}', _qty(r.get("net")),
                     f'<span class="{_cls(r.get("d1"))}">{_qty(r.get("d1"))}</span>', f'<span class="{_cls(r.get("d5"))}">{_qty(r.get("d5"))}</span>', _qty(r.get("stk")),
                     f'<span class="{_cls(r.get("stk_d5"))}">{_qty(r.get("stk_d5"))}</span>', _qty(r.get("opt")), f'<span class="{_cls(r.get("opt_d1"))}">{_qty(r.get("opt_d1"))}</span>'])
    part = _t([("Who", "l"), ("Index futures long", ""), ("Higher than, 1 yr", "x"), ("Net contracts", ""), ("1 session", "x"), ("5 sessions", ""), ("Stock futures net", "xx"), ("5 sessions", "xx"),
               ("Index options net", "xx"), ("1 session", "xx")], rows, scroll=False) if rows else _empty("Participant positions are not stored yet. They come with the F&O data workflow.")
    fii = next((r for r in P.get("rows") or [] if r["who"] == "FIIs"), None)
    if fii:
        part += (f'<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b>FIIs hold {_pct(fii.get("long_share"), 0, False)} of their index futures long</b>, more than on '
                 f'{_pct(fii.get("share_pct"), 0, False)} of days in the past year. Net contracts are longs less shorts; a positive options number means more calls than puts held on balance.</div>'
                 '<div class="fd-n">Tested on five years of this data: no participant reading gave a reliable buy or sell signal on its own, so these are context.</div></div>')
    fh = P.get("fii") or {}
    fchart = _line(fh.get("date") or [], [("FII long share of index futures", AMBER, fh.get("share") or [], _pct((fh.get("share") or [0])[-1], 0, False))], h=150, fmt=lambda v: f"{v * 100:.0f}%") if fh else _empty("No history yet.")
    B = (S.get("buildups") or {}).get("lists") or {}
    bcols = [("Price", lambda r: _sg(r.get("p5"), 1), ""), ("OI", lambda r: _sg(r.get("oi5"), 0), "")]
    bu = "".join(_pan(k, "futures price and open interest · 5 sessions", _mini_list(B.get(k) or [], names, bcols), "c3 t6", True) for k in ("Long build-up", "Short covering", "Short build-up", "Long unwinding"))
    F = S.get("footprints") or {}
    fcols = lambda lab: [(lab, lambda r: f'{_num(r.get("x"), 1)}×', ""), ("Day", lambda r: _sg(r.get("d1"), 1), ""), ("10 sessions", lambda r: _sg(r.get("d10"), 0), "x"), ("Delivered", lambda r: _pct(r.get("dshare"), 0, False), "x")]
    fp = (_pan("Delivery accumulation", "10 sessions of delivery against normal · price up", _mini_list(F.get("accumulation") or [], names, fcols("Delivery")), "c4 t6", True)
          + _pan("Delivery distribution", "heavy delivery · price down", _mini_list(F.get("distribution") or [], names, fcols("Delivery")), "c4 t6", True)
          + _pan("Delivery spike today", "against the last 20 sessions", _mini_list(F.get("delivery_spike") or [], names, fcols("Delivery")), "c4 t12", True)
          + _pan("Big tickets", "average trade size against normal", _mini_list(F.get("big_tickets") or [], names, fcols("Trade size")), "c6 t6", True)
          + _pan("Volume surge", "against the last 50 sessions", _mini_list(F.get("volume") or [], names, fcols("Volume")), "c6 t6", True))
    deals = S.get("deals") or []
    drows = [[_e(d.get("kind", "")), f'<span class="s">{_sym(d.get("sym", ""), names)}</span>', _act("Buy", "b") if str(d.get("side", "")).upper().startswith("B") else _act("Sell", "s"), _e(d.get("who", "")),
              _qty(d.get("qty")), _px(d.get("price")), (f'₹{d["value_cr"]:,.1f} Cr' if _ok(d.get("value_cr")) else "—")] for d in sorted(deals, key=lambda d: -(d.get("value_cr") or 0))[:40]]
    dl = _t([("Type", "l"), ("Symbol", "l"), ("Side", "l"), ("Who", "l w"), ("Shares", "x"), ("Price", "x"), ("Value", "")], drows) if drows else _empty(
        "No bulk or block deal was read on the last run. NSE publishes only the latest day, so they are collected from now on (arka_data/signals/deals.csv).")
    _html(_grid(_pan("Who holds what in futures and options", f'NSE participant data · {_day(P.get("date"))}', part, "c8 t12", True), _pan("FII positioning, 120 sessions", "share of their index futures that is long", fchart, "c4 t12"))
          + _grid(bu) + _grid(fp) + _grid(_pan("Bulk and block deals", "largest first", dl, "c12 t12", True))
          + '<div class="fd-n"><b>How to read this tab.</b> These are footprints of large money, shown as context. Each was also tested as a buy signal on eleven years of prices: '
            'delivery accumulation, delivery spikes and heavy-volume days did not beat simply owning the average liquid stock, so none of them places an order on the desk. See Evidence.</div>')


# ───────────── options map (the earlier positioning views) ─────────────
def _options_map(on_open):
    try:
        import fno_legacy as L
    except Exception:
        _html(_pan("Options map", "", _empty("<b>fno_legacy.py is not installed.</b> Add it next to this file to get the option chain, dealer gamma, six-month history and the F&O stock table here."), "", True))
        return
    st.markdown(L.CSS + "<style>.fo-g{gap:8px;margin-bottom:8px;} .fo-card{border-radius:6px;} .fo-ct{background:var(--s2);padding:6px 10px;} .fo-ct b{font-size:12px;} .fo-cb{padding:10px;}"
                ".fo-tiles{border-radius:6px;margin-top:10px;} .fo-tile{padding:7px 10px;} .fo-tile b{font-size:15px;} .fo-sec{margin:10px 0 8px;}</style>", unsafe_allow_html=True)
    try:
        with st.spinner("Reading the stored F&O history…"):
            res = L.E.run_cached()
    except Exception as e:
        _html(_empty(f"The F&O history could not be read ({_e(type(e).__name__)})."))
        return
    if not res.get("ok") or not res.get("indexes"):
        _html(_empty("No F&O history yet. Run the F&O data workflow once."))
        return
    idx = list(res["indexes"].values())
    pick = st.pills("Options map", [x["name"] for x in idx] + ["F&O stocks"], default=idx[0]["name"], key="fd_om", label_visibility="collapsed") or idx[0]["name"]
    wrap = lambda body: st.markdown(f'<div class="fo">{body}</div>', unsafe_allow_html=True)
    if pick == "F&O stocks":
        stocks = res.get("stocks", [])
        flt = st.pills("Stocks", L.FILTERS, default="All", key="fd_om_flt", label_visibility="collapsed") or "All"
        rows = L._pick(stocks, flt)
        wrap(L._grid("two", L._card("Price and open interest map", f"{len(stocks)} stocks · last 5 sessions", L._quadrant(stocks)), L._card("F&O stocks", f"{len(rows)} shown", L._stock_table(rows), True)))
        c1, c2 = st.columns([3, 1])
        with c1:
            sel = st.selectbox("Open one stock", sorted(s["sym"] for s in stocks), index=None, placeholder="Open one stock for its option chain and history", key="fd_om_sym", label_visibility="collapsed")
        with c2:
            if sel and on_open and st.button("Open in research", key="fd_om_open", use_container_width=True):
                try:
                    on_open(sel)
                except Exception:
                    pass
        if sel:
            d = L.E.stock_detail(res, sel)
            if d:
                lv = d["levels"]
                body = L._grid("", L._card(sel, f'expiry {L._day(lv.get("m_exp"), False)}', L._level_bar(lv) + L._levels(lv)))
                if d.get("chain"):
                    body += L._chain_cards(d["chain"], lv.get("fut"))
                if d.get("hist"):
                    body += L._grid("", L._card("Six months", "end of day", L._hist_charts(d["hist"])))
                wrap(body)
        return
    x = next(v for v in idx if v["name"] == pick)
    lv = x["levels"]
    ch = x.get("chain") or {}
    which = "m"
    if ch.get("w"):
        which = "w" if (st.pills("Expiry", ["Monthly", "Weekly"], default="Monthly", key=f"fd_om_exp_{x['sym']}", label_visibility="collapsed") or "Monthly") == "Weekly" else "m"
    wrap(L._grid("", L._card("Levels into expiry", f'expiry {L._day(lv.get("m_exp"), False)}', L._level_bar(lv) + L._levels(lv))) + L._chain_cards(ch.get(which), lv.get("fut"))
         + L._grid("", L._card("Six months", "end of day", L._hist_charts(x.get("hist") or {}))))


# ───────────── evidence ─────────────
def _log_rows() -> list:
    p = ROOT / "log.csv"
    try:
        return _read_csv(str(p), _stamp(p)) if p.exists() else []
    except Exception:
        return []


def _evidence(S, names):
    ld, sw = S["leaders"], S["swing"]
    w = {x["key"]: x for x in ld["record"]["windows"]}
    a = w["all"]
    used = lambda st_: _act("In use", "b") if st_ == "on" else _act("On trial", "w") if st_ == "trial" else _act("Off")
    rows = [[f'<span class="s w"><b>Hold the 20 strongest stocks</b><small>12-month strength · weekly check</small></span>', used(ld["status"]), str(ld["record"]["trades"].get("n", "—")),
             _pct(ld["record"]["trades"].get("win"), 0, False), f'{_pct(a["book"].get("cagr"))} a year', f'{_pct((a["book"].get("cagr") or 0) - (a["avg"].get("cagr") or 0))} a year', "—"]]
    s, pk = sw["record"]["all"], (sw["record"]["all"].get("pick") or {})
    rows.append([f'<span class="s w"><b>Leader pullback</b><small>strong stock, sharp 5-session fall</small></span>', used(sw["status"]), str(s.get("n", "—")), _pct(s.get("win"), 0, False), f'{_pct(s.get("exp"), 2)} a trade',
                 f'{_pct(pk.get("edge"), 2)} a trade', _num(pk.get("t"), 1)])
    for x in S.get("index") or []:
        r, oh = x["record"]["all"], (x["record"]["all"].get("over_hold") or {})
        rows.append([f'<span class="s w"><b>{_e(x["name"])}: dip in an uptrend</b><small>fall of 2.5% above the 200-day</small></span>', used(x["status"]), str(r.get("n", "—")), _pct(r.get("win"), 0, False),
                     f'{_pct(r.get("exp"), 2)} a trade', f'{_pct(oh.get("edge"), 2)} over holding', _num(oh.get("t"), 1)])
    for r in (S.get("evidence") or {}).get("rejected") or []:
        rows.append([f'<span class="s w"><b>{_e(r["name"])}</b><small>{_e(r["rule"].split(".")[0])}</small></span>', _act("Rejected", "s"), str(r.get("n", "—")), _pct(r.get("win"), 0, False), f'{_pct(r.get("exp"), 2)} a trade',
                     f'{_pct(r.get("pick"), 2)} a trade', _num(r.get("pick_t"), 1)])
    for x in S.get("index") or []:
        h = x.get("high20") or {}
        rows.append([f'<span class="s w"><b>{_e(x["name"])}: new 20-day high</b><small>hold 10 sessions</small></span>', _act("Rejected", "s"), str(h.get("n", "—")), "—", f'{_pct(h.get("exp"), 2)} a trade',
                     f'{_pct(h.get("edge"), 2)} over holding', _num(h.get("t"), 1)])
    tested = _t([("Idea", "l"), ("Verdict", "l"), ("Trades", "x"), ("Won", "x"), ("Result", ""), ("Edge", ""), ("t", "x")], rows, scroll=False)
    D = (S.get("evidence") or {}).get("deciles") or {}
    dec = ""
    if D.get("all"):
        dec = (_vbars(["weak", "", "", "", "", "", "", "", "", "strong"], D["all"], fmt=lambda v: f"{v * 100:.1f}") + f'<div class="fd-n" style="margin-top:8px">Average move over the next 21 sessions for ten groups of stocks sorted by '
               f'12-month strength, {D.get("all_n", 0)} monthly snapshots. Last 3 years: weakest {_pct(D["last3"][0], 1)}, strongest {_pct(D["last3"][-1], 1)}. This is the effect the Leaders book harvests, and why the '
               'weakest group is the stay-away list.</div>')
    bad = [str(y["y"]) for y in ld["record"]["years"] if not y.get("part") and _ok(y.get("book")) and y["book"] < 0]
    bad_years = (", ".join(bad[:-1]) + " and " + bad[-1]) if len(bad) > 1 else (bad[0] if bad else "no full year so far")
    static = ['Stops on the Leaders book (4 ATR trail, 20% and 25% from entry, exit below the 200-day): lower return, same worst fall.',
              'Selling everything when NIFTY 500 is below its 200-day: return fell from about 20% to 7 to 13% a year.',
              'Selling a holding as soon as it leaves the 350 most traded: same return, a quarter more trades, many of them one-week round trips. Holdings are now kept while among the 500 most traded.',
              'Breadth filters and a "recent breakouts are working" gauge on breakouts: no help in either half of the history.',
              '10 holdings instead of 20: much more luck, and worse in the first half of the history. 30 holdings: a little steadier.',
              'The earlier F&O factor model (19 inputs): no forecasting skill on five years of real data. It no longer places orders.']
    limits = ['The price history leaves out companies that were later delisted, so old results look better than they were. The last three years are least affected.',
              'These rules were chosen in November 2026 after testing about thirty ideas on this same history. Expect less going forward than the record shows.',
              'Costs are 0.15% a side for stocks. Taxes, slippage on thin days and circuit limits are not in the numbers. Demergers and rights issues are not adjusted in the price files.',
              f'The Leaders book has fallen {_pct(-(a["book"].get("dd") or 0), 0, False)} from a peak at its worst, and lost money in {bad_years}. Only use money you can leave alone through that.',
              'NSE\'s own momentum indexes beat their parent indexes over 20 years, yet an index fund on one of them launched in late 2024 was still under water in mid-2026. Momentum has long bad stretches.',
              'End-of-day data only: no intraday signals, and fills are assumed at the next open.']
    log = _log_rows()
    fnum = lambda v: _px(float(v)) if str(v).replace(".", "", 1).isdigit() else "—"
    lrows = [[_dshort(r.get("date", "")), _e(str(r.get("book", "")).capitalize()), _act(r.get("action", "").capitalize(), "b" if r.get("action") == "BUY" else "s"), f'<b style="color:var(--tx)">{_e(r.get("sym", ""))}</b>',
              fnum(r.get("price", "")), fnum(r.get("stop", "")), _e(r.get("note", ""))] for r in reversed(log[-40:])]
    lg = _t([("Signal day", "l"), ("Book", "l"), ("", "l"), ("Symbol", "l"), ("Close", ""), ("Stop", "x"), ("Note", "l x")], lrows) if lrows else _empty(
        f'<b>No order has been issued yet.</b> The live record starts on {_day(S.get("live_since"))}; every order is written to arka_data/signals/log.csv on the day it is issued and is never edited.')
    health = _ks([_k("Prices to", _day(S["asof"]), f'built {_dshort(str(S.get("built", ""))[:10])}'), _k("History from", _day(S.get("first")), f'{S.get("top", 350)} most traded'),
                  _k("Live books since", _day(S.get("live_since")), "forward only"), _k("Replay matches live", "No" if ld.get("drift") else "Yes", "history unchanged" if not ld.get("drift") else "history was revised")], 2, True)
    li = lambda xs: '<ul class="fd-list">' + "".join(f"<li>{_e(x)}</li>" for x in xs) + "</ul>"
    avoid = [[f'<span class="s">{_sym(r["sym"], names)}</span>', _px(r["price"]), _num((r.get("rank") or 0) * 100, 0), _sg(r.get("below200"), 0), ("F&O" if r.get("fno") else "")] for r in S.get("avoid") or []]
    _html(_grid(_pan("What was tested, and what survived", "edge = beyond the average liquid stock, or beyond just holding the index", tested, "c8 t12", True), _pan("Strength today, return next month", "% over the next 21 sessions", dec or _empty("Not computed."), "c4 t12"))
          + _grid(_pan("Also tested, not used", "November 2026", li(static), "c4 t6"), _pan("Limits you should know", "read before using real money", li(limits), "c4 t6"),
                  _pan("Stay-away list", "weakest tenth by strength, below the 200-day", _t([("Symbol", "l"), ("Close", ""), ("Strength", ""), ("vs 200-day", ""), ("", "x")], avoid) if avoid else _empty("None."), "c4 t12", True))
          + _grid(_pan("Live record: orders as they were issued", f'{len(log)} logged', lg, "c8 t12", True), _pan("Data health", "", health, "c4 t12", True)))


# ───────────── charts on demand ─────────────
def _chart_picker(S, C, syms, key, marks_for, on_open=None):
    syms = [s for s in dict.fromkeys(syms) if s in (C.get("s") or {})]
    if not syms:
        return
    c1, c2 = st.columns([3, 1])
    with c1:
        sel = st.selectbox("Chart", syms, index=0, key=key, label_visibility="collapsed")
    with c2:
        if sel and on_open and st.button("Open in research", key=key + "_open", use_container_width=True):
            try:
                on_open(sel)
            except Exception:
                pass
    if not sel:
        return
    marks = [m for m in marks_for(sel) if _ok(m[0])]
    d, s = C["d"], C["s"][sel]
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Candlestick(x=d, open=s["o"], high=s["h"], low=s["l"], close=s["c"], name=sel, increasing_line_color="#30D158", decreasing_line_color="#FF453A",
                                     increasing_fillcolor="#30D158", decreasing_fillcolor="#FF453A", showlegend=False))
        fig.add_trace(go.Scatter(x=d, y=s.get("m"), mode="lines", line=dict(color="#FF9F0A", width=1.3), name="50-day average", hoverinfo="skip"))
        for v, lab, _c in marks:
            fig.add_hline(y=v, line_dash="dot", line_width=1, line_color="#FF453A" if lab == "Stop" else "#A1A1AA", annotation_text=f"{lab} {_px(v)}", annotation_position="top left",
                          annotation_font=dict(size=10, color="#A1A1AA"))
        fig.update_layout(height=340, margin=dict(l=6, r=6, t=26, b=6), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, system-ui, sans-serif", size=11, color="#A1A1AA"),
                          title=dict(text=f"{sel} · {len(d)} sessions · candles with the 50-day average", font=dict(size=12, color="#EDEDEF"), x=0.01, y=0.98), hovermode="x unified", showlegend=False,
                          xaxis=dict(type="category", nticks=8, showgrid=False, rangeslider=dict(visible=False), tickfont=dict(size=10)), yaxis=dict(side="right", gridcolor="rgba(127,127,127,.15)", tickfont=dict(size=10)))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key + "_fig")
    except Exception:
        _html(_grid(_pan(f"{sel}", f"{len(d)} sessions · candles with the 50-day average", _candles(d, s, marks), "c12 t12")))


# ───────────── page ─────────────
def _setup_note() -> str:
    return ('<b>The signal files are not in the repository yet.</b><br>1. Add <b>signal_engine.py</b> to the repository (next to app.py).<br>'
            '2. Add <b>.github/workflows/signals.yml</b>.<br>3. In GitHub open Actions, choose "Signals", press Run workflow and wait for the green tick (about three minutes).<br>'
            'The desk fills in on its own after that, and refreshes every weekday evening.')


def render_fno(supabase=None, on_open=None):
    st.markdown(CSS3, unsafe_allow_html=True)
    S = signals()
    if not S or S.get("v") != 3:
        _html(_pan("Signal desk", "", _empty(_setup_note()), "", True))
        tab = st.pills("View", ["Options map"], default="Options map", key="fd_tab0", label_visibility="collapsed")
        if tab:
            _options_map(on_open)
        return
    C = _charts()
    names = _names()
    _html(_ticker(S))
    tab = st.pills("View", TABS, default="Desk", key="fd_tab", label_visibility="collapsed") or "Desk"
    try:
        if tab in ("Desk", "Leaders", "Swing", "Index"):
            sz = _sizing()
        if tab == "Desk":
            _desk(S, C, sz, names)
        elif tab == "Leaders":
            _leaders(S, C, sz, names, on_open)
        elif tab == "Swing":
            _swing(S, C, sz, names, on_open)
        elif tab == "Index":
            _index(S, C, sz)
        elif tab == "Big players":
            _players(S, names)
        elif tab == "Options map":
            _options_map(on_open)
        else:
            _evidence(S, names)
    except Exception as e:
        _html(_pan("This view could not be drawn", "", _empty(f"{_e(type(e).__name__)}: {_e(str(e)[:200])}. The other tabs still work; running the Signals workflow again usually fixes a half-written file."), "", True))
    _html('<div class="fd-n" style="margin-top:4px">End-of-day signals from fixed rules tested on past prices. They can and do lose money. Not investment advice.</div>')


# ───────────── for other pages ─────────────
def home_desk() -> dict | None:
    """What Home shows about the desk. None when the signal files are not there yet."""
    S = signals()
    if not S or S.get("v") != 3:
        return None
    ld, sw = S["leaders"], S["swing"]
    w = {x["key"]: x for x in ld["record"]["windows"]}
    orders = order_rows(S, _saved_sizing())
    return {"asof": S["asof"], "orders": [{"book": o["book"], "act": o["act"], "sym": o["sym"], "ref": o["ref"], "stop": o["stop"]} for o in orders],
            "leaders": {"status": ld["status"], "cagr": w["all"]["book"].get("cagr"), "index": w["all"]["index"].get("cagr"), "held": len(ld["holdings"]), "dd_now": ld["record"].get("dd_now"),
                        "check_today": ld.get("check_today"), "top": [{"sym": h["sym"], "pnl": h["pnl"]} for h in ld["holdings"][:3]]},
            "swing": {"status": sw["status"], "new": len(sw["new"]), "open": len(sw["open"]), "win": sw["record"]["all"].get("win"), "exp": sw["record"]["all"].get("exp")},
            "index": [{"name": x["name"], "status": x["status"], "action": x["action"], "last": x["last"], "r5": x.get("r5"), "trigger": x.get("trigger"), "above200": x.get("above200"),
                       "pcr": (x.get("fno") or {}).get("pcr"), "max_pain": (x.get("fno") or {}).get("max_pain"), "put_wall": (x.get("fno") or {}).get("put_wall"),
                       "call_wall": (x.get("fno") or {}).get("call_wall"), "iv": (x.get("fno") or {}).get("iv")} for x in S.get("index") or []]}


def home_rows(wait: float = 2.0) -> list | None:
    """Rows in the shape the earlier Home card expects (one per index), filled from the new desk."""
    D = home_desk()
    if D is None:
        return []
    word = {"buy": "Buy the dip at the open", "sell": "Sell at the open", "hold": "In a dip trade", "none": "No signal"}
    out = []
    for x in D["index"]:
        out.append({"name": x["name"], "verdict": word[x["action"]], "tone": "up" if x["action"] in ("buy", "hold") else "flat", "asof": D["asof"], "pcr": x["pcr"], "iv": x["iv"],
                    "max_pain": x["max_pain"], "put_wall": x["put_wall"], "call_wall": x["call_wall"], "move5": None, "fut": x["last"], "proven": 0, "reason": "", "forecast": None,
                    "conf": STATUS.get(x["status"], STATUS["off"])[0], "p_up": None, "signal": word[x["action"]]})
    return out


def signal_for(sym: str) -> tuple:
    """(what the desk says about this stock, short detail) for the Research page."""
    S = signals()
    if not S or S.get("v") != 3:
        return "", ""
    sym = str(sym).upper()
    ld, sw = S["leaders"], S["swing"]
    for r in ld.get("buy") or []:
        if r["sym"] == sym:
            return "Leaders book: buy at the next open", f'12-month strength {_num((r.get("rank") or 0) * 100, 0)} of 100'
    for h in ld.get("holdings") or []:
        if h["sym"] == sym:
            word = "sell at the next open" if h["flag"] == "sell" else "held, on watch" if h["flag"] == "risk" else "held"
            return f"Leaders book: {word}", f'since {_day(h["since"])} · {_pct(h["pnl"], 0)} · strength {_num((h.get("rank") or 0) * 100, 0)} of 100'
    for r in sw.get("new") or []:
        if r["sym"] == sym:
            return "Swing: buy at the next open", f'stop about {_px(r["stop"])}'
    for r in sw.get("open") or []:
        if r["sym"] == sym:
            return ("Swing: sell at the next open" if r.get("sell") else "Swing: open trade"), f'entry {_px(r["entry"])} · stop {_px(r["stop"])} · {_pct(r["pnl"], 1)}'
    for r in S.get("avoid") or []:
        if r["sym"] == sym:
            return "On the stay-away list", "weakest tenth by 12-month strength and below its 200-day average"
    for r in ld.get("candidates") or []:
        if r["sym"] == sym:
            return "Leaders book: next in line", f'12-month strength {_num((r.get("rank") or 0) * 100, 0)} of 100'
    return "No signal on the desk", ""
