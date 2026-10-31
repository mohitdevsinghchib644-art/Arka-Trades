"""
news_feed.py — Arka News Desk v10

A Bloomberg-style news layer for the terminal: one shared wire, entity tagging, scoring and a full NEWS workspace.

What changed vs v9.2
--------------------
MORE AND BETTER SOURCES
  * A real wire: Economic Times, Business Standard, Mint, Moneycontrol, BusinessLine and NDTV Profit RSS feeds,
    plus Google News for macro topics and for each of your stocks, plus BSE corporate filings (exchange
    announcements, with the PDF link). v9 used Google News only.
  * Every source has a live health status (OK / FAILED / items), shown on the NEWS page — no silent gaps.

SMARTER
  * Entity tagging: every headline is scanned for your watchlist / portfolio stocks (tickers + company aliases such
    as "RIL", "L&T", "Tata Consultancy"), sectors (banks, IT, auto, pharma, metals …) and macro themes (RBI, Fed,
    crude, rupee, FII …). A market-wide wire story that names one of your stocks now lands in MY STOCKS.
  * Sentiment is a weighted score (−3 … +3) with negation handling ("not upgraded", "fails to beat"), not a
    first-keyword match. Rupee amounts are extracted ("₹1,200 crore order" → ₹1,200 Cr) and raise importance.
  * Stories are clustered across sources; source count, HOT (3+ outlets inside 3 h) and BREAKING flags.
  * NEWS PULSE: per-stock story count, net tone and latest headline. SECTOR TONE and a market NEWS TONE index.

FASTER
  * Fetches are shared by every user (st.cache_data) instead of per-session caches; one assembled feed is memoised
    for 5 minutes; slow sources are skipped after a hard 9-second budget instead of blocking the page.
  * The rail uses a selector instead of st.tabs (tabs built all four lists — with a price download per story —
    on every click). Price-reaction badges are computed only for the stock you are viewing.

NEW
  * render_news_terminal(): a full NEWS workspace (wire, breaking tape, pulse, sector tone, source health).

Public API kept: render_news_rail, news_panel, news_box, refresh_news, get_news_dot, get_security_news,
_fetch_news_for_stock, _ensure_news_state.  Headlines are third-party content; tone tags are automatic and
can be wrong — not investment advice.
"""
from __future__ import annotations

import hashlib
import html
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import quote_plus, urlparse

import feedparser
import pandas as pd
import requests
import streamlit as st

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

IST = timezone(timedelta(hours=5, minutes=30))

# ── palette (app v7) ──
AMBER, GREEN, RED, CYAN, PURPLE, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
TX, TX2, TX3, LINE, LINE2, S1, S2 = "#ECECEC", "#A3A3A3", "#6B6B6B", "#1F1F1F", "#2B2B2B", "#0A0A0A", "#0F0F0F"
MONO = "'JetBrains Mono','Consolas',monospace"
# legacy names some modules import
GOLD, DARK, DARK2, DARK3, BORDER, T2, T3, IVORY, BLUE = AMBER, "#000000", "#090909", "#111111", "#242424", "#8E8E8E", "#5C5C5C", "#E8E8E8", CYAN

FEED_TTL = 300              # seconds a source is reused before it is fetched again
ASSEMBLE_TTL = 300          # assembled feed memo
FETCH_BUDGET_S = 9.0        # hard wall-clock budget per assembly; slow sources are picked up on the next run
HTTP_TIMEOUT = 7
MAX_COMPANY_FETCH = 10      # stocks queried on Google News per cycle (pinned stock + rotation)
MAX_ITEMS_PER_FEED = 40
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# ═════════════════════════ sources ═════════════════════════
WIRE_SOURCES = [
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Business Standard", "https://www.business-standard.com/rss/companies-101.rss"),
    ("Mint", "https://www.livemint.com/rss/markets"),
    ("Mint", "https://www.livemint.com/rss/companies"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
    ("BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    ("NDTV Profit", "https://feeds.feedburner.com/ndtvprofit-latest"),
]
MACRO_QUERIES = ["RBI monetary policy repo rate", "Nifty Sensex stock market today", "FII DII flows India equities",
                 "US Federal Reserve rates markets", "crude oil prices India", "rupee dollar forex India",
                 "India inflation GDP economy", "global markets Wall Street Asia"]

# ═════════════════════════ taxonomy ═════════════════════════
ALIASES = {
    "RELIANCE": ["reliance industries", "ril", "reliance jio", "jio platforms", "reliance retail"],
    "TCS": ["tata consultancy"], "INFY": ["infosys"], "HDFCBANK": ["hdfc bank"], "ICICIBANK": ["icici bank"],
    "SBIN": ["state bank of india", "sbi"], "AXISBANK": ["axis bank"], "KOTAKBANK": ["kotak mahindra bank", "kotak bank"],
    "BAJFINANCE": ["bajaj finance"], "BAJAJFINSV": ["bajaj finserv"], "LT": ["larsen & toubro", "larsen and toubro", "l&t"],
    "HINDUNILVR": ["hindustan unilever", "hul"], "BHARTIARTL": ["bharti airtel", "airtel"], "WIPRO": ["wipro"],
    "HCLTECH": ["hcltech", "hcl tech", "hcl technologies"], "TECHM": ["tech mahindra"], "LTIM": ["ltimindtree"],
    "MARUTI": ["maruti suzuki", "maruti"], "TATAMOTORS": ["tata motors", "jaguar land rover", "jlr"],
    "M&M": ["mahindra & mahindra", "mahindra and mahindra", "m&m"], "BAJAJ-AUTO": ["bajaj auto"],
    "HEROMOTOCO": ["hero motocorp"], "EICHERMOT": ["eicher motors", "royal enfield"], "SUNPHARMA": ["sun pharma"],
    "DRREDDY": ["dr reddy", "dr. reddy"], "CIPLA": ["cipla"], "DIVISLAB": ["divi's lab", "divis lab"],
    "APOLLOHOSP": ["apollo hospitals"], "TATASTEEL": ["tata steel"], "JSWSTEEL": ["jsw steel"], "HINDALCO": ["hindalco"],
    "VEDL": ["vedanta"], "COALINDIA": ["coal india"], "ONGC": ["oil and natural gas", "ongc"], "NTPC": ["ntpc"],
    "POWERGRID": ["power grid"], "TATAPOWER": ["tata power"], "IOC": ["indian oil"], "BPCL": ["bharat petroleum", "bpcl"],
    "ADANIENT": ["adani enterprises"], "ADANIPORTS": ["adani ports"], "ADANIGREEN": ["adani green"],
    "ASIANPAINT": ["asian paints"], "TITAN": ["titan company"], "NESTLEIND": ["nestle india"], "BRITANNIA": ["britannia"],
    "ULTRACEMCO": ["ultratech"], "GRASIM": ["grasim"], "HAL": ["hindustan aeronautics"], "BEL": ["bharat electronics"],
    "BHEL": ["bharat heavy electricals", "bhel"], "IRFC": ["indian railway finance", "irfc"], "RVNL": ["rail vikas nigam", "rvnl"],
    "ETERNAL": ["zomato", "eternal ltd", "blinkit"], "ZOMATO": ["zomato"], "PAYTM": ["paytm", "one 97"], "NYKAA": ["nykaa", "fsn e-commerce"],
    "DMART": ["avenue supermarts", "dmart", "d-mart"], "TRENT": ["trent ltd", "zudio"], "DIXON": ["dixon technologies"],
    "PERSISTENT": ["persistent systems"], "HDFCLIFE": ["hdfc life"], "SBILIFE": ["sbi life"], "ICICIPRULI": ["icici prudential life"],
    "INDUSINDBK": ["indusind bank"], "BANKBARODA": ["bank of baroda"], "PNB": ["punjab national bank"], "CANBK": ["canara bank"],
    "IDEA": ["vodafone idea"], "YESBANK": ["yes bank"], "DLF": ["dlf"], "LICI": ["life insurance corporation", "lic"],
    "ITC": ["itc ltd", "itc hotels"], "JIOFIN": ["jio financial"], "SHRIRAMFIN": ["shriram finance"], "CHOLAFIN": ["cholamandalam"],
}
SECTORS = {
    "BANKS & FIN": r"banks?|banking|lenders?|nbfcs?|insurers?|insurance|fintech|microfinance|credit growth|deposits?",
    "IT": r"it services|it stocks|it sector|software|tech services|it majors?|outsourcing",
    "AUTO": r"autos?|automakers?|car sales|two-wheelers?|passenger vehicles?|ev makers?|electric vehicles?|tractors?",
    "PHARMA": r"pharma|drugmakers?|usfda|drug|hospitals?|healthcare|api makers?",
    "METALS": r"steel|metals?|aluminium|copper|mining|iron ore|zinc",
    "ENERGY": r"crude|oil marketing|refiners?|opec|petrol|diesel|lng|natural gas|city gas",
    "FMCG": r"fmcg|consumer goods|staples|rural demand",
    "REALTY": r"real estate|realty|housing sales|property market",
    "POWER": r"power demand|electricity|renewables?|solar|discoms?|power sector|power stocks",
    "CAP GOODS": r"defence|defense|capital goods|railways?|engineering|order book",
    "TELECOM": r"telecom|5g|spectrum|arpu",
    "CEMENT": r"cement",
    "CHEMICALS": r"chemicals?|agrochemicals?|fertilisers?|fertilizers?",
}
MACRO_TAGS = {
    "RBI": r"rbi|repo rate|monetary policy|mpc", "FED": r"fed|federal reserve|fomc|powell", "INFLATION": r"inflation|cpi|wpi",
    "CRUDE": r"crude|brent|opec", "RUPEE": r"rupee|forex|inr", "FII": r"fiis?|fpis?|foreign investors|dii", "GDP": r"gdp|growth forecast",
    "TRADE": r"tariffs?|trade deal|trade war|exports?|imports?", "BUDGET": r"budget|fiscal deficit|gst", "GLOBAL": r"wall street|nasdaq|dow|s&p 500|china|nikkei",
}
EVENTS = [  # checked in order
    ("REGULATORY", r"sebi|penalty|probe|raid|show[- ]cause|banned|ban on|regulatory|compliance|enforcement directorate|\bed\b|cbi|court|tribunal|nclt"),
    ("FILING", r"regulation 30|intimation|disclosure under"),
    ("ORDER", r"orders?|contracts?|letter of award|bags|wins .*?(deal|order|contract)|work order|loa"),
    ("STAKE", r"stake|acqui(re|res|red|sition)|merger|demerger|block deal|bulk deal|open offer|takeover"),
    ("PROMOTER", r"promoters?|pledge[ds]?|insider"),
    ("EARNINGS", r"results?|earnings|q[1-4]\b|quarterly|net profit|revenue|ebitda|margins?|guidance|profit (rises|falls|jumps|drops)"),
    ("CORP ACTION", r"dividend|record date|bonus|stock split|split|buyback|rights issue|qip|fund raise|ncd"),
    ("IPO", r"\bipo\b|listing|lists at|grey market|gmp|subscribed"),
    ("RATING", r"upgrades?|downgrades?|target price|price target|rating|outperform|underperform|brokerage|initiates coverage|buy call|sell call"),
    ("CAPEX", r"capex|expansion|new plant|capacity|commission(s|ed|ing)|greenfield|brownfield"),
    ("MANAGEMENT", r"\bceo\b|\bcfo\b|\bmd\b|chairman|appoints?|resigns?|steps down|board meeting"),
]
EVENT_COLORS = {"REGULATORY": RED, "FILING": CYAN, "ORDER": GREEN, "STAKE": AMBER, "PROMOTER": AMBER, "EARNINGS": PURPLE,
                "CORP ACTION": GREEN, "IPO": YELLOW, "RATING": CYAN, "CAPEX": CYAN, "MANAGEMENT": YELLOW, "MACRO": AMBER, "NEWS": TX3}
EVENT_WEIGHT = {"REGULATORY": 3, "ORDER": 3, "STAKE": 3, "PROMOTER": 2, "EARNINGS": 3, "CORP ACTION": 2, "FILING": 2,
                "IPO": 1, "RATING": 2, "CAPEX": 2, "MANAGEMENT": 2, "MACRO": 1, "NEWS": 0}
SENT = [  # (pattern, weight); longer phrases first
    (r"record (high|profit|revenue|sales)|all[- ]time high|52[- ]week high|upper circuit|multibagger", 2),
    (r"beats? (estimates?|expectations?|street)|raises? (guidance|outlook|target)|target raised", 2),
    (r"(wins?|bags?|secures?) (an? )?(\w+ )?(order|contract|deal)", 2),
    (r"surges?|soars?|skyrockets?|zooms?|rall(y|ies)|profit (jumps|surges|soars|doubles)|upgraded?", 2),
    (r"rises?|gains?|climbs?|jumps?|higher|growth|expands?|launch(es)?|partnership|approv(al|es|ed)|recover(s|y)|rebounds?|"
     r"outperforms?|buy rating|strong demand|improves?|robust|tailwinds?", 1),
    (r"crash(es)?|plung(e|es)|plummets?|collapses?|fraud|scam|defaults?|bankrupt(cy)?|insolvency|raid|scandal|"
     r"lower circuit|52[- ]week low|profit warning|loss widens|misses (estimates?|expectations?)|downgraded?|resigns?", -2),
    (r"slumps?|tumbles?|sinks?|penalty|banned|suspended|probe|sell[- ]?off", -2),
    (r"falls?|declines?|drops?|dips?|slips?|slides?|weak(ens|er)?|concerns?|delay(ed|s)?|misses|lower|cuts?|sell rating|"
     r"target cut|pressure|outflows?|headwinds?|losses|slowdown", -1),
]
NEGATION = re.compile(r"\b(not|no|never|fails? to|unlikely to|without)\b[\w\s,'-]{0,18}$")
AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s?([\d,]+(?:\.\d+)?)\s?(lakh crore|lakh cr|crore|cr\b|billion|bn\b|million|mn\b)", re.I)
BORING_FILINGS = re.compile(r"trading window|74\s?\(5\)|loss of share|duplicate share|newspaper (publication|advertisement)|"
                            r"compliance certificate|closure of trading|investor (meet|presentation) intimation|book closure|"
                            r"change in (address|registrar)|copy of newspaper", re.I)

_SENT_RX = [(re.compile(rf"(?<![a-z0-9])(?:{p})(?![a-z0-9])"), w) for p, w in SENT]
_EVENT_RX = [(e, re.compile(rf"(?<![a-z0-9])(?:{p})", re.I)) for e, p in EVENTS]
_SECTOR_RX = {k: re.compile(rf"(?<![a-z0-9])(?:{p})(?![a-z0-9])") for k, p in SECTORS.items()}
_MACRO_RX = {k: re.compile(rf"(?<![a-z0-9])(?:{p})(?![a-z0-9])") for k, p in MACRO_TAGS.items()}

# ═════════════════════════ fetching (shared cache + health) ═════════════════════════
_LOCK = threading.Lock()
_HEALTH: dict = {}          # url -> {"name", "ok", "n", "t", "err"}
_LAST_GOOD: dict = {}
_COOL: dict = {}
_MEMO: dict = {}


def _bucket(ttl=FEED_TTL) -> int:
    return int(time.time() // ttl)


def _now() -> datetime:
    return datetime.now(IST)


def _parse_dt(raw, parsed=None):
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(IST)
        except Exception:
            pass
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(IST)
        except Exception:
            pass
    return None


def _clean_html(s: str, n=260) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    return s[:n] + ("…" if len(s) > n else "")


@st.cache_data(ttl=FEED_TTL * 2, show_spinner=False, max_entries=256)
def _rss_cached(url: str, bucket: int) -> list:
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    rows = []
    for e in feed.entries[:MAX_ITEMS_PER_FEED]:
        src = e.get("source", {}) or {}
        rows.append({"title": e.get("title", ""), "link": e.get("link", ""), "summary": _clean_html(e.get("summary", "")),
                     "dt": _parse_dt(e.get("published") or e.get("updated"), e.get("published_parsed") or e.get("updated_parsed")),
                     "src": src.get("title") if isinstance(src, dict) else None})
    if not rows:
        raise RuntimeError("empty feed")
    return rows


def _fetch(url: str, name: str, fn=None) -> list:
    """Cached fetch with health tracking, last-good fallback and a 3-minute cool-down after failures."""
    now = time.time()
    if _COOL.get(url, 0) > now:
        return _LAST_GOOD.get(url, [])
    try:
        rows = (fn or (lambda: _rss_cached(url, _bucket())))()
        with _LOCK:
            _LAST_GOOD[url] = rows
            _HEALTH[url] = {"name": name, "ok": True, "n": len(rows), "t": now, "err": ""}
        return rows
    except Exception as e:
        with _LOCK:
            _COOL[url] = now + 180
            _HEALTH[url] = {"name": name, "ok": False, "n": len(_LAST_GOOD.get(url, [])), "t": now, "err": type(e).__name__}
        return _LAST_GOOD.get(url, [])


def _gnews_url(query: str, days: int) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(f'{query} when:{max(1, int(days))}d')}&hl=en-IN&gl=IN&ceid=IN:en"


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def _bse_cached(d_from: str, d_to: str, bucket: int) -> list:
    """BSE corporate announcements (exchange filings), market-wide, newest first."""
    r = requests.get("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
                     params={"pageno": 1, "strCat": -1, "strPrevDate": d_from, "strScrip": "", "strSearch": "P",
                             "strToDate": d_to, "strType": "C", "subcategory": -1},
                     headers={"User-Agent": UA, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
                              "Accept": "application/json, text/plain, */*"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    rows = (r.json() or {}).get("Table") or []
    out = []
    for x in rows[:200]:
        name = str(x.get("SLONGNAME") or "").strip()
        head = str(x.get("HEADLINE") or x.get("NEWSSUB") or "").strip()
        if not name or not head or BORING_FILINGS.search(head) or BORING_FILINGS.search(str(x.get("SUBCATNAME") or "")):
            continue
        raw_dt = str(x.get("DT_TM") or x.get("NEWS_DT") or "")
        try:
            dt = pd.Timestamp(raw_dt).to_pydatetime()
            dt = dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
        except Exception:
            continue
        att = str(x.get("ATTACHMENTNAME") or "").strip()
        link = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else "https://www.bseindia.com/corporates/ann.html"
        out.append({"title": f"{name.title()}: {head}"[:240], "link": link, "summary": str(x.get("CATEGORYNAME") or x.get("SUBCATNAME") or ""),
                    "dt": dt, "src": "BSE filing", "company": name})
    if not out:
        raise RuntimeError("no filings")
    return out


# ═════════════════════════ scoring / tagging ═════════════════════════
def _sentiment(text: str) -> int:
    t = (text or "").lower()
    score = 0
    taken = []
    for rx, w in _SENT_RX:
        for m in rx.finditer(t):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            score += -w if NEGATION.search(t[max(0, m.start() - 26):m.start()]) else w
    return max(-3, min(3, score))


def _sent_key(score: int) -> str:
    return "strong_positive" if score >= 2 else "mild_positive" if score == 1 else "strong_negative" if score <= -2 else "mild_negative" if score == -1 else "neutral"


_SENT_LABEL = {"strong_positive": "POS", "mild_positive": "pos", "neutral": "—", "mild_negative": "neg", "strong_negative": "NEG"}
_SENT_COLOR = {"strong_positive": GREEN, "mild_positive": "#6FD98F", "neutral": TX3, "mild_negative": "#FF8A80", "strong_negative": RED}


def _event(text: str, macro: bool) -> str:
    for name, rx in _EVENT_RX:
        if rx.search(text or ""):
            return name
    return "MACRO" if macro else "NEWS"


def _amount_cr(text: str):
    m = AMOUNT.search(text or "")
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    u = m.group(2).lower().replace(" ", "")
    mult = {"lakhcrore": 1e5, "lakhcr": 1e5, "crore": 1, "cr": 1, "billion": 100, "bn": 100, "million": 0.1, "mn": 0.1}.get(u, 1)
    return v * mult


@lru_cache(maxsize=64)
def _tagger(symbols: tuple, names: tuple):
    """Compiled matchers for the user's universe: ticker (case-sensitive) + aliases / company names (case-insensitive)."""
    out = []
    name_map = dict(names)
    for sym in symbols:
        pats = [a for a in ALIASES.get(sym, [])]
        nm = name_map.get(sym)
        if nm:
            words = [w for w in re.sub(r"[^A-Za-z0-9& ]", " ", nm).split() if w.lower() not in {"ltd", "limited", "the", "india", "co", "corporation"}]
            if words and (len(words) > 1 or len(words[0]) >= 5):
                pats.append(" ".join(words[:2]).lower())
        alias_rx = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(p) for p in pats) + r")(?![a-z0-9])") if pats else None
        tick_rx = re.compile(rf"(?<![A-Za-z0-9]){re.escape(sym)}(?![A-Za-z0-9])") if len(sym) >= 3 else None
        out.append((sym, tick_rx, alias_rx))
    return out


def _tag(title: str, summary: str, tagger) -> list:
    low = f"{title} {summary}".lower()
    found = []
    for sym, tick_rx, alias_rx in tagger:
        if (tick_rx and tick_rx.search(title)) or (alias_rx and alias_rx.search(low)):
            found.append(sym)
    return found


def _norm_title(t: str) -> str:
    t = re.sub(r"\[[^\]]+\]|\b(live updates?|live blog|breaking|watch|explained)\b", " ", (t or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


def _similar(a: str, b: str) -> bool:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return False
    return len(sa & sb) / max(1, min(len(sa), len(sb))) >= 0.72 and len(sa & sb) >= 4


def _make(row: dict, kind: str, source: str, symbol: str | None, tagger) -> dict | None:
    dt = row.get("dt")
    title = (row.get("title") or "").strip()
    if not dt or not title:
        return None
    src = row.get("src") or source
    if kind in ("company", "macro"):            # Google News appends " - Publisher"
        parts = re.split(r"\s+-\s+", title)
        if len(parts) >= 2 and len(parts[-1]) <= 60:
            src, title = row.get("src") or parts[-1].strip(), " - ".join(parts[:-1]).strip()
    summary = row.get("summary") or ""
    text = f"{title}. {summary}"
    low = text.lower()
    score = _sentiment(title) if kind == "filing" else _sentiment(title) * 2 + _sentiment(summary)
    score = max(-3, min(3, round(score / (1 if kind == "filing" else 2)))) if kind != "filing" else score
    syms = _tag(title, summary if kind != "company" else "", tagger)
    if symbol and symbol not in syms and kind == "company":
        syms.insert(0, symbol)
    sectors = [k for k, rx in _SECTOR_RX.items() if rx.search(low)]
    macro = [k for k, rx in _MACRO_RX.items() if rx.search(low)]
    event = _event(title if kind != "filing" else f"{title} {summary}", macro=kind == "macro" or (bool(macro) and not syms))
    amt = _amount_cr(text)
    link = row.get("link") or ""
    return {"id": hashlib.md5((link or title).encode()).hexdigest()[:12], "title": title, "link": link, "url": link,
            "source": src, "sources": [src], "source_count": 1, "pub_dt": dt, "kind": kind, "summary": summary,
            "symbols": syms, "symbol": syms[0] if syms else ("MACRO" if kind == "macro" or macro else "MARKET"),
            "sectors": sectors, "macro": macro, "event": event, "score": score, "sentiment": _sent_key(score),
            "sentiment_label": _SENT_LABEL[_sent_key(score)], "amount_cr": amt, "company": row.get("company")}


def _cluster(items: list) -> list:
    items = sorted(items, key=lambda a: a["pub_dt"], reverse=True)
    out, keys = [], []
    by_link = {}
    for a in items:
        if a["link"] and a["link"] in by_link:
            continue
        k = _norm_title(a["title"])
        hit = next((i for i, kk in enumerate(keys[-150:], start=max(0, len(keys) - 150)) if _similar(k, kk)), None)
        if hit is None:
            keys.append(k)
            out.append(dict(a))
            by_link[a["link"]] = len(out) - 1
        else:
            lead = out[hit]
            lead["sources"] = list(dict.fromkeys(lead["sources"] + a["sources"]))[:8]
            lead["source_count"] = len(lead["sources"])
            lead["symbols"] = list(dict.fromkeys(lead["symbols"] + a["symbols"]))
            lead["sectors"] = list(dict.fromkeys(lead["sectors"] + a["sectors"]))
            lead["first_dt"] = min(lead.get("first_dt", lead["pub_dt"]), a["pub_dt"])
            if lead["symbol"] in ("MARKET", "MACRO") and a["symbols"]:
                lead["symbol"] = a["symbols"][0]
    return out


def _score(items: list, mine: set, current: str) -> list:
    now = _now()
    for a in items:
        age = max(0.0, (now - a["pub_dt"]).total_seconds() / 60)
        imp = 1 + EVENT_WEIGHT.get(a["event"], 0) // 1
        imp = min(5, max(1, (1 if a["event"] == "NEWS" else 2 if EVENT_WEIGHT.get(a["event"], 0) <= 1 else 3)
                         + (1 if abs(a["score"]) >= 2 else 0) + (1 if (a["amount_cr"] or 0) >= 1000 else 0)
                         + (1 if a["source_count"] >= 3 else 0)))
        tagged = set(a["symbols"])
        a["importance"] = imp
        a["mine"] = bool(tagged & mine)
        a["priority"] = int(min(100, imp * 12 + max(0, 30 - age / 15) + min(12, (a["source_count"] - 1) * 4)
                                + (15 if a["mine"] else 0) + (15 if current and current in tagged else 0)))
        a["is_breaking"] = age <= 60 and (imp >= 4 or a["source_count"] >= 2)
        a["hot"] = a["source_count"] >= 3 and age <= 180
        a["age_min"] = age
        a["time_str"] = _ago(a["pub_dt"])
        a["event_reason"] = a["event"].title()
    return items


def _ago(dt) -> str:
    s = int((_now() - dt).total_seconds())
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return dt.strftime("%d %b")


# ═════════════════════════ assembly ═════════════════════════
def _rotation(symbols: list, current: str, n: int) -> list:
    rest = [s for s in symbols if s != current]
    pinned = [current] if current else []
    slots = max(0, n - len(pinned))
    if len(rest) <= slots:
        return pinned + rest
    k = -(-len(rest) // slots)
    off = (_bucket() % k) * slots
    return pinned + (rest[off:off + slots] + rest[:max(0, slots - len(rest[off:off + slots]))])


def _company_query(sym: str, name: str | None) -> str:
    if name:
        words = [w for w in re.sub(r"[^A-Za-z0-9& ]", " ", name).split() if w.lower() not in {"ltd", "limited"}]
        nm = " ".join(words[:3])
        return f'("{nm}" OR "{sym}") (shares OR stock OR results OR order OR stake OR earnings)'
    alias = (ALIASES.get(sym) or [None])[0]
    return f'("{alias}" OR "{sym}") (shares OR stock)' if alias else f'"{sym}" share price NSE'


def assemble(symbols=(), names=(), current="", days=1, include_filings=True) -> list:
    """The shared feed for a universe of stocks. Memoised ASSEMBLE_TTL seconds; never blocks longer than FETCH_BUDGET_S."""
    symbols = tuple(dict.fromkeys(str(s).upper().strip() for s in symbols if str(s).strip()))
    current = (current or "").upper().strip()
    if current and current not in symbols:
        symbols = (current,) + symbols
    names = tuple(sorted((k, v) for k, v in dict(names).items() if v))
    days = max(1, min(int(days or 1), 30))
    key = (symbols, names, current, days, include_filings)
    hit = _MEMO.get(key)
    if hit and time.time() - hit[0] < (ASSEMBLE_TTL if hit[2] else 20):
        return hit[1]
    tagger = _tagger(symbols, names)
    name_map = dict(names)
    jobs = [(u, n, "wire", None, None) for n, u in WIRE_SOURCES]
    jobs += [(_gnews_url(q, days), "Google News · macro", "macro", None, None) for q in MACRO_QUERIES]
    jobs += [(_gnews_url(_company_query(s, name_map.get(s)), days), f"Google News · {s}", "company", s, None)
             for s in _rotation(list(symbols), current, MAX_COMPANY_FETCH)]
    if include_filings:
        d_to, d_from = _now().strftime("%Y%m%d"), (_now() - timedelta(days=min(days, 7))).strftime("%Y%m%d")
        jobs.append(("bse://filings", "BSE filings", "filing", None, lambda: _bse_cached(d_from, d_to, _bucket(600))))
    pool = ThreadPoolExecutor(max_workers=10)
    futs = {pool.submit(_fetch, u, n, fn): (u, n, kind, sym) for u, n, kind, sym, fn in jobs}
    done, pending = wait(futs, timeout=FETCH_BUDGET_S)
    pool.shutdown(wait=False, cancel_futures=True)
    cutoff = _now() - timedelta(days=days)
    items = []
    for f in done:
        u, n, kind, sym = futs[f]
        try:
            rows = f.result()
        except Exception:
            continue
        src = n.split(" · ")[0] if kind != "wire" else n
        for r in rows:
            a = _make(r, kind, src, sym, tagger)
            if a and a["pub_dt"] >= cutoff:
                if kind == "company" and sym and not (_tag(a["title"], "", tagger) or sym in a["title"].upper()) and name_map.get(sym):
                    continue                                   # with a known company name, keep only headlines that name it
                items.append(a)
    out = _score(_cluster(items), set(symbols), current)
    out.sort(key=lambda a: (a["priority"], a["pub_dt"]), reverse=True)
    _MEMO[key] = (time.time(), out, not pending)
    if len(_MEMO) > 64:
        _MEMO.pop(next(iter(_MEMO)))
    return out


# ═════════════════════════ views & metrics ═════════════════════════
MODES_RAIL = ["TOP", "LIVE", "MINE", "RISK", "MACRO"]
MODES_FULL = ["TOP", "LIVE", "MY STOCKS", "CATALYSTS", "RISK", "EARNINGS", "CORP ACTIONS", "FILINGS", "MACRO"]


def _view(items: list, mode: str, query: str = "", current: str = "") -> list:
    m = mode.upper()
    if m == "LIVE":
        out = sorted(items, key=lambda a: a["pub_dt"], reverse=True)
    elif m in ("MINE", "MY STOCKS"):
        out = [a for a in items if a["mine"]]
    elif m == "CATALYSTS":
        out = [a for a in items if a["mine"] and (a["score"] > 0 or a["event"] in ("ORDER", "CAPEX", "STAKE", "CORP ACTION")) and a["score"] >= 0]
    elif m == "RISK":
        out = [a for a in items if a["score"] < 0 or a["event"] == "REGULATORY"]
    elif m == "EARNINGS":
        out = [a for a in items if a["event"] == "EARNINGS"]
    elif m == "CORP ACTIONS":
        out = [a for a in items if a["event"] in ("CORP ACTION", "STAKE", "PROMOTER", "MANAGEMENT", "IPO")]
    elif m == "FILINGS":
        out = sorted([a for a in items if a["kind"] == "filing"], key=lambda a: (not a["mine"], -a["pub_dt"].timestamp()))
    elif m == "MACRO":
        out = [a for a in items if a["kind"] == "macro" or a["macro"]]
    elif m == "SECURITY":
        out = [a for a in items if current and current in a["symbols"]]
    else:
        out = [a for a in items if a["kind"] != "filing" or a["mine"]]
    q = (query or "").strip().lower()
    if q:
        out = [a for a in out if q in a["title"].lower() or q in a["source"].lower() or q in a["event"].lower()
               or any(q == s.lower() for s in a["symbols"]) or any(q in s.lower() for s in a["sectors"])]
    return out


def news_tone(items: list) -> int:
    """−100 … +100 net tone of the headlines (importance-weighted)."""
    w = [(a["score"], a["importance"]) for a in items if a["kind"] != "filing"]
    tot = sum(i for _, i in w)
    return int(round(sum(s * i for s, i in w) / tot / 3 * 100)) if tot else 0


def pulse(items: list, symbols) -> pd.DataFrame:
    rows = []
    for s in symbols:
        st_ = [a for a in items if s in a["symbols"]]
        if not st_:
            rows.append({"symbol": s, "stories": 0, "tone": 0, "pos": 0, "neg": 0, "latest": "", "age": None, "link": ""})
            continue
        latest = max(st_, key=lambda a: a["pub_dt"])
        rows.append({"symbol": s, "stories": len(st_), "tone": round(sum(a["score"] for a in st_) / len(st_), 2),
                     "pos": sum(a["score"] > 0 for a in st_), "neg": sum(a["score"] < 0 for a in st_),
                     "latest": latest["title"], "age": latest["time_str"], "link": latest["link"]})
    return pd.DataFrame(rows).sort_values(["stories", "tone"], ascending=False) if rows else pd.DataFrame()


def sector_tone(items: list) -> list:
    acc = defaultdict(list)
    for a in items:
        for s in a["sectors"]:
            acc[s].append(a["score"])
    return sorted(((k, len(v), sum(v) / len(v)) for k, v in acc.items() if len(v) >= 2), key=lambda x: -x[1])


def source_health() -> list:
    with _LOCK:
        rows = list(_HEALTH.values())
    agg = {}
    for r in rows:
        k = r["name"].split(" · ")[0] if r["name"].startswith("Google News") else r["name"]
        a = agg.setdefault(k, {"name": k, "ok": 0, "fail": 0, "n": 0, "err": ""})
        a["ok" if r["ok"] else "fail"] += 1
        a["n"] += r["n"]
        if not r["ok"]:
            a["err"] = r["err"]
    return sorted(agg.values(), key=lambda a: (a["fail"] > 0 and a["ok"] == 0, a["name"]))


# ═════════════════════════ price reaction (current stock only) ═════════════════════════
@st.cache_data(ttl=300, show_spinner=False, max_entries=16)
def _intraday(symbol: str):
    if yf is None:
        return []
    df = yf.download(f"{symbol}.NS", period="5d", interval="15m", progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        return []
    c = df["Close"]
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    c = pd.to_numeric(c, errors="coerce").dropna()
    idx = pd.to_datetime(c.index)
    idx = idx.tz_localize("UTC").tz_convert(IST) if idx.tz is None else idx.tz_convert(IST)
    return list(zip(idx.to_pydatetime(), c.to_numpy().astype(float)))


def _reaction(symbol: str, dt) -> float | None:
    try:
        pts = _intraday(symbol)
    except Exception:
        return None
    after = [p for t, p in pts if t >= dt]
    if len(after) < 1 or not pts:
        return None
    return (pts[-1][1] / after[0] - 1) * 100 if after[0] > 0 else None


# ═════════════════════════ rendering ═════════════════════════
def _css():
    st.markdown(f"""<style>
    .nd-s{{display:block;text-decoration:none !important;border-left:2px solid var(--c);padding:8px 8px 8px 10px;margin:0 0 6px;
           background:linear-gradient(90deg,rgba(255,255,255,.025),transparent 70%);transition:background .12s ease;}}
    .nd-s:hover{{background:linear-gradient(90deg,rgba(255,159,10,.07),transparent 80%);}}
    .nd-m{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font:800 8.5px {MONO};letter-spacing:.7px;color:{TX3};margin-bottom:3px;}}
    .nd-p{{padding:0 4px;border-radius:2px;color:#000;}}
    .nd-h{{font:600 12.5px/1.42 Inter,Arial,sans-serif;color:{TX};}}
    .nd-full .nd-h{{font-size:14px;}}
    .nd-x{{font:11.5px/1.55 Inter,Arial,sans-serif;color:{TX2};margin-top:4px;}}
    .nd-f{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font:9px {MONO};color:{TX3};margin-top:5px;}}
    .nd-tag{{padding:0 5px;border:1px solid currentColor;border-radius:2px;font-weight:800;letter-spacing:.5px;}}
    .nd-k{{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;border-top:1px solid {LINE};border-bottom:1px solid {LINE};padding:7px 0;margin:6px 0;text-align:center;}}
    .nd-k b{{display:block;font:800 8px {MONO};letter-spacing:.8px;}} .nd-k span{{font:700 13px {MONO};color:{TX};}}
    .nd-tone{{position:relative;height:5px;background:linear-gradient(90deg,{RED}66,#222 50%,{GREEN}66);margin:4px 0 2px;}}
    .nd-tone i{{position:absolute;top:-3px;width:3px;height:11px;background:{AMBER};transform:translateX(-1px);}}
    .nd-tape{{overflow:hidden;white-space:nowrap;border:1px solid {LINE};border-left:3px solid {RED};background:{S1};height:34px;margin:4px 0 10px;}}
    .nd-tape div{{display:inline-block;padding:9px 0;animation:ndtape 70s linear infinite;font:700 11px {MONO};color:{TX2};}}
    .nd-tape b{{color:{RED};margin:0 6px 0 28px;}} .nd-tape:hover div{{animation-play-state:paused;}}
    @keyframes ndtape{{from{{transform:translateX(0)}}to{{transform:translateX(-50%)}}}}
    .nd-kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:4px 0 8px;}}
    .nd-kp{{background:{S1};border:1px solid {LINE};border-top:2px solid var(--a,{LINE2});padding:10px 12px;}}
    .nd-kp .l{{font:700 8px {MONO};letter-spacing:1.1px;color:{TX3};}} .nd-kp .v{{font:700 19px {MONO};color:{TX};margin-top:4px;}}
    .nd-kp .s{{font:10px {MONO};color:{TX3};margin-top:3px;}}
    .nd-sec{{font:700 10px {MONO};letter-spacing:1.2px;color:{AMBER};border-bottom:1px solid {LINE};padding:8px 0 6px;margin:12px 0 8px;}}
    .nd-pl{{display:grid;grid-template-columns:76px 34px 1fr;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid #141414;font:10px {MONO};}}
    .nd-pl b{{color:{TX};}} .nd-pl a{{color:{TX2};text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font:11px Inter,sans-serif;}}
    .nd-bar{{display:grid;grid-template-columns:92px 1fr 44px;gap:8px;align-items:center;font:10px {MONO};color:{TX2};margin:5px 0;}}
    .nd-bar .t{{height:6px;background:#161616;position:relative;}} .nd-bar .t i{{position:absolute;top:0;height:100%;}}
    .nd-src{{display:flex;justify-content:space-between;font:10px {MONO};padding:4px 0;border-bottom:1px solid #141414;color:{TX2};}}
    .nd-empty{{font:11px {MONO};color:{TX3};padding:14px 2px;line-height:1.6;}}
    div[class*="st-key-nd_rmode"] [role="radiogroup"],div[class*="st-key-nd_fmode"] [role="radiogroup"]{{gap:3px !important;}}
    div[class*="st-key-nd_rmode"] label[data-baseweb="radio"]{{padding:3px 7px !important;}}
    div[class*="st-key-nd_rmode"] label[data-baseweb="radio"] p{{font-size:9px !important;}}
    </style>""", unsafe_allow_html=True)


def _href(u: str) -> str:
    return html.escape(u, quote=True) if u and urlparse(u).scheme in ("http", "https") else "#"


def _story(a: dict, full: bool = False, current: str = "") -> str:
    sc = _SENT_COLOR[a["sentiment"]]
    pr = "P1" if a["priority"] >= 70 else "P2" if a["priority"] >= 45 else "P3"
    pc = RED if pr == "P1" else AMBER if pr == "P2" else "#444"
    flags = ""
    if a.get("is_breaking"):
        flags += f'<span style="color:{RED}">● BREAKING</span>'
    if a.get("hot"):
        flags += f'<span style="color:{YELLOW}">▲ HOT</span>'
    if a.get("is_new"):
        flags += f'<span style="color:{CYAN}">NEW</span>'
    ev = a["event"]
    syms = "".join(f'<span class="nd-tag" style="color:{AMBER if s == current else CYAN}">{html.escape(s)}</span>' for s in a["symbols"][:4])
    if not syms:
        syms = f'<span class="nd-tag" style="color:{TX3}">{"MACRO" if a["kind"] == "macro" or a["macro"] else "MARKET"}</span>'
    extra = "".join(f'<span style="color:{TX3}">{html.escape(s)}</span>' for s in (a["sectors"][:2] + a["macro"][:2]))
    amt = f'<span style="color:{GREEN if a["score"] >= 0 else RED}">₹{a["amount_cr"]:,.0f} Cr</span>' if a.get("amount_cr") else ""
    src = html.escape(a["source"]) + (f' +{a["source_count"] - 1}' if a["source_count"] > 1 else "")
    react = ""
    if full and current and current in a["symbols"] and a["kind"] != "macro":
        r = _reaction(current, a["pub_dt"])
        if r is not None:
            react = f'<span style="color:{GREEN if r >= 0 else RED}">{current} {r:+.2f}% since</span>'
    tone = f'<span style="color:{sc}">{"▲" if a["score"] > 0 else "▼" if a["score"] < 0 else "■"} {a["sentiment_label"]}</span>'
    summ = f'<div class="nd-x">{html.escape(a["summary"])}</div>' if full and a.get("summary") and a["kind"] != "company" else ""
    return (f'<a class="nd-s{" nd-full" if full else ""}" href="{_href(a["link"])}" target="_blank" style="--c:{sc}">'
            f'<div class="nd-m"><span class="nd-p" style="background:{pc}">{pr}</span><span style="color:{EVENT_COLORS.get(ev, TX3)}">{html.escape(ev)}</span>'
            f'<span>{a["time_str"]}</span>{flags}</div><div class="nd-h">{html.escape(a["title"])}</div>{summ}'
            f'<div class="nd-f">{syms}{tone}{amt}<span>{src}</span>{extra}{react}</div></a>')


def _stories(items, full=False, current="", empty="No stories in this view."):
    if not items:
        st.markdown(f'<div class="nd-empty">{html.escape(empty)}</div>', unsafe_allow_html=True)
        return
    st.markdown("".join(_story(a, full, current) for a in items), unsafe_allow_html=True)


def _mark_new(items):
    seen = st.session_state.setdefault("_news_seen_keys", set())
    first = not seen
    for a in items:
        a["is_new"] = (not first) and a["id"] not in seen
    seen.update(a["id"] for a in items[:200])
    if len(seen) > 3000:
        st.session_state["_news_seen_keys"] = set(list(seen)[-1500:])
    return items


def _names_from_session() -> tuple:
    """Company names when other modules have put them in session (portfolio lots / research)."""
    names = {}
    for lot in st.session_state.get("pf_lots", []) or []:
        if isinstance(lot, dict) and lot.get("symbol") and lot.get("company"):
            names[str(lot["symbol"]).upper()] = str(lot["company"])
    return tuple(names.items())


def _universe(watchlist):
    syms = [str(s).upper().strip() for s in (watchlist or []) if str(s).strip()]
    for key in ("watchlist", "admin_watchlist"):
        syms += [str(s).upper() for s in st.session_state.get(key, []) or []]
    syms += [str(l.get("symbol", "")).upper() for l in st.session_state.get("pf_lots", []) or [] if isinstance(l, dict)]
    return list(dict.fromkeys(s for s in syms if s))[:120]


# ═════════════════════════ public API ═════════════════════════
def _ensure_news_state():
    st.session_state.setdefault("_news_seen_keys", set())
    st.session_state.setdefault("_news_days", 1)


def refresh_news(watchlist, current_security=None, sector=None, days=1):
    """Warm the shared feed (kept for older callers)."""
    return assemble(tuple(_universe(watchlist)), _names_from_session(), current_security or "", days)


def _fetch_news_for_stock(symbol: str, days: int = 1, company: str | None = None) -> list:
    """Company headlines (Google News query + wire stories that name the company). Used by Portfolio and Research."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return []
    names = ((sym, company),) if company else ()
    tagger = _tagger((sym,), names)
    rows = _fetch(_gnews_url(_company_query(sym, company), days), f"Google News · {sym}")
    cutoff = _now() - timedelta(days=max(1, int(days)))
    out = []
    for r in rows:
        a = _make(r, "company", "Google News", sym, tagger)
        if not a or a["pub_dt"] < cutoff:
            continue
        if company and not (_tag(a["title"], "", tagger) or sym in a["title"].upper()):
            continue
        out.append(a)
    return _score(_cluster(out), {sym}, sym)


def get_security_news(symbol: str, days: int = 7) -> list:
    """Research > News: company stories plus wire / filing stories that mention the stock."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return []
    feed = assemble((sym,), _names_from_session(), sym, days)
    items = [a for a in feed if sym in a["symbols"]]
    return sorted(items, key=lambda a: a["pub_dt"], reverse=True)


def get_news_dot(sym: str) -> str:
    s = (sym or "").upper().strip()
    for _, (t, items, _) in list(_MEMO.items()):
        if any(s in a["symbols"] and a["age_min"] <= 24 * 60 for a in items):
            return "1"
    return ""


def render_news_rail(watchlist, label: str = "WATCHLIST", current_security: str | None = None, sector: str | None = None):
    """Compact right-rail news: tone, counts, mode selector, top stories."""
    _ensure_news_state()
    _css()
    current = (current_security or st.session_state.get("active_security") or "").upper().strip()
    if st.session_state.get("page") != "security":
        current = (current_security or "").upper().strip()
    c1, c2 = st.columns([1, 1.4])
    days = {"24H": 1, "3D": 3, "7D": 7}[c1.selectbox("Window", ["24H", "3D", "7D"], key="nd_rwin", label_visibility="collapsed")]
    query = c2.text_input("Search news", key="nd_rq", placeholder="search…", label_visibility="collapsed")
    with st.spinner("Loading news…"):
        items = _mark_new(assemble(tuple(_universe(watchlist)), _names_from_session(), current, days))
    tone = news_tone(items)
    mine = sum(a["mine"] for a in items)
    st.markdown(
        f'<div class="nd-k"><div><b style="color:{AMBER}">STORIES</b><span>{len(items)}</span></div>'
        f'<div><b style="color:{CYAN}">MINE</b><span>{mine}</span></div>'
        f'<div><b style="color:{RED}">BREAKING</b><span>{sum(a["is_breaking"] for a in items)}</span></div>'
        f'<div><b style="color:{GREEN if tone >= 0 else RED}">TONE</b><span style="color:{GREEN if tone >= 0 else RED}">{tone:+d}</span></div></div>'
        f'<div class="nd-tone"><i style="left:{(tone + 100) / 2:.0f}%"></i></div>', unsafe_allow_html=True)
    modes = (["SECURITY"] if current else []) + MODES_RAIL
    with st.container(key="nd_rmode"):
        mode = st.radio("News view", modes, horizontal=True, key="nd_rmode_v", label_visibility="collapsed")
    shown = _view(items, mode, query, current)
    _stories(shown[:18], False, current, "No stories in this view yet. Add stocks to your watchlist or portfolio for company news.")
    if st.button("OPEN NEWS DESK ▸", key="nd_open_desk", use_container_width=True):
        st.session_state["page"] = "news"
        st.rerun()


news_panel = render_news_rail
news_box = render_news_rail


def render_news_terminal(watchlist=None, on_open=None, current_security: str | None = None):
    """Full NEWS workspace: breaking tape, KPIs, wire with filters, my-stocks pulse, sector tone, source health."""
    _ensure_news_state()
    _css()
    current = (current_security or st.session_state.get("active_security") or "").upper().strip()
    universe = _universe(watchlist)
    c1, c2, c3, c4 = st.columns([1, 2.4, 1.2, 0.8], gap="small")
    days = {"24 HOURS": 1, "3 DAYS": 3, "7 DAYS": 7, "30 DAYS": 30}[c1.selectbox("Window", ["24 HOURS", "3 DAYS", "7 DAYS", "30 DAYS"], key="nd_fwin", label_visibility="collapsed")]
    query = c2.text_input("Search", key="nd_fq", placeholder="Search headlines, stocks, sectors, sources…  e.g. RBI, order, HDFCBANK, pharma", label_visibility="collapsed")
    sort = c3.selectbox("Sort", ["Priority", "Newest"], key="nd_fsort", label_visibility="collapsed")
    if c4.button("↻ REFRESH", key="nd_refresh", use_container_width=True):
        _MEMO.clear()
        _rss_cached.clear()
        _COOL.clear()
    with st.spinner("Assembling the wire…"):
        items = _mark_new(assemble(tuple(universe), _names_from_session(), current, days))
    if not items:
        st.markdown('<div class="nd-empty">No stories returned yet — sources may be slow. Press REFRESH in a few seconds.</div>', unsafe_allow_html=True)
        return

    tone = news_tone(items)
    pos, neg = sum(a["score"] > 0 for a in items), sum(a["score"] < 0 for a in items)
    health = source_health()
    live = sum(1 for h in health if h["ok"])
    breaking = [a for a in items if a["is_breaking"] or (a["priority"] >= 70 and a["age_min"] <= 180)][:12]
    if breaking:
        tape = "".join(f'<b>{html.escape(", ".join(a["symbols"][:2]) or a["event"])}</b>{html.escape(a["title"])}' for a in breaking)
        st.markdown(f'<div class="nd-tape"><div>{tape}{tape}</div></div>', unsafe_allow_html=True)
    kp = lambda l, v, s, a: f'<div class="nd-kp" style="--a:{a}"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
    st.markdown('<div class="nd-kpis">'
                + kp("STORIES", len(items), f"{sum(a['is_new'] for a in items)} new since last look", AMBER)
                + kp("MY STOCKS", sum(a["mine"] for a in items), f"across {len(universe)} tracked stocks", CYAN)
                + kp("NEWS TONE", f'<span style="color:{GREEN if tone >= 0 else RED}">{tone:+d}</span>',
                     f'<span style="color:{GREEN}">{pos} positive</span> · <span style="color:{RED}">{neg} negative</span>', GREEN if tone >= 0 else RED)
                + kp("BREAKING · HOT", f'{sum(a["is_breaking"] for a in items)} · {sum(a["hot"] for a in items)}', "last hour · 3+ outlets in 3 h", RED)
                + kp("FILINGS", sum(a["kind"] == "filing" for a in items), "BSE exchange announcements", PURPLE)
                + kp("SOURCES LIVE", f"{live}/{len(health)}", "see SOURCE HEALTH", GREEN if live == len(health) else AMBER)
                + '</div>', unsafe_allow_html=True)

    left, right = st.columns([2.35, 1], gap="medium")
    with left:
        modes = (["SECURITY"] if current else []) + MODES_FULL
        with st.container(key="nd_fmode"):
            mode = st.radio("Desk view", modes, horizontal=True, key="nd_fmode_v", label_visibility="collapsed")
        shown = _view(items, mode, query, current)
        if sort == "Newest":
            shown = sorted(shown, key=lambda a: a["pub_dt"], reverse=True)
        st.markdown(f'<div class="nd-sec">{html.escape(mode)} · {len(shown)} STORIES</div>', unsafe_allow_html=True)
        _stories(shown[:45], True, current, "Nothing in this view for the selected window.")
        tagged = list(dict.fromkeys(s for a in shown[:45] for s in a["symbols"]))
        if on_open and tagged:
            o1, o2 = st.columns([3, 1])
            pick = o1.selectbox("Open a stock from these stories", tagged, key="nd_open_pick", label_visibility="collapsed")
            if o2.button("OPEN STOCK ▸", key="nd_open_btn", use_container_width=True, type="primary"):
                on_open(pick)
    with right:
        st.markdown('<div class="nd-sec">NEWS PULSE · YOUR STOCKS</div>', unsafe_allow_html=True)
        pdf = pulse(items, universe)
        if pdf.empty or pdf["stories"].sum() == 0:
            st.markdown('<div class="nd-empty">No stories naming your stocks in this window.</div>', unsafe_allow_html=True)
        else:
            rows = []
            for r in pdf[pdf["stories"] > 0].head(16).to_dict("records"):
                c = GREEN if r["tone"] > 0.2 else RED if r["tone"] < -0.2 else TX3
                rows.append(f'<div class="nd-pl"><b>{html.escape(r["symbol"])}</b><span style="color:{c}">{r["stories"]}{"▲" if r["tone"] > 0.2 else "▼" if r["tone"] < -0.2 else "·"}</span>'
                            f'<a href="{_href(r["link"])}" target="_blank" title="{html.escape(r["latest"])}">{html.escape(r["age"] or "")} · {html.escape(r["latest"])}</a></div>')
            quiet = int((pdf["stories"] == 0).sum())
            st.markdown("".join(rows) + (f'<div class="nd-empty" style="padding:6px 0">{quiet} tracked stocks with no news in this window.</div>' if quiet else ""),
                        unsafe_allow_html=True)
        st.markdown('<div class="nd-sec">SECTOR TONE</div>', unsafe_allow_html=True)
        stn = sector_tone(items)
        if stn:
            bars = []
            for name, n, t in stn[:12]:
                w = min(50, abs(t) / 3 * 50)
                c = GREEN if t >= 0 else RED
                pos_css = f"left:50%;width:{w:.0f}%" if t >= 0 else f"left:{50 - w:.0f}%;width:{w:.0f}%"
                bars.append(f'<div class="nd-bar"><span>{html.escape(name)}</span><div class="t"><i style="{pos_css};background:{c}"></i>'
                            f'<i style="left:50%;width:1px;background:#444"></i></div><span style="color:{c};text-align:right">{n} · {t:+.1f}</span></div>')
            st.markdown("".join(bars), unsafe_allow_html=True)
        else:
            st.markdown('<div class="nd-empty">Not enough sector-tagged stories yet.</div>', unsafe_allow_html=True)
        st.markdown('<div class="nd-sec">MACRO THEMES</div>', unsafe_allow_html=True)
        mc = Counter(t for a in items for t in a["macro"]).most_common(8)
        st.markdown(" ".join(f'<span class="nd-tag" style="color:{AMBER};font:800 9px {MONO};margin:2px;display:inline-block">{html.escape(k)} {v}</span>' for k, v in mc)
                    or '<div class="nd-empty">—</div>', unsafe_allow_html=True)
        st.markdown('<div class="nd-sec">SOURCE HEALTH</div>', unsafe_allow_html=True)
        st.markdown("".join(
            f'<div class="nd-src"><span>{html.escape(h["name"])}</span><span style="color:{GREEN if h["ok"] else RED}">'
            f'{"● " + str(h["n"]) + " items" if h["ok"] else "✕ " + html.escape(h["err"] or "failed")}'
            f'{" · " + str(h["fail"]) + " failed" if h["ok"] and h["fail"] else ""}</span></div>' for h in health), unsafe_allow_html=True)
        st.caption("Tone and event tags are automatic keyword scoring of headlines — a reading aid, not a recommendation. Click any story to read the source.")
