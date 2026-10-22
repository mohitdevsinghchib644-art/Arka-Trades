"""Arka Money Flow: sector rotation, market breadth, constituents and FII/DII.

Uses NSE public endpoints where available. Missing feed fields stay missing.
The turnover signal is a price-signed activity proxy, not institutional order flow.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st

NSE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE + "/market-data/live-market-indices",
    "X-Requested-With": "XMLHttpRequest",
}
NUM_INDEX = ["last", "percentChange", "pe", "pb", "dy", "advances", "declines", "unchanged",
             "oneWeekAgo", "oneMonthAgo", "oneYearAgo", "yearHigh", "yearLow"]
SCOPES = {
    "SECTORAL": ("SECTORAL",), "THEMATIC": ("THEMATIC",),
    "SECTORAL + THEMATIC": ("SECTORAL", "THEMATIC"),
    "BROAD MARKET": ("BROAD MARKET", "BROADMARKET"), "ALL INDICES": (),
}
QUAD = {"LEADING": "#32d296", "WEAKENING": "#f7b84b", "LAGGING": "#ff6969", "IMPROVING": "#57b8ff"}


def _style():
    st.markdown("""<style>
    .arka-mf-kicker{font:700 10px monospace;letter-spacing:1.5px;color:#9ca3af;text-transform:uppercase}
    .arka-mf-title{font-size:24px;font-weight:800;color:#f4f6f8;margin:2px 0}
    .arka-mf-sub{font-size:12px;color:#8b929d;margin:0 0 16px}
    .arka-mf-section{font:700 11px monospace;letter-spacing:1px;color:#d1d7e0;margin:17px 0 8px;padding-bottom:7px;border-bottom:1px solid #292e36}
    .arka-mf-note{font-size:11px;color:#929aa6;border-left:2px solid #5a6472;padding:7px 10px;background:rgba(255,255,255,.025);margin:7px 0 12px}
    div[data-testid="stMetric"]{background:linear-gradient(135deg,rgba(255,255,255,.035),rgba(255,255,255,.012));border:1px solid #292e36;padding:10px 12px;border-radius:5px}
    div[data-testid="stMetricLabel"]{font-size:10px!important;letter-spacing:.8px;text-transform:uppercase;color:#a4acb8!important}
    div[data-testid="stMetricValue"]{font-size:22px!important;font-weight:800}
    </style>""", unsafe_allow_html=True)


@st.cache_resource(ttl=600, show_spinner=False)
def _session():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(NSE, timeout=8)
        session.get(NSE + "/market-data/live-market-indices", timeout=8)
    except requests.RequestException:
        pass
    return session


def _get(path):
    for attempt in range(2):
        try:
            response = _session().get(NSE + path, timeout=12)
            if response.status_code == 200:
                return response.json()
        except (requests.RequestException, ValueError):
            pass
        if attempt == 0:
            _session.clear()
            time.sleep(.4)
    return None


def _rename(df, target, aliases):
    if target not in df.columns:
        for name in aliases:
            if name in df.columns:
                df.rename(columns={name: target}, inplace=True)
                return


def _numeric(df, columns):
    for col in columns:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")


@st.cache_data(ttl=60, show_spinner=False, max_entries=4)
def fetch_all_indices():
    """Latest available NSE index snapshot and normalized return/breadth fields."""
    payload = _get("/api/allIndices")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        return None
    df = pd.DataFrame(payload["data"])
    for target, aliases in {"index": ("indexName", "name"), "last": ("lastPrice",),
                            "percentChange": ("percChange", "pChange", "changePercent"),
                            "key": ("category", "indexCategory")}.items():
        _rename(df, target, aliases)
    if "index" not in df:
        return None
    df["index"] = df["index"].astype(str).str.strip()
    df = df[df["index"].ne("") & df["index"].str.lower().ne("nan")].copy()
    if "key" not in df:
        df["key"] = "OTHER"
    df["key"] = df["key"].fillna("OTHER").astype(str).str.upper().str.strip()
    _numeric(df, NUM_INDEX)
    df["r1d"] = df["percentChange"]
    for period, field in (("r1w", "oneWeekAgo"), ("r1m", "oneMonthAgo"), ("r1y", "oneYearAgo")):
        ref = df[field].where(df[field] > 0)
        df[period] = (df["last"] / ref - 1) * 100
    total = df[["advances", "declines", "unchanged"]].fillna(0).sum(axis=1).replace(0, np.nan)
    df["adv_pct"] = df["advances"] / total * 100
    df["ad_ratio"] = df["advances"] / df["declines"].where(df["declines"] > 0)
    df["net_adv"] = df["advances"] - df["declines"]
    df["breadth_pct"] = df["net_adv"] / total * 100
    return df.replace([np.inf, -np.inf], np.nan).sort_values("index").reset_index(drop=True)


@st.cache_data(ttl=120, show_spinner=False, max_entries=96)
def fetch_constituents(index_name):
    """Latest constituent snapshot for one index; volume/value units are not converted."""
    payload = _get(f"/api/equity-stockIndices?index={quote(str(index_name), safe='')}")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        return None
    df = pd.DataFrame(payload["data"])
    for target, aliases in {"symbol": ("SYMBOL", "stockSymbol"), "lastPrice": ("last", "closePrice", "price"),
                            "pChange": ("percentChange", "changePercent"),
                            "totalTradedValue": ("tradedValue", "value"),
                            "totalTradedVolume": ("tradedVolume", "volume")}.items():
        _rename(df, target, aliases)
    if "symbol" not in df:
        return None
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df = df[df["symbol"].ne("") & df["symbol"].str.lower().ne("nan")].copy()
    df = df[~df["symbol"].str.replace(" ", "", regex=False).eq(str(index_name).upper().replace(" ", ""))]
    if "priority" in df:
        priority = pd.to_numeric(df["priority"], errors="coerce")
        if priority.notna().any() and (priority == 0).any():
            df = df[priority.eq(0)]
    _numeric(df, ["lastPrice", "pChange", "totalTradedVolume", "totalTradedValue", "ffmc",
                  "yearHigh", "yearLow", "nearWKH", "nearWKL"])
    df["value_raw"] = df["totalTradedValue"]
    return df.reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def fetch_fii_dii():
    payload = _get("/api/fiidiiTradeReact")
    rows = payload.get("data", payload.get("records", [])) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        return None
    df = pd.DataFrame(rows)
    for target, aliases in {"category": ("investorType", "clientType", "categoryName"),
                            "buyValue": ("buy", "buy_value"), "sellValue": ("sell", "sell_value"),
                            "netValue": ("net", "net_value"), "date": ("tradingDate", "tradeDate", "timestamp")}.items():
        _rename(df, target, aliases)
    _numeric(df, ["buyValue", "sellValue", "netValue"])
    if "category" not in df:
        df["category"] = "Investor"
    return df


def rank_sectors(indices, scope="SECTORAL"):
    """Relative-strength rank and simple rotation quadrant versus NIFTY 50."""
    if indices is None or indices.empty or "NIFTY 50" not in indices["index"].values:
        return pd.DataFrame()
    keys = SCOPES.get(scope, ("SECTORAL",))
    if keys:
        cats = indices["key"].astype(str).str.upper().str.replace("_", " ", regex=False)
        sector = indices[cats.apply(lambda x: any(k in x for k in keys))].copy()
    else:
        sector = indices.copy()
    sector = sector[sector["index"].ne("NIFTY 50")].copy()
    if sector.empty:
        return sector
    base = indices.loc[indices["index"].eq("NIFTY 50")].iloc[0]
    for p in ("1d", "1w", "1m"):
        sector[f"rs_{p}"] = sector[f"r{p}"] - base[f"r{p}"]
    sector["score"] = .2 * sector.rs_1d + .3 * sector.rs_1w + .5 * sector.rs_1m
    sector["rank"] = sector["score"].rank(ascending=False, method="min").astype("Int64")
    for col, base_col in (("rot_x", "oneMonthAgo"), ("rot_y", "oneWeekAgo")):
        sec_ref = sector[base_col].where(sector[base_col] > 0)
        base_ref = base.get(base_col)
        rel_now = sector["last"] / base.get("last") if pd.notna(base.get("last")) and base.get("last") else np.nan
        rel_ref = sec_ref / base_ref if pd.notna(base_ref) and base_ref else np.nan
        sector[col] = rel_now / rel_ref * 100
    def quadrant(row):
        x, y = row.rot_x, row.rot_y
        if pd.isna(x) or pd.isna(y): return "UNAVAILABLE"
        return ("LEADING" if y >= 100 else "WEAKENING") if x >= 100 else ("IMPROVING" if y >= 100 else "LAGGING")
    sector["quadrant"] = sector.apply(quadrant, axis=1)
    return sector.sort_values(["rank", "index"], na_position="last").reset_index(drop=True)


def flow_stats(cons):
    """Price-signed turnover proxy; reported turnover units are intentionally preserved."""
    if cons is None or cons.empty or not {"pChange", "totalTradedValue"}.issubset(cons.columns):
        return None
    data = cons.dropna(subset=["pChange", "totalTradedValue"])
    up = float(data.loc[data.pChange > 0, "totalTradedValue"].sum())
    down = float(data.loc[data.pChange < 0, "totalTradedValue"].sum())
    total = up + down
    return {"up_value": up, "down_value": down, "net_value": up-down,
            "net_pct": (up-down)/total*100 if total else np.nan,
            "up": int((data.pChange > 0).sum()), "down": int((data.pChange < 0).sum()),
            "flat": int((data.pChange == 0).sum()), "stocks": len(data)}


def stock_breadth(cons):
    """Count advancers/decliners within one selected index's returned constituents."""
    if cons is None or cons.empty or "pChange" not in cons:
        return None, pd.DataFrame()
    data = cons.copy()
    data["pChange"] = pd.to_numeric(data["pChange"], errors="coerce")
    data = data.dropna(subset=["pChange"])
    up, down, flat = int((data.pChange > 0).sum()), int((data.pChange < 0).sum()), int((data.pChange == 0).sum())
    total = len(data)
    ratio = up / down if down else (float(up) if up else np.nan)
    for c in ("nearWKH", "nearWKL"):
        if c in data: data[c] = pd.to_numeric(data[c], errors="coerce")
    high = int(data.nearWKH.between(0, 1).sum()) if "nearWKH" in data else None
    low = int(data.nearWKL.between(0, 1).sum()) if "nearWKL" in data else None
    stats = {"total": total, "up": up, "down": down, "flat": flat, "net": up-down,
             "ratio": ratio, "up_pct": up/total*100 if total else np.nan,
             "median": data.pChange.median() if total else np.nan, "near_high": high, "near_low": low}
    return stats, data


def _num(v, decimals=2):
    try: return "—" if pd.isna(v) else f"{float(v):,.{decimals}f}"
    except (ValueError, TypeError): return "—"


def _pct(v):
    try: return "—" if pd.isna(v) else f"{float(v):+.2f}%"
    except (ValueError, TypeError): return "—"


def _rotation_chart(data):
    data = data.dropna(subset=["rot_x", "rot_y"])
    if data.empty: st.info("Rotation plot unavailable: reference levels are missing."); return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        st.info("Install matplotlib for the rotation plot; the table remains available."); return
    fig, ax = plt.subplots(figsize=(9, 5))
    fig.patch.set_facecolor("#090b0f"); ax.set_facecolor("#090b0f")
    dx = max(float((data.rot_x-100).abs().max())*1.2, .6)
    dy = max(float((data.rot_y-100).abs().max())*1.2, .6)
    ax.set(xlim=(100-dx, 100+dx), ylim=(100-dy, 100+dy))
    ax.axhline(100, color="#39404b", lw=1); ax.axvline(100, color="#39404b", lw=1)
    for _, r in data.iterrows():
        ax.scatter(r.rot_x, r.rot_y, s=32, color=QUAD.get(r.quadrant, "#9ca3af"))
        ax.annotate(str(r["index"]).replace("NIFTY ", ""), (r.rot_x, r.rot_y), xytext=(4,3), textcoords="offset points", fontsize=6.5, color="#d7dce2")
    ax.set_xlabel("Relative trend · 1 month (100 = in line)", color="#9ca3af")
    ax.set_ylabel("Relative momentum · 1 week (100 = in line)", color="#9ca3af")
    ax.tick_params(colors="#9ca3af", labelsize=8)
    for spine in ax.spines.values(): spine.set_color("#303640")
    st.pyplot(fig, use_container_width=True); plt.close(fig)


def _index_table(df, key):
    cols = [c for c in ["index", "key", "last", "r1d", "r1w", "r1m", "advances", "declines", "unchanged", "net_adv", "adv_pct", "ad_ratio", "pe", "pb", "dy"] if c in df]
    view = df[cols].rename(columns={"index":"Index", "key":"Category", "last":"Last", "r1d":"1D %", "r1w":"1W %", "r1m":"1M %", "advances":"Adv", "declines":"Dec", "unchanged":"Flat", "net_adv":"Net Adv", "adv_pct":"Advancing %", "ad_ratio":"A/D", "pe":"P/E", "pb":"P/B", "dy":"Div Yield"})
    config = {c: st.column_config.NumberColumn(c, format="%+.2f%%") for c in ["1D %", "1W %", "1M %"]}
    config["Advancing %"] = st.column_config.ProgressColumn("Advancing %", min_value=0, max_value=100, format="%.0f%%")
    st.dataframe(view, hide_index=True, use_container_width=True, column_config=config, key=key)


def render_money_flow(on_open=None):
    """Unified Arka Money Flow workspace. Includes Market Breadth; no breadth_page dependency."""
    _style()
    st.markdown('<div class="arka-mf-kicker">ARKA MARKET TERMINAL · MARKET INTERNALS</div><div class="arka-mf-title">MONEY FLOW</div><div class="arka-mf-sub">Sector rotation, index and stock breadth, constituents, turnover pressure and disclosed FII/DII activity in one workspace.</div>', unsafe_allow_html=True)
    with st.spinner("Loading NSE index snapshot…"):
        indices = fetch_all_indices()
    if indices is None or indices.empty:
        st.error("NSE index data is unavailable. No placeholder values are shown.")
        if st.button("RETRY LIVE FEED", key="mf_retry_live"):
            fetch_all_indices.clear(); fetch_constituents.clear(); fetch_fii_dii.clear(); _session.clear(); st.rerun()
        st.caption("NSE public endpoint access can vary by network or response format.")
        return

    changes = indices.r1d.dropna()
    up, down, flat = int((changes > 0).sum()), int((changes < 0).sum()), int((changes == 0).sum())
    breadth_idx = indices.dropna(subset=["advances", "declines"])
    stamp = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%d %b %Y · %H:%M IST")
    cards = st.columns(5)
    cards[0].metric("Index records", len(indices))
    cards[1].metric("Indexes up", up)
    cards[2].metric("Indexes down", down)
    cards[3].metric("Index A/D", f"{up/down:.2f}" if down else (str(up) if up else "—"))
    cards[4].metric("Index records with A/D", len(breadth_idx))
    st.caption(f"Latest returned snapshot · {stamp} · index cache up to 60 seconds. Index rows overlap and do not represent unique stocks.")

    overview, rotation, breadth, flows, stocks = st.tabs(["OVERVIEW", "SECTOR ROTATION", "MARKET BREADTH", "MONEY FLOW / FII-DII", "INDEX CONSTITUENTS"])

    with overview:
        a, b = st.columns(2)
        movers = indices.dropna(subset=["r1d"]).sort_values("r1d", ascending=False)
        with a:
            st.markdown('<div class="arka-mf-section">TOP INDEX PERFORMERS · 1D</div>', unsafe_allow_html=True)
            st.dataframe(movers[["index", "key", "r1d", "r1w", "r1m"]].head(10).rename(columns={"index":"Index", "key":"Category", "r1d":"1D %", "r1w":"1W %", "r1m":"1M %"}), hide_index=True, use_container_width=True)
        with b:
            st.markdown('<div class="arka-mf-section">WEAKEST INDEX PERFORMERS · 1D</div>', unsafe_allow_html=True)
            st.dataframe(movers.sort_values("r1d")[["index", "key", "r1d", "r1w", "r1m"]].head(10).rename(columns={"index":"Index", "key":"Category", "r1d":"1D %", "r1w":"1W %", "r1m":"1M %"}), hide_index=True, use_container_width=True)
        st.markdown('<div class="arka-mf-section">INDEX PARTICIPATION BY CATEGORY</div>', unsafe_allow_html=True)
        cats = indices.groupby("key", dropna=False).agg(Indexes=("index","count"), Up=("r1d",lambda s:int((s>0).sum())), Down=("r1d",lambda s:int((s<0).sum())), Flat=("r1d",lambda s:int((s==0).sum())), Median_1D=("r1d","median"), Median_1W=("r1w","median"), Median_1M=("r1m","median")).reset_index().rename(columns={"key":"Category","Median_1D":"Median 1D %","Median_1W":"Median 1W %","Median_1M":"Median 1M %"})
        st.dataframe(cats, hide_index=True, use_container_width=True)

    with rotation:
        scope = st.radio("Universe", list(SCOPES), horizontal=True, key="mf_scope")
        sector = rank_sectors(indices, scope)
        if sector.empty:
            st.info("No matching index records or NIFTY 50 reference levels are available.")
        else:
            st.caption("Relative-strength score = 20% 1D + 30% 1W + 50% 1M performance versus NIFTY 50. This ranking is descriptive, not predictive.")
            cols = ["rank", "index", "key", "last", "r1d", "r1w", "r1m", "rs_1w", "rs_1m", "score", "advances", "declines", "quadrant"]
            view = sector[cols].rename(columns={"rank":"Rank", "index":"Index", "key":"Category", "last":"Last", "r1d":"1D %", "r1w":"1W %", "r1m":"1M %", "rs_1w":"RS 1W", "rs_1m":"RS 1M", "score":"RS Score", "advances":"Adv", "declines":"Dec", "quadrant":"Rotation"})
            st.dataframe(view, hide_index=True, use_container_width=True)
            st.markdown("**RELATIVE STRENGTH SCORE**")
            chart = sector.set_index("index").score.dropna().sort_values()
            if not chart.empty: st.bar_chart(chart, horizontal=True)
            st.markdown("**ROTATION QUADRANT · SIMPLIFIED RRG-STYLE MAP**")
            _rotation_chart(sector)

    with breadth:
        st.markdown('<div class="arka-mf-section">INDEX-LEVEL ADVANCE / DECLINE</div>', unsafe_allow_html=True)
        categories = ["ALL CATEGORIES"] + sorted(indices.key.dropna().astype(str).unique().tolist())
        cat = st.selectbox("Index category", categories, key="mf_breadth_category")
        idxdata = indices if cat == "ALL CATEGORIES" else indices[indices.key.astype(str).eq(cat)]
        usable = idxdata.dropna(subset=["advances", "declines"])
        if not usable.empty:
            vals = st.columns(4)
            vals[0].metric("Indexes with A/D", len(usable))
            vals[1].metric("Advancing majority", int((usable.advances > usable.declines).sum()))
            vals[2].metric("Declining majority", int((usable.advances < usable.declines).sum()))
            vals[3].metric("Median advancing %", _num(usable.adv_pct.median(), 1) + "%" if pd.notna(usable.adv_pct.median()) else "—")
            plot = idxdata.dropna(subset=["adv_pct"]).sort_values("adv_pct")
            if not plot.empty: st.bar_chart(plot.set_index("index").adv_pct, horizontal=True)
        _index_table(idxdata.sort_values("adv_pct", ascending=False, na_position="last"), "mf_index_breadth_table")

        st.markdown('<div class="arka-mf-section">STOCK-LEVEL BREADTH · SELECT ONE INDEX UNIVERSE</div>', unsafe_allow_html=True)
        names = indices.index.dropna().astype(str).tolist()
        preferred = next((i for i, n in enumerate(names) if n.upper() == "NIFTY 500"), 0)
        csel, cref = st.columns([4, 1])
        with csel: universe = st.selectbox("Constituent universe", names, index=preferred, key="mf_breadth_universe")
        with cref:
            st.write(""); st.write("")
            if st.button("REFRESH BREADTH", key="mf_breadth_refresh", use_container_width=True): fetch_constituents.clear(); st.rerun()
        cons = fetch_constituents(universe)
        stats, detail = stock_breadth(cons)
        if stats is None:
            st.warning("NSE returned no constituent data for this universe. Try another index or refresh later.")
        else:
            m = st.columns(5)
            m[0].metric("Stocks counted", stats["total"])
            m[1].metric("Advancing", stats["up"], f"{stats['up_pct']:.1f}%" if pd.notna(stats["up_pct"]) else None)
            m[2].metric("Declining", stats["down"])
            m[3].metric("Stock A/D ratio", _num(stats["ratio"]))
            m[4].metric("Net advances", f"{stats['net']:+d}")
            z = st.columns(4)
            z[0].metric("Unchanged", stats["flat"])
            z[1].metric("Median stock move", _pct(stats["median"]))
            z[2].metric("Within 1% of 52W high", "—" if stats["near_high"] is None else stats["near_high"])
            z[3].metric("Within 1% of 52W low", "—" if stats["near_low"] is None else stats["near_low"])
            counts = pd.DataFrame({"Stocks": [stats["up"], stats["down"], stats["flat"]]}, index=["Advancing", "Declining", "Unchanged"])
            st.bar_chart(counts)
            l, r = st.columns(2)
            fields = [c for c in ["symbol", "lastPrice", "pChange", "totalTradedVolume", "nearWKH", "nearWKL"] if c in detail]
            rename = {"symbol":"Symbol", "lastPrice":"Price", "pChange":"1D %", "totalTradedVolume":"Volume", "nearWKH":"% from 52W High", "nearWKL":"% from 52W Low"}
            with l:
                st.markdown("**TOP ADVANCERS**")
                st.dataframe(detail.sort_values("pChange", ascending=False)[fields].head(10).rename(columns=rename), hide_index=True, use_container_width=True)
            with r:
                st.markdown("**TOP DECLINERS**")
                st.dataframe(detail.sort_values("pChange")[fields].head(10).rename(columns=rename), hide_index=True, use_container_width=True)
            search, direction = st.columns([2, 1])
            query = search.text_input("Search constituents", key="mf_breadth_search").strip().upper()
            move = direction.selectbox("Filter", ["All", "Advancers", "Decliners", "Unchanged"], key="mf_breadth_filter")
            filtered = detail.copy()
            if query: filtered = filtered[filtered.symbol.astype(str).str.contains(query, na=False)]
            if move == "Advancers": filtered = filtered[filtered.pChange > 0]
            elif move == "Decliners": filtered = filtered[filtered.pChange < 0]
            elif move == "Unchanged": filtered = filtered[filtered.pChange == 0]
            fields = [c for c in ["symbol", "lastPrice", "pChange", "totalTradedVolume", "value_raw", "yearHigh", "yearLow", "nearWKH", "nearWKL"] if c in filtered]
            rename = {"symbol":"Symbol", "lastPrice":"Price", "pChange":"1D %", "totalTradedVolume":"Volume", "value_raw":"Traded Value (NSE units)", "yearHigh":"52W High", "yearLow":"52W Low", "nearWKH":"% from 52W High", "nearWKL":"% from 52W Low"}
            st.caption(f"Showing {len(filtered)} of {len(detail)} returned constituents. This is a live snapshot, not a historical breadth time series.")
            event = st.dataframe(filtered[fields].rename(columns=rename).reset_index(drop=True), hide_index=True, use_container_width=True,
                                 on_select="rerun" if on_open else "ignore", selection_mode="single-row" if on_open else "multi-row", key="mf_breadth_stock_table")
            selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
            if selected and on_open: on_open(str(filtered.reset_index(drop=True).iloc[int(selected[0])]["symbol"]))

    with flows:
        st.markdown('<div class="arka-mf-section">DISCLOSED MARKET-WIDE FII / DII CASH ACTIVITY</div>', unsafe_allow_html=True)
        fii = fetch_fii_dii()
        if fii is None or fii.empty:
            st.info("FII/DII feed is unavailable. No values are substituted.")
        else:
            metrics = st.columns(min(max(len(fii), 1), 4))
            for col, (_, row) in zip(metrics, fii.head(4).iterrows()):
                category = str(row.get("category", "Investor")).strip() or "Investor"
                date = str(row.get("date", "")).strip()
                col.metric(f"{category} · {date}" if date else category, _num(row.get("netValue")),
                           help=f"Buy: {_num(row.get('buyValue'))} · Sell: {_num(row.get('sellValue'))}")
            st.dataframe(fii, hide_index=True, use_container_width=True)
        st.markdown('<div class="arka-mf-section">SECTOR TURNOVER PRESSURE PROXY</div>', unsafe_allow_html=True)
        st.markdown('<div class="arka-mf-note">Proxy = traded value of stocks closing up minus value of stocks closing down. It is not actual institutional flow, not net order flow and not proof of accumulation. Turnover units are shown as returned by NSE.</div>', unsafe_allow_html=True)
        scope = st.radio("Flow universe", list(SCOPES), horizontal=True, key="mf_flow_scope")
        sectors = rank_sectors(indices, scope)
        if sectors.empty:
            st.info("No matching index rows are available for this scope.")
        else:
            available = sectors["index"].astype(str).tolist()
            default = available[:min(8, len(available))]
            chosen = st.multiselect("Indexes to calculate (select a small group to limit requests)", available, default=default, key="mf_flow_indexes")
            if st.button("COMPUTE / REFRESH FLOW PROXY", type="primary", key="mf_flow_compute", disabled=not chosen):
                result = {}
                progress = st.progress(0, text="Fetching constituent snapshots…")
                for i, name in enumerate(chosen):
                    result[name] = flow_stats(fetch_constituents(name))
                    progress.progress((i+1)/len(chosen), text=f"Processed {i+1} of {len(chosen)} indexes")
                progress.empty(); st.session_state["mf_flow_result"] = result
            result = st.session_state.get("mf_flow_result", {})
            rows = [{"Index": name, "Up Value": x["up_value"], "Down Value": x["down_value"], "Net Value": x["net_value"], "Net %": x["net_pct"], "Adv": x["up"], "Dec": x["down"], "Stocks": x["stocks"]} for name, x in result.items() if x]
            if rows:
                out = pd.DataFrame(rows).sort_values("Net %", ascending=False)
                st.dataframe(out, hide_index=True, use_container_width=True)
                st.bar_chart(out.set_index("Index")["Net %"], horizontal=True)
            elif result: st.warning("No flow proxy values returned for the selected indexes. Refresh the snapshot or try fewer indexes.")

    with stocks:
        index_names = indices["index"].dropna().astype(str).tolist()
        preferred = next((i for i, n in enumerate(index_names) if n.upper() == "NIFTY 50"), 0)
        a, b, c = st.columns([2, 2, 1])
        with a: index_name = st.selectbox("Index / constituent universe", index_names, index=preferred, key="mf_const_index")
        with b: query = st.text_input("Find stock", placeholder="e.g. RELIANCE", key="mf_const_search").strip().upper()
        with c: sort = st.selectbox("Sort", ["1D change", "Traded value", "Symbol"], key="mf_const_sort")
        cons = fetch_constituents(index_name)
        if cons is None or cons.empty:
            st.warning("NSE returned no constituents for this index.")
        else:
            view = cons.copy()
            if query: view = view[view.symbol.str.contains(query, na=False)]
            if sort == "1D change": view = view.sort_values("pChange", ascending=False, na_position="last")
            elif sort == "Traded value": view = view.sort_values("totalTradedValue", ascending=False, na_position="last")
            else: view = view.sort_values("symbol")
            fields = [c for c in ["symbol", "lastPrice", "pChange", "totalTradedVolume", "value_raw", "ffmc", "yearHigh", "yearLow", "nearWKH", "nearWKL"] if c in view]
            table = view[fields].rename(columns={"symbol":"Symbol", "lastPrice":"Price", "pChange":"1D %", "totalTradedVolume":"Volume", "value_raw":"Traded Value (NSE units)", "ffmc":"FF Mcap", "yearHigh":"52W High", "yearLow":"52W Low", "nearWKH":"% from 52W High", "nearWKL":"% from 52W Low"}).reset_index(drop=True)
            st.caption(f"{len(table)} rows returned for {index_name}; select a row to open Arka stock research.")
            event = st.dataframe(table, hide_index=True, use_container_width=True, on_select="rerun" if on_open else "ignore", selection_mode="single-row" if on_open else "multi-row", key="mf_constituents_table")
            selected = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
            if selected and on_open: on_open(str(table.iloc[int(selected[0])]["Symbol"]))

    st.caption("Data: NSE India public index, constituent and FII/DII endpoints, where accessible. All metrics reflect the latest returned snapshot; this is not a licensed real-time feed.")
    with st.expander("DATA SOURCES & METRIC DEFINITIONS"):
        st.markdown("""
        **Live index snapshot and index-level breadth:** [NSE Live Market Indices](https://www.nseindia.com/market-data/live-market-indices) — public `/api/allIndices` response.

        **Constituent prices, volume and turnover:** public `/api/equity-stockIndices?index=...` response. Reported turnover units are preserved as returned rather than assumed to be rupees or crore.

        **Disclosed market-wide investor activity:** [NSE FII/FPI & DII reports](https://www.nseindia.com/reports/fii-dii). These figures are market-wide, not sector-specific.

        **Sector index reference:** [NSE sectoral indices](https://www.nseindia.com/static/products-services/indices-sectoral) · [Nifty Indices sectoral list](https://www.niftyindices.com/indices/equity/sectoral-indices).

        **Interpretation:** relative-strength ranking compares returns with NIFTY 50. Rotation quadrants are simplified approximations, not a licensed JdK RRG. Turnover pressure is up-closing traded value minus down-closing traded value; it does not reveal buyer identity, net institutional orders, or prove accumulation. Index families overlap, and the live snapshot is not a historical breadth series.
        """)
