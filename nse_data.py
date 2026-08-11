"""
nse_data.py — Arka Trades price/index data layer.

Replaces the old yfinance-only get_static / get_price / get_index in
app.py. Same function names, same return dict shapes, so app.py only
needs one line changed: `from nse_data import get_static, get_price,
get_index` instead of the inline yfinance versions.

── Why this file exists (read before changing anything) ────────────
nsepythonserver is the primary source because it pulls directly from
NSE — the actual exchange, not a third-party scrape of a news portal.
But it has two real, confirmed risks on Streamlit Cloud specifically:

  1. It shells out to `curl` internally instead of using Python's
     `requests` (CONFIRMED: verified by installing the package and
     reading its source directly — nse_get_index_quote() literally
     calls nsefetch(), which shells to curl). Streamlit Cloud's
     container may not have curl on PATH.
  2. NSE's own robots.txt is documented to block ALL web-server
     traffic (AWS, GCP, etc.) for the plain `nsepython` local
     edition — the server edition's curl-based workaround exists
     specifically because of this, but there's no guarantee it gets
     through Streamlit Cloud's specific network/region either.

Rather than gate the whole app on one untested assumption, EVERY
function here tries nsepythonserver first, and on ANY failure
(import error, network error, missing/malformed data, exception of
any kind) falls straight through to the yfinance path that used to
be the only path. The person using the app should never see a blank
"No data" card because of this migration — worst case, it behaves
exactly like it did before.

Set NSE_DEBUG = True below to have failures show *which* path was
used and why the primary failed, surfaced via st.caption on each
card in app.py (opt-in, off by default so it doesn't clutter the
live dashboard).
"""

import math
import time
import pandas as pd
import yfinance as yf
import streamlit as st

NSE_DEBUG = False  # flip True to see fallback reasons in the UI

# ── Try to import nsepythonserver once, at module load. If this
#    fails (not installed, or fails to import for any reason), every
#    function below skips straight to yfinance without retrying the
#    import on every call. ─────────────────────────────────────────
try:
    import nsepythonserver as _nse
    _NSE_AVAILABLE = True
    _NSE_IMPORT_ERROR = None
except Exception as _e:
    _NSE_AVAILABLE = False
    _NSE_IMPORT_ERROR = str(_e)


def _debug_note(source: str, detail: str = ""):
    """Optional, silent unless NSE_DEBUG is on."""
    if NSE_DEBUG:
        st.caption(f"🔧 data source: {source}" + (f" — {detail}" if detail else ""))


# ═══════════════════════════════════════════════════════════════════
# RSI — unchanged from the original, needed by both paths
# ═══════════════════════════════════════════════════════════════════

def calc_rsi(close, period=14):
    d = close.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, float("nan"))
    v = (100 - 100 / (1 + rs)).iloc[-1]
    return int(v) if pd.notna(v) else 0


def _values_are_sane(cur, pc):
    """Same sanity gate as the original app.py — reject NaN, inf,
    zero/negative prices, zero previous-close (div-by-zero risk)."""
    try:
        if cur is None or pc is None:
            return False
        cur, pc = float(cur), float(pc)
        if not (math.isfinite(cur) and math.isfinite(pc)):
            return False
        if cur <= 0 or pc <= 0:
            return False
        return True
    except (TypeError, ValueError):
        return False


# ═══════════════════════════════════════════════════════════════════
# STATIC DATA (PDH/PDL/RSI/spark) — 30-day history, one fetch covers
# both PDH/PDL and RSI/spark, so this stays a single function like
# the original. Cache TTL kept at 4hrs — this data only needs to
# refresh once a day (previous day's high/low doesn't change
# intraday), so it isn't part of the 30s live-refresh conversation.
# ═══════════════════════════════════════════════════════════════════

def _get_static_nse(sym: str):
    """Primary path: NSE's own quote-equity endpoint via nse_eq().
    Returns None on ANY problem so the caller falls back cleanly."""
    try:
        q = _nse.nse_eq(sym)
        price_info = q.get("priceInfo", {})
        week_hl = price_info.get("weekHighLow", {})
        # nse_eq gives current-day intraday high/low, previous close,
        # and 52-week data — but NOT a clean "previous day's" high/low
        # the way our 30d-history yfinance approach derives it. NSE's
        # quote-equity endpoint's `intraDayHighLow` is TODAY's, not
        # yesterday's — using it as PDH/PDL would be wrong on any day
        # after the first candle. For accurate PDH/PDL we still need
        # a short daily-history call. NSE doesn't expose a simple
        # "last N days OHLC" the way yfinance's .history() does
        # through this same library reliably, so PDH/PDL and the
        # spark line stay on the yfinance path even when nsepythonserver
        # succeeds for other fields — this is a deliberate partial
        # fallback, not a bug.
        if not price_info or "lastPrice" not in price_info:
            return None
        return None  # see note above — PDH/PDL intentionally deferred to yfinance for now
    except Exception:
        return None


def _get_static_yf(sym: str):
    try:
        h = yf.Ticker(sym + ".NS").history(period="30d", interval="1d")
        if len(h) < 16:
            return None
        prev = h.iloc[-2]
        return {
            "pdh": float(prev["High"]), "pdl": float(prev["Low"]),
            "prev_close": float(prev["Close"]), "rsi": calc_rsi(h["Close"]),
            "spark": [float(x) for x in h["Close"].tail(12).tolist()],
        }
    except Exception:
        return None


@st.cache_data(ttl=14400, show_spinner=False)
def get_static(sym: str):
    """
    PDH/PDL requires multi-day daily-candle history. NSE's
    quote-equity endpoint (what nse_eq/nse_quote expose) only gives
    TODAY's intraday high/low and 52-week data — not yesterday's
    candle specifically — so an accurate PDH/PDL needs a proper daily
    history series. yfinance's .history(period="30d") already does
    this reliably and is free, so PDH/PDL and the RSI/spark line stay
    on yfinance regardless of nsepythonserver's availability. This is
    intentional, not an oversight — see _get_static_nse's docstring.
    """
    result = _get_static_yf(sym)
    if result:
        _debug_note("yfinance", "static/PDH-PDL (by design — see docstring)")
    return result


# ═══════════════════════════════════════════════════════════════════
# LIVE PRICE — this is the one that changes cache behavior. Cache
# floor raised from 10s to 30s per the explicit decision above: NSE
# actively firewalls tight polling loops, and 10s was already
# aggressive even for yfinance.
# ═══════════════════════════════════════════════════════════════════

def _get_price_nse(sym: str, retries: int = 1):
    """
    Primary path: nse_eq() -> priceInfo.lastPrice + priceInfo.previousClose.
    These are real, standard fields on NSE's quote-equity JSON (this
    is NSE's own well-documented response schema, not a guess).
    One retry with a short backoff before giving up — NSE occasionally
    drops a single request without actively blocking the caller.
    """
    for attempt in range(retries + 1):
        try:
            q = _nse.nse_eq(sym)
            price_info = q.get("priceInfo", {})
            cur = price_info.get("lastPrice")
            prev_close = price_info.get("previousClose")
            if not _values_are_sane(cur, prev_close):
                return None
            cur, prev_close = float(cur), float(prev_close)
            return {
                "price": cur,
                "chg": ((cur - prev_close) / prev_close) * 100,
                "prev_close": prev_close,
            }
        except Exception:
            if attempt < retries:
                time.sleep(1.5)
                continue
            return None
    return None


def _get_price_yf(sym: str):
    try:
        intra = yf.Ticker(sym + ".NS").history(period="1d", interval="1m")
        if intra.empty:
            return None
        cur = float(intra["Close"].iloc[-1])
        daily = yf.Ticker(sym + ".NS").history(period="5d", interval="1d")
        if len(daily) < 2:
            return None
        prev_close = float(daily["Close"].iloc[-2])
        if not _values_are_sane(cur, prev_close):
            return None
        return {"price": cur, "chg": ((cur - prev_close) / prev_close) * 100, "prev_close": prev_close}
    except Exception:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def get_price(sym: str):
    """
    30s cache floor (was 10s). NSE's own docs warn that tight polling
    loops risk the exchange reinforcing its firewall against the
    caller — 30s is a meaningfully safer floor while still being
    close to live for a manual "Run Scan" workflow.
    """
    if _NSE_AVAILABLE:
        result = _get_price_nse(sym)
        if result:
            _debug_note("nsepythonserver", sym)
            return result
        _debug_note("yfinance (fallback)", f"{sym} — NSE path failed or returned nothing")
    else:
        _debug_note("yfinance (fallback)", f"nsepythonserver not available: {_NSE_IMPORT_ERROR}")
    return _get_price_yf(sym)


# ═══════════════════════════════════════════════════════════════════
# INDEX DATA — NIFTY 50, BANK NIFTY, SENSEX, MIDCAP, SMALLCAP, and
# global indices (S&P 500, DOW, GOLD — these stay on yfinance always,
# since nse_get_index_quote only covers NSE's own domestic indices).
# ═══════════════════════════════════════════════════════════════════

# nsepythonserver's nse_get_index_quote() reads NSE's live indices
# JSON and matches on `indexName` (confirmed by reading the actual
# function source — see project notes). These are the real NSE
# indexName strings for the indices this dashboard shows. Verified
# against NSE's standard indices naming convention; if any of these
# don't match exactly, that specific card falls back to yfinance
# automatically — it won't break the others.
_NSE_INDEX_NAMES = {
    "NIFTY 50": "NIFTY 50",
    "BANK NIFTY": "NIFTY BANK",
    "MIDCAP 100": "NIFTY MIDCAP 100",
    "SMALLCAP 100": "NIFTY SMLCAP 100",
    # SENSEX is a BSE index, not NSE — nsepythonserver only covers
    # NSE's own indices, so SENSEX always stays on yfinance.
}


def _get_index_nse(index_label: str):
    """Primary path for NSE-domestic indices only."""
    nse_name = _NSE_INDEX_NAMES.get(index_label)
    if not nse_name or not _NSE_AVAILABLE:
        return None
    try:
        q = _nse.nse_get_index_quote(nse_name)
        if not q:
            return None
        cur = q.get("last")
        pc = q.get("previousClose")
        if not _values_are_sane(cur, pc):
            return None
        cur, pc = float(cur), float(pc)
        # nse_get_index_quote's live-indices feed doesn't include a
        # sparkline series (that needs separate historical-data call)
        # — spark stays empty on the NSE path; app.py's sparkline()
        # already handles an empty list gracefully (returns "").
        return {"price": cur, "chg": ((cur - pc) / pc) * 100, "pts": cur - pc, "spark": [], "ticker_used": nse_name}
    except Exception:
        return None


def _fetch_index_history_yf(sym):
    try:
        h = yf.Ticker(sym).history(period="5d", interval="1d")
        if h.empty or len(h) < 2:
            return None
        return h
    except Exception:
        return None


def _get_index_yf(sym, fallback_syms=None):
    candidates = [sym] + (fallback_syms or [])
    for candidate in candidates:
        h = _fetch_index_history_yf(candidate)
        if h is None:
            continue
        try:
            cur = float(h["Close"].iloc[-1])
            pc = float(h["Close"].iloc[-2])
        except Exception:
            continue
        if not _values_are_sane(cur, pc):
            continue
        spark_raw = [float(x) for x in h["Close"].tolist()]
        spark = [x for x in spark_raw if math.isfinite(x)]
        if len(spark) < 2:
            continue
        return {"price": cur, "chg": ((cur - pc) / pc) * 100, "pts": cur - pc, "spark": spark, "ticker_used": candidate}
    return None


@st.cache_data(ttl=30, show_spinner=False)
def get_index(sym, fallback_syms=None, index_label=None):
    """
    index_label: the NSE-style display name ("NIFTY 50", "BANK NIFTY",
    etc.) used to look up the NSE path. If provided and it's an
    NSE-domestic index, tries nsepythonserver first, then yfinance
    with the original ticker/fallback chain. If not provided (or the
    index isn't NSE-domestic, e.g. S&P 500 / DOW / GOLD), goes
    straight to the yfinance path — unchanged from before.

    Cache TTL: 30s, matching get_price's new floor for consistency
    (was 60s before — tightened slightly since indices are lower-risk
    to poll than individual equities, but kept aligned with the
    price cache rather than introducing a third TTL value).
    """
    if index_label:
        result = _get_index_nse(index_label)
        if result:
            _debug_note("nsepythonserver", index_label)
            return result
        _debug_note("yfinance (fallback)", f"{index_label} — NSE path failed or not covered")
    return _get_index_yf(sym, fallback_syms)


# ── Ticker candidate lists — unchanged from original app.py ────────
MIDCAP_CANDIDATES = ["NIFTY_MIDCAP_100.NS", "^CRSMID", "^NIFTYMIDCAP100"]
SMALLCAP_CANDIDATES = ["^CNXSC", "^CNXSMALLCAP", "NIFTYSMLCAP100.NS"]
SP500_CANDIDATES = ["^GSPC"]
DOWJONES_CANDIDATES = ["^DJI"]
GOLD_CANDIDATES = ["GC=F"]
