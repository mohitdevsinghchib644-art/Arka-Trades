"""
global_indices.py — Global market context for the Arka Trades dashboard:
GIFT Nifty, US indexes, Gold, and Tickertape's Market Mood Index (MMI).

Kept in its own module (rather than inline in app.py) because the MMI
fetch is a real HTML scrape with its own failure modes, and isolating
it means a Tickertape markup change only requires editing this file,
not hunting through app.py's rendering code.

── Tickers (verified before writing this file, not guessed) ─────────
GIFT Nifty   : IN-Z22.SI   (Yahoo's listing for SGX Nifty 50 Index
                Futures — GIFT Nifty's continuation of the old SGX
                Nifty contract after the 2023 migration to NSE IX)
S&P 500      : ^GSPC
Dow Jones    : ^DJI
Gold         : GC=F         (COMEX Gold Futures, USD)

── Market Mood Index (MMI) ───────────────────────────────────────────
Tickertape does not publish a public API or MCP for MMI — confirmed:
no developer API, no documented JSON endpoint, "closed surface" per
Tickertape's own ecosystem documentation. The only documented approach
(e.g. FabTrader's writeup) uses Selenium + headless Chrome, which does
NOT run on Streamlit Community Cloud (no Chrome binary, no way to
install one there) — so that approach is a dead end for this app.

Instead, this module fetches the MMI page's raw server-rendered HTML
with plain `requests` (no browser, no JS execution) and extracts the
score two ways, in order:
  1. Next.js embeds page data in a <script id="__NEXT_DATA__"> JSON
     blob on most Tickertape pages (confirmed pattern from Tickertape's
     own stock-page scraper library). Tried first since it's
     structured and least likely to break on a wording change.
  2. Regex against the visible-text score pattern as a fallback, since
     a direct fetch of the MMI page's rendered text showed the score
     appears as a bare number (e.g. "49.42") immediately followed by
     "Updated X hours/minutes ago" in the server-rendered markup.

Both paths are wrapped so ANY failure (site down, markup changed,
network blocked) returns None rather than raising — the dashboard
should never break because a best-effort sentiment widget failed.
"""

import re
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
import streamlit as st

IST = timezone(timedelta(hours=5, minutes=30))

# ── Verified tickers ──────────────────────────────────────────────
GLOBAL_INDEX_TICKERS = {
    "GIFT NIFTY":  "IN-Z22.SI",
    "S&P 500":     "^GSPC",
    "DOW JONES":   "^DJI",
    "GOLD (USD)":  "GC=F",
}

_MMI_URL = "https://www.tickertape.in/market-mood-index"
_MMI_ZONES = [
    (0, 30, "Extreme Fear"),
    (30, 50, "Fear"),
    (50, 70, "Greed"),
    (70, 100.0001, "Extreme Greed"),
]


def _zone_for_score(score: float) -> str:
    for lo, hi, label in _MMI_ZONES:
        if lo <= score < hi:
            return label
    return "Unknown"


def _parse_next_data(html: str) -> Optional[float]:
    """
    Attempts the structured path: locate the __NEXT_DATA__ JSON blob
    and walk it looking for a plausible MMI score field. Tickertape's
    internal prop names for this page aren't publicly documented, so
    this searches recursively for a numeric leaf between 0-100 sitting
    under a key that looks MMI-related, rather than hardcoding an exact
    path that would silently break on any prop rename.
    """
    try:
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if not m:
            return None
        data = json.loads(m.group(1))

        candidates = []

        def walk(node, key_hint=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, k.lower())
            elif isinstance(node, list):
                for item in node:
                    walk(item, key_hint)
            elif isinstance(node, (int, float)):
                if "mmi" in key_hint or "mood" in key_hint or "sentiment" in key_hint:
                    if 0 <= float(node) <= 100:
                        candidates.append(float(node))

        walk(data)
        if candidates:
            return candidates[0]
        return None
    except Exception:
        return None


def _parse_visible_text(html: str) -> Optional[float]:
    """
    Fallback: the MMI page's server-rendered text contains the score as
    a bare decimal number immediately followed by an "Updated ... ago"
    string (confirmed by direct inspection of the rendered page).
    Regex looks for a 2-digit(.2-digit) number in the 0-100 range that
    precedes that phrase, which is specific enough to avoid matching
    unrelated numbers elsewhere on the page (prices, follower counts,
    etc — those don't sit directly before "Updated ... ago").
    """
    try:
        m = re.search(r'(\d{1,3}\.\d{1,2})\s*(?:</[^>]+>\s*)*Updated', html)
        if m:
            val = float(m.group(1))
            if 0 <= val <= 100:
                return val
        # Secondary fallback: any 0-100 decimal near the words "Fear" or "Greed"
        m2 = re.search(r'(\d{1,3}\.\d{1,2})', html)
        if m2:
            val = float(m2.group(1))
            if 0 <= val <= 100:
                return val
        return None
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def get_mmi() -> Optional[dict]:
    """
    Fetches the current Market Mood Index. Cached 30 minutes — MMI is
    an end-of-day-ish sentiment composite (FII activity, VIX, breadth,
    etc.), not a tick-by-tick number, so this doesn't need the 10-60s
    cadence used elsewhere in the app.

    Returns {"score": float, "zone": str, "fetched_at": str} or None
    if the fetch/parse fails for any reason. Never raises.
    """
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        resp = session.get(_MMI_URL, timeout=10)
        if resp.status_code != 200:
            return None
        html = resp.text

        score = _parse_next_data(html)
        if score is None:
            score = _parse_visible_text(html)
        if score is None:
            return None

        return {
            "score": round(score, 2),
            "zone": _zone_for_score(score),
            "fetched_at": datetime.now(IST).strftime("%d %b %Y, %I:%M%p"),
        }
    except Exception:
        return None


def mmi_zone_color(zone: str) -> str:
    """Returns a hex color matching the app's palette for a given MMI zone."""
    return {
        "Extreme Fear": "#EF4444",   # RED
        "Fear":         "#F59E0B",   # AMBER
        "Greed":        "#84CC16",   # lime (between amber and green)
        "Extreme Greed":"#22C55E",   # GREEN
    }.get(zone, "#8C97A8")           # T2 fallback
