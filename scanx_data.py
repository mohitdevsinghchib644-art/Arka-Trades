"""ScanX public company-page connector for Arka Trades.

Uses only the public ScanX company HTML. No login, private API, or access-control
bypass is used. The connector is deliberately defensive because ScanX company
slugs and page sections vary by security.
"""
from __future__ import annotations

from io import StringIO
import re
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

SCANX_BASE = "https://scanx.trade/company/"
SCANX_HOST = "https://scanx.trade"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

# ScanX uses company-name slugs rather than NSE symbols. These aliases cover
# the common cases where only the ticker is supplied. Unknown securities still
# go through the generic name/slug candidates below.
_SYMBOL_ALIASES = {
    "RELIANCE": "reliance-industries-ltd",
    "HDFCBANK": "hdfc-bank-ltd",
    "ICICIBANK": "icici-bank-ltd",
    "AXISBANK": "axis-bank-ltd",
    "SBIN": "state-bank-of-india-ltd",
    "INFY": "infosys-ltd",
    "TCS": "tcs-tata-consultancy-services-ltd",
    "ITC": "itc-ltd",
    "LT": "larsen-toubro-ltd",
    "BHARTIARTL": "bharti-airtel-ltd",
    "KOTAKBANK": "kotak-mahindra-bank-ltd",
    "MARUTI": "maruti-suzuki-india-ltd",
    "TATAMOTORS": "tata-motors-ltd",
    "TATASTEEL": "tata-steel-ltd",
    "SUNPHARMA": "sun-pharmaceutical-industries-ltd",
    "HINDUNILVR": "hindustan-unilever-ltd",
    "BAJFINANCE": "bajaj-finance-ltd",
    "ADANIENT": "adani-enterprises-ltd",
    "ADANIPORTS": "adani-ports-and-special-economic-zone-ltd",
    "WIPRO": "wipro-ltd",
    "TECHM": "tech-mahindra-ltd",
    "HCLTECH": "hcl-technologies-ltd",
}


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    # ScanX normally uses -ltd, not -limited.
    s = re.sub(r"-limited$", "-ltd", s)
    s = re.sub(r"-limited-", "-ltd-", s)
    return s


def _name_candidates(company_name: str | None) -> list[str]:
    if not company_name:
        return []
    raw = str(company_name).strip()
    s = _slugify(raw)
    out = []
    if s:
        out.append(s)
    # Also try dropping legal suffixes, and the common LTD spelling.
    base = re.sub(r"\s+(limited|ltd\.?|pvt\.?\s*ltd\.?)$", "", raw, flags=re.I).strip()
    bs = _slugify(base)
    if bs:
        out.extend([bs + "-ltd", bs])
    if s.endswith("-limited"):
        out.append(s[:-9] + "-ltd")
    return list(dict.fromkeys(out))


def company_url(symbol: str, company_name: str | None = None) -> str:
    candidates = _company_url_candidates(symbol, company_name)
    return candidates[0] if candidates else SCANX_BASE + _slugify(symbol)


def _company_url_candidates(symbol: str, company_name: str | None = None) -> list[str]:
    sym = str(symbol or "").strip().upper()
    slugs: list[str] = []
    if sym in _SYMBOL_ALIASES:
        slugs.append(_SYMBOL_ALIASES[sym])
    slugs.extend(_name_candidates(company_name))
    # Last-resort ticker-derived forms.
    raw_sym = _slugify(sym)
    if raw_sym:
        slugs.extend([raw_sym + "-ltd", raw_sym])
    return list(dict.fromkeys(SCANX_BASE + s for s in slugs if s))


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x.columns = [str(c).strip() for c in x.columns]
    x = x.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return x.reset_index(drop=True)


def _table_text(df: pd.DataFrame) -> str:
    vals = df.astype(str).head(8).values.flatten().tolist()
    return " ".join([str(c) for c in df.columns] + vals).lower()


def _classify_tables(tables: list[pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for raw in tables:
        df = _clean_frame(raw)
        if df.empty:
            continue
        text = _table_text(df)
        cols = " ".join(str(c).lower() for c in df.columns)

        # Order matters: financial/ownership tables should not be swallowed by
        # generic metric matches.
        if ("quarterly financials" in text or
                (all(k in text for k in ("revenue", "expenses", "ebitda")) and len(df.columns) >= 5)):
            out.setdefault("financials", df)
        elif "balance sheet" in text or all(k in text for k in ("total assets", "total equity")):
            out.setdefault("balance_sheet", df)
        elif "cash flow" in text or all(k in text for k in ("operating activities", "financing activities")):
            out.setdefault("cash_flow", df)
        elif ("% holding" in text or
              all(k in text for k in ("promoter", "fiis", "diis")) or
              all(k in text for k in ("promoters", "fii", "dii"))):
            out.setdefault("shareholding", df)
        elif "funding house" in text and "current holding" in text:
            out.setdefault("mf_holdings", df)
        elif "competitors" in text and "market cap" in text and ("p/e" in text or "pe ratio" in text):
            out.setdefault("peers", df)
        elif "record date" in text and ("corporate action" in text or "ltp at announcement" in text):
            out.setdefault("corporate_actions", df)
        elif "dividend per share" in text or ("dividend" in text and "record date" in text):
            out.setdefault("dividend", df)
        elif "analyst" in text and "buy" in text and "hold" in text:
            out.setdefault("analyst", df)
        elif "market cap" in cols and ("pe ratio" in cols or "p/e" in cols):
            out.setdefault("snapshot", df)
    return out


def _extract_metric(text: str, label: str) -> str | None:
    # ScanX renders these as adjacent text nodes. Keep the terminating labels
    # explicit so one metric cannot consume the next one.
    next_labels = (
        "Market Cap|PE Ratio|Volume|Day High - Low|52W High-Low|EPS|PB Ratio|"
        "Book Value|EBITDA|Dividend Yield|Industry|Sector|Return on Equity|Debt to Equity|Analyst Rating"
    )
    pat = rf"{re.escape(label)}\s*([₹\d,\.\-+%()A-Za-z ]{{1,60}}?)(?=\s+(?:{next_labels})\b|$)"
    m = re.search(pat, text, re.I)
    return m.group(1).strip() if m else None


def _extract_price(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else None
    strings = list(soup.stripped_strings)
    if name and name in strings:
        i = strings.index(name)
        tail = " ".join(strings[i + 1:i + 8])
        m = re.search(r"(\d[\d,]*\.\d+)\s+([+-]\d[\d,.]*?)\s*\(([-+]?\d[\d.]+%)\)", tail)
        if m:
            return name, m.group(1), f"{m.group(2)} ({m.group(3)})"
    text = " ".join(strings)
    if name:
        m = re.search(rf"{re.escape(name)}\s+(\d[\d,]*\.\d+)\s+([+-]\d[\d,.]*?)\s*\(([-+]?\d[\d.]+%)\)", text, re.I)
        if m:
            return name, m.group(1), f"{m.group(2)} ({m.group(3)})"
    return name, None, None


def _extract_snapshot(soup: BeautifulSoup) -> dict[str, str]:
    text = " ".join(soup.stripped_strings)
    result: dict[str, str] = {}
    for label in [
        "Market Cap", "PE Ratio", "Volume", "Day High - Low", "52W High-Low",
        "EPS", "PB Ratio", "Book Value", "EBITDA", "Dividend Yield",
        "Return on Equity", "Debt to Equity", "Industry", "Sector",
    ]:
        v = _extract_metric(text, label)
        if v:
            result[label] = v
    name, price, change = _extract_price(soup)
    if name:
        result["company_name"] = name
    if price:
        result["price"] = price
    if change:
        result["change"] = change
    return result


def _extract_about(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for h in soup.find_all(["h2", "h3"]):
        title = h.get_text(" ", strip=True)
        if title.lower().startswith("about "):
            parts = []
            for node in h.find_all_next():
                if node is not h and getattr(node, "name", None) in {"h2", "h3"}:
                    break
                if getattr(node, "name", None) in {"script", "style"}:
                    continue
                t = node.get_text(" ", strip=True)
                if t and t.lower() != title.lower():
                    parts.append(t)
            if parts:
                result["description"] = " ".join(dict.fromkeys(parts))[:5000]
            break
    return result


def _section_links(soup: BeautifulSoup, heading_text: str, limit: int = 100) -> list[dict[str, str]]:
    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(" ", strip=True).lower() == heading_text.lower():
            heading = h
            break
    if heading is None:
        return []
    out = []
    for node in heading.find_all_next():
        if getattr(node, "name", None) in {"h2", "h3"} and node is not heading:
            break
        if getattr(node, "name", None) != "a":
            continue
        title = node.get_text(" ", strip=True)
        href = node.get("href")
        if not title or not href:
            continue
        href = urljoin(SCANX_HOST, href)
        # Keep actual content links and skip navigation/watchlist boilerplate.
        low = href.lower()
        if not ("announcement" in low or "stock-market-news" in low or "news" in low or "bseindia" in low or "nseindia" in low):
            continue
        item = {"title": title, "url": href}
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _extract_announcements(soup: BeautifulSoup) -> list[dict[str, str]]:
    return _section_links(soup, "Announcements", 100)


def _extract_news(soup: BeautifulSoup) -> list[dict[str, str]]:
    return _section_links(soup, "Company News", 30)


def _extract_technical(soup: BeautifulSoup) -> list[dict[str, str]]:
    text = " ".join(soup.stripped_strings)
    names = ["RSI(14)", "ATR(14)", "STOCH(9,6)", "STOCH RSI(14)", "MACD(12,26)", "ADX(14)", "UO(9)", "ROC(12)", "WillR(14)"]
    result = []
    for name in names:
        pat = rf"{re.escape(name)}\s+(?:Image\s+)?(?:[A-Za-z ]+\s+)?(-?\d+(?:\.\d+)?)"
        m = re.search(pat, text, re.I)
        if not m:
            continue
        value = m.group(1)
        tail = text[m.start():m.start() + 100]
        state = ""
        for s in ["Bullish", "Bearish", "Neutral", "Weak Trend", "Less Volatile", "Volatile", "Overbought", "Oversold", "Uptrend And Accelerating", "Downtrend And Accelerating"]:
            if re.search(rf"\b{re.escape(s)}\b", tail, re.I):
                state = s
                break
        result.append({"indicator": name, "value": value, "state": state})
    return result


def _extract_analyst(soup: BeautifulSoup) -> pd.DataFrame:
    text = " ".join(soup.stripped_strings)
    m = re.search(r"Analyst Rating and Forecast.*?By Refinitiv from\s+(\d+)\s+analysts.*?Buy\s+Buy\s*([\d.]+)%.*?Hold\s+Hold\s*([\d.]+)%.*?Sell\s+Sell\s*([\d.]+)%", text, re.I)
    if not m:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Analysts": m.group(1),
        "Buy %": m.group(2),
        "Hold %": m.group(3),
        "Sell %": m.group(4),
    }])


def _df_records(df: pd.DataFrame, limit: int = 300) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    x = df.head(limit).copy()
    return x.to_dict(orient="records")


def _looks_like_scanx(soup: BeautifulSoup) -> bool:
    text = " ".join(soup.stripped_strings).lower()
    return bool(soup.find("h1")) and "key fundamentals" in text and "market cap" in text


def _request(url: str, timeout: int) -> requests.Response | None:
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://scanx.trade/",
        "Connection": "keep-alive",
    }
    try:
        s = requests.Session()
        r = s.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r
    except requests.RequestException:
        return None
    return None


def fetch_scanx_company(symbol: str, company_name: str | None = None, timeout: int = 20) -> dict[str, Any]:
    """Fetch and normalize a public ScanX company page.

    The old connector failed for ticker-only input such as RELIANCE because it
    constructed /company/reliance-ltd. ScanX actually uses name-based slugs
    such as /company/reliance-industries-ltd. This version tries the proper
    aliases/name variants and validates that the response is a real company page.
    """
    sym = str(symbol or "").strip().upper()
    candidates = _company_url_candidates(sym, company_name)
    last_url = candidates[0] if candidates else SCANX_BASE + _slugify(sym)
    resp = None

    for url in candidates:
        last_url = url
        r = _request(url, timeout)
        if r is None:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        if _looks_like_scanx(soup):
            resp = r
            break

    if resp is None:
        return {
            "status": "unavailable",
            "symbol": sym,
            "url": last_url,
            "reason": "ScanX company page could not be fetched or the returned page was not a valid public company page.",
        }

    html_text = resp.text
    soup = BeautifulSoup(html_text, "html.parser")
    try:
        tables = pd.read_html(StringIO(html_text))
    except (ValueError, ImportError):
        tables = []

    classified = _classify_tables(tables)
    snapshot = _extract_snapshot(soup)
    about = _extract_about(soup)
    announcements = _extract_announcements(soup)
    news = _extract_news(soup)
    technical = _extract_technical(soup)
    analyst = _extract_analyst(soup)
    if not analyst.empty:
        classified["analyst"] = analyst

    title = soup.find("h1")
    name = title.get_text(" ", strip=True) if title else snapshot.get("company_name") or company_name or sym

    return {
        "status": "live",
        "symbol": sym,
        "name": name,
        "url": resp.url,
        "snapshot": snapshot,
        "about": about,
        "announcements": announcements,
        "news": news,
        "technical": technical,
        "tables": classified,
        "table_records": {k: _df_records(v) for k, v in classified.items()},
    }
