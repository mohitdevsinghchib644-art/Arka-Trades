"""
Arka Trades — Daily Market Breadth Engine

Performance-first DAILY/EOD breadth engine.

Important design decision:
- Opening F7 Market Breadth does NOT download market data.
- Historical breadth is read from a tiny local JSONL file.
- Network work happens only when UPDATE TODAY / BACKFILL is pressed.
- Price history is fetched from Yahoo Finance in parallel chunks instead of
  downloading hundreds of NSE bhavcopy files one-by-one.

Metrics:
- Advances / declines
- Stocks above 20 / 50 / 200 DMA
- Eligible denominator for each DMA
- New 5-day highs / lows
- Historical daily snapshots
- A/D Line + McClellan
- Composite score (kept for compatibility)
"""

from __future__ import annotations

import json
import os
import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
EOD_CUTOFF = dtime(16, 0)

CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_FILE = CACHE_DIR / "breadth_history.jsonl"
CLOSE_CACHE_FILE = CACHE_DIR / "breadth_closes_1y.pkl"
UNIVERSE_CACHE_FILE = CACHE_DIR / "nse_universe.json"

UNIVERSE_CACHE_HOURS = 24
CLOSE_CACHE_HOURS = 12

YF_PERIOD = "1y"
YF_CHUNK_SIZE = 120
YF_MAX_WORKERS = 6

DEFAULT_BACKFILL_DAYS = 20
MAX_HISTORY_ROWS = 180

LIQUID_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "HCLTECH", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND", "M&M", "ADANIENT",
    "POWERGRID", "NTPC", "TATAMOTORS", "TATASTEEL", "JSWSTEEL",
    "COALINDIA", "TECHM", "BAJAJFINSV", "HINDALCO", "DRREDDY",
    "GRASIM", "CIPLA", "EICHERMOT", "BRITANNIA", "DIVISLAB",
    "HEROMOTOCO", "APOLLOHOSP", "INDUSINDBK", "TATACONSUM", "BPCL",
    "ONGC", "SBILIFE", "HDFCLIFE", "BAJAJ-AUTO", "UPL", "SHREECEM",
    "ADANIPORTS", "VEDL", "GODREJCP", "DABUR", "PIDILITIND", "SIEMENS",
    "DLF", "AMBUJACEM", "BANKBARODA", "CHOLAFIN", "COLPALIN", "GAIL",
    "HAVELLS", "ICICIGI", "ICICIPRULI", "IOC", "LUPIN", "MARICO",
    "MOTHERSON", "MUTHOOTFIN", "NAUKRI", "PAGEIND", "PEL", "PERSISTENT",
    "PIIND", "SAIL", "SRF", "TATAPOWER", "TORNTPHARM", "TRENT",
    "TVSMOTOR", "ZOMATO",
]


# ---------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------

def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def _latest_completed_session(now: Optional[datetime] = None) -> date:
    now = now or datetime.now(IST)
    d = now.date()

    if _is_weekday(d) and now.time() < EOD_CUTOFF:
        d -= timedelta(days=1)

    while not _is_weekday(d):
        d -= timedelta(days=1)

    return d


def _session_key(now: Optional[datetime] = None) -> str:
    return _latest_completed_session(now).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------
# NSE UNIVERSE — CACHED, NEVER BLOCKS PAGE RENDER
# ---------------------------------------------------------------------

def _fetch_nse_universe_live() -> Optional[list[str]]:
    try:
        import requests

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/csv,application/csv,*/*",
        })

        session.get("https://www.nseindia.com", timeout=5)
        r = session.get(
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
            timeout=10,
        )

        if r.status_code != 200 or len(r.content) < 1000:
            return None

        import io
        df = pd.read_csv(io.BytesIO(r.content))
        df.columns = [str(c).strip().upper() for c in df.columns]

        if "SYMBOL" not in df.columns:
            return None

        if "SERIES" in df.columns:
            df = df[
                df["SERIES"].astype(str).str.strip().str.upper().eq("EQ")
            ]

        symbols = (
            df["SYMBOL"].astype(str).str.strip().str.upper()
            .replace("NAN", pd.NA).dropna().tolist()
        )

        symbols = list(dict.fromkeys(symbols))
        return symbols if len(symbols) > 500 else None

    except Exception:
        return None


def get_nse_universe(force_refresh: bool = False) -> tuple[list[str], str]:
    if not force_refresh and UNIVERSE_CACHE_FILE.exists():
        try:
            payload = json.loads(UNIVERSE_CACHE_FILE.read_text())
            fetched = datetime.fromisoformat(payload["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            age = (
                datetime.now(timezone.utc) - fetched
            ).total_seconds() / 3600

            if age < UNIVERSE_CACHE_HOURS and len(payload.get("symbols", [])) > 500:
                syms = payload["symbols"]
                return syms, f"cached · {len(syms)} NSE EQ stocks"
        except Exception:
            pass

    live = _fetch_nse_universe_live()
    if live:
        try:
            UNIVERSE_CACHE_FILE.write_text(
                json.dumps({
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "symbols": live,
                })
            )
        except Exception:
            pass
        return live, f"live NSE universe · {len(live)} stocks"

    if UNIVERSE_CACHE_FILE.exists():
        try:
            payload = json.loads(UNIVERSE_CACHE_FILE.read_text())
            syms = payload.get("symbols", [])
            if len(syms) > 500:
                return syms, f"stale cached NSE universe · {len(syms)} stocks"
        except Exception:
            pass

    return LIQUID_FALLBACK, f"liquid fallback · {len(LIQUID_FALLBACK)} stocks"


# ---------------------------------------------------------------------
# YFINANCE DOWNLOAD
# ---------------------------------------------------------------------

def _extract_close_from_yf(data: pd.DataFrame, ticker: str) -> Optional[pd.Series]:
    """Extract a single Close series across yfinance's different layouts."""
    try:
        if data is None or data.empty:
            return None

        # One ticker commonly gives simple columns.
        if isinstance(data.columns, pd.Index):
            if "Close" in data.columns:
                s = data["Close"]
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0]
                return pd.to_numeric(s, errors="coerce").dropna()

        # MultiIndex: either (Price, Ticker) or (Ticker, Price).
        if isinstance(data.columns, pd.MultiIndex):
            candidates = [
                ("Close", ticker),
                (ticker, "Close"),
                ("Close", ticker.upper()),
                (ticker.upper(), "Close"),
            ]
            for key in candidates:
                if key in data.columns:
                    return pd.to_numeric(
                        data[key], errors="coerce"
                    ).dropna()

            # Last resort: find a column whose second/first component is Close.
            for key in data.columns:
                if isinstance(key, tuple) and "Close" in key:
                    return pd.to_numeric(
                        data[key], errors="coerce"
                    ).dropna()

    except Exception:
        return None

    return None


def _download_yf_chunk(tickers: list[str]) -> dict[str, pd.Series]:
    try:
        import yfinance as yf

        data = yf.download(
            tickers=tickers,
            period=YF_PERIOD,
            interval="1d",
            auto_adjust=True,
            group_by="column",
            threads=False,
            progress=False,
            timeout=20,
        )

        out: dict[str, pd.Series] = {}
        for ticker in tickers:
            s = _extract_close_from_yf(data, ticker)
            if s is not None and len(s) >= 30:
                # Use naive dates to make all chunks align cleanly.
                s.index = pd.to_datetime(s.index).tz_localize(None)
                out[ticker[:-3] if ticker.endswith(".NS") else ticker] = s
        return out

    except Exception:
        return {}


@st.cache_data(ttl=CLOSE_CACHE_HOURS * 3600, show_spinner=False)
def _download_close_history_cached(
    symbols: tuple[str, ...],
    cache_key: str,
) -> dict[str, pd.Series]:
    """Cached expensive network operation. cache_key changes once per day."""
    del cache_key

    # Disk cache survives a Streamlit rerun / process in many deployments.
    try:
        if CLOSE_CACHE_FILE.exists():
            age = time.time() - CLOSE_CACHE_FILE.stat().st_mtime
            if age < CLOSE_CACHE_HOURS * 3600:
                with CLOSE_CACHE_FILE.open("rb") as f:
                    payload = pickle.load(f)
                if isinstance(payload, dict) and payload:
                    return payload
    except Exception:
        pass

    tickers = [
        s if s.endswith(".NS") else f"{s}.NS"
        for s in symbols
    ]

    chunks = [
        tickers[i:i + YF_CHUNK_SIZE]
        for i in range(0, len(tickers), YF_CHUNK_SIZE)
    ]

    merged: dict[str, pd.Series] = {}

    # Parallel chunks: far fewer requests than 260 individual bhavcopy calls.
    with ThreadPoolExecutor(max_workers=YF_MAX_WORKERS) as pool:
        futures = [pool.submit(_download_yf_chunk, c) for c in chunks]
        for fut in as_completed(futures):
            try:
                merged.update(fut.result())
            except Exception:
                continue

    if merged:
        try:
            with CLOSE_CACHE_FILE.open("wb") as f:
                pickle.dump(merged, f, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            pass

    return merged


# ---------------------------------------------------------------------
# VECTORISED BREADTH CALCULATION
# ---------------------------------------------------------------------

def _build_daily_records(
    close_data: dict[str, pd.Series],
    days: Optional[int] = None,
) -> list[dict]:
    if not close_data:
        return []

    # Keep only symbols with enough history for 200 DMA.
    close_data = {
        sym: s.sort_index()
        for sym, s in close_data.items()
        if s is not None and len(s.dropna()) >= 200
    }

    if len(close_data) < 25:
        return []

    closes = pd.concat(close_data, axis=1).sort_index()

    # Daily returns / A-D.
    day_pct = closes.pct_change() * 100.0
    advances = day_pct.gt(0.05).sum(axis=1)
    declines = day_pct.lt(-0.05).sum(axis=1)

    # Moving averages.
    ma20 = closes.rolling(20, min_periods=20).mean()
    ma50 = closes.rolling(50, min_periods=50).mean()
    ma200 = closes.rolling(200, min_periods=200).mean()

    valid20 = closes.notna() & ma20.notna()
    valid50 = closes.notna() & ma50.notna()
    valid200 = closes.notna() & ma200.notna()

    above20 = (closes.gt(ma20) & valid20).sum(axis=1)
    above50 = (closes.gt(ma50) & valid50).sum(axis=1)
    above200 = (closes.gt(ma200) & valid200).sum(axis=1)

    denom20 = valid20.sum(axis=1)
    denom50 = valid50.sum(axis=1)
    denom200 = valid200.sum(axis=1)

    # 5-day high / low.
    hi5 = closes.rolling(5, min_periods=5).max()
    lo5 = closes.rolling(5, min_periods=5).min()
    valid5 = closes.notna() & hi5.notna()
    new_hi5 = (closes.ge(hi5) & valid5).sum(axis=1)
    new_lo5 = (closes.le(lo5) & valid5).sum(axis=1)

    records: list[dict] = []

    idx = closes.index
    if days is not None:
        idx = idx[-days:]

    for ts in idx:
        d20 = int(denom20.loc[ts])
        d50 = int(denom50.loc[ts])
        d200 = int(denom200.loc[ts])
        a20 = int(above20.loc[ts])
        a50 = int(above50.loc[ts])
        a200 = int(above200.loc[ts])

        p20 = round(a20 / d20 * 100, 2) if d20 else None
        p50 = round(a50 / d50 * 100, 2) if d50 else None
        p200 = round(a200 / d200 * 100, 2) if d200 else None

        records.append({
            "date": pd.Timestamp(ts).strftime("%d %b %Y"),
            "advances": int(advances.loc[ts]),
            "declines": int(declines.loc[ts]),
            "above_20dma": a20,
            "below_20dma": max(d20 - a20, 0),
            "above_50dma": a50,
            "below_50dma": max(d50 - a50, 0),
            "above_200dma": a200,
            "below_200dma": max(d200 - a200, 0),
            "above_20dma_denom": d20,
            "above_50dma_denom": d50,
            "above_200dma_denom": d200,
            "above_20dma_pct": p20,
            "above_50dma_pct": p50,
            "above_200dma_pct": p200,
            "new_hi_5d": int(new_hi5.loc[ts]),
            "new_lo_5d": int(new_lo5.loc[ts]),
        })

    return records


# ---------------------------------------------------------------------
# HISTORY STORAGE
# ---------------------------------------------------------------------

def _parse_history_date(value: str):
    return pd.to_datetime(value, format="%d %b %Y", errors="coerce")


def _normalise_history_rows(rows: list[dict]) -> list[dict]:
    by_date = {}
    for row in rows:
        d = row.get("date")
        if d:
            by_date[d] = row

    ordered = sorted(
        by_date.values(),
        key=lambda r: _parse_history_date(r["date"]),
    )
    return ordered[-MAX_HISTORY_ROWS:]


def append_history(snapshot: dict) -> None:
    if not snapshot or snapshot.get("error"):
        return

    record = {
        k: snapshot.get(k)
        for k in [
            "date", "advances", "declines",
            "above_20dma", "below_20dma",
            "above_50dma", "below_50dma",
            "above_200dma", "below_200dma",
            "above_20dma_denom", "above_50dma_denom",
            "above_200dma_denom",
            "above_20dma_pct", "above_50dma_pct",
            "above_200dma_pct",
            "new_hi_5d", "new_lo_5d",
        ]
    }

    try:
        rows = []
        if HISTORY_FILE.exists():
            for line in HISTORY_FILE.read_text().splitlines():
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue

        rows.append(record)
        rows = _normalise_history_rows(rows)
        HISTORY_FILE.write_text(
            "\n".join(json.dumps(r, separators=(",", ":")) for r in rows)
        )
    except Exception:
        pass


def load_history() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        return pd.DataFrame()

    try:
        rows = []
        for line in HISTORY_FILE.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(
            df["date"],
            format="%d %b %Y",
            errors="coerce",
        )
        df = (
            df.dropna(subset=["date"])
            .drop_duplicates("date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )

        # Reconstruct compatibility columns for older history files.
        for ma in ("20", "50", "200"):
            above = f"above_{ma}dma"
            below = f"below_{ma}dma"
            denom = f"above_{ma}dma_denom"
            pct = f"above_{ma}dma_pct"

            if denom not in df.columns:
                df[denom] = (
                    pd.to_numeric(df[above], errors="coerce")
                    + pd.to_numeric(df[below], errors="coerce")
                )

            if pct not in df.columns:
                den = pd.to_numeric(df[denom], errors="coerce")
                abv = pd.to_numeric(df[above], errors="coerce")
                df[pct] = abv.div(den.replace(0, pd.NA)) * 100

            df[pct] = pd.to_numeric(df[pct], errors="coerce").round(2)
            df[f"above_{ma}dma_change"] = df[pct].diff().round(2)

        return df

    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------
# SNAPSHOT / UPDATE
# ---------------------------------------------------------------------

def _snapshot_from_record(row: pd.Series, source: str) -> dict:
    out = row.to_dict()
    out["date"] = pd.Timestamp(out["date"]).strftime("%d %b %Y")
    out["source"] = source
    out["total_stocks"] = int(
        max(
            int(out.get("above_200dma_denom", 0) or 0),
            int(out.get("above_50dma_denom", 0) or 0),
            int(out.get("above_20dma_denom", 0) or 0),
        )
    )
    return out


def get_latest_history_snapshot() -> Optional[dict]:
    """Fast path: zero network calls."""
    history = load_history()
    if history.empty:
        return None
    return _snapshot_from_record(history.iloc[-1], "stored daily breadth")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def compute_breadth_snapshot(
    symbols: tuple[str, ...],
    calculation_key: str,
) -> dict:
    """
    Expensive operation, explicitly triggered by the UI.
    The calculation is cached for one completed session.
    """
    del calculation_key

    close_data = _download_close_history_cached(
        tuple(symbols),
        _session_key(),
    )

    records = _build_daily_records(
        close_data,
        days=2,
    )

    if not records:
        return {
            "error": (
                "No sufficient daily price history was returned. "
                "Yahoo Finance may be temporarily unavailable."
            )
        }

    latest = records[-1]
    latest["source"] = (
        f"yfinance daily EOD · {len(close_data)} stocks with history"
    )
    latest["total_stocks"] = max(
        latest["above_20dma_denom"],
        latest["above_50dma_denom"],
        latest["above_200dma_denom"],
    )
    return latest


def update_today(symbols: list[str]) -> dict:
    """Update today's completed NSE session. Called only by a button."""
    snap = compute_breadth_snapshot(
        tuple(symbols),
        _session_key(),
    )

    if snap.get("error"):
        return snap

    append_history(snap)
    return snap


# ---------------------------------------------------------------------
# BACKFILL
# ---------------------------------------------------------------------

def backfill_history_from_bhavcopy(
    symbols: list[str],
    days: int = DEFAULT_BACKFILL_DAYS,
) -> dict:
    """
    Backwards-compatible function name.
    Internally it uses batched Yahoo 1y history because fetching hundreds
    of individual NSE bhavcopy archives was the main latency problem.
    """
    try:
        close_data = _download_close_history_cached(
            tuple(symbols),
            _session_key(),
        )

        records = _build_daily_records(
            close_data,
            days=max(1, int(days)),
        )

        if not records:
            return {
                "days_written": 0,
                "error": "No sufficient daily history available.",
            }

        for rec in records:
            rec["source"] = (
                f"yfinance daily EOD · {len(close_data)} stocks"
            )
            rec["total_stocks"] = max(
                rec["above_20dma_denom"],
                rec["above_50dma_denom"],
                rec["above_200dma_denom"],
            )
            append_history(rec)

        return {
            "days_written": len(records),
            "date_range": (
                records[0]["date"],
                records[-1]["date"],
            ),
        }

    except Exception as exc:
        return {
            "days_written": 0,
            "error": f"Backfill failed: {exc}",
        }


# ---------------------------------------------------------------------
# A/D LINE + MCCLELLAN
# ---------------------------------------------------------------------

def compute_ad_line_and_mcclellan(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history

    df = history.copy()
    df["net_advances"] = (
        pd.to_numeric(df["advances"], errors="coerce").fillna(0)
        - pd.to_numeric(df["declines"], errors="coerce").fillna(0)
    )
    df["ad_line"] = df["net_advances"].cumsum()

    ema19 = df["net_advances"].ewm(span=19, adjust=False).mean()
    ema39 = df["net_advances"].ewm(span=39, adjust=False).mean()
    df["mcclellan"] = ema19 - ema39

    return df


# ---------------------------------------------------------------------
# COMPOSITE SCORE — COMPATIBILITY
# ---------------------------------------------------------------------

def compute_composite_score(
    snapshot: dict,
    history: pd.DataFrame | None = None,
) -> dict:
    if snapshot.get("error"):
        return {"score": None, "label": "N/A", "error": snapshot["error"]}

    total = int(snapshot.get("total_stocks", 0) or 0)
    if total <= 0:
        return {"score": None, "label": "N/A", "error": "Zero-stock snapshot."}

    adv = int(snapshot.get("advances", 0) or 0)
    dec = int(snapshot.get("declines", 0) or 0)
    ad_ratio = adv / dec if dec else (2.0 if adv else 1.0)
    ad_score = min(25.0, max(0.0, ad_ratio / 2.0 * 25.0))

    p20 = float(snapshot.get("above_20dma_pct") or 0) / 100.0
    p50 = float(snapshot.get("above_50dma_pct") or 0) / 100.0
    p200 = float(snapshot.get("above_200dma_pct") or 0) / 100.0
    ma_score = ((p20 + p50 + p200) / 3.0) * 35.0

    hi = int(snapshot.get("new_hi_5d", 0) or 0)
    lo = int(snapshot.get("new_lo_5d", 0) or 0)
    hilo_score = 10.0 + max(-10.0, min(10.0, ((hi - lo) / max(total * 0.1, 1)) * 10.0))

    mc_score = 10.0
    mc_val = None
    if history is not None and not history.empty and "mcclellan" in history.columns:
        vals = history["mcclellan"].dropna()
        if not vals.empty:
            mc_val = float(vals.iloc[-1])
            mc_score = 10.0 + max(-10.0, min(10.0, (mc_val / max(total * 0.05, 1)) * 10.0))

    score = round(max(0.0, min(100.0, ad_score + ma_score + hilo_score + mc_score)), 1)

    if score >= 70:
        label = "STRONG"
    elif score >= 55:
        label = "MODERATELY STRONG"
    elif score >= 45:
        label = "NEUTRAL / MIXED"
    elif score >= 30:
        label = "MODERATELY WEAK"
    else:
        label = "WEAK"

    return {
        "score": score,
        "label": label,
        "breakdown": {
            "ad_ratio": {"value": round(ad_ratio, 2), "points": round(ad_score, 1), "max": 25},
            "ma_breadth": {"value": f"{((p20+p50+p200)/3)*100:.1f}%", "points": round(ma_score, 1), "max": 35},
            "new_hilo": {"value": f"{hi} hi / {lo} lo", "points": round(hilo_score, 1), "max": 20},
            "mcclellan": {"value": round(mc_val, 1) if mc_val is not None else "insufficient history", "points": round(mc_score, 1), "max": 20},
        },
    }


# ---------------------------------------------------------------------
# COMPATIBILITY HELPER
# ---------------------------------------------------------------------

def fetch_universe_ohlcv(tickers, period="260d"):
    """Compatibility helper. Returns Close-only daily history."""
    del period
    symbols = [str(x).upper().replace(".NS", "") for x in tickers]
    return _download_close_history_cached(tuple(symbols), _session_key())
