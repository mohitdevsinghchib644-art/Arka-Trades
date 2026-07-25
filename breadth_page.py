"""
breadth_page.py — UI layer for Market Breadth & Health Analysis.

Rewritten to call breadth_engine's real functions directly instead of
the lossy compute_daily_breadth_metrics bridge wrapper (which silently
collapsed any snapshot error into an all-zero metrics dict via
`.get("total_scanned", 1)` — a key that snapshot never actually sets —
and was the root cause of the "everything shows 0" bug).

This page now:
  1. Calls compute_breadth_snapshot() directly and reads its real keys
     (total_stocks, up_5d_pct, new_hi_5d, etc).
  2. Surfaces snapshot["error"] with st.error() the moment it appears,
     instead of rendering a wall of zeros with no explanation.
  3. Persists every successful scan via append_history() and rebuilds
     the A/D Line + McClellan series from load_history() each render,
     so those indicators (and the HMM regime detector, which needs
     >= 15 real trading days) actually have data to work with instead
     of a synthetic 1-row frame that can never satisfy the model's
     minimum-history floor.
  4. Caches the last snapshot/composite in session_state so pressing
     "Generate AI Market Narrative" doesn't force a full universe
     re-download.
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
)
from breadth_ai import get_hmm_regime, generate_breadth_ai_narrative

# ── Local styling constants (mirrors app.py's dark terminal palette) ──
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


def render_market_breadth():
    st.markdown(f"""
    <div style="font-size:24px;font-weight:800;color:{IVORY};margin-bottom:4px;">
        📊 Market Breadth &amp; Health Analysis
    </div>
    <div style="font-size:13px;color:{T2};margin-bottom:20px;">
        Advance/decline internals, DMA participation, A/D Line, McClellan Oscillator,
        Markov regime clustering, and AI executive summaries — computed from real NSE data.
    </div>""", unsafe_allow_html=True)

    scan_col, info_col = st.columns([1, 3])
    with scan_col:
        run_scan = st.button("🔄 Refresh Now", type="primary", use_container_width=True,
                              help="Data refreshes automatically once per trading day after "
                                   "4:00 PM IST. This re-checks for that session — it will not "
                                   "re-fetch a session you already have.")

    st.caption(
        "📅 Breadth data updates once per trading session, at/after 4:00 PM IST, Monday–Friday. "
        "It will not change again until the next session's close."
    )

    # ── Run scan (or reuse last cached snapshot in session_state) ──
    # This button clears the page's own session_state cache so the UI
    # recomputes, but the underlying fetch (_load_bhavcopy_history /
    # _batch_download_yfinance in breadth_engine.py) is cached by
    # Streamlit with ttl=None, keyed on the resolved trading-session
    # date. Pressing this before 4pm IST re-renders but correctly
    # returns the SAME prior session's data rather than re-fetching,
    # since the session key hasn't rolled over yet — that's intentional.
    if run_scan:
        st.session_state.pop("breadth_snapshot", None)
        st.session_state.pop("breadth_composite", None)
        st.session_state.pop("breadth_narrative", None)

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

    # ── Surface errors immediately instead of rendering fake zeros ──
    if "error" in snapshot:
        st.error(f"Breadth computation failed: {snapshot['error']}")
        st.caption(
            "This usually means the batched yfinance download returned no usable data "
            "(rate limit, network blip, or a stale universe list). Try scanning again "
            "in a minute, or check that _batch_download isn't being throttled."
        )
        return

    # ════════════════ Top composite banner ════════════════
    score = composite.get("score")
    score_display = f"{score} / 100" if score is not None else "N/A"
    label = composite.get("label", "N/A")
    accent = _score_color(score)

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
        pct20 = snapshot['above_20dma'] / snapshot['total_stocks'] * 100 if snapshot['total_stocks'] else 0
        _metric_card("Above 20 DMA", f"{pct20:.1f}%", f"{snapshot['above_20dma']} of {snapshot['total_stocks']} stocks", accent=CYAN)
    with b4:
        pct200 = snapshot['above_200dma'] / snapshot['total_stocks'] * 100 if snapshot['total_stocks'] else 0
        _metric_card("Above 200 DMA", f"{pct200:.1f}%", f"{snapshot['above_200dma']} of {snapshot['total_stocks']} stocks", accent=PURPLE)

    st.caption(f"Snapshot as of {snapshot['date']} · {snapshot['total_stocks']} stocks with sufficient history")

    # ════════════════ Key breadth indicators ════════════════
    _section_header("📌 Key Breadth Indicators", CYAN)
    g1, g2, g3 = st.columns(3)

    with g1:
        st.markdown(f"**Moving Average Participation**")
        total = snapshot['total_stocks'] or 1
        for label_, above, below in [
            ("20 DMA", snapshot['above_20dma'], snapshot['below_20dma']),
            ("50 DMA", snapshot['above_50dma'], snapshot['below_50dma']),
            ("200 DMA", snapshot['above_200dma'], snapshot['below_200dma']),
        ]:
            pct = above / total * 100
            st.write(f"• Above {label_}: **{pct:.1f}%** ({above} stocks) · Below: {below}")

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
        st.write(f"• Total stocks analyzed: **{snapshot['total_stocks']}**")
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
            f"Only {len(history)} day(s) of history on file. Run this scan daily to build "
            "the A/D Line and McClellan series — both need multiple sessions to plot a trend. "
            "History accumulates automatically each time you scan."
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
            f"(have {have}). Scan daily to accumulate enough sessions — this happens "
            "automatically, there's nothing else to configure."
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
