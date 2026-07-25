"""
breadth_engine.py — Market Breadth data & math layer for Arka Trades.

Computes the same family of numbers Chartink's Market Breadth dashboard
shows (advance/decline, 5d new highs/lows, above/below 20/50/200 DMA),
plus two derived series Chartink's raw table doesn't give you:
  - A/D Line: cumulative running sum of (advances - declines). Shows
    whether breadth is *building* or *fading*, not just today's count.
  - McClellan Oscillator: 19-day EMA minus 39-day EMA of daily net
    advances. Standard breadth-momentum indicator — leads index turns
    more often than the index itself.

Universe fetch reuses the same fallback pattern as smart_scan_page.py's
NSE archive fetcher: session-based live fetch -> local CSV cache ->
hardcoded liquid-list floor. Do not replace this with a single
un-cached yf.download over a hardcoded list; NSE's URL has broken this
before (see arka-trades memory) and Streamlit Cloud RAM limits mean the
whole universe can't be pulled uncached on every rerun.
"""

import streamlit as st
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

# yfinance and streamlit are imported inside the specific functions that
# need them (_batch_download, get_nse_universe's cache), not at module
# level. compute_ad_line_and_mcclellan and compute_composite_score below
# are pure pandas math with no network or Streamlit dependency — keeping
# those imports function-local means the math functions stay importable
# and unit-testable even if yfinance/streamlit are slow to import, fail
# to import, or aren't installed in whatever context is testing this
# file. It also means a future change to the data-fetch layer can't
# accidentally break the composite-score math through a shared import
# chain.

IST = timezone(timedelta(hours=5, minutes=30))

# Fallback floor if both live NSE fetch and local cache fail. Deliberately
# liquid, large-cap, index-representative — never the full universe, just
# enough that breadth numbers are directionally meaningful rather than
# empty. Extend this list over time rather than depending on it being
# reached in normal operation.
LIQUID_FALLBACK = [
    "RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","HINDUNILVR","ITC",
    "SBIN","BHARTIARTL","KOTAKBANK","LT","AXISBANK","BAJFINANCE","ASIANPAINT",
    "MARUTI","HCLTECH","SUNPHARMA","TITAN","ULTRACEMCO","WIPRO","NESTLEIND",
    "M&M","ADANIENT","POWERGRID","NTPC","TATAMOTORS","TATASTEEL","JSWSTEEL",
    "COALINDIA","TECHM","BAJAJFINSV","HINDALCO","DRREDDY","GRASIM","CIPLA",
    "EICHERMOT","BRITANNIA","DIVISLAB","HEROMOTOCO","APOLLOHOSP","INDUSINDBK",
    "TATACONSUM","BPCL","ONGC","SBILIFE","HDFCLIFE","BAJAJ-AUTO","UPL",
    "SHREECEM","ADANIPORTS","VEDL","GODREJCP","DABUR","PIDILITIND","SIEMENS",
    "DLF","AMBUJACEM","BANDHANBNK","BANKBARODA","CHOLAFIN","COLPALIN","GAIL",
    "HAVELLS","ICICIGI","ICICIPRULI","IOC","LUPIN","MARICO","MOTHERSON",
    "MUTHOOTFIN","NAUKRI","PAGEIND","PEL","PERSISTENT","PIIND","SAIL",
    "SRF","TATAPOWER","TORNTPHARM","TRENT","TVSMOTOR","ZOMATO",
]

_CACHE_DIR = Path(".cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_UNIVERSE_CACHE_FILE = _CACHE_DIR / "nse_universe.json"
_UNIVERSE_CACHE_TTL_HOURS = 24


def _fetch_nse_universe_live() -> Optional[list]:
    """
    Attempt a live fetch of the NSE equity list. Wrapped tightly in
    try/except with a short timeout because this is a scraped endpoint,
    not a stable API — it WILL break again at some point. Every failure
    mode here falls through to the caller's next tier, it never raises.
    """
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36",
            "Accept": "text/csv,application/csv,*/*",
        })
        session.get("https://www.nseindia.com", timeout=6)
        resp = session.get(
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
            timeout=10,
        )
        if resp.status_code != 200 or len(resp.content) < 1000:
            return None
        df = pd.read_csv(io.BytesIO(resp.content))
        sym_col = next((c for c in df.columns if c.strip().upper() == "SYMBOL"), None)
        series_col = next((c for c in df.columns if "SERIES" in c.strip().upper()), None)
        if sym_col is None:
            return None
        if series_col is not None:
            df = df[df[series_col].astype(str).str.strip() == "EQ"]
        symbols = df[sym_col].astype(str).str.strip().str.upper().tolist()
        symbols = [s for s in symbols if s and s != "NAN"]
        return list(dict.fromkeys(symbols)) if len(symbols) > 500 else None
    except Exception:
        return None


def get_nse_universe(force_refresh: bool = False) -> tuple[list, str]:
    """
    Returns (symbol_list, source_label). source_label is surfaced in the
    UI so you always know whether breadth counts are computed off the
    live full universe, a cached one, or the liquid fallback floor —
    never silently.
    """
    if not force_refresh and _UNIVERSE_CACHE_FILE.exists():
        try:
            cached = json.loads(_UNIVERSE_CACHE_FILE.read_text())
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
            if age_hours < _UNIVERSE_CACHE_TTL_HOURS and len(cached["symbols"]) > 500:
                return cached["symbols"], f"cached ({age_hours:.0f}h old, {len(cached['symbols'])} stocks)"
        except Exception:
            pass

    live = _fetch_nse_universe_live()
    if live:
        try:
            _UNIVERSE_CACHE_FILE.write_text(json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "symbols": live,
            }))
        except Exception:
            pass
        return live, f"live NSE fetch ({len(live)} stocks)"

    if _UNIVERSE_CACHE_FILE.exists():
        try:
            cached = json.loads(_UNIVERSE_CACHE_FILE.read_text())
            if len(cached["symbols"]) > 500:
                return cached["symbols"], f"stale cache ({len(cached['symbols'])} stocks) — live fetch failed"
        except Exception:
            pass

    return LIQUID_FALLBACK, f"liquid fallback ({len(LIQUID_FALLBACK)} stocks) — live + cache both unavailable"


@st.cache_data(ttl=900, show_spinner=False)
def _batch_download(symbols, period: str = "260d") -> dict:
    """
    Batched yf.download across the universe. 260 calendar days covers the
    200-trading-day MA with room for holidays. Returns {symbol: DataFrame}.
    Cached 15min — breadth doesn't need 10s refresh like the price
    scanner; recomputing 1500+ tickers every rerun would blow Streamlit
    Cloud's RAM/time budget for no real benefit, since daily breadth
    counts don't materially move minute to minute.
    """
    # Safe conversion: handle tuple, list, or single items robustly
    if isinstance(symbols, tuple):
        symbols = list(symbols)
    elif not isinstance(symbols, list):
        symbols = [symbols]

    tickers = [s if str(s).endswith(".NS") else str(s) + ".NS" for s in symbols]
    result = {}
    chunk_size = 200
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            data = yf.download(
                chunk, period=period, interval="1d",
                group_by="ticker", threads=True, progress=False,
                auto_adjust=True,
            )
        except Exception:
            continue
        for t in chunk:
            sym = t[:-3] if t.endswith(".NS") else t
            try:
                if len(chunk) == 1:
                    df = data
                else:
                    df = data[t]
                df = df.dropna(how="all")
                if len(df) >= 20:
                    result[sym] = df
            except Exception:
                continue
    return result
    result = {}
    chunk_size = 200
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            data = yf.download(
                chunk, period=period, interval="1d",
                group_by="ticker", threads=True, progress=False,
                auto_adjust=True,
            )
        except Exception:
            continue
        for t in chunk:
            sym = t[:-3]
            try:
                if len(chunk) == 1:
                    df = data
                else:
                    df = data[t]
                df = df.dropna(how="all")
                if len(df) >= 20:
                    result[sym] = df
            except Exception:
                continue
    return result


def _pct_change_5d(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 6:
        return None
    try:
        return (df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1) * 100
    except Exception:
        return None


def compute_breadth_snapshot(symbols: list) -> dict:
    """
    The core computation. Returns a dict with every metric in the
    Chartink screenshot (advances/declines, 5d up/down 20%+, above/below
    20/50/200 DMA) plus per-symbol classification used for the AI
    narrative and any drill-down UI.

    NOTE: "Up/Down 4.5%+ today" and "Up/Down 20%+ in 5d" thresholds are
    Chartink's own scan parameters, not a universal standard — they are
    reproduced here as configurable constants (THRESH_DAY_PCT,
    THRESH_5D_PCT below) so you can retune them without touching the
    computation logic.
    """
    THRESH_DAY_PCT = 4.5
    THRESH_5D_PCT = 20.0

    price_data = _batch_download(tuple(symbols))
    if not price_data:
        return {"error": "No price data returned for any symbol in the universe."}

    up_day = down_day = up_5d = down_5d = 0
    above_20 = below_20 = above_50 = below_50 = above_200 = below_200 = 0
    new_hi_5d = new_lo_5d = 0
    advances = declines = unchanged = 0
    per_symbol = {}

    for sym, df in price_data.items():
        try:
            close = df["Close"]
            if len(close) < 2:
                continue
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            day_pct = (last / prev - 1) * 100 if prev else 0.0

            if day_pct > 0.05:
                advances += 1
            elif day_pct < -0.05:
                declines += 1
            else:
                unchanged += 1

            if day_pct >= THRESH_DAY_PCT:
                up_day += 1
            elif day_pct <= -THRESH_DAY_PCT:
                down_day += 1

            chg5d = _pct_change_5d(df)
            if chg5d is not None:
                if chg5d >= THRESH_5D_PCT:
                    up_5d += 1
                elif chg5d <= -THRESH_5D_PCT:
                    down_5d += 1

            if len(close) >= 20:
                sma20 = close.rolling(20).mean().iloc[-1]
                if pd.notna(sma20):
                    if last > sma20:
                        above_20 += 1
                    else:
                        below_20 += 1
            if len(close) >= 50:
                sma50 = close.rolling(50).mean().iloc[-1]
                if pd.notna(sma50):
                    if last > sma50:
                        above_50 += 1
                    else:
                        below_50 += 1
            if len(close) >= 200:
                sma200 = close.rolling(200).mean().iloc[-1]
                if pd.notna(sma200):
                    if last > sma200:
                        above_200 += 1
                    else:
                        below_200 += 1

            window5 = close.tail(5)
            if len(window5) == 5:
                if last >= window5.max():
                    new_hi_5d += 1
                if last <= window5.min():
                    new_lo_5d += 1

            per_symbol[sym] = {
                "price": last, "day_pct": round(day_pct, 2),
                "chg_5d": round(chg5d, 2) if chg5d is not None else None,
                "above_20dma": last > sma20 if len(close) >= 20 and pd.notna(sma20) else None,
                "above_50dma": last > sma50 if len(close) >= 50 and pd.notna(sma50) else None,
                "above_200dma": last > sma200 if len(close) >= 200 and pd.notna(sma200) else None,
            }
        except Exception:
            continue

    total = len(per_symbol)
    if total == 0:
        return {"error": "Price data fetched but no symbols had enough history to compute breadth."}

    return {
        "date": datetime.now(IST).strftime("%d %b %Y, %I:%M%p"),
        "total_stocks": total,
        "advances": advances, "declines": declines, "unchanged": unchanged,
        "up_day_pct": up_day, "down_day_pct": down_day,
        "up_5d_pct": up_5d, "down_5d_pct": down_5d,
        "above_20dma": above_20, "below_20dma": below_20,
        "above_50dma": above_50, "below_50dma": below_50,
        "above_200dma": above_200, "below_200dma": below_200,
        "new_hi_5d": new_hi_5d, "new_lo_5d": new_lo_5d,
        "per_symbol": per_symbol,
        "thresholds": {"day_pct": THRESH_DAY_PCT, "five_day_pct": THRESH_5D_PCT},
    }


# ── Historical series (A/D Line, McClellan) ─────────────────────────

_HISTORY_FILE = _CACHE_DIR / "breadth_history.jsonl"


def append_history(snapshot: dict) -> None:
    """
    Appends today's snapshot to a local JSONL history file so A/D Line
    and McClellan (which need multi-day series, not a single snapshot)
    can be computed. Streamlit Cloud's filesystem is ephemeral on
    redeploy — if you want this to survive redeploys, point this at a
    Supabase table instead (you already have Supabase wired for
    watchlist/alerts; a `breadth_history` table with the same
    delete+insert pattern as db_save_watchlist would work). Flagged
    here rather than silently building on a non-durable store.
    """
    if "error" in snapshot:
        return
    record = {
        "date": datetime.now(IST).strftime("%Y-%m-%d"),
        "advances": snapshot["advances"],
        "declines": snapshot["declines"],
        "above_20dma": snapshot["above_20dma"],
        "below_20dma": snapshot["below_20dma"],
        "above_50dma": snapshot["above_50dma"],
        "below_50dma": snapshot["below_50dma"],
        "above_200dma": snapshot["above_200dma"],
        "below_200dma": snapshot["below_200dma"],
        "new_hi_5d": snapshot["new_hi_5d"],
        "new_lo_5d": snapshot["new_lo_5d"],
    }
    try:
        existing = []
        if _HISTORY_FILE.exists():
            existing = [json.loads(l) for l in _HISTORY_FILE.read_text().splitlines() if l.strip()]
        existing = [r for r in existing if r["date"] != record["date"]]
        existing.append(record)
        existing.sort(key=lambda r: r["date"])
        existing = existing[-90:]
        _HISTORY_FILE.write_text("\n".join(json.dumps(r) for r in existing))
    except Exception:
        pass


def load_history() -> pd.DataFrame:
    if not _HISTORY_FILE.exists():
        return pd.DataFrame()
    try:
        rows = [json.loads(l) for l in _HISTORY_FILE.read_text().splitlines() if l.strip()]
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def compute_ad_line_and_mcclellan(history: pd.DataFrame) -> pd.DataFrame:
    """
    A/D Line: cumsum(advances - declines). McClellan Oscillator:
    ema19(net_advances) - ema39(net_advances), computed on whatever
    history is available (McClellan is directionally usable before 39
    days accumulate, since pandas ewm adapts its effective window to
    however much data exists — it just won't be the standard-strength
    reading until enough days are on file). Both return NaN-safe
    columns if history is too short for a given field.
    """
    if history.empty:
        return history
    df = history.copy()
    df["net_advances"] = df["advances"] - df["declines"]
    df["ad_line"] = df["net_advances"].cumsum()
    if len(df) >= 2:
        ema19 = df["net_advances"].ewm(span=19, adjust=False).mean()
        ema39 = df["net_advances"].ewm(span=39, adjust=False).mean()
        df["mcclellan"] = ema19 - ema39
    else:
        df["mcclellan"] = None
    return df


# ── Composite strength score ─────────────────────────────────────────

def compute_composite_score(snapshot: dict, history: pd.DataFrame) -> dict:
    """
    Weighted 0-100 composite across four metric families. Returns the
    score plus a per-family breakdown so the UI can show *why*, not
    just the number — a bare score without the breakdown is not
    actionable, you'd have no way to tell if it's the MA breadth or the
    new hi/lo count dragging it down.

    Weights (sum to 100):
      - A/D ratio today               25
      - MA breadth (20/50/200 avg)    35
      - New 5d hi/lo differential     20
      - McClellan Oscillator sign     20  (0 if history too short)

    This is a deliberately transparent, rule-based score — not a
    trained model. Retune the weights below if your own read of past
    strong/weak regimes disagrees with how this scores them; that's a
    parameter change, not a rewrite.
    """
    if "error" in snapshot:
        return {"score": None, "label": "N/A", "error": snapshot["error"]}

    total = snapshot["total_stocks"]
    if total == 0:
        return {"score": None, "label": "N/A", "error": "Zero-stock snapshot."}

    # 1. A/D ratio -> 0-25
    adv, dec = snapshot["advances"], snapshot["declines"]
    ad_ratio = adv / dec if dec > 0 else (2.0 if adv > 0 else 1.0)
    ad_score = min(25, max(0, (ad_ratio / 2.0) * 25))

    # 2. MA breadth -> 0-35, average of the three % above
    pct_above_20 = snapshot["above_20dma"] / total if total else 0
    pct_above_50 = snapshot["above_50dma"] / total if total else 0
    pct_above_200 = snapshot["above_200dma"] / total if total else 0
    ma_avg_pct = (pct_above_20 + pct_above_50 + pct_above_200) / 3
    ma_score = ma_avg_pct * 35

    # 3. New hi/lo differential -> 0-20
    hi, lo = snapshot["new_hi_5d"], snapshot["new_lo_5d"]
    hilo_net = hi - lo
    hilo_score = 10 + max(-10, min(10, (hilo_net / max(total * 0.1, 1)) * 10))

    # 4. McClellan sign/magnitude -> 0-20 (neutral 10 if unavailable)
    mcclellan_score = 10.0
    mcclellan_val = None
    if not history.empty and "mcclellan" in history.columns:
        recent = history["mcclellan"].dropna()
        if len(recent) > 0:
            mcclellan_val = float(recent.iloc[-1])
            mcclellan_score = 10 + max(-10, min(10, (mcclellan_val / max(total * 0.05, 1)) * 10))

    score = round(ad_score + ma_score + hilo_score + mcclellan_score, 1)
    score = max(0, min(100, score))

    if score >= 70:
        label, tone = "STRONG", "bullish"
    elif score >= 55:
        label, tone = "MODERATELY STRONG", "leaning bullish"
    elif score >= 45:
        label, tone = "NEUTRAL / MIXED", "no clear edge"
    elif score >= 30:
        label, tone = "MODERATELY WEAK", "leaning bearish"
    else:
        label, tone = "WEAK", "bearish"

    return {
        "score": score,
        "label": label,
        "tone": tone,
        "breakdown": {
            "ad_ratio": {"value": round(ad_ratio, 2), "points": round(ad_score, 1), "max": 25},
            "ma_breadth": {"value": f"{ma_avg_pct*100:.1f}%", "points": round(ma_score, 1), "max": 35},
            "new_hilo": {"value": f"{hi} hi / {lo} lo", "points": round(hilo_score, 1), "max": 20},
            "mcclellan": {
                "value": round(mcclellan_val, 1) if mcclellan_val is not None else "insufficient history",
                "points": round(mcclellan_score, 1), "max": 20,
            },
        },
    }
def compute_daily_breadth_metrics(data, tickers):
    """Bridge wrapper for compatibility with breadth_page.py UI."""
    snapshot = compute_breadth_snapshot(tickers)
    if "error" in snapshot:
        return {
            "total_scanned": 0, "advances": 0, "declines": 0, "unchanged": 0,
            "net_advances": 0, "above_20dma": 0, "above_50dma": 0, "above_200dma": 0,
            "pct_above_20dma": 0.0, "pct_above_50dma": 0.0, "pct_above_200dma": 0.0,
            "up_5d": 0, "down_5d": 0, "new_52w_hi": 0, "new_52w_lo": 0
        }
    
    total = snapshot.get("total_scanned", 1)
    adv = snapshot.get("advances", 0)
    dec = snapshot.get("declines", 0)
    
    return {
        "total_scanned": total,
        "advances": adv,
        "declines": dec,
        "unchanged": snapshot.get("unchanged", 0),
        "net_advances": adv - dec,
        "above_20dma": snapshot.get("above_20dma", 0),
        "above_50dma": snapshot.get("above_50dma", 0),
        "above_200dma": snapshot.get("above_200dma", 0),
        "pct_above_20dma": round((snapshot.get("above_20dma", 0) / max(total, 1)) * 100, 1),
        "pct_above_50dma": round((snapshot.get("above_50dma", 0) / max(total, 1)) * 100, 1),
        "pct_above_200dma": round((snapshot.get("above_200dma", 0) / max(total, 1)) * 100, 1),
        "up_5d": snapshot.get("up_5d_pct", 0),
        "down_5d": snapshot.get("down_5d_pct", 0),
        "new_52w_hi": snapshot.get("new_hi_5d", 0),
        "new_52w_lo": snapshot.get("new_lo_5d", 0),
    }

def fetch_universe_ohlcv(tickers, period="260d"):
    """Bridge wrapper for breadth_page.py to fetch batch OHLCV data."""
    return _batch_download(tuple(tickers), period=period)
