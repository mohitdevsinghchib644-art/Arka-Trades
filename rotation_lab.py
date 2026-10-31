"""
rotation_lab.py — Arka ROTATION LAB (UI). Sector-rotation lenses, real FPI sector flows and a full backtest.

Tabs
  1 · NOW           where each sector sits today: stage, lane, score, momentum table, weekly rank heatmap,
                    cyclical-vs-defensive risk appetite, sector dispersion
  2 · BACKTEST      pick a rule (momentum, dual momentum, Arka score, RRG lane, ROTATING IN) and test it
                    against NIFTY 50 and an equal-weight basket of all sectors, after costs, with a 1-day lag
  3 · ROBUSTNESS    parameter heat-map, walk-forward (out-of-sample), random-pick baseline, cost / delay sensitivity
  4 · SIGNAL TESTS  does a high score / a given stage actually lead to better NEXT-month returns?
  5 · FPI FLOWS     NSDL fortnightly sector-wise FPI net investment — actual foreign money in/out of each sector
  HOW IT WORKS      plain-English methodology and the known biases

Data
  NSE OFFICIAL SECTOR INDICES  Yahoo daily history (10+ years for most). Best for backtests: no survivorship bias.
  ARKA STOCK-BUILT SECTORS     the sector indices built in SECTOR RANKING (st.session_state["mf_sr_res"]).
                               Adds breadth + money-flow legs to the score, but only ~2 years and survivorship-biased.

All maths lives in rotation_backtest.py (pure, unit-tested). Nothing here is investment advice.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd
import requests
import streamlit as st

import mf_ui as ui
import rotation_backtest as rb

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None
try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None

# Same palette as the rest of Money Flow (mf_ui)
C = {"bg": "#000000", "panel": "#090909", "border": ui.BORDER, "amber": ui.AMBER, "ivory": ui.IVORY, "t2": ui.T2,
     "t3": ui.T3, "green": ui.GREEN, "red": ui.RED, "cyan": ui.CYAN, "purple": ui.PURPLE, "yellow": ui.YELLOW,
     "mono": "'JetBrains Mono','Consolas',monospace"}
STAGE_C = ui.STAGE_COLORS

# Official NSE sectoral indices on Yahoo. Tickers that fail are skipped and reported.
CORE = {"NIFTY BANK": "^NSEBANK", "NIFTY IT": "^CNXIT", "NIFTY AUTO": "^CNXAUTO", "NIFTY FMCG": "^CNXFMCG",
        "NIFTY PHARMA": "^CNXPHARMA", "NIFTY METAL": "^CNXMETAL", "NIFTY REALTY": "^CNXREALTY", "NIFTY ENERGY": "^CNXENERGY",
        "NIFTY MEDIA": "^CNXMEDIA", "NIFTY PSU BANK": "^CNXPSUBANK", "NIFTY INFRA": "^CNXINFRA",
        "NIFTY FIN SERVICE": "NIFTY_FIN_SERVICE.NS", "NIFTY HEALTHCARE": "NIFTY_HEALTHCARE.NS",
        "NIFTY OIL & GAS": "NIFTY_OIL_AND_GAS.NS", "NIFTY CONSUMER DURABLES": "NIFTY_CONSR_DURBL.NS",
        "NIFTY PVT BANK": "NIFTY_PVT_BANK.NS"}
THEMATIC = {"NIFTY PSE": "^CNXPSE", "NIFTY MNC": "^CNXMNC", "NIFTY SERVICES": "^CNXSERVICE",
            "NIFTY COMMODITIES": "^CNXCMDT", "NIFTY CONSUMPTION": "^CNXCONSUM"}
BENCH_T = "^NSEI"


# ═════════════════════════ styling / small UI helpers ═════════════════════════
def _css():
    ui.inject()
    # section / note classes are also defined by money_flow._style(); repeated here so the Lab works standalone
    st.markdown("""<style>
    .arka-mf-section{font:700 11px monospace;letter-spacing:1px;color:#d1d7e0;margin:17px 0 8px;padding-bottom:7px;border-bottom:1px solid #292e36}
    .arka-mf-note{font-size:11px;color:#929aa6;border-left:2px solid #5a6472;padding:7px 10px;background:rgba(255,255,255,.025);margin:7px 0 12px;line-height:1.65}
    .rl-verdict{font:12px 'Inter',sans-serif;color:#d8d8d8;border:1px solid;border-left-width:3px;padding:11px 15px;margin:8px 0 12px;line-height:1.85;background:#0b0b0b}
    .rl-verdict .x{color:#6f6f6f;font-size:11px}
    </style>""", unsafe_allow_html=True)


def _h(t):
    st.markdown(f'<div class="arka-mf-section">{t}</div>', unsafe_allow_html=True)


def _note(t):
    st.markdown(f'<div class="arka-mf-note">{t}</div>', unsafe_allow_html=True)


def _kpis(items, cols=None):
    """items: (label, value, sub, accent) — rendered with the Money Flow KPI strip."""
    st.markdown(ui.kpi_strip([(l, ui.esc(v), ui.esc(s), a or C["t2"]) for l, v, s, a in items]), unsafe_allow_html=True)


def _g(v, fmt="{:+.1f}%"):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)


def _layout(fig, h=320, **kw):
    ui._dark(fig, h)
    if kw:
        fig.update_layout(**kw)
    fig.update_xaxes(showgrid=False, linecolor=C["border"])
    fig.update_yaxes(gridcolor="#161616", zeroline=False)
    return fig


def _recs(df: pd.DataFrame) -> list:
    """DataFrame -> list of dicts with NaN turned into None (what mf_ui expects)."""
    return [{k: (None if (not isinstance(v, (list, tuple, dict)) and pd.isna(v)) else v) for k, v in r.items()}
            for r in df.to_dict("records")]


def _show(fig, key, fallback=None):
    if fig is None:
        if fallback is not None:
            st.line_chart(fallback)
        return
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


def _hbar(labels, vals, key, h=None, suffix="%"):
    if go is None:
        st.bar_chart(pd.Series(vals, index=labels), horizontal=True)
        return
    fig = go.Figure(go.Bar(x=vals, y=labels, orientation="h", marker_color=[C["green"] if v >= 0 else C["red"] for v in vals],
                           text=[f"{v:+,.0f}{suffix}" for v in vals], textposition="outside", cliponaxis=False,
                           textfont=dict(size=9, color=ui.T2), hovertemplate="<b>%{y}</b> %{x:+,.0f}" + suffix + "<extra></extra>"))
    ui._dark(fig, h or max(220, 22 * len(labels) + 40), False, (4, 60, 6, 4))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=10, color=ui.IVORY))
    fig.update_xaxes(gridcolor="#161616", zeroline=True, zerolinecolor="#444")
    _show(fig, key)


# ═════════════════════════ data ═════════════════════════
def _closes(raw, tickers):
    out = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in set(raw.columns.get_level_values(0)):
            raw = raw.swaplevel(axis=1).sort_index(axis=1)
        top = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in top and "Close" in raw[t].columns:
                out[t] = raw[t]["Close"]
    elif "Close" in raw.columns and len(tickers) == 1:
        out[tickers[0]] = raw["Close"]
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=4)
def load_official(pairs: tuple, period: str = "max"):
    """({name: ticker}) -> (prices DataFrame, NIFTY 50 Series, report dict). Raises if NIFTY 50 is missing."""
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    names = dict(pairs)
    tickers = list(dict.fromkeys(list(names.values()) + [BENCH_T]))
    raw = yf.download(tickers, period=period, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    cl = _closes(raw, tickers)
    if BENCH_T not in cl:
        raise RuntimeError("Yahoo returned no NIFTY 50 history")
    series = {n: cl[t] for n, t in names.items() if t in cl and cl[t].dropna().shape[0] >= 300}
    prices, n_fix = rb.tidy_prices(pd.DataFrame(series))
    bench, _ = rb.tidy_prices(cl[BENCH_T].to_frame("NIFTY 50"))
    bench = bench["NIFTY 50"].dropna()
    last = prices.apply(lambda c: c.last_valid_index())
    newest = last.max()
    stale = [c for c in prices.columns if last[c] is None or (newest - last[c]).days > 10]
    prices = prices.drop(columns=stale)
    report = {"missing": [n for n in names if n not in series], "stale": stale, "repaired_ticks": n_fix,
              "first": {c: str(prices[c].first_valid_index())[:10] for c in prices.columns}, "asof": str(newest)[:10]}
    return prices, bench, report


@st.cache_data(show_spinner=False, max_entries=12)
def _signals(prices, bench, breadth, cmf, share, lookback, skip):
    return rb.compute_signals(prices, bench, breadth, cmf, share, lookback, skip)


def _data():
    """Returns dict(prices, bench, bench_name, breadth, cmf, share, source, warn) or None."""
    src = st.radio("Sector data", ["NSE OFFICIAL SECTOR INDICES", "ARKA STOCK-BUILT SECTORS"], horizontal=True, key="rl_src",
                   help="Official indices: 10+ years, no survivorship bias — use these for backtests. "
                        "Stock-built: the sectors from SECTOR RANKING (adds breadth + flow to the score) but only ~2 years.")
    if src.startswith("ARKA"):
        res = st.session_state.get("mf_sr_res")
        if res is None:
            st.info("Open SECTOR RANKING and press LOAD / REFRESH SECTOR DATA first; the Lab reuses those sector indices.")
            return None
        benches = [b for b in res["bench_index"].columns]
        bname = st.radio("Benchmark", benches, horizontal=True, key="rl_bench_sb")
        return {"prices": res["sector_index"], "bench": res["bench_index"][bname].dropna(), "bench_name": bname,
                "breadth": res.get("ab20_series"), "cmf": res.get("cmf_series_full"), "share": res.get("share_series_full"),
                "source": f"Arka stock-built sectors · {res['n_stocks']} stocks · as of {res['asof']}",
                "warn": "Built from TODAY's constituent lists (survivorship bias flatters backtests) and only ~2 years of history: "
                        "treat backtest numbers here as indicative only. Use NSE OFFICIAL SECTOR INDICES for the real test."}
    c1, c2, c3 = st.columns([2.2, 1.4, 1.2])
    with c1:
        use_them = st.checkbox("Include thematic indices (PSE, MNC, Services, Commodities, Consumption)", key="rl_them")
    with c2:
        period = st.selectbox("History", ["max", "15y", "10y", "5y"], key="rl_period")
    with c3:
        st.write("")
        if st.button("LOAD / REFRESH", type="primary", use_container_width=True, key="rl_load"):
            if st.session_state.get("rl_ready"):
                load_official.clear()
            st.session_state["rl_ready"] = True
    if not st.session_state.get("rl_ready"):
        _note("Press LOAD to download daily history for the official NSE sector indices from Yahoo (≈20 tickers, one batch, cached 6 hours).")
        return None
    pairs = tuple(sorted({**CORE, **(THEMATIC if use_them else {})}.items()))
    with st.spinner("Downloading sector index history…"):
        try:
            prices, bench, rep = load_official(pairs, period)
        except Exception as exc:
            st.error(f"Sector history unavailable: {exc}. Yahoo may be rate-limiting; try again in a minute.")
            return None
    if prices.shape[1] < 5:
        st.error("Fewer than 5 sector indices returned data — not enough for rotation analysis.")
        return None
    msg = []
    if rep["missing"]:
        msg.append("no data: " + ", ".join(rep["missing"]))
    if rep["stale"]:
        msg.append("stale (dropped): " + ", ".join(rep["stale"]))
    if rep["repaired_ticks"]:
        msg.append(f"{rep['repaired_ticks']} bad ticks repaired")
    return {"prices": prices, "bench": bench, "bench_name": "NIFTY 50", "breadth": None, "cmf": None, "share": None,
            "source": f"{prices.shape[1]} official NSE indices · Yahoo daily · as of {rep['asof']}" + (f" · {'; '.join(msg)}" if msg else ""),
            "warn": "Thematic indices overlap with sectors (e.g. PSE contains PSU banks); keep them off for a clean test." if use_them else ""}


# ═════════════════════════ tab 1 · NOW ═════════════════════════
def _tab_now(sig, D):
    snap = rb.snapshot(sig)
    t = sig["P"].index[-1]
    counts = snap["stage"].value_counts()
    _kpis([(("★ " if s == "ROTATING IN" else "") + s.title(), int(counts.get(s, 0)),
             ", ".join(snap.loc[snap.stage == s, "sector"].head(3).str.replace("NIFTY ", "")) or "none right now",
             STAGE_C[s]) for s in rb.STAGE_ORDER])
    recs = _recs(snap)
    _h(f"ROTATION BOARD · EVERY SECTOR IN ITS LANE vs {ui.esc(D['bench_name'])} · {str(t)[:10]}")
    st.markdown(ui.how_to_read(D["bench_name"]).replace(" The arrow shows where the sector moved in the last 10 sessions.", ""),
                unsafe_allow_html=True)
    st.markdown(ui.rotation_board(recs), unsafe_allow_html=True)

    _h("DETAILS · THE NUMBERS BEHIND EACH STAGE")
    ui.show_table([ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="quadrant", sub_fmt="{} lane"),
                   ui.C("Stage", "stage", "stage"), ui.C("Score", "score", "score", tip="0-100 Arka rotation score (legs that need breadth/flow data are skipped for official indices)"),
                   ui.C("Trend", "rrg_x", "num", d=1, tip=f"100 × (sector ÷ {D['bench_name']}) ÷ its 50-session average; above 100 = beating it"),
                   ui.C("Momentum", "rrg_y", "num", d=1, tip="Trend ÷ its value 10 sessions ago × 100; above 100 = speeding up"),
                   ui.C("1D", "r1d", "pct", scale=2.0), ui.C("1W", "r1w", "pct", scale=4.0), ui.C("1M", "r1m", "pct", scale=8.0),
                   ui.C("RSI", "rsi_d", "rsi"), ui.C("EMA 10·20·50·200", t="ema"),
                   ui.C("vs 10", "dist10", "dist", scale=5.0), ui.C("vs 20", "dist20", "dist")], recs, max_height=640)

    _h("MOMENTUM LADDER · 1M / 3M / 6M / 12-1M AND RISK-ADJUSTED")
    mt = rb.momentum_table(sig)
    if not mt.empty:
        ui.show_table([ui.C("Rank", "Composite rank", "int"), ui.C("Sector", "sector", "sector", sub="group"),
                       ui.C("1M", "1M %", "pct", scale=8.0), ui.C("3M", "3M %", "pct", scale=15.0), ui.C("6M", "6M %", "pct", scale=25.0),
                       ui.C("12-1M", "12-1M %", "pct", scale=40.0, tip="12 months ago → 1 month ago"),
                       ui.C(f"3M vs {D['bench_name']}", "3M vs bench pp", "pct", scale=10.0),
                       ui.C("Volatility", "Vol %", "num", d=1, suf="%"), ui.C("6M ÷ vol", "6M ÷ vol", "num", d=2)], _recs(mt), max_height=620)
        _note("12-1M = return from 12 months ago to 1 month ago — the classic academic momentum measure (skips the noisy last month). "
              "6M ÷ vol rewards steady trends over jumpy ones. Composite rank averages the four horizons; rank 1 = strongest.")

    _h("ROTATION HEAT-MAP · WEEKLY RANK OF 1-MONTH RELATIVE RETURN (1 = STRONGEST)")
    weeks = st.slider("Weeks", 8, 52, 26, key="rl_hm_weeks")
    rk = rb.rank_history(sig, 21, weeks)
    if go is not None and not rk.empty:
        n = rk.shape[0]
        fig = go.Figure(go.Heatmap(z=rk.values, x=list(rk.columns), y=[s.replace("NIFTY ", "") for s in rk.index],
                                   colorscale=[[0, C["green"]], [0.5, "#151515"], [1, C["red"]]], zmin=1, zmax=n, showscale=False,
                                   text=rk.values.astype(int), texttemplate="%{text}", hovertemplate="%{y} · week %{x}: rank %{z}<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        _show(_layout(fig, 24 * n + 60), "rl_heat")
    _note("Read rows left → right. A sector turning from red to green over several weeks is money rotating IN; green turning red is rotating OUT. "
          "Sorted by this week's rank.")

    a, b = st.columns(2)
    with a:
        _h("RISK APPETITE · CYCLICALS ÷ DEFENSIVES")
        ra = rb.risk_appetite(sig)
        if ra.empty:
            st.info("Need at least one cyclical and one defensive sector.")
        else:
            x = ra.tail(504)
            if go is not None:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=x.index, y=x["ratio"], name="ratio", line=dict(color=C["amber"], width=2)))
                fig.add_trace(go.Scatter(x=x.index, y=x["sma50"], name="50-day avg", line=dict(color=C["cyan"], width=1)))
                fig.add_trace(go.Scatter(x=x.index, y=x["sma200"], name="200-day avg", line=dict(color=C["t3"], width=1, dash="dot")))
                _show(_layout(fig, 280), "rl_ra")
            last = ra.iloc[-1]
            mood = "RISK-ON (cyclicals leading)" if last["ratio"] > last["sma50"] else "RISK-OFF (defensives leading)"
            st.caption(f"Now: **{mood}**. Cyclical: {', '.join(ra.attrs['cyclical'])}. Defensive: {', '.join(ra.attrs['defensive'])}.")
    with b:
        _h("SECTOR DISPERSION · HOW FAR APART SECTORS ARE MOVING")
        dp = rb.dispersion(sig).tail(504)
        if go is not None and len(dp):
            fig = go.Figure(go.Scatter(x=dp.index, y=dp.values, line=dict(color=C["purple"], width=1.5), fill="tozeroy",
                                       fillcolor="rgba(191,90,242,.08)", name="dispersion"))
            fig.add_hline(y=float(dp.median()), line=dict(color=C["t3"], dash="dot"))
            _show(_layout(fig, 280), "rl_disp")
            st.caption(f"Now {dp.iloc[-1]:.1f} pp vs median {dp.median():.1f} pp. High dispersion = picking the right sector matters more; "
                       "low = sectors move together and rotation adds little.")


# ═════════════════════════ tab 2 · BACKTEST ═════════════════════════
def _controls(prefix="rl_bt"):
    c = st.columns([1.6, 0.8, 1, 0.8, 1, 0.9])
    strat = c[0].selectbox("Rule", [s for s in rb.STRATEGIES if s != "EQUAL WEIGHT"], key=f"{prefix}_s",
                           help="\n\n".join(f"{k}: {v}" for k, v in rb.STRATEGIES.items()))
    top_n = c[1].number_input("Top N", 1, 8, 3, key=f"{prefix}_n")
    lb = c[2].selectbox("Momentum lookback", [21, 42, 63, 126, 189, 252], index=2, key=f"{prefix}_lb",
                        format_func=lambda v: f"{v} d (~{round(v / 21)}M)")
    skip = c[3].selectbox("Skip last", [0, 5, 21], key=f"{prefix}_skip", format_func=lambda v: f"{v} d")
    reb = c[4].selectbox("Rebalance", ["M", "2W", "W"], key=f"{prefix}_reb", format_func=lambda v: {"M": "Monthly", "2W": "Fortnightly", "W": "Weekly"}[v])
    cost = c[5].number_input("Cost bps/side", 0.0, 100.0, 15.0, 5.0, key=f"{prefix}_cost")
    d = st.columns([1, 1, 1, 1, 1.2])
    lag = d[0].selectbox("Trade on", [1, 0, 2], key=f"{prefix}_lag", format_func=lambda v: {0: "same close", 1: "next close", 2: "2 closes later"}[v])
    regime = d[1].checkbox("Regime filter", key=f"{prefix}_reg", help="Go defensive when NIFTY 50 closes below its 200-day EMA")
    dfn = d[2].selectbox("Unused slots", ["CASH", "BENCH"], key=f"{prefix}_def")
    cy = d[3].number_input("Cash yield %", 0.0, 10.0, 6.0, 0.5, key=f"{prefix}_cy")
    ms = d[4].slider("Min score (Arka rules)", 40, 85, 55, 5, key=f"{prefix}_ms")
    return rb.BTConfig(strategy=strat, top_n=int(top_n), lookback=int(lb), skip=int(skip), rebalance=reb, cost_bps=float(cost),
                       exec_lag=int(lag), regime_filter=bool(regime), defensive=dfn, cash_yield=float(cy), min_score=float(ms))


def _verdict(m, rnd_pct=None, wf=None, bn="NIFTY 50"):
    ex, ir, dd, bdd = m.get("Excess CAGR %"), m.get("Info ratio"), m.get("Max DD %"), m.get("bench_dd")
    lines = []
    good = ex is not None and ex > 1 and ir is not None and ir > 0.3
    weak = ex is not None and ex > 0 and not good
    if good:
        lines.append(f"✔ Beat {bn} by {ex:+.1f} pp a year with an information ratio of {ir:.2f} (above 0.3 is respectable, above 0.5 is strong).")
    elif weak:
        lines.append(f"◐ Ahead of {bn} by only {ex:+.1f} pp a year (information ratio {ir:.2f}) — within what luck and costs can explain.")
    else:
        lines.append(f"✕ Did NOT beat {bn} after costs ({_g(ex, '{:+.1f}')} pp a year).")
    if dd is not None and bdd is not None:
        lines.append(f"{'✔' if dd > bdd else '✕'} Worst drawdown {dd:.1f}% vs benchmark {bdd:.1f}%.")
    if rnd_pct is not None:
        lines.append(f"{'✔' if rnd_pct >= 90 else '◐' if rnd_pct >= 70 else '✕'} Better than {rnd_pct:.0f}% of random sector picks with the same schedule and costs "
                     "(90%+ suggests real skill, not luck).")
    if wf:
        wex = wf["metrics"].get("Excess CAGR %")
        lines.append(f"{'✔' if wex and wex > 0 else '✕'} Walk-forward (parameters chosen only from past data): {_g(wex, '{:+.1f}')} pp a year vs {bn}.")
    col = C["green"] if good else C["amber"] if weak else C["red"]
    st.markdown(f'<div class="rl-verdict" style="border-color:{col}">' + "<br>".join(lines) +
                '<br><span class="x">Run ROBUSTNESS for the random-pick and walk-forward checks before trusting any result.</span></div>',
                unsafe_allow_html=True)


def _tab_backtest(sig_base, D):
    cfg = _controls()
    dates = sig_base["P"].index
    first = sig_base["P"].dropna(how="all").index.min() + pd.Timedelta(days=380)
    s1, s2 = st.columns([3, 1])
    rng_ = s1.slider("Test window", min_value=first.date(), max_value=dates[-1].date(), value=(first.date(), dates[-1].date()),
                     key="rl_bt_win", format="MMM YYYY")
    with s2:
        st.write("")
        run = st.button("RUN BACKTEST", type="primary", use_container_width=True, key="rl_bt_run")
    st.caption(rb.STRATEGIES[cfg.strategy] + " The first ~year is kept as indicator warm-up.")
    if run:
        sig = rb.with_lookback(sig_base, cfg.lookback, cfg.skip)
        start, end = pd.Timestamp(rng_[0]), pd.Timestamp(rng_[1])
        with st.spinner("Simulating…"):
            try:
                res = rb.run_backtest(sig, cfg, start, end)
                ew = rb.run_backtest(sig, replace(cfg, strategy="EQUAL WEIGHT", regime_filter=False), start, end)
            except ValueError as exc:
                st.error(str(exc))
                return
        st.session_state["rl_bt_res"] = {"res": res, "ew": ew, "cfg": cfg, "start": start, "end": end, "src": D["source"],
                                         "now": rb.current_picks(sig, cfg)}
    R = st.session_state.get("rl_bt_res")
    if not R:
        _note("Choose a rule and press RUN BACKTEST. Signals are computed at each rebalance close using only data available that day; trades happen "
              "at the next close, weights drift between rebalances, every rupee traded pays the cost you set, and empty slots earn the cash yield.")
        return
    if R["src"] != D["source"]:
        st.caption("Data source changed since this run — press RUN BACKTEST to refresh.")
    res, ew, cfg = R["res"], R["ew"], R["cfg"]
    r, b = res["returns"], res["bench_returns"]
    ms, mb, me = rb.metrics(r, b, cfg.cash_yield), rb.metrics(b, None, cfg.cash_yield), rb.metrics(ew["returns"], b, cfg.cash_yield)
    ms["bench_dd"] = mb.get("Max DD %")
    _verdict(ms, st.session_state.get("rl_rand_pct"), st.session_state.get("rl_wf"), D["bench_name"])
    _kpis([("Strategy CAGR", _g(ms["CAGR %"]), cfg.label(), C["amber"]),
           (f"{D['bench_name']} CAGR", _g(mb["CAGR %"]), "buy & hold, no costs", C["t2"]),
           ("Equal-weight sectors", _g(me["CAGR %"]), "all sectors, same schedule & costs", C["cyan"]),
           ("Excess vs bench", _g(ms["Excess CAGR %"], "{:+.1f} pp"), f"info ratio {ms['Info ratio']:.2f}",
            C["green"] if ms["Excess CAGR %"] > 0 else C["red"])], 4)

    _h("SCORECARD")
    keys = ["CAGR %", "Vol %", "Sharpe", "Sortino", "Max DD %", "Longest DD (days)", "Calmar", "Beta", "Alpha %/yr", "Info ratio", "Months beating bench %"]
    tbl = pd.DataFrame({"Strategy": [ms.get(k) for k in keys], D["bench_name"]: [mb.get(k) for k in keys],
                        "Equal-weight sectors": [me.get(k) for k in keys]}, index=keys)
    extra = pd.DataFrame({"Strategy": [res["turnover_yr"], res["avg_holdings"], res["exposure"]],
                          D["bench_name"]: [0, np.nan, 100], "Equal-weight sectors": [ew["turnover_yr"], ew["avg_holdings"], ew["exposure"]]},
                         index=["Turnover %/yr", "Avg sectors held", "Invested %"])
    sc = pd.concat([tbl, extra])
    pct_rows = {"CAGR %", "Vol %", "Max DD %", "Alpha %/yr", "Months beating bench %", "Turnover %/yr", "Invested %"}
    def _fmt(k, v):
        if v is None or pd.isna(v):
            return '<span class="mf-na">—</span>'
        if k in ("Longest DD (days)", "Turnover %/yr"):
            return f"{v:,.0f}"
        return f"{v:+.1f}%" if k in ("CAGR %", "Alpha %/yr", "Max DD %") else (f"{v:.1f}%" if k in pct_rows else f"{v:.2f}")
    rows = [{"metric": k, **{c: _fmt(k, sc.loc[k, c]) for c in sc.columns}} for k in sc.index]
    ui.show_table([ui.C("Metric", "metric", "sector")] + [ui.C(c, c, "html", al="r") for c in sc.columns], rows)

    _h("GROWTH OF ₹100 · LOG SCALE")
    eq = pd.DataFrame({"Strategy": (1 + r).cumprod() * 100, D["bench_name"]: (1 + b).cumprod() * 100,
                       "Equal-weight sectors": (1 + ew["returns"]).cumprod() * 100})
    if go is not None:
        fig = go.Figure()
        for name, col in zip(eq.columns, (C["amber"], C["t2"], C["cyan"])):
            fig.add_trace(go.Scatter(x=eq.index, y=eq[name], name=name, line=dict(color=col, width=2 if name == "Strategy" else 1.3)))
        fig.update_yaxes(type="log")
        _show(_layout(fig, 340), "rl_eq")
        dd = eq / eq.cummax() - 1
        fig = go.Figure()
        for name, col in zip(eq.columns, (C["amber"], C["t2"], C["cyan"])):
            fig.add_trace(go.Scatter(x=dd.index, y=dd[name] * 100, name=name, line=dict(color=col, width=1.2)))
        _h("DRAWDOWN · % BELOW PREVIOUS PEAK")
        _show(_layout(fig, 220), "rl_dd")
        rx = rb.rolling_excess(r, b)
        _h(f"ROLLING 1-YEAR EXCESS RETURN vs {D['bench_name']} (pp) · IS THE EDGE STEADY OR ONE LUCKY PERIOD?")
        fig = go.Figure(go.Scatter(x=rx.index, y=rx.values, line=dict(color=C["amber"], width=1.4), fill="tozeroy", fillcolor="rgba(255,159,10,.08)"))
        fig.add_hline(y=0, line=dict(color=C["t3"]))
        _show(_layout(fig, 220), "rl_rx")
    else:
        st.line_chart(eq)

    _h("CALENDAR-YEAR RETURNS %")
    yr = rb.yearly_returns({"Strategy": r, D["bench_name"]: b, "Equal-weight": ew["returns"]})
    yr["Excess pp"] = yr["Strategy"] - yr[D["bench_name"]]
    ui.show_table([ui.C("Year", "Year", "sector")] + [ui.C(c, c, "pct", d=1, scale=25.0 if c != "Excess pp" else 12.0) for c in yr.columns],
                  _recs(yr.iloc[::-1].reset_index()), max_height=520)
    st.caption(f"Beat the benchmark in {int((yr['Excess pp'] > 0).sum())} of {len(yr)} calendar years (partial first / last years included).")

    a, b2 = st.columns([1, 2])
    with a:
        _h("WHAT THE RULE HOLDS NOW")
        now = R["now"]
        if not now["holdings"]:
            st.markdown(f"**Defensive** ({'regime filter: NIFTY 50 below 200-EMA' if not now['risk_on'] else 'no sector qualifies'})")
        for n in now["holdings"]:
            st.markdown(f"- **{n}**")
        st.caption(f"Signal as of {str(now['date'])[:10]} close. Research shortlist, not advice.")
    with b2:
        _h("RECENT REBALANCES")
        tr = pd.DataFrame(res["trades"][-15:][::-1])
        if not tr.empty:
            tr["holdings"] = tr["holdings"].map(lambda x: ", ".join(s.replace("NIFTY ", "") for s in x) or "— defensive —")
            tr["signal_date"] = tr["signal_date"].astype(str).str[:10]
            tr["trade_date"] = tr["trade_date"].astype(str).str[:10]
            tr["turnover"] = tr["turnover"] * 100
            ui.show_table([ui.C("Signal", "signal_date", "text"), ui.C("Traded", "trade_date", "text"), ui.C("Holdings", "holdings", "muted"),
                           ui.C("Turnover", "turnover", "num", d=0, suf="%"), ui.C("Defensive", "defensive_pct", "num", d=0, suf="%")],
                          _recs(tr), max_height=420)
    csv = pd.DataFrame({"strategy": r, "benchmark": b, "equal_weight": ew["returns"]}).to_csv()
    st.download_button("⤓ DAILY RETURNS (CSV)", csv, "arka_rotation_backtest.csv", "text/csv", key="rl_bt_dl")


# ═════════════════════════ tab 3 · ROBUSTNESS ═════════════════════════
def _tab_robust(sig_base):
    R = st.session_state.get("rl_bt_res")
    if not R:
        st.info("Run a backtest in the BACKTEST tab first; robustness checks use the same rule and window.")
        return
    cfg, start, end = R["cfg"], R["start"], R["end"]
    sig = rb.with_lookback(sig_base, cfg.lookback, cfg.skip)
    st.caption(f"Testing: {cfg.label()} · {str(start)[:10]} → {str(end)[:10]}")
    b = st.columns(4)
    if b[0].button("PARAMETER HEAT-MAP", use_container_width=True, key="rl_rb_sweep"):
        with st.spinner("25 backtests…"):
            st.session_state["rl_sweep"] = rb.param_sweep(sig_base, cfg, start=start, end=end)
    if b[1].button("WALK-FORWARD", use_container_width=True, key="rl_rb_wf"):
        with st.spinner("Re-optimising every year on the previous 3 years…"):
            st.session_state["rl_wf"] = rb.walk_forward(sig_base, cfg)
    if b[2].button("RANDOM-PICK BASELINE", use_container_width=True, key="rl_rb_rand"):
        with st.spinner("100 random portfolios…"):
            rnd = rb.random_baseline(sig, cfg, 100, start, end)
            mine = rb.metrics(R["res"]["returns"])["CAGR %"]
            st.session_state["rl_rand"] = (rnd, mine)
            st.session_state["rl_rand_pct"] = float((rnd["CAGR %"] < mine).mean() * 100) if len(rnd) else None
    if b[3].button("COST & DELAY SENSITIVITY", use_container_width=True, key="rl_rb_sens"):
        with st.spinner("Re-running…"):
            st.session_state["rl_sens"] = (rb.sensitivity(sig, cfg, "cost_bps", [0, 10, 25, 50, 100], start, end),
                                           rb.sensitivity(sig, cfg, "exec_lag", [0, 1, 2, 5], start, end))

    sw = st.session_state.get("rl_sweep")
    if sw is not None and not sw.empty:
        _h("PARAMETER HEAT-MAP · LOOKBACK × NUMBER OF SECTORS")
        metric = st.radio("Colour by", ["Excess CAGR %", "Sharpe", "Max DD %", "Info ratio"], horizontal=True, key="rl_sw_m")
        pv = sw.pivot(index="lookback", columns="top_n", values=metric)
        if go is not None:
            fig = go.Figure(go.Heatmap(z=pv.values, x=[f"top {c}" for c in pv.columns], y=[f"{i} d" for i in pv.index],
                                       colorscale=[[0, C["red"]], [0.5, "#151515"], [1, C["green"]]],
                                       zmid=0 if metric in ("Excess CAGR %", "Info ratio") else None,
                                       text=np.round(pv.values, 2), texttemplate="%{text}", showscale=False))
            _show(_layout(fig, 260), "rl_sw_hm")
        else:
            st.dataframe(pv.round(2))
        _note("Look for a broad green PLATEAU, not one bright cell. If only one exact setting works, the result is probably curve-fitted "
              "and will not repeat. A robust rule works across most neighbouring lookbacks and portfolio sizes.")

    wf = st.session_state.get("rl_wf")
    if wf:
        _h("WALK-FORWARD · TRAIN ON THE PAST 3 YEARS, TRADE THE NEXT YEAR, REPEAT")
        m = wf["metrics"]
        _kpis([("Out-of-sample CAGR", _g(m["CAGR %"]), f"{m['Years']:.1f} years stitched", C["amber"]),
               ("Benchmark CAGR", _g(m["Bench CAGR %"]), "same years", C["t2"]),
               ("Excess", _g(m["Excess CAGR %"], "{:+.1f} pp"), f"info ratio {m['Info ratio']:.2f}", C["green"] if m["Excess CAGR %"] > 0 else C["red"]),
               ("Max DD", _g(m["Max DD %"], "{:.1f}%"), f"Sharpe {m['Sharpe']:.2f}", C["t2"])])
        ui.show_table([ui.C("Year", "Year", "sector"), ui.C("Lookback", "Chosen lookback", "int"), ui.C("Top N", "Chosen top N", "int"),
                       ui.C("In-sample Sharpe", "In-sample Sharpe", "num", d=2), ui.C("Out-of-sample", "Out-of-sample %", "pct", d=1, scale=25.0),
                       ui.C("Benchmark", "Benchmark %", "pct", d=1, scale=25.0), ui.C("Excess", "Excess pp", "pct", d=1, scale=12.0)],
                      _recs(wf["log"].iloc[::-1]))
        _note("This is the most honest number on the page: each year's settings were picked WITHOUT seeing that year. "
              "If walk-forward excess is far below the full-period backtest, the full-period result was flattered by hindsight.")
    elif wf == {}:
        st.warning("Not enough history for walk-forward (needs ~4 years after warm-up). Use NSE OFFICIAL SECTOR INDICES with max history.")

    rr = st.session_state.get("rl_rand")
    if rr:
        rnd, mine = rr
        _h("RANDOM-PICK BASELINE · 100 PORTFOLIOS OF RANDOM SECTORS, SAME SCHEDULE AND COSTS")
        pct = st.session_state.get("rl_rand_pct")
        if go is not None and len(rnd):
            fig = go.Figure(go.Histogram(x=rnd["CAGR %"], nbinsx=25, marker_color=C["t3"]))
            fig.add_vline(x=mine, line=dict(color=C["amber"], width=3), annotation_text=f"your rule {mine:.1f}%", annotation_font_color=C["amber"])
            _show(_layout(fig, 240), "rl_rand_h")
        st.caption(f"Your rule beat {pct:.0f}% of random portfolios (median random CAGR {rnd['CAGR %'].median():.1f}%). "
                   "Below ~90% the edge is hard to tell apart from luck.")

    sens = st.session_state.get("rl_sens")
    if sens:
        a, b2 = st.columns(2)
        with a:
            _h("COST SENSITIVITY")
            ui.show_table([ui.C("Cost bps/side", "cost_bps", "int"), ui.C("CAGR", "CAGR %", "pct", d=1, scale=20.0),
                           ui.C("Excess", "Excess CAGR %", "pct", d=1, scale=8.0), ui.C("Sharpe", "Sharpe", "num", d=2),
                           ui.C("Max DD", "Max DD %", "pct", d=1, scale=40.0)], _recs(sens[0]))
        with b2:
            _h("DELAY SENSITIVITY (SESSIONS)")
            ui.show_table([ui.C("Delay (sessions)", "exec_lag", "int"), ui.C("CAGR", "CAGR %", "pct", d=1, scale=20.0),
                           ui.C("Excess", "Excess CAGR %", "pct", d=1, scale=8.0), ui.C("Sharpe", "Sharpe", "num", d=2),
                           ui.C("Max DD", "Max DD %", "pct", d=1, scale=40.0)], _recs(sens[1]))
        _note("A good rotation rule should survive realistic costs (15-25 bps per side for sector ETFs) and a 1-2 day delay. "
              "If the edge vanishes with a 2-day delay, it depended on reacting instantly — unrealistic for most investors.")


# ═════════════════════════ tab 4 · SIGNAL TESTS ═════════════════════════
def _tab_signals(sig_base, D):
    c1, c2 = st.columns(2)
    h = c1.selectbox("Look-ahead horizon", [5, 10, 21, 63], index=2, key="rl_st_h", format_func=lambda v: f"next {v} sessions (~{max(1, round(v / 5))} wk)" if v < 21 else f"next {v} sessions (~{round(v / 21)}M)")
    lb = c2.selectbox("Momentum lookback for the comparison", [21, 63, 126, 252], index=1, key="rl_st_lb")
    sig = rb.with_lookback(sig_base, lb)
    fwd = rb.forward_excess(sig, h)
    dates = rb.test_dates(sig, h)
    if len(dates) < 12:
        st.info("Not enough history for a meaningful signal test.")
        return
    ic_s, sum_s = rb.information_coefficient(sig["score"], fwd, dates)
    ic_m, sum_m = rb.information_coefficient(sig["mom"], fwd, dates)
    _h(f"DOES A HIGHER SIGNAL TODAY MEAN A BETTER NEXT {h} SESSIONS? (vs {D['bench_name']})")
    _kpis([("Arka score · mean IC", _g(sum_s["Mean IC"], "{:+.3f}"), f"t-stat {_g(sum_s['IC t-stat'], '{:.1f}')} · positive {_g(sum_s['% periods IC > 0'], '{:.0f}%')}",
            C["green"] if (sum_s["Mean IC"] or 0) > 0.03 else C["amber"]),
           (f"Momentum {lb}d · mean IC", _g(sum_m["Mean IC"], "{:+.3f}"), f"t-stat {_g(sum_m['IC t-stat'], '{:.1f}')} · positive {_g(sum_m['% periods IC > 0'], '{:.0f}%')}",
            C["green"] if (sum_m["Mean IC"] or 0) > 0.03 else C["amber"]),
           ("Test periods", sum_s["Periods"], f"non-overlapping, every {h} sessions", C["t2"]),
           ("Sectors", sig["P"].shape[1], D["source"][:40], C["t2"])])
    if go is not None and len(ic_s):
        fig = go.Figure()
        fig.add_trace(go.Bar(x=ic_s.index, y=ic_s.values, name="Arka score IC", marker_color=C["amber"], opacity=.55))
        fig.add_trace(go.Scatter(x=ic_s.index, y=ic_s.rolling(12).mean(), name="12-period average", line=dict(color=C["amber"], width=2)))
        fig.add_trace(go.Scatter(x=ic_m.index, y=ic_m.rolling(12).mean(), name=f"momentum {lb}d (12-period avg)", line=dict(color=C["cyan"], width=2)))
        fig.add_hline(y=0, line=dict(color=C["t3"]))
        _show(_layout(fig, 260), "rl_ic")
    _note("IC (information coefficient) = rank correlation between today's signal and each sector's NEXT-period return vs the benchmark. "
          "+0.05 or more, held consistently (t-stat above 2), is a useful signal in practice; around 0 means the signal does not predict.")
    a, b = st.columns(2)
    with a:
        _h("FORWARD EXCESS RETURN BY STAGE")
        stt = rb.stage_forward_table(sig, fwd, dates)
        if not stt.empty:
            x = stt.reset_index()
            ui.show_table([ui.C("Stage", "stage", "stage"), ui.C("Avg excess", "Avg next-period excess (pp)", "pct", scale=3.0),
                           ui.C("Median", "Median (pp)", "pct", scale=3.0),
                           ui.C("Beat bench", "Beat benchmark %", "bar", suf="%", color=ui.CYAN), ui.C("Obs", "Observations", "int")], _recs(x))
        _note("If the labels work, ROTATING IN / LEADING should show positive average excess and a 'beat benchmark' rate above 50%, "
              "and WEAK / FADING should be below. If not, the stage rules need re-tuning.")
    with b:
        _h("FORWARD EXCESS RETURN BY SCORE BUCKET")
        bt = rb.score_bucket_table(sig, fwd, dates)
        if not bt.empty:
            x = bt.reset_index().rename(columns={"bucket": "Score"})
            x["Score"] = x["Score"].astype(str)
            ui.show_table([ui.C("Score bucket", "Score", "sector"), ui.C("Avg excess", "Avg next-period excess (pp)", "pct", scale=3.0),
                           ui.C("Beat bench", "Beat benchmark %", "bar", suf="%", color=ui.CYAN), ui.C("Obs", "Observations", "int")], _recs(x))
        _note("A useful score shows returns rising from the low buckets to the high buckets (a monotonic staircase).")


# ═════════════════════════ tab 5 · FPI FLOWS ═════════════════════════
@st.cache_data(ttl=12 * 3600, show_spinner=False, max_entries=8)
def _nsdl_fetch(d: str):
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"})
    for url in rb.nsdl_urls(pd.Timestamp(d)):
        try:
            r = sess.get(url, timeout=12)
            if r.status_code == 200 and "Sector" in r.text:
                p = rb.parse_nsdl_sector_html(r.text)
                if not p.empty:
                    return p, url
        except requests.RequestException:
            continue
    return None, ""


def load_fpi(n: int):
    dates = [str(d)[:10] for d in rb.nsdl_report_dates(n)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(_nsdl_fetch, dates))
    parsed = [p for p, _ in got if p is not None]
    return rb.nsdl_panels(parsed), sum(p is not None for p, _ in got), len(dates)


def _tab_fpi(sig_base, D):
    _note("NSDL publishes how much foreign portfolio investors (FPIs) put into or took out of each sector every fortnight. "
          "Unlike price-based signals, this is real recorded money. Limits: FPIs only (not mutual funds / DIIs), published with a lag, "
          "NSDL's own sector classification. Source: fpi.nsdl.co.in (Fortnightly Sector-wise FPI Investment Data).")
    c1, c2 = st.columns([1, 3])
    n = c1.selectbox("Fortnights", [12, 24, 48], index=1, key="rl_fpi_n")
    with c2:
        st.write("")
        if st.button("LOAD FPI SECTOR FLOWS", type="primary", key="rl_fpi_load"):
            with st.spinner("Downloading NSDL fortnightly reports…"):
                st.session_state["rl_fpi"] = load_fpi(int(n))
    got = st.session_state.get("rl_fpi")
    if not got:
        return
    (net, auc), ok, tried = got
    if net.empty:
        st.warning(f"No NSDL report could be read ({ok} of {tried}). The site may be blocking this server or its file names changed.")
        return
    st.caption(f"{ok} of {tried} fortnightly reports loaded · latest fortnight ending {str(net.index[-1])[:10]} · values in ₹ crore (equity).")
    last = net.iloc[-1].dropna().sort_values(ascending=False)
    _h(f"LATEST FORTNIGHT · FPI NET EQUITY INVESTMENT BY SECTOR (₹ CR) · ENDING {str(net.index[-1])[:10]}")
    _hbar(list(last.index), list(last.values), "rl_fpi_last", suffix=" Cr")

    _h("CUMULATIVE FLOWS AND FLOW AS % OF HOLDINGS")
    rows = []
    for s in net.columns:
        x = net[s].dropna()
        a_ = auc[s].dropna() if s in auc else pd.Series(dtype=float)
        aum = float(a_.iloc[-1]) if len(a_) else np.nan
        f2, f6, f12 = x.tail(2).sum(), x.tail(6).sum(), x.tail(12).sum()
        streak = 0
        for v in x.iloc[::-1]:
            if streak >= 0 and v > 0:
                streak += 1
            elif streak <= 0 and v < 0:
                streak -= 1
            else:
                break
        rows.append({"sector": s, "1M ₹Cr": f2, "3M ₹Cr": f6, "6M ₹Cr": f12, "3M % of holdings": f6 / aum * 100 if aum and aum > 0 else np.nan,
                     "Streak (fortnights)": streak, "FPI holdings ₹Cr": aum})
    tb = pd.DataFrame(rows).sort_values("3M % of holdings", ascending=False, na_position="last")
    sb = st.session_state.get("mf_sr_res")
    if sb is not None:     # compare with the price-based stage for the same NSE sector names
        try:
            rot = sb.get("_rot_last")
            if rot is None:
                import sector_engine as se
                rot, _ = se.rotation_table(sb, list(sb["bench_index"].columns)[0])
            stage = {rb.norm_name(k): v for k, v in zip(rot["sector"], rot["stage"])}
            tb["Price stage"] = tb["sector"].map(lambda s: stage.get(rb.norm_name(s), "—"))
        except Exception:
            pass
    def _cr(v):
        if v is None or pd.isna(v):
            return '<span class="mf-na">—</span>'
        col = ui.GREEN if v >= 0 else ui.RED
        return f'<span class="mf-pct" style="color:{col};background:{ui._heat(v, 20000)}">{v:+,.0f}</span>'
    def _streak(v):
        if not v:
            return '<span class="mf-na">0</span>'
        col = ui.GREEN if v > 0 else ui.RED
        return f'<span class="mf-chip {"up" if v > 0 else "dn"}">{"▲" if v > 0 else "▼"} {abs(int(v))}</span>'
    view = tb.copy()
    for c in ("1M ₹Cr", "3M ₹Cr", "6M ₹Cr"):
        view[c + "_h"] = view[c].map(_cr)
    view["streak_h"] = view["Streak (fortnights)"].map(_streak)
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector"), ui.C("1M ₹Cr", "1M ₹Cr_h", "html", al="r"),
            ui.C("3M ₹Cr", "3M ₹Cr_h", "html", al="r"), ui.C("6M ₹Cr", "6M ₹Cr_h", "html", al="r"),
            ui.C("3M % of holdings", "3M % of holdings", "pct", scale=2.0, tip="3-month net flow ÷ FPI holdings in the sector"),
            ui.C("Streak", "streak_h", "html", tip="+3 = three fortnights of buying in a row; −2 = two of selling"),
            ui.C("FPI holdings ₹Cr", "FPI holdings ₹Cr", "num", d=0)]
    if "Price stage" in view:
        cols.append(ui.C("Price stage", "Price stage", "stage", tip="Stage from the stock-built SECTOR RANKING radar"))
    ui.show_table(cols, _recs(view), max_height=720)
    _note("'% of holdings' scales flows by how much FPIs already own, so a ₹2,000 Cr inflow into a small sector counts for more than into "
          "Financial Services. The strongest signal is when price (stage ROTATING IN / LEADING) and FPI money (positive streak) agree.")

    _h("FLOW HEAT-MAP · LAST 12 FORTNIGHTS (₹ CR)")
    hm = net.tail(12).T
    hm = hm.loc[hm.sum(axis=1).sort_values(ascending=False).index]
    if go is not None:
        z = hm.values
        lim = float(np.nanpercentile(np.abs(z), 90)) or 1.0
        fig = go.Figure(go.Heatmap(z=z, x=[d.strftime("%d %b %y") for d in hm.columns], y=list(hm.index),
                                   colorscale=[[0, C["red"]], [0.5, "#151515"], [1, C["green"]]], zmin=-lim, zmax=lim, showscale=False,
                                   text=np.round(z / 100) / 10, texttemplate="%{text}k", hovertemplate="%{y} · %{x}: ₹%{z:,.0f} Cr<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        _show(_layout(fig, 24 * len(hm) + 60), "rl_fpi_hm")
    st.download_button("⤓ FPI FLOWS (CSV)", net.to_csv(), "nsdl_fpi_sector_flows.csv", "text/csv", key="rl_fpi_dl")


# ═════════════════════════ HOW IT WORKS ═════════════════════════
def _tab_method():
    st.markdown("""
**What sector rotation is.** Money does not leave the market so much as move between sectors as the economy, interest rates and
earnings expectations change. A rotation tool tries to spot that movement early enough to matter and to avoid sectors money is leaving.

**The lenses on the NOW tab, and why each exists**

| Lens | Question it answers | Evidence base |
|---|---|---|
| Relative strength / momentum ladder | Which sectors have been beating the market over 1-12 months? | Industry momentum is one of the best documented effects in finance (Moskowitz & Grinblatt 1999): winning industries tend to keep winning for months. |
| RRG lanes (trend × momentum) | Is the out-performance speeding up or fading? | JdK-style relative rotation; Arka uses a simplified, transparent version. |
| Rank heat-map | Is a sector *climbing* the table week after week? | A visual rate of change of relative strength. |
| Risk appetite (cyclicals ÷ defensives) | Is money taking risk (metals, autos, banks, realty) or hiding (FMCG, pharma, IT)? | Classic business-cycle rotation. |
| Dispersion | Is choosing a sector worth it right now? | When sectors move together, rotation adds little. |
| FPI sector flows (NSDL) | Where did foreign money actually go? | Actual recorded flows, not inferred from price. |
| Breadth / CMF / turnover (stock-built sectors) | Is the move broad, and is volume confirming it? | Participation and accumulation evidence. |

**How the backtest avoids fooling you**

1. *No look-ahead.* Signals use only data up to the rebalance close; trades happen at the next close (adjustable).
   Verified: on simulated random-walk data with no real rotation, every rule shows no edge.
2. *Realistic costs and drift.* Every rupee traded pays the cost you set (15 bps per side ≈ sector-ETF brokerage + taxes + spread);
   weights drift with prices between rebalances.
3. *Three yardsticks.* NIFTY 50 buy & hold, an equal-weight basket of all sectors (no-skill rotation), and 100 random-pick portfolios (luck).
4. *Walk-forward.* Settings are re-chosen each year using only the previous three years, then traded blind for a year.
5. *Signal tests.* IC and stage tables check whether the radar's labels predicted the next month, independent of portfolio rules.

**Known biases and limits**
* Stock-built sectors use today's constituent lists → survivorship bias (failed companies are missing), which flatters results. Official indices avoid this.
* Yahoo index history is a price index (no dividends) and occasionally has bad ticks (repaired when a spike reverts the next day).
* Sector indices are not directly tradable except through ETFs / index funds, which exist for the bigger sectors only.
* A strong backtest is evidence, not a promise: regimes change. Prefer rules that pass the heat-map plateau, walk-forward and random-baseline checks.

*Research tool only — not investment advice.*
""")


# ═════════════════════════ entry point ═════════════════════════
def render_rotation_lab(on_open=None):
    _css()
    st.markdown(f'<div style="font:700 10px {C["mono"]};letter-spacing:1.5px;color:{C["t3"]}">ARKA MONEY FLOW · ROTATION LAB</div>'
                f'<div style="font:800 20px {C["mono"]};color:{C["ivory"]};margin:2px 0 4px">WHERE IS MONEY ROTATING — AND DOES FOLLOWING IT PAY?</div>',
                unsafe_allow_html=True)
    D = _data()
    if not D:
        return
    st.caption(D["source"])
    if D.get("warn"):
        st.warning(D["warn"])
    base_lb = 63
    with st.spinner("Computing point-in-time signals…"):
        sig = _signals(D["prices"], D["bench"], D["breadth"], D["cmf"], D["share"], base_lb, 0)
    tabs = st.tabs(["1 · NOW", "2 · BACKTEST", "3 · ROBUSTNESS", "4 · SIGNAL TESTS", "5 · FPI SECTOR FLOWS", "HOW IT WORKS"])
    with tabs[0]:
        _tab_now(sig, D)
    with tabs[1]:
        _tab_backtest(sig, D)
    with tabs[2]:
        _tab_robust(sig)
    with tabs[3]:
        _tab_signals(sig, D)
    with tabs[4]:
        _tab_fpi(sig, D)
    with tabs[5]:
        _tab_method()
