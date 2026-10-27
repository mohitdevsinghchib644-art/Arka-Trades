"""
arka_ai.py — Arka AI v9 · YOUR playbook, YOUR zones, YOUR trade plan

You teach Arka how YOU trade. Then you type a stock and it answers with a complete, marked-up analysis.

HOW IT WORKS (and why it is accurate)
  TEACH     Four ways to teach, all saved to your playbook (Pinecone, same index as before):
              · Import a strategy PDF  - Gemini reads text, diagrams and examples into a structured setup
              · Label zones            - Arka auto-detects supply/demand zones on any chart; you tick the ones that are
                                         "your kind" and cross the ones that are not. It learns your style from that.
              · Teach from a screenshot- upload a chart you marked up (zones, lines, entries) and explain it
              · Quick rules            - type a setup in plain English
  ANALYZE   Type a stock. A deterministic Python engine finds EVERY supply / demand zone, support / resistance level,
            trendline, breakout, retest and candle signal with exact candle indices and prices (calibrated to the
            style you taught). Gemini then judges them against YOUR playbook, picks which ones are "yours", writes
            the trade plan and explains why. The model never invents coordinates: it can only choose detection IDs,
            and Python draws them. If Gemini is offline the engine still produces zones, levels and a plan.
  OUTPUT    Verdict + grade + score, setup scorecard (each of your rules: present / not present / unclear),
            entry zone / stop / targets / R:R / position size, supports vs contradictions, bull-bear-neutral
            scenarios, and an interactive annotated chart (plus a PNG you can download and share).
  RADAR     Scan your whole watchlist with the same engine and see which stocks are sitting at YOUR zones today.

Honest limits: zones and levels are rule-based approximations of discretionary chart reading; Yahoo data is delayed and
may contain gaps; scores measure how well evidence matches your playbook, not the probability of profit.
This is research tooling, not investment advice.
"""
from __future__ import annotations

import base64
import html as _html
import io
import json
import math
import os
import re
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ── Safe imports ────────────────────────────────────────────
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except Exception:  # pragma: no cover
    genai = None
    HAS_GEMINI = False

try:
    from pinecone import Pinecone, ServerlessSpec
    HAS_PINECONE = True
except Exception:  # pragma: no cover
    HAS_PINECONE = False

try:
    import pdfplumber
    HAS_PDF = True
except Exception:  # pragma: no cover
    HAS_PDF = False

try:
    import yfinance as yf
    HAS_YFINANCE = True
except Exception:  # pragma: no cover
    yf = None
    HAS_YFINANCE = False

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except Exception:  # pragma: no cover
    go = None
    make_subplots = None
    HAS_PLOTLY = False


def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


GEMINI_KEY = _secret("GEMINI_KEY")
PINECONE_KEY = _secret("PINECONE_KEY")
INDEX_NAME = "arka-trading-rules"          # unchanged: your v8 setups are still readable
V9_VERSION = "arka-v9"
IST = timezone(timedelta(hours=5, minutes=30))

# ── Theme (matches the terminal) ────────────────────────────
AMBER, AMBER2 = "#FF9F0A", "#FFB340"
GREEN, RED, CYAN, PURPLE, YELLOW = "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
BG, S1, S2, S3 = "#050505", "#0A0A0A", "#0F0F0F", "#151515"
LINE, LINE2 = "#1F1F1F", "#2B2B2B"
IVORY, T2, T3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace"
FONT = "'Inter',system-ui,sans-serif"

KIND_LABEL = {"setup": "SETUP", "lesson": "LESSON", "feedback": "LABELLED ZONES", "style": "ZONE STYLE"}

# How the engine reads a chart. Learned values (from your labelled zones) override these.
DEFAULT_STYLE = {
    "pivot_left": 3, "pivot_right": 3,          # swing high / low confirmation
    "base_max": 4,                              # most candles allowed in a base
    "base_range_atr": 1.0,                      # a base candle's range must be <= this x ATR
    "impulse_atr": 1.6,                         # the move away from the base must be >= this x ATR
    "impulse_window": 3,                        # ... within this many candles
    "max_zone_atr": 2.2,                        # zone thickness cap (x ATR)
    "min_quality": 40,                          # hide zones scoring below this
    "sr_tol_atr": 0.6,                          # S/R clustering tolerance (x ATR)
    "sr_min_touches": 2,
    "lookback": 220,
    "fresh_only": False,
}

STYLE_HELP = {
    "base_max": "Maximum candles in a base (consolidation) before the move",
    "base_range_atr": "Largest candle range allowed inside a base, in ATR",
    "impulse_atr": "How far price must leave the base, in ATR, to count as an impulse",
    "max_zone_atr": "Thickest zone you accept, in ATR (thinner = tighter risk)",
    "min_quality": "Hide zones scoring below this quality (0-100)",
    "sr_tol_atr": "Prices within this many ATR are treated as one support/resistance level",
    "sr_min_touches": "Touches needed before a level is drawn",
}


def esc(x) -> str:
    return _html.escape("" if x is None else str(x))


# ══════════════════════════════════════════════════════════
# 1. PLAYBOOK STORAGE  (Pinecone, with a session fallback)
# ══════════════════════════════════════════════════════════
@st.cache_resource
def get_pinecone_index():
    if not HAS_PINECONE or not PINECONE_KEY:
        return None
    try:
        pc = Pinecone(api_key=PINECONE_KEY)
        existing = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in existing:
            pc.create_index(name=INDEX_NAME, dimension=768, metric="cosine",
                            spec=ServerlessSpec(cloud="aws", region="us-east-1"))
        return pc.Index(INDEX_NAME)
    except Exception:
        return None


def get_embedding(text):
    if not HAS_GEMINI or not GEMINI_KEY or not text or not str(text).strip():
        return None
    try:
        genai.configure(api_key=GEMINI_KEY)
        r = genai.embed_content(model="models/text-embedding-004", content=str(text).strip()[:8000])
        emb = r.get("embedding", [])
        return emb if len(emb) == 768 and any(abs(v) > 1e-7 for v in emb) else None
    except Exception:
        return None


_CONST_VEC = [0.001] * 768     # storage does not need real embeddings: items are read back by id, not by similarity


def _invalidate():
    st.session_state.pop("aa_items_cache", None)


def _decode(vector_id, meta: dict):
    """Pinecone metadata -> playbook item. Reads both v9 items and legacy v8 setups."""
    kind = meta.get("memory_kind")
    try:
        if kind == "setup" and "setup_json" in meta:                       # v8 / v9 setup
            payload = json.loads(meta["setup_json"])
            name = meta.get("setup_name") or payload.get("setup_name", "Unnamed setup")
        elif kind in KIND_LABEL and "item_json" in meta:                   # v9 lesson / feedback / style
            payload = json.loads(meta["item_json"])
            name = meta.get("name", kind)
        else:
            return None
    except Exception:
        return None
    return {"item_id": meta.get("setup_id") or meta.get("item_id") or vector_id, "kind": kind, "name": name,
            "active": bool(meta.get("active", False)), "saved_at": meta.get("saved_at", ""),
            "source": meta.get("source_name", ""), "payload": payload}


def _local_items():
    return st.session_state.setdefault("aa_local_items", {})


def load_items(force: bool = False) -> list:
    """Every playbook item, newest first. Cached for the session; writes invalidate the cache."""
    cache = st.session_state.get("aa_items_cache")
    if cache is not None and not force:
        return cache
    items = []
    idx = get_pinecone_index()
    if idx is not None:
        try:
            ids = []
            for batch in idx.list(limit=100):
                ids.extend(batch)
            for start in range(0, len(ids), 200):
                fetched = idx.fetch(ids=ids[start:start + 200])
                vectors = getattr(fetched, "vectors", None)
                if vectors is None and isinstance(fetched, dict):
                    vectors = fetched.get("vectors", {})
                for vid, vec in (vectors or {}).items():
                    meta = vec.metadata if hasattr(vec, "metadata") else vec.get("metadata", {})
                    it = _decode(vid, meta or {})
                    if it:
                        items.append(it)
        except Exception:
            pass
    items += list(_local_items().values())
    items.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    st.session_state["aa_items_cache"] = items
    return items


def storage_mode() -> str:
    return "CLOUD" if get_pinecone_index() is not None else "SESSION"


def save_item(kind: str, name: str, payload: dict, activate: bool = True, source_name: str = "", source_text: str = "") -> bool:
    name = (name or "").strip() or KIND_LABEL.get(kind, kind).title()
    item_id = f"{kind}_{int(time.time() * 1000)}"
    saved_at = datetime.now(timezone.utc).isoformat()
    idx = get_pinecone_index()
    if idx is None:
        _local_items()[item_id] = {"item_id": item_id, "kind": kind, "name": name, "active": activate, "saved_at": saved_at,
                                   "source": source_name, "payload": payload}
        if activate and kind == "style":
            for it in _local_items().values():
                if it["kind"] == "style" and it["item_id"] != item_id:
                    it["active"] = False
        _invalidate()
        return True
    try:
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(compact) > 34000:
            st.error("This item is too large to store (over 34 KB). Trim it or split the document.")
            return False
        emb = get_embedding(name + "\n" + str(payload.get("summary", "")) + "\n" + compact[:4000]) or _CONST_VEC
        meta = {"memory_kind": kind, "item_id": item_id, "name": name, "active": bool(activate), "saved_at": saved_at,
                "source_name": source_name, "source_text": str(source_text or "")[:3000], "version": V9_VERSION}
        if kind == "setup":
            meta.update({"setup_id": item_id, "setup_name": name, "setup_json": compact})
        else:
            meta["item_json"] = compact
        idx.upsert(vectors=[{"id": item_id, "values": emb, "metadata": meta}])
        if activate and kind == "style":                           # only one active style at a time
            for it in load_items(force=True):
                if it["kind"] == "style" and it["item_id"] != item_id and it["active"]:
                    set_item_active(it["item_id"], False)
        _invalidate()
        return True
    except Exception as exc:
        st.error(f"Could not save to your playbook: {exc}")
        return False


def set_item_active(item_id: str, active: bool) -> bool:
    local = _local_items()
    if item_id in local:
        local[item_id]["active"] = active
        _invalidate()
        return True
    idx = get_pinecone_index()
    if idx is None:
        return False
    try:
        idx.update(id=item_id, set_metadata={"active": bool(active)})
        _invalidate()
        return True
    except Exception:
        return False


def delete_item(item_id: str) -> bool:
    local = _local_items()
    if item_id in local:
        local.pop(item_id)
        _invalidate()
        return True
    idx = get_pinecone_index()
    if idx is None:
        return False
    try:
        idx.delete(ids=[item_id])
        _invalidate()
        return True
    except Exception:
        return False


def active_items(kind: str) -> list:
    return [i for i in load_items() if i["kind"] == kind and i["active"]]


def get_style() -> dict:
    """Engine parameters: defaults, overridden by the active learned / saved zone style."""
    style = dict(DEFAULT_STYLE)
    for it in active_items("style"):
        style.update({k: v for k, v in it["payload"].get("params", {}).items() if k in DEFAULT_STYLE})
        break
    return style


def style_source() -> str:
    for it in active_items("style"):
        return it["name"]
    return "DEFAULT"


# ══════════════════════════════════════════════════════════
# 2. GEMINI
# ══════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are Arka AI, the personal trading analyst of ONE trader. Your only source of strategy is that trader's PLAYBOOK
(setups, lessons about how they draw supply and demand zones and lines, and zones they approved or rejected).

RULES YOU MUST FOLLOW
1. Never invent a strategy. Do not use indicators, patterns or criteria the playbook does not use. You may add clearly separated
   "context" observations, but they must not change the setup verdict.
2. All numbers come from the DETECTIONS and MARKET FACTS supplied by Python. Never invent prices, dates, candle indices or volumes.
3. Never output pixel coordinates. To draw something, return the ID of a supplied detection (for example DZ1, SZ2, L3, TL1).
4. If the evidence cannot decide a rule, say UNCLEAR. Do not guess.
5. A score is how well the evidence matches the playbook. It is NOT a probability of profit.
6. Be direct and specific: exact prices, exact dates, exact detection IDs. Plain language, no hype.
7. Reply with ONE JSON object and nothing else."""


def get_gemini():
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    try:
        genai.configure(api_key=GEMINI_KEY)
        return genai.GenerativeModel(model_name="gemini-2.5-flash", system_instruction=SYSTEM_PROMPT)
    except Exception:
        return None


def _extract_json(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    a, b = text.find("{"), text.rfind("}")
    return text[a:b + 1] if a >= 0 and b > a else text


def parse_json_loose(raw: str) -> dict:
    """strict parse -> outermost {...} -> trailing-comma repair. One local pass, no extra API call."""
    for candidate in (raw or "", _extract_json(raw)):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    repaired = re.sub(r",\s*([}\]])", r"\1", _extract_json(raw))
    return json.loads(repaired)


def call_json(prompt: str, images=None, uploaded_file=None) -> dict:
    """images: list of PNG bytes."""
    model = get_gemini()
    if model is None:
        raise RuntimeError("Gemini is not configured (check GEMINI_KEY and the google-generativeai package).")
    content = [prompt]
    if uploaded_file is not None:
        content.append(uploaded_file)
    for png in images or []:
        content.append({"mime_type": "image/png", "data": png})
    try:
        resp = model.generate_content(content, generation_config={"response_mime_type": "application/json", "temperature": 0.2})
    except Exception:
        resp = model.generate_content(content)                      # older SDKs without response_mime_type
    return parse_json_loose(getattr(resp, "text", "") or "")


def call_text(prompt: str) -> str:
    model = get_gemini()
    if model is None:
        raise RuntimeError("Gemini is not configured.")
    resp = model.generate_content([prompt])
    return (getattr(resp, "text", "") or "").strip()


# ══════════════════════════════════════════════════════════
# 3. MARKET DATA
# ══════════════════════════════════════════════════════════
_SYMBOL_MAP = {"NIFTY50": "^NSEI", "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYBANK": "^NSEBANK",
               "SENSEX": "^BSESN", "FINNIFTY": "NIFTY_FIN_SERVICE.NS", "NIFTYIT": "^CNXIT"}


def yahoo_symbols(ticker: str) -> list:
    t = (ticker or "").upper().strip().replace(" ", "")
    if t in _SYMBOL_MAP:
        return [_SYMBOL_MAP[t]]
    if t.startswith("^") or t.endswith(".NS") or t.endswith(".BO"):
        return [t]
    return [t + ".NS", t + ".BO"]


def _clean_ohlc(hist) -> pd.DataFrame:
    df = hist.rename(columns={c: str(c).lower() for c in hist.columns})
    need = ["open", "high", "low", "close"]
    if any(c not in df for c in need):
        raise ValueError("price columns missing")
    if "volume" not in df:
        df["volume"] = 0.0
    df = df[need + ["volume"]].astype(float)
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].dropna(subset=need).sort_index()
    df["volume"] = df["volume"].fillna(0.0)
    return df


@st.cache_data(ttl=300, show_spinner=False, max_entries=64)
def fetch_ohlc(ticker: str, interval: str = "1d", period: str = "2y") -> pd.DataFrame:
    """Daily / weekly OHLCV. Raises (so failures are never cached) when nothing usable comes back."""
    if not HAS_YFINANCE:
        raise RuntimeError("yfinance is not installed")
    last_err = None
    for sym in yahoo_symbols(ticker):
        try:
            hist = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=True)
            if hist is not None and len(hist) >= 40:
                return _clean_ohlc(hist)
        except Exception as exc:
            last_err = exc
    raise ValueError(f"No usable {interval} price history for {ticker}" + (f" ({last_err})" if last_err else ""))


def _split_download(raw, tickers):
    out = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in set(raw.columns.get_level_values(0)):
            raw = raw.swaplevel(axis=1).sort_index(axis=1)
        present = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in present:
                out[t] = raw[t]
    elif len(tickers) == 1:
        out[tickers[0]] = raw
    clean = {}
    for t, f in out.items():
        try:
            df = _clean_ohlc(f)
            if len(df) >= 60:
                clean[t] = df
        except Exception:
            continue
    return clean


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def fetch_many(symbols: tuple) -> dict:
    """Batched daily download for the radar. {symbol: DataFrame}."""
    if not HAS_YFINANCE:
        raise RuntimeError("yfinance is not installed")
    tick = [s + ".NS" for s in symbols]
    raw = yf.download(tick, period="1y", interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=True)
    frames = _split_download(raw, tick)
    return {t[:-3]: f for t, f in frames.items()}


# ══════════════════════════════════════════════════════════
# 4. DETERMINISTIC ENGINE  (pure functions: no network, no Streamlit)
# ══════════════════════════════════════════════════════════
def _f(x, nd=2):
    try:
        v = float(x)
        return round(v, nd) if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def atr_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi_series(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(~((dn == 0) & up.notna()), 100.0)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["atr"] = atr_series(d)
    d["rsi"] = rsi_series(d.close)
    for p in (20, 50, 200):
        d[f"sma{p}"] = d.close.rolling(p, min_periods=p).mean()
    d["vavg"] = d.volume.rolling(20, min_periods=5).mean()
    return d


def find_pivots(df: pd.DataFrame, left: int = 3, right: int = 3):
    h, l, n = df.high.values, df.low.values, len(df)
    ph, pl = [], []
    for i in range(left, n - right):
        wh, wl = h[i - left:i + right + 1], l[i - left:i + right + 1]
        if h[i] >= wh.max() and int(np.argmax(wh)) == left:
            ph.append((i, float(h[i])))
        if l[i] <= wl.min() and int(np.argmin(wl)) == left:
            pl.append((i, float(l[i])))
    return ph, pl


# ── support / resistance ────────────────────────────────────
def build_levels(df, ph, pl, atr_now, style):
    n, price = len(df), float(df.close.iloc[-1])
    tol = max(style["sr_tol_atr"] * atr_now, price * 0.003)
    pts = sorted([(p, i, "H") for i, p in ph] + [(p, i, "L") for i, p in pl])
    clusters = []
    for p, i, k in pts:
        if clusters and abs(p - float(np.mean([x[0] for x in clusters[-1]]))) <= tol:
            clusters[-1].append((p, i, k))
        else:
            clusters.append([(p, i, k)])
    raw = []
    for cl in clusters:
        if len(cl) < style["sr_min_touches"]:
            continue
        prices, idxs = [x[0] for x in cl], [x[1] for x in cl]
        lvl = float(np.median(prices))
        raw.append({"price": round(lvl, 2), "touches": len(cl), "first": int(min(idxs)), "last": int(max(idxs)),
                    "flip": len({x[2] for x in cl}) == 2,
                    "score": round(sum(0.5 + 0.5 * (i / max(1, n - 1)) for i in idxs), 2),
                    "dist_pct": round((lvl - price) / price * 100, 2), "dist_atr": round((lvl - price) / atr_now, 2) if atr_now else None})
    above = sorted([x for x in raw if x["price"] > price], key=lambda x: x["price"])[:5]
    below = sorted([x for x in raw if x["price"] <= price], key=lambda x: -x["price"])[:5]
    for k, x in enumerate(above, 1):
        x.update({"id": f"R{k}", "kind": "resistance"})
    for k, x in enumerate(below, 1):
        x.update({"id": f"S{k}", "kind": "support"})
    return below + above


# ── supply & demand zones ───────────────────────────────────
def _zone_quality(dep, impa, base_n, thick_atr, tests, vr, bos, aligned):
    pts = 30 * min(1.0, dep / (impa * 2.5))
    pts += {1: 15, 2: 12, 3: 8}.get(base_n, 5)
    pts += 20 if tests == 0 else 10 if tests == 1 else 4 if tests == 2 else 0
    if vr is not None and np.isfinite(vr):
        pts += 10 if vr >= 2 else 7 if vr >= 1.5 else 4 if vr >= 1.2 else 0
    pts += 10 if thick_atr <= 0.8 else 7 if thick_atr <= 1.4 else 4 if thick_atr <= 2.0 else 1
    pts += 8 if bos else 0
    pts += 7 if aligned else 0
    return int(round(min(100, pts)))


def detect_zones(df: pd.DataFrame, style: dict) -> list:
    """Base -> impulse zones. Demand = base followed by a strong up move; supply = base followed by a strong down move.
    Distal line = far edge of the base (wick), proximal line = near edge (body). Status is evaluated candle by candle."""
    n = len(df)
    if n < 40:
        return []
    o, h, l, c, v = (df[k].values for k in ("open", "high", "low", "close", "volume"))
    atr, vavg, sma50, rng = df.atr.values, df.vavg.values, df.sma50.values, df.high.values - df.low.values
    bmax, brng, impa, W = int(style["base_max"]), float(style["base_range_atr"]), float(style["impulse_atr"]), int(style["impulse_window"])
    raw = []
    for k in range(max(5, n - int(style["lookback"]), bmax + 1), n):
        e = k - 1
        a = atr[e]
        if not np.isfinite(a) or a <= 0 or abs(c[k] - o[k]) < 0.55 * a:
            continue
        m, j = 0, e
        while j >= 0 and m < bmax and rng[j] <= brng * a:
            m += 1
            j -= 1
        if m == 0:
            continue
        s, w_end, up = e - m + 1, min(n - 1, k + W - 1), c[k] > o[k]
        if up:
            dep = (c[k:w_end + 1].max() - h[s:e + 1].max()) / a
            distal, proximal = float(l[s:e + 1].min()), float(np.maximum(o[s:e + 1], c[s:e + 1]).max())
            proximal = max(proximal, distal + 0.1 * a)
            imp_end = k + int(np.argmax(c[k:w_end + 1]))
        else:
            dep = (l[s:e + 1].min() - c[k:w_end + 1].min()) / a
            distal, proximal = float(h[s:e + 1].max()), float(np.minimum(o[s:e + 1], c[s:e + 1]).min())
            proximal = min(proximal, distal - 0.1 * a)
            imp_end = k + int(np.argmin(c[k:w_end + 1]))
        thick = abs(proximal - distal)
        if dep < impa or thick > float(style["max_zone_atr"]) * a:
            continue
        zt = "demand" if up else "supply"
        lin = (c[s - 1] - c[s - 4]) / a if s >= 4 else 0.0
        if up:
            pattern = "RBR" if lin > 0.8 else "DBR" if lin < -0.8 else "BR"
        else:
            pattern = "RBD" if lin > 0.8 else "DBD" if lin < -0.8 else "BD"
        vr = float(v[k:w_end + 1].max() / vavg[e]) if np.isfinite(vavg[e]) and vavg[e] > 0 else None
        pw = slice(max(0, s - 15), s)
        bos = bool(s > 0 and ((up and c[imp_end] > h[pw].max()) or ((not up) and c[imp_end] < l[pw].min())))
        aligned = bool(s > 0 and np.isfinite(sma50[s - 1]) and ((up and c[s - 1] > sma50[s - 1]) or ((not up) and c[s - 1] < sma50[s - 1])))
        # walk forward: touches and break
        tests, in_touch, broken = 0, False, False
        for t in range(imp_end + 1, n):
            if up:
                if c[t] < distal:
                    broken = True
                    break
                inside = l[t] <= proximal
            else:
                if c[t] > distal:
                    broken = True
                    break
                inside = h[t] >= proximal
            if inside and not in_touch:
                tests += 1
            in_touch = inside
        if broken:
            continue
        tests_prior = tests - 1 if in_touch else tests
        status = "IN ZONE" if in_touch else "FRESH" if tests == 0 else f"TESTED {tests}x"
        q = _zone_quality(dep, impa, m, thick / a, tests_prior, vr, bos, aligned)
        top, bottom = max(proximal, distal), min(proximal, distal)
        raw.append({"type": zt, "pattern": pattern, "s": int(s), "e": int(e), "imp": int(imp_end), "proximal": round(proximal, 2),
                    "distal": round(distal, 2), "top": round(top, 2), "bottom": round(bottom, 2), "thickness_atr": round(thick / a, 2),
                    "base_n": int(m), "dep_atr": round(float(dep), 2), "vol_ratio": _f(vr), "bos": bos, "aligned": aligned,
                    "tests": int(tests_prior), "status": status, "in_zone": bool(in_touch), "quality": q,
                    "formed": str(df.index[e])[:10], "age": int(n - 1 - imp_end)})
    raw = [z for z in raw if z["quality"] >= style["min_quality"] and not (style["fresh_only"] and z["tests"] > 0)]
    kept = []
    for z in sorted(raw, key=lambda x: -x["quality"]):                         # drop overlapping duplicates, strongest wins
        dup = False
        for k in kept:
            if k["type"] != z["type"]:
                continue
            inter = min(k["top"], z["top"]) - max(k["bottom"], z["bottom"])
            if inter > 0.5 * min(k["top"] - k["bottom"], z["top"] - z["bottom"]):
                dup = True
                break
        if not dup:
            kept.append(z)
    price, a_now = float(c[-1]), float(atr[-1])
    for z in kept:
        if z["in_zone"] or z["bottom"] <= price <= z["top"]:
            d = 0.0
        elif z["type"] == "demand":
            d = price - z["top"]
        else:
            d = z["bottom"] - price
        z["dist_pct"] = round(d / price * 100, 2)
        z["dist_atr"] = round(d / a_now, 2) if a_now else None
    out = []
    for zt, prefix in (("demand", "DZ"), ("supply", "SZ")):
        zs = sorted([z for z in kept if z["type"] == zt], key=lambda x: (x["dist_atr"] if x["dist_atr"] is not None else 99, -x["quality"]))
        for k, z in enumerate(zs, 1):
            z["id"] = f"{prefix}{k}"
            out.append(z)
    return out


# ── trendlines ──────────────────────────────────────────────
def detect_trendlines(df, ph, pl, atr_now):
    n = len(df)
    c = df.close.values
    tol = 0.45 * atr_now
    out = []
    for kind, piv in (("support", pl), ("resistance", ph)):
        cand, best = piv[-7:], None
        for a_i in range(len(cand) - 1):
            for b_i in range(a_i + 1, len(cand)):
                (i0, p0), (i1, p1) = cand[a_i], cand[b_i]
                if i1 - i0 < 8:
                    continue
                slope = (p1 - p0) / (i1 - i0)
                if (kind == "support" and slope <= 0) or (kind == "resistance" and slope >= 0):
                    continue
                ys = p0 + slope * (np.arange(i0, n) - i0)
                if (kind == "support" and (c[i0:] < ys - tol).any()) or (kind == "resistance" and (c[i0:] > ys + tol).any()):
                    continue
                touches = sum(1 for (i, p) in cand if i >= i0 and abs(p - (p0 + slope * (i - i0))) <= tol)
                if touches < 3:
                    continue
                score = touches * 2 + (i1 / n) * 3
                if best is None or score > best[0]:
                    best = (score, i0, p0, slope, touches)
        if best:
            _, i0, p0, slope, touches = best
            now = p0 + slope * (n - 1 - i0)
            out.append({"kind": kind, "i0": int(i0), "p0": round(p0, 2), "i1": int(n - 1), "p1": round(now, 2),
                        "touches": int(touches), "slope_pct_per_bar": round(slope / float(c[-1]) * 100, 3),
                        "dist_pct": round((float(c[-1]) - now) / float(c[-1]) * 100, 2)})
    for k, t in enumerate(out, 1):
        t["id"] = f"TL{k}"
    return out


# ── events, candle signals, structure ───────────────────────
def detect_events(df, levels, zones, atr_now):
    n = len(df)
    c, l, v, vavg = df.close.values, df.low.values, df.volume.values, df.vavg.values
    ev = []
    for lv in levels:
        p = lv["price"]
        for t in range(max(1, n - 6), n):
            up, dn = c[t - 1] <= p < c[t], c[t - 1] >= p > c[t]
            if not (up or dn):
                continue
            vr = float(v[t] / vavg[t - 1]) if np.isfinite(vavg[t - 1]) and vavg[t - 1] > 0 else None
            retest = None
            if up:
                retest = any(l[u] <= p + 0.5 * atr_now and c[u] >= p for u in range(t + 1, n)) if t < n - 1 else None
            ev.append({"type": "BREAKOUT" if up else "BREAKDOWN", "level_id": lv["id"], "price": p, "i": int(t),
                       "date": str(df.index[t])[:10], "vol_ratio": _f(vr), "retest_held": retest if up else None})
    for z in zones:
        if z["in_zone"]:
            ev.append({"type": "PRICE_IN_" + z["type"].upper() + "_ZONE", "zone_id": z["id"], "i": n - 1, "date": str(df.index[-1])[:10]})
    return ev


def candle_signals(df):
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n, out = len(df), []
    for i in range(max(1, n - 3), n):
        body, rng = abs(c[i] - o[i]), h[i] - l[i]
        if rng <= 0:
            continue
        lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
        if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1]:
            out.append({"type": "BULLISH_ENGULFING", "i": i, "bias": "bull"})
        if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
            out.append({"type": "BEARISH_ENGULFING", "i": i, "bias": "bear"})
        if lw >= 2 * max(body, 1e-9) and uw <= 0.35 * rng and body / rng < 0.4:
            out.append({"type": "HAMMER_REJECTION", "i": i, "bias": "bull"})
        if uw >= 2 * max(body, 1e-9) and lw <= 0.35 * rng and body / rng < 0.4:
            out.append({"type": "SHOOTING_STAR_REJECTION", "i": i, "bias": "bear"})
        if h[i] < h[i - 1] and l[i] > l[i - 1]:
            out.append({"type": "INSIDE_BAR", "i": i, "bias": "neutral"})
    for s in out:
        s["date"] = str(df.index[s["i"]])[:10]
    return out


def market_structure(ph, pl):
    if len(ph) >= 2 and len(pl) >= 2:
        hh, hl = ph[-1][1] > ph[-2][1], pl[-1][1] > pl[-2][1]
        if hh and hl:
            return "UPTREND (higher highs, higher lows)"
        if (not hh) and (not hl):
            return "DOWNTREND (lower highs, lower lows)"
        return "RANGE / TRANSITION"
    return "UNDEFINED"


def analyze_market(df_raw: pd.DataFrame, style: dict) -> dict:
    """Everything the engine can say about one timeframe, with exact candle indices and prices."""
    df = prepare(df_raw)
    n = len(df)
    price, atr_now = float(df.close.iloc[-1]), float(df.atr.iloc[-1]) if np.isfinite(df.atr.iloc[-1]) else float(df.close.iloc[-1]) * 0.02
    ph, pl = find_pivots(df, int(style["pivot_left"]), int(style["pivot_right"]))
    levels = build_levels(df, ph, pl, atr_now, style)
    zones = detect_zones(df, style)
    lines = detect_trendlines(df, ph, pl, atr_now)
    last = df.iloc[-1]
    vr20 = float(last.volume / last.vavg) if np.isfinite(last.vavg) and last.vavg > 0 else None
    sma = {p: _f(last[f"sma{p}"]) for p in (20, 50, 200)}
    s50, s50p = df.sma50.iloc[-1], df.sma50.iloc[-6] if n > 6 else np.nan
    det = {
        "n": n, "asof": str(df.index[-1])[:10], "close": round(price, 2), "atr": round(atr_now, 2), "atr_pct": round(atr_now / price * 100, 2),
        "day_chg_pct": round((price / float(df.close.iloc[-2]) - 1) * 100, 2) if n > 1 else 0.0,
        "structure": market_structure(ph, pl), "rsi": _f(last.rsi, 1), "sma": sma,
        "ma_state": ("ABOVE 20/50" if sma[20] and sma[50] and price > sma[20] and price > sma[50] else
                     "BELOW 20/50" if sma[20] and sma[50] and price < sma[20] and price < sma[50] else "MIXED"),
        "sma50_slope_atr": _f((s50 - s50p) / atr_now) if np.isfinite(s50) and np.isfinite(s50p) else None,
        "volume": {"last": int(last.volume), "ratio20": _f(vr20), "ratio5": _f(df.volume.tail(5).mean() / last.vavg) if np.isfinite(last.vavg) and last.vavg > 0 else None},
        "roc5_pct": _f((price / float(df.close.iloc[-6]) - 1) * 100) if n > 6 else None,
        "roc20_pct": _f((price / float(df.close.iloc[-21]) - 1) * 100) if n > 21 else None,
        "refs": {"pdh": _f(df.high.iloc[-2]) if n > 1 else None, "pdl": _f(df.low.iloc[-2]) if n > 1 else None,
                 "hi52": _f(df.high.tail(252).max()), "lo52": _f(df.low.tail(252).min()),
                 "hi20": _f(df.high.tail(20).max()), "lo20": _f(df.low.tail(20).min())},
        "swings": {"highs": [{"i": i, "date": str(df.index[i])[:10], "price": round(p, 2)} for i, p in ph[-4:]],
                   "lows": [{"i": i, "date": str(df.index[i])[:10], "price": round(p, 2)} for i, p in pl[-4:]]},
        "zones": zones, "levels": levels, "trendlines": lines,
        "events": detect_events(df, levels, zones, atr_now), "candles": candle_signals(df),
    }
    return det


def add_htf_confluence(det_d: dict, det_w: dict | None):
    """Mark daily zones that sit inside (or overlap) a weekly zone of the same type."""
    for z in det_d["zones"]:
        z["htf"] = False
        if not det_w:
            continue
        for w in det_w["zones"]:
            if w["type"] == z["type"] and min(z["top"], w["top"]) - max(z["bottom"], w["bottom"]) > 0:
                z["htf"] = True
                break


# ── learning your style from labelled zones ─────────────────
def zone_features(z: dict) -> dict:
    keys = ("type", "pattern", "thickness_atr", "base_n", "dep_atr", "vol_ratio", "quality", "tests", "bos", "aligned", "age")
    return {k: z.get(k) for k in keys}


def calibrate_style(approved: list, rejected: list):
    """Turn zones you approved / rejected into engine parameters. Needs at least 3 approved zones."""
    if len(approved) < 3:
        return None, ["Approve at least 3 zones so Arka has something to learn from."]
    arr = lambda key, src: np.array([x[key] for x in src if x.get(key) is not None], dtype=float)
    notes, params = [], {}
    th, bn, dp, ql = arr("thickness_atr", approved), arr("base_n", approved), arr("dep_atr", approved), arr("quality", approved)
    if len(th):
        params["max_zone_atr"] = float(np.clip(round(np.percentile(th, 80) * 1.15, 2), 0.8, 3.5))
        notes.append(f"Your zones are typically {np.median(th):.2f} ATR thick (80th percentile {np.percentile(th, 80):.2f}) -> max zone thickness {params['max_zone_atr']} ATR.")
    if len(bn):
        params["base_max"] = int(np.clip(math.ceil(np.percentile(bn, 80)), 1, 6))
        notes.append(f"Bases of {int(np.median(bn))} candle(s) are typical for you -> allow up to {params['base_max']}.")
    if len(dp):
        params["impulse_atr"] = float(np.clip(round(np.percentile(dp, 20) * 0.9, 2), 1.0, 4.0))
        notes.append(f"You favour moves of {np.median(dp):.1f} ATR away from the base -> minimum impulse {params['impulse_atr']} ATR.")
    if len(ql):
        params["min_quality"] = int(np.clip(round(np.percentile(ql, 20)) - 5, 25, 70))
        notes.append(f"Approved zones scored {int(np.percentile(ql, 20))}-{int(np.percentile(ql, 80))} -> hide anything below {params['min_quality']}.")
    fresh = np.mean([1.0 if x.get("tests", 0) == 0 else 0.0 for x in approved])
    if len(approved) >= 5 and fresh >= 0.8:
        params["fresh_only"] = True
        notes.append(f"{fresh:.0%} of your approved zones were untested -> showing fresh zones only.")
    if rejected:
        rth = arr("thickness_atr", rejected)
        if len(rth) and len(th) and np.median(rth) > np.median(th) * 1.3:
            notes.append(f"You rejected thicker zones (median {np.median(rth):.2f} ATR) than you approved ({np.median(th):.2f}).")
        rq = arr("quality", rejected)
        if len(rq) and len(ql) and np.median(rq) < np.median(ql) - 10:
            notes.append("Rejected zones scored clearly lower than approved ones: the quality score matches your taste.")
    return params, notes


# ── deterministic plan + score (works with no AI at all) ────
def deterministic_plan(det: dict):
    price, atr = det["close"], det["atr"]
    zones, levels = det["zones"], det["levels"]
    trend_up = det["structure"].startswith("UPTREND")
    trend_dn = det["structure"].startswith("DOWNTREND")

    def pick(zt):
        c = [z for z in zones if z["type"] == zt and z["dist_atr"] is not None and z["dist_atr"] <= 2.5]
        if not c:
            return None
        bonus = lambda z: z["quality"] - 6 * z["dist_atr"] + (8 if (zt == "demand" and trend_up) or (zt == "supply" and trend_dn) else 0) + (6 if z.get("htf") else 0)
        return max(c, key=bonus)

    zl, zs = pick("demand"), pick("supply")
    if not zl and not zs:
        return None
    choose_long = zl and (not zs or (zl["quality"] - 6 * zl["dist_atr"] + (8 if trend_up else 0)) >= (zs["quality"] - 6 * zs["dist_atr"] + (8 if trend_dn else 0)))
    z = zl if choose_long else zs
    long = bool(choose_long)
    ref = min(max(price, z["bottom"]), z["top"]) if z["in_zone"] else (z["top"] if long else z["bottom"])
    stop = round(z["distal"] - 0.25 * atr, 2) if long else round(z["distal"] + 0.25 * atr, 2)
    risk = abs(ref - stop)
    if risk <= 0:
        return None
    cands = []
    for lv in levels:
        if (long and lv["price"] > ref + risk) or ((not long) and lv["price"] < ref - risk):
            cands.append((lv["price"], lv["id"], "resistance" if long else "support"))
    for oz in zones:
        if long and oz["type"] == "supply" and oz["bottom"] > ref + risk:
            cands.append((oz["bottom"], oz["id"], "supply zone"))
        if (not long) and oz["type"] == "demand" and oz["top"] < ref - risk:
            cands.append((oz["top"], oz["id"], "demand zone"))
    cands.sort(key=lambda x: x[0] if long else -x[0])
    targets = [{"price": round(p, 2), "ref": r, "reason": f"Next {k}", "rr": round(abs(p - ref) / risk, 2)} for p, r, k in cands[:3]]
    for mult in (2, 3):
        if len(targets) < 2:
            p = ref + mult * risk if long else ref - mult * risk
            targets.append({"price": round(p, 2), "ref": f"{mult}R", "reason": f"{mult}R projection (no level found)", "rr": float(mult)})
    return {"direction": "LONG" if long else "SHORT", "ref_zone": z["id"], "entry_low": z["bottom"],
            "entry_high": z["top"], "entry_ref": round(ref, 2), "stop": stop, "stop_reason": f"Beyond {z['id']} distal line ({z['distal']:.2f}) minus 0.25 ATR",
            "targets": targets[:3], "counter_trend": bool((long and trend_dn) or ((not long) and trend_up)), "source": "engine"}


def engine_score(det: dict, plan: dict | None) -> int:
    if not plan:
        return 20 if det["structure"] == "RANGE / TRANSITION" else 28
    z = next((x for x in det["zones"] if x["id"] == plan["ref_zone"]), None)
    if not z:
        return 25
    long = plan["direction"] == "LONG"
    proximity = max(0.0, 100 - (z["dist_atr"] or 0) * 35)
    trend = 15 if plan["counter_trend"] else (100 if det["structure"].startswith("UPTREND" if long else "DOWNTREND") else 55)
    htf = 100 if z.get("htf") else 40
    want = "bull" if long else "bear"
    sig = any(s["bias"] == want and s["i"] >= det["n"] - 2 for s in det["candles"])
    vol = (det["volume"]["ratio20"] or 0) >= 1.2
    confirm = 100 if (sig and vol) else 65 if sig else 40 if vol else 20
    return int(round(0.40 * z["quality"] + 0.20 * proximity + 0.15 * trend + 0.10 * htf + 0.15 * confirm))


def grade_for(score: int) -> str:
    return "A+" if score >= 85 else "A" if score >= 72 else "B" if score >= 58 else "C" if score >= 45 else "D"


def position_size(plan: dict | None, risk_amount: float) -> dict:
    if not plan or risk_amount <= 0:
        return {}
    per_share = abs(plan["entry_ref"] - plan["stop"])
    if per_share <= 0:
        return {}
    qty = int(risk_amount // per_share)
    return {"risk_amount": risk_amount, "risk_per_share": round(per_share, 2), "qty": qty,
            "capital": round(qty * plan["entry_ref"], 2)}


# ══════════════════════════════════════════════════════════
# 5. WHAT TO DRAW  +  CHARTS
# ══════════════════════════════════════════════════════════
def build_spec(det: dict, draw: dict | None, plan: dict | None) -> dict:
    """Pick which detections go on the chart. `draw` holds IDs chosen by the AI; without it the engine picks the best ones."""
    zid = set((draw or {}).get("zones", []))
    lid = set((draw or {}).get("levels", []))
    tid = set((draw or {}).get("trendlines", []))
    if plan:
        zid.add(plan.get("ref_zone"))
        for t in plan.get("targets", []):
            (zid if str(t.get("ref", "")).startswith(("DZ", "SZ")) else lid).add(t.get("ref"))
    if not zid:
        for zt in ("demand", "supply"):
            zid |= {z["id"] for z in sorted([z for z in det["zones"] if z["type"] == zt], key=lambda x: (x["dist_atr"] or 99))[:3]}
    if not lid:
        near = sorted(det["levels"], key=lambda x: abs(x["dist_atr"] or 99))[:5]
        lid = {x["id"] for x in near}
    if not tid:
        tid = {x["id"] for x in det["trendlines"]}
    markers = [{"i": e["i"], "price": e["price"], "dir": "up" if e["type"] == "BREAKOUT" else "down", "label": e["type"].title()}
               for e in det["events"] if e["type"] in ("BREAKOUT", "BREAKDOWN")]
    return {"zones": [z for z in det["zones"] if z["id"] in zid], "levels": [x for x in det["levels"] if x["id"] in lid],
            "lines": [x for x in det["trendlines"] if x["id"] in tid], "markers": markers, "candles": det["candles"], "plan": plan}


def _rgba(hex_color: str, a: float) -> str:
    c = hex_color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{a})"


def _date_ticks(index, start, n, count=8):
    pos = list(range(start, n, max(1, (n - start) // count)))
    return pos, [str(index[p])[5:10] if (n - start) < 70 else str(index[p])[2:10] for p in pos]


def fig_plotly(df: pd.DataFrame, spec: dict, window: int = 120, show: dict | None = None):
    """Interactive chart: candles, MAs, volume, RSI, zones, levels, trendlines and the trade plan. Zoom, pan, hover."""
    if not HAS_PLOTLY:
        return None
    show = {"zones": True, "levels": True, "lines": True, "plan": True, "mas": True, "markers": True, **(show or {})}
    d = prepare(df)
    n = len(d)
    start = max(0, n - window)
    w = d.iloc[start:]
    xs = list(range(start, n))
    ext = max(8, int(len(w) * 0.14))
    xr = n + ext
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.018, row_heights=[0.68, 0.13, 0.19])
    dates = [str(i)[:10] for i in w.index]
    fig.add_trace(go.Candlestick(x=xs, open=w.open, high=w.high, low=w.low, close=w.close, name="Price", text=dates, hoverinfo="text+y",
                                 increasing=dict(line=dict(color=GREEN), fillcolor=GREEN), decreasing=dict(line=dict(color=RED), fillcolor=RED),
                                 showlegend=False), row=1, col=1)
    if show["mas"]:
        for col, name, color in (("sma20", "SMA 20", AMBER), ("sma50", "SMA 50", CYAN), ("sma200", "SMA 200", PURPLE)):
            if w[col].notna().any():
                fig.add_trace(go.Scatter(x=xs, y=w[col], mode="lines", name=name, line=dict(color=color, width=1.1), opacity=.8), row=1, col=1)
    fig.add_trace(go.Bar(x=xs, y=w.volume, marker_color=[_rgba(GREEN if c >= o else RED, .45) for o, c in zip(w.open, w.close)],
                         name="Volume", showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=xs, y=w.rsi, mode="lines", name="RSI 14", line=dict(color=AMBER, width=1.3), showlegend=False), row=3, col=1)
    for lvl in (30, 70):
        fig.add_shape(type="line", x0=start, x1=xr, y0=lvl, y1=lvl, line=dict(color="#3a3a3a", width=1, dash="dot"), row=3, col=1)

    ys = [float(w.low.min()), float(w.high.max())]
    if show["zones"]:
        for z in spec["zones"]:
            col = GREEN if z["type"] == "demand" else RED
            x0 = max(z["s"], start)
            fig.add_shape(type="rect", x0=x0, x1=xr, y0=z["bottom"], y1=z["top"], fillcolor=_rgba(col, .15), line=dict(color=_rgba(col, .9), width=1),
                          layer="below", row=1, col=1)
            fig.add_annotation(x=x0, y=z["top"] if z["type"] == "supply" else z["bottom"], xanchor="left", yanchor="bottom" if z["type"] == "supply" else "top",
                               text=f"<b>{z['id']}</b> {z['pattern']} · Q{z['quality']} · {z['status']}", showarrow=False, font=dict(size=10, color=col), row=1, col=1)
            if z["top"] >= ys[0] * .8 and z["bottom"] <= ys[1] * 1.2:
                ys += [z["top"], z["bottom"]]
    if show["levels"]:
        for lv in spec["levels"]:
            col = GREEN if lv["kind"] == "support" else RED
            fig.add_shape(type="line", x0=start, x1=xr, y0=lv["price"], y1=lv["price"], line=dict(color=_rgba(col, .75), width=1, dash="dot"), row=1, col=1)
            fig.add_annotation(x=xr, y=lv["price"], xanchor="right", yanchor="bottom", showarrow=False, font=dict(size=9, color=col),
                               text=f"{lv['id']} {lv['price']:,.2f} ({lv['touches']}x)", row=1, col=1)
            ys.append(lv["price"])
    if show["lines"]:
        for t in spec["lines"]:
            col = GREEN if t["kind"] == "support" else RED
            fig.add_trace(go.Scatter(x=[t["i0"], n - 1 + ext // 2], y=[t["p0"], t["p0"] + (t["p1"] - t["p0"]) / max(1, t["i1"] - t["i0"]) * (n - 1 + ext // 2 - t["i0"])],
                                     mode="lines", name=f"{t['id']} trendline", line=dict(color=col, width=1.6, dash="dash")), row=1, col=1)
    p = spec.get("plan")
    if show["plan"] and p:
        ref, stop = p["entry_ref"], p["stop"]
        x0, x1 = n - 1, n - 1 + int(ext * .8)
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=min(ref, stop), y1=max(ref, stop), fillcolor=_rgba(RED, .10), line=dict(width=0), row=1, col=1)
        t1 = p["targets"][0]["price"] if p["targets"] else ref
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=min(ref, t1), y1=max(ref, t1), fillcolor=_rgba(GREEN, .10), line=dict(width=0), row=1, col=1)
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=p["entry_low"], y1=p["entry_high"], fillcolor=_rgba(CYAN, .18), line=dict(color=CYAN, width=1), row=1, col=1)
        for price, label, col in [(stop, "STOP", RED)] + [(t["price"], f"T{i}", GREEN) for i, t in enumerate(p["targets"], 1)]:
            fig.add_shape(type="line", x0=x0, x1=x1, y0=price, y1=price, line=dict(color=col, width=1.4), row=1, col=1)
            fig.add_annotation(x=x1, y=price, xanchor="left", showarrow=False, text=f"<b>{label}</b> {price:,.2f}", font=dict(size=10, color=col), row=1, col=1)
            ys.append(price)
        fig.add_annotation(x=x1, y=(p["entry_low"] + p["entry_high"]) / 2, xanchor="left", showarrow=False, font=dict(size=10, color=CYAN),
                           text=f"<b>ENTRY</b> {p['entry_low']:,.2f}-{p['entry_high']:,.2f}", row=1, col=1)
    if show["markers"]:
        mk = [m for m in spec["markers"] if m["i"] >= start]
        if mk:
            fig.add_trace(go.Scatter(x=[m["i"] for m in mk], y=[m["price"] for m in mk], mode="markers+text", text=[m["label"] for m in mk],
                                     textposition="top center", textfont=dict(size=9, color=IVORY), name="Breakouts",
                                     marker=dict(symbol=["triangle-up" if m["dir"] == "up" else "triangle-down" for m in mk], size=11,
                                                 color=[GREEN if m["dir"] == "up" else RED for m in mk])), row=1, col=1)
        cs = [c for c in spec.get("candles", []) if c["i"] >= start and c["type"] != "INSIDE_BAR"]
        if cs:
            fig.add_trace(go.Scatter(x=[c["i"] for c in cs], y=[float(d.low.iloc[c["i"]]) * .995 if c["bias"] == "bull" else float(d.high.iloc[c["i"]]) * 1.005 for c in cs],
                                     mode="markers", name="Candle signals", hovertext=[c["type"].replace("_", " ").title() for c in cs], hoverinfo="text",
                                     marker=dict(symbol="diamond", size=8, color=[CYAN if c["bias"] == "bull" else AMBER for c in cs])), row=1, col=1)
    pad = (max(ys) - min(ys)) * 0.06
    pos, labels = _date_ticks(d.index, start, n)
    fig.update_layout(height=780, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Inter, sans-serif", size=11, color=T2), hovermode="x",
                      legend=dict(orientation="h", y=1.04, x=0, font=dict(size=10)),
                      hoverlabel=dict(bgcolor=S1, bordercolor=LINE2, font=dict(family="JetBrains Mono, monospace", size=11, color=IVORY)))
    fig.update_xaxes(showgrid=False, range=[start - 1, xr], rangeslider_visible=False, linecolor=LINE)
    fig.update_xaxes(tickvals=pos, ticktext=labels, row=3, col=1)
    fig.update_yaxes(gridcolor="#151515", zeroline=False)
    fig.update_yaxes(range=[min(ys) - pad, max(ys) + pad], row=1, col=1)
    fig.update_yaxes(range=[0, 100], row=3, col=1, tickvals=[30, 50, 70])
    return fig


def render_png(df: pd.DataFrame, spec: dict, window: int = 110, title: str = "", dpi: int = 120) -> bytes:
    """Annotated chart as PNG (download / share, and the image Gemini looks at). Needs only matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    d = prepare(df)
    n = len(d)
    start = max(0, n - window)
    w = d.iloc[start:]
    m = len(w)
    ext = max(8, int(m * .16))
    x = np.arange(m)
    fig = plt.figure(figsize=(14.5, 7.4), dpi=dpi, facecolor=BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[4.4, 1], hspace=0.03)
    ax, axv = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    for a in (ax, axv):
        a.set_facecolor(BG)
        for sp in a.spines.values():
            sp.set_color(LINE2)
        a.tick_params(colors=T2, labelsize=8)
    cols = np.where(w.close.values >= w.open.values, GREEN, RED)
    lo, hi = float(w.low.min()), float(w.high.max())
    body = np.maximum(np.abs(w.close.values - w.open.values), (hi - lo) * 0.0012)
    ax.vlines(x, w.low.values, w.high.values, colors=cols, linewidth=1, zorder=2)
    ax.bar(x, body, bottom=np.minimum(w.open.values, w.close.values), width=.62, color=cols, zorder=3)
    for col, color in (("sma20", AMBER), ("sma50", CYAN)):
        if w[col].notna().any():
            ax.plot(x, w[col].values, color=color, linewidth=1, alpha=.85, zorder=2)
    ys = [lo, hi]
    for z in spec["zones"]:
        col = GREEN if z["type"] == "demand" else RED
        x0 = max(z["s"], start) - start
        ax.add_patch(Rectangle((x0, z["bottom"]), m + ext - x0, z["top"] - z["bottom"], facecolor=col, alpha=.16, edgecolor=col, linewidth=1, zorder=1))
        ax.text(x0 + .3, z["top"] if z["type"] == "supply" else z["bottom"], f"{z['id']} {z['pattern']} Q{z['quality']} {z['status']}", color=col, fontsize=8,
                fontfamily="monospace", va="bottom" if z["type"] == "supply" else "top", zorder=6)
        ys += [z["top"], z["bottom"]]
    for lv in spec["levels"]:
        col = GREEN if lv["kind"] == "support" else RED
        ax.hlines(lv["price"], 0, m + ext, colors=col, linestyles=":", linewidth=1, alpha=.8, zorder=1)
        ax.text(m + ext - .3, lv["price"], f"{lv['id']} {lv['price']:,.2f}", color=col, fontsize=8, ha="right", va="bottom", fontfamily="monospace")
        ys.append(lv["price"])
    for t in spec["lines"]:
        col = GREEN if t["kind"] == "support" else RED
        slope = (t["p1"] - t["p0"]) / max(1, t["i1"] - t["i0"])
        xe = n - 1 + ext // 2
        ax.plot([t["i0"] - start, xe - start], [t["p0"], t["p0"] + slope * (xe - t["i0"])], color=col, linestyle="--", linewidth=1.4, zorder=4)
    p = spec.get("plan")
    if p:
        x0, x1 = m - 1, m - 1 + int(ext * .85)
        ref, stop = p["entry_ref"], p["stop"]
        t1 = p["targets"][0]["price"] if p["targets"] else ref
        ax.add_patch(Rectangle((x0, min(ref, stop)), x1 - x0, abs(ref - stop), facecolor=RED, alpha=.12, linewidth=0, zorder=1))
        ax.add_patch(Rectangle((x0, min(ref, t1)), x1 - x0, abs(t1 - ref), facecolor=GREEN, alpha=.12, linewidth=0, zorder=1))
        ax.add_patch(Rectangle((x0, p["entry_low"]), x1 - x0, p["entry_high"] - p["entry_low"], facecolor=CYAN, alpha=.22, edgecolor=CYAN, linewidth=1, zorder=2))
        ax.text(x1 + .3, (p["entry_low"] + p["entry_high"]) / 2, f"ENTRY {p['entry_low']:,.2f}-{p['entry_high']:,.2f}", color=CYAN, fontsize=8, fontfamily="monospace", va="center")
        for price, label, col in [(stop, "STOP", RED)] + [(t["price"], f"T{i}", GREEN) for i, t in enumerate(p["targets"], 1)]:
            ax.hlines(price, x0, x1, colors=col, linewidth=1.4, zorder=3)
            ax.text(x1 + .3, price, f"{label} {price:,.2f}", color=col, fontsize=8, fontfamily="monospace", va="center")
            ys.append(price)
    for mk in spec["markers"]:
        if mk["i"] >= start:
            ax.scatter([mk["i"] - start], [mk["price"]], marker="^" if mk["dir"] == "up" else "v", s=70, color=GREEN if mk["dir"] == "up" else RED, zorder=7)
    pad = (max(ys) - min(ys)) * .06
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_xlim(-1, m + ext + 14)
    ax.grid(axis="y", color="#151515", linewidth=.6)
    ax.tick_params(labelbottom=False)
    axv.bar(x, w.volume.values, color=np.where(w.close.values >= w.open.values, GREEN, RED), alpha=.5, width=.62)
    axv.set_xlim(-1, m + ext + 14)
    pos = list(range(0, m, max(1, m // 9)))
    axv.set_xticks(pos)
    axv.set_xticklabels([str(w.index[i])[2:10] for i in pos], rotation=0, fontsize=8, color=T2)
    axv.tick_params(labelleft=False)
    fig.text(0.012, 0.965, title, color=AMBER, fontsize=12, fontweight="bold", fontfamily="monospace")
    fig.text(0.988, 0.965, "Arka AI · research tool, not advice", color=T3, fontsize=8, ha="right", fontfamily="monospace")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=BG, bbox_inches=None)
    plt.close(fig)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════
# 6. AI: PROMPTS, VALIDATION, ANALYSIS
# ══════════════════════════════════════════════════════════
ANALYSIS_SCHEMA = """Return ONLY this JSON object (fill every field; use null / [] when unknown):
{
 "verdict": "MATCH | PARTIAL | NO_MATCH | AVOID",
 "score": 0,
 "grade": "A+ | A | B | C | D",
 "bias": "BULLISH | BEARISH | NEUTRAL",
 "setup_state": "WATCHING | FORMING | READY | TRIGGERED | EXTENDED | INVALIDATED | NOT_APPLICABLE",
 "headline": "one sentence a trader can act on",
 "setups": [ {"setup_id": "...", "name": "...", "match_pct": 0, "state": "...",
              "checks": [ {"rule_id": "R01", "rule_name": "...", "status": "PRESENT | NOT_PRESENT | UNCLEAR",
                           "confidence": 0, "reason": "exact dates / prices / detection IDs", "refs": ["DZ1"]} ] } ],
 "my_zones": [ {"id": "DZ1", "role": "ENTRY_ZONE | TARGET | SUPPORT | RESISTANCE | CONTEXT", "matches_my_style": true, "comment": "..."} ],
 "plan": { "direction": "LONG | SHORT | NONE", "ref_zone": "DZ1", "entry_low": 0, "entry_high": 0, "stop": 0, "stop_reason": "...",
           "targets": [ {"price": 0, "ref": "SZ1 or R1", "reason": "..."} ], "invalidation": "...", "confirmation_needed": ["..."] },
 "supports": ["things that agree with MY playbook"],
 "contradicts": ["things that disagree with MY playbook"],
 "watch_next": ["what to watch before acting"],
 "scenarios": { "bull": {"trigger": "...", "path": "...", "level": 0}, "bear": {"trigger": "...", "path": "...", "level": 0},
                "neutral": {"trigger": "...", "path": "...", "level": 0} },
 "context": ["optional observations NOT used for the verdict"],
 "draw": { "zones": ["DZ1"], "levels": ["R1"], "trendlines": ["TL1"] },
 "narrative": "a clear 150-250 word analysis in plain language"
}"""


def _slim_zone(z):
    keys = ("id", "type", "pattern", "bottom", "top", "proximal", "distal", "thickness_atr", "base_n", "dep_atr", "vol_ratio", "bos", "aligned",
            "tests", "status", "quality", "formed", "age", "dist_pct", "dist_atr", "htf", "in_zone")
    return {k: z.get(k) for k in keys}


def _slim_levels(ls):
    return [{k: x.get(k) for k in ("id", "kind", "price", "touches", "flip", "dist_pct", "dist_atr")} for x in ls]


def feedback_summary(items: list) -> dict:
    ap, rj = [], []
    for it in items:
        for lab in it["payload"].get("labels", []):
            (ap if lab.get("approved") else rj).append(lab.get("features", {}))
    med = lambda rows, k: _f(np.median([r[k] for r in rows if r.get(k) is not None])) if any(r.get(k) is not None for r in rows) else None
    return {"approved": len(ap), "rejected": len(rj),
            "approved_median": {k: med(ap, k) for k in ("thickness_atr", "base_n", "dep_atr", "quality")},
            "rejected_median": {k: med(rj, k) for k in ("thickness_atr", "base_n", "dep_atr", "quality")},
            "approved_patterns": sorted({r.get("pattern") for r in ap if r.get("pattern")})}


def build_prompt(ticker, det, det_w, style, setups, lessons, feedback, note, df) -> str:
    setup_json = [{"setup_id": it["item_id"], "name": it["name"], **{k: v for k, v in it["payload"].items() if k not in ("source_name",)}} for it in setups]
    lesson_json = [{"name": it["name"], **it["payload"]} for it in lessons]
    market = {k: det[k] for k in ("asof", "close", "atr", "atr_pct", "day_chg_pct", "structure", "rsi", "sma", "ma_state", "sma50_slope_atr", "volume",
                                  "roc5_pct", "roc20_pct", "refs", "swings")}
    detections = {"zones": [_slim_zone(z) for z in det["zones"]], "levels": _slim_levels(det["levels"]), "trendlines": det["trendlines"],
                  "events": det["events"], "candle_signals": det["candles"]}
    weekly = None
    if det_w:
        weekly = {"structure": det_w["structure"], "ma_state": det_w["ma_state"], "rsi": det_w["rsi"], "close": det_w["close"],
                  "zones": [_slim_zone(z) for z in det_w["zones"][:4]], "levels": _slim_levels(det_w["levels"][:6])}
    rows = df.tail(70)
    ohlc = "i,date,o,h,l,c,v\n" + "\n".join(f"{len(df) - len(rows) + k},{str(ix)[:10]},{r.open:.2f},{r.high:.2f},{r.low:.2f},{r.close:.2f},{int(r.volume)}"
                                            for k, (ix, r) in enumerate(rows.iterrows()))
    parts = [
        "TICKER: " + ticker,
        "== PLAYBOOK: ACTIVE SETUPS (evaluate EACH one separately; rule ids are inside each setup) ==\n" + (json.dumps(setup_json, ensure_ascii=False) if setup_json else "NONE TAUGHT YET"),
        "== PLAYBOOK: LESSONS (how this trader defines and draws supply / demand zones, lines, entries, stops, targets) ==\n" + (json.dumps(lesson_json, ensure_ascii=False) if lesson_json else "NONE"),
        "== PLAYBOOK: ZONES THIS TRADER APPROVED OR REJECTED (summary) ==\n" + json.dumps(feedback_summary(feedback), ensure_ascii=False),
        "== ENGINE STYLE PARAMETERS (how detections below were produced) ==\n" + json.dumps(style),
        "== MARKET FACTS (daily, computed by Python) ==\n" + json.dumps(market, ensure_ascii=False, default=str),
        "== DETECTIONS (daily). Choose ONLY from these IDs ==\n" + json.dumps(detections, ensure_ascii=False, default=str),
        "== WEEKLY CONTEXT ==\n" + (json.dumps(weekly, ensure_ascii=False, default=str) if weekly else "UNAVAILABLE"),
        "== DAILY OHLC (last 70 candles; i is the candle index used by detections) ==\n" + ohlc,
        "== TRADER NOTE ==\n" + (note or "None"),
        ("TASK: Judge this stock ONLY against the trader's playbook. 1) Check every rule of every active setup against the detections and facts. "
         "2) Say which detected zones are the trader's kind of zone (use the lessons and the approved / rejected summary) and what role each plays. "
         "3) Build a trade plan using ONLY detected zones and levels (entry zone inside a zone, stop beyond its distal line, targets at the next opposing zone or level); "
         "use direction NONE if nothing qualifies. 4) List what supports and what contradicts the playbook, and what to watch next. "
         "5) Give bull / bear / neutral scenarios with exact trigger prices. 6) Choose which detection IDs to draw. The attached image shows the detections. "
         "If no setup is taught, judge against the lessons and approved zones and say so in the headline."),
        ANALYSIS_SCHEMA,
    ]
    return "\n\n".join(parts)


def _clean_list(x, cap=6):
    return [str(i)[:400] for i in (x if isinstance(x, list) else [])[:cap] if str(i).strip()]


def validate_ai(ai: dict, det: dict, price: float):
    """Coerce the model output to something safe to render and draw. Unknown IDs and impossible plans are dropped."""
    zid, lid, tid = {z["id"] for z in det["zones"]}, {x["id"] for x in det["levels"]}, {x["id"] for x in det["trendlines"]}
    out = {}
    out["verdict"] = ai.get("verdict") if ai.get("verdict") in ("MATCH", "PARTIAL", "NO_MATCH", "AVOID") else "PARTIAL"
    try:
        out["score"] = int(max(0, min(100, round(float(ai.get("score", 0))))))
    except Exception:
        out["score"] = 0
    out["grade"] = ai.get("grade") if ai.get("grade") in ("A+", "A", "B", "C", "D") else grade_for(out["score"])
    out["bias"] = ai.get("bias") if ai.get("bias") in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL"
    out["setup_state"] = str(ai.get("setup_state", "WATCHING"))[:30]
    out["headline"] = str(ai.get("headline", ""))[:400]
    setups = []
    for s in ai.get("setups", []) if isinstance(ai.get("setups"), list) else []:
        checks = []
        for c in s.get("checks", []) if isinstance(s.get("checks"), list) else []:
            st_ = c.get("status") if c.get("status") in ("PRESENT", "NOT_PRESENT", "UNCLEAR") else "UNCLEAR"
            try:
                conf = int(max(0, min(100, float(c.get("confidence", 0)))))
            except Exception:
                conf = 0
            checks.append({"rule_id": str(c.get("rule_id", ""))[:12], "rule_name": str(c.get("rule_name", ""))[:140], "status": st_, "confidence": conf,
                           "reason": str(c.get("reason", ""))[:500], "refs": [r for r in c.get("refs", []) if r in zid | lid | tid] if isinstance(c.get("refs"), list) else []})
        try:
            mp = int(max(0, min(100, float(s.get("match_pct", 0)))))
        except Exception:
            mp = 0
        setups.append({"setup_id": str(s.get("setup_id", "")), "name": str(s.get("name", "Setup"))[:120], "match_pct": mp, "state": str(s.get("state", ""))[:30], "checks": checks})
    out["setups"] = setups
    out["my_zones"] = [{"id": z.get("id"), "role": str(z.get("role", "CONTEXT"))[:20], "matches_my_style": bool(z.get("matches_my_style")), "comment": str(z.get("comment", ""))[:300]}
                       for z in ai.get("my_zones", []) if isinstance(z, dict) and z.get("id") in zid | lid]
    for k in ("supports", "contradicts", "watch_next", "context"):
        out[k] = _clean_list(ai.get(k))
    sc = ai.get("scenarios") if isinstance(ai.get("scenarios"), dict) else {}
    out["scenarios"] = {k: {kk: (sc.get(k, {}) or {}).get(kk) for kk in ("trigger", "path", "level")} for k in ("bull", "bear", "neutral")}
    dr = ai.get("draw") if isinstance(ai.get("draw"), dict) else {}
    out["draw"] = {"zones": [i for i in dr.get("zones", []) if i in zid], "levels": [i for i in dr.get("levels", []) if i in lid],
                   "trendlines": [i for i in dr.get("trendlines", []) if i in tid]}
    out["narrative"] = str(ai.get("narrative", ""))[:3000]
    # plan
    p, plan, atr = ai.get("plan") if isinstance(ai.get("plan"), dict) else {}, None, det["atr"]
    direction = p.get("direction")
    out["no_trade"] = direction == "NONE"
    if direction in ("LONG", "SHORT"):
        try:
            lo, hi, stop = float(p["entry_low"]), float(p["entry_high"]), float(p["stop"])
            lo, hi = min(lo, hi), max(lo, hi)
            ref = min(max(price, lo), hi) if lo <= price <= hi else (hi if direction == "LONG" and price > hi else lo if direction == "SHORT" and price < lo else (hi if price > hi else lo))
            long = direction == "LONG"
            targets = []
            for t in p.get("targets", [])[:3]:
                tp = float(t["price"])
                if (long and tp > ref) or ((not long) and tp < ref):
                    targets.append({"price": round(tp, 2), "ref": str(t.get("ref", ""))[:10], "reason": str(t.get("reason", ""))[:200],
                                    "rr": round(abs(tp - ref) / abs(ref - stop), 2) if ref != stop else 0.0})
            sane = (stop < ref if long else stop > ref) and targets and 0.6 * price < lo and hi < 1.6 * price and abs(ref - stop) <= 8 * atr
            if sane:
                plan = {"direction": direction, "ref_zone": p.get("ref_zone") if p.get("ref_zone") in zid else None, "entry_low": round(lo, 2), "entry_high": round(hi, 2),
                        "entry_ref": round(ref, 2), "stop": round(stop, 2), "stop_reason": str(p.get("stop_reason", ""))[:300], "targets": targets,
                        "invalidation": str(p.get("invalidation", ""))[:400], "confirmation_needed": _clean_list(p.get("confirmation_needed"), 5),
                        "counter_trend": False, "source": "ai"}
        except Exception:
            plan = None
    out["plan"] = plan
    return out


def verdict_text(report: dict):
    ai = report.get("ai")
    if ai:
        v = ai["verdict"]
        return {"MATCH": ("MATCHES YOUR SETUP", GREEN), "PARTIAL": ("PARTIAL MATCH", AMBER), "NO_MATCH": ("NOT YOUR SETUP", T3), "AVOID": ("AVOID", RED)}[v]
    s = report["score"]
    return ("ENGINE: STRONG ZONE SETUP", GREEN) if s >= 72 else ("ENGINE: DEVELOPING", AMBER) if s >= 50 else ("ENGINE: NO CLEAR SETUP", T3)


def run_analysis(ticker: str, note: str = "", use_ai: bool = True, window: int = 120) -> dict:
    t = ticker.upper().strip()
    daily = fetch_ohlc(t, "1d", "2y")
    weekly = None
    try:
        weekly = fetch_ohlc(t, "1wk", "5y")
    except Exception:
        weekly = None
    style = get_style()
    det = analyze_market(daily, style)
    det_w = None
    if weekly is not None and len(weekly) >= 60:
        det_w = analyze_market(weekly, dict(style, pivot_left=2, pivot_right=2, lookback=140, base_max=min(3, int(style["base_max"]))))
    add_htf_confluence(det, det_w)
    eplan = deterministic_plan(det)
    report = {"ticker": t, "asof": det["asof"], "det": det, "det_w": det_w, "style": style, "engine_plan": eplan, "engine_score": engine_score(det, eplan),
              "ai": None, "ai_error": "", "plan": eplan, "score": engine_score(det, eplan), "note": note, "window": window,
              "n_setups": len(active_items("setup")), "n_lessons": len(active_items("lesson")), "n_labelled": len(active_items("feedback")),
              "ran_at": datetime.now(IST).strftime("%d %b %Y %H:%M IST"), "df": daily}
    if use_ai and get_gemini() is not None:
        try:
            preview = render_png(daily, build_spec(det, None, eplan), window=110, title=f"{t} · detections (preview for AI)")
            prompt = build_prompt(t, det, det_w, style, active_items("setup"), active_items("lesson"), active_items("feedback"), note, daily)
            ai = validate_ai(call_json(prompt, images=[preview]), det, det["close"])
            report["ai"] = ai
            report["score"] = ai["score"]
            report["plan"] = None if ai["no_trade"] else (ai["plan"] or eplan)
        except Exception as exc:
            report["ai_error"] = str(exc)
    report["spec"] = build_spec(det, (report["ai"] or {}).get("draw"), report["plan"])
    return report


def ask_followup(report: dict, question: str) -> str:
    det, ai = report["det"], report.get("ai") or {}
    ctx = {"ticker": report["ticker"], "asof": report["asof"], "close": det["close"], "structure": det["structure"], "plan": report.get("plan"),
           "zones": [_slim_zone(z) for z in det["zones"]], "levels": _slim_levels(det["levels"]), "headline": ai.get("headline"), "narrative": ai.get("narrative"),
           "supports": ai.get("supports"), "contradicts": ai.get("contradicts")}
    prompt = ("You are Arka AI. Answer the trader's follow-up question about this analysis. Use ONLY the supplied data; if it is not there, say so. "
              "Be concise and specific (prices, IDs). No hype, no guarantees.\n\nANALYSIS DATA:\n" + json.dumps(ctx, ensure_ascii=False, default=str) +
              "\n\nQUESTION: " + question)
    return call_text(prompt)


# ══════════════════════════════════════════════════════════
# 7. TEACHING HELPERS
# ══════════════════════════════════════════════════════════
PDF_PROMPT = """Read this trading-strategy PDF as a TRAINING document for a personal trading analyst.
Understand everything: prose, screenshots, diagrams, marked-up charts, tables and examples. Do not merely summarise.
Convert it into a structured, testable trading setup. Keep the author's own terminology. Do not invent conditions;
anything ambiguous goes in "ambiguities". Capture how the author defines and draws SUPPLY and DEMAND zones and any lines.

Return ONLY this JSON:
{
 "setup_name": "...", "summary": "...", "market_context": "...",
 "conditions": [ {"id": "R01", "name": "...", "type": "structure|zone|price|volume|indicator|context|other", "required": true,
                  "condition": "testable condition", "evidence_hint": "what proves it"} ],
 "trigger": {"description": "...", "type": "..."},
 "confirmation": [ {"id": "C01", "name": "...", "description": "...", "required": true} ],
 "invalidation": [ {"id": "I01", "description": "...", "price_logic": "..."} ],
 "zone_definitions": [ {"type": "demand|supply", "how_identified": "...", "how_drawn": "...", "quality_markers": ["..."], "avoid": ["..."]} ],
 "line_definitions": [ {"type": "support|resistance|trendline|other", "how_drawn": "...", "use": "..."} ],
 "entry_logic": "...", "stop_logic": "...", "target_logic": "...",
 "exclusions": ["..."], "examples": [ {"label": "VALID|INVALID|UNCLEAR", "description": "...", "source_page": 1} ],
 "ambiguities": ["..."], "confidence": "HIGH|MEDIUM|LOW"
}
Prefer atomic, individually testable conditions. A generic indicator is NOT a rule unless the document actually uses it."""

IMAGE_PROMPT = """These images are charts the trader marked up. Study the drawings (boxes, zones, lines, arrows, labels, entries, stops, targets)
together with the trader's explanation. Learn HOW THIS TRADER reads a chart: what a good supply zone and demand zone look like to them
(base size, impulse strength, location, freshness), how they draw lines, what they wait for before entering, where the stop and targets go.
Do not describe the stock. Extract rules of style.

Return ONLY this JSON:
{
 "lesson_title": "...", "summary": "...",
 "zone_definitions": [ {"type": "demand|supply", "how_identified": "...", "how_drawn": "...", "quality_markers": ["..."], "avoid": ["..."]} ],
 "line_definitions": [ {"type": "support|resistance|trendline|other", "how_drawn": "...", "use": "..."} ],
 "entry_logic": "...", "stop_logic": "...", "target_logic": "...",
 "good_setup_signs": ["..."], "bad_setup_signs": ["..."], "ambiguities": ["..."]
}"""


def extract_pdf_text(pdf_file) -> str:
    if not HAS_PDF:
        return ""
    try:
        parts = []
        pdf_file.seek(0)
        with pdfplumber.open(pdf_file) as pdf:
            for no, page in enumerate(pdf.pages, 1):
                txt = page.extract_text() or ""
                if txt.strip():
                    parts.append(f"[Page {no}]\n{txt.strip()}")
        pdf_file.seek(0)
        return "\n\n".join(parts)
    except Exception:
        return ""


def understand_pdf_setup(pdf_file, filename="setup.pdf") -> dict:
    if not HAS_GEMINI or not GEMINI_KEY:
        return {"error": "Gemini is not configured. Check GEMINI_KEY and the google-generativeai package."}
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(pdf_file.getvalue())
        tmp.flush()
        tmp.close()
        genai.configure(api_key=GEMINI_KEY)
        uploaded = genai.upload_file(path=tmp.name, mime_type="application/pdf", display_name=filename)
        result = call_json(PDF_PROMPT, uploaded_file=uploaded)
        result["source_name"] = filename
        return result
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass


def learn_from_images(images: list, explanation: str) -> dict:
    try:
        return call_json(IMAGE_PROMPT + "\n\nTRADER'S EXPLANATION:\n" + (explanation or "None given."), images=images)
    except Exception as exc:
        return {"error": str(exc)}


def manual_setup(name: str, summary: str, conditions: str, trigger: str, invalidation: str) -> dict:
    rows = [{"id": f"R{i:02d}", "name": ln.strip()[:80], "type": "other", "required": True, "condition": ln.strip(), "evidence_hint": "Use the supplied detections and market facts."}
            for i, ln in enumerate([x for x in conditions.splitlines() if x.strip()], 1)]
    return {"setup_name": name.strip(), "summary": summary.strip(), "conditions": rows, "trigger": {"description": trigger.strip(), "type": "manual"},
            "confirmation": [], "invalidation": [{"id": "I01", "description": invalidation.strip()}] if invalidation.strip() else [], "exclusions": [],
            "examples": [], "confidence": "HIGH", "source_name": "manual"}


# ══════════════════════════════════════════════════════════
# 8. RADAR (watchlist scan, engine only: no AI cost)
# ══════════════════════════════════════════════════════════
def radar_scan(symbols: list, style: dict) -> list:
    frames = fetch_many(tuple(symbols))
    rows = []
    for sym, df in frames.items():
        try:
            det = analyze_market(df, style)
            plan = deterministic_plan(det)
            z = next((x for x in det["zones"] if plan and x["id"] == plan["ref_zone"]), None)
            near = min([x for x in det["zones"] if x["dist_atr"] is not None], key=lambda x: x["dist_atr"], default=None)
            rows.append({"symbol": sym, "close": det["close"], "chg": det["day_chg_pct"], "structure": det["structure"].split(" ")[0], "rsi": det["rsi"],
                         "zone": (z or near or {}).get("id"), "ztype": (z or near or {}).get("type"), "quality": (z or near or {}).get("quality"),
                         "status": (z or near or {}).get("status"), "dist_pct": (z or near or {}).get("dist_pct"), "dist_atr": (z or near or {}).get("dist_atr"),
                         "direction": plan["direction"] if plan else None, "rr": plan["targets"][0]["rr"] if plan and plan["targets"] else None,
                         "score": engine_score(det, plan), "n_zones": len(det["zones"])})
        except Exception:
            continue
    rows.sort(key=lambda r: -r["score"])
    return rows


# ══════════════════════════════════════════════════════════
# 9. INTERFACE
# ══════════════════════════════════════════════════════════
ARKA_CSS = """
<style>
.aa-chips{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 12px;}
.aa-chip{display:inline-flex;align-items:center;gap:7px;padding:4px 11px;border:1px solid #2B2B2B;border-radius:999px;font:700 9px 'JetBrains Mono',monospace;letter-spacing:.8px;color:#A3A3A3;text-transform:uppercase;white-space:nowrap;}
.aa-chip i{width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block;}
.aa-chip.ok{color:#30D158;border-color:rgba(48,209,88,.35);background:rgba(48,209,88,.07);}
.aa-chip.warn{color:#FF9F0A;border-color:rgba(255,159,10,.35);background:rgba(255,159,10,.07);}
.aa-chip.bad{color:#FF453A;border-color:rgba(255,69,58,.35);background:rgba(255,69,58,.07);}
.aa-intro{border:1px solid #1F1F1F;border-left:3px solid #FF9F0A;background:#0A0A0A;padding:14px 18px;margin:4px 0 12px;font:13px 'Inter',sans-serif;color:#A3A3A3;line-height:1.7;}
.aa-intro b{color:#ECECEC;}
.aa-steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px;margin:8px 0 14px;}
.aa-step{border:1px solid #1F1F1F;border-top:2px solid #2B2B2B;background:#0A0A0A;padding:14px 16px;}
.aa-step .n{font:800 9px 'JetBrains Mono',monospace;letter-spacing:1.4px;color:#FF9F0A;}
.aa-step .t{font:700 14px 'Inter',sans-serif;color:#ECECEC;margin:6px 0 4px;}
.aa-step .d{font:12px 'Inter',sans-serif;color:#6B6B6B;line-height:1.6;}

.aa-hero{display:grid;grid-template-columns:minmax(200px,1.05fr) minmax(250px,1.4fr) auto minmax(250px,1.3fr);gap:24px;align-items:center;background:#0A0A0A;border:1px solid #1F1F1F;border-top:2px solid var(--vc,#FF9F0A);padding:18px 22px;margin-top:8px;}
@media(max-width:1150px){.aa-hero{grid-template-columns:1fr 1fr;}}
.aa-sym{display:inline-block;font:800 10px 'JetBrains Mono',monospace;letter-spacing:1px;color:#000;background:#FF9F0A;padding:2px 9px;border-radius:3px;}
.aa-px{font:700 28px 'JetBrains Mono',monospace;color:#ECECEC;margin-top:8px;font-variant-numeric:tabular-nums;}
.aa-px em{font:700 12px 'JetBrains Mono',monospace;font-style:normal;padding:2px 8px;border-radius:3px;margin-left:8px;vertical-align:middle;}
.aa-px em.up{color:#30D158;background:rgba(48,209,88,.10);} .aa-px em.dn{color:#FF453A;background:rgba(255,69,58,.10);}
.aa-sub{font:10px 'JetBrains Mono',monospace;color:#6B6B6B;margin-top:6px;letter-spacing:.4px;}
.aa-verdict{display:inline-block;font:800 12px 'JetBrains Mono',monospace;letter-spacing:1.4px;padding:7px 14px;border:1px solid var(--vc);color:var(--vc);background:rgba(255,255,255,.02);border-radius:3px;}
.aa-hm .aa-chips{margin:10px 0 0;}
.aa-mini{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 18px;}
.aa-mini div span{display:block;font:700 8px 'JetBrains Mono',monospace;letter-spacing:1px;color:#6B6B6B;}
.aa-mini div b{font:600 14px 'JetBrains Mono',monospace;color:#ECECEC;font-variant-numeric:tabular-nums;}
.aa-mini div b.dn{color:#FF453A;} .aa-mini div b.up{color:#30D158;}
.aa-head{border:1px solid #1F1F1F;border-top:0;background:#0F0F0F;padding:12px 22px;font:600 14px 'Inter',sans-serif;color:#ECECEC;line-height:1.6;}
.aa-head span{color:#6B6B6B;font:11px 'JetBrains Mono',monospace;margin-left:10px;}

.aa-card{background:#0A0A0A;border:1px solid #1F1F1F;border-top:2px solid #2B2B2B;padding:14px 16px;min-height:100px;}
.aa-ct{display:flex;justify-content:space-between;align-items:center;font:700 10px 'JetBrains Mono',monospace;letter-spacing:1.3px;color:#FF9F0A;border-bottom:1px solid #1F1F1F;padding-bottom:9px;margin-bottom:6px;text-transform:uppercase;}
.aa-ct span{color:#6B6B6B;font-weight:600;letter-spacing:.6px;}
.aa-kv{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #141414;font:12px 'Inter',sans-serif;color:#A3A3A3;}
.aa-kv b{font:600 12px 'JetBrains Mono',monospace;color:#ECECEC;text-align:right;}
.aa-kv b.dn{color:#FF453A;} .aa-kv b.up{color:#30D158;} .aa-kv b.cy{color:#5AC8FA;}
.aa-kv em{display:block;font:10px 'Inter',sans-serif;font-style:normal;color:#6B6B6B;text-align:right;margin-top:2px;max-width:260px;}
.aa-note{font:11px 'Inter',sans-serif;color:#6B6B6B;line-height:1.7;margin-top:8px;}
.aa-empty{border:1px dashed #2B2B2B;padding:22px;text-align:center;color:#6B6B6B;font:11px 'JetBrains Mono',monospace;letter-spacing:.5px;line-height:1.8;}

.aa-sh{display:flex;justify-content:space-between;gap:8px;margin-top:10px;font:700 11px 'Inter',sans-serif;color:#ECECEC;}
.aa-sh span{font:700 9px 'JetBrains Mono',monospace;color:#FF9F0A;letter-spacing:.8px;}
.aa-ck{border:1px solid #1F1F1F;border-left:3px solid var(--c);background:#0F0F0F;padding:8px 11px;margin-top:5px;}
.aa-ck .h{display:flex;justify-content:space-between;gap:8px;font:600 11px 'Inter',sans-serif;color:#ECECEC;}
.aa-ck .h span{font:700 8px 'JetBrains Mono',monospace;letter-spacing:.8px;color:var(--c);white-space:nowrap;}
.aa-ck .r{font:10px 'Inter',sans-serif;color:#A3A3A3;line-height:1.5;margin-top:4px;}
.aa-ck .b{height:2px;background:#1a1a1a;margin-top:6px;} .aa-ck .b i{display:block;height:2px;background:var(--c);}
.aa-ref{display:inline-block;font:700 8px 'JetBrains Mono',monospace;color:#5AC8FA;border:1px solid rgba(90,200,250,.3);padding:0 5px;border-radius:3px;margin-left:5px;}

.aa-list{list-style:none;margin:0;padding:0;}
.aa-list li{display:flex;gap:9px;padding:7px 0;border-bottom:1px solid #141414;font:12px 'Inter',sans-serif;color:#C8C8C8;line-height:1.55;}
.aa-list li:last-child{border-bottom:0;}
.aa-list li:before{content:var(--ic);color:var(--c);font:700 12px 'JetBrains Mono',monospace;flex-shrink:0;}
.aa-scn{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:8px;}
.aa-sc{background:#0A0A0A;border:1px solid #1F1F1F;border-top:2px solid var(--c);padding:14px 16px;}
.aa-sc .t{font:800 10px 'JetBrains Mono',monospace;letter-spacing:1.2px;color:var(--c);}
.aa-sc .l{font:700 18px 'JetBrains Mono',monospace;color:#ECECEC;margin:8px 0 4px;}
.aa-sc .d{font:12px 'Inter',sans-serif;color:#A3A3A3;line-height:1.6;}
.aa-sc .d b{color:#ECECEC;}

.aa-tw{overflow:auto;border:1px solid #1F1F1F;background:#070707;margin:6px 0 10px;}
.aa-t{width:100%;border-collapse:separate;border-spacing:0;font:12px 'Inter',sans-serif;color:#E0E0E0;}
.aa-t th{position:sticky;top:0;background:#0C0C0C;color:#777;font:700 9px 'JetBrains Mono',monospace;letter-spacing:.9px;text-transform:uppercase;text-align:left;padding:10px;border-bottom:1px solid #252525;white-space:nowrap;}
.aa-t td{padding:9px 10px;border-bottom:1px solid #141414;white-space:nowrap;vertical-align:middle;}
.aa-t tbody tr:hover td{background:rgba(255,159,10,.05);}
.aa-t td.n{font:11px 'JetBrains Mono',monospace;text-align:right;color:#CFCFCF;font-variant-numeric:tabular-nums;}
.aa-t b{color:#FF9F0A;font:700 11px 'JetBrains Mono',monospace;}
.aa-pill{display:inline-block;padding:2px 8px;border-radius:999px;font:800 8px 'JetBrains Mono',monospace;letter-spacing:.8px;border:1px solid var(--c);color:var(--c);}
.aa-q{display:inline-flex;align-items:center;gap:7px;font:700 11px 'JetBrains Mono',monospace;color:#ECECEC;}
.aa-q i{display:inline-block;width:54px;height:5px;background:#1a1a1a;position:relative;} .aa-q i:after{content:"";position:absolute;left:0;top:0;height:100%;width:var(--w);background:var(--c);}
.aa-sec{display:flex;align-items:center;gap:12px;margin:26px 0 10px;}
.aa-sec:before{content:"";width:3px;height:14px;background:#FF9F0A;}
.aa-sec b{font:700 11px 'JetBrains Mono',monospace;letter-spacing:1.6px;color:#ECECEC;text-transform:uppercase;white-space:nowrap;}
.aa-sec span{font:11px 'Inter',sans-serif;color:#6B6B6B;} .aa-sec i{flex:1;height:1px;background:#1F1F1F;}
.aa-lab{border:1px solid #1F1F1F;background:#0A0A0A;padding:8px 12px;margin-top:6px;font:12px 'Inter',sans-serif;color:#A3A3A3;}
.aa-lab b{font:700 11px 'JetBrains Mono',monospace;color:#FF9F0A;margin-right:8px;}
.aa-item{border:1px solid #1F1F1F;border-left:3px solid var(--c);background:#0A0A0A;padding:11px 14px;margin-top:6px;}
.aa-item .h{display:flex;justify-content:space-between;gap:10px;align-items:center;} .aa-item .h b{font:700 13px 'Inter',sans-serif;color:#ECECEC;}
.aa-item .d{font:11px 'Inter',sans-serif;color:#6B6B6B;margin-top:4px;line-height:1.6;}
</style>
"""


def _inject_css():
    st.markdown(ARKA_CSS, unsafe_allow_html=True)


def _sec(title: str, sub: str = ""):
    s = f"<span>{esc(sub)}</span>" if sub else ""
    st.markdown(f'<div class="aa-sec"><b>{esc(title)}</b>{s}<i></i></div>', unsafe_allow_html=True)


def _fmt(p, nd=2):
    return f"{p:,.{nd}f}" if isinstance(p, (int, float)) and math.isfinite(p) else "—"


def _chip(text, cls=""):
    return f'<span class="aa-chip {cls}"><i></i>{esc(text)}</span>'


def html_status() -> str:
    ai_ok = get_gemini() is not None
    mem = storage_mode()
    n_set, n_les, n_lab = len(active_items("setup")), len(active_items("lesson")), len(active_items("feedback"))
    chips = [_chip("AI ENGINE ONLINE" if ai_ok else "AI OFFLINE · ENGINE ONLY", "ok" if ai_ok else "warn"),
             _chip("PLAYBOOK SAVED TO CLOUD" if mem == "CLOUD" else "PLAYBOOK: THIS SESSION ONLY", "ok" if mem == "CLOUD" else "warn"),
             _chip(f"{n_set} ACTIVE SETUP{'S' if n_set != 1 else ''}", "ok" if n_set else "bad"),
             _chip(f"{n_les} LESSON{'S' if n_les != 1 else ''}", "ok" if n_les else ""),
             _chip(f"{n_lab} LABELLED ZONE SET{'S' if n_lab != 1 else ''}", "ok" if n_lab else ""),
             _chip("ZONE STYLE: " + style_source(), "ok" if style_source() != "DEFAULT" else "")]
    if not HAS_PLOTLY:
        chips.append(_chip("INSTALL PLOTLY FOR INTERACTIVE CHARTS", "warn"))
    return '<div class="aa-chips">' + "".join(chips) + "</div>"


def _pill(text, color):
    return f'<span class="aa-pill" style="--c:{color}">{esc(text)}</span>'


def _ring(score: int, color: str) -> str:
    c = 2 * math.pi * 34
    return (f'<svg width="96" height="96" viewBox="0 0 92 92"><circle cx="46" cy="46" r="34" fill="none" stroke="#1a1a1a" stroke-width="7"/>'
            f'<circle cx="46" cy="46" r="34" fill="none" stroke="{color}" stroke-width="7" stroke-linecap="round" '
            f'stroke-dasharray="{max(0, min(100, score)) / 100 * c:.1f} {c:.1f}" transform="rotate(-90 46 46)"/>'
            f'<text x="46" y="50" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="22" font-weight="700" fill="#ECECEC">{score}</text>'
            f'<text x="46" y="65" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="7" letter-spacing="1" fill="#6B6B6B">SCORE</text></svg>')


def html_hero(r: dict) -> str:
    det, ai = r["det"], r.get("ai") or {}
    label, color = verdict_text(r)
    score = r["score"]
    grade = ai.get("grade") or grade_for(score)
    chg = det["day_chg_pct"]
    chg_cls = "up" if chg >= 0 else "dn"
    struct = det["structure"]
    bias = ai.get("bias") or ("BULLISH" if struct.startswith("UPTREND") else "BEARISH" if struct.startswith("DOWNTREND") else "NEUTRAL")
    in_zone = any(z["in_zone"] for z in det["zones"])
    state = ai.get("setup_state") or ("PRICE IN ZONE" if in_zone else "WATCHING")
    bias_cls = "ok" if bias == "BULLISH" else "bad" if bias == "BEARISH" else ""
    chips = (_chip(f"GRADE {grade}", "ok" if grade in ("A+", "A") else "warn" if grade in ("B", "C") else "bad") + _chip(f"BIAS {bias}", bias_cls)
             + _chip(f"STATE {state}") + _chip(("AI + ENGINE" if ai else "ENGINE ONLY"), "ok" if ai else "warn"))
    p = r.get("plan")
    if p:
        t1 = p["targets"][0] if p["targets"] else None
        mini = ('<div class="aa-mini">'
                f'<div><span>{p["direction"]} ENTRY</span><b>{_fmt(p["entry_ref"])}</b></div><div><span>STOP</span><b class="dn">{_fmt(p["stop"])}</b></div>'
                f'<div><span>TARGET 1</span><b class="up">{_fmt(t1["price"]) if t1 else "—"}</b></div>'
                f'<div><span>REWARD : RISK</span><b>{(str(t1["rr"]) + " R") if t1 else "—"}</b></div></div>')
    else:
        mini = '<div class="aa-empty" style="padding:14px">NO TRADE PLAN RIGHT NOW<br>waiting for price to reach one of your zones</div>'
    head = ai.get("headline") or _engine_headline(det)
    return (f'<div class="aa-hero" style="--vc:{color}"><div><span class="aa-sym">{esc(r["ticker"])}</span>'
            f'<div class="aa-px">₹{_fmt(det["close"])}<em class="{chg_cls}">{"▲" if chg >= 0 else "▼"} {abs(chg):.2f}%</em></div>'
            f'<div class="aa-sub">DAILY · AS OF {esc(det["asof"])} · ATR {det["atr_pct"]}% · RSI {_fmt(det["rsi"], 0)}</div></div>'
            f'<div class="aa-hm"><span class="aa-verdict" style="--vc:{color}">{esc(label)}</span><div class="aa-chips">{chips}</div></div>'
            f'<div>{_ring(score, color)}</div><div>{mini}</div></div>'
            f'<div class="aa-head">{esc(head)}<span>{esc(r["ran_at"])}</span></div>')


def _engine_headline(det: dict) -> str:
    zs = [z for z in det["zones"] if z["dist_atr"] is not None]
    if not zs:
        return f"No supply or demand zones detected with your current style. Structure: {det['structure'].lower()}."
    z = min(zs, key=lambda x: x["dist_atr"])
    where = "price is inside it now" if z["in_zone"] else f"{abs(z['dist_pct']):.1f}% ({z['dist_atr']:.1f} ATR) away"
    return f"{len(det['zones'])} zones detected. Nearest: {z['id']} ({z['type']}, quality {z['quality']}, {z['status'].lower()}); {where}. Structure: {det['structure'].lower()}."


def html_plan(r: dict, risk: float) -> str:
    p, ai = r.get("plan"), r.get("ai") or {}
    if not p:
        why = ("The AI found no entry that fits your playbook right now." if ai.get("no_trade")
               else "No zone is close enough to price for a clean plan.")
        alt = ""
        e = r.get("engine_plan")
        if ai.get("no_trade") and e:
            alt = (f'<div class="aa-note">Engine alternative (not matched to your playbook): {e["direction"]} from {e["ref_zone"]}, entry '
                   f'{_fmt(e["entry_low"])}-{_fmt(e["entry_high"])}, stop {_fmt(e["stop"])}.</div>')
        return f'<div class="aa-card"><div class="aa-ct">Trade plan<span>none</span></div><div class="aa-empty">{esc(why)}</div>{alt}</div>'
    dcls = "up" if p["direction"] == "LONG" else "dn"
    ref_zone = p.get("ref_zone") or "-"
    tag = " · counter-trend" if p.get("counter_trend") else ""
    rows = [f'<div class="aa-kv"><span>Direction</span><b class="{dcls}">{p["direction"]}{tag}</b></div>',
            f'<div class="aa-kv"><span>Based on</span><b>{esc(ref_zone)}</b></div>',
            f'<div class="aa-kv"><span>Entry zone</span><b class="cy">{_fmt(p["entry_low"])} – {_fmt(p["entry_high"])}</b></div>',
            f'<div class="aa-kv"><span>Entry reference</span><b>{_fmt(p["entry_ref"])}</b></div>',
            f'<div class="aa-kv"><span>Stop</span><div><b class="dn">{_fmt(p["stop"])}</b><em>{esc(p.get("stop_reason", ""))}</em></div></div>']
    for i, t in enumerate(p["targets"], 1):
        rr = f'{t["rr"]} R' if t.get("rr") is not None else ""
        rows.append(f'<div class="aa-kv"><span>Target {i} · {esc(t.get("ref", ""))}</span><div><b class="up">{_fmt(t["price"])}</b><em>{rr} · {esc(t.get("reason", ""))}</em></div></div>')
    sz = position_size(p, risk)
    if sz:
        rows.append(f'<div class="aa-kv"><span>Position size @ ₹{risk:,.0f} risk</span><div><b>{sz["qty"]:,} shares</b>'
                    f'<em>₹{sz["risk_per_share"]} risk per share · about ₹{sz["capital"]:,.0f} deployed</em></div></div>')
    extra = ""
    if p.get("invalidation"):
        extra += f'<div class="aa-note"><b style="color:#ECECEC">Invalidation:</b> {esc(p["invalidation"])}</div>'
    if p.get("confirmation_needed"):
        extra += '<div class="aa-note"><b style="color:#ECECEC">Wait for:</b> ' + " · ".join(esc(x) for x in p["confirmation_needed"]) + "</div>"
    return f'<div class="aa-card"><div class="aa-ct">Trade plan<span>{"AI" if p.get("source") == "ai" else "ENGINE"}</span></div>{"".join(rows)}{extra}</div>'


def html_scorecard(r: dict) -> str:
    ai = r.get("ai")
    if not ai:
        msg = "AI is offline, so your written rules cannot be checked. Zones, levels and the plan above come from the engine." if not r.get("ai_error") else f"AI analysis failed: {r['ai_error']}"
        return f'<div class="aa-card"><div class="aa-ct">Setup scorecard<span>engine only</span></div><div class="aa-empty">{esc(msg)}</div></div>'
    if not ai["setups"]:
        return ('<div class="aa-card"><div class="aa-ct">Setup scorecard<span>no setup taught</span></div><div class="aa-empty">'
                'No active setup in your playbook.<br>Open TEACH to import a PDF or write your rules.</div></div>')
    colors = {"PRESENT": GREEN, "NOT_PRESENT": RED, "UNCLEAR": AMBER}
    labels = {"PRESENT": "✓ PRESENT", "NOT_PRESENT": "✕ NOT PRESENT", "UNCLEAR": "◐ UNCLEAR"}
    out = ['<div class="aa-card"><div class="aa-ct">Setup scorecard<span>your rules, one by one</span></div>']
    for s in ai["setups"]:
        out.append(f'<div class="aa-sh"><div>{esc(s["name"])}</div><span>{s["match_pct"]}% MATCH · {esc(s["state"])}</span></div>')
        for c in s["checks"]:
            col = colors[c["status"]]
            refs = "".join(f'<span class="aa-ref">{esc(x)}</span>' for x in c["refs"])
            out.append(f'<div class="aa-ck" style="--c:{col}"><div class="h"><div>{esc(c["rule_name"])}{refs}</div><span>{labels[c["status"]]}</span></div>'
                       f'<div class="r">{esc(c["reason"])}</div><div class="b"><i style="width:{c["confidence"]}%"></i></div></div>')
    out.append("</div>")
    return "".join(out)


def html_read(ai: dict | None) -> str:
    if not ai:
        return '<div class="aa-card"><div class="aa-ct">The read<span>AI offline</span></div><div class="aa-empty">Supports, contradictions and what to watch appear here when the AI is online.</div></div>'

    def block(title, items, color, icon):
        li = "".join(f"<li>{esc(x)}</li>" for x in items) or '<li style="color:#6B6B6B">Nothing to report.</li>'
        return f'<div class="aa-sh" style="margin-top:12px"><div>{title}</div></div><ul class="aa-list" style="--c:{color};--ic:\'{icon}\'">{li}</ul>'
    return ('<div class="aa-card"><div class="aa-ct">The read<span>against YOUR playbook</span></div>'
            + block("What supports your setup", ai["supports"], GREEN, "+") + block("What contradicts it", ai["contradicts"], RED, "-")
            + block("Watch before acting", ai["watch_next"], AMBER, ">") + "</div>")


def html_zones(r: dict) -> str:
    ai, det = r.get("ai") or {}, r["det"]
    mine = {z["id"]: z for z in ai.get("my_zones", [])}
    rows = []
    for z in sorted(det["zones"], key=lambda x: (x["dist_atr"] if x["dist_atr"] is not None else 99)):
        col = GREEN if z["type"] == "demand" else RED
        qc = GREEN if z["quality"] >= 70 else AMBER if z["quality"] >= 50 else RED
        flags = []
        if z.get("htf"):
            flags.append("WEEKLY CONFLUENCE")
        if z["bos"]:
            flags.append("BROKE STRUCTURE")
        if z["aligned"]:
            flags.append("WITH TREND")
        m = mine.get(z["id"])
        if m and m["matches_my_style"]:
            flags.append("YOUR STYLE")
        role = esc(m["role"].replace("_", " ")) if m else "—"
        rows.append(f'<tr><td><b>{z["id"]}</b></td><td>{_pill(z["type"].upper(), col)}</td><td>{z["pattern"]}</td>'
                    f'<td class="n">{_fmt(z["bottom"])} – {_fmt(z["top"])}</td><td class="n">{z["thickness_atr"]} ATR</td>'
                    f'<td><span class="aa-q" style="--c:{qc};--w:{z["quality"]}%"><i></i>{z["quality"]}</span></td><td>{esc(z["status"])}</td>'
                    f'<td class="n">{_fmt(z["dist_pct"])}% · {z["dist_atr"]} ATR</td><td class="n">{esc(z["formed"])}</td><td>{role}</td>'
                    f'<td>{" ".join(_pill(f, CYAN) for f in flags) or "—"}</td></tr>')
    if not rows:
        return '<div class="aa-empty">No zones detected with your current zone style.</div>'
    head = "".join(f"<th>{h}</th>" for h in ("ID", "Type", "Pattern", "Zone", "Thickness", "Quality", "Status", "Distance", "Formed", "Role", "Flags"))
    return f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def html_levels(r: dict) -> str:
    det = r["det"]
    rows = []
    for lv in sorted(det["levels"], key=lambda x: -x["price"]):
        col = GREEN if lv["kind"] == "support" else RED
        rows.append(f'<tr><td><b>{lv["id"]}</b></td><td>{_pill(lv["kind"].upper(), col)}</td><td class="n">{_fmt(lv["price"])}</td><td class="n">{lv["touches"]}</td>'
                    f'<td class="n">{_fmt(lv["dist_pct"])}%</td><td>{"role reversal" if lv["flip"] else "—"}</td></tr>')
    for t in det["trendlines"]:
        col = GREEN if t["kind"] == "support" else RED
        rows.append(f'<tr><td><b>{t["id"]}</b></td><td>{_pill("TRENDLINE · " + t["kind"].upper(), col)}</td><td class="n">{_fmt(t["p1"])}</td>'
                    f'<td class="n">{t["touches"]}</td><td class="n">{_fmt(t["dist_pct"])}%</td><td>slope {t["slope_pct_per_bar"]}%/bar</td></tr>')
    if not rows:
        return '<div class="aa-empty">No support / resistance levels with enough touches.</div>'
    head = "".join(f"<th>{h}</th>" for h in ("ID", "Kind", "Price", "Touches", "Distance", "Note"))
    return f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def html_scenarios(ai: dict | None) -> str:
    if not ai:
        return ""
    cards = []
    for key, title, col in (("bull", "BULL CASE", GREEN), ("bear", "BEAR CASE", RED), ("neutral", "NEUTRAL / RANGE", AMBER)):
        s = ai["scenarios"][key]
        lvl = _fmt(s.get("level")) if isinstance(s.get("level"), (int, float)) else "—"
        cards.append(f'<div class="aa-sc" style="--c:{col}"><div class="t">{title}</div><div class="l">{lvl}</div>'
                     f'<div class="d"><b>Trigger:</b> {esc(s.get("trigger") or "—")}<br><b>Path:</b> {esc(s.get("path") or "—")}</div></div>')
    return '<div class="aa-scn">' + "".join(cards) + "</div>"


def png_for(r: dict, window: int) -> bytes:
    cache = r.setdefault("png_cache", {})
    if window not in cache:
        label, _ = verdict_text(r)
        cache[window] = render_png(r["df"], r["spec"], window=window, title=f"{r['ticker']}  ·  Daily  ·  {r['asof']}  ·  {label}  ·  score {r['score']}")
    return cache[window]


def render_chart(r: dict, window: int):
    cols = st.columns(6)
    show = {"zones": cols[0].checkbox("Zones", True, key="aa_t_zones"), "levels": cols[1].checkbox("Levels", True, key="aa_t_levels"),
            "lines": cols[2].checkbox("Trendlines", True, key="aa_t_lines"), "plan": cols[3].checkbox("Trade plan", True, key="aa_t_plan"),
            "mas": cols[4].checkbox("Moving averages", True, key="aa_t_mas"), "markers": cols[5].checkbox("Signals", True, key="aa_t_mk")}
    fig = fig_plotly(r["df"], r["spec"], window, show)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True, key="aa_chart", config={"displaylogo": False, "scrollZoom": True,
                                                                              "modeBarButtonsToRemove": ["lasso2d", "select2d"]})
    else:
        st.image(png_for(r, window), use_container_width=True)
        st.caption("Interactive chart needs plotly (add `plotly` to requirements.txt). Showing the annotated image instead.")


def render_report(r: dict, window: int, risk: float):
    st.markdown(html_hero(r), unsafe_allow_html=True)
    if r.get("ai_error"):
        st.warning(f"The AI step failed, so this is the engine-only view. Reason: {r['ai_error']}")
    render_chart(r, window)
    c1, c2, c3 = st.columns([1.05, 1.25, 1.0], gap="small")
    with c1:
        st.markdown(html_plan(r, risk), unsafe_allow_html=True)
    with c2:
        st.markdown(html_scorecard(r), unsafe_allow_html=True)
    with c3:
        st.markdown(html_read(r.get("ai")), unsafe_allow_html=True)
    if r.get("ai"):
        _sec("Scenarios", "what would change the picture")
        st.markdown(html_scenarios(r["ai"]), unsafe_allow_html=True)
    _sec("Supply & demand zones found", "every zone the engine detects, with your style applied")
    st.markdown(html_zones(r), unsafe_allow_html=True)
    _sec("Support, resistance and trendlines")
    st.markdown(html_levels(r), unsafe_allow_html=True)
    ai = r.get("ai")
    if ai and ai.get("narrative"):
        with st.expander("FULL ANALYSIS", expanded=False):
            st.write(ai["narrative"])
            if ai.get("context"):
                st.markdown("**Context (not used for the verdict)**")
                for c in ai["context"]:
                    st.markdown(f"- {c}")
    with st.expander("ENGINE EVIDENCE (exact numbers the AI was given)", expanded=False):
        d = r["det"]
        st.json({"structure": d["structure"], "ma_state": d["ma_state"], "sma": d["sma"], "volume": d["volume"], "refs": d["refs"], "swings": d["swings"],
                 "events": d["events"], "candle_signals": d["candles"], "weekly": ({"structure": r["det_w"]["structure"], "ma_state": r["det_w"]["ma_state"]} if r.get("det_w") else None)})
    if ai:
        _sec("Ask Arka about this chart", "follow-up questions use the same data")
        q = st.text_input("Question", key="aa_q", placeholder="e.g. Why is DZ1 better than DZ2? Where would you trail the stop?")
        if st.button("ASK", key="aa_ask") and q.strip():
            with st.spinner("Thinking…"):
                try:
                    ans = ask_followup(r, q.strip())
                except Exception as exc:
                    ans = f"Could not answer: {exc}"
            st.session_state.setdefault("aa_qa", []).append((r["ticker"], q.strip(), ans))
        for tk, qq, aa in reversed([x for x in st.session_state.get("aa_qa", []) if x[0] == r["ticker"]][-4:]):
            st.markdown(f'<div class="aa-lab"><b>YOU</b>{esc(qq)}</div><div class="aa-lab" style="border-left:3px solid #FF9F0A"><b>ARKA</b>{esc(aa)}</div>', unsafe_allow_html=True)
    _sec("Export")
    e1, e2, e3, e4 = st.columns(4, gap="small")
    e1.download_button("⬇ ANNOTATED CHART (PNG)", png_for(r, window), file_name=f"{r['ticker']}_arka_{r['asof']}.png", mime="image/png", use_container_width=True)
    payload = {k: r[k] for k in ("ticker", "asof", "score", "plan", "ai", "engine_plan")}
    payload.update({"zones": r["det"]["zones"], "levels": r["det"]["levels"]})
    report_json = json.dumps(payload, default=str, ensure_ascii=False, indent=2)
    e2.download_button("⬇ FULL REPORT (JSON)", report_json, file_name=f"{r['ticker']}_arka_{r['asof']}.json", mime="application/json", use_container_width=True)
    if e3.button("＋ ADD TO WATCHLIST", key="aa_wl", use_container_width=True):
        try:
            import user_data as ud
            added, err = ud.add_symbols(_SB, [r["ticker"]], ud.W_TABLE)
            if added:
                st.success(f"{r['ticker']} added to your watchlist")
            else:
                st.info(err or "Already in your watchlist")
        except Exception as exc:
            st.error(f"Could not add: {exc}")
    e4.caption("Research tool, not investment advice.")


_SB = None          # Supabase client handed over by app.py (used for "add to watchlist")


def _set_run(sym: str):
    st.session_state["aa_ticker"] = sym
    st.session_state["aa_run_request"] = sym
    st.session_state["aa_mode"] = "ANALYZE"


def render_getting_started():
    st.markdown(
        '<div class="aa-intro"><b>Teach once. Ask about any stock.</b> Arka learns how <b>you</b> read a chart (your setups, your supply and demand zones, '
        'your lines) and then analyses any stock the same way, marking everything on the chart.</div>'
        '<div class="aa-steps">'
        '<div class="aa-step"><div class="n">STEP 1 · TEACH</div><div class="t">Show Arka your style</div><div class="d">Import your strategy PDF, tick the zones you like and cross the ones you don\'t, or upload charts you marked up.</div></div>'
        '<div class="aa-step"><div class="n">STEP 2 · ANALYZE</div><div class="t">Type a stock</div><div class="d">Every zone, level and trendline is detected with exact prices, then judged against your playbook.</div></div>'
        '<div class="aa-step"><div class="n">STEP 3 · ACT</div><div class="t">Get the marked-up chart</div><div class="d">Verdict, scorecard, entry zone, stop, targets, position size and scenarios, ready to download.</div></div></div>',
        unsafe_allow_html=True)


def render_analyze():
    c1, c2, c3 = st.columns([3.4, 1.2, 1.4], gap="small")
    ticker = c1.text_input("Stock", key="aa_ticker", placeholder="e.g. RELIANCE · TCS · HDFCBANK · NIFTY50")
    window = c2.selectbox("Candles shown", [80, 120, 180, 250], index=1, key="aa_window")
    risk = c3.number_input("Risk per trade (₹)", min_value=0.0, value=1000.0, step=500.0, key="aa_risk")
    d1, d2, d3 = st.columns([4.4, 1.2, 1.2], gap="small")
    note = d1.text_input("Note for Arka (optional)", key="aa_note", placeholder="e.g. I'm only interested in long setups from fresh demand zones")
    ai_online = get_gemini() is not None
    use_ai = d2.checkbox("Use AI", value=True, key="aa_use_ai", disabled=not ai_online)
    go_clicked = d3.button("ANALYZE", type="primary", use_container_width=True, key="aa_go")
    recents = st.session_state.get("aa_recents", [])
    if recents:
        cols = st.columns(min(len(recents), 8) + 1, gap="small")
        cols[0].caption("RECENT")
        for col, sym in zip(cols[1:], recents[:8]):
            col.button(sym, key=f"aa_rc_{sym}", use_container_width=True, on_click=_set_run, args=(sym,))
    req = st.session_state.pop("aa_run_request", None)
    if go_clicked and ticker.strip():
        req = ticker.strip().upper()
    if req:
        with st.spinner(f"Reading {req}: detecting zones, levels and trendlines" + (", then judging against your playbook…" if (use_ai and ai_online) else "…")):
            try:
                st.session_state["aa_report"] = run_analysis(req, note, use_ai and ai_online, window)
                rec = [req] + [x for x in st.session_state.get("aa_recents", []) if x != req]
                st.session_state["aa_recents"] = rec[:8]
            except Exception as exc:
                st.session_state.pop("aa_report", None)
                st.error(f"Could not analyse {req}: {exc}")
    r = st.session_state.get("aa_report")
    if r:
        render_report(r, window, risk)
    elif not (active_items("setup") or active_items("lesson") or active_items("feedback")):
        render_getting_started()
    else:
        st.markdown('<div class="aa-empty">Type a stock above and press ANALYZE.<br>Your playbook is loaded and ready.</div>', unsafe_allow_html=True)


# ── RADAR ───────────────────────────────────────────────────
def _analyze_selected():
    sym = st.session_state.get("aa_radar_pick")
    if sym:
        _set_run(sym)


def render_radar():
    _sec("Radar", "scan a list with the same zone engine: which stocks are sitting at your zones right now")
    src = st.radio("List to scan", ["My watchlist", "Arka desk list", "Type symbols"], horizontal=True, key="aa_radar_src")
    if src == "My watchlist":
        symbols = list(st.session_state.get("watchlist", []))
    elif src == "Arka desk list":
        symbols = list(st.session_state.get("admin_watchlist", []))
    else:
        txt = st.text_area("Symbols (comma or line separated)", key="aa_radar_txt", height=80, placeholder="RELIANCE, TCS, INFY, HDFCBANK")
        symbols = [s.strip().upper() for s in re.split(r"[,\n;\s]+", txt) if s.strip()]
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) > 40:
        st.caption(f"Scanning the first 40 of {len(symbols)} symbols.")
        symbols = symbols[:40]
    st.caption(f"{len(symbols)} symbol(s) · uses your zone style ({style_source()}) · no AI cost")
    if st.button("SCAN FOR MY ZONES", type="primary", key="aa_radar_go", disabled=not symbols):
        with st.spinner(f"Scanning {len(symbols)} stocks…"):
            try:
                st.session_state["aa_radar"] = {"rows": radar_scan(symbols, get_style()), "at": datetime.now(IST).strftime("%d %b %H:%M IST")}
            except Exception as exc:
                st.error(f"Scan failed: {exc}")
    res = st.session_state.get("aa_radar")
    if not res:
        st.markdown('<div class="aa-empty">Press SCAN to rank your list by how ready each stock is.</div>', unsafe_allow_html=True)
        return
    rows = res["rows"]
    if not rows:
        st.warning("No usable price history came back for these symbols.")
        return
    body = []
    for x in rows:
        sc = x["score"]
        scol = GREEN if sc >= 72 else AMBER if sc >= 50 else T3
        zc = GREEN if x["ztype"] == "demand" else RED if x["ztype"] == "supply" else T3
        ch = x["chg"]
        body.append(f'<tr><td><b>{esc(x["symbol"])}</b></td><td class="n">{_fmt(x["close"])}</td>'
                    f'<td class="n" style="color:{GREEN if ch >= 0 else RED}">{"▲" if ch >= 0 else "▼"} {abs(ch):.2f}%</td><td>{esc(x["structure"])}</td>'
                    f'<td class="n">{_fmt(x["rsi"], 0)}</td>'
                    f'<td>{(_pill(x["ztype"].upper(), zc) + " " + esc(x["zone"])) if x["zone"] else "—"}</td>'
                    f'<td class="n">{x["quality"] if x["quality"] is not None else "—"}</td><td>{esc(x["status"] or "—")}</td>'
                    f'<td class="n">{(_fmt(x["dist_pct"]) + "%") if x["dist_pct"] is not None else "—"}</td>'
                    f'<td>{esc(x["direction"] or "—")}</td><td class="n">{(str(x["rr"]) + " R") if x["rr"] else "—"}</td>'
                    f'<td><span class="aa-q" style="--c:{scol};--w:{sc}%"><i></i>{sc}</span></td></tr>')
    head = "".join(f"<th>{h}</th>" for h in ("Symbol", "Price", "1D", "Trend", "RSI", "Nearest zone", "Quality", "Status", "Distance", "Plan", "T1 R:R", "Readiness"))
    st.markdown(f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)
    st.caption(f"Scanned {res['at']}. Readiness = zone quality, proximity, trend, weekly confluence and confirmation. Run ANALYZE on a name for the full AI read.")
    p1, p2 = st.columns([3, 1], gap="small")
    p1.selectbox("Open in ANALYZE", [x["symbol"] for x in rows], key="aa_radar_pick")
    p2.write("")
    p2.button("ANALYZE SELECTED", key="aa_radar_open", use_container_width=True, on_click=_analyze_selected)


# ── TEACH ───────────────────────────────────────────────────
def _setup_summary_html(s: dict) -> str:
    return (f'<div class="aa-item" style="--c:{AMBER}"><div class="h"><b>{esc(s.get("setup_name", "Unnamed setup"))}</b>'
            f'{_pill("CONFIDENCE " + str(s.get("confidence", "?")), AMBER)}</div><div class="d">{esc(s.get("summary", ""))}<br>'
            f'{len(s.get("conditions", []))} conditions · {len(s.get("confirmation", []))} confirmations · {len(s.get("invalidation", []))} invalidations · '
            f'{len(s.get("zone_definitions", []))} zone definitions · {len(s.get("line_definitions", []))} line definitions · {len(s.get("examples", []))} examples</div></div>')


def teach_pdf():
    st.markdown('<div class="aa-intro">Upload your strategy PDF. Arka reads text, diagrams and marked-up examples, turns them into a structured setup '
                '(rules, trigger, confirmation, invalidation, how you draw supply / demand zones and lines) and shows you what it understood. '
                '<b>You approve it before it is used.</b></div>', unsafe_allow_html=True)
    pdf = st.file_uploader("Strategy PDF", type=["pdf"], key="aa_pdf")
    if pdf and st.button("READ MY PDF", type="primary", key="aa_pdf_go"):
        with st.spinner("Arka is reading the whole PDF: text, diagrams, screenshots and examples…"):
            st.session_state["aa_pending_setup"] = understand_pdf_setup(pdf, pdf.name)
            st.session_state["aa_pending_text"] = extract_pdf_text(pdf)
    pend = st.session_state.get("aa_pending_setup")
    if not pend:
        return
    if pend.get("error"):
        st.error(pend["error"])
        return
    st.markdown(_setup_summary_html(pend), unsafe_allow_html=True)
    if pend.get("ambiguities"):
        st.warning("Arka was unsure about: " + " | ".join(map(str, pend["ambiguities"][:6])))
    with st.expander("WHAT ARKA UNDERSTOOD (review before approving)", expanded=True):
        st.json(pend)
    if st.button("APPROVE & ACTIVATE THIS SETUP", type="primary", key="aa_pdf_ok"):
        if save_item("setup", pend.get("setup_name", "PDF setup"), pend, True, pend.get("source_name", ""), st.session_state.get("aa_pending_text", "")):
            st.session_state.pop("aa_pending_setup", None)
            st.success("Setup saved and active. ANALYZE will now check stocks against it.")
            st.rerun()


def teach_label():
    st.markdown('<div class="aa-intro"><b>The fastest way to teach your zones.</b> Arka detects every supply and demand zone on a chart. '
                'Mark the ones that are <b>your kind of zone</b> and the ones that are not. After a few charts it learns your style: how thick, how fresh, '
                'how strong the move away. No drawing needed.</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([4, 1], gap="small")
    t = c1.text_input("Stock to learn from", key="aa_lab_ticker", placeholder="Choose a chart where you can see both good and bad zones")
    if c2.button("DETECT ZONES", type="primary", key="aa_lab_go", use_container_width=True) and t.strip():
        try:
            df = fetch_ohlc(t.strip().upper(), "1d", "2y")
            det = analyze_market(df, dict(get_style(), min_quality=20, fresh_only=False))
            st.session_state["aa_lab"] = {"ticker": t.strip().upper(), "df": df, "det": det}
        except Exception as exc:
            st.error(str(exc))
    lab = st.session_state.get("aa_lab")
    if not lab:
        return
    det, tk = lab["det"], lab["ticker"]
    zones = sorted(det["zones"], key=lambda z: -z["quality"])[:14]
    if not zones:
        st.info("No zones detected on this chart. Try a more volatile stock.")
        return
    spec = {"zones": zones, "levels": [], "lines": [], "markers": [], "candles": [], "plan": None}
    fig = fig_plotly(lab["df"], spec, 160, {"plan": False, "mas": False, "markers": False})
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True, key="aa_lab_chart", config={"displaylogo": False, "scrollZoom": True})
    else:
        st.image(render_png(lab["df"], spec, window=160, title=f"{tk} · detected zones"), use_container_width=True)
    _sec("Which of these are YOUR kind of zone?", f"{tk} · strongest {len(zones)} shown")
    choices = {}
    for z in zones:
        col = GREEN if z["type"] == "demand" else RED
        a, b, c = st.columns([3.2, 2.2, 2.6], gap="small")
        a.markdown(f'<div class="aa-lab"><b>{z["id"]}</b>{_pill(z["type"].upper(), col)} {z["pattern"]} · {_fmt(z["bottom"])}–{_fmt(z["top"])} · '
                   f'{z["thickness_atr"]} ATR thick · move away {z["dep_atr"]} ATR · Q{z["quality"]} · {esc(z["status"])}</div>', unsafe_allow_html=True)
        pick = b.radio("Verdict", ["Skip", "✔ Mine", "✘ Not mine"], horizontal=True, key=f"aa_lab_{tk}_{z['id']}", label_visibility="collapsed")
        why = c.text_input("Why (optional)", key=f"aa_why_{tk}_{z['id']}", label_visibility="collapsed", placeholder="Why? e.g. too wide, already tested, no real impulse")
        choices[z["id"]] = (z, pick, why)
    n_ok = sum(1 for v in choices.values() if v[1] == "✔ Mine")
    n_no = sum(1 for v in choices.values() if v[1] == "✘ Not mine")
    if st.button(f"SAVE MY LABELS ({n_ok} mine · {n_no} not mine)", type="primary", key="aa_lab_save", disabled=(n_ok + n_no) == 0):
        labels = [{"zone_id": zid, "zone_type": z["type"], "approved": pick == "✔ Mine", "reason": why, "features": zone_features(z)}
                  for zid, (z, pick, why) in choices.items() if pick != "Skip"]
        payload = {"ticker": tk, "asof": det["asof"], "labels": labels, "summary": f"{n_ok} approved and {n_no} rejected zones on {tk}"}
        if save_item("feedback", f"{tk} · {n_ok} mine / {n_no} not · {det['asof']}", payload, True):
            st.success("Saved. Open ZONE STYLE and press LEARN to turn your labels into engine settings.")


def teach_screenshot():
    st.markdown('<div class="aa-intro">Upload charts <b>you</b> marked up (zones, lines, entries, stops, targets) and explain what you did. '
                'Arka studies the drawings and writes down how you read a chart, which it then applies to every stock you analyse.</div>', unsafe_allow_html=True)
    files = st.file_uploader("Marked-up charts (PNG / JPG)", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="aa_imgs")
    why = st.text_area("Explain your markings", height=110, key="aa_img_why",
                       placeholder="e.g. The green boxes are my demand zones: a tight base of 1-3 candles, then a strong move away. I only buy fresh zones in an uptrend. Stop below the zone, first target at the previous swing high.")
    if files and st.button("LEARN FROM THESE CHARTS", type="primary", key="aa_img_go"):
        with st.spinner("Studying your charts…"):
            st.session_state["aa_pending_lesson"] = learn_from_images([f.getvalue() for f in files[:6]], why)
    pend = st.session_state.get("aa_pending_lesson")
    if not pend:
        return
    if pend.get("error"):
        st.error(pend["error"])
        return
    st.markdown(f'<div class="aa-item" style="--c:{CYAN}"><div class="h"><b>{esc(pend.get("lesson_title", "Lesson"))}</b>{_pill("NEW LESSON", CYAN)}</div>'
                f'<div class="d">{esc(pend.get("summary", ""))}</div></div>', unsafe_allow_html=True)
    with st.expander("WHAT ARKA LEARNED (review)", expanded=True):
        st.json(pend)
    if st.button("SAVE LESSON TO MY PLAYBOOK", type="primary", key="aa_img_ok"):
        if save_item("lesson", pend.get("lesson_title", "Chart lesson"), pend, True, "screenshot"):
            st.session_state.pop("aa_pending_lesson", None)
            st.success("Lesson saved and active.")
            st.rerun()


def teach_quick():
    st.markdown('<div class="aa-intro">Prefer typing? Write a setup as plain rules, or teach a lesson in your own words.</div>', unsafe_allow_html=True)
    _sec("Write a setup", "one rule per line")
    name = st.text_input("Setup name", key="aa_m_name", placeholder="Fresh demand pullback")
    summary = st.text_area("What is this setup?", key="aa_m_sum", height=70)
    conds = st.text_area("Rules (one per line)", key="aa_m_conds", height=150,
                         placeholder="Price is in an uptrend (higher highs, higher lows)\nPrice returns to a fresh demand zone\nThe demand zone is thin (base of 1-3 candles)\nA rejection candle forms inside the zone")
    trig = st.text_input("Trigger", key="aa_m_trig", placeholder="Bullish close back above the zone's proximal line")
    inv = st.text_input("Invalidation", key="aa_m_inv", placeholder="Close below the zone's distal line")
    if st.button("SAVE & ACTIVATE SETUP", type="primary", key="aa_m_save"):
        if not name.strip() or not conds.strip():
            st.warning("Give the setup a name and at least one rule.")
        elif save_item("setup", name, manual_setup(name, summary, conds, trig, inv), True, "manual"):
            st.success("Setup saved and active.")
            st.rerun()
    _sec("Teach a lesson", "how you define zones and lines, in your words")
    lt = st.text_input("Lesson title", key="aa_l_title", placeholder="How I draw demand zones")
    lx = st.text_area("Lesson", key="aa_l_text", height=130, placeholder="A demand zone starts at the lowest wick of the base and ends at the top of the base bodies. I ignore zones thicker than 2 ATR...")
    if st.button("SAVE LESSON", key="aa_l_save"):
        if not lt.strip() or not lx.strip():
            st.warning("Add a title and the lesson text.")
        elif save_item("lesson", lt, {"lesson_title": lt.strip(), "summary": lx.strip(), "entry_logic": "", "stop_logic": "", "target_logic": ""}, True, "manual"):
            st.success("Lesson saved and active.")
            st.rerun()


def _all_labels():
    ap, rj = [], []
    for it in active_items("feedback"):
        for lab in it["payload"].get("labels", []):
            (ap if lab.get("approved") else rj).append(lab.get("features", {}))
    return ap, rj


def teach_style():
    style = get_style()
    ap, rj = _all_labels()
    st.markdown(f'<div class="aa-intro">Your zone style tells the engine what counts as a zone. Current source: <b>{esc(style_source())}</b>. '
                f'You have labelled <b>{len(ap)}</b> zone(s) as yours and <b>{len(rj)}</b> as not yours.</div>', unsafe_allow_html=True)
    _sec("Learn from my labels")
    if st.button("LEARN MY ZONE STYLE", type="primary", key="aa_learn", disabled=len(ap) < 3):
        params, notes = calibrate_style(ap, rj)
        st.session_state["aa_learned"] = {"params": params, "notes": notes}
    if len(ap) < 3:
        st.caption("Approve at least 3 zones in LABEL ZONES first.")
    learned = st.session_state.get("aa_learned")
    if learned and learned["params"]:
        for n in learned["notes"]:
            st.markdown(f"- {n}")
        st.json(learned["params"])
        if st.button("SAVE AS MY ZONE STYLE", type="primary", key="aa_learn_save"):
            if save_item("style", "Learned style · " + datetime.now(IST).strftime("%d %b %Y"), {"params": learned["params"], "notes": learned["notes"], "summary": "Learned from labelled zones"}, True):
                st.session_state.pop("aa_learned", None)
                st.success("Saved. Every analysis now uses your style.")
                st.rerun()
    _sec("Or set it by hand")
    c = st.columns(4, gap="small")
    vals = {
        "base_max": c[0].number_input("Max base candles", 1, 8, int(style["base_max"]), 1, key="aa_s_base", help=STYLE_HELP["base_max"]),
        "base_range_atr": c[1].number_input("Base candle range (ATR)", 0.3, 2.5, float(style["base_range_atr"]), 0.1, key="aa_s_brng", help=STYLE_HELP["base_range_atr"]),
        "impulse_atr": c[2].number_input("Min impulse (ATR)", 0.8, 6.0, float(style["impulse_atr"]), 0.1, key="aa_s_imp", help=STYLE_HELP["impulse_atr"]),
        "max_zone_atr": c[3].number_input("Max zone thickness (ATR)", 0.5, 5.0, float(style["max_zone_atr"]), 0.1, key="aa_s_thick", help=STYLE_HELP["max_zone_atr"]),
    }
    c2 = st.columns(4, gap="small")
    vals["min_quality"] = int(c2[0].number_input("Min quality", 0, 90, int(style["min_quality"]), 5, key="aa_s_q", help=STYLE_HELP["min_quality"]))
    vals["sr_tol_atr"] = c2[1].number_input("S/R tolerance (ATR)", 0.2, 2.0, float(style["sr_tol_atr"]), 0.1, key="aa_s_tol", help=STYLE_HELP["sr_tol_atr"])
    vals["sr_min_touches"] = int(c2[2].number_input("S/R min touches", 2, 6, int(style["sr_min_touches"]), 1, key="aa_s_touch", help=STYLE_HELP["sr_min_touches"]))
    vals["fresh_only"] = c2[3].checkbox("Fresh zones only", value=bool(style["fresh_only"]), key="aa_s_fresh")
    vals["base_max"] = int(vals["base_max"])
    b1, b2 = st.columns(2, gap="small")
    if b1.button("SAVE THESE SETTINGS", key="aa_s_save", use_container_width=True):
        if save_item("style", "Manual style · " + datetime.now(IST).strftime("%d %b %Y"), {"params": vals, "summary": "Set by hand"}, True):
            st.success("Saved.")
            st.rerun()
    if b2.button("RESET TO ARKA DEFAULTS", key="aa_s_reset", use_container_width=True):
        for it in active_items("style"):
            set_item_active(it["item_id"], False)
        st.rerun()


def render_teach():
    tabs = st.tabs(["IMPORT PDF", "LABEL ZONES", "FROM SCREENSHOT", "QUICK RULES", "ZONE STYLE"])
    with tabs[0]:
        teach_pdf()
    with tabs[1]:
        teach_label()
    with tabs[2]:
        teach_screenshot()
    with tabs[3]:
        teach_quick()
    with tabs[4]:
        teach_style()


# ── PLAYBOOK ────────────────────────────────────────────────
def render_playbook():
    c1, c2 = st.columns([5, 1], gap="small")
    c1.markdown('<div class="aa-intro">Everything you have taught Arka. <b>Active</b> items are used in every analysis; switch items off to test a different approach without deleting anything.</div>', unsafe_allow_html=True)
    if c2.button("↻ RELOAD", key="aa_reload", use_container_width=True):
        load_items(force=True)
        st.rerun()
    items = load_items()
    if not items:
        render_getting_started()
        return
    colors = {"setup": AMBER, "lesson": CYAN, "feedback": GREEN, "style": PURPLE}
    for kind in ("setup", "lesson", "feedback", "style"):
        group = [i for i in items if i["kind"] == kind]
        if not group:
            continue
        _sec(KIND_LABEL[kind] + "S" if kind != "feedback" else "LABELLED ZONES", f"{len(group)} saved")
        for it in group:
            p = it["payload"]
            desc = p.get("summary") or p.get("setup_name") or ""
            meta = {"setup": f'{len(p.get("conditions", []))} rules · {len(p.get("zone_definitions", []))} zone definitions',
                    "lesson": f'{len(p.get("zone_definitions", []))} zone definitions · {len(p.get("line_definitions", []))} line definitions',
                    "feedback": f'{sum(1 for x in p.get("labels", []) if x.get("approved"))} mine · {sum(1 for x in p.get("labels", []) if not x.get("approved"))} not mine',
                    "style": ", ".join(f"{k} {v}" for k, v in list(p.get("params", {}).items())[:4])}[kind]
            st.markdown(f'<div class="aa-item" style="--c:{colors[kind] if it["active"] else "#2B2B2B"}"><div class="h"><b>{esc(it["name"])}</b>'
                        f'{_pill("ACTIVE" if it["active"] else "OFF", GREEN if it["active"] else T3)}</div>'
                        f'<div class="d">{esc(str(desc)[:260])}<br>{esc(meta)} · saved {esc(it["saved_at"][:10])}</div></div>', unsafe_allow_html=True)
            a, b, c = st.columns([1.2, 1.4, 4], gap="small")
            on = a.checkbox("Active", value=it["active"], key=f"aa_act_{it['item_id']}")
            if on != it["active"]:
                set_item_active(it["item_id"], on)
                st.rerun()
            sure = b.checkbox("Confirm delete", key=f"aa_dok_{it['item_id']}")
            if c.button("DELETE", key=f"aa_del_{it['item_id']}", disabled=not sure):
                delete_item(it["item_id"])
                st.rerun()
            with st.expander("View details"):
                st.json(p)


# ── ENTRY POINT ─────────────────────────────────────────────
def _consume_autorun():
    """Watchlist ANALYSIS button / security page send us here with a symbol."""
    sym = st.session_state.pop("arka_autorun_v8", None)
    if sym:
        st.session_state["aa_ticker"] = str(sym).upper()
        st.session_state["aa_run_request"] = str(sym).upper()
        st.session_state["aa_mode"] = "ANALYZE"
    elif "aa_ticker" not in st.session_state:
        pre = st.session_state.get("m1_ticker") or st.session_state.get("m1_ticker_v8")
        if pre:
            st.session_state["aa_ticker"] = str(pre).upper()


def render_arka_ai(supabase=None):
    global _SB
    _SB = supabase
    _inject_css()
    _consume_autorun()
    st.markdown(html_status(), unsafe_allow_html=True)
    st.session_state.setdefault("aa_mode", "ANALYZE")
    mode = st.radio("Mode", ["ANALYZE", "RADAR", "TEACH", "PLAYBOOK"], horizontal=True, key="aa_mode", label_visibility="collapsed")
    if mode == "ANALYZE":
        render_analyze()
    elif mode == "RADAR":
        render_radar()
    elif mode == "TEACH":
        render_teach()
    else:
        render_playbook()
    st.caption("Arka AI is a research tool. Zones and levels are rule-based approximations of discretionary chart reading; scores measure how well the evidence matches "
               "your playbook, not the probability of profit. Not investment advice.")
