"""
screener_scraper.py — Arka Trades Research Module (data layer)
Fetches fundamentals from Screener.in without login.

DESIGN NOTE ON EXTRACTION METHOD:
Screener's HTML table id/class attributes were not directly inspectable
while building this (sandboxed dev environment could not reach
screener.in — same class of restriction your egress allowlist handles
in prod). Rather than hand-write BeautifulSoup selectors against markup
that was never actually verified, every table extractor here uses
pandas.read_html() and identifies the right table by matching on
VISIBLE CONTENT (header text like "Promoters", "Sales", quarter labels
like "Jun 2026") — content that WAS verified against a live fetch of
the Reliance company page. This is more resilient than id/class
matching: if Screener renames a CSS class, this still works, because
it never looked at the class. If Screener changes header wording
entirely, this fails LOUDLY (returns None / empty), not silently —
consistent with "tell me it's not there" over "fake a result."

CONFIRMED FROM LIVE FETCH (2026-08):
  - Quarterly Results, Profit & Loss, Balance Sheet, Cash Flows,
    Ratios, Shareholding Pattern are ALL plain server-rendered
    <table> elements. No JS required, no login required.
  - "Peers" section text-literally contains "Loading peers table ..."
    in the static HTML — it is JS/AJAX-loaded and NOT reachable by
    this method. Deliberately NOT implemented; see get_peers() stub.
  - Sector P/E as a standalone number was not found anywhere on the
    company page. Only the sector/industry CLASSIFICATION (breadcrumb
    text) is present. Deliberately NOT implemented as a number; see
    get_sector_info() which returns classification text only.
  - Some "Insights" segment-level metrics (store counts, ARPU, etc.)
    are premium/login-gated and render as literal "x,xxx" placeholder
    text in the anonymous view. Not scraped — there is no real data
    there to get.

CACHING:
Same last-known-good disk cache pattern as app.py's MMI scraper
(_MMI_CACHE_FILE). Fundamentals change slowly (quarterly at most),
so a stale cache is a perfectly reasonable fallback and worth keeping
across app restarts, unlike live price data.
"""

import re
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd

# ── Constants ────────────────────────────────────────────────
_BASE = "https://www.screener.in"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT = 12
_CACHE_DIR = Path(".cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_TTL_SECONDS = 3600 * 6  # 6h — fundamentals don't move intraday


# ── Disk cache helpers (mirrors app.py's MMI pattern) ───────────

def _cache_path(symbol: str, section: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]", "_", symbol.upper())
    return _CACHE_DIR / f"screener_{safe}_{section}.json"


def _cache_write(symbol: str, section: str, data: dict):
    try:
        payload = {
            "data": data,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _cache_path(symbol, section).write_text(json.dumps(payload, default=str))
    except Exception:
        pass  # cache is best-effort; never let a write failure break the caller


def _cache_read(symbol: str, section: str):
    p = _cache_path(symbol, section)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text())
        fetched = datetime.fromisoformat(payload["fetched_at_utc"])
        age_s = (datetime.now(timezone.utc) - fetched).total_seconds()
        return {"data": payload["data"], "age_seconds": age_s, "fetched_at_utc": fetched}
    except Exception:
        return None


# ── Page fetch (one fetch, all sections parsed from it) ─────────

def _resolve_url(symbol: str) -> str | None:
    """
    Try consolidated first (matches app default view), fall back to
    standalone-only URL if that 404s. Returns None if both fail —
    caller should then try the search fallback in resolve_symbol().
    """
    sym = symbol.upper().strip()
    for path in (f"/company/{sym}/consolidated/", f"/company/{sym}/"):
        try:
            r = requests.get(_BASE + path, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 200 and "Screener" in r.text:
                return _BASE + path
        except Exception:
            continue
    return None


def resolve_symbol(symbol: str) -> dict | None:
    """
    Confirms a symbol resolves to a real Screener company page, OR
    falls back to Screener's own search endpoint if the direct
    /company/{SYM}/ URL doesn't exist (e.g. symbol typo, or Screener
    uses a different short code for this stock than the NSE ticker).
    Returns {"url": ..., "name": ...} or None if nothing found.
    """
    direct = _resolve_url(symbol)
    if direct:
        try:
            r = requests.get(direct, headers=_HEADERS, timeout=_TIMEOUT)
            m = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", r.text)
            name = m.group(1).strip() if m else symbol.upper()
            return {"url": direct, "name": name}
        except Exception:
            return {"url": direct, "name": symbol.upper()}

    # Fallback: Screener's own search-as-you-type endpoint
    try:
        r = requests.get(
            f"{_BASE}/api/company/search/",
            params={"q": symbol}, headers=_HEADERS, timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            results = r.json()
            if results:
                first = results[0]
                url = _BASE + first.get("url", "")
                if url and not url.endswith("/"):
                    url += "/"
                return {"url": url, "name": first.get("name", symbol.upper())}
    except Exception:
        pass
    return None


def _fetch_all_tables(url: str) -> list[pd.DataFrame] | None:
    """
    Single fetch, all tables parsed once via read_html. Every
    get_*() function below re-uses this rather than re-fetching per
    section, since it's the same page. NOT cached at this layer —
    caching happens per-section below, so a partial-page parse
    failure in one section doesn't invalidate cache for sections that
    parsed fine.
    """
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        # read_html wants file-like or raw html string; flavor=lxml
        # falls back to bs4/html5lib automatically if lxml missing.
        tables = pd.read_html(r.text)
        return tables if tables else None
    except Exception:
        return None


def _find_table_by_header(tables: list[pd.DataFrame], must_contain: list[str]) -> pd.DataFrame | None:
    """
    Scans parsed tables for one whose first column (row labels) or
    header row contains ALL of the given substrings, case-insensitive.
    This is the content-matching approach explained in the module
    docstring — resilient to markup changes, sensitive to wording
    changes (which is the correct tradeoff here).
    """
    for t in tables:
        try:
            # Flatten first column + column headers into one search blob
            first_col = t.iloc[:, 0].astype(str).str.cat(sep=" ")
            headers = " ".join(str(c) for c in t.columns)
            blob = (first_col + " " + headers).lower()
            if all(needle.lower() in blob for needle in must_contain):
                return t
        except Exception:
            continue
    return None


def _df_to_records(df: pd.DataFrame) -> dict:
    """
    Converts a Screener results-style table (row labels in col 0,
    period columns after) into {"periods": [...], "rows": [{"label":
    ..., "values": [...]}]}. Keeps raw string values (Screener already
    formats %, Cr, etc.) rather than trying to re-parse numerics —
    less to get wrong, and the UI wants the same formatting Screener
    itself chose.
    """
    try:
        periods = [str(c) for c in df.columns[1:]]
        rows = []
        for _, row in df.iterrows():
            label = str(row.iloc[0]).strip()
            # Skip Screener's expandable-row markers/junk rows
            if not label or label.lower() == "nan":
                continue
            values = [str(v) for v in row.iloc[1:].tolist()]
            rows.append({"label": label, "values": values})
        return {"periods": periods, "rows": rows}
    except Exception:
        return {"periods": [], "rows": []}


# ── Public section fetchers ─────────────────────────────────────
# Each: try live fetch -> on success, write cache, return fresh.
#       on failure, read cache -> return stale-but-labeled.
#       on total failure, return a clear "unavailable" shape.

def _get_section(symbol: str, section: str, must_contain: list[str], url: str | None = None) -> dict:
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    tables = _fetch_all_tables(resolved_url)
    if tables:
        table = _find_table_by_header(tables, must_contain)
        if table is not None:
            records = _df_to_records(table)
            if records["rows"]:
                _cache_write(symbol, section, records)
                return {"status": "live", "data": records, "url": resolved_url}

    # Live fetch failed or table not found — fall back to cache
    cached = _cache_read(symbol, section)
    if cached:
        return {
            "status": "stale",
            "data": cached["data"],
            "age_seconds": cached["age_seconds"],
            "url": resolved_url,
        }

    return {"status": "unavailable", "reason": "Not found on page and no cached copy exists", "data": None}


def get_quarterly_results(symbol: str, url: str | None = None) -> dict:
    """Quarterly Sales/Profit table. Header row has month-year cols like 'Jun 2026'."""
    return _get_section(symbol, "quarterly", ["sales", "net profit"], url)


def get_yearly_results(symbol: str, url: str | None = None) -> dict:
    """Profit & Loss (annual) table. Same shape as quarterly but yearly columns."""
    return _get_section(symbol, "yearly", ["sales", "net profit", "eps"], url)


def get_shareholding(symbol: str, url: str | None = None) -> dict:
    """Shareholding Pattern table — Promoters/FIIs/DIIs/Government/Public rows."""
    return _get_section(symbol, "shareholding", ["promoters", "fiis", "diis"], url)


def get_sector_info(symbol: str, url: str | None = None) -> dict:
    """
    NOT a table — Screener shows sector classification as a breadcrumb
    of links (Broad Sector -> Sector -> Industry -> Broad Industry),
    not a data table. Parsed with a targeted regex instead of
    read_html since there's no table structure to match against.
    Deliberately returns classification TEXT, not a P/E NUMBER — see
    module docstring on why sector P/E as a figure isn't available
    via this route.
    """
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    try:
        r = requests.get(resolved_url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code == 200:
            # Links carrying title="Broad Sector" / "Sector" / "Industry" etc,
            # matching the breadcrumb structure confirmed on the live fetch.
            pattern = re.compile(
                r'<a[^>]*title="(Broad Sector|Sector|Broad Industry|Industry)"[^>]*>([^<]+)</a>',
                re.IGNORECASE,
            )
            matches = pattern.findall(r.text)
            if matches:
                classification = {label: text.strip() for label, text in matches}
                _cache_write(symbol, "sector", classification)
                return {"status": "live", "data": classification, "url": resolved_url}
    except Exception:
        pass

    cached = _cache_read(symbol, "sector")
    if cached:
        return {"status": "stale", "data": cached["data"], "age_seconds": cached["age_seconds"], "url": resolved_url}
    return {"status": "unavailable", "reason": "Classification not found and no cached copy exists", "data": None}


def get_peers(symbol: str, url: str | None = None) -> dict:
    """
    DELIBERATELY NOT IMPLEMENTED. Screener's peer comparison table is
    loaded via a separate AJAX call after page render — it is not
    present in the HTML this scraper (or any plain requests.get) can
    see. A real implementation needs either:
      (a) a headless-browser tool (playwright/selenium) that executes
          JS and waits for the request, or
      (b) reverse-engineering the specific JSON endpoint the page
          calls to fill that table (not yet found/confirmed).
    Returns a stable "unavailable" shape so the UI can show a clear
    placeholder rather than a blank section or a crash.
    """
    return {
        "status": "not_implemented",
        "reason": "Peer comparison requires a JS-executing fetch; not available via this scraper yet.",
        "data": None,
    }


def get_summary(symbol: str, url: str | None = None) -> dict:
    """
    Top-of-page key stats: Market Cap, Current Price, P/E, Book Value,
    Dividend Yield, ROCE, ROE, Face Value. These render as a bullet
    list, not a <table>, so parsed via regex on the "Market Cap ₹ ..."
    style lines confirmed in the live fetch.
    """
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    try:
        r = requests.get(resolved_url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code == 200:
            text = r.text
            fields = {}
            label_patterns = {
                "market_cap":     r"Market Cap[^\d₹]*₹\s*([\d,]+)",
                "current_price":  r"Current Price[^\d₹]*₹\s*([\d,]+\.?\d*)",
                "pe_ratio":       r"Stock P/E[^\d]*([\d,]+\.?\d*)",
                "book_value":     r"Book Value[^\d₹]*₹\s*([\d,]+\.?\d*)",
                "dividend_yield": r"Dividend Yield[^\d]*([\d.]+)\s*%",
                "roce":           r"ROCE[^\d]*([\d.]+)\s*%",
                "roe":            r"ROE[^\d]*([\d.]+)\s*%",
                "face_value":     r"Face Value[^\d₹]*₹\s*([\d.]+)",
            }
            for key, pat in label_patterns.items():
                m = re.search(pat, text)
                if m:
                    fields[key] = m.group(1).strip()
            if fields:
                _cache_write(symbol, "summary", fields)
                return {"status": "live", "data": fields, "url": resolved_url}
    except Exception:
        pass

    cached = _cache_read(symbol, "summary")
    if cached:
        return {"status": "stale", "data": cached["data"], "age_seconds": cached["age_seconds"], "url": resolved_url}
    return {"status": "unavailable", "reason": "Summary stats not found and no cached copy exists", "data": None}


_SECTOR_MAP_FILE = _CACHE_DIR / "sector_map.json"


def _load_sector_map() -> dict:
    if not _SECTOR_MAP_FILE.exists():
        return {}
    try:
        return json.loads(_SECTOR_MAP_FILE.read_text())
    except Exception:
        return {}


def _save_sector_map(mapping: dict):
    try:
        _SECTOR_MAP_FILE.write_text(json.dumps(mapping))
    except Exception:
        pass  # best-effort, same as every other cache write in this module


def get_sector_for_heatmap(symbol: str) -> str:
    """
    Returns a short sector label for grouping symbols on the Heatmap
    page, backed by a PERSISTED lookup file rather than a live fetch
    per call. Sector classification doesn't change intraday (or even
    week to week in practice), so re-fetching it on every scan the
    way price data needs to be would be pure network waste layered on
    top of the scanner's existing per-symbol price fetch — this
    function fetches a symbol's sector via get_sector_info() ONLY the
    first time it's ever asked about, then serves every future call
    from the on-disk sector_map.json file.

    Returns "Unclassified" (never raises, never returns None) if the
    live fetch fails and the symbol has never been cached before —
    the Heatmap page groups these under an explicit "Unclassified"
    bucket rather than silently dropping the symbol from the map.
    """
    mapping = _load_sector_map()
    if symbol in mapping:
        return mapping[symbol]

    info = get_sector_info(symbol)
    data = info.get("data") or {}
    # Prefer "Sector" over "Broad Sector" for heatmap granularity —
    # "Broad Sector" (e.g. "Energy") groups too coarsely for a useful
    # tile grid; "Sector" (e.g. "Oil, Gas & Consumable Fuels") is the
    # level Screener's own breadcrumb treats as the meaningful one.
    sector = data.get("Sector") or data.get("Broad Sector") or "Unclassified"

    mapping[symbol] = sector
    _save_sector_map(mapping)
    return sector


def get_full_research(symbol: str) -> dict:
    """
    Convenience wrapper: resolves the symbol ONCE, then fetches every
    section using that resolved URL directly (skips re-resolving per
    section — each get_*() accepts an optional url= for exactly this
    reason). This is what research_page.py should call for a full
    search result rather than calling each get_*() independently,
    which would re-hit resolve_symbol() up to 5x for one search.
    """
    res = resolve_symbol(symbol)
    if not res:
        return {
            "resolved": False,
            "symbol": symbol.upper(),
            "reason": "Could not find this symbol on Screener.",
        }

    url = res["url"]
    return {
        "resolved": True,
        "symbol": symbol.upper(),
        "name": res["name"],
        "url": url,
        "summary": get_summary(symbol, url=url),
        "quarterly": get_quarterly_results(symbol, url=url),
        "yearly": get_yearly_results(symbol, url=url),
        "shareholding": get_shareholding(symbol, url=url),
        "sector": get_sector_info(symbol, url=url),
        "peers": get_peers(symbol, url=url),
    }
