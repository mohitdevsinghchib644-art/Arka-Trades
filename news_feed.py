"""
news_feed.py — Arka Trades News Intelligence v9.1

A terminal-style news engine designed for the Arka Trades right rail and the
Research > News workspace.

v9.1 goals:
- fast staged refresh instead of fetching a large watchlist on every rerun
- one normalized article schema for every consumer
- Google News RSS as the public baseline feed, with strict timeouts
- event / sentiment / source / importance classification
- recency + relevance + multi-source priority ranking
- near-duplicate headline clustering
- breaking / catalyst / risk views
- security-aware context and watchlist filtering
- optional intraday price-reaction calculation for the active security
- explicit data provenance; no fabricated articles, scores, or market claims
- backwards-compatible public API:
    render_news_rail, news_panel, news_box, refresh_news,
    get_news_dot, _fetch_news_for_stock
"""

from __future__ import annotations

import difflib
import html
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import feedparser
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))

# Terminal palette
GOLD = "#FF9F0A"
DARK = "#000000"
DARK2 = "#090909"
DARK3 = "#111111"
BORDER = "#242424"
T2 = "#8E8E8E"
T3 = "#5C5C5C"
IVORY = "#E8E8E8"
GREEN = "#30D158"
RED = "#FF453A"
BLUE = "#5AC8FA"
PURPLE = "#BF5AF2"
CYAN = "#64D2FF"
MONO = "'JetBrains Mono','Consolas',monospace"

# Performance: the news rail is a narrow context panel, so it does not need
# every watchlist symbol on every rerun.
NEWS_FETCH_TTL = 300
NEWS_EXPIRE_MIN = 5
MAX_SECURITY_REFRESH = 4
MAX_FETCH_WORKERS = 4
MAX_COMBINED_ITEMS = 70
MAX_ARTICLES_PER_FEED = 25
HTTP_TIMEOUT = 7
DEFAULT_DAYS = 1

_EVENT_RULES = {
    "EARNINGS": ["earnings", "results", "quarterly result", "profit", "revenue", "ebitda", "eps", "guidance"],
    "ORDER": ["order", "contract", "bags order", "wins order", "wins contract", "work order", "purchase order", "letter of award"],
    "STAKE": ["stake", "acquire", "acquisition", "buy stake", "sells stake", "buys stake", "block deal", "open offer"],
    "PROMOTER": ["promoter", "promoters", "pledge", "pledged", "insider", "insider buying", "insider selling"],
    "REGULATORY": ["sebi", "rbi", "penalty", "probe", "raid", "regulatory", "show cause", "ban", "banned", "compliance", "notice"],
    "DIVIDEND": ["dividend", "ex-dividend", "record date", "bonus", "split", "buyback"],
    "RATING": ["upgrade", "downgrade", "target price", "rating", "outperform", "underperform", "brokerage", "price target"],
    "CAPEX": ["capex", "capital expenditure", "expansion", "new plant", "capacity", "investment plan", "commissioning"],
    "MANAGEMENT": ["ceo", "cfo", "management", "md & ceo", "appoints", "resigns", "steps down", "outlook", "guidance"],
    "MACRO": ["rbi", "fed", "federal reserve", "inflation", "gdp", "crude", "oil price", "rupee", "dollar", "nifty", "sensex", "fii", "dii", "bond yield", "tariff"],
}

_STRONG_NEGATIVE = [
    "crash", "plunge", "collapse", "fraud", "scam", "default", "bankrupt",
    "insolvency", "probe", "raid", "scandal", "resign", "sebi action",
    "penalty", "banned", "suspended", "downgrade", "slump", "tumble",
    "loss widens", "profit warning", "recall", "misses estimate", "misses estimates",
]
_STRONG_POSITIVE = [
    "record high", "record profit", "surge", "rally", "beats estimate",
    "beats estimates", "upgrade", "wins order", "wins contract", "stake buy",
    "acquire", "acquisition", "expansion", "breakthrough", "outperform",
    "all-time high", "jumps", "soars", "bags order", "profit rises", "raises guidance",
]
_MILD_NEGATIVE = ["falls", "declines", "drops", "down", "cut", "weak", "concern", "delay", "misses", "soft outlook"]
_MILD_POSITIVE = ["rises", "gains", "up", "beats", "growth", "expands", "launch", "partnership", "higher outlook"]

_EVENT_COLORS = {
    "EARNINGS": PURPLE,
    "ORDER": GREEN,
    "STAKE": GOLD,
    "PROMOTER": GOLD,
    "REGULATORY": RED,
    "DIVIDEND": GREEN,
    "RATING": BLUE,
    "CAPEX": CYAN,
    "MANAGEMENT": "#FFD60A",
    "MACRO": GOLD,
    "NEWS": T2,
}
_SENTIMENT_LABELS = {
    "strong_negative": "NEG",
    "strong_positive": "POS",
    "mild_negative": "neg",
    "mild_positive": "pos",
    "neutral": "—",
}
_SENTIMENT_COLORS = {
    "strong_negative": RED,
    "strong_positive": GREEN,
    "mild_negative": f"{RED}99",
    "mild_positive": f"{GREEN}99",
    "neutral": BORDER,
}
_SOURCE_TYPES = {
    "reuters": "NEWS",
    "cnbc": "NEWS",
    "economic times": "NEWS",
    "times of india": "NEWS",
    "moneycontrol": "NEWS",
    "business standard": "NEWS",
    "businessline": "NEWS",
    "mint": "NEWS",
    "ndtv": "NEWS",
    "bloomberg": "NEWS",
    "seeking alpha": "NEWS",
    "nse india": "EXCHANGE",
    "bse india": "EXCHANGE",
    "sebi": "REGULATOR",
    "rbi": "REGULATOR",
}

_MACRO_QUERIES = [
    "RBI monetary policy India",
    "Nifty Sensex Indian market",
    "FII DII flows India",
    "US Fed interest rates stocks",
    "crude oil India markets",
    "India GDP inflation markets",
    "US stock market today",
    "rupee dollar India markets",
]
_SECTOR_QUERY_TERMS = {
    "bank": "Indian banks banking stocks",
    "financial": "Indian financial services stocks",
    "information technology": "Indian IT stocks technology",
    "it": "Indian IT stocks technology",
    "pharmaceutical": "Indian pharma stocks healthcare",
    "healthcare": "Indian pharma stocks healthcare",
    "automobile": "Indian auto stocks automobile",
    "oil & gas": "Indian oil gas energy stocks",
    "oil and gas": "Indian oil gas energy stocks",
    "metals": "Indian metals stocks steel",
    "power": "Indian power stocks electricity",
}

_EVENT_REASON = {
    "EARNINGS": "Earnings / operating performance headline",
    "ORDER": "Commercial order or contract headline",
    "STAKE": "Ownership / acquisition headline",
    "PROMOTER": "Promoter / insider / pledge headline",
    "REGULATORY": "Regulatory, compliance or policy action",
    "DIVIDEND": "Corporate action headline",
    "RATING": "Brokerage / rating / target-price headline",
    "CAPEX": "Capacity / investment / expansion headline",
    "MANAGEMENT": "Management / outlook / leadership headline",
    "MACRO": "Macro or market-wide headline",
    "NEWS": "General company / market headline",
}


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_ist() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _format_time(pub_dt: datetime) -> str:
    now = _now_ist()
    diff = int((now - pub_dt.astimezone(IST)).total_seconds())
    if diff < 0 or diff < 60:
        return "now"
    if diff < 3600:
        return f"{diff // 60}m"
    if diff < 86400:
        return f"{diff // 3600}h"
    return pub_dt.astimezone(IST).strftime("%d %b")


def _parse_pub(entry) -> datetime | None:
    raw = entry.get("published", entry.get("updated", ""))
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST)
        except Exception:
            pass
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(IST)
        except Exception:
            pass
    return None


def _classify_sentiment(title: str) -> str:
    t = (title or "").lower()
    if any(kw in t for kw in _STRONG_NEGATIVE):
        return "strong_negative"
    if any(kw in t for kw in _STRONG_POSITIVE):
        return "strong_positive"
    if any(kw in t for kw in _MILD_NEGATIVE):
        return "mild_negative"
    if any(kw in t for kw in _MILD_POSITIVE):
        return "mild_positive"
    return "neutral"


def _classify_event(title: str, is_macro: bool = False) -> str:
    t = (title or "").lower()
    order = ["REGULATORY", "ORDER", "STAKE", "PROMOTER", "DIVIDEND", "EARNINGS", "CAPEX", "MANAGEMENT", "RATING", "MACRO"]
    for event in order:
        if any(kw in t for kw in _EVENT_RULES[event]):
            return event
    return "MACRO" if is_macro else "NEWS"


def _importance(title: str, event: str, sentiment: str) -> int:
    score = 1
    if event in {"EARNINGS", "ORDER", "STAKE", "PROMOTER", "REGULATORY", "DIVIDEND"}:
        score += 2
    elif event in {"CAPEX", "MANAGEMENT", "RATING"}:
        score += 1
    if sentiment.startswith("strong"):
        score += 1
    t = (title or "").lower()
    if any(x in t for x in ["breaking", "just in", "exclusive", "flash"]):
        score += 1
    return min(score, 5)


def _source_type(source: str, link: str = "") -> str:
    s = (source or "").lower()
    for key, typ in _SOURCE_TYPES.items():
        if key in s:
            return typ
    host = urlparse(link or "").netloc.lower()
    if "nseindia" in host:
        return "EXCHANGE"
    if "bseindia" in host:
        return "EXCHANGE"
    if "sebi" in host:
        return "REGULATOR"
    if "rbi" in host:
        return "REGULATOR"
    return "NEWS"


def _normalise_title(title: str) -> str:
    t = re.sub(r"\[[^\]]+\]", " ", title or "")
    t = re.sub(r"\b(live updates?|live blog|breaking|watch)\b", " ", t, flags=re.I)
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _token_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a.split()), set(b.split())
    jaccard = len(sa & sb) / max(1, len(sa | sb))
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jaccard, seq * 0.9)


def _extract_source(entry, title: str) -> tuple[str, str]:
    source_obj = entry.get("source", {}) or {}
    source = source_obj.get("title") if isinstance(source_obj, dict) else str(source_obj)
    clean_title = title.strip()
    if source:
        return str(source).strip(), clean_title
    parts = re.split(r"\s+-\s+", clean_title)
    if len(parts) >= 2:
        maybe_source = parts[-1].strip()
        if 1 < len(maybe_source) <= 60:
            return maybe_source, " - ".join(parts[:-1]).strip()
    return "News", clean_title


def _build_article(entry, symbol: str, is_macro: bool = False, query_kind: str = "company") -> dict | None:
    pub_dt = _parse_pub(entry)
    if not pub_dt:
        return None
    raw_title = entry.get("title", "No title") or "No title"
    source, title = _extract_source(entry, raw_title)
    link = entry.get("link", "") or ""
    event = _classify_event(title, is_macro=is_macro)
    sentiment = _classify_sentiment(title)
    return {
        "symbol": symbol,
        "title": title,
        "link": link,
        "source": source or "News",
        "pub_dt": pub_dt,
        "time_str": _format_time(pub_dt),
        "sentiment": sentiment,
        "sentiment_label": _SENTIMENT_LABELS[sentiment],
        "event": event,
        "event_reason": _EVENT_REASON.get(event, "General news headline"),
        "importance": _importance(title, event, sentiment),
        "source_type": _source_type(source, link),
        "query_kind": query_kind,
        "cluster_key": _normalise_title(title),
        "is_new": False,
        "is_breaking": False,
        "priority": 0,
        "relevance": 0,
        "source_count": 1,
        "sources": [source or "News"],
    }


def _rss_url(query: str, days: int = 1) -> str:
    q = f"{query} when:{max(1, int(days))}d"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"


@st.cache_data(ttl=NEWS_FETCH_TTL, show_spinner=False)
def _fetch_rss_cached(url: str) -> list[dict]:
    """Fetch RSS through requests so the connection has a hard timeout."""
    try:
        response = requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 Arka-Trades-News/9.1"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        rows = []
        for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
            rows.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "updated": entry.get("updated", ""),
                "published_parsed": entry.get("published_parsed"),
                "updated_parsed": entry.get("updated_parsed"),
                "source": entry.get("source", {}),
            })
        return rows
    except Exception:
        return []


@st.cache_data(ttl=NEWS_FETCH_TTL, show_spinner=False)
def _fetch_query_cached(
    query: str,
    symbol: str,
    is_macro: bool = False,
    query_kind: str = "company",
    days: int = 1,
) -> list[dict]:
    cutoff = _now_ist() - timedelta(days=days)
    out = []
    for entry in _fetch_rss_cached(_rss_url(query, days)):
        article = _build_article(entry, symbol, is_macro=is_macro, query_kind=query_kind)
        if article and article["pub_dt"] >= cutoff:
            out.append(article)
    return out


def _fetch_query(query: str, symbol: str, is_macro: bool = False, query_kind: str = "company", days: int = 1) -> list[dict]:
    return _fetch_query_cached(query, symbol, is_macro, query_kind, days)


def _fetch_news_for_stock(symbol: str, days: int = 1) -> list[dict]:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return []
    # One broad query keeps latency bounded. It is deliberately event-rich so
    # the same feed supports earnings/orders/ownership/regulatory stories.
    query = f'"{symbol}" India stock results earnings order promoter stake'
    return _dedupe_articles(_fetch_query_cached(query, symbol, query_kind="company", days=days))


def _fetch_macro_news(days: int = 1) -> list[dict]:
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as ex:
        futures = [ex.submit(_fetch_query_cached, q, "MACRO", True, "macro", days) for q in _MACRO_QUERIES]
        for f in as_completed(futures):
            try:
                results.extend(f.result())
            except Exception:
                pass
    return _dedupe_articles(results)


@st.cache_data(ttl=NEWS_FETCH_TTL, show_spinner=False)
def _fetch_sector_news(sector: str, days: int = 1) -> list[dict]:
    sector = (sector or "").strip()
    if not sector:
        return []
    query = _SECTOR_QUERY_TERMS.get(sector.lower(), f"India {sector} stocks")
    return _fetch_query_cached(query, "SECTOR", query_kind="sector", days=days)


def _dedupe_articles(items: list[dict]) -> list[dict]:
    """Deduplicate links and cluster highly similar headlines into one story."""
    by_link: dict[str, dict] = {}
    for item in items:
        link = item.get("link") or item.get("title")
        if link not in by_link:
            by_link[link] = item
            continue
        old = by_link[link]
        if item.get("importance", 0) > old.get("importance", 0):
            by_link[link] = item

    raw = list(by_link.values())
    raw.sort(key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
    clusters: list[list[dict]] = []
    representatives: list[str] = []

    # Compare only recent headlines; this avoids quadratic work over the full feed.
    for item in raw:
        key = item.get("cluster_key") or _normalise_title(item.get("title", ""))
        found = None
        for idx, rep in enumerate(representatives[-80:]):
            if _token_similarity(key, rep) >= 0.88:
                found = len(representatives) - len(representatives[-80:]) + idx
                break
        if found is None:
            representatives.append(key)
            clusters.append([item])
        else:
            clusters[found].append(item)

    clustered: list[dict] = []
    for group in clusters:
        group.sort(key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
        lead = dict(group[0])
        lead["source_count"] = len(group)
        lead["sources"] = list(dict.fromkeys(str(x.get("source", "News")) for x in group))[:6]
        clustered.append(lead)

    clustered.sort(key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
    return clustered


def _minutes_old(article: dict) -> float:
    try:
        return max(0.0, (_now_ist() - article["pub_dt"].astimezone(IST)).total_seconds() / 60.0)
    except Exception:
        return 999999.0


def _enrich_priority(items: list[dict], current_security: str | None = None) -> list[dict]:
    current = (current_security or "").strip().upper()
    for art in items:
        age = _minutes_old(art)
        recency = max(0, 30 - int(age // 20))
        multi_source = min(15, max(0, int(art.get("source_count", 1)) - 1) * 5)
        source_bonus = 5 if art.get("source_type") in {"EXCHANGE", "REGULATOR"} else 0
        context_bonus = 20 if current and art.get("symbol") == current else 0
        importance = int(art.get("importance", 1)) * 10
        score = min(100, importance + recency + multi_source + source_bonus + context_bonus)
        art["priority"] = score
        art["relevance"] = min(100, 40 + context_bonus + multi_source + recency)
        art["is_breaking"] = age <= 75 and (art.get("importance", 1) >= 4 or art.get("source_count", 1) > 1)
    return items


def _midnight_cleanup():
    today = _today_ist()
    if st.session_state.get("_news_date") != today:
        st.session_state["_news_cache"] = {}
        st.session_state["_news_fetched"] = {}
        st.session_state["_news_seen_keys"] = set()
        st.session_state["_news_date"] = today


def _ensure_news_state():
    _midnight_cleanup()
    defaults = {
        "_news_cache": {},
        "_news_fetched": {},
        "_news_seen_keys": set(),
        "_news_days": 1,
        "_news_category": "ALL",
        "_news_search": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            if isinstance(value, dict):
                st.session_state[key] = dict(value)
            elif isinstance(value, set):
                st.session_state[key] = set(value)
            else:
                st.session_state[key] = value


def _mark_new(items: list[dict]) -> list[dict]:
    seen = st.session_state.setdefault("_news_seen_keys", set())
    for item in items:
        key = item.get("link") or item.get("cluster_key") or item.get("title")
        item["is_new"] = key not in seen
    for item in items[:120]:
        key = item.get("link") or item.get("cluster_key") or item.get("title")
        if key:
            seen.add(key)
    if len(seen) > 2500:
        st.session_state["_news_seen_keys"] = set(list(seen)[-1200:])
    return items


def _rotating_batch(symbols: list[str], batch_size: int, current: str | None = None) -> list[str]:
    """Pick up to batch_size symbols to actively refresh this cycle.

    Without this, refresh_news always took the first `batch_size` symbols
    of the watchlist — meaning whichever stocks happened to be first in
    the uploaded list permanently owned all per-stock news coverage, and
    everything after position `batch_size` never got fetched, ever, with
    no indication that was happening. This rotates the batch by a time
    epoch matching NEWS_EXPIRE_MIN, so a different slice of the watchlist
    gets refreshed each cycle and coverage works its way through the
    whole list over time. The active security (if any) is always pinned
    into every batch, since it should never lose coverage to rotation.
    """
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return []
    current = (current or "").strip().upper()
    pinned = [current] if current and current in symbols else []
    rest = [s for s in symbols if s not in pinned]
    if not rest:
        return pinned[:batch_size]
    slots = max(1, batch_size - len(pinned))
    if len(rest) <= slots:
        return pinned + rest
    n_batches = -(-len(rest) // slots)  # ceil division
    epoch = int(time.time() // (NEWS_EXPIRE_MIN * 60))
    offset = (epoch % n_batches) * slots
    batch = rest[offset:offset + slots]
    if len(batch) < slots:
        batch += rest[: slots - len(batch)]  # wrap around to the start
    return pinned + batch


def refresh_news(
    watchlist: list[str],
    current_security: str | None = None,
    sector: str | None = None,
    days: int = 1,
):
    """Refresh a small prioritized news universe; the UI remains responsive."""
    _ensure_news_state()
    days = max(1, min(int(days or 1), 30))
    now = time.time()

    all_symbols = list(dict.fromkeys([str(s).strip().upper() for s in (watchlist or []) if str(s).strip()]))
    current = (current_security or "").strip().upper()
    symbols = _rotating_batch(all_symbols, MAX_SECURITY_REFRESH, current)

    jobs = []
    for sym in symbols:
        key = f"SEC:{sym}:{days}"
        last = st.session_state["_news_fetched"].get(key, 0)
        if now - last >= NEWS_EXPIRE_MIN * 60:
            jobs.append((key, sym))

    if jobs:
        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as ex:
            future_map = {
                ex.submit(_fetch_news_for_stock, sym, days): (key, sym)
                for key, sym in jobs
            }
            for future in as_completed(future_map):
                key, _sym = future_map[future]
                try:
                    st.session_state["_news_cache"][key] = future.result()
                except Exception:
                    st.session_state["_news_cache"][key] = []
                st.session_state["_news_fetched"][key] = now

    macro_key = f"MACRO:{days}"
    macro_last = st.session_state["_news_fetched"].get(macro_key, 0)
    if now - macro_last >= NEWS_EXPIRE_MIN * 60:
        st.session_state["_news_cache"][macro_key] = _fetch_macro_news(days)
        st.session_state["_news_fetched"][macro_key] = now

    if sector:
        sector_key = f"SECTOR:{sector}:{days}"
        sector_last = st.session_state["_news_fetched"].get(sector_key, 0)
        if now - sector_last >= NEWS_EXPIRE_MIN * 60:
            st.session_state["_news_cache"][sector_key] = _fetch_sector_news(sector, days)
            st.session_state["_news_fetched"][sector_key] = now


def get_news_dot(sym: str) -> str:
    _ensure_news_state()
    symbol = (sym or "").strip().upper()
    for key, items in st.session_state.get("_news_cache", {}).items():
        if key.startswith(f"SEC:{symbol}:") and items:
            return "1"
    return ""


def _combined_feed(
    watchlist: list[str],
    macro_only: bool = False,
    current_security: str | None = None,
    sector: str | None = None,
    days: int = 1,
) -> list[dict]:
    cache = st.session_state.get("_news_cache", {})
    symbols = list(dict.fromkeys([str(s).strip().upper() for s in (watchlist or []) if str(s).strip()]))
    current = (current_security or "").strip().upper()
    if current:
        symbols = [current] + [s for s in symbols if s != current]

    # FIX: this used to slice to [:MAX_SECURITY_REFRESH] here too, which
    # meant even a symbol that WAS fetched (in an earlier rotation cycle,
    # see _rotating_batch) and sitting right there in cache would never be
    # read back — coverage was capped on both the fetch side and the read
    # side. Reading cache is just dict lookups, free either way, so the
    # only side that needs the cap is the one making network calls.
    keys = []
    if not macro_only:
        keys.extend(f"SEC:{s}:{days}" for s in symbols)
        if sector:
            keys.append(f"SECTOR:{sector}:{days}")
    keys.append(f"MACRO:{days}")

    combined: list[dict] = []
    for key in keys:
        combined.extend(cache.get(key, []))
    combined = _dedupe_articles(combined)
    combined = _enrich_priority(combined, current)
    combined.sort(key=lambda x: (x.get("priority", 0), x.get("pub_dt", datetime.min.replace(tzinfo=IST))), reverse=True)
    return combined[:MAX_COMBINED_ITEMS]


def get_security_news(symbol: str, days: int = 7) -> list[dict]:
    """Public helper for Research > News."""
    _ensure_news_state()
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return []
    key = f"SEC:{symbol}:{int(days)}"
    now = time.time()
    last = st.session_state["_news_fetched"].get(key, 0)
    if now - last >= NEWS_EXPIRE_MIN * 60:
        try:
            st.session_state["_news_cache"][key] = _fetch_news_for_stock(symbol, int(days))
        except Exception:
            st.session_state["_news_cache"][key] = []
        st.session_state["_news_fetched"][key] = now
    items = _dedupe_articles(st.session_state["_news_cache"].get(key, []))
    return _enrich_priority(items, symbol)


def _category_filter(items: list[dict], category: str, current_security: str | None = None) -> list[dict]:
    category = (category or "ALL").upper()
    current = (current_security or "").strip().upper()
    if category == "ALL":
        return items
    if category == "WATCHLIST":
        return [x for x in items if x.get("symbol") not in {"MACRO", "SECTOR"}]
    if category == "SECURITY":
        return [x for x in items if current and x.get("symbol") == current]
    if category == "SECTOR":
        return [x for x in items if x.get("query_kind") == "sector" or x.get("symbol") == "SECTOR"]
    if category == "MACRO":
        return [x for x in items if x.get("symbol") == "MACRO" or x.get("event") == "MACRO"]
    if category == "EARNINGS":
        return [x for x in items if x.get("event") == "EARNINGS"]
    if category == "CORPORATE":
        return [x for x in items if x.get("event") in {"ORDER", "STAKE", "PROMOTER", "DIVIDEND", "CAPEX", "MANAGEMENT"}]
    if category == "REGULATORY":
        return [x for x in items if x.get("event") == "REGULATORY"]
    if category == "RATING":
        return [x for x in items if x.get("event") == "RATING"]
    if category == "POSITIVE":
        return [x for x in items if x.get("sentiment") in {"strong_positive", "mild_positive"}]
    if category == "NEGATIVE":
        return [x for x in items if x.get("sentiment") in {"strong_negative", "mild_negative"}]
    return [x for x in items if x.get("event") == category]


def _search_filter(items: list[dict], query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return items
    return [
        item for item in items
        if q in item.get("title", "").lower()
        or q in item.get("source", "").lower()
        or q in item.get("event", "").lower()
        or q in item.get("symbol", "").lower()
    ]


@st.cache_data(ttl=300, show_spinner=False)
def _intraday_closes(symbol: str):
    """Fetch one compact intraday series per active security."""
    try:
        sym = (symbol or "").strip().upper()
        if not sym or sym in {"MACRO", "SECTOR"}:
            return []
        ticker = sym if "." in sym else f"{sym}.NS"
        df = yf.download(
            ticker,
            period="1d",
            interval="5m",
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if df is None or df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if "Close" not in df.columns:
            return []
        series = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if series.empty:
            return []
        idx = pd.to_datetime(series.index)
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC").tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)
        return [(ts.isoformat(), float(v)) for ts, v in zip(idx, series.to_numpy())]
    except Exception:
        return []


def _intraday_price_impact(symbol: str, pub_dt_iso: str):
    try:
        points = _intraday_closes(symbol)
        if not points:
            return None
        dt = datetime.fromisoformat(pub_dt_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        target = dt.astimezone(IST)
        after = [
            (parsed_ts, price)
            for ts, price in points
            for parsed_ts in (datetime.fromisoformat(ts),)
            if parsed_ts >= target
        ]
        if not after:
            return None
        start = float(after[0][1])
        end = float(points[-1][1])
        if start <= 0:
            return None
        return {"pct": (end / start - 1) * 100, "start": start, "end": end}
    except Exception:
        return None


def _impact_badge(article: dict) -> str:
    symbol = article.get("symbol")
    if symbol in {"MACRO", "SECTOR", None, ""}:
        return ""
    impact = _intraday_price_impact(symbol, article["pub_dt"].isoformat())
    if not impact:
        return ""
    pct = float(impact["pct"])
    c = GREEN if pct >= 0 else RED
    sign = "+" if pct >= 0 else ""
    return f'<span style="color:{c};">{sign}{pct:.2f}% since headline</span>'


def _safe_href(url: str) -> str:
    if not url:
        return "#"
    p = urlparse(url)
    if p.scheme in {"http", "https"}:
        return html.escape(url, quote=True)
    return "#"


def _priority_badge(priority: int) -> tuple[str, str]:
    if priority >= 70:
        return "P1", RED
    if priority >= 45:
        return "P2", GOLD
    return "P3", T2


def _render_story(art: dict, show_impact: bool = False):
    sentiment = art.get("sentiment", "neutral")
    accent = _SENTIMENT_COLORS.get(sentiment, BORDER)
    event = str(art.get("event", "NEWS"))
    event_color = _EVENT_COLORS.get(event, T2)
    source = html.escape(str(art.get("source", "News")))
    title = html.escape(str(art.get("title", "No title")))
    href = _safe_href(art.get("link", ""))
    symbol = art.get("symbol", "NEWS")
    tag = "MACRO" if symbol == "MACRO" else ("SECTOR" if symbol == "SECTOR" else str(symbol))
    tag_color = GOLD if symbol in {"MACRO", "SECTOR"} else BLUE
    ptxt, pcolor = _priority_badge(int(art.get("priority", 0)))
    source_count = int(art.get("source_count", 1) or 1)
    sources_html = f" · {source_count} sources" if source_count > 1 else ""
    new_html = '<span style="color:#FF453A;font-weight:800;">NEW</span> · ' if art.get("is_new") else ""
    impact_html = _impact_badge(art) if show_impact else ""
    impact_block = f'<span style="margin-left:8px;">{impact_html}</span>' if impact_html else ""
    breaking_html = '<span style="color:#FF453A;font-weight:800;">BREAK</span> · ' if art.get("is_breaking") else ""
    reason = html.escape(str(art.get("event_reason", "General headline")))

    return f'''
<a href="{href}" target="_blank" style="text-decoration:none;">
  <div style="border-left:2px solid {accent};padding:8px 0 8px 10px;margin-bottom:7px;background:linear-gradient(90deg,#0b0b0b,transparent);">
    <div style="font:800 9px {MONO};letter-spacing:.7px;margin-bottom:3px;">
      <span style="color:{pcolor};">{ptxt}</span> ·
      <span style="color:{event_color};">{html.escape(event)}</span> ·
      <span style="color:{T3};">IMP {art.get('importance', 1)}/5</span>
    </div>
    <div style="font:600 12px/1.4 Inter,Arial,sans-serif;color:{IVORY};">{title}</div>
    <div style="font:700 8.5px {MONO};color:{T3};margin-top:4px;">{reason}</div>
    <div style="font:9px {MONO};color:{T2};margin-top:4px;">
      {new_html}{breaking_html}<span style="color:{tag_color};font-weight:700;">{html.escape(tag)}</span> · {source}{sources_html} · {art.get('time_str','—')} ·
      <span style="color:{accent};">{html.escape(art.get('sentiment_label','—'))}</span>{impact_block}
    </div>
  </div>
</a>'''


def _render_rows(items: list[dict], show_tag: bool = True, show_impact: bool = False, impact_symbols: set[str] | None = None):
    if not items:
        st.markdown(
            f'<div style="font:11px {MONO};color:{T2};padding:14px 2px;line-height:1.5;">No matching news in the selected window.</div>',
            unsafe_allow_html=True,
        )
        return

    marked = _mark_new(items)
    html_rows = []
    # FIX: an empty impact_symbols set means "no active security to scope
    # to" — that must mean NO impact badges, not badges for everyone. The
    # previous `not active_symbols or ...` read the empty case backwards,
    # so every article got an intraday yf.download() triggered for it
    # whenever no security was loaded (which, before the render_news_rail
    # call site was wired up, was always).
    active_symbols = impact_symbols or set()
    for art in marked:
        should_impact = show_impact and bool(active_symbols) and art.get("symbol") in active_symbols
        html_rows.append(_render_story(art, show_impact=should_impact))
    st.markdown("".join(html_rows), unsafe_allow_html=True)


def _render_controls():
    c1, c2 = st.columns([1.0, 1.0])
    with c1:
        days_label = st.selectbox(
            "WINDOW",
            ["24H", "7D", "30D"],
            index={1: 0, 7: 1, 30: 2}.get(int(st.session_state.get("_news_days", 1) or 1), 0),
            key="news_window_v91",
            label_visibility="collapsed",
        )
    with c2:
        search = st.text_input(
            "NEWS SEARCH",
            value=st.session_state.get("_news_search", ""),
            placeholder="search…",
            key="news_search_v91",
            label_visibility="collapsed",
        )
    st.session_state["_news_days"] = {"24H": 1, "7D": 7, "30D": 30}[days_label]
    st.session_state["_news_search"] = search


def _coverage_note(watchlist: list[str], current: str, days: int, T_mono: str = MONO) -> str:
    """A one-line, honest readout of how much of the watchlist actually has
    per-stock news right now vs. macro-only coverage — so the rotating
    4-symbol refresh (see _rotating_batch) isn't a silent mystery."""
    symbols = list(dict.fromkeys([str(s).strip().upper() for s in (watchlist or []) if str(s).strip()]))
    if not symbols:
        return ""
    fetched = st.session_state.get("_news_fetched", {})
    covered = sum(1 for s in symbols if fetched.get(f"SEC:{s}:{days}", 0) > 0)
    total = len(symbols)
    if covered >= total:
        return ""
    return (
        f'<div style="font:8px {T_mono};color:{T3};margin:2px 0 6px;">'
        f'COVERAGE · {covered} of {total} watchlist symbols refreshed so far'
        f' · rotates {MAX_SECURITY_REFRESH} at a time every {NEWS_EXPIRE_MIN} min'
        f'{" · " + current + " always included" if current else ""}</div>'
    )


def render_news_rail(
    watchlist: list[str],
    label: str = "WATCHLIST",
    current_security: str | None = None,
    sector: str | None = None,
):
    """Render the v9.1 right rail: prioritized, context-aware market news."""
    _ensure_news_state()

    # Controls FIRST. This prevents a 7D/30D click from rendering the old
    # window once before the selected value is stored in session state.
    _render_controls()
    days = int(st.session_state.get("_news_days", 1) or 1)

    refresh_news(watchlist, current_security=current_security, sector=sector, days=days)
    combined = _combined_feed(
        watchlist,
        current_security=current_security,
        sector=sector,
        days=days,
    )
    combined = _mark_new(combined)

    current = (current_security or "").strip().upper()
    search_query = st.session_state.get("_news_search", "")

    coverage_html = _coverage_note(watchlist, current, days)
    if coverage_html:
        st.markdown(coverage_html, unsafe_allow_html=True)

    options = ["ALL", "WATCHLIST"]
    if current:
        options.insert(1, "SECURITY")
    if sector:
        options.append("SECTOR")
    options += ["MACRO", "EARNINGS", "CORPORATE", "REGULATORY", "RATING", "POSITIVE", "NEGATIVE"]
    options = list(dict.fromkeys(options))
    default = st.session_state.get("_news_category", "ALL")
    if default not in options:
        default = "ALL"
    category = st.selectbox(
        "FILTER",
        options,
        index=options.index(default),
        key="news_category_v91",
        label_visibility="collapsed",
    )
    st.session_state["_news_category"] = category

    filtered = _search_filter(_category_filter(combined, category, current), search_query)
    catalysts = [x for x in combined if x.get("sentiment") in {"strong_positive", "mild_positive"} or x.get("event") in {"ORDER", "CAPEX", "DIVIDEND", "RATING"}]
    risks = [x for x in combined if x.get("sentiment") in {"strong_negative", "mild_negative"} or x.get("event") in {"REGULATORY", "PROMOTER"}]
    breaking = [x for x in combined if x.get("is_breaking") or x.get("importance", 0) >= 4]

    # Compact desk metrics make the rail useful without turning it into a card dashboard.
    story_count = len(combined)
    source_count = len({str(x.get("source", "News")) for x in combined})
    new_count = sum(bool(x.get("is_new")) for x in combined)
    event_counts = Counter(x.get("event", "NEWS") for x in combined)
    st.markdown(
        f'''<div style="border-top:1px solid {BORDER};border-bottom:1px solid {BORDER};padding:7px 0;margin:8px 0 6px;">
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;text-align:center;font:8px {MONO};">
          <div><div style="color:{GOLD};font-weight:800;">STORIES</div><div style="color:{IVORY};font-size:12px;">{story_count}</div></div>
          <div><div style="color:{RED};font-weight:800;">NEW</div><div style="color:{IVORY};font-size:12px;">{new_count}</div></div>
          <div><div style="color:{GREEN};font-weight:800;">CATALYST</div><div style="color:{IVORY};font-size:12px;">{len(catalysts)}</div></div>
          <div><div style="color:{BLUE};font-weight:800;">SOURCES</div><div style="color:{IVORY};font-size:12px;">{source_count}</div></div>
        </div>
        </div>''',
        unsafe_allow_html=True,
    )

    # A tiny event tape gives the user a quick read on what is driving today's rail.
    tape = " · ".join(f"{k} {v}" for k, v in event_counts.most_common(4))
    if tape:
        st.markdown(f'<div style="font:8px {MONO};color:{T3};margin:3px 0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">TAPE · {html.escape(tape)}</div>', unsafe_allow_html=True)

    tab_top, tab_break, tab_cat, tab_risk = st.tabs(["TOP", "BREAK", "CATALYST", "RISK"])
    impact_symbols = {current} if current else set()
    with tab_top:
        _render_rows(filtered[:24], show_tag=True, show_impact=True, impact_symbols=impact_symbols)
    with tab_break:
        _render_rows(_search_filter(_category_filter(breaking, category, current), search_query)[:20], show_tag=True, show_impact=True, impact_symbols=impact_symbols)
    with tab_cat:
        _render_rows(_search_filter(_category_filter(catalysts, category, current), search_query)[:20], show_tag=True, show_impact=True, impact_symbols=impact_symbols)
    with tab_risk:
        _render_rows(_search_filter(_category_filter(risks, category, current), search_query)[:20], show_tag=True, show_impact=True, impact_symbols=impact_symbols)


news_panel = render_news_rail
news_box = render_news_rail
