"""
research_page.py — Arka Trades Research Terminal (v3 — Tabbed Pro Layout)
"""

import re
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


# ── Chart Panel (Upgraded with Volume) ──────────────────────────

def _fetch_chart_data(symbol: str, period: str = "6mo"):
    try:
        import yfinance as yf
        # FIX: Clean the symbol so "HDFC BANK" becomes "HDFCBANK.NS"
        clean_symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper().strip())
        h = yf.Ticker(clean_symbol + ".NS").history(period=period, interval="1d")
        if h is None or h.empty or len(h) < 2:
            return None
        return h
    except Exception:
        return None


def _render_chart_panel(symbol: str, T: dict):
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">PRICE & VOLUME</div>
    </div>""", unsafe_allow_html=True)

    period_key = f"research_chart_period_{symbol}"
    period_choice = st.radio(
        "Range", ["1mo", "3mo", "6mo", "1y"], index=2, horizontal=True,
        key=period_key, label_visibility="collapsed",
    )

    hist = _fetch_chart_data(symbol, period=period_choice)
    if hist is None:
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:40px 16px;text-align:center;">
            <div style="font-size:11px;color:{T['t3']};">Chart unavailable — could not fetch price history for {symbol} from yfinance. Check if symbol is valid.</div>
        </div>""", unsafe_allow_html=True)
        return

    try:
        from plotly.subplots import make_subplots
        import plotly.graph_objects as go
        
        up_color = T["green"]
        down_color = T["red"]
        
        # Color array for volume bars
        colors = [up_color if row['Close'] >= row['Open'] else down_color for _, row in hist.iterrows()]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                            vertical_spacing=0.03, subplot_titles=('', ''), 
                            row_width=[0.25, 0.75])

        # Candlestick
        fig.add_trace(go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"],
            low=hist["Low"], close=hist["Close"],
            increasing_line_color=up_color, decreasing_line_color=down_color,
            increasing_fillcolor=up_color, decreasing_fillcolor=down_color,
            name="Price"
        ), row=1, col=1)

        # Volume
        fig.add_trace(go.Bar(
            x=hist.index, y=hist['Volume'],
            marker_color=colors,
            name="Volume"
        ), row=2, col=1)

        fig.update_layout(
            height=400, margin=dict(l=8, r=8, t=8, b=8),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["t2"], size=10),
            xaxis=dict(rangeslider_visible=False, showgrid=False, type='category'),
            xaxis2=dict(gridcolor=T["border"], showgrid=False, type='category'),
            yaxis=dict(gridcolor=T["border"], side="right"),
            yaxis2=dict(gridcolor=T["border"], side="right", showticklabels=False),
            showlegend=False,
        )
        # Simplify the x-axis labels to avoid crowding
        fig.update_xaxes(nticks=10, row=2, col=1)
        
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except ImportError:
        st.line_chart(hist["Close"], height=340)
        st.caption("Install plotly for candlestick view — showing closing price line instead.")


# ── Factors panel ──────────────────────────────────────────────

def _render_factors_panel(symbol: str, full_research: dict, T: dict):
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">FACTORS & ALERTS</div>
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

    # Upcoming Macro Data Calendar specifically formatted for high-impact events
    st.markdown(f"""<div style="margin-top:14px;font-family:{T['mono']};font-size:10px;font-weight:700;color:{T['t3']};letter-spacing:1px;">UPCOMING MACRO DATA (48H)</div>""",
                unsafe_allow_html=True)
    
    # Placeholder struct for Macro events. Once you plug in Investing.com API or similar, this renders beautifully.
    mock_macro_calendar = [
        {"time": "Tomorrow, 6:00 PM", "event": "US Core CPI (MoM)", "impact": "HIGH", "color": T["red"]},
        {"time": "Tomorrow, 8:00 PM", "event": "Crude Oil Inventories", "impact": "MED", "color": T["amber"]},
        {"time": "Thurs, 10:00 AM", "event": "RBI Monetary Policy Minutes", "impact": "HIGH", "color": T["red"]}
    ]
    
    for item in mock_macro_calendar:
        st.markdown(f"""
        <div style="padding:6px 0;border-bottom:1px solid {T['border']};display:flex;justify-content:space-between;align-items:center;">
            <div>
                <div style="font-size:11px;color:{T['ivory']};font-weight:600;">{item['event']}</div>
                <div style="font-size:9.5px;color:{T['t3']};margin-top:2px;">{item['time']}</div>
            </div>
            <div style="font-size:9px;font-weight:700;color:{item['color']};border:1px solid {item['color']}55;padding:2px 4px;border-radius:2px;">
                {item['impact']} IMPACT
            </div>
        </div>""", unsafe_allow_html=True)


# ── MAIN RENDER ───────────────────────────────────────────────

def render_research_page(T: dict, news_fetch_fn=None):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0 14px;">
        <div style="font-family:{T['mono']};font-size:13px;font-weight:700;color:{T['amber']};letter-spacing:2px;">
            RESEARCH TERMINAL</div>
        <div style="flex:1;height:1px;background:{T['border']};"></div>
    </div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='font-size:11px; color:{T['t3']}; margin-bottom:8px;'>Enter Exact NSE Symbol (e.g. HDFCBANK, RELIANCE)</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([4, 1])
    with c1:
        query = st.text_input("Search symbol", placeholder="e.g. RELIANCE, HDFCBANK",
                               label_visibility="collapsed", key="research_query")
    with c2:
        search = st.button("SEARCH", use_container_width=True, type="primary", key="research_search")

    if not (search or st.session_state.get("research_last_query")):
        st.markdown(f"""<div style="padding:60px 20px;text-align:center;color:{T['t3']};font-size:12px;">
            Enter an exact NSE symbol above to pull fundamentals, chart, factors, and shareholding data.</div>""",
            unsafe_allow_html=True)
        return

    if search and query.strip():
        st.session_state["research_last_query"] = query.strip()
        st.session_state.pop("research_data", None) 

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

    # ── Header strip ──
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

    # ── Grid row 1: Chart & Factors ──────────────
    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    grid_a, grid_b = st.columns([2.5, 1.2])
    with grid_a:
        _render_chart_panel(data["symbol"], T)
    with grid_b:
        _render_factors_panel(data["symbol"], data, T)

    # ── Grid row 2: Tabbed Sub-Groups ──────────────
    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
    
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Valuation & Quality", 
        "📈 Financial Statements", 
        "🏛️ Balance Sheet & Leverage", 
        "🤝 Shareholding Dynamics"
    ])

    with tab1:
        # Valuation & Quality Scorecard
        st.markdown(f"""<div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};margin-bottom:12px;">VALUATION & SECTOR METRICS</div>""", unsafe_allow_html=True)
        
        sector = data["sector"]
        sfields2 = sector.get("data") or {}
        if sfields2:
            chain = " → ".join(sfields2.get(k, "") for k in ("Broad Sector", "Sector", "Broad Industry", "Industry") if sfields2.get(k))
            st.markdown(f'<div style="font-size:12px;color:{T["ivory"]};border:1px solid {T["border"]};padding:12px 14px;margin-bottom:12px;">Sector: <b>{chain}</b></div>', unsafe_allow_html=True)
        
        # Next Earnings Data
        result = get_earnings_date(data["symbol"])
        if result["status"] == "live" and result.get("date"):
            st.markdown(f"""<div style="border:1px solid {T['border']};padding:12px 16px; margin-bottom:12px;">
                <span style="color:{T['t3']};font-size:11px;">Next Earnings Date:</span> 
                <span style="font-family:{T['mono']};font-size:14px;font-weight:700;color:{T['ivory']};margin-left:8px;">{result['date']}</span>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:14px 16px;">
            <div style="font-size:11px;color:{T['t3']};line-height:1.7;">
                Peer Comparison Data currently loads via an authenticated background request on Screener. Sector P/E as a standalone figure is pending integration.</div>
        </div>""", unsafe_allow_html=True)

    with tab2:
        # Financial Statements
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
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">YEARLY RESULTS (P&L)</div>
            {_status_tag(y['status'], y.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        ydata = y.get("data") or {}
        _render_data_table(ydata.get("periods", []), ydata.get("rows", []), T, highlight_labels=["Net Profit", "Sales"])

    with tab3:
        # Balance Sheet & Leverage
        st.markdown(f"""<div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};margin-bottom:12px;">BALANCE SHEET</div>""", unsafe_allow_html=True)
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:24px 16px;text-align:center;">
            <div style="font-size:12px;color:{T['t3']};line-height:1.7;">
                Integration for full Balance Sheet (Debt-to-Equity, Cash Flows, CWIP) is scheduled for the next scraper update.<br>Currently focusing on P&L and Core Valuation metrics.</div>
        </div>""", unsafe_allow_html=True)

    with tab4:
        # Shareholding Dynamics
        sh = data["shareholding"]
        st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">SHAREHOLDING PATTERN</div>
            {_status_tag(sh['status'], sh.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        shdata = sh.get("data") or {}
        _render_data_table(shdata.get("periods", []), shdata.get("rows", []), T, highlight_labels=["Promoters", "FIIs", "DIIs"])


    # ── News Feed ──
    st.markdown("<div style='height:30px;'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div style="display:flex;align-items:center;gap:10px;border-bottom:1px solid {T['border']};padding-bottom:8px;margin-bottom:12px;">
        <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};letter-spacing:1px;">COMPANY NEWS FEED</div>
    </div>""", unsafe_allow_html=True)
    
    try:
        from news_feed import _ensure_news_state, refresh_news
        _ensure_news_state()
        refresh_news([data["symbol"]])
        articles = st.session_state.get("_news_cache", {}).get(data["symbol"], [])
    except Exception:
        articles = []

    if not articles:
        st.markdown(f'<div style="font-size:11px;color:{T["t3"]};">No recent news found for {data["symbol"]}.</div>',
                     unsafe_allow_html=True)
    else:
        for art in articles[:8]:
            st.markdown(f"""
            <div style="padding:8px 4px;display:flex;justify-content:space-between;gap:12px;">
                <a href="{art.get('link','#')}" target="_blank" style="font-size:13px;color:{T['ivory']};
                   text-decoration:none;flex:1;line-height:1.4;">{art.get('title','')}</a>
                <span style="font-family:{T['mono']};font-size:10px;color:{T['t3']};white-space:nowrap;padding-top:2px;">{art.get('time_str','')}</span>
            </div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="margin-top:40px;font-size:10px;color:{T['t3']};text-align:center;">
        Source: Screener.in (unauthenticated) · yfinance · Data provided by C-MOTS Internet Technologies</div>""", unsafe_allow_html=True)

this is the current code the send me the new rewritten code 
and make it like i sended the video make same to same
