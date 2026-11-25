"""
scan_export.py — Arka Trades · price files for the Screener of the new app                                   v1.0 · Nov 2026

The Streamlit Screener (smart_scan_page.py) downloads daily candles from Yahoo Finance for every NSE stock and checks your
rules on them. A web page cannot download 2,000 stocks, so this script does the download on GitHub (with the SAME functions
smart_scan_page.py uses) and publishes small files; the new app checks the rules on them in your browser.

    python scan_export.py history --out DIR     every evening: 2 years of daily candles, NIFTY 50, the stock lists, deliveries
    python scan_export.py live --out DIR        in market hours: today's candle so far for every stock

Files written by "history" (branch scan-data):
    meta.json     when it was built, the stock order, the NIFTY 500 list
    h0.json …     the last year of candles, 330 stocks per file (NIFTY 500 stocks first, so a NIFTY 500 scan reads 2 files)
    x0.json …     the year before that (read only for weekly rules, long averages, past dates and the backtest)
    nifty.json    NIFTY 50 closes, 2 years
    deliv.json    delivery % per stock and day (from the delivery store the "Market data" job keeps), when it is there
Written by "live" (branch scan-live):
    live.json     {"at": time in India, "day": date, "nifty": level, "q": {SYMBOL: [open, high, low, close, volume]}}

Prices are whole paise (close × 100; open, high and low as the difference from the close), so the files stay small.
Only public market data. No secrets needed.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
CHUNK = 330
J = dict(separators=(",", ":"), allow_nan=False)


def _day(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")


def pack(frames: dict, syms: list) -> dict:
    """{SYM: frame} -> {"d": [dates], "s": {SYM: [first date number, close, open-close, high-close, low-close, volume(, own date numbers)]}}"""
    days = sorted({d for s in syms for d in frames[s].index})
    pos = {d: i for i, d in enumerate(days)}
    out = {}
    for s in syms:
        df = frames[s]
        c = np.rint(pd.to_numeric(df["Close"], errors="coerce").to_numpy(dtype=float) * 100)
        cols = []
        for name in ("Open", "High", "Low"):
            x = np.rint(pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float) * 100) if name in df.columns else np.full(len(c), np.nan)
            cols.append(np.where(np.isnan(x), 0, x - c))            # a missing open / high / low is taken as the close, as the scanner does
        v = np.nan_to_num(pd.to_numeric(df["Volume"], errors="coerce").to_numpy(dtype=float), nan=0.0) if "Volume" in df.columns else np.zeros(len(c))
        idx = [pos[d] for d in df.index]
        row = [idx[0], [int(x) for x in c]] + [[int(x) for x in col] for col in cols] + [[int(round(x)) for x in v]]
        if idx != list(range(idx[0], idx[0] + len(idx))):
            row.append(idx)                                          # the stock missed some sessions: its own date numbers
        out[s] = row
    return {"d": [_day(d) for d in days], "s": out}


def clean(frames: dict) -> dict:
    out = {}
    for s, df in frames.items():
        if df is None or not len(df) or "Close" not in df.columns:
            continue
        df = df[pd.to_numeric(df["Close"], errors="coerce") > 0]
        if len(df):
            out[s] = df
    return out


def split_year(frames: dict, today) -> tuple:
    """(the last year, the year before): the last year is what Yahoo returns for period="1y", which the Streamlit scan uses."""
    cut = pd.Timestamp(today) - pd.DateOffset(years=1)
    new, old = {}, {}
    for s, df in frames.items():
        a, b = df[df.index >= cut], df[df.index < cut]
        if len(a):
            new[s] = a
            if len(b):
                old[s] = b
        elif len(b):
            new[s] = b                                               # a stock that stopped trading: keep what there is
    return new, old


def order(all_syms: list, n500: list) -> list:
    seen, out = set(), []
    for s in sorted(n500) + sorted(all_syms):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def write_chunks(out: Path, prefix: str, frames: dict, syms: list) -> int:
    for old in out.glob(f"{prefix}[0-9]*.json"):
        old.unlink()
    n = 0
    for k in range(0, len(syms), CHUNK):
        part = [s for s in syms[k:k + CHUNK] if s in frames]
        (out / f"{prefix}{k // CHUNK}.json").write_text(json.dumps(pack(frames, part) if part else {"d": [], "s": {}}, **J))
        n += 1
    return n


def delivery_json(store_csv_gz: bytes, syms: list):
    """The delivery store of the Market data job -> {"d": [dates], "s": {SYM: [delivery % × 100 or null]}}"""
    df = pd.read_csv(io.BytesIO(gzip.decompress(store_csv_gz)), dtype={"symbol": str, "date": str})
    df = df[df["symbol"].isin(set(syms))]
    if df.empty:
        return None
    pct = pd.to_numeric(df["deliv_qty"], errors="coerce") / pd.to_numeric(df["volume"], errors="coerce") * 100
    t = df.assign(pct=pct.replace([np.inf, -np.inf], np.nan)).pivot_table(index="date", columns="symbol", values="pct", aggfunc="first").sort_index()
    num = lambda v: None if pd.isna(v) else int(round(float(v) * 100))
    return {"d": [str(d)[:10] for d in t.index], "s": {s: [num(v) for v in t[s]] for s in t.columns}}


def run_history(out: Path, S=None, deliv_store: Path | None = None) -> dict:
    if S is None:
        import smart_scan_page as S
    now = datetime.now(IST)
    all_syms, all_src = S.get_universe("All NSE stocks")
    n500, n500_src = S.get_universe("NIFTY 500")
    syms = order(all_syms, n500)
    print(f"universe: {len(all_syms)} NSE stocks ({all_src}) · NIFTY 500: {len(n500)} ({n500_src})")
    frames = clean(S._download(syms, "2y", lambda p, m: print(m) if int(p * 100) % 20 == 0 else None, "History"))
    if len(frames) < max(50, len(syms) * 0.5):
        raise SystemExit(f"Only {len(frames)} of {len(syms)} stocks were priced: Yahoo is not answering. Yesterday's files are kept.")
    have = [s for s in syms if s in frames]
    new, old = split_year(frames, now.date())
    out.mkdir(parents=True, exist_ok=True)
    nh = write_chunks(out, "h", new, have)
    write_chunks(out, "x", old, have)
    nifty = S.nifty_close()
    if nifty is not None and len(nifty):
        (out / "nifty.json").write_text(json.dumps({"d": [_day(d) for d in nifty.index], "c": [round(float(v), 2) for v in nifty]}, **J))
    dl = None
    if deliv_store is not None and deliv_store.exists():
        try:
            dl = delivery_json(deliv_store.read_bytes(), have)
            if dl:
                (out / "deliv.json").write_text(json.dumps(dl, **J))
        except Exception as exc:
            print(f"deliveries skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
    last = max(_day(df.index[-1]) for df in new.values())
    meta = {"v": 1, "at": now.isoformat(timespec="seconds"), "last": last, "size": CHUNK, "chunks": nh, "syms": have,
            "miss": [s for s in syms if s not in frames], "n500": sorted(set(n500)), "only500": sorted(set(n500) - set(all_syms)),
            "all_src": all_src, "n500_src": n500_src, "nifty": nifty is not None and len(nifty) > 0, "deliv": len(dl["d"]) if dl else 0}
    (out / "meta.json").write_text(json.dumps(meta, **J))
    print(f"history: {len(have)} stocks in {nh} files, candles to {last}, {len(meta['miss'])} without prices, deliveries {meta['deliv']} sessions")
    return meta


def market_open(now=None) -> bool:
    now = now or datetime.now(IST)
    t = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 15 <= t <= 15 * 60 + 45


def run_live(out: Path, S=None, syms: list | None = None, force: bool = False) -> dict | None:
    now = datetime.now(IST)
    if not force and not market_open(now):
        print("live: the market is closed, nothing to do")
        return None
    if S is None:
        import smart_scan_page as S
    if syms is None:
        syms = order(S.get_universe("All NSE stocks")[0], S.get_universe("NIFTY 500")[0])
    snaps = clean(S._download(syms, "5d", None, "Prices"))
    today = pd.Timestamp(now.date())
    q = {}
    for s, df in snaps.items():
        if df.index[-1] != today:
            continue
        r = df.iloc[-1]
        c = float(r["Close"])
        g = lambda k: c if pd.isna(r.get(k)) else float(r[k])
        q[s] = [round(g("Open"), 2), round(g("High"), 2), round(g("Low"), 2), round(c, 2), int(0 if pd.isna(r.get("Volume")) else r["Volume"])]
    if len(q) < max(20, len(syms) * 0.4):
        print(f"live: only {len(q)} of {len(syms)} stocks have a candle for today (holiday, or Yahoo is late): nothing published")
        return None
    nf = None
    try:
        nifty = S.nifty_close()
        if nifty is not None and len(nifty) and nifty.index[-1] == today:
            nf = round(float(nifty.iloc[-1]), 2)
    except Exception:
        pass
    data = {"v": 1, "at": now.isoformat(timespec="seconds"), "day": _day(today), "nifty": nf, "q": q}
    out.mkdir(parents=True, exist_ok=True)
    (out / "live.json").write_text(json.dumps(data, **J))
    print(f"live: {len(q)} stocks at {now:%H:%M} IST")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["history", "live"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--deliv", default="", help="path of dlv/_store.csv.gz from the market-data branch (history only)")
    ap.add_argument("--force", action="store_true", help="live: run even when the market is closed")
    a = ap.parse_args()
    if a.what == "history":
        run_history(Path(a.out), deliv_store=Path(a.deliv) if a.deliv else None)
    else:
        run_live(Path(a.out), force=a.force)
