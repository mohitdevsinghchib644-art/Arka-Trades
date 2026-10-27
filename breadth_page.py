"""
Arka Trades — Market Breadth page (v2)

* Data refreshes ONCE per trading day at 4:15 PM IST (Mon-Fri) in a background thread
  (see breadth_engine.py). Opening the page / changing the lookback reads stored data only.
* First ever run backfills ~2 years of history automatically; progress is shown live.
* Shows: Above/Below 20/50/200 DMA counts + daily change, advances/declines, new 52-week
  highs/lows, participation trend, and an optional Chartink cross-check.
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

import breadth_engine as be

_CSS = """
<style>
.bt-title{font:800 15px 'Inter',sans-serif;color:#E8E8E8;letter-spacing:.02em;margin:2px 0 2px}
.bt-title i{font-style:normal;color:#FF9F0A}
.bt-sub{font:400 11px 'Inter',sans-serif;color:#6B6B6B;margin-bottom:10px}
.bt-strip{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px}
.bt-chip{display:inline-flex;align-items:center;gap:7px;font:600 9.5px 'JetBrains Mono',monospace;letter-spacing:.06em;padding:6px 10px;border:1px solid #262626;background:#0A0A0A;color:#8A8A8A;text-transform:uppercase;white-space:nowrap}
.bt-chip b{color:#E8E8E8;font-weight:700}
.bt-chip.ok{border-color:#30D15855;color:#30D158}.bt-chip.run{border-color:#FF9F0A66;color:#FF9F0A}.bt-chip.bad{border-color:#FF453A66;color:#FF453A}
.bt-dot{width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block}
.bt-chip.run .bt-dot{animation:btp 1.2s ease-in-out infinite}
@keyframes btp{0%,100%{opacity:1}50%{opacity:.25}}
.bt-card{background:#0A0A0A;border:1px solid #1B1B1B;border-top:2px solid #262626;padding:11px 13px;min-height:84px}
.bt-card .l{font:600 9px 'JetBrains Mono',monospace;letter-spacing:.12em;color:#5A5A5A;text-transform:uppercase}
.bt-card .v{font:700 22px 'JetBrains Mono',monospace;color:#E8E8E8;margin-top:6px;font-variant-numeric:tabular-nums}
.bt-card .m{font:500 10px 'JetBrains Mono',monospace;color:#6B6B6B;margin-top:5px}
.bt-card.up{border-top-color:#30D158}.bt-card.dn{border-top-color:#FF453A}.bt-card.am{border-top-color:#FF9F0A}
.bt-bar{height:3px;background:#1a1a1a;margin-top:8px}.bt-bar i{display:block;height:3px;background:#FF9F0A}
.bt-up{color:#30D158}.bt-dn{color:#FF453A}.bt-fl{color:#6B6B6B}
.bt-h{font:700 10px 'JetBrains Mono',monospace;letter-spacing:.14em;color:#FF9F0A;text-transform:uppercase;margin:20px 0 8px;padding-bottom:7px;border-bottom:1px solid #1B1B1B}
.bt-wrap{overflow:auto;max-height:520px;border:1px solid #1B1B1B;background:#080808}
.bt-t{border-collapse:separate;border-spacing:0;width:100%;font:11px 'JetBrains Mono',monospace;font-variant-numeric:tabular-nums}
.bt-t th,.bt-t td{padding:7px 10px;white-space:nowrap;border-right:1px solid #161616;border-bottom:1px solid #161616;text-align:right}
.bt-t thead tr.g th{position:sticky;top:0;z-index:3;background:#111;color:#FF9F0A;font-weight:700;text-align:center;font-size:10px;letter-spacing:.05em}
.bt-t thead tr.s th{position:sticky;top:31px;z-index:2;background:#0B0B0B;color:#5A5A5A;font-weight:700;font-size:9px;letter-spacing:.08em}
.bt-t td.d,.bt-t th.d{text-align:left;font-weight:700;color:#E8E8E8;position:sticky;left:0;background:#0A0A0A}
.bt-t tbody tr:nth-child(even) td{background:#0B0B0B}.bt-t tbody tr:hover td{background:#121212}
.bt-t tbody tr:first-child td{box-shadow:inset 0 1px 0 #FF9F0A55}
.bt-note{font:400 10.5px 'Inter',sans-serif;color:#6B6B6B;margin-top:8px;line-height:1.6}
.bt-warn{border:1px solid #FF9F0A55;background:#120D04;color:#E3A33A;padding:12px 14px;font:11px 'JetBrains Mono',monospace;line-height:1.7;margin:10px 0}
.bt-err{border:1px solid #FF453A55;background:#140808;color:#F0706A;padding:12px 14px;font:11px 'JetBrains Mono',monospace;line-height:1.7;margin:10px 0;white-space:pre-wrap}
</style>
"""


# ───────────────────────── formatting ─────────────────────────
def _n(v):
    try:
        return None if pd.isna(v) else int(round(float(v)))
    except Exception:
        return None


def _fmt(v) -> str:
    n = _n(v)
    return "-" if n is None else f"{n:,}"


def _dlt(v) -> str:
    n = _n(v)
    if n is None:
        return "-"
    return f"+{n:,}" if n > 0 else f"{n:,}"


def _cls(v, good_up=True) -> str:
    n = _n(v)
    if n is None or n == 0:
        return "bt-fl"
    return "bt-up" if (n > 0) == good_up else "bt-dn"


def _when(ts: str | None) -> str:
    d = be._parse_ts(ts)
    return "never" if d is None else d.astimezone(be.IST).strftime("%a %d %b · %I:%M %p IST").replace(" 0", " ")


# ───────────────────────── status ─────────────────────────
def _status_chips(meta: dict, hist: pd.DataFrame) -> str:
    running = be.STATE.get("running")
    due = be.is_due(meta, hist.empty)
    err = meta.get("last_error")
    if running:
        state = f'<span class="bt-chip run"><span class="bt-dot"></span>UPDATING · {_h.escape(str(be.STATE.get("progress") or "working"))}</span>'
    elif err and not hist.empty:
        state = '<span class="bt-chip bad"><span class="bt-dot"></span>LAST REFRESH FAILED · SHOWING STORED DATA</span>'
    elif err:
        state = '<span class="bt-chip bad"><span class="bt-dot"></span>REFRESH FAILED · RETRYING</span>'
    elif due:
        state = '<span class="bt-chip run"><span class="bt-dot"></span>REFRESH DUE</span>'
    else:
        state = '<span class="bt-chip ok"><span class="bt-dot"></span>UP TO DATE</span>'
    latest = "-" if hist.empty else pd.Timestamp(hist.iloc[-1]["date"]).strftime("%a %d %b %Y")
    chips = [
        state,
        f'<span class="bt-chip">DATA THROUGH <b>{latest}</b></span>',
        f'<span class="bt-chip">LAST REFRESH <b>{_when(meta.get("last_success"))}</b></span>',
        f'<span class="bt-chip">NEXT AUTO REFRESH <b>{be.next_run_time().strftime("%a %d %b · %I:%M %p IST").replace(" 0", " ")}</b></span>',
    ]
    if meta.get("universe"):
        chips.append(f'<span class="bt-chip">UNIVERSE <b>{int(meta["universe"]):,} STOCKS</b></span>')
    return '<div class="bt-strip">' + "".join(chips) + "</div>"


@st.fragment(run_every=3)
def _live_status() -> None:
    """Refreshes itself every 3 s; when a background refresh finishes, reloads the page data once."""
    meta, hist = be.load_meta(max_age=0), be.load_history(max_age=0)
    st.markdown(_status_chips(meta, hist), unsafe_allow_html=True)
    fin = be.STATE.get("finished", 0.0)
    if not be.STATE.get("running") and fin and fin != st.session_state.get("_bt_seen_finish"):
        st.session_state["_bt_seen_finish"] = fin
        st.rerun()


# ───────────────────────── components ─────────────────────────
def _card(label: str, value: str, meta: str, tone: str = "", pct: float | None = None) -> str:
    bar = f'<div class="bt-bar"><i style="width:{max(0, min(100, pct)):.0f}%"></i></div>' if pct is not None else ""
    return f'<div class="bt-card {tone}"><div class="l">{label}</div><div class="v">{value}</div><div class="m">{meta}</div>{bar}</div>'


def _summary(hist: pd.DataFrame) -> None:
    last = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) > 1 else None
    cols = st.columns(4)
    for col, ma in zip(cols[:3], (20, 50, 200)):
        a, b = _n(last.get(f"above_{ma}dma")), _n(last.get(f"below_{ma}dma"))
        tot = (a or 0) + (b or 0)
        pct = (a / tot * 100) if (a is not None and tot) else None
        d = (a - _n(prev.get(f"above_{ma}dma"))) if (prev is not None and a is not None and _n(prev.get(f"above_{ma}dma")) is not None) else None
        tone = "" if pct is None else "up" if pct >= 60 else "dn" if pct < 40 else "am"
        meta = f'{"-" if pct is None else f"{pct:.1f}%"} OF {tot:,} · <span class="{_cls(d)}">{_dlt(d)} vs PREV</span>'
        col.markdown(_card(f"ABOVE {ma} DMA", _fmt(a), meta, tone, pct), unsafe_allow_html=True)
    cols[3].markdown(_card("LATEST SESSION", pd.Timestamp(last["date"]).strftime("%d %b %Y"), f"{len(hist):,} SESSIONS STORED"), unsafe_allow_html=True)

    c2 = st.columns(4)
    adv, dec = _n(last.get("advances")), _n(last.get("declines"))
    ratio = f"{adv / dec:.2f}" if (adv is not None and dec) else "-"
    c2[0].markdown(_card("ADVANCES / DECLINES", f'{_fmt(adv)} / {_fmt(dec)}', f"A/D RATIO {ratio}", "up" if (adv or 0) > (dec or 0) else "dn"), unsafe_allow_html=True)
    net5 = hist["net_advances"].tail(5).astype("float").sum() if "net_advances" in hist else None
    c2[1].markdown(_card("NET ADVANCES (5 SESSIONS)", _dlt(net5), f'TODAY {_dlt(_n(last.get("net_advances")))}', "up" if (net5 or 0) > 0 else "dn"), unsafe_allow_html=True)
    nh, nl = _n(last.get("new_52w_high")), _n(last.get("new_52w_low"))
    c2[2].markdown(_card("NEW 52-WEEK HIGH / LOW", f'{_fmt(nh)} / {_fmt(nl)}', "CLOSE-BASED", "up" if (nh or 0) > (nl or 0) else "dn"), unsafe_allow_html=True)
    c2[3].markdown(_card("STOCKS COUNTED", _fmt(last.get("universe")), "WITH A REAL BAR THAT SESSION"), unsafe_allow_html=True)


def _grouped_table(hist: pd.DataFrame, lookback: int) -> None:
    df = hist.sort_values("date").tail(lookback + 1).copy()
    for ma in (20, 50, 200):
        df[f"a{ma}d"] = df[f"above_{ma}dma"].astype("float").diff()
        df[f"b{ma}d"] = df[f"below_{ma}dma"].astype("float").diff()
    df = df.tail(lookback).sort_values("date", ascending=False)
    body = []
    for _, r in df.iterrows():
        cells = [f'<td class="d">{pd.Timestamp(r["date"]).strftime("%d %b %Y")}</td>']
        for ma in (20, 50, 200):
            cells.append(f'<td>{_fmt(r[f"above_{ma}dma"])}</td><td class="{_cls(r[f"a{ma}d"])}">{_dlt(r[f"a{ma}d"])}</td>')
            cells.append(f'<td>{_fmt(r[f"below_{ma}dma"])}</td><td class="{_cls(r[f"b{ma}d"], good_up=False)}">{_dlt(r[f"b{ma}d"])}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    head = ('<thead><tr class="g"><th class="d" rowspan="2">DATE</th>'
            + "".join(f'<th colspan="2">ABOVE {m} DMA</th><th colspan="2">BELOW {m} DMA</th>' for m in (20, 50, 200))
            + '</tr><tr class="s">' + "<th>COUNT</th><th>Δ</th>" * 6 + "</tr></thead>")
    st.markdown(f'<div class="bt-wrap"><table class="bt-t">{head}<tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)
    st.markdown('<div class="bt-note">Δ = change in stock count vs the previous session. A stock counts only when it has a valid moving average that session. Red Δ on "Below" columns is a rise in weak stocks.</div>', unsafe_allow_html=True)


def _adv_table(hist: pd.DataFrame, lookback: int) -> None:
    df = hist.sort_values("date").tail(lookback).sort_values("date", ascending=False)
    body = []
    for _, r in df.iterrows():
        adv, dec = _n(r.get("advances")), _n(r.get("declines"))
        ratio = f"{adv / dec:.2f}" if (adv is not None and dec) else "-"
        net = _n(r.get("net_advances"))
        body.append(f'<tr><td class="d">{pd.Timestamp(r["date"]).strftime("%d %b %Y")}</td><td class="bt-up">{_fmt(adv)}</td><td class="bt-dn">{_fmt(dec)}</td>'
                    f'<td>{_fmt(r.get("unchanged"))}</td><td class="{_cls(net)}">{_dlt(net)}</td><td>{ratio}</td>'
                    f'<td class="bt-up">{_fmt(r.get("new_52w_high"))}</td><td class="bt-dn">{_fmt(r.get("new_52w_low"))}</td><td>{_fmt(r.get("universe"))}</td></tr>')
    head = ('<thead><tr class="g"><th class="d">DATE</th><th>ADVANCES</th><th>DECLINES</th><th>UNCHANGED</th><th>NET</th><th>A/D</th>'
            '<th>52W HIGHS</th><th>52W LOWS</th><th>STOCKS</th></tr></thead>')
    st.markdown(f'<div class="bt-wrap" style="max-height:380px"><table class="bt-t">{head}<tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)


def _trend(hist: pd.DataFrame, lookback: int) -> None:
    c = hist.sort_values("date").tail(lookback).set_index("date")
    out = pd.DataFrame(index=c.index)
    for ma in (20, 50, 200):
        a = pd.to_numeric(c[f"above_{ma}dma"], errors="coerce")
        b = pd.to_numeric(c[f"below_{ma}dma"], errors="coerce")
        out[f"Above {ma} DMA %"] = a / (a + b).replace(0, pd.NA) * 100
    st.line_chart(out.astype(float), height=280, use_container_width=True)


def _chartink_strip(meta: dict, hist: pd.DataFrame) -> None:
    ck = meta.get("chartink")
    if not ck or hist.empty:
        return
    last = hist.iloc[-1]
    rows = []
    for key, lbl in (("above_20dma", "ABOVE 20"), ("above_50dma", "ABOVE 50"), ("above_200dma", "ABOVE 200")):
        a, c = _n(last.get(key)), _n(ck.get(key))
        if a is None or c is None:
            continue
        rows.append(f'<span class="bt-chip">{lbl} · ARKA <b>{a:,}</b> · CHARTINK <b>{c:,}</b> · Δ <b class="{_cls(a - c, good_up=True)}">{_dlt(a - c)}</b></span>')
    if rows:
        st.markdown('<div class="bt-h">CHARTINK CROSS-CHECK</div><div class="bt-strip">' + "".join(rows) + "</div>"
                    '<div class="bt-note">Chartink counts its own universe and may use different adjusted prices, so small differences are normal. Large gaps usually mean a different stock universe.</div>', unsafe_allow_html=True)


def _diagnostics(meta: dict, hist: pd.DataFrame) -> None:
    with st.expander("DIAGNOSTICS & MAINTENANCE"):
        rows = [("Last successful refresh", _when(meta.get("last_success"))), ("Last attempt", _when(meta.get("last_attempt"))),
                ("Last error", meta.get("last_error") or "none"), ("Mode of last run", meta.get("mode") or "-"),
                ("Universe", meta.get("universe_label") or "-"), ("Stocks with price data", meta.get("universe") or "-"),
                ("Symbols with no data", meta.get("missing") if meta.get("missing") is not None else "-"),
                ("Stored sessions", len(hist)), ("Chartink cross-check", meta.get("chartink_error") or ("ok" if meta.get("chartink") else "not run yet")),
                ("Supabase persistence", meta.get("persist_error") or "ok")]
        st.markdown('<div class="bt-note">' + "<br>".join(f"<b>{_h.escape(str(k))}</b> · {_h.escape(str(v))}" for k, v in rows) + "</div>", unsafe_allow_html=True)
        if meta.get("persist_error"):
            st.caption("To keep history across app restarts, run once in Supabase → SQL Editor:")
            st.code("create table if not exists breadth_store (\n  key text primary key, value jsonb not null, updated_at timestamptz default now());\n"
                    "alter table breadth_store disable row level security;", language="sql")
        if "FALLBACK" in str(meta.get("universe_label", "")):
            st.warning("Only ~100 large caps are being counted. For true full-market breadth, add nse_universe.csv (column SYMBOL) to your repo root — download EQUITY_L.csv from NSE archives and rename it.")
        c1, c2 = st.columns(2)
        if c1.button("REBUILD FULL HISTORY (2-YEAR BACKFILL)", use_container_width=True, disabled=bool(be.STATE.get("running"))):
            be.start_sync("backfill")
            st.rerun()
        if c2.button("CLEAR STORED DATA", use_container_width=True, disabled=bool(be.STATE.get("running"))):
            be.clear_stored()
            st.rerun()


# ───────────────────────── entry point ─────────────────────────
def render_market_breadth() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    be.ensure_scheduler()
    be.maybe_start()                       # starts a background refresh ONLY if one is due (never blocks)

    st.markdown('<div class="bt-title"><i>▍</i>MARKET BREADTH</div>'
                '<div class="bt-sub">Daily participation across the NSE universe · 20 / 50 / 200 DMA · refreshed automatically once a day, Mon–Fri 4:15 PM IST</div>',
                unsafe_allow_html=True)
    _live_status()

    c1, c2, _sp = st.columns([1.1, 1.3, 4])
    lookback = c1.selectbox("LOOKBACK (SESSIONS)", [20, 50, 90, 180], index=0, key="bt_lookback")
    c2.markdown("<div style='height:27px'></div>", unsafe_allow_html=True)
    if c2.button("REFRESH NOW", use_container_width=True, disabled=bool(be.STATE.get("running")),
                 help="Normally not needed — data refreshes by itself every trading day at 4:15 PM IST."):
        be.start_sync("daily")
        st.rerun()

    meta, hist = be.load_meta(max_age=0), be.load_history(max_age=0)
    if hist.empty:
        if be.STATE.get("running"):
            st.markdown('<div class="bt-warn">BUILDING THE BREADTH DATABASE FOR THE FIRST TIME.<br>This downloads ~2 years of NSE prices once and takes a few minutes. '
                        'The page updates by itself when it finishes — you can keep using other modules meanwhile.</div>', unsafe_allow_html=True)
        elif meta.get("last_error"):
            st.markdown(f'<div class="bt-err">BREADTH REFRESH FAILED\n\n{_h.escape(str(meta["last_error"]))}\n\nIt retries automatically every {be.RETRY_AFTER_FAIL_MIN} minutes.</div>', unsafe_allow_html=True)
        _diagnostics(meta, hist)
        return

    _summary(hist)
    st.markdown('<div class="bt-h">DAILY MOVING-AVERAGE BREADTH</div>', unsafe_allow_html=True)
    _grouped_table(hist, lookback)
    st.markdown('<div class="bt-h">PARTICIPATION TREND · % OF STOCKS ABOVE EACH MOVING AVERAGE</div>', unsafe_allow_html=True)
    _trend(hist, lookback)
    st.markdown('<div class="bt-h">ADVANCES / DECLINES & 52-WEEK EXTREMES</div>', unsafe_allow_html=True)
    _adv_table(hist, lookback)
    _chartink_strip(meta, hist)
    _diagnostics(meta, hist)
    st.markdown(f'<div class="bt-note">SOURCE · {_h.escape(str(hist.iloc[-1].get("source") or meta.get("source") or "Arka"))} · EOD · '
                f'{len(hist):,} sessions stored. Breadth is descriptive market context, not a trading signal.</div>', unsafe_allow_html=True)
