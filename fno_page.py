"""
Arka Trades · Futures and options page (fno_page.py)

Shows what fno_engine.py reads from the stored F&O history: a verdict for NIFTY 50 and BANK NIFTY with its tested record,
the levels that matter into expiry, who holds what, every signal with its backtest, a ranked table of all F&O stocks, and
a defined-risk option structure that fits the view.

Nothing here calls an outside service. The data comes from the files the "F&O data" workflow saves in arka_data/fno/.

Public API:  render_fno(supabase=None, on_open=None)
"""
from __future__ import annotations

import html
import math
from datetime import date

import streamlit as st

import fno_engine as E

GRADE = {"proven": ("Proven", "g"), "leaning": ("Leaning", "a"), "unproven": ("Not proven", "n"), "thin": ("Too few", "n")}
TONE = {"up": "up", "dn": "dn", "flat": "fl"}

CSS = """<style>
.fo{font-family:var(--sans,'Inter',system-ui,sans-serif);font-variant-numeric:tabular-nums;container-type:inline-size;width:100%;color:var(--tx);}
.fo *{box-sizing:border-box;}
.fo .up{color:var(--green,#22C55E);} .fo .dn{color:var(--red,#EF4444);} .fo .fl{color:var(--tx2);} .fo .am{color:var(--amber,#F59E0B);}
.fo-chips{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px;}
.fo-chip{display:inline-flex;gap:6px;align-items:center;padding:6px 11px;border-radius:8px;background:var(--s1);border:1px solid var(--line);font:500 12.5px var(--sans);color:var(--tx2);white-space:nowrap;}
.fo-chip b{font-weight:600;color:var(--tx);}
.fo-g{display:grid;grid-template-columns:1fr;gap:14px;margin-bottom:14px;}
.fo-card{background:var(--s1);border:1px solid var(--line);border-radius:12px;overflow:hidden;min-width:0;}
.fo-ct{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:11px 14px;border-bottom:1px solid var(--line);}
.fo-ct b{font:600 13.5px var(--sans);color:var(--tx);} .fo-ct span{font:400 12px var(--sans);color:var(--tx3);text-align:right;}
.fo-cb{padding:14px;}
.fo-vd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
.fo-vd b{font:650 24px/1.15 var(--sans);letter-spacing:-.01em;} .fo-vd span{font:500 12.5px var(--sans);color:var(--tx3);}
.fo-meter{position:relative;height:6px;border-radius:3px;margin:12px 0 4px;background:linear-gradient(90deg,rgba(239,68,68,.55),rgba(113,113,122,.35) 42%,rgba(113,113,122,.35) 58%,rgba(34,197,94,.55));}
.fo-meter i{position:absolute;top:-4px;width:3px;height:14px;border-radius:2px;background:var(--tx);transform:translateX(-50%);}
.fo-scale{display:flex;justify-content:space-between;font:400 11px var(--sans);color:var(--tx3);}
.fo-tx{font:400 13.5px/1.55 var(--sans);color:var(--tx2);margin-top:10px;}
.fo-rec{margin-top:12px;padding:10px 12px;border-radius:9px;background:var(--s2);border:1px solid var(--line);font:400 12.5px/1.55 var(--sans);color:var(--tx2);}
.fo-rec b{color:var(--tx);font-weight:600;} .fo-rec small{display:block;font:600 11.5px var(--sans);color:var(--tx3);margin-bottom:3px;}
.fo-why{margin-top:12px;display:grid;gap:7px;}
.fo-why div{display:flex;gap:8px;font:400 13px/1.5 var(--sans);color:var(--tx2);}
.fo-why i{flex:0 0 7px;height:7px;border-radius:50%;margin-top:6px;background:var(--tx3);} .fo-why i.up{background:var(--green,#22C55E);} .fo-why i.dn{background:var(--red,#EF4444);}
.fo-sub{font:600 12px var(--sans);color:var(--tx3);margin:16px 0 8px;}
.fo-kvs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--s1);border:1px solid var(--line);border-radius:9px;overflow:hidden;}
.fo-kv{background:var(--s1);padding:9px 11px;min-width:0;box-shadow:0 0 0 1px var(--line);}
.fo-kv small{display:block;font:500 11.5px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.fo-kv b{display:block;font:600 15px/1.3 var(--sans);color:var(--tx);margin-top:2px;} .fo-kv em{display:block;font:400 11.5px var(--sans);font-style:normal;color:var(--tx3);margin-top:1px;}
.fo-bar{position:relative;height:30px;margin:6px 0 2px;}
.fo-bar .tr{position:absolute;left:0;right:0;top:13px;height:4px;border-radius:2px;background:var(--s3);}
.fo-bar .bd{position:absolute;top:9px;height:12px;border-radius:3px;background:rgba(161,161,170,.18);border:1px solid var(--line2);}
.fo-bar i{position:absolute;top:5px;width:3px;height:20px;border-radius:2px;transform:translateX(-50%);}
.fo-leg{display:flex;gap:12px;flex-wrap:wrap;font:400 11.5px var(--sans);color:var(--tx3);} .fo-leg i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;}
.fo-sp{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:4px;}
.fo-sp div{min-width:0;} .fo-sp small{font:500 11.5px var(--sans);color:var(--tx3);} .fo-sp svg{display:block;width:100%;height:34px;margin-top:3px;}
.fo-idea b.nm{font:650 16px var(--sans);color:var(--tx);} .fo-idea .tag{font:500 11px var(--sans);color:var(--tx3);border:1px solid var(--line2);border-radius:6px;padding:2px 7px;margin-left:8px;white-space:nowrap;}
.fo-legs{display:grid;gap:6px;margin-top:10px;}
.fo-legs div{display:flex;justify-content:space-between;gap:10px;padding:7px 10px;border-radius:8px;background:var(--s2);font:500 13px var(--sans);}
.fo-legs div b{font-weight:600;} .fo-legs div span{color:var(--tx3);}
.fo-who{display:grid;gap:10px;}
.fo-who .r{display:grid;grid-template-columns:84px 1fr 96px;gap:10px;align-items:center;font:500 12.5px var(--sans);color:var(--tx2);}
.fo-who .t{height:8px;border-radius:4px;background:rgba(239,68,68,.45);overflow:hidden;} .fo-who .t i{display:block;height:100%;background:rgba(34,197,94,.75);}
.fo-who .r b{text-align:right;color:var(--tx);font-weight:600;}
.fo-tw{overflow:auto;max-height:560px;}
.fo-t{width:100%;border-collapse:collapse;font:400 13px var(--sans);}
.fo-t th{position:sticky;top:0;z-index:1;background:var(--s1);text-align:right;font:600 11.5px var(--sans);color:var(--tx3);padding:9px 8px;border-bottom:1px solid var(--line);white-space:nowrap;}
.fo-t td{text-align:right;padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap;color:var(--tx2);}
.fo-t th:first-child,.fo-t td:first-child{text-align:left;padding-left:14px;} .fo-t th:last-child,.fo-t td:last-child{padding-right:14px;} .fo-t td:first-child{color:var(--tx);font-weight:600;}
.fo-t td.l,.fo-t th.l{text-align:left;} .fo-t td.w{white-space:normal;min-width:150px;}
.fo-t tr:last-child td{border-bottom:0;} .fo-t tr.on td:first-child{box-shadow:inset 3px 0 0 var(--c,#3B82F6);}
.fo-t small{display:block;font:400 11.5px var(--sans);color:var(--tx3);font-weight:400;white-space:normal;}
.fo-pill{display:inline-block;padding:2px 8px;border-radius:6px;font:600 11.5px var(--sans);white-space:nowrap;}
.fo-pill.g{background:rgba(34,197,94,.14);color:var(--green,#22C55E);} .fo-pill.r{background:rgba(239,68,68,.14);color:var(--red,#EF4444);}
.fo-pill.a{background:rgba(245,158,11,.14);color:var(--amber,#F59E0B);} .fo-pill.n{background:var(--s3);color:var(--tx3);}
.fo-note{font:400 12.5px/1.6 var(--sans);color:var(--tx3);margin-top:10px;}
.fo-empty{padding:18px;font:400 13.5px/1.6 var(--sans);color:var(--tx2);}
.fo .xw{display:none;}
@container (min-width:620px){.fo-kvs{grid-template-columns:repeat(3,minmax(0,1fr));} .fo .xw{display:table-cell;} .fo-t td.w{min-width:170px;}}
@container (min-width:900px){.fo-g.two{grid-template-columns:repeat(2,minmax(0,1fr));} .fo-g.split{grid-template-columns:minmax(0,5fr) minmax(0,7fr);}
  .fo-kvs{grid-template-columns:repeat(3,minmax(0,1fr));} .fo-vd b{font-size:27px;}}
@container (max-width:619px){.fo-cb{padding:12px;} .fo-t th,.fo-t td{padding:8px 8px;} .fo-who .r{grid-template-columns:64px 1fr 84px;}}
</style>"""


# ───────────── small helpers ─────────────
def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _ok(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and x == x and not math.isinf(x)


def _num(x, d=0) -> str:
    return f"{x:,.{d}f}" if _ok(x) else "—"


def _px(x) -> str:
    return "—" if not _ok(x) else f"{x:,.0f}" if x >= 5000 else f"{x:,.1f}" if x >= 100 else f"{x:,.2f}"


def _pct(x, d=1, sign=True) -> str:
    return "—" if not _ok(x) else f"{x * 100:{'+' if sign else ''}.{d}f}%"


def _cls(x) -> str:
    return "fl" if not _ok(x) or abs(x) < 1e-9 else "up" if x > 0 else "dn"


def _col(x, d=1) -> str:
    return f'<span class="{_cls(x)}">{_pct(x, d)}</span>'


def _rank(p) -> str:
    """A percentile said the way a person would say it."""
    if not _ok(p):
        return "not enough history"
    return f"higher than {p * 100:.0f}% of the last year"


def _day(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%b %Y')}"
    except Exception:
        return "—"


def _short(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%b')}"
    except Exception:
        return "—"


def _spark(vals, color="var(--tx2)") -> str:
    v = [x for x in vals if _ok(x)]
    if len(v) < 5:
        return '<svg viewBox="0 0 100 34"></svg>'
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(v) - 1) * 100:.2f},{31 - (x - lo) / rng * 28:.2f}" for i, x in enumerate(v))
    return (f'<svg viewBox="0 0 100 34" preserveAspectRatio="none"><polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5" '
            'vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>')


def _card(title, right, body, flush=False) -> str:
    return (f'<div class="fo-card"><div class="fo-ct"><b>{_e(title)}</b><span>{right}</span></div>'
            f'<div class="{"" if flush else "fo-cb"}">{body}</div></div>')


def _kv(label, val, sub="") -> str:
    return f'<div class="fo-kv"><small>{_e(label)}</small><b>{val}</b>{f"<em>{sub}</em>" if sub else ""}</div>'


# ───────────── index card ─────────────
def _record(x: dict) -> str:
    tr, h = x.get("track") or {}, E.MAIN_H
    st_ = (tr.get("by") or {}).get(x["level"])
    if not tr.get("tested_days"):
        return ('<div class="fo-rec"><small>Record of this verdict</small>Not available yet: the verdict is only scored on days after the first two years '
                'of history, so the model is never graded on data it learnt from.</div>')
    if not st_:
        return (f'<div class="fo-rec"><small>Record of this verdict</small>Over {tr["tested_days"]:,} tested sessions this verdict came up too rarely to have a '
                'record. Treat it as unproven.</div>')
    return (f'<div class="fo-rec"><small>Record of this verdict, on sessions the model had never seen</small>'
            f'It came up on <b>{st_["n"]:,} sessions</b> in <b>{st_["neff"]} separate episodes</b>. Over the next {h} sessions the index then moved '
            f'<b class="{_cls(st_["mean"])}">{_pct(st_["mean"])}</b> on average, against {_pct(st_["base"])} on a normal day, and was higher '
            f'<b>{st_["hit"] * 100:.0f}%</b> of the time (normal: {st_["base_hit"] * 100:.0f}%).</div>')


def _level_bar(lv: dict) -> str:
    fut, mv = lv.get("fut"), lv.get("move10")
    marks = [(lv.get("put_wall"), "var(--green,#22C55E)", "Put wall"), (lv.get("max_pain"), "var(--amber,#F59E0B)", "Max pain"),
             (fut, "var(--tx)", "Price"), (lv.get("call_wall"), "var(--red,#EF4444)", "Call wall")]
    pts = [m[0] for m in marks if _ok(m[0]) and m[0] > 0]
    if not _ok(fut) or len(pts) < 2:
        return ""
    lo, hi = min(pts + ([fut - mv] if _ok(mv) else [])), max(pts + ([fut + mv] if _ok(mv) else []))
    pad = (hi - lo) * 0.06 or fut * 0.01
    lo, hi = lo - pad, hi + pad
    pos = lambda v: max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))
    band = f'<div class="bd" style="left:{pos(fut - mv):.1f}%;width:{pos(fut + mv) - pos(fut - mv):.1f}%"></div>' if _ok(mv) else ""
    ticks = "".join(f'<i style="left:{pos(v):.1f}%;background:{c}" title="{_e(n)} {_px(v)}"></i>' for v, c, n in marks if _ok(v) and v > 0)
    leg = "".join(f'<span><i style="background:{c}"></i>{_e(n)} {_px(v)}</span>' for v, c, n in marks if _ok(v) and v > 0)
    if _ok(mv):
        leg += f'<span><i style="background:rgba(161,161,170,.35)"></i>Normal 10-session swing ±{_px(mv)}</span>'
    return f'<div class="fo-bar"><div class="tr"></div>{band}{ticks}</div><div class="fo-leg">{leg}</div>'


def _levels(lv: dict) -> str:
    fut = lv.get("fut")
    cells = [_kv("Future", _px(fut), f'{_col(lv.get("r1"), 2)} today · {_col(lv.get("r5"))} in 5 sessions'),
             _kv("Put-call ratio", _num(lv.get("pcr"), 2), _rank(lv.get("pcr_pct"))),
             _kv("Implied volatility", _pct(lv.get("iv"), 1, False), _rank(lv.get("iv_pct"))),
             _kv("Max pain", _px(lv.get("max_pain")), f'expiry {_short(lv.get("m_exp"))} · {lv.get("dte")} days' if lv.get("dte") is not None else ""),
             _kv("Put wall · call wall", f'{_px(lv.get("put_wall"))} · {_px(lv.get("call_wall"))}', "strikes with the most open interest"),
             _kv("Futures open interest", _col(lv.get("oi5")), "change over 5 sessions")]
    if _ok(lv.get("move5")) and _ok(fut):
        cells.append(_kv("Priced move, 5 sessions", f'±{_px(lv["move5"])}', f'{_px(fut - lv["move5"])} to {_px(fut + lv["move5"])}'))
    if _ok(lv.get("move_exp")) and _ok(fut):
        cells.append(_kv("Priced move to expiry", f'±{_px(lv["move_exp"])}', f'{_px(fut - lv["move_exp"])} to {_px(fut + lv["move_exp"])}'))
    if _ok(lv.get("roll")):
        cells.append(_kv("Rolled to next month", _pct(lv["roll"], 0, False), "share of futures open interest"))
    if lv.get("w_exp") and _ok(lv.get("w_max_pain")):
        cells.append(_kv(f'Weekly max pain · {_short(lv["w_exp"])}', _px(lv["w_max_pain"]),
                         f'walls {_px(lv.get("w_put_wall"))} · {_px(lv.get("w_call_wall"))}'))
    return f'<div class="fo-kvs">{"".join(cells)}</div>'


def _idea(idea: dict) -> str:
    if not idea or not idea.get("name"):
        return f'<div class="fo-tx">{_e((idea or {}).get("why") or "No structure today.")}</div>'
    out = [f'<div class="fo-idea"><b class="nm">{_e(idea["name"])}</b><span class="tag">Rule-based · not backtested</span></div>',
           f'<div class="fo-tx">{_e(idea["why"])}</div>']
    if idea.get("legs"):
        rows = "".join(f'<div><b class="{"dn" if l["act"] == "Sell" else "up"}">{l["act"]} {_px(l["strike"])} {"call" if l["typ"] == "CE" else "put"}</b>'
                       f'<span>{("at " + _num(l["px"], 2)) if _ok(l.get("px")) else "price not stored"}</span></div>' for l in idea["legs"])
        out.append(f'<div class="fo-legs">{rows}</div>')
    if _ok(idea.get("max_gain")):
        be = " and ".join(_px(b) for b in idea["breakeven"])
        lot = f' per lot of {idea["lot"]:,.0f}' if _ok(idea.get("lot")) else " per unit"
        out.append('<div class="fo-kvs" style="margin-top:10px">'
                   + _kv("Most you can make", f'<span class="up">₹{_num(idea["max_gain"])}</span>', lot.strip())
                   + _kv("Most you can lose", f'<span class="dn">₹{_num(idea["max_loss"])}</span>', lot.strip())
                   + _kv("Breakeven", be, f'{"receive" if idea["net"] > 0 else "pay"} {_num(abs(idea["net"]), 2)} a unit') + '</div>')
    if idea.get("note"):
        out.append(f'<div class="fo-note">{_e(idea["note"])}</div>')
    if idea.get("legs"):
        out.append('<div class="fo-note">Premiums are the last stored closing prices, not live quotes. Check the live chain, margin and costs before placing anything.</div>')
    return "".join(out)


def _index_card(x: dict) -> str:
    tone, lv = TONE[x["tone"]], x["levels"]
    z = max(-0.6, min(0.6, x.get("z") or 0.0))
    c = x.get("counts", {})
    head = (f'<div class="fo-vd"><b class="{tone}">{_e(x["verdict"])}</b><span>next {E.MAIN_H} sessions</span></div>'
            f'<div class="fo-meter"><i style="left:{(z + 0.6) / 1.2 * 100:.1f}%"></i></div>'
            '<div class="fo-scale"><span>Correction risk</span><span>No edge</span><span>Rally setup</span></div>'
            f'<div class="fo-tx">{_e(x["line"])}</div>')
    why = ""
    if x.get("reasons"):
        why = '<div class="fo-why">' + "".join(f'<div><i class="{r["dir"]}"></i><span>{_e(r["text"])}'
                                              f'{" Leaning, not yet proven." if r["grade"] == "leaning" else ""}</span></div>' for r in x["reasons"][:4]) + '</div>'
    hist = x.get("hist") or {}
    sp = ('<div class="fo-sp">' + "".join(f'<div><small>{n}</small>{_spark(hist.get(k) or [], col)}</div>'
                                         for n, k, col in (("Price, 6 months", "px", "var(--tx)"), ("Put-call ratio", "pcr", "var(--c,#3B82F6)"),
                                                           ("Implied volatility", "iv", "var(--amber,#F59E0B)"))) + '</div>')
    body = (head + _record(x) + why + '<div class="fo-sub">Levels into expiry</div>' + _level_bar(lv) + '<div style="height:10px"></div>' + _levels(lv)
            + '<div class="fo-sub">Last six months</div>' + sp + '<div class="fo-sub">Option structure that fits this view</div>' + _idea(x.get("idea")))
    right = f'{c.get("proven", 0)} proven · {c.get("leaning", 0)} leaning of {sum(c.values())} signals'
    return _card(x["name"], right, body)


# ───────────── who holds what ─────────────
def _who(res: dict) -> str:
    m = res.get("market") or {}
    rows = []
    for name, k in (("FIIs", "fii_ls"), ("Clients", "cli_ls"), ("Pro desks", "pro_ls"), ("DIIs", "dii_ls")):
        v = m.get(k)
        if _ok(v):
            rows.append(f'<div class="r"><span>{name}</span><div class="t"><i style="width:{v * 100:.1f}%"></i></div><b>{v * 100:.0f}% long</b></div>')
    if not rows:
        return '<div class="fo-empty">Participant positions are not stored yet. They arrive with the next run of the F&O data workflow.</div>'
    out = [f'<div class="fo-who">{"".join(rows)}</div>',
           '<div class="fo-note">Share of each group\'s index-futures contracts that are long. Green is long, red is short.</div>']
    kv = []
    if _ok(m.get("fii_ls_pct")):
        kv.append(_kv("FII long share", _pct(m.get("fii_ls"), 0, False), _rank(m["fii_ls_pct"])))
    if _ok(m.get("fii_ls_d5")):
        kv.append(_kv("FII change, 5 sessions", f'<span class="{_cls(m["fii_ls_d5"])}">{m["fii_ls_d5"] * 100:+.1f} pts</span>', "of their long share"))
    if _ok(m.get("fii_opt")):
        kv.append(_kv("FII index options, net", f'<span class="{_cls(m["fii_opt"])}">{m["fii_opt"]:+,.0f}</span>', "contracts · calls less puts"))
    if _ok(m.get("br_above")):
        kv.append(_kv("F&O stocks in uptrends", _pct(m["br_above"], 0, False), "above their 50-day average"))
    if _ok(m.get("br_flow")):
        kv.append(_kv("Build-up balance", f'<span class="{_cls(m["br_flow"])}">{m["br_flow"] * 100:+.0f} pts</span>', "long build-up less short build-up"))
    if kv:
        out.append(f'<div class="fo-kvs" style="margin-top:12px">{"".join(kv)}</div>')
    return "".join(out)


# ───────────── evidence table ─────────────
def _edge_cell(st_) -> str:
    if not st_:
        return '<td class="xw">—</td>'
    return f'<td class="xw">{_col(st_["edge"], 2)}</td>'


def _evidence(signals: list, relative: bool) -> str:
    order = {"proven": 0, "leaning": 1, "unproven": 2, "thin": 3}
    sig = sorted(signals, key=lambda s: (not s.get("on"), order[s["grade"]], -(abs(s["stats"][E.MAIN_H]["t"]) if s["stats"].get(E.MAIN_H) else 0)))
    rows = []
    for s in sig:
        m = s["stats"].get(E.MAIN_H)
        lab, cl = GRADE[s["grade"]]
        hit = f'{m["hit"] * 100:.0f}% <span class="fl">vs {m["base_hit"] * 100:.0f}%</span>' if m else "—"
        main = f'<td>{_col(m["edge"], 2)}</td>' if m else "<td>—</td>"
        today = '<b class="up">On</b>' if s.get("on") else '<span class="fl">Off</span>'
        rows.append(f'<tr class="{"on" if s.get("on") else ""}"><td class="w">{_e(s["label"])}<small>{_e(s["note"])}</small></td>{"" if relative else f"<td>{today}</td>"}'
                    f'{_edge_cell(s["stats"].get(5))}{main}{_edge_cell(s["stats"].get(20))}<td class="xw">{hit}</td>'
                    f'<td class="xw">{m["neff"] if m else "—"}</td><td><span class="fo-pill {cl}">{lab}</span></td></tr>')
    vs = "against other F&O stocks" if relative else "against a normal day"
    head = (f'<tr><th>Reading</th>{"" if relative else "<th>Today</th>"}<th class="xw">5 sessions</th><th>{E.MAIN_H} sessions</th><th class="xw">20 sessions</th>'
            f'<th class="xw">{"Beat the rest" if relative else "Ended higher"}</th><th class="xw">Episodes</th><th>Status</th></tr>')
    return (f'<div class="fo-tw"><table class="fo-t"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="fo-note" style="padding:0 14px 12px">The percentages are the extra move after the reading, {vs}, acting at the next day\'s close. '
            'Proven means a large edge against its noise, seen in at least 15 separate episodes, pointing the same way in both halves of the history. '
            '"Too few" means the reading has not happened often enough to judge. '
            'Only proven and leaning readings move the verdict.</div>')


# ───────────── stocks ─────────────
FILTERS = ["All", "Strong setups", "Weak setups", "Long build-up", "Short build-up", "Short covering", "Long unwinding", "Options expensive", "Options cheap", "My watchlist"]


def _mine() -> set:
    try:
        raw = list(st.session_state.get("watchlist", [])) + list(st.session_state.get("admin_watchlist", []))
        return {str(s).upper().replace(".NS", "").replace(".BO", "").strip() for s in raw}
    except Exception:
        return set()


def _pick(stocks: list, flt: str) -> list:
    if flt == "Strong setups":
        return [s for s in stocks if s["level"] > 0]
    if flt == "Weak setups":
        return [s for s in stocks if s["level"] < 0][::-1]
    if flt in ("Long build-up", "Short build-up", "Short covering", "Long unwinding"):
        return [s for s in stocks if s["buildup"] == flt]
    if flt == "Options expensive":
        return sorted([s for s in stocks if "iv_hi" in s["on"]], key=lambda s: -(s["levels"].get("iv_pct") or 0))
    if flt == "Options cheap":
        return sorted([s for s in stocks if "iv_lo" in s["on"]], key=lambda s: (s["levels"].get("iv_pct") or 0))
    if flt == "My watchlist":
        mine = _mine()
        return [s for s in stocks if s["sym"] in mine]
    return stocks


def _stock_table(rows: list) -> str:
    if not rows:
        return '<div class="fo-empty">No stock matches this filter today.</div>'
    out = []
    for s in rows:
        lv = s["levels"]
        cl = "g" if s["level"] > 0 else "r" if s["level"] < 0 else "n"
        bu = s["buildup"]
        bcl = "up" if bu in ("Long build-up", "Short covering") else "dn" if bu else "fl"
        ivp = lv.get("iv_pct")
        out.append(f'<tr><td>{_e(s["sym"])}</td><td class="l"><span class="fo-pill {cl}">{_e(s["verdict"])}</span></td><td>{_px(lv.get("fut"))}</td>'
                   f'<td>{_col(lv.get("r1"))}</td><td class="xw">{_col(lv.get("r5"))}</td><td class="xw">{_col(lv.get("oi5"))}</td>'
                   f'<td class="l {bcl}">{_e(bu) if bu else "—"}</td><td class="xw">{_num(lv.get("pcr"), 2)}</td>'
                   f'<td class="xw">{_pct(lv.get("iv"), 0, False)}</td><td class="xw">{f"{ivp * 100:.0f}" if _ok(ivp) else "—"}</td>'
                   f'<td class="xw">{_px(lv.get("max_pain"))}</td></tr>')
    head = ('<tr><th>Stock</th><th class="l">Setup</th><th>Future</th><th>Today</th><th class="xw">5 sessions</th><th class="xw">Open interest, 5 sessions</th>'
            '<th class="l">Build-up</th><th class="xw">Put-call</th><th class="xw">Volatility</th><th class="xw">Vol rank</th><th class="xw">Max pain</th></tr>')
    return f'<div class="fo-tw"><table class="fo-t"><thead>{head}</thead><tbody>{"".join(out)}</tbody></table></div>'


def _stock_record(res: dict) -> str:
    tr = res.get("stock_track") or {}
    by = tr.get("by") or {}
    if not tr.get("tested_days"):
        return ""
    bits = []
    for lvl, name in ((2, "Strong setup"), (1, "Leaning up"), (-1, "Leaning down"), (-2, "Weak setup")):
        s = by.get(lvl)
        if s:
            bits.append(f'<b>{name}</b> {_col(s["edge"], 2)} against other F&O stocks ({s["n"]:,} cases on {s.get("days", 0):,} days)')
    if not bits:
        return (f'<div class="fo-rec"><small>Record of the stock setups, on sessions the model had never seen</small>Over {tr["tested_days"]:,} tested '
                'sessions no setup came up often enough to have a record.</div>')
    return ('<div class="fo-rec"><small>Record of the stock setups over the next 10 sessions, on sessions the model had never seen</small>'
            + " · ".join(bits) + '</div>')


def _stock_detail(d: dict) -> str:
    lv, tone = d["levels"], TONE[d["tone"]]
    head = f'<div class="fo-vd"><b class="{tone}">{_e(d["verdict"])}</b><span>against other F&O stocks · next {E.MAIN_H} sessions</span></div><div class="fo-tx">{_e(d["line"])}</div>'
    why = ""
    if d.get("reasons"):
        why = '<div class="fo-why">' + "".join(f'<div><i class="{r["dir"]}"></i><span>{_e(r["text"])}</span></div>' for r in d["reasons"][:4]) + '</div>'
    return _card(d["sym"], d["buildup"] or "No build-up pattern", head + why + '<div class="fo-sub">Levels</div>' + _level_bar(lv)
                 + '<div style="height:10px"></div>' + _levels(lv))


# ───────────── page ─────────────
def _method(res: dict) -> str:
    notes = "".join(f"<br>· {_e(n)}" for n in res.get("notes", []))
    return ('<div class="fo-note"><b style="color:var(--tx2)">How to read this page.</b> Every reading is a fixed rule on the daily NSE files saved in your '
            'repository. Each one is tested on the whole history: what did the price do after it, compared with a normal day, if you acted at the next '
            'day\'s close. The verdict adds up only the readings that pass the test, and is itself graded on sessions that came after the data it learnt '
            'from. With about thirty readings under test, roughly one useless reading in a hundred can still pass as proven by luck, so read a verdict '
            'as odds, never as a promise. Costs, slippage and taxes are not included. This is analysis of public data, not investment advice.'
            f'{notes}</div>')


def render_fno(supabase=None, on_open=None):
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        with st.spinner("Reading the stored F&O history…"):
            res = E.run_cached()
    except Exception as e:
        st.markdown(f'<div class="fo"><div class="fo-card"><div class="fo-empty"><b>The F&O history could not be read.</b><br>{_e(type(e).__name__)}: '
                    f'{_e(str(e)[:200])}</div></div></div>', unsafe_allow_html=True)
        return
    if not res.get("ok") or not res.get("indexes"):
        msg = " ".join(res.get("notes") or []) or "No F&O history found yet."
        st.markdown('<div class="fo"><div class="fo-card"><div class="fo-empty"><b>No F&O data to analyse yet.</b><br>' + _e(msg)
                    + '<br>In GitHub open Actions, choose "F&O data", press Run workflow, type 5 in the years box and wait for the green tick. '
                      'The page fills in on its own after that.</div></div></div>', unsafe_allow_html=True)
        return

    idx = list(res["indexes"].values())
    n_proven = sum(x["counts"].get("proven", 0) for x in idx) + sum(1 for s in res.get("stock_signals", []) if s["grade"] == "proven")
    chips = (f'<span class="fo-chip">Data to <b>{_day(res["asof"])}</b></span>'
             f'<span class="fo-chip"><b>{res["days"]:,}</b> sessions of history</span>'
             f'<span class="fo-chip"><b>{res.get("n_stocks", 0)}</b> F&O stocks</span>'
             f'<span class="fo-chip"><b>{n_proven}</b> proven readings</span>'
             '<span class="fo-chip">End-of-day data · updates after 8 pm</span>')
    cards = "".join(_index_card(x) for x in idx)
    st.markdown(f'<div class="fo"><div class="fo-chips">{chips}</div><div class="fo-g two">{cards}</div></div>', unsafe_allow_html=True)

    # evidence, one market at a time
    names = [x["name"] for x in idx] + ["F&O stocks"]
    pick = st.pills("Evidence for", names, default=names[0], key="fo_ev", label_visibility="collapsed") or names[0]
    if pick == "F&O stocks":
        ev = _card("Every reading and its record · F&O stocks", "Tested on all stocks together, day by day", _evidence(res.get("stock_signals", []), True), flush=True)
    else:
        x = next(v for v in idx if v["name"] == pick)
        ev = _card(f"Every reading and its record · {pick}", f'{x["days"]:,} sessions tested', _evidence(x["signals"], False), flush=True)
    st.markdown(f'<div class="fo"><div class="fo-g split">{_card("Who holds what", "Index futures and breadth", _who(res))}{ev}</div></div>',
                unsafe_allow_html=True)

    # stocks
    stocks = res.get("stocks", [])
    if stocks:
        flt = st.pills("Stocks", FILTERS, default="All", key="fo_flt", label_visibility="collapsed") or "All"
        rows = _pick(stocks, flt)
        up, dn = sum(1 for s in stocks if s["level"] > 0), sum(1 for s in stocks if s["level"] < 0)
        table = _card("F&O stocks", f"{len(rows)} shown · {up} leaning up · {dn} leaning down · ranked by score", _stock_table(rows), flush=True)
        st.markdown(f'<div class="fo">{_stock_record(res)}<div style="height:12px"></div><div class="fo-g">{table}</div></div>', unsafe_allow_html=True)
        c1, c2 = st.columns([3, 1])
        syms = sorted(s["sym"] for s in stocks)
        with c1:
            sel = st.selectbox("Look at one stock", syms, index=None, placeholder="Look at one stock", key="fo_sym", label_visibility="collapsed")
        with c2:
            if sel and on_open and st.button("Open in research", key="fo_open", use_container_width=True):
                try:
                    on_open(sel)
                except Exception:
                    pass
        if sel:
            d = E.stock_detail(res, sel)
            if d:
                st.markdown(f'<div class="fo"><div class="fo-g">{_stock_detail(d)}</div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="fo">{_method(res)}</div>', unsafe_allow_html=True)


# ───────────── compact card for Home ─────────────
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
        lv = x["levels"]
        out.append({"name": x["name"], "verdict": x["verdict"], "tone": x["tone"], "asof": res.get("asof"), "pcr": lv.get("pcr"), "iv": lv.get("iv"),
                    "max_pain": lv.get("max_pain"), "put_wall": lv.get("put_wall"), "call_wall": lv.get("call_wall"), "move5": lv.get("move5"), "fut": lv.get("fut"),
                    "proven": x["counts"].get("proven", 0), "reason": (x["reasons"][0]["label"] if x.get("reasons") else "")})
    return out
