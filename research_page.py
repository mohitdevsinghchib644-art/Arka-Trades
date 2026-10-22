"""Arka Trades — Research Terminal v11 (full data, fast).

What changed vs v10
-------------------
* Works WITHOUT ScanX. Primary sources are Screener.in (fundamentals) and
  Yahoo Finance (prices, analysts, dividends, news). ScanX is now an OPTIONAL
  extra that loads only when you open ANNOUNCEMENTS / MF HOLDINGS.
* One reusable panel — render_research_panel() — is used both on the Research
  page and directly under the chart in the Security workspace.
* The panel is an st.fragment: switching sections reruns ONLY the panel and
  every dataset is cached, so section switches are near-instant.
* Only the selected section is computed (st.radio, not st.tabs).
* Nothing is invented: a missing dataset says DATA UNAVAILABLE.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from data_engine import (get_screener_bundle, get_peers_table, get_history, get_quotes,
                         get_scanx, yf_attr, rsi_wilder, to_float)

try:
    from news_feed import get_security_news
except Exception:  # pragma: no cover
    get_security_news = None

try:
    import screener_scraper as _ss
    _SECTOR_PEERS = getattr(_ss, "_SECTOR_PEERS", {})
except Exception:  # pragma: no cover
    _SECTOR_PEERS = {}

IST = timezone(timedelta(hours=5, minutes=30))

T0 = {
    "bg": "#000000", "panel": "#080808", "panel2": "#0E0E0E", "row_alt": "#0B0B0B",
    "border": "#242424", "amber": "#FF9F0A", "ivory": "#E8E8E8", "t2": "#9A9A9A",
    "t3": "#5E5E5E", "green": "#30D158", "red": "#FF453A", "cyan": "#5AC8FA",
    "purple": "#BF5AF2", "mono": "'JetBrains Mono','Consolas',monospace",
}

FUNCS = ["OVERVIEW", "FINANCIALS", "BALANCE SHEET", "CASH FLOW", "RATIOS", "SHAREHOLDING",
         "PEERS", "TECHNICALS", "ANALYST", "DIVIDENDS", "NEWS", "ANNOUNCEMENTS",
         "MF HOLDINGS", "THESIS", "SOURCES"]


def _theme(T):
    x = dict(T0)
    if T:
        x.update(T)
    return x


# ═══════════════════════════ UI primitives ═══════════════════════════
def _esc(x: Any) -> str:
    return html.escape(str(x))


def _tag(status, T, text=None):
    if status == "live":
        label, color = text or "● LIVE", T["green"]
    elif status == "stale":
        label, color = text or "◐ CACHED", T["amber"]
    else:
        label, color = text or "✕ N/A", T["t3"]
    return f'<span style="color:{color};font:700 9px {T["mono"]};letter-spacing:.8px">{_esc(label)}</span>'


def _header(title, T, status=None, note=None):
    right = _tag(status, T) if status else ""
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {T["border"]};padding:9px 0 6px;margin:13px 0 8px">'
        f'<span style="font:700 11px {T["mono"]};letter-spacing:1px;color:{T["amber"]}">{_esc(title).upper()}</span>{right}</div>',
        unsafe_allow_html=True)
    if note:
        st.markdown(f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin:3px 0 8px">{_esc(note)}</div>', unsafe_allow_html=True)


def _panel(title, body, T, dashed=False):
    border = f"1px dashed {T['border']}" if dashed else f"1px solid {T['border']}"
    st.markdown(
        f'<div style="border:{border};background:{T["panel"]};padding:11px 13px;margin:7px 0">'
        f'<div style="font:700 10px {T["mono"]};color:{T["ivory"]};margin-bottom:7px">{_esc(title).upper()}</div>{body}</div>',
        unsafe_allow_html=True)


def _unavailable(title, requirement, T, detail=None):
    detail = detail or "No verified dataset returned this field."
    _panel(title,
           f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.75">'
           f'<b style="color:{T["t3"]}">STATUS:</b> DATA UNAVAILABLE<br>'
           f'<b style="color:{T["t3"]}">REQUIRED:</b> {_esc(requirement)}<br>{_esc(detail)}</div>', T, dashed=True)


def _kpis(items, T, cols=4):
    c = st.columns(cols)
    for i, (label, value, sub) in enumerate(items):
        with c[i % cols]:
            st.markdown(
                f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:10px 11px;min-height:67px;margin-bottom:7px">'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">{_esc(label.upper())}</div>'
                f'<div style="font:700 15px {T["mono"]};color:{T["ivory"]};margin-top:4px">{_esc(value)}</div>'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:3px">{_esc(sub or "")}</div></div>',
                unsafe_allow_html=True)


def _table(periods, rows, T, highlights=()):
    if not rows:
        _unavailable("No rows returned", "Selected dataset", T)
        return
    heads = "".join(f'<th style="text-align:right;padding:7px 8px;color:{T["t3"]};font:700 9px {T["mono"]};white-space:nowrap">{_esc(p)}</th>' for p in periods)
    out = []
    for i, r in enumerate(rows):
        label = str(r.get("label", ""))
        hl = any(x.lower() in label.lower() for x in highlights if x)
        cells = "".join(
            f'<td style="text-align:right;padding:7px 8px;color:{T["ivory"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(v)}</td>'
            for v in r.get("values", []))
        out.append(
            f'<tr style="background:{T["row_alt"] if i % 2 else "transparent"};border-bottom:1px solid {T["border"]}">'
            f'<td style="padding:7px 8px;color:{T["amber"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap;position:sticky;left:0;background:{T["row_alt"] if i % 2 else T["panel"]}">{_esc(label)}</td>{cells}</tr>')
    st.markdown(
        f'<div style="overflow-x:auto;border:1px solid {T["border"]};background:{T["panel"]};max-width:100%"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="background:{T["panel2"]};border-bottom:1px solid {T["border"]}"><th style="text-align:left;padding:7px 8px;color:{T["t3"]};font:700 9px {T["mono"]};position:sticky;left:0;background:{T["panel2"]}">METRIC</th>{heads}</tr></thead>'
        f'<tbody>{"".join(out)}</tbody></table></div>', unsafe_allow_html=True)


def _rec_table(rec, T, highlights=(), last_n=None):
    """Render a Screener-style record {'periods': [...], 'rows': [{'label','values'}]}."""
    if not rec or not rec.get("rows"):
        _unavailable("Table", "Screener.in company page", T)
        return
    periods, rows = rec["periods"], rec["rows"]
    if last_n and len(periods) > last_n:
        periods = periods[-last_n:]
        rows = [{"label": r["label"], "values": r["values"][-last_n:]} for r in rows]
    _table(periods, rows, T, highlights)


def _fmt_dt_cell(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, (pd.Timestamp, datetime)):
        return str(v)[:10]
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _df_table(df, T, max_rows=40, max_cols=8, highlights=()):
    """yfinance statement frame (index = metric, columns = dates)."""
    if df is None or df.empty:
        _unavailable("Table", "Yahoo Finance dataset", T)
        return
    x = df.iloc[:max_rows, :max_cols]
    periods = [str(c)[:10] for c in x.columns]
    rows = [{"label": str(i), "values": [_fmt_dt_cell(v) for v in r.tolist()]} for i, r in x.iterrows()]
    _table(periods, rows, T, highlights)


def _row(rec, *needles):
    if not rec:
        return None
    for n in needles:
        n = n.lower()
        for r in rec["rows"]:
            if r["label"].lower().startswith(n):
                return r
        for r in rec["rows"]:
            if n in r["label"].lower():
                return r
    return None


def _nums(row):
    return [to_float(v) for v in (row or {}).get("values", [])]


def _growth_rows(rec, labels, lags, lag_names):
    out = []
    for lab in labels:
        r = _row(rec, lab)
        if not r:
            continue
        nums = _nums(r)
        vals = [r["values"][-1] if r["values"] else "—"]
        for lag in lags:
            if len(nums) > lag and nums[-1] is not None and nums[-1 - lag] is not None:
                b = nums[-1 - lag]
                vals.append(f"{(nums[-1] / b - 1) * 100:+.1f}%" if b > 0 else "n/m")
            else:
                vals.append("—")
        out.append({"label": r["label"], "values": vals})
    return ["LATEST"] + lag_names, out


def _bar_chart(rec, labels, colors, T, key, n=12):
    import plotly.graph_objects as go
    if not rec:
        return
    fig = go.Figure()
    for lab, col in zip(labels, colors):
        r = _row(rec, lab)
        if not r:
            continue
        per = rec["periods"][-n:]
        vals = _nums(r)[-n:]
        fig.add_trace(go.Bar(x=per, y=vals, name=r["label"], marker_color=col))
    if not fig.data:
        return
    fig.update_layout(height=290, barmode="group", margin=dict(l=0, r=10, t=10, b=0),
                      paper_bgcolor=T["bg"], plot_bgcolor=T["bg"],
                      font=dict(color=T["t2"], family="JetBrains Mono, Consolas, monospace", size=9),
                      legend=dict(orientation="h", y=1.1, x=0))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#181818")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


# ═══════════════════════════ Sections ═══════════════════════════
def _fmt_money_cr(v):
    return f"₹{v} Cr" if v else "N/A"


def _debt_equity(B):
    bs = B.get("balance_sheet")
    b, r, e = _row(bs, "borrowings"), _row(bs, "reserves"), _row(bs, "equity capital", "share capital")
    try:
        bv = [x for x in _nums(b) if x is not None][-1]
        rv = [x for x in _nums(r) if x is not None][-1]
        ev = [x for x in _nums(e) if x is not None][-1]
        nw = rv + ev
        return round(bv / nw, 2) if nw else None
    except Exception:
        return None


def _overview(sym, B, T, key):
    s = B.get("summary") or {}
    info = yf_attr(sym, "info") or {}
    hist = get_history(sym, "1y")
    _header("Key fundamentals", T, "live" if B.get("resolved") else "unavailable", B.get("url"))
    de = _debt_equity(B)
    pb = info.get("priceToBook")
    _kpis([
        ("Market Cap", _fmt_money_cr(s.get("market_cap")), "Screener"),
        ("Stock P/E", f'{s["pe_ratio"]}x' if s.get("pe_ratio") else "N/A", "Screener"),
        ("ROCE", f'{s["roce"]}%' if s.get("roce") else "N/A", "Screener"),
        ("ROE", f'{s["roe"]}%' if s.get("roe") else "N/A", "Screener"),
        ("Book Value", f'₹{s["book_value"]}' if s.get("book_value") else "N/A", "Screener"),
        ("Dividend Yield", f'{s["dividend_yield"]}%' if s.get("dividend_yield") else "N/A", "Screener"),
        ("Price / Book", f"{pb:.2f}x" if isinstance(pb, (int, float)) else "N/A", "Yahoo"),
        ("Debt / Equity", f"{de}x" if de is not None else "N/A", "Computed from balance sheet"),
    ], T, 4)

    # 52-week position bar from the real price history
    if hist is not None and len(hist) > 20:
        h252 = hist.tail(252)
        lo, hi, cur = float(h252["Low"].min()), float(h252["High"].max()), float(hist["Close"].iloc[-1])
        pct = 0 if hi == lo else max(0, min(100, (cur - lo) / (hi - lo) * 100))
        _panel("52-week range",
               f'<div style="display:flex;justify-content:space-between;font:10px {T["mono"]};color:{T["t2"]};margin-bottom:6px">'
               f'<span>₹{lo:,.2f}</span><span style="color:{T["ivory"]}">₹{cur:,.2f} · {pct:.0f}% of range · {(cur / hi - 1) * 100:+.1f}% from high</span><span>₹{hi:,.2f}</span></div>'
               f'<div style="position:relative;height:6px;background:#1a1a1a"><div style="position:absolute;left:{pct:.1f}%;top:-3px;width:3px;height:12px;background:{T["amber"]}"></div></div>', T)

    growth = B.get("growth") or {}
    if growth:
        _header("Growth snapshot", T, "live")
        cols = st.columns(len(growth))
        for c, (title, rows) in zip(cols, growth.items()):
            with c:
                body = "".join(f'<div style="display:flex;justify-content:space-between;font:10px {T["mono"]};padding:3px 0;color:{T["t2"]}"><span>{_esc(k)}</span><b style="color:{T["ivory"]}">{_esc(v)}</b></div>' for k, v in rows.items())
                _panel(title, body, T)

    desc = info.get("longBusinessSummary") or B.get("about")
    if desc:
        _panel("Business", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(desc[:1400])}</div>', T)

    if B.get("pros") or B.get("cons"):
        a, b = st.columns(2)
        with a:
            _panel("Pros (Screener)", "".join(f'<div style="font:10px {T["mono"]};color:{T["t2"]};padding:3px 0;line-height:1.6"><span style="color:{T["green"]}">+</span> {_esc(x)}</div>' for x in B["pros"]) or "—", T)
        with b:
            _panel("Cons (Screener)", "".join(f'<div style="font:10px {T["mono"]};color:{T["t2"]};padding:3px 0;line-height:1.6"><span style="color:{T["red"]}">−</span> {_esc(x)}</div>' for x in B["cons"]) or "—", T)

    if not B.get("resolved"):
        _unavailable("Screener fundamentals", "Screener.in company page", T, B.get("reason"))


def _financials(sym, B, T, key):
    mode = st.radio("Period", ["Quarterly", "Yearly"], horizontal=True, key=f"{key}_fin_mode", label_visibility="collapsed")
    rec = B.get("quarterly") if mode == "Quarterly" else B.get("yearly")
    src = "Screener"
    if not rec:  # Yahoo fallback
        df = yf_attr(sym, "quarterly_income_stmt" if mode == "Quarterly" else "income_stmt")
        _header(f"{mode} income statement · Yahoo fallback", T, "stale" if df is not None else "unavailable")
        _df_table(df, T, 40, 8, ["Total Revenue", "Net Income", "EBITDA", "Diluted EPS"])
        return
    _header(f"{mode} results · {src}", T, "live")
    _bar_chart(rec, ["Sales", "Revenue", "Net Profit"], [T["cyan"], T["cyan"], T["green"]], T, f"{key}_fin_bar_{mode}")
    lags, names = ([1, 4], ["QoQ", "YoY"]) if mode == "Quarterly" else ([1], ["YoY"])
    heads, grows = _growth_rows(rec, ["Sales", "Revenue", "Operating Profit", "Financing Profit", "Net Profit", "EPS"], lags, names)
    if grows:
        _header("Growth (latest vs earlier)", T)
        _table(heads, grows, T, ["Sales", "Revenue", "Net Profit"])
    _header("Full table", T)
    _rec_table(rec, T, ["Sales", "Revenue", "Operating Profit", "Net Profit", "EPS", "OPM"])


def _balance(sym, B, T, key):
    rec = B.get("balance_sheet")
    if rec:
        _header("Balance sheet · Screener", T, "live")
        _rec_table(rec, T, ["Total Assets", "Total Liabilities", "Borrowings", "Reserves"])
    else:
        df = yf_attr(sym, "balance_sheet")
        _header("Balance sheet · Yahoo fallback", T, "stale" if df is not None else "unavailable")
        _df_table(df, T, 50, 6, ["Total Assets", "Total Debt", "Stockholders Equity"])


def _cashflow(sym, B, T, key):
    rec = B.get("cash_flow")
    if rec:
        _header("Cash flow · Screener", T, "live")
        _bar_chart(rec, ["Cash from Operating", "Cash from Investing", "Cash from Financing"], [T["green"], T["red"], T["amber"]], T, f"{key}_cf_bar", n=10)
        _rec_table(rec, T, ["Operating", "Investing", "Financing", "Net Cash"])
    else:
        df = yf_attr(sym, "cashflow")
        _header("Cash flow · Yahoo fallback", T, "stale" if df is not None else "unavailable")
        _df_table(df, T, 40, 6, ["Operating Cash Flow", "Free Cash Flow", "Capital Expenditure"])


def _ratios(sym, B, T, key):
    rec = B.get("ratios")
    _header("Efficiency ratios · Screener", T, "live" if rec else "unavailable")
    if rec:
        _rec_table(rec, T, ["ROCE", "Cash Conversion"])
    else:
        _unavailable("Efficiency ratios", "Screener 'Ratios' table (not published for banks / NBFCs)", T)
    de = _debt_equity(B)
    s = B.get("summary") or {}
    _kpis([("Debt / Equity", f"{de}x" if de is not None else "N/A", "Borrowings ÷ (Equity + Reserves)"),
           ("ROCE", f'{s.get("roce", "N/A")}%', "Screener"), ("ROE", f'{s.get("roe", "N/A")}%', "Screener"),
           ("Face Value", f'₹{s.get("face_value", "N/A")}', "Screener")], T, 4)


def _shareholding(sym, B, T, key):
    mode = st.radio("Period", ["Quarterly", "Yearly"], horizontal=True, key=f"{key}_sh_mode", label_visibility="collapsed")
    rec = B.get("shareholding") if mode == "Quarterly" else B.get("shareholding_yearly")
    _header(f"Shareholding pattern ({mode.lower()}) · Screener", T, "live" if rec else "unavailable")
    if not rec:
        mh = yf_attr(sym, "major_holders")
        if mh is not None:
            _header("Major holders · Yahoo fallback", T, "stale")
            _df_table(mh.reset_index() if hasattr(mh, "reset_index") else mh, T, 12, 6)
        else:
            _unavailable("Shareholding", "Screener or Yahoo holders data", T)
        return
    import plotly.graph_objects as go
    fig = go.Figure()
    colors = {"promoters": T["amber"], "fiis": T["cyan"], "diis": T["green"], "government": T["purple"], "public": T["red"]}
    for r in rec["rows"]:
        name = r["label"].lower()
        c = next((v for k, v in colors.items() if name.startswith(k)), None)
        if not c:
            continue
        fig.add_trace(go.Scatter(x=rec["periods"], y=_nums(r), mode="lines+markers", name=r["label"], line=dict(color=c, width=2), marker=dict(size=4)))
    if fig.data:
        fig.update_layout(height=320, margin=dict(l=0, r=10, t=10, b=0), paper_bgcolor=T["bg"], plot_bgcolor=T["bg"],
                          font=dict(color=T["t2"], family="JetBrains Mono, Consolas, monospace", size=9),
                          legend=dict(orientation="h", y=1.1, x=0))
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, gridcolor="#181818", ticksuffix="%")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"{key}_sh_chart_{mode}")
    _rec_table(rec, T, ["Promoters", "FIIs", "DIIs"], last_n=14)


def _peers(sym, B, T, key):
    rec = get_peers_table(sym, tuple(B.get("peer_urls") or ()))
    _header("Peer comparison · Screener", T, "live" if rec else "unavailable")
    if rec:
        _table(rec["periods"], rec["rows"], T, [B.get("name", sym), sym])
        return
    peers = [p for p in _SECTOR_PEERS.get(sym, []) if p != sym]
    if peers:
        q = get_quotes(tuple([sym] + peers))
        rows = [{"label": p + (" ◀" if p == sym else ""), "values": [f'₹{q[p]["price"]:,.2f}', f'{q[p]["chg"]:+.2f}%']}
                for p in [sym] + peers if q.get(p)]
        if rows:
            _panel("Note", f'<div style="font:10px {T["mono"]};color:{T["t2"]}">Screener peer table did not load; showing curated peers with live prices only.</div>', T)
            _table(["PRICE", "CHG"], rows, T, [sym])
            return
    _unavailable("Peer set", "Screener peer table or a curated peer map", T)


def _technicals(sym, T, key):
    h = get_history(sym, "2y")
    if h is None or len(h) < 30:
        _unavailable("Technicals", "Yahoo Finance daily history", T)
        return
    close, high, low, vol = h["Close"].astype(float), h["High"].astype(float), h["Low"].astype(float), h["Volume"].astype(float)
    last = float(close.iloc[-1])
    rsi = float(rsi_wilder(close).iloc[-1])
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    avgv = float(vol.iloc[-21:-1].mean()) if len(vol) > 21 else float("nan")
    buzz = float(vol.iloc[-1]) / avgv if avgv and avgv == avgv else float("nan")
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    _header("Momentum & volatility · computed from Yahoo daily data", T, "live")
    _kpis([("Close", f"₹{last:,.2f}", ""), ("RSI 14", f"{rsi:.1f}", "Wilder"),
           ("ATR 14", f"₹{atr:,.2f}", f"{atr / last * 100:.2f}% of price"),
           ("Volume buzz", f"{buzz:.2f}x" if buzz == buzz else "N/A", "Today vs 20-day avg")], T, 4)
    _kpis([("MACD", f"{macd.iloc[-1]:.2f}", f"Signal {sig.iloc[-1]:.2f}"),
           ("MACD hist", f"{(macd - sig).iloc[-1]:+.2f}", "Positive = bullish momentum"),
           ("52W high", f"₹{high.tail(252).max():,.2f}", f"{(last / high.tail(252).max() - 1) * 100:+.1f}% from high"),
           ("52W low", f"₹{low.tail(252).min():,.2f}", f"{(last / low.tail(252).min() - 1) * 100:+.1f}% above low")], T, 4)
    rows = []
    for n in (10, 20, 50, 100, 200):
        if len(close) >= n:
            m = float(close.rolling(n).mean().iloc[-1])
            rows.append({"label": f"SMA {n}", "values": [f"₹{m:,.2f}", f"{(last / m - 1) * 100:+.2f}%", "ABOVE" if last >= m else "BELOW"]})
    for n in (20, 50):
        m = float(close.ewm(span=n, adjust=False).mean().iloc[-1])
        rows.append({"label": f"EMA {n}", "values": [f"₹{m:,.2f}", f"{(last / m - 1) * 100:+.2f}%", "ABOVE" if last >= m else "BELOW"]})
    _header("Moving averages", T)
    _table(["VALUE", "PRICE VS MA", "STATE"], rows, T, ["SMA 50", "SMA 200"])
    ret_rows = []
    for lab, n in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 252)):
        if len(close) > n:
            ret_rows.append({"label": lab, "values": [f"{(last / float(close.iloc[-1 - n]) - 1) * 100:+.2f}%"]})
    _header("Returns", T)
    _table(["RETURN"], ret_rows, T)
    bb_m, bb_s = close.rolling(20).mean().iloc[-1], close.rolling(20).std().iloc[-1]
    _header("Bollinger (20, 2)", T)
    _table(["LOWER", "MID", "UPPER"], [{"label": "Band", "values": [f"₹{bb_m - 2 * bb_s:,.2f}", f"₹{bb_m:,.2f}", f"₹{bb_m + 2 * bb_s:,.2f}"]}], T)


def _analyst(sym, T, key):
    info = yf_attr(sym, "info") or {}
    tg = yf_attr(sym, "analyst_price_targets") or {}
    _header("Analyst targets · Yahoo Finance", T, "live" if (tg or info.get("targetMeanPrice")) else "unavailable")
    if tg or info.get("targetMeanPrice"):
        g = lambda k, ik: tg.get(k) or info.get(ik)
        f = lambda v: f"₹{v:,.2f}" if isinstance(v, (int, float)) else "N/A"
        _kpis([("Mean target", f(g("mean", "targetMeanPrice")), ""), ("High", f(g("high", "targetHighPrice")), ""),
               ("Low", f(g("low", "targetLowPrice")), ""),
               ("Analysts", str(info.get("numberOfAnalystOpinions", "N/A")), str(info.get("recommendationKey", "")).upper())], T, 4)
    else:
        _unavailable("Price targets", "Yahoo analyst coverage (thin for small/mid caps)", T)
    rec = yf_attr(sym, "recommendations")
    if rec is not None and not rec.empty:
        _header("Rating distribution", T, "live")
        _df_table(rec.set_index(rec.columns[0]).T if rec.columns[0] == "period" else rec, T, 12, 6)
    ud = yf_attr(sym, "upgrades_downgrades")
    if ud is not None and not ud.empty:
        _header("Recent rating changes", T, "live")
        x = ud.head(25)
        cols = [c for c in ("Firm", "ToGrade", "FromGrade", "Action") if c in x.columns]
        _table([c.upper() for c in cols], [{"label": str(i)[:10], "values": [str(r[c]) for c in cols]} for i, r in x.iterrows()], T)


def _dividends(sym, T, key):
    d = yf_attr(sym, "dividends")
    _header("Dividend history · Yahoo Finance", T, "live" if d is not None else "unavailable")
    if d is None:
        _unavailable("Dividends", "Yahoo dividend history", T)
    else:
        ttm = float(d[d.index >= (d.index.max() - pd.Timedelta(days=365))].sum())
        _kpis([("Last dividend", f"₹{float(d.iloc[-1]):,.2f}", str(d.index[-1])[:10]), ("Trailing 12M", f"₹{ttm:,.2f}", "Sum of payouts"),
               ("Payouts on record", str(len(d)), "")], T, 3)
        _table(["₹ PER SHARE"], [{"label": str(i)[:10], "values": [f"{float(v):,.2f}"]} for i, v in d.iloc[::-1].head(25).items()], T)
    sp = yf_attr(sym, "splits")
    if sp is not None:
        _header("Splits / bonus", T, "live")
        _table(["RATIO"], [{"label": str(i)[:10], "values": [f"{float(v):g} : 1"]} for i, v in sp.iloc[::-1].head(15).items()], T)


def _news(sym, T, key, news_fetch_fn):
    news = []
    if get_security_news:
        try:
            news = get_security_news(sym, days=7) or []
        except Exception:
            news = []
    if not news and news_fetch_fn:
        for call in (lambda: news_fetch_fn(sym, days=7), lambda: news_fetch_fn(sym)):
            try:
                news = call() or []
                break
            except Exception:
                continue
    if not news:
        raw = yf_attr(sym, "news") or []
        for n in raw[:20]:
            c = n.get("content", n)
            link = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link")
            news.append({"title": c.get("title"), "source": (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher"),
                         "published": c.get("pubDate") or c.get("providerPublishTime", ""), "url": link})
    if not news:
        _unavailable("Security news", "Arka News Intelligence or Yahoo news", T)
        return
    _header("Security news timeline", T, "live")
    for it in news[:30]:
        title = it.get("title") or it.get("headline") or "Untitled"
        source = it.get("source") or it.get("publisher") or "Source"
        ts = it.get("time_str") or it.get("published") or it.get("published_at") or it.get("time") or ""
        sent = it.get("sentiment_label") or it.get("sentiment") or ""
        url = it.get("url") or it.get("link")
        link = f'<a href="{html.escape(str(url), quote=True)}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(title)}</a>' if url else _esc(title)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><div>{link}</div>'
                    f'<div style="color:{T["t3"]};margin-top:3px">{_esc(source)} · {_esc(ts)} {("· " + _esc(sent)) if sent else ""}</div></div>', unsafe_allow_html=True)


def _announcements(sym, B, T, key):
    d = get_scanx(sym, B.get("name", ""))
    anns = (d or {}).get("announcements") or []
    _header("Exchange announcements · ScanX (optional source)", T, "live" if anns else "unavailable")
    if not anns:
        _unavailable("Announcements", "Public ScanX announcement section", T, "ScanX is optional; the rest of the terminal does not depend on it.")
        return
    for a in anns[:60]:
        url = html.escape(str(a.get("url", "")), quote=True)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><a href="{url}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(a.get("title"))}</a>'
                    f'<div style="color:{T["t3"]};margin-top:3px">OPEN SOURCE DOCUMENT ↗</div></div>', unsafe_allow_html=True)


def _mf(sym, B, T, key):
    d = get_scanx(sym, B.get("name", ""))
    df = ((d or {}).get("tables") or {}).get("mf_holdings")
    api = (d or {}).get("mf_transactions") or {}
    if df is not None and not df.empty:
        _header("Mutual fund holdings · ScanX (optional source)", T, "live")
        cols = list(df.columns)
        _table([str(c) for c in cols[1:]], [{"label": str(r.iloc[0]), "values": [_fmt_dt_cell(v) for v in r.iloc[1:].tolist()]} for _, r in df.head(80).iterrows()], T)
        return
    rows = api.get("rows") or []
    if rows:
        _header("Mutual fund transactions · ScanX (optional source)", T, "live")
        cols = list(dict.fromkeys(k for r in rows for k in r.keys()))
        _table(cols, [{"label": str(r.get("fundName") or r.get("fund_name") or r.get("scheme") or "Fund"), "values": [str(r.get(c, "")) for c in cols]} for r in rows[:100]], T)
        return
    _header("Mutual fund holdings · ScanX (optional source)", T, "unavailable")
    _unavailable("MF holdings", "ScanX company page or mftransaction endpoint (needs an ISIN mapping)", T, "Optional source — not required by the rest of the terminal.")


def _thesis(sym, T, key):
    fields = [("Bull case", "bull"), ("Bear case", "bear"), ("Catalysts", "cat"), ("Risks", "risk")]
    for title, k in fields:
        st.text_area(title, height=100, key=f"{key}_note_{k}_{sym}")
    txt = "\n\n".join(f"{t.upper()}\n{st.session_state.get(f'{key}_note_{k}_{sym}', '')}" for t, k in fields)
    st.download_button("DOWNLOAD NOTES (.txt)", txt, file_name=f"{sym}_research_notes.txt", key=f"{key}_note_dl_{sym}")
    _panel("Note", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">Notes live in this browser session. Download them to keep a copy.</div>', T)


def _sources(sym, B, T, key):
    _header("Data provenance", T)
    _table(["SOURCE", "STATUS"], [
        {"label": "Fundamentals (financials, ratios, holding)", "values": ["Screener.in", "live" if B.get("resolved") else "unavailable"]},
        {"label": "Prices / charts / technicals", "values": ["Yahoo Finance", "live"]},
        {"label": "Analyst / dividends / fallbacks", "values": ["Yahoo Finance", "on demand"]},
        {"label": "News", "values": ["news_feed.py", "on demand"]},
        {"label": "Announcements / MF holdings", "values": ["ScanX (optional)", "on demand"]}], T)
    _panel("Integrity rule", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">Values are shown only when a source returns them. Missing data says DATA UNAVAILABLE; nothing is estimated or invented.</div>', T)


# ═══════════════════════════ Panel + page ═══════════════════════════
def _css(T):
    st.markdown(f"""<style>
    div[class*="st-key-rpfn"] div[role="radiogroup"]{{gap:3px;flex-wrap:wrap}}
    div[class*="st-key-rpfn"] label{{background:{T["panel"]};border:1px solid {T["border"]};padding:6px 10px !important;margin:0 !important;cursor:pointer}}
    div[class*="st-key-rpfn"] label > div:first-child{{display:none !important}}
    div[class*="st-key-rpfn"] label p{{font:700 9px {T["mono"]} !important;color:{T["t2"]} !important;letter-spacing:.6px;white-space:nowrap}}
    div[class*="st-key-rpfn"] label:has(input:checked){{border-color:{T["amber"]};background:{T["panel2"]}}}
    div[class*="st-key-rpfn"] label:has(input:checked) p{{color:{T["amber"]} !important}}
    </style>""", unsafe_allow_html=True)


@st.fragment
def _research_fragment(symbol, T, news_fetch_fn, key):
    fn = st.radio("Research function", FUNCS, horizontal=True, label_visibility="collapsed", key=f"rpfn_{key}")
    needs_bundle = fn in {"OVERVIEW", "FINANCIALS", "BALANCE SHEET", "CASH FLOW", "RATIOS", "SHAREHOLDING", "PEERS",
                          "ANNOUNCEMENTS", "MF HOLDINGS", "SOURCES"}
    B = {"resolved": False, "summary": {}, "growth": {}}
    if needs_bundle:
        with st.spinner("Loading fundamentals…"):
            B = get_screener_bundle(symbol)
    if fn == "OVERVIEW":
        _overview(symbol, B, T, key)
    elif fn == "FINANCIALS":
        _financials(symbol, B, T, key)
    elif fn == "BALANCE SHEET":
        _balance(symbol, B, T, key)
    elif fn == "CASH FLOW":
        _cashflow(symbol, B, T, key)
    elif fn == "RATIOS":
        _ratios(symbol, B, T, key)
    elif fn == "SHAREHOLDING":
        _shareholding(symbol, B, T, key)
    elif fn == "PEERS":
        _peers(symbol, B, T, key)
    elif fn == "TECHNICALS":
        _technicals(symbol, T, key)
    elif fn == "ANALYST":
        _analyst(symbol, T, key)
    elif fn == "DIVIDENDS":
        _dividends(symbol, T, key)
    elif fn == "NEWS":
        _news(symbol, T, key, news_fetch_fn)
    elif fn == "ANNOUNCEMENTS":
        _announcements(symbol, B, T, key)
    elif fn == "MF HOLDINGS":
        _mf(symbol, B, T, key)
    elif fn == "THESIS":
        _thesis(symbol, T, key)
    elif fn == "SOURCES":
        _sources(symbol, B, T, key)


def render_research_panel(symbol, T=None, news_fetch_fn=None, key="rp"):
    """Reusable research panel. Call from the Security workspace or the Research page."""
    T = _theme(T)
    sym = str(symbol or "").upper().strip()
    if not sym:
        return
    _css(T)
    _research_fragment(sym, T, news_fetch_fn, key)


def render_research_page(T=None, news_fetch_fn=None):
    T = _theme(T)
    _css(T)
    now = datetime.now(IST)
    opened = now.weekday() < 5 and now.replace(hour=9, minute=15, second=0, microsecond=0) <= now <= now.replace(hour=15, minute=30, second=0, microsecond=0)
    color = T["green"] if opened else T["red"]
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;border:1px solid {T["border"]};border-bottom:2px solid {T["amber"]};background:{T["panel"]};padding:7px 12px;margin-bottom:10px;font:10px {T["mono"]}">'
        f'<span style="color:{T["amber"]};font-weight:800;letter-spacing:1px">ARKA TERMINAL · RESEARCH</span>'
        f'<span style="color:{color};font-weight:700">● {"MARKET OPEN" if opened else "MARKET CLOSED"}</span>'
        f'<span style="color:{T["t3"]}">{now.strftime("%d %b %Y · %H:%M IST")}</span></div>', unsafe_allow_html=True)

    active = st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    with st.form("research_load_form", clear_on_submit=False):
        c1, c2 = st.columns([5, 1])
        q = c1.text_input("Research security", value=active, placeholder="RELIANCE / HDFCBANK / TCS",
                          label_visibility="collapsed", key=f"research_q_{active}")
        go_ = c2.form_submit_button("LOAD", type="primary", use_container_width=True)
    if go_ and q.strip():
        st.session_state.active_security = q.strip().upper()
        st.session_state.research_last_query = q.strip().upper()
        st.rerun()

    symbol = st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    if not symbol:
        _panel("Research ready", f'<div style="font:11px {T["mono"]};color:{T["t3"]}">Load a security to open its research workspace.</div>', T, dashed=True)
        return

    hist = get_history(symbol, "1y")
    B = get_screener_bundle(symbol)
    name = B.get("name") or symbol
    price = chg = None
    if hist is not None and len(hist) >= 2:
        price, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
        chg = (price / prev - 1) * 100
    cc = T["green"] if (chg or 0) >= 0 else T["red"]
    st.markdown(
        f'<div style="border:1px solid {T["border"]};border-top:2px solid {T["amber"]};background:{T["panel"]};padding:12px 14px;margin:8px 0;display:flex;justify-content:space-between;align-items:end;flex-wrap:wrap;gap:12px">'
        f'<div><span style="font:800 18px {T["mono"]};color:{T["ivory"]}">{_esc(name)}</span><span style="font:10px {T["mono"]};color:{T["t3"]};margin-left:10px">{_esc(symbol)} · NSE</span></div>'
        f'<div style="text-align:right"><span style="font:800 22px {T["mono"]};color:{T["ivory"]}">{"₹{:,.2f}".format(price) if price else "N/A"}</span>'
        f'<span style="font:700 11px {T["mono"]};color:{cc};margin-left:10px">{"{:+.2f}%".format(chg) if chg is not None else ""}</span></div></div>',
        unsafe_allow_html=True)

    try:
        from chart_engine import render_pro_chart
        render_pro_chart(symbol, height=620)
    except Exception as e:  # chart must never break the research page
        st.caption(f"Chart unavailable: {e}")

    render_research_panel(symbol, T, news_fetch_fn, key="rp_page")
