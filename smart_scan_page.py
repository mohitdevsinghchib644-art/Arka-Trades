"""
smart_scan_page.py — Arka Trades · Technical Setup Scanner v4 (Nov 2026)

New in v4 (saved setups open unchanged; their rules now get the two allowances below)
-------------------------------------------------------------------------------------
  * CLOSE ENOUGH COUNTS: every setup has a tolerance (Exact · 5% · 10% · 20%; default 5%). A number that misses by less
    than that still passes, shown as "≈" with the real value: a 17.5% run-up passes "at least 20%" at 15% tolerance,
    RSI 48 passes "above 50" at 5%. Every result gets a FIT score (100 = every rule passed exactly) and is ranked by it.
    Change the tolerance for one run on the Scan page, or save it with the setup.
  * GREEN CANDLES IN A PULLBACK: a pullback candle that closes up by less than X% (default 4%) no longer breaks the pullback;
    bigger up candles still count against "up candles allowed".
  * UNDERSTANDS YOU BETTER: the AI reads flexibility words ("3-4% is ok", "roughly", "4 is also fine") into ranges and the
    tolerance; it explains back every rule in plain words; unclear points become questions you answer in place and the
    rules are rebuilt with your answers. The built-in reader also understands "x% up is ok" and tolerance phrases.
  * Redesigned: same look as the rest of the terminal, sentence-case labels, fit bars, phone layout with result cards.

v3 notes follow.

New in v3 (everything in v2 still works; saved setups open unchanged)
----------------------------------------------------------------------
  * LEARN FROM EXAMPLES (SETUPS): list past cases as "SYMBOL date"; the scanner measures each one and proposes
    rules whose ranges cover all of them. Also shows which of your current rules reject your own examples.
  * BACKTEST (new view): replays a setup on every past day; signals, % that rose, average / median move after
    5 / 10 / 20 sessions against "any stock, any day", worst dip, full signal list + CSV.
  * SCAN A PAST DATE (RUN SCAN) with a "since then" column; TEST ON A STOCK accepts a date.
  * WHICH RULE REMOVES THE MOST STOCKS: per-rule pass counts after every scan.
  * 5 new rule types: candle shape · touched a moving average and held · shrinking ranges (VCP) · volume falling
    candle after candle · delivery % (NSE, needs research_data.py). Breakout can be "3–8 candles ago".
    Every rule can be measured N candles ago, on weekly candles, put in an either-or group, or marked required.
  * Pass at least K of N rules (per setup).
  * AI: build rules from the chart picture; assumptions and unclear points are shown after BUILD RULES;
    the Gemini model is picked from what the key supports. 9 ready-made templates.

v2 notes follow.

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
CANDLES = ["hammer", "bullish engulfing", "bearish engulfing", "doji", "strong close", "weak close", "green candle", "red candle"]
RULES = {
    "pullback": {"label": "Pullback on low volume", "fields": [
        ("nmin", "Min candles", "i", 2, 1, 10), ("nmax", "Max candles", "i", 4, 1, 10),
        ("max_up", "Up candles allowed", "i", 0, 0, 5), ("small_up", "Ignore up candles smaller than %", "f", 4.0, 0.0, 15.0),
        ("vol", "Avg volume ≤ × 20-day avg", "f", 0.8, 0.1, 2.0),
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
    "breakout": {"label": "Breakout above N-day high", "fields": [
        ("days", "Previous candles", "i", 20, 3, 260), ("amin", "Happened at least N candles ago", "i", 0, 0, 60),
        ("amax", "…and at most N candles ago", "i", 0, 0, 60)]},
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
    "candle": {"label": "Candle shape", "fields": [("pat", "Shape", "s", "hammer", CANDLES, None)]},
    "ma_touch": {"label": "Touched a moving average and held", "fields": [
        ("kind", "Type", "s", "EMA", MA_KINDS, None), ("len", "Length", "i", 20, 2, 300),
        ("within", "Touched within last candles", "i", 1, 1, 10), ("tol", "Touch tolerance %", "f", 0.5, 0.0, 5.0)]},
    "contract": {"label": "Shrinking ranges (VCP style)", "fields": [
        ("legs", "Number of legs", "i", 3, 2, 4), ("n", "Candles per leg", "i", 5, 3, 20)]},
    "vol_fall": {"label": "Volume falling candle after candle", "fields": [
        ("n", "Last candles", "i", 3, 2, 10), ("skip", "Exceptions allowed", "i", 0, 0, 3)]},
    "delivery": {"label": "Delivery % (NSE)", "fields": [
        ("n", "Average of last sessions", "i", 5, 1, 20), ("lo", "At least %", "f", 40.0, 0.0, 100.0),
        ("x", "At least × its earlier average (0 = skip)", "f", 0.0, 0.0, 5.0)]},
}
RULE_ORDER = ["pullback", "lowvol", "vol_fall", "runup", "rsi", "rsi_avg", "ma_pos", "ma_touch", "ma_stack", "ma_cross", "vol_spike",
              "delivery", "atr", "day_move", "change", "near_high", "breakout", "tight", "contract", "nr", "inside", "candle", "gap",
              "rs", "macd", "adx"]
# options every rule has: when it is measured, on which candles, either-or groups, always required
COMMON = [("ago", "Measured N candles ago (0 = latest)", "i", 0, 0, 60), ("tf", "Candles", "s", "daily", ["daily", "weekly"], None),
          ("grp", "Either-or group", "s", "—", ["—", "A", "B", "C"], None), ("must", "Always required", "b", False, None, None)]


def all_fields(rtype):
    return RULES[rtype]["fields"] + COMMON


def new_rule(rtype, **vals):
    r = {"type": rtype, "on": True, "_id": uuid.uuid4().hex[:8]}
    for name, _, kind, default, a, b in all_fields(rtype):
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
    for name, _, kind, default, a, b in all_fields(t):
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
    if t == "breakout" and out["amax"] < out["amin"]:
        out["amax"] = out["amin"]
    return out


def _r(lo, hi, unit="", zero_any=True):
    if zero_any and not hi:
        return f"≥ {lo:g}{unit}"
    if not lo and zero_any:
        return f"≤ {hi:g}{unit}"
    return f"{lo:g}–{hi:g}{unit}"


def describe(r):
    s = _describe(r)
    if r.get("tf") == "weekly":
        s += " · on weekly candles"
    if r.get("ago"):
        s += f' · measured {r["ago"]} candle{"s" if r["ago"] != 1 else ""} ago'
    if r.get("grp", "—") != "—":
        s += f' · either-or group {r["grp"]}'
    if r.get("must"):
        s += " · always required"
    return s


def _describe(r):
    t = r["type"]
    if t == "pullback":
        n = f'{r["nmin"]}–{r["nmax"]}' if r["nmax"] != r["nmin"] else f'{r["nmin"]}'
        s = f'Pullback of {n} candles on avg volume ≤ {r["vol"]:g}× the 20-day average'
        if r["max_up"]:
            s += f', up to {r["max_up"]} up candle{"s" if r["max_up"] > 1 else ""} allowed'
        if r.get("small_up"):
            s += f', green candles under {r["small_up"]:g}% are fine'
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
        when = "" if not (r["amin"] or r["amax"]) else (f', {r["amin"]}–{r["amax"]} candles ago' if r["amax"] != r["amin"] else f', {r["amin"]} candles ago')
        return f'Close above the highest high of the previous {r["days"]} candles{when}'
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
    if t == "candle":
        return f'Latest candle is a {r["pat"]}'
    if t == "ma_touch":
        return f'Low touched {r["kind"]} {r["len"]} (within {r["tol"]:g}%) in the last {r["within"]} candle{"s" if r["within"] > 1 else ""} and the close is above it'
    if t == "contract":
        return f'{r["legs"]} legs of {r["n"]} candles, each with a smaller high–low range than the one before'
    if t == "vol_fall":
        return f'Volume lower than the previous candle on each of the last {r["n"]} candles' + (f', {r["skip"]} exception{"s" if r["skip"] > 1 else ""} allowed' if r["skip"] else "")
    if t == "delivery":
        return f'Delivery averages ≥ {r["lo"]:g}% over the last {r["n"]} sessions' + (f' and ≥ {r["x"]:g}× its earlier average' if r["x"] else "")
    return t


# ═════════════════════════ indicators (per-stock context) ═════════════════════════
def _rma(x: pd.Series, n):
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


class Ctx:
    """One stock's candles + memoised indicators. Today's volume is projected to a full day in market hours.

    Every indicator here only looks backwards, so its value on a past candle is the same whether it is computed on the
    full history or on history cut at that candle. `at(end)` therefore returns a cheap view "as of candle end-1" that
    slices the indicators already computed on the full history — this is what makes past-date scans and backtests fast."""

    def __init__(self, sym, df, live_frac=None, nifty=None, extra=None):
        df = df[pd.to_numeric(df["Close"], errors="coerce") > 0].copy()
        for c in ("Open", "High", "Low", "Close", "Volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["Volume"] = df["Volume"].fillna(0.0)
        df[["Open", "High", "Low"]] = df[["Open", "High", "Low"]].apply(lambda s: s.fillna(df["Close"]))
        self.sym, self.df, self.nifty, self.extra = sym, df, nifty, (extra or {})
        self.c, self.o, self.h, self.l = df["Close"], df["Open"], df["High"], df["Low"]
        v = df["Volume"].astype(float).copy()
        self.projected = False
        if live_frac is not None and len(v):
            v.iloc[-1] = v.iloc[-1] / max(0.15, min(1.0, live_frac))
            self.projected = True
        self.v = v
        self.memo, self.root, self.end = {}, self, len(df)

    def at(self, end):
        """View of this stock as of candle number `end` (exclusive)."""
        root = self.root
        end = max(0, min(int(end), root.end))
        if end == root.end:
            return root
        k = ("view", end)
        if k not in root.memo:
            x = Ctx.__new__(Ctx)
            x.sym, x.nifty, x.extra, x.root, x.end, x.memo, x.projected = root.sym, root.nifty, root.extra, root, end, {}, False
            x.df = root.df.iloc[:end]
            x.c, x.o, x.h, x.l, x.v = root.c.iloc[:end], root.o.iloc[:end], root.h.iloc[:end], root.l.iloc[:end], root.v.iloc[:end]
            if len(root.memo) > 4000:
                root.memo = {a: b for a, b in root.memo.items() if a[0] != "view"}
            root.memo[k] = x
        return root.memo[k]

    def _ind(self, key, fn):
        root = self.root
        if key not in root.memo:
            root.memo[key] = fn(root)
        full = root.memo[key]
        return full if self is root else full.iloc[:self.end]

    def ma(self, kind, n):
        return self._ind(("ma", kind, n), lambda r: (r.c.ewm(span=n, adjust=False, min_periods=n).mean() if kind == "EMA" else r.c.rolling(n).mean()))

    def rsi(self, n=14):
        def calc(r):
            if rsi_wilder is not None:
                return rsi_wilder(r.c, n)
            d = r.c.diff()
            up, dn = _rma(d.clip(lower=0), n), _rma(-d.clip(upper=0), n)
            return 100 - 100 / (1 + up / dn.replace(0, np.nan))
        return self._ind(("rsi", n), calc)

    def avg_vol(self):
        """20-day average volume ending YESTERDAY (so today's spike or dry-up is measured against it)."""
        return self._ind(("avgv",), lambda r: r.v.rolling(20).mean().shift(1))

    def atr(self, n=14):
        def calc(r):
            pc = r.c.shift(1)
            tr = pd.concat([r.h - r.l, (r.h - pc).abs(), (r.l - pc).abs()], axis=1).max(axis=1)
            return _rma(tr, n)
        return self._ind(("atr", n), calc)

    def macd(self):
        """-> (macd line, signal line)"""
        m = self._ind(("macd",), lambda r: r.c.ewm(span=12, adjust=False).mean() - r.c.ewm(span=26, adjust=False).mean())
        sg = self._ind(("macds",), lambda r: (r.c.ewm(span=12, adjust=False).mean() - r.c.ewm(span=26, adjust=False).mean()).ewm(span=9, adjust=False).mean())
        return m, sg

    def adx(self, n=14):
        def calc(r):
            up, dn = r.h.diff(), -r.l.diff()
            pdm = up.where((up > dn) & (up > 0), 0.0)
            mdm = dn.where((dn > up) & (dn > 0), 0.0)
            tr = r.atr(n)
            pdi, mdi = 100 * _rma(pdm, n) / tr, 100 * _rma(mdm, n) / tr
            dx = (100 * (pdi - mdi).abs() / (pdi + mdi)).replace([np.inf, -np.inf], np.nan)
            return _rma(dx.fillna(0), n)
        return self._ind(("adx", n), calc)

    def weekly(self):
        """The same stock on weekly candles (the current, unfinished week is the last candle)."""
        if "wk" in self.memo:
            return self.memo["wk"]
        root, agg = self.root, {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        if "wkfull" not in root.memo:
            full = root.df.resample("W-FRI").agg(agg).dropna(subset=["Close"])
            root.memo["wkfull"] = (full, root.df.index.to_period("W-FRI").end_time.normalize())
        full, lab = root.memo["wkfull"]
        if self is root:
            w = full
        else:                                                    # finished weeks from the full table + this view's own unfinished week
            this = lab[self.end - 1]
            first = int(lab.searchsorted(this, side="left"))
            seg = root.df.iloc[first:self.end]
            part = pd.DataFrame({"Open": [seg["Open"].iloc[0]], "High": [seg["High"].max()], "Low": [seg["Low"].min()],
                                 "Close": [seg["Close"].iloc[-1]], "Volume": [seg["Volume"].sum()]}, index=[this])
            w = pd.concat([full.iloc[:int(full.index.searchsorted(this, side="left"))], part])
        x = Ctx.__new__(Ctx)
        x.sym, x.df, x.nifty, x.extra, x.projected, x.memo, x.end = self.sym, w, self.nifty, self.extra, False, {}, len(w)
        x.c, x.o, x.h, x.l, x.v, x.root = w["Close"], w["Open"], w["High"], w["Low"], w["Volume"].astype(float), x
        self.memo["wk"] = x
        return x

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


def _count_ups(closes, small_up=0.0):
    """Up candles inside a pullback. Candles that close up by less than `small_up` % are treated as part of the pullback."""
    closes = np.asarray(closes, dtype=float)
    if len(closes) < 2:
        return 0
    pct = np.diff(closes) / closes[:-1] * 100
    return int((pct > (small_up or 0.0)).sum())


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
        ups = _count_ups(c[piv:N], r.get("small_up", 0.0))
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
    """-> (ok, chip, detail, metrics). Handles the options every rule has: weekly candles and "N candles ago"."""
    tag = ""
    if r.get("tf") == "weekly":
        ctx = ctx.weekly()
        tag += " W"
        if len(ctx.c) < 12:
            return False, f'{r["type"].upper()} W ?', "not enough weekly history", {}
    ago = int(r.get("ago") or 0)
    if ago:
        if ctx.end - ago < 12:
            return False, f'{r["type"].upper()} ?', f"not enough history {ago} candles ago", {}
        ctx = ctx.at(ctx.end - ago)
        tag += f" −{ago}c"
    ok, chip, detail, m = _eval_rule(ctx, r)
    if tag:
        chip += tag
        detail += (" · weekly candles" if r.get("tf") == "weekly" else "") + (f" · {ago} candles ago" if ago else "")
    return ok, chip, detail, m


def _eval_rule(ctx, r):
    """One rule on the latest candle of `ctx`. ok is False when data is insufficient."""
    t = r["type"]
    c = ctx.c
    cur = _last(c)
    try:
        if t == "pullback":
            p = _find_pullback(ctx, r)
            if p:
                return True, f'PB {p["n"]}c · {p["ratio"]:.2f}×', f'{p["n"]}-candle pullback, {p["depth"]:.1f}% deep, volume {p["ratio"]:.2f}× average', \
                    {"pb_n": p["n"], "pb_vol": p["ratio"], "pb_depth": p["depth"], "pb_ups": p["ups"]}
            # diagnostics for the near-match list: measure from the most recent swing high
            hh = ctx.h.iloc[-15:].to_numpy()
            k = len(hh) - 1 - int(np.argmax(hh))
            if k == 0:
                why = "no pullback yet — today is the high"
            else:
                piv = len(c) - k - 1
                base = float(ctx.v.iloc[max(0, piv - 19):piv + 1].mean())
                vr = float(ctx.v.iloc[-k:].mean()) / base if base else float("nan")
                ups = _count_ups(c.iloc[-k - 1:].to_numpy(), r.get("small_up", 0.0))
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
            sl = float(r.get("_slack") or 0.0)
            ok = v > a - sl if r["dir"] == "above" else v < a + sl
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
                sl = float(r.get("_slack") or 0.0)
                ok = (dist > -sl) if r["dir"] == "above" else (dist < sl)
                ok = ok and (abs(dist) >= r["dmin"] or sl > 0) and (not r["dmax"] or abs(dist) <= r["dmax"])
            return ok, f"{name} {dist:+.1f}%", f'price {dist:+.2f}% vs {r["kind"]} {r["len"]} ({describe(r).lower()})', {"ma_dist": dist}
        if t in ("ma_stack", "ma_cross"):
            f, s = ctx.ma(r["k1"], r["n1"]), ctx.ma(r["k2"], r["n2"])
            a, b = _last(f), _last(s)
            if a is None or b is None:
                return False, "MA ?", "not enough history", {}
            if t == "ma_stack":
                return a > b * (1 - float(r.get("_slack") or 0.0) / 100), f'{r["k1"]}{r["n1"]}{">" if a > b else "<"}{r["k2"]}{r["n2"]}', f'{r["k1"]} {r["n1"]} {a:,.1f} vs {r["k2"]} {r["n2"]} {b:,.1f}', {}
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
            days, amin, amax = int(r["days"]), int(r.get("amin") or 0), int(r.get("amax") or 0)
            hv, cv = ctx.h.to_numpy(dtype=float), c.to_numpy(dtype=float)
            if len(hv) < days + amax + 1:
                return False, "BO ?", "not enough history", {}
            for k in range(amin, amax + 1):
                e = len(cv) - k                                  # candle e-1 is the breakout candle
                lvl = float(hv[e - 1 - days:e - 1].max())
                if cv[e - 1] > lvl * (1 - float(r.get("_slack") or 0.0) / 100):
                    when = "today" if k == 0 else f"{k} candle{'s' if k != 1 else ''} ago"
                    return True, f"BO ✓ {lvl:,.0f}" + ("" if k == 0 else f" −{k}c"), f'closed above the {days}-candle high {lvl:,.2f} {when}', {"bo_ago": k}
            lvl = float(hv[-1 - days:-1].max())
            span = "" if not amax else f" (needs a breakout {amin}–{amax} candles ago)"
            return False, f"BO ✗ {lvl:,.0f}", f'close {cur:,.2f} vs {days}-candle high {lvl:,.2f}{span}', {}
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
            m, sg = ctx.macd()
            hist = m - sg
            if r["mode"] == "above signal":
                ok = float(m.iloc[-1]) > float(sg.iloc[-1])
            elif r["mode"] == "above zero":
                ok = float(m.iloc[-1]) > 0
            else:
                ok = float(hist.iloc[-1]) > float(hist.iloc[-2])
            return ok, f'MACD {"✓" if ok else "✗"}', f'MACD {float(m.iloc[-1]):.2f}, signal {float(sg.iloc[-1]):.2f}', {}
        if t == "adx":
            a = _last(ctx.adx(int(r["n"])))
            if a is None:
                return False, "ADX ?", "not enough history", {}
            ok = a >= r["lo"] and (not r["hi"] or a <= r["hi"])
            return ok, f"ADX {a:.0f}", f'ADX {a:.1f} (needs {_r(r["lo"], r["hi"])})', {"adx": a}
        if t == "candle":
            o, h, l = float(ctx.o.iloc[-1]), float(ctx.h.iloc[-1]), float(ctx.l.iloc[-1])
            po, pc = float(ctx.o.iloc[-2]), float(c.iloc[-2])
            rng = h - l
            if rng <= 0:
                return False, "CANDLE ?", "the candle has no range", {}
            body, top, bot = abs(cur - o), h - max(cur, o), min(cur, o) - l
            pos = (cur - l) / rng                                 # where the close sits in the candle, 0 = low, 1 = high
            shapes = {"hammer": bot >= 2 * body and top <= 0.25 * rng and body <= 0.4 * rng,
                      "bullish engulfing": pc < po and cur > o and cur >= po and o <= pc,
                      "bearish engulfing": pc > po and cur < o and cur <= po and o >= pc,
                      "doji": body <= 0.1 * rng, "strong close": pos >= 0.75, "weak close": pos <= 0.25,
                      "green candle": cur > o, "red candle": cur < o}
            ok = bool(shapes[r["pat"]])
            return ok, f'{r["pat"].upper()} {"✓" if ok else "✗"}', (f'body {body / rng * 100:.0f}% of the range, close {pos * 100:.0f}% of the way up '
                                                                   f'({"is" if ok else "not"} a {r["pat"]})'), {"close_pos": pos * 100}
        if t == "ma_touch":
            ma = ctx.ma(r["kind"], r["len"])
            m = _last(ma)
            if m is None or len(ma.dropna()) < r["within"]:
                return False, "TOUCH ?", f'not enough history for {r["kind"]} {r["len"]}', {}
            w = int(r["within"])
            gap = (ctx.l.iloc[-w:] / ma.iloc[-w:] - 1) * 100       # how far each low stayed above the average, %
            best = float(gap.min())
            ok = best <= r["tol"] and cur > m
            return ok, f'TOUCH {r["kind"]}{r["len"]} {"✓" if ok else "✗"}', (f'closest low was {best:+.2f}% from {r["kind"]} {r["len"]} in the last {w} candle'
                                                                            f'{"s" if w > 1 else ""}, close {(cur / m - 1) * 100:+.2f}% vs it'), {"touch": best}
        if t == "contract":
            legs, n = int(r["legs"]), int(r["n"])
            if len(c) < legs * n + 1:
                return False, "VCP ?", "not enough history", {}
            hv, lv = ctx.h.to_numpy(dtype=float)[-legs * n:], ctx.l.to_numpy(dtype=float)[-legs * n:]
            rg = [(hv[i * n:(i + 1) * n].max() - lv[i * n:(i + 1) * n].min()) / cur * 100 for i in range(legs)]
            ok = all(rg[i] < rg[i - 1] for i in range(1, legs))
            return ok, f'VCP {"✓" if ok else "✗"}', "leg ranges " + " → ".join(f"{x:.1f}%" for x in rg) + ("" if ok else " (not shrinking every time)"), {"vcp_last": rg[-1]}
        if t == "vol_fall":
            n = int(r["n"])
            vv = ctx.v.to_numpy(dtype=float)[-n - 1:]
            if len(vv) < n + 1:
                return False, "VOL↓ ?", "not enough history", {}
            rises = int((np.diff(vv) >= 0).sum())
            ok = rises <= r["skip"]
            return ok, f"VOL↓ {n - rises}/{n}", f'volume fell on {n - rises} of the last {n} candles (needs {n - int(r["skip"])})', {"vol_fall": n - rises}
        if t == "delivery":
            d = (ctx.extra or {}).get("deliv")
            if d is None:
                return False, "DELIV ?", "NSE delivery data is not available on this server", {}
            d = d[d.index <= c.index[-1]].dropna()
            n = int(r["n"])
            if len(d) < n:
                return False, "DELIV ?", f"delivery data covers only {len(d)} sessions up to this date", {}
            now = float(d.iloc[-n:].mean())
            ok = now >= r["lo"]
            txt = f"delivery {now:.1f}% over {n} sessions (needs ≥ {r['lo']:g}%)"
            if r["x"]:
                prev = d.iloc[:-n].tail(20)
                if len(prev) < 5:
                    return False, "DELIV ?", "not enough earlier delivery sessions to compare with", {}
                ratio = now / float(prev.mean()) if float(prev.mean()) else float("nan")
                ok = ok and ratio >= r["x"]
                txt += f", {ratio:.2f}× its earlier {len(prev)}-session average (needs ≥ {r['x']:g}×)"
            return ok, f"DELIV {now:.0f}%", txt, {"deliv": now}
    except Exception as e:  # pragma: no cover - a bad candle never crashes the scan
        return False, f"{t.upper()} ?", f"could not compute ({type(e).__name__})", {}
    return False, t, "unknown rule", {}


# ═════════════════════════ tolerance: "close enough" ═════════════════════════
TOLERANCES = {"Exact": 0.0, "Small (5%)": 0.05, "Medium (10%)": 0.10, "Loose (20%)": 0.20}
DEFAULT_TOL = 0.05
# how each number may be relaxed: min = a lower bound (relax down), max = an upper bound (relax up), max0 = upper bound where 0 = any,
# cmin / cmax = candle counts (one candle either way), smin / smax = signed percentages, rmin / rmax = RSI / ADX points
_TOL_FIELDS = {
    "pullback": [("nmin", "cmin"), ("nmax", "cmax"), ("max_up", "cmax"), ("vol", "max"), ("dmin", "min"), ("dmax", "max0"), ("small_up", "max")],
    "lowvol": [("nmin", "cmin"), ("nmax", "cmax0"), ("x", "max")],
    "runup": [("lo", "min"), ("hi", "max0")],
    "rsi": [("lo", "rmin"), ("hi", "rmax")],
    "ma_pos": [("dmin", "min"), ("dmax", "max0")],
    "vol_spike": [("lo", "min"), ("hi", "max0")],
    "atr": [("lo", "min"), ("hi", "max0")],
    "day_move": [("lo", "min"), ("hi", "max0")],
    "change": [("lo", "smin"), ("hi", "smax")],
    "near_high": [("lo", "min"), ("hi", "max")],
    "tight": [("max", "max")],
    "gap": [("lo", "smin"), ("hi", "smax")],
    "rs": [("lo", "smin")],
    "adx": [("lo", "rmin"), ("hi", "rmax0")],
    "ma_touch": [("tol", "max"), ("within", "cmax")],
    "vol_fall": [("skip", "cmax")],
    "delivery": [("lo", "min"), ("x", "min")],
}


def relax_rule(r, t):
    """A copy of rule r with every number widened by tolerance t (0.05 = 5%). Used only when the exact rule fails."""
    if not t:
        return r
    x = dict(r)
    for name, how in _TOL_FIELDS.get(r["type"], []):
        v = x.get(name)
        if v is None:
            continue
        if how == "min" and v > 0:
            x[name] = v * (1 - t)
        elif how in ("max", "max0") and v > 0:
            x[name] = v * (1 + t)
        elif how == "cmin":
            x[name] = max(1, v - 1)
        elif how == "cmax" or (how == "cmax0" and v):
            x[name] = v + 1
        elif how == "smin":
            x[name] = v - t * max(abs(v), 10.0)
        elif how == "smax":
            x[name] = v + t * max(abs(v), 10.0)
        elif how == "rmin" and v > 0:
            x[name] = max(0.0, v - 50 * t)
        elif how in ("rmax", "rmax0") and (v or how == "rmax") and v < 100:
            x[name] = min(100.0, v + 50 * t)
    if r["type"] in ("ma_pos", "ma_stack", "rsi_avg", "breakout"):
        x["_slack"] = 20 * t                    # 5% tolerance -> price may sit up to 1% on the wrong side of the average / level
    return x


def evaluate_rule_tol(ctx, r, t):
    """-> (ok, close, chip, detail, metrics). close = failed exactly but passes within tolerance t."""
    ok, chip, detail, m = evaluate_rule(ctx, r)
    if ok or not t or r["type"] not in set(_TOL_FIELDS) | {"ma_pos", "ma_stack", "rsi_avg", "breakout"}:
        return ok, False, chip, detail, m
    ok2, chip2, detail2, m2 = evaluate_rule(ctx, relax_rule(r, t))
    if ok2:
        return True, True, "≈ " + chip2, f"{detail2} · passes within the {t * 100:g}% tolerance (exact rule: {detail})", m2
    return False, False, chip, detail, m


def setup_tol(setup):
    cfg = setup.get("rule_config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = None
    try:
        v = (cfg or {}).get("tol") if isinstance(cfg, dict) else None
        return DEFAULT_TOL if v is None else max(0.0, min(0.3, float(v)))
    except (TypeError, ValueError):
        return DEFAULT_TOL


def tol_label(t):
    return next((k for k, v in TOLERANCES.items() if abs(v - t) < 1e-9), f"{t * 100:g}%")


def verdict(checks, min_pass=0):
    """Either-or groups count as one rule. -> dict(passed, total, need, status 'match' | 'near' | 'no', fails, miss)."""
    units, groups = [], {}
    for ck in checks:
        g = ck.get("grp", "—")
        if g != "—":
            if g not in groups:
                groups[g] = {"ok": False, "must": False, "checks": []}
                units.append(groups[g])
            u = groups[g]
        else:
            u = {"ok": False, "must": False, "checks": []}
            units.append(u)
        u["checks"].append(ck)
        u["ok"] = u["ok"] or ck["ok"]
        u["must"] = u["must"] or ck.get("must", False)
    total = len(units)
    need = total if not min_pass else max(1, min(int(min_pass), total))
    passed = sum(1 for u in units if u["ok"])
    failed = [u for u in units if not u["ok"]]
    req_fail = [u for u in failed if u["must"]]
    short = max(0, need - passed)
    status = "match" if not short and not req_fail else "near" if max(short, len(req_fail)) == 1 and total >= 2 else "no"
    order = req_fail + [u for u in failed if not u["must"]]
    fails = [ck for u in order for ck in u["checks"]]
    miss = ""
    if status != "match" and need < total:
        miss = f"passes {passed} of {total}, needs {need}" + (" · a required rule failed" if req_fail else "")
    return {"passed": passed, "total": total, "need": need, "status": status, "fails": fails, "miss": miss}


def evaluate_stock(sym, df, rules, live_frac=None, nifty=None, ctx=None, min_pass=0, extra=None, tol=0.0):
    """All enabled rules on one stock -> result dict (or None when there is too little data).
    Pass `ctx` (a Ctx or a view from Ctx.at) to evaluate a past date without rebuilding indicators."""
    if ctx is None:
        if df is None or len(df) < 30:
            return None
        ctx = Ctx(sym, df, live_frac, nifty, extra)
    if len(ctx.c) < 30:
        return None
    cur, prev = float(ctx.c.iloc[-1]), float(ctx.c.iloc[-2])
    checks, metrics = [], {}
    for r in rules:
        if not r.get("on", True):
            continue
        ok, close, chip, detail, m = evaluate_rule_tol(ctx, r, tol)
        checks.append({"ok": bool(ok), "close": bool(close), "chip": chip, "detail": detail, "label": RULES[r["type"]]["label"], "desc": describe(r),
                       "grp": r.get("grp", "—"), "must": bool(r.get("must")), "type": r["type"]})
        metrics.update(m)
    av = _last(ctx.avg_vol())
    v = verdict(checks, min_pass)
    n_chk = len(checks) or 1
    fit = round(100 * sum(1.0 if (c_["ok"] and not c_["close"]) else 0.6 if c_["ok"] else 0.0 for c_ in checks) / n_chk)
    return {"symbol": sym, "close": cur, "chg": (cur / prev - 1) * 100 if prev else 0.0, "turnover": ctx.turnover_cr(),
            "vol_x": float(ctx.v.iloc[-1]) / av if av else None, "rsi": _last(ctx.rsi(14)), "checks": checks,
            "fails": v["fails"], "status": v["status"], "passed": v["passed"], "total": v["total"], "miss": v["miss"], "fit": fit,
            "close_n": sum(1 for c_ in checks if c_["close"]),
            "m": metrics, "date": ctx.c.index[-1], "projected": ctx.projected,
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


def setup_min_pass(setup):
    cfg = setup.get("rule_config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = None
    try:
        return max(0, int((cfg or {}).get("min_pass") or 0)) if isinstance(cfg, dict) else 0
    except (TypeError, ValueError):
        return 0


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
_AI_PROMPT = """You turn a trader's {source} into scanner rules. Reply with ONLY JSON:
{{"price_min": number or null, "price_max": number or null, "tolerance": 0 | 5 | 10 | 20, "rules": [ ... ],
  "assumptions": [short sentences: every number you had to choose because the trader did not state it],
  "questions": [at most 3 short questions about things that are genuinely unclear and would change the rules]}}

Use ONLY these rule objects (fields exactly as shown; numbers are examples):
{{"type":"pullback","nmin":2,"nmax":4,"max_up":0,"small_up":4,"vol":0.8,"each":false,"dmin":0,"dmax":0}}   last nmin–nmax candles pull back from the recent high; average volume of those candles ≤ vol × 20-day average; max_up = bigger up candles tolerated; small_up = an up candle that closes up by less than this % is still part of the pullback ("a 3-4% green candle is ok" -> 4); each = every candle below average volume; dmin/dmax = pullback depth % (0 = any)
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
{{"type":"breakout","days":20,"amin":0,"amax":0}}   close above the previous `days`-candle high; amin–amax = how many candles ago it happened (0,0 = today)
{{"type":"tight","n":5,"max":6}}
{{"type":"nr","n":7}}
{{"type":"inside"}}
{{"type":"gap","lo":1,"hi":10}}
{{"type":"rs","days":63,"lo":0}}   beats NIFTY 50 by lo % points
{{"type":"macd","mode":"above signal"}}   or "above zero" / "histogram rising"
{{"type":"adx","lo":20,"hi":0,"n":14}}
{{"type":"candle","pat":"hammer"}}   pat: hammer / bullish engulfing / bearish engulfing / doji / strong close / weak close / green candle / red candle
{{"type":"ma_touch","kind":"EMA","len":20,"within":1,"tol":0.5}}   a low came within tol % of the average in the last `within` candles and the close is above it ("bounced off / tested / took support at the 20 EMA")
{{"type":"contract","legs":3,"n":5}}   `legs` blocks of n candles, each with a smaller high-low range than the one before (volatility contraction, VCP)
{{"type":"vol_fall","n":3,"skip":0}}   volume lower than the previous candle on each of the last n candles ("volume decreasing every candle")
{{"type":"delivery","n":5,"lo":40,"x":0}}   average delivery % of the last n sessions ≥ lo; x = also ≥ x times its earlier average (0 = skip)

Optional fields allowed on ANY rule (leave them out when not needed):
"ago": N       measure the rule N candles ago instead of on the latest candle
"tf": "weekly" measure on weekly candles instead of daily
"grp": "A"     rules sharing the same letter are either-or: one of them passing is enough ("RSI above 60 OR a volume spike")
"must": true   this rule is always required even when the trader says "most of these"

Rules:
- Flexible numbers become ranges: "3 low-volume candles, 4 is also fine" -> nmin 3, nmax 4.
- "tolerance" = how far a number may miss and still count, in %: 0 when the trader wants exact numbers, 5 by default, 10 when they say
  "roughly / around / about / approximately / 3-4% off is ok", 20 when they say "loosely / give or take a lot". Small up candles inside a pullback
  ("a green candle of 3-4% is fine") go into the pullback's small_up, not into the tolerance.
- Read the trader's intent, not just the words: a "pullback" is price easing back from a recent swing high; "momentum" before it is a run-up;
  "above the 20" means price above the 20 EMA unless SMA is said; "dry volume" means volume clearly below average.
- A pullback "with volume dry-up / decreasing volume" -> pullback rule (vol 0.8 unless stated).
- "low volume candles" without a pullback -> lowvol.
- Only include what the trader describes{or_shows}. Never invent rules or prices. price_min/max null when not given.
- Vague words get the usual meaning and go into "assumptions": "low volume" = below 0.8× the 20-day average; "near" = within 2 %; "strong trend" = ADX ≥ 25.
{extra}
Trader's setup:
\"\"\"{text}\"\"\"
"""
_AI_PICTURE = """- A chart picture is attached. Read the setup AT THE RIGHT EDGE of the chart (the latest candles): the trend before it, where price sits
  against any moving averages drawn, the last few candles (pullback / tight range / breakout / candle shape) and the volume bars under them.
- You cannot read exact indicator values from a picture: use sensible ranges, and list every one in "assumptions".
"""


_MODEL_MEMO = {}


def gemini_model(client):
    """The newest stable 'flash' model this key can use (Google retires model names); falls back to GEMINI_MODEL."""
    if "m" in _MODEL_MEMO:
        return _MODEL_MEMO["m"]
    pick = GEMINI_MODEL
    try:
        names = []
        for m in client.models.list():
            n = str(getattr(m, "name", "")).split("/")[-1]
            acts = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or ["generateContent"]
            if n.startswith("gemini-") and "flash" in n and "generateContent" in acts and not any(
                    x in n for x in ("lite", "preview", "exp", "image", "tts", "live", "audio", "embedding", "thinking")):
                names.append(n)
        if names and GEMINI_MODEL not in names:
            ver = lambda n: [float(x) for x in re.findall(r"\d+(?:\.\d+)?", n)[:1]] or [0.0]
            pick = sorted(names, key=lambda n: (ver(n), -len(n)))[-1]
    except Exception:
        pass
    _MODEL_MEMO["m"] = pick
    return pick


def parse_with_ai(text, key, image=None):
    """Description (and / or a chart picture) -> {"price_min", "price_max", "rules", "assumptions", "questions"} or None."""
    if not HAS_GEMINI or not key or not (text.strip() or image):
        return None
    try:
        client = genai.Client(api_key=key)
        prompt = _AI_PROMPT.format(text=text.strip() or "(no description — use the picture)",
                                   source="chart picture and notes" if image else "plain-English technical setup",
                                   or_shows=" or the picture clearly shows" if image else "", extra=_AI_PICTURE if image else "")
        parts = [prompt]
        if image:
            parts.append(genai_types.Part.from_bytes(data=image, mime_type="image/png"))
        kw = {"response_mime_type": "application/json", "temperature": 0.1}
        try:
            kw["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=1024)
        except Exception:
            pass
        try:
            resp = client.models.generate_content(model=gemini_model(client), contents=parts, config=genai_types.GenerateContentConfig(**kw))
        except Exception as exc:
            if "think" not in str(exc).lower():
                raise
            kw.pop("thinking_config", None)
            resp = client.models.generate_content(model=gemini_model(client), contents=parts, config=genai_types.GenerateContentConfig(**kw))
        data = json.loads(resp.text)
        rules = [x for x in (clean_rule(dict(r)) for r in data.get("rules", []) if isinstance(r, dict)) if x]
        pm = lambda v: float(v) if isinstance(v, (int, float)) and v >= 0 else None
        notes = lambda k: [str(x)[:220] for x in (data.get(k) or []) if isinstance(x, str) and x.strip()][:6]
        tol = data.get("tolerance")
        tol = (max(0.0, min(30.0, float(tol))) / 100) if isinstance(tol, (int, float)) else None
        return {"price_min": pm(data.get("price_min")), "price_max": pm(data.get("price_max")), "rules": rules, "tol": tol,
                "assumptions": notes("assumptions"), "questions": notes("questions")[:3]}
    except Exception as e:
        st.warning(f"AI could not read the {'picture' if image else 'description'} ({type(e).__name__})."
                   + ("" if image else " The built-in parser was used instead."))
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
        mu = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)?\s*(\d+(?:\.\d+)?)?\s*%\s*(?:up|green|gain|rise)[^.]{0,30}?(?:ok|okay|fine|allowed|acceptable)", d)
        small = float(mu.group(2) or mu.group(1)) if mu else 4.0
        rules.append(new_rule("pullback", nmin=a, nmax=b, vol=vol, max_up=1 if "mostly" in d else 0, small_up=small))
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
    if re.search(r"hammer", d):
        rules.append(new_rule("candle", pat="hammer"))
    if re.search(r"bullish engulfing", d):
        rules.append(new_rule("candle", pat="bullish engulfing"))
    if re.search(r"volume[^.]{0,30}?(?:decreas|fall|declin|lower)\w*\s*(?:on\s*)?(?:each|every)", d):
        rules.append(new_rule("vol_fall", n=3))
    m = re.search(r"deliver\w*[^.]{0,30}?(\d+(?:\.\d+)?)\s*%", d)
    if m:
        rules.append(new_rule("delivery", lo=float(m.group(1))))
    out["rules"] = rules
    if re.search(r"\b(exact|exactly|strict)\b", d):
        out["tol"] = 0.0
    elif re.search(r"\b(loose|loosely|give or take)\b", d):
        out["tol"] = 0.20
    elif re.search(r"\b(roughly|around|about|approximately|approx)\b|\d\s*%?\s*(?:off|less|short)[^.]{0,20}?(?:ok|okay|fine)", d):
        out["tol"] = 0.10
    out["assumptions"] = ["Built-in reader used (no AI): it only understands common phrasings — check every rule below."] if rules else []
    out["questions"] = []
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
            s["long_ok"] = set(d.get("long_ok") or [])
    except Exception:
        pass
    return s


def _save_store(s):
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        with open(STORE_FILE, "wb") as fh:
            pickle.dump({"hist": s["hist"], "long_ok": sorted(s.get("long_ok") or [])}, fh, protocol=pickle.HIGHEST_PROTOCOL)
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


def get_histories(symbols, snaps, progress=None, long=False):
    """~1 year of candles per symbol: stored history topped up with today's snapshot; refetched when missing,
    gapped, or split-adjusted differently (a split / bonus changes Yahoo's past prices)."""
    s = _store()
    need = []
    for x in symbols:
        h, sn = s["hist"].get(x), snaps.get(x)
        if h is None or len(h) < 60 or (long and x not in s.setdefault("long_ok", set())):
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
        got = _download(need, "2y" if long else "1y", progress, "History")
        with s["lock"]:
            s["hist"].update(got)
            if long:
                s.setdefault("long_ok", set()).update(need)
    out = {}
    with s["lock"]:
        for x in symbols:
            h, sn = s["hist"].get(x), snaps.get(x)
            if h is None:
                continue
            if sn is not None and len(sn) and h.index[-1] >= sn.index[0] - pd.Timedelta(days=10):
                h = pd.concat([h[h.index < sn.index[0]], sn])
                h = h[~h.index.duplicated(keep="last")].tail(520)
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


# ═════════════════════════ ready-made setups ═════════════════════════
def _t(*rules, **kw):
    return {"rules": list(rules), **kw}


TEMPLATES = {
    "Momentum pullback on dry volume": _t(
        ("runup", dict(days=30, lo=20)), ("rsi", dict(lo=50, hi=100)), ("ma_pos", dict(kind="EMA", len=20, dir="above")),
        ("ma_stack", dict(k1="EMA", n1=20, k2="SMA", n2=50)), ("pullback", dict(nmin=2, nmax=4, vol=0.8, max_up=1)),
        desc="Stock ran 20%+ in 30 candles and is still above EMA 20, then pulls back 2–4 candles on low volume."),
    "Pullback to EMA 20 that held": _t(
        ("ma_stack", dict(k1="EMA", n1=20, k2="SMA", n2=50)), ("ma_pos", dict(kind="SMA", len=200, dir="above")),
        ("ma_touch", dict(kind="EMA", len=20, within=2, tol=0.5)), ("candle", dict(pat="strong close")), ("rsi", dict(lo=45, hi=70)),
        desc="Uptrend (EMA 20 above SMA 50, price above SMA 200); a low touched EMA 20 in the last 2 candles and today closed near its high."),
    "Breakout near 52-week high on volume": _t(
        ("breakout", dict(days=20)), ("near_high", dict(days=252, lo=0, hi=5)), ("vol_spike", dict(lo=1.5)),
        ("candle", dict(pat="strong close")), ("rs", dict(days=63, lo=0)),
        desc="Closes above the 20-candle high within 5% of the 52-week high, on 1.5× volume, beating NIFTY over 3 months."),
    "Tight base after a run (flag)": _t(
        ("runup", dict(days=40, lo=25)), ("tight", dict(n=7, max=8)), ("lowvol", dict(nmin=3, nmax=0, x=1.0)),
        ("ma_pos", dict(kind="EMA", len=20, dir="above")), ("near_high", dict(days=60, lo=0, hi=12)),
        desc="A 25%+ run in 40 candles, then 7 candles inside an 8% range on below-average volume, still near the high."),
    "Volatility contraction (VCP)": _t(
        ("contract", dict(legs=3, n=7)), ("vol_fall", dict(n=3, skip=1)), ("near_high", dict(days=252, lo=0, hi=20)),
        ("ma_stack", dict(k1="SMA", n1=50, k2="SMA", n2=200)), ("ma_pos", dict(kind="SMA", len=50, dir="above")),
        desc="Three 7-candle legs, each tighter than the last, volume drying up, within 20% of the 52-week high in a long-term uptrend."),
    "NR7 + inside bar (coiled)": _t(
        ("nr", dict(n=7)), ("inside", {}), ("ma_pos", dict(kind="EMA", len=20, dir="above")), ("adx", dict(lo=20)),
        desc="Narrowest candle of 7 that is also an inside bar, above EMA 20 in a trending stock."),
    "Breakout 3–8 candles ago, now retesting": _t(
        ("breakout", dict(days=30, amin=3, amax=8)), ("pullback", dict(nmin=2, nmax=5, vol=0.9, max_up=1)),
        ("ma_pos", dict(kind="EMA", len=20, dir="above")), ("rsi", dict(lo=50, hi=75)),
        desc="Broke the 30-candle high 3–8 candles ago and is now pulling back on lighter volume above EMA 20."),
    "Weekly uptrend, daily oversold bounce": _t(
        ("ma_pos", dict(kind="EMA", len=20, dir="above", tf="weekly")), ("rsi", dict(lo=50, hi=100, tf="weekly")),
        ("rsi", dict(lo=30, hi=45, ago=1)), ("candle", dict(pat="green candle")), ("candle", dict(pat="strong close")),
        desc="Above the 20-week EMA with weekly RSI over 50; daily RSI was 30–45 yesterday and today is a green candle closing near its high."),
    "High delivery accumulation": _t(
        ("delivery", dict(n=5, lo=50, x=1.2)), ("ma_pos", dict(kind="SMA", len=50, dir="above")), ("tight", dict(n=10, max=10)),
        ("rs", dict(days=63, lo=0)),
        desc="Delivery above 50% and 1.2× its earlier average for 5 sessions while price holds a 10% range above SMA 50. Needs NSE delivery data."),
}


def template_rules(name):
    t = TEMPLATES[name]
    return [new_rule(k, **v) for k, v in t["rules"]], t.get("desc", "")


# ═════════════════════════ learn a setup from examples ═════════════════════════
def parse_examples(text):
    """'TATAPOWER 12 Aug 2026' / 'BEL, 2026-09-03' per line -> ([(SYM, Timestamp)], [lines that could not be read])."""
    out, bad = [], []
    for line in str(text).replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?:NSE:)?([A-Za-z0-9&\-]+)[\s,:|]+(.+)$", line)
        d = pd.to_datetime(m.group(2).strip(), errors="coerce", dayfirst=True) if m else pd.NaT
        if m is None or pd.isna(d):
            bad.append(line)
        else:
            out.append((m.group(1).upper(), pd.Timestamp(d).normalize()))
    return out, bad


def _ctx_on(root, day):
    """View of a stock on `day` (the last candle on or before it) or None when history does not reach that far."""
    pos = int(root.df.index.searchsorted(pd.Timestamp(day), side="right"))
    if pos < 30:
        return None
    v = root.at(pos)
    return v if (pd.Timestamp(day) - v.c.index[-1]).days <= 5 else None


_PROBES = [  # (column title, wide-open rule that always measures, metric key)
    ("Run-up 30c %", ("runup", dict(days=30, lo=0)), "runup"), ("RSI", ("rsi", dict(lo=0, hi=100)), "rsi"),
    ("vs EMA20 %", ("ma_pos", dict(kind="EMA", len=20, dir="above")), "ma_dist"), ("vs SMA50 %", ("ma_pos", dict(kind="SMA", len=50, dir="above")), "ma_dist"),
    ("vs SMA200 %", ("ma_pos", dict(kind="SMA", len=200, dir="above")), "ma_dist"),
    ("Pullback candles", ("pullback", dict(nmin=1, nmax=8, max_up=3, vol=5.0)), "pb_n"), ("Pullback vol ×", ("pullback", dict(nmin=1, nmax=8, max_up=3, vol=5.0)), "pb_vol"),
    ("Pullback depth %", ("pullback", dict(nmin=1, nmax=8, max_up=3, vol=5.0)), "pb_depth"), ("Pullback up candles", ("pullback", dict(nmin=1, nmax=8, max_up=3, vol=5.0)), "pb_ups"),
    ("Low-vol candles", ("lowvol", dict(nmin=1, x=1.0)), "lowvol"), ("Volume ×", ("vol_spike", dict(lo=0)), "vol_x"),
    ("ATR %", ("atr", dict(lo=0)), "atr"), ("Below 52w high %", ("near_high", dict(days=252, lo=0, hi=100)), "off_high"),
    ("5-candle range %", ("tight", dict(n=5, max=50)), "tight"), ("vs NIFTY 63c", ("rs", dict(days=63, lo=-100)), "rs"),
    ("ADX", ("adx", dict(lo=0)), "adx"), ("Close position %", ("candle", dict(pat="strong close")), "close_pos"),
]
_FLAGS = [("RSI > its avg", ("rsi_avg", dict(dir="above", n=14))), ("EMA20 > SMA50", ("ma_stack", dict(k1="EMA", n1=20, k2="SMA", n2=50))),
          ("20c breakout", ("breakout", dict(days=20))), ("Inside bar", ("inside", {})), ("NR7", ("nr", dict(n=7))),
          ("Touched EMA20", ("ma_touch", dict(kind="EMA", len=20, within=2, tol=0.5))), ("MACD > signal", ("macd", dict(mode="above signal")))]


def learn_rules(views):
    """views: {label: Ctx view on the example date}. Measures every example and proposes rules whose ranges cover ALL of them.
    -> (rules, measurements {title: {label: value}})"""
    meas = {}
    for title, (rt, kw), key in _PROBES:
        rule = new_rule(rt, **kw)
        meas[title] = {lab: evaluate_rule(v, rule)[3].get(key) for lab, v in views.items()}
    for title, (rt, kw) in _FLAGS:
        rule = new_rule(rt, **kw)
        meas[title] = {lab: bool(evaluate_rule(v, rule)[0]) for lab, v in views.items()}
    n = len(views)

    def vals(title):
        x = [v for v in meas[title].values() if v is not None]
        return x if len(x) == n else None                      # a rule is only proposed when every example has the value

    def allflag(title):
        return all(meas[title].values())

    rules = []
    x = vals("Run-up 30c %")
    if x and min(x) >= 8:
        rules.append(new_rule("runup", days=30, lo=math.floor(min(x) * 0.9)))
    x = vals("RSI")
    if x:
        rules.append(new_rule("rsi", lo=max(0, math.floor(min(x) - 3)), hi=min(100, math.ceil(max(x) + 3))))
    if allflag("RSI > its avg"):
        rules.append(new_rule("rsi_avg", dir="above", n=14))
    for title, kind, ln in (("vs EMA20 %", "EMA", 20), ("vs SMA50 %", "SMA", 50), ("vs SMA200 %", "SMA", 200)):
        x = vals(title)
        if not x:
            continue
        if max(abs(v) for v in x) <= 3 and min(x) < 0 < max(x):
            rules.append(new_rule("ma_pos", kind=kind, len=ln, dir="near", dmax=math.ceil(max(abs(v) for v in x) * 1.25 * 2) / 2))
        elif min(x) > 0:
            rules.append(new_rule("ma_pos", kind=kind, len=ln, dir="above", dmax=math.ceil(max(x) * 1.3) if ln == 20 else 0.0))
        elif max(x) < 0:
            rules.append(new_rule("ma_pos", kind=kind, len=ln, dir="below"))
    if allflag("EMA20 > SMA50"):
        rules.append(new_rule("ma_stack", k1="EMA", n1=20, k2="SMA", n2=50))
    pn, pv, pd_, pu = vals("Pullback candles"), vals("Pullback vol ×"), vals("Pullback depth %"), vals("Pullback up candles")
    if pn and pv and pd_ and pu:
        rules.append(new_rule("pullback", nmin=int(min(pn)), nmax=int(max(pn)), max_up=int(max(pu)), vol=math.ceil(max(pv) * 1.1 * 20) / 20,
                              dmax=math.ceil(max(pd_) * 1.3)))
    else:
        x = vals("Low-vol candles")
        if x and min(x) >= 2:
            rules.append(new_rule("lowvol", nmin=int(min(x)), nmax=0, x=1.0))
    x = vals("Volume ×")
    if x and min(x) >= 1.3:
        rules.append(new_rule("vol_spike", lo=math.floor(min(x) * 0.9 * 10) / 10))
    x = vals("Below 52w high %")
    if x and max(x) <= 25:
        rules.append(new_rule("near_high", days=252, lo=0, hi=math.ceil(max(x) * 1.25)))
    x = vals("5-candle range %")
    if x and max(x) <= 10:
        rules.append(new_rule("tight", n=5, max=math.ceil(max(x) * 1.2 * 2) / 2))
    x = vals("vs NIFTY 63c")
    if x and min(x) > 0:
        rules.append(new_rule("rs", days=63, lo=0))
    x = vals("ADX")
    if x and min(x) >= 20:
        rules.append(new_rule("adx", lo=math.floor(min(x) * 0.9)))
    x = vals("ATR %")
    if x:
        rules.append(new_rule("atr", lo=math.floor(min(x) * 0.75 * 10) / 10, hi=math.ceil(max(x) * 1.35 * 10) / 10))
    x = vals("Close position %")
    if x and min(x) >= 75:
        rules.append(new_rule("candle", pat="strong close"))
    for title, (rt, kw) in _FLAGS[2:6]:
        if allflag(title):
            rules.append(new_rule(rt, **kw))
    return rules, meas


# ═════════════════════════ NSE delivery % for the whole market ═════════════════════════
def delivery_table(sessions=30):
    """date x symbol table of delivery % from NSE's daily file (needs research_data.py). None when NSE is unreachable."""
    s = _store()
    key = datetime.now(IST).strftime("%Y%m%d") + ("p" if datetime.now(IST).hour >= 19 else "a")
    if s.get("deliv_key") == key:
        return s.get("deliv")
    table = None
    try:
        import research_data as rd
        cols, errors, d = {}, 0, datetime.now(IST).date()
        for _ in range(sessions * 2 + 10):
            if len(cols) >= sessions or errors >= 2:
                break
            if d.weekday() < 5:
                state, df = rd._load_day(d)
                if state == "ok" and df is not None and len(df):
                    x = df[df["series"] == "EQ"] if "series" in df.columns else df
                    pct = (pd.to_numeric(x["deliv_qty"], errors="coerce") / pd.to_numeric(x["volume"], errors="coerce") * 100)
                    cols[pd.Timestamp(d)] = pd.Series(pct.to_numpy(), index=x["symbol"].astype(str).to_numpy()).groupby(level=0).first()
                elif state == "error":
                    errors += 1
            d -= timedelta(days=1)
        if len(cols) >= 3:
            table = pd.DataFrame(cols).T.sort_index()
    except Exception:
        table = None
    s["deliv_key"], s["deliv"] = key, table
    return table


def _extra_for(sym, deliv):
    return {"deliv": deliv[sym]} if deliv is not None and sym in deliv.columns else {}


# ═════════════════════════ the scan ═════════════════════════
def _candidates(universe, pmin, pmax, min_turnover, prog, widen=1.0, long=False):
    """Stage 1 (5-day prices: price band + rough liquidity) and stage 2 (history for the survivors).
    widen > 1 loosens the price band (a past-date scan or backtest needs stocks that were in the band THEN)."""
    snaps = get_snapshots(universe, lambda p, m: prog(0.05 + 0.40 * p, m))
    band = []
    for sym, sn in snaps.items():
        c = pd.to_numeric(sn["Close"], errors="coerce").dropna()
        if not len(c):
            continue
        last = float(c.iloc[-1])
        if not (pmin / widen <= last <= pmax * widen):
            continue
        tv = (sn["Close"] * sn["Volume"]).iloc[:-1] if len(sn) > 1 else sn["Close"] * sn["Volume"]
        if min_turnover > 0 and float(tv.mean()) / 1e7 < min_turnover * 0.5 / widen:      # lenient pre-filter; exact check later
            continue
        band.append(sym)
    hist = get_histories(band, snaps, lambda p, m: prog(0.45 + 0.40 * p, m), long=long)
    return snaps, band, hist


def _needs(rules):
    on = [r for r in rules if r.get("on", True)]
    return {"nifty": any(r["type"] == "rs" for r in on), "deliv": any(r["type"] == "delivery" for r in on),
            "long": any(r.get("tf") == "weekly" or (r["type"] in ("ma_pos", "ma_stack", "ma_cross", "ma_touch") and
                                                    max(r.get("len", 0), r.get("n1", 0), r.get("n2", 0)) > 150) for r in on)}


def run_scan(universe, rules, pmin, pmax, min_turnover, want_near=True, progress=None, min_pass=0, asof=None, tol=0.0):
    """asof: a past date -> the scan as it would have looked at that day's close (plus what the price did since)."""
    t0 = time.time()
    stats = {"universe": len(universe)}
    prog = progress or (lambda p, m: None)
    on = [r for r in rules if r.get("on", True)]
    need = _needs(on)
    snaps, band, hist = _candidates(universe, pmin, pmax, min_turnover, prog, widen=2.0 if asof is not None else 1.0, long=need["long"])
    stats["priced"], stats["in_band"] = len(snaps), len(band)
    nifty = nifty_close() if need["nifty"] else None
    deliv = delivery_table() if need["deliv"] else None
    match, near, liquid, no_data = [], [], 0, []
    funnel = [{"desc": describe(r), "pass": 0, "only": 0} for r in on]
    for i, sym in enumerate(band):
        df = hist.get(sym)
        if df is None or len(df) < 30:
            no_data.append(sym)
            continue
        if asof is None:
            res = evaluate_stock(sym, df, on, _live_fraction(df.index[-1]), nifty, min_pass=min_pass, extra=_extra_for(sym, deliv), tol=tol)
        else:
            root = Ctx(sym, df, None, nifty, _extra_for(sym, deliv))
            view = _ctx_on(root, asof)
            res = evaluate_stock(sym, None, on, ctx=view, min_pass=min_pass, tol=tol) if view is not None else None
            if res is not None:
                if not (pmin <= res["close"] <= pmax):
                    continue
                res["since"] = (float(root.c.iloc[-1]) / res["close"] - 1) * 100
                res["since_days"] = root.end - view.end
        if res is None:
            no_data.append(sym)
            continue
        if res["turnover"] < min_turnover:
            continue
        liquid += 1
        bad = [k for k, ck in enumerate(res["checks"]) if not ck["ok"]]
        for k, ck in enumerate(res["checks"]):
            funnel[k]["pass"] += ck["ok"]
        if len(bad) == 1:
            funnel[bad[0]]["only"] += 1
        if res["status"] == "match":
            match.append(res)
        elif res["status"] == "near" and want_near:
            near.append(res)
        if i % 100 == 0:
            prog(0.85 + 0.15 * (i + 1) / max(1, len(band)), f"Checking rules · {i + 1}/{len(band)}")
    match.sort(key=lambda r: -r["fit"])
    near.sort(key=lambda r: -r["fit"])
    stats.update(liquid=liquid, no_data=len(no_data), match=len(match), near=len(near), secs=time.time() - t0,
                 exact=sum(1 for r in match if not r["close_n"]))
    asof_d = max((r["date"] for r in match + near), default=None)
    return {"match": match, "near": near, "stats": stats, "no_data": no_data[:200], "asof": asof_d, "funnel": funnel,
            "past": asof is not None, "deliv_missing": need["deliv"] and deliv is None,
            "live": any(r["projected"] for r in match + near), "at": datetime.now(IST), "tol": tol}


# ═════════════════════════ backtest: what happened after past signals ═════════════════════════
HORIZONS = (5, 10, 20)


def backtest(hist, rules, pmin, pmax, min_turnover=0.0, sessions=120, min_pass=0, nifty=None, deliv=None, gap=5, progress=None, tol=0.0):
    """Replays the setup on every one of the last `sessions` candles of every stock in `hist`.
    A signal is taken at the close; entry is the NEXT candle's open. The same stock is not counted again for `gap` candles.
    -> {"signals": [...], "summary": {h: {...}}, "base": {h: mean % of every stock-day in the same sample}, "stats": {...}}"""
    t0 = time.time()
    on = [r for r in rules if r.get("on", True)]
    simple = not min_pass and all(r.get("grp", "—") == "—" for r in on)
    quick = sorted(on, key=lambda r: r.get("tf") == "weekly")          # cheap daily rules first
    signals, base_sum, base_n = [], {h: 0.0 for h in HORIZONS}, {h: 0 for h in HORIZONS}
    days_checked, stocks = 0, 0
    syms = list(hist)
    for si, sym in enumerate(syms):
        df = hist[sym]
        if df is None or len(df) < 80:
            continue
        root = Ctx(sym, df, None, nifty, _extra_for(sym, deliv))
        N = root.end
        o, c, l, h_ = (x.to_numpy(dtype=float) for x in (root.o, root.c, root.l, root.h))
        first = max(60, N - sessions)
        stocks += 1
        for hz in HORIZONS:                                   # baseline: buy any day in the window at the next open
            t = np.arange(first, N - hz + 1)
            t = t[(c[t - 1] >= pmin) & (c[t - 1] <= pmax)] if len(t) else t
            if len(t):
                base_sum[hz] += float((c[t + hz - 1] / o[t] - 1).sum() * 100)
                base_n[hz] += len(t)
        last_sig = -10 ** 9
        for end in range(first, N + 1):                        # view "as of candle end-1"
            px = c[end - 1]
            if not (pmin <= px <= pmax) or end - 1 - last_sig < gap:
                continue
            days_checked += 1
            view = root.at(end)
            if simple and not all(evaluate_rule_tol(view, r, tol)[0] for r in quick):
                continue
            res = evaluate_stock(sym, None, on, ctx=view, min_pass=min_pass, tol=tol)
            if res is None or res["status"] != "match" or res["turnover"] < min_turnover:
                continue
            last_sig = end - 1
            sig = {"symbol": sym, "date": root.c.index[end - 1], "close": px, "entry": None, "open": end >= N}
            if end < N:
                e = o[end]
                sig["entry"] = e
                for hz in HORIZONS:
                    sig[f"r{hz}"] = (c[end + hz - 1] / e - 1) * 100 if end + hz - 1 < N else None
                w = slice(end, min(N, end + 20))
                sig["worst"], sig["best"] = (l[w].min() / e - 1) * 100, (h_[w].max() / e - 1) * 100
                sig["now"] = (c[-1] / e - 1) * 100
            signals.append(sig)
        if progress and si % 20 == 0:
            progress((si + 1) / max(1, len(syms)), f"Replaying {si + 1}/{len(syms)} stocks · {len(signals)} signals so far")
    summary = {}
    for hz in HORIZONS:
        x = np.array([s[f"r{hz}"] for s in signals if s.get(f"r{hz}") is not None], dtype=float)
        summary[hz] = None if not len(x) else {"n": int(len(x)), "win": float((x > 0).mean() * 100), "mean": float(x.mean()),
                                               "median": float(np.median(x)), "worst": float(x.min()), "best": float(x.max())}
    worst = [s["worst"] for s in signals if s.get("worst") is not None]
    signals.sort(key=lambda s: s["date"], reverse=True)
    return {"signals": signals, "summary": summary, "base": {h: (base_sum[h] / base_n[h] if base_n[h] else None) for h in HORIZONS},
            "stats": {"stocks": stocks, "days": days_checked, "signals": len(signals), "names": len({s["symbol"] for s in signals}),
                      "avg_worst": float(np.mean(worst)) if worst else None, "secs": time.time() - t0, "sessions": sessions}}


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
           "rule_config": {"version": 2, "min_pass": int(draft.get("min_pass") or 0), "tol": float(draft.get("tol", DEFAULT_TOL)),
                           "rules": [{k: v for k, v in r.items() if k != "_id"} for r in rules]}}
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
        resp = client.models.generate_content(model=gemini_model(client), contents=parts,
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
_CSS = """<style>
.ss-hero{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;padding:22px 0 10px;}
.ss-hero .t{font:600 17px var(--sans,Inter,sans-serif);letter-spacing:-.2px;color:var(--tx,#EDEDEF);}
.ss-hero .t em{font:600 12px var(--sans);font-style:normal;color:#FF9F0A;display:block;margin-bottom:4px;letter-spacing:0;}
.ss-hero .d{font:13.5px/1.6 var(--sans);color:var(--tx3,#71717A);margin-top:4px;max-width:880px;}
.ss-card{background:var(--s1,#111113);border:1px solid var(--line,#232326);border-radius:12px;padding:14px 16px;margin:6px 0 10px;}
.ss-card .n{font:600 15px var(--sans);color:var(--tx);} .ss-card .o{font:500 12.5px var(--sans);color:var(--tx3);margin-left:10px;}
.ss-rules{margin-top:10px;display:flex;flex-direction:column;gap:6px;}
.ss-rule{font:13.5px/1.55 var(--sans);color:var(--tx2,#A1A1AA);display:flex;gap:10px;align-items:baseline;}
.ss-rule b{font:600 11.5px var(--sans);color:#FF9F0A;background:rgba(255,159,10,.10);border-radius:999px;padding:1px 8px;flex-shrink:0;}
.ss-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:8px 0 12px;}
.ss-kpi{background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:12px 14px;position:relative;overflow:hidden;}
.ss-kpi:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--a,transparent);}
.ss-kpi small{display:block;font:500 12px var(--sans);color:var(--tx3);} .ss-kpi b{display:block;font:600 20px var(--sans);color:var(--tx);margin-top:4px;font-variant-numeric:tabular-nums;}
.ss-tw{border:1px solid var(--line);border-radius:12px;background:var(--s1);overflow:auto;}
.ss-t{width:100%;border-collapse:separate;border-spacing:0;font:13.5px var(--sans);font-variant-numeric:tabular-nums;}
.ss-t th{background:var(--s1);font:500 12px var(--sans);color:var(--tx3);text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:0;}
.ss-t td{padding:10px 12px;border-bottom:1px solid var(--line);color:var(--tx);vertical-align:middle;}
.ss-t tr:last-child td{border-bottom:0;}
.ss-t td.r,.ss-t th.r{text-align:right;white-space:nowrap;}
.ss-t tr:hover td{background:rgba(255,255,255,.02);}
.ss-chip{display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:999px;font:500 11.5px var(--sans);white-space:nowrap;}
.ss-chip.ok{color:#30D158;background:rgba(48,209,88,.10);}
.ss-chip.cl{color:#FFB340;background:rgba(255,159,10,.12);}
.ss-chip.no{color:#FF453A;background:rgba(255,69,58,.10);}
.ss-miss{color:#FFB340;font:500 13px var(--sans);white-space:normal;max-width:320px;}
.ss-ai{display:inline-block;padding:2px 9px;border-radius:999px;font:600 12px var(--sans);background:rgba(255,255,255,.05);}
.ss-fit{display:inline-flex;align-items:center;gap:8px;font:600 13px var(--sans);}
.ss-fit i{display:inline-block;width:46px;height:5px;border-radius:3px;background:#232326;position:relative;overflow:hidden;}
.ss-fit i:after{content:"";position:absolute;left:0;top:0;bottom:0;width:var(--w);background:var(--c);border-radius:3px;}
.hc-wrap{position:relative;display:inline-block;font:600 14px var(--sans);color:var(--tx);cursor:default;border-bottom:1px dashed #52525B;}
.hc-pop{display:none;position:absolute;top:-8px;left:calc(100% + 12px);width:344px;background:#161618;border:1px solid #2E2E33;border-radius:12px;
  padding:10px;z-index:99999;box-shadow:0 18px 40px rgba(0,0,0,.6);}
.hc-wrap:hover .hc-pop{display:block;}
.hc-pop-h{display:flex;justify-content:space-between;font:500 12px var(--sans);color:var(--tx3);margin-bottom:6px;}
.ss-wl{color:#30D158;font:600 12px var(--sans);}
div[data-testid="stMarkdown"]:has(.hc-wrap:hover),div[data-testid="stElementContainer"]:has(.hc-wrap:hover){position:relative;z-index:9999;overflow:visible !important;}
div[class*="st-key-ss_rule_"]{background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:12px 14px 4px;}
.ss-rh{font:600 14px var(--sans);color:var(--tx);padding-top:2px;} .ss-rh span{display:block;font:13px/1.5 var(--sans);color:var(--tx3);margin:3px 0 0;}
.ss-plain{background:linear-gradient(180deg,rgba(255,159,10,.06),rgba(255,159,10,.02));border:1px solid rgba(255,159,10,.22);border-radius:14px;padding:16px 18px;margin:8px 0 12px;}
.ss-plain .h{font:600 15px var(--sans);color:var(--tx);} .ss-plain .h span{font:500 12.5px var(--sans);color:var(--tx3);margin-left:8px;}
.ss-plain ol{margin:10px 0 0 18px;padding:0;} .ss-plain li{font:14px/1.6 var(--sans);color:var(--tx2);margin:3px 0;}
.ss-plain .f{font:13px/1.6 var(--sans);color:var(--tx3);margin-top:10px;}
/* phone result cards */
.ss-cards{display:flex;flex-direction:column;gap:10px;}
.ss-rc{background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:14px;}
.ss-rc .top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
.ss-rc .sym{font:600 16px var(--sans);color:var(--tx);} .ss-rc .sub{font:12.5px var(--sans);color:var(--tx3);margin-top:2px;}
.ss-rc .px{text-align:right;font:600 15px var(--sans);color:var(--tx);} .ss-rc .px small{display:block;font:500 12.5px var(--sans);}
.ss-rc .chips{margin-top:10px;}
.ss-rc .miss{margin-top:8px;font:13px/1.5 var(--sans);color:#FFB340;}
</style>"""


def _e(s):
    return html.escape("" if s is None else str(s))


def _fit_html(fit):
    c = GREEN if fit >= 90 else "#8AD66B" if fit >= 75 else AMBER if fit >= 55 else RED
    return f'<span class="ss-fit" style="--w:{fit}%;--c:{c}"><i></i>{fit}</span>'


def _chip_html(ck):
    cls = "cl" if ck.get("close") else ("ok" if ck["ok"] else "no")
    mark = "≈" if ck.get("close") else ("✓" if ck["ok"] else "✗")
    chip = ck["chip"][2:] if ck.get("close") and ck["chip"].startswith("≈ ") else ck["chip"]
    return f'<span class="ss-chip {cls}" title="{_e(ck["label"] + ": " + ck["detail"])}">{mark} {_e(chip)}</span>'


def _is_phone():
    return bool(st.session_state.get("_ak_phone"))


def _results_cards(rows, near, wl, past=False):
    """Phone layout: one card per stock."""
    out = []
    for r in rows:
        cc = GREEN if r["chg"] >= 0 else RED
        miss = ""
        if near and r["fails"]:
            f = r["fails"][0]
            miss = f'<div class="miss">Missed: {_e(f["label"])} · {_e(f["detail"])}</div>'
        since = ""
        if past and r.get("since") is not None:
            since = f' · since then <span style="color:{GREEN if r["since"] >= 0 else RED}">{r["since"]:+.1f}%</span>'
        out.append(f'<div class="ss-rc"><div class="top"><div><div class="sym">{_e(r["symbol"])}{" <span class=ss-wl>· in watchlist</span>" if r["symbol"] in wl else ""}</div>'
                   f'<div class="sub">Fit {_fit_html(r.get("fit", 0))} · RSI {"—" if r["rsi"] is None else format(r["rsi"], ".0f")} · ₹{r["turnover"]:,.1f} Cr/day{since}</div></div>'
                   f'<div class="px">₹{r["close"]:,.2f}<small style="color:{cc}">{r["chg"]:+.2f}%</small></div></div>'
                   f'<div class="chips">{"".join(_chip_html(ck) for ck in r["checks"])}</div>{miss}</div>')
    return '<div class="ss-cards">' + "".join(out) + "</div>"


def plain_summary(rules, tol, min_pass=0):
    """The setup read back in plain words, so the trader can confirm it was understood."""
    on = [r for r in rules if r.get("on", True)]
    if not on:
        return ""
    need = "all of these" if not min_pass or min_pass >= len(on) else f"at least {min_pass} of these {len(on)}"
    items = "".join(f"<li>{_e(describe(r))}</li>" for r in on)
    tol_txt = ("Numbers must be met exactly." if not tol else
               f"Close enough counts: a number that misses by less than {tol * 100:g}% still passes (shown as ≈), so a stock is not dropped for a tiny miss.")
    pb = next((r for r in on if r["type"] == "pullback"), None)
    pb_txt = (f" Inside the pullback, green candles that close up by less than {pb['small_up']:g}% are treated as part of the pullback."
              if pb and pb.get("small_up") else "")
    return (f'<div class="ss-plain"><div class="h">How the scanner reads your setup<span>a stock must pass {need}</span></div><ol>{items}</ol>'
            f'<div class="f">{_e(tol_txt + pb_txt)}</div></div>')


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


def _results_table(rows, near, wl, ai, past=False):
    head = (["#", "Stock", "Fit", "Close" if past else "LTP", "Day", "Turnover", "Vol×", "RSI"] + (["Since then"] if past else []) + ["Rule checks"]
            + (["Missed rule"] if near else []) + (["AI"] if ai else []) + [""])
    right = {3, 4, 5, 6, 7} | ({8} if past else set())
    th = "".join(f'<th class="{"r" if i in right else ""}">{h}</th>' for i, h in enumerate(head))
    body = []
    for i, r in enumerate(rows, 1):
        cc = GREEN if r["chg"] >= 0 else RED
        pop = (f'<span class="hc-pop"><div class="hc-pop-h"><span>{_e(r["symbol"])} · 60 candles · <span style="color:{CYAN}">EMA 20</span> · '
               f'<span style="color:{AMBER}">SMA 50</span></span><span>NSE</span></div>{_svg_chart(r["chart"])}</span>')
        chips = "".join(_chip_html(ck) for ck in r["checks"])
        if r.get("total") and (near or r["passed"] != r["total"]):
            chips = f'<span class="ss-chip cl">{r["passed"]}/{r["total"]}</span>' + chips
        cells = [f'<td style="color:{T3}">{i}</td>', f'<td><span class="hc-wrap">{_e(r["symbol"])}{pop}</span></td>', f'<td>{_fit_html(r.get("fit", 0))}</td>',
                 f'<td class="r">₹{r["close"]:,.2f}</td>', f'<td class="r" style="color:{cc}">{"▲" if r["chg"] >= 0 else "▼"} {abs(r["chg"]):.2f}%</td>',
                 f'<td class="r">₹{r["turnover"]:,.1f} Cr</td>',
                 f'<td class="r">{"—" if r["vol_x"] is None else format(r["vol_x"], ".2f") + "×"}{"*" if r["projected"] else ""}</td>',
                 f'<td class="r">{"—" if r["rsi"] is None else format(r["rsi"], ".0f")}</td>']
        if past:
            sv = r.get("since")
            cells.append(f'<td class="r" style="color:{T3}">—</td>' if sv is None else
                         f'<td class="r" style="color:{GREEN if sv >= 0 else RED}" title="{r.get("since_days", 0)} sessions later">{sv:+.1f}%</td>')
        cells.append(f'<td style="white-space:normal">{chips}</td>')
        if near:
            f = r["fails"][0]
            extra = f'<br><span style="color:{T2}">{_e(r["miss"])}</span>' if r.get("miss") else ""
            cells.append(f'<td class="ss-miss"><b>{_e(f["label"])}</b><br>{_e(f["detail"])}{extra}</td>')
        if ai:
            a = ai.get(r["symbol"])
            if a:
                col = GREEN if a["score"] >= 8 else CYAN if a["score"] >= 6 else T2
                cells.append(f'<td><span class="ss-ai" style="color:{col}" title="{_e(a["caption"])}">{a["score"]}/10</span></td>')
            else:
                cells.append(f'<td style="color:{T3}">—</td>')
        cells.append(f'<td class="ss-wl">{"In watchlist" if r["symbol"] in wl else ""}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="ss-tw"><table class="ss-t"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _simple_table(head, rows, right_from=1):
    th = "".join(f'<th class="{"r" if i >= right_from else ""}">{_e(h)}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td class="{"r" if i >= right_from else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="ss-tw" style="overflow:auto"><table class="ss-t"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def _pc(v, d=1):
    return f'<span style="color:{T3}">—</span>' if v is None else f'<span style="color:{GREEN if v >= 0 else RED}">{v:+.{d}f}%</span>'


SORTS = {"Best fit": (lambda r: (-r.get("fit", 0), -r["turnover"])), "Liquidity (turnover)": (lambda r: -r["turnover"]), "Day % (best first)": (lambda r: -r["chg"]),
         "Lowest pullback volume": (lambda r: r["m"].get("pb_vol", 9)), "Strongest run-up": (lambda r: -r["m"].get("runup", -1e9)),
         "Closest to moving average": (lambda r: abs(r["m"].get("ma_dist", 1e9))), "RSI (high first)": (lambda r: -(r["rsi"] or 0)),
         "Relative strength vs NIFTY": (lambda r: -r["m"].get("rs", -1e9)), "Most rules passed": (lambda r: -(r.get("passed") or 0)),
         "Move since the scan date": (lambda r: -(r.get("since") or -1e9)), "A–Z": (lambda r: r["symbol"])}


# ═════════════════════════ page: SETUPS (builder) ═════════════════════════
def _load_draft(setup=None):
    """Put a setup (or a blank one) into the editor state."""
    for k in [k for k in st.session_state if k.startswith("ssf_")]:
        del st.session_state[k]
    if setup:
        rules = setup_rules(setup)
        d = {"id": setup.get("id"), "name": setup.get("name", ""), "desc": setup.get("visual_rules") or "",
             "pmin": float(setup.get("price_min") or 0), "pmax": float(setup.get("price_max") or 99999), "owner": setup.get("owner"),
             "min_pass": setup_min_pass(setup), "tol": setup_tol(setup)}
    else:
        rules = []
        d = {"id": None, "name": "", "desc": "", "pmin": 0.0, "pmax": 99999.0, "owner": None, "min_pass": 0, "tol": DEFAULT_TOL}
    st.session_state["ssd_tol"] = tol_label(d["tol"]) if tol_label(d["tol"]) in TOLERANCES else "Small (5%)"
    st.session_state["ssd_minpass"] = int(d["min_pass"])
    for k in ("ss_ai_notes", "ss_learn"):
        st.session_state.pop(k, None)
    st.session_state["ss_draft"] = d
    st.session_state["ss_rules"] = rules
    st.session_state["ssd_name"], st.session_state["ssd_desc"] = d["name"], d["desc"]
    st.session_state["ssd_pmin"], st.session_state["ssd_pmax"] = d["pmin"], min(d["pmax"], 999999.0)


def _rule_editor(r, i):
    meta = RULES[r["type"]]
    with st.container(key=f"ss_rule_{r['_id']}"):
        h1, h2, h3, h4, h5 = st.columns([6, 1.1, 0.6, 0.6, 0.6], gap="small")
        h1.markdown(f'<div class="ss-rh">{i + 1}. {_e(meta["label"])}<span>{_e(describe(r))}</span></div>', unsafe_allow_html=True)
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
        def widgets(fields):
            for start in range(0, len(fields), 4):
                chunk = fields[start:start + 4]
                cols = st.columns(4, gap="small")
                for col, (name, label, kind, default, a, b) in zip(cols, chunk):
                    k = f"ssf_{r['_id']}_{name}"
                    if k not in st.session_state:
                        st.session_state[k] = r.get(name, default)
                    if kind == "i":
                        r[name] = int(col.number_input(label, min_value=int(a), max_value=int(b), step=1, key=k))
                    elif kind == "f":
                        r[name] = float(col.number_input(label, min_value=float(a), max_value=float(b), step=0.05 if b <= 5 else 0.5, key=k, format="%g"))
                    elif kind == "s":
                        r[name] = col.selectbox(label, a, key=k)
                    else:
                        r[name] = col.checkbox(label, key=k)
        widgets(meta["fields"])
        used = [x for x in (f'{r.get("ago")} candles ago' if r.get("ago") else "", "weekly" if r.get("tf") == "weekly" else "",
                            f'group {r.get("grp")}' if r.get("grp", "—") != "—" else "", "required" if r.get("must") else "") if x]
        with st.expander("More options: earlier candle · weekly · either-or · always required" + (f"  ({', '.join(used)})" if used else "")):
            widgets(COMMON)
            st.caption("Either-or: rules with the same letter count as one rule, and one of them passing is enough. "
                       "Always required only matters when the setup needs fewer than all rules to pass.")
        st.session_state["ss_rules"][i] = clean_rule(r) | {"_id": r["_id"]}


def _apply_parsed(parsed, src):
    """Put AI / parser output into the editor. -> False when nothing was read."""
    if not parsed or not parsed.get("rules"):
        return False
    for k in [k for k in st.session_state if k.startswith("ssf_")]:
        del st.session_state[k]
    st.session_state["ss_rules"] = parsed["rules"]
    # the price boxes may already be on screen in this run, so their new values are applied at the start of the next run
    st.session_state["_ss_pending_price"] = (parsed.get("price_min"), parsed.get("price_max"))
    st.session_state["ss_ai_notes"] = {"src": src, "assumptions": parsed.get("assumptions") or [], "questions": parsed.get("questions") or []}
    if parsed.get("tol") is not None:
        st.session_state["_ss_pending_tol"] = tol_label(float(parsed["tol"])) if tol_label(float(parsed["tol"])) in TOLERANCES else "Medium (10%)"
    st.toast(f'{len(parsed["rules"])} rules built — check the numbers below.')
    st.rerun()
    return True


def _fmt_meas(v):
    if v is None:
        return f'<span style="color:{T3}">—</span>'
    if isinstance(v, bool):
        return f'<span style="color:{GREEN if v else T3}">{"yes" if v else "no"}</span>'
    return f"{v:,.2f}" if abs(v) < 10 else f"{v:,.1f}"


def _learn_panel(supabase, editable):
    """Give 3–6 past examples (symbol + date); the scanner measures them and proposes rules that cover all of them."""
    st.markdown('<div class="ss-hero"><div><div class="t"><em>Examples</em>Show the scanner your setup</div>'
                '<div class="d">Not sure about the numbers? List past cases of your setup, one per line, as symbol and date (the day you would have '
                'bought). The scanner measures each one and proposes rules whose ranges cover all of them.</div></div></div>', unsafe_allow_html=True)
    with st.expander("Learn from examples", expanded=bool(st.session_state.get("ss_learn"))):
        st.text_area("Examples", key="ss_ex_text", height=110, label_visibility="collapsed",
                     placeholder="TATAPOWER 12 Aug 2026\nBEL 3 Sep 2026\nHAL 2026-10-07")
        if st.button("Measure these examples", key="ss_ex_btn", type="primary"):
            ex, bad = parse_examples(st.session_state.get("ss_ex_text", ""))
            out = {"bad": bad, "missing": [], "labels": [], "meas": {}, "rules": [], "cur": []}
            if ex:
                with st.spinner("Loading price history for your examples…"):
                    syms = list(dict.fromkeys(e[0] for e in ex))
                    snaps = get_snapshots(syms)
                    hist = get_histories(syms, snaps, long=True)
                    nifty = nifty_close()
                    roots = {x: Ctx(x, hist[x], None, nifty) for x in syms if x in hist}
                    views = {}
                    for sym, day in ex:
                        v = _ctx_on(roots[sym], day) if sym in roots else None
                        if v is None:
                            out["missing"].append(f"{sym} {day:%d %b %Y}" + ("" if sym in roots else " (symbol not found)"))
                        else:
                            views[f"{sym} · {v.c.index[-1]:%d %b %y}"] = v
                    if views:
                        rules, meas = learn_rules(views)
                        cur = [r for r in st.session_state["ss_rules"] if r.get("on", True)]
                        out.update(labels=list(views), meas=meas, rules=rules,
                                   cur=[(describe(r), [bool(evaluate_rule(v, r)[0]) for v in views.values()],
                                         [evaluate_rule(v, r)[2] for v in views.values()]) for r in cur])
            st.session_state["ss_learn"] = out
        L = st.session_state.get("ss_learn")
        if not L:
            st.caption("Price history goes back about two years. Three or more examples give much better ranges than one.")
            return
        if L["bad"]:
            st.warning("Could not read: " + " · ".join(L["bad"]) + ". Write each line as SYMBOL and a date, e.g. TATAPOWER 12 Aug 2026.")
        if L["missing"]:
            st.warning("No price history on that date for: " + " · ".join(L["missing"]))
        if not L["labels"]:
            return
        st.markdown(f'<div class="ss-rh">What the scanner measured on each example</div>', unsafe_allow_html=True)
        st.markdown(_simple_table(["Measure"] + L["labels"], [[_e(t)] + [_fmt_meas(row.get(lab)) for lab in L["labels"]] for t, row in L["meas"].items()]),
                    unsafe_allow_html=True)
        if L["cur"]:
            st.markdown(f'<div class="ss-rh" style="margin-top:10px">Your current rules on these examples</div>', unsafe_allow_html=True)
            st.markdown(_simple_table(["Rule"] + L["labels"], [[_e(d)] + [f'<span style="color:{GREEN if ok else RED}" title="{_e(why)}">{"✓ pass" if ok else "✗ fail"}</span>'
                                                                         for ok, why in zip(oks, whys)] for d, oks, whys in L["cur"]]), unsafe_allow_html=True)
            st.caption("A ✗ here means your current rule rejects one of your own examples — hover it for the actual value, then widen that rule.")
        st.markdown(f'<div class="ss-rh" style="margin-top:10px">Proposed rules · {len(L["rules"])} · every example passes all of them</div>'
                    + "".join(f'<div class="ss-rule"><b>{i + 1}</b>{_e(describe(r))}</div>' for i, r in enumerate(L["rules"])), unsafe_allow_html=True)
        st.caption("Ranges are stretched a little beyond your examples. With few examples they can still be too narrow or too wide: "
                   "remove rules that are not part of your idea, then check the setup in BACKTEST.")
        if editable and L["rules"] and st.button(f"Use these {len(L['rules'])} rules (replaces the rules below)", key="ss_ex_use"):
            for k in [k for k in st.session_state if k.startswith("ssf_")]:
                del st.session_state[k]
            st.session_state["ss_rules"] = [dict(r, _id=uuid.uuid4().hex[:8]) for r in L["rules"]]
            st.session_state.pop("ss_ai_notes", None)
            st.rerun()


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
    pt = st.session_state.pop("_ss_pending_tol", None)
    if pt in TOLERANCES:
        st.session_state["ssd_tol"] = pt
    lo_, hi_ = st.session_state.pop("_ss_pending_price", (None, None))
    if lo_ is not None:
        st.session_state["ssd_pmin"] = float(lo_)
    if hi_ is not None:
        st.session_state["ssd_pmax"] = min(float(hi_), 999999.0)
    editable = sel is None or can_edit(sel, is_admin)
    if sel is not None and not editable:
        st.info("This is a desk setup — you can scan with it or DUPLICATE it to make your own editable copy.")
    if editable:
        k1, k2 = st.columns([3, 1])
        tname = k1.selectbox("Start from a ready-made setup", list(TEMPLATES), key="ss_tpl", label_visibility="collapsed",
                             format_func=lambda n: f"Template · {n}")
        if k2.button("Load template", key="ss_tpl_btn", use_container_width=True, help="Replaces the rules below. Nothing is saved until you press SAVE."):
            trules, tdesc = template_rules(tname)
            for k in [k for k in st.session_state if k.startswith("ssf_")]:
                del st.session_state[k]
            st.session_state["ss_rules"] = trules
            st.session_state["ssd_minpass"] = 0
            if not st.session_state.get("ssd_desc", "").strip():
                st.session_state["ssd_desc"] = tdesc
            if not st.session_state.get("ssd_name", "").strip():
                st.session_state["ssd_name"] = tname
            st.session_state.pop("ss_ai_notes", None)
            st.toast(f"Template loaded: {len(trules)} rules. Change any number, then save.")
            st.rerun()
    if c2.button("Duplicate", key="ss_dup", use_container_width=True, disabled=sel is None):
        d["id"], d["owner"] = None, None
        st.session_state["ss_draft"] = d
        st.session_state["ssd_name"] = (d["name"] + " (copy)")[:80]
        st.session_state["_ss_pending_pick"] = "new"
        st.session_state["ss_loaded_for"] = None
        st.rerun()

    st.markdown('<div class="ss-hero"><div><div class="t"><em>Setup</em>Describe it, check how it was understood, save</div>'
                '<div class="d">Write it the way you would explain it to a friend, including what is "ok" (e.g. "a 3-4% green candle in the pullback is fine", '
                '"4 candles is also fine"). Press <b>Build rules</b>, read how it was understood, answer any question, then save.</div></div></div>', unsafe_allow_html=True)
    a, b = st.columns([1.6, 1])
    with a:
        st.text_input("Setup name", key="ssd_name", placeholder="e.g. Momentum pullback on dry volume", disabled=not editable)
        st.text_area("Describe the setup in your own words", key="ssd_desc", height=118, disabled=not editable,
                     placeholder="e.g. Stock gained 20%+ in the last 30 days. RSI above 50 and above its 14 SMA. Price above EMA 20 and SMA 50. "
                                 "Then a pullback of 3 candles on low volume (4 candles is also fine).")
        x1, x2 = st.columns([1.3, 1])
        if x1.button("Build rules from my description", key="ss_build", type="primary", use_container_width=True, disabled=not editable):
            text = st.session_state.get("ssd_desc", "")
            if not text.strip():
                st.warning("Write the description first.")
            else:
                with st.spinner("Reading your setup…"):
                    parsed = parse_with_ai(text, gemini_key) or parse_locally(text)
                if not _apply_parsed(parsed, "your description"):
                    st.warning("No rules could be read from that description. Add rules by hand below.")
        x2.caption("Uses Arka AI (Gemini) when GEMINI_KEY is set, otherwise the built-in reader. It replaces the rules below.")
        notes = st.session_state.get("ss_ai_notes")
        if notes and (notes["assumptions"] or notes["questions"]):
            body = "".join(f'<div class="ss-rule"><b>Assumed</b>{_e(x)}</div>' for x in notes["assumptions"])
            st.markdown(f'<div class="ss-card"><span class="n">How {_e(notes["src"])} was read</span><span class="o">check these before you save</span>'
                        f'<div class="ss-rules">{body}</div></div>', unsafe_allow_html=True)
            if notes["questions"] and editable:
                st.markdown('<div class="ss-rh" style="margin-top:6px">A few questions so the rules match what you mean</div>', unsafe_allow_html=True)
                answers = []
                for qi, q in enumerate(notes["questions"]):
                    ans = st.text_input(q, key=f"ss_qa_{qi}", placeholder="Your answer")
                    if ans.strip():
                        answers.append(f"Q: {q} A: {ans.strip()}")
                if st.button("Rebuild with my answers", key="ss_qa_go", type="primary", disabled=not answers):
                    text = (st.session_state.get("ssd_desc", "") + "\n\nClarifications from the trader:\n" + "\n".join(answers)).strip()
                    with st.spinner("Rebuilding your rules…"):
                        parsed = parse_with_ai(text, gemini_key) or parse_locally(text)
                    for qi in range(len(notes["questions"])):
                        st.session_state.pop(f"ss_qa_{qi}", None)
                    if not _apply_parsed(parsed, "your description and answers"):
                        st.warning("No rules could be read. Fix the numbers by hand below.")
    with b:
        p1, p2 = st.columns(2)
        p1.number_input("Price from (₹)", min_value=0.0, max_value=999999.0, step=10.0, key="ssd_pmin", disabled=not editable)
        p2.number_input("Price to (₹)", min_value=0.0, max_value=999999.0, step=10.0, key="ssd_pmax", disabled=not editable)
        st.selectbox("Close enough", list(TOLERANCES), key="ssd_tol", disabled=not editable,
                     help="How far a number may miss and still pass. 5%: a 19% run-up passes 'at least 20%', RSI 48 passes 'above 50'. "
                          "Results show these as ≈ and rank exact matches first.")
        img = st.file_uploader("Reference chart (optional, for the AI check)", type=["png", "jpg", "jpeg"], key="ssd_img", disabled=not editable)
        b64 = None
        if img is None and sel is not None:
            b64 = setup_image(supabase, sel["id"])
            if b64:
                st.image(base64.b64decode(b64), caption="Saved reference chart", use_container_width=True)
        elif img is not None:
            st.image(img, caption="New reference chart", use_container_width=True)
        pic = img.getvalue() if img is not None else (base64.b64decode(b64) if sel is not None and img is None and b64 else None)
        if st.button("Build rules from the chart picture", key="ss_build_pic", use_container_width=True,
                     disabled=not (editable and pic and HAS_GEMINI and gemini_key),
                     help="Gemini looks at the right edge of your chart and proposes rules. Needs GEMINI_KEY and a picture. Replaces the rules below."):
            with st.spinner("Looking at your chart…"):
                parsed = parse_with_ai(st.session_state.get("ssd_desc", ""), gemini_key, image=pic)
            if not _apply_parsed(parsed, "your chart picture"):
                st.warning("No rules could be read from the picture.")

    _learn_panel(supabase, editable)

    n_on = sum(1 for r in st.session_state["ss_rules"] if r.get("on", True))
    mp = int(st.session_state.get("ssd_minpass") or 0)
    st.markdown(plain_summary(st.session_state["ss_rules"], TOLERANCES.get(st.session_state.get("ssd_tol"), DEFAULT_TOL), mp), unsafe_allow_html=True)
    st.markdown(f'<div class="ss-hero"><div><div class="t"><em>Rules</em>{len(st.session_state["ss_rules"])} rules · a stock must pass '
                f'{"all of them" if not mp or mp >= n_on else f"at least {mp} of {n_on}"}</div>'
                '<div class="d">Change any number. Turn a rule off to keep it but skip it. Stocks that miss by exactly one rule are shown as near matches.</div></div></div>',
                unsafe_allow_html=True)
    if editable:
        st.number_input("A stock must pass at least this many rules (0 = all of them)", min_value=0, max_value=40, step=1, key="ssd_minpass",
                        help="Example: 7 rules and 5 here = any 5 of the 7 is a match. Mark a rule 'Always required' under More options to protect it.")
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
        if r2.button("Add rule", key="ss_add_btn", use_container_width=True):
            st.session_state["ss_rules"].append(new_rule(add))
            st.rerun()

    # test on one stock
    st.markdown('<div class="ss-hero"><div><div class="t"><em>Test</em>Check one stock against these rules</div>'
                '<div class="d">See exactly which rule a stock passes or fails — e.g. why it did not show up in a scan.</div></div></div>', unsafe_allow_html=True)
    t1, t3, t2 = st.columns([2, 1.4, 1])
    tsym = t1.text_input("Symbol to test", key="ss_test_sym", placeholder="e.g. TATAPOWER", label_visibility="collapsed").strip().upper()
    tdate = t3.text_input("On date", key="ss_test_date", placeholder="date (blank = latest), e.g. 12 Aug 2026", label_visibility="collapsed").strip()
    if t2.button("Test", key="ss_test_btn", use_container_width=True) and tsym:
        when = pd.to_datetime(tdate, errors="coerce", dayfirst=True) if tdate else None
        res = None
        if tdate and pd.isna(when):
            st.error("That date could not be read. Write it like 12 Aug 2026 or 2026-08-12.")
            return
        with st.spinner(f"Checking {tsym}…"):
            rules = [r for r in st.session_state["ss_rules"] if r.get("on", True)]
            need = _needs(rules)
            snaps = get_snapshots([tsym])
            hist = get_histories([tsym], snaps, long=need["long"])
            if tsym in hist:
                deliv = delivery_table() if need["deliv"] else None
                root = Ctx(tsym, hist[tsym], _live_fraction(hist[tsym].index[-1]) if when is None else None,
                           nifty_close() if need["nifty"] else None, _extra_for(tsym, deliv))
                view = root if when is None else _ctx_on(root, when)
                if view is not None:
                    res = evaluate_stock(tsym, None, rules, ctx=view, min_pass=int(st.session_state.get("ssd_minpass") or 0),
                                         tol=TOLERANCES.get(st.session_state.get("ssd_tol"), DEFAULT_TOL))
        if res is None and when is not None and tsym in hist:
            st.error(f"{tsym} has no candle near {when:%d %b %Y} in the stored history "
                     f"({hist[tsym].index[0]:%d %b %Y} – {hist[tsym].index[-1]:%d %b %Y}).")
        elif res is None:
            st.error(f"No price history found for {tsym}. Use the NSE symbol.")
        else:
            ok = res["status"] == "match"
            nfail = sum(1 for ck in res["checks"] if not ck["ok"])
            verdict_ = ("Match" if ok else "Near match" if res["status"] == "near" else f'{nfail} rules failed') + f' · {res["passed"]}/{res["total"]} passed'
            verdict_ += f' · as of {res["date"]:%d %b %Y}'
            col = GREEN if ok else AMBER if res["status"] == "near" else RED
            rows = "".join(f'<tr><td>{"≈" if ck.get("close") else "✓" if ck["ok"] else "✗"}</td><td>{_e(ck["desc"])}</td>'
                           f'<td style="color:{AMBER if ck.get("close") else GREEN if ck["ok"] else RED}">{_e(ck["detail"])}</td></tr>'
                           for ck in res["checks"])
            pmin, pmax = float(st.session_state.get("ssd_pmin", 0)), float(st.session_state.get("ssd_pmax", 99999))
            price_ok = pmin <= res["close"] <= pmax
            st.markdown(f'<div class="ss-card"><span class="n">{_e(tsym)}</span><span class="o" style="color:{col}">{verdict_}</span>'
                        f'<span class="o">₹{res["close"]:,.2f} · {res["chg"]:+.2f}% · {"inside" if price_ok else "outside"} the ₹{pmin:,.0f}–{pmax:,.0f} band · '
                        f'turnover ₹{res["turnover"]:,.1f} Cr/day</span></div>'
                        f'<div class="ss-tw"><table class="ss-t"><thead><tr><th></th><th>Rule</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>',
                        unsafe_allow_html=True)

    # save
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    s1, s2, s3 = st.columns([1.4, 1, 1])
    if editable and s1.button("Save setup" if not d.get("id") else "Save changes", type="primary", key="ss_save", use_container_width=True):
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
            d.update(name=name, desc=st.session_state.get("ssd_desc", ""), pmin=pmin, pmax=pmax, min_pass=int(st.session_state.get("ssd_minpass") or 0),
                     tol=TOLERANCES.get(st.session_state.get("ssd_tol"), DEFAULT_TOL))
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
    if sel is not None and editable and s3.button("Delete setup", key="ss_del", use_container_width=True):
        st.session_state["ss_confirm_del"] = sel["id"]
    if st.session_state.get("ss_confirm_del") == sel_id and sel is not None:
        st.warning(f'Delete “{sel["name"]}” permanently?')
        y, n_ = st.columns(2)
        if y.button("Yes, delete", key="ss_del_yes", type="primary", use_container_width=True):
            err = delete_setup(supabase, sel_id)
            st.session_state.pop("ss_confirm_del", None)
            if err:
                st.error(err)
            else:
                load_setups(supabase, force=True)
                st.session_state["_ss_pending_pick"] = "new"
                st.session_state["ss_loaded_for"] = "reload"
                st.rerun()
        if n_.button("Cancel", key="ss_del_no", use_container_width=True):
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
        st.info("No setups yet. Open My setups, describe your setup, press Build rules and save it.")
        return
    ids = [s["id"] for s in setups]
    cur = st.session_state.get("ss_sel")
    idx = ids.index(cur) if cur in ids else 0
    c1, c2 = st.columns([2.2, 1])
    i = c1.selectbox("Setup", range(len(setups)), index=idx, key="ss_scan_pick",
                     format_func=lambda k: f'{setups[k]["name"]}{"  ·  desk" if (setups[k].get("owner") or "admin") == "admin" and not is_admin else ""}')
    setup = setups[i]
    st.session_state["ss_sel"] = setup["id"]
    rules = setup_rules(setup)
    if not rules:
        st.warning("This setup has no rules yet. Open My setups and build them.")
        return
    with c2:
        b64 = setup_image(supabase, setup["id"]) if st.session_state.get("ss_show_ref") else None
        st.toggle("Show reference chart", key="ss_show_ref")
    if b64:
        st.image(base64.b64decode(b64), width=420)

    # rules in this run
    st.markdown(f'<div class="ss-card"><span class="n">{_e(setup["name"])}</span><span class="o">{len(rules)} rules · '
                f'₹{float(setup.get("price_min") or 0):,.0f}–{float(setup.get("price_max") or 99999):,.0f}</span>'
                f'<div class="ss-rules">' + "".join(f'<div class="ss-rule"><b>{k + 1}</b>{_e(describe(r))}{"" if r.get("on", True) else " · off"}</div>'
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
    near = d.checkbox("Show near matches", value=True, key="ss_near", help="Stocks that miss by exactly one rule")
    min_pass = setup_min_pass(setup)
    tkey = f"ss_tol_run_{setup['id']}"
    if tkey not in st.session_state:
        st.session_state[tkey] = tol_label(setup_tol(setup)) if tol_label(setup_tol(setup)) in TOLERANCES else "Small (5%)"
    tol = TOLERANCES[st.selectbox("Close enough", list(TOLERANCES), key=tkey,
                                  help="How far a number may miss and still pass (shown as ≈, ranked below exact matches). Saved per setup on the Setups page.")]
    asof = None
    z1, z2 = st.columns([1.6, 3])
    if z1.checkbox("Scan a past date", key="ss_past", help="Shows what this scan would have found at that day's close, and what the price did since."):
        asof = pd.Timestamp(z2.date_input("Date", value=(datetime.now(IST) - timedelta(days=30)).date(), key="ss_past_date",
                                          max_value=datetime.now(IST).date(), label_visibility="collapsed"))
        st.caption("Past-date scan: stocks are picked from a wider price band today (½× to 2×) and then checked against your band on that date. "
                   "Stocks that were delisted since are not included.")
    st.caption(f"Searching ₹{pmin:,.0f} – ₹{pmax:,.0f}. The first full-market scan of the day downloads prices (about 1–3 minutes); "
               "re-scans with other rules or bands use the stored data and take seconds.")
    if pmin > pmax:
        st.error("The price band is reversed.")
        return
    if st.button(f"Run scan · {setup['name']}", type="primary", use_container_width=True, key="ss_run"):
        universe, src = get_universe(uni)
        if not universe:
            st.warning(f"{uni} is empty. Add stocks in the WATCHLIST workspace first." if is_list else "No stocks to scan.")
            return
        bar, msg = st.progress(0.0), st.empty()

        def prog(p, m):
            bar.progress(max(0.0, min(1.0, float(p))))
            msg.caption(m)
        prog(0.02, f"Universe: {src}")
        res = run_scan(universe, rules, pmin, pmax, float(liq), near, prog, min_pass=min_pass, asof=asof, tol=tol)
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
    st.markdown(f'<div class="ss-hero"><div><div class="t"><em>Results</em>{_e(res["setup_name"])}</div>'
                f'<div class="d">{_e(res["source"])} · ₹{res["band"][0]:,.0f}–{res["band"][1]:,.0f} · scanned {res["at"]:%d %b %H:%M IST} in {S["secs"]:.0f}s'
                f'{(" · PAST-DATE SCAN as of " if res.get("past") else " · prices as of ") + format(res["asof"], "%d %b %Y") if res["asof"] is not None else ""}'
                f'{" · * volume projected to a full day (market open)" if res["live"] else ""}</div></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="ss-kpis">' + _kpi("Universe", f'{S["universe"]:,}') + _kpi("Priced", f'{S["priced"]:,}') +
                _kpi("In price band", f'{S["in_band"]:,}') + _kpi("Liquid & checked", f'{S["liquid"]:,}') +
                _kpi("Matches", f'<span style="color:{GREEN}">{S["match"]}</span>'
                     + (f'<span style="font:500 12px var(--sans);color:var(--tx3);margin-left:8px">{S.get("exact", 0)} exact</span>' if res.get("tol") else ""), GREEN) +
                _kpi("Near matches", f'<span style="color:{AMBER}">{S["near"]}</span>', AMBER) + '</div>', unsafe_allow_html=True)
    if S["no_data"]:
        st.caption(f'{S["no_data"]} stocks had too little price history (new listings or suspended) and were skipped.')
    if res.get("deliv_missing"):
        st.warning("NSE delivery data could not be loaded on this server, so the delivery rule failed for every stock. "
                   "Skip that rule for this run, or see the Research page → SRC for the NSE status.")
    fun = res.get("funnel") or []
    if fun and S["liquid"]:
        tight = min(fun, key=lambda f: f["pass"])
        with st.expander(f'Which rule removes the most stocks — strictest: {tight["desc"][:70]} ({tight["pass"]} of {S["liquid"]} pass)'):
            st.markdown(_simple_table(["Rule", "Stocks that pass", "% of checked", "Fail ONLY this rule"],
                                      [[_e(f["desc"]), f'{f["pass"]:,}', f'{f["pass"] / S["liquid"] * 100:.0f}%',
                                        f'<span style="color:{AMBER if f["only"] else T3}">{f["only"]:,}</span>'] for f in fun]), unsafe_allow_html=True)
            st.caption("'Fail ONLY this rule' = stocks you would gain by removing or widening that one rule. A rule that almost nothing passes "
                       "is usually the number to look at first.")

    v1, v2, v3 = st.columns([1.6, 1.3, 1])
    tab = v1.radio("Show", [f"Matches ({S['match']})", f"Near matches ({S['near']})"], horizontal=True, key="ss_tab", label_visibility="collapsed")
    is_near = tab.startswith("Near")
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
    if _is_phone():
        st.markdown(_results_cards(chunk, is_near, wl, past=bool(res.get("past"))), unsafe_allow_html=True)
    else:
        st.markdown(_results_table(chunk, is_near, wl, ai if ai and not is_near else None, past=bool(res.get("past"))), unsafe_allow_html=True)
    st.caption("Fit 100 = every rule passed exactly · ≈ = passed within the tolerance · hover a symbol for its chart and a chip for the exact value"
               + (" · Vol× * = projected to a full day" if res["live"] else ""))

    # action bar
    with st.container(key="ss_actions"):
        syms = [r["symbol"] for r in rows]
        e1, e2 = st.columns([3, 1.2])
        chosen = e1.multiselect("Add to watchlist", [s for s in syms if s not in wl], key=f"ss_pick_{is_near}",
                                placeholder="Tick stocks to add to your watchlist…", label_visibility="collapsed")
        if e2.button(f"Add {len(chosen)} to watchlist" if chosen else "Add selected", key=f"ss_add_sel_{is_near}",
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
        if f1.button(f"Add all {len(new_all)} {'near ' if is_near else ''}matches", key=f"ss_add_all_{is_near}",
                     use_container_width=True, disabled=not new_all):
            n, err = add_to_watchlist(supabase, new_all)
            st.error(f"Could not save: {err}") if err else st.toast(f"{n} stock(s) added to your watchlist")
            if not err:
                st.rerun()
        one = f2.selectbox("Stock", syms, key=f"ss_one_{is_near}", label_visibility="collapsed")
        if f3.button("Arka AI", key=f"ss_an_{is_near}", use_container_width=True, help="Open in Arka AI and start the analysis"):
            _goto(one, "analysis")
        if f4.button("Research", key=f"ss_rs_{is_near}", use_container_width=True):
            _goto(one, "research")
        with f5:
            out = pd.DataFrame([{"symbol": r["symbol"], "ltp": round(r["close"], 2), "chg_pct": round(r["chg"], 2),
                                 "turnover_cr": round(r["turnover"], 2), "rsi": None if r["rsi"] is None else round(r["rsi"], 1),
                                 "missed": r["fails"][0]["detail"] if r["fails"] else "",
                                 "checks": " | ".join(("OK " if ck["ok"] else "NO ") + ck["detail"] for ck in r["checks"])} for r in rows])
            fmt = st.selectbox("Export", ["CSV", "TradingView list"], key="ss_exp_fmt", label_visibility="collapsed")
            if fmt == "CSV":
                st.download_button("Download", out.to_csv(index=False), f"arka_scan_{datetime.now(IST):%Y%m%d}.csv", "text/csv",
                                   key="ss_dl_csv", use_container_width=True)
            else:
                st.download_button("Download", "\n".join(f"NSE:{s}" for s in syms), f"arka_scan_{datetime.now(IST):%Y%m%d}.txt",
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
                if st.button(f"Run AI check on {int(n)} stocks", key="ss_ai_run"):
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


# ═════════════════════════ page: BACKTEST ═════════════════════════
def _view_backtest(supabase, is_admin):
    setups = load_setups(supabase)
    if not setups:
        st.info("No setups yet. Open My setups and save one first.")
        return
    ids = [s["id"] for s in setups]
    cur = st.session_state.get("ss_sel")
    st.markdown('<div class="ss-hero"><div><div class="t"><em>Backtest</em>What happened after this setup appeared before</div>'
                '<div class="d">Replays the setup on every past day for every stock. A signal is read at the close and bought at the next day\'s open. '
                'Use it to see whether the rules you wrote find what you meant — and whether it was worth finding.</div></div></div>', unsafe_allow_html=True)
    a, b, c, d = st.columns([2, 1.4, 1, 1])
    i = a.selectbox("Setup", range(len(setups)), index=ids.index(cur) if cur in ids else 0, key="ss_bt_pick", format_func=lambda k: setups[k]["name"])
    setup = setups[i]
    rules = [r for r in setup_rules(setup) if r.get("on", True)]
    uni = b.selectbox("Stocks", ["NIFTY 500", "All NSE stocks", "Your watchlist", "Arka watchlist", "Your portfolio"], key="ss_bt_uni")
    sessions = c.selectbox("Look back", [60, 120, 200], index=1, key="ss_bt_n", format_func=lambda n: f"{n} sessions")
    liq = d.number_input("Min turnover ₹ Cr/day", 0.0, 500.0, 1.0, 0.5, key="ss_bt_liq")
    pmin, pmax = float(setup.get("price_min") or 0), float(setup.get("price_max") or 99999)
    tol = TOLERANCES[st.selectbox("Close enough", list(TOLERANCES), index=list(TOLERANCES).index(tol_label(setup_tol(setup)))
                                  if tol_label(setup_tol(setup)) in TOLERANCES else 1, key=f"ss_bt_tol_{setup['id']}")]
    if not rules:
        st.warning("This setup has no rules yet.")
        return
    st.caption(f'{len(rules)} rules · price band ₹{pmin:,.0f}–{pmax:,.0f} (checked on each past day) · about half a minute per 500 stocks at 120 sessions (a few times longer with either-or groups or pass-K-of-N setups), '
               "plus the price download the first time.")
    if st.button(f"Run backtest · {setup['name']}", type="primary", use_container_width=True, key="ss_bt_run"):
        universe, src = get_universe(uni)
        if not universe:
            st.warning(f"{uni} is empty.")
            return
        bar, msg = st.progress(0.0), st.empty()

        def prog(p, m):
            bar.progress(max(0.0, min(1.0, float(p))))
            msg.caption(m)
        need = _needs(rules)
        snaps, band, hist = _candidates(universe, pmin, pmax, float(liq), lambda p, m: prog(p * 0.5, m), widen=2.0, long=need["long"])
        bt = backtest({x: hist[x] for x in band if x in hist}, rules, pmin, pmax, float(liq), int(sessions), setup_min_pass(setup),
                      nifty_close() if need["nifty"] else None, delivery_table() if need["deliv"] else None,
                      progress=lambda p, m: prog(0.5 + 0.5 * p, m), tol=tol)
        bt.update(setup_name=setup["name"], source=src, band=(pmin, pmax), deliv=need["deliv"])
        st.session_state["ss_bt"] = bt
        bar.empty()
        msg.empty()
        st.rerun()
    bt = st.session_state.get("ss_bt")
    if not bt:
        return
    S = bt["stats"]
    st.markdown(f'<div class="ss-hero"><div><div class="t"><em>Result</em>{_e(bt["setup_name"])}</div><div class="d">{_e(bt["source"])} · '
                f'{S["stocks"]:,} stocks · last {S["sessions"]} sessions · {S["days"]:,} stock-days checked in {S["secs"]:.0f}s</div></div></div>', unsafe_allow_html=True)
    per_month = S["signals"] / max(1, S["sessions"]) * 21
    st.markdown('<div class="ss-kpis">' + _kpi("Signals", f'{S["signals"]:,}', AMBER) + _kpi("Different stocks", f'{S["names"]:,}') +
                _kpi("Signals per month", f"{per_month:.1f}") +
                _kpi("Average worst dip, 20 days", "—" if S["avg_worst"] is None else f'<span style="color:{RED}">{S["avg_worst"]:.1f}%</span>', RED) + '</div>',
                unsafe_allow_html=True)
    if not S["signals"]:
        st.info("The setup never triggered in this period. It is too strict as written: run a scan and open 'Which rule removes the most stocks', "
                "or widen the ranges and try again.")
        return
    rows = []
    for hz in HORIZONS:
        m, base = bt["summary"][hz], bt["base"][hz]
        if not m:
            rows.append([f"{hz} sessions later", "0", "—", "—", "—", "—", "—", "—"])
            continue
        edge = None if base is None else m["mean"] - base
        rows.append([f"{hz} sessions later", f'{m["n"]:,}', f'{m["win"]:.0f}%', _pc(m["mean"]), _pc(m["median"]), _pc(base, 2),
                     "—" if edge is None else f'<b style="color:{GREEN if edge > 0 else RED}">{edge:+.1f} pp</b>', _pc(m["worst"])])
    st.markdown(_simple_table(["After the signal", "Signals", "Rose", "Average", "Middle (median)", "Any stock, any day", "Setup minus any day", "Worst"], rows),
                unsafe_allow_html=True)
    n20 = (bt["summary"][20] or {}).get("n", 0)
    st.caption("'Any stock, any day' is the average result of buying every stock in the same list on every day of the same period: the setup is only "
               "useful if it beats that. " + (f"Only {n20} completed signals: too few to trust the percentages. " if n20 < 30 else "")
               + "Not included: brokerage, slippage, and stocks that were delisted. One period of one market — the next one can differ."
               + (" Delivery data only covers recent sessions, so older days could not trigger." if bt.get("deliv") else ""))
    st.markdown('<div class="ss-rh" style="margin:12px 0 6px">Every signal, newest first</div>', unsafe_allow_html=True)
    sig = bt["signals"]
    show = sig[:150]
    st.markdown(_simple_table(["Stock", "Signal date", "Close", "Entry (next open)", "+5", "+10", "+20", "Worst dip 20d", "Till now"],
                              [[f'<b style="color:{AMBER}">{_e(x["symbol"])}</b>', f'{x["date"]:%d %b %Y}', f'₹{x["close"]:,.2f}',
                                "waiting for next open" if x["entry"] is None else f'₹{x["entry"]:,.2f}', _pc(x.get("r5")), _pc(x.get("r10")), _pc(x.get("r20")),
                                _pc(x.get("worst")), _pc(x.get("now"))] for x in show]), unsafe_allow_html=True)
    if len(sig) > len(show):
        st.caption(f"Showing the newest {len(show)} of {len(sig)} signals. The download has all of them.")
    out = pd.DataFrame([{"symbol": x["symbol"], "signal_date": f'{x["date"]:%Y-%m-%d}', "close": round(x["close"], 2),
                         "entry_next_open": None if x["entry"] is None else round(x["entry"], 2),
                         **{f"ret_{h}d_pct": None if x.get(f"r{h}") is None else round(x[f"r{h}"], 2) for h in HORIZONS},
                         "worst_dip_20d_pct": None if x.get("worst") is None else round(x["worst"], 2)} for x in sig])
    st.download_button("Download all signals (CSV)", out.to_csv(index=False), f"arka_backtest_{datetime.now(IST):%Y%m%d}.csv", "text/csv", key="ss_bt_dl")


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
    if st.session_state.get("ss_view") not in ("RUN SCAN", "SETUPS", "BACKTEST"):
        st.session_state["ss_view"] = "RUN SCAN"
    view = st.radio("Screener view", ["RUN SCAN", "SETUPS", "BACKTEST"], horizontal=True, key="ss_view", label_visibility="collapsed",
                    format_func=lambda v: {"RUN SCAN": "Scan", "SETUPS": "My setups", "BACKTEST": "Backtest"}[v])
    if view == "RUN SCAN":
        _view_scan(supabase, is_admin, gemini_key)
    elif view == "BACKTEST":
        _view_backtest(supabase, is_admin)
    else:
        _view_setups(supabase, is_admin, gemini_key)
    st.caption("Technical scanner for research — not investment advice. Prices: Yahoo Finance, delayed.")
