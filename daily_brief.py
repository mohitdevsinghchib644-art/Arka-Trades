"""
daily_brief.py — Arka Trades · Daily brief (morning edition), v1

What it is
  A morning report written before the 09:15 IST open: what happened overnight and what can move Indian stocks today.
    1  Bottom line            three points that matter for today
    2  Global session wrap    US close, Europe, Asia this morning (numbers from the price feed, commentary by the AI)
    3  India setup            how the last session ended and what the day looks like
    4  Commodities, currencies and rates
    5  Stories                5A deep (four to six developing stories: what happened, why it matters, what to watch
                              next and when)   5B on the radar (ten to fifteen one-liners with an implication tag)
    6  Rapid fire             eight to ten sourced one-liners: earnings, deals, IPOs, flows, rating changes
    7  On the calendar        scheduled events named in the news

Two ways it runs, from the same code
  * In the app:      render_brief(supabase, on_open)  -> the "Daily brief" page.
  * On a schedule:   python daily_brief.py --send     -> writes the brief and sends it to Telegram
                     (run by .github/workflows/daily-brief.yml at about 08:00 IST on weekdays).
  If the Supabase table `arka_briefs` exists, both share one brief: the scheduled job writes it and the app shows the
  same text instantly. Without the table each side keeps its own copy in a local folder and everything still works.

How it stays honest
  * Prices and percentage moves come from the price feed (Yahoo Finance), never from the AI.
  * The AI is given the price table and about 150 fresh headlines, each with an id. Every story, radar item and
    rapid-fire item must cite the ids it rests on; items that cite nothing are deleted before you see them.
  * Each item links to the articles it came from.
  * "What changed": the stories of the last few briefs are passed back in, so a continuing story is written as an
    update rather than explained again.

Privacy
  * Only public information is sent to the AI (index prices and news headlines). Your holdings and watchlist are
    never sent anywhere: the "in your book" marks are worked out inside the app after the brief is written.
  * Secrets are read by NAME from Streamlit secrets or environment variables and are never written to logs:
      GEMINI_KEY (already used by Arka AI) · TELEGRAM_BOT_TOKEN · TELEGRAM_CHAT_ID (scheduled job only)
      optional: SUPABASE_URL · SUPABASE_KEY (to share the brief) · BRIEF_GEMINI_MODEL · BRIEF_WEB_SEARCH=on

Needs: requests, yfinance, pandas, google-genai (all already in the app's requirements). No feedparser needed here.
"""
from __future__ import annotations

import html
import importlib
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests

VERSION = 1
IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TABLE = "arka_briefs"
KIND = "am"


class BriefError(RuntimeError):
    """A problem the reader can act on (missing key, no headlines, the AI returned nothing usable)."""


# ═════════════════════════ settings and secrets ═════════════════════════
def _cfg(name: str, default: str = "") -> str:
    """Environment variable first (scheduled job), then Streamlit secrets (the app). Values are never logged."""
    v = os.environ.get(name)
    if v and v.strip():
        return v.strip()
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "").strip() or default
    except Exception:
        return default


_SECRET_NAMES = ("GEMINI_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_KEY", "SUPABASE_URL")


def _safe_err(e) -> str:
    """Error text that is safe to show or log: secret values and bot tokens inside URLs are masked."""
    msg = f"{type(e).__name__}: {e}"
    for n in _SECRET_NAMES:
        v = _cfg(n)
        if v and len(v) >= 6:
            msg = msg.replace(v, "***")
    msg = re.sub(r"bot\d+:[\w-]+", "bot***", msg)
    msg = re.sub(r"(key|token|apikey)=[^&\s'\"]+", r"\1=***", msg, flags=re.I)
    return msg[:300]


def _now() -> datetime:
    return datetime.now(IST)


# ═════════════════════════ 1. market snapshot ═════════════════════════
# (group, name, Yahoo tickers, kind, label in home_page.market_data)   kind: idx · vol · usd · fx · yld · cry
SNAP = [
    ("India", "NIFTY 50", ("^NSEI",), "idx", "NIFTY 50"), ("India", "SENSEX", ("^BSESN",), "idx", "SENSEX"),
    ("India", "BANK NIFTY", ("^NSEBANK",), "idx", "BANK NIFTY"),
    ("India", "NIFTY MIDCAP 100", ("^CRSMID", "NIFTY_MIDCAP_100.NS"), "idx", "NIFTY MIDCAP 100"),
    ("India", "India VIX", ("^INDIAVIX",), "vol", "INDIA VIX"),
    ("United States", "S&P 500", ("^GSPC",), "idx", "S&P 500"), ("United States", "Nasdaq Composite", ("^IXIC",), "idx", "NASDAQ COMP"),
    ("United States", "Dow Jones", ("^DJI",), "idx", "DOW JONES"), ("United States", "Russell 2000", ("^RUT",), "idx", "RUSSELL 2000"),
    ("United States", "CBOE VIX", ("^VIX",), "vol", "CBOE VIX"),
    ("Europe", "FTSE 100", ("^FTSE",), "idx", "FTSE 100"), ("Europe", "DAX", ("^GDAXI",), "idx", "DAX"),
    ("Europe", "CAC 40", ("^FCHI",), "idx", "CAC 40"), ("Europe", "Euro Stoxx 50", ("^STOXX50E",), "idx", "EURO STOXX 50"),
    ("Asia", "Nikkei 225", ("^N225",), "idx", "NIKKEI 225"), ("Asia", "Hang Seng", ("^HSI",), "idx", "HANG SENG"),
    ("Asia", "Shanghai Composite", ("000001.SS",), "idx", "SHANGHAI COMP"), ("Asia", "KOSPI", ("^KS11",), "idx", "KOSPI"),
    ("Asia", "Taiwan TAIEX", ("^TWII",), "idx", "TAIEX"), ("Asia", "ASX 200", ("^AXJO",), "idx", "ASX 200"),
    ("Commodities", "Brent crude", ("BZ=F",), "usd", "BRENT CRUDE"), ("Commodities", "WTI crude", ("CL=F",), "usd", "WTI CRUDE"),
    ("Commodities", "Gold", ("GC=F",), "usd", "GOLD"), ("Commodities", "Silver", ("SI=F",), "usd", "SILVER"),
    ("Commodities", "Copper", ("HG=F",), "usd", "COPPER"), ("Commodities", "Natural gas", ("NG=F",), "usd", "NATURAL GAS"),
    ("Currencies and rates", "USD / INR", ("INR=X",), "fx", "USD / INR"),
    ("Currencies and rates", "Dollar index", ("DX-Y.NYB",), "fx", "DOLLAR INDEX"),
    ("Currencies and rates", "US 10-year yield", ("^TNX",), "yld", "US 10Y"),
    ("Currencies and rates", "Bitcoin", ("BTC-USD",), "cry", "BITCOIN"),
]
GROUPS = ["India", "United States", "Europe", "Asia", "Commodities", "Currencies and rates"]


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _snapshot_home() -> list:
    """Inside the app: reuse the Home page's cached quotes, so nothing extra is downloaded."""
    hp = sys.modules.get("home_page") or importlib.import_module("home_page")
    M = hp.market_data() or {}
    rows = []
    for g, name, _, kind, key in SNAP:
        m = M.get(key)
        if not m or _num(m.get("price")) is None:
            continue
        try:
            day = m["date"].strftime("%Y-%m-%d")
        except Exception:
            day = ""
        rows.append({"group": g, "name": name, "kind": kind, "last": _num(m.get("price")), "chg": _num(m.get("chg")),
                     "pts": _num(m.get("pts")), "w1": _num(m.get("w1")), "date": day})
    return rows


def _snapshot_yf() -> list:
    """Stand-alone (scheduled job): one batched Yahoo download for all instruments."""
    try:
        hp = sys.modules.get("home_page")
        yf = hp._lock_yfinance() if hp is not None and hasattr(hp, "_lock_yfinance") else importlib.import_module("yfinance")
    except Exception as e:
        raise BriefError(f"yfinance is not available ({_safe_err(e)})")
    tickers = sorted({t for _, _, chain, _, _ in SNAP for t in chain})
    raw = yf.download(tickers, period="1mo", interval="1d", group_by="ticker", progress=False, threads=False, auto_adjust=False)
    rows = []
    for g, name, chain, kind, _ in SNAP:
        for t in chain:
            try:
                s = raw[t]["Close"].dropna()
                s = s[s > 0]
                if len(s) < 2:
                    continue
                last, prev, d = float(s.iloc[-1]), float(s.iloc[-2]), s.index[-1]
                back = s[s.index <= d - timedelta(days=7)]
                w1 = (last / float(back.iloc[-1]) - 1) * 100 if len(back) else None
                rows.append({"group": g, "name": name, "kind": kind, "last": last, "chg": (last / prev - 1) * 100,
                             "pts": last - prev, "w1": w1, "date": d.strftime("%Y-%m-%d")})
                break
            except Exception:
                continue
    return rows


def snapshot(prefer_home: bool = True) -> list:
    if prefer_home:
        try:
            rows = _snapshot_home()
            if len(rows) >= 10:
                return rows
        except Exception:
            pass
    return _snapshot_yf()


def _f_last(r) -> str:
    v, k = r.get("last"), r.get("kind")
    if v is None:
        return "—"
    if k == "yld":
        return f"{v:.2f}%"
    if k in ("fx", "vol"):
        return f"{v:,.2f}"
    if k == "cry":
        return f"${v:,.0f}"
    if k == "usd":
        return f"${v:,.2f}"
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def _f_chg(r) -> str:
    if r.get("kind") == "yld":
        return "—" if r.get("pts") is None else f"{r['pts'] * 100:+.0f} bp"
    c = r.get("chg")
    return "—" if c is None else ("0.00%" if round(c, 2) == 0 else f"{c:+.2f}%")


def _move(r):
    """The number whose sign colours a row (points for yields, percent for everything else)."""
    return r.get("pts") if r.get("kind") == "yld" else r.get("chg")


def _table_text(rows: list, today: date) -> str:
    out = []
    for g in GROUPS:
        part = [r for r in rows if r["group"] == g]
        if not part:
            continue
        out.append(g.upper())
        for r in part:
            w1 = "" if r.get("w1") is None or r["kind"] == "yld" else f", 1 week {r['w1']:+.1f}%"
            stamp = ""
            if r.get("date"):
                try:
                    d = date.fromisoformat(r["date"])
                    stamp = " (today, live)" if d == today else f" (close of {d:%a %d %b})"
                except ValueError:
                    pass
            out.append(f"  {r['name']}: {_f_last(r).replace('$', 'USD ')}, 1 day {_f_chg(r)}{w1}{stamp}")
    return "\n".join(out)


# ═════════════════════════ 2. headlines ═════════════════════════
FEEDS = [  # (source, url, desk)   desk: IN = India, GL = global
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "IN"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms", "IN"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss", "IN"),
    ("Business Standard", "https://www.business-standard.com/rss/companies-101.rss", "IN"),
    ("Mint", "https://www.livemint.com/rss/markets", "IN"),
    ("Mint", "https://www.livemint.com/rss/companies", "IN"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml", "IN"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml", "IN"),
    ("BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss", "IN"),
    ("NDTV Profit", "https://feeds.feedburner.com/ndtvprofit-latest", "IN"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "GL"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", "GL"),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml", "GL"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "GL"),
]
SEARCHES = [  # Google News searches (query, desk)
    ("Nifty OR Sensex stock market", "IN"), ("RBI OR rupee OR bond yields India", "IN"), ("FII OR FPI OR DII flows Indian equities", "IN"),
    ("SEBI OR NSE OR BSE new rule OR circular", "IN"), ("quarterly results net profit India company shares", "IN"),
    ("IPO India listing OR subscription OR DRHP", "IN"), ("brokerage upgrade OR downgrade target price India stock", "IN"),
    ("India inflation OR GDP OR GST OR fiscal policy", "IN"), ("order win OR stake sale OR acquisition Indian company", "IN"),
    ("Wall Street stocks close S&P 500 Nasdaq", "GL"), ("Federal Reserve rate OR Treasury yields", "GL"),
    ("earnings beat OR miss shares jump OR fall", "GL"), ("merger OR acquisition deal announced billion", "GL"),
    ("IPO pricing OR filing raises", "GL"), ("oil prices Brent OPEC", "GL"), ("gold OR copper OR silver prices", "GL"),
    ("China economy OR stimulus OR yuan OR property", "GL"), ("Asian markets Nikkei Hang Seng", "GL"),
    ("European stocks ECB OR Bank of England", "GL"), ("tariffs OR trade deal OR sanctions markets", "GL"),
    ("yen OR dollar Bank of Japan", "GL"), ("ETF OR fund flows inflows OR outflows", "GL"),
    ("semiconductor OR AI chip stocks", "GL"), ("analyst upgrade OR downgrade large cap", "GL"),
]
_STOP = set("a an the of to in on for and or as at by with from is are was be its it this that after over into amid vs per new says say "
            "said will may us up down".split())
_TAG_RX = re.compile(r"<[^>]+>")
_ATOM = "{http://www.w3.org/2005/Atom}"


def _gnews_url(query: str, days: int, desk: str) -> str:
    loc = "hl=en-IN&gl=IN&ceid=IN:en" if desk == "IN" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(f'{query} when:{max(1, int(days))}d')}&{loc}"


def _text(s, n=0) -> str:
    s = html.unescape(_TAG_RX.sub(" ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return (s[: n - 1].rstrip() + "…") if n and len(s) > n else s


def _when(raw):
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw.strip())
    except Exception:
        try:
            d = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(IST)


def parse_feed(xml_bytes: bytes, source: str, desk: str, google: bool = False) -> list:
    """RSS 2.0 or Atom -> [{title, link, source, summary, dt, desk}]. Returns [] on anything unparsable."""
    if not xml_bytes or len(xml_bytes) > 3_000_000 or b"<!ENTITY" in xml_bytes[:4000]:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return []
    out = []
    items = list(root.iter("item"))
    if items:
        for it in items:
            title, link = _text(it.findtext("title")), (it.findtext("link") or "").strip()
            src = _text(it.findtext("source")) or source
            if google and src and title.endswith(" - " + src):
                title = title[: -len(src) - 3].rstrip()
            summary = "" if google else _text(it.findtext("description"), 220)
            out.append({"title": title, "link": link, "source": src, "summary": summary, "dt": _when(it.findtext("pubDate")), "desk": desk})
    else:
        for it in root.iter(_ATOM + "entry"):
            link = ""
            for l in it.iter(_ATOM + "link"):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href") or ""
                    break
            out.append({"title": _text(it.findtext(_ATOM + "title")), "link": link, "source": source,
                        "summary": _text(it.findtext(_ATOM + "summary"), 220),
                        "dt": _when(it.findtext(_ATOM + "updated") or it.findtext(_ATOM + "published")), "desk": desk})
    return [x for x in out if x["title"] and len(x["title"]) >= 18]


def _get(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}, timeout=9)
    r.raise_for_status()
    return r.content


def _sig(title: str) -> tuple:
    """(words, first word, numbers) of a headline, for spotting the same story told by two publishers."""
    words = [w for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", title.lower()) if w not in _STOP and len(w) > 1]
    return frozenset(words), (words[0] if words else ""), frozenset(w for w in words if w[0].isdigit())


def _tokens(title: str) -> frozenset:
    return _sig(title)[0]


def _same(a: tuple, b: tuple) -> bool:
    """Same story? Most words shared, the subject (first word) of each appears in the other, and if both quote
    numbers they share one. Deliberately strict: 'TCS Q2 profit rises 9%' and 'Infosys Q2 profit rises 12%' differ."""
    ta, fa, na = a
    tb, fb, nb = b
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter < 3 or inter / min(len(ta), len(tb)) < 0.7 or inter / len(ta | tb) < 0.5:
        return False
    if fa not in tb or fb not in ta:
        return False
    return not (na and nb and not (na & nb))


def collect_headlines(now: datetime | None = None, hours: int | None = None, limit: int = 150, fetch=None) -> tuple:
    """-> (headlines, stats). Headlines are de-duplicated, ranked (how many sources carry the story, how fresh) and
    numbered: [{id, title, summary, source, link, age_h, n_src, desk}]."""
    now = now or _now()
    if hours is None:                     # Monday reads the weekend too; Saturday and Sunday read two days
        hours = 66 if now.weekday() == 0 else 48 if now.weekday() >= 5 else 26
    days = max(1, math.ceil(hours / 24))
    fetch = fetch or _get
    jobs = [(src, url, desk, False) for src, url, desk in FEEDS] + [("Google News", _gnews_url(q, days, desk), desk, True) for q, desk in SEARCHES]

    def one(job):
        src, url, desk, google = job
        try:
            return parse_feed(fetch(url), src, desk, google)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, jobs))
    ok = sum(1 for r in results if r)
    cutoff = now - timedelta(hours=hours)
    raw = []
    for r in results:
        for x in r or []:
            if x["dt"] is None or cutoff <= x["dt"] <= now + timedelta(minutes=30):
                raw.append(x)
    clusters = []                          # [{"tok", "items": [...]}]
    for x in sorted(raw, key=lambda v: v["dt"] or cutoff, reverse=True):
        tok = _sig(x["title"])
        for c in clusters:
            if _same(tok, c["tok"]):
                c["items"].append(x)
                break
        else:
            clusters.append({"tok": tok, "items": [x]})
    heads = []
    for c in clusters:
        its = c["items"]
        best = max(its, key=lambda v: (bool(v["summary"]), v["dt"] or cutoff))
        dt = max((v["dt"] for v in its if v["dt"]), default=None)
        age = (now - dt).total_seconds() / 3600 if dt else float(hours)
        n_src = len({v["source"] for v in its})
        score = 2.0 * min(n_src, 6) + (2 if age < 6 else 1 if age < 14 else 0) + (0.5 if best["summary"] else 0)
        heads.append({"title": best["title"], "summary": best["summary"], "source": best["source"], "link": best["link"],
                      "age_h": round(max(0.0, age), 1), "n_src": n_src, "desk": best["desk"], "_s": score})
    heads.sort(key=lambda h: (-h["_s"], h["age_h"]))
    india, world = [h for h in heads if h["desk"] == "IN"], [h for h in heads if h["desk"] != "IN"]
    half = limit // 2
    pick = india[:half] + world[:half]
    rest = sorted(india[half:] + world[half:], key=lambda h: (-h["_s"], h["age_h"]))
    pick += rest[: max(0, limit - len(pick))]
    pick.sort(key=lambda h: (h["desk"] != "IN", -h["_s"], h["age_h"]))
    for i, h in enumerate(pick, 1):
        h["id"] = i
        h.pop("_s", None)
    stats = {"headlines": len(pick), "seen": len(raw), "sources": len({h["source"] for h in pick}), "feeds_ok": ok, "feeds": len(jobs),
             "hours": hours}
    return pick, stats


def _heads_text(heads: list) -> str:
    out, desk = [], None
    for h in heads:
        if h["desk"] != desk:
            desk = h["desk"]
            out.append("INDIA DESK" if desk == "IN" else "GLOBAL DESK")
        extra = f", {h['n_src']} sources" if h["n_src"] > 1 else ""
        tail = f" — {h['summary']}" if h["summary"] else ""
        out.append(f"[{h['id']}] ({h['source']}, {h['age_h']:.0f}h ago{extra}) {h['title']}{tail}")
    return "\n".join(out)


# ═════════════════════════ 3. the writer ═════════════════════════
SYSTEM = """You write the morning market brief for an Indian equity trader who swing-trades NSE stocks. It is read before the 09:15 IST open. The reader wants to know what happened and what can move Indian stocks today. Write plain, direct English. No hype, no greetings, no advice to buy or sell.

RULES OF EVIDENCE
- Facts, numbers, names and dates must come from the MARKET TABLE or the HEADLINES you are given. Background knowledge may explain why something matters; it must never add a new number, quote or event.
- Index levels and percentage moves must match the MARKET TABLE exactly. If the table says it is unavailable, quote only figures that appear in the headlines.
- Every deep story, radar item and rapid-fire item must list the headline ids it rests on in "src", for example [12, 40]. Items without ids are deleted before the reader sees them.
- If the headlines do not support the requested number of items, write fewer. Never pad, never repeat an item in two sections.
- Prefer stories carried by several sources and stories that are developing over several days to one-session noise.

SECTIONS
bottom_line: exactly three points, at most 28 words each, on what matters most for Indian equities today and which way it leans.
global: "us", "europe", "asia": two or three sentences each on the session (Asia is this morning's trade), led by what drove it.
india: two or three sentences on how the last NSE session ended and the setup for today.
commodities: two to four sentences on crude, gold, metals, the rupee, the dollar and yields, with the read-through for Indian sectors.

deep (5A): four to six developing stories of roughly 80 to 120 words each across "what", "why" and "watch".
  what  = what happened.
  why   = why it matters beyond the headline.
  watch = what to watch next, and when (give the date or event if the headlines give one).
  If a story appeared in PREVIOUS BRIEFS, set "update": true and put in "changed" one sentence on what specifically changed since then; do not re-explain the setup.
  Cover a mix: single-company situations, sector shifts, macro and policy, and structural changes in market plumbing, regulation or capital flows. At least two must be about India and at least one about a market outside the United States.

radar (5B): ten to fifteen further items the reader should know about that do not deserve a paragraph.
  "line" = one statement of what happened, under 20 words.
  "tag"  = an implication of 2 to 6 words that states a directional or actionable consequence and does not restate the item. Use tags of these kinds:
    Directional: "Bearish INR", "Supportive for gold", "Headwind, IT exporters"
    Second-order: "Margin pressure, paints", "Refinancing risk, NBFCs"
    Watch-item: "Watch RBI, 5 Dec", "Confirms if TCS beats"
    Read-through: "Read-through to HDFC Bank", "Signals broader capex cut"
    Null: "Noise for now" when there is genuinely no implication yet.

rapid (6): eight to ten one-line items: earnings that reported and whether they beat or missed, M&A and deal news, IPO filings and pricings, major fund flows, analyst rating changes on large caps, and anything else worth knowing but not worth a paragraph. "type" is one of earnings, deal, ipo, flows, rating, other.

today: up to six scheduled events for today or the next few days that the headlines mention (results, data releases, central bank meetings, expiries, listings), each as "when · what".

"nse": NSE trading symbols of listed Indian companies directly affected by an item (for example "TCS", "HDFCBANK"), only when you are sure of the symbol; otherwise an empty list.

OUTPUT
Return one JSON object and nothing else, with exactly this shape:
{"bottom_line": ["", "", ""],
 "global": {"us": "", "europe": "", "asia": ""},
 "india": "",
 "commodities": "",
 "deep": [{"title": "", "kind": "company|sector|macro|plumbing", "region": "India|US|Europe|Asia|Global", "what": "", "why": "", "watch": "", "update": false, "changed": "", "nse": [], "src": []}],
 "radar": [{"line": "", "tag": "", "nse": [], "src": []}],
 "rapid": [{"line": "", "type": "", "nse": [], "src": []}],
 "today": [""]}"""

_MODEL_RX = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash$")
_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-flash-latest"]


def build_prompt(rows: list, heads: list, previous: list, now: datetime) -> str:
    edition = ("Weekend edition: markets are closed today; cover the week's close and what to watch on Monday."
               if now.weekday() >= 5 else "Monday edition: the headlines also cover the weekend." if now.weekday() == 0 else "Weekday edition.")
    prev = []
    for b in previous[:4]:
        lines = [f"  - {s.get('title', '')}: {_text(s.get('what', ''), 170)} | watch: {_text(s.get('watch', ''), 110)}" for s in b.get("deep", [])]
        if lines:
            prev.append(f"{b.get('day', '')}\n" + "\n".join(lines))
    return (f"NOW: {now:%A %d %B %Y, %H:%M} IST. {edition}\n\n"
            f"MARKET TABLE (from the price feed)\n{_table_text(rows, now.date()) or '  unavailable today'}\n\n"
            f"PREVIOUS BRIEFS (deep stories already told this week)\n{chr(10).join(prev) if prev else '  none'}\n\n"
            f"HEADLINES (id, source, age, how many sources carry it)\n{_heads_text(heads)}\n")


def _client():
    key = _cfg("GEMINI_KEY")
    if not key:
        raise BriefError("GEMINI_KEY is not set, so the brief cannot be written.")
    try:
        from google import genai
        from google.genai import types
    except Exception:
        raise BriefError("The google-genai package is missing (add `google-genai` to requirements.txt).")
    # v1beta on purpose: on the "v1" address Google rejects the system instruction and the "-latest" model names.
    client = None
    for opts in ({"api_version": "v1beta", "timeout": 240_000}, {"api_version": "v1beta"}, {}):
        try:
            client = genai.Client(api_key=key, http_options=types.HttpOptions(**opts)) if opts else genai.Client(api_key=key)
            break
        except Exception:
            continue
    if client is None:
        raise BriefError("The Gemini client could not be started.")
    return client, types


def _thinking_variants(types) -> list:
    """Ways to ask for a quick answer, newest first. Newer models take a "thinking level", older ones a token budget;
    None means the model's own default (slowest, the last resort)."""
    out = []
    for kw in ({"thinking_level": "low"}, {"thinking_budget": 1024}):
        try:
            out.append(types.ThinkingConfig(**kw))
        except Exception:
            pass
    return out + [None]


def _models(client) -> list:
    names = []
    try:
        names = [str(m.name).split("/")[-1] for m in client.models.list()]
    except Exception:
        pass
    stable = sorted({n for n in names if _MODEL_RX.match(n)}, key=lambda n: float(_MODEL_RX.match(n).group(1)), reverse=True)
    out = []
    for n in [_cfg("BRIEF_GEMINI_MODEL"), _cfg("ARKA_GEMINI_MODEL")] + stable[:2] + _FALLBACK_MODELS:
        if n and n not in out:
            out.append(n)
    return out


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in the reply")
    return json.loads(t[a:b + 1])


def _web_sources(resp) -> list:
    out = []
    try:
        for ch in resp.candidates[0].grounding_metadata.grounding_chunks or []:
            web = getattr(ch, "web", None)
            if web is not None and getattr(web, "uri", None):
                out.append({"title": str(getattr(web, "title", "") or "Web"), "url": str(web.uri)})
    except Exception:
        pass
    return out[:20]


def ask_model(prompt: str, progress=None, backend=None, budget_s: float = 420.0) -> tuple:
    """-> (data, model name, mode, web sources). Tries the newest flash model first, then falls back.
    For each model it first asks for light "thinking" (a long think is what makes a request time out); if the model
    refuses that setting it asks again without it. A busy server gets one retry; a timeout moves on to the next model."""
    client, types = backend or _client()
    want_search = _cfg("BRIEF_WEB_SEARCH").lower() in ("on", "1", "true", "yes")
    variants = _thinking_variants(types)
    errors, t0 = [], time.time()
    for model in _models(client):
        for use_search in ([True, False] if want_search else [False]):
            next_model = False
            for think in variants:
                if next_model:
                    break
                for attempt in (1, 2):
                    if time.time() - t0 > budget_s:
                        raise BriefError("The AI took too long to answer. Try again in a few minutes. " + " | ".join(errors[-2:]))
                    try:
                        kw = {"system_instruction": SYSTEM, "temperature": 0.3}
                        if think is not None:
                            kw["thinking_config"] = think
                        if use_search:
                            kw["tools"] = [types.Tool(google_search=types.GoogleSearch())]
                        else:
                            kw["response_mime_type"] = "application/json"
                        if progress:
                            progress(f"Writing with {model}" + (" and web search" if use_search else ""))
                        resp = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(**kw))
                        data = _parse_json(getattr(resp, "text", "") or "")
                        if not isinstance(data, dict) or not data.get("deep"):
                            raise ValueError("the reply has no stories")
                        return data, model, ("wire + web search" if use_search else "wire"), (_web_sources(resp) if use_search else [])
                    except Exception as e:
                        msg = _safe_err(e)
                        errors.append(f"{model}: {msg[:160]}")
                        low = msg.lower()
                        if attempt == 1 and any(k in msg for k in ("503", "429", "UNAVAILABLE", "overloaded", "RESOURCE_EXHAUSTED")):
                            time.sleep(8)
                            continue                              # busy: the same request once more
                        if think is not None and ("400" in msg or "invalid_argument" in low or "thinking" in low):
                            break                                 # the model refused this setting: next variant
                        next_model = True                         # timeout or anything else: next model
                        break
    raise BriefError("The AI did not return a usable brief. " + " | ".join(errors[-3:]))


# ═════════════════════════ 4. checking what the AI wrote ═════════════════════════
_SYM_RX = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{1,19}$")
_KINDS = {"company": "Company", "sector": "Sector", "macro": "Macro and policy", "plumbing": "Market structure"}
_TYPES = {"earnings": "Earnings", "deal": "Deal", "ipo": "IPO", "flows": "Flows", "rating": "Rating", "other": "Also"}


def _words(s: str) -> int:
    return len(re.findall(r"\S+", s or ""))


def _srcs(ids, by_id: dict) -> list:
    out, seen = [], set()
    for i in ids if isinstance(ids, list) else []:
        try:
            h = by_id.get(int(i))
        except (TypeError, ValueError):
            h = None
        if h and h["id"] not in seen:
            seen.add(h["id"])
            out.append({"name": h["source"], "url": h["link"], "title": h["title"]})
    return out[:4]


def _syms(v) -> list:
    out = []
    for s in v if isinstance(v, list) else []:
        s = str(s).upper().strip().replace(".NS", "")
        if _SYM_RX.match(s) and s not in out:
            out.append(s)
    return out[:5]


def _s(v, n=0) -> str:
    return _text(v if isinstance(v, str) else "", n)


def _l(v) -> list:
    return v if isinstance(v, list) else []


def clean(data: dict, heads: list) -> tuple:
    """Keep only well-formed, sourced items. -> (sections, notes for the footer)."""
    by_id = {h["id"]: h for h in heads}
    notes = []
    g = data.get("global") if isinstance(data.get("global"), dict) else {}
    out = {"bottom_line": [_s(x, 260) for x in _l(data.get("bottom_line")) if _s(x)][:3],
           "global": {k: _s(g.get(k), 700) for k in ("us", "europe", "asia")},
           "india": _s(data.get("india"), 700), "commodities": _s(data.get("commodities"), 900),
           "deep": [], "radar": [], "rapid": [], "today": [_s(x, 140) for x in _l(data.get("today")) if _s(x)][:6]}
    dropped = 0
    for d in _l(data.get("deep")):
        if not isinstance(d, dict):
            continue
        src = _srcs(d.get("src"), by_id)
        what, why, watch = _s(d.get("what")), _s(d.get("why")), _s(d.get("watch"))
        if not src or not _s(d.get("title")) or not what or not why:
            dropped += 1
            continue
        kind = str(d.get("kind", "")).lower().strip()
        out["deep"].append({"title": _s(d.get("title"), 110), "kind": kind if kind in _KINDS else "macro",
                            "region": _s(d.get("region"), 20) or "Global", "what": what, "why": why, "watch": watch,
                            "update": bool(d.get("update")) and bool(_s(d.get("changed"))), "changed": _s(d.get("changed"), 300),
                            "nse": _syms(d.get("nse")), "src": src, "words": _words(what) + _words(why) + _words(watch)})
    out["deep"] = out["deep"][:6]
    for r in _l(data.get("radar")):
        if not isinstance(r, dict):
            continue
        src, line, tag = _srcs(r.get("src"), by_id), _s(r.get("line")), _s(r.get("tag")).strip(" .>")
        if not src or not line or not tag:
            dropped += 1
            continue
        if _words(tag) > 7:
            tag = " ".join(tag.split()[:6])
        out["radar"].append({"line": line, "tag": tag, "nse": _syms(r.get("nse")), "src": src})
    out["radar"] = out["radar"][:15]
    for r in _l(data.get("rapid")):
        if not isinstance(r, dict):
            continue
        src, line = _srcs(r.get("src"), by_id), _s(r.get("line"))
        if not src or not line:
            dropped += 1
            continue
        typ = str(r.get("type", "")).lower().strip()
        out["rapid"].append({"line": line, "type": typ if typ in _TYPES else "other", "nse": _syms(r.get("nse")), "src": src})
    out["rapid"] = out["rapid"][:10]
    if len(out["deep"]) < 2:
        raise BriefError("The AI returned fewer than two sourced stories, so this brief was not saved.")
    if dropped:
        notes.append(f"{dropped} item{'s' if dropped != 1 else ''} removed for citing no source.")
    for name, lo, key in (("deep stories", 4, "deep"), ("radar items", 10, "radar"), ("rapid-fire items", 8, "rapid")):
        if len(out[key]) < lo:
            notes.append(f"Only {len(out[key])} {name} today: the news wire did not support more.")
    return out, notes


def write_brief(store=None, now: datetime | None = None, progress=None, prefer_home: bool = True, backend=None, fetch=None) -> dict:
    """Collect, write, check and save today's brief. Raises BriefError with a readable reason on failure."""
    now = now or _now()
    say = progress or (lambda _m: None)
    say("Reading overnight prices")
    no_prices = ""
    try:
        rows = snapshot(prefer_home)
    except Exception:
        rows = []
    if len(rows) < 8:                       # the stories are still worth having; say so instead of failing
        rows, no_prices = [], "The price feed did not answer, so this brief has no price table."
    say("Collecting headlines from the India and global desks")
    heads, stats = collect_headlines(now, fetch=fetch)
    if len(heads) < 25:
        raise BriefError(f"Only {len(heads)} headlines could be collected ({stats['feeds_ok']} of {stats['feeds']} feeds answered).")
    previous = []
    if store is not None:
        try:
            previous = [b for b in store.recent(6) if b.get("day") and b["day"] < now.strftime("%Y-%m-%d")]
        except Exception:
            previous = []
    data, model, mode, web = ask_model(build_prompt(rows, heads, previous, now), say, backend)
    say("Checking sources")
    sections, notes = clean(data, heads)
    if no_prices:
        notes.insert(0, no_prices)
    brief = {"v": VERSION, "day": now.strftime("%Y-%m-%d"), "kind": KIND, "written": now.isoformat(timespec="seconds"),
             "model": model, "mode": mode, "stats": stats, "snapshot": rows, "notes": notes, "web": web, **sections}
    if store is not None:
        store.put(brief)
    return brief


# ═════════════════════════ 5. storage ═════════════════════════
class Store:
    """Briefs by day. Supabase table `arka_briefs` when it is reachable (shared by the app and the scheduled job),
    always backed by a local folder so nothing depends on the table existing."""

    def __init__(self, supabase=None, folder: str | None = None):
        self.sb = supabase
        self.sb_ok = supabase is not None
        self.sb_error = ""
        self.dir = Path(folder or _cfg("BRIEF_STORE") or ".brief_store")

    def _file(self, day: str) -> Path:
        return self.dir / f"{day}_{KIND}.json"

    def _sb_fail(self, e):
        self.sb_ok, self.sb_error = False, _safe_err(e)

    def get(self, day: str):
        if self.sb_ok:
            try:
                r = self.sb.table(TABLE).select("payload").eq("day", day).eq("kind", KIND).limit(1).execute()
                if r.data:
                    p = r.data[0]["payload"]
                    return json.loads(p) if isinstance(p, str) else p
            except Exception as e:
                self._sb_fail(e)
        try:
            return json.loads(self._file(day).read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, brief: dict):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._file(brief["day"]).write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
            for old in sorted(self.dir.glob(f"*_{KIND}.json"))[:-12]:
                old.unlink()
        except Exception:
            pass
        if self.sb_ok:
            try:
                self.sb.table(TABLE).upsert({"day": brief["day"], "kind": KIND, "payload": brief}, on_conflict="day,kind").execute()
            except Exception as e:
                self._sb_fail(e)

    def recent(self, n: int = 7) -> list:
        """Newest first."""
        by_day = {}
        try:
            for f in self.dir.glob(f"*_{KIND}.json"):
                b = json.loads(f.read_text(encoding="utf-8"))
                by_day[b["day"]] = b
        except Exception:
            pass
        if self.sb_ok:
            try:
                r = self.sb.table(TABLE).select("day,payload").eq("kind", KIND).order("day", desc=True).limit(n).execute()
                for row in r.data or []:
                    p = row["payload"]
                    b = json.loads(p) if isinstance(p, str) else p
                    by_day[b["day"]] = b
            except Exception as e:
                self._sb_fail(e)
        return [by_day[d] for d in sorted(by_day, reverse=True)[:n]]


SQL_SETUP = """create table if not exists public.arka_briefs (
  day date not null,
  kind text not null default 'am',
  payload jsonb not null,
  created_at timestamptz not null default now(),
  primary key (day, kind)
);"""


# ═════════════════════════ 6. Telegram ═════════════════════════
def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _href(u: str) -> str:
    return html.escape(u, quote=True) if u and urlparse(u).scheme in ("http", "https") else ""


def _tg_src(src: list) -> str:
    parts = []
    for s in src[:3]:
        u = _href(s.get("url", ""))
        parts.append(f'<a href="{u}">{_e(s["name"])}</a>' if u else _e(s["name"]))
    return " · ".join(parts)


def _day_title(brief: dict) -> str:
    try:
        return f"{date.fromisoformat(brief['day']):%A %d %B %Y}"
    except Exception:
        return str(brief.get("day", ""))


def to_telegram(brief: dict) -> list:
    """The brief as Telegram HTML, split into messages that fit Telegram's 4,096-character limit."""
    rows = brief.get("snapshot", [])

    def nums(group, names=None):
        part = [r for r in rows if r["group"] == group and (names is None or r["name"] in names)]
        return "\n".join(f"{_e(r['name'])}  <b>{_e(_f_last(r))}</b>  {_e(_f_chg(r))}" for r in part)
    blocks = [f"<b>Arka morning brief</b>\n{_e(_day_title(brief))}"]
    if brief.get("bottom_line"):
        blocks.append("<b>Bottom line</b>\n" + "\n".join(f"• {_e(x)}" for x in brief["bottom_line"]))
    g = brief.get("global", {})
    for title, group, key in (("United States", "United States", "us"), ("Europe", "Europe", "europe"), ("Asia this morning", "Asia", "asia")):
        body = "\n".join(x for x in (nums(group), _e(g.get(key, ""))) if x)
        if body:
            blocks.append(f"<b>{title}</b>\n{body}")
    india = "\n".join(x for x in (nums("India"), _e(brief.get("india", ""))) if x)
    if india:
        blocks.append(f"<b>India setup</b>\n{india}")
    comm = "\n".join(x for x in (nums("Commodities"), nums("Currencies and rates"), _e(brief.get("commodities", ""))) if x)
    if comm:
        blocks.append(f"<b>Commodities, currencies and rates</b>\n{comm}")
    if brief.get("deep"):
        blocks.append("<b>STORIES</b>")
    for i, d in enumerate(brief.get("deep", []), 1):
        head = f"<b>{i}. {_e(d['title'])}</b>" + ("  (update)" if d.get("update") else "")
        lines = [head]
        if d.get("update") and d.get("changed"):
            lines.append(f"<i>What changed:</i> {_e(d['changed'])}")
        lines.append(f"<i>What happened:</i> {_e(d['what'])}")
        lines.append(f"<i>Why it matters:</i> {_e(d['why'])}")
        if d.get("watch"):
            lines.append(f"<i>Watch next:</i> {_e(d['watch'])}")
        lines.append(_tg_src(d["src"]))
        blocks.append("\n".join(lines))
    if brief.get("radar"):
        blocks.append("<b>ON THE RADAR</b>\n" + "\n".join(f"• {_e(r['line'])} › <b>{_e(r['tag'])}</b>" for r in brief["radar"]))
    if brief.get("rapid"):
        blocks.append("<b>RAPID FIRE</b>\n" + "\n".join(f"• {_e(r['line'])} ({_tg_src(r['src'][:1])})" for r in brief["rapid"]))
    if brief.get("today"):
        blocks.append("<b>On the calendar</b>\n" + "\n".join(f"• {_e(x)}" for x in brief["today"]))
    st_ = brief.get("stats", {})
    blocks.append(f"<i>Prices: Yahoo Finance. Written from {st_.get('headlines', 0)} headlines across {st_.get('sources', 0)} sources. "
                  "Information, not advice.</i>")
    msgs, cur = [], ""
    for b in blocks:
        if len(b) > 3800:                                   # one very long block: cut at a line break
            b = b[:3800].rsplit("\n", 1)[0]
        if cur and len(cur) + len(b) + 2 > 3800:
            msgs.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        msgs.append(cur)
    return msgs


def send_telegram(messages: list, post=None) -> int:
    """Send to the chat named by TELEGRAM_CHAT_ID. Returns how many messages were delivered. Never logs the token."""
    token, chat = _cfg("TELEGRAM_BOT_TOKEN"), _cfg("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise BriefError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not both set.")
    post = post or requests.post
    sent = 0
    for m in messages:
        try:
            r = post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                     json={"chat_id": chat, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True})
            ok = r.status_code == 200
            if not ok:                                      # usually a formatting problem: send it again as plain text
                plain = html.unescape(_TAG_RX.sub("", m))
                r = post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                         json={"chat_id": chat, "text": plain, "disable_web_page_preview": True})
                ok = r.status_code == 200
            if not ok:
                raise BriefError(f"Telegram refused the message (HTTP {r.status_code}).")
            sent += 1
            time.sleep(0.4)
        except BriefError:
            raise
        except Exception as e:
            raise BriefError(f"Telegram could not be reached ({_safe_err(e)}).")
    return sent


# ═════════════════════════ 7. the page ═════════════════════════
_CSS = """<style>
.db{font-family:var(--sans,'Inter',system-ui,sans-serif);font-variant-numeric:tabular-nums;container-type:inline-size;width:100%;color:var(--tx);}
.db *{box-sizing:border-box;}
.db-up{color:var(--green) !important;} .db-dn{color:var(--red) !important;} .db-fl{color:var(--tx3) !important;}
.db-ed{display:flex;justify-content:space-between;align-items:flex-end;gap:10px 20px;flex-wrap:wrap;padding:4px 0 0;}
.db-ed .o{font:500 12.5px var(--sans);color:var(--tx3);} .db-ed .t{font:650 24px/1.2 var(--sans);letter-spacing:-.5px;margin-top:3px;}
.db-chips{display:flex;gap:8px;flex-wrap:wrap;}
.db-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border-radius:8px;background:var(--s1);border:1px solid var(--line);font:500 12.5px var(--sans);color:var(--tx2);white-space:nowrap;}
.db-chip b{font-weight:600;color:var(--tx);}
.db-g{display:grid;gap:12px;margin-top:12px;grid-template-columns:minmax(0,1fr);align-items:stretch;}
@container (min-width:900px){.db-g{grid-template-columns:repeat(12,minmax(0,1fr));} .db-g>.c4{grid-column:span 4;} .db-g>.c5{grid-column:span 5;}
  .db-g>.c6{grid-column:span 6;} .db-g>.c7{grid-column:span 7;} .db-g>.c8{grid-column:span 8;} .db-g>.c12{grid-column:1/-1;}}
.db-sec{display:flex;align-items:center;gap:10px;margin:28px 0 -2px;flex-wrap:wrap;}
.db-sec::before{content:"";width:3px;height:15px;border-radius:2px;background:var(--amber);}
.db-sec b{font:600 15.5px var(--sans);letter-spacing:-.2px;} .db-sec span{font:12.5px var(--sans);color:var(--tx3);}
.db-p{background:var(--s1);border:1px solid var(--line);border-radius:12px;min-width:0;display:flex;flex-direction:column;overflow:hidden;}
.db-pt{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 14px;min-height:42px;border-bottom:1px solid var(--line);}
.db-pt b{font:600 13.5px var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.db-pt span{font:12px var(--sans);color:var(--tx3);white-space:nowrap;}
.db-pb{padding:14px;flex:1;min-width:0;}
.db-say{font:14px/1.65 var(--sans);color:var(--tx2);} .db-say+.db-say{margin-top:10px;}
/* bottom line */
.db-bl{position:relative;} .db-bl::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--amber);}
.db-bl ol{margin:0;padding:0;list-style:none;display:grid;gap:0;}
@container (min-width:900px){.db-bl ol{grid-template-columns:repeat(3,minmax(0,1fr));} .db-bl li+li{border-left:1px solid var(--line);}}
@container (max-width:899.98px){.db-bl li+li{border-top:1px solid var(--line);}}
.db-bl li{padding:14px 16px 15px 18px;font:500 14.5px/1.55 var(--sans);color:var(--tx);display:flex;gap:12px;}
.db-bl li i{flex:0 0 22px;height:22px;border-radius:6px;background:rgba(255,159,10,.14);color:var(--amber);font:700 12px var(--sans);font-style:normal;display:inline-flex;align-items:center;justify-content:center;margin-top:1px;}
/* number rows */
.db-nums{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:12px;}
.db-n{background:var(--s2);border-radius:9px;padding:8px 10px 9px;min-width:0;}
.db-n small{display:block;font:12px var(--sans);color:var(--tx3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.db-n b{font:600 14.5px var(--sans);color:var(--tx);} .db-n em{font:500 12.5px var(--sans);font-style:normal;margin-left:7px;}
.db-n u{display:block;text-decoration:none;font:11.5px var(--sans);color:var(--tx3);margin-top:1px;}
.db-reg{padding:14px;border-top:1px solid var(--line);} .db-reg:first-child{border-top:0;}
.db-reg h4{margin:0 0 9px;font:600 13px var(--sans);color:var(--tx);display:flex;justify-content:space-between;gap:10px;}
.db-reg h4 span{font:12px var(--sans);color:var(--tx3);font-weight:400;}
/* stories */
.db-st{padding:16px;}
.db-st h3{margin:8px 0 12px;font:650 17px/1.3 var(--sans);letter-spacing:-.2px;color:var(--tx);}
.db-tags{display:flex;gap:6px;flex-wrap:wrap;align-items:center;}
.db-tag{display:inline-block;padding:2px 8px;border-radius:6px;font:600 11.5px var(--sans);white-space:nowrap;color:var(--c,var(--tx3));background:color-mix(in srgb,var(--c,#71717A) 14%,transparent);}
.db-num{font:700 12px var(--sans);color:var(--tx3);margin-right:2px;}
.db-part{display:grid;grid-template-columns:118px minmax(0,1fr);gap:4px 14px;padding:9px 0;border-top:1px solid var(--line);}
.db-part small{font:600 12px var(--sans);color:var(--tx3);padding-top:3px;}
.db-part p{margin:0;font:14px/1.65 var(--sans);color:var(--tx2);}
.db-part.chg p{color:var(--tx);} .db-part.chg small{color:var(--amber);}
@container (max-width:619.98px){.db-part{grid-template-columns:minmax(0,1fr);} .db-part small{padding-top:0;}}
.db-foot{display:flex;gap:6px 10px;flex-wrap:wrap;align-items:center;margin-top:10px;padding-top:10px;border-top:1px solid var(--line);font:12px var(--sans);color:var(--tx3);}
.db a{color:var(--tx2);text-decoration:none;border-bottom:1px solid var(--line2,#2E2E33);} .db a:hover{color:var(--amber);border-color:var(--amber);}
.db-sym{display:inline-block;padding:2px 8px;border-radius:6px;font:600 11.5px var(--sans);background:var(--s2);color:var(--tx2);cursor:pointer;border:1px solid var(--line);}
.db-sym:hover{border-color:var(--amber);color:var(--tx);} .db-sym.mine{background:rgba(255,159,10,.14);color:var(--amber);border-color:rgba(255,159,10,.35);}
/* radar and rapid fire */
.db-li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 14px;align-items:center;padding:10px 14px;border-top:1px solid var(--line);}
.db-li:first-child{border-top:0;}
.db-li p{margin:0;font:13.5px/1.5 var(--sans);color:var(--tx);} .db-li p small{font:12px var(--sans);color:var(--tx3);margin-left:6px;white-space:nowrap;}
.db-imp{justify-self:end;display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:7px;font:600 12px var(--sans);white-space:nowrap;
  color:var(--c,var(--tx2));background:color-mix(in srgb,var(--c,#A1A1AA) 13%,transparent);}
.db-imp::before{content:"›";font-weight:700;opacity:.7;}
@container (max-width:619.98px){.db-li{grid-template-columns:minmax(0,1fr);} .db-imp{justify-self:start;}}
.db-rf{display:grid;grid-template-columns:74px minmax(0,1fr);gap:4px 12px;align-items:baseline;padding:9px 14px;border-top:1px solid var(--line);}
.db-rf:first-child{border-top:0;} .db-rf p{margin:0;font:13.5px/1.5 var(--sans);color:var(--tx);} .db-rf p small{font:12px var(--sans);color:var(--tx3);margin-left:6px;}
.db-kv{padding:9px 14px;border-top:1px solid var(--line);font:13.5px/1.5 var(--sans);color:var(--tx2);} .db-kv:first-child{border-top:0;}
.db-mine{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px;padding:10px 14px;border:1px solid rgba(255,159,10,.3);border-radius:12px;background:rgba(255,159,10,.06);font:13px var(--sans);color:var(--tx2);}
.db-note{font:12px/1.6 var(--sans);color:var(--tx3);padding:18px 0 0;}
.db-empty{border:1px dashed var(--line2,#2E2E33);border-radius:12px;padding:26px 20px;text-align:center;margin-top:12px;}
.db-empty b{display:block;font:600 16px var(--sans);color:var(--tx);} .db-empty span{display:block;font:13.5px/1.6 var(--sans);color:var(--tx3);margin-top:6px;}
</style>"""
_IMP = [(r"bearish|headwind|negative|pressure|risk|weak|drag|downside|caution", "#FF453A"),
        (r"bullish|supportive|tailwind|positive|boost|upside|strong|relief", "#30D158"),
        (r"^watch|confirms|awaits|due ", "#FF9F0A"), (r"read-through|read through|signals|implies", "#5AC8FA"), (r"noise", "#71717A")]
_KIND_C = {"company": "#5AC8FA", "sector": "#BF5AF2", "macro": "#FFD60A", "plumbing": "#FF9F0A"}


def _imp_color(tag: str) -> str:
    t = tag.lower()
    for rx, c in _IMP:
        if re.search(rx, t):
            return c
    return "#A1A1AA"


def _cls(v) -> str:
    return "db-fl" if v is None or abs(v) < 0.005 else "db-up" if v > 0 else "db-dn"


def _src_html(src: list) -> str:
    parts = []
    for s in src[:3]:
        u = _href(s.get("url", ""))
        t = html.escape(s.get("title", ""), quote=True).replace("$", "&#36;")
        parts.append(f'<a href="{u}" target="_blank" rel="noopener noreferrer" title="{t}">{_fix(s["name"])}</a>' if u else _fix(s["name"]))
    return " · ".join(parts)


def _sym_html(syms: list, mine: set, valid) -> str:
    out = []
    for s in syms:
        if valid is not None and s not in valid:
            continue
        cls = "db-sym mine" if s in mine else "db-sym"
        out.append(f'<span class="{cls}" data-ak-sym="{html.escape(s, quote=True)}">{_e(s)}</span>')
    return "".join(out)


def _stamp(day: str, today: str) -> str:
    if not day:
        return ""
    if day == today:
        return "Today, live"
    try:
        return f"Close of {date.fromisoformat(day):%a %d %b}"
    except ValueError:
        return ""


def _group_day(rows: list, group: str) -> str:
    """The date most instruments of a group last traded on (shown once in the heading, not on every tile)."""
    days = [r.get("date") for r in rows if r["group"] == group and r.get("date")]
    return max(set(days), key=days.count) if days else ""


def _nums_html(rows: list, group: str, today: str) -> str:
    common = _group_day(rows, group)
    out = []
    for r in rows:
        if r["group"] != group:
            continue
        odd = _stamp(r.get("date", ""), today) if r.get("date") and r["date"] != common else ""
        stamp = f"<u>{_e(odd)}</u>" if odd else ""
        last = _e(_f_last(r)).replace("$", "&#36;")
        out.append(f'<div class="db-n"><small>{_e(r["name"])}</small><b>{last}</b><em class="{_cls(_move(r))}">{_e(_f_chg(r))}</em>{stamp}</div>')
    return f'<div class="db-nums">{"".join(out)}</div>' if out else ""


def _panel(title, right, body, span="", pad=True):
    inner = f'<div class="db-pb">{body}</div>' if pad else body
    return f'<div class="db-p {span}"><div class="db-pt"><b>{title}</b><span>{right}</span></div>{inner}</div>'


def _fix(s: str) -> str:
    """Escape for HTML and keep '$' from being read as LaTeX by Streamlit."""
    return _e(s).replace("$", "&#36;")


def brief_html(brief: dict, mine=(), valid=None, compact: bool = False) -> str:
    """The whole brief as one HTML block. `mine` = symbols in the reader's book, `valid` = known NSE symbols (or None)."""
    mine = set(mine or ())
    rows, day = brief.get("snapshot", []), brief.get("day", "")
    try:
        written = datetime.fromisoformat(brief["written"]).astimezone(IST)
        stamp = f"written {written:%H:%M} IST"
    except Exception:
        stamp = ""
    st_ = brief.get("stats", {})
    chips = (f'<span class="db-chip"><b>{st_.get("headlines", 0)}</b> headlines</span><span class="db-chip"><b>{st_.get("sources", 0)}</b> sources</span>'
             + (f'<span class="db-chip">{_e(stamp)}</span>' if stamp else ""))
    head = (f'<div class="db-ed"><div><div class="o">Morning edition · before the open</div><div class="t">{_e(_day_title(brief))}</div></div>'
            f'<div class="db-chips">{chips}</div></div>')
    # which of the reader's stocks are named today (worked out here, never sent to the AI)
    hits = {}
    for sec in ("deep", "radar", "rapid"):
        for it in brief.get(sec, []):
            for s in it.get("nse", []):
                if s in mine:
                    hits[s] = hits.get(s, 0) + 1
    book = ""
    if hits:
        chips_ = "".join(f'<span class="db-sym mine" data-ak-sym="{html.escape(s, quote=True)}">{_e(s)} · {n}</span>' for s, n in sorted(hits.items(), key=lambda x: -x[1]))
        book = f'<div class="db-mine"><b style="color:var(--tx)">In your book today</b>{chips_}<span>Tap a stock to open it.</span></div>'
    bl = "".join(f"<li><i>{i}</i><span>{_fix(x)}</span></li>" for i, x in enumerate(brief.get("bottom_line", []), 1))
    bottom = f'<div class="db-p db-bl c12"><div class="db-pt"><b>Bottom line</b><span>What matters today</span></div><ol>{bl}</ol></div>' if bl else ""
    g = brief.get("global", {})
    regs = ""
    for title, group, key in (("United States", "United States", "us"), ("Europe", "Europe", "europe"), ("Asia", "Asia", "asia")):
        body = _nums_html(rows, group, day) + (f'<div class="db-say">{_fix(g.get(key, ""))}</div>' if g.get(key) else "")
        if body:
            regs += f'<div class="db-reg"><h4>{title}<span>{_e(_stamp(_group_day(rows, group), day))}</span></h4>{body}</div>'
    wrap = _panel("Global session wrap", "Overnight and this morning", regs, "c7", pad=False) if regs else ""
    india = _nums_html(rows, "India", day) + (f'<div class="db-say">{_fix(brief.get("india", ""))}</div>' if brief.get("india") else "")
    comm = (_nums_html(rows, "Commodities", day) + _nums_html(rows, "Currencies and rates", day)
            + (f'<div class="db-say">{_fix(brief.get("commodities", ""))}</div>' if brief.get("commodities") else ""))
    side = ('<div class="c5" style="display:flex;flex-direction:column;gap:12px;min-width:0">'
            + (_panel("India setup", _e(_stamp(_group_day(rows, "India"), day)), india) if india else "")
            + (_panel("Commodities, currencies and rates", _e(_stamp(_group_day(rows, "Commodities"), day)), comm) if comm else "") + "</div>")
    order = wrap + side

    stories = []
    n_deep = len(brief.get("deep", []))
    for i, d in enumerate(brief.get("deep", []), 1):
        span = "c12" if (i == 1 and n_deep % 2 == 1) else "c6"          # an odd count: the lead story takes the full width
        tags = (f'<span class="db-num">{i:02d}</span><span class="db-tag" style="--c:{_KIND_C.get(d["kind"], "#A1A1AA")}">{_KINDS.get(d["kind"], "Macro and policy")}</span>'
                f'<span class="db-tag">{_e(d.get("region", ""))}</span>'
                + ('<span class="db-tag" style="--c:#FF9F0A">Update</span>' if d.get("update") else ""))
        parts = ""
        if d.get("update") and d.get("changed"):
            parts += f'<div class="db-part chg"><small>What changed</small><p>{_fix(d["changed"])}</p></div>'
        parts += f'<div class="db-part"><small>What happened</small><p>{_fix(d["what"])}</p></div>'
        parts += f'<div class="db-part"><small>Why it matters</small><p>{_fix(d["why"])}</p></div>'
        if d.get("watch"):
            parts += f'<div class="db-part"><small>Watch next</small><p>{_fix(d["watch"])}</p></div>'
        foot = f'<div class="db-foot">{_sym_html(d.get("nse", []), mine, valid)}<span>Sources: {_src_html(d["src"])}</span></div>'
        stories.append(f'<div class="db-p {span}"><div class="db-st"><div class="db-tags">{tags}</div><h3>{_fix(d["title"])}</h3>{parts}{foot}</div></div>')
    radar = "".join(
        f'<div class="db-li"><p>{_fix(r["line"])} {_sym_html(r.get("nse", []), mine, valid)}<small>{_src_html(r["src"][:1])}</small></p>'
        f'<span class="db-imp" style="--c:{_imp_color(r["tag"])}">{_fix(r["tag"])}</span></div>' for r in brief.get("radar", []))
    rapid = "".join(
        f'<div class="db-rf"><span class="db-tag">{_TYPES.get(r["type"], "Also")}</span><p>{_fix(r["line"])} {_sym_html(r.get("nse", []), mine, valid)}'
        f'<small>{_src_html(r["src"][:1])}</small></p></div>' for r in brief.get("rapid", []))
    today_ = "".join(f'<div class="db-kv">{_fix(x)}</div>' for x in brief.get("today", []))
    out = head + book + f'<div class="db-g">{bottom}</div><div class="db-g">{order}</div>'
    if stories:
        out += ('<div class="db-sec"><b>Stories</b><span>Developing situations: what happened, why it matters, what to watch next</span></div>'
                f'<div class="db-g">{"".join(stories)}</div>')
    if radar:
        out += ('<div class="db-sec"><b>On the radar</b><span>One line each, with what it implies</span></div>'
                f'<div class="db-g"><div class="db-p c12">{radar}</div></div>')
    if rapid or today_:
        out += '<div class="db-sec"><b>Rapid fire</b><span>Earnings, deals, IPOs, flows and rating changes</span></div><div class="db-g">'
        if rapid:
            out += f'<div class="db-p {"c8" if today_ else "c12"}">{rapid}</div>'
        if today_:
            out += _panel("On the calendar", "From the news", today_, "c4" if rapid else "c12", pad=False)
        out += "</div>"
    notes = " ".join(brief.get("notes", []))
    web = brief.get("web") or []
    web_html = ("" if not web else " Web pages consulted: "
                + " · ".join(f'<a href="{_href(w["url"])}" target="_blank" rel="noopener noreferrer">{_fix(w["title"])}</a>' for w in web if _href(w.get("url", ""))) + ".")
    out += (f'<div class="db-note">Prices come from Yahoo Finance and can trail the exchange. The text is written by AI ({_e(brief.get("model", ""))}) from the '
            f'price table and {st_.get("headlines", 0)} headlines ({st_.get("feeds_ok", 0)} of {st_.get("feeds", 0)} news feeds answered); every story links to its sources, '
            f'and items that cited no source were removed. Check the source before acting on any item. Information, not advice. {_e(notes)}{web_html}</div>')
    return f'<div class="db{" db-compact" if compact else ""}">{out}</div>'


def _my_symbols(st) -> set:
    """Holdings and watchlist symbols, read locally. Used only to mark items on the page."""
    mine = set(st.session_state.get("watchlist", []) or []) | set(st.session_state.get("admin_watchlist", []) or [])
    try:
        import user_data as ud
        mine |= set(ud.holding_symbols() or [])
    except Exception:
        pass
    return {str(s).upper() for s in mine}


def _valid_symbols():
    try:
        wl = sys.modules.get("watchlist_page") or importlib.import_module("watchlist_page")
        names = wl.equity_names() or {}
        return set(names) if len(names) > 500 else None
    except Exception:
        return None


def render_brief(supabase=None, on_open=None):
    """The Daily brief page."""
    import streamlit as st
    st.markdown(_CSS, unsafe_allow_html=True)
    ss = st.session_state
    compact = bool(ss.get("_ak_phone", False))
    store = Store(supabase, folder=str(Path(".cache") / "briefs"))
    now = _now()
    today = now.strftime("%Y-%m-%d")
    memo = ss.get("_db_memo")
    if memo and memo[0] > time.time() - 90 and not ss.get("_db_dirty"):
        recent = memo[1]
    else:
        recent = store.recent(7)
        ss["_db_memo"], ss["_db_dirty"] = (time.time(), recent), False
    by_day = {b["day"]: b for b in recent}
    is_admin = bool(ss.get("is_admin", False))

    def _write():
        ss["_db_go"] = True
    if ss.pop("_db_go", False):
        box = st.empty()
        try:
            with st.spinner("Writing today's brief. This takes about a minute."):
                brief = write_brief(store, now, progress=lambda m: box.caption(m))
            by_day[brief["day"]] = brief
            ss["_db_dirty"], ss["_db_day"] = True, brief["day"]
            ss.pop("_db_err", None)
        except BriefError as e:
            ss["_db_err"] = str(e)
        except Exception as e:
            ss["_db_err"] = "The brief could not be written: " + _safe_err(e)
        box.empty()
    if ss.get("_db_err"):
        st.error(ss["_db_err"])

    days = sorted(by_day, reverse=True)
    have_today = today in by_day
    c1, c2 = st.columns([3, 1]) if not compact else (st.container(), st.container())
    with c1:
        if len(days) > 1:
            def _lab(d):
                try:
                    return "Today" if d == today else f"{date.fromisoformat(d):%a %d %b}"
                except ValueError:
                    return d
            if ss.get("_db_day") not in days:
                ss["_db_day"] = days[0]
            try:
                pick = st.pills("Edition", days, key="_db_day", format_func=_lab, label_visibility="collapsed") or days[0]
            except Exception:
                pick = st.selectbox("Edition", days, key="_db_day_sel", format_func=_lab, label_visibility="collapsed")
        else:
            pick = days[0] if days else None
    with c2:
        if have_today and is_admin:
            st.button("Write it again", key="db_again", on_click=_write, use_container_width=True,
                      help="Collects fresh headlines and rewrites today's brief. Uses the AI key once.")
    if not have_today:
        late = now.hour >= 6
        msg = ("Today's brief has not been written yet." if late else "Today's brief is written after 6 am IST.")
        sub = ("If the morning schedule is set up it arrives by about 8:30 am IST. You can also write it now; it takes about a minute."
               if late else "You can still write one now from the latest headlines; it takes about a minute.")
        st.markdown(f'<div class="db"><div class="db-empty"><b>{msg}</b><span>{sub}</span></div></div>', unsafe_allow_html=True)
        st.button("Write today's brief now", key="db_write", type="primary", on_click=_write)
    if pick and pick in by_day:
        if not have_today:
            st.caption("Showing the most recent brief.")
        st.markdown(brief_html(by_day[pick], _my_symbols(st), _valid_symbols(), compact), unsafe_allow_html=True)
    if is_admin and supabase is not None and not store.sb_ok:
        with st.expander("Share one brief between the app and Telegram (admin)"):
            st.caption("The table for briefs was not found in Supabase, so the app keeps its own copy. To make the app show the exact "
                       "brief the morning schedule sends to Telegram, run this once in the Supabase SQL editor:")
            st.code(SQL_SETUP, language="sql")


# ═════════════════════════ 8. scheduled job ═════════════════════════
def _cli_store() -> Store:
    sb = None
    url, key = _cfg("SUPABASE_URL"), _cfg("SUPABASE_KEY")
    if url and key:
        try:
            from supabase import create_client
            sb = create_client(url, key)
        except Exception as e:
            print("Supabase not used:", _safe_err(e))
    return Store(sb)


def main(argv=None) -> int:
    args = set(argv if argv is not None else sys.argv[1:])
    now = _now()
    store = _cli_store()
    brief = None if "--force" in args else store.get(now.strftime("%Y-%m-%d"))
    try:
        if brief is None:
            brief = write_brief(store, now, progress=lambda m: print("·", m), prefer_home=False)
            print(f"Written with {brief['model']}: {len(brief['deep'])} stories, {len(brief['radar'])} radar, {len(brief['rapid'])} rapid fire, "
                  f"{brief['stats']['headlines']} headlines, {brief['stats']['feeds_ok']}/{brief['stats']['feeds']} feeds.")
        else:
            print("Today's brief already exists; sending that one.")
        if store.sb is not None:
            print("Shared with the app through Supabase." if store.sb_ok else f"Supabase table not usable ({store.sb_error}); kept locally.")
        msgs = to_telegram(brief)
        if "--print" in args:
            print("\n\n".join(html.unescape(_TAG_RX.sub("", m)) for m in msgs))
        if "--send" in args:
            print(f"Telegram: {send_telegram(msgs)} message(s) sent.")
        return 0
    except Exception as e:
        reason = str(e) if isinstance(e, BriefError) else _safe_err(e)
        print("FAILED:", reason)
        if "--send" in args:
            try:
                send_telegram([f"<b>Arka morning brief</b>\nToday's brief could not be written.\n{_e(reason)}"])
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
