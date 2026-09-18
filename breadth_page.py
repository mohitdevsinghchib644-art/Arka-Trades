# breadth_page.py
# Arka Trades — F7 Market Breadth
# DAILY / EOD MARKET BREADTH
#
# Requires:
#   breadth_engine.py
#
# Main data:
#   NSE equities
#   20 DMA / 50 DMA / 200 DMA
#   Daily historical participation
#
# IMPORTANT:
# This page is DAILY only. It does not run intraday refresh logic.

import streamlit as st
import pandas as pd

from breadth_engine import (
    get_nse_universe,
    compute_breadth_snapshot,
    append_history,
    load_history,
    backfill_history_from_bhavcopy,
    compute_ad_line_and_mcclellan,
)


# ═════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Arka Trades — Market Breadth",
    page_icon="",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ═════════════════════════════════════════════════════════════════════
# TERMINAL CSS
# ═════════════════════════════════════════════════════════════════════

st.markdown(
    """
    <style>

    /* ─────────────────────────────────────────────
       GLOBAL
    ───────────────────────────────────────────── */

    .stApp {
        background:#050505;
        color:#d7dbe0;
    }

    .block-container {
        padding-top:0.65rem;
        padding-left:0.55rem;
        padding-right:0.55rem;
        padding-bottom:2rem;
        max-width:100%;
    }

    header[data-testid="stHeader"] {
        background:#050505;
    }

    section[data-testid="stSidebar"] {
        display:none;
    }

    /* ─────────────────────────────────────────────
       TOP TERMINAL BAR
    ───────────────────────────────────────────── */

    .terminal-top {
        height:38px;
        border-top:1px solid #24282d;
        border-bottom:1px solid #24282d;
        display:flex;
        align-items:center;
        justify-content:space-between;
        padding:0 10px;
        margin-bottom:8px;
        font-family:monospace;
    }

    .terminal-brand {
        font-size:13px;
        font-weight:700;
        letter-spacing:.4px;
        color:#e7ebef;
    }

    .terminal-module {
        font-size:11px;
        letter-spacing:1px;
        color:#e7a51a;
        font-weight:700;
    }

    .terminal-status {
        font-size:9px;
        letter-spacing:1px;
        color:#4d8b58;
    }

    /* ─────────────────────────────────────────────
       SECTION TITLES
    ───────────────────────────────────────────── */

    .section-title {
        font-family:monospace;
        font-size:18px;
        font-weight:700;
        letter-spacing:.5px;
        color:#e5e8eb;
        margin-top:5px;
        margin-bottom:2px;
    }

    .section-subtitle {
        font-family:monospace;
        font-size:10px;
        letter-spacing:.5px;
        color:#6f7882;
        margin-bottom:12px;
    }

    .panel-title {
        font-family:monospace;
        font-size:11px;
        font-weight:700;
        letter-spacing:.7px;
        color:#c9cdd2;
        padding-bottom:7px;
        border-bottom:1px solid #252a2f;
        margin-bottom:8px;
    }

    /* ─────────────────────────────────────────────
       METRIC BLOCKS
    ───────────────────────────────────────────── */

    .breadth-card {
        background:#090a0c;
        border:1px solid #252a30;
        padding:11px 12px;
        min-height:83px;
    }

    .breadth-label {
        font-family:monospace;
        font-size:9px;
        letter-spacing:.8px;
        color:#737c86;
        margin-bottom:6px;
    }

    .breadth-value {
        font-family:monospace;
        font-size:23px;
        line-height:1;
        font-weight:700;
        color:#e8ebee;
    }

    .breadth-meta {
        font-family:monospace;
        font-size:9px;
        color:#69727c;
        margin-top:8px;
    }

    .positive {
        color:#55a96a !important;
    }

    .negative {
        color:#d55b5b !important;
    }

    .neutral {
        color:#9aa2aa !important;
    }

    /* ─────────────────────────────────────────────
       INFO STRIP
    ───────────────────────────────────────────── */

    .info-strip {
        border-top:1px solid #22272c;
        border-bottom:1px solid #22272c;
        padding:7px 9px;
        margin:9px 0 12px 0;
        font-family:monospace;
        font-size:9px;
        color:#6e7781;
        letter-spacing:.25px;
    }

    /* ─────────────────────────────────────────────
       TABLE
    ───────────────────────────────────────────── */

    div[data-testid="stDataFrame"] {
        border:1px solid #24292e;
    }

    /* ─────────────────────────────────────────────
       BUTTONS
    ───────────────────────────────────────────── */

    .stButton > button {
        border:1px solid #353b42;
        background:#0b0d10;
        color:#d4d8dc;
        border-radius:1px;
        font-family:monospace;
        font-size:10px;
        min-height:31px;
    }

    .stButton > button:hover {
        border-color:#e7a51a;
        color:#e7a51a;
        background:#0d0e10;
    }

    /* ─────────────────────────────────────────────
       SELECTBOX
    ───────────────────────────────────────────── */

    div[data-baseweb="select"] > div {
        background:#090a0c;
        border-color:#30353b;
        border-radius:1px;
        min-height:32px;
    }

    /* ─────────────────────────────────────────────
       ALERTS
    ───────────────────────────────────────────── */

    div[data-testid="stAlert"] {
        border-radius:1px;
        font-family:monospace;
        font-size:10px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ═════════════════════════════════════════════════════════════════════
# TOP BAR
# ═════════════════════════════════════════════════════════════════════

st.markdown(
    """
    <div class="terminal-top">
        <div class="terminal-brand">▲ ARKA TRADES</div>
        <div class="terminal-module">F7&nbsp;&nbsp; MARKET BREADTH</div>
        <div class="terminal-status">● EOD MARKET DATA · NSE EQUITIES</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ═════════════════════════════════════════════════════════════════════
# HEADER
# ═════════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="section-title">MARKET BREADTH</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="section-subtitle">'
    'DAILY MARKET PARTICIPATION · NSE EQUITIES · MOVING-AVERAGE BREADTH'
    '</div>',
    unsafe_allow_html=True,
)


# ═════════════════════════════════════════════════════════════════════
# CONTROLS
# ═════════════════════════════════════════════════════════════════════

c1, c2, c3, c4 = st.columns(
    [1.0, 1.0, 1.0, 3.5]
)

with c1:
    refresh = st.button(
        "REFRESH",
        use_container_width=True,
    )

with c2:
    backfill = st.button(
        "BACKFILL 20D",
        use_container_width=True,
    )

with c3:
    history_days = st.selectbox(
        "HISTORY",
        [20, 50, 90, 180],
        index=1,
        label_visibility="collapsed",
    )

with c4:
    st.markdown(
        """
        <div class="info-strip" style="margin-top:0;">
        DAILY TIMEFRAME · ONE OBSERVATION PER COMPLETED NSE SESSION
        · Δ = CHANGE VS PREVIOUS TRADING SESSION
        </div>
        """,
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════════════
# UNIVERSE
# ═════════════════════════════════════════════════════════════════════

try:

    symbols, universe_source = get_nse_universe()

except Exception as e:

    st.error(
        f"Universe error: {e}"
    )

    symbols = []

    universe_source = "unavailable"


if not symbols:

    st.error(
        "No NSE universe available."
    )

    st.stop()


# ═════════════════════════════════════════════════════════════════════
# BACKFILL
# ═════════════════════════════════════════════════════════════════════

if backfill:

    with st.spinner(
        "Backfilling daily breadth history..."
    ):

        result = backfill_history_from_bhavcopy(
            symbols,
            days=20,
        )

    if result.get("days_written", 0) > 0:

        st.success(
            f"Backfilled {result['days_written']} "
            f"trading sessions."
        )

    else:

        st.warning(
            result.get(
                "error",
                "Backfill did not produce any sessions."
            )
        )


# ═════════════════════════════════════════════════════════════════════
# CURRENT SNAPSHOT
# ═════════════════════════════════════════════════════════════════════

with st.spinner(
    "Loading daily market breadth..."
):

    try:

        snapshot = compute_breadth_snapshot(
            symbols
        )

    except Exception as e:

        snapshot = {
            "error": str(e)
        }


if snapshot.get("error"):

    st.error(
        snapshot["error"]
    )

    st.stop()


# ═════════════════════════════════════════════════════════════════════
# SAVE CURRENT SESSION
# ═════════════════════════════════════════════════════════════════════

try:

    append_history(
        snapshot
    )

except Exception:
    pass


# ═════════════════════════════════════════════════════════════════════
# LOAD HISTORY
# ═════════════════════════════════════════════════════════════════════

history = load_history()

if history.empty:

    st.warning(
        "No historical breadth records available yet. "
        "Use BACKFILL 20D to build the daily history."
    )


# ═════════════════════════════════════════════════════════════════════
# MA PARTICIPATION — CURRENT
# ═════════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="panel-title">'
    'CURRENT MA PARTICIPATION'
    '</div>',
    unsafe_allow_html=True,
)


def _pct(
    value,
    denominator
):
    try:

        if denominator <= 0:
            return 0.0

        return (
            float(value)
            / float(denominator)
            * 100
        )

    except Exception:

        return 0.0


p20 = _pct(
    snapshot["above_20dma"],
    snapshot["above_20dma_denom"],
)

p50 = _pct(
    snapshot["above_50dma"],
    snapshot["above_50dma_denom"],
)

p200 = _pct(
    snapshot["above_200dma"],
    snapshot["above_200dma_denom"],
)


m1, m2, m3, m4 = st.columns(4)


with m1:

    st.markdown(
        f"""
        <div class="breadth-card">
            <div class="breadth-label">ABOVE 20 DMA</div>
            <div class="breadth-value">{snapshot["above_20dma"]}</div>
            <div class="breadth-meta">
                {p20:.1f}% OF ELIGIBLE STOCKS
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


with m2:

    st.markdown(
        f"""
        <div class="breadth-card">
            <div class="breadth-label">ABOVE 50 DMA</div>
            <div class="breadth-value">{snapshot["above_50dma"]}</div>
            <div class="breadth-meta">
                {p50:.1f}% OF ELIGIBLE STOCKS
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


with m3:

    st.markdown(
        f"""
        <div class="breadth-card">
            <div class="breadth-label">ABOVE 200 DMA</div>
            <div class="breadth-value">{snapshot["above_200dma"]}</div>
            <div class="breadth-meta">
                {p200:.1f}% OF ELIGIBLE STOCKS
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


with m4:

    st.markdown(
        f"""
        <div class="breadth-card">
            <div class="breadth-label">NSE UNIVERSE</div>
            <div class="breadth-value">{snapshot["total_stocks"]}</div>
            <div class="breadth-meta">
                {snapshot["date"]} · EOD
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════════════
# DAILY TABLE
# ═════════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="panel-title" style="margin-top:18px;">'
    'DAILY MA BREADTH HISTORY'
    '</div>',
    unsafe_allow_html=True,
)


if not history.empty:

    hist = history.copy()

    hist = (
        hist
        .sort_values("date", ascending=False)
        .head(history_days)
        .copy()
    )

    # ─────────────────────────────────────────────
    # ENSURE PERCENTAGES EXIST
    # ─────────────────────────────────────────────

    for ma in ["20", "50", "200"]:

        pct_col = f"above_{ma}dma_pct"

        above_col = f"above_{ma}dma"

        denom_col = f"above_{ma}dma_denom"

        if pct_col not in hist.columns:

            denom = pd.to_numeric(
                hist[denom_col],
                errors="coerce",
            )

            above = pd.to_numeric(
                hist[above_col],
                errors="coerce",
            )

            hist[pct_col] = (
                above
                / denom.replace(
                    0,
                    pd.NA,
                )
                * 100
            )

        hist[pct_col] = pd.to_numeric(
            hist[pct_col],
            errors="coerce",
        )

        # Change in percentage points.
        hist[f"{ma}d_pp"] = (
            hist[pct_col]
            .diff(-1)
        )

        # Change in actual number of stocks.
        hist[f"{ma}d_count_change"] = (
            pd.to_numeric(
                hist[above_col],
                errors="coerce",
            )
            .diff(-1)
        )


    # ─────────────────────────────────────────────
    # DISPLAY TABLE
    # ─────────────────────────────────────────────

    display = pd.DataFrame()

    display["DATE"] = hist[
        "date"
    ].dt.strftime(
        "%d %b %Y"
    )

    display["20D"] = (
        hist["above_20dma"]
        .astype("Int64")
    )

    display["Δ20D"] = (
        hist["20d_count_change"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else (
                f"+{int(x)}"
                if x > 0
                else str(int(x))
            )
        )
    )

    display["20D %"] = (
        hist["above_20dma_pct"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else f"{x:.1f}%"
        )
    )

    display["Δ20D pp"] = (
        hist["20d_pp"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else (
                f"+{x:.1f}"
                if x > 0
                else f"{x:.1f}"
            )
        )
    )

    display["50D"] = (
        hist["above_50dma"]
        .astype("Int64")
    )

    display["Δ50D"] = (
        hist["50d_count_change"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else (
                f"+{int(x)}"
                if x > 0
                else str(int(x))
            )
        )
    )

    display["50D %"] = (
        hist["above_50dma_pct"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else f"{x:.1f}%"
        )
    )

    display["Δ50D pp"] = (
        hist["50d_pp"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else (
                f"+{x:.1f}"
                if x > 0
                else f"{x:.1f}"
            )
        )
    )

    display["200D"] = (
        hist["above_200dma"]
        .astype("Int64")
    )

    display["Δ200D"] = (
        hist["200d_count_change"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else (
                f"+{int(x)}"
                if x > 0
                else str(int(x))
            )
        )
    )

    display["200D %"] = (
        hist["above_200dma_pct"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else f"{x:.1f}%"
        )
    )

    display["Δ200D pp"] = (
        hist["200d_pp"]
        .apply(
            lambda x:
            "—"
            if pd.isna(x)
            else (
                f"+{x:.1f}"
                if x > 0
                else f"{x:.1f}"
            )
        )
    )


    # ─────────────────────────────────────────────
    # STYLE
    # ─────────────────────────────────────────────

    def _style_table(
        df
    ):

        return (
            df.style
            .format(
                na_rep="—"
            )
            .set_properties(
                **{
                    "font-family":
                        "monospace",

                    "font-size":
                        "11px",

                    "background-color":
                        "#08090b",

                    "color":
                        "#d7dbe0",

                    "border-color":
                        "#24292e",
                }
            )
        )


    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=min(
            680,
            44 + len(display) * 35
        ),
    )


else:

    st.markdown(
        """
        <div class="info-strip">
        NO DAILY HISTORY AVAILABLE.
        USE BACKFILL 20D TO BUILD THE HISTORICAL SERIES.
        </div>
        """,
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════════════
# TREND CHART
# ═════════════════════════════════════════════════════════════════════

if not history.empty:

    st.markdown(
        '<div class="panel-title" style="margin-top:18px;">'
        'MA PARTICIPATION TREND'
        '</div>',
        unsafe_allow_html=True,
    )

    chart_df = history.copy()

    chart_df = (
        chart_df
        .sort_values("date")
        .tail(history_days)
        .set_index("date")
    )

    chart_cols = [
        c for c in [
            "above_20dma_pct",
            "above_50dma_pct",
            "above_200dma_pct",
        ]
        if c in chart_df.columns
    ]

    if chart_cols:

        chart_display = chart_df[
            chart_cols
        ].copy()

        chart_display.columns = [
            c.replace(
                "above_",
                "Above "
            )
            .replace(
                "dma_pct",
                " DMA %"
            )
            for c in chart_display.columns
        ]

        st.line_chart(
            chart_display,
            height=300,
            use_container_width=True,
        )


# ═════════════════════════════════════════════════════════════════════
# ADVANCE / DECLINE
# ═════════════════════════════════════════════════════════════════════

if not history.empty:

    st.markdown(
        '<div class="panel-title" style="margin-top:18px;">'
        'MARKET INTERNALS'
        '</div>',
        unsafe_allow_html=True,
    )

    internal = history.copy()

    internal = (
        internal
        .sort_values("date")
        .tail(history_days)
    )

    internal = compute_ad_line_and_mcclellan(
        internal
    )

    x1, x2, x3, x4 = st.columns(4)

    latest = internal.iloc[-1]

    with x1:

        st.markdown(
            f"""
            <div class="breadth-card">
                <div class="breadth-label">ADVANCES</div>
                <div class="breadth-value">
                    {int(latest["advances"])}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with x2:

        st.markdown(
            f"""
            <div class="breadth-card">
                <div class="breadth-label">DECLINES</div>
                <div class="breadth-value">
                    {int(latest["declines"])}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with x3:

        net = (
            int(latest["advances"])
            - int(latest["declines"])
        )

        sign = "+" if net > 0 else ""

        st.markdown(
            f"""
            <div class="breadth-card">
                <div class="breadth-label">NET ADVANCES</div>
                <div class="breadth-value">
                    {sign}{net}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with x4:

        if (
            "mcclellan" in internal.columns
            and pd.notna(
                latest.get("mcclellan")
            )
        ):

            mc = float(
                latest["mcclellan"]
            )

            mc_text = f"{mc:.1f}"

        else:

            mc_text = "—"

        st.markdown(
            f"""
            <div class="breadth-card">
                <div class="breadth-label">MCCLELLAN</div>
                <div class="breadth-value">
                    {mc_text}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ═════════════════════════════════════════════════════════════════════
# FOOTER / DATA SOURCE
# ═════════════════════════════════════════════════════════════════════

st.markdown(
    f"""
    <div class="info-strip" style="margin-top:18px;">
        SOURCE · {universe_source}
        &nbsp;&nbsp;|&nbsp;&nbsp;
        TIMEFRAME · DAILY
        &nbsp;&nbsp;|&nbsp;&nbsp;
        MA · SIMPLE MOVING AVERAGE
        &nbsp;&nbsp;|&nbsp;&nbsp;
        SESSION · COMPLETED NSE TRADING DAY
    </div>
    """,
    unsafe_allow_html=True,
)
