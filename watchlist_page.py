"""
Arka Trades — Watchlist Terminal (v3)

A market-watch screen in the style of a broker / Bloomberg monitor: one dense row per stock with the full quote
(LTP, change, open, high, low, previous close, volume), an intraday chart and an EVENTS / INDICATORS column that
says what is happening to each stock today (Open=High, gaps, 52-week highs, volume spikes, results, news, filings,
your own price alerts).

What changed vs v2
------------------
LOOK
  * Market-watch table modelled on terminal monitors: full quote per row, intraday chart with the previous close,
    company name and ISIN, and an events column with coloured chips. Four views: QUOTES, PERFORMANCE, TECHNICAL,
    HEATMAP. Sticky header, row grouping by sector, 0.5-second readable numbers (lakh / crore volume).
  * A detail card for the selected stock (day range, 52-week range, returns, events, latest headlines).
MORE DATA (all free, no keys)
  * Today's candle comes from a fresh 5-day download every minute while NSE is open; history (2 years) is cached
    for 6 hours, so a 60-stock list costs about one request per minute.
  * Events per stock: OPEN=HIGH / OPEN=LOW, gap up / down, previous-day high / low break, new 52-week high / low,
    volume spike (projected to a full day while the market is open), NR7, inside bar, crossing the 200-day average,
    RSI extremes, upcoming results and ex-dividend dates (Yahoo calendar), today's news, exchange filings and
    "results out" (your news_feed.py wire + BSE filings), and your own price alerts.
  * Delivery % from NSE's daily file (via research_data.py, when the server can reach NSE).
  * Performance vs NIFTY 50 (1W ... 1Y, YTD), RSI, distance from EMA 20 / SMA 50 / SMA 200, ATR %, PDH / PDL.
LISTS
  * Several named watchlists (Main, Swing, Long term …), notes and price alerts per stock, move between lists.
    Needs one SQL statement (shown in MANAGE → Storage). Without it the page works exactly as one list, as before.
  * The Arka desk list is unchanged: read-only for members, editable by admins.
LIVE
  * Auto-refresh every 60 seconds while NSE is open (toggle), without rerunning the rest of the app.

Public API unchanged: render_watchlist(supabase, on_open=None, is_admin=False)
"""
from __future__ import annotations

import html
import io
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import user_data as ud

try:
    from data_engine import _parse_download as _de_parse
except Exception:  # pragma: no cover
    _de_parse = None
try:
    from data_engine import rsi_wilder as _de_rsi
except Exception:  # pragma: no cover
    _de_rsi = None
try:
    from screener_scraper import resolve_symbol
except Exception:  # pragma: no cover
    resolve_symbol = None

IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# Terminal palette (same as the rest of the app)
TX, TX2, TX3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
AMBER, GREEN, RED, CYAN, PURPLE, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
BLUE = "#4FA3FF"
LINE, LINE2, S1, S2 = "#1F1F1F", "#2B2B2B", "#0A0A0A", "#0F0F0F"
MONO = "'JetBrains Mono','Consolas',monospace"

DEFAULT_LIST = "Main"
DESK = "ARKA DESK"
VIEWS = ["QUOTES", "PERFORMANCE", "TECHNICAL", "HEATMAP"]
FILTERS = ["ALL", "GAINERS", "LOSERS", "WITH EVENTS", "OPEN=HIGH", "OPEN=LOW", "52W HIGH", "VOLUME SPIKE",
           "ABOVE PDH", "BELOW PDL", "NEWS TODAY", "ALERTS HIT", "NO DATA"]
SQL_LISTS = """-- Arka watchlist v3 · run once in Supabase → SQL Editor (named lists, notes, price alerts)
alter table watchlist add column if not exists list_name   text default 'Main';
alter table watchlist add column if not exists note        text;
alter table watchlist add column if not exists alert_above double precision;
alter table watchlist add column if not exists alert_below double precision;
update watchlist set list_name = 'Main' where list_name is null;"""
_COLS = "id,symbol,list_name,note,alert_above,alert_below"


# ═════════════════════════ small helpers ═════════════════════════
def _num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _now() -> datetime:
    return datetime.now(IST)


def market_open(now: datetime | None = None) -> bool:
    now = now or _now()
    t = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 15 <= t < 15 * 60 + 30


def live_fraction(last_date, now: datetime | None = None):
    """Share of today's session already traded (0.1–1) when the last candle is today's unfinished one, else None."""
    now = now or _now()
    if not market_open(now) or pd.Timestamp(last_date).date() != now.date():
        return None
    return max(0.1, min(1.0, ((now.hour * 60 + now.minute) - (9 * 60 + 15)) / 375.0))


def _bucket(live_s=60, closed_s=900) -> int:
    return int(time.time() // (live_s if market_open() else closed_s))


def fmt_price(v, d=2):
    v = _num(v)
    return f"{v:,.{d}f}" if v is not None else "—"


def fmt_qty(v):
    """Indian compact volume: 12,345 / 5.94 L / 2.34 Cr."""
    v = _num(v)
    if v is None:
        return "—"
    if v >= 1e7:
        return f"{v / 1e7:,.2f} Cr"
    if v >= 1e5:
        return f"{v / 1e5:,.2f} L"
    return f"{v:,.0f}"


def _pct(v, d=2, scale=3.0, bg=True, arrow=True):
    v = _num(v)
    if v is None:
        return '<span class="na">—</span>'
    c = GREEN if v > 0 else RED if v < 0 else TX2
    style = f"color:{c}"
    if bg:
        a = min(0.26, abs(v) / scale * 0.26)
        style += f";background:rgba({'48,209,88' if v >= 0 else '255,69,58'},{a:.3f})"
    sign = ("▲ " if v > 0 else "▼ " if v < 0 else "") if arrow else ("+" if v > 0 else "")
    return f'<span class="pc" style="{style}">{sign}{abs(v) if arrow else v:,.{d}f}%</span>'


def _plain_pct(v, d=1):
    """Coloured percentage without the table cell styling (for the detail card)."""
    v = _num(v)
    if v is None:
        return '<span class="na">—</span>'
    return f'<span style="color:{GREEN if v > 0 else RED if v < 0 else TX2}">{v:+.{d}f}%</span>'


def _signed(v, d=2):
    v = _num(v)
    if v is None:
        return '<span class="na">—</span>'
    return f'<span style="color:{GREEN if v > 0 else RED if v < 0 else TX2}">{v:+,.{d}f}</span>'


# ═════════════════════════ price data ═════════════════════════
def _ticker(sym: str) -> str:
    return sym if sym.startswith("^") else f"{sym}.NS"


def _parse(raw, tickers):
    if _de_parse is not None:
        return _de_parse(raw, tickers)
    out = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            if t in raw.columns.get_level_values(0):
                out[t] = raw[t].dropna(how="all")
    elif len(tickers) == 1:
        out[tickers[0]] = raw.dropna(how="all")
    return out


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep].copy()
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(IST).tz_localize(None)                     # exchange time, no timezone
    df.index = idx
    df = df[df["Close"] > 0]
    if "Volume" in df:
        df["Volume"] = df["Volume"].fillna(0.0)
    for c in ("Open", "High", "Low"):
        if c in df:
            df[c] = df[c].fillna(df["Close"])
    return df[~df.index.duplicated(keep="last")].sort_index()


def _download(symbols, period, interval, chunk=60) -> dict:
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), chunk):
        part = list(symbols[i:i + chunk])
        tick = [_ticker(s) for s in part]
        try:
            raw = yf.download(tick, period=period, interval=interval, group_by="ticker", threads=True, progress=False, auto_adjust=False)
        except Exception:
            continue
        got = _parse(raw, tick)
        for s, t in zip(part, tick):
            f = got.get(t)
            if f is not None and len(f):
                try:
                    f = _norm(f)
                    if len(f):
                        out[s] = f
                except Exception:
                    continue
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=24)
def _history(symbols: tuple, day_key: str) -> dict:
    """2 years of daily candles. Cached for the day (6 h); raises when nothing comes back so failures are not cached."""
    out = _download(symbols, "2y", "1d")
    if symbols and not out:
        raise RuntimeError("no history")
    for s, f in out.items():
        f.index = f.index.normalize()
    return out


@st.cache_data(ttl=600, show_spinner=False, max_entries=24)
def _live(symbols: tuple, bucket: int) -> dict:
    """Last 5 daily candles, refreshed every minute while NSE is open: today's open / high / low / last / volume."""
    out = _download(symbols, "5d", "1d")
    if symbols and not out:
        raise RuntimeError("no quotes")
    for s, f in out.items():
        f.index = f.index.normalize()
    return out


@st.cache_data(ttl=600, show_spinner=False, max_entries=24)
def _intraday(symbols: tuple, bucket: int) -> dict:
    """Latest session in 5-minute bars -> {SYM: [(minute of session, close), ...]} for the chart column."""
    raw = _download(symbols, "1d", "5m")
    out = {}
    for s, f in raw.items():
        c = f["Close"].dropna()
        if len(c) >= 2:
            day = c.index[-1].normalize()
            c = c[c.index >= day]
            out[s] = [(int((ix - day).total_seconds() // 60) - 555, float(v)) for ix, v in c.items()]
    if symbols and not out:
        raise RuntimeError("no intraday")
    return out


_STALE: dict = {}
_FAIL: dict = {}


def _safe(key, fn, fallback):
    """Run fn(); on failure serve the last good value and do not retry for 45 s (a slow provider never freezes the page)."""
    now = time.time()
    if _FAIL.get(key, 0) > now:
        return _STALE.get(key, fallback)
    try:
        v = fn()
        _STALE[key] = v
        _FAIL.pop(key, None)
        if len(_STALE) > 200:
            _STALE.pop(next(iter(_STALE)))
        return v
    except Exception:
        _FAIL[key] = now + 45
        return _STALE.get(key, fallback)


def merge_live(hist: pd.DataFrame | None, live: pd.DataFrame | None) -> pd.DataFrame | None:
    """History up to the day before the live window, then the fresh live candles (they win on overlapping days)."""
    if live is None or not len(live):
        return hist
    if hist is None or not len(hist):
        return live
    return pd.concat([hist[hist.index < live.index[0]], live]).sort_index()


def load_frames(symbols: tuple, with_intraday: bool):
    syms = tuple(dict.fromkeys(symbols))
    day = _now().strftime("%Y-%m-%d")
    hist = _safe(("hist", syms, day), lambda: _history(syms, day), {})
    live = _safe(("live", syms), lambda: _live(syms, _bucket()), {})
    frames = {s: merge_live(hist.get(s), live.get(s)) for s in syms}
    intra = _safe(("intra", syms), lambda: _intraday(syms, _bucket()), {}) if with_intraday else {}
    return {s: f for s, f in frames.items() if f is not None and len(f)}, intra


# ═════════════════════════ reference data: names, delivery, calendar, news ═════════════════════════
_REF_FAIL: dict = {}


@st.cache_data(ttl=7 * 86400, show_spinner=False)
def _equity_list_cached(day: str) -> dict:
    texts = []
    for f in ("data/EQUITY_L.csv", "EQUITY_L.csv", "nse_universe.csv", "data/nse_universe.csv"):
        p = Path(f)
        if p.exists():
            texts.append(p.read_text(encoding="utf-8-sig", errors="replace"))
            break
    if not texts:
        import requests
        for u in ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "https://archives.nseindia.com/content/equities/EQUITY_L.csv"):
            try:
                r = requests.get(u, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=6)
                if r.status_code == 200 and "SYMBOL" in r.text[:300].upper():
                    texts.append(r.text)
                    break
            except Exception:
                continue
    if not texts:
        raise RuntimeError("equity list unavailable")
    df = pd.read_csv(io.StringIO(texts[0]), dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]
    if "SYMBOL" not in df.columns:
        raise RuntimeError("unexpected equity list format")
    name_col = next((c for c in df.columns if "NAME" in c), None)
    isin_col = next((c for c in df.columns if "ISIN" in c), None)
    out = {}
    for _, r in df.iterrows():
        s = str(r["SYMBOL"]).strip().upper()
        out[s] = (str(r[name_col]).strip() if name_col else "", str(r[isin_col]).strip() if isin_col else "")
    return out


def equity_names() -> dict:
    """{SYMBOL: (company name, ISIN)} from NSE's equity list (repo copy first, then NSE). {} when unreachable."""
    if _REF_FAIL.get("eq", 0) > time.time():
        return {}
    try:
        return _equity_list_cached(_now().strftime("%Y-%m"))
    except Exception:
        _REF_FAIL["eq"] = time.time() + 1800
        return {}


@st.cache_data(ttl=3 * 3600, show_spinner=False, max_entries=8)
def _delivery_cached(day_key: str) -> dict:
    import research_data as rd
    if time.time() < rd._BLOCKED_UNTIL.get("nse", 0):
        raise RuntimeError("NSE blocked")
    days, d, errors = [], _now().date(), 0
    while len(days) < 6 and errors < 2 and d > _now().date() - timedelta(days=16):
        if d.weekday() < 5:
            kind, df = rd._load_day(d)
            if kind == "ok" and df is not None and len(df):
                days.append((d, df))
            elif kind == "error":
                errors += 1
        d -= timedelta(days=1)
    if not days:
        rd._BLOCKED_UNTIL["nse"] = time.time() + 1800
        raise RuntimeError("no delivery files")
    per = {}
    for d, df in days:
        x = df[df["series"].astype(str).str.strip() == "EQ"] if "series" in df.columns else df
        vol = pd.to_numeric(x["volume"], errors="coerce")
        pct = pd.to_numeric(x["deliv_qty"], errors="coerce") / vol.where(vol > 0) * 100
        for s, p in zip(x["symbol"].astype(str).str.strip().str.upper(), pct):
            if pd.notna(p):
                per.setdefault(s, []).append((d, float(p)))
    out = {}
    for s, v in per.items():
        v.sort()
        prior = [p for _, p in v[:-1]]
        out[s] = {"pct": v[-1][1], "date": v[-1][0].isoformat(), "avg": sum(prior) / len(prior) if prior else None}
    return out


def delivery() -> dict:
    """{SYMBOL: {pct, date, avg}} from NSE's last ~6 daily files (shared by every stock). {} when NSE is unreachable."""
    if _REF_FAIL.get("dl", 0) > time.time():
        return {}
    try:
        return _delivery_cached(_now().strftime("%Y-%m-%d") + ("pm" if _now().hour >= 18 else "am"))
    except Exception:
        _REF_FAIL["dl"] = time.time() + 1800
        return {}


_CAL: dict = {}
_CAL_LOCK = threading.Lock()


def _calendar_one(sym: str) -> dict:
    """Next results date and ex-dividend date from Yahoo's calendar. {} on any failure. Cached 12 h per symbol."""
    with _CAL_LOCK:
        hit = _CAL.get(sym)
    if hit and time.time() - hit[0] < 12 * 3600:
        return hit[1]
    out = {}
    try:
        import yfinance as yf
        cal = yf.Ticker(_ticker(sym)).calendar
        cal = cal if isinstance(cal, dict) else (cal.to_dict() if hasattr(cal, "to_dict") else {})

        def first_date(v):
            vals = v if isinstance(v, (list, tuple)) else [v]
            ds = []
            for x in vals:
                try:
                    t = pd.Timestamp(x)
                    if not pd.isna(t):
                        ds.append((t.tz_localize(None) if t.tzinfo else t).date())
                except Exception:
                    continue
            return min(ds) if ds else None
        out = {"results": first_date(cal.get("Earnings Date")), "exdiv": first_date(cal.get("Ex-Dividend Date"))}
    except Exception:
        out = {}
    with _CAL_LOCK:
        _CAL[sym] = (time.time(), out)
    return out


def calendars(symbols, budget_s: float = 6.0) -> dict:
    """Calendars for the list in parallel; whatever is not back within budget_s is filled in on a later refresh."""
    todo = [s for s in symbols if not (_CAL.get(s) and time.time() - _CAL[s][0] < 12 * 3600)]
    if todo:
        pool = ThreadPoolExecutor(max_workers=8)
        futs = [pool.submit(_calendar_one, s) for s in todo]
        wait(futs, timeout=budget_s)
        pool.shutdown(wait=False, cancel_futures=True)
    return {s: _CAL[s][1] for s in symbols if s in _CAL}


def news_by_symbol(symbols, names: dict) -> dict:
    """Today's stories per stock from news_feed.py (wire + Google News + BSE filings). {} when news_feed is unavailable."""
    try:
        import news_feed as nf
    except Exception:
        return {}
    try:
        items = nf.assemble(tuple(symbols), tuple((s, names[s][0]) for s in symbols if s in names and names[s][0]), "", 1, True)
    except Exception:
        return {}
    cutoff = _now() - timedelta(hours=24)
    out = {}
    for a in items or []:
        dt = a.get("pub_dt")
        if dt is None or dt < cutoff:
            continue
        for s in a.get("symbols") or []:
            if s in symbols:
                out.setdefault(s, []).append(a)
    for s in out:
        out[s].sort(key=lambda a: a["pub_dt"], reverse=True)
    return out


# ═════════════════════════ indicators ═════════════════════════
def _rsi(close: pd.Series, n=14) -> pd.Series:
    if _de_rsi is not None:
        return _de_rsi(close, n)
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _ret(c: np.ndarray, n: int):
    return (c[-1] / c[-1 - n] - 1) * 100 if len(c) > n and c[-1 - n] > 0 else None


def compute(sym: str, f: pd.DataFrame | None, nifty: pd.DataFrame | None = None, now: datetime | None = None) -> dict:
    """Every number and event for one stock from its daily candles. Pure function (no network)."""
    row = {"symbol": sym, "has_data": False, "events": []}
    if f is None or len(f) < 2:
        return row
    o, h, l, c, v = (f[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close", "Volume"))
    n = len(c)
    last, prev = c[-1], c[-2]
    frac = live_fraction(f.index[-1], now)
    avg20 = float(np.mean(v[-21:-1])) if n >= 6 else None
    vol_proj = v[-1] / frac if frac else v[-1]
    yr = slice(max(0, n - 252), n)
    hi52, lo52 = float(h[yr].max()), float(l[yr].min())
    prior_hi = float(h[max(0, n - 253):n - 1].max()) if n > 20 else None
    prior_lo = float(l[max(0, n - 253):n - 1].min()) if n > 20 else None
    close_s = f["Close"]
    rsi = _num(_rsi(close_s).iloc[-1]) if n > 15 else None
    ema20 = _num(close_s.ewm(span=20, adjust=False, min_periods=20).mean().iloc[-1]) if n >= 20 else None
    sma50 = float(c[-50:].mean()) if n >= 50 else None
    sma200 = float(c[-200:].mean()) if n >= 200 else None
    sma200_prev = float(c[-201:-1].mean()) if n >= 201 else None
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = float(pd.Series(tr).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]) if len(tr) >= 14 else None
    ytd_base = f[f.index.year < f.index[-1].year]["Close"]
    ytd = (last / float(ytd_base.iloc[-1]) - 1) * 100 if len(ytd_base) else None
    r3 = _ret(c, 63)
    n3 = None
    if nifty is not None and len(nifty) > 64:
        nc = nifty["Close"].to_numpy(dtype=float)
        n3 = _ret(nc, 63)
    row.update(
        has_data=True, last=last, prev=prev, chg=last - prev, pct=(last / prev - 1) * 100 if prev else None,
        open=o[-1], high=h[-1], low=l[-1], volume=v[-1], avg20=avg20, live=bool(frac),
        vol_x=(vol_proj / avg20) if avg20 else None, turnover_cr=last * v[-1] / 1e7,
        pdh=h[-2], pdl=l[-2], hi52=hi52, lo52=lo52, pos52=((last - lo52) / (hi52 - lo52) * 100) if hi52 > lo52 else None,
        off_hi52=(last / hi52 - 1) * 100 if hi52 else None, day_pos=((last - l[-1]) / (h[-1] - l[-1]) * 100) if h[-1] > l[-1] else None,
        r1w=_ret(c, 5), r1m=_ret(c, 21), r3m=r3, r6m=_ret(c, 126), r1y=_ret(c, 252), ytd=ytd,
        vs_nifty_3m=(r3 - n3) if r3 is not None and n3 is not None else None,
        rsi=rsi, vs_ema20=(last / ema20 - 1) * 100 if ema20 else None, vs_sma50=(last / sma50 - 1) * 100 if sma50 else None,
        vs_sma200=(last / sma200 - 1) * 100 if sma200 else None, atr_pct=(atr / last * 100) if atr else None,
        spark=[float(x) for x in c[-63:]], date=f.index[-1])
    row["events"] = price_events(row, o, h, l, c, sma200_prev, prior_hi, prior_lo)
    return row


def price_events(r, o, h, l, c, sma200_prev, prior_hi, prior_lo) -> list:
    """(label, colour, tooltip, sort weight). Everything is read from today's (last) candle."""
    ev = []
    tol = max(0.01, 0.0002 * o[-1])
    rng = h[-1] - l[-1]
    if rng > 0 and abs(o[-1] - h[-1]) <= tol:
        ev.append(("Open=High", RED, "Opened at the day's high: sellers in control since the open", 8))
    elif rng > 0 and abs(o[-1] - l[-1]) <= tol:
        ev.append(("Open=Low", GREEN, "Opened at the day's low: buyers in control since the open", 8))
    gap = (o[-1] / c[-2] - 1) * 100
    if abs(gap) >= 1:
        ev.append((f"Gap {'up' if gap > 0 else 'down'} {abs(gap):.1f}%", GREEN if gap > 0 else RED, f"Opened {gap:+.2f}% from the previous close", 7))
    if prior_hi is not None and h[-1] > prior_hi:
        ev.append(("52W high", GREEN, f"New 52-week high (previous {prior_hi:,.2f})", 9))
    elif prior_lo is not None and l[-1] < prior_lo:
        ev.append(("52W low", RED, f"New 52-week low (previous {prior_lo:,.2f})", 9))
    if c[-1] > h[-2]:
        ev.append(("PDH break", GREEN, f"Trading above yesterday's high {h[-2]:,.2f}", 5))
    elif c[-1] < l[-2]:
        ev.append(("PDL break", RED, f"Trading below yesterday's low {l[-2]:,.2f}", 5))
    if r.get("vol_x") is not None and r["vol_x"] >= 2:
        ev.append((f"Vol {r['vol_x']:.1f}×", YELLOW, "Volume vs the 20-day average" + (" (projected to a full day)" if r["live"] else ""), 7))
    if len(h) >= 7 and rng > 0 and rng <= (h[-7:] - l[-7:]).min() + 1e-9:
        ev.append(("NR7", CYAN, "Narrowest range of the last 7 sessions: a move often follows", 3))
    if len(h) >= 2 and h[-1] < h[-2] and l[-1] > l[-2]:
        ev.append(("Inside", CYAN, "Inside bar: today's range is inside yesterday's", 2))
    s200 = r.get("vs_sma200")
    if s200 is not None and sma200_prev:
        if c[-2] < sma200_prev and s200 > 0:
            ev.append(("↑200 DMA", GREEN, "Crossed above the 200-day average today", 6))
        elif c[-2] > sma200_prev and s200 < 0:
            ev.append(("↓200 DMA", RED, "Crossed below the 200-day average today", 6))
    if r.get("rsi") is not None and r["rsi"] >= 70:
        ev.append((f"RSI {r['rsi']:.0f}", AMBER, "RSI 14 at or above 70 (overbought)", 3))
    elif r.get("rsi") is not None and r["rsi"] <= 30:
        ev.append((f"RSI {r['rsi']:.0f}", PURPLE, "RSI 14 at or below 30 (oversold)", 3))
    return ev


def add_reference_events(r: dict, cal: dict | None, stories: list | None, meta: dict | None, today: date | None = None):
    """Results / ex-dividend dates, today's news and filings, and the user's price alerts."""
    today = today or _now().date()
    ev = r.setdefault("events", [])
    cal = cal or {}
    rd = cal.get("results")
    if rd is not None and 0 <= (rd - today).days <= 14:
        days = (rd - today).days
        ev.append(("Results today" if days == 0 else f"Results {rd:%d %b}", PURPLE, f"Expected results date {rd:%d %b %Y} (Yahoo calendar)", 10 if days <= 2 else 6))
    xd = cal.get("exdiv")
    if xd is not None and 0 <= (xd - today).days <= 14:
        ev.append((f"Ex-div {xd:%d %b}", BLUE, f"Ex-dividend date {xd:%d %b %Y}: buy before this date to receive the dividend", 4))
    stories = stories or []
    if stories:
        filings = [a for a in stories if a.get("kind") == "filing"]
        news = [a for a in stories if a.get("kind") != "filing"]
        if any(a.get("event") == "EARNINGS" for a in stories):
            ev.append(("Results out", PURPLE, "Results / earnings story or filing in the last 24 hours: " + stories[0]["title"][:140], 10))
        if news:
            tone = sum(int(a.get("score") or 0) for a in news)
            ev.append((f"News {len(news)}", GREEN if tone > 0 else RED if tone < 0 else BLUE,
                       " | ".join(a["title"][:110] for a in news[:3]), 6))
        if filings:
            ev.append((f"Filing{'s' if len(filings) > 1 else ''} {len(filings)}", CYAN, " | ".join(a["title"][:110] for a in filings[:3]), 5))
        r["stories"] = stories
    meta = meta or {}
    last = r.get("last")
    above, below = _num(meta.get("alert_above")), _num(meta.get("alert_below"))
    r["alert_hit"] = False
    if last is not None and above is not None and last >= above:
        ev.append((f"Alert ▲ {above:,.2f}", AMBER, f"Your alert: price at or above {above:,.2f}", 11))
        r["alert_hit"] = True
    if last is not None and below is not None and last <= below:
        ev.append((f"Alert ▼ {below:,.2f}", AMBER, f"Your alert: price at or below {below:,.2f}", 11))
        r["alert_hit"] = True
    ev.sort(key=lambda e: -e[3])


# ═════════════════════════ storage: named lists, notes, alerts ═════════════════════════
def _local_rows() -> list:
    return st.session_state.setdefault("wl3_local_rows", [{"id": i + 1, "symbol": s, "list_name": DEFAULT_LIST, "note": None, "alert_above": None, "alert_below": None}
                                                         for i, s in enumerate(st.session_state.get("watchlist", []) or [])])


def lists_supported(sb) -> bool:
    """True when the watchlist table has the v3 columns (or when running without Supabase)."""
    if sb is None:
        return True
    if "wl3_cols" not in st.session_state:
        try:
            sb.table(ud.W_TABLE).select(_COLS).limit(1).execute()
            st.session_state["wl3_cols"] = True
        except Exception:
            st.session_state["wl3_cols"] = False
    return bool(st.session_state["wl3_cols"])


def load_rows(sb) -> list:
    """Every watchlist row: {id, symbol, list_name, note, alert_above, alert_below}."""
    if sb is None:
        rows = _local_rows()
    elif lists_supported(sb):
        try:
            rows = sb.table(ud.W_TABLE).select(_COLS).order("id").execute().data or []
        except Exception as exc:
            st.error(f"Could not load the watchlist: {exc}")
            rows = []
    else:
        rows = [{"id": None, "symbol": s, "list_name": DEFAULT_LIST} for s in ud.load_symbols(sb, ud.W_TABLE)]
    out, seen = [], set()
    for r in rows:
        sym = ud.clean_symbol(r.get("symbol"))
        lst = (r.get("list_name") or DEFAULT_LIST).strip() or DEFAULT_LIST
        if sym and (lst, sym) not in seen:
            seen.add((lst, sym))
            out.append({**r, "symbol": sym, "list_name": lst})
    st.session_state["watchlist"] = list(dict.fromkeys(r["symbol"] for r in out))      # Home, Scanner, news rail see every list
    return out


def add_to_list(sb, symbols, list_name) -> tuple:
    rows = load_rows(sb)
    have = {r["symbol"] for r in rows if r["list_name"] == list_name}
    new = [s for s in dict.fromkeys(ud.clean_symbol(x) for x in symbols) if s and s not in have]
    if not new:
        return [], None
    if sb is None:
        loc = _local_rows()
        nid = max([r["id"] for r in loc] + [0]) + 1
        loc += [{"id": nid + i, "symbol": s, "list_name": list_name, "note": None, "alert_above": None, "alert_below": None} for i, s in enumerate(new)]
        load_rows(sb)
        return new, None
    if not lists_supported(sb):
        added, err = ud.add_symbols(sb, new, ud.W_TABLE)
        return added, err
    try:
        sb.table(ud.W_TABLE).insert([{"symbol": s, "list_name": list_name} for s in new]).execute()
        load_rows(sb)
        return new, None
    except Exception as exc:
        return [], f"Could not save: {exc}"


def remove_from_list(sb, symbols, list_name):
    symbols = [ud.clean_symbol(s) for s in symbols]
    if sb is None:
        st.session_state["wl3_local_rows"] = [r for r in _local_rows() if not (r["list_name"] == list_name and r["symbol"] in symbols)]
        load_rows(sb)
        return None
    try:
        for s in symbols:
            if lists_supported(sb):
                sb.table(ud.W_TABLE).delete().eq("symbol", s).eq("list_name", list_name).execute()
            else:
                ud.remove_symbol(sb, s, ud.W_TABLE)
        load_rows(sb)
        return None
    except Exception as exc:
        return f"Could not remove: {exc}"


def update_meta(sb, symbol, list_name, fields: dict):
    if sb is None:
        for r in _local_rows():
            if r["symbol"] == symbol and r["list_name"] == list_name:
                r.update(fields)
        return None
    try:
        sb.table(ud.W_TABLE).update(fields).eq("symbol", symbol).eq("list_name", list_name).execute()
        return None
    except Exception as exc:
        return f"Could not save: {exc}"


def rename_list(sb, old, new):
    if sb is None:
        for r in _local_rows():
            if r["list_name"] == old:
                r["list_name"] = new
        return None
    try:
        sb.table(ud.W_TABLE).update({"list_name": new}).eq("list_name", old).execute()
        return None
    except Exception as exc:
        return f"Could not rename: {exc}"


def move_to_list(sb, symbol, src, dst):
    added, err = add_to_list(sb, [symbol], dst)
    if err:
        return err
    return remove_from_list(sb, [symbol], src)


# ═════════════════════════ symbol input ═════════════════════════
def _ticker_from_screener(query: str) -> str:
    if resolve_symbol is None:
        return ""
    try:
        res = resolve_symbol(query)
    except Exception:
        return ""
    m = re.search(r"/company/([^/]+)/", str((res or {}).get("url", ""))) if isinstance(res, dict) else None
    if not m or m.group(1).isdigit():
        return ""
    return ud.clean_symbol(m.group(1))


def resolve_tokens(text):
    """Symbols / company names -> (valid NSE tickers, unresolved). Uses the NSE equity list, then a price check, then Screener."""
    pairs = {}
    for t in ud.split_tokens(text):
        s = ud.clean_symbol(t)
        if s and s not in pairs:
            pairs[s] = str(t).strip()
    if not pairs:
        return [], []
    names = equity_names()
    check = [s for s in pairs if s not in names]
    live = _safe(("chk", tuple(check)), lambda: _download(tuple(check), "5d", "1d"), {}) if check else {}
    valid, unresolved = [], []
    for s in pairs:                                    # keep the order the user typed
        if s in names or s in live:
            valid.append(s)
            continue
        t = _ticker_from_screener(pairs[s])
        if t:
            valid.append(t)
        else:
            unresolved.append(s)
    return list(dict.fromkeys(valid)), unresolved


# ═════════════════════════ style ═════════════════════════
def _css():
    st.markdown(f"""<style>
    .w3-top{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:2px 0 8px;font:700 11px {MONO};letter-spacing:1.2px}}
    .w3-top b{{color:{AMBER}}} .w3-top span{{color:{TX3};font-weight:600;letter-spacing:.6px}}
    .w3-kp{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:{LINE};border:1px solid {LINE};margin:4px 0 10px}}
    .w3-k{{background:{S1};padding:10px 12px;min-width:0}}
    .w3-k .l{{font:700 9px {MONO};letter-spacing:1px;color:{TX3};text-transform:uppercase}}
    .w3-k .v{{font:700 17px {MONO};color:{TX};margin-top:5px;white-space:nowrap;font-variant-numeric:tabular-nums}}
    .w3-k .s{{font:10.5px {MONO};color:{TX3};margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .w3-br{{display:flex;height:4px;margin-top:7px;background:#151515}} .w3-br i{{display:block;height:100%}}
    .w3-tw{{overflow:auto;border:1px solid {LINE};background:#060606;max-height:720px}}
    table.w3{{width:100%;border-collapse:separate;border-spacing:0;font:12px {MONO};font-variant-numeric:tabular-nums}}
    .w3 th{{position:sticky;top:0;z-index:3;background:#121212;color:#8C8C8C;font:700 10px {MONO};letter-spacing:.6px;text-transform:uppercase;
           text-align:right;padding:9px 10px;border-bottom:1px solid {LINE2};border-right:1px solid #191919;white-space:nowrap}}
    .w3 td{{padding:6px 10px;border-bottom:1px solid #141414;border-right:1px solid #111;text-align:right;color:#D6D6D6;white-space:nowrap;vertical-align:middle}}
    .w3 th.l,.w3 td.l{{text-align:left}} .w3 td.c,.w3 th.c{{text-align:center}}
    .w3 tbody tr:nth-child(even) td{{background:#080808}} .w3 tbody tr:hover td{{background:#17130A}}
    .w3 tr.sel td{{background:#1E1709 !important}} .w3 tr.sel td.sym b{{color:{AMBER}}}
    .w3 tr.grp td{{background:#0D0D0D !important;color:{AMBER};font:700 10px {MONO};letter-spacing:1px;text-align:left;padding:7px 10px}}
    .w3 td.rk{{color:#4A4A4A;width:24px}}
    .w3 td.sym b{{display:block;font:700 12.5px {MONO};color:#F2F2F2;letter-spacing:.3px}}
    .w3 td.sym span{{display:block;font:10.5px Inter,sans-serif;color:{TX3};max-width:190px;overflow:hidden;text-overflow:ellipsis;margin-top:1px}}
    .w3 td.sym i{{font-style:normal;color:{AMBER};margin-left:5px;font-size:10px}}
    .w3 td.px{{color:#FFFFFF;font-weight:700}}
    .pc{{display:inline-block;min-width:66px;text-align:right;padding:1px 6px;border-radius:2px;font-weight:700}}
    .na{{color:#444}} .live{{color:{AMBER};font-size:9px;margin-left:3px}}
    .chip{{display:inline-block;margin:1px 4px 1px 0;padding:2px 7px;border:1px solid currentColor;border-radius:3px;font:700 10px Inter,sans-serif;
          background:rgba(255,255,255,.03);white-space:nowrap}}
    .more{{color:{TX3};font:700 10px {MONO}}}
    .bar{{position:relative;display:inline-block;width:70px;height:4px;background:#222;vertical-align:middle}}
    .bar i{{position:absolute;top:-3px;width:3px;height:10px;transform:translateX(-1px);border-radius:1px}}
    .w3-heat{{display:grid;grid-template-columns:repeat(auto-fill,minmax(122px,1fr));gap:4px;margin-bottom:8px}}
    .w3-tile{{padding:9px 10px;min-height:66px;border:1px solid #000}}
    .w3-tile b{{display:block;font:700 12px {MONO};color:#fff}} .w3-tile .p{{font:11px {MONO};color:#E6E6E6;margin-top:3px}}
    .w3-tile .c{{font:800 14px {MONO};color:#fff;margin-top:3px}}
    .w3-gh{{font:700 10px {MONO};color:{AMBER};letter-spacing:1px;margin:10px 0 4px}}
    .w3-card{{border:1px solid {LINE};border-top:2px solid {AMBER};background:{S1};padding:12px 14px;margin:8px 0}}
    .w3-card .hd{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}}
    .w3-card .hd b{{font:800 17px {MONO};color:#fff}} .w3-card .hd span{{font:12px Inter,sans-serif;color:{TX3}}}
    .w3-card .hd span.px{{font:800 20px {MONO};color:#fff}}
    .w3-g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 18px;margin-top:10px}}
    .w3-g > div > span{{display:block;font:700 9px {MONO};letter-spacing:.9px;color:{TX3};text-transform:uppercase}}
    .w3-g > div > b{{font:600 13px {MONO};color:{TX}}}
    .w3-rb{{position:relative;height:6px;background:linear-gradient(90deg,rgba(255,69,58,.35),#262626 50%,rgba(48,209,88,.35));margin:6px 0 3px}}
    .w3-rb i{{position:absolute;top:-4px;width:3px;height:14px;background:{AMBER};transform:translateX(-1px)}}
    .w3-rl{{display:flex;justify-content:space-between;font:10px {MONO};color:{TX3}}}
    .w3-st{{padding:6px 0;border-bottom:1px solid #161616}} .w3-st a{{font:600 12.5px Inter,sans-serif;color:{TX};text-decoration:none}}
    .w3-st a:hover{{color:{AMBER}}} .w3-st div{{font:10.5px {MONO};color:{TX3};margin-top:2px}}
    .w3-empty{{padding:30px 18px;text-align:center;color:{TX3};border:1px dashed {LINE2};background:{S1};font:12.5px Inter,sans-serif;line-height:1.8}}
    .w3-empty b{{color:{TX2}}}
    div[class*="st-key-w3_act"]{{border:1px solid {LINE};border-top:2px solid {AMBER};background:{S1};padding:9px 11px 3px;margin-top:8px}}
    div[class*="st-key-w3_lists"] button{{border-radius:0 !important;min-height:30px !important;font:700 11px {MONO} !important}}
    div[class*="st-key-w3_lists"] button p{{font:700 11px {MONO} !important;letter-spacing:.6px}}
    div[class*="st-key-w3_lists"] button[kind="primary"],div[class*="st-key-w3_lists"] button[data-testid="stBaseButton-primary"]{{
        background:{AMBER} !important;border-color:{AMBER} !important}}
    div[class*="st-key-w3_lists"] button[kind="primary"] p,div[class*="st-key-w3_lists"] button[data-testid="stBaseButton-primary"] p{{color:#000 !important}}
    </style>""", unsafe_allow_html=True)


# ═════════════════════════ cells ═════════════════════════
def _spark_daily(vals, w=96, h=26):
    pts = [p for p in (vals or []) if p is not None]
    if len(pts) < 2:
        return '<span class="na">—</span>'
    lo, hi = min(pts), max(pts)
    sp = hi - lo or 1.0
    xy = " ".join(f"{i / (len(pts) - 1) * w:.1f},{h - 3 - (p - lo) / sp * (h - 6):.1f}" for i, p in enumerate(pts))
    col = GREEN if pts[-1] >= pts[0] else RED
    return f'<svg width="{w}" height="{h}" style="display:block;margin-left:auto"><polyline points="{xy}" fill="none" stroke="{col}" stroke-width="1.4"/></svg>'


def _spark_intraday(pts, prev, w=110, h=28):
    """Session chart: x = minutes since 9:15 (full 375-minute session width), dashed line = previous close."""
    if not pts or len(pts) < 2:
        return '<span class="na">—</span>'
    vals = [p for _, p in pts] + ([prev] if prev else [])
    lo, hi = min(vals), max(vals)
    sp = hi - lo or 1.0
    y = lambda p: h - 3 - (p - lo) / sp * (h - 6)
    xy = " ".join(f"{max(0, min(375, m)) / 375 * w:.1f},{y(p):.1f}" for m, p in pts)
    col = GREEN if prev is None or pts[-1][1] >= prev else RED
    base = f'<line x1="0" x2="{w}" y1="{y(prev):.1f}" y2="{y(prev):.1f}" stroke="#555" stroke-dasharray="2 2" stroke-width="1"/>' if prev else ""
    return f'<svg width="{w}" height="{h}" style="display:block;margin:auto">{base}<polyline points="{xy}" fill="none" stroke="{col}" stroke-width="1.4"/></svg>'


def _rbar(pos, title=""):
    if pos is None:
        return '<span class="na">—</span>'
    p = max(0.0, min(100.0, pos))
    col = GREEN if pos >= 80 else RED if pos <= 20 else AMBER
    return f'<span class="bar" title="{_e(title)}"><i style="left:{p:.0f}%;background:{col}"></i></span>'


def _rsi_cell(v):
    v = _num(v)
    if v is None:
        return '<span class="na">—</span>'
    col = AMBER if v >= 70 else GREEN if v >= 55 else TX2 if v >= 45 else "#FF8A80" if v > 30 else RED
    return f'<b style="color:{col}">{v:.0f}</b>'


def _events_cell(evs, cap=4):
    if not evs:
        return '<span class="na">-</span>'
    shown = "".join(f'<span class="chip" style="color:{c}" title="{_e(tip)}">{_e(lab)}</span>' for lab, c, tip, _ in evs[:cap])
    if len(evs) > cap:
        shown += f'<span class="more" title="{_e(" · ".join(e[0] for e in evs[cap:]))}">+{len(evs) - cap}</span>'
    return shown


def _sym_cell(r, names, meta):
    nm, isin = names.get(r["symbol"], ("", ""))
    note = (meta or {}).get("note")
    tip = " · ".join(x for x in (nm, isin, f"Note: {note}" if note else "") if x)
    sub = nm or ("no data" if not r["has_data"] else "")
    return (f'<td class="l sym" title="{_e(tip)}"><b>{_e(r["symbol"])}{"<i>✎</i>" if note else ""}</b>'
            f'<span>{_e(sub)}</span></td>')


def _vol_cell(r):
    if not r["has_data"]:
        return '<span class="na">—</span>'
    vx = r.get("vol_x")
    star = '<span class="live" title="Projected to a full day">*</span>' if r.get("live") else ""
    vx_html = f'<div style="font-size:10px;color:{YELLOW if vx and vx >= 2 else GREEN if vx and vx >= 1.2 else TX3}">{vx:.2f}×{star}</div>' if vx else ""
    return f'<span title="{r["volume"]:,.0f} shares">{fmt_qty(r["volume"])}</span>{vx_html}'


def _deliv_cell(d):
    if not d:
        return '<span class="na">—</span>'
    p, a = d.get("pct"), d.get("avg")
    col = GREEN if a and p >= a * 1.2 else RED if a and p <= a * 0.8 else TX
    sub = f'<div style="font-size:10px;color:{TX3}">avg {a:.0f}%</div>' if a else ""
    return f'<span style="color:{col}" title="NSE delivery % on {d.get("date")}; avg = previous sessions">{p:.1f}%</span>{sub}'


# ═════════════════════════ views ═════════════════════════
def _columns(view):
    """(header, cell function(r, ctx) -> inner html, td class, header tooltip)"""
    q = lambda k, d=2: (lambda r, x: fmt_price(r.get(k), d) if r["has_data"] else '<span class="na">—</span>')
    if view == "QUOTES":
        return [
            ("LTP", lambda r, x: fmt_price(r.get("last")) + ('<span class="live">●</span>' if r.get("live") else "") if r["has_data"] else '<span class="na">no data</span>', "px", "Last traded price"),
            ("Chg", lambda r, x: _signed(r.get("chg")), "", "Change from the previous close"),
            ("% Chg", lambda r, x: _pct(r.get("pct")), "", ""),
            ("Open", q("open"), "", ""), ("High", q("high"), "", ""), ("Low", q("low"), "", ""),
            ("Prev Close", q("prev"), "", ""),
            ("Day range", lambda r, x: _rbar(r.get("day_pos"), "Where price sits between today's low and high"), "c", "Low ← → High"),
            ("Volume", lambda r, x: _vol_cell(r), "", "Today's volume and its multiple of the 20-day average"),
            ("Deliv %", lambda r, x: _deliv_cell(x["deliv"].get(r["symbol"])), "", "NSE delivery % of the latest published session"),
            ("Chart", lambda r, x: _spark_intraday(x["intra"].get(r["symbol"]), r.get("prev")), "c", "Today, 5-minute candles; dashed = previous close"),
            ("Events / Indicators", lambda r, x: _events_cell(r["events"]), "l", "Hover a chip for details"),
        ]
    if view == "PERFORMANCE":
        p = lambda k: (lambda r, x: _pct(r.get(k), 1, 12, True, False))
        return [
            ("LTP", lambda r, x: fmt_price(r.get("last")) if r["has_data"] else '<span class="na">no data</span>', "px", ""),
            ("1D", lambda r, x: _pct(r.get("pct"), 2, 3, True, False), "", ""), ("1W", p("r1w"), "", ""), ("1M", p("r1m"), "", ""),
            ("3M", p("r3m"), "", ""), ("6M", p("r6m"), "", ""), ("YTD", p("ytd"), "", ""), ("1Y", p("r1y"), "", ""),
            ("vs NIFTY 3M", lambda r, x: _pct(r.get("vs_nifty_3m"), 1, 12, True, False), "", "3-month return minus NIFTY 50's, in % points"),
            ("52W range", lambda r, x: _rbar(r.get("pos52"), f'52W low {fmt_price(r.get("lo52"))} · high {fmt_price(r.get("hi52"))}'), "c", ""),
            ("From 52W high", lambda r, x: _pct(r.get("off_hi52"), 1, 30, False, False), "", ""),
            ("3M trend", lambda r, x: _spark_daily(r.get("spark")), "c", "Daily closes, last 3 months"),
            ("Events / Indicators", lambda r, x: _events_cell(r["events"], 3), "l", ""),
        ]
    return [  # TECHNICAL
        ("LTP", lambda r, x: fmt_price(r.get("last")) if r["has_data"] else '<span class="na">no data</span>', "px", ""),
        ("% Chg", lambda r, x: _pct(r.get("pct")), "", ""),
        ("RSI 14", lambda r, x: _rsi_cell(r.get("rsi")), "", ""),
        ("vs EMA 20", lambda r, x: _pct(r.get("vs_ema20"), 1, 8, True, False), "", ""),
        ("vs SMA 50", lambda r, x: _pct(r.get("vs_sma50"), 1, 12, True, False), "", ""),
        ("vs SMA 200", lambda r, x: _pct(r.get("vs_sma200"), 1, 20, True, False), "", ""),
        ("ATR %", lambda r, x: f'{r["atr_pct"]:.2f}%' if r.get("atr_pct") else '<span class="na">—</span>', "", "Average daily range, 14 sessions, as % of price"),
        ("PDH", q("pdh"), "", "Previous day's high"), ("PDL", q("pdl"), "", "Previous day's low"),
        ("Vol ×", lambda r, x: (f'{r["vol_x"]:.2f}×' + ('<span class="live">*</span>' if r.get("live") else "")) if r.get("vol_x") else '<span class="na">—</span>', "", "Volume vs 20-day average"),
        ("Deliv %", lambda r, x: _deliv_cell(x["deliv"].get(r["symbol"])), "", ""),
        ("Events / Indicators", lambda r, x: _events_cell(r["events"]), "l", ""),
    ]


def render_table(rows, view, ctx, group=False, selected=None) -> str:
    cols = _columns(view)
    head = '<th class="l">#</th><th class="l">Symbol</th>' + "".join(f'<th class="{cls if cls in ("l", "c") else ""}" title="{_e(tip)}">{_e(h)}</th>' for h, _, cls, tip in cols)
    body, last_grp, i = [], None, 0
    for r in rows:
        if group and r.get("group") != last_grp:
            last_grp = r.get("group")
            members = [x for x in rows if x.get("group") == last_grp and x.get("pct") is not None]
            avg = sum(x["pct"] for x in members) / len(members) if members else None
            avg_html = f' · <span style="color:{GREEN if (avg or 0) >= 0 else RED}">{avg:+.2f}%</span>' if avg is not None else ""
            body.append(f'<tr class="grp"><td colspan="{len(cols) + 2}">{_e(last_grp)} · {len(members)}{avg_html}</td></tr>')
        i += 1
        cells = "".join(f'<td class="{cls}">{fn(r, ctx)}</td>' for _, fn, cls, _ in cols)
        body.append(f'<tr class="{"sel" if r["symbol"] == selected else ""}"><td class="rk">{i}</td>{_sym_cell(r, ctx["names"], ctx["meta"].get(r["symbol"]))}{cells}</tr>')
    return f'<div class="w3-tw"><table class="w3"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_heatmap(rows, field, group=False) -> str:
    def tile(r):
        v = r.get(field)
        if v is None:
            bg, txt = "#141414", "no data"
        else:
            scale = {"pct": 4, "r1w": 8, "r1m": 15, "r3m": 25, "ytd": 40, "r1y": 60}.get(field, 10)
            a = min(0.85, 0.15 + abs(v) / scale * 0.7)
            bg = f"rgba({'34,160,70' if v >= 0 else '215,50,45'},{a:.3f})"
            txt = f"{v:+.2f}%"
        return (f'<div class="w3-tile" style="background:{bg}" title="{_e(r["symbol"])}"><b>{_e(r["symbol"])}</b>'
                f'<div class="p">{fmt_price(r.get("last"))}</div><div class="c">{txt}</div></div>')
    srt = sorted(rows, key=lambda r: r.get(field) if r.get(field) is not None else -1e9, reverse=True)
    if not group:
        return '<div class="w3-heat">' + "".join(tile(r) for r in srt) + "</div>"
    out = []
    for g in dict.fromkeys(r.get("group") for r in sorted(rows, key=lambda r: r.get("group") or "")):
        members = [r for r in srt if r.get("group") == g]
        out.append(f'<div class="w3-gh">{_e(g)} · {len(members)}</div><div class="w3-heat">' + "".join(tile(r) for r in members) + "</div>")
    return "".join(out)


def render_kpis(rows, nifty_row) -> str:
    ok = [r for r in rows if r["has_data"]]
    adv = sum(1 for r in ok if (r.get("pct") or 0) > 0)
    dec = sum(1 for r in ok if (r.get("pct") or 0) < 0)
    flat = len(ok) - adv - dec
    tot = max(1, len(ok))
    avg = sum(r["pct"] for r in ok if r.get("pct") is not None) / len(ok) if ok else None
    npct = nifty_row.get("pct") if nifty_row and nifty_row.get("has_data") else None
    hi = sum(1 for r in ok if any(e[0] == "52W high" for e in r["events"]))
    lo = sum(1 for r in ok if any(e[0] == "52W low" for e in r["events"]))
    upvol = sum(1 for r in ok if (r.get("pct") or 0) > 0 and (r.get("vol_x") or 0) >= 1.5)
    dnvol = sum(1 for r in ok if (r.get("pct") or 0) < 0 and (r.get("vol_x") or 0) >= 1.5)
    with_ev = sum(1 for r in ok if r["events"])
    movers = sorted([r for r in ok if r.get("pct") is not None], key=lambda r: r["pct"])
    best, worst = (movers[-1], movers[0]) if movers else (None, None)
    k = lambda l, v, s="": f'<div class="w3-k"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
    br = (f'<div class="w3-br"><i style="width:{adv / tot * 100:.1f}%;background:{GREEN}"></i><i style="width:{flat / tot * 100:.1f}%;background:#333"></i>'
          f'<i style="width:{dec / tot * 100:.1f}%;background:{RED}"></i></div>')
    cell = lambda v: f'<span style="color:{GREEN if v >= 0 else RED}">{v:+.2f}%</span>' if v is not None else "—"
    cards = [
        k("Stocks", f"{len(rows)}", f"{len(rows) - len(ok)} without data" if len(rows) != len(ok) else "all priced"),
        f'<div class="w3-k"><div class="l">Advance / decline</div><div class="v"><span style="color:{GREEN}">{adv}</span>'
        f' <span style="color:{TX3}">/</span> <span style="color:{RED}">{dec}</span></div>{br}</div>',
        k("List today", cell(avg), f"NIFTY 50 {cell(npct)}" if npct is not None else "equal-weighted"),
        k("Up on volume", f'<span style="color:{GREEN}">{upvol}</span> / <span style="color:{RED}">{dnvol}</span>', "up / down with volume ≥ 1.5×"),
        k("52W high / low", f'<span style="color:{GREEN}">{hi}</span> / <span style="color:{RED}">{lo}</span>', "new today"),
        k("With events", f"{with_ev}", "chips in the events column"),
        k("Best", _e(best["symbol"]) if best else "—", cell(best["pct"]) if best else ""),
        k("Worst", _e(worst["symbol"]) if worst else "—", cell(worst["pct"]) if worst else ""),
    ]
    return '<div class="w3-kp">' + "".join(cards) + "</div>"


def render_card(r, names, meta, deliv) -> str:
    if not r or not r["has_data"]:
        return ""
    nm, isin = names.get(r["symbol"], ("", ""))
    col = GREEN if (r.get("pct") or 0) >= 0 else RED

    def band(lo, hi, v, ll, hl):
        if lo is None or hi is None or hi <= lo:
            return ""
        p = max(0, min(100, (v - lo) / (hi - lo) * 100))
        return f'<div class="w3-rb"><i style="left:{p:.1f}%"></i></div><div class="w3-rl"><span>{ll} {fmt_price(lo)}</span><span>{hl} {fmt_price(hi)}</span></div>'
    g = lambda lab, val: f"<div><span>{lab}</span><b>{val}</b></div>"
    d = deliv.get(r["symbol"])
    grid = "".join([
        g("Open", fmt_price(r["open"])), g("Prev close", fmt_price(r["prev"])), g("Volume", fmt_qty(r["volume"])),
        g("20D avg volume", fmt_qty(r.get("avg20"))), g("Turnover", f'₹{r["turnover_cr"]:,.1f} Cr'),
        g("Delivery", f'{d["pct"]:.1f}% ({d["date"]})' if d else "—"), g("RSI 14", f'{r["rsi"]:.0f}' if r.get("rsi") is not None else "—"),
        g("ATR", f'{r["atr_pct"]:.2f}% of price' if r.get("atr_pct") else "—"),
        g("1W / 1M", f'{_plain_pct(r.get("r1w"))} / {_plain_pct(r.get("r1m"))}'),
        g("3M / 1Y", f'{_plain_pct(r.get("r3m"))} / {_plain_pct(r.get("r1y"))}'),
        g("vs SMA 50 / 200", f'{_plain_pct(r.get("vs_sma50"))} / {_plain_pct(r.get("vs_sma200"))}'),
        g("Alerts", " · ".join(x for x in (f'▲ {fmt_price(meta.get("alert_above"))}' if _num((meta or {}).get("alert_above")) else "",
                                         f'▼ {fmt_price(meta.get("alert_below"))}' if _num((meta or {}).get("alert_below")) else "") if x) or "none"),
    ])
    ev = "".join(f'<div class="w3-st"><span class="chip" style="color:{c}">{_e(lab)}</span> <span style="font:12px Inter,sans-serif;color:{TX2}">{_e(tip)}</span></div>'
                 for lab, c, tip, _ in r["events"]) or f'<div class="w3-st" style="color:{TX3};font:12px Inter,sans-serif">No events today.</div>'
    stories = "".join(f'<div class="w3-st"><a href="{_e(a.get("link") or "#")}" target="_blank">{_e(a["title"][:150])}</a>'
                      f'<div>{_e(a.get("time_str") or a["pub_dt"].strftime("%d %b %H:%M"))} · {_e(a.get("source", ""))}{" · " + _e(a.get("event")) if a.get("event") and a.get("event") != "NEWS" else ""}</div></div>'
                      for a in (r.get("stories") or [])[:4])
    note = (meta or {}).get("note")
    return (f'<div class="w3-card"><div class="hd"><div><b>{_e(r["symbol"])}</b> <span>{_e(nm)}{" · " + _e(isin) if isin else ""}</span></div>'
            f'<div><span class="px">₹{fmt_price(r["last"])}</span> <span style="font:700 13px {MONO};color:{col}">{r["chg"]:+,.2f} ({r["pct"]:+.2f}%)</span>'
            f'{" <span class=live>LIVE</span>" if r.get("live") else ""}</div></div>'
            + (f'<div style="font:12px Inter,sans-serif;color:{AMBER};margin-top:6px">✎ {_e(note)}</div>' if note else "")
            + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:10px">'
            f'<div><div class="w3-rl"><span>DAY RANGE</span></div>{band(r["low"], r["high"], r["last"], "L", "H")}</div>'
            f'<div><div class="w3-rl"><span>52-WEEK RANGE</span></div>{band(r["lo52"], r["hi52"], r["last"], "L", "H")}</div></div>'
            f'<div class="w3-g">{grid}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:12px"><div><div class="w3-rl"><span>EVENTS TODAY</span></div>{ev}</div>'
            f'<div><div class="w3-rl"><span>LATEST HEADLINES (24 H)</span></div>{stories or "<div class=w3-st style=color:#6B6B6B>No stories tagged to this stock.</div>"}</div></div></div>')


# ═════════════════════════ filter / sort ═════════════════════════
SORTS = {
    "List order": (None, False), "% Chg ↓": ("pct", True), "% Chg ↑": ("pct", False), "Volume × ↓": ("vol_x", True),
    "Events first": ("n_events", True), "1W ↓": ("r1w", True), "1M ↓": ("r1m", True), "3M ↓": ("r3m", True), "YTD ↓": ("ytd", True),
    "1Y ↓": ("r1y", True), "vs NIFTY 3M ↓": ("vs_nifty_3m", True), "Near 52W high": ("off_hi52", True), "RSI ↓": ("rsi", True),
    "RSI ↑": ("rsi", False), "Turnover ↓": ("turnover_cr", True), "Delivery % ↓": ("deliv_pct", True), "Symbol A–Z": ("symbol", False),
}


def filter_rows(rows, flt, query=""):
    q = (query or "").strip().upper()
    out = [r for r in rows if not q or q in r["symbol"] or q in (r.get("name") or "").upper()]
    has = lambda r, lab: any(e[0].startswith(lab) for e in r["events"])
    tests = {
        "GAINERS": lambda r: (r.get("pct") or 0) > 0, "LOSERS": lambda r: (r.get("pct") or 0) < 0,
        "WITH EVENTS": lambda r: bool(r["events"]), "OPEN=HIGH": lambda r: has(r, "Open=High"), "OPEN=LOW": lambda r: has(r, "Open=Low"),
        "52W HIGH": lambda r: has(r, "52W high"), "VOLUME SPIKE": lambda r: (r.get("vol_x") or 0) >= 2,
        "ABOVE PDH": lambda r: has(r, "PDH break"), "BELOW PDL": lambda r: has(r, "PDL break"),
        "NEWS TODAY": lambda r: bool(r.get("stories")), "ALERTS HIT": lambda r: bool(r.get("alert_hit")), "NO DATA": lambda r: not r["has_data"],
    }
    return [r for r in out if tests[flt](r)] if flt in tests else out


def sort_rows(rows, how, group=False):
    key, rev = SORTS.get(how, (None, False))
    out = list(rows)
    if key == "symbol":
        out.sort(key=lambda r: r["symbol"])
    elif key:
        have = [r for r in out if r.get(key) is not None]
        miss = [r for r in out if r.get(key) is None]
        out = sorted(have, key=lambda r: r[key], reverse=rev) + miss          # missing values always last
    if group:
        out.sort(key=lambda r: (r.get("group") == "Unclassified", r.get("group") or ""))   # stable: keeps the order inside each group
    return out


def _group_of(sym, names):
    try:
        import sector_map as sm
        return sm.classify(sym, company=names.get(sym, ("", ""))[0])
    except Exception:
        return "All stocks"


def build_rows(symbols, frames, names, deliv, cals, news, meta, nifty=None, now=None):
    rows = []
    for s in symbols:
        r = compute(s, frames.get(s), nifty, now)
        add_reference_events(r, cals.get(s), news.get(s), meta.get(s), (now or _now()).date())
        r["name"] = names.get(s, ("", ""))[0]
        r["deliv_pct"] = (deliv.get(s) or {}).get("pct")
        r["n_events"] = len(r["events"])
        rows.append(r)
    return rows


# ═════════════════════════ page pieces ═════════════════════════
def _popover(label, disabled=False):
    """st.popover when available (its `disabled` argument only exists on newer Streamlit), else an expander."""
    if not hasattr(st, "popover"):
        return st.expander(label)
    try:
        return st.popover(label, use_container_width=True, disabled=disabled)
    except TypeError:
        return st.popover(label, use_container_width=True)

def _add_box(sb, list_name, key, desk=False):
    c1, c2 = st.columns([3.2, 1.2], gap="small")
    with c1:
        with st.form(f"w3_add_{key}", clear_on_submit=True):
            a, b = st.columns([4.2, 1])
            raw = a.text_input("Add", placeholder=f"Add to {list_name}: RELIANCE, HDFCBANK, tata motors …", label_visibility="collapsed")
            go = b.form_submit_button("＋ ADD", use_container_width=True, type="primary")
    with c2:
        with _popover("⇪ IMPORT"):
            st.caption("TradingView export, CSV or TXT. A column named symbol / ticker is used, otherwise the first column.")
            up = st.file_uploader("File", type=["csv", "txt"], key=f"w3_up_{key}", label_visibility="collapsed")
            if up is not None and st.button("VALIDATE & IMPORT", key=f"w3_imp_{key}", type="primary", use_container_width=True):
                try:
                    text = up.getvalue().decode("utf-8-sig", errors="replace")
                    if up.name.lower().endswith(".csv"):
                        fr = pd.read_csv(io.StringIO(text))
                        pref = [c for c in fr.columns if str(c).strip().lower() in {"symbol", "symbols", "ticker", "stock", "tradingsymbol"}]
                        text = ",".join(fr[pref[0] if pref else fr.columns[0]].dropna().astype(str))
                    raw = text.replace("\n", ",")
                    go = True
                except Exception as exc:
                    st.error(f"Could not read the file: {exc}")
    if go and raw and raw.strip():
        with st.spinner("Checking symbols…"):
            valid, bad = resolve_tokens(raw)
        if valid:
            added, err = (ud.add_symbols(sb, valid, ud.A_TABLE) if desk else add_to_list(sb, valid, list_name))
            if err:
                st.warning(err)
            if added:
                st.toast(f"Added {', '.join(added[:6])}{'…' if len(added) > 6 else ''} to {list_name}")
            elif not err:
                st.info("Already in this list.")
        if bad:
            st.warning("Not found on NSE: " + ", ".join(bad) + ". Check the spelling or use the NSE ticker.")


def _list_bar(names_in_use, current, supports, is_admin):
    tabs = list(names_in_use) + [DESK]
    with st.container(key="w3_lists"):
        cols = st.columns([1] * len(tabs) + [1.1, max(0.2, 7 - len(tabs))], gap="small")
        for c, name in zip(cols, tabs):
            if c.button(name.upper() if name != DESK else DESK, key=f"w3_l_{name}", use_container_width=True, type="primary" if name == current else "secondary"):
                st.session_state["w3_list"] = name
                st.rerun()
        with cols[len(tabs)]:
            with _popover("＋ LIST", not supports):
                nm = st.text_input("New list name", key="w3_newlist", placeholder="e.g. Swing, Long term, Breakouts").strip()
                if st.button("CREATE", key="w3_newlist_go", disabled=not nm or nm in tabs):
                    st.session_state.setdefault("w3_empty_lists", []).append(nm[:24])
                    st.session_state["w3_list"] = nm[:24]
                    st.rerun()
    if not supports:
        st.caption("Several named lists, notes and price alerts need one SQL statement: open MANAGE → Storage at the bottom of the page.")


def _actions(sb, shown, all_rows, list_name, key, on_open, can_edit, meta, lists):
    syms = [r["symbol"] for r in shown] or [r["symbol"] for r in all_rows]
    if not syms:
        return None
    with st.container(key=f"w3_act_{key}"):
        c1, c2, c3, c4, c5, c6 = st.columns([2.2, 0.9, 1, 1.1, 1.1, 0.9], gap="small")
        cur = st.session_state.get(f"w3_pick_{key}")
        if cur not in syms:
            st.session_state[f"w3_pick_{key}"] = syms[0]
        pick = c1.selectbox("Stock", syms, key=f"w3_pick_{key}", label_visibility="collapsed",
                            format_func=lambda s: f"{s}  ·  {next((fmt_price(r.get('last')) + '  ' + (format(r['pct'], '+.2f') + '%' if r.get('pct') is not None else '') for r in all_rows if r['symbol'] == s), '')}")
        if c2.button("OPEN ▸", key=f"w3_open_{key}", use_container_width=True, type="primary", disabled=on_open is None):
            on_open(pick)
        if c3.button("RESEARCH", key=f"w3_res_{key}", use_container_width=True):
            ud.go_research(pick)
        if c4.button("◆ AI ANALYSIS", key=f"w3_ai_{key}", use_container_width=True):
            ud.go_analysis(pick)
        with c5:
            with _popover("✎ NOTE / ALERT", not (can_edit and key != "desk" and lists_supported(sb))):
                m = meta.get(pick) or {}
                note = st.text_input("Note", value=m.get("note") or "", key=f"w3_note_{key}_{pick}", placeholder="Why it is on the list")
                a1, a2 = st.columns(2)
                above = a1.number_input("Alert at or above ₹", min_value=0.0, value=float(_num(m.get("alert_above")) or 0.0), step=1.0, key=f"w3_ab_{key}_{pick}")
                below = a2.number_input("Alert at or below ₹", min_value=0.0, value=float(_num(m.get("alert_below")) or 0.0), step=1.0, key=f"w3_bl_{key}_{pick}")
                st.caption("0 = no alert. A hit shows as an amber chip in the table and under the ALERTS HIT filter.")
                others = [x for x in lists if x != list_name]
                dst = st.selectbox("Move to list", ["—"] + others, key=f"w3_mv_{key}_{pick}") if others else "—"
                if st.button("SAVE", key=f"w3_meta_go_{key}_{pick}", type="primary"):
                    err = update_meta(sb, pick, list_name, {"note": note.strip() or None, "alert_above": above or None, "alert_below": below or None})
                    if not err and dst != "—":
                        err = move_to_list(sb, pick, list_name, dst)
                    st.error(err) if err else st.toast(f"{pick} saved")
                    if not err:
                        st.rerun()
        if c6.button("✕ REMOVE", key=f"w3_rm_{key}", use_container_width=True, disabled=not can_edit):
            err = ud.remove_symbol(sb, pick, ud.A_TABLE) if key == "desk" else remove_from_list(sb, [pick], list_name)
            st.error(err) if err else st.toast(f"Removed {pick}")
            if not err:
                st.rerun()
    return pick


def _manage(sb, rows, list_name, key, can_edit, supports):
    with st.expander("MANAGE · REMOVE SEVERAL · RENAME LIST · STORAGE"):
        if can_edit:
            dead = [r["symbol"] for r in rows if not r["has_data"]]
            sel = st.multiselect("Stocks to remove", [r["symbol"] for r in rows], key=f"w3_multi_{key}")
            a, b = st.columns(2)
            if a.button(f"REMOVE {len(sel)} SELECTED", key=f"w3_multi_go_{key}", disabled=not sel, use_container_width=True):
                err = remove_from_list(sb, sel, list_name) if key != "desk" else next((e for e in (ud.remove_symbol(sb, s, ud.A_TABLE) for s in sel) if e), None)
                st.error(err) if err else st.rerun()
            if b.button(f"REMOVE {len(dead)} WITHOUT DATA", key=f"w3_dead_{key}", disabled=not dead, use_container_width=True,
                        help="Symbols that return no price: usually typos, renamed or delisted stocks"):
                err = remove_from_list(sb, dead, list_name) if key != "desk" else next((e for e in (ud.remove_symbol(sb, s, ud.A_TABLE) for s in dead) if e), None)
                st.error(err) if err else st.rerun()
            if key != "desk" and supports and list_name != DEFAULT_LIST:
                r1, r2 = st.columns([3, 1])
                new = r1.text_input("Rename this list", value=list_name, key=f"w3_ren_{key}").strip()[:24]
                if r2.button("RENAME", key=f"w3_ren_go_{key}", disabled=not new or new == list_name, use_container_width=True):
                    err = rename_list(sb, list_name, new)
                    if not err:
                        st.session_state["w3_list"] = new
                    st.error(err) if err else st.rerun()
        st.markdown("**Storage**")
        st.caption("Run this once in Supabase → SQL Editor to enable several named lists, notes and price alerts. "
                   "Existing stocks go to the list 'Main'; nothing else changes." if not supports else "Named lists, notes and alerts are enabled.")
        st.code(SQL_LISTS, language="sql")


# ═════════════════════════ entry point ═════════════════════════
def render_watchlist(supabase, on_open=None, is_admin=False):
    """Main Watchlist module. Signature preserved for app.py."""
    _css()
    sb = supabase
    if sb is None:
        st.info("Cloud storage is not connected, so changes last only for this session. Add SUPABASE_URL and SUPABASE_KEY to Streamlit secrets.")
    supports = lists_supported(sb)
    all_rows = load_rows(sb)
    lists = list(dict.fromkeys([r["list_name"] for r in all_rows] + [DEFAULT_LIST] + st.session_state.get("w3_empty_lists", [])))
    if not supports:
        lists = [DEFAULT_LIST]
    current = st.session_state.get("w3_list", DEFAULT_LIST)
    if current not in lists and current != DESK:
        current = DEFAULT_LIST
    _list_bar(lists, current, supports, is_admin)

    if current == DESK:
        desk = ud.load_symbols(sb, ud.A_TABLE) if sb is not None else list(st.session_state.get("admin_watchlist", []))
        symbols, meta, can_edit, key = desk, {}, bool(is_admin), "desk"
        sub = "CURATED BY THE ARKA DESK" + (" · YOU CAN EDIT" if is_admin else " · READ-ONLY")
    else:
        mine = [r for r in all_rows if r["list_name"] == current]
        symbols, meta, can_edit, key = [r["symbol"] for r in mine], {r["symbol"]: r for r in mine}, True, "l_" + re.sub(r"\W", "_", current)
        sub = f"{len(symbols)} STOCKS · " + ("SYNCED TO THE CLOUD" if sb is not None else "THIS SESSION ONLY")
    st.markdown(f'<div class="w3-top"><b>{_e(current.upper())}</b><span>{_e(sub)}</span></div>', unsafe_allow_html=True)
    if can_edit:
        _add_box(sb, current, key, desk=current == DESK)

    o1, o2, o3, o4 = st.columns([1.2, 1.2, 1.2, 1.6], gap="small")
    view = o1.selectbox("View", VIEWS, key="w3_view", label_visibility="collapsed")
    group = o2.toggle("Group by sector", key="w3_group")
    rich = o3.toggle("News, filings & results", value=True, key="w3_rich", help="Adds today's news, filings and results dates to the events column (a few seconds the first time)")
    auto = o4.toggle("Live refresh every 60 s", value=True, key="w3_auto", disabled=not market_open(), help="Only while NSE is open")
    run_every = 60 if (auto and market_open()) else None

    @st.fragment(run_every=run_every)
    def live_part():
        _body(sb, symbols, meta, key, on_open, can_edit, view, group, rich, current, lists, supports)
    live_part()


def _body(sb, symbols, meta, key, on_open, can_edit, view, group, rich, list_name, lists, supports):
    symbols = list(dict.fromkeys(s for s in symbols if s))
    if not symbols:
        msg = ("<b>This list is empty.</b><br>Add stocks above: tickers or company names, comma-separated, or import a TradingView / CSV file."
               if can_edit else "<b>The Arka desk list is empty.</b>")
        st.markdown(f'<div class="w3-empty">{msg}</div>', unsafe_allow_html=True)
        _manage(sb, [], list_name, key, can_edit, supports) if can_edit else None
        return
    with st.spinner(f"Loading {len(symbols)} stocks…"):
        frames, intra = load_frames(tuple(symbols) + ("^NSEI",), view == "QUOTES")
        names = equity_names()
        deliv = delivery() if view in ("QUOTES", "TECHNICAL") else {}
        cals = calendars(symbols) if rich else {}
        news = news_by_symbol(symbols, names) if rich else {}
    nifty = frames.get("^NSEI")
    rows = build_rows(symbols, frames, names, deliv, cals, news, meta, nifty)
    if group:
        for r in rows:
            r["group"] = _group_of(r["symbol"], names)
    st.markdown(render_kpis(rows, compute("^NSEI", nifty)), unsafe_allow_html=True)

    f1, f2, f3 = st.columns([1.3, 4.4, 1.3], gap="small")
    query = f1.text_input("Search", placeholder="Search symbol / name…", key=f"w3_q_{key}", label_visibility="collapsed")
    counts = {f: len(filter_rows(rows, f)) for f in FILTERS}
    opts = [f for f in FILTERS if f == "ALL" or counts[f]]
    if st.session_state.get(f"w3_f_{key}") not in opts:
        st.session_state[f"w3_f_{key}"] = "ALL"
    fmt = lambda f: f if f == "ALL" else f"{f} {counts[f]}"
    flt = (f2.pills("Filter", opts, key=f"w3_f_{key}", format_func=fmt, label_visibility="collapsed") if hasattr(st, "pills")
           else f2.radio("Filter", opts, key=f"w3_f_{key}", format_func=fmt, horizontal=True, label_visibility="collapsed")) or "ALL"
    sort_opts = list(SORTS)
    if view == "HEATMAP":
        hf = f3.selectbox("Colour by", ["1D", "1W", "1M", "3M", "YTD", "1Y"], key="w3_heat", label_visibility="collapsed")
    else:
        how = f3.selectbox("Sort", sort_opts, key=f"w3_s_{key}", label_visibility="collapsed")
    shown = filter_rows(rows, flt, query)
    if not shown:
        st.markdown('<div class="w3-empty">No stocks match this filter.</div>', unsafe_allow_html=True)
    elif view == "HEATMAP":
        field = {"1D": "pct", "1W": "r1w", "1M": "r1m", "3M": "r3m", "YTD": "ytd", "1Y": "r1y"}[hf]
        st.markdown(render_heatmap(shown, field, group), unsafe_allow_html=True)
    else:
        shown = sort_rows(shown, how, group)
        sel = st.session_state.get(f"w3_pick_{key}")
        ctx = {"names": names, "meta": meta, "deliv": deliv, "intra": intra}
        st.markdown(render_table(shown, view, ctx, group, sel), unsafe_allow_html=True)

    pick = _actions(sb, shown, rows, list_name, key, on_open, can_edit, meta, lists)
    if pick:
        st.markdown(render_card(next((r for r in rows if r["symbol"] == pick), None), names, meta.get(pick), deliv), unsafe_allow_html=True)

    asof = max((r["date"] for r in rows if r["has_data"]), default=None)
    c1, c2, c3, c4 = st.columns([4, 0.9, 0.9, 1.2], gap="small")
    c1.caption(f"Showing {len(shown)} of {len(rows)}" + (f" · prices as of {asof:%d %b %Y}" if asof is not None else "")
               + " · Yahoo Finance (delayed) · ● / * = today's candle is still forming (volume projected to a full day) · "
               "delivery from NSE's daily file · hover an event chip for details.")
    if c2.button("↻ REFRESH", key=f"w3_ref_{key}", use_container_width=True):
        for fn in (_live, _intraday):
            fn.clear()
        st.rerun(scope="fragment")
    out = pd.DataFrame([{"symbol": r["symbol"], "name": r.get("name"), "ltp": r.get("last"), "chg": r.get("chg"), "pct_chg": r.get("pct"),
                         "open": r.get("open"), "high": r.get("high"), "low": r.get("low"), "prev_close": r.get("prev"), "volume": r.get("volume"),
                         "vol_x_20d": r.get("vol_x"), "deliv_pct": r.get("deliv_pct"), "r1w": r.get("r1w"), "r1m": r.get("r1m"), "r3m": r.get("r3m"),
                         "ytd": r.get("ytd"), "r1y": r.get("r1y"), "rsi": r.get("rsi"), "events": " | ".join(e[0] for e in r["events"])} for r in shown])
    c3.download_button("⤓ CSV", out.to_csv(index=False).encode(), f"arka_{key}_{_now():%Y%m%d}.csv", "text/csv", key=f"w3_csv_{key}", use_container_width=True)
    c4.download_button("⤓ TRADINGVIEW", "\n".join(f"NSE:{r['symbol']}" for r in shown), f"arka_{key}_tv.txt", "text/plain", key=f"w3_tv_{key}",
                       use_container_width=True, help="Import into a TradingView watchlist")
    if can_edit:
        _manage(sb, rows, list_name, key, can_edit, supports)
