"""
news_feed.py — Arka Trades News Intelligence v4

Terminal-style right-rail news engine.

v4 keeps the existing public API compatible with app.py while adding:
- security-aware news context
- watchlist / security / sector / macro filtering
- event classification (earnings, order, stake, regulatory, etc.)
- headline importance and breaking-news detection
- duplicate / near-duplicate headline clustering
- source-type classification
- 24h / 7d / 30d cache windows
- compact news search
- optional price-impact calculation for company headlines
- explicit source/provenance labels; no invented data

The fetch layer still uses Google News RSS. If a stronger exchange/company
filings connector is added later, it can feed the same normalized article
schema without changing the UI.
"""

from __future__ import annotations

import html
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import feedparser
import pandas as pd
import streamlit as st
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))

# Terminal palette
GOLD = "#FF9F0A"
DARK = "#000000"
DARK2 = "#0A0A0A"
DARK3 = "#111111"
BORDER = "#262626"
T2 = "#8A8A8A"
T3 = "#5A5A5A"
IVORY = "#E8E8E8"
GREEN = "#30D158"
RED = "#FF453A"
BLUE = "#5AC8FA"
MONO = "'JetBrains Mono',monospace"

NEWS_EXPIRE = 20
NEWS_FETCH_TTL = 300
MAX_COMBINED_ITEMS = 60
MAX_SECURITY_REFRESH = 12
MAX_FETCH_WORKERS = 6
DEFAULT_DAYS = 1

# ---------------------------------------------------------------------------
# Classification dictionaries
# ---------------------------------------------------------------------------
_EVENT_RULES = {
    "EARNINGS": ["earnings", "results", "quarterly result", "profit", "revenue", "ebitda", "eps"],
    "ORDER": ["order", "contract", "bags order", "wins order", "wins contract", "work order", "purchase order"],
    "STAKE": ["stake", "acquire", "acquisition", "buy stake", "sells stake", "buys stake", "block deal"],
    "PROMOTER": ["promoter", "promoters", "pledge", "pledged", "insider", "insider buying", "insider selling"],
    "REGULATORY": ["sebi", "rbi", "penalty", "probe", "raid", "regulatory", "show cause", "ban", "banned", "compliance"],
    "DIVIDEND": ["dividend", "ex-dividend", "record date", "bonus", "split", "buyback"],
    "RATING": ["upgrade", "downgrade", "target price", "rating", "outperform", "underperform", "brokerage"],
    "CAPEX": ["capex", "capital expenditure", "expansion", "new plant", "capacity", "investment plan"],
    "MANAGEMENT": ["ceo", "cfo", "management", "md & ceo", "appoints", "resigns", "steps down", "guidance"],
    "MACRO": ["rbi", "fed", "federal reserve", "inflation", "gdp", "crude", "oil price", "rupee", "dollar", "nifty", "sensex", "fii", "dii"],
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
    "all-time high", "jumps", "soars", "bags order", "profit rises",
]
_MILD_NEGATIVE = ["falls", "declines", "drops", "down", "cut", "weak", "concern", "delay", "misses"]
_MILD_POSITIVE = ["rises", "gains", "up", "beats", "growth", "expands", "launch", "partnership"]

_SENTIMENT_COLORS = {
    "strong_negative": RED,
    "strong_positive": GREEN,
    "mild_negative": "#FF453A99",
    "mild_positive": "#30D15899",
    "neutral": BORDER,
}
_SENTIMENT_LABELS = {
    "strong_negative": "NEG",
    "strong_positive": "POS",
    "mild_negative": "neg",
    "mild_positive": "pos",
    "neutral": "—",
}

_EVENT_COLORS = {
    "EARNINGS": "#BF5AF2",
    "ORDER": GREEN,
    "STAKE": GOLD,
    "PROMOTER": GOLD,
    "REGULATORY": RED,
    "DIVIDEND": GREEN,
    "RATING": BLUE,
    "CAPEX": "#64D2FF",
    "MANAGEMENT": "#FFD60A",
    "MACRO": GOLD,
    "NEWS": T2,
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

# Macro queries intentionally remain broad enough to catch market-moving stories.
_MACRO_QUERIES = [
    "RBI monetary policy",
    "Nifty Sensex market",
    "FII DII flows India",
    "US Fed interest rate",
    "crude oil price India",
    "India GDP inflation",
    "US stock market today",
    "dollar index rupee",
]

# Sector query aliases used when a sector is known. These are query fragments,
# not claims about a company's classification.
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


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_ist() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _format_time(pub_dt: datetime) -> str:
    now = _now_ist()
    diff = now - pub_dt.astimezone(IST)
    secs = int(diff.total_seconds())
    if secs < 0:
        return "now"
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return pub_dt.astimezone(IST).strftime("%d %b")


def _parse_pub(entry):
    raw = entry.get("published", entry.get("updated", ""))
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    except Exception:
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
    # Specific events first so generic words such as "profit" do not swallow
    # a regulatory/order headline.
    order = ["REGULATORY", "ORDER", "STAKE", "PROMOTER", "DIVIDEND", "EARNINGS", "CAPEX", "MANAGEMENT", "RATING", "MACRO"]
    for event in order:
        if any(kw in t for kw in _EVENT_RULES[event]):
            return event
    return "MACRO" if is_macro else "NEWS"


def _importance(title: str, event: str, sentiment: str) -> int:
    score = 1
    if event in {"EARNINGS", "ORDER", "STAKE", "PROMOTER", "REGULATORY", "DIVIDEND"}:
        score += 2
    if event in {"CAPEX", "MANAGEMENT", "RATING"}:
        score += 1
    if sentiment.startswith("strong"):
        score += 1
    t = (title or "").lower()
    if any(x in t for x in ["breaking", "just in", "exclusive"]):
        score += 1
    return min(score, 5)


def _source_type(source: str, link: str = "") -> str:
    s = (source or "").lower()
    for key, typ in _SOURCE_TYPES.items():
        if key in s:
            return typ
    host = urlparse(link or "").netloc.lower()
    if "nseindia" in host or "nse" in host:
        return "EXCHANGE"
    if "bseindia" in host:
        return "EXCHANGE"
    if "sebi" in host:
        return "REGULATOR"
    return "NEWS"


def _normalise_title(title: str) -> str:
    t = re.sub(r"\[[^\]]+\]", " ", title or "")
    t = re.sub(r"\b(live updates?|live blog|breaking)\b", " ", t, flags=re.I)
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    # Remove common source suffixes so the same story from several feeds can cluster.
    t = re.sub(r"\s+-\s+(reuters|cnbc|moneycontrol|business standard|economic times|mint)$", "", t)
    return t


def _article_key(article: dict) -> str:
    return _normalise_title(article.get("title", ""))[:180]


def _build_article(entry, symbol: str, is_macro: bool = False, query_kind: str = "company") -> dict | None:
    pub_dt = _parse_pub(entry)
    if not pub_dt:
        return None
    title = entry.get("title", "No title") or "No title"
    link = entry.get("link", "") or ""
    source_obj = entry.get("source", {}) or {}
    source = source_obj.get("title", "News") if isinstance(source_obj, dict) else str(source_obj)
    event = _classify_event(title, is_macro=is_macro)
    sentiment = _classify_sentiment(title)
    return {
        "symbol": symbol,
        "title": title.strip(),
        "link": link,
        "source": source or "News",
        "pub_dt": pub_dt,
        "time_str": _format_time(pub_dt),
        "sentiment": sentiment,
        "event": event,
        "importance": _importance(title, event, sentiment),
        "source_type": _source_type(source, link),
        "query_kind": query_kind,
        "cluster_key": _normalise_title(title),
        "is_new": False,
    }


def _rss_url(query: str, days: int = 1) -> str:
    # Google News accepts when:N queries and returns a compact RSS feed.
    q = f"{query} when:{max(1, int(days))}d"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    )


@st.cache_data(ttl=NEWS_FETCH_TTL, show_spinner=False)
def _fetch_query_cached(query: str, symbol: str, is_macro: bool = False, query_kind: str = "company", days: int = 1) -> list[dict]:
    """Cached network boundary: one RSS request is shared across reruns/users."""
    try:
        feed = feedparser.parse(_rss_url(query, days=days))
        out = []
        cutoff = _now_ist() - timedelta(days=days)
        for entry in feed.entries[:20]:
            article = _build_article(entry, symbol, is_macro=is_macro, query_kind=query_kind)
            if not article or article["pub_dt"] < cutoff:
                continue
            out.append(article)
        return out
    except Exception:
        return []


def _fetch_query(query: str, symbol: str, is_macro: bool = False, query_kind: str = "company", days: int = 1) -> list[dict]:
    return _fetch_query_cached(query, symbol, is_macro, query_kind, days)


def _fetch_news_for_stock(symbol: str, days: int = 1) -> list[dict]:
    """Fetch company/security news with a single cached RSS request."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return []
    query = f"{symbol} NSE India stock results earnings order stake promoter"
    return _dedupe_articles(_fetch_query_cached(query, symbol, query_kind="company", days=days))


def _fetch_macro_news(days: int = 1) -> list[dict]:
    # Keep macro coverage broad, but fetch concurrently and cache each feed.
    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as ex:
        futures = [ex.submit(_fetch_query_cached, q, "MACRO", True, "macro", days) for q in _MACRO_QUERIES]
        results = []
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
    """Remove duplicate links and cluster repeated headlines into one lead story."""
    by_link = {}
    for item in items:
        link = item.get("link") or item.get("title")
        if link not in by_link:
            by_link[link] = item
        else:
            # Keep the stronger metadata when the same article appeared twice.
            old = by_link[link]
            if item.get("importance", 0) > old.get("importance", 0):
                by_link[link] = item

    groups = defaultdict(list)
    for item in by_link.values():
        groups[item.get("cluster_key") or _article_key(item)].append(item)

    clustered = []
    for group in groups.values():
        group.sort(key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
        lead = dict(group[0])
        lead["source_count"] = len(group)
        lead["sources"] = list(dict.fromkeys(x.get("source", "News") for x in group))[:6]
        clustered.append(lead)
    clustered.sort(key=lambda x: x.get("pub_dt", datetime.min.replace(tzinfo=IST)), reverse=True)
    return clustered


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
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v.copy() if isinstance(v, dict) else (set(v) if isinstance(v, set) else v)


def _mark_new(items: list[dict]) -> list[dict]:
    seen = st.session_state.setdefault("_news_seen_keys", set())
    for item in items:
        key = item.get("link") or item.get("cluster_key") or item.get("title")
        item["is_new"] = key not in seen
    # Do not let the set grow forever.
    for item in items[:100]:
        key = item.get("link") or item.get("cluster_key") or item.get("title")
        if key:
            seen.add(key)
    if len(seen) > 2000:
        st.session_state["_news_seen_keys"] = set(list(seen)[-1000:])
    return items


def refresh_news(watchlist: list[str], current_security: str | None = None, sector: str | None = None, days: int = 1):
    """Refresh news without blocking the app on dozens of serial network calls."""
    _ensure_news_state()
    days = max(1, min(int(days or 1), 30))
    now = time.time()
    symbols = list(dict.fromkeys([s.strip().upper() for s in (watchlist or []) if s]))
    if current_security:
        cs = current_security.strip().upper()
        if cs:
            if cs in symbols:
                symbols.remove(cs)
            symbols.insert(0, cs)
    # The rail is narrow; prioritize the active security, then the first part of the watchlist.
    symbols = symbols[:MAX_SECURITY_REFRESH]

    jobs = []
    for sym in symbols:
        cache_key = f"SEC:{sym}:{days}"
        last = st.session_state["_news_fetched"].get(cache_key, 0)
        if now - last > NEWS_EXPIRE * 60:
            jobs.append((cache_key, sym))

    if jobs:
        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as ex:
            future_map = {ex.submit(_fetch_news_for_stock, sym, days): (key, sym) for key, sym in jobs}
            for future in as_completed(future_map):
                key, sym = future_map[future]
                try:
                    st.session_state["_news_cache"][key] = future.result()
                except Exception:
                    st.session_state["_news_cache"][key] = []
                st.session_state["_news_fetched"][key] = now

    macro_key = f"MACRO:{days}"
    macro_last = st.session_state["_news_fetched"].get(macro_key, 0)
    if now - macro_last > NEWS_EXPIRE * 60:
        st.session_state["_news_cache"][macro_key] = _fetch_macro_news(days=days)
        st.session_state["_news_fetched"][macro_key] = now

    if sector:
        sector_key = f"SECTOR:{sector}:{days}"
        sector_last = st.session_state["_news_fetched"].get(sector_key, 0)
        if now - sector_last > NEWS_EXPIRE * 60:
            st.session_state["_news_cache"][sector_key] = _fetch_sector_news(sector, days=days)
            st.session_state["_news_fetched"][sector_key] = now


def get_news_dot(sym: str) -> str:
    _ensure_news_state()
    sym = (sym or "").strip().upper()
    for key, items in st.session_state.get("_news_cache", {}).items():
        if key.startswith(f"SEC:{sym}:") and items:
            return "1"
    return ""


def _combined_feed(watchlist: list[str], macro_only: bool = False, current_security: str | None = None, sector: str | None = None, days: int = 1) -> list[dict]:
    cache = st.session_state.get("_news_cache", {})
    symbols = list(dict.fromkeys([s.strip().upper() for s in (watchlist or []) if s]))
    if current_security:
        cs = current_security.strip().upper()
        if cs:
            symbols.insert(0, cs)
    symbols = list(dict.fromkeys(symbols))

    keys = []
    if not macro_only:
        keys.extend(f"SEC:{s}:{days}" for s in symbols[:30])
        if sector:
            keys.append(f"SECTOR:{sector}:{days}")
    keys.append(f"MACRO:{days}")

    combined = []
    for key in keys:
        combined.extend(cache.get(key, []))
    return _dedupe_articles(combined)[:MAX_COMBINED_ITEMS]


def _category_filter(items: list[dict], category: str) -> list[dict]:
    category = (category or "ALL").upper()
    if category == "ALL":
        return items
    if category == "WATCHLIST":
        return [x for x in items if x.get("symbol") not in {"MACRO", "SECTOR"}]
    if category == "SECURITY":
        return [x for x in items if x.get("symbol") not in {"MACRO", "SECTOR"}]
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
    return [x for x in items if x.get("event") == category]


def _search_filter(items: list[dict], query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return items
    return [
        x for x in items
        if q in x.get("title", "").lower()
        or q in x.get("source", "").lower()
        or q in x.get("event", "").lower()
        or q in x.get("symbol", "").lower()
    ]


@st.cache_data(ttl=300, show_spinner=False)
@st.cache_data(ttl=300, show_spinner=False)
def _intraday_closes(symbol: str):
    """Fetch intraday prices once per symbol, not once per headline."""
    try:
        sym = (symbol or "").strip().upper()
        if not sym or sym in {"MACRO", "SECTOR"}:
            return []
        ticker = sym if "." in sym else f"{sym}.NS"
        df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=False, threads=False)
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
    """Best-effort move from nearest post-headline 1m price to latest price."""
    try:
        points = _intraday_closes(symbol)
        if not points:
            return None
        dt = datetime.fromisoformat(pub_dt_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        target = dt.astimezone(IST)
        after = [(ts, price) for ts, price in points if datetime.fromisoformat(ts) >= target]
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
    if article.get("symbol") in {"MACRO", "SECTOR"}:
        return ""
    impact = _intraday_price_impact(article["symbol"], article["pub_dt"].isoformat())
    if not impact:
        return ""
    pct = impact["pct"]
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


def _render_rows(items: list[dict], show_tag: bool = True, show_impact: bool = True, impact_symbols: set[str] | None = None):
    if not items:
        st.markdown(
            f'<div style="font-size:11px;color:{T2};padding:12px 2px;line-height:1.5;">'
            "No matching news in the selected window."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    items = _mark_new(items)
    rows_html = []
    for art in items:
        sentiment = art.get("sentiment", "neutral")
        accent = _SENTIMENT_COLORS.get(sentiment, BORDER)
        slabel = _SENTIMENT_LABELS.get(sentiment, "—")
        event = html.escape(art.get("event", "NEWS"))
        event_color = _EVENT_COLORS.get(art.get("event", "NEWS"), T2)
        source = html.escape(str(art.get("source", "News")))
        title = html.escape(str(art.get("title", "No title")))
        href = _safe_href(art.get("link", ""))
        tag = "MACRO" if art.get("symbol") == "MACRO" else ("SECTOR" if art.get("symbol") == "SECTOR" else art.get("symbol", "NEWS"))
        tag = html.escape(str(tag))
        tag_color = GOLD if art.get("symbol") in {"MACRO", "SECTOR"} else BLUE
        source_count = int(art.get("source_count", 1) or 1)
        source_count_html = f" · {source_count} sources" if source_count > 1 else ""
        new_html = '<span style="color:#FF453A;font-weight:800;">NEW</span>&nbsp;·&nbsp;' if art.get("is_new") else ""
        impact_ok = show_impact and (impact_symbols is None or art.get("symbol") in impact_symbols)
        impact_html = _impact_badge(art) if impact_ok else ""
        impact_block = f'<span style="margin-left:7px;">{impact_html}</span>' if impact_html else ""

        rows_html.append(
            f'''<a href="{href}" target="_blank" style="text-decoration:none;">
            <div style="border-left:2px solid {accent};padding:7px 0 7px 10px;margin-bottom:7px;background:linear-gradient(90deg,#0b0b0b,transparent);">
                <div style="font-size:9px;font-family:{MONO};font-weight:800;letter-spacing:.7px;margin-bottom:3px;">
                    <span style="color:{event_color};">{event}</span>&nbsp;·&nbsp;<span style="color:{T3};">IMP {art.get('importance',1)}/5</span>
                </div>
                <div style="font-size:12px;font-weight:600;color:{IVORY};line-height:1.4;">{title}</div>
                <div style="font-family:{MONO};font-size:9.5px;color:{T2};margin-top:4px;">
                    {new_html}<span style="color:{tag_color};font-weight:700;">{tag}</span>&nbsp;·&nbsp;{source}{source_count_html}&nbsp;·&nbsp;{art.get('time_str','—')}&nbsp;·&nbsp;
                    <span style="color:{accent};">{slabel}</span>{impact_block}
                </div>
            </div></a>'''
        )
    st.markdown("".join(rows_html), unsafe_allow_html=True)


def _render_controls(watchlist: list[str], current_security: str | None, sector: str | None):
    # The controls are deliberately compact for a narrow right rail.
    c1, c2 = st.columns([1.4, 1])
    with c1:
        days_label = st.selectbox("WINDOW", ["24H", "7D", "30D"], index={1: 0, 7: 1, 30: 2}.get(st.session_state.get("_news_days", 1), 0), key="news_window_v4", label_visibility="collapsed")
    with c2:
        st.session_state["_news_days"] = {"24H": 1, "7D": 7, "30D": 30}[days_label]
    st.text_input("NEWS SEARCH", placeholder="search headlines…", key="_news_search", label_visibility="collapsed")


def render_news_rail(
    watchlist: list[str],
    label: str = "WATCHLIST",
    current_security: str | None = None,
    sector: str | None = None,
):
    """Render the compact Bloomberg-style news intelligence rail.

    Existing calls such as render_news_rail(watchlist, label=...) continue to work.
    """
    _ensure_news_state()
    days = int(st.session_state.get("_news_days", 1) or 1)
    refresh_news(watchlist, current_security=current_security, sector=sector, days=days)
    _render_controls(watchlist, current_security, sector)
    # Controls can change state only on rerun; use the selected value for this render too.
    days = int(st.session_state.get("_news_days", days) or days)

    # Compact filter row. Security gets first priority when one is active.
    options = ["ALL", "WATCHLIST"]
    if current_security:
        options.insert(1, "SECURITY")
    if sector:
        options.append("SECTOR")
    options += ["MACRO", "EARNINGS", "CORPORATE", "REGULATORY", "RATING"]
    options = list(dict.fromkeys(options))
    default = st.session_state.get("_news_category", "ALL")
    if default not in options:
        default = "ALL"
    category = st.selectbox("FILTER", options, index=options.index(default), key="news_category_v4", label_visibility="collapsed")
    st.session_state["_news_category"] = category

    tab_all, tab_breaking = st.tabs(["NEWS", "BREAKING"])

    combined = _combined_feed(
        watchlist,
        macro_only=False,
        current_security=current_security,
        sector=sector,
        days=days,
    )
    filtered = _search_filter(_category_filter(combined, category), st.session_state.get("_news_search", ""))

    # Intraday impact is expensive; calculate it only for the active security.
    # This keeps the rail responsive even when many watchlist headlines are visible.
    impact_symbols = {current_security.strip().upper()} if current_security else set()

    with tab_all:
        _render_rows(filtered, show_tag=True, show_impact=True, impact_symbols=impact_symbols)

    with tab_breaking:
        breaking = [x for x in combined if x.get("is_new") or x.get("importance", 0) >= 4]
        breaking = _search_filter(_category_filter(breaking, category), st.session_state.get("_news_search", ""))
        _render_rows(breaking[:30], show_tag=True, show_impact=True, impact_symbols=impact_symbols)


# Backwards-compatible aliases
news_panel = render_news_rail
news_box = render_news_rail
