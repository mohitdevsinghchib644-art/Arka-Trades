"""
Arka Trades — F7 Market Breadth page.

Source: Chartink dashboard 86550.

Important performance design:
- No network request on page open.
- No network request when changing lookback.
- Chartink is queried only from the explicit SYNC CHARTINK button.
- Historical rows are read from local cache.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from breadth_engine import load_history, sync_chartink


# ---------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------

def _css() -> None:
    st.markdown(
        """
        <style>
        .stApp{background:#050505;color:#d7dbe0;}
        .block-container{padding:8px 8px 28px 8px;max-width:100%;}
        section[data-testid="stSidebar"]{display:none;}

        .bt-top{height:36px;border-top:1px solid #24282d;border-bottom:1px solid #24282d;
                display:flex;align-items:center;justify-content:space-between;padding:0 10px;
                margin-bottom:12px;font-family:monospace;}
        .bt-brand{font-size:12px;font-weight:700;letter-spacing:.5px;color:#e7ebef;}
        .bt-module{font-size:11px;font-weight:700;letter-spacing:1px;color:#e7a51a;}
        .bt-status{font-size:9px;letter-spacing:.7px;color:#4c8758;}

        .bt-title{font:700 18px monospace;letter-spacing:.5px;color:#e5e8eb;margin:4px 0 2px;}
        .bt-sub{font:10px monospace;letter-spacing:.45px;color:#6f7882;margin-bottom:12px;}
        .bt-info{border-top:1px solid #24292e;border-bottom:1px solid #24292e;
                 padding:7px 9px;font:9px monospace;color:#717a84;letter-spacing:.3px;}

        .bt-card{background:#090a0c;border:1px solid #252a30;padding:10px 12px;min-height:72px;}
        .bt-label{font:9px monospace;letter-spacing:.7px;color:#737c86;}
        .bt-value{font:700 22px monospace;color:#e9ecef;margin-top:5px;}
        .bt-meta{font:9px monospace;color:#68717b;margin-top:5px;}
        .bt-up{color:#55a96a;}
        .bt-down{color:#d35b5b;}
        .bt-flat{color:#9aa1a9;}

        .bt-table-wrap{overflow-x:auto;border:1px solid #252a30;background:#08090b;}
        .bt-table{border-collapse:collapse;width:100%;font-family:monospace;table-layout:fixed;}
        .bt-table th,.bt-table td{border-right:1px solid #24292e;border-bottom:1px solid #24292e;
                                  padding:7px 8px;white-space:nowrap;font-size:10px;}
        .bt-table thead tr.group th{background:#111318;color:#e4a31c;font-weight:700;text-align:center;
                                    letter-spacing:.4px;font-size:10px;}
        .bt-table thead tr.sub th{background:#0c0e11;color:#737c86;font-weight:700;text-align:right;font-size:9px;}
        .bt-table thead tr.sub th.date{text-align:left;}
        .bt-table tbody td{color:#d7dbe0;text-align:right;}
        .bt-table tbody td.date{text-align:left;color:#d7dbe0;font-weight:600;}
        .bt-table tbody tr:nth-child(even){background:#0a0b0e;}
        .bt-table tbody tr:hover{background:#101318;}
        .bt-delta-up{color:#55a96a;}
        .bt-delta-down{color:#d35b5b;}
        .bt-delta-flat{color:#858d96;}

        .bt-warn{border:1px solid #544511;background:#0d0b06;color:#d8b52d;
                 padding:10px;font:10px monospace;margin:10px 0;}
        .bt-error{border:1px solid #642f2f;background:#150909;color:#e36a6a;
                  padding:10px;font:10px monospace;margin:10px 0;white-space:pre-wrap;}

        .stButton>button{border:1px solid #353b42;background:#0b0d10;color:#d4d8dc;
                         border-radius:1px;font:10px monospace;min-height:32px;}
        .stButton>button:hover{border-color:#e7a51a;color:#e7a51a;background:#0d0f11;}
        div[data-baseweb="select"]>div{background:#090a0c;border-color:#30353b;border-radius:1px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# FORMATTING
# ---------------------------------------------------------------------

def _fmt(v) -> str:
    try:
        if pd.isna(v):
            return "-"
        return f"{int(round(float(v))):,}"
    except Exception:
        return "-"


def _delta(v) -> str:
    try:
        if pd.isna(v):
            return "-"
        n = int(round(float(v)))
        if n > 0:
            return f"+{n:,}"
        return f"{n:,}"
    except Exception:
        return "-"


def _delta_class(v) -> str:
    try:
        if pd.isna(v):
            return "bt-delta-flat"
        n = float(v)
        if n > 0:
            return "bt-delta-up"
        if n < 0:
            return "bt-delta-down"
        return "bt-delta-flat"
    except Exception:
        return "bt-delta-flat"


# ---------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------

def _render_summary(history: pd.DataFrame) -> None:
    if history.empty:
        st.markdown(
            '<div class="bt-warn">NO CHARTINK BREADTH HISTORY STORED. USE SYNC CHARTINK.</div>',
            unsafe_allow_html=True,
        )
        return

    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) >= 2 else None

    cols = st.columns(4)

    for col, ma in zip(cols[:3], (20, 50, 200)):
        above = latest.get(f"above_{ma}dma")
        below = latest.get(f"below_{ma}dma")

        total = 0
        try:
            total = int(above) + int(below)
        except Exception:
            pass

        pct = (float(above) / total * 100) if total and pd.notna(above) else 0.0

        if previous is not None:
            try:
                d = int(above) - int(previous.get(f"above_{ma}dma"))
            except Exception:
                d = None
        else:
            d = None

        dtext = "-" if d is None else _delta(d)
        dclass = "bt-delta-flat" if d is None else _delta_class(d)

        with col:
            st.markdown(
                f"""
                <div class="bt-card">
                    <div class="bt-label">ABOVE {ma} DMA</div>
                    <div class="bt-value">{_fmt(above)}</div>
                    <div class="bt-meta">
                        {pct:.1f}% OF {total:,} · <span class="{dclass}">DELTA {dtext}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with cols[3]:
        date_text = pd.Timestamp(latest["date"]).strftime("%d %b %Y")
        st.markdown(
            f"""
            <div class="bt-card">
                <div class="bt-label">LATEST CHARTINK SESSION</div>
                <div class="bt-value">{date_text}</div>
                <div class="bt-meta">ROWS STORED · {len(history):,}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------
# GROUPED TABLE
# ---------------------------------------------------------------------

def _render_grouped_table(history: pd.DataFrame, lookback: int) -> None:
    if history.empty:
        return

    df = history.sort_values("date").tail(lookback).copy()

    for ma in (20, 50, 200):
        df[f"above_{ma}_delta"] = df[f"above_{ma}dma"].diff()
        df[f"below_{ma}_delta"] = df[f"below_{ma}dma"].diff()

    df = df.sort_values("date", ascending=False)

    body = []

    for _, r in df.iterrows():
        date_text = pd.Timestamp(r["date"]).strftime("%d %b %Y")
        cells = [f'<td class="date">{date_text}</td>']

        for ma in (20, 50, 200):
            cells.append(f'<td>{_fmt(r[f"above_{ma}dma"])}</td>')
            cells.append(
                f'<td><span class="{_delta_class(r[f"above_{ma}_delta"])}">'
                f'{_delta(r[f"above_{ma}_delta"])}</span></td>'
            )
            cells.append(f'<td>{_fmt(r[f"below_{ma}dma"])}</td>')
            cells.append(
                f'<td><span class="{_delta_class(r[f"below_{ma}_delta"])}">'
                f'{_delta(r[f"below_{ma}_delta"])}</span></td>'
            )

        body.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        f"""
        <div class="bt-table-wrap">
            <table class="bt-table">
                <thead>
                    <tr class="group">
                        <th rowspan="2">DATE</th>
                        <th colspan="2">ABOVE 20DMA</th>
                        <th colspan="2">BELOW 20DMA</th>
                        <th colspan="2">ABOVE 50DMA</th>
                        <th colspan="2">BELOW 50DMA</th>
                        <th colspan="2">ABOVE 200DMA</th>
                        <th colspan="2">BELOW 200DMA</th>
                    </tr>
                    <tr class="sub">
                        <th>COUNT</th><th>DELTA</th>
                        <th>COUNT</th><th>DELTA</th>
                        <th>COUNT</th><th>DELTA</th>
                        <th>COUNT</th><th>DELTA</th>
                        <th>COUNT</th><th>DELTA</th>
                        <th>COUNT</th><th>DELTA</th>
                    </tr>
                </thead>
                <tbody>{''.join(body)}</tbody>
            </table>
        </div>
        <div class="bt-info" style="margin-top:8px;">
            DELTA = CHANGE IN STOCK COUNT VS PREVIOUS CHARTINK BREADTH SESSION.
            DATA SOURCE = CHARTINK DASHBOARD 86550.
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------

def render_market_breadth() -> None:
    _css()

    st.markdown(
        """
        <div class="bt-top">
            <div class="bt-brand">ARKA TRADES</div>
            <div class="bt-module">F7 MARKET BREADTH</div>
            <div class="bt-status">CHARTINK · DAILY EOD</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="bt-title">MARKET BREADTH</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="bt-sub">DAILY MARKET PARTICIPATION · SOURCE: CHARTINK DASHBOARD 86550 · 20 / 50 / 200 DMA</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns([1.2, 1.0, 1.0, 3.5])

    with c1:
        sync_btn = st.button(
            "SYNC CHARTINK",
            use_container_width=True,
        )

    with c2:
        clear_btn = st.button(
            "CLEAR LOCAL",
            use_container_width=True,
        )

    with c3:
        lookback = st.selectbox(
            "LOOKBACK",
            [20, 50, 90, 180],
            index=0,
            label_visibility="collapsed",
        )

    with c4:
        st.markdown(
            '<div class="bt-info">NO NETWORK REQUEST ON PAGE OPEN OR LOOKBACK CHANGE. SYNC IS THE ONLY CHARTINK REQUEST.</div>',
            unsafe_allow_html=True,
        )

    if clear_btn:
        from breadth_engine import HISTORY_FILE
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        st.rerun()

    if sync_btn:
        with st.spinner("Syncing Market Breadth from Chartink..."):
            try:
                result = sync_chartink(force=True)
                st.success(
                    f"Chartink synced · {result['rows']} source rows · "
                    f"latest {result['latest_date']}"
                )
            except Exception as exc:
                st.markdown(
                    f'<div class="bt-error">CHARTINK SYNC FAILED\n\n{exc}</div>',
                    unsafe_allow_html=True,
                )
                return

    history = load_history()

    if history.empty:
        _render_summary(history)
        st.markdown(
            '<div class="bt-warn">THE LOCAL DATABASE IS EMPTY. PRESS SYNC CHARTINK ONCE TO IMPORT THE DAILY SERIES FROM DASHBOARD 86550.</div>',
            unsafe_allow_html=True,
        )
        return

    _render_summary(history)

    st.markdown(
        '<div class="bt-title" style="font-size:12px;margin-top:18px;">DAILY MA BREADTH HISTORY</div>',
        unsafe_allow_html=True,
    )

    _render_grouped_table(history, lookback)

    st.markdown(
        '<div class="bt-title" style="font-size:12px;margin-top:18px;">PARTICIPATION TREND</div>',
        unsafe_allow_html=True,
    )

    chart = history.sort_values("date").tail(lookback).copy()
    chart = chart.set_index("date")

    for ma in (20, 50, 200):
        above = pd.to_numeric(chart[f"above_{ma}dma"], errors="coerce")
        below = pd.to_numeric(chart[f"below_{ma}dma"], errors="coerce")
        denom = (above + below).replace(0, pd.NA)
        chart[f"Above {ma}DMA %"] = above / denom * 100

    st.line_chart(
        chart[[
            "Above 20DMA %",
            "Above 50DMA %",
            "Above 200DMA %",
        ]],
        height=280,
        use_container_width=True,
    )

    source = history.iloc[-1].get("source", "Chartink dashboard 86550")
    st.markdown(
        f'<div class="bt-info">SOURCE · {source} · DAILY / EOD · LOCAL HISTORY {len(history):,} ROWS</div>',
        unsafe_allow_html=True,
    )
