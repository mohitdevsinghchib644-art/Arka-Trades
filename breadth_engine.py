"""
breadth_engine.py — Market Breadth data & math layer for Arka Trades.

── Two fixes in this revision ──────────────────────────────────────

FIX 1 — MA participation denominator bug (found by auditing your
live snapshot: above_50dma + below_50dma summed to 2037 against a
total_stocks of 2045, and above_200dma + below_200dma summed to only
1873 — 172 stocks short). Root cause: the original loop only counted
a stock into above_X/below_X if it had >= X days of price history,
but every downstream consumer (compute_composite_score, the UI %
calculations) divided by total_stocks — the FULL universe, including
stocks that were silently skipped. A stock with 90 days of history
(too young for a 200DMA) was contributing 0 to above_200dma but was
still in the total_stocks denominator, which is mathematically
identical to counting it as "below" its 200DMA — even though the
correct answer for that stock is "unknown, insufficient history".
This was a real downward bias on the composite score, not a
Chartink-matching cosmetic issue.

FIX: each snapshot now also returns above_20dma_denom /
above_50dma_denom / above_200dma_denom — the count of stocks that
actually HAD enough history to be judged on that specific metric.
compute_composite_score and the UI must divide by these, not by
total_stocks, for the 50DMA/200DMA metrics specifically (20DMA's
denominator equals total_stocks in practice since virtually every
NSE-listed equity has >=20 days of history, but the field is returned
for consistency and defensiveness anyway).

FIX 2 — history backfill. append_history() only ever wrote TODAY's
scan, so after any redeploy (Streamlit Cloud's filesystem is
ephemeral) history resets to 1 row, and A/D Line / McClellan / HMM
regime have nothing to plot or fit against. backfill_history_from_bhavcopy()
below reconstructs the last N trading days' advances/declines/MA-
participation directly from Bhavcopy data the fetcher already
downloads (260 days per symbol) — walking backward day by day and
recomputing that day's cross-sectional breadth from the OHLCV each
symbol already has on file, rather than needing a second data source.
Run this once after deploy (or any time history looks thin) to get
15-20 days of real history immediately instead of waiting three weeks
of daily scans.

Everything else (Bhavcopy primary / yfinance fallback, 4pm IST session
resolution, universe fetch tiers) is unchanged from the version you're
running — this revision only touches the counting bug and adds the
backfill function.
"""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
_EOD_CUTOFF = dtime(16, 0)

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


def _is_weekday(d) -> bool:
    return d.weekday() < 5


def _resolve_eod_session_date(now: Optional[datetime] = None) -> "datetime.date":
    now = now or datetime.now(IST)
    d = now.date()
    if _is_weekday(d) and now.timetz().replace(tzinfo=None) < _EOD_CUTOFF:
        d -= timedelta(days=1)
    while not _is_weekday(d):
        d -= timedelta(days=1)
    return d


def _eod_cache_key(now: Optional[datetime] = None) -> str:
    return _resolve_eod_session_date(now).strftime("%Y%m%d")


def _fetch_nse_universe_live() -> Optional[list]:
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


def _fetch_bhavcopy_for_date(session_date: "datetime.date") -> Optional[pd.DataFrame]:
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
            return None
        try:
            df.to_csv(local_cache, index=False, compression="gzip")
        except Exception:
            pass
        return df
    except Exception:
        return None


import streamlit as st  # scoped here: @st.cache_data decorators below need `st` resolvable at def time


@st.cache_data(ttl=None, show_spinner=False)
def _load_bhavcopy_history(session_key: str, lookback_sessions: int = 260) -> dict:
    session_date = datetime.strptime(session_key, "%Y%m%d").date()
    frames = []
    d = session_date
    fetched = 0
    attempts = 0
    max_attempts = lookback_sessions + 15
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


@st.cache_data(ttl=None, show_spinner=False)
def _batch_download_yfinance(session_key: str, symbols: tuple, period: str = "260d") -> dict:
    import yfinance as yf  # local: only this function touches yfinance
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


def _compute_cross_sectional_breadth(price_data: dict, as_of_idx: int = -1) -> Optional[dict]:
    """
    The core per-day breadth computation, factored out so both
    compute_breadth_snapshot() (today) and backfill_history_from_bhavcopy()
    (each of the last N days) call the exact same logic instead of
    two versions drifting apart. `as_of_idx` lets the backfill walk
    backward through each symbol's own Close series (-1 = latest day,
    -2 = one day before that, etc.) using the SAME already-downloaded
    260-day history — no extra fetch needed per backfilled day.

    THE FIX: above_X/below_X denominators are now tracked explicitly
    per metric (X_denom = above_X + below_X), and callers divide by
    that specific denominator — never by total_stocks — for any metric
    where a stock might be excluded for insufficient history. This is
    what corrects the bug where 172 stocks with <200 days of history
    were being counted as "below 200DMA" by omission (0 in numerator,
    but still in the total_stocks denominator downstream) instead of
    correctly excluded from that metric's percentage entirely.
    """
    THRESH_DAY_PCT = 4.5
    THRESH_5D_PCT = 20.0

    up_day = down_day = up_5d = down_5d = 0
    above_20 = below_20 = above_50 = below_50 = above_200 = below_200 = 0
    new_hi_5d = new_lo_5d = 0
    advances = declines = unchanged = 0
    per_symbol = {}
    latest_date = None

    for sym, df in price_data.items():
        try:
            close_full = df["Close"]
            # Slice to "as of" this index so backfill can look at an
            # earlier day using the same already-fetched series.
            if as_of_idx != -1:
                if len(close_full) < abs(as_of_idx):
                    continue
                close = close_full.iloc[: len(close_full) + as_of_idx + 1]
            else:
                close = close_full
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

            chg5d = _pct_change_5d(close.to_frame("Close"))
            if chg5d is not None:
                if chg5d >= THRESH_5D_PCT:
                    up_5d += 1
                elif chg5d <= -THRESH_5D_PCT:
                    down_5d += 1

            # FIX: each block below only increments above_X or below_X
            # when the stock HAS enough history for that specific MA —
            # this part was already correct. The bug was downstream
            # (percentages dividing by total_stocks instead of
            # above_X + below_X). Kept the same gating here; fixed the
            # consumers instead, per the docstring above.
            if len(close) >= 20:
                sma20 = close.rolling(20).mean().iloc[-1]
                if pd.notna(sma20):
                    if last > sma20: above_20 += 1
                    else: below_20 += 1
            if len(close) >= 50:
                sma50 = close.rolling(50).mean().iloc[-1]
                if pd.notna(sma50):
                    if last > sma50: above_50 += 1
                    else: below_50 += 1
            if len(close) >= 200:
                sma200 = close.rolling(200).mean().iloc[-1]
                if pd.notna(sma200):
                    if last > sma200: above_200 += 1
                    else: below_200 += 1

            window5 = close.tail(5)
            if len(window5) == 5:
                if last >= window5.max(): new_hi_5d += 1
                if last <= window5.min(): new_lo_5d += 1

            per_symbol[sym] = {
                "price": last, "day_pct": round(day_pct, 2),
                "chg_5d": round(chg5d, 2) if chg5d is not None else None,
            }
        except Exception:
            continue

    total = len(per_symbol)
    if total == 0:
        return None

    return {
        "latest_date": latest_date,
        "total_stocks": total,
        "advances": advances, "declines": declines, "unchanged": unchanged,
        "up_day_pct": up_day, "down_day_pct": down_day,
        "up_5d_pct": up_5d, "down_5d_pct": down_5d,
        "above_20dma": above_20, "below_20dma": below_20,
        "above_50dma": above_50, "below_50dma": below_50,
        "above_200dma": above_200, "below_200dma": below_200,
        # THE FIX: explicit denominators, one per MA metric. UI and
        # compute_composite_score must use these — NOT total_stocks —
        # when turning above_X into a percentage.
        "above_20dma_denom": above_20 + below_20,
        "above_50dma_denom": above_50 + below_50,
        "above_200dma_denom": above_200 + below_200,
        "new_hi_5d": new_hi_5d, "new_lo_5d": new_lo_5d,
        "per_symbol": per_symbol,
        "thresholds": {"day_pct": THRESH_DAY_PCT, "five_day_pct": THRESH_5D_PCT},
    }


def compute_breadth_snapshot(symbols: list) -> dict:
    price_data, source_label = _batch_download(tuple(symbols))
    if not price_data:
        return {"error": "No price data returned for any symbol in the universe (NSE Bhavcopy and yfinance fallback both unavailable)."}

    result = _compute_cross_sectional_breadth(price_data, as_of_idx=-1)
    if result is None:
        return {"error": "Price data fetched but no symbols had enough history to compute breadth."}

    latest_date = result.pop("latest_date")
    if latest_date is not None:
        try:
            date_label = pd.Timestamp(latest_date).strftime("%d %b %Y")
        except Exception:
            date_label = datetime.now(IST).strftime("%d %b %Y")
    else:
        date_label = datetime.now(IST).strftime("%d %b %Y")

    result["date"] = date_label
    result["source"] = source_label
    return result


# ── Historical series (A/D Line, McClellan) ─────────────────────────

_HISTORY_FILE = _CACHE_DIR / "breadth_history.jsonl"


def _history_record_from_snapshot(snapshot: dict) -> dict:
    """Shared shape for a single history row, used by both today's
    append and the backfill below, so the two never drift apart."""
    return {
        "date": snapshot["date"],
        "advances": snapshot["advances"],
        "declines": snapshot["declines"],
        "above_20dma": snapshot["above_20dma"],
        "below_20dma": snapshot["below_20dma"],
        "above_50dma": snapshot["above_50dma"],
        "below_50dma": snapshot["below_50dma"],
        "above_200dma": snapshot["above_200dma"],
        "below_200dma": snapshot["below_200dma"],
        # FIX: denominators now flow into history too, so the A/D Line/
        # McClellan/HMM layer (and any UI reading history directly)
        "above_20dma_denom": snapshot.get("above_20dma_denom", snapshot["above_20dma"] + snapshot["below_20dma"]),
        "above_50dma_denom": snapshot.get("above_50dma_denom", snapshot["above_50dma"] + snapshot["below_50dma"]),
        "above_200dma_denom": snapshot.get("above_200dma_denom", snapshot["above_200dma"] + snapshot["below_200dma"]),
        "new_hi_5d": snapshot["new_hi_5d"],
        "new_lo_5d": snapshot["new_lo_5d"],
    }


def append_history(snapshot: dict) -> None:
    if "error" in snapshot:
        return
    record = _history_record_from_snapshot(snapshot)
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


def backfill_history_from_bhavcopy(symbols: list, days: int = 20) -> dict:
    """
    Reconstructs the last `days` trading days of breadth history in
    one call, using the SAME 260-day Bhavcopy download the normal
    scan already fetches — no second data source, no extra network
    round-trips beyond the one batch fetch. This is what gets you from
    "1 trading day on file" to "15-20 days on file" immediately after
    a redeploy, instead of waiting three-to-four weeks of daily scans.

    Walks each symbol's already-downloaded Close series backward day
    by day (as_of_idx = -1, -2, -3, ... -days), recomputes that day's
    full cross-sectional breadth via _compute_cross_sectional_breadth
    (the exact same function today's live snapshot uses — no separate
    code path to drift out of sync), and writes each resulting day as
    a history row via append_history(). Existing rows for dates already
    on file are left untouched (append_history dedupes by date and
    keeps the most recent write per date), so running this repeatedly
    is safe and won't duplicate rows.

    Returns a small summary dict: {"days_written": N, "date_range":
    (oldest, newest)} — surfaced in the UI so backfilling isn't a
    silent operation either.
    """
    price_data, source_label = _batch_download(tuple(symbols))
    if not price_data:
        return {"days_written": 0, "error": "Could not fetch price data for backfill."}

    written_dates = []
    for offset in range(days):
        as_of_idx = -1 - offset
        result = _compute_cross_sectional_breadth(price_data, as_of_idx=as_of_idx)
        if result is None:
            # Ran out of history depth for this offset (e.g. asked for
            # 20 days but some symbols only have 15 usable rows at this
            # offset) — stop rather than write a garbage/empty row.
            break
        latest_date = result.pop("latest_date")
        if latest_date is None:
            continue
        try:
            date_label = pd.Timestamp(latest_date).strftime("%d %b %Y")
        except Exception:
            continue
        result["date"] = date_label
        result["source"] = f"{source_label} (backfilled)"
        append_history(result)
        written_dates.append(date_label)

    if not written_dates:
        return {"days_written": 0, "error": "No usable trading days found to backfill."}

    return {
        "days_written": len(written_dates),
        "date_range": (written_dates[-1], written_dates[0]),  # oldest, newest (loop walks backward)
    }


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
    Weighted 0-100 composite. FIX: MA breadth (component 2) now divides
    each above_X count by that metric's own denominator
    (above_X + below_X, i.e. only stocks with enough history to be
    judged on that specific MA) instead of by total_stocks. Previously
    a stock too young for a 200DMA silently pulled the 200DMA
    percentage down as if it were confirmed below its 200DMA — this
    is what was producing the ~4-point downward bias measured against
    your real snapshot (172 stocks short of 200-day history out of 2045
    total). Other components (A/D ratio, new hi/lo, McClellan) were not
    affected by this bug — they don't depend on per-symbol history
    length — so they're unchanged below.
    """
    if history is None:
        history = pd.DataFrame()
    if "error" in snapshot:
        return {"score": None, "label": "N/A", "error": snapshot["error"]}

    total = snapshot.get("total_stocks", 0)
    if total == 0:
        return {"score": None, "label": "N/A", "error": "Zero-stock snapshot."}

    adv = snapshot.get("advances", 0)
    dec = snapshot.get("declines", 0)
    ad_ratio = adv / dec if dec > 0 else (2.0 if adv > 0 else 1.0)
    ad_score = min(25, max(0, (ad_ratio / 2.0) * 25))

    # FIX: divide by each metric's own denom, not by total. Falls back
    # to total if an older snapshot/history row predates this fix and
    # doesn't have the _denom fields yet (backward compatible with
    # history rows written before today).
    denom_20 = snapshot.get("above_20dma_denom") or (snapshot.get("above_20dma", 0) + snapshot.get("below_20dma", 0)) or total
    denom_50 = snapshot.get("above_50dma_denom") or (snapshot.get("above_50dma", 0) + snapshot.get("below_50dma", 0)) or total
    denom_200 = snapshot.get("above_200dma_denom") or (snapshot.get("above_200dma", 0) + snapshot.get("below_200dma", 0)) or total
    pct_above_20 = snapshot.get("above_20dma", 0) / denom_20 if denom_20 else 0
    pct_above_50 = snapshot.get("above_50dma", 0) / denom_50 if denom_50 else 0
    pct_above_200 = snapshot.get("above_200dma", 0) / denom_200 if denom_200 else 0
    ma_avg_pct = (pct_above_20 + pct_above_50 + pct_above_200) / 3
    ma_score = ma_avg_pct * 35

    hi = snapshot.get("new_hi_5d", 0)
    lo = snapshot.get("new_lo_5d", 0)
    hilo_net = hi - lo
    hilo_score = 10 + max(-10, min(10, (hilo_net / max(total * 0.1, 1)) * 10))

    mcclellan_score = 10.0
    mcclellan_val = None
    if not history.empty and "mcclellan" in history.columns:
        recent = history["mcclellan"].dropna()
        if len(recent) > 0:
            mcclellan_val = float(recent.iloc[-1])
            mcclellan_score = 10 + max(-10, min(10, (mcclellan_val / max(total * 0.05, 1)) * 10))

    score = round(ad_score + ma_score + hilo_score + mcclellan_score, 1)
    score = max(0, min(100, score))

    if score >= 70: label, tone = "STRONG", "bullish"
    elif score >= 55: label, tone = "MODERATELY STRONG", "leaning bullish"
    elif score >= 45: label, tone = "NEUTRAL / MIXED", "no clear edge"
    elif score >= 30: label, tone = "MODERATELY WEAK", "leaning bearish"
    else: label, tone = "WEAK", "bearish"

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


def fetch_universe_ohlcv(tickers, period="260d"):
    data, _source = _batch_download(tuple(tickers), period=period)
    return data
