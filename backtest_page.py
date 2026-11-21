"""
Arka Trades — backtest_page.py (v3): write your setup in plain words, pick a stock or an index, run.

  1. What to test     one search box: type any NSE stock symbol or an index name (NIFTY 50, BANK NIFTY …).
  2. Your setup       a text box. Write the setup the way you would explain it to another trader.
  3. How I read it    every phrase is shown back as an exact rule before anything runs. Anything not understood is
                      listed in red and is NOT tested. Numbers that had to be assumed are marked.
  4. Run              the result: verdict, expectancy, equity curve, the trades on the chart, year by year, every trade.

Nothing is added to your rules. The reading is done by setup_language.py (fixed patterns, not an AI model), the
simulation by backtest_engine.py (signals at the close, entry the next day, Indian costs).

Public API: render_backtest(on_open=None)
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

import numpy as np
import pandas as pd
import streamlit as st

import backtest_engine as bt
import quant_data as qd
import quant_ui as ui
import setup_language as sl

INDICES = {
    "NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "SENSEX": "^BSESN", "NIFTY NEXT 50": "^NSMIDCP", "NIFTY 500": "^CRSLDX",
    "FIN NIFTY": "NIFTY_FIN_SERVICE.NS", "NIFTY IT": "^CNXIT", "NIFTY AUTO": "^CNXAUTO", "NIFTY PHARMA": "^CNXPHARMA", "NIFTY FMCG": "^CNXFMCG",
    "NIFTY METAL": "^CNXMETAL", "NIFTY REALTY": "^CNXREALTY", "NIFTY ENERGY": "^CNXENERGY", "NIFTY PSU BANK": "^CNXPSUBANK",
    "NIFTY MEDIA": "^CNXMEDIA", "NIFTY INFRA": "^CNXINFRA",
}
_ALIAS = {"NIFTY": "NIFTY 50", "NIFTY50": "NIFTY 50", "BANKNIFTY": "BANK NIFTY", "NIFTY BANK": "BANK NIFTY", "FINNIFTY": "FIN NIFTY", "NIFTYIT": "NIFTY IT"}
YEARS = [5, 10, 3, 8]
MAX_ITEMS = 25


# ═════════════════════════ what to test ═════════════════════════
def _options() -> list:
    wl = [str(s).upper() for s in (st.session_state.get("watchlist") or []) + (st.session_state.get("admin_watchlist") or [])]
    try:
        known = sorted(qd.FALLBACK)
    except Exception:
        known = []
    return list(dict.fromkeys(list(INDICES) + wl + known))


def resolve(name: str):
    """What the user typed → (label, Yahoo ticker, is_index) or None."""
    s = re.sub(r"\s+", " ", str(name or "").upper().strip())
    if not s:
        return None
    s = _ALIAS.get(s, s)
    if s in INDICES:
        return s, INDICES[s], True
    if s.startswith("^"):
        return s, s, True
    s = s.replace(".NS", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9&\-_.]{1,25}", s):
        return None
    return s, f"{s}.NS", False


@st.cache_data(ttl=3 * 3600, show_spinner=False, max_entries=6)
def _panel(items: tuple, years: int, day: str) -> dict:
    """Prices for exactly the instruments asked for (plus NIFTY 50 for the calendar). Raises, so failures are not cached."""
    start = (qd._now() - timedelta(days=int(years * 365.25) + 420)).strftime("%Y-%m-%d")
    want = list(dict.fromkeys([qd.INDEX_TICKER, qd.VIX_TICKER] + [t for _, t, _ in items]))
    df = None
    for _ in range(2):
        try:
            df = qd._yf_download(want, start)
            break
        except Exception:
            df = None
    frames = {}
    for t in want:
        f = qd._frame_from(df, t, single=len(want) == 1)
        if f is not None:
            f = qd.drop_unfinished(f)
            if len(f) >= 60:
                frames[t] = f
    if qd.INDEX_TICKER not in frames:
        raise RuntimeError("Yahoo Finance did not return NIFTY 50 history, which sets the trading calendar. Try again in a minute.")
    fr, labels, missing, no_vol = {qd.INDEX_TICKER: frames[qd.INDEX_TICKER]}, [], [], []
    if qd.VIX_TICKER in frames:
        fr[qd.VIX_TICKER] = frames[qd.VIX_TICKER]
    for label, t, _ in items:
        if t in frames:
            fr[f"{label}.NS"] = frames[t]                    # build_panel looks instruments up as "<label>.NS"
            labels.append(label)
        else:
            missing.append(label)
    if not labels:
        raise RuntimeError("No price history came back for: " + ", ".join(missing) + ". Check the NSE symbols.")
    p = qd.build_panel(fr, labels, {}, years, "Backtest")
    for j, label in enumerate(p["symbols"]):
        v = p["v"][:, j]
        if not np.nansum(v[-250:]) > 0:
            p["v"][:, j] = np.nan                            # an index has no traded volume: volume rules can then never be true
            no_vol.append(label)
    qd.add_indicators(p)
    p["missing"], p["no_vol"] = missing, no_vol
    return p


# ═════════════════════════ page pieces ═════════════════════════
def _css():
    st.markdown(f"""<style>
    div[class*="st-key-bx_card"]{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:16px;padding:16px 18px 10px;margin-bottom:14px}}
    .bx-h{{display:flex;align-items:center;gap:10px;font:650 16px {ui.SANS};color:var(--tx,{ui.TX});margin:0 0 4px}}
    .bx-h em{{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;font:650 12.5px {ui.SANS};
             font-style:normal;color:{ui.AMBER};background:rgba(255,159,10,.12);border:1px solid rgba(255,159,10,.3)}}
    .bx-d{{font:13.5px/1.55 {ui.SANS};color:var(--tx3,{ui.TX3});margin:0 0 10px 34px}}
    div[class*="st-key-bx_card"] textarea{{font:14.5px/1.65 {ui.SANS} !important;border-radius:12px !important;padding:14px !important}}
    .bx-read{{font-family:{ui.SANS};border:1px solid var(--line,#1E1E21);border-radius:16px;background:var(--s1,#0E0E10);padding:16px 18px;margin-bottom:14px}}
    .bx-rt{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font:650 16px {ui.SANS};color:var(--tx,{ui.TX});margin-bottom:4px}}
    .bx-rt span{{font:500 12.5px {ui.SANS};color:var(--tx3,{ui.TX3})}}
    .bx-sec{{font:600 12.5px {ui.SANS};color:var(--tx3,{ui.TX3});margin:14px 0 4px}}
    .bx-it{{display:grid;grid-template-columns:22px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid #17171B;font:14px/1.5 {ui.SANS};color:#E4E4E8}}
    .bx-it:last-child{{border-bottom:0}}
    .bx-it i{{width:20px;height:20px;margin-top:1px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:700 11px {ui.SANS};font-style:normal}}
    .bx-it.ok i{{color:{ui.GREEN};background:rgba(48,209,88,.13)}} .bx-it.ex i{{color:{ui.AMBER};background:rgba(255,159,10,.13)}}
    .bx-it.no i{{color:{ui.RED};background:rgba(255,69,58,.13)}} .bx-it.as i{{color:{ui.YELLOW};background:rgba(255,214,10,.12)}}
    .bx-it small{{display:block;font:12.5px/1.5 {ui.SANS};color:var(--tx3,{ui.TX3});margin-top:2px}}
    .bx-it small.w{{color:{ui.YELLOW}}} .bx-it.no span{{color:#F2B8B3}} .bx-it q{{quotes:none;color:var(--tx2,{ui.TX2})}}
    .bx-empty{{font:13.5px/1.6 {ui.SANS};color:var(--tx3,{ui.TX3});padding:6px 0}}
    .bx-voc{{font-family:{ui.SANS}}} .bx-voc b{{display:block;font:600 12.5px {ui.SANS};color:var(--tx2,{ui.TX2});margin:10px 0 6px}}
    .bx-voc span{{display:inline-block;font:12.5px {ui.SANS};color:#D6D6DB;background:#16161A;border:1px solid #232328;border-radius:999px;padding:4px 11px;margin:0 6px 6px 0}}
    .bx-tr td:first-child,.bx-tr th:first-child{{text-align:left}}
    div[class*="st-key-bx_ex"] button{{min-height:34px !important;border-radius:999px !important;font:500 13px {ui.SANS} !important}}
    div[class*="st-key-bx_run"] button{{min-height:48px !important;border-radius:14px !important;font:650 15px {ui.SANS} !important}}
    @media (max-width:760px){{.bx-d{{margin-left:0}} div[class*="st-key-bx_card"]{{padding:14px 14px 8px}} .bx-read{{padding:14px}}}}
    </style>""", unsafe_allow_html=True)


def _use_example(name: str):
    st.session_state["bx_text"] = sl.EXAMPLES[name]


def _use_example_pick():
    name = st.session_state.get("bx_ex_pick")
    if name in sl.EXAMPLES:
        st.session_state["bx_text"] = sl.EXAMPLES[name]


def _reading_html(r: dict) -> str:
    entry = [x for x in r["read"] if x[3] == "entry"]
    out = []
    n_ok = len(entry)
    head = (f'{n_ok} buy condition{"s" if n_ok != 1 else ""} understood' + (f' · {len(r["missed"])} not understood' if r["missed"] else ""))
    out.append(f'<div class="bx-rt">How I read your setup<span>{ui.e(head)}</span></div>')
    out.append('<div class="bx-sec">Buy signal: every line below must be true at the day\'s close</div>')
    if entry:
        for words, meaning, note, _ in entry:
            out.append(f'<div class="bx-it ok"><i>✓</i><div>{ui.e(meaning)}<small>from <q>{ui.e(words)}</q></small>'
                       + (f'<small class="w">{ui.e(note)}</small>' if note else "") + "</div></div>")
    else:
        out.append('<div class="bx-empty">No buy condition yet. Write at least one, for example "close above 50 EMA" or "breakout of 20 day high".</div>')
    out.append('<div class="bx-sec">How the trade is taken and closed</div>')
    notes = {m: n for _, m, n, k in r["read"] if k == "exit" and n}
    for line in sl.exit_summary(r["ex"]):
        note = next((n for m, n in notes.items() if m[:22] == line[:22]), None)
        out.append(f'<div class="bx-it ex"><i>→</i><div>{ui.e(line)}' + (f'<small class="w">{ui.e(note)}</small>' if note else "") + "</div></div>")
    if r["book"].get("risk_pct"):
        out.append(f'<div class="bx-it ex"><i>→</i><div>Risk {r["book"]["risk_pct"]:g}% of capital on each trade</div></div>')
    if r["assumed"]:
        out.append('<div class="bx-sec">Filled in because your setup did not say</div>')
        out += [f'<div class="bx-it as"><i>!</i><div>{ui.e(a)}</div></div>' for a in r["assumed"]]
    if r["missed"]:
        out.append('<div class="bx-sec">Not understood, so NOT tested</div>')
        out += [f'<div class="bx-it no"><i>✕</i><div><span>{ui.e(words)}</span><small>{ui.e(why)}</small></div></div>' for words, why in r["missed"]]
    return '<div class="bx-read">' + "".join(out) + "</div>"


def _controls():
    with st.container(key="bx_card_what"):
        st.markdown('<div class="bx-h"><em>1</em>What do you want to test on?</div>'
                    '<div class="bx-d">Type a stock symbol or an index name and press Enter. You can add more than one.</div>', unsafe_allow_html=True)
        opts = _options()
        try:
            picked = st.multiselect("Stocks or indices", opts, key="bx_items", placeholder="Search: RELIANCE, TCS, NIFTY 50, BANK NIFTY …",
                                    label_visibility="collapsed", accept_new_options=True, max_selections=MAX_ITEMS)
        except TypeError:                                  # Streamlit without typed-in options
            picked = st.multiselect("Stocks or indices", opts, key="bx_items", placeholder="Search: RELIANCE, TCS, NIFTY 50 …", label_visibility="collapsed")
            other = st.text_input("Other NSE symbols, comma separated", key="bx_other", placeholder="e.g. ECLERX, STYL")
            picked = list(picked) + [x for x in re.split(r"[,;\s]+", other or "") if x]
        items, bad = [], []
        for nm in picked:
            got = resolve(nm)
            if got is None:
                bad.append(str(nm))
            elif got not in items:
                items.append(got)
        if bad:
            st.markdown(f'<div class="q-note" style="color:{ui.YELLOW}">Not a symbol I can use: {ui.e(", ".join(bad))}. Use the NSE symbol, for example TMPV or M&amp;M.</div>',
                        unsafe_allow_html=True)

    with st.container(key="bx_card_setup"):
        st.markdown('<div class="bx-h"><em>2</em>Write your setup</div>'
                    '<div class="bx-d">In your own words, one idea per line or separated by commas: when to buy, where the stop goes, when to exit.</div>',
                    unsafe_allow_html=True)
        text = st.text_area("Your setup", key="bx_text", height=190, label_visibility="collapsed",
                            placeholder="Example:\nClose above 50 EMA and 200 SMA\nBreakout of 20 day high with volume 1.5x average\n"
                                        "Stop loss at signal candle low\nExit when close below 21 EMA")
        if ui.phone():
            st.selectbox("Show me an example", list(sl.EXAMPLES), index=None, key="bx_ex_pick", placeholder="Show me an example",
                         label_visibility="collapsed", on_change=_use_example_pick)
        with st.container(key="bx_ex") if not ui.phone() else st.container(key="bx_ex_hidden"):
            cols = st.columns([1.1] + [1] * len(sl.EXAMPLES), gap="small") if not ui.phone() else []
            if cols:
                cols[0].markdown(f'<div style="font:500 12.5px {ui.SANS};color:{ui.TX3};padding-top:8px">Show me an example</div>', unsafe_allow_html=True)
            for c, name in zip(cols[1:], sl.EXAMPLES):
                c.button(name, key=f"bx_ex_{name}", on_click=_use_example, args=(name,), use_container_width=True)
        with st.expander("Words I understand"):
            st.markdown('<div class="bx-voc">' + "".join(f"<b>{ui.e(g)}</b>" + "".join(f"<span>{ui.e(x)}</span>" for x in xs) for g, xs in sl.VOCAB)
                        + '<b>Good to know</b><div class="q-note" style="margin:0">Numbers can be anything: "close above 30 EMA", "breakout of 55 day high", '
                          '"stop loss 4.5%". Weeks and months are converted to trading days (1 week = 5, 1 month = 21). Long trades on daily candles only. '
                          'If a line shows in red below, it was not understood and is left out of the test.</div></div>', unsafe_allow_html=True)

    r = sl.parse(text or "")
    if (text or "").strip():
        st.markdown(_reading_html(r), unsafe_allow_html=True)

    with st.expander("Capital, risk, costs and history"):
        a, b, c, d = st.columns(4, gap="small")
        years = a.selectbox("History", YEARS, key="bx_years", format_func=lambda y: f"{y} years")
        capital = b.number_input("Capital (₹)", 10000.0, 1e10, 1000000.0, 50000.0, key="bx_cap", format="%.0f")
        risk = c.number_input("Risk per trade (%)", 0.1, 10.0, 1.0, 0.25, key="bx_risk",
                              help="How much of the capital is lost when a trade hits its stop. Your setup text overrides this if it says \"risk 1% per trade\".")
        cost = d.number_input("Costs per round trip (%)", 0.0, 3.0, bt.COST_DEFAULT, 0.05, key="bx_cost",
                              help="Brokerage, STT, stamp duty, exchange and GST charges plus slippage, for buying and selling once.")
    if r["book"].get("risk_pct"):
        risk = float(r["book"]["risk_pct"])
    ready = bool(items) and bool(r["rules"])
    with st.container(key="bx_run"):
        run = st.button("Run backtest", type="primary", use_container_width=True, disabled=not ready, key="bx_go")
    if not ready:
        need = " and ".join(x for x in ("add a stock or index" if not items else "", "write at least one buy condition I can read" if not r["rules"] else "") if x)
        st.markdown(f'<div class="q-note">To run: {need}.</div>', unsafe_allow_html=True)
    cfg = {"items": [list(x) for x in items], "text": (text or "").strip(), "rules": r["rules"], "ex": r["ex"], "years": int(years),
           "capital": float(capital), "risk": float(risk), "cost": float(cost)}
    return cfg, r, run


# ═════════════════════════ run ═════════════════════════
def _run(cfg: dict, reading: dict):
    items = tuple(tuple(x) for x in cfg["items"])
    day = (qd._now() - timedelta(hours=16)).strftime("%Y%m%d")
    with st.spinner("Downloading prices and testing…"):
        try:
            p = _panel(items, cfg["years"], day)
        except Exception as exc:  # pragma: no cover - network
            return None, str(exc) or type(exc).__name__
        n = len(p["symbols"])
        book = bt.Book(capital=cfg["capital"], risk_pct=cfg["risk"], max_pos=max(1, n), max_pos_pct=100.0 / max(1, min(n, 4)), regime_mode="Off")
        res = bt.run_single(p, cfg["rules"], cfg["ex"], book, cfg["cost"], None, None)
    curve = res["curve"]
    if curve is not None and len(curve) > 1:
        ic = pd.Series(p["idx_c"], index=p["dates"]).reindex(curve.index).ffill()
        res["bench_curve"] = ic / ic.iloc[0] * 100
        res["bench_stats"] = bt.curve_stats(ic)
    s0 = res["start_i"]
    res.update(cfg=cfg, reading=reading, symbols=list(p["symbols"]), missing=list(p.get("missing") or []), no_vol=list(p.get("no_vol") or []),
               first=str(p["dates"][s0].date()), last=str(p["dates"][-1].date()), book=book,
               px={s: (p["dates"][s0:], p["c"][s0:, j].astype(float),
                       bt.ma(p, cfg["ex"]["trail_kind"], cfg["ex"]["trail_n"])[s0:, j].astype(float) if cfg["ex"].get("trail_n") else None)
                   for j, s in enumerate(p["symbols"])})
    return res, None


# ═════════════════════════ results ═════════════════════════
def _r(v):
    return "—" if v is None or not np.isfinite(v) else f"{v:+.2f}R"


def _stock_chart(res, sym) -> str:
    dates, c, trail = res["px"][sym]
    tr = res["trades"]
    tr = tr[tr["symbol"] == sym] if len(tr) else tr
    s0 = res["start_i"]
    series = [(c, "#F2F2F5", 1.4, "Close")]
    ex = res["cfg"]["ex"]
    if trail is not None:
        series.append((trail, ui.AMBER, 1.0, "Exit average"))
    marks = [(int(t.entry_i) - s0, t.entry, ui.GREEN, "up") for t in tr.itertuples()] + \
            [(int(t.exit_i) - s0, t.exit, ui.RED, "down") for t in tr.itertuples() if t.reason != "Open at end"]
    ch = ui.line_chart(dates, series, height=300 if not ui.phone() else 220, log=True, markers=marks)
    s = bt.trade_stats(tr["r"]) if len(tr) else {"n": 0}
    sub = f'{s["n"]} trades · {s["win"]:.0f}% won · {s["exp"]:+.2f}R per trade · total {s["total_r"]:+.1f}R' if s["n"] else "no trades"
    leg = ui.legend([("Close (adjusted for splits and dividends)", "#F2F2F5", False)]
                    + ([(f'{ex["trail_n"]}-day {ex["trail_kind"]}', ui.AMBER, False)] if trail is not None else [])
                    + [("Buy ▲", ui.GREEN, True), ("Sell ▼", ui.RED, True)])
    return f'<div class="q-card"><div class="q-ch">{ui.e(sym)} with your trades<em>{sub}</em></div>{ch}{leg}</div>'


def _results(res: dict, on_open=None):
    cfg, ph = res["cfg"], ui.phone()
    tr, taken = res["trades"], res["taken"]
    names = ", ".join(res["symbols"][:6]) + (f" and {len(res['symbols']) - 6} more" if len(res["symbols"]) > 6 else "")
    if res["missing"]:
        st.warning("No price history for: " + ", ".join(res["missing"]) + ". Left out; check the NSE symbols.")
    vol_rules = [r for r in cfg["rules"] if r["type"] in ("vol_mult", "vol_dry", "turnover_min", "x_vol_mult_n", "x_vol_gt_prev")]
    if res["no_vol"] and vol_rules:
        st.warning(f'{", ".join(res["no_vol"])} has no traded volume (an index), so your volume condition can never be true for it and it gives no trades. '
                   "Remove the volume line to test an index.")
    if tr is None or tr.empty:
        st.markdown('<div class="q-verdict" style="--c:{c}"><b>No trades</b><p>Your buy conditions were never all true on the same day for {n} between {a} and {b}, '
                    'or every signal was skipped at the entry. Loosen or remove one condition and run again.</p></div>'
                    .format(c=ui.YELLOW, n=ui.e(names), a=res["first"], b=res["last"]), unsafe_allow_html=True)
        return
    tone, head, body = bt.verdict(res)
    col = {"good": ui.GREEN, "warn": ui.YELLOW, "bad": ui.RED}[tone]
    st.markdown(f'<div class="q-verdict" style="--c:{col}"><b>{ui.e(head)}</b><p>{ui.e(body)}</p>'
                f'<p style="color:{ui.TX3};font-size:12.5px">{ui.e(names)} · {res["first"]} to {res["last"]} · {len(cfg["rules"])} buy conditions · '
                f'costs {cfg["cost"]:.2f}% per round trip · buys the day after the signal</p></div>', unsafe_allow_html=True)

    s = bt.trade_stats(tr["r"], tr["days"])
    cs = bt.curve_stats(res["curve"])
    pf = "∞" if not np.isfinite(s["pf"]) else f'{s["pf"]:.2f}'
    kp = [ui.kpi("Trades", f'{s["n"]}', f'{s["win"]:.0f}% won'),
          ui.kpi("Average result per trade", _r(s["exp"]), f'{tr["ret_pct"].mean():+.2f}% of the buy price', ui.signed_color(s["exp"])),
          ui.kpi("Average win / loss", f'<span style="color:{ui.GREEN}">{s["avg_win"]:+.2f}R</span> / <span style="color:{ui.RED}">{s["avg_loss"]:.2f}R</span>', "after costs"),
          ui.kpi("Profit factor", pf, "money won ÷ money lost"),
          ui.kpi("Average holding", f'{s["avg_days"]:.0f} sessions', f'longest losing run {s["max_losing_streak"]} trades'),
          ui.kpi("Best / worst trade", f'<span style="color:{ui.GREEN}">{s["best"]:+.1f}R</span> / <span style="color:{ui.RED}">{s["worst"]:.1f}R</span>', "R = one unit of risk")]
    if cs:
        kp += [ui.kpi("Account growth a year", f'{cs["cagr"]:+.1f}%', f'₹{cfg["capital"]:,.0f} → ₹{float(res["curve"].iloc[-1]):,.0f}', ui.signed_color(cs["cagr"])),
               ui.kpi("Deepest fall", f'{cs["maxdd"]:.1f}%', f'{cs["dd_peak"]:%b %Y} to {cs["dd_trough"]:%b %Y}', ui.RED)]
        if res.get("bench_stats"):
            kp.append(ui.kpi("NIFTY 50, buy and hold", f'{res["bench_stats"]["cagr"]:+.1f}% a year', f'deepest fall {res["bench_stats"]["maxdd"]:.1f}%'))
    st.markdown('<div class="q-kpis">' + "".join(kp) + "</div>", unsafe_allow_html=True)
    st.markdown('<div class="q-note">R is your risk on a trade: the distance from the buy price to the stop. +2R means the trade made twice what it risked; '
                '−1R is a full stop-out.</div>', unsafe_allow_html=True)

    curve = res["curve"]
    if cs and len(curve) > 2:
        base = curve / curve.iloc[0] * 100
        series = [(base.to_numpy(), ui.AMBER, 1.8, "Your setup")]
        if res.get("bench_curve") is not None:
            series.append((res["bench_curve"].to_numpy(), "#9A9AA0", 1.2, "NIFTY 50"))
        ch = ui.line_chart(base.index, series, height=260 if not ph else 200, log=True, area_first=True)
        dd = cs["dd_series"] * 100
        ddc = ui.line_chart(dd.index, [(dd.to_numpy(), ui.RED, 1.2, "Fall from peak")], height=110, y_suffix="%", area_first=True)
        idle = (" With one or a few instruments the money sits in cash between trades, so judge the setup by the result per trade more than by account growth."
                if len(res["symbols"]) <= 5 else "")
        st.markdown(f'<div class="q-card"><div class="q-ch">Account value<em>start 100 · risk {cfg["risk"]:g}% per trade · log scale</em></div>{ch}'
                    f'{ui.legend([("Your setup", ui.AMBER, False), ("NIFTY 50 buy and hold", "#9A9AA0", False)])}'
                    f'<div class="q-ch" style="margin-top:14px">Fall from the previous peak<em>%</em></div>{ddc}'
                    f'<div class="q-note">{len(tr)} trades simulated, {len(taken)} taken by the account.{idle}</div></div>', unsafe_allow_html=True)

    syms = res["symbols"]
    sym = syms[0] if len(syms) == 1 else ui.choice("Instrument", syms, "bx_sym", pills=True)
    st.markdown(_stock_chart(res, sym), unsafe_allow_html=True)
    if on_open is not None and sym not in INDICES and not sym.startswith("^"):
        if st.button(f"Open {sym} in the terminal", key="bx_open"):
            on_open(sym)

    two = []
    t2 = tr.copy()
    t2["year"] = pd.to_datetime(t2["entry_date"]).dt.year
    by = bt.breakdown(t2, "year")
    if len(by):
        rows = "".join(f'<tr><td>{int(y)}</td><td>{int(r_.trades)}</td><td>{r_.win:.0f}%</td><td style="color:{ui.signed_color(r_.exp) or ui.TX}">{r_.exp:+.2f}R</td>'
                       f'<td style="color:{ui.signed_color(r_.total) or ui.TX}">{r_.total:+.1f}R</td></tr>' for y, r_ in by.iterrows())
        two.append('<div class="q-card"><div class="q-ch">Year by year<em>does it work every year, or only in one?</em></div><div class="q-tw"><table class="q-t">'
                   f'<thead><tr><th>Year</th><th>Trades</th><th>Won</th><th>Per trade</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table></div></div>')
    br = bt.breakdown(tr, "reason")
    if len(br):
        br = br.sort_values("trades", ascending=False)
        rows = "".join(f'<tr><td>{ui.e(k)}</td><td>{int(r_.trades)}</td><td>{r_.win:.0f}%</td><td style="color:{ui.signed_color(r_.exp) or ui.TX}">{r_.exp:+.2f}R</td></tr>'
                       for k, r_ in br.iterrows())
        two.append('<div class="q-card"><div class="q-ch">How trades ended<em>which exit makes or loses the money</em></div><div class="q-tw"><table class="q-t">'
                   f'<thead><tr><th>Exit</th><th>Trades</th><th>Won</th><th>Per trade</th></tr></thead><tbody>{rows}</tbody></table></div></div>')
    if two:
        st.markdown(f'<div class="q-two">{"".join(two)}</div>', unsafe_allow_html=True)
    if len(syms) > 1:
        ps = bt.per_stock(tr)
        rows = "".join(f'<tr><td>{ui.e(k)}</td><td>{int(r_.trades)}</td><td>{r_.win:.0f}%</td><td style="color:{ui.signed_color(r_.exp) or ui.TX}">{r_.exp:+.2f}R</td>'
                       f'<td style="color:{ui.signed_color(r_.total) or ui.TX}">{r_.total:+.1f}R</td></tr>' for k, r_ in ps.iterrows())
        st.markdown('<div class="q-card"><div class="q-ch">By instrument</div><div class="q-tw"><table class="q-t"><thead><tr><th>Instrument</th><th>Trades</th><th>Won</th>'
                    f'<th>Per trade</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table></div></div>', unsafe_allow_html=True)

    with st.expander("Can I trust it? First half against second half, and how bad a losing run can get"):
        hv = bt.halves(tr)
        mc = bt.monte_carlo(tr["r"].to_numpy(), cfg["risk"])
        parts = []
        if hv:
            parts.append(f'<div class="q-card"><div class="q-ch">First half against second half</div><div class="q-sub">First half: <b style="color:#fff">{_r(hv["a_exp"])}</b> per trade '
                         f'over {hv["a_n"]} trades. Second half: <b style="color:#fff">{_r(hv["b_exp"])}</b> over {hv["b_n"]} trades. '
                         "An edge that shows up in only one half is fragile.</div></div>")
        if mc:
            parts.append(f'<div class="q-card"><div class="q-ch">If the same trades came in a different order<em>{mc["sims"]:,} shuffles</em></div><div class="q-sub">'
                         f'Typical deepest fall <b style="color:#fff">{mc["dd_med"]:.1f}%</b>; one time in ten it is worse than <b style="color:#fff">{mc["dd_p90"]:.1f}%</b>, '
                         f'one in twenty worse than <b style="color:#fff">{mc["dd_p95"]:.1f}%</b>. Typical longest losing run {mc["streak_med"]:.0f} trades '
                         f'(one in ten: {mc["streak_p90"]:.0f}). Chance of ending below the starting capital: <b style="color:#fff">{mc["p_loss"]:.0f}%</b>.</div></div>')
        st.markdown("".join(parts) or '<div class="q-note">Needs at least 20 trades.</div>', unsafe_allow_html=True)

    show = tr.sort_values("entry_i", ascending=False)
    rows = "".join(
        f'<tr><td>{ui.e(t.symbol)}</td><td>{pd.Timestamp(t.entry_date):%d %b %Y}</td><td>{t.entry:,.2f}</td><td>{t.stop:,.2f}</td>'
        f'<td>{pd.Timestamp(t.exit_date):%d %b %Y}</td><td>{t.exit:,.2f}</td><td style="text-align:left">{ui.e(t.reason)}</td><td>{int(t.days)}</td>'
        f'<td style="color:{ui.signed_color(t.ret_pct) or ui.TX}">{t.ret_pct:+.2f}%</td><td style="color:{ui.signed_color(t.r) or ui.TX}">{t.r:+.2f}R</td></tr>'
        for t in show.head(300).itertuples())
    st.markdown(f'<div class="q-card"><div class="q-ch">Every trade<em>newest first{" · first 300 shown" if len(show) > 300 else ""}</em></div><div class="q-tw">'
                '<table class="q-t bx-tr"><thead><tr><th>Instrument</th><th>Bought</th><th>Buy price</th><th>Stop</th><th>Sold</th><th>Sell price</th>'
                f'<th style="text-align:left">Why it ended</th><th>Sessions</th><th>Result</th><th>In R</th></tr></thead><tbody>{rows}</tbody></table></div></div>',
                unsafe_allow_html=True)
    csv = show[["symbol", "entry_date", "entry", "stop", "exit_date", "exit", "reason", "days", "ret_pct", "r"]].rename(
        columns={"symbol": "Instrument", "entry_date": "Bought", "entry": "Buy price", "stop": "Stop", "exit_date": "Sold", "exit": "Sell price",
                 "reason": "Why it ended", "days": "Sessions", "ret_pct": "Result %", "r": "Result R"}).to_csv(index=False).encode("utf-8")
    st.download_button("Download all trades (CSV)", csv, file_name="arka_backtest_trades.csv", mime="text/csv", key="bx_csv")
    st.markdown('<div class="q-note">Past results do not promise future results. Prices are Yahoo Finance daily candles adjusted for splits and dividends. '
                'A signal is read at the close and bought the next session, so the test never uses information it could not have had. '
                'Index results are for the index level itself, which cannot be bought directly. Information only, not investment advice.</div>',
                unsafe_allow_html=True)


def render_backtest(on_open=None):
    ui.css()
    _css()
    cfg, reading, run = _controls()
    key = json.dumps(cfg, sort_keys=True, default=str)
    if run:
        res, err = _run(cfg, reading)
        if err:
            st.markdown(f'<div class="q-verdict" style="--c:{ui.RED}"><b>Could not run</b><p>{ui.e(err)}</p></div>', unsafe_allow_html=True)
            return
        st.session_state["bx_result"], st.session_state["bx_result_key"] = res, key
    res = st.session_state.get("bx_result")
    if res is None:
        return
    if st.session_state.get("bx_result_key") != key:
        st.markdown(f'<div class="q-note" style="color:{ui.YELLOW}">You changed something since this result. Press Run backtest to update it.</div>', unsafe_allow_html=True)
    _results(res, on_open)
