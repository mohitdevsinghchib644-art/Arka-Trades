"""
news_feed.py — Arka Trades News Module
Drop this file next to app.py and import it.

Features:
  - Google News RSS via feedparser
  - 10-second auto-refresh using @st.fragment
  - Shows ONLY stocks that have news today
  - Clean empty state when no watchlist stock is in the news
  - Auto-clears all news at midnight
  - Time shown on right side of each card
  - No full-page flicker
"""

import streamlit as st
import feedparser
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ── Constants (ChartX theme) ──────────────────────────────────
IST         = timezone(timedelta(hours=5, minutes=30))
GOLD        = "#4F8DFD"   # accent (electric blue)
GREEN       = "#10B981"
RED         = "#EF4444"
DARK        = "#0B0F17"
DARK2       = "#0F1522"
DARK3       = "#151D2E"
BORDER      = "#1E293B"
T2          = "#94A3B8"
IVORY       = "#E2E8F0"
NEWS_EXPIRE = 20   # minutes between fetches per stock


# ── Helpers ───────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_ist() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _format_time(pub_dt: datetime) -> str:
    """Return human-friendly time string."""
    now   = _now_ist()
    diff  = now - pub_dt.astimezone(IST)
    secs  = int(diff.total_seconds())

    if secs < 60:
        return "Just now"
    elif secs < 3600:
        m = secs // 60
        return f"{m} min ago"
    elif secs < 86400:
        h = secs // 3600
        return f"{h} hr ago"
    else:
        return pub_dt.astimezone(IST).strftime("%d %b")


def _parse_pub(entry) -> datetime | None:
    """Parse RSS published date to aware datetime."""
    raw = entry.get("published", entry.get("updated", ""))
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


def _fetch_news_for_stock(symbol: str) -> list[dict]:
    """
    Fetch today's news for one stock from Google News RSS.
    Returns list of dicts: {title, link, source, pub_dt, time_str}
    """
    today = _today_ist()
    query = symbol.replace("&", "and").replace(" ", "+")
    url   = (
        f"https://news.google.com/rss/search?"
        f"q={query}+NSE+India+stock"
        f"&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        feed    = feedparser.parse(url)
        results = []
        for entry in feed.entries[:10]:
            pub_dt = _parse_pub(entry)
            if not pub_dt:
                continue
            pub_ist = pub_dt.astimezone(IST)
            # Only today's news
            if pub_ist.strftime("%Y-%m-%d") != today:
                continue
            results.append({
                "title":    entry.get("title", "No title"),
                "link":     entry.get("link", "#"),
                "source":   entry.get("source", {}).get("title", "News"),
                "pub_dt":   pub_ist,
                "time_str": _format_time(pub_ist),
            })
        return results
    except Exception:
        return []


def _midnight_cleanup():
    """Clear all cached news if day changed."""
    today = _today_ist()
    if st.session_state.get("_news_date") != today:
        st.session_state["_news_cache"]   = {}
        st.session_state["_news_fetched"] = {}
        st.session_state["_news_date"]    = today


def _ensure_news_state():
    """Initialise session state keys."""
    for k, v in {
        "_news_cache":   {},   # sym -> [articles]
        "_news_fetched": {},   # sym -> timestamp of last fetch
        "_news_date":    _today_ist(),
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


def refresh_news(watchlist: list[str]):
    """
    Refresh stale stocks (older than NEWS_EXPIRE minutes).
    Call this inside the fragment so it runs on every 10-s tick.
    """
    _midnight_cleanup()
    now = time.time()
    for sym in watchlist:
        last = st.session_state["_news_fetched"].get(sym, 0)
        if now - last > NEWS_EXPIRE * 60:
            articles = _fetch_news_for_stock(sym)
            st.session_state["_news_cache"][sym]   = articles
            st.session_state["_news_fetched"][sym] = now


def get_news_dot(sym: str) -> str:
    """Return truthy marker if stock has news today, else '' (no emoji)."""
    return "1" if st.session_state.get("_news_cache", {}).get(sym) else ""


# ── Main Fragment ─────────────────────────────────────────────

@st.fragment(run_every=10)
def news_panel(watchlist: list[str]):
    """
    News panel — reruns every 10 seconds independently.
    Shows ONLY the stocks that have news today.
    """
    _ensure_news_state()
    refresh_news(watchlist)

    # ── Header
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;margin:32px 0 6px;">
        <div style="font-family:'Plus Jakarta Sans','Inter',sans-serif;font-size:17px;
             font-weight:800;color:{IVORY};white-space:nowrap;">Today's News</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
        <div style="font-size:11px;color:{T2};white-space:nowrap;">
            Auto-refreshes every 10s · Resets at midnight IST</div>
    </div>
    """, unsafe_allow_html=True)

    if not watchlist:
        st.info("Upload your watchlist to see news.")
        return

    cache     = st.session_state.get("_news_cache", {})
    with_news = [s for s in watchlist if cache.get(s)]

    # ── Empty state: no watchlist stock is in the news
    if not with_news:
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-radius:12px;
             padding:36px 24px;text-align:center;margin-top:12px;">
            <div style="font-size:14px;font-weight:700;color:{IVORY};margin-bottom:6px;">
                No stock news right now</div>
            <div style="font-size:12px;color:{T2};line-height:1.7;">
                None of the {len(watchlist)} stocks in this watchlist have news today.<br>
                Checking again every {NEWS_EXPIRE} minutes.</div>
        </div>""", unsafe_allow_html=True)
        return

    # ── Tabs: ONLY stocks with news (max 15 shown)
    display = with_news[:15]
    tabs    = st.tabs([f"{sym} ({len(cache.get(sym, []))})" for sym in display])

    for tab, sym in zip(tabs, display):
        with tab:
            articles = cache.get(sym, [])

            st.markdown(f"""
            <div style="font-size:12px;color:{GREEN};
                 margin:10px 0 12px;font-weight:700;">
                 {len(articles)} article{'s' if len(articles)>1 else ''} today
            </div>""", unsafe_allow_html=True)

            for art in articles:
                # Left: title+source | Right: time
                col_left, col_right = st.columns([5, 1])

                with col_right:
                    st.markdown(f"""
                    <div style="text-align:right;padding-top:14px;">
                        <div style="font-family:'JetBrains Mono',monospace;
                             font-size:11px;color:{T2};white-space:nowrap;">
                             {art['time_str']}</div>
                    </div>""", unsafe_allow_html=True)

                with col_left:
                    st.markdown(f"""
                    <div style="background:{DARK2};border:1px solid {BORDER};
                         border-left:3px solid {GOLD};border-radius:10px;
                         padding:14px 16px;margin-bottom:8px;
                         box-shadow:0 1px 3px rgba(0,0,0,.3);">
                        <a href="{art['link']}" target="_blank"
                           style="font-family:'Plus Jakarta Sans','Inter',sans-serif;
                                  font-weight:700;font-size:14px;
                                  color:{IVORY};text-decoration:none;
                                  line-height:1.5;">
                           {art['title']}
                        </a>
                        <div style="font-size:11px;color:{T2};margin-top:6px;
                             font-weight:600;letter-spacing:1px;">
                             {art['source']}
                        </div>
                    </div>""", unsafe_allow_html=True)
