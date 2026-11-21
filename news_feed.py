"""
news_feed.py — Arka News Desk v12.1

v12.1
  * NSE is a source: the exchange's own filing feeds (announcements, financial results, board meetings, corporate
    actions, insider trading) from nsearchives.nseindia.com. Each filing is matched to its stock through NSE's
    company list, so it carries a stock chip you can tap, and filings from your stocks show under My stocks.

v12 (faster and sharper; the look and the public API are unchanged)
  FASTER
  * The page no longer waits for the news. Once a feed has been built, an expired copy is shown at once and rebuilt
    in the background; the fresh version appears on the next refresh. Only the very first load can take a few seconds.
  * Tagging your stocks in a headline is one pass over the text instead of two searches per stock (about 100 times
    fewer searches with a large watchlist). Tone, sector, theme and story type are worked out once per headline.
  * One shared connection to each source, and sources are asked "anything new since last time?" so an unchanged feed
    costs almost nothing.
  * The news desk redraws only itself when you change a view, and refreshes on its own every two minutes.
  SHARPER
  * Stories are merged only when they are really the same story: the subject, the numbers and the tagged stocks must
    agree, so "TCS Q2 profit rises 9%" is no longer folded into "Infosys Q2 profit rises 12%".
  * Tone knows more market wording (USFDA letters, pledges, guidance cuts, order inflows, rating changes, tax demands).
  * News desk: each stock chip shows today's price move, and two new views: Movers (news on your stocks that moved
    2% or more today) and Developing (stories carried by three or more outlets).

v11 (look only; data, scoring and the public API are unchanged)
  * Rebuilt on the app design system: Inter, sentence case, rounded story cards, soft pills; no uppercase monospace.
  * Story cards: clock time + age + source, plain-word tags (Results, Order win, Breaking, Trending), stock chips, tone dot.
  * Views are pills / segmented controls instead of radio buttons; news desk has a phone layout (one section at a time).
  * Summaries that only repeat the headline (Google News) are dropped, so the watchlist news sheet no longer echoes titles.

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
import importlib
import re
import sys
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
ASSEMBLE_TTL = 300          # an assembled feed is fresh for this long
STALE_MAX = 3 * 3600        # after that it is still shown at once (and rebuilt in the background) for up to this long
AUTO_REFRESH_S = 120        # the news desk redraws itself this often
MOVE_PCT = 2.0              # "Movers": news on a stock that moved at least this much today
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
NSE_FEEDS = [  # the exchange's own RSS feeds of company filings
    ("Announcements", "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"),
    ("Financial results", "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml"),
    ("Board meetings", "https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml"),
    ("Corporate actions", "https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml"),
    ("Insider trading", "https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml"),
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
    (r"usfda (approval|nod|clearance)|eir from (the )?usfda|order inflows?|record order book|debt[- ]free|stake hike|"
     r"special dividend|bonus issue|guidance raised|raises? full[- ]year|margin expansion|turns? profitable|rating upgrade|"
     r"npas? (fall|falls|decline|declines|ease|eases)", 2),
    (r"usfda (warning letter|import alert|observations?)|form 483|pledge[sd]? (more )?shares|rating downgrade|guidance cut|"
     r"cuts? (guidance|outlook|forecast)|margin contraction|npas? (rise|rises|surge|surges|jump|jumps)|show[- ]cause notice|"
     r"(tax|gst) demand|data breach|recalls?|write[- ]?offs?|qualified opinion|auditor (resigns|quits)|fire (at|in) (the )?(plant|factory|unit)|"
     r"promoters? (sell|sells|offload|offloads)", -2),
    (r"record (high|profit|revenue|sales)|all[- ]time high|52[- ]week high|upper circuit|multibagger", 2),
    (r"beats? (estimates?|expectations?|street)|raises? (guidance|outlook|target)|target raised", 2),
    (r"(wins?|bags?|secures?|receives?) (an? )?(?:[\w₹$.,-]+ ){0,4}(orders?|contracts?|deals?)", 2),   # v11: "bags ₹1,200 crore order"
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
                            r"change in (address|registrar)|copy of newspaper|postal ballot|esop|esos|"
                            r"analysts?.{0,25}meet|investors?.{0,12}meet|certificate under sebi|record of proceedings", re.I)

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
_INFLIGHT: set = set()      # feeds being rebuilt in the background right now
_DOTS: dict = {}            # memo key -> symbols with a story in the last 24 h (for get_news_dot)
_VALID: dict = {}           # url -> (etag, last-modified, rows): lets a source answer "nothing new"

_SESSION = requests.Session()   # one connection per source instead of a new handshake for every fetch
try:
    _adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0)
    _SESSION.mount("https://", _adapter)
    _SESSION.mount("http://", _adapter)
except Exception:  # pragma: no cover
    pass


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
    if raw:                                               # NSE writes "22-Nov-2026 18:30:00", in Indian time
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
            try:
                return datetime.strptime(str(raw).strip(), fmt).replace(tzinfo=IST)
            except ValueError:
                continue
    return None


def _clean_html(s: str, n=260) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    return s[:n] + ("…" if len(s) > n else "")


@st.cache_data(ttl=FEED_TTL * 2, show_spinner=False, max_entries=256)
def _rss_cached(url: str, bucket: int) -> list:
    head = {"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"}
    prev = _VALID.get(url)
    if prev:                                              # ask only for what changed since the last fetch
        if prev[0]:
            head["If-None-Match"] = prev[0]
        if prev[1]:
            head["If-Modified-Since"] = prev[1]
    r = _SESSION.get(url, timeout=HTTP_TIMEOUT, headers=head)
    if r.status_code == 304 and prev:
        return prev[2]
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
    if r.headers.get("ETag") or r.headers.get("Last-Modified"):
        _VALID[url] = (r.headers.get("ETag"), r.headers.get("Last-Modified"), rows)
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
    r = _SESSION.get("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
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


_NAME_INDEX: dict = {"t": 0.0, "map": {}}
_NAME_LOCK = threading.Lock()


def _co_key(name: str) -> str:
    """'Tata Consultancy Services Limited' -> 'tata consultancy services' (for matching a filing to its stock)."""
    k = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip()
    return re.sub(r"\s+(limited|ltd)$", "", k)


def _name_index() -> dict:
    """Company name -> NSE symbol, from the exchange's own equity list (the list the search box uses). Refreshed
    every 6 hours; an empty result is retried after 10 minutes."""
    with _NAME_LOCK:                                      # several feeds ask at once: build it once, hand out the same map
        now = time.time()
        if now - _NAME_INDEX["t"] < (6 * 3600 if _NAME_INDEX["map"] else 600):
            return _NAME_INDEX["map"]
        out = {}
        try:
            wl = sys.modules.get("watchlist_page") or importlib.import_module("watchlist_page")
            for sym, v in (wl.equity_names() or {}).items():
                k = _co_key(str(v[0] if isinstance(v, (tuple, list)) else v or ""))
                if k:
                    out.setdefault(k, str(sym).upper())
        except Exception:
            out = {}
        _NAME_INDEX["map"], _NAME_INDEX["t"] = out, now
        return out


@st.cache_data(ttl=FEED_TTL * 2, show_spinner=False, max_entries=16)
def _nse_cached(url: str, label: str, bucket: int) -> list:
    """One NSE filing feed, newest first. Each row names its company; `sym` is set when NSE's list knows it."""
    r = _SESSION.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*",
                                                         "Referer": "https://www.nseindia.com/"})
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    index = _name_index()
    out = []
    for e in feed.entries[:150]:
        title, desc = _clean_html(e.get("title", ""), 300), _clean_html(e.get("summary", ""), 300)
        if " - " in title:
            name, head = (x.strip() for x in title.split(" - ", 1))
        else:
            name, head = title.strip(), desc
        if not name or BORING_FILINGS.search(f"{head} {desc}"):
            continue
        dt = _parse_dt(e.get("published") or e.get("updated"), e.get("published_parsed") or e.get("updated_parsed"))
        if dt is None:
            continue
        out.append({"title": f"{name}: {head or label}"[:240], "link": e.get("link", "") or "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                    "summary": desc if desc and desc != head else label, "dt": dt, "src": "NSE filing", "company": name, "sym": index.get(_co_key(name))})
    if not out:
        raise RuntimeError("no filings")
    return out


# ═════════════════════════ scoring / tagging ═════════════════════════
@lru_cache(maxsize=8192)
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


@lru_cache(maxsize=8192)
def _event_of(text: str) -> str:
    for name, rx in _EVENT_RX:
        if rx.search(text or ""):
            return name
    return ""


def _event(text: str, macro: bool) -> str:
    return _event_of(text or "") or ("MACRO" if macro else "NEWS")


@lru_cache(maxsize=8192)
def _topics(low: str) -> tuple:
    """(sectors, macro themes) named in a lower-cased headline + summary."""
    return (tuple(k for k, rx in _SECTOR_RX.items() if rx.search(low)), tuple(k for k, rx in _MACRO_RX.items() if rx.search(low)))


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
    """One compiled matcher for the whole universe: tickers (case-sensitive, in the headline) and aliases / company
    names (case-insensitive, in headline + summary). Longest names are tried first, so "SBI Life" is SBILIFE, not SBIN."""
    name_map = dict(names)
    alias_map = {}                                    # alias -> [symbols]
    for sym in symbols:
        pats = [a for a in ALIASES.get(sym, [])]
        nm = name_map.get(sym)
        if nm:
            words = [w for w in re.sub(r"[^A-Za-z0-9& ]", " ", nm).split() if w.lower() not in {"ltd", "limited", "the", "india", "co", "corporation"}]
            if words and (len(words) > 1 or len(words[0]) >= 5):
                pats.append(" ".join(words[:2]).lower())
        for p_ in pats:
            if sym not in alias_map.setdefault(p_, []):
                alias_map[p_].append(sym)
    ticks = sorted((s_ for s_ in symbols if len(s_) >= 3), key=len, reverse=True)
    tick_rx = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in ticks) + r")(?![A-Za-z0-9])") if ticks else None
    alias_rx = (re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(p_) for p_ in sorted(alias_map, key=len, reverse=True)) + r")(?![a-z0-9])")
                if alias_map else None)
    return tick_rx, alias_rx, alias_map, {sym: i for i, sym in enumerate(symbols)}


def _tag(title: str, summary: str, tagger) -> list:
    tick_rx, alias_rx, alias_map, order = tagger
    found = set()
    if tick_rx is not None:
        found.update(tick_rx.findall(title))
    if alias_rx is not None:
        for m in alias_rx.findall(f"{title} {summary}".lower()):
            found.update(alias_map[m])
    return sorted(found, key=lambda x: order.get(x, 1 << 30))


def _norm_title(t: str) -> str:
    t = re.sub(r"\[[^\]]+\]|\b(live updates?|live blog|breaking|watch|explained)\b", " ", (t or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


def _similar(a: str, b: str) -> bool:
    """Same story told by two outlets? Most words shared, the subject (first word) of each appears in the other, and
    if both quote numbers they share one. So two companies' look-alike results headlines stay two stories."""
    wa, wb = a.split(), b.split()
    sa, sb = set(wa), set(wb)
    if not sa or not sb:
        return False
    inter = len(sa & sb)
    if inter < 4 or inter / max(1, min(len(sa), len(sb))) < 0.72:
        return False
    if wa[0] not in sb or wb[0] not in sa:
        return False
    na, nb = {w for w in sa if w[0].isdigit()}, {w for w in sb if w[0].isdigit()}
    return not (na and nb and not (na & nb))


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
    if summary and (_norm_title(title)[:60] in _norm_title(summary) or len(summary) < 25):
        summary = ""                                 # Google News summaries just repeat the headline + publisher
    text = f"{title}. {summary}"
    low = text.lower()
    score = _sentiment(f"{title}. {summary}") if kind == "filing" else _sentiment(title) * 2 + _sentiment(summary)   # a filing's substance is in its text
    score = max(-3, min(3, round(score / (1 if kind == "filing" else 2)))) if kind != "filing" else score
    syms = _tag(title, summary if kind != "company" else "", tagger)
    if symbol and symbol not in syms and kind == "company":
        syms.insert(0, symbol)
    if row.get("sym") and row["sym"] not in syms:        # a filing matched to its stock through NSE's company list
        syms.insert(0, row["sym"])
    sectors, macro = (list(x) for x in _topics(low))
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
        hit = next((i for i, kk in enumerate(keys[-150:], start=max(0, len(keys) - 150))
                    if _similar(k, kk) and not (a["symbols"] and out[i]["symbols"] and not set(a["symbols"]) & set(out[i]["symbols"]))), None)
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
    """The shared feed for a universe of stocks. Fresh for ASSEMBLE_TTL seconds; after that the last copy is returned
    at once while a new one is built in the background, so only the first load ever waits (at most FETCH_BUDGET_S)."""
    symbols = tuple(dict.fromkeys(str(s).upper().strip() for s in symbols if str(s).strip()))
    current = (current or "").upper().strip()
    if current and current not in symbols:
        symbols = (current,) + symbols
    names = tuple(sorted((k, v) for k, v in dict(names).items() if v))
    days = max(1, min(int(days or 1), 30))
    key = (symbols, names, current, days, include_filings)
    hit = _MEMO.get(key)
    if hit:
        age = time.time() - hit[0]
        if age < (ASSEMBLE_TTL if hit[2] else 20):
            return hit[1]
        if age < STALE_MAX:                               # expired: show it now, rebuild behind the scenes
            _rebuild_later(key)
            return _score(hit[1], set(symbols), current)   # ages and "breaking" flags brought up to date
    return _build(key)


def _rebuild_later(key):
    with _LOCK:
        if key in _INFLIGHT:
            return
        _INFLIGHT.add(key)

    def work():
        try:
            _build(key)
        except Exception:
            pass
        finally:
            with _LOCK:
                _INFLIGHT.discard(key)
    threading.Thread(target=work, name="arka-news", daemon=True).start()


def _build(key) -> list:
    """Fetch every source (never longer than FETCH_BUDGET_S), tag, merge, score and remember the result."""
    symbols, names, current, days, include_filings = key
    tagger = _tagger(symbols, names)
    name_map = dict(names)
    jobs = [(u, n, "wire", None, None) for n, u in WIRE_SOURCES]
    jobs += [(_gnews_url(q, days), "Google News · macro", "macro", None, None) for q in MACRO_QUERIES]
    jobs += [(_gnews_url(_company_query(s, name_map.get(s)), days), f"Google News · {s}", "company", s, None)
             for s in _rotation(list(symbols), current, MAX_COMPANY_FETCH)]
    if include_filings:
        d_to, d_from = _now().strftime("%Y%m%d"), (_now() - timedelta(days=min(days, 7))).strftime("%Y%m%d")
        jobs.append(("bse://filings", "BSE filings", "filing", None, lambda: _bse_cached(d_from, d_to, _bucket(600))))
        for label, url in NSE_FEEDS:
            jobs.append((url, "NSE filings", "filing", None, (lambda u=url, l=label: _nse_cached(u, l, _bucket()))))
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
    _DOTS[key] = frozenset(s_ for a in out if a["age_min"] <= 24 * 60 for s_ in a["symbols"])
    if len(_MEMO) > 64:
        old = next(iter(_MEMO))
        _MEMO.pop(old, None)
        _DOTS.pop(old, None)
    return out


# ═════════════════════════ views & metrics ═════════════════════════
MODES_RAIL = ["TOP", "LIVE", "MINE", "RISK", "MACRO"]
MODES_FULL = ["TOP", "LIVE", "MY STOCKS", "MOVERS", "DEVELOPING", "CATALYSTS", "RISK", "EARNINGS", "CORP ACTIONS", "FILINGS", "MACRO"]


def _view(items: list, mode: str, query: str = "", current: str = "", moves: dict | None = None) -> list:
    m = mode.upper()
    moves = moves or {}
    if m == "MOVERS":                                     # news on a stock that is actually moving today
        def big(a):
            return max((abs(moves.get(s_, 0.0)) for s_ in a["symbols"]), default=0.0)
        out = sorted([a for a in items if big(a) >= MOVE_PCT], key=lambda a: (big(a), a["pub_dt"]), reverse=True)
    elif m == "DEVELOPING":                               # carried by three or more outlets
        out = sorted([a for a in items if a["source_count"] >= 3], key=lambda a: (a["source_count"], a["pub_dt"]), reverse=True)
    elif m == "LIVE":
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


# ═════════════════════════ rendering (v11: app design system) ═════════════════════════
SANS = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"
EVENT_LABEL = {"REGULATORY": "Regulatory", "FILING": "Filing", "ORDER": "Order win", "STAKE": "Stake / M&A", "PROMOTER": "Promoter",
               "EARNINGS": "Results", "CORP ACTION": "Corporate action", "IPO": "IPO", "RATING": "Rating", "CAPEX": "Capex",
               "MANAGEMENT": "Management", "MACRO": "Macro", "NEWS": "News"}
MODE_LABEL = {"TOP": "Top", "LIVE": "Latest", "MINE": "My stocks", "MY STOCKS": "My stocks", "MOVERS": "Movers", "DEVELOPING": "Developing",
              "CATALYSTS": "Catalysts", "RISK": "Risk",
              "EARNINGS": "Results", "CORP ACTIONS": "Corporate actions", "FILINGS": "Filings", "MACRO": "Macro"}
TAG_LABEL = {"BANKS & FIN": "Banks & finance", "IT": "IT", "AUTO": "Auto", "PHARMA": "Pharma & health", "METALS": "Metals",
             "ENERGY": "Oil & gas", "FMCG": "FMCG", "REALTY": "Realty", "POWER": "Power", "CAP GOODS": "Capital goods",
             "TELECOM": "Telecom", "CEMENT": "Cement", "CHEMICALS": "Chemicals", "RBI": "RBI", "FED": "Fed", "INFLATION": "Inflation",
             "CRUDE": "Crude", "RUPEE": "Rupee", "FII": "FII / DII flows", "GDP": "GDP", "TRADE": "Trade & tariffs", "BUDGET": "Budget & GST",
             "GLOBAL": "Global markets"}
TONE_WORD = {"strong_positive": "Positive", "mild_positive": "Slightly positive", "neutral": "Neutral",
             "mild_negative": "Slightly negative", "strong_negative": "Negative"}
WINDOWS = {"24 h": 1, "3 days": 3, "7 days": 7, "30 days": 30}


def _tag_label(t: str) -> str:
    return TAG_LABEL.get(t, str(t).capitalize())


def _phone() -> bool:
    try:
        return bool(st.session_state.get("_ak_phone"))
    except Exception:
        return False


def _choice(label, options, key, fmt=None, pills=False):
    """Segmented control / pills on newer Streamlit, a horizontal radio otherwise. Never returns None."""
    fmt = fmt or (lambda o: o)
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]

    def _keep():
        if st.session_state.get(key) not in options:
            st.session_state[key] = options[0]
    fn = getattr(st, "pills" if pills else "segmented_control", None) or getattr(st, "segmented_control", None) or getattr(st, "pills", None)
    if fn is not None:
        try:
            v = fn(label, options, key=key, format_func=fmt, label_visibility="collapsed", on_change=_keep)
            return v if v in options else options[0]
        except TypeError:
            pass
    return st.radio(label, options, key=key, format_func=fmt, horizontal=True, label_visibility="collapsed")


def _css():
    st.markdown(f"""<style>
    .nd-root,.nd-s,.nd-kpis,.nd-card,.nd-tape,.nd-k{{font-family:{SANS};font-variant-numeric:tabular-nums}}
    /* story */
    a.nd-s{{display:block;text-decoration:none !important;padding:12px 14px;margin:0 0 8px;border-radius:12px;background:var(--s1,#0E0E10);
           border:1px solid var(--line,#1E1E21);transition:border-color .15s ease,background .15s ease}}
    a.nd-s:hover{{border-color:#34343A;background:#131316}}
    a.nd-s.compact{{padding:10px 12px;margin-bottom:6px}}
    .nd-m{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:11.5px;color:var(--tx3,{TX3})}}
    .nd-m .t{{color:#E4E4E8;font-weight:600}} .nd-m .dot{{opacity:.45}}
    .nd-pill{{--c:#8A8A90;display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;color:var(--c);
             background:color-mix(in srgb,var(--c) 13%,transparent);white-space:nowrap}}
    .nd-pill.solid{{background:var(--c);color:#0A0A0A}}
    .nd-h{{font-size:13.5px;font-weight:600;line-height:1.45;color:#F2F2F5;margin-top:5px}}
    a.nd-s.full .nd-h{{font-size:15px}}
    .nd-x{{font-size:12.5px;line-height:1.55;color:var(--tx2,{TX2});margin-top:5px}}
    .nd-f{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:11.5px;color:var(--tx3,{TX3});margin-top:8px}}
    .nd-sym{{display:inline-block;padding:1px 7px;border-radius:6px;font-size:11px;font-weight:650;color:#E8E8EC;background:#1C1C21;border:1px solid #26262C}}
    .nd-sym[data-ak-sym]{{cursor:pointer}} .nd-sym[data-ak-sym]:hover{{border-color:rgba(255,159,10,.45);color:#fff}}
    .nd-sym.cur{{color:{AMBER};border-color:rgba(255,159,10,.4);background:rgba(255,159,10,.08)}}
    .nd-tone-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--c);margin-right:4px;vertical-align:1px}}
    /* rail summary */
    .nd-k{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:4px 0 8px}}
    .nd-k > div{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:10px;padding:8px 9px}}
    .nd-k b{{display:block;font-size:11px;font-weight:500;color:var(--tx3,{TX3});white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .nd-k span{{display:block;font-size:15px;font-weight:650;color:var(--tx,{TX});margin-top:2px}}
    .nd-tone{{position:relative;height:5px;border-radius:5px;background:linear-gradient(90deg,rgba(255,69,58,.45),#26262A 50%,rgba(48,209,88,.45));margin:6px 2px 4px}}
    .nd-tone i{{position:absolute;top:-4px;width:4px;height:13px;border-radius:2px;background:#fff;transform:translateX(-2px)}}
    .nd-tl{{display:flex;justify-content:space-between;font-size:11px;color:var(--tx3,{TX3});margin:0 2px 10px}}
    /* breaking strip */
    .nd-tape{{display:flex;align-items:center;gap:10px;overflow:hidden;white-space:nowrap;border:1px solid var(--line,#1E1E21);border-radius:12px;
             background:var(--s1,#0E0E10);height:40px;margin:6px 0 12px;padding-left:10px}}
    .nd-tape .lab{{flex:0 0 auto;font-size:11.5px;font-weight:650;color:#fff;background:{RED};padding:3px 10px;border-radius:999px}}
    .nd-tape .run{{overflow:hidden;flex:1;mask-image:linear-gradient(90deg,transparent,#000 3%,#000 97%,transparent);-webkit-mask-image:linear-gradient(90deg,transparent,#000 3%,#000 97%,transparent)}}
    .nd-tape .run div{{display:inline-block;animation:ndtape 80s linear infinite;font-size:12.5px;color:var(--tx2,{TX2})}}
    .nd-tape .run b{{color:#F2F2F5;font-weight:650;margin:0 6px 0 26px}}
    .nd-tape:hover .run div{{animation-play-state:paused}}
    @keyframes ndtape{{from{{transform:translateX(0)}}to{{transform:translateX(-50%)}}}}
    /* KPI cards */
    .nd-kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:4px 0 12px}}
    .nd-kp{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:12px;padding:11px 13px}}
    .nd-kp .l{{font-size:12px;font-weight:500;color:var(--tx3,{TX3})}} .nd-kp .v{{font-size:20px;font-weight:650;color:var(--tx,{TX});margin-top:4px;letter-spacing:-.2px}}
    .nd-kp .s{{font-size:11.5px;color:var(--tx3,{TX3});margin-top:2px}}
    /* side cards */
    .nd-card{{background:var(--s1,#0E0E10);border:1px solid var(--line,#1E1E21);border-radius:14px;padding:12px 14px;margin:0 0 10px}}
    .nd-ch{{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;font-weight:600;color:var(--tx,{TX});margin-bottom:8px}}
    .nd-ch em{{font-style:normal;font-weight:500;font-size:11.5px;color:var(--tx3,{TX3})}}
    .nd-sec{{display:flex;justify-content:space-between;align-items:baseline;font-size:14px;font-weight:600;color:var(--tx,{TX});margin:10px 2px 8px}}
    .nd-sec em{{font-style:normal;font-weight:500;font-size:12px;color:var(--tx3,{TX3})}}
    .nd-pl{{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;padding:8px 0;border-bottom:1px solid #18181C;text-decoration:none !important}}
    .nd-pl:last-child{{border-bottom:0}}
    .nd-pl .s{{font-size:12.5px;font-weight:650;color:#F2F2F5}} .nd-pl .n{{font-size:11.5px;text-align:right;font-weight:600}}
    .nd-pl .h{{grid-column:1 / span 2;font-size:12px;line-height:1.45;color:var(--tx2,{TX2});overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}}
    a.nd-pl:hover .h{{color:#fff}}
    .nd-bar{{display:grid;grid-template-columns:110px 1fr 58px;gap:8px;align-items:center;font-size:12px;color:var(--tx2,{TX2});margin:7px 0}}
    .nd-bar .t{{height:6px;border-radius:6px;background:#1A1A1E;position:relative;overflow:hidden}} .nd-bar .t i{{position:absolute;top:0;height:100%;border-radius:6px}}
    .nd-chips{{display:flex;flex-wrap:wrap;gap:6px}}
    .nd-src{{display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:6px 0;border-bottom:1px solid #18181C;color:var(--tx2,{TX2})}}
    .nd-src:last-child{{border-bottom:0}}
    .nd-empty{{font-size:12.5px;color:var(--tx3,{TX3});padding:16px 4px;line-height:1.6}}
    .nd-note{{font-size:11.5px;color:var(--tx3,{TX3});margin:8px 2px}}
    </style>""", unsafe_allow_html=True)


def _href(u: str) -> str:
    return html.escape(u, quote=True) if u and urlparse(u).scheme in ("http", "https") else "#"


def _clock(dt) -> str:
    now = _now()
    return dt.strftime("%H:%M") if dt.date() == now.date() else dt.strftime("%d %b, %H:%M")


def _moves(symbols) -> dict:
    """Today's change in percent for the reader's stocks, from the app's own quote cache (no extra source)."""
    syms = tuple(dict.fromkeys(symbols))[:80]
    if not syms:
        return {}
    try:
        import data_engine as de
        q = de.get_quotes(syms) or {}
    except Exception:
        return {}
    out = {}
    for s_, v in q.items():
        try:
            c = float((v or {}).get("chg"))
        except (TypeError, ValueError):
            continue
        if c == c:
            out[s_] = c
    return out


def _story(a: dict, full: bool = False, current: str = "", moves: dict | None = None) -> str:
    sc = _SENT_COLOR[a["sentiment"]]
    ev = a["event"]
    pills = []
    if a.get("is_breaking"):
        pills.append(f'<span class="nd-pill solid" style="--c:{RED}">Breaking</span>')
    elif a["priority"] >= 70:
        pills.append(f'<span class="nd-pill" style="--c:{AMBER}">Important</span>')
    if a.get("hot"):
        pills.append(f'<span class="nd-pill" style="--c:{YELLOW}">Trending · {a["source_count"]} outlets</span>')
    if a.get("is_new"):
        pills.append(f'<span class="nd-pill" style="--c:{CYAN}">New</span>')
    if ev != "NEWS":
        pills.append(f'<span class="nd-pill" style="--c:{EVENT_COLORS.get(ev, TX3)}">{html.escape(EVENT_LABEL.get(ev, ev.capitalize()))}</span>')
    src = html.escape(a["source"]) + (f' and {a["source_count"] - 1} more' if a["source_count"] > 1 else "")
    ago = a["time_str"] + ("" if a["time_str"] == "now" or not a["time_str"][-1:] in "mh" else " ago")
    meta = (f'<span class="t">{_clock(a["pub_dt"])}</span><span class="dot">·</span><span>{html.escape(ago)}</span>'
            f'<span class="dot">·</span><span>{src}</span>')
    def _mv(s_):
        c = (moves or {}).get(s_)
        if c is None:
            return ""
        return f'<i style="font-style:normal;font-weight:600;margin-left:5px;color:{GREEN if c > 0.005 else RED if c < -0.005 else TX3}">{c:+.1f}%</i>'
    syms = "".join(f'<span class="nd-sym{" cur" if s == current else ""}" data-ak-sym="{html.escape(s)}" title="Open {html.escape(s)}">{html.escape(s)}{_mv(s)}</span>' for s in a["symbols"][:4])
    if not syms:
        syms = f'<span class="nd-sym">{"Macro" if a["kind"] == "macro" or a["macro"] else "Market"}</span>'
    tone = f'<span><span class="nd-tone-dot" style="--c:{sc}"></span>{TONE_WORD[a["sentiment"]]}</span>'
    amt = f'<span style="color:{GREEN if a["score"] >= 0 else RED};font-weight:600">₹{a["amount_cr"]:,.0f} Cr</span>' if a.get("amount_cr") else ""
    extra = " · ".join(html.escape(_tag_label(s)) for s in (a["sectors"][:2] + a["macro"][:2]))
    react = ""
    if full and current and current in a["symbols"] and a["kind"] != "macro":
        r = _reaction(current, a["pub_dt"])
        if r is not None:
            react = f'<span class="nd-pill" style="--c:{GREEN if r >= 0 else RED}">{html.escape(current)} {r:+.2f}% since</span>'
    summ = f'<div class="nd-x">{html.escape(a["summary"])}</div>' if full and a.get("summary") else ""
    return (f'<a class="nd-s {"full" if full else "compact"}" href="{_href(a["link"])}" target="_blank" rel="noopener noreferrer">'
            f'<div class="nd-m">{meta}{"".join(pills)}</div><div class="nd-h">{html.escape(a["title"])}</div>{summ}'
            f'<div class="nd-f">{syms}{tone}{amt}{f"<span>{extra}</span>" if extra else ""}{react}</div></a>')


def _stories(items, full=False, current="", empty="No stories in this view.", moves=None):
    if not items:
        st.markdown(f'<div class="nd-empty">{html.escape(empty)}</div>', unsafe_allow_html=True)
        return
    st.markdown('<div class="nd-root">' + "".join(_story(a, full, current, moves) for a in items) + "</div>", unsafe_allow_html=True)


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
    return "1" if s and any(s in d for d in list(_DOTS.values())) else ""


def render_news_rail(watchlist, label: str = "WATCHLIST", current_security: str | None = None, sector: str | None = None):
    """Compact right-rail news: tone, counts, view selector, top stories."""
    _ensure_news_state()
    _css()
    current = (current_security or st.session_state.get("active_security") or "").upper().strip()
    if st.session_state.get("page") != "security":
        current = (current_security or "").upper().strip()
    days = {"24 hours": 1, "3 days": 3, "7 days": 7}[_choice("Window", ["24 hours", "3 days", "7 days"], "nd_rwin3")]
    query = st.text_input("Search news", key="nd_rq", placeholder="Search news, stocks, sectors", label_visibility="collapsed")
    with st.spinner("Loading news…"):
        items = _mark_new(assemble(tuple(_universe(watchlist)), _names_from_session(), current, days))
    tone = news_tone(items)
    mine = sum(a["mine"] for a in items)
    tc = GREEN if tone > 0 else RED if tone < 0 else TX2
    st.markdown(
        f'<div class="nd-k"><div><b>Stories</b><span>{len(items)}</span></div>'
        f'<div><b>Mine</b><span>{mine}</span></div>'
        f'<div><b>Breaking</b><span style="color:{RED if any(a["is_breaking"] for a in items) else TX}">{sum(a["is_breaking"] for a in items)}</span></div>'
        f'<div><b>Tone</b><span style="color:{tc}">{tone:+d}</span></div></div>'
        f'<div class="nd-tone"><i style="left:{(tone + 100) / 2:.0f}%"></i></div><div class="nd-tl"><span>Negative</span><span>Positive</span></div>',
        unsafe_allow_html=True)
    modes = (["SECURITY"] if current else []) + MODES_RAIL
    with st.container(key="nd_rmode"):
        mode = _choice("News view", modes, "nd_rmode_v2", lambda m: current if m == "SECURITY" else MODE_LABEL.get(m, m.capitalize()), pills=True)
    shown = _view(items, mode, query, current)
    _stories(shown[:18], False, current, "No stories here yet. Add stocks to your watchlist or portfolio to get company news.")
    if st.button("Open news desk", key="nd_open_desk", use_container_width=True):
        st.session_state["page"] = "news"
        st.rerun()


news_panel = render_news_rail
news_box = render_news_rail


def _side_cards(items, universe):
    """Your stocks, sector tone, macro themes and source health, as soft cards."""
    pdf = pulse(items, universe)
    if pdf.empty or pdf["stories"].sum() == 0:
        body = '<div class="nd-empty" style="padding:4px 0">No stories name your stocks in this window.</div>'
        quiet = 0
    else:
        rows = []
        for r in pdf[pdf["stories"] > 0].head(14).to_dict("records"):
            c = GREEN if r["tone"] > 0.2 else RED if r["tone"] < -0.2 else TX3
            word = "positive" if r["tone"] > 0.2 else "negative" if r["tone"] < -0.2 else "mixed"
            rows.append(f'<a class="nd-pl" href="{_href(r["link"])}" target="_blank" rel="noopener noreferrer"><span class="s">{html.escape(r["symbol"])}</span>'
                        f'<span class="n" style="color:{c}">{r["stories"]} {"story" if r["stories"] == 1 else "stories"} · {word}</span>'
                        f'<span class="h">{html.escape(r["latest"])}{" · " + html.escape(r["age"]) if r["age"] else ""}</span></a>')
        body = "".join(rows)
        quiet = int((pdf["stories"] == 0).sum())
    out = [f'<div class="nd-card"><div class="nd-ch">Your stocks in the news<em>{len(universe)} tracked</em></div>{body}'
           + (f'<div class="nd-note">{quiet} tracked stocks had no news.</div>' if quiet else "") + "</div>"]
    stn = sector_tone(items)
    if stn:
        bars = []
        for name, n, t in stn[:10]:
            w = min(50, abs(t) / 3 * 50)
            c = GREEN if t >= 0 else RED
            pos_css = f"left:50%;width:{w:.0f}%" if t >= 0 else f"left:{50 - w:.0f}%;width:{w:.0f}%"
            bars.append(f'<div class="nd-bar"><span>{html.escape(_tag_label(name))}</span><div class="t"><i style="{pos_css};background:{c}"></i>'
                        f'<i style="left:50%;width:1px;background:#3A3A40;border-radius:0"></i></div><span style="color:{c};text-align:right">{n} · {t:+.1f}</span></div>')
        out.append('<div class="nd-card"><div class="nd-ch">Sector tone<em>stories · average tone</em></div>' + "".join(bars) + "</div>")
    mc = Counter(t for a in items for t in a["macro"]).most_common(8)
    if mc:
        out.append('<div class="nd-card"><div class="nd-ch">Macro themes</div><div class="nd-chips">'
                   + "".join(f'<span class="nd-pill" style="--c:{AMBER}">{html.escape(_tag_label(k))} · {v}</span>' for k, v in mc) + "</div></div>")
    health = source_health()
    live = sum(1 for h in health if h["ok"])
    out.append(f'<div class="nd-card"><div class="nd-ch">Sources<em>{live} of {len(health)} live</em></div>' + "".join(
        f'<div class="nd-src"><span><span class="nd-tone-dot" style="--c:{GREEN if h["ok"] else RED}"></span>{html.escape(h["name"])}</span>'
        f'<span style="color:{TX3 if h["ok"] else RED}">{str(h["n"]) + " items" if h["ok"] else "not reachable (" + html.escape(h["err"] or "failed") + ")"}'
        f'{" · " + str(h["fail"]) + " failed" if h["ok"] and h["fail"] else ""}</span></div>' for h in health) + "</div>")
    return out


def _desk(watchlist=None, on_open=None, current_security: str | None = None):
    """The news desk body. Runs as a fragment: changing a view redraws only the desk, and it refreshes by itself."""
    current = (current_security or st.session_state.get("active_security") or "").upper().strip()
    universe = _universe(watchlist)
    phone = _phone()
    st.markdown(f'<div class="nd-sec" style="font-size:20px;margin-top:4px">News desk<em>{_now():%a %d %b, %H:%M} IST · updates by itself every '
                f'{AUTO_REFRESH_S // 60} minutes</em></div>', unsafe_allow_html=True)
    if phone:
        days = WINDOWS[_choice("Window", list(WINDOWS), "nd_fwin2")]
        query = st.text_input("Search", key="nd_fq", placeholder="Search: RBI, order, HDFCBANK, pharma…", label_visibility="collapsed")
        s1, s2 = st.columns(2, gap="small")
        with s1:
            sort = _choice("Sort", ["Priority", "Newest"], "nd_fsort2")
        refresh = s2.button("Refresh", key="nd_refresh", use_container_width=True)
    else:
        c1, c2, c3, c4 = st.columns([1.5, 2.4, 1.1, 0.7], gap="small")
        with c1:
            days = WINDOWS[_choice("Window", list(WINDOWS), "nd_fwin2")]
        query = c2.text_input("Search", key="nd_fq", placeholder="Search headlines, stocks, sectors, sources: RBI, order, HDFCBANK, pharma…", label_visibility="collapsed")
        with c3:
            sort = _choice("Sort", ["Priority", "Newest"], "nd_fsort2")
        refresh = c4.button("Refresh", key="nd_refresh", use_container_width=True)
    if refresh:
        _MEMO.clear()
        _rss_cached.clear()
        _COOL.clear()
    with st.spinner("Gathering the news…"):
        items = _mark_new(assemble(tuple(universe), _names_from_session(), current, days))
    if not items:
        st.markdown('<div class="nd-empty">No stories yet; some sources may be slow. Press Refresh in a few seconds.</div>', unsafe_allow_html=True)
        return

    moves = _moves(universe)
    tone = news_tone(items)
    pos, neg = sum(a["score"] > 0 for a in items), sum(a["score"] < 0 for a in items)
    health = source_health()
    live = sum(1 for h in health if h["ok"])
    breaking = [a for a in items if a["is_breaking"] or (a["priority"] >= 70 and a["age_min"] <= 180)][:12]
    if breaking:
        tape = "".join(f'<b>{html.escape(", ".join(a["symbols"][:2]) or EVENT_LABEL.get(a["event"], "News"))}</b>{html.escape(a["title"])}' for a in breaking)
        st.markdown(f'<div class="nd-tape"><span class="lab">Breaking</span><div class="run"><div>{tape}{tape}</div></div></div>', unsafe_allow_html=True)
    tc = GREEN if tone > 0 else RED if tone < 0 else TX
    kp = lambda l, v, s: f'<div class="nd-kp"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
    cards = [kp("Stories", len(items), f"{sum(a['is_new'] for a in items)} new since you last looked"),
             kp("Your stocks", sum(a["mine"] for a in items), f"stories across {len(universe)} tracked stocks"),
             kp("News tone", f'<span style="color:{tc}">{tone:+d}</span>', f'<span style="color:{GREEN}">{pos} positive</span> · <span style="color:{RED}">{neg} negative</span>'),
             kp("Breaking", f'{sum(a["is_breaking"] for a in items)}', f'{sum(a["hot"] for a in items)} trending (3+ outlets)')]
    if not phone:
        cards += [kp("Filings", sum(a["kind"] == "filing" for a in items), "NSE and BSE exchange filings"),
                  kp("Sources live", f"{live} of {len(health)}", "details in Sources")]
    st.markdown('<div class="nd-kpis">' + "".join(cards) + "</div>", unsafe_allow_html=True)

    def wire():
        modes = (["SECURITY"] if current else []) + MODES_FULL
        with st.container(key="nd_fmode"):
            mode = _choice("Desk view", modes, "nd_fmode_v2", lambda m: current if m == "SECURITY" else MODE_LABEL.get(m, m.capitalize()), pills=True)
        shown = _view(items, mode, query, current, moves)
        if sort == "Newest":
            shown = sorted(shown, key=lambda a: a["pub_dt"], reverse=True)
        title = current if mode == "SECURITY" else MODE_LABEL.get(mode, mode.capitalize())
        st.markdown(f'<div class="nd-sec">{html.escape(title)}<em>{len(shown)} {"story" if len(shown) == 1 else "stories"}</em></div>', unsafe_allow_html=True)
        empty = ("None of your stocks with news moved 2% or more today." if mode == "MOVERS"
                 else "No story is carried by three or more outlets in this window." if mode == "DEVELOPING"
                 else "Nothing in this view for the selected window.")
        _stories(shown[:45], True, current, empty, moves)
        tagged = list(dict.fromkeys(s for a in shown[:45] for s in a["symbols"]))
        if on_open and tagged:
            o1, o2 = st.columns([3, 1], gap="small")
            pick = o1.selectbox("Open a stock from these stories", tagged, key="nd_open_pick", label_visibility="collapsed")
            if o2.button("Open stock", key="nd_open_btn", use_container_width=True, type="primary"):
                on_open(pick)

    side = _side_cards(items, universe)
    if phone:
        part = _choice("Section", ["Stories", "Your stocks", "Sectors & macro", "Sources"], "nd_phone_part")
        if part == "Stories":
            wire()
        elif part == "Your stocks":
            st.markdown(side[0], unsafe_allow_html=True)
        elif part == "Sources":
            st.markdown(side[-1], unsafe_allow_html=True)
        else:
            st.markdown("".join(side[1:-1]) or '<div class="nd-empty">Not enough sector-tagged stories yet.</div>', unsafe_allow_html=True)
    else:
        left, right = st.columns([2.35, 1], gap="medium")
        with left:
            wire()
        with right:
            st.markdown("".join(side), unsafe_allow_html=True)
    st.caption("Tone and story types are tagged automatically from the headline wording. Treat them as a reading aid, not a recommendation. Tap any story to read it at the source.")


_desk_frag = st.fragment(run_every=AUTO_REFRESH_S)(_desk) if hasattr(st, "fragment") else _desk


def render_news_terminal(watchlist=None, on_open=None, current_security: str | None = None):
    """Full news desk: breaking strip, summary cards, the wire with views, your stocks, sector tone, sources."""
    _ensure_news_state()
    _css()
    _desk_frag(watchlist, on_open, current_security)
