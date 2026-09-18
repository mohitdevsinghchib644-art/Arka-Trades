"""
breadth_engine.py — Daily Market Breadth data & math layer for Arka Trades.

Tracks daily:
- Advances / Declines
- % stocks above 20 DMA
- % stocks above 50 DMA
- % stocks above 200 DMA
- Day-over-day change in each MA breadth percentage
- 5-day momentum
- New 5-day highs/lows
- A/D Line
- McClellan Oscillator
- Composite breadth score
- Historical breadth backfill

Designed for DAILY / SWING trading, not intraday.
"""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import streamlit as st


# ═════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════

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

_HISTORY_FILE = _CACHE_DIR / "breadth_history.jsonl"


# ═════════════════════════════════════════════════════════════════════
# SESSION / DATE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _is_weekday(d) -> bool:
    return d.weekday() < 5


def _resolve_eod_session_date(
    now: Optional[datetime] = None
) -> "datetime.date":

    now = now or datetime.now(IST)

    d = now.date()

    if _is_weekday(d) and now.timetz().replace(tzinfo=None) < _EOD_CUTOFF:
        d -= timedelta(days=1)

    while not _is_weekday(d):
        d -= timedelta(days=1)

    return d


def _eod_cache_key(now: Optional[datetime] = None) -> str:
    return _resolve_eod_session_date(now).strftime("%Y%m%d")


# ═════════════════════════════════════════════════════════════════════
# NSE UNIVERSE
# ═════════════════════════════════════════════════════════════════════

def _fetch_nse_universe_live() -> Optional[list]:

    try:

        session = requests.Session()

        session.headers.update({
            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36",

            "Accept": "text/csv,application/csv,*/*",
        })

        session.get(
            "https://www.nseindia.com",
            timeout=6
        )

        resp = session.get(
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
            timeout=10,
        )

        if resp.status_code != 200 or len(resp.content) < 1000:
            return None

        df = pd.read_csv(io.BytesIO(resp.content))

        sym_col = next(
            (
                c for c in df.columns
                if c.strip().upper() == "SYMBOL"
            ),
            None
        )

        series_col = next(
            (
                c for c in df.columns
                if "SERIES" in c.strip().upper()
            ),
            None
        )

        if sym_col is None:
            return None

        if series_col is not None:

            df = df[
                df[series_col]
                .astype(str)
                .str.strip()
                .str.upper()
                == "EQ"
            ]

        symbols = (
            df[sym_col]
            .astype(str)
            .str.strip()
            .str.upper()
            .tolist()
        )

        symbols = [
            s for s in symbols
            if s and s != "NAN"
        ]

        if len(symbols) <= 500:
            return None

        return list(dict.fromkeys(symbols))

    except Exception:
        return None


def get_nse_universe(
    force_refresh: bool = False
) -> tuple[list, str]:

    if (
        not force_refresh
        and _UNIVERSE_CACHE_FILE.exists()
    ):

        try:

            cached = json.loads(
                _UNIVERSE_CACHE_FILE.read_text()
            )

            fetched_at = datetime.fromisoformat(
                cached["fetched_at"]
            )

            age_hours = (
                datetime.now(timezone.utc)
                - fetched_at
            ).total_seconds() / 3600

            if (
                age_hours < _UNIVERSE_CACHE_TTL_HOURS
                and len(cached["symbols"]) > 500
            ):

                return (
                    cached["symbols"],
                    f"cached ({age_hours:.0f}h old, "
                    f"{len(cached['symbols'])} stocks)"
                )

        except Exception:
            pass

    live = _fetch_nse_universe_live()

    if live:

        try:

            _UNIVERSE_CACHE_FILE.write_text(
                json.dumps({
                    "fetched_at":
                        datetime.now(timezone.utc).isoformat(),

                    "symbols":
                        live,
                })
            )

        except Exception:
            pass

        return (
            live,
            f"live NSE fetch ({len(live)} stocks)"
        )

    if _UNIVERSE_CACHE_FILE.exists():

        try:

            cached = json.loads(
                _UNIVERSE_CACHE_FILE.read_text()
            )

            if len(cached["symbols"]) > 500:

                return (
                    cached["symbols"],
                    f"stale cache "
                    f"({len(cached['symbols'])} stocks) "
                    f"— live fetch failed"
                )

        except Exception:
            pass

    return (
        LIQUID_FALLBACK,
        f"liquid fallback "
        f"({len(LIQUID_FALLBACK)} stocks) "
        f"— live + cache unavailable"
    )


# ═════════════════════════════════════════════════════════════════════
# NSE BHAVCOPY
# ═════════════════════════════════════════════════════════════════════

def _fetch_bhavcopy_for_date(
    session_date: "datetime.date"
) -> Optional[pd.DataFrame]:

    date_str = session_date.strftime("%Y%m%d")

    local_cache = (
        _BHAVCOPY_CACHE_DIR
        / f"{date_str}.csv.gz"
    )

    if local_cache.exists():

        try:

            df = pd.read_csv(
                local_cache,
                compression="gzip"
            )

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
            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36",

            "Accept":
                "application/zip,application/octet-stream,*/*",
        })

        session.get(
            "https://www.nseindia.com",
            timeout=6
        )

        resp = session.get(
            url,
            timeout=15
        )

        if (
            resp.status_code != 200
            or len(resp.content) < 1000
        ):
            return None

        with zipfile.ZipFile(
            io.BytesIO(resp.content)
        ) as zf:

            csv_names = [
                n for n in zf.namelist()
                if n.lower().endswith(".csv")
            ]

            if not csv_names:
                return None

            with zf.open(csv_names[0]) as f:
                df = pd.read_csv(f)

        df.columns = [
            c.strip().upper()
            for c in df.columns
        ]

        if (
            "SYMBOL" not in df.columns
            or "CLOSE" not in df.columns
        ):
            return None

        if len(df) < 500:
            return None

        try:

            df.to_csv(
                local_cache,
                index=False,
                compression="gzip"
            )

        except Exception:
            pass

        return df

    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════
# LOAD 260 DAYS OF DAILY PRICE DATA
# ═════════════════════════════════════════════════════════════════════

@st.cache_data(
    ttl=None,
    show_spinner=False
)
def _load_bhavcopy_history(
    session_key: str,
    lookback_sessions: int = 260
) -> dict:

    session_date = datetime.strptime(
        session_key,
        "%Y%m%d"
    ).date()

    frames = []

    d = session_date

    fetched = 0
    attempts = 0

    max_attempts = lookback_sessions + 20

    while (
        fetched < lookback_sessions
        and attempts < max_attempts
    ):

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

    full = pd.concat(
        frames,
        ignore_index=True
    )

    if "SERIES" in full.columns:

        full = full[
            full["SERIES"]
            .astype(str)
            .str.strip()
            .str.upper()
            == "EQ"
        ]

    open_col = next(
        (
            c for c in full.columns
            if c in ("OPEN", "OPEN_PRICE")
        ),
        None
    )

    high_col = next(
        (
            c for c in full.columns
            if c in ("HIGH", "HIGH_PRICE")
        ),
        None
    )

    low_col = next(
        (
            c for c in full.columns
            if c in ("LOW", "LOW_PRICE")
        ),
        None
    )

    close_col = next(
        (
            c for c in full.columns
            if c in ("CLOSE", "CLOSE_PRICE")
        ),
        None
    )

    vol_col = next(
        (
            c for c in full.columns
            if c in (
                "TTL_TRD_QNTY",
                "TOT_TRD_QTY",
                "VOLUME"
            )
        ),
        None
    )

    if close_col is None:
        return {}

    result = {}

    for sym, g in full.groupby("SYMBOL"):

        try:

            g = g.sort_values("_DATE")

            cols = {
                "Close":
                    pd.to_numeric(
                        g[close_col],
                        errors="coerce"
                    )
            }

            if open_col:
                cols["Open"] = pd.to_numeric(
                    g[open_col],
                    errors="coerce"
                )

            if high_col:
                cols["High"] = pd.to_numeric(
                    g[high_col],
                    errors="coerce"
                )

            if low_col:
                cols["Low"] = pd.to_numeric(
                    g[low_col],
                    errors="coerce"
                )

            if vol_col:
                cols["Volume"] = pd.to_numeric(
                    g[vol_col],
                    errors="coerce"
                )

            df = pd.DataFrame(cols)

            df = df.dropna(
                subset=["Close"]
            )

            df.index = pd.DatetimeIndex(
                g.loc[
                    df.index,
                    "_DATE"
                ]
            )

            if len(df) >= 20:

                result[
                    str(sym)
                    .strip()
                    .upper()
                ] = df

        except Exception:
            continue

    return result


# ═════════════════════════════════════════════════════════════════════
# YFINANCE FALLBACK
# ═════════════════════════════════════════════════════════════════════

@st.cache_data(
    ttl=None,
    show_spinner=False
)
def _batch_download_yfinance(
    session_key: str,
    symbols: tuple,
    period: str = "260d"
) -> dict:

    import yfinance as yf

    symbols = list(symbols)

    tickers = [
        s if str(s).endswith(".NS")
        else str(s) + ".NS"
        for s in symbols
    ]

    result = {}

    chunk_size = 200

    for i in range(
        0,
        len(tickers),
        chunk_size
    ):

        chunk = tickers[
            i:i + chunk_size
        ]

        try:

            data = yf.download(
                chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=True,
            )

        except Exception:
            continue

        for t in chunk:

            sym = (
                t[:-3]
                if t.endswith(".NS")
                else t
            )

            try:

                df = (
                    data
                    if len(chunk) == 1
                    else data[t]
                )

                df = df.dropna(
                    how="all"
                )

                if len(df) >= 20:
                    result[sym] = df

            except Exception:
                continue

    return result


def _batch_download(
    symbols,
    period: str = "260d"
) -> tuple[dict, str]:

    if isinstance(symbols, tuple):
        symbols = list(symbols)

    elif not isinstance(symbols, list):
        symbols = [symbols]

    session_key = _eod_cache_key()

    bhav_data = _load_bhavcopy_history(
        session_key,
        lookback_sessions=260
    )

    if bhav_data:

        wanted = {
            s.upper()
            for s in symbols
        }

        filtered = {
            sym: df
            for sym, df
            in bhav_data.items()
            if sym in wanted
        }

        if (
            len(filtered)
            >= max(
                50,
                len(wanted) * 0.3
            )
        ):

            return (
                filtered,
                f"NSE Bhavcopy "
                f"(official EOD, session "
                f"{session_key})"
            )

    yf_data = _batch_download_yfinance(
        session_key,
        tuple(symbols),
        period=period
    )

    return (
        yf_data,
        "yfinance "
        "(fallback — Bhavcopy unavailable)"
    )


# ═════════════════════════════════════════════════════════════════════
# BASIC HELPERS
# ═════════════════════════════════════════════════════════════════════

def _pct_change_5d(
    df: pd.DataFrame
) -> Optional[float]:

    if len(df) < 6:
        return None

    try:

        return (
            df["Close"].iloc[-1]
            / df["Close"].iloc[-6]
            - 1
        ) * 100

    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════
# CORE CROSS-SECTIONAL BREADTH
# ═════════════════════════════════════════════════════════════════════

def _compute_cross_sectional_breadth(
    price_data: dict,
    as_of_idx: int = -1
) -> Optional[dict]:

    THRESH_DAY_PCT = 4.5
    THRESH_5D_PCT = 20.0

    up_day = 0
    down_day = 0

    up_5d = 0
    down_5d = 0

    above_20 = 0
    below_20 = 0

    above_50 = 0
    below_50 = 0

    above_200 = 0
    below_200 = 0

    new_hi_5d = 0
    new_lo_5d = 0

    advances = 0
    declines = 0
    unchanged = 0

    per_symbol = {}

    latest_date = None

    for sym, df in price_data.items():

        try:

            close_full = df["Close"].dropna()

            if as_of_idx != -1:

                if len(close_full) < abs(as_of_idx):
                    continue

                close = close_full.iloc[
                    :len(close_full)
                    + as_of_idx
                    + 1
                ]

            else:
                close = close_full

            if len(close) < 2:
                continue

            last = float(
                close.iloc[-1]
            )

            prev = float(
                close.iloc[-2]
            )

            if prev:

                day_pct = (
                    last / prev - 1
                ) * 100

            else:
                day_pct = 0.0

            try:

                this_date = close.index[-1]

                if (
                    latest_date is None
                    or this_date > latest_date
                ):
                    latest_date = this_date

            except Exception:
                pass

            # ─────────────────────────────
            # ADVANCE / DECLINE
            # ─────────────────────────────

            if day_pct > 0.05:
                advances += 1

            elif day_pct < -0.05:
                declines += 1

            else:
                unchanged += 1

            # ─────────────────────────────
            # STRONG DAILY MOVES
            # ─────────────────────────────

            if day_pct >= THRESH_DAY_PCT:
                up_day += 1

            elif day_pct <= -THRESH_DAY_PCT:
                down_day += 1

            # ─────────────────────────────
            # 5-DAY MOMENTUM
            # ─────────────────────────────

            chg5d = _pct_change_5d(
                close.to_frame("Close")
            )

            if chg5d is not None:

                if chg5d >= THRESH_5D_PCT:
                    up_5d += 1

                elif chg5d <= -THRESH_5D_PCT:
                    down_5d += 1

            # ─────────────────────────────
            # 20 DMA
            # ─────────────────────────────

            if len(close) >= 20:

                sma20 = (
                    close
                    .rolling(20)
                    .mean()
                    .iloc[-1]
                )

                if pd.notna(sma20):

                    if last > sma20:
                        above_20 += 1
                    else:
                        below_20 += 1

            # ─────────────────────────────
            # 50 DMA
            # ─────────────────────────────

            if len(close) >= 50:

                sma50 = (
                    close
                    .rolling(50)
                    .mean()
                    .iloc[-1]
                )

                if pd.notna(sma50):

                    if last > sma50:
                        above_50 += 1
                    else:
                        below_50 += 1

            # ─────────────────────────────
            # 200 DMA
            # ─────────────────────────────

            if len(close) >= 200:

                sma200 = (
                    close
                    .rolling(200)
                    .mean()
                    .iloc[-1]
                )

                if pd.notna(sma200):

                    if last > sma200:
                        above_200 += 1
                    else:
                        below_200 += 1

            # ─────────────────────────────
            # 5-DAY HIGH / LOW
            # ─────────────────────────────

            window5 = close.tail(5)

            if len(window5) == 5:

                if last >= window5.max():
                    new_hi_5d += 1

                if last <= window5.min():
                    new_lo_5d += 1

            per_symbol[sym] = {
                "price":
                    last,

                "day_pct":
                    round(day_pct, 2),

                "chg_5d":
                    round(chg5d, 2)
                    if chg5d is not None
                    else None,
            }

        except Exception:
            continue

    total = len(per_symbol)

    if total == 0:
        return None

    # ═════════════════════════════════════
    # EXPLICIT MA DENOMINATORS
    # ═════════════════════════════════════

    denom20 = (
        above_20
        + below_20
    )

    denom50 = (
        above_50
        + below_50
    )

    denom200 = (
        above_200
        + below_200
    )

    # ═════════════════════════════════════
    # MA PERCENTAGES
    # ═════════════════════════════════════

    pct20 = (
        above_20 / denom20 * 100
        if denom20
        else 0
    )

    pct50 = (
        above_50 / denom50 * 100
        if denom50
        else 0
    )

    pct200 = (
        above_200 / denom200 * 100
        if denom200
        else 0
    )

    return {

        "latest_date":
            latest_date,

        "total_stocks":
            total,

        "advances":
            advances,

        "declines":
            declines,

        "unchanged":
            unchanged,

        "up_day_pct":
            up_day,

        "down_day_pct":
            down_day,

        "up_5d_pct":
            up_5d,

        "down_5d_pct":
            down_5d,

        "above_20dma":
            above_20,

        "below_20dma":
            below_20,

        "above_50dma":
            above_50,

        "below_50dma":
            below_50,

        "above_200dma":
            above_200,

        "below_200dma":
            below_200,

        "above_20dma_denom":
            denom20,

        "above_50dma_denom":
            denom50,

        "above_200dma_denom":
            denom200,

        # Daily percentage values.
        # These make historical display much easier.
        "above_20dma_pct":
            round(pct20, 2),

        "above_50dma_pct":
            round(pct50, 2),

        "above_200dma_pct":
            round(pct200, 2),

        "new_hi_5d":
            new_hi_5d,

        "new_lo_5d":
            new_lo_5d,

        "per_symbol":
            per_symbol,

        "thresholds": {
            "day_pct":
                THRESH_DAY_PCT,

            "five_day_pct":
                THRESH_5D_PCT,
        },
    }


# ═════════════════════════════════════════════════════════════════════
# TODAY'S SNAPSHOT
# ═════════════════════════════════════════════════════════════════════

def compute_breadth_snapshot(
    symbols: list
) -> dict:

    price_data, source_label = _batch_download(
        tuple(symbols)
    )

    if not price_data:

        return {
            "error":
                "No price data returned for any "
                "symbol in the universe."
        }

    result = _compute_cross_sectional_breadth(
        price_data,
        as_of_idx=-1
    )

    if result is None:

        return {
            "error":
                "Price data fetched but no symbols "
                "had enough history."
        }

    latest_date = result.pop(
        "latest_date"
    )

    if latest_date is not None:

        try:

            date_label = pd.Timestamp(
                latest_date
            ).strftime(
                "%d %b %Y"
            )

        except Exception:

            date_label = datetime.now(
                IST
            ).strftime(
                "%d %b %Y"
            )

    else:

        date_label = datetime.now(
            IST
        ).strftime(
            "%d %b %Y"
        )

    result["date"] = date_label

    result["source"] = source_label

    return result


# ═════════════════════════════════════════════════════════════════════
# HISTORY
# ═════════════════════════════════════════════════════════════════════

def _history_record_from_snapshot(
    snapshot: dict
) -> dict:

    return {

        "date":
            snapshot["date"],

        "advances":
            snapshot["advances"],

        "declines":
            snapshot["declines"],

        "above_20dma":
            snapshot["above_20dma"],

        "below_20dma":
            snapshot["below_20dma"],

        "above_50dma":
            snapshot["above_50dma"],

        "below_50dma":
            snapshot["below_50dma"],

        "above_200dma":
            snapshot["above_200dma"],

        "below_200dma":
            snapshot["below_200dma"],

        "above_20dma_denom":
            snapshot.get(
                "above_20dma_denom",
                snapshot["above_20dma"]
                + snapshot["below_20dma"]
            ),

        "above_50dma_denom":
            snapshot.get(
                "above_50dma_denom",
                snapshot["above_50dma"]
                + snapshot["below_50dma"]
            ),

        "above_200dma_denom":
            snapshot.get(
                "above_200dma_denom",
                snapshot["above_200dma"]
                + snapshot["below_200dma"]
            ),

        # Store percentages directly.
        "above_20dma_pct":
            snapshot.get(
                "above_20dma_pct"
            ),

        "above_50dma_pct":
            snapshot.get(
                "above_50dma_pct"
            ),

        "above_200dma_pct":
            snapshot.get(
                "above_200dma_pct"
            ),

        "new_hi_5d":
            snapshot["new_hi_5d"],

        "new_lo_5d":
            snapshot["new_lo_5d"],
    }


def append_history(
    snapshot: dict
) -> None:

    if "error" in snapshot:
        return

    record = _history_record_from_snapshot(
        snapshot
    )

    try:

        existing = []

        if _HISTORY_FILE.exists():

            existing = [
                json.loads(l)
                for l
                in _HISTORY_FILE
                .read_text()
                .splitlines()
                if l.strip()
            ]

        # Replace same date rather than duplicate.
        existing = [
            r for r in existing
            if r["date"] != record["date"]
        ]

        existing.append(record)

        existing.sort(
            key=lambda r:
                datetime.strptime(
                    r["date"],
                    "%d %b %Y"
                )
        )

        # Keep 180 trading sessions.
        existing = existing[-180:]

        _HISTORY_FILE.write_text(
            "\n".join(
                json.dumps(r)
                for r in existing
            )
        )

    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════
# BACKFILL
# ═════════════════════════════════════════════════════════════════════

def backfill_history_from_bhavcopy(
    symbols: list,
    days: int = 20
) -> dict:

    price_data, source_label = _batch_download(
        tuple(symbols)
    )

    if not price_data:

        return {
            "days_written": 0,
            "error":
                "Could not fetch price data "
                "for backfill."
        }

    written_dates = []

    for offset in range(days):

        as_of_idx = -1 - offset

        result = _compute_cross_sectional_breadth(
            price_data,
            as_of_idx=as_of_idx
        )

        if result is None:
            break

        latest_date = result.pop(
            "latest_date"
        )

        if latest_date is None:
            continue

        try:

            date_label = pd.Timestamp(
                latest_date
            ).strftime(
                "%d %b %Y"
            )

        except Exception:
            continue

        result["date"] = date_label

        result["source"] = (
            f"{source_label} (backfilled)"
        )

        append_history(result)

        written_dates.append(
            date_label
        )

    if not written_dates:

        return {
            "days_written": 0,
            "error":
                "No usable trading days "
                "found to backfill."
        }

    return {

        "days_written":
            len(written_dates),

        "date_range":
            (
                written_dates[-1],
                written_dates[0]
            ),
    }


# ═════════════════════════════════════════════════════════════════════
# LOAD HISTORY
# ═════════════════════════════════════════════════════════════════════

def load_history() -> pd.DataFrame:

    if not _HISTORY_FILE.exists():
        return pd.DataFrame()

    try:

        rows = [
            json.loads(l)
            for l
            in _HISTORY_FILE
            .read_text()
            .splitlines()
            if l.strip()
        ]

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        df["date"] = pd.to_datetime(
            df["date"],
            format="%d %b %Y"
        )

        df = (
            df
            .sort_values("date")
            .reset_index(drop=True)
        )

        # ═══════════════════════════════
        # BACKWARD COMPATIBILITY
        # ═══════════════════════════════

        # Older history files may not have
        # percentage columns.

        for ma in (
            "20",
            "50",
            "200"
        ):

            pct_col = (
                f"above_{ma}dma_pct"
            )

            above_col = (
                f"above_{ma}dma"
            )

            denom_col = (
                f"above_{ma}dma_denom"
            )

            if (
                pct_col not in df.columns
                and above_col in df.columns
            ):

                denom = (
                    df[denom_col]
                    if denom_col in df.columns
                    else (
                        df[above_col]
                        + df[
                            f"below_{ma}dma"
                        ]
                    )
                )

                df[pct_col] = (
                    df[above_col]
                    / denom.replace(
                        0,
                        pd.NA
                    )
                    * 100
                )

        # ═══════════════════════════════
        # DAY-OVER-DAY CHANGE
        # ═══════════════════════════════

        for ma in (
            "20",
            "50",
            "200"
        ):

            pct_col = (
                f"above_{ma}dma_pct"
            )

            change_col = (
                f"above_{ma}dma_change"
            )

            if pct_col in df.columns:

                df[change_col] = (
                    pd.to_numeric(
                        df[pct_col],
                        errors="coerce"
                    )
                    .diff()
                    .round(2)
                )

        return df

    except Exception:
        return pd.DataFrame()


# ═════════════════════════════════════════════════════════════════════
# A/D LINE + MCCLELLAN
# ═════════════════════════════════════════════════════════════════════

def compute_ad_line_and_mcclellan(
    history: pd.DataFrame
) -> pd.DataFrame:

    if history.empty:
        return history

    df = history.copy()

    df["net_advances"] = (
        df["advances"]
        - df["declines"]
    )

    df["ad_line"] = (
        df["net_advances"]
        .cumsum()
    )

    if len(df) >= 2:

        ema19 = (
            df["net_advances"]
            .ewm(
                span=19,
                adjust=False
            )
            .mean()
        )

        ema39 = (
            df["net_advances"]
            .ewm(
                span=39,
                adjust=False
            )
            .mean()
        )

        df["mcclellan"] = (
            ema19 - ema39
        )

    else:

        df["mcclellan"] = None

    return df


# ═════════════════════════════════════════════════════════════════════
# COMPOSITE SCORE
# ═════════════════════════════════════════════════════════════════════

def compute_composite_score(
    snapshot: dict,
    history: pd.DataFrame = None
) -> dict:

    if history is None:
        history = pd.DataFrame()

    if "error" in snapshot:

        return {
            "score": None,
            "label": "N/A",
            "error":
                snapshot["error"]
        }

    total = snapshot.get(
        "total_stocks",
        0
    )

    if total == 0:

        return {
            "score": None,
            "label": "N/A",
            "error":
                "Zero-stock snapshot."
        }

    # ═══════════════════════════════
    # A/D RATIO
    # ═══════════════════════════════

    adv = snapshot.get(
        "advances",
        0
    )

    dec = snapshot.get(
        "declines",
        0
    )

    ad_ratio = (
        adv / dec
        if dec > 0
        else (
            2.0
            if adv > 0
            else 1.0
        )
    )

    ad_score = min(
        25,
        max(
            0,
            (ad_ratio / 2.0) * 25
        )
    )

    # ═══════════════════════════════
    # MA BREADTH
    # ═══════════════════════════════

    denom20 = (
        snapshot.get(
            "above_20dma_denom"
        )
        or (
            snapshot.get(
                "above_20dma",
                0
            )
            +
            snapshot.get(
                "below_20dma",
                0
            )
        )
        or total
    )

    denom50 = (
        snapshot.get(
            "above_50dma_denom"
        )
        or (
            snapshot.get(
                "above_50dma",
                0
            )
            +
            snapshot.get(
                "below_50dma",
                0
            )
        )
        or total
    )

    denom200 = (
        snapshot.get(
            "above_200dma_denom"
        )
        or (
            snapshot.get(
                "above_200dma",
                0
            )
            +
            snapshot.get(
                "below_200dma",
                0
            )
        )
        or total
    )

    pct_above_20 = (
        snapshot.get(
            "above_20dma",
            0
        )
        / denom20
    )

    pct_above_50 = (
        snapshot.get(
            "above_50dma",
            0
        )
        / denom50
    )

    pct_above_200 = (
        snapshot.get(
            "above_200dma",
            0
        )
        / denom200
    )

    ma_avg_pct = (
        pct_above_20
        + pct_above_50
        + pct_above_200
    ) / 3

    ma_score = (
        ma_avg_pct * 35
    )

    # ═══════════════════════════════
    # NEW HIGH / LOW
    # ═══════════════════════════════

    hi = snapshot.get(
        "new_hi_5d",
        0
    )

    lo = snapshot.get(
        "new_lo_5d",
        0
    )

    hilo_net = hi - lo

    hilo_score = (
        10
        + max(
            -10,
            min(
                10,
                (
                    hilo_net
                    / max(
                        total * 0.1,
                        1
                    )
                ) * 10
            )
        )
    )

    # ═══════════════════════════════
    # MCCLELLAN
    # ═══════════════════════════════

    mcclellan_score = 10.0

    mcclellan_val = None

    if (
        not history.empty
        and "mcclellan"
        in history.columns
    ):

        recent = (
            history["mcclellan"]
            .dropna()
        )

        if len(recent) > 0:

            mcclellan_val = float(
                recent.iloc[-1]
            )

            mcclellan_score = (
                10
                + max(
                    -10,
                    min(
                        10,
                        (
                            mcclellan_val
                            / max(
                                total * 0.05,
                                1
                            )
                        ) * 10
                    )
                )
            )

    # ═══════════════════════════════
    # FINAL SCORE
    # ═══════════════════════════════

    score = round(
        ad_score
        + ma_score
        + hilo_score
        + mcclellan_score,
        1
    )

    score = max(
        0,
        min(
            100,
            score
        )
    )

    if score >= 70:

        label = "STRONG"
        tone = "bullish"

    elif score >= 55:

        label = "MODERATELY STRONG"
        tone = "leaning bullish"

    elif score >= 45:

        label = "NEUTRAL / MIXED"
        tone = "no clear edge"

    elif score >= 30:

        label = "MODERATELY WEAK"
        tone = "leaning bearish"

    else:

        label = "WEAK"
        tone = "bearish"

    return {

        "score":
            score,

        "label":
            label,

        "tone":
            tone,

        "breakdown": {

            "ad_ratio": {
                "value":
                    round(
                        ad_ratio,
                        2
                    ),

                "points":
                    round(
                        ad_score,
                        1
                    ),

                "max":
                    25,
            },

            "ma_breadth": {

                "value":
                    f"{ma_avg_pct * 100:.1f}%",

                "points":
                    round(
                        ma_score,
                        1
                    ),

                "max":
                    35,
            },

            "new_hilo": {

                "value":
                    f"{hi} hi / {lo} lo",

                "points":
                    round(
                        hilo_score,
                        1
                    ),

                "max":
                    20,
            },

            "mcclellan": {

                "value":
                    (
                        round(
                            mcclellan_val,
                            1
                        )
                        if mcclellan_val
                        is not None
                        else
                        "insufficient history"
                    ),

                "points":
                    round(
                        mcclellan_score,
                        1
                    ),

                "max":
                    20,
            },
        },
    }


# ═════════════════════════════════════════════════════════════════════
# EXTERNAL HELPER
# ═════════════════════════════════════════════════════════════════════

def fetch_universe_ohlcv(
    tickers,
    period="260d"
):

    data, _source = _batch_download(
        tuple(tickers),
        period=period
    )

    return data
