"""
money_flow.py — Sector rotation, index breadth and money-flow proxies (NSE data).

Sources (all NSE, no login, no paid API):
  /api/allIndices            every NSE index: last, 1d, week/month/year-ago levels,
                             PE/PB/DY, advances / declines / unchanged, category (key)
  /api/equity-stockIndices   constituents of one index with price, volume, traded value
  /api/fiidiiTradeReact      FII/FPI and DII cash-market buy / sell / net (end of day)

IMPORTANT — what "money flow" means here:
  NSE publishes no real institutional order data. The per-sector number is a PROXY:
  traded value of constituents that closed up minus traded value of those that
  closed down. FII/DII is the only true institutional figure, and it is
  market-wide (not by sector).

Missing data is shown as missing. Nothing is invented or estimated.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st

_NSE = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _NSE + "/market-data/live-market-indices",
}


# ── NSE session (cookie warm-up is mandatory or the API returns 401/403) ──
@st.cache_resource(ttl=600)
def _session():
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get(_NSE, timeout=10)
        s.get(_NSE + "/market-data/live-market-indices", timeout=10)
    except Exception:
        pass
    return s


def _get(path):
    for _ in range(2):
        try:
            r = _session().get(_NSE + path, timeout=12)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        _session.clear()          # cookie expired / blocked -> rebuild and retry once
        time.sleep(0.6)
    return None


def _to_num(df, cols):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan


# ── Indices: levels, returns, breadth ────────────────────────────
_IDX_NUM = ["last", "percentChange", "pe", "pb", "dy", "advances", "declines", "unchanged",
            "oneWeekAgo", "oneMonthAgo", "oneYearAgo", "yearHigh", "yearLow"]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_all_indices():
    j = _get("/api/allIndices")
    if not j or not j.get("data"):
        return None
    df = pd.DataFrame(j["data"])
    _to_num(df, _IDX_NUM)
    if "key" not in df:
        df["key"] = "OTHER"
    df["key"] = df["key"].fillna("OTHER").astype(str)
    df["r1d"] = df["percentChange"]
    df["r1w"] = (df["last"] / df["oneWeekAgo"] - 1) * 100
    df["r1m"] = (df["last"] / df["oneMonthAgo"] - 1) * 100
    df["r1y"] = (df["last"] / df["oneYearAgo"] - 1) * 100
    total = df[["advances", "declines", "unchanged"]].sum(axis=1).replace(0, np.nan)
    df["adv_pct"] = df["advances"] / total * 100
    df["ad_ratio"] = df["advances"] / df["declines"].replace(0, np.nan)
    return df.replace([np.inf, -np.inf], np.nan)


_SCOPES = {
    "SECTORAL": ("SECTORAL",),
    "THEMATIC": ("THEMATIC",),
    "SECTORAL + THEMATIC": ("SECTORAL", "THEMATIC"),
    "ALL INDICES": (),
}


def rank_sectors(df, scope="SECTORAL"):
    """Relative strength vs NIFTY 50 (score) + rotation quadrant coordinates."""
    keys = _SCOPES.get(scope, ("SECTORAL",))
    if keys:
        mask = df["key"].str.upper().apply(lambda k: any(w in k for w in keys))
        sec = df[mask].copy()
    else:
        sec = df.copy()
    sec = sec[sec["index"] != "NIFTY 50"]
    base = df[df["index"] == "NIFTY 50"]
    if sec.empty or base.empty:
        return sec
    b = base.iloc[0]
    for c in ("r1d", "r1w", "r1m"):
        sec["rs_" + c[1:]] = sec[c] - b[c]
    sec["score"] = 0.2 * sec["rs_1d"] + 0.3 * sec["rs_1w"] + 0.5 * sec["rs_1m"]
    sec["rank"] = sec["score"].rank(ascending=False, method="min").fillna(0).astype(int)

    # Rotation quadrant (RRG-style approximation from NSE's own reference levels).
    # RS line = index level / NIFTY 50 level.
    #   x (trend)    = RS now / RS one month ago * 100   -> >100 = outperforming over the month
    #   y (momentum) = RS now / RS one week ago  * 100   -> >100 = still gaining this week
    rs_now = sec["last"] / b["last"]
    rs_1w = sec["oneWeekAgo"] / b["oneWeekAgo"]
    rs_1m = sec["oneMonthAgo"] / b["oneMonthAgo"]
    sec["rot_x"] = rs_now / rs_1m * 100
    sec["rot_y"] = rs_now / rs_1w * 100

    def quad(r):
        if pd.isna(r["rot_x"]) or pd.isna(r["rot_y"]):
            return "—"
        if r["rot_x"] >= 100:
            return "LEADING" if r["rot_y"] >= 100 else "WEAKENING"
        return "IMPROVING" if r["rot_y"] >= 100 else "LAGGING"

    sec["quadrant"] = sec.apply(quad, axis=1)
    return sec.sort_values("rank")


# ── Constituents + money-flow proxy ──────────────────────────────
_CONS_NUM = ["lastPrice", "pChange", "totalTradedVolume", "totalTradedValue",
             "ffmc", "yearHigh", "yearLow"]


def _load_constituents(index_name):
    j = _get(f"/api/equity-stockIndices?index={quote(index_name)}")
    if not j or not j.get("data"):
        return None
    df = pd.DataFrame(j["data"])
    df = df[df["symbol"].astype(str).str.upper() != index_name.upper()]   # drop the index's own row
    if "priority" in df:
        df = df[pd.to_numeric(df["priority"], errors="coerce").fillna(0) == 0]
    _to_num(df, _CONS_NUM)
    # NOTE: verify on first run that totalTradedValue is in rupees (÷1e7 = crore).
    df["value_cr"] = df["totalTradedValue"] / 1e7
    return df.reset_index(drop=True)


fetch_constituents = st.cache_data(ttl=120, show_spinner=False)(_load_constituents)


def flow_stats(cons):
    up = cons.loc[cons["pChange"] > 0, "totalTradedValue"].sum()
    dn = cons.loc[cons["pChange"] < 0, "totalTradedValue"].sum()
    tot = up + dn
    return {"up_cr": up / 1e7, "down_cr": dn / 1e7, "net_cr": (up - dn) / 1e7,
            "net_pct": (up - dn) / tot * 100 if tot else np.nan}


@st.cache_data(ttl=180, show_spinner=False)
def fetch_sector_flows(names):
    def one(n):
        c = _load_constituents(n)          # uncached inner call: safe inside worker threads
        return n, (flow_stats(c) if c is not None and len(c) else None)
    with ThreadPoolExecutor(max_workers=4) as ex:   # NSE throttles hard; keep this low
        return dict(ex.map(one, names))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_fii_dii():
    j = _get("/api/fiidiiTradeReact")
    if not j:
        return None
    df = pd.DataFrame(j)
    for c in ("buyValue", "sellValue", "netValue"):
        if c in df:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    return df


_QUAD_COL = {"LEADING": "#30D158", "WEAKENING": "#FF9F0A", "LAGGING": "#FF453A", "IMPROVING": "#5AC8FA"}


def _rotation_chart(sec):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = sec.dropna(subset=["rot_x", "rot_y"])
    if d.empty:
        return
    st.markdown('<div class="panel-title" style="margin-top:14px">SECTOR ROTATION QUADRANT · VS NIFTY 50</div>',
                unsafe_allow_html=True)
    st.caption("X = relative trend over 1 month · Y = relative momentum over 1 week · 100 = in line with NIFTY 50. "
               "Approximation built from NSE's week/month-ago levels, not a full JdK RRG.")
    fig, ax = plt.subplots(figsize=(9, 6))
    fig.patch.set_facecolor("#050505"); ax.set_facecolor("#0a0a0a")
    pad_x = max(abs(d["rot_x"] - 100).max() * 1.25, 0.5)
    pad_y = max(abs(d["rot_y"] - 100).max() * 1.25, 0.5)
    ax.set_xlim(100 - pad_x, 100 + pad_x); ax.set_ylim(100 - pad_y, 100 + pad_y)
    ax.axhline(100, color="#333", lw=1); ax.axvline(100, color="#333", lw=1)
    for (qx, qy, name) in ((.98, .98, "LEADING"), (.98, .02, "WEAKENING"), (.02, .02, "LAGGING"), (.02, .98, "IMPROVING")):
        ax.text(qx, qy, name, transform=ax.transAxes, ha="right" if qx > .5 else "left",
                va="top" if qy > .5 else "bottom", color=_QUAD_COL[name], alpha=.55, fontsize=9, fontweight="bold")
    for _, r in d.iterrows():
        c = _QUAD_COL.get(r["quadrant"], "#888")
        ax.scatter(r["rot_x"], r["rot_y"], s=34, color=c, zorder=3)
        ax.annotate(str(r["index"]).replace("NIFTY ", ""), (r["rot_x"], r["rot_y"]), fontsize=6.5,
                    color="#d8d8d8", xytext=(4, 3), textcoords="offset points")
    ax.tick_params(colors="#777", labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#262626")
    ax.set_xlabel("RELATIVE TREND (1M)", color="#777", fontsize=8)
    ax.set_ylabel("RELATIVE MOMENTUM (1W)", color="#777", fontsize=8)
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)


# ── UI ───────────────────────────────────────────────────────────
_PCT = lambda label: st.column_config.NumberColumn(label, format="%.2f%%")


def render_money_flow(on_open=None):
    """on_open(symbol) -> opens the security workspace (pass app.py's _open_security)."""
    st.markdown('<div class="panel-title">MONEY FLOW · SECTOR ROTATION · INDEX BREADTH</div>',
                unsafe_allow_html=True)
    df = fetch_all_indices()
    if df is None:
        st.error("NSE index feed unreachable (NSE sometimes blocks cloud IPs). Nothing is shown rather than guessing.")
        if st.button("RETRY", key="mf_retry"):
            fetch_all_indices.clear(); _session.clear(); st.rerun()
        return

    scope = st.radio("Universe", list(_SCOPES), horizontal=True, key="mf_scope")
    t1, t2, t3, t4 = st.tabs(["SECTOR RANKING", "INDEX BREADTH (A/D)", "MONEY FLOW", "CONSTITUENTS"])

    # 1 ── sector ranking
    with t1:
        sec = rank_sectors(df, scope)
        if sec.empty:
            st.info("No sectoral indices returned.")
        else:
            st.caption("Score = relative strength vs NIFTY 50 · 20% 1D + 30% 1W + 50% 1M. Positive = outperforming.")
            v = sec[["rank", "index", "last", "r1d", "r1w", "r1m", "rs_1w", "rs_1m", "score",
                     "advances", "declines", "quadrant"]].rename(columns={
                "rank": "Rank", "index": "Sector Index", "last": "Last", "r1d": "1D", "r1w": "1W",
                "r1m": "1M", "rs_1w": "RS 1W", "rs_1m": "RS 1M", "score": "Score",
                "advances": "Adv", "declines": "Dec", "quadrant": "Rotation"})
            st.dataframe(v, hide_index=True, use_container_width=True, column_config={
                "Last": st.column_config.NumberColumn(format="%.2f"),
                "1D": _PCT("1D"), "1W": _PCT("1W"), "1M": _PCT("1M"),
                "RS 1W": st.column_config.NumberColumn(format="%.2f"),
                "RS 1M": st.column_config.NumberColumn(format="%.2f"),
                "Score": st.column_config.NumberColumn(format="%.2f")})
            st.bar_chart(sec.set_index("index")["score"])
            _rotation_chart(sec)

    # 2 ── advance / decline for every index
    with t2:
        cats = ["ALL"] + sorted(df["key"].unique())
        cat = st.selectbox("Category", cats, key="mf_cat")
        v = df if cat == "ALL" else df[df["key"] == cat]
        v = v.sort_values("adv_pct", ascending=False)[
            ["index", "key", "last", "r1d", "advances", "declines", "unchanged", "adv_pct", "ad_ratio", "pe", "pb", "dy"]
        ].rename(columns={"index": "Index", "key": "Category", "last": "Last", "r1d": "1D",
                          "advances": "Adv", "declines": "Dec", "unchanged": "Unch",
                          "adv_pct": "% Advancing", "ad_ratio": "A/D", "pe": "PE", "pb": "PB", "dy": "DY"})
        st.caption(f"{len(v)} indices")
        st.dataframe(v, hide_index=True, use_container_width=True, column_config={
            "1D": _PCT("1D"), "% Advancing": st.column_config.ProgressColumn(
                "% Advancing", min_value=0, max_value=100, format="%.0f%%"),
            "Last": st.column_config.NumberColumn(format="%.2f"),
            "A/D": st.column_config.NumberColumn(format="%.2f")})

    # 3 ── money flow
    with t3:
        fd = fetch_fii_dii()
        st.markdown('<div class="panel-title">FII / DII · CASH MARKET · ₹ CR · LATEST SESSION</div>', unsafe_allow_html=True)
        if fd is None or fd.empty:
            st.info("FII/DII feed unavailable right now.")
        else:
            cols = st.columns(max(len(fd), 1))
            for col, (_, r) in zip(cols, fd.iterrows()):
                col.metric(f"{str(r.get('category', '')).strip()} · {r.get('date', '')}",
                           f"{r['netValue']:,.0f}" if pd.notna(r.get("netValue")) else "—",
                           help=f"Buy {r.get('buyValue')} · Sell {r.get('sellValue')}")
        st.markdown('<div class="panel-title" style="margin-top:14px">SECTOR MONEY FLOW (PRICE-SIGNED TURNOVER PROXY)</div>',
                    unsafe_allow_html=True)
        st.caption("Traded value of stocks closing up minus closing down, per sectoral index. A proxy — not actual institutional orders.")
        sec = rank_sectors(df, scope)
        if st.button("COMPUTE SECTOR FLOW", type="primary", key="mf_flow_btn") and not sec.empty:
            with st.spinner(f"Pulling constituents for {len(sec)} sectors…"):
                st.session_state["mf_flows"] = fetch_sector_flows(tuple(sec["index"]))
        flows = st.session_state.get("mf_flows")
        if flows:
            rows = [{"Sector Index": n, "Up Value (Cr)": f["up_cr"], "Down Value (Cr)": f["down_cr"],
                     "Net (Cr)": f["net_cr"], "Net %": f["net_pct"]}
                    for n, f in flows.items() if f]
            missing = [n for n, f in flows.items() if not f]
            if rows:
                out = pd.DataFrame(rows).sort_values("Net %", ascending=False)
                st.dataframe(out, hide_index=True, use_container_width=True, column_config={
                    c: st.column_config.NumberColumn(format="%.1f") for c in out.columns[1:]})
                st.bar_chart(out.set_index("Sector Index")["Net %"])
            if missing:
                st.caption(f"No data returned for: {', '.join(missing)}")

    # 4 ── constituents of any index
    with t4:
        names = list(df["index"])
        default = names.index("NIFTY 50") if "NIFTY 50" in names else 0
        idx = st.selectbox("Index", names, index=default, key="mf_idx")
        cons = fetch_constituents(idx)
        if cons is None or cons.empty:
            st.info("Constituents unavailable for this index right now.")
        else:
            v = cons[["symbol", "lastPrice", "pChange", "value_cr", "ffmc", "yearHigh", "yearLow"]].rename(columns={
                "symbol": "Symbol", "lastPrice": "Price", "pChange": "1D", "value_cr": "Traded (Cr)",
                "ffmc": "FF Mcap", "yearHigh": "52W High", "yearLow": "52W Low"}).sort_values("1D", ascending=False)
            st.caption(f"{len(v)} stocks · click a row to open the security workspace")
            ev = st.dataframe(v.reset_index(drop=True), hide_index=True, use_container_width=True,
                              on_select="rerun", selection_mode="single-row", key=f"mf_cons_{idx}",
                              column_config={"1D": _PCT("1D")})
            rows = ev.selection.rows if ev is not None else []
            if rows and on_open:
                on_open(str(v.reset_index(drop=True).iloc[rows[0]]["Symbol"]))
