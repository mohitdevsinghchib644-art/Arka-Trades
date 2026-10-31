"""
portfolio_page.py — Arka Trades Portfolio v3 (professional edition)

What changed vs v2
------------------
FIXES
  * Splits / bonus issues: Yahoo prices are split-adjusted but your buy price was not, so a 1:1 bonus showed a
    fake −50 %. Every lot (and every sell) is now adjusted automatically with the stock's split history.
  * REFRESH really refreshes (it called get_quotes.clear(), which no longer exists, and silently did nothing).
  * Lag: st.tabs built all five tabs — every chart — on each click, and each holding had two buttons.
    Views are now a selector (only the open view is built) and holdings are one table with an action bar.
  * "Remove holding" in one book no longer deletes the same stock in your other books.

NEW
  * SELLS + REALISED P&L — FIFO matching against your oldest lots, realised vs unrealised, per lot.
  * TAX — STCG / LTCG by Indian financial year with set-off rules, ₹1.25 L LTCG exemption, cess; editable rates
    (defaults: STCG 20 %, LTCG 12.5 % — unchanged in Budget 2026). Lots about to turn long-term, tax-free LTCG
    headroom left this year.
  * STOP-LOSS / TARGET per holding — ₹ at risk, R-multiple, reward:risk, portfolio heat, SL-hit flags.
  * MULTIPLE BOOKS (e.g. Long-term · Trading · Family) — filter everything by book; move lots between books.
  * PERFORMANCE — true time-weighted return with sells, benchmark with identical cash flows (NIFTY 50 / 500 /
    MIDCAP 100), monthly returns heatmap, contribution by stock and sector, saved daily history.
  * RISK & STRESS — per-stock beta, market and sector shock scenarios, the worst historical NIFTY day / month
    replayed on today's portfolio.
  * REBALANCE assistant — max-per-stock / max-per-sector rules → exact quantities, with the tax impact of each trim.
  * INCOME & EVENTS — estimated dividends received (from Yahoo dividend history and your holding dates),
    yield on cost, upcoming results / ex-dividend dates.
  * LINKS — sector rotation stage from Money Flow, news tone per holding from the News Desk, Piotroski-style
    quality score from Research.
  * MONTHLY REVIEW — a printable HTML report.

Storage: buy lots stay in your existing `portfolio` table (user_data.py). Sells, books, stop-loss / targets,
settings and daily snapshots use five small new tables — the page shows the SQL once. Until they exist, those
features still work for the current session.

Informational only — not investment or tax advice. P&L excludes brokerage, STT and other charges.
"""
from __future__ import annotations

import html
import json
import math
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st

import user_data as ud
import data_engine as de
from data_engine import get_quotes

try:
    from data_engine import warm_security
except Exception:  # pragma: no cover
    warm_security = None
try:
    from data_engine import yf_attr
except Exception:  # pragma: no cover
    yf_attr = None
try:
    from news_feed import _fetch_news_for_stock
except Exception:  # pragma: no cover
    _fetch_news_for_stock = None
try:
    from screener_scraper import resolve_symbol
except Exception:  # pragma: no cover
    resolve_symbol = None
try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None
try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
except Exception:  # pragma: no cover
    add_script_run_ctx = get_script_run_ctx = None


# ═════════════════════════ theme / constants ═════════════════════════
AMBER, GREEN, RED, CYAN, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#FFD60A"
PURPLE, PINK, INDIGO = "#BF5AF2", "#FF6482", "#5E8CFF"
DARK, DARK2, DARK3, BORDER = "#000000", "#0A0A0A", "#111111", "#262626"
IVORY, T2, T3 = "#E8E8E8", "#8A8A8A", "#5A5A5A"
MONO = "'JetBrains Mono',monospace"
IST = timezone(timedelta(hours=5, minutes=30))
PALETTE = ["#FF9F0A", "#5AC8FA", "#BF5AF2", "#30D158", "#FF6482", "#5E8CFF",
           "#FFD60A", "#40C8E0", "#AC8E68", "#FF7A45", "#7D7AFF", "#8E8E93"]
UNCLASSIFIED, UNCLASSIFIED_COLOR = "Unclassified", "#6E6E73"
LARGE_CAP_CR, MID_CAP_CR = 100_000, 30_000          # approximate AMFI buckets (₹ crore)
DEFAULT_BOOK = "Main"
TAX_DEFAULTS = {"stcg": 20.0, "ltcg": 12.5, "exempt": 125000.0, "cess": 4.0}
REB_DEFAULTS = {"max_stock": 15.0, "max_sector": 30.0, "redeploy": True, "cash": 0.0}
BENCHMARKS = {"NIFTY 50": ("^NSEI",), "NIFTY 500": ("^CRSLDX",), "NIFTY MIDCAP 100": ("^CRSMID", "NIFTY_MIDCAP_100.NS")}
STAGE_COLORS = {"ROTATING IN": GREEN, "LEADING": "#8AD66B", "EXTENDED": YELLOW, "IMPROVING": CYAN, "FADING": AMBER, "WEAK": RED}
VIEWS = ["DASHBOARD", "HOLDINGS", "ANALYSIS", "PERFORMANCE", "RISK & STRESS", "REBALANCE", "INCOME & EVENTS",
         "TAX & REALISED", "TRANSACTIONS", "NEWS"]

SQL_EXTRA = """-- Arka Portfolio v3 · run once in Supabase → SQL Editor
create table if not exists portfolio_lot_meta (
  lot_id text primary key, book text not null default 'Main', updated_at timestamptz default now());
create table if not exists portfolio_plan (
  book text not null default 'Main', symbol text not null, stop_loss numeric, target numeric,
  updated_at timestamptz default now(), primary key (book, symbol));
create table if not exists portfolio_sells (
  id bigserial primary key, book text not null default 'Main', symbol text not null,
  quantity numeric not null check (quantity > 0), sell_price numeric not null check (sell_price > 0),
  sell_date date not null, note text default '', created_at timestamptz default now());
create table if not exists portfolio_snapshots (
  snap_date date not null, book text not null, invested numeric, value numeric, realised numeric,
  dividends numeric, primary key (snap_date, book));
create table if not exists portfolio_settings (
  key text primary key, value jsonb, updated_at timestamptz default now());
-- If Row Level Security is ON for your existing `portfolio` table, copy its policies to these five tables."""

_CSS = """
<style>
:root{--pf-amber:#FF9F0A;--pf-green:#30D158;--pf-red:#FF453A;--pf-cyan:#5AC8FA;--pf-yellow:#FFD60A;
      --pf-border:#262626;--pf-panel:#0A0A0A;--pf-ivory:#E8E8E8;--pf-t2:#8A8A8A;--pf-t3:#5A5A5A;}
.pf-hero{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;padding:14px 16px;background:linear-gradient(180deg,#0e0e0e,#080808);border:1px solid var(--pf-border);border-top:2px solid var(--pf-amber);margin-bottom:8px;}
.pf-hero .k{font:700 8px 'JetBrains Mono',monospace;letter-spacing:1.5px;color:var(--pf-t3);}
.pf-hero .t{font:800 24px 'JetBrains Mono',monospace;color:var(--pf-ivory);margin-top:3px;letter-spacing:-.3px;}
.pf-hero .d{font:700 12px 'JetBrains Mono',monospace;margin-top:3px;}
.pf-hero .m{font:10px 'JetBrains Mono',monospace;color:var(--pf-t2);text-align:right;line-height:1.7;}
.pf-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:6px 0 10px;}
.pf-kpi{border:1px solid var(--pf-border);border-top:2px solid var(--a,#444);background:var(--pf-panel);padding:11px 13px;min-height:80px;min-width:0;}
.pf-kpi .l{font:700 8px 'JetBrains Mono',monospace;color:var(--pf-t3);letter-spacing:1px;text-transform:uppercase;}
.pf-kpi .v{font:700 18px 'JetBrains Mono',monospace;color:var(--pf-ivory);margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pf-kpi .s{font:10px 'JetBrains Mono',monospace;margin-top:4px;color:var(--pf-t2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pf-ph{display:flex;align-items:baseline;gap:10px;border-bottom:1px solid var(--pf-border);padding:14px 0 7px;margin:6px 0 10px;}
.pf-ph:before{content:"";width:3px;height:11px;background:var(--pf-amber);display:inline-block;align-self:center;}
.pf-ph-t{font:700 10px 'JetBrains Mono',monospace;letter-spacing:1.4px;color:var(--pf-amber);text-transform:uppercase;}
.pf-ph-s{font:10px 'Inter',sans-serif;color:var(--pf-t3);}
.pf-note{font:10.5px 'JetBrains Mono',monospace;color:var(--pf-t2);border-left:2px solid #444;padding:7px 11px;background:rgba(255,255,255,.02);margin:8px 0;line-height:1.7;}
.pf-note b{color:var(--pf-ivory);}
.pf-empty{border:1px dashed #333;background:#080808;padding:34px 24px;text-align:center;margin:14px 0;}
.pf-empty .a{font:800 18px 'Inter',sans-serif;color:var(--pf-ivory);} .pf-empty .b{font:11px 'Inter',sans-serif;color:var(--pf-t2);margin-top:8px;line-height:1.7;}
.pf-flag{display:flex;gap:10px;padding:9px 11px;border:1px solid var(--pf-border);border-left:3px solid var(--fc,#444);background:var(--pf-panel);margin-bottom:6px;}
.pf-flag .lv{font:800 8px 'JetBrains Mono',monospace;letter-spacing:1px;color:var(--fc,#888);min-width:44px;padding-top:2px;}
.pf-flag .ft{font:700 12px 'Inter',sans-serif;color:var(--pf-ivory);} .pf-flag .fd{font:11px 'Inter',sans-serif;color:var(--pf-t2);margin-top:2px;line-height:1.55;}
.pf-score{font:800 9px 'JetBrains Mono',monospace;letter-spacing:1.4px;text-align:center;margin-top:-6px;}
.pf-tw{overflow:auto;border:1px solid var(--pf-border);background:#080808;max-height:640px;}
.pf-tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:11px;}
.pf-tbl th{position:sticky;top:0;z-index:2;background:#0e0e0e;font:700 8px 'JetBrains Mono',monospace;letter-spacing:.9px;color:var(--pf-t2);text-align:left;padding:8px 7px;border-bottom:1px solid var(--pf-border);text-transform:uppercase;white-space:nowrap;}
.pf-tbl td{padding:8px 7px;border-bottom:1px solid #161616;color:var(--pf-ivory);vertical-align:middle;white-space:nowrap;}
.pf-tbl tr:hover td{background:rgba(255,159,10,.035);}
.pf-tbl th.r,.pf-tbl td.r{text-align:right;font-family:'JetBrains Mono',monospace;}
.pf-tbl td .sub{font-size:9px;color:var(--pf-t3);margin-top:2px;white-space:nowrap;}
.pf-sym{font:800 12px 'JetBrains Mono',monospace;color:var(--pf-amber);}
.pf-chip{display:inline-block;font:700 8px 'JetBrains Mono',monospace;letter-spacing:.5px;padding:1px 6px;margin:2px 3px 0 0;border:1px solid var(--c,#444);color:var(--c,#aaa);background:rgba(255,255,255,.02);text-transform:uppercase;}
.pf-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--pf-yellow);margin-left:6px;vertical-align:middle;box-shadow:0 0 6px var(--pf-yellow);animation:pfpulse 1.8s ease-in-out infinite;cursor:help;}
@keyframes pfpulse{0%,100%{opacity:1}50%{opacity:.35}}
.pf-sw{display:inline-block;width:8px;height:8px;margin-right:7px;vertical-align:middle;}
.pf-wbar{display:inline-block;width:56px;height:5px;background:#1a1a1a;vertical-align:middle;margin-right:6px;} .pf-wbar i{display:block;height:100%;}
.pf-rng{position:relative;display:inline-block;width:64px;height:4px;background:linear-gradient(90deg,#FF453A55,#333,#30D15855);vertical-align:middle;}
.pf-rng i{position:absolute;top:-3px;width:3px;height:10px;background:#FF9F0A;transform:translateX(-1px);}
.pf-mini{font:10px 'JetBrains Mono',monospace;color:var(--pf-t2);}
.pf-mover{display:flex;justify-content:space-between;padding:7px 2px;border-bottom:1px solid #161616;font:11px 'JetBrains Mono',monospace;}
.pf-mover b{color:var(--pf-amber);}
.pf-pill{display:inline-block;padding:1px 7px;border-radius:999px;font:800 8px 'JetBrains Mono',monospace;letter-spacing:.6px;border:1px solid currentColor;}
div[class*="st-key-pf_act"]{border:1px solid var(--pf-border);border-top:2px solid var(--pf-amber);background:#0a0a0a;padding:10px 12px 4px;margin-top:8px;}
div[class*="st-key-pf_views"] [role="radiogroup"]{gap:4px !important;}
</style>
"""


# ═════════════════════════ formatting helpers ═════════════════════════
def _f(x):
    if x is None:
        return None
    if isinstance(x, (int, float, np.integer, np.floating)):
        v = float(x)
    else:
        try:
            v = float(re.sub(r"[₹,%\s]|Cr\.?", "", str(x)))
        except ValueError:
            return None
    return v if math.isfinite(v) else None


def _esc(s, quote=False):
    return html.escape("" if s is None else str(s), quote=quote)


def _inr(x, d=2):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    neg = x < 0
    ip, _, fp = f"{abs(x):.{d}f}".partition(".")
    if len(ip) > 3:
        ip = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", ip[:-3]) + "," + ip[-3:]
    out = "₹" + ip + (("." + fp) if d else "")
    return ("-" + out) if neg else out


def _short(x):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    a, sign = abs(x), ("-" if x < 0 else "")
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return f"{sign}₹{a:,.0f}"


def _pct(x, d=2, sign=True):
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%"


def _num(x, d=1, suffix=""):
    return "—" if x is None else f"{x:,.{d}f}{suffix}"


def _qty(x):
    return "—" if x is None else (f"{x:,.0f}" if abs(x - round(x)) < 1e-6 else f"{x:,.3f}".rstrip("0").rstrip("."))


def _col(v):
    return GREEN if (v or 0) >= 0 else RED


def _kpi(label, value, sub="", vcolor=IVORY, scolor=T2, accent=AMBER):
    return (f'<div class="pf-kpi" style="--a:{accent}"><div class="l">{_esc(label)}</div>'
            f'<div class="v" style="color:{vcolor}">{value}</div><div class="s" style="color:{scolor}">{sub}</div></div>')


def _kpis(cards):
    st.markdown('<div class="pf-kpis">' + "".join(cards) + '</div>', unsafe_allow_html=True)


def _ph(title, sub=""):
    s = f'<span class="pf-ph-s">{_esc(sub)}</span>' if sub else ""
    st.markdown(f'<div class="pf-ph"><span class="pf-ph-t">{_esc(title)}</span>{s}</div>', unsafe_allow_html=True)


def _note(text):
    st.markdown(f'<div class="pf-note">{text}</div>', unsafe_allow_html=True)


def _d(x):
    """'2024-05-17' / date / datetime -> date | None."""
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def _fy(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y}-{(y + 1) % 100:02d}"


def _today() -> date:
    return datetime.now(IST).date()


# ═════════════════════════ storage for the new features ═════════════════════════
_KEYS = {"portfolio_lot_meta": ("lot_id",), "portfolio_plan": ("book", "symbol"), "portfolio_snapshots": ("snap_date", "book"),
         "portfolio_settings": ("key",), "portfolio_sells": ("id",)}


def _is_missing(e) -> bool:
    s = str(e).lower()
    try:
        if ud.table_missing(str(e)):
            return True
    except Exception:
        pass
    return "does not exist" in s or "pgrst205" in s or "could not find the table" in s or "42p01" in s


class Store:
    """Supabase tables for sells / books / plans / snapshots / settings, with a session fallback when a table
    is missing (features keep working for this session, and the page shows the SQL once)."""

    def __init__(self, sb):
        self.sb = sb
        self.missing = set(st.session_state.get("_pfx_missing", set()))
        self.errors = []

    def _session(self, t):
        return st.session_state.setdefault(f"_pfx_{t}", [])

    def _remote(self, t):
        return self.sb is not None and t not in self.missing

    def select(self, t):
        if self._remote(t):
            try:
                return self.sb.table(t).select("*").execute().data or []
            except Exception as e:
                if _is_missing(e):
                    self.missing.add(t)
                    st.session_state["_pfx_missing"] = set(self.missing)
                else:
                    self.errors.append(f"{t}: {e}")
        return list(self._session(t))

    def upsert(self, t, row):
        keys = _KEYS[t]
        if self._remote(t):
            try:
                self.sb.table(t).upsert(row, on_conflict=",".join(keys)).execute()
                return None
            except Exception as e:
                if not _is_missing(e):
                    return str(e)
                self.missing.add(t)
                st.session_state["_pfx_missing"] = set(self.missing)
        rows = self._session(t)
        rows[:] = [r for r in rows if tuple(str(r.get(k)) for k in keys) != tuple(str(row.get(k)) for k in keys)] + [row]
        return None

    def insert(self, t, row):
        if self._remote(t):
            try:
                self.sb.table(t).insert(row).execute()
                return None
            except Exception as e:
                if not _is_missing(e):
                    return str(e)
                self.missing.add(t)
                st.session_state["_pfx_missing"] = set(self.missing)
        rows = self._session(t)
        row = dict(row, id=max([int(r.get("id", 0)) for r in rows] + [0]) + 1)
        rows.append(row)
        return None

    def delete(self, t, **eq):
        if self._remote(t):
            try:
                q = self.sb.table(t).delete()
                for k, v in eq.items():
                    q = q.eq(k, v)
                q.execute()
                return None
            except Exception as e:
                if not _is_missing(e):
                    return str(e)
        rows = self._session(t)
        rows[:] = [r for r in rows if not all(str(r.get(k)) == str(v) for k, v in eq.items())]
        return None

    def load(self):
        meta = {str(r["lot_id"]): (r.get("book") or DEFAULT_BOOK) for r in self.select("portfolio_lot_meta") if r.get("lot_id") is not None}
        plans = {(r.get("book") or DEFAULT_BOOK, str(r["symbol"]).upper()): {"sl": _f(r.get("stop_loss")), "target": _f(r.get("target"))}
                 for r in self.select("portfolio_plan") if r.get("symbol")}
        sells = [{"id": r.get("id"), "book": r.get("book") or DEFAULT_BOOK, "symbol": str(r.get("symbol", "")).upper(),
                  "quantity": _f(r.get("quantity")) or 0.0, "sell_price": _f(r.get("sell_price")) or 0.0,
                  "sell_date": str(r.get("sell_date") or "")[:10], "note": r.get("note") or ""} for r in self.select("portfolio_sells")]
        snaps = self.select("portfolio_snapshots")
        settings = {}
        for r in self.select("portfolio_settings"):
            v = r.get("value")
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            settings[r.get("key")] = v
        return {"meta": meta, "plans": plans, "sells": [s for s in sells if s["symbol"] and s["quantity"] > 0], "snaps": snaps,
                "settings": settings}


# ═════════════════════════ data loading ═════════════════════════
def _pool(n):
    ctx = get_script_run_ctx() if get_script_run_ctx else None

    def _init():
        if ctx is not None and add_script_run_ctx is not None:
            try:
                add_script_run_ctx(threading.current_thread(), ctx)
            except Exception:
                pass
    return ThreadPoolExecutor(max_workers=max(1, n), initializer=_init)


def _norm_series(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index().astype("float64")


def _clean_close(hist):
    try:
        if hist is None or len(hist) < 5 or "Close" not in hist:
            return None
        s = _norm_series(hist["Close"])
        return s if len(s) >= 5 else None
    except Exception:
        return None


def _events_list(series):
    """Yahoo splits / dividends Series -> [(date, value)] sorted."""
    if series is None or not hasattr(series, "items") or len(series) == 0:
        return []
    out = []
    for k, v in series.items():
        try:
            d = pd.Timestamp(k)
            d = (d.tz_convert(None) if d.tzinfo else d).date()
            fv = float(v)
            if fv > 0:
                out.append((d, fv))
        except Exception:
            continue
    return sorted(out)


def _fscore(B):
    try:
        from research_page import _piotroski
    except Exception:
        return None
    try:
        res = _piotroski(B)
    except Exception:
        return None
    if not res:
        return None
    tests = res[0]
    avail = [t for t in tests if t[1] is not None]
    return (sum(1 for t in avail if t[1]), len(avail)) if avail else None


def _fetch_info(sym):
    """Sector, fundamentals, price history, split + dividend history, quality score for one symbol."""
    B, hist = ({}, None)
    if warm_security is not None:
        try:
            B, hist = warm_security(sym)
        except Exception:
            B, hist = {}, None
    B = B if isinstance(B, dict) else {}
    sec = B.get("sector") if isinstance(B.get("sector"), dict) else {}
    summ = B.get("summary") if isinstance(B.get("summary"), dict) else {}
    close = _clean_close(hist)
    hi = lo = None
    try:
        if hist is not None and len(hist) >= 2:
            tail = hist.tail(252)
            hi, lo = _f(tail["High"].max()), _f(tail["Low"].min())
    except Exception:
        pass
    if hi is None or lo is None:
        hi, lo = _f(summ.get("year_high")), _f(summ.get("year_low"))
    splits = _events_list(yf_attr(sym, "splits")) if yf_attr else []
    divs = _events_list(yf_attr(sym, "dividends")) if yf_attr else []
    sector = str(sec.get("Sector") or sec.get("Broad Sector") or "").strip()
    if not sector and close is None and not summ:
        return {"partial": True, "splits": splits, "divs": divs, "close": None}
    return {
        "sector": sector or UNCLASSIFIED, "broad": str(sec.get("Broad Sector") or "").strip(),
        "industry": str(sec.get("Industry") or sec.get("Broad Industry") or "").strip() or UNCLASSIFIED,
        "mcap": _f(summ.get("market_cap")), "pe": _f(summ.get("pe_ratio")), "roe": _f(summ.get("roe")),
        "roce": _f(summ.get("roce")), "dy": _f(summ.get("dividend_yield")), "hi52": hi, "lo52": lo,
        "name": str(B.get("name") or ""), "close": close, "splits": splits, "divs": divs,
        "fscore": _fscore(B) if B.get("resolved") else None, "partial": not sector,
    }


def _load_infos(symbols):
    store = st.session_state.setdefault("_pf_info", {})
    tried = st.session_state.setdefault("_pf_info_tried", {})
    now = time.time()
    todo = [s for s in symbols if (s not in store or store[s].get("partial")) and now - tried.get(s, 0) > 120]
    if todo:
        for s in todo:
            tried[s] = now
        with st.spinner(f"Analysing {len(symbols)} stocks — sectors, fundamentals, price, split and dividend history…"):
            with _pool(min(6, len(todo))) as ex:
                for s, info in zip(todo, ex.map(_fetch_info, todo)):
                    if info:
                        store[s] = info
    return {s: store.get(s) for s in symbols}


@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def _bench_cached(tickers: tuple):
    import yfinance as yf
    for t in tickers:
        try:
            df = yf.download(t, period="10y", interval="1d", progress=False, auto_adjust=True, threads=False)
        except Exception:
            continue
        if df is None or len(df) < 60:
            continue
        s = df["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s = _norm_series(s)
        if (pd.Timestamp.now().normalize() - s.index[-1]).days <= 10:
            return t, s
    raise ValueError("benchmark unavailable")


def _bench(name):
    try:
        return _bench_cached(BENCHMARKS.get(name, ("^NSEI",)))
    except Exception:
        if name != "NIFTY 50":
            try:
                return _bench_cached(BENCHMARKS["NIFTY 50"])
            except Exception:
                return None, None
        return None, None


def holding_news(symbols, names, days):
    if _fetch_news_for_stock is None or not symbols:
        return {}

    def one(sym):
        try:
            try:
                arts = _fetch_news_for_stock(sym, days, company=names.get(sym) or None)
            except TypeError:
                arts = _fetch_news_for_stock(sym, days)
            return sym, (arts or [])
        except Exception:
            return sym, []
    with _pool(min(6, len(symbols))) as ex:
        return dict(ex.map(one, symbols))


def _news_tone(arts):
    if not arts:
        return None
    m = {"strong_positive": 2, "mild_positive": 1, "neutral": 0, "mild_negative": -1, "strong_negative": -2}
    vals = [a["score"] if isinstance(a.get("score"), (int, float)) else m.get(str(a.get("sentiment")), 0) for a in arts]
    return sum(vals) / len(vals)


def _lookup_name(sym):
    if not resolve_symbol:
        return ""
    try:
        return (resolve_symbol(sym) or {}).get("name", "") or ""
    except Exception:
        return ""


def _rotation_map():
    """{normalised sector name: stage} from Money Flow → Sector Ranking, if it has been loaded this session."""
    res = st.session_state.get("mf_sr_res")
    if not res:
        return {}
    key = str(res.get("asof"))
    cache = st.session_state.get("_pf_rot")
    if cache and cache[0] == key:
        return cache[1]
    try:
        import sector_engine as se
        bench = "MARKET AVG" if "MARKET AVG" in res["bench_index"].columns else res["bench_index"].columns[0]
        rot, _ = se.rotation_table(res, bench)
        m = {re.sub(r"[^a-z]", "", str(s).lower()): stg for s, stg in zip(rot["sector"], rot["stage"])}
    except Exception:
        m = {}
    st.session_state["_pf_rot"] = (key, m)
    return m


def _stage_for(info, rmap):
    if not rmap or not info:
        return None
    for k in (info.get("broad"), info.get("sector"), info.get("industry")):
        s = rmap.get(re.sub(r"[^a-z]", "", str(k or "").lower()))
        if s:
            return s
    return None


# ═════════════════════════ engine: splits, FIFO, holdings ═════════════════════════
def split_factor(splits, since):
    """Number of today's shares per share held at `since` (product of splits after that date)."""
    f = 1.0
    if since is None:
        return f
    for d, r in splits or []:
        if d > since:
            f *= r
    return f


def run_engine(lots, sells, book_of, infos):
    """Split-adjust every lot and sell, then FIFO-match sells to lots inside each (book, symbol).
    Returns (adjusted lots with open quantity, realised matches, data problems)."""
    groups = defaultdict(list)
    for l in lots:
        sym = str(l["symbol"]).upper()
        bd = _d(l.get("buy_date"))
        splits = (infos.get(sym) or {}).get("splits") or []
        f = split_factor(splits, bd)
        q, p = float(l["quantity"]), float(l["buy_price"])
        groups[(book_of.get(str(l.get("id")), DEFAULT_BOOK), sym)].append({
            "id": l.get("id"), "symbol": sym, "book": book_of.get(str(l.get("id")), DEFAULT_BOOK), "date": bd,
            "qty": q * f, "price": p / f if f else p, "raw_qty": q, "raw_price": p, "factor": f,
            "note": l.get("note") or "", "name": l.get("name") or "", "open": q * f, "consumed": []})
    for g in groups.values():
        g.sort(key=lambda x: (x["date"] or date.min, str(x["id"])))
    realised, problems, adj_sells = [], [], []
    for s in sorted(sells, key=lambda x: (_d(x["sell_date"]) or date.min, str(x.get("id")))):
        sd = _d(s["sell_date"])
        sym = s["symbol"].upper()
        f = split_factor((infos.get(sym) or {}).get("splits") or [], sd)
        q, p = s["quantity"] * f, s["sell_price"] / f
        adj_sells.append({**s, "date": sd, "qty": q, "price": p, "factor": f})
        pool = [l for l in groups.get((s["book"], sym), []) if l["date"] is None or sd is None or l["date"] <= sd]
        avail = sum(l["open"] for l in pool)
        if q > avail + 1e-6:
            problems.append(f'Sell of {_qty(s["quantity"])} {sym} on {s["sell_date"]} ({s["book"]}) is more than the '
                            f'{_qty(avail / f if f else avail)} shares held then — only {_qty(avail)} matched. Check the sell or add the missing buy.')
        rem = min(q, avail)
        for l in pool:
            if rem <= 1e-9:
                break
            take = min(l["open"], rem)
            if take <= 1e-9:
                continue
            l["open"] -= take
            l["consumed"].append((sd, take))
            rem -= take
            long_term = bool(l["date"] and sd and sd > _add_months(l["date"], 12))
            realised.append({"symbol": sym, "book": s["book"], "sell_id": s.get("id"), "sell_date": sd, "buy_date": l["date"],
                             "qty": take, "buy_price": l["price"], "sell_price": p, "cost": take * l["price"], "proceeds": take * p,
                             "gain": take * (p - l["price"]), "days": (sd - l["date"]).days if (l["date"] and sd) else None,
                             "term": "LT" if long_term else "ST", "fy": _fy(sd) if sd else "—"})
    alots = [l for g in groups.values() for l in g]
    return alots, realised, problems, adj_sells


def aggregate(alots, quotes, infos, plans, book_filter, rmap, news):
    by = defaultdict(list)
    for l in alots:
        if l["open"] > 1e-9:
            by[l["symbol"]].append(l)
    out = []
    for sym, ls in by.items():
        qty = sum(l["open"] for l in ls)
        invested = sum(l["open"] * l["price"] for l in ls)
        q, info = quotes.get(sym) or {}, infos.get(sym) or {}
        ltp, prev, chg = _f(q.get("price")), _f(q.get("prev_close")), _f(q.get("chg"))
        if chg is None and ltp is not None and prev:
            chg = (ltp / prev - 1) * 100
        value = qty * ltp if ltp is not None else None
        books = sorted({l["book"] for l in ls})
        plan_key = (books[0], sym) if len(books) == 1 else (book_filter if book_filter != "ALL BOOKS" else books[0], sym)
        plan = plans.get(plan_key) or {}
        sl, tgt = plan.get("sl"), plan.get("target")
        avg = invested / qty if qty else 0.0
        risk = qty * (ltp - sl) if (sl is not None and ltp is not None and ltp > sl) else (0.0 if sl is not None and ltp is not None else None)
        reward = qty * (tgt - ltp) if (tgt is not None and ltp is not None and tgt > ltp) else None
        r_mult = (ltp - avg) / (avg - sl) if (sl is not None and ltp is not None and avg > sl) else None
        arts = news.get(sym) or []
        out.append({
            "symbol": sym, "name": next((l["name"] for l in ls if l["name"]), "") or info.get("name", ""),
            "sector": info.get("sector") or UNCLASSIFIED, "industry": info.get("industry") or UNCLASSIFIED,
            "mcap": info.get("mcap"), "pe": info.get("pe"), "roe": info.get("roe"), "roce": info.get("roce"), "dy": info.get("dy"),
            "hi52": info.get("hi52"), "lo52": info.get("lo52"), "fscore": info.get("fscore"),
            "qty": qty, "avg": avg, "invested": invested, "ltp": ltp, "value": value, "chg": chg,
            "pnl": (value - invested) if value is not None else None,
            "pnl_pct": ((value / invested - 1) * 100) if (value is not None and invested) else None,
            "day_pnl": qty * (ltp - prev) if (ltp is not None and prev) else None,
            "lots": sorted(ls, key=lambda l: (l["date"] or date.min)), "books": books, "split_adj": any(l["factor"] != 1 for l in ls),
            "sl": sl, "target": tgt, "risk": risk, "reward": reward, "r_mult": r_mult, "sl_hit": sl is not None and ltp is not None and ltp <= sl,
            "stage": _stage_for(info, rmap), "news_n": len(arts), "tone": _news_tone(arts), "first_date": min((l["date"] for l in ls if l["date"]), default=None),
        })
    total = sum(h["value"] for h in out if h["value"] is not None)
    for h in out:
        h["weight"] = (h["value"] / total * 100) if (h["value"] is not None and total) else None
    return sorted(out, key=lambda h: (h["value"] is None, -(h["value"] or 0)))


def estimate_dividends(alots, infos):
    """Gross dividends received per lot from Yahoo's dividend history (per share, in today's share units)."""
    rows = []
    for l in alots:
        if l["date"] is None:
            continue
        for d, amt in (infos.get(l["symbol"]) or {}).get("divs") or []:
            if d <= l["date"] or d > _today():
                continue
            held = l["qty"] - sum(q for sd, q in l["consumed"] if sd is not None and sd < d)
            if held > 1e-9:
                rows.append({"symbol": l["symbol"], "book": l["book"], "ex_date": d, "per_share": amt, "qty": held,
                             "amount": held * amt, "fy": _fy(d)})
    return rows


# ═════════════════════════ engine: performance ═════════════════════════
def timeline(alots, adj_sells, closes, bench):
    """Daily holdings value, net cash flows, time-weighted return and a benchmark bought / sold with the same cash."""
    dated = [l for l in alots if l["date"] is not None and l["symbol"] in closes]
    if not dated:
        return None
    start = pd.Timestamp(min(l["date"] for l in dated))
    syms = sorted({l["symbol"] for l in dated})
    px = pd.concat({s: closes[s] for s in syms}, axis=1).sort_index()
    px = px[px.index >= start - pd.Timedelta(days=7)]
    idx = px.index[px.index >= start]
    if len(idx) < 2:
        return None
    px = px.ffill().bfill().reindex(idx)
    qty = pd.DataFrame(0.0, index=idx, columns=syms)
    flow = pd.Series(0.0, index=idx)

    def snap(d):
        pos = idx.searchsorted(pd.Timestamp(d))
        return idx[min(pos, len(idx) - 1)] if pos < len(idx) else None

    for l in dated:
        t = snap(l["date"])
        if t is None:
            continue
        qty.loc[t:, l["symbol"]] += l["qty"]
        flow.loc[t] += l["qty"] * l["price"]
    for s in adj_sells:
        if s["date"] is None or s["symbol"] not in syms:
            continue
        t = snap(s["date"])
        if t is None:
            continue
        held = qty.loc[t, s["symbol"]]
        q = min(s["qty"], max(0.0, held))
        qty.loc[t:, s["symbol"]] -= q
        flow.loc[t] -= q * s["price"]
    value = (qty * px).sum(axis=1)
    prev = value.shift(1).fillna(0.0)
    denom = prev + flow.clip(lower=0)
    r = ((value - prev - flow) / denom.replace(0, np.nan)).fillna(0.0).clip(-0.5, 0.5)
    df = pd.DataFrame({"Value": value, "Flow": flow, "Net invested": flow.cumsum(), "r": r})
    if bench is not None:
        b = bench.reindex(idx.union(bench.index)).ffill().reindex(idx).ffill().bfill()
        units = (flow / b).cumsum().clip(lower=0)
        df["Benchmark"] = units * b
        df["rb"] = b.pct_change().fillna(0.0)
    return df


def monthly_returns(r: pd.Series) -> pd.Series:
    try:
        m = (1 + r).resample("ME").prod() - 1
    except ValueError:
        m = (1 + r).resample("M").prod() - 1
    return m * 100


def _xirr(flows):
    flows = [(d, a) for d, a in flows if a]
    if len(flows) < 2 or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(r):
        return sum(a / (1.0 + r) ** ((d - t0).days / 365.0) for d, a in flows)
    try:
        lo, hi = -0.99, 20.0
        f_lo, f_hi = npv(lo), npv(hi)
        if f_lo * f_hi > 0:
            return None
        for _ in range(140):
            mid = (lo + hi) / 2
            f_mid = npv(mid)
            if f_lo * f_mid <= 0:
                hi = mid
            else:
                lo, f_lo = mid, f_mid
        return (lo + hi) / 2
    except (OverflowError, ZeroDivisionError):
        return None


def portfolio_xirr(alots, adj_sells, holds):
    flows = [(l["date"], -l["qty"] * l["price"]) for l in alots if l["date"]]
    flows += [(s["date"], s["qty"] * s["price"]) for s in adj_sells if s["date"]]
    value_now = sum(h["value"] for h in holds if h["value"] is not None)
    if not flows:
        return None, 0
    span = (_today() - min(d for d, _ in flows)).days
    if span < 60:
        return None, span
    flows.append((_today(), value_now))
    return _xirr(flows), span


# ═════════════════════════ analytics ═════════════════════════
def concentration(holds):
    w = sorted([h["weight"] for h in holds if h["weight"]], reverse=True)
    if not w:
        return None
    hhi = sum((x / 100) ** 2 for x in w)
    return {"top1": w[0], "top3": sum(w[:3]), "top5": sum(w[:5]), "hhi": hhi, "eff_n": (1 / hhi) if hhi else None, "n": len(w)}


def group_table(holds, key):
    agg = {}
    for h in holds:
        k = h[key] or UNCLASSIFIED
        a = agg.setdefault(k, {"name": k, "n": 0, "syms": [], "invested": 0.0, "invested_v": 0.0, "value": 0.0, "day": 0.0, "stage": None})
        a["n"] += 1
        a["syms"].append(h["symbol"])
        a["invested"] += h["invested"]
        a["stage"] = a["stage"] or h.get("stage")
        if h["value"] is not None:
            a["value"] += h["value"]
            a["invested_v"] += h["invested"]
        if h["day_pnl"] is not None:
            a["day"] += h["day_pnl"]
    total = sum(a["value"] for a in agg.values())
    for a in agg.values():
        a["pnl"] = a["value"] - a["invested_v"]
        a["pnl_pct"] = (a["value"] / a["invested_v"] - 1) * 100 if a["invested_v"] else None
        a["weight"] = a["value"] / total * 100 if total else 0.0
    return sorted(agg.values(), key=lambda a: -a["value"])


def sector_colors(sectors):
    out, i = {}, 0
    for s in sectors:
        if s["name"] == UNCLASSIFIED:
            out[s["name"]] = UNCLASSIFIED_COLOR
        else:
            out[s["name"]] = PALETTE[i % len(PALETTE)]
            i += 1
    return out


def mcap_split(holds):
    b = {"Large cap": 0.0, "Mid cap": 0.0, "Small cap": 0.0, "Unknown": 0.0}
    for h in holds:
        if h["value"] is None:
            continue
        m = h.get("mcap")
        b["Unknown" if m is None else "Large cap" if m >= LARGE_CAP_CR else "Mid cap" if m >= MID_CAP_CR else "Small cap"] += h["value"]
    tot = sum(b.values())
    return [{"name": k, "value": v, "weight": (v / tot * 100) if tot else 0.0} for k, v in b.items()]


def weighted_fundamentals(holds):
    tot = sum(h["value"] for h in holds if h["value"] is not None)

    def wavg(key):
        num = den = 0.0
        for h in holds:
            v = h.get(key)
            if h["value"] is None or v is None:
                continue
            num += h["value"] * v
            den += h["value"]
        return (num / den if den else None), (den / tot * 100 if tot else 0.0)
    inv = den = 0.0
    for h in holds:
        pe = h.get("pe")
        if h["value"] is None or pe is None or pe <= 0:
            continue
        inv += h["value"] / pe
        den += h["value"]
    out = {"pe": ((den / inv) if inv else None, den / tot * 100 if tot else 0.0)}
    for k in ("roe", "roce", "dy"):
        out[k] = wavg(k)
    return out


def _returns_frame(syms, closes, n=300):
    px = pd.concat({s: closes[s] for s in syms}, axis=1).sort_index().ffill().tail(n)
    return (px / px.shift(1) - 1).iloc[1:]


def risk_metrics(holds, closes, nifty):
    syms = [h["symbol"] for h in holds if h["value"] and h["symbol"] in closes]
    if not syms:
        return None
    rets = _returns_frame(syms, closes).tail(252)
    if len(rets) < 60:
        return None
    w = pd.Series({h["symbol"]: h["value"] for h in holds if h["symbol"] in syms}, dtype="float64")
    w = w / w.sum()
    wsum = rets.notna().mul(w, axis=1).sum(axis=1)
    port = (rets.fillna(0.0).mul(w, axis=1).sum(axis=1) / wsum).replace([np.inf, -np.inf], np.nan).dropna()
    if len(port) < 60:
        return None
    total_val = sum(h["value"] for h in holds if h["value"])
    cum = (1 + port).cumprod()
    out = {"n_days": len(port), "vol": float(port.std() * math.sqrt(252) * 100), "mdd": float((cum / cum.cummax() - 1).min() * 100),
           "ret1y": float((cum.iloc[-1] - 1) * 100), "var95": float(-np.percentile(port.values, 5) * total_val),
           "beta": None, "bench_ret": None, "corr": None, "avg_corr": None, "top_pair": None, "betas": {}, "port": port}
    if nifty is not None:
        nr = (nifty / nifty.shift(1) - 1).dropna()
        j = pd.concat([port.rename("p"), nr.rename("n")], axis=1, join="inner").dropna()
        if len(j) >= 60 and j["n"].var() > 0:
            out["beta"] = float(j["p"].cov(j["n"]) / j["n"].var())
        for s in syms:
            js = pd.concat([rets[s].rename("s"), nr.rename("n")], axis=1, join="inner").dropna()
            if len(js) >= 60 and js["n"].var() > 0:
                out["betas"][s] = float(js["s"].cov(js["n"]) / js["n"].var())
        nw = nifty[(nifty.index >= port.index[0]) & (nifty.index <= port.index[-1])]
        if len(nw) > 10:
            out["bench_ret"] = float((nw.iloc[-1] / nw.iloc[0] - 1) * 100)
    top = sorted([h for h in holds if h["value"] and h["symbol"] in rets.columns], key=lambda h: -h["value"])[:12]
    if len(top) >= 2:
        cols = [h["symbol"] for h in top]
        corr = rets[cols].corr(min_periods=60)
        vals = corr.values
        iu = np.triu_indices_from(vals, k=1)
        pv = vals[iu]
        ok = ~np.isnan(pv)
        out["corr"] = corr
        if ok.any():
            out["avg_corr"] = float(pv[ok].mean())
            j = int(np.nanargmax(np.where(ok, pv, -np.inf)))
            out["top_pair"] = (cols[iu[0][j]], cols[iu[1][j]], float(pv[j]))
    return out


def diversification_score(conc, sectors, risk):
    if not conc:
        return None
    known = [s for s in sectors if s["name"] != UNCLASSIFIED]
    pen = min(30.0, max(0.0, conc["top1"] - 10) * 1.2)
    if known:
        pen += min(30.0, max(0.0, known[0]["weight"] - 25) * 1.0)
    pen += min(25.0, max(0.0, 10 - (conc["eff_n"] or 1)) * 3)
    if risk and risk.get("avg_corr") is not None:
        pen += min(15.0, max(0.0, risk["avg_corr"] - 0.5) * 60)
    return int(max(0, round(100 - pen)))


def tax_summary(realised, rates, fy=None):
    """Indian listed-equity capital gains for each FY: set-off (STCL vs STCG then LTCG; LTCL vs LTCG only),
    LTCG exemption, flat rates + cess. Ignores surcharge, carry-forward and pre-2018 grandfathering."""
    by = defaultdict(lambda: {"st_gain": 0.0, "st_loss": 0.0, "lt_gain": 0.0, "lt_loss": 0.0, "proceeds": 0.0, "n": 0})
    for r in realised:
        if fy and r["fy"] != fy:
            continue
        a = by[r["fy"]]
        a["n"] += 1
        a["proceeds"] += r["proceeds"]
        k = "st" if r["term"] == "ST" else "lt"
        if r["gain"] >= 0:
            a[f"{k}_gain"] += r["gain"]
        else:
            a[f"{k}_loss"] += -r["gain"]
    out = {}
    for f, a in by.items():
        st_set = min(a["st_gain"], a["st_loss"])
        st_net = a["st_gain"] - st_set
        left_stl = a["st_loss"] - st_set
        lt_net = a["lt_gain"] - a["lt_loss"] - left_stl
        st_net = max(st_net, 0.0)
        lt_taxable = max(0.0, lt_net - rates["exempt"]) if lt_net > 0 else 0.0
        tax = (st_net * rates["stcg"] + lt_taxable * rates["ltcg"]) / 100 * (1 + rates["cess"] / 100)
        out[f] = {**a, "st_net": st_net, "lt_net": lt_net, "lt_taxable": lt_taxable, "tax": tax,
                  "exempt_left": max(0.0, rates["exempt"] - max(lt_net, 0.0)), "carry_loss": max(0.0, -lt_net)}
    return dict(sorted(out.items()))


def rebalance_plan(holds, rules):
    """Trim positions / sectors above the caps; optionally redeploy freed cash (and new cash) into holdings with
    headroom, proportional to their current value. Returns rows with exact share quantities."""
    H = [h for h in holds if h["value"] and h["ltp"]]
    if not H:
        return [], 0.0
    cash_new = max(0.0, float(rules.get("cash") or 0.0))
    total = sum(h["value"] for h in H) + cash_new
    cap_s, cap_sec = rules["max_stock"] / 100, rules["max_sector"] / 100
    tgt = {h["symbol"]: h["value"] for h in H}
    sec = {h["symbol"]: h["sector"] for h in H}
    for _ in range(60):
        changed = False
        for s, v in tgt.items():
            if v > cap_s * total + 1e-6:
                tgt[s] = cap_s * total
                changed = True
        sums = defaultdict(float)
        for s, v in tgt.items():
            if sec[s] != UNCLASSIFIED:
                sums[sec[s]] += v
        for name, v in sums.items():
            if v > cap_sec * total + 1e-6:
                k = cap_sec * total / v
                for s in tgt:
                    if sec[s] == name:
                        tgt[s] *= k
                changed = True
        if not changed:
            break
    pool = total - sum(tgt.values())
    if rules.get("redeploy"):
        for _ in range(60):
            if pool <= 1:
                break
            sums = defaultdict(float)
            for s, v in tgt.items():
                sums[sec[s]] += v
            room = {}
            for s, v in tgt.items():
                r1 = cap_s * total - v
                r2 = (cap_sec * total - sums[sec[s]]) if sec[s] != UNCLASSIFIED else r1
                r = min(r1, r2)
                if r > 1:
                    room[s] = r
            if not room:
                break
            base = sum(tgt[s] for s in room) or 1.0
            given = 0.0
            for s, r in room.items():
                add = min(r, pool * tgt[s] / base)
                tgt[s] += add
                given += add
            pool -= given
            if given < 1:
                break
    rows = []
    for h in H:
        dv = tgt[h["symbol"]] - h["value"]
        q = math.floor(abs(dv) / h["ltp"]) if dv > 0 else math.ceil(abs(dv) / h["ltp"])
        q = min(q, math.floor(h["qty"])) if dv < 0 else q
        if q >= 1 and abs(dv) >= max(500.0, 0.002 * total):
            rows.append({"symbol": h["symbol"], "sector": h["sector"], "now_w": h["value"] / total * 100,
                         "new_w": (h["value"] + (q if dv > 0 else -q) * h["ltp"]) / total * 100,
                         "action": "BUY" if dv > 0 else "SELL", "qty": q, "amount": q * h["ltp"], "ltp": h["ltp"], "h": h})
    return sorted(rows, key=lambda r: (r["action"] != "SELL", -r["amount"])), pool


def fifo_preview(h, qty, price):
    """Realised gain and term mix if `qty` shares were sold today at `price` (FIFO over open lots)."""
    rem, st_g, lt_g = qty, 0.0, 0.0
    for l in h["lots"]:
        if rem <= 1e-9:
            break
        take = min(l["open"], rem)
        g = take * (price - l["price"])
        if l["date"] and _today() > _add_months(l["date"], 12):
            lt_g += g
        else:
            st_g += g
        rem -= take
    return st_g, lt_g


def build_flags(A, holds, news, days_label):
    flags = []

    def add(level, title, detail):
        flags.append({"level": level, "title": title, "detail": detail})
    conc, sectors, mcap, risk = A["conc"], A["sectors"], A["mcap"], A.get("risk")
    n = A["tot"]["n"]
    weighted = [h for h in holds if h["weight"]]
    if conc and weighted:
        top = max(weighted, key=lambda h: h["weight"])
        t = f'{top["symbol"]} is {top["weight"]:.1f}% of the portfolio'
        if conc["top1"] > 25:
            add("alert", "Single-stock concentration", t + ". One stock this large can dominate your results — check this is intentional.")
        elif conc["top1"] > 15:
            add("watch", "Large top position", t + ". Comfortable for high-conviction ideas, but worth a periodic review.")
        else:
            add("ok", "Position sizing looks balanced", t + ", so no single stock dominates.")
        if conc["top5"] > 70 and n > 5:
            add("watch", "Top-5 heavy", f'Your five largest positions make up {conc["top5"]:.0f}% of the value.')
        if n >= 5 and conc["eff_n"] and conc["eff_n"] < 0.5 * n:
            add("watch", "Lopsided weights", f'You hold {n} stocks but they behave like about {conc["eff_n"]:.1f} equal-sized positions.')
    if n < 5:
        add("alert", "Very few holdings", f"Only {n} stock{'s' if n != 1 else ''} — company-specific events will move the whole portfolio.")
    elif n < 10:
        add("watch", "Compact portfolio", f"{n} holdings. Fine if deliberate; spread across sectors matters more at this size.")
    elif n > 30:
        add("info", "Many holdings", f"{n} stocks can be hard to track closely; small positions barely move results.")
    known = [s for s in sectors if s["name"] != UNCLASSIFIED]
    if known:
        s0 = known[0]
        t = f'{s0["name"]} is {s0["weight"]:.1f}% of the portfolio ({s0["n"]} stock{"s" if s0["n"] != 1 else ""})'
        if s0["weight"] > 40:
            add("alert", "Sector concentration", t + ". A sector-wide event would hit most of your money at once.")
        elif s0["weight"] > 30:
            add("watch", "Sector tilt", t + ". Not necessarily wrong — just a deliberate bet to be aware of.")
        else:
            add("ok", "Sector spread looks healthy", t + ", your largest sector.")
    weak = [s for s in known if s.get("stage") in ("FADING", "WEAK") and s["weight"] >= 10]
    if weak:
        add("watch", "Money rotating out of your sectors",
            "; ".join(f'{s["name"]} {s["weight"]:.0f}% ({s["stage"]})' for s in weak[:4]) + " — per Money Flow → Sector Ranking.")
    unk = [h["symbol"] for h in holds if h["sector"] == UNCLASSIFIED]
    if unk:
        add("info", "Sector data missing", "Could not classify: " + ", ".join(unk[:8]) + (" …" if len(unk) > 8 else "") + ". It retries automatically.")
    small = next((m for m in mcap if m["name"] == "Small cap"), None)
    if small and small["weight"] > 40:
        add("watch", "Small-cap heavy", f'{small["weight"]:.0f}% of value is in small caps, which are typically more volatile and less liquid.')
    hit = [h["symbol"] for h in holds if h["sl_hit"]]
    if hit:
        add("alert", "Stop-loss breached", "Price is at or below your stop-loss for: " + ", ".join(hit) + ".")
    tot_v = A["tot"]["val"] or 0
    uncovered = sum(h["value"] for h in holds if h["value"] and h["sl"] is None)
    if tot_v and uncovered / tot_v > 0.5 and any(h["sl"] is not None for h in holds):
        add("info", "Stops on part of the portfolio", f"{uncovered / tot_v * 100:.0f}% of value has no stop-loss set.")
    if risk:
        if risk["top_pair"] and risk["top_pair"][2] > 0.8:
            a, b, c = risk["top_pair"]
            add("watch", "Highly correlated pair", f"{a} and {b} have moved almost together (correlation {c:.2f}).")
        if risk["avg_corr"] is not None and risk["avg_corr"] > 0.6:
            add("watch", "Holdings move together", f'Average correlation between your top holdings is {risk["avg_corr"]:.2f}.')
        if risk["mdd"] < -25:
            add("watch", "Deep drawdown", f'At today\'s weights the portfolio fell {abs(risk["mdd"]):.0f}% peak-to-trough in the last year.')
        if risk["beta"] is not None and risk["beta"] > 1.3:
            add("watch", "High market sensitivity", f'Beta vs NIFTY 50 is {risk["beta"]:.2f} — expect larger swings than the index.')
        elif risk["beta"] is not None and risk["beta"] < 0.7:
            add("info", "Defensive profile", f'Beta vs NIFTY 50 is {risk["beta"]:.2f} — the portfolio moves less than the index.')
    losers = sorted([h for h in holds if h["pnl_pct"] is not None and h["pnl_pct"] <= -20], key=lambda h: h["pnl_pct"])
    if losers:
        add("watch", "Deep losers", f"{len(losers)} position{'s are' if len(losers) != 1 else ' is'} down 20% or more: " +
            ", ".join(f'{h["symbol"]} ({h["pnl_pct"]:.0f}%)' for h in losers[:5]) + ".")
    lowq = [f'{h["symbol"]} ({h["fscore"][0]}/{h["fscore"][1]})' for h in holds if h.get("fscore") and h["fscore"][1] >= 6 and h["fscore"][0] / h["fscore"][1] < 0.45]
    if lowq:
        add("info", "Weak quality scores", "Piotroski-style score below 45%: " + ", ".join(lowq[:6]) + ". See Research → HC.")
    negn = [h["symbol"] for h in holds if h.get("tone") is not None and h["tone"] <= -1 and h["news_n"] >= 2]
    if negn:
        add("watch", "Negative news flow", f"Mostly negative headlines in the last {days_label} for: " + ", ".join(negn[:6]) + ".")
    if A["tot"]["n_valid"] < n:
        add("watch", "Missing prices", f'{n - A["tot"]["n_valid"]} holding(s) have no live price and are excluded from totals.')
    if A.get("problems"):
        add("alert", "Transaction data needs attention", A["problems"][0] + (f" (+{len(A['problems']) - 1} more — see TRANSACTIONS)" if len(A["problems"]) > 1 else ""))
    adj = [h["symbol"] for h in holds if h["split_adj"]]
    if adj:
        add("info", "Adjusted for splits / bonus", "Quantities and buy prices adjusted automatically for: " + ", ".join(adj[:8]) + ".")
    order = {"alert": 0, "watch": 1, "info": 2, "ok": 3}
    return sorted(flags, key=lambda f: order[f["level"]])


# ═════════════════════════ plotting ═════════════════════════
def _layout(fig, h=260, margin=(0, 0, 0, 0), legend=False):
    fig.update_layout(height=h, margin=dict(l=margin[0], r=margin[1], t=margin[2], b=margin[3]),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=legend,
                      font=dict(family="Inter, sans-serif", size=11, color=T2),
                      hoverlabel=dict(bgcolor=DARK2, bordercolor=BORDER, font=dict(family="JetBrains Mono, monospace", size=11, color=IVORY)))
    return fig


def _plot(builder, key, *args):
    if go is None:
        st.caption("Charts need plotly — add `plotly` to requirements.txt.")
        return
    try:
        fig = builder(*args)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)
    except Exception as e:  # pragma: no cover
        st.caption(f"Chart unavailable ({type(e).__name__}: {e}).")


def _inr_axis(fig, lo, hi, n=5):
    vals = list(np.linspace(lo, hi, n))
    fig.update_yaxes(tickvals=vals, ticktext=[_short(v) for v in vals])


def _fig_growth(df, bench_label):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["Value"], name="Your holdings", mode="lines", line=dict(color=AMBER, width=2),
                             fill="tozeroy", fillcolor="rgba(255,159,10,0.07)", hovertemplate="%{x|%d %b %Y}<br>Value ₹%{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=df.index, y=df["Net invested"], name="Net invested", mode="lines",
                             line=dict(color="#8A8A8A", width=1.3, dash="dot", shape="hv"), hovertemplate="Net invested ₹%{y:,.0f}<extra></extra>"))
    if "Benchmark" in df:
        fig.add_trace(go.Scatter(x=df.index, y=df["Benchmark"], name=f"{bench_label}, same cash flows", mode="lines",
                                 line=dict(color=CYAN, width=1.5), hovertemplate=bench_label + " ₹%{y:,.0f}<extra></extra>"))
    _layout(fig, 310, (0, 6, 6, 0), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.08, x=0, font=dict(size=10)), hovermode="x unified")
    fig.update_xaxes(showgrid=False, linecolor=BORDER)
    fig.update_yaxes(showgrid=True, gridcolor="#161616", zeroline=False)
    top = float(df[[c for c in ("Value", "Net invested", "Benchmark") if c in df]].max().max())
    _inr_axis(fig, 0.0, top * 1.05 if top > 0 else 1.0)
    return fig


def _fig_twr(df, bench_label):
    me = (1 + df["r"]).cumprod() * 100
    fig = go.Figure(go.Scatter(x=me.index, y=me.values, name="Portfolio (time-weighted)", line=dict(color=AMBER, width=2)))
    if "rb" in df:
        b = (1 + df["rb"]).cumprod() * 100
        fig.add_trace(go.Scatter(x=b.index, y=b.values, name=bench_label, line=dict(color=CYAN, width=1.5)))
    fig.add_hline(y=100, line=dict(color="#444", dash="dot"))
    _layout(fig, 260, (0, 6, 6, 0), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.1, x=0), hovermode="x unified")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#161616")
    return fig


def _fig_heat_months(m: pd.Series):
    t = pd.DataFrame({"y": m.index.year, "m": m.index.month, "v": m.values}).pivot(index="y", columns="m", values="v").reindex(columns=range(1, 13))
    names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    z = t.values
    text = [[("" if pd.isna(v) else f"{v:+.1f}") for v in row] for row in z]
    fig = go.Figure(go.Heatmap(z=z, x=names, y=[str(y) for y in t.index], text=text, texttemplate="%{text}", zmid=0, showscale=False,
                               colorscale=[[0, RED], [0.5, "#141414"], [1, GREEN]], xgap=2, ygap=2,
                               hovertemplate="%{y} %{x}: %{z:+.2f}%<extra></extra>"))
    _layout(fig, 60 + 34 * len(t))
    fig.update_yaxes(autorange="reversed")
    return fig


def _fig_donut(sectors, scol, total_value):
    rows = [s for s in sectors if s["value"] > 0]
    fig = go.Figure(go.Pie(labels=[s["name"] for s in rows], values=[s["value"] for s in rows], hole=0.66, sort=False,
                           marker=dict(colors=[scol[s["name"]] for s in rows], line=dict(color="#050505", width=2)),
                           textinfo="percent", textfont=dict(size=10, color="#fff"), hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>"))
    _layout(fig, 250)
    fig.add_annotation(text=f"<b>{_short(total_value)}</b><br><span style='font-size:9px'>VALUE</span>", showarrow=False,
                       font=dict(size=14, color=IVORY, family="JetBrains Mono, monospace"))
    return fig


def _fig_hbars(labels, vals, colors, suffix="%", h=None, fmt="{:.1f}"):
    fig = go.Figure(go.Bar(x=vals, y=labels, orientation="h", marker=dict(color=colors),
                           text=[(fmt.format(v) + suffix) if suffix else _short(v) for v in vals], textposition="outside", cliponaxis=False,
                           textfont=dict(size=10, color=T2), hovertemplate="<b>%{y}</b> %{x:,.2f}<extra></extra>"))
    _layout(fig, h or max(200, 26 * len(labels) + 40), (0, 60, 0, 0))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=IVORY, size=10, family="JetBrains Mono, monospace"))
    fig.update_xaxes(visible=False)
    return fig


def _fig_treemap(holds, sectors):
    ids, labels, parents, values, colors, custom = ["ROOT"], ["Portfolio"], [""], [0.0], [0.0], [["", ""]]
    lim = 5.0
    for s in sectors:
        if s["value"] <= 0:
            continue
        sid = "S::" + s["name"]
        ids.append(sid); labels.append(s["name"]); parents.append("ROOT"); values.append(0.0)
        colors.append(s["pnl_pct"] or 0.0); custom.append([_short(s["value"]), _pct(s["pnl_pct"])])
        lim = max(lim, abs(s["pnl_pct"] or 0))
        for h in holds:
            if (h["sector"] or UNCLASSIFIED) != s["name"] or not h["value"]:
                continue
            ids.append("H::" + h["symbol"]); labels.append(h["symbol"]); parents.append(sid); values.append(h["value"])
            colors.append(h["pnl_pct"] or 0.0); custom.append([_short(h["value"]), _pct(h["pnl_pct"])])
            lim = max(lim, abs(h["pnl_pct"] or 0))
    lim = min(lim, 40.0)
    fig = go.Figure(go.Treemap(ids=ids, labels=labels, parents=parents, values=values, branchvalues="remainder", customdata=custom,
                               marker=dict(colors=colors, colorscale=[[0.0, "#B3261E"], [0.5, "#1b1b1b"], [1.0, "#1E9E4A"]], cmin=-lim, cmax=lim,
                                           showscale=False, line=dict(width=1.5, color="#050505")),
                               texttemplate="<b>%{label}</b><br>%{percentRoot:.1%}", textfont=dict(family="Inter, sans-serif", size=12, color="#fff"),
                               hovertemplate="<b>%{label}</b><br>Value %{customdata[0]}<br>P&L %{customdata[1]}<extra></extra>",
                               pathbar=dict(visible=False), tiling=dict(pad=2), root_color="rgba(0,0,0,0)"))
    _layout(fig, 340)
    return fig


def _fig_corr(corr):
    names = list(corr.columns)
    fig = go.Figure(go.Heatmap(z=corr.values, x=names, y=names, zmin=-1, zmax=1, xgap=2, ygap=2,
                               colorscale=[[0.0, "#1E5BA8"], [0.5, "#171717"], [1.0, "#FF9F0A"]], showscale=False,
                               texttemplate="%{z:.2f}", textfont=dict(size=9, color="#fff"), hovertemplate="%{x} / %{y}: %{z:.2f}<extra></extra>"))
    _layout(fig, min(520, 38 * len(names) + 70))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=9, color=IVORY))
    fig.update_xaxes(tickfont=dict(size=9, color=IVORY), side="top")
    return fig


def _fig_gauge(score):
    color = GREEN if score >= 75 else AMBER if score >= 50 else RED
    fig = go.Figure(go.Indicator(mode="gauge+number", value=score, number=dict(font=dict(size=34, color=color, family="JetBrains Mono, monospace")),
                                 gauge=dict(axis=dict(range=[0, 100], tickcolor=T3, tickfont=dict(size=8, color=T3)), bar=dict(color=color, thickness=0.28),
                                            bgcolor="#111", borderwidth=0,
                                            steps=[dict(range=[0, 50], color="#241211"), dict(range=[50, 75], color="#241f10"), dict(range=[75, 100], color="#10241a")])))
    _layout(fig, 170, (14, 14, 12, 0))
    return fig


# ═════════════════════════ HTML blocks ═════════════════════════
def _flags_html(flags, limit=None):
    colors = {"alert": RED, "watch": AMBER, "info": CYAN, "ok": GREEN}
    out = ""
    for f in (flags[:limit] if limit else flags):
        c = colors[f["level"]]
        out += (f'<div class="pf-flag" style="--fc:{c}"><div class="lv">{f["level"].upper()}</div>'
                f'<div><div class="ft">{_esc(f["title"])}</div><div class="fd">{_esc(f["detail"])}</div></div></div>')
    return out


def _stage_pill(stage):
    if not stage:
        return '<span style="color:#444">—</span>'
    return f'<span class="pf-pill" style="color:{STAGE_COLORS.get(stage, T2)}">{_esc(stage)}</span>'


def _table(head, rows, right=()):
    th = "".join(f'<th class="{"r" if i in right else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"r" if i in right else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in rows)
    st.markdown(f'<div class="pf-tw"><table class="pf-tbl"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>', unsafe_allow_html=True)


def _group_rows(rows, colors=None):
    out = []
    for r in rows:
        c = (colors or {}).get(r["name"], T2)
        syms = ", ".join(r["syms"][:4]) + (" …" if len(r["syms"]) > 4 else "")
        w = max(0.0, min(100.0, r["weight"]))
        out.append([f'<span class="pf-sw" style="background:{c}"></span>{_esc(r["name"])}<div class="sub">{_esc(syms)}</div>', str(r["n"]),
                    _short(r["value"]), f'<span class="pf-wbar"><i style="width:{w:.1f}%;background:{c}"></i></span><span class="pf-mini">{r["weight"]:.1f}%</span>',
                    f'<span style="color:{_col(r["pnl_pct"])}">{_pct(r["pnl_pct"])}</span>', _stage_pill(r.get("stage"))])
    return out


def _movers_html(holds):
    ranked = sorted([h for h in holds if h["chg"] is not None], key=lambda h: h["chg"], reverse=True)
    if not ranked:
        return '<div class="pf-note">No live price changes yet.</div>'
    up = ranked[:3]
    dn = [h for h in ranked[::-1][:3] if h not in up]
    return "".join(f'<div class="pf-mover"><b>{_esc(h["symbol"])}</b><span style="color:{T2}">{_short(h["day_pnl"])}</span>'
                   f'<span style="color:{_col(h["chg"])}">{"▲" if h["chg"] >= 0 else "▼"} {abs(h["chg"]):.2f}%</span></div>' for h in up + dn)


# ═════════════════════════ forms ═════════════════════════
def _missing_table_help():
    st.error("The `portfolio` table does not exist in Supabase yet. Run this once in Supabase → SQL Editor, then try again:")
    st.code(ud.SQL_PORTFOLIO, language="sql")


def _book_options(D):
    books = set(D["meta"].values()) | {s["book"] for s in D["sells"]} | {DEFAULT_BOOK}
    books |= set((D["settings"].get("books") or []))
    return sorted(books, key=lambda b: (b != DEFAULT_BOOK, b.lower()))


def _add_buy_form(supabase, store, D, key="main", preset="", book=None):
    books = _book_options(D)
    with st.form(f"pf_add_{key}", clear_on_submit=True):
        c1, c2, c3, c4, c5 = st.columns([1.8, 1, 1.2, 1.2, 1.2])
        sym = c1.text_input("Stock (NSE symbol)", value=preset, placeholder="e.g. RELIANCE")
        qty = c2.number_input("Quantity", min_value=0.0, value=0.0, step=1.0, format="%g")
        price = c3.number_input("Buy price (₹)", min_value=0.0, value=0.0, step=0.05, format="%.2f")
        when = c4.date_input("Buy date", value=_today(), max_value=_today())
        bk = c5.selectbox("Book", books, index=books.index(book) if book in books else 0)
        note = st.text_input("Note (optional)", placeholder="e.g. breakout entry")
        go_btn = st.form_submit_button("＋ ADD BUY", type="primary", use_container_width=True)
    if not go_btn:
        return
    s = ud.clean_symbol(sym)
    if not s:
        st.error("Enter the stock symbol.")
        return
    if qty <= 0 or price <= 0:
        st.error("Quantity and buy price must be greater than zero.")
        return
    if not (get_quotes((s,)) or {}).get(s):
        st.error(f"No price data found for “{s}”. Use the NSE trading symbol (e.g. M&M, BAJAJ-AUTO).")
        return
    before = {str(l.get("id")) for l in (st.session_state.get("pf_lots") or [])}
    ok, msg = ud.add_lot(supabase, s, qty, price, when, note, name=_lookup_name(s))
    if not ok:
        _missing_table_help() if msg == "MISSING_TABLE" else st.error(msg)
        return
    if bk != DEFAULT_BOOK:
        lots_now, _ = ud.load_portfolio(supabase)
        new_ids = [str(l.get("id")) for l in lots_now if str(l.get("id")) not in before and str(l["symbol"]).upper() == s]
        for lid in new_ids:
            store.upsert("portfolio_lot_meta", {"lot_id": lid, "book": bk})
    if msg:
        st.warning(msg)
    st.toast(f"BUY {s}: {qty:g} @ {_inr(price)} · {bk}")
    st.rerun()


def _sell_form(store, D, alots, key="main", preset_sym=None, preset_book=None):
    openq = defaultdict(float)
    for l in alots:
        if l["open"] > 1e-9:
            openq[(l["book"], l["symbol"])] += l["open"]
    if not openq:
        _note("No open positions to sell.")
        return
    options = sorted(openq, key=lambda k: (k[1], k[0]))
    labels = [f"{s} · {b} · {_qty(q)} open" for (b, s), q in ((k, openq[k]) for k in options)]
    default = next((i for i, k in enumerate(options) if k[1] == preset_sym and (preset_book in (None, k[0]))), 0)
    with st.form(f"pf_sell_{key}", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2.2, 1, 1.2, 1.2])
        pick = c1.selectbox("Position", labels, index=default)
        qty = c2.number_input("Quantity sold", min_value=0.0, value=0.0, step=1.0, format="%g")
        price = c3.number_input("Sell price (₹)", min_value=0.0, value=0.0, step=0.05, format="%.2f")
        when = c4.date_input("Sell date", value=_today(), max_value=_today())
        note = st.text_input("Note (optional)", placeholder="e.g. target hit, trailing SL")
        ok = st.form_submit_button("− RECORD SELL", type="primary", use_container_width=True)
    if not ok:
        return
    book, sym = options[labels.index(pick)]
    open_now = openq[(book, sym)]
    if qty <= 0 or price <= 0:
        st.error("Quantity and sell price must be greater than zero.")
        return
    if qty > open_now + 1e-6:
        st.error(f"You hold {_qty(open_now)} {sym} in {book} (split-adjusted). Sell quantity can't exceed that.")
        return
    first = min((l["date"] for l in alots if l["symbol"] == sym and l["book"] == book and l["date"]), default=None)
    if first and when < first:
        st.error(f"Sell date is before your first {sym} buy ({first:%d %b %Y}).")
        return
    err = store.insert("portfolio_sells", {"book": book, "symbol": sym, "quantity": qty, "sell_price": price,
                                           "sell_date": when.isoformat(), "note": note or ""})
    if err:
        st.error(f"Could not save the sell: {err}")
        return
    st.toast(f"SELL {sym}: {qty:g} @ {_inr(price)} · {book}")
    st.rerun()


# ═════════════════════════ views ═════════════════════════
def _empty_state():
    st.markdown('<div class="pf-empty"><div class="a">Build your portfolio review</div>'
                '<div class="b">Open <b>TRANSACTIONS</b> and add each purchase: stock, quantity, buy price, date and book.<br>'
                'Record sells there too. The page then shows exposure, risk, performance versus the index, realised gains and tax, '
                'dividends, a rebalance plan and an automatic health check.</div></div>', unsafe_allow_html=True)


def _view_dashboard(A, holds, news, days_label, bench_label):
    T = A["tot"]
    xi = A["xirr"]
    _kpis([
        _kpi("Invested (open)", _short(T["inv"]), f'{T["n"]} holdings · {T["n_lots"]} lots', accent=T3),
        _kpi("Current value", _short(T["val"]), "live · delayed per source", accent=CYAN),
        _kpi("Unrealised P&L", _short(T["pnl"]), _pct(T["pnl_pct"]), _col(T["pnl"]), _col(T["pnl"]), _col(T["pnl"])),
        _kpi("Day P&L", _short(T["day"]), _pct(T["day_pct"]), _col(T["day"]), _col(T["day"]), _col(T["day"])),
        _kpi("Realised P&L", _short(A["realised_total"]), f'{A["realised_n"]} matched sells', _col(A["realised_total"]), T2, PURPLE),
        _kpi("XIRR", _pct(xi * 100, 1) if xi is not None else "—", f'money-weighted · {A["xirr_days"]}d' if xi is not None else "needs 60+ days of history",
             _col(xi) if xi is not None else T3, T2, PURPLE),
        _kpi("Dividends (est.)", _short(A["div_total"]), "gross, since you bought", GREEN, T2, GREEN),
        _kpi("Portfolio heat", _pct(A["heat"], 1, False) if A["heat"] is not None else "—",
             "₹ at risk to stops ÷ value" if A["heat"] is not None else "set stop-losses in HOLDINGS", AMBER if (A["heat"] or 0) > 6 else IVORY, T2, RED),
    ])
    if T["n_valid"] < T["n"]:
        st.caption("Some holdings have no live price right now and are excluded from the totals.")
    c1, c2 = st.columns([2.2, 1])
    with c1:
        _ph("Growth of your money", f"holdings value vs net invested vs {bench_label} with identical buys and sells")
        tl = A["timeline"]()
        if tl is None:
            _note("The growth chart needs lots with a buy date and price history.")
        else:
            _plot(_fig_growth, "pf_growth", tl, bench_label)
            last = tl.iloc[-1]
            line = f'You: <b style="color:{AMBER}">{_short(last["Value"])}</b> · net invested {_short(last["Net invested"])}'
            if "Benchmark" in tl:
                diff = last["Value"] - last["Benchmark"]
                line += f' · {bench_label} equivalent <b style="color:{CYAN}">{_short(last["Benchmark"])}</b> · difference <b style="color:{_col(diff)}">{_short(diff)}</b>'
            st.markdown(f'<div class="pf-mini">{line}</div>', unsafe_allow_html=True)
    with c2:
        _ph("Portfolio health", "diversification score")
        if A["score"] is not None:
            _plot(_fig_gauge, "pf_gauge", A["score"])
            s = A["score"]
            lab, c = ("WELL DIVERSIFIED", GREEN) if s >= 75 else ("MODERATE", AMBER) if s >= 50 else ("CONCENTRATED", RED)
            st.markdown(f'<div class="pf-score" style="color:{c}">{lab}</div>', unsafe_allow_html=True)
        st.markdown(_flags_html(A["flags"], 4), unsafe_allow_html=True)
    d1, d2, d3 = st.columns(3)
    with d1:
        _ph("Sector allocation")
        _plot(_fig_donut, "pf_donut", A["sectors"], A["scol"], T["val"])
    with d2:
        _ph("Top positions", "% of portfolio")
        rows = [h for h in holds if h["weight"]][:10]
        _plot(_fig_hbars, "pf_topw", [h["symbol"] for h in rows], [h["weight"] for h in rows], [A["scol"].get(h["sector"], T3) for h in rows])
    with d3:
        _ph("Today's movers")
        st.markdown(_movers_html(holds), unsafe_allow_html=True)
    st.caption("Informational only, not investment advice. P&L excludes brokerage, taxes and charges.")


def _view_holdings(supabase, store, D, A, holds, alots, news, on_open, book_filter):
    scol = A["scol"]
    c1, c2, c3, c4 = st.columns([1.3, 1.6, 1.4, 1.2])
    sorts = {"Value ↓": lambda h: (h["value"] is None, -(h["value"] or 0)), "P&L ₹ ↓": lambda h: (h["pnl"] is None, -(h["pnl"] or 0)),
             "P&L ₹ ↑": lambda h: (h["pnl"] is None, h["pnl"] or 0), "P&L % ↓": lambda h: (h["pnl_pct"] is None, -(h["pnl_pct"] or 0)),
             "P&L % ↑": lambda h: (h["pnl_pct"] is None, h["pnl_pct"] or 0), "Day % ↓": lambda h: (h["chg"] is None, -(h["chg"] or 0)),
             "Day % ↑": lambda h: (h["chg"] is None, h["chg"] or 0), "Risk to SL ↓": lambda h: (h["risk"] is None, -(h["risk"] or 0)),
             "Sector": lambda h: (h["sector"], -(h["value"] or 0)), "A–Z": lambda h: h["symbol"]}
    sort = c1.selectbox("Sort by", list(sorts), key="pf_sort", label_visibility="collapsed")
    sec_f = c2.selectbox("Sector", ["All sectors"] + sorted({h["sector"] for h in holds}), key="pf_sector_filter", label_visibility="collapsed")
    q = c3.text_input("Find", key="pf_find", placeholder="Find stock…", label_visibility="collapsed").strip().upper()
    export = pd.DataFrame([{"Symbol": h["symbol"], "Name": h["name"], "Books": ", ".join(h["books"]), "Sector": h["sector"],
                            "Industry": h["industry"], "Quantity": round(h["qty"], 4), "Avg cost": round(h["avg"], 2), "LTP": h["ltp"],
                            "Invested": round(h["invested"], 2), "Value": None if h["value"] is None else round(h["value"], 2),
                            "P&L": None if h["pnl"] is None else round(h["pnl"], 2), "P&L %": None if h["pnl_pct"] is None else round(h["pnl_pct"], 2),
                            "Weight %": None if h["weight"] is None else round(h["weight"], 2), "Stop-loss": h["sl"], "Target": h["target"],
                            "Risk to SL": h["risk"], "Sector stage": h["stage"]} for h in holds])
    c4.download_button("⤓ EXPORT CSV", export.to_csv(index=False), file_name="arka_portfolio.csv", mime="text/csv", key="pf_csv", use_container_width=True)
    rows = [h for h in holds if (sec_f == "All sectors" or h["sector"] == sec_f) and (not q or q in h["symbol"])]
    rows = sorted(rows, key=sorts[sort])
    trs = []
    for x in rows:
        arts = news.get(x["symbol"]) or []
        dot = f'<span class="pf-dot" title="{_esc(str(len(arts)) + " headlines · latest: " + arts[0].get("title", ""), True)}"></span>' if arts else ""
        books = "".join(f'<span class="pf-chip" style="--c:{T2}">{_esc(b)}</span>' for b in x["books"]) if book_filter == "ALL BOOKS" else ""
        adj = f'<span class="pf-chip" style="--c:{CYAN}" title="Adjusted for splits / bonus">ADJ</span>' if x["split_adj"] else ""
        name = f'<div class="sub">{_esc(x["name"][:34])}</div>' if x["name"] else ""
        chip = f'<span class="pf-chip" style="--c:{scol.get(x["sector"], T3)}">{_esc(x["sector"][:22])}</span>'
        w = x["weight"]
        wbar = f'<span class="pf-wbar"><i style="width:{min(100.0, w * 2):.0f}%;background:{scol.get(x["sector"], AMBER)}"></i></span>{w:.1f}%' if w is not None else "—"
        if x["sl"] is not None:
            slc = RED if x["sl_hit"] else T2
            plan = (f'<span style="color:{slc}">SL {_inr(x["sl"])}</span><div class="sub">'
                    f'{"STOP HIT" if x["sl_hit"] else "risk " + _short(x["risk"])}{" · R " + format(x["r_mult"], ".1f") if x["r_mult"] is not None else ""}</div>')
        else:
            plan = '<span style="color:#444">set in DETAILS</span>'
        tgt = (f'{_inr(x["target"])}<div class="sub">{"+" + _short(x["reward"]) if x["reward"] else "reached"}</div>' if x["target"] is not None else "—")
        tone = x.get("tone")
        tone_html = (f'<span style="color:{GREEN if tone > 0.3 else RED if tone < -0.3 else T2}">{x["news_n"]} · {tone:+.1f}</span>'
                     if tone is not None else '<span style="color:#444">—</span>')
        fs = x.get("fscore")
        fs_html = (f'<span style="color:{GREEN if fs[0] / fs[1] >= 0.75 else AMBER if fs[0] / fs[1] >= 0.45 else RED}">{fs[0]}/{fs[1]}</span>' if fs else '<span style="color:#444">—</span>')
        trs.append([f'<span class="pf-sym">{_esc(x["symbol"])}</span>{dot}{name}{chip}{books}{adj}', _qty(x["qty"]), _inr(x["avg"]), _inr(x["ltp"]),
                    f'<span style="color:{_col(x["chg"])}">{_pct(x["chg"])}</span>', _inr(x["invested"], 0), _inr(x["value"], 0),
                    f'<span style="color:{_col(x["pnl"])}">{_inr(x["pnl"], 0)}</span><div class="sub" style="color:{_col(x["pnl"])}">{_pct(x["pnl_pct"])}</div>',
                    wbar, plan, tgt, _stage_pill(x["stage"]), tone_html, fs_html])
    _table(["Stock", "Qty", "Avg cost", "LTP", "Day", "Invested", "Value", "P&L", "Weight", "Stop-loss", "Target", "Sector stage", "News", "Quality"],
           trs, right={1, 2, 3, 4, 5, 6, 7})
    st.caption("Yellow dot = fresh headlines. Sector stage comes from Money Flow → Sector Ranking (load it there once per session). "
               "Quality = Piotroski-style score from Research. ADJ = split / bonus adjusted. P&L excludes charges.")
    with st.container(key="pf_act"):
        a1, a2, a3 = st.columns([3, 1, 1])
        pick = a1.selectbox("Selected holding", [h["symbol"] for h in rows] or [h["symbol"] for h in holds], key="pf_pick", label_visibility="collapsed")
        if a2.button("OPEN ▸", key="pf_open", use_container_width=True, type="primary") and pick:
            on_open(pick)
        if a3.button("DETAILS / MANAGE", key="pf_det", use_container_width=True) and pick:
            st.session_state["pf_sel"] = pick
    if st.session_state.get("pf_sel"):
        _detail_panel(supabase, store, D, holds, alots, book_filter)


def _detail_panel(supabase, store, D, holds, alots, book_filter):
    sym = st.session_state.get("pf_sel")
    h = next((x for x in holds if x["symbol"] == sym), None)
    if not h:
        st.session_state.pop("pf_sel", None)
        return
    _ph(f"{sym} · lots, plan & management", " · ".join(h["books"]) + " · " + h["sector"])
    top = st.columns([6, 1])
    top[0].markdown(f'<div class="pf-note"><b>{_esc(h["name"] or sym)}</b> · {_qty(h["qty"])} shares · average cost {_inr(h["avg"])} · '
                    f'invested {_inr(h["invested"], 0)} · value {_inr(h["value"], 0)} · P&L <span style="color:{_col(h["pnl"])}">{_inr(h["pnl"], 0)} ({_pct(h["pnl_pct"])})</span>'
                    f'{" · <b>split / bonus adjusted</b>" if h["split_adj"] else ""}</div>', unsafe_allow_html=True)
    if top[1].button("CLOSE", key="pf_det_close", use_container_width=True):
        st.session_state.pop("pf_sel", None)
        st.rerun()
    today = _today()
    rows = []
    for l in h["lots"]:
        lp = (h["ltp"] - l["price"]) * l["open"] if h["ltp"] is not None else None
        lpp = (h["ltp"] / l["price"] - 1) * 100 if (h["ltp"] is not None and l["price"]) else None
        lt_on = _add_months(l["date"], 12) if l["date"] else None
        term = "—" if not lt_on else ("LONG-TERM" if today > lt_on else f"LT on {lt_on:%d %b %y}")
        adj = f'<div class="sub">entered {_qty(l["raw_qty"])} @ {_inr(l["raw_price"])} · ×{l["factor"]:g}</div>' if l["factor"] != 1 else ""
        rows.append([_esc(l["date"].isoformat() if l["date"] else "—"), _esc(l["book"]), _qty(l["open"]) + (f'<div class="sub">of {_qty(l["qty"])}</div>' if l["open"] < l["qty"] - 1e-9 else ""),
                     _inr(l["price"]) + adj, _inr(l["open"] * l["price"], 0), f'<span style="color:{_col(lp)}">{_inr(lp, 0)}</span>',
                     f'<span style="color:{_col(lpp)}">{_pct(lpp, 1)}</span>', f'{(today - l["date"]).days}d' if l["date"] else "—", term, _esc(l["note"])])
    _table(["Bought", "Book", "Open qty", "Cost / share", "Cost", "Lot P&L", "Lot %", "Held", "Tax term", "Note"], rows, right={2, 3, 4, 5, 6, 7})

    p1, p2 = st.columns(2)
    with p1:
        _ph("Trade plan", "stop-loss and target for this holding")
        plan_book = h["books"][0] if len(h["books"]) == 1 else (book_filter if book_filter != "ALL BOOKS" else h["books"][0])
        with st.form(f"pf_plan_{sym}"):
            a, b = st.columns(2)
            sl = a.number_input("Stop-loss (₹)", min_value=0.0, value=float(h["sl"] or 0.0), step=0.05, format="%.2f")
            tg = b.number_input("Target (₹)", min_value=0.0, value=float(h["target"] or 0.0), step=0.05, format="%.2f")
            st.caption(f"Saved for book: {plan_book}. Enter 0 to clear.")
            if st.form_submit_button("SAVE PLAN", type="primary", use_container_width=True):
                if sl and tg and tg <= sl:
                    st.error("Target must be above the stop-loss.")
                else:
                    err = store.upsert("portfolio_plan", {"book": plan_book, "symbol": sym, "stop_loss": sl or None, "target": tg or None})
                    st.error(err) if err else st.rerun()
        if h["sl"] is not None and h["ltp"] is not None:
            rr = (h["target"] - h["ltp"]) / (h["ltp"] - h["sl"]) if (h["target"] and h["ltp"] > h["sl"]) else None
            _note(f'Distance to stop <b>{_pct((h["sl"] / h["ltp"] - 1) * 100, 1)}</b> · ₹ at risk <b>{_short(h["risk"])}</b>'
                  + (f' · reward:risk <b>{rr:.2f}</b>' if rr is not None else "") + (f' · open R-multiple <b>{h["r_mult"]:.2f}</b>' if h["r_mult"] is not None else ""))
    with p2:
        _ph("Move a lot to another book")
        books = _book_options(D)
        lot_labels = [f'{l["date"] or "—"} · {_qty(l["open"])} @ {_inr(l["price"])} · {l["book"]}' for l in h["lots"]]
        with st.form(f"pf_move_{sym}"):
            li = st.selectbox("Lot", range(len(lot_labels)), format_func=lambda i: lot_labels[i])
            nb = st.selectbox("New book", books)
            if st.form_submit_button("MOVE LOT", use_container_width=True):
                l = h["lots"][li]
                if any(s["symbol"] == sym and s["book"] == l["book"] for s in D["sells"]):
                    st.warning("This position has recorded sells in its current book; they stay there. Check TRANSACTIONS afterwards.")
                err = store.upsert("portfolio_lot_meta", {"lot_id": str(l["id"]), "book": nb})
                st.error(err) if err else st.rerun()
    s1, s2 = st.columns(2)
    with s1:
        _ph(f"Buy more {sym}")
        _add_buy_form(supabase, store, D, key=f"more_{sym}", preset=sym, book=h["books"][0])
    with s2:
        _ph(f"Sell {sym}")
        _sell_form(store, D, alots, key=f"sell_{sym}", preset_sym=sym, preset_book=h["books"][0] if len(h["books"]) == 1 else None)
    with st.expander("Danger zone · delete lots"):
        li = st.selectbox("Lot to delete", range(len(h["lots"])), format_func=lambda i: lot_labels[i], key=f"pf_del_pick_{sym}")
        if st.button("DELETE THIS LOT", key=f"pf_del_lot_{sym}"):
            err = ud.delete_lot(supabase, h["lots"][li]["id"])
            if err:
                st.error(err)
            else:
                store.delete("portfolio_lot_meta", lot_id=str(h["lots"][li]["id"]))
                st.rerun()
        sure = st.checkbox(f"Yes, delete every {sym} lot shown here ({len(h['lots'])}) and its recorded sells in these books", key=f"pf_sure_{sym}")
        if st.button(f"REMOVE {sym} FROM {' / '.join(h['books']).upper()}", disabled=not sure, key=f"pf_rm_all_{sym}"):
            errs = [e for e in (ud.delete_lot(supabase, l["id"]) for l in h["lots"]) if e]
            for l in h["lots"]:
                store.delete("portfolio_lot_meta", lot_id=str(l["id"]))
            for s in D["sells"]:
                if s["symbol"] == sym and s["book"] in h["books"]:
                    store.delete("portfolio_sells", id=s["id"])
            if errs:
                st.error("; ".join(errs))
            else:
                st.session_state.pop("pf_sel", None)
                st.rerun()


def _view_analysis(A, holds):
    T, sectors, scol = A["tot"], A["sectors"], A["scol"]
    _ph("Sector & industry exposure", "tile size = value · colour = P&L % · stage = Money Flow rotation")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        _plot(_fig_treemap, "pf_treemap", holds, sectors)
    with c2:
        _table(["Sector", "Stocks", "Value", "Weight", "P&L", "Stage"], _group_rows(sectors, scol), right={1, 2, 4})
    with st.expander("Industry breakdown"):
        _table(["Industry", "Stocks", "Value", "Weight", "P&L", "Stage"], _group_rows(A["industries"]), right={1, 2, 4})
    _ph("Composition")
    conc = A["conc"]
    if conc:
        _kpis([_kpi("Top 1", _num(conc["top1"], 1, "%"), "largest position"), _kpi("Top 3", _num(conc["top3"], 1, "%"), "of portfolio"),
               _kpi("Top 5", _num(conc["top5"], 1, "%"), "of portfolio"), _kpi("Effective N", _num(conc["eff_n"], 1), f'behaves like · of {conc["n"]}', accent=CYAN)])
    d1, d2 = st.columns(2)
    with d1:
        st.markdown('<div class="pf-mini">MARKET-CAP MIX (approximate AMFI buckets)</div>', unsafe_allow_html=True)
        mc = [m for m in A["mcap"] if m["value"] > 0]
        cols = {"Large cap": CYAN, "Mid cap": AMBER, "Small cap": PURPLE, "Unknown": UNCLASSIFIED_COLOR}
        _plot(_fig_hbars, "pf_mcap", [m["name"] for m in mc], [m["weight"] for m in mc], [cols[m["name"]] for m in mc])
    with d2:
        st.markdown('<div class="pf-mini">UNREALISED P&L BY HOLDING</div>', unsafe_allow_html=True)
        pv = sorted([h for h in holds if h["pnl"] is not None], key=lambda h: -h["pnl"])[:15]
        _plot(_fig_hbars, "pf_pnlbars", [h["symbol"] for h in pv], [h["pnl"] for h in pv], [_col(h["pnl"]) for h in pv], "")
    _ph("Valuation & quality", "value-weighted across holdings with data")
    F = A["fund"]
    cov = lambda t: f"{t[1]:.0f}% of value covered"
    _kpis([_kpi("Weighted P/E", _num(F["pe"][0], 1, "x"), cov(F["pe"]), accent=CYAN), _kpi("Weighted ROE", _num(F["roe"][0], 1, "%"), cov(F["roe"]), accent=GREEN),
           _kpi("Weighted ROCE", _num(F["roce"][0], 1, "%"), cov(F["roce"]), accent=GREEN), _kpi("Dividend yield", _num(F["dy"][0], 2, "%"), cov(F["dy"])),
           _kpi("Avg quality", _num(A["avg_fscore"], 0, "%") if A["avg_fscore"] is not None else "—", "Piotroski-style, value-weighted", accent=PURPLE)])
    rows = []
    for h in holds:
        pos = None
        if h["ltp"] is not None and h["hi52"] and h["lo52"] and h["hi52"] > h["lo52"]:
            pos = max(0.0, min(1.0, (h["ltp"] - h["lo52"]) / (h["hi52"] - h["lo52"])))
        rng = f'<span class="pf-rng"><i style="left:{pos * 100:.0f}%"></i></span> <span class="pf-mini">{pos * 100:.0f}%</span>' if pos is not None else "—"
        fs = h.get("fscore")
        rows.append([f'<b style="color:{AMBER}">{_esc(h["symbol"])}</b><div class="sub">{_esc(h["sector"])}</div>', _num(h["pe"], 1, "x"), _num(h["roe"], 1, "%"),
                     _num(h["roce"], 1, "%"), _num(h["dy"], 2, "%"), "—" if h["mcap"] is None else f'₹{h["mcap"]:,.0f} Cr',
                     f"{fs[0]}/{fs[1]}" if fs else "—", rng, _num(h["weight"], 1, "%")])
    _table(["Stock", "P/E", "ROE", "ROCE", "Div yld", "Mkt cap", "Quality", "52W position", "Weight"], rows, right={1, 2, 3, 4, 5, 6, 8})
    st.caption("P/E is blended as a harmonic mean (the correct way to average P/E); loss-making companies are excluded from it.")
    _ph("Health check", "automatic review")
    st.markdown(_flags_html(A["flags"]), unsafe_allow_html=True)
    with st.expander("How the diversification score works"):
        st.markdown("Starts at 100 and loses points for: largest position above 10% (up to 30), largest sector above 25% (up to 30), "
                    "fewer than 10 effective equal-weight stocks (up to 25) and average correlation above 0.5 (up to 15). Rules of thumb, not recommendations.")


def _view_performance(A, holds, store, D, bench_label):
    tl = A["timeline"]()
    if tl is None:
        _note("Performance needs lots with buy dates and price history.")
        return
    r = tl["r"]
    days = len(r)
    twr = float((1 + r).prod() - 1) * 100
    ann = ((1 + twr / 100) ** (252 / days) - 1) * 100 if days > 250 else None
    btw = float((1 + tl["rb"]).prod() - 1) * 100 if "rb" in tl else None
    xi = A["xirr"]
    _kpis([_kpi("Time-weighted return", _pct(twr, 1), f"since {tl.index[0]:%d %b %Y}", _col(twr), T2, AMBER),
           _kpi("Annualised (TWR)", _pct(ann, 1) if ann is not None else "—", "needs 1+ year" if ann is None else "per year", _col(ann) if ann else T3, T2, AMBER),
           _kpi(f"{bench_label} same period", _pct(btw, 1) if btw is not None else "—", "price index", CYAN, T2, CYAN),
           _kpi("Excess vs benchmark", _pct(twr - btw, 1) if btw is not None else "—", "time-weighted", _col(twr - btw) if btw is not None else T3, T2, GREEN),
           _kpi("XIRR (money-weighted)", _pct(xi * 100, 1) if xi is not None else "—", "your actual cash flows", _col(xi) if xi is not None else T3, T2, PURPLE)])
    c1, c2 = st.columns([1.4, 1])
    with c1:
        _ph("Growth of your money", f"vs {bench_label} bought and sold with the same cash")
        _plot(_fig_growth, "pf_growth_perf", tl, bench_label)
    with c2:
        _ph("Time-weighted index (start = 100)", "removes the effect of when you added money")
        _plot(_fig_twr, "pf_twr", tl, bench_label)
    _ph("Monthly returns", "time-weighted, %")
    m = monthly_returns(r)
    if len(m) >= 1:
        _plot(_fig_heat_months, "pf_months", m)
        if "rb" in tl:
            mb = monthly_returns(tl["rb"])
            beat = int(((m - mb.reindex(m.index)) > 0).sum())
            st.caption(f"Beat {bench_label} in {beat} of {len(m)} months (partial first / current months included).")
    _ph("What drove your returns", "unrealised + realised + estimated dividends, by stock and by sector")
    contrib = defaultdict(float)
    secmap = {h["symbol"]: h["sector"] for h in holds}
    for h in holds:
        contrib[h["symbol"]] += h["pnl"] or 0.0
    for x in A["realised"]:
        contrib[x["symbol"]] += x["gain"]
    for d in A["divs"]:
        contrib[d["symbol"]] += d["amount"]
    if contrib:
        items = sorted(contrib.items(), key=lambda kv: -kv[1])
        show = items[:8] + [kv for kv in items[-6:] if kv not in items[:8]]
        a, b = st.columns(2)
        with a:
            _plot(_fig_hbars, "pf_contrib", [k for k, _ in show], [v for _, v in show], [_col(v) for _, v in show], "")
        with b:
            bysec = defaultdict(float)
            for k, v in contrib.items():
                bysec[secmap.get(k) or (A["infos"].get(k) or {}).get("sector") or UNCLASSIFIED] += v
            sv = sorted(bysec.items(), key=lambda kv: -kv[1])
            _plot(_fig_hbars, "pf_contrib_sec", [k for k, _ in sv], [v for _, v in sv], [_col(v) for _, v in sv], "")
    _ph("Recorded history", "a daily snapshot is saved when you open this page")
    snaps = [s for s in D["snaps"] if s.get("book") == A["book_key"]]
    if len(snaps) >= 2 and go is not None:
        sdf = pd.DataFrame(snaps)
        sdf["snap_date"] = pd.to_datetime(sdf["snap_date"])
        sdf = sdf.sort_values("snap_date")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=sdf["snap_date"], y=pd.to_numeric(sdf["value"]), name="Value", line=dict(color=AMBER, width=2)))
        fig.add_trace(go.Scatter(x=sdf["snap_date"], y=pd.to_numeric(sdf["invested"]), name="Invested (open)", line=dict(color=T2, dash="dot")))
        fig.add_trace(go.Scatter(x=sdf["snap_date"], y=pd.to_numeric(sdf["realised"]), name="Realised P&L", line=dict(color=PURPLE, width=1.4)))
        _layout(fig, 240, (0, 6, 6, 0), legend=True)
        fig.update_layout(legend=dict(orientation="h", y=1.1, x=0), hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="pf_snaps")
    else:
        _note(f"{len(snaps)} snapshot(s) recorded so far. The curve appears from the second day you open the portfolio"
              + (" — create the new tables (TRANSACTIONS → Storage) to keep it permanently." if "portfolio_snapshots" in store.missing else "."))


def _view_risk(A, holds, bench_label):
    R = A["risk"]
    _ph("Risk", "today's weights over the last ~1 year of daily returns")
    if R is None:
        _note("Risk metrics need about 3 months of price history for your holdings.")
        return
    _kpis([_kpi("Volatility", _num(R["vol"], 1, "%"), "annualised"), _kpi("Max drawdown", _pct(R["mdd"], 1, False), "peak to trough", RED, T2, RED),
           _kpi("1-day VaR (95%)", _short(R["var95"]), "historical, worst 1-in-20 day", accent=PURPLE),
           _kpi("Beta", _num(R["beta"], 2), "vs NIFTY 50", accent=CYAN),
           _kpi("1Y basket return", _pct(R["ret1y"], 1), f'NIFTY 50 {_pct(R["bench_ret"], 1)}' if R["bench_ret"] is not None else "", _col(R["ret1y"]), T2, GREEN)])
    tot = A["tot"]["val"] or 0
    _ph("Stop-loss risk", "what you lose if every stop is hit")
    with_sl = [h for h in holds if h["sl"] is not None and h["value"]]
    if with_sl:
        risk_sum = sum(h["risk"] or 0 for h in with_sl)
        cover = sum(h["value"] for h in with_sl) / tot * 100 if tot else 0
        _kpis([_kpi("₹ at risk to stops", _short(risk_sum), f"{risk_sum / tot * 100:.1f}% of portfolio" if tot else "", RED, T2, RED),
               _kpi("Value covered by stops", _pct(cover, 0, False), f"{len(with_sl)} of {len(holds)} holdings"),
               _kpi("Stops breached", str(sum(h["sl_hit"] for h in holds)), "price at / below stop", RED if any(h["sl_hit"] for h in holds) else IVORY, T2, RED)])
        rows = [[f'<b style="color:{AMBER}">{_esc(h["symbol"])}</b>', _inr(h["ltp"]), _inr(h["sl"]), _pct((h["sl"] / h["ltp"] - 1) * 100, 1),
                 f'<span style="color:{RED}">{_short(h["risk"])}</span>', _num(h["r_mult"], 2), _inr(h["target"]) if h["target"] else "—",
                 _num((h["target"] - h["ltp"]) / (h["ltp"] - h["sl"]), 2) if (h["target"] and h["ltp"] > h["sl"]) else "—"]
                for h in sorted(with_sl, key=lambda h: -(h["risk"] or 0))]
        _table(["Stock", "LTP", "Stop", "Distance", "₹ at risk", "Open R", "Target", "Reward:risk"], rows, right={1, 2, 3, 4, 5, 6, 7})
    else:
        _note("No stop-losses set. Open HOLDINGS → select a stock → DETAILS / MANAGE → Trade plan.")
    _ph("Stress test", "estimated using each stock's beta to NIFTY 50 over the last year")
    betas = R["betas"]
    c1, c2, c3 = st.columns([1.2, 1.4, 1])
    mkt = c1.slider("NIFTY 50 move (%)", -30, 20, -10, 1, key="pf_st_mkt")
    secs = ["(none)"] + sorted({h["sector"] for h in holds if h["sector"] != UNCLASSIFIED})
    sec = c2.selectbox("Extra sector shock", secs, key="pf_st_sec")
    sh = c3.slider("Sector move (%)", -40, 20, -15, 1, key="pf_st_shock", disabled=sec == "(none)")
    rows, total_imp = [], 0.0
    for h in holds:
        if not h["value"]:
            continue
        b = betas.get(h["symbol"], 1.0)
        move = b * mkt + (sh if h["sector"] == sec else 0)
        imp = h["value"] * move / 100
        total_imp += imp
        rows.append((h, b, move, imp))
    _kpis([_kpi("Scenario impact", _short(total_imp), _pct(total_imp / tot * 100 if tot else 0, 1), _col(total_imp), _col(total_imp), RED),
           _kpi("Portfolio after", _short(tot + total_imp), "estimated value"),
           _kpi("Stocks without beta", str(sum(1 for h in holds if h["value"] and h["symbol"] not in betas)), "assumed beta 1.0")])
    rows.sort(key=lambda x: x[3])
    _table(["Stock", "Beta", "Est. move", "Impact"], [[f'<b style="color:{AMBER}">{_esc(h["symbol"])}</b><div class="sub">{_esc(h["sector"])}</div>',
                                                     f"{b:.2f}", f'<span style="color:{_col(m)}">{_pct(m, 1)}</span>', f'<span style="color:{_col(i)}">{_short(i)}</span>']
                                                    for h, b, m, i in rows[:15]], right={1, 2, 3})
    nifty = A["nifty"]
    port = R.get("port")
    if nifty is not None and port is not None:
        _ph("Historical replay", "the worst NIFTY 50 day and month of the last 5 years, applied to today's portfolio")
        nr = nifty.pct_change().dropna().tail(1250)
        worst_day = nr.idxmin()
        m = nifty.resample("ME").last().pct_change().dropna().tail(60) if True else None
        worst_m = m.idxmin()
        out = []
        for label, n_move, when in (("Worst day", nr.min() * 100, f"{worst_day:%d %b %Y}"), ("Worst month", m.min() * 100, f"{worst_m:%b %Y}")):
            est = sum(h["value"] * betas.get(h["symbol"], 1.0) * n_move / 100 for h in holds if h["value"])
            out.append([label, when, f'<span style="color:{RED}">{_pct(n_move, 1)}</span>', f'<span style="color:{_col(est)}">{_short(est)}</span>',
                        _pct(est / tot * 100 if tot else 0, 1)])
        _table(["Scenario", "When", "NIFTY 50", "Your est. impact", "% of portfolio"], out, right={2, 3, 4})
    if R["corr"] is not None:
        _ph("Correlation of top holdings", "closer to 1.00 = move together")
        _plot(_fig_corr, "pf_corr", R["corr"])
        if R["top_pair"]:
            a, b, c = R["top_pair"]
            st.markdown(f'<div class="pf-mini">Most correlated pair: <b style="color:{AMBER}">{_esc(a)}</b> and <b style="color:{AMBER}">{_esc(b)}</b> '
                        f'({c:.2f}) · average {R["avg_corr"]:.2f}</div>', unsafe_allow_html=True)
    st.caption("Beta-based scenarios are linear estimates; real crashes are often worse for small caps and better for defensives.")


def _view_rebalance(A, holds, store, D):
    rules = {**REB_DEFAULTS, **(D["settings"].get("rebalance") or {})}
    _ph("Rebalance assistant", "rules → exact quantities to get back inside them")
    with st.form("pf_reb"):
        c1, c2, c3, c4 = st.columns(4)
        ms = c1.number_input("Max per stock (%)", 2.0, 100.0, float(rules["max_stock"]), 0.5)
        mx = c2.number_input("Max per sector (%)", 5.0, 100.0, float(rules["max_sector"]), 1.0)
        cash = c3.number_input("New cash to invest (₹)", 0.0, value=float(rules.get("cash") or 0.0), step=1000.0)
        red = c4.checkbox("Redeploy freed cash", value=bool(rules["redeploy"]), help="Put trimmed cash (and new cash) into holdings that are under the caps")
        if st.form_submit_button("BUILD PLAN", type="primary", use_container_width=True):
            rules = {"max_stock": ms, "max_sector": mx, "cash": cash, "redeploy": red}
            store.upsert("portfolio_settings", {"key": "rebalance", "value": rules})
            D["settings"]["rebalance"] = rules
    plan, left = rebalance_plan(holds, rules)
    if not plan:
        _note(f"Everything is already inside the rules (max {rules['max_stock']:.1f}% per stock, {rules['max_sector']:.0f}% per sector). Nothing to do.")
        return
    rows, st_tot, lt_tot = [], 0.0, 0.0
    for p in plan:
        tax = ""
        if p["action"] == "SELL":
            sg, lg = fifo_preview(p["h"], p["qty"], p["ltp"])
            st_tot += sg
            lt_tot += lg
            tax = f'<span style="color:{_col(sg + lg)}">{_short(sg + lg)}</span><div class="sub">ST {_short(sg)} · LT {_short(lg)}</div>'
        c = RED if p["action"] == "SELL" else GREEN
        rows.append([f'<b style="color:{AMBER}">{_esc(p["symbol"])}</b><div class="sub">{_esc(p["sector"])}</div>', f'<span class="pf-pill" style="color:{c}">{p["action"]}</span>',
                     _qty(p["qty"]), _inr(p["ltp"]), _short(p["amount"]), f'{p["now_w"]:.1f}%', f'{p["new_w"]:.1f}%', tax or "—"])
    sells = sum(p["amount"] for p in plan if p["action"] == "SELL")
    buys = sum(p["amount"] for p in plan if p["action"] == "BUY")
    rates = {**TAX_DEFAULTS, **(D["settings"].get("tax") or {})}
    est_tax = (max(0, st_tot) * rates["stcg"] + max(0, lt_tot - A["ltcg_headroom"]) * rates["ltcg"]) / 100 * (1 + rates["cess"] / 100)
    _kpis([_kpi("Sell", _short(sells), f'{sum(p["action"] == "SELL" for p in plan)} trades', RED, T2, RED),
           _kpi("Buy", _short(buys), f'{sum(p["action"] == "BUY" for p in plan)} trades', GREEN, T2, GREEN),
           _kpi("Cash left over", _short(left), "after the plan"),
           _kpi("Gains realised by trims", _short(st_tot + lt_tot), f"ST {_short(st_tot)} · LT {_short(lt_tot)}", _col(st_tot + lt_tot)),
           _kpi("Estimated tax on trims", _short(est_tax), "after this FY's LTCG headroom", AMBER, T2, AMBER)])
    _table(["Stock", "Action", "Qty", "Price", "Amount", "Weight now", "Weight after", "Gain realised (FIFO)"], rows, right={2, 3, 4, 5, 6})
    st.caption("Quantities are whole shares at the last price. Sells use FIFO, matching how gains are taxed. A planning aid, not advice — "
               "consider brokerage, STT and whether the reason you own each stock still holds.")


def _view_income(A, holds):
    divs = A["divs"]
    tot_inv = A["tot"]["inv"] or 0
    by_fy = defaultdict(float)
    for d in divs:
        by_fy[d["fy"]] += d["amount"]
    cur_fy = _fy(_today())
    ttm = sum(d["amount"] for d in divs if d["ex_date"] >= _today() - timedelta(days=365))
    _ph("Dividend income", "estimated from Yahoo's dividend history and the dates you held each lot (gross, before TDS)")
    _kpis([_kpi("Total received (est.)", _short(sum(by_fy.values())), f"{len(divs)} payouts", GREEN, T2, GREEN),
           _kpi("Last 12 months", _short(ttm), "gross"),
           _kpi(f"This year ({cur_fy})", _short(by_fy.get(cur_fy, 0.0)), "financial year"),
           _kpi("Yield on cost (12M)", _pct(ttm / tot_inv * 100 if tot_inv else None, 2, False), "on open invested amount", accent=AMBER)])
    if divs:
        a, b = st.columns([1, 1.4])
        with a:
            fy = sorted(by_fy.items())
            _plot(_fig_hbars, "pf_div_fy", [k for k, _ in fy], [v for _, v in fy], [GREEN] * len(fy), "")
        with b:
            per = defaultdict(float)
            for d in divs:
                per[d["symbol"]] += d["amount"]
            rows = []
            for sym, amt in sorted(per.items(), key=lambda kv: -kv[1]):
                h = next((x for x in holds if x["symbol"] == sym), None)
                yoc = amt / h["invested"] * 100 if h and h["invested"] else None
                rows.append([f'<b style="color:{AMBER}">{_esc(sym)}</b>', _short(amt), _pct(yoc, 1, False) if yoc is not None else "sold",
                             max(d["ex_date"] for d in divs if d["symbol"] == sym).strftime("%d %b %Y")])
            _table(["Stock", "Received (est.)", "Total yield on cost", "Last ex-date"], rows, right={1, 2})
    else:
        _note("No dividends found for the periods you held these stocks.")
    _ph("Upcoming events", "results and ex-dividend dates published by Yahoo (often incomplete for NSE)")
    if st.button("LOAD EVENT CALENDAR", key="pf_ev_load"):
        st.session_state["pf_ev"] = True
    if st.session_state.get("pf_ev") and yf_attr:
        ev = []
        with st.spinner("Checking calendars…"):
            with _pool(6) as ex:
                for h, cal in zip(holds, ex.map(lambda x: yf_attr(x["symbol"], "calendar"), holds)):
                    if not isinstance(cal, dict):
                        continue
                    for k, label in (("Earnings Date", "Results"), ("Ex-Dividend Date", "Ex-dividend"), ("Dividend Date", "Dividend paid")):
                        v = cal.get(k)
                        for x in (v if isinstance(v, (list, tuple)) else [v]):
                            dd = _d(x)
                            if dd and _today() <= dd <= _today() + timedelta(days=120):
                                ev.append((dd, h["symbol"], label))
        if ev:
            ev.sort()
            _table(["Date", "Stock", "Event", "In"], [[d.strftime("%a %d %b %Y"), f'<b style="color:{AMBER}">{_esc(s)}</b>', l, f"{(d - _today()).days} days"]
                                                      for d, s, l in ev], right={3})
        else:
            _note("No upcoming results or dividend dates were published for your holdings in the next 120 days.")


def _view_tax(A, store, D):
    rates = {**TAX_DEFAULTS, **(D["settings"].get("tax") or {})}
    with st.expander("Tax assumptions (editable)", expanded=False):
        with st.form("pf_tax"):
            c1, c2, c3, c4 = st.columns(4)
            r1 = c1.number_input("STCG rate %", 0.0, 50.0, float(rates["stcg"]), 0.5)
            r2 = c2.number_input("LTCG rate %", 0.0, 50.0, float(rates["ltcg"]), 0.5)
            r3 = c3.number_input("LTCG exemption ₹ / FY", 0.0, value=float(rates["exempt"]), step=5000.0)
            r4 = c4.number_input("Cess %", 0.0, 10.0, float(rates["cess"]), 0.5)
            if st.form_submit_button("SAVE"):
                rates = {"stcg": r1, "ltcg": r2, "exempt": r3, "cess": r4}
                store.upsert("portfolio_settings", {"key": "tax", "value": rates})
                D["settings"]["tax"] = rates
        st.caption("Defaults: listed equity with STT — STCG 20%, LTCG 12.5% above ₹1.25 lakh a year, long-term = held more than 12 months "
                   "(rates from 23 Jul 2024, unchanged in Budget 2026), 4% cess. Surcharge, carry-forward losses and pre-2018 grandfathering are not modelled.")
    summ = A["tax"]
    cur = _fy(_today())
    c = summ.get(cur, {"st_net": 0.0, "lt_net": 0.0, "tax": 0.0, "exempt_left": rates["exempt"], "proceeds": 0.0, "n": 0})
    _ph(f"This financial year · {cur}")
    _kpis([_kpi("Realised P&L (total)", _short(A["realised_total"]), f'{A["realised_n"]} matched sells, all years', _col(A["realised_total"]), T2, PURPLE),
           _kpi("Net STCG this FY", _short(c["st_net"]), "after set-off", _col(c["st_net"]), T2, AMBER),
           _kpi("Net LTCG this FY", _short(c["lt_net"]), "after set-off", _col(c["lt_net"]), T2, CYAN),
           _kpi("Tax-free LTCG left", _short(c["exempt_left"]), "of the annual exemption", GREEN, T2, GREEN),
           _kpi("Estimated tax this FY", _short(c["tax"]), "incl. cess", AMBER, T2, RED)])
    if summ:
        rows = [[f, _short(v["proceeds"]), f'<span style="color:{_col(v["st_net"])}">{_short(v["st_net"])}</span>',
                 f'<span style="color:{_col(v["lt_net"])}">{_short(v["lt_net"])}</span>', _short(v["lt_taxable"]), _short(v["tax"]),
                 _short(v["carry_loss"]) if v["carry_loss"] else "—"] for f, v in summ.items()]
        _table(["Financial year", "Sale value", "Net STCG", "Net LTCG", "Taxable LTCG", "Est. tax", "Loss to carry forward"], rows, right={1, 2, 3, 4, 5, 6})
    _ph("Unrealised gains by tax term", "what selling today would realise")
    st_u = lt_u = 0.0
    soon = []
    for l in A["alots_book"]:
        if l["open"] <= 1e-9:
            continue
        ltp = _f((A["quotes"].get(l["symbol"]) or {}).get("price"))
        if ltp is None:
            continue
        g = l["open"] * (ltp - l["price"])
        lt_on = _add_months(l["date"], 12) if l["date"] else None
        if lt_on and _today() > lt_on:
            lt_u += g
        else:
            st_u += g
            if lt_on and (lt_on - _today()).days <= 60 and g > 0:
                soon.append((lt_on, l, g))
    _kpis([_kpi("Unrealised short-term", _short(st_u), "held ≤ 12 months", _col(st_u), T2, AMBER),
           _kpi("Unrealised long-term", _short(lt_u), "held > 12 months", _col(lt_u), T2, CYAN),
           _kpi("LTCG you could book tax-free", _short(min(max(lt_u, 0.0), c["exempt_left"])), f"this FY ({cur})", GREEN, T2, GREEN)])
    if soon:
        soon.sort(key=lambda x: x[0])
        _note("<b>Turning long-term soon</b> — waiting could cut the tax rate on these gains from STCG to LTCG.")
        _table(["Stock", "Bought", "Long-term from", "Days left", "Open gain"], [[f'<b style="color:{AMBER}">{_esc(l["symbol"])}</b>', l["date"].strftime("%d %b %Y"),
                                                                               d.strftime("%d %b %Y"), str((d - _today()).days), _short(g)] for d, l, g in soon], right={3, 4})
    _ph("Realised trades (FIFO matched)")
    if A["realised"]:
        rows = [[x["sell_date"].strftime("%d %b %Y") if x["sell_date"] else "—", f'<b style="color:{AMBER}">{_esc(x["symbol"])}</b><div class="sub">{_esc(x["book"])}</div>',
                 _qty(x["qty"]), x["buy_date"].strftime("%d %b %Y") if x["buy_date"] else "—", _inr(x["buy_price"]), _inr(x["sell_price"]),
                 f'<span style="color:{_col(x["gain"])}">{_inr(x["gain"], 0)}</span>', f'{x["days"]}d' if x["days"] is not None else "—",
                 f'<span class="pf-pill" style="color:{CYAN if x["term"] == "LT" else AMBER}">{"LONG" if x["term"] == "LT" else "SHORT"}</span>', x["fy"]]
                for x in sorted(A["realised"], key=lambda x: x["sell_date"] or date.min, reverse=True)]
        _table(["Sold", "Stock", "Qty", "Bought", "Buy", "Sell", "Gain", "Held", "Term", "FY"], rows, right={2, 4, 5, 6, 7})
        df = pd.DataFrame([{k: (v.isoformat() if isinstance(v, date) else v) for k, v in x.items()} for x in A["realised"]])
        st.download_button("⤓ REALISED P&L (CSV)", df.to_csv(index=False), "arka_realised_pnl.csv", "text/csv", key="pf_real_csv")
    else:
        _note("No sells recorded yet. Record them in TRANSACTIONS → Record a sell.")
    st.caption("An estimate to help planning — not tax advice. Use your broker's capital-gains statement / AIS for filing.")


def _view_transactions(supabase, store, D, A, alots, lots):
    a, b = st.columns(2)
    with a:
        _ph("Add a buy", "same stock at another price = another lot")
        _add_buy_form(supabase, store, D, key="main")
    with b:
        _ph("Record a sell", "matched FIFO against your oldest lots in that book")
        _sell_form(store, D, alots, key="main")
    _ph("Books", "separate portfolios such as Long-term, Trading, Family")
    books = _book_options(D)
    c1, c2 = st.columns([3, 1])
    nb = c1.text_input("New book name", key="pf_new_book", placeholder="e.g. Trading", label_visibility="collapsed")
    if c2.button("＋ CREATE BOOK", key="pf_new_book_btn", use_container_width=True):
        nb = re.sub(r"\s+", " ", nb or "").strip()[:30]
        if not nb:
            st.error("Type a name first.")
        elif nb.lower() in {b.lower() for b in books} or nb.upper() == "ALL BOOKS":
            st.error("That book already exists.")
        else:
            store.upsert("portfolio_settings", {"key": "books", "value": sorted(set((D["settings"].get("books") or []) + [nb]))})
            st.toast(f"Book “{nb}” created — choose it when you add a buy, or move lots into it from HOLDINGS → DETAILS.")
            st.rerun()
    st.markdown(" ".join(f'<span class="pf-chip" style="--c:{AMBER}">{_esc(b)}</span>' for b in books), unsafe_allow_html=True)
    if A and A.get("problems"):
        _ph("Data checks")
        for p in A["problems"]:
            st.markdown(_flags_html([{"level": "alert", "title": "Sell larger than holding", "detail": p}]), unsafe_allow_html=True)
    _ph("Ledger", "every buy and sell, newest first")
    led = [{"date": _d(l.get("buy_date")), "type": "BUY", "symbol": l["symbol"], "book": D["meta"].get(str(l.get("id")), DEFAULT_BOOK),
            "qty": float(l["quantity"]), "price": float(l["buy_price"]), "note": l.get("note") or "", "ref": ("lot", l.get("id"))} for l in lots]
    led += [{"date": _d(s["sell_date"]), "type": "SELL", "symbol": s["symbol"], "book": s["book"], "qty": s["quantity"], "price": s["sell_price"],
             "note": s["note"], "ref": ("sell", s["id"])} for s in D["sells"]]
    led.sort(key=lambda x: (x["date"] or date.min), reverse=True)
    if led:
        _table(["Date", "Type", "Stock", "Book", "Qty", "Price", "Amount", "Note"],
               [[x["date"].strftime("%d %b %Y") if x["date"] else "—", f'<span class="pf-pill" style="color:{GREEN if x["type"] == "BUY" else RED}">{x["type"]}</span>',
                 f'<b style="color:{AMBER}">{_esc(x["symbol"])}</b>', _esc(x["book"]), _qty(x["qty"]), _inr(x["price"]), _inr(x["qty"] * x["price"], 0), _esc(x["note"])]
                for x in led], right={4, 5, 6})
        st.caption("Ledger shows quantities and prices exactly as entered; analysis uses split / bonus-adjusted figures.")
        sells_only = [x for x in led if x["type"] == "SELL"]
        if sells_only:
            with st.expander("Delete a recorded sell"):
                i = st.selectbox("Sell", range(len(sells_only)), format_func=lambda i: f'{sells_only[i]["date"]} · {sells_only[i]["symbol"]} · '
                                 f'{_qty(sells_only[i]["qty"])} @ {_inr(sells_only[i]["price"])} · {sells_only[i]["book"]}', key="pf_del_sell")
                if st.button("DELETE SELL", key="pf_del_sell_btn"):
                    err = store.delete("portfolio_sells", id=sells_only[i]["ref"][1])
                    st.error(err) if err else st.rerun()
    _ph("Storage")
    if store.missing:
        _note("These features are working <b>for this session only</b> because their tables don't exist yet: "
              + ", ".join(sorted(store.missing)) + ". Run this once in Supabase → SQL Editor to keep sells, books, plans, settings and history permanently:")
        st.code(SQL_EXTRA, language="sql")
    elif supabase is None:
        _note("Supabase is not connected, so everything is kept for this session only.")
    else:
        _note("All portfolio data is saved in Supabase.")
    for e in store.errors[:3]:
        st.warning(e)


def _view_news(holds, news, days_label):
    st.selectbox("News window", ["24 hours", "3 days", "7 days"], key="pf_news_window", index=["24 hours", "3 days", "7 days"].index(days_label))
    with_news = sorted([h for h in holds if news.get(h["symbol"])], key=lambda h: -(h["value"] or 0))
    if not with_news:
        _note(f"No relevant headlines for your holdings in the last {_esc(days_label)}. A yellow dot appears next to a stock when news arrives.")
        return
    for h in with_news:
        arts = news[h["symbol"]]
        tone = h.get("tone")
        tone_txt = f' · tone <span style="color:{GREEN if tone > 0.3 else RED if tone < -0.3 else T2}">{tone:+.1f}</span>' if tone is not None else ""
        st.markdown(f'<div class="pf-ph"><span class="pf-dot" style="margin:0 2px 0 0"></span><span class="pf-ph-t">{_esc(h["symbol"])} · {len(arts)} headline'
                    f'{"s" if len(arts) > 1 else ""}</span><span class="pf-ph-s">{_pct(h["weight"], 1, False)} of portfolio{tone_txt}</span></div>', unsafe_allow_html=True)
        for a in arts[:6]:
            sent = str(a.get("sentiment", "neutral"))
            c = GREEN if "positive" in sent else RED if "negative" in sent else T3
            st.markdown(f'<a href="{_esc(a.get("link", "#"), True)}" target="_blank" style="text-decoration:none"><div style="border-left:2px solid {c};padding:6px 0 6px 10px;margin-bottom:5px;">'
                        f'<div style="font:600 12px Inter,sans-serif;color:{IVORY}">{_esc(a.get("title", ""))}</div>'
                        f'<div style="font:9px {MONO};color:{T2};margin-top:3px">{_esc(a.get("source", ""))} · {_esc(a.get("time_str", ""))} · {_esc(a.get("event", "NEWS"))}</div></div></a>',
                        unsafe_allow_html=True)


# ═════════════════════════ monthly review report ═════════════════════════
def _report_html(A, holds, book_label, bench_label):
    T = A["tot"]
    css = ("body{font-family:Inter,Arial,sans-serif;color:#111;margin:32px;}h1{margin:0;font-size:22px}h2{font-size:14px;margin:26px 0 8px;border-bottom:2px solid #FF9F0A;padding-bottom:4px}"
           "table{border-collapse:collapse;width:100%;font-size:11px}th,td{padding:5px 6px;border-bottom:1px solid #ddd;text-align:left}td.r,th.r{text-align:right}"
           ".k{display:inline-block;margin:0 18px 8px 0}.k b{display:block;font-size:16px}.s{color:#666;font-size:11px}.pos{color:#0a7a2f}.neg{color:#b3261e}")
    cl = lambda v: "pos" if (v or 0) >= 0 else "neg"
    k = lambda l, v, c="": f'<div class="k"><span class="s">{l}</span><b class="{c}">{v}</b></div>'
    rows = "".join(f'<tr><td>{_esc(h["symbol"])}</td><td>{_esc(h["sector"])}</td><td class="r">{_qty(h["qty"])}</td><td class="r">{_inr(h["avg"])}</td>'
                   f'<td class="r">{_inr(h["ltp"])}</td><td class="r">{_inr(h["value"], 0)}</td><td class="r {cl(h["pnl"])}">{_inr(h["pnl"], 0)} ({_pct(h["pnl_pct"], 1)})</td>'
                   f'<td class="r">{_num(h["weight"], 1, "%")}</td></tr>' for h in holds)
    secs = "".join(f'<tr><td>{_esc(s["name"])}</td><td class="r">{s["n"]}</td><td class="r">{_short(s["value"])}</td><td class="r">{s["weight"]:.1f}%</td>'
                   f'<td class="r {cl(s["pnl_pct"])}">{_pct(s["pnl_pct"], 1)}</td><td>{_esc(s.get("stage") or "")}</td></tr>' for s in A["sectors"])
    flags = "".join(f'<li><b>{_esc(f["title"])}</b> — {_esc(f["detail"])}</li>' for f in A["flags"] if f["level"] in ("alert", "watch"))
    taxr = "".join(f'<tr><td>{f}</td><td class="r">{_short(v["st_net"])}</td><td class="r">{_short(v["lt_net"])}</td><td class="r">{_short(v["tax"])}</td></tr>' for f, v in A["tax"].items())
    xi = A["xirr"]
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Arka portfolio review</title><style>{css}</style></head><body>"
            f"<h1>Portfolio review · {_esc(book_label)}</h1><div class='s'>Arka Trades · {datetime.now(IST):%d %b %Y %H:%M IST} · prices delayed · not investment advice</div>"
            f"<h2>Summary</h2>{k('Invested (open)', _short(T['inv']))}{k('Value', _short(T['val']))}{k('Unrealised P&L', _short(T['pnl']) + ' (' + _pct(T['pnl_pct'], 1) + ')', cl(T['pnl']))}"
            f"{k('Realised P&L', _short(A['realised_total']), cl(A['realised_total']))}{k('Dividends (est.)', _short(A['div_total']))}"
            f"{k('XIRR', _pct(xi * 100, 1) if xi is not None else '—', cl(xi))}{k('Diversification score', str(A['score'] if A['score'] is not None else '—'))}"
            f"<h2>Holdings</h2><table><tr><th>Stock</th><th>Sector</th><th class='r'>Qty</th><th class='r'>Avg</th><th class='r'>LTP</th><th class='r'>Value</th>"
            f"<th class='r'>P&L</th><th class='r'>Weight</th></tr>{rows}</table>"
            f"<h2>Sectors</h2><table><tr><th>Sector</th><th class='r'>Stocks</th><th class='r'>Value</th><th class='r'>Weight</th><th class='r'>P&L</th><th>Rotation stage</th></tr>{secs}</table>"
            f"<h2>Points to review</h2><ul>{flags or '<li>No warnings.</li>'}</ul>"
            f"<h2>Capital gains (estimate)</h2><table><tr><th>FY</th><th class='r'>Net STCG</th><th class='r'>Net LTCG</th><th class='r'>Est. tax</th></tr>{taxr or '<tr><td colspan=4>No sells.</td></tr>'}</table>"
            f"<p class='s'>Benchmark used for performance: {_esc(bench_label)}. Print this page or save it as PDF from your browser.</p></body></html>")


# ═════════════════════════ page ═════════════════════════
def _refresh():
    for name in ("_get_quotes_cached", "_scan_cached", "_history_cached"):
        fn = getattr(de, name, None)
        if fn is not None and hasattr(fn, "clear"):
            try:
                fn.clear()
            except Exception:
                pass
    for attr in ("_LAST_GOOD", "_FAIL_UNTIL"):
        d = getattr(de, attr, None)
        if isinstance(d, dict):
            d.clear()
    st.session_state.get("_pf_info_tried", {}).clear()
    st.session_state.pop("_pf_info", None)


def _snapshot(store, D, key, invested, value, realised, divs):
    flag = f"_pf_snap_{_today()}_{key}"
    if st.session_state.get(flag):
        return
    st.session_state[flag] = True
    row = {"snap_date": _today().isoformat(), "book": key, "invested": round(invested, 2), "value": round(value, 2),
           "realised": round(realised, 2), "dividends": round(divs, 2)}
    store.upsert("portfolio_snapshots", row)
    D["snaps"] = [s for s in D["snaps"] if not (str(s.get("snap_date"))[:10] == row["snap_date"] and s.get("book") == key)] + [row]


def render_portfolio(supabase, on_open):
    st.markdown(_CSS, unsafe_allow_html=True)
    lots, warn = ud.load_portfolio(supabase)
    if warn:
        _missing_table_help() if ud.table_missing(warn) else st.warning(warn)
    lots = [l for l in (lots or []) if l.get("symbol") and _f(l.get("quantity")) and _f(l.get("buy_price"))]
    for l in lots:
        l["symbol"] = str(l["symbol"]).upper().strip()
        l["quantity"], l["buy_price"] = float(_f(l["quantity"])), float(_f(l["buy_price"]))
    store = Store(supabase)
    D = store.load()
    books = _book_options(D)

    # ── header controls ──
    c1, c2, c3, c4 = st.columns([1.4, 1.3, 1, 1])
    book_filter = c1.selectbox("Book", ["ALL BOOKS"] + books, key="pf_book", label_visibility="collapsed")
    bench_label = c2.selectbox("Benchmark", list(BENCHMARKS), key="pf_bench", label_visibility="collapsed")
    if c3.button("↻ REFRESH", key="pf_refresh", use_container_width=True, help="Reload live prices and fundamentals"):
        _refresh()
        st.rerun()
    days_label = st.session_state.get("pf_news_window", "24 hours")
    days = {"24 hours": 1, "3 days": 3, "7 days": 7}.get(days_label, 1)

    A, holds, news, alots = None, [], {}, []
    if lots:
        all_syms = sorted({l["symbol"] for l in lots} | {s["symbol"] for s in D["sells"]})
        infos = _load_infos(all_syms)
        alots_all, realised_all, problems, adj_sells_all = run_engine(lots, D["sells"], D["meta"], infos)
        inb = (lambda b: True) if book_filter == "ALL BOOKS" else (lambda b: b == book_filter)
        alots = [l for l in alots_all if inb(l["book"])]
        realised = [r for r in realised_all if inb(r["book"])]
        adj_sells = [s for s in adj_sells_all if inb(s["book"])]
        open_syms = sorted({l["symbol"] for l in alots if l["open"] > 1e-9})
        quotes = get_quotes(tuple(open_syms)) if open_syms else {}
        news = holding_news(open_syms, {l["symbol"]: l["name"] for l in alots if l["name"]}, days)
        rmap = _rotation_map()
        holds = aggregate(alots, quotes, infos, D["plans"], book_filter, rmap, news)
        closes = {s: i["close"] for s, i in infos.items() if i and i.get("close") is not None}
        _, nifty = _bench("NIFTY 50")
        btick, bench = _bench(bench_label)
        bench_used = bench_label if btick else "NIFTY 50"
        valid = [h for h in holds if h["value"] is not None]
        inv = sum(h["invested"] for h in holds)
        inv_v = sum(h["invested"] for h in valid)
        val = sum(h["value"] for h in valid)
        day = sum(h["day_pnl"] for h in valid if h["day_pnl"] is not None)
        tot = {"inv": inv, "val": val, "pnl": val - inv_v, "pnl_pct": ((val / inv_v - 1) * 100) if inv_v else None, "day": day,
               "day_pct": (day / (val - day) * 100) if (val - day) else None, "n": len(holds), "n_valid": len(valid),
               "n_lots": sum(1 for l in alots if l["open"] > 1e-9)}
        sectors = group_table(holds, "sector")
        divs = estimate_dividends(alots, infos)
        rates = {**TAX_DEFAULTS, **(D["settings"].get("tax") or {})}
        tax = tax_summary(realised, rates)
        cur = tax.get(_fy(_today()))
        risk = risk_metrics(holds, closes, nifty)
        heat_den = sum(h["value"] for h in holds if h["value"])
        heat = (sum(h["risk"] or 0 for h in holds if h["sl"] is not None) / heat_den * 100) if heat_den and any(h["sl"] is not None for h in holds) else None
        fs_w = [(h["fscore"][0] / h["fscore"][1], h["value"]) for h in holds if h.get("fscore") and h["value"] and h["fscore"][1]]
        _tl_memo = {}

        def _timeline():
            if "v" not in _tl_memo:
                try:
                    _tl_memo["v"] = timeline(alots, adj_sells, closes, bench)
                except Exception:
                    _tl_memo["v"] = None
            return _tl_memo["v"]
        A = {"tot": tot, "sectors": sectors, "scol": sector_colors(sectors), "industries": group_table(holds, "industry"),
             "mcap": mcap_split(holds), "fund": weighted_fundamentals(holds), "conc": concentration(holds), "risk": risk,
             "realised": realised, "realised_total": sum(r["gain"] for r in realised), "realised_n": len({r["sell_id"] for r in realised}),
             "divs": divs, "div_total": sum(d["amount"] for d in divs), "tax": tax,
             "ltcg_headroom": cur["exempt_left"] if cur else rates["exempt"], "heat": heat, "problems": [p for p in problems],
             "avg_fscore": (sum(a * b for a, b in fs_w) / sum(b for _, b in fs_w) * 100) if fs_w else None,
             "timeline": _timeline, "nifty": nifty, "infos": infos, "quotes": quotes, "alots_book": alots,
             "book_key": "ALL" if book_filter == "ALL BOOKS" else book_filter}
        A["xirr"], A["xirr_days"] = portfolio_xirr(alots, adj_sells, holds)
        A["score"] = diversification_score(A["conc"], sectors, risk)
        A["flags"] = build_flags(A, holds, news, days_label)
        if val > 0:
            _snapshot(store, D, A["book_key"], inv, val, A["realised_total"], A["div_total"])
        bench_label = bench_used

    if A:
        c4.download_button("⤓ MONTHLY REVIEW", _report_html(A, holds, book_filter, bench_label).encode(),
                           f"arka_portfolio_review_{_today():%Y_%m}.html", "text/html", key="pf_report", use_container_width=True,
                           help="A printable HTML report — open it and use your browser's Print → Save as PDF")
    now = datetime.now(IST).strftime("%d %b %Y · %H:%M IST")
    if A:
        T = A["tot"]
        st.markdown(f'<div class="pf-hero"><div><div class="k">ARKA TRADES · PORTFOLIO · {_esc(book_filter)}</div>'
                    f'<div class="t">{_short(T["val"])}</div><div class="d" style="color:{_col(T["pnl"])}">{_short(T["pnl"])} ({_pct(T["pnl_pct"])}) unrealised · '
                    f'<span style="color:{_col(T["day"])}">{_short(T["day"])} today</span></div></div>'
                    f'<div class="m">{T["n"]} holdings · {len([s for s in A["sectors"] if s["name"] != UNCLASSIFIED])} sectors · {len(books)} book(s)<br>'
                    f'benchmark {_esc(bench_label)} · as of {now}</div></div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="pf-hero"><div><div class="k">ARKA TRADES · PORTFOLIO</div><div class="t">Portfolio</div></div>'
                    f'<div class="m">no holdings yet<br>as of {now}</div></div>', unsafe_allow_html=True)

    with st.container(key="pf_views"):
        view = st.radio("Portfolio view", VIEWS, horizontal=True, key="pf_view", label_visibility="collapsed")
    if not A and view not in ("TRANSACTIONS",):
        _empty_state()
        if view != "NEWS":
            return
    if not holds and A and view not in ("TRANSACTIONS", "TAX & REALISED", "PERFORMANCE"):
        _note(f"No open positions in {_esc(book_filter)}. Realised trades and tax are under TAX & REALISED; add buys under TRANSACTIONS.")
        return
    if view == "DASHBOARD":
        _view_dashboard(A, holds, news, days_label, bench_label)
    elif view == "HOLDINGS":
        _view_holdings(supabase, store, D, A, holds, alots, news, on_open, book_filter)
    elif view == "ANALYSIS":
        _view_analysis(A, holds)
    elif view == "PERFORMANCE":
        _view_performance(A, holds, store, D, bench_label)
    elif view == "RISK & STRESS":
        _view_risk(A, holds, bench_label)
    elif view == "REBALANCE":
        _view_rebalance(A, holds, store, D)
    elif view == "INCOME & EVENTS":
        _view_income(A, holds)
    elif view == "TAX & REALISED":
        _view_tax(A, store, D)
    elif view == "TRANSACTIONS":
        _view_transactions(supabase, store, D, A, alots, lots)
    elif view == "NEWS":
        _view_news(holds, news, days_label)
