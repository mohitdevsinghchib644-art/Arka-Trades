"""
Arka Trades — regime_page.py (v1): Market regime and breadth dashboard.

Answers one question every evening: should I be buying breakouts aggressively, carefully, or not at all?
Today's state, the reasons in plain words, every component, the history, and the proof: how NIFTY and the average
stock behaved after each state over the last decade.

Public API: render_regime(on_open=None)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import quant_data as qd
import quant_ui as ui
import regime_engine as rg

COMP_INFO = {
    "trend": ("Trend", "NIFTY 50 vs its 21 / 50 EMA and 200 DMA, and the 50 EMA's slope"),
    "breadth": ("Breadth", "Share of stocks above their 50 EMA and 200 DMA"),
    "highs": ("New highs vs lows", "Share of new 52-week highs among highs + lows, 10 days"),
    "mcclellan": ("McClellan", "Momentum of advancers vs decliners"),
    "distribution": ("Distribution", "Institutional selling days in the last 25 sessions"),
    "volatility": ("Volatility", "India VIX vs its past year, and how fast it is rising"),
    "appetite": ("Risk appetite", "Average stock vs NIFTY 50"),
}


def _regime_for(p: dict) -> pd.DataFrame:
    if "_regime" not in p:
        p["_regime"] = rg.compute(p)
    return p["_regime"]


def get_panel(universe: str, years: int = 10, force: bool = False):
    """Load with a progress bar; returns (panel, error message)."""
    bar = None

    def prog(f, msg):
        nonlocal bar
        if bar is None:
            bar = st.progress(0.0, text="Downloading prices: the first load of the day takes a minute or two.")
        bar.progress(min(1.0, f), text=f"{msg}. First load of the day; later visits are instant.")
    try:
        p = qd.load_panel(universe, st.session_state.get("watchlist", []), years, prog, force)
        return p, None
    except Exception as exc:  # pragma: no cover - network
        return None, str(exc) or type(exc).__name__
    finally:
        if bar is not None:
            bar.empty()


def _fwd_card(df: pd.DataFrame, horizon: int) -> str:
    ft = rg.forward_table(df, horizon)
    ft = ft[ft["days"] > 0]
    if ft.empty:
        return ""
    rows = []
    for r in ft.itertuples():
        c = ui.STATE_COLOR[r.state]
        rows.append(f'<tr><td>{ui.pill(r.state, c)}</td><td>{r.share:.0f}%</td>'
                    f'<td style="color:{ui.signed_color(r.nifty_avg) or ui.TX}">{r.nifty_avg:+.2f}%</td><td>{r.nifty_up:.0f}%</td>'
                    f'<td style="color:{ui.signed_color(r.ew_avg) or ui.TX}">{r.ew_avg:+.2f}%</td><td>{r.ew_up:.0f}%</td>'
                    f'<td style="color:{ui.RED}">{r.worst_avg:.1f}%</td></tr>')
    up = ft.set_index("state")
    line = ""
    if "Confirmed uptrend" in up.index and "Correction" in up.index:
        a, b = up.loc["Confirmed uptrend"], up.loc["Correction"]
        line = (f'<div class="q-sub" style="margin-top:10px">Over the past {len(df) / 252:.0f} years, after a Confirmed uptrend reading the average stock moved '
                f'<b style="color:{ui.signed_color(a.ew_avg)}">{a.ew_avg:+.1f}%</b> over the next {horizon} sessions and rose {a.ew_up:.0f}% of the time. '
                f'After a Correction reading it moved <b style="color:{ui.signed_color(b.ew_avg)}">{b.ew_avg:+.1f}%</b> and rose {b.ew_up:.0f}% of the time.'
                + (" The states separate good and bad times to buy, so the reading is worth following." if a.ew_avg > b.ew_avg + 1 else
                   " The gap between states is small in this universe, so treat the reading as context, not a hard rule.") + "</div>")
    return (f'<div class="q-card"><div class="q-ch">Does it work? What happened next<em>next {horizon} sessions after each reading</em></div>'
            f'<div class="q-tw"><table class="q-t"><thead><tr><th>State</th><th>Time in state</th><th>NIFTY 50 avg</th><th>NIFTY up</th>'
            f'<th>Average stock</th><th>Average stock up</th><th>Avg worst dip</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{line}</div>')


def render_regime(on_open=None):
    ui.css()
    ph = ui.phone()
    # the page title and subtitle come from app.py (PAGE_META)
    c1, c2, c3 = st.columns([2.2, 1.4, 0.8] if not ph else [1.6, 1.2, 0.9], gap="small")
    with c1:
        uni = ui.choice("Breadth universe", ["Nifty 500", "Nifty 200", "Nifty 100"], "rg_uni")
    with c2:
        span = ui.choice("Chart period", ["1 year", "3 years", "All"], "rg_span")
    force = c3.button("Refresh", key="rg_refresh", use_container_width=True, help="Download again (normally once a day is enough)")
    p, err = get_panel(uni, 10, force)
    if p is None:
        st.markdown(f'<div class="q-card"><div class="q-ch">Could not load market data</div><div class="q-sub">{ui.e(err)}. '
                    'Yahoo Finance may be busy; try Refresh in a minute.</div></div>', unsafe_allow_html=True)
        return
    df = _regime_for(p)
    if df.empty:
        st.info("Not enough history to read the regime yet.")
        return
    row = df.iloc[-1]
    state = row["state"]
    col = ui.STATE_COLOR[state]
    eps = rg.episodes(df)
    since, days_in = eps[-1][1], eps[-1][3]
    chg = row["ret"] * 100
    expo = int(rg.EXPOSURE[state] * 100)

    hero = (f'<div class="q-hero" style="--c:{col}"><div class="q-hgrid"><div>'
            f'<span class="st"><i></i>Market regime · {df.index[-1]:%a %d %b %Y}</span><h2>{ui.e(state)}</h2>'
            f'<p>{ui.e(rg.GUIDE[state])}</p>'
            f'<p style="margin-top:8px;color:{ui.TX3}">In this state since {since:%d %b} ({days_in} session{"s" if days_in != 1 else ""}). '
            f'NIFTY 50 {row["close"]:,.0f} <span style="color:{ui.signed_color(chg) or ui.TX2}">{chg:+.2f}%</span>.</p></div>'
            f'<div><div class="q-big">{row["score"]:.0f}<small> / 100 regime score</small></div>'
            f'<div class="q-gauge" style="margin-top:12px"><i style="left:{row["score"]:.1f}%"></i></div>'
            f'<div class="q-gl"><span>Correction</span><span>Under pressure</span><span>Uptrend</span></div>'
            f'<div class="q-sub" style="margin-top:10px">Suggested exposure <b style="color:#fff">{expo}%</b> of your normal size</div></div></div></div>')
    st.markdown(hero, unsafe_allow_html=True)

    kp = [ui.kpi("Above 50 EMA", f'{row["pct50"]:.0f}%', f'200 DMA {row["pct200"]:.0f}% · 21 EMA {row["pct20"]:.0f}%'),
          ui.kpi("New highs / lows", f'<span style="color:{ui.GREEN}">{int(row["nh"])}</span> / <span style="color:{ui.RED}">{int(row["nl"])}</span>', "52-week, today"),
          ui.kpi("Distribution days", f'{int(row["dd_count"])}', "last 25 sessions", ui.GREEN if row["dd_count"] <= 2 else ui.YELLOW if row["dd_count"] <= 4 else ui.RED),
          ui.kpi("India VIX", f'{row["vix"]:.1f}' if np.isfinite(row["vix"]) else "—", f'{row["vix_pct"]:.0f}th percentile of 1 year' if np.isfinite(row["vix_pct"]) else ""),
          ui.kpi("Advancers / decliners", f'<span style="color:{ui.GREEN}">{int(row["adv"])}</span> / <span style="color:{ui.RED}">{int(row["dec"])}</span>', f'McClellan {row["mcclellan"]:+.0f}'),
          ui.kpi("From 3-month high", f'{row["dd_from_hi"] * 100:.1f}%', "NIFTY 50")]
    st.markdown('<div class="q-kpis">' + "".join(kp) + "</div>", unsafe_allow_html=True)

    rs = "".join(f'<div class="q-rs" style="--c:{ui.TONE_COLOR[t]}"><i></i><span>{ui.e(x)}</span></div>' for t, x in rg.reasons(row, df))
    comps = []
    for k, w in rg.WEIGHTS.items():
        v = float(row[f"c_{k}"])
        cc = ui.GREEN if v >= 65 else ui.YELLOW if v >= 40 else ui.RED
        name, desc = COMP_INFO[k]
        comps.append(f'<div class="q-cp"><div class="n"><span>{name}</span><em>{v:.0f} · {int(w * 100)}%</em></div>'
                     f'<div class="q-bar"><i style="width:{v:.0f}%;background:{cc}"></i></div><div class="d">{desc}</div></div>')
    left = f'<div class="q-card"><div class="q-ch">Why<em>today\'s reading in plain words</em></div>{rs}</div>'
    right = f'<div class="q-card"><div class="q-ch">Components<em>score · weight</em></div><div class="q-comp">{"".join(comps)}</div></div>'
    st.markdown(f'<div class="q-two">{left}{right}</div>', unsafe_allow_html=True)

    n = {"1 year": 252, "3 years": 756, "All": len(df)}[span]
    d = df.iloc[-n:]
    states = d["state"].to_numpy()
    bands = ui.state_bands(states)
    ch = ui.line_chart(d.index, [(d["close"], "#F2F2F5", 1.6, "NIFTY 50"), (d["ema50"], ui.AMBER, 1.1, "50 EMA"), (d["sma200"], ui.PURPLE, 1.1, "200 DMA")],
                       height=280 if not ph else 220, bands=bands, log=span == "All")
    leg = ui.legend([("NIFTY 50", "#F2F2F5", False), ("50 EMA", ui.AMBER, False), ("200 DMA", ui.PURPLE, False)]
                    + [(s, ui.STATE_COLOR[s], True) for s in rg.STATES])
    st.markdown(f'<div class="q-card"><div class="q-ch">NIFTY 50 coloured by regime<em>{span.lower()}</em></div>{ch}{leg}</div>', unsafe_allow_html=True)

    br = ui.line_chart(d.index, [(d["pct50"], ui.BLUE, 1.5, "% above 50 EMA"), (d["pct200"], ui.GREEN, 1.3, "% above 200 DMA")],
                       height=190, hlines=[(70, ui.GREEN, ""), (30, ui.RED, "")], y_suffix="%")
    sc = ui.line_chart(d.index, [(d["score"], ui.AMBER, 1.5, "Regime score")], height=190, bands=bands,
                       hlines=[(65, ui.GREEN, ""), (45, ui.RED, "")])
    nhl = (d["nh"] - d["nl"]).to_numpy()
    hl = ui.bar_chart(d.index, nhl, [ui.GREEN if x > 0 else ui.RED for x in nhl], height=150)
    mc = ui.line_chart(d.index, [(d["mcclellan"], ui.CYAN, 1.3, "McClellan")], height=150, hlines=[(0, "#555", "")])
    st.markdown(
        f'<div class="q-two"><div class="q-card"><div class="q-ch">Breadth<em>share of stocks in uptrends</em></div>{br}'
        f'{ui.legend([("Above 50 EMA", ui.BLUE, False), ("Above 200 DMA", ui.GREEN, False)])}</div>'
        f'<div class="q-card"><div class="q-ch">Regime score<em>65+ uptrend · under 45 weak</em></div>{sc}</div></div>'
        f'<div class="q-two"><div class="q-card"><div class="q-ch">New 52-week highs minus lows<em>daily</em></div>{hl}</div>'
        f'<div class="q-card"><div class="q-ch">McClellan oscillator<em>above 0 = advancers gaining</em></div>{mc}</div></div>',
        unsafe_allow_html=True)

    hz = ui.choice("Horizon", [20, 10, 60], "rg_hz", lambda h: f"Next {h} sessions")
    st.markdown(_fwd_card(df, hz), unsafe_allow_html=True)

    recent = eps[-10:][::-1]
    rows = "".join(f'<tr><td>{ui.pill(s, ui.STATE_COLOR[s])}</td><td>{a:%d %b %Y}</td><td>{"today" if b == df.index[-1] else f"{b:%d %b %Y}"}</td>'
                   f'<td>{nd}</td><td style="color:{ui.signed_color(mv) or ui.TX}">{mv:+.1f}%</td></tr>' for s, a, b, nd, mv in recent)
    dd_days = df.index[-25:][df["dd_flag"].iloc[-25:].to_numpy()]
    ftds = df.index[df["ftd"].to_numpy()][-3:]
    dd_txt = ", ".join(f"{x:%d %b}" for x in dd_days) or "none"
    st.markdown(
        f'<div class="q-two"><div class="q-card"><div class="q-ch">Recent state changes<em>NIFTY 50 move during each</em></div>'
        f'<div class="q-tw"><table class="q-t"><thead><tr><th>State</th><th>From</th><th>To</th><th>Sessions</th><th>NIFTY</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div></div>'
        f'<div class="q-card"><div class="q-ch">Distribution and follow-through</div>'
        f'<div class="q-sub"><b style="color:#fff">Distribution days in the last 25 sessions:</b> {ui.e(dd_txt)}.<br>'
        f'A distribution day is NIFTY 50 falling 0.2% or more on higher volume than the day before: big funds selling. Five or six in 25 sessions has '
        f'preceded most corrections.<br><br><b style="color:#fff">Recent follow-through days:</b> {", ".join(f"{x:%d %b %Y}" for x in ftds) or "none"}.<br>'
        f'A follow-through day is a strong up day (+1.2% or more) on higher volume on day 4 or later of a rally attempt. It is how a new uptrend gets '
        f'confirmed; most big rallies started with one, but not every one works.</div></div></div>', unsafe_allow_html=True)

    miss = len(p.get("missing") or [])
    st.markdown(f'<div class="q-note">Breadth from {len(p["symbols"])} stocks ({ui.e(p.get("note", ""))}'
                f'{f", {miss} returned no data" if miss else ""}). Prices: Yahoo Finance daily, adjusted for splits and bonuses; volume for distribution '
                f'days is the universe\'s total turnover because Yahoo\'s index volume is unreliable. Data built {ui.e(p.get("built", "")[:16].replace("T", " "))} IST. '
                'Every reading uses only data available at that day\'s close. A reading aid, not investment advice.</div>', unsafe_allow_html=True)
