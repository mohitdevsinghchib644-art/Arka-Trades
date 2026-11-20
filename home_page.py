"""
home_page.py — Arka Trades · Market overview (the HOME page), v3.1

v3.1 (fixes v3)
  * v3 downloaded from Yahoo in parallel. yfinance is not safe to call from several threads at once, so results got
    mixed up: NIFTY 50 "unavailable", half the heatmap blank, sectors without history, and constant re-downloads
    (the lag). Downloads are one at a time again, behind a process-wide lock, and an incomplete download is never cached.
  * Index cards keep a fixed width instead of stretching when some are missing; stocks without a quote are dimmed.
  * Continuous animations removed (pulsing dot, entrance animation, edge mask) to keep scrolling light.

v3
  * A morning-brief header: greeting, one sentence on the market (NIFTY, breadth, leading and lagging sector, VIX)
    and chips for the session and, when it is already loaded, the Market regime.
  * No more "Loading markets…" spinner: the first visit shows the logo with a shimmering skeleton of the page, then
    the real page fades in. Later refreshes swap numbers in place with no loader.
  * Faster first load: quotes, history, stock quotes and the mood index download in parallel instead of one by one.
  * Polish: soft depth and hover on cards, a glow on the NIFTY card in the day's colour, a live dot on the chart,
    edge fade on the scrolling index strip. Motion respects "reduce motion".

v2 is a visual redesign; the data, calendar and caching code is unchanged from v1.1.
  * App-style look: Inter everywhere with tabular numbers, sentence-case labels, soft 12 px cards, no code
    badges, no scrolling tape, no uppercase monospace.
  * Index cards strip at the top (NIFTY 50, SENSEX, BANK NIFTY, VIX, USD/INR, gold, crude, S&P 500) that swipes
    sideways on phones.
  * Phones get their own layout (render_home(..., compact=True), set by app.py v9.1): lists instead of wide
    tables, a 4-across heatmap, and the most useful blocks first (NIFTY, movers, your book, heatmap, sectors).
  * The function launcher cards are gone; navigation lives in the app's tab bar.

Public API unchanged: render_home(supabase, on_open, nav[, compact]) · render_tape() · nse_session() · get_mmi()
· mmi_zone_color() · market_data().

Data: Yahoo Finance (delayed). Two batched downloads for ~60 instruments: last 5 days every 90 s while NSE
is open (15 min when closed) and 1 year of history every 6 h. Stale-while-error via data_engine._guarded.
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import data_engine as de
from data_engine import get_quotes

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

IST = timezone(timedelta(hours=5, minutes=30))
AMBER, GREEN, RED, CYAN, PURPLE, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
T2, T3 = "#A3A3A3", "#6B6B6B"

# ═════════════════════════ NSE calendar (update once a year) ═════════════════════════
# Source: NSE equity-segment holiday list for 2026 (16 weekday closures).
NSE_HOLIDAYS = {
    date(2026, 1, 15): "Municipal elections (Maharashtra)", date(2026, 1, 26): "Republic Day",
    date(2026, 3, 3): "Holi", date(2026, 3, 26): "Shri Ram Navami", date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3): "Good Friday", date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti", date(2026, 5, 1): "Maharashtra Day",
    date(2026, 5, 28): "Bakri Id", date(2026, 6, 26): "Muharram", date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti", date(2026, 10, 20): "Dussehra", date(2026, 11, 10): "Diwali Balipratipada",
    date(2026, 11, 24): "Guru Nanak Jayanti", date(2026, 12, 25): "Christmas",
}
SPECIAL_SESSIONS = {date(2026, 11, 8): "Muhurat trading (Diwali) · timing per NSE circular"}
PRE_OPEN, OPEN, CLOSE = 9 * 60, 9 * 60 + 15, 15 * 60 + 30


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(n):
            return n
        n += timedelta(days=1)
    return n


def nse_session(now: datetime | None = None) -> dict:
    """NSE cash-market session in IST, holiday-aware. Keys: label, cls (live / pre / closed), detail, progress, today."""
    now = now or datetime.now(IST)
    d, t = now.date(), now.hour * 60 + now.minute
    stamp = now.strftime("%H:%M IST")
    nxt = next_trading_day(d)
    nxt_txt = f"next session {nxt:%a %d %b}"
    special = SPECIAL_SESSIONS.get(d)
    if d in NSE_HOLIDAYS:
        return {"label": "MARKET HOLIDAY", "cls": "closed", "detail": f"{NSE_HOLIDAYS[d]} · {nxt_txt}", "progress": None,
                "short": "HOLIDAY", "stamp": stamp}
    if d.weekday() >= 5:
        detail = (special + " · " if special else "") + f"Weekend · {nxt_txt}"
        return {"label": "MARKET CLOSED", "cls": "closed", "detail": detail, "progress": None, "short": "CLOSED", "stamp": stamp}
    if PRE_OPEN <= t < OPEN:
        return {"label": "PRE-OPEN", "cls": "pre", "detail": f"Order matching · opens 09:15 · {stamp}", "progress": 0.0,
                "short": "PRE-OPEN", "stamp": stamp}
    if OPEN <= t <= CLOSE:
        left = CLOSE - t
        return {"label": "MARKET OPEN", "cls": "live", "detail": f"Closes 15:30 · {left // 60}h {left % 60:02d}m left",
                "progress": (t - OPEN) / (CLOSE - OPEN), "short": "OPEN", "stamp": stamp}
    if t < PRE_OPEN:
        return {"label": "MARKET CLOSED", "cls": "closed", "detail": f"Pre-open 09:00 · opens 09:15 · {stamp}", "progress": None,
                "short": "CLOSED", "stamp": stamp}
    return {"label": "MARKET CLOSED", "cls": "closed", "detail": f"Closed 15:30 · {nxt_txt}", "progress": 1.0,
            "short": "CLOSED", "stamp": stamp}


def prev_trading_day_on_or_before(d: date) -> date:
    for _ in range(15):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def next_expiry(now: datetime | None = None, weekday: int = 1, monthly: bool = False) -> date:
    """Next derivatives expiry: NSE contracts expire on Tuesday (weekday=1), BSE on Thursday (3); monthly = last such
    weekday of the month. If that day is a holiday the expiry moves to the previous trading day."""
    now = now or datetime.now(IST)
    today, after_close = now.date(), now.hour * 60 + now.minute > CLOSE

    def candidate(month_start: date):
        nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = nxt - timedelta(days=1)
        while last.weekday() != weekday:
            last -= timedelta(days=1)
        return prev_trading_day_on_or_before(last)
    if monthly:
        m = today.replace(day=1)
        for _ in range(3):
            e = candidate(m)
            if e > today or (e == today and not after_close):
                return e
            m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        return e
    d = today
    for _ in range(3):
        c = d + timedelta(days=(weekday - d.weekday()) % 7)
        e = prev_trading_day_on_or_before(c)
        if e > today or (e == today and not after_close):
            return e
        d = c + timedelta(days=1)
    return e


def trading_days_left(start: date) -> int:
    end = date(start.year, 12, 31)
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        n += is_trading_day(d)
        d += timedelta(days=1)
    return n


def upcoming_holidays(n=3, start: date | None = None):
    start = start or datetime.now(IST).date()
    days = sorted([(d, nm, "HOLIDAY") for d, nm in NSE_HOLIDAYS.items() if d >= start] +
                  [(d, nm, "SPECIAL") for d, nm in SPECIAL_SESSIONS.items() if d >= start])
    return days[:n]


# ═════════════════════════ instruments ═════════════════════════
# (group, label, ticker chain, kind) — kind: idx · fx · fx4 · usd · yld · vol · cry
WEI = [
    ("INDIA", "NIFTY 50", ("^NSEI",), "idx"), ("INDIA", "SENSEX", ("^BSESN",), "idx"),
    ("INDIA", "BANK NIFTY", ("^NSEBANK",), "idx"), ("INDIA", "FIN NIFTY", ("NIFTY_FIN_SERVICE.NS",), "idx"),
    ("INDIA", "NIFTY NEXT 50", ("^NSMIDCP",), "idx"), ("INDIA", "NIFTY MIDCAP 100", ("^CRSMID", "NIFTY_MIDCAP_100.NS"), "idx"),
    ("INDIA", "NIFTY 500", ("^CRSLDX",), "idx"),
    ("AMERICAS", "S&P 500", ("^GSPC",), "idx"), ("AMERICAS", "NASDAQ COMP", ("^IXIC",), "idx"),
    ("AMERICAS", "DOW JONES", ("^DJI",), "idx"), ("AMERICAS", "RUSSELL 2000", ("^RUT",), "idx"),
    ("EUROPE", "FTSE 100", ("^FTSE",), "idx"), ("EUROPE", "DAX", ("^GDAXI",), "idx"),
    ("EUROPE", "CAC 40", ("^FCHI",), "idx"), ("EUROPE", "EURO STOXX 50", ("^STOXX50E",), "idx"),
    ("ASIA-PACIFIC", "NIKKEI 225", ("^N225",), "idx"), ("ASIA-PACIFIC", "HANG SENG", ("^HSI",), "idx"),
    ("ASIA-PACIFIC", "SHANGHAI COMP", ("000001.SS",), "idx"), ("ASIA-PACIFIC", "KOSPI", ("^KS11",), "idx"),
    ("ASIA-PACIFIC", "TAIEX", ("^TWII",), "idx"), ("ASIA-PACIFIC", "ASX 200", ("^AXJO",), "idx"),
]
XASSET = [
    ("COMMODITIES · USD", "GOLD", ("GC=F",), "usd"), ("COMMODITIES · USD", "SILVER", ("SI=F",), "usd"),
    ("COMMODITIES · USD", "BRENT CRUDE", ("BZ=F",), "usd"), ("COMMODITIES · USD", "WTI CRUDE", ("CL=F",), "usd"),
    ("COMMODITIES · USD", "NATURAL GAS", ("NG=F",), "usd"), ("COMMODITIES · USD", "COPPER", ("HG=F",), "usd"),
    ("CURRENCIES", "USD / INR", ("INR=X",), "fx"), ("CURRENCIES", "EUR / INR", ("EURINR=X",), "fx"),
    ("CURRENCIES", "GBP / INR", ("GBPINR=X",), "fx"), ("CURRENCIES", "JPY / INR", ("JPYINR=X",), "fx4"),
    ("CURRENCIES", "DOLLAR INDEX", ("DX-Y.NYB",), "fx"),
    ("RATES · YIELD %", "US 10Y", ("^TNX",), "yld"), ("RATES · YIELD %", "US 30Y", ("^TYX",), "yld"),
    ("VOLATILITY", "INDIA VIX", ("^INDIAVIX",), "vol"), ("VOLATILITY", "CBOE VIX", ("^VIX",), "vol"),
    ("CRYPTO · USD", "BITCOIN", ("BTC-USD",), "cry"), ("CRYPTO · USD", "ETHEREUM", ("ETH-USD",), "cry"),
]
SECTORS = [
    ("BANK", ("^NSEBANK",)), ("PRIVATE BANK", ("NIFTY_PVT_BANK.NS",)), ("PSU BANK", ("^CNXPSUBANK",)),
    ("FIN SERVICES", ("NIFTY_FIN_SERVICE.NS",)), ("IT", ("^CNXIT",)), ("AUTO", ("^CNXAUTO",)), ("FMCG", ("^CNXFMCG",)),
    ("PHARMA", ("^CNXPHARMA",)), ("METAL", ("^CNXMETAL",)), ("ENERGY", ("^CNXENERGY",)), ("REALTY", ("^CNXREALTY",)),
    ("MEDIA", ("^CNXMEDIA",)), ("INFRA", ("^CNXINFRA",)), ("PSE", ("^CNXPSE",)), ("COMMODITIES", ("^CNXCMDT",)),
    ("CONSUMPTION", ("^CNXCONSUM",)), ("MNC", ("^CNXMNC",)), ("SERVICES", ("^CNXSERVICE",)),
]
TAPE = ["NIFTY 50", "SENSEX", "BANK NIFTY", "INDIA VIX", "USD / INR", "GOLD", "BRENT CRUDE", "US 10Y", "BITCOIN",
        "S&P 500", "NASDAQ COMP", "DOW JONES", "FTSE 100", "DAX", "NIKKEI 225", "HANG SENG", "DOLLAR INDEX", "SILVER"]

# NIFTY 50 as of 30 Sep 2026 (BSE in, Wipro out). Put NSE's `ind_nifty50list.csv` in data/ to override automatically.
NIFTY50 = {
    "FINANCIALS": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "BAJAJFINSV", "JIOFIN",
                   "SHRIRAMFIN", "HDFCLIFE", "SBILIFE", "BSE"],
    "IT": ["TCS", "INFY", "HCLTECH", "TECHM"],
    "ENERGY & POWER": ["RELIANCE", "ONGC", "COALINDIA", "NTPC", "POWERGRID"],
    "AUTO": ["M&M", "MARUTI", "TMPV", "BAJAJ-AUTO", "EICHERMOT"],
    "FMCG": ["ITC", "HINDUNILVR", "NESTLEIND", "TATACONSUM"],
    "HEALTHCARE": ["SUNPHARMA", "CIPLA", "DRREDDY", "APOLLOHOSP", "MAXHEALTH"],
    "METALS": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "ADANIENT"],
    "CONSUMER": ["TITAN", "ASIANPAINT", "TRENT", "ETERNAL"],
    "INDUSTRIALS": ["LT", "BEL", "ADANIPORTS", "INDIGO"],
    "MATERIALS": ["ULTRACEMCO", "GRASIM"],
    "TELECOM": ["BHARTIARTL"],
}

FUNCS = [("MF", "MONEY FLOW", "flow", "Rotation desk: where money is moving, sector ranking, breadth, FPI flows"),
         ("RES", "RESEARCH", "research", "Financials, peers, valuation, health score and technicals"),
         ("PORT", "PORTFOLIO", "portfolio", "P&amp;L, tax, risk, rebalance, dividends and reports"),
         ("WL", "WATCHLIST", "watchlist", "Live list with RSI, PDH / PDL levels and signals"),
         ("NEWS", "NEWS DESK", "news", "Scored wire, BSE filings, news pulse and sector tone"),
         ("EQS", "SCREENER", "smart_scan", "Rule-based equity screens across the NSE universe"),
         ("AI", "ARKA AI", "analysis", "AI chart analysis against your own trading rules")]


# ═════════════════════════ CSS ═════════════════════════
# Calm, app-like styling (Inter everywhere, tabular numbers, 12 px corners). Layout uses container queries so the
# grid follows the width the HOME column really has; phones (and narrow columns) get one column.
_CSS = """<style>
.hm{--hm-r:12px;font-family:var(--sans,'Inter',system-ui,sans-serif);font-variant-numeric:tabular-nums;container-type:inline-size;width:100%;}
.hm *{box-sizing:border-box;}
.hm-head{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;padding:18px 0 4px;}
.hm-head .t{font:600 22px var(--sans);letter-spacing:-.4px;color:var(--tx);}
.hm-head .s{font:13px var(--sans);color:var(--tx3);margin-top:4px;}
.hm-chip{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:999px;background:var(--s2);font:500 12.5px var(--sans);color:var(--tx2);white-space:nowrap;}
.hm-chip i{width:7px;height:7px;border-radius:50%;background:var(--tx4,#52525B);display:inline-block;}
.hm-chip.live i{background:var(--green);box-shadow:0 0 0 3px rgba(48,209,88,.18);} .hm-chip.pre i{background:var(--amber);}

/* index cards strip */
.hm-cards{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding:12px 0 4px;margin:0;scrollbar-width:none;-webkit-overflow-scrolling:touch;}
.hm-cards::-webkit-scrollbar{display:none;}
.hm-card{flex:0 0 172px;scroll-snap-align:start;background:var(--s1);border:1px solid var(--line);border-radius:var(--hm-r);padding:12px 14px 10px;min-width:158px;}
.hm-card .n{font:500 12.5px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-card .v{font:600 17px var(--sans);color:var(--tx);margin-top:6px;letter-spacing:-.2px;white-space:nowrap;}
.hm-card .c{font:500 12.5px var(--sans);margin-top:2px;white-space:nowrap;}
.hm-card svg{display:block;margin-top:8px;width:100%;}

/* grid */
.hm-row{display:grid;gap:12px;margin-top:12px;grid-template-columns:minmax(0,1fr);align-items:stretch;}
@container (min-width:760px){
  .hm-r1{grid-template-columns:minmax(0,1fr) minmax(0,1fr);} .hm-r1>.hm-p:first-child{grid-column:1/-1;}
  .hm-r2,.hm-r3,.hm-r5{grid-template-columns:minmax(0,1fr) minmax(0,1fr);}
}
@container (min-width:1120px){
  .hm-r1{grid-template-columns:minmax(0,1.7fr) minmax(0,1fr) minmax(0,1fr);} .hm-r1>.hm-p:first-child{grid-column:auto;}
  .hm-r2{grid-template-columns:minmax(0,1fr) minmax(0,2fr);}
  .hm-r5{grid-template-columns:repeat(3,minmax(0,1fr));}
}
@container (min-width:1500px){ .hm-r4{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);} }

/* panels */
.hm-p{background:var(--s1);border:1px solid var(--line);border-radius:var(--hm-r);min-width:0;display:flex;flex-direction:column;overflow:hidden;}
.hm-pt{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:14px 16px 4px;}
.hm-pt b{font:600 15px var(--sans);color:var(--tx);white-space:nowrap;}
.hm-pt span{font:12.5px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;text-align:right;}
.hm-pb{padding:8px 16px 16px;flex:1;min-width:0;}
.hm-pb.np{padding:6px 0 8px;}
.hm-sub{font:600 13px var(--sans);color:var(--tx2);padding:16px 0 4px;}
.hm-up{color:var(--green) !important;} .hm-dn{color:var(--red) !important;} .hm-fl{color:var(--tx3) !important;}

/* tables */
.hm-t{width:100%;border-collapse:collapse;font:13.5px var(--sans);}
.hm-t th{font:500 12px var(--sans);color:var(--tx3);text-align:right;padding:8px 16px;border-bottom:1px solid var(--line);white-space:nowrap;}
.hm-t th:first-child,.hm-t td:first-child{text-align:left;}
.hm-t td{padding:9px 16px;border-bottom:1px solid var(--line);text-align:right;color:var(--tx2);white-space:nowrap;}
.hm-t tr:last-child td{border-bottom:0;}
.hm-t td.n{color:var(--tx);font-weight:500;}
.hm-t td.l{color:var(--tx);font-weight:500;}
.hm-t td.st{opacity:.45;}
.hm-t tr.g td{font:600 12px var(--sans);color:var(--tx3);padding:14px 16px 6px;border-bottom:0;}
.hm-t tbody tr:not(.g):hover td{background:rgba(255,255,255,.02);}
.hm-t svg{vertical-align:middle;}
.hm-hm td.c{font-weight:600;color:#fff !important;text-align:center !important;min-width:64px;border-left:2px solid var(--s1);}

/* compact list rows (phones) */
.hm-li{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 16px;border-bottom:1px solid var(--line);}
.hm-li:last-child{border-bottom:0;}
.hm-li .a{min-width:0;} .hm-li .a b{display:block;font:500 14px var(--sans);color:var(--tx);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-li .a small{display:block;font:12px var(--sans);color:var(--tx3);margin-top:2px;}
.hm-li .z{text-align:right;flex-shrink:0;} .hm-li .z b{display:block;font:500 14px var(--sans);color:var(--tx);}
.hm-li .z small{display:block;font:500 12.5px var(--sans);margin-top:2px;}
.hm-lg{font:600 12px var(--sans);color:var(--tx3);padding:14px 16px 4px;}
.hm-pct{display:inline-block;min-width:64px;padding:4px 8px;border-radius:6px;font:600 12.5px var(--sans);text-align:center;}

/* NIFTY hero */
.hm-hv{font:600 34px var(--sans);color:var(--tx);letter-spacing:-.8px;line-height:1.1;}
.hm-hc{font:500 14px var(--sans);margin-top:4px;}
.hm-hrow{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;}
.hm-per{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;}
.hm-per span{font:500 12px var(--sans);padding:4px 9px;border-radius:999px;background:var(--s2);color:var(--tx2);white-space:nowrap;}
.hm-per span b{font-weight:600;margin-left:4px;}
.hm-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 20px;margin-top:10px;}
@container (max-width:520px){.hm-stats{grid-template-columns:repeat(2,minmax(0,1fr));} .hm-hv{font-size:30px;}}
.hm-stats div{display:flex;justify-content:space-between;gap:8px;font:13px var(--sans);color:var(--tx3);border-bottom:1px solid var(--line);padding:8px 0;}
.hm-stats b{color:var(--tx);font-weight:500;}
.hm-rng{margin-top:14px;}
.hm-rng .t{display:flex;justify-content:space-between;gap:10px;font:12px var(--sans);color:var(--tx3);margin-bottom:7px;}
.hm-rng .t b{color:var(--tx2);font-weight:500;}
.hm-rng .b{position:relative;height:4px;background:var(--s3);border-radius:2px;}
.hm-rng .b i{position:absolute;top:-4px;width:12px;height:12px;background:var(--tx);border:2px solid var(--s1);border-radius:50%;transform:translateX(-6px);}

/* regime, session, mood */
.hm-big{font:600 24px var(--sans);letter-spacing:-.4px;line-height:1.15;}
.hm-cap{font:13px var(--sans);color:var(--tx3);margin-top:4px;line-height:1.55;}
.hm-chk{display:flex;justify-content:space-between;align-items:center;gap:14px;font:13px var(--sans);color:var(--tx2);padding:8px 0;border-bottom:1px solid var(--line);line-height:1.45;}
.hm-chk:last-child{border-bottom:0;}
.hm-chk b{font:600 12.5px var(--sans);white-space:nowrap;}
.hm-meter{display:flex;gap:4px;margin:12px 0 6px;} .hm-meter i{flex:1;height:6px;background:var(--s3);border-radius:3px;}
.hm-prog{position:relative;height:6px;background:var(--s3);margin:14px 0 6px;border-radius:3px;overflow:hidden;}
.hm-prog i{position:absolute;left:0;top:0;bottom:0;background:var(--green);border-radius:3px;}
.hm-ticks{display:flex;justify-content:space-between;font:12px var(--sans);color:var(--tx3);}
.hm-kv{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:8px 0;border-bottom:1px solid var(--line);font:13px var(--sans);color:var(--tx3);line-height:1.45;}
.hm-kv:last-child{border-bottom:0;}
.hm-kv b{font:500 13px var(--sans);color:var(--tx);white-space:nowrap;}
.hm-gauge{position:relative;height:6px;border-radius:3px;margin:14px 0 6px;background:linear-gradient(90deg,#FF453A,#FF9F0A 40%,#B5D334 60%,#30D158);}
.hm-gauge i{position:absolute;top:-5px;width:16px;height:16px;background:#fff;border:3px solid var(--s1);border-radius:50%;transform:translateX(-8px);}
.hm-gl{display:flex;justify-content:space-between;font:12px var(--sans);color:var(--tx3);}

/* NIFTY 50 heatmap */
.hm-imap{display:flex;flex-wrap:wrap;gap:14px 16px;padding:12px 16px 8px;}
.hm-grp{display:flex;flex-direction:column;gap:6px;min-width:0;}
.hm-grp>small{font:500 12px var(--sans);color:var(--tx3);white-space:nowrap;}
.hm-tiles{display:flex;flex-wrap:wrap;gap:3px;max-width:520px;}
.hm-tile{width:84px;height:54px;display:flex;flex-direction:column;justify-content:center;align-items:center;border-radius:6px;color:#fff;overflow:hidden;}
.hm-tile b{font:600 11.5px var(--sans);max-width:78px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hm-tile span{font:500 11.5px var(--sans);opacity:.92;margin-top:2px;}
@container (max-width:620px){.hm-grp{width:100%;} .hm-tiles{max-width:none;} .hm-tile{width:calc((100% - 9px)/4);}}
.hm-ad{display:flex;height:6px;margin:0 16px;background:var(--s3);border-radius:3px;overflow:hidden;gap:2px;}
.hm-ad i{display:block;height:100%;}
.hm-adl{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px 14px;font:500 12.5px var(--sans);padding:8px 16px 8px;color:var(--tx3);}
.hm-leg{display:flex;align-items:center;gap:10px;padding:2px 16px 12px;font:12px var(--sans);color:var(--tx3);}
.hm-leg i{width:140px;height:6px;border-radius:3px;background:linear-gradient(90deg,rgba(255,69,58,.8),#2a2a2e 50%,rgba(48,209,88,.8));}

/* movers */
.hm-mv{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);}
.hm-mv>div+div{border-left:1px solid var(--line);}

/* your book */
.hm-kp{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin-top:6px;}
.hm-kp div{background:var(--s1);padding:12px 16px;}
.hm-kp small{display:block;font:12px var(--sans);color:var(--tx3);}
.hm-kp b{display:block;font:600 18px var(--sans);color:var(--tx);margin-top:4px;white-space:nowrap;letter-spacing:-.2px;}
.hm-kp em{font:500 12px var(--sans);font-style:normal;}
.hm-empty{font:13.5px var(--sans);color:var(--tx3);line-height:1.65;padding:12px 16px;}
.hm-pb .hm-empty{padding:4px 0;}
.hm-empty b{color:var(--tx2);font-weight:600;}
.hm-pill{display:inline-block;padding:3px 9px;border-radius:999px;font:500 12px var(--sans);background:var(--s2);white-space:nowrap;}
.hm-clk{display:grid;grid-template-columns:1fr auto auto;gap:6px 14px;align-items:center;font:13px var(--sans);padding:8px 0;border-bottom:1px solid var(--line);}
.hm-clk:last-child{border-bottom:0;}
.hm-clk span{color:var(--tx2);} .hm-clk b{color:var(--tx);font-weight:500;}
.hm-foot{font:12px var(--sans);color:var(--tx3);padding:18px 0 0;line-height:1.6;}

/* v3: brief header */
.hm-brief{display:flex;justify-content:space-between;align-items:flex-end;gap:14px 24px;flex-wrap:wrap;padding:18px 0 6px;}
.hm-brief .g{font:500 13px var(--sans);color:var(--tx3);}
.hm-brief .t{font:650 26px/1.2 var(--sans);letter-spacing:-.5px;color:var(--tx);margin-top:4px;}
.hm-brief .s{font:14px/1.6 var(--sans);color:var(--tx2);margin-top:8px;max-width:760px;}
.hm-brief .s b{font-weight:600;color:var(--tx);}
.hm-chips{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;}
.hm-chip.k{--c:var(--tx3);color:var(--c);background:color-mix(in srgb,var(--c) 12%,transparent);}
.hm-chip.k i{background:var(--c);}
@container (max-width:620px){.hm-brief .t{font-size:22px;} .hm-chips{justify-content:flex-start;}}

/* v3: depth, hover, glow */
.hm-card,.hm-p{transition:border-color .18s ease,background .18s ease;}
.hm-card:hover{border-color:#2f2f35;background:#121215;}
.hm-p:hover{border-color:#26262b;}
.hm-p.hero{position:relative;}
.hm-p.hero::before{content:"";position:absolute;inset:0 0 auto 0;height:170px;pointer-events:none;
  background:radial-gradient(60% 120% at 12% 0%,color-mix(in srgb,var(--hg,#30D158) 16%,transparent),transparent 70%);}
.hm-p.hero>*{position:relative;}
.hm-tile:hover{filter:brightness(1.15);}
.hm-li,.hm-t tbody tr:not(.g) td{transition:background .15s ease;} .hm-li:hover{background:rgba(255,255,255,.025);}
.hm-dot{opacity:.25;}
.hm-tile.nq{opacity:.38;}

/* v3: loading skeleton */
.hm-load{display:flex;align-items:center;gap:14px;padding:22px 0 14px;}
.hm-load img{width:40px;height:40px;border-radius:11px;animation:hmbreath 1.6s ease-in-out infinite;}
@keyframes hmbreath{0%,100%{transform:scale(1);box-shadow:0 0 0 0 rgba(247,148,29,.45)}50%{transform:scale(1.05);box-shadow:0 0 0 10px rgba(247,148,29,0)}}
.hm-load b{display:block;font:600 16px var(--sans);color:var(--tx);} .hm-load span{display:block;font:13px var(--sans);color:var(--tx3);margin-top:2px;}
.hm-load .bar{flex:1;max-width:220px;height:3px;border-radius:3px;background:#1c1c20;overflow:hidden;position:relative;margin-left:auto;}
.hm-load .bar i{position:absolute;left:-40%;top:0;bottom:0;width:40%;border-radius:3px;background:var(--amber,#FF9F0A);animation:hmbar 1.1s ease-in-out infinite;}
@keyframes hmbar{to{left:100%}}
.sk{border-radius:12px;background:linear-gradient(100deg,#101013 30%,#1a1a1f 50%,#101013 70%);background-size:220% 100%;animation:hmsk 1.25s linear infinite;border:1px solid var(--line,#1E1E21);}
@keyframes hmsk{from{background-position:120% 0}to{background-position:-100% 0}}
.sk-cards{display:flex;gap:10px;overflow:hidden;margin:6px 0 12px;} .sk-cards .sk{flex:1 0 150px;height:96px;}
.sk-grid{display:grid;gap:12px;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr) minmax(0,1fr);} .sk-grid .sk{height:330px;}
.sk-grid2{display:grid;gap:12px;grid-template-columns:minmax(0,1fr) minmax(0,2fr);margin-top:12px;} .sk-grid2 .sk{height:220px;}
@container (max-width:900px){.sk-grid,.sk-grid2{grid-template-columns:minmax(0,1fr);} .sk-grid .sk{height:220px;} .sk-grid .sk+.sk{height:150px;}}
@media (prefers-reduced-motion:reduce){.sk,.hm-load img{animation:none !important;}}
</style>"""


# ═════════════════════════ formatting ═════════════════════════
def _e(s):
    return html.escape("" if s is None else str(s))


def _ok(x):
    return x is not None and isinstance(x, (int, float, np.floating)) and math.isfinite(float(x))


def _cls(v, eps=0.005):
    if not _ok(v):
        return "hm-fl"
    return "hm-up" if v > eps else "hm-dn" if v < -eps else "hm-fl"


def _arrow(v, eps=0.005):
    return "▲" if _ok(v) and v > eps else "▼" if _ok(v) and v < -eps else "■"


def _pct(v, d=2, sign=True):
    if not _ok(v):
        return "—"
    if round(v, d) == 0:             # never show "-0.0%"
        return f"{0:.{d}f}%"
    return f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%"


def _px(v, kind="idx"):
    """Price formatter by instrument kind. '$' is written as &#36; so Streamlit never reads it as LaTeX."""
    if not _ok(v):
        return "—"
    if kind in ("fx4",):
        return f"{v:,.4f}"
    if kind in ("fx", "vol"):
        return f"{v:,.2f}"
    if kind == "yld":
        return f"{v:.3f}%"
    if kind == "cry":
        return f"&#36;{v:,.0f}"
    if kind == "usd":
        return f"&#36;{v:,.2f}" if v < 1000 else f"&#36;{v:,.1f}"
    return f"{v:,.2f}"


def _chg_txt(m, kind):
    if not m or not _ok(m.get("pts")):
        return "—"
    if kind == "yld":
        return f'{m["pts"] * 100:+.1f} bp'
    if kind in ("fx4",):
        return f'{m["pts"]:+.4f}'
    return f'{m["pts"]:+,.2f}'


def _inr(x):
    if not _ok(x):
        return "—"
    a, sign = abs(x), ("-" if x <= -0.5 else "")
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return f"{sign}₹{a:,.0f}"


def _heat(v, lim=3.0):
    """Background colour for a % change: deeper green / red as the move grows (clamped at ±lim)."""
    if not _ok(v):
        return "#1a1a1a"
    k = min(1.0, abs(v) / lim)
    if abs(v) < 0.05:
        return "#262626"
    if v > 0:
        return f"rgba(48,209,88,{0.16 + 0.62 * k:.2f})"
    return f"rgba(255,69,58,{0.16 + 0.62 * k:.2f})"


def _spark(values, w=92, h=24, color=None):
    vals = [float(v) for v in (values or []) if _ok(v)]
    if len(vals) < 3:
        return ""
    color = color or (GREEN if vals[-1] >= vals[0] else RED)
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(vals) - 1) * w:.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for i, v in enumerate(vals))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _area(values, uid, h=150):
    vals = [float(v) for v in (values or []) if _ok(v)]
    if len(vals) < 3:
        return ""
    w = 600
    color = GREEN if vals[-1] >= vals[0] else RED
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    xy = [(i / (len(vals) - 1) * w, h - (v - lo) / (hi - lo) * h) for i, v in enumerate(vals)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    base = h - (vals[0] - lo) / (hi - lo) * h
    return (f'<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="display:block;margin-top:14px">'
            f'<defs><linearGradient id="hmg{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{color}" stop-opacity=".28"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<line x1="0" y1="{base:.1f}" x2="{w}" y2="{base:.1f}" stroke="#3a3a3a" stroke-dasharray="3 4" stroke-width="1" vector-effect="non-scaling-stroke"/>'
            f'<path d="{line} L{w},{h} L0,{h} Z" fill="url(#hmg{uid})"/>'
            f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>'
            f'<div style="position:relative;height:0"><svg width="22" height="22" viewBox="-11 -11 22 22" style="position:absolute;right:-11px;'
            f'top:{xy[-1][1] - h - 11:.1f}px;overflow:visible"><circle class="hm-dot" r="8" fill="{color}"/><circle r="3.5" fill="{color}" '
            f'stroke="#0E0E10" stroke-width="1.5"/></svg></div>')


def _panel(code, title, right, body, cls="hm-pb"):
    """`code` is kept for compatibility and no longer shown."""
    return (f'<div class="hm-p"><div class="hm-pt"><b>{title}</b><span title="{_e(re.sub(r"<[^>]+>", "", right))}">{right}</span></div>'
            f'<div class="{cls}">{body}</div></div>')


def _row(cls, inner):
    return f'<div class="hm-row {cls}">{inner}</div>'


_KEEP = {"NIFTY", "SENSEX", "VIX", "USD", "INR", "EUR", "GBP", "JPY", "US", "S&P", "DAX", "FTSE", "CAC", "ASX", "KOSPI", "TAIEX",
         "WTI", "CBOE", "NSE", "BSE", "PSU", "IT", "FMCG", "MNC", "PSE", "10Y", "30Y", "EURO", "STOXX", "FIN", "/", "·", "&"}


def _nm(label) -> str:
    """'BRENT CRUDE' -> 'Brent Crude'; index names and tickers (NIFTY 50, USD / INR, S&P 500) stay as they are."""
    s = str(label or "")
    if "NIFTY" in s or "SENSEX" in s:
        return s
    return " ".join(w if (w in _KEEP or any(ch.isdigit() for ch in w)) else "-".join(p.capitalize() for p in w.split("-"))
                    for w in s.split(" "))


# ═════════════════════════ market data ═════════════════════════
def _all_tickers():
    t = [x for _, _, chain, _ in WEI + XASSET for x in chain] + [x for _, chain in SECTORS for x in chain]
    return tuple(sorted(set(t)))


def _norm(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


_YF_GUARD = threading.Lock()


def _lock_yfinance():
    """yfinance keeps shared state inside yf.download, so two downloads at the same time corrupt each other.
    Wrap yf.download once per process so every caller (this page, data_engine, the Regime page) takes turns."""
    import yfinance as yf
    with _YF_GUARD:
        if not getattr(yf, "_arka_serial", False):
            lock, orig = threading.RLock(), yf.download

            def download(*a, **k):
                with lock:
                    return orig(*a, **k)
            yf.download = download
            yf._arka_serial = True
    return yf


def _download(tickers, period):
    yf = _lock_yfinance()
    raw = yf.download(list(tickers), period=period, interval="1d", group_by="ticker", progress=False, threads=True, auto_adjust=False)
    return de._parse_download(raw, list(tickers))


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=4)
def _hist_cached(tickers: tuple, bucket: int) -> dict:
    out = {}
    for t, df in _download(tickers, "1y").items():
        s = _norm(df["Close"])
        if len(s) >= 20:
            out[t] = s
    if "^NSEI" not in out or len(out) < 0.5 * len(tickers):
        raise RuntimeError("history incomplete")       # never cached: the next run tries again
    return out


@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def _live_cached(tickers: tuple, bucket: int) -> dict:
    out = {}
    for t, df in _download(tickers, "5d").items():
        d = df.copy()
        d["Close"] = pd.to_numeric(d["Close"], errors="coerce")
        d = d.dropna(subset=["Close"])
        d = d[d["Close"] > 0]
        if len(d) < 2:
            continue
        idx = pd.to_datetime(d.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        last, prev = d.iloc[-1], d.iloc[-2]
        out[t] = {"price": float(last["Close"]), "prev": float(prev["Close"]), "date": idx[-1].normalize(),
                  "high": float(last["High"]) if "High" in d and _ok(last.get("High")) else None,
                  "low": float(last["Low"]) if "Low" in d and _ok(last.get("Low")) else None}
    if "^NSEI" not in out or len(out) < 0.5 * len(tickers):
        raise RuntimeError("quotes incomplete")        # never cached
    return out


def _metrics(t, live, hist):
    L, H = live.get(t), hist.get(t)
    if L is None and H is None:
        return None
    if L is None:
        if len(H) < 2:
            return None
        L = {"price": float(H.iloc[-1]), "prev": float(H.iloc[-2]), "date": H.index[-1], "high": None, "low": None}
    price, prev, d = L["price"], L["prev"], pd.Timestamp(L["date"])
    m = {"price": price, "pts": price - prev, "chg": (price / prev - 1) * 100 if prev else None, "date": d,
         "high": L.get("high"), "low": L.get("low"), "w1": None, "m1": None, "ytd": None, "spark": [], "series": None,
         "hi52": None, "lo52": None, "w1_pts": None, "m1_pts": None, "ytd_pts": None}
    if H is not None and len(H):
        s = pd.concat([H[H.index < d], pd.Series([price], index=[d])])
        m["series"] = s

        def ref(days):
            r = s[s.index <= d - pd.Timedelta(days=days)]
            return float(r.iloc[-1]) if len(r) else None
        for key, days in (("w1", 7), ("m1", 30)):
            r = ref(days)
            if r:
                m[key], m[key + "_pts"] = (price / r - 1) * 100, price - r
        y0 = s[s.index < pd.Timestamp(d.year, 1, 1)]
        if len(y0):
            r = float(y0.iloc[-1])
            m["ytd"], m["ytd_pts"] = (price / r - 1) * 100, price - r
        tail = s[s.index > d - pd.Timedelta(days=365)]
        m["hi52"], m["lo52"] = float(tail.max()), float(tail.min())
        m["spark"] = s.tail(23).tolist()
    return m


def market_data() -> dict:
    """{label: metrics} for every WEI / cross-asset / sector row. Never raises."""
    tick = _all_tickers()
    live = de._guarded(("hm_live31", tick), lambda: _live_cached(tick, de._bucket(90, 900)), {})
    hist = de._guarded(("hm_hist31", tick), lambda: _hist_cached(tick, int(time.time() // (6 * 3600))), {})
    out = {}
    for _, label, chain, kind in WEI + XASSET:
        for t in chain:
            m = _metrics(t, live or {}, hist or {})
            if m:
                m["kind"] = kind
                out[label] = m
                break
    for label, chain in SECTORS:
        for t in chain:
            m = _metrics(t, live or {}, hist or {})
            if m:
                out["SEC::" + label] = m
                break
    return out


def _nifty50():
    """Constituents grouped by sector; data/ind_nifty50list.csv (NSE download) overrides the built-in list."""
    f = Path("data") / "ind_nifty50list.csv"
    if f.exists():
        try:
            df = pd.read_csv(f)
            cols = {c.lower().strip(): c for c in df.columns}
            sym, ind = cols.get("symbol"), cols.get("industry")
            if sym:
                g = {}
                for _, r in df.iterrows():
                    g.setdefault(str(r[ind]).upper()[:18] if ind else "NIFTY 50", []).append(str(r[sym]).strip().upper())
                if sum(len(v) for v in g.values()) >= 45:
                    return g
        except Exception:
            pass
    return NIFTY50


def _rsi(s, n=14):
    try:
        d = s.diff().dropna()
        up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
        r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        v = float(r.iloc[-1])
        return v if math.isfinite(v) else None
    except Exception:
        return None


# ═════════════════════════ Market Mood Index (Tickertape) ═════════════════════════
_MMI_URL = "https://www.tickertape.in/market-mood-index"
_MMI_FILE = Path(".cache") / "mmi_last_known.json"
_MMI_ZONES = [(0, 30, "Extreme Fear"), (30, 50, "Fear"), (50, 70, "Greed"), (70, 100.0001, "Extreme Greed")]


def mmi_zone_color(zone):
    return {"Extreme Fear": RED, "Fear": AMBER, "Greed": "#84CC16", "Extreme Greed": GREEN}.get(zone, T2)


@st.cache_data(ttl=1800, show_spinner=False)
def _mmi_live():
    import requests
    try:
        r = requests.get(_MMI_URL, timeout=3, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
        if r.status_code != 200:
            return None
        for m in re.finditer(r'(\d{1,3}\.\d{1,2})\s*(?:<[^>]+>\s*)*Updated', r.text):
            v = float(m.group(1))
            if 0 <= v <= 100:
                zone = next(z for lo, hi, z in _MMI_ZONES if lo <= v < hi)
                return {"score": round(v, 2), "zone": zone}
    except Exception:
        return None
    return None


def get_mmi():
    live = _mmi_live()
    if live:
        try:
            _MMI_FILE.parent.mkdir(parents=True, exist_ok=True)
            _MMI_FILE.write_text(json.dumps({**live, "at": datetime.now(timezone.utc).isoformat()}))
        except Exception:
            pass
        return {"status": "live", **live}
    try:
        d = json.loads(_MMI_FILE.read_text())
        return {"status": "stale", "score": d["score"], "zone": d["zone"], "at": datetime.fromisoformat(d["at"]).astimezone(IST)}
    except Exception:
        return {"status": "unavailable"}


# ═════════════════════════ your book (portfolio + watchlist) ═════════════════════════
def _book_base(supabase):
    """Lots, sells, books and split history, memoised for 60 s (Supabase is not queried on every refresh)."""
    key = int(time.time() // 60)
    memo = st.session_state.get("_hm_book")
    if memo and memo[0] == key:
        return memo[1]
    base = None
    try:
        import user_data as ud
        lots, _ = ud.load_portfolio(supabase)
    except Exception:
        lots = st.session_state.get("pf_lots") or []
    lots = [dict(l) for l in (lots or []) if l.get("symbol")]
    clean = []
    for l in lots:
        try:
            q, p = float(l.get("quantity")), float(l.get("buy_price"))
        except (TypeError, ValueError):
            continue
        if q > 0 and p > 0:
            clean.append({**l, "symbol": str(l["symbol"]).upper().strip(), "quantity": q, "buy_price": p})
    if clean:
        try:
            import portfolio_page as pp
            store = pp.Store(supabase)
            D = store.load()
            syms = sorted({l["symbol"] for l in clean} | {s["symbol"] for s in D["sells"]})
            cache = st.session_state.get("_pf_info") or {}
            need = [s for s in syms if not (cache.get(s) or {}).get("splits") and s not in cache]
            infos = {s: cache[s] for s in syms if s in cache}
            if need and getattr(de, "yf_attr", None):
                with pp._pool(min(6, len(need))) as ex:
                    for s, sp in zip(need, ex.map(lambda x: pp._events_list(de.yf_attr(x, "splits")), need)):
                        infos[s] = {"splits": sp}
            alots, realised, problems, _ = pp.run_engine(clean, D["sells"], D["meta"], infos)
            base = {"engine": True, "alots": alots, "realised": realised, "infos": infos, "plans": D["plans"],
                    "problems": problems, "n_lots": len(clean)}
        except Exception:
            base = None
    if base is None:
        base = {"engine": False, "lots": clean, "n_lots": len(clean)}
    st.session_state["_hm_book"] = (key, base)
    return base


def _book_holdings(base, quotes):
    """-> (holdings [{symbol, qty, avg, ltp, chg, value, pnl, pnl_pct, day}], realised total)"""
    if base.get("engine"):
        try:
            import portfolio_page as pp
            holds = pp.aggregate(base["alots"], quotes, base["infos"], base["plans"], "ALL BOOKS", {}, {})
            return holds, sum(r["gain"] for r in base["realised"])
        except Exception:
            pass
    agg = {}
    for l in base.get("lots", []):
        a = agg.setdefault(l["symbol"], {"qty": 0.0, "cost": 0.0})
        a["qty"] += l["quantity"]
        a["cost"] += l["quantity"] * l["buy_price"]
    holds = []
    for s, a in agg.items():
        q = quotes.get(s) or {}
        ltp, prev = q.get("price"), q.get("prev_close")
        val = a["qty"] * ltp if _ok(ltp) else None
        holds.append({"symbol": s, "qty": a["qty"], "avg": a["cost"] / a["qty"], "invested": a["cost"], "ltp": ltp, "chg": q.get("chg"),
                      "value": val, "pnl": (val - a["cost"]) if val is not None else None,
                      "pnl_pct": (val / a["cost"] - 1) * 100 if val is not None and a["cost"] else None,
                      "day_pnl": a["qty"] * (ltp - prev) if _ok(ltp) and _ok(prev) else None})
    tot = sum(h["value"] for h in holds if h["value"] is not None)
    for h in holds:
        h["weight"] = h["value"] / tot * 100 if (h["value"] is not None and tot) else None
    return sorted(holds, key=lambda h: -(h["value"] or 0)), 0.0


# ═════════════════════════ blocks ═════════════════════════
# ═════════════════════════ blocks ═════════════════════════
CARDS = ["NIFTY 50", "SENSEX", "BANK NIFTY", "INDIA VIX", "USD / INR", "GOLD", "BRENT CRUDE", "S&P 500"]


def _cards(M, labels=CARDS):
    out = []
    for label in labels:
        m = M.get(label)
        if not m:
            continue
        kind = m.get("kind", "idx")
        ch = f'{m["pts"] * 100:+.1f} bp' if kind == "yld" else f'{_chg_txt(m, kind)} ({_pct(m["chg"])})'
        out.append(f'<div class="hm-card"><div class="n">{_e(_nm(label))}</div><div class="v">{_px(m["price"], kind)}</div>'
                   f'<div class="c {_cls(m["chg"])}">{ch}</div>{_spark(m.get("spark", [])[-20:], 130, 26)}</div>')
    return f'<div class="hm-cards">{"".join(out)}</div>' if out else ""


def _tape(M):
    """Kept for compatibility: now the index cards strip."""
    return _cards(M)


def render_tape():
    """Index cards strip alone (used on the stock page)."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="hm">{_cards(market_data())}</div>', unsafe_allow_html=True)


def _hero(M):
    m = M.get("NIFTY 50")
    if not m:
        return _panel("", "NIFTY 50", "", '<div class="hm-empty">NIFTY 50 data is unavailable right now. It retries automatically.</div>')
    s = m.get("series")
    rng = ""
    if _ok(m.get("low")) and _ok(m.get("high")) and m["high"] > m["low"]:
        p = max(0.0, min(100.0, (m["price"] - m["low"]) / (m["high"] - m["low"]) * 100))
        rng += (f'<div class="hm-rng"><div class="t"><span>Day low <b>{m["low"]:,.2f}</b></span><span>Day high <b>{m["high"]:,.2f}</b></span></div>'
                f'<div class="b"><i style="left:{p:.1f}%"></i></div></div>')
    if _ok(m.get("hi52")) and _ok(m.get("lo52")) and m["hi52"] > m["lo52"]:
        p = max(0.0, min(100.0, (m["price"] - m["lo52"]) / (m["hi52"] - m["lo52"]) * 100))
        off = (m["price"] / m["hi52"] - 1) * 100
        rng += (f'<div class="hm-rng"><div class="t"><span>52W low <b>{m["lo52"]:,.0f}</b></span><span>{off:+.1f}% from high</span>'
                f'<span>52W high <b>{m["hi52"]:,.0f}</b></span></div><div class="b"><i style="left:{p:.1f}%"></i></div></div>')
    dma50 = float(s.tail(50).mean()) if s is not None and len(s) >= 50 else None
    dma200 = float(s.tail(200).mean()) if s is not None and len(s) >= 200 else None
    rsi = _rsi(s) if s is not None and len(s) > 30 else None
    per = "".join(f'<span>{k}<b class="{_cls(v)}">{_pct(v, 1)}</b></span>' for k, v in (("1W", m["w1"]), ("1M", m["m1"]), ("YTD", m["ytd"])))
    st_ = [("50-DMA", f"{dma50:,.0f}" if dma50 else "—", _cls(m["price"] - dma50) if dma50 else ""),
           ("200-DMA", f"{dma200:,.0f}" if dma200 else "—", _cls(m["price"] - dma200) if dma200 else ""),
           ("RSI 14", f"{rsi:.0f}" if rsi else "—", "")]
    stats = "".join(f'<div><span>{k}</span><b class="{c}">{v}</b></div>' for k, v, c in st_)
    chart = _area(s.tail(66).tolist() if s is not None else [], "nifty", 140)
    body = (f'<div class="hm-hrow"><div><div class="hm-hv">{m["price"]:,.2f}</div>'
            f'<div class="hm-hc {_cls(m["chg"])}">{m["pts"]:+,.2f} ({_pct(m["chg"])}) today</div></div><div class="hm-per">{per}</div></div>'
            f'{chart}<div class="hm-stats">{stats}</div>{rng}')
    return _panel("", "NIFTY 50", f'NSE · {m["date"]:%d %b} · 3 months', body).replace(
        '<div class="hm-p">', f'<div class="hm-p hero" style="--hg:{GREEN if (m["chg"] or 0) >= 0 else RED}">', 1)


def _regime(M, breadth):
    n, v = M.get("NIFTY 50"), M.get("INDIA VIX")
    checks = []
    s = n.get("series") if n else None
    if s is not None and len(s) >= 200:
        d50, d200 = float(s.tail(50).mean()), float(s.tail(200).mean())
        checks += [("NIFTY above 50-day average", n["price"] > d50), ("NIFTY above 200-day average", n["price"] > d200),
                   ("50-day above 200-day average", d50 > d200)]
    if n and _ok(n.get("hi52")):
        checks.append(("Within 10% of 52-week high", n["price"] >= 0.9 * n["hi52"]))
    if breadth and breadth["n"] >= 30:
        checks.append((f'More stocks up than down ({breadth["adv"]} / {breadth["dec"]})', breadth["adv"] > breadth["dec"]))
    vs = v.get("series") if v else None
    if vs is not None and len(vs) >= 100:
        med = float(vs.median())
        checks.append((f"India VIX below 1-year median ({med:.1f})", v["price"] < med))
    if not checks:
        return _panel("", "Market regime", "", '<div class="hm-empty">Needs NIFTY 50 history. Retries automatically.</div>')
    score, tot = sum(1 for _, ok in checks if ok), len(checks)
    r = score / tot
    lab, c = (("Risk-on", GREEN) if r >= 0.83 else ("Constructive", "#84CC16") if r >= 0.66 else ("Neutral", YELLOW) if r >= 0.5
              else ("Cautious", AMBER) if r >= 0.33 else ("Risk-off", RED))
    meter = "".join(f'<i style="background:{c if i < score else ""}"></i>' for i in range(tot))
    rows = "".join(f'<div class="hm-chk"><span>{_e(t)}</span><b style="color:{GREEN if ok else RED}">{"Yes" if ok else "No"}</b></div>' for t, ok in checks)
    body = (f'<div class="hm-big" style="color:{c}">{lab}</div><div class="hm-cap">{score} of {tot} trend, breadth and volatility checks pass</div>'
            f'<div class="hm-meter">{meter}</div>{rows}')
    return _panel("", "Market regime", "Rule-based", body)


def _session_block(S):
    now = datetime.now(IST)
    col = GREEN if S["cls"] == "live" else AMBER if S["cls"] == "pre" else T2
    p = S.get("progress")
    prog = ""
    if p is not None:
        prog = (f'<div class="hm-prog"><i style="width:{max(0.0, min(1.0, p)) * 100:.1f}%"></i></div>'
                '<div class="hm-ticks"><span>09:15</span><span>12:00</span><span>15:30</span></div>')
    special = SPECIAL_SESSIONS.get(now.date())
    hol = "".join(f'<div class="hm-kv"><span>{d:%a %d %b}</span><b>{_e(nm.split(" · ")[0])}</b></div>'
                  for d, nm, k in upcoming_holidays(3, now.date()))

    def dd(d):
        k = (d - now.date()).days
        return f'{d:%a %d %b}' + (" · today" if k == 0 else " · tomorrow" if k == 1 else f" · in {k}d")
    exp = (f'<div class="hm-kv"><span>NIFTY weekly expiry</span><b>{dd(next_expiry(now, 1))}</b></div>'
           f'<div class="hm-kv"><span>Monthly F&amp;O expiry</span><b>{dd(next_expiry(now, 1, True))}</b></div>'
           f'<div class="hm-kv"><span>SENSEX weekly expiry</span><b>{dd(next_expiry(now, 3))}</b></div>'
           f'<div class="hm-kv"><span>Trading days left in {now.year}</span><b>{trading_days_left(now.date())}</b></div>')
    body = (f'<div class="hm-big" style="color:{col}">{_e(S["short"].capitalize())}</div><div class="hm-cap">{_e(S["detail"])}</div>{prog}'
            + (f'<div class="hm-cap" style="color:{AMBER};margin-top:8px">{_e(special)}</div>' if special else "")
            + f'<div class="hm-sub">Expiries</div>{exp}'
            + f'<div class="hm-sub">Upcoming holidays</div>{hol or "<div class=hm-cap>No more holidays listed this year.</div>"}')
    return _panel("", "NSE session", now.strftime("%a %d %b · %H:%M"), body)


def _mood(M, mmi, B=None):
    parts = []
    if mmi.get("status") in ("live", "stale"):
        sc, zone = float(mmi["score"]), mmi["zone"]
        parts.append(f'<div class="hm-big" style="color:{mmi_zone_color(zone)}">{sc:.0f} <span style="font:500 15px var(--sans)">{_e(str(zone).capitalize())}</span></div>'
                     f'<div class="hm-cap">Tickertape Market Mood Index{" · cached" if mmi["status"] == "stale" else ""}</div>'
                     f'<div class="hm-gauge"><i style="left:{max(0, min(100, sc)):.1f}%"></i></div>'
                     '<div class="hm-gl"><span>Extreme fear</span><span>Greed</span><span>Extreme greed</span></div>')
    else:
        parts.append('<div class="hm-cap">Market Mood Index is unavailable right now.</div>')
    v = M.get("INDIA VIX")
    if v:
        vs = v.get("series")
        pctl = float((vs < v["price"]).mean() * 100) if vs is not None and len(vs) >= 60 else None
        lvl = ("calm" if v["price"] < 13 else "normal" if v["price"] < 18 else "elevated" if v["price"] < 24 else "stressed")
        parts.append('<div class="hm-sub">Volatility</div>'
                     f'<div class="hm-kv"><span>India VIX</span><b>{v["price"]:.2f} <span class="{_cls(v["chg"])}">{_pct(v["chg"], 1)}</span></b></div>'
                     + (f'<div class="hm-kv"><span>1-year percentile</span><b>{pctl:.0f}th · {lvl}</b></div>' if pctl is not None else "")
                     + f'<div class="hm-kv"><span>Expected NIFTY move / day</span><b>±{v["price"] / math.sqrt(252):.2f}%</b></div>')
    if B and B["n"]:
        parts.append(f'<div class="hm-kv"><span>Advance / decline</span><b><span class="hm-up">{B["adv"]}</span> / '
                     f'<span class="hm-dn">{B["dec"]}</span></b></div>')
    fx = M.get("USD / INR")
    if fx:
        parts.append(f'<div class="hm-kv"><span>USD / INR</span><b>{fx["price"]:.2f} <span class="{_cls(-fx["chg"])}">{_pct(fx["chg"], 2)}</span></b></div>')
    return _panel("", "Sentiment", "", "".join(parts))


def _table(rows_spec, M, cols, show_date=True):
    """Desktop grouped quote table."""
    head = "".join(f"<th>{c}</th>" for c in cols)
    body, last_g = [], None
    newest = max((M[l]["date"] for _, l, _, _ in rows_spec if l in M), default=None)
    for g, label, _, kind in rows_spec:
        m = M.get(label)
        if not m:
            continue
        if g != last_g:
            body.append(f'<tr class="g"><td colspan="{len(cols)}">{_e(_nm(g))}</td></tr>')
            last_g = g
        stale = newest is not None and (newest - m["date"]).days > 4
        yl = kind == "yld"

        def cell(pct, pts):
            if yl:
                return f'<td class="{_cls(pts)}">{pts * 100:+.0f} bp</td>' if _ok(pts) else "<td>—</td>"
            return f'<td class="{_cls(pct)}">{_pct(pct, 1)}</td>'
        tds = [f'<td class="l">{_e(_nm(label))}</td>', f'<td class="n">{_px(m["price"], kind)}</td>',
               cell(m["chg"], m["pts"]) if not yl else f'<td class="{_cls(m["pts"])}">{m["pts"] * 100:+.1f} bp</td>']
        if "1W" in cols:
            tds.append(cell(m["w1"], m["w1_pts"]))
        tds += [cell(m["m1"], m["m1_pts"]), cell(m["ytd"], m["ytd_pts"]), f'<td>{_spark(m["spark"], 80, 22)}</td>']
        if show_date:
            tds.append(f'<td class="{"st" if stale else ""}" style="color:var(--tx3)">{m["date"]:%d %b}</td>')
        body.append(f'<tr>{"".join(tds)}</tr>')
    if not body:
        return '<div class="hm-empty">Quotes are unavailable right now. They retry automatically.</div>'
    return f'<div style="overflow-x:auto"><table class="hm-t"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _qlist(rows_spec, M, groups=True, limit_per_group=None):
    """Phone quote list: name · date on the left, price · 1D change on the right."""
    out, last_g, n_in_g = [], None, 0
    for g, label, _, kind in rows_spec:
        m = M.get(label)
        if not m:
            continue
        if g != last_g:
            last_g, n_in_g = g, 0
            if groups:
                out.append(f'<div class="hm-lg">{_e(_nm(g))}</div>')
        n_in_g += 1
        if limit_per_group and n_in_g > limit_per_group:
            continue
        ch = f'{m["pts"] * 100:+.1f} bp' if kind == "yld" else _pct(m["chg"])
        out.append(f'<div class="hm-li"><div class="a"><b>{_e(_nm(label))}</b><small>1M {_pct(m["m1"], 1)} · {m["date"]:%d %b}</small></div>'
                   f'<div class="z"><b>{_px(m["price"], kind)}</b><small class="{_cls(m["chg"] if kind != "yld" else m["pts"])}">{ch}</small></div></div>')
    return "".join(out) or '<div class="hm-empty">Quotes are unavailable right now.</div>'


def _breadth(nq):
    vals = [q.get("chg") for q in nq.values() if q and _ok(q.get("chg"))]
    adv = sum(1 for v in vals if v > 0.05)
    dec = sum(1 for v in vals if v < -0.05)
    return {"adv": adv, "dec": dec, "flat": len(vals) - adv - dec, "n": len(vals),
            "avg": float(np.mean(vals)) if vals else None, "med": float(np.median(vals)) if vals else None}


def _imap(groups, nq, B):
    blocks = []
    for g, syms in groups.items():
        tiles = []
        for s in syms:
            q = nq.get(s) or {}
            c, px = q.get("chg"), q.get("price")
            tip = f"{s} · ₹{px:,.2f}" if _ok(px) else f"{s} · no price"
            tiles.append(f'<div class="hm-tile{"" if _ok(c) else " nq"}" style="background:{_heat(c)}" title="{_e(tip)}"><b>{_e(s)}</b><span>{_pct(c, 1)}</span></div>')
        blocks.append(f'<div class="hm-grp"><small>{_e(_nm(g))}</small><div class="hm-tiles">{"".join(tiles)}</div></div>')
    tot = max(B["n"], 1)
    ad = (f'<div class="hm-adl"><span class="hm-up">{B["adv"]} advancing</span><span>{B["flat"]} unchanged</span>'
          f'<span class="hm-dn">{B["dec"]} declining</span></div>'
          f'<div class="hm-ad"><i style="width:{B["adv"] / tot * 100:.1f}%;background:{GREEN}"></i>'
          f'<i style="width:{B["flat"] / tot * 100:.1f}%;background:#3a3a3e"></i><i style="width:{B["dec"] / tot * 100:.1f}%;background:{RED}"></i></div>')
    leg = '<div class="hm-leg"><span>−3%</span><i></i><span>+3%</span></div>'
    return ad + f'<div class="hm-imap">{"".join(blocks)}</div>' + leg


def _sector_matrix(M):
    rows = [(label, M["SEC::" + label]) for label, _ in SECTORS if M.get("SEC::" + label)]
    if not rows:
        return '<div class="hm-empty">Sector indices are unavailable right now.</div>'
    rows.sort(key=lambda x: -(x[1]["chg"] if _ok(x[1]["chg"]) else -99))
    body = "".join(
        f'<tr><td class="l">{_e(_nm(l))}</td><td class="n">{m["price"]:,.0f}</td>'
        + "".join(f'<td class="c" style="background:{_heat(v, lim)}">{_pct(v, 1)}</td>' for v, lim in
                  ((m["chg"], 2.5), (m["w1"], 5), (m["m1"], 9), (m["ytd"], 25)))
        + f'<td>{_spark(m["spark"], 76, 22)}</td></tr>' for l, m in rows)
    return ('<div style="overflow-x:auto"><table class="hm-t hm-hm"><thead><tr><th>Sector</th><th>Last</th><th>1D</th><th>1W</th>'
            f'<th>1M</th><th>YTD</th><th>1M trend</th></tr></thead><tbody>{body}</tbody></table></div>')


def _sector_list(M):
    rows = [(label, M["SEC::" + label]) for label, _ in SECTORS if M.get("SEC::" + label)]
    if not rows:
        return '<div class="hm-empty">Sector indices are unavailable right now.</div>'
    rows.sort(key=lambda x: -(x[1]["chg"] if _ok(x[1]["chg"]) else -99))
    return "".join(f'<div class="hm-li"><div class="a"><b>{_e(_nm(l))}</b><small>1W {_pct(m["w1"], 1)} · 1M {_pct(m["m1"], 1)}</small></div>'
                   f'<div class="z"><span class="hm-pct" style="background:{_heat(m["chg"], 2.5)};color:#fff">{_pct(m["chg"], 2)}</span></div></div>'
                   for l, m in rows)


def _movers(nq):
    ranked = sorted([(s, q) for s, q in nq.items() if q and _ok(q.get("chg"))], key=lambda x: -x[1]["chg"])
    if not ranked:
        return '<div class="hm-empty">NIFTY 50 quotes are unavailable right now.</div>'

    def col(items, title):
        if not items:
            return f'<div><div class="hm-lg">{title}</div><div class="hm-empty">None today.</div></div>'
        rows = "".join(f'<div class="hm-li"><div class="a"><b>{_e(s)}</b><small>₹{q["price"]:,.2f}</small></div>'
                       f'<div class="z"><small class="{_cls(q["chg"])}" style="font-size:13.5px">{_pct(q["chg"])}</small></div></div>' for s, q in items)
        return f'<div><div class="hm-lg">{title}</div>{rows}</div>'
    g = [x for x in ranked[:5] if x[1]["chg"] > 0]
    l = [x for x in ranked[::-1][:5] if x[1]["chg"] < 0]
    return f'<div class="hm-mv">{col(g, "Top gainers")}{col(l, "Top losers")}</div>'


def _book(holds, realised, wl, wl_label, quotes):
    valid = [h for h in holds if h.get("value") is not None]
    out = []
    if holds:
        val = sum(h["value"] for h in valid)
        inv = sum(h["invested"] for h in valid)
        day = sum(h["day_pnl"] for h in valid if h.get("day_pnl") is not None)
        pnl = val - inv
        dp = day / (val - day) * 100 if (val - day) else None
        pp = pnl / inv * 100 if inv else None
        out.append('<div class="hm-kp">'
                   f'<div><small>Current value</small><b>{_inr(val)}</b><em style="color:var(--tx3)">{len(holds)} holdings</em></div>'
                   f'<div><small>Today</small><b class="{_cls(day)}">{_inr(day)}</b><em class="{_cls(dp)}">{_pct(dp)}</em></div>'
                   f'<div><small>Unrealised P&amp;L</small><b class="{_cls(pnl)}">{_inr(pnl)}</b><em class="{_cls(pp)}">{_pct(pp)}</em></div>'
                   f'<div><small>Realised P&amp;L</small><b class="{_cls(realised) if realised else ""}">{_inr(realised)}</b><em style="color:var(--tx3)">all time</em></div></div>')
        def hrow(h):
            if not _ok(h.get("ltp")):
                return (f'<div class="hm-li" style="opacity:.6"><div class="a"><b>{_e(h["symbol"])}</b><small>Price not available right now</small></div>'
                        '<div class="z"></div></div>')
            return (f'<div class="hm-li"><div class="a"><b>{_e(h["symbol"])}</b><small>₹{h["ltp"]:,.2f} · {_pct(h.get("weight"), 1, False)} of book</small></div>'
                    f'<div class="z"><b class="{_cls(h.get("pnl_pct"))}">{_pct(h.get("pnl_pct"), 1)}</b>'
                    f'<small class="{_cls(h.get("chg"))}">{_pct(h.get("chg"))} today</small></div></div>')
        rows = "".join(hrow(h) for h in holds[:6])
        more = f'<div class="hm-empty">+ {len(holds) - 6} more in Portfolio</div>' if len(holds) > 6 else ""
        out.append(rows + more)
    else:
        out.append('<div class="hm-empty"><b>No holdings yet.</b> Add buys in Portfolio to see value and P&amp;L here.</div>')
    if wl:
        rows = []
        for s in wl[:6]:
            q = quotes.get(s) or {}
            rows.append(f'<div class="hm-li"><div class="a"><b>{_e(s)}</b></div><div class="z"><b>{"₹" + format(q["price"], ",.2f") if _ok(q.get("price")) else "—"}</b>'
                        f'<small class="{_cls(q.get("chg"))}">{_pct(q.get("chg"))}</small></div></div>')
        out.append(f'<div class="hm-lg">{_e(wl_label)}</div>{"".join(rows)}')
    return "".join(out)


def _rotation():
    """Snapshot published by MONEY FLOW (money_flow._publish) into st.session_state['mf_rot']."""
    snap = st.session_state.get("mf_rot")
    if not snap or snap.get("table") is None:
        return '<div class="hm-empty">Open <b>Money flow</b> and load sector data once this session to see where money is rotating.</div>'
    tbl = snap["table"]
    colors = {"ROTATING IN": GREEN, "LEADING": "#8AD66B", "STRETCHED": YELLOW, "WATCH": CYAN, "WEAKENING": AMBER, "LAGGING": RED}
    rows = []
    for r in tbl.sort_values("score", ascending=False).head(6).to_dict("records"):
        sc, ex = r.get("score"), r.get("ex1m")
        rows.append(f'<div class="hm-li"><div class="a"><b>{_e(str(r["sector"])[:26])}</b><small>Score {(f"{sc:.0f}" if _ok(sc) else "—")} · RS 1M '
                    f'<span class="{_cls(ex)}">{_pct(ex, 1)}</span></small></div>'
                    f'<div class="z"><span class="hm-pill" style="color:{colors.get(r["action"], T2)}">{_e(str(r["action"]).capitalize())}</span></div></div>')
    return "".join(rows) + f'<div class="hm-empty" style="padding-top:6px">vs {_e(snap.get("bench", "market"))} · {_e(snap.get("asof", ""))}</div>'


WORLD = [("Tokyo", "Asia/Tokyo", (9, 0), (15, 30)), ("Hong Kong", "Asia/Hong_Kong", (9, 30), (16, 0)),
         ("Shanghai", "Asia/Shanghai", (9, 30), (15, 0)), ("Mumbai · NSE", "Asia/Kolkata", (9, 15), (15, 30)),
         ("Frankfurt", "Europe/Berlin", (9, 0), (17, 30)), ("London", "Europe/London", (8, 0), (16, 30)),
         ("New York", "America/New_York", (9, 30), (16, 0))]


def _world_clock():
    if ZoneInfo is None:
        return ""
    now_utc = datetime.now(timezone.utc)
    rows = []
    for name, tz, o, c in WORLD:
        try:
            lt = now_utc.astimezone(ZoneInfo(tz))
        except Exception:
            continue
        t = lt.hour * 60 + lt.minute
        if tz == "Asia/Kolkata":
            opened = nse_session(lt.astimezone(IST))["cls"] == "live"
        else:
            opened = lt.weekday() < 5 and o[0] * 60 + o[1] <= t < c[0] * 60 + c[1]
        tag = f'<span class="hm-pill" style="color:{GREEN if opened else T3}">{"Open" if opened else "Closed"}</span>'
        rows.append(f'<div class="hm-clk"><span>{_e(name)}</span><b>{lt:%a %H:%M}</b>{tag}</div>')
    return "".join(rows)


# ═════════════════════════ page ═════════════════════════
def _safe_mmi():
    try:
        return get_mmi()
    except Exception:
        return {"status": "unavailable"}


def _regime_chip():
    """Market regime chip, only when the Regime page has already computed it (never triggers a download here)."""
    try:
        qd = sys.modules.get("quant_data")
        for p in list(getattr(qd, "_MEM", {}).values()) if qd else []:
            df = p.get("_regime")
            if df is not None and len(df):
                r = df.iloc[-1]
                col = {"Confirmed uptrend": GREEN, "Under pressure": YELLOW, "Rally attempt": "#4FA3FF", "Correction": RED}.get(r["state"], T2)
                return f'<span class="hm-chip k" style="--c:{col}"><i></i>Regime: {_e(r["state"])} · {r["score"]:.0f}</span>'
    except Exception:
        pass
    return ""


def _brief(M, B, S):
    now = datetime.now(IST)
    greet = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    n = M.get("NIFTY 50")
    bits = []
    if n and _ok(n.get("chg")):
        word = "up" if n["chg"] > 0.05 else "down" if n["chg"] < -0.05 else "flat"
        bits.append(f'NIFTY 50 is <b class="{_cls(n["chg"])}">{word}{"" if word == "flat" else " " + _pct(abs(n["chg"]), 2, False)}</b> at <b>{n["price"]:,.0f}</b>.')
    if B and B.get("n"):
        bits.append(f'<b>{B["adv"]}</b> of {B["n"]} NIFTY stocks are up' + ("." if B["n"] >= 45 else " (quotes for the rest have not arrived yet)."))
    secs = [(l, M["SEC::" + l]["chg"]) for l, _ in SECTORS if M.get("SEC::" + l) and _ok(M["SEC::" + l].get("chg"))]
    if len(secs) >= 4:
        secs.sort(key=lambda x: x[1])
        (wl_, wv), (bl_, bv) = secs[0], secs[-1]
        bits.append(f'{_e(_nm(bl_))} leads (<b class="{_cls(bv)}">{_pct(bv, 1)}</b>), {_e(_nm(wl_))} lags (<b class="{_cls(wv)}">{_pct(wv, 1)}</b>).')
    v = M.get("INDIA VIX")
    if v:
        lvl = "calm" if v["price"] < 13 else "normal" if v["price"] < 18 else "elevated" if v["price"] < 24 else "stressed"
        bits.append(f'India VIX <b>{v["price"]:.1f}</b>, {lvl}.')
    asof = f'Prices as of {n["date"]:%d %b}, delayed' if n else "Prices delayed"
    chip_cls = {"live": "live", "pre": "pre"}.get(S["cls"], "")
    return (f'<div class="hm-brief"><div><div class="g">{greet} · {now:%a %d %b}</div><div class="t">Markets today</div>'
            f'<div class="s">{" ".join(bits) or "Market data is loading."}</div></div>'
            f'<div class="hm-chips"><span class="hm-chip {chip_cls}"><i></i>{_e(S["detail"])}</span>{_regime_chip()}'
            f'<span class="hm-chip">{asof}</span></div></div>')


def _skeleton():
    try:
        import auth
        logo = auth.logo_data_uri(96)
    except Exception:
        logo = ""
    img = f'<img src="{logo}" alt="">' if logo else ""
    return ('<div class="hm"><div class="hm-load">' + img + '<div><b>Reading the market</b><span>Indices, sectors, your stocks and the mood index</span></div>'
            '<div class="bar"><i></i></div></div>'
            '<div class="sk-cards">' + '<div class="sk"></div>' * 7 + '</div>'
            '<div class="sk-grid"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div>'
            '<div class="sk-grid2"><div class="sk"></div><div class="sk"></div></div></div>')


def _gather(supabase):
    """Everything the page needs. Independent downloads run in parallel; all of them are cached underneath."""
    groups = _nifty50()
    n50 = [s for v in groups.values() for s in v]
    wl = list(dict.fromkeys(st.session_state.get("watchlist", []) or []))
    wl_label = "Your watchlist"
    if not wl:
        wl, wl_label = list(dict.fromkeys(st.session_state.get("admin_watchlist", []) or [])), "Arka watchlist"
    base = _book_base(supabase)
    held = sorted({l["symbol"] for l in base.get("alots", []) if l.get("open", 0) > 1e-9} if base.get("engine")
                  else {l["symbol"] for l in base.get("lots", [])})
    want = tuple(dict.fromkeys(n50 + wl[:8] + held))
    M = market_data()
    quotes = get_quotes(want) or {}
    mmi = _safe_mmi()
    return M, groups, n50, wl, wl_label, base, quotes, mmi


def _body(supabase, compact=False):
    M, groups, n50, wl, wl_label, base, quotes, mmi = _gather(supabase)
    return _draw(M, groups, n50, wl, wl_label, base, quotes, mmi, compact)


def _draw(M, groups, n50, wl, wl_label, base, quotes, mmi, compact):
    root = "hm"
    n50 = [s for v in groups.values() for s in v]
    nq = {s: quotes.get(s) for s in n50}
    B = _breadth(nq)
    S = nse_session()
    holds, realised = _book_holdings(base, quotes)

    head = _brief(M, B, S)
    imap = _panel("", "NIFTY 50 heatmap", f"{B['n']} stocks · 1-day change", _imap(groups, nq, B), "hm-pb np")
    movers = _panel("", "Top movers", "NIFTY 50 · today", _movers(nq), "hm-pb np")
    book = _panel("", "Your portfolio", "", _book(holds, realised, wl, wl_label, quotes), "hm-pb np")

    if compact:
        html_ = (head + _cards(M)
                 + _row("", _hero(M) + movers + book + imap
                        + _panel("", "Sectors", "Sorted by today", _sector_list(M), "hm-pb np")
                        + _mood(M, mmi, B) + _regime(M, B)
                        + _panel("", "Global markets", "", _qlist(WEI, M), "hm-pb np")
                        + _panel("", "Commodities, currencies & more", "", _qlist(XASSET, M), "hm-pb np")
                        + _session_block(S)))
        st.markdown(f'<div class="{root} hm-compact">{html_}</div>', unsafe_allow_html=True)
        return

    wei = _table(WEI, M, ["Index", "Last", "1D", "1W", "1M", "YTD", "1M trend", "Date"])
    xa = _table(XASSET, M, ["Instrument", "Last", "1D", "1M", "YTD", "1M trend", "Date"])
    html_ = (head + _cards(M)
             + _row("hm-r1", _hero(M) + _mood(M, mmi, B) + _regime(M, B))
             + _row("hm-r2", movers + imap)
             + _row("hm-r3", _panel("", "Sectors", "Sorted by today", _sector_matrix(M), "hm-pb np") + book)
             + _row("hm-r4", _panel("", "Global indices", "Local currency", wei, "hm-pb np")
                    + _panel("", "Commodities, currencies & more", "", xa, "hm-pb np"))
             + _row("hm-r5", _session_block(S) + _panel("", "Sector rotation", "From Money flow", _rotation(), "hm-pb np")
                    + _panel("", "World markets", "Local time", _world_clock())))
    st.markdown(f'<div class="{root}">{html_}</div>', unsafe_allow_html=True)


def _body_live(supabase, compact=False):
    _body(supabase, compact)


def _body_closed(supabase, compact=False):
    _body(supabase, compact)


_frag_live = st.fragment(run_every=60)(_body_live) if hasattr(st, "fragment") else _body_live
_frag_closed = st.fragment(_body_closed) if hasattr(st, "fragment") else _body_closed


def render_home(supabase=None, on_open=None, nav=None, compact=None):
    """HOME page. compact=True gives the phone layout (app.py passes it; default: st.session_state['_ak_phone'])."""
    if compact is None:
        compact = bool(st.session_state.get("_ak_phone", False))
    st.markdown(_CSS, unsafe_allow_html=True)
    S = nse_session()
    ph = None
    if not st.session_state.get("_hm_warm"):                 # first visit only: a skeleton while the data loads (loaded once, below)
        ph = st.empty()
        ph.markdown(_skeleton(), unsafe_allow_html=True)
    try:
        (_frag_live if S["cls"] in ("live", "pre") else _frag_closed)(supabase, compact)
    finally:
        if ph is not None:
            ph.empty()
        st.session_state["_hm_warm"] = True
    st.markdown('<div class="hm-foot">Prices from Yahoo Finance, delayed; changes are close-to-close. Regime, mood and heatmap are information, '
                'not advice. NIFTY 50 list as of 30 Sep 2026 · Market Mood Index © Tickertape.</div>', unsafe_allow_html=True)
