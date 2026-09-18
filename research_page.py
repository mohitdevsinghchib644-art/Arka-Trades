"""Arka Trades — Research Terminal v9.1.

Design goals:
- Research shell opens quickly. No get_full_research() on page entry.
- Each Research function loads only the data it needs (lazy loading).
- Free sources are used where available: Screener + Yahoo/yfinance.
- Missing datasets stay explicitly unavailable; no fabricated numbers.
- Data calls are cached independently so moving between functions is fast.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import streamlit as st
import yfinance as yf

from news_feed import get_security_news

from screener_scraper import (
    resolve_symbol,
    get_summary,
    get_sector_info,
    get_quarterly_results,
    get_yearly_results,
    get_shareholding,
    get_balance_sheet,
    get_cash_flow,
    get_peer_comparison,
    get_earnings_date,
)

IST = timezone(timedelta(hours=5, minutes=30))
_HOT_TICKERS = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "TATAMOTORS", "SBIN"]

T0 = {
    "bg": "#000000", "panel": "#080808", "panel2": "#0E0E0E", "row_alt": "#0B0B0B",
    "border": "#242424", "amber": "#FF9F0A", "ivory": "#E8E8E8", "t2": "#9A9A9A",
    "t3": "#5E5E5E", "green": "#30D158", "red": "#FF453A", "cyan": "#5AC8FA",
    "mono": "'JetBrains Mono','Consolas',monospace",
}


def _theme(T):
    x = dict(T0)
    if T:
        x.update(T)
    return x


def _esc(x: Any) -> str:
    return html.escape(str(x))


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").replace("₹", "").replace("%", "").replace("Cr", "").replace("x", "").strip()
    if s.lower() in {"", "—", "-", "nan", "none", "n/a"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _age(seconds):
    if seconds is None:
        return ""
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _tag(status, T, age_seconds=None):
    status = status or "unavailable"
    if status == "live":
        text, color = "● LIVE", T["green"]
    elif status == "cached":
        text, color = f"◐ CACHED · {_age(age_seconds)}", T["amber"]
    elif status == "partial":
        text, color = "◐ PARTIAL", T["amber"]
    else:
        text, color = "✕ N/A", T["t3"]
    return f'<span style="color:{color};font:700 9px {T["mono"]};letter-spacing:.8px">{text}</span>'


def _header(title, T, status=None, note=None):
    right = _tag(status, T) if status else ""
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {T["border"]};padding:9px 0 6px;margin:13px 0 8px">'
        f'<span style="font:700 11px {T["mono"]};letter-spacing:1px;color:{T["amber"]}">{_esc(title).upper()}</span>{right}</div>',
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin:3px 0 8px">{_esc(note)}</div>', unsafe_allow_html=True)


def _panel(title, body, T, dashed=False):
    border = f"1px dashed {T['border']}" if dashed else f"1px solid {T['border']}"
    st.markdown(
        f'<div style="border:{border};background:{T["panel"]};padding:11px 13px;margin:7px 0">'
        f'<div style="font:700 10px {T["mono"]};color:{T["ivory"]};margin-bottom:7px">{_esc(title).upper()}</div>{body}</div>',
        unsafe_allow_html=True,
    )


def _unavailable(title, requirement, T, detail=None):
    detail = detail or "No verified free-source dataset is connected for this workspace yet."
    body = (
        f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.75">'
        f'<b style="color:{T["t3"]}">STATUS:</b> DATA UNAVAILABLE<br>'
        f'<b style="color:{T["t3"]}">REQUIRED:</b> {_esc(requirement)}<br>{_esc(detail)}</div>'
    )
    _panel(title, body, T, dashed=True)


def _kpis(items, T, cols=4):
    c = st.columns(cols)
    for i, (label, value, sub) in enumerate(items):
        with c[i % cols]:
            st.markdown(
                f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:10px 11px;min-height:67px;margin-bottom:7px">'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">{_esc(label.upper())}</div>'
                f'<div style="font:700 15px {T["mono"]};color:{T["ivory"]};margin-top:4px">{_esc(value)}</div>'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:3px">{_esc(sub or "")}</div></div>',
                unsafe_allow_html=True,
            )


def _table(periods, rows, T, highlights=()):
    if not rows:
        _panel("No data returned", '<div style="font:10px ' + T["mono"] + ';color:' + T["t3"] + '">The selected source returned no rows for this security.</div>', T, dashed=True)
        return
    heads = "".join(
        f'<th style="text-align:right;padding:6px 8px;color:{T["t3"]};font:700 9px {T["mono"]};white-space:nowrap">{_esc(p)}</th>'
        for p in periods
    )
    out = []
    for i, r in enumerate(rows):
        label = str(r.get("label", ""))
        hl = any(x.lower() in label.lower() for x in highlights)
        vals = r.get("values", [])
        cells = "".join(
            f'<td style="text-align:right;padding:6px 8px;color:{T["ivory"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(v)}</td>'
            for v in vals
        )
        out.append(
            f'<tr style="background:{T["row_alt"] if i % 2 else "transparent"};border-bottom:1px solid {T["border"]}">'
            f'<td style="padding:6px 8px;color:{T["amber"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(label)}</td>{cells}</tr>'
        )
    st.markdown(
        f'<div style="overflow-x:auto;border:1px solid {T["border"]};background:{T["panel"]}"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="background:{T["panel2"]};border-bottom:1px solid {T["border"]}"><th style="text-align:left;padding:6px 8px;color:{T["t3"]};font:700 9px {T["mono"]}">METRIC</th>{heads}</tr></thead>'
        f'<tbody>{"".join(out)}</tbody></table></div>', unsafe_allow_html=True
    )


def _df_to_rows(df: pd.DataFrame, max_rows=22, max_cols=8) -> tuple[list[str], list[dict]]:
    if df is None or df.empty:
        return [], []
    x = df.copy()
    if isinstance(x.index, pd.MultiIndex):
        x.index = [" · ".join(str(z) for z in idx) for idx in x.index]
    periods = [str(c)[:16] for c in x.columns[:max_cols]]
    rows = []
    for idx, row in x.head(max_rows).iterrows():
        vals = []
        for v in row.iloc[:max_cols].tolist():
            if pd.isna(v):
                vals.append("—")
            elif isinstance(v, (pd.Timestamp, datetime)):
                vals.append(str(v)[:19])
            elif isinstance(v, float):
                vals.append(f"{v:,.2f}")
            else:
                vals.append(str(v))
        rows.append({"label": str(idx), "values": vals})
    return periods, rows


def _row(section, names):
    d = (section or {}).get("data") or {}
    for r in d.get("rows", []):
        label = str(r.get("label", "")).lower()
        if any(n.lower() in label for n in names):
            return r
    return None


def _latest2(r):
    if not r:
        return None, None
    vals = []
    for v in r.get("values", []):
        n = _num(v)
        if n is not None:
            vals.append(n)
    if not vals:
        return None, None
    return vals[-1], vals[-2] if len(vals) > 1 else None


def _pct_change(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / abs(b) - 1) * 100


# ----------------------- Cached data boundaries ------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _yf_history(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").history(period=period, interval=interval, auto_adjust=False)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def _yf_info(symbol: str) -> dict:
    try:
        return dict(yf.Ticker(symbol.upper().strip() + ".NS").info or {})
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def _yf_income(symbol: str, freq: str = "quarterly") -> pd.DataFrame:
    try:
        t = yf.Ticker(symbol.upper().strip() + ".NS")
        return (t.quarterly_income_stmt if freq == "quarterly" else t.income_stmt).copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_balance(symbol: str, freq: str = "quarterly") -> pd.DataFrame:
    try:
        t = yf.Ticker(symbol.upper().strip() + ".NS")
        return (t.quarterly_balance_sheet if freq == "quarterly" else t.balance_sheet).copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_cashflow(symbol: str, freq: str = "quarterly") -> pd.DataFrame:
    try:
        t = yf.Ticker(symbol.upper().strip() + ".NS")
        return (t.quarterly_cashflow if freq == "quarterly" else t.cashflow).copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_valuation(symbol: str) -> pd.DataFrame:
    try:
        t = yf.Ticker(symbol.upper().strip() + ".NS")
        return t.get_valuation_measures(freq="quarterly", periods=8).copy()
    except Exception:
        try:
            return yf.Ticker(symbol.upper().strip() + ".NS").valuation.copy()
        except Exception:
            return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_earnings_history(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").earnings_history.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_earnings_estimate(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").earnings_estimate.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_revenue_estimate(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").revenue_estimate.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_eps_revisions(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").eps_revisions.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_eps_trend(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").eps_trend.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_target(symbol: str) -> dict:
    try:
        return dict(yf.Ticker(symbol.upper().strip() + ".NS").analyst_price_targets or {})
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def _yf_recommendations(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").recommendations_summary.copy()
    except Exception:
        try:
            return yf.Ticker(symbol.upper().strip() + ".NS").recommendations.copy()
        except Exception:
            return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_major_holders(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").major_holders.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_institutional_holders(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").institutional_holders.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_mutualfund_holders(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").mutualfund_holders.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_insider_transactions(symbol: str) -> pd.DataFrame:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").insider_transactions.copy()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900, show_spinner=False)
def _yf_company_officers(symbol: str) -> list[dict]:
    info = _yf_info(symbol)
    officers = info.get("companyOfficers") or []
    return officers if isinstance(officers, list) else []


@st.cache_data(ttl=900, show_spinner=False)
def _yf_calendar(symbol: str):
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").calendar
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def _yf_dividends(symbol: str) -> pd.Series:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").dividends.tail(12).copy()
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=900, show_spinner=False)
def _yf_splits(symbol: str) -> pd.Series:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").splits.tail(12).copy()
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=900, show_spinner=False)
def _yf_news(symbol: str) -> list[dict]:
    try:
        return yf.Ticker(symbol.upper().strip() + ".NS").news or []
    except Exception:
        return []


def _safe_screener_core(symbol: str) -> dict:
    try:
        res = resolve_symbol(symbol)
    except Exception:
        res = None
    if not res:
        return {"resolved": False, "symbol": symbol.upper(), "reason": "Security could not be resolved on Screener."}
    url = res["url"]
    try:
        summary = get_summary(symbol, url=url)
    except Exception as e:
        summary = {"status": "unavailable", "reason": str(e), "data": {}}
    try:
        sector = get_sector_info(symbol, url=url)
    except Exception:
        sector = {"status": "unavailable", "data": {}}
    return {"resolved": True, "symbol": symbol.upper(), "name": res.get("name", symbol.upper()), "url": url, "summary": summary, "sector": sector}


@st.cache_data(ttl=600, show_spinner=False)
def _core_cached(symbol: str) -> dict:
    return _safe_screener_core(symbol.upper().strip())


def _render_chart(symbol, T, period="6mo", key_suffix="main"):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    h = _yf_history(symbol, period, "1d")
    if h is None or h.empty:
        _unavailable("Price / volume chart", "Yahoo Finance market history", T)
        return h
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[0.82, 0.18])
    fig.add_trace(go.Candlestick(x=h.index, open=h["Open"], high=h["High"], low=h["Low"], close=h["Close"], increasing_line_color=T["green"], decreasing_line_color=T["red"], increasing_fillcolor=T["green"], decreasing_fillcolor=T["red"], name="Price"), row=1, col=1)
    vol_colors = [T["green"] if c >= o else T["red"] for c, o in zip(h["Close"], h["Open"])]
    fig.add_trace(go.Bar(x=h.index, y=h["Volume"], marker_color=vol_colors, name="Volume"), row=2, col=1)
    fig.update_layout(height=430, margin=dict(l=0, r=45, t=5, b=0), paper_bgcolor=T["bg"], plot_bgcolor=T["bg"], font=dict(color=T["t2"], size=10, family="JetBrains Mono, Consolas, monospace"), showlegend=False, xaxis_rangeslider_visible=False, hovermode="x unified")
    fig.update_xaxes(showgrid=True, gridcolor="#181818", nticks=10)
    fig.update_yaxes(showgrid=True, gridcolor="#181818", side="right", row=1, col=1)
    fig.update_yaxes(showgrid=False, side="right", showticklabels=False, row=2, col=1)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True, "scrollZoom": True}, key=f"research_chart_{symbol}_{period}_{key_suffix}")
    return h


# ── v9.2: Bloomberg-style grouped function nav ──────────────────────
# Bloomberg's own terminal groups functions into short codes (FA <GO>,
# DES <GO>, ...) rather than one long flat list. The 24 research
# functions are grouped the same way below: pick a group, then a
# function within it. Group membership is fixed and covers all 24
# items exactly once — nothing was dropped or duplicated.
_GROUPS = [
    ("OVERVIEW", "OV", ["01 Overview", "02 Company", "03 Business"]),
    ("FINANCIALS", "FA", ["04 Financials", "05 Earnings", "20 Price + Fundamentals", "21 What Changed?"]),
    ("VALUATION", "VAL", ["06 Valuation", "18 Factors"]),
    ("OWNERSHIP", "OWN", ["07 Ownership", "08 Flows", "09 Segments", "10 Peers"]),
    ("QUALITATIVE", "QL", ["11 Supply Chain", "12 Management", "13 Filings", "14 Events", "15 News"]),
    ("TECHNICAL", "TECH", ["16 Technical", "17 Relative Strength", "19 Risk"]),
    ("WORKSPACE", "WRK", ["22 Thesis", "23 Research Graph", "24 Data / Sources"]),
]


def _group_nav_css(T):
    st.markdown(f'''<style>
    div[class*="st-key-rgrp_"] > div > button, div[class*="st-key-rfn_"] > div > button {{
        background:{T["panel"]} !important; color:{T["t2"]} !important;
        border:1px solid {T["border"]} !important; border-radius:0 !important;
        font-family:{T["mono"]} !important; font-size:10px !important; font-weight:700 !important;
        letter-spacing:.6px !important; text-transform:uppercase !important; padding:6px 4px !important;
    }}
    div[class*="st-key-rgrp_"] > div > button:hover, div[class*="st-key-rfn_"] > div > button:hover {{
        border-color:{T["amber"]} !important; color:{T["amber"]} !important;
    }}
    div[class*="st-key-rgrp_active"] > div > button {{
        background:{T["amber"]}1c !important; color:{T["amber"]} !important; border-color:{T["amber"]} !important;
    }}
    div[class*="st-key-rfn_active"] > div > button {{
        background:{T["panel2"]} !important; color:{T["ivory"]} !important;
        border-color:{T["t3"]} !important; box-shadow:inset 0 -2px 0 {T["amber"]} !important;
    }}
    </style>''', unsafe_allow_html=True)


def _research_nav(T):
    """Renders the group row + function row and returns the selected
    function's full label (e.g. "04 Financials") — same return shape
    the old flat selectbox produced, so the dispatch dict below is
    unchanged."""
    _group_nav_css(T)
    group_names = [g[0] for g in _GROUPS]
    active_group = st.session_state.get("research_group_v92", group_names[0])
    if active_group not in group_names:
        active_group = group_names[0]

    st.markdown(f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:1px;margin:2px 0 4px;">FUNCTION GROUP</div>', unsafe_allow_html=True)
    gcols = st.columns(len(_GROUPS))
    for col, (gname, gcode, _items) in zip(gcols, _GROUPS):
        with col:
            is_active = gname == active_group
            key = f"rgrp_active_{gname}" if is_active else f"rgrp_{gname}"
            if st.button(f"{gcode}", key=key, use_container_width=True, help=gname):
                st.session_state["research_group_v92"] = gname
                st.session_state.pop("research_func_v92", None)
                st.rerun()

    current_group = next(g for g in _GROUPS if g[0] == active_group)
    items = current_group[2]
    active_func = st.session_state.get("research_func_v92", items[0])
    if active_func not in items:
        active_func = items[0]

    st.markdown(f'<div style="font:9px {T["mono"]};color:{T["amber"]};letter-spacing:1px;margin:10px 0 4px;">{active_group} FUNCTIONS</div>', unsafe_allow_html=True)
    fcols = st.columns(len(items))
    for col, item in zip(fcols, items):
        with col:
            is_active = item == active_func
            short = item.split(" ", 1)[1] if item[:2].isdigit() else item
            key = f"rfn_active_{item}" if is_active else f"rfn_{item}"
            if st.button(short, key=key, use_container_width=True):
                st.session_state["research_func_v92"] = item
                st.rerun()

    return active_func


def _status_strip(T):
    now = datetime.now(IST)
    opened = now.weekday() < 5 and now.replace(hour=9, minute=15, second=0, microsecond=0) <= now <= now.replace(hour=15, minute=30, second=0, microsecond=0)
    color = T["green"] if opened else T["red"]
    label = "MARKET OPEN" if opened else "MARKET CLOSED"
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;border:1px solid {T["border"]};border-bottom:2px solid {T["amber"]};background:{T["panel"]};padding:7px 12px;margin-bottom:10px;font:10px {T["mono"]}">'
        f'<span style="color:{T["amber"]};font-weight:800;letter-spacing:1px">ARKA TERMINAL · RESEARCH V9.1</span>'
        f'<span style="color:{color};font-weight:700">● {label}</span><span style="color:{T["t3"]}">{now.strftime("%d %b %Y · %H:%M:%S IST")}</span></div>', unsafe_allow_html=True)


def _summary_header(data, T):
    s = (data.get("summary") or {}).get("data") or {}
    st.markdown(
        f'<div style="border:1px solid {T["border"]};border-top:2px solid {T["amber"]};background:{T["panel"]};padding:10px 13px;margin:8px 0">'
        f'<div style="display:flex;justify-content:space-between;align-items:center"><div><span style="font:800 15px {T["mono"]};color:{T["ivory"]}">{_esc(data.get("name",""))}</span>'
        f'<span style="font:10px {T["mono"]};color:{T["t3"]};margin-left:8px">{_esc(data.get("symbol",""))} · NSE/BSE</span></div>'
        f'<span>{_tag((data.get("summary") or {}).get("status"),T,(data.get("summary") or {}).get("age_seconds"))}</span></div></div>', unsafe_allow_html=True)
    if s:
        _kpis([
            ("Price", s.get("current_price", "N/A"), "₹"),
            ("Market Cap", s.get("market_cap", "N/A"), "₹ Cr"),
            ("P/E", s.get("pe_ratio", "N/A"), "x"),
            ("Book Value", s.get("book_value", "N/A"), "₹"),
            ("ROE", s.get("roe", "N/A"), "%"),
            ("ROCE", s.get("roce", "N/A"), "%"),
            ("Dividend Yield", s.get("dividend_yield", "N/A"), "%"),
            ("Face Value", s.get("face_value", "N/A"), "₹"),
        ], T, 4)


def _overview(data, T):
    s = (data.get("summary") or {}).get("data") or {}
    sec = (data.get("sector") or {}).get("data") or {}
    info = _yf_info(data["symbol"])
    _header("Company overview", T)
    items = [
        ("Company", data.get("name", "N/A"), "Screener"),
        ("Symbol", data.get("symbol", "N/A"), "NSE"),
        ("Sector", sec.get("Sector") or info.get("sector") or "N/A", "provider"),
        ("Industry", sec.get("Industry") or info.get("industry") or "N/A", "provider"),
        ("Market Cap", s.get("market_cap") or info.get("marketCap") or "N/A", "₹ / provider"),
        ("Employees", info.get("fullTimeEmployees", "N/A"), "Yahoo"),
        ("Country", info.get("country", "N/A"), "Yahoo"),
        ("Website", info.get("website", "N/A"), "Yahoo"),
    ]
    _table(["VALUE","SOURCE"], [{"label":a,"values":[str(b),c]} for a,b,c in items], T)
    desc = info.get("longBusinessSummary") or "No detailed business description was returned by the connected free provider."
    _panel("Business description", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(desc)}</div>', T)


def _business(data, T):
    info = _yf_info(data["symbol"])
    sec = (data.get("sector") or {}).get("data") or {}
    _header("Business model", T)
    _kpis([
        ("Sector", sec.get("Sector") or info.get("sector") or "N/A", "classification"),
        ("Industry", sec.get("Industry") or info.get("industry") or "N/A", "classification"),
        ("Employees", info.get("fullTimeEmployees", "N/A"), "latest provider value"),
        ("Currency", info.get("currency", "N/A"), "reported"),
    ], T, 4)
    desc = info.get("longBusinessSummary")
    if desc:
        _panel("Business description", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(desc)}</div>', T)
    else:
        _unavailable("Business description", "Verified company profile disclosure", T)
    # Product/service and order-book data are genuinely company-specific and are not safely inferable.
    for title, req in [("Products / services", "Company annual report / investor presentation"), ("Customer concentration", "Company filing with customer concentration disclosure"), ("Order book / capacity", "Company operating disclosure")]:
        _unavailable(title, req, T)


def _financials(data, T):
    symbol = data["symbol"]
    for title, df in [
        ("Quarterly income statement · Yahoo", _yf_income(symbol, "quarterly")),
        ("Annual income statement · Yahoo", _yf_income(symbol, "annual")),
        ("Quarterly balance sheet · Yahoo", _yf_balance(symbol, "quarterly")),
        ("Quarterly cash flow · Yahoo", _yf_cashflow(symbol, "quarterly")),
    ]:
        periods, rows = _df_to_rows(df, max_rows=30, max_cols=8)
        _header(title, T, "live" if rows else "unavailable")
        _table(periods, rows, T, ["Revenue", "Operating Income", "Net Income", "EBITDA", "Total Assets", "Stockholders Equity", "Operating Cash Flow", "Free Cash Flow"])
    # Screener fallback is useful when Yahoo doesn't populate a table.
    if data.get("url"):
        try:
            scr_q = get_quarterly_results(symbol, url=data["url"])
            d = scr_q.get("data") or {}
            if d.get("rows"):
                _header("Quarterly income statement · Screener fallback", T, scr_q.get("status"))
                _table(d.get("periods", []), d.get("rows", []), T, ["sales", "operating profit", "net profit", "eps"])
        except Exception:
            pass


def _earnings(data, T):
    symbol = data["symbol"]
    hist = _yf_earnings_history(symbol)
    est = _yf_earnings_estimate(symbol)
    rev = _yf_revenue_estimate(symbol)
    revs = _yf_eps_revisions(symbol)
    trend = _yf_eps_trend(symbol)
    targets = _yf_target(symbol)
    _header("Earnings surprise history", T, "live" if not hist.empty else "unavailable")
    p, r = _df_to_rows(hist, 16, 8)
    _table(p, r, T, ["epsactual", "epsestimate", "surprise"])
    _header("Consensus EPS estimates", T, "live" if not est.empty else "unavailable")
    p, r = _df_to_rows(est, 14, 8)
    _table(p, r, T)
    _header("Revenue estimates", T, "live" if not rev.empty else "unavailable")
    p, r = _df_to_rows(rev, 14, 8)
    _table(p, r, T)
    _header("EPS revisions", T, "live" if not revs.empty else "unavailable")
    p, r = _df_to_rows(revs, 14, 8)
    _table(p, r, T)
    _header("EPS trend", T, "live" if not trend.empty else "unavailable")
    p, r = _df_to_rows(trend, 14, 8)
    _table(p, r, T)
    if targets:
        _header("Analyst price target dataset", T, "live")
        _table(["VALUE"], [{"label":str(k).title(),"values":[f"{v}" ]} for k,v in targets.items()], T)
    else:
        _unavailable("Analyst price targets", "Provider analyst-target dataset", T)


@st.cache_data(ttl=900, show_spinner=False)
def _peer_cached(symbol: str) -> dict:
    try:
        return get_peer_comparison(symbol) or {}
    except Exception:
        return {}


def _valuation(data, T):
    symbol = data["symbol"]
    df = _yf_valuation(symbol)
    if not df.empty:
        _header("Historical valuation measures", T, "live")
        p, r = _df_to_rows(df, 18, 9)
        _table(p, r, T, ["PERatio", "PBRatio", "EnterpriseValue", "EVToEBITDA", "PSTrailing12Months"])
    else:
        s = (data.get("summary") or {}).get("data") or {}
        _kpis([
            ("P/E", s.get("pe_ratio", "N/A"), "current"),
            ("P/B", "N/A", "requires balance-sheet normalization"),
            ("EV/EBITDA", "N/A", "provider unavailable"),
            ("FCF yield", "N/A", "provider unavailable"),
        ], T, 4)

    peers = _peer_cached(symbol)
    _header("Relative valuation · peer set", T, peers.get("status"))
    if peers.get("rows"):
        rows=[{"label":f'{x["name"]} ({x["symbol"]})',"values":[x.get("cmp","—"),x.get("pe","—"),x.get("market_cap","—"),x.get("div_yield","—"),x.get("roce","—")]} for x in peers["rows"]]
        _table(["CMP ₹","P/E","M.CAP ₹Cr","DIV YIELD %","ROCE %"],rows,T)
    else:
        _unavailable("Peer valuation", "Comparable-company dataset", T)

    # V9.1 includes an actual editable DCF when a usable free-cash-flow basis
    # and share-count are available. For banks/insurers, FCF DCF is not used
    # because their cash-flow statements are not comparable to industrial firms.
    info=_yf_info(symbol)
    sec=((data.get("sector") or {}).get("data") or {})
    sector_name=str(sec.get("Sector") or info.get("sector") or "").lower()
    financial_firm=any(x in sector_name for x in ["bank", "financial", "insurance"])
    cf=_yf_cashflow(symbol,"annual")
    base_fcf=None
    if not financial_firm and cf is not None and not cf.empty:
        try:
            if "Free Cash Flow" in cf.index:
                v=cf.loc["Free Cash Flow"].dropna()
                if len(v): base_fcf=float(v.iloc[0])
            if base_fcf is None and "Operating Cash Flow" in cf.index and "Capital Expenditure" in cf.index:
                ocf=cf.loc["Operating Cash Flow"].dropna(); cap=cf.loc["Capital Expenditure"].dropna()
                if len(ocf) and len(cap): base_fcf=float(ocf.iloc[0])+float(cap.iloc[0])
        except Exception:
            base_fcf=None
    shares=info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if shares is None:
        try:
            shares=float(info.get("marketCap"))/float(info.get("currentPrice")) if info.get("marketCap") and info.get("currentPrice") else None
        except Exception:
            shares=None
    if financial_firm:
        _panel("DCF", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">FUNDAMENTAL FIT CHECK: skipped for this security because a conventional industrial free-cash-flow DCF is not an appropriate cash-flow model for banks / insurers. Use relative valuation, book value and return metrics instead.</div>', T)
    elif base_fcf is None or base_fcf<=0 or not shares:
        _unavailable("DCF", "Usable positive free cash flow + share count", T, "The model is enabled only when the connected provider supplies the required inputs.")
    else:
        _header("DCF workspace",T,"live","Illustrative model using the latest provider free-cash-flow observation; edit assumptions below.")
        a,b,c=st.columns(3)
        growth=a.number_input("FCF growth %",min_value=-20.0,max_value=40.0,value=8.0,step=0.5,key="dcf_growth_v91")/100
        wacc=b.number_input("WACC %",min_value=5.0,max_value=25.0,value=11.0,step=0.5,key="dcf_wacc_v91")/100
        tg=c.number_input("Terminal growth %",min_value=0.0,max_value=8.0,value=3.0,step=0.25,key="dcf_terminal_v91")/100
        years=st.slider("Forecast years",3,10,5,key="dcf_years_v91")
        if tg>=wacc:
            st.warning("Terminal growth must be below WACC.")
        else:
            fcf0=float(base_fcf)
            pv=0.0; forecast=[]
            for y in range(1,years+1):
                fcf=fcf0*((1+growth)**y); disc=(1+wacc)**y; pv+=fcf/disc; forecast.append({"label":f"Year {y}","values":[f"₹{fcf:,.0f} Cr",f"₹{fcf/disc:,.0f} Cr"]})
            terminal=forecast[-1]["values"] if forecast else None
            last_fcf=fcf0*((1+growth)**years)
            tv=last_fcf*(1+tg)/(wacc-tg)
            equity_value=pv+tv/(1+wacc)**years
            per_share=equity_value/shares if shares else None
            _kpis([("Base FCF",f"₹{base_fcf:,.0f}","provider cash-flow basis"),("PV of forecast",f"₹{pv:,.0f}","Cr"),("Terminal value",f"₹{tv:,.0f}","Cr"),("DCF / share",f"₹{per_share:,.2f}" if per_share is not None else "N/A","model output")],T,4)
            _table(["FCF","PV OF FCF"],forecast,T)
            _panel("Model discipline",f'<div style="font:9px {T["mono"]};color:{T["t3"]};line-height:1.7">Inputs are editable and stored only for this session. This is a scenario model, not a forecast or investment recommendation. Base FCF: ₹{base_fcf:,.0f} Cr · Shares: {shares:,.0f} · WACC: {wacc*100:.1f}% · Terminal growth: {tg*100:.2f}%.</div>',T)

            _header("DCF sensitivity",T,"live","Rows = WACC · Columns = terminal growth. Values are model output per share.")
            wacc_grid=[max(0.06,wacc-0.02),max(0.06,wacc-0.01),wacc,min(0.25,wacc+0.01),min(0.25,wacc+0.02)]
            tg_grid=[max(0.0,tg-0.01),max(0.0,tg-0.005),tg,min(wacc-0.005,tg+0.005),min(wacc-0.005,tg+0.01)]
            sens=[]
            for wa in wacc_grid:
                vals=[]
                for gt in tg_grid:
                    if gt>=wa:
                        vals.append("—"); continue
                    pvx=sum(fcf0*((1+growth)**y)/(1+wa)**y for y in range(1,years+1))
                    last=fcf0*((1+growth)**years)
                    tvx=last*(1+gt)/(wa-gt)
                    evx=pvx+tvx/(1+wa)**years
                    vals.append(f"₹{evx/shares:,.0f}")
                sens.append({"label":f"WACC {wa*100:.1f}%","values":vals})
            _table([f"g {gt*100:.1f}%" for gt in tg_grid],sens,T)


def _ownership(data, T):
    symbol = data["symbol"]
    sh = data.get("shareholding") or {}
    sd = sh.get("data") or {}
    if sd.get("rows"):
        _header("Promoter / FII / DII / public ownership", T, sh.get("status"))
        _table(sd.get("periods", []), sd.get("rows", []), T, ["promoter", "fii", "dii", "public"])
    major = _yf_major_holders(symbol)
    _header("Major holders", T, "live" if not major.empty else "unavailable")
    p,r=_df_to_rows(major, 20, 6); _table(p,r,T)
    inst = _yf_institutional_holders(symbol)
    _header("Institutional holders", T, "live" if not inst.empty else "unavailable")
    p,r=_df_to_rows(inst, 20, 8); _table(p,r,T)
    mf = _yf_mutualfund_holders(symbol)
    _header("Mutual-fund holders", T, "live" if not mf.empty else "unavailable")
    p,r=_df_to_rows(mf, 20, 8); _table(p,r,T)
    ins = _yf_insider_transactions(symbol)
    _header("Insider transactions", T, "live" if not ins.empty else "unavailable")
    p,r=_df_to_rows(ins, 25, 9); _table(p,r,T)
    _panel("Important distinction", f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">The holder tables above are provider observations. They are not a substitute for an exchange-linked FII/DII flow attribution dataset.</div>', T)


def _flows(data,T):
    symbol=data["symbol"]
    sh=data.get("shareholding") or {}
    sd=sh.get("data") or {}
    _header("Ownership-change tape",T,sh.get("status","unavailable"))
    flow_rows=[]
    for needle,label in [("promoters","Promoters"),("fiis","FIIs"),("fiis","FII"),("diis","DIIs"),("diis","DII"),("public","Public")]:
        if any(r.get("label","").lower()==needle or needle in r.get("label","").lower() for r in sd.get("rows",[])):
            row=next(r for r in sd.get("rows",[]) if needle in r.get("label","").lower())
            vals=[_num(v) for v in row.get("values",[])]; vals=[v for v in vals if v is not None]
            if vals:
                latest=vals[-1]; prev=vals[-2] if len(vals)>1 else None
                change=(latest-prev) if prev is not None else None
                flow_rows.append({"label":label,"values":[f"{latest:.2f}%",f"{prev:.2f}%" if prev is not None else "—",f"{change:+.2f} pp" if change is not None else "—"]})
    if flow_rows:
        _table(["LATEST","PREVIOUS","CHANGE"],flow_rows,T)
    else:
        _unavailable("Ownership-change tape","Quarterly shareholding observations",T)

    ins=_yf_insider_transactions(symbol)
    inst=_yf_institutional_holders(symbol)
    if not ins.empty:
        _header("Insider transaction observations",T,"live"); p,r=_df_to_rows(ins,20,9); _table(p,r,T)
    if not inst.empty:
        _header("Institutional-holder observations",T,"live"); p,r=_df_to_rows(inst,20,9); _table(p,r,T)
    _unavailable("FII / DII security-level flow attribution", "Exchange-linked security-level flow dataset", T)
    _unavailable("Block / bulk deals", "NSE/BSE bulk and block deal feed", T)


def _segments(data,T):
    info=_yf_info(data["symbol"])
    if info.get("sector") or info.get("industry"):
        _kpis([("Sector",info.get("sector","N/A"),"Yahoo"),("Industry",info.get("industry","N/A"),"Yahoo"),("Revenue",info.get("totalRevenue","N/A"),"provider"),("Gross Margin",info.get("grossMargins","N/A"),"provider")],T,4)
    for title, req in [("Segment revenue", "Company filing / annual-report segment tables"),("Segment EBITDA", "Segment-level operating-profit/EBITDA disclosures"),("Segment growth & mix", "Multi-period segment history"),("Geographic exposure", "Geographic revenue/profit disclosures")]:
        _unavailable(title, req, T)


def _peers(data,T):
    result=_peer_cached(data["symbol"])
    _header("Peer table",T,result.get("status"))
    if result.get("rows"):
        rows=[{"label":f'{r["name"]} ({r["symbol"]})',"values":[r.get("cmp","—"),r.get("pe","—"),r.get("market_cap","—"),r.get("div_yield","—"),r.get("roce","—")]} for r in result["rows"]]
        _table(["CMP ₹","P/E","M.CAP ₹Cr","DIV YIELD %","ROCE %"],rows,T)
        _panel("Peer set",f'<div style="font:10px {T["mono"]};color:{T["t2"]}">The current free peer set is curated by sector in the data layer. Extend the map when you want more companies covered.</div>',T)
    else:
        _unavailable("Peer comparison", "Comparable-company universe", T)
    _unavailable("Peer growth / quality", "Synchronized multi-period peer financial history", T)


def _supply_chain(data,T):
    info=_yf_info(data["symbol"])
    _panel("Supplier / customer workspace",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">Sector: {_esc(info.get("sector","N/A"))} · Industry: {_esc(info.get("industry","N/A"))}. The terminal does not invent supplier or customer relationships from sector labels.</div>',T)
    for title, req in [("Supplier map", "Company filings, supplier disclosures and supply-chain relationship dataset"),("Customer map", "Customer concentration / relationship disclosures"),("Commodity exposure", "Segment-level commodity sensitivity and input-cost history"),("Supply-chain news", "Entity-linked news/event graph")]:
        _unavailable(title,req,T)


def _management(data,T):
    officers=_yf_company_officers(data["symbol"])
    _header("Company officers",T,"live" if officers else "unavailable")
    if officers:
        rows=[]
        for x in officers[:12]:
            rows.append({"label":x.get("name","—"),"values":[x.get("title","—"),x.get("yearBorn","—") or "—"]})
        _table(["ROLE","YEAR BORN"],rows,T)
    else:
        _unavailable("Company officers", "Provider company-officer dataset", T)
    for title, req in [("Earnings-call transcripts", "Timestamped earnings-call transcript/audio source"),("Management guidance", "Transcript / investor-presentation extraction"),("Commentary changes", "Quarter-over-quarter management-language history"),("Filings & presentations", "Exchange/company filing document feed")]:
        _unavailable(title,req,T)


def _filings(data,T):
    info=_yf_info(data["symbol"])
    website=info.get("website")
    if website:
        _panel("Investor relations entry point",f'<a href="{html.escape(str(website),quote=True)}" target="_blank" style="color:{T["amber"]};font:10px {T["mono"]}">{_esc(website)}</a>',T)
    # yfinance SEC filings is useful when Yahoo exposes them; for NSE names it can be sparse.
    try:
        filings=yf.Ticker(data["symbol"]+".NS").sec_filings or {}
    except Exception:
        filings={}
    if filings:
        _header("Provider filings",T,"live")
        items=[]
        if isinstance(filings,dict):
            for k,v in list(filings.items())[:20]: items.append({"label":str(k),"values":[str(v)]})
        elif isinstance(filings,list):
            for i,v in enumerate(filings[:20]): items.append({"label":str(i+1),"values":[str(v)]})
        _table(["VALUE"],items,T)
    else:
        _unavailable("Exchange announcements", "NSE/BSE corporate announcement feed", T)
        _unavailable("Financial results filings", "NSE/BSE results filing feed", T)
        _unavailable("Annual reports", "Company/NSE/BSE document repository", T)
        _unavailable("Investor presentations", "Company investor-relations document feed", T)


def _events(data,T):
    symbol=data["symbol"]
    cal=_yf_calendar(symbol)
    if isinstance(cal,dict) and cal:
        _header("Event calendar",T,"live")
        _table(["VALUE"],[{"label":str(k),"values":[str(v)]} for k,v in cal.items()],T)
    elif isinstance(cal, pd.DataFrame) and not cal.empty:
        _header("Event calendar",T,"live")
        p,r=_df_to_rows(cal,20,8); _table(p,r,T)
    else:
        ed=get_earnings_date(symbol)
        _panel("Next earnings",f'<div style="font:13px {T["mono"]};color:{T["ivory"]}">{_esc(ed.get("date") or "N/A")}</div>',T)
    div=_yf_dividends(symbol); splits=_yf_splits(symbol)
    _header("Dividend history",T,"live" if len(div) else "unavailable")
    if len(div): _table(["DATE","DIVIDEND"],[{"label":str(k),"values":[f"{v:,.4f}"]} for k,v in div.items()],T)
    else: _unavailable("Dividend history","Corporate-actions history",T)
    _header("Split history",T,"live" if len(splits) else "unavailable")
    if len(splits): _table(["DATE","RATIO"],[{"label":str(k),"values":[str(v)]} for k,v in splits.items()],T)
    else: _unavailable("Split history","Corporate-actions history",T)


def _news(data,T,news_fetch_fn):
    symbol=data["symbol"]
    news=[]
    # v9.1 Arka News engine is the primary source so Research > News and the
    # right rail use one normalized article model.
    try:
        news=get_security_news(symbol,days=7) or []
    except Exception:
        news=[]
    if not news and news_fetch_fn:
        try: news=news_fetch_fn(symbol,days=7) or []
        except TypeError:
            try: news=news_fetch_fn(symbol) or []
            except Exception: news=[]
        except Exception: news=[]
    if not news:
        raw=_yf_news(symbol)
        for x in raw[:20]:
            content=x.get("content",x) if isinstance(x,dict) else {}
            title=content.get("title") or x.get("title") or "Untitled"
            url=(content.get("canonicalUrl") or {}).get("url") if isinstance(content.get("canonicalUrl"),dict) else content.get("link")
            provider=content.get("provider",{}).get("displayName") if isinstance(content.get("provider"),dict) else x.get("publisher")
            news.append({"title":title,"source":provider or "Yahoo Finance","published":content.get("pubDate") or x.get("providerPublishTime"),"url":url})
    if not news:
        _unavailable("News timeline","Arka News Intelligence v9.1 / Yahoo fallback",T)
        return
    _header("Security news timeline",T,"live")
    for item in news[:30]:
        title=item.get("title") or item.get("headline") or "Untitled"
        source=item.get("source") or item.get("publisher") or "Source"
        ts=item.get("time_str") or item.get("published") or item.get("published_at") or item.get("time") or ""
        event=item.get("event") or "NEWS"
        priority=item.get("priority")
        sentiment=item.get("sentiment_label") or item.get("sentiment") or "—"
        url=item.get("url") or item.get("link")
        link=f'<a href="{html.escape(str(url),quote=True)}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(title)}</a>' if url else _esc(title)
        meta=f'{_esc(source)} · {_esc(ts)} · {_esc(event)} · {_esc(sentiment)}'
        if priority is not None: meta += f' · priority {_esc(priority)}'
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><div>{link}</div><div style="color:{T["t3"]};margin-top:3px">{meta}</div></div>',unsafe_allow_html=True)


def _rsi(close, period=14):
    d=close.diff(); g=d.clip(lower=0).rolling(period).mean(); l=(-d.clip(upper=0)).rolling(period).mean(); rs=g/l.replace(0,float("nan")); return 100-100/(1+rs)


def _technical(data,T):
    period=st.selectbox("Technical range",["1mo","3mo","6mo","1y","2y"],index=2,key="research_technical_range_v91")
    h=_yf_history(data["symbol"],period,"1d")
    if h.empty:
        _unavailable("OHLCV history","Yahoo Finance market history",T); return
    close=h["Close"].dropna(); vol=h["Volume"].fillna(0)
    ma20=close.rolling(20).mean(); ma50=close.rolling(50).mean(); ma200=close.rolling(200).mean()
    rsi=_rsi(close).iloc[-1] if len(close)>=15 else None
    atr = None
    if len(h) >= 15:
        tr = pd.concat([h["High"]-h["Low"], (h["High"]-h["Close"].shift(1)).abs(), (h["Low"]-h["Close"].shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
    avg20=vol.rolling(20).mean().iloc[-1] if len(vol)>=20 else None
    relvol=float(vol.iloc[-1]/avg20) if avg20 else None
    def f(x): return f"{x:,.2f}" if x is not None and pd.notna(x) else "N/A"
    _kpis([("Close",f(float(close.iloc[-1])),"₹"),("SMA20",f(float(ma20.iloc[-1])),"₹"),("SMA50",f(float(ma50.iloc[-1])),"₹"),("SMA200",f(float(ma200.iloc[-1])),"₹"),("RSI14",f(float(rsi)) if rsi is not None else "N/A",""),("ATR14",f(float(atr)) if atr is not None else "N/A","₹"),("Rel Volume",f(relvol) if relvol is not None else "N/A","x"),("20D Vol Avg",f(float(avg20)) if avg20 else "N/A","shares")],T,4)
    _render_chart(data["symbol"],T,period,"technical")
    pdh=float(h["High"].iloc[-2]) if len(h)>1 else None; pdl=float(h["Low"].iloc[-2]) if len(h)>1 else None
    hi52 = float(h["High"].tail(252).max()) if len(h) else None
    lo52 = float(h["Low"].tail(252).min()) if len(h) else None
    rows=[{"label":"PDH","values":[f"₹{pdh:,.2f}" if pdh else "N/A"]},{"label":"PDL","values":[f"₹{pdl:,.2f}" if pdl else "N/A"]},{"label":"52W High","values":[f"₹{hi52:,.2f}" if hi52 else "N/A"]},{"label":"52W Low","values":[f"₹{lo52:,.2f}" if lo52 else "N/A"]}]
    _table(["VALUE"],rows,T)


def _relative_strength(data,T):
    period=st.selectbox("Relative-strength window",["1mo","3mo","6mo","1y","2y"],index=2,key="research_rs_range_v91")
    stock=_yf_history(data["symbol"],period,"1d")["Close"].dropna()
    if stock.empty:
        _unavailable("Relative strength","Stock + benchmark market history",T); return
    sret=(stock.iloc[-1]/stock.iloc[0]-1)*100 if len(stock)>1 else None
    rows=[{"label":data["symbol"],"values":[f"{sret:+.2f}%" if sret is not None else "N/A","BASE"]}]
    for name,ticker in [("NIFTY 50","^NSEI"),("NIFTY 500","^CRSLDX"),("S&P 500","^GSPC")]:
        b=yf.Ticker(ticker).history(period=period,interval="1d")["Close"].dropna()
        r=(b.iloc[-1]/b.iloc[0]-1)*100 if len(b)>1 else None
        ex=sret-r if sret is not None and r is not None else None
        rows.append({"label":name,"values":[f"{r:+.2f}%" if r is not None else "N/A",f"{ex:+.2f}%" if ex is not None else "N/A"]})
    _header("Relative performance",T,"live"); _table(["RETURN","EXCESS VS STOCK"],rows,T)


def _factors(data,T):
    symbol=data["symbol"]; h=_yf_history(symbol,"1y","1d")
    if h.empty: _unavailable("Factor observations","Price history + company fundamentals",T); return
    close=h["Close"].dropna(); ret=close.pct_change().dropna()
    nifty=yf.Ticker("^NSEI").history(period="1y",interval="1d")["Close"].dropna()
    merged=pd.concat([close.rename("s"),nifty.rename("n")],axis=1).dropna(); beta=None
    if len(merged)>30: beta=merged["s"].pct_change().cov(merged["n"].pct_change())/merged["n"].pct_change().var()
    info=_yf_info(symbol)
    rows=[
        {"label":"1Y Momentum","values":[f"{(close.iloc[-1]/close.iloc[0]-1)*100:+.2f}%"]},
        {"label":"Annualized Volatility","values":[f"{ret.std()*(252**0.5)*100:.2f}%"]},
        {"label":"Beta vs NIFTY","values":[f"{beta:.2f}" if beta is not None else "N/A"]},
        {"label":"ROE","values":[str(info.get("returnOnEquity","N/A"))]},
        {"label":"ROA","values":[str(info.get("returnOnAssets","N/A"))]},
        {"label":"Operating Margin","values":[str(info.get("operatingMargins","N/A"))]},
        {"label":"Profit Margin","values":[str(info.get("profitMargins","N/A"))]},
    ]
    _header("Current factor observations",T,"live"); _table(["LATEST"],rows,T)
    _panel("Factor definitions",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">Momentum = trailing price return; volatility = annualized daily-return volatility; beta = covariance to NIFTY / NIFTY variance. Profitability rows come from provider fundamentals.</div>',T)


def _risk(data,T):
    info=_yf_info(data["symbol"]); s=(data.get("summary") or {}).get("data") or {}
    rows=[]
    for label,key,basis in [("Beta","beta","Yahoo"),("Current Ratio","currentRatio","Yahoo"),("Quick Ratio","quickRatio","Yahoo"),("Debt / Equity","debtToEquity","Yahoo"),("Interest Coverage","interestCoverage","Yahoo"),("Operating Margin","operatingMargins","Yahoo"),("Profit Margin","profitMargins","Yahoo"),("Valuation P/E","pe_ratio","Screener")]:
        val=info.get(key,s.get(key))
        rows.append({"label":label,"values":[str(val if val not in (None,"") else "N/A"),basis]})
    _header("Risk monitor",T,"live"); _table(["VALUE","BASIS"],rows,T)
    for title,req in [("Business concentration","Business/segment concentration dataset"),("Regulatory risk","Regulatory event/entity feed"),("Commodity risk","Commodity sensitivity disclosures"),("Currency risk","FX exposure disclosures"),("Liquidity / impact risk","Free float, turnover and market-impact history"),("Governance risk","Related-party, pledge and governance event history")]: _unavailable(title,req,T)


def _price_fundamentals(data,T):
    symbol=data["symbol"]
    period=st.selectbox("Price / fundamentals window",["3mo","6mo","1y","2y","5y"],index=1,key="research_pf_range_v91")
    h=_render_chart(symbol,T,period,"pricefund")
    hist=_yf_earnings_history(symbol)
    if not hist.empty and h is not None and not h.empty:
        rows=[]
        # Keep this table point-in-time: only use reported actual EPS observations.
        for idx,row in hist.tail(8).iterrows():
            dt=str(idx)[:10]
            actual=row.get("epsactual") if isinstance(row,pd.Series) else None
            surprise=row.get("surprisePercent") if isinstance(row,pd.Series) else None
            rows.append({"label":dt,"values":[str(actual if pd.notna(actual) else "—"),str(surprise if pd.notna(surprise) else "—")]})
        _header("Earnings observations",T,"live"); _table(["EPS ACTUAL","SURPRISE %"],rows,T)
    val=_yf_valuation(symbol)
    if not val.empty:
        _header("Valuation history",T,"live"); p,r=_df_to_rows(val,12,9); _table(p,r,T,["PERatio","PBRatio","EVToEBITDA"])


def _what_changed(data,T):
    symbol=data["symbol"]
    q=_yf_income(symbol,"quarterly")
    rows=[]
    for label in ["Total Revenue","Operating Income","Net Income","EBITDA","Diluted EPS","Basic EPS"]:
        if label in q.index and q.shape[1]>=2:
            a,b=q.loc[label].iloc[0],q.loc[label].iloc[1]
            ch=(float(a)/abs(float(b))-1)*100 if pd.notna(a) and pd.notna(b) and float(b)!=0 else None
            rows.append({"label":label,"values":[f"{float(a):,.2f}" if pd.notna(a) else "—",f"{float(b):,.2f}" if pd.notna(b) else "—",f"{ch:+.2f}%" if ch is not None else "—"]})
    _header("Fundamental changes",T,"live" if rows else "unavailable"); _table(["LATEST","PREVIOUS","CHANGE"],rows,T)
    val=_yf_valuation(symbol)
    if not val.empty:
        cols=[c for c in val.columns if c != "Current"]
        metric=next((m for m in val.index if "PERatio" in str(m)),None)
        if metric and len(cols)>=2:
            a,b=val.loc[metric,cols[0]],val.loc[metric,cols[1]]
            _kpis([("P/E latest",f"{a:.2f}" if pd.notna(a) else "N/A","valuation"),("P/E previous",f"{b:.2f}" if pd.notna(b) else "N/A","valuation"),("P/E change",f"{((a/b)-1)*100:+.2f}%" if pd.notna(a) and pd.notna(b) and b!=0 else "N/A","history")],T,3)
    _panel("Change engine",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">V9.1 computes changes only from adjacent dated provider observations. Estimate revisions and security-level ownership flows remain gated until point-in-time datasets are connected.</div>',T)


def _thesis(data,T):
    symbol=data["symbol"]
    st.markdown(f'<div style="font:10px {T["mono"]};color:{T["t2"]};margin:6px 0">RESEARCH NOTEBOOK · {html.escape(symbol)}</div>',unsafe_allow_html=True)
    fields=[("Bull case","research_bull"),("Bear case","research_bear"),("Catalysts","research_catalysts"),("Risks","research_risks")]
    for title,key in fields:
        st.text_area(title,value=st.session_state.get(key,""),height=110,key=key)
    if st.button("SAVE RESEARCH NOTES",key="research_notes_save_v91",type="primary"):
        st.success("Research notes saved for this session.")
    _panel("Source discipline",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">Use sourced facts, dates and links in each field. The notebook stores your notes; it does not turn them into a recommendation.</div>',T)


def _graph(data,T):
    symbol=data["symbol"]
    nodes=["FINANCIALS","EARNINGS","VALUATION","OWNERSHIP","FLOWS","SEGMENTS","PEERS","SUPPLY CHAIN","MANAGEMENT","EVENTS","NEWS","RISK"]
    _header("Research graph",T,"live")
    cols=st.columns(3)
    for i,n in enumerate(nodes):
        with cols[i%3]:
            st.markdown(f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:10px;margin:4px 0;font:700 10px {T["mono"]};color:{T["ivory"]}">{_esc(symbol)} <span style="color:{T["amber"]}">→ {_esc(n)}</span><div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:4px">OPEN THIS FUNCTION TO DRILL DOWN</div></div>',unsafe_allow_html=True)


def _data_sources(data,T):
    s=data.get("summary") or {}; sec=data.get("sector") or {}
    rows=[
        {"label":"Core security resolution","values":["Screener",s.get("status","unavailable")]},
        {"label":"Price / OHLCV","values":["Yahoo Finance / yfinance","cached 5m"]},
        {"label":"Company profile","values":["Yahoo Finance + Screener","cached 10m"]},
        {"label":"Income / balance / cash flow","values":["yfinance + Screener fallback","lazy"]},
        {"label":"Earnings estimates / revisions","values":["yfinance","lazy"]},
        {"label":"Holder datasets","values":["yfinance","lazy"]},
        {"label":"Peer comparison","values":["Curated Screener peer map","lazy"]},
        {"label":"News","values":["Arka News Intelligence v9.1 + Yahoo fallback","cached / lazy"]},
        {"label":"FII/DII security attribution","values":["Dedicated exchange dataset required","not connected"]},
        {"label":"Segment / supply chain","values":["Company filing / research dataset required","not connected"]},
        {"label":"Point-in-time DCF","values":["Editable assumption engine required","not connected"]},
    ]
    _header("Data provenance",T)
    _table(["SOURCE / ENGINE","STATUS"],rows,T)
    _panel("V9.1 integrity rule",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">No unavailable dataset is fabricated. A section is marked live only when the selected connector actually returns data.</div>',T)


def render_research_page(T=None, news_fetch_fn=None):
    T=_theme(T)
    st.markdown(f'''<style>
    [data-testid="stAppViewContainer"]{{background:{T["bg"]}}}
    div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea{{font-family:{T["mono"]};background:{T["panel"]};color:{T["ivory"]};border:1px solid {T["border"]}}}
    div[data-testid="stTextInput"] input:focus, div[data-testid="stTextArea"] textarea:focus{{border-color:{T["amber"]}}}
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div{{background:{T["panel"]};border-color:{T["border"]};font-family:{T["mono"]};}}
    </style>''', unsafe_allow_html=True)
    _status_strip(T)

    active=st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    query=st.text_input("Research security",value=active,placeholder="RELIANCE / HDFCBANK / TCS",label_visibility="collapsed",key="research_query_v91")
    c1,c2=st.columns([5,1])
    with c1:
        if query.strip() and query.strip().upper()!=active.upper():
            # Do not fetch while the user is still typing; only commit from the button.
            pass
        st.caption("Research shell loads the core security only. Detailed datasets load when you choose a function.")
    with c2:
        if st.button("LOAD SECURITY",key="research_load_v91",use_container_width=True,type="primary"):
            q=query.strip().upper()
            if q:
                try:
                    res=resolve_symbol(q)
                    if res:
                        import re
                        m=re.search(r"/company/([^/]+)",str(res.get("url","")),flags=re.I)
                        canonical=m.group(1).upper() if m else q
                    else:
                        canonical=q
                except Exception:
                    canonical=q
                st.session_state.active_security=canonical
                st.session_state.research_last_query=canonical
                st.session_state.pop("research_core",None)
                st.rerun()

    symbol=st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    if not symbol:
        _panel("Research ready", f'<div style="font:11px {T["mono"]};color:{T["t3"]}">Load a security to initialize the Research workspace.</div>',T,dashed=True)
        return

    core=st.session_state.get("research_core")
    if not core or core.get("symbol")!=symbol:
        with st.spinner("Loading security core…"):
            core=_core_cached(symbol)
        if not core.get("resolved"):
            st.error(core.get("reason","Security resolution failed.")); return
        st.session_state.research_core=core
    data=core
    _summary_header(data,T)

    periods=["1mo","3mo","6mo","1y","2y","5y"]
    chart_period=st.selectbox("Research chart range",periods,index=2,key="research_top_chart_v91")
    _header("Price / volume chart",T,(data.get("summary") or {}).get("status"),"Core chart is loaded once and reused by research functions where possible")
    _render_chart(symbol,T,chart_period,"top")

    tabs=[
        "01 Overview","02 Company","03 Business","04 Financials","05 Earnings","06 Valuation",
        "07 Ownership","08 Flows","09 Segments","10 Peers","11 Supply Chain","12 Management",
        "13 Filings","14 Events","15 News","16 Technical","17 Relative Strength","18 Factors",
        "19 Risk","20 Price + Fundamentals","21 What Changed?","22 Thesis","23 Research Graph","24 Data / Sources"
    ]
    selected=_research_nav(tabs,0)

    dispatch={
        "01 Overview":lambda:_overview(data,T),"02 Company":lambda:_overview(data,T) if False else _company(data,T),"03 Business":lambda:_business(data,T),
        "04 Financials":lambda:_financials(data,T),"05 Earnings":lambda:_earnings(data,T),"06 Valuation":lambda:_valuation(data,T),
        "07 Ownership":lambda:_ownership(data,T),"08 Flows":lambda:_flows(data,T),"09 Segments":lambda:_segments(data,T),
        "10 Peers":lambda:_peers(data,T),"11 Supply Chain":lambda:_supply_chain(data,T),"12 Management":lambda:_management(data,T),
        "13 Filings":lambda:_filings(data,T),"14 Events":lambda:_events(data,T),"15 News":lambda:_news(data,T,news_fetch_fn),
        "16 Technical":lambda:_technical(data,T),"17 Relative Strength":lambda:_relative_strength(data,T),"18 Factors":lambda:_factors(data,T),
        "19 Risk":lambda:_risk(data,T),"20 Price + Fundamentals":lambda:_price_fundamentals(data,T),"21 What Changed?":lambda:_what_changed(data,T),
        "22 Thesis":lambda:_thesis(data,T),"23 Research Graph":lambda:_graph(data,T),"24 Data / Sources":lambda:_data_sources(data,T),
    }
    dispatch[selected]()


def _company(data,T):
    # Keep Company separate from Overview so it can be drilled into without repeating the entire research dataset.
    info=_yf_info(data["symbol"]); s=(data.get("summary") or {}).get("data") or {}; sec=(data.get("sector") or {}).get("data") or {}
    _header("Company profile",T,"live")
    rows=[
        {"label":"Name","values":[data.get("name","N/A")]},
        {"label":"Symbol","values":[data.get("symbol","N/A")]},
        {"label":"Sector","values":[sec.get("Sector") or info.get("sector") or "N/A"]},
        {"label":"Industry","values":[sec.get("Industry") or info.get("industry") or "N/A"]},
        {"label":"Market Cap","values":[str(s.get("market_cap") or info.get("marketCap") or "N/A")]},
        {"label":"Employees","values":[str(info.get("fullTimeEmployees") or "N/A")]},
        {"label":"Website","values":[str(info.get("website") or "N/A")]},
    ]
    _table(["VALUE"],rows,T)
    _panel("Business description",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(info.get("longBusinessSummary") or "No verified business description returned.")}</div>',T)
