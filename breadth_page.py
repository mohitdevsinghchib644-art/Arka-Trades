"""
breadth_page.py — Daily Market Breadth for Arka Trades.

Purpose:
    Track how many NSE stocks are above their 20 / 50 / 200 DMA
    on each trading day and show the day-over-day change.

Example:

Date          >20 DMA     Δ20      >50 DMA     Δ50      >200 DMA    Δ200
18 Sep 2026    1421       +86       1237       +43        984        +21
17 Sep 2026    1335       -12       1194       +17        963         +8
16 Sep 2026    1347       +51       1177       +32        955         -4

The purpose is NOT to create an arbitrary market score.

It is a historical participation monitor:
    - Are more stocks moving above the 20 DMA?
    - Is participation expanding through the 50 DMA?
    - Is long-term participation improving through the 200 DMA?
    - How quickly is the breadth changing?
"""

import streamlit as st
import pandas as pd

from breadth_engine import (
    get_nse_universe,
    compute_breadth_snapshot,
    append_history,
    load_history,
    backfill_history_from_bhavcopy,
    _eod_cache_key,
)


# ================================================================
# TERMINAL PALETTE
# ================================================================

BG = "#05070A"
PANEL = "#0B0F14"
PANEL_2 = "#10151C"
BORDER = "#202832"

TEXT = "#E7EBF0"
MUTED = "#7F8A99"

GREEN = "#30D158"
RED = "#FF453A"
AMBER = "#FF9F0A"
BLUE = "#3B82F6"


# ================================================================
# HELPERS
# ================================================================

def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def _format_change(value):
    """
    Format daily breadth change.

    +86
    -12
    0
    """
    if value is None:
        return "—"

    value = _safe_int(value)

    if value > 0:
        return f"+{value:,}"
    if value < 0:
        return f"{value:,}"

    return "0"


def _change_class(value):
    """
    Returns a simple semantic class used for the HTML table.
    """
    if value is None:
        return "neutral"

    value = _safe_int(value)

    if value > 0:
        return "positive"

    if value < 0:
        return "negative"

    return "neutral"


def _pct_change(current, previous):
    """
    Percentage-point style change based on stock counts.

    This is NOT a stock price percentage return.

    Example:
        1200 / 2000 = 60%
        1300 / 2000 = 65%

        change = +5.0 percentage points
    """
    if current is None or previous is None:
        return None

    try:
        return float(current) - float(previous)
    except Exception:
        return None


def _history_with_changes(history):
    """
    Prepare historical breadth table.

    The dataframe is sorted oldest -> newest for calculations.

    Daily change:
        today's count - previous trading day's count

    Then the final dataframe is returned newest -> oldest,
    matching the way traders normally read a daily breadth table.
    """

    if history is None or history.empty:
        return pd.DataFrame()

    df = history.copy()

    if "date" not in df.columns:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    df = (
        df.dropna(subset=["date"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )

    required = [
        "above_20dma",
        "above_50dma",
        "above_200dma",
    ]

    for col in required:
        if col not in df.columns:
            df[col] = 0

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        ).fillna(0).astype(int)

    # ------------------------------------------------------------
    # DAILY CHANGE
    # ------------------------------------------------------------

    df["change_20dma"] = df["above_20dma"].diff()
    df["change_50dma"] = df["above_50dma"].diff()
    df["change_200dma"] = df["above_200dma"].diff()

    # Keep first historical day without a previous comparison.
    df["change_20dma"] = df["change_20dma"].astype("Int64")
    df["change_50dma"] = df["change_50dma"].astype("Int64")
    df["change_200dma"] = df["change_200dma"].astype("Int64")

    return df.sort_values("date", ascending=False).reset_index(drop=True)


def _get_period_change(history, column, sessions):
    """
    Return:
        current value
        value N sessions ago
        absolute change

    Example:
        current >20 DMA = 1421
        5 sessions ago = 1100
        change = +321
    """

    if history is None or history.empty:
        return None, None, None

    df = history.sort_values("date").reset_index(drop=True)

    if column not in df.columns:
        return None, None, None

    if len(df) <= sessions:
        return None, None, None

    try:
        current = int(df[column].iloc[-1])
        previous = int(df[column].iloc[-1 - sessions])
        return current, previous, current - previous
    except Exception:
        return None, None, None


def _render_change(value):
    if value is None:
        return '<span class="change neutral">—</span>'

    value = _safe_int(value)

    if value > 0:
        return f'<span class="change positive">+{value:,}</span>'

    if value < 0:
        return f'<span class="change negative">{value:,}</span>'

    return '<span class="change neutral">0</span>'


# ================================================================
# HEADER
# ================================================================

def _render_header():
    st.markdown(
        f"""
        <div style="
            display:flex;
            align-items:flex-end;
            justify-content:space-between;
            border-bottom:1px solid {BORDER};
            padding-bottom:12px;
            margin-bottom:16px;
        ">
            <div>
                <div style="
                    font-family:'JetBrains Mono',monospace;
                    font-size:20px;
                    font-weight:700;
                    color:{TEXT};
                    letter-spacing:-0.3px;
                ">
                    MARKET BREADTH
                </div>

                <div style="
                    margin-top:4px;
                    font-family:'JetBrains Mono',monospace;
                    font-size:11px;
                    color:{MUTED};
                ">
                    DAILY MARKET PARTICIPATION · NSE EQUITIES
                </div>
            </div>

            <div style="
                font-family:'JetBrains Mono',monospace;
                font-size:10px;
                color:{MUTED};
                text-align:right;
            ">
                TIMEFRAME<br>
                <span style="color:{TEXT};font-weight:700;">DAILY</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ================================================================
# TOP SUMMARY
# ================================================================

def _render_summary(history):
    if history is None or history.empty:
        return

    latest = history.sort_values("date").iloc[-1]

    metrics = [
        (
            "ABOVE 20 DMA",
            _safe_int(latest.get("above_20dma")),
            latest.get("change_20dma"),
            BLUE,
        ),
        (
            "ABOVE 50 DMA",
            _safe_int(latest.get("above_50dma")),
            latest.get("change_50dma"),
            AMBER,
        ),
        (
            "ABOVE 200 DMA",
            _safe_int(latest.get("above_200dma")),
            latest.get("change_200dma"),
            GREEN,
        ),
    ]

    cols = st.columns(3)

    for col, (label, value, change, accent) in zip(cols, metrics):
        if change is None or pd.isna(change):
            change_text = "—"
            change_color = MUTED
        elif int(change) > 0:
            change_text = f"+{int(change):,}"
            change_color = GREEN
        elif int(change) < 0:
            change_text = f"{int(change):,}"
            change_color = RED
        else:
            change_text = "0"
            change_color = MUTED

        with col:
            st.markdown(
                f"""
                <div style="
                    background:{PANEL};
                    border:1px solid {BORDER};
                    border-top:2px solid {accent};
                    padding:13px 15px;
                    min-height:92px;
                ">
                    <div style="
                        font-family:'JetBrains Mono',monospace;
                        font-size:10px;
                        color:{MUTED};
                        letter-spacing:.8px;
                    ">
                        {label}
                    </div>

                    <div style="
                        display:flex;
                        align-items:baseline;
                        gap:10px;
                        margin-top:7px;
                    ">
                        <div style="
                            font-family:'JetBrains Mono',monospace;
                            font-size:24px;
                            font-weight:700;
                            color:{TEXT};
                        ">
                            {value:,}
                        </div>

                        <div style="
                            font-family:'JetBrains Mono',monospace;
                            font-size:13px;
                            font-weight:700;
                            color:{change_color};
                        ">
                            {change_text}
                        </div>
                    </div>

                    <div style="
                        margin-top:3px;
                        font-family:'JetBrains Mono',monospace;
                        font-size:9px;
                        color:{MUTED};
                    ">
                        vs previous trading session
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ================================================================
# DAILY BREADTH TABLE
# ================================================================

def _render_daily_table(history):
    st.markdown(
        f"""
        <div style="
            margin-top:22px;
            margin-bottom:8px;
            font-family:'JetBrains Mono',monospace;
            font-size:12px;
            font-weight:700;
            color:{TEXT};
            letter-spacing:.5px;
        ">
            DAILY BREADTH HISTORY
        </div>
        """,
        unsafe_allow_html=True,
    )

    if history.empty:
        st.info("No breadth history available yet.")
        return

    df = history.copy()

    # ------------------------------------------------------------
    # Display newest first
    # ------------------------------------------------------------

    rows = []

    for _, row in df.iterrows():
        date_value = row["date"]

        try:
            date_text = pd.Timestamp(date_value).strftime("%d %b %Y")
        except Exception:
            date_text = str(date_value)

        c20 = row.get("change_20dma")
        c50 = row.get("change_50dma")
        c200 = row.get("change_200dma")

        if pd.isna(c20):
            c20 = None
        if pd.isna(c50):
            c50 = None
        if pd.isna(c200):
            c200 = None

        rows.append(
            {
                "Date": date_text,

                ">20 DMA": f"{_safe_int(row.get('above_20dma')):,}",
                "Δ 20D": _format_change(c20),

                ">50 DMA": f"{_safe_int(row.get('above_50dma')):,}",
                "Δ 50D": _format_change(c50),

                ">200 DMA": f"{_safe_int(row.get('above_200dma')):,}",
                "Δ 200D": _format_change(c200),
            }
        )

    # ------------------------------------------------------------
    # HTML table
    # ------------------------------------------------------------

    html = f"""
    <div style="
        border:1px solid {BORDER};
        background:{PANEL};
        overflow-x:auto;
        margin-bottom:20px;
    ">
        <table style="
            width:100%;
            border-collapse:collapse;
            font-family:'JetBrains Mono',monospace;
            font-size:11px;
        ">
            <thead>
                <tr style="
                    background:{PANEL_2};
                    border-bottom:1px solid {BORDER};
                ">
                    <th style="
                        padding:9px 12px;
                        text-align:left;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">DATE</th>

                    <th style="
                        padding:9px 12px;
                        text-align:right;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">ABOVE 20 DMA</th>

                    <th style="
                        padding:9px 12px;
                        text-align:right;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">Δ</th>

                    <th style="
                        padding:9px 12px;
                        text-align:right;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">ABOVE 50 DMA</th>

                    <th style="
                        padding:9px 12px;
                        text-align:right;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">Δ</th>

                    <th style="
                        padding:9px 12px;
                        text-align:right;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">ABOVE 200 DMA</th>

                    <th style="
                        padding:9px 12px;
                        text-align:right;
                        color:{MUTED};
                        font-weight:600;
                        white-space:nowrap;
                    ">Δ</th>
                </tr>
            </thead>

            <tbody>
    """

    for index, row in enumerate(rows):
        border_bottom = (
            f"border-bottom:1px solid {BORDER};"
            if index < len(rows) - 1
            else ""
        )

        html += f"""
            <tr style="{border_bottom}">
                <td style="
                    padding:9px 12px;
                    color:{TEXT};
                    font-weight:{'700' if index == 0 else '500'};
                    white-space:nowrap;
                ">
                    {row['Date']}
                </td>

                <td style="
                    padding:9px 12px;
                    text-align:right;
                    color:{TEXT};
                ">
                    {row['>20 DMA']}
                </td>

                <td style="
                    padding:9px 12px;
                    text-align:right;
                ">
                    {_render_change(
                        None
                        if row['Δ 20D'] == "—"
                        else int(row['Δ 20D'].replace(",", "").replace("+", ""))
                    )}
                </td>

                <td style="
                    padding:9px 12px;
                    text-align:right;
                    color:{TEXT};
                ">
                    {row['>50 DMA']}
                </td>

                <td style="
                    padding:9px 12px;
                    text-align:right;
                ">
                    {_render_change(
                        None
                        if row['Δ 50D'] == "—"
                        else int(row['Δ 50D'].replace(",", "").replace("+", ""))
                    )}
                </td>

                <td style="
                    padding:9px 12px;
                    text-align:right;
                    color:{TEXT};
                ">
                    {row['>200 DMA']}
                </td>

                <td style="
                    padding:9px 12px;
                    text-align:right;
                ">
                    {_render_change(
                        None
                        if row['Δ 200D'] == "—"
                        else int(row['Δ 200D'].replace(",", "").replace("+", ""))
                    )}
                </td>
            </tr>
        """

    html += """
            </tbody>
        </table>
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


# ================================================================
# BREADTH TREND CHART
# ================================================================

def _render_trend_chart(history):
    if history is None or history.empty:
        return

    st.markdown(
        f"""
        <div style="
            margin-top:18px;
            margin-bottom:8px;
            font-family:'JetBrains Mono',monospace;
            font-size:12px;
            font-weight:700;
            color:{TEXT};
            letter-spacing:.5px;
        ">
            MARKET BREADTH TREND
        </div>

        <div style="
            font-family:'JetBrains Mono',monospace;
            font-size:10px;
            color:{MUTED};
            margin-bottom:10px;
        ">
            NUMBER OF NSE STOCKS ABOVE MOVING AVERAGE
        </div>
        """,
        unsafe_allow_html=True,
    )

    chart_df = history.copy()

    chart_df["date"] = pd.to_datetime(
        chart_df["date"],
        errors="coerce"
    )

    chart_df = (
        chart_df
        .dropna(subset=["date"])
        .sort_values("date")
        .set_index("date")
    )

    chart_df = chart_df[
        [
            "above_20dma",
            "above_50dma",
            "above_200dma",
        ]
    ].rename(
        columns={
            "above_20dma": ">20 DMA",
            "above_50dma": ">50 DMA",
            "above_200dma": ">200 DMA",
        }
    )

    if chart_df.empty:
        return

    st.line_chart(
        chart_df,
        height=360,
        use_container_width=True,
    )


# ================================================================
# PERIOD CHANGE
# ================================================================

def _render_period_change(history):
    st.markdown(
        f"""
        <div style="
            margin-top:20px;
            margin-bottom:8px;
            font-family:'JetBrains Mono',monospace;
            font-size:12px;
            font-weight:700;
            color:{TEXT};
            letter-spacing:.5px;
        ">
            BREADTH CHANGE
        </div>

        <div style="
            font-family:'JetBrains Mono',monospace;
            font-size:10px;
            color:{MUTED};
            margin-bottom:10px;
        ">
            CHANGE IN NUMBER OF STOCKS ABOVE EACH MOVING AVERAGE
        </div>
        """,
        unsafe_allow_html=True,
    )

    periods = [
        ("1D", 1),
        ("3D", 3),
        ("5D", 5),
        ("10D", 10),
        ("20D", 20),
    ]

    cols = st.columns(len(periods))

    for col, (label, sessions) in zip(cols, periods):

        with col:

            values = []

            for column in [
                "above_20dma",
                "above_50dma",
                "above_200dma",
            ]:
                current, previous, change = _get_period_change(
                    history,
                    column,
                    sessions,
                )

                values.append(change)

            c20, c50, c200 = values

            def value_html(value):
                if value is None:
                    return (
                        f'<span style="color:{MUTED};">—</span>'
                    )

                value = int(value)

                if value > 0:
                    color = GREEN
                    prefix = "+"
                elif value < 0:
                    color = RED
                    prefix = ""
                else:
                    color = MUTED
                    prefix = ""

                return (
                    f'<span style="color:{color};">'
                    f'{prefix}{value:,}'
                    f'</span>'
                )

            st.markdown(
                f"""
                <div style="
                    background:{PANEL};
                    border:1px solid {BORDER};
                    padding:12px 13px;
                    min-height:115px;
                ">
                    <div style="
                        font-family:'JetBrains Mono',monospace;
                        font-size:10px;
                        font-weight:700;
                        color:{TEXT};
                        margin-bottom:10px;
                    ">
                        {label}
                    </div>

                    <div style="
                        font-family:'JetBrains Mono',monospace;
                        font-size:10px;
                        color:{MUTED};
                        line-height:2;
                    ">
                        <div>
                            20 DMA&nbsp;&nbsp;
                            <b style="font-size:11px;">
                                {value_html(c20)}
                            </b>
                        </div>

                        <div>
                            50 DMA&nbsp;&nbsp;
                            <b style="font-size:11px;">
                                {value_html(c50)}
                            </b>
                        </div>

                        <div>
                            200 DMA
                            <b style="font-size:11px;">
                                {value_html(c200)}
                            </b>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ================================================================
# DATA SOURCE / STATUS
# ================================================================

def _render_status(snapshot, universe_source, history):
    if snapshot is None:
        return

    source = snapshot.get("source", "Unknown")

    if "Bhavcopy" in source:
        source_color = GREEN
    elif "fallback" in source.lower():
        source_color = AMBER
    else:
        source_color = MUTED

    date_text = snapshot.get("date", "Unknown")

    st.markdown(
        f"""
        <div style="
            display:flex;
            justify-content:space-between;
            align-items:center;
            margin-top:16px;
            padding:8px 11px;
            background:{PANEL};
            border:1px solid {BORDER};
            font-family:'JetBrains Mono',monospace;
            font-size:9px;
            color:{MUTED};
        ">
            <div>
                DATA DATE:
                <span style="color:{TEXT};">
                    {date_text}
                </span>
            </div>

            <div>
                HISTORY:
                <span style="color:{TEXT};">
                    {len(history)} sessions
                </span>
            </div>

            <div>
                SOURCE:
                <span style="color:{source_color};">
                    {source}
                </span>
            </div>

            <div>
                STOCKS:
                <span style="color:{TEXT};">
                    {_safe_int(snapshot.get("total_stocks")):,}
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ================================================================
# MAIN PAGE
# ================================================================

def render_market_breadth():

    _render_header()

    # ------------------------------------------------------------
    # CONTROLS
    # ------------------------------------------------------------

    refresh_col, backfill_col, spacer = st.columns([1, 1, 4])

    with refresh_col:
        run_scan = st.button(
            "REFRESH",
            use_container_width=True,
            help=(
                "Refresh the current daily breadth snapshot. "
                "The engine resolves the latest completed NSE trading session."
            ),
        )

    with backfill_col:
        run_backfill = st.button(
            "BACKFILL 20D",
            use_container_width=True,
            help=(
                "Build the recent daily breadth history using the "
                "NSE price history already fetched by the breadth engine."
            ),
        )

    st.caption(
        "Daily timeframe · One breadth observation per completed NSE trading session · "
        "Δ = change versus previous trading session"
    )

    # ------------------------------------------------------------
    # MANUAL REFRESH
    # ------------------------------------------------------------

    if run_scan:
        st.session_state.pop("breadth_snapshot", None)
        st.session_state.pop("breadth_session_key", None)

    # ------------------------------------------------------------
    # BACKFILL
    # ------------------------------------------------------------

    if run_backfill:

        with st.spinner(
            "Building the last 20 trading sessions of market breadth..."
        ):

            tickers, universe_source = get_nse_universe()

            summary = backfill_history_from_bhavcopy(
                tickers,
                days=20,
            )

        if summary.get("days_written", 0) > 0:

            oldest, newest = summary["date_range"]

            st.success(
                f"Backfilled {summary['days_written']} trading sessions "
                f"({oldest} → {newest})."
            )

        else:

            st.error(
                summary.get(
                    "error",
                    "Unable to backfill breadth history.",
                )
            )

        # Re-read history after backfill.
        st.session_state["breadth_history"] = load_history()

    # ------------------------------------------------------------
    # SESSION BOUNDARY
    # ------------------------------------------------------------

    current_session_key = _eod_cache_key()

    stored_session_key = st.session_state.get(
        "breadth_session_key"
    )

    if (
        stored_session_key is not None
        and stored_session_key != current_session_key
    ):

        st.session_state.pop("breadth_snapshot", None)

        st.toast(
            "New trading session detected — refreshing daily breadth."
        )

    # ------------------------------------------------------------
    # CURRENT SNAPSHOT
    # ------------------------------------------------------------

    if "breadth_snapshot" not in st.session_state:

        with st.spinner(
            "Computing daily NSE market breadth..."
        ):

            tickers, universe_source = get_nse_universe()

            snapshot = compute_breadth_snapshot(tickers)

            if "error" not in snapshot:
                append_history(snapshot)

            st.session_state["breadth_snapshot"] = snapshot
            st.session_state["breadth_universe_source"] = (
                universe_source
            )
            st.session_state["breadth_session_key"] = (
                current_session_key
            )

    # ------------------------------------------------------------
    # LOAD STATE
    # ------------------------------------------------------------

    snapshot = st.session_state.get(
        "breadth_snapshot",
        {},
    )

    universe_source = st.session_state.get(
        "breadth_universe_source",
        "Unknown",
    )

    history = load_history()

    # ------------------------------------------------------------
    # ERROR
    # ------------------------------------------------------------

    if "error" in snapshot:

        st.error(
            f"Market breadth computation failed: "
            f"{snapshot['error']}"
        )

        st.caption(
            "Try REFRESH again. The engine uses official NSE Bhavcopy "
            "data first and yfinance as fallback."
        )

        return

    # ------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------

    _render_summary(history)

    # ------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------

    _render_status(
        snapshot,
        universe_source,
        history,
    )

    # ------------------------------------------------------------
    # DAILY TABLE
    # ------------------------------------------------------------

    _render_daily_table(
        _history_with_changes(history)
    )

    # ------------------------------------------------------------
    # TREND
    # ------------------------------------------------------------

    _render_trend_chart(history)

    # ------------------------------------------------------------
    # PERIOD CHANGES
    # ------------------------------------------------------------

    _render_period_change(history)

    # ------------------------------------------------------------
    # FOOTNOTE
    # ------------------------------------------------------------

    st.markdown(
        f"""
        <div style="
            margin-top:18px;
            padding-top:10px;
            border-top:1px solid {BORDER};
            font-family:'JetBrains Mono',monospace;
            font-size:9px;
            color:{MUTED};
            line-height:1.7;
        ">
            ABOVE DMA = stocks whose closing price was above the
            corresponding simple moving average on that trading session.
            <br>
            DAILY Δ = current session count minus the previous completed
            trading session. Stocks without sufficient price history for
            a particular DMA are excluded from that DMA's denominator.
        </div>
        """,
        unsafe_allow_html=True,
    )
