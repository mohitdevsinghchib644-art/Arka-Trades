"""
news_feed.py — Arka Trades News Module (v2 — persistent combined feed)

CHANGED FROM v1:
  - Old: per-symbol tabs (st.tabs), one tab per stock with news today.
    Called separately inside the Scanner tab AND the standalone News
    Terminal page — two different call sites, two different states.
  - New: ONE combined, deduped feed across the entire watchlist,
    meant to be called once and rendered as a single persistent
    rectangle box, bottom-left, present on every page — not
    re-invoked per-tab. The "sub-group by symbol" tabs are removed
    entirely, per direct request.

The underlying fetch (Google News RSS via feedparser, today-only
filter, midnight cleanup, 20-minute per-stock expiry) is unchanged —
only the shape of what gets returned and how it's meant to be
displayed has changed.
"""

import streamlit as st
import feedparser
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=5, minutes=30))
GOLD = "#FF9500"   # matches app.py's AMBER — kept as a separate
                   # constant here so news_feed.py has no import
                   # dependency on app.py's design tokens
DARK2 = "#0A0A0A"
DARK3 = "#141414"
BORDER = "#2A2A2A"
T2 = "#8A8A8A"
IVORY = "#E8E8E8"
NEWS_EXPIRE = 20  # minutes between fetches per stock
MAX_COMBINED_ITEMS = 40  # raised from 25 — box is now larger/top-right, can hold more

# ── Sentiment coloring ──────────────────────────────────────────
# NOTE: This is a keyword-based classifier, NOT an AI/LLM call. A
# true AI sentiment pass (e.g. via the Gemini client arka_ai.py
# already sets up) would cost one API call per headline, repeated on
# every ~20min refresh across up to 40 combined items — real, scaling
# API spend that wasn't something to wire in silently without
# confirming the cost is wanted. This keyword approach is free and
# instant, and gives the same three-tier coloring outcome for the
# common, unambiguous cases (crashes, record results, fraud,
# beats/misses). It will misclassify subtler headlines that need real
# language understanding — flag if you want to swap this for an
# actual Gemini call once cost is confirmed.
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

# Colors: strong negative/positive get the FULL saturated color per
# the request ("very bad/very good = full dark green/red only");
# mild cases get muted/light versions; neutral gets no color accent
# at all (plain border).
_SENTIMENT_COLORS = {
    "strong_negative": "#B91C1C",  # dark, saturated red
    "strong_positive": "#15803D",  # dark, saturated green
    "mild_negative": "#F87171",    # light red
    "mild_positive": "#86EFAC",    # light green
    "neutral": BORDER,             # no color signal — just the default border
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
        return "Just now"
    elif secs < 3600:
        return f"{secs // 60} min ago"
    elif secs < 86400:
        return f"{secs // 3600} hr ago"
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
    """Unchanged fetch logic from v1 — Google News RSS, today only."""
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


# NEW: broad national/international market news, independent of the
# watchlist. Runs a small fixed set of macro queries covering what
# generally moves Indian markets — RBI policy, FII/DII flows, global
# cues, crude oil (India is a major importer so this matters a lot),
# US Fed decisions. This is a fixed query list, not a dynamic "what
# matters today" AI judgment — that would need an LLM call to decide
# relevance, which has the same cost consideration flagged above for
# sentiment. A fixed macro query set is a reasonable free
# approximation of "important for Indian markets" for now.
_MACRO_QUERIES = [
    "RBI monetary policy",
    "Nifty Sensex market",
    "FII DII flows India",
    "US Fed interest rate",
    "crude oil price India",
    "India GDP inflation",
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
    """Refresh stale stocks (older than NEWS_EXPIRE minutes), plus the
    macro/national feed on the same expiry cadence."""
    _midnight_cleanup()
    now = time.time()
    for sym in watchlist:
        last = st.session_state["_news_fetched"].get(sym, 0)
        if now - last > NEWS_EXPIRE * 60:
            articles = _fetch_news_for_stock(sym)
            st.session_state["_news_cache"][sym] = articles
            st.session_state["_news_fetched"][sym] = now
    # NEW: macro feed refreshes on the same NEWS_EXPIRE cadence,
    # stored under a fixed pseudo-symbol key so it merges into the
    # combined feed the same way per-stock articles do.
    macro_last = st.session_state["_news_fetched"].get("_MACRO_", 0)
    if now - macro_last > NEWS_EXPIRE * 60:
        st.session_state["_news_cache"]["_MACRO_"] = _fetch_macro_news()
        st.session_state["_news_fetched"]["_MACRO_"] = now


def get_news_dot(sym: str) -> str:
    """Unchanged — still used by Scanner result cards to show a dot
    marker on individual stock cards. Kept for that purpose only;
    no longer drives a separate per-symbol news tab."""
    return "1" if st.session_state.get("_news_cache", {}).get(sym) else ""


def _combined_feed(watchlist: list[str]) -> list[dict]:
    """
    Merge every symbol's cached articles PLUS the macro/national feed
    into one flat list, dedupe by link, sort by publish time
    descending, cap at MAX_COMBINED_ITEMS. Macro news is always
    included even with an empty watchlist — national/international
    market news isn't watchlist-dependent.
    """
    cache = st.session_state.get("_news_cache", {})
    seen_links = set()
    combined = []
    for sym in list(watchlist) + ["_MACRO_"]:
        for art in cache.get(sym, []):
            if art["link"] in seen_links:
                continue
            seen_links.add(art["link"])
            combined.append(art)
    combined.sort(key=lambda a: a["pub_dt"], reverse=True)
    return combined[:MAX_COMBINED_ITEMS]


@st.fragment(run_every=30)
def news_box(watchlist: list[str]):
    """
    Persistent combined news feed — ONE call site, meant to render
    inside the top-right fixed container (positioning handled by the
    CSS wrapper in app.py; this function renders the box's CONTENTS
    only). Moved from bottom-left to top-right, enlarged, and now
    includes a national/international macro feed alongside per-stock
    news — a plain watchlist upload is no longer required to see
    anything, since macro news always shows.

    Refresh interval raised from 10s to 30s to match the same
    NSE-safe polling posture as the price data — this fragment
    doesn't call NSE directly, but there's no reason for it to poll
    faster than the rest of the app now moves.
    """
    _ensure_news_state()
    refresh_news(watchlist)  # always runs now — macro feed doesn't need a watchlist

    combined = _combined_feed(watchlist)

    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-radius:4px;
         padding:16px 18px;height:100%;display:flex;flex-direction:column;">
        <div style="display:flex;align-items:center;justify-content:space-between;
             margin-bottom:12px;flex-shrink:0;">
            <span style="font-size:14px;font-weight:800;color:{IVORY};
                 letter-spacing:0.5px;text-transform:uppercase;">Market News</span>
            <span style="font-size:10px;color:{T2};font-weight:700;">LIVE</span>
        </div>
        <div id="arka-news-scroll" style="overflow-y:auto;flex:1;">
    """, unsafe_allow_html=True)

    if not combined:
        st.markdown(f"""<div style="font-size:12px;color:{T2};padding:8px 0;">
            No news yet. Checking every {NEWS_EXPIRE} min.</div>""", unsafe_allow_html=True)
    else:
        rows_html = []
        for art in combined:
            sentiment = art.get("sentiment", "neutral")
            accent = _SENTIMENT_COLORS.get(sentiment, BORDER)
            # macro items show a "MARKET" tag instead of a stock
            # symbol, since their "symbol" field is the internal
            # "_MACRO_" placeholder key.
            tag = "MARKET" if art["symbol"] == "_MACRO_" else art["symbol"]
            tag_color = GOLD if art["symbol"] == "_MACRO_" else IVORY
            rows_html.append(f"""<div style="border-left:3px solid {accent};padding:8px 0 8px 12px;margin-bottom:10px;">
                <a href="{art['link']}" target="_blank" style="font-size:13px;font-weight:600;
                   color:{IVORY};text-decoration:none;line-height:1.45;display:block;">
                   {art['title']}
                </a>
                <div style="font-size:10.5px;color:{T2};margin-top:4px;">
                    <span style="color:{tag_color};font-weight:700;">{tag}</span>
                    &nbsp;·&nbsp;{art['source']}&nbsp;·&nbsp;{art['time_str']}
                </div>
            </div>""")
        st.markdown("".join(rows_html), unsafe_allow_html=True)

    st.markdown("</div></div>", unsafe_allow_html=True)
