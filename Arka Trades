"""
Arka Trades — quant_data.py (v1)

Shared end-of-day data layer for the Regime dashboard and the Backtest lab.

* Universes: Nifty 50 / 100 / 200 / 500, Midcap 150, Smallcap 250 (official NSE Indices constituent files), or your
  watchlist. A built-in Nifty 100 list is used when the NSE file cannot be reached.
* Prices: daily OHLCV from Yahoo Finance, split- and bonus-adjusted, downloaded in batches and aligned to NIFTY 50
  trading days into one float32 panel (dates x stocks).
* Cache: one download per universe per day, kept in memory (shared by every user of the app) and on disk, so the
  first visit of the day takes a minute or two for 500 stocks and every later visit is instant.
* Indicators are computed for the whole panel at once (EMA 10/21/50, SMA 200, ATR 14, 50-day average volume and
  turnover, rolling highs, 52-week high, RS rating 1-99 like IBD).

Survivorship note: constituent files list TODAY's members, so stocks that were dropped or delisted are missing from
history. Backtests on them look somewhat better than reality; the Backtest lab says so next to every result.
"""
from __future__ import annotations

import io
import os
import pickle
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None
try:
    import requests
except Exception:  # pragma: no cover
    requests = None

IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CACHE_DIR = Path(os.environ.get("ARKA_CACHE_DIR") or Path(tempfile.gettempdir()) / "arka_quant")
INDEX_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"

UNIVERSES = {
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv",
    "Nifty 500": "ind_nifty500list.csv",
    "Midcap 150": "ind_niftymidcap150list.csv",
    "Smallcap 250": "ind_niftysmallcap250list.csv",
}
_URLS = ["https://niftyindices.com/IndexConstituent/{f}", "https://archives.nseindia.com/content/indices/{f}",
         "https://nsearchives.nseindia.com/content/indices/{f}"]

# Built-in fallback (large and liquid names). Renamed / merged symbols simply return no data and are dropped.
FALLBACK = {
    "RELIANCE": "Oil Gas & Consumable Fuels", "HDFCBANK": "Financial Services", "ICICIBANK": "Financial Services",
    "INFY": "Information Technology", "TCS": "Information Technology", "BHARTIARTL": "Telecommunication", "ITC": "Fast Moving Consumer Goods",
    "LT": "Construction", "SBIN": "Financial Services", "AXISBANK": "Financial Services", "KOTAKBANK": "Financial Services",
    "HINDUNILVR": "Fast Moving Consumer Goods", "BAJFINANCE": "Financial Services", "M&M": "Automobile and Auto Components",
    "SUNPHARMA": "Healthcare", "HCLTECH": "Information Technology", "MARUTI": "Automobile and Auto Components", "NTPC": "Power",
    "TITAN": "Consumer Durables", "ULTRACEMCO": "Construction Materials", "POWERGRID": "Power", "TATASTEEL": "Metals & Mining",
    "ASIANPAINT": "Consumer Durables", "ONGC": "Oil Gas & Consumable Fuels", "NESTLEIND": "Fast Moving Consumer Goods",
    "ADANIPORTS": "Services", "BAJAJFINSV": "Financial Services", "JSWSTEEL": "Metals & Mining", "TECHM": "Information Technology",
    "WIPRO": "Information Technology", "COALINDIA": "Oil Gas & Consumable Fuels", "HINDALCO": "Metals & Mining",
    "GRASIM": "Construction Materials", "CIPLA": "Healthcare", "DRREDDY": "Healthcare", "BAJAJ-AUTO": "Automobile and Auto Components",
    "EICHERMOT": "Automobile and Auto Components", "HEROMOTOCO": "Automobile and Auto Components", "BRITANNIA": "Fast Moving Consumer Goods",
    "APOLLOHOSP": "Healthcare", "SBILIFE": "Financial Services", "HDFCLIFE": "Financial Services", "TATACONSUM": "Fast Moving Consumer Goods",
    "ADANIENT": "Metals & Mining", "SHRIRAMFIN": "Financial Services", "TRENT": "Consumer Services", "BEL": "Capital Goods",
    "INDUSINDBK": "Financial Services", "ETERNAL": "Consumer Services", "JIOFIN": "Financial Services", "HAL": "Capital Goods",
    "DLF": "Realty", "VEDL": "Metals & Mining", "PIDILITIND": "Chemicals", "SIEMENS": "Capital Goods", "GODREJCP": "Fast Moving Consumer Goods",
    "DABUR": "Fast Moving Consumer Goods", "AMBUJACEM": "Construction Materials", "BANKBARODA": "Financial Services", "PNB": "Financial Services",
    "CANBK": "Financial Services", "GAIL": "Oil Gas & Consumable Fuels", "IOC": "Oil Gas & Consumable Fuels", "BPCL": "Oil Gas & Consumable Fuels",
    "TATAPOWER": "Power", "ADANIGREEN": "Power", "ADANIPOWER": "Power", "DMART": "Consumer Services", "LICI": "Financial Services",
    "HAVELLS": "Consumer Durables", "CHOLAFIN": "Financial Services", "ICICIGI": "Financial Services", "ICICIPRULI": "Financial Services",
    "TVSMOTOR": "Automobile and Auto Components", "SHREECEM": "Construction Materials", "TORNTPHARM": "Healthcare", "ZYDUSLIFE": "Healthcare",
    "DIVISLAB": "Healthcare", "LTIM": "Information Technology", "NAUKRI": "Consumer Services", "INDIGO": "Services",
    "MOTHERSON": "Automobile and Auto Components", "BOSCHLTD": "Automobile and Auto Components", "ABB": "Capital Goods", "CGPOWER": "Capital Goods",
    "VBL": "Fast Moving Consumer Goods", "UNITDSPR": "Fast Moving Consumer Goods", "IRFC": "Financial Services", "RECLTD": "Financial Services",
    "PFC": "Financial Services", "BAJAJHLDNG": "Financial Services", "JSWENERGY": "Power", "INDHOTEL": "Consumer Services", "LODHA": "Realty",
    "POLYCAB": "Capital Goods", "PERSISTENT": "Information Technology", "COFORGE": "Information Technology", "MUTHOOTFIN": "Financial Services",
    "BHEL": "Capital Goods", "HINDZINC": "Metals & Mining", "TATAMOTORS": "Automobile and Auto Components", "TMPV": "Automobile and Auto Components",
}

_LOCK = threading.Lock()
_MEM: dict = {}


def _now() -> datetime:
    return datetime.now(IST)


def _cache(fn=None, **kw):
    """st.cache_data when Streamlit is present, plain function otherwise (tests, scripts)."""
    def deco(f):
        if st is not None and hasattr(st, "cache_data"):
            try:
                return st.cache_data(show_spinner=False, **kw)(f)
            except Exception:
                return f
        return f
    return deco(fn) if fn else deco


# ═════════════════════════ universes ═════════════════════════
def _parse_constituents(text: str) -> dict:
    df = pd.read_csv(io.StringIO(text))
    cols = {c.strip().lower(): c for c in df.columns}
    sym = cols.get("symbol")
    ind = cols.get("industry")
    if not sym:
        return {}
    out = {}
    for _, r in df.iterrows():
        s = str(r[sym]).strip().upper()
        if s and s != "NAN":
            out[s] = str(r[ind]).strip() if ind else ""
    return out


@_cache(ttl=24 * 3600)
def _constituents_cached(fname: str, day: str) -> dict:
    if requests is None:
        return {}
    for u in _URLS:
        try:
            r = requests.get(u.format(f=fname), headers={"User-Agent": UA, "Referer": "https://www.niftyindices.com/"}, timeout=8)
            if r.status_code == 200 and "symbol" in r.text[:400].lower():
                out = _parse_constituents(r.text)
                if len(out) >= 40:
                    return out
        except Exception:
            continue
    return {}


def universe(name: str, watchlist=None) -> tuple[dict, str]:
    """({symbol: industry}, note). Falls back to the built-in list when the NSE file is unreachable."""
    if name == "My watchlist":
        syms = [str(s).upper().strip() for s in (watchlist or []) if str(s).strip()]
        return {s: FALLBACK.get(s, "") for s in dict.fromkeys(syms)}, f"{len(syms)} stocks from your lists"
    fname = UNIVERSES.get(name, UNIVERSES["Nifty 500"])
    got = _constituents_cached(fname, _now().strftime("%Y-%m-%d"))
    if got:
        return got, f"{len(got)} stocks · today's {name} members (NSE Indices)"
    return dict(FALLBACK), f"NSE list unreachable · using the built-in list of {len(FALLBACK)} large and liquid stocks"


# ═════════════════════════ download ═════════════════════════
def _frame_from(df: pd.DataFrame, ticker: str, single: bool) -> pd.DataFrame | None:
    """One ticker's OHLCV out of whatever column layout this yfinance version returns."""
    if df is None or df.empty:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            lv0 = set(map(str, df.columns.get_level_values(0)))
            if ticker in lv0:
                f = df[ticker]
            elif ticker in set(map(str, df.columns.get_level_values(-1))):
                f = df.xs(ticker, axis=1, level=-1)
            else:
                return None
        else:
            if not single:
                return None
            f = df
        f = f.rename(columns=lambda c: str(c).strip().title())
        need = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in f.columns for c in need):
            return None
        f = f[need].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
        f = f[f["Close"] > 0]
        if f.empty:
            return None
        idx = pd.to_datetime(f.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert(IST).tz_localize(None)
        f.index = idx.normalize()
        return f[~f.index.duplicated(keep="last")]
    except Exception:
        return None


def _yf_download(tickers: list, start: str):
    import yfinance as yf
    kw = dict(start=start, interval="1d", auto_adjust=True, progress=False, group_by="ticker", threads=True)
    try:
        return yf.download(tickers, **kw)
    except TypeError:
        kw.pop("threads", None)
        return yf.download(tickers, **kw)


def download(symbols: list, years: int, progress=None, chunk: int = 60) -> tuple[dict, list]:
    """{ticker: frame} for symbols (+ NIFTY 50 and India VIX) and the list of symbols that returned nothing."""
    start = (_now() - timedelta(days=int(years * 365.25) + 420)).strftime("%Y-%m-%d")   # +~1.1y for 200-day / 52-week warm-up
    tick = {s: f"{s}.NS" for s in symbols}
    wanted = [INDEX_TICKER, VIX_TICKER] + list(tick.values())
    frames, missing = {}, []
    for i in range(0, len(wanted), chunk):
        part = wanted[i:i + chunk]
        df = None
        for attempt in range(2):
            try:
                df = _yf_download(part, start)
                break
            except Exception:
                time.sleep(1.5)
        for t in part:
            f = _frame_from(df, t, single=len(part) == 1)
            if f is not None and len(f) >= 60:
                frames[t] = f
        if progress:
            progress(min(1.0, (i + len(part)) / len(wanted)), f"Downloaded {min(i + len(part), len(wanted))} of {len(wanted)}")
    for s, t in tick.items():
        if t not in frames:
            missing.append(s)
    return frames, missing


# ═════════════════════════ panel ═════════════════════════
def build_panel(frames: dict, symbols: list, industries: dict, years: int, name: str = "") -> dict:
    """Align every stock to NIFTY 50 trading days. Arrays are float32, shape (T, N); NaN where a stock did not trade."""
    idx_f = frames.get(INDEX_TICKER)
    if idx_f is None or len(idx_f) < 300:
        raise RuntimeError("NIFTY 50 history is unavailable, so the trading calendar cannot be built.")
    dates = idx_f.index
    syms = [s for s in symbols if f"{s}.NS" in frames]
    T, N = len(dates), len(syms)
    arr = {k: np.full((T, N), np.nan, dtype=np.float32) for k in ("o", "h", "l", "c", "v")}
    for j, s in enumerate(syms):
        f = frames[f"{s}.NS"].reindex(dates)
        for k, col in (("o", "Open"), ("h", "High"), ("l", "Low"), ("c", "Close"), ("v", "Volume")):
            arr[k][:, j] = f[col].to_numpy(dtype=np.float32)
    vix = frames.get(VIX_TICKER)
    vix_c = vix["Close"].reindex(dates).ffill().to_numpy(dtype=np.float32) if vix is not None else np.full(T, np.nan, dtype=np.float32)
    idx = idx_f.reindex(dates)
    return {"name": name, "dates": dates, "symbols": syms, "industry": [industries.get(s, "") for s in syms],
            "o": arr["o"], "h": arr["h"], "l": arr["l"], "c": arr["c"], "v": arr["v"],
            "idx_o": idx["Open"].to_numpy(np.float32), "idx_h": idx["High"].to_numpy(np.float32),
            "idx_l": idx["Low"].to_numpy(np.float32), "idx_c": idx["Close"].to_numpy(np.float32),
            "vix": vix_c, "years": years, "built": _now().isoformat(timespec="minutes")}


def _cache_key(name: str, symbols: list, years: int) -> str:
    import hashlib
    h = hashlib.md5(",".join(sorted(symbols)).encode()).hexdigest()[:8]
    stamp = _now() - timedelta(hours=18, minutes=30)     # a new file after ~18:30 IST, when the day's EOD data is final
    return f"{name.replace(' ', '_')}_{years}y_{h}_{stamp:%Y%m%d}"


def load_panel(name: str, watchlist=None, years: int = 10, progress=None, force: bool = False) -> dict:
    """The panel for a universe: memory → disk → Yahoo. Raises RuntimeError with a readable message on failure."""
    inds, note = universe(name, watchlist)
    symbols = list(inds)
    if not symbols:
        raise RuntimeError("This universe is empty. Add stocks to your watchlist or pick an index.")
    key = _cache_key(name, symbols, years)
    with _LOCK:
        if not force and key in _MEM:
            return _MEM[key]
    path = CACHE_DIR / f"{key}.pkl"
    if not force and path.exists():
        try:
            with open(path, "rb") as fh:
                p = pickle.load(fh)
            with _LOCK:
                _MEM[key] = p
            return p
        except Exception:
            pass
    frames, missing = download(symbols, years, progress)
    p = build_panel(frames, symbols, inds, years, name)
    p["note"], p["missing"] = note, missing
    add_indicators(p)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for old in CACHE_DIR.glob(f"{name.replace(' ', '_')}_{years}y_*.pkl"):
            old.unlink(missing_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(p, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
    with _LOCK:
        _MEM[key] = p
        while len(_MEM) > 4:
            _MEM.pop(next(iter(_MEM)))
    return p


# ═════════════════════════ indicators (whole panel at once) ═════════════════════════
def ema(x: np.ndarray, n: int) -> np.ndarray:
    """EMA down axis 0, seeded with the first valid value of each column; NaN gaps keep the previous value."""
    a = 2.0 / (n + 1.0)
    x = np.asarray(x, dtype=np.float64)
    out = np.full_like(x, np.nan)
    prev = np.full(x.shape[1:], np.nan)
    for t in range(x.shape[0]):
        xt = x[t]
        ok = ~np.isnan(xt)
        new = np.where(np.isnan(prev), xt, prev + a * (xt - prev))
        prev = np.where(ok, new, prev)
        out[t] = np.where(ok, prev, np.nan)
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.DataFrame(x).rolling(n, min_periods=n).mean().to_numpy()


def roll_max(x: np.ndarray, n: int) -> np.ndarray:
    return pd.DataFrame(x).rolling(n, min_periods=n).max().to_numpy()


def roll_min(x: np.ndarray, n: int) -> np.ndarray:
    return pd.DataFrame(x).rolling(n, min_periods=n).min().to_numpy()


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if k > 0:
        out[k:] = x[:-k]
    elif k < 0:
        out[:k] = x[-k:]
    else:
        out[:] = x
    return out


def atr(h, l, c, n: int = 14) -> np.ndarray:
    pc = shift(c, 1)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tr = np.nanmax(np.stack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[np.isnan(c)] = np.nan
    return pd.DataFrame(tr).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def rs_rating(c: np.ndarray) -> np.ndarray:
    """IBD-style relative strength: 40% last quarter + 20% each of the three before, ranked 1-99 across stocks each day."""
    def r(k):
        return c / shift(c, k) - 1.0
    score = 0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(252)
    score = np.where(np.isnan(score), 0.4 * r(63) + 0.6 * r(126), score)          # younger listings: shorter history
    rk = pd.DataFrame(score).rank(axis=1, pct=True).to_numpy()
    return np.clip(np.round(rk * 98 + 1), 1, 99)


def add_indicators(p: dict) -> dict:
    o, h, l, c, v = (p[k].astype(np.float64) for k in ("o", "h", "l", "c", "v"))
    f32 = lambda a: np.asarray(a, dtype=np.float32)
    p["ema10"], p["ema21"], p["ema50"] = f32(ema(c, 10)), f32(ema(c, 21)), f32(ema(c, 50))
    p["sma200"] = f32(sma(c, 200))
    p["atr14"] = f32(atr(h, l, c, 14))
    p["atr50"] = f32(atr(h, l, c, 50))
    p["vavg50"] = f32(shift(sma(v, 50), 1))                    # average of the 50 sessions BEFORE today
    p["turn_cr"] = f32(sma(c * v, 20) / 1e7)                  # 20-day average turnover, ₹ crore
    p["hh52"] = f32(roll_max(h, 252))
    p["rs"] = f32(rs_rating(c))
    p["idx_ema21"] = f32(ema(p["idx_c"][:, None].astype(np.float64), 21)[:, 0])
    p["idx_ema50"] = f32(ema(p["idx_c"][:, None].astype(np.float64), 50)[:, 0])
    p["idx_sma200"] = f32(sma(p["idx_c"][:, None].astype(np.float64), 200)[:, 0])
    return p


def prior_high(p: dict, n: int) -> np.ndarray:
    """Highest high of the n sessions before today (cached on the panel)."""
    k = f"ph{n}"
    if k not in p:
        p[k] = np.asarray(shift(roll_max(p["h"].astype(np.float64), n), 1), dtype=np.float32)
    return p[k]
