"""
data_engine.py — Arka Trades fast data layer (v1)

Why this file exists
--------------------
The old app fetched everything one symbol at a time, on every rerun:
  * 12 index quotes, one after another
  * 2 yfinance calls per watchlist symbol (1m + daily)
  * Screener page parsed 5-6 times per security
This module replaces all of that with:
  * parallel / batched downloads (one HTTP call for a whole watchlist)
  * st.cache_data on everything, but failures are NEVER cached
    (inner cached function raises -> outer wrapper catches)
  * ONE Screener fetch + ONE table parse per security, cached for an hour
  * lazy helpers for yfinance extras (only fetched when a tab needs them)

Nothing here renders UI. It only returns data.
"""
from __future__ import annotations

import io
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

import screener_scraper as ss


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
# Price history (one cached 5y download serves chart + technicals + PDH/PDL)
# ════════════════════════════════════════════════════════════════
@st.cache_data(ttl=120, show_spinner=False, max_entries=64)
def _history_cached(symbol: str, period: str) -> pd.DataFrame:
    h = yf.Ticker(symbol + ".NS").history(period=period, interval="1d", auto_adjust=False)
    if h is None or h.empty:
        raise ValueError("empty history")          # raising = not cached
    return h[~h.index.duplicated(keep="last")].sort_index()


def get_history(symbol: str, period: str = "5y") -> pd.DataFrame:
    try:
        return _history_cached(str(symbol).upper().strip(), period)
    except Exception:
        return pd.DataFrame()


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
@st.cache_data(ttl=20, show_spinner=False, max_entries=32)
def get_quotes(symbols: tuple) -> dict:
    """{SYM: {price, chg, prev_close} | None} — ONE download call per 60 symbols."""
    syms = list(dict.fromkeys(str(x).upper().strip() for x in (symbols or ()) if x))
    out = {s: None for s in syms}
    for chunk in _chunks(syms, 60):
        tick = [s + ".NS" for s in chunk]
        try:
            df = yf.download(tick, period="5d", interval="1d", group_by="ticker",
                             progress=False, threads=True, auto_adjust=False)
        except Exception:
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
    return out


get_prices_batch = get_quotes   # backwards-compatible name


@st.cache_data(ttl=30, show_spinner=False, max_entries=16)
def scan_batch(symbols: tuple) -> dict:
    """
    Whole-watchlist scan in one or two HTTP calls.
    Returns {SYM: {cur, chg, prev_close, pdh, pdl, rsi, spark}} (missing = no data).
    """
    syms = list(dict.fromkeys(str(x).upper().strip() for x in (symbols or ()) if x))
    out = {}
    for chunk in _chunks(syms, 50):
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
    return out


# ════════════════════════════════════════════════════════════════
# Index strip — all 12 indices fetched in parallel, cached 60s
# ════════════════════════════════════════════════════════════════
def _index_one(sym):
    try:
        h = yf.Ticker(sym).history(period="5d", interval="1d")
        if h is None or h.empty:
            return None
        close = h["Close"].dropna()
        if len(close) < 2:
            return None
        cur, pc = float(close.iloc[-1]), float(close.iloc[-2])
        if not (math.isfinite(cur) and math.isfinite(pc)) or cur <= 0 or pc <= 0:
            return None
        return {"price": cur, "chg": (cur - pc) / pc * 100, "pts": cur - pc,
                "spark": [float(x) for x in close.tolist()], "ticker_used": sym}
    except Exception:
        return None


def _index_chain(chain):
    for s in chain:
        r = _index_one(s)
        if r:
            return r
    return None


@st.cache_data(ttl=60, show_spinner=False)
def get_indices(items: tuple) -> dict:
    """items = ((label, (primary, fallback, ...)), ...)  ->  {label: dict | None}"""
    out = {}
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=min(12, len(items))) as ex:
        futs = {ex.submit(_index_chain, chain): label for label, chain in items}
        for f in as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception:
                out[futs[f]] = None
    return out


# ════════════════════════════════════════════════════════════════
# Screener bundle — ONE fetch, ONE table parse, cached an hour
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
        # resolve_symbol caches misses for the whole process lifetime — clear it
        # so one transient Screener hiccup doesn't poison the symbol until reboot.
        ss._RESOLVE_CACHE.pop(sym, None)
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

    pl = _match(tables, _M_PL)
    sh = _match(tables, _M_SH)
    bs = _match(tables, _M_BS)
    cf = _match(tables, _M_CF)
    rat = _match(tables, _M_RAT)

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


@st.cache_data(ttl=3600, show_spinner=False, max_entries=48)
def _screener_cached(symbol: str) -> dict:
    return _screener_raw(symbol)


def get_screener_bundle(symbol: str) -> dict:
    try:
        return _screener_cached(str(symbol).upper().strip())
    except Exception as e:
        return {"resolved": False, "symbol": str(symbol).upper().strip(), "reason": str(e),
                "summary": {}, "sector": {}, "growth": {}, "pros": [], "cons": [], "about": "",
                "peer_urls": []}


@st.cache_data(ttl=3600, show_spinner=False, max_entries=48)
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
    try:
        return _peers_cached(str(symbol).upper().strip(), tuple(peer_urls or ()))
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# yfinance extras — lazy, each cached 15 min, failures not cached
# ════════════════════════════════════════════════════════════════
@st.cache_data(ttl=900, show_spinner=False, max_entries=64)
def _yf_attr_cached(symbol: str, attr: str):
    obj = getattr(yf.Ticker(symbol + ".NS"), attr)
    if obj is None:
        raise ValueError("none")
    if hasattr(obj, "empty") and obj.empty:
        raise ValueError("empty")
    if isinstance(obj, (dict, list)) and not obj:
        raise ValueError("empty")
    return obj


def yf_attr(symbol: str, attr: str):
    """attr in: info, quarterly_income_stmt, income_stmt, balance_sheet, cashflow,
    major_holders, institutional_holders, mutualfund_holders, dividends, splits,
    recommendations, upgrades_downgrades, analyst_price_targets, news"""
    try:
        return _yf_attr_cached(str(symbol).upper().strip(), attr)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# ScanX (optional, lazy) — only called when the user opens those tabs
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
    try:
        return _scanx_cached(str(symbol).upper().strip(), name or "")
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# One call that warms everything the Security workspace needs, in parallel
# ════════════════════════════════════════════════════════════════
def warm_security(symbol: str):
    """Returns (screener_bundle, history_df). Both fetched concurrently."""
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
