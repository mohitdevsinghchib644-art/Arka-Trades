"""
smart_scan_page.py — Arka Trades · Technical Setup Scanner v2 (Nov 2026)

What it does
------------
You describe YOUR setup (e.g. "stock ran 20%+ in 30 days, RSI above 50 and above its average, price above
EMA 20, then a 3-candle pullback on low volume — 4 candles is also fine"). The scanner turns it into rules
with RANGES, searches the whole NSE (or a list) inside your price band and shows:

  MATCHES       stocks that pass every rule
  NEAR MATCHES  stocks that miss exactly one rule — and it tells you which rule and by how much

Each result: hover the symbol for a 60-day candle + volume + EMA 20 / SMA 50 chart, see a ✓ / ✗ chip per
rule, tick the ones you like and add them to your watchlist (or ADD ALL). An optional AI chart check compares
the matches with your reference picture (Gemini) — it never overrides a failed rule.

What changed vs the Oct-2026 version
------------------------------------
NEW
  * 20 rule types, every number a range: RSI · RSI vs its average · price vs EMA / SMA (above / below / near,
    with a distance band) · MA alignment · fresh MA crossover · prior run-up · price change · PULLBACK on low
    volume (2–4 candles, up candles allowed, volume ratio, depth) · consecutive low-volume candles · volume
    spike · ATR % · today's move · distance from N-day high · N-day breakout · tight range · NR-N · inside bar
    · gap · relative strength vs NIFTY · MACD · ADX.
  * Plain English -> rules with Gemini (or a built-in parser when no key), then edit any number by hand.
  * NEAR MATCH list with the missed rule and the actual value.
  * TEST ON A STOCK: see every rule's pass / fail for any symbol ("why didn't RELIANCE show up?").
  * Fast: NSE data is stored once per day; a full-NSE rescan with different rules takes seconds.
    Stage 1 downloads 5 days for the universe and applies price band + liquidity; stage 2 only needs
    1-year history for the survivors (kept and topped up daily, refetched automatically after a split).
  * Universe: All NSE (EQ) · NIFTY 500 · your watchlist · Arka watchlist · your portfolio; minimum average
    daily turnover (₹ crore) removes illiquid names.
  * Edit setups (not just create / delete), duplicate them, per-user ownership (members can't delete the
    desk's setups once the `owner` column exists).
FIXED
  * "Let AI suggest filters" no longer wipes the name, picture and description (the form cleared itself).
  * AI result cards rendered matches / mismatches outside the card (HTML split across calls).
  * Tabs built both pages and loaded every setup + reference image from Supabase twice on every click.
  * Every scan re-downloaded ~2,000 stocks; failed NSE universe fetches were cached for 24 h.
  * Stale tickers in the liquid list (ZOMATO -> ETERNAL, TATAMOTORS -> TMPV).
  * "Upload your watchlist in the Scanner tab" (Scanner was removed).
  * Market hours: today's unfinished candle made volume look low — volume is now projected to a full day.
    Empty last rows from Yahoo are dropped.
  * The pullback rule demanded that every candle closed lower; you now choose how many up candles are allowed.
  * "+" bypassed user_data (duplicates, ₹ shown as Rs). Pullback was computed twice per stock.
  * ATR uses Wilder smoothing (same as TradingView, like RSI).

Supabase: the existing `setups` table works. Run once (SETUPS -> Storage) to enable rule ranges + ownership:
  alter table setups add column if not exists rule_config jsonb default '{}'::jsonb,
                     add column if not exists owner text default 'admin';
Old setups are converted to the new rules automatically when opened.
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
import pickle
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

try:
    from data_engine import _parse_download
except Exception:  # pragma: no cover
    _parse_download = None
try:
    from data_engine import rsi_wilder
except Exception:  # pragma: no cover
    rsi_wilder = None
try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GEMINI = True
except Exception:  # pragma: no cover
    HAS_GEMINI = False

IST = timezone(timedelta(hours=5, minutes=30))
AMBER, GREEN, RED, CYAN, PURPLE, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
DARK, DARK2, BORDER, IVORY, T2, T3 = "#000000", "#0A0A0A", "#262626", "#E8E8E8", "#8A8A8A", "#5A5A5A"
MONO = "'JetBrains Mono',monospace"
GEMINI_MODEL = "gemini-2.5-flash"
CACHE_DIR = Path(".cache")
STORE_FILE = CACHE_DIR / "scan_store.pkl"
BATCH = 150

SETUP_SQL = """-- Arka scanner · run once in Supabase → SQL Editor
create table if not exists setups (
  id bigint generated always as identity primary key,
  name text not null,
  price_min double precision default 0, price_max double precision default 99999,
  rsi_min double precision default 0, rsi_max double precision default 100,
  volume_multiplier double precision default 0,
  atr_pct_min double precision default 0, atr_pct_max double precision default 0,
  move_pct_min double precision default 0, move_pct_max double precision default 0,
  rule_config jsonb default '{}'::jsonb, visual_rules text, reference_image_b64 text,
  owner text default 'admin', created_at timestamptz default now());
alter table setups add column if not exists rule_config jsonb default '{}'::jsonb;
alter table setups add column if not exists owner text default 'admin';"""

LIQUID = list(dict.fromkeys([
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT",
    "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO",
    "NESTLEIND", "ADANIENT", "ADANIPORTS", "POWERGRID", "NTPC", "M&M", "TMPV", "TATASTEEL", "JSWSTEEL", "BAJAJFINSV",
    "ONGC", "COALINDIA", "INDUSINDBK", "GRASIM", "HDFCLIFE", "SBILIFE", "DRREDDY", "CIPLA", "EICHERMOT", "BRITANNIA",
    "DIVISLAB", "APOLLOHOSP", "HEROMOTOCO", "BPCL", "TECHM", "UPL", "HINDALCO", "TATACONSUM", "BAJAJ-AUTO", "SHREECEM",
    "VEDL", "GODREJCP", "DABUR", "PIDILITIND", "SIEMENS", "AMBUJACEM", "BANKBARODA", "CANBK", "PNB", "IDFCFIRSTB",
    "FEDERALBNK", "AUBANK", "BANDHANBNK", "CHOLAFIN", "LICHSGFIN", "MUTHOOTFIN", "PFC", "RECLTD", "IRFC", "IEX",
    "ETERNAL", "NYKAA", "PAYTM", "POLICYBZR", "DMART", "TRENT", "JUBLFOOD", "PERSISTENT", "COFORGE", "LTIM", "MPHASIS",
    "OFSS", "LTTS", "DLF", "GODREJPROP", "OBEROIRLTY", "PHOENIXLTD", "PGHH", "COLPAL", "MARICO", "EMAMILTD", "VBL",
    "TVSMOTOR", "ASHOKLEY", "BALKRISIND", "MOTHERSON", "BOSCHLTD", "BSE", "INDIGO", "MAXHEALTH", "JIOFIN", "BEL", "HAL",
    "SHRIRAMFIN", "DIXON", "POLYCAB", "CDSL", "ANGELONE", "MCX", "KAYNES", "SUZLON", "IREDA", "RVNL", "TATAPOWER"]))

PRICE_PRESETS = {"Setup's own range": None, "₹20 – 100": (20, 100), "₹100 – 250": (100, 250), "₹250 – 500": (250, 500),
                 "₹500 – 1,000": (500, 1000), "₹1,000 – 2,000": (1000, 2000), "₹2,000 – 5,000": (2000, 5000),
                 "Any price": (0, 999999), "Custom": "custom"}


# ═════════════════════════ rule catalogue ═════════════════════════
# field: (name, label, kind, default, a, b)   kind f=float(min,max) i=int(min,max) s=select(options) b=bool
MA_KINDS = ["EMA", "SMA"]
RULES = {
    "pullback": {"label": "Pullback on low volume", "fields": [
        ("nmin", "Min candles", "i", 2, 1, 10), ("nmax", "Max candles", "i", 4, 1, 10),
        ("max_up", "Up candles allowed", "i", 0, 0, 5), ("vol", "Avg volume ≤ × 20-day avg", "f", 0.8, 0.1, 2.0),
        ("each", "Every candle below avg volume", "b", False, None, None),
        ("dmin", "Depth min %", "f", 0.0, 0.0, 50.0), ("dmax", "Depth max % (0 = any)", "f", 0.0, 0.0, 60.0)]},
    "lowvol": {"label": "Consecutive low-volume candles", "fields": [
        ("nmin", "Min candles", "i", 3, 1, 15), ("nmax", "Max candles (0 = any)", "i", 0, 0, 30),
        ("x", "Volume below × 20-day avg", "f", 1.0, 0.1, 2.0)]},
    "runup": {"label": "Prior run-up (momentum)", "fields": [
        ("days", "Look-back candles", "i", 30, 5, 250), ("lo", "Rise at least %", "f", 20.0, 0.0, 1000.0),
        ("hi", "Rise at most % (0 = any)", "f", 0.0, 0.0, 5000.0)]},
    "rsi": {"label": "RSI in range", "fields": [
        ("lo", "Min", "f", 50.0, 0.0, 100.0), ("hi", "Max", "f", 100.0, 0.0, 100.0), ("n", "Period", "i", 14, 2, 50)]},
    "rsi_avg": {"label": "RSI vs its average", "fields": [
        ("dir", "RSI is", "s", "above", ["above", "below"], None), ("n", "Average length", "i", 14, 2, 50)]},
    "ma_pos": {"label": "Price vs moving average", "fields": [
        ("kind", "Type", "s", "EMA", MA_KINDS, None), ("len", "Length", "i", 20, 2, 300),
        ("dir", "Price is", "s", "above", ["above", "below", "near"], None),
        ("dmin", "Distance min %", "f", 0.0, 0.0, 100.0), ("dmax", "Distance max % (0 = any)", "f", 0.0, 0.0, 200.0)]},
    "ma_stack": {"label": "Moving-average alignment", "fields": [
        ("k1", "Fast type", "s", "EMA", MA_KINDS, None), ("n1", "Fast length", "i", 20, 2, 300),
        ("k2", "Slow type", "s", "EMA", MA_KINDS, None), ("n2", "Slow length", "i", 50, 2, 300)]},
    "ma_cross": {"label": "Fresh MA crossover", "fields": [
        ("k1", "Fast type", "s", "EMA", MA_KINDS, None), ("n1", "Fast length", "i", 20, 2, 300),
        ("k2", "Slow type", "s", "EMA", MA_KINDS, None), ("n2", "Slow length", "i", 50, 2, 300),
        ("within", "Crossed within candles", "i", 5, 1, 60)]},
    "vol_spike": {"label": "Volume vs 20-day average", "fields": [
        ("lo", "At least ×", "f", 1.5, 0.0, 50.0), ("hi", "At most × (0 = any)", "f", 0.0, 0.0, 100.0)]},
    "atr": {"label": "ATR % (typical daily range)", "fields": [
        ("lo", "Min %", "f", 1.0, 0.0, 50.0), ("hi", "Max % (0 = any)", "f", 0.0, 0.0, 50.0), ("n", "Period", "i", 14, 2, 50)]},
    "day_move": {"label": "Today's move %", "fields": [
        ("mode", "Measure", "s", "size", ["size", "up", "down"], None),
        ("lo", "Min %", "f", 2.0, 0.0, 50.0), ("hi", "Max % (0 = any)", "f", 0.0, 0.0, 50.0)]},
    "change": {"label": "Price change over N candles", "fields": [
        ("days", "Candles", "i", 5, 1, 250), ("lo", "Min %", "f", 0.0, -100.0, 1000.0), ("hi", "Max %", "f", 100.0, -100.0, 5000.0)]},
    "near_high": {"label": "Distance below N-day high", "fields": [
        ("days", "High of last candles", "i", 252, 5, 260), ("lo", "Min % below", "f", 0.0, 0.0, 100.0),
        ("hi", "Max % below", "f", 15.0, 0.0, 100.0)]},
    "breakout": {"label": "Breakout above N-day high", "fields": [("days", "Previous candles", "i", 20, 3, 260)]},
    "tight": {"label": "Tight range (consolidation)", "fields": [
        ("n", "Last candles", "i", 5, 2, 60), ("max", "High–low range at most %", "f", 6.0, 0.5, 50.0)]},
    "nr": {"label": "Narrow-range candle (NR-N)", "fields": [("n", "Narrowest of last", "i", 7, 3, 30)]},
    "inside": {"label": "Inside bar (today inside yesterday)", "fields": []},
    "gap": {"label": "Gap at today's open %", "fields": [("lo", "Min %", "f", 1.0, -50.0, 50.0), ("hi", "Max %", "f", 20.0, -50.0, 50.0)]},
    "rs": {"label": "Relative strength vs NIFTY 50", "fields": [
        ("days", "Over candles", "i", 63, 5, 250), ("lo", "Beats NIFTY by at least % pts", "f", 0.0, -100.0, 500.0)]},
    "macd": {"label": "MACD (12, 26, 9)", "fields": [("mode", "Condition", "s", "above signal", ["above signal", "above zero", "histogram rising"], None)]},
    "adx": {"label": "ADX trend strength", "fields": [
        ("lo", "Min", "f", 20.0, 0.0, 100.0), ("hi", "Max (0 = any)", "f", 0.0, 0.0, 100.0), ("n", "Period", "i", 14, 2, 50)]},
}
RULE_ORDER = ["pullback", "lowvol", "runup", "rsi", "rsi_avg", "ma_pos", "ma_stack", "ma_cross", "vol_spike", "atr",
              "day_move", "change", "near_high", "breakout", "tight", "nr", "inside", "gap", "rs", "macd", "adx"]


def new_rule(rtype, **vals):
    r = {"type": rtype, "on": True, "_id": uuid.uuid4().hex[:8]}
    for name, _, kind, default, a, b in RULES[rtype]["fields"]:
        r[name] = default
    for k, v in vals.items():
        if k in r:
            r[k] = v
    return clean_rule(r)


def clean_rule(r):
    """Validate / coerce one rule dict against the catalogue (used for AI output and saved JSON)."""
    t = r.get("type")
    if t not in RULES:
        return None
    out = {"type": t, "on": bool(r.get("on", True)), "_id": r.get("_id") or uuid.uuid4().hex[:8]}
    for name, _, kind, default, a, b in RULES[t]["fields"]:
        v = r.get(name, default)
        try:
            if kind == "i":
                v = int(round(float(v)))
                v = max(a, min(b, v))
            elif kind == "f":
                v = float(v)
                v = max(a, min(b, v)) if math.isfinite(v) else default
            elif kind == "s":
                v = str(v)
                v = v if v in a else ({"signed": "size", "abs": "size", "above_signal": "above signal",
                                       "above_zero": "above zero", "hist_rising": "histogram rising"}.get(v) or default)
                v = v if v in a else default
            elif kind == "b":
                v = bool(v)
        except (TypeError, ValueError):
            v = default
        out[name] = v
    if t in ("pullback", "lowvol") and out["nmax"] and out["nmax"] < out["nmin"]:
        out["nmax"] = out["nmin"]
    if t in ("rsi", "change", "gap") and out["hi"] < out["lo"]:
        out["lo"], out["hi"] = out["hi"], out["lo"]
    return out


def _r(lo, hi, unit="", zero_any=True):
    if zero_any and not hi:
        return f"≥ {lo:g}{unit}"
    if not lo and zero_any:
        return f"≤ {hi:g}{unit}"
    return f"{lo:g}–{hi:g}{unit}"


def describe(r):
    t = r["type"]
    if t == "pullback":
        n = f'{r["nmin"]}–{r["nmax"]}' if r["nmax"] != r["nmin"] else f'{r["nmin"]}'
        s = f'Pullback of {n} candles on avg volume ≤ {r["vol"]:g}× the 20-day average'
        if r["max_up"]:
            s += f', up to {r["max_up"]} up candle{"s" if r["max_up"] > 1 else ""} allowed'
        if r["each"]:
            s += ", every candle below average volume"
        if r["dmin"] or r["dmax"]:
            s += f', depth {_r(r["dmin"], r["dmax"], "%")}'
        return s
    if t == "lowvol":
        n = f'at least {r["nmin"]}' if not r["nmax"] else (f'{r["nmin"]}–{r["nmax"]}' if r["nmax"] != r["nmin"] else f'{r["nmin"]}')
        return f'{n} consecutive candles with volume below {r["x"]:g}× the 20-day average'
    if t == "runup":
        return f'Rose {_r(r["lo"], r["hi"], "%")} from a low to a later high in the last {r["days"]} candles'
    if t == "rsi":
        return f'RSI({r["n"]}) between {r["lo"]:g} and {r["hi"]:g}'
    if t == "rsi_avg":
        return f'RSI {r["dir"]} its {r["n"]}-period average'
    if t == "ma_pos":
        ma = f'{r["kind"]} {r["len"]}'
        if r["dir"] == "near":
            return f'Price within {r["dmax"] or 2:g}% of {ma} (either side)'
        band = f' by {_r(r["dmin"], r["dmax"], "%")}' if (r["dmin"] or r["dmax"]) else ""
        return f'Price {r["dir"]} {ma}{band}'
    if t == "ma_stack":
        return f'{r["k1"]} {r["n1"]} above {r["k2"]} {r["n2"]}'
    if t == "ma_cross":
        return f'{r["k1"]} {r["n1"]} crossed above {r["k2"]} {r["n2"]} in the last {r["within"]} candles'
    if t == "vol_spike":
        return f'Today\'s volume {_r(r["lo"], r["hi"], "×")} the 20-day average'
    if t == "atr":
        return f'ATR({r["n"]}) {_r(r["lo"], r["hi"], "%")} of price'
    if t == "day_move":
        what = {"size": "Today's move (up or down)", "up": "Today's gain", "down": "Today's fall"}[r["mode"]]
        return f'{what} {_r(r["lo"], r["hi"], "%")}'
    if t == "change":
        return f'Price change over {r["days"]} candles between {r["lo"]:g}% and {r["hi"]:g}%'
    if t == "near_high":
        return f'{r["lo"]:g}–{r["hi"]:g}% below the {r["days"]}-candle high'
    if t == "breakout":
        return f'Close above the highest high of the previous {r["days"]} candles'
    if t == "tight":
        return f'Last {r["n"]} candles inside a {r["max"]:g}% range'
    if t == "nr":
        return f'Today\'s range is the narrowest of the last {r["n"]} candles (NR{r["n"]})'
    if t == "inside":
        return "Inside bar: today's high / low inside yesterday's"
    if t == "gap":
        return f'Opened {r["lo"]:g}% to {r["hi"]:g}% away from yesterday\'s close'
    if t == "rs":
        return f'Beats NIFTY 50 by ≥ {r["lo"]:g} % points over {r["days"]} candles'
    if t == "macd":
        return f'MACD {r["mode"]}'
    if t == "adx":
        return f'ADX({r["n"]}) {_r(r["lo"], r["hi"])}'
    return t


# ═════════════════════════ indicators (per-stock context) ═════════════════════════
def _rma(x: pd.Series, n):
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


class Ctx:
    """One stock's daily candles + memoised indicators. Today's volume is projected to a full day in market hours."""

    def __init__(self, sym, df, live_frac=None, nifty=None):
        df = df[pd.to_numeric(df["Close"], errors="coerce") > 0].copy()
        for c in ("Open", "High", "Low", "Close", "Volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["Volume"] = df["Volume"].fillna(0.0)
        df[["Open", "High", "Low"]] = df[["Open", "High", "Low"]].apply(lambda s: s.fillna(df["Close"]))
        self.sym, self.df, self.nifty = sym, df, nifty
        self.c, self.o, self.h, self.l = df["Close"], df["Open"], df["High"], df["Low"]
        v = df["Volume"].astype(float).copy()
        self.projected = False
        if live_frac is not None and len(v):
            v.iloc[-1] = v.iloc[-1] / max(0.15, min(1.0, live_frac))
            self.projected = True
        self.v = v
        self.memo = {}

    def ma(self, kind, n):
        k = ("ma", kind, n)
        if k not in self.memo:
            self.memo[k] = (self.c.ewm(span=n, adjust=False, min_periods=n).mean() if kind == "EMA"
                            else self.c.rolling(n).mean())
        return self.memo[k]

    def rsi(self, n=14):
        k = ("rsi", n)
        if k not in self.memo:
            if rsi_wilder is not None:
                self.memo[k] = rsi_wilder(self.c, n)
            else:
                d = self.c.diff()
                up, dn = _rma(d.clip(lower=0), n), _rma(-d.clip(upper=0), n)
                self.memo[k] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        return self.memo[k]

    def avg_vol(self):
        """20-day average volume ending YESTERDAY (so today's spike or dry-up is measured against it)."""
        k = "avgv"
        if k not in self.memo:
            self.memo[k] = self.v.rolling(20).mean().shift(1)
        return self.memo[k]

    def atr(self, n=14):
        k = ("atr", n)
        if k not in self.memo:
            pc = self.c.shift(1)
            tr = pd.concat([self.h - self.l, (self.h - pc).abs(), (self.l - pc).abs()], axis=1).max(axis=1)
            self.memo[k] = _rma(tr, n)
        return self.memo[k]

    def turnover_cr(self):
        """Average daily traded value over the last 20 completed candles, ₹ crore."""
        tv = (self.c * self.df["Volume"]).iloc[-21:-1] if len(self.c) > 21 else (self.c * self.df["Volume"])
        return float(tv.mean() / 1e7) if len(tv) else 0.0


def _last(s):
    try:
        v = float(s.iloc[-1])
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _find_pullback(ctx, r):
    c, h, l, v = (x.to_numpy(dtype=float) for x in (ctx.c, ctx.h, ctx.l, ctx.v))
    N = len(c)
    for n in range(int(r["nmax"]), int(r["nmin"]) - 1, -1):
        piv = N - n - 1
        if piv < 21:
            continue
        seg = slice(N - n, N)
        if h[piv] < h[seg].max():                       # the pullback must start from the recent high …
            continue
        if h[piv] < h[max(0, piv - max(5, int(r["nmax"]))):piv].max():
            continue                                    # … and that candle must be a swing high (else a longer pullback slips in)
        ups = int((np.diff(c[piv:N]) > 0).sum())
        if ups > r["max_up"] or c[-1] >= c[piv]:
            continue
        base = v[piv - 19:piv + 1].mean()               # 20 candles up to and including the high
        if base <= 0:
            continue
        sv = v[seg]
        ratio = sv.mean() / base
        if ratio > r["vol"] or (r["each"] and (sv >= base).any()):
            continue
        depth = (h[piv] - l[seg].min()) / h[piv] * 100
        if depth < r["dmin"] or (r["dmax"] and depth > r["dmax"]):
            continue
        return {"n": n, "ratio": ratio, "depth": depth, "ups": ups}
    return None


def evaluate_rule(ctx, r):
    """-> (ok: bool, chip: str, detail: str, metrics: dict). ok is False when data is insufficient."""
    t = r["type"]
    c = ctx.c
    cur = _last(c)
    try:
        if t == "pullback":
            p = _find_pullback(ctx, r)
            if p:
                return True, f'PB {p["n"]}c · {p["ratio"]:.2f}×', f'{p["n"]}-candle pullback, {p["depth"]:.1f}% deep, volume {p["ratio"]:.2f}× average', \
                    {"pb_n": p["n"], "pb_vol": p["ratio"], "pb_depth": p["depth"]}
            # diagnostics for the near-match list: measure from the most recent swing high
            hh = ctx.h.iloc[-15:].to_numpy()
            k = len(hh) - 1 - int(np.argmax(hh))
            if k == 0:
                why = "no pullback yet — today is the high"
            else:
                piv = len(c) - k - 1
                base = float(ctx.v.iloc[max(0, piv - 19):piv + 1].mean())
                vr = float(ctx.v.iloc[-k:].mean()) / base if base else float("nan")
                ups = int((c.iloc[-k - 1:].diff().dropna() > 0).sum())
                why = f"high was {k} candle{'s' if k != 1 else ''} ago, {ups} up candle{'s' if ups != 1 else ''}, volume {vr:.2f}× avg"
            return False, "PB ✗", f'needs a {r["nmin"]}–{r["nmax"]} candle pullback on ≤ {r["vol"]:g}× volume ({why})', {}
        if t == "lowvol":
            av = ctx.avg_vol()
            below = (ctx.v < r["x"] * av).to_numpy()[::-1]
            k = 0
            for b in below:
                if not b:
                    break
                k += 1
            ok = k >= r["nmin"] and (not r["nmax"] or k <= r["nmax"])
            return ok, f"LOWVOL {k}c", f'{k} low-volume candles in a row (needs {r["nmin"]}{"–" + str(r["nmax"]) if r["nmax"] else "+"})', {"lowvol": k}
        if t == "runup":
            w = c.iloc[-int(r["days"]) - 1:-1]
            if len(w) < 5:
                return False, "RUN ?", "not enough history", {}
            arr = w.to_numpy()
            i = int(np.argmin(arr))
            rise = (arr[i:].max() / arr[i] - 1) * 100 if arr[i] > 0 else 0.0
            ok = rise >= r["lo"] and (not r["hi"] or rise <= r["hi"])
            return ok, f"RUN {rise:+.0f}%", f'rose {rise:.1f}% in {r["days"]} candles (needs {_r(r["lo"], r["hi"], "%")})', {"runup": rise}
        if t == "rsi":
            v = _last(ctx.rsi(r["n"]))
            if v is None:
                return False, "RSI ?", "not enough history", {}
            return r["lo"] <= v <= r["hi"], f"RSI {v:.0f}", f'RSI {v:.1f} (needs {r["lo"]:g}–{r["hi"]:g})', {"rsi": v}
        if t == "rsi_avg":
            rs = ctx.rsi(14)
            a = _last(rs.rolling(r["n"]).mean())
            v = _last(rs)
            if v is None or a is None:
                return False, "RSI/AVG ?", "not enough history", {}
            ok = v > a if r["dir"] == "above" else v < a
            return ok, f"RSI {'>' if v > a else '<'} AVG", f'RSI {v:.1f} vs its {r["n"]}-avg {a:.1f} (needs {r["dir"]})', {}
        if t == "ma_pos":
            m = _last(ctx.ma(r["kind"], r["len"]))
            if m is None or cur is None:
                return False, f'{r["kind"]}{r["len"]} ?', f'not enough history for {r["kind"]} {r["len"]}', {}
            dist = (cur / m - 1) * 100
            name = f'{r["kind"]}{r["len"]}'
            if r["dir"] == "near":
                lim = r["dmax"] or 2.0
                ok = r["dmin"] <= abs(dist) <= lim
            else:
                ok = (dist > 0) if r["dir"] == "above" else (dist < 0)
                ok = ok and abs(dist) >= r["dmin"] and (not r["dmax"] or abs(dist) <= r["dmax"])
            return ok, f"{name} {dist:+.1f}%", f'price {dist:+.2f}% vs {r["kind"]} {r["len"]} ({describe(r).lower()})', {"ma_dist": dist}
        if t in ("ma_stack", "ma_cross"):
            f, s = ctx.ma(r["k1"], r["n1"]), ctx.ma(r["k2"], r["n2"])
            a, b = _last(f), _last(s)
            if a is None or b is None:
                return False, "MA ?", "not enough history", {}
            if t == "ma_stack":
                return a > b, f'{r["k1"]}{r["n1"]}{">" if a > b else "<"}{r["k2"]}{r["n2"]}', f'{r["k1"]} {r["n1"]} {a:,.1f} vs {r["k2"]} {r["n2"]} {b:,.1f}', {}
            d = (f - s).dropna()
            w = d.iloc[-int(r["within"]) - 1:]
            crossed = bool(((w.shift(1) <= 0) & (w > 0)).any()) and d.iloc[-1] > 0
            return crossed, "X-OVER ✓" if crossed else "X-OVER ✗", f'{"crossed above" if crossed else "no cross above"} in last {r["within"]} candles', {}
        if t == "vol_spike":
            av = _last(ctx.avg_vol())
            if not av:
                return False, "VOL ?", "no volume history", {}
            x = float(ctx.v.iloc[-1]) / av
            ok = x >= r["lo"] and (not r["hi"] or x <= r["hi"])
            return ok, f"VOL {x:.1f}×", f'volume {x:.2f}× the 20-day average{" (projected)" if ctx.projected else ""}', {"vol_x": x}
        if t == "atr":
            a = _last(ctx.atr(r["n"]))
            if a is None or not cur:
                return False, "ATR ?", "not enough history", {}
            p = a / cur * 100
            ok = p >= r["lo"] and (not r["hi"] or p <= r["hi"])
            return ok, f"ATR {p:.1f}%", f'ATR {p:.2f}% of price (needs {_r(r["lo"], r["hi"], "%")})', {"atr": p}
        if t == "day_move":
            ch = (cur / float(c.iloc[-2]) - 1) * 100
            val = abs(ch) if r["mode"] == "size" else (ch if r["mode"] == "up" else -ch)
            ok = val >= r["lo"] and (not r["hi"] or val <= r["hi"])
            return ok, f"DAY {ch:+.1f}%", f'today {ch:+.2f}% (needs {describe(r).lower()})', {}
        if t == "change":
            if len(c) <= r["days"]:
                return False, "CHG ?", "not enough history", {}
            ch = (cur / float(c.iloc[-1 - int(r["days"])]) - 1) * 100
            return r["lo"] <= ch <= r["hi"], f'{r["days"]}D {ch:+.0f}%', f'{ch:+.1f}% over {r["days"]} candles (needs {r["lo"]:g}–{r["hi"]:g}%)', {"change": ch}
        if t == "near_high":
            hh = float(ctx.h.iloc[-int(r["days"]):].max())
            d = (1 - cur / hh) * 100 if hh else 0.0
            return r["lo"] <= d <= r["hi"], f"{d:.1f}% < HIGH", f'{d:.1f}% below the {r["days"]}-candle high {hh:,.2f}', {"off_high": d}
        if t == "breakout":
            prior = ctx.h.iloc[-int(r["days"]) - 1:-1]
            if len(prior) < r["days"]:
                return False, "BO ?", "not enough history", {}
            lvl = float(prior.max())
            return cur > lvl, f'BO {"✓" if cur > lvl else "✗"} {lvl:,.0f}', f'close {cur:,.2f} vs {r["days"]}-candle high {lvl:,.2f}', {}
        if t == "tight":
            w = int(r["n"])
            rng = (float(ctx.h.iloc[-w:].max()) - float(ctx.l.iloc[-w:].min())) / cur * 100
            return rng <= r["max"], f"RANGE {rng:.1f}%", f'last {w} candles span {rng:.1f}% (needs ≤ {r["max"]:g}%)', {"tight": rng}
        if t == "nr":
            rg = (ctx.h - ctx.l).iloc[-int(r["n"]):]
            ok = float(rg.iloc[-1]) <= float(rg.min()) + 1e-9
            return ok, f'NR{r["n"]} {"✓" if ok else "✗"}', f'today\'s range {"is" if ok else "is not"} the narrowest of {r["n"]}', {}
        if t == "inside":
            ok = float(ctx.h.iloc[-1]) <= float(ctx.h.iloc[-2]) and float(ctx.l.iloc[-1]) >= float(ctx.l.iloc[-2])
            return ok, f'INSIDE {"✓" if ok else "✗"}', "inside bar" if ok else "not an inside bar", {}
        if t == "gap":
            g = (float(ctx.o.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            return r["lo"] <= g <= r["hi"], f"GAP {g:+.1f}%", f'opened {g:+.2f}% vs yesterday (needs {r["lo"]:g}–{r["hi"]:g}%)', {}
        if t == "rs":
            if ctx.nifty is None or len(c) <= r["days"]:
                return False, "RS ?", "NIFTY data unavailable", {}
            d0, d1 = c.index[-1 - int(r["days"])], c.index[-1]
            n = ctx.nifty
            n0, n1 = n[n.index <= d0], n[n.index <= d1]
            if not len(n0) or not len(n1):
                return False, "RS ?", "NIFTY data unavailable", {}
            sr = (cur / float(c.loc[d0]) - 1) * 100
            nr = (float(n1.iloc[-1]) / float(n0.iloc[-1]) - 1) * 100
            ex = sr - nr
            return ex >= r["lo"], f"RS {ex:+.0f}", f'stock {sr:+.1f}% vs NIFTY {nr:+.1f}% over {r["days"]} candles', {"rs": ex}
        if t == "macd":
            m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
            sg = m.ewm(span=9, adjust=False).mean()
            hist = m - sg
            if r["mode"] == "above signal":
                ok = float(m.iloc[-1]) > float(sg.iloc[-1])
            elif r["mode"] == "above zero":
                ok = float(m.iloc[-1]) > 0
            else:
                ok = float(hist.iloc[-1]) > float(hist.iloc[-2])
            return ok, f'MACD {"✓" if ok else "✗"}', f'MACD {float(m.iloc[-1]):.2f}, signal {float(sg.iloc[-1]):.2f}', {}
        if t == "adx":
            n = int(r["n"])
            up, dn = ctx.h.diff(), -ctx.l.diff()
            pdm = up.where((up > dn) & (up > 0), 0.0)
            mdm = dn.where((dn > up) & (dn > 0), 0.0)
            tr = ctx.atr(n)
            pdi = 100 * _rma(pdm, n) / tr
            mdi = 100 * _rma(mdm, n) / tr
            dx = (100 * (pdi - mdi).abs() / (pdi + mdi)).replace([np.inf, -np.inf], np.nan)
            a = _last(_rma(dx.fillna(0), n))
            if a is None:
                return False, "ADX ?", "not enough history", {}
            ok = a >= r["lo"] and (not r["hi"] or a <= r["hi"])
            return ok, f"ADX {a:.0f}", f'ADX {a:.1f} (needs {_r(r["lo"], r["hi"])})', {"adx": a}
    except Exception as e:  # pragma: no cover - a bad candle never crashes the scan
        return False, f"{t.upper()} ?", f"could not compute ({type(e).__name__})", {}
    return False, t, "unknown rule", {}


def evaluate_stock(sym, df, rules, live_frac=None, nifty=None):
    """All enabled rules on one stock -> result dict (or None when there is too little data)."""
    if df is None or len(df) < 30:
        return None
    ctx = Ctx(sym, df, live_frac, nifty)
    if len(ctx.c) < 30:
        return None
    cur, prev = float(ctx.c.iloc[-1]), float(ctx.c.iloc[-2])
    checks, metrics = [], {}
    for r in rules:
        if not r.get("on", True):
            continue
        ok, chip, detail, m = evaluate_rule(ctx, r)
        checks.append({"ok": bool(ok), "chip": chip, "detail": detail, "label": RULES[r["type"]]["label"], "desc": describe(r)})
        metrics.update(m)
    av = _last(ctx.avg_vol())
    return {"symbol": sym, "close": cur, "chg": (cur / prev - 1) * 100 if prev else 0.0, "turnover": ctx.turnover_cr(),
            "vol_x": float(ctx.v.iloc[-1]) / av if av else None, "rsi": _last(ctx.rsi(14)), "checks": checks,
            "fails": [ck for ck in checks if not ck["ok"]], "m": metrics, "date": ctx.c.index[-1], "projected": ctx.projected,
            "chart": ctx.df.tail(120)[["Open", "High", "Low", "Close", "Volume"]]}


# ═════════════════════════ legacy setups -> rules ═════════════════════════
def legacy_rules(setup):
    """Old setups (numeric columns + rule_config v1) -> list of v2 rules, same behaviour."""
    rules = []
    cfg = setup.get("rule_config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    if not cfg:
        cfg = _legacy_cfg_from_text(setup.get("visual_rules") or "")
    rmin, rmax = float(setup.get("rsi_min") or 0), float(setup.get("rsi_max") or 100)
    if cfg.get("require_rsi_above_50"):
        rmin = max(rmin, 50.0)
    if rmin > 0 or rmax < 100:
        rules.append(new_rule("rsi", lo=rmin, hi=rmax or 100))
    if cfg.get("require_momentum_30"):
        rules.append(new_rule("runup", days=30, lo=float(cfg.get("momentum_30_min", 20))))
    if cfg.get("require_rsi_above_sma14"):
        rules.append(new_rule("rsi_avg", dir="above", n=14))
    if cfg.get("require_sma50"):
        rules.append(new_rule("ma_pos", kind="SMA", len=50, dir="above"))
    if cfg.get("require_pullback"):
        rules.append(new_rule("pullback", nmin=int(cfg.get("pullback_min_candles", 2)), nmax=int(cfg.get("pullback_max_candles", 4)),
                              vol=float(cfg.get("volume_dryup_ratio_max", 0.8)), max_up=0))
    if float(setup.get("volume_multiplier") or 0) > 0:
        rules.append(new_rule("vol_spike", lo=float(setup["volume_multiplier"])))
    if float(setup.get("atr_pct_max") or 0) > 0:
        rules.append(new_rule("atr", lo=float(setup.get("atr_pct_min") or 0), hi=float(setup["atr_pct_max"])))
    if float(setup.get("move_pct_max") or 0) > 0:
        rules.append(new_rule("day_move", mode="size", lo=float(setup.get("move_pct_min") or 0), hi=float(setup["move_pct_max"])))
    return rules


def _legacy_cfg_from_text(d):
    d = d.lower()
    cfg = {}
    m = re.search(r'(?:more than|over|above|greater than|at least|>)\s*(\d+(?:\.\d+)?)\s*%', d)
    if m and any(k in d for k in ("momentum", "previous 30", "last 30", "30 days", "30d")):
        cfg.update(require_momentum_30=True, momentum_30_min=float(m.group(1)))
    if "rsi" in d and any(k in d for k in ("above 50", "> 50", ">50")):
        cfg["require_rsi_above_50"] = True
    if "rsi" in d and re.search(r"(?:above|>)\s*(?:its\s*)?(?:rsi\s*)?(?:14\s*)?sma\s*14|above (?:its )?14[- ](?:period )?sma", d):
        cfg["require_rsi_above_sma14"] = True
    if re.search(r"above (?:the |its )?(?:50[- ]?(?:day )?(?:ma|sma)|sma ?50)", d):
        cfg["require_sma50"] = True
    mm = re.search(r'(\d+)\s*(?:to|-|–)\s*(\d+)\s*candles?', d)
    if mm and "pullback" in d:
        cfg.update(require_pullback=True, pullback_min_candles=int(mm.group(1)), pullback_max_candles=max(int(mm.group(2)), int(mm.group(1))))
    return cfg


def setup_rules(setup):
    cfg = setup.get("rule_config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = None
    if isinstance(cfg, dict) and cfg.get("version") == 2:
        return [x for x in (clean_rule(r) for r in cfg.get("rules", [])) if x]
    return legacy_rules(setup)


# ═════════════════════════ plain English -> rules ═════════════════════════
_AI_PROMPT = """You turn a trader's plain-English technical setup into scanner rules. Reply with ONLY JSON:
{{"price_min": number or null, "price_max": number or null, "rules": [ ... ]}}

Use ONLY these rule objects (fields exactly as shown; numbers are examples):
{{"type":"pullback","nmin":2,"nmax":4,"max_up":0,"vol":0.8,"each":false,"dmin":0,"dmax":0}}   last nmin–nmax candles pull back from the recent high; average volume of those candles ≤ vol × 20-day average; max_up = up candles tolerated; each = every candle below average volume; dmin/dmax = pullback depth % (0 = any)
{{"type":"lowvol","nmin":3,"nmax":0,"x":1.0}}   at least nmin (to nmax, 0 = no max) consecutive latest candles with volume below x × 20-day average
{{"type":"runup","days":30,"lo":20,"hi":0}}   rose at least lo % from a low to a later high in the last `days` candles (prior momentum)
{{"type":"rsi","lo":50,"hi":100,"n":14}}
{{"type":"rsi_avg","dir":"above","n":14}}   RSI above / below its own moving average
{{"type":"ma_pos","kind":"EMA","len":20,"dir":"above","dmin":0,"dmax":0}}   dir above / below / near; dmax = max distance % (0 = any); near = within dmax % either side
{{"type":"ma_stack","k1":"EMA","n1":20,"k2":"EMA","n2":50}}   first MA above second
{{"type":"ma_cross","k1":"EMA","n1":20,"k2":"EMA","n2":50,"within":5}}
{{"type":"vol_spike","lo":1.5,"hi":0}}   today's volume ≥ lo × 20-day average
{{"type":"atr","lo":1,"hi":4,"n":14}}   typical daily range % of price
{{"type":"day_move","mode":"size","lo":2,"hi":3}}   mode size (up or down) / up / down, today's % move
{{"type":"change","days":5,"lo":-5,"hi":10}}
{{"type":"near_high","days":252,"lo":0,"hi":15}}   % below the N-candle high
{{"type":"breakout","days":20}}
{{"type":"tight","n":5,"max":6}}
{{"type":"nr","n":7}}
{{"type":"inside"}}
{{"type":"gap","lo":1,"hi":10}}
{{"type":"rs","days":63,"lo":0}}   beats NIFTY 50 by lo % points
{{"type":"macd","mode":"above signal"}}   or "above zero" / "histogram rising"
{{"type":"adx","lo":20,"hi":0,"n":14}}

Rules:
- Flexible numbers become ranges: "3 low-volume candles, 4 is also fine" -> nmin 3, nmax 4.
- A pullback "with volume dry-up / decreasing volume" -> pullback rule (vol 0.8 unless stated).
- "low volume candles" without a pullback -> lowvol.
- Only include what the trader describes. Never invent rules or prices. price_min/max null when not given.

Trader's setup:
\"\"\"{text}\"\"\"
"""


def parse_with_ai(text, key):
    if not HAS_GEMINI or not key or not text.strip():
        return None
    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=_AI_PROMPT.format(text=text),
                                              config=genai_types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
        data = json.loads(resp.text)
        rules = [x for x in (clean_rule(dict(r)) for r in data.get("rules", []) if isinstance(r, dict)) if x]
        pm = lambda v: float(v) if isinstance(v, (int, float)) and v >= 0 else None
        return {"price_min": pm(data.get("price_min")), "price_max": pm(data.get("price_max")), "rules": rules}
    except Exception as e:
        st.warning(f"AI could not read the description ({type(e).__name__}). The built-in parser was used instead.")
        return None


def parse_locally(text):
    """No-AI fallback: understands the common phrasings of RSI / MA / pullback / volume / momentum setups."""
    d = " " + text.lower().replace("–", "-").replace("—", "-") + " "
    rules, out = [], {"price_min": None, "price_max": None}
    m = re.search(r"(?:rs\.?|₹|price)\s*(\d+(?:\.\d+)?)\s*(?:-|to)\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)", d)
    if m:
        out["price_min"], out["price_max"] = float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:gain(?:ed)?|rose|up|rall(?:y|ied)|momentum|run[- ]?up)[^.]{0,40}?(\d+(?:\.\d+)?)\s*%[^.]{0,30}?(\d+)\s*(?:days|d\b|candles|sessions)", d) \
        or re.search(r"(\d+(?:\.\d+)?)\s*%[^.]{0,40}?(?:in|over|within)\s*(?:the\s*)?(?:last|previous|past)?\s*(\d+)\s*(?:days|candles|sessions)", d)
    if m:
        rules.append(new_rule("runup", lo=float(m.group(1)), days=int(m.group(2))))
    m = re.search(r"rsi[^.]{0,20}?between\s*(\d+)\s*(?:and|-|to)\s*(\d+)", d)
    if m:
        rules.append(new_rule("rsi", lo=float(m.group(1)), hi=float(m.group(2))))
    else:
        m = re.search(r"rsi[^.]{0,15}?(?:above|>|over)\s*(\d+)", d)
        if m:
            rules.append(new_rule("rsi", lo=float(m.group(1)), hi=100.0))
    if re.search(r"rsi[^.]{0,40}?(?:above|>)\s*(?:its\s*)?(?:\d+\s*)?(?:sma|ma|average)", d):
        rules.append(new_rule("rsi_avg", dir="above", n=14))
    for m in re.finditer(r"\b(ema|sma|dma|ma)\s*-?\s*(\d{1,3})\b|\b(\d{1,3})\s*-?\s*(?:day\s*)?(ema|sma|dma|ma)\b", d):
        before = re.split(r"[.;\n]", d[max(0, m.start() - 40):m.start()])[-1]      # same sentence only
        if "rsi" in before or not any(w in before for w in ("above", "over", "below", "under", "near", "close to", "and", ",")):
            continue                                   # "RSI above its 14 SMA" is the RSI-average rule, not a price MA
        kind = m.group(1) or m.group(4)
        n = int(m.group(2) or m.group(3))
        dir_ = "below" if re.search(r"(below|under)[^.]*$", before) else "near" if re.search(r"(near|close to)[^.]*$", before) else "above"
        if not any(r["type"] == "ma_pos" and r["len"] == n for r in rules):
            rules.append(new_rule("ma_pos", kind="EMA" if kind == "ema" else "SMA", len=n, dir=dir_, dmax=2.0 if dir_ == "near" else 0.0))
    m = re.search(r"(\d+)\s*(?:-|to)\s*(\d+)\s*(?:low[- ]volume\s*)?candles?", d)
    m2 = re.search(r"(\d+)\s*(?:low[- ]volume\s*)?candles?[^.]{0,40}?(\d+)\s*(?:low[- ]volume\s*)?(?:candles?\s*)?(?:is\s*|are\s*)?(?:also\s*)?(?:fine|ok|okay|acceptable|allowed)", d)
    lo_hi = (int(m.group(1)), int(m.group(2))) if m else ((min(int(m2.group(1)), int(m2.group(2))), max(int(m2.group(1)), int(m2.group(2)))) if m2 else None)
    if "pullback" in d or "pull back" in d or "retrace" in d:
        a, b = lo_hi or (2, 4)
        vol = 0.8 if any(k in d for k in ("dry", "low volume", "low-volume", "decreas", "declin")) else 1.0
        rules.append(new_rule("pullback", nmin=a, nmax=b, vol=vol, max_up=1 if "mostly" in d else 0))
    elif lo_hi and "volume" in d:
        rules.append(new_rule("lowvol", nmin=lo_hi[0], nmax=lo_hi[1], x=1.0))
    m = re.search(r"volume[^.]{0,25}?(\d+(?:\.\d+)?)\s*(?:x|times)", d)
    if m:
        rules.append(new_rule("vol_spike", lo=float(m.group(1))))
    if re.search(r"inside\s*(?:bar|candle)", d):
        rules.append(new_rule("inside"))
    m = re.search(r"\bnr\s*(\d+)", d)
    if m:
        rules.append(new_rule("nr", n=int(m.group(1))))
    m = re.search(r"(\d+)[- ]?(?:day|candle)?\s*(?:high\s*)?breakout|breakout\s*(?:above|of)\s*(?:the\s*)?(\d+)", d)
    if m:
        rules.append(new_rule("breakout", days=int(m.group(1) or m.group(2))))
    out["rules"] = rules
    return out


# ═════════════════════════ universe ═════════════════════════
def _nse_get(url, timeout=6):
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                      "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"})
    if "nseindia.com" in url:
        try:
            s.get("https://www.nseindia.com", timeout=timeout)
        except Exception:
            pass
    r = s.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


_UNIV_FAIL: dict = {}


def _symbols_from_csv_text(text, col_names=("SYMBOL", "Symbol", "symbol")):
    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    if "SERIES" in df.columns:
        df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
    col = next((c for c in col_names if c in df.columns), df.columns[0])
    return [s for s in df[col].astype(str).str.strip().str.upper().dropna().unique().tolist() if s and s.isascii() and s != "NAN"]


def _list_from_sources(name, files, urls, min_n):
    """Local file -> last good copy on disk -> live download (failures cool down 30 min, never cached)."""
    for f in files:
        p = Path(f)
        if p.exists():
            try:
                syms = _symbols_from_csv_text(p.read_text(encoding="utf-8-sig", errors="replace"))
                if len(syms) >= min_n:
                    return syms, f"{p.name} in your repo"
            except Exception:
                pass
    disk = CACHE_DIR / f"universe_{name}.json"
    fresh = None
    try:
        d = json.loads(disk.read_text())
        if time.time() - d["at"] < 7 * 86400:
            fresh = d
    except Exception:
        d = None
    if fresh and time.time() - fresh["at"] < 86400:
        return fresh["syms"], f'NSE list saved {datetime.fromtimestamp(fresh["at"], IST):%d %b}'
    if time.time() > _UNIV_FAIL.get(name, 0):
        for u in urls:
            try:
                syms = _symbols_from_csv_text(_nse_get(u))
                if len(syms) >= min_n:
                    try:
                        CACHE_DIR.mkdir(exist_ok=True)
                        disk.write_text(json.dumps({"at": time.time(), "syms": syms}))
                    except Exception:
                        pass
                    return syms, "NSE live list"
            except Exception:
                continue
        _UNIV_FAIL[name] = time.time() + 1800
    if d:
        return d["syms"], f'NSE list saved {datetime.fromtimestamp(d["at"], IST):%d %b}'
    return None, None


def get_universe(kind):
    """-> (symbols, source text)"""
    if kind == "All NSE stocks":
        syms, src = _list_from_sources("all", ["data/EQUITY_L.csv", "nse_universe.csv", "data/nse_universe.csv"],
                                       ["https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                                        "https://archives.nseindia.com/content/equities/EQUITY_L.csv"], 1000)
        if syms:
            return syms, f"{len(syms)} NSE equities · {src}"
        merged = []
        for f in ("data/ind_nifty500list.csv", "data/ind_niftymicrocap250_list.csv", "data/ind_niftysmallcap250list.csv"):
            try:
                merged += _symbols_from_csv_text(Path(f).read_text(encoding="utf-8-sig"))
            except Exception:
                pass
        merged = list(dict.fromkeys(merged))
        if len(merged) >= 300:
            return merged, f"{len(merged)} stocks · index lists in data/ (NSE equity list unreachable — add data/EQUITY_L.csv for every stock)"
        return LIQUID, f"{len(LIQUID)} liquid stocks · NSE list unreachable — add data/EQUITY_L.csv to the repo for the full market"
    if kind == "NIFTY 500":
        syms, src = _list_from_sources("n500", ["data/ind_nifty500list.csv"],
                                       ["https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
                                        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
                                        "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"], 400)
        if syms:
            return syms, f"{len(syms)} stocks · {src}"
        return LIQUID, f"{len(LIQUID)} liquid stocks · NIFTY 500 list unreachable — add data/ind_nifty500list.csv"
    if kind == "Your watchlist":
        s = list(dict.fromkeys(st.session_state.get("watchlist", []) or []))
        return s, f"{len(s)} stocks · your watchlist"
    if kind == "Arka watchlist":
        s = list(dict.fromkeys(st.session_state.get("admin_watchlist", []) or []))
        return s, f"{len(s)} stocks · Arka watchlist"
    if kind == "Your portfolio":
        s = sorted({str(l.get("symbol", "")).upper() for l in (st.session_state.get("pf_lots") or []) if l.get("symbol")})
        return s, f"{len(s)} stocks · your portfolio"
    return LIQUID, f"{len(LIQUID)} liquid stocks"


# ═════════════════════════ price data store ═════════════════════════
@st.cache_resource(show_spinner=False)
def _store():
    s = {"hist": {}, "snap": {}, "snap_key": {}, "lock": threading.Lock(), "nifty": None, "nifty_at": 0.0}
    try:
        with open(STORE_FILE, "rb") as fh:
            d = pickle.load(fh)
        if isinstance(d, dict) and isinstance(d.get("hist"), dict):
            s["hist"] = d["hist"]
    except Exception:
        pass
    return s


def _save_store(s):
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        with open(STORE_FILE, "wb") as fh:
            pickle.dump({"hist": s["hist"]}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass


def _market_open(now=None):
    now = now or datetime.now(IST)
    t = now.hour * 60 + now.minute
    try:
        from home_page import is_trading_day
        trading = is_trading_day(now.date())
    except Exception:
        trading = now.weekday() < 5
    return trading and 9 * 60 + 15 <= t <= 15 * 60 + 30


def _live_fraction(last_date):
    now = datetime.now(IST)
    if not _market_open(now) or pd.Timestamp(last_date).date() != now.date():
        return None
    return ((now.hour * 60 + now.minute) - (9 * 60 + 15)) / 375.0


def _data_key():
    """Snapshot freshness: 10-minute slots while NSE is open, otherwise once per session close."""
    now = datetime.now(IST)
    if _market_open(now):
        return f"L{now:%Y%m%d%H}{now.minute // 10}"
    t = now.hour * 60 + now.minute
    return f"C{now:%Y%m%d}{'post' if t > 15 * 60 + 30 else 'pre'}"


def _norm_frame(df):
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep]
    df = df[pd.to_numeric(df["Close"], errors="coerce") > 0]
    return df


def _parse(raw, tickers):
    if _parse_download is not None:
        return _parse_download(raw, tickers)
    out = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            if t in raw.columns.get_level_values(0):
                out[t] = raw[t].dropna(how="all")
    elif len(tickers) == 1:
        out[tickers[0]] = raw.dropna(how="all")
    return out


def _download(symbols, period, progress=None, label=""):
    """Batched yf.download -> {SYM: frame}. A failed batch is retried once in halves."""
    import yfinance as yf
    out = {}
    batches = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]

    def get(batch):
        tick = [f"{s}.NS" for s in batch]
        raw = yf.download(tick, period=period, interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)
        parsed = _parse(raw, tick)
        return {s: _norm_frame(parsed[f"{s}.NS"]) for s in batch if f"{s}.NS" in parsed}
    for i, b in enumerate(batches):
        try:
            got = get(b)
        except Exception:
            got = {}
        if len(got) < len(b) * 0.5 and len(b) > 20:          # throttled / partial: retry the misses in halves
            miss = [s for s in b if s not in got]
            for half in (miss[:len(miss) // 2], miss[len(miss) // 2:]):
                time.sleep(0.8)
                try:
                    got.update(get(half))
                except Exception:
                    pass
        out.update({k: v for k, v in got.items() if len(v)})
        if progress:
            progress((i + 1) / len(batches), f"{label} {min((i + 1) * BATCH, len(symbols))}/{len(symbols)}")
    return out


def get_snapshots(symbols, progress=None):
    """Last ~5 candles for each symbol (fresh per _data_key)."""
    s, key = _store(), _data_key()
    need = [x for x in symbols if s["snap_key"].get(x) != key]
    if need:
        got = _download(need, "5d", progress, "Prices")
        with s["lock"]:
            for sym in need:
                if sym in got:
                    s["snap"][sym] = got[sym]
                    s["snap_key"][sym] = key
    return {x: s["snap"].get(x) for x in symbols if s["snap"].get(x) is not None}


def get_histories(symbols, snaps, progress=None):
    """~1 year of candles per symbol: stored history topped up with today's snapshot; refetched when missing,
    gapped, or split-adjusted differently (a split / bonus changes Yahoo's past prices)."""
    s = _store()
    need = []
    for x in symbols:
        h, sn = s["hist"].get(x), snaps.get(x)
        if h is None or len(h) < 60:
            need.append(x)
            continue
        if sn is None or not len(sn):
            continue
        if h.index[-1] < sn.index[0]:
            need.append(x)                 # a gap: history older than the 5-day snapshot
            continue
        common = h.index.intersection(sn.index[:-1])
        if len(common):
            ratio = (h.loc[common, "Close"] / sn.loc[common, "Close"]).dropna()
            if len(ratio) and (ratio - 1).abs().max() > 0.03:
                need.append(x)             # split / bonus
    if need:
        got = _download(need, "1y", progress, "History")
        with s["lock"]:
            s["hist"].update(got)
    out = {}
    with s["lock"]:
        for x in symbols:
            h, sn = s["hist"].get(x), snaps.get(x)
            if h is None:
                continue
            if sn is not None and len(sn) and h.index[-1] >= sn.index[0] - pd.Timedelta(days=10):
                h = pd.concat([h[h.index < sn.index[0]], sn])
                h = h[~h.index.duplicated(keep="last")].tail(300)
                s["hist"][x] = h
            out[x] = h
    if need:
        _save_store(s)
    return out


def nifty_close():
    s = _store()
    if s["nifty"] is not None and time.time() - s["nifty_at"] < 3600:
        return s["nifty"]
    try:
        import yfinance as yf
        df = yf.download("^NSEI", period="2y", interval="1d", progress=False, auto_adjust=False, threads=False)
        c = df["Close"]
        if isinstance(c, pd.DataFrame):
            c = c.iloc[:, 0]
        c = pd.to_numeric(c, errors="coerce").dropna()
        idx = pd.to_datetime(c.index)
        c.index = (idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx).normalize()
        s["nifty"], s["nifty_at"] = c, time.time()
    except Exception:
        pass
    return s["nifty"]


# ═════════════════════════ the scan ═════════════════════════
def run_scan(universe, rules, pmin, pmax, min_turnover, want_near=True, progress=None):
    t0 = time.time()
    stats = {"universe": len(universe)}
    prog = progress or (lambda p, m: None)
    snaps = get_snapshots(universe, lambda p, m: prog(0.05 + 0.40 * p, m))
    stats["priced"] = len(snaps)
    band = []
    for sym, sn in snaps.items():
        c = pd.to_numeric(sn["Close"], errors="coerce").dropna()
        if not len(c):
            continue
        last = float(c.iloc[-1])
        if not (pmin <= last <= pmax):
            continue
        tv = (sn["Close"] * sn["Volume"]).iloc[:-1] if len(sn) > 1 else sn["Close"] * sn["Volume"]
        if min_turnover > 0 and float(tv.mean()) / 1e7 < min_turnover * 0.5:      # lenient pre-filter; exact check below
            continue
        band.append(sym)
    stats["in_band"] = len(band)
    hist = get_histories(band, snaps, lambda p, m: prog(0.45 + 0.40 * p, m))
    needs_nifty = any(r["type"] == "rs" and r.get("on", True) for r in rules)
    nifty = nifty_close() if needs_nifty else None
    match, near, liquid, no_data = [], [], 0, []
    on = [r for r in rules if r.get("on", True)]
    for i, sym in enumerate(band):
        df = hist.get(sym)
        if df is None or len(df) < 30:
            no_data.append(sym)
            continue
        res = evaluate_stock(sym, df, on, _live_fraction(df.index[-1]), nifty)
        if res is None:
            no_data.append(sym)
            continue
        if res["turnover"] < min_turnover:
            continue
        liquid += 1
        nf = len(res["fails"])
        if nf == 0:
            match.append(res)
        elif nf == 1 and want_near and len(on) >= 2:
            near.append(res)
        if i % 100 == 0:
            prog(0.85 + 0.15 * (i + 1) / max(1, len(band)), f"Checking rules · {i + 1}/{len(band)}")
    stats.update(liquid=liquid, no_data=len(no_data), match=len(match), near=len(near), secs=time.time() - t0)
    asof = max((r["date"] for r in match + near), default=None)
    return {"match": match, "near": near, "stats": stats, "no_data": no_data[:200], "asof": asof,
            "live": any(r["projected"] for r in match + near), "at": datetime.now(IST)}


# ═════════════════════════ setups in Supabase ═════════════════════════
_COLS = "id,name,price_min,price_max,rsi_min,rsi_max,volume_multiplier,atr_pct_min,atr_pct_max,move_pct_min,move_pct_max,rule_config,visual_rules,created_at"


def load_setups(supabase, force=False):
    memo = st.session_state.get("_ss_setups")
    if memo and not force and time.time() - memo[0] < 600:
        return memo[1]
    rows, has_owner, err = [], False, None
    if supabase is not None:
        for cols in (_COLS + ",owner", _COLS, "*"):
            try:
                rows = supabase.table("setups").select(cols).order("created_at", desc=True).execute().data or []
                has_owner = cols.endswith(",owner") or bool(rows and "owner" in rows[0])
                err = None
                break
            except Exception as e:
                err = e
        if err is not None:
            st.error(f"Could not load setups: {err}")
        for r in rows:
            r.pop("reference_image_b64", None)          # images are fetched one at a time, only when shown
    else:
        has_owner = True
        rows = st.session_state.setdefault("_ss_local_setups", [])
    st.session_state["_ss_setups"] = (time.time(), rows)
    st.session_state["_ss_has_owner"] = bool(has_owner)
    return rows


def setup_image(supabase, sid):
    cache = st.session_state.setdefault("_ss_img", {})
    if sid in cache:
        return cache[sid]
    b64 = None
    if supabase is not None:
        try:
            d = supabase.table("setups").select("reference_image_b64").eq("id", sid).execute().data or []
            b64 = (d[0] or {}).get("reference_image_b64") if d else None
        except Exception:
            b64 = None
    else:
        b64 = next((s.get("reference_image_b64") for s in st.session_state.get("_ss_local_setups", []) if s.get("id") == sid), None)
    cache[sid] = b64
    return b64


def _role(is_admin):
    return "admin" if is_admin else "member"


def can_edit(setup, is_admin):
    if is_admin:
        return True
    if not st.session_state.get("_ss_has_owner"):
        return False
    return (setup.get("owner") or "admin") == "member"


def save_setup(supabase, draft, rules, img_b64, is_admin):
    row = {"name": draft["name"].strip(), "price_min": float(draft["pmin"]), "price_max": float(draft["pmax"]),
           "rsi_min": 0.0, "rsi_max": 100.0, "volume_multiplier": 0.0, "atr_pct_min": 0.0, "atr_pct_max": 0.0,
           "move_pct_min": 0.0, "move_pct_max": 0.0, "visual_rules": draft.get("desc", ""),
           "rule_config": {"version": 2, "rules": [{k: v for k, v in r.items() if k != "_id"} for r in rules]}}
    if img_b64:
        row["reference_image_b64"] = img_b64
    sid = draft.get("id")
    if supabase is None:
        rows = st.session_state.setdefault("_ss_local_setups", [])
        if sid:
            for r in rows:
                if r["id"] == sid:
                    r.update(row)
        else:
            sid = max([r["id"] for r in rows] + [0]) + 1
            rows.insert(0, {**row, "id": sid, "owner": _role(is_admin), "created_at": datetime.now(IST).isoformat()})
        st.session_state.get("_ss_img", {}).pop(sid, None)
        return sid, None
    attempts = [dict(row, owner=_role(is_admin)) if not sid else dict(row), dict(row)]
    err = None
    for payload in attempts:
        try:
            if sid:
                supabase.table("setups").update(payload).eq("id", sid).execute()
                res_id = sid
            else:
                res = supabase.table("setups").insert(payload).execute()
                res_id = (res.data or [{}])[0].get("id")
            st.session_state.get("_ss_img", {}).pop(sid, None)
            return res_id, None
        except Exception as e:
            err = str(e)
            if "rule_config" in err:
                return None, "Your `setups` table has no rule_config column — run the SQL in SETUPS → Storage once, then save again."
            continue
    return None, err


def delete_setup(supabase, sid):
    if supabase is None:
        rows = st.session_state.setdefault("_ss_local_setups", [])
        rows[:] = [r for r in rows if r["id"] != sid]
        return None
    try:
        supabase.table("setups").delete().eq("id", sid).execute()
        return None
    except Exception as e:
        return str(e)


# ═════════════════════════ watchlist / navigation ═════════════════════════
def add_to_watchlist(supabase, syms):
    current = list(dict.fromkeys(st.session_state.get("watchlist", []) or []))
    new = [s for s in dict.fromkeys(str(x).upper().strip() for x in syms) if s and s not in current]
    if not new:
        return 0, None
    try:
        import user_data as ud
        if hasattr(ud, "add_symbols") and supabase is not None:
            ud.add_symbols(supabase, new, ud.W_TABLE)
        elif supabase is not None:
            supabase.table("watchlist").insert([{"symbol": s} for s in new]).execute()
    except Exception as e:
        return 0, str(e)
    st.session_state["watchlist"] = current + new
    return len(new), None


def _goto(sym, page):
    sym = str(sym).upper().strip()
    st.session_state["active_security"] = sym
    st.session_state["research_last_query"] = sym
    st.session_state["m1_ticker"] = sym
    st.session_state.pop("research_data", None)
    rec = [s for s in st.session_state.get("recent_symbols", []) if s != sym]
    st.session_state["recent_symbols"] = ([sym] + rec)[:6]
    if page == "analysis":
        st.session_state.update({"ai_mode_v8": "Mode 1 — Setup Analysis", "m1_ticker_v8": sym, "m1_loaded_ticker_v8": sym,
                                 "m1_chart_fetched_v8": False, "arka_autorun_v8": sym})
        st.session_state.pop("m1_result_v8", None)
        st.session_state.pop("m1_annotated_v8", None)
    st.session_state["page"] = page
    st.rerun()


# ═════════════════════════ AI chart check (optional) ═════════════════════════
_AUDIT_PROMPT = """You are a skeptical technical-analysis reviewer. Compare the CANDIDATE chart with the trader's
REFERENCE chart{ref_note}. Trader's description of the setup:
\"\"\"{rules}\"\"\"
The candidate already passed every hard rule; you only judge visual similarity and must not re-score the rules.
Score 0-10: 0-2 no resemblance · 3-5 different structure · 6-7 same broad pattern with real differences · 8-10 the same setup.
Reply with ONLY JSON: {{"score": int, "matches": [max 3 short phrases], "mismatches": [max 3 short phrases], "caption": "one sentence"}}"""


def _chart_png(sym, df, n=60):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    full = df.copy()
    ema20 = full["Close"].ewm(span=20, adjust=False).mean()
    sma50 = full["Close"].rolling(50).mean()
    d = full.tail(n)
    x = np.arange(len(d))
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6, 4.2), dpi=110, gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    fig.patch.set_facecolor(DARK)
    for ax in (a1, a2):
        ax.set_facecolor(DARK)
        ax.tick_params(colors=T2, labelsize=6)
        for sp in ax.spines.values():
            sp.set_color(BORDER)
    up = (d["Close"] >= d["Open"]).to_numpy()
    op, cl, hi, lo = (d[c].to_numpy() for c in ("Open", "Close", "High", "Low"))
    a1.vlines(x, lo, hi, color=T2, linewidth=0.6)
    a1.bar(x[up], (cl - op)[up], bottom=op[up], width=0.6, color=GREEN)
    a1.bar(x[~up], (op - cl)[~up], bottom=cl[~up], width=0.6, color=RED)
    a1.plot(x, ema20.tail(n).to_numpy(), color=CYAN, linewidth=0.9)
    a1.plot(x, sma50.tail(n).to_numpy(), color=AMBER, linewidth=0.9)
    a1.set_title(f"{sym} · {n} candles · EMA20 / SMA50", color=IVORY, fontsize=8, loc="left")
    a2.bar(x, d["Volume"].to_numpy(), width=0.6, color=np.where(up, GREEN, RED), alpha=0.7)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _audit(sym, png, rules_text, ref_b64, key):
    try:
        client = genai.Client(api_key=key)
        parts = [_AUDIT_PROMPT.format(rules=rules_text or "(none)", ref_note="" if ref_b64 else " (no reference picture: judge against the description)")]
        if ref_b64:
            parts.append(genai_types.Part.from_bytes(data=base64.b64decode(ref_b64), mime_type="image/png"))
        parts.append(genai_types.Part.from_bytes(data=png, mime_type="image/png"))
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=parts,
                                              config=genai_types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2))
        txt = resp.text.strip().strip("`")
        txt = txt[4:] if txt.lower().startswith("json") else txt
        d = json.loads(txt)
        sc = d.get("score", 0)
        return {"score": max(0, min(10, int(sc) if isinstance(sc, (int, float)) else 0)), "matches": list(d.get("matches", []))[:3],
                "mismatches": list(d.get("mismatches", []))[:3], "caption": str(d.get("caption", ""))[:240]}
    except Exception as e:
        return {"score": 0, "matches": [], "mismatches": [f"AI call failed: {type(e).__name__}"], "caption": ""}


# ═════════════════════════ rendering helpers ═════════════════════════
_CSS = f"""<style>
.ss-hero{{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;padding:4px 2px 10px;border-bottom:1px solid var(--line,#1f1f1f);margin-bottom:8px;}}
.ss-hero .t{{font:800 13px {MONO};letter-spacing:2px;color:{IVORY};}} .ss-hero .t em{{font-style:normal;color:#000;background:{AMBER};padding:2px 7px;margin-right:10px;border-radius:2px;font-size:10px;}}
.ss-hero .d{{font:11px Inter,sans-serif;color:{T2};margin-top:5px;}}
.ss-card{{background:{DARK2};border:1px solid {BORDER};border-top:2px solid {AMBER};padding:12px 14px;margin:6px 0 10px;}}
.ss-card .n{{font:800 15px Inter,sans-serif;color:{IVORY};}} .ss-card .o{{font:700 8px {MONO};letter-spacing:1px;color:{T3};margin-left:8px;}}
.ss-rules{{margin-top:8px;display:flex;flex-direction:column;gap:3px;}}
.ss-rule{{font:11.5px Inter,sans-serif;color:{T2};}} .ss-rule b{{font:700 9px {MONO};color:{AMBER};margin-right:7px;}}
.ss-kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin:8px 0;}}
.ss-kpi{{background:{DARK2};border:1px solid {BORDER};border-top:2px solid var(--a,#333);padding:9px 11px;}}
.ss-kpi small{{display:block;font:700 7.5px {MONO};letter-spacing:1px;color:{T3};text-transform:uppercase;}} .ss-kpi b{{display:block;font:700 17px {MONO};color:{IVORY};margin-top:4px;}}
.ss-tw{{border:1px solid {BORDER};background:#080808;}}
.ss-t{{width:100%;border-collapse:separate;border-spacing:0;font:11px {MONO};font-variant-numeric:tabular-nums;}}
.ss-t th{{background:#0e0e0e;font:700 8px {MONO};letter-spacing:.8px;color:{T2};text-align:left;padding:7px 8px;border-bottom:1px solid {BORDER};text-transform:uppercase;white-space:nowrap;}}
.ss-t td{{padding:7px 8px;border-bottom:1px solid #151515;color:{IVORY};vertical-align:middle;}}
.ss-t td.r,.ss-t th.r{{text-align:right;white-space:nowrap;}}
.ss-t tr:hover td{{background:rgba(255,159,10,.035);}}
.ss-chip{{display:inline-block;margin:2px 3px 2px 0;padding:1px 6px;border:1px solid;border-radius:2px;font:700 8.5px {MONO};letter-spacing:.3px;white-space:nowrap;}}
.ss-chip.ok{{color:{GREEN};border-color:rgba(48,209,88,.35);background:rgba(48,209,88,.06);}}
.ss-chip.no{{color:{RED};border-color:rgba(255,69,58,.45);background:rgba(255,69,58,.08);}}
.ss-miss{{color:{AMBER};font:600 10.5px Inter,sans-serif;white-space:normal;max-width:300px;}}
.ss-ai{{display:inline-block;padding:1px 7px;border-radius:999px;font:800 9px {MONO};border:1px solid currentColor;}}
.hc-wrap{{position:relative;display:inline-block;font:800 12px {MONO};color:{AMBER};cursor:default;border-bottom:1px dotted {AMBER}88;}}
.hc-pop{{display:none;position:absolute;top:-8px;left:calc(100% + 12px);width:344px;background:{DARK2};border:1px solid {BORDER};border-top:2px solid {AMBER};
  padding:10px;z-index:99999;box-shadow:0 12px 34px rgba(0,0,0,.85);}}
.hc-wrap:hover .hc-pop{{display:block;}}
.hc-pop-h{{display:flex;justify-content:space-between;font:700 9px {MONO};color:{T2};letter-spacing:.5px;margin-bottom:6px;}}
.ss-wl{{color:{GREEN};font:800 10px {MONO};}}
div[data-testid="stMarkdown"]:has(.hc-wrap:hover),div[data-testid="stElementContainer"]:has(.hc-wrap:hover){{position:relative;z-index:9999;overflow:visible !important;}}
div[class*="st-key-ss_rule_"]{{background:{DARK2};border:1px solid {BORDER};border-left:3px solid {AMBER};padding:8px 10px 2px;}}
div[class*="st-key-ss_rule_"].off{{opacity:.5;}}
.ss-rh{{font:700 10px {MONO};letter-spacing:1px;color:{IVORY};padding-top:6px;}} .ss-rh span{{font:11px Inter,sans-serif;color:{T2};letter-spacing:0;margin-left:8px;}}
</style>"""


def _e(s):
    return html.escape("" if s is None else str(s))


def _kpi(label, value, accent="#333"):
    return f'<div class="ss-kpi" style="--a:{accent}"><small>{_e(label)}</small><b>{value}</b></div>'


def _svg_chart(df, width=324, height=176, n=60):
    try:
        full = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    except Exception:
        return ""
    if len(full) < 5:
        return ""
    sma = full["Close"].rolling(50).mean().tail(n).to_numpy()
    ema = full["Close"].ewm(span=20, adjust=False).mean().tail(n).to_numpy()
    d = full.tail(n)
    px, pw = 6, width - 12
    p_top, p_bot, v_top, v_bot = 6, 124, 132, height - 4
    lo, hi = float(d["Low"].min()), float(d["High"].max())
    for arr in (sma, ema):
        ok = arr[~np.isnan(arr)]
        if ok.size:
            lo, hi = min(lo, float(ok.min())), max(hi, float(ok.max()))
    rng = (hi - lo) or 1.0
    vmax = float(d["Volume"].max()) or 1.0
    step = pw / len(d)
    bw = max(2.0, min(6.0, step * 0.62))
    y = lambda p: p_top + (hi - float(p)) / rng * (p_bot - p_top)
    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="{width}" height="{height}" fill="{DARK2}"/>']
    for k in range(1, 4):
        gy = p_top + (p_bot - p_top) * k / 4
        out.append(f'<line x1="0" y1="{gy:.1f}" x2="{width}" y2="{gy:.1f}" stroke="#161616"/>')
    for i, (_, r) in enumerate(d.iterrows()):
        x = px + (i + 0.5) * step
        o, c, h, l = float(r["Open"]), float(r["Close"]), float(r["High"]), float(r["Low"])
        col = GREEN if c >= o else RED
        out.append(f'<line x1="{x:.1f}" y1="{y(h):.1f}" x2="{x:.1f}" y2="{y(l):.1f}" stroke="{col}"/>')
        out.append(f'<rect x="{x - bw / 2:.1f}" y="{min(y(o), y(c)):.1f}" width="{bw:.1f}" height="{max(1.2, abs(y(c) - y(o))):.1f}" fill="{col}"/>')
        vv = float(r["Volume"] or 0) / vmax * (v_bot - v_top)
        out.append(f'<rect x="{x - bw / 2:.1f}" y="{v_bot - vv:.1f}" width="{bw:.1f}" height="{max(1.0, vv):.1f}" fill="{col}" opacity=".55"/>')
    for arr, col in ((ema, CYAN), (sma, AMBER)):
        pts = [f"{px + (i + 0.5) * step:.1f},{y(v):.1f}" for i, v in enumerate(arr) if not np.isnan(v)]
        if len(pts) > 1:
            out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" stroke-width="1.3"/>')
    out.append(f'<line x1="0" y1="{v_top - 4}" x2="{width}" y2="{v_top - 4}" stroke="{BORDER}"/>')
    out.append(f'<text x="{width - 4}" y="14" text-anchor="end" fill="{T2}" font-size="9" font-family="monospace">{hi:,.2f}</text>')
    out.append(f'<text x="{width - 4}" y="{p_bot - 3}" text-anchor="end" fill="{T2}" font-size="9" font-family="monospace">{lo:,.2f}</text></svg>')
    return "".join(out)


def _results_table(rows, near, wl, ai):
    head = ["#", "Stock", "LTP", "Day", "Turnover", "Vol×", "RSI", "Rule checks"] + (["Missed rule"] if near else []) + (["AI"] if ai else []) + [""]
    right = {2, 3, 4, 5, 6}
    th = "".join(f'<th class="{"r" if i in right else ""}">{h}</th>' for i, h in enumerate(head))
    body = []
    for i, r in enumerate(rows, 1):
        cc = GREEN if r["chg"] >= 0 else RED
        pop = (f'<span class="hc-pop"><div class="hc-pop-h"><span>{_e(r["symbol"])} · 60 CANDLES · <span style="color:{CYAN}">EMA20</span> · '
               f'<span style="color:{AMBER}">SMA50</span></span><span>NSE</span></div>{_svg_chart(r["chart"])}</span>')
        chips = "".join(f'<span class="ss-chip {"ok" if ck["ok"] else "no"}" title="{_e(ck["label"] + ": " + ck["detail"])}">'
                        f'{"✓" if ck["ok"] else "✗"} {_e(ck["chip"])}</span>' for ck in r["checks"])
        cells = [f'<td style="color:{T3}">{i}</td>', f'<td><span class="hc-wrap">{_e(r["symbol"])}{pop}</span></td>',
                 f'<td class="r">₹{r["close"]:,.2f}</td>', f'<td class="r" style="color:{cc}">{"▲" if r["chg"] >= 0 else "▼"} {abs(r["chg"]):.2f}%</td>',
                 f'<td class="r">₹{r["turnover"]:,.1f} Cr</td>',
                 f'<td class="r">{"—" if r["vol_x"] is None else format(r["vol_x"], ".2f") + "×"}{"*" if r["projected"] else ""}</td>',
                 f'<td class="r">{"—" if r["rsi"] is None else format(r["rsi"], ".0f")}</td>', f'<td style="white-space:normal">{chips}</td>']
        if near:
            f = r["fails"][0]
            cells.append(f'<td class="ss-miss"><b>{_e(f["label"])}</b><br>{_e(f["detail"])}</td>')
        if ai:
            a = ai.get(r["symbol"])
            if a:
                col = GREEN if a["score"] >= 8 else CYAN if a["score"] >= 6 else T2
                cells.append(f'<td><span class="ss-ai" style="color:{col}" title="{_e(a["caption"])}">{a["score"]}/10</span></td>')
            else:
                cells.append(f'<td style="color:{T3}">—</td>')
        cells.append(f'<td class="ss-wl">{"✓ WL" if r["symbol"] in wl else ""}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="ss-tw"><table class="ss-t"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


SORTS = {"Liquidity (turnover)": (lambda r: -r["turnover"]), "Day % (best first)": (lambda r: -r["chg"]),
         "Lowest pullback volume": (lambda r: r["m"].get("pb_vol", 9)), "Strongest run-up": (lambda r: -r["m"].get("runup", -1e9)),
         "Closest to moving average": (lambda r: abs(r["m"].get("ma_dist", 1e9))), "RSI (high first)": (lambda r: -(r["rsi"] or 0)),
         "Relative strength vs NIFTY": (lambda r: -r["m"].get("rs", -1e9)), "A–Z": (lambda r: r["symbol"])}


# ═════════════════════════ page: SETUPS (builder) ═════════════════════════
def _load_draft(setup=None):
    """Put a setup (or a blank one) into the editor state."""
    for k in [k for k in st.session_state if k.startswith("ssf_")]:
        del st.session_state[k]
    if setup:
        rules = setup_rules(setup)
        d = {"id": setup.get("id"), "name": setup.get("name", ""), "desc": setup.get("visual_rules") or "",
             "pmin": float(setup.get("price_min") or 0), "pmax": float(setup.get("price_max") or 99999), "owner": setup.get("owner")}
    else:
        rules = []
        d = {"id": None, "name": "", "desc": "", "pmin": 0.0, "pmax": 99999.0, "owner": None}
    st.session_state["ss_draft"] = d
    st.session_state["ss_rules"] = rules
    st.session_state["ssd_name"], st.session_state["ssd_desc"] = d["name"], d["desc"]
    st.session_state["ssd_pmin"], st.session_state["ssd_pmax"] = d["pmin"], min(d["pmax"], 999999.0)


def _rule_editor(r, i):
    meta = RULES[r["type"]]
    with st.container(key=f"ss_rule_{r['_id']}"):
        h1, h2, h3, h4, h5 = st.columns([6, 1.1, 0.6, 0.6, 0.6], gap="small")
        h1.markdown(f'<div class="ss-rh">{i + 1}. {_e(meta["label"].upper())}<span>{_e(describe(r))}</span></div>', unsafe_allow_html=True)
        r["on"] = h2.toggle("On", value=r.get("on", True), key=f"ssf_{r['_id']}_on")
        if h3.button("↑", key=f"ssf_{r['_id']}_up", help="Move up", disabled=i == 0):
            st.session_state["ss_rules"].insert(i - 1, st.session_state["ss_rules"].pop(i))
            st.rerun()
        if h4.button("↓", key=f"ssf_{r['_id']}_dn", help="Move down", disabled=i == len(st.session_state["ss_rules"]) - 1):
            st.session_state["ss_rules"].insert(i + 1, st.session_state["ss_rules"].pop(i))
            st.rerun()
        if h5.button("✕", key=f"ssf_{r['_id']}_x", help="Remove this rule"):
            st.session_state["ss_rules"].pop(i)
            st.rerun()
        fields = meta["fields"]
        for start in range(0, len(fields), 4):
            chunk = fields[start:start + 4]
            cols = st.columns(4, gap="small")
            for col, (name, label, kind, default, a, b) in zip(cols, chunk):
                k = f"ssf_{r['_id']}_{name}"
                if k not in st.session_state:
                    st.session_state[k] = r[name]
                if kind == "i":
                    r[name] = int(col.number_input(label, min_value=int(a), max_value=int(b), step=1, key=k))
                elif kind == "f":
                    r[name] = float(col.number_input(label, min_value=float(a), max_value=float(b), step=0.05 if b <= 5 else 0.5, key=k, format="%g"))
                elif kind == "s":
                    r[name] = col.selectbox(label, a, key=k)
                else:
                    r[name] = col.checkbox(label, key=k)
        st.session_state["ss_rules"][i] = clean_rule(r) | {"_id": r["_id"]}


def _view_setups(supabase, is_admin, gemini_key):
    setups = load_setups(supabase)
    opts = ["＋ New setup"] + [f'{s["name"]}  ·  #{s["id"]}' for s in setups]
    pend = st.session_state.pop("_ss_pending_pick", None)          # set by DUPLICATE / SAVE / DELETE on the previous run
    if pend == "new":
        st.session_state["ss_edit_pick"] = opts[0]
    elif pend is not None:
        st.session_state["ss_edit_pick"] = next((o for o, s_ in zip(opts[1:], setups) if s_["id"] == pend), opts[0])
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Edit setup", opts, key="ss_edit_pick", label_visibility="collapsed")
    sel = None if pick == opts[0] else setups[opts.index(pick) - 1]
    sel_id = sel["id"] if sel else None
    if st.session_state.get("ss_loaded_for") != sel_id or "ss_draft" not in st.session_state:
        _load_draft(sel)
        st.session_state["ss_loaded_for"] = sel_id
    d = st.session_state["ss_draft"]
    editable = sel is None or can_edit(sel, is_admin)
    if sel is not None and not editable:
        st.info("This is a desk setup — you can scan with it or DUPLICATE it to make your own editable copy.")
    if c2.button("DUPLICATE", key="ss_dup", use_container_width=True, disabled=sel is None):
        d["id"], d["owner"] = None, None
        st.session_state["ss_draft"] = d
        st.session_state["ssd_name"] = (d["name"] + " (copy)")[:80]
        st.session_state["_ss_pending_pick"] = "new"
        st.session_state["ss_loaded_for"] = None
        st.rerun()

    st.markdown('<div class="ss-hero"><div><div class="t"><em>SETUP</em>DESCRIBE IT · CHECK THE RULES · SAVE</div>'
                '<div class="d">Write how you trade the setup in plain English, press BUILD RULES, then fine-tune any number. '
                'Every number is a range, so "3 low-volume candles — 4 is also fine" becomes 3 to 4.</div></div></div>', unsafe_allow_html=True)
    a, b = st.columns([1.6, 1])
    with a:
        st.text_input("Setup name", key="ssd_name", placeholder="e.g. Momentum pullback on dry volume", disabled=not editable)
        st.text_area("Describe the setup in your own words", key="ssd_desc", height=118, disabled=not editable,
                     placeholder="e.g. Stock gained 20%+ in the last 30 days. RSI above 50 and above its 14 SMA. Price above EMA 20 and SMA 50. "
                                 "Then a pullback of 3 candles on low volume (4 candles is also fine).")
        x1, x2 = st.columns([1.3, 1])
        if x1.button("✦ BUILD RULES FROM DESCRIPTION", key="ss_build", type="primary", use_container_width=True, disabled=not editable):
            text = st.session_state.get("ssd_desc", "")
            if not text.strip():
                st.warning("Write the description first.")
            else:
                with st.spinner("Reading your setup…"):
                    parsed = parse_with_ai(text, gemini_key) or parse_locally(text)
                if parsed["rules"]:
                    st.session_state["ss_rules"] = parsed["rules"]
                    if parsed.get("price_min") is not None:
                        st.session_state["ssd_pmin"] = float(parsed["price_min"])
                    if parsed.get("price_max") is not None:
                        st.session_state["ssd_pmax"] = float(parsed["price_max"])
                    st.toast(f'{len(parsed["rules"])} rules built — check the numbers below.')
                    st.rerun()
                else:
                    st.warning("No rules could be read from that description. Add rules by hand below.")
        x2.caption("Uses Gemini when GEMINI_KEY is set, otherwise the built-in reader. It replaces the rules below.")
    with b:
        p1, p2 = st.columns(2)
        p1.number_input("Price from (₹)", min_value=0.0, max_value=999999.0, step=10.0, key="ssd_pmin", disabled=not editable)
        p2.number_input("Price to (₹)", min_value=0.0, max_value=999999.0, step=10.0, key="ssd_pmax", disabled=not editable)
        img = st.file_uploader("Reference chart (optional, for the AI check)", type=["png", "jpg", "jpeg"], key="ssd_img", disabled=not editable)
        if img is None and sel is not None:
            b64 = setup_image(supabase, sel["id"])
            if b64:
                st.image(base64.b64decode(b64), caption="Saved reference chart", use_container_width=True)
        elif img is not None:
            st.image(img, caption="New reference chart", use_container_width=True)

    st.markdown(f'<div class="ss-hero"><div><div class="t"><em>RULES</em>{len(st.session_state["ss_rules"])} RULES · A STOCK MUST PASS ALL</div>'
                '<div class="d">Turn a rule off to keep it but skip it. Stocks that miss exactly one rule are shown as NEAR MATCHES.</div></div></div>',
                unsafe_allow_html=True)
    if not st.session_state["ss_rules"]:
        st.caption("No rules yet — build them from the description or add them below.")
    for i, r in enumerate(list(st.session_state["ss_rules"])):
        if editable:
            _rule_editor(r, i)
        else:
            st.markdown(f'<div class="ss-rule"><b>{i + 1}</b>{_e(describe(r))}{"" if r.get("on", True) else " (off)"}</div>', unsafe_allow_html=True)
    if editable:
        r1, r2 = st.columns([3, 1])
        add = r1.selectbox("Add a rule", RULE_ORDER, format_func=lambda t: RULES[t]["label"], key="ss_add_type", label_visibility="collapsed")
        if r2.button("＋ ADD RULE", key="ss_add_btn", use_container_width=True):
            st.session_state["ss_rules"].append(new_rule(add))
            st.rerun()

    # test on one stock
    st.markdown('<div class="ss-hero"><div><div class="t"><em>TEST</em>CHECK A STOCK AGAINST THESE RULES</div>'
                '<div class="d">See exactly which rule a stock passes or fails — e.g. why it did not show up in a scan.</div></div></div>', unsafe_allow_html=True)
    t1, t2 = st.columns([3, 1])
    tsym = t1.text_input("Symbol to test", key="ss_test_sym", placeholder="e.g. TATAPOWER", label_visibility="collapsed").strip().upper()
    if t2.button("TEST", key="ss_test_btn", use_container_width=True) and tsym:
        with st.spinner(f"Checking {tsym}…"):
            snaps = get_snapshots([tsym])
            hist = get_histories([tsym], snaps)
            rules = [r for r in st.session_state["ss_rules"] if r.get("on", True)]
            res = evaluate_stock(tsym, hist.get(tsym), rules, _live_fraction(hist[tsym].index[-1]) if tsym in hist else None,
                                 nifty_close() if any(r["type"] == "rs" for r in rules) else None)
        if res is None:
            st.error(f"No price history found for {tsym}. Use the NSE symbol.")
        else:
            ok = not res["fails"]
            verdict = "MATCH" if ok else ("NEAR MATCH" if len(res["fails"]) == 1 else f'{len(res["fails"])} RULES FAILED')
            col = GREEN if ok else AMBER if len(res["fails"]) == 1 else RED
            rows = "".join(f'<tr><td>{"✓" if ck["ok"] else "✗"}</td><td>{_e(ck["desc"])}</td><td style="color:{GREEN if ck["ok"] else RED}">{_e(ck["detail"])}</td></tr>'
                           for ck in res["checks"])
            pmin, pmax = float(st.session_state.get("ssd_pmin", 0)), float(st.session_state.get("ssd_pmax", 99999))
            price_ok = pmin <= res["close"] <= pmax
            st.markdown(f'<div class="ss-card"><span class="n">{_e(tsym)}</span><span class="o" style="color:{col}">{verdict}</span>'
                        f'<span class="o">₹{res["close"]:,.2f} · {res["chg"]:+.2f}% · {"inside" if price_ok else "OUTSIDE"} the ₹{pmin:,.0f}–{pmax:,.0f} band · '
                        f'turnover ₹{res["turnover"]:,.1f} Cr/day</span></div>'
                        f'<div class="ss-tw"><table class="ss-t"><thead><tr><th></th><th>Rule</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>',
                        unsafe_allow_html=True)

    # save
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    s1, s2, s3 = st.columns([1.4, 1, 1])
    if editable and s1.button("SAVE SETUP" if not d.get("id") else "SAVE CHANGES", type="primary", key="ss_save", use_container_width=True):
        name = st.session_state.get("ssd_name", "").strip()
        pmin, pmax = float(st.session_state.get("ssd_pmin", 0)), float(st.session_state.get("ssd_pmax", 99999))
        rules = [r for r in st.session_state["ss_rules"]]
        if not name:
            st.error("Give the setup a name.")
        elif pmin > pmax:
            st.error("'Price from' is higher than 'Price to'.")
        elif not rules:
            st.error("Add at least one rule.")
        else:
            imgf = st.session_state.get("ssd_img")
            b64 = base64.b64encode(imgf.getvalue()).decode() if imgf is not None else None
            d.update(name=name, desc=st.session_state.get("ssd_desc", ""), pmin=pmin, pmax=pmax)
            sid, err = save_setup(supabase, d, rules, b64, is_admin)
            if err:
                st.error(f"Could not save: {err}")
            else:
                load_setups(supabase, force=True)
                st.session_state["ss_sel"] = sid
                st.session_state["_ss_pending_pick"] = sid
                st.session_state["ss_loaded_for"] = "reload"
                st.toast(f"Setup “{name}” saved.")
                st.rerun()
    if sel is not None and editable and s3.button("DELETE SETUP", key="ss_del", use_container_width=True):
        st.session_state["ss_confirm_del"] = sel["id"]
    if st.session_state.get("ss_confirm_del") == sel_id and sel is not None:
        st.warning(f'Delete “{sel["name"]}” permanently?')
        y, n_ = st.columns(2)
        if y.button("YES, DELETE", key="ss_del_yes", type="primary", use_container_width=True):
            err = delete_setup(supabase, sel_id)
            st.session_state.pop("ss_confirm_del", None)
            if err:
                st.error(err)
            else:
                load_setups(supabase, force=True)
                st.session_state["_ss_pending_pick"] = "new"
                st.session_state["ss_loaded_for"] = "reload"
                st.rerun()
        if n_.button("CANCEL", key="ss_del_no", use_container_width=True):
            st.session_state.pop("ss_confirm_del", None)
            st.rerun()

    with st.expander("Storage (Supabase)"):
        if not st.session_state.get("_ss_has_owner"):
            st.caption("Run this once to store rule ranges and who owns each setup (members can then edit their own setups):")
        st.code(SETUP_SQL, language="sql")


# ═════════════════════════ page: RUN SCAN ═════════════════════════
def _view_scan(supabase, is_admin, gemini_key):
    setups = load_setups(supabase)
    if not setups:
        st.info("No setups yet. Open SETUPS, describe your setup, press BUILD RULES and save it.")
        return
    ids = [s["id"] for s in setups]
    cur = st.session_state.get("ss_sel")
    idx = ids.index(cur) if cur in ids else 0
    c1, c2 = st.columns([2.2, 1])
    i = c1.selectbox("Setup", range(len(setups)), index=idx, key="ss_scan_pick",
                     format_func=lambda k: f'{setups[k]["name"]}{"  ·  DESK" if (setups[k].get("owner") or "admin") == "admin" and not is_admin else ""}')
    setup = setups[i]
    st.session_state["ss_sel"] = setup["id"]
    rules = setup_rules(setup)
    if not rules:
        st.warning("This setup has no rules yet — open SETUPS and build them.")
        return
    with c2:
        b64 = setup_image(supabase, setup["id"]) if st.session_state.get("ss_show_ref") else None
        st.toggle("Show reference chart", key="ss_show_ref")
    if b64:
        st.image(base64.b64decode(b64), width=420)

    # rules in this run
    st.markdown(f'<div class="ss-card"><span class="n">{_e(setup["name"])}</span><span class="o">{len(rules)} RULES · '
                f'₹{float(setup.get("price_min") or 0):,.0f}–{float(setup.get("price_max") or 99999):,.0f}</span>'
                f'<div class="ss-rules">' + "".join(f'<div class="ss-rule"><b>{k + 1}</b>{_e(describe(r))}{"" if r.get("on", True) else " · OFF"}</div>'
                                                     for k, r in enumerate(rules)) + "</div></div>", unsafe_allow_html=True)
    with st.expander("Skip rules for this run only"):
        for k, r in enumerate(rules):
            if r.get("on", True):
                r["on"] = st.checkbox(describe(r), value=True, key=f"ss_run_on_{setup['id']}_{k}")

    a, b, c, d = st.columns([1.6, 1.4, 1.1, 1.1])
    preset = a.selectbox("Price band", list(PRICE_PRESETS), key="ss_band")
    if PRICE_PRESETS[preset] is None:
        pmin, pmax = float(setup.get("price_min") or 0), float(setup.get("price_max") or 99999)
    elif PRICE_PRESETS[preset] == "custom":
        q1, q2 = a.columns(2)
        pmin = q1.number_input("From ₹", 0.0, 999999.0, 100.0, 10.0, key="ss_cmin")
        pmax = q2.number_input("To ₹", 0.0, 999999.0, 1000.0, 10.0, key="ss_cmax")
    else:
        pmin, pmax = map(float, PRICE_PRESETS[preset])
    uni = b.selectbox("Search in", ["All NSE stocks", "NIFTY 500", "Your watchlist", "Arka watchlist", "Your portfolio"], key="ss_uni")
    is_list = uni not in ("All NSE stocks", "NIFTY 500")
    liq = c.number_input("Min turnover ₹ Cr/day", 0.0, 500.0, 0.0 if is_list else 1.0, 0.5, key=f"ss_liq_{is_list}",
                         help="Average daily traded value over 20 days. Removes stocks that barely trade.")
    near = d.checkbox("Show near matches", value=True, key="ss_near", help="Stocks that miss exactly one rule")
    st.caption(f"Searching ₹{pmin:,.0f} – ₹{pmax:,.0f}. The first full-market scan of the day downloads prices (about 1–3 minutes); "
               "re-scans with other rules or bands use the stored data and take seconds.")
    if pmin > pmax:
        st.error("The price band is reversed.")
        return
    if st.button(f"▶ RUN SCAN · {setup['name']}", type="primary", use_container_width=True, key="ss_run"):
        universe, src = get_universe(uni)
        if not universe:
            st.warning(f"{uni} is empty. Add stocks in the WATCHLIST workspace first." if is_list else "No stocks to scan.")
            return
        bar, msg = st.progress(0.0), st.empty()

        def prog(p, m):
            bar.progress(max(0.0, min(1.0, float(p))))
            msg.caption(m)
        prog(0.02, f"Universe: {src}")
        res = run_scan(universe, rules, pmin, pmax, float(liq), near, prog)
        res.update(setup_id=setup["id"], setup_name=setup["name"], source=src, band=(pmin, pmax), rules=[describe(r) for r in rules if r.get("on", True)])
        st.session_state["ss_res"] = res
        st.session_state.pop("ss_ai", None)
        bar.empty()
        msg.empty()
        st.rerun()

    res = st.session_state.get("ss_res")
    if not res:
        return
    S = res["stats"]
    st.markdown(f'<div class="ss-hero"><div><div class="t"><em>RESULTS</em>{_e(res["setup_name"]).upper()}</div>'
                f'<div class="d">{_e(res["source"])} · ₹{res["band"][0]:,.0f}–{res["band"][1]:,.0f} · scanned {res["at"]:%d %b %H:%M IST} in {S["secs"]:.0f}s'
                f'{" · prices as of " + format(res["asof"], "%d %b") if res["asof"] is not None else ""}'
                f'{" · * volume projected to a full day (market open)" if res["live"] else ""}</div></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="ss-kpis">' + _kpi("Universe", f'{S["universe"]:,}') + _kpi("Priced", f'{S["priced"]:,}') +
                _kpi("In price band", f'{S["in_band"]:,}') + _kpi("Liquid & checked", f'{S["liquid"]:,}') +
                _kpi("MATCHES", f'<span style="color:{GREEN}">{S["match"]}</span>', GREEN) +
                _kpi("NEAR MATCHES", f'<span style="color:{AMBER}">{S["near"]}</span>', AMBER) + '</div>', unsafe_allow_html=True)
    if S["no_data"]:
        st.caption(f'{S["no_data"]} stocks had too little price history (new listings or suspended) and were skipped.')

    v1, v2, v3 = st.columns([1.6, 1.3, 1])
    tab = v1.radio("Show", [f"MATCHES ({S['match']})", f"NEAR MATCHES ({S['near']})"], horizontal=True, key="ss_tab", label_visibility="collapsed")
    is_near = tab.startswith("NEAR")
    rows = res["near"] if is_near else res["match"]
    sort = v2.selectbox("Sort", list(SORTS), key="ss_sort", label_visibility="collapsed")
    psize = v3.selectbox("Rows", [30, 60, 100], key="ss_psize", label_visibility="collapsed", format_func=lambda n: f"{n} per page")
    rows = sorted(rows, key=SORTS[sort])
    if not rows:
        st.info("No near matches." if is_near else "No stock passes every rule today. Check NEAR MATCHES, widen a range, or skip a rule for this run.")
        return
    pages = max(1, math.ceil(len(rows) / psize))
    page = 1
    if pages > 1:
        page = st.number_input(f"Page (of {pages})", 1, pages, 1, key=f"ss_page_{is_near}_{sort}_{psize}")
    chunk = rows[(page - 1) * psize: page * psize]
    wl = set(st.session_state.get("watchlist", []) or [])
    ai = st.session_state.get("ss_ai")
    st.markdown(_results_table(chunk, is_near, wl, ai if ai and not is_near else None), unsafe_allow_html=True)
    st.caption("Hover a symbol for its chart · hover a chip for the exact value · ✓ WL = already in your watchlist"
               + (" · Vol× * = projected to a full day" if res["live"] else ""))

    # action bar
    with st.container(key="ss_actions"):
        syms = [r["symbol"] for r in rows]
        e1, e2 = st.columns([3, 1.2])
        chosen = e1.multiselect("Add to watchlist", [s for s in syms if s not in wl], key=f"ss_pick_{is_near}",
                                placeholder="Tick stocks to add to your watchlist…", label_visibility="collapsed")
        if e2.button(f"＋ ADD {len(chosen)} TO WATCHLIST" if chosen else "＋ ADD SELECTED", key=f"ss_add_sel_{is_near}",
                     use_container_width=True, type="primary", disabled=not chosen):
            n, err = add_to_watchlist(supabase, chosen)
            st.error(f"Could not save: {err}") if err else st.toast(f"{n} stock(s) added to your watchlist")
            if not err:
                try:
                    del st.session_state[f"ss_pick_{is_near}"]
                except Exception:
                    pass
                st.rerun()
        f1, f2, f3, f4, f5 = st.columns([1.6, 1, 1, 1, 1.3])
        new_all = [s for s in syms if s not in wl]
        if f1.button(f"＋ ADD ALL {len(new_all)} {'NEAR ' if is_near else ''}MATCHES", key=f"ss_add_all_{is_near}",
                     use_container_width=True, disabled=not new_all):
            n, err = add_to_watchlist(supabase, new_all)
            st.error(f"Could not save: {err}") if err else st.toast(f"{n} stock(s) added to your watchlist")
            if not err:
                st.rerun()
        one = f2.selectbox("Stock", syms, key=f"ss_one_{is_near}", label_visibility="collapsed")
        if f3.button("ANALYSIS", key=f"ss_an_{is_near}", use_container_width=True, help="Open in Arka AI and start the analysis"):
            _goto(one, "analysis")
        if f4.button("RESEARCH", key=f"ss_rs_{is_near}", use_container_width=True):
            _goto(one, "research")
        with f5:
            out = pd.DataFrame([{"symbol": r["symbol"], "ltp": round(r["close"], 2), "chg_pct": round(r["chg"], 2),
                                 "turnover_cr": round(r["turnover"], 2), "rsi": None if r["rsi"] is None else round(r["rsi"], 1),
                                 "missed": r["fails"][0]["detail"] if r["fails"] else "",
                                 "checks": " | ".join(("OK " if ck["ok"] else "NO ") + ck["detail"] for ck in r["checks"])} for r in rows])
            fmt = st.selectbox("Export", ["CSV", "TradingView list"], key="ss_exp_fmt", label_visibility="collapsed")
            if fmt == "CSV":
                st.download_button("⤓ DOWNLOAD", out.to_csv(index=False), f"arka_scan_{datetime.now(IST):%Y%m%d}.csv", "text/csv",
                                   key="ss_dl_csv", use_container_width=True)
            else:
                st.download_button("⤓ DOWNLOAD", "\n".join(f"NSE:{s}" for s in syms), f"arka_scan_{datetime.now(IST):%Y%m%d}.txt",
                                   "text/plain", key="ss_dl_tv", use_container_width=True, help="Import into a TradingView watchlist")

    # optional AI chart check (matches only)
    if not is_near:
        with st.expander("AI chart check (optional) — compare matches with your reference picture"):
            if not (HAS_GEMINI and gemini_key):
                st.caption("Add GEMINI_KEY in Streamlit secrets to use this. The rule scan above works without it.")
            else:
                g1, g2 = st.columns([1, 2])
                n = g1.number_input("Check the top", 1, min(40, len(rows)), min(10, len(rows)), 1, key="ss_ai_n")
                g2.caption("Uses one Gemini call per stock, in the current sort order. Results are kept for the day, so a stock is never "
                           "paid for twice. The AI score never overrides a failed rule.")
                if st.button(f"RUN AI CHECK ON {int(n)} STOCKS", key="ss_ai_run"):
                    ref = setup_image(supabase, res["setup_id"])
                    cache = st.session_state.setdefault("_ss_ai_cache", {})
                    todo = [r for r in rows[:int(n)]]
                    out_ai = dict(st.session_state.get("ss_ai") or {})
                    bar = st.progress(0.0)
                    text = "\n".join(res["rules"])

                    pngs = {}
                    for r in todo:                              # charts drawn here: matplotlib is not thread-safe
                        k = (res["setup_id"], r["symbol"], str(r["date"])[:10])
                        if k not in cache:
                            pngs[r["symbol"]] = _chart_png(r["symbol"], r["chart"])

                    def one_(r):
                        k = (res["setup_id"], r["symbol"], str(r["date"])[:10])
                        return r["symbol"], k, cache.get(k) or _audit(r["symbol"], pngs[r["symbol"]], text, ref, gemini_key)
                    with ThreadPoolExecutor(max_workers=4) as ex:
                        futs = [ex.submit(one_, r) for r in todo]
                        for j, f in enumerate(as_completed(futs), 1):
                            sym, k, a_ = f.result()
                            cache[k] = a_
                            out_ai[sym] = a_
                            bar.progress(j / len(todo))
                    st.session_state["ss_ai"] = out_ai
                    st.rerun()
                if ai:
                    for r in sorted([r for r in rows if r["symbol"] in ai], key=lambda r: -ai[r["symbol"]]["score"]):
                        a_ = ai[r["symbol"]]
                        col = GREEN if a_["score"] >= 8 else CYAN if a_["score"] >= 6 else T2
                        st.markdown(f'<div class="ss-card" style="border-top-color:{col}"><span class="n">{_e(r["symbol"])}</span>'
                                    f'<span class="o" style="color:{col}">{a_["score"]}/10</span><div class="ss-rule" style="margin-top:6px">{_e(a_["caption"])}</div>'
                                    + "".join(f'<div class="ss-rule" style="color:{GREEN}">+ {_e(m)}</div>' for m in a_["matches"])
                                    + "".join(f'<div class="ss-rule" style="color:{RED}">− {_e(m)}</div>' for m in a_["mismatches"]) + "</div>",
                                    unsafe_allow_html=True)


# ═════════════════════════ entry point ═════════════════════════
def render_smart_scanner(supabase, is_admin=None, on_open=None):
    """Called by app.py for page == 'smart_scan'."""
    st.markdown(_CSS, unsafe_allow_html=True)
    if is_admin is None:
        is_admin = bool(st.session_state.get("is_admin", False))
    try:
        gemini_key = st.secrets.get("GEMINI_KEY", "")
    except Exception:
        gemini_key = ""
    if st.session_state.get("ss_loaded_for") == "reload":
        st.session_state.pop("ss_loaded_for", None)
        st.session_state.pop("ss_draft", None)
    view = st.radio("Screener view", ["RUN SCAN", "SETUPS"], horizontal=True, key="ss_view", label_visibility="collapsed")
    if view == "RUN SCAN":
        _view_scan(supabase, is_admin, gemini_key)
    else:
        _view_setups(supabase, is_admin, gemini_key)
    st.caption("Technical scanner for research — not investment advice. Prices: Yahoo Finance, delayed.")
