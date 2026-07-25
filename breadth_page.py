"""
breadth_page.py — UI layer for Market Breadth & Health Analysis.

Two changes in this revision, matching breadth_engine.py's fixes:

1. Added a "Backfill History" button (separate from the daily "Refresh
   Now" scan) that calls backfill_history_from_bhavcopy() once to pull
   15-20 days of real history immediately, instead of waiting three-plus
   weeks of daily scans to accumulate enough for the A/D Line, McClellan
   Oscillator, and HMM regime detector (which needs >=15 days) to have
   anything to work with. This directly addresses "I don't have previous
   data to see whether the environment is improving."

2. MA percentage displays (the g1 column and the top metric cards) now
   read snapshot["above_Xdma_denom"] as the denominator instead of
   snapshot["total_stocks"], matching the fix in compute_composite_score.
   Falls back to total_stocks if an older snapshot shape is somehow
   still in session_state (defensive only — a fresh scan always has the
   _denom fields).
"""

import streamlit as st
import pandas as pd

from breadth_engine import (
    get_nse_universe,
    compute_breadth_snapshot,
    compute_composite_score,
    compute_ad_line_and_mcclellan,
    append_history,
    load_history,
    backfill_history_from_bhavcopy,
    _eod_cache_key,
)
from breadth_ai import get_hmm_regime, generate_breadth_ai_narrative

DARK2  = "#11161D"
BORDER = "#242D3A"
IVORY  = "#E8ECF2"
T2     = "#8C97A8"
INDIGO = "#3B82F6"
CYAN   = "#06B6D4"
GREEN  = "#22C55E"
RED    = "#EF4444"
AMBER  = "#F59E0B"
PURPLE = "#8B5CF6"
PINK   = "#EC4899"
FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"


def _metric_card(label, value, sub=None, accent=None):
    accent = accent or INDIGO
    sub_html = f'<div style="font-size:11px;color:{T2};margin-top:4px;">{sub}</div>' if sub else ""
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};border-top:2px solid {accent};
                border-radius:12px;padding:16px;">
        <div style="font-size:11px;font-weight:700;color:{T2};text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:8px;">{label}</div>
        <div style="font-family:{MONO};font-weight:700;font-size:22px;color:{IVORY};">{value}</div>
        {sub_html}
    </div>""", unsafe_allow_html=True)


def _section_header(title, accent=None):
    accent = accent or INDIGO
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;margin:30px 0 16px;">
        <div style="width:4px;height:18px;border-radius:2px;background:{accent};"></div>
        <div style="font-family:{FONT};font-size:16px;font-weight:800;color:{IVORY};
                    white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)


def _score_color(score):
    if score is None:
        return T2
    if score >= 70:
        return GREEN
    if score >= 55:
        return CYAN
    if score >= 45:
        return T2
    if score >= 30:
        return AMBER
    return RED


def _pct_above(snapshot, ma_key):
    """
    Returns (percentage, above_count, denom) for a given MA key
    ('20', '50', or '200'), using the metric-specific denominator
    (above_Xdma_denom) instead of total_stocks. This is the display-
    side half of the same fix in compute_composite_score — both must
    use the same denominator or the score breakdown and the raw
    percentages shown elsewhere on the page would silently disagree
    with each other again.
    """
    above = snapshot.get(f"above_{ma_key}dma", 0)
    denom = snapshot.get(f"above_{ma_key}dma_denom")
    if not denom:
        below = snapshot.get(f"below_{ma_key}dma", 0)
        denom = above + below if (above + below) else snapshot.get("total_stocks", 1)
    pct = (above / denom * 100) if denom else 0.0
    return pct, above, denom


def render_market_breadth():
    st.markdown(f"""
    <div style="font-size:24px;font-weight:800;color:{IVORY};margin-bottom:4px;">
        📊 Market Breadth &amp; Health Analysis
    </div>
    <div style="font-size:13px;color:{T2};margin-bottom:20px;">
        Advance/decline internals, DMA participation, A/D Line, McClellan Oscillator,
        Markov regime clustering, and AI executive summaries — computed from real NSE data.
    </div>""", unsafe_allow_html=True)

    scan_col, backfill_col, info_col = st.columns([1, 1, 2])
    with scan_col:
        run_scan = st.button("🔄 Refresh Now", type="primary", use_container_width=True,
                              help="Data refreshes automatically once per trading day after "
                                   "4:00 PM IST. This re-checks for that session — it will not "
                                   "re-fetch a session you already have.")
    with backfill_col:
        run_backfill = st.button("📜 Backfill 20 Days", use_container_width=True,
                                  help="Reconstructs the last 20 trading days of breadth history "
                                       "in one go, using price data already on file — no extra "
                                       "network cost. Run this once now (or any time history looks "
                                       "thin, e.g. right after a redeploy) instead of waiting three "
                                       "weeks of daily scans for the A/D Line, McClellan Oscillator, "
                                       "and regime detector to have enough to work with.")

    st.caption(
        "📅 Breadth data updates once per trading session, at/after 4:00 PM IST, Monday–Friday. "
        "It will not change again until the next session's close."
    )

    if run_scan:
        st.session_state.pop("breadth_snapshot", None)
        st.session_state.pop("breadth_composite", None)
        st.session_state.pop("breadth_narrative", None)
        st.session_state.pop("breadth_session_key", None)

    if run_backfill:
        with st.spinner("Backfilling 20 trading days of history from price data already on file..."):
            tickers, _ = get_nse_universe()
            summary = backfill_history_from_bhavcopy(tickers, days=20)
        if summary.get("days_written", 0) > 0:
            oldest, newest = summary["date_range"]
            st.success(f"Backfilled {summary['days_written']} trading days ({oldest} → {newest}). "
                       "A/D Line, McClellan, and regime detection below now have real history to work with.")
        else:
            st.error(f"Backfill couldn't write any days: {summary.get('error', 'unknown reason')}")
        # Force recompute of history-dependent state below, without
        # forcing a full re-scan of today's snapshot (that's a separate
        # concern from "Refresh Now" and shouldn't trigger on backfill).
        st.session_state.pop("breadth_composite", None)
        st.session_state["breadth_history"] = compute_ad_line_and_mcclellan(load_history())
        if "breadth_snapshot" in st.session_state and "error" not in st.session_state["breadth_snapshot"]:
            st.session_state["breadth_composite"] = compute_composite_score(
                st.session_state["breadth_snapshot"], st.session_state["breadth_history"]
            )

    # Auto re-sync on the trading-session boundary, not just on a button
    # press. _eod_cache_key() resolves to a string that only changes
    # once, right at 4:00 PM IST on a trading day (see breadth_engine's
    # _resolve_eod_session_date) — the same key breadth_engine.py's own
    # @st.cache_data(ttl=None) fetch layer is keyed on. Previously this
    # page only checked "does a snapshot exist in session_state", which
    # meant that once a snapshot was stored, NOTHING re-fetched it short
    # of a manual click — even after a new session's data was already
    # sitting ready in the engine's own cache. Comparing the stored
    # session key against the CURRENT one means a plain page reload (no
    # button, no click) picks up the new session automatically the
    # moment it rolls over, while still doing nothing extra the other
    # ~23.5 hours of the day, since the key genuinely hasn't changed and
    # the underlying fetch is already cached by breadth_engine.py either
    # way — this only removes the page's own redundant, unaware gate.
    current_session_key = _eod_cache_key()
    stored_session_key = st.session_state.get("breadth_session_key")
    if stored_session_key is not None and stored_session_key != current_session_key:
        st.session_state.pop("breadth_snapshot", None)
        st.session_state.pop("breadth_composite", None)
        st.session_state.pop("breadth_narrative", None)
        st.toast(f"New trading session detected ({current_session_key}) — refreshing breadth automatically.")

    if "breadth_snapshot" not in st.session_state:
        with st.spinner("Fetching NSE universe and computing breadth..."):
            tickers, universe_source = get_nse_universe()
            snapshot = compute_breadth_snapshot(tickers)

            if "error" not in snapshot:
                append_history(snapshot)

            history = load_history()
            history = compute_ad_line_and_mcclellan(history)
            composite = compute_composite_score(snapshot, history)

            st.session_state["breadth_snapshot"] = snapshot
            st.session_state["breadth_universe_source"] = universe_source
            st.session_state["breadth_history"] = history
            st.session_state["breadth_composite"] = composite
            st.session_state["breadth_session_key"] = current_session_key

    snapshot  = st.session_state["breadth_snapshot"]
    universe_source = st.session_state.get("breadth_universe_source", "unknown")
    history   = st.session_state.get("breadth_history", pd.DataFrame())
    composite = st.session_state["breadth_composite"]

    with info_col:
        source_label = snapshot.get("source", "unknown") if "error" not in snapshot else "—"
        source_color = GREEN if "Bhavcopy" in source_label else (AMBER if "fallback" in source_label else T2)
        st.markdown(f"""<div style="font-size:11px;color:{T2};padding-top:10px;">
            Universe: {universe_source}<br>
            Data source: <span style="color:{source_color};font-weight:600;">{source_label}</span>
        </div>""", unsafe_allow_html=True)

    if "error" in snapshot:
        st.error(f"Breadth computation failed: {snapshot['error']}")
        st.caption(
            "This usually means the batched download returned no usable data "
            "(rate limit, network blip, or a stale universe list). Try scanning again "
            "in a minute."
        )
        return

    # If history is thin, nudge toward the backfill button rather than
    # let the A/D Line / McClellan / regime sections render as empty
    # with no explanation of why or what to do about it.
    if len(history) < 15:
        st.info(
            f"📜 Only **{len(history)}** trading day(s) of history on file. "
            "Click **Backfill 20 Days** above to reconstruct recent history immediately "
            "(uses price data already fetched — no extra cost), or keep scanning daily "
            "and it'll build up on its own over the next few weeks."
        )

    # ════════════════ Top composite banner ════════════════
    score = composite.get("score")
    score_display = f"{score} / 100" if score is not None else "N/A"
    label = composite.get("label", "N/A")
    accent = _score_color(score)

    pct20, above20, denom20 = _pct_above(snapshot, "20")
    pct200, above200, denom200 = _pct_above(snapshot, "200")

    b1, b2, b3, b4 = st.columns(4)
    with b1:
        _metric_card("Composite Score", score_display, label, accent=accent)
    with b2:
        _metric_card(
            "Advances / Declines",
            f"{snapshot['advances']} / {snapshot['declines']}",
            f"Net: {snapshot['advances'] - snapshot['declines']} · Unchanged: {snapshot['unchanged']}",
            accent=GREEN if snapshot['advances'] >= snapshot['declines'] else RED,
        )
    with b3:
        _metric_card("Above 20 DMA", f"{pct20:.1f}%", f"{above20} of {denom20} stocks with 20d+ history", accent=CYAN)
    with b4:
        _metric_card("Above 200 DMA", f"{pct200:.1f}%", f"{above200} of {denom200} stocks with 200d+ history", accent=PURPLE)

    st.caption(f"Snapshot as of {snapshot['date']} · {snapshot['total_stocks']} stocks fetched total "
               f"({denom200} had enough history for a 200DMA reading)")

    # ════════════════ Key breadth indicators ════════════════
    _section_header("📌 Key Breadth Indicators", CYAN)
    g1, g2, g3 = st.columns(3)

    with g1:
        st.markdown(f"**Moving Average Participation**")
        for label_, ma_key in [("20 DMA", "20"), ("50 DMA", "50"), ("200 DMA", "200")]:
            pct, above, denom = _pct_above(snapshot, ma_key)
            below = denom - above
            st.write(f"• Above {label_}: **{pct:.1f}%** ({above} of {denom}) · Below: {below}")

    with g2:
        thresh = snapshot.get("thresholds", {})
        st.markdown(f"**5-Day Momentum & Extremes**")
        st.write(f"• Up ≥{thresh.get('five_day_pct', 20)}% in 5d: **{snapshot['up_5d_pct']}** stocks")
        st.write(f"• Down ≥{thresh.get('five_day_pct', 20)}% in 5d: **{snapshot['down_5d_pct']}** stocks")
        st.write(f"• New 5-day highs / lows: **{snapshot['new_hi_5d']}** / **{snapshot['new_lo_5d']}**")
        st.write(f"• Up ≥{thresh.get('day_pct', 4.5)}% today: **{snapshot['up_day_pct']}** stocks")
        st.write(f"• Down ≥{thresh.get('day_pct', 4.5)}% today: **{snapshot['down_day_pct']}** stocks")

    with g3:
        st.markdown(f"**Session Breakdown**")
        st.write(f"• Total stocks fetched: **{snapshot['total_stocks']}**")
        st.write(f"• Unchanged stocks: **{snapshot['unchanged']}**")
        ad_ratio = snapshot['advances'] / snapshot['declines'] if snapshot['declines'] else float('inf')
        ad_ratio_display = f"{ad_ratio:.2f}" if ad_ratio != float('inf') else "∞"
        st.write(f"• Advance/Decline ratio: **{ad_ratio_display}**")
        st.write(f"• History on file: **{len(history)}** trading day(s)")

    # ════════════════ Composite score breakdown ════════════════
    if "breakdown" in composite:
        _section_header("🧮 Composite Score Breakdown", INDIGO)
        bd = composite["breakdown"]
        c1, c2, c3, c4 = st.columns(4)
        for col, key, title in [
            (c1, "ad_ratio", "A/D Ratio"),
            (c2, "ma_breadth", "MA Breadth"),
            (c3, "new_hilo", "New Hi/Lo"),
            (c4, "mcclellan", "McClellan"),
        ]:
            item = bd.get(key, {})
            with col:
                st.markdown(f"""
                <div style="background:{DARK2};border:1px solid {BORDER};border-radius:12px;padding:14px;">
                    <div style="font-size:11px;color:{T2};font-weight:700;text-transform:uppercase;
                                margin-bottom:6px;">{title}</div>
                    <div style="font-family:{MONO};font-size:16px;color:{IVORY};font-weight:700;">
                        {item.get('points', '—')} / {item.get('max', '—')}
                    </div>
                    <div style="font-size:11px;color:{T2};margin-top:4px;">{item.get('value', '—')}</div>
                </div>""", unsafe_allow_html=True)

    # ════════════════ A/D Line & McClellan chart ════════════════
    _section_header("📈 A/D Line & McClellan Oscillator", PINK)
    if history.empty or len(history) < 2:
        st.info(
            f"Only {len(history)} day(s) of history on file. Click **Backfill 20 Days** above "
            "the scan buttons to get a real trend immediately, or run this scan daily to build "
            "it up on its own."
        )
    else:
        chart_df = history.set_index("date")[["ad_line"]].rename(columns={"ad_line": "A/D Line"})
        st.line_chart(chart_df, color=[CYAN])
        if "mcclellan" in history.columns and history["mcclellan"].notna().any():
            mc_df = history.set_index("date")[["mcclellan"]].dropna().rename(columns={"mcclellan": "McClellan Oscillator"})
            st.line_chart(mc_df, color=[PURPLE])
        else:
            st.caption("McClellan Oscillator will appear once at least 2 days of history are on file.")

    # ════════════════ AI & statistical regime read ════════════════
    _section_header("🤖 AI & Statistical Regime Read", PURPLE)

    regime = get_hmm_regime(history)
    if regime:
        conf_color = GREEN if regime["confidence"] >= 70 else AMBER if regime["confidence"] >= 50 else T2
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {conf_color};
                    border-radius:12px;padding:18px 22px;margin-bottom:12px;">
            <div style="font-size:13px;color:{T2};margin-bottom:6px;">Markov Regime Detection</div>
            <div style="font-size:18px;font-weight:800;color:{IVORY};">
                {regime['regime']} <span style="color:{conf_color};font-family:{MONO};font-size:14px;">
                ({regime['confidence']}% confidence)</span>
            </div>
            <div style="font-size:12px;color:{T2};margin-top:8px;">
                Strong-regime mean net advances: {regime['strong_regime_mean']} ·
                Weak-regime mean: {regime['weak_regime_mean']} ·
                Fitted on {regime['observations_used']} trading day(s)
            </div>
        </div>""", unsafe_allow_html=True)
    else:
        needed = 15
        have = len(history)
        st.info(
            f"Markov regime detection needs at least {needed} days of history "
            f"(have {have}). Click **Backfill 20 Days** above to get there immediately."
        )

    if st.button("💡 Generate AI Market Narrative"):
        with st.spinner("Analyzing breadth metrics with Gemini..."):
            narrative = generate_breadth_ai_narrative(snapshot, composite, history)
            st.session_state["breadth_narrative"] = narrative

    if st.session_state.get("breadth_narrative"):
        st.markdown(f"""
        <div style="background:{DARK2};border:1px solid {BORDER};border-left:3px solid {PURPLE};
                    border-radius:12px;padding:18px 22px;margin-top:12px;font-size:14px;
                    color:{IVORY};line-height:1.7;">
            {st.session_state['breadth_narrative']}
        </div>""", unsafe_allow_html=True)
    elif "breadth_narrative" in st.session_state:
        st.warning(
            "AI narrative unavailable — check that GEMINI_API_KEY is set in st.secrets, "
            "and that google-generativeai>=0.8.0 is installed."
        )
