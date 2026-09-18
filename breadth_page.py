"""
Arka Trades — F7 Market Breadth UI

DAILY / EOD ONLY.

Performance rule:
The page must render from local history immediately. It must NOT perform
network requests just because the user opens F7 or changes the history
lookback selector.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from breadth_engine import (
    get_nse_universe,
    get_latest_history_snapshot,
    load_history,
    update_today,
    append_history,
    backfill_history_from_bhavcopy,
    compute_ad_line_and_mcclellan,
)


# ---------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------

def _css():
    st.markdown(
        """
        <style>
        .stApp { background:#050505; color:#d8dce1; }
        .block-container { padding-top:.55rem; padding-left:.55rem; padding-right:.55rem; max-width:100%; }
        section[data-testid="stSidebar"] { display:none; }
        .breadth-top {
            height:38px; border-top:1px solid #24282d; border-bottom:1px solid #24282d;
            display:flex; align-items:center; justify-content:space-between;
            padding:0 10px; font-family:monospace; margin-bottom:10px;
        }
        .brand { font-size:12px; font-weight:700; color:#e8ebee; }
        .module { font-size:11px; font-weight:700; letter-spacing:1px; color:#e7a51a; }
        .status { font-size:9px; letter-spacing:.8px; color:#4e8b59; }
        .title { font:700 18px monospace; letter-spacing:.4px; color:#e6e9ec; margin:6px 0 2px; }
        .subtitle { font:10px monospace; letter-spacing:.45px; color:#727b85; margin-bottom:12px; }
        .panel-title {
            font:700 10px monospace; letter-spacing:1px; color:#bfc4ca;
            border-bottom:1px solid #24292e; padding-bottom:7px; margin:16px 0 8px;
        }
        .metric {
            background:#090a0c; border:1px solid #252a30; padding:10px 12px; min-height:74px;
        }
        .metric-label { font:9px monospace; letter-spacing:.7px; color:#737c86; }
        .metric-value { font:700 22px monospace; color:#e9ecef; margin-top:5px; }
        .metric-sub { font:9px monospace; color:#68717b; margin-top:5px; }
        .info {
            border-top:1px solid #22272c; border-bottom:1px solid #22272c;
            padding:7px 9px; font:9px monospace; color:#6f7882; margin:8px 0 10px;
        }
        .warn {
            border:1px solid #51451f; background:#0d0c08; color:#c5aa54;
            padding:10px 12px; font:10px monospace; margin:8px 0;
        }
        .good { color:#55a96a; }
        .bad { color:#d35b5b; }
        .flat { color:#9aa1a9; }
        .stButton > button {
            border:1px solid #353b42; background:#0b0d10; color:#d4d8dc;
            border-radius:1px; font:10px monospace; min-height:32px;
        }
        .stButton > button:hover { border-color:#e7a51a; color:#e7a51a; }
        div[data-baseweb="select"] > div { background:#090a0c; border-color:#30353b; border-radius:1px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# FORMAT HELPERS
# ---------------------------------------------------------------------

def _signed_int(value) -> str:
    if pd.isna(value):
        return "—"
    v = int(round(float(value)))
    return f"+{v}" if v > 0 else str(v)


def _signed_pp(value) -> str:
    if pd.isna(value):
        return "—"
    v = float(value)
    return f"+{v:.1f}" if v > 0 else f"{v:.1f}"


def _delta_class(value) -> str:
    if pd.isna(value):
        return "flat"
    if float(value) > 0:
        return "good"
    if float(value) < 0:
        return "bad"
    return "flat"


# ---------------------------------------------------------------------
# CURRENT METRICS
# ---------------------------------------------------------------------

def _render_current_cards(history: pd.DataFrame):
    if history.empty:
        st.markdown(
            '<div class="warn">NO DAILY BREADTH HISTORY YET · USE BACKFILL 20D TO INITIALISE THE SERIES</div>',
            unsafe_allow_html=True,
        )
        return

    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) >= 2 else None

    vals = []
    for ma in (20, 50, 200):
        col = f"above_{ma}dma"
        pct_col = f"above_{ma}dma_pct"
        now = float(latest[col])
        pct = float(latest[pct_col]) if pd.notna(latest[pct_col]) else 0.0
        delta = now - float(previous[col]) if previous is not None else None
        vals.append((ma, now, pct, delta))

    cols = st.columns(4)

    for i, (ma, count, pct, delta) in enumerate(vals):
        with cols[i]:
            delta_text = _signed_int(delta) if delta is not None else "—"
            delta_class = _delta_class(delta) if delta is not None else "flat"
            st.markdown(
                f"""
                <div class="metric">
                    <div class="metric-label">STOCKS ABOVE {ma} DMA</div>
                    <div class="metric-value">{int(count)}</div>
                    <div class="metric-sub">{pct:.1f}% · <span class="{delta_class}">Δ {delta_text} stocks</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with cols[3]:
        universe = max(
            int(latest.get("above_200dma_denom", 0) or 0),
            int(latest.get("above_50dma_denom", 0) or 0),
            int(latest.get("above_20dma_denom", 0) or 0),
        )
        st.markdown(
            f"""
            <div class="metric">
                <div class="metric-label">ELIGIBLE NSE STOCKS</div>
                <div class="metric-value">{universe}</div>
                <div class="metric-sub">LAST SESSION · {pd.Timestamp(latest['date']).strftime('%d %b %Y')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------
# HISTORY TABLE
# ---------------------------------------------------------------------

def _render_history_table(history: pd.DataFrame, lookback: int):
    if history.empty:
        return

    df = history.sort_values("date", ascending=False).head(lookback).copy()

    # Counts / percentages are already in history. Changes are calculated
    # from the full ascending series BEFORE truncation so the first visible
    # row has the correct previous-session delta.
    full = history.sort_values("date").copy()
    for ma in (20, 50, 200):
        col = f"above_{ma}dma"
        pct = f"above_{ma}dma_pct"
        full[f"{ma}d_count_delta"] = full[col].diff()
        full[f"{ma}d_pp_delta"] = full[pct].diff()

    df = full.tail(lookback).sort_values("date", ascending=False).copy()

    out = pd.DataFrame({
        "DATE": df["date"].dt.strftime("%d %b %Y"),
        ">20D": df["above_20dma"].astype("Int64"),
        "Δ20D": df["20d_count_delta"].map(_signed_int),
        "20D %": df["above_20dma_pct"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "—"),
        "Δ20D pp": df["20d_pp_delta"].map(_signed_pp),
        ">50D": df["above_50dma"].astype("Int64"),
        "Δ50D": df["50d_count_delta"].map(_signed_int),
        "50D %": df["above_50dma_pct"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "—"),
        "Δ50D pp": df["50d_pp_delta"].map(_signed_pp),
        ">200D": df["above_200dma"].astype("Int64"),
        "Δ200D": df["200d_count_delta"].map(_signed_int),
        "200D %": df["above_200dma_pct"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "—"),
        "Δ200D pp": df["200d_pp_delta"].map(_signed_pp),
    })

    st.dataframe(
        out,
        use_container_width=True,
        hide_index=True,
        height=min(620, 43 + len(out) * 34),
    )


# ---------------------------------------------------------------------
# MAIN RENDER FUNCTION — REQUIRED BY app.py
# ---------------------------------------------------------------------

def render_market_breadth():
    """
    Main F7 entry point.

    Deliberately NO network call here. This function must be cheap enough to
    render immediately when the user taps F7, and changing the lookback must
    only filter already-loaded history.
    """

    _css()

    st.markdown(
        """
        <div class="breadth-top">
            <div class="brand">▲ ARKA TRADES</div>
            <div class="module">F7&nbsp;&nbsp; MARKET BREADTH</div>
            <div class="status">● DAILY EOD · NSE EQUITIES</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="title">MARKET BREADTH</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">DAILY MARKET PARTICIPATION · 20 / 50 / 200 DMA · COMPLETED NSE TRADING SESSIONS</div>',
        unsafe_allow_html=True,
    )

    # IMPORTANT: this is only a local file read, so F7 opens immediately.
    history = load_history()

    # Controls. Lookback selector NEVER triggers market-data download.
    c1, c2, c3, c4 = st.columns([1.05, 1.15, 1.0, 3.4])

    with c1:
        update_btn = st.button(
            "UPDATE TODAY",
            use_container_width=True,
        )

    with c2:
        backfill_btn = st.button(
            "BACKFILL 20D",
            use_container_width=True,
        )

    with c3:
        lookback = st.selectbox(
            "LOOKBACK",
            [20, 50, 90, 180],
            index=1,
            label_visibility="collapsed",
        )

    with c4:
        st.markdown(
            '<div class="info" style="margin-top:0;">DAILY ONLY · NO INTRADAY REFRESH · Δ = CHANGE VS PREVIOUS TRADING SESSION · SELECTING LOOKBACK DOES NOT FETCH DATA</div>',
            unsafe_allow_html=True,
        )

    # -----------------------------------------------------------------
    # EXPLICIT UPDATE
    # -----------------------------------------------------------------
    if update_btn:
        with st.spinner("Updating completed NSE session…"):
            try:
                symbols, source = get_nse_universe()
                result = update_today(symbols)
            except Exception as exc:
                result = {"error": str(exc)}
                source = "unavailable"

        if result.get("error"):
            st.error(result["error"])
        else:
            st.success(
                f"Updated {result.get('date', 'latest session')} · {result.get('source', source)}"
            )
        st.rerun()

    # -----------------------------------------------------------------
    # EXPLICIT BACKFILL
    # -----------------------------------------------------------------
    if backfill_btn:
        with st.spinner("Building 20 trading sessions of daily breadth…"):
            try:
                symbols, source = get_nse_universe()
                result = backfill_history_from_bhavcopy(
                    symbols,
                    days=20,
                )
            except Exception as exc:
                result = {
                    "days_written": 0,
                    "error": str(exc),
                }
                source = "unavailable"

        if result.get("days_written", 0) > 0:
            st.success(
                f"Built {result['days_written']} sessions "
                f"({result['date_range'][0]} → {result['date_range'][1]})."
            )
        else:
            st.error(
                result.get(
                    "error",
                    "Backfill failed.",
                )
            )
        st.rerun()

    # -----------------------------------------------------------------
    # REFRESH LOCAL HISTORY ONLY
    # -----------------------------------------------------------------
    history = load_history()

    _render_current_cards(history)

    if history.empty:
        st.markdown(
            '<div class="warn">THE UI IS READY. NO NETWORK REQUEST WAS MADE ON PAGE OPEN. PRESS BACKFILL 20D ONCE TO BUILD THE FIRST DAILY SERIES.</div>',
            unsafe_allow_html=True,
        )
        return

    latest_date = history.iloc[-1]["date"]
    last_dt = pd.Timestamp(latest_date).strftime("%d %b %Y")

    st.markdown(
        f'<div class="info">LAST STORED SESSION · {last_dt} · HISTORY ROWS · {len(history)} · DAILY / EOD</div>',
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------
    # DAILY TABLE
    # -----------------------------------------------------------------
    st.markdown(
        '<div class="panel-title">DAILY MA BREADTH HISTORY</div>',
        unsafe_allow_html=True,
    )
    _render_history_table(history, lookback)

    # -----------------------------------------------------------------
    # TREND CHART — LOCAL DATA ONLY
    # -----------------------------------------------------------------
    st.markdown(
        '<div class="panel-title">MA PARTICIPATION TREND</div>',
        unsafe_allow_html=True,
    )

    chart = history.sort_values("date").tail(lookback).set_index("date")
    chart = chart[
        [
            "above_20dma_pct",
            "above_50dma_pct",
            "above_200dma_pct",
        ]
    ].copy()
    chart.columns = [
        "Above 20 DMA %",
        "Above 50 DMA %",
        "Above 200 DMA %",
    ]

    st.line_chart(
        chart,
        height=290,
        use_container_width=True,
    )

    # -----------------------------------------------------------------
    # MARKET INTERNALS
    # -----------------------------------------------------------------
    st.markdown(
        '<div class="panel-title">MARKET INTERNALS</div>',
        unsafe_allow_html=True,
    )

    internals = compute_ad_line_and_mcclellan(
        history.sort_values("date").tail(lookback)
    )
    latest = internals.iloc[-1]

    i1, i2, i3, i4 = st.columns(4)
    cards = [
        ("ADVANCES", int(latest["advances"])),
        ("DECLINES", int(latest["declines"])),
        (
            "NET ADVANCES",
            int(latest["advances"] - latest["declines"]),
        ),
        (
            "MCCLELLAN",
            f"{float(latest['mcclellan']):.1f}",
        ),
    ]

    for col, (label, value) in zip([i1, i2, i3, i4], cards):
        with col:
            st.markdown(
                f"""
                <div class="metric">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # -----------------------------------------------------------------
    # FOOTER
    # -----------------------------------------------------------------
    st.markdown(
        '<div class="info">DATA MODEL · DAILY CLOSES · SIMPLE MOVING AVERAGE · ONE OBSERVATION PER COMPLETED NSE SESSION · LOOKBACK IS A DISPLAY FILTER ONLY</div>',
        unsafe_allow_html=True,
    )
