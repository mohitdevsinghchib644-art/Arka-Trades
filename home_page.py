"""
home_page.py — Arka Trades · Market Monitor (the HOME page), v1

A Bloomberg Launchpad-style monitor for Indian markets. Every block is plain HTML / SVG (no Plotly, no
per-row widgets), so the whole page is built in milliseconds once data is cached.

  TAPE   scrolling cross-asset ticker (pauses on hover)
  NIFTY  hero: last, change, 3-month chart, day range, 52-week range, 1W / 1M / YTD, 50 / 200-DMA, RSI
  REGM   market regime: six transparent checks -> RISK-ON ... RISK-OFF
  SESS   NSE session with progress bar, 2026 holiday calendar, next session, Muhurat trading
  MOOD   Market Mood Index (Tickertape) + India VIX with its 1-year percentile
  WEI    world equity indices by region: last, change, 1D / 1W / 1M / YTD, 1-month trend, date
  XA     cross-asset: commodities, currencies (vs INR), US yields (in basis points), volatility, crypto
  IMAP   NIFTY 50 heatmap grouped by sector + advance / decline breadth
  SECT   NSE sectoral indices heat matrix 1D / 1W / 1M / YTD
  MOV    NIFTY 50 top gainers / losers
  BOOK   your portfolio (same split-adjusted FIFO engine as the Portfolio page) and watchlist
  ROT    sector rotation snapshot from Money Flow (when it has been loaded this session)
  WCLK   world market clock (open / closed by local exchange hours)
  FUNC   function launcher (the same codes work in the command line: MF, RES, PORT, WL, NEWS, EQS, AI)

Data: Yahoo Finance (delayed). Two batched downloads for ~60 instruments: last 5 days every 90 s while NSE
is open (15 min when closed) and 1 year of history every 6 h. Stale-while-error via data_engine._guarded.
"""
from __future__ import annotations

import html
import json
import math
import re
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

FUNCS = [("MF", "MONEY FLOW", "flow", "Sector rotation radar, Rotation Lab backtests, FPI sector flows, FII / DII"),
         ("RES", "RESEARCH", "research", "Financials, peers, valuation, health score and technicals"),
         ("PORT", "PORTFOLIO", "portfolio", "P&amp;L, tax, risk, rebalance, dividends and reports"),
         ("WL", "WATCHLIST", "watchlist", "Live list with RSI, PDH / PDL levels and signals"),
         ("NEWS", "NEWS DESK", "news", "Scored wire, BSE filings, news pulse and sector tone"),
         ("EQS", "SCREENER", "smart_scan", "Rule-based equity screens across the NSE universe"),
         ("AI", "ARKA AI", "analysis", "AI chart analysis against your own trading rules")]


# ═════════════════════════ CSS ═════════════════════════
_CSS = """<style>
.hm-head{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:10px 2px 9px;border-bottom:1px solid var(--line);margin-top:4px;}
.hm-head .t{font:800 14px var(--mono);letter-spacing:2.4px;color:var(--tx);white-space:nowrap;}
.hm-head .t em{font-style:normal;color:#000;background:var(--amber);padding:2px 7px;margin-right:10px;border-radius:2px;font-size:10px;letter-spacing:1.4px;}
.hm-head .t small{font:600 10px var(--sans);letter-spacing:0;color:var(--tx3);margin-left:12px;}
.hm-head .m{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
.hm-tag{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border:1px solid var(--line2);border-radius:2px;font:700 9px var(--mono);letter-spacing:.8px;color:var(--tx2);white-space:nowrap;}
.hm-tag i{width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block;}
.hm-tag.live{color:var(--green);border-color:rgba(48,209,88,.4);background:rgba(48,209,88,.07);} .hm-tag.live i{box-shadow:0 0 8px var(--green);animation:hmpulse 2s infinite;}
.hm-tag.pre{color:var(--amber);border-color:rgba(255,159,10,.4);background:rgba(255,159,10,.07);}
@keyframes hmpulse{0%,100%{opacity:1}50%{opacity:.3}}
.hm-tape{position:relative;overflow:hidden;height:30px;border:1px solid var(--line);border-left:3px solid var(--amber);background:#000;margin-top:8px;}
.hm-tape .tr{display:inline-flex;white-space:nowrap;padding:8px 0;animation:hmtape 140s linear infinite;will-change:transform;}
.hm-tape:hover .tr{animation-play-state:paused;}
.hm-tape span{font:600 11px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-right:30px;}
.hm-tape span b{font-weight:700;color:var(--amber);margin-right:7px;letter-spacing:.4px;}
.hm-tape span em{font-style:normal;margin-left:7px;}
@keyframes hmtape{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.hm-row{display:grid;gap:8px;margin-top:8px;align-items:stretch;}
.hm-r1{grid-template-columns:1.55fr 1fr 1fr 1fr;}
.hm-r2{grid-template-columns:1.18fr 1fr;}
.hm-r3{grid-template-columns:1.32fr 1fr;}
.hm-r4{grid-template-columns:1fr 1.25fr 1fr;}
@media(max-width:1500px){.hm-r1{grid-template-columns:1fr 1fr;}.hm-r4{grid-template-columns:1fr 1fr;}.hm-r4>.hm-p:last-child{grid-column:1/-1;}}
@media(max-width:1150px){.hm-r2,.hm-r3,.hm-r4{grid-template-columns:1fr;}.hm-r4>.hm-p:last-child{grid-column:auto;}}
@media(max-width:720px){.hm-r1{grid-template-columns:1fr;}.hm-head{flex-direction:column;align-items:flex-start;}}
.hm-p{background:var(--s1);border:1px solid var(--line);min-width:0;display:flex;flex-direction:column;}
.hm-pt{height:30px;flex-shrink:0;padding:0 10px;display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid var(--line);background:var(--s2);}
.hm-pt b{font:700 9px var(--mono);letter-spacing:1.3px;color:var(--tx);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-pt b i{font-style:normal;color:#000;background:var(--amber);padding:1px 5px;margin-right:8px;border-radius:2px;font-size:8px;letter-spacing:.8px;}
.hm-pt span{font:600 8.5px var(--mono);letter-spacing:.6px;color:var(--tx3);white-space:nowrap;}
.hm-pb{padding:10px 12px;flex:1;min-width:0;}
.hm-pb.np{padding:0;}
.hm-sub{font:700 8px var(--mono);letter-spacing:1.3px;color:var(--tx3);padding:9px 0 5px;text-transform:uppercase;}
.hm-up{color:var(--green) !important;} .hm-dn{color:var(--red) !important;} .hm-fl{color:var(--tx3) !important;}
.hm-t{width:100%;border-collapse:collapse;font:11px var(--mono);font-variant-numeric:tabular-nums;}
.hm-t th{font:700 8px var(--mono);letter-spacing:.8px;color:var(--tx3);text-align:right;padding:6px 8px;border-bottom:1px solid var(--line2);text-transform:uppercase;white-space:nowrap;background:#0c0c0c;position:sticky;top:0;}
.hm-t th:first-child,.hm-t td:first-child{text-align:left;}
.hm-t td{padding:4px 8px;line-height:1.35;border-bottom:1px solid #121212;text-align:right;color:var(--tx2);white-space:nowrap;}
.hm-t td.n{color:var(--tx);font-weight:600;font-family:var(--mono);}
.hm-t td.l{color:var(--tx);font-weight:700;}
.hm-t td.st{opacity:.45;}
.hm-t tr.g td{font:700 8px var(--mono);letter-spacing:1.3px;color:var(--amber);background:#0b0b0b;padding:6px 8px 4px;text-align:left;border-bottom:1px solid var(--line);}
.hm-t tbody tr:not(.g):hover td{background:rgba(255,159,10,.045);}
.hm-t svg{vertical-align:middle;}
.hm-hv{font:700 30px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);letter-spacing:-.5px;line-height:1.05;}
.hm-hc{font:700 13px var(--mono);font-variant-numeric:tabular-nums;margin-top:4px;}
.hm-hrow{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
.hm-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 12px;margin-top:8px;}
.hm-stats div{display:flex;justify-content:space-between;font:10px var(--mono);color:var(--tx3);border-bottom:1px dotted #1f1f1f;padding:3px 0;}
.hm-stats b{color:var(--tx);font-weight:600;font-variant-numeric:tabular-nums;}
.hm-rng{margin-top:9px;}
.hm-rng .t{display:flex;justify-content:space-between;font:600 8.5px var(--mono);letter-spacing:.5px;color:var(--tx3);margin-bottom:4px;}
.hm-rng .b{position:relative;height:4px;background:linear-gradient(90deg,rgba(255,69,58,.45),#2a2a2a 50%,rgba(48,209,88,.45));border-radius:2px;}
.hm-rng .b i{position:absolute;top:-4px;width:3px;height:12px;background:var(--amber);border-radius:1px;transform:translateX(-1px);box-shadow:0 0 6px rgba(255,159,10,.7);}
.hm-big{font:800 22px var(--mono);letter-spacing:.5px;line-height:1.1;}
.hm-cap{font:10px var(--sans);color:var(--tx3);margin-top:3px;line-height:1.5;}
.hm-chk{display:flex;justify-content:space-between;gap:8px;font:10.5px var(--sans);color:var(--tx2);padding:4px 0;border-bottom:1px solid #141414;}
.hm-chk:last-child{border-bottom:0;}
.hm-chk b{font:700 10px var(--mono);white-space:nowrap;}
.hm-meter{display:flex;gap:3px;margin:8px 0 6px;} .hm-meter i{flex:1;height:5px;background:#1d1d1d;border-radius:1px;}
.hm-prog{position:relative;height:6px;background:#161616;margin:12px 0 4px;border-radius:1px;}
.hm-prog i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,rgba(48,209,88,.5),var(--green));}
.hm-prog s{position:absolute;top:-3px;width:1px;height:12px;background:#3a3a3a;}
.hm-ticks{display:flex;justify-content:space-between;font:600 8px var(--mono);color:var(--tx3);}
.hm-kv{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #141414;font:10.5px var(--sans);color:var(--tx3);}
.hm-kv:last-child{border-bottom:0;}
.hm-kv b{font:600 10.5px var(--mono);color:var(--tx);font-variant-numeric:tabular-nums;white-space:nowrap;}
.hm-gauge{position:relative;height:8px;border-radius:2px;margin:10px 0 3px;background:linear-gradient(90deg,#FF453A 0%,#FF9F0A 30%,#FF9F0A 50%,#84CC16 50%,#30D158 70%,#30D158 100%);opacity:.85;}
.hm-gauge i{position:absolute;top:-4px;width:4px;height:16px;background:#fff;border-radius:1px;transform:translateX(-2px);box-shadow:0 0 6px #000;}
.hm-gl{display:flex;justify-content:space-between;font:600 7.5px var(--mono);color:var(--tx3);letter-spacing:.5px;}
.hm-imap{display:flex;flex-wrap:wrap;gap:8px 10px;padding:10px 12px 8px;}
.hm-leg{display:flex;align-items:center;gap:8px;padding:2px 12px 12px;font:600 8px var(--mono);color:var(--tx3);letter-spacing:.5px;}
.hm-leg i{width:160px;height:6px;border-radius:1px;background:linear-gradient(90deg,rgba(255,69,58,.78),rgba(255,69,58,.16) 48%,#262626 50%,rgba(48,209,88,.16) 52%,rgba(48,209,88,.78));}
.hm-grp{display:flex;flex-direction:column;gap:3px;}
.hm-grp>small{font:700 7.5px var(--mono);letter-spacing:1px;color:var(--tx3);white-space:nowrap;}
.hm-tiles{display:flex;flex-wrap:wrap;gap:2px;max-width:456px;}
.hm-tile{width:74px;height:54px;display:flex;flex-direction:column;justify-content:center;align-items:center;border-radius:2px;color:#fff;overflow:hidden;}
.hm-tile b{font:700 10px var(--mono);letter-spacing:.2px;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hm-tile span{font:600 9.5px var(--mono);opacity:.92;margin-top:2px;font-variant-numeric:tabular-nums;}
.hm-ad{display:flex;height:6px;margin:0 12px;background:#151515;}
.hm-ad i{display:block;height:100%;}
.hm-adl{display:flex;justify-content:space-between;font:700 9px var(--mono);padding:10px 12px 5px;color:var(--tx3);letter-spacing:.6px;}
.hm-hm td.c{font-weight:700;color:#fff !important;text-align:center !important;min-width:58px;}
.hm-kp{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border-bottom:1px solid var(--line);}
.hm-kp div{background:var(--s1);padding:8px 11px;}
.hm-kp small{display:block;font:700 7.5px var(--mono);letter-spacing:1px;color:var(--tx3);}
.hm-kp b{display:block;font:700 15px var(--mono);font-variant-numeric:tabular-nums;color:var(--tx);margin-top:3px;white-space:nowrap;}
.hm-kp em{font:600 9px var(--mono);font-style:normal;}
.hm-empty{font:11px var(--sans);color:var(--tx3);line-height:1.7;padding:12px;}
.hm-empty b{color:var(--tx2);}
.hm-pill{display:inline-block;padding:1px 7px;border-radius:999px;font:800 7.5px var(--mono);letter-spacing:.7px;border:1px solid currentColor;white-space:nowrap;}
.hm-clk{display:grid;grid-template-columns:1fr auto auto;gap:4px 10px;align-items:center;font:10.5px var(--mono);padding:4px 0;border-bottom:1px solid #141414;}
.hm-clk:last-child{border-bottom:0;}
.hm-clk span{color:var(--tx2);} .hm-clk b{color:var(--tx);font-weight:600;font-variant-numeric:tabular-nums;}
.hm-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font:600 8.5px var(--mono);letter-spacing:.5px;color:var(--tx3);padding:8px 2px 0;}
.hm-fhead{display:flex;align-items:center;gap:10px;margin:16px 0 8px;}
.hm-fhead b{font:700 10px var(--mono);letter-spacing:1.6px;color:var(--tx);} .hm-fhead i{flex:1;height:1px;background:var(--line);}
.hm-fhead span{font:9px var(--mono);color:var(--tx3);letter-spacing:.5px;}
div[class*="st-key-hm_fn_"]{background:var(--s1);border:1px solid var(--line);border-top:2px solid var(--line2);padding:10px 11px 2px;gap:.15rem;transition:border-color .12s ease,background .12s ease;min-height:132px;}
div[class*="st-key-hm_fn_"]:hover{border-top-color:var(--amber);background:var(--s2);}
div[class*="st-key-hm_fn_"] .stButton>button{background:transparent !important;border:0 !important;border-top:1px solid var(--line) !important;border-radius:0 !important;
  color:var(--amber) !important;justify-content:flex-start !important;padding:7px 0 4px !important;font:700 9px var(--mono) !important;letter-spacing:1px;min-height:30px !important;}
.hm-fn .c{display:inline-block;font:800 9px var(--mono);letter-spacing:.8px;color:#000;background:var(--amber);padding:1px 6px;border-radius:2px;}
.hm-fn .t{font:700 11px var(--mono);letter-spacing:1px;color:var(--tx);margin-top:7px;}
.hm-fn .d{font:10.5px var(--sans);color:var(--tx3);line-height:1.5;margin-top:4px;min-height:32px;}
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


def _spark(values, w=74, h=18, color=None):
    vals = [float(v) for v in (values or []) if _ok(v)]
    if len(vals) < 3:
        return ""
    color = color or (GREEN if vals[-1] >= vals[0] else RED)
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(vals) - 1) * w:.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for i, v in enumerate(vals))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _area(values, uid, h=92):
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
    return (f'<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="display:block;margin-top:8px">'
            f'<defs><linearGradient id="hmg{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{color}" stop-opacity=".28"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<line x1="0" y1="{base:.1f}" x2="{w}" y2="{base:.1f}" stroke="#3a3a3a" stroke-dasharray="3 4" stroke-width="1" vector-effect="non-scaling-stroke"/>'
            f'<path d="{line} L{w},{h} L0,{h} Z" fill="url(#hmg{uid})"/>'
            f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>')


def _panel(code, title, right, body, cls="hm-pb"):
    return (f'<div class="hm-p"><div class="hm-pt"><b><i>{_e(code)}</i>{title}</b><span>{right}</span></div>'
            f'<div class="{cls}">{body}</div></div>')


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


def _download(tickers, period):
    import yfinance as yf
    raw = yf.download(list(tickers), period=period, interval="1d", group_by="ticker", progress=False, threads=True, auto_adjust=False)
    return de._parse_download(raw, list(tickers))


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=4)
def _hist_cached(tickers: tuple, bucket: int) -> dict:
    out = {}
    for t, df in _download(tickers, "1y").items():
        s = _norm(df["Close"])
        if len(s) >= 20:
            out[t] = s
    if not out:
        raise RuntimeError("history unavailable")      # never cached
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
    if not out:
        raise RuntimeError("quotes unavailable")
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
    live = de._guarded(("hm_live", tick), lambda: _live_cached(tick, de._bucket(90, 900)), {})
    hist = de._guarded(("hm_hist", tick), lambda: _hist_cached(tick, int(time.time() // (6 * 3600))), {})
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
def _tape(M):
    items = []
    for label in TAPE:
        m = M.get(label)
        if not m:
            continue
        kind = m.get("kind", "idx")
        ch = f'{m["pts"] * 100:+.1f}bp' if kind == "yld" else _pct(m["chg"])
        items.append(f'<span><b>{_e(label)}</b>{_px(m["price"], kind)}<em class="{_cls(m["chg"])}">{_arrow(m["chg"])} {ch}</em></span>')
    if not items:
        return ""
    run = "".join(items)
    return f'<div class="hm-tape"><div class="tr">{run}{run}</div></div>'


def render_tape():
    """Ticker tape alone (used on the security page)."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(_tape(market_data()), unsafe_allow_html=True)


def _hero(M):
    m = M.get("NIFTY 50")
    if not m:
        return _panel("NIFTY", "NIFTY 50 · NSE", "", '<div class="hm-empty">NIFTY 50 data is unavailable right now. It retries automatically.</div>')
    s = m.get("series")
    rng_html = ""
    if _ok(m.get("low")) and _ok(m.get("high")) and m["high"] > m["low"]:
        p = max(0.0, min(100.0, (m["price"] - m["low"]) / (m["high"] - m["low"]) * 100))
        rng_html += (f'<div class="hm-rng"><div class="t"><span>DAY LOW {m["low"]:,.2f}</span><span>DAY RANGE</span><span>DAY HIGH {m["high"]:,.2f}</span></div>'
                     f'<div class="b"><i style="left:{p:.1f}%"></i></div></div>')
    if _ok(m.get("hi52")) and _ok(m.get("lo52")) and m["hi52"] > m["lo52"]:
        p = max(0.0, min(100.0, (m["price"] - m["lo52"]) / (m["hi52"] - m["lo52"]) * 100))
        off = (m["price"] / m["hi52"] - 1) * 100
        rng_html += (f'<div class="hm-rng"><div class="t"><span>52W LOW {m["lo52"]:,.2f}</span><span>{off:+.1f}% FROM 52W HIGH</span>'
                     f'<span>52W HIGH {m["hi52"]:,.2f}</span></div><div class="b"><i style="left:{p:.1f}%"></i></div></div>')
    dma50 = float(s.tail(50).mean()) if s is not None and len(s) >= 50 else None
    dma200 = float(s.tail(200).mean()) if s is not None and len(s) >= 200 else None
    rsi = _rsi(s) if s is not None and len(s) > 30 else None
    st_ = [("1W", _pct(m["w1"]), _cls(m["w1"])), ("1M", _pct(m["m1"]), _cls(m["m1"])), ("YTD", _pct(m["ytd"]), _cls(m["ytd"])),
           ("50-DMA", f"{dma50:,.0f}" if dma50 else "—", _cls(m["price"] - dma50) if dma50 else ""),
           ("200-DMA", f"{dma200:,.0f}" if dma200 else "—", _cls(m["price"] - dma200) if dma200 else ""),
           ("RSI 14", f"{rsi:.0f}" if rsi else "—", "")]
    stats = "".join(f'<div><span>{k}</span><b class="{c}">{v}</b></div>' for k, v, c in st_)
    chart = _area(s.tail(66).tolist() if s is not None else [], "nifty")
    body = (f'<div class="hm-hrow"><div><div class="hm-hv">{m["price"]:,.2f}</div>'
            f'<div class="hm-hc {_cls(m["chg"])}">{_arrow(m["chg"])} {m["pts"]:+,.2f} ({_pct(m["chg"])})</div></div>'
            f'<div style="text-align:right;font:600 9px var(--mono);color:var(--tx3);line-height:1.7">3-MONTH TREND<br>'
            f'<span class="{_cls(m["m1"])}">1M {_pct(m["m1"], 1)}</span></div></div>'
            f'{chart}<div class="hm-stats">{stats}</div>{rng_html}')
    return _panel("NIFTY", "NIFTY 50 · NSE", f'{m["date"]:%d %b %Y} · DELAYED', body)


def _regime(M, breadth):
    n, v = M.get("NIFTY 50"), M.get("INDIA VIX")
    checks = []
    s = n.get("series") if n else None
    if s is not None and len(s) >= 200:
        d50, d200 = float(s.tail(50).mean()), float(s.tail(200).mean())
        checks += [("NIFTY above its 50-day average", n["price"] > d50), ("NIFTY above its 200-day average", n["price"] > d200),
                   ("50-day average above 200-day (uptrend)", d50 > d200)]
    if n and _ok(n.get("hi52")):
        checks.append(("Within 10% of the 52-week high", n["price"] >= 0.9 * n["hi52"]))
    if breadth and breadth["n"] >= 30:
        checks.append((f'More NIFTY 50 stocks up than down ({breadth["adv"]}/{breadth["dec"]})', breadth["adv"] > breadth["dec"]))
    vs = v.get("series") if v else None
    if vs is not None and len(vs) >= 100:
        med = float(vs.median())
        checks.append((f"India VIX below its 1-year median ({med:.1f})", v["price"] < med))
    if not checks:
        return _panel("REGM", "MARKET REGIME", "", '<div class="hm-empty">Needs NIFTY 50 history — retries automatically.</div>')
    score, tot = sum(1 for _, ok in checks if ok), len(checks)
    r = score / tot
    lab, c = (("RISK-ON", GREEN) if r >= 0.83 else ("CONSTRUCTIVE", "#84CC16") if r >= 0.66 else ("NEUTRAL", YELLOW) if r >= 0.5
              else ("CAUTIOUS", AMBER) if r >= 0.33 else ("RISK-OFF", RED))
    meter = "".join(f'<i style="background:{c if i < score else "#1d1d1d"}"></i>' for i in range(tot))
    rows = "".join(f'<div class="hm-chk"><span>{_e(t)}</span><b style="color:{GREEN if ok else RED}">{"✓ YES" if ok else "✗ NO"}</b></div>' for t, ok in checks)
    body = (f'<div class="hm-big" style="color:{c}">{lab}</div><div class="hm-cap">{score} of {tot} trend, breadth and volatility checks pass</div>'
            f'<div class="hm-meter">{meter}</div>{rows}')
    return _panel("REGM", "MARKET REGIME", "RULE-BASED · NOT ADVICE", body)


def _session_block(S):
    now = datetime.now(IST)
    col = GREEN if S["cls"] == "live" else AMBER if S["cls"] == "pre" else T2
    p = S.get("progress")
    prog = ""
    if p is not None:
        ticks = "".join(f'<s style="left:{(m - OPEN) / (CLOSE - OPEN) * 100:.1f}%"></s>' for m in (10 * 60, 11 * 60, 12 * 60, 13 * 60, 14 * 60, 15 * 60))
        prog = (f'<div class="hm-prog"><i style="width:{max(0.0, min(1.0, p)) * 100:.1f}%"></i>{ticks}</div>'
                '<div class="hm-ticks"><span>09:15</span><span>11:00</span><span>13:00</span><span>15:30</span></div>')
    special = SPECIAL_SESSIONS.get(now.date())
    hol = "".join(f'<div class="hm-kv"><span>{d:%a %d %b}</span><b style="color:{AMBER if k == "SPECIAL" else T2};font-family:var(--sans);font-weight:600">'
                  f'{_e(nm.split(" · ")[0])}</b></div>' for d, nm, k in upcoming_holidays(3, now.date()))
    def dd(d):
        k = (d - now.date()).days
        return f'{d:%a %d %b}' + (" · today" if k == 0 else " · tomorrow" if k == 1 else f" · in {k}d")
    exp = (f'<div class="hm-kv"><span>NIFTY weekly expiry · Tue</span><b>{dd(next_expiry(now, 1))}</b></div>'
           f'<div class="hm-kv"><span>NSE monthly F&amp;O expiry</span><b>{dd(next_expiry(now, 1, True))}</b></div>'
           f'<div class="hm-kv"><span>SENSEX weekly · Thu</span><b>{dd(next_expiry(now, 3))}</b></div>'
           f'<div class="hm-kv"><span>Trading days left in {now.year}</span><b>{trading_days_left(now.date())}</b></div>')
    body = (f'<div class="hm-big" style="color:{col}">{_e(S["short"])}</div><div class="hm-cap">{_e(S["detail"])}</div>{prog}'
            + (f'<div class="hm-cap" style="color:{AMBER};margin-top:6px">{_e(special)}</div>' if special else "")
            + f'<div class="hm-sub">Derivatives &amp; calendar</div>{exp}'
            + f'<div class="hm-sub">Upcoming exchange calendar</div>{hol or "<div class=hm-cap>No more holidays listed this year.</div>"}')
    return _panel("SESS", "NSE SESSION", now.strftime("%a %d %b · %H:%M IST"), body)


def _mood(M, mmi, B=None):
    parts = []
    if mmi.get("status") in ("live", "stale"):
        sc, zone = float(mmi["score"]), mmi["zone"]
        parts.append(f'<div class="hm-hrow"><div><div class="hm-big" style="color:{mmi_zone_color(zone)}">{sc:.0f}</div>'
                     f'<div class="hm-cap">{_e(zone)} · Tickertape MMI{" · cached" if mmi["status"] == "stale" else ""}</div></div></div>'
                     f'<div class="hm-gauge"><i style="left:{max(0, min(100, sc)):.1f}%"></i></div>'
                     '<div class="hm-gl"><span>EXT FEAR</span><span>FEAR</span><span>GREED</span><span>EXT GREED</span></div>')
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
                     + (f'<div class="hm-kv"><span>Implied NIFTY move / day (≈VIX÷√252)</span><b>±{v["price"] / math.sqrt(252):.2f}%</b></div>'))
    if B and B["n"]:
        parts.append('<div class="hm-sub">NIFTY 50 internals</div>'
                     f'<div class="hm-kv"><span>Advance / decline</span><b><span class="hm-up">{B["adv"]}</span> / <span class="hm-dn">{B["dec"]}</span>'
                     f' · A/D {(B["adv"] / B["dec"]) if B["dec"] else float(B["adv"]):.2f}</b></div>'
                     f'<div class="hm-kv"><span>Average / median stock move</span><b><span class="{_cls(B["avg"])}">{_pct(B["avg"])}</span> · '
                     f'<span class="{_cls(B["med"])}">{_pct(B["med"])}</span></b></div>')
    u = M.get("CBOE VIX")
    if u:
        parts.append(f'<div class="hm-kv"><span>CBOE VIX (US)</span><b>{u["price"]:.2f} <span class="{_cls(u["chg"])}">{_pct(u["chg"], 1)}</span></b></div>')
    fx = M.get("USD / INR")
    if fx:
        parts.append(f'<div class="hm-kv"><span>USD / INR</span><b>{fx["price"]:.2f} <span class="{_cls(-fx["chg"])}">{_pct(fx["chg"], 2)}</span></b></div>')
    return _panel("MOOD", "SENTIMENT & VOLATILITY", "", "".join(parts))


def _table(rows_spec, M, cols, show_date=True):
    """rows_spec: [(group, label, chain, kind)] -> grouped quote table."""
    head = "".join(f"<th>{c}</th>" for c in cols)
    body, last_g = [], None
    newest = max((M[l]["date"] for _, l, _, _ in rows_spec if l in M), default=None)
    for g, label, _, kind in rows_spec:
        m = M.get(label)
        if not m:
            continue
        if g != last_g:
            body.append(f'<tr class="g"><td colspan="{len(cols)}">{_e(g)}</td></tr>')
            last_g = g
        stale = newest is not None and (newest - m["date"]).days > 4
        yl = kind == "yld"

        def cell(pct, pts):
            if yl:
                return f'<td class="{_cls(pts)}">{pts * 100:+.0f}bp</td>' if _ok(pts) else "<td>—</td>"
            return f'<td class="{_cls(pct)}">{_pct(pct, 1)}</td>'
        tds = [f'<td class="l">{_e(label)}</td>', f'<td class="n">{_px(m["price"], kind)}</td>',
               f'<td class="{_cls(m["pts"])}">{_chg_txt(m, kind)}</td>']
        if not yl:
            tds.append(f'<td class="{_cls(m["chg"])}">{_pct(m["chg"])}</td>')
        else:
            tds.append(f'<td class="{_cls(m["pts"])}">{_arrow(m["pts"])}</td>')
        if "1W" in cols:
            tds.append(cell(m["w1"], m["w1_pts"]))
        tds += [cell(m["m1"], m["m1_pts"]), cell(m["ytd"], m["ytd_pts"]), f'<td>{_spark(m["spark"])}</td>']
        if show_date:
            tds.append(f'<td class="{"st" if stale else ""}" style="color:var(--tx3)">{m["date"]:%d %b}</td>')
        body.append(f'<tr>{"".join(tds)}</tr>')
    if not body:
        return '<div class="hm-empty">Quotes are unavailable right now (Yahoo Finance did not respond). They retry automatically.</div>'
    return f'<div style="overflow-x:auto"><table class="hm-t"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


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
            c = q.get("chg")
            tiles.append(f'<div class="hm-tile" style="background:{_heat(c)}" title="{_e(s)} · ₹{q.get("price", 0):,.2f}">'
                         f'<b>{_e(s)}</b><span>{_pct(c, 1)}</span></div>')
        blocks.append(f'<div class="hm-grp"><small>{_e(g)}</small><div class="hm-tiles">{"".join(tiles)}</div></div>')
    tot = max(B["n"], 1)
    ad = (f'<div class="hm-adl"><span class="hm-up">▲ {B["adv"]} ADVANCING</span><span>{B["flat"]} UNCHANGED · A/D '
          f'{(B["adv"] / B["dec"]) if B["dec"] else float(B["adv"]):.2f} · MEDIAN {_pct(B["med"])}</span><span class="hm-dn">{B["dec"]} DECLINING ▼</span></div>'
          f'<div class="hm-ad"><i style="width:{B["adv"] / tot * 100:.1f}%;background:{GREEN}"></i>'
          f'<i style="width:{B["flat"] / tot * 100:.1f}%;background:#3a3a3a"></i><i style="width:{B["dec"] / tot * 100:.1f}%;background:{RED}"></i></div>')
    leg = '<div class="hm-leg"><span>−3%</span><i></i><span>+3%</span><span style="margin-left:auto">GREY = UNCHANGED / NO PRICE · HOVER A TILE FOR ITS PRICE</span></div>'
    return ad + f'<div class="hm-imap">{"".join(blocks)}</div>' + leg


def _sector_matrix(M):
    rows = []
    for label, _ in SECTORS:
        m = M.get("SEC::" + label)
        if m:
            rows.append((label, m))
    if not rows:
        return '<div class="hm-empty">Sector indices are unavailable right now.</div>'
    rows.sort(key=lambda x: -(x[1]["chg"] if _ok(x[1]["chg"]) else -99))
    body = "".join(
        f'<tr><td class="l">{_e(l)}</td><td class="n">{m["price"]:,.0f}</td>'
        + "".join(f'<td class="c" style="background:{_heat(v, lim)}">{_pct(v, 1)}</td>' for v, lim in
                  ((m["chg"], 2.5), (m["w1"], 5), (m["m1"], 9), (m["ytd"], 25)))
        + f'<td>{_spark(m["spark"], 64)}</td></tr>' for l, m in rows)
    return ('<div style="overflow-x:auto"><table class="hm-t hm-hm"><thead><tr><th>Sector index</th><th>Last</th><th>1D</th><th>1W</th>'
            f'<th>1M</th><th>YTD</th><th>1M trend</th></tr></thead><tbody>{body}</tbody></table></div>')


def _movers(nq):
    ranked = sorted([(s, q) for s, q in nq.items() if q and _ok(q.get("chg"))], key=lambda x: -x[1]["chg"])
    if not ranked:
        return '<div class="hm-empty">NIFTY 50 quotes are unavailable right now.</div>'

    def tbl(items, title):
        rows = "".join(f'<tr><td class="l">{_e(s)}</td><td class="n">₹{q["price"]:,.2f}</td>'
                       f'<td class="{_cls(q["chg"])}">{_arrow(q["chg"])} {abs(q["chg"]):.2f}%</td></tr>' for s, q in items)
        return f'<table class="hm-t"><thead><tr><th>{title}</th><th>LTP</th><th>Chg</th></tr></thead><tbody>{rows}</tbody></table>'
    g = [x for x in ranked[:5] if x[1]["chg"] > 0]
    l = [x for x in ranked[::-1][:5] if x[1]["chg"] < 0]
    return (tbl(g, "Top gainers") if g else '<div class="hm-empty">No gainers.</div>') + \
        (tbl(l, "Top losers") if l else '<div class="hm-empty">No losers.</div>')


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
                   f'<div><small>VALUE</small><b>{_inr(val)}</b><em style="color:var(--tx3)">{len(holds)} holdings</em></div>'
                   f'<div><small>DAY P&amp;L</small><b class="{_cls(day)}">{_inr(day)}</b><em class="{_cls(dp)}">{_pct(dp)}</em></div>'
                   f'<div><small>UNREALISED</small><b class="{_cls(pnl)}">{_inr(pnl)}</b><em class="{_cls(pp)}">{_pct(pp)} on cost</em></div>'
                   f'<div><small>REALISED</small><b class="{_cls(realised) if realised else ""}">{_inr(realised)}</b><em style="color:var(--tx3)">FIFO, all time</em></div></div>')
        rows = "".join(f'<tr><td class="l">{_e(h["symbol"])}</td><td class="n">{"₹" + format(h["ltp"], ",.2f") if _ok(h.get("ltp")) else "—"}</td>'
                       f'<td class="{_cls(h.get("chg"))}">{_pct(h.get("chg"))}</td><td class="{_cls(h.get("pnl_pct"))}">{_pct(h.get("pnl_pct"), 1)}</td>'
                       f'<td>{_pct(h.get("weight"), 1, False)}</td></tr>' for h in holds[:7])
        more = f'<tr><td colspan="5" style="color:var(--tx3);text-align:left">+ {len(holds) - 7} more · open PORT</td></tr>' if len(holds) > 7 else ""
        out.append(f'<table class="hm-t"><thead><tr><th>Holding</th><th>LTP</th><th>Day</th><th>P&amp;L</th><th>Weight</th></tr></thead><tbody>{rows}{more}</tbody></table>')
    else:
        out.append('<div class="hm-empty"><b>No holdings yet.</b> Add buys in PORTFOLIO → TRANSACTIONS to see value, P&amp;L and tax here.</div>')
    if wl:
        rows = []
        for s in wl[:8]:
            q = quotes.get(s) or {}
            rows.append(f'<tr><td class="l">{_e(s)}</td><td class="n">{"₹" + format(q["price"], ",.2f") if _ok(q.get("price")) else "—"}</td>'
                        f'<td class="{_cls(q.get("chg"))}">{_arrow(q.get("chg"))} {_pct(abs(q["chg"]) if _ok(q.get("chg")) else None, 2, False)}</td></tr>')
        more = f'<tr><td colspan="3" style="color:var(--tx3);text-align:left">+ {len(wl) - 8} more · open WL</td></tr>' if len(wl) > 8 else ""
        out.append(f'<table class="hm-t" style="margin-top:6px"><thead><tr><th>{_e(wl_label)}</th><th>LTP</th><th>Day</th></tr></thead>'
                   f'<tbody>{"".join(rows)}{more}</tbody></table>')
    return "".join(out)


def _rotation():
    res = st.session_state.get("mf_sr_res")
    if not res:
        return ('<div class="hm-empty">Open <b>MONEY FLOW</b> (code <b>MF</b>) → Sector Ranking once this session and the live rotation board '
                '— which sectors money is moving into — appears here.</div>')
    try:
        key = str(res.get("asof"))
        memo = st.session_state.get("_hm_rot")
        if memo and memo[0] == key:
            rot = memo[1]
        else:
            import sector_engine as se
            bi = res["bench_index"]
            bench = "MARKET AVG" if "MARKET AVG" in bi.columns else bi.columns[0]
            rot, _ = se.rotation_table(res, bench)
            st.session_state["_hm_rot"] = (key, rot)
    except Exception:
        return '<div class="hm-empty">Rotation data could not be read — reopen MONEY FLOW to refresh it.</div>'
    colors = {"ROTATING IN": GREEN, "LEADING": "#8AD66B", "EXTENDED": YELLOW, "IMPROVING": CYAN, "FADING": AMBER, "WEAK": RED}
    counts = rot["stage"].value_counts().to_dict() if "stage" in rot else {}
    chips = " ".join(f'<span class="hm-pill" style="color:{colors.get(k, T2)}">{_e(k)} {v}</span>' for k, v in counts.items())
    rows = []
    for _, r in rot.head(6).iterrows():
        sc, w = r.get("score"), r.get("r1w")
        rows.append(f'<tr><td class="l">{_e(str(r["sector"])[:22])}</td><td><span class="hm-pill" style="color:{colors.get(r.get("stage"), T2)}">'
                    f'{_e(r.get("stage") or "—")}</span></td><td class="n">{(f"{sc:.0f}" if _ok(sc) else "—")}</td>'
                    f'<td class="{_cls(w)}">{_pct(w, 1)}</td></tr>')
    return (f'<div style="padding:8px 0 6px;line-height:2">{chips}</div><table class="hm-t"><thead><tr><th>Top sectors</th><th>Stage</th>'
            f'<th>Score</th><th>1W</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


WORLD = [("TOKYO", "Asia/Tokyo", (9, 0), (15, 30)), ("HONG KONG", "Asia/Hong_Kong", (9, 30), (16, 0)),
         ("SHANGHAI", "Asia/Shanghai", (9, 30), (15, 0)), ("MUMBAI · NSE", "Asia/Kolkata", (9, 15), (15, 30)),
         ("FRANKFURT", "Europe/Berlin", (9, 0), (17, 30)), ("LONDON", "Europe/London", (8, 0), (16, 30)),
         ("NEW YORK", "America/New_York", (9, 30), (16, 0))]


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
        tag = (f'<span class="hm-pill" style="color:{GREEN}">OPEN</span>' if opened else f'<span class="hm-pill" style="color:{T3}">CLOSED</span>')
        rows.append(f'<div class="hm-clk"><span>{_e(name)}</span><b>{lt:%a %H:%M}</b>{tag}</div>')
    return "".join(rows)


# ═════════════════════════ page ═════════════════════════
def _body(supabase):
    M = market_data()
    groups = _nifty50()
    n50 = [s for v in groups.values() for s in v]
    wl = list(dict.fromkeys(st.session_state.get("watchlist", []) or []))
    wl_label = "Your watchlist"
    if not wl:
        wl, wl_label = list(dict.fromkeys(st.session_state.get("admin_watchlist", []) or [])), "Arka watchlist"
    base = _book_base(supabase)
    held = sorted({l["symbol"] for l in base.get("alots", []) if l.get("open", 0) > 1e-9} if base.get("engine")
                  else {l["symbol"] for l in base.get("lots", [])})
    quotes = get_quotes(tuple(dict.fromkeys(n50 + wl[:8] + held))) or {}
    nq = {s: quotes.get(s) for s in n50}
    B = _breadth(nq)
    S = nse_session()
    try:
        mmi = get_mmi()
    except Exception:
        mmi = {"status": "unavailable"}

    # header strip
    n = M.get("NIFTY 50")
    tag_cls = {"live": "live", "pre": "pre"}.get(S["cls"], "")
    stamp = datetime.now(IST).strftime("%a %d %b %Y · %H:%M IST")
    asof = f'NIFTY {n["date"]:%d %b}' if n else "NO DATA"
    st.markdown(f'<div class="hm-head"><div class="t"><em>HOME</em>MARKET MONITOR<small>India first · global context · your book</small></div>'
                f'<div class="m"><span class="hm-tag {tag_cls}"><i></i>{_e(S["label"])}</span><span class="hm-tag">{stamp}</span>'
                f'<span class="hm-tag">LAST CLOSE · {asof}</span><span class="hm-tag">YAHOO · DELAYED</span></div></div>', unsafe_allow_html=True)
    st.markdown(_tape(M), unsafe_allow_html=True)

    # row 1 — NIFTY hero · regime · session · mood
    st.markdown(f'<div class="hm-row hm-r1">{_hero(M)}{_regime(M, B)}{_session_block(S)}{_mood(M, mmi, B)}</div>', unsafe_allow_html=True)

    # row 2 — world equity indices · cross-asset
    wei = _table(WEI, M, ["Index", "Last", "Chg", "1D", "1W", "1M", "YTD", "1M trend", "Date"])
    xa = _table(XASSET, M, ["Instrument", "Last", "Chg", "1D", "1M", "YTD", "1M trend", "Date"])
    st.markdown(f'<div class="hm-row hm-r2">{_panel("WEI", "WORLD EQUITY INDICES", "LOCAL CURRENCY · CLOSE-TO-CLOSE", wei, "hm-pb np")}'
                f'{_panel("XA", "CROSS-ASSET MONITOR", "COMMODITIES · FX · RATES · VOL · CRYPTO", xa, "hm-pb np")}</div>', unsafe_allow_html=True)

    # row 3 — NIFTY 50 heatmap · sectors
    imap_note = f"{B['n']} PRICED · COLOUR = 1-DAY % (±3%)"
    st.markdown(f'<div class="hm-row hm-r3">{_panel("IMAP", "NIFTY 50 HEATMAP · BY SECTOR", imap_note, _imap(groups, nq, B), "hm-pb np")}'
                f'{_panel("SECT", "NSE SECTOR PERFORMANCE", "SORTED BY 1-DAY MOVE", _sector_matrix(M), "hm-pb np")}</div>', unsafe_allow_html=True)

    # row 4 — movers · your book · rotation + world clock
    holds, realised = _book_holdings(base, quotes)
    rot_clock = _rotation() + '<div class="hm-sub" style="padding:12px 0 4px">World markets · local time</div>' + _world_clock()
    st.markdown(f'<div class="hm-row hm-r4">{_panel("MOV", "NIFTY 50 MOVERS", "1-DAY", _movers(nq), "hm-pb np")}'
                f'{_panel("BOOK", "YOUR BOOK", "SPLIT-ADJUSTED · FIFO", _book(holds, realised, wl, wl_label, quotes), "hm-pb np")}'
                f'{_panel("ROT", "SECTOR ROTATION · MONEY FLOW", "LIVE WHEN MF IS LOADED", rot_clock)}</div>', unsafe_allow_html=True)


def _body_live(supabase):
    _body(supabase)


def _body_closed(supabase):
    _body(supabase)


_frag_live = st.fragment(run_every=60)(_body_live) if hasattr(st, "fragment") else _body_live
_frag_closed = st.fragment(_body_closed) if hasattr(st, "fragment") else _body_closed


def render_home(supabase=None, on_open=None, nav=None):
    """HOME page. `nav(target)` switches workspace (used by the function launcher buttons)."""
    st.markdown(_CSS, unsafe_allow_html=True)
    S = nse_session()
    with st.spinner("Loading markets…"):
        (_frag_live if S["cls"] in ("live", "pre") else _frag_closed)(supabase)

    st.markdown('<div class="hm-fhead"><b>FUNCTIONS</b><span>type the code in the command line above, or click</span><i></i></div>', unsafe_allow_html=True)
    cols = st.columns(len(FUNCS), gap="small")
    for col, (code, title, target, desc) in zip(cols, FUNCS):
        with col:
            with st.container(key=f"hm_fn_{target}"):
                st.markdown(f'<div class="hm-fn"><span class="c">{code}</span><div class="t">{title}</div><div class="d">{desc}</div></div>',
                            unsafe_allow_html=True)
                if nav is not None:
                    st.button("GO  →", key=f"hm_go_{target}", use_container_width=True, on_click=nav, args=(target,))
    st.markdown('<div class="hm-foot"><span>Prices: Yahoo Finance, delayed; changes are close-to-close. Regime, mood and heatmap are '
                'information, not advice.</span><span>NIFTY 50 list as of 30 Sep 2026 · NSE holidays 2026 · Market Mood Index © Tickertape</span></div>',
                unsafe_allow_html=True)
