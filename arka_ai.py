"""
arka_ai.py — Arka AI v11 · YOUR playbook, YOUR zones, YOUR trade plan — now with evidence

NEW IN v11 (speed + redesign; analysis logic and your saved playbook are unchanged)
  SPEED     * Analysis: price data, NIFTY context and the results date load in parallel; the weekly chart is built from the
              daily data (one download fewer); the image sent to the AI is smaller; and the AI's "thinking" is capped by the
              new Speed setting (Fast / Balanced / Deep). Balanced is typically 2-3x faster than v10 with the same output.
            * A live progress panel shows each step and the total time.
            * Saving is instant: the playbook is cached and updated in place after every save / switch / delete, instead of
              re-downloading the whole playbook from Pinecone (v10 reloaded everything, twice for a zone style).
            * Only the TEACH section you open runs (v10 ran all five tabs, including a chart, on every click).
            * Journal fetches every plan's prices in one batched download; the chart image is built only when you download it;
              the interactive chart is reused instead of rebuilt on every click.
  DESIGN    * Same look as the terminal: Inter, sentence case, soft cards; verdict hero with score ring and plan tiles;
              chart layers as pills; phone friendly (shorter chart, stacked cards).

NEW IN v10 (your saved playbook is read unchanged)
  EXACT MAPPING   * Prices are the prices on your chart: Yahoo data is no longer dividend-adjusted, so zones and levels sit where
                    you see them on TradingView / your broker (v9 shifted older zones down by the dividends paid since).
                  * The AI can no longer place a price. Its plan is rebuilt by Python from detections: entry = the exact top / bottom of
                    a detected zone of the right type, stop beyond its distal line, each target = the exact price of a detected level
                    or opposing zone. Anything that does not map is dropped and listed under ENGINE CHECK OF THE AI.
                  * Scorecard = every rule of every active setup, once, in playbook order, matched by rule id. Match %, score, grade and
                    verdict are computed by Python from the rule statuses (the model used to pick its own score).
                  * Charts show the swing points that make each level and trendline, each zone's distal line and price range, and
                    R multiples on the targets. Today's unfinished candle is flagged and cannot confirm a trigger.
  EVIDENCE        * TRACK RECORD: every zone your style would have drawn in the last 2 years is replayed with no hindsight (first return,
                    1R / 2R before the stop), per stock in ANALYZE and across a list in RADAR. This is the test of your ZONE STYLE.
                  * JOURNAL: log a plan and Arka follows it (waiting / filled / target 1 / stopped), giving a real forward record in R.
  RISK            * Room before the first obstacle, in R; plans with less than 1R of room are flagged and scored down.
                  * NIFTY trend, 3-month return vs NIFTY, and a warning when results are due within two weeks.
  PLUMBING        * Works with the current google-genai SDK (the old google-generativeai reached end of life) and picks the newest
                    stable flash model your key supports. Add `google-genai` to requirements.txt.
                  * Rejected-zone reasons you typed are now given to the AI; style learning uses rejections as well as approvals.

v9 notes follow.

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
try:                                    # current SDK (package: google-genai)
    from google import genai as genai_new
    from google.genai import types as genai_types
    HAS_GENAI_NEW = True
except Exception:  # pragma: no cover
    genai_new = genai_types = None
    HAS_GENAI_NEW = False
try:                                    # legacy SDK (package: google-generativeai, end of life Nov 2025) — used only if the new one is absent
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        import google.generativeai as genai
    HAS_GENAI_OLD = True
except Exception:  # pragma: no cover
    genai = None
    HAS_GENAI_OLD = False
HAS_GEMINI = HAS_GENAI_NEW or HAS_GENAI_OLD

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
V9_VERSION = "arka-v10"
DEFAULT_MODEL = "gemini-2.5-flash"         # only a fallback: the newest stable flash model your key can use is picked automatically
IST = timezone(timedelta(hours=5, minutes=30))

# ── Theme (matches the terminal) ────────────────────────────
AMBER, AMBER2 = "#FF9F0A", "#FFB340"
GREEN, RED, CYAN, PURPLE, YELLOW = "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
BG, S1, S2, S3 = "#050505", "#0A0A0A", "#0F0F0F", "#151515"
LINE, LINE2 = "#1F1F1F", "#2B2B2B"
IVORY, T2, T3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace"
FONT = "'Inter',system-ui,sans-serif"

KIND_LABEL = {"setup": "SETUP", "lesson": "LESSON", "feedback": "LABELLED ZONES", "style": "ZONE STYLE", "call": "LOGGED PLAN"}

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


_CONST_VEC = [0.001] * 768     # storage does not need real embeddings: items are read back by id, not by similarity


def _invalidate():
    st.session_state.pop("aa_items_cache", None)


def _cache_put(item: dict):
    """Add / replace one item in the session cache (newest first) instead of reloading the whole playbook."""
    cache = st.session_state.get("aa_items_cache")
    if cache is None:
        return
    cache[:] = [i for i in cache if i["item_id"] != item["item_id"]]
    cache.insert(0, item)


def _cache_set(item_id: str, **changes):
    for i in st.session_state.get("aa_items_cache") or []:
        if i["item_id"] == item_id:
            i.update(changes)


def _cache_drop(item_id: str):
    cache = st.session_state.get("aa_items_cache")
    if cache is not None:
        cache[:] = [i for i in cache if i["item_id"] != item_id]


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
    item_id = f"{kind}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
    saved_at = datetime.now(timezone.utc).isoformat()
    idx = get_pinecone_index()
    if idx is None:
        _local_items()[item_id] = {"item_id": item_id, "kind": kind, "name": name, "active": activate, "saved_at": saved_at,
                                   "source": source_name, "payload": payload}
        if activate and kind == "style":
            for it in _local_items().values():
                if it["kind"] == "style" and it["item_id"] != item_id:
                    it["active"] = False
                    _cache_set(it["item_id"], active=False)
        _cache_put(_local_items()[item_id])
        return True
    try:
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(compact) > 34000:
            st.error("This item is too large to store (over 34 KB). Trim it or split the document.")
            return False
        emb = _CONST_VEC
        meta = {"memory_kind": kind, "item_id": item_id, "name": name, "active": bool(activate), "saved_at": saved_at,
                "source_name": source_name, "source_text": str(source_text or "")[:3000], "version": V9_VERSION}
        if kind == "setup":
            meta.update({"setup_id": item_id, "setup_name": name, "setup_json": compact})
        else:
            meta["item_json"] = compact
        idx.upsert(vectors=[{"id": item_id, "values": emb, "metadata": meta}])
        if activate and kind == "style":                           # only one active style at a time (uses the cached list: no reload)
            for it in [i for i in load_items() if i["kind"] == "style" and i["item_id"] != item_id and i["active"]]:
                set_item_active(it["item_id"], False)
        _cache_put({"item_id": item_id, "kind": kind, "name": name, "active": bool(activate), "saved_at": saved_at,
                    "source": source_name, "payload": payload})
        return True
    except Exception as exc:
        st.error(f"Could not save to your playbook: {exc}")
        return False


def set_item_active(item_id: str, active: bool) -> bool:
    local = _local_items()
    if item_id in local:
        local[item_id]["active"] = active
        _cache_set(item_id, active=active)
        return True
    idx = get_pinecone_index()
    if idx is None:
        return False
    try:
        idx.update(id=item_id, set_metadata={"active": bool(active)})
        _cache_set(item_id, active=bool(active))
        return True
    except Exception:
        return False


def delete_item(item_id: str) -> bool:
    local = _local_items()
    if item_id in local:
        local.pop(item_id)
        _cache_drop(item_id)
        return True
    idx = get_pinecone_index()
    if idx is None:
        return False
    try:
        idx.delete(ids=[item_id])
        _cache_drop(item_id)
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
7. The scorecard must contain EVERY rule id of EVERY active setup exactly once, using the ids exactly as written in the playbook.
8. A candle or event marked "provisional" belongs to today's unfinished candle. It cannot confirm a trigger; say so.
9. Reply with ONE JSON object and nothing else."""


_MODEL_RX = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash$")


def _rank_models(names) -> list:
    """Stable 'gemini-<version>-flash' names, newest first, then safe fallbacks."""
    stable = sorted({n for n in names if _MODEL_RX.match(n)}, key=lambda n: float(_MODEL_RX.match(n).group(1)), reverse=True)
    out = []
    for n in [_secret("ARKA_GEMINI_MODEL")] + stable + [DEFAULT_MODEL, "gemini-flash-latest"]:
        if n and n not in out:
            out.append(n)
    return out


@st.cache_resource(show_spinner=False)
def _ai_backend(key_tail: str):
    """{"kind": "new" | "old", "client": ..., "models": [candidates, best first]} or None."""
    if not GEMINI_KEY:
        return None
    if HAS_GENAI_NEW:
        try:
            try:
                client = genai_new.Client(api_key=GEMINI_KEY, http_options=genai_types.HttpOptions(timeout=120_000))
            except Exception:
                client = genai_new.Client(api_key=GEMINI_KEY)
            names = []
            try:
                names = [str(getattr(m, "name", "")).split("/")[-1] for m in client.models.list()]
            except Exception:
                pass
            return {"kind": "new", "client": client, "models": _rank_models(names)}
        except Exception:
            pass
    if HAS_GENAI_OLD:
        try:
            genai.configure(api_key=GEMINI_KEY)
            names = []
            try:
                names = [str(m.name).split("/")[-1] for m in genai.list_models()]
            except Exception:
                pass
            return {"kind": "old", "client": None, "models": _rank_models(names)}
        except Exception:
            pass
    return None


def get_gemini():
    """The AI backend, or None when no key / SDK is available (the engine still works)."""
    if not HAS_GEMINI or not GEMINI_KEY:
        return None
    return _ai_backend(GEMINI_KEY[-6:])


def ai_model_name() -> str:
    b = get_gemini()
    return (st.session_state.get("aa_model_ok") or (b["models"][0] if b else "")) if b else ""


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
            out = json.loads(candidate)
            if isinstance(out, dict):
                return out
        except Exception:
            pass
    repaired = re.sub(r",\s*([}\]])", r"\1", _extract_json(raw))
    out = json.loads(repaired)
    if not isinstance(out, dict):
        raise ValueError("the AI did not return a JSON object")
    return out


def _model_gone(exc) -> bool:
    t = str(exc).lower()
    return any(k in t for k in ("not found", "404", "is not supported", "deprecated", "no longer available", "unknown model", "not_found"))


SPEEDS = {"Fast": 0, "Balanced": 1024, "Deep": None}         # AI thinking budget in tokens; None = the model decides (slowest)


def _thinking_budget():
    return SPEEDS.get(st.session_state.get("aa_speed", "Balanced"), 1024)


def _generate(prompt: str, images=None, pdf: bytes | None = None, want_json: bool = True) -> str:
    """One Gemini call through whichever SDK is installed. A retired model name moves on to the next candidate."""
    b = get_gemini()
    if b is None:
        raise RuntimeError("Gemini is not configured (check GEMINI_KEY and add `google-genai` to requirements.txt).")
    ok = st.session_state.get("aa_model_ok")
    models = ([ok] if ok else []) + [m for m in b["models"] if m != ok]
    last = None
    for model in models[:4]:
        try:
            if b["kind"] == "new":
                parts = [prompt]
                if pdf:
                    parts.append(genai_types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
                for png in images or []:
                    parts.append(genai_types.Part.from_bytes(data=png, mime_type="image/png"))
                kw = {"system_instruction": SYSTEM_PROMPT, "temperature": 0.0, "response_mime_type": "application/json" if want_json else None}
                budget = _thinking_budget()
                if budget is not None and not st.session_state.get("aa_no_thinking_cfg"):
                    try:
                        kw["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=budget)
                    except Exception:
                        pass
                try:
                    resp = b["client"].models.generate_content(model=model, contents=parts, config=genai_types.GenerateContentConfig(**kw))
                except Exception as exc:
                    if "thinking_config" in kw and "think" in str(exc).lower():      # this model does not accept a budget: run without it
                        st.session_state["aa_no_thinking_cfg"] = True
                        kw.pop("thinking_config")
                        resp = b["client"].models.generate_content(model=model, contents=parts, config=genai_types.GenerateContentConfig(**kw))
                    else:
                        raise
            else:
                m = genai.GenerativeModel(model_name=model, system_instruction=SYSTEM_PROMPT)
                content = [prompt]
                if pdf:
                    content.append({"mime_type": "application/pdf", "data": pdf})
                for png in images or []:
                    content.append({"mime_type": "image/png", "data": png})
                gc = {"temperature": 0.0, **({"response_mime_type": "application/json"} if want_json else {})}
                resp = m.generate_content(content, generation_config=gc)
            st.session_state["aa_model_ok"] = model
            return (getattr(resp, "text", "") or "").strip()
        except Exception as exc:
            last = exc
            if not _model_gone(exc):
                break
    raise RuntimeError(f"Gemini call failed: {last}")


def call_json(prompt: str, images=None, pdf: bytes | None = None) -> dict:
    """images: list of PNG bytes. One automatic retry when the reply is not valid JSON."""
    raw = _generate(prompt, images, pdf, True)
    try:
        return parse_json_loose(raw)
    except Exception:
        raw = _generate(prompt + "\n\nYour previous reply was not valid JSON. Reply again with ONE valid JSON object only.", images, pdf, True)
        return parse_json_loose(raw)


def call_text(prompt: str) -> str:
    return _generate(prompt, None, None, False)


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
            hist = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=False)   # prices as on your chart (splits adjusted, dividends not)
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
    raw = yf.download(tick, period="2y", interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)
    frames = _split_download(raw, tick)
    return {t[:-3]: f for t, f in frames.items()}


def weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Weekly candles (weeks ending Friday) built from the daily data, so no second download is needed."""
    w = daily.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open", "close"])
    return w


def _in_thread(fn, *args):
    """Run fn(*args) in a background thread that can still use Streamlit's caches. Returns a Future-like object."""
    import concurrent.futures as cf
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
    except Exception:
        add_script_run_ctx = ctx = None

    def run():
        if ctx is not None:
            try:
                import threading
                add_script_run_ctx(threading.current_thread(), ctx)
            except Exception:
                pass
        return fn(*args)
    fut = ex.submit(run)
    ex.shutdown(wait=False)
    return fut


def _result(fut, timeout: float, default=None):
    try:
        return fut.result(timeout=timeout)
    except Exception:
        return default


def live_fraction(last_date, now=None):
    """Share of today's session already traded (0.15-1) when the last daily candle is still forming, else None."""
    now = now or datetime.now(IST)
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5 or pd.Timestamp(last_date).date() != now.date() or not (9 * 60 + 15 <= mins < 15 * 60 + 30):
        return None
    return max(0.15, min(1.0, (mins - (9 * 60 + 15)) / 375.0))


def is_index(ticker: str) -> bool:
    t = (ticker or "").upper().replace(" ", "")
    return t in _SYMBOL_MAP or t.startswith("^")


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=128)
def next_results_date(ticker: str):
    """Next earnings date from Yahoo's calendar as 'YYYY-MM-DD', or None. Never raises."""
    if not HAS_YFINANCE or is_index(ticker):
        return None
    try:
        cal = yf.Ticker(yahoo_symbols(ticker)[0]).calendar
        val = cal.get("Earnings Date") if isinstance(cal, dict) else (cal.loc["Earnings Date"].tolist() if hasattr(cal, "loc") else None)
        dates = [pd.Timestamp(v) for v in (val if isinstance(val, (list, tuple)) else [val]) if v is not None]
        today = pd.Timestamp(datetime.now(IST).date())
        fut = sorted(d.tz_localize(None) if d.tzinfo else d for d in dates)
        fut = [d for d in fut if d >= today]
        return str(fut[0])[:10] if fut else None
    except Exception:
        return None


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
                    "touch_idx": sorted(int(i) for i in idxs), "touch_px": [round(float(p), 2) for _, p in sorted(zip(idxs, prices))],
                    "flip": len({x[2] for x in cl}) == 2,
                    "score": round(sum(0.5 + 0.5 * (i / max(1, n - 1)) for i in idxs), 2),
                    "dist_pct": round((lvl - price) / price * 100, 2), "dist_atr": round((lvl - price) / atr_now, 2) if atr_now else None})
    def pick(side, near_key):
        """The 3 nearest levels plus the 2 strongest of the rest (so a major level is never crowded out by weak nearby ones)."""
        near = sorted(side, key=near_key)
        keep = near[:3] + sorted(near[3:], key=lambda x: -x["score"])[:2]
        return sorted(keep, key=near_key)
    above = pick([x for x in raw if x["price"] > price], lambda x: x["price"])
    below = pick([x for x in raw if x["price"] <= price], lambda x: -x["price"])
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
                tp = [(int(i), round(float(p), 2)) for (i, p) in cand if i >= i0 and abs(p - (p0 + slope * (i - i0))) <= tol]
                touches = len(tp)
                if touches < 3:
                    continue
                score = touches * 2 + (i1 / n) * 3
                if best is None or score > best[0]:
                    best = (score, i0, p0, slope, touches, tp)
        if best:
            _, i0, p0, slope, touches, tp = best
            now = p0 + slope * (n - 1 - i0)
            out.append({"kind": kind, "i0": int(i0), "p0": round(p0, 2), "i1": int(n - 1), "p1": round(now, 2),
                        "touches": int(touches), "touch_idx": [i for i, _ in tp], "touch_px": [p for _, p in tp],
                        "slope_pct_per_bar": round(slope / float(c[-1]) * 100, 3),
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


def analyze_market(df_raw: pd.DataFrame, style: dict, live_frac: float | None = None) -> dict:
    """Everything the engine can say about one timeframe, with exact candle indices and prices.
    live_frac: share of today's session already traded when the last candle is still forming (volume is projected, signals on it are provisional)."""
    df = prepare(df_raw)
    n = len(df)
    price, atr_now = float(df.close.iloc[-1]), float(df.atr.iloc[-1]) if np.isfinite(df.atr.iloc[-1]) else float(df.close.iloc[-1]) * 0.02
    ph, pl = find_pivots(df, int(style["pivot_left"]), int(style["pivot_right"]))
    levels = build_levels(df, ph, pl, atr_now, style)
    zones = detect_zones(df, style)
    lines = detect_trendlines(df, ph, pl, atr_now)
    last = df.iloc[-1]
    vavg_prev = float(df.vavg.iloc[-2]) if n > 1 and np.isfinite(df.vavg.iloc[-2]) else float("nan")      # average that excludes today
    vol_now = float(last.volume) / live_frac if live_frac else float(last.volume)
    vr20 = float(vol_now / vavg_prev) if np.isfinite(vavg_prev) and vavg_prev > 0 else None
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
        "events": detect_events(df, levels, zones, atr_now), "candles": candle_signals(df), "live": bool(live_frac),
    }
    if live_frac:                                    # anything read off today's unfinished candle is provisional
        det["volume"]["projected"] = True
        for x in det["events"] + det["candles"]:
            if x.get("i") == n - 1:
                x["provisional"] = True
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
            cap = float(np.clip(round((np.percentile(th, 80) + np.median(rth)) / 2, 2), 0.8, 3.5))
            if cap < params.get("max_zone_atr", 9):
                params["max_zone_atr"] = cap
            notes.append(f"You rejected thicker zones (median {np.median(rth):.2f} ATR) than you approved ({np.median(th):.2f}) -> max zone thickness tightened to {params['max_zone_atr']} ATR.")
        r_tested = np.mean([1.0 if x.get("tests", 0) > 0 else 0.0 for x in rejected])
        if len(rejected) >= 4 and r_tested >= 0.75 and fresh >= 0.6 and not params.get("fresh_only"):
            params["fresh_only"] = True
            notes.append(f"{r_tested:.0%} of the zones you rejected had already been tested, and most you approved were untested -> showing fresh zones only.")
        rdp = arr("dep_atr", rejected)
        if len(rdp) and len(dp) and np.median(rdp) < np.median(dp) * 0.75:
            lift = float(np.clip(round((np.median(rdp) + np.percentile(dp, 20)) / 2, 2), 1.0, 4.0))
            if lift > params.get("impulse_atr", 0):
                params["impulse_atr"] = lift
            notes.append(f"You rejected zones with weaker moves away (median {np.median(rdp):.1f} ATR vs {np.median(dp):.1f}) -> minimum impulse raised to {params['impulse_atr']} ATR.")
        rq = arr("quality", rejected)
        if len(rq) and len(ql) and np.median(rq) < np.median(ql) - 10:
            notes.append("Rejected zones scored clearly lower than approved ones: the quality score matches your taste.")
    return params, notes


# ── track record: replay every past zone with no hindsight ──
def zone_history(df_raw: pd.DataFrame, style: dict, max_wait: int = 120) -> list:
    """Every zone this style would have drawn in the past, and what happened the first time price came back to it.

    No hindsight: a zone exists only from the candle on which the move away reached the required size (`confirm`), its quality uses
    data up to that candle, and the entry / stop / targets are fixed at that moment.
    Trade model: limit entry at the proximal line on the first return (or the open, if price gaps through it), stop 0.25 ATR beyond the
    distal line, targets at 1R and 2R. If a candle touches both stop and target, the stop is counted (conservative). The entry candle
    itself can only stop the trade out, never hit a target."""
    df = prepare(df_raw)
    n = len(df)
    if n < 60:
        return []
    o, h, l, c, v = (df[k].values for k in ("open", "high", "low", "close", "volume"))
    atr, vavg, sma50, rng = df.atr.values, df.vavg.values, df.sma50.values, df.high.values - df.low.values
    bmax, brng, impa, W = int(style["base_max"]), float(style["base_range_atr"]), float(style["impulse_atr"]), int(style["impulse_window"])
    out, last = [], {"demand": None, "supply": None}
    for k in range(max(30, bmax + 1), n):
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
        base_hi, base_lo = float(h[s:e + 1].max()), float(l[s:e + 1].min())
        confirm, dep = None, 0.0
        for j in range(k, w_end + 1):
            dep = (c[j] - base_hi) / a if up else (base_lo - c[j]) / a
            if dep >= impa:
                confirm = j
                break
        if confirm is None:
            continue
        if up:
            distal, proximal = base_lo, max(float(np.maximum(o[s:e + 1], c[s:e + 1]).max()), base_lo + 0.1 * a)
        else:
            distal, proximal = base_hi, min(float(np.minimum(o[s:e + 1], c[s:e + 1]).min()), base_hi - 0.1 * a)
        thick = abs(proximal - distal)
        if thick > float(style["max_zone_atr"]) * a:
            continue
        zt = "demand" if up else "supply"
        vr = float(v[k:confirm + 1].max() / vavg[e]) if np.isfinite(vavg[e]) and vavg[e] > 0 else None
        pw = slice(max(0, s - 15), s)
        bos = bool(s > 0 and ((up and c[confirm] > h[pw].max()) or ((not up) and c[confirm] < l[pw].min())))
        aligned = bool(s > 0 and np.isfinite(sma50[s - 1]) and ((up and c[s - 1] > sma50[s - 1]) or ((not up) and c[s - 1] < sma50[s - 1])))
        q0 = _zone_quality(dep, impa, m, thick / a, 0, vr, bos, aligned)
        if q0 < style["min_quality"]:
            continue
        top, bottom = max(proximal, distal), min(proximal, distal)
        pv = last[zt]
        if pv and confirm - pv["confirm"] <= 10 and min(pv["top"], top) - max(pv["bottom"], bottom) > 0.5 * min(pv["top"] - pv["bottom"], top - bottom):
            continue                                                    # the same area, formed again a few candles later
        stop = distal - 0.25 * a if up else distal + 0.25 * a
        row = {"type": zt, "quality": q0, "aligned": aligned, "confirm": int(confirm), "formed": str(df.index[e])[:10], "top": round(top, 2),
               "bottom": round(bottom, 2), "proximal": round(float(proximal), 2), "stop": round(float(stop), 2), "touch": None, "touch_date": None,
               "outcome": "WAITING", "hit1": None, "hit2": None, "wait": None}
        last[zt] = row
        touch = None
        for t in range(confirm + 1, min(n, confirm + 1 + max_wait)):
            if (up and l[t] <= proximal) or ((not up) and h[t] >= proximal):
                touch = t
                break
        if touch is None:
            row["outcome"] = "NEVER TESTED" if confirm + max_wait < n - 1 else "WAITING"
            out.append(row)
            continue
        entry = min(proximal, o[touch]) if up else max(proximal, o[touch])
        risk = (entry - stop) if up else (stop - entry)
        row.update(touch=int(touch), touch_date=str(df.index[touch])[:10], wait=int(touch - confirm), entry=round(float(entry), 2))
        if risk <= 0:                                                   # gapped straight through the whole zone
            row.update(outcome="STOP", hit1=False, hit2=False)
            out.append(row)
            continue
        t1, t2 = (entry + risk, entry + 2 * risk) if up else (entry - risk, entry - 2 * risk)
        hit1, outcome = False, "OPEN"
        for t in range(touch, n):
            if (up and l[t] <= stop) or ((not up) and h[t] >= stop):
                outcome = "STOP"
                break
            if t > touch:
                if (up and h[t] >= t1) or ((not up) and l[t] <= t1):
                    hit1 = True
                if (up and h[t] >= t2) or ((not up) and l[t] <= t2):
                    outcome = "2R"
                    break
        row.update(outcome=outcome, hit1=bool(hit1) if (hit1 or outcome == "STOP") else None, hit2=(outcome == "2R") if outcome in ("2R", "STOP") else None)
        out.append(row)
    return out


def track_stats(rows: list) -> dict:
    """n1 / p1: zones resolved at 1R and the share that reached 1R before the stop. n2 / p2: the same for 2R.
    exp2: average result in R when every trade aims for 2R (win +2, loss -1), before costs."""
    r1 = [x["hit1"] for x in rows if x.get("hit1") is not None]
    r2 = [x["hit2"] for x in rows if x.get("hit2") is not None]
    p2 = (sum(r2) / len(r2)) if r2 else None
    return {"formed": len(rows), "tested": sum(1 for x in rows if x.get("touch") is not None), "n1": len(r1),
            "p1": round(sum(r1) / len(r1) * 100, 1) if r1 else None, "n2": len(r2), "p2": round(p2 * 100, 1) if r2 else None,
            "exp2": round(p2 * 2 - (1 - p2), 2) if r2 else None}


def track_record(rows: list, zone: dict | None = None) -> dict:
    """Stats for all zones, by type, by quality band and with / against trend; `like` = zones similar to the plan's zone."""
    band = lambda q: "Q 70+" if q >= 70 else "Q 55-69" if q >= 55 else "Q below 55"
    groups = {"All zones": rows, "Demand": [x for x in rows if x["type"] == "demand"], "Supply": [x for x in rows if x["type"] == "supply"],
              "With trend": [x for x in rows if x["aligned"]], "Against trend": [x for x in rows if not x["aligned"]]}
    for b in ("Q 70+", "Q 55-69", "Q below 55"):
        groups[b] = [x for x in rows if band(x["quality"]) == b]
    out = {"groups": {k: track_stats(v) for k, v in groups.items()}, "like": None, "like_label": None,
           "recent": [x for x in rows if x.get("touch") is not None][-8:][::-1]}
    if zone:
        like = [x for x in rows if x["type"] == zone["type"] and x["aligned"] == bool(zone.get("aligned"))]
        out["like"] = track_stats(like)
        out["like_label"] = f'{zone["type"].title()} zones {"with" if zone.get("aligned") else "against"} the trend'
    return out


def evaluate_call(p: dict, df: pd.DataFrame) -> dict:
    """What happened to a logged plan since the day it was logged (daily candles, stop counted first when a candle touches both)."""
    try:
        long = p["direction"] == "LONG"
        ref, stop = float(p["entry_ref"]), float(p["stop"])
        t1 = float(p["targets"][0]["price"]) if p.get("targets") else None
        risk = abs(ref - stop)
        after = df[df.index > pd.Timestamp(p["asof"])]
        base = {"status": "WAITING", "r": None, "days": len(after), "entry": None, "last": float(df.close.iloc[-1]) if len(df) else None}
        if risk <= 0 or t1 is None:
            return dict(base, status="INVALID")
        in_zone = float(p["entry_low"]) <= float(p.get("close_at_log", ref)) <= float(p["entry_high"])
        entry, start = (ref, 0) if in_zone else (None, None)
        o, h, l, c = (after[k].values for k in ("open", "high", "low", "close"))
        if entry is None:
            for t in range(len(after)):
                if (long and l[t] <= ref) or ((not long) and h[t] >= ref):
                    entry, start = (min(ref, o[t]) if long else max(ref, o[t])), t
                    break
                if (long and h[t] >= t1) or ((not long) and l[t] <= t1):
                    return dict(base, status="MISSED (ran to target without entry)")
        if entry is None:
            return base
        risk = abs(entry - stop) or risk
        for t in range(start, len(after)):
            if (long and l[t] <= stop) or ((not long) and h[t] >= stop):
                return dict(base, status="STOPPED", r=-1.0, entry=round(float(entry), 2), exit_date=str(after.index[t])[:10])
            if (t > start or in_zone) and ((long and h[t] >= t1) or ((not long) and l[t] <= t1)):
                return dict(base, status="TARGET 1 HIT", r=round(abs(t1 - entry) / risk, 2), entry=round(float(entry), 2), exit_date=str(after.index[t])[:10])
        now = float(c[-1]) if len(after) else float(p.get("close_at_log", ref))
        return dict(base, status="OPEN", r=round(((now - entry) if long else (entry - now)) / risk, 2), entry=round(float(entry), 2))
    except Exception as exc:
        return {"status": f"ERROR ({type(exc).__name__})", "r": None, "days": 0, "entry": None, "last": None}


def market_context(stock_df: pd.DataFrame, nifty_df: pd.DataFrame | None) -> dict | None:
    """NIFTY 50 trend and the stock's 3-month return against it (context only)."""
    if nifty_df is None or len(nifty_df) < 80:
        return None
    nf = prepare(nifty_df)
    ph, pl = find_pivots(nf, 3, 3)
    roc = lambda d: (float(d.close.iloc[-1]) / float(d.close.iloc[-64]) - 1) * 100 if len(d) > 64 else None
    sr, nr = roc(stock_df), roc(nf)
    s50 = nf.sma50.iloc[-1]
    return {"nifty_structure": market_structure(ph, pl), "nifty_close": round(float(nf.close.iloc[-1]), 2),
            "nifty_vs_sma50": None if not np.isfinite(s50) else ("ABOVE" if nf.close.iloc[-1] > s50 else "BELOW"),
            "stock_3m_pct": _f(sr), "nifty_3m_pct": _f(nr), "rs_3m_pp": _f(sr - nr) if sr is not None and nr is not None else None}


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
    plan = {"direction": "LONG" if long else "SHORT", "ref_zone": z["id"], "entry_low": z["bottom"],
            "entry_high": z["top"], "entry_ref": round(ref, 2), "stop": stop,
            "stop_reason": f"Beyond {z['id']} distal line ({z['distal']:.2f}) {'minus' if long else 'plus'} 0.25 ATR",
            "targets": targets[:3], "counter_trend": bool((long and trend_dn) or ((not long) and trend_up)), "source": "engine"}
    plan.update(plan_room(det, plan))
    return plan


def plan_room(det: dict, plan: dict) -> dict:
    """Distance from the entry to the FIRST thing in the way (any level, or the near edge of an opposing zone), in R.
    Less than 1R of room means the trade risks more than it can reasonably make before meeting resistance / support."""
    long, ref = plan["direction"] == "LONG", plan["entry_ref"]
    risk = abs(ref - plan["stop"])
    if risk <= 0:
        return {"room_r": None, "obstacle": None, "blocked": False}
    obs = []
    for lv in det["levels"]:
        if (long and lv["price"] > ref) or ((not long) and lv["price"] < ref):
            obs.append((abs(lv["price"] - ref), lv["id"], lv["price"]))
    for oz in det["zones"]:
        if oz["id"] == plan.get("ref_zone"):
            continue
        if long and oz["type"] == "supply" and oz["bottom"] > ref:
            obs.append((oz["bottom"] - ref, oz["id"], oz["bottom"]))
        if (not long) and oz["type"] == "demand" and oz["top"] < ref:
            obs.append((ref - oz["top"], oz["id"], oz["top"]))
    if not obs:
        return {"room_r": None, "obstacle": None, "blocked": False}
    d, oid, px = min(obs)
    # a level between today's price and the entry is support / resistance today, but price has to break it to reach the entry,
    # after which it stands in the way of the trade (role reversal)
    flips = bool((long and px < det["close"]) or ((not long) and px > det["close"]))
    return {"room_r": round(d / risk, 2), "obstacle": oid, "obstacle_price": round(px, 2), "blocked": bool(d / risk < 1.0), "obstacle_flips": flips}


def targets_for(det: dict, ref: float, risk: float, long: bool) -> list:
    """Targets at detected levels / opposing zones at least 1R away, padded with 2R / 3R projections when fewer than two exist."""
    cands = []
    for lv in det["levels"]:
        if (long and lv["price"] > ref + risk) or ((not long) and lv["price"] < ref - risk):
            cands.append((lv["price"], lv["id"], "resistance" if long else "support"))
    for oz in det["zones"]:
        if long and oz["type"] == "supply" and oz["bottom"] > ref + risk:
            cands.append((oz["bottom"], oz["id"], "supply zone"))
        if (not long) and oz["type"] == "demand" and oz["top"] < ref - risk:
            cands.append((oz["top"], oz["id"], "demand zone"))
    cands.sort(key=lambda x: x[0] if long else -x[0])
    out = [{"price": round(p, 2), "ref": r, "reason": f"Next {k}", "rr": round(abs(p - ref) / risk, 2)} for p, r, k in cands[:3]]
    for mult in (2, 3):
        if len(out) < 2:
            p = ref + mult * risk if long else ref - mult * risk
            out.append({"price": round(p, 2), "ref": f"{mult}R", "reason": f"{mult}R projection (no level found)", "rr": float(mult)})
    return sorted(out[:3], key=lambda t: t["rr"])


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
    sig = any(s["bias"] == want and s["i"] >= det["n"] - 2 and not s.get("provisional") for s in det["candles"])
    confirm = 100 if (sig and vol) else 65 if sig else 40 if vol else 20
    score = 0.40 * z["quality"] + 0.20 * proximity + 0.15 * trend + 0.10 * htf + 0.15 * confirm
    if plan.get("blocked"):
        score -= 15                                  # less than 1R of room before the first obstacle
    return int(round(max(0, score)))


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
        ob = str(plan.get("obstacle") or "")                             # always draw what stands in the way of the trade
        (zid if ob.startswith(("DZ", "SZ")) else lid).add(ob)
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
            "lines": [x for x in det["trendlines"] if x["id"] in tid], "markers": markers, "candles": det["candles"], "plan": plan, "live": bool(det.get("live"))}


def _rgba(hex_color: str, a: float) -> str:
    c = hex_color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{a})"


def _date_ticks(index, start, n, count=8):
    pos = list(range(start, n, max(1, (n - start) // count)))
    return pos, [str(index[p])[5:10] if (n - start) < 70 else str(index[p])[2:10] for p in pos]


def fig_plotly(df: pd.DataFrame, spec: dict, window: int = 120, show: dict | None = None, height: int = 780):
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
            fig.add_shape(type="line", x0=x0, x1=xr, y0=z["distal"], y1=z["distal"], line=dict(color=_rgba(col, .9), width=1, dash="dash"), row=1, col=1)
            fig.add_annotation(x=x0, y=z["top"] if z["type"] == "supply" else z["bottom"], xanchor="left", yanchor="bottom" if z["type"] == "supply" else "top",
                               text=f"<b>{z['id']}</b> {z['pattern']} · Q{z['quality']} · {z['status']} · {z['bottom']:,.2f}–{z['top']:,.2f}", showarrow=False,
                               font=dict(size=10, color=col), row=1, col=1)
            if z["top"] >= ys[0] * .8 and z["bottom"] <= ys[1] * 1.2:
                ys += [z["top"], z["bottom"]]
    if show["levels"]:
        for lv in spec["levels"]:
            col = GREEN if lv["kind"] == "support" else RED
            fig.add_shape(type="line", x0=max(start, lv.get("first", start)), x1=xr, y0=lv["price"], y1=lv["price"], line=dict(color=_rgba(col, .75), width=1, dash="dot"), row=1, col=1)
            tx = [(i, p) for i, p in zip(lv.get("touch_idx", []), lv.get("touch_px", [])) if i >= start]
            if tx:                                                       # the swing points that make this level
                fig.add_trace(go.Scatter(x=[i for i, _ in tx], y=[p for _, p in tx], mode="markers", showlegend=False, hovertext=[f"{lv['id']} touch · {p:,.2f}" for _, p in tx],
                                         hoverinfo="text", marker=dict(symbol="circle-open", size=9, color=col, line=dict(width=1.5, color=col))), row=1, col=1)
            fig.add_annotation(x=xr, y=lv["price"], xanchor="right", yanchor="bottom", showarrow=False, font=dict(size=9, color=col),
                               text=f"{lv['id']} {lv['price']:,.2f} ({lv['touches']}x)", row=1, col=1)
            ys.append(lv["price"])
    if show["lines"]:
        for t in spec["lines"]:
            col = GREEN if t["kind"] == "support" else RED
            fig.add_trace(go.Scatter(x=[t["i0"], n - 1 + ext // 2], y=[t["p0"], t["p0"] + (t["p1"] - t["p0"]) / max(1, t["i1"] - t["i0"]) * (n - 1 + ext // 2 - t["i0"])],
                                     mode="lines", name=f"{t['id']} trendline", line=dict(color=col, width=1.6, dash="dash")), row=1, col=1)
            tx = [(i, p) for i, p in zip(t.get("touch_idx", []), t.get("touch_px", [])) if i >= start]
            if tx:
                fig.add_trace(go.Scatter(x=[i for i, _ in tx], y=[p for _, p in tx], mode="markers", showlegend=False, hovertext=[f"{t['id']} touch · {p:,.2f}" for _, p in tx],
                                         hoverinfo="text", marker=dict(symbol="diamond-open", size=9, color=col, line=dict(width=1.5, color=col))), row=1, col=1)
    p = spec.get("plan")
    if show["plan"] and p:
        ref, stop = p["entry_ref"], p["stop"]
        x0, x1 = n - 1, n - 1 + int(ext * .8)
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=min(ref, stop), y1=max(ref, stop), fillcolor=_rgba(RED, .10), line=dict(width=0), row=1, col=1)
        t1 = p["targets"][0]["price"] if p["targets"] else ref
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=min(ref, t1), y1=max(ref, t1), fillcolor=_rgba(GREEN, .10), line=dict(width=0), row=1, col=1)
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=p["entry_low"], y1=p["entry_high"], fillcolor=_rgba(CYAN, .18), line=dict(color=CYAN, width=1), row=1, col=1)
        for price, label, col in [(stop, "STOP", RED)] + [(t["price"], f"T{i} · {t.get('rr', 0):g}R", GREEN) for i, t in enumerate(p["targets"], 1)]:
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
    fig.update_layout(height=height, margin=dict(l=6, r=6, t=30, b=6), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Inter, sans-serif", size=11, color=T2), hovermode="x",
                      legend=dict(orientation="h", y=1.04, x=0, font=dict(size=10)),
                      hoverlabel=dict(bgcolor="#161618", bordercolor="#2E2E33", font=dict(family="Inter, sans-serif", size=12, color=IVORY)))
    fig.update_xaxes(showgrid=False, range=[start - 1, xr], rangeslider_visible=False, linecolor=LINE)
    fig.update_xaxes(tickvals=pos, ticktext=labels, row=3, col=1)
    fig.update_yaxes(gridcolor="#1A1A1D", zeroline=False)
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
        ax.hlines(z["distal"], x0, m + ext, colors=col, linestyles="--", linewidth=1.1, zorder=2)             # distal line = where the zone fails
        ax.annotate(f"{z['id']} {z['pattern']} Q{z['quality']} {z['status']}  {z['bottom']:,.2f}-{z['top']:,.2f}", (x0 + .3, z["top"] if z["type"] == "supply" else z["bottom"]),
                    xytext=(0, 3 if z["type"] == "supply" else -3), textcoords="offset points", color=col, fontsize=8, fontfamily="sans-serif",
                    va="bottom" if z["type"] == "supply" else "top", zorder=6)
        ys += [z["top"], z["bottom"]]
    for lv in spec["levels"]:
        col = GREEN if lv["kind"] == "support" else RED
        ax.hlines(lv["price"], max(0, lv.get("first", start) - start), m + ext, colors=col, linestyles=":", linewidth=1, alpha=.8, zorder=1)
        tx = [(i - start, p_) for i, p_ in zip(lv.get("touch_idx", []), lv.get("touch_px", [])) if i >= start]
        if tx:                                                           # the swing points that make this level
            ax.scatter([a_ for a_, _ in tx], [b_ for _, b_ in tx], s=46, facecolors="none", edgecolors=col, linewidths=1.2, zorder=6)
        ax.annotate(f"{lv['id']} {lv['price']:,.2f} ({lv['touches']}x)", (max(0, lv.get("first", start) - start) + .3, lv["price"]), xytext=(0, 2), textcoords="offset points",
                    color=col, fontsize=8, ha="left", va="bottom", fontfamily="sans-serif", zorder=6)
        ys.append(lv["price"])
    for t in spec["lines"]:
        col = GREEN if t["kind"] == "support" else RED
        slope = (t["p1"] - t["p0"]) / max(1, t["i1"] - t["i0"])
        xe = n - 1 + ext // 2
        ax.plot([t["i0"] - start, xe - start], [t["p0"], t["p0"] + slope * (xe - t["i0"])], color=col, linestyle="--", linewidth=1.4, zorder=4)
        tx = [(i - start, p_) for i, p_ in zip(t.get("touch_idx", []), t.get("touch_px", [])) if i >= start]
        if tx:
            ax.scatter([a_ for a_, _ in tx], [b_ for _, b_ in tx], s=40, marker="D", facecolors="none", edgecolors=col, linewidths=1.2, zorder=6)
    p = spec.get("plan")
    if p:
        x0, x1 = m - 1, m - 1 + int(ext * .85)
        ref, stop = p["entry_ref"], p["stop"]
        t1 = p["targets"][0]["price"] if p["targets"] else ref
        ax.add_patch(Rectangle((x0, min(ref, stop)), x1 - x0, abs(ref - stop), facecolor=RED, alpha=.12, linewidth=0, zorder=1))
        ax.add_patch(Rectangle((x0, min(ref, t1)), x1 - x0, abs(t1 - ref), facecolor=GREEN, alpha=.12, linewidth=0, zorder=1))
        ax.add_patch(Rectangle((x0, p["entry_low"]), x1 - x0, p["entry_high"] - p["entry_low"], facecolor=CYAN, alpha=.22, edgecolor=CYAN, linewidth=1, zorder=2))
        tags = [((p["entry_low"] + p["entry_high"]) / 2, f"ENTRY {p['entry_low']:,.2f}-{p['entry_high']:,.2f}", CYAN)]
        for price, label, col in [(stop, "STOP", RED)] + [(t["price"], f"T{i} {t.get('rr', 0):g}R", GREEN) for i, t in enumerate(p["targets"], 1)]:
            ax.hlines(price, x0, x1, colors=col, linewidth=1.4, zorder=3)
            tags.append((price, f"{label} {price:,.2f}", col))
            ys.append(price)
        gap, prev_y = (max(ys) - min(ys)) * 0.028, None
        for y_, text_, col in sorted(tags):                              # keep the price tags from printing over each other
            ty = y_ if prev_y is None else max(y_, prev_y + gap)
            ax.text(x1 + .3, ty, text_, color=col, fontsize=8, fontfamily="sans-serif", va="center", zorder=7)
            prev_y = ty
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
    fig.text(0.012, 0.965, title, color=IVORY, fontsize=12, fontweight="bold", fontfamily="sans-serif")
    fig.text(0.988, 0.965, "Arka AI · research tool, not advice · prices as traded (not dividend-adjusted)", color=T3, fontsize=8, ha="right", fontfamily="sans-serif")
    if spec.get("live"):
        ax.text(m - 1, float(w.high.iloc[-1]) + (max(ys) - min(ys)) * .02, "LIVE", color=AMBER, fontsize=7, ha="center", fontfamily="sans-serif", fontweight="bold", zorder=8)
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
 "plan": { "direction": "LONG | SHORT | NONE", "ref_zone": "the ID of ONE detected zone (demand for LONG, supply for SHORT)", "stop": 0, "stop_reason": "...",
           "targets": [ {"ref": "ID of a detected level or opposing zone, e.g. R1 or SZ1", "reason": "..."} ], "invalidation": "...", "confirmation_needed": ["..."] },
 "supports": ["things that agree with MY playbook"],
 "contradicts": ["things that disagree with MY playbook"],
 "watch_next": ["what to watch before acting"],
 "scenarios": { "bull": {"trigger": "...", "path": "...", "level": 0}, "bear": {"trigger": "...", "path": "...", "level": 0},
                "neutral": {"trigger": "...", "path": "...", "level": 0} },
 "context": ["optional observations NOT used for the verdict"],
 "draw": { "zones": ["DZ1"], "levels": ["R1"], "trendlines": ["TL1"] },
 "narrative": "a clear 150-250 word analysis in plain language"
}
The entry zone is drawn by Python from ref_zone (its exact top and bottom) and each target from its ref ID, so give IDs, not your own prices.
Python recomputes match_pct, score and grade from your rule statuses, so spend your effort on getting each status and its evidence right."""


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


def feedback_reasons(items: list, cap: int = 14) -> list:
    """The trader's own words about why a zone is / is not theirs (most recent first)."""
    out = []
    for it in items:
        for lab in it["payload"].get("labels", []):
            why = str(lab.get("reason") or "").strip()
            if why:
                f = lab.get("features", {})
                out.append({"verdict": "MINE" if lab.get("approved") else "NOT MINE", "why": why[:200], "zone": f.get("type"), "pattern": f.get("pattern"),
                            "thickness_atr": f.get("thickness_atr"), "tests": f.get("tests"), "quality": f.get("quality")})
    return out[:cap]


def build_prompt(ticker, det, det_w, style, setups, lessons, feedback, note, df, extra: dict | None = None) -> str:
    extra = extra or {}
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
        "== PLAYBOOK: THE TRADER'S OWN REASONS FOR APPROVING / REJECTING ZONES ==\n" + (json.dumps(feedback_reasons(feedback), ensure_ascii=False) if feedback_reasons(feedback) else "NONE GIVEN"),
        "== ENGINE STYLE PARAMETERS (how detections below were produced) ==\n" + json.dumps(style),
        "== MARKET FACTS (daily, computed by Python) ==\n" + json.dumps(market, ensure_ascii=False, default=str),
        "== DETECTIONS (daily). Choose ONLY from these IDs ==\n" + json.dumps(detections, ensure_ascii=False, default=str),
        "== WEEKLY CONTEXT ==\n" + (json.dumps(weekly, ensure_ascii=False, default=str) if weekly else "UNAVAILABLE"),
        "== DAILY OHLC (last 70 candles; i is the candle index used by detections) ==\n" + ohlc,
        "== CONTEXT (not part of the playbook; use only under \"context\" unless a playbook rule asks for it) ==\n" + json.dumps({
            "last_candle_unfinished": bool(det.get("live")), "market": extra.get("market"), "next_results_date": extra.get("results"),
            "engine_plan": {k: (extra.get("engine_plan") or {}).get(k) for k in ("direction", "ref_zone", "entry_low", "entry_high", "stop", "room_r", "obstacle", "blocked")} if extra.get("engine_plan") else None,
            "zone_track_record_this_stock": extra.get("track")}, ensure_ascii=False, default=str),
        "== TRADER NOTE ==\n" + (note or "None"),
        ("TASK: Judge this stock ONLY against the trader's playbook. 1) Check every rule of every active setup against the detections and facts. "
         "2) Say which detected zones are the trader's kind of zone (use the lessons and the approved / rejected summary) and what role each plays. "
         "3) Build a trade plan using ONLY detected zones and levels: name the zone to trade from (ref_zone), a stop beyond its distal line, and targets as the IDs of the next opposing zone or level; "
         "use direction NONE if nothing qualifies. 4) List what supports and what contradicts the playbook, and what to watch next. "
         "5) Give bull / bear / neutral scenarios with exact trigger prices. 6) Choose which detection IDs to draw. The attached image shows the detections. "
         "If no setup is taught, judge against the lessons and approved zones and say so in the headline."),
        ANALYSIS_SCHEMA,
    ]
    return "\n\n".join(parts)


def _clean_list(x, cap=6):
    return [str(i)[:400] for i in (x if isinstance(x, list) else [])[:cap] if str(i).strip()]


def playbook_rules(item: dict) -> list:
    """The rules of one saved setup that the scorecard must cover: conditions first, then confirmations. Ids are made unique."""
    p, out, seen = item.get("payload") or {}, [], set()
    for group, key, prefix in (("rule", "conditions", "R"), ("confirmation", "confirmation", "C")):
        for k, c in enumerate(p.get(key) or [], 1):
            if not isinstance(c, dict):
                c = {"name": str(c)}
            rid = str(c.get("id") or f"{prefix}{k:02d}").strip() or f"{prefix}{k:02d}"
            while rid.upper() in seen:
                rid += "'"
            seen.add(rid.upper())
            out.append({"id": rid, "name": str(c.get("name") or c.get("condition") or c.get("description") or rid)[:140],
                        "required": bool(c.get("required", True)), "group": group})
    return out


def _check(c: dict, ids: set) -> dict:
    st_ = c.get("status") if c.get("status") in ("PRESENT", "NOT_PRESENT", "UNCLEAR") else "UNCLEAR"
    try:
        conf = int(max(0, min(100, float(c.get("confidence", 0)))))
    except Exception:
        conf = 0
    return {"status": st_, "confidence": conf, "reason": str(c.get("reason", ""))[:500],
            "refs": [r for r in c.get("refs", []) if r in ids] if isinstance(c.get("refs"), list) else []}


def _snap_price(det: dict, price: float, tol: float):
    """Nearest detected price (level, or zone edge) within tol -> (price, id) or None."""
    best = None
    for lv in det["levels"]:
        best = min(best or (9e18, 0, ""), (abs(lv["price"] - price), lv["price"], lv["id"]))
    for z in det["zones"]:
        for edge in (z["top"], z["bottom"]):
            best = min(best or (9e18, 0, ""), (abs(edge - price), edge, z["id"]))
    return (best[1], best[2]) if best and best[0] <= tol else None


def validate_ai(ai: dict, det: dict, price: float, setups: list | None = None):
    """Make the model's reply safe and exact.

    * Scorecard: one line per rule of every active setup, in playbook order, matched by rule id. Rules the model skipped become UNCLEAR;
      rules it invented are dropped. Match %, score, grade and verdict are computed here from the statuses.
    * Plan: the entry zone is the exact top / bottom of a detected zone of the right type, the stop must sit beyond its distal line, and every
      target is the exact price of a detected level or opposing zone. Anything that cannot be mapped is dropped.
    Every change is listed in out["corrections"] so the trader can see what was fixed."""
    zid, lid, tid = {z["id"] for z in det["zones"]}, {x["id"] for x in det["levels"]}, {x["id"] for x in det["trendlines"]}
    ids, fix, out, atr = zid | lid | tid, [], {}, det["atr"]
    ai = ai if isinstance(ai, dict) else {}
    ai_verdict = ai.get("verdict") if ai.get("verdict") in ("MATCH", "PARTIAL", "NO_MATCH", "AVOID") else None
    out["bias"] = ai.get("bias") if ai.get("bias") in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL"
    out["setup_state"] = str(ai.get("setup_state", "WATCHING"))[:30]
    out["headline"] = str(ai.get("headline", ""))[:400]

    # ── scorecard ──
    ai_setups = [x for x in (ai.get("setups") if isinstance(ai.get("setups"), list) else []) if isinstance(x, dict)]
    result = []
    if setups:
        used = set()

        def find(item, pos):
            for k, a in enumerate(ai_setups):
                if k not in used and str(a.get("setup_id", "")) == str(item["item_id"]):
                    return k
            for k, a in enumerate(ai_setups):
                if k not in used and str(a.get("name", "")).strip().lower() == str(item["name"]).strip().lower():
                    return k
            return pos if (len(ai_setups) == len(setups) and pos not in used) else None
        for pos, item in enumerate(setups):
            k = find(item, pos)
            a = ai_setups[k] if k is not None else {}
            if k is not None:
                used.add(k)
            else:
                fix.append(f'The AI did not evaluate the setup "{item["name"]}"; its rules are shown as UNCLEAR.')
            raw = [c for c in (a.get("checks") if isinstance(a.get("checks"), list) else []) if isinstance(c, dict)]
            by_id = {str(c.get("rule_id", "")).strip().upper(): c for c in raw}
            by_name = {str(c.get("rule_name", "")).strip().lower(): c for c in raw}
            rules, checks, skipped = playbook_rules(item), [], 0
            for r in rules:
                c = by_id.pop(r["id"].upper(), None) or by_name.get(r["name"].strip().lower())
                if c is None:
                    skipped += 1
                    c = {"status": "UNCLEAR", "confidence": 0, "reason": "The AI did not evaluate this rule."}
                checks.append({"rule_id": r["id"], "rule_name": r["name"], "required": r["required"], "group": r["group"], **_check(c, ids)})
            if not rules:                                                # legacy setup without a rule list: keep what the model returned
                checks = [{"rule_id": str(c.get("rule_id", ""))[:12], "rule_name": str(c.get("rule_name", ""))[:140], "required": True, "group": "rule", **_check(c, ids)} for c in raw]
            else:
                extra_n = len([c for c in raw if str(c.get("rule_id", "")).strip().upper() in by_id])
                if skipped:
                    fix.append(f'{item["name"]}: {skipped} rule(s) were not evaluated by the AI and are shown as UNCLEAR.')
                if extra_n:
                    fix.append(f'{item["name"]}: {extra_n} check(s) that are not rules of this setup were removed.')
            req = [c for c in checks if c["required"]] or checks
            present = sum(1 for c in req if c["status"] == "PRESENT")
            result.append({"setup_id": str(item["item_id"]), "name": str(item["name"])[:120], "match_pct": int(round(100 * present / len(req))) if req else 0,
                           "present": present, "required": len(req), "unclear": sum(1 for c in req if c["status"] == "UNCLEAR"),
                           "failed": sum(1 for c in req if c["status"] == "NOT_PRESENT"), "state": str(a.get("state", ""))[:30], "checks": checks})
    out["setups"] = result
    if result:
        best = max(result, key=lambda x: x["match_pct"])
        out["score"] = best["match_pct"]
        derived = "MATCH" if best["required"] and best["present"] == best["required"] else "PARTIAL" if best["match_pct"] >= 50 else "NO_MATCH"
        out["verdict"] = "AVOID" if ai_verdict == "AVOID" else derived
        if ai_verdict and ai_verdict not in ("AVOID", derived):
            fix.append(f'Verdict set to {derived.replace("_", " ")} from the rule statuses ({best["present"]} of {best["required"]} required rules present); the AI had said {ai_verdict.replace("_", " ")}.')
    else:
        out["score"] = None                                              # no setup taught: the engine score is used
        out["verdict"] = ai_verdict or "PARTIAL"
    out["grade"] = grade_for(out["score"]) if out["score"] is not None else None

    out["my_zones"] = [{"id": z.get("id"), "role": str(z.get("role", "CONTEXT"))[:20], "matches_my_style": bool(z.get("matches_my_style")), "comment": str(z.get("comment", ""))[:300]}
                       for z in ai.get("my_zones", []) if isinstance(z, dict) and z.get("id") in zid | lid] if isinstance(ai.get("my_zones"), list) else []
    for k in ("supports", "contradicts", "watch_next", "context"):
        out[k] = _clean_list(ai.get(k))
    sc = ai.get("scenarios") if isinstance(ai.get("scenarios"), dict) else {}
    out["scenarios"] = {}
    for k in ("bull", "bear", "neutral"):
        one = sc.get(k) if isinstance(sc.get(k), dict) else {}
        lvl, ref = one.get("level"), None
        try:
            lvl = float(lvl)
            if not (0.4 * price < lvl < 2.5 * price):
                lvl = None
            else:
                snap = _snap_price(det, lvl, 0.35 * atr)
                if snap:
                    lvl, ref = snap
        except (TypeError, ValueError):
            lvl = None
        out["scenarios"][k] = {"trigger": one.get("trigger"), "path": one.get("path"), "level": lvl, "ref": ref}
    dr = ai.get("draw") if isinstance(ai.get("draw"), dict) else {}
    lst = lambda v: v if isinstance(v, list) else []
    out["draw"] = {"zones": [i for i in lst(dr.get("zones")) if i in zid], "levels": [i for i in lst(dr.get("levels")) if i in lid],
                   "trendlines": [i for i in lst(dr.get("trendlines")) if i in tid]}
    out["narrative"] = str(ai.get("narrative", ""))[:3000]

    # ── plan ──
    p, plan = ai.get("plan") if isinstance(ai.get("plan"), dict) else {}, None
    direction = p.get("direction")
    out["no_trade"] = direction == "NONE"
    if direction in ("LONG", "SHORT"):
        long = direction == "LONG"
        want = "demand" if long else "supply"
        z = next((x for x in det["zones"] if x["id"] == p.get("ref_zone")), None)
        if z is None:                                                    # no usable id: accept only if the AI's own prices sit on a detected zone
            try:
                mid = (float(p["entry_low"]) + float(p["entry_high"])) / 2
                near = [x for x in det["zones"] if x["type"] == want and x["bottom"] - 0.25 * atr <= mid <= x["top"] + 0.25 * atr]
                z = min(near, key=lambda x: abs((x["top"] + x["bottom"]) / 2 - mid)) if near else None
                if z:
                    fix.append(f'The plan did not name a zone; it was matched to {z["id"]} from its prices.')
            except Exception:
                z = None
        if z is not None and z["type"] != want:
            fix.append(f'The AI planned a {direction} from {z["id"]}, which is a {z["type"]} zone. That plan was rejected.')
            z = None
        if z is None:
            fix.append("The AI's plan did not map to a detected zone, so it is not drawn. The engine plan is shown instead when one exists.")
        else:
            lo, hi, distal = z["bottom"], z["top"], z["distal"]
            ref = min(max(price, lo), hi) if z["in_zone"] else (hi if long else lo)
            default_stop = round(distal - 0.25 * atr, 2) if long else round(distal + 0.25 * atr, 2)
            stop, reason = default_stop, f"Beyond {z['id']} distal line ({distal:.2f}) {'minus' if long else 'plus'} 0.25 ATR"
            try:
                s_ai = float(p.get("stop"))
                beyond = (s_ai < distal) if long else (s_ai > distal)
                if beyond and abs(s_ai - distal) <= 1.5 * atr:
                    stop, reason = round(s_ai, 2), str(p.get("stop_reason") or reason)[:300]
                elif abs(s_ai - default_stop) > 0.02 * atr:
                    fix.append(f'Stop {s_ai:,.2f} was not a sensible distance beyond {z["id"]}\'s distal line ({distal:,.2f}); set to {default_stop:,.2f}.')
            except (TypeError, ValueError):
                pass
            risk = abs(ref - stop)
            targets, seen, dropped = [], set(), 0
            for t in (p.get("targets") if isinstance(p.get("targets"), list) else [])[:4]:
                if not isinstance(t, dict):
                    continue
                rid, px = str(t.get("ref", "")).strip(), None
                lv = next((x for x in det["levels"] if x["id"] == rid), None)
                oz = next((x for x in det["zones"] if x["id"] == rid and x["id"] != z["id"]), None)
                if lv:
                    px = lv["price"]
                elif oz:
                    px = oz["bottom"] if long else oz["top"]
                else:
                    try:
                        snap = _snap_price(det, float(t.get("price")), 0.35 * atr)
                        if snap:
                            px, rid = snap
                    except (TypeError, ValueError):
                        pass
                if px is None or rid in seen or not ((long and px > ref) or ((not long) and px < ref)):
                    dropped += 1
                    continue
                seen.add(rid)
                targets.append({"price": round(px, 2), "ref": rid, "reason": str(t.get("reason", ""))[:200], "rr": round(abs(px - ref) / risk, 2) if risk else 0.0})
            if dropped:
                fix.append(f"{dropped} target(s) did not map to a detected level or zone on the right side of the entry and were removed.")
            if not targets and risk > 0:
                targets = targets_for(det, ref, risk, long)
                fix.append("No usable target came from the AI; targets were taken from the next detected levels / zones.")
            if risk > 0 and targets:
                st_txt = det["structure"]
                plan = {"direction": direction, "ref_zone": z["id"], "entry_low": lo, "entry_high": hi, "entry_ref": round(ref, 2), "stop": stop,
                        "stop_reason": reason, "targets": sorted(targets, key=lambda t: t["rr"])[:3], "invalidation": str(p.get("invalidation", ""))[:400],
                        "confirmation_needed": _clean_list(p.get("confirmation_needed"), 5),
                        "counter_trend": bool((long and st_txt.startswith("DOWNTREND")) or ((not long) and st_txt.startswith("UPTREND"))), "source": "ai"}
                plan.update(plan_room(det, plan))
    out["plan"] = plan
    out["corrections"] = fix
    return out


def verdict_text(report: dict):
    ai = report.get("ai")
    if ai:
        v = ai["verdict"]
        return {"MATCH": ("Matches your setup", GREEN), "PARTIAL": ("Partial match", AMBER), "NO_MATCH": ("Not your setup", T3), "AVOID": ("Avoid", RED)}[v]
    s = report["score"]
    return ("Strong zone setup", GREEN) if s >= 72 else ("Developing", AMBER) if s >= 50 else ("No clear setup", T3)


def run_analysis(ticker: str, note: str = "", use_ai: bool = True, window: int = 120, progress=None) -> dict:
    """progress(label) is called at each step (drives the live progress panel)."""
    t0 = time.time()
    say = progress or (lambda _m: None)
    t = ticker.upper().strip()
    # context that does not depend on the stock's own data loads in parallel
    f_nifty = _in_thread(fetch_ohlc, "NIFTY50", "1d", "2y") if not is_index(t) else None
    f_res = _in_thread(next_results_date, t)
    say(f"Loading {t} prices")
    daily = fetch_ohlc(t, "1d", "2y")
    weekly = weekly_from_daily(daily)
    say("Detecting zones, levels and trendlines")
    style = get_style()
    det = analyze_market(daily, style, live_fraction(daily.index[-1]))
    det_w = None
    if weekly is not None and len(weekly) >= 60:
        det_w = analyze_market(weekly, dict(style, pivot_left=2, pivot_right=2, lookback=140, base_max=min(3, int(style["base_max"]))))
    add_htf_confluence(det, det_w)
    eplan = deterministic_plan(det)
    escore = engine_score(det, eplan)
    try:
        hist_rows = zone_history(daily, style)
    except Exception:
        hist_rows = []
    market = None
    if f_nifty is not None:
        nf = _result(f_nifty, 12)
        try:
            market = market_context(daily, nf) if nf is not None else None
        except Exception:
            market = None
    results = _result(f_res, 4)                                         # never wait long for the calendar
    setups = active_items("setup")
    report = {"ticker": t, "asof": det["asof"], "det": det, "det_w": det_w, "style": style, "engine_plan": eplan, "engine_score": escore,
              "ai": None, "ai_error": "", "plan": eplan, "score": escore, "note": note, "window": window, "market": market, "results": results,
              "zone_rows": hist_rows, "n_setups": len(setups), "n_lessons": len(active_items("lesson")), "n_labelled": len(active_items("feedback")),
              "ran_at": datetime.now(IST).strftime("%d %b %Y, %H:%M"), "df": daily, "model": ""}
    if use_ai and get_gemini() is not None:
        try:
            say("Judging against your playbook (Arka AI)")
            preview = render_png(daily, build_spec(det, None, eplan), window=110, title=f"{t} · detections (preview for AI)", dpi=72)
            zref = next((z for z in det["zones"] if eplan and z["id"] == eplan["ref_zone"]), None)
            tr = track_record(hist_rows, zref)
            extra = {"market": market, "results": results, "engine_plan": eplan,
                     "track": {"all": tr["groups"]["All zones"], "similar_to_plan_zone": tr["like"], "note": "walk-forward replay on this stock; small samples are weak evidence"}}
            prompt = build_prompt(t, det, det_w, style, setups, active_items("lesson"), active_items("feedback"), note, daily, extra)
            t_ai = time.time()
            ai = validate_ai(call_json(prompt, images=[preview]), det, det["close"], setups)
            report["ai_secs"] = round(time.time() - t_ai, 1)
            report["ai"] = ai
            report["model"] = ai_model_name()
            report["score"] = ai["score"] if ai["score"] is not None else escore
            ai["grade"] = ai["grade"] or grade_for(report["score"])
            report["plan"] = None if ai["no_trade"] else (ai["plan"] or eplan)
        except Exception as exc:
            report["ai_error"] = str(exc)
    zref = next((z for z in det["zones"] if report["plan"] and z["id"] == report["plan"].get("ref_zone")), None)
    report["track"] = track_record(hist_rows, zref)
    report["spec"] = build_spec(det, (report["ai"] or {}).get("draw"), report["plan"])
    report["secs"] = round(time.time() - t0, 1)
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
    if get_gemini() is None:
        return {"error": "Gemini is not configured. Check GEMINI_KEY and add `google-genai` to requirements.txt."}
    try:
        data = pdf_file.getvalue()
        if len(data) > 18 * 1024 * 1024:
            return {"error": "This PDF is larger than 18 MB. Split it or export a smaller copy, then upload again."}
        result = call_json(PDF_PROMPT, pdf=data)
        result["source_name"] = filename
        return result
    except Exception as exc:
        return {"error": str(exc)}


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
def radar_scan(symbols: list, style: dict, with_track: bool = False):
    """-> rows, or (rows, track) when with_track: track = track_record over every zone of every scanned stock."""
    frames = fetch_many(tuple(symbols))
    rows, hist = [], []
    for sym, df in frames.items():
        try:
            det = analyze_market(df, style, live_fraction(df.index[-1]))
            if with_track:
                hist += zone_history(df, style)
            plan = deterministic_plan(det)
            z = next((x for x in det["zones"] if plan and x["id"] == plan["ref_zone"]), None)
            near = min([x for x in det["zones"] if x["dist_atr"] is not None], key=lambda x: x["dist_atr"], default=None)
            rows.append({"symbol": sym, "close": det["close"], "chg": det["day_chg_pct"], "structure": det["structure"].split(" ")[0], "rsi": det["rsi"],
                         "zone": (z or near or {}).get("id"), "ztype": (z or near or {}).get("type"), "quality": (z or near or {}).get("quality"),
                         "status": (z or near or {}).get("status"), "dist_pct": (z or near or {}).get("dist_pct"), "dist_atr": (z or near or {}).get("dist_atr"),
                         "direction": plan["direction"] if plan else None, "rr": plan["targets"][0]["rr"] if plan and plan["targets"] else None,
                         "room_r": plan.get("room_r") if plan else None, "blocked": bool(plan and plan.get("blocked")),
                         "score": engine_score(det, plan), "n_zones": len(det["zones"])})
        except Exception:
            continue
    rows.sort(key=lambda r: -r["score"])
    if with_track:
        tr = track_record(hist)
        tr["n_stocks"] = len(frames)
        return rows, tr
    return rows


# ══════════════════════════════════════════════════════════
# 9. INTERFACE
# ══════════════════════════════════════════════════════════
ARKA_CSS = """
<style>
.aa{--r:12px;}
.aa-chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 12px;}
.aa-chip{display:inline-flex;align-items:center;gap:7px;padding:4px 10px;border-radius:999px;background:var(--s2,#161618);font:500 12px var(--sans,Inter,sans-serif);color:var(--tx2,#A1A1AA);white-space:nowrap;}
.aa-chip i{width:6px;height:6px;border-radius:50%;background:#52525B;display:inline-block;}
.aa-chip.ok i{background:#30D158;} .aa-chip.warn i{background:#FF9F0A;} .aa-chip.bad i{background:#FF453A;}
.aa-chip.ok{color:#E4E4E7;}
.aa-intro{border-radius:12px;background:var(--s1,#111113);border:1px solid var(--line,#232326);padding:14px 16px;margin:4px 0 12px;font:14px/1.65 var(--sans);color:var(--tx2,#A1A1AA);}
.aa-intro b{color:var(--tx,#EDEDEF);font-weight:600;}
.aa-steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:8px 0 14px;}
.aa-step{border-radius:12px;border:1px solid var(--line);background:var(--s1);padding:16px;}
.aa-step .n{font:600 12px var(--sans);color:#FF9F0A;}
.aa-step .t{font:600 15px var(--sans);color:var(--tx);margin:6px 0 4px;}
.aa-step .d{font:13.5px/1.6 var(--sans);color:var(--tx3,#71717A);}

/* hero */
.aa-hero{display:grid;grid-template-columns:minmax(0,1.1fr) auto minmax(0,1.3fr);gap:20px 28px;align-items:center;background:var(--s1);
  border:1px solid var(--line);border-radius:16px 16px 0 0;padding:20px 22px;margin-top:8px;position:relative;overflow:hidden;}
.aa-hero:before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--vc,#FF9F0A);opacity:.9;}
@media(max-width:900px){.aa-hero{grid-template-columns:1fr auto;} .aa-hero>div:last-child{grid-column:1/-1;}}
.aa-sym{font:600 13px var(--sans);color:var(--tx3);}
.aa-px{font:600 30px var(--sans);color:var(--tx);margin-top:4px;letter-spacing:-.6px;font-variant-numeric:tabular-nums;}
.aa-px em{font:600 13px var(--sans);font-style:normal;padding:3px 9px;border-radius:999px;margin-left:8px;vertical-align:middle;letter-spacing:0;}
.aa-px em.up{color:#30D158;background:rgba(48,209,88,.12);} .aa-px em.dn{color:#FF453A;background:rgba(255,69,58,.12);}
.aa-sub{font:12.5px var(--sans);color:var(--tx3);margin-top:6px;}
.aa-verdict{display:inline-flex;align-items:center;gap:8px;font:600 14px var(--sans);padding:7px 14px;border-radius:999px;color:var(--vc);
  background:color-mix(in srgb,var(--vc) 14%,transparent);}
.aa-hm .aa-chips{margin:10px 0 0;}
.aa-mini{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;}
.aa-mini div{background:var(--s2);border-radius:10px;padding:10px 12px;}
.aa-mini div span{display:block;font:500 12px var(--sans);color:var(--tx3);}
.aa-mini div b{display:block;font:600 16px var(--sans);color:var(--tx);font-variant-numeric:tabular-nums;margin-top:2px;}
.aa-mini div b.dn{color:#FF453A;} .aa-mini div b.up{color:#30D158;}
.aa-head{border:1px solid var(--line);border-top:0;border-radius:0 0 16px 16px;background:var(--s2);padding:12px 22px;font:500 14.5px/1.6 var(--sans);color:var(--tx);}
.aa-head span{display:block;color:var(--tx3);font:12.5px var(--sans);margin-top:2px;}

/* cards */
.aa-card{background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:16px;min-height:100px;}
.aa-ct{display:flex;justify-content:space-between;align-items:baseline;font:600 15px var(--sans);color:var(--tx);padding-bottom:8px;margin-bottom:4px;}
.aa-ct span{color:var(--tx3);font:500 12.5px var(--sans);}
.aa-kv{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--line);font:13.5px var(--sans);color:var(--tx3);}
.aa-kv:last-child{border-bottom:0;}
.aa-kv b{font:600 14px var(--sans);color:var(--tx);text-align:right;font-variant-numeric:tabular-nums;}
.aa-kv b.dn{color:#FF453A;} .aa-kv b.up{color:#30D158;} .aa-kv b.cy{color:#5AC8FA;}
.aa-kv em{display:block;font:12px var(--sans);font-style:normal;color:var(--tx3);text-align:right;margin-top:2px;max-width:280px;}
.aa-note{font:13px/1.65 var(--sans);color:var(--tx3);margin-top:10px;}
.aa-empty{border-radius:12px;background:var(--s1);border:1px dashed var(--line2,#2E2E33);padding:22px;text-align:center;color:var(--tx3);font:14px/1.7 var(--sans);}

/* scorecard */
.aa-sh{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-top:12px;font:600 14px var(--sans);color:var(--tx);}
.aa-sh span{font:500 12.5px var(--sans);color:#FF9F0A;}
.aa-ck{border-radius:10px;background:var(--s2);padding:10px 12px;margin-top:6px;border-left:3px solid var(--c);}
.aa-ck .h{display:flex;justify-content:space-between;gap:8px;font:500 13.5px var(--sans);color:var(--tx);}
.aa-ck .h>span{font:600 12px var(--sans);color:var(--c);white-space:nowrap;}
.aa-ck .r{font:13px/1.55 var(--sans);color:var(--tx2);margin-top:4px;}
.aa-ck .b{height:3px;background:var(--s3,#1C1C1F);margin-top:8px;border-radius:2px;overflow:hidden;} .aa-ck .b i{display:block;height:3px;background:var(--c);}
.aa-ref{display:inline-block;font:600 11px var(--sans);color:#5AC8FA;background:rgba(90,200,250,.10);padding:1px 7px;border-radius:999px;margin-left:6px;}

.aa-list{list-style:none;margin:0;padding:0;}
.aa-list li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font:13.5px/1.55 var(--sans);color:var(--tx2);}
.aa-list li:last-child{border-bottom:0;}
.aa-list li:before{content:"";width:6px;height:6px;margin-top:8px;border-radius:50%;background:var(--c);flex-shrink:0;}
.aa-scn{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;}
.aa-sc{background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:16px;}
.aa-sc .t{font:600 13px var(--sans);color:var(--c);}
.aa-sc .l{font:600 20px var(--sans);color:var(--tx);margin:6px 0 6px;font-variant-numeric:tabular-nums;}
.aa-sc .d{font:13.5px/1.6 var(--sans);color:var(--tx2);}
.aa-sc .d b{color:var(--tx);font-weight:600;}

/* tables */
.aa-tw{overflow:auto;border:1px solid var(--line);border-radius:12px;background:var(--s1);margin:6px 0 10px;}
.aa-t{width:100%;border-collapse:separate;border-spacing:0;font:13.5px var(--sans);color:var(--tx);}
.aa-t th{position:sticky;top:0;background:var(--s1);color:var(--tx3);font:500 12px var(--sans);text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;}
.aa-t td{padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle;}
.aa-t tr:last-child td{border-bottom:0;}
.aa-t tbody tr:hover td{background:rgba(255,255,255,.02);}
.aa-t td.n{text-align:right;color:var(--tx2);font-variant-numeric:tabular-nums;}
.aa-t b{color:var(--tx);font:600 13.5px var(--sans);}
.aa-pill{display:inline-block;padding:2px 9px;border-radius:999px;font:600 11.5px var(--sans);color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent);}
.aa-q{display:inline-flex;align-items:center;gap:8px;font:600 13px var(--sans);color:var(--tx);}
.aa-q i{display:inline-block;width:54px;height:5px;background:var(--s3);border-radius:3px;position:relative;overflow:hidden;}
.aa-q i:after{content:"";position:absolute;left:0;top:0;height:100%;width:var(--w);background:var(--c);border-radius:3px;}
.aa-sec{display:flex;align-items:baseline;gap:10px;margin:30px 0 10px;flex-wrap:wrap;}
.aa-sec b{font:600 17px var(--sans);letter-spacing:-.2px;color:var(--tx);}
.aa-sec span{font:13px var(--sans);color:var(--tx3);}
.aa-lab{border-radius:10px;background:var(--s1);border:1px solid var(--line);padding:10px 12px;margin-top:6px;font:13.5px/1.55 var(--sans);color:var(--tx2);}
.aa-lab b{font:600 12.5px var(--sans);color:#FF9F0A;margin-right:8px;}
.aa-item{border-radius:12px;background:var(--s1);border:1px solid var(--line);border-left:3px solid var(--c);padding:12px 14px;margin-top:8px;}
.aa-item .h{display:flex;justify-content:space-between;gap:10px;align-items:center;} .aa-item .h b{font:600 14.5px var(--sans);color:var(--tx);}
.aa-item .d{font:13px/1.6 var(--sans);color:var(--tx3);margin-top:4px;}
.aa-time{font:500 12.5px var(--sans);color:var(--tx3);margin:6px 0 0;}
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
    n_calls = sum(1 for i in load_items() if i["kind"] == "call")
    src = style_source()
    chips = [_chip(("AI online · " + ai_model_name()) if ai_ok else "AI offline · engine only", "ok" if ai_ok else "warn"),
             _chip("Playbook saved to cloud" if mem == "CLOUD" else "Playbook: this session only", "ok" if mem == "CLOUD" else "warn"),
             _chip(f"{n_set} active setup{'s' if n_set != 1 else ''}", "ok" if n_set else "bad"),
             _chip(f"{n_les} lesson{'s' if n_les != 1 else ''}", "ok" if n_les else ""),
             _chip(f"{n_lab} labelled set{'s' if n_lab != 1 else ''}", "ok" if n_lab else ""),
             _chip("Zone style: " + ("default" if src == "DEFAULT" else src), "ok" if src != "DEFAULT" else ""),
             _chip(f"{n_calls} plan{'s' if n_calls != 1 else ''} in journal", "ok" if n_calls else "")]
    if not HAS_PLOTLY:
        chips.append(_chip("Install plotly for interactive charts", "warn"))
    return '<div class="aa-chips">' + "".join(chips) + "</div>"


def _pill(text, color):
    return f'<span class="aa-pill" style="--c:{color}">{esc(text)}</span>'


def _ring(score: int, color: str) -> str:
    c = 2 * math.pi * 34
    return (f'<svg width="92" height="92" viewBox="0 0 92 92"><circle cx="46" cy="46" r="34" fill="none" stroke="#232326" stroke-width="6"/>'
            f'<circle cx="46" cy="46" r="34" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round" '
            f'stroke-dasharray="{max(0, min(100, score)) / 100 * c:.1f} {c:.1f}" transform="rotate(-90 46 46)"/>'
            f'<text x="46" y="51" text-anchor="middle" font-family="Inter,system-ui,sans-serif" font-size="24" font-weight="600" fill="#EDEDEF">{score}</text>'
            f'<text x="46" y="66" text-anchor="middle" font-family="Inter,system-ui,sans-serif" font-size="9" fill="#71717A">score</text></svg>')


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
    nice = lambda x: str(x).replace("_", " ").capitalize()
    bias_cls = "ok" if bias == "BULLISH" else "bad" if bias == "BEARISH" else ""
    chips = (_chip(f"Grade {grade}", "ok" if grade in ("A+", "A") else "warn" if grade in ("B", "C") else "bad") + _chip(nice(bias), bias_cls)
             + _chip(nice(state)) + _chip(("AI + engine" if ai else "Engine only"), "ok" if ai else "warn"))
    mk = r.get("market")
    if mk:
        ns = mk["nifty_structure"].split(" ")[0]
        chips += _chip(f"NIFTY {ns.lower()}", "ok" if ns == "UPTREND" else "bad" if ns == "DOWNTREND" else "")
        if mk.get("rs_3m_pp") is not None:
            chips += _chip(f"vs NIFTY 3M {mk['rs_3m_pp']:+.1f} pp", "ok" if mk["rs_3m_pp"] > 0 else "bad")
    if r.get("results"):
        days = (pd.Timestamp(r["results"]) - pd.Timestamp(datetime.now(IST).date())).days
        if 0 <= days <= 14:
            chips += _chip(f"Results in {days} day{'s' if days != 1 else ''}" if days else "Results today", "warn")
    if det.get("live"):
        chips += _chip("Today's candle unfinished", "warn")
    p = r.get("plan")
    if p:
        t1 = p["targets"][0] if p["targets"] else None
        mini = ('<div class="aa-mini">'
                f'<div><span>{p["direction"].capitalize()} entry</span><b>{_fmt(p["entry_ref"])}</b></div><div><span>Stop</span><b class="dn">{_fmt(p["stop"])}</b></div>'
                f'<div><span>Target 1</span><b class="up">{_fmt(t1["price"]) if t1 else "—"}</b></div>'
                f'<div><span>Reward : risk</span><b>{(str(t1["rr"]) + " R") if t1 else "—"}</b></div></div>')
    else:
        mini = '<div class="aa-empty" style="padding:14px">No trade plan right now.<br>Waiting for price to reach one of your zones.</div>'
    head = ai.get("headline") or _engine_headline(det)
    timing = f' · analysed in {r["secs"]} s' if r.get("secs") is not None else ""
    return (f'<div class="aa-hero" style="--vc:{color}"><div><div class="aa-sym">{esc(r["ticker"])} · daily</div>'
            f'<div class="aa-px">₹{_fmt(det["close"])}<em class="{chg_cls}">{"+" if chg >= 0 else "−"}{abs(chg):.2f}%</em></div>'
            f'<div class="aa-sub">As of {esc(det["asof"])} · ATR {det["atr_pct"]}% · RSI {_fmt(det["rsi"], 0)}</div>'
            f'<div class="aa-hm" style="margin-top:12px"><span class="aa-verdict" style="--vc:{color}">{esc(label)}</span><div class="aa-chips">{chips}</div></div></div>'
            f'<div>{_ring(score, color)}</div><div>{mini}</div></div>'
            f'<div class="aa-head">{esc(head)}<span>{esc(r["ran_at"])}{timing}</span></div>')


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
        return f'<div class="aa-card"><div class="aa-ct">Trade plan<span>none</span></div><div class="aa-empty" style="border:0;background:var(--s2)">{esc(why)}</div>{alt}</div>'
    dcls = "up" if p["direction"] == "LONG" else "dn"
    ref_zone = p.get("ref_zone") or "-"
    tag = " · counter-trend" if p.get("counter_trend") else ""
    rows = [f'<div class="aa-kv"><span>Direction</span><b class="{dcls}">{p["direction"].capitalize()}{tag}</b></div>',
            f'<div class="aa-kv"><span>Based on</span><b>{esc(ref_zone)}</b></div>',
            f'<div class="aa-kv"><span>Entry zone</span><b class="cy">{_fmt(p["entry_low"])} – {_fmt(p["entry_high"])}</b></div>',
            f'<div class="aa-kv"><span>Entry reference</span><b>{_fmt(p["entry_ref"])}</b></div>',
            f'<div class="aa-kv"><span>Stop</span><div><b class="dn">{_fmt(p["stop"])}</b><em>{esc(p.get("stop_reason", ""))}</em></div></div>']
    for i, t in enumerate(p["targets"], 1):
        rr = f'{t["rr"]} R' if t.get("rr") is not None else ""
        rows.append(f'<div class="aa-kv"><span>Target {i} · {esc(t.get("ref", ""))}</span><div><b class="up">{_fmt(t["price"])}</b><em>{rr} · {esc(t.get("reason", ""))}</em></div></div>')
    if p.get("room_r") is not None:
        cls = "dn" if p.get("blocked") else "up" if p["room_r"] >= 2 else ""
        rows.append(f'<div class="aa-kv"><span>Room before first obstacle</span><div><b class="{cls}">{p["room_r"]:g} R</b>'
                    f'<em>{esc(p.get("obstacle") or "")} at {_fmt(p.get("obstacle_price"))}{" · price must break this level to reach the entry, after which it is in the way" if p.get("obstacle_flips") else ""}{" · less than 1R: the trade risks more than it has room to make" if p.get("blocked") else ""}</em></div></div>')
    sz = position_size(p, risk)
    if sz:
        rows.append(f'<div class="aa-kv"><span>Position size @ ₹{risk:,.0f} risk</span><div><b>{sz["qty"]:,} shares</b>'
                    f'<em>₹{sz["risk_per_share"]} risk per share · about ₹{sz["capital"]:,.0f} deployed</em></div></div>')
    extra = ""
    if p.get("invalidation"):
        extra += f'<div class="aa-note"><b style="color:#ECECEC">Invalidation:</b> {esc(p["invalidation"])}</div>'
    if p.get("confirmation_needed"):
        extra += '<div class="aa-note"><b style="color:#ECECEC">Wait for:</b> ' + " · ".join(esc(x) for x in p["confirmation_needed"]) + "</div>"
    return f'<div class="aa-card"><div class="aa-ct">Trade plan<span>{"from Arka AI" if p.get("source") == "ai" else "from the engine"}</span></div>{"".join(rows)}{extra}</div>'


def html_scorecard(r: dict) -> str:
    ai = r.get("ai")
    if not ai:
        msg = "AI is offline, so your written rules cannot be checked. Zones, levels and the plan above come from the engine." if not r.get("ai_error") else f"AI analysis failed: {r['ai_error']}"
        return f'<div class="aa-card"><div class="aa-ct">Setup scorecard<span>engine only</span></div><div class="aa-empty">{esc(msg)}</div></div>'
    if not ai["setups"]:
        return ('<div class="aa-card"><div class="aa-ct">Setup scorecard<span>no setup taught</span></div><div class="aa-empty">'
                'No active setup in your playbook.<br>Open Teach to import a PDF or write your rules.</div></div>')
    colors = {"PRESENT": GREEN, "NOT_PRESENT": RED, "UNCLEAR": AMBER}
    labels = {"PRESENT": "Present", "NOT_PRESENT": "Not present", "UNCLEAR": "Unclear"}
    out = ['<div class="aa-card"><div class="aa-ct">Setup scorecard<span>your rules, one by one</span></div>']
    for s in ai["setups"]:
        tally = f'{s.get("present", 0)} of {s.get("required", 0)} required present' + (f' · {s["unclear"]} unclear' if s.get("unclear") else "")
        out.append(f'<div class="aa-sh"><div>{esc(s["name"])}</div><span>{s["match_pct"]}% · {tally}</span></div>')
        for c in s["checks"]:
            col = colors[c["status"]]
            refs = "".join(f'<span class="aa-ref">{esc(x)}</span>' for x in c["refs"])
            kind = "" if c.get("required", True) else '<span class="aa-ref" style="color:#A1A1AA;background:#1C1C1F">optional</span>'
            kind += '<span class="aa-ref" style="color:#BF5AF2;background:rgba(191,90,242,.12)">confirmation</span>' if c.get("group") == "confirmation" else ""
            out.append(f'<div class="aa-ck" style="--c:{col}"><div class="h"><div><span style="color:#71717A;font:600 12px Inter,sans-serif;margin-right:6px">{esc(c.get("rule_id", ""))}</span>'
                       f'{esc(c["rule_name"])}{kind}{refs}</div><span>{labels[c["status"]]}</span></div>'
                       f'<div class="r">{esc(c["reason"])}</div><div class="b"><i style="width:{c["confidence"]}%"></i></div></div>')
    out.append("</div>")
    return "".join(out)


def html_read(ai: dict | None) -> str:
    if not ai:
        return '<div class="aa-card"><div class="aa-ct">The read<span>AI offline</span></div><div class="aa-empty">Supports, contradictions and what to watch appear here when the AI is online.</div></div>'

    def block(title, items, color, icon):
        li = "".join(f"<li>{esc(x)}</li>" for x in items) or '<li style="color:#71717A">Nothing to report.</li>'
        return f'<div class="aa-sh" style="margin-top:12px"><div>{title}</div></div><ul class="aa-list" style="--c:{color}">{li}</ul>'
    return ('<div class="aa-card"><div class="aa-ct">The read<span>against your playbook</span></div>'
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
            flags.append("Weekly confluence")
        if z["bos"]:
            flags.append("Broke structure")
        if z["aligned"]:
            flags.append("With trend")
        m = mine.get(z["id"])
        if m and m["matches_my_style"]:
            flags.append("Your style")
        role = esc(m["role"].replace("_", " ")) if m else "—"
        rows.append(f'<tr><td><b>{z["id"]}</b></td><td>{_pill(z["type"].capitalize(), col)}</td><td>{z["pattern"]}</td>'
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
        rows.append(f'<tr><td><b>{lv["id"]}</b></td><td>{_pill(lv["kind"].capitalize(), col)}</td><td class="n">{_fmt(lv["price"])}</td><td class="n">{lv["touches"]}</td>'
                    f'<td class="n">{_fmt(lv["dist_pct"])}%</td><td>{"role reversal" if lv["flip"] else "—"}</td></tr>')
    for t in det["trendlines"]:
        col = GREEN if t["kind"] == "support" else RED
        rows.append(f'<tr><td><b>{t["id"]}</b></td><td>{_pill("Trendline · " + t["kind"], col)}</td><td class="n">{_fmt(t["p1"])}</td>'
                    f'<td class="n">{t["touches"]}</td><td class="n">{_fmt(t["dist_pct"])}%</td><td>slope {t["slope_pct_per_bar"]}%/bar</td></tr>')
    if not rows:
        return '<div class="aa-empty">No support / resistance levels with enough touches.</div>'
    head = "".join(f"<th>{h}</th>" for h in ("ID", "Kind", "Price", "Touches", "Distance", "Note"))
    return f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def html_scenarios(ai: dict | None) -> str:
    if not ai:
        return ""
    cards = []
    for key, title, col in (("bull", "Bull case", GREEN), ("bear", "Bear case", RED), ("neutral", "Neutral / range", AMBER)):
        s = ai["scenarios"][key]
        lvl = (_fmt(s.get("level")) + (f" · {s['ref']}" if s.get("ref") else "")) if isinstance(s.get("level"), (int, float)) else "—"
        cards.append(f'<div class="aa-sc" style="--c:{col}"><div class="t">{title}</div><div class="l">{lvl}</div>'
                     f'<div class="d"><b>Trigger:</b> {esc(s.get("trigger") or "—")}<br><b>Path:</b> {esc(s.get("path") or "—")}</div></div>')
    return '<div class="aa-scn">' + "".join(cards) + "</div>"


def _pct_cell(n, p, base=None):
    if not n or p is None:
        return '<td class="n" style="color:#71717A">—</td>'
    col = "#D4D4D8" if base is None else (GREEN if p > base else RED if p < base - 5 else "#D4D4D8")
    return f'<td class="n" style="color:{col}">{p:.0f}% <span style="color:#71717A">of {n}</span></td>'


def html_track(tr: dict, universe: dict | None = None) -> str:
    """Track record table. 33% reaching 2R first is break-even before costs; random price data gives about 30%."""
    g = tr["groups"]
    if not g["All zones"]["formed"]:
        return '<div class="aa-empty">No zone of your style formed on this stock in the last two years.</div>'
    rows = []

    def line(label, x, bold=False):
        exp = "—" if x["exp2"] is None else f'<span style="color:{GREEN if x["exp2"] > 0 else RED}">{x["exp2"]:+.2f} R</span>'
        name = f"<b>{esc(label)}</b>" if bold else esc(label)
        rows.append(f'<tr><td>{name}</td><td class="n">{x["formed"]}</td><td class="n">{x["tested"]}</td>{_pct_cell(x["n1"], x["p1"], 50)}{_pct_cell(x["n2"], x["p2"], 33.3)}<td class="n">{exp}</td></tr>')
    if tr.get("like") and tr.get("like_label"):
        line("Like the plan's zone · " + tr["like_label"], tr["like"], True)
    for k in ("All zones", "Demand", "Supply", "With trend", "Against trend", "Q 70+", "Q 55-69", "Q below 55"):
        if g[k]["formed"]:
            line(k, g[k])
    if universe and universe.get("groups", {}).get("All zones", {}).get("formed"):
        line(f'Your last radar scan · {universe.get("n_stocks", 0)} stocks · all zones', universe["groups"]["All zones"], True)
    head = "".join(f"<th>{h}</th>" for h in ("Zones of your style", "Formed", "Tested", "Reached 1R before stop", "Reached 2R before stop", "Average result aiming for 2R"))
    recent = ""
    if tr.get("recent"):
        col = {"2R": GREEN, "STOP": RED, "OPEN": AMBER}
        recent = '<div class="aa-note">Latest tested zones: ' + " · ".join(
            f'{esc(x["touch_date"])} {x["type"]} Q{x["quality"]} <b style="color:{col.get(x["outcome"], T2)}">{"reached 2R" if x["outcome"] == "2R" else "stopped" + (" after 1R" if x.get("hit1") else "") if x["outcome"] == "STOP" else "still open"}</b>'
            for x in tr["recent"][:6]) + "</div>"
    return f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>{recent}'


def png_for(r: dict, window: int) -> bytes:
    cache = r.setdefault("png_cache", {})
    if window not in cache:
        label, _ = verdict_text(r)
        cache[window] = render_png(r["df"], r["spec"], window=window, title=f"{r['ticker']}  ·  Daily  ·  {r['asof']}  ·  {label}  ·  score {r['score']}")
    return cache[window]


LAYERS = ["Zones", "Levels", "Trendlines", "Trade plan", "Moving averages", "Signals"]
_LAYER_KEYS = {"Zones": "zones", "Levels": "levels", "Trendlines": "lines", "Trade plan": "plan", "Moving averages": "mas", "Signals": "markers"}


def _is_phone() -> bool:
    return bool(st.session_state.get("_ak_phone"))


def _cols(spec):
    """Columns on a desktop; full-width blocks one under another on a phone."""
    n = spec if isinstance(spec, int) else len(spec)
    return [st.container() for _ in range(n)] if _is_phone() else st.columns(spec, gap="medium")


def render_chart(r: dict, window: int):
    try:
        picked = st.pills("Chart layers", LAYERS, selection_mode="multi", default=LAYERS, key="aa_layers", label_visibility="collapsed")
    except Exception:                                            # Streamlit without st.pills
        cols = st.columns(6)
        picked = [lab for col, lab in zip(cols, LAYERS) if col.checkbox(lab, True, key=f"aa_t_{_LAYER_KEYS[lab]}")]
    picked = picked or []
    show = {v: (k in picked) for k, v in _LAYER_KEYS.items()}
    height = 560 if _is_phone() else 780
    cache = r.setdefault("fig_cache", {})
    ck = (window, height, tuple(sorted(k for k, v in show.items() if v)))
    if ck not in cache:                                          # build each view once; clicks elsewhere reuse it
        cache.clear()
        cache[ck] = fig_plotly(r["df"], r["spec"], window, show, height)
    fig = cache[ck]
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
    c1, c2, c3 = _cols([1.05, 1.25, 1.0])
    with c1:
        st.markdown(html_plan(r, risk), unsafe_allow_html=True)
    with c2:
        st.markdown(html_scorecard(r), unsafe_allow_html=True)
    with c3:
        st.markdown(html_read(r.get("ai")), unsafe_allow_html=True)
    fixes = (r.get("ai") or {}).get("corrections") or []
    if fixes:
        with st.expander(f"Engine check of the AI · {len(fixes)} correction{'s' if len(fixes) != 1 else ''} applied", expanded=False):
            st.caption("Everything the AI says is matched back to your playbook rules and to the detected zones and levels. These are the points where its reply did not match and what was done.")
            for f in fixes:
                st.markdown(f"- {f}")
    if r.get("ai"):
        _sec("Scenarios", "what would change the picture")
        st.markdown(html_scenarios(r["ai"]), unsafe_allow_html=True)
    if r.get("track"):
        _sec("Track record of your zone style", f"every zone of this style on {r['ticker']} in the last 2 years, replayed with no hindsight")
        st.markdown(html_track(r["track"], st.session_state.get("aa_track_univ")), unsafe_allow_html=True)
        st.caption("How to read it: a zone is 'tested' the first time price returns to it. Entry at the proximal line, stop 0.25 ATR beyond the distal line. "
                   "Reaching 2R before the stop one time in three is break-even before costs; on random price data this replay gives about 30%, so a real edge "
                   "has to sit clearly above that. Fewer than 20 tested zones is too small a sample to trust. Daily candles only: if one candle touches both "
                   "stop and target the stop is counted, and brokerage and slippage are not included.")
    _sec("Supply & demand zones found", "every zone the engine detects, with your style applied")
    st.markdown(html_zones(r), unsafe_allow_html=True)
    _sec("Support, resistance and trendlines")
    st.markdown(html_levels(r), unsafe_allow_html=True)
    ai = r.get("ai")
    if ai and ai.get("narrative"):
        with st.expander("Full analysis", expanded=False):
            st.write(ai["narrative"])
            if ai.get("context"):
                st.markdown("**Context (not used for the verdict)**")
                for c in ai["context"]:
                    st.markdown(f"- {c}")
    with st.expander("Engine evidence (the exact numbers the AI was given)", expanded=False):
        d = r["det"]
        st.json({"market": r.get("market"), "next_results_date": r.get("results"), "ai_model": r.get("model") or None,
                 "structure": d["structure"], "ma_state": d["ma_state"], "sma": d["sma"], "volume": d["volume"], "refs": d["refs"], "swings": d["swings"],
                 "events": d["events"], "candle_signals": d["candles"], "weekly": ({"structure": r["det_w"]["structure"], "ma_state": r["det_w"]["ma_state"]} if r.get("det_w") else None)})
    if ai:
        _sec("Ask Arka about this chart", "follow-up questions use the same data")
        q = st.text_input("Question", key="aa_q", placeholder="e.g. Why is DZ1 better than DZ2? Where would you trail the stop?")
        if st.button("Ask Arka", key="aa_ask", type="primary") and q.strip():
            with st.spinner("Thinking…"):
                try:
                    ans = ask_followup(r, q.strip())
                except Exception as exc:
                    ans = f"Could not answer: {exc}"
            st.session_state.setdefault("aa_qa", []).append((r["ticker"], q.strip(), ans))
        for tk, qq, aa in reversed([x for x in st.session_state.get("aa_qa", []) if x[0] == r["ticker"]][-4:]):
            st.markdown(f'<div class="aa-lab"><b style="color:#A1A1AA">You</b>{esc(qq)}</div><div class="aa-lab" style="border-left:3px solid #FF9F0A"><b>Arka</b>{esc(aa)}</div>', unsafe_allow_html=True)
    _sec("Export")
    e1, e2, e3, e4 = _cols(4)
    if window in r.get("png_cache", {}):                     # the PNG is built only when asked for (it takes a second or two)
        e1.download_button("Download chart (PNG)", png_for(r, window), file_name=f"{r['ticker']}_arka_{r['asof']}.png", mime="image/png",
                           use_container_width=True)
    elif e1.button("Prepare chart image", key="aa_png_make", use_container_width=True):
        with st.spinner("Drawing the chart…"):
            png_for(r, window)
        st.rerun()
    payload = {k: r.get(k) for k in ("ticker", "asof", "score", "plan", "ai", "engine_plan", "market", "results", "model")}
    payload.update({"zones": r["det"]["zones"], "levels": r["det"]["levels"], "trendlines": r["det"]["trendlines"],
                    "track_record": (r.get("track") or {}).get("groups")})
    report_json = json.dumps(payload, default=str, ensure_ascii=False, indent=2)
    e2.download_button("Download report (JSON)", report_json, file_name=f"{r['ticker']}_arka_{r['asof']}.json", mime="application/json", use_container_width=True)
    if e3.button("Add to watchlist", key="aa_wl", use_container_width=True):
        try:
            import user_data as ud
            added, err = ud.add_symbols(_SB, [r["ticker"]], ud.W_TABLE)
            if added:
                st.toast(f"{r['ticker']} added to your watchlist")
            else:
                st.info(err or "Already in your watchlist")
        except Exception as exc:
            st.error(f"Could not add: {exc}")
    p = r.get("plan")
    if e4.button("Log this plan", key="aa_log", use_container_width=True, disabled=not p, type="primary",
                 help="Saves the plan to your JOURNAL. Arka then checks every day whether it triggered, hit target 1 or was stopped."):
        label, _ = verdict_text(r)
        call = {k: p.get(k) for k in ("direction", "ref_zone", "entry_low", "entry_high", "entry_ref", "stop", "targets", "source", "room_r")}
        call.update({"ticker": r["ticker"], "asof": r["asof"], "close_at_log": r["det"]["close"], "score": r["score"], "verdict": label,
                     "summary": f'{p["direction"]} {r["ticker"]} from {p.get("ref_zone")} · entry {p["entry_ref"]:,.2f} · stop {p["stop"]:,.2f}'})
        if save_item("call", f'{r["ticker"]} · {p["direction"]} · {r["asof"]}', call, True):
            st.toast("Plan logged. Open Journal to follow it.")


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
        '<div class="aa-step"><div class="n">Step 1 · Teach</div><div class="t">Show Arka your style</div><div class="d">Import your strategy PDF, tick the zones you like and cross the ones you don\'t, or upload charts you marked up.</div></div>'
        '<div class="aa-step"><div class="n">Step 2 · Analyze</div><div class="t">Type a stock</div><div class="d">Every zone, level and trendline is detected with exact prices, then judged against your playbook.</div></div>'
        '<div class="aa-step"><div class="n">Step 3 · Act</div><div class="t">Get the marked-up chart</div><div class="d">Verdict, scorecard, entry zone, stop, targets, position size and scenarios, ready to download.</div></div></div>',
        unsafe_allow_html=True)


def render_analyze():
    phone = _is_phone()
    if phone:
        ticker = st.text_input("Stock", key="aa_ticker", placeholder="RELIANCE, TCS, NIFTY50…")
        go_clicked = st.button("Analyze", type="primary", use_container_width=True, key="aa_go")
        with st.expander("Options"):
            window = st.selectbox("Candles shown", [80, 120, 180, 250], index=1, key="aa_window")
            risk = st.number_input("Risk per trade (₹)", min_value=0.0, value=1000.0, step=500.0, key="aa_risk")
            st.radio("Speed", list(SPEEDS), key="aa_speed", horizontal=True,
                     help="How long Arka AI may think. Fast ≈ quickest, Deep = most thorough (slowest).")
            note = st.text_input("Note for Arka (optional)", key="aa_note", placeholder="e.g. only long setups from fresh demand")
            ai_online = get_gemini() is not None
            use_ai = st.toggle("Use Arka AI", value=True, key="aa_use_ai", disabled=not ai_online)
    else:
        c1, c2, c3, c4 = st.columns([3.0, 1.1, 1.3, 1.2], gap="small")
        ticker = c1.text_input("Stock", key="aa_ticker", placeholder="RELIANCE, TCS, HDFCBANK, NIFTY50…")
        window = c2.selectbox("Candles shown", [80, 120, 180, 250], index=1, key="aa_window")
        risk = c3.number_input("Risk per trade (₹)", min_value=0.0, value=1000.0, step=500.0, key="aa_risk")
        c4.selectbox("Speed", list(SPEEDS), key="aa_speed", help="How long Arka AI may think. Fast is quickest, Deep is most thorough (slowest).")
        d1, d2, d3 = st.columns([4.2, 1.3, 1.3], gap="small")
        note = d1.text_input("Note for Arka (optional)", key="aa_note", placeholder="e.g. I'm only interested in long setups from fresh demand zones",
                             label_visibility="collapsed")
        ai_online = get_gemini() is not None
        use_ai = d2.toggle("Use Arka AI", value=True, key="aa_use_ai", disabled=not ai_online)
        go_clicked = d3.button("Analyze", type="primary", use_container_width=True, key="aa_go")
    recents = st.session_state.get("aa_recents", [])
    if recents:
        try:
            st.pills("Recent", recents[:8], key="aa_recent_pick", on_change=_recent_picked, label_visibility="collapsed")
        except Exception:
            cols = st.columns(min(len(recents), 8), gap="small")
            for col, sym in zip(cols, recents[:8]):
                col.button(sym, key=f"aa_rc_{sym}", use_container_width=True, on_click=_set_run, args=(sym,))
    req = st.session_state.pop("aa_run_request", None)
    if go_clicked and ticker.strip():
        req = ticker.strip().upper()
    if req:
        _run_with_progress(req, note, use_ai and ai_online, window)
    r = st.session_state.get("aa_report")
    if r:
        render_report(r, window, risk)
    elif not (active_items("setup") or active_items("lesson") or active_items("feedback")):
        render_getting_started()
    else:
        st.markdown('<div class="aa-empty">Type a stock above and press <b>Analyze</b>.<br>Your playbook is loaded and ready.</div>', unsafe_allow_html=True)


def _recent_picked():
    sym = st.session_state.get("aa_recent_pick")
    if sym:
        _set_run(sym)
    st.session_state["aa_recent_pick"] = None                 # allowed here: this runs as the widget's callback


def _run_with_progress(req: str, note: str, use_ai: bool, window: int):
    """Runs the analysis inside a live progress panel that shows each step and the total time."""
    try:
        box = st.status(f"Analysing {req}…", expanded=False)
    except Exception:
        box = None
    steps = []

    def say(msg):
        steps.append(msg)
        if box is not None:
            box.update(label=f"{msg}…")
            box.write(msg)
    try:
        if box is None:
            with st.spinner(f"Analysing {req}…"):
                rep_ = run_analysis(req, note, use_ai, window, say)
        else:
            rep_ = run_analysis(req, note, use_ai, window, say)
        st.session_state["aa_report"] = rep_
        rec = [req] + [x for x in st.session_state.get("aa_recents", []) if x != req]
        st.session_state["aa_recents"] = rec[:8]
        if box is not None:
            extra = f" · AI {rep_['ai_secs']} s" if rep_.get("ai_secs") is not None else ""
            box.update(label=f"{req} analysed in {rep_['secs']} s{extra}", state="complete", expanded=False)
    except Exception as exc:
        st.session_state.pop("aa_report", None)
        if box is not None:
            box.update(label=f"Could not analyse {req}", state="error", expanded=True)
        st.error(f"Could not analyse {req}: {exc}")


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
    want_track = st.checkbox("Also replay the track record of my zone style on these stocks (2 years, a few seconds more)", value=True, key="aa_radar_track")
    if st.button("Scan for my zones", type="primary", key="aa_radar_go", disabled=not symbols):
        with st.spinner(f"Scanning {len(symbols)} stocks…"):
            try:
                out = radar_scan(symbols, get_style(), want_track)
                rows_, tr_ = out if want_track else (out, None)
                st.session_state["aa_radar"] = {"rows": rows_, "at": datetime.now(IST).strftime("%d %b %H:%M IST"), "track": tr_}
                if tr_:
                    st.session_state["aa_track_univ"] = tr_
            except Exception as exc:
                st.error(f"Scan failed: {exc}")
    res = st.session_state.get("aa_radar")
    if not res:
        st.markdown('<div class="aa-empty">Press <b>Scan for my zones</b> to rank your list by how ready each stock is.</div>', unsafe_allow_html=True)
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
                    f'<td>{(_pill(x["ztype"].capitalize(), zc) + " " + esc(x["zone"])) if x["zone"] else "—"}</td>'
                    f'<td class="n">{x["quality"] if x["quality"] is not None else "—"}</td><td>{esc(x["status"] or "—")}</td>'
                    f'<td class="n">{(_fmt(x["dist_pct"]) + "%") if x["dist_pct"] is not None else "—"}</td>'
                    f'<td>{esc(x["direction"] or "—")}</td><td class="n">{(str(x["rr"]) + " R") if x["rr"] else "—"}</td>'
                    f'<td class="n" style="color:{RED if x.get("blocked") else "#CFCFCF"}">{(format(x["room_r"], "g") + " R") if x.get("room_r") is not None else "—"}</td>'
                    f'<td><span class="aa-q" style="--c:{scol};--w:{sc}%"><i></i>{sc}</span></td></tr>')
    head = "".join(f"<th>{h}</th>" for h in ("Symbol", "Price", "1D", "Trend", "RSI", "Nearest zone", "Quality", "Status", "Distance", "Plan", "T1 R:R", "Room", "Readiness"))
    st.markdown(f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)
    st.caption(f"Scanned {res['at']}. Readiness = zone quality, proximity, trend, weekly confluence and confirmation. Analyze a name for the full AI read.")
    if res.get("track"):
        _sec("Track record of your zone style on this list", f'{res["track"].get("n_stocks", 0)} stocks · 2 years · replayed with no hindsight')
        st.markdown(html_track(res["track"]), unsafe_allow_html=True)
        st.caption("This is the evidence for your ZONE STYLE settings: change a setting in TEACH → ZONE STYLE, scan again, and compare. "
                   "Break-even is 33% reaching 2R first; random price data gives about 30%. Costs are not included.")
    p1, p2 = st.columns([3, 1], gap="small")
    p1.selectbox("Open in Analyze", [x["symbol"] for x in rows], key="aa_radar_pick", label_visibility="collapsed")
    p2.button("Analyze selected", key="aa_radar_open", use_container_width=True, on_click=_analyze_selected, type="primary")


# ── JOURNAL ─────────────────────────────────────────────────
def render_journal():
    st.markdown('<div class="aa-intro"><b>Your logged plans, checked against what the market did.</b> Press <b>Log this plan</b> under any analysis. '
                'Each plan is then followed on daily candles: did price reach the entry, then target 1 or the stop first. This is the real, forward '
                'record of the plans you chose to take, which no backtest can give you.</div>', unsafe_allow_html=True)
    calls = [i for i in load_items() if i["kind"] == "call"]
    if not calls:
        st.markdown('<div class="aa-empty">No plans logged yet.<br>Analyze a stock and press <b>Log this plan</b>.</div>', unsafe_allow_html=True)
        return
    rows, closed = [], []
    calls = calls[:60]
    tick = sorted({str(it["payload"].get("ticker", "")).upper() for it in calls if it["payload"].get("ticker")})
    frames = {}
    stocks = tuple(x for x in tick if not is_index(x))
    if stocks:
        try:
            frames = fetch_many(stocks)                               # one batched download for every plan
        except Exception:
            frames = {}
    for it in calls:
        p = it["payload"]
        try:
            df_ = frames.get(str(p["ticker"]).upper())
            ev = evaluate_call(p, df_ if df_ is not None else fetch_ohlc(p["ticker"], "1d", "2y"))
        except Exception as exc:
            ev = {"status": f"NO DATA ({type(exc).__name__})", "r": None, "days": 0, "entry": None, "last": None}
        rows.append((it, p, ev))
        if ev["status"] in ("STOPPED", "TARGET 1 HIT"):
            closed.append(ev["r"])
    wins = sum(1 for x in closed if x > 0)
    open_r = [ev["r"] for _, _, ev in rows if ev["status"] == "OPEN" and ev["r"] is not None]
    kp = lambda lab, val: f'<div><span>{lab}</span><b>{val}</b></div>'
    st.markdown('<div class="aa-card" style="min-height:0"><div class="aa-mini" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))">'
                + kp("Plans logged", len(rows)) + kp("Closed", len(closed)) + kp("Hit target 1", f"{wins} of {len(closed)}" if closed else "—")
                + kp("Result of closed plans", f"{sum(closed):+.1f} R" if closed else "—") + kp("Open plans now", f"{sum(open_r):+.1f} R" if open_r else "—")
                + "</div></div>", unsafe_allow_html=True)
    colors = {"TARGET 1 HIT": GREEN, "STOPPED": RED, "OPEN": AMBER, "WAITING": T2}
    body = []
    for it, p, ev in rows:
        t1 = p["targets"][0]["price"] if p.get("targets") else None
        rr = "—" if ev["r"] is None else f'<span style="color:{GREEN if ev["r"] > 0 else RED if ev["r"] < 0 else T2}">{ev["r"]:+.2f} R</span>'
        body.append(f'<tr><td><b>{esc(p["ticker"])}</b></td><td>{esc(p["asof"])}</td><td>{_pill(p["direction"].capitalize(), GREEN if p["direction"] == "LONG" else RED)}</td>'
                    f'<td class="n">{_fmt(p["entry_low"])} – {_fmt(p["entry_high"])}</td><td class="n">{_fmt(p["stop"])}</td><td class="n">{_fmt(t1)}</td>'
                    f'<td class="n">{p.get("score", "—")}</td><td>{_pill(ev["status"].capitalize(), colors.get(ev["status"], T3))}</td><td class="n">{_fmt(ev.get("entry"))}</td>'
                    f'<td class="n">{_fmt(ev.get("last"))}</td><td class="n">{rr}</td><td class="n">{ev.get("days", 0)}</td></tr>')
    head = "".join(f"<th>{h}</th>" for h in ("Stock", "Logged", "Side", "Entry zone", "Stop", "Target 1", "Score", "Status", "Filled at", "Last price", "Result", "Sessions"))
    st.markdown(f'<div class="aa-tw"><table class="aa-t"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)
    st.caption("WAITING = price has not reached the entry yet · OPEN = filled, result shown at the last close · a candle that touches stop and target counts as stopped · "
               "results are in R (1R = the distance from entry to stop). Daily candles; costs not included.")
    d1, d2, d3 = st.columns([3, 1.2, 1], gap="small")
    pick = d1.selectbox("Plan", [it["item_id"] for it, _, _ in rows], key="aa_j_pick", label_visibility="collapsed",
                        format_func=lambda i: next(x["name"] for x, _, _ in rows if x["item_id"] == i))
    if d2.button("Open in Analyze", key="aa_j_open", use_container_width=True):
        _set_run(next(p["ticker"] for x, p, _ in rows if x["item_id"] == pick))
        st.rerun()
    sure = d3.checkbox("Confirm", key="aa_j_sure")
    if st.button("Delete this plan", key="aa_j_del", disabled=not sure):
        delete_item(pick)
        st.rerun()


# ── TEACH ───────────────────────────────────────────────────
def _setup_summary_html(s: dict) -> str:
    return (f'<div class="aa-item" style="--c:{AMBER}"><div class="h"><b>{esc(s.get("setup_name", "Unnamed setup"))}</b>'
            f'{_pill("Confidence " + str(s.get("confidence", "?")).lower(), AMBER)}</div><div class="d">{esc(s.get("summary", ""))}<br>'
            f'{len(s.get("conditions", []))} conditions · {len(s.get("confirmation", []))} confirmations · {len(s.get("invalidation", []))} invalidations · '
            f'{len(s.get("zone_definitions", []))} zone definitions · {len(s.get("line_definitions", []))} line definitions · {len(s.get("examples", []))} examples</div></div>')


def teach_pdf():
    st.markdown('<div class="aa-intro">Upload your strategy PDF. Arka reads text, diagrams and marked-up examples, turns them into a structured setup '
                '(rules, trigger, confirmation, invalidation, how you draw supply / demand zones and lines) and shows you what it understood. '
                '<b>You approve it before it is used.</b></div>', unsafe_allow_html=True)
    pdf = st.file_uploader("Strategy PDF", type=["pdf"], key="aa_pdf")
    if pdf and st.button("Read my PDF", type="primary", key="aa_pdf_go"):
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
    with st.expander("What Arka understood (review before approving)", expanded=True):
        st.json(pend)
    if st.button("Approve and activate this setup", type="primary", key="aa_pdf_ok"):
        if save_item("setup", pend.get("setup_name", "PDF setup"), pend, True, pend.get("source_name", ""), st.session_state.get("aa_pending_text", "")):
            st.session_state.pop("aa_pending_setup", None)
            st.success("Setup saved and active. Analyze now checks stocks against it.")
            st.rerun()


def teach_label():
    st.markdown('<div class="aa-intro"><b>The fastest way to teach your zones.</b> Arka detects every supply and demand zone on a chart. '
                'Mark the ones that are <b>your kind of zone</b> and the ones that are not. After a few charts it learns your style: how thick, how fresh, '
                'how strong the move away. No drawing needed.</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([4, 1], gap="small")
    t = c1.text_input("Stock to learn from", key="aa_lab_ticker", placeholder="Choose a chart where you can see both good and bad zones")
    if c2.button("Detect zones", type="primary", key="aa_lab_go", use_container_width=True) and t.strip():
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
        a.markdown(f'<div class="aa-lab"><b>{z["id"]}</b>{_pill(z["type"].capitalize(), col)} {z["pattern"]} · {_fmt(z["bottom"])}–{_fmt(z["top"])} · '
                   f'{z["thickness_atr"]} ATR thick · move away {z["dep_atr"]} ATR · Q{z["quality"]} · {esc(z["status"])}</div>', unsafe_allow_html=True)
        pick = b.radio("Verdict", ["Skip", "✔ Mine", "✘ Not mine"], horizontal=True, key=f"aa_lab_{tk}_{z['id']}", label_visibility="collapsed")
        why = c.text_input("Why (optional)", key=f"aa_why_{tk}_{z['id']}", label_visibility="collapsed", placeholder="Why? e.g. too wide, already tested, no real impulse")
        choices[z["id"]] = (z, pick, why)
    n_ok = sum(1 for v in choices.values() if v[1] == "✔ Mine")
    n_no = sum(1 for v in choices.values() if v[1] == "✘ Not mine")
    if st.button(f"Save my labels ({n_ok} mine · {n_no} not mine)", type="primary", key="aa_lab_save", disabled=(n_ok + n_no) == 0):
        labels = [{"zone_id": zid, "zone_type": z["type"], "approved": pick == "✔ Mine", "reason": why, "features": zone_features(z)}
                  for zid, (z, pick, why) in choices.items() if pick != "Skip"]
        payload = {"ticker": tk, "asof": det["asof"], "labels": labels, "summary": f"{n_ok} approved and {n_no} rejected zones on {tk}"}
        if save_item("feedback", f"{tk} · {n_ok} mine / {n_no} not · {det['asof']}", payload, True):
            st.success("Saved. Open Zone style and press Learn to turn your labels into engine settings.")


def teach_screenshot():
    st.markdown('<div class="aa-intro">Upload charts <b>you</b> marked up (zones, lines, entries, stops, targets) and explain what you did. '
                'Arka studies the drawings and writes down how you read a chart, which it then applies to every stock you analyse.</div>', unsafe_allow_html=True)
    files = st.file_uploader("Marked-up charts (PNG / JPG)", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="aa_imgs")
    why = st.text_area("Explain your markings", height=110, key="aa_img_why",
                       placeholder="e.g. The green boxes are my demand zones: a tight base of 1-3 candles, then a strong move away. I only buy fresh zones in an uptrend. Stop below the zone, first target at the previous swing high.")
    if files and st.button("Learn from these charts", type="primary", key="aa_img_go"):
        with st.spinner("Studying your charts…"):
            st.session_state["aa_pending_lesson"] = learn_from_images([f.getvalue() for f in files[:6]], why)
    pend = st.session_state.get("aa_pending_lesson")
    if not pend:
        return
    if pend.get("error"):
        st.error(pend["error"])
        return
    st.markdown(f'<div class="aa-item" style="--c:{CYAN}"><div class="h"><b>{esc(pend.get("lesson_title", "Lesson"))}</b>{_pill("New lesson", CYAN)}</div>'
                f'<div class="d">{esc(pend.get("summary", ""))}</div></div>', unsafe_allow_html=True)
    with st.expander("What Arka learned (review)", expanded=True):
        st.json(pend)
    if st.button("Save lesson to my playbook", type="primary", key="aa_img_ok"):
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
    if st.button("Save and activate setup", type="primary", key="aa_m_save"):
        if not name.strip() or not conds.strip():
            st.warning("Give the setup a name and at least one rule.")
        elif save_item("setup", name, manual_setup(name, summary, conds, trig, inv), True, "manual"):
            st.success("Setup saved and active.")
            st.rerun()
    _sec("Teach a lesson", "how you define zones and lines, in your words")
    lt = st.text_input("Lesson title", key="aa_l_title", placeholder="How I draw demand zones")
    lx = st.text_area("Lesson", key="aa_l_text", height=130, placeholder="A demand zone starts at the lowest wick of the base and ends at the top of the base bodies. I ignore zones thicker than 2 ATR...")
    if st.button("Save lesson", key="aa_l_save"):
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
    if st.button("Learn my zone style", type="primary", key="aa_learn", disabled=len(ap) < 3):
        params, notes = calibrate_style(ap, rj)
        st.session_state["aa_learned"] = {"params": params, "notes": notes}
    if len(ap) < 3:
        st.caption("Approve at least 3 zones in Label zones first.")
    learned = st.session_state.get("aa_learned")
    if learned and learned["params"]:
        for n in learned["notes"]:
            st.markdown(f"- {n}")
        st.json(learned["params"])
        if st.button("Save as my zone style", type="primary", key="aa_learn_save"):
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
    if b1.button("Save these settings", key="aa_s_save", use_container_width=True):
        if save_item("style", "Manual style · " + datetime.now(IST).strftime("%d %b %Y"), {"params": vals, "summary": "Set by hand"}, True):
            st.success("Saved.")
            st.rerun()
    if b2.button("Reset to Arka defaults", key="aa_s_reset", use_container_width=True):
        for it in active_items("style"):
            set_item_active(it["item_id"], False)
        st.rerun()


TEACH_SECTIONS = {"Import PDF": "teach_pdf", "Label zones": "teach_label", "From screenshot": "teach_screenshot", "Quick rules": "teach_quick",
                  "Zone style": "teach_style"}


def render_teach():
    """Only the section you open runs (st.tabs runs all of them on every click, chart included)."""
    sec = st.radio("Teach", list(TEACH_SECTIONS), horizontal=True, key="aa_teach_sec", label_visibility="collapsed")
    globals()[TEACH_SECTIONS.get(sec, "teach_pdf")]()


# ── PLAYBOOK ────────────────────────────────────────────────
def render_playbook():
    c1, c2 = st.columns([5, 1], gap="small")
    c1.markdown('<div class="aa-intro">Everything you have taught Arka. <b>Active</b> items are used in every analysis; switch items off to test a different approach without deleting anything.</div>', unsafe_allow_html=True)
    if c2.button("Reload", key="aa_reload", use_container_width=True):
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
        _sec({"setup": "Setups", "lesson": "Lessons", "feedback": "Labelled zones", "style": "Zone styles"}[kind], f"{len(group)} saved")
        for it in group:
            p = it["payload"]
            desc = p.get("summary") or p.get("setup_name") or ""
            meta = {"setup": f'{len(p.get("conditions", []))} rules · {len(p.get("zone_definitions", []))} zone definitions',
                    "lesson": f'{len(p.get("zone_definitions", []))} zone definitions · {len(p.get("line_definitions", []))} line definitions',
                    "feedback": f'{sum(1 for x in p.get("labels", []) if x.get("approved"))} mine · {sum(1 for x in p.get("labels", []) if not x.get("approved"))} not mine',
                    "style": ", ".join(f"{k} {v}" for k, v in list(p.get("params", {}).items())[:4])}[kind]
            st.markdown(f'<div class="aa-item" style="--c:{colors[kind] if it["active"] else "#2B2B2B"}"><div class="h"><b>{esc(it["name"])}</b>'
                        f'{_pill("Active" if it["active"] else "Off", GREEN if it["active"] else T3)}</div>'
                        f'<div class="d">{esc(str(desc)[:260])}<br>{esc(meta)} · saved {esc(it["saved_at"][:10])}</div></div>', unsafe_allow_html=True)
            a, b, c = st.columns([1.2, 1.4, 4], gap="small")
            on = a.checkbox("Active", value=it["active"], key=f"aa_act_{it['item_id']}")
            if on != it["active"]:
                set_item_active(it["item_id"], on)
                st.rerun()
            sure = b.checkbox("Confirm delete", key=f"aa_dok_{it['item_id']}")
            if c.button("Delete", key=f"aa_del_{it['item_id']}", disabled=not sure):
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


MODES = ["ANALYZE", "RADAR", "JOURNAL", "TEACH", "PLAYBOOK"]
MODE_LABELS = {"ANALYZE": "Analyze", "RADAR": "Radar", "JOURNAL": "Journal", "TEACH": "Teach", "PLAYBOOK": "Playbook"}


def render_arka_ai(supabase=None):
    global _SB
    _SB = supabase
    _inject_css()
    _consume_autorun()
    st.session_state.setdefault("aa_speed", "Balanced")
    st.markdown(html_status(), unsafe_allow_html=True)
    st.session_state.setdefault("aa_mode", "ANALYZE")
    if st.session_state.get("aa_mode") not in MODES:
        st.session_state["aa_mode"] = "ANALYZE"
    mode = st.radio("Mode", MODES, horizontal=True, key="aa_mode", label_visibility="collapsed", format_func=lambda m: MODE_LABELS[m])
    if mode == "ANALYZE":
        render_analyze()
    elif mode == "RADAR":
        render_radar()
    elif mode == "JOURNAL":
        render_journal()
    elif mode == "TEACH":
        render_teach()
    else:
        render_playbook()
    st.caption("Arka AI is a research tool. Zones and levels are rule-based approximations of discretionary chart reading; scores measure how well the evidence matches "
               "your playbook, not the probability of profit. Not investment advice.")
