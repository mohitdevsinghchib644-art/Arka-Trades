"""
Arka Trades — Watchlist Terminal
Compact market-monitor layout using the existing user_data and data_engine APIs.
"""

from __future__ import annotations

import html
import io
import math

import pandas as pd
import streamlit as st

import user_data as ud
from data_engine import get_quotes, scan_batch

try:
    from screener_scraper import resolve_symbol
except Exception:
    resolve_symbol = None


# ── Palette ──────────────────────────────────────────────────────

BG = "#0B0E11"
PANEL = "#10151B"
BORDER = "#252D36"
TEXT = "#E5EAF0"
MUTED = "#84909D"
AMBER = "#E8B04B"
GREEN = "#36C98F"
RED = "#F06C75"
CYAN = "#66BDE8"


# ── Formatting and safety ────────────────────────────────────────

def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fmt_price(value):
    value = _number(value)
    return f"₹{value:,.2f}" if value is not None else "—"


def _fmt_pct(value):
    value = _number(value)
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _fmt_rsi(value):
    value = _number(value)
    return f"{value:.0f}" if value is not None else "—"


def _spark(values, width=104, height=28):
    """Small SVG sparkline; malformed values are safely ignored."""
    if not isinstance(values, (list, tuple)):
        return ""

    points = [_number(v) for v in values]
    points = [v for v in points if v is not None]

    if len(points) < 2:
        return ""

    lo, hi = min(points), max(points)
    span = hi - lo or 1.0

    coords = []
    for i, value in enumerate(points):
        x = i / (len(points) - 1) * width
        y = height - 3 - ((value - lo) / span) * (height - 7)
        coords.append(f"{x:.1f},{y:.1f}")

    color = GREEN if points[-1] >= points[0] else RED
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'xmlns="http://www.w3.org/2000/svg" style="display:block;">'
        f'<polyline points="{" ".join(coords)}" fill="none" '
        f'stroke="{color}" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )


def _inject_styles():
    st.markdown(
        f"""
        <style>
        .wl-head {{
            display:flex; align-items:center; justify-content:space-between;
            gap:12px; padding:4px 0 16px; border-bottom:1px solid {BORDER};
            margin-bottom:16px;
        }}
        .wl-title {{
            font-size:23px; font-weight:750; color:{TEXT};
            letter-spacing:-.5px;
        }}
        .wl-sub {{
            font-size:11px; color:{MUTED}; margin-top:4px;
            letter-spacing:.6px;
        }}
        .wl-status {{
            color:{AMBER}; font-size:10px; font-weight:700;
            border:1px solid #66502D; padding:5px 8px;
            letter-spacing:.8px; white-space:nowrap;
        }}
        .wl-metric {{
            background:{PANEL}; border:1px solid {BORDER};
            padding:11px 13px; min-height:70px;
        }}
        .wl-metric-label {{
            color:{MUTED}; font-size:10px; letter-spacing:.8px;
            font-weight:650; text-transform:uppercase;
        }}
        .wl-metric-value {{
            color:{TEXT}; font-size:21px; font-weight:750;
            margin-top:5px; line-height:1.2;
        }}
        .wl-table-head {{
            display:grid;
            grid-template-columns:minmax(125px,1.35fr) minmax(95px,.9fr)
              minmax(85px,.8fr) minmax(85px,.8fr) minmax(85px,.8fr)
              minmax(95px,.9fr) minmax(105px,1fr);
            gap:10px; align-items:center; padding:9px 12px;
            background:#141A21; border:1px solid {BORDER};
            color:{MUTED}; font-size:10px; font-weight:700;
            letter-spacing:.65px; text-transform:uppercase;
        }}
        .wl-row {{
            display:grid;
            grid-template-columns:minmax(125px,1.35fr) minmax(95px,.9fr)
              minmax(85px,.8fr) minmax(85px,.8fr) minmax(85px,.8fr)
              minmax(95px,.9fr) minmax(105px,1fr);
            gap:10px; align-items:center; padding:12px;
            background:{BG}; border:1px solid {BORDER};
            border-top:0; color:{TEXT}; font-size:12px;
        }}
        .wl-symbol {{
            color:{TEXT}; font-weight:750; font-size:13px;
            letter-spacing:.2px;
        }}
        .wl-muted {{ color:{MUTED}; font-size:11px; }}
        .wl-green {{ color:{GREEN}; font-weight:700; }}
        .wl-red {{ color:{RED}; font-weight:700; }}
        .wl-neutral {{ color:{TEXT}; font-weight:650; }}
        .wl-empty {{
            padding:28px 16px; text-align:center; color:{MUTED};
            border:1px dashed {BORDER}; background:{PANEL};
            font-size:12px;
        }}
        div[data-testid="stMetric"] {{
            background:{PANEL}; border:1px solid {BORDER};
            padding:10px 12px;
        }}
        div[data-testid="stMetricLabel"] {{
            font-size:10px; letter-spacing:.5px;
        }}
        div[data-testid="stMetricValue"] {{
            font-size:21px;
        }}
        div[data-testid="stExpander"] {{
            border-color:{BORDER};
        }}
        div[data-testid="stButton"] button {{
            border-radius:3px; font-size:11px; font-weight:650;
        }}
        div[data-testid="stTextInput"] input,
        div[data-testid="stSelectbox"] > div > div,
        div[data-testid="stTextArea"] textarea {{
            border-radius:3px;
        }}
        @media(max-width:850px) {{
            .wl-table-head, .wl-row {{
                grid-template-columns:1.2fr 1fr .9fr .9fr;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric(label, value, color=TEXT):
    st.markdown(
        f"""
        <div class="wl-metric">
            <div class="wl-metric-label">{html.escape(str(label))}</div>
            <div class="wl-metric-value" style="color:{color}">
                {html.escape(str(value))}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Symbol parsing and validation ────────────────────────────────

def _normalise_symbol(raw):
    try:
        symbol = ud.clean_symbol(str(raw).strip())
        return str(symbol).upper().strip() if symbol else ""
    except Exception:
        return ""


def resolve_tokens(text):
    """Resolve and validate symbols in one batched quote request."""
    tokens = ud.split_tokens(text)
    symbols = list(dict.fromkeys(
        symbol for symbol in (_normalise_symbol(x) for x in tokens)
        if symbol
    ))

    if not symbols:
        return [], []

    try:
        quotes = get_quotes(tuple(symbols)) or {}
    except Exception:
        quotes = {}

    valid = []
    unresolved = []

    for symbol in symbols:
        quote = quotes.get(symbol)
        if isinstance(quote, dict) and _number(quote.get("price")) is not None:
            valid.append(symbol)
            continue

        # Company-name resolution is optional. Never block the whole import
        # if Screener is unavailable.
        resolved = None
        if resolve_symbol is not None:
            try:
                result = resolve_symbol(symbol)
                if isinstance(result, dict):
                    resolved = _normalise_symbol(
                        result.get("symbol") or result.get("name") or ""
                    )
            except Exception:
                resolved = None

        if resolved and resolved not in valid:
            valid.append(resolved)
        else:
            unresolved.append(symbol)

    return list(dict.fromkeys(valid)), unresolved


# ── Add and bulk import ──────────────────────────────────────────

def _add_box(supabase, table, key):
    with st.form(f"wl_add_form_{key}", clear_on_submit=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            raw = st.text_input(
                "ADD SYMBOLS",
                placeholder="RELIANCE, HDFCBANK, TCS …",
                label_visibility="collapsed",
                key=f"wl_add_input_{key}",
            )
        with c2:
            submitted = st.form_submit_button(
                "＋ ADD", use_container_width=True
            )

    if not submitted:
        return

    if not raw.strip():
        st.warning("Enter at least one stock symbol.")
        return

    with st.spinner("Validating symbols…"):
        valid, unresolved = resolve_tokens(raw)

    if valid:
        try:
            ud.add_symbols(supabase, valid, table)
            st.success(f"Added {len(valid)} symbol(s).")
        except Exception as exc:
            st.error(f"Could not save symbols: {exc}")

    if unresolved:
        st.caption("Not resolved: " + ", ".join(unresolved))

    if valid:
        st.rerun()


def _bulk_import(supabase, table, key):
    with st.expander("IMPORT WATCHLIST · CSV / TXT", expanded=False):
        uploaded = st.file_uploader(
            "Choose a file",
            type=["csv", "txt"],
            key=f"wl_upload_{key}",
            help="CSV or TXT with stock symbols. The first column is used.",
        )

        if uploaded is None:
            return

        try:
            content = uploaded.getvalue().decode("utf-8-sig", errors="replace")

            if uploaded.name.lower().endswith(".csv"):
                frame = pd.read_csv(io.StringIO(content))
                if frame.empty:
                    st.warning("The file contains no symbols.")
                    return

                preferred = [
                    c for c in frame.columns
                    if str(c).strip().lower() in {
                        "symbol", "symbols", "ticker", "stock", "tradingsymbol"
                    }
                ]
                column = preferred[0] if preferred else frame.columns[0]
                raw = " ".join(frame[column].dropna().astype(str).tolist())
            else:
                raw = content.replace("\n", " ").replace("\r", " ")

        except Exception as exc:
            st.error(f"Could not read file: {exc}")
            return

        if st.button(
            "VALIDATE & IMPORT",
            key=f"wl_import_button_{key}",
            type="primary",
        ):
            with st.spinner("Validating imported symbols…"):
                valid, unresolved = resolve_tokens(raw)

            if valid:
                try:
                    ud.add_symbols(supabase, valid, table)
                    st.success(f"Imported {len(valid)} symbol(s).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Import could not be saved: {exc}")

            if unresolved:
                st.warning("Skipped unresolved symbols: " + ", ".join(unresolved))


# ── Watchlist data ───────────────────────────────────────────────

def _make_rows(symbols, data):
    rows = []

    for position, symbol in enumerate(symbols):
        sym = str(symbol).upper().strip()
        raw = data.get(sym) or {}

        cur = _number(raw.get("cur"))
        chg = _number(raw.get("chg"))
        pdh = _number(raw.get("pdh"))
        pdl = _number(raw.get("pdl"))
        rsi = _number(raw.get("rsi"))
        prev_close = _number(raw.get("prev_close"))

        if cur is None:
            condition = "No data"
        elif pdh is not None and cur > pdh:
            condition = "Above PDH"
        elif pdl is not None and cur < pdl:
            condition = "Below PDL"
        else:
            condition = "In range"

        rows.append({
            "position": position,
            "symbol": sym,
            "cur": cur,
            "chg": chg,
            "pdh": pdh,
            "pdl": pdl,
            "rsi": rsi,
            "prev_close": prev_close,
            "spark": raw.get("spark") or [],
            "condition": condition,
            "has_data": cur is not None,
        })

    return rows


def _filter_rows(rows, query, condition, sort_by):
    q = (query or "").strip().upper()

    filtered = [
        row for row in rows
        if not q or q in row["symbol"]
    ]

    if condition == "Positive":
        filtered = [
            r for r in filtered
            if r["chg"] is not None and r["chg"] > 0
        ]
    elif condition == "Negative":
        filtered = [
            r for r in filtered
            if r["chg"] is not None and r["chg"] < 0
        ]
    elif condition == "Above PDH":
        filtered = [r for r in filtered if r["condition"] == "Above PDH"]
    elif condition == "Below PDL":
        filtered = [r for r in filtered if r["condition"] == "Below PDL"]
    elif condition == "RSI > 70":
        filtered = [r for r in filtered if r["rsi"] is not None and r["rsi"] > 70]
    elif condition == "RSI < 35":
        filtered = [r for r in filtered if r["rsi"] is not None and r["rsi"] < 35]
    elif condition == "No data":
        filtered = [r for r in filtered if not r["has_data"]]

    if sort_by == "Symbol A–Z":
        filtered.sort(key=lambda r: r["symbol"])
    elif sort_by == "Change %":
        filtered.sort(
            key=lambda r: r["chg"] if r["chg"] is not None else -math.inf,
            reverse=True,
        )
    elif sort_by == "RSI":
        filtered.sort(
            key=lambda r: r["rsi"] if r["rsi"] is not None else -math.inf,
            reverse=True,
        )
    # "Watchlist order" preserves the saved order.

    return filtered


# ── Rows and actions ─────────────────────────────────────────────

def _render_rows(rows, supabase, table, key, on_open):
    if not rows:
        st.markdown(
            '<div class="wl-empty">No stocks match these filters.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        """
        <div class="wl-table-head">
            <div>SYMBOL</div>
            <div>LAST PRICE</div>
            <div>CHANGE</div>
            <div>RSI</div>
            <div>PDH / PDL</div>
            <div>12D TREND</div>
            <div>ACTIONS</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for row in rows:
        sym = row["symbol"]
        safe_sym = html.escape(sym)
        cur = row["cur"]
        chg = row["chg"]
        rsi = row["rsi"]

        chg_class = (
            "wl-green" if chg is not None and chg > 0
            else "wl-red" if chg is not None and chg < 0
            else "wl-neutral"
        )

        rsi_class = (
            "wl-red" if rsi is not None and rsi >= 70
            else "wl-green" if rsi is not None and rsi <= 35
            else "wl-neutral"
        )

        trend = _spark(row["spark"])

        if not row["has_data"]:
            price_html = '<span class="wl-muted">Unavailable</span>'
            change_html = '<span class="wl-muted">—</span>'
        else:
            price_html = (
                f'<span class="wl-neutral">{_fmt_price(cur)}</span>'
            )
            change_html = (
                f'<span class="{chg_class}">{_fmt_pct(chg)}</span>'
            )

        pdh_pdl_html = (
            f'<div class="wl-neutral">{_fmt_price(row["pdh"])}</div>'
            f'<div class="wl-muted">{_fmt_price(row["pdl"])}</div>'
        )

        st.markdown(
            f"""
            <div class="wl-row">
                <div>
                    <div class="wl-symbol">{safe_sym}</div>
                    <div class="wl-muted">{html.escape(row["condition"])}</div>
                </div>
                <div>{price_html}</div>
                <div>{change_html}</div>
                <div class="{rsi_class}">{_fmt_rsi(rsi)}</div>
                <div>{pdh_pdl_html}</div>
                <div>{trend or '<span class="wl-muted">—</span>'}</div>
                <div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        a, b, c = st.columns([1, 1, 1.15], gap="small")

        with a:
            if st.button(
                "OPEN",
                key=f"wl_open_{key}_{sym}",
                use_container_width=True,
                disabled=not bool(on_open),
            ):
                on_open(sym)

        with b:
            if st.button(
                "ANALYSIS",
                key=f"wl_analysis_{key}_{sym}",
                use_container_width=True,
            ):
                ud.go_analysis(sym)

        with c:
            if st.button(
                "REMOVE",
                key=f"wl_remove_{key}_{sym}",
                use_container_width=True,
            ):
                try:
                    ud.remove_symbol(supabase, sym, table)
                    st.rerun(scope="fragment")
                except Exception as exc:
                    st.error(f"Could not remove {sym}: {exc}")


@st.fragment
def _list_fragment(supabase, table, key, on_open):
    try:
        symbols = ud.load_symbols(supabase, table) or []
    except Exception as exc:
        st.error(f"Could not load watchlist: {exc}")
        return

    symbols = list(dict.fromkeys(
        str(s).upper().strip() for s in symbols if str(s).strip()
    ))

    if not symbols:
        st.markdown(
            '<div class="wl-empty">Your watchlist is empty. Add stocks above '
            'or import a CSV/TXT file.</div>',
            unsafe_allow_html=True,
        )
        return

    # scan_batch has its own 30-second cache. Do not add another cache here.
    with st.spinner("Loading watchlist market data…"):
        try:
            data = scan_batch(tuple(symbols)) or {}
        except Exception:
            data = {}

    rows = _make_rows(symbols, data)

    total = len(rows)
    valid = [r for r in rows if r["has_data"]]
    advancing = sum(1 for r in valid if r["chg"] is not None and r["chg"] > 0)
    declining = sum(1 for r in valid if r["chg"] is not None and r["chg"] < 0)
    above_pdh = sum(1 for r in valid if r["condition"] == "Above PDH")
    no_data = total - len(valid)

    m1, m2, m3, m4, m5 = st.columns(5, gap="small")
    with m1:
        _metric("WATCHLIST", total)
    with m2:
        _metric("ADVANCING", advancing, GREEN)
    with m3:
        _metric("DECLINING", declining, RED)
    with m4:
        _metric("ABOVE PDH", above_pdh, CYAN)
    with m5:
        _metric("NO DATA", no_data, MUTED if no_data == 0 else AMBER)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    f1, f2, f3 = st.columns([2.1, 1.3, 1.3], gap="small")

    with f1:
        query = st.text_input(
            "Search",
            placeholder="Search symbol…",
            key=f"wl_search_{key}",
            label_visibility="collapsed",
        )

    with f2:
        condition = st.selectbox(
            "Condition",
            [
                "All stocks", "Above PDH", "Below PDL",
                "Positive", "Negative", "RSI > 70", "RSI < 35", "No data",
            ],
            key=f"wl_condition_{key}",
            label_visibility="collapsed",
        )

    with f3:
        sort_by = st.selectbox(
            "Sort",
            ["Watchlist order", "Symbol A–Z", "Change %", "RSI"],
            key=f"wl_sort_{key}",
            label_visibility="collapsed",
        )

    filtered = _filter_rows(
        rows,
        query,
        "All stocks" if condition == "All stocks" else condition,
        sort_by,
    )

    st.caption(
        f"Showing {len(filtered)} of {total} stocks · "
        "Market data is provider-dependent; scan cache refreshes every 30 seconds."
    )

    _render_rows(filtered, supabase, table, key, on_open)


# ── Public entry point ───────────────────────────────────────────

def _panel(supabase, on_open, table, label, key):
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;justify-content:space-between;
                    gap:12px;margin:4px 0 12px;">
            <div style="font-size:14px;font-weight:750;color:{TEXT};">
                {html.escape(label)}
            </div>
            <div style="font-size:10px;color:{MUTED};">
                BATCHED SCAN · 30S CACHE
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _add_box(supabase, table, key)
    _bulk_import(supabase, table, key)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    _list_fragment(supabase, table, key, on_open)


def render_watchlist(supabase, on_open=None, is_admin=False):
    """Main Watchlist module. Signature preserved for app.py."""
    _inject_styles()

    st.markdown(
        f"""
        <div class="wl-head">
            <div>
                <div class="wl-title">Watchlist</div>
                <div class="wl-sub">
                    PERSONAL MARKET MONITOR · PRICE ACTION · MOMENTUM
                </div>
            </div>
            <div class="wl-status">ARKA / MONITOR</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if supabase is None:
        st.info(
            "Cloud storage is not connected. Your watchlist may not load or "
            "save until the Supabase connection is configured."
        )

    if is_admin:
        tabs = st.tabs(["MY WATCHLIST", "ARKA DESK LIST"])
        with tabs[0]:
            _panel(supabase, on_open, ud.W_TABLE, "MY WATCHLIST", "personal")
        with tabs[1]:
            _panel(supabase, on_open, ud.A_TABLE, "ARKA DESK LIST", "admin")
    else:
        _panel(supabase, on_open, ud.W_TABLE, "MY WATCHLIST", "personal")
