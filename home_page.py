"""
home_page.py — Arka Trades · Market overview (the HOME page), v4.1

v4.1: a "Futures and options" section under the benchmarks: the F&O engine verdict for NIFTY 50 and BANK NIFTY with put-call
      ratio, implied volatility, max pain and the open-interest walls (from fno_page.py; hidden if that file is not installed).

v4 — rebuilt top to bottom so the most important thing is always highest on the page
  Reading order (desktop and phone):
    1  Status      thin moving ticker · date · one-sentence brief · session chip with a countdown to the open or close
    2  Your money  one line: portfolio value, today's profit, open profit, stops (hit / near), best and worst holding
    3  The market  NIFTY 50 (6-month chart with 20- and 50-day averages, price scale, key levels) beside Market health
                   (verdict from six checks, advancing / declining, India VIX, market mood)
    4  Benchmarks  SENSEX, BANK NIFTY, FIN NIFTY, MIDCAP 100, NEXT 50, NIFTY 500 as tiles with 1W and 1M change
    5  Your book   holdings table (today, today in rupees, overall, weight, stop tags) and your watchlist
    6  Leadership  sectors ranked by today with 1D / 1W / 1M / YTD heat, top movers, sector rotation, NIFTY 50 heatmap
    7  World       global indices by region · commodities, currencies, rates, volatility, crypto
    8  Calendar    NSE session and expiries · market holidays · world market hours
  New on the page
    * Key levels for NIFTY: open and gap, previous close, pivot / resistance 1 / support 1, 20 / 50 / 200-day averages
      with the distance to each, RSI and the expected daily move implied by India VIX.
    * Stop tags on holdings (Stop hit, Near stop, At target) from the stops and targets saved in Portfolio.
    * Sector rotation always has something to show: Money flow's ranking when that page was opened this session,
      otherwise sectors against NIFTY 50 over one month.
    * Before the bell on a trading day the brief adds how the US closed and how Asia is trading.
  Changed
    * The index cards strip is now a thin one-line ticker (same 22 instruments). render_tape() draws the same ticker.
    * One HTML block as before, so the 60-second refresh still swaps numbers in place without a flash.
  Data layer: unchanged except that the quote download also keeps the day's open and the previous day's high and low
  (same request, nothing extra is downloaded).

Earlier versions: v3.x added the brief header, the skeleton loader, serialized Yahoo downloads and the moving index
strip; v2 was the app-style redesign (Inter, sentence case, soft cards, separate phone layout).

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
# One visual system for the whole page: Inter with tabular numbers, sentence case, 12 px cards with a hairline header.
# Layout follows the width the HOME column really has (container queries), so it adapts when the news column opens:
#   wide  >= 900 px  : 12-column grid          mid 620-899 px : two columns          narrow / phone : one column
_CSS = """<style>
.hm{--hm-r:12px;font-family:var(--sans,'Inter',system-ui,sans-serif);font-variant-numeric:tabular-nums;container-type:inline-size;width:100%;color:var(--tx);}
.hm *{box-sizing:border-box;}
.hm-up{color:var(--green) !important;} .hm-dn{color:var(--red) !important;} .hm-fl{color:var(--tx3) !important;}

/* ── ticker: one thin line; the items twice in one track that slides left by exactly one copy ── */
.hm-tape{position:relative;overflow:hidden;height:36px;margin-top:8px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);
  -webkit-mask-image:linear-gradient(90deg,transparent,#000 28px,#000 calc(100% - 28px),transparent);
          mask-image:linear-gradient(90deg,transparent,#000 28px,#000 calc(100% - 28px),transparent);}
.hm-tape .trk{display:flex;align-items:center;height:100%;width:max-content;animation:hmtk var(--dur,90s) linear infinite;will-change:transform;}
.hm-tape:hover .trk,.hm-tape:active .trk{animation-play-state:paused;}
@keyframes hmtk{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.hm-tk{display:inline-flex;align-items:baseline;gap:8px;padding:0 18px;white-space:nowrap;font:500 12.5px var(--sans);position:relative;}
.hm-tk::after{content:"";position:absolute;right:0;top:50%;width:1px;height:12px;margin-top:-6px;background:var(--line2,#2E2E33);}
.hm-tk b{font-weight:500;color:var(--tx3);} .hm-tk i{font-style:normal;font-weight:600;color:var(--tx);} .hm-tk em{font-style:normal;font-weight:500;}
@media (prefers-reduced-motion:reduce){.hm-tape{overflow-x:auto;-webkit-mask-image:none;mask-image:none;} .hm-tape .trk{animation:none;} .hm-tape .dup{display:none;}}

/* ── header ── */
.hm-hd{display:flex;justify-content:space-between;align-items:flex-end;gap:12px 24px;flex-wrap:wrap;padding:16px 0 0;}
.hm-hd .o{font:500 12.5px var(--sans);color:var(--tx3);}
.hm-hd .t{font:650 24px/1.2 var(--sans);letter-spacing:-.5px;color:var(--tx);margin-top:3px;}
.hm-brief{font:13.5px/1.6 var(--sans);color:var(--tx2);margin-top:8px;}
.hm-brief b{font-weight:600;color:var(--tx);}
.hm-chips{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;}
.hm-chip{display:inline-flex;align-items:center;gap:7px;padding:6px 11px;border-radius:8px;background:var(--s1);border:1px solid var(--line);font:500 12.5px var(--sans);color:var(--tx2);white-space:nowrap;}
.hm-chip b{font-weight:600;color:var(--tx);margin-right:-2px;}
.hm-chip i{width:7px;height:7px;border-radius:50%;background:var(--tx4,#52525B);display:inline-block;}
.hm-chip.live i{background:var(--green);box-shadow:0 0 0 3px rgba(48,209,88,.18);} .hm-chip.pre i{background:var(--amber);}
.hm-chip.k{--c:var(--tx3);color:var(--c);background:color-mix(in srgb,var(--c) 10%,transparent);border-color:color-mix(in srgb,var(--c) 22%,transparent);}
.hm-chip.k i{background:var(--c);}
.hm-day{position:relative;height:3px;border-radius:2px;background:var(--s3);margin-top:12px;overflow:hidden;}
.hm-day i{position:absolute;left:0;top:0;bottom:0;background:var(--green);border-radius:2px;}

/* ── your book, one line ── */
.hm-bk{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--hm-r);overflow:hidden;margin-top:12px;}
@container (min-width:620px){.hm-bk{grid-template-columns:repeat(3,minmax(0,1fr));}}
@container (min-width:900px){.hm-bk{grid-template-columns:repeat(6,minmax(0,1fr));}}
.hm-bk>div{background:var(--s1);padding:10px 14px 11px;min-width:0;}
.hm-bk small{display:block;font:12px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-bk b{display:block;font:600 17px var(--sans);letter-spacing:-.2px;color:var(--tx);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-bk em{display:block;font:500 12px var(--sans);font-style:normal;color:var(--tx3);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-bk>div[data-ak-sym]{cursor:pointer;transition:background .15s;} .hm-bk>div[data-ak-sym]:hover{background:#17171a;}

/* ── grid ── */
.hm-g{display:grid;gap:12px;margin-top:12px;grid-template-columns:minmax(0,1fr);align-items:stretch;}
@container (min-width:620px){.hm-g{grid-template-columns:repeat(2,minmax(0,1fr));} .hm-g>.m2{grid-column:1/-1;}}
@container (min-width:900px){
  .hm-g{grid-template-columns:repeat(12,minmax(0,1fr));}
  .hm-g>.c4{grid-column:span 4;} .hm-g>.c5{grid-column:span 5;} .hm-g>.c6{grid-column:span 6;}
  .hm-g>.c7{grid-column:span 7;} .hm-g>.c8{grid-column:span 8;} .hm-g>.c12{grid-column:1/-1;}
}
.hm-stack{display:flex;flex-direction:column;gap:12px;min-width:0;} .hm-stack>.hm-p:last-child{flex:1;}
.hm-sec{display:flex;align-items:center;gap:10px;margin:28px 0 -2px;flex-wrap:wrap;}
.hm-sec::before{content:"";width:3px;height:15px;border-radius:2px;background:var(--amber);}
.hm-sec b{font:600 15.5px var(--sans);letter-spacing:-.2px;color:var(--tx);}
.hm-sec span{font:12.5px var(--sans);color:var(--tx3);}

/* ── panels ── */
.hm-p{background:var(--s1);border:1px solid var(--line);border-radius:var(--hm-r);min-width:0;display:flex;flex-direction:column;overflow:hidden;transition:border-color .18s ease;}
.hm-p:hover{border-color:#2a2a2f;}
.hm-pt{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 14px;min-height:42px;border-bottom:1px solid var(--line);}
.hm-pt b{font:600 13.5px var(--sans);color:var(--tx);white-space:nowrap;}
.hm-pt span{font:12px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;text-align:right;}
.hm-pb{padding:14px;flex:1;min-width:0;} .hm-pb.np{padding:0;}
.hm-sub{font:600 12.5px var(--sans);color:var(--tx2);padding:14px 0 4px;}
.hm-empty{font:13px var(--sans);color:var(--tx3);line-height:1.6;padding:12px 14px;} .hm-pb:not(.np)>.hm-empty{padding:0;}
.hm-empty b{color:var(--tx2);font-weight:600;}
.hm-note{font:12px/1.55 var(--sans);color:var(--tx3);margin-top:10px;}
.hm-pill{display:inline-block;padding:3px 8px;border-radius:6px;font:500 12px var(--sans);background:var(--s2);white-space:nowrap;}
.hm-tag{display:inline-block;margin-left:8px;padding:1px 6px;border-radius:5px;font:600 11px var(--sans);vertical-align:1px;white-space:nowrap;
  color:var(--c,var(--tx3));background:color-mix(in srgb,var(--c,#71717A) 14%,transparent);}

/* ── NIFTY 50 ── */
.hm-hero{position:relative;}
.hm-hero::before{content:"";position:absolute;inset:42px 0 auto 0;height:150px;pointer-events:none;
  background:radial-gradient(55% 110% at 10% 0%,color-mix(in srgb,var(--hg,#30D158) 13%,transparent),transparent 70%);}
.hm-hero>*{position:relative;}
.hm-htop{display:flex;justify-content:space-between;align-items:flex-start;gap:10px 14px;flex-wrap:wrap;}
.hm-hv{font:650 32px/1.1 var(--sans);letter-spacing:-.8px;color:var(--tx);}
.hm-hc{font:500 13.5px var(--sans);margin-top:5px;}
.hm-per{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;}
.hm-per span{font:500 12px var(--sans);padding:4px 9px;border-radius:7px;background:var(--s2);color:var(--tx3);white-space:nowrap;}
.hm-per span b{font-weight:600;margin-left:5px;}
.hm-ch{position:relative;padding-right:58px;margin-top:12px;}
.hm-ch .plot{position:relative;height:var(--h,160px);}
.hm-ch svg{display:block;width:100%;height:100%;overflow:visible;}
.hm-ch .dot{position:absolute;right:-4px;width:8px;height:8px;border-radius:50%;margin-top:-4px;}
.hm-ch .ax{position:absolute;right:0;top:0;width:52px;height:var(--h,160px);}
.hm-ch .ax span{position:absolute;right:0;transform:translateY(-50%);font:11.5px var(--sans);color:var(--tx3);}
.hm-ch .ax b{position:absolute;right:0;transform:translateY(-50%);font:600 11px var(--sans);color:#0B0B0C;padding:2px 5px;border-radius:4px;}
.hm-ch .xs{position:relative;height:16px;margin-top:5px;}
.hm-ch .xs span{position:absolute;font:11.5px var(--sans);color:var(--tx3);white-space:nowrap;}
.hm-lgd{display:flex;gap:6px 14px;flex-wrap:wrap;font:12px var(--sans);color:var(--tx3);margin-top:8px;}
.hm-lgd .nt{margin-left:auto;cursor:help;}
.hm-lgd i{display:inline-block;width:12px;height:2px;border-radius:1px;vertical-align:middle;margin-right:6px;}
.hm-st{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 0;margin-top:14px;padding-top:14px;border-top:1px solid var(--line);}
@container (min-width:620px){.hm-st{grid-template-columns:repeat(6,minmax(0,1fr));}}
.hm-st div{padding:0 8px 0 10px;border-left:1px solid var(--line);min-width:0;}
.hm-st small{display:block;font:12px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-st b{display:block;font:600 13.5px var(--sans);color:var(--tx);margin-top:3px;white-space:nowrap;}
.hm-st em{display:block;font:500 11.5px var(--sans);font-style:normal;color:var(--tx3);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}

/* ── market health ── */
.hm-vd{display:flex;align-items:baseline;gap:6px 10px;flex-wrap:wrap;}
.hm-vd b{font:650 24px/1.15 var(--sans);letter-spacing:-.4px;} .hm-vd span{font:12.5px var(--sans);color:var(--tx3);}
.hm-meter{display:flex;gap:4px;margin-top:10px;} .hm-meter i{flex:1;height:5px;background:var(--s3);border-radius:3px;}
.hm-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:14px;}
.hm-gz{background:var(--s2);border-radius:10px;padding:9px 11px 11px;min-width:0;}
.hm-gz small{display:block;font:12px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-gz b{display:block;font:600 17px var(--sans);letter-spacing:-.2px;color:var(--tx);margin-top:3px;white-space:nowrap;}
.hm-gz em{display:block;font:500 11.5px var(--sans);font-style:normal;color:var(--tx3);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-gz .ad{display:flex;height:4px;gap:2px;border-radius:2px;overflow:hidden;margin-top:9px;background:var(--s3);} .hm-gz .ad i{display:block;height:100%;}
.hm-gz .gg{position:relative;height:4px;border-radius:2px;margin-top:9px;background:linear-gradient(90deg,#FF453A,#FF9F0A 40%,#B5D334 60%,#30D158);}
.hm-gz .gg i{position:absolute;top:-3px;width:10px;height:10px;background:#fff;border:2px solid var(--s2);border-radius:50%;transform:translateX(-5px);}
.hm-gz .lv{position:relative;height:4px;border-radius:2px;margin-top:9px;background:var(--s3);overflow:hidden;} .hm-gz .lv i{position:absolute;left:0;top:0;bottom:0;border-radius:2px;}
.hm-cks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px;margin-top:12px;}
.hm-ck{display:flex;align-items:center;gap:8px;font:12.5px var(--sans);color:var(--tx2);padding:7px 0;border-top:1px solid var(--line);min-width:0;}
.hm-ck i{flex:0 0 16px;width:16px;height:16px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font:700 9.5px var(--sans);font-style:normal;}
.hm-ck i.y{background:rgba(48,209,88,.16);color:var(--green);} .hm-ck i.n{background:rgba(255,69,58,.16);color:var(--red);}
.hm-ck span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
@container (max-width:619.98px){.hm-cks{grid-template-columns:minmax(0,1fr);}}
.hm-kv{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:7px 0;border-top:1px solid var(--line);font:12.5px var(--sans);color:var(--tx3);line-height:1.45;}
.hm-kv b{font:500 12.5px var(--sans);color:var(--tx);white-space:nowrap;}
.hm-kvs{margin-top:0;} .hm-kvs.first .hm-kv:first-child{border-top:0;padding-top:0;}

/* ── benchmark tiles ── */
.hm-ixs{display:grid;gap:10px;margin-top:12px;grid-template-columns:repeat(2,minmax(0,1fr));}
@container (min-width:620px){.hm-ixs{grid-template-columns:repeat(3,minmax(0,1fr));}}
@container (min-width:900px){.hm-ixs{grid-template-columns:repeat(6,minmax(0,1fr));}}
.hm-ix{background:var(--s1);border:1px solid var(--line);border-radius:var(--hm-r);padding:10px 12px 9px;min-width:0;transition:border-color .18s ease;}
.hm-ix:hover{border-color:#2a2a2f;}
.hm-ix .n{font:500 12px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-ix .r{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:4px;}
.hm-ix .v{font:600 15.5px var(--sans);letter-spacing:-.2px;color:var(--tx);white-space:nowrap;}
.hm-ix .c{font:500 12px var(--sans);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-ix .w{display:flex;gap:10px;font:500 11.5px var(--sans);color:var(--tx3);margin-top:7px;padding-top:7px;border-top:1px solid var(--line);white-space:nowrap;overflow:hidden;}
.hm-ix .w b{font-weight:500;margin-left:4px;}
.hm-ix svg{flex:0 0 auto;display:block;}

/* ── tables ── */
.hm-t{width:100%;border-collapse:collapse;font:13px var(--sans);}
.hm-t th{font:500 12px var(--sans);color:var(--tx3);text-align:right;padding:8px 12px;border-bottom:1px solid var(--line);white-space:nowrap;}
.hm-t th:first-child,.hm-t td:first-child{text-align:left;padding-left:14px;} .hm-t th:last-child,.hm-t td:last-child{padding-right:14px;}
.hm-t td{padding:7px 12px;border-bottom:1px solid var(--line);text-align:right;color:var(--tx2);white-space:nowrap;}
.hm-t tr:last-child td{border-bottom:0;}
.hm-t tbody tr:not(.g) td{height:35px;}
.hm-t td.n,.hm-t td.l{color:var(--tx);font-weight:500;}
.hm-t tr.st td{opacity:.5;}
.hm-t tr.g td{font:600 12px var(--sans);color:var(--tx3);padding:12px 14px 5px;border-bottom:0;}
.hm-t tbody tr:not(.g) td{transition:background .15s ease;} .hm-t tbody tr:not(.g):hover td{background:rgba(255,255,255,.025);}
.hm-t tr[data-ak-sym]{cursor:pointer;}
.hm-t svg{vertical-align:middle;}
.hm-hm td{padding-top:8px;padding-bottom:8px;}
.hm-hm td.c{font-weight:600;color:#fff !important;text-align:center !important;min-width:58px;border-left:2px solid var(--s1);}
.hm-hm td.rk{color:var(--tx3);font-weight:400;width:1%;padding-right:0;}
.hm-wt{display:inline-block;width:46px;height:4px;border-radius:2px;background:var(--s3);vertical-align:middle;margin-right:8px;overflow:hidden;}
.hm-wt i{display:block;height:100%;background:var(--tx3);border-radius:2px;}

/* ── list rows ── */
.hm-li{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 14px;border-bottom:1px solid var(--line);transition:background .15s ease;}
.hm-li:last-child{border-bottom:0;} .hm-li:hover{background:rgba(255,255,255,.025);}
.hm-li .a{min-width:0;} .hm-li .a b{display:block;font:500 13.5px var(--sans);color:var(--tx);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-li .a small{display:block;font:12px var(--sans);color:var(--tx3);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hm-li .z{text-align:right;flex-shrink:0;} .hm-li .z b{display:block;font:500 13.5px var(--sans);color:var(--tx);}
.hm-li .z small{display:block;font:500 12.5px var(--sans);margin-top:2px;}
.hm-li[data-ak-sym]{cursor:pointer;}
.hm-lg{font:600 12px var(--sans);color:var(--tx3);padding:12px 14px 5px;}
.hm-pct{display:inline-block;min-width:64px;padding:4px 8px;border-radius:6px;font:600 12.5px var(--sans);text-align:center;}
.hm-mv{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);}
.hm-mv>div+div{border-left:1px solid var(--line);}
.hm-mv .hm-li{padding:8px 14px;}

/* ── NIFTY 50 heatmap ── */
.hm-imap{display:flex;flex-wrap:wrap;gap:12px 16px;padding:14px 14px 8px;}
.hm-grp{display:flex;flex-direction:column;gap:6px;min-width:0;}
.hm-grp>small{font:500 12px var(--sans);color:var(--tx3);white-space:nowrap;}
.hm-tiles{display:flex;flex-wrap:wrap;gap:3px;max-width:520px;}
.hm-tile{width:84px;height:50px;display:flex;flex-direction:column;justify-content:center;align-items:center;border-radius:6px;color:#fff;overflow:hidden;}
.hm-tile b{font:600 11.5px var(--sans);max-width:78px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hm-tile span{font:500 11.5px var(--sans);opacity:.92;margin-top:2px;}
.hm-tile[data-ak-sym]{cursor:pointer;transition:filter .15s;} .hm-tile:hover{filter:brightness(1.18);}
.hm-tile.nq{opacity:.38;}
@container (max-width:619.98px){.hm-grp{width:100%;} .hm-tiles{max-width:none;} .hm-tile{width:calc((100% - 9px)/4);}}
.hm-stiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:3px;padding:12px 14px 14px;}
.hm-stile{height:52px;display:flex;flex-direction:column;justify-content:center;align-items:center;border-radius:6px;color:#fff;overflow:hidden;padding:0 4px;}
.hm-stile b{font:600 11.5px var(--sans);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hm-stile span{font:500 11.5px var(--sans);opacity:.92;margin-top:2px;}
.hm-leg{display:flex;align-items:center;gap:10px;padding:2px 14px 12px;font:12px var(--sans);color:var(--tx3);}
.hm-leg i{width:140px;height:5px;border-radius:3px;background:linear-gradient(90deg,rgba(255,69,58,.8),#2a2a2e 50%,rgba(48,209,88,.8));}

/* ── calendar ── */
.hm-big{font:650 22px/1.15 var(--sans);letter-spacing:-.4px;}
.hm-cap{font:12.5px var(--sans);color:var(--tx3);margin-top:4px;line-height:1.55;}
.hm-prog{position:relative;height:5px;background:var(--s3);margin:12px 0 6px;border-radius:3px;overflow:hidden;}
.hm-prog i{position:absolute;left:0;top:0;bottom:0;background:var(--green);border-radius:3px;}
.hm-ticks{display:flex;justify-content:space-between;font:12px var(--sans);color:var(--tx3);margin-bottom:8px;}
.hm-clk{display:grid;grid-template-columns:1fr auto auto;gap:6px 14px;align-items:center;font:12.5px var(--sans);padding:7px 0;border-top:1px solid var(--line);}
.hm-clk:first-child{border-top:0;padding-top:0;}
.hm-clk span{color:var(--tx2);} .hm-clk b{color:var(--tx);font-weight:500;}
.hm-foot{font:12px var(--sans);color:var(--tx3);padding:20px 0 0;line-height:1.6;}

/* ── phone ── */
@container (max-width:619.98px){
  .hm-hd{padding-top:14px;} .hm-hd .t{font-size:22px;} .hm-chips{justify-content:flex-start;}
  .hm-hv{font-size:30px;} .hm-ch{padding-right:50px;} .hm-ch .ax{width:46px;}
  .hm-sec{margin-top:24px;} .hm-lgd .nt{margin-left:0;} .hm-leg span:last-child{display:none;}
  .hm-gz{padding:9px 9px 11px;}
}

/* ── first-visit skeleton ── */
.hm-load{display:flex;align-items:center;gap:14px;padding:20px 0 14px;}
.hm-load img{width:40px;height:40px;border-radius:11px;animation:hmbreath 1.6s ease-in-out infinite;}
@keyframes hmbreath{0%,100%{transform:scale(1);box-shadow:0 0 0 0 rgba(247,148,29,.45)}50%{transform:scale(1.05);box-shadow:0 0 0 10px rgba(247,148,29,0)}}
.hm-load b{display:block;font:600 16px var(--sans);color:var(--tx);} .hm-load span{display:block;font:13px var(--sans);color:var(--tx3);margin-top:2px;}
.hm-load .bar{flex:1;max-width:220px;height:3px;border-radius:3px;background:#1c1c20;overflow:hidden;position:relative;margin-left:auto;}
.hm-load .bar i{position:absolute;left:-40%;top:0;bottom:0;width:40%;border-radius:3px;background:var(--amber,#FF9F0A);animation:hmbar 1.1s ease-in-out infinite;}
@keyframes hmbar{to{left:100%}}
.sk{border-radius:12px;background:linear-gradient(100deg,#101013 30%,#1a1a1f 50%,#101013 70%);background-size:220% 100%;animation:hmsk 1.25s linear infinite;border:1px solid var(--line,#1E1E21);}
@keyframes hmsk{from{background-position:120% 0}to{background-position:-100% 0}}
.sk-bar .sk{height:64px;} .sk-bar{margin-bottom:12px;}
.sk-a{display:grid;gap:12px;grid-template-columns:minmax(0,7fr) minmax(0,5fr);} .sk-a .sk{height:420px;}
.sk-b{display:grid;gap:10px;grid-template-columns:repeat(6,minmax(0,1fr));margin-top:12px;} .sk-b .sk{height:92px;}
@container (max-width:899.98px){.sk-a{grid-template-columns:minmax(0,1fr);} .sk-a .sk{height:300px;} .sk-a .sk+.sk{height:220px;} .sk-b{grid-template-columns:repeat(2,minmax(0,1fr));}}
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
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none"><polyline points="{pts}" fill="none" stroke="{color}" '
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
        def num(row, col):
            v = row.get(col) if col in d else None
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) and v > 0 else None
        out[t] = {"price": float(last["Close"]), "prev": float(prev["Close"]), "date": idx[-1].normalize(),
                  "high": num(last, "High"), "low": num(last, "Low"), "open": num(last, "Open"),
                  "pdh": num(prev, "High"), "pdl": num(prev, "Low")}
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
         "high": L.get("high"), "low": L.get("low"), "open": L.get("open"), "pdh": L.get("pdh"), "pdl": L.get("pdl"), "w1": None, "m1": None, "ytd": None, "spark": [], "series": None,
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
            holds = pp.aggregate(base["alots"], quotes, base["infos"], base["plans"], getattr(pp, "ALL_BOOKS", "All books"), {}, {})
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
CARDS = ["NIFTY 50", "SENSEX", "BANK NIFTY", "FIN NIFTY", "NIFTY MIDCAP 100", "NIFTY NEXT 50", "NIFTY 500", "INDIA VIX",
         "USD / INR", "GOLD", "SILVER", "BRENT CRUDE", "NATURAL GAS", "S&P 500", "NASDAQ COMP", "DOW JONES", "NIKKEI 225",
         "HANG SENG", "DAX", "FTSE 100", "US 10Y", "BITCOIN"]
BENCH = ["SENSEX", "BANK NIFTY", "FIN NIFTY", "NIFTY MIDCAP 100", "NIFTY NEXT 50", "NIFTY 500"]
MA20_C, MA50_C = "#FFB340", "#5AC8FA"


def _sinr(x):
    """Signed rupees: +₹4,200 / -₹2,400."""
    if not _ok(x):
        return "—"
    return ("+" if x >= 0.5 else "") + _inr(x)


def _ticker(M, labels=CARDS):
    """The moving ticker: name, last price and 1-day change for 22 instruments on one thin line."""
    out = []
    for label in labels:
        m = M.get(label)
        if not m:
            continue
        kind = m.get("kind", "idx")
        if kind == "yld":
            ch, ref = f'{m["pts"] * 100:+.1f} bp', m["pts"]
        else:
            ch, ref = _pct(m["chg"]), m["chg"]
        out.append(f'<span class="hm-tk"><b>{_e(_nm(label))}</b><i>{_px(m["price"], kind)}</i>'
                   f'<em class="{_cls(ref)}">{_arrow(ref)} {ch}</em></span>')
    if not out:
        return ""
    one = "".join(out)
    dup = one.replace('<span class="hm-tk">', '<span class="hm-tk dup" aria-hidden="true">')
    return f'<div class="hm-tape" style="--dur:{max(40, len(out) * 5):.0f}s"><div class="trk">{one}{dup}</div></div>'


_cards = _ticker            # earlier names, kept so nothing that imports them breaks


def _tape(M):
    return _ticker(M)


def render_tape():
    """The ticker alone (used above the stock screen)."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="hm">{_ticker(market_data())}</div>', unsafe_allow_html=True)


def _card(title, right, body, span="", flush=False, cls=""):
    """A panel: hairline header (title left, context right) and a body. `span` is its width on the 12-column grid."""
    tip = _e(re.sub(r"<[^>]+>", "", right))
    pb = "hm-pb np" if flush else "hm-pb"
    return (f'<div class="hm-p {span} {cls}"><div class="hm-pt"><b>{title}</b><span title="{tip}">{right}</span></div>'
            f'<div class="{pb}">{body}</div></div>')


def _grid(inner):
    return f'<div class="hm-g">{inner}</div>'


def _sec(title, sub=""):
    return f'<div class="hm-sec"><b>{_e(title)}</b><span>{_e(sub)}</span></div>'


# ───────────── header ─────────────
def _session_chip(S, now=None):
    """-> (css class, label, detail) for the session chip, with a countdown to the next open or close."""
    now = now or datetime.now(IST)
    d, t = now.date(), now.hour * 60 + now.minute
    if S["cls"] == "live":
        left = max(0, CLOSE - t)
        return "live", "Market open", (f"closes in {left // 60}h {left % 60:02d}m" if left >= 60 else f"closes in {left} min")
    if S["cls"] == "pre":
        return "pre", "Pre-open", f"opens 09:15, in {max(0, OPEN - t)} min"
    if is_trading_day(d) and t < PRE_OPEN:
        k = OPEN - t
        return "", "Market closed", f"opens 09:15, in {k // 60}h {k % 60:02d}m"
    nxt = next_trading_day(d)
    lab = "Market holiday" if d in NSE_HOLIDAYS else "Market closed"
    when = "tomorrow" if (nxt - d).days == 1 else f"{nxt:%a %d %b}"
    return "", lab, f"opens {when}, 09:15"


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


def _cues(M, S, now):
    """Before the bell on a trading day: how the US closed and how Asia is trading."""
    d, t = now.date(), now.hour * 60 + now.minute
    if S["cls"] == "live" or not is_trading_day(d) or t >= OPEN:
        return ""
    parts = []
    for label, name in (("S&P 500", "S&amp;P 500"), ("NASDAQ COMP", "Nasdaq"), ("NIKKEI 225", "Nikkei"), ("HANG SENG", "Hang Seng")):
        m = M.get(label)
        try:
            fresh = m is not None and (d - pd.Timestamp(m["date"]).date()).days <= 4
        except Exception:
            fresh = False
        if fresh and _ok(m.get("chg")):
            parts.append(f'{name} <b class="{_cls(m["chg"])}">{_pct(m["chg"], 1)}</b>')
    return ("Global cues: " + ", ".join(parts) + ".") if parts else ""


def _head(M, B, S, now=None):
    now = now or datetime.now(IST)
    n = M.get("NIFTY 50")
    bits = []
    if n and _ok(n.get("chg")):
        word = "up" if n["chg"] > 0.05 else "down" if n["chg"] < -0.05 else "flat"
        amt = "" if word == "flat" else " " + _pct(abs(n["chg"]), 2, False)
        bits.append(f'NIFTY 50 is <b class="{_cls(n["chg"])}">{word}{amt}</b> at <b>{n["price"]:,.0f}</b>.')
    if B and B.get("n"):
        bits.append(f'<b>{B["adv"]}</b> of {B["n"]} NIFTY stocks are up' + ("." if B["n"] >= 45 else " (quotes for the rest have not arrived yet)."))
    secs = [(l, M["SEC::" + l]["chg"]) for l, _ in SECTORS if M.get("SEC::" + l) and _ok(M["SEC::" + l].get("chg"))]
    if len(secs) >= 4:
        secs.sort(key=lambda x: x[1])
        (wl_, wv), (bl_, bv) = secs[0], secs[-1]
        bits.append(f'{_e(_nm(bl_))} leads (<b class="{_cls(bv)}">{_pct(bv, 1)}</b>), {_e(_nm(wl_))} lags (<b class="{_cls(wv)}">{_pct(wv, 1)}</b>).')
    v = M.get("INDIA VIX")
    if v:
        bits.append(f'India VIX <b>{v["price"]:.1f}</b>, {_vix_word(v["price"])}.')
    cue = _cues(M, S, now)
    if cue:
        bits.append(cue)
    if not n:
        asof = "Waiting for prices"
    elif S["cls"] == "live":
        asof = f"Updated {now:%H:%M}"
    else:
        asof = f'Closing prices · {n["date"]:%d %b}'
    cls, lab, sub = _session_chip(S, now)
    chips = (f'<span class="hm-chip {cls}"><i></i><b>{lab}</b>· {_e(sub)}</span>{_regime_chip()}<span class="hm-chip">{asof}</span>')
    p = S.get("progress")
    day = ""
    if S["cls"] == "live" and p is not None:
        day = f'<div class="hm-day" title="Trading day, 09:15 to 15:30"><i style="width:{max(0.0, min(1.0, p)) * 100:.1f}%"></i></div>'
    return (f'<div class="hm-hd"><div><div class="o">{now:%A %d %B %Y}</div><div class="t">Market overview</div></div>'
            f'<div class="hm-chips">{chips}</div></div><div class="hm-brief">{" ".join(bits) or "Market data is loading."}</div>{day}')


def _vix_word(x):
    return "calm" if x < 13 else "normal" if x < 18 else "elevated" if x < 24 else "stressed"


# ───────────── your book ─────────────
def _stop_state(h):
    """'hit' / 'near' (within 3% above the stop) / 'target' / '' for one holding."""
    ltp, sl, tgt = h.get("ltp"), h.get("sl"), h.get("target")
    if not _ok(ltp):
        return ""
    if _ok(sl) and sl > 0:
        if ltp <= sl:
            return "hit"
        if ltp <= sl * 1.03:
            return "near"
    if _ok(tgt) and tgt > 0 and ltp >= tgt:
        return "target"
    return ""


_STOP_TAG = {"hit": ("Stop hit", RED), "near": ("Near stop", AMBER), "target": ("At target", GREEN)}


def _book_bar(holds):
    """Your money on one line: value, today, open profit, stops, best and worst holding today."""
    valid = [h for h in holds if h.get("value") is not None]
    if not valid:
        return ""
    val = sum(h["value"] for h in valid)
    inv = sum(h.get("invested") or 0 for h in valid)
    day = sum(h["day_pnl"] for h in valid if _ok(h.get("day_pnl")))
    pnl = val - inv
    dp = day / (val - day) * 100 if (val - day) else None
    pp_ = pnl / inv * 100 if inv else None
    states = [_stop_state(h) for h in valid]
    with_stop = sum(1 for h in valid if _ok(h.get("sl")) and h["sl"] > 0)
    hit, near = states.count("hit"), states.count("near")
    if not with_stop:
        stop = '<div><small>Stops</small><b class="hm-fl">Not set</b><em>Add stops in Portfolio</em></div>'
    elif hit or near:
        txt = " · ".join(x for x in (f"{hit} hit" if hit else "", f"{near} near" if near else "") if x)
        stop = (f'<div><small>Stops</small><b style="color:{RED if hit else AMBER}">{txt}</b>'
                f'<em>{with_stop} of {len(valid)} have a stop</em></div>')
    else:
        stop = f'<div><small>Stops</small><b class="hm-up">All clear</b><em>{with_stop} of {len(valid)} have a stop</em></div>'
    moved = [h for h in valid if _ok(h.get("chg"))]

    def mover(h, title, up):
        if not h or (up and h["chg"] <= 0) or (not up and h["chg"] >= 0):
            none = "No gainers today" if up else "No losers today"
            return f'<div><small>{title}</small><b class="hm-fl">—</b><em>{none}</em></div>'
        return (f'<div data-ak-sym="{_e(h["symbol"])}"><small>{title}</small><b>{_e(h["symbol"])}</b>'
                f'<em class="{_cls(h["chg"])}">{_pct(h["chg"])} · {_sinr(h.get("day_pnl"))}</em></div>')
    best = max(moved, key=lambda h: h["chg"]) if moved else None
    worst = min(moved, key=lambda h: h["chg"]) if moved else None
    n = len(holds)
    return ('<div class="hm-bk">'
            f'<div><small>Portfolio value</small><b>{_inr(val)}</b><em>{n} holding{"" if n == 1 else "s"}</em></div>'
            f'<div><small>Today</small><b class="{_cls(day)}">{_sinr(day)}</b><em class="{_cls(dp)}">{_pct(dp)}</em></div>'
            f'<div><small>Open profit</small><b class="{_cls(pnl)}">{_sinr(pnl)}</b><em class="{_cls(pp_)}">{_pct(pp_)}</em></div>'
            f'{stop}{mover(best, "Best today", True)}{mover(worst, "Worst today", False)}</div>')


def _holdings(holds, realised, span, compact):
    right = f'Realised {_sinr(realised)} all time' if realised else "By value"
    if not holds:
        body = '<div class="hm-empty"><b>No holdings yet.</b> Add your buys in Portfolio to see value, today&#39;s profit and stops here.</div>'
        return _card("Holdings", "", body, span, True)
    top = holds[:6]
    more = f'<div class="hm-empty">+ {len(holds) - 6} more in Portfolio</div>' if len(holds) > 6 else ""

    def tag(h):
        t = _STOP_TAG.get(_stop_state(h))
        return f'<span class="hm-tag" style="--c:{t[1]}">{t[0]}</span>' if t else ""
    if compact:
        rows = []
        for h in top:
            sym = _e(h["symbol"])
            if not _ok(h.get("ltp")):
                rows.append(f'<div class="hm-li" data-ak-sym="{sym}" style="opacity:.6"><div class="a"><b>{sym}</b>'
                            '<small>Price not available right now</small></div><div class="z"></div></div>')
                continue
            rows.append(f'<div class="hm-li" data-ak-sym="{sym}"><div class="a"><b>{sym}{tag(h)}</b>'
                        f'<small>₹{h["ltp"]:,.2f} · {_pct(h.get("weight"), 1, False)} of book</small></div>'
                        f'<div class="z"><b class="{_cls(h.get("chg"))}">{_pct(h.get("chg"))}</b>'
                        f'<small class="{_cls(h.get("pnl_pct"))}">{_pct(h.get("pnl_pct"), 1)} overall</small></div></div>')
        return _card("Holdings", right, "".join(rows) + more, span, True)
    rows = []
    for h in top:
        sym = _e(h["symbol"])
        if not _ok(h.get("ltp")):
            rows.append(f'<tr data-ak-sym="{sym}" class="st"><td class="l">{sym}</td><td colspan="5" style="text-align:left">Price not available right now</td></tr>')
            continue
        w = h.get("weight")
        wt = (f'<span class="hm-wt"><i style="width:{max(0.0, min(100.0, w)):.0f}%"></i></span>{w:.0f}%' if _ok(w) else "—")
        rows.append(f'<tr data-ak-sym="{sym}"><td class="l">{sym}{tag(h)}</td><td class="n">₹{h["ltp"]:,.2f}</td>'
                    f'<td class="{_cls(h.get("chg"))}">{_pct(h.get("chg"))}</td>'
                    f'<td class="{_cls(h.get("day_pnl"), 0.5)}">{_sinr(h.get("day_pnl"))}</td>'
                    f'<td class="{_cls(h.get("pnl_pct"))}" title="{_e(_sinr(h.get("pnl")))}">{_pct(h.get("pnl_pct"), 1)}</td><td>{wt}</td></tr>')
    table = ('<div style="overflow-x:auto"><table class="hm-t"><thead><tr><th>Stock</th><th>Price</th><th>Today</th><th>Today ₹</th>'
             f'<th>Overall</th><th>Weight</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    return _card("Holdings", right, table + more, span, True)


def _watch(wl, wl_label, quotes, span):
    if not wl:
        body = '<div class="hm-empty"><b>Your watchlist is empty.</b> Add the stocks you are stalking in Watchlist and they show up here.</div>'
        return _card("Watchlist", "", body, span, True)
    rows = []
    for s in wl[:7]:
        q = quotes.get(s) or {}
        px = "₹" + format(q["price"], ",.2f") if _ok(q.get("price")) else "—"
        rows.append(f'<div class="hm-li" data-ak-sym="{_e(s)}"><div class="a"><b>{_e(s)}</b></div>'
                    f'<div class="z" style="display:flex;gap:16px;align-items:baseline"><b>{px}</b>'
                    f'<small class="{_cls(q.get("chg"))}" style="min-width:58px;margin:0">{_pct(q.get("chg"))}</small></div></div>')
    more = f'{len(wl)} stocks' if len(wl) > 7 else "Today"
    return _card(_e(wl_label), more, "".join(rows), span, True)


# ───────────── NIFTY 50 ─────────────
def _nice_ticks(lo, hi, most=5.5):
    """Round gridline values inside lo..hi (about five of them) -> (values, step)."""
    span = hi - lo
    if not (span > 0):
        return [], 1.0
    mag = 10 ** math.floor(math.log10(span))
    step = next((mag * f for f in (0.1, 0.2, 0.25, 0.5, 1, 2) if span / (mag * f) <= most), mag * 2)
    v, out = math.ceil(lo / step) * step, []
    while v <= hi + 1e-9 and len(out) < 12:
        out.append(v)
        v += step
    return out, step


def _chart(s, color, h=160, bars=126, uid="n", note=""):
    """Close line with 20- and 50-day averages, a price scale on the right and month marks. Pure SVG + HTML labels."""
    if s is None:
        return ""
    s = s.dropna()
    win = s.tail(bars)
    n = len(win)
    if n < 5:
        return ""
    vals = [float(v) for v in win.tolist()]
    mas = []
    for k, col in ((20, MA20_C), (50, MA50_C)):
        if len(s) >= k + 5:
            mas.append((col, [float(v) if _ok(float(v)) else None for v in s.rolling(k).mean().tail(n).tolist()]))
    every = vals + [v for _, vs in mas for v in vs if v is not None]
    lo, hi = min(every), max(every)
    pad = (hi - lo) * 0.07 or 1.0
    lo, hi = lo - pad, hi + pad
    W = 1000.0

    def X(i):
        return i / (n - 1) * W

    def Y(v):
        return h - (v - lo) / (hi - lo) * h
    line = "M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    svg = [f'<svg viewBox="0 0 {W:.0f} {h}" preserveAspectRatio="none"><defs><linearGradient id="hmg{uid}" x1="0" y1="0" x2="0" y2="1">'
           f'<stop offset="0%" stop-color="{color}" stop-opacity=".22"/><stop offset="100%" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>']
    ticks, step = _nice_ticks(lo, hi)
    for tv in ticks:
        svg.append(f'<line x1="0" y1="{Y(tv):.1f}" x2="{W:.0f}" y2="{Y(tv):.1f}" stroke="#1E1E22" stroke-width="1" vector-effect="non-scaling-stroke"/>')
    months, last_m = [], None
    for i, d in enumerate(win.index):
        key = (d.year, d.month)
        if last_m is not None and key != last_m:
            months.append((i, d))
            svg.append(f'<line x1="{X(i):.1f}" y1="0" x2="{X(i):.1f}" y2="{h}" stroke="#19191C" stroke-width="1" vector-effect="non-scaling-stroke"/>')
        last_m = key
    svg.append(f'<path d="{line} L{W:.0f},{h} L0,{h} Z" fill="url(#hmg{uid})"/>')
    for col, vs in mas:
        pts = [(X(i), Y(v)) for i, v in enumerate(vs) if v is not None]
        if len(pts) >= 2:
            d_ = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            svg.append(f'<path d="{d_}" fill="none" stroke="{col}" stroke-opacity=".8" stroke-width="1.1" vector-effect="non-scaling-stroke"/>')
    svg.append(f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>')
    y_last = Y(vals[-1]) / h * 100
    fmt = ",.0f" if step >= 1 else ",.2f"
    ax = "".join(f'<span style="top:{Y(tv) / h * 100:.1f}%">{format(tv, fmt)}</span>' for tv in ticks if abs(Y(tv) / h * 100 - y_last) > 10)
    ax += f'<b style="top:{y_last:.1f}%;background:{color}">{format(vals[-1], fmt)}</b>'
    xs, prev_x = [], -100.0
    for i, d in months:
        x = X(i) / W * 100
        if x - prev_x >= 9 and x <= 93:
            xs.append(f'<span style="left:{x:.1f}%">{d:%b}</span>')
            prev_x = x
    lgd = (f'<div class="hm-lgd"><span><i style="background:{color}"></i>Close</span>'
           + "".join(f'<span><i style="background:{c}"></i>{k}-day avg</span>' for (c, _), k in zip(mas, (20, 50))) + note + '</div>')
    return (f'<div class="hm-ch" style="--h:{h}px"><div class="plot">{"".join(svg)}'
            f'<i class="dot" style="top:{y_last:.1f}%;background:{color};box-shadow:0 0 0 4px {color}33"></i></div>'
            f'<div class="ax">{ax}</div><div class="xs">{"".join(xs)}</div></div>{lgd}')


def _pivots(m, S, now=None):
    """Classic floor pivots -> (pivot, r1, s1, hi, lo, live). During the session they come from the previous day;
    after the close they come from the session that just ended, so they are the levels for the next session."""
    now = now or datetime.now(IST)
    try:
        today = pd.Timestamp(m["date"]).date() == now.date()
    except Exception:
        today = False
    live = S["cls"] == "live" and today
    prev = m["price"] - m["pts"] if _ok(m.get("pts")) else None
    if live:
        H, L, C = m.get("pdh"), m.get("pdl"), prev
    else:
        H, L, C = m.get("high"), m.get("low"), m.get("price")
    if not (_ok(H) and _ok(L) and _ok(C)) or H < L:
        return None, None, None, H if _ok(H) else None, L if _ok(L) else None, live
    p = (H + L + C) / 3
    return p, 2 * p - L, 2 * p - H, H, L, live


def _hero(M, S, span="", compact=False):
    m = M.get("NIFTY 50")
    if not m:
        return _card("NIFTY 50", "", '<div class="hm-empty">NIFTY 50 data is unavailable right now. It retries automatically.</div>', span)
    s = m.get("series")
    price, col = m["price"], (GREEN if (m["chg"] or 0) >= 0 else RED)
    prev = price - m["pts"] if _ok(m.get("pts")) else None
    per = "".join(f'<span>{k}<b class="{_cls(v)}">{_pct(v, 1)}</b></span>' for k, v in (("1W", m["w1"]), ("1M", m["m1"]), ("YTD", m["ytd"])))

    def dma(k):
        return float(s.tail(k).mean()) if s is not None and len(s) >= k else None

    def vs(level):
        """(text, class) for how far the price is from a level."""
        if not _ok(level) or not level:
            return "", ""
        d = (price / level - 1) * 100
        return f"{d:+.1f}%", _cls(d)
    piv, r1, s1, H, L, live = _pivots(m, S)
    cells = []

    def cell(label, val, sub="", cls="", f=",.0f"):
        v = format(val, f) if _ok(val) else "—"
        cells.append(f'<div><small>{label}</small><b>{v}</b><em class="{cls}">{sub or "&nbsp;"}</em></div>')
    op = m.get("open")
    gap = (op / prev - 1) * 100 if _ok(op) and _ok(prev) and prev else None
    cell("Open", op, f"Gap {_pct(gap)}" if gap is not None else "", _cls(gap))
    cell("Prev close", prev)
    cell("Day high", m.get("high"))
    cell("Day low", m.get("low"))
    cell("52W high", m.get("hi52"), *vs(m.get("hi52")))
    cell("52W low", m.get("lo52"), *vs(m.get("lo52")))
    cell("Pivot", piv, *vs(piv))
    cell("Resistance", r1, *vs(r1))
    cell("Support", s1, *vs(s1))
    for k in (20, 50, 200):
        cell(f"{k}-day avg", dma(k), *vs(dma(k)))
    when = "today" if live else "the next session"
    basis = "yesterday" if live else "the last session"
    note = (f'<span class="nt" title="Classic floor pivots from the high, low and close of {basis}. The small percentages show how far '
            f'NIFTY is from each level.">Pivot, resistance and support are for {when}</span>')
    body = (f'<div class="hm-htop"><div><div class="hm-hv">{price:,.2f}</div>'
            f'<div class="hm-hc {_cls(m["chg"])}">{_arrow(m["chg"])} {m["pts"]:+,.2f} ({_pct(m["chg"])}) today</div></div><div class="hm-per">{per}</div></div>'
            f'{_chart(s, col, 132 if compact else 164, note=note)}<div class="hm-st">{"".join(cells)}</div>')
    card = _card("NIFTY 50", f'NSE · {m["date"]:%d %b} · 6 months, daily', body, span, cls="hm-hero")
    return card.replace('<div class="hm-p ', f'<div style="--hg:{col}" class="hm-p ', 1)


# ───────────── market health ─────────────
def _checks(M, B):
    """Six plain yes/no checks on trend, breadth and volatility -> [(text, passed)]."""
    n, v = M.get("NIFTY 50"), M.get("INDIA VIX")
    checks = []
    s = n.get("series") if n else None
    if s is not None and len(s) >= 200:
        d50, d200 = float(s.tail(50).mean()), float(s.tail(200).mean())
        checks += [("Above 50-day average", n["price"] > d50), ("Above 200-day average", n["price"] > d200),
                   ("50-day above 200-day", d50 > d200)]
    if n and _ok(n.get("hi52")):
        checks.append(("Within 10% of 52W high", n["price"] >= 0.9 * n["hi52"]))
    if B and B["n"] >= 30:
        checks.append((f'More stocks up ({B["adv"]} vs {B["dec"]})', B["adv"] > B["dec"]))
    vs = v.get("series") if v else None
    if vs is not None and len(vs) >= 100:
        checks.append((f"VIX below 1Y median ({float(vs.median()):.1f})", v["price"] < float(vs.median())))
    return checks


def _sector_count(M):
    vals = [M["SEC::" + l]["chg"] for l, _ in SECTORS if M.get("SEC::" + l) and _ok(M["SEC::" + l].get("chg"))]
    return sum(1 for x in vals if x > 0.05), len(vals)


def _health(M, mmi, B, span=""):
    checks = _checks(M, B)
    if checks:
        score, tot = sum(1 for _, ok in checks if ok), len(checks)
        r = score / tot
        lab, c = (("Risk-on", GREEN) if r >= 0.83 else ("Constructive", "#84CC16") if r >= 0.66 else ("Neutral", YELLOW) if r >= 0.5
                  else ("Cautious", AMBER) if r >= 0.33 else ("Risk-off", RED))
        meter = "".join(f'<i style="background:{c if i < score else ""}"></i>' for i in range(tot))
        top = (f'<div class="hm-vd"><b style="color:{c}">{lab}</b><span>{score} of {tot} checks pass</span></div>'
               f'<div class="hm-meter">{meter}</div>')
        rows = "".join(f'<div class="hm-ck"><i class="{"y" if ok else "n"}">{"✓" if ok else "✕"}</i><span title="{_e(t)}">{_e(t)}</span></div>'
                       for t, ok in checks)
        rows = f'<div class="hm-cks">{rows}</div>'
    else:
        top, rows = '<div class="hm-empty">The trend checks need NIFTY 50 history. They retry automatically.</div>', ""
    g = []
    if B and B["n"]:
        tot_ = max(B["n"], 1)
        g.append('<div class="hm-gz"><small>Breadth</small>'
                 f'<b><span class="hm-up">{B["adv"]}</span> <span class="hm-fl">/</span> <span class="hm-dn">{B["dec"]}</span></b>'
                 f'<em>Average {_pct(B["avg"], 1)}</em>'
                 f'<div class="ad"><i style="width:{B["adv"] / tot_ * 100:.1f}%;background:{GREEN}"></i>'
                 f'<i style="width:{B["flat"] / tot_ * 100:.1f}%;background:#3a3a3e"></i>'
                 f'<i style="width:{B["dec"] / tot_ * 100:.1f}%;background:{RED}"></i></div></div>')
    else:
        g.append('<div class="hm-gz"><small>Breadth</small><b class="hm-fl">—</b><em>Waiting for quotes</em></div>')
    v = M.get("INDIA VIX")
    kv = []
    if v:
        vs = v.get("series")
        pctl = float((vs < v["price"]).mean() * 100) if vs is not None and len(vs) >= 60 else None
        word = _vix_word(v["price"])
        vc = {"calm": GREEN, "normal": "#84CC16", "elevated": AMBER, "stressed": RED}[word]
        g.append(f'<div class="hm-gz"><small>India VIX</small><b>{v["price"]:.2f}</b>'
                 f'<em>{word.capitalize()} · <span class="{_cls(-v["chg"]) if _ok(v.get("chg")) else ""}">{_pct(v["chg"], 1)}</span></em>'
                 f'<div class="lv"><i style="width:{max(4.0, min(100.0, pctl if pctl is not None else 50.0)):.0f}%;background:{vc}"></i></div></div>')
        if pctl is not None:
            kv.append(f'<div class="hm-kv"><span>VIX against its last year</span><b>higher than {pctl:.0f}% of days</b></div>')
    else:
        g.append('<div class="hm-gz"><small>India VIX</small><b class="hm-fl">—</b><em>Unavailable</em></div>')
    if mmi.get("status") in ("live", "stale"):
        sc, zone = float(mmi["score"]), str(mmi["zone"]).capitalize()
        g.append(f'<div class="hm-gz"><small>Market mood</small><b style="color:{mmi_zone_color(mmi["zone"])}">{sc:.0f}</b>'
                 f'<em>{_e(zone)}{" · cached" if mmi["status"] == "stale" else ""}</em>'
                 f'<div class="gg"><i style="left:{max(0, min(100, sc)):.1f}%"></i></div></div>')
    else:
        g.append('<div class="hm-gz"><small>Market mood</small><b class="hm-fl">—</b><em>Unavailable right now</em></div>')
    up, n_sec = _sector_count(M)
    if n_sec:
        kv.append(f'<div class="hm-kv"><span>Sectors advancing</span><b><span class="{"hm-up" if up * 2 > n_sec else "hm-dn" if up * 2 < n_sec else ""}">{up}</span> of {n_sec}</b></div>')
    n = M.get("NIFTY 50")
    ns = n.get("series") if n else None
    rsi = _rsi(ns) if ns is not None and len(ns) > 30 else None
    pre = []
    if rsi is not None:
        word = "overbought" if rsi >= 70 else "strong" if rsi >= 55 else "neutral" if rsi > 45 else "weak" if rsi > 30 else "oversold"
        pre.append(f'<div class="hm-kv"><span>NIFTY RSI (14)</span><b>{rsi:.0f} · {word}</b></div>')
    if n and v and _ok(v.get("price")):
        mv = v["price"] / math.sqrt(252)
        pre.append(f'<div class="hm-kv"><span>Expected daily move, from VIX</span><b>±{n["price"] * mv / 100:,.0f} pts · ±{mv:.2f}%</b></div>')
    kv = pre + kv
    fx = M.get("USD / INR")
    if fx and _ok(fx.get("chg")):
        kv.append(f'<div class="hm-kv"><span>USD / INR</span><b>{fx["price"]:.2f} <span class="{_cls(-fx["chg"])}">{_pct(fx["chg"], 2)}</span></b></div>')
    body = top + f'<div class="hm-g3">{"".join(g)}</div>' + rows + f'<div class="hm-kvs">{"".join(kv)}</div>'
    return _card("Market health", "Trend, breadth and volatility", body, span)


# ───────────── futures and options ─────────────
def _fno(compact=False):
    """The F&O engine's verdict for NIFTY 50 and BANK NIFTY, from fno_page.py. Shows nothing if that file is not installed."""
    try:
        import fno_page
        rows = fno_page.home_rows()
    except Exception:
        return ""
    if rows is None:
        body = '<div class="hm-empty">Reading the stored F&O history. The verdict appears here in a moment; the F&O tab has the full picture.</div>'
        return _sec("Futures and options", "" if compact else "What positioning says, tested on the stored history") + _grid(_card("F&O view", "", body, "c12 m2"))
    if not rows:
        return ""

    def num(v, d=0):
        return f"{v:,.{d}f}" if _ok(v) else "—"

    cards = []
    for r in rows:
        col = GREEN if r["tone"] == "up" else RED if r["tone"] == "dn" else "var(--tx2)"
        sub = f'next 10 sessions · model confidence {_e(str(r.get("conf", "untested")).lower())}'
        fc = r.get("forecast")
        kv = [f'<div class="hm-kv"><span>Model forecast, 10 sessions</span><b class="{"hm-up" if (_ok(fc) and fc > 0) else "hm-dn" if (_ok(fc) and fc < 0) else ""}">{f"{fc * 100:+.1f}%" if _ok(fc) else "—"}</b></div>',
              f'<div class="hm-kv"><span>Put-call ratio</span><b>{num(r["pcr"], 2)}</b></div>',
              f'<div class="hm-kv"><span>Implied volatility</span><b>{num(r["iv"] * 100, 1) + "%" if _ok(r["iv"]) else "—"}</b></div>',
              f'<div class="hm-kv"><span>Max pain</span><b>{num(r["max_pain"])}</b></div>',
              f'<div class="hm-kv"><span>Put wall · call wall</span><b>{num(r["put_wall"])} · {num(r["call_wall"])}</b></div>']
        if _ok(r.get("move5")):
            kv.append(f'<div class="hm-kv"><span>Priced move, 5 sessions</span><b>±{num(r["move5"])} pts</b></div>')
        if r.get("reason"):
            kv.append(f'<div class="hm-kv"><span>Biggest driver</span><b>{_e(r["reason"])}</b></div>')
        body = f'<div class="hm-vd"><b style="color:{col}">{_e(r["verdict"])}</b><span>{sub}</span></div><div class="hm-kvs" style="margin-top:10px">{"".join(kv)}</div>'
        asof = ""
        try:
            d = date.fromisoformat(str(r["asof"]))
            asof = f"Data to {d.day} {d.strftime('%b')}"
        except Exception:
            pass
        cards.append(_card(_e(r["name"]) + " · F&O view", asof, body, "" if compact else "c6"))
    return _sec("Futures and options", "" if compact else "What positioning says, tested on the stored history · full evidence in the F&O tab") + _grid("".join(cards))


# ───────────── benchmarks ─────────────
def _bench(M, labels=BENCH):
    out = []
    for label in labels:
        m = M.get(label)
        if not m:
            continue
        out.append(f'<div class="hm-ix"><div class="n">{_e(_nm(label))}</div>'
                   f'<div class="r"><div class="v">{m["price"]:,.2f}</div>{_spark(m.get("spark", [])[-22:], 58, 24)}</div>'
                   f'<div class="c {_cls(m["chg"])}">{_chg_txt(m, "idx")} ({_pct(m["chg"])})</div>'
                   f'<div class="w"><span>1W<b class="{_cls(m["w1"])}">{_pct(m["w1"], 1)}</b></span>'
                   f'<span>1M<b class="{_cls(m["m1"])}">{_pct(m["m1"], 1)}</b></span></div></div>')
    return f'<div class="hm-ixs">{"".join(out)}</div>' if out else ""


# ───────────── quote tables ─────────────
def _table(rows_spec, M, cols, show_date=True):
    """Desktop grouped quote table. Rows whose last trade is more than four days behind the newest are dimmed."""
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
        tds = [f'<td class="l" title="Last trade {m["date"]:%d %b}">{_e(_nm(label))}</td>', f'<td class="n">{_px(m["price"], kind)}</td>',
               cell(m["chg"], m["pts"]) if not yl else f'<td class="{_cls(m["pts"])}">{m["pts"] * 100:+.1f} bp</td>']
        if "1W" in cols:
            tds.append(cell(m["w1"], m["w1_pts"]))
        tds += [cell(m["m1"], m["m1_pts"]), cell(m["ytd"], m["ytd_pts"])]
        if "1M trend" in cols:
            tds.append(f'<td>{_spark(m["spark"], 72, 20)}</td>')
        if show_date:
            tds.append(f'<td style="color:var(--tx3)">{m["date"]:%d %b}</td>')
        tr = '<tr class="st">' if stale else "<tr>"
        body.append(f'{tr}{"".join(tds)}</tr>')
    if not body:
        return '<div class="hm-empty">Quotes are unavailable right now. They retry automatically.</div>'
    return f'<div style="overflow-x:auto"><table class="hm-t"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _qlist(rows_spec, M, groups=True, limit_per_group=None, skip=()):
    """Phone quote list: name and 1-month change on the left, price and 1-day change on the right."""
    out, last_g, n_in_g = [], None, 0
    for g, label, _, kind in rows_spec:
        m = M.get(label)
        if not m or g in skip:
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


# ───────────── leadership ─────────────
def _breadth(nq):
    vals = [q.get("chg") for q in nq.values() if q and _ok(q.get("chg"))]
    adv = sum(1 for v in vals if v > 0.05)
    dec = sum(1 for v in vals if v < -0.05)
    return {"adv": adv, "dec": dec, "flat": len(vals) - adv - dec, "n": len(vals),
            "avg": float(np.mean(vals)) if vals else None, "med": float(np.median(vals)) if vals else None}


def _imap(groups, nq, B=None):
    blocks = []
    for g, syms in groups.items():
        tiles = []
        for s in syms:
            q = nq.get(s) or {}
            c, px = q.get("chg"), q.get("price")
            tip = f"{s} · ₹{px:,.2f}" if _ok(px) else f"{s} · no price"
            nq_ = "" if _ok(c) else " nq"
            tiles.append(f'<div class="hm-tile{nq_}" data-ak-sym="{_e(s)}" style="background:{_heat(c)}" title="{_e(tip)}"><b>{_e(s)}</b><span>{_pct(c, 1)}</span></div>')
        blocks.append(f'<div class="hm-grp"><small>{_e(_nm(g))}</small><div class="hm-tiles">{"".join(tiles)}</div></div>')
    leg = '<div class="hm-leg"><span>−3%</span><i></i><span>+3%</span><span style="margin-left:auto">Tap a stock to open it</span></div>'
    return f'<div class="hm-imap">{"".join(blocks)}</div>' + leg


def _sector_rows(M):
    rows = [(label, M["SEC::" + label]) for label, _ in SECTORS if M.get("SEC::" + label)]
    rows.sort(key=lambda x: -(x[1]["chg"] if _ok(x[1]["chg"]) else -99))
    return rows


def _sector_matrix(M):
    rows = _sector_rows(M)
    if not rows:
        return '<div class="hm-empty">Sector indices are unavailable right now.</div>'
    body = "".join(
        f'<tr><td class="rk">{i}</td><td class="l" style="text-align:left">{_e(_nm(l))}</td><td class="n">{m["price"]:,.0f}</td>'
        + "".join(f'<td class="c" style="background:{_heat(v, lim)}">{_pct(v, 1)}</td>' for v, lim in
                  ((m["chg"], 2.5), (m["w1"], 5), (m["m1"], 9), (m["ytd"], 25)))
        + f'<td>{_spark(m["spark"], 70, 20)}</td></tr>' for i, (l, m) in enumerate(rows, 1))
    return ('<div style="overflow-x:auto"><table class="hm-t hm-hm"><thead><tr><th></th><th style="text-align:left">Sector</th><th>Last</th><th>1D</th><th>1W</th>'
            f'<th>1M</th><th>YTD</th><th>1M trend</th></tr></thead><tbody>{body}</tbody></table></div>')


def _sector_tiles(M):
    """Phone: sectors as heat tiles, three across, ranked by today."""
    rows = _sector_rows(M)
    if not rows:
        return '<div class="hm-empty">Sector indices are unavailable right now.</div>'
    tiles = "".join(f'<div class="hm-stile" style="background:{_heat(m["chg"], 2.5)}" title="1W {_pct(m["w1"], 1)} · 1M {_pct(m["m1"], 1)}">'
                    f'<b>{_e(_nm(l))}</b><span>{_pct(m["chg"], 2)}</span></div>' for l, m in rows)
    return f'<div class="hm-stiles">{tiles}</div>'


def _sector_list(M):
    rows = _sector_rows(M)
    if not rows:
        return '<div class="hm-empty">Sector indices are unavailable right now.</div>'
    return "".join(f'<div class="hm-li"><div class="a"><b>{_e(_nm(l))}</b><small>1W {_pct(m["w1"], 1)} · 1M {_pct(m["m1"], 1)}</small></div>'
                   f'<div class="z"><span class="hm-pct" style="background:{_heat(m["chg"], 2.5)};color:#fff">{_pct(m["chg"], 2)}</span></div></div>'
                   for l, m in rows)


def _movers(nq, k=5):
    ranked = sorted([(s, q) for s, q in nq.items() if q and _ok(q.get("chg"))], key=lambda x: -x[1]["chg"])
    if not ranked:
        return '<div class="hm-empty">NIFTY 50 quotes are unavailable right now.</div>'

    def col(items, title):
        if not items:
            return f'<div><div class="hm-lg">{title}</div><div class="hm-empty">None today.</div></div>'
        rows = "".join(f'<div class="hm-li" data-ak-sym="{_e(s)}"><div class="a"><b>{_e(s)}</b><small>₹{q["price"]:,.2f}</small></div>'
                       f'<div class="z"><small class="{_cls(q["chg"])}" style="font-size:13.5px;margin:0">{_pct(q["chg"])}</small></div></div>' for s, q in items)
        return f'<div><div class="hm-lg">{title}</div>{rows}</div>'
    g = [x for x in ranked[:k] if x[1]["chg"] > 0]
    l = [x for x in ranked[::-1][:k] if x[1]["chg"] < 0]
    return f'<div class="hm-mv">{col(g, "Top gainers")}{col(l, "Top losers")}</div>'


def _rotation(M=None):
    """-> (context text, body). Uses the Money flow snapshot when that page has been opened this session; otherwise a
    simple reading from the sector indices already on this page: 1-month return against NIFTY 50."""
    snap = st.session_state.get("mf_rot")
    if snap and snap.get("table") is not None:
        tbl = snap["table"]
        colors = {"ROTATING IN": GREEN, "LEADING": "#8AD66B", "STRETCHED": YELLOW, "WATCH": CYAN, "WEAKENING": AMBER, "LAGGING": RED}
        rows = []
        for r in tbl.sort_values("score", ascending=False).head(6).to_dict("records"):
            sc, ex = r.get("score"), r.get("ex1m")
            sc_t = f"{sc:.0f}" if _ok(sc) else "—"
            rows.append(f'<div class="hm-li"><div class="a"><b>{_e(str(r["sector"])[:26])}</b><small>Score {sc_t} · vs market 1M '
                        f'<span class="{_cls(ex)}">{_pct(ex, 1)}</span></small></div>'
                        f'<div class="z"><span class="hm-pill" style="color:{colors.get(r["action"], T2)}">{_e(str(r["action"]).capitalize())}</span></div></div>')
        return f'From Money flow · {_e(snap.get("asof", ""))}', "".join(rows)
    n = (M or {}).get("NIFTY 50")
    rows = [(l, m) for l, m in ((l, (M or {}).get("SEC::" + l)) for l, _ in SECTORS) if m and _ok(m.get("m1"))]
    if not n or not _ok(n.get("m1")) or len(rows) < 6:
        return "", '<div class="hm-empty">Open <b>Money flow</b> once this session to see where money is rotating.</div>'
    rel = sorted(((l, m["m1"] - n["m1"], m) for l, m in rows), key=lambda x: -x[1])

    def row(l, d, m, lead):
        w1 = m["w1"] - n["w1"] if _ok(m.get("w1")) and _ok(n.get("w1")) else None
        if lead:
            tag, c = ("Leading", GREEN) if (w1 is None or w1 >= 0) else ("Cooling", YELLOW)
        else:
            tag, c = ("Lagging", RED) if (w1 is None or w1 <= 0) else ("Turning up", CYAN)
        return (f'<div class="hm-li"><div class="a"><b>{_e(_nm(l))}</b><small>vs NIFTY 1M <span class="{_cls(d)}">{_pct(d, 1)}</span>'
                f' · 1W <span class="{_cls(w1)}">{_pct(w1, 1)}</span></small></div>'
                f'<div class="z"><span class="hm-pill" style="color:{c}">{tag}</span></div></div>')
    body = ('<div class="hm-lg">Stronger than NIFTY</div>' + "".join(row(l, d, m, True) for l, d, m in rel[:3] if d > 0)
            + '<div class="hm-lg">Weaker than NIFTY</div>' + "".join(row(l, d, m, False) for l, d, m in rel[::-1][:3] if d < 0))
    return "1-month return against NIFTY 50", body


# ───────────── calendar ─────────────
def _session_panel(S, span=""):
    now = datetime.now(IST)
    col = GREEN if S["cls"] == "live" else AMBER if S["cls"] == "pre" else T2
    p = S.get("progress")
    prog = ""
    if p is not None:
        prog = (f'<div class="hm-prog"><i style="width:{max(0.0, min(1.0, p)) * 100:.1f}%"></i></div>'
                '<div class="hm-ticks"><span>09:15</span><span>12:00</span><span>15:30</span></div>')
    special = SPECIAL_SESSIONS.get(now.date())

    def dd(d):
        k = (d - now.date()).days
        return f'{d:%a %d %b}' + (" · today" if k == 0 else " · tomorrow" if k == 1 else f" · in {k} days")
    exp = (f'<div class="hm-kv"><span>NIFTY weekly expiry</span><b>{dd(next_expiry(now, 1))}</b></div>'
           f'<div class="hm-kv"><span>Monthly F&amp;O expiry</span><b>{dd(next_expiry(now, 1, True))}</b></div>'
           f'<div class="hm-kv"><span>SENSEX weekly expiry</span><b>{dd(next_expiry(now, 3))}</b></div>')
    body = (f'<div class="hm-big" style="color:{col}">{_e(S["short"].capitalize())}</div><div class="hm-cap">{_e(S["detail"])}</div>{prog}'
            + (f'<div class="hm-cap" style="color:{AMBER};margin:6px 0">{_e(special)}</div>' if special else "")
            + f'<div class="hm-kvs" style="margin-top:12px">{exp}</div>')
    return _card("NSE session", now.strftime("%a %d %b · %H:%M IST"), body, span)


def _holiday_panel(span=""):
    now = datetime.now(IST)
    rows = []
    for d, nm, kind in upcoming_holidays(5, now.date()):
        k = (d - now.date()).days
        when = "today" if k == 0 else "tomorrow" if k == 1 else f"in {k} days"
        rows.append(f'<div class="hm-kv"><span>{_e(nm.split(" · ")[0])}{" (special session)" if kind == "SPECIAL" else ""}</span><b>{d:%a %d %b} · {when}</b></div>')
    body = (f'<div class="hm-kvs first">{"".join(rows)}</div>' if rows else '<div class="hm-empty">No more holidays listed this year.</div>')
    body += f'<div class="hm-kvs"><div class="hm-kv"><span>Trading days left in {now.year}</span><b>{trading_days_left(now.date())}</b></div></div>'
    return _card("Market holidays", "NSE equity segment", body, span)


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


def _skeleton():
    try:
        import auth
        logo = auth.logo_data_uri(96)
    except Exception:
        logo = ""
    img = f'<img src="{logo}" alt="">' if logo else ""
    return ('<div class="hm"><div class="hm-load">' + img + '<div><b>Reading the market</b><span>Indices, sectors, your stocks and the mood index</span></div>'
            '<div class="bar"><i></i></div></div>'
            '<div class="sk-bar"><div class="sk"></div></div>'
            '<div class="sk-a"><div class="sk"></div><div class="sk"></div></div>'
            '<div class="sk-b">' + '<div class="sk"></div>' * 6 + '</div></div>')


def _gather(supabase):
    """Everything the page needs. The downloads underneath are cached and take turns (yfinance is not thread-safe)."""
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
    """The page, most important first:
       1 status + one-line brief     2 your money on one line     3 NIFTY 50 and market health     4 other benchmarks
       5 your holdings and watchlist 6 leadership (sectors, movers, rotation, heatmap)
       7 world and macro             8 calendar"""
    n50 = [s for v in groups.values() for s in v]
    nq = {s: quotes.get(s) for s in n50}
    B = _breadth(nq)
    S = nse_session()
    holds, realised = _book_holdings(base, quotes)
    up, n_sec = _sector_count(M)
    sec_right = (f"{up} of {n_sec} up · ranked by today" if n_sec else "")
    rot_right, rot_body = _rotation(M)
    heat_right = f'{B["adv"]} up · {B["flat"]} flat · {B["dec"]} down'
    top = _ticker(M) + _head(M, B, S) + _book_bar(holds)
    has_book = bool(holds or wl)
    empty_book = ('<div class="hm-p c12 m2"><div class="hm-empty" style="padding:14px"><b>Make this page yours.</b> Add your buys in Portfolio and the '
                  'stocks you are stalking in Watchlist: your profit for the day, stops and prices then sit at the top of this page.</div></div>')

    if compact:
        book = (_holdings(holds, realised, "", True) + _watch(wl, wl_label, quotes, "")) if has_book else empty_book
        html_ = (top
                 + _grid(_hero(M, S, "", True)) + _bench(M) + _grid(_health(M, mmi, B))
                 + _fno(True)
                 + _sec("Your book") + _grid(book)
                 + _sec("Leadership", "today")
                 + _grid(_card("Top movers", "NIFTY 50", _movers(nq), flush=True)
                         + _card("Sectors", sec_right, _sector_tiles(M), flush=True)
                         + _card("Sector rotation", rot_right, rot_body, flush=True)
                         + _card("NIFTY 50 heatmap", heat_right, _imap(groups, nq), flush=True))
                 + _sec("World and macro")
                 + _grid(_card("Global markets", "Local currency", _qlist(WEI, M, skip=("INDIA",)), flush=True)
                         + _card("Commodities, currencies and rates", "", _qlist(XASSET, M), flush=True))
                 + _sec("Calendar")
                 + _grid(_session_panel(S) + _holiday_panel()))
        st.markdown(f'<div class="hm hm-compact">{html_}</div>', unsafe_allow_html=True)
        return

    book = (_holdings(holds, realised, "c7 m2", False) + _watch(wl, wl_label, quotes, "c5 m2")) if has_book else empty_book
    wei = _table(WEI, M, ["Index", "Last", "1D", "1W", "1M", "YTD", "1M trend"], show_date=False)
    xa = (_table(XASSET, M, ["Instrument", "Last", "1D", "1M", "YTD"], show_date=False)
          + '<div class="hm-empty" style="border-top:1px solid var(--line)">Yields move in basis points (100 bp = 1%). '
            'For rupee pairs a rise means a weaker rupee. Hover a name for its last trade date.</div>')
    lead_right = ('<div class="hm-stack c5 m2">' + _card("Top movers", "NIFTY 50 · today", _movers(nq), flush=True)
                  + _card("Sector rotation", rot_right, rot_body, flush=True) + '</div>')
    html_ = (top
             + _grid(_hero(M, S, "c7 m2") + _health(M, mmi, B, "c5 m2"))
             + _bench(M)
             + _fno()
             + _sec("Your book", "Holdings and the stocks you are watching") + _grid(book)
             + _sec("Leadership", "Sectors and stocks moving the market today")
             + _grid(_card("Sectors", sec_right, _sector_matrix(M), "c7 m2", True) + lead_right
                     + _card("NIFTY 50 heatmap", heat_right, _imap(groups, nq), "c12 m2", True))
             + _sec("World and macro", "Global indices, commodities, currencies and rates")
             + _grid(_card("Global indices", "Local currency", wei, "c7 m2", True)
                     + _card("Commodities, currencies and rates", "", xa, "c5 m2", True))
             + _sec("Calendar", "Session, expiries, holidays and world market hours")
             + _grid(_session_panel(S, "c4") + _holiday_panel("c4") + _card("World markets", "Local time", _world_clock(), "c4 m2")))
    st.markdown(f'<div class="hm">{html_}</div>', unsafe_allow_html=True)


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
    st.markdown('<div class="hm-foot">Prices from Yahoo Finance, refreshed every 90 seconds during the session (free feeds can trail the exchange by a few minutes); changes are close-to-close. '
                'Market health, mood, pivots and the heatmap are information, not advice. NIFTY 50 list as of 30 Sep 2026 · Market Mood Index © Tickertape.</div>', unsafe_allow_html=True)
