"""
Arka Trades — Watchlist Terminal (v2)

What changed vs v1
------------------
FASTER
  * v1 created three buttons (OPEN / ANALYSIS / REMOVE) for EVERY stock, plus st.tabs that built both lists
    (and ran both scans) on every click. 50 stocks = 150+ widgets per rerun. v2 draws the list as one HTML
    table and uses a single action bar, and only the list you are looking at is built.
  * The list lives in an st.fragment, so filtering, sorting and actions never rerun the rest of the app.

MORE USEFUL
  * Columns: last price, day change, 12-session change, RSI with a gauge, distance from yesterday's high (PDH)
    and low (PDL), where price sits inside yesterday's range, a trend sparkline and automatic signal tags
    (PDH BREAK, PDL BREAK, NEAR PDH, NEAR PDL, RSI 70+, RSI <30, 12D LEADER ...).
  * KPI strip with a breadth bar, best / worst performer and average RSI.
  * TABLE and HEATMAP views; quick filters as one-click pills; search; more sort orders.
  * Action bar: pick a stock → OPEN, AI ANALYSIS, REMOVE. "Manage list" removes many at once or cleans out
    symbols that return no data. CSV export of the table.
  * Members can VIEW the Arka desk list (read-only); only admins can edit it.

FIXED
  * Typing a company name ("tata motors") used to save the company's full name as the symbol, which never
    loads a price. The ticker is now taken from the Screener URL (/company/TATAMOTORS/).
  * After add / remove, st.session_state.watchlist / admin_watchlist are kept in sync, so the Home dashboard,
    Scanner and news rail see the change immediately.

Public API unchanged: render_watchlist(supabase, on_open=None, is_admin=False)
"""
from __future__ import annotations

import html
import io
import math
import re

import pandas as pd
import streamlit as st

import user_data as ud
from data_engine import get_quotes, scan_batch

try:
    from screener_scraper import resolve_symbol
except Exception:  # pragma: no cover
    resolve_symbol = None

# Terminal palette (same as app.py v7)
TX, TX2, TX3 = "#ECECEC", "#A3A3A3", "#6B6B6B"
AMBER, GREEN, RED, CYAN, PURPLE, YELLOW = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA", "#BF5AF2", "#FFD60A"
LINE, LINE2, S1, S2 = "#1F1F1F", "#2B2B2B", "#0A0A0A", "#0F0F0F"

FILTERS = ["ALL", "GAINERS", "LOSERS", "ABOVE PDH", "BELOW PDL", "NEAR PDH", "RSI 70+", "RSI < 35", "NO DATA"]
SORTS = ["Watchlist order", "Change % ↓", "Change % ↑", "12-session % ↓", "RSI ↓", "RSI ↑", "Closest to PDH", "Symbol A–Z"]
NEAR_PCT = 0.5          # "near" PDH / PDL = within 0.5 %


# ═════════════════════════ formatting ═════════════════════════
def _num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _e(x) -> str:
    return html.escape(str(x))


def _price(v):
    v = _num(v)
    return f"₹{v:,.2f}" if v is not None else "—"


def _pct_html(v, d=2, scale=3.0):
    v = _num(v)
    if v is None:
        return '<span class="wl-na">—</span>'
    c = GREEN if v > 0 else RED if v < 0 else TX2
    a = min(0.28, abs(v) / scale * 0.28)
    rgb = "48,209,88" if v >= 0 else "255,69,58"
    return f'<span class="wl-pct" style="color:{c};background:rgba({rgb},{a:.3f})">{"▲" if v > 0 else "▼" if v < 0 else "■"} {abs(v):.{d}f}%</span>'


def _spark(values, w=92, h=26):
    pts = [p for p in (_num(v) for v in (values or [])) if p is not None] if isinstance(values, (list, tuple)) else []
    if len(pts) < 2:
        return '<span class="wl-na">—</span>'
    lo, hi = min(pts), max(pts)
    span = hi - lo or 1.0
    xy = " ".join(f"{i / (len(pts) - 1) * w:.1f},{h - 3 - (p - lo) / span * (h - 7):.1f}" for i, p in enumerate(pts))
    c = GREEN if pts[-1] >= pts[0] else RED
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block">'
            f'<polyline points="{xy}" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _rsi_html(v):
    v = _num(v)
    if v is None:
        return '<span class="wl-na">—</span>'
    c = AMBER if v >= 70 else GREEN if v >= 50 else "#FF8A80" if v >= 35 else RED
    return (f'<span class="wl-gauge"><span class="b"><i style="width:{max(0, min(100, v)):.0f}%;background:{c}"></i></span>'
            f'<b style="color:{c}">{v:.0f}</b></span>')


def _range_html(pos):
    """Where price sits inside yesterday's range: 0 % = PDL, 100 % = PDH (can be outside)."""
    if pos is None:
        return '<span class="wl-na">—</span>'
    clamped = max(0.0, min(100.0, pos))
    c = GREEN if pos > 100 else RED if pos < 0 else AMBER
    label = "above" if pos > 100 else "below" if pos < 0 else f"{pos:.0f}%"
    return (f'<span class="wl-range"><span class="b"><i style="left:{clamped:.0f}%;background:{c}"></i></span>'
            f'<em style="color:{c if pos > 100 or pos < 0 else TX3}">{label}</em></span>')


# ═════════════════════════ styles ═════════════════════════
def _inject_styles():
    st.markdown(f"""<style>
    .wl-bar{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:2px 0 10px;}}
    .wl-bar b{{font:700 11px 'JetBrains Mono',monospace;letter-spacing:1.4px;color:{AMBER};}}
    .wl-bar span{{font:600 9px 'JetBrains Mono',monospace;letter-spacing:.8px;color:{TX3};}}
    .wl-kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:6px 0 10px;}}
    .wl-k{{background:{S1};border:1px solid {LINE};border-top:2px solid var(--a,{LINE2});padding:11px 13px;min-width:0;}}
    .wl-k .l{{font:700 8px 'JetBrains Mono',monospace;letter-spacing:1.1px;color:{TX3};text-transform:uppercase;}}
    .wl-k .v{{font:700 19px 'JetBrains Mono',monospace;font-variant-numeric:tabular-nums;color:{TX};margin-top:5px;white-space:nowrap;}}
    .wl-k .s{{font:10px 'JetBrains Mono',monospace;color:{TX3};margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
    .wl-breadth{{display:flex;height:4px;margin-top:8px;background:#151515;}} .wl-breadth i{{display:block;height:100%;}}
    .wl-tw{{overflow:auto;border:1px solid {LINE};background:{S1};max-height:680px;}}
    .wl-t{{width:100%;border-collapse:separate;border-spacing:0;font:12px 'Inter',sans-serif;}}
    .wl-t th{{position:sticky;top:0;z-index:2;background:{S2};color:{TX3};font:700 9px 'JetBrains Mono',monospace;letter-spacing:.9px;
             text-transform:uppercase;text-align:right;padding:10px;border-bottom:1px solid {LINE2};white-space:nowrap;}}
    .wl-t th.l,.wl-t td.l{{text-align:left;}}
    .wl-t td{{padding:8px 10px;border-bottom:1px solid #141414;text-align:right;font:11px 'JetBrains Mono',monospace;
             font-variant-numeric:tabular-nums;color:{TX2};white-space:nowrap;vertical-align:middle;}}
    .wl-t tbody tr:hover td{{background:rgba(255,159,10,.045);}}
    .wl-t td.rk{{color:#4a4a4a;width:26px;}}
    .wl-sym b{{display:block;font:700 12px 'JetBrains Mono',monospace;color:{TX};letter-spacing:.3px;}}
    .wl-sym span{{font:9px 'Inter',sans-serif;color:{TX3};}}
    .wl-pct{{display:inline-block;min-width:74px;text-align:right;padding:2px 7px;border-radius:3px;font:700 11px 'JetBrains Mono',monospace;}}
    .wl-na{{color:#444;}}
    .wl-gauge{{display:inline-flex;align-items:center;gap:7px;}} .wl-gauge .b{{width:44px;height:4px;background:#1b1b1b;display:inline-block;}}
    .wl-gauge .b i{{display:block;height:100%;}} .wl-gauge b{{font:700 11px 'JetBrains Mono',monospace;min-width:20px;text-align:right;}}
    .wl-range{{display:inline-flex;align-items:center;gap:7px;}} .wl-range .b{{position:relative;width:62px;height:4px;
             background:linear-gradient(90deg,rgba(255,69,58,.35),#262626 50%,rgba(48,209,88,.35));display:inline-block;}}
    .wl-range .b i{{position:absolute;top:-3px;width:3px;height:10px;border-radius:1px;transform:translateX(-1px);}}
    .wl-range em{{font:600 9px 'JetBrains Mono',monospace;font-style:normal;min-width:34px;text-align:left;}}
    .wl-tag{{display:inline-block;margin:1px 3px 1px 0;padding:1px 6px;border-radius:3px;font:800 8px 'JetBrains Mono',monospace;
            letter-spacing:.6px;border:1px solid currentColor;background:rgba(255,255,255,.02);}}
    .wl-heat{{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:6px;}}
    .wl-tile{{border:1px solid {LINE};padding:10px 11px;min-height:74px;}}
    .wl-tile b{{display:block;font:700 12px 'JetBrains Mono',monospace;color:{TX};}}
    .wl-tile .p{{font:600 11px 'JetBrains Mono',monospace;color:{TX2};margin-top:4px;}}
    .wl-tile .c{{font:800 14px 'JetBrains Mono',monospace;margin-top:4px;}}
    .wl-empty{{padding:34px 18px;text-align:center;color:{TX3};border:1px dashed {LINE2};background:{S1};font-size:12px;line-height:1.8;}}
    .wl-empty b{{color:{TX2};}}
    div[class*="st-key-wl_actions_"]{{border:1px solid {LINE};border-top:2px solid {AMBER};background:{S1};padding:10px 12px 4px;margin-top:8px;}}
    div[class*="st-key-wl_filters_"] [role="radiogroup"]{{gap:4px !important;}}
    div[class*="st-key-wl_filters_"] label[data-baseweb="radio"]{{padding:4px 9px !important;}}
    </style>""", unsafe_allow_html=True)


# ═════════════════════════ symbols ═════════════════════════
def _normalise_symbol(raw) -> str:
    try:
        s = ud.clean_symbol(str(raw).strip())
        return str(s).upper().strip() if s else ""
    except Exception:
        return ""


def _ticker_from_screener(query: str) -> str:
    """Company name / odd input -> NSE ticker via Screener's company URL (/company/TATAMOTORS/...)."""
    if resolve_symbol is None:
        return ""
    try:
        res = resolve_symbol(query)
    except Exception:
        return ""
    if not isinstance(res, dict):
        return ""
    m = re.search(r"/company/([^/]+)/", str(res.get("url", "")))
    if not m or m.group(1).isdigit():          # numeric ids are BSE-only listings, not NSE tickers
        return ""
    return _normalise_symbol(m.group(1))


def resolve_tokens(text):
    """Validate symbols with one batched quote request; unknown entries go through Screener. -> (valid, unresolved)."""
    pairs = {}                                            # cleaned symbol -> what the user typed (first occurrence)
    for t in ud.split_tokens(text):
        sym = _normalise_symbol(t)
        if sym and sym not in pairs:
            pairs[sym] = str(t).strip()
    symbols = list(pairs)
    if not symbols:
        return [], []
    try:
        quotes = get_quotes(tuple(symbols)) or {}
    except Exception:
        quotes = {}
    valid, unresolved = [], []
    for sym, raw in pairs.items():
        q = quotes.get(sym)
        if isinstance(q, dict) and _num(q.get("price")) is not None:
            valid.append(sym)
            continue
        ticker = _ticker_from_screener(str(raw))
        if ticker:
            valid.append(ticker)
        else:
            unresolved.append(sym)
    return list(dict.fromkeys(valid)), unresolved


def _sync_session(table, symbols):
    """Keep the app-wide lists (Home, Scanner, news rail) in step with the database."""
    if table == ud.W_TABLE:
        st.session_state["watchlist"] = list(symbols)
    elif table == getattr(ud, "A_TABLE", None):
        st.session_state["admin_watchlist"] = list(symbols)


# ═════════════════════════ add / import ═════════════════════════
def _add_box(supabase, table, key):
    c1, c2 = st.columns([3.2, 1.3], gap="small")
    with c1:
        with st.form(f"wl_add_form_{key}", clear_on_submit=True):
            a, b = st.columns([4.2, 1])
            raw = a.text_input("Add symbols", placeholder="Add stocks: RELIANCE, HDFCBANK, tata motors …", label_visibility="collapsed")
            submitted = b.form_submit_button("＋ ADD", use_container_width=True, type="primary")
    with c2:
        with st.popover("⇪ IMPORT CSV / TXT", use_container_width=True) if hasattr(st, "popover") else st.expander("IMPORT CSV / TXT"):
            _import_box(supabase, table, key)
    if submitted:
        if not raw.strip():
            st.warning("Enter at least one stock symbol or company name.")
            return
        with st.spinner("Validating symbols…"):
            valid, unresolved = resolve_tokens(raw)
        if valid:
            try:
                ud.add_symbols(supabase, valid, table)
                st.toast(f"Added {', '.join(valid[:6])}{'…' if len(valid) > 6 else ''}")
            except Exception as exc:
                st.error(f"Could not save symbols: {exc}")
        if unresolved:
            st.warning("Not found on NSE: " + ", ".join(unresolved) + ". Check the spelling or use the NSE ticker.")


def _import_box(supabase, table, key):
    st.caption("TradingView exports and CSVs work. A column named symbol / ticker is used, otherwise the first column.")
    up = st.file_uploader("Choose a file", type=["csv", "txt"], key=f"wl_upload_{key}", label_visibility="collapsed")
    if up is None:
        return
    try:
        content = up.getvalue().decode("utf-8-sig", errors="replace")
        if up.name.lower().endswith(".csv"):
            frame = pd.read_csv(io.StringIO(content))
            if frame.empty:
                st.warning("The file contains no symbols.")
                return
            pref = [c for c in frame.columns if str(c).strip().lower() in {"symbol", "symbols", "ticker", "stock", "tradingsymbol"}]
            raw = " ".join(frame[pref[0] if pref else frame.columns[0]].dropna().astype(str).tolist())
        else:
            raw = content.replace("\n", " ").replace("\r", " ")
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return
    st.caption(f"{len(ud.split_tokens(raw))} entries found in {up.name}.")
    if st.button("VALIDATE & IMPORT", key=f"wl_import_button_{key}", type="primary", use_container_width=True):
        with st.spinner("Validating imported symbols…"):
            valid, unresolved = resolve_tokens(raw)
        if valid:
            try:
                ud.add_symbols(supabase, valid, table)
                st.success(f"Imported {len(valid)} symbol(s).")
            except Exception as exc:
                st.error(f"Import could not be saved: {exc}")
        if unresolved:
            st.warning("Skipped (not found): " + ", ".join(unresolved))


# ═════════════════════════ data ═════════════════════════
def _make_rows(symbols, data):
    rows = []
    for pos, symbol in enumerate(symbols):
        sym = str(symbol).upper().strip()
        raw = data.get(sym) or {}
        cur, chg, pdh, pdl, rsi = (_num(raw.get(k)) for k in ("cur", "chg", "pdh", "pdl", "rsi"))
        spark = [p for p in (_num(v) for v in (raw.get("spark") or [])) if p is not None]
        r12 = (spark[-1] / spark[0] - 1) * 100 if len(spark) >= 2 and spark[0] else None
        vs_pdh = (cur / pdh - 1) * 100 if cur is not None and pdh else None
        vs_pdl = (cur / pdl - 1) * 100 if cur is not None and pdl else None
        rng = (cur - pdl) / (pdh - pdl) * 100 if cur is not None and pdh is not None and pdl is not None and pdh > pdl else None
        if cur is None:
            cond = "No data"
        elif pdh is not None and cur > pdh:
            cond = "Above PDH"
        elif pdl is not None and cur < pdl:
            cond = "Below PDL"
        else:
            cond = "Inside range"
        tags = []
        if cond == "Above PDH":
            tags.append(("PDH BREAK", GREEN))
        elif cond == "Below PDL":
            tags.append(("PDL BREAK", RED))
        elif vs_pdh is not None and -NEAR_PCT <= vs_pdh <= 0:
            tags.append(("NEAR PDH", CYAN))
        elif vs_pdl is not None and 0 <= vs_pdl <= NEAR_PCT:
            tags.append(("NEAR PDL", AMBER))
        if rsi is not None and rsi >= 70:
            tags.append(("RSI 70+", AMBER))
        elif rsi is not None and rsi < 30:
            tags.append(("RSI <30", PURPLE))
        if chg is not None and abs(chg) >= 4:
            tags.append(("BIG MOVE", YELLOW))
        rows.append({"pos": pos, "symbol": sym, "cur": cur, "chg": chg, "r12": r12, "pdh": pdh, "pdl": pdl, "rsi": rsi,
                     "vs_pdh": vs_pdh, "vs_pdl": vs_pdl, "range": rng, "spark": spark, "cond": cond, "tags": tags,
                     "has_data": cur is not None})
    # relative leaders over 12 sessions
    ranked = sorted([r for r in rows if r["r12"] is not None], key=lambda r: r["r12"], reverse=True)
    for r in ranked[:3] if len(ranked) >= 6 else []:
        r["tags"].append(("12D LEADER", GREEN))
    return rows


def _filter_sort(rows, query, flt, sort_by):
    q = (query or "").strip().upper()
    out = [r for r in rows if not q or q in r["symbol"]]
    tests = {
        "GAINERS": lambda r: r["chg"] is not None and r["chg"] > 0,
        "LOSERS": lambda r: r["chg"] is not None and r["chg"] < 0,
        "ABOVE PDH": lambda r: r["cond"] == "Above PDH",
        "BELOW PDL": lambda r: r["cond"] == "Below PDL",
        "NEAR PDH": lambda r: r["vs_pdh"] is not None and -NEAR_PCT <= r["vs_pdh"] <= 0,
        "RSI 70+": lambda r: r["rsi"] is not None and r["rsi"] >= 70,
        "RSI < 35": lambda r: r["rsi"] is not None and r["rsi"] < 35,
        "NO DATA": lambda r: not r["has_data"],
    }
    if flt in tests:
        out = [r for r in out if tests[flt](r)]
    lo = -math.inf
    keys = {
        "Change % ↓": (lambda r: r["chg"] if r["chg"] is not None else lo, True),
        "Change % ↑": (lambda r: r["chg"] if r["chg"] is not None else math.inf, False),
        "12-session % ↓": (lambda r: r["r12"] if r["r12"] is not None else lo, True),
        "RSI ↓": (lambda r: r["rsi"] if r["rsi"] is not None else lo, True),
        "RSI ↑": (lambda r: r["rsi"] if r["rsi"] is not None else math.inf, False),
        "Closest to PDH": (lambda r: abs(r["vs_pdh"]) if r["vs_pdh"] is not None else math.inf, False),
        "Symbol A–Z": (lambda r: r["symbol"], False),
    }
    if sort_by in keys:
        fn, rev = keys[sort_by]
        out = sorted(out, key=fn, reverse=rev)
    return out


# ═════════════════════════ views ═════════════════════════
def _kpis(rows):
    valid = [r for r in rows if r["has_data"]]
    adv = sum(1 for r in valid if (r["chg"] or 0) > 0)
    dec = sum(1 for r in valid if (r["chg"] or 0) < 0)
    flat = len(valid) - adv - dec
    tot = max(len(valid), 1)
    above = sum(1 for r in valid if r["cond"] == "Above PDH")
    below = sum(1 for r in valid if r["cond"] == "Below PDL")
    rsis = [r["rsi"] for r in valid if r["rsi"] is not None]
    avg_rsi = sum(rsis) / len(rsis) if rsis else None
    movers = sorted([r for r in valid if r["chg"] is not None], key=lambda r: r["chg"])
    best, worst = (movers[-1], movers[0]) if movers else (None, None)
    med = sorted(r["chg"] for r in movers)[len(movers) // 2] if movers else None
    k = lambda l, v, s, a, vc=None: (f'<div class="wl-k" style="--a:{a}"><div class="l">{l}</div>'
                                     f'<div class="v"{f" style=color:{vc}" if vc else ""}>{v}</div><div class="s">{s}</div></div>')
    breadth = (f'<div class="wl-breadth"><i style="width:{adv / tot * 100:.1f}%;background:{GREEN}"></i>'
               f'<i style="width:{flat / tot * 100:.1f}%;background:#333"></i><i style="width:{dec / tot * 100:.1f}%;background:{RED}"></i></div>')
    cards = [
        k("Stocks", len(rows), f"{len(rows) - len(valid)} without data" if len(rows) != len(valid) else "all priced", LINE2),
        f'<div class="wl-k" style="--a:{AMBER}"><div class="l">Breadth</div><div class="v"><span style="color:{GREEN}">▲ {adv}</span> '
        f'<span style="color:{TX3}">/</span> <span style="color:{RED}">▼ {dec}</span></div>'
        f'<div class="s">{"median move " + format(med, "+.2f") + "%" if med is not None else "advancing / declining"}</div>{breadth}</div>',
        k("Above PDH", above, "breaking yesterday's high", GREEN, GREEN if above else None),
        k("Below PDL", below, "breaking yesterday's low", RED, RED if below else None),
        k("Average RSI", f"{avg_rsi:.0f}" if avg_rsi is not None else "—", "momentum of the list", CYAN),
        k("Best today", _e(best["symbol"]) if best else "—", f'{best["chg"]:+.2f}%' if best else "", GREEN, GREEN if best else None),
        k("Worst today", _e(worst["symbol"]) if worst else "—", f'{worst["chg"]:+.2f}%' if worst else "", RED, RED if worst else None),
    ]
    st.markdown('<div class="wl-kpis">' + "".join(cards) + '</div>', unsafe_allow_html=True)


def _table(rows):
    body = []
    for i, r in enumerate(rows, 1):
        tags = "".join(f'<span class="wl-tag" style="color:{c}">{_e(t)}</span>' for t, c in r["tags"]) or '<span class="wl-na">—</span>'
        price = _price(r["cur"]) if r["has_data"] else '<span class="wl-na">no data</span>'
        body.append(
            f'<tr><td class="rk">{i}</td><td class="l wl-sym"><b>{_e(r["symbol"])}</b><span>{_e(r["cond"])}</span></td>'
            f'<td style="color:{TX}">{price}</td><td>{_pct_html(r["chg"])}</td><td>{_pct_html(r["r12"], 1, 10.0)}</td>'
            f'<td>{_rsi_html(r["rsi"])}</td>'
            f'<td>{_pct_html(r["vs_pdh"], 2, 2.0)}<br><span style="font-size:9px;color:{TX3}">PDH {_price(r["pdh"])}</span></td>'
            f'<td>{_pct_html(r["vs_pdl"], 2, 2.0)}<br><span style="font-size:9px;color:{TX3}">PDL {_price(r["pdl"])}</span></td>'
            f'<td>{_range_html(r["range"])}</td><td>{_spark(r["spark"])}</td><td class="l">{tags}</td></tr>')
    st.markdown(
        '<div class="wl-tw"><table class="wl-t"><thead><tr><th class="l">#</th><th class="l">Symbol</th><th>Last</th><th>Today</th>'
        '<th title="Change over the last 12 sessions">12 sessions</th><th>RSI 14</th>'
        '<th title="Distance from yesterday\'s high">vs PDH</th><th title="Distance from yesterday\'s low">vs PDL</th>'
        '<th title="Where price sits inside yesterday\'s high-low range">In yesterday\'s range</th><th>Trend</th><th class="l">Signals</th></tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)


def _heatmap(rows):
    tiles = []
    for r in sorted(rows, key=lambda x: x["chg"] if x["chg"] is not None else -999, reverse=True):
        v = r["chg"]
        if v is None:
            bg, c, txt = "#111", TX3, "no data"
        else:
            a = min(0.55, 0.08 + abs(v) / 4 * 0.47)
            bg = f"rgba({'48,209,88' if v >= 0 else '255,69,58'},{a:.3f})"
            c, txt = (GREEN if v >= 0 else RED), f'{"▲" if v >= 0 else "▼"} {abs(v):.2f}%'
        tiles.append(f'<div class="wl-tile" style="background:{bg}"><b>{_e(r["symbol"])}</b><div class="p">{_price(r["cur"])}</div>'
                     f'<div class="c" style="color:{"#fff" if v is not None and abs(v) > 2 else c}">{txt}</div></div>')
    st.markdown('<div class="wl-heat">' + "".join(tiles) + '</div>', unsafe_allow_html=True)


def _actions(rows, all_symbols, supabase, table, key, on_open, can_edit):
    with st.container(key=f"wl_actions_{key}"):
        c1, c2, c3, c4 = st.columns([2.6, 1, 1, 1], gap="small")
        pick = c1.selectbox("Selected stock", [r["symbol"] for r in rows] or all_symbols, key=f"wl_pick_{key}",
                            label_visibility="collapsed", placeholder="Choose a stock")
        if c2.button("OPEN ▸", key=f"wl_open_{key}", use_container_width=True, type="primary", disabled=not (on_open and pick)):
            on_open(pick)
        if c3.button("◆ AI ANALYSIS", key=f"wl_ai_{key}", use_container_width=True, disabled=not pick):
            if hasattr(ud, "go_analysis"):
                ud.go_analysis(pick)
            st.session_state["page"] = "analysis"
            st.session_state["m1_ticker"] = pick
            st.rerun()
        if c4.button("✕ REMOVE", key=f"wl_rm_{key}", use_container_width=True, disabled=not (can_edit and pick)):
            try:
                ud.remove_symbol(supabase, pick, table)
                _sync_session(table, [s for s in all_symbols if s != pick])
                st.toast(f"Removed {pick}")
                st.rerun(scope="fragment")
            except Exception as exc:
                st.error(f"Could not remove {pick}: {exc}")


def _manage(rows, all_symbols, supabase, table, key):
    with st.expander("MANAGE LIST · REMOVE SEVERAL · CLEAN UP"):
        dead = [r["symbol"] for r in rows if not r["has_data"]]
        sel = st.multiselect("Stocks to remove", all_symbols, key=f"wl_multi_{key}")
        a, b = st.columns(2)
        if a.button(f"REMOVE {len(sel)} SELECTED", key=f"wl_multi_btn_{key}", disabled=not sel, use_container_width=True):
            _bulk_remove(sel, all_symbols, supabase, table)
        if b.button(f"REMOVE {len(dead)} WITHOUT DATA", key=f"wl_dead_btn_{key}", disabled=not dead, use_container_width=True,
                    help="Symbols that returned no price — usually typos, delisted or renamed stocks."):
            _bulk_remove(dead, all_symbols, supabase, table)


def _bulk_remove(symbols, all_symbols, supabase, table):
    failed = []
    for s in symbols:
        try:
            ud.remove_symbol(supabase, s, table)
        except Exception:
            failed.append(s)
    _sync_session(table, [s for s in all_symbols if s not in set(symbols) - set(failed)])
    if failed:
        st.error("Could not remove: " + ", ".join(failed))
    else:
        st.toast(f"Removed {len(symbols)} stock(s)")
        st.rerun(scope="fragment")


@st.fragment
def _list_fragment(supabase, table, key, on_open, can_edit):
    try:
        symbols = ud.load_symbols(supabase, table) or []
    except Exception as exc:
        st.error(f"Could not load the list: {exc}")
        return
    symbols = list(dict.fromkeys(str(s).upper().strip() for s in symbols if str(s).strip()))
    _sync_session(table, symbols)
    if not symbols:
        msg = ("<b>Your watchlist is empty.</b><br>Add stocks above (tickers or company names, comma-separated) "
               "or import a TradingView / CSV file." if can_edit else "<b>The Arka desk list is empty.</b>")
        st.markdown(f'<div class="wl-empty">{msg}</div>', unsafe_allow_html=True)
        return

    with st.spinner(f"Loading {len(symbols)} stocks…"):
        try:
            data = scan_batch(tuple(symbols)) or {}
        except Exception:
            data = {}
    rows = _make_rows(symbols, data)
    _kpis(rows)

    with st.container(key=f"wl_filters_{key}"):
        f1, f2, f3, f4 = st.columns([1.5, 4.6, 1.3, 0.9], gap="small")
        query = f1.text_input("Search", placeholder="Search symbol…", key=f"wl_search_{key}", label_visibility="collapsed")
        flt = f2.radio("Filter", FILTERS, horizontal=True, key=f"wl_flt_{key}", label_visibility="collapsed")
        sort_by = f3.selectbox("Sort", SORTS, key=f"wl_sort_{key}", label_visibility="collapsed")
        view = f4.radio("View", ["TABLE", "HEATMAP"], key=f"wl_view_{key}", label_visibility="collapsed")

    shown = _filter_sort(rows, query, flt, sort_by)
    if not shown:
        st.markdown('<div class="wl-empty">No stocks match this filter.</div>', unsafe_allow_html=True)
    elif view == "HEATMAP":
        _heatmap(shown)
    else:
        _table(shown)

    _actions(shown, symbols, supabase, table, key, on_open, can_edit)
    c1, c2, c3 = st.columns([4, 1, 1], gap="small")
    c1.caption(f"Showing {len(shown)} of {len(symbols)} · PDH / PDL = previous session's high / low · prices from Yahoo Finance, delayed; "
               "refreshed every 30 s while NSE is open.")
    if c2.button("↻ REFRESH", key=f"wl_refresh_{key}", use_container_width=True):
        scan_batch.clear()
        st.rerun(scope="fragment")
    export = pd.DataFrame([{k: r[k] for k in ("symbol", "cur", "chg", "r12", "rsi", "pdh", "pdl", "vs_pdh", "vs_pdl", "cond")} for r in shown])
    c3.download_button("⤓ CSV", export.to_csv(index=False).encode(), f"arka_{key}_watchlist.csv", "text/csv",
                       key=f"wl_csv_{key}", use_container_width=True)
    if can_edit:
        _manage(rows, symbols, supabase, table, key)


# ═════════════════════════ entry point ═════════════════════════
def render_watchlist(supabase, on_open=None, is_admin=False):
    """Main Watchlist module. Signature preserved for app.py."""
    _inject_styles()
    if supabase is None:
        st.info("Cloud storage is not connected, so changes last only for this session. Add SUPABASE_URL and SUPABASE_KEY to Streamlit secrets.")

    lists = ["MY WATCHLIST", "ARKA DESK LIST"]
    which = st.radio("List", lists, horizontal=True, key="wl_which", label_visibility="collapsed")   # only the open list is built
    if which == "MY WATCHLIST":
        table, key, can_edit = ud.W_TABLE, "personal", True
        sub = "YOUR STOCKS · SYNCED TO THE CLOUD"
    else:
        table, key, can_edit = ud.A_TABLE, "admin", bool(is_admin)
        sub = "CURATED BY THE ARKA DESK" + (" · YOU CAN EDIT" if is_admin else " · READ-ONLY")
    st.markdown(f'<div class="wl-bar"><b>{_e(which)}</b><span>{sub}</span></div>', unsafe_allow_html=True)
    if can_edit:
        _add_box(supabase, table, key)
    _list_fragment(supabase, table, key, on_open, can_edit)
