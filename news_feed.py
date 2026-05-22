"""
news_feed.py — Arka Trades News Module
Drop this file next to app.py and import it.

Features:
  - Google News RSS via feedparser
  - 10-second auto-refresh using @st.fragment
  - Yellow dot 🟡 on stocks with news today
  - Auto-clears all news at midnight
  - Time shown on right side of each card
  - No full-page flicker
"""

import streamlit as st
import feedparser
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ── Constants ─────────────────────────────────────────────────
IST         = timezone(timedelta(hours=5, minutes=30))
GOLD        = "#C8A96A"
GREEN       = "#00B37A"
RED         = "#E84545"
DARK        = "#04080F"
DARK2       = "#060D1A"
DARK3       = "#091525"
BORDER      = "#0F2040"
T2          = "#8A9AB5"
IVORY       = "#F7EBE0"
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
    """Return '🟡' if stock has news today, else ''."""
    return "🟡" if st.session_state.get("_news_cache", {}).get(sym) else ""


# ── Main Fragment ─────────────────────────────────────────────

@st.fragment(run_every=10)
def news_panel(watchlist: list[str]):
    """
    Full news panel — reruns every 10 seconds independently.
    Paste   news_panel(st.session_state.watchlist)
    wherever you want the news section to appear.
    """
    _ensure_news_state()
    refresh_news(watchlist)

    # ── Header
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:16px;margin:32px 0 18px;">
        <div style="flex:1;height:1px;background:{BORDER};"></div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:17px;
             letter-spacing:5px;color:{GOLD};white-space:nowrap;">
             TODAY'S NEWS &nbsp;🟡 = new today</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>
    <div style="font-size:11px;color:{T2};margin-bottom:16px;text-align:right;">
        ⚡ Auto-refreshes every 10 sec · Resets at midnight IST
    </div>
    """, unsafe_allow_html=True)

    # ── Stock selector
    cache = st.session_state.get("_news_cache", {})

    # Stocks WITH news first, then rest
    with_news    = [s for s in watchlist if cache.get(s)]
    without_news = [s for s in watchlist if not cache.get(s)]
    ordered      = with_news + without_news

    if not ordered:
        st.info("Upload your watchlist to see news.")
        return

    # ── Tabs: one per stock (max 10 shown)
    display = ordered[:15]
    tabs    = st.tabs([
        f"{sym} {'🟡' if cache.get(sym) else ''}"
        for sym in display
    ])

    for tab, sym in zip(tabs, display):
        with tab:
            articles = cache.get(sym, [])
            if not articles:
                st.markdown(f"""
                <div style="background:{DARK2};border:1px solid {BORDER};
                     border-radius:12px;padding:24px;text-align:center;
                     color:{T2};font-size:14px;margin-top:8px;">
                    No news found for <strong>{sym}</strong> today.<br>
                    <span style="font-size:12px;opacity:.6;">
                        Checking every {NEWS_EXPIRE} minutes.</span>
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="font-size:12px;color:{GREEN};
                     margin-bottom:12px;font-weight:700;">
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
                             transition:border-color .2s;">
                            <a href="{art['link']}" target="_blank"
                               style="font-family:'Inter',sans-serif;
                                      font-weight:700;font-size:14px;
                                      color:{IVORY};text-decoration:none;
                                      line-height:1.5;">
                               {art['title']}
                            </a>
                            <div style="font-size:11px;color:{T2};margin-top:6px;
                                 font-weight:600;letter-spacing:1px;">
                                 📰 {art['source']}
                            </div>
                        </div>""", unsafe_allow_html=True)
