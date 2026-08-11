"""
research_page.py — Arka Trades Research Terminal
Stock lookup: search a symbol, get summary + quarterly/yearly
results + shareholding + sector classification from Screener.in.

VISUAL LANGUAGE (terminal reskin, matches app.py's TERM_* tokens):
  - Flat panels, 1-2px hairline borders, no rounded-corner cards
  - Dense padding, tight line-height
  - Single accent color (amber) + green/red for price/delta only
  - Monospace for ALL data values and table cells, not just prices
  - Data as row-based tables with alternating shading, not card grids

Peer comparison and sector P/E are NOT implemented — see
screener_scraper.py docstring for why. This page shows a clearly
labeled placeholder for both rather than hiding them or faking data.
"""

import streamlit as st
from datetime import datetime, timezone, timedelta

from screener_scraper import get_full_research

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


def render_research_page(T: dict, news_fetch_fn=None):
    """
    T: dict of terminal design tokens from app.py (colors, fonts) so
    this page stays visually consistent with the rest of the reskin
    without duplicating the token definitions in two files.
    news_fetch_fn: optional callable(symbol) -> list[article dicts],
    reusing app.py's existing news_feed.py fetcher rather than
    building a second news path. If None, news section is skipped.
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
            Enter an NSE symbol above to pull fundamentals, results, and shareholding data.</div>""",
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

    # ── Sector classification ────────────────────────────────────
    sector = data["sector"]
    sfields2 = sector.get("data") or {}
    st.markdown(f"""<div style="margin-top:18px;display:flex;align-items:center;gap:10px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">SECTOR</div>
        {_status_tag(sector['status'], sector.get('age_seconds'), T)}</div>""", unsafe_allow_html=True)
    if sfields2:
        chain = " → ".join(
            sfields2.get(k, "") for k in ("Broad Sector", "Sector", "Broad Industry", "Industry") if sfields2.get(k)
        )
        st.markdown(f'<div style="font-size:12px;color:{T["ivory"]};margin-top:4px;">{chain}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="font-size:11px;color:{T["t3"]};margin-top:4px;">Classification unavailable.</div>', unsafe_allow_html=True)

    # ── Quarterly Results ────────────────────────────────────────
    q = data["quarterly"]
    st.markdown(f"""<div style="margin-top:22px;display:flex;align-items:center;gap:10px;">
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

    # ── Peer Comparison — explicitly unavailable ──────────────────
    st.markdown(f"""<div style="margin-top:22px;display:flex;align-items:center;gap:10px;">
        <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">PEER COMPARISON</div>
        {_status_tag('not_implemented', None, T)}</div>
    <div style="border:1px dashed {T['border']};padding:14px 16px;margin-top:6px;">
        <div style="font-size:11px;color:{T['t3']};line-height:1.7;">
            Not available yet — Screener loads this table via a background request after
            the page loads, which a direct page fetch can't see. Sector P/E as a standalone
            figure is also not published on this page. Both need a different fetch method
            than the rest of this page uses.</div>
    </div>""", unsafe_allow_html=True)

    # ── News (reuses existing news_feed.py fetcher if provided) ───
    if news_fetch_fn:
        st.markdown(f"""<div style="margin-top:22px;display:flex;align-items:center;gap:10px;">
            <div style="font-family:{T['mono']};font-size:11px;font-weight:700;color:{T['t2']};letter-spacing:1px;">RECENT NEWS</div>
        </div>""", unsafe_allow_html=True)
        try:
            articles = news_fetch_fn(data["symbol"])
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
        Source: Screener.in (unauthenticated) · Data provided by C-MOTS Internet Technologies ·
        Fetched via {data['url']}</div>""", unsafe_allow_html=True)
