"""ScanX data adapter for Arka Trades — v2.

Uses only PUBLIC pages and the same public web-service calls the ScanX site makes
in a normal browser. No login, no token, no access-control bypass.

Known browser-observed endpoints:
  POST https://ow-static-scanx.dhan.co/staticscanx/mftransaction
  POST https://openweb-ticks.dhan.co/getDataH

What's new vs v1
----------------
* ISIN auto-discovery: the ISIN is read from the company page and validated with
  the ISIN check digit, so MF holdings work for ANY stock — no hard-coded ISIN map.
  (Env override ARKA_SCANX_ISIN_<SYMBOL> and the built-in map still win.)
* Shared requests.Session with retry/backoff and connection pooling.
* Page and MF endpoint are fetched in parallel when the ISIN is already known.
* Remembers which URL slug worked per symbol, so repeat loads skip the guessing.
* Slug guessing is smarter ("&" -> "and", "Limited" -> "ltd", etc.) and capped.
* About text, technical-indicator table and analyst table are now actually parsed
  (v1 returned empty 'about' and 'technical').
* MF endpoint is paged (up to `max_pages`) instead of only page 1.
* Price-history response can be turned into a DataFrame (history_to_df).
* Result always has the same shape, plus 'errors' and 'fetched_at' for debugging.

Backwards compatible: fetch_scanx_company / fetch_mf_transactions / fetch_price_history /
company_url keep their names, signatures and result keys.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCANX_BASE = "https://scanx.trade/company/"
SCANX_HOST = "https://scanx.trade"
MF_TX_URL = "https://ow-static-scanx.dhan.co/staticscanx/mftransaction"
PRICE_URL = "https://openweb-ticks.dhan.co/getDataH"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_MAX_SLUG_TRIES = 5
_SLUG_CACHE: dict[str, str] = {}      # symbol -> company URL that worked
_ISIN_CACHE: dict[str, str] = {}      # symbol -> validated ISIN

_SYMBOL_ALIASES = {
    "RELIANCE": "reliance-industries-ltd", "HDFCBANK": "hdfc-bank-ltd", "ICICIBANK": "icici-bank-ltd",
    "AXISBANK": "axis-bank-ltd", "SBIN": "state-bank-of-india-ltd", "INFY": "infosys-ltd",
    "TCS": "tcs-tata-consultancy-services-ltd", "ITC": "itc-ltd", "LT": "larsen-toubro-ltd",
    "BHARTIARTL": "bharti-airtel-ltd", "KOTAKBANK": "kotak-mahindra-bank-ltd", "MARUTI": "maruti-suzuki-india-ltd",
    "TATAMOTORS": "tata-motors-ltd", "TATASTEEL": "tata-steel-ltd", "SUNPHARMA": "sun-pharmaceutical-industries-ltd",
    "HINDUNILVR": "hindustan-unilever-ltd", "BAJFINANCE": "bajaj-finance-ltd", "ADANIENT": "adani-enterprises-ltd",
    "ADANIPORTS": "adani-ports-and-special-economic-zone-ltd", "WIPRO": "wipro-ltd", "TECHM": "tech-mahindra-ltd",
    "HCLTECH": "hcl-technologies-ltd", "FCL": "fineotex-chemical-ltd",
}
_ISIN = {
    # Browser-observed from ScanX MF request for Reliance. Others are auto-discovered.
    "RELIANCE": "INE002A01018",
}


# ═════════════════════════ HTTP session ═════════════════════════
def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "POST"}), raise_on_status=False)
    ad = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=16)
    s.mount("https://", ad)
    s.mount("http://", ad)
    s.headers.update({"User-Agent": _UA})
    return s


_SESSION = _make_session()


def _headers(referer: str = "https://scanx.trade/") -> dict:
    return {"User-Agent": _UA, "Accept": "application/json,text/plain,*/*", "Content-Type": "application/json",
            "Origin": "https://scanx.trade", "Referer": referer, "Cache-Control": "no-cache"}


def _post_json(url, payload, timeout=15):
    try:
        r = _SESSION.post(url, json=payload, headers=_headers(), timeout=timeout)
        if 200 <= r.status_code < 300:
            try:
                return r.json(), r.status_code, None
            except Exception:
                return r.text, r.status_code, None
        return None, r.status_code, (r.text or "")[:500]
    except Exception as e:
        return None, None, str(e)


def _get(url, timeout=15):
    h = _headers()
    h["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    h.pop("Content-Type", None)
    try:
        r = _SESSION.get(url, headers=h, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r
    except Exception:
        pass
    return None


# ═════════════════════════ URL slug guessing ═════════════════════════
def _slugify(v):
    s = str(v or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-limited$", "-ltd", s)


def _name_candidates(name):
    if not name:
        return []
    raw = str(name).strip()
    out = [_slugify(raw)]
    base = re.sub(r"\s+(limited|ltd\.?|pvt\.?\s*ltd\.?)$", "", raw, flags=re.I).strip()
    bs = _slugify(base)
    if bs:
        out += [bs + "-ltd", bs]
    # "&" variants: ScanX sometimes drops "and" altogether
    if "&" in raw:
        alt = _slugify(raw.replace("&", " "))
        out += [alt, alt + "-ltd"]
    return [x for x in dict.fromkeys(out) if x]


def _company_url_candidates(symbol, company_name=None):
    sym = str(symbol or "").strip().upper()
    slugs = []
    if sym in _SYMBOL_ALIASES:
        slugs.append(_SYMBOL_ALIASES[sym])
    slugs += _name_candidates(company_name)
    if sym:
        slugs += [_slugify(sym) + "-ltd", _slugify(sym)]
    urls = list(dict.fromkeys(SCANX_BASE + s for s in slugs if s))
    known = _SLUG_CACHE.get(sym)
    if known:
        urls = [known] + [u for u in urls if u != known]
    return urls


def company_url(symbol, company_name=None):
    c = _company_url_candidates(symbol, company_name)
    return c[0] if c else SCANX_BASE + _slugify(symbol)


# ═════════════════════════ ISIN discovery ═════════════════════════
_ISIN_RE = re.compile(r"\b(IN[EF][0-9A-Z]{8}[0-9])\b")


def isin_is_valid(isin: str) -> bool:
    """ISIN format + check digit (Luhn over the letter-expanded string)."""
    s = str(isin or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", s):
        return False
    digits = "".join(str(int(c, 36)) for c in s)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _discover_isin(html: str) -> str | None:
    """Most frequent VALID equity ISIN (INE…) on the page. Check digit filters out false hits."""
    if not html:
        return None
    found = [m for m in _ISIN_RE.findall(html) if m.startswith("INE") and isin_is_valid(m)]
    if not found:
        return None
    return Counter(found).most_common(1)[0][0]


def _find_isin(symbol, company_name=None, html=None):
    sym = str(symbol or "").upper().strip()
    env = os.getenv("ARKA_SCANX_ISIN_" + re.sub("[^A-Z0-9]", "", sym), "").strip().upper()
    if env and isin_is_valid(env):
        return env
    if sym in _ISIN:
        return _ISIN[sym]
    if sym in _ISIN_CACHE:
        return _ISIN_CACHE[sym]
    found = _discover_isin(html) if html else None
    if found:
        _ISIN_CACHE[sym] = found
    return found


# ═════════════════════════ Table classification ═════════════════════════
def _clean_frame(df):
    x = df.copy()
    x.columns = [str(c).strip() for c in x.columns]
    return x.dropna(axis=0, how="all").dropna(axis=1, how="all").reset_index(drop=True)


def _table_text(df):
    return " ".join([str(c) for c in df.columns] + df.astype(str).head(8).values.flatten().tolist()).lower()


def _classify_tables(tables):
    out = {}
    for raw in tables:
        try:
            df = _clean_frame(raw)
        except Exception:
            continue
        if df.empty:
            continue
        text = _table_text(df)
        cols = " ".join(str(c).lower() for c in df.columns)
        if "quarterly financials" in text or (all(k in text for k in ("revenue", "expenses", "ebitda")) and len(df.columns) >= 5):
            out.setdefault("financials", df)
        elif "balance sheet" in text or all(k in text for k in ("total assets", "total equity")):
            out.setdefault("balance_sheet", df)
        elif "cash flow" in text or all(k in text for k in ("operating activities", "financing activities")):
            out.setdefault("cash_flow", df)
        elif "% holding" in text or all(k in text for k in ("promoter", "fiis", "diis")) or all(k in text for k in ("promoters", "fii", "dii")):
            out.setdefault("shareholding", df)
        elif "funding house" in text and "current holding" in text:
            out.setdefault("mf_holdings", df)
        elif "competitors" in text and "market cap" in text and ("p/e" in text or "pe ratio" in text):
            out.setdefault("peers", df)
        elif "record date" in text and ("corporate action" in text or "ltp at announcement" in text):
            out.setdefault("corporate_actions", df)
        elif "dividend per share" in text or ("dividend" in text and "record date" in text):
            out.setdefault("dividend", df)
        elif "rsi" in text and any(k in text for k in ("macd", "sma", "ema", "moving average")):
            out.setdefault("technical", df)
        elif any(k in text for k in ("target price", "analyst", "buy", "sell")) and any(k in text for k in ("target", "rating")):
            out.setdefault("analyst", df)
        elif "market cap" in cols and ("pe ratio" in cols or "p/e" in cols):
            out.setdefault("snapshot", df)
    return out


def _technical_rows(df):
    """Technical-indicator table -> [{'indicator','value','state'}]."""
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        vals = ["" if pd.isna(v) else str(v) for v in r.tolist()]
        if not vals or not vals[0].strip():
            continue
        rows.append({"indicator": vals[0].strip(),
                     "value": vals[1].strip() if len(vals) > 1 else "—",
                     "state": vals[2].strip() if len(vals) > 2 else "—"})
    return rows


# ═════════════════════════ HTML extraction ═════════════════════════
_METRIC_LABELS = ["Market Cap", "PE Ratio", "Volume", "Day High - Low", "52W High-Low", "EPS", "PB Ratio",
                  "Book Value", "EBITDA", "Dividend Yield", "Industry", "Sector", "Return on Equity",
                  "Debt to Equity", "Analyst Rating"]


def _extract_metric(text, label):
    labels = "|".join(re.escape(x) for x in _METRIC_LABELS)
    m = re.search(rf"{re.escape(label)}\s*([₹\d,\.\-+%()A-Za-z ]{{1,80}}?)(?=\s+(?:{labels})\b|$)", text, re.I)
    return m.group(1).strip() if m else None


def _extract_snapshot(soup):
    text = " ".join(soup.stripped_strings)
    out = {}
    for label in _METRIC_LABELS:
        if label in ("Analyst Rating",):
            continue
        v = _extract_metric(text, label)
        if v:
            out[label] = v
    h = soup.find("h1")
    if h:
        name = h.get_text(" ", strip=True)
        out["company_name"] = name
        strings = list(soup.stripped_strings)
        try:
            i = strings.index(name)
            tail = " ".join(strings[i + 1:i + 8])
        except ValueError:
            tail = ""
        m = re.search(r"(\d[\d,]*\.\d+)\s+([+-]\d[\d,.]*?)\s*\(([-+]?\d[\d.]+%)\)", tail)
        if m:
            out["price"] = m.group(1)
            out["change"] = f"{m.group(2)} ({m.group(3)})"
    return out


def _extract_about(soup):
    """Paragraphs under an 'About' heading; falls back to the meta description."""
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(" ", strip=True).lower().startswith("about"):
            parts = []
            for n in h.find_all_next():
                if n is not h and getattr(n, "name", None) in {"h2", "h3"}:
                    break
                if getattr(n, "name", None) == "p":
                    t = n.get_text(" ", strip=True)
                    if len(t) > 40:
                        parts.append(t)
                if sum(len(p) for p in parts) > 1800:
                    break
            if parts:
                return {"description": " ".join(parts)[:2000]}
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        return {"description": meta["content"].strip()}
    return {}


def _section_links(soup, heading_text, limit=100):
    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(" ", strip=True).lower() == heading_text.lower():
            heading = h
            break
    if heading is None:
        return []
    out, seen = [], set()
    for n in heading.find_all_next():
        if getattr(n, "name", None) in {"h2", "h3"} and n is not heading:
            break
        if getattr(n, "name", None) != "a":
            continue
        title = n.get_text(" ", strip=True)
        href = n.get("href")
        if not title or not href:
            continue
        url = urljoin(SCANX_HOST, href)
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": title, "url": url})
        if len(out) >= limit:
            break
    return out


def _df_records(df, limit=500):
    if df is None or df.empty:
        return []
    return df.head(limit).to_dict(orient="records")


def _looks_like_scanx(soup):
    text = " ".join(soup.stripped_strings).lower()
    return bool(soup.find("h1")) and ("key fundamentals" in text or "quarterly financial results" in text) and "market cap" in text


# ═════════════════════════ MF transactions ═════════════════════════
def _normalize_mf_response(raw):
    """Keep ScanX's native schema; expose row dicts and positional arrays. Nothing is position-mapped."""
    if raw is None:
        return {"raw": None, "rows": [], "arrays": []}
    obj = raw
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return {"raw": obj, "rows": [], "arrays": []}
    rows = []
    fundish = {"fundname", "fund_name", "fundinghouse", "scheme", "transaction", "isin"}

    def walk(x):
        if isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            if any(k.lower() in fundish for k in x):
                rows.append(x)
            for v in x.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    walk(obj)

    def find_arrays(x):
        if isinstance(x, list):
            if x and all(isinstance(v, list) for v in x):
                return x
            for v in x:
                r = find_arrays(v)
                if r is not None:
                    return r
        elif isinstance(x, dict):
            for v in x.values():
                r = find_arrays(v)
                if r is not None:
                    return r
        return None

    return {"raw": obj, "rows": rows, "arrays": find_arrays(obj) or []}


def fetch_mf_transactions(isin, page=1, page_size=25, timeout=15, max_pages=1):
    """max_pages>1 keeps requesting following pages until a page returns no new rows."""
    all_rows, all_arrays, last = [], [], None
    for p in range(int(page), int(page) + max(1, int(max_pages))):
        payload = {"data": {"isin": str(isin), "page": p, "pageSize": int(page_size)}}
        raw, status, error = _post_json(MF_TX_URL, payload, timeout)
        if raw is None:
            if last is None:
                return {"status": "unavailable", "endpoint": MF_TX_URL, "http_status": status, "error": error, "payload": payload}
            break
        norm = _normalize_mf_response(raw)
        last = {"status": "live", "endpoint": MF_TX_URL, "http_status": status, "payload": payload, **norm}
        new_rows = [r for r in norm["rows"] if r not in all_rows]
        new_arrays = [a for a in norm["arrays"] if a not in all_arrays]
        if not new_rows and not new_arrays:
            break
        all_rows += new_rows
        all_arrays += new_arrays
        if len(norm["rows"]) < page_size and len(norm["arrays"]) < page_size:
            break
    if last is None:
        return {"status": "unavailable", "endpoint": MF_TX_URL, "error": "no response"}
    last["rows"], last["arrays"] = all_rows, all_arrays
    return last


# ═════════════════════════ Price history ═════════════════════════
def fetch_price_history(sec_id, symbol, interval="D", start=None, end=None, delivery=True, timeout=15):
    payload = {"EXCH": "NSE", "SYM": str(symbol).upper(), "SEG": "E", "INST": "EQUITY", "SEC_ID": int(sec_id),
               "EXPCODE": 0, "INTERVAL": interval,
               "START": int(start) if start is not None else int(time.time()) - 365 * 86400,
               "END": int(end) if end is not None else int(time.time()), "DeliveryPer": bool(delivery)}
    raw, status, error = _post_json(PRICE_URL, payload, timeout)
    if raw is None:
        return {"status": "unavailable", "endpoint": PRICE_URL, "http_status": status, "error": error, "payload": payload}
    return {"status": "live", "endpoint": PRICE_URL, "http_status": status, "payload": payload, "raw": raw}


def history_to_df(result: dict) -> pd.DataFrame:
    """Best-effort: turn a fetch_price_history() result into an OHLCV DataFrame.

    Handles the two usual shapes (dict of parallel lists, or list of row dicts).
    Returns an empty DataFrame if the schema is not recognised — never guesses values.
    """
    raw = (result or {}).get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return pd.DataFrame()
    if isinstance(raw, dict) and isinstance(raw.get("data"), (dict, list)):
        raw = raw["data"]
    df = pd.DataFrame()
    try:
        if isinstance(raw, dict):
            df = pd.DataFrame(raw)
        elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
            df = pd.DataFrame(raw)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    low = {str(c).lower(): c for c in df.columns}
    pick = lambda *names: next((low[n] for n in names if n in low), None)
    cols = {"time": pick("time", "t", "timestamp", "date", "start_time"), "Open": pick("open", "o"),
            "High": pick("high", "h"), "Low": pick("low", "l"), "Close": pick("close", "c"),
            "Volume": pick("volume", "v", "vol")}
    if not all(cols[k] for k in ("time", "Open", "High", "Low", "Close")):
        return pd.DataFrame()
    out = pd.DataFrame({k: df[v] for k, v in cols.items() if v})
    t = pd.to_numeric(out["time"], errors="coerce")
    unit = "s" if t.dropna().median() < 1e11 else "ms"
    out.index = pd.to_datetime(t, unit=unit, errors="coerce")
    return out.drop(columns=["time"]).dropna(subset=["Close"]).sort_index()


# ═════════════════════════ Main entry ═════════════════════════
def _empty_result(sym, company_name, url):
    return {"status": "unavailable", "symbol": sym, "name": company_name or sym, "url": url,
            "snapshot": {}, "about": {}, "announcements": [], "news": [], "technical": [],
            "tables": {}, "table_records": {}, "isin": None,
            "mf_transactions": {"status": "unavailable", "reason": "not fetched"},
            "source_mode": "api+html", "errors": [], "fetched_at": time.time()}


def _load_page(sym, company_name, timeout):
    """Try slug candidates (capped). Returns (response, soup, last_url)."""
    candidates = _company_url_candidates(sym, company_name)[:_MAX_SLUG_TRIES]
    last_url = candidates[0] if candidates else company_url(sym, company_name)
    for url in candidates:
        last_url = url
        r = _get(url, timeout)
        if r is None:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        if _looks_like_scanx(soup):
            _SLUG_CACHE[sym] = r.url if r.url.startswith(SCANX_BASE) else url
            return r, soup, last_url
    return None, None, last_url


def fetch_scanx_company(symbol, company_name=None, timeout=15, mf_pages=1):
    sym = str(symbol or "").strip().upper()
    result = _empty_result(sym, company_name, company_url(sym, company_name))

    pre_isin = _find_isin(sym, company_name)        # env / built-in map / cache
    ex = ThreadPoolExecutor(max_workers=2)
    mf_future = ex.submit(fetch_mf_transactions, pre_isin, 1, 100, timeout, mf_pages) if pre_isin else None
    try:
        resp, soup, last_url = _load_page(sym, company_name, timeout)
        result["url"] = last_url
        html_text = ""
        if resp is not None:
            html_text = resp.text
            try:
                tables = pd.read_html(StringIO(html_text))
            except Exception as e:
                tables = []
                result["errors"].append(f"table parse: {e}")
            classified = _classify_tables(tables)
            result.update({
                "status": "live",
                "name": soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else (company_name or sym),
                "url": resp.url, "snapshot": _extract_snapshot(soup), "about": _extract_about(soup),
                "tables": classified, "table_records": {k: _df_records(v) for k, v in classified.items()},
                "technical": _technical_rows(classified.get("technical")),
                "announcements": _section_links(soup, "Announcements", 100),
                "news": _section_links(soup, "Company News", 30),
            })
        else:
            result["errors"].append("company page not found (slug guess failed)")

        isin = pre_isin or _find_isin(sym, company_name, html_text)
        result["isin"] = isin
        if mf_future is None and isin:
            mf_future = ex.submit(fetch_mf_transactions, isin, 1, 100, timeout, mf_pages)
        if mf_future is not None:
            try:
                result["mf_transactions"] = mf_future.result(timeout=timeout * max(1, mf_pages) + 10)
            except Exception as e:
                result["mf_transactions"] = {"status": "unavailable", "error": str(e)}
        else:
            result["mf_transactions"] = {
                "status": "unavailable",
                "reason": "ISIN not found on the page. Set ARKA_SCANX_ISIN_<SYMBOL> in the environment to force one."}
    finally:
        ex.shutdown(wait=False)
    result["fetched_at"] = time.time()
    return result
