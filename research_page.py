"""
research_page.py — Arka Trades Research Terminal (v2 — dense multi-panel grid)

CHANGED FROM v1:
  - Layout: was a single-column vertical scroll (header -> sector ->
    quarterly -> yearly -> shareholding -> peers placeholder -> news
    -> footer, one section after another). Now a dense grid: a price
    chart panel, a factors panel, and a news panel sit side-by-side
    near the top, with financial tables below — closer to a real
    terminal screen where multiple panels are visible without
    scrolling between them, per direct request.
  - NEW: price chart panel, sourced from yfinance (this page
    previously never touched yfinance at all — it was 100%
    Screener-scraped). Reuses the exact yf.Ticker(sym+".NS").history()
    pattern already proven in app.py's get_static/get_price, so this
    isn't a new, unverified way of talking to yfinance.
  - NEW: factors panel (screener_scraper.get_factors) — directional
    deltas only (ROCE/ROE/P-E latest reading, promoter holding
    change, Sales/Net Profit QoQ change). Deliberately does NOT say
    whether a change is good or bad — see get_factors()'s docstring
    in screener_scraper.py for why that line is drawn there.
  - NEW: earnings-date panel (screener_scraper.get_earnings_date) —
    best-effort via yfinance. NSE tickers do not reliably carry this
    field in yfinance's data; when it's empty, this shows an honest
    "Not available" state, same pattern as the peer-comparison
    section already used for a real, confirmed data gap.
  - FIXED: news section now goes through news_feed.py's shared
    refresh_news()/_news_cache instead of calling the raw
    _fetch_news_for_stock() fetcher directly on every rerun. The
    previous version bypassed news_feed.py's 20-minute cache
    entirely, meaning every page interaction fired a fresh live RSS
    request — this now shares the exact same cache used everywhere
    else news is shown in the app, filtered to just this symbol.
  - UNCHANGED: quarterly/yearly/shareholding tables, sector
    classification, peer-comparison honest placeholder, "symbol not
    found" state, and the overall Screener-scraped data source for
    all of those — none of that was broken, so none of it changed.
"""

import streamlit as st
from datetime import datetime, timezone, timedelta

from screener_scraper import get_full_research, get_factors, get_earnings_date

IST = timezone(timedelta(hours=5, minutes=30))


def _age_label(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    hrs = seconds / 3600
    if hrs < 48:
        return f"{int(hrs)}h ago"
    return f"{int(hrs // 24)}d ago"


def _status_tag(status: str, age_seconds: float = None, T=None) -> str:
    """Small inline status marker — LIVE / STALE(age) / N/A. Matches
    the terminal language: square tag, no pill/rounded background."""
    if status == "live":
        return f'<span style="color:{T["green"]};font-size:10px;font-weight:700;letter-spacing:1px;">● LIVE</span>'
    elif status == "stale":
        age = _age_label(age_seconds) if age_seconds is not None else "?"
        return f'<span style="color:{T["amber"]};font-size:10px;font-weight:700;letter-spacing:1px;">◐ CACHED · {age}</span>'
    elif status == "partial":
        return f'<span style="color:{T["amber"]};font-size:10px;font-weight:700;letter-spacing:1px;">◐ PARTIAL</span>'
    else:
        return f'<span style="color:{T["t3"]};font-size:10px;font-weight:700;letter-spacing:1px;">✕ N/A</span>'


def _render_data_table(periods, rows, T, highlight_labels=None):
    """
    Dense monospace table matching terminal density: thin row
    dividers, right-aligned numeric columns, alternating row tint.
    highlight_labels: row labels to bold/tint (e.g. 'Net Profit +').
    """
    if not rows:
        st.markdown(f'<div style="padding:16px;color:{T["t3"]};font-size:12px;">No data rows parsed.</div>',
                     unsafe_allow_html=True)
        return

    highlight_labels = highlight_labels or []
    col_w = max(60, int(560 / max(len(periods), 1)))

    header_cells = "".join(
        f'<th style="text-align:right;padding:5px 8px;font-size:10px;color:{T["t3"]};'
        f'font-weight:600;white-space:nowrap;min-width:{col_w}px;">{p}</th>'
        for p in periods
    )
    body_rows = ""
    for i, row in enumerate(rows):
        bg = T["row_alt"] if i % 2 == 1 else "transparent"
        is_hl = any(h.lower() in row["label"].lower() for h in highlight_labels)
        label_color = T["ivory"] if is_hl else T["t2"]
        label_weight = "700" if is_hl else "500"
        cells = "".join(
            f'<td style="text-align:right;padding:5px 8px;font-family:{T["mono"]};'
            f'font-size:11.5px;color:{T["ivory"] if is_hl else T["t2"]};white-space:nowrap;">{v}</td>'
            for v in row["values"]
        )
        body_rows += (
            f'<tr style="background:{bg};border-bottom:1px solid {T["border"]};">'
            f'<td style="padding:5px 8px;font-size:11.5px;color:{label_color};'
            f'font-weight:{label_weight};white-space:nowrap;">{row["label"]}</td>{cells}</tr>'
        )

    st.markdown(f"""
    <div style="overflow-x:auto;border:1px solid {T['border']};">
        <table style="border-collapse:collapse;width:100%;">
            <thead><tr style="border-bottom:1px solid {T['border']};background:{T['panel2']};">
                <th style="text-align:left;padding:5px 8px;font-size:10px;color:{T['t3']};font-weight:600;">METRIC</th>
                {header_cells}
            </tr></thead>
            <tbody>{body_rows}</tbody>
        </table>
    </div>""", unsafe_allow_html=True)


# ── NEW: price chart panel ──────────────────────────────────────

def _fetch_chart_data(symbol: str, period: str = "6mo"):
    """
    Fetches OHLC history via yfinance, same call shape already proven
    in app.py's get_static/get_price (yf.Ticker(sym+'.NS').history()).
    Returns the raw DataFrame or None on any failure — chart panel
    below shows an honest 'unavailable' state rather than an empty
    or broken chart if this returns None.
    """
    try:
        import yfinance as yf
        h = yf.Ticker(symbol.upper().strip() + ".NS").history(period=period, interval="1d")
        if h is None or h.empty or len(h) < 2:
            return None
        return h
    except Exception:
        return None


def _render_chart_panel(symbol: str, T: dict):
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">PRICE CHART</div>
    </div>""", unsafe_allow_html=True)

    period_key = f"research_chart_period_{symbol}"
    period_choice = st.radio(
        "Range", ["1mo", "3mo", "6mo", "1y"], index=2, horizontal=True,
        key=period_key, label_visibility="collapsed",
    )

    hist = _fetch_chart_data(symbol, period=period_choice)
    if hist is None:
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:40px 16px;text-align:center;">
            <div style="font-size:11px;color:{T['t3']};">Chart unavailable — could not fetch price history for {symbol} from yfinance.</div>
        </div>""", unsafe_allow_html=True)
        return

    try:
        import plotly.graph_objects as go
        up_color = T["green"]
        down_color = T["red"]
        fig = go.Figure(data=[go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"],
            low=hist["Low"], close=hist["Close"],
            increasing_line_color=up_color, decreasing_line_color=down_color,
            increasing_fillcolor=up_color, decreasing_fillcolor=down_color,
        )])
        fig.update_layout(
            height=340, margin=dict(l=8, r=8, t=8, b=8),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["t2"], size=10),
            xaxis=dict(gridcolor=T["border"], rangeslider_visible=False, showgrid=False),
            yaxis=dict(gridcolor=T["border"], side="right"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except ImportError:
        # Fallback if plotly isn't installed in this deployment —
        # degrade to a plain closing-price line via Streamlit's
        # built-in chart rather than crashing the whole page over a
        # missing optional dependency.
        st.line_chart(hist["Close"], height=340)
        st.caption("Install plotly for candlestick view — showing closing price line instead.")


# ── NEW: factors panel (macro + micro) ──────────────────────────

def _render_factors_panel(symbol: str, full_research: dict, T: dict):
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">FACTORS</div>
    </div>""", unsafe_allow_html=True)

    factors = get_factors(symbol, full_research=full_research)

    if factors["status"] == "unavailable" or not factors["items"]:
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:20px 16px;text-align:center;">
            <div style="font-size:11px;color:{T['t3']};">Not enough underlying data to compute factor deltas for {symbol}.</div>
        </div>""", unsafe_allow_html=True)
    else:
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
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid {T['border']};">
                <span style="font-size:11px;color:{T['t2']};">{item['label']}</span>
                <div style="text-align:right;">
                    <span style="font-family:{T['mono']};font-size:12px;color:{T['ivory']};font-weight:700;">{latest_str}</span>
                    <span style="margin-left:8px;">{delta_str}</span>
                </div>
            </div>""")
        st.markdown(f'<div style="border:1px solid {T["border"]};padding:4px 12px;">{"".join(rows_html)}</div>',
                     unsafe_allow_html=True)
        if factors["status"] == "partial":
            st.markdown(f'<div style="font-size:9.5px;color:{T["t3"]};margin-top:4px;">Some factors unavailable — showing what could be computed.</div>',
                         unsafe_allow_html=True)

    # Macro factors — reuses the same national/international feed
    # already fetched for the news dock elsewhere in the app, filtered
    # to display here as short factor-style lines rather than full
    # article cards (news panel below already shows the full macro
    # feed with links; this is a compact restating for the factors
    # context specifically).
    st.markdown(f"""<div style="margin-top:14px;font-family:{T['mono']};font-size:10px;font-weight:700;color:{T['t3']};letter-spacing:1px;">MACRO CONTEXT</div>""",
                unsafe_allow_html=True)
    try:
        from news_feed import _ensure_news_state, refresh_news
        _ensure_news_state()
        refresh_news([])  # empty watchlist still refreshes the macro feed
        macro_items = st.session_state.get("_news_cache", {}).get("_MACRO_", [])
    except Exception:
        macro_items = []

    if not macro_items:
        st.markdown(f'<div style="font-size:11px;color:{T["t3"]};margin-top:4px;">No macro headlines available right now.</div>',
                     unsafe_allow_html=True)
    else:
        for art in macro_items[:4]:
            st.markdown(f"""<div style="padding:5px 0;border-bottom:1px solid {T['border']};">
                <a href="{art.get('link','#')}" target="_blank" style="font-size:11px;color:{T['ivory']};text-decoration:none;line-height:1.4;">{art.get('title','')}</a>
                <div style="font-size:9.5px;color:{T['t3']};margin-top:2px;">{art.get('time_str','')}</div>
            </div>""", unsafe_allow_html=True)


# ── NEW: earnings date panel ─────────────────────────────────────

def _render_earnings_panel(symbol: str, T: dict):
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">NEXT EARNINGS</div>
    </div>""", unsafe_allow_html=True)

    result = get_earnings_date(symbol)
    if result["status"] == "live" and result.get("date"):
        st.markdown(f"""<div style="border:1px solid {T['border']};border-top:2px solid {T['amber']};padding:14px 16px;">
            <div style="font-family:{T['mono']};font-size:16px;font-weight:700;color:{T['ivory']};">{result['date']}</div>
            <div style="font-size:9.5px;color:{T['t3']};margin-top:4px;">Source: {result.get('source','yfinance')}</div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:16px;text-align:center;">
            <div style="font-size:11px;color:{T['t3']};line-height:1.6;">Not available — yfinance does not reliably publish forward earnings
            dates for NSE-listed stocks. Check the company's investor relations page or NSE announcements directly for the confirmed date.</div>
        </div>""", unsafe_allow_html=True)


def render_research_page(T: dict, news_fetch_fn=None):
    """
    T: dict of terminal design tokens from app.py.
    news_fetch_fn: kept for backward-compatible call signature, but no
    longer used directly — the news section below now always goes
    through news_feed.py's shared cache (_ensure_news_state,
    refresh_news, _news_cache) instead of accepting a raw fetch
    callable, so a bad or missing argument here can no longer break
    this page. If news_feed.py itself is unavailable for some reason,
    the section fails to the same honest 'unavailable' pattern used
    everywhere else on this page.
    """
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0 14px;">
        <div style="font-family:{T['mono']};font-size:13px;font-weight:700;color:{T['amber']};letter-spacing:2px;">
            RESEARCH TERMINAL</div>
        <div style="flex:1;height:1px;background:{T['border']};"></div>
    </div>""", unsafe_allow_html=True)

    c1, c2 = st.columns([4, 1])
    with c1:
        query = st.text_input("Search symbol", placeholder="e.g. RELIANCE, TCS, ARKADE",
                               label_visibility="collapsed", key="research_query")
    with c2:
        search = st.button("SEARCH", use_container_width=True, type="primary", key="research_search")

    if not (search or st.session_state.get("research_last_query")):
        st.markdown(f"""<div style="padding:60px 20px;text-align:center;color:{T['t3']};font-size:12px;">
            Enter an NSE symbol above to pull fundamentals, chart, factors, and shareholding data.</div>""",
            unsafe_allow_html=True)
        return

    if search and query.strip():
        st.session_state["research_last_query"] = query.strip()
        st.session_state.pop("research_data", None)  # force refetch on new search

    active_query = st.session_state.get("research_last_query", "")
    if not active_query:
        return

    if "research_data" not in st.session_state:
        with st.spinner(f"Pulling data for {active_query.upper()}..."):
            st.session_state["research_data"] = get_full_research(active_query)

    data = st.session_state["research_data"]

    if not data.get("resolved"):
        st.markdown(f"""<div style="background:{T['panel']};border:1px solid {T['red']}55;
            border-left:3px solid {T['red']};padding:14px 18px;margin-top:8px;">
            <div style="font-size:12px;color:{T['red']};font-weight:700;">SYMBOL NOT FOUND</div>
            <div style="font-size:11px;color:{T['t3']};margin-top:4px;">{data.get('reason','')}</div>
        </div>""", unsafe_allow_html=True)
        return

    # ── Header strip: name, symbol, key stats in one dense row ──
    summary = data["summary"]
    sfields = summary.get("data") or {}
    st.markdown(f"""
    <div style="border:1px solid {T['border']};border-top:2px solid {T['amber']};padding:12px 16px;margin-top:6px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;">
            <div>
                <span style="font-size:16px;font-weight:800;color:{T['ivory']};">{data['name']}</span>
                <span style="font-family:{T['mono']};font-size:11px;color:{T['t3']};margin-left:8px;">{data['symbol']}</span>
            </div>
            {_status_tag(summary['status'], summary.get('age_seconds'), T)}
        </div>
    </div>""", unsafe_allow_html=True)

    if sfields:
        stat_order = [
            ("Market Cap", sfields.get("market_cap", "—"), "₹", "Cr"),
            ("Price", sfields.get("current_price", "—"), "₹", ""),
            ("P/E", sfields.get("pe_ratio", "—"), "", ""),
            ("Book Value", sfields.get("book_value", "—"), "₹", ""),
            ("Div Yield", sfields.get("dividend_yield", "—"), "", "%"),
            ("ROCE", sfields.get("roce", "—"), "", "%"),
            ("ROE", sfields.get("roe", "—"), "", "%"),
            ("Face Value", sfields.get("face_value", "—"), "₹", ""),
        ]
        cells = "".join(
            f'<div style="flex:1;min-width:90px;padding:8px 10px;border-right:1px solid {T["border"]};">'
            f'<div style="font-size:9px;color:{T["t3"]};letter-spacing:1px;margin-bottom:3px;">{label.upper()}</div>'
            f'<div style="font-family:{T["mono"]};font-size:13px;color:{T["ivory"]};font-weight:700;">{pre}{val}{post}</div>'
            f'</div>'
            for label, val, pre, post in stat_order
        )
        st.markdown(f'<div style="display:flex;flex-wrap:wrap;border:1px solid {T["border"]};border-top:none;">{cells}</div>',
                     unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="padding:10px;color:{T["t3"]};font-size:11px;border:1px solid {T["border"]};border-top:none;">Key stats unavailable.</div>',
                     unsafe_allow_html=True)

    # ── Grid row 1: Chart (wide) + Factors (narrow) ──────────────
    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    grid_a, grid_b = st.columns([2, 1])
    with grid_a:
        _render_chart_panel(data["symbol"], T)
    with grid_b:
        _render_factors_panel(data["symbol"], data, T)

    # ── Grid row 2: Earnings date (narrow) + Sector (wide) ───────
    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    grid_c, grid_d = st.columns([1, 2])
    with grid_c:
        _render_earnings_panel(data["symbol"], T)
    with grid_d:
        sector = data["sector"]
        sfields2 = sector.get("data") or {}
        st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">SECTOR</div>
            {_status_tag(sector['status'], sector.get('age_seconds'), T)}</div>""", unsafe_allow_html=True)
        if sfields2:
            chain = " → ".join(
                sfields2.get(k, "") for k in ("Broad Sector", "Sector", "Broad Industry", "Industry") if sfields2.get(k)
            )
            st.markdown(f'<div style="font-size:12px;color:{T["ivory"]};border:1px solid {T["border"]};padding:12px 14px;">{chain}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div style="font-size:11px;color:{T["t3"]};border:1px dashed {T["border"]};padding:12px 14px;">Classification unavailable.</div>', unsafe_allow_html=True)

        # ── Peer Comparison — explicitly unavailable, same honest
        # placeholder as before; left as-is per direct instruction.
        st.markdown(f"""<div style="margin-top:14px;display:flex;align-items:center;gap:10px;">
            <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">PEER COMPARISON</div>
            {_status_tag('not_implemented', None, T)}</div>
        <div style="border:1px dashed {T['border']};padding:14px 16px;margin-top:6px;">
            <div style="font-size:11px;color:{T['t3']};line-height:1.7;">
                Not available yet — Screener loads this table via a background request after
                the page loads, which a direct page fetch can't see. Sector P/E as a standalone
                figure is also not published on this page.</div>
        </div>""", unsafe_allow_html=True)

    # ── Quarterly Results ────────────────────────────────────────
    st.markdown("<div style='height:22px;'></div>", unsafe_allow_html=True)
    q = data["quarterly"]
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">QUARTERLY RESULTS (STANDALONE)</div>
        {_status_tag(q['status'], q.get('age_seconds'), T)}</div>""", unsafe_allow_html=True)
    qdata = q.get("data") or {}
    _render_data_table(qdata.get("periods", []), qdata.get("rows", []), T,
                        highlight_labels=["Net Profit", "Sales"])

    # ── Yearly Results ───────────────────────────────────────────
    y = data["yearly"]
    st.markdown(f"""<div style="margin-top:22px;display:flex;align-items:center;gap:10px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">YEARLY RESULTS — P&amp;L (STANDALONE)</div>
        {_status_tag(y['status'], y.get('age_seconds'), T)}</div>""", unsafe_allow_html=True)
    ydata = y.get("data") or {}
    _render_data_table(ydata.get("periods", []), ydata.get("rows", []), T,
                        highlight_labels=["Net Profit", "Sales"])

    # ── Shareholding Pattern ──────────────────────────────────────
    sh = data["shareholding"]
    st.markdown(f"""<div style="margin-top:22px;display:flex;align-items:center;gap:10px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">SHAREHOLDING PATTERN</div>
        {_status_tag(sh['status'], sh.get('age_seconds'), T)}</div>""", unsafe_allow_html=True)
    shdata = sh.get("data") or {}
    _render_data_table(shdata.get("periods", []), shdata.get("rows", []), T,
                        highlight_labels=["Promoters"])

    # ── News — FIXED: now goes through news_feed.py's shared cache
    # (_ensure_news_state / refresh_news / _news_cache) instead of
    # calling a raw fetch function directly. Filtered to this
    # symbol only, per direct instruction that this section should
    # be stock-specific (the FACTORS panel above is where the macro
    # feed shows up on this page).
    st.markdown(f"""<div style="margin-top:22px;display:flex;align-items:center;gap:10px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">RECENT NEWS — {data['symbol']}</div>
    </div>""", unsafe_allow_html=True)
    try:
        from news_feed import _ensure_news_state, refresh_news
        _ensure_news_state()
        refresh_news([data["symbol"]])
        articles = st.session_state.get("_news_cache", {}).get(data["symbol"], [])
    except Exception:
        articles = []

    if not articles:
        st.markdown(f'<div style="font-size:11px;color:{T["t3"]};margin-top:6px;">No news today for {data["symbol"]}.</div>',
                     unsafe_allow_html=True)
    else:
        for art in articles[:8]:
            st.markdown(f"""
            <div style="border-bottom:1px solid {T['border']};padding:7px 2px;display:flex;justify-content:space-between;gap:10px;">
                <a href="{art.get('link','#')}" target="_blank" style="font-size:12px;color:{T['ivory']};
                   text-decoration:none;flex:1;">{art.get('title','')}</a>
                <span style="font-family:{T['mono']};font-size:10px;color:{T['t3']};white-space:nowrap;">{art.get('time_str','')}</span>
            </div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="margin-top:24px;font-size:10px;color:{T['t3']};">
        Source: Screener.in (unauthenticated) · yfinance · Data provided by C-MOTS Internet Technologies ·
        Fetched via {data['url']}</div>""", unsafe_allow_html=True)
