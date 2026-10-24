"""
news_feed.py — Arka Trades News Intelligence v9.2

A terminal-style news engine for the Arka Trades right rail, the Research > News
workspace and the Portfolio news light.

v9.2 fixes (on top of v9.1)
---------------------------
* TOP / BREAK / CATALYST / RISK now show DIFFERENT stories:
    TOP       highest-priority stories overall
    BREAK     everything from the last 3 hours, newest first
    CATALYST  company-specific positive news / orders / capex / stake / dividend
    RISK      negative-sentiment or regulatory stories (market-wide included)
  Root cause: keywords were matched as raw substrings ("up" matched "support",
  "group", "update"; "ban" matched "bank"; "eps" matched "steps"), so almost
  every headline was tagged positive/regulatory and every tab got the same
  list. All keywords now match on word boundaries.
* _fetch_news_for_stock() accepts company=... (used by the Portfolio page). With a
  company name the query is more precise and results are filtered so a yellow light
  only appears for headlines that really name the company or its symbol.
* Macro headlines are capped so they cannot drown company news.
* The NEW badge actually shows now (it was being reset before rendering).
* Refreshes up to 8 symbols per cycle (holdings first), rotating through the rest.

Public API (unchanged): render_news_rail, news_panel, news_box, refresh_news,
get_news_dot, get_security_news, _fetch_news_for_stock
"""

from __future__ import annotations

import difflib
import html
import re
import time
from collections import Counter
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

NEWS_FETCH_TTL = 300
NEWS_EXPIRE_MIN = 5
MAX_SECURITY_REFRESH = 8
MAX_FETCH_WORKERS = 6
MAX_COMBINED_ITEMS = 80
MAX_MACRO_ITEMS = 18
MAX_ARTICLES_PER_FEED = 25
HTTP_TIMEOUT = 7
DEFAULT_DAYS = 1
BREAK_WINDOW_MIN = 180

_EVENT_RULES = {
    "EARNINGS": ["earnings", "results", "quarterly result", "profit", "revenue", "ebitda", "eps", "guidance"],
    "ORDER": ["order", "contract", "bags order", "wins order", "wins contract", "work order", "purchase order", "letter of award"],
    "STAKE": ["stake", "acquire", "acquisition", "buy stake", "sells stake", "buys stake", "block deal", "open offer"],
    "PROMOTER": ["promoter", "promoters", "pledge", "pledged", "insider", "insider buying", "insider selling"],
    "REGULATORY": ["sebi", "rbi", "penalty", "probe", "raid", "regulatory", "show cause", "ban", "banned", "compliance", "notice"],
    "DIVIDEND": ["dividend", "ex-dividend", "record date", "bonus", "split", "buyback"],
    "RATING": ["upgrade", "downgrade", "target price", "rating", "outperform", "underperform", "brokerage", "price target"],
    "CAPEX": ["capex", "capital expenditure", "expansion", "new plant", "capacity", "investment plan", "commissioning"],
    "MANAGEMENT": ["ceo", "cfo", "management", "md & ceo", "appoints", "resigns", "steps down", "outlook"],
    "MACRO": ["rbi", "fed", "federal reserve", "inflation", "gdp", "crude", "oil price", "rupee", "dollar", "nifty", "sensex", "fii", "dii", "bond yield", "tariff"],
}

_STRONG_NEGATIVE = [
    "crash", "plunge", "collapse", "fraud", "scam", "default", "bankrupt",
    "insolvency", "probe", "raid", "scandal", "resign", "steps down", "sebi action",
    "penalty", "banned", "suspended", "downgrade", "slump", "tumble",
    "loss widens", "profit warning", "recall", "misses estimate", "misses estimates",
]
_STRONG_POSITIVE = [
    "record high", "record profit", "surge", "rally", "beats estimate",
    "beats estimates", "upgrade", "wins order", "wins contract", "stake buy",
    "acquire", "acquisition", "expansion", "breakthrough", "outperform",
    "all-time high", "jumps", "soars", "bags order", "profit rises", "raises guidance",
]
# Ambiguous short words ("up", "down", "cut") were removed on purpose.
_MILD_NEGATIVE = ["falls", "fall", "declines", "declined", "drops", "dips", "slips", "slides", "weak", "weakens",
                  "concern", "concerns", "delay", "delayed", "misses", "soft outlook"]
_MILD_POSITIVE = ["rises", "gains", "climbs", "beats", "growth", "expands", "launch", "launches", "partnership",
                  "higher outlook", "improves", "strong demand"]


def _kw_regex(words) -> re.Pattern:
    """Word-boundary keyword matcher. Short tokens (<=4 chars) must match the whole word;
    longer ones may carry a suffix (resign -> resigns, probe -> probes)."""
    parts = []
    for w in sorted(set(words), key=len, reverse=True):
        esc = re.escape(w)
        parts.append(rf"(?<![a-z0-9]){esc}(?![a-z0-9])" if len(w) <= 4 else rf"(?<![a-z0-9]){esc}")
    return re.compile("|".join(parts))


_EVENT_RE = {k: _kw_regex(v) for k, v in _EVENT_RULES.items()}
_SENT_RE = {
    "strong_negative": _kw_regex(_STRONG_NEGATIVE),
    "strong_positive": _kw_regex(_STRONG_POSITIVE),
    "mild_negative": _kw_regex(_MILD_NEGATIVE),
    "mild_positive": _kw_regex(_MILD_POSITIVE),
}

_EVENT_COLORS = {
    "EARNINGS": PURPLE, "ORDER": GREEN, "STAKE": GOLD, "PROMOTER": GOLD, "REGULATORY": RED,
    "DIVIDEND": GREEN, "RATING": BLUE, "CAPEX": CYAN, "MANAGEMENT": "#FFD60A", "MACRO": GOLD, "NEWS": T2,
}
_SENTIMENT_LABELS = {"strong_negative": "NEG", "strong_positive": "POS", "mild_negative": "neg", "mild_positive": "pos", "neutral": "—"}
_SENTIMENT_COLORS = {"strong_negative": RED, "strong_positive": GREEN, "mild_negative": f"{RED}99",
                     "mild_positive": f"{GREEN}99", "neutral": BORDER}
_SOURCE_TYPES = {
    "reuters": "NEWS", "cnbc": "NEWS", "economic times": "NEWS", "times of india": "NEWS", "moneycontrol": "NEWS",
    "business standard": "NEWS", "businessline": "NEWS", "mint": "NEWS", "ndtv": "NEWS", "bloomberg": "NEWS",
    "seeking alpha": "NEWS", "nse india": "EXCHANGE", "bse india": "EXCHANGE", "sebi": "REGULATOR", "rbi": "REGULATOR",
}

_MACRO_QUERIES = [
    "RBI monetary policy India", "Nifty Sensex Indian market", "FII DII flows India", "US Fed interest rates stocks",
    "crude oil India markets", "India GDP inflation markets", "US stock market today", "rupee dollar India markets",
]
_SECTOR_QUERY_TERMS = {
    "bank": "Indian banks banking stocks", "financial": "Indian financial services stocks",
    "information technology": "Indian IT stocks technology", "it": "Indian IT stocks technology",
    "pharmaceutical": "Indian pharma stocks healthcare", "healthcare": "Indian pharma stocks healthcare",
    "automobile": "Indian auto stocks automobile", "oil & gas": "Indian oil gas energy stocks",
    "oil and gas": "Indian oil gas energy stocks", "metals": "Indian metals stocks steel", "power": "Indian power stocks electricity",
}
_EVENT_REASON = {
    "EARNINGS": "Earnings / operating performance headline", "ORDER": "Commercial order or contract headline",
    "STAKE": "Ownership / acquisition headline", "PROMOTER": "Promoter / insider / pledge headline",
    "REGULATORY": "Regulatory, compliance or policy action", "DIVIDEND": "Corporate action headline",
    "RATING": "Brokerage / rating / target-price headline", "CAPEX": "Capacity / investment / expansion headline",
    "MANAGEMENT": "Management / outlook / leadership headline", "MACRO": "Macro or market-wide headline",
    "NEWS": "General company / market headline",
}

_POS = {"strong_positive", "mild_positive"}
_NEG = {"strong_negative", "mild_negative"}
_NON_COMPANY = {"MACRO", "SECTOR", None, ""}


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
    for label in ("strong_negative", "strong_positive", "mild_negative", "mild_positive"):
        if _SENT_RE[label].search(t):
            return label
    return "neutral"


def _classify_event(title: str, is_macro: bool = False) -> str:
    t = (title or "").lower()
    for event in ("REGULATORY", "ORDER", "STAKE", "PROMOTER", "DIVIDEND", "EARNINGS", "CAPEX", "MANAGEMENT", "RATING", "MACRO"):
        if _EVENT_RE[event].search(t):
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
    if "nseindia" in host or "bseindia" in host:
        return "EXCHANGE"
    if "sebi" in host or "rbi" in host:
        return "REGULATOR"
    return "NEWS"


def _normalise_title(title: str) -> str:
    t = re.sub(r"\[[^\]]+\]", " ", title or "")
    t = re.sub(r"\b(live updates?|live blog|breaking|watch)\b", " ", t, flags=re.I)
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


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
        "symbol": symbol, "title": title, "link": link, "source": source or "News", "pub_dt": pub_dt,
        "time_str": _format_time(pub_dt), "sentiment": sentiment, "sentiment_label": _SENTIMENT_LABELS[sentiment],
        "event": event, "event_reason": _EVENT_REASON.get(event, "General news headline"),
        "importance": _importance(title, event, sentiment), "source_type": _source_type(source, link),
        "query_kind": query_kind, "cluster_key": _normalise_title(title), "is_new": False, "is_breaking": False,
        "priority": 0, "relevance": 0, "source_count": 1, "sources": [source or "News"],
    }


def _rss_url(query: str, days: int = 1) -> str:
    q = f"{query} when:{max(1, int(days))}d"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"


@st.cache_data(ttl=NEWS_FETCH_TTL, show_spinner=False)
def _fetch_rss_cached(url: str) -> list[dict]:
    """Fetch RSS through requests so the connection has a hard timeout."""
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 Arka-Trades-News/9.2"})
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        rows = []
        for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
            rows.append({
                "title": entry.get("title", ""), "link": entry.get("link", ""),
                "published": entry.get("published", ""), "updated": entry.get("updated", ""),
                "published_parsed": entry.get("published_parsed"), "updated_parsed": entry.get("updated_parsed"),
                "source": entry.get("source", {}),
            })
        return rows
    except Exception:
        return []


@st.cache_data(ttl=NEWS_FETCH_TTL, show_spinner=False)
def _fetch_query_cached(query: str, symbol: str, is_macro: bool = False, query_kind: str = "company", days: int = 1) -> list[dict]:
    cutoff = _now_ist() - timedelta(days=days)
    out = []
    for entry in _fetch_rss_cached(_rss_url(query, days)):
        article = _build_article(entry, symbol, is_macro=is_macro, query_kind=query_kind)
        if article and article["pub_dt"] >= cutoff:
            out.append(article)
    return out


def _fetch_query(query: str, symbol: str, is_macro: bool = False, query_kind: str = "company", days: int = 1) -> list[dict]:
    return _fetch_query_cached(query, symbol, is_macro, query_kind, days)


# ───────────────────────── company relevance ─────────────────────────
_NAME_STOP = {"limited", "ltd", "the", "of", "and", "co", "company", "corporation", "corp", "inc"}


def _company_words(company: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9& ]+", " ", company or "")
    return [w for w in cleaned.split() if w.lower() not in _NAME_STOP and w != "&"]


def _is_relevant(title: str, symbol: str, company: str | None) -> bool:
    """True when the headline names the company (first two words of its name) or its NSE symbol."""
    t = (title or "").lower()
    sym = (symbol or "").lower()
    if len(sym) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(sym)}(?![a-z0-9])", t):
        return True
    words = _company_words(company or "")
    if not words:
        return False
    key = " ".join(words[:2]).lower()
    if len(words) == 1 and len(words[0]) < 4:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(key)}", t))


def _fetch_news_for_stock(symbol: str, days: int = 1, company: str | None = None) -> list[dict]:
    """Company headlines. With `company`, the query uses the name and results are filtered to
    headlines that really mention the company / symbol (so a portfolio light is trustworthy)."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return []
    company = (company or "").strip() or None
    if company:
        words = _company_words(company)
        name = " ".join(words[:3]) if words else symbol
        query = f'("{name}" OR "{symbol}") (stock OR shares OR results OR order OR stake OR earnings)'
    else:
        query = f'"{symbol}" India stock results earnings order promoter stake'
    items = _fetch_query_cached(query, symbol, query_kind="company", days=days)
    if company:
        items = [a for a in items if _is_relevant(a.get("title", ""), symbol, company)]
    return _dedupe_articles(items)


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
        if item.get("importance", 0) > by_link[link].get("importance", 0):
            by_link[link] = item

    raw = list(by_link.values())
    raw.sort(key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
    clusters: list[list[dict]] = []
    representatives: list[str] = []
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
        art["priority"] = min(100, importance + recency + multi_source + source_bonus + context_bonus)
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
    defaults = {"_news_cache": {}, "_news_fetched": {}, "_news_seen_keys": set(),
                "_news_days": 1, "_news_category": "ALL", "_news_search": ""}
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
    """Pick up to batch_size symbols to refresh this cycle. The active security is always pinned;
    the rest rotate on a time epoch so the whole list is covered over a few cycles."""
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
    n_batches = -(-len(rest) // slots)
    epoch = int(time.time() // (NEWS_EXPIRE_MIN * 60))
    offset = (epoch % n_batches) * slots
    batch = rest[offset:offset + slots]
    if len(batch) < slots:
        batch += rest[: slots - len(batch)]
    return pinned + batch


def refresh_news(watchlist: list[str], current_security: str | None = None, sector: str | None = None, days: int = 1):
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
        if now - st.session_state["_news_fetched"].get(key, 0) >= NEWS_EXPIRE_MIN * 60:
            jobs.append((key, sym))
    if jobs:
        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as ex:
            future_map = {ex.submit(_fetch_news_for_stock, sym, days): (key, sym) for key, sym in jobs}
            for future in as_completed(future_map):
                key, _sym = future_map[future]
                try:
                    st.session_state["_news_cache"][key] = future.result()
                except Exception:
                    st.session_state["_news_cache"][key] = []
                st.session_state["_news_fetched"][key] = now

    macro_key = f"MACRO:{days}"
    if now - st.session_state["_news_fetched"].get(macro_key, 0) >= NEWS_EXPIRE_MIN * 60:
        st.session_state["_news_cache"][macro_key] = _fetch_macro_news(days)
        st.session_state["_news_fetched"][macro_key] = now

    if sector:
        sector_key = f"SECTOR:{sector}:{days}"
        if now - st.session_state["_news_fetched"].get(sector_key, 0) >= NEWS_EXPIRE_MIN * 60:
            st.session_state["_news_cache"][sector_key] = _fetch_sector_news(sector, days)
            st.session_state["_news_fetched"][sector_key] = now


def get_news_dot(sym: str) -> str:
    _ensure_news_state()
    symbol = (sym or "").strip().upper()
    for key, items in st.session_state.get("_news_cache", {}).items():
        if key.startswith(f"SEC:{symbol}:") and items:
            return "1"
    return ""


def _combined_feed(watchlist: list[str], macro_only: bool = False, current_security: str | None = None,
                   sector: str | None = None, days: int = 1) -> list[dict]:
    cache = st.session_state.get("_news_cache", {})
    symbols = list(dict.fromkeys([str(s).strip().upper() for s in (watchlist or []) if str(s).strip()]))
    current = (current_security or "").strip().upper()
    if current:
        symbols = [current] + [s for s in symbols if s != current]

    keys = []
    if not macro_only:
        keys.extend(f"SEC:{s}:{days}" for s in symbols)
        if sector:
            keys.append(f"SECTOR:{sector}:{days}")
    keys.append(f"MACRO:{days}")

    combined: list[dict] = []
    for key in keys:
        combined.extend(cache.get(key, []))
    combined = _enrich_priority(_dedupe_articles(combined), current)
    combined.sort(key=lambda x: (x.get("priority", 0), x.get("pub_dt", datetime.min.replace(tzinfo=IST))), reverse=True)

    if not macro_only:
        # macro headlines are plentiful; cap them so company news is never drowned out
        macro = [x for x in combined if x.get("symbol") == "MACRO"][:MAX_MACRO_ITEMS]
        other = [x for x in combined if x.get("symbol") != "MACRO"]
        combined = sorted(other + macro, key=lambda x: (x.get("priority", 0), x.get("pub_dt", datetime.min.replace(tzinfo=IST))), reverse=True)
    return combined[:MAX_COMBINED_ITEMS]


def get_security_news(symbol: str, days: int = 7) -> list[dict]:
    """Public helper for Research > News."""
    _ensure_news_state()
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return []
    key = f"SEC:{symbol}:{int(days)}"
    now = time.time()
    if now - st.session_state["_news_fetched"].get(key, 0) >= NEWS_EXPIRE_MIN * 60:
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
        return [x for x in items if x.get("sentiment") in _POS]
    if category == "NEGATIVE":
        return [x for x in items if x.get("sentiment") in _NEG]
    return [x for x in items if x.get("event") == category]


def _search_filter(items: list[dict], query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return items
    return [i for i in items if q in i.get("title", "").lower() or q in i.get("source", "").lower()
            or q in i.get("event", "").lower() or q in i.get("symbol", "").lower()]


def _tab_views(items: list[dict]):
    """The four rail tabs. Each has its own rule, so they show different stories:
    TOP = ranked by priority · BREAK = last 3h newest-first · CATALYST = company-specific positives
    · RISK = negative / regulatory (market-wide included)."""
    top = list(items)
    brk = sorted([x for x in items if _minutes_old(x) <= BREAK_WINDOW_MIN],
                 key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
    cat = [x for x in items if x.get("symbol") not in _NON_COMPANY and (
        x.get("sentiment") in _POS or (x.get("event") in {"ORDER", "CAPEX", "STAKE", "DIVIDEND"} and x.get("sentiment") not in _NEG))]
    risk = [x for x in items if x.get("sentiment") in _NEG or x.get("event") == "REGULATORY"]
    return top, brk, cat, risk


@st.cache_data(ttl=300, show_spinner=False)
def _intraday_closes(symbol: str):
    """Fetch one compact intraday series per active security."""
    try:
        sym = (symbol or "").strip().upper()
        if not sym or sym in {"MACRO", "SECTOR"}:
            return []
        ticker = sym if "." in sym else f"{sym}.NS"
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=False, threads=False)
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
        idx = idx.tz_localize("UTC").tz_convert(IST) if getattr(idx, "tz", None) is None else idx.tz_convert(IST)
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
        after = [(datetime.fromisoformat(ts), price) for ts, price in points if datetime.fromisoformat(ts) >= target]
        if not after:
            return None
        start, end = float(after[0][1]), float(points[-1][1])
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
    return f'<span style="color:{c};">{"+" if pct >= 0 else ""}{pct:.2f}% since headline</span>'


def _safe_href(url: str) -> str:
    if not url:
        return "#"
    if urlparse(url).scheme in {"http", "https"}:
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


def _render_rows(items: list[dict], empty_msg: str = "No matching news in the selected window.",
                 show_impact: bool = False, impact_symbols: set[str] | None = None):
    if not items:
        st.markdown(f'<div style="font:11px {MONO};color:{T2};padding:14px 2px;line-height:1.5;">{html.escape(empty_msg)}</div>',
                    unsafe_allow_html=True)
        return
    # is_new is set once on the combined feed (render_news_rail); do NOT re-mark here, that reset the badge.
    active = impact_symbols or set()
    st.markdown("".join(_render_story(a, show_impact=show_impact and bool(active) and a.get("symbol") in active) for a in items),
                unsafe_allow_html=True)


def _tab_note(text: str):
    st.markdown(f'<div style="font:8px {MONO};color:{T3};margin:2px 0 6px;">{html.escape(text)}</div>', unsafe_allow_html=True)


def _render_controls():
    c1, c2 = st.columns([1.0, 1.0])
    with c1:
        days_label = st.selectbox("WINDOW", ["24H", "7D", "30D"],
                                  index={1: 0, 7: 1, 30: 2}.get(int(st.session_state.get("_news_days", 1) or 1), 0),
                                  key="news_window_v91", label_visibility="collapsed")
    with c2:
        search = st.text_input("NEWS SEARCH", value=st.session_state.get("_news_search", ""), placeholder="search…",
                               key="news_search_v91", label_visibility="collapsed")
    st.session_state["_news_days"] = {"24H": 1, "7D": 7, "30D": 30}[days_label]
    st.session_state["_news_search"] = search


def _coverage_note(watchlist: list[str], current: str, days: int) -> str:
    symbols = list(dict.fromkeys([str(s).strip().upper() for s in (watchlist or []) if str(s).strip()]))
    if not symbols:
        return ""
    fetched = st.session_state.get("_news_fetched", {})
    covered = sum(1 for s in symbols if fetched.get(f"SEC:{s}:{days}", 0) > 0)
    if covered >= len(symbols):
        return ""
    return (f'<div style="font:8px {MONO};color:{T3};margin:2px 0 6px;">COVERAGE · {covered} of {len(symbols)} symbols refreshed so far'
            f' · rotates {MAX_SECURITY_REFRESH} at a time every {NEWS_EXPIRE_MIN} min'
            f'{" · " + current + " always included" if current else ""}</div>')


def render_news_rail(watchlist: list[str], label: str = "WATCHLIST", current_security: str | None = None, sector: str | None = None):
    """Right rail: prioritized, context-aware market news."""
    _ensure_news_state()
    _render_controls()          # controls first so a 7D/30D click is honoured on this run
    days = int(st.session_state.get("_news_days", 1) or 1)

    refresh_news(watchlist, current_security=current_security, sector=sector, days=days)
    combined = _mark_new(_combined_feed(watchlist, current_security=current_security, sector=sector, days=days))

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
    category = st.selectbox("FILTER", options, index=options.index(default), key="news_category_v91", label_visibility="collapsed")
    st.session_state["_news_category"] = category

    base = _search_filter(_category_filter(combined, category, current), search_query)
    top, brk, cat, risk = _tab_views(base)
    _, _, all_cat, _ = _tab_views(combined)

    story_count = len(combined)
    source_count = len({str(x.get("source", "News")) for x in combined})
    new_count = sum(bool(x.get("is_new")) for x in combined)
    event_counts = Counter(x.get("event", "NEWS") for x in combined)
    st.markdown(
        f'''<div style="border-top:1px solid {BORDER};border-bottom:1px solid {BORDER};padding:7px 0;margin:8px 0 6px;">
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;text-align:center;font:8px {MONO};">
          <div><div style="color:{GOLD};font-weight:800;">STORIES</div><div style="color:{IVORY};font-size:12px;">{story_count}</div></div>
          <div><div style="color:{RED};font-weight:800;">NEW</div><div style="color:{IVORY};font-size:12px;">{new_count}</div></div>
          <div><div style="color:{GREEN};font-weight:800;">CATALYST</div><div style="color:{IVORY};font-size:12px;">{len(all_cat)}</div></div>
          <div><div style="color:{BLUE};font-weight:800;">SOURCES</div><div style="color:{IVORY};font-size:12px;">{source_count}</div></div>
        </div></div>''', unsafe_allow_html=True)
    tape = " · ".join(f"{k} {v}" for k, v in event_counts.most_common(4))
    if tape:
        st.markdown(f'<div style="font:8px {MONO};color:{T3};margin:3px 0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">TAPE · {html.escape(tape)}</div>',
                    unsafe_allow_html=True)

    impact_symbols = {current} if current else set()
    t_top, t_brk, t_cat, t_risk = st.tabs(["TOP", "BREAK", "CATALYST", "RISK"])
    with t_top:
        _tab_note(f"{len(top)} stories · highest priority first (importance, recency, sources)")
        _render_rows(top[:24], show_impact=True, impact_symbols=impact_symbols)
    with t_brk:
        _tab_note(f"{len(brk)} stories in the last {BREAK_WINDOW_MIN // 60}h · newest first")
        _render_rows(brk[:20], empty_msg=f"Nothing new in the last {BREAK_WINDOW_MIN // 60} hours.",
                     show_impact=True, impact_symbols=impact_symbols)
    with t_cat:
        _tab_note(f"{len(cat)} company stories · positive tone, orders, capex, stakes, dividends")
        _render_rows(cat[:20], empty_msg="No company-specific positive catalysts right now. Add stocks to your Watchlist or Portfolio to see them here.",
                     show_impact=True, impact_symbols=impact_symbols)
    with t_risk:
        _tab_note(f"{len(risk)} stories · negative tone or regulatory action (market-wide included)")
        _render_rows(risk[:20], empty_msg="No risk headlines in this window.", show_impact=True, impact_symbols=impact_symbols)


news_panel = render_news_rail
news_box = render_news_rail
