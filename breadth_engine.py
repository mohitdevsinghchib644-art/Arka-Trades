"""
breadth_engine.py — Market Breadth data & math layer for Arka Trades.

Computes the same family of numbers Chartink's Market Breadth dashboard
shows (advance/decline, 5d new highs/lows, above/below 20/50/200 DMA),
plus two derived series Chartink's raw table doesn't give you:
  - A/D Line: cumulative running sum of (advances - declines). Shows
    whether breadth is *building* or *fading*, not just today's count.
  - McClellan Oscillator: 19-day EMA minus 39-day EMA of daily net
    advances. Standard breadth-momentum indicator — leads index turns
    more often than the index itself.

── Data source (rewritten) ───────────────────────────────────────────
Primary: NSE's official EOD Bhavcopy (CM-UDiFF Common Bhavcopy Final).
This is NSE's own published closing-price file for every equity that
traded that session — not a third-party scrape, so it lines up with
what NSE-derived tools (Chartink, etc.) show, unlike yfinance which
has its own delay/adjustment quirks.

URL pattern (current since NSE's July 8, 2024 format migration —
the old cmDDMMMYYYYbhav.csv.zip path is discontinued, do not revert
to it):
  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip

Fallback: yfinance batch download (the original method), used only if
the Bhavcopy fetch fails for the resolved trading date (weekend/holiday
gaps, NSE endpoint hiccups, etc.) — kept so the app is never worse off
than before this rewrite, never silently substituted without a visible
source label.

── Refresh cadence (rewritten) ───────────────────────────────────────
Breadth data now refreshes once per trading day, at/after 4:00 PM IST,
Monday–Friday — not every 15 minutes. This matches how EOD breadth is
actually meant to be read (one clean snapshot per session) and avoids
hammering NSE with intraday polling for a number that shouldn't move
intraday anyway. Implemented via a cache key derived from the *resolved
trading session date* (see `_resolve_eod_session_date`), not a TTL —
Streamlit's cache naturally holds all day and rolls over exactly once,
right at 4pm, without a background job.

Universe fetch (symbol list) is unchanged: session-based live fetch ->
local CSV cache -> hardcoded liquid-list floor. Do not replace this
with a single un-cached call over a hardcoded list; NSE's URL has
broken this before and Streamlit Cloud RAM limits mean the whole
universe can't be pulled uncached on every rerun.
"""

import streamlit as st
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

# yfinance and streamlit are imported inside the specific functions that
# need them, not at module level. compute_ad_line_and_mcclellan and
# compute_composite_score below are pure pandas math with no network or
# Streamlit dependency — keeping those imports function-local means the
# math functions stay importable and unit-testable even if yfinance/
# streamlit are slow to import, fail to import, or aren't installed in
# whatever context is testing this file.

IST = timezone(timedelta(hours=5, minutes=30))
_EOD_CUTOFF = dtime(16, 0)  # 4:00 PM IST — data is considered "today's session" from here on

# Fallback floor if both live NSE fetch and local cache fail. Deliberately
# liquid, large-cap, index-representative — never the full universe, just
# enough that breadth numbers are directionally meaningful rather than
# empty. Extend this list over time rather than depending on it being
# reached in normal operation.
LIQUID_FALLBACK = [
    "RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","HINDUNILVR","ITC",
    "SBIN","BHARTIARTL","KOTAKBANK","LT","AXISBANK","BAJFINANCE","ASIANPAINT",
    "MARUTI","HCLTECH","SUNPHARMA","TITAN","ULTRACEMCO","WIPRO","NESTLEIND",
    "M&M","ADANIENT","POWERGRID","NTPC","TATAMOTORS","TATASTEEL","JSWSTEEL",
    "COALINDIA","TECHM","BAJAJFINSV","HINDALCO","DRREDDY","GRASIM","CIPLA",
    "EICHERMOT","BRITANNIA","DIVISLAB","HEROMOTOCO","APOLLOHOSP","INDUSINDBK",
    "TATACONSUM","BPCL","ONGC","SBILIFE","HDFCLIFE","BAJAJ-AUTO","UPL",
    "SHREECEM","ADANIPORTS","VEDL","GODREJCP","DABUR","PIDILITIND","SIEMENS",
    "DLF","AMBUJACEM","BANDHANBNK","BANKBARODA","CHOLAFIN","COLPALIN","GAIL",
    "HAVELLS","ICICIGI","ICICIPRULI","IOC","LUPIN","MARICO","MOTHERSON",
    "MUTHOOTFIN","NAUKRI","PAGEIND","PEL","PERSISTENT","PIIND","SAIL",
    "SRF","TATAPOWER","TORNTPHARM","TRENT","TVSMOTOR","ZOMATO",
]

_CACHE_DIR = Path(".cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_UNIVERSE_CACHE_FILE = _CACHE_DIR / "nse_universe.json"
_UNIVERSE_CACHE_TTL_HOURS = 24
_BHAVCOPY_CACHE_DIR = _CACHE_DIR / "bhavcopy"
_BHAVCOPY_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── EOD session resolution (4pm IST, Mon-Fri gating) ────────────────

def _is_weekday(d) -> bool:
    return d.weekday() < 5  # Mon=0 .. Fri=4


def _resolve_eod_session_date(now: Optional[datetime] = None) -> "datetime.date":
    """
    Resolves "which trading session's EOD data should be showing right
    now" — the core of the 4pm-IST-Mon-Fri refresh rule.

    Rules:
      - Before 4pm IST on a weekday: the most recently *completed*
        session hasn't published yet for today, so resolve to the
        previous trading day.
      - At/after 4pm IST on a weekday: today is the resolved session
        (Bhavcopy is typically live on NSE's archive by ~6-8pm IST in
        practice, but we intentionally gate the app's cache key at 4pm
        — right after close — rather than waiting on Bhavcopy's actual
        publish time, since the fallback path covers the gap if the
        file isn't up yet).
      - Weekends: always resolve back to the prior Friday (or earlier,
        skipping holidays isn't handled here — NSE holidays fall
        through to the yfinance fallback if Bhavcopy 404s, which is
        the correct behavior rather than guessing a holiday calendar).

    This function is pure (no I/O) and deliberately separate from the
    cache-key helper below so both the display layer and the cache
    layer resolve to the exact same date — a mismatch between "what
    date we say we're showing" and "what date we actually fetched"
    would be worse than the original bug.
    """
    now = now or datetime.now(IST)
    d = now.date()

    if _is_weekday(d) and now.timetz().replace(tzinfo=None) < _EOD_CUTOFF:
        # Weekday, before 4pm -> roll back to previous trading day
        d -= timedelta(days=1)

    while not _is_weekday(d):
        d -= timedelta(days=1)

    return d


def _eod_cache_key(now: Optional[datetime] = None) -> str:
    """
    String cache key that only changes once per trading day, at 4pm
    IST. Passed into the cached fetch functions below as an argument
    (not read from inside them) so Streamlit's cache_data correctly
    treats "same key" as "same result" — st.cache_data keys off
    function arguments, not wall-clock time, so this is what actually
    makes the once-a-day refresh work rather than the old ttl=900
    fifteen-minute expiry.
    """
    return _resolve_eod_session_date(now).strftime("%Y%m%d")


# ── NSE universe (symbol list) — unchanged from original ───────────

def _fetch_nse_universe_live() -> Optional[list]:
    """
    Attempt a live fetch of the NSE equity list. Wrapped tightly in
    try/except with a short timeout because this is a scraped endpoint,
    not a stable API — it WILL break again at some point. Every failure
    mode here falls through to the caller's next tier, it never raises.
    """
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36",
            "Accept": "text/csv,application/csv,*/*",
        })
        session.get("https://www.nseindia.com", timeout=6)
        resp = session.get(
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
            timeout=10,
        )
        if resp.status_code != 200 or len(resp.content) < 1000:
            return None
        df = pd.read_csv(io.BytesIO(resp.content))
        sym_col = next((c for c in df.columns if c.strip().upper() == "SYMBOL"), None)
        series_col = next((c for c in df.columns if "SERIES" in c.strip().upper()), None)
        if sym_col is None:
            return None
        if series_col is not None:
            df = df[df[series_col].astype(str).str.strip() == "EQ"]
        symbols = df[sym_col].astype(str).str.strip().str.upper().tolist()
        symbols = [s for s in symbols if s and s != "NAN"]
        return list(dict.fromkeys(symbols)) if len(symbols) > 500 else None
    except Exception:
        return None


def get_nse_universe(force_refresh: bool = False) -> tuple[list, str]:
    """
    Returns (symbol_list, source_label). source_label is surfaced in the
    UI so you always know whether breadth counts are computed off the
    live full universe, a cached one, or the liquid fallback floor —
    never silently.
    """
    if not force_refresh and _UNIVERSE_CACHE_FILE.exists():
        try:
            cached = json.loads(_UNIVERSE_CACHE_FILE.read_text())
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
            if age_hours < _UNIVERSE_CACHE_TTL_HOURS and len(cached["symbols"]) > 500:
                return cached["symbols"], f"cached ({age_hours:.0f}h old, {len(cached['symbols'])} stocks)"
        except Exception:
            pass

    live = _fetch_nse_universe_live()
    if live:
        try:
            _UNIVERSE_CACHE_FILE.write_text(json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "symbols": live,
            }))
        except Exception:
            pass
        return live, f"live NSE fetch ({len(live)} stocks)"

    if _UNIVERSE_CACHE_FILE.exists():
        try:
            cached = json.loads(_UNIVERSE_CACHE_FILE.read_text())
            if len(cached["symbols"]) > 500:
                return cached["symbols"], f"stale cache ({len(cached['symbols'])} stocks) — live fetch failed"
        except Exception:
            pass

    return LIQUID_FALLBACK, f"liquid fallback ({len(LIQUID_FALLBACK)} stocks) — live + cache both unavailable"


# ── NSE Bhavcopy (official EOD OHLCV) — new primary data source ────

def _fetch_bhavcopy_for_date(session_date: "datetime.date") -> Optional[pd.DataFrame]:
    """
    Downloads and parses NSE's official CM-UDiFF Bhavcopy zip for a
    single trading date. Returns a DataFrame with at least SYMBOL,
    SERIES, CLOSE columns (raw Bhavcopy column names vary in casing
    across NSE's own publishes, so both are normalized to uppercase
    immediately). Returns None on ANY failure — wrong weekday, market
    holiday, endpoint down, zip malformed, whatever. Never raises into
    the caller; the caller falls through to yfinance.

    A local on-disk cache is also written/read here (separate from
    Streamlit's cache_data) so that once a given date's Bhavcopy is
    successfully downloaded, the raw file survives a Streamlit Cloud
    process restart without a full re-fetch — the CSV.gz for one day's
    full NSE universe is a few hundred KB, cheap to keep.
    """
    date_str = session_date.strftime("%Y%m%d")
    local_cache = _BHAVCOPY_CACHE_DIR / f"{date_str}.csv.gz"

    if local_cache.exists():
        try:
            df = pd.read_csv(local_cache, compression="gzip")
            if len(df) > 500:
                return df
        except Exception:
            pass

    url = (
        "https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
    )
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36",
            "Accept": "application/zip,application/octet-stream,*/*",
        })
        session.get("https://www.nseindia.com", timeout=6)
        resp = session.get(url, timeout=15)
        if resp.status_code != 200 or len(resp.content) < 1000:
            return None

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                return None
            with zf.open(csv_names[0]) as f:
                df = pd.read_csv(f)

        df.columns = [c.strip().upper() for c in df.columns]
        if "SYMBOL" not in df.columns or "CLOSE" not in df.columns:
            return None
        if len(df) < 500:
            # A genuine full-universe Bhavcopy is always >1500 rows;
            # anything much smaller means we parsed a malformed/partial
            # file and should fall back rather than trust it.
            return None

        try:
            df.to_csv(local_cache, index=False, compression="gzip")
        except Exception:
            pass

        return df
    except Exception:
        return None


@st.cache_data(ttl=None, show_spinner=False)
def _load_bhavcopy_history(session_key: str, lookback_sessions: int = 260) -> dict:
    """
    Builds {symbol: DataFrame} of OHLCV history purely from NSE
    Bhavcopy files, walking backward trading-day by trading-day from
    the resolved session date until `lookback_sessions` days of data
    have been collected (260 covers the 200-DMA with holiday margin,
    same reasoning as the original yfinance period="260d").

    Cached with ttl=None — this is intentional and is what makes the
    "once per trading day, at 4pm IST" behavior work: the cache is
    keyed on `session_key` (from `_eod_cache_key()`), which itself only
    changes once a day at the 4pm rollover. Streamlit will keep serving
    this exact same cached result all day and all weekend without any
    network calls, and will only recompute the moment session_key
    ticks over to the next trading date. Do not add a ttl here; that
    would reintroduce the every-N-minutes refresh this rewrite removes.

    Returns {} (empty dict, not None) if even the most recent session's
    Bhavcopy can't be fetched — caller interprets empty dict as "primary
    source unavailable, use fallback" rather than crashing on missing
    keys.
    """
    session_date = datetime.strptime(session_key, "%Y%m%d").date()

    frames = []
    d = session_date
    fetched = 0
    attempts = 0
    max_attempts = lookback_sessions + 15  # generous slack for holidays

    while fetched < lookback_sessions and attempts < max_attempts:
        attempts += 1
        if _is_weekday(d):
            day_df = _fetch_bhavcopy_for_date(d)
            if day_df is not None:
                day_df = day_df.copy()
                day_df["_DATE"] = pd.Timestamp(d)
                frames.append(day_df)
                fetched += 1
        d -= timedelta(days=1)

    if not frames:
        return {}

    full = pd.concat(frames, ignore_index=True)

    series_col = next((c for c in full.columns if c == "SERIES"), None)
    if series_col:
        full = full[full[series_col].astype(str).str.strip() == "EQ"]

    open_col  = next((c for c in full.columns if c in ("OPEN", "OPEN_PRICE")), None)
    high_col  = next((c for c in full.columns if c in ("HIGH", "HIGH_PRICE")), None)
    low_col   = next((c for c in full.columns if c in ("LOW", "LOW_PRICE")), None)
    close_col = next((c for c in full.columns if c in ("CLOSE", "CLOSE_PRICE")), None)
    vol_col   = next((c for c in full.columns if c in ("TTL_TRD_QNTY", "TOT_TRD_QTY", "VOLUME")), None)

    if close_col is None:
        return {}

    result = {}
    for sym, g in full.groupby("SYMBOL"):
        g = g.sort_values("_DATE")
        cols = {"Close": g[close_col].astype(float)}
        if open_col:  cols["Open"]  = g[open_col].astype(float)
        if high_col:  cols["High"]  = g[high_col].astype(float)
        if low_col:   cols["Low"]   = g[low_col].astype(float)
        if vol_col:   cols["Volume"] = g[vol_col].astype(float)
        df = pd.DataFrame(cols)
        df.index = pd.DatetimeIndex(g["_DATE"])
        if len(df) >= 20:
            result[str(sym).strip().upper()] = df

    return result


# ── yfinance fallback (original method, now secondary) ─────────────

@st.cache_data(ttl=None, show_spinner=False)
def _batch_download_yfinance(session_key: str, symbols: tuple, period: str = "260d") -> dict:
    """
    yfinance fallback, used only when Bhavcopy is unavailable for the
    resolved session (holiday NSE hasn't published yet, endpoint
    down, etc.). Cache key includes session_key for the same reason as
    _load_bhavcopy_history: holds all day, rolls over once at 4pm IST,
    rather than the original ttl=900 fifteen-minute expiry.
    """
    symbols = list(symbols)
    tickers = [s if str(s).endswith(".NS") else str(s) + ".NS" for s in symbols]
    result = {}
    chunk_size = 200
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            data = yf.download(
                chunk, period=period, interval="1d",
                group_by="ticker", threads=True, progress=False,
                auto_adjust=True,
            )
        except Exception:
            continue
        for t in chunk:
            sym = t[:-3] if t.endswith(".NS") else t
            try:
                df = data if len(chunk) == 1 else data[t]
                df = df.dropna(how="all")
                if len(df) >= 20:
                    result[sym] = df
            except Exception:
                continue
    return result


def _batch_download(symbols, period: str = "260d") -> tuple[dict, str]:
    """
    Unified fetch entry point: tries NSE Bhavcopy first (source_label
    "NSE Bhavcopy (official EOD)"), falls back to yfinance if Bhavcopy
    returned nothing usable (source_label "yfinance (fallback)").
    Returns (price_data_dict, source_label) — the label is threaded
    through to compute_breadth_snapshot's return so the UI can always
    show which source actually backed a given snapshot, matching the
    same never-silently pattern get_nse_universe already uses.
    """
    if isinstance(symbols, tuple):
        symbols = list(symbols)
    elif not isinstance(symbols, list):
        symbols = [symbols]

    session_key = _eod_cache_key()

    bhav_data = _load_bhavcopy_history(session_key, lookback_sessions=260)
    if bhav_data:
        wanted = {s.upper() for s in symbols}
        filtered = {sym: df for sym, df in bhav_data.items() if sym in wanted}
        if len(filtered) >= max(50, len(wanted) * 0.3):
            # Accept Bhavcopy if it covers a sane fraction of the
            # requested universe. A near-total miss (e.g. symbol-list
            # mismatch) is treated as failure so we fall back rather
            # than silently return a near-empty breadth snapshot.
            return filtered, f"NSE Bhavcopy (official EOD, session {session_key})"

    yf_data = _batch_download_yfinance(session_key, tuple(symbols), period=period)
    return yf_data, "yfinance (fallback — Bhavcopy unavailable for this session)"


def _pct_change_5d(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 6:
        return None
    try:
        return (df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1) * 100
    except Exception:
        return None


def compute_breadth_snapshot(symbols: list) -> dict:
    """
    The core computation. Returns a dict with every metric in the
    Chartink screenshot (advances/declines, 5d up/down 20%+, above/below
    20/50/200 DMA) plus per-symbol classification used for the AI
    narrative and any drill-down UI.

    `snapshot["date"]` now reflects the actual resolved trading session
    (from the last row of the fetched price data), not
    datetime.now(IST) — the old version stamped wall-clock time, which
    meant the label could claim "25 Jul" while the underlying close was
    still the 24th's. `snapshot["source"]` reports which data source
    (Bhavcopy vs yfinance fallback) actually backed this snapshot.

    NOTE: "Up/Down 4.5%+ today" and "Up/Down 20%+ in 5d" thresholds are
    Chartink's own scan parameters, not a universal standard — they are
    reproduced here as configurable constants (THRESH_DAY_PCT,
    THRESH_5D_PCT below) so you can retune them without touching the
    computation logic.
    """
    THRESH_DAY_PCT = 4.5
    THRESH_5D_PCT = 20.0

    price_data, source_label = _batch_download(tuple(symbols))
    if not price_data:
        return {"error": "No price data returned for any symbol in the universe (NSE Bhavcopy and yfinance fallback both unavailable)."}

    up_day = down_day = up_5d = down_5d = 0
    above_20 = below_20 = above_50 = below_50 = above_200 = below_200 = 0
    new_hi_5d = new_lo_5d = 0
    advances = declines = unchanged = 0
    per_symbol = {}
    latest_date = None

    for sym, df in price_data.items():
        try:
            close = df["Close"]
            if len(close) < 2:
                continue
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            day_pct = (last / prev - 1) * 100 if prev else 0.0

            try:
                this_date = close.index[-1]
                if latest_date is None or this_date > latest_date:
                    latest_date = this_date
            except Exception:
                pass

            if day_pct > 0.05:
                advances += 1
            elif day_pct < -0.05:
                declines += 1
            else:
                unchanged += 1

            if day_pct >= THRESH_DAY_PCT:
                up_day += 1
            elif day_pct <= -THRESH_DAY_PCT:
                down_day += 1

            chg5d = _pct_change_5d(df)
            if chg5d is not None:
                if chg5d >= THRESH_5D_PCT:
                    up_5d += 1
                elif chg5d <= -THRESH_5D_PCT:
                    down_5d += 1

            if len(close) >= 20:
                sma20 = close.rolling(20).mean().iloc[-1]
                if pd.notna(sma20):
                    if last > sma20:
                        above_20 += 1
                    else:
                        below_20 += 1
            if len(close) >= 50:
                sma50 = close.rolling(50).mean().iloc[-1]
                if pd.notna(sma50):
                    if last > sma50:
                        above_50 += 1
                    else:
                        below_50 += 1
            if len(close) >= 200:
                sma200 = close.rolling(200).mean().iloc[-1]
                if pd.notna(sma200):
                    if last > sma200:
                        above_200 += 1
                    else:
                        below_200 += 1

            window5 = close.tail(5)
            if len(window5) == 5:
                if last >= window5.max():
                    new_hi_5d += 1
                if last <= window5.min():
                    new_lo_5d += 1

            per_symbol[sym] = {
                "price": last, "day_pct": round(day_pct, 2),
                "chg_5d": round(chg5d, 2) if chg5d is not None else None,
                "above_20dma": last > sma20 if len(close) >= 20 and pd.notna(sma20) else None,
                "above_50dma": last > sma50 if len(close) >= 50 and pd.notna(sma50) else None,
                "above_200dma": last > sma200 if len(close) >= 200 and pd.notna(sma200) else None,
            }
        except Exception:
            continue

    total = len(per_symbol)
    if total == 0:
        return {"error": "Price data fetched but no symbols had enough history to compute breadth."}

    if latest_date is not None:
        try:
            date_label = pd.Timestamp(latest_date).strftime("%d %b %Y")
        except Exception:
            date_label = datetime.now(IST).strftime("%d %b %Y")
    else:
        date_label = datetime.now(IST).strftime("%d %b %Y")

    return {
        "date": date_label,
        "source": source_label,
        "total_stocks": total,
        "advances": advances, "declines": declines, "unchanged": unchanged,
        "up_day_pct": up_day, "down_day_pct": down_day,
        "up_5d_pct": up_5d, "down_5d_pct": down_5d,
        "above_20dma": above_20, "below_20dma": below_20,
        "above_50dma": above_50, "below_50dma": below_50,
        "above_200dma": above_200, "below_200dma": below_200,
        "new_hi_5d": new_hi_5d, "new_lo_5d": new_lo_5d,
        "per_symbol": per_symbol,
        "thresholds": {"day_pct": THRESH_DAY_PCT, "five_day_pct": THRESH_5D_PCT},
    }


# ── Historical series (A/D Line, McClellan) ─────────────────────────

_HISTORY_FILE = _CACHE_DIR / "breadth_history.jsonl"


def append_history(snapshot: dict) -> None:
    """
    Appends today's snapshot to a local JSONL history file so A/D Line
    and McClellan (which need multi-day series, not a single snapshot)
    can be computed. Keyed by snapshot["date"] (the resolved session
    date), so calling this multiple times in the same session before
    4pm correctly overwrites rather than duplicates the same day's row.
    Streamlit Cloud's filesystem is ephemeral on redeploy — if you want
    this to survive redeploys, point this at a Supabase table instead
    (you already have Supabase wired for watchlist/alerts; a
    `breadth_history` table with the same delete+insert pattern as
    db_save_watchlist would work). Flagged here rather than silently
    building on a non-durable store.
    """
    if "error" in snapshot:
        return
    record = {
        "date": snapshot["date"],
        "advances": snapshot["advances"],
        "declines": snapshot["declines"],
        "above_20dma": snapshot["above_20dma"],
        "below_20dma": snapshot["below_20dma"],
        "above_50dma": snapshot["above_50dma"],
        "below_50dma": snapshot["below_50dma"],
        "above_200dma": snapshot["above_200dma"],
        "below_200dma": snapshot["below_200dma"],
        "new_hi_5d": snapshot["new_hi_5d"],
        "new_lo_5d": snapshot["new_lo_5d"],
    }
    try:
        existing = []
        if _HISTORY_FILE.exists():
            existing = [json.loads(l) for l in _HISTORY_FILE.read_text().splitlines() if l.strip()]
        existing = [r for r in existing if r["date"] != record["date"]]
        existing.append(record)
        existing.sort(key=lambda r: datetime.strptime(r["date"], "%d %b %Y"))
        existing = existing[-90:]
        _HISTORY_FILE.write_text("\n".join(json.dumps(r) for r in existing))
    except Exception:
        pass


def load_history() -> pd.DataFrame:
    if not _HISTORY_FILE.exists():
        return pd.DataFrame()
    try:
        rows = [json.loads(l) for l in _HISTORY_FILE.read_text().splitlines() if l.strip()]
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], format="%d %b %Y")
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def compute_ad_line_and_mcclellan(history: pd.DataFrame) -> pd.DataFrame:
    """
    A/D Line: cumsum(advances - declines). McClellan Oscillator:
    ema19(net_advances) - ema39(net_advances), computed on whatever
    history is available (McClellan is directionally usable before 39
    days accumulate, since pandas ewm adapts its effective window to
    however much data exists — it just won't be the standard-strength
    reading until enough days are on file). Both return NaN-safe
    columns if history is too short for a given field.
    """
    if history.empty:
        return history
    df = history.copy()
    df["net_advances"] = df["advances"] - df["declines"]
    df["ad_line"] = df["net_advances"].cumsum()
    if len(df) >= 2:
        ema19 = df["net_advances"].ewm(span=19, adjust=False).mean()
        ema39 = df["net_advances"].ewm(span=39, adjust=False).mean()
        df["mcclellan"] = ema19 - ema39
    else:
        df["mcclellan"] = None
    return df


# ── Composite strength score ─────────────────────────────────────────

def compute_composite_score(snapshot: dict, history: pd.DataFrame = None) -> dict:
    """
    Weighted 0-100 composite across four metric families. Returns the
    score plus a per-family breakdown so the UI can show *why*, not
    just the number — a bare score without the breakdown is not
    actionable, you'd have no way to tell if it's the MA breadth or the
    new hi/lo count dragging it down.

    Weights (sum to 100):
      - A/D ratio today               25
      - MA breadth (20/50/200 avg)    35
      - New 5d hi/lo differential     20
      - McClellan Oscillator sign     20  (0 if history too short)

    This is a deliberately transparent, rule-based score — not a
    trained model. Retune the weights below if your own read of past
    strong/weak regimes disagrees with how this scores them; that's a
    parameter change, not a rewrite.
    """
    if history is None:
        history = pd.DataFrame()

    if "error" in snapshot:
        return {"score": None, "label": "N/A", "error": snapshot["error"]}

    total = snapshot.get("total_stocks", snapshot.get("total", len(snapshot)))
    if total == 0:
        return {"score": None, "label": "N/A", "error": "Zero-stock snapshot."}
    # 1. A/D ratio -> 0-25
    adv = snapshot.get("advances", 0)
    dec = snapshot.get("declines", 0)
    ad_ratio = adv / dec if dec > 0 else (2.0 if adv > 0 else 1.0)
    ad_score = min(25, max(0, (ad_ratio / 2.0) * 25))

    # 2. MA breadth -> 0-35, average of the three % above
    pct_above_20 = snapshot.get("above_20dma", 0) / total if total else 0
    pct_above_50 = snapshot.get("above_50dma", 0) / total if total else 0
    pct_above_200 = snapshot.get("above_200dma", 0) / total if total else 0
    ma_avg_pct = (pct_above_20 + pct_above_50 + pct_above_200) / 3
    ma_score = ma_avg_pct * 35

    # 3. New hi/lo differential -> 0-20
    hi = snapshot.get("new_hi_5d", snapshot.get("new_52w_hi", 0))
    lo = snapshot.get("new_lo_5d", snapshot.get("new_52w_lo", 0))
    hilo_net = hi - lo
    hilo_score = 10 + max(-10, min(10, (hilo_net / max(total * 0.1, 1)) * 10))

    # 4. McClellan sign/magnitude -> 0-20 (neutral 10 if unavailable)
    mcclellan_score = 10.0
    mcclellan_val = None
    if not history.empty and "mcclellan" in history.columns:
        recent = history["mcclellan"].dropna()
        if len(recent) > 0:
            mcclellan_val = float(recent.iloc[-1])
            mcclellan_score = 10 + max(-10, min(10, (mcclellan_val / max(total * 0.05, 1)) * 10))

    score = round(ad_score + ma_score + hilo_score + mcclellan_score, 1)
    score = max(0, min(100, score))

    if score >= 70:
        label, tone = "STRONG", "bullish"
    elif score >= 55:
        label, tone = "MODERATELY STRONG", "leaning bullish"
    elif score >= 45:
        label, tone = "NEUTRAL / MIXED", "no clear edge"
    elif score >= 30:
        label, tone = "MODERATELY WEAK", "leaning bearish"
    else:
        label, tone = "WEAK", "bearish"

    return {
        "score": score,
        "label": label,
        "tone": tone,
        "breakdown": {
            "ad_ratio": {"value": round(ad_ratio, 2), "points": round(ad_score, 1), "max": 25},
            "ma_breadth": {"value": f"{ma_avg_pct*100:.1f}%", "points": round(ma_score, 1), "max": 35},
            "new_hilo": {"value": f"{hi} hi / {lo} lo", "points": round(hilo_score, 1), "max": 20},
            "mcclellan": {
                "value": round(mcclellan_val, 1) if mcclellan_val is not None else "insufficient history",
                "points": round(mcclellan_score, 1), "max": 20,
            },
        },
    }


def compute_daily_breadth_metrics(data, tickers):
    """Bridge wrapper for compatibility with breadth_page.py UI."""
    snapshot = compute_breadth_snapshot(tickers)
    if "error" in snapshot:
        return {
            "total_scanned": 0, "advances": 0, "declines": 0, "unchanged": 0,
            "net_advances": 0, "above_20dma": 0, "above_50dma": 0, "above_200dma": 0,
            "pct_above_20dma": 0.0, "pct_above_50dma": 0.0, "pct_above_200dma": 0.0,
            "up_5d": 0, "down_5d": 0, "new_52w_hi": 0, "new_52w_lo": 0
        }

    total = snapshot.get("total_stocks", 1)
    adv = snapshot.get("advances", 0)
    dec = snapshot.get("declines", 0)

    return {
        "total_scanned": total,
        "advances": adv,
        "declines": dec,
        "unchanged": snapshot.get("unchanged", 0),
        "net_advances": adv - dec,
        "above_20dma": snapshot.get("above_20dma", 0),
        "above_50dma": snapshot.get("above_50dma", 0),
        "above_200dma": snapshot.get("above_200dma", 0),
        "pct_above_20dma": round((snapshot.get("above_20dma", 0) / max(total, 1)) * 100, 1),
        "pct_above_50dma": round((snapshot.get("above_50dma", 0) / max(total, 1)) * 100, 1),
        "pct_above_200dma": round((snapshot.get("above_200dma", 0) / max(total, 1)) * 100, 1),
        "up_5d": snapshot.get("up_5d_pct", 0),
        "down_5d": snapshot.get("down_5d_pct", 0),
        "new_52w_hi": snapshot.get("new_hi_5d", 0),
        "new_52w_lo": snapshot.get("new_lo_5d", 0),
    }


def fetch_universe_ohlcv(tickers, period="260d"):
    """Bridge wrapper for breadth_page.py to fetch batch OHLCV data."""
    data, _source = _batch_download(tuple(tickers), period=period)
    return data
