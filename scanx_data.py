"""ScanX public-company-page data adapter for Arka Trades.

This module reads the public HTML of a ScanX company page. It does not log in,
reverse-engineer private APIs, or bypass access controls. ScanX is treated as a
source layer; Arka's UI remains independent of ScanX's presentation.
"""
from __future__ import annotations

from io import StringIO
import re
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

SCANX_BASE = "https://scanx.trade/company/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0 Safari/537.36 ArkaTrades/1.0"
)


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s


def company_url(symbol: str, company_name: str | None = None) -> str:
    """Build the normal ScanX public company URL.

    Prefer the supplied company name because ScanX slugs company names rather
    than NSE symbols. Examples: Reliance Industries -> reliance-industries-ltd.
    """
    name = str(company_name or symbol).strip()
    slug = _slugify(name)
    if slug and not slug.endswith("-ltd") and not slug.endswith("-limited"):
        slug += "-ltd"
    return SCANX_BASE + slug


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x.columns = [str(c).strip() for c in x.columns]
    x = x.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return x.reset_index(drop=True)


def _table_text(df: pd.DataFrame) -> str:
    return " ".join([str(c) for c in df.columns] + [str(v) for v in df.astype(str).head(3).values.flatten()])


def _classify_tables(tables: list[pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for raw in tables:
        df = _clean_frame(raw)
        if df.empty:
            continue
        text = _table_text(df).lower()
        cols = " ".join(str(c).lower() for c in df.columns)
        if "quarterly financials" in text or ("revenue" in text and "ebitda" in text and len(df.columns) > 8):
            out.setdefault("financials", df)
        elif "balance sheet" in text or ("total assets" in text and "total equity" in text):
            out.setdefault("balance_sheet", df)
        elif "cash flow" in text or ("operating activities" in text and "financing activities" in text):
            out.setdefault("cash_flow", df)
        elif "% holding" in text or ("promoter" in text and "fiis" in text and "diis" in text):
            out.setdefault("shareholding", df)
        elif "annual cash flows" in text or ("dividend per share" in text and "dividend yield" in text):
            out.setdefault("dividend", df)
        elif "record date" in text and "corporate action" in text:
            out.setdefault("corporate_actions", df)
        elif "funding house" in text and "current holding" in text:
            out.setdefault("mf_holdings", df)
        elif "competitors" in text and "market cap" in text and "p/e" in text:
            out.setdefault("peers", df)
        elif "analyst" in text and "buy" in text and "hold" in text:
            out.setdefault("analyst", df)
        elif "market cap" in cols and "pe ratio" in cols and "volume" in cols:
            out.setdefault("snapshot", df)
    return out


def _extract_metric(text: str, label: str) -> str | None:
    # Handles the common ScanX text form: "Market Cap 17,96,173.86 Cr".
    pat = rf"{re.escape(label)}\s+([₹\d,\.\-+%()A-Za-z ]{{1,40}}?)(?=\s+(?:Market Cap|PE Ratio|Volume|Day High|52W High|EPS|PB Ratio|Book Value|EBITDA|Dividend Yield|Industry|Sector|Return on Equity|Debt to Equity)\b|$)"
    m = re.search(pat, text, re.I)
    return m.group(1).strip() if m else None


def _extract_snapshot(soup: BeautifulSoup) -> dict[str, str]:
    text = " ".join(soup.stripped_strings)
    labels = [
        "Market Cap", "PE Ratio", "Volume", "Day High - Low", "52W High-Low",
        "EPS", "PB Ratio", "Book Value", "EBITDA", "Dividend Yield",
        "Return on Equity", "Debt to Equity",
    ]
    result: dict[str, str] = {}
    for label in labels:
        v = _extract_metric(text, label)
        if v:
            result[label] = v
    # The first price block is easier to recover with a focused regex.
    m = re.search(r"#\s*([^\n]+?)\s+(\d[\d,]*\.\d+)\s+([+-]\d[\d,.]*\s*\([^)]+\))", text)
    if m:
        result["company_name"] = m.group(1).strip()
        result["price"] = m.group(2).strip()
        result["change"] = m.group(3).strip()
    return result


def _extract_about(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for h in soup.find_all(["h2", "h3"]):
        title = h.get_text(" ", strip=True).lower()
        if title.startswith("about "):
            parts = []
            for node in h.find_all_next(limit=10):
                if getattr(node, "name", None) in {"h2", "h3"} and node is not h:
                    break
                t = node.get_text(" ", strip=True)
                if t and t.lower() != title:
                    parts.append(t)
            if parts:
                result["description"] = " ".join(dict.fromkeys(parts))[:4000]
            break
    return result


def _extract_announcements(soup: BeautifulSoup) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(" ", strip=True).lower() == "announcements":
            heading = h
            break
    if heading is None:
        return result
    for node in heading.find_all_next():
        if getattr(node, "name", None) in {"h2", "h3"} and node is not heading:
            break
        if getattr(node, "name", None) != "a":
            continue
        title = node.get_text(" ", strip=True)
        href = node.get("href")
        if not title or not href:
            continue
        if href.startswith("/"):
            href = "https://scanx.trade" + href
        # Avoid nav/footer links that sometimes occur inside the section.
        if "bseindia.com" not in href.lower() and "nseindia.com" not in href.lower():
            continue
        result.append({"title": title, "url": href})
        if len(result) >= 60:
            break
    return result


def _extract_technical(soup: BeautifulSoup) -> list[dict[str, str]]:
    text = " ".join(soup.stripped_strings)
    names = ["RSI(14)", "ATR(14)", "STOCH(9,6)", "STOCH RSI(14)", "MACD(12,26)", "ADX(14)", "UO(9)", "ROC(12)", "WillR(14)"]
    result=[]
    for name in names:
        # ScanX presents indicator name -> label -> numeric value.
        pat = rf"{re.escape(name)}\s+(?:[A-Za-z ]+\s+)?(-?\d+(?:\.\d+)?)"
        m = re.search(pat, text, re.I)
        if m:
            value=m.group(1)
            start=m.start(); tail=text[start:start+120]
            state=None
            for s in ["Bullish","Bearish","Neutral","Weak Trend","Less Volatile","Overbought","Oversold","Uptrend And Accelerating"]:
                if re.search(rf"\b{re.escape(s)}\b", tail, re.I):
                    state=s; break
            result.append({"indicator":name,"value":value,"state":state or ""})
    return result


def _df_records(df: pd.DataFrame, limit: int = 200) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    x=df.head(limit).copy()
    return x.to_dict(orient="records")


def fetch_scanx_company(symbol: str, company_name: str | None = None, timeout: int = 15) -> dict[str, Any]:
    """Fetch and normalize a public ScanX company page."""
    url = company_url(symbol, company_name)
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            allow_redirects=True,
        )
    except Exception as exc:
        return {"status":"unavailable","symbol":symbol.upper(),"url":url,"reason":str(exc)}

    if resp.status_code != 200:
        return {"status":"unavailable","symbol":symbol.upper(),"url":resp.url,"reason":f"ScanX HTTP {resp.status_code}"}

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    try:
        tables = pd.read_html(StringIO(html))
    except Exception:
        tables = []
    classified = _classify_tables(tables)
    snapshot = _extract_snapshot(soup)
    about = _extract_about(soup)
    announcements = _extract_announcements(soup)
    technical = _extract_technical(soup)

    # Name extraction from the page is more reliable than slug reconstruction.
    title = soup.find("h1")
    name = title.get_text(" ", strip=True) if title else snapshot.get("company_name") or company_name or symbol.upper()

    return {
        "status":"live",
        "symbol":symbol.upper(),
        "name":name,
        "url":resp.url,
        "snapshot":snapshot,
        "about":about,
        "announcements":announcements,
        "technical":technical,
        "tables":classified,
        "table_records":{k:_df_records(v) for k,v in classified.items()},
    }
