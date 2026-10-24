"""
portfolio_page.py — Arka Trades Portfolio

* Add holdings as BUY LOTS: stock · quantity · buy price · buy date · note.
  Buying the same stock again at a different price is just another lot;
  the holding shows the weighted-average cost.
* Live P&L: invested, current value, total P&L, day P&L, weight — one batched
  price download, cached 20 s.
* NEWS INDICATOR: a small yellow light after the stock name when that company
  has relevant headlines in the selected window (default 24 h). Hover for the
  latest headline; the HOLDING NEWS tab lists them all.
* Everything is saved to Supabase (table `portfolio`, SQL in user_data.py).
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd
import streamlit as st

import user_data as ud
from data_engine import get_quotes

try:
    from news_feed import _fetch_news_for_stock
except Exception:  # pragma: no cover
    _fetch_news_for_stock = None

try:
    from screener_scraper import resolve_symbol
except Exception:  # pragma: no cover
    resolve_symbol = None

AMBER, GREEN, RED, CYAN, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#FFD60A"
DARK, DARK2, BORDER, IVORY, T2, T3 = "#000000", "#0A0A0A", "#262626", "#E8E8E8", "#8A8A8A", "#5A5A5A"
MONO = "'JetBrains Mono',monospace"

_CSS = f"""
<style>
div[class*="st-key-pfrows"]{{gap:.15rem !important;}}
div[data-testid="stHorizontalBlock"]:has(.pf-cell){{align-items:center;border-bottom:1px solid {BORDER};padding:2px 0;}}
div[data-testid="stHorizontalBlock"]:has(.pf-cell):hover{{background:{DARK2};}}
.pf-cell{{font-family:{MONO};font-size:11px;color:{IVORY};min-height:38px;display:flex;align-items:center;}}
.pf-grid{{display:grid;grid-template-columns:1.9fr .7fr .95fr .95fr .75fr 1.05fr 1.05fr 1.05fr .85fr .7fr;gap:8px;width:100%;align-items:center;}}
.pf-grid span{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.pf-head{{font-family:{MONO};font-size:9px;letter-spacing:.7px;color:{T2};font-weight:700;min-height:26px;display:flex;align-items:center;}}
.pf-sym{{font-weight:800;font-size:12px;color:{AMBER};}}
.pf-name{{font-size:9px;color:{T3};display:block;overflow:hidden;text-overflow:ellipsis;}}
.pf-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:{YELLOW};margin-left:7px;vertical-align:middle;
         box-shadow:0 0 6px {YELLOW},0 0 12px {YELLOW}88;animation:pfpulse 1.8s ease-in-out infinite;cursor:help;}}
@keyframes pfpulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
.pf-kpi{{border:1px solid {BORDER};background:{DARK2};padding:11px 13px;min-height:72px;}}
.pf-kpi .l{{font:9px {MONO};color:{T3};letter-spacing:.7px;text-transform:uppercase;}}
.pf-kpi .v{{font:700 18px {MONO};color:{IVORY};margin-top:5px;}}
.pf-kpi .s{{font:10px {MONO};margin-top:3px;}}
.pf-note{{font:10px {MONO};color:{T2};border-left:2px solid #444;padding:6px 10px;background:rgba(255,255,255,.02);margin:8px 0;line-height:1.6;}}
.pf-title{{font:700 10px {MONO};letter-spacing:1.2px;color:{AMBER};border-bottom:1px solid {BORDER};padding:8px 0 6px;margin:12px 0 8px;text-transform:uppercase;}}
</style>
"""


# ───────────────────────── formatting ─────────────────────────
def _inr(x, d=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    neg = x < 0
    s = f"{abs(x):.{d}f}"
    ip, _, fp = s.partition(".")
    if len(ip) > 3:
        head, tail = ip[:-3], ip[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        ip = head + "," + tail
    return ("-" if neg else "") + "₹" + ip + (("." + fp) if d else "")


def _col(v):
    return GREEN if (v or 0) >= 0 else RED


def _kpi(label, value, sub="", color=IVORY):
    return (f'<div class="pf-kpi"><div class="l">{label}</div><div class="v" style="color:{color}">{value}</div>'
            f'<div class="s" style="color:{color if sub else T3}">{sub}</div></div>')


# ───────────────────────── calculations ─────────────────────────
def aggregate(lots, quotes):
    by = defaultdict(list)
    for l in lots:
        by[l["symbol"]].append(l)
    out = []
    for sym, ls in by.items():
        qty = sum(l["quantity"] for l in ls)
        invested = sum(l["quantity"] * l["buy_price"] for l in ls)
        q = quotes.get(sym)
        ltp = q["price"] if q else None
        prev = q["prev_close"] if q else None
        value = qty * ltp if ltp is not None else None
        out.append({
            "symbol": sym, "name": next((l["name"] for l in ls if l["name"]), ""),
            "qty": qty, "avg": invested / qty if qty else 0, "invested": invested,
            "ltp": ltp, "value": value, "chg": q["chg"] if q else None,
            "pnl": (value - invested) if value is not None else None,
            "pnl_pct": ((value / invested - 1) * 100) if (value is not None and invested) else None,
            "day_pnl": qty * (ltp - prev) if (ltp is not None and prev) else None,
            "lots": ls,
        })
    total_val = sum(h["value"] for h in out if h["value"] is not None)
    for h in out:
        h["weight"] = (h["value"] / total_val * 100) if (h["value"] is not None and total_val) else None
    return sorted(out, key=lambda h: (h["value"] is None, -(h["value"] or 0)))


def holding_news(symbols, names, days):
    """{symbol: [articles]} — parallel, each call is cached 5 min inside news_feed."""
    if _fetch_news_for_stock is None or not symbols:
        return {}

    def one(sym):
        try:
            return sym, (_fetch_news_for_stock(sym, days, company=names.get(sym) or None) or [])
        except Exception:
            return sym, []

    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(one, symbols))


def _lookup_name(sym):
    if not resolve_symbol:
        return ""
    try:
        r = resolve_symbol(sym)
        return (r or {}).get("name", "") or ""
    except Exception:
        return ""


# ───────────────────────── sections ─────────────────────────
def _missing_table_help():
    st.error("The `portfolio` table does not exist in Supabase yet. Run this once in Supabase → SQL Editor, then try again:")
    st.code(ud.SQL_PORTFOLIO, language="sql")


def _add_form(supabase, key="main", preset=""):
    with st.form(f"pf_add_{key}", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1.2, 1.4, 1.4])
        sym = c1.text_input("Stock (NSE symbol)", value=preset, placeholder="e.g. RELIANCE")
        qty = c2.number_input("Quantity", min_value=0.0, value=0.0, step=1.0, format="%g")
        price = c3.number_input("Buy price (₹)", min_value=0.0, value=0.0, step=0.05, format="%.2f")
        when = c4.date_input("Buy date", value=date.today(), max_value=date.today())
        note = st.text_input("Note (optional)", placeholder="e.g. breakout entry, SL 1180")
        go = st.form_submit_button("＋ ADD TO PORTFOLIO", type="primary", use_container_width=True)
    if not go:
        return
    s = ud.clean_symbol(sym)
    if not s:
        st.error("Enter the stock symbol.")
    elif qty <= 0 or price <= 0:
        st.error("Quantity and buy price must be greater than zero.")
    elif not get_quotes((s,)).get(s):
        st.error(f"No price data found for “{s}”. Use the NSE trading symbol (e.g. M&M, BAJAJ-AUTO).")
    else:
        ok, msg = ud.add_lot(supabase, s, qty, price, when, note, name=_lookup_name(s))
        if ok:
            st.toast(f"{s}: {qty:g} @ {_inr(price)} added")
            if msg:
                st.warning(msg)
            st.rerun()
        elif msg == "MISSING_TABLE":
            _missing_table_help()
        else:
            st.error(msg)


def _holdings_table(holds, news, on_open):
    heads = ["STOCK", "QTY", "AVG BUY", "LTP", "DAY %", "INVESTED", "VALUE", "P&L", "P&L %", "WEIGHT"]
    h = st.columns([9.6, 0.8, 0.85])
    h[0].markdown('<div class="pf-head pf-grid">' + "".join(f"<span>{x}</span>" for x in heads) + "</div>", unsafe_allow_html=True)

    with st.container(key="pfrows_main"):
        for x in holds:
            sym = x["symbol"]
            arts = news.get(sym) or []
            dot = ""
            if arts:
                tip = html.escape(f'{len(arts)} headline{"s" if len(arts) > 1 else ""} · latest: {arts[0].get("title", "")}', quote=True)
                dot = f'<span class="pf-dot" title="{tip}"></span>'
            name = f'<span class="pf-name">{html.escape(x["name"])}</span>' if x["name"] else ""
            cc, pc = _col(x["chg"]), _col(x["pnl"])
            row = (f'<div class="pf-cell pf-grid">'
                   f'<span><span class="pf-sym">{sym}</span>{dot}{name}</span>'
                   f'<span>{x["qty"]:g}</span><span>{_inr(x["avg"])}</span><span>{_inr(x["ltp"])}</span>'
                   f'<span style="color:{cc}">{"—" if x["chg"] is None else f"{x['chg']:+.2f}%"}</span>'
                   f'<span>{_inr(x["invested"], 0)}</span><span>{_inr(x["value"], 0)}</span>'
                   f'<span style="color:{pc}">{_inr(x["pnl"], 0)}</span>'
                   f'<span style="color:{pc}">{"—" if x["pnl_pct"] is None else f"{x['pnl_pct']:+.2f}%"}</span>'
                   f'<span>{"—" if x["weight"] is None else f"{x['weight']:.1f}%"}</span></div>')
            cols = st.columns([9.6, 0.8, 0.85])
            cols[0].markdown(row, unsafe_allow_html=True)
            if cols[1].button("OPEN", key=f"pf_open_{sym}", use_container_width=True):
                on_open(sym)
            if cols[2].button("DETAILS", key=f"pf_det_{sym}", use_container_width=True, help="Lots, add more shares, remove"):
                st.session_state["pf_sel"] = sym
                st.rerun()


def _charts(holds):
    import plotly.graph_objects as go
    valid = [h for h in holds if h["value"] is not None]
    if not valid:
        return
    a, b = st.columns([1, 1.6])
    with a:
        st.markdown('<div class="pf-title">ALLOCATION</div>', unsafe_allow_html=True)
        fig = go.Figure(go.Pie(labels=[h["symbol"] for h in valid], values=[h["value"] for h in valid], hole=.62,
                               textinfo="label+percent", textfont=dict(size=9), sort=True,
                               marker=dict(line=dict(color=DARK, width=2))))
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor=DARK, showlegend=False,
                          font=dict(color=T2, family="JetBrains Mono"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="pf_donut")
    with b:
        st.markdown('<div class="pf-title">P&L BY STOCK (₹)</div>', unsafe_allow_html=True)
        pv = sorted([h for h in valid if h["pnl"] is not None], key=lambda h: h["pnl"])
        fig = go.Figure(go.Bar(x=[h["symbol"] for h in pv], y=[h["pnl"] for h in pv],
                               marker_color=[GREEN if h["pnl"] >= 0 else RED for h in pv]))
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor=DARK, plot_bgcolor=DARK,
                          font=dict(color=T2, family="JetBrains Mono", size=9))
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, gridcolor="#181818")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="pf_pnl_bar")


def _detail_panel(supabase, holds, news):
    sym = st.session_state.get("pf_sel")
    h = next((x for x in holds if x["symbol"] == sym), None)
    if not h:
        st.session_state.pop("pf_sel", None)
        return
    st.markdown(f'<div class="pf-title">{sym} · LOTS & MANAGEMENT</div>', unsafe_allow_html=True)
    top = st.columns([6, 1])
    top[0].markdown(f'<div class="pf-note">{html.escape(h["name"] or sym)} · {h["qty"]:g} shares · average cost {_inr(h["avg"])} · '
                    f'invested {_inr(h["invested"], 0)} · current {_inr(h["value"], 0)}</div>', unsafe_allow_html=True)
    if top[1].button("CLOSE", key="pf_det_close", use_container_width=True):
        st.session_state.pop("pf_sel", None)
        st.rerun()

    hd = st.columns([1.2, 1, 1.2, 1.3, 1.3, 2, 0.6])
    for c, t in zip(hd, ["DATE", "QTY", "BUY PRICE", "INVESTED", "LOT P&L", "NOTE", ""]):
        c.markdown(f'<div class="pf-head">{t}</div>', unsafe_allow_html=True)
    for l in h["lots"]:
        lp = (h["ltp"] - l["buy_price"]) * l["quantity"] if h["ltp"] is not None else None
        r = st.columns([1.2, 1, 1.2, 1.3, 1.3, 2, 0.6])
        r[0].markdown(f'<div class="pf-cell">{l["buy_date"] or "—"}</div>', unsafe_allow_html=True)
        r[1].markdown(f'<div class="pf-cell">{l["quantity"]:g}</div>', unsafe_allow_html=True)
        r[2].markdown(f'<div class="pf-cell">{_inr(l["buy_price"])}</div>', unsafe_allow_html=True)
        r[3].markdown(f'<div class="pf-cell">{_inr(l["quantity"] * l["buy_price"], 0)}</div>', unsafe_allow_html=True)
        r[4].markdown(f'<div class="pf-cell" style="color:{_col(lp)}">{_inr(lp, 0)}</div>', unsafe_allow_html=True)
        r[5].markdown(f'<div class="pf-cell" style="color:{T2}">{html.escape(l["note"])}</div>', unsafe_allow_html=True)
        if r[6].button("✕", key=f"pf_dl_{l['id']}", help="Delete this lot", use_container_width=True):
            err = ud.delete_lot(supabase, l["id"])
            st.error(err) if err else st.rerun()

    st.markdown('<div class="pf-title">ADD MORE SHARES OF THIS STOCK</div>', unsafe_allow_html=True)
    _add_form(supabase, key=f"more_{sym}", preset=sym)
    with st.expander("Danger zone"):
        sure = st.checkbox(f"Yes, remove the entire {sym} holding ({len(h['lots'])} lots)", key=f"pf_sure_{sym}")
        if st.button(f"REMOVE {sym} FROM PORTFOLIO", disabled=not sure, key=f"pf_rm_all_{sym}"):
            err = ud.delete_holding(supabase, sym)
            if err:
                st.error(err)
            else:
                st.session_state.pop("pf_sel", None)
                st.rerun()


def _news_tab(holds, news, days_label):
    with_news = [h for h in holds if news.get(h["symbol"])]
    if not with_news:
        st.markdown(f'<div class="pf-note">No relevant headlines for your holdings in the last {days_label}. '
                    f'A yellow light appears next to a stock the moment news arrives.</div>', unsafe_allow_html=True)
        return
    for h in with_news:
        arts = news[h["symbol"]]
        st.markdown(f'<div class="pf-title"><span class="pf-dot" style="margin:0 8px 0 0"></span>{h["symbol"]} · {len(arts)} headline{"s" if len(arts) > 1 else ""}</div>', unsafe_allow_html=True)
        for a in arts[:6]:
            sent = a.get("sentiment", "neutral")
            c = GREEN if "positive" in sent else RED if "negative" in sent else T3
            link = html.escape(a.get("link", "#"), quote=True)
            st.markdown(
                f'<a href="{link}" target="_blank" style="text-decoration:none"><div style="border-left:2px solid {c};padding:6px 0 6px 10px;margin-bottom:5px;">'
                f'<div style="font:600 12px Inter,sans-serif;color:{IVORY}">{html.escape(a.get("title", ""))}</div>'
                f'<div style="font:9px {MONO};color:{T2};margin-top:3px">{html.escape(str(a.get("source", "")))} · {a.get("time_str", "")} · '
                f'{html.escape(str(a.get("event", "NEWS")))}</div></div></a>', unsafe_allow_html=True)


# ───────────────────────── page ─────────────────────────
def render_portfolio(supabase, on_open):
    st.markdown(_CSS, unsafe_allow_html=True)
    lots, warn = ud.load_portfolio(supabase)
    if warn:
        if ud.table_missing(warn):
            _missing_table_help()
        else:
            st.warning(warn)

    days_label = st.session_state.get("pf_news_window", "24 hours")
    days = {"24 hours": 1, "3 days": 3, "7 days": 7}[days_label]

    tab_h, tab_add, tab_news = st.tabs(["HOLDINGS", "ADD HOLDING", "HOLDING NEWS"])

    holds, news = [], {}
    if lots:
        symbols = sorted({l["symbol"] for l in lots})
        quotes = get_quotes(tuple(symbols))
        holds = aggregate(lots, quotes)
        names = {h["symbol"]: h["name"] for h in holds}
        news = holding_news(symbols, names, days)

    with tab_h:
        if not holds:
            st.markdown('<div class="pf-note">Your portfolio is empty. Open the <b>ADD HOLDING</b> tab, enter the stock, quantity and buy price — '
                        'it is saved permanently.</div>', unsafe_allow_html=True)
        else:
            valid = [h for h in holds if h["value"] is not None]
            inv = sum(h["invested"] for h in holds)
            inv_v = sum(h["invested"] for h in valid)
            val = sum(h["value"] for h in valid)
            pnl = val - inv_v
            day = sum(h["day_pnl"] for h in valid if h["day_pnl"] is not None)
            k = st.columns(5)
            k[0].markdown(_kpi("Invested", _inr(inv, 0), f"{len(holds)} holdings · {len(lots)} lots"), unsafe_allow_html=True)
            k[1].markdown(_kpi("Current value", _inr(val, 0)), unsafe_allow_html=True)
            k[2].markdown(_kpi("Total P&L", _inr(pnl, 0), f"{(val / inv_v - 1) * 100:+.2f}%" if inv_v else "", _col(pnl)), unsafe_allow_html=True)
            k[3].markdown(_kpi("Day P&L", _inr(day, 0), f"{day / (val - day) * 100:+.2f}%" if (val - day) else "", _col(day)), unsafe_allow_html=True)
            nn = sum(1 for h in holds if news.get(h["symbol"]))
            k[4].markdown(_kpi("Stocks with news", str(nn), f"last {days_label}", YELLOW if nn else T3), unsafe_allow_html=True)
            if len(valid) < len(holds):
                st.caption("Some holdings have no live price right now and are excluded from the totals.")
            _charts(holds)
            st.markdown('<div class="pf-title">HOLDINGS</div>', unsafe_allow_html=True)
            _holdings_table(holds, news, on_open)
            st.caption(f"Yellow light = relevant headlines in the last {days_label}. Prices are Yahoo Finance (NSE), delayed as per source. "
                       f"P&L excludes brokerage, taxes and charges.")
            _detail_panel(supabase, holds, news)

    with tab_add:
        st.markdown('<div class="pf-note">Add each purchase as a lot. Bought the same stock twice at different prices? Add two lots — '
                    'Arka shows the weighted-average cost.</div>', unsafe_allow_html=True)
        _add_form(supabase, key="main")

    with tab_news:
        st.selectbox("News window", ["24 hours", "3 days", "7 days"], key="pf_news_window",
                     index=["24 hours", "3 days", "7 days"].index(days_label))
        _news_tab(holds, news, days_label)
