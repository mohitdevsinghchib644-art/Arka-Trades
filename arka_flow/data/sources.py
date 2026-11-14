"""
Data sources. Each loader returns (data, QualityRecord-like info) and REPORTS failures; none of them
retries forever, substitutes data, or hides a partial download.

Source inventory (limits are listed in README.md)
  NSE security-wise bhavcopy with delivery   sec_bhavdata_full_DDMMYYYY.csv on nsearchives.nseindia.com
      daily OHLC, total traded quantity, turnover (Rs lakh), deliverable quantity, delivery %. UNADJUSTED prices,
      so it is used for traded value and delivery, not for returns across corporate actions.
  Yahoo Finance (yfinance)                    adjusted daily closes for stocks and NSE indices. Supplementary:
      no official licence, gaps and stale bars happen; every series is validated and dated.
  NSDL fortnightly sector-wise FPI report     parser lives in the existing rotation_engine.py (Phase 4).

Downloads from NSE should be polite: one file per request, a pause between requests, and a local copy kept so
the same day is never fetched twice. Check NSE's website terms before automating at scale.
"""
from __future__ import annotations

import io
import time
from datetime import date

import numpy as np
import pandas as pd

BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
BHAV_COLS = {"SYMBOL": "symbol", "SERIES": "series", "DATE1": "date", "OPEN_PRICE": "open", "HIGH_PRICE": "high",
             "LOW_PRICE": "low", "CLOSE_PRICE": "close", "TTL_TRD_QNTY": "volume", "TURNOVER_LACS": "turnover_lacs",
             "DELIV_QTY": "deliv_qty", "DELIV_PER": "deliv_per"}


def bhav_url(d: date) -> str:
    return BHAV_URL.format(d=pd.Timestamp(d).strftime("%d%m%Y"))


def parse_bhavcopy(text: str, series=("EQ", "BE")) -> pd.DataFrame:
    """Parse one security-wise bhavcopy file. Header and cells carry stray spaces; '-' means not available.
    -> one row per symbol: date, open, high, low, close, volume, traded_value (Rs), deliv_qty, deliv_per."""
    df = pd.read_csv(io.StringIO(text), dtype=str, skipinitialspace=True)
    df.columns = [c.strip().upper() for c in df.columns]
    missing = [c for c in BHAV_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"bhavcopy format changed, missing columns: {missing}")
    df = df[list(BHAV_COLS)].rename(columns=BHAV_COLS)
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    df = df[df["series"].isin(series)].copy()
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    for c in ("open", "high", "low", "close", "volume", "turnover_lacs", "deliv_qty", "deliv_per"):
        df[c] = pd.to_numeric(df[c].replace({"-": np.nan, "": np.nan}), errors="coerce")
    df["traded_value"] = df["turnover_lacs"] * 1e5
    # if a symbol trades in more than one series that day, keep the EQ row
    df = df.sort_values("series").drop_duplicates("symbol", keep="first")
    return df.drop(columns=["turnover_lacs"]).set_index("symbol")


def fetch_bhavcopy(d: date, session=None, timeout=15):
    """-> (DataFrame or None, error or None). One request. No retry loop here; the caller decides."""
    import requests
    s = session or requests.Session()
    try:
        r = s.get(bhav_url(d), timeout=timeout, headers={"Accept": "text/csv,*/*"})
        if r.status_code == 404:
            return None, f"{d}: no file (holiday or not yet published)"
        if r.status_code != 200:
            return None, f"{d}: HTTP {r.status_code}"
        return parse_bhavcopy(r.text), None
    except Exception as exc:
        return None, f"{d}: {type(exc).__name__}: {exc}"


def fetch_yahoo(tickers, period="3y", pause=0.5, retries=2):
    """One ticker per request so a failure is attributed to that ticker.
    -> ({ticker: OHLCV DataFrame, adjusted}, {ticker: error})"""
    import yfinance as yf
    got, errors = {}, {}
    for t in tickers:
        last = None
        for attempt in range(retries + 1):
            try:
                df = yf.download(t, period=period, interval="1d", auto_adjust=True, progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if df is None or df.empty or "Close" not in df:
                    raise ValueError("empty response")
                got[t] = df.rename(columns=str.lower)
                last = None
                break
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(pause * (2 ** attempt))           # back off on rate limits
        if last:
            errors[t] = last
        time.sleep(pause)
    return got, errors
