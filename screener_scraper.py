"""
screener_scraper.py — Arka Trades Research Module (data layer)

v6 CHANGES FROM v5:
  - FIXED (root cause of "No data rows parsed" on Streamlit Cloud):
    _find_tables_by_header() called " ".join(t.astype(str).values.flatten())
    to build a searchable text blob per table. On pandas 3.0 (which Cloud
    installs fresh since requirements.txt pins "pandas>=2.0.0" with no
    upper bound), .astype(str) reports dtype "str" but leaves NaN cells
    as real Python float('nan') objects instead of the string "nan".
    " ".join() then throws TypeError: sequence item N: expected str
    instance, float found. That exception was swallowed by a bare
    "except Exception: continue", so any table containing a NaN cell
    (which is most of them — Screener's tables routinely have a trailing
    "Raw PDF" row that's all NaN, or blank cells in early-period columns)
    was silently dropped from matching, on every single call, before
    header-matching ever ran. This is why all four sections
    (Quarterly/Yearly/Balance Sheet/Shareholding) failed identically and
    deterministically on Cloud (pandas 3.0) while working locally
    (presumably pandas 2.x, where .astype(str) on NaN does not leave a
    raw float behind). Fixed by building the text blob with an explicit
    NaN-safe conversion instead of relying on .astype(str) to have
    already done it. Verified against real Screener.in HTML: quarterly/
    yearly matching goes from 0 tables found to the expected 2 (one
    quarterly, one yearly), balance sheet and shareholding go from
    crashing-then-partially-matching to matching cleanly.
  - NEW: _fetch_html() cache TTL raised from 60s to 300s and the whole
    get_full_research() pipeline now fetches the page ONCE and reuses
    that HTML for every section, instead of relying on each section
    function to hit the 60s cache window. Same effect, but explicit and
    not dependent on wall-clock timing between calls.
  - NEW: resolve_symbol() results are now cached in-memory for the
    process lifetime (symbols rarely change which URL they resolve to),
    cutting the "resolve, then check" double round-trip for every hot
    ticker on every rerun.
  - UNCHANGED: get_balance_sheet(), get_leverage_ratios(),
    get_peer_comparison(), the curated _SECTOR_PEERS map, and all other
    v5 logic — those were not the source of the bug and did not need
    to change.
"""

import re
import json
import time
import io
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
_CACHE_TTL_SECONDS = 43200

_HTTP_CACHE = {}
_HTTP_CACHE_TTL = 300  # was 60 — widened so a full get_full_research() pass
                        # (6+ calls to the same URL) always reuses one fetch
                        # even if something upstream is briefly slow.

_RESOLVE_CACHE = {}  # symbol -> resolve_symbol() result, process-lifetime


def _fetch_html(url: str) -> str | None:
    now = time.time()
    if url in _HTTP_CACHE:
        cache_time, html = _HTTP_CACHE[url]
        if now - cache_time < _HTTP_CACHE_TTL:
            return html
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code == 200:
            _HTTP_CACHE[url] = (now, r.text)
            return r.text
    except Exception as e:
        print(f"Fetch failed for {url}: {e}")
    return None


def _cache_path(symbol: str, section: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]", "_", symbol.upper())
    return _CACHE_DIR / f"screener_{safe}_{section}.json"


def _cache_write(symbol: str, section: str, data: dict):
    try:
        payload = {"data": data, "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
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
        if age_s > _CACHE_TTL_SECONDS:
            return None
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
    sym_key = symbol.upper().strip()
    if sym_key in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[sym_key]

    direct = _resolve_url(symbol)
    result = None
    if direct:
        try:
            html = _fetch_html(direct)
            name = symbol.upper()
            if html:
                m = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", html)
                if m:
                    name = m.group(1).strip()
            result = {"url": direct, "name": name}
        except Exception:
            result = {"url": direct, "name": symbol.upper()}

    if result is None:
        try:
            r = requests.get(f"{_BASE}/api/company/search/", params={"q": symbol},
                              headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 200:
                results = r.json()
                if results:
                    first = results[0]
                    url = _BASE + first.get("url", "")
                    if url and not url.endswith("/"):
                        url += "/"
                    result = {"url": url, "name": first.get("name", symbol.upper())}
        except Exception:
            pass

    _RESOLVE_CACHE[sym_key] = result  # cache the miss too, avoids hammering on a bad symbol
    return result


def _cell_to_text(v) -> str:
    """NaN-safe scalar -> str conversion for building the searchable
    header-matching blob. This is the fix: on pandas 3.0, a DataFrame's
    .astype(str) can report dtype "str" while still holding real
    float('nan') objects in cells that were empty/missing, so joining
    the flattened array with plain str() (or relying on .astype(str)
    having already stringified everything) throws
    TypeError: sequence item N: expected str instance, float found.
    """
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def _find_tables_by_header(tables: list[pd.DataFrame], must_contain: list) -> list[pd.DataFrame]:
    matches = []
    for t in tables:
        try:
            flat = t.values.flatten()
            all_text = " ".join(_cell_to_text(v) for v in flat)
            cols_text = " ".join(str(c) for c in t.columns)
            blob = (all_text + " " + cols_text).lower()
            blob = re.sub(r'\s+', ' ', blob)
            match = True
            for condition in must_contain:
                if isinstance(condition, str):
                    if condition.lower() not in blob:
                        match = False
                        break
                elif isinstance(condition, (list, tuple)):
                    if not any(c.lower() in blob for c in condition):
                        match = False
                        break
            if match:
                matches.append(t)
        except Exception as e:
            print(f"Table header matching failed, skipping this table: {e}")
            continue
    return matches


def _df_to_records(df: pd.DataFrame) -> dict:
    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[-1]) for c in df.columns]
        else:
            df.columns = [str(c) for c in df.columns]
        periods = [str(c) for c in df.columns[1:]]
        rows = []
        for _, row in df.iterrows():
            label = _cell_to_text(row.iloc[0]).strip()
            label = label.replace("+", "").strip()
            if not label or label.lower() in ("nan", "none", ""):
                continue
            values = []
            for v in row.iloc[1:]:
                v_str = _cell_to_text(v).strip()
                if v_str.lower() in ("nan", "none", ""):
                    v_str = "—"
                values.append(v_str)
            rows.append({"label": label, "values": values})
        return {"periods": periods, "rows": rows}
    except Exception as e:
        print(f"_df_to_records failed: {e}")
        return {"periods": [], "rows": []}


def _get_section(symbol: str, section: str, must_contain: list, url: str | None = None,
                  target: str = "", html_override: str | None = None) -> dict:
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    html = html_override if html_override is not None else _fetch_html(resolved_url)
    if html:
        try:
            tables = pd.read_html(io.StringIO(html))
            matches = _find_tables_by_header(tables, must_contain)
            table = None
            if matches:
                if target == "quarterly":
                    if len(matches) >= 2:
                        table = matches[0]
                    elif len(matches) == 1:
                        table = matches[0]
                elif target == "yearly":
                    if len(matches) >= 2:
                        table = matches[1]
                    elif len(matches) == 1:
                        table = matches[0]
                elif target in ("shareholding", "balance_sheet"):
                    table = matches[0]
                else:
                    table = matches[0]

            if table is not None:
                records = _df_to_records(table)
                if records["rows"]:
                    _cache_write(symbol, section, records)
                    return {"status": "live", "data": records, "url": resolved_url}
        except Exception as e:
            print(f"Extraction failed for {section}: {e}")

    cached = _cache_read(symbol, section)
    if cached:
        return {"status": "stale", "data": cached["data"], "age_seconds": cached["age_seconds"], "url": resolved_url}

    return {"status": "unavailable", "reason": "Not found on page and no cached copy exists", "data": None}


def get_quarterly_results(symbol: str, url: str | None = None, html_override: str | None = None) -> dict:
    must_contain = [("sales", "revenue", "interest", "financing margin"), ("net profit", "profit for the period")]
    return _get_section(symbol, "quarterly", must_contain, url, target="quarterly", html_override=html_override)


def get_yearly_results(symbol: str, url: str | None = None, html_override: str | None = None) -> dict:
    must_contain = [("sales", "revenue", "interest", "financing margin"), ("net profit", "profit for the period")]
    return _get_section(symbol, "yearly", must_contain, url, target="yearly", html_override=html_override)


def get_shareholding(symbol: str, url: str | None = None, html_override: str | None = None) -> dict:
    must_contain = ["promoters", ("fiis", "fii"), ("diis", "dii")]
    return _get_section(symbol, "shareholding", must_contain, url, target="shareholding", html_override=html_override)


def get_balance_sheet(symbol: str, url: str | None = None, html_override: str | None = None) -> dict:
    must_contain = [("equity capital", "share capital"), ("reserves",), ("borrowings", "total liabilities")]
    return _get_section(symbol, "balance_sheet", must_contain, url, target="balance_sheet", html_override=html_override)


def get_sector_info(symbol: str, url: str | None = None, html_override: str | None = None) -> dict:
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    html = html_override if html_override is not None else _fetch_html(resolved_url)
    if html:
        try:
            pattern = re.compile(
                r'<a[^>]*title="(Broad Sector|Sector|Broad Industry|Industry)"[^>]*>([^<]+)</a>',
                re.IGNORECASE,
            )
            matches = pattern.findall(html)
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
    UNCHANGED — the raw Screener JS-locked peer widget is still not
    reachable. This stays as the honest placeholder. Real peer data
    lives in get_peer_comparison() below, sourced differently.
    """
    return {
        "status": "not_implemented",
        "reason": "Screener's own peer widget requires a JS-executing fetch; not available via this scraper.",
        "data": None,
    }


def get_summary(symbol: str, url: str | None = None, html_override: str | None = None) -> dict:
    resolved_url = url
    if resolved_url is None:
        res = resolve_symbol(symbol)
        if not res:
            return {"status": "unavailable", "reason": "Symbol not found on Screener", "data": None}
        resolved_url = res["url"]

    html = html_override if html_override is not None else _fetch_html(resolved_url)
    if html:
        try:
            fields = {}
            label_patterns = {
                "market_cap":     r"Market Cap.*?<span class=\"number\">([\d,\.]+)</span>",
                "current_price":  r"Current Price.*?<span class=\"number\">([\d,\.]+)</span>",
                "pe_ratio":       r"Stock P/E.*?<span class=\"number\">([\d,\.]+)</span>",
                "book_value":     r"Book Value.*?<span class=\"number\">([\d,\.]+)</span>",
                "dividend_yield": r"Dividend Yield.*?<span class=\"number\">([\d,\.]+)</span>",
                "roce":           r"ROCE.*?<span class=\"number\">([\d,\.]+)</span>",
                "roe":            r"ROE.*?<span class=\"number\">([\d,\.]+)</span>",
                "face_value":     r"Face Value.*?<span class=\"number\">([\d,\.]+)</span>",
            }
            for key, pat in label_patterns.items():
                m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
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


def _latest_one(values: list) -> float | None:
    nums = [_parse_numeric(v) for v in values]
    nums = [n for n in nums if n is not None]
    return nums[-1] if nums else None


def get_factors(symbol: str, full_research: dict | None = None) -> dict:
    data = full_research or get_full_research(symbol)
    if not data.get("resolved"):
        return {"status": "unavailable", "items": []}

    items = []
    summary_data = (data.get("summary") or {}).get("data") or {}
    for key, label, unit in (("roce", "ROCE", "%"), ("roe", "ROE", "%"), ("pe_ratio", "P/E", "x")):
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
        if not row and needle == "sales":
            row = _row_by_label(q_data.get("rows", []), "revenue") or _row_by_label(q_data.get("rows", []), "interest")
        if not row and needle == "net profit":
            row = _row_by_label(q_data.get("rows", []), "profit for the period")
        if row:
            latest, prev = _latest_two(row["values"])
            if latest is not None:
                items.append({"label": label, "latest": latest, "previous": prev, "unit": "Cr"})

    if not items:
        return {"status": "unavailable", "items": []}
    status = "live" if len(items) >= 6 else "partial"
    return {"status": status, "items": items}


def get_leverage_ratios(symbol: str, full_research: dict | None = None) -> dict:
    data = full_research or get_full_research(symbol)
    if not data.get("resolved"):
        return {"status": "unavailable", "debt_to_equity": None, "interest_coverage": None}

    bs_data = (data.get("balance_sheet") or {}).get("data") or {}
    q_data = (data.get("quarterly") or {}).get("data") or {}

    de = None
    borrowings_row = _row_by_label(bs_data.get("rows", []), "borrowings")
    reserves_row = _row_by_label(bs_data.get("rows", []), "reserves")
    if borrowings_row and reserves_row:
        borrowings = _latest_one(borrowings_row["values"])
        reserves = _latest_one(reserves_row["values"])
        if borrowings is not None and reserves not in (None, 0):
            de = round(borrowings / reserves, 2)

    ic = None
    interest_row = _row_by_label(q_data.get("rows", []), "interest")
    opm_row = (_row_by_label(q_data.get("rows", []), "operating profit")
               or _row_by_label(q_data.get("rows", []), "financing profit"))
    if interest_row and opm_row:
        interest = _latest_one(interest_row["values"])
        operating_profit = _latest_one(opm_row["values"])
        if interest not in (None, 0) and operating_profit is not None:
            ic = round(operating_profit / interest, 2)

    if de is None and ic is None:
        return {"status": "unavailable", "debt_to_equity": None, "interest_coverage": None}
    status = "live" if (de is not None and ic is not None) else "partial"
    return {"status": status, "debt_to_equity": de, "interest_coverage": ic}


def get_earnings_date(symbol: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        return {"status": "unavailable", "reason": "yfinance not installed", "date": None}

    t = None
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

    if t is not None:
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


# ── Peer comparison (REAL data, curated sector map) ──────────────
_SECTOR_PEERS = {
    "RELIANCE":  ["RELIANCE", "ONGC", "IOC", "BPCL"],
    "HDFCBANK":  ["HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK"],
    "ICICIBANK": ["ICICIBANK", "HDFCBANK", "AXISBANK", "KOTAKBANK"],
    "AXISBANK":  ["AXISBANK", "HDFCBANK", "ICICIBANK", "KOTAKBANK"],
    "KOTAKBANK": ["KOTAKBANK", "HDFCBANK", "ICICIBANK", "AXISBANK"],
    "SBIN":      ["SBIN", "HDFCBANK", "ICICIBANK", "BANKBARODA"],
    "TCS":       ["TCS", "INFY", "WIPRO", "HCLTECH"],
    "INFY":      ["INFY", "TCS", "WIPRO", "HCLTECH"],
    "WIPRO":     ["WIPRO", "TCS", "INFY", "HCLTECH"],
    "HCLTECH":   ["HCLTECH", "TCS", "INFY", "WIPRO"],
    "TATAMOTORS":["TATAMOTORS", "M&M", "MARUTI", "BAJAJ-AUTO"],
    "MARUTI":    ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO"],
    "SUNPHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB"],
    "ITC":       ["ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA"],
    "HINDUNILVR":["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA"],
}


def get_peer_comparison(symbol: str) -> dict:
    sym = symbol.upper().strip()
    peer_list = _SECTOR_PEERS.get(sym)
    if not peer_list:
        return {
            "status": "unavailable",
            "reason": f"No curated peer list for {sym} yet — sector-peer mapping covers major banks, IT, auto, pharma, and FMCG names only.",
            "rows": [],
        }

    rows = []
    for peer_sym in peer_list:
        res = resolve_symbol(peer_sym)
        if not res:
            continue
        summary = get_summary(peer_sym, url=res["url"])
        sfields = summary.get("data") or {}
        if not sfields:
            continue
        rows.append({
            "symbol": peer_sym,
            "name": res["name"],
            "is_current": peer_sym == sym,
            "cmp": sfields.get("current_price", "—"),
            "pe": sfields.get("pe_ratio", "—"),
            "market_cap": sfields.get("market_cap", "—"),
            "div_yield": sfields.get("dividend_yield", "—"),
            "roce": sfields.get("roce", "—"),
        })

    if not rows:
        return {"status": "unavailable", "reason": "Curated peers found but none resolved on Screener right now.", "rows": []}
    return {"status": "live", "rows": rows}


def get_full_research(symbol: str) -> dict:
    """
    Fetches the Screener page ONCE and reuses that HTML for every
    section below, instead of letting each section function call
    _fetch_html() independently and rely on the short-lived cache to
    avoid re-requesting. This is both faster (one network round trip
    instead of up to six) and removes any dependency on cache timing.
    """
    res = resolve_symbol(symbol)
    if not res:
        return {"resolved": False, "symbol": symbol.upper(), "reason": "Could not find this symbol on Screener."}

    url = res["url"]
    html = _fetch_html(url)

    return {
        "resolved": True,
        "symbol": symbol.upper(),
        "name": res["name"],
        "url": url,
        "summary": get_summary(symbol, url=url, html_override=html),
        "quarterly": get_quarterly_results(symbol, url=url, html_override=html),
        "yearly": get_yearly_results(symbol, url=url, html_override=html),
        "shareholding": get_shareholding(symbol, url=url, html_override=html),
        "balance_sheet": get_balance_sheet(symbol, url=url, html_override=html),
        "sector": get_sector_info(symbol, url=url, html_override=html),
        "peers": get_peers(symbol, url=url),
    }
