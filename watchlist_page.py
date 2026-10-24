"""
watchlist_page.py — Arka Trades Watchlist

* Type stock symbols (or company names) → validated against live prices → saved
  to Supabase immediately. They stay until you remove them.
* Paste many at once: comma, semicolon or new-line separated.
* Live row data in ONE batched download: price, change, RSI(14), PDH/PDL status.
* Per-row actions: OPEN (full workspace) · ANALYSIS (Arka AI) · ✕ remove.
* Admins also see the ARKA DESK LIST tab (feeds the Smart Screener's
  "Arka Watchlist" universe).
"""
from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

import user_data as ud
from data_engine import get_quotes, scan_batch

try:
    from screener_scraper import resolve_symbol
except Exception:  # pragma: no cover
    resolve_symbol = None

AMBER, GREEN, RED, CYAN = "#FF9F0A", "#30D158", "#FF453A", "#5AC8FA"
DARK2, BORDER, IVORY, T2, T3 = "#0A0A0A", "#262626", "#E8E8E8", "#8A8A8A", "#5A5A5A"
MONO = "'JetBrains Mono',monospace"

_CSS = f"""
<style>
div[class*="st-key-wlrows"]{{gap:.15rem !important;}}
div[data-testid="stHorizontalBlock"]:has(.wl-cell){{align-items:center;border-bottom:1px solid {BORDER};padding:2px 0;}}
div[data-testid="stHorizontalBlock"]:has(.wl-cell):hover{{background:{DARK2};}}
.wl-cell{{font-family:{MONO};font-size:11px;color:{IVORY};min-height:34px;display:flex;align-items:center;}}
.wl-grid{{display:grid;grid-template-columns:1.3fr 1fr .8fr .8fr 1.2fr 1fr;gap:8px;width:100%;align-items:center;}}
.wl-head{{font-family:{MONO};font-size:9px;letter-spacing:.7px;color:{T2};font-weight:700;min-height:26px;display:flex;align-items:center;}}
.wl-sym{{font-weight:800;font-size:12px;color:{AMBER};}}
.wl-pill{{font-size:9px;font-weight:700;padding:2px 7px;border:1px solid;letter-spacing:.5px;}}
.wl-note{{font-family:{MONO};font-size:10px;color:{T2};border-left:2px solid #444;padding:6px 10px;background:rgba(255,255,255,.02);margin:8px 0;}}
</style>
"""


# ─────────────────────────── input resolution ───────────────────────────
def _company_lookup(text: str):
    """'reliance industries' -> 'RELIANCE' via Screener search (best effort)."""
    if not resolve_symbol:
        return None
    try:
        r = resolve_symbol(text)
    except Exception:
        return None
    if not r:
        return None
    m = re.search(r"/company/([^/]+)", str(r.get("url", "")), flags=re.I)
    return ud.clean_symbol(m.group(1)) if m else None


def resolve_tokens(tokens):
    """Returns (valid_symbols, invalid_tokens, notes). Validated against live price data."""
    plan = []
    for tok in tokens:
        whole = ud.clean_symbol(tok)
        parts = [ud.clean_symbol(p) for p in re.split(r"\s+", tok.strip()) if ud.clean_symbol(p)]
        plan.append((tok, whole, parts))
    probe = {s for _, whole, parts in plan for s in ([whole] if whole else []) + parts}
    quotes = get_quotes(tuple(sorted(probe))) if probe else {}
    valid, invalid, notes = [], [], []
    for tok, whole, parts in plan:
        if whole and quotes.get(whole):
            valid.append(whole)
        elif len(parts) > 1 and all(quotes.get(p) for p in parts):
            valid.extend(parts)
        else:
            sym = _company_lookup(tok)
            if sym and get_quotes((sym,)).get(sym):
                valid.append(sym)
                notes.append(f"“{tok}” matched to {sym}")
            else:
                invalid.append(tok)
    return list(dict.fromkeys(valid)), invalid, notes


# ─────────────────────────── UI pieces ───────────────────────────
def _spark(values, w=90, h=24):
    if not values or len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i / (len(values) - 1) * w:.1f},{h - 2 - ((v - lo) / rng) * (h - 6):.1f}" for i, v in enumerate(values))
    c = GREEN if values[-1] >= values[0] else RED
    return (f'<svg width="{w}" height="{h}"><polyline points="{pts}" fill="none" stroke="{c}" '
            f'stroke-width="1.5" stroke-linejoin="round"/></svg>')


def _add_box(supabase, table, key):
    with st.form(f"wl_add_{key}", clear_on_submit=True):
        c1, c2 = st.columns([5, 1])
        text = c1.text_input("Add stocks", placeholder="Type a symbol or company name — e.g. RELIANCE   ·   paste many: TCS, INFY, HDFCBANK",
                             label_visibility="collapsed")
        go = c2.form_submit_button("＋ ADD", type="primary", use_container_width=True)
    if go and text.strip():
        with st.spinner("Checking symbols…"):
            valid, invalid, notes = resolve_tokens(ud.split_tokens(text) or [text])
            added, err = ud.add_symbols(supabase, valid, table)
        already = [s for s in valid if s not in added]
        if added:
            st.success(f"Added {', '.join(added)}")
        if already:
            st.info(f"Already in your list: {', '.join(already)}")
        for n in notes:
            st.caption(n)
        if invalid:
            st.warning(f"No price data found for: {', '.join(invalid)}. Use the NSE trading symbol (e.g. M&M, BAJAJ-AUTO).")
        if err:
            st.error(err)
        if added:
            st.rerun()


def _bulk_import(supabase, table, key):
    with st.expander("Bulk import from a file (TradingView / CSV / TXT)"):
        up = st.file_uploader("Upload", type=["csv", "txt"], key=f"wl_up_{key}", label_visibility="collapsed")
        if up is not None and st.button("IMPORT", key=f"wl_imp_{key}"):
            try:
                df = pd.read_csv(io.BytesIO(up.getvalue()), header=None)
                raw = df.iloc[:, 0].astype(str).tolist()
            except Exception:
                raw = up.getvalue().decode("utf-8", "ignore").splitlines()
            tokens = [t for t in (ud.clean_symbol(r.split(",")[0]) for r in raw) if t and t.lower() != "nan"]
            with st.spinner(f"Validating {len(tokens)} symbols…"):
                valid, invalid, _ = resolve_tokens(list(dict.fromkeys(tokens)))
                added, err = ud.add_symbols(supabase, valid, table)
            st.success(f"Imported {len(added)} new symbols." + (f" Skipped {len(invalid)} with no price data." if invalid else ""))
            if err:
                st.error(err)
            st.rerun()


@st.fragment
def _list_fragment(supabase, table, key, on_open):
    syms = ud.load_symbols(supabase, table)
    if not syms:
        st.markdown(f'<div class="wl-note">Your list is empty. Add a stock above — it is saved automatically and stays until you remove it.</div>', unsafe_allow_html=True)
        return

    c1, c2, c3 = st.columns([2, 1.3, 3])
    q = c1.text_input("Filter", placeholder="filter list…", key=f"wl_filter_{key}", label_visibility="collapsed").strip().upper()
    sort = c2.selectbox("Sort", ["Added order", "Symbol A-Z", "Change % ↓", "Change % ↑", "RSI ↓", "RSI ↑"], key=f"wl_sort_{key}", label_visibility="collapsed")

    with st.spinner("Loading live data…"):
        data = scan_batch(tuple(syms))
    rows = []
    for s in syms:
        d = data.get(s)
        rows.append({"sym": s, "d": d, "chg": d["chg"] if d else None, "rsi": d["rsi"] if d else None})
    if q:
        rows = [r for r in rows if q in r["sym"]]
    if sort == "Symbol A-Z":
        rows.sort(key=lambda r: r["sym"])
    elif sort.startswith("Change"):
        rows.sort(key=lambda r: (r["chg"] is None, -(r["chg"] or 0) if "↓" in sort else (r["chg"] or 0)))
    elif sort.startswith("RSI"):
        rows.sort(key=lambda r: (r["rsi"] is None, -(r["rsi"] or 0) if "↓" in sort else (r["rsi"] or 0)))

    up = sum(1 for r in rows if r["chg"] is not None and r["chg"] > 0)
    dn = sum(1 for r in rows if r["chg"] is not None and r["chg"] < 0)
    c3.markdown(f'<div style="font:10px {MONO};color:{T2};padding-top:8px;">{len(syms)} stocks · '
                f'<span style="color:{GREEN}">▲ {up}</span> · <span style="color:{RED}">▼ {dn}</span> · saved automatically</div>', unsafe_allow_html=True)

    h = st.columns([8.2, 0.95, 0.95, 0.45])
    h[0].markdown('<div class="wl-head wl-grid"><span>SYMBOL</span><span>PRICE</span><span>CHG %</span><span>RSI 14</span><span>LEVEL (PDH / PDL)</span><span>12D TREND</span></div>', unsafe_allow_html=True)

    with st.container(key=f"wlrows_{key}"):
        for r in rows:
            sym, d = r["sym"], r["d"]
            cols = st.columns([8.2, 0.95, 0.95, 0.45])
            if d:
                cc = GREEN if d["chg"] >= 0 else RED
                if d["cur"] > d["pdh"]:
                    lvl = f'<span class="wl-pill" style="color:{GREEN};border-color:{GREEN}66">ABOVE PDH</span>'
                elif d["cur"] < d["pdl"]:
                    lvl = f'<span class="wl-pill" style="color:{RED};border-color:{RED}66">BELOW PDL</span>'
                else:
                    lvl = f'<span class="wl-pill" style="color:{T2};border-color:#444">IN RANGE</span>'
                rsi_c = GREEN if d["rsi"] < 35 else RED if d["rsi"] > 70 else IVORY
                html = (f'<div class="wl-cell wl-grid"><span class="wl-sym">{sym}</span><span>₹{d["cur"]:,.2f}</span>'
                        f'<span style="color:{cc}">{"▲" if d["chg"] >= 0 else "▼"} {abs(d["chg"]):.2f}%</span>'
                        f'<span style="color:{rsi_c}">{d["rsi"]}</span><span>{lvl}</span><span>{_spark(d.get("spark"))}</span></div>')
            else:
                html = (f'<div class="wl-cell wl-grid"><span class="wl-sym">{sym}</span><span style="color:{T3}">no data</span>'
                        f'<span>—</span><span>—</span><span>—</span><span></span></div>')
            cols[0].markdown(html, unsafe_allow_html=True)
            if cols[1].button("OPEN", key=f"wl_open_{key}_{sym}", use_container_width=True):
                on_open(sym)
            if cols[2].button("ANALYSIS", key=f"wl_an_{key}_{sym}", use_container_width=True, help="Open in Arka AI and start the analysis"):
                ud.go_analysis(sym)
            if cols[3].button("✕", key=f"wl_rm_{key}_{sym}", use_container_width=True, help=f"Remove {sym}"):
                err = ud.remove_symbol(supabase, sym, table)
                if err:
                    st.error(err)
                else:
                    st.toast(f"{sym} removed")
                    st.rerun(scope="fragment")


def _panel(supabase, table, key, on_open, blurb):
    if supabase is None:
        st.warning("Supabase is not configured, so this list is kept for this browser session only. "
                   "Add SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets to save it permanently.")
    st.markdown(f'<div class="wl-note">{blurb}</div>', unsafe_allow_html=True)
    _add_box(supabase, table, key)
    _list_fragment(supabase, table, key, on_open)
    _bulk_import(supabase, table, key)


def render_watchlist(supabase, on_open, is_admin=False):
    st.markdown(_CSS, unsafe_allow_html=True)
    if is_admin:
        t1, t2 = st.tabs(["MY WATCHLIST", "ARKA DESK LIST (ADMIN)"])
        with t1:
            _panel(supabase, ud.W_TABLE, "mine", on_open,
                   "Add any NSE stock by symbol or company name. Everything you add is saved automatically and stays until you press ✕.")
        with t2:
            _panel(supabase, ud.A_TABLE, "desk", on_open,
                   "The curated Arka desk list. It feeds the Smart Screener’s “Arka Watchlist” universe and the news rail for members with an empty watchlist.")
    else:
        _panel(supabase, ud.W_TABLE, "mine", on_open,
               "Add any NSE stock by symbol or company name. Everything you add is saved automatically and stays until you press ✕.")
