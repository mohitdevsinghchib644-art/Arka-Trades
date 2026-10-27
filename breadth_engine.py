"""
breadth_engine.py — Arka Market Breadth engine v2

WHAT IT DOES
------------
* Computes market breadth from end-of-day prices of the NSE equity universe:
    Above/Below 20 / 50 / 200 DMA counts, advances / declines, new 52-week highs / lows.
  The first run backfills ~2 years of daily history in ONE download, so the table and
  chart are complete immediately (no waiting for history to accumulate).
* REFRESH POLICY: exactly once per trading day at 16:15 IST, Monday-Friday.
  - A background thread checks every minute and starts the daily refresh when it is due.
  - Opening the page or changing the lookback NEVER hits the network.
  - A failed run is retried after 30 minutes (not every rerun).
* The refresh runs in a background thread, so the Streamlit page never blocks.
* Persistence: history + meta are saved to local .cache AND (if configured) Supabase, so a
  Streamlit Cloud restart does not lose them:

      create table if not exists breadth_store (
        key text primary key, value jsonb not null, updated_at timestamptz default now());
      alter table breadth_store disable row level security;

* Chartink (dashboard 86550) is now an OPTIONAL cross-check: after each refresh Arka tries to read
  Chartink's current counts with a tolerant parser. If it works you see both numbers; if it fails
  the page simply omits the strip (reason shown under Diagnostics). It can never break the page.

UNIVERSE
--------
1) nse_universe.csv in the repo root (column SYMBOL)  ← recommended, most reliable
2) NSE archive EQUITY_L.csv (live)
3) built-in list of ~100 liquid large caps (clearly labelled as NOT full-market breadth)
"""
from __future__ import annotations

import io
import json
import math
import re
import threading
import time as _time
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
import requests

try:                                    # streamlit is only needed for secrets; engine stays importable without it
    import streamlit as st
except Exception:                       # pragma: no cover
    st = None

# ───────────────────────── configuration ─────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
SYNC_HOUR, SYNC_MINUTE = 16, 15         # 4:15 PM IST
RETRY_AFTER_FAIL_MIN = 30
MAX_ROWS = 600
BATCH = 100

CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = CACHE_DIR / "arka_breadth_history.json"      # kept name-compatible with the old engine's HISTORY_FILE import
META_FILE = CACHE_DIR / "arka_breadth_meta.json"

CORE = ["above_20dma", "below_20dma", "above_50dma", "below_50dma", "above_200dma", "below_200dma"]
EXTRA = ["advances", "declines", "unchanged", "new_52w_high", "new_52w_low", "universe"]
COLS = CORE + EXTRA

NSE_ARCHIVE_URLS = [
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
]
LIQUID_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT",
    "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND",
    "ADANIENT", "ADANIPORTS", "POWERGRID", "NTPC", "M&M", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "BAJAJFINSV", "ONGC",
    "COALINDIA", "INDUSINDBK", "GRASIM", "HDFCLIFE", "SBILIFE", "DRREDDY", "CIPLA", "EICHERMOT", "BRITANNIA", "DIVISLAB",
    "APOLLOHOSP", "HEROMOTOCO", "BPCL", "TECHM", "UPL", "HINDALCO", "TATACONSUM", "BAJAJ-AUTO", "SHREECEM", "VEDL",
    "GODREJCP", "DABUR", "PIDILITIND", "SIEMENS", "AMBUJACEM", "BANKBARODA", "CANBK", "PNB", "IDFCFIRSTB", "FEDERALBNK",
    "AUBANK", "BANDHANBNK", "CHOLAFIN", "LICHSGFIN", "MUTHOOTFIN", "PFC", "RECLTD", "IRFC", "IEX", "ZOMATO", "NYKAA",
    "PAYTM", "POLICYBZR", "DMART", "TRENT", "JUBLFOOD", "PERSISTENT", "COFORGE", "LTIM", "MPHASIS", "OFSS", "LTTS", "DLF",
    "GODREJPROP", "OBEROIRLTY", "PHOENIXLTD", "PGHH", "COLPAL", "MARICO", "EMAMILTD", "VBL", "TVSMOTOR", "ASHOKLEY",
    "BALKRISIND", "MOTHERSON", "BOSCHLTD", "BEL", "HAL", "RVNL", "TATAPOWER", "NHPC", "SAIL", "GAIL", "IOC",
]

_LOCK = threading.RLock()
STATE: dict[str, Any] = {"running": False, "progress": "", "started": None, "finished": 0.0, "error": None, "mode": None}
_MEM: dict[str, Any] = {"history": None, "history_ts": 0.0, "meta": None, "meta_ts": 0.0}
_SB = None
_SB_TRIED = False
_SCHED: Optional[threading.Thread] = None


# ───────────────────────── schedule logic (pure) ─────────────────────────
def now_ist() -> datetime:
    return datetime.now(IST)


def _sched_at(day) -> datetime:
    return datetime.combine(day, dtime(SYNC_HOUR, SYNC_MINUTE), tzinfo=IST)


def last_due_time(now: Optional[datetime] = None) -> datetime:
    """Most recent scheduled refresh moment (a weekday 16:15 IST) at or before `now`."""
    now = now or now_ist()
    for k in range(0, 8):
        day = (now - timedelta(days=k)).date()
        if day.weekday() >= 5:
            continue
        s = _sched_at(day)
        if s <= now:
            return s
    return _sched_at(now.date())


def next_run_time(now: Optional[datetime] = None) -> datetime:
    """Next scheduled refresh moment strictly after `now`."""
    now = now or now_ist()
    for k in range(0, 8):
        day = (now + timedelta(days=k)).date()
        if day.weekday() >= 5:
            continue
        s = _sched_at(day)
        if s > now:
            return s
    return _sched_at(now.date() + timedelta(days=1))


def _parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except Exception:
        return None


def is_due(meta: dict, history_empty: bool, now: Optional[datetime] = None) -> bool:
    """True when a refresh should start now (pure function — unit-tested)."""
    now = now or now_ist()
    last_ok = _parse_ts(meta.get("last_success"))
    last_try = _parse_ts(meta.get("last_attempt"))
    failed_recently = bool(meta.get("last_error")) and last_try is not None and (now - last_try) < timedelta(minutes=RETRY_AFTER_FAIL_MIN)
    if failed_recently:
        return False
    if history_empty or last_ok is None:
        return True                                   # first run: backfill immediately
    return last_ok < last_due_time(now)


# ───────────────────────── persistence ─────────────────────────
def _sb():
    global _SB, _SB_TRIED
    if _SB_TRIED:
        return _SB
    _SB_TRIED = True
    try:
        url = st.secrets.get("SUPABASE_URL", "") if st else ""
        key = st.secrets.get("SUPABASE_KEY", "") if st else ""
        if url and key:
            from supabase import create_client
            _SB = create_client(url, key)
    except Exception:
        _SB = None
    return _SB


def _sb_get(key: str):
    c = _sb()
    if c is None:
        return None
    try:
        r = c.table("breadth_store").select("value").eq("key", key).execute()
        return r.data[0]["value"] if r.data else None
    except Exception:
        return None


def _sb_put(key: str, value) -> Optional[str]:
    c = _sb()
    if c is None:
        return "supabase not configured"
    try:
        c.table("breadth_store").upsert({"key": key, "value": value, "updated_at": now_ist().isoformat()}).execute()
        return None
    except Exception as e:
        return str(e)


def _rows_to_df(rows) -> pd.DataFrame:
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "date" not in df:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for c in COLS:
        if c not in df:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    if "source" not in df:
        df["source"] = ""
    df["net_advances"] = (df["advances"].astype("float") - df["declines"].astype("float")).round().astype("Int64")
    return df


def _df_to_rows(df: pd.DataFrame) -> list:
    out = []
    for _, r in df.sort_values("date").tail(MAX_ROWS).iterrows():
        rec = {"date": pd.Timestamp(r["date"]).strftime("%Y-%m-%d"), "source": str(r.get("source", "") or "")}
        for c in COLS:
            v = r.get(c)
            rec[c] = None if pd.isna(v) else int(v)
        out.append(rec)
    return out


def load_history(max_age: float = 20.0) -> pd.DataFrame:
    with _LOCK:
        if _MEM["history"] is not None and (_time.time() - _MEM["history_ts"]) < max_age:
            return _MEM["history"].copy()
    rows = None
    v = _sb_get("history")
    if isinstance(v, list) and v:
        rows = v
    if rows is None and HISTORY_FILE.exists():
        try:
            rows = json.loads(HISTORY_FILE.read_text())
        except Exception:
            rows = None
    df = _rows_to_df(rows)
    with _LOCK:
        _MEM["history"], _MEM["history_ts"] = df, _time.time()
    return df.copy()


def save_history(df: pd.DataFrame) -> Optional[str]:
    rows = _df_to_rows(df)
    try:
        HISTORY_FILE.write_text(json.dumps(rows))
    except Exception:
        pass
    err = _sb_put("history", rows)
    with _LOCK:
        _MEM["history"], _MEM["history_ts"] = _rows_to_df(rows), _time.time()
    return err


def load_meta(max_age: float = 5.0) -> dict:
    with _LOCK:
        if _MEM["meta"] is not None and (_time.time() - _MEM["meta_ts"]) < max_age:
            return dict(_MEM["meta"])
    meta = _sb_get("meta")
    if not isinstance(meta, dict) and META_FILE.exists():
        try:
            meta = json.loads(META_FILE.read_text())
        except Exception:
            meta = None
    meta = meta if isinstance(meta, dict) else {}
    with _LOCK:
        _MEM["meta"], _MEM["meta_ts"] = meta, _time.time()
    return dict(meta)


def save_meta(meta: dict) -> None:
    try:
        META_FILE.write_text(json.dumps(meta, default=str))
    except Exception:
        pass
    _sb_put("meta", json.loads(json.dumps(meta, default=str)))
    with _LOCK:
        _MEM["meta"], _MEM["meta_ts"] = dict(meta), _time.time()


def clear_stored() -> None:
    for p in (HISTORY_FILE, META_FILE):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    _sb_put("history", [])
    _sb_put("meta", {})
    with _LOCK:
        _MEM.update(history=None, history_ts=0.0, meta=None, meta_ts=0.0)


# ───────────────────────── universe + prices ─────────────────────────
def _clean_symbols(symbols) -> list:
    out = []
    for s in symbols:
        s = str(s).strip().upper()
        if re.fullmatch(r"[A-Z0-9&\-]{1,20}", s):
            out.append(s)
    return list(dict.fromkeys(out))


def load_universe() -> tuple[list, str]:
    """(symbols, human label). Never raises."""
    try:
        local = pd.read_csv("nse_universe.csv")
        col = "SYMBOL" if "SYMBOL" in local.columns else local.columns[0]
        syms = _clean_symbols(local[col].dropna().tolist())
        if len(syms) > 500:
            return syms, f"nse_universe.csv · {len(syms):,} stocks"
    except Exception:
        pass
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                          "Accept-Language": "en-US,en;q=0.9"})
        try:
            s.get("https://www.nseindia.com", timeout=8)
        except Exception:
            pass
        for url in NSE_ARCHIVE_URLS:
            try:
                r = s.get(url, timeout=15)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = [c.strip() for c in df.columns]
                if "SERIES" in df.columns:
                    df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
                syms = _clean_symbols(df["SYMBOL"].dropna().tolist())
                if len(syms) > 1000:
                    return syms, f"NSE archive · {len(syms):,} EQ stocks"
            except Exception:
                continue
    except Exception:
        pass
    syms = _clean_symbols(LIQUID_FALLBACK)
    return syms, f"FALLBACK · {len(syms)} liquid large caps (add nse_universe.csv for full-market breadth)"


def _closes_from_download(df, tickers) -> pd.DataFrame:
    out = {}
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        lv0, lv1 = set(df.columns.get_level_values(0)), set(df.columns.get_level_values(1))
        for t in tickers:
            try:
                if t in lv0:
                    s = df[t]["Close"]
                elif t in lv1:
                    s = df["Close"][t]
                else:
                    continue
                s = pd.to_numeric(s, errors="coerce").dropna()
                if len(s) >= 30:
                    out[t[:-3]] = s
            except Exception:
                continue
    elif len(tickers) == 1 and "Close" in df.columns:
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(s) >= 30:
            out[tickers[0][:-3]] = s
    res = pd.DataFrame(out)
    if res.empty:
        return res
    idx = pd.to_datetime(res.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    res.index = idx.normalize()
    return res[~res.index.duplicated(keep="last")].sort_index()


def download_closes(symbols: list, period: str, cb: Optional[Callable[[str], None]] = None) -> tuple[pd.DataFrame, list]:
    import yfinance as yf
    frames, missing = [], []
    n_b = max(1, math.ceil(len(symbols) / BATCH))
    for i in range(n_b):
        chunk = symbols[i * BATCH:(i + 1) * BATCH]
        tickers = [f"{s}.NS" for s in chunk]
        raw = None
        for attempt in range(2):
            try:
                raw = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                                  progress=False, threads=True, auto_adjust=False)
                break
            except Exception:
                _time.sleep(3)
        got = _closes_from_download(raw, tickers)
        frames.append(got)
        missing += [s for s in chunk if s not in got.columns]
        if cb:
            cb(f"Downloading prices · batch {i + 1}/{n_b} · {sum(f.shape[1] for f in frames):,} stocks so far")
        _time.sleep(0.4)
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(), missing
    return pd.concat(frames, axis=1).sort_index(), missing


# ───────────────────────── the maths (pure) ─────────────────────────
def compute_breadth(close: pd.DataFrame, min_coverage: float = 0.6, last_n: Optional[int] = None) -> pd.DataFrame:
    """
    close: dates x symbols (NaN where the stock has no bar). Returns one row per session:
      above/below 20/50/200 DMA (stocks with a valid SMA only), advances/declines/unchanged,
      new 52-week highs/lows (close-based), universe (stocks with a real bar that session).
    Sessions where fewer than min_coverage of the usual universe has a bar (partial/incomplete) are dropped.
    """
    close = close.sort_index()
    has = close.notna()
    cover = has.sum(axis=1) / max(close.shape[1], 1)
    ref = float(cover.tail(60).median()) if len(cover) else 0.0
    keep = cover[cover >= min_coverage * ref].index
    close, has = close.loc[keep], has.loc[keep]
    ff = close.ffill()
    out = pd.DataFrame(index=close.index)
    for n in (20, 50, 200):
        sma = ff.rolling(n, min_periods=n).mean()
        valid = has & sma.notna()
        a = ((ff > sma) & valid).sum(axis=1)
        b = ((ff <= sma) & valid).sum(axis=1)
        none = (a + b) == 0
        out[f"above_{n}dma"] = a.where(~none)
        out[f"below_{n}dma"] = b.where(~none)
    prev = ff.shift(1)
    ok = has & prev.notna()
    out["advances"] = ((ff > prev) & ok).sum(axis=1)
    out["declines"] = ((ff < prev) & ok).sum(axis=1)
    out["unchanged"] = ((ff == prev) & ok).sum(axis=1)
    hi = ff.rolling(252, min_periods=200).max()
    lo = ff.rolling(252, min_periods=200).min()
    vh, vl = has & hi.notna(), has & lo.notna()
    n_hi = ((ff >= hi) & vh).sum(axis=1)
    n_lo = ((ff <= lo) & vl).sum(axis=1)
    out["new_52w_high"] = n_hi.where(vh.any(axis=1))
    out["new_52w_low"] = n_lo.where(vl.any(axis=1))
    out["universe"] = has.sum(axis=1)
    out = out[(out["above_20dma"].notna())]
    out = out.reset_index().rename(columns={"index": "date"})
    out.columns = ["date"] + list(out.columns[1:])
    if last_n:
        out = out.tail(last_n)
    return out.reset_index(drop=True)


def merge_history(old: pd.DataFrame, new_rows: pd.DataFrame, source: str) -> pd.DataFrame:
    new = new_rows.copy()
    new["date"] = pd.to_datetime(new["date"])
    new["source"] = source
    n = new.set_index("date")
    if old is None or old.empty:
        merged = n
    else:
        o = old.set_index("date")
        merged = n.combine_first(o)                       # new values win; old fills gaps (e.g. 200-DMA before enough history)
    merged = merged.sort_index().reset_index()
    return _rows_to_df(_df_to_rows(merged))


# ───────────────────────── Chartink cross-check (optional, tolerant) ─────────────────────────
CHARTINK_DASHBOARD_URL = "https://chartink.com/dashboard/86550"
CHARTINK_WIDGET_URL = "https://chartink.com/widget/process"
_CK_ALIASES = {"above20dma": "above_20dma", "below20dma": "below_20dma", "above50dma": "above_50dma",
               "below50dma": "below_50dma", "above200dma": "above_200dma", "below200dma": "below_200dma"}
_CK_QUERY = """select
    groupcount( {cash} 1 where latest close > latest sma( close , 20 ) ) as 'Above20dma',
    groupcount( {cash} 1 where latest close <= latest sma( close , 20 ) ) as 'Below20dma',
    groupcount( {cash} 1 where latest close > latest sma( close , 50 ) ) as 'Above50dma',
    groupcount( {cash} 1 where latest close <= latest sma( close , 50 ) ) as 'Below50dma',
    groupcount( {cash} 1 where latest close > latest sma( close , 200 ) ) as 'Above200dma',
    groupcount( {cash} 1 where latest close <= latest sma( close , 200 ) ) as 'Below200dma'"""


def parse_chartink_snapshot(payload: Any) -> dict:
    """Find the six Above/Below DMA values anywhere in a Chartink JSON response (scalar OR list -> last value)."""
    found: dict[str, float] = {}

    def num(v):
        try:
            f = float(v)
            return f if math.isfinite(f) else None
        except Exception:
            return None

    def visit(node):
        if len(found) == len(_CK_ALIASES):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                key = re.sub(r"[^a-z0-9]", "", str(k).lower())
                if key in _CK_ALIASES:
                    val = v[-1] if isinstance(v, (list, tuple)) and v else v
                    if isinstance(val, (list, tuple)) and val:
                        val = val[-1]
                    n = num(val)
                    if n is not None:
                        found[_CK_ALIASES[key]] = n
            for v in node.values():
                visit(v)
        elif isinstance(node, (list, tuple)):
            for it in node:
                visit(it)

    visit(payload)
    return {k: int(round(v)) for k, v in found.items()} if len(found) >= 4 else {}


def fetch_chartink_snapshot() -> tuple[Optional[dict], Optional[str]]:
    """(snapshot, error). Never raises. Short timeouts; failure is informational only."""
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                          "Referer": CHARTINK_DASHBOARD_URL})
        page = s.get(CHARTINK_DASHBOARD_URL, timeout=(6, 12))
        page.raise_for_status()
        m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)', page.text, flags=re.I)
        if m:
            s.headers["X-CSRF-TOKEN"] = m.group(1)
        s.headers.update({"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        resp = s.post(CHARTINK_WIDGET_URL, data={"query": _CK_QUERY.strip()}, timeout=(6, 20))
        resp.raise_for_status()
        try:
            payload = resp.json()
        except Exception:
            return None, f"Chartink did not return JSON (HTTP {resp.status_code})"
        snap = parse_chartink_snapshot(payload)
        if not snap:
            return None, "Chartink responded but no Above/Below DMA values were found in the response"
        return snap, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ───────────────────────── sync orchestration ─────────────────────────
def _progress(msg: str) -> None:
    with _LOCK:
        STATE["progress"] = msg


def run_sync(mode: str = "daily", cb: Optional[Callable[[str], None]] = None) -> dict:
    """Blocking sync (used by the worker thread and by tests). mode: 'daily' | 'backfill'."""
    cb = cb or _progress
    meta = load_meta(max_age=0)
    meta["last_attempt"] = now_ist().isoformat()
    history = load_history(max_age=0)
    backfill = (mode == "backfill") or history.empty
    try:
        cb("Loading NSE universe…")
        symbols, label = load_universe()
        close, missing = download_closes(symbols, "2y" if backfill else "1y", cb)
        if close.shape[1] < 50:
            raise RuntimeError(f"Price download returned only {close.shape[1]} stocks — Yahoo Finance may be rate-limiting. "
                               f"It will retry automatically in {RETRY_AFTER_FAIL_MIN} minutes.")
        cb("Computing breadth…")
        rows = compute_breadth(close, last_n=None if backfill else 10)
        if rows.empty:
            raise RuntimeError("Breadth computation produced no rows.")
        src = f"Arka · NSE EOD prices ({close.shape[1]:,} stocks)"
        merged = merge_history(history, rows, src)
        perr = save_history(merged)
        meta.update(last_success=now_ist().isoformat(), last_error=None, mode="backfill" if backfill else "daily",
                    source=src, universe=int(close.shape[1]), universe_label=label, missing=len(missing),
                    latest_date=str(pd.Timestamp(merged.iloc[-1]["date"]).date()), rows=int(len(merged)),
                    persist_error=perr)
        cb("Cross-checking with Chartink…")
        snap, cerr = fetch_chartink_snapshot()
        meta["chartink"] = ({"at": now_ist().isoformat(), **snap} if snap else None)
        meta["chartink_error"] = cerr
        save_meta(meta)
        return {"ok": True, "rows": len(merged), "universe": int(close.shape[1])}
    except Exception as e:
        meta["last_error"] = f"{type(e).__name__}: {e}"
        save_meta(meta)
        return {"ok": False, "error": meta["last_error"]}


def _worker(mode: str) -> None:
    try:
        run_sync(mode)
    finally:
        with _LOCK:
            STATE["running"] = False
            STATE["finished"] = _time.time()
            STATE["progress"] = ""


def start_sync(mode: str = "daily") -> bool:
    """Start a background sync. Returns False if one is already running."""
    with _LOCK:
        if STATE["running"]:
            return False
        STATE.update(running=True, progress="Starting…", started=_time.time(), error=None, mode=mode)
    threading.Thread(target=_worker, args=(mode,), daemon=True, name="arka-breadth-sync").start()
    return True


def maybe_start() -> bool:
    """Start a sync if (and only if) one is due. Safe to call on every page render."""
    try:
        hist = load_history()
        if is_due(load_meta(), hist.empty):
            return start_sync("backfill" if hist.empty else "daily")
    except Exception:
        pass
    return False


def _scheduler_loop() -> None:
    while True:
        try:
            maybe_start()
        except Exception:
            pass
        _time.sleep(60)


def ensure_scheduler() -> None:
    """Start the once-per-process scheduler thread (checks every minute)."""
    global _SCHED
    with _LOCK:
        if _SCHED is not None and _SCHED.is_alive():
            return
        _SCHED = threading.Thread(target=_scheduler_loop, daemon=True, name="arka-breadth-scheduler")
        _SCHED.start()


# ───────────────────────── compatibility helpers ─────────────────────────
def get_nse_universe(*_a, **_k):
    return load_universe()


def sync_chartink(force: bool = True) -> dict:
    """Legacy name: now triggers the Arka computed sync (blocking)."""
    return run_sync("daily")


def compute_breadth_snapshot(*_a, **_k) -> dict:
    h = load_history()
    if h.empty:
        return {"error": "No breadth history yet."}
    r = h.iloc[-1]
    out = {"date": pd.Timestamp(r["date"]).strftime("%d %b %Y"), "source": r.get("source", ""),
           "total_stocks": int(r["universe"]) if pd.notna(r["universe"]) else 0}
    for c in COLS:
        out[c] = None if pd.isna(r.get(c)) else int(r[c])
    return out
