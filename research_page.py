"""
research_page.py — Arka Trades Research Terminal (v4 — Pro TradingView Layout)
"""

import re
import streamlit as st
from datetime import datetime, timezone, timedelta
from streamlit_option_menu import option_menu

from screener_scraper import get_full_research, get_factors, get_earnings_date

IST = timezone(timedelta(hours=5, minutes=30))

# ── Helper Functions ──────────────────────────────────────────

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


# ── Chart Panel (TradingView Style) ──────────────────────────

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


def _render_tv_chart(symbol: str, current_price: str, price_change: str):
    period_key = f"research_chart_period_{symbol}"
    period_choice = st.radio(
        "Range", ["1mo", "3mo", "6mo", "1y", "2y"], index=2, horizontal=True,
        key=period_key, label_visibility="collapsed"
    )

    hist = _fetch_chart_data(symbol, period=period_choice)
    
    if hist is None:
        st.error(f"Could not fetch data for {symbol}")
        return

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        up_color = '#089981'
        down_color = '#F23645'
        bg_color = '#131722'
        grid_color = '#2A2E39'
        text_color = '#B2B5BE'

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
            x=hist.index, y=hist['Volume'],
            marker_color=colors,
            name="Volume"
        ), row=2, col=1)

        fig.update_layout(
            height=500, margin=dict(l=0, r=50, t=10, b=0),
            paper_bgcolor=bg_color, plot_bgcolor=bg_color,
            font=dict(color=text_color, size=11),
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
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:20px 16px;text-align:center;">
            <div style="font-size:11px;color:{T['t3']};">Not enough underlying data to compute factor deltas.</div>
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
        <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid {T['border']};">
            <span style="font-size:11px;color:{T['t2']};">{item['label']}</span>
            <div style="text-align:right;">
                <span style="font-family:{T['mono']};font-size:12px;color:{T['ivory']};font-weight:700;">{latest_str}</span>
                <span style="margin-left:8px;">{delta_str}</span>
            </div>
        </div>""")
        
    st.markdown(f'<div style="border:1px solid {T["border"]};padding:4px 12px; margin-bottom:16px;">{"".join(rows_html)}</div>',
                 unsafe_allow_html=True)


# ── MAIN RENDER ───────────────────────────────────────────────

def render_research_page(T: dict, news_fetch_fn=None):
    st.markdown(f"<div style='font-size:11px; color:{T['t3']}; margin-bottom:4px;'>Enter Exact NSE Symbol</div>", unsafe_allow_html=True)
    
    query = st.text_input("Search symbol", placeholder="e.g. RELIANCE, HDFCBANK", label_visibility="collapsed", key="research_query_input")
    
    st.write("<span style='color:#888; font-size:12px;'>Quick picks:</span>", unsafe_allow_html=True)
    cols = st.columns(8)
    picks = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN"]
    
    for i, col in enumerate(cols):
        with col:
            if st.button(picks[i], use_container_width=True, key=f"qp_{picks[i]}"):
                st.session_state["research_last_query"] = picks[i]
                st.session_state.pop("research_data", None) 
                st.rerun()

    if query and query.strip() != st.session_state.get("research_last_query", ""):
        st.session_state["research_last_query"] = query.strip()
        st.session_state.pop("research_data", None)

    active_query = st.session_state.get("research_last_query", "")
    
    st.markdown("---")

    if not active_query:
        st.markdown(f"""<div style="padding:60px 20px;text-align:center;color:{T['t3']};font-size:12px;">
            Select a quick pick or enter an exact NSE symbol above to initialize the terminal.</div>""",
            unsafe_allow_html=True)
        return

    if "research_data" not in st.session_state:
        with st.spinner(f"Pulling institutional data for {active_query.upper()}..."):
            st.session_state["research_data"] = get_full_research(active_query)

    data = st.session_state["research_data"]

    if not data.get("resolved"):
        st.error(f"SYMBOL NOT FOUND: {data.get('reason','')}")
        return

    summary = data["summary"]
    sfields = summary.get("data") or {}
    
    current_price = sfields.get("current_price", "—")
    
    chart_col, ai_col = st.columns([2.5, 1])

    with chart_col:
        st.markdown(f"""
            <div style='display:flex; align-items:baseline; gap:12px; margin-bottom:10px;'>
                <h2 style='margin:0; color:#4E8BFF; font-weight:800;'>{data['symbol'].upper()}</h2>
                <span style='color:#888; font-size:12px;'>• NSE • Daily</span>
                <h3 style='margin:0; font-family:{T["mono"]};'>₹{current_price}</h3>
                {_status_tag(summary['status'], summary.get('age_seconds'), T)}
            </div>
        """, unsafe_allow_html=True)
        
        _render_tv_chart(data["symbol"], current_price, "")

    with ai_col:
        st.markdown(f"""<div style="font-family:{T['mono']};font-size:13px;font-weight:700;color:{T['amber']};margin-bottom:12px;letter-spacing:1px;">
            ARKA AI ANALYSIS</div>""", unsafe_allow_html=True)
        
        _render_factors_panel(data["symbol"], data, T)

        st.markdown("<h4 style='color:#ddd; font-size:12px; margin-bottom:4px;'>Add context (optional)</h4>", unsafe_allow_html=True)
        st.text_area("Context", placeholder="e.g. Check for specific volume profile setups, liquidity sweeps, or order block interactions...", height=120, label_visibility="collapsed", key="ai_context")
        
        st.toggle("Auto-speak analysis", key="ai_voice_toggle")
        st.button("Analyze Technical Setup", type="primary", use_container_width=True, key="ai_analyze_btn")

    st.markdown("<div style='height:30px;'></div>", unsafe_allow_html=True)
    
    selected_tab = option_menu(
        menu_title=None,
        options=["Valuation & Quality", "Financial Statements", "Balance Sheet & Leverage", "Shareholding Dynamics"],
        icons=["bar-chart", "file-earmark-text", "bank", "pie-chart"],
        default_index=0,
        orientation="horizontal",
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": T["amber"], "font-size": "14px"}, 
            "nav-link": {"font-size": "12px", "text-align": "center", "margin":"0px", "--hover-color": T["panel"]},
            "nav-link-selected": {"background-color": "transparent", "color": T["amber"], "border-bottom": f"2px solid {T['amber']}"},
        }
    )

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    if selected_tab == "Valuation & Quality":
        sector = data["sector"]
        sfields2 = sector.get("data") or {}
        if sfields2:
            chain = " → ".join(sfields2.get(k, "") for k in ("Broad Sector", "Sector", "Broad Industry", "Industry") if sfields2.get(k))
            st.markdown(f'<div style="font-size:12px;color:{T["ivory"]};border:1px solid {T["border"]};padding:12px 14px;margin-bottom:12px;">Sector: <b>{chain}</b></div>', unsafe_allow_html=True)
        
        if sfields:
            stat_order = [
                ("Market Cap", sfields.get("market_cap", "—"), "₹", "Cr"),
                ("P/E", sfields.get("pe_ratio", "—"), "", ""),
                ("Book Value", sfields.get("book_value", "—"), "₹", ""),
                ("Div Yield", sfields.get("dividend_yield", "—"), "", "%"),
                ("ROCE", sfields.get("roce", "—"), "", "%"),
                ("ROE", sfields.get("roe", "—"), "", "%"),
                ("Face Value", sfields.get("face_value", "—"), "₹", ""),
            ]
            cells = "".join(
                f'<div style="flex:1;min-width:110px;padding:12px 16px;border-right:1px solid {T["border"]};">'
                f'<div style="font-size:10px;color:{T["t3"]};letter-spacing:1px;margin-bottom:6px;">{label.upper()}</div>'
                f'<div style="font-family:{T["mono"]};font-size:15px;color:{T["ivory"]};font-weight:700;">{pre}{val}{post}</div>'
                f'</div>'
                for label, val, pre, post in stat_order
            )
            st.markdown(f'<div style="display:flex;flex-wrap:wrap;border:1px solid {T["border"]};">{cells}</div>', unsafe_allow_html=True)

        result = get_earnings_date(data["symbol"])
        if result["status"] == "live" and result.get("date"):
            st.markdown(f"""<div style="border:1px solid {T['border']};padding:12px 16px; margin-top:12px;">
                <span style="color:{T['t3']};font-size:11px;">Next Earnings Date:</span> 
                <span style="font-family:{T['mono']};font-size:14px;font-weight:700;color:{T['ivory']};margin-left:8px;">{result['date']}</span>
            </div>""", unsafe_allow_html=True)

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
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">YEARLY RESULTS (P&L)</div>
            {_status_tag(y['status'], y.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        ydata = y.get("data") or {}
        _render_data_table(ydata.get("periods", []), ydata.get("rows", []), T, highlight_labels=["Net Profit", "Sales"])

    elif selected_tab == "Balance Sheet & Leverage":
        st.markdown(f"""<div style="border:1px dashed {T['border']};padding:24px 16px;text-align:center;">
            <div style="font-size:12px;color:{T['t3']};line-height:1.7;">
                Integration for full Balance Sheet (Debt-to-Equity, Cash Flows, CWIP) is scheduled for the next scraper update.</div>
        </div>""", unsafe_allow_html=True)

    elif selected_tab == "Shareholding Dynamics":
        sh = data["shareholding"]
        st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-family:{T['mono']};font-size:12px;font-weight:700;color:{T['amber']};">SHAREHOLDING PATTERN</div>
            {_status_tag(sh['status'], sh.get('age_seconds'), T)}
        </div>""", unsafe_allow_html=True)
        shdata = sh.get("data") or {}
        _render_data_table(shdata.get("periods", []), shdata.get("rows", []), T, highlight_labels=["Promoters", "FIIs", "DIIs"])

    st.markdown("<div style='height:40px;'></div>", unsafe_allow_html=True)
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
        st.markdown(f'<div style="font-size:11px;color:{T["t3"]};">No recent news found for {data["symbol"]}.</div>', unsafe_allow_html=True)
    else:
        for art in articles[:8]:
            st.markdown(f"""
            <div style="padding:8px 4px;display:flex;justify-content:space-between;gap:12px;">
                <a href="{art.get('link','#')}" target="_blank" style="font-size:13px;color:{T['ivory']};
                   text-decoration:none;flex:1;line-height:1.4;">{art.get('title','')}</a>
                <span style="font-family:{T['mono']};font-size:10px;color:{T['t3']};white-space:nowrap;padding-top:2px;">{art.get('time_str','')}</span>
            </div>""", unsafe_allow_html=True)
