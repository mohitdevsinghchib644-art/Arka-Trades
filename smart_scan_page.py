# --------------------------------------------------------------
# smart_scan_page.py  —  Arka Trades Smart Screener (rewritten)
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import base64, io, json, time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Optional imports ─────────────────────────────────────────────────────
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── UI theme constants ─────────────────────────────────────────────────
NAVY   = "#101A33"
IVORY  = "#E2E8F0"
GOLD   = "#4F8DFD"
BLUE   = "#4F8DFD"
GREEN  = "#10B981"
RED    = "#EF4444"
PURPLE = "#8B5CF6"
DARK   = "#0B0F17"
DARK2  = "#0F1522"
DARK3  = "#151D2E"
BORDER = "#1E293B"
T2     = "#94A3B8"
FONT   = "'Plus Jakarta Sans','Inter',sans-serif"
MONO   = "'JetBrains Mono',monospace"

MODEL_NAME = "gemini-2.5-flash"
MIN_SIMILARITY_FLOOR = 6

# ── Liquid NSE universe (fast fallback) ─────────────────────────────────
NSE_UNIVERSE = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN",
    # … (rest of the symbols you already have) …
]
NSE_UNIVERSE = list(dict.fromkeys(NSE_UNIVERSE))

# ── Full NSE universe (official list) ─────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def get_full_nse_universe(retries: int = 3) -> list:
    """
    Download the official NSE equity list (all listed stocks, EQ series).
    Retries a few times before giving up – we never silently fall back to the
    short hard‑coded list when the user explicitly asked for the full universe.
    """
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for attempt in range(1, retries + 1):
        try:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            if "SERIES" in df.columns:
                df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
            syms = (
                df["SYMBOL"]
                .astype(str)
                .str.strip()
                .str.upper()
                .dropna()
                .unique()
                .tolist()
            )
            syms = [s for s in syms if s and s.isascii()]
            if len(syms) > 500:          # sanity check – should be ~2000
                return syms
        except Exception as e:
            if attempt == retries:
                st.error(
                    f"❌ Failed to download the full NSE list after {retries} attempts. "
                    f"Error: {e}"
                )
                raise
            time.sleep(2 * attempt)
    return []   # unreachable – kept for type‑checkers

# ── (All the parsing, indicator, chart‑drawing helpers stay unchanged) ──
#   … (keep the original definitions of _PARSE_PROMPT, parse_rules_with_ai,
#       _filters_summary, _load_setups, _save_setup, _delete_setup,
#       _upload_image, _rsi, _ema, _sma, _atr, _fetch_bulk,
#       _calculate_indicators, _apply_filter, run_math_scan,
#       _make_chart_image, _audit_one, _parse_audit, etc.) …

# ── AI audit – now respects a strictness floor ─────────────────────────────
def run_ai_audit(candidates, setup, gemini_key,
                 max_stocks: int = 15,
                 strict_min: int = MIN_SIMILARITY_FLOOR,
                 progress_cb=None):
    """
    Sends the top `max_stocks` candidates to Gemini Vision and returns only
    those with a similarity score >= `strict_min`.
    """
    top = candidates[:max_stocks]
    visual_rules = setup.get("visual_rules", "")
    ref_url = setup.get("reference_image_url", "")

    results = []

    def _process(candidate):
        sym = candidate["symbol"]
        chart_bytes = _make_chart_image(sym, candidate["df"])
        audit = _audit_one(sym, chart_bytes, visual_rules, ref_url, gemini_key)
        return {**candidate, **audit}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_process, c): c for c in top}
        for i, fut in enumerate(as_completed(futures), 1):
            if progress_cb:
                progress_cb(i / len(top), f"AI comparing charts… ({i}/{len(top)})")
            try:
                res = fut.result()
                if res.get("score", 0) >= strict_min:
                    results.append(res)
            except Exception as exc:
                st.error(f"⚠️ AI audit failed for {futures[fut]['symbol']}: {exc}")

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results

# ── UI – scan page (universe selection & strictness handling) ───────────────
def _render_scan_page(supabase, gemini_key: str):
    setups = _load_setups(supabase)
    if not setups:
        st.warning("No setups found. Go to the Manage Setups tab and create one first.")
        return

    # -------------------------------------------------------------------------
    # Header & setup selector (unchanged)
    # -------------------------------------------------------------------------
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};
         border-left:3px solid {BLUE};border-radius:14px;
         padding:14px 20px;margin-bottom:18px;">
        <div style="font-size:16px;font-weight:800;color:{IVORY};margin-bottom:2px;">Smart Scan</div>
        <div style="font-size:12px;color:{T2};">
            Pick a setup, tune the live overrides (price / RSI / volume), hit Run Scan.
            Your rules filter the universe, then strict AI vision keeps ONLY the charts
            that truly match your reference setup.
        </div>
    </div>""", unsafe_allow_html=True)

    _section("Your Setups — Tap to Select")
    cols = st.columns(min(len(setups), 3))
    selected_key = st.session_state.get("selected_setup_id")

    for i, setup in enumerate(setups):
        with cols[i % 3]:
            is_sel = str(setup["id"]) == str(selected_key)
            bd = BLUE if is_sel else BORDER
            bg = "rgba(79,141,253,0.08)" if is_sel else DARK2
            sel_txt = "SELECTED" if is_sel else "TAP TO SELECT"
            sel_col = BLUE if is_sel else T2

            if setup.get("reference_image_url"):
                st.image(setup["reference_image_url"], use_container_width=True)

            st.markdown(f"""
            <div style="background:{bg};border:1px solid {bd};
                 border-radius:12px;padding:14px;margin-bottom:8px;text-align:center;">
                <div style="font-size:14px;font-weight:800;color:{IVORY};margin-bottom:6px;">{setup['name']}</div>
                <div style="font-size:10px;color:{T2};line-height:1.8;">{_filters_summary(setup)}</div>
                <div style="font-size:9px;letter-spacing:2px;color:{sel_col};
                     margin-top:8px;font-weight:700;">{sel_txt}</div>
            </div>""", unsafe_allow_html=True)

            if st.button("Select", key=f"sel_{setup['id']}", use_container_width=True):
                st.session_state["selected_setup_id"] = str(setup["id"])
                st.rerun()

    # -------------------------------------------------------------------------
    # Resolve selected setup
    # -------------------------------------------------------------------------
    selected_setup = None
    if selected_key:
        selected_setup = next((s for s in setups if str(s["id"]) == str(selected_key)), None)

    if not selected_setup:
        st.info("Select a setup above to start scanning.")
        return

    # -------------------------------------------------------------------------
    # Live overrides (price / RSI / volume) – unchanged
    # -------------------------------------------------------------------------
    _section("Price Range — Pick Before Scanning")
    base_pmin = float(selected_setup.get("price_min") or 0)
    base_pmax = float(selected_setup.get("price_max") or 99999)

    PRESETS = {
        "Use setup's range": (base_pmin, base_pmax),
        "100 - 250":         (100, 250),
        "250 - 500":         (250, 500),
        "500 - 750":         (500, 750),
        "750 - 1000":        (750, 1000),
        "1000 - 1500":       (1000, 1500),
        "1500 - 2000":       (1500, 2000),
        "Custom":            None,
    }
    pcol1, pcol2, pcol3 = st.columns([2, 1, 1])
    with pcol1:
        preset = st.selectbox("Price band (Rs)", list(PRESETS.keys()),
                              key="price_preset",
                              help="Override the saved setup's price range just for this scan.")
    if preset == "Custom":
        with pcol2:
            ov_pmin = st.number_input("Min price (Rs)", 0.0, 99999.0,
                                      value=max(base_pmin, 0.0), step=10.0, key="ov_pmin")
        with pcol3:
            ov_pmax = st.number_input("Max price (Rs)", 0.0, 99999.0,
                                      value=min(base_pmax, 99999.0), step=10.0, key="ov_pmax")
    else:
        ov_pmin, ov_pmax = PRESETS[preset]
        with pcol2:
            st.metric("Min", f"Rs {ov_pmin:,.0f}")
        with pcol3:
            st.metric("Max", f"Rs {ov_pmax:,.0f}")

    if ov_pmin > ov_pmax:
        st.error("❌ Min price is greater than Max price — fix the range before scanning.")
        return

    _section("Quick Filters — Live Overrides (RSI / Volume)")
    st.caption("Temporarily tighten or loosen rules without editing your saved setup. "
               "Leave a control OFF to keep the setup's own value.")

    qcol1, qcol2 = st.columns(2)
    with qcol1:
        rsi_override_on = st.toggle("Override RSI range", value=False, key="rsi_ov_on")
        if rsi_override_on:
            ov_rsi_min, ov_rsi_max = st.slider(
                "RSI between", 0, 100,
                (int(float(selected_setup.get("rsi_min") or 0)),
                 int(float(selected_setup.get("rsi_max") or 100))),
                key="ov_rsi")
        else:
            ov_rsi_min = float(selected_setup.get("rsi_min") or 0)
            ov_rsi_max = float(selected_setup.get("rsi_max") or 100)
    with qcol2:
        vol_override_on = st.toggle("Override volume rule", value=False, key="vol_ov_on")
        if vol_override_on:
            ov_vol = st.slider("Min volume (x 20‑day avg)", 0.0, 5.0,
                               float(selected_setup.get("volume_multiplier") or 0.0),
                               0.1, key="ov_vol")
        else:
            ov_vol = float(selected_setup.get("volume_multiplier") or 0.0)

    # Build the effective scan‑setup
    scan_setup = dict(selected_setup)
    scan_setup["price_min"] = float(ov_pmin)
    scan_setup["price_max"] = float(ov_pmax)
    scan_setup["rsi_min"]   = float(ov_rsi_min)
    scan_setup["rsi_max"]   = float(ov_rsi_max)
    scan_setup["volume_multiplier"] = float(ov_vol)

    # Show active overrides as chips (unchanged)
    badges = []
    if (ov_pmin, ov_pmax) != (base_pmin, base_pmax):
        badges.append(f"Price {ov_pmin:,.0f}-{ov_pmax:,.0f}")
    if rsi_override_on:
        badges.append(f"RSI {ov_rsi_min:.0f}-{ov_rsi_max:.0f}")
    if vol_override_on and ov_vol > 0:
        badges.append(f"Vol ≥ {ov_vol:.1f}x")
    if badges:
        chips = "".join(
            f"<span style='display:inline-block;background:rgba(79,141,253,0.12);"
            f"border:1px solid rgba(79,141,253,0.4);color:{BLUE};font-size:11px;"
            f"font-weight:700;border-radius:20px;padding:3px 10px;margin:2px 4px 2px 0;'>{b}</span>"
            for b in badges)
        st.markdown(f"<div style='margin:6px 0 2px;'><span style='font-size:11px;color:{T2};'>"
                    f"Active overrides for this scan: </span>{chips}</div>",
                    unsafe_allow_html=True)

    # Scan configuration (universe, AI limit, strictness)
    _section(f"Scan With: {selected_setup['name']}")
    c1, c2 = st.columns([2, 1])
    with c1:
        universe_opt = st.selectbox(
            "Scan Universe",
            ["ALL NSE Stocks (~2000, slower)", "Liquid NSE (~180, fast)",
             "Your Watchlist", "Arka Watchlist"],
            key="scan_universe")
    with c2:
        max_ai = st.number_input("Max AI Comparisons", 3, 25, 12, 1,
                                 key="scan_max_ai",
                                 help="Top N candidates sent to Gemini Vision after the rules filter")

    strict_min = st.slider("Match strictness (hide anything below this score)",
                           0, 10, MIN_SIMILARITY_FLOOR, 1,
                           key="strict_min",
                           help="6 = balanced. 8 = only near‑identical setups. 0 = show everything.")

    # Resolve the actual universe list
    if universe_opt.startswith("ALL"):
        with st.spinner("Downloading the full NSE symbol list (≈2000 symbols)…"):
            try:
                universe = get_full_nse_universe()
            except Exception:
                st.stop()
        st.caption(f"✅ {len(universe)} NSE symbols loaded – will be scanned in one pass")
    elif universe_opt.startswith("Liquid"):
        universe = NSE_UNIVERSE
        st.caption(f"🔹 Using the liquid list ({len(universe)} symbols)")
    elif universe_opt == "Your Watchlist":
        universe = st.session_state.get("watchlist", [])
        if not universe:
            st.warning("⚠️ Upload your watchlist in the ‘Scanner’ tab first.")
            return
    else:  # “Arka Watchlist”
        universe = st.session_state.get("admin_watchlist", [])
        if not universe:
            st.warning("⚠️ Arka Watchlist not available yet.")
            return

    if not gemini_key:
        st.warning("🔑 GEMINI_KEY not found – AI vision will be skipped; only rule‑based results shown.")

    # -------------------------------------------------------------------------
    # Run Scan button
    # -------------------------------------------------------------------------
    run_disabled = ov_pmin > ov_pmax
    if st.button("Run Scan", type="primary", use_container_width=True,
                 key="run_scan", disabled=run_disabled):
        # 1️⃣ Math‑only scan
        prog = st.progress(0.0)
        stat = st.empty()

        def _prog(pct, msg):
            prog.progress(min(float(pct), 1.0))
            stat.markdown(f"**{msg}**")

        _prog(0.05, f"Scanning {len(universe)} symbols (price filter built‑in)…")
        shortlist, failed = run_math_scan(universe, scan_setup, _prog)

        if not shortlist:
            prog.progress(1.0)
            stat.empty()
            st.warning("❌ No stocks passed your numeric filters. Adjust overrides or edit the setup.")
            if failed:
                with st.expander(f"{len(failed)} symbols had no data"):
                    st.write(", ".join(failed[:60]))
            st.stop()

        st.session_state["scan_math_results"] = shortlist
        st.session_state["scan_strict_min"]   = int(strict_min)
        stat.markdown(f"✅ {len(shortlist)} candidates passed the rule filter")

        # 2️⃣ AI Vision (only if we have a key)
        if HAS_GEMINI and gemini_key:
            ai_prog = st.progress(0.0)
            ai_stat = st.empty()

            def _ai_prog(pct, msg):
                ai_prog.progress(min(float(pct), 1.0))
                ai_stat.markdown(f"**{msg}**")

            ai_results = run_ai_audit(
                shortlist,
                scan_setup,
                gemini_key,
                max_stocks=int(max_ai),
                strict_min=int(strict_min),   # enforce floor here
                progress_cb=_ai_prog,
            )
            ai_prog.empty()
            ai_stat.empty()
            st.session_state["scan_ai_results"] = ai_results

            kept = len(ai_results)
            st.success(
                f"🔎 Vision step complete – {kept} stocks meet the similarity ≥ {strict_min}/10"
            )
        else:
            st.info("⚙️ Gemini key missing – skipping AI vision; only rule‑based results shown.")
        prog.empty()
        stat.empty()
        st.rerun()

    # -------------------------------------------------------------------------
    # Show results (unchanged, except we now read the strictness from session)
    # -------------------------------------------------------------------------
    math_results = st.session_state.get("scan_math_results")
    ai_results   = st.session_state.get("scan_ai_results")
    used_strict  = st.session_state.get("scan_strict_min", MIN_SIMILARITY_FLOOR)

    if math_results is None:
        return

    _section("Scan Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Universe Selected", len(universe))
    m2.metric("Passed Your Rules", len(math_results))
    m3.metric("AI Compared", len(ai_results) if ai_results else "—")
    if ai_results:
        m4.metric(f"True Matches (≥{used_strict})", len(ai_results))
    else:
        m4.metric("AI Status", "Skipped")

    # AI results table (same UI, but already filtered by strict_min)
    if ai_results:
        _section("Pattern Match Results")
        fcol1, fcol2, fcol3 = st.columns([1.4, 1.4, 1.4])
        with fcol1:
            min_sim = st.slider("Min similarity", 0, 10, int(used_strict), 1,
                                key="min_sim",
                                help="Hide any AI match scoring below this.")
        with fcol2:
            sort_opt = st.selectbox("Sort by",
                ["Similarity", "RSI (oversold first)", "Volume Ratio", "% Change"],
                key="sort_results")
        with fcol3:
            filt_v = st.radio("Show",
                ["All", "Strong", "Partial", "No Match"],
                horizontal=True, key="filt_verdict")

        ordered = [r for r in ai_results if r.get("score", 0) >= min_sim]

        if sort_opt == "Similarity":
            ordered.sort(key=lambda x: x.get("score", 0), reverse=True)
        elif "RSI" in sort_opt:
            ordered.sort(key=lambda x: x.get("rsi", 50))
        elif "Volume" in sort_opt:
            ordered.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)
        else:
            ordered.sort(key=lambda x: x.get("chg_pct", 0), reverse=True)

        if filt_v == "Strong":
            ordered = [r for r in ordered if r.get("verdict") == "STRONG MATCH"]
        elif filt_v == "Partial":
            ordered = [r for r in ordered if r.get("verdict") == "PARTIAL MATCH"]
        elif filt_v == "No Match":
            ordered = [r for r in ordered if r.get("verdict") == "NO MATCH"]

        hidden = len(ai_results) - len(ordered)
        st.caption(f"Showing {len(ordered)} of {len(ai_results)} – {hidden} weaker results hidden.")

        if not ordered:
            st.info("🚫 No charts satisfied the current filters. Lower the similarity slider or adjust overrides.")
        else:
            for res in ordered:
                _render_result_card(res, selected_setup)

    # Rule‑only shortlist (unchanged)
    _section(f"Rules Shortlist ({len(math_results)} stocks)")
    rows = []
    for r in math_results:
        chg = r["chg_pct"]
        rows.append({
            "Symbol": r["symbol"],
            "Price": f"Rs {r['close']:,.2f}",
            "Chg %": f"{'▲' if chg>=0 else '▼'} {abs(chg):.2f}%",
            "RSI": f"{r['rsi']:.0f}",
            "Vol Ratio": f"{r['vol_ratio']:.2f}x",
            "5D ROC": f"{r['roc_5']:.1f}%",
            "ATR %": f"{r['atr_pct']:.2f}%",
            "PDH": f"Rs {r['pdh']:,.2f}",
            "PDL": f"Rs {r['pdl']:,.2f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True,
                 hide_index=True, height=320)

# ── Main entry point (unchanged) ─────────────────────────────────────────────
def render_smart_scanner(supabase):
    """Call this from app.py when page == 'smart_scan'."""
    gemini_key = st.secrets.get("GEMINI_KEY", "")

    scan_tab, setup_tab = st.tabs(["Run Scan", "Manage Setups"])
    with scan_tab:
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        _render_scan_page(supabase, gemini_key)
    with setup_tab:
        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        _render_setup_manager(supabase, gemini_key)
