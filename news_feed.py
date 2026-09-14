"""
news_feed.py — Arka Trades News Module (v3 — right-rail terminal feed)

Fetching/caching logic is unchanged from v2. What changed is the
render: news_box() now draws a compact, Bloomberg-style scrolling
list meant for a narrow FIXED RIGHT RAIL (built in app.py), not a
dashboard card and not the old bottom-left floating dock. Everything
else (sentiment tagging, per-symbol cache, macro query set) is the
same so nothing else in the app needs to change.

Backwards-compatible aliases (`news_panel`, `news_box`) are kept so
old imports don't break.
"""

import streamlit as st
import feedparser
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=5, minutes=30))

# Bloomberg terminal palette — must match app.py's TERM_TOKENS
GOLD = "#FF9F0A"
DARK = "#000000"
DARK2 = "#0A0A0A"
DARK3 = "#111111"
BORDER = "#262626"
T2 = "#8A8A8A"
T3 = "#5A5A5A"
IVORY = "#E8E8E8"
MONO = "'JetBrains Mono',monospace"

NEWS_EXPIRE = 20
MAX_COMBINED_ITEMS = 60

_STRONG_NEGATIVE = [
    "crash", "plunge", "collapse", "fraud", "scam", "default", "bankrupt",
    "insolvency", "probe", "raid", "scandal", "resign", "sebi action",
    "penalty", "banned", "suspended", "downgrade", "slump", "tumble",
    "loss widens", "profit warning", "recall",
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
    "expands", "launch", "partnership",
]

_SENTIMENT_COLORS = {
    "strong_negative": "#FF453A",
    "strong_positive": "#30D158",
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
            })
        return results
    except Exception:
        return []


# Macro/global queries — these are what let you trade off news that
# moves the WHOLE market (India + global), not just single names.
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


def _fetch_macro_news() -> list[dict]:
    today = _today_ist()
    results = []
    for q in _MACRO_QUERIES:
        query = q.replace(" ", "+")
        url = (
            f"https://news.google.com/rss/search?"
            f"q={query}"
            f"&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                pub_dt = _parse_pub(entry)
                if not pub_dt:
                    continue
                pub_ist = pub_dt.astimezone(IST)
                if pub_ist.strftime("%Y-%m-%d") != today:
                    continue
                results.append({
                    "symbol": "MACRO",
                    "title": entry.get("title", "No title"),
                    "link": entry.get("link", "#"),
                    "source": entry.get("source", {}).get("title", "News"),
                    "pub_dt": pub_ist,
                    "time_str": _format_time(pub_ist),
                    "sentiment": _classify_sentiment(entry.get("title", "")),
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
    for sym in watchlist:
        last = st.session_state["_news_fetched"].get(sym, 0)
        if now - last > NEWS_EXPIRE * 60:
            articles = _fetch_news_for_stock(sym)
            st.session_state["_news_cache"][sym] = articles
            st.session_state["_news_fetched"][sym] = now
    macro_last = st.session_state["_news_fetched"].get("_MACRO_", 0)
    if now - macro_last > NEWS_EXPIRE * 60:
        st.session_state["_news_cache"]["_MACRO_"] = _fetch_macro_news()
        st.session_state["_news_fetched"]["_MACRO_"] = now


def get_news_dot(sym: str) -> str:
    return "1" if st.session_state.get("_news_cache", {}).get(sym) else ""


def _combined_feed(watchlist: list[str], macro_only: bool = False) -> list[dict]:
    cache = st.session_state.get("_news_cache", {})
    seen_links = set()
    combined = []
    syms = ["_MACRO_"] if macro_only else list(watchlist) + ["_MACRO_"]
    for sym in syms:
        for art in cache.get(sym, []):
            if art["link"] in seen_links:
                continue
            seen_links.add(art["link"])
            combined.append(art)
    combined.sort(key=lambda a: a["pub_dt"], reverse=True)
    return combined[:MAX_COMBINED_ITEMS]


def render_news_rail(watchlist: list[str], label: str = "WATCHLIST"):
    """
    Renders the terminal-style news feed for the FIXED RIGHT RAIL.
    Call this from inside the rail's container in app.py. This does
    NOT create its own fixed/positioned div — app.py owns the rail
    shell; this function only fills it with rows so it can be reused
    inside a normal-width container too if needed.
    """
    _ensure_news_state()
    refresh_news(watchlist)

    tab_all, tab_macro = st.tabs(["All Impact News", "Macro / Global Only"])

    with tab_all:
        combined = _combined_feed(watchlist, macro_only=False)
        _render_rows(combined, show_tag=True)

    with tab_macro:
        macro = _combined_feed(watchlist, macro_only=True)
        _render_rows(macro, show_tag=False)


def _render_rows(items, show_tag=True):
    if not items:
        st.markdown(
            f"""<div style="font-size:11px;color:{T2};padding:10px 2px;">
            No news yet today. Checking every {NEWS_EXPIRE} min.</div>""",
            unsafe_allow_html=True,
        )
        return

    rows_html = []
    for art in items:
        sentiment = art.get("sentiment", "neutral")
        accent = _SENTIMENT_COLORS.get(sentiment, BORDER)
        slabel = _SENTIMENT_LABELS.get(sentiment, "—")
        tag = "MACRO" if art["symbol"] == "_MACRO_" else art["symbol"]
        tag_color = GOLD if art["symbol"] == "_MACRO_" else "#5AC8FA"
        tag_html = (
            f'<span style="color:{tag_color};font-weight:700;">{tag}</span>&nbsp;·&nbsp;'
            if show_tag else ""
        )
        rows_html.append(f"""<a href="{art['link']}" target="_blank" style="text-decoration:none;">
            <div style="border-left:2px solid {accent};padding:6px 0 6px 10px;margin-bottom:6px;">
                <div style="font-size:12px;font-weight:600;color:{IVORY};line-height:1.4;">
                    {art['title']}
                </div>
                <div style="font-family:{MONO};font-size:9.5px;color:{T2};margin-top:3px;">
                    {tag_html}{art['source']}&nbsp;·&nbsp;{art['time_str']}&nbsp;·&nbsp;
                    <span style="color:{accent};">{slabel}</span>
                </div>
            </div>
        </a>""")
    st.markdown("".join(rows_html), unsafe_allow_html=True)


# Backwards-compatible aliases for older import sites
news_panel = render_news_rail
news_box = render_news_rail
