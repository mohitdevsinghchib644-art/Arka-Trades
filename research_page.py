"""
research_page.py — Arka Trades Research Terminal (v7)

REQUIRES: streamlit>=1.37 (for st.fragment) and the `streamlit-autorefresh`
package in requirements.txt. If either is missing, the blotter degrades
to a static (non-ticking) row automatically — see _HAS_FRAGMENT /
_HAS_AUTOREFRESH below — so this file won't crash an older deploy, it'll
just silently lose the live-tick feature until you bump the version.

CHANGES FROM v6 — visual rebuild + live blotter:
  - REDESIGNED: full Bloomberg-terminal visual pass. Monospace-first
    typography, a true dark-panel grid with hairline borders, a top
    status strip (clock + market session state), amber-on-black key
    stats, and a two-column dense layout instead of the previous
    wide-margin card style. This is a visual rebuild only — every data
    source, section, and status-tag semantic (live/stale/partial/N/A)
    from v6 is unchanged underneath it.
  - NEW: hot-ticker strip is now a real live blotter. It re-renders on
    its own timer via st.fragment(run_every=...) — NOT the old
    session_state 5-minute TTL cache from v6. Only the blotter
    fragment reruns on the timer; the rest of the page (search input,
    active research view, scroll position) is untouched, because
    fragments in Streamlit only re-execute their own function, not the
    full script. This replaces v6's "cache so reruns don't re-fetch"
    approach with "only the blotter reruns at all."
  - NEW: blotter rows now show live % change (green/red) alongside
    price, sourced from screener_scraper.get_hot_ticker_quotes().
  - REMOVED: the debug query-param JSON dump block from v6 main render
    (st.experimental_get_query_params is deprecated upstream in modern
    Streamlit anyway; if you need the raw payload for debugging, read
    st.session_state["research_data"] directly in a dev shell instead).
  - UNCHANGED: get_full_research() fallback-to-summary error handling,
    all four data tabs, factors panel, peer comparison, leverage
    ratios, news feed wiring — logic is identical to v6, restyled only.
"""

import re
import time
import streamlit as st
from datetime import datetime, timezone, timedelta

try:
    from streamlit_option_menu import option_menu
except Exception:
    def option_menu(menu_title=None, options=None, icons=None, default_index=0,
                    orientation="horizontal", styles=None, **kwargs):
        key = "option_menu_fallback_" + (menu_title or "_")
        if not options:
            return None
        idx = default_index if 0 <= default_index < len(options) else 0
        return st.radio(menu_title if menu_title else "", options, index=idx, key=key)

_HAS_FRAGMENT = hasattr(st, "fragment")

try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except Exception:
    _HAS_AUTOREFRESH = False

from screener_scraper import (
    get_full_research, get_factors, get_earnings_date,
    get_balance_sheet, get_leverage_ratios, get_peer_comparison,
    get_summary, resolve_symbol, get_hot_ticker_quotes,
)

IST = timezone(timedelta(hours=5, minutes=30))

_HOT_TICKERS = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "TATAMOTORS", "SBIN"]
_BLOTTER_REFRESH_SECONDS = 20  # auto-refresh cadence for the live blotter fragment


# ── Terminal theme (Bloomberg-style palette) ──────────────────────
# This is what "Bloomberg terminal" actually means visually: near-black
# background, amber for headline data, green/red strictly for
# price direction (never decorative), thin 1px hairline borders instead
# of card shadows/radius, and monospace for every number so columns
# align. If T is passed in from app.py, this local default is only a
# fallback so this file still renders standalone.
_DEFAULT_T = {
    "bg": "#000000",
    "panel": "#0A0A0A",
    "panel2": "#111111",
    "row_alt": "#0D0D0D",
    "border": "#262626",
    "amber": "#FFB000",
    "ivory": "#E8E6E0",
    "t2": "#A8A8A0",
    "t3": "#6B6B65",
    "green": "#00C853",
    "red": "#FF3B30",
    "mono": "'IBM Plex Mono', 'Consolas', monospace",
}


def _get_theme(T: dict | None) -> dict:
    merged = dict(_DEFAULT_T)
    if T:
        merged.update(T)
    return merged


# ── Helper functions ──────────────────────────────────────────

def _age_label(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    hrs = seconds / 3600
    if hrs < 48:
        return f"{int(hrs)}h ago"
    return f"{int(hrs // 24)}d ago"


def _status_tag(status: str, age_seconds: float = None, T=None) -> str:
    if status == "live":
        return f'<span style="color:{T["green"]};font-size:10px;font-weight:700;letter-spacing:1px;font-family:{T["mono"]};">● LIVE</span>'
    elif status == "stale":
        age = _age_label(age_seconds) if age_seconds is not None else "?"
        return f'<span style="color:{T["amber"]};font-size:10px;font-weight:700;letter-spacing:1px;font-family:{T["mono"]};">◐ CACHED · {age}</span>'
    elif status == "partial":
        return f'<span style="color:{T["amber"]};font-size:10px;font-weight:700;letter-spacing:1px;font-family:{T["mono"]};">◐ PARTIAL</span>'
    else:
        return f'<span style="color:{T["t3"]};font-size:10px;font-weight:700;letter-spacing:1px;font-family:{T["mono"]};">✕ N/A</span>'


def _render_data_table(periods, rows, T, highlight_labels=None):
    if not rows:
        st.markdown(f'<div style="padding:16px;color:{T["t3"]};font-size:12px;font-family:{T["mono"]};">NO DATA ROWS PARSED.</div>',
                    unsafe_allow_html=True)
        return

    highlight_labels = highlight_labels or []
    col_w = max(60, int(560 / max(len(periods), 1)))

    header_cells = "".join(
        f'<th style="text-align:right;padding:6px 10px;font-size:10px;color:{T["t3"]};'
        f'font-weight:700;letter-spacing:0.5px;white-space:nowrap;min-width:{col_w}px;font-family:{T["mono"]};">{p}</th>'
        for p in periods
    )
    body_rows = ""
    for i, row in enumerate(rows):
        bg = T["row_alt"] if i % 2 == 1 else "transparent"
        is_hl = any(h.lower() in row["label"].lower() for h in highlight_labels)
        label_color = T["amber"] if is_hl else T["t2"]
        label_weight = "700" if is_hl else "500"
        cells = "".join(
            f'<td style="text-align:right;padding:6px 10px;font-family:{T["mono"]};'
            f'font-size:11.5px;color:{T["ivory"] if is_hl else T["t2"]};white-space:nowrap;">{v}</td>'
            for v in row["values"]
        )
        body_rows += (
            f'<tr style="background:{bg};border-bottom:1px solid {T["border"]};">'
            f'<td style="padding:6px 10px;font-size:11.5px;color:{label_color};'
            f'font-weight:{label_weight};white-space:nowrap;font-family:{T["mono"]};">{row["label"]}</td>' + cells + '</tr>'
        )

    st.markdown(f"""
    <div style="overflow-x:auto;border:1px solid {T['border']};background:{T['panel']};">
        <table style="border-collapse:collapse;width:100%;">
            <thead><tr style="border-bottom:1px solid {T['border']};background:{T['panel2']};">
                <th style="text-align:left;padding:6px 10px;font-size:10px;color:{T['t3']};font-weight:700;letter-spacing:0.5px;font-family:{T['mono']};">METRIC</th>
                {header_cells}
            </tr></thead>
            <tbody>{body_rows}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


# ── Live status strip (clock + session state) ─────────────────────

def _render_status_strip(T: dict):
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%H:%M:%S")
    date_str = now_ist.strftime("%d %b %Y")
    weekday = now_ist.weekday()  # 0=Mon .. 6=Sun
    market_open_t = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close_t = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    is_session = weekday < 5 and market_open_t <= now_ist <= market_close_t
    session_label = "MARKET OPEN" if is_session else "MARKET CLOSED"
    session_color = T["green"] if is_session else T["red"]

    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:center;
                background:{T['panel']};border:1px solid {T['border']};border-bottom:2px solid {T['amber']};
                padding:8px 16px;margin-bottom:14px;font-family:{T['mono']};">
        <div style="display:flex;align-items:center;gap:14px;">
            <span style="color:{T['amber']};font-weight:800;font-size:13px;letter-spacing:1px;">ARKA TERMINAL</span>
            <span style="color:{T['t3']};font-size:10px;">RESEARCH</span>
        </div>
        <div style="display:flex;align-items:center;gap:16px;">
            <span style="color:{session_color};font-size:10px;font-weight:700;letter-spacing:1px;">● {session_label}</span>
            <span style="color:{T['t2']};font-size:11px;">{date_str}</span>
            <span style="color:{T['ivory']};font-size:12px;font-weight:700;">{time_str} IST</span>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Hot Tickers blotter (live, auto-refreshing) ────────────────────

def _render_blotter_row(T: dict):
    quotes = get_hot_ticker_quotes(_HOT_TICKERS)
    cols = st.columns(len(_HOT_TICKERS))
    for i, sym in enumerate(_HOT_TICKERS):
        q = quotes.get(sym, {})
        price = q.get("price", "···")
        pct = q.get("pct_change")
        is_active = st.session_state.get("research_last_query", "").upper() == sym

        if pct is None:
            pct_str, pct_color = "—", T["t3"]
        else:
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
            pct_color = T["green"] if pct > 0 else (T["red"] if pct < 0 else T["t3"])
            pct_str = f"{arrow} {abs(pct):.2f}%"

        border_color = T["amber"] if is_active else T["border"]
        with cols[i]:
            st.markdown(f"""
            <div style="border:1px solid {border_color};background:{T['panel']};padding:6px 8px;margin-bottom:4px;font-family:{T['mono']};">
                <div style="font-size:10px;color:{T['t2']};font-weight:700;letter-spacing:0.5px;">{sym}</div>
                <div style="font-size:13px;color:{T['ivory']};font-weight:700;">₹{price}</div>
                <div style="font-size:10px;color:{pct_color};font-weight:700;">{pct_str}</div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("SELECT", key=f"hot_{sym}", use_container_width=True,
                         type=("primary" if is_active else "secondary")):
                st.session_state["research_last_query"] = sym
                st.session_state.pop("research_data", None)
                st.rerun()


def _render_hot_tickers_blotter(T: dict):
    """
    Wraps the blotter row in an st.fragment so it re-renders on its own
    timer without rerunning the whole page (search box text, scroll
    position, and the active research_data below are untouched by this
    fragment's reruns). Falls back to a static single render if either
    st.fragment or streamlit-autorefresh isn't available, so this file
    still works on an older Streamlit pin — it just won't tick live.
    """
    label = "HOT TICKERS · LIVE" if (_HAS_FRAGMENT and _HAS_AUTOREFRESH) else "HOT TICKERS"
    st.markdown(f"<div style='font-size:10px;color:{T['t3']};letter-spacing:1px;margin:10px 0 6px;font-family:{T['mono']};'>{label}</div>",
                unsafe_allow_html=True)

    if not (_HAS_FRAGMENT and _HAS_AUTOREFRESH):
        _render_blotter_row(T)
        st.caption("Live auto-refresh needs `streamlit>=1.37` and `streamlit-autorefresh` in requirements.txt — showing a static snapshot for now.")
        return

    @st.fragment(run_every=_BLOTTER_REFRESH_SECONDS)
    def _live_blotter():
        st_autorefresh(interval=_BLOTTER_REFRESH_SECONDS * 1000, key="blotter_autorefresh")
        _render_blotter_row(T)
        st.markdown(f"<div style='font-size:9px;color:{T['t3']};text-align:right;font-family:{T['mono']};'>refreshes every {_BLOTTER_REFRESH_SECONDS}s</div>",
                    unsafe_allow_html=True)

    _live_blotter()


# ── Chart panel ────────────────────────────────────────────────

def _fetch_chart_data(symbol: str, period: str = "6mo"):
    try:
        import yfinance as yf
        clean_symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper().strip())
        h = yf.Ticker(clean_symbol + ".NS").history(period=period, interval="1d")
        if h is None or h.empty or len(h) < 2:
            return None
        return h
    except Exception:
        return None


def _render_tv_chart(symbol: str):
    period_key = f"research_chart_period_{symbol}"
    period_choice = st.radio(
        "Range", ["1mo", "3mo", "6mo", "1y", "2y"], index=2,
        key=period_key, label_visibility="collapsed", horizontal=True
    )

    hist = _fetch_chart_data(symbol, period=period_choice)
    if hist is None:
        st.error(f"Could not fetch data for {symbol}")
        return

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        up_color = '#00C853'
        down_color = '#FF3B30'
        bg_color = '#000000'
        grid_color = '#1A1A1A'
        text_color = '#A8A8A0'

        colors = [up_color if row['Close'] >= row['Open'] else down_color for _, row in hist.iterrows()]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0, row_heights=[0.8, 0.2])

        fig.add_trace(go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"],
            low=hist["Low"], close=hist["Close"],
            increasing_line_color=up_color, decreasing_line_color=down_color,
            increasing_fillcolor=up_color, decreasing_fillcolor=down_color,
            name="Price"
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=hist.index, y=hist['Volume'], marker_color=colors, name="Volume"
        ), row=2, col=1)

        fig.update_layout(
            height=460, margin=dict(l=0, r=50, t=10, b=0),
            paper_bgcolor=bg_color, plot_bgcolor=bg_color,
            font=dict(color=text_color, size=11, family="IBM Plex Mono, Consolas, monospace"),
            showlegend=False,
            xaxis=dict(rangeslider_visible=False, showgrid=True, gridcolor=grid_color, type='category'),
            xaxis2=dict(rangeslider_visible=False, showgrid=True, gridcolor=grid_color, type='category'),
            yaxis=dict(showgrid=True, gridcolor=grid_color, side="right", tickformat=".2f"),
            yaxis2=dict(showgrid=False, side="right", showticklabels=False),
            hovermode="x unified"
        )
        fig.update_xaxes(nticks=10, row=2, col=1)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    except ImportError:
        st.line_chart(hist["Close"], height=400)


# ── Factors panel ──────────────────────────────────────────────

def _render_factors_panel(symbol: str, full_research: dict, T: dict):
    factors = get_factors(symbol, full_research=full_research)

    if factors["status"] == "unavailable" or not factors["items"]:
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:20px 16px;text-align:center;background:{T['panel']};">
            <div style="font-size:11px;color:{T['t3']};font-family:{T['mono']};">NOT ENOUGH DATA TO COMPUTE FACTOR DELTAS.</div>
        </div>""", unsafe_allow_html=True)
        return

    rows_html = []
    for item in factors["items"]:
        latest_str = f'{item["latest"]:,.2f}{item["unit"]}'
        if item["previous"] is not None:
            delta = item["latest"] - item["previous"]
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
            dc = T["green"] if delta > 0 else (T["red"] if delta < 0 else T["t3"])
            delta_str = f'<span style="color:{dc};font-family:{T["mono"]};font-size:10px;">{arrow} {abs(delta):,.2f}{item["unit"]}</span>'
        else:
            delta_str = f'<span style="color:{T["t3"]};font-size:10px;">—</span>'
        rows_html.append(f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid {T['border']};">
            <span style="font-size:11px;color:{T['t2']};font-family:{T['mono']};">{item['label']}</span>
            <div style="text-align:right;">
                <span style="font-family:{T['mono']};font-size:12px;color:{T['ivory']};font-weight:700;">{latest_str}</span>
                <span style="margin-left:8px;">{delta_str}</span>
            </div>
        </div>""")
    st.markdown(f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:4px 12px;margin-bottom:16px;">{"".join(rows_html)}</div>',
                 unsafe_allow_html=True)


# ── Peer Comparison table ────────────────────────────────

def _render_peer_comparison(symbol: str, T: dict):
    result = get_peer_comparison(symbol)

    if result["status"] != "live" or not result["rows"]:
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:16px;text-align:center;background:{T['panel']};">
            <div style="font-size:11px;color:{T['t3']};line-height:1.6;font-family:{T['mono']};">{result.get('reason', 'Peer comparison unavailable.')}</div>
        </div>""", unsafe_allow_html=True)
        return

    header_cells = "".join(
        f'<th style="text-align:right;padding:8px 12px;font-size:10px;color:{T["t3"]};font-weight:700;letter-spacing:0.5px;font-family:{T["mono"]};">{h}</th>'
        for h in ["CMP (₹)", "P/E", "M.CAP (₹ Cr)", "DIV YIELD %", "ROCE %"]
    )
    body_rows = ""
    for row in result["rows"]:
        bg = f'{T["amber"]}14' if row["is_current"] else "transparent"
        name_color = T["amber"] if row["is_current"] else T["ivory"]
        current_tag = f'<span style="color:{T["amber"]};font-size:9px;font-weight:700;border:1px solid {T["amber"]}55;padding:1px 6px;margin-left:6px;">CURRENT</span>' if row["is_current"] else ""
        body_rows += f"""
        <tr style="background:{bg};border-bottom:1px solid {T['border']};">
            <td style="padding:8px 12px;font-size:12px;color:{name_color};font-weight:700;font-family:{T['mono']};">{row['name']} <span style="color:{T['t3']};font-weight:500;">({row['symbol']})</span>{current_tag}</td>
            <td style="text-align:right;padding:8px 12px;font-family:{T['mono']};font-size:12px;color:{T['ivory']};">₹{row['cmp']}</td>
            <td style="text-align:right;padding:8px 12px;font-family:{T['mono']};font-size:12px;color:{T['ivory']};">{row['pe']}</td>
            <td style="text-align:right;padding:8px 12px;font-family:{T['mono']};font-size:12px;color:{T['ivory']};">₹{row['market_cap']}</td>
            <td style="text-align:right;padding:8px 12px;font-family:{T['mono']};font-size:12px;color:{T['ivory']};">{row['div_yield']}%</td>
            <td style="text-align:right;padding:8px 12px;font-family:{T['mono']};font-size:12px;color:{T['green']};">{row['roce']}%</td>
        </tr>"""

    st.markdown(f"""
    <div style="overflow-x:auto;border:1px solid {T['border']};background:{T['panel']};">
        <table style="border-collapse:collapse;width:100%;">
            <thead><tr style="border-bottom:1px solid {T['border']};background:{T['panel2']};">
                <th style="text-align:left;padding:8px 12px;font-size:10px;color:{T['t3']};font-weight:700;letter-spacing:0.5px;font-family:{T['mono']};">NAME / SYMBOL</th>
                {header_cells}
            </tr></thead>
            <tbody>{body_rows}</tbody>
        </table>
    </div>
    <div style="font-size:9px;color:{T['t3']};margin-top:6px;font-family:{T['mono']};">
        PEER SET IS CURATED (MAJOR SECTORS ONLY) — EACH FIGURE IS A LIVE SCREENER.IN FETCH.</div>
    """, unsafe_allow_html=True)


# ── MAIN RENDER ───────────────────────────────────────────────

def render_research_page(T: dict = None, news_fetch_fn=None):
    T = _get_theme(T)

    st.markdown(f"""
    <style>
        [data-testid="stAppViewContainer"] {{ background:{T['bg']}; }}
        div[data-testid="stTextInput"] input {{
            font-family:{T['mono']}; background:{T['panel']}; color:{T['ivory']};
            border:1px solid {T['border']};
        }}
        div[data-testid="stTextInput"] input:focus {{ border-color:{T['amber']}; }}
    </style>
    """, unsafe_allow_html=True)

    _render_status_strip(T)

    st.markdown(f"<div style='font-size:11px; color:{T['t3']}; margin-bottom:4px;font-family:{T['mono']};'>ENTER EXACT NSE SYMBOL</div>", unsafe_allow_html=True)

    query = st.text_input("Search symbol", placeholder="e.g. RELIANCE, HDFCBANK",
                           label_visibility="collapsed", key="research_query_input")

    _render_hot_tickers_blotter(T)

    if query and query.strip().upper() != st.session_state.get("research_last_query", "").upper():
        st.session_state["research_last_query"] = query.strip()
        st.session_state.pop("research_data", None)

    active_query = st.session_state.get("research_last_query", "")

    st.markdown(f"<hr style='border-color:{T['border']};margin:16px 0;'>", unsafe_allow_html=True)

    if not active_query:
        st.markdown(f"""<div style="padding:60px 20px;text-align:center;color:{T['t3']};font-size:12px;font-family:{T['mono']};border:1px dashed {T['border']};">
            SELECT A HOT TICKER OR ENTER AN EXACT NSE SYMBOL ABOVE TO INITIALIZE THE TERMINAL.</div>""",
            unsafe_allow_html=True)
        return

    # Robust fetch: try full research, fallback to best-effort summary on error
    if "research_data" not in st.session_state:
        with st.spinner(f"Pulling institutional data for {active_query.upper()}..."):
            try:
                st.session_state["research_data"] = get_full_research(active_query)
            except Exception as e:
                st.error(f"Full research fetch failed for {active_query.upper()}. Falling back to summary.")
                try:
                    res = resolve_symbol(active_query)
                except Exception:
                    res = None
                if res:
                    try:
                        summary = get_summary(active_query, url=res.get("url"))
                        sdata = summary.get("data") if isinstance(summary, dict) else {}
                    except Exception:
                        sdata = {}
                    st.session_state["research_data"] = {
                        "resolved": True,
                        "symbol": active_query.upper(),
                        "name": res.get("name", active_query.upper()),
                        "url": res.get("url"),
                        "summary": {"status": "live" if sdata else "unavailable", "data": sdata},
                        "quarterly": {"status": "unavailable", "data": {}},
                        "yearly": {"status": "unavailable", "data": {}},
                        "balance_sheet": {"status": "unavailable", "data": {}},
                        "shareholding": {"status": "unavailable", "data": {}},
                        "sector": {"status": "unavailable", "data": {}},
                        "peers": {"status": "not_implemented", "data": None},
                    }
                else:
                    st.session_state["research_data"] = {"resolved": False, "reason": f"fetch exception: {e}"}

    data = st.session_state["research_data"]

    if not data.get("resolved"):
        st.error(f"SYMBOL NOT FOUND: {data.get('reason','')}")
        return

    summary = data["summary"]
    sfields = summary.get("data") or {}
    current_price = sfields.get("current_price", "—")

    if sfields:
        stat_order = [
            ("Market Cap", sfields.get("market_cap", "—"), "₹", "Cr"),
            ("Current Price", current_price, "₹", ""),
            ("P/E Ratio", sfields.get("pe_ratio", "—"), "", "x"),
            ("Book Value", sfields.get("book_value", "—"), "₹", ""),
            ("Div Yield", sfields.get("dividend_yield", "—"), "", "%"),
            ("ROCE", sfields.get("roce", "—"), "", "%"),
            ("ROE", sfields.get("roe", "—"), "", "%"),
            ("Face Value", sfields.get("face_value", "—"), "₹", ""),
        ]
        cells = "".join(
            f'<div style="flex:1;min-width:100px;padding:10px 14px;border-right:1px solid {T["border"]};">\n'
            f'<div style="font-size:9px;color:{T["t3"]};letter-spacing:1px;margin-bottom:4px;font-family:{T["mono"]};">{label.upper()}</div>'
            f'<div style="font-family:{T["mono"]};font-size:14px;color:{T["amber"] if label=="Current Price" else T["ivory"]};font-weight:700;">{pre}{val}{post}</div>'
            f'</div>'
            for label, val, pre, post in stat_order
        )
        st.markdown(f"""
        <div style="border:1px solid {T['border']};border-top:2px solid {T['amber']};padding:12px 16px;background:{T['panel']};">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;">
                <div><span style="font-size:16px;font-weight:800;color:{T['ivory']};font-family:{T['mono']};">{data['name']}</span>
                <span style="font-family:{T['mono']};font-size:11px;color:{T['t3']};margin-left:8px;">{data['symbol']} · NSE / BSE</span></div>
                {_status_tag(summary['status'], summary.get('age_seconds'), T)}
            </div>
        </div>
        <div style="display:flex;flex-wrap:wrap;border:1px solid {T['border']};border-top:none;background:{T['panel']};">{cells}</div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="padding:10px;color:{T["t3"]};font-size:11px;border:1px solid {T["border"]};font-family:{T["mono"]};">KEY STATS UNAVAILABLE.</div>',
                     unsafe_allow_html=True)

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    chart_col, factors_col = st.columns([2.5, 1])
    with chart_col:
        _render_tv_chart(data["symbol"])
    with factors_col:
        st.markdown(f"""<div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['amber']};letter-spacing:1px;margin-bottom:8px;">FACTORS &amp; ALERTS</div>""",
                    unsafe_allow_html=True)
        _render_factors_panel(data["symbol"], data, T)

    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
    selected_tab = option_menu(
        menu_title=None,
        options=["Valuation & Quality", "Financial Statements", "Balance Sheet & Leverage", "Shareholding Dynamics"],
        icons=["bar-chart", "file-earmark-text", "bank", "pie-chart"],
        default_index=0, orientation="horizontal",
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": T["amber"], "font-size": "14px"},
            "nav-link": {"font-size": "12px", "text-align": "center", "margin": "0px", "--hover-color": T["panel"], "font-family": T["mono"]},
            "nav-link-selected": {"background-color": "transparent", "color": T["amber"], "border-bottom": f"2px solid {T['amber']}"},
        }
    )
    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    if selected_tab == "Valuation & Quality":
        sector = data["sector"]
        sfields2 = sector.get("data") or {}
        if sfields2:
            chain = " → ".join(sfields2.get(k, "") for k in ("Broad Sector", "Sector", "Broad Industry", "Industry") if sfields2.get(k))
            st.markdown(f'<div style="font-size:12px;color:{T["ivory"]};border:1px solid {T["border"]};background:{T["panel"]};padding:12px 14px;margin-bottom:12px;font-family:{T["mono"]};">SECTOR: <b style="color:{T["amber"]};">{chain}</b></div>', unsafe_allow_html=True)

        result = get_earnings_date(data["symbol"])
        if result["status"] == "live" and result.get("date"):
            st.markdown(f"""<div style="border:1px solid {T['border']};background:{T['panel']};padding:12px 16px;margin-bottom:12px;font-family:{T['mono']};">
                <span style="color:{T['t3']};font-size:11px;">NEXT EARNINGS DATE:</span>
                <span style="font-family:{T['mono']};font-size:14px;font-weight:700;color:{T['ivory']};margin-left:8px;">{result['date']}</span>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['amber']};letter-spacing:1px;">INDUSTRY PEER COMPARISON</div>
        </div>""", unsafe_allow_html=True)
        _render_peer_comparison(data["symbol"], T)

    elif selected_tab == "Financial Statements":
        q = data["quarterly"]
        y = data["yearly"]
        st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">QUARTERLY RESULTS</div>
            {_status_tag(q['status'], q.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        qdata = q.get("data") or {}
        _render_data_table(qdata.get("periods", []), qdata.get("rows", []), T, highlight_labels=["Net Profit", "Sales"])

        st.markdown("<div style='height:20px;'></div>", unsafe_allow_html=True)
        st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">YEARLY RESULTS (P&amp;L)</div>
            {_status_tag(y['status'], y.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        ydata = y.get("data") or {}
        _render_data_table(ydata.get("periods", []), ydata.get("rows", []), T, highlight_labels=["Net Profit", "Sales"])

    elif selected_tab == "Balance Sheet & Leverage":
        bs = data["balance_sheet"]
        st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">BALANCE SHEET</div>
            {_status_tag(bs['status'], bs.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        bsdata = bs.get("data") or {}
        _render_data_table(bsdata.get("periods", []), bsdata.get("rows", []), T,
                            highlight_labels=["Total Assets", "Total Liabilities", "Borrowings"])

        st.markdown("<div style='height:20px;'></div>", unsafe_allow_html=True)
        st.markdown(f"""<div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};margin-bottom:8px;">LEVERAGE RATIOS (COMPUTED)</div>""",
                    unsafe_allow_html=True)
        leverage = get_leverage_ratios(data["symbol"], full_research=data)
        if leverage["status"] == "unavailable":
            st.markdown(f"""<div style="border:1px dashed {T['border']};background:{T['panel']};padding:16px;text-align:center;">
                <div style="font-size:11px;color:{T['t3']};font-family:{T['mono']};">COULD NOT COMPUTE — REQUIRED ROWS UNAVAILABLE.</div>
            </div>""", unsafe_allow_html=True)
        else:
            de_str = f'{leverage["debt_to_equity"]:.2f}x' if leverage["debt_to_equity"] is not None else "N/A"
            ic_str = f'{leverage["interest_coverage"]:.2f}x' if leverage["interest_coverage"] is not None else "N/A"
            st.markdown(f"""
            <div style="display:flex;gap:0;border:1px solid {T['border']};background:{T['panel']};">
                <div style="flex:1;padding:14px 16px;border-right:1px solid {T['border']};">
                    <div style="font-size:9px;color:{T['t3']};letter-spacing:1px;margin-bottom:4px;font-family:{T['mono']};">DEBT TO EQUITY</div>
                    <div style="font-family:{T['mono']};font-size:16px;color:{T['ivory']};font-weight:700;">{de_str}</div>
                </div>
                <div style="flex:1;padding:14px 16px;">
                    <div style="font-size:9px;color:{T['t3']};letter-spacing:1px;margin-bottom:4px;font-family:{T['mono']};">INTEREST COVERAGE</div>
                    <div style="font-family:{T['mono']};font-size:16px;color:{T['ivory']};font-weight:700;">{ic_str}</div>
                </div>
            </div>
            <div style="font-size:9px;color:{T['t3']};margin-top:6px;font-family:{T['mono']};">COMPUTED FROM BALANCE SHEET + QUARTERLY ROWS ABOVE — NOT A SEPARATE SOURCE.</div>
            """, unsafe_allow_html=True)

    elif selected_tab == "Shareholding Dynamics":
        sh = data["shareholding"]
        st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">SHAREHOLDING PATTERN</div>
            {_status_tag(sh['status'], sh.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        shdata = sh.get("data") or {}
        _render_data_table(shdata.get("periods", []), shdata.get("rows", []), T, highlight_labels=["Promoters", "FIIs", "DIIs"])

    # ── News Feed (sentiment-tagged) ──────────────────────────────
    st.markdown("<div style='height:40px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;border-bottom:1px solid {T['border']};padding-bottom:8px;margin-bottom:12px;">
        <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};letter-spacing:1px;">COMPANY NEWS FEED</div>
    </div>""", unsafe_allow_html=True)

    try:
        from news_feed import _ensure_news_state, refresh_news, sentiment_color
        _ensure_news_state()
        refresh_news([data["symbol"]])
        articles = st.session_state.get("_news_cache", {}).get(data["symbol"], [])
    except Exception:
        articles = []
        sentiment_color = lambda s: T["t3"]

    if not articles:
        st.markdown(f'<div style="font-size:11px;color:{T["t3"]};font-family:{T["mono"]};">NO RECENT NEWS FOUND FOR {data["symbol"]}.</div>', unsafe_allow_html=True)
    else:
        news_cols = st.columns(2)
        for i, art in enumerate(articles[:8]):
            sentiment = art.get("sentiment", "NEUTRAL")
            sc = sentiment_color(sentiment)
            with news_cols[i % 2]:
                st.markdown(f"""
                <div style="border:1px solid {T['border']};background:{T['panel']};padding:12px 14px;margin-bottom:10px;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
                        <span style="font-size:10px;color:{T['amber']};font-weight:700;font-family:{T['mono']};">{art.get('source','')}</span>
                        <span style="font-family:{T['mono']};font-size:9px;color:{T['t3']};">{art.get('time_str','')}</span>
                    </div>
                    <a href="{art.get('link','#')}" target="_blank" style="font-size:13px;color:{T['ivory']};
                       text-decoration:none;line-height:1.4;display:block;margin-bottom:6px;">{art.get('title','')}</a>
                    <div style="font-size:9px;color:{T['t3']};font-family:{T['mono']};">SENTIMENT: <span style="color:{sc};font-weight:700;">{sentiment}</span></div>
                </div>""", unsafe_allow_html=True)
