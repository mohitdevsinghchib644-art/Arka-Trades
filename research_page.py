"""Arka Trades — Research Terminal v12 (function-code UI + new analytics).

What changed vs v11
-------------------
UI / navigation
* Bloomberg-style function codes. Every section has a short code (DES, FA, BS,
  CF, HDS, RV, TECH …) and lives in a grouped left-hand menu (SNAPSHOT,
  FINANCIALS, OWNERSHIP, COMPARE, MARKET, EVENTS, WORKSPACE) instead of one
  long row of 15 look-alike buttons.
* Command bar: type a code (``FA``), a symbol + code (``TCS FA``) or just a
  symbol (``INFY``) and press Enter. Long names also work (``VALUATION``).
* Always-visible snapshot strip (price, MCap, P/E, P/B, ROE, ROCE, D/E, yield,
  52-week position) above every section, so key numbers never need hunting.
* Function title bar tells you where you are and what the screen shows.
* Every statement table has a CSV export. Recently viewed symbols are one
  click away on the Research page.

New functions (all computed from data already fetched — nothing invented)
* HC    Health check — Piotroski-style 9-point quality score + automatic
        red / green flags (promoter selling, debt growth, cash conversion …).
* VAL   Valuation — TTM EPS, P/E, P/B, earnings yield, Graham number and a
        historical P/E band built from quarterly EPS + price history.
* PERF  Performance vs NIFTY 50 — rebased chart, alpha, beta, correlation,
        volatility, max drawdown.
* SEAS  Seasonality — monthly-returns heatmap, average month, win rate.
* TECH  adds a rule-based signal summary and classic pivot levels.
* DES   shows the next earnings date when Yahoo publishes one.

Unchanged
* Public API: render_research_panel(), render_research_page(), FUNCS.
* data_engine contract. Missing data still says DATA UNAVAILABLE.
"""
from __future__ import annotations

import html
import math
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from data_engine import (get_screener_bundle, get_peers_table, get_history, get_quotes,
                         get_scanx, yf_attr, rsi_wilder)

try:
    from news_feed import get_security_news
except Exception:  # pragma: no cover
    get_security_news = None

try:
    import screener_scraper as _ss
    _SECTOR_PEERS = getattr(_ss, "_SECTOR_PEERS", {})
except Exception:  # pragma: no cover
    _ss = None
    _SECTOR_PEERS = {}

IST = timezone(timedelta(hours=5, minutes=30))
BENCHMARK = ("^NSEI", "NIFTY 50")

T0 = {
    "bg": "#000000", "panel": "#080808", "panel2": "#0E0E0E", "row_alt": "#0B0B0B",
    "border": "#242424", "amber": "#FF9F0A", "ivory": "#E8E8E8", "t2": "#9A9A9A",
    "t3": "#5E5E5E", "green": "#30D158", "red": "#FF453A", "cyan": "#5AC8FA",
    "purple": "#BF5AF2", "mono": "'JetBrains Mono','Consolas',monospace",
}

# ═══════════════════════════ Function map ═══════════════════════════
GROUPS = [
    ("SNAPSHOT", [
        ("DES", "Description", "Company profile, key fundamentals, growth snapshot, pros & cons"),
        ("HC", "Health Check", "Piotroski-style quality score and automatic red / green flags"),
        ("VAL", "Valuation", "TTM P/E, P/B, earnings yield, Graham number and historical P/E band"),
    ]),
    ("FINANCIALS", [
        ("FA", "Income Statement", "Quarterly / yearly results with QoQ and YoY growth"),
        ("BS", "Balance Sheet", "Assets, liabilities, borrowings and reserves"),
        ("CF", "Cash Flow", "Operating, investing and financing cash flows"),
        ("RAT", "Ratios", "Efficiency and return ratios"),
    ]),
    ("OWNERSHIP", [
        ("HDS", "Shareholding", "Promoter / FII / DII / public holding trend"),
        ("MF", "MF Holdings", "Mutual fund positions (ScanX, optional source)"),
    ]),
    ("COMPARE", [
        ("RV", "Peers", "Relative value versus sector peers"),
        ("PERF", "vs NIFTY 50", "Relative performance, alpha, beta, volatility, drawdown"),
    ]),
    ("MARKET", [
        ("TECH", "Technicals", "Trend, momentum, moving averages and pivot levels"),
        ("SEAS", "Seasonality", "Monthly return heatmap, average month and win rate"),
        ("ANR", "Analysts", "Price targets, rating distribution and rating changes"),
    ]),
    ("EVENTS", [
        ("DVD", "Dividends", "Dividend history, splits and bonus issues"),
        ("CN", "News", "Company news timeline"),
        ("CACS", "Announcements", "Exchange filings (ScanX, optional source)"),
    ]),
    ("WORKSPACE", [
        ("NOTE", "Thesis Notes", "Your bull / bear case, catalysts, risks and target"),
        ("SRC", "Sources", "Where every number on this screen comes from"),
    ]),
]
FN = {code: (name, desc, grp) for grp, items in GROUPS for code, name, desc in items}
FUNCS = list(FN)

ALIASES = {
    "OVERVIEW": "DES", "PROFILE": "DES", "HEALTH": "HC", "SCORE": "HC", "PIOTROSKI": "HC",
    "VALUATION": "VAL", "PE": "VAL", "FINANCIALS": "FA", "RESULTS": "FA", "PL": "FA", "IS": "FA",
    "BALANCE": "BS", "CASH": "CF", "CASHFLOW": "CF", "RATIOS": "RAT", "SHAREHOLDING": "HDS",
    "OWN": "HDS", "HOLDING": "HDS", "MFH": "MF", "PEERS": "RV", "COMP": "RV", "PERFORMANCE": "PERF",
    "BETA": "PERF", "GP": "TECH", "TECHNICALS": "TECH", "SEASONALITY": "SEAS", "SEAG": "SEAS",
    "ANALYST": "ANR", "TARGET": "ANR", "DIVIDENDS": "DVD", "NEWS": "CN", "ANNOUNCEMENTS": "CACS",
    "FILINGS": "CACS", "THESIS": "NOTE", "NOTES": "NOTE", "SOURCES": "SRC",
}


def _theme(T):
    x = dict(T0)
    if T:
        x.update(T)
    return x


# ═══════════════════════════ Small helpers ═══════════════════════════
def _esc(x: Any) -> str:
    return html.escape(str(x))


def _f(v):
    """Robust number parser for Screener strings like '1,234', '23%', '₹45 Cr'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    s = str(v).replace(",", "").replace("%", "").replace("₹", "").replace("Cr", "").strip()
    if s in ("", "-", "—", "nan", "NaN", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _naive(s: pd.Series) -> pd.Series:
    """Timezone-free, day-normalised index so stock and index series align."""
    s = s.copy()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _market_open(now=None):
    now = now or datetime.now(IST)
    return now.weekday() < 5 and now.replace(hour=9, minute=15, second=0, microsecond=0) <= now <= now.replace(hour=15, minute=30, second=0, microsecond=0)


def _pct(v, d=1, sign=False):
    return "N/A" if v is None else (f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%")


def _rs(v, d=2):
    return "N/A" if v is None else f"₹{v:,.{d}f}"


@st.cache_data(ttl=900, show_spinner=False)
def _index_history(ticker: str = BENCHMARK[0], period: str = "2y"):
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        return df if df is not None and not df.empty else None
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _earnings_date(sym: str):
    if _ss is None or not hasattr(_ss, "get_earnings_date"):
        return None
    try:
        r = _ss.get_earnings_date(sym)
        return str(r.get("date"))[:10] if r.get("status") == "live" and r.get("date") else None
    except Exception:
        return None


def _hist(sym, period, fallback="2y"):
    h = get_history(sym, period)
    if (h is None or len(h) < 2) and fallback:
        h = get_history(sym, fallback)
    return h if h is not None and len(h) >= 2 else None


# ═══════════════════════════ UI primitives ═══════════════════════════
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
    """items: (label, value, sub) or (label, value, sub, color)."""
    c = st.columns(cols)
    for i, it in enumerate(items):
        label, value, sub = it[:3]
        color = it[3] if len(it) > 3 and it[3] else T["ivory"]
        with c[i % cols]:
            st.markdown(
                f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:10px 11px;min-height:67px;margin-bottom:7px">'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">{_esc(label.upper())}</div>'
                f'<div style="font:700 15px {T["mono"]};color:{color};margin-top:4px">{_esc(value)}</div>'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:3px">{_esc(sub or "")}</div></div>',
                unsafe_allow_html=True)


def _table(periods, rows, T, highlights=(), dl=None):
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
        bg = T["row_alt"] if i % 2 else T["panel"]
        out.append(
            f'<tr style="background:{bg};border-bottom:1px solid {T["border"]}">'
            f'<td style="padding:7px 8px;color:{T["amber"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap;position:sticky;left:0;background:{bg}">{_esc(label)}</td>{cells}</tr>')
    st.markdown(
        f'<div style="overflow-x:auto;border:1px solid {T["border"]};background:{T["panel"]};max-width:100%"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="background:{T["panel2"]};border-bottom:1px solid {T["border"]}"><th style="text-align:left;padding:7px 8px;color:{T["t3"]};font:700 9px {T["mono"]};position:sticky;left:0;background:{T["panel2"]}">METRIC</th>{heads}</tr></thead>'
        f'<tbody>{"".join(out)}</tbody></table></div>', unsafe_allow_html=True)
    if dl:
        n = len(periods)
        data = [[r.get("label", "")] + (list(r.get("values", [])) + [""] * n)[:n] for r in rows]
        csv = pd.DataFrame(data, columns=["METRIC"] + [str(p) for p in periods]).to_csv(index=False)
        st.download_button("⤓ CSV", csv, file_name=f"{dl}.csv", mime="text/csv", key=f"dl_{dl}")


def _rec_table(rec, T, highlights=(), last_n=None, dl=None):
    """Render a Screener-style record {'periods': [...], 'rows': [{'label','values'}]}."""
    if not rec or not rec.get("rows"):
        _unavailable("Table", "Screener.in company page", T)
        return
    periods, rows = rec["periods"], rec["rows"]
    if last_n and len(periods) > last_n:
        periods = periods[-last_n:]
        rows = [{"label": r["label"], "values": r["values"][-last_n:]} for r in rows]
    _table(periods, rows, T, highlights, dl)


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


def _df_table(df, T, max_rows=40, max_cols=8, highlights=(), dl=None):
    """yfinance statement frame (index = metric, columns = dates)."""
    if df is None or df.empty:
        _unavailable("Table", "Yahoo Finance dataset", T)
        return
    x = df.iloc[:max_rows, :max_cols]
    periods = [str(c)[:10] for c in x.columns]
    rows = [{"label": str(i), "values": [_fmt_dt_cell(v) for v in r.tolist()]} for i, r in x.iterrows()]
    _table(periods, rows, T, highlights, dl)


def _checklist(items, T, labels=("PASS", "FAIL", "N/A")):
    """items: (text, ok: True/False/None, detail)."""
    body = ""
    for text, ok, detail in items:
        mark, col = ((labels[0], T["green"]) if ok is True else
                     (labels[1], T["red"]) if ok is False else (labels[2], T["t3"]))
        body += (f'<div style="display:flex;gap:12px;align-items:baseline;padding:6px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}">'
                 f'<b style="color:{col};min-width:42px">{mark}</b><span style="color:{T["ivory"]};flex:1">{_esc(text)}</span>'
                 f'<span style="color:{T["t3"]};text-align:right">{_esc(detail or "")}</span></div>')
    return body


def _fig(fig, T, h=300, key=None, **layout):
    fig.update_layout(height=h, margin=dict(l=0, r=10, t=10, b=0), paper_bgcolor=T["bg"], plot_bgcolor=T["bg"],
                      font=dict(color=T["t2"], family="JetBrains Mono, Consolas, monospace", size=9),
                      legend=dict(orientation="h", y=1.1, x=0), **layout)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#181818")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


# ═══════════════════════════ Record helpers ═══════════════════════════
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
    return [_f(v) for v in (row or {}).get("values", [])]


def _annual_map(rec, *labels):
    """{period: value} for a row, skipping TTM and blanks."""
    r = _row(rec, *labels)
    if not r or not rec:
        return {}
    out = {}
    for p, v in zip(rec["periods"], r["values"]):
        if "TTM" in str(p).upper():
            continue
        x = _f(v)
        if x is not None:
            out[str(p)] = x
    return out


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
    seen = set()
    for lab, col in zip(labels, colors):
        r = _row(rec, lab)
        if not r or r["label"] in seen:
            continue
        seen.add(r["label"])
        fig.add_trace(go.Bar(x=rec["periods"][-n:], y=_nums(r)[-n:], name=r["label"], marker_color=col))
    if fig.data:
        _fig(fig, T, 290, key, barmode="group")


def _debt_equity(B):
    bs = B.get("balance_sheet")
    try:
        bv = [x for x in _nums(_row(bs, "borrowings")) if x is not None][-1]
        rv = [x for x in _nums(_row(bs, "reserves")) if x is not None][-1]
        ev = [x for x in _nums(_row(bs, "equity capital", "share capital")) if x is not None][-1]
        nw = rv + ev
        return round(bv / nw, 2) if nw else None
    except Exception:
        return None


def _ttm_eps(B):
    eps = _nums(_row(B.get("quarterly"), "EPS"))
    last4 = eps[-4:]
    return sum(last4) if len(last4) == 4 and all(v is not None for v in last4) else None


# ═══════════════════════════ Snapshot strip ═══════════════════════════
def _snapshot(sym, B, T, show_quote=True):
    s = B.get("summary") or {}
    hist = get_history(sym, "1y")
    price = chg = pos = None
    if hist is not None and len(hist) >= 2:
        price, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
        chg = (price / prev - 1) * 100
        h252 = hist.tail(252)
        lo, hi = float(h252["Low"].min()), float(h252["High"].max())
        pos = None if hi == lo else max(0.0, min(100.0, (price - lo) / (hi - lo) * 100))
    bv = _f(s.get("book_value"))
    de = _debt_equity(B)
    cells = []
    if show_quote:
        cc = T["green"] if (chg or 0) >= 0 else T["red"]
        cells.append(("LAST", _rs(price), T["ivory"]))
        cells.append(("CHG", _pct(chg, 2, True), cc))
    cells += [
        ("MCAP", f'₹{s["market_cap"]} Cr' if s.get("market_cap") else "N/A", None),
        ("P/E", f'{s["pe_ratio"]}x' if s.get("pe_ratio") else "N/A", None),
        ("P/B", f"{price / bv:.2f}x" if price and bv else "N/A", None),
        ("ROE", f'{s["roe"]}%' if s.get("roe") else "N/A", None),
        ("ROCE", f'{s["roce"]}%' if s.get("roce") else "N/A", None),
        ("D/E", f"{de}x" if de is not None else "N/A", None),
        ("DIV YLD", f'{s["dividend_yield"]}%' if s.get("dividend_yield") else "N/A", None),
    ]
    body = "".join(
        f'<div style="padding:6px 13px;border-right:1px solid {T["border"]}">'
        f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">{_esc(l)}</div>'
        f'<div style="font:700 12px {T["mono"]};color:{c or T["ivory"]};margin-top:2px;white-space:nowrap">{_esc(v)}</div></div>'
        for l, v, c in cells)
    if pos is not None:
        body += (f'<div style="padding:6px 13px;min-width:150px;flex:1">'
                 f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">52W POSITION · {pos:.0f}%</div>'
                 f'<div style="position:relative;height:5px;background:#1a1a1a;margin-top:8px">'
                 f'<div style="position:absolute;left:0;top:0;height:5px;width:{pos:.1f}%;background:{T["amber"]}55"></div>'
                 f'<div style="position:absolute;left:{pos:.1f}%;top:-3px;width:3px;height:11px;background:{T["amber"]}"></div></div></div>')
    st.markdown(f'<div style="display:flex;flex-wrap:wrap;align-items:stretch;border:1px solid {T["border"]};background:{T["panel"]};margin:4px 0 8px">{body}</div>',
                unsafe_allow_html=True)


# ═══════════════════════════ Sections ═══════════════════════════
def _overview(sym, B, T, key, nf):
    s = B.get("summary") or {}
    info = yf_attr(sym, "info") or {}
    _header("Key fundamentals", T, "live" if B.get("resolved") else "unavailable", B.get("url"))
    ed = _earnings_date(sym)
    _kpis([
        ("Market Cap", f'₹{s["market_cap"]} Cr' if s.get("market_cap") else "N/A", "Screener"),
        ("Stock P/E", f'{s["pe_ratio"]}x' if s.get("pe_ratio") else "N/A", "Screener"),
        ("Book Value", f'₹{s["book_value"]}' if s.get("book_value") else "N/A", "Screener"),
        ("Face Value", f'₹{s["face_value"]}' if s.get("face_value") else "N/A", "Screener"),
        ("52W High / Low", f'₹{s["year_high"]} / ₹{s["year_low"]}' if s.get("year_high") else "N/A", "Screener"),
        ("Sector", str(info.get("sector") or "N/A"), str(info.get("industry") or "Yahoo")),
        ("Employees", f'{info["fullTimeEmployees"]:,}' if isinstance(info.get("fullTimeEmployees"), int) else "N/A", "Yahoo"),
        ("Next earnings", ed or "N/A", "Yahoo calendar" if ed else "Not published"),
    ], T, 4)

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
            _panel("Pros (Screener)", "".join(f'<div style="font:10px {T["mono"]};color:{T["t2"]};padding:3px 0;line-height:1.6"><span style="color:{T["green"]}">+</span> {_esc(x)}</div>' for x in B.get("pros") or []) or "—", T)
        with b:
            _panel("Cons (Screener)", "".join(f'<div style="font:10px {T["mono"]};color:{T["t2"]};padding:3px 0;line-height:1.6"><span style="color:{T["red"]}">−</span> {_esc(x)}</div>' for x in B.get("cons") or []) or "—", T)

    if not B.get("resolved"):
        _unavailable("Screener fundamentals", "Screener.in company page", T, B.get("reason"))


# ---------- HC: health check ----------
def _piotroski(B):
    y, bs, cf = B.get("yearly"), B.get("balance_sheet"), B.get("cash_flow")
    npf = _annual_map(y, "Net Profit", "Profit for the period")
    sales = _annual_map(y, "Sales", "Revenue")
    opm = _annual_map(y, "OPM", "Financing Margin")
    ta = _annual_map(bs, "Total Assets", "Total Liabilities")
    debt = _annual_map(bs, "Borrowings")
    eqc = _annual_map(bs, "Equity Capital", "Share Capital")
    cfo = _annual_map(cf, "Cash from Operating", "Operating")
    yrs = [p for p in (y or {}).get("periods", []) if p in npf and p in ta]
    if len(yrs) < 2:
        return None
    t, p = yrs[-1], yrs[-2]

    def ratio(a, b, k):
        return a[k] / b[k] if k in a and b.get(k) else None

    def up(a, b):
        return None if a is None or b is None else a > b

    def arrow(a, b, mult=100, suf="%", d=1):
        return "" if a is None or b is None else f"{b * mult:.{d}f}{suf} → {a * mult:.{d}f}{suf}"

    roa_t, roa_p = ratio(npf, ta, t), ratio(npf, ta, p)
    lev_t, lev_p = ratio(debt, ta, t), ratio(debt, ta, p)
    at_t, at_p = ratio(sales, ta, t), ratio(sales, ta, p)
    has_cfo = t in cfo
    tests = [
        ("Profitable (net profit > 0)", npf[t] > 0, f"NP ₹{npf[t]:,.0f} Cr"),
        ("Operating cash flow positive", cfo[t] > 0 if has_cfo else None,
         f"CFO ₹{cfo[t]:,.0f} Cr" if has_cfo else "cash flow not available"),
        ("Return on assets improving", up(roa_t, roa_p), arrow(roa_t, roa_p)),
        ("Cash flow exceeds profit (earnings quality)", (cfo[t] > npf[t]) if has_cfo else None,
         f"CFO/NP {cfo[t] / npf[t]:.2f}x" if has_cfo and npf[t] else ""),
        ("Leverage falling (debt / assets)", None if lev_t is None or lev_p is None else lev_t <= lev_p, arrow(lev_t, lev_p)),
        ("Liquidity improving (current ratio)", None, "current assets not published on Screener"),
        ("No share dilution (equity capital)", None if t not in eqc or p not in eqc else eqc[t] <= eqc[p],
         "a split / bonus can show as a false fail"),
        ("Operating margin improving", up(opm.get(t), opm.get(p)), arrow(opm.get(t), opm.get(p), 1)),
        ("Asset turnover improving", up(at_t, at_p), arrow(at_t, at_p, 1, "x", 2)),
    ]
    return tests, t, p


def _flags(B):
    out = []
    y, bs, cf = B.get("yearly"), B.get("balance_sheet"), B.get("cash_flow")
    yp = (y or {}).get("periods", [])

    pr = [v for v in _nums(_row(B.get("shareholding"), "Promoters")) if v is not None]
    if len(pr) >= 5:
        d = pr[-1] - pr[-5]
        if d <= -2:
            out.append((f"Promoter holding down {abs(d):.1f} pp over 4 quarters", False, f"{pr[-5]:.1f}% → {pr[-1]:.1f}%"))
        elif d >= 1:
            out.append((f"Promoter holding up {d:.1f} pp over 4 quarters", True, f"{pr[-5]:.1f}% → {pr[-1]:.1f}%"))

    debt = _annual_map(bs, "Borrowings")
    dy = [p for p in (bs or {}).get("periods", []) if p in debt]
    if dy:
        if debt[dy[-1]] == 0:
            out.append(("Debt-free on latest balance sheet", True, dy[-1]))
        elif len(dy) >= 4 and debt[dy[-4]] > 0 and debt[dy[-1]] / debt[dy[-4]] > 1.5:
            out.append((f"Borrowings up {(debt[dy[-1]] / debt[dy[-4]] - 1) * 100:.0f}% in 3 years", False,
                        f"₹{debt[dy[-4]]:,.0f} → ₹{debt[dy[-1]]:,.0f} Cr"))

    npf = _annual_map(y, "Net Profit", "Profit for the period")
    cfo = _annual_map(cf, "Cash from Operating", "Operating")
    common = [p for p in yp if p in npf and p in cfo][-5:]
    if len(common) >= 3:
        snp, scfo = sum(npf[p] for p in common), sum(cfo[p] for p in common)
        if snp > 0:
            conv = scfo / snp
            if conv < 0.5:
                out.append((f"Weak cash conversion over {len(common)} years", False, f"CFO = {conv * 100:.0f}% of profit"))
            elif conv >= 0.9:
                out.append((f"Strong cash conversion over {len(common)} years", True, f"CFO = {conv * 100:.0f}% of profit"))

    opm = _annual_map(y, "OPM")
    oy = [p for p in yp if p in opm]
    if len(oy) >= 2:
        d = opm[oy[-1]] - opm[oy[-2]]
        if d <= -3:
            out.append((f"Operating margin fell {abs(d):.1f} pp YoY", False, f"{opm[oy[-2]]:.0f}% → {opm[oy[-1]]:.0f}%"))
        elif d >= 3:
            out.append((f"Operating margin expanded {d:.1f} pp YoY", True, f"{opm[oy[-2]]:.0f}% → {opm[oy[-1]]:.0f}%"))

    for label, m in (("Sales", _annual_map(y, "Sales", "Revenue")), ("Net profit", npf)):
        ys = [p for p in yp if p in m]
        if len(ys) < 4:
            continue
        a, b = m[ys[-1]], m[ys[-4]]
        if a > 0 and b > 0:
            cagr = ((a / b) ** (1 / 3) - 1) * 100
            if cagr < 0:
                out.append((f"{label} shrinking", False, f"3-yr CAGR {cagr:+.1f}%"))
            elif cagr >= 15:
                out.append((f"{label} compounding fast", True, f"3-yr CAGR {cagr:+.1f}%"))
        elif b > 0 >= a:
            out.append((f"{label} turned negative", False, f"{ys[-4]} → {ys[-1]}"))
    return out


def _health(sym, B, T, key, nf):
    if not B.get("resolved"):
        _unavailable("Health check", "Screener yearly P&L, balance sheet and cash flow", T, B.get("reason"))
        return
    res = _piotroski(B)
    _header("Quality score · Piotroski-style, computed from Screener annual data", T, "live" if res else "unavailable")
    if res is None:
        _unavailable("Quality score", "At least two fiscal years of P&L and balance sheet", T)
    else:
        tests, t, p = res
        avail = [x for x in tests if x[1] is not None]
        score = sum(1 for x in avail if x[1])
        ratio = score / len(avail) if avail else 0
        grade, gc = (("STRONG", T["green"]) if ratio >= 0.75 else ("AVERAGE", T["amber"]) if ratio >= 0.45 else ("WEAK", T["red"]))
        _kpis([("Score", f"{score} / {len(avail)}", f"{len(tests) - len(avail)} test(s) not computable", gc),
               ("Reading", grade, f"Comparing {p} → {t}", gc)], T, 2)
        _panel("The 9 tests", _checklist(tests, T), T)
    flags = _flags(B)
    _header("Automatic flags", T, "live" if flags else None)
    if flags:
        _panel(f"{sum(1 for f in flags if f[1] is False)} warning(s) · {sum(1 for f in flags if f[1])} positive(s)",
               _checklist(flags, T, ("✓ GOOD", "⚠ WARN", "—")), T)
    else:
        _panel("No flags triggered", f'<div style="font:10px {T["mono"]};color:{T["t2"]}">None of the rule thresholds were crossed, or the data needed was not available.</div>', T)
    st.caption("Rule-based screen for research only — not investment advice. Thresholds: promoter ±2/1 pp, debt +50% in 3y, "
               "CFO/NP <50% or ≥90%, OPM ±3 pp, 3-yr CAGR <0% or ≥15%.")


# ---------- VAL: valuation ----------
def _quarter_end(label):
    try:
        return pd.to_datetime(str(label).strip(), format="%b %Y") + pd.offsets.MonthEnd(0)
    except Exception:
        return None


def _valuation(sym, B, T, key, nf):
    import plotly.graph_objects as go
    s = B.get("summary") or {}
    hist = _hist(sym, "5y")
    if hist is None:
        _unavailable("Valuation", "Yahoo daily price history", T)
        return
    close = _naive(hist["Close"].astype(float))
    price = float(close.iloc[-1])
    ttm = _ttm_eps(B)
    bv = _f(s.get("book_value"))
    pe = price / ttm if ttm and ttm > 0 else None
    pb = price / bv if bv and bv > 0 else None
    graham = math.sqrt(22.5 * ttm * bv) if ttm and bv and ttm > 0 and bv > 0 else None
    _header("Valuation · computed from Screener EPS / book value + Yahoo price", T, "live" if ttm else "unavailable")
    _kpis([("TTM EPS", _rs(ttm), "Sum of last 4 quarters"),
           ("P/E (TTM)", f"{pe:.1f}x" if pe else ("n/m (loss)" if ttm is not None else "N/A"), "Price ÷ TTM EPS"),
           ("P/B", f"{pb:.2f}x" if pb else "N/A", "Price ÷ book value"),
           ("Earnings yield", _pct(ttm / price * 100 if ttm else None, 2), "TTM EPS ÷ price"),
           ("Graham number", _rs(graham), "√(22.5 × EPS × BVPS)"),
           ("Price vs Graham", _pct((price / graham - 1) * 100, 1, True) if graham else "N/A",
            "Negative = below Graham value", (T["green"] if graham and price < graham else T["red"]) if graham else None)], T, 3)

    q = B.get("quarterly")
    eps = _nums(_row(q, "EPS"))
    pts = []
    if q and eps:
        for i in range(3, len(eps)):
            w = eps[i - 3:i + 1]
            if any(v is None for v in w) or sum(w) <= 0:
                continue
            d = _quarter_end(q["periods"][i]) if i < len(q["periods"]) else None
            if d is None or d < close.index[0]:
                continue
            px = close.asof(d)
            if pd.notna(px):
                pts.append((d, float(px) / sum(w)))
    _header("Historical P/E band", T, "live" if len(pts) >= 4 else "unavailable",
            "Quarter-end price ÷ trailing-4-quarter EPS. Limited by how many quarters Screener shows.")
    if len(pts) < 4:
        _unavailable("P/E band", "≥ 7 quarters of positive EPS plus matching price history", T)
        return
    ser = pd.Series([v for _, v in pts], index=[d for d, _ in pts])
    med = float(ser.median())
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ser.index, y=ser.values, mode="lines+markers", name="P/E (TTM)",
                             line=dict(color=T["cyan"], width=2), marker=dict(size=5)))
    fig.add_hline(y=med, line=dict(color=T["amber"], dash="dash", width=1), annotation_text=f"median {med:.1f}x",
                  annotation_font_color=T["amber"])
    if pe:
        fig.add_trace(go.Scatter(x=[close.index[-1]], y=[pe], mode="markers", name="Now",
                                 marker=dict(color=T["amber"], size=10, symbol="diamond")))
    _fig(fig, T, 300, f"{key}_val_pe")
    pctile = float((ser < pe).mean() * 100) if pe else None
    _kpis([("Min P/E", f"{ser.min():.1f}x", str(ser.idxmin())[:7]), ("Median P/E", f"{med:.1f}x", f"{len(ser)} quarters"),
           ("Max P/E", f"{ser.max():.1f}x", str(ser.idxmax())[:7]),
           ("Current vs history", f"{pctile:.0f}th pct" if pctile is not None else "N/A",
            f"Price at median P/E: {_rs(med * ttm)}" if ttm and ttm > 0 else "")], T, 4)


def _financials(sym, B, T, key, nf):
    mode = st.radio("Period", ["Quarterly", "Yearly"], horizontal=True, key=f"{key}_fin_mode", label_visibility="collapsed")
    rec = B.get("quarterly") if mode == "Quarterly" else B.get("yearly")
    if not rec:  # Yahoo fallback
        df = yf_attr(sym, "quarterly_income_stmt" if mode == "Quarterly" else "income_stmt")
        _header(f"{mode} income statement · Yahoo fallback", T, "stale" if df is not None else "unavailable")
        _df_table(df, T, 40, 8, ["Total Revenue", "Net Income", "EBITDA", "Diluted EPS"], dl=f"{sym}_income_{mode.lower()}_{key}")
        return
    _header(f"{mode} results · Screener", T, "live")
    _bar_chart(rec, ["Sales", "Revenue", "Net Profit"], [T["cyan"], T["cyan"], T["green"]], T, f"{key}_fin_bar_{mode}")
    lags, names = ([1, 4], ["QoQ", "YoY"]) if mode == "Quarterly" else ([1, 3], ["YoY", "3Y TOTAL"])
    heads, grows = _growth_rows(rec, ["Sales", "Revenue", "Operating Profit", "Financing Profit", "Net Profit", "EPS"], lags, names)
    if grows:
        _header("Growth (latest vs earlier)", T)
        _table(heads, grows, T, ["Sales", "Revenue", "Net Profit"])
    _header("Full table", T)
    _rec_table(rec, T, ["Sales", "Revenue", "Operating Profit", "Net Profit", "EPS", "OPM"], dl=f"{sym}_results_{mode.lower()}_{key}")


def _balance(sym, B, T, key, nf):
    rec = B.get("balance_sheet")
    if rec:
        _header("Balance sheet · Screener", T, "live")
        _bar_chart(rec, ["Borrowings", "Reserves", "Total Assets"], [T["red"], T["green"], T["cyan"]], T, f"{key}_bs_bar", n=10)
        _rec_table(rec, T, ["Total Assets", "Total Liabilities", "Borrowings", "Reserves"], dl=f"{sym}_balance_sheet_{key}")
    else:
        df = yf_attr(sym, "balance_sheet")
        _header("Balance sheet · Yahoo fallback", T, "stale" if df is not None else "unavailable")
        _df_table(df, T, 50, 6, ["Total Assets", "Total Debt", "Stockholders Equity"], dl=f"{sym}_balance_sheet_{key}")


def _cashflow(sym, B, T, key, nf):
    rec = B.get("cash_flow")
    if rec:
        _header("Cash flow · Screener", T, "live")
        _bar_chart(rec, ["Cash from Operating", "Cash from Investing", "Cash from Financing"], [T["green"], T["red"], T["amber"]], T, f"{key}_cf_bar", n=10)
        _rec_table(rec, T, ["Operating", "Investing", "Financing", "Net Cash"], dl=f"{sym}_cash_flow_{key}")
    else:
        df = yf_attr(sym, "cashflow")
        _header("Cash flow · Yahoo fallback", T, "stale" if df is not None else "unavailable")
        _df_table(df, T, 40, 6, ["Operating Cash Flow", "Free Cash Flow", "Capital Expenditure"], dl=f"{sym}_cash_flow_{key}")


def _ratios(sym, B, T, key, nf):
    s = B.get("summary") or {}
    de = _debt_equity(B)
    _kpis([("Debt / Equity", f"{de}x" if de is not None else "N/A", "Borrowings ÷ (Equity + Reserves)"),
           ("ROCE", f'{s["roce"]}%' if s.get("roce") else "N/A", "Screener"),
           ("ROE", f'{s["roe"]}%' if s.get("roe") else "N/A", "Screener"),
           ("Face Value", f'₹{s["face_value"]}' if s.get("face_value") else "N/A", "Screener")], T, 4)
    rec = B.get("ratios")
    _header("Efficiency ratios · Screener", T, "live" if rec else "unavailable")
    if rec:
        _rec_table(rec, T, ["ROCE", "Cash Conversion"], dl=f"{sym}_ratios_{key}")
    else:
        _unavailable("Efficiency ratios", "Screener 'Ratios' table (not published for banks / NBFCs)", T)


def _shareholding(sym, B, T, key, nf):
    import plotly.graph_objects as go
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
    colors = {"promoters": T["amber"], "fiis": T["cyan"], "diis": T["green"], "government": T["purple"], "public": T["red"]}
    latest = []
    fig = go.Figure()
    for r in rec["rows"]:
        name = r["label"].lower()
        c = next((v for k, v in colors.items() if name.startswith(k)), None)
        if not c:
            continue
        vals = _nums(r)
        fig.add_trace(go.Scatter(x=rec["periods"], y=vals, mode="lines+markers", name=r["label"], line=dict(color=c, width=2), marker=dict(size=4)))
        clean = [v for v in vals if v is not None]
        if clean:
            d = clean[-1] - clean[-2] if len(clean) >= 2 else None
            latest.append((r["label"], f"{clean[-1]:.2f}%", f"{d:+.2f} pp vs prev" if d is not None else "",
                           (T["green"] if d > 0 else T["red"] if d < 0 else None) if d is not None else None))
    if latest:
        _kpis(latest, T, min(len(latest), 5))
    if fig.data:
        _fig(fig, T, 320, f"{key}_sh_chart_{mode}")
    _rec_table(rec, T, ["Promoters", "FIIs", "DIIs"], last_n=14, dl=f"{sym}_shareholding_{mode.lower()}_{key}")


def _peers(sym, B, T, key, nf):
    rec = get_peers_table(sym, tuple(B.get("peer_urls") or ()))
    _header("Peer comparison · Screener", T, "live" if rec else "unavailable")
    if rec:
        _table(rec["periods"], rec["rows"], T, [B.get("name", sym), sym], dl=f"{sym}_peers_{key}")
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


# ---------- PERF: vs benchmark ----------
def _performance(sym, B, T, key, nf):
    import plotly.graph_objects as go
    h = _hist(sym, "2y", "1y")
    if h is None:
        _unavailable("Performance", "Yahoo daily price history", T)
        return
    s = _naive(h["Close"].astype(float)).rename("s")
    ix = _index_history()
    if ix is None:
        _unavailable(f"{BENCHMARK[1]} comparison", f"Yahoo history for {BENCHMARK[0]}", T)
        return
    df = pd.concat([s, _naive(ix["Close"].astype(float)).rename("b")], axis=1).dropna()
    if len(df) < 60:
        _unavailable("Performance", "≥ 60 overlapping trading days", T)
        return
    w = df.tail(252)
    reb = w / w.iloc[0] * 100
    _header(f"Rebased performance · {sym} vs {BENCHMARK[1]} (1Y = 100)", T, "live")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=reb.index, y=reb["s"], name=sym, line=dict(color=T["amber"], width=2)))
    fig.add_trace(go.Scatter(x=reb.index, y=reb["b"], name=BENCHMARK[1], line=dict(color=T["t2"], width=1.5)))
    _fig(fig, T, 300, f"{key}_perf_chart")

    rows = []
    for lab, n in (("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 252), ("2 years", 504)):
        if len(df) > n:
            rs = (df["s"].iloc[-1] / df["s"].iloc[-1 - n] - 1) * 100
            rb = (df["b"].iloc[-1] / df["b"].iloc[-1 - n] - 1) * 100
            rows.append({"label": lab, "values": [f"{rs:+.2f}%", f"{rb:+.2f}%", f"{rs - rb:+.2f} pp", "OUTPERFORM" if rs > rb else "UNDERPERFORM"]})
    _header("Returns vs benchmark", T)
    _table([sym, BENCHMARK[1], "ALPHA", "STATE"], rows, T, ["1 year"])

    r = w.pct_change().dropna()
    var_b = r["b"].var()
    beta = float(r["s"].cov(r["b"]) / var_b) if var_b else None
    corr = float(r["s"].corr(r["b"]))
    vol_s, vol_b = float(r["s"].std() * math.sqrt(252) * 100), float(r["b"].std() * math.sqrt(252) * 100)
    mdd = float((w["s"] / w["s"].cummax() - 1).min() * 100)
    ret1y = float((w["s"].iloc[-1] / w["s"].iloc[0] - 1) * 100)
    _header("Risk (1 year, daily returns)", T)
    _kpis([("Beta", f"{beta:.2f}" if beta is not None else "N/A", ">1 moves more than the index"),
           ("Correlation", f"{corr:.2f}", f"with {BENCHMARK[1]}"),
           ("Volatility (ann.)", f"{vol_s:.1f}%", f"Index {vol_b:.1f}%"),
           ("Max drawdown", f"{mdd:.1f}%", "Peak-to-trough, 1Y", T["red"]),
           ("Return / volatility", f"{ret1y / vol_s:.2f}" if vol_s else "N/A", "1Y return ÷ ann. vol (no risk-free rate)")], T, 5)


def _technicals(sym, B, T, key, nf):
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
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    hist_v = float((macd - sig).iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # Rule-based signal summary
    if sma50 and sma200:
        trend = ("UPTREND", T["green"]) if last > sma50 and last > sma200 else ("DOWNTREND", T["red"]) if last < sma50 and last < sma200 else ("MIXED", T["amber"])
        regime = ("GOLDEN CROSS", T["green"]) if sma50 > sma200 else ("DEATH CROSS", T["red"])
    else:
        trend = regime = ("N/A", T["t3"])
    rsi_z = ("OVERBOUGHT", T["red"]) if rsi >= 70 else ("OVERSOLD", T["green"]) if rsi <= 30 else ("NEUTRAL", T["t2"])
    mom = ("BULLISH", T["green"]) if hist_v > 0 else ("BEARISH", T["red"])
    _header("Signal summary · rule-based, not advice", T, "live")
    _kpis([("Trend (vs SMA 50/200)", trend[0], "Price above both = uptrend", trend[1]),
           ("MA regime", regime[0], "SMA 50 vs SMA 200", regime[1]),
           ("RSI zone", rsi_z[0], f"RSI 14 = {rsi:.1f}", rsi_z[1]),
           ("MACD momentum", mom[0], f"Histogram {hist_v:+.2f}", mom[1])], T, 4)

    _header("Momentum & volatility · computed from Yahoo daily data", T, "live")
    _kpis([("Close", f"₹{last:,.2f}", ""), ("ATR 14", f"₹{atr:,.2f}", f"{atr / last * 100:.2f}% of price"),
           ("Volume buzz", f"{buzz:.2f}x" if buzz == buzz else "N/A", "Today vs 20-day avg"),
           ("MACD / Signal", f"{macd.iloc[-1]:.2f}", f"Signal {sig.iloc[-1]:.2f}"),
           ("52W high", f"₹{high.tail(252).max():,.2f}", f"{(last / high.tail(252).max() - 1) * 100:+.1f}% from high"),
           ("52W low", f"₹{low.tail(252).min():,.2f}", f"{(last / low.tail(252).min() - 1) * 100:+.1f}% above low")], T, 3)

    a, b = st.columns(2)
    with a:
        rows = []
        for n in (10, 20, 50, 100, 200):
            if len(close) >= n:
                m = float(close.rolling(n).mean().iloc[-1])
                rows.append({"label": f"SMA {n}", "values": [f"₹{m:,.2f}", f"{(last / m - 1) * 100:+.2f}%", "ABOVE" if last >= m else "BELOW"]})
        for n in (20, 50):
            m = float(close.ewm(span=n, adjust=False).mean().iloc[-1])
            rows.append({"label": f"EMA {n}", "values": [f"₹{m:,.2f}", f"{(last / m - 1) * 100:+.2f}%", "ABOVE" if last >= m else "BELOW"]})
        _header("Moving averages", T)
        _table(["VALUE", "VS PRICE", "STATE"], rows, T, ["SMA 50", "SMA 200"])
    with b:
        # Classic floor pivots from the last *completed* session
        i = -2 if _market_open() and len(h) >= 2 else -1
        H, L, C = float(high.iloc[i]), float(low.iloc[i]), float(close.iloc[i])
        P = (H + L + C) / 3
        piv = [("R3", H + 2 * (P - L)), ("R2", P + (H - L)), ("R1", 2 * P - L), ("PIVOT", P),
               ("S1", 2 * P - H), ("S2", P - (H - L)), ("S3", L - 2 * (H - P))]
        _header("Pivot levels (classic)", T, note=f"From session {str(h.index[i])[:10]}")
        _table(["LEVEL", "VS PRICE"], [{"label": n, "values": [f"₹{v:,.2f}", f"{(v / last - 1) * 100:+.2f}%"]} for n, v in piv], T, ["PIVOT"])

    ret_rows = [{"label": lab, "values": [f"{(last / float(close.iloc[-1 - n]) - 1) * 100:+.2f}%"]}
                for lab, n in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 252)) if len(close) > n]
    bb_m, bb_s = close.rolling(20).mean().iloc[-1], close.rolling(20).std().iloc[-1]
    c1, c2 = st.columns(2)
    with c1:
        _header("Returns", T)
        _table(["RETURN"], ret_rows, T)
    with c2:
        _header("Bollinger (20, 2)", T)
        _table(["LOWER", "MID", "UPPER"], [{"label": "Band", "values": [f"₹{bb_m - 2 * bb_s:,.2f}", f"₹{bb_m:,.2f}", f"₹{bb_m + 2 * bb_s:,.2f}"]}], T)


# ---------- SEAS: seasonality ----------
def _seasonality(sym, B, T, key, nf):
    import plotly.graph_objects as go
    h = _hist(sym, "5y")
    if h is None or len(h) < 260:
        _unavailable("Seasonality", "≥ 1 year of Yahoo daily history", T)
        return
    c = _naive(h["Close"].astype(float))
    try:
        m = c.resample("ME").last()
    except ValueError:  # pandas < 2.2
        m = c.resample("M").last()
    r = (m.pct_change() * 100).dropna()
    df = pd.DataFrame({"y": r.index.year, "m": r.index.month, "r": r.values})
    tbl = df.pivot(index="y", columns="m", values="r").reindex(columns=range(1, 13))
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    _header(f"Monthly returns heatmap · {len(tbl)} years", T, "live")
    z = tbl.values
    text = [[("" if pd.isna(v) else f"{v:+.1f}") for v in row] for row in z]
    fig = go.Figure(go.Heatmap(z=z, x=months, y=[str(y) for y in tbl.index], text=text, texttemplate="%{text}",
                               colorscale=[[0, T["red"]], [0.5, "#111111"], [1, T["green"]]], zmid=0, showscale=False,
                               hovertemplate="%{y} %{x}: %{z:+.2f}%<extra></extra>"))
    _fig(fig, T, 60 + 34 * len(tbl), f"{key}_seas_heat")
    avg = tbl.mean()
    win = (tbl > 0).sum() / tbl.notna().sum() * 100
    _table(months, [{"label": "Average", "values": [("—" if pd.isna(v) else f"{v:+.2f}%") for v in avg]},
                    {"label": "Win rate", "values": [("—" if pd.isna(v) else f"{v:.0f}%") for v in win]},
                    {"label": "Years", "values": [str(int(n)) for n in tbl.notna().sum()]}], T, ["Average"])
    st.caption("Past seasonality is descriptive only; small samples (≤ 5 years) are noisy.")


def _analyst(sym, B, T, key, nf):
    info = yf_attr(sym, "info") or {}
    tg = yf_attr(sym, "analyst_price_targets") or {}
    _header("Analyst targets · Yahoo Finance", T, "live" if (tg or info.get("targetMeanPrice")) else "unavailable")
    if tg or info.get("targetMeanPrice"):
        g = lambda k, ik: tg.get(k) or info.get(ik)
        f = lambda v: f"₹{v:,.2f}" if isinstance(v, (int, float)) else "N/A"
        mean, cur = g("mean", "targetMeanPrice"), g("current", "currentPrice")
        up = (mean / cur - 1) * 100 if isinstance(mean, (int, float)) and isinstance(cur, (int, float)) and cur else None
        _kpis([("Mean target", f(mean), _pct(up, 1, True) + " upside" if up is not None else "",
                (T["green"] if up and up > 0 else T["red"]) if up is not None else None),
               ("High", f(g("high", "targetHighPrice")), ""), ("Low", f(g("low", "targetLowPrice")), ""),
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


def _dividends(sym, B, T, key, nf):
    d = yf_attr(sym, "dividends")
    has = d is not None and len(d) > 0
    _header("Dividend history · Yahoo Finance", T, "live" if has else "unavailable")
    if not has:
        _unavailable("Dividends", "Yahoo dividend history", T)
    else:
        ttm = float(d[d.index >= (d.index.max() - pd.Timedelta(days=365))].sum())
        hist = get_history(sym, "1y")
        px = float(hist["Close"].iloc[-1]) if hist is not None and len(hist) else None
        _kpis([("Last dividend", f"₹{float(d.iloc[-1]):,.2f}", str(d.index[-1])[:10]),
               ("Trailing 12M", f"₹{ttm:,.2f}", "Sum of payouts"),
               ("TTM yield", _pct(ttm / px * 100 if px else None, 2), "TTM payout ÷ price"),
               ("Payouts on record", str(len(d)), "")], T, 4)
        yearly = d.groupby(pd.to_datetime(d.index).year).sum()
        if len(yearly) >= 2:
            import plotly.graph_objects as go
            fig = go.Figure(go.Bar(x=[str(y) for y in yearly.index], y=yearly.values, marker_color=T["green"], name="₹ / share"))
            _fig(fig, T, 220, f"{key}_dvd_bar")
        _table(["₹ PER SHARE"], [{"label": str(i)[:10], "values": [f"{float(v):,.2f}"]} for i, v in d.iloc[::-1].head(25).items()], T)
    sp = yf_attr(sym, "splits")
    if sp is not None and len(sp) > 0:
        _header("Splits / bonus", T, "live")
        _table(["RATIO"], [{"label": str(i)[:10], "values": [f"{float(v):g} : 1"]} for i, v in sp.iloc[::-1].head(15).items()], T)


def _news(sym, B, T, key, news_fetch_fn):
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
    q = st.text_input("Filter headlines", key=f"{key}_news_q", placeholder="Filter headlines… e.g. results, order, merger",
                      label_visibility="collapsed")
    if q:
        news = [n for n in news if q.lower() in str(n.get("title") or n.get("headline") or "").lower()]
    _header(f"Security news timeline · {len(news)} item(s)", T, "live")
    sent_col = {"positive": T["green"], "bullish": T["green"], "negative": T["red"], "bearish": T["red"]}
    for it in news[:30]:
        title = it.get("title") or it.get("headline") or "Untitled"
        source = it.get("source") or it.get("publisher") or "Source"
        ts = it.get("time_str") or it.get("published") or it.get("published_at") or it.get("time") or ""
        sent = str(it.get("sentiment_label") or it.get("sentiment") or "")
        sc = sent_col.get(sent.lower(), T["t3"])
        url = it.get("url") or it.get("link")
        link = f'<a href="{html.escape(str(url), quote=True)}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(title)}</a>' if url else _esc(title)
        st.markdown(f'<div style="padding:8px 0 8px 10px;border-bottom:1px solid {T["border"]};border-left:2px solid {sc};font:10px {T["mono"]}"><div>{link}</div>'
                    f'<div style="color:{T["t3"]};margin-top:3px">{_esc(source)} · {_esc(ts)} {("· <span style=color:" + sc + ">" + _esc(sent.upper()) + "</span>") if sent else ""}</div></div>',
                    unsafe_allow_html=True)


def _announcements(sym, B, T, key, nf):
    d = get_scanx(sym, B.get("name", ""))
    anns = (d or {}).get("announcements") or []
    _header("Exchange announcements · ScanX (optional source)", T, "live" if anns else "unavailable")
    if not anns:
        _unavailable("Announcements", "Public ScanX announcement section", T, "ScanX is optional; the rest of the terminal does not depend on it.")
        return
    q = st.text_input("Filter filings", key=f"{key}_ann_q", placeholder="Filter filings… e.g. board meeting, dividend, outcome",
                      label_visibility="collapsed")
    if q:
        anns = [a for a in anns if q.lower() in str(a.get("title", "")).lower()]
    for a in anns[:60]:
        url = html.escape(str(a.get("url", "")), quote=True)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><a href="{url}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(a.get("title"))}</a>'
                    f'<div style="color:{T["t3"]};margin-top:3px">OPEN SOURCE DOCUMENT ↗</div></div>', unsafe_allow_html=True)


def _mf(sym, B, T, key, nf):
    d = get_scanx(sym, B.get("name", ""))
    df = ((d or {}).get("tables") or {}).get("mf_holdings")
    api = (d or {}).get("mf_transactions") or {}
    if df is not None and not df.empty:
        _header("Mutual fund holdings · ScanX (optional source)", T, "live")
        cols = list(df.columns)
        _table([str(c) for c in cols[1:]], [{"label": str(r.iloc[0]), "values": [_fmt_dt_cell(v) for v in r.iloc[1:].tolist()]} for _, r in df.head(80).iterrows()],
               T, dl=f"{sym}_mf_{key}")
        return
    rows = api.get("rows") or []
    if rows:
        _header("Mutual fund transactions · ScanX (optional source)", T, "live")
        cols = list(dict.fromkeys(k for r in rows for k in r.keys()))
        _table(cols, [{"label": str(r.get("fundName") or r.get("fund_name") or r.get("scheme") or "Fund"), "values": [str(r.get(c, "")) for c in cols]} for r in rows[:100]],
               T, dl=f"{sym}_mf_{key}")
        return
    _header("Mutual fund holdings · ScanX (optional source)", T, "unavailable")
    _unavailable("MF holdings", "ScanX company page or mftransaction endpoint (needs an ISIN mapping)", T, "Optional source — not required by the rest of the terminal.")


def _thesis(sym, B, T, key, nf):
    a, b, c = st.columns(3)
    a.selectbox("Stance", ["—", "Bullish", "Neutral", "Bearish"], key=f"{key}_note_stance_{sym}")
    b.number_input("Target price (₹)", min_value=0.0, step=1.0, key=f"{key}_note_tp_{sym}")
    c.slider("Conviction", 1, 5, 3, key=f"{key}_note_conv_{sym}")
    fields = [("Bull case", "bull"), ("Bear case", "bear"), ("Catalysts", "cat"), ("Risks", "risk")]
    l, r = st.columns(2)
    for i, (title, k) in enumerate(fields):
        (l if i % 2 == 0 else r).text_area(title, height=120, key=f"{key}_note_{k}_{sym}")
    ss = st.session_state
    head = (f"{sym} RESEARCH NOTE · {datetime.now(IST).strftime('%d %b %Y %H:%M IST')}\n"
            f"Stance: {ss.get(f'{key}_note_stance_{sym}', '—')} · Target: ₹{ss.get(f'{key}_note_tp_{sym}', 0):,.2f} · "
            f"Conviction: {ss.get(f'{key}_note_conv_{sym}', 3)}/5\n")
    txt = head + "\n\n".join(f"{t.upper()}\n{ss.get(f'{key}_note_{k}_{sym}', '')}" for t, k in fields)
    st.download_button("⤓ DOWNLOAD NOTE (.txt)", txt, file_name=f"{sym}_research_note.txt", key=f"{key}_note_dl_{sym}")
    st.caption("Notes live in this browser session. Download them to keep a copy.")


def _sources(sym, B, T, key, nf):
    _header("Data provenance", T)
    _table(["SOURCE", "STATUS"], [
        {"label": "Fundamentals (financials, ratios, holding)", "values": ["Screener.in", "live" if B.get("resolved") else "unavailable"]},
        {"label": "Prices / charts / technicals / seasonality", "values": ["Yahoo Finance", "live"]},
        {"label": f"Benchmark ({BENCHMARK[1]})", "values": [f"Yahoo {BENCHMARK[0]}", "on demand"]},
        {"label": "Health check, valuation, P/E band", "values": ["Computed from the above", "on demand"]},
        {"label": "Analyst / dividends / fallbacks", "values": ["Yahoo Finance", "on demand"]},
        {"label": "News", "values": ["news_feed.py / Yahoo", "on demand"]},
        {"label": "Announcements / MF holdings", "values": ["ScanX (optional)", "on demand"]}], T)
    _panel("Integrity rule", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">Values are shown only when a source returns them '
           f'or when they are computed directly from returned values (formula shown on screen). Missing data says DATA UNAVAILABLE; nothing is estimated or invented.</div>', T)


HANDLERS = {
    "DES": _overview, "HC": _health, "VAL": _valuation,
    "FA": _financials, "BS": _balance, "CF": _cashflow, "RAT": _ratios,
    "HDS": _shareholding, "MF": _mf,
    "RV": _peers, "PERF": _performance,
    "TECH": _technicals, "SEAS": _seasonality, "ANR": _analyst,
    "DVD": _dividends, "CN": _news, "CACS": _announcements,
    "NOTE": _thesis, "SRC": _sources,
}


# ═══════════════════════════ Navigation ═══════════════════════════
def _css(T):
    st.markdown(f"""<style>
    div[class*="st-key-rpnav"] div[data-testid="stVerticalBlock"]{{gap:2px}}
    div[class*="st-key-rpnav"] button{{background:{T["panel"]};border:1px solid {T["border"]};border-left:3px solid transparent;
        border-radius:0;min-height:27px;padding:2px 8px;justify-content:flex-start;width:100%}}
    div[class*="st-key-rpnav"] button:hover{{border-color:{T["amber"]}88;background:{T["panel2"]}}}
    div[class*="st-key-rpnav"] button p{{font:600 10px {T["mono"]} !important;color:{T["t2"]} !important;text-align:left;white-space:nowrap}}
    div[class*="st-key-rpnav"] button[kind="primary"], div[class*="st-key-rpnav"] button[data-testid="stBaseButton-primary"]{{
        background:{T["panel2"]};border-color:{T["amber"]};border-left:3px solid {T["amber"]}}}
    div[class*="st-key-rpnav"] button[kind="primary"] p, div[class*="st-key-rpnav"] button[data-testid="stBaseButton-primary"] p{{color:{T["amber"]} !important}}
    div[class*="st-key-rpcmd"] input{{background:#000 !important;color:{T["amber"]} !important;font:700 12px {T["mono"]} !important;
        border:1px solid {T["amber"]}66 !important;border-radius:0 !important;letter-spacing:.8px}}
    div[class*="st-key-rprecent"] button{{background:{T["panel"]};border:1px solid {T["border"]};border-radius:0;min-height:24px;padding:1px 8px}}
    div[class*="st-key-rprecent"] button p{{font:600 9px {T["mono"]} !important;color:{T["t2"]} !important}}
    </style>""", unsafe_allow_html=True)


def _resolve_code(tok: str):
    tok = tok.upper().strip()
    return tok if tok in FN else ALIASES.get(tok)


def _on_cmd(key):
    ck, fk = f"rpcmd_{key}", f"rp_fn_{key}"
    raw = str(st.session_state.get(ck, "")).upper().replace("<GO>", " ").strip()
    st.session_state[ck] = ""
    st.session_state.pop(f"rp_msg_{key}", None)
    if not raw:
        return
    toks = raw.split()
    code = _resolve_code(toks[-1])
    sym = None
    if code:
        st.session_state[fk] = code
        if len(toks) >= 2:
            sym = toks[0]
    elif len(toks) == 1:
        sym = toks[0]
    else:
        st.session_state[f"rp_msg_{key}"] = f"Unknown function '{toks[-1]}'. Try DES, FA, HC, VAL, TECH …"
        return
    if sym:
        st.session_state.active_security = sym
        st.session_state.research_last_query = sym
        st.session_state[f"rp_jump_{key}"] = True


def _set_fn(key, code):
    st.session_state[f"rp_fn_{key}"] = code


def _command_bar(sym, key, T):
    c1, c2 = st.columns([3, 2])
    with c1:
        with st.container(key=f"rpcmd_wrap_{key}"):
            st.text_input("Command", key=f"rpcmd_{key}", on_change=_on_cmd, args=(key,),
                          placeholder=f"{sym} ▸ type a function (FA, HC, VAL, TECH…) or a symbol + function (TCS FA), then Enter",
                          label_visibility="collapsed")
    with c2:
        msg = st.session_state.get(f"rp_msg_{key}")
        tip = (f'<span style="color:{T["red"]}">{_esc(msg)}</span>' if msg else
               f'<span style="color:{T["t3"]}">SHORTCUTS · </span>' + " ".join(
                   f'<b style="color:{T["t2"]}">{c}</b>' for c in ("DES", "HC", "VAL", "FA", "HDS", "PERF", "TECH")))
        st.markdown(f'<div style="font:10px {T["mono"]};padding-top:9px">{tip}</div>', unsafe_allow_html=True)


def _nav(key, T):
    cur = st.session_state[f"rp_fn_{key}"]
    with st.container(key=f"rpnav_{key}"):
        for grp, items in GROUPS:
            st.markdown(f'<div style="font:700 8px {T["mono"]};color:{T["t3"]};letter-spacing:1.2px;margin:8px 0 2px">{grp}</div>',
                        unsafe_allow_html=True)
            for code, name, _ in items:
                st.button(f"{code} · {name}", key=f"rpb_{key}_{code}", on_click=_set_fn, args=(key, code),
                          type="primary" if code == cur else "secondary", use_container_width=True)


def _title_bar(fn, sym, T):
    name, desc, grp = FN[fn]
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;border:1px solid {T["border"]};border-left:3px solid {T["amber"]};'
        f'background:{T["panel"]};padding:8px 12px;margin:0 0 4px">'
        f'<span style="font:800 12px {T["mono"]};color:{T["amber"]};letter-spacing:1px">{_esc(sym)} {fn} &lt;GO&gt; '
        f'<span style="color:{T["ivory"]}">· {_esc(name).upper()}</span></span>'
        f'<span style="font:9px {T["mono"]};color:{T["t3"]}">{_esc(grp)} · {_esc(desc)}</span></div>', unsafe_allow_html=True)


# ═══════════════════════════ Panel + page ═══════════════════════════
@st.fragment
def _research_fragment(symbol, T, news_fetch_fn, key, show_quote):
    fk = f"rp_fn_{key}"
    if st.session_state.get(fk) not in FN:
        st.session_state[fk] = "DES"
    if st.session_state.pop(f"rp_jump_{key}", False):
        st.rerun(scope="app")  # symbol changed from the command bar → redraw chart + header too

    with st.spinner("Loading fundamentals…"):
        B = get_screener_bundle(symbol) or {"resolved": False, "summary": {}, "growth": {}}
    _snapshot(symbol, B, T, show_quote)
    _command_bar(symbol, key, T)

    nav, body = st.columns([1.05, 5], gap="small")
    with nav:
        _nav(key, T)
    with body:
        fn = st.session_state[fk]
        _title_bar(fn, symbol, T)
        try:
            HANDLERS[fn](symbol, B, T, key, news_fetch_fn)
        except Exception as e:  # one broken section must never take down the terminal
            _unavailable(FN[fn][0], "Section renderer", T, f"Error while rendering: {type(e).__name__}: {e}")


def render_research_panel(symbol, T=None, news_fetch_fn=None, key="rp", show_quote=True):
    """Reusable research panel. Call from the Security workspace or the Research page."""
    T = _theme(T)
    sym = str(symbol or "").upper().strip()
    if not sym:
        return
    _css(T)
    _research_fragment(sym, T, news_fetch_fn, key, show_quote)


def _remember(sym):
    r = [s for s in st.session_state.get("research_recent", []) if s != sym]
    st.session_state.research_recent = ([sym] + r)[:8]


def _load_symbol(sym):
    st.session_state.active_security = sym
    st.session_state.research_last_query = sym


def render_research_page(T=None, news_fetch_fn=None):
    T = _theme(T)
    _css(T)
    now = datetime.now(IST)
    opened = _market_open(now)
    color = T["green"] if opened else T["red"]
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;border:1px solid {T["border"]};border-bottom:2px solid {T["amber"]};background:{T["panel"]};padding:7px 12px;margin-bottom:10px;font:10px {T["mono"]}">'
        f'<span style="color:{T["amber"]};font-weight:800;letter-spacing:1px">ARKA TERMINAL · RESEARCH</span>'
        f'<span style="color:{color};font-weight:700">● {"MARKET OPEN" if opened else "MARKET CLOSED"}</span>'
        f'<span style="color:{T["t3"]}">{now.strftime("%d %b %Y · %H:%M IST")}</span></div>', unsafe_allow_html=True)

    active = st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    with st.form("research_load_form", clear_on_submit=False):
        c1, c2 = st.columns([5, 1])
        q = c1.text_input("Research security", value=active, placeholder="Symbol: RELIANCE / HDFCBANK / TCS",
                          label_visibility="collapsed", key=f"research_q_{active}")
        go_ = c2.form_submit_button("LOAD", type="primary", use_container_width=True)
    if go_ and q.strip():
        _load_symbol(q.strip().upper())
        st.rerun()

    recent = [s for s in st.session_state.get("research_recent", []) if s != active]
    if recent:
        with st.container(key="rprecent"):
            cols = st.columns([0.6] + [1] * len(recent) + [max(1, 9 - len(recent))])
            cols[0].markdown(f'<div style="font:700 8px {T["mono"]};color:{T["t3"]};letter-spacing:1px;padding-top:7px">RECENT</div>', unsafe_allow_html=True)
            for c, s in zip(cols[1:], recent):
                c.button(s, key=f"rprecent_{s}", on_click=_load_symbol, args=(s,), use_container_width=True)

    symbol = st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    if not symbol:
        _panel("Research ready", f'<div style="font:11px {T["mono"]};color:{T["t3"]};line-height:1.9">Load a security to open its research workspace.<br>'
               f'Tip: once loaded, type function codes like <b style="color:{T["amber"]}">FA</b>, <b style="color:{T["amber"]}">HC</b> or '
               f'<b style="color:{T["amber"]}">TCS VAL</b> into the command bar.</div>', T, dashed=True)
        return
    _remember(symbol)

    hist = get_history(symbol, "1y")
    B = get_screener_bundle(symbol) or {}
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

    with st.expander("CHART", expanded=True):
        try:
            from chart_engine import render_pro_chart
            render_pro_chart(symbol, height=560)
        except Exception as e:  # chart must never break the research page
            st.caption(f"Chart unavailable: {e}")

    render_research_panel(symbol, T, news_fetch_fn, key="rp_page", show_quote=False)
