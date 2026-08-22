"""
screener_scraper.py — Arka Trades Research Module (data layer)
"""

import re
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd

_BASE = "https://www.screener.in"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT = 12
_CACHE_DIR = Path(".cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_TTL_SECONDS = 3600 * 6


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
        pass


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


def _resolve_url(symbol: str) -> str | None:
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
    direct = _resolve_url(symbol)
    if direct:
        try:
            r = requests.get(direct, headers=_HEADERS, timeout=_TIMEOUT)
            m = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", r.text)
            name = m.group(1).strip() if m else symbol.upper()
            return {"url": direct, "name": name}
        except Exception:
            return {"url": direct, "name": symbol.upper()}

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
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        tables = pd.read_html(r.text)
        return tables if tables else None
    except Exception:
        return None


def _find_table_by_header(tables: list[pd.DataFrame], must_contain: list[str]) -> pd.DataFrame | None:
    for t in tables:
        try:
            first_col = t.iloc[:, 0].astype(str).str.cat(sep=" ")
            headers = " ".join(str(c) for c in t.columns)
            blob = (first_col + " " + headers).lower()
            if all(needle.lower() in blob for needle in must_contain):
                return t
        except Exception:
            continue
    return None


def _df_to_records(df: pd.DataFrame) -> dict:
    try:
        periods = [str(c) for c in df.columns[1:]]
        rows = []
        for _, row in df.iterrows():
            label = str(row.iloc[0]).strip()
            if not label or label.lower() == "nan":
                continue
            values = [str(v) for v in row.iloc[1:].tolist()]
            rows.append({"label": label, "values": values})
        return {"periods": periods, "rows": rows}
    except Exception:
        return {"periods": [], "rows": []}


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
    return _get_section(symbol, "quarterly", ["sales", "net profit"], url)


def get_yearly_results(symbol: str, url: str | None = None) -> dict:
    return _get_section(symbol, "yearly", ["sales", "net profit", "eps"], url)


def get_shareholding(symbol: str, url: str | None = None) -> dict:
    return _get_section(symbol, "shareholding", ["promoters", "fiis", "diis"], url)


def get_sector_info(symbol: str, url: str | None = None) -> dict:
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    try:
        r = requests.get(resolved_url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code == 200:
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
    return {
        "status": "not_implemented",
        "reason": "Peer comparison requires a JS-executing fetch; not available via this scraper yet.",
        "data": None,
    }


def get_summary(symbol: str, url: str | None = None) -> dict:
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
            # Updated robust pattern matching for Screener top ratio cards
            label_patterns = {
                "market_cap":     r"Market Cap.*?<span>\s*₹\s*([\d,]+)\s*</span>",
                "current_price":  r"Current Price.*?<span>\s*₹\s*([\d,]+\.?\d*)\s*</span>",
                "pe_ratio":       r"Stock P/E.*?>([\d,]+\.?\d*)<",
                "book_value":     r"Book Value.*?₹\s*([\d,]+\.?\d*)",
                "dividend_yield": r"Dividend Yield.*?([\d.]+)\s*%",
                "roce":           r"ROCE.*?([\d.]+)\s*%",
                "roe":            r"ROE.*?([\d.]+)\s*%",
                "face_value":     r"Face Value.*?₹\s*([\d.]+)",
            }
            for key, pat in label_patterns.items():
                m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
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


# ── Factors panel helpers ────────────────────────────────────────

def _parse_numeric(s) -> float | None:
    if s is None:
        return None
    cleaned = str(s).strip().replace(",", "").replace("%", "").replace("₹", "").strip()
    cleaned = cleaned.replace("Cr", "").strip()
    if cleaned in ("", "-", "—", "nan", "NaN"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _row_by_label(rows: list[dict], needle: str) -> dict | None:
    needle = needle.lower()
    for r in rows:
        if needle in r["label"].lower():
            return r
    return None


def _latest_two(values: list) -> tuple:
    nums = [_parse_numeric(v) for v in values]
    nums = [n for n in nums if n is not None]
    if len(nums) < 2:
        return (nums[-1], None) if nums else (None, None)
    return nums[-1], nums[-2]


def get_factors(symbol: str, full_research: dict | None = None) -> dict:
    data = full_research or get_full_research(symbol)
    if not data.get("resolved"):
        return {"status": "unavailable", "items": []}

    items = []

    summary_data = (data.get("summary") or {}).get("data") or {}
    for key, label, unit in (
        ("roce", "ROCE", "%"),
        ("roe", "ROE", "%"),
        ("pe_ratio", "P/E", "x"),
    ):
        val = _parse_numeric(summary_data.get(key))
        if val is not None:
            items.append({"label": label, "latest": val, "previous": None, "unit": unit})

    sh_data = (data.get("shareholding") or {}).get("data") or {}
    promoters_row = _row_by_label(sh_data.get("rows", []), "promoters")
    if promoters_row:
        latest, prev = _latest_two(promoters_row["values"])
        if latest is not None:
            items.append({"label": "Promoter Holding", "latest": latest, "previous": prev, "unit": "%"})

    q_data = (data.get("quarterly") or {}).get("data") or {}
    for needle, label in (("sales", "Sales (QoQ)"), ("net profit", "Net Profit (QoQ)")):
        row = _row_by_label(q_data.get("rows", []), needle)
        if row:
            latest, prev = _latest_two(row["values"])
            if latest is not None:
                items.append({"label": label, "latest": latest, "previous": prev, "unit": "Cr"})

    if not items:
        return {"status": "unavailable", "items": []}
    
    status = "live" if len(items) >= 6 else "partial"
    return {"status": status, "items": items}


def get_earnings_date(symbol: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        return {"status": "unavailable", "reason": "yfinance not installed", "date": None}

    try:
        t = yf.Ticker(symbol.upper().strip() + ".NS")
        cal = t.calendar
        if cal:
            for key in ("Earnings Date", "EarningsDate"):
                if key in cal:
                    val = cal[key]
                    if isinstance(val, (list, tuple)) and val:
                        return {"status": "live", "date": str(val[0]), "source": "yfinance calendar"}
                    if val:
                        return {"status": "live", "date": str(val), "source": "yfinance calendar"}
    except Exception:
        pass

    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            upcoming = ed[ed.index >= pd.Timestamp.now(tz=ed.index.tz)]
            if not upcoming.empty:
                next_date = upcoming.index[0]
                return {"status": "live", "date": str(next_date.date()), "source": "yfinance earnings_dates"}
    except Exception:
        pass

    return {
        "status": "unavailable",
        "reason": "yfinance does not reliably publish forward earnings dates for NSE-listed stocks.",
        "date": None,
    }


def get_full_research(symbol: str) -> dict:
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
