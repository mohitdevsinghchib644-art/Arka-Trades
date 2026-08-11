"""
heatmap_page.py — Arka Trades Heatmap

Sector-grouped tiles, colored by day % change, matching the visual
pattern from the Fluere reference screenshots (colored tile grid,
grouped under sector headers). Uses:
  - price/% change: the SAME get_static/get_price functions app.py
    already calls for the Scanner (no new price data source)
  - sector grouping: screener_scraper.get_sector_for_heatmap(), which
    is disk-cached after first lookup (see that function's docstring)
    so re-rendering the heatmap doesn't re-fetch sectors every time

Renders whichever watchlist is active (user's own if uploaded, else
the Arka admin watchlist), same fallback pattern as the news dock.
"""

import streamlit as st
from screener_scraper import get_sector_for_heatmap


def _tile_color(chg: float, green: str, red: str, panel: str) -> str:
    """
    Color intensity scales with |chg|, capped at a 4% move so a single
    outlier day doesn't wash out every other tile to full saturation.
    Flat/near-zero days render as the neutral panel color rather than
    a washed-out green/red, so "no real move" reads as visually quiet
    the way a real heatmap should.
    """
    if abs(chg) < 0.05:
        return panel
    capped = max(-4.0, min(4.0, chg))
    intensity = abs(capped) / 4.0  # 0..1
    base = green if chg >= 0 else red
    alpha = 0.18 + intensity * 0.65
    return f"{base}{int(alpha * 255):02x}"


def render_heatmap(get_static_fn, get_price_fn, watchlist: list[str], tokens: dict):
    """
    get_static_fn / get_price_fn: passed in from app.py rather than
    imported directly, so this module has no hard dependency on
    app.py's specific yfinance-backed implementations — any function
    matching the same (symbol) -> {"price":..., "chg":...} shape
    works, including a future swapped-out data source without this
    file changing.
    """
    panel = tokens.get("panel", "#0A0A0A")
    border = tokens.get("border", "#2A2A2A")
    ivory = tokens.get("ivory", "#E8E8E8")
    t2 = tokens.get("t2", "#8A8A8A")
    t3 = tokens.get("t3", "#5A5A5A")
    amber = tokens.get("amber", "#FF9500")
    green = tokens.get("green", "#00D964")
    red = tokens.get("red", "#FF3B3B")
    mono = tokens.get("mono", "'JetBrains Mono',monospace")

    if not watchlist:
        st.markdown(f"""<div style="padding:60px 20px;text-align:center;color:{t3};font-size:12px;">
            No watchlist loaded — upload one in Scanner first.</div>""", unsafe_allow_html=True)
        return

    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:10px;padding:4px 0 14px;">
        <div style="font-family:{mono};font-size:13px;font-weight:700;color:{amber};letter-spacing:2px;">
            HEATMAP</div>
        <div style="flex:1;height:1px;background:{border};"></div>
        <div style="font-size:10px;color:{t3};">{len(watchlist)} symbols</div>
    </div>""", unsafe_allow_html=True)

    if st.button("Load / Refresh Heatmap", type="primary", key="heatmap_load"):
        st.session_state.pop("heatmap_data", None)

    if "heatmap_data" not in st.session_state:
        rows = []
        bar = st.progress(0.0, text="Fetching prices and sectors...")
        for i, sym in enumerate(watchlist):
            price_data = get_price_fn(sym)
            if price_data:
                sector = get_sector_for_heatmap(sym)
                rows.append({"sym": sym, "chg": price_data["chg"], "price": price_data["price"], "sector": sector})
            bar.progress((i + 1) / len(watchlist), text=f"Fetching {sym}...")
        bar.empty()
        st.session_state["heatmap_data"] = rows

    rows = st.session_state.get("heatmap_data", [])
    if not rows:
        st.info("No price data available yet. Click Load / Refresh Heatmap above.")
        return

    by_sector: dict[str, list] = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r)

    sector_order = sorted(by_sector.keys(),
                           key=lambda s: sum(abs(r["chg"]) for r in by_sector[s]), reverse=True)

    for sector in sector_order:
        symbols_in_sector = sorted(by_sector[sector], key=lambda r: r["chg"], reverse=True)
        sector_avg = sum(r["chg"] for r in symbols_in_sector) / len(symbols_in_sector)
        avg_color = green if sector_avg >= 0 else red

        st.markdown(f"""<div style="display:flex;align-items:baseline;gap:10px;margin:18px 0 6px;">
            <span style="font-size:11px;font-weight:700;color:{ivory};text-transform:uppercase;letter-spacing:0.5px;">{sector}</span>
            <span style="font-family:{mono};font-size:11px;font-weight:700;color:{avg_color};">{'+' if sector_avg>=0 else ''}{sector_avg:.2f}% avg</span>
            <span style="font-size:10px;color:{t3};">· {len(symbols_in_sector)} stocks</span></div>""",
            unsafe_allow_html=True)

        tile_cols = st.columns(6)
        for i, r in enumerate(symbols_in_sector):
            bg = _tile_color(r["chg"], green, red, panel)
            with tile_cols[i % 6]:
                st.markdown(f"""<div style="background:{bg};border:1px solid {border};padding:10px 8px;
                    margin-bottom:4px;text-align:center;">
                    <div style="font-size:11px;font-weight:800;color:{ivory};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{r['sym']}</div>
                    <div style="font-family:{mono};font-size:11px;font-weight:700;color:{ivory};margin-top:2px;">{'+' if r['chg']>=0 else ''}{r['chg']:.2f}%</div>
                    </div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="margin-top:20px;font-size:10px;color:{t3};">
        Sector classification: Screener.in (cached after first lookup per symbol) ·
        Price data: same source as Scanner</div>""", unsafe_allow_html=True)
