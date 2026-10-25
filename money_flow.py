"""Arka Money Flow (v4): sector rotation radar, sector ranking, index breadth, Index Explorer, constituents, FII/DII.

What this module does
---------------------
* SECTOR RANKING   - the stock universe grouped by NSE industry. Views: ROTATION RADAR (which sectors are
                     turning up, with stage + score + reasons), SECTOR RANKING, TREND (RSI + EMA 10/20/50/200
                     chips and distance from each EMA), VS BENCHMARKS (every sector against NIFTY 50, MIDCAP 100
                     and SMALLCAP 100), TURNOVER A/D, SECTOR TURNOVER, CHAIKIN MONEY FLOW, and a stock drill-down.
* NIFTY INDICES   - every sectoral / thematic index with RSI, EMA chips, EMA distance and pivots.
* SECTOR ROTATION - NSE sectoral indices ranked by relative performance vs NIFTY 50 + rotation quadrant.
* MARKET BREADTH  - advance/decline for EVERY NSE index plus stock-level breadth for one index universe.
* INDEX EXPLORER  - constituents' breadth, contribution, relative strength, relative volume, pressure scores.
* MONEY FLOW      - disclosed FII/DII cash flows + a price-signed turnover proxy per index.
* CONSTITUENTS    - stocks of any index; click a row to open it in Arka.

Data sources: NSE public endpoints (allIndices, equity-stockIndices, fiidiiTradeReact, historical index closes),
NSE archive constituent CSVs (fallback), Yahoo Finance (prices). Missing data stays missing - nothing is invented.
Pressure / turnover / rotation signals are price + volume + breadth evidence, NOT institutional order flow and
NOT investment advice.

v4
--
* RS percentile ratings removed everywhere; RSI(14) + EMA 10/20/50/200 position (green = price above, red = below)
  and % distance from the 10 / 20 EMA added.
* New rotation radar: RRG-style relative rotation vs NIFTY 50 / MIDCAP 100 / SMALLCAP 100, a transparent 0-100
  rotation score and stage labels (ROTATING IN / LEADING / EXTENDED / IMPROVING / WEAKENING / LAGGING).
* Professional table + chart styling (mf_ui.py). The sector ranking now also works when NSE's live API is blocked.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

import flow_engine as fe
import index_engine as ix
import mf_ui as ui
import sector_engine as se

NSE = "https://www.nseindia.com"
ARCHIVE_BASE = "https://nsearchives.nseindia.com/content/indices/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE + "/market-data/live-market-indices",
    "X-Requested-With": "XMLHttpRequest",
}
NUM_INDEX = ["last", "percentChange", "pe", "pb", "dy", "advances", "declines", "unchanged",
             "oneWeekAgo", "oneMonthAgo", "oneYearAgo", "yearHigh", "yearLow"]
SCOPES = {
    "SECTORAL": ("SECTORAL",), "THEMATIC": ("THEMATIC",),
    "SECTORAL + THEMATIC": ("SECTORAL", "THEMATIC"),
    "BROAD MARKET": ("BROAD MARKET", "BROADMARKET"), "ALL INDICES": (),
}
QUAD = {"LEADING": "#32d296", "WEAKENING": "#f7b84b", "LAGGING": "#ff6969", "IMPROVING": "#57b8ff"}
SIGNAL_COLOR = {"BROAD PARTICIPATION": "#32d296", "NARROW LEADERSHIP": "#f7b84b",
                "HEAVYWEIGHTS DRAGGING": "#f7b84b", "BROAD SELLING": "#ff6969",
                "MIXED": "#9ca3af", "UNAVAILABLE": "#9ca3af"}

# NSE archive constituent CSVs (fallback when the live API is blocked). Best-effort slugs:
# an index whose file 404s simply reports "constituents unavailable".
ARCHIVE_SLUGS = {
    "NIFTY 50": "ind_nifty50list", "NIFTY NEXT 50": "ind_niftynext50list", "NIFTY 100": "ind_nifty100list",
    "NIFTY 200": "ind_nifty200list", "NIFTY 500": "ind_nifty500list",
    "NIFTY MIDCAP 50": "ind_niftymidcap50list", "NIFTY MIDCAP 100": "ind_niftymidcap100list",
    "NIFTY MIDCAP 150": "ind_niftymidcap150list", "NIFTY SMALLCAP 50": "ind_niftysmallcap50list",
    "NIFTY SMALLCAP 100": "ind_niftysmallcap100list", "NIFTY SMALLCAP 250": "ind_niftysmallcap250list",
    "NIFTY MIDSMALLCAP 400": "ind_niftymidsmallcap400list", "NIFTY LARGEMIDCAP 250": "ind_niftylargemidcap250list",
    "NIFTY BANK": "ind_niftybanklist", "NIFTY AUTO": "ind_niftyautolist",
    "NIFTY FINANCIAL SERVICES": "ind_niftyfinancelist", "NIFTY FMCG": "ind_niftyfmcglist",
    "NIFTY IT": "ind_niftyitlist", "NIFTY MEDIA": "ind_niftymedialist", "NIFTY METAL": "ind_niftymetallist",
    "NIFTY PHARMA": "ind_niftypharmalist", "NIFTY PSU BANK": "ind_niftypsubanklist",
    "NIFTY PRIVATE BANK": "ind_nifty_privatebanklist", "NIFTY REALTY": "ind_niftyrealtylist",
    "NIFTY HEALTHCARE INDEX": "ind_niftyhealthcarelist", "NIFTY CONSUMER DURABLES": "ind_niftyconsumerdurableslist",
    "NIFTY OIL & GAS": "ind_niftyoilgaslist", "NIFTY ENERGY": "ind_niftyenergylist",
    "NIFTY INFRASTRUCTURE": "ind_niftyinfralist", "NIFTY COMMODITIES": "ind_niftycommoditieslist",
    "NIFTY CONSUMPTION": "ind_niftyconsumptionlist", "NIFTY MNC": "ind_niftymnclist",
    "NIFTY PSE": "ind_niftypselist", "NIFTY SERVICES SECTOR": "ind_niftyservicelist",
    "NIFTY TOTAL MARKET": "ind_niftytotalmarket_list", "NIFTY MICROCAP 250": "ind_niftymicrocap250_list",
}

# Yahoo tickers for index-level 1W/1M returns when NSE history is unavailable (coverage is partial).
YF_INDEX = {
    "NIFTY 50": "^NSEI", "NIFTY NEXT 50": "^NSMIDCP", "NIFTY 500": "^CRSLDX", "NIFTY BANK": "^NSEBANK",
    "NIFTY IT": "^CNXIT", "NIFTY AUTO": "^CNXAUTO", "NIFTY FMCG": "^CNXFMCG", "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY METAL": "^CNXMETAL", "NIFTY REALTY": "^CNXREALTY", "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY MEDIA": "^CNXMEDIA", "NIFTY PSU BANK": "^CNXPSUBANK", "NIFTY FINANCIAL SERVICES": "^CNXFIN",
    "NIFTY INFRASTRUCTURE": "^CNXINFRA", "NIFTY PSE": "^CNXPSE", "NIFTY MNC": "^CNXMNC",
    "NIFTY SERVICES SECTOR": "^CNXSERVICE", "NIFTY COMMODITIES": "^CNXCMDT", "NIFTY CONSUMPTION": "^CNXCONSUM",
}
_HISTORY_ALIASES = {"NIFTY FINANCIAL SERVICES": "NIFTY FIN SERVICE", "NIFTY HEALTHCARE INDEX": "NIFTY HEALTHCARE"}

# Benchmarks for the sector charts. Candidates are tried in order and each is sanity-checked against an
# equal-weight proxy built from the index's own constituents; if none passes, the proxy itself is used (and labelled).
BENCH_YF = {"MIDCAP 100": ["^CRSMID", "NIFTY_MIDCAP_100.NS", "NIFTYMDCP100.NS"],
            "SMALLCAP 100": ["^CNXSC", "NIFTYSMLCAP100.NS", "NIFTY_SMLCAP_100.NS"]}
BENCH_ARCHIVE = {"MIDCAP 100": "NIFTY MIDCAP 100", "SMALLCAP 100": "NIFTY SMALLCAP 100"}


# ═════════════════════════ styling / network ═════════════════════════
def _style():
    st.markdown("""<style>
    .arka-mf-kicker{font:700 10px monospace;letter-spacing:1.5px;color:#9ca3af;text-transform:uppercase}
    .arka-mf-title{font-size:24px;font-weight:800;color:#f4f6f8;margin:2px 0}
    .arka-mf-sub{font-size:12px;color:#8b929d;margin:0 0 16px}
    .arka-mf-section{font:700 11px monospace;letter-spacing:1px;color:#d1d7e0;margin:17px 0 8px;padding-bottom:7px;border-bottom:1px solid #292e36}
    .arka-mf-note{font-size:11px;color:#929aa6;border-left:2px solid #5a6472;padding:7px 10px;background:rgba(255,255,255,.025);margin:7px 0 12px}
    .arka-mf-banner{font:700 12px monospace;letter-spacing:.8px;padding:10px 14px;border:1px solid;margin:8px 0 12px;background:rgba(255,255,255,.02)}
    </style>""", unsafe_allow_html=True)
    ui.inject()


@st.cache_resource(ttl=600, show_spinner=False)
def _session():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(NSE, timeout=8)
        session.get(NSE + "/market-data/live-market-indices", timeout=8)
    except requests.RequestException:
        pass
    return session


def _get(path):
    for attempt in range(2):
        try:
            response = _session().get(NSE + path, timeout=12)
            if response.status_code == 200:
                return response.json()
        except (requests.RequestException, ValueError):
            pass
        if attempt == 0:
            _session.clear()
            time.sleep(.4)
    return None


def _rename(df, target, aliases):
    if target not in df.columns:
        for name in aliases:
            if name in df.columns:
                df.rename(columns={name: target}, inplace=True)
                return


def _numeric(df, columns):
    for col in columns:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")


def _num(v, decimals=2):
    try: return "—" if pd.isna(v) else f"{float(v):,.{decimals}f}"
    except (ValueError, TypeError): return "—"


def _pct(v):
    try: return "—" if pd.isna(v) else f"{float(v):+.2f}%"
    except (ValueError, TypeError): return "—"


def _fmt_ret(df, cols):
    """Display-only: signed % strings, '—' for missing (no literal None/NaN in tables)."""
    for c in cols:
        if c in df:
            df[c] = df[c].map(lambda v: f"{float(v):+.2f}%" if pd.notna(v) else "—")
    return df


# ═════════════════════════ NSE data ═════════════════════════
@st.cache_data(ttl=60, show_spinner=False, max_entries=4)
def fetch_all_indices():
    """Latest NSE index snapshot incl. advances/declines/unchanged for EVERY index."""
    payload = _get("/api/allIndices")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        return None
    df = pd.DataFrame(payload["data"])
    for target, aliases in {"index": ("indexName", "name"), "last": ("lastPrice",),
                            "percentChange": ("percChange", "pChange", "changePercent"),
                            "key": ("category", "indexCategory")}.items():
        _rename(df, target, aliases)
    if "index" not in df:
        return None
    df["index"] = df["index"].astype(str).str.strip()
    df = df[df["index"].ne("") & df["index"].str.lower().ne("nan")].copy()
    if "key" not in df:
        df["key"] = "OTHER"
    df["key"] = df["key"].fillna("OTHER").astype(str).str.upper().str.strip()
    _numeric(df, NUM_INDEX)
    df["r1d"] = df["percentChange"]
    for period, field in (("r1w", "oneWeekAgo"), ("r1m", "oneMonthAgo"), ("r1y", "oneYearAgo")):
        ref = df[field].where(df[field] > 0)
        df[period] = (df["last"] / ref - 1) * 100
    total = df[["advances", "declines", "unchanged"]].fillna(0).sum(axis=1).replace(0, np.nan)
    df["adv_pct"] = df["advances"] / total * 100
    df["ad_ratio"] = df["advances"] / df["declines"].where(df["declines"] > 0)
    df["net_adv"] = df["advances"] - df["declines"]
    df["breadth_pct"] = df["net_adv"] / total * 100
    return df.replace([np.inf, -np.inf], np.nan).sort_values("index").reset_index(drop=True)


def _equity_index_rows(indices):
    """Broad-market, sectoral and thematic equity indices (drops VIX / inverse / G-Sec style rows)."""
    key = indices["key"].fillna("").astype(str).str.upper()
    keep = key.str.contains(r"BROAD MARKET|SECTORAL|THEMATIC", regex=True)
    names = indices["index"].astype(str).str.upper()
    keep &= ~names.str.contains(r"\bVIX\b|INVERSE|G-SEC|GSEC|TREASURY|TR 1X|PR 1X", regex=True)
    return indices[keep].copy()


@st.cache_resource(ttl=3600, show_spinner=False)
def _hist_store() -> dict:
    """Shared in-memory store {index name: (timestamp, OHLC DataFrame)}; safe to fill from worker threads."""
    return {}


def _parse_nse_history(rows) -> pd.DataFrame:
    """NSE index-history rows -> OHLC DataFrame (daily). Raises on unrecognised shapes."""
    if isinstance(rows, dict):                                   # {"indexCloseOnlineRecords": [...]}
        rows = rows.get("indexCloseOnlineRecords") or next((v for v in rows.values() if isinstance(v, list)), [])
    if not isinstance(rows, list) or len(rows) < 2:
        raise RuntimeError("insufficient history rows")
    h = pd.DataFrame(rows)
    pick = lambda opts: next((c for c in opts if c in h), None)
    dcol = pick(("EOD_TIMESTAMP", "CH_TIMESTAMP", "HistoricalDate", "date", "Date"))
    ccol = pick(("EOD_CLOSE_INDEX_VAL", "EOD_CLOSE_INDEX_VALUE", "CLOSE_INDEX_VAL", "CLOSE", "close", "Close"))
    if dcol is None or ccol is None:
        raise RuntimeError("unrecognised history columns")
    num = lambda c: (pd.to_numeric(h[c].astype(str).str.replace(",", "", regex=False), errors="coerce")
                     if c else pd.Series(np.nan, index=h.index))
    out = pd.DataFrame({"Open": num(pick(("EOD_OPEN_INDEX_VAL", "OPEN_INDEX_VAL", "OPEN", "open", "Open"))),
                        "High": num(pick(("EOD_HIGH_INDEX_VAL", "HIGH_INDEX_VAL", "HIGH", "high", "High"))),
                        "Low": num(pick(("EOD_LOW_INDEX_VAL", "LOW_INDEX_VAL", "LOW", "low", "Low"))),
                        "Close": num(ccol)})
    out.index = pd.to_datetime(h[dcol], errors="coerce", dayfirst=True)
    out = out[out.index.notna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out = out.dropna(subset=["Close"])
    out = out[out["Close"] > 0]
    for c in ("Open", "High", "Low"):
        out[c] = out[c].fillna(out["Close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if len(out) < 2:
        raise RuntimeError("no usable closes")
    return out[["Open", "High", "Low", "Close"]]


def _fetch_nse_ohlc(index_name: str, days: int = 400) -> pd.DataFrame:
    """~400 days of daily OHLC from NSE (two requests of ~200 days). Tries NSE's short name first."""
    key = str(index_name).strip()
    cands = []
    for c in (_HISTORY_ALIASES.get(key.upper()), key):
        if c and c not in cands:
            cands.append(c)
    end = date.today()
    mid = end - timedelta(days=days // 2)
    spans = [(end - timedelta(days=days), mid - timedelta(days=1)), (mid, end)]
    last_err = None
    for req in cands:
        try:
            parts = []
            for a, b in spans:
                r = _session().get(NSE + "/api/historicalOR/indicesHistory",
                                   params={"indexType": req, "from": a.strftime("%d-%m-%Y"),
                                           "to": b.strftime("%d-%m-%Y")}, timeout=12)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                payload = r.json()
                parts.append(_parse_nse_history(payload.get("data", []) if isinstance(payload, dict) else payload))
            df = pd.concat(parts)
            df = df[~df.index.duplicated(keep="last")].sort_index()
            if len(df) >= 30:
                return df
            raise RuntimeError("too few rows")
        except Exception as exc:
            last_err = exc
    raise RuntimeError(str(last_err))


@st.cache_data(ttl=1800, show_spinner=False, max_entries=8)
def yahoo_index_ohlc(names: tuple) -> dict:
    """Fallback OHLC from Yahoo for the major indices listed in YF_INDEX (coverage is partial)."""
    pairs = {n: YF_INDEX[n] for n in names if n in YF_INDEX}
    if not pairs:
        return {}
    tickers = sorted(set(pairs.values()))
    try:
        raw = yf.download(tickers, period="2y", interval="1d", group_by="ticker",
                          progress=False, auto_adjust=False, threads=True)
    except Exception:
        return {}
    frames = fe._split_download(raw, tickers)
    out = {}
    for n, t in pairs.items():
        f = frames.get(t)
        if f is not None and len(f) >= 30:
            g = f[["Open", "High", "Low", "Close"]].copy()
            idx = pd.to_datetime(g.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            g.index = idx.normalize()
            out[n] = g
    return out


def load_index_histories(names, progress=None) -> tuple[int, int]:
    """Fill the shared store: NSE history first (3 workers), Yahoo fallback. Returns (loaded, attempted)."""
    store, now = _hist_store(), time.time()
    names = list(dict.fromkeys(names))
    todo = [n for n in names if n not in store or now - store[n][0] > 3600]
    if not todo:
        return len(names), len(names)
    failed = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_nse_ohlc, n): n for n in todo}
        for i, fut in enumerate(as_completed(futures), start=1):
            n = futures[fut]
            try:
                store[n] = (time.time(), fut.result())
            except Exception:
                failed.append(n)
            if progress:
                progress(i / len(todo), f"Loading index history · {i} / {len(todo)}")
    if failed:
        for n, df in yahoo_index_ohlc(tuple(failed)).items():
            store[n] = (time.time(), df)
    return sum(1 for n in names if n in store), len(names)


def index_histories(names) -> dict:
    store = _hist_store()
    return {n: store[n][1] for n in names if n in store}


def _apply_history_returns(indices):
    """Fill 1W / 1M returns from stored EOD closes wherever the live snapshot lacks them."""
    out = indices.copy()
    store = _hist_store()
    for i, row in out.iterrows():
        ent = store.get(str(row.get("index", "")))
        if not ent:
            continue
        c = ent[1]["Close"].dropna()
        if len(c) >= 6 and pd.isna(row.get("r1w")):
            out.at[i, "r1w"] = (c.iloc[-1] / c.iloc[-6] - 1) * 100
        if len(c) >= 22 and pd.isna(row.get("r1m")):
            out.at[i, "r1m"] = (c.iloc[-1] / c.iloc[-22] - 1) * 100
    return out


@st.cache_data(ttl=120, show_spinner=False, max_entries=96)
def fetch_constituents(index_name):
    """Live constituents + prices for one index (NSE API). Value/volume units are not converted."""
    payload = _get(f"/api/equity-stockIndices?index={quote(str(index_name), safe='')}")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        return None
    df = pd.DataFrame(payload["data"])
    for target, aliases in {"symbol": ("SYMBOL", "stockSymbol"), "lastPrice": ("last", "closePrice", "price"),
                            "pChange": ("percentChange", "changePercent"),
                            "totalTradedValue": ("tradedValue", "value"),
                            "totalTradedVolume": ("tradedVolume", "volume")}.items():
        _rename(df, target, aliases)
    if "symbol" not in df:
        return None
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df = df[df["symbol"].ne("") & df["symbol"].str.lower().ne("nan")].copy()
    df = df[~df["symbol"].str.replace(" ", "", regex=False).eq(str(index_name).upper().replace(" ", ""))]
    if "priority" in df:
        priority = pd.to_numeric(df["priority"], errors="coerce")
        if priority.notna().any() and (priority == 0).any():
            df = df[priority.eq(0)]
    _numeric(df, ["lastPrice", "pChange", "totalTradedVolume", "totalTradedValue", "ffmc",
                  "yearHigh", "yearLow", "nearWKH", "nearWKL"])
    df["value_raw"] = df["totalTradedValue"]
    return df.reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=24)
def _read_archive(slug: str):
    """Raw NSE archive constituent CSV (Company Name, Industry, Symbol, ...) or None."""
    try:
        r = requests.get(f"{ARCHIVE_BASE}{slug}.csv", headers={"User-Agent": HEADERS["User-Agent"]}, timeout=12)
        if r.status_code != 200:
            return None
        df = pd.read_csv(StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        return df if "Symbol" in df else None
    except Exception:
        return None


def archive_constituents(index_name):
    """Symbols (+industry) from NSE's archive CSV. Works when the live API is blocked."""
    slug = ARCHIVE_SLUGS.get(str(index_name).strip().upper())
    df = _read_archive(slug) if slug else None
    if df is None:
        return None
    out = pd.DataFrame({"symbol": df["Symbol"].astype(str).str.upper().str.strip()})
    if "Industry" in df:
        out["industry"] = df["Industry"].astype(str).str.strip()
    return out.drop_duplicates("symbol").reset_index(drop=True)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=2)
def universe_meta():
    """Stock universe + NSE industry classification (Nifty Total Market, else Nifty 500 + Microcap 250)."""
    df = None
    for slugs in (["ind_niftytotalmarket_list"], ["ind_nifty500list", "ind_niftymicrocap250_list"], ["ind_nifty500list"]):
        parts = [p for p in (_read_archive(s) for s in slugs) if p is not None]
        if parts and sum(len(p) for p in parts) >= 300:
            df = pd.concat(parts, ignore_index=True)
            break
    if df is None:
        return None
    out = pd.DataFrame({
        "symbol": df["Symbol"].astype(str).str.upper().str.strip(),
        "industry": df["Industry"].astype(str).str.strip() if "Industry" in df else "Unclassified",
        "company": df["Company Name"].astype(str).str.strip() if "Company Name" in df else ""})
    return out.drop_duplicates("symbol").reset_index(drop=True)


@st.cache_data(ttl=900, show_spinner=False, max_entries=2)
def load_universe_frames(symbols: tuple) -> dict:
    """~2 years of daily OHLCV for the whole stock universe + NIFTY 50 (batched Yahoo download).
    Two years so the 200-EMA of every sector is meaningful."""
    frames = fe._download_frames([f"{s}.NS" for s in symbols] + ["^NSEI"], "2y")
    if len(frames) < 30:
        raise ValueError(f"Yahoo returned history for only {len(frames)} tickers")
    return frames


def local_constituents(index_name):
    """Optional: index_constituents.csv (columns: index,symbol[,weight]) in the repo root."""
    p = Path("index_constituents.csv")
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
        df.columns = [c.strip().lower() for c in df.columns]
        sub = df[df["index"].astype(str).str.upper().str.strip().eq(str(index_name).upper().strip())]
        if sub.empty:
            return None
        out = pd.DataFrame({"symbol": sub["symbol"].astype(str).str.upper().str.strip()})
        out["ffmc"] = pd.to_numeric(sub["weight"], errors="coerce") if "weight" in sub else np.nan
        return out.drop_duplicates("symbol").reset_index(drop=True)
    except Exception:
        return None


def get_constituent_symbols(index_name):
    """NSE live API -> NSE archive CSV -> local CSV. Returns (DataFrame|None, source label)."""
    cons = fetch_constituents(index_name)
    if cons is not None and not cons.empty:
        return cons, "NSE live constituents"
    cons = archive_constituents(index_name)
    if cons is not None and not cons.empty:
        return cons, "NSE archive CSV (equal-weight)"
    cons = local_constituents(index_name)
    if cons is not None and not cons.empty:
        return cons, "local index_constituents.csv"
    return None, ""


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def fetch_fii_dii():
    payload = _get("/api/fiidiiTradeReact")
    rows = payload.get("data", payload.get("records", [])) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        return None
    df = pd.DataFrame(rows)
    for target, aliases in {"category": ("investorType", "clientType", "categoryName"),
                            "buyValue": ("buy", "buy_value"), "sellValue": ("sell", "sell_value"),
                            "netValue": ("net", "net_value"), "date": ("tradingDate", "tradeDate", "timestamp")}.items():
        _rename(df, target, aliases)
    _numeric(df, ["buyValue", "sellValue", "netValue"])
    if "category" not in df:
        df["category"] = "Investor"
    return df


# ═════════════════════════ benchmarks for the sector charts ═════════════════════════
def _yf_close(ticker: str):
    df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = se.norm_series(s)
    return s if len(s) >= 200 else None


@st.cache_data(ttl=1800, show_spinner=False, max_entries=2)
def _yahoo_candidates():
    """{benchmark: [(ticker, close Series), ...]} for every candidate ticker that returned data."""
    out = {}
    for name, cands in BENCH_YF.items():
        found = []
        for t in cands:
            try:
                s = _yf_close(t)
            except Exception:
                continue
            if s is not None and (pd.Timestamp.now().normalize() - s.index[-1]).days <= 7:
                found.append((t, s))
        out[name] = found
    if not any(out.values()):
        raise ValueError("no benchmark candidate returned data")          # exceptions are never cached
    return out


def _proxy_benchmark(frames: dict, index_name: str):
    """Equal-weight, daily-rebalanced index of an index's constituents (built from the universe frames)."""
    cons = archive_constituents(index_name)
    if cons is None:
        return None
    cols = {s: frames[f"{s}.NS"]["Close"] for s in cons["symbol"] if f"{s}.NS" in frames}
    if len(cols) < 20:
        return None
    px = pd.DataFrame(cols).sort_index().ffill()
    r = (px / px.shift(1) - 1).mean(axis=1)
    return se.norm_series((1 + r.fillna(0)).cumprod() * 100)


def _matches_proxy(candidate: pd.Series, proxy) -> bool:
    """A candidate ticker must move like the index it claims to be (correlation of daily returns > 0.9)."""
    if proxy is None:
        return True
    a = candidate / candidate.shift(1) - 1
    b = proxy / proxy.shift(1) - 1
    j = pd.concat([a, b], axis=1, join="inner").dropna().tail(120)
    return len(j) >= 40 and float(j.corr().iloc[0, 1]) > 0.9


def _build_benchmarks(frames: dict):
    """({name: close Series}, {name: source label}) for NIFTY 50, MIDCAP 100, SMALLCAP 100."""
    benches, sources = {}, {}
    n50 = frames.get("^NSEI")
    if n50 is not None and len(n50) > 60:
        benches["NIFTY 50"] = se.norm_series(n50["Close"])
        sources["NIFTY 50"] = "Yahoo ^NSEI"
    try:
        cands = _yahoo_candidates()
    except Exception:
        cands = {}
    for name in ("MIDCAP 100", "SMALLCAP 100"):
        proxy = _proxy_benchmark(frames, BENCH_ARCHIVE[name])
        chosen = next(((t, s) for t, s in cands.get(name, []) if _matches_proxy(s, proxy)), None)
        if chosen:
            benches[name], sources[name] = chosen[1], f"Yahoo {chosen[0]}"
        elif proxy is not None:
            benches[name], sources[name] = proxy, f"proxy · equal-weight {BENCH_ARCHIVE[name]} stocks"
    return benches, sources


@st.cache_data(ttl=900, show_spinner=False, max_entries=2)
def _sector_analysis(symbols: tuple):
    """Heavy part of SECTOR RANKING, cached 15 min: frames -> benchmarks -> sector analytics."""
    meta = universe_meta()
    if meta is None:
        raise ValueError("stock universe unavailable")
    frames = load_universe_frames(symbols)
    benches, sources = _build_benchmarks(frames)
    res = se.analyse_universe(frames, meta, "^NSEI", benches or None)
    res["bench_sources"] = sources
    return res


# ═════════════════════════ analytics ═════════════════════════
def rank_sectors(indices, scope="SECTORAL"):
    """RS rank vs NIFTY 50, LEADER/EMERGING/FADING/LAGGARD label, rotation quadrant."""
    if indices is None or indices.empty or "NIFTY 50" not in indices["index"].values:
        return pd.DataFrame()
    keys = SCOPES.get(scope, ("SECTORAL",))
    if keys:
        cats = indices["key"].astype(str).str.upper().str.replace("_", " ", regex=False)
        sector = indices[cats.apply(lambda x: any(k in x for k in keys))].copy()
    else:
        sector = indices.copy()
    sector = sector[sector["index"].ne("NIFTY 50")].copy()
    if sector.empty:
        return sector
    base = indices.loc[indices["index"].eq("NIFTY 50")].iloc[0]
    for p in ("1d", "1w", "1m"):
        sector[f"rs_{p}"] = sector[f"r{p}"] - base[f"r{p}"]
    # 20/30/50 weights re-normalised over the periods that actually have data
    num = pd.Series(0.0, index=sector.index)
    den = pd.Series(0.0, index=sector.index)
    for col, wt in (("rs_1d", .2), ("rs_1w", .3), ("rs_1m", .5)):
        ok = sector[col].notna()
        num.loc[ok] += sector.loc[ok, col] * wt
        den.loc[ok] += wt
    sector["score"] = num / den.replace(0, np.nan)
    sector["rank"] = sector["score"].rank(ascending=False, method="min").astype("Int64")
    b1w, b1m = base.get("r1w"), base.get("r1m")
    sector["rot_x"] = (1 + sector["r1m"] / 100) / (1 + b1m / 100) * 100 if pd.notna(b1m) else np.nan
    sector["rot_y"] = (1 + sector["r1w"] / 100) / (1 + b1w / 100) * 100 if pd.notna(b1w) else np.nan

    def quadrant(r):
        if pd.isna(r.rot_x) or pd.isna(r.rot_y): return "UNAVAILABLE"
        return ("LEADING" if r.rot_y >= 100 else "WEAKENING") if r.rot_x >= 100 else ("IMPROVING" if r.rot_y >= 100 else "LAGGING")

    def leadership(r):
        if pd.isna(r.rs_1w) or pd.isna(r.rs_1m): return "—"
        if r.rs_1w > 0 and r.rs_1m > 0: return "LEADER"
        if r.rs_1w > 0: return "EMERGING"
        if r.rs_1m > 0: return "FADING"
        return "LAGGARD"

    sector["quadrant"] = sector.apply(quadrant, axis=1)
    sector["leadership"] = sector.apply(leadership, axis=1)
    return sector.sort_values(["rank", "index"], na_position="last").reset_index(drop=True)


def flow_stats(cons):
    """Price-signed turnover proxy; reported turnover units are intentionally preserved."""
    if cons is None or cons.empty or not {"pChange", "totalTradedValue"}.issubset(cons.columns):
        return None
    data = cons.dropna(subset=["pChange", "totalTradedValue"])
    up = float(data.loc[data.pChange > 0, "totalTradedValue"].sum())
    down = float(data.loc[data.pChange < 0, "totalTradedValue"].sum())
    total = up + down
    return {"up_value": up, "down_value": down, "net_value": up - down,
            "net_pct": (up - down) / total * 100 if total else np.nan,
            "up": int((data.pChange > 0).sum()), "down": int((data.pChange < 0).sum()),
            "flat": int((data.pChange == 0).sum()), "stocks": len(data)}


def stock_breadth(cons):
    if cons is None or cons.empty or "pChange" not in cons:
        return None, pd.DataFrame()
    data = cons.copy()
    data["pChange"] = pd.to_numeric(data["pChange"], errors="coerce")
    data = data.dropna(subset=["pChange"])
    up, down, flat = int((data.pChange > 0).sum()), int((data.pChange < 0).sum()), int((data.pChange == 0).sum())
    total = len(data)
    ratio = up / down if down else (float(up) if up else np.nan)
    for c in ("nearWKH", "nearWKL"):
        if c in data: data[c] = pd.to_numeric(data[c], errors="coerce")
    high = int(data.nearWKH.between(0, 1).sum()) if "nearWKH" in data else None
    low = int(data.nearWKL.between(0, 1).sum()) if "nearWKL" in data else None
    stats = {"total": total, "up": up, "down": down, "flat": flat, "net": up - down, "ratio": ratio,
             "up_pct": up / total * 100 if total else np.nan,
             "median": data.pChange.median() if total else np.nan, "near_high": high, "near_low": low}
    return stats, data


# ═════════════════════════ charts / tables ═════════════════════════
def _rotation_chart(data):
    """matplotlib fallback for the NSE-index rotation map (columns: index, rot_x, rot_y, quadrant)."""
    data = data.dropna(subset=["rot_x", "rot_y"])
    if data.empty:
        st.info("Rotation plot unavailable: press LOAD INDEX HISTORY above to fetch the reference returns."); return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        st.info("Install matplotlib for the rotation plot; the table remains available."); return
    fig, ax = plt.subplots(figsize=(9, 5))
    fig.patch.set_facecolor("#090b0f"); ax.set_facecolor("#090b0f")
    dx = max(float((data.rot_x - 100).abs().max()) * 1.2, .6)
    dy = max(float((data.rot_y - 100).abs().max()) * 1.2, .6)
    ax.set(xlim=(100 - dx, 100 + dx), ylim=(100 - dy, 100 + dy))
    ax.axhline(100, color="#39404b", lw=1); ax.axvline(100, color="#39404b", lw=1)
    for _, r in data.iterrows():
        ax.scatter(r.rot_x, r.rot_y, s=32, color=QUAD.get(r.quadrant, "#9ca3af"))
        ax.annotate(str(r["index"]).replace("NIFTY ", ""), (r.rot_x, r.rot_y), xytext=(4, 3),
                    textcoords="offset points", fontsize=6.5, color="#d7dce2")
    ax.set_xlabel("Relative trend · 1 month (100 = in line)", color="#9ca3af")
    ax.set_ylabel("Relative momentum · 1 week (100 = in line)", color="#9ca3af")
    ax.tick_params(colors="#9ca3af", labelsize=8)
    for spine in ax.spines.values(): spine.set_color("#303640")
    st.pyplot(fig, use_container_width=True); plt.close(fig)


def _index_table(df, key):
    cols = [c for c in ["index", "key", "last", "r1d", "r1w", "r1m", "advances", "declines", "unchanged",
                        "net_adv", "adv_pct", "ad_ratio", "pe", "pb", "dy"] if c in df]
    view = df[cols].rename(columns={"index": "Index", "key": "Category", "last": "Last", "r1d": "1D %", "r1w": "1W %",
                                    "r1m": "1M %", "advances": "Adv", "declines": "Dec", "unchanged": "Flat",
                                    "net_adv": "Net Adv", "adv_pct": "Advancing %", "ad_ratio": "A/D",
                                    "pe": "P/E", "pb": "P/B", "dy": "Div Yield"}).copy()
    _fmt_ret(view, ["1D %", "1W %", "1M %"])
    st.dataframe(view, hide_index=True, use_container_width=True, key=key, column_config={
        "Advancing %": st.column_config.ProgressColumn("Advancing %", min_value=0, max_value=100, format="%.0f%%")})


def _quad_pill(name: str) -> str:
    c = QUAD.get(name, "#9ca3af")
    return f'<span class="mf-stage" style="--sc:{c}">{ui.esc(name)}</span>'


# ═════════════════════════ INDEX EXPLORER ═════════════════════════
_EX_RENAME = {"symbol": "Symbol", "price": "Price", "weight": "Weight %", "r1d": "1D %", "r1w": "1W %",
              "r1m": "1M %", "r3m": "3M %", "rs_1w": "RS 1W", "rs_1m": "RS 1M", "rs_3m": "RS 3M",
              "relvol": "Rel Vol ×", "tv_ratio": "Value ×", "close_loc": "Close Loc",
              "buy_score": "Buy Score", "sell_score": "Sell Score", "pressure": "Pressure",
              "contrib": "Contrib (pp)"}


def _ex_cfg():
    cfg = {c: st.column_config.NumberColumn(c, format="%+.2f") for c in
           ["1D %", "1W %", "1M %", "3M %", "RS 1W", "RS 1M", "RS 3M", "Contrib (pp)"]}
    cfg["Weight %"] = st.column_config.NumberColumn("Weight %", format="%.2f")
    cfg["Rel Vol ×"] = st.column_config.NumberColumn("Rel Vol ×", format="%.2f")
    cfg["Value ×"] = st.column_config.NumberColumn("Value ×", format="%.2f")
    cfg["Close Loc"] = st.column_config.NumberColumn("Close Loc", format="%.2f", help="0 = closed at the day's low, 1 = at the day's high")
    cfg["Buy Score"] = st.column_config.ProgressColumn("Buy Score", min_value=0, max_value=100, format="%d")
    cfg["Sell Score"] = st.column_config.ProgressColumn("Sell Score", min_value=0, max_value=100, format="%d")
    return cfg


def _ex_view(df, cols, n=None):
    out = df[[c for c in cols if c in df]]
    if n:
        out = out.head(n)
    return out.rename(columns=_EX_RENAME).reset_index(drop=True)


def _render_explorer(indices, on_open=None):
    st.markdown('<div class="arka-mf-section">INDEX EXPLORER · CONSTITUENT BREADTH, STRENGTH, VOLUME AND PRESSURE</div>', unsafe_allow_html=True)
    names = indices["index"].dropna().astype(str).tolist()
    pref = next((i for i, n in enumerate(names) if n.upper() == "NIFTY BANK"), 0)
    c1, c2, c3 = st.columns([3, 1.4, 1.1])
    with c1:
        index_name = st.selectbox("Index", names, index=pref, key="mf_ex_index")
    with c2:
        bench = st.selectbox("Benchmark for RS", list(fe.BENCHMARKS), key="mf_ex_bench")
    with c3:
        st.write(""); st.write("")
        if st.button("ANALYSE INDEX", type="primary", use_container_width=True, key="mf_ex_run"):
            st.session_state["mf_ex_target"] = (index_name, bench)

    target = st.session_state.get("mf_ex_target")
    if not target:
        st.info("Choose an index and press ANALYSE INDEX. Arka downloads every constituent's price history in one batch "
                "(large indices such as NIFTY 500 can take a minute the first time; results are cached for 10 minutes).")
        return
    if target != (index_name, bench):
        st.caption(f"Showing results for {target[0]} vs {target[1]} — press ANALYSE INDEX to update.")
    name, bench_name = target

    cons, source = get_constituent_symbols(name)
    if cons is None:
        st.warning(f"No constituent list available for {name}: NSE live API and the NSE archive CSV both returned nothing. "
                   "Add an index_constituents.csv (columns: index,symbol[,weight]) to the repo root as a manual fallback.")
        return
    symbols = cons["symbol"].tolist()
    weights = dict(zip(cons["symbol"], cons["ffmc"])) if "ffmc" in cons else {}
    with st.spinner(f"Analysing {len(symbols)} constituents of {name}…"):
        res = fe.analyse_index(symbols, weights, bench_name)
    if res is None or "error" in res:
        st.error("Could not analyse this index: " + (res or {}).get("error", "unknown error") +
                 ". Yahoo may be rate-limiting; try again in a minute.")
        return
    t, s, series = res["table"], res["summary"], res["series"]

    color = SIGNAL_COLOR.get(s["signal"], "#9ca3af")
    st.markdown(f'<div class="arka-mf-banner" style="color:{color};border-color:{color}55">{name} · {s["signal"]} · '
                f'index proxy {_pct(s["idx_1d"])} today with {_num(s["adv_pct"], 0)}% of stocks advancing</div>', unsafe_allow_html=True)
    if s["deterioration"]:
        st.warning("BREADTH DETERIORATION: the index proxy is within 2% of its 60-day high, but the share of stocks above their "
                   "50-DMA has fallen 10+ points over the last 10 sessions. Fewer stocks are supporting the move.")

    m = st.columns(6)
    m[0].metric("Stocks analysed", s["stocks"], help=f"Constituents: {source}. Missing: {', '.join(s['missing'][:8]) or 'none'}")
    m[1].metric("Advancing today", f'{_num(s["adv_pct"], 0)}%')
    m[2].metric("Above 20 DMA", f'{_num(s["above20"], 0)}%')
    m[3].metric("Above 50 DMA", f'{_num(s["above50"], 0)}%')
    m[4].metric("Above 200 DMA", f'{_num(s["above200"], 0)}%')
    m[5].metric("Proxy 1M", _pct(s["idx_1m"]), help=f"Weighted by {s['weight_basis']}. An approximation of the index, not the official index value.")
    st.caption(f"As of {s['asof']} close · benchmark {bench_name} · weights: {s['weight_basis']} · "
               f"{len(s['missing'])} symbol(s) had no Yahoo data.")

    st.markdown('<div class="arka-mf-section">PARTICIPATION OVER TIME · % OF STOCKS ABOVE MOVING AVERAGES</div>', unsafe_allow_html=True)
    plot = series.dropna(subset=["above50"]).set_index("date")[["above20", "above50", "above200"]].rename(
        columns={"above20": "Above 20 DMA %", "above50": "Above 50 DMA %", "above200": "Above 200 DMA %"})
    if not plot.empty:
        st.line_chart(plot)

    a, b = st.columns(2)
    with a:
        st.markdown('<div class="arka-mf-section">TOP CONTRIBUTORS TO TODAY\'S MOVE</div>', unsafe_allow_html=True)
        st.dataframe(_ex_view(t.sort_values("contrib", ascending=False), ["symbol", "weight", "r1d", "contrib"], 8),
                     hide_index=True, use_container_width=True, column_config=_ex_cfg())
    with b:
        st.markdown('<div class="arka-mf-section">BIGGEST DRAGS ON TODAY\'S MOVE</div>', unsafe_allow_html=True)
        st.dataframe(_ex_view(t.sort_values("contrib"), ["symbol", "weight", "r1d", "contrib"], 8),
                     hide_index=True, use_container_width=True, column_config=_ex_cfg())
    st.markdown('<div class="arka-mf-note">Contribution = weight × 1-day return, in percentage points of the index move. '
                'It shows when a few heavyweights are carrying (or dragging) the index.</div>', unsafe_allow_html=True)

    a, b = st.columns(2)
    with a:
        st.markdown(f'<div class="arka-mf-section">RELATIVE-STRENGTH LEADERS · 1M VS {bench_name}</div>', unsafe_allow_html=True)
        st.dataframe(_ex_view(t.sort_values("rs_1m", ascending=False), ["symbol", "r1m", "rs_1w", "rs_1m", "rs_3m"], 10),
                     hide_index=True, use_container_width=True, column_config=_ex_cfg())
    with b:
        st.markdown(f'<div class="arka-mf-section">RELATIVE-STRENGTH LAGGARDS · 1M VS {bench_name}</div>', unsafe_allow_html=True)
        st.dataframe(_ex_view(t.sort_values("rs_1m"), ["symbol", "r1m", "rs_1w", "rs_1m", "rs_3m"], 10),
                     hide_index=True, use_container_width=True, column_config=_ex_cfg())

    st.markdown('<div class="arka-mf-section">VOLUME WATCH · RELATIVE VOLUME ≥ 1.5× (20-DAY AVERAGE)</div>', unsafe_allow_html=True)
    vw = t[t.relvol >= 1.5].sort_values("relvol", ascending=False)
    if vw.empty:
        st.info("No constituent is trading at 1.5× its 20-day average volume. (During market hours today's volume is still building.)")
    else:
        st.dataframe(_ex_view(vw, ["symbol", "price", "r1d", "relvol", "tv_ratio", "close_loc", "rs_1m"], 20),
                     hide_index=True, use_container_width=True, column_config=_ex_cfg())

    a, b = st.columns(2)
    with a:
        st.markdown('<div class="arka-mf-section">BUYING-PRESSURE CANDIDATES</div>', unsafe_allow_html=True)
        bp = t[t.pressure.eq("BUYING PRESSURE")].sort_values(["buy_score", "relvol"], ascending=False)
        if bp.empty: st.info("None meet the buying-pressure threshold today.")
        else: st.dataframe(_ex_view(bp, ["symbol", "r1d", "relvol", "close_loc", "buy_score"], 15), hide_index=True, use_container_width=True, column_config=_ex_cfg())
    with b:
        st.markdown('<div class="arka-mf-section">SELLING-PRESSURE WATCH</div>', unsafe_allow_html=True)
        sp = t[t.pressure.eq("SELLING PRESSURE")].sort_values(["sell_score", "relvol"], ascending=False)
        if sp.empty: st.info("None meet the selling-pressure threshold today.")
        else: st.dataframe(_ex_view(sp, ["symbol", "r1d", "relvol", "close_loc", "sell_score"], 15), hide_index=True, use_container_width=True, column_config=_ex_cfg())
    st.markdown('<div class="arka-mf-note">Pressure score (0-100) = price direction 20 + volume expansion 25 + close location 20 + 1W relative strength 15 '
                '+ 1M relative strength 10 + trend vs 50-DMA 10. It is evidence from price and volume, NOT proof that institutions bought or sold: '
                'every trade has a buyer and a seller. Use it to shortlist, then verify on the chart.</div>', unsafe_allow_html=True)

    st.markdown('<div class="arka-mf-section">ALL CONSTITUENTS · SELECT A ROW TO OPEN THE STOCK</div>', unsafe_allow_html=True)
    q = st.text_input("Find stock", key="mf_ex_search").strip().upper()
    full = t.sort_values("rs_1m", ascending=False)
    if q:
        full = full[full.symbol.str.contains(q, na=False)]
    cols = ["symbol", "price", "weight", "r1d", "r1w", "r1m", "r3m", "rs_1m", "relvol", "close_loc", "buy_score", "sell_score", "pressure"]
    view = _ex_view(full, cols)
    event = st.dataframe(view, hide_index=True, use_container_width=True, column_config=_ex_cfg(),
                         on_select="rerun" if on_open else "ignore",
                         selection_mode="single-row" if on_open else "multi-row", key="mf_ex_table")
    selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
    if selected and on_open:
        on_open(str(view.iloc[int(selected[0])]["Symbol"]))


# ═════════════════════════ NIFTY INDICES (sectoral / thematic) ═════════════════════════
def _scope_rows(indices, scope):
    keys = SCOPES.get(scope, ())
    cats = indices["key"].astype(str).str.upper().str.replace("_", " ", regex=False)
    sub = indices[cats.apply(lambda x: any(k in x for k in keys))] if keys else indices
    return _equity_index_rows(sub)


def _render_nifty_indices(indices):
    st.markdown('<div class="arka-mf-section">NIFTY INDICES · RSI, EMA POSITION, DISTANCE FROM EMAs AND PIVOTS</div>', unsafe_allow_html=True)
    scope = st.radio("Universe", ["SECTORAL + THEMATIC", "SECTORAL", "THEMATIC", "BROAD MARKET"], horizontal=True, key="mf_ni_scope")
    mode = st.radio("Change columns", ["Daily", "Weekly", "Both"], horizontal=True, key="mf_ni_mode", index=2)
    sub = _scope_rows(indices, scope)
    names = sub["index"].astype(str).tolist()
    st.caption(f"{len(names)} NSE {scope.lower()} indices in the live feed. History-based columns need LOAD INDEX HISTORY (top of page).")
    hists = index_histories(names + ["NIFTY 50"])
    have = [n for n in names if n in hists]
    live = sub.set_index("index")

    if not have:
        st.info("History not loaded yet — showing the live snapshot only. Press LOAD INDEX HISTORY above to add RSI, EMA chips, EMA distance and pivots.")
        cols = [ui.C("#", t="rank"), ui.C("Index", "index", "sector"), ui.C("Price", "last", "num"),
                ui.C("1D", "r1d", "pct"), ui.C("Adv", "advances", "int"), ui.C("Dec", "declines", "int")]
        rows = sub.sort_values("r1d", ascending=False, na_position="last").to_dict("records")
        ui.show_table(cols, rows)
        return
    try:
        res = ix.analyse_histories({n: hists[n] for n in have}, "NIFTY 50")
    except Exception as exc:
        st.error(f"Could not analyse index history: {exc}")
        return
    res = res[res["index"].isin(names)].copy()
    res["price"] = res["index"].map(live["last"]).fillna(res["price"])
    res["r1d"] = res["index"].map(live["percentChange"]).fillna(res["r1d"])

    sorts = {"1D %": ("r1d", False), "1W %": ("r1w", False), "RSI (daily)": ("d_rsi", False), "RSI (weekly)": ("w_rsi", False),
             "Distance from 20-EMA": ("dist20", False), "Distance from 10-EMA": ("dist10", False), "Name": ("index", True)}
    label = st.selectbox("Sort by", list(sorts), key="mf_ni_sort")
    col, asc = sorts[label]
    res = res.sort_values(col, ascending=asc, na_position="last")

    cols = [ui.C("#", t="rank"), ui.C("Index", "index", "sector"), ui.C("Price", "price", "num")]
    if mode in ("Daily", "Both"):
        cols.append(ui.C("1D", "r1d", "pct", scale=2.0))
    if mode in ("Weekly", "Both"):
        cols.append(ui.C("1W", "r1w", "pct", scale=4.0))
    cols += [ui.C("RSI D", "d_rsi", "rsi", tip="Daily RSI(14)"), ui.C("RSI W", "w_rsi", "rsi", tip="Weekly RSI(14)"),
             ui.C("EMA 10·20·50·200", t="ema", tip="Green = price above that daily EMA, red = below"),
             ui.C("vs 10", "dist10", "dist", scale=5.0, tip="% distance of price from the 10-EMA"),
             ui.C("vs 20", "dist20", "dist", scale=8.0, tip="% distance of price from the 20-EMA"),
             ui.C("vs 50", "dist50", "dist", scale=12.0),
             ui.C("D-Pivot", "d_pivot", "text"), ui.C("W-Pivot", "w_pivot", "text")]
    ui.show_table(cols, res.to_dict("records"), max_height=720)
    miss = [n for n in names if n not in hists]
    asof = res["asof"].max() if "asof" in res else ""
    st.markdown(f'<div class="arka-mf-note">History as of {asof} close · {len(have)} of {len(names)} indices have history'
                f'{(" (missing: " + ", ".join(miss[:6]) + ("…" if len(miss) > 6 else "") + ")") if miss else ""}. '
                'EMA chips use DAILY EMAs: green = price above, red = below, grey = fewer bars than that EMA needs. '
                '"vs 10 / vs 20" = % distance of price from the EMA (amber = stretched more than 6% above the 20-EMA). '
                'RSI: 70+ hot, 50-70 bullish, below 40 weak. Pivots: classic levels from the previous completed day / week.</div>', unsafe_allow_html=True)


# ═════════════════════════ SECTOR RANKING (stock universe) ═════════════════════════
_SHORT = {"NIFTY 50": "NIFTY 50", "MIDCAP 100": "MIDCAP 100", "SMALLCAP 100": "SMALLCAP 100"}
_VIEWS = ["ROTATION RADAR", "SECTOR RANKING", "TREND · RSI & EMA", "VS BENCHMARKS", "TURNOVER A/D", "SECTOR TURNOVER", "CHAIKIN MONEY FLOW"]


def _sorted_records(df: pd.DataFrame, key: str, options: dict, default: str):
    label = st.selectbox("Sort by", list(options), index=list(options).index(default), key=f"mf_sr_sort_{key}")
    col, asc = options[label]
    if col in df:
        df = df.sort_values(col, ascending=asc, na_position="last")
    return df.to_dict("records")


def _plot(fig, key, fallback_df=None):
    if fig is None:
        if fallback_df is not None:
            st.line_chart(fallback_df)
        else:
            st.info("Install plotly (add `plotly` to requirements.txt) to see this chart.")
        return
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


def _view_radar(rot, trails, res, bench):
    stages = rot["stage"].value_counts()
    names = lambda s: ", ".join(rot.loc[rot.stage == s, "sector"].head(3)) or "none right now"
    kp = [("Rotating in", int(stages.get("ROTATING IN", 0)), names("ROTATING IN"), ui.GREEN),
          ("Leading", int(stages.get("LEADING", 0)), names("LEADING"), "#8AD66B"),
          ("Improving", int(stages.get("IMPROVING", 0)), names("IMPROVING"), ui.CYAN),
          ("Extended", int(stages.get("EXTENDED", 0)), names("EXTENDED"), ui.YELLOW),
          ("Weakening", int(stages.get("WEAKENING", 0)), names("WEAKENING"), ui.AMBER),
          ("Lagging", int(stages.get("LAGGING", 0)), names("LAGGING"), ui.RED)]
    st.markdown(ui.kpi_strip(kp), unsafe_allow_html=True)

    st.markdown(f'<div class="arka-mf-section">SECTORS TURNING UP · CONFIRMED BY TREND, MOMENTUM, RELATIVE STRENGTH vs {bench}, BREADTH AND FLOW</div>', unsafe_allow_html=True)
    top = rot[rot.stage == "ROTATING IN"].head(3)
    if len(top) < 3:
        extra = rot[rot.stage.isin(["IMPROVING", "LEADING"]) & ~rot.sector.isin(top.sector)].head(3 - len(top))
        if top.empty:
            st.info("No sector shows a confirmed fresh turn today (ROTATING IN needs a new 20-EMA reclaim / RSI-50 cross / 10-20 EMA cross / RS turn, "
                    "price above the 20-EMA, RSI 50+, not stretched, score 55+). Showing the strongest improving / leading sectors instead.")
        top = pd.concat([top, extra])
    if not top.empty:
        st.markdown('<div class="mf-picks">' + "".join(ui.pick_card(r, bench) for r in top.to_dict("records")) + "</div>", unsafe_allow_html=True)

    st.markdown('<div class="arka-mf-section">ROTATION RADAR · ALL SECTORS</div>', unsafe_allow_html=True)
    opts = {"Rotation score": ("score", False), "1D %": ("r1d", False), "1W %": ("r1w", False), "1M %": ("r1m", False),
            "RSI (daily)": ("rsi_d", False), "Distance from 20-EMA": ("dist20", False), "RS-Momentum": ("rrg_y", False),
            "Turnover share Δ (1W)": ("dshare_w", False)}
    rows = _sorted_records(rot, "radar", opts, "Rotation score")
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{} stocks"),
            ui.C("Stage", "stage", "stage"), ui.C("Score", "score", "score", tip="0-100 rotation score (see methodology)"),
            ui.C("RS-Ratio", "rrg_x", "num", d=1, tip=f"Relative trend vs {bench}: above 100 = outperforming its own 50-session trend"),
            ui.C("RS-Mom", "rrg_y", "num", d=1, tip="Is the relative trend accelerating? above 100 = yes"),
            ui.C("1D", "r1d", "pct", scale=2.0), ui.C("1W", "r1w", "pct", scale=4.0), ui.C("RSI", "rsi_d", "rsi"),
            ui.C("EMA 10·20·50·200", t="ema"), ui.C("vs 10", "dist10", "dist", scale=5.0), ui.C("vs 20", "dist20", "dist"),
            ui.C("Fresh signals", "signals", "tags"),
            ui.C("% >20EMA", "pct_above20", "num", d=0, suf="%", tip="Share of the sector's stocks above their 20-EMA"),
            ui.C("CMF", "cmf", "num", d=2, tip="Chaikin money flow (20)"), ui.C("Leaders (1W)", "leaders", "muted")]
    ui.show_table(cols, rows, max_height=760)

    st.markdown(f'<div class="arka-mf-section">ROTATION MAP vs {bench} · TRAILS = LAST {se.TRAIL_LEN * se.TRAIL_STEP} SESSIONS (5-SESSION STEPS)</div>', unsafe_allow_html=True)
    fig = ui.fig_rrg(rot, trails)
    if fig is None:
        _rotation_chart(rot.rename(columns={"sector": "index", "rrg_x": "rot_x", "rrg_y": "rot_y"}))
    else:
        _plot(fig, "mf_sr_rrg")
    st.markdown('<div class="arka-mf-note">Sectors usually travel clockwise: IMPROVING (top-left) → LEADING (top-right) → WEAKENING (bottom-right) → LAGGING (bottom-left). '
                'The early-rotation zone is a sector moving from LAGGING into IMPROVING and then crossing into LEADING, with its trail pointing up and right.</div>', unsafe_allow_html=True)

    with st.expander("HOW THE ROTATION SCORE AND STAGES WORK"):
        st.markdown(
            "**Sector index.** Equal-weight, daily-rebalanced average of the sector's stocks, so every sector is read like a chart.\n\n"
            "**Score (0-100).** Trend 30 (above 20-EMA 10, above 50-EMA 10, 10-EMA over 20-EMA 5, 20-EMA over 50-EMA 5) · "
            "Momentum 20 (RSI above 50: 10, RSI rising over 5 sessions: 5, RSI not above 75: 5) · "
            "Relative strength 25 (RS-Ratio at or above 100: 10, RS-Momentum at or above 100: 10, RS line above its 20-EMA: 5) · "
            "Breadth 15 (half or more of the stocks above their 20-EMA: 8, that share rising over 5 sessions: 7) · "
            "Flow 10 (Chaikin money flow above zero: 5, turnover share up over the week: 5).\n\n"
            "**Stages.** ROTATING IN = leading or improving quadrant + a fresh turn in the last 5-10 sessions (20-EMA reclaim, RSI back above 50, "
            "10/20-EMA cross or the RS line crossing up) + price above the 20-EMA + RSI 50 or more + not stretched + score 55 or more. "
            "LEADING = trend intact. EXTENDED = leading but more than 6% above the 20-EMA or RSI above 72 (moves like this often pause first). "
            "IMPROVING = relative momentum is turning up but price has not confirmed. WEAKENING / LAGGING = outperformance fading / underperforming.\n\n"
            "**RS-Ratio / RS-Momentum.** RRG-style and simplified (not the licensed JdK RRG): RS line = sector index ÷ benchmark; "
            "RS-Ratio = 100 × RS ÷ its 50-session average; RS-Momentum = RS-Ratio ÷ its value 10 sessions ago × 100.\n\n"
            "**Use it as a shortlist.** These are signals from price, breadth and volume, not forecasts or advice: "
            "check the leader stocks on a chart, consider position size and where you would be wrong, and remember equal-weight sector "
            "indices differ from NSE's official sector indices.")


def _view_ranking(rot, bench):
    opts = {"Rotation score": ("score", False), "1D %": ("r1d", False), "1W %": ("r1w", False), "1M %": ("r1m", False),
            "3M %": ("r3m", False), f"vs {bench} 1M": ("ex1m", False), "RSI (daily)": ("rsi_d", False),
            "Distance from 20-EMA": ("dist20", False), "Turnover": ("turnover_cr", False), "Turnover share Δ (1W)": ("dshare_w", False)}
    rows = _sorted_records(rot, "rank", opts, "Rotation score")
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{} stocks"),
            ui.C("30D", "spark", "spark"),
            ui.C("1D", "r1d", "pct", scale=2.0), ui.C("1W", "r1w", "pct", scale=4.0), ui.C("1M", "r1m", "pct", scale=8.0),
            ui.C("3M", "r3m", "pct", scale=15.0), ui.C(f"vs {bench[:9]} 1M", "ex1m", "pct", scale=6.0, tip="Excess return vs the benchmark, percentage points"),
            ui.C("RSI", "rsi_d", "rsi"), ui.C("EMA 10·20·50·200", t="ema"),
            ui.C("vs 10", "dist10", "dist", scale=5.0), ui.C("vs 20", "dist20", "dist"),
            ui.C("Turnover ₹Cr", "turnover_cr", "num", d=0), ui.C("Share %", "share", "num", d=2),
            ui.C("Δ Share 1W", "dshare_w", "num", d=2, tip="Change in turnover share over 5 sessions, percentage points"),
            ui.C("A/D net", "ad_net", "num", d=2), ui.C("% >20EMA", "pct_above20", "num", d=0, suf="%"),
            ui.C("Stage", "stage", "stage")]
    ui.show_table(cols, rows, max_height=760)
    st.markdown('<div class="arka-mf-note">Returns come from the equal-weight sector index (daily rebalanced). EMA chips: green = the sector index is above that daily EMA, red = below. '
                '"vs 10 / vs 20" = % distance from the EMA (amber = more than 6% above the 20-EMA). Share % = sector turnover ÷ universe turnover. '
                'A/D net = (advancers − decliners) ÷ stocks. Turnover = close × volume.</div>', unsafe_allow_html=True)


def _view_trend(rot):
    opts = {"RSI (daily)": ("rsi_d", False), "RSI (weekly)": ("rsi_w", False), "RSI change (5 sessions)": ("rsi_chg", False),
            "Distance from 10-EMA": ("dist10", False), "Distance from 20-EMA": ("dist20", False),
            "Distance from 50-EMA": ("dist50", False), "% stocks above 50-EMA": ("pct_above50", False)}
    rows = _sorted_records(rot, "trend", opts, "Distance from 20-EMA")
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{} stocks"),
            ui.C("RSI D", "rsi_d", "rsi", tip="Daily RSI(14) of the sector index"), ui.C("RSI W", "rsi_w", "rsi", tip="Weekly RSI(14)"),
            ui.C("Δ RSI 5d", "rsi_chg", "num", d=1, tip="RSI now minus RSI 5 sessions ago"), ui.C("Zone", "rsi_zone", "text"),
            ui.C("EMA 10·20·50·200", t="ema"),
            ui.C("vs 10", "dist10", "dist", scale=5.0), ui.C("vs 20", "dist20", "dist"), ui.C("vs 50", "dist50", "dist", scale=12.0),
            ui.C("vs 200", "dist200", "dist", scale=25.0, ext=999), ui.C("Trend", "trend", "text"),
            ui.C("% >50EMA", "pct_above50", "num", d=0, suf="%"), ui.C("% >200EMA", "pct_above200", "num", d=0, suf="%")]
    ui.show_table(cols, rows, max_height=760)
    valid = [r for r in rows if r.get("dist20") is not None and not pd.isna(r.get("dist20"))]
    if valid:
        st.markdown('<div class="arka-mf-section">DISTANCE FROM THE 20-EMA · WHO IS STRETCHED, WHO HAS PULLED BACK</div>', unsafe_allow_html=True)
        valid = sorted(valid, key=lambda r: r["dist20"], reverse=True)
        fig = ui.fig_hbar([r["sector"] for r in valid], [float(r["dist20"]) for r in valid],
                          [ui.AMBER if r["dist20"] > ui.EXTENDED_PCT else (ui.GREEN if r["dist20"] >= 0 else ui.RED) for r in valid])
        if fig is None:
            st.bar_chart(pd.DataFrame({"vs 20-EMA %": [r["dist20"] for r in valid]}, index=[r["sector"] for r in valid]), horizontal=True)
        else:
            _plot(fig, "mf_sr_dist20")
    st.markdown('<div class="arka-mf-note">Green chip = price above that EMA, red = below. Distance shows how far price has run from the average: near zero after a rise is a '
                'pullback to support; a large positive number is stretched. RSI zones: 70+ overbought, 60-70 strong, 50-60 bullish, 40-50 weak, 30-40 bearish, below 30 oversold.</div>', unsafe_allow_html=True)


def _view_benchmarks(res, rot):
    rv, bret = se.returns_vs_benchmarks(res)
    srcs = res.get("bench_sources", {})
    benches = list(res["bench_index"].columns)
    kp = [(b, f'{bret["1m"][b]:+.1f}%' if pd.notna(bret["1m"][b]) else "—",
           f'1M · 3M {bret["3m"][b]:+.1f}% · 1W {bret["1w"][b]:+.1f}%<br>{ui.esc(srcs.get(b, ""))}' if pd.notna(bret["3m"][b]) else ui.esc(srcs.get(b, "")),
           ui.BENCH_COLORS.get(b, ui.T2)) for b in benches]
    st.markdown(ui.kpi_strip(kp), unsafe_allow_html=True)

    st.markdown('<div class="arka-mf-section">EVERY SECTOR vs NIFTY 50 · MIDCAP 100 · SMALLCAP 100 · EXCESS RETURN IN PERCENTAGE POINTS</div>', unsafe_allow_html=True)
    short = {"NIFTY 50": "N50", "MIDCAP 100": "MID100", "SMALLCAP 100": "SML100"}
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector")]
    for p, scale in (("1w", 4.0), ("1m", 8.0), ("3m", 15.0)):
        cols.append(ui.C(f"{p.upper()} return", f"r{p}", "pct", scale=scale))
        for b in benches:
            cols.append(ui.C(f"{p.upper()} vs {short[b]}", f"x{p}_{se.BK[b]}", "pct", scale=scale / 2, tip=f"Sector return minus {b} return"))
    opts = {f"{p.upper()} vs {short[b]}": (f"x{p}_{se.BK[b]}", False) for p in ("1m", "3m", "1w") for b in benches}
    opts.update({"1M return": ("r1m", False), "3M return": ("r3m", False)})
    first = next(iter(opts))
    rows = _sorted_records(rv, "bench", opts, first)
    ui.show_table(cols, rows, max_height=700)

    st.markdown('<div class="arka-mf-section">SECTOR vs BENCHMARKS · REBASED TO 100 AND RELATIVE LINE</div>', unsafe_allow_html=True)
    order = rot["sector"].tolist()
    c1, c2 = st.columns([2, 3])
    sector = c1.selectbox("Sector", order, key="mf_sr_cmp_sector")
    per = c2.radio("Window", ["1M", "3M", "6M", "1Y"], index=1, horizontal=True, key="mf_sr_cmp_window")
    sessions = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252}[per]
    a, b = st.columns(2)
    with a:
        st.markdown('<div class="pf-mini" style="font:10px monospace;color:#8a8a8a">PRICE · everything starts at 100</div>', unsafe_allow_html=True)
        df = se.rebased(res, sector, sessions)
        _plot(ui.fig_lines(df, sector, base_line=True), "mf_sr_cmp_lines", df)
    with b:
        st.markdown('<div class="pf-mini" style="font:10px monospace;color:#8a8a8a">RELATIVE LINE · sector ÷ benchmark, rising = outperforming</div>', unsafe_allow_html=True)
        rt = se.ratios(res, sector, sessions)
        _plot(ui.fig_lines(rt, "", base_line=True), "mf_sr_cmp_ratio", rt)

    if st.checkbox("Show every sector as small multiples (sector vs NIFTY 50 / MIDCAP 100 / SMALLCAP 100)", key="mf_sr_sm_on"):
        per2 = st.radio("Small-multiples window", ["3M", "6M", "1Y"], index=1, horizontal=True, key="mf_sr_sm_window")
        s2 = {"3M": 63, "6M": 126, "1Y": 252}[per2]
        frames = {s: se.rebased(res, s, s2) for s in order}
        fig = ui.fig_multiples(frames)
        if fig is None:
            st.info("Install plotly (add `plotly` to requirements.txt) for the small-multiples grid.")
        else:
            _plot(fig, "mf_sr_multiples")
    st.markdown('<div class="arka-mf-note">Sector lines are equal-weight sector indices built from the stock universe. NIFTY 50 comes from Yahoo; MIDCAP 100 and SMALLCAP 100 use the official index ticker when it '
                'matches the index\'s own constituents, otherwise an equal-weight proxy built from those constituents (labelled above). A rising relative line means the sector is beating that benchmark.</div>', unsafe_allow_html=True)


def _view_turn_ad(rot):
    opts = {"% of turnover in advancers": ("pct_turn_adv", False), "Turnover A/D": ("turn_ad", False), "Up turnover": ("up_turn_cr", False)}
    rows = _sorted_records(rot, "tad", opts, "% of turnover in advancers")
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{} stocks"),
            ui.C("Adv", "adv", "int"), ui.C("Dec", "dec", "int"),
            ui.C("Up turnover ₹Cr", "up_turn_cr", "num", d=0), ui.C("Down turnover ₹Cr", "dn_turn_cr", "num", d=0),
            ui.C("Turnover A/D", "turn_ad", "num", d=2), ui.C("% of turnover in advancers", "pct_turn_adv", "bar", suf="%")]
    ui.show_table(cols, rows, max_height=700)
    chart = pd.DataFrame(rows).set_index("sector")["pct_turn_adv"]
    st.bar_chart(chart, horizontal=True)
    st.markdown('<div class="arka-mf-note">Turnover A/D = turnover of stocks closing up ÷ turnover of stocks closing down. Above 1 means more money changed hands in rising stocks. '
                'Every trade has two sides, so this shows where activity is concentrated, not who is buying.</div>', unsafe_allow_html=True)


def _view_turnover(rot, res):
    per = st.radio("Period", ["Daily", "Weekly"], horizontal=True, key="mf_sr_period")
    dsh = "dshare_d" if per == "Daily" else "dshare_w"
    opts = {"Turnover": ("turnover_cr", False), "Share %": ("share", False), "Today ÷ 20d average": ("turn_vs20", False), f"Δ Share ({per.lower()})": (dsh, False)}
    rows = _sorted_records(rot, "turn", opts, "Turnover")
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{} stocks"),
            ui.C("Turnover ₹Cr", "turnover_cr", "num", d=0), ui.C("20d avg ₹Cr", "turn_avg20_cr", "num", d=0),
            ui.C("Today ÷ 20d", "turn_vs20", "num", d=2, suf="×"), ui.C("Share %", "share", "num", d=2),
            ui.C(f"Δ Share ({per.lower()})", dsh, "num", d=2)]
    ui.show_table(cols, rows, max_height=700)
    top = [r["sector"] for r in sorted(rows, key=lambda r: -(r.get("turnover_cr") or 0))[:6]]
    st.markdown("**TURNOVER SHARE % · LAST 60 SESSIONS · TOP 6 SECTORS**")
    st.line_chart(res["share_series"][top].rename_axis("date"))


def _view_cmf(rot, res):
    rot = rot.copy()
    rot["d_cmf"] = rot["cmf"] - rot["cmf_5d"]
    rot["cmf_signal"] = np.where(rot["cmf"] > .05, "ACCUMULATION", np.where(rot["cmf"] < -.05, "DISTRIBUTION", "NEUTRAL"))
    opts = {"CMF 20": ("cmf", False), "Δ CMF (5d)": ("d_cmf", False)}
    rows = _sorted_records(rot, "cmf", opts, "CMF 20")
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{} stocks"),
            ui.C("CMF 20", "cmf", "num", d=2), ui.C("CMF 5d ago", "cmf_5d", "num", d=2), ui.C("Δ CMF", "d_cmf", "num", d=2),
            ui.C("Signal", "cmf_signal", "text")]
    ui.show_table(cols, rows, max_height=700)
    chart = pd.DataFrame(rows).set_index("sector")["cmf"]
    st.bar_chart(chart, horizontal=True)
    pick = st.multiselect("Plot CMF history", [r["sector"] for r in rows], default=[r["sector"] for r in rows[:4]], key="mf_sr_cmf_pick")
    if pick:
        st.line_chart(res["cmf_series"][pick].rename_axis("date"))
    st.markdown('<div class="arka-mf-note">Chaikin Money Flow (20) = Σ money-flow-volume ÷ Σ volume over 20 sessions, aggregated across the sector\'s stocks. '
                'Positive = closes tend to land in the upper part of the day\'s range on volume (accumulation-like); negative = lower part (distribution-like). '
                'The ±0.05 band is a convention, not a forecast.</div>', unsafe_allow_html=True)


def _drilldown(res, rot, on_open):
    st.markdown('<div class="arka-mf-section">STOCKS IN A SECTOR · RSI, EMA CHIPS, DISTANCE FROM 10 / 20 EMA</div>', unsafe_allow_html=True)
    stk = res["stocks"]
    c1, c2 = st.columns([3, 2])
    pick = c1.selectbox("Sector", rot["sector"].tolist(), key="mf_sr_drill")
    opts = {"Turnover": ("turnover_cr", False), "1D %": ("r1d", False), "1W %": ("r1w", False), "1M %": ("r1m", False),
            "RSI": ("rsi", False), "Distance from 20-EMA": ("dist20", False), "Distance from 10-EMA": ("dist10", False)}
    label = c2.selectbox("Sort stocks by", list(opts), key="mf_sr_drill_sort")
    col, asc = opts[label]
    sub = stk[stk["sector"] == pick].sort_values(col, ascending=asc, na_position="last").copy()
    sub["bo"] = np.where(sub["bo10"], "✔", "—")
    cols = [ui.C("#", t="rank"), ui.C("Symbol", "symbol", "sector"), ui.C("Price", "price", "num"),
            ui.C("1D", "r1d", "pct", scale=3.0), ui.C("1W", "r1w", "pct", scale=6.0), ui.C("1M", "r1m", "pct", scale=12.0),
            ui.C("RSI", "rsi", "rsi"), ui.C("EMA 10·20·50·200", t="ema"),
            ui.C("vs 10", "dist10", "dist", scale=6.0), ui.C("vs 20", "dist20", "dist", scale=10.0),
            ui.C("Turnover ₹Cr", "turnover_cr", "num", d=1), ui.C("CMF 20", "cmf", "num", d=2), ui.C("Breakout 10d", "bo", "text")]
    ui.show_table(cols, sub.to_dict("records"), max_height=620)
    if on_open and not sub.empty:
        o1, o2 = st.columns([3, 1])
        sym = o1.selectbox("Open stock in Arka", sub["symbol"].tolist(), key="mf_sr_open_sym")
        o2.write(""); o2.write("")
        if o2.button("OPEN", key="mf_sr_open_btn", use_container_width=True):
            on_open(str(sym))


def _render_sector_ranking(on_open=None):
    st.markdown('<div class="arka-mf-section">SECTOR RANKING · WHERE MONEY IS ROTATING ACROSS THE STOCK UNIVERSE</div>', unsafe_allow_html=True)
    meta = universe_meta()
    if meta is None:
        st.warning("Could not load the stock universe (NSE archive constituent files were unreachable). Sector Ranking needs the "
                   "Nifty Total Market / Nifty 500 constituent CSVs with their Industry column.")
        return
    c1, c2 = st.columns([1.7, 5])
    with c1:
        if st.button("LOAD / REFRESH SECTOR DATA", type="primary", use_container_width=True, key="mf_sr_run"):
            if st.session_state.get("mf_sr_ready"):
                for fn in (load_universe_frames, _sector_analysis, _yahoo_candidates):
                    fn.clear()
            st.session_state["mf_sr_ready"] = True
    with c2:
        st.markdown(f'<div class="arka-mf-note">Downloads ~2 years of daily prices for {len(meta)} stocks (Yahoo, batched; the first load can take 2-3 minutes, '
                    'then cached 15 minutes). Sector = NSE industry classification. Each sector becomes an equal-weight index, read with RSI, EMAs, '
                    'relative strength, breadth and turnover.</div>', unsafe_allow_html=True)
    if not st.session_state.get("mf_sr_ready"):
        st.info("Press LOAD / REFRESH SECTOR DATA to build the sector radar.")
        return
    with st.spinner(f"Loading price history for {len(meta)} stocks and building sector indices…"):
        try:
            res = _sector_analysis(tuple(meta["symbol"]))
        except Exception as exc:
            st.error(f"Sector data unavailable right now: {exc}. Yahoo may be rate-limiting; try again in a minute.")
            return
    benches = list(res["bench_index"].columns)
    if not benches:
        st.error("No benchmark series could be built, so relative strength cannot be computed.")
        return
    st.caption(f"As of {res['asof']} close · {res['n_stocks']} stocks in {len(res['sectors'])} sectors · "
               f"{len(res['unmapped'])} symbols had no Yahoo data · {len(res['stale'])} stale tickers skipped.")

    bench = st.radio("Benchmark for relative strength", benches, horizontal=True, key="mf_sr_bench")
    view = st.radio("View", _VIEWS, horizontal=True, key="mf_sr_view")
    try:
        rot, trails = se.rotation_table(res, bench)
    except Exception as exc:
        st.error(f"Could not compute rotation versus {bench}: {exc}")
        return
    rot["spark"] = rot["sector"].map(res["sparks"])
    rot["rsi_chg"] = rot["rsi_d"] - rot["rsi_d_5"]
    rot["rsi_zone"] = rot["rsi_d"].map(ui.rsi_zone)
    rot["trend"] = [se.trend_label(a, b, c) for a, b, c in zip(rot["ab20"], rot["ab50"], rot["ab200"])]

    if view == "ROTATION RADAR":
        _view_radar(rot, trails, res, bench)
    elif view == "SECTOR RANKING":
        _view_ranking(rot, bench)
    elif view == "TREND · RSI & EMA":
        _view_trend(rot)
    elif view == "VS BENCHMARKS":
        _view_benchmarks(res, rot)
    elif view == "TURNOVER A/D":
        _view_turn_ad(rot)
    elif view == "SECTOR TURNOVER":
        _view_turnover(rot, res)
    else:
        _view_cmf(rot, res)
    _drilldown(res, rot, on_open)
    st.caption("Evidence from price, breadth and volume for research and shortlisting, not investment advice.")


# ═════════════════════════ MAIN ═════════════════════════
def render_money_flow(on_open=None):
    """Arka Money Flow workspace."""
    _style()
    st.markdown('<div class="arka-mf-kicker">ARKA MARKET TERMINAL · MARKET INTERNALS</div><div class="arka-mf-title">MONEY FLOW</div>'
                '<div class="arka-mf-sub">Sector rotation radar, index and stock breadth, constituents, turnover pressure and disclosed FII/DII activity in one workspace.</div>',
                unsafe_allow_html=True)
    with st.spinner("Loading NSE index snapshot…"):
        indices = fetch_all_indices()

    # ---------- offline mode: NSE live API blocked / down ----------
    if indices is None or indices.empty:
        st.warning("NSE's live index feed is unreachable from this server (NSE often blocks cloud IPs). Index tables, A/D and FII/DII "
                   "need that feed and are hidden rather than faked. SECTOR RANKING and INDEX EXPLORER still work below: constituent lists come from NSE "
                   "archive CSVs and prices from Yahoo Finance.")
        if st.button("RETRY LIVE FEED", key="mf_retry_live"):
            fetch_all_indices.clear(); fetch_constituents.clear(); fetch_fii_dii.clear(); _session.clear(); st.rerun()
        t_sr, t_ex = st.tabs(["SECTOR RANKING", "INDEX EXPLORER"])
        with t_sr:
            _render_sector_ranking(on_open)
        with t_ex:
            _render_explorer(pd.DataFrame({"index": list(ARCHIVE_SLUGS)}), on_open)
        return

    # ---------- index history (1W/1M returns, RSI, EMAs, pivots) ----------
    equity = _equity_index_rows(indices)
    hcol, hdesc = st.columns([1.6, 5.0])
    with hcol:
        load_history = st.button("LOAD INDEX HISTORY", key="mf_load_history", use_container_width=True, type="primary")
    with hdesc:
        st.markdown('<div class="arka-mf-note">Loads ~1 year of daily index history from NSE (Yahoo fallback for major indices), cached for an hour. '
                    'It fills 1W/1M returns, RSI, EMA chips and distances, and pivots for the index tables. First load can take a minute.</div>', unsafe_allow_html=True)
    if load_history:
        # NIFTY 50 is the RS benchmark and is NOT in the sectoral/thematic categories: always include it (and NIFTY 500).
        names = list(dict.fromkeys(equity["index"].astype(str).tolist() + ["NIFTY 50", "NIFTY 500"]))
        bar = st.progress(0.0, text=f"Loading index history · 0 / {len(names)}")
        loaded, attempted = load_index_histories(names, progress=lambda f, t: bar.progress(min(f, 1.0), text=t))
        bar.empty()
        st.info(f"History loaded for {loaded} of {attempted} indices. Missing ones stay —.")
    indices = _apply_history_returns(indices)
    equity = _equity_index_rows(indices)

    changes = equity.r1d.dropna()
    up, down = int((changes > 0).sum()), int((changes < 0).sum())
    breadth_idx = equity.dropna(subset=["advances", "declines"])
    stamp = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%d %b %Y · %H:%M IST")
    cards = st.columns(5)
    cards[0].metric("Equity indexes", len(equity))
    cards[1].metric("Indexes up", up)
    cards[2].metric("Indexes down", down)
    cards[3].metric("Index A/D", f"{up / down:.2f}" if down else (str(up) if up else "—"))
    cards[4].metric("Indexes with A/D", len(breadth_idx))
    st.caption(f"Latest returned snapshot · {stamp} · {len(indices)} NSE index records in total (all listed in MARKET BREADTH). "
               "Index rows overlap and do not represent unique stocks.")

    sector_rk, nifty_ix, rotation, overview, breadth, explorer, flows, stocks = st.tabs(
        ["SECTOR RANKING", "NIFTY INDICES", "SECTOR ROTATION", "OVERVIEW", "MARKET BREADTH", "INDEX EXPLORER", "MONEY FLOW / FII-DII", "INDEX CONSTITUENTS"])

    with sector_rk:
        _render_sector_ranking(on_open)
    with nifty_ix:
        _render_nifty_indices(indices)

    # ---------------- OVERVIEW ----------------
    with overview:
        a, b = st.columns(2)
        movers = equity.dropna(subset=["r1d"]).sort_values("r1d", ascending=False)
        pcols = ["index", "key", "r1d", "r1w", "r1m"]
        ren = {"index": "Index", "key": "Category", "r1d": "1D %", "r1w": "1W %", "r1m": "1M %"}
        with a:
            st.markdown('<div class="arka-mf-section">TOP EQUITY INDEX PERFORMERS · 1D</div>', unsafe_allow_html=True)
            top = _fmt_ret(movers[pcols].head(10).copy(), ["r1d", "r1w", "r1m"]).rename(columns=ren)
            st.dataframe(top, hide_index=True, use_container_width=True)
        with b:
            st.markdown('<div class="arka-mf-section">WEAKEST EQUITY INDEX PERFORMERS · 1D</div>', unsafe_allow_html=True)
            weak = _fmt_ret(movers.sort_values("r1d")[pcols].head(10).copy(), ["r1d", "r1w", "r1m"]).rename(columns=ren)
            st.dataframe(weak, hide_index=True, use_container_width=True)
        st.markdown('<div class="arka-mf-section">INDEX PARTICIPATION BY CATEGORY</div>', unsafe_allow_html=True)
        cats = equity.groupby("key", dropna=False).agg(
            Indexes=("index", "count"), Up=("r1d", lambda s: int((s > 0).sum())), Down=("r1d", lambda s: int((s < 0).sum())),
            Flat=("r1d", lambda s: int((s == 0).sum())), m1d=("r1d", "median"), m1w=("r1w", "median"),
            m1m=("r1m", "median")).reset_index().rename(columns={"key": "Category"})
        _fmt_ret(cats, ["m1d", "m1w", "m1m"])
        cats = cats.rename(columns={"m1d": "Median 1D %", "m1w": "Median 1W %", "m1m": "Median 1M %"})
        st.dataframe(cats, hide_index=True, use_container_width=True)

    # ---------------- SECTOR ROTATION (NSE sectoral indices) ----------------
    with rotation:
        scope = st.radio("Universe", list(SCOPES), horizontal=True, key="mf_scope")
        sector = rank_sectors(indices, scope)
        if sector.empty:
            st.info("No matching index records or NIFTY 50 reference levels are available.")
        else:
            st.caption("Outperformance score = 20% 1D + 30% 1W + 50% 1M return versus NIFTY 50, re-weighted over the periods that have data "
                       "(press LOAD INDEX HISTORY if 1W/1M show —). Descriptive, not predictive. "
                       "LEADER = ahead of NIFTY 50 on both 1W and 1M · EMERGING = ahead on 1W only · FADING = ahead on 1M only · LAGGARD = behind on both. "
                       "For the full stock-based rotation radar with RSI, EMAs and stages, open SECTOR RANKING.")
            rows = sector.to_dict("records")
            for r in rows:
                r["lead_html"] = f'<span class="mf-stage" style="--sc:{ {"LEADER": "#30D158", "EMERGING": "#5AC8FA", "FADING": "#FF9F0A", "LAGGARD": "#FF453A"}.get(r["leadership"], "#5A5A5A") }">{ui.esc(r["leadership"])}</span>'
                r["rot_html"] = _quad_pill(r["quadrant"])
            cols = [ui.C("#", "rank", "num", d=0), ui.C("Index", "index", "sector"), ui.C("Last", "last", "num"),
                    ui.C("1D", "r1d", "pct", scale=2.0), ui.C("1W", "r1w", "pct", scale=4.0), ui.C("1M", "r1m", "pct", scale=8.0),
                    ui.C("vs N50 1W", "rs_1w", "pct", scale=3.0), ui.C("vs N50 1M", "rs_1m", "pct", scale=6.0),
                    ui.C("Score", "score", "num", d=2), ui.C("Adv", "advances", "int"), ui.C("Dec", "declines", "int"),
                    ui.C("Leadership", "lead_html", "html"), ui.C("Rotation", "rot_html", "html")]
            ui.show_table(cols, rows, max_height=700)
            emerging = sector[sector.leadership.eq("EMERGING")]["index"].tolist()
            fading = sector[sector.leadership.eq("FADING")]["index"].tolist()
            if emerging or fading:
                st.markdown(f'<div class="arka-mf-note"><b>Leadership change to investigate</b> — Emerging: {", ".join(emerging[:8]) or "none"} · '
                            f'Fading: {", ".join(fading[:8]) or "none"}. Open INDEX EXPLORER to see which stocks are driving it.</div>', unsafe_allow_html=True)
            st.markdown("**OUTPERFORMANCE SCORE**")
            chart = sector.set_index("index").score.dropna().sort_values()
            if not chart.empty: st.bar_chart(chart, horizontal=True)
            st.markdown("**ROTATION QUADRANT · SIMPLIFIED RRG-STYLE MAP**")
            _rotation_chart(sector)

    # ---------------- MARKET BREADTH ----------------
    with breadth:
        st.markdown('<div class="arka-mf-section">INDEX-LEVEL ADVANCE / DECLINE · ALL NSE INDICES</div>', unsafe_allow_html=True)
        categories = ["ALL CATEGORIES"] + sorted(indices.key.dropna().astype(str).unique().tolist())
        cat = st.selectbox("Index category", categories, key="mf_breadth_category")
        idxdata = indices if cat == "ALL CATEGORIES" else indices[indices.key.astype(str).eq(cat)]
        usable = idxdata.dropna(subset=["advances", "declines"])
        if not usable.empty:
            vals = st.columns(4)
            vals[0].metric("Indexes with A/D", len(usable))
            vals[1].metric("Advancing majority", int((usable.advances > usable.declines).sum()))
            vals[2].metric("Declining majority", int((usable.advances < usable.declines).sum()))
            med = usable.adv_pct.median()
            vals[3].metric("Median advancing %", f"{med:.1f}%" if pd.notna(med) else "—")
            plot = idxdata.dropna(subset=["adv_pct"]).sort_values("adv_pct")
            if not plot.empty: st.bar_chart(plot.set_index("index").adv_pct, horizontal=True)
        _index_table(idxdata.sort_values("adv_pct", ascending=False, na_position="last"), "mf_index_breadth_table")

        st.markdown('<div class="arka-mf-section">STOCK-LEVEL BREADTH · SELECT ONE INDEX UNIVERSE</div>', unsafe_allow_html=True)
        names = indices["index"].dropna().astype(str).tolist()
        preferred = next((i for i, n in enumerate(names) if n.upper() == "NIFTY 500"), 0)
        csel, cref = st.columns([4, 1])
        with csel: universe = st.selectbox("Constituent universe", names, index=preferred, key="mf_breadth_universe")
        with cref:
            st.write(""); st.write("")
            if st.button("REFRESH BREADTH", key="mf_breadth_refresh", use_container_width=True):
                fetch_constituents.clear(); st.rerun()
        cons = fetch_constituents(universe)
        stats, detail = stock_breadth(cons)
        if stats is None:
            st.warning("NSE returned no constituent data for this universe. Try another index or refresh later.")
        else:
            m = st.columns(5)
            m[0].metric("Stocks counted", stats["total"])
            m[1].metric("Advancing", stats["up"], f"{stats['up_pct']:.1f}%" if pd.notna(stats["up_pct"]) else None)
            m[2].metric("Declining", stats["down"])
            m[3].metric("Stock A/D ratio", _num(stats["ratio"]))
            m[4].metric("Net advances", f"{stats['net']:+d}")
            z = st.columns(4)
            z[0].metric("Unchanged", stats["flat"])
            z[1].metric("Median stock move", _pct(stats["median"]))
            z[2].metric("Within 1% of 52W high", "—" if stats["near_high"] is None else stats["near_high"])
            z[3].metric("Within 1% of 52W low", "—" if stats["near_low"] is None else stats["near_low"])
            st.bar_chart(pd.DataFrame({"Stocks": [stats["up"], stats["down"], stats["flat"]]}, index=["Advancing", "Declining", "Unchanged"]))
            l, r = st.columns(2)
            fields = [c for c in ["symbol", "lastPrice", "pChange", "totalTradedVolume", "nearWKH", "nearWKL"] if c in detail]
            rename = {"symbol": "Symbol", "lastPrice": "Price", "pChange": "1D %", "totalTradedVolume": "Volume",
                      "nearWKH": "% from 52W High", "nearWKL": "% from 52W Low"}
            with l:
                st.markdown("**TOP ADVANCERS**")
                st.dataframe(detail.sort_values("pChange", ascending=False)[fields].head(10).rename(columns=rename), hide_index=True, use_container_width=True)
            with r:
                st.markdown("**TOP DECLINERS**")
                st.dataframe(detail.sort_values("pChange")[fields].head(10).rename(columns=rename), hide_index=True, use_container_width=True)
            search, direction = st.columns([2, 1])
            query = search.text_input("Search constituents", key="mf_breadth_search").strip().upper()
            move = direction.selectbox("Filter", ["All", "Advancers", "Decliners", "Unchanged"], key="mf_breadth_filter")
            filtered = detail.copy()
            if query: filtered = filtered[filtered.symbol.astype(str).str.contains(query, na=False)]
            if move == "Advancers": filtered = filtered[filtered.pChange > 0]
            elif move == "Decliners": filtered = filtered[filtered.pChange < 0]
            elif move == "Unchanged": filtered = filtered[filtered.pChange == 0]
            fields = [c for c in ["symbol", "lastPrice", "pChange", "totalTradedVolume", "value_raw", "yearHigh", "yearLow", "nearWKH", "nearWKL"] if c in filtered]
            rename = {"symbol": "Symbol", "lastPrice": "Price", "pChange": "1D %", "totalTradedVolume": "Volume",
                      "value_raw": "Traded Value (NSE units)", "yearHigh": "52W High", "yearLow": "52W Low",
                      "nearWKH": "% from 52W High", "nearWKL": "% from 52W Low"}
            st.caption(f"Showing {len(filtered)} of {len(detail)} returned constituents. Live snapshot, not a time series — use INDEX EXPLORER for 120-day history.")
            table = filtered[fields].rename(columns=rename).reset_index(drop=True)
            event = st.dataframe(table, hide_index=True, use_container_width=True,
                                 on_select="rerun" if on_open else "ignore",
                                 selection_mode="single-row" if on_open else "multi-row", key="mf_breadth_stock_table")
            selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
            if selected and on_open: on_open(str(table.iloc[int(selected[0])]["Symbol"]))

    # ---------------- INDEX EXPLORER ----------------
    with explorer:
        _render_explorer(indices, on_open)

    # ---------------- MONEY FLOW / FII-DII ----------------
    with flows:
        st.markdown('<div class="arka-mf-section">DISCLOSED MARKET-WIDE FII / DII CASH ACTIVITY</div>', unsafe_allow_html=True)
        fii = fetch_fii_dii()
        if fii is None or fii.empty:
            st.info("FII/DII feed is unavailable. No values are substituted.")
        else:
            metrics = st.columns(min(max(len(fii), 1), 4))
            for col, (_, row) in zip(metrics, fii.head(4).iterrows()):
                category = str(row.get("category", "Investor")).strip() or "Investor"
                dt = str(row.get("date", "")).strip()
                col.metric(f"{category} · {dt}" if dt else category, _num(row.get("netValue")),
                           help=f"Buy: {_num(row.get('buyValue'))} · Sell: {_num(row.get('sellValue'))}")
            st.dataframe(fii, hide_index=True, use_container_width=True)
        st.markdown('<div class="arka-mf-section">SECTOR TURNOVER PRESSURE PROXY</div>', unsafe_allow_html=True)
        st.markdown('<div class="arka-mf-note">Proxy = traded value of stocks closing up minus value of stocks closing down. It is not actual institutional flow, '
                    'not net order flow and not proof of accumulation. Turnover units are shown as returned by NSE.</div>', unsafe_allow_html=True)
        fscope = st.radio("Flow universe", list(SCOPES), horizontal=True, key="mf_flow_scope")
        sectors = rank_sectors(indices, fscope)
        if sectors.empty:
            st.info("No matching index rows are available for this scope.")
        else:
            available = sectors["index"].astype(str).tolist()
            chosen = st.multiselect("Indexes to calculate (select a small group to limit requests)", available,
                                    default=available[:min(8, len(available))], key="mf_flow_indexes")
            if st.button("COMPUTE / REFRESH FLOW PROXY", type="primary", key="mf_flow_compute", disabled=not chosen):
                result = {}
                progress = st.progress(0, text="Fetching constituent snapshots…")
                for i, nm in enumerate(chosen):
                    result[nm] = flow_stats(fetch_constituents(nm))
                    progress.progress((i + 1) / len(chosen), text=f"Processed {i + 1} of {len(chosen)} indexes")
                progress.empty(); st.session_state["mf_flow_result"] = result
            result = st.session_state.get("mf_flow_result", {})
            rows = [{"Index": nm, "Up Value": x["up_value"], "Down Value": x["down_value"], "Net Value": x["net_value"],
                     "Net %": x["net_pct"], "Adv": x["up"], "Dec": x["down"], "Stocks": x["stocks"]} for nm, x in result.items() if x]
            if rows:
                out = pd.DataFrame(rows).sort_values("Net %", ascending=False)
                st.dataframe(out, hide_index=True, use_container_width=True)
                st.bar_chart(out.set_index("Index")["Net %"], horizontal=True)
            elif result:
                st.warning("No flow proxy values returned for the selected indexes. Refresh the snapshot or try fewer indexes.")

    # ---------------- INDEX CONSTITUENTS ----------------
    with stocks:
        index_names = indices["index"].dropna().astype(str).tolist()
        preferred = next((i for i, n in enumerate(index_names) if n.upper() == "NIFTY 50"), 0)
        a, b, c = st.columns([2, 2, 1])
        with a: index_name = st.selectbox("Index / constituent universe", index_names, index=preferred, key="mf_const_index")
        with b: query = st.text_input("Find stock", placeholder="e.g. RELIANCE", key="mf_const_search").strip().upper()
        with c: sort = st.selectbox("Sort", ["1D change", "Traded value", "Symbol"], key="mf_const_sort")
        cons = fetch_constituents(index_name)
        if cons is None or cons.empty:
            arch = archive_constituents(index_name)
            if arch is None:
                st.warning("NSE returned no constituents for this index.")
            else:
                st.info("NSE live prices unavailable; showing the constituent list from NSE archive (no prices).")
                st.dataframe(arch.rename(columns={"symbol": "Symbol", "industry": "Industry"}), hide_index=True, use_container_width=True)
        else:
            view = cons.copy()
            if query: view = view[view.symbol.str.contains(query, na=False)]
            if sort == "1D change": view = view.sort_values("pChange", ascending=False, na_position="last")
            elif sort == "Traded value": view = view.sort_values("totalTradedValue", ascending=False, na_position="last")
            else: view = view.sort_values("symbol")
            fields = [c for c in ["symbol", "lastPrice", "pChange", "totalTradedVolume", "value_raw", "ffmc", "yearHigh", "yearLow", "nearWKH", "nearWKL"] if c in view]
            table = view[fields].rename(columns={"symbol": "Symbol", "lastPrice": "Price", "pChange": "1D %", "totalTradedVolume": "Volume",
                                                 "value_raw": "Traded Value (NSE units)", "ffmc": "FF Mcap", "yearHigh": "52W High",
                                                 "yearLow": "52W Low", "nearWKH": "% from 52W High", "nearWKL": "% from 52W Low"}).reset_index(drop=True)
            st.caption(f"{len(table)} rows returned for {index_name}; select a row to open Arka stock research.")
            event = st.dataframe(table, hide_index=True, use_container_width=True,
                                 on_select="rerun" if on_open else "ignore",
                                 selection_mode="single-row" if on_open else "multi-row", key="mf_constituents_table")
            selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
            if selected and on_open: on_open(str(table.iloc[int(selected[0])]["Symbol"]))

    st.caption("Data: NSE India public index, constituent and FII/DII endpoints where accessible; NSE archive CSVs; Yahoo Finance for constituent price history. "
               "Snapshot data, not a licensed real-time feed.")
    with st.expander("DATA SOURCES & METRIC DEFINITIONS"):
        st.markdown("""
        **Index snapshot, index-level advance/decline (all indices):** NSE `/api/allIndices` ([Live Market Indices](https://www.nseindia.com/market-data/live-market-indices)).

        **Constituents, prices, free-float weights, turnover:** NSE `/api/equity-stockIndices?index=...`; fallback constituent lists from NSE archive CSVs (`nsearchives.nseindia.com/content/indices/`).

        **1W / 1M index returns:** NSE index history endpoint, Yahoo Finance fallback for major indices.

        **Sector ranking:** about 2 years of daily Yahoo prices for the stock universe; each NSE industry becomes an equal-weight, daily-rebalanced index. RSI, EMAs and rotation are computed on that index.
        Benchmarks: NIFTY 50 (^NSEI); MIDCAP 100 and SMALLCAP 100 from Yahoo when the ticker matches the index's constituents, otherwise an equal-weight proxy.

        **Constituent history (INDEX EXPLORER):** one batched Yahoo Finance daily download per index (≈1 year), cached 10 minutes.

        **FII / DII:** [NSE FII/FPI & DII reports](https://www.nseindia.com/reports/fii-dii) — market-wide, not by sector.

        **Interpretation:** relative strength compares returns with a benchmark. Rotation quadrants are simplified, not a licensed JdK RRG.
        The explorer's index proxy is weighted by free-float market cap (equal-weight when weights are unavailable) and approximates the official index.
        Pressure and rotation scores are price/volume/breadth evidence and do not reveal buyer identity. Index families overlap.
        """)
