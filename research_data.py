"""
research_data.py — extra data for the Arka Research terminal (v1). No UI in this file.

No paid API keys. Sources:
  DELIVERY       NSE "security-wise bhavcopy with delivery" (sec_bhavdata_full_DDMMYYYY.csv), one public file per
                 trading day, every NSE stock. Parsed once per day and kept on disk (.cache/delivery) and, when
                 Supabase is configured, in the table delivery_daily so history builds up and survives restarts.
  ANNOUNCEMENTS  BSE corporate-announcement API for one company (the same API news_feed.py already uses market-wide).
                 The BSE scrip code is read from the company's Screener page.
  AI             Google Gemini REST API with the key in Streamlit secrets (GEMINI_KEY). The model is discovered from
                 the API's own model list, so no model name is hard-coded. Set GEMINI_MODEL in secrets to pin one.
  STORAGE        Supabase REST API (SUPABASE_URL + SUPABASE_KEY in secrets). Tables: see supabase_setup.sql.

Every source reports its health through status(); nothing is estimated when a source fails.
evidence() builds the fact sheet and the contradiction list used by the Research Intelligence screen.
"""
from __future__ import annotations

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 10
CACHE_DIR = Path(".cache") / "delivery"
BHAV_URLS = ("https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv",
             "https://archives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv")
_STATUS: dict = {}
_BLOCKED_UNTIL = {"nse": 0.0}


def _set(name: str, ok: bool, detail: str = ""):
    _STATUS[name] = {"ok": ok, "detail": detail, "at": datetime.now(IST).strftime("%d %b %H:%M")}


def status() -> dict:
    """{source: {ok, detail, at}} for the SRC screen."""
    return dict(_STATUS)


# ═════════════════════════ secrets ═════════════════════════
def _secret(*names):
    try:
        s = st.secrets
        for n in names:
            try:
                if n in s and s[n]:
                    return str(s[n])
            except Exception:
                continue
        for sect in ("supabase", "SUPABASE", "gemini", "GEMINI", "connections"):
            try:
                sub = s[sect]
            except Exception:
                continue
            for n in names:
                for k in (n, n.lower(), n.split("_", 1)[-1].lower(), n.split("_", 1)[-1]):
                    try:
                        if k in sub and sub[k]:
                            return str(sub[k])
                    except Exception:
                        continue
    except Exception:
        pass
    return None


# ═════════════════════════ Supabase (REST, no SDK) ═════════════════════════
def sb_config():
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY", "SUPABASE_ANON_KEY")
    return (url.rstrip("/"), key) if url and key else None


def _sb_headers(key, extra=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def sb_select(table: str, params: dict) -> list:
    cfg = sb_config()
    if not cfg:
        raise RuntimeError("Supabase not configured")
    r = requests.get(f"{cfg[0]}/rest/v1/{table}", params=params, headers=_sb_headers(cfg[1]), timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Supabase {table}: HTTP {r.status_code} {r.text[:120]}")
    return r.json()


def sb_upsert(table: str, rows: list, on_conflict: str) -> int:
    cfg = sb_config()
    if not cfg or not rows:
        return 0
    r = requests.post(f"{cfg[0]}/rest/v1/{table}", params={"on_conflict": on_conflict}, data=json.dumps(rows, default=str),
                      headers=_sb_headers(cfg[1], {"Prefer": "resolution=merge-duplicates,return=minimal"}), timeout=TIMEOUT)
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Supabase {table}: HTTP {r.status_code} {r.text[:120]}")
    return len(rows)


# ═════════════════════════ NSE delivery ═════════════════════════
_BHAV_COLS = {"SYMBOL": "symbol", "SERIES": "series", "DATE1": "date", "PREV_CLOSE": "prev_close", "CLOSE_PRICE": "close",
              "TTL_TRD_QNTY": "volume", "TURNOVER_LACS": "turnover_lacs", "DELIV_QTY": "deliv_qty", "DELIV_PER": "deliv_per"}


def parse_bhavcopy(text: str) -> pd.DataFrame:
    """One day's file -> symbol, series, date, prev_close, close, volume, traded_value (Rs), deliv_qty.
    '-' (no delivery data, e.g. BE series) becomes NaN, never 0. Raises if NSE changes the columns."""
    df = pd.read_csv(io.StringIO(text), dtype=str, skipinitialspace=True)
    df.columns = [c.strip().upper() for c in df.columns]
    miss = [c for c in _BHAV_COLS if c not in df.columns]
    if miss:
        raise ValueError(f"bhavcopy format changed, missing {miss}")
    df = df[list(_BHAV_COLS)].rename(columns=_BHAV_COLS)
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    df = df[df["series"].isin(["EQ", "BE", "BZ", "SM", "ST"])].copy()
    for c in ("prev_close", "close", "volume", "turnover_lacs", "deliv_qty", "deliv_per"):
        df[c] = pd.to_numeric(df[c].where(~df[c].isin(["-", "", "nan"])), errors="coerce")
    df["traded_value"] = df["turnover_lacs"] * 1e5
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce").dt.strftime("%Y-%m-%d")
    return df.drop(columns=["turnover_lacs", "deliv_per"]).dropna(subset=["date"])


def _day_file(d: date) -> Path:
    return CACHE_DIR / f"{d:%Y%m%d}.csv"


def _load_day(d: date):
    """-> ("ok", DataFrame) | ("missing", None) holiday / not published | ("error", message)."""
    f = _day_file(d)
    if f.exists():
        try:
            return "ok", pd.read_csv(f)
        except Exception:
            pass
    if time.time() < _BLOCKED_UNTIL["nse"]:
        return "error", "NSE archive skipped (recent failures)"
    last = ""
    for u in BHAV_URLS:
        try:
            r = requests.get(u.format(d=f"{d:%d%m%Y}"), headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=TIMEOUT)
            if r.status_code == 404:
                return "missing", None
            if r.status_code == 200 and "SYMBOL" in r.text[:200].upper():
                df = parse_bhavcopy(r.text)
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    df.to_csv(f, index=False)
                except Exception:
                    pass
                return "ok", df
            last = f"HTTP {r.status_code}"
        except Exception as exc:
            last = type(exc).__name__
    return "error", last or "unreachable"


def _rows_for(df: pd.DataFrame, symbol: str) -> list:
    x = df[df["symbol"] == symbol]
    if x.empty:
        return []
    x = x.sort_values("series", key=lambda s: s.map(lambda v: 0 if v == "EQ" else 1)).iloc[0]
    g = lambda k: None if pd.isna(x[k]) else float(x[k])
    return [{"symbol": symbol, "date": str(x["date"]), "close": g("close"), "prev_close": g("prev_close"), "volume": g("volume"),
             "traded_value": g("traded_value"), "deliv_qty": g("deliv_qty")}]


def delivery_history(symbol: str, dates: list) -> tuple:
    """Delivery rows for `symbol` on the given trading dates (date objects, oldest first).
    -> (DataFrame indexed by date: close, prev_close, volume, traded_value, deliv_qty, deliv_pct, info dict)."""
    sym = str(symbol).upper().strip()
    dates = sorted(set(dates))
    info = {"requested": len(dates), "from_store": 0, "from_disk_or_nse": 0, "not_published": 0, "errors": 0, "error": ""}
    rows, have = [], set()
    if sb_config() and dates:
        try:
            got = sb_select("delivery_daily", {"select": "*", "symbol": f"eq.{sym}", "date": f"gte.{dates[0]}", "order": "date.asc", "limit": "1000"})
            for r in got:
                if r.get("date"):
                    rows.append(r)
                    have.add(str(r["date"])[:10])
            info["from_store"] = len(rows)
            _set("Supabase", True, "delivery_daily read")
        except Exception as exc:
            _set("Supabase", False, str(exc)[:140])
    todo = [d for d in dates if d.isoformat() not in have]
    new_rows = []

    def take(d, res):
        kind, val = res
        if kind == "ok":
            r = _rows_for(val, sym)
            new_rows.extend(r)
            info["from_disk_or_nse"] += 1 if r else 0
        elif kind == "missing":
            info["not_published"] += 1
        else:
            info["errors"] += 1
            info["error"] = val

    probe = todo[-2:][::-1]                                  # newest two first: if both fail, NSE is not reachable
    for d in probe:
        take(d, _load_day(d))
    rest = [d for d in todo if d not in probe]
    if probe and info["errors"] == len(probe) and not any(_day_file(d).exists() for d in rest):
        _BLOCKED_UNTIL["nse"] = time.time() + 600
        _set("NSE delivery archive", False, f"not reachable from this server ({info['error']})")
        info["errors"] += len(rest)
    elif rest:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for d, res in zip(rest, pool.map(_load_day, rest)):
                take(d, res)
    if new_rows:
        _set("NSE delivery archive", True, f"{len(new_rows)} day(s) loaded")
        try:
            sb_upsert("delivery_daily", new_rows, "symbol,date")
        except Exception as exc:
            _set("Supabase", False, str(exc)[:140])
    rows += new_rows
    if not rows:
        return pd.DataFrame(), info
    df = pd.DataFrame(rows).drop_duplicates("date", keep="last")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for c in ("close", "prev_close", "volume", "traded_value", "deliv_qty"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["deliv_pct"] = df["deliv_qty"] / df["volume"].where(df["volume"] > 0) * 100
    return df[["close", "prev_close", "volume", "traded_value", "deliv_qty", "deliv_pct"]], info


def aggregate_delivery(df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """freq D / W (Sunday-Saturday weeks) / M. Delivery % = delivered quantity / traded quantity over the period.
    Days without delivery data are left out of both sums."""
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    ok = x["deliv_qty"].notna() & x["volume"].notna()
    x["dq"], x["vq"] = x["deliv_qty"].where(ok), x["volume"].where(ok)
    if freq == "D":
        out = pd.DataFrame({"label": x.index.strftime("%d %b %Y"), "deliv_qty": x["dq"], "volume": x["volume"], "close": x["close"]})
        out["deliv_pct"] = x["deliv_pct"]
    else:
        per = x.index.to_period("W-SAT" if freq == "W" else "M")
        g = x.groupby(per)
        days = pd.Series(x.index, index=x.index)
        out = pd.DataFrame({"deliv_qty": g["dq"].sum(min_count=1), "volume": g["volume"].sum(min_count=1), "close": g["close"].last(),
                            "vq": g["vq"].sum(min_count=1), "first": days.groupby(per).min(), "last": days.groupby(per).max()})
        out["deliv_pct"] = out["deliv_qty"] / out["vq"].where(out["vq"] > 0) * 100
        out["label"] = ([f"{a:%d %b} - {b:%d %b}" for a, b in zip(out["first"], out["last"])] if freq == "W"
                        else [p.strftime("%b %Y") for p in out.index])
        out = out.drop(columns=["vq", "first", "last"])
    out["chg_pct"] = (out["close"] / out["close"].shift(1) - 1) * 100
    if freq == "D" and "prev_close" in df:
        out["chg_pct"] = ((df["close"] / df["prev_close"].where(df["prev_close"] > 0) - 1) * 100).values
    return out.reset_index(drop=True)


# ═════════════════════════ BSE announcements ═════════════════════════
def bse_code_from_html(html: str):
    for pat in (r"bseindia\.com/[^\"'<> ]*?/(\d{6})/?[\"']", r"BSE:\s*(?:</?[a-z][^>]*>\s*)*(\d{6})"):
        m = re.search(pat, html or "", re.I)
        if m:
            return m.group(1)
    return None


@st.cache_data(ttl=7 * 86400, show_spinner=False, max_entries=512)
def _bse_code_cached(url: str):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=TIMEOUT)
    code = bse_code_from_html(r.text) if r.status_code == 200 else None
    if not code:
        raise RuntimeError("BSE code not found on the Screener page")
    return code


def bse_code(screener_url: str):
    try:
        return _bse_code_cached(screener_url) if screener_url else None
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False, max_entries=128)
def _bse_ann_cached(code: str, d_from: str, d_to: str) -> list:
    out = []
    for page in (1, 2, 3):
        r = requests.get("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
                         params={"pageno": page, "strCat": -1, "strPrevDate": d_from, "strScrip": code, "strSearch": "P",
                                 "strToDate": d_to, "strType": "C", "subcategory": -1},
                         headers={"User-Agent": UA, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
                                  "Accept": "application/json, text/plain, */*"}, timeout=TIMEOUT)
        r.raise_for_status()
        rows = (r.json() or {}).get("Table") or []
        for x in rows:
            head = str(x.get("HEADLINE") or x.get("NEWSSUB") or "").strip()
            if not head:
                continue
            att = str(x.get("ATTACHMENTNAME") or "").strip()
            out.append({"dt": str(x.get("DT_TM") or x.get("NEWS_DT") or "")[:16].replace("T", " "), "title": head[:300],
                        "category": str(x.get("CATEGORYNAME") or "").strip() or "Other", "sub": str(x.get("SUBCATNAME") or "").strip(),
                        "url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else "https://www.bseindia.com/corporates/ann.html"})
        if len(rows) < 40:
            break
    return out


def bse_announcements(code: str, days: int = 120) -> list:
    now = datetime.now(IST)
    try:
        rows = _bse_ann_cached(str(code), (now - timedelta(days=days)).strftime("%Y%m%d"), now.strftime("%Y%m%d"))
        _set("BSE announcements", True, f"{len(rows)} filing(s)")
        return rows
    except Exception as exc:
        _set("BSE announcements", False, f"{type(exc).__name__}: {exc}"[:140])
        return []


# ═════════════════════════ Gemini ═════════════════════════
GEM = "https://generativelanguage.googleapis.com/v1beta"
_SKIP = ("embedding", "tts", "image", "live", "audio", "vision", "aqa", "learnlm", "gemma", "robotics", "computer")


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=2)
def _gemini_models(key_tail: str) -> list:
    key = _secret("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    r = requests.get(f"{GEM}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=TIMEOUT)
    r.raise_for_status()
    names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
             if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    names = [n for n in names if n.startswith("gemini") and not any(s in n for s in _SKIP)]
    stable = [n for n in names if "preview" not in n and "exp" not in n] or names
    rank = lambda n: (0 if "flash" in n and "lite" not in n else 1 if "flash" in n else 2, [-int(x) for x in re.findall(r"\d+", n)[:2]] or [0])
    out = sorted(stable, key=rank)
    if not out:
        raise RuntimeError("no text model available for this key")
    return out


def gemini_generate(prompt: str, system: str = "") -> dict:
    """-> {"text", "model", "error"}. Never raises."""
    key = _secret("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return {"text": None, "model": None, "error": "GEMINI_KEY not found in Streamlit secrets"}
    pinned = _secret("GEMINI_MODEL")
    try:
        models = [pinned] if pinned else _gemini_models(key[-6:])
    except Exception as exc:
        _set("Gemini", False, f"model list: {exc}"[:140])
        return {"text": None, "model": None, "error": f"Could not list Gemini models: {type(exc).__name__}: {exc}"[:200]}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    last = ""
    for m in models[:3]:
        try:
            r = requests.post(f"{GEM}/models/{m}:generateContent", headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              data=json.dumps(body), timeout=60)
            if r.status_code != 200:
                last = f"{m}: HTTP {r.status_code} {r.text[:120]}"
                continue
            parts = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                _set("Gemini", True, m)
                return {"text": text, "model": m, "error": None}
            last = f"{m}: empty answer"
        except Exception as exc:
            last = f"{m}: {type(exc).__name__}"
    _set("Gemini", False, last[:140])
    return {"text": None, "model": None, "error": last or "Gemini request failed"}


# ═════════════════════════ thesis notes ═════════════════════════
def note_load(user: str, symbol: str):
    rows = sb_select("research_notes", {"select": "payload,updated_at", "user_id": f"eq.{user}", "symbol": f"eq.{symbol}", "limit": "1"})
    return rows[0] if rows else None


def note_save(user: str, symbol: str, payload: dict) -> None:
    sb_upsert("research_notes", [{"user_id": user, "symbol": symbol, "payload": payload,
                                  "updated_at": datetime.now(timezone.utc).isoformat()}], "user_id,symbol")


# ═════════════════════════ evidence + contradictions (pure) ═════════════════════════
def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    s = str(v).replace(",", "").replace("%", "").replace("₹", "").replace("Cr", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _vals(rec, *needles):
    for n in needles:
        for r in (rec or {}).get("rows", []):
            if r["label"].lower().startswith(n.lower()):
                return [_num(v) for v in r["values"]]
    for n in needles:
        for r in (rec or {}).get("rows", []):
            if n.lower() in r["label"].lower():
                return [_num(v) for v in r["values"]]
    return []


def _ttm_pair(vals):
    """(sum of last 4 quarters, sum of the 4 before) or (None, None)."""
    v = [x for x in vals]
    if len(v) >= 8 and all(x is not None for x in v[-8:]):
        return sum(v[-4:]), sum(v[-8:-4])
    return None, None


def evidence(B: dict, close: pd.Series | None, pe_now=None, pe_pctile=None, dlv: pd.DataFrame | None = None) -> dict:
    """Fact sheet + contradictions from data already loaded. Every fact carries its source.
    -> {"facts": [{"label","value","source"}], "contradictions": [{"name","numbers"}], "unknown": [str]}"""
    facts, con, unk = [], [], []
    add = lambda label, value, source: facts.append({"label": label, "value": value, "source": source})
    q, y, sh, cf = B.get("quarterly"), B.get("yearly"), B.get("shareholding"), B.get("cash_flow")

    ret1y = ret20 = None
    if close is not None and len(close) > 21:
        c = close.dropna()
        ret20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
        add("Price change, 20 sessions", f"{ret20:+.1f}%", "Yahoo")
        if len(c) > 252:
            ret1y = (c.iloc[-1] / c.iloc[-253] - 1) * 100
            add("Price change, 1 year", f"{ret1y:+.1f}%", "Yahoo")
    else:
        unk.append("price history")

    s_now, s_prev = _ttm_pair(_vals(q, "Sales", "Revenue"))
    p_now, p_prev = _ttm_pair(_vals(q, "Net Profit", "Profit for the period"))
    sales_g = (s_now / s_prev - 1) * 100 if s_now is not None and s_prev and s_prev > 0 else None
    prof_g = (p_now / p_prev - 1) * 100 if p_now is not None and p_prev and p_prev > 0 else None
    if sales_g is not None:
        add("Sales, last 4 quarters vs previous 4", f"{sales_g:+.1f}%", "Screener")
    if prof_g is not None:
        add("Net profit, last 4 quarters vs previous 4", f"{prof_g:+.1f}%", "Screener")
    elif p_now is not None and p_prev is not None:
        add("Net profit, last 4 quarters vs previous 4", f"₹{p_prev:,.0f} Cr → ₹{p_now:,.0f} Cr", "Screener")
    if sales_g is None and prof_g is None:
        unk.append("8 quarters of results")

    opm = [v for v in _vals(q, "OPM", "Financing Margin") if v is not None]
    opm_d = opm[-1] - opm[-5] if len(opm) >= 5 else None
    if opm_d is not None:
        add("Operating margin, latest quarter vs a year ago", f"{opm[-5]:.0f}% → {opm[-1]:.0f}% ({opm_d:+.1f} pp)", "Screener")

    if pe_now:
        add("P/E (TTM)", f"{pe_now:.1f}x" + (f", {pe_pctile:.0f}th percentile of its own history" if pe_pctile is not None else ""),
            "Screener EPS + Yahoo price")
    else:
        unk.append("P/E (needs positive TTM EPS)")

    pr = [v for v in _vals(sh, "Promoters") if v is not None]
    prom_d = pr[-1] - pr[-5] if len(pr) >= 5 else None
    if pr:
        add("Promoter holding", f"{pr[-1]:.2f}%" + (f" ({prom_d:+.2f} pp in 4 quarters)" if prom_d is not None else ""), "Screener")
    else:
        unk.append("shareholding pattern")
    for lab, nd in (("FII holding", "FII"), ("DII holding", "DII")):
        v = [x for x in _vals(sh, nd) if x is not None]
        if v:
            add(lab, f"{v[-1]:.2f}%" + (f" ({v[-1] - v[-5]:+.2f} pp in 4 quarters)" if len(v) >= 5 else ""), "Screener")

    npy = {p: v for p, v in zip((y or {}).get("periods", []), _vals(y, "Net Profit", "Profit for the period")) if v is not None and "TTM" not in str(p).upper()}
    cfo = {p: v for p, v in zip((cf or {}).get("periods", []), _vals(cf, "Cash from Operating", "Operating")) if v is not None}
    common = [p for p in npy if p in cfo][-3:]
    conv = None
    if len(common) == 3 and sum(npy[p] for p in common) > 0:
        conv = sum(cfo[p] for p in common) / sum(npy[p] for p in common) * 100
        add("Operating cash flow as % of net profit, 3 years", f"{conv:.0f}%", "Screener")
    else:
        unk.append("3 years of cash flow")

    dl_now = dl_base = None
    if dlv is not None and len(dlv) >= 40 and dlv["deliv_pct"].notna().sum() >= 40:
        d = dlv["deliv_pct"].dropna()
        dl_now, dl_base = float(d.tail(10).mean()), float(d.iloc[:-10].mean())
        add("Delivery share of traded quantity, last 10 sessions vs before", f"{dl_base:.0f}% → {dl_now:.0f}%", "NSE bhavcopy")
    else:
        unk.append("delivery data")

    c_ = lambda name, numbers: con.append({"name": name, "numbers": numbers})
    if pe_pctile is not None and pe_pctile >= 80 and prof_g is not None and prof_g < 0:
        c_("Valuation high, profit falling", f"P/E at the {pe_pctile:.0f}th percentile of its own history while 4-quarter profit is {prof_g:+.1f}%")
    if ret1y is not None and ret1y > 20 and prof_g is not None and prof_g < 0:
        c_("Price up, profit down", f"price {ret1y:+.1f}% in a year while 4-quarter profit is {prof_g:+.1f}%")
    if ret1y is not None and ret1y < -20 and prof_g is not None and prof_g > 15:
        c_("Price down, profit up", f"price {ret1y:+.1f}% in a year while 4-quarter profit is {prof_g:+.1f}%")
    if sales_g is not None and sales_g > 10 and opm_d is not None and opm_d <= -2:
        c_("Sales growing, margin shrinking", f"sales {sales_g:+.1f}% while operating margin moved {opm_d:+.1f} pp")
    if conv is not None and conv < 50 and prof_g is not None and prof_g > 0:
        c_("Profit growing, cash not following", f"operating cash flow is {conv:.0f}% of profit over 3 years while profit is {prof_g:+.1f}%")
    if prom_d is not None and prom_d <= -1 and ret1y is not None and ret1y > 0:
        c_("Price up, promoter holding down", f"promoters {prom_d:+.2f} pp in 4 quarters while price is {ret1y:+.1f}% in a year")
    if dl_now is not None and ret20 is not None and ret20 < -5 and dl_now >= dl_base * 1.25:
        c_("Price falling, delivery share rising", f"price {ret20:+.1f}% in 20 sessions while delivery share rose from {dl_base:.0f}% to {dl_now:.0f}%")
    unk += ["analyst estimate revisions", "who bought or sold", "management guidance"]
    return {"facts": facts, "contradictions": con, "unknown": unk}
