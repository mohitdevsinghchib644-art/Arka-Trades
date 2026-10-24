"""
portfolio_page.py — Arka Trades Portfolio · analytics edition

Add buy lots once (stock · quantity · buy price · buy date · note) and the page
builds a full portfolio review for you:

  DASHBOARD  KPI strip (invested, value, P&L, day P&L, XIRR) · growth of your
             money vs a NIFTY 50 investment with the same cash flows ·
             diversification score · sector donut · top positions · movers
  ANALYSIS   sector + industry exposure (treemap + table) · market-cap mix ·
             concentration · weighted P/E, ROE, ROCE, dividend yield ·
             risk (volatility, max drawdown, beta, 1-day VaR, correlation) ·
             automatic health check
  HOLDINGS   sortable / filterable table with sector tags, news light,
             lot-level detail, CSV export
  ADD        add a buy lot (same stock at another price = another lot)
  NEWS       headlines for your holdings (24 h / 3 d / 7 d)

Data sources: prices via data_engine.get_quotes, sector / fundamentals / price
history via data_engine.warm_security (cached per session), news via
news_feed. Everything you type is saved to Supabase (table `portfolio`, SQL in
user_data.py). Nothing here is investment advice.

Requires: pandas, numpy, plotly. Optional: yfinance (NIFTY comparison + beta).
"""
from __future__ import annotations

import html
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
from data_engine import get_quotes

try:
    from data_engine import warm_security
except Exception:  # pragma: no cover
    warm_security = None

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
UNCLASSIFIED = "Unclassified"
UNCLASSIFIED_COLOR = "#6E6E73"

# Approximate market-cap buckets in ₹ crore. AMFI/SEBI revise the real cut-offs
# every six months — edit these two numbers if you want them exact.
LARGE_CAP_CR = 100_000
MID_CAP_CR = 30_000

_CSS = """
<style>
:root{--pf-amber:#FF9F0A;--pf-green:#30D158;--pf-red:#FF453A;--pf-cyan:#5AC8FA;--pf-yellow:#FFD60A;
      --pf-border:#262626;--pf-panel:#0A0A0A;--pf-ivory:#E8E8E8;--pf-t2:#8A8A8A;--pf-t3:#5A5A5A;}
div[class*="st-key-pfrows"]{gap:.1rem !important;}
div[data-testid="stHorizontalBlock"]:has(.pf-cell){align-items:center;border-bottom:1px solid #1a1a1a;padding:2px 0;}
div[data-testid="stHorizontalBlock"]:has(.pf-cell):hover{background:#0c0c0c;}
.pf-cell{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--pf-ivory);min-height:44px;display:flex;align-items:center;}
.pf-grid{display:grid;grid-template-columns:2.2fr .7fr .95fr .95fr .75fr 1.05fr 1.05fr 1.05fr .85fr 1.1fr;gap:8px;width:100%;align-items:center;}
.pf-grid span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pf-head{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.8px;color:var(--pf-t2);font-weight:700;min-height:28px;display:flex;align-items:center;border-bottom:1px solid var(--pf-border);}
.pf-sym{font-weight:800;font-size:12px;color:var(--pf-amber);}
.pf-name{font-size:9px;color:var(--pf-t3);display:block;overflow:hidden;text-overflow:ellipsis;margin-top:1px;}
.pf-chip{display:inline-block;font:700 8px 'JetBrains Mono',monospace;letter-spacing:.5px;padding:1px 6px;margin-top:3px;border:1px solid var(--c,#444);color:var(--c,#aaa);background:rgba(255,255,255,.02);text-transform:uppercase;max-width:100%;overflow:hidden;text-overflow:ellipsis;}
.pf-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--pf-yellow);margin-left:7px;vertical-align:middle;box-shadow:0 0 6px var(--pf-yellow),0 0 12px #FFD60A88;animation:pfpulse 1.8s ease-in-out infinite;cursor:help;}
@keyframes pfpulse{0%,100%{opacity:1}50%{opacity:.35}}
.pf-hero{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;padding:14px 16px;background:linear-gradient(180deg,#0e0e0e,#080808);border:1px solid var(--pf-border);border-top:2px solid var(--pf-amber);margin-bottom:10px;}
.pf-hero .k{font:700 8px 'JetBrains Mono',monospace;letter-spacing:1.5px;color:var(--pf-t3);}
.pf-hero .t{font:800 21px 'Inter',sans-serif;color:var(--pf-ivory);margin-top:3px;letter-spacing:-.3px;}
.pf-hero .m{font:10px 'JetBrains Mono',monospace;color:var(--pf-t2);text-align:right;line-height:1.7;}
.pf-kpi{border:1px solid var(--pf-border);border-top:2px solid var(--a,#444);background:var(--pf-panel);padding:11px 13px;min-height:84px;}
.pf-kpi .l{font:700 8px 'JetBrains Mono',monospace;color:var(--pf-t3);letter-spacing:1px;text-transform:uppercase;}
.pf-kpi .v{font:700 19px 'JetBrains Mono',monospace;color:var(--pf-ivory);margin-top:6px;white-space:nowrap;}
.pf-kpi .s{font:10px 'JetBrains Mono',monospace;margin-top:4px;color:var(--pf-t2);}
.pf-ph{display:flex;align-items:baseline;gap:10px;border-bottom:1px solid var(--pf-border);padding:14px 0 7px;margin:6px 0 10px;}
.pf-ph:before{content:"";width:3px;height:11px;background:var(--pf-amber);display:inline-block;align-self:center;}
.pf-ph-t{font:700 10px 'JetBrains Mono',monospace;letter-spacing:1.4px;color:var(--pf-amber);text-transform:uppercase;}
.pf-ph-s{font:10px 'Inter',sans-serif;color:var(--pf-t3);}
.pf-note{font:10px 'JetBrains Mono',monospace;color:var(--pf-t2);border-left:2px solid #444;padding:7px 11px;background:rgba(255,255,255,.02);margin:8px 0;line-height:1.7;}
.pf-empty{border:1px dashed #333;background:#080808;padding:34px 24px;text-align:center;margin:14px 0;}
.pf-empty .a{font:800 18px 'Inter',sans-serif;color:var(--pf-ivory);}
.pf-empty .b{font:11px 'Inter',sans-serif;color:var(--pf-t2);margin-top:8px;line-height:1.7;}
.pf-flag{display:flex;gap:10px;padding:9px 11px;border:1px solid var(--pf-border);border-left:3px solid var(--fc,#444);background:var(--pf-panel);margin-bottom:6px;}
.pf-flag .lv{font:800 8px 'JetBrains Mono',monospace;letter-spacing:1px;color:var(--fc,#888);min-width:44px;padding-top:2px;}
.pf-flag .ft{font:700 12px 'Inter',sans-serif;color:var(--pf-ivory);}
.pf-flag .fd{font:11px 'Inter',sans-serif;color:var(--pf-t2);margin-top:2px;line-height:1.55;}
.pf-score{font:800 9px 'JetBrains Mono',monospace;letter-spacing:1.4px;text-align:center;margin-top:-6px;}
.pf-tbl{width:100%;border-collapse:collapse;font-size:11px;}
.pf-tbl th{font:700 8px 'JetBrains Mono',monospace;letter-spacing:.9px;color:var(--pf-t2);text-align:left;padding:7px 6px;border-bottom:1px solid var(--pf-border);text-transform:uppercase;white-space:nowrap;}
.pf-tbl td{padding:8px 6px;border-bottom:1px solid #161616;color:var(--pf-ivory);vertical-align:middle;}
.pf-tbl th.r,.pf-tbl td.r{text-align:right;font-family:'JetBrains Mono',monospace;}
.pf-tbl td .sub{font-size:9px;color:var(--pf-t3);margin-top:2px;}
.pf-sw{display:inline-block;width:8px;height:8px;margin-right:7px;vertical-align:middle;}
.pf-wbar{display:inline-block;width:64px;height:5px;background:#1a1a1a;vertical-align:middle;margin-right:7px;}
.pf-wbar i{display:block;height:100%;}
.pf-rng{position:relative;display:inline-block;width:70px;height:4px;background:linear-gradient(90deg,#FF453A55,#333,#30D15855);vertical-align:middle;}
.pf-rng i{position:absolute;top:-3px;width:3px;height:10px;background:#FF9F0A;transform:translateX(-1px);}
.pf-mini{font:10px 'JetBrains Mono',monospace;color:var(--pf-t2);}
.pf-mover{display:flex;justify-content:space-between;padding:7px 2px;border-bottom:1px solid #161616;font:11px 'JetBrains Mono',monospace;}
.pf-mover b{color:var(--pf-amber);}
</style>
"""


# ═════════════════════════ formatting helpers ═════════════════════════
def _f(x):
    """Tolerant float: 12,345.6 / '₹12,345 Cr' / '14.2%' / None -> float | None."""
    if x is None:
        return None
    if isinstance(x, (int, float, np.integer, np.floating)):
        v = float(x)
    else:
        s = re.sub(r"[₹,%\s]|Cr\.?", "", str(x))
        try:
            v = float(s)
        except ValueError:
            return None
    return v if math.isfinite(v) else None


def _esc(s, quote=False):
    return html.escape("" if s is None else str(s), quote=quote)


def _inr(x, d=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    neg = x < 0
    s = f"{abs(x):.{d}f}"
    ip, _, fp = s.partition(".")
    if len(ip) > 3:
        head, tail = ip[:-3], ip[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        ip = head + "," + tail
    out = "₹" + ip + (("." + fp) if d else "")
    return ("-" + out) if neg else out


def _short(x):
    """₹1.25 Cr / ₹3.40 L / ₹45,200 — Indian compact format."""
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


def _col(v):
    return GREEN if (v or 0) >= 0 else RED


def _kpi(label, value, sub="", vcolor=IVORY, scolor=T2, accent=AMBER):
    return (f'<div class="pf-kpi" style="--a:{accent}"><div class="l">{_esc(label)}</div>'
            f'<div class="v" style="color:{vcolor}">{value}</div>'
            f'<div class="s" style="color:{scolor}">{sub}</div></div>')


def _ph(title, sub=""):
    s = f'<span class="pf-ph-s">{_esc(sub)}</span>' if sub else ""
    st.markdown(f'<div class="pf-ph"><span class="pf-ph-t">{_esc(title)}</span>{s}</div>', unsafe_allow_html=True)


# ═════════════════════════ plotting helpers ═════════════════════════
def _layout(fig, h=260, margin=(0, 0, 0, 0), legend=False):
    fig.update_layout(
        height=h, margin=dict(l=margin[0], r=margin[1], t=margin[2], b=margin[3]),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=legend,
        font=dict(family="Inter, sans-serif", size=11, color=T2),
        hoverlabel=dict(bgcolor=DARK2, bordercolor=BORDER,
                        font=dict(family="JetBrains Mono, monospace", size=11, color=IVORY)),
    )
    return fig


def _plot(builder, key, *args):
    """Build + draw a chart; a chart error never takes the page down."""
    if go is None:
        st.caption("Charts need plotly — add `plotly` to requirements.txt.")
        return
    try:
        fig = builder(*args)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)
    except Exception as e:  # pragma: no cover
        st.caption(f"Chart unavailable ({type(e).__name__}).")


def _inr_ticks(fig, lo, hi, n=5):
    vals = list(np.linspace(lo, hi, n))
    fig.update_yaxes(tickvals=vals, ticktext=[_short(v) for v in vals])


# ═════════════════════════ data loading ═════════════════════════
def _pool(n):
    """ThreadPool whose workers inherit Streamlit's script context (no cache warnings)."""
    ctx = get_script_run_ctx() if get_script_run_ctx else None

    def _init():
        if ctx is not None and add_script_run_ctx is not None:
            try:
                add_script_run_ctx(threading.current_thread(), ctx)
            except Exception:
                pass

    return ThreadPoolExecutor(max_workers=max(1, n), initializer=_init)


def _clean_close(hist):
    try:
        if hist is None or len(hist) < 5 or "Close" not in hist:
            return None
        s = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        s.index = idx.normalize()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        return s.astype("float64").tail(1300) if len(s) >= 5 else None
    except Exception:
        return None


def _fetch_info(sym):
    """Sector, fundamentals and price history for one symbol (None on total failure)."""
    if warm_security is None:
        return None
    try:
        B, hist = warm_security(sym)
    except Exception:
        return None
    B = B if isinstance(B, dict) else {}
    sec = B.get("sector") if isinstance(B.get("sector"), dict) else {}
    summ = B.get("summary") if isinstance(B.get("summary"), dict) else {}
    sector = str(sec.get("Sector") or sec.get("Broad Sector") or "").strip()
    industry = str(sec.get("Industry") or sec.get("Broad Industry") or "").strip()
    close = _clean_close(hist)

    hi = lo = None
    try:
        if hist is not None and len(hist) >= 2 and "High" in hist and "Low" in hist:
            tail = hist.tail(252)
            hi, lo = _f(tail["High"].max()), _f(tail["Low"].min())
    except Exception:
        pass
    if hi is None or lo is None:
        hi, lo = _f(summ.get("year_high")), _f(summ.get("year_low"))
    if (hi is None or lo is None) and close is not None:
        hi, lo = float(close.tail(252).max()), float(close.tail(252).min())

    if not sector and close is None and not summ:
        return None
    return {
        "sector": sector or UNCLASSIFIED, "industry": industry or UNCLASSIFIED,
        "mcap": _f(summ.get("market_cap")), "pe": _f(summ.get("pe_ratio")),
        "roe": _f(summ.get("roe")), "roce": _f(summ.get("roce")), "dy": _f(summ.get("dividend_yield")),
        "hi52": hi, "lo52": lo, "name": str(B.get("name") or ""),
        "close": close, "partial": not sector,
    }


def _load_infos(symbols):
    """Per-session cache. Only missing symbols are fetched (in parallel); symbols whose
    fundamentals failed are retried every two minutes instead of being stuck."""
    store = st.session_state.setdefault("_pf_info", {})
    tried = st.session_state.setdefault("_pf_info_tried", {})
    now = time.time()
    todo = [s for s in symbols if (s not in store or store[s].get("partial")) and now - tried.get(s, 0) > 120]
    if todo:
        for s in todo:
            tried[s] = now
        with st.spinner(f"Analysing {len(symbols)} holdings — loading sectors, fundamentals and price history…"):
            with _pool(min(6, len(todo))) as ex:
                for s, info in zip(todo, ex.map(_fetch_info, todo)):
                    if info:
                        store[s] = info
    return {s: store.get(s) for s in symbols}


@st.cache_data(ttl=3600, show_spinner=False)
def _nifty_cached():
    import yfinance as yf
    df = yf.download("^NSEI", period="5y", interval="1d", progress=False, auto_adjust=True, threads=False, timeout=15)
    if df is None or len(df) < 30:
        raise ValueError("no NIFTY data")          # exceptions are never cached
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")].sort_index().astype("float64")


def _nifty():
    try:
        return _nifty_cached()
    except Exception:
        return None


def holding_news(symbols, names, days):
    """{symbol: [articles]} — parallel; each call is cached 5 min inside news_feed."""
    if _fetch_news_for_stock is None or not symbols:
        return {}

    def one(sym):
        try:
            try:
                arts = _fetch_news_for_stock(sym, days, company=names.get(sym) or None)
            except TypeError:                      # older news_feed without `company=`
                arts = _fetch_news_for_stock(sym, days)
            return sym, (arts or [])
        except Exception:
            return sym, []

    with _pool(min(6, len(symbols))) as ex:
        return dict(ex.map(one, symbols))


def _lookup_name(sym):
    if not resolve_symbol:
        return ""
    try:
        r = resolve_symbol(sym)
        return (r or {}).get("name", "") or ""
    except Exception:
        return ""


# ═════════════════════════ calculations ═════════════════════════
def aggregate(lots, quotes, infos=None):
    infos = infos or {}
    by = defaultdict(list)
    for l in lots:
        by[l["symbol"]].append(l)
    out = []
    for sym, ls in by.items():
        qty = sum(l["quantity"] for l in ls)
        invested = sum(l["quantity"] * l["buy_price"] for l in ls)
        q = quotes.get(sym) or {}
        info = infos.get(sym) or {}
        ltp, prev = _f(q.get("price")), _f(q.get("prev_close"))
        chg = _f(q.get("chg"))
        if chg is None and ltp is not None and prev:
            chg = (ltp / prev - 1) * 100
        value = qty * ltp if ltp is not None else None
        dates = [l["buy_date"] for l in ls if l["buy_date"]]
        out.append({
            "symbol": sym,
            "name": next((l["name"] for l in ls if l["name"]), "") or info.get("name", ""),
            "sector": info.get("sector") or UNCLASSIFIED, "industry": info.get("industry") or UNCLASSIFIED,
            "mcap": info.get("mcap"), "pe": info.get("pe"), "roe": info.get("roe"),
            "roce": info.get("roce"), "dy": info.get("dy"), "hi52": info.get("hi52"), "lo52": info.get("lo52"),
            "qty": qty, "avg": invested / qty if qty else 0, "invested": invested,
            "ltp": ltp, "value": value, "chg": chg, "since": min(dates) if dates else "",
            "pnl": (value - invested) if value is not None else None,
            "pnl_pct": ((value / invested - 1) * 100) if (value is not None and invested) else None,
            "day_pnl": qty * (ltp - prev) if (ltp is not None and prev) else None,
            "lots": ls,
        })
    total_val = sum(h["value"] for h in out if h["value"] is not None)
    for h in out:
        h["weight"] = (h["value"] / total_val * 100) if (h["value"] is not None and total_val) else None
    return sorted(out, key=lambda h: (h["value"] is None, -(h["value"] or 0)))


def _xirr(flows):
    flows = [(d, a) for d, a in flows if a]
    if len(flows) < 2:
        return None
    t0 = min(d for d, _ in flows)

    def npv(r):
        return sum(a / (1.0 + r) ** ((d - t0).days / 365.0) for d, a in flows)

    try:
        lo, hi = -0.99, 20.0
        f_lo, f_hi = npv(lo), npv(hi)
        if f_lo * f_hi > 0:
            return None
        for _ in range(120):
            mid = (lo + hi) / 2
            f_mid = npv(mid)
            if f_lo * f_mid <= 0:
                hi = mid
            else:
                lo, f_lo = mid, f_mid
        return (lo + hi) / 2
    except (OverflowError, ZeroDivisionError):
        return None


def portfolio_xirr(lots, quotes):
    """Money-weighted annual return across all dated lots (needs 60+ days of history)."""
    flows, value_now = [], 0.0
    for l in lots:
        ltp = _f((quotes.get(l["symbol"]) or {}).get("price"))
        if ltp is None or not l["buy_date"]:
            continue
        try:
            d = datetime.strptime(l["buy_date"][:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        flows.append((d, -l["quantity"] * l["buy_price"]))
        value_now += l["quantity"] * ltp
    if not flows or value_now <= 0:
        return None, 0
    today = datetime.now(IST).date()
    span = (today - min(d for d, _ in flows)).days
    if span < 60:
        return None, span
    flows.append((today, value_now))
    return _xirr(flows), span


def concentration(holds):
    w = sorted([h["weight"] for h in holds if h["weight"]], reverse=True)
    if not w:
        return None
    hhi = sum((x / 100) ** 2 for x in w)
    return {"top1": w[0], "top3": sum(w[:3]), "top5": sum(w[:5]), "hhi": hhi,
            "eff_n": (1 / hhi) if hhi else None, "n": len(w)}


def group_table(holds, key):
    """Aggregate holdings by `sector` or `industry`."""
    agg = {}
    for h in holds:
        k = h[key] or UNCLASSIFIED
        a = agg.setdefault(k, {"name": k, "n": 0, "syms": [], "invested": 0.0, "invested_v": 0.0,
                               "value": 0.0, "day": 0.0})
        a["n"] += 1
        a["syms"].append(h["symbol"])
        a["invested"] += h["invested"]
        if h["value"] is not None:
            a["value"] += h["value"]
            a["invested_v"] += h["invested"]
        if h["day_pnl"] is not None:
            a["day"] += h["day_pnl"]
    total = sum(a["value"] for a in agg.values())
    rows = []
    for a in agg.values():
        a["pnl"] = a["value"] - a["invested_v"]
        a["pnl_pct"] = (a["value"] / a["invested_v"] - 1) * 100 if a["invested_v"] else None
        a["weight"] = a["value"] / total * 100 if total else 0.0
        rows.append(a)
    return sorted(rows, key=lambda a: -a["value"])


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
        k = "Unknown" if m is None else "Large cap" if m >= LARGE_CAP_CR else "Mid cap" if m >= MID_CAP_CR else "Small cap"
        b[k] += h["value"]
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

    inv = den = 0.0                                    # harmonic mean is the correct way to blend P/E
    for h in holds:
        pe = h.get("pe")
        if h["value"] is None or pe is None or pe <= 0:
            continue
        inv += h["value"] / pe
        den += h["value"]
    pe_w = (den / inv) if inv else None
    out = {"pe": (pe_w, den / tot * 100 if tot else 0.0)}
    for k in ("roe", "roce", "dy"):
        out[k] = wavg(k)
    return out


def performance_series(lots, closes, nifty):
    """Value of today's holdings from each buy date, vs invested, vs a NIFTY 50
    investment with exactly the same rupee amounts on the same dates."""
    dated = []
    for l in lots:
        if l["symbol"] not in closes or not l["buy_date"]:
            continue
        try:
            dated.append((l, pd.Timestamp(l["buy_date"][:10])))
        except Exception:
            continue
    if not dated:
        return None
    px = pd.concat({s: closes[s] for s in {l["symbol"] for l, _ in dated}}, axis=1).sort_index().ffill()
    start = min(bd for _, bd in dated)
    px = px[px.index >= start]
    if len(px) < 2:
        return None
    idx = px.index
    value = pd.Series(0.0, index=idx)
    invested = pd.Series(0.0, index=idx)
    bench, nf = None, None
    if nifty is not None:
        nf = nifty.reindex(idx.union(nifty.index)).ffill().reindex(idx)
        if nf.isna().all():
            nf = None
        else:
            nf = nf.ffill().bfill()
            bench = pd.Series(0.0, index=idx)
    for l, bd in dated:
        mask = np.asarray(idx >= bd)
        if not mask.any():
            mask = np.asarray(idx == idx[-1])
        cost = l["quantity"] * l["buy_price"]
        series = px[l["symbol"]].fillna(l["buy_price"])
        value.loc[mask] += (series[mask] * l["quantity"]).values
        invested.loc[mask] += cost
        if bench is not None:
            p0 = float(nf[mask].iloc[0])
            if p0 > 0:
                bench.loc[mask] += (nf[mask] * (cost / p0)).values
    df = pd.DataFrame({"Value": value, "Invested": invested})
    if bench is not None:
        df["NIFTY 50"] = bench
    return {"df": df, "excluded": len(lots) - len(dated)}


def risk_metrics(holds, closes, nifty):
    """Risk of today's weights over the last ~1 year of daily returns."""
    syms = [h["symbol"] for h in holds if h["value"] and h["symbol"] in closes]
    if not syms:
        return None
    px = pd.concat({s: closes[s] for s in syms}, axis=1).sort_index().ffill().tail(300)
    rets = (px / px.shift(1) - 1).iloc[1:].tail(252)
    if len(rets) < 60:
        return None
    w = pd.Series({h["symbol"]: h["value"] for h in holds if h["symbol"] in syms}, dtype="float64")
    w = w / w.sum()
    avail = rets.notna()
    wsum = avail.mul(w, axis=1).sum(axis=1)
    port = rets.fillna(0.0).mul(w, axis=1).sum(axis=1) / wsum
    port = port.replace([np.inf, -np.inf], np.nan).dropna()
    if len(port) < 60:
        return None
    total_val = sum(h["value"] for h in holds if h["value"])
    cum = (1 + port).cumprod()
    out = {
        "n_days": len(port),
        "vol": float(port.std() * math.sqrt(252) * 100),
        "mdd": float((cum / cum.cummax() - 1).min() * 100),
        "ret1y": float((cum.iloc[-1] - 1) * 100),
        "var95": float(-np.percentile(port.values, 5) * total_val),
        "beta": None, "bench_ret": None, "corr": None, "avg_corr": None, "top_pair": None,
    }
    if nifty is not None:
        nr = (nifty / nifty.shift(1) - 1).dropna()
        j = pd.concat([port.rename("p"), nr.rename("n")], axis=1, join="inner").dropna()
        if len(j) >= 60 and j["n"].var() > 0:
            out["beta"] = float(j["p"].cov(j["n"]) / j["n"].var())
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
            j = int(np.nanargmax(pv))
            out["top_pair"] = (cols[iu[0][j]], cols[iu[1][j]], float(pv[j]))
    return out


def diversification_score(conc, sectors, risk):
    """0-100. Transparent: starts at 100, loses points for concentration."""
    if not conc:
        return None
    known = [s for s in sectors if s["name"] != UNCLASSIFIED]
    pen = min(30.0, max(0.0, conc["top1"] - 10) * 1.2)                          # biggest position
    if known:
        pen += min(30.0, max(0.0, known[0]["weight"] - 25) * 1.0)                # biggest sector
    pen += min(25.0, max(0.0, 10 - (conc["eff_n"] or 1)) * 3)                    # effective number of stocks
    if risk and risk.get("avg_corr") is not None:
        pen += min(15.0, max(0.0, risk["avg_corr"] - 0.5) * 60)                  # holdings moving together
    return int(max(0, round(100 - pen)))


def build_flags(A, holds, news, days_label):
    """Plain-English health check. Rules of thumb, not advice."""
    flags = []

    def add(level, title, detail):
        flags.append({"level": level, "title": title, "detail": detail})

    conc, sectors, mcap, risk = A["conc"], A["sectors"], A["mcap"], A["risk"]
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
        if len(known) == 1 and n >= 3:
            add("watch", "Single sector", "All classified holdings sit in one sector.")
    unk = [h["symbol"] for h in holds if h["sector"] == UNCLASSIFIED]
    if unk:
        add("info", "Sector data missing", "Could not classify: " + ", ".join(unk[:8]) + (" …" if len(unk) > 8 else "") + ". It retries automatically.")

    small = next((m for m in mcap if m["name"] == "Small cap"), None)
    if small and small["weight"] > 40:
        add("watch", "Small-cap heavy", f'{small["weight"]:.0f}% of value is in small caps, which are typically more volatile and less liquid.')

    if risk:
        if risk["top_pair"] and risk["top_pair"][2] > 0.8:
            a, b, c = risk["top_pair"]
            add("watch", "Highly correlated pair", f"{a} and {b} have moved almost together (correlation {c:.2f}) — they add less diversification than two separate stocks suggest.")
        if risk["avg_corr"] is not None and risk["avg_corr"] > 0.6:
            add("watch", "Holdings move together", f'Average correlation between your top holdings is {risk["avg_corr"]:.2f}.')
        if risk["mdd"] < -25:
            add("watch", "Deep drawdown", f'At today\'s weights the portfolio fell {abs(risk["mdd"]):.0f}% peak-to-trough in the last year.')
        if risk["beta"] is not None:
            if risk["beta"] > 1.3:
                add("watch", "High market sensitivity", f'Beta vs NIFTY 50 is {risk["beta"]:.2f} — expect larger swings than the index.')
            elif risk["beta"] < 0.7:
                add("info", "Defensive profile", f'Beta vs NIFTY 50 is {risk["beta"]:.2f} — the portfolio moves less than the index.')

    losers = sorted([h for h in holds if h["pnl_pct"] is not None and h["pnl_pct"] <= -20], key=lambda h: h["pnl_pct"])
    if losers:
        add("watch", "Deep losers", f"{len(losers)} position{'s are' if len(losers) != 1 else ' is'} down 20% or more: " +
            ", ".join(f'{h["symbol"]} ({h["pnl_pct"]:.0f}%)' for h in losers[:5]) + ".")
    if A["tot"]["n_valid"] < n:
        add("watch", "Missing prices", f'{n - A["tot"]["n_valid"]} holding(s) have no live price and are excluded from totals.')
    wn = [h["symbol"] for h in holds if news.get(h["symbol"])]
    if wn:
        add("info", "Fresh headlines", f"News in the last {days_label} for: " + ", ".join(wn[:8]) + (" …" if len(wn) > 8 else "") + ".")
    order = {"alert": 0, "watch": 1, "info": 2, "ok": 3}
    return sorted(flags, key=lambda f: order[f["level"]])


def build_analytics(lots, holds, quotes, closes, news, days_label):
    valid = [h for h in holds if h["value"] is not None]
    inv = sum(h["invested"] for h in holds)
    inv_v = sum(h["invested"] for h in valid)
    val = sum(h["value"] for h in valid)
    day = sum(h["day_pnl"] for h in valid if h["day_pnl"] is not None)
    tot = {
        "inv": inv, "inv_v": inv_v, "val": val, "pnl": val - inv_v,
        "pnl_pct": ((val / inv_v - 1) * 100) if inv_v else None,
        "day": day, "day_pct": (day / (val - day) * 100) if (val - day) else None,
        "n": len(holds), "n_valid": len(valid), "n_lots": len(lots),
    }
    nifty = _nifty()
    sectors = group_table(holds, "sector")
    A = {
        "tot": tot, "nifty_ok": nifty is not None,
        "conc": concentration(holds), "sectors": sectors, "scol": sector_colors(sectors),
        "industries": group_table(holds, "industry"), "mcap": mcap_split(holds),
        "fund": weighted_fundamentals(holds), "risk": risk_metrics(holds, closes, nifty),
        "perf": performance_series(lots, closes, nifty),
    }
    A["xirr"], A["xirr_days"] = portfolio_xirr(lots, quotes)
    A["score"] = diversification_score(A["conc"], sectors, A["risk"])
    A["flags"] = build_flags(A, holds, news, days_label)
    return A


# ═════════════════════════ charts ═════════════════════════
def _fig_perf(perf):
    df = perf["df"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["Value"], name="Your portfolio", mode="lines",
                             line=dict(color=AMBER, width=2), fill="tozeroy", fillcolor="rgba(255,159,10,0.07)",
                             hovertemplate="%{x|%d %b %Y}<br>Value ₹%{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=df.index, y=df["Invested"], name="Invested", mode="lines",
                             line=dict(color="#8A8A8A", width=1.4, dash="dot", shape="hv"),
                             hovertemplate="Invested ₹%{y:,.0f}<extra></extra>"))
    if "NIFTY 50" in df:
        fig.add_trace(go.Scatter(x=df.index, y=df["NIFTY 50"], name="NIFTY 50, same cash flows", mode="lines",
                                 line=dict(color=CYAN, width=1.6),
                                 hovertemplate="NIFTY equivalent ₹%{y:,.0f}<extra></extra>"))
    _layout(fig, 300, (0, 6, 6, 0), legend=True)
    fig.update_layout(legend=dict(orientation="h", y=1.08, x=0, font=dict(size=10)), hovermode="x unified")
    fig.update_xaxes(showgrid=False, linecolor=BORDER)
    fig.update_yaxes(showgrid=True, gridcolor="#161616", zeroline=False)
    lo = float(min(df.min().min(), 0)) if False else 0.0
    _inr_ticks(fig, lo, float(df.max().max()) * 1.05)
    return fig


def _fig_donut(sectors, scol, total_value):
    rows = [s for s in sectors if s["value"] > 0]
    fig = go.Figure(go.Pie(
        labels=[s["name"] for s in rows], values=[s["value"] for s in rows], hole=0.66, sort=False,
        marker=dict(colors=[scol[s["name"]] for s in rows], line=dict(color="#050505", width=2)),
        textinfo="percent", textfont=dict(size=10, color="#fff"),
        hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>"))
    _layout(fig, 250)
    fig.add_annotation(text=f"<b>{_short(total_value)}</b><br><span style='font-size:9px'>VALUE</span>",
                       showarrow=False, font=dict(size=14, color=IVORY, family="JetBrains Mono, monospace"))
    return fig


def _fig_weights(holds, scol):
    rows = [h for h in holds if h["weight"]][:10]
    fig = go.Figure(go.Bar(
        x=[h["weight"] for h in rows], y=[h["symbol"] for h in rows], orientation="h",
        marker=dict(color=[scol.get(h["sector"], T3) for h in rows]),
        text=[f'{h["weight"]:.1f}%' for h in rows], textposition="outside", cliponaxis=False,
        textfont=dict(size=10, color=T2), hovertemplate="<b>%{y}</b> %{x:.1f}%<extra></extra>"))
    _layout(fig, 250, (0, 34, 0, 0))
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
    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="remainder", customdata=custom,
        marker=dict(colors=colors, colorscale=[[0.0, "#B3261E"], [0.5, "#1b1b1b"], [1.0, "#1E9E4A"]],
                    cmin=-lim, cmax=lim, showscale=False, line=dict(width=1.5, color="#050505")),
        texttemplate="<b>%{label}</b><br>%{percentRoot:.1%}", textfont=dict(family="Inter, sans-serif", size=12, color="#fff"),
        hovertemplate="<b>%{label}</b><br>Value %{customdata[0]}<br>P&L %{customdata[1]}<extra></extra>",
        pathbar=dict(visible=False), tiling=dict(pad=2), root_color="rgba(0,0,0,0)"))
    _layout(fig, 340)
    return fig


def _fig_mcap(split):
    colors = {"Large cap": CYAN, "Mid cap": AMBER, "Small cap": PURPLE, "Unknown": UNCLASSIFIED_COLOR}
    rows = [m for m in split if m["value"] > 0]
    fig = go.Figure(go.Bar(
        x=[m["weight"] for m in rows], y=[m["name"] for m in rows], orientation="h",
        marker=dict(color=[colors[m["name"]] for m in rows]),
        text=[f'{m["weight"]:.1f}%' for m in rows], textposition="outside", cliponaxis=False,
        textfont=dict(size=10, color=T2), hovertemplate="<b>%{y}</b> %{x:.1f}%<extra></extra>"))
    _layout(fig, 40 * max(len(rows), 1) + 30, (0, 40, 0, 0))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=IVORY, size=10))
    fig.update_xaxes(visible=False, range=[0, 115])
    return fig


def _fig_corr(corr):
    names = list(corr.columns)
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=names, y=names, zmin=-1, zmax=1, xgap=2, ygap=2,
        colorscale=[[0.0, "#1E5BA8"], [0.5, "#171717"], [1.0, "#FF9F0A"]], showscale=False,
        texttemplate="%{z:.2f}", textfont=dict(size=9, color="#fff"),
        hovertemplate="%{x} / %{y}: %{z:.2f}<extra></extra>"))
    _layout(fig, min(520, 38 * len(names) + 70), (0, 0, 0, 0))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=9, color=IVORY))
    fig.update_xaxes(tickfont=dict(size=9, color=IVORY), side="top")
    return fig


def _fig_gauge(score):
    color = GREEN if score >= 75 else AMBER if score >= 50 else RED
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=score,
        number=dict(font=dict(size=34, color=color, family="JetBrains Mono, monospace")),
        gauge=dict(axis=dict(range=[0, 100], tickcolor=T3, tickfont=dict(size=8, color=T3)),
                   bar=dict(color=color, thickness=0.28), bgcolor="#111", borderwidth=0,
                   steps=[dict(range=[0, 50], color="#241211"), dict(range=[50, 75], color="#241f10"),
                          dict(range=[75, 100], color="#10241a")])))
    _layout(fig, 170, (14, 14, 12, 0))
    return fig


def _fig_pnl_bars(holds):
    pv = sorted([h for h in holds if h["pnl"] is not None], key=lambda h: h["pnl"])
    fig = go.Figure(go.Bar(x=[h["symbol"] for h in pv], y=[h["pnl"] for h in pv],
                           marker_color=[GREEN if h["pnl"] >= 0 else RED for h in pv],
                           hovertemplate="<b>%{x}</b> ₹%{y:,.0f}<extra></extra>"))
    _layout(fig, 250, (0, 0, 4, 0))
    fig.update_xaxes(showgrid=False, tickfont=dict(size=9))
    fig.update_yaxes(showgrid=True, gridcolor="#161616", zeroline=True, zerolinecolor="#333")
    return fig


# ═════════════════════════ small HTML blocks ═════════════════════════
def _flags_html(flags, limit=None):
    colors = {"alert": RED, "watch": AMBER, "info": CYAN, "ok": GREEN}
    labels = {"alert": "ALERT", "watch": "WATCH", "info": "INFO", "ok": "OK"}
    out = ""
    for f in (flags[:limit] if limit else flags):
        c = colors[f["level"]]
        out += (f'<div class="pf-flag" style="--fc:{c}"><div class="lv">{labels[f["level"]]}</div>'
                f'<div><div class="ft">{_esc(f["title"])}</div><div class="fd">{_esc(f["detail"])}</div></div></div>')
    return out


def _group_table_html(rows, colors=None, show_syms=True):
    body = ""
    for r in rows:
        c = (colors or {}).get(r["name"], T2)
        syms = ", ".join(r["syms"][:4]) + (" …" if len(r["syms"]) > 4 else "")
        sub = f'<div class="sub">{_esc(syms)}</div>' if show_syms else ""
        w = max(0.0, min(100.0, r["weight"]))
        body += (f'<tr><td><span class="pf-sw" style="background:{c}"></span>{_esc(r["name"])}{sub}</td>'
                 f'<td class="r">{r["n"]}</td><td class="r">{_short(r["value"])}</td>'
                 f'<td><span class="pf-wbar"><i style="width:{w:.1f}%;background:{c}"></i></span>'
                 f'<span class="pf-mini">{r["weight"]:.1f}%</span></td>'
                 f'<td class="r" style="color:{_col(r["pnl_pct"])}">{_pct(r["pnl_pct"])}</td></tr>')
    return ('<table class="pf-tbl"><tr><th>Name</th><th class="r">Stocks</th><th class="r">Value</th>'
            '<th>Weight</th><th class="r">P&amp;L</th></tr>' + body + '</table>')


def _fund_table_html(holds, scol):
    body = ""
    for h in holds:
        c = scol.get(h["sector"], T2)
        pos = None
        if h["ltp"] is not None and h["hi52"] and h["lo52"] and h["hi52"] > h["lo52"]:
            pos = max(0.0, min(1.0, (h["ltp"] - h["lo52"]) / (h["hi52"] - h["lo52"])))
        rng = (f'<span class="pf-rng" title="52-week range"><i style="left:{pos * 100:.0f}%"></i></span> '
               f'<span class="pf-mini">{pos * 100:.0f}%</span>') if pos is not None else "—"
        mc = "—" if h["mcap"] is None else f'₹{h["mcap"]:,.0f} Cr'
        body += (f'<tr><td><b style="color:{AMBER}">{_esc(h["symbol"])}</b>'
                 f'<div class="sub"><span class="pf-sw" style="background:{c};width:6px;height:6px"></span>{_esc(h["sector"])}</div></td>'
                 f'<td class="r">{_num(h["pe"], 1, "x")}</td><td class="r">{_num(h["roe"], 1, "%")}</td>'
                 f'<td class="r">{_num(h["roce"], 1, "%")}</td><td class="r">{_num(h["dy"], 2, "%")}</td>'
                 f'<td class="r">{mc}</td><td>{rng}</td>'
                 f'<td class="r">{_num(h["weight"], 1, "%")}</td></tr>')
    return ('<table class="pf-tbl"><tr><th>Stock</th><th class="r">P/E</th><th class="r">ROE</th><th class="r">ROCE</th>'
            '<th class="r">Div yld</th><th class="r">Mkt cap</th><th>52W position</th><th class="r">Weight</th></tr>'
            + body + '</table>')


def _movers_html(holds):
    ranked = [h for h in holds if h["chg"] is not None]
    if not ranked:
        return '<div class="pf-note">No live price changes yet.</div>'
    ranked.sort(key=lambda h: h["chg"], reverse=True)
    up, dn = ranked[:3], [h for h in ranked[::-1][:3] if h not in ranked[:3]]
    out = ""
    for h in up + dn:
        c = _col(h["chg"])
        arrow = "▲" if h["chg"] >= 0 else "▼"
        out += (f'<div class="pf-mover"><b>{_esc(h["symbol"])}</b><span style="color:{T2}">{_short(h["day_pnl"])}</span>'
                f'<span style="color:{c}">{arrow} {abs(h["chg"]):.2f}%</span></div>')
    return out


# ═════════════════════════ forms / sections ═════════════════════════
def _missing_table_help():
    st.error("The `portfolio` table does not exist in Supabase yet. Run this once in Supabase → SQL Editor, then try again:")
    st.code(ud.SQL_PORTFOLIO, language="sql")


def _add_form(supabase, key="main", preset=""):
    with st.form(f"pf_add_{key}", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1.2, 1.4, 1.4])
        sym = c1.text_input("Stock (NSE symbol)", value=preset, placeholder="e.g. RELIANCE")
        qty = c2.number_input("Quantity", min_value=0.0, value=0.0, step=1.0, format="%g")
        price = c3.number_input("Buy price (₹)", min_value=0.0, value=0.0, step=0.05, format="%.2f")
        when = c4.date_input("Buy date", value=date.today(), max_value=date.today())
        note = st.text_input("Note (optional)", placeholder="e.g. breakout entry, SL 1180")
        go_btn = st.form_submit_button("＋ ADD TO PORTFOLIO", type="primary", use_container_width=True)
    if not go_btn:
        return
    s = ud.clean_symbol(sym)
    if not s:
        st.error("Enter the stock symbol.")
    elif qty <= 0 or price <= 0:
        st.error("Quantity and buy price must be greater than zero.")
    elif not get_quotes((s,)).get(s):
        st.error(f"No price data found for “{s}”. Use the NSE trading symbol (e.g. M&M, BAJAJ-AUTO).")
    else:
        ok, msg = ud.add_lot(supabase, s, qty, price, when, note, name=_lookup_name(s))
        if ok:
            st.toast(f"{s}: {qty:g} @ {_inr(price)} added")
            if msg:
                st.warning(msg)
            st.rerun()
        elif msg == "MISSING_TABLE":
            _missing_table_help()
        else:
            st.error(msg)


_SORTS = {
    "Value ↓": lambda h: (h["value"] is None, -(h["value"] or 0)),
    "P&L ₹ ↓": lambda h: (h["pnl"] is None, -(h["pnl"] or 0)),
    "P&L ₹ ↑": lambda h: (h["pnl"] is None, h["pnl"] or 0),
    "P&L % ↓": lambda h: (h["pnl_pct"] is None, -(h["pnl_pct"] or 0)),
    "P&L % ↑": lambda h: (h["pnl_pct"] is None, h["pnl_pct"] or 0),
    "Day % ↓": lambda h: (h["chg"] is None, -(h["chg"] or 0)),
    "Day % ↑": lambda h: (h["chg"] is None, h["chg"] or 0),
    "Sector": lambda h: (h["sector"], -(h["value"] or 0)),
    "A–Z": lambda h: h["symbol"],
}


def _holding_row_html(x, news, scol):
    sym = x["symbol"]
    arts = news.get(sym) or []
    dot = ""
    if arts:
        plural = "s" if len(arts) > 1 else ""
        tip = _esc(f'{len(arts)} headline{plural} · latest: {arts[0].get("title", "")}', True)
        dot = f'<span class="pf-dot" title="{tip}"></span>'
    name = f'<span class="pf-name">{_esc(x["name"])}</span>' if x["name"] else ""
    chip = f'<span class="pf-chip" style="--c:{scol.get(x["sector"], T3)}">{_esc(x["sector"])}</span>'
    chg_txt = _pct(x["chg"])
    pnl_pct_txt = _pct(x["pnl_pct"])
    w = x["weight"]
    wbar = ""
    if w is not None:
        wbar = (f'<span class="pf-wbar" style="width:34px"><i style="width:{min(100.0, w * 2):.0f}%;'
                f'background:{scol.get(x["sector"], AMBER)}"></i></span>{w:.1f}%')
    else:
        wbar = "—"
    return (f'<div class="pf-cell pf-grid">'
            f'<span><span class="pf-sym">{_esc(sym)}</span>{dot}{name}{chip}</span>'
            f'<span>{x["qty"]:g}</span><span>{_inr(x["avg"])}</span><span>{_inr(x["ltp"])}</span>'
            f'<span style="color:{_col(x["chg"])}">{chg_txt}</span>'
            f'<span>{_inr(x["invested"], 0)}</span><span>{_inr(x["value"], 0)}</span>'
            f'<span style="color:{_col(x["pnl"])}">{_inr(x["pnl"], 0)}</span>'
            f'<span style="color:{_col(x["pnl"])}">{pnl_pct_txt}</span>'
            f'<span>{wbar}</span></div>')


def _holdings_table(holds, news, on_open, scol):
    c1, c2, c3 = st.columns([1.3, 1.6, 4])
    sort = c1.selectbox("Sort by", list(_SORTS), key="pf_sort", label_visibility="collapsed")
    sectors_present = sorted({h["sector"] for h in holds})
    sec_f = c2.selectbox("Sector", ["All sectors"] + sectors_present, key="pf_sector_filter", label_visibility="collapsed")
    df = pd.DataFrame([{
        "Symbol": h["symbol"], "Name": h["name"], "Sector": h["sector"], "Industry": h["industry"],
        "Quantity": h["qty"], "Avg buy": round(h["avg"], 2), "LTP": h["ltp"], "Invested": round(h["invested"], 2),
        "Value": None if h["value"] is None else round(h["value"], 2),
        "P&L": None if h["pnl"] is None else round(h["pnl"], 2),
        "P&L %": None if h["pnl_pct"] is None else round(h["pnl_pct"], 2),
        "Weight %": None if h["weight"] is None else round(h["weight"], 2),
    } for h in holds])
    c3.download_button("⬇ EXPORT CSV", df.to_csv(index=False), file_name="arka_portfolio.csv", mime="text/csv", key="pf_csv")

    rows = [h for h in holds if sec_f == "All sectors" or h["sector"] == sec_f]
    rows = sorted(rows, key=_SORTS[sort])

    heads = ["STOCK", "QTY", "AVG BUY", "LTP", "DAY %", "INVESTED", "VALUE", "P&L", "P&L %", "WEIGHT"]
    h = st.columns([9.6, 0.8, 0.85])
    h[0].markdown('<div class="pf-head pf-grid">' + "".join(f"<span>{x}</span>" for x in heads) + "</div>", unsafe_allow_html=True)
    with st.container(key="pfrows_main"):
        for x in rows:
            sym = x["symbol"]
            cols = st.columns([9.6, 0.8, 0.85])
            cols[0].markdown(_holding_row_html(x, news, scol), unsafe_allow_html=True)
            if cols[1].button("OPEN", key=f"pf_open_{sym}", use_container_width=True):
                on_open(sym)
            if cols[2].button("DETAILS", key=f"pf_det_{sym}", use_container_width=True, help="Lots, add more shares, remove"):
                st.session_state["pf_sel"] = sym
                st.rerun()


def _detail_panel(supabase, holds, news):
    sym = st.session_state.get("pf_sel")
    h = next((x for x in holds if x["symbol"] == sym), None)
    if not h:
        st.session_state.pop("pf_sel", None)
        return
    _ph(f"{sym} · lots & management", h["sector"])
    top = st.columns([6, 1])
    top[0].markdown(f'<div class="pf-note">{_esc(h["name"] or sym)} · {h["qty"]:g} shares · average cost {_inr(h["avg"])} · '
                    f'invested {_inr(h["invested"], 0)} · current {_inr(h["value"], 0)}</div>', unsafe_allow_html=True)
    if top[1].button("CLOSE", key="pf_det_close", use_container_width=True):
        st.session_state.pop("pf_sel", None)
        st.rerun()

    widths = [1.2, 0.9, 1.2, 1.3, 1.3, 0.9, 0.8, 1.8, 0.6]
    hd = st.columns(widths)
    for c, t in zip(hd, ["DATE", "QTY", "BUY PRICE", "INVESTED", "LOT P&L", "LOT %", "HELD", "NOTE", ""]):
        c.markdown(f'<div class="pf-head">{t}</div>', unsafe_allow_html=True)
    today = datetime.now(IST).date()
    for l in h["lots"]:
        lp = (h["ltp"] - l["buy_price"]) * l["quantity"] if h["ltp"] is not None else None
        lpp = (h["ltp"] / l["buy_price"] - 1) * 100 if (h["ltp"] is not None and l["buy_price"]) else None
        held = "—"
        try:
            held = f'{(today - datetime.strptime(l["buy_date"][:10], "%Y-%m-%d").date()).days}d'
        except Exception:
            pass
        r = st.columns(widths)
        r[0].markdown(f'<div class="pf-cell">{_esc(l["buy_date"] or "—")}</div>', unsafe_allow_html=True)
        r[1].markdown(f'<div class="pf-cell">{l["quantity"]:g}</div>', unsafe_allow_html=True)
        r[2].markdown(f'<div class="pf-cell">{_inr(l["buy_price"])}</div>', unsafe_allow_html=True)
        r[3].markdown(f'<div class="pf-cell">{_inr(l["quantity"] * l["buy_price"], 0)}</div>', unsafe_allow_html=True)
        r[4].markdown(f'<div class="pf-cell" style="color:{_col(lp)}">{_inr(lp, 0)}</div>', unsafe_allow_html=True)
        r[5].markdown(f'<div class="pf-cell" style="color:{_col(lpp)}">{_pct(lpp, 1)}</div>', unsafe_allow_html=True)
        r[6].markdown(f'<div class="pf-cell" style="color:{T2}">{held}</div>', unsafe_allow_html=True)
        r[7].markdown(f'<div class="pf-cell" style="color:{T2}">{_esc(l["note"])}</div>', unsafe_allow_html=True)
        if r[8].button("✕", key=f"pf_dl_{l['id']}", help="Delete this lot", use_container_width=True):
            err = ud.delete_lot(supabase, l["id"])
            if err:
                st.error(err)
            else:
                st.rerun()

    _ph("Add more shares of this stock")
    _add_form(supabase, key=f"more_{sym}", preset=sym)
    with st.expander("Danger zone"):
        sure = st.checkbox(f"Yes, remove the entire {sym} holding ({len(h['lots'])} lots)", key=f"pf_sure_{sym}")
        if st.button(f"REMOVE {sym} FROM PORTFOLIO", disabled=not sure, key=f"pf_rm_all_{sym}"):
            err = ud.delete_holding(supabase, sym)
            if err:
                st.error(err)
            else:
                st.session_state.pop("pf_sel", None)
                st.rerun()


def _news_tab(holds, news, days_label):
    st.selectbox("News window", ["24 hours", "3 days", "7 days"], key="pf_news_window",
                 index=["24 hours", "3 days", "7 days"].index(days_label))
    with_news = [h for h in holds if news.get(h["symbol"])]
    if not with_news:
        st.markdown(f'<div class="pf-note">No relevant headlines for your holdings in the last {_esc(days_label)}. '
                    'A yellow light appears next to a stock the moment news arrives.</div>', unsafe_allow_html=True)
        return
    for h in with_news:
        arts = news[h["symbol"]]
        plural = "s" if len(arts) > 1 else ""
        st.markdown(f'<div class="pf-ph"><span class="pf-dot" style="margin:0 2px 0 0"></span>'
                    f'<span class="pf-ph-t">{_esc(h["symbol"])} · {len(arts)} headline{plural}</span></div>', unsafe_allow_html=True)
        for a in arts[:6]:
            sent = str(a.get("sentiment", "neutral"))
            c = GREEN if "positive" in sent else RED if "negative" in sent else T3
            link = _esc(a.get("link", "#"), True)
            st.markdown(
                f'<a href="{link}" target="_blank" style="text-decoration:none"><div style="border-left:2px solid {c};padding:6px 0 6px 10px;margin-bottom:5px;">'
                f'<div style="font:600 12px Inter,sans-serif;color:{IVORY}">{_esc(a.get("title", ""))}</div>'
                f'<div style="font:9px {MONO};color:{T2};margin-top:3px">{_esc(a.get("source", ""))} · {_esc(a.get("time_str", ""))} · '
                f'{_esc(a.get("event", "NEWS"))}</div></div></a>', unsafe_allow_html=True)


# ═════════════════════════ tabs ═════════════════════════
def _empty_state():
    st.markdown(
        '<div class="pf-empty"><div class="a">Build your portfolio review</div>'
        '<div class="b">Open the <b>ADD HOLDING</b> tab and enter each purchase: stock, quantity, buy price and date.<br>'
        'As soon as you add one, this page shows your sector exposure, concentration, valuation and quality metrics, '
        'risk, growth versus NIFTY 50, and an automatic health check. Everything is saved permanently.</div></div>',
        unsafe_allow_html=True)


def _tab_dashboard(A, holds, news, days_label):
    T = A["tot"]
    xi = A["xirr"]
    nn = sum(1 for h in holds if news.get(h["symbol"]))
    k = st.columns(6)
    k[0].markdown(_kpi("Invested", _short(T["inv"]), f'{T["n"]} holdings · {T["n_lots"]} lots', accent=T3), unsafe_allow_html=True)
    k[1].markdown(_kpi("Current value", _short(T["val"]), "live · delayed per source", accent=CYAN), unsafe_allow_html=True)
    k[2].markdown(_kpi("Total P&L", _short(T["pnl"]), _pct(T["pnl_pct"]), _col(T["pnl"]), _col(T["pnl"]), _col(T["pnl"])), unsafe_allow_html=True)
    k[3].markdown(_kpi("Day P&L", _short(T["day"]), _pct(T["day_pct"]), _col(T["day"]), _col(T["day"]), _col(T["day"])), unsafe_allow_html=True)
    if xi is not None:
        k[4].markdown(_kpi("XIRR (annualised)", _pct(xi * 100, 1), f'money-weighted · {A["xirr_days"]}d', _col(xi), T2, PURPLE), unsafe_allow_html=True)
    else:
        k[4].markdown(_kpi("XIRR (annualised)", "—", "needs buy dates 60+ days old", T3, T3, PURPLE), unsafe_allow_html=True)
    k[5].markdown(_kpi("Coverage", f'{len([s for s in A["sectors"] if s["name"] != UNCLASSIFIED])} sectors',
                       f'{nn} with news · {days_label}', IVORY, YELLOW if nn else T3, AMBER), unsafe_allow_html=True)
    if T["n_valid"] < T["n"]:
        st.caption("Some holdings have no live price right now and are excluded from the totals.")

    c1, c2 = st.columns([2.2, 1])
    with c1:
        _ph("Portfolio growth", "your holdings vs invested vs NIFTY 50 with identical cash flows")
        perf = A["perf"]
        if perf is None:
            st.markdown('<div class="pf-note">Growth chart needs lots with a buy date and enough price history.</div>', unsafe_allow_html=True)
        else:
            _plot(_fig_perf, "pf_perf", perf)
            last = perf["df"].iloc[-1]
            line = f'You: <b style="color:{AMBER}">{_short(last["Value"])}</b> on {_short(last["Invested"])} invested'
            if "NIFTY 50" in perf["df"]:
                diff = last["Value"] - last["NIFTY 50"]
                line += (f' · NIFTY 50 equivalent: <b style="color:{CYAN}">{_short(last["NIFTY 50"])}</b>'
                         f' · difference <b style="color:{_col(diff)}">{_short(diff)}</b>')
            if perf["excluded"]:
                line += f' · {perf["excluded"]} lot(s) without a buy date left out'
            st.markdown(f'<div class="pf-mini">{line}</div>', unsafe_allow_html=True)
    with c2:
        _ph("Portfolio health", "diversification score")
        if A["score"] is not None:
            _plot(_fig_gauge, "pf_gauge", A["score"])
            s = A["score"]
            lab, c = ("WELL DIVERSIFIED", GREEN) if s >= 75 else ("MODERATE", AMBER) if s >= 50 else ("CONCENTRATED", RED)
            st.markdown(f'<div class="pf-score" style="color:{c}">{lab}</div>', unsafe_allow_html=True)
        st.markdown(_flags_html(A["flags"], 3), unsafe_allow_html=True)

    d1, d2, d3 = st.columns([1, 1, 1])
    with d1:
        _ph("Sector allocation")
        _plot(_fig_donut, "pf_donut_dash", A["sectors"], A["scol"], T["val"])
    with d2:
        _ph("Top positions", "% of portfolio")
        _plot(_fig_weights, "pf_weights_dash", holds, A["scol"])
    with d3:
        _ph("Today's movers")
        st.markdown(_movers_html(holds), unsafe_allow_html=True)
    st.caption("Informational only, not investment advice. P&L excludes brokerage, taxes and charges.")


def _tab_analysis(A, holds):
    T, sectors, scol = A["tot"], A["sectors"], A["scol"]

    # ── sector & industry ──
    _ph("Sector & industry exposure", "tile size = value · colour = P&L %")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        _plot(_fig_treemap, "pf_treemap", holds, sectors)
    with c2:
        st.markdown(_group_table_html(sectors, scol), unsafe_allow_html=True)
    with st.expander("Industry breakdown"):
        st.markdown(_group_table_html(A["industries"], None, show_syms=True), unsafe_allow_html=True)

    # ── composition ──
    _ph("Portfolio composition")
    conc = A["conc"]
    d1, d2 = st.columns([1, 1])
    with d1:
        st.markdown('<div class="pf-mini" style="margin-bottom:4px">MARKET-CAP MIX (approximate buckets)</div>', unsafe_allow_html=True)
        _plot(_fig_mcap, "pf_mcap", A["mcap"])
    with d2:
        if conc:
            kk = st.columns(4)
            kk[0].markdown(_kpi("Top 1", _num(conc["top1"], 1, "%"), "largest position", accent=AMBER), unsafe_allow_html=True)
            kk[1].markdown(_kpi("Top 3", _num(conc["top3"], 1, "%"), "of portfolio", accent=AMBER), unsafe_allow_html=True)
            kk[2].markdown(_kpi("Top 5", _num(conc["top5"], 1, "%"), "of portfolio", accent=AMBER), unsafe_allow_html=True)
            kk[3].markdown(_kpi("Effective N", _num(conc["eff_n"], 1), f'of {conc["n"]} stocks', accent=CYAN), unsafe_allow_html=True)
    _plot(_fig_weights, "pf_weights_an", holds, scol)
    _plot(_fig_pnl_bars, "pf_pnl_an", holds)

    # ── fundamentals ──
    _ph("Valuation & quality", "value-weighted across holdings with data")
    F = A["fund"]
    f = st.columns(4)
    def cov(t):
        return f"{t[1]:.0f}% of value covered"
    f[0].markdown(_kpi("Weighted P/E", _num(F["pe"][0], 1, "x"), cov(F["pe"]), accent=CYAN), unsafe_allow_html=True)
    f[1].markdown(_kpi("Weighted ROE", _num(F["roe"][0], 1, "%"), cov(F["roe"]), accent=GREEN), unsafe_allow_html=True)
    f[2].markdown(_kpi("Weighted ROCE", _num(F["roce"][0], 1, "%"), cov(F["roce"]), accent=GREEN), unsafe_allow_html=True)
    f[3].markdown(_kpi("Dividend yield", _num(F["dy"][0], 2, "%"), cov(F["dy"]), accent=AMBER), unsafe_allow_html=True)
    st.markdown(_fund_table_html(holds, scol), unsafe_allow_html=True)
    st.caption("P/E is blended as a harmonic mean (the correct way to average P/E); loss-making companies are excluded from it.")

    # ── risk ──
    _ph("Risk", "today's weights over the last ~1 year of daily returns")
    R = A["risk"]
    if R is None:
        st.markdown('<div class="pf-note">Risk metrics need about 3 months of price history for your holdings.</div>', unsafe_allow_html=True)
    else:
        r = st.columns(5)
        r[0].markdown(_kpi("Volatility", _num(R["vol"], 1, "%"), "annualised", accent=AMBER), unsafe_allow_html=True)
        r[1].markdown(_kpi("Max drawdown", _pct(R["mdd"], 1, False), "peak to trough", RED, T2, RED), unsafe_allow_html=True)
        r[2].markdown(_kpi("1-day VaR (95%)", _short(R["var95"]), "historical, worst 1-in-20 day", accent=PURPLE), unsafe_allow_html=True)
        beta_sub = "vs NIFTY 50" if R["beta"] is not None else "needs yfinance"
        r[3].markdown(_kpi("Beta", _num(R["beta"], 2), beta_sub, accent=CYAN), unsafe_allow_html=True)
        sub = f'NIFTY 50: {_pct(R["bench_ret"], 1)}' if R["bench_ret"] is not None else "same window"
        r[4].markdown(_kpi("1Y return (basket)", _pct(R["ret1y"], 1), sub, _col(R["ret1y"]), T2, GREEN), unsafe_allow_html=True)
        if R["corr"] is not None:
            _ph("Correlation of top holdings", "closer to 1.00 = move together")
            _plot(_fig_corr, "pf_corr", R["corr"])
            if R["top_pair"]:
                a, b, c = R["top_pair"]
                st.markdown(f'<div class="pf-mini">Most correlated pair: <b style="color:{AMBER}">{_esc(a)}</b> and '
                            f'<b style="color:{AMBER}">{_esc(b)}</b> ({c:.2f}) · average {R["avg_corr"]:.2f}</div>', unsafe_allow_html=True)
        st.caption("Basket returns assume today's weights were held throughout the period — they describe risk, not your actual past performance.")

    # ── health ──
    _ph("Health check", "automatic review of your portfolio")
    st.markdown(_flags_html(A["flags"]), unsafe_allow_html=True)
    with st.expander("How the diversification score works"):
        st.markdown(
            "The score starts at 100 and loses points for: a largest position above 10% (up to 30 pts), "
            "a largest sector above 25% (up to 30), fewer than 10 effective equal-weight stocks (up to 25) and "
            "average correlation above 0.5 (up to 15). Thresholds are common rules of thumb, "
            "not recommendations — your own risk appetite matters more.")
    st.caption("Informational only, not investment advice.")


def _tab_holdings(supabase, holds, news, on_open, A):
    _holdings_table(holds, news, on_open, A["scol"])
    st.caption("Yellow light = relevant headlines in the selected news window. Prices are Yahoo Finance (NSE), delayed as per source. "
               "P&L excludes brokerage, taxes and charges.")
    _detail_panel(supabase, holds, news)


# ═════════════════════════ page ═════════════════════════
def render_portfolio(supabase, on_open):
    st.markdown(_CSS, unsafe_allow_html=True)
    lots, warn = ud.load_portfolio(supabase)
    if warn:
        if ud.table_missing(warn):
            _missing_table_help()
        else:
            st.warning(warn)

    days_label = st.session_state.get("pf_news_window", "24 hours")
    days = {"24 hours": 1, "3 days": 3, "7 days": 7}.get(days_label, 1)

    holds, news, quotes, A = [], {}, {}, None
    if lots:
        symbols = sorted({l["symbol"] for l in lots})
        quotes = get_quotes(tuple(symbols))
        infos = _load_infos(symbols)
        holds = aggregate(lots, quotes, infos)
        closes = {s: i["close"] for s, i in infos.items() if i and i.get("close") is not None}
        news = holding_news(symbols, {h["symbol"]: h["name"] for h in holds}, days)
        A = build_analytics(lots, holds, quotes, closes, news, days_label)

    # ── header ──
    now = datetime.now(IST).strftime("%d %b %Y · %H:%M IST")
    n_sec = len([s for s in A["sectors"] if s["name"] != UNCLASSIFIED]) if A else 0
    meta = f'{len(holds)} holdings · {n_sec} sectors<br>as of {now}' if A else f'no holdings yet<br>as of {now}'
    h1, h2 = st.columns([8, 1])
    h1.markdown(f'<div class="pf-hero"><div><div class="k">ARKA TRADES · PORTFOLIO REVIEW</div>'
                f'<div class="t">{_short(A["tot"]["val"]) if A else "Portfolio"}</div></div>'
                f'<div class="m">{meta}</div></div>', unsafe_allow_html=True)
    if h2.button("↻ REFRESH", key="pf_refresh", use_container_width=True, help="Reload live prices"):
        try:
            get_quotes.clear()
        except Exception:
            pass
        st.session_state.get("_pf_info_tried", {}).clear()
        st.rerun()

    tab_d, tab_a, tab_h, tab_add, tab_news = st.tabs(["DASHBOARD", "ANALYSIS", "HOLDINGS", "ADD HOLDING", "NEWS"])
    with tab_d:
        if A:
            _tab_dashboard(A, holds, news, days_label)
        else:
            _empty_state()
    with tab_a:
        if A:
            _tab_analysis(A, holds)
        else:
            _empty_state()
    with tab_h:
        if A:
            _tab_holdings(supabase, holds, news, on_open, A)
        else:
            _empty_state()
    with tab_add:
        st.markdown('<div class="pf-note">Add each purchase as a lot. Bought the same stock twice at different prices? Add two lots — '
                    'Arka shows the weighted-average cost and the analysis updates instantly.</div>', unsafe_allow_html=True)
        _add_form(supabase, key="main")
    with tab_news:
        _news_tab(holds, news, days_label)
