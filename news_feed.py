"""
news_feed.py — Bloomberg-style Global Market News Feed
Fetches market-moving headlines: Indian stocks, indices, macro, global markets, commodities, crypto.
"""

import streamlit as st
import feedparser
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=5, minutes=30))

# ── Colors ──────────────────────
GOLD = "#FFB81C"
RED = "#FF453A"
GREEN = "#30D158"
CYAN = "#5AC8FA"
PURPLE = "#BF5AF2"
DARK = "#000000"
DARK2 = "#0A0A0A"
DARK3 = "#111111"
BORDER = "#262626"
T2 = "#8A8A8A"
T3 = "#5A5A5A"
IVORY = "#E8E8E8"

NEWS_EXPIRE = 15  # 15 min cache
MAX_FEED_ITEMS = 100

_STRONG_NEGATIVE = [
    "crash", "plunge", "collapse", "fraud", "scam", "default", "bankrupt",
    "insolvency", "probe", "raid", "scandal", "resign", "sebi action",
    "penalty", "banned", "suspended", "downgrade", "slump", "tumble",
    "loss widens", "profit warning", "recall", "halt", "suspend",
]
_STRONG_POSITIVE = [
    "record high", "record profit", "surge", "rally", "beats estimate",
    "beats estimates", "upgrade", "wins order", "wins contract",
    "stake buy", "acquire", "acquisition", "expansion", "breakthrough",
    "outperform", "all-time high", "jumps", "soars", "bags order",
]
_MILD_NEGATIVE = [
    "falls", "declines", "drops", "down", "misses estimate",
    "misses estimates", "cut", "weak", "concern", "delay",
]
_MILD_POSITIVE = [
    "rises", "gains", "up", "beats", "growth", "profit rises",
    "expands", "launch", "partnership", "approval",
]

_SENTIMENT_COLORS = {
    "strong_negative": RED,
    "strong_positive": GREEN,
    "mild_negative": "#FF9500",
    "mild_positive": "#5AC8FA",
    "neutral": BORDER,
}


def _classify_sentiment(title: str) -> str:
    t = title.lower()
    if any(kw in t for kw in _STRONG_NEGATIVE):
        return "strong_negative"
    if any(kw in t for kw in _STRONG_POSITIVE):
        return "strong_positive"
    if any(kw in t for kw in _MILD_NEGATIVE):
        return "mild_negative"
    if any(kw in t for kw in _MILD_POSITIVE):
        return "mild_positive"
    return "neutral"


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_ist() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _format_time(pub_dt: datetime) -> str:
    now = _now_ist()
    diff = now - pub_dt.astimezone(IST)
    secs = int(diff.total_seconds())
    if secs < 60:
        return "now"
    elif secs < 3600:
        return f"{secs // 60}m"
    elif secs < 86400:
        return f"{secs // 3600}h"
    else:
        return pub_dt.astimezone(IST).strftime("%d %b")


def _parse_pub(entry):
    raw = entry.get("published", entry.get("updated", ""))
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


def _fetch_news_for_stock(symbol: str) -> list[dict]:
    today = _today_ist()
    query = symbol.replace("&", "and").replace(" ", "+")
    url = (
        f"https://news.google.com/rss/search?"
        f"q={query}+NSE+India+stock"
        f"&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:10]:
            pub_dt = _parse_pub(entry)
            if not pub_dt:
                continue
            pub_ist = pub_dt.astimezone(IST)
            if pub_ist.strftime("%Y-%m-%d") != today:
                continue
            results.append({
                "symbol": symbol,
                "title": entry.get("title", "No title"),
                "link": entry.get("link", "#"),
                "source": entry.get("source", {}).get("title", "News"),
                "pub_dt": pub_ist,
                "time_str": _format_time(pub_ist),
                "sentiment": _classify_sentiment(entry.get("title", "")),
                "type": "stock",
            })
        return results
    except Exception:
        return []


# Market-critical queries for top-left panel + global feeds
_MACRO_QUERIES = [
    ("RBI monetary policy India", "macro"),
    ("Nifty Sensex BSE market today", "indices"),
    ("FII DII flows India markets", "flows"),
    ("Fed interest rate decision", "global"),
    ("US market S&P Dow Nasdaq", "global"),
    ("Crude oil price today", "commodity"),
    ("Gold silver commodity prices", "commodity"),
    ("Rupee USD exchange rate", "forex"),
    ("Bitcoin Ethereum crypto market", "crypto"),
    ("Earnings results stock market", "results"),
    ("India inflation CPI RBI", "macro"),
    ("US unemployment jobs report", "global"),
    ("ECB European markets news", "global"),
    ("China markets Hang Seng", "global"),
    ("Blockchain tech stocks", "tech"),
]


def _fetch_macro_news() -> list[dict]:
    today = _today_ist()
    results = []
    for query, category in _MACRO_QUERIES:
        q_encoded = query.replace(" ", "+")
        url = (
            f"https://news.google.com/rss/search?"
            f"q={q_encoded}"
            f"&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                pub_dt = _parse_pub(entry)
                if not pub_dt:
                    continue
                pub_ist = pub_dt.astimezone(IST)
                if pub_ist.strftime("%Y-%m-%d") != today:
                    continue
                results.append({
                    "symbol": "MARKET",
                    "title": entry.get("title", "No title"),
                    "link": entry.get("link", "#"),
                    "source": entry.get("source", {}).get("title", "News"),
                    "pub_dt": pub_ist,
                    "time_str": _format_time(pub_ist),
                    "sentiment": _classify_sentiment(entry.get("title", "")),
                    "type": category,
                })
        except Exception:
            continue
    return results


def _midnight_cleanup():
    today = _today_ist()
    if st.session_state.get("_news_date") != today:
        st.session_state["_news_cache"] = {}
        st.session_state["_news_fetched"] = {}
        st.session_state["_news_date"] = today


def _ensure_news_state():
    for k, v in {
        "_news_cache": {},
        "_news_fetched": {},
        "_news_date": _today_ist(),
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


def refresh_news(watchlist: list[str]):
    _midnight_cleanup()
    now = time.time()
    
    # Fetch stock news
    for sym in watchlist:
        last = st.session_state["_news_fetched"].get(sym, 0)
        if now - last > NEWS_EXPIRE * 60:
            articles = _fetch_news_for_stock(sym)
            st.session_state["_news_cache"][sym] = articles
            st.session_state["_news_fetched"][sym] = now
    
    # Fetch macro + global news
    macro_last = st.session_state["_news_fetched"].get("_MACRO_", 0)
    if now - macro_last > NEWS_EXPIRE * 60:
        st.session_state["_news_cache"]["_MACRO_"] = _fetch_macro_news()
        st.session_state["_news_fetched"]["_MACRO_"] = now


def get_news_dot(sym: str) -> str:
    return "1" if st.session_state.get("_news_cache", {}).get(sym) else ""


def _combined_feed(watchlist: list[str]) -> list[dict]:
    cache = st.session_state.get("_news_cache", {})
    seen_links = set()
    combined = []
    
    # Add macro first (highest priority)
    for art in cache.get("_MACRO_", []):
        if art["link"] not in seen_links:
            seen_links.add(art["link"])
            combined.append(art)
    
    # Then add stock news
    for sym in watchlist:
        for art in cache.get(sym, []):
            if art["link"] not in seen_links:
                seen_links.add(art["link"])
                combined.append(art)
    
    combined.sort(key=lambda a: a["pub_dt"], reverse=True)
    return combined[:MAX_FEED_ITEMS]


# Backwards-compatible aliases
news_panel = lambda w: None  # Stub for compatibility
_fetch_news_for_stock = _fetch_news_for_stock
refresh_news = refresh_news
