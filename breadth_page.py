import streamlit as st
import pandas as pd

from breadth_engine import (
    get_nse_universe,
    fetch_universe_ohlcv,
    compute_daily_breadth_metrics,
    compute_composite_score,
    compute_ad_line_and_mcclellan,
)
from breadth_ai import get_hmm_regime, generate_breadth_ai_narrative

def render_market_breadth():
    st.title("📊 Market Breadth & Health Analysis")
    st.caption("Quantitative participation metrics, statistical Markov regime clustering, and AI executive summaries.")

    if st.button("🔄 Scan Market Breadth Now", type="primary"):
        st.cache_data.clear()

    with st.spinner("Downloading OHLCV data for NSE universe and computing breadth metrics..."):
        tickers = get_nse_universe()
        ohlcv_data = fetch_universe_ohlcv(tickers)
        metrics = compute_daily_breadth_metrics(ohlcv_data, tickers)
        composite = compute_composite_score(metrics)

    # Top Composite Score Banner
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Composite Score", f"{composite['score']} / 100", delta=composite['label'])
    with col2:
        st.metric("Advances / Declines", f"{metrics['advances']} / {metrics['declines']}", delta=f"Net: {metrics['net_advances']}")
    with col3:
        st.metric("Above 20 DMA", f"{metrics['pct_above_20dma']}%", f"{metrics['above_20dma']} stocks")
    with col4:
        st.metric("Above 200 DMA", f"{metrics['pct_above_200dma']}%", f"{metrics['above_200dma']} stocks")

    st.markdown("---")

    # Detailed Indicator Grid
    st.subheader("📌 Key Breadth Indicators")
    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown("**Moving Average Participation**")
        st.write(f"• Above 20 DMA: **{metrics['pct_above_20dma']}%** ({metrics['above_20dma']} stocks)")
        st.write(f"• Above 50 DMA: **{metrics['pct_above_50dma']}%** ({metrics['above_50dma']} stocks)")
        st.write(f"• Above 200 DMA: **{metrics['pct_above_200dma']}%** ({metrics['above_200dma']} stocks)")

    with g2:
        st.markdown("**5-Day Momentum & Extremes**")
        st.write(f"• 5-Day Gainers: **{metrics['up_5d']}** stocks")
        st.write(f"• 5-Day Losers: **{metrics['down_5d']}** stocks")
        st.write(f"• 52-Week Highs / Lows: **{metrics['new_52w_hi']}** / **{metrics['new_52w_lo']}**")

    with g3:
        st.markdown("**Session Breakdown**")
        st.write(f"• Total Stocks Analyzed: **{metrics['total_scanned']}**")
        st.write(f"• Unchanged Stocks: **{metrics['unchanged']}**")
        st.write(f"• Advance/Decline Ratio: **{round(metrics['advances']/max(metrics['declines'], 1), 2)}**")

    st.markdown("---")

    # AI & Statistical Regime Analysis
    st.subheader("🤖 AI & Statistical Regime Read")
    
    # Construct synthetic history from current metric for regime modeling
    today_date = pd.Timestamp.now().strftime("%Y-%m-%d")
    sample_df = pd.DataFrame([{"date": pd.Timestamp(today_date), "net_advances": metrics["net_advances"]}])
    regime = get_hmm_regime(sample_df)

    if regime:
        st.info(f"**Markov Regime Detection:** Market is currently in a **{regime['regime']}** regime with **{regime['confidence']}%** confidence.")
    else:
        st.caption("ℹ️ Markov regime detection requires multi-day historical scans to fit regime models.")

    if st.button("💡 Generate AI Market Narrative"):
        with st.spinner("Analyzing breadth metrics with Gemini AI..."):
            narrative = generate_breadth_ai_narrative(metrics, composite, regime)
            st.markdown(narrative)
