"""Arka Trades — Research Terminal v15.

v15: redesign and a new function.
* PB  Price behaviour: in the last 20 / 60 / 90 / 120 / 250 sessions (or any number) how many up and down days or
      green and red candles, average and median up / down move, biggest days, up/down volume ratio, delivered
      quantity on up vs down days (NSE), streaks, gaps, move-size buckets, range and ATR, and the same window for
      NIFTY 50, with a bar per session. Also a 60-session card on the dashboard.
* New look: Inter throughout, sentence case, soft rounded cards with more room, a security header card, section pills
  instead of two rows of code buttons, clean tables, charts that match the app. Function codes still work in the
  command bar (now with an "All codes" list).

v14: new terminal design (security bar, two-row function menu, dashboard as the default screen, class-based tables

v14: new terminal design (security bar, two-row function menu, dashboard as the default screen, class-based tables
with automatic green / red) and 11 new functions: OV dashboard, GP chart, HP price history, COMP comparison,
BETA regression, HVG volatility, TRA total return, DCF reverse DCF, DUP DuPont, GRW growth and common size,
EVTS events timeline; plus management in DES and a P/E band on price in VAL.

v13 notes follow (deliveries, quant, financial health, filings, research intelligence).

What changed vs v12
-------------------
New functions (data layer: research_data.py)
* DLV   Deliveries — delivered vs traded quantity and delivery %, daily / weekly / monthly, from NSE's
        daily bhavcopy. Stored in Supabase when configured so history builds up.
* FH    Financial health — debt/equity, interest coverage, current ratio, net debt/EBITDA, free cash flow,
        cash conversion, working-capital days.
* ERN   Earnings trend — each quarter against the same quarter a year earlier.
* QR    Quant — relative strength vs NIFTY 500 and NIFTY 50, RS line and trend, momentum 12-1,
        volatility, beta, drawdown.
* SYN   Research intelligence — evidence sheet with sources, contradiction detection, and a Gemini
        synthesis that is given only that evidence sheet.
* CACS  now reads the company's filings from BSE (by category); ScanX remains the fallback.
* NOTE  thesis notes are saved to Supabase when configured.
Fixes
* DES   52-week high / low now comes from Yahoo history (it showed N/A); sector uses the NSE classification.
* HC    the liquidity test (current ratio) is now computed from Yahoo's balance sheet.

v12 notes follow.

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
import json
import math
import re

import numpy as np
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

try:
    import research_data as rd
except Exception:  # pragma: no cover
    rd = None

try:
    import sector_map as _smap
except Exception:  # pragma: no cover
    _smap = None

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
    ("OVERVIEW", [
        ("OV", "Dashboard", "Chart, performance, valuation, quality, earnings, ownership, contradictions and filings on one screen"),
        ("DES", "Description", "Company profile, key fundamentals, management, growth snapshot, pros and cons"),
        ("SYN", "Research Intel", "Evidence sheet, contradiction detection and AI synthesis"),
    ]),
    ("FINANCIALS", [
        ("FA", "Income Statement", "Quarterly / yearly results with QoQ and YoY growth"),
        ("BS", "Balance Sheet", "Assets, liabilities, borrowings and reserves"),
        ("CF", "Cash Flow", "Operating, investing and financing cash flows"),
        ("RAT", "Ratios", "Efficiency and return ratios"),
        ("GRW", "Growth", "1 / 3 / 5 / 10-year compound growth and common-size statement"),
        ("ERN", "Earnings Trend", "Each quarter against the same quarter a year earlier"),
    ]),
    ("QUALITY", [
        ("HC", "Health Check", "Piotroski-style quality score and automatic red / green flags"),
        ("FH", "Financial Health", "Leverage, coverage, liquidity, free cash flow and cash conversion"),
        ("DUP", "DuPont", "Return on equity split into margin, asset turnover and leverage"),
    ]),
    ("VALUATION", [
        ("VAL", "Valuation", "P/E, P/B, earnings yield, Graham number, P/E history and P/E band on price"),
        ("DCF", "Reverse DCF", "The growth rate today's price already assumes, with a sensitivity grid"),
        ("RV", "Peers", "Relative value versus sector peers"),
        ("ANR", "Analysts", "Price targets, rating distribution and rating changes"),
    ]),
    ("PRICE", [
        ("GP", "Chart", "Full-size price chart"),
        ("PB", "Price behaviour", "Up vs down days, average moves, biggest days, volume and delivery on up vs down days, streaks and gaps"),
        ("HP", "Price History", "Daily open / high / low / close / volume table with CSV export"),
        ("TECH", "Technicals", "Trend, momentum, moving averages and pivot levels"),
        ("SEAS", "Seasonality", "Monthly return heatmap, average month and win rate"),
        ("TRA", "Total Return", "Price return plus dividends, per-year return"),
    ]),
    ("RISK", [
        ("QR", "Quant", "Relative strength vs NIFTY 500 / 50, momentum, volatility, beta, drawdown"),
        ("COMP", "Compare", "Rebased performance against any stocks or indices, with correlation"),
        ("BETA", "Beta", "Regression against the index: beta, alpha, R-squared, rolling beta"),
        ("HVG", "Volatility", "Realised volatility 20 / 60 / 120 sessions, percentile, ATR"),
        ("PERF", "vs NIFTY 50", "Relative performance, alpha, beta, volatility, drawdown"),
    ]),
    ("OWNERSHIP", [
        ("HDS", "Shareholding", "Promoter / FII / DII / public holding trend"),
        ("DLV", "Deliveries", "Delivered vs traded quantity and delivery %, daily / weekly / monthly (NSE)"),
        ("MF", "MF Holdings", "Mutual fund positions (ScanX, optional source)"),
    ]),
    ("EVENTS", [
        ("EVTS", "Events", "Timeline of results, board meetings, dividends, splits and material filings"),
        ("CN", "News", "Company news timeline"),
        ("CACS", "Filings", "Exchange filings from BSE, by category"),
        ("DVD", "Dividends", "Dividend history, splits and bonus issues"),
    ]),
    ("WORKSPACE", [
        ("NOTE", "Thesis Notes", "Your bull / bear case, catalysts, risks and target"),
        ("SRC", "Sources", "Where every number on this screen comes from"),
    ]),
]
def _sent(name: str) -> str:
    """'Income Statement' -> 'Income statement'; acronyms and brand words stay."""
    w = name.split(" ")
    return " ".join([w[0]] + [x if (x.isupper() or x in ("DuPont",) or any(c.isdigit() for c in x)) else x.lower() for x in w[1:]])


GROUPS = [(g, [(c, _sent(n), d) for c, n, d in items]) for g, items in GROUPS]
FN = {code: (name, desc, grp) for grp, items in GROUPS for code, name, desc in items}
GROUP_LABEL = {"OVERVIEW": "Overview", "FINANCIALS": "Financials", "QUALITY": "Quality", "VALUATION": "Valuation", "PRICE": "Price",
               "RISK": "Risk & quant", "OWNERSHIP": "Ownership", "EVENTS": "Events & news", "WORKSPACE": "Workspace"}
FUNCS = list(FN)

ALIASES = {
    "OVERVIEW": "OV", "DASH": "OV", "DASHBOARD": "OV", "PROFILE": "DES", "MGMT": "DES", "HEALTH": "HC", "SCORE": "HC", "PIOTROSKI": "HC",
    "VALUATION": "VAL", "PE": "VAL", "FINANCIALS": "FA", "RESULTS": "FA", "PL": "FA", "IS": "FA",
    "BALANCE": "BS", "CASH": "CF", "CASHFLOW": "CF", "RATIOS": "RAT", "SHAREHOLDING": "HDS",
    "OWN": "HDS", "HOLDING": "HDS", "MFH": "MF", "PEERS": "RV", "COMPARE": "COMP", "PERFORMANCE": "PERF",
    "CHART": "GP", "G": "GP", "PRICES": "HP", "HIST": "HP", "VOL": "HVG", "HV": "HVG", "TR": "TRA", "TOTALRETURN": "TRA", "RDCF": "DCF", "DUPONT": "DUP", "GROWTH": "GRW", "CAGR": "GRW", "EVENTS": "EVTS", "EVT": "EVTS", "CALENDAR": "EVTS", "TECHNICALS": "TECH", "SEASONALITY": "SEAS", "SEAG": "SEAS",
    "ANALYST": "ANR", "TARGET": "ANR", "DIVIDENDS": "DVD", "NEWS": "CN", "ANNOUNCEMENTS": "CACS",
    "FILINGS": "CACS", "THESIS": "NOTE", "NOTES": "NOTE", "SOURCES": "SRC",
    "DELIVERY": "DLV", "DELIVERIES": "DLV", "DEL": "DLV", "FINHEALTH": "FH", "EARNINGS": "ERN", "EE": "ERN",
    "QUANT": "QR", "BEHAVIOUR": "PB", "BEHAVIOR": "PB", "CANDLES": "PB", "UPDOWN": "PB", "RS": "QR", "MOMENTUM": "QR", "INTEL": "SYN", "AI": "SYN", "SYNTHESIS": "SYN", "EVIDENCE": "SYN",
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
# ═══════════════════════════ UI primitives (v14 terminal design) ═══════════════════════════
_POS_WORDS = {"ABOVE", "OUTPERFORM", "PASS", "UP", "UPTREND", "BULLISH", "GOLDEN CROSS", "OVERSOLD"}
_NEG_WORDS = {"BELOW", "UNDERPERFORM", "FAIL", "DOWN", "DOWNTREND", "BEARISH", "DEATH CROSS", "OVERBOUGHT"}


_ACR = {"P/E", "P/B", "EPS", "ROE", "ROCE", "TTM", "CSV", "SMA", "EMA", "RSI", "ATR", "NIFTY", "MF", "BSE", "NSE", "FII", "DII",
        "FIIS", "DIIS", "MACD", "CAGR", "DMA", "R²", "AI", "IT", "BANK", "₹", "PE", "PB", "ID", "AGM", "EGM", "EBITDA", "ISIN"}
_ACR_FIX = {"YOY": "YoY", "QOQ": "QoQ", "FIIS": "FIIs", "DIIS": "DIIs"}
_STATE_WORDS = {"ABOVE", "BELOW", "OUTPERFORM", "UNDERPERFORM", "PASS", "FAIL", "UP", "DOWN", "UPTREND", "DOWNTREND", "MIXED", "BULLISH",
                "BEARISH", "GOLDEN CROSS", "DEATH CROSS", "OVERBOUGHT", "OVERSOLD", "NEUTRAL", "STRONG", "AVERAGE", "WEAK", "MARGIN",
                "ASSET TURNOVER", "LEVERAGE", "NEXT RESULTS", "DIVIDEND", "SPLIT / BONUS"}


def _hd(s) -> str:
    """Sentence case for an ALL-CAPS label, keeping acronyms, numbers and the current symbol."""
    s = str(s)
    if not s.isupper():
        return s
    keep = {str(st.session_state.get("_rp_sym", "")).upper()}
    out = []
    for i, w in enumerate(s.split(" ")):
        if w in _ACR_FIX:
            out.append(_ACR_FIX[w])
        elif w in _ACR or w in keep or any(ch.isdigit() for ch in w):
            out.append(w)
        else:
            out.append(w.lower())
    t = " ".join(out)
    return t[:1].upper() + t[1:] if t else t


def _val(v):
    """State words shown as values (UPTREND, GOLDEN CROSS…) in sentence case; everything else unchanged."""
    s = str(v)
    return s[:1] + s[1:].lower() if s.upper() in _STATE_WORDS and s.isupper() else s


def _cls(v) -> str:
    """Colour class for a table cell: signed numbers and a few state words."""
    s = str(v).strip()
    if re.match(r"^\+\s?[\d₹.]", s):
        return " pos"
    if re.match(r"^[-−]\s?[\d₹.]", s):
        return " neg"
    u = s.upper()
    return " pos" if u in _POS_WORDS else " neg" if u in _NEG_WORDS else ""


def _mini(title, rows, T, code=""):
    """Compact dashboard panel. rows: (label, value) or (label, value, color)."""
    body = ""
    for r in rows:
        c = r[2] if len(r) > 2 and r[2] else ""
        cls = _cls(r[1]) if not c else ""
        body += f'<div class="rp-mr"><span>{_esc(r[0])}</span><b class="{cls.strip()}" style="{"color:" + c if c else ""}">{_esc(_val(r[1]))}</b></div>'
    hint = f'<em>{_esc(code)}</em>' if code else ""
    st.markdown(f'<div class="rp-panel"><div class="t">{_esc(title)}{hint}</div><div class="b tight">{body}</div></div>', unsafe_allow_html=True)


def _spark(values, color, w=220, h=44):
    v = [float(x) for x in values if x == x]
    if len(v) < 3:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(v) - 1) * w:.1f},{h - 3 - (x - lo) / rng * (h - 6):.1f}" for i, x in enumerate(v))
    return (f'<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>')


def _tag(status, T, text=None):
    if status == "live":
        label, color = text or "Live", T["green"]
    elif status == "stale":
        label, color = text or "Cached", T["amber"]
    else:
        label, color = text or "Not available", T["t3"]
    return f'<span class="rp-tag" style="color:{color}">{_esc(label)}</span>'


def _header(title, T, status=None, note=None):
    st.markdown(f'<div class="rp-h"><b>{_esc(title)}</b>{_tag(status, T) if status else ""}</div>'
                + (f'<div class="rp-note">{_esc(note)}</div>' if note else ""), unsafe_allow_html=True)


def _panel(title, body, T, dashed=False):
    st.markdown(f'<div class="rp-panel{" dash" if dashed else ""}"><div class="t">{_esc(title)}</div><div class="b">{body}</div></div>',
                unsafe_allow_html=True)


def _unavailable(title, requirement, T, detail=None):
    detail = detail or "No verified dataset returned this field."
    _panel(title, f'<b style="color:var(--rp-tx);font-weight:600">Not available.</b> Needs {_esc(requirement)}.'
                  f'<div style="margin-top:4px">{_esc(detail)}</div>', T, dashed=True)


def _kpis(items, T, cols=4):
    """items: (label, value, sub) or (label, value, sub, color). One dense grid, hairline separators."""
    tiles = ""
    for it in items:
        label, value, sub = it[:3]
        color = it[3] if len(it) > 3 and it[3] else T["ivory"]
        tiles += (f'<div class="rp-k"><div class="l">{_esc(label)}</div><div class="v" style="color:{color}">{_esc(_val(value))}</div>'
                  f'<div class="s">{_esc(sub or "")}</div></div>')
    st.markdown(f'<div class="rp-kg" style="--n:{max(1, min(cols, len(items) or 1))}">{tiles}</div>', unsafe_allow_html=True)


def _table(periods, rows, T, highlights=(), dl=None):
    if not rows:
        _unavailable("No rows returned", "Selected dataset", T)
        return
    heads = "".join(f"<th>{_esc(_hd(p))}</th>" for p in periods)
    out = []
    for r in rows:
        label = str(r.get("label", ""))
        hl = any(x.lower() in label.lower() for x in highlights if x)
        cells = "".join(f'<td class="n{_cls(v)}">{_esc(_val(v))}</td>' for v in r.get("values", []))
        out.append(f'<tr class="{"hl" if hl else ""}"><td class="l">{_esc(label)}</td>{cells}</tr>')
    st.markdown(f'<div class="rp-tw"><table class="rp-t"><thead><tr><th class="l">&nbsp;</th>{heads}</tr></thead>'
                f'<tbody>{"".join(out)}</tbody></table></div>', unsafe_allow_html=True)
    if dl:
        n = len(periods)
        data = [[r.get("label", "")] + (list(r.get("values", [])) + [""] * n)[:n] for r in rows]
        csv = pd.DataFrame(data, columns=["METRIC"] + [str(p) for p in periods]).to_csv(index=False)
        st.download_button("Download CSV", csv, file_name=f"{dl}.csv", mime="text/csv", key=f"dl_{dl}")


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


def _checklist(items, T, labels=("Pass", "Fail", "N/A")):
    """items: (text, ok: True/False/None, detail)."""
    body = ""
    for text, ok, detail in items:
        mark, col = ((labels[0], T["green"]) if ok is True else (labels[1], T["red"]) if ok is False else (labels[2], T["t3"]))
        body += (f'<div class="rp-cl"><b style="color:{col}">{mark}</b><span>{_esc(text)}</span><i>{_esc(detail or "")}</i></div>')
    return body


def _fig(fig, T, h=300, key=None, **layout):
    fig.update_layout(height=h, margin=dict(l=4, r=12, t=34, b=4), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color="#A9A9B0", family="Inter, -apple-system, Segoe UI, sans-serif", size=12),
                      legend=dict(orientation="h", y=1.1, x=0, font=dict(size=12)), hoverlabel=dict(font_family="Inter, sans-serif"),
                      **layout)
    fig.update_xaxes(showgrid=False, linecolor="#26262B", tickfont=dict(size=11))
    fig.update_yaxes(showgrid=True, gridcolor="#1A1A1E", zeroline=False, tickfont=dict(size=11))
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
    price = chg = pts = pos = lo = hi = None
    if hist is not None and len(hist) >= 2:
        price, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
        chg, pts = (price / prev - 1) * 100, price - prev
        h252 = hist.tail(252)
        lo, hi = float(h252["Low"].min()), float(h252["High"].max())
        pos = None if hi == lo else max(0.0, min(100.0, (price - lo) / (hi - lo) * 100))
    bv, de = _f(s.get("book_value")), _debt_equity(B)
    sc = B.get("sector") or {}
    grp = None
    if _smap is not None:
        try:
            grp = _smap.classify(sym, B.get("name") or "", sc.get("Sector") or sc.get("Broad Sector") or "", "", sc.get("Industry") or "")
            grp = None if grp == "Unclassified" else grp
        except Exception:
            grp = None
    chips = [sym, "NSE"] + [x for x in dict.fromkeys((sc.get("Sector") or sc.get("Broad Sector"), sc.get("Industry"))) if x]
    sub = "".join(f"<span>{_esc(x)}</span>" for x in chips) + (f'<span class="g" title="Money flow rotation group">{_esc(grp)}</span>'
                                                             if grp and grp.lower() not in {str(c).lower() for c in chips} else "")
    cc = T["green"] if (chg or 0) >= 0 else T["red"]
    quote = ""
    if show_quote:
        quote = (f'<div class="q"><div class="px">{_rs(price)}</div>'
                 f'<div class="ch" style="color:{cc}">{pts:+,.2f} ({chg:+.2f}%)</div></div>' if price else
                 '<div class="q"><div class="px">N/A</div></div>')
    stats = [("Market cap", f'₹{s["market_cap"]} Cr' if s.get("market_cap") else "N/A"), ("P/E", f'{s["pe_ratio"]}x' if s.get("pe_ratio") else "N/A"),
             ("P/B", f"{price / bv:.2f}x" if price and bv else "N/A"), ("ROE", f'{s["roe"]}%' if s.get("roe") else "N/A"),
             ("ROCE", f'{s["roce"]}%' if s.get("roce") else "N/A"), ("Debt / equity", f"{de}x" if de is not None else "N/A"),
             ("Dividend yield", f'{s["dividend_yield"]}%' if s.get("dividend_yield") else "N/A")]
    cells = "".join(f'<div class="st"><span>{l}</span><b>{_esc(v)}</b></div>' for l, v in stats)
    rng = ""
    if pos is not None:
        rng = (f'<div class="st rg"><span>52-week range · {pos:.0f}% of the way from low to high</span><div class="bar"><i style="width:{pos:.1f}%"></i><u style="left:{pos:.1f}%"></u></div>'
               f'<div class="ends"><em>{lo:,.2f}</em><em>{hi:,.2f}</em></div></div>')
    st.markdown(f'<div class="rp-sec"><div class="id"><div class="nm">{_esc(B.get("name") or sym)}</div><div class="sb">{sub}</div></div>'
                f'{quote}<div class="sts">{cells}{rng}</div></div>', unsafe_allow_html=True)


# ═══════════════════════════ Sections ═══════════════════════════
def _overview(sym, B, T, key, nf):
    s = B.get("summary") or {}
    info = yf_attr(sym, "info") or {}
    _header("Key fundamentals", T, "live" if B.get("resolved") else "unavailable", B.get("url"))
    ed = _earnings_date(sym)
    hh = get_history(sym, "1y")
    if hh is not None and len(hh) > 20:
        hl = (f'₹{float(hh["High"].max()):,.2f} / ₹{float(hh["Low"].min()):,.2f}', "Yahoo, last 1 year")
    else:
        hl = (f'₹{s["year_high"]} / ₹{s["year_low"]}', "Screener") if s.get("year_high") else ("N/A", "Screener")
    sc = B.get("sector") or {}
    sector = sc.get("Sector") or sc.get("Broad Sector") or info.get("sector") or "N/A"
    industry = sc.get("Industry") or sc.get("Broad Industry") or info.get("industry") or ""
    _kpis([
        ("Market Cap", f'₹{s["market_cap"]} Cr' if s.get("market_cap") else "N/A", "Screener"),
        ("Stock P/E", f'{s["pe_ratio"]}x' if s.get("pe_ratio") else "N/A", "Screener"),
        ("Book Value", f'₹{s["book_value"]}' if s.get("book_value") else "N/A", "Screener"),
        ("Face Value", f'₹{s["face_value"]}' if s.get("face_value") else "N/A", "Screener"),
        ("52W High / Low", hl[0], hl[1]),
        ("Sector", str(sector), str(industry) or ("Screener" if sc else "Yahoo")),
        ("Employees", f'{info["fullTimeEmployees"]:,}' if isinstance(info.get("fullTimeEmployees"), int) else "N/A", "Yahoo"),
        ("Next earnings", ed or "N/A", "Yahoo calendar" if ed else "Not published"),
    ], T, 4)

    growth = B.get("growth") or {}
    if growth:
        _header("Growth snapshot", T, "live")
        cols = st.columns(len(growth))
        for c, (title, rows) in zip(cols, growth.items()):
            with c:
                body = "".join(f'<div style="display:flex;justify-content:space-between;font:12.5px/1.65 var(--rp-s);padding:3px 0;color:{T["t2"]}"><span>{_esc(k)}</span><b style="color:{T["ivory"]}">{_esc(v)}</b></div>' for k, v in rows.items())
                _panel(title, body, T)

    desc = info.get("longBusinessSummary") or B.get("about")
    if desc:
        _panel("Business", f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]};line-height:1.8">{_esc(desc[:1400])}</div>', T)

    _management(sym, T)

    if B.get("pros") or B.get("cons"):
        a, b = st.columns(2)
        with a:
            _panel("Pros (Screener)", "".join(f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]};padding:3px 0;line-height:1.6"><span style="color:{T["green"]}">+</span> {_esc(x)}</div>' for x in B.get("pros") or []) or "—", T)
        with b:
            _panel("Cons (Screener)", "".join(f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]};padding:3px 0;line-height:1.6"><span style="color:{T["red"]}">−</span> {_esc(x)}</div>' for x in B.get("cons") or []) or "—", T)

    if not B.get("resolved"):
        _unavailable("Screener fundamentals", "Screener.in company page", T, B.get("reason"))


# ---------- HC: health check ----------
def _piotroski(B, liq=None):
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
        ("Liquidity improving (current ratio)", None if not liq else liq[0] > liq[1],
         f"{liq[1]:.2f}x → {liq[0]:.2f}x · Yahoo" if liq else "current assets not available"),
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
    ybs = yf_attr(sym, "balance_sheet")
    crs = [(_yv(ybs, ["Current Assets"], i), _yv(ybs, ["Current Liabilities"], i)) for i in (0, 1)]
    liq = (crs[0][0] / crs[0][1], crs[1][0] / crs[1][1]) if all(a is not None and b for a, b in crs) else None
    res = _piotroski(B, liq)
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
               _checklist(flags, T, ("Good", "Warning", "—")), T)
    else:
        _panel("No flags triggered", f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]}">None of the rule thresholds were crossed, or the data needed was not available.</div>', T)
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
    _pe_band_chart(sym, B, T, key, close)


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
            _panel("Note", f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]}">Screener peer table did not load; showing curated peers with live prices only.</div>', T)
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
        st.markdown(f'<div style="padding:8px 0 8px 10px;border-bottom:1px solid {T["border"]};border-left:2px solid {sc};font:12.5px/1.65 var(--rp-s)"><div>{link}</div>'
                    f'<div style="color:{T["t3"]};margin-top:3px">{_esc(source)} · {_esc(ts)} {("· <span style=color:" + sc + ">" + _esc(sent.upper()) + "</span>") if sent else ""}</div></div>',
                    unsafe_allow_html=True)


# ═══════════════════════════ v14 additions ═══════════════════════════
def _yv(df, names, col=0):
    """A value from a yfinance statement frame (index = line item, columns = dates, newest first)."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            try:
                v = df.loc[n].iloc[col]
                return None if pd.isna(v) else float(v)
            except Exception:
                return None
    return None


def _last_num(vals):
    v = [x for x in vals if x is not None]
    return v[-1] if v else None


def _user_id():
    for k in ("username", "user", "user_email", "auth_user", "user_id"):
        v = st.session_state.get(k)
        if isinstance(v, dict):
            v = v.get("email") or v.get("username") or v.get("id")
        if v:
            return str(v)[:80]
    return "default"


def _qty(v):
    return "—" if v is None or pd.isna(v) else f"{v:,.0f}"


def _pe_stats(B, close):
    """(current TTM P/E, percentile of it within the stock's own quarter-end P/E history) or (None, None)."""
    ttm = _ttm_eps(B)
    if close is None or len(close) == 0 or not ttm or ttm <= 0:
        return None, None
    pe = float(close.iloc[-1]) / ttm
    q = B.get("quarterly")
    eps = _nums(_row(q, "EPS"))
    hist = []
    for i in range(3, len(eps)):
        w = eps[i - 3:i + 1]
        if any(v is None for v in w) or sum(w) <= 0 or i >= len(q["periods"]):
            continue
        d = _quarter_end(q["periods"][i])
        if d is None or d < close.index[0]:
            continue
        px = close.asof(d)
        if pd.notna(px):
            hist.append(float(px) / sum(w))
    return pe, (sum(1 for v in hist if v < pe) / len(hist) * 100 if len(hist) >= 4 else None)


# ---------- DLV: deliveries ----------
def _delivery_frame(sym, sessions):
    h = get_history(sym, "2y")
    if h is None or len(h) < 5:
        return None, {"error": "no price history to take trading dates from"}
    days = [d.date() for d in _naive(h["Close"]).index]
    if days and days[-1] == datetime.now(IST).date() and datetime.now(IST).hour < 19:
        days = days[:-1]                      # today's file is published in the evening
    days = days[-int(sessions):]
    ck = f"rp_dlv_{sym}_{sessions}_{days[-1] if days else ''}"
    if ck in st.session_state:
        return st.session_state[ck]
    df, info = rd.delivery_history(sym, days)
    if df is not None and not df.empty:
        st.session_state[ck] = (df, info)
        st.session_state[f"rp_dlv_last_{sym}"] = df
    return df, info


def _deliveries(sym, B, T, key, nf):
    if rd is None:
        _unavailable("Deliveries", "research_data.py next to research_page.py", T)
        return
    c1, c2 = st.columns([2, 1])
    mode = c1.radio("Period", ["Daily", "Weekly", "Monthly"], index=1, horizontal=True, key=f"{key}_dlv_mode", label_visibility="collapsed")
    n = c2.selectbox("History", [60, 120, 250], key=f"{key}_dlv_n", format_func=lambda v: f"Last {v} sessions", label_visibility="collapsed")
    with st.spinner("Loading NSE delivery data…"):
        df, info = _delivery_frame(sym, n)
    ok = df is not None and not df.empty
    _header("Deliveries · NSE security-wise bhavcopy", T, "live" if ok else "unavailable",
            "Delivered quantity = shares that changed hands for delivery (not squared off intraday). NSE only.")
    if not ok:
        _unavailable("Delivery data", "NSE daily bhavcopy with delivery (sec_bhavdata_full)", T,
                     f"{info.get('error') or 'No file could be read'}. NSE may be refusing this server. "
                     "See SRC for source health; a small script on your PC can fill the Supabase table instead.")
        return
    d = df["deliv_pct"].dropna()
    last_i = d.index[-1] if len(d) else None
    avg20 = float(d.tail(20).mean()) if len(d) >= 5 else None
    avg_all = float(d.mean()) if len(d) else None
    lastv = float(d.iloc[-1]) if len(d) else None
    _kpis([("Latest delivery %", _pct(lastv), str(last_i)[:10] if last_i is not None else "",
            (T["green"] if lastv is not None and avg_all and lastv > avg_all else None)),
           ("20-session average", _pct(avg20), "delivered ÷ traded quantity"),
           (f"{len(d)}-session average", _pct(avg_all), "the stock's own normal"),
           ("Sessions loaded", f"{len(df)} / {info.get('requested', len(df))}",
            f"{info.get('from_store', 0)} stored · {info.get('not_published', 0)} no file · {info.get('errors', 0)} failed")], T, 4)

    agg = rd.aggregate_delivery(df, {"Daily": "D", "Weekly": "W", "Monthly": "M"}[mode])
    show = agg.tail({"Daily": 30, "Weekly": 16, "Monthly": 12}[mode]).iloc[::-1]
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Bar(y=show["label"], x=show["volume"], orientation="h", name="Traded qty", marker_color="#2A3F5F"))
        fig.add_trace(go.Bar(y=show["label"], x=show["deliv_qty"], orientation="h", name="Delivered qty", marker_color=T["cyan"],
                             text=[("" if pd.isna(v) else f"{v:.1f}%") for v in show["deliv_pct"]], textposition="outside"))
        fig.update_yaxes(autorange="reversed")
        _fig(fig, T, 70 + 26 * len(show), f"{key}_dlv_bar_{mode}", barmode="overlay")
    except Exception:
        pass
    rows = [{"label": r["label"], "values": [_qty(r["deliv_qty"]), _qty(r["volume"]), _pct(None if pd.isna(r["deliv_pct"]) else r["deliv_pct"]),
                                             _rs(None if pd.isna(r["close"]) else r["close"]),
                                             _pct(None if pd.isna(r["chg_pct"]) else r["chg_pct"], 1, True)]} for _, r in show.iterrows()]
    _table(["DELIVERED QTY", "TRADED QTY", "DELIVERY %", "CLOSE", "CHANGE"], rows, T, dl=f"{sym}_delivery_{mode.lower()}_{key}")

    top = df.dropna(subset=["deliv_pct"]).sort_values("deliv_pct", ascending=False).head(5)
    if len(top):
        _header("Highest delivery days in this window", T)
        _table(["DELIVERY %", "DELIVERED QTY", "CLOSE", "DAY CHANGE"],
               [{"label": str(i)[:10], "values": [_pct(r["deliv_pct"]), _qty(r["deliv_qty"]), _rs(r["close"]),
                                                  _pct((r["close"] / r["prev_close"] - 1) * 100 if r["prev_close"] and r["prev_close"] == r["prev_close"] else None, 1, True)]}
                for i, r in top.iterrows()], T)
    st.caption("High delivery means more of the day's trading was taken for delivery. It does not show who bought or sold, "
               "or whether they were buying or selling. Compare a day with the stock's own average, not with other stocks.")


# ---------- FH: financial health ----------
def _fin_health(sym, B, T, key, nf):
    y, bs, cf, rt = B.get("yearly"), B.get("balance_sheet"), B.get("cash_flow"), B.get("ratios")
    ybs, ycf = yf_attr(sym, "balance_sheet"), yf_attr(sym, "cashflow")
    info = yf_attr(sym, "info") or {}
    de = _debt_equity(B)
    lender = _row(y, "Financing Profit", "Financing Margin") is not None
    op, it = _last_num(_nums(_row(y, "Operating Profit"))), _last_num(_nums(_row(y, "Interest")))
    cov = op / it if (not lender and op is not None and it and it > 0) else None
    ca = _yv(ybs, ["Current Assets", "Total Current Assets"])
    cl = _yv(ybs, ["Current Liabilities", "Total Current Liabilities"])
    cr = ca / cl if ca is not None and cl else None
    fcf = _yv(ycf, ["Free Cash Flow"])
    td, cash, ebitda = info.get("totalDebt"), info.get("totalCash"), info.get("ebitda")
    nde = (td - cash) / ebitda if all(isinstance(v, (int, float)) for v in (td, cash, ebitda)) and ebitda and ebitda > 0 else None
    npm, cfo = _annual_map(y, "Net Profit", "Profit for the period"), _annual_map(cf, "Cash from Operating", "Operating")
    common = [p for p in (y or {}).get("periods", []) if p in npm and p in cfo][-3:]
    conv = sum(cfo[p] for p in common) / sum(npm[p] for p in common) * 100 if len(common) == 3 and sum(npm[p] for p in common) > 0 else None
    _header("Financial health · balance-sheet strength and cash quality", T, "live" if B.get("resolved") else "unavailable")
    _kpis([("Debt / equity", f"{de}x" if de is not None else "N/A", "Borrowings ÷ (equity + reserves) · Screener"),
           ("Interest coverage", f"{cov:.1f}x" if cov is not None else "N/A",
            "Not meaningful for lenders" if lender else "Operating profit ÷ interest, latest year · Screener"),
           ("Current ratio", f"{cr:.2f}x" if cr is not None else "N/A", "Current assets ÷ current liabilities · Yahoo"),
           ("Net debt / EBITDA", f"{nde:.1f}x" if nde is not None else "N/A", "(Debt − cash) ÷ EBITDA · Yahoo"),
           ("Free cash flow", f"₹{fcf / 1e7:,.0f} Cr" if fcf is not None else "N/A", "Latest year · Yahoo"),
           ("Cash conversion, 3 yrs", _pct(conv, 0), "Operating cash flow ÷ net profit · Screener")], T, 3)
    extra = []
    for lab in ("Debtor Days", "Inventory Days", "Days Payable", "Cash Conversion Cycle", "Working Capital Days", "ROCE"):
        r = _row(rt, lab)
        v = [x for x in (r or {}).get("values", []) if x not in ("—", "")]
        if r and len(v) >= 2:
            extra.append({"label": r["label"], "values": [v[-2], v[-1]]})
    if extra:
        _header("Working capital · previous year → latest", T)
        _table(["PREVIOUS", "LATEST"], extra, T, ["Cash Conversion", "ROCE"])
    if bs and bs.get("rows"):
        _header("Balance sheet trend · ₹ Cr", T)
        keep = [r for lab in ("Borrowings", "Reserves", "Total Assets") if (r := _row(bs, lab))]
        seen, rows = set(), []
        for r in keep:
            if r["label"] not in seen:
                seen.add(r["label"])
                rows.append({"label": r["label"], "values": r["values"][-6:]})
        _table(bs["periods"][-6:], rows, T, ["Borrowings"])
    st.caption("Ratios are shown without a verdict: what is healthy differs by industry. Compare with peers (RV) and with the company's own past.")


# ---------- ERN: earnings trend ----------
def _earnings_trend(sym, B, T, key, nf):
    q = B.get("quarterly")
    if not q or not q.get("rows"):
        _unavailable("Earnings trend", "Screener quarterly results", T, B.get("reason"))
        return
    per = q["periods"]

    def yoy(vals):
        return [((vals[i] / vals[i - 4] - 1) * 100 if i >= 4 and vals[i] is not None and vals[i - 4] and vals[i - 4] > 0 else None)
                for i in range(len(vals))]

    sales, npf = _nums(_row(q, "Sales", "Revenue")), _nums(_row(q, "Net Profit", "Profit for the period"))
    eps, opm = _nums(_row(q, "EPS")), _nums(_row(q, "OPM", "Financing Margin"))
    ys, yn, ye = yoy(sales), yoy(npf), yoy(eps)
    n = min(8, len(per))
    cut = lambda a: (a + [None] * len(per))[:len(per)][-n:]
    f0 = lambda a: [("—" if v is None else f"{v:,.0f}") for v in cut(a)]
    f2 = lambda a: [("—" if v is None else f"{v:,.2f}") for v in cut(a)]
    fp = lambda a: [("—" if v is None else f"{v:+.1f}%") for v in cut(a)]
    wins = [v for v in cut(yn) if v is not None]
    ttm = lambda a: (sum(a[-4:]), sum(a[-8:-4])) if len(a) >= 8 and all(v is not None for v in a[-8:]) else (None, None)
    (s1, s0), (p1, p0) = ttm(sales), ttm(npf)
    o = [v for v in opm if v is not None]
    _header("Earnings trend · last quarters vs the same quarter a year earlier", T, "live")
    _kpis([("Sales, last 4 quarters", _pct((s1 / s0 - 1) * 100 if s1 is not None and s0 and s0 > 0 else None, 1, True), "vs the 4 quarters before"),
           ("Net profit, last 4 quarters", _pct((p1 / p0 - 1) * 100 if p1 is not None and p0 and p0 > 0 else None, 1, True), "vs the 4 quarters before"),
           ("Profit up year-on-year", f"{sum(1 for v in wins if v > 0)} of {len(wins)}" if wins else "N/A", "quarters shown"),
           ("Operating margin", f"{o[-1]:.0f}%" if o else "N/A", f"{o[-5]:.0f}% a year ago" if len(o) >= 5 else "")], T, 4)
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Bar(x=per[-n:], y=cut(ys), name="Sales YoY %", marker_color=T["cyan"]))
        fig.add_trace(go.Bar(x=per[-n:], y=cut(yn), name="Net profit YoY %", marker_color=T["green"]))
        _fig(fig, T, 280, f"{key}_ern_bar", barmode="group")
    except Exception:
        pass
    _table(per[-n:], [{"label": "Sales ₹ Cr", "values": f0(sales)}, {"label": "Sales YoY", "values": fp(ys)},
                      {"label": "Net profit ₹ Cr", "values": f0(npf)}, {"label": "Net profit YoY", "values": fp(yn)},
                      {"label": "EPS ₹", "values": f2(eps)}, {"label": "EPS YoY", "values": fp(ye)},
                      {"label": "Operating margin %", "values": f0(opm)}], T, ["YoY"], dl=f"{sym}_earnings_trend_{key}")
    st.caption("YoY compares each quarter with the same quarter a year earlier, which removes seasonality. "
               "Result dates and the price reaction to each result are not available from this source.")


# ---------- QR: quant ----------
def _quant(sym, B, T, key, nf):
    h = _hist(sym, "2y", "1y")
    if h is None:
        _unavailable("Quant", "Yahoo daily price history", T)
        return
    s = _naive(h["Close"].astype(float))
    bn = {}
    for name, tk in (("NIFTY 500", "^CRSLDX"), ("NIFTY 50", "^NSEI")):
        ix = _index_history(tk)
        if ix is not None and len(ix) > 60:
            bn[name] = _naive(ix["Close"].astype(float))
    if not bn:
        _unavailable("Benchmarks", "Yahoo history for NIFTY 500 / NIFTY 50", T)
        return
    main = "NIFTY 500" if "NIFTY 500" in bn else "NIFTY 50"
    rows = []
    for lab, k in (("5 sessions", 5), ("20 sessions", 20), ("60 sessions", 60), ("120 sessions", 120), ("252 sessions", 252)):
        vals = []
        for name in ("NIFTY 500", "NIFTY 50"):
            if name not in bn:
                vals.append("N/A")
                continue
            df = pd.concat([s.rename("s"), bn[name].rename("b")], axis=1).dropna()
            if len(df) <= k:
                vals.append("—")
                continue
            rs_, rb = (df["s"].iloc[-1] / df["s"].iloc[-1 - k] - 1) * 100, (df["b"].iloc[-1] / df["b"].iloc[-1 - k] - 1) * 100
            if not vals:
                vals.insert(0, f"{rs_:+.1f}%")
            vals.append(f"{rs_ - rb:+.1f} pp")
        while len(vals) < 3:
            vals.insert(0, "—")
        rows.append({"label": lab, "values": vals[:3]})
    _header("Relative strength · stock return minus index return", T, "live")
    _table([sym, "vs NIFTY 500", "vs NIFTY 50"], rows, T, ["60 sessions"])

    df = pd.concat([s.rename("s"), bn[main].rename("b")], axis=1).dropna()
    ratio = df["s"] / df["b"]
    sma = ratio.rolling(50).mean()
    trend = "N/A"
    if len(sma.dropna()) > 21:
        up, rising = ratio.iloc[-1] > sma.iloc[-1], sma.iloc[-1] > sma.iloc[-21]
        trend = "UP" if up and rising else "DOWN" if (not up and not rising) else "MIXED"
    w = df.tail(252)
    r = w.pct_change().dropna()
    vol20 = float(r["s"].tail(20).std() * math.sqrt(252) * 100) if len(r) >= 20 else None
    vol60 = float(r["s"].tail(60).std() * math.sqrt(252) * 100) if len(r) >= 60 else None
    beta = float(r["s"].cov(r["b"]) / r["b"].var()) if len(r) >= 60 and r["b"].var() else None
    dd = w["s"] / w["s"].cummax() - 1
    mom = (s.iloc[-22] / s.iloc[-253] - 1) * 100 if len(s) > 253 else None
    upd = float((r["s"].tail(60) > 0).mean() * 100) if len(r) >= 60 else None
    _header(f"Relative-strength line · {sym} ÷ {main}", T, note="Rising line = the stock is beating the index. Trend uses the 50-session average, as in Money Flow.")
    try:
        import plotly.graph_objects as go
        x = (ratio / ratio.iloc[-min(252, len(ratio))] * 100).tail(252)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x.index, y=x.values, name="RS line (100 = a year ago)", line=dict(color=T["amber"], width=2)))
        sm = (sma / ratio.iloc[-min(252, len(ratio))] * 100).tail(252)
        fig.add_trace(go.Scatter(x=sm.index, y=sm.values, name="50-session average", line=dict(color=T["cyan"], width=1)))
        _fig(fig, T, 280, f"{key}_qr_rs")
    except Exception:
        pass
    tc = T["green"] if trend == "UP" else T["red"] if trend == "DOWN" else None
    _kpis([("RS trend", trend, f"vs {main}", tc),
           ("Momentum 12-1", _pct(mom, 1, True), "12 months ago → 1 month ago"),
           ("Volatility 20 / 60", f"{vol20:.0f}% / {vol60:.0f}%" if vol20 is not None and vol60 is not None else "N/A", "annualised, daily returns"),
           ("Beta", f"{beta:.2f}" if beta is not None else "N/A", f"vs {main}, 1 year"),
           ("Drawdown now / max 1Y", f"{dd.iloc[-1] * 100:.1f}% / {dd.min() * 100:.1f}%", "from the 1-year high", T["red"]),
           ("Up days, last 60", _pct(upd, 0), "share of sessions that closed higher")], T, 3)
    st.caption("Descriptive statistics from daily closes. Momentum 12-1 skips the latest month, the standard academic definition.")


# ---------- CACS: exchange filings from BSE ----------
def _filings(sym, B, T, key, nf):
    code = rd.bse_code(B.get("url")) if rd is not None else None
    anns = rd.bse_announcements(code, 180) if code else []
    if not anns:
        return _announcements_scanx(sym, B, T, key, nf)
    _header(f"Exchange filings · BSE (scrip {code}) · {len(anns)} in 180 days", T, "live")
    cats = ["All"] + sorted({a["category"] for a in anns})
    c1, c2 = st.columns([1, 2])
    cat = c1.selectbox("Category", cats, key=f"{key}_cacs_cat", label_visibility="collapsed")
    q = c2.text_input("Filter filings", key=f"{key}_cacs_q", placeholder="Filter… e.g. result, dividend, order, board meeting", label_visibility="collapsed")
    if cat != "All":
        anns = [a for a in anns if a["category"] == cat]
    if q:
        anns = [a for a in anns if q.lower() in (a["title"] + " " + a["sub"]).lower()]
    for a in anns[:80]:
        url = html.escape(a["url"], quote=True)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:12.5px/1.65 var(--rp-s)">'
                    f'<a href="{url}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(a["title"])}</a>'
                    f'<div style="color:{T["t3"]};margin-top:3px">{_esc(a["dt"])} · <span style="color:{T["cyan"]}">{_esc(a["category"])}</span>'
                    f'{" · " + _esc(a["sub"]) if a["sub"] else ""} · Open document ↗</div></div>', unsafe_allow_html=True)


# ---------- SYN: research intelligence ----------
_SYN_SYSTEM = ("You are the research interpretation layer of Arka Trades, an Indian equity research terminal. "
               "Use ONLY the facts in the JSON you are given. Do not add any number, price, target, date or outside knowledge. "
               "Do not give a buy, sell or hold recommendation. Write three short sections with these exact headings: "
               "FACT (what the numbers show), INFERENCE (what they suggest together, including each listed contradiction and what could explain it), "
               "UNKNOWN (what is missing and why it matters). Plain English, under 220 words.")


def _synthesis(sym, B, T, key, nf):
    if rd is None:
        _unavailable("Research intelligence", "research_data.py next to research_page.py", T)
        return
    h = _hist(sym, "5y", "2y")
    close = _naive(h["Close"].astype(float)) if h is not None else None
    pe, pct = _pe_stats(B, close) if close is not None else (None, None)
    E = rd.evidence(B, close, pe, pct, st.session_state.get(f"rp_dlv_last_{sym}"))
    con = E["contradictions"]
    _header(f"Contradictions · {len(con)} found", T, "live" if E["facts"] else "unavailable",
            "Places where two measurements point in different directions. Rules are fixed and shown; nothing here is a forecast.")
    if con:
        _panel("Where the evidence disagrees", _checklist([(c["name"], False, c["numbers"]) for c in con], T, ("", "⚠", "")), T)
    else:
        _panel("No contradiction rule triggered", f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]}">Checked: valuation vs profit, price vs profit, '
               "sales vs margin, profit vs cash flow, price vs promoter holding, price vs delivery share.</div>", T)
    _header("Evidence sheet · every fact with its source", T)
    _table(["VALUE", "SOURCE"], [{"label": f["label"], "values": [f["value"], f["source"]]} for f in E["facts"]], T, dl=f"{sym}_evidence_{key}")
    _panel("Not available", f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]};line-height:1.7">{_esc("; ".join(E["unknown"]))}.'
           + ("" if st.session_state.get(f"rp_dlv_last_{sym}") is not None else " Open DLV once to add delivery data to this sheet.") + "</div>", T, dashed=True)

    _header("AI synthesis · Gemini reads the evidence sheet above, nothing else", T)
    sk = f"rp_syn_{sym}"
    if st.button("Generate synthesis", key=f"{key}_syn_btn", type="primary"):
        payload = {"company": B.get("name", sym), "symbol": sym, "as_of": datetime.now(IST).strftime("%d %b %Y"),
                   "facts": E["facts"], "contradictions": con, "unknown": E["unknown"]}
        with st.spinner("Asking Gemini…"):
            st.session_state[sk] = rd.gemini_generate(json.dumps(payload, ensure_ascii=False), _SYN_SYSTEM)
    res = st.session_state.get(sk)
    if res and res.get("text"):
        st.markdown(res["text"])
        st.caption(f"Written by {res.get('model')} from the evidence sheet only. AI text can be wrong: check it against the numbers above. Not investment advice.")
    elif res:
        _unavailable("AI synthesis", "GEMINI_KEY in Streamlit secrets", T, res.get("error"))


def _announcements_scanx(sym, B, T, key, nf):
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
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:12.5px/1.65 var(--rp-s)"><a href="{url}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(a.get("title"))}</a>'
                    f'<div style="color:{T["t3"]};margin-top:3px">Open source document ↗</div></div>', unsafe_allow_html=True)


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
    store = rd is not None and rd.sb_config() is not None
    uid, lk = _user_id(), f"{key}_note_loaded_{sym}"
    if store and not st.session_state.get(lk):
        st.session_state[lk] = True
        try:
            saved = rd.note_load(uid, sym)
            pl = (saved or {}).get("payload") or {}
            if pl.get("stance") in ("—", "Bullish", "Neutral", "Bearish"):
                st.session_state[f"{key}_note_stance_{sym}"] = pl["stance"]
            if isinstance(pl.get("tp"), (int, float)):
                st.session_state[f"{key}_note_tp_{sym}"] = float(max(0.0, pl["tp"]))
            if isinstance(pl.get("conv"), (int, float)) and 1 <= int(pl["conv"]) <= 5:
                st.session_state[f"{key}_note_conv_{sym}"] = int(pl["conv"])
            for k2 in ("bull", "bear", "cat", "risk"):
                if isinstance(pl.get(k2), str):
                    st.session_state[f"{key}_note_{k2}_{sym}"] = pl[k2]
            if saved:
                st.session_state[f"{key}_note_at_{sym}"] = str(saved.get("updated_at", ""))[:16].replace("T", " ")
        except Exception as e:
            st.session_state[f"{key}_note_err_{sym}"] = f"{type(e).__name__}: {e}"[:200]
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
    st.download_button("Download note (.txt)", txt, file_name=f"{sym}_research_note.txt", key=f"{key}_note_dl_{sym}")
    if store:
        if st.button("Save note", key=f"{key}_note_save_{sym}", type="primary"):
            try:
                rd.note_save(uid, sym, {k2: ss.get(f"{key}_note_{k2}_{sym}") for k2 in ("stance", "tp", "conv", "bull", "bear", "cat", "risk")})
                st.session_state[f"{key}_note_at_{sym}"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
                st.session_state.pop(f"{key}_note_err_{sym}", None)
            except Exception as e:
                st.session_state[f"{key}_note_err_{sym}"] = f"{type(e).__name__}: {e}"[:200]
        err = ss.get(f"{key}_note_err_{sym}")
        if err:
            st.caption(f"Supabase problem: {err}. Check that the research_notes table exists (supabase_setup.sql).")
        else:
            st.caption(f"Notes are saved in Supabase for user '{uid}'. Last saved: {ss.get(f'{key}_note_at_{sym}') or 'not yet'}.")
    else:
        st.caption("Notes live in this browser session. Add SUPABASE_URL and SUPABASE_KEY to Streamlit secrets to save them.")


def _sources(sym, B, T, key, nf):
    _header("Data provenance", T)
    _table(["SOURCE", "STATUS"], [
        {"label": "Fundamentals (financials, ratios, holding)", "values": ["Screener.in", "live" if B.get("resolved") else "unavailable"]},
        {"label": "Prices / charts / technicals / seasonality", "values": ["Yahoo Finance", "live"]},
        {"label": f"Benchmark ({BENCHMARK[1]})", "values": [f"Yahoo {BENCHMARK[0]}", "on demand"]},
        {"label": "Health check, valuation, P/E band", "values": ["Computed from the above", "on demand"]},
        {"label": "Analyst / dividends / fallbacks", "values": ["Yahoo Finance", "on demand"]},
        {"label": "News", "values": ["news_feed.py / Yahoo", "on demand"]},
        {"label": "Delivery (DLV)", "values": ["NSE daily bhavcopy", "on demand"]},
        {"label": "Announcements (CACS)", "values": ["BSE API, ScanX fallback", "on demand"]},
        {"label": "MF holdings", "values": ["ScanX (optional)", "on demand"]},
        {"label": "Storage for notes and delivery history", "values": ["Supabase", "configured" if rd and rd.sb_config() else "not configured"]},
        {"label": "AI synthesis (SYN)", "values": ["Gemini", "key found" if rd and rd._secret("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY") else "no key"]},
    ] + [{"label": f"Last call · {k}", "values": [v["detail"] or "—", ("OK" if v["ok"] else "FAILED") + " · " + v["at"]]}
         for k, v in (rd.status() if rd else {}).items()], T)
    _panel("Integrity rule", f'<div style="font:12.5px/1.65 var(--rp-s);color:{T["t2"]};line-height:1.8">Values are shown only when a source returns them '
           f'or when they are computed directly from returned values (formula shown on screen). Missing data says DATA UNAVAILABLE; nothing is estimated or invented.</div>', T)


# ═══════════════════════════ v14 functions ═══════════════════════════
_IDX = {"NIFTY 500": "^CRSLDX", "NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK", "NIFTY IT": "^CNXIT", "NIFTY MIDCAP 100": "NIFTY_MIDCAP_100.NS"}


def _bench(name="NIFTY 500"):
    ix = _index_history(_IDX.get(name, name))
    return _naive(ix["Close"].astype(float)) if ix is not None and len(ix) > 30 else None


def _close(sym, period="5y", fallback="2y"):
    h = _hist(sym, period, fallback)
    return _naive(h["Close"].astype(float)) if h is not None else None


def _ret(s, n):
    return (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100 if s is not None and len(s) > n else None


# ---------- OV: dashboard ----------
def _dashboard(sym, B, T, key, nf):
    close = _close(sym, "2y", "1y")
    b500 = _bench("NIFTY 500")
    if b500 is None:
        b500 = _bench("NIFTY 50")
    pe, pct = _pe_stats(B, _close(sym)) if close is not None else (None, None)
    E = rd.evidence(B, close, pe, pct, st.session_state.get(f"rp_dlv_last_{sym}")) if rd is not None else {"facts": [], "contradictions": [], "unknown": []}
    F = {f["label"]: f["value"] for f in E["facts"]}
    s = B.get("summary") or {}

    left, right = st.columns([2.1, 1], gap="small")
    with left:
        drawn = False
        try:
            from chart_engine import render_pro_chart
            drawn = bool(render_pro_chart(sym, height=520))
        except Exception:
            drawn = False
        if not drawn and close is not None:
            c = close.tail(252)
            _panel("Price · 1 year", _spark(c.tolist(), T["green"] if c.iloc[-1] >= c.iloc[0] else T["red"], 600, 150), T)
    with right:
        rows = []
        for lab, n in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 252)):
            r = _ret(close, n)
            if r is None:
                continue
            rb = None
            if b500 is not None:
                df = pd.concat([close.rename("s"), b500.rename("b")], axis=1).dropna()
                rb = _ret(df["b"], n) if len(df) > n else None
            rows.append((lab, f"{r:+.1f}%" + (f"  ·  {r - rb:+.1f} pp" if rb is not None else "")))
        _mini("Performance · vs NIFTY 500", rows or [("Price history", "N/A")], T, "QR")
        bv, price = _f(s.get("book_value")), (float(close.iloc[-1]) if close is not None else None)
        _mini("Valuation", [("P/E (TTM)", f"{pe:.1f}x" if pe else "N/A"),
                            ("vs own history", f"{pct:.0f}th percentile" if pct is not None else "N/A"),
                            ("P/B", f"{price / bv:.2f}x" if price and bv else "N/A"),
                            ("Earnings yield", f"{100 / pe:.2f}%" if pe else "N/A"),
                            ("Dividend yield", f'{s["dividend_yield"]}%' if s.get("dividend_yield") else "N/A")], T, "VAL")
        hh = _hist(sym, "2y", "1y")
        if hh is not None and len(hh) > 60:
            c_, v_ = hh["Close"].astype(float), hh["Volume"].astype(float)
            trade = [("Volume vs 20-session average", f"{v_.iloc[-1] / v_.tail(20).mean():.2f}x" if v_.tail(20).mean() else "N/A"),
                     ("Volatility, 20 sessions", f"{c_.pct_change().tail(20).std() * math.sqrt(252) * 100:.0f}% a year"),
                     ("RSI 14", f"{float(rsi_wilder(c_, 14).iloc[-1]):.0f}")]
            for n in (50, 200):
                if len(c_) >= n:
                    trade.append((f"vs {n}-day average", f"{(c_.iloc[-1] / c_.tail(n).mean() - 1) * 100:+.1f}%"))
            _mini("Trading", trade, T, "TECH")
        _pb_mini(sym, T)

    a, b, c = st.columns(3, gap="small")
    with a:
        res = _piotroski(B)
        sc = "N/A"
        if res:
            av = [x for x in res[0] if x[1] is not None]
            sc = f"{sum(1 for x in av if x[1])} / {len(av)}"
        de = _debt_equity(B)
        _mini("Quality", [("Piotroski-style score", sc), ("ROE", f'{s["roe"]}%' if s.get("roe") else "N/A"),
                          ("ROCE", f'{s["roce"]}%' if s.get("roce") else "N/A"), ("Debt / equity", f"{de}x" if de is not None else "N/A"),
                          ("Cash flow ÷ profit, 3 yrs", F.get("Operating cash flow as % of net profit, 3 years", "N/A"))], T, "HC")
    with b:
        _mini("Earnings", [("Sales, last 4 quarters", F.get("Sales, last 4 quarters vs previous 4", "N/A")),
                           ("Net profit, last 4 quarters", F.get("Net profit, last 4 quarters vs previous 4", "N/A")),
                           ("Operating margin", F.get("Operating margin, latest quarter vs a year ago", "N/A")),
                           ("Price, 1 year", F.get("Price change, 1 year", "N/A"))], T, "ERN")
    with c:
        _mini("Ownership", [("Promoters", F.get("Promoter holding", "N/A")), ("FIIs", F.get("FII holding", "N/A")), ("DIIs", F.get("DII holding", "N/A")),
                            ("Delivery share", F.get("Delivery share of traded quantity, last 10 sessions vs before", "open DLV to load"))], T, "HDS")

    l2, r2 = st.columns(2, gap="small")
    with l2:
        con = E["contradictions"]
        _panel(f"Contradictions · {len(con)}" , (_checklist([(x["name"], False, x["numbers"]) for x in con], T, ("", "⚠", "")) if con else
               "No contradiction rule triggered. Checked: valuation vs profit, price vs profit, sales vs margin, profit vs cash, price vs promoters, price vs delivery."), T)
    with r2:
        code = rd.bse_code(B.get("url")) if rd is not None else None
        anns = rd.bse_announcements(code, 90)[:6] if code else []
        if anns:
            body = "".join(f'<div class="rp-fil"><a href="{html.escape(x["url"], quote=True)}" target="_blank">{_esc(x["title"][:110])}</a>'
                           f'<div>{_esc(x["dt"])} · {_esc(x["category"])}</div></div>' for x in anns)
        else:
            body = "No BSE filings returned. Open CACS for details."
        _panel("Latest exchange filings", body, T)


def _graph(sym, B, T, key, nf):
    try:
        from chart_engine import render_pro_chart
        render_pro_chart(sym, height=640)
    except Exception as e:
        _unavailable("Chart", "chart_engine.py", T, str(e))


# ---------- HP: historical prices ----------
def _hist_prices(sym, B, T, key, nf):
    h = _hist(sym, "5y", "2y")
    if h is None:
        _unavailable("Historical prices", "Yahoo daily price history", T)
        return
    rng = st.radio("Range", ["1M", "3M", "6M", "1Y", "5Y"], index=1, horizontal=True, key=f"{key}_hp_rng", label_visibility="collapsed")
    d = h.copy()
    d.index = _naive(h["Close"]).index if len(_naive(h["Close"])) == len(h) else pd.to_datetime(h.index)
    d = d.tail({"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "5Y": len(d)}[rng])
    chg = d["Close"].pct_change() * 100
    _header(f"Historical prices · {len(d)} sessions · Yahoo, unadjusted", T, "live")
    _kpis([("High", _rs(float(d["High"].max())), str(d["High"].idxmax())[:10]), ("Low", _rs(float(d["Low"].min())), str(d["Low"].idxmin())[:10]),
           ("Average close", _rs(float(d["Close"].mean())), f"{rng} window"),
           ("Change", _pct((d["Close"].iloc[-1] / d["Close"].iloc[0] - 1) * 100, 1, True), "first → last close",
            T["green"] if d["Close"].iloc[-1] >= d["Close"].iloc[0] else T["red"]),
           ("Average volume", _qty(float(d["Volume"].mean())), "shares per session")], T, 5)
    show = d.iloc[::-1].head(90)
    _table(["OPEN", "HIGH", "LOW", "CLOSE", "CHANGE", "VOLUME"],
           [{"label": str(i)[:10], "values": [f"{r['Open']:,.2f}", f"{r['High']:,.2f}", f"{r['Low']:,.2f}", f"{r['Close']:,.2f}",
                                              _pct(None if pd.isna(chg.loc[i]) else float(chg.loc[i]), 2, True), _qty(r["Volume"])]}
            for i, r in show.iterrows()], T)
    out = d[["Open", "High", "Low", "Close", "Volume"]].copy()
    out.index = [str(i)[:10] for i in out.index]
    st.download_button("Download CSV · full range", out.to_csv(index_label="Date"), file_name=f"{sym}_prices_{rng}.csv", mime="text/csv", key=f"{key}_hp_dl")


# ---------- COMP: comparative returns ----------
def _compare(sym, B, T, key, nf):
    import plotly.graph_objects as go
    default = ", ".join(p for p in _SECTOR_PEERS.get(sym, []) if p != sym) or "NIFTY 500, NIFTY 50"
    c1, c2 = st.columns([3, 1])
    raw = c1.text_input("Compare with", value=default, key=f"{key}_comp_q_{sym}", label_visibility="collapsed",
                        placeholder="Symbols or indices, comma separated: TCS, INFY, NIFTY 500, NIFTY IT")
    rng = c2.radio("Range", ["3M", "6M", "1Y", "2Y"], index=2, horizontal=True, key=f"{key}_comp_rng", label_visibility="collapsed")
    n = {"3M": 63, "6M": 126, "1Y": 252, "2Y": 504}[rng]
    names = [sym] + [t.strip().upper() for t in raw.split(",") if t.strip()][:6]
    series, missing = {}, []
    for nm in dict.fromkeys(names):
        s = _bench(nm) if nm in _IDX else _close(nm, "2y", "1y")
        if s is None or len(s) < 30:
            missing.append(nm)
        else:
            series[nm] = s
    if sym not in series or len(series) < 2:
        _unavailable("Comparison", "Price history for the stock and at least one other name", T, f"No data for: {', '.join(missing) or 'other names'}")
        return
    df = pd.concat(series, axis=1).dropna().tail(n + 1)
    reb = df / df.iloc[0] * 100
    _header(f"Rebased performance · {rng} = 100 · {len(df) - 1} common sessions", T, "live",
            f"No data for: {', '.join(missing)}" if missing else None)
    pal = [T["amber"], T["cyan"], T["green"], T["purple"], "#FFD60A", T["red"], "#B8B8B8"]
    fig = go.Figure()
    for i, col in enumerate(reb.columns):
        fig.add_trace(go.Scatter(x=reb.index, y=reb[col], name=col, line=dict(color=pal[i % len(pal)], width=2.4 if col == sym else 1.4)))
    _fig(fig, T, 340, f"{key}_comp_chart")
    r = df.pct_change().dropna()
    rows = []
    for col in df.columns:
        tot = (df[col].iloc[-1] / df[col].iloc[0] - 1) * 100
        rows.append({"label": col, "values": [f"{tot:+.1f}%", f"{tot - (df[sym].iloc[-1] / df[sym].iloc[0] - 1) * 100:+.1f} pp" if col != sym else "—",
                                              f"{r[col].std() * math.sqrt(252) * 100:.0f}%", f"{(df[col] / df[col].cummax() - 1).min() * 100:.1f}%",
                                              f"{r[col].corr(r[sym]):.2f}" if col != sym else "1.00"]})
    _table(["RETURN", f"VS {sym}", "VOLATILITY", "MAX DRAWDOWN", f"CORRELATION WITH {sym}"], rows, T, [sym])
    st.caption("Index names accepted: " + ", ".join(_IDX) + ". Prices are unadjusted closes; dividends are not included (see TRA).")


# ---------- BETA: regression ----------
def _beta(sym, B, T, key, nf):
    import plotly.graph_objects as go
    c1, c2 = st.columns(2)
    bname = c1.radio("Index", ["NIFTY 500", "NIFTY 50"], horizontal=True, key=f"{key}_beta_ix", label_visibility="collapsed")
    win = c2.radio("Window", ["1Y", "2Y"], horizontal=True, key=f"{key}_beta_win", label_visibility="collapsed")
    s, b = _close(sym, "2y", "1y"), _bench(bname)
    if s is None or b is None:
        _unavailable("Beta", f"Price history for {sym} and {bname}", T)
        return
    df = pd.concat([s.rename("s"), b.rename("b")], axis=1).dropna().tail({"1Y": 253, "2Y": 505}[win])
    r = df.pct_change().dropna() * 100
    if len(r) < 60:
        _unavailable("Beta", "At least 60 common sessions", T)
        return
    x, y = r["b"].to_numpy(), r["s"].to_numpy()
    beta, alpha = [float(v) for v in __import__("numpy").polyfit(x, y, 1)]
    corr = float(r["s"].corr(r["b"]))
    resid = y - (alpha + beta * x)
    se = float(resid.std(ddof=2) / (x.std(ddof=1) * math.sqrt(len(x))))
    _header(f"Regression of daily returns · {sym} on {bname} · {len(r)} sessions", T, "live")
    _kpis([("Beta", f"{beta:.2f}", f"± {1.96 * se:.2f} (95% range)"), ("Alpha, annualised", _pct(alpha * 252, 1, True), "return not explained by the index"),
           ("R²", f"{corr * corr:.2f}", "share of moves explained by the index"), ("Correlation", f"{corr:.2f}", ""),
           ("Stock-specific volatility", f"{resid.std() * math.sqrt(252):.0f}%", "annualised, after removing the index")], T, 5)
    a, c = st.columns(2, gap="small")
    with a:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="Daily returns %", marker=dict(color=T["cyan"], size=4, opacity=0.55)))
        xs = [float(x.min()), float(x.max())]
        fig.add_trace(go.Scatter(x=xs, y=[alpha + beta * v for v in xs], mode="lines", name=f"Fit · beta {beta:.2f}", line=dict(color=T["amber"], width=2)))
        _fig(fig, T, 300, f"{key}_beta_sc")
    with c:
        rb = (r["s"].rolling(60).cov(r["b"]) / r["b"].rolling(60).var()).dropna()
        fig = go.Figure(go.Scatter(x=rb.index, y=rb.values, name="Rolling 60-session beta", line=dict(color=T["amber"], width=1.8)))
        _fig(fig, T, 300, f"{key}_beta_roll")
    st.caption("Beta above 1 means the stock has moved more than the index. It describes the past window only.")


# ---------- HVG: historical volatility ----------
def _volatility(sym, B, T, key, nf):
    import plotly.graph_objects as go
    h = _hist(sym, "5y", "2y")
    if h is None or len(h) < 130:
        _unavailable("Volatility", "At least 130 sessions of price history", T)
        return
    c = _naive(h["Close"].astype(float))
    r = c.pct_change()
    v = {n: r.rolling(n).std() * math.sqrt(252) * 100 for n in (20, 60, 120)}
    cur = float(v[20].iloc[-1])
    hist2y = v[20].dropna().tail(504)
    pctl = float((hist2y < cur).mean() * 100)
    hi, lo, cl = h["High"].astype(float), h["Low"].astype(float), h["Close"].astype(float)
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    big = r.tail(252).abs()
    _header("Realised volatility · annualised, from daily closes", T, "live")
    _kpis([("20-session", f"{cur:.0f}%", f"{pctl:.0f}th percentile of the last 2 years"), ("60-session", f"{v[60].iloc[-1]:.0f}%", ""),
           ("120-session", f"{v[120].iloc[-1]:.0f}%", ""), ("ATR 14", _rs(atr), f"{atr / float(cl.iloc[-1]) * 100:.2f}% of price"),
           ("Days moving 3%+", f"{int((big >= 0.03).sum())}", "in the last 252 sessions"),
           ("Largest 1-day move", _pct(float(r.tail(252).loc[big.idxmax()] * 100), 1, True), str(big.idxmax())[:10])], T, 3)
    fig = go.Figure()
    for n, col in ((20, T["amber"]), (60, T["cyan"]), (120, T["purple"])):
        x = v[n].dropna().tail(504)
        fig.add_trace(go.Scatter(x=x.index, y=x.values, name=f"{n}-session", line=dict(color=col, width=2 if n == 20 else 1.3)))
    _fig(fig, T, 320, f"{key}_hvg_chart")
    st.caption("Volatility is how much the price has moved, not which way. A low reading after a long calm period often precedes a bigger move, in either direction.")


# ---------- TRA: total return ----------
def _total_return(sym, B, T, key, nf):
    h = _hist(sym, "5y", "2y")
    if h is None:
        _unavailable("Total return", "Yahoo daily price history", T)
        return
    c = _naive(h["Close"].astype(float))
    dv = yf_attr(sym, "dividends")
    d = _naive(dv.astype(float)) if dv is not None and len(dv) else pd.Series(dtype=float)
    d = d.groupby(d.index).sum().reindex(c.index).fillna(0.0) if len(d) else pd.Series(0.0, index=c.index)
    tr = ((c + d) / c.shift(1)).fillna(1.0).cumprod()
    rows = []
    for lab, n in (("1 year", 252), ("3 years", 756), ("5 years", 1240)):
        if len(c) <= n:
            n = len(c) - 1 if lab == "5 years" and len(c) > 800 else None
        if not n:
            continue
        pr = (c.iloc[-1] / c.iloc[-1 - n] - 1) * 100
        tt = (tr.iloc[-1] / tr.iloc[-1 - n] - 1) * 100
        yrs = n / 252
        rows.append({"label": lab, "values": [f"{pr:+.1f}%", f"{tt:+.1f}%", f"{tt - pr:+.1f} pp", f"₹{d.iloc[-n:].sum():,.2f}",
                                              f"{((1 + tt / 100) ** (1 / yrs) - 1) * 100:+.1f}%"]})
    _header("Total return · price change plus dividends reinvested on the ex-date", T, "live" if rows else "unavailable",
            "Dividends from Yahoo. Splits and bonuses are already in Yahoo's prices." if len(dv if dv is not None else []) else "Yahoo returned no dividend history: total return equals price return here.")
    if rows:
        _table(["PRICE RETURN", "TOTAL RETURN", "FROM DIVIDENDS", "DIVIDENDS PAID / SHARE", "TOTAL RETURN PER YEAR"], rows, T, ["1 year"])
    else:
        _unavailable("Total return", "At least 1 year of price history", T)


# ---------- DCF: reverse DCF ----------
def _dcf_value(cf, g, r, tg, years=10):
    """Present value of `years` of cash flows growing at g for the first half, fading in a straight line to tg, plus a terminal value."""
    pv, cur, half = 0.0, cf, years // 2
    for t in range(1, years + 1):
        gt = g if t <= half else g + (tg - g) * (t - half) / (years - half)
        cur *= 1 + gt
        pv += cur / (1 + r) ** t
    return pv + cur * (1 + tg) / (r - tg) / (1 + r) ** years


def _dcf(sym, B, T, key, nf):
    s = B.get("summary") or {}
    mcap = _f(s.get("market_cap"))
    npq = _nums(_row(B.get("quarterly"), "Net Profit", "Profit for the period"))
    ttm = sum(npq[-4:]) if len(npq) >= 4 and all(v is not None for v in npq[-4:]) else None
    if not mcap or not ttm or ttm <= 0:
        _unavailable("Reverse DCF", "Market cap and a positive trailing-4-quarter net profit (Screener)", T,
                     "A discounted cash flow needs positive earnings to start from.")
        return
    c1, c2, c3 = st.columns(3)
    base = c1.number_input("Starting cash flow ₹ Cr (default: last 4 quarters' net profit)", min_value=0.0, value=float(round(ttm, 1)), step=10.0, key=f"{key}_dcf_cf_{sym}")
    r = c2.number_input("Discount rate %", min_value=6.0, max_value=25.0, value=12.0, step=0.5, key=f"{key}_dcf_r") / 100
    tg = c3.number_input("Growth after year 10 %", min_value=0.0, max_value=7.0, value=5.0, step=0.5, key=f"{key}_dcf_tg") / 100
    if base <= 0 or r <= tg + 0.005:
        _unavailable("Reverse DCF", "A positive cash flow and a discount rate above long-run growth", T)
        return
    lo, hi = -0.5, 1.0
    if _dcf_value(base, hi, r, tg) < mcap:
        implied = None
    else:
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _dcf_value(base, mid, r, tg) < mcap else (lo, mid)
        implied = (lo + hi) / 2
    y = B.get("yearly")
    hist = _annual_map(y, "Net Profit", "Profit for the period")
    ks = [p for p in (y or {}).get("periods", []) if p in hist]
    past = ((hist[ks[-1]] / hist[ks[-4]]) ** (1 / 3) - 1) * 100 if len(ks) >= 4 and hist[ks[-4]] > 0 and hist[ks[-1]] > 0 else None
    _header("Reverse DCF · what growth does today's price already assume?", T, "live",
            "The market value is taken as given and solved for the growth rate that justifies it. Growth holds for 5 years, then fades to the long-run rate by year 10.")
    gap = (implied * 100 - past) if implied is not None and past is not None else None
    _kpis([("Market value", f"₹{mcap:,.0f} Cr", "Screener"), ("Starting cash flow", f"₹{base:,.0f} Cr", f"market value is {mcap / base:.1f}x this"),
           ("Growth the price assumes", _pct(implied * 100, 1) if implied is not None else "> 100%", "per year, for 5 years",
            (T["red"] if gap is not None and gap > 5 else T["green"] if gap is not None and gap < -5 else None)),
           ("Actual profit growth, 3 yrs", _pct(past, 1, True), "per year · Screener"),
           ("Gap", _pct(gap, 1, True) if gap is not None else "N/A", "assumed minus achieved")], T, 5)
    grid = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    rows = [{"label": f"Discount rate {rr * 100:.0f}%", "values": [f"{(_dcf_value(base, g, rr, tg) / mcap - 1) * 100:+.0f}%" for g in grid]}
            for rr in (r - 0.02, r, r + 0.02) if rr > tg + 0.005]
    _header("Value vs today's market value, for other growth assumptions", T)
    _table([f"GROWTH {g * 100:.0f}%" for g in grid], rows, T, [f"Discount rate {r * 100:.0f}%"])
    st.caption("A calculator, not a target price. Net profit stands in for cash flow; use FH to see how much profit turns into cash, and lower the "
               "starting figure if it does not. Small changes in the discount rate move the answer a lot.")


# ---------- DUP: DuPont ----------
def _dupont(sym, B, T, key, nf):
    y, bs = B.get("yearly"), B.get("balance_sheet")
    npm, sal = _annual_map(y, "Net Profit", "Profit for the period"), _annual_map(y, "Sales", "Revenue")
    ta, rsv, eq = _annual_map(bs, "Total Assets", "Total Liabilities"), _annual_map(bs, "Reserves"), _annual_map(bs, "Equity Capital", "Share Capital")
    yrs = [p for p in (bs or {}).get("periods", []) if p in npm and p in sal and p in ta and p in rsv and p in eq and sal[p] and ta[p] and (rsv[p] + eq[p]) > 0][-6:]
    if len(yrs) < 2:
        _unavailable("DuPont", "At least two years with sales, profit, assets and equity (Screener)", T)
        return
    m = [npm[p] / sal[p] * 100 for p in yrs]
    t = [sal[p] / ta[p] for p in yrs]
    l = [ta[p] / (rsv[p] + eq[p]) for p in yrs]
    roe = [a / 100 * b * c * 100 for a, b, c in zip(m, t, l)]
    drv = max((("margin", m[-1] / m[0] if m[0] else 1), ("asset turnover", t[-1] / t[0]), ("leverage", l[-1] / l[0])), key=lambda z: abs(z[1] - 1))
    _header("DuPont · what return on equity is made of", T, "live", "ROE = net margin × asset turnover × leverage. Year-end balances.")
    _kpis([("Return on equity", f"{roe[-1]:.1f}%", f"{roe[0]:.1f}% in {yrs[0]}", T["green"] if roe[-1] >= roe[0] else T["red"]),
           ("Net margin", f"{m[-1]:.1f}%", f"{m[0]:.1f}% in {yrs[0]}"), ("Asset turnover", f"{t[-1]:.2f}x", f"{t[0]:.2f}x in {yrs[0]}"),
           ("Leverage", f"{l[-1]:.2f}x", f"{l[0]:.2f}x in {yrs[0]}"), ("Biggest change", drv[0].capitalize(), f"{(drv[1] - 1) * 100:+.0f}% since {yrs[0]}")], T, 5)
    _table(yrs, [{"label": "Net margin %", "values": [f"{v:.1f}" for v in m]}, {"label": "Asset turnover x", "values": [f"{v:.2f}" for v in t]},
                 {"label": "Leverage x", "values": [f"{v:.2f}" for v in l]}, {"label": "Return on equity %", "values": [f"{v:.1f}" for v in roe]}],
           T, ["Return on equity"], dl=f"{sym}_dupont_{key}")
    st.caption("ROE that rises because leverage rose is lower quality than ROE that rises because margin or turnover rose.")


# ---------- GRW: growth and common size ----------
def _growth(sym, B, T, key, nf):
    y = B.get("yearly")
    if not y or not y.get("rows"):
        _unavailable("Growth", "Screener yearly results", T, B.get("reason"))
        return
    per = [p for p in y["periods"] if "TTM" not in str(p).upper()]
    rows = []
    for lab in ("Sales", "Revenue", "Operating Profit", "Financing Profit", "Net Profit", "EPS"):
        r = _row(y, lab)
        if not r or any(x["label"] == r["label"] for x in rows):
            continue
        mp = {p: _f(v) for p, v in zip(y["periods"], r["values"]) if p in per}
        vals = []
        for n in (1, 3, 5, 10):
            a, b = (mp.get(per[-1]), mp.get(per[-1 - n])) if len(per) > n else (None, None)
            vals.append(f"{((a / b) ** (1 / n) - 1) * 100:+.1f}%" if a and b and a > 0 and b > 0 else "—")
        rows.append({"label": r["label"], "values": vals})
    _header("Compound growth per year · to the latest full year", T, "live")
    _table(["1 YEAR", "3 YEARS", "5 YEARS", "10 YEARS"], rows, T, ["Net Profit"])
    sal = _row(y, "Sales", "Revenue")
    if sal:
        sv = [_f(v) for v in sal["values"]]
        cs = []
        for r in y["rows"]:
            if r["label"] == sal["label"] or "%" in r["label"] or r["label"].lower().startswith(("eps", "dividend")):
                continue
            vals = [(f"{_f(v) / s * 100:.1f}" if _f(v) is not None and s else "—") for v, s in zip(r["values"], sv)][-7:]
            cs.append({"label": r["label"], "values": vals})
        _header("Common size · each line as % of sales", T)
        _table(y["periods"][-7:], cs, T, ["Net Profit", "Operating Profit"], dl=f"{sym}_common_size_{key}")
    g = B.get("growth") or {}
    if g:
        _header("Screener's compounded growth boxes", T)
        heads = list(dict.fromkeys(k for rows_ in g.values() for k in rows_))
        _table(heads, [{"label": t, "values": [rows_.get(k, "—") for k in heads]} for t, rows_ in g.items()], T)


# ---------- EVTS: events ----------
def _events(sym, B, T, key, nf):
    ev = []
    ed = _earnings_date(sym)
    if ed:
        ev.append((ed, "NEXT RESULTS", "Expected earnings date (Yahoo calendar)", ""))
    for attr, kind, fmt in (("dividends", "DIVIDEND", lambda v: f"₹{float(v):,.2f} per share, ex-date"), ("splits", "SPLIT / BONUS", lambda v: f"{float(v):g} : 1")):
        x = yf_attr(sym, attr)
        if x is not None and len(x):
            for i, v in x.iloc[::-1].head(12).items():
                ev.append((str(i)[:10], kind, fmt(v), ""))
    code = rd.bse_code(B.get("url")) if rd is not None else None
    want = re.compile(r"result|board meeting|dividend|bonus|split|buy ?back|agm|egm|record date|acquisition|order|rating|allot", re.I)
    for a in (rd.bse_announcements(code, 365) if code else []):
        if want.search(a["title"] + " " + a["category"] + " " + a["sub"]):
            ev.append((a["dt"][:10], (a["category"] or "FILING").upper()[:22], a["title"][:140], a["url"]))
    if not ev:
        _unavailable("Events", "Yahoo corporate actions or BSE filings", T)
        return
    ev.sort(key=lambda e: e[0], reverse=True)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    up = [e for e in ev if e[0] >= today]
    _header(f"Events timeline · {len(ev)} items · {len(up)} upcoming", T, "live", "Results, board meetings, dividends, splits and other material filings, newest first.")
    kinds = ["All"] + sorted({e[1] for e in ev})
    pick = st.selectbox("Type", kinds, key=f"{key}_evts_kind", label_visibility="collapsed")
    body = ""
    for d, kind, text, url in [e for e in ev if pick == "All" or e[1] == pick][:80]:
        t = f'<a href="{html.escape(url, quote=True)}" target="_blank">{_esc(text)}</a>' if url else f'<a>{_esc(text)}</a>'
        col = T["amber"] if d >= today else T["cyan"]
        body += f'<div class="rp-fil">{t}<div><span style="color:{col}">{_esc(d)}</span> · {_esc(kind)}</div></div>'
    st.markdown(body, unsafe_allow_html=True)


def _management(sym, T):
    info = yf_attr(sym, "info") or {}
    off = [o for o in (info.get("companyOfficers") or []) if isinstance(o, dict) and o.get("name")]
    if not off:
        return
    _header("Management · Yahoo", T)
    _table(["ROLE", "AGE", "PAY"], [{"label": str(o.get("name")), "values": [str(o.get("title") or "—")[:60], str(o.get("age") or "—"),
                                                                              f"₹{o['totalPay'] / 1e7:,.2f} Cr" if isinstance(o.get("totalPay"), (int, float)) and o["totalPay"] else "—"]}
                                    for o in off[:12]], T)


def _pe_band_chart(sym, B, T, key, close):
    """Price against what it would be at the stock's own minimum / median / maximum P/E, quarter by quarter."""
    import plotly.graph_objects as go
    q = B.get("quarterly")
    eps = _nums(_row(q, "EPS"))
    pts = []
    for i in range(3, len(eps)):
        w = eps[i - 3:i + 1]
        d = _quarter_end(q["periods"][i]) if i < len(q["periods"]) else None
        if d is None or any(v is None for v in w) or sum(w) <= 0 or d < close.index[0]:
            continue
        px = close.asof(d)
        if pd.notna(px):
            pts.append((d, sum(w), float(px) / sum(w)))
    if len(pts) < 4:
        return
    pes = sorted(p[2] for p in pts)
    lo, med, hi = pes[0], pes[len(pes) // 2], pes[-1]
    e = pd.Series([p[1] for p in pts], index=[p[0] for p in pts]).reindex(close.index, method="ffill").dropna()
    c = close.reindex(e.index)
    _header("P/E band on price · where the price would be at its own low, median and high P/E", T)
    fig = go.Figure()
    for mult, name, col in ((hi, f"{hi:.0f}x (highest)", T["red"]), (med, f"{med:.0f}x (median)", T["amber"]), (lo, f"{lo:.0f}x (lowest)", T["green"])):
        fig.add_trace(go.Scatter(x=e.index, y=(e * mult).values, name=name, line=dict(color=col, width=1, dash="dot")))
    fig.add_trace(go.Scatter(x=c.index, y=c.values, name="Price", line=dict(color="#EDEDED", width=2)))
    _fig(fig, T, 320, f"{key}_val_band")


def _choice(label, options, key, fmt=None):
    """Segmented control on newer Streamlit, a horizontal radio otherwise. Never returns None."""
    fmt = fmt or (lambda o: o)
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]

    def _keep():
        if st.session_state.get(key) not in options:
            st.session_state[key] = options[0]
    fn = getattr(st, "segmented_control", None)
    if fn is not None:
        try:
            v = fn(label, options, key=key, format_func=fmt, label_visibility="collapsed", on_change=_keep)
            return v if v in options else options[0]
        except TypeError:
            pass
    return st.radio(label, options, key=key, format_func=fmt, horizontal=True, label_visibility="collapsed")


# ═══════════════════════════ v15: PB · price behaviour ═══════════════════════════
def _svg_moves(dates, moves, vols, ups, T, h=170):
    """One bar per session: height = the day's move, colour = up / down; thin volume bars underneath."""
    n = len(moves)
    if n < 2:
        return ""
    W = 1000
    top_h, gap, vol_h = h, 10, 46
    mx = max(1e-9, max(abs(m) for m in moves if m == m))
    vmx = max(1e-9, max(v for v in vols if v == v)) if any(v == v for v in vols) else 1
    bw = W / n
    mid = top_h / 2
    parts = [f'<line x1="0" x2="{W}" y1="{mid}" y2="{mid}" stroke="#2A2A30" stroke-width="1"/>']
    for i, (d, m, v, u) in enumerate(zip(dates, moves, vols, ups)):
        if m != m:
            continue
        col = T["green"] if u else T["red"] if u is False else "#5A5A60"
        hh = abs(m) / mx * (mid - 6)
        y = mid - hh if m >= 0 else mid
        x = i * bw + bw * 0.14
        tip = f"{pd.Timestamp(d):%a %d %b %Y} · {m:+.2f}%" + (f" · vol {_qty(v)}" if v == v else "")
        parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(0.8, bw * 0.72):.2f}" height="{max(0.8, hh):.2f}" rx="1.2" fill="{col}"><title>{_esc(tip)}</title></rect>')
        if v == v:
            vh = v / vmx * vol_h
            parts.append(f'<rect x="{x:.2f}" y="{top_h + gap + vol_h - vh:.2f}" width="{max(0.8, bw * 0.72):.2f}" height="{max(0.6, vh):.2f}" '
                         f'rx="1" fill="{col}" opacity=".38"><title>{_esc(tip)}</title></rect>')
    H = top_h + gap + vol_h
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" height="{H}" preserveAspectRatio="none" style="display:block">{"".join(parts)}</svg>'
            f'<div class="rp-axis"><span>{pd.Timestamp(dates[0]):%d %b %Y}</span><span>daily move · volume underneath · hover a bar</span>'
            f'<span>{pd.Timestamp(dates[-1]):%d %b %Y}</span></div>')


def _streaks(flags):
    """(longest up, longest down, current run length, current run is up?) over a sequence of True / False / None."""
    best_u = best_d = cur = 0
    last = None
    for f in flags:
        if f is None:
            cur, last = 0, None
            continue
        cur = cur + 1 if f == last else 1
        last = f
        if f:
            best_u = max(best_u, cur)
        else:
            best_d = max(best_d, cur)
    return best_u, best_d, cur, last


def behaviour_stats(h: pd.DataFrame, n: int, mode: str = "day", splits=None, bench: pd.Series | None = None,
                    deliv: pd.DataFrame | None = None) -> dict | None:
    """Everything the PB screen shows, from daily OHLCV. mode: 'day' = close vs previous close, 'candle' = close vs open."""
    if h is None or len(h) < 3:
        return None
    d = h[["Open", "High", "Low", "Close", "Volume"]].astype(float).copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index().dropna(subset=["Close"])
    d = d[d["Close"] > 0]
    prev = d["Close"].shift(1)
    d["chg"] = (d["Close"] / prev - 1) * 100
    d["body"] = (d["Close"] / d["Open"] - 1) * 100
    d["gap"] = (d["Open"] / prev - 1) * 100
    d["range"] = (d["High"] - d["Low"]) / prev * 100
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev).abs(), (d["Low"] - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["vavg50"] = d["Volume"].shift(1).rolling(50, min_periods=10).mean()
    # unadjusted prices: a split / bonus day looks like a crash, so leave those days out
    bad = (d["chg"].abs() > 45)
    if splits is not None and len(splits):
        sd = pd.to_datetime(pd.Index(splits.index))
        if getattr(sd, "tz", None) is not None:
            sd = sd.tz_localize(None)
        bad |= d.index.isin(sd.normalize())
    w = d.iloc[-int(n):].copy()
    w["bad"] = bad.reindex(w.index).fillna(False)
    if len(w) < 3:
        return None
    if mode == "candle":
        w["move"] = w["body"]
        w["up"] = np.where(w["Close"] > w["Open"], True, np.where(w["Close"] < w["Open"], False, None))
    else:
        w["move"] = w["chg"]
        w["up"] = np.where(w["chg"] > 0, True, np.where(w["chg"] < 0, False, None))
    w.loc[w["bad"] | w["move"].isna(), "up"] = None
    good = w[~w["bad"] & w["move"].notna()]
    U, D = good[good["up"] == True], good[good["up"] == False]  # noqa: E712
    flat = len(good) - len(U) - len(D)
    su, sd_ = U["Volume"].sum(), D["Volume"].sum()
    bu, bd, cur, cur_up = _streaks(list(w["up"]))
    out = {
        "n": len(w), "used": len(good), "excluded": int(w["bad"].sum()), "mode": mode,
        "first": w.index[0], "last": w.index[-1],
        "up": len(U), "down": len(D), "flat": flat, "up_pct": len(U) / len(good) * 100 if len(good) else None,
        "avg_up": float(U["move"].mean()) if len(U) else None, "avg_dn": float(D["move"].mean()) if len(D) else None,
        "med_up": float(U["move"].median()) if len(U) else None, "med_dn": float(D["move"].median()) if len(D) else None,
        "best": (good["move"].idxmax(), float(good["move"].max())) if len(good) else None,
        "worst": (good["move"].idxmin(), float(good["move"].min())) if len(good) else None,
        "vol_ratio": float(su / sd_) if sd_ > 0 else None,
        "avg_vol_up": float(U["Volume"].mean()) if len(U) else None, "avg_vol_dn": float(D["Volume"].mean()) if len(D) else None,
        "streak_up": bu, "streak_dn": bd, "streak_now": cur, "streak_now_up": cur_up,
        "ret": float((w["Close"].iloc[-1] / d["Close"].shift(1).reindex(w.index).iloc[0] - 1) * 100)
        if pd.notna(d["Close"].shift(1).reindex(w.index).iloc[0]) else None,
        "atr_pct": float(atr.iloc[-1] / d["Close"].iloc[-1] * 100), "avg_range": float(good["range"].mean()) if len(good) else None,
        "frame": w,
    }
    if out["avg_up"] is not None and out["avg_dn"]:
        out["payoff"] = out["avg_up"] / abs(out["avg_dn"])
    gu = good[good["gap"] >= 1.0]
    gd = good[good["gap"] <= -1.0]
    out.update(gap_up=len(gu), gap_up_held=int((gu["Close"] > gu["Open"]).sum()),
               gap_dn=len(gd), gap_dn_held=int((gd["Close"] < gd["Open"]).sum()))
    buckets = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 1e9)]
    out["buckets"] = [(lo, hi, int(((U["move"] >= lo) & (U["move"] < hi)).sum()), int(((-D["move"] >= lo) & (-D["move"] < hi)).sum()))
                      for lo, hi in buckets]
    if bench is not None and len(bench) > 3:
        b = bench.copy()
        bi = pd.to_datetime(b.index)
        if getattr(bi, "tz", None) is not None:
            bi = bi.tz_localize(None)
        b.index = bi.normalize()
        b = b[~b.index.duplicated(keep="last")].sort_index()
        bc = (b / b.shift(1) - 1) * 100
        bw = bc.reindex(w.index).dropna()
        if len(bw) >= 3:
            bu_, bd_ = bw[bw > 0], bw[bw < 0]
            out["bench"] = {"up_pct": len(bu_) / len(bw) * 100, "avg_up": float(bu_.mean()) if len(bu_) else None,
                            "avg_dn": float(bd_.mean()) if len(bd_) else None,
                            "ret": float((b.reindex(w.index).dropna().iloc[-1] / b.shift(1).reindex(w.index).dropna().iloc[0] - 1) * 100)}
    if deliv is not None and len(deliv):
        dv = deliv.copy()
        di = pd.to_datetime(pd.Index(dv.index))
        if getattr(di, "tz", None) is not None:
            di = di.tz_localize(None)
        dv.index = di.normalize()
        j = good.join(dv[["deliv_qty", "deliv_pct"]], how="inner")
        ju, jd = j[j["up"] == True], j[j["up"] == False]  # noqa: E712
        if len(ju) >= 3 and len(jd) >= 3 and ju["deliv_qty"].sum() > 0 and jd["deliv_qty"].sum() > 0:
            out["deliv"] = {"days": len(j), "up_days": len(ju), "dn_days": len(jd),
                            "qty_ratio": float(ju["deliv_qty"].sum() / jd["deliv_qty"].sum()),
                            "pct_up": float(ju["deliv_pct"].mean()), "pct_dn": float(jd["deliv_pct"].mean())}
    return out


def _pb_reading(S, sym) -> str:
    """One plain sentence. Descriptive, not a forecast."""
    vr, po = S.get("vol_ratio"), S.get("payoff")
    if vr is None or po is None:
        return ""
    if vr >= 1.25 and po >= 1.0:
        tone = "buyers have had the upper hand: up days were bigger and carried more volume."
    elif vr <= 0.8 and po <= 1.0:
        tone = "sellers have had the upper hand: down days were bigger and carried more volume."
    elif vr >= 1.25:
        tone = "volume favoured up days even though down days moved further: quiet demand under choppy price."
    elif vr <= 0.8:
        tone = "volume favoured down days even though up days moved further: rallies came on thin volume."
    else:
        tone = "neither side has clearly dominated."
    return (f"Over the last {S['n']} sessions {_esc(sym)} {'closed higher' if S['mode'] == 'day' else 'printed a green candle'} on "
            f"<b>{S['up']} days ({S['up_pct']:.0f}%)</b>. Up days averaged <b class='pos'>{S['avg_up']:+.2f}%</b>, down days "
            f"<b class='neg'>{S['avg_dn']:+.2f}%</b>, and up days carried <b>{vr:.2f}×</b> the volume of down days, so {tone}")


def _price_behaviour(sym, B, T, key, nf):
    h = _hist(sym, "2y", "1y")
    if h is None or len(h) < 25:
        _unavailable("Price behaviour", "Yahoo daily price history", T)
        return
    c1, c2, c3 = st.columns([2.2, 1.0, 2.0], gap="small")
    presets = ["20", "60", "90", "120", "250", "Custom"]
    with c1:
        pick = _choice("Window", presets, f"{key}_pb_win", lambda v: f"{v} days" if v != "Custom" else "Custom")
    if pick == "Custom":
        n = int(c2.number_input("Sessions", min_value=5, max_value=min(1000, len(h) - 1), value=min(45, len(h) - 1), step=5, key=f"{key}_pb_n"))
    else:
        n = min(int(pick), len(h) - 1)
        c2.markdown('<div style="height:2px"></div>', unsafe_allow_html=True)
    with c3:
        mode_l = _choice("Count", ["Close vs previous close", "Candle colour"], f"{key}_pb_mode")
    mode = "candle" if mode_l == "Candle colour" else "day"
    deliv = st.session_state.get(f"rp_dlv_last_{sym}")
    S = behaviour_stats(h, n, mode, yf_attr(sym, "splits"), _bench("NIFTY 50"), deliv)
    if not S or not S["used"]:
        _unavailable("Price behaviour", f"At least a few sessions in the last {n}", T)
        return
    up_w = "up days" if mode == "day" else "green candles"
    dn_w = "down days" if mode == "day" else "red candles"
    _header(f"Price behaviour · last {S['n']} sessions · {S['first']:%d %b} to {S['last']:%d %b %Y}", T, "live",
            ("Close vs previous close: the day's real gain or loss." if mode == "day" else
             "Candle colour: close vs the same day's open. A gap-up day that closes below its open counts as red.")
            + (f" {S['excluded']} split / bonus day(s) left out (prices are unadjusted)." if S["excluded"] else ""))
    reading = _pb_reading(S, sym)
    if reading:
        st.markdown(f'<div class="rp-read">{reading}</div>', unsafe_allow_html=True)
    g, r_ = T["green"], T["red"]
    vr = S.get("vol_ratio")
    _kpis([(up_w.capitalize(), f"{S['up']}", f"{S['up_pct']:.0f}% of {S['used']} · {S['flat']} flat", g),
           (dn_w.capitalize(), f"{S['down']}", f"{100 - S['up_pct'] - (S['flat'] / S['used'] * 100 if S['used'] else 0):.0f}% of {S['used']}", r_),
           (f"Average {up_w[:-1]}", _pct(S["avg_up"], 2, True), f"median {_pct(S['med_up'], 2, True)}", g),
           (f"Average {dn_w[:-1]}", _pct(S["avg_dn"], 2, True), f"median {_pct(S['med_dn'], 2, True)}", r_),
           ("Biggest up", _pct(S["best"][1], 2, True), f"{S['best'][0]:%a %d %b %Y}", g),
           ("Biggest down", _pct(S["worst"][1], 2, True), f"{S['worst'][0]:%a %d %b %Y}", r_),
           ("Up / down volume", f"{vr:.2f}×" if vr else "N/A", "volume on up days ÷ down days · above 1.25 = demand",
            (g if vr and vr >= 1.25 else r_ if vr and vr <= 0.8 else None)),
           ("Average up ÷ average down", f"{S['payoff']:.2f}×" if S.get("payoff") else "N/A", "size of the typical up day vs down day")], T, 4)
    st.markdown(f'<div class="rp-panel"><div class="t">Every session in the window</div><div class="b">'
                f'{_svg_moves(list(S["frame"].index), list(S["frame"]["move"]), list(S["frame"]["Volume"]), list(S["frame"]["up"]), T)}</div></div>',
                unsafe_allow_html=True)

    a, b, c = st.columns(3, gap="medium")
    with a:
        su = S["streak_now"]
        _mini("Streaks and gaps", [
            ("Longest up streak", f"{S['streak_up']} sessions", g), ("Longest down streak", f"{S['streak_dn']} sessions", r_),
            ("Current run", (f"{su} {'up' if S['streak_now_up'] else 'down'}" if S["streak_now_up"] is not None else "flat"),
             g if S["streak_now_up"] else r_ if S["streak_now_up"] is False else None),
            ("Gap ups of 1%+", f"{S['gap_up']} · {S['gap_up_held']} closed above the open"),
            ("Gap downs of 1%+", f"{S['gap_dn']} · {S['gap_dn_held']} closed below the open")], T)
    with b:
        rows = [("Average daily range", _pct(S["avg_range"], 2)), ("ATR 14", f"{S['atr_pct']:.2f}% of price"),
                ("Return over the window", _pct(S["ret"], 1, True))]
        bn = S.get("bench")
        if bn:
            rows += [("NIFTY 50 up days", f"{bn['up_pct']:.0f}% (stock {S['up_pct']:.0f}%)" if mode == "day" else f"{bn['up_pct']:.0f}% (by close)"),
                     ("NIFTY 50 average up / down", f"{_pct(bn['avg_up'], 2, True)} / {_pct(bn['avg_dn'], 2, True)}"),
                     ("NIFTY 50 over the window", _pct(bn["ret"], 1, True))]
        _mini("Range and the index", rows, T)
    with c:
        dv = S.get("deliv")
        if dv:
            q = dv["qty_ratio"]
            _mini("Delivery on up vs down days", [
                ("Delivered qty, up ÷ down", f"{q:.2f}×", g if q >= 1.25 else r_ if q <= 0.8 else None),
                ("Average delivery %, up days", _pct(dv["pct_up"], 1)), ("Average delivery %, down days", _pct(dv["pct_dn"], 1)),
                ("Sessions with delivery data", f"{dv['days']} ({dv['up_days']} up · {dv['dn_days']} down)")], T)
            st.caption("Above 1.25×: more shares were taken home on up days than on down days, a sign of quiet accumulation. "
                       "Delivery is not only institutions; read it together with price.")
        else:
            _panel("Delivery on up vs down days",
                   f'<div style="color:{T["t2"]}">Needs NSE delivery data for these sessions. Load it here (it is also used by DLV).</div>', T)
            if rd is not None and st.button("Load delivery data", key=f"{key}_pb_dlv_{sym}", use_container_width=True):
                with st.spinner("Loading NSE delivery files…"):
                    _delivery_frame(sym, max(60, min(250, n)))
                st.rerun(scope="fragment")

    l, r = st.columns(2, gap="medium")
    with l:
        rows = [{"label": (f"{lo:g}% to {hi:g}%" if hi < 1e8 else f"{lo:g}% or more"), "values": [str(u), str(d_)]} for lo, hi, u, d_ in S["buckets"]]
        _header("How big the moves were", T)
        _table([up_w.capitalize(), dn_w.capitalize()], rows, T)
    with r:
        fr = S["frame"][~S["frame"]["bad"] & S["frame"]["move"].notna()]
        top = pd.concat([fr.nlargest(5, "move"), fr.nsmallest(5, "move")])
        dvf = st.session_state.get(f"rp_dlv_last_{sym}")
        dmap = {}
        if dvf is not None and len(dvf):
            for i_, rr in dvf.iterrows():
                dmap[pd.Timestamp(i_).normalize()] = rr.get("deliv_pct")
        _header("Biggest days", T)
        _table(["Move", "Volume vs 50-day avg", "Delivery %"],
               [{"label": f"{i_:%a %d %b %Y}", "values": [f"{rw['move']:+.2f}%", f"{rw['Volume'] / rw['vavg50']:.2f}×" if rw["vavg50"] and rw["vavg50"] == rw["vavg50"] else "—",
                                                          _pct(dmap.get(pd.Timestamp(i_).normalize()), 1) if dmap.get(pd.Timestamp(i_).normalize()) is not None else "—"]}
                for i_, rw in top.iterrows()], T)
    st.caption("Descriptive statistics of past sessions, not a forecast. Prices are Yahoo daily data (unadjusted); "
               "today's candle is included while it is still forming during market hours.")


def _pb_mini(sym, T):
    """Dashboard card: 60-session behaviour."""
    h = _hist(sym, "1y")
    S = behaviour_stats(h, 60, "day", yf_attr(sym, "splits")) if h is not None else None
    if not S or not S["used"]:
        return
    vr = S.get("vol_ratio")
    _mini("Price behaviour · 60 sessions", [
        ("Up days", f"{S['up']} of {S['used']} ({S['up_pct']:.0f}%)"),
        ("Average up / down day", f"{_pct(S['avg_up'], 2, True)} / {_pct(S['avg_dn'], 2, True)}"),
        ("Up / down volume", f"{vr:.2f}×" if vr else "N/A", T["green"] if vr and vr >= 1.25 else T["red"] if vr and vr <= 0.8 else None),
        ("Longest up / down streak", f"{S['streak_up']} / {S['streak_dn']}")], T, "PB")


HANDLERS = {
    "DES": _overview, "HC": _health, "VAL": _valuation,
    "FA": _financials, "BS": _balance, "CF": _cashflow, "RAT": _ratios,
    "HDS": _shareholding, "MF": _mf,
    "RV": _peers, "PERF": _performance,
    "TECH": _technicals, "SEAS": _seasonality, "ANR": _analyst,
    "DVD": _dividends, "CN": _news, "CACS": _filings,
    "FH": _fin_health, "ERN": _earnings_trend, "DLV": _deliveries, "QR": _quant, "SYN": _synthesis,
    "NOTE": _thesis, "SRC": _sources,
    "OV": _dashboard, "GP": _graph, "HP": _hist_prices, "COMP": _compare, "BETA": _beta, "HVG": _volatility,
    "TRA": _total_return, "DCF": _dcf, "DUP": _dupont, "GRW": _growth, "EVTS": _events, "PB": _price_behaviour,
}
assert set(HANDLERS) == set(FN), set(HANDLERS) ^ set(FN)


# ═══════════════════════════ Navigation ═══════════════════════════
def _css(T):
    st.markdown(f"""<style>
    :root{{--rp-p:var(--s1,#0E0E10);--rp-p2:#131316;--rp-b:var(--line,#1E1E22);--rp-b2:#26262B;--rp-am:{T["amber"]};--rp-tx:#ECECEF;--rp-t2:#A9A9B0;
           --rp-t3:#76767E;--rp-g:{T["green"]};--rp-r:{T["red"]};--rp-c:{T["cyan"]};--rp-s:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
    .rp-sec,.rp-panel,.rp-kg,.rp-tw,.rp-title,.rp-h,.rp-note,.rp-read,.rp-axis{{font-family:var(--rp-s);font-variant-numeric:tabular-nums}}

    /* security header */
    .rp-sec{{display:grid;grid-template-columns:minmax(220px,1.1fr) auto minmax(0,2.4fr);gap:0;border:1px solid var(--rp-b);border-radius:18px;
            background:linear-gradient(135deg,#121215,#0D0D0F 60%);margin:6px 0 14px;overflow:hidden}}
    .rp-sec .id{{padding:18px 20px;min-width:0}}
    .rp-sec .nm{{font-size:22px;font-weight:650;letter-spacing:-.3px;color:var(--rp-tx);line-height:1.2}}
    .rp-sec .sb{{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}}
    .rp-sec .sb span{{font-size:12px;font-weight:500;color:var(--rp-t2);background:#1A1A1E;border:1px solid #24242A;padding:3px 9px;border-radius:999px}}
    .rp-sec .sb span.g{{color:var(--rp-am);border-color:rgba(255,159,10,.3);background:rgba(255,159,10,.08)}}
    .rp-sec .q{{padding:18px 22px;border-left:1px solid var(--rp-b);border-right:1px solid var(--rp-b);text-align:right;min-width:170px}}
    .rp-sec .px{{font-size:28px;font-weight:700;color:#fff;letter-spacing:-.5px}}
    .rp-sec .ch{{display:inline-block;font-size:13px;font-weight:600;margin-top:6px;padding:3px 10px;border-radius:999px;
                background:color-mix(in srgb,currentColor 13%,transparent)}}
    .rp-sec .sts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));align-content:center;gap:14px 18px;padding:16px 20px}}
    .rp-sec .st{{display:flex;flex-direction:column;min-width:0}}
    .rp-sec .st span{{font-size:12px;color:var(--rp-t3)}}
    .rp-sec .st b{{font-size:15px;font-weight:600;color:var(--rp-tx);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .rp-sec .rg{{grid-column:1/-1}} .rp-sec .bar{{position:relative;height:6px;border-radius:6px;background:#1F1F24;margin-top:9px}}
    .rp-sec .bar i{{position:absolute;left:0;top:0;height:6px;border-radius:6px;background:linear-gradient(90deg,rgba(255,159,10,.15),rgba(255,159,10,.55))}}
    .rp-sec .bar u{{position:absolute;top:-4px;width:4px;height:14px;border-radius:2px;background:#fff;transform:translateX(-2px)}}
    .rp-sec .ends{{display:flex;justify-content:space-between;margin-top:6px}} .rp-sec .ends em{{font-style:normal;font-size:11.5px;color:var(--rp-t3)}}
    @media(max-width:900px){{.rp-sec{{grid-template-columns:1fr}} .rp-sec .q{{border:0;border-top:1px solid var(--rp-b);text-align:left}}
        .rp-sec .sts{{border-top:1px solid var(--rp-b)}}}}

    /* titles and section headers */
    .rp-title{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:14px 0 6px;padding:2px 2px 12px;border-bottom:1px solid var(--rp-b)}}
    .rp-title b{{display:block;font-size:20px;font-weight:650;color:var(--rp-tx);letter-spacing:-.3px}}
    .rp-title i{{display:block;font-style:normal;font-size:13px;color:var(--rp-t3);margin-top:3px;line-height:1.5}}
    .rp-title span.code{{flex:0 0 auto;font-size:12px;font-weight:600;color:var(--rp-am);background:rgba(255,159,10,.09);border:1px solid rgba(255,159,10,.25);
                        padding:4px 10px;border-radius:999px;white-space:nowrap}}
    .rp-h{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:26px 2px 10px}}
    .rp-h b{{font-size:15px;font-weight:600;color:var(--rp-tx);letter-spacing:-.1px}}
    .rp-tag{{font-size:11.5px;font-weight:600;padding:2px 9px;border-radius:999px;background:color-mix(in srgb,currentColor 12%,transparent);white-space:nowrap}}
    .rp-note{{font-size:13px;line-height:1.6;color:var(--rp-t3);margin:-4px 2px 12px}}
    .rp-mut{{color:var(--rp-t3);font-weight:600}}
    .rp-read{{font-size:14px;line-height:1.7;color:var(--rp-t2);background:var(--rp-p);border:1px solid var(--rp-b);border-left:3px solid var(--rp-am);
             border-radius:12px;padding:14px 16px;margin:4px 0 14px}}
    .rp-read b{{color:var(--rp-tx);font-weight:600}} .rp-read b.pos{{color:var(--rp-g)}} .rp-read b.neg{{color:var(--rp-r)}}
    .rp-axis{{display:flex;justify-content:space-between;gap:10px;font-size:11.5px;color:var(--rp-t3);margin-top:6px}}

    /* cards */
    .rp-panel{{border:1px solid var(--rp-b);border-radius:14px;background:var(--rp-p);margin:0 0 14px;overflow:hidden}}
    .rp-panel.dash{{border-style:dashed;background:transparent}}
    .rp-panel>.t{{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:14px;font-weight:600;color:var(--rp-tx);padding:14px 16px 4px}}
    .rp-panel>.t em{{font-style:normal;font-size:11.5px;font-weight:600;color:var(--rp-t3);background:#1A1A1E;padding:2px 8px;border-radius:999px}}
    .rp-panel>.b{{padding:8px 16px 14px;font-size:13.5px;line-height:1.65;color:var(--rp-t2)}} .rp-panel>.b.tight{{padding:4px 16px 10px}}
    .rp-mr{{display:flex;justify-content:space-between;align-items:baseline;gap:14px;padding:9px 0;border-bottom:1px solid #18181C;font-size:13px}}
    .rp-mr:last-child{{border-bottom:0}} .rp-mr span{{color:var(--rp-t2)}} .rp-mr b{{color:var(--rp-tx);font-weight:600;text-align:right}}
    .rp-kg{{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:10px;margin:4px 0 14px}}
    .rp-k{{background:var(--rp-p);border:1px solid var(--rp-b);border-radius:12px;padding:13px 15px;min-width:0}}
    .rp-k .l{{font-size:12.5px;color:var(--rp-t3)}}
    .rp-k .v{{font-size:20px;font-weight:650;letter-spacing:-.2px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .rp-k .s{{font-size:12px;line-height:1.45;color:var(--rp-t3);margin-top:4px}}
    @media(max-width:900px){{.rp-kg{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}

    /* tables */
    .rp-tw{{overflow:auto;max-height:640px;border:1px solid var(--rp-b);border-radius:14px;background:var(--rp-p);margin:0 0 14px}}
    table.rp-t{{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}}
    .rp-t th{{position:sticky;top:0;z-index:1;background:#111114;color:var(--rp-t3);font-size:12px;font-weight:550;text-align:right;padding:10px 14px;
             border-bottom:1px solid var(--rp-b);white-space:nowrap}}
    .rp-t td{{padding:9px 14px;border-bottom:1px solid #17171B;white-space:nowrap;color:var(--rp-t2)}}
    .rp-t tbody tr:last-child td{{border-bottom:0}}
    .rp-t td.n{{text-align:right}} .rp-t td.l,.rp-t th.l{{text-align:left;position:sticky;left:0;background:var(--rp-p)}}
    .rp-t th.l{{background:#111114;z-index:2}} .rp-t td.l{{color:var(--rp-tx);font-weight:500}}
    .rp-t tbody tr:hover td{{background:#141417}} .rp-t tr.hl td.l{{color:var(--rp-am)}} .rp-t tr.hl td{{color:var(--rp-tx);font-weight:600}}
    .rp-t td.pos,.rp-t tr.hl td.pos,.rp-mr b.pos{{color:var(--rp-g)}} .rp-t td.neg,.rp-t tr.hl td.neg,.rp-mr b.neg{{color:var(--rp-r)}}
    .rp-cl{{display:flex;gap:14px;align-items:baseline;padding:9px 0;border-bottom:1px solid #18181C;font-size:13px}}
    .rp-cl:last-child{{border-bottom:0}}
    .rp-cl b{{min-width:64px;font-size:12px;font-weight:600}} .rp-cl span{{color:var(--rp-tx);flex:0 0 42%}} .rp-cl i{{font-style:normal;color:var(--rp-t3);text-align:right;flex:1}}
    .rp-fil{{padding:10px 0;border-bottom:1px solid #18181C}} .rp-fil:last-child{{border-bottom:0}}
    .rp-fil a{{font-size:13.5px;font-weight:500;color:var(--rp-tx);text-decoration:none;line-height:1.5}}
    .rp-fil a:hover{{color:var(--rp-am)}} .rp-fil div{{font-size:12px;color:var(--rp-t3);margin-top:3px}}

    /* navigation */
    div[class*="st-key-rpgrp"] [data-testid="stButtonGroup"],div[class*="st-key-rpnav"] [data-testid="stButtonGroup"]{{flex-wrap:wrap;gap:6px}}
    div[class*="st-key-rpnav"]{{margin-top:2px}}
    div[class*="st-key-rpgrp"] div[data-testid="stHorizontalBlock"],div[class*="st-key-rpnav"] div[data-testid="stHorizontalBlock"]{{gap:6px !important}}
    div[class*="st-key-rpgrp"] button,div[class*="st-key-rpnav"] button{{border-radius:999px !important;min-height:34px !important;padding:2px 12px !important}}
    div[class*="st-key-rpgrp"] button p,div[class*="st-key-rpnav"] button p{{font:500 13px var(--rp-s) !important;white-space:nowrap}}
    div[class*="st-key-rprecent"] button{{border-radius:999px !important;min-height:30px !important;padding:1px 12px !important}}
    div[class*="st-key-rprecent"] button p{{font:500 12.5px var(--rp-s) !important}}
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
        st.session_state[f"rp_msg_{key}"] = f"Unknown function '{toks[-1]}'. All codes are listed beside the command bar."
        return
    if sym:
        st.session_state.active_security = sym
        st.session_state.research_last_query = sym
        st.session_state[f"rp_jump_{key}"] = True


def _set_grp(key, grp):
    st.session_state[f"rp_grp_{key}"] = grp
    st.session_state[f"rp_fn_{key}"] = next(code for g, items in GROUPS if g == grp for code, _, _ in items)


def _set_fn(key, code):
    st.session_state[f"rp_fn_{key}"] = code
    st.session_state[f"rp_grp_{key}"] = FN[code][2]


def _command_bar(sym, key, T):
    c1, c2 = st.columns([4, 1], gap="small")
    with c1:
        with st.container(key=f"rpcmd_wrap_{key}"):
            st.text_input("Command", key=f"rpcmd_{key}", on_change=_on_cmd, args=(key,),
                          placeholder="Jump to a function: type a code like PB, DLV or DCF, or a symbol and a code like TCS FA, then Enter",
                          label_visibility="collapsed")
    with c2:
        pop = st.popover("All codes", use_container_width=True) if hasattr(st, "popover") else st.expander("All codes")
        with pop:
            st.markdown("".join(f'<div class="rp-mr"><span><b style="color:{T["amber"]};font-weight:600">{c}</b> &nbsp;{_esc(FN[c][0])}</span>'
                                f'<b style="font-weight:500;color:var(--rp-t3)">{_esc(GROUP_LABEL.get(FN[c][2], FN[c][2]))}</b></div>' for c in FUNCS),
                        unsafe_allow_html=True)
    msg = st.session_state.get(f"rp_msg_{key}")
    if msg:
        st.markdown(f'<div class="rp-note" style="color:{T["red"]};margin-top:2px">{_esc(msg)}</div>', unsafe_allow_html=True)


def _nav(key, T):
    cur = st.session_state[f"rp_fn_{key}"]
    grp = FN[cur][2]
    st.session_state[f"rp_grp_{key}"] = grp
    items = next(it for g, it in GROUPS if g == grp)
    if hasattr(st, "pills"):
        gk, fk = f"rpgrp_sel_{key}", f"rpnav_sel_{key}"
        st.session_state[gk] = grp                       # set before the widgets exist in this run
        st.session_state[fk] = cur

        def on_grp():
            g = st.session_state.get(gk) or grp
            _set_grp(key, g)

        def on_fn():
            _set_fn(key, st.session_state.get(fk) or cur)
        with st.container(key=f"rpgrp_{key}"):
            st.pills("Section", [g for g, _ in GROUPS], key=gk, format_func=lambda g: GROUP_LABEL.get(g, g), on_change=on_grp,
                     label_visibility="collapsed")
        with st.container(key=f"rpnav_{key}"):
            st.pills("Function", [c for c, _, _ in items], key=fk, format_func=lambda c: FN[c][0], on_change=on_fn,
                     label_visibility="collapsed")
        return
    with st.container(key=f"rpgrp_{key}"):                # older Streamlit: buttons
        cols = st.columns(len(GROUPS))
        for c, (g, _) in zip(cols, GROUPS):
            c.button(GROUP_LABEL.get(g, g), key=f"rpg_{key}_{g}", on_click=_set_grp, args=(key, g), type="primary" if g == grp else "secondary",
                     use_container_width=True)
    with st.container(key=f"rpnav_{key}"):
        cols = st.columns([1.5] * len(items) + [max(0.05, (6 - len(items)) * 1.5)])
        for c, (code, name, _) in zip(cols, items):
            c.button(name, key=f"rpb_{key}_{code}", on_click=_set_fn, args=(key, code),
                     type="primary" if code == cur else "secondary", use_container_width=True)


def _title_bar(fn, sym, T):
    name, desc, grp = FN[fn]
    st.markdown(f'<div class="rp-title"><div><b>{_esc(name)}</b><i>{_esc(desc)}</i></div><span class="code">{_esc(sym)} {fn}</span></div>',
                unsafe_allow_html=True)


# ═══════════════════════════ Panel + page ═══════════════════════════
@st.fragment
def _research_fragment(symbol, T, news_fetch_fn, key, show_quote):
    fk = f"rp_fn_{key}"
    if st.session_state.get(fk) not in FN:
        st.session_state[fk] = "OV"
    if st.session_state.pop(f"rp_jump_{key}", False):
        st.rerun(scope="app")  # symbol changed from the command bar → redraw everything

    st.session_state["_rp_sym"] = symbol
    with st.spinner("Loading fundamentals…"):
        B = get_screener_bundle(symbol) or {"resolved": False, "summary": {}, "growth": {}}
    _snapshot(symbol, B, T, show_quote)
    _command_bar(symbol, key, T)
    _nav(key, T)
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
    active = st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    with st.form("research_load_form", clear_on_submit=False):
        c1, c2 = st.columns([5, 1])
        q = c1.text_input("Research security", value=active, placeholder="Search a stock: RELIANCE, HDFCBANK, TCS…",
                          label_visibility="collapsed", key=f"research_q_{active}")
        go_ = c2.form_submit_button("Open", type="primary", use_container_width=True)
    if go_ and q.strip():
        _load_symbol(q.strip().upper())
        st.rerun()

    recent = [s for s in st.session_state.get("research_recent", []) if s != active]
    if recent:
        with st.container(key="rprecent"):
            cols = st.columns([0.6] + [1] * len(recent) + [max(1, 9 - len(recent))])
            cols[0].markdown(f'<div style="font:500 12.5px Inter,sans-serif;color:{T["t3"]};padding-top:7px">Recent</div>', unsafe_allow_html=True)
            for c, s in zip(cols[1:], recent):
                c.button(s, key=f"rprecent_{s}", on_click=_load_symbol, args=(s,), use_container_width=True)

    symbol = st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    if not symbol:
        _panel("Pick a stock to start", "Search a stock above to open its research workspace: dashboard, financials, valuation, price behaviour, "
               f'ownership and filings. Inside it, type codes such as <b style="color:{T["amber"]}">PB</b>, <b style="color:{T["amber"]}">DLV</b> '
               f'or <b style="color:{T["amber"]}">TCS FA</b> to jump straight to a function.', T, dashed=True)
        return
    _remember(symbol)
    render_research_panel(symbol, T, news_fetch_fn, key="rp_page", show_quote=True)
