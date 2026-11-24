"""
Arka Trades — Watchlist (v6)

v6: rebuilt to be easy to read the first time and calm to look at.
  * A details panel sits next to the list (under the stock on a phone). Click a stock and it shows the price, today's and
    the 52-week range, the key numbers in plain words, what is happening today and the last 24 hours of news and filings.
    It is part of the page, not a pop-up, so it can never get stuck over the screen (v5's news pop-up could).
  * Remove like TradingView: point at a row and click ✕ (always shown faintly on touch screens). An "Undo" bar
    brings the stock back, with its note and alerts.
  * Fewer, clearer columns with plain names ("Today", "Day's range", "What's happening"); columns drop out as the
    screen gets narrow. Views: Today, Returns, Technicals, Heatmap. Trend and RSI are written as words.
  * Filters and sorting in plain words; a four-part summary instead of six cards.
  * Needs auth.py with WL_PREFIX (clicks on rows and ✕ reach this page). With an older auth.py the page still works:
    a picker in the panel chooses the stock and rows open the stock as before.
  Data, lists, notes, alerts, storage and the public API are unchanged.

(v5 notes follow)

v5: a calmer, roomier screen. Lists are pills; adding and importing sit in one tidy row; the three switches moved into
an Options menu; six summary cards instead of eight; filters wrap instead of running off the edge; the Quotes table
shows the day's low and high as one range bar with the numbers at its ends (three fewer columns) and the change as one
cell. Technical view gains a Trend column (20 EMA / 50-day / 200-day) and distance from the 52-week high. Any row opens
the stock. Data, lists, alerts, storage and the public API are unchanged.

(v4 notes follow)

A market-watch screen: one row per stock with the full quote, an intraday chart and an "Events" column that says
what is happening to each stock today (news, filings, results, Open = high, gaps, 52-week highs, volume spikes,
your own price alerts).

What changed vs v3
------------------
NEWS YOU CAN READ
  * Tap any event chip and a sheet opens over the page (like a broker's "Events / Indicators" popup): every news
    story and exchange filing of the last 24 hours for that stock, with the time, how long ago, the source, the
    kind of story (results, order win, filing …), a summary when the feed has one and a link to the full article.
    The same sheet explains every price signal on that row in plain words. Tap outside or ✕ to close.
    Pure HTML (<details>), so it works on phone and desktop without a custom component.
  * A "News" button in the action bar opens the same content in a proper dialog for the selected stock.
LOOK
  * Rebuilt on the app's design system: Inter, sentence case, soft rounded cards and pill chips; no uppercase
    monospace labels. Views and filters are segmented controls.
  * Phone gets its own layout: a stacked card per stock (symbol, price, change, chips) instead of a 14-column table.
Data, lists, alerts, storage and the public API are unchanged from v3.

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
MONO = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"   # v4: one sans family, numbers use tabular figures
SANS = MONO

DEFAULT_LIST = "Main"
DESK = "Arka desk"
VIEWS = ["Quotes", "Performance", "Technical", "Heatmap"]
FILTERS = ["All", "Gainers", "Losers", "With events", "News today", "Open = high", "Open = low", "52W high", "Volume spike",
           "Above PDH", "Below PDL", "Alerts hit", "No data"]
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


def _phone() -> bool:
    """app.py sets _ak_phone for narrow screens (v9 shell)."""
    try:
        return bool(st.session_state.get("_ak_phone"))
    except Exception:
        return False


def _seg(where, label, options, key):
    """Segmented control when this Streamlit has one (1.40+), otherwise a select box. Never returns None."""
    fn = getattr(where, "segmented_control", None)
    if fn is not None:
        try:
            def _keep():   # tapping the active segment again would clear it; put the first option back
                if st.session_state.get(key) not in options:
                    st.session_state[key] = options[0]
            v = fn(label, options, key=key, label_visibility="collapsed", on_change=_keep)
            return v if v in options else options[0]
        except TypeError:
            pass
    return where.selectbox(label, options, key=key, label_visibility="collapsed")


_NEWS_PREFIX = ("News ", "Filing", "Results out")
_KIND = {"EARNINGS": ("Results", PURPLE), "RESULTS": ("Results", PURPLE), "ORDER": ("Order win", GREEN), "DIVIDEND": ("Dividend", BLUE),
         "BONUS": ("Bonus", BLUE), "SPLIT": ("Split", BLUE), "BUYBACK": ("Buyback", GREEN), "RATING": ("Rating", YELLOW),
         "UPGRADE": ("Upgrade", GREEN), "DOWNGRADE": ("Downgrade", RED), "MERGER": ("M&A", CYAN), "ACQUISITION": ("M&A", CYAN),
         "MGMT": ("Management", YELLOW), "BLOCK": ("Block deal", CYAN), "BULK": ("Bulk deal", CYAN), "PLEDGE": ("Pledge", RED),
         "FUNDRAISE": ("Fund raise", YELLOW), "REGULATORY": ("Regulatory", RED), "LEGAL": ("Legal", RED)}


def _is_news_event(label: str) -> bool:
    return str(label).startswith(_NEWS_PREFIX)


def _as_ist(dt):
    if dt is None:
        return None
    try:
        return dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)
    except Exception:
        return None


def _when(a, now=None):
    """('14:32', '2 h ago') for a story; clock shows the date too when it is not today."""
    dt = _as_ist(a.get("pub_dt"))
    if dt is None:
        return (a.get("time_str") or ""), ""
    now = now or _now()
    mins = max(0.0, (now - dt).total_seconds() / 60)
    rel = "just now" if mins < 1 else f"{int(mins)} min ago" if mins < 60 else f"{int(mins // 60)} h ago" if mins < 1440 else f"{int(mins // 1440)} d ago"
    clock = dt.strftime("%H:%M") if dt.date() == now.date() else dt.strftime("%d %b, %H:%M")
    return clock, rel


def _summary_of(a) -> str:
    for k in ("summary", "description", "desc", "snippet", "body", "text", "content"):
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", v))).strip()
            if t and t.lower() != str(a.get("title", "")).strip().lower():
                return t[:320] + ("…" if len(t) > 320 else "")
    return ""


def _story_html(a, now=None) -> str:
    clock, rel = _when(a, now)
    if a.get("kind") == "filing":
        tag, tc = "Exchange filing", CYAN
    else:
        ev = str(a.get("event") or "").upper()
        tag, tc = _KIND.get(ev, (ev.replace("_", " ").capitalize(), BLUE) if ev and ev != "NEWS" else ("", BLUE))
    sc = int(a.get("score") or 0)
    tone = (f'<span class="ns-k" style="--c:{GREEN}">Positive</span>' if sc > 0 else
            f'<span class="ns-k" style="--c:{RED}">Negative</span>' if sc < 0 else "")
    meta = [f'<span class="t">{_e(clock)}</span>' if clock else "", _e(rel), _e(a.get("source") or "")]
    meta = '<span class="dot">·</span>'.join(m for m in meta if m)
    tags = (f'<span class="ns-k" style="--c:{tc}">{_e(tag)}</span>' if tag else "") + tone
    summ = _summary_of(a)
    link = a.get("link") or ""
    body = (f'<div class="ns-m">{meta}{tags}</div><div class="ns-t">{_e(str(a.get("title") or "")[:240])}</div>'
            + (f'<div class="ns-d">{_e(summ)}</div>' if summ else "")
            + (f'<div class="ns-go">{"Open filing" if a.get("kind") == "filing" else "Read full story"} ↗</div>' if link else ""))
    if link:
        return f'<a class="ns" href="{_e(link)}" target="_blank" rel="noopener noreferrer">{body}</a>'
    return f'<div class="ns" style="display:block;padding:12px 14px;margin:0 0 8px;border-radius:12px;background:#141417;border:1px solid #1F1F24">{body}</div>'



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
        "Gainers": lambda r: (r.get("pct") or 0) > 0, "Losers": lambda r: (r.get("pct") or 0) < 0,
        "With events": lambda r: bool(r["events"]), "Open = high": lambda r: has(r, "Open=High"), "Open = low": lambda r: has(r, "Open=Low"),
        "52W high": lambda r: has(r, "52W high"), "Volume spike": lambda r: (r.get("vol_x") or 0) >= 2,
        "Above PDH": lambda r: has(r, "PDH break"), "Below PDL": lambda r: has(r, "PDL break"),
        "News today": lambda r: bool(r.get("stories")), "Alerts hit": lambda r: bool(r.get("alert_hit")), "No data": lambda r: not r["has_data"],
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
    with st.container(key=f"w3_addrow_{key}"):
        _add_box_inner(sb, list_name, key, desk)


def _add_box_inner(sb, list_name, key, desk=False):
    c1, c2 = st.columns([5, 1], gap="small")
    with c1:
        with st.form(f"w3_add_{key}", clear_on_submit=True):
            a, b = st.columns([5, 1], gap="small")
            raw = a.text_input("Add", placeholder=f"Add stocks to {list_name}: type symbols or company names, separated by commas", label_visibility="collapsed")
            go = b.form_submit_button("Add", use_container_width=True, type="primary")
    with c2:
        with _popover("Import"):
            st.caption("TradingView export, CSV or TXT. A column named symbol / ticker is used, otherwise the first column.")
            up = st.file_uploader("File", type=["csv", "txt"], key=f"w3_up_{key}", label_visibility="collapsed")
            if up is not None and st.button("Check and import", key=f"w3_imp_{key}", type="primary", use_container_width=True):
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


def _pick_list():
    v = st.session_state.get("w3_list_pills")
    if v:
        st.session_state["w3_list"] = v


def _list_bar(names_in_use, current, supports, is_admin):
    tabs = list(names_in_use) + [DESK]
    with st.container(key="w3_lists"):
        a, b = st.columns([6, 1], gap="small")
        done = False
        if hasattr(st, "pills"):
            try:
                if st.session_state.get("w3_list_pills") != current:
                    st.session_state["w3_list_pills"] = current
                a.pills("List", tabs, key="w3_list_pills", label_visibility="collapsed", on_change=_pick_list)
                done = True
            except Exception:
                done = False
        if not done:                                       # Streamlit without pills: plain buttons
            cols = a.columns([1] * len(tabs) + [max(0.2, 6 - len(tabs))], gap="small")
            for c, name in zip(cols, tabs):
                if c.button(name, key=f"w3_l_{name}", use_container_width=True, type="primary" if name == current else "secondary"):
                    st.session_state["w3_list"] = name
                    st.rerun()
        with b:
            with _popover("New list", not supports):
                nm = st.text_input("New list name", key="w3_newlist", placeholder="e.g. Swing, Long term, Breakouts").strip()
                if st.button("Create", key="w3_newlist_go", disabled=not nm or nm in tabs):
                    st.session_state.setdefault("w3_empty_lists", []).append(nm[:24])
                    st.session_state["w3_list"] = nm[:24]
                    st.rerun()
    if not supports:
        st.caption("Several named lists, notes and price alerts need one SQL statement: open Manage list → Storage at the bottom of the page.")


def _manage(sb, rows, list_name, key, can_edit, supports):
    with st.expander("Manage list: remove several, rename, storage"):
        if can_edit:
            dead = [r["symbol"] for r in rows if not r["has_data"]]
            sel = st.multiselect("Stocks to remove", [r["symbol"] for r in rows], key=f"w3_multi_{key}")
            a, b = st.columns(2)
            if a.button(f"Remove {len(sel)} selected", key=f"w3_multi_go_{key}", disabled=not sel, use_container_width=True):
                err = remove_from_list(sb, sel, list_name) if key != "desk" else next((e for e in (ud.remove_symbol(sb, s, ud.A_TABLE) for s in sel) if e), None)
                st.error(err) if err else st.rerun()
            if b.button(f"Remove {len(dead)} without data", key=f"w3_dead_{key}", disabled=not dead, use_container_width=True,
                        help="Symbols that return no price: usually typos, renamed or delisted stocks"):
                err = remove_from_list(sb, dead, list_name) if key != "desk" else next((e for e in (ud.remove_symbol(sb, s, ud.A_TABLE) for s in dead) if e), None)
                st.error(err) if err else st.rerun()
            if key != "desk" and supports and list_name != DEFAULT_LIST:
                r1, r2 = st.columns([3, 1])
                new = r1.text_input("Rename this list", value=list_name, key=f"w3_ren_{key}").strip()[:24]
                if r2.button("Rename", key=f"w3_ren_go_{key}", disabled=not new or new == list_name, use_container_width=True):
                    err = rename_list(sb, list_name, new)
                    if not err:
                        st.session_state["w3_list"] = new
                    st.error(err) if err else st.rerun()
        st.markdown("**Storage**")
        st.caption("Run this once in Supabase → SQL Editor to enable several named lists, notes and price alerts. "
                   "Existing stocks go to the list 'Main'; nothing else changes." if not supports else "Named lists, notes and alerts are enabled.")
        st.code(SQL_LISTS, language="sql")


# ═════════════════════════ v6 look ═════════════════════════
def _css():
    """v6: one calm card list on the left, a details panel on the right (inline, never an overlay)."""
    st.markdown(f"""<style>
    .w6, .w6 *{{box-sizing:border-box}}
    .w6{{font-family:{SANS};font-variant-numeric:tabular-nums;color:{TX};-webkit-font-smoothing:antialiased}}
    /* title */
    .w6-head{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:12px 2px 2px}}
    .w6-head h2{{margin:0 !important;padding:0 !important;font:650 25px/1.2 {SANS} !important;letter-spacing:-.5px;color:#F5F5F7 !important}}
    .w6-head .sub{{font-size:13px;color:#8A8A90;padding-bottom:3px;text-align:right}}
    .w6-hint{{display:flex;gap:8px;align-items:center;font-size:12.5px;color:#7A7A81;margin:6px 2px 14px}}
    .w6-hint i{{font-style:normal;display:inline-grid;place-items:center;width:18px;height:18px;border-radius:6px;background:#1A1A1D;color:#A3A3A8;font-size:11px}}
    /* summary */
    .w6-sum{{display:grid;grid-template-columns:1.25fr 1fr 1fr 1fr;gap:1px;background:#1F1F23;border:1px solid #232326;border-radius:14px;overflow:hidden;margin:4px 0 14px}}
    .w6-sum>div{{background:#111113;padding:12px 16px;min-width:0}}
    .w6-sum .l{{font-size:12px;color:#8A8A90}}
    .w6-sum .v{{font:600 17px/1.25 {SANS};margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#F2F2F4}}
    .w6-sum .s{{font-size:12px;color:#6E6E75;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .w6-bb{{display:flex;height:4px;border-radius:4px;overflow:hidden;background:#232327;margin-top:9px}} .w6-bb i{{display:block;height:100%}}
    @media (max-width:760px){{.w6-sum{{grid-template-columns:1fr 1fr}} .w6-sum>div{{padding:11px 13px}} .w6-sum .v{{font-size:15.5px}}}}
    .up{{color:{GREEN}}} .dn{{color:{RED}}} .fl{{color:#A3A3A8}} .na{{color:#4A4A50}}
    /* the list */
    .w6-list{{container-type:inline-size;border:1px solid #232326;border-radius:16px;background:#111113;overflow:hidden}}
    .w6-hd,.w6-row{{display:grid;grid-template-columns:var(--cols);column-gap:16px;align-items:center;padding-left:16px;padding-right:46px}}
    .w6-hd{{padding-top:11px;padding-bottom:11px;background:#141416;border-bottom:1px solid #232326;font-size:12px;font-weight:500;color:#7C7C83}}
    .w6-hd>div{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:default}}
    .w6-row{{position:relative;padding-top:12px;padding-bottom:12px;border-bottom:1px solid #1B1B1F;cursor:pointer;transition:background .12s ease}}
    .w6-row:last-child{{border-bottom:0}}
    .w6-row:hover{{background:#16161A}}
    .w6-row.sel{{background:rgba(255,159,10,.075);box-shadow:inset 3px 0 0 {AMBER}}}
    .w6-hd>div,.w6-row>div{{min-width:0;text-align:right}}
    .w6-hd>div:first-child,.w6-row>div:first-child,.w6-hd>.lft,.w6-row>.lft{{text-align:left}}
    .w6-hd>.ctr,.w6-row>.ctr{{text-align:center}}
    .w6-grp{{padding:9px 16px;background:#0E0E10;border-bottom:1px solid #1B1B1F;font-size:12.5px;font-weight:600;color:#A3A3A8}}
    .w6-grp span{{font-weight:500;color:#6E6E75;margin-left:6px}}
    .w6-sym b{{display:block;font-size:14.5px;font-weight:650;color:#F4F4F6;letter-spacing:-.1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .w6-sym span{{display:block;font-size:12px;color:#7A7A81;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .w6-sym em{{font-style:normal;color:{AMBER};font-size:11px;margin-left:5px}}
    .w6-row.sel .w6-sym b{{color:{AMBER}}}
    .w6-px{{font-size:14.5px;font-weight:650;color:#F4F4F6;white-space:nowrap}}
    .w6-px .lv{{display:inline-block;width:6px;height:6px;border-radius:50%;background:{AMBER};margin-left:6px;vertical-align:middle;
               box-shadow:0 0 0 3px rgba(255,159,10,.18)}}
    .w6-chg{{display:inline-block;min-width:70px;text-align:center;padding:4px 9px;border-radius:8px;font-weight:650;font-size:13px;white-space:nowrap}}
    .w6-chg.up{{background:rgba(48,209,88,.13)}} .w6-chg.dn{{background:rgba(255,69,58,.13)}} .w6-chg.fl{{background:#1C1C20}}
    .w6-sub{{display:block;font-size:11.5px;color:#7A7A81;margin-top:4px;white-space:nowrap}}
    .w6-ret{{font-weight:600;font-size:13.5px;white-space:nowrap}}
    /* range bar */
    .w6-rg{{display:block}}
    .w6-rg .bar{{display:block;position:relative;height:5px;border-radius:5px;background:#26262B;margin:2px 0 6px}}
    .w6-rg .bar i{{position:absolute;top:-4px;width:13px;height:13px;border-radius:50%;transform:translateX(-6.5px);border:2.5px solid #111113;background:#E6E6EA}}
    .w6-rg .lh{{display:flex;justify-content:space-between;font-size:11px;color:#6E6E75;white-space:nowrap;gap:6px}}
    /* chips */
    .w6-chips{{display:flex;flex-wrap:wrap;gap:5px;justify-content:flex-start}}
    .w6-chip{{--c:#8A8A90;display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;font-size:11.5px;font-weight:600;white-space:nowrap;
             color:var(--c);background:color-mix(in srgb,var(--c) 13%,transparent)}}
    .w6-chip.nw::before{{content:"";width:5px;height:5px;border-radius:50%;background:var(--c)}}
    .w6-more{{font-size:11.5px;font-weight:600;color:#8A8A90;padding:3px 7px;border-radius:999px;background:#1C1C20}}
    /* trend / rsi words */
    .w6-word{{display:inline-block;font-size:12.5px;font-weight:600;padding:3px 9px;border-radius:8px;white-space:nowrap;
             color:var(--c);background:color-mix(in srgb,var(--c) 12%,transparent)}}
    /* remove (TradingView style: appears on hover, always faintly visible on touch screens) */
    .w6-x{{position:absolute;right:10px;top:50%;margin-top:-14px;width:28px;height:28px;border-radius:8px;display:grid;place-items:center;
          font-size:14px;line-height:1;color:#8A8A90;opacity:0;transition:opacity .12s ease,background .12s ease,color .12s ease}}
    .w6-row:hover .w6-x{{opacity:1}}
    .w6-x:hover{{background:rgba(255,69,58,.16);color:#FF6961}}
    @media (hover:none){{.w6-x{{opacity:.6}}}}
    /* column layouts per view; columns drop out as the list gets narrow */
    .v-today{{--cols:minmax(140px,1.5fr) 92px 96px minmax(132px,1.15fr) 112px minmax(130px,1.25fr)}}
    .v-ret{{--cols:minmax(140px,1.5fr) 92px repeat(5,minmax(62px,.62fr)) 104px}}
    .v-tech{{--cols:minmax(140px,1.5fr) 92px minmax(120px,1fr) minmax(108px,.9fr) 96px 86px 80px}}
    @container (max-width:900px){{ .v-today{{--cols:minmax(130px,1.5fr) 88px 92px minmax(120px,1.1fr) 104px}} .v-today .h5{{display:none}}
                                   .v-ret{{--cols:minmax(130px,1.5fr) 88px repeat(5,minmax(58px,.6fr))}} .v-ret .h7{{display:none}}
                                   .v-tech{{--cols:minmax(130px,1.5fr) 88px minmax(116px,1fr) minmax(104px,.9fr) 92px}} .v-tech .h5,.v-tech .h6{{display:none}} }}
    @container (max-width:640px){{ .v-today{{--cols:minmax(120px,1.4fr) 84px 90px}} .v-today .h3,.v-today .h4{{display:none}}
                                   .v-ret{{--cols:minmax(120px,1.4fr) 84px repeat(3,minmax(56px,.6fr))}} .v-ret .h3,.v-ret .h6{{display:none}}
                                   .v-tech{{--cols:minmax(120px,1.4fr) 84px minmax(110px,1fr)}} .v-tech .h3,.v-tech .h4{{display:none}} }}
    /* undo bar */
    .w6-undo{{display:flex;align-items:center;gap:10px;font-size:13px;color:#D6D6DA}}
    .w6-undo b{{color:#fff}}
    div[class*="st-key-w6_undo"]{{border:1px solid #2C2C31;background:#161619;border-radius:12px;padding:6px 8px 6px 14px;margin:0 0 10px}}
    div[class*="st-key-w6_undo"] [data-testid="stHorizontalBlock"]{{align-items:center}}
    /* heatmap */
    .w6-heat{{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:6px}}
    .w6-tile{{padding:11px 12px;min-height:70px;border-radius:12px;cursor:pointer;transition:filter .12s, box-shadow .12s}}
    .w6-tile:hover{{filter:brightness(1.15)}} .w6-tile.sel{{box-shadow:0 0 0 2px {AMBER} inset}}
    .w6-tile b{{display:block;font-size:13px;font-weight:650;color:#fff}} .w6-tile .p{{font-size:11.5px;color:#EDEDED;margin-top:3px;opacity:.85}}
    .w6-tile .c{{font-size:15.5px;font-weight:700;color:#fff;margin-top:3px}}
    .w6-gh{{font-size:12.5px;font-weight:600;color:#A3A3A8;margin:12px 2px 6px}}
    /* phone cards */
    .w6-cards{{display:flex;flex-direction:column;gap:8px}}
    .w6-card{{position:relative;background:#111113;border:1px solid #1F1F23;border-radius:14px;padding:12px 44px 12px 14px;cursor:pointer}}
    .w6-card.sel{{border-color:rgba(255,159,10,.55);background:rgba(255,159,10,.06)}}
    .w6-card .t{{display:flex;justify-content:space-between;align-items:center;gap:10px}}
    .w6-card .t .w6-sym{{min-width:0}} .w6-card .r{{text-align:right;flex:0 0 auto}}
    .w6-card .r .w6-chg{{min-width:0;margin-top:4px;padding:2px 8px;font-size:12px}}
    .w6-card .w6-chips{{margin-top:9px}}
    .w6-card .w6-x{{opacity:.6;right:8px}}
    .w6-mg{{font-size:12.5px;font-weight:600;color:#A3A3A8;margin:10px 2px 0}}
    /* details panel */
    div[class*="st-key-w6_panel"]{{border:1px solid #26262B;background:#111113;border-radius:18px;padding:18px 18px 8px}}
    @media (min-width:900px){{div[class*="st-key-w6_panel"]{{position:sticky;top:12px}}}}
    .w6-p-hd{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
    .w6-p-hd b{{display:block;font-size:21px;font-weight:700;color:#fff;letter-spacing:-.3px}}
    .w6-p-hd small{{display:block;font-size:12.5px;color:#8A8A90;margin-top:3px;line-height:1.4}}
    .w6-p-hd .q{{text-align:right;white-space:nowrap;flex:0 0 auto}} .w6-p-hd>div:first-child{{min-width:0}}
    .w6-p-hd .q b{{font-size:21px}} .w6-p-hd .q span{{display:block;font-size:13px;font-weight:650;margin-top:3px}}
    .w6-p-hd .q small{{font-size:11.5px}}
    .w6-note{{margin-top:10px;font-size:12.5px;color:{AMBER};background:rgba(255,159,10,.08);border-radius:10px;padding:8px 11px}}
    .w6-sec{{display:flex;justify-content:space-between;align-items:baseline;font-size:12.5px;font-weight:650;color:#C9C9CE;margin:16px 0 9px}}
    .w6-sec em{{font-style:normal;font-weight:500;color:#6E6E75;font-size:12px}}
    .w6-rows2{{display:grid;grid-template-columns:1fr;gap:14px}}
    .w6-rgl{{font-size:12px;color:#8A8A90;margin-bottom:6px;display:flex;justify-content:space-between}}
    .w6-rgl b{{color:#D6D6DA;font-weight:600}}
    .w6-band{{position:relative;height:6px;border-radius:6px;background:linear-gradient(90deg,rgba(255,69,58,.45),#2A2A2F 50%,rgba(48,209,88,.45))}}
    .w6-band i{{position:absolute;top:-5px;width:4px;height:16px;border-radius:2px;background:#fff;transform:translateX(-2px);box-shadow:0 0 0 2px #111113}}
    .w6-bl{{display:flex;justify-content:space-between;font-size:11.5px;color:#6E6E75;margin-top:6px}}
    .w6-stats{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1F1F23;border-radius:12px;overflow:hidden;margin-top:2px}}
    .w6-stats>div{{background:#141417;padding:10px 12px;min-width:0}}
    .w6-stats span{{display:block;font-size:11.5px;color:#7A7A81}}
    .w6-stats b{{display:block;font-size:14px;font-weight:600;color:#ECECEF;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .w6-stats b small{{font-size:11.5px;font-weight:500;color:#8A8A90;margin-left:4px}}
    .w6-sig{{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid #1C1C20}} .w6-sig:last-child{{border-bottom:0}}
    .w6-sig .w6-chip{{flex:0 0 auto}} .w6-sig p{{margin:1px 0 0 !important;font-size:12.5px;line-height:1.5;color:#A3A3A8}}
    .w6-none{{font-size:12.5px;color:#6E6E75;padding:4px 0 2px}}
    a.ns{{display:block;text-decoration:none !important;padding:11px 13px;margin:0 0 8px;border-radius:12px;background:#151518;border:1px solid #202025;
         transition:border-color .15s, background .15s}}
    a.ns:hover{{border-color:#36363C;background:#18181C}}
    .ns-m{{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:11.5px;color:#7A7A81}}
    .ns-m .t{{color:#E8E8EC;font-weight:600}} .ns-m .dot{{opacity:.5}}
    .ns-k{{--c:#8A8A90;padding:1px 7px;border-radius:999px;font-weight:600;font-size:11px;color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent)}}
    .ns-t{{font-size:13.5px;font-weight:600;line-height:1.45;color:#F2F2F5;margin-top:6px}}
    .ns-d{{font-size:12.5px;line-height:1.55;color:#A3A3A8;margin-top:5px}}
    .ns-go{{font-size:12px;font-weight:600;color:{AMBER};margin-top:7px}}
    .w6-empty{{padding:38px 20px;text-align:center;color:#8A8A90;border:1px dashed #2A2A2F;border-radius:16px;background:#111113;font:13.5px/1.8 {SANS}}}
    .w6-empty b{{display:block;color:#E6E6EA;font-size:16px;margin-bottom:4px}}
    .w6-empty code{{background:#1C1C20;border-radius:6px;padding:1px 6px;color:#E6E6EA}}
    .w6-foot{{font-size:12px;color:#6E6E75;line-height:1.6}}
    /* native controls */
    div[class*="st-key-w3_addrow"]{{border:1px solid #232326;background:#111113;border-radius:16px;padding:12px 14px 0;margin:6px 0 4px}}
    div[class*="st-key-w3_addrow"] [data-testid="stForm"]{{border:0 !important;padding:0 !important;background:transparent !important}}
    div[class*="st-key-w3_lists"]{{margin:2px 0 0}}
    div[class*="st-key-w3_lists"] button{{border-radius:999px !important;min-height:34px !important}}
    div[class*="st-key-w3_lists"] button p{{font:600 13px {SANS} !important}}
    div[class*="st-key-w6_flt"]{{margin:0 0 10px}}
    div[class*="st-key-w6_panel"] button p{{font-weight:600 !important}}
    div[class*="st-key-w6_panel"] [data-testid="stExpander"] details{{border-radius:12px !important;border-color:#26262B !important;background:#141417 !important}}
    </style>""", unsafe_allow_html=True)


# ═════════════════════════ words and small cells ═════════════════════════
_NICE = [("Open=High", "Opened at high"), ("Open=Low", "Opened at low"), ("PDH break", "Above yesterday's high"),
         ("PDL break", "Below yesterday's low"), ("NR7", "Quiet day"), ("Inside", "Inside day"), ("52W high", "52-week high"),
         ("52W low", "52-week low"), ("↑200 DMA", "Crossed above 200-day"), ("↓200 DMA", "Crossed below 200-day")]


def _nice(label: str) -> str:
    """Chip text a first-time reader understands. Filters keep using the raw labels."""
    lab = str(label)
    for raw, nice in _NICE:
        if lab == raw:
            return nice
    m = re.match(r"^News (\d+)$", lab)
    if m:
        return f"{m.group(1)} news"
    m = re.match(r"^Filings? (\d+)$", lab)
    if m:
        return f"{m.group(1)} filing{'s' if m.group(1) != '1' else ''}"
    m = re.match(r"^Vol ([\d.]+)×$", lab)
    if m:
        return f"Volume {m.group(1)}×"
    return lab


def _cls(v) -> str:
    return "na" if v is None else "up" if v > 0 else "dn" if v < 0 else "fl"


def _chg_pill(pct) -> str:
    if pct is None:
        return '<span class="na">—</span>'
    return f'<span class="w6-chg {_cls(pct)}">{"▲" if pct > 0 else "▼" if pct < 0 else ""} {abs(pct):.2f}%</span>'


def _retc(v, d=1) -> str:
    if v is None:
        return '<span class="na">—</span>'
    return f'<span class="w6-ret {_cls(v)}">{v:+.{d}f}%</span>'


def _range_cell(lo, hi, v) -> str:
    if lo is None or hi is None or v is None:
        return '<span class="na">—</span>'
    p = 50.0 if hi <= lo else max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))
    return (f'<span class="w6-rg" title="Where the price is between today\'s low and high"><span class="bar"><i style="left:{p:.0f}%"></i></span>'
            f'<span class="lh"><span>{fmt_price(lo)}</span><span>{fmt_price(hi)}</span></span></span>')


def trend_word(r):
    """('Strong uptrend', colour, detail) from the price against its 20-day, 50-day and 200-day averages."""
    a, b, c = r.get("vs_ema20"), r.get("vs_sma50"), r.get("vs_sma200")
    if c is None and b is None:
        return None
    up = lambda x: x is not None and x >= 0
    dn = lambda x: x is not None and x < 0
    detail = " · ".join(f"{lab} {'above' if v >= 0 else 'below'}" for lab, v in (("20-day", a), ("50-day", b), ("200-day", c)) if v is not None)
    if c is None:
        word = ("Uptrend", GREEN) if up(b) else ("Downtrend", RED)
    elif up(a) and up(b) and up(c):
        word = ("Strong uptrend", GREEN)
    elif up(b) and up(c):
        word = ("Uptrend", GREEN)
    elif dn(a) and dn(b) and dn(c):
        word = ("Strong downtrend", RED)
    elif dn(b) and dn(c):
        word = ("Downtrend", RED)
    else:
        word = ("Sideways", AMBER)
    return word[0], word[1], "Price vs its averages: " + detail


def rsi_word(v):
    v = _num(v)
    if v is None:
        return None
    if v >= 70:
        return f"{v:.0f} · overbought", AMBER
    if v <= 30:
        return f"{v:.0f} · oversold", PURPLE
    if v >= 55:
        return f"{v:.0f} · strong", GREEN
    if v <= 45:
        return f"{v:.0f} · weak", "#FF8A80"
    return f"{v:.0f} · neutral", TX2


def _word(t) -> str:
    if not t:
        return '<span class="na">—</span>'
    tip = f' title="{_e(t[2])}"' if len(t) > 2 else ""
    return f'<span class="w6-word" style="--c:{t[1]}"{tip}>{_e(t[0])}</span>'


def _chips(evs, cap=2) -> str:
    if not evs:
        return '<span class="na">—</span>'
    evs = sorted(evs, key=lambda e: 0 if str(e[0]).startswith("Alert") else 1 if _is_news_event(e[0]) else 2)
    out = "".join(f'<span class="w6-chip{" nw" if _is_news_event(lab) else ""}" style="--c:{c}" title="{_e(tip)}">{_e(_nice(lab))}</span>'
                  for lab, c, tip, _ in evs[:cap])
    if len(evs) > cap:
        out += f'<span class="w6-more" title="Click the stock to see all">+{len(evs) - cap}</span>'
    return f'<div class="w6-chips">{out}</div>'


def _signal_html(e) -> str:
    lab, c, tip, _ = e
    return f'<div class="w6-sig"><span class="w6-chip" style="--c:{c}">{_e(_nice(lab))}</span><p>{_e(tip)}</p></div>'


def _act(kind: str, sym: str) -> str:
    """The click target for the app's tap bridge: 'wl:sel:TCS' selects a stock, 'wl:rm:TCS' removes it."""
    return f"{WL_PREFIX}{kind}:{sym}" if _clicks_ok() else sym


def _clicks_ok() -> bool:
    """True when auth.py (v-wl) hands watchlist clicks to this page. Older auth.py: rows open the stock instead."""
    try:
        import auth
        return getattr(auth, "WL_PREFIX", None) == WL_PREFIX and getattr(auth, "_bridge_component", None) is not None
    except Exception:
        return False


WL_PREFIX = "wl:"


# ═════════════════════════ views ═════════════════════════
VIEWS6 = ["Today", "Returns", "Technicals", "Heatmap"]


def _cols6(view):
    """(header, tooltip, cell(r, ctx) -> html, extra class)."""
    stock = ("Stock", "", lambda r, x: _stock_cell(r, x), "lft")
    price = ("Price", "Last traded price (₹)", lambda r, x: (f'<span class="w6-px">{fmt_price(r.get("last"))}'
                                                              f'{"<span class=lv title=Live></span>" if r.get("live") else ""}</span>'
                                                              if r["has_data"] else '<span class="na">no data</span>'), "")
    if view == "Today":
        return [stock, price,
                ("Today", "Change from yesterday's close", lambda r, x: (_chg_pill(r.get("pct")) + (f'<span class="w6-sub">{_signed(r.get("chg"))}</span>' if r.get("chg") is not None else ""))
                 if r["has_data"] else '<span class="na">—</span>', ""),
                ("Day's range", "Today's low and high; the dot is where the price is now", lambda r, x: _range_cell(r.get("low"), r.get("high"), r.get("last")) if r["has_data"] else '<span class="na">—</span>', ""),
                ("Chart today", "Today in 5-minute steps; the dashed line is yesterday's close", lambda r, x: _spark_intraday(x["intra"].get(r["symbol"]), r.get("prev")), "ctr"),
                ("What's happening", "News, results, filings, your alerts and price signals. Click the stock to read them", lambda r, x: _chips(r["events"], 2), "lft")]
    if view == "Returns":
        return [stock, price,
                ("1 week", "", lambda r, x: _retc(r.get("r1w")), ""), ("1 month", "", lambda r, x: _retc(r.get("r1m")), ""),
                ("3 months", "", lambda r, x: _retc(r.get("r3m")), ""), ("This year", "Since 31 December", lambda r, x: _retc(r.get("ytd")), ""),
                ("1 year", "", lambda r, x: _retc(r.get("r1y")), ""),
                ("Last 3 months", "Daily closes", lambda r, x: _spark_daily(r.get("spark"), 96, 26), "ctr")]
    return [stock, price,
            ("Trend", "Price against its 20-day, 50-day and 200-day averages", lambda r, x: _word(trend_word(r)), ""),
            ("RSI (14 days)", "Momentum from 0 to 100: above 70 is stretched, below 30 is washed out", lambda r, x: _word(rsi_word(r.get("rsi"))), ""),
            ("From 52W high", "How far the price is below its highest price of the last year", lambda r, x: _retc(r.get("off_hi52")), ""),
            ("Volume", "Today's volume against its 20-day average" , lambda r, x: (f'<span class="w6-ret" style="color:{YELLOW if r["vol_x"] >= 2 else GREEN if r["vol_x"] >= 1.2 else TX2}">{r["vol_x"]:.1f}×</span>'
                                                                                 if r.get("vol_x") else '<span class="na">—</span>'), ""),
            ("Delivery", "Share of traded volume taken for delivery (NSE, latest day)", lambda r, x: (f'{x["deliv"][r["symbol"]]["pct"]:.0f}%' if x["deliv"].get(r["symbol"]) else '<span class="na">—</span>'), "")]


def _stock_cell(r, ctx):
    nm = (ctx["names"].get(r["symbol"]) or ("", ""))[0] or r.get("name") or ("no price data" if not r["has_data"] else "")
    note = (ctx["meta"].get(r["symbol"]) or {}).get("note")
    return (f'<div class="w6-sym"><b>{_e(r["symbol"])}{"<em title=" + chr(34) + _e(note) + chr(34) + ">✎</em>" if note else ""}</b>'
            f'<span>{_e(nm)}</span></div>')


def _x(r, can_edit) -> str:
    if not can_edit or not _clicks_ok():
        return ""
    return f'<span class="w6-x" data-ak-sym="{_e(_act("rm", r["symbol"]))}" title="Remove {_e(r["symbol"])} from this list">✕</span>'


def render_list6(rows, view, ctx, group=False, selected=None, can_edit=False) -> str:
    cols = _cols6(view)
    vcls = {"Today": "v-today", "Returns": "v-ret", "Technicals": "v-tech"}[view]
    head = "".join(f'<div class="h{i} {c}" title="{_e(tip)}">{_e(h)}</div>' for i, (h, tip, _, c) in enumerate(cols))
    body, last_grp = [], None
    for r in rows:
        if group and r.get("group") != last_grp:
            last_grp = r.get("group")
            members = [x for x in rows if x.get("group") == last_grp and x.get("pct") is not None]
            avg = sum(x["pct"] for x in members) / len(members) if members else None
            body.append(f'<div class="w6-grp">{_e(last_grp)}<span>{len([x for x in rows if x.get("group") == last_grp])} stocks'
                        + (f' · average <b class="{_cls(avg)}">{avg:+.2f}%</b>' if avg is not None else "") + "</span></div>")
        cells = "".join(f'<div class="h{i} {c}">{fn(r, ctx)}</div>' for i, (_, _, fn, c) in enumerate(cols))
        body.append(f'<div class="w6-row {vcls}{" sel" if r["symbol"] == selected else ""}" data-ak-sym="{_e(_act("sel", r["symbol"]))}"'
                    f' title="Click for details and news">{cells}{_x(r, can_edit)}</div>')
    return f'<div class="w6"><div class="w6-list"><div class="w6-hd {vcls}">{head}</div>{"".join(body)}</div></div>'


def render_cards6(rows, view, ctx, group=False, selected=None, can_edit=False) -> list:
    """Phone: one card per stock. Returned as a list of (symbol, html) so the page can put the details under a card."""
    out, last_grp = [], None
    for r in rows:
        pre = ""
        if group and r.get("group") != last_grp:
            last_grp = r.get("group")
            pre = f'<div class="w6-mg">{_e(last_grp)}</div>'
        if r["has_data"]:
            right = f'<div class="r"><span class="w6-px">{fmt_price(r.get("last"))}</span><br>{_chg_pill(r.get("pct"))}</div>'
        else:
            right = '<div class="r"><span class="na">no data</span></div>'
        if view == "Returns":
            sub = " · ".join(f"{lab} {_plain_pct(r.get(k))}" for lab, k in (("1W", "r1w"), ("1M", "r1m"), ("1Y", "r1y")))
            extra = f'<span class="w6-sub">{_e(sub)}</span>'
        elif view == "Technicals":
            tw, rw = trend_word(r), rsi_word(r.get("rsi"))
            extra = '<div class="w6-chips" style="margin-top:8px">' + (_word(tw) if tw else "") + (_word((f"RSI {rw[0]}", rw[1])) if rw else "") + "</div>"
        else:
            extra = _chips(r["events"], 3) if r["events"] else ""
        out.append((r["symbol"], pre + f'<div class="w6-card{" sel" if r["symbol"] == selected else ""}" data-ak-sym="{_e(_act("sel", r["symbol"]))}">'
                                       f'<div class="t">{_stock_cell(r, ctx)}{right}</div>{extra}{_x(r, can_edit)}</div>'))
    return out


def render_heat6(rows, field, group=False, selected=None) -> str:
    def tile(r):
        v = r.get(field)
        if v is None:
            bg, txt = "#161618", "no data"
        else:
            scale = {"pct": 4, "r1w": 8, "r1m": 15, "r3m": 25, "ytd": 40, "r1y": 60}.get(field, 10)
            a = min(0.85, 0.16 + abs(v) / scale * 0.7)
            bg = f"rgba({'34,160,70' if v >= 0 else '215,50,45'},{a:.3f})"
            txt = f"{v:+.2f}%"
        return (f'<div class="w6-tile{" sel" if r["symbol"] == selected else ""}" style="background:{bg}" data-ak-sym="{_e(_act("sel", r["symbol"]))}">'
                f'<b>{_e(r["symbol"])}</b><div class="p">{fmt_price(r.get("last"))}</div><div class="c">{txt}</div></div>')
    srt = sorted(rows, key=lambda r: r.get(field) if r.get(field) is not None else -1e9, reverse=True)
    if not group:
        return '<div class="w6"><div class="w6-heat">' + "".join(tile(r) for r in srt) + "</div></div>"
    out = []
    for g in dict.fromkeys(r.get("group") for r in sorted(rows, key=lambda r: r.get("group") or "")):
        members = [r for r in srt if r.get("group") == g]
        out.append(f'<div class="w6-gh">{_e(g)} · {len(members)}</div><div class="w6-heat">' + "".join(tile(r) for r in members) + "</div>")
    return '<div class="w6">' + "".join(out) + "</div>"


def render_summary6(rows, nifty_row) -> str:
    ok = [r for r in rows if r["has_data"] and r.get("pct") is not None]
    adv = sum(1 for r in ok if r["pct"] > 0)
    dec = sum(1 for r in ok if r["pct"] < 0)
    flat = len(ok) - adv - dec
    tot = max(1, len(ok))
    avg = sum(r["pct"] for r in ok) / len(ok) if ok else None
    npct = nifty_row.get("pct") if nifty_row and nifty_row.get("has_data") else None
    movers = sorted(ok, key=lambda r: r["pct"])
    best, worst = (movers[-1], movers[0]) if movers else (None, None)
    news = sum(1 for r in rows if r.get("stories"))
    alerts = sum(1 for r in rows if r.get("alert_hit"))
    pct = lambda v: f'<span class="{_cls(v)}">{v:+.2f}%</span>' if v is not None else '<span class="na">—</span>'
    bb = (f'<div class="w6-bb"><i style="width:{adv / tot * 100:.1f}%;background:{GREEN}"></i><i style="width:{flat / tot * 100:.1f}%;background:#3A3A40"></i>'
          f'<i style="width:{dec / tot * 100:.1f}%;background:{RED}"></i></div>')
    cells = [
        f'<div><div class="l">Today</div><div class="v"><span class="up">{adv} up</span> <span class="na">·</span> <span class="dn">{dec} down</span></div>{bb}</div>',
        f'<div><div class="l">Your list on average</div><div class="v">{pct(avg)}</div><div class="s">NIFTY 50 {pct(npct) if npct is not None else "—"}</div></div>',
        (f'<div><div class="l">Best · worst</div><div class="v"><span class="up">{_e(best["symbol"])}</span> <span class="na">·</span> '
         f'<span class="dn">{_e(worst["symbol"])}</span></div><div class="s">{pct(best["pct"])} · {pct(worst["pct"])}</div></div>') if best else
        '<div><div class="l">Best · worst</div><div class="v na">—</div></div>',
        f'<div><div class="l">Needs a look</div><div class="v">{news} in the news</div><div class="s">{alerts} price alert{"s" if alerts != 1 else ""} hit</div></div>',
    ]
    return '<div class="w6"><div class="w6-sum">' + "".join(cells) + "</div></div>"


# ═════════════════════════ details panel ═════════════════════════
def _band(lo, hi, v, label) -> str:
    if lo is None or hi is None or v is None or hi <= lo:
        return ""
    p = max(0, min(100, (v - lo) / (hi - lo) * 100))
    return (f'<div><div class="w6-rgl"><span>{label}</span></div><div class="w6-band"><i style="left:{p:.1f}%"></i></div>'
            f'<div class="w6-bl"><span>Low {fmt_price(lo)}</span><span>High {fmt_price(hi)}</span></div></div>')


def panel_head_html(r, names, meta) -> str:
    nm, isin = names.get(r["symbol"], ("", ""))
    nm = nm or r.get("name") or ""
    if r.get("has_data") and r.get("pct") is not None:
        when = "Live" if r.get("live") else (f'Close of {r["date"]:%d %b}' if r.get("date") is not None else "")
        q = (f'<div class="q"><b>₹{fmt_price(r.get("last"))}</b><span class="{_cls(r["pct"])}">{_signed(r.get("chg"))} ({r["pct"]:+.2f}%)</span>'
             f'<small>{_e(when)}</small></div>')
    else:
        q = '<div class="q"><small>No price data for this symbol</small></div>'
    note = (meta or {}).get("note")
    return (f'<div class="w6"><div class="w6-p-hd"><div><b>{_e(r["symbol"])}</b><small>{_e(nm)}</small></div>{q}</div>'
            + (f'<div class="w6-note">✎ {_e(note)}</div>' if note else "") + "</div>")


def panel_body_html(r, meta, deliv) -> str:
    if not r.get("has_data"):
        return ('<div class="w6"><div class="w6-none" style="margin-top:12px">Yahoo Finance returns no prices for this symbol. It may be misspelt, '
                'renamed or delisted: you can remove it from the list.</div></div>')
    d = deliv.get(r["symbol"])
    tw, rw = trend_word(r), rsi_word(r.get("rsi"))
    stat = lambda lab, val: f"<div><span>{lab}</span><b>{val}</b></div>"
    vx = r.get("vol_x")
    alerts = " · ".join(x for x in (f'above ₹{fmt_price(meta.get("alert_above"))}' if _num((meta or {}).get("alert_above")) else "",
                                     f'below ₹{fmt_price(meta.get("alert_below"))}' if _num((meta or {}).get("alert_below")) else "") if x)
    stats = "".join([
        stat("Volume today", f'{fmt_qty(r.get("volume"))}<small>{f"{vx:.1f}× usual" if vx else ""}</small>'),
        stat("Delivery", f'{d["pct"]:.0f}%<small>{"usual " + format(d["avg"], ".0f") + "%" if d.get("avg") else ""}</small>' if d else "—"),
        stat("Trend", f'<span style="color:{tw[1]}">{_e(tw[0])}</span>' if tw else "—"),
        stat("RSI (14 days)", f'<span style="color:{rw[1]}">{_e(rw[0])}</span>' if rw else "—"),
        stat("1 month", _retc(r.get("r1m"))), stat("1 year", _retc(r.get("r1y"))),
        stat("Open · prev. close", f'{fmt_price(r.get("open"))} · {fmt_price(r.get("prev"))}'),
        stat("Your alerts", _e(alerts) if alerts else '<span class="na">none set</span>'),
    ])
    sig = [e for e in r["events"] if not _is_news_event(e[0])]
    stories = r.get("stories") or []
    news = "".join(_story_html(a) for a in stories[:8])
    more = f'<div class="w6-none">+ {len(stories) - 8} older stories on the Research page.</div>' if len(stories) > 8 else ""
    return ('<div class="w6">'
            f'<div class="w6-sec">Price ranges</div><div class="w6-rows2">{_band(r.get("low"), r.get("high"), r.get("last"), "Today")}'
            f'{_band(r.get("lo52"), r.get("hi52"), r.get("last"), "Last 52 weeks")}</div>'
            f'<div class="w6-sec">Key numbers</div><div class="w6-stats">{stats}</div>'
            f'<div class="w6-sec">What\'s happening today<em>{len(sig) or "nothing unusual"}</em></div>'
            + ("".join(_signal_html(e) for e in sig) if sig else '<div class="w6-none">No unusual price moves today.</div>')
            + f'<div class="w6-sec">News and filings<em>last 24 hours</em></div>'
            + (news + more if stories else '<div class="w6-none">No news or filings about this stock in the last 24 hours.</div>')
            + "</div>")


def _panel(sb, r, names, meta, deliv, key, list_name, lists, can_edit, supports, on_open, pick_from=None):
    """The details panel for one stock: inline, so it can never get stuck over the page."""
    with st.container(key=f"w6_panel_{key}"):
        if pick_from is not None:                           # old auth.py: rows cannot select, so offer a picker
            cur = st.session_state.get(f"w6_sel_{key}")
            idx = pick_from.index(cur) if cur in pick_from else 0
            st.selectbox("Stock", pick_from, index=idx, key=f"w6_pickbox_{key}", label_visibility="collapsed",
                         on_change=lambda: st.session_state.update({f"w6_sel_{key}": st.session_state.get(f"w6_pickbox_{key}")}))
        st.markdown(panel_head_html(r, names, meta.get(r["symbol"])), unsafe_allow_html=True)
        sym = r["symbol"]
        b1, b2, b3 = st.columns([1.25, 1, 1], gap="small")
        if b1.button("Open chart", key=f"w6_open_{key}", type="primary", use_container_width=True, disabled=on_open is None):
            on_open(sym)
        if b2.button("Research", key=f"w6_res_{key}", use_container_width=True):
            ud.go_research(sym)
        if b3.button("Arka AI", key=f"w6_ai_{key}", use_container_width=True):
            ud.go_analysis(sym)
        st.markdown(panel_body_html(r, meta.get(sym) or {}, deliv), unsafe_allow_html=True)
        if can_edit and key != "desk" and supports:
            m = meta.get(sym) or {}
            with st.expander("Note and price alerts"):
                note = st.text_input("Note", value=m.get("note") or "", key=f"w6_note_{key}_{sym}", placeholder="Why is this stock on your list?")
                a1, a2 = st.columns(2)
                above = a1.number_input("Tell me when price goes above ₹", min_value=0.0, value=float(_num(m.get("alert_above")) or 0.0), step=1.0, key=f"w6_ab_{key}_{sym}")
                below = a2.number_input("Tell me when price goes below ₹", min_value=0.0, value=float(_num(m.get("alert_below")) or 0.0), step=1.0, key=f"w6_bl_{key}_{sym}")
                st.caption("Leave 0 for no alert. When the price crosses it, the stock shows an amber alert chip and appears under the “Alert hit” filter.")
                others = [x for x in lists if x != list_name]
                dst = st.selectbox("Move to another list", ["Keep it here"] + others, key=f"w6_mv_{key}_{sym}") if others else "Keep it here"
                if st.button("Save", key=f"w6_meta_go_{key}_{sym}", type="primary"):
                    err = update_meta(sb, sym, list_name, {"note": note.strip() or None, "alert_above": above or None, "alert_below": below or None})
                    if not err and dst != "Keep it here":
                        err = move_to_list(sb, sym, list_name, dst)
                    if err:
                        st.error(err)
                    else:
                        st.toast(f"{sym} saved")
                        st.rerun()
        if can_edit:
            if st.button(f"Remove {sym} from {list_name}", key=f"w6_rm_{key}", use_container_width=True):
                _remove(sb, sym, key, list_name, meta)
                st.rerun()


# ═════════════════════════ remove and undo ═════════════════════════
def _remove(sb, sym, key, list_name, meta) -> bool:
    m = dict(meta.get(sym) or {})
    err = ud.remove_symbol(sb, sym, ud.A_TABLE) if key == "desk" else remove_from_list(sb, [sym], list_name)
    if err:
        st.error(err)
        return False
    st.session_state["w6_undo"] = {"sym": sym, "list": list_name, "key": key, "t": time.time(),
                                   "meta": {k: m.get(k) for k in ("note", "alert_above", "alert_below") if m.get(k) not in (None, "")}}
    if st.session_state.get(f"w6_sel_{key}") == sym:
        st.session_state.pop(f"w6_sel_{key}", None)
    return True


def _undo(sb):
    u = st.session_state.pop("w6_undo", None)
    if not u:
        return
    if u["key"] == "desk":
        _, err = ud.add_symbols(sb, [u["sym"]], ud.A_TABLE)
    else:
        _, err = add_to_list(sb, [u["sym"]], u["list"])
        if not err and u.get("meta"):
            err = update_meta(sb, u["sym"], u["list"], u["meta"])
    if err:
        st.error(err)
    else:
        st.session_state[f"w6_sel_{u['key']}"] = u["sym"]
        st.toast(f"{u['sym']} is back in {u['list']}")


def _undo_bar(sb, key):
    u = st.session_state.get("w6_undo")
    if not u or u.get("key") != key:
        return
    if time.time() - u.get("t", 0) > 90:
        st.session_state.pop("w6_undo", None)
        return
    with st.container(key=f"w6_undo_{key}"):
        a, b, c = st.columns([6, 1.1, 0.5], gap="small")
        a.markdown(f'<div class="w6-undo">Removed <b>{_e(u["sym"])}</b> from {_e(u["list"])}.</div>', unsafe_allow_html=True)
        if b.button("Undo", key=f"w6_undo_go_{key}", use_container_width=True, type="primary"):
            _undo(sb)
            st.rerun()
        if c.button("✕", key=f"w6_undo_x_{key}", use_container_width=True, help="Dismiss"):
            st.session_state.pop("w6_undo", None)
            st.rerun()


def _take_click(sb, key, list_name, can_edit, meta):
    """A click on a row or a ✕ arrives from the tap bridge (auth.py) as 'wl:sel:SYM' / 'wl:rm:SYM'."""
    cmd = st.session_state.pop("_wl_cmd_in", None)
    if not isinstance(cmd, str) or not cmd.startswith(WL_PREFIX):
        return
    op, _, sym = cmd[len(WL_PREFIX):].partition(":")
    sym = ud.clean_symbol(sym)
    if not sym:
        return
    if op == "sel":
        k = f"w6_sel_{key}"
        if _phone() and st.session_state.get(k) == sym:     # phone: tapping the open card again closes it
            st.session_state[k] = None
        else:
            st.session_state[k] = sym
    elif op == "rm" and can_edit:
        _remove(sb, sym, key, list_name, meta)


# ═════════════════════════ filter and sort, in plain words ═════════════════════════
FILTERS6 = [("All", "All"), ("Up today", "Gainers"), ("Down today", "Losers"), ("In the news", "News today"), ("Alert hit", "Alerts hit"),
            ("Volume spike", "Volume spike"), ("52-week high", "52W high"), ("Opened at high", "Open = high"), ("Opened at low", "Open = low"),
            ("Above yesterday's high", "Above PDH"), ("Below yesterday's low", "Below PDL"), ("No price data", "No data")]
SORTS6 = {"Your order": "List order", "Biggest gainers": "% Chg ↓", "Biggest losers": "% Chg ↑", "Busiest (volume)": "Volume × ↓",
          "Most events": "Events first", "Best 1 month": "1M ↓", "Best this year": "YTD ↓", "Best 1 year": "1Y ↓",
          "Nearest 52-week high": "Near 52W high", "Highest RSI": "RSI ↓", "Lowest RSI": "RSI ↑", "Name A–Z": "Symbol A–Z"}


# ═════════════════════════ entry point ═════════════════════════
def render_watchlist(supabase, on_open=None, is_admin=False):
    """Main Watchlist module. Signature preserved for app.py."""
    _css()
    sb = supabase
    ss = st.session_state
    if sb is None:
        st.info("Cloud storage is not connected, so changes last only for this session. Add SUPABASE_URL and SUPABASE_KEY to Streamlit secrets.")
    supports = lists_supported(sb)
    current = ss.get("w3_list", DEFAULT_LIST)
    if current in ("ARKA DESK",):
        current = DESK
    key = "desk" if current == DESK else "l_" + re.sub(r"\W", "_", current)
    can_edit = bool(is_admin) if current == DESK else True

    all_rows = load_rows(sb)
    if ss.get("_wl_cmd_in"):                                   # a click on a row or on ✕
        meta0 = {} if current == DESK else {r["symbol"]: r for r in all_rows if r["list_name"] == current}
        _take_click(sb, key, current, can_edit, meta0)
        all_rows = load_rows(sb)

    lists = list(dict.fromkeys([r["list_name"] for r in all_rows] + [DEFAULT_LIST] + ss.get("w3_empty_lists", [])))
    if not supports:
        lists = [DEFAULT_LIST]
    if current not in lists and current != DESK:
        current, key, can_edit = DEFAULT_LIST, "l_" + DEFAULT_LIST, True
    _list_bar(lists, current, supports, is_admin)

    if current == DESK:
        desk = ud.load_symbols(sb, ud.A_TABLE) if sb is not None else list(ss.get("admin_watchlist", []))
        symbols, meta = desk, {}
        sub = "Picked by the Arka desk" + (" · you can edit" if is_admin else " · read-only")
    else:
        mine = [r for r in all_rows if r["list_name"] == current]
        symbols, meta = [r["symbol"] for r in mine], {r["symbol"]: r for r in mine}
        sub = f"{len(symbols)} stock{'s' if len(symbols) != 1 else ''} · " + ("saved to your account" if sb is not None else "this session only")
    st.markdown(f'<div class="w6"><div class="w6-head"><h2>{_e(current)}</h2><div class="sub">{_e(sub)}</div></div></div>', unsafe_allow_html=True)
    if can_edit:
        _add_box(sb, current, key, desk=current == DESK)
    _undo_bar(sb, key)

    if ss.get("w6_view") not in VIEWS6:
        ss["w6_view"] = VIEWS6[0]
    for _k, _v in (("w3_group", False), ("w3_rich", True), ("w3_auto", True)):
        ss.setdefault(_k, _v)
    group, rich, auto = bool(ss.get("w3_group")), bool(ss.get("w3_rich")), bool(ss.get("w3_auto"))
    run_every = 60 if (auto and market_open()) else None

    @st.fragment(run_every=run_every)
    def live_part():
        _body(sb, symbols, meta, key, on_open, can_edit, group, rich, current, lists, supports)
    live_part()


def _body(sb, symbols, meta, key, on_open, can_edit, group, rich, list_name, lists, supports):
    ss = st.session_state
    symbols = list(dict.fromkeys(s for s in symbols if s))
    if not symbols:
        msg = ("<b>This list is empty</b>Type a stock in the box above and press Add, for example <code>TCS</code> or <code>Reliance</code>.<br>"
               "You can add several at once, separated by commas, or import a TradingView / CSV file."
               if can_edit else "<b>The Arka desk list is empty</b>")
        st.markdown(f'<div class="w6"><div class="w6-empty">{msg}</div></div>', unsafe_allow_html=True)
        _manage(sb, [], list_name, key, can_edit, supports) if can_edit else None
        return
    phone = _phone()
    view = ss.get("w6_view", VIEWS6[0])
    with st.spinner(f"Loading {len(symbols)} stocks…"):
        frames, intra = load_frames(tuple(symbols) + ("^NSEI",), view == "Today" and not phone)
        names = equity_names()
        deliv = delivery() if view in ("Today", "Technicals") or not phone else {}
        cals = calendars(symbols) if rich else {}
        news = news_by_symbol(symbols, names) if rich else {}
    nifty = frames.get("^NSEI")
    rows = build_rows(symbols, frames, names, deliv, cals, news, meta, nifty)
    if group:
        for r in rows:
            r["group"] = _group_of(r["symbol"], names)
    st.markdown(render_summary6(rows, compute("^NSEI", nifty)), unsafe_allow_html=True)

    # controls: search · view · sort · options
    if phone:
        view = _seg(st, "View", VIEWS6, "w6_view")
        c_q, c_s, c_o = st.columns([1.35, 1.1, 0.85], gap="small")
    else:
        c_q, c_v, c_s, c_o = st.columns([2.1, 3.1, 1.6, 0.9], gap="small")
        view = _seg(c_v, "View", VIEWS6, "w6_view")
    query = c_q.text_input("Search", placeholder="Find a stock in this list", key=f"w6_q_{key}", label_visibility="collapsed")
    if view == "Heatmap":
        hf = c_s.selectbox("Colour by", ["1D", "1W", "1M", "3M", "YTD", "1Y"], key="w6_heat", label_visibility="collapsed",
                           format_func=lambda x: {"1D": "Colour: today", "1W": "Colour: 1 week", "1M": "Colour: 1 month", "3M": "Colour: 3 months",
                                                  "YTD": "Colour: this year", "1Y": "Colour: 1 year"}[x])
        how = "Your order"
    else:
        how = c_s.selectbox("Sort", list(SORTS6), key=f"w6_s_{key}", label_visibility="collapsed", format_func=lambda x: "Sort: " + x)
    with c_o:
        with _popover("Options"):
            st.toggle("Group by sector", key="w3_group")
            st.toggle("News, filings and results", key="w3_rich", help="Looks up today's news, filings and results dates (a few seconds the first time)")
            st.toggle("Live refresh every 60 seconds", key="w3_auto", disabled=not market_open(), help="Only while NSE is open")
    counts = {lab: len(filter_rows(rows, raw)) for lab, raw in FILTERS6}
    opts = [lab for lab, _ in FILTERS6 if lab == "All" or counts[lab]]
    if ss.get(f"w6_f_{key}") not in opts:
        ss[f"w6_f_{key}"] = "All"
    fmt = lambda f: f if f == "All" else f"{f} · {counts[f]}"
    with st.container(key=f"w6_flt_{key}"):
        flt = (st.pills("Show", opts, key=f"w6_f_{key}", format_func=fmt, label_visibility="collapsed") if hasattr(st, "pills")
               else st.radio("Show", opts, key=f"w6_f_{key}", format_func=fmt, horizontal=True, label_visibility="collapsed")) or "All"
    shown = filter_rows(rows, dict(FILTERS6).get(flt, "All"), query)
    if view != "Heatmap":
        shown = sort_rows(shown, SORTS6.get(how, "List order"), group)

    # which stock the panel shows
    sel_key = f"w6_sel_{key}"
    syms_shown = [r["symbol"] for r in shown]
    sel = ss.get(sel_key)
    if sel not in syms_shown:
        sel = None if phone else (syms_shown[0] if syms_shown else None)
        if not phone:
            ss[sel_key] = sel
    row = next((r for r in rows if r["symbol"] == sel), None)
    clicks = _clicks_ok()
    hint = ("Tap a stock to see its details and news. Tap ✕ to remove it." if phone else
            "Click a stock to see its details and news on the right. To remove one, point at its row and click ✕.") if clicks else \
           "Pick a stock in the panel to see its details and news. Clicking a row opens the stock."
    st.markdown(f'<div class="w6"><div class="w6-hint"><i>i</i>{_e(hint)}</div></div>', unsafe_allow_html=True)
    ctx = {"names": names, "meta": meta, "deliv": deliv, "intra": intra}
    pick_from = None if clicks else syms_shown

    if not shown:
        st.markdown('<div class="w6"><div class="w6-empty"><b>Nothing to show</b>No stock in this list matches the search or filter.</div></div>',
                    unsafe_allow_html=True)
    elif phone:
        if view == "Heatmap":
            field = {"1D": "pct", "1W": "r1w", "1M": "r1m", "3M": "r3m", "YTD": "ytd", "1Y": "r1y"}[hf]
            st.markdown(render_heat6(shown, field, group, sel), unsafe_allow_html=True)
            if row is not None:
                _panel(sb, row, names, meta, deliv, key, list_name, lists, can_edit, supports, on_open, pick_from)
        else:
            cards = render_cards6(shown, view, ctx, group, sel, can_edit)
            cut = next((i + 1 for i, (s, _) in enumerate(cards) if s == sel), len(cards))
            st.markdown('<div class="w6"><div class="w6-cards">' + "".join(h for _, h in cards[:cut]) + "</div></div>", unsafe_allow_html=True)
            if row is not None:
                _panel(sb, row, names, meta, deliv, key, list_name, lists, can_edit, supports, on_open, pick_from)
            if cards[cut:]:
                st.markdown('<div class="w6"><div class="w6-cards">' + "".join(h for _, h in cards[cut:]) + "</div></div>", unsafe_allow_html=True)
    else:
        left, right = st.columns([2.05, 1], gap="medium")
        with left:
            if view == "Heatmap":
                field = {"1D": "pct", "1W": "r1w", "1M": "r1m", "3M": "r3m", "YTD": "ytd", "1Y": "r1y"}[hf]
                st.markdown(render_heat6(shown, field, group, sel), unsafe_allow_html=True)
            else:
                st.markdown(render_list6(shown, view, ctx, group, sel, can_edit), unsafe_allow_html=True)
        with right:
            if row is not None:
                _panel(sb, row, names, meta, deliv, key, list_name, lists, can_edit, supports, on_open, pick_from)

    asof = max((r["date"] for r in rows if r["has_data"]), default=None)
    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([4, 0.9, 0.9, 1.2], gap="small")
    c1.markdown(f'<div class="w6"><div class="w6-foot">Showing {len(shown)} of {len(rows)} stocks'
                + (f" · prices as of {asof:%d %b %Y}" if asof is not None else "")
                + " · prices from Yahoo Finance, delivery from NSE · an amber dot means the day is still trading.</div></div>", unsafe_allow_html=True)
    if c2.button("Refresh", key=f"w6_ref_{key}", use_container_width=True):
        for fn in (_live, _intraday):
            fn.clear()
        st.rerun(scope="fragment")
    out = pd.DataFrame([{"symbol": r["symbol"], "name": r.get("name"), "ltp": r.get("last"), "chg": r.get("chg"), "pct_chg": r.get("pct"),
                         "open": r.get("open"), "high": r.get("high"), "low": r.get("low"), "prev_close": r.get("prev"), "volume": r.get("volume"),
                         "vol_x_20d": r.get("vol_x"), "deliv_pct": r.get("deliv_pct"), "r1w": r.get("r1w"), "r1m": r.get("r1m"), "r3m": r.get("r3m"),
                         "ytd": r.get("ytd"), "r1y": r.get("r1y"), "rsi": r.get("rsi"), "events": " | ".join(_nice(e[0]) for e in r["events"])} for r in shown])
    c3.download_button("CSV", out.to_csv(index=False).encode(), f"arka_{key}_{_now():%Y%m%d}.csv", "text/csv", key=f"w6_csv_{key}", use_container_width=True)
    c4.download_button("TradingView", "\n".join(f"NSE:{r['symbol']}" for r in shown), f"arka_{key}_tv.txt", "text/plain", key=f"w6_tv_{key}",
                       use_container_width=True, help="A file you can import into a TradingView watchlist")
    if can_edit:
        _manage(sb, rows, list_name, key, can_edit, supports)
