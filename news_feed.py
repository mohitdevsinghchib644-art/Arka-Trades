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
GOLD = "#3B82F6"
GREEN = "#22C55E"
RED = "#EF4444"
DARK2 = "#11161D"
DARK3 = "#1A212B"
BORDER = "#242D3A"
T2 = "#8C97A8"
IVORY = "#E8ECF2"
NEWS_EXPIRE = 20  # minutes between fetches per stock
MAX_COMBINED_ITEMS = 25  # cap on the persistent box — most recent N across all symbols


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
            })
        return results
    except Exception:
        return []


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
    """Refresh stale stocks (older than NEWS_EXPIRE minutes)."""
    _midnight_cleanup()
    now = time.time()
    for sym in watchlist:
        last = st.session_state["_news_fetched"].get(sym, 0)
        if now - last > NEWS_EXPIRE * 60:
            articles = _fetch_news_for_stock(sym)
            st.session_state["_news_cache"][sym] = articles
            st.session_state["_news_fetched"][sym] = now


def get_news_dot(sym: str) -> str:
    """Unchanged — still used by Scanner result cards to show a dot
    marker on individual stock cards. Kept for that purpose only;
    no longer drives a separate per-symbol news tab."""
    return "1" if st.session_state.get("_news_cache", {}).get(sym) else ""


def _combined_feed(watchlist: list[str]) -> list[dict]:
    """
    Merge every symbol's cached articles into one flat list, dedupe
    by link (same story can surface for multiple symbols), sort by
    publish time descending, cap at MAX_COMBINED_ITEMS.
    """
    cache = st.session_state.get("_news_cache", {})
    seen_links = set()
    combined = []
    for sym in watchlist:
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
    once per page load as a fixed bottom-left rectangle (positioning
    handled by the CSS wrapper in app.py; this function renders the
    box's CONTENTS only).

    Refresh interval raised from 10s to 30s to match the same
    NSE-safe polling posture as the price data — this fragment
    doesn't call NSE directly, but there's no reason for it to poll
    faster than the rest of the app now moves.
    """
    _ensure_news_state()
    if watchlist:
        refresh_news(watchlist)

    combined = _combined_feed(watchlist) if watchlist else []

    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-radius:12px;
         padding:14px 16px;height:100%;display:flex;flex-direction:column;">
        <div style="display:flex;align-items:center;justify-content:space-between;
             margin-bottom:10px;flex-shrink:0;">
            <span style="font-size:12px;font-weight:800;color:{IVORY};
                 letter-spacing:0.5px;">MARKET NEWS</span>
            <span style="font-size:9px;color:{T2};">LIVE</span>
        </div>
        <div id="arka-news-scroll" style="overflow-y:auto;flex:1;">
    """, unsafe_allow_html=True)

    if not watchlist:
        st.markdown(f"""<div style="font-size:11px;color:{T2};padding:8px 0;">
            Upload a watchlist to see news here.</div>""", unsafe_allow_html=True)
    elif not combined:
        st.markdown(f"""<div style="font-size:11px;color:{T2};padding:8px 0;">
            No stock news today yet. Checking every {NEWS_EXPIRE} min.</div>""", unsafe_allow_html=True)
    else:
        rows = "".join(
            f"""<div style="border-left:2px solid {GOLD};padding:6px 0 6px 10px;margin-bottom:8px;">
                <a href="{art['link']}" target="_blank" style="font-size:11.5px;font-weight:600;
                   color:{IVORY};text-decoration:none;line-height:1.4;display:block;">
                   {art['title']}
                </a>
                <div style="font-size:9.5px;color:{T2};margin-top:3px;">
                    <span style="color:{GOLD};font-weight:700;">{art['symbol']}</span>
                    &nbsp;·&nbsp;{art['source']}&nbsp;·&nbsp;{art['time_str']}
                </div>
            </div>"""
            for art in combined
        )
        st.markdown(rows, unsafe_allow_html=True)

    st.markdown("</div></div>", unsafe_allow_html=True)
