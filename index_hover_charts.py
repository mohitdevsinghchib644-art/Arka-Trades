"""Small hover charts for Arka Trades' Indian and global index strip.

This module is intentionally separate from app.py. It fetches cached daily index
history from Yahoo Finance and turns it into a compact SVG, so hovering an index
shows a chart without creating a Streamlit rerun or inserting another iframe.
"""
from __future__ import annotations

from html import escape as _escape
import math
from typing import Iterable

import pandas as pd
import streamlit as st
import yfinance as yf


INDEX_HOVER_CSS = """
<style>
.ak-strips{overflow:visible !important;position:relative;z-index:2;}
.ak-strip{position:relative;overflow:visible !important;}
.ak-strip:has(.ak-ix-hover:hover),.ak-strip:has(.ak-ix-hover:focus-within){z-index:80;}
.ak-ix-hover{cursor:default;z-index:1;}
.ak-ix-hover:hover,.ak-ix-hover:focus-within{z-index:90;background:#111 !important;}
.ak-ix-pop{display:none;position:absolute;top:calc(100% + 7px);left:0;width:310px;max-width:min(310px,84vw);
  padding:12px;background:#080808;border:1px solid #393939;border-top:2px solid #FF9F0A;
  box-shadow:0 14px 36px rgba(0,0,0,.55);z-index:9999;text-align:left;white-space:normal;}
.ak-ix-hover:hover .ak-ix-pop,.ak-ix-hover:focus-within .ak-ix-pop{display:block;}
.ak-ix:nth-last-child(-n+2) .ak-ix-pop{left:auto;right:0;}
.ak-ix-pop-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;}
.ak-ix-pop-title{font:700 10px 'JetBrains Mono',monospace;letter-spacing:1px;color:#ECECEC;}
.ak-ix-pop-range{font:600 8px 'JetBrains Mono',monospace;letter-spacing:.7px;color:#FF9F0A;white-space:nowrap;}
.ak-ix-pop-chart{display:block;width:100%;height:auto;background:#0B0B0B;border:1px solid #1F1F1F;}
.ak-ix-pop-metrics{display:flex;justify-content:space-between;gap:8px;margin-top:8px;font:500 9px 'JetBrains Mono',monospace;color:#A3A3A3;}
.ak-ix-pop-source{margin-top:7px;padding-top:6px;border-top:1px solid #1F1F1F;font:8px 'Inter',sans-serif;color:#626262;}
.ak-ix-pop-empty{padding:24px 8px;text-align:center;font:10px 'Inter',sans-serif;color:#777;}
@media(max-width:800px){.ak-ix-pop{width:270px;}.ak-ix:nth-last-child(-n+2) .ak-ix-pop{left:0;right:auto;}}
</style>
"""


@st.cache_data(ttl=300, show_spinner=False, max_entries=32)
def _cached_index_history(candidates: tuple[str, ...]) -> dict:
    """Fetch at most one successful 1-month daily series for an index ticker list."""
    for ticker in candidates:
        try:
            hist = yf.Ticker(str(ticker)).history(
                period="1mo", interval="1d", auto_adjust=False, actions=False
            )
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            if len(close) < 2:
                continue
            close = close.tail(24)
            values = [float(v) for v in close.tolist() if math.isfinite(float(v))]
            if len(values) < 2:
                continue
            dates = [pd.Timestamp(d).strftime("%d %b") for d in close.index[-len(values):]]
            return {
                "ticker": str(ticker),
                "values": values,
                "dates": dates,
                "high": max(values),
                "low": min(values),
                "last_date": dates[-1] if dates else "",
            }
        except Exception:
            continue
    return {"ticker": "", "values": [], "dates": [], "high": None, "low": None, "last_date": ""}


def _svg_sparkline(values: list[float]) -> str:
    """Create an inline chart with grid lines, area tint and last-point marker."""
    if len(values) < 2:
        return '<div class="ak-ix-pop-empty">Historical chart unavailable right now.</div>'

    width, height = 282, 100
    left, right, top, bottom = 7, 7, 10, 15
    plot_w, plot_h = width - left - right, height - top - bottom
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        span = max(abs(hi) * 0.001, 1.0)
    coords = []
    for i, value in enumerate(values):
        x = left + (i / (len(values) - 1)) * plot_w
        y = top + ((hi - value) / span) * plot_h
        coords.append((x, y))
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    x_last, y_last = coords[-1]
    up = values[-1] >= values[0]
    line_color = "#30D158" if up else "#FF453A"
    area_points = f"{left},{height-bottom} {points} {width-right},{height-bottom}"
    grids = "".join(
        f'<line x1="{left}" y1="{y}" x2="{width-right}" y2="{y}" stroke="#202020" stroke-width="1" />'
        for y in (top + plot_h * .25, top + plot_h * .5, top + plot_h * .75)
    )
    return (
        f'<svg class="ak-ix-pop-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="One month daily index close chart">'
        f'{grids}<polygon points="{area_points}" fill="{line_color}" fill-opacity=".08" />'
        f'<polyline points="{points}" fill="none" stroke="{line_color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" />'
        f'<circle cx="{x_last:.1f}" cy="{y_last:.1f}" r="3.3" fill="{line_color}" />'
        '</svg>'
    )


def index_hover_cell(label: str, quote: dict | None, candidates: Iterable[str]) -> str:
    """Return the index price tile plus a CSS-only chart visible on mouse hover."""
    tickers = tuple(str(x).strip() for x in candidates if x)
    history = _cached_index_history(tickers) if tickers else {"values": []}
    values = history.get("values", [])

    safe_label = _escape(str(label))
    if quote:
        try:
            price = float(quote["price"])
            change = float(quote["chg"])
            cls = "ak-up" if change > 0.005 else "ak-dn" if change < -0.005 else "ak-fl"
            arrow = "▲" if change > 0.005 else "▼" if change < -0.005 else "●"
            width = min(100.0, abs(change) / 3 * 100)
            line_color = "#30D158" if change >= 0 else "#FF453A"
            quote_html = (
                f'<div class="n">{safe_label}</div><div class="p">{price:,.2f}</div>'
                f'<span class="c {cls}">{arrow} {abs(change):.2f}%</span>'
                f'<i class="m" style="width:{width:.0f}%;background:{line_color}"></i>'
            )
        except (KeyError, TypeError, ValueError):
            quote_html = f'<div class="n">{safe_label}</div><div class="p" style="color:#444">—</div><span class="c ak-fl">—</span>'
    else:
        quote_html = f'<div class="n">{safe_label}</div><div class="p" style="color:#444">—</div><span class="c ak-fl">—</span>'

    if values:
        chart = _svg_sparkline(values)
        last = values[-1]
        first = values[0]
        move = ((last / first) - 1) * 100 if first else 0.0
        direction_color = "#30D158" if move >= 0 else "#FF453A"
        metrics = (
            f'<span>LOW {history["low"]:,.2f}</span>'
            f'<span style="color:{direction_color}">{move:+.2f}% · 1M</span>'
            f'<span>HIGH {history["high"]:,.2f}</span>'
        )
        source = f'Daily close · { _escape(history.get("ticker", "Yahoo Finance")) } · { _escape(history.get("last_date", "")) }'
    else:
        chart = '<div class="ak-ix-pop-empty">Historical chart unavailable right now.</div>'
        metrics = '<span>1 MONTH</span><span>HISTORY UNAVAILABLE</span>'
        source = 'Index data provider may be temporarily unavailable.'

    popup = (
        f'<div class="ak-ix-pop" aria-label="{safe_label} historical chart">'
        f'<div class="ak-ix-pop-head"><div class="ak-ix-pop-title">{safe_label}</div>'
        '<div class="ak-ix-pop-range">1M · DAILY</div></div>'
        f'{chart}<div class="ak-ix-pop-metrics">{metrics}</div>'
        f'<div class="ak-ix-pop-source">{source}</div></div>'
    )
    return f'<div class="ak-ix ak-ix-hover" tabindex="0">{quote_html}{popup}</div>'
