"""Arka Money Flow (v3): sector rotation, index breadth, Index Explorer, constituents, FII/DII.

What this module does
---------------------
* SECTOR ROTATION  - every sectoral/thematic/broad index ranked by relative strength vs NIFTY 50,
                     LEADER / EMERGING / FADING / LAGGARD labels and a rotation quadrant map.
* MARKET BREADTH   - advance/decline for EVERY NSE index (from NSE's own allIndices feed) plus
                     stock-level breadth for one chosen index universe.
* INDEX EXPLORER   - pick any index; constituents' price history is downloaded in one batch and
                     breadth (% above 20/50/200 DMA), contribution, relative strength, relative
                     volume and transparent buying/selling PRESSURE scores are computed.
* MONEY FLOW       - disclosed FII/DII cash flows + a price-signed turnover proxy per index.
* CONSTITUENTS     - stocks of any index; click a row to open it in Arka.

Data sources: NSE public endpoints (allIndices, equity-stockIndices, fiidiiTradeReact,
historical index closes), NSE archive constituent CSVs (fallback), Yahoo Finance (prices).
Missing data stays missing - nothing is invented. Pressure / turnover signals are price+volume
evidence, NOT institutional order flow.

v3 fixes
--------
* No dependency on data_engine (flow_engine is self-contained).
* NSE's allIndices often omits week/month-ago levels -> 1W/1M returns can be loaded from NSE
  history with a Yahoo fallback; scores are re-weighted over whatever periods exist.
* Offline mode: if NSE's live API blocks the host (common on cloud IPs) the Index Explorer still
  works using NSE archive constituent CSVs + Yahoo prices.
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
    """~6 months of daily OHLCV for the whole stock universe + NIFTY 50 (batched Yahoo download)."""
    frames = fe._download_frames([f"{s}.NS" for s in symbols] + ["^NSEI"], "6mo")
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


# ═════════════════════════ NIFTY INDICES (44 sectoral / thematic) ═════════════════════════
def _scope_rows(indices, scope):
    keys = SCOPES.get(scope, ())
    cats = indices["key"].astype(str).str.upper().str.replace("_", " ", regex=False)
    sub = indices[cats.apply(lambda x: any(k in x for k in keys))] if keys else indices
    return _equity_index_rows(sub)


def _render_nifty_indices(indices):
    st.markdown('<div class="arka-mf-section">NIFTY INDICES · PRICE, WEEKLY RSI, EMAs, PIVOTS AND RS RANK HISTORY</div>', unsafe_allow_html=True)
    scope = st.radio("Universe", ["SECTORAL + THEMATIC", "SECTORAL", "THEMATIC", "BROAD MARKET"], horizontal=True, key="mf_ni_scope")
    mode = st.radio("Change columns", ["Daily", "Weekly", "Both"], horizontal=True, key="mf_ni_mode", index=2)
    sub = _scope_rows(indices, scope)
    names = sub["index"].astype(str).tolist()
    st.caption(f"{len(names)} NSE {scope.lower()} indices in the live feed. History-based columns need LOAD INDEX HISTORY (top of page).")
    hists = index_histories(names + ["NIFTY 50"])
    have = [n for n in names if n in hists]
    live = sub.set_index("index")

    if "NIFTY 50" not in hists or not have:
        st.info("History not loaded yet — showing the live snapshot only. Press LOAD INDEX HISTORY above to add weekly RSI, EMAs, pivots and RS rank history.")
        v = sub[["index", "last", "r1d", "advances", "declines"]].rename(
            columns={"index": "Index", "last": "Price", "r1d": "1D %", "advances": "Adv", "declines": "Dec"})
        st.dataframe(v.sort_values("1D %", ascending=False), hide_index=True, use_container_width=True)
        return
    try:
        res = ix.analyse_histories({n: hists[n] for n in have + ["NIFTY 50"]}, "NIFTY 50")
    except Exception as exc:
        st.error(f"Could not analyse index history: {exc}")
        return
    res = res[res["index"].isin(names)].copy()
    res["price"] = res["index"].map(live["last"]).fillna(res["price"])
    res["r1d"] = res["index"].map(live["percentChange"]).fillna(res["r1d"])
    res = res.sort_values("r1d", ascending=False).reset_index(drop=True)
    res.insert(0, "#", range(1, len(res) + 1))
    cols = {"Daily": ["#", "index", "price", "r1d"], "Weekly": ["#", "index", "price", "r1w"],
            "Both": ["#", "index", "price", "r1d", "r1w"]}[mode]
    cols += ["w_rsi", "ema_chips", "pct_w20", "d_pivot", "w_pivot", "rs_t30", "rs_t15", "rs_t5", "rs_t0"]
    ren = {"index": "Index", "price": "Price", "r1d": "1D %", "r1w": "1W %", "w_rsi": "W-RSI", "ema_chips": "W-EMA 10 · 20 · 50 · D200",
           "pct_w20": "% from W-20EMA", "d_pivot": "D-Pivot", "w_pivot": "W-Pivot",
           "rs_t30": "RS T-30", "rs_t15": "RS T-15", "rs_t5": "RS T-5", "rs_t0": "RS T0"}
    view = res[cols].rename(columns=ren)
    cfg = {"1D %": st.column_config.NumberColumn("1D %", format="%+.2f%%"),
           "1W %": st.column_config.NumberColumn("1W %", format="%+.2f%%"),
           "Price": st.column_config.NumberColumn("Price", format="%.2f"),
           "W-RSI": st.column_config.NumberColumn("W-RSI", format="%.1f"),
           "% from W-20EMA": st.column_config.NumberColumn("% from W-20EMA", format="%+.1f%%")}
    for c in ("RS T-30", "RS T-15", "RS T-5", "RS T0"):
        cfg[c] = st.column_config.ProgressColumn(c, min_value=0, max_value=99, format="%d")
    st.dataframe(view, hide_index=True, use_container_width=True, column_config=cfg, key="mf_ni_table")
    miss = [n for n in names if n not in hists]
    asof = res["asof"].max() if "asof" in res else ""
    st.markdown(f'<div class="arka-mf-note">History as of {asof} close · {len(have)} of {len(names)} indices have history'
                f'{(" (missing: " + ", ".join(miss[:6]) + ("…" if len(miss) > 6 else "") + ")") if miss else ""}. '
                'RS T-n = percentile (0-99) of the index\'s 20-session performance vs NIFTY 50 among the indices shown, n sessions ago. '
                'EMA dots: 🟢 price above / 🔴 below the weekly 10/20/50 EMA and the 200-DAY EMA (weekly 200 needs ~4 years of data). '
                'Pivots: classic levels from the previous completed day / week; the label says where the latest close sits.</div>', unsafe_allow_html=True)


# ═════════════════════════ SECTOR RANKING (stock universe) ═════════════════════════
def _sector_cfg():
    c = {"Stocks": st.column_config.NumberColumn("Stocks", format="%d"),
         "Turnover (Cr)": st.column_config.NumberColumn("Turnover (Cr)", format="₹%.0f"),
         "Share %": st.column_config.NumberColumn("Share %", format="%.2f"),
         "A/D Net": st.column_config.NumberColumn("A/D Net", format="%+.2f"),
         "Avg RSI": st.column_config.NumberColumn("Avg RSI", format="%.0f"),
         "% >20EMA": st.column_config.NumberColumn("% >20EMA", format="%.0f%%"),
         "% BO 10d": st.column_config.NumberColumn("% BO 10d", format="%.0f%%")}
    for k in ("% Chg", "Δ Share (pt)", "CMF 20", "CMF 5d ago", "Δ CMF"):
        c[k] = st.column_config.NumberColumn(k, format="%+.2f")
    for k in ("RS T-30", "RS T-15", "RS T-5", "RS T0"):
        c[k] = st.column_config.ProgressColumn(k, min_value=0, max_value=99, format="%d")
    return c


def _render_sector_ranking(on_open=None):
    st.markdown('<div class="arka-mf-section">SECTOR RANKING · WHERE TURNOVER, STRENGTH AND MONEY FLOW SIT ACROSS THE STOCK UNIVERSE</div>', unsafe_allow_html=True)
    meta = universe_meta()
    if meta is None:
        st.warning("Could not load the stock universe (NSE archive constituent files were unreachable). Sector Ranking needs the "
                   "Nifty Total Market / Nifty 500 constituent CSVs with their Industry column.")
        return
    c1, c2 = st.columns([1.7, 5])
    with c1:
        if st.button("LOAD / REFRESH SECTOR DATA", type="primary", use_container_width=True, key="mf_sr_run"):
            st.session_state["mf_sr_ready"] = True
    with c2:
        st.markdown(f'<div class="arka-mf-note">Downloads ~6 months of daily prices for {len(meta)} stocks (Yahoo, batched; first load can take 1-2 minutes, '
                    'then cached 15 minutes). Sector = NSE industry classification. Turnover = close × volume.</div>', unsafe_allow_html=True)
    if not st.session_state.get("mf_sr_ready"):
        st.info("Press LOAD / REFRESH SECTOR DATA to build the sector tables.")
        return
    with st.spinner(f"Loading price history for {len(meta)} stocks…"):
        try:
            frames = load_universe_frames(tuple(meta["symbol"]))
        except Exception as exc:
            st.error(f"Price history unavailable right now: {exc}. Yahoo may be rate-limiting; try again in a minute.")
            return
    try:
        res = se.analyse_universe(frames, meta, "^NSEI")
    except Exception as exc:
        st.error(f"Could not analyse the universe: {exc}")
        return
    sec, stk = res["sectors"], res["stocks"]
    st.caption(f"As of {res['asof']} close · {res['n_stocks']} stocks in {len(sec)} sectors · "
               f"{len(res['unmapped'])} symbols had no Yahoo data · {len(res['stale'])} stale tickers skipped.")

    view = st.radio("View", ["Sector ranking", "Turnover A/D", "Sector turnover", "Chaikin money flow", "Relative strength"],
                    horizontal=True, key="mf_sr_view")
    per = st.radio("Period", ["Daily", "Weekly"], horizontal=True, key="mf_sr_period") if view in ("Sector ranking", "Sector turnover") else "Daily"
    chg, dsh = ("r1d", "dshare_d") if per == "Daily" else ("r1w", "dshare_w")
    cfg = _sector_cfg()

    if view == "Sector ranking":
        t = sec[["sector", "stocks", "turnover_cr", chg, "share", dsh, "ad_net", "avg_rsi", "pct_above20", "pct_bo10", "leaders"]].rename(columns={
            "sector": "Sector", "stocks": "Stocks", "turnover_cr": "Turnover (Cr)", chg: "% Chg", "share": "Share %", dsh: "Δ Share (pt)",
            "ad_net": "A/D Net", "avg_rsi": "Avg RSI", "pct_above20": "% >20EMA", "pct_bo10": "% BO 10d", "leaders": "Leaders (1W, liquid names)"})
        st.dataframe(t, hide_index=True, use_container_width=True, column_config=cfg)
        st.markdown('<div class="arka-mf-note">% Chg = average of the sector\'s stocks. Share % = sector turnover ÷ universe turnover; Δ Share = change in percentage points '
                    '(vs yesterday / 5 sessions ago). A/D Net = (advancers − decliners) ÷ stocks. % BO 10d = share of stocks that closed above their prior 20-day high in the last 10 sessions.</div>', unsafe_allow_html=True)
    elif view == "Turnover A/D":
        t = sec[["sector", "stocks", "adv", "dec", "up_turn_cr", "dn_turn_cr", "turn_ad", "pct_turn_adv"]].rename(columns={
            "sector": "Sector", "stocks": "Stocks", "adv": "Adv", "dec": "Dec", "up_turn_cr": "Up Turnover (Cr)", "dn_turn_cr": "Down Turnover (Cr)",
            "turn_ad": "Turnover A/D", "pct_turn_adv": "% Turnover in Advancers"}).sort_values("% Turnover in Advancers", ascending=False)
        st.dataframe(t, hide_index=True, use_container_width=True, column_config={
            "Up Turnover (Cr)": st.column_config.NumberColumn(format="₹%.0f"), "Down Turnover (Cr)": st.column_config.NumberColumn(format="₹%.0f"),
            "Turnover A/D": st.column_config.NumberColumn(format="%.2f"),
            "% Turnover in Advancers": st.column_config.ProgressColumn("% Turnover in Advancers", min_value=0, max_value=100, format="%.0f%%")})
        st.bar_chart(t.set_index("Sector")["% Turnover in Advancers"], horizontal=True)
        st.markdown('<div class="arka-mf-note">Turnover A/D = turnover of stocks closing up ÷ turnover of stocks closing down. Above 1 means more money changed hands in rising stocks. '
                    'Every trade has two sides, so this shows where activity is concentrated, not who is buying.</div>', unsafe_allow_html=True)
    elif view == "Sector turnover":
        t = sec[["sector", "turnover_cr", "turn_avg20_cr", "turn_vs20", "share", dsh]].rename(columns={
            "sector": "Sector", "turnover_cr": "Turnover (Cr)", "turn_avg20_cr": "20d Avg (Cr)", "turn_vs20": "Today ÷ 20d Avg",
            "share": "Share %", dsh: "Δ Share (pt)"}).sort_values("Turnover (Cr)", ascending=False)
        st.dataframe(t, hide_index=True, use_container_width=True, column_config={
            "20d Avg (Cr)": st.column_config.NumberColumn(format="₹%.0f"), "Today ÷ 20d Avg": st.column_config.NumberColumn(format="%.2f×"), **cfg})
        top = t["Sector"].head(6).tolist()
        ss = res["share_series"][top].rename_axis("date")
        st.markdown("**TURNOVER SHARE % · LAST 60 SESSIONS · TOP 6 SECTORS**")
        st.line_chart(ss)
    elif view == "Chaikin money flow":
        sec = sec.copy()
        sec["d_cmf"] = sec["cmf"] - sec["cmf_5d"]
        sec["signal"] = np.where(sec["cmf"] > .05, "ACCUMULATION", np.where(sec["cmf"] < -.05, "DISTRIBUTION", "NEUTRAL"))
        t = sec[["sector", "stocks", "cmf", "cmf_5d", "d_cmf", "signal"]].rename(columns={
            "sector": "Sector", "stocks": "Stocks", "cmf": "CMF 20", "cmf_5d": "CMF 5d ago", "d_cmf": "Δ CMF", "signal": "Signal"}).sort_values("CMF 20", ascending=False)
        st.dataframe(t, hide_index=True, use_container_width=True, column_config=cfg)
        st.bar_chart(t.set_index("Sector")["CMF 20"], horizontal=True)
        pick = st.multiselect("Plot CMF history", t["Sector"].tolist(), default=t["Sector"].head(4).tolist(), key="mf_sr_cmf_pick")
        if pick:
            st.line_chart(res["cmf_series"][pick].rename_axis("date"))
        st.markdown('<div class="arka-mf-note">Chaikin Money Flow (20) = Σ money-flow-volume ÷ Σ volume over 20 sessions, aggregated across the sector\'s stocks. '
                    'Positive = closes tend to land in the upper part of the day\'s range on volume (accumulation-like); negative = lower part (distribution-like). '
                    'Signal band ±0.05 is a convention, not a forecast.</div>', unsafe_allow_html=True)
    else:
        sec = sec.copy()
        sec["trend"] = sec["rs_t0"] - sec["rs_t15"]
        t = sec[["sector", "stocks", "rs_1w", "rs_1m", "rs_t30", "rs_t15", "rs_t5", "rs_t0", "trend"]].rename(columns={
            "sector": "Sector", "stocks": "Stocks", "rs_1w": "RS 1W (pp)", "rs_1m": "RS 1M (pp)", "rs_t30": "RS T-30", "rs_t15": "RS T-15",
            "rs_t5": "RS T-5", "rs_t0": "RS T0", "trend": "Δ Rank (T0 − T-15)"}).sort_values("RS T0", ascending=False)
        st.dataframe(t, hide_index=True, use_container_width=True, column_config={
            **cfg, "RS 1W (pp)": st.column_config.NumberColumn(format="%+.2f"), "RS 1M (pp)": st.column_config.NumberColumn(format="%+.2f"),
            "Δ Rank (T0 − T-15)": st.column_config.NumberColumn(format="%+d")})
        st.markdown('<div class="arka-mf-note">RS = the sector\'s 20-session performance (equal-weight index of its stocks) relative to NIFTY 50; the rank is the percentile (0-99) among sectors '
                    'at T-30, T-15, T-5 sessions ago and today. RS 1W / 1M = excess return vs NIFTY 50 in percentage points.</div>', unsafe_allow_html=True)

    # ---- drill-down: stocks of one sector ----
    st.markdown('<div class="arka-mf-section">STOCKS IN A SECTOR · SELECT A ROW TO OPEN THE STOCK</div>', unsafe_allow_html=True)
    pick = st.selectbox("Sector", sec["sector"].tolist(), key="mf_sr_drill")
    st_t = stk[stk["sector"] == pick].sort_values("turnover_cr", ascending=False)
    tv = st_t[["symbol", "price", "turnover_cr", "r1d", "r1w", "rsi", "above20", "bo10", "cmf"]].rename(columns={
        "symbol": "Symbol", "price": "Price", "turnover_cr": "Turnover (Cr)", "r1d": "1D %", "r1w": "1W %", "rsi": "RSI 14",
        "above20": ">20EMA", "bo10": "Breakout 10d", "cmf": "CMF 20"}).reset_index(drop=True)
    ev = st.dataframe(tv, hide_index=True, use_container_width=True, key="mf_sr_stocks",
                      on_select="rerun" if on_open else "ignore", selection_mode="single-row" if on_open else "multi-row",
                      column_config={"Price": st.column_config.NumberColumn(format="%.2f"), "Turnover (Cr)": st.column_config.NumberColumn(format="₹%.1f"),
                                     "1D %": st.column_config.NumberColumn(format="%+.2f%%"), "1W %": st.column_config.NumberColumn(format="%+.2f%%"),
                                     "RSI 14": st.column_config.NumberColumn(format="%.0f"), "CMF 20": st.column_config.NumberColumn(format="%+.2f")})
    rows = getattr(getattr(ev, "selection", None), "rows", []) if ev is not None else []
    if rows and on_open:
        on_open(str(tv.iloc[int(rows[0])]["Symbol"]))


# ═════════════════════════ MAIN ═════════════════════════
def render_money_flow(on_open=None):
    """Arka Money Flow workspace."""
    _style()
    st.markdown('<div class="arka-mf-kicker">ARKA MARKET TERMINAL · MARKET INTERNALS</div><div class="arka-mf-title">MONEY FLOW</div>'
                '<div class="arka-mf-sub">Sector rotation, index and stock breadth, constituents, turnover pressure and disclosed FII/DII activity in one workspace.</div>',
                unsafe_allow_html=True)
    with st.spinner("Loading NSE index snapshot…"):
        indices = fetch_all_indices()

    # ---------- offline mode: NSE live API blocked / down ----------
    if indices is None or indices.empty:
        st.warning("NSE's live index feed is unreachable from this server (NSE often blocks cloud IPs). Sector ranking, A/D and FII/DII "
                   "need that feed and are hidden rather than faked. INDEX EXPLORER still works below: constituent lists come from NSE "
                   "archive CSVs and prices from Yahoo Finance.")
        if st.button("RETRY LIVE FEED", key="mf_retry_live"):
            fetch_all_indices.clear(); fetch_constituents.clear(); fetch_fii_dii.clear(); _session.clear(); st.rerun()
        _render_explorer(pd.DataFrame({"index": list(ARCHIVE_SLUGS)}), on_open)
        return

    # ---------- index history (1W/1M returns, RS, RSI, EMAs, pivots) ----------
    equity = _equity_index_rows(indices)
    hcol, hdesc = st.columns([1.6, 5.0])
    with hcol:
        load_history = st.button("LOAD INDEX HISTORY", key="mf_load_history", use_container_width=True, type="primary")
    with hdesc:
        st.markdown('<div class="arka-mf-note">Loads ~1 year of daily index history from NSE (Yahoo fallback for major indices), cached for an hour. '
                    'It fills 1W/1M returns, RS vs NIFTY 50, weekly RSI/EMAs, pivots and RS rank history. First load can take a minute.</div>', unsafe_allow_html=True)
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

    overview, nifty_ix, sector_rk, rotation, breadth, explorer, flows, stocks = st.tabs(
        ["OVERVIEW", "NIFTY INDICES", "SECTOR RANKING", "SECTOR ROTATION", "MARKET BREADTH", "INDEX EXPLORER", "MONEY FLOW / FII-DII", "INDEX CONSTITUENTS"])

    with nifty_ix:
        _render_nifty_indices(indices)
    with sector_rk:
        _render_sector_ranking(on_open)

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

    # ---------------- SECTOR ROTATION ----------------
    with rotation:
        scope = st.radio("Universe", list(SCOPES), horizontal=True, key="mf_scope")
        sector = rank_sectors(indices, scope)
        if sector.empty:
            st.info("No matching index records or NIFTY 50 reference levels are available.")
        else:
            st.caption("Relative-strength score = 20% 1D + 30% 1W + 50% 1M versus NIFTY 50, re-weighted over the periods that have data "
                       "(press LOAD INDEX HISTORY if 1W/1M show —). Descriptive, not predictive. "
                       "LEADER = ahead of NIFTY 50 on both 1W and 1M · EMERGING = ahead on 1W only · FADING = ahead on 1M only · LAGGARD = behind on both.")
            cols = ["rank", "index", "key", "last", "r1d", "r1w", "r1m", "rs_1w", "rs_1m", "score", "advances", "declines", "leadership", "quadrant"]
            view = sector[cols].rename(columns={"rank": "Rank", "index": "Index", "key": "Category", "last": "Last", "r1d": "1D %",
                                                "r1w": "1W %", "r1m": "1M %", "rs_1w": "RS 1W", "rs_1m": "RS 1M", "score": "RS Score",
                                                "advances": "Adv", "declines": "Dec", "leadership": "Leadership", "quadrant": "Rotation"}).copy()
            _fmt_ret(view, ["1D %", "1W %", "1M %"])
            for col in ("Last", "RS 1W", "RS 1M", "RS Score"):
                view[col] = view[col].map(lambda v: f"{float(v):,.2f}" if pd.notna(v) else "—")
            for col in ("Adv", "Dec"):
                view[col] = view[col].map(lambda v: f"{int(v)}" if pd.notna(v) else "—")
            view["Rank"] = view["Rank"].map(lambda v: f"{int(v)}" if pd.notna(v) else "—")
            st.dataframe(view, hide_index=True, use_container_width=True)
            emerging = sector[sector.leadership.eq("EMERGING")]["index"].tolist()
            fading = sector[sector.leadership.eq("FADING")]["index"].tolist()
            if emerging or fading:
                st.markdown(f'<div class="arka-mf-note"><b>Leadership change to investigate</b> — Emerging: {", ".join(emerging[:8]) or "none"} · '
                            f'Fading: {", ".join(fading[:8]) or "none"}. Open INDEX EXPLORER to see which stocks are driving it.</div>', unsafe_allow_html=True)
            st.markdown("**RELATIVE STRENGTH SCORE**")
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

        **Constituent history (INDEX EXPLORER):** one batched Yahoo Finance daily download per index (≈1 year), cached 10 minutes.

        **FII / DII:** [NSE FII/FPI & DII reports](https://www.nseindia.com/reports/fii-dii) — market-wide, not by sector.

        **Interpretation:** relative strength compares returns with NIFTY 50 (or NIFTY 500 in the explorer). Rotation quadrants are simplified, not a licensed JdK RRG.
        The explorer's index proxy is weighted by free-float market cap (equal-weight when weights are unavailable) and approximates the official index.
        Pressure scores are price/volume evidence and do not reveal buyer identity. Index families overlap.
        """)
