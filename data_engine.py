"""
data_engine.py — Arka Trades fast data layer (v2)

What changed vs v1 (all about speed and resilience; the public API is unchanged)
-------------------------------------------------------------------------------
1. ONE price download per symbol.  get_history(sym, "1y" / "2y" / "3mo" / "5y") used to download each
   period separately (the Security page alone pulled the same stock 3-4 times). Now one 5-year download
   is cached and every shorter period is a slice of it.
2. MARKET-AWARE CACHING.  Cache keys include a time bucket: quotes refresh every 30 s while NSE is open
   and every 15 min when it is closed; history every 2 min open / 1 h closed. Evenings and weekends no
   longer re-download anything on each click.
3. STALE-WHILE-ERROR + COOL-DOWN.  When Yahoo rate-limits or fails, the last good value is served and the
   failing call is not retried for 45 s. Before, every rerun retried and waited for timeouts (the main
   source of "freezing" when Yahoo is slow). Failures are still never written into st.cache_data.
4. INDEX STRIP IN ONE CALL.  All primary index tickers are fetched in a single batched download; only
   the ones that fail fall back (in parallel) to their alternates.
5. Fundamentals (Screener) cached 6 h, Yahoo statements 6 h, Yahoo news 15 min.

Nothing here renders UI. It only returns data.
"""
from __future__ import annotations

import io
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

import screener_scraper as ss

IST = timezone(timedelta(hours=5, minutes=30))


# ════════════════════════════════════════════════════════════════
# Small helpers
# ════════════════════════════════════════════════════════════════
def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def to_float(x):
    """'1,234.50' / '12%' / '₹668' -> float, else None."""
    if x is None:
        return None
    try:
        v = float(str(x).replace(",", "").replace("%", "").replace("₹", "").replace("Cr", "").strip())
        return v if math.isfinite(v) else None
    except Exception:
        return None


def market_open(now: datetime | None = None) -> bool:
    """NSE cash session incl. pre-open, Mon-Fri 09:00-15:40 IST (exchange holidays not modelled)."""
    now = now or datetime.now(IST)
    t = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 <= t <= 15 * 60 + 40


def _bucket(live_s: int, closed_s: int) -> int:
    """Cache-key time bucket: short while the market is open, long when it is closed."""
    s = live_s if market_open() else closed_s
    return int(time.time() // s)


# ── stale-while-error store ─────────────────────────────────────
_LOCK = threading.Lock()
_LAST_GOOD: dict = {}
_FAIL_UNTIL: dict = {}
_COOLDOWN_S = 45
_MAX_KEEP = 400


def _guarded(key, fn, fallback):
    """Run fn(); on success remember the value. On failure serve the last good value (or fallback) and
    skip new attempts for _COOLDOWN_S seconds so a slow provider cannot freeze every rerun."""
    now = time.time()
    if _FAIL_UNTIL.get(key, 0) > now:
        return _LAST_GOOD.get(key, fallback)
    try:
        v = fn()
    except Exception:
        with _LOCK:
            _FAIL_UNTIL[key] = now + _COOLDOWN_S
        return _LAST_GOOD.get(key, fallback)
    with _LOCK:
        _LAST_GOOD[key] = v
        _FAIL_UNTIL.pop(key, None)
        while len(_LAST_GOOD) > _MAX_KEEP:
            _LAST_GOOD.pop(next(iter(_LAST_GOOD)))
    return v


def _parse_download(df, tickers):
    """yf.download output -> {ticker: OHLCV DataFrame}, robust to yfinance versions."""
    out = {}
    if df is None or df.empty:
        return out
    if isinstance(df.columns, pd.MultiIndex):
        lv0 = list(df.columns.get_level_values(0).unique())
        lv1 = list(df.columns.get_level_values(1).unique())
        for t in tickers:
            try:
                if t in lv0:
                    sub = df[t]
                elif t in lv1:
                    sub = df.xs(t, axis=1, level=1)
                else:
                    continue
                sub = sub.dropna(how="all")
                if not sub.empty and "Close" in sub.columns:
                    out[t] = sub
            except Exception:
                continue
    elif len(tickers) == 1 and "Close" in df.columns:
        out[tickers[0]] = df.dropna(how="all")
    return out


# ════════════════════════════════════════════════════════════════
# Indicators (shared by scanner, chart and technicals)
# ════════════════════════════════════════════════════════════════
def _rma(values: np.ndarray, n: int) -> np.ndarray:
    """Wilder's smoothing, seeded with an SMA (same as TradingView's ta.rma)."""
    out = np.full(len(values), np.nan)
    if len(values) <= n:
        return out
    out[n] = np.nanmean(values[1:n + 1])
    for i in range(n + 1, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI exactly as TradingView computes it."""
    c = close.astype(float).to_numpy()
    if len(c) <= n:
        return pd.Series(np.nan, index=close.index)
    d = np.diff(c, prepend=np.nan)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    gain[0] = np.nan
    loss[0] = np.nan
    ag, al = _rma(gain, n), _rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - 100.0 / (1.0 + ag / al)
    rsi = np.where((al == 0) & ~np.isnan(ag), 100.0, rsi)
    return pd.Series(rsi, index=close.index)


# ════════════════════════════════════════════════════════════════
# Price history — ONE cached 5-year download per symbol; shorter periods are slices
# ════════════════════════════════════════════════════════════════
_PERIOD_DAYS = {"5d": 8, "1mo": 32, "3mo": 95, "6mo": 186, "1y": 368, "2y": 733, "3y": 1098, "5y": 1830}


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=96)
def _history_cached(symbol: str, bucket: int) -> pd.DataFrame:
    h = yf.Ticker(symbol + ".NS").history(period="5y", interval="1d", auto_adjust=False)
    if h is None or h.empty:
        raise ValueError("empty history")          # raising = not cached
    return h[~h.index.duplicated(keep="last")].sort_index()


def get_history(symbol: str, period: str = "5y") -> pd.DataFrame:
    sym = str(symbol).upper().strip()
    if not sym:
        return pd.DataFrame()
    h = _guarded(("hist", sym), lambda: _history_cached(sym, _bucket(120, 3600)), pd.DataFrame())
    if h is None or h.empty or period not in _PERIOD_DAYS:
        return h if h is not None else pd.DataFrame()
    cutoff = h.index[-1] - pd.Timedelta(days=_PERIOD_DAYS[period])
    return h[h.index >= cutoff]


def get_static(sym: str):
    """PDH / PDL / prev close / RSI / sparkline. Same shape as the old get_static."""
    h = get_history(sym, "3mo")
    if h is None or len(h) < 16:
        return None
    try:
        prev = h.iloc[-2]
        r = rsi_wilder(h["Close"]).iloc[-1]
        return {
            "pdh": float(prev["High"]), "pdl": float(prev["Low"]),
            "prev_close": float(prev["Close"]),
            "rsi": int(round(r)) if pd.notna(r) else 0,
            "spark": [float(x) for x in h["Close"].tail(12).tolist()],
        }
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# Batched quotes (watchlist monitor) and scanner
# ════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner=False, max_entries=48)
def _get_quotes_cached(symbols: tuple, bucket: int) -> dict:
    """Cached quote snapshot. Raises on total provider failure so failures aren't cached."""
    syms = list(symbols)
    out = {s: None for s in syms}
    attempted = provider_errors = 0
    for chunk in _chunks(syms, 60):
        tick = [s + ".NS" for s in chunk]
        attempted += 1
        try:
            df = yf.download(tick, period="5d", interval="1d", group_by="ticker",
                             progress=False, threads=True, auto_adjust=False)
        except Exception:
            provider_errors += 1
            continue
        parsed = _parse_download(df, tick)
        for s in chunk:
            d = parsed.get(s + ".NS")
            if d is None:
                continue
            c = d["Close"].dropna()
            if len(c) < 2:
                continue
            cur, pc = float(c.iloc[-1]), float(c.iloc[-2])
            if cur > 0 and pc > 0:
                out[s] = {"price": cur, "chg": (cur - pc) / pc * 100, "prev_close": pc}
    if syms and attempted and provider_errors == attempted:
        raise RuntimeError("All quote-provider requests failed")
    if syms and not any(out.values()):
        raise RuntimeError("Quote provider returned no prices")
    return out


def get_quotes(symbols: tuple) -> dict:
    """Safe public quote API: sorted key (better cache hits), last good value on failure."""
    syms = tuple(sorted(dict.fromkeys(str(x).upper().strip() for x in (symbols or ()) if x)))
    if not syms:
        return {}
    return _guarded(("quotes", syms), lambda: _get_quotes_cached(syms, _bucket(30, 900)), {s: None for s in syms})


get_prices_batch = get_quotes   # backwards-compatible name


@st.cache_data(ttl=3600, show_spinner=False, max_entries=24)
def _scan_cached(symbols: tuple, bucket: int) -> dict:
    out = {}
    for chunk in _chunks(list(symbols), 50):
        tick = [s + ".NS" for s in chunk]
        try:
            df = yf.download(tick, period="3mo", interval="1d", group_by="ticker",
                             progress=False, threads=True, auto_adjust=False)
        except Exception:
            continue
        parsed = _parse_download(df, tick)
        for s in chunk:
            h = parsed.get(s + ".NS")
            if h is None:
                continue
            h = h.dropna(subset=["Close", "High", "Low"])
            if len(h) < 16:
                continue
            try:
                close = h["Close"].astype(float)
                cur, pc = float(close.iloc[-1]), float(close.iloc[-2])
                prev = h.iloc[-2]
                r = rsi_wilder(close).iloc[-1]
                out[s] = {
                    "cur": cur, "chg": (cur - pc) / pc * 100, "prev_close": pc,
                    "pdh": float(prev["High"]), "pdl": float(prev["Low"]),
                    "rsi": int(round(r)) if pd.notna(r) else 0,
                    "spark": [float(x) for x in close.tail(12).tolist()],
                }
            except Exception:
                continue
    if symbols and not out:
        raise RuntimeError("scan returned nothing")
    return out


def scan_batch(symbols: tuple) -> dict:
    """Whole-watchlist scan in one or two HTTP calls -> {SYM: {cur, chg, prev_close, pdh, pdl, rsi, spark}}."""
    syms = tuple(dict.fromkeys(str(x).upper().strip() for x in (symbols or ()) if x))
    if not syms:
        return {}
    return _guarded(("scan", syms), lambda: _scan_cached(syms, _bucket(30, 900)), {})


scan_batch.clear = _scan_cached.clear      # keeps app.py's "Force fresh data" working


# ════════════════════════════════════════════════════════════════
# Index strip — one batched download, parallel fallbacks for the misses
# ════════════════════════════════════════════════════════════════
def _from_frame(h):
    if h is None or h.empty or "Close" not in h:
        return None
    close = h["Close"].dropna()
    if len(close) < 2:
        return None
    cur, pc = float(close.iloc[-1]), float(close.iloc[-2])
    if not (math.isfinite(cur) and math.isfinite(pc)) or cur <= 0 or pc <= 0:
        return None
    return {"price": cur, "chg": (cur - pc) / pc * 100, "pts": cur - pc, "spark": [float(x) for x in close.tolist()]}


def _index_one(sym):
    try:
        r = _from_frame(yf.Ticker(sym).history(period="5d", interval="1d"))
        if r:
            r["ticker_used"] = sym
        return r
    except Exception:
        return None


def _index_chain(chain):
    for s in chain:
        r = _index_one(s)
        if r:
            return r
    return None


@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def _get_indices_cached(items: tuple, bucket: int) -> dict:
    out = {}
    if not items:
        return out
    primaries = list(dict.fromkeys(chain[0] for _, chain in items))
    try:
        raw = yf.download(primaries, period="5d", interval="1d", group_by="ticker",
                          progress=False, threads=True, auto_adjust=False)
        parsed = _parse_download(raw, primaries)
    except Exception:
        parsed = {}
    missing = []
    for label, chain in items:
        r = _from_frame(parsed.get(chain[0]))
        if r:
            r["ticker_used"] = chain[0]
            out[label] = r
        else:
            missing.append((label, chain[1:] or chain))
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as ex:
            futs = {ex.submit(_index_chain, chain): label for label, chain in missing}
            for f in as_completed(futs):
                try:
                    out[futs[f]] = f.result()
                except Exception:
                    out[futs[f]] = None
    if not any(out.values()):
        raise RuntimeError("All index-provider lookups failed")
    return out


def get_indices(items: tuple) -> dict:
    items = tuple(items or ())
    return _guarded(("indices", items), lambda: _get_indices_cached(items, _bucket(60, 900)),
                    {label: None for label, _chain in items})


# ════════════════════════════════════════════════════════════════
# Screener bundle — ONE fetch, ONE table parse, cached 6 hours
# ════════════════════════════════════════════════════════════════
_M_PL = [("sales", "revenue", "interest", "financing margin"), ("net profit", "profit for the period")]
_M_SH = ["promoters", ("fiis", "fii"), ("diis", "dii")]
_M_BS = [("equity capital", "share capital"), ("reserves",), ("borrowings", "total liabilities")]
_M_CF = [("cash from operating", "cash flow from operating", "operating activities"),
         ("cash from investing", "investing activities", "capital expenditure")]
_M_RAT = [("debtor days",)]


def _match(tables, must):
    try:
        return ss._find_tables_by_header(tables, must)
    except Exception:
        return []


def _records(df):
    if df is None:
        return None
    try:
        r = ss._df_to_records(df.copy())
        return r if r.get("rows") else None
    except Exception:
        return None


def _growth_blocks(tables):
    """Screener's 'Compounded Sales Growth' style 2-column boxes."""
    keys = (("compounded sales growth", "Sales Growth"), ("compounded profit growth", "Profit Growth"),
            ("stock price cagr", "Stock Price CAGR"), ("return on equity", "Return on Equity"))
    blocks = {}
    for t in tables:
        try:
            if t.shape[1] != 2:
                continue
            cols = " ".join(str(c) for c in t.columns).lower()
            for needle, label in keys:
                if needle in cols:
                    rows = {}
                    for _, r in t.iterrows():
                        k = ss._cell_to_text(r.iloc[0]).replace(":", "").strip()
                        v = ss._cell_to_text(r.iloc[1]).strip()
                        if k:
                            rows[k] = v
                    if rows:
                        blocks[label] = rows
        except Exception:
            continue
    return blocks


def _screener_raw(symbol: str) -> dict:
    sym = symbol.upper().strip()
    res = ss.resolve_symbol(sym)
    if not res:
        ss._RESOLVE_CACHE.pop(sym, None)   # a transient miss must not poison the symbol until reboot
        raise RuntimeError("symbol not resolved on Screener")
    url = res["url"]
    html = ss._fetch_html(url)
    if not html:
        ss._RESOLVE_CACHE.pop(sym, None)
        raise RuntimeError("Screener page fetch failed")

    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        tables = []

    pl, sh, bs = _match(tables, _M_PL), _match(tables, _M_SH), _match(tables, _M_BS)
    cf, rat = _match(tables, _M_CF), _match(tables, _M_RAT)
    summary = (ss.get_summary(sym, url=url, html_override=html) or {}).get("data") or {}
    sector = (ss.get_sector_info(sym, url=url, html_override=html) or {}).get("data") or {}

    pros, cons, about = [], [], ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        pros = [li.get_text(" ", strip=True) for li in soup.select("div.pros li")][:8]
        cons = [li.get_text(" ", strip=True) for li in soup.select("div.cons li")][:8]
        node = soup.select_one("div.company-profile .about") or soup.select_one("div.about")
        if node:
            about = node.get_text(" ", strip=True)[:1200]
    except Exception:
        pass

    peer_urls = list(dict.fromkeys(f"/api/company/{i}/peers/" for i in re.findall(r"/api/company/(\d+)/peers/", html)))
    for i in re.findall(r'data-(?:warehouse|company)-id="(\d+)"', html):
        p = f"/api/company/{i}/peers/"
        if p not in peer_urls:
            peer_urls.append(p)

    bundle = {
        "resolved": True, "symbol": sym, "name": res.get("name", sym), "url": url,
        "summary": summary, "sector": sector,
        "quarterly": _records(pl[0]) if pl else None,
        "yearly": _records(pl[1]) if len(pl) > 1 else None,
        "shareholding": _records(sh[0]) if sh else None,
        "shareholding_yearly": _records(sh[1]) if len(sh) > 1 else None,
        "balance_sheet": _records(bs[0]) if bs else None,
        "cash_flow": _records(cf[0]) if cf else None,
        "ratios": _records(rat[0]) if rat else None,
        "growth": _growth_blocks(tables),
        "pros": pros, "cons": cons, "about": about, "peer_urls": peer_urls[:4],
    }
    if not (bundle["quarterly"] or bundle["yearly"] or summary):
        raise RuntimeError("Screener returned no usable data")
    return bundle


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=64)
def _screener_cached(symbol: str) -> dict:
    return _screener_raw(symbol)


def get_screener_bundle(symbol: str) -> dict:
    sym = str(symbol).upper().strip()
    empty = {"resolved": False, "symbol": sym, "reason": "Screener unavailable right now",
             "summary": {}, "sector": {}, "growth": {}, "pros": [], "cons": [], "about": "", "peer_urls": []}
    return _guarded(("screener", sym), lambda: _screener_cached(sym), empty)


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=64)
def _peers_cached(symbol: str, peer_urls: tuple) -> dict:
    hdr = {**ss._HEADERS, "X-Requested-With": "XMLHttpRequest", "Referer": ss._BASE + "/"}
    for path in peer_urls:
        try:
            r = requests.get(ss._BASE + path, headers=hdr, timeout=ss._TIMEOUT)
            if r.status_code != 200 or "<table" not in r.text.lower():
                continue
            tabs = pd.read_html(io.StringIO(r.text))
            if not tabs:
                continue
            df = tabs[0]
            if str(df.columns[0]).lower().startswith("s.no"):
                df = df.iloc[:, 1:]
            rec = ss._df_to_records(df)
            if rec.get("rows"):
                return rec
        except Exception:
            continue
    raise RuntimeError("peers unavailable")


def get_peers_table(symbol: str, peer_urls=()):
    sym = str(symbol).upper().strip()
    return _guarded(("peers", sym), lambda: _peers_cached(sym, tuple(peer_urls or ())), None)


# ════════════════════════════════════════════════════════════════
# yfinance extras — lazy; statements 6 h, news 15 min; failures not cached
# ════════════════════════════════════════════════════════════════
@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=128)
def _yf_attr_cached(symbol: str, attr: str, bucket: int):
    obj = getattr(yf.Ticker(symbol + ".NS"), attr)
    if obj is None:
        raise ValueError("none")
    if hasattr(obj, "empty") and obj.empty:
        raise ValueError("empty")
    if isinstance(obj, (dict, list)) and not obj:
        raise ValueError("empty")
    return obj


def yf_attr(symbol: str, attr: str):
    """attr in: info, quarterly_income_stmt, income_stmt, balance_sheet, cashflow, major_holders,
    institutional_holders, mutualfund_holders, dividends, splits, recommendations,
    upgrades_downgrades, analyst_price_targets, news"""
    sym = str(symbol).upper().strip()
    bucket = int(time.time() // (900 if attr == "news" else 6 * 3600))
    return _guarded(("yf", sym, attr), lambda: _yf_attr_cached(sym, attr, bucket), None)


# ════════════════════════════════════════════════════════════════
# ScanX (optional, lazy) — only called when the user opens those sections
# ════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner=False, max_entries=16)
def _scanx_cached(symbol: str, name: str):
    from scanx_data import fetch_scanx_company
    d = fetch_scanx_company(symbol, company_name=name or symbol)
    mf_ok = ((d.get("mf_transactions") or {}).get("status") == "live")
    if d.get("status") != "live" and not mf_ok:
        raise RuntimeError("ScanX unavailable")
    return d


def get_scanx(symbol: str, name: str = ""):
    sym = str(symbol).upper().strip()
    return _guarded(("scanx", sym), lambda: _scanx_cached(sym, name or ""), None)


# ════════════════════════════════════════════════════════════════
# One call that warms everything the Security workspace needs, in parallel
# ════════════════════════════════════════════════════════════════
def warm_security(symbol: str):
    """Returns (screener_bundle, 5-year history). Both fetched concurrently."""
    sym = str(symbol).upper().strip()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_b = ex.submit(get_screener_bundle, sym)
        f_h = ex.submit(get_history, sym, "5y")
        try:
            bundle = f_b.result()
        except Exception:
            bundle = get_screener_bundle(sym)
        try:
            hist = f_h.result()
        except Exception:
            hist = pd.DataFrame()
    return bundle, hist
