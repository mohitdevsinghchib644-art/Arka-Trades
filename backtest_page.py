"""
Arka Trades — backtest_page.py (v1): Backtest lab, a walk-forward backtester and expectancy engine.

Pick a setup (breakout, pullback to the 21 EMA, 52-week high, volatility contraction), set your stop, exit,
risk and costs, and test it on up to 10+ years of NSE data before you risk capital. Walk-forward mode tunes the
rule on past years and judges it only on the months that followed, so the result is what you could have
known at the time.

Public API: render_backtest(on_open=None)
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

import backtest_engine as bt
import quant_data as qd
import quant_ui as ui
import regime_engine as rg
from regime_page import _regime_for, get_panel

UNIVERSES = ["Nifty 500", "Nifty 200", "Nifty 100", "Midcap 150", "Smallcap 250", "Nifty 50", "My watchlist"]
STOPS = ["Signal-day low", "ATR multiple", "Percent"]
TRAILS = {0: "No trailing exit", 10: "Close below 10 EMA", 21: "Close below 21 EMA", 50: "Close below 50 EMA"}
NUM = {"lookback": (5, 120, 5, "%d"), "vol_mult": (0.5, 5.0, 0.1, "%.1f"), "rs_min": (1, 99, 5, "%d"), "ext_max": (1.0, 40.0, 1.0, "%.0f"),
       "touch": (0.0, 6.0, 0.5, "%.1f"), "max_off_high": (2.0, 40.0, 1.0, "%.0f"), "contraction": (0.4, 1.2, 0.05, "%.2f"),
       "base_max": (4.0, 40.0, 1.0, "%.0f")}


def _num_input(where, label, key, value, spec, help=None):
    lo, hi, step, fmt = spec
    if isinstance(value, int) and isinstance(step, int):
        return int(where.number_input(label, min_value=int(lo), max_value=int(hi), value=int(value), step=int(step), key=key, help=help))
    return float(where.number_input(label, min_value=float(lo), max_value=float(hi), value=float(value), step=float(step), format=fmt, key=key, help=help))


def _controls():
    ph = ui.phone()
    with st.container(key="q_ctrl_bt"):
        setup = ui.choice("Setup", list(bt.SETUPS), "bt_setup", pills=True)
        st.markdown(f'<div class="q-sub" style="margin:2px 2px 10px">{ui.e(bt.SETUPS[setup]["about"])}</div>', unsafe_allow_html=True)
        a, b, c = st.columns(3 if not ph else [1, 1, 1], gap="small")
        uni = a.selectbox("Universe", UNIVERSES, key="bt_uni", help="Today's index members. Stocks that left the index are missing (survivorship bias).")
        years = b.selectbox("History", [10, 8, 5, 12], key="bt_years", format_func=lambda y: f"{y} years")
        mode = c.selectbox("Test", ["Walk-forward (recommended)", "Single test"], key="bt_mode",
                           help="Walk-forward tunes on past years and judges only on the unseen months that followed.")
        prm = dict(bt.SETUPS[setup]["params"])
        with st.expander("Entry rules", expanded=False):
            cols = st.columns(len(prm) if not ph else 2, gap="small")
            for i, (k, v) in enumerate(prm.items()):
                prm[k] = _num_input(cols[i % len(cols)], bt.PARAM_LABEL.get(k, k), f"bt_p_{setup}_{k}", v, NUM[k])
            min_turn = st.number_input("Skip stocks trading less than ₹ crore a day (20-day average)", 0.0, 500.0, float(bt.MIN_TURN_CR), 1.0, key="bt_turn")
        ex = dict(bt.EXIT_DEFAULT)
        with st.expander("Stop and exit", expanded=False):
            s1, s2, s3 = st.columns(3, gap="small")
            ex["stop"] = s1.selectbox("Initial stop", STOPS, key="bt_stop")
            if ex["stop"] == "ATR multiple":
                ex["stop_atr"] = s2.number_input("ATR × below entry", 0.5, 5.0, 1.5, 0.25, key="bt_satr")
            elif ex["stop"] == "Percent":
                ex["stop_pct"] = s2.number_input("% below entry", 1.0, 20.0, 7.0, 0.5, key="bt_spct")
            else:
                s2.markdown('<div class="q-note" style="margin-top:30px">Stop at the low of the signal day.</div>', unsafe_allow_html=True)
            ex["max_risk"] = s3.number_input("Never risk more than % per share", 2.0, 20.0, 10.0, 0.5, key="bt_mrisk")
            e1, e2, e3, e4 = st.columns(4 if not ph else 2, gap="small") if not ph else (st.columns(2) + st.columns(2))
            ex["trail"] = e1.selectbox("Trailing exit", list(TRAILS), index=2, format_func=TRAILS.get, key="bt_trail")
            ex["target_r"] = e2.number_input("Profit target in R (0 = none)", 0.0, 20.0, 0.0, 0.5, key="bt_tgt")
            ex["max_days"] = int(e3.number_input("Time stop, sessions", 3, 250, 40, 1, key="bt_days"))
            ex["breakeven_r"] = e4.number_input("Move stop to entry at R (0 = never)", 0.0, 10.0, 0.0, 0.5, key="bt_be")
            ex["gap_max"] = st.number_input("Skip if the next open gaps up more than %", 0.5, 20.0, 4.0, 0.5, key="bt_gap")
        book = bt.Book()
        with st.expander("Capital, risk and costs", expanded=False):
            k1, k2, k3, k4 = st.columns(4 if not ph else 2, gap="small") if not ph else (st.columns(2) + st.columns(2))
            book.capital = float(k1.number_input("Capital ₹", 50_000.0, 1e9, 1_000_000.0, 50_000.0, key="bt_cap", format="%.0f"))
            book.risk_pct = float(k2.number_input("Risk per trade %", 0.1, 5.0, 1.0, 0.1, key="bt_risk"))
            book.max_pos = int(k3.number_input("Maximum open positions", 1, 40, 8, 1, key="bt_maxpos"))
            book.max_pos_pct = float(k4.number_input("Maximum position size %", 5.0, 100.0, 25.0, 5.0, key="bt_maxpct"))
            cost = float(st.number_input("Costs and slippage per round trip %", 0.0, 2.0, bt.COST_DEFAULT, 0.05, key="bt_cost",
                                         help="Delivery trades: STT 0.1% on each side, stamp duty, exchange and SEBI fees, GST and DP charges come to "
                                              "about 0.25%. The default adds about 0.1% for slippage."))
        with st.expander("Market regime filter and walk-forward", expanded=False):
            book.regime_mode = st.selectbox("Use the Market regime", ["Off", "Skip corrections", "Scale by regime"], key="bt_reg",
                                            help="Skip corrections: no new trades in a Correction. Scale by regime: risk 100% / 50% / 25% / 0% of normal "
                                                 "in Uptrend / Under pressure / Rally attempt / Correction.")
            w1, w2, w3 = st.columns(3, gap="small")
            train = w1.selectbox("Tune on", [3, 2, 4, 5], key="bt_train", format_func=lambda y: f"{y} years")
            test = w2.selectbox("Then trade unseen", [6, 3, 12], key="bt_test", format_func=lambda m: f"{m} months")
            tune_exit = w3.toggle("Also tune the trailing exit", value=True, key="bt_tune_exit")
        run = st.button("Run backtest", key="bt_run", type="primary", use_container_width=True)
    cfg = {"setup": setup, "uni": uni, "years": int(years), "mode": mode, "prm": prm, "ex": ex, "book": book.__dict__.copy(), "cost": cost,
           "train": int(train), "test": int(test), "tune_exit": bool(tune_exit), "min_turn": float(min_turn)}
    return cfg, run


def _run(cfg: dict):
    p, err = get_panel(cfg["uni"], cfg["years"])
    if p is None:
        return None, f"Could not load prices: {err}"
    if len(p["symbols"]) < 5:
        return None, "Fewer than 5 stocks returned prices. Pick a bigger universe."
    df = _regime_for(p)
    state = df["state"].reindex(p["dates"]).to_numpy(dtype=object)
    book = bt.Book(**cfg["book"])
    bar = st.progress(0.0, text="Testing…")
    prog = lambda f, m: bar.progress(min(1.0, f), text=m)
    try:
        if cfg["mode"].startswith("Walk"):
            grid = dict(bt.SETUPS[cfg["setup"]]["grid"])
            if cfg["tune_exit"] and cfg["ex"].get("trail"):
                grid["trail"] = [10, 21, 50]
            res = bt.run_walk_forward(p, cfg["setup"], cfg["prm"], cfg["ex"], book, cfg["cost"], grid, cfg["train"], cfg["test"], state,
                                      progress=prog, min_turn=cfg["min_turn"])
        else:
            prog(0.1, "Simulating every signal")
            res = bt.run_single(p, cfg["setup"], cfg["prm"], cfg["ex"], book, cfg["cost"], state, min_turn=cfg["min_turn"])
            keys = list(bt.SETUPS[cfg["setup"]]["grid"])[:2]
            axes = {}
            for k in keys:
                cur = cfg["prm"][k]
                vals = sorted(set(bt.SETUPS[cfg["setup"]]["grid"][k] + [cur]))
                axes[k] = vals[:4]
            prog(0.5, "Checking nearby settings (is it a plateau or a lucky peak?)")
            res["sensitivity"] = bt.sensitivity(p, cfg["setup"], cfg["prm"], cfg["ex"], cfg["cost"], axes, cfg["min_turn"])
            res["sens_axes"] = keys
    finally:
        bar.empty()
    res["state"] = state
    res["cfg"] = cfg
    res["panel_key"] = (p["name"], len(p["symbols"]), str(p["dates"][-1].date()), p.get("note", ""), len(p.get("missing") or []))
    return res, None


def _heat(df: pd.DataFrame, a: str, b: str) -> str:
    if df is None or df.empty or a not in df or b not in df:
        return ""
    pv = df.groupby([a, b])["exp"].mean().unstack(b)
    cnt = df.groupby([a, b])["trades"].sum().unstack(b)
    vals = pv.to_numpy(dtype=float)
    mx = np.nanmax(np.abs(vals)) if np.isfinite(vals).any() else 1
    head = "".join(f"<th>{ui.e(bt.PARAM_LABEL.get(b, b))}: {ui.e(c)}</th>" for c in pv.columns)
    rows = []
    for ia, r in pv.iterrows():
        cells = []
        for ib, v in r.items():
            if not np.isfinite(v):
                cells.append("<td>—</td>")
                continue
            al = 0.12 + 0.5 * min(1, abs(v) / (mx or 1))
            bg = f"rgba(48,209,88,{al:.2f})" if v > 0 else f"rgba(255,69,58,{al:.2f})"
            cells.append(f'<td style="background:{bg};color:#fff">{v:+.2f}R<br><span style="font-weight:500;font-size:11px;opacity:.75">{int(cnt.loc[ia, ib])} trades</span></td>')
        rows.append(f"<tr><td>{ui.e(bt.PARAM_LABEL.get(a, a))}: {ui.e(ia)}</td>{''.join(cells)}</tr>")
    return f'<div class="q-tw"><table class="q-t q-hm"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _bd_table(tb: pd.DataFrame, by: str, title: str, order=None) -> str:
    d = bt.breakdown(tb, by, order)
    if d.empty:
        return ""
    rows = "".join(f'<tr><td>{ui.e(i)}</td><td>{int(r.trades)}</td><td>{r.win:.0f}%</td>'
                   f'<td style="color:{ui.signed_color(r.exp) or ui.TX}">{r.exp:+.2f}R</td><td style="color:{ui.signed_color(r.total) or ui.TX}">{r.total:+.0f}R</td></tr>'
                   for i, r in d.iterrows())
    return (f'<div class="q-card"><div class="q-ch">{ui.e(title)}</div><div class="q-tw"><table class="q-t"><thead><tr><th></th><th>Trades</th><th>Win</th>'
            f'<th>Expectancy</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table></div></div>')


def _results(res: dict, on_open=None):
    cfg = res["cfg"]
    ph = ui.phone()
    p_name, n_syms, last_day, note, miss = res["panel_key"]
    tr = res["trades"]
    taken = res["taken"]
    tone, head, body = bt.verdict(res)
    col = {"good": ui.GREEN, "warn": ui.YELLOW, "bad": ui.RED}[tone]
    st.markdown(f'<div class="q-verdict" style="--c:{col}"><b>{ui.e(head)}</b><p>{ui.e(body)}</p>'
                f'<p style="color:{ui.TX3};font-size:12px">{ui.e(cfg["setup"])} · {ui.e(cfg["uni"])} ({n_syms} stocks) · '
                f'{"walk-forward, out-of-sample only" if res["mode"] == "walk" else "single test"} · costs {cfg["cost"]:.2f}% per round trip · data to {last_day}</p></div>',
                unsafe_allow_html=True)
    if tr is None or tr.empty:
        st.markdown('<div class="q-card"><div class="q-sub">No trades. The rules may be too strict for this universe: lower the RS or volume filter.</div></div>',
                    unsafe_allow_html=True)
        return
    s = bt.trade_stats(tr["r"], tr["days"])
    cs = bt.curve_stats(res["curve"])
    idx = pd.Series(res["curve"].index)
    p_close = None
    try:
        p_close = res.get("bench")
    except Exception:
        pass
    pf = "∞" if not np.isfinite(s["pf"]) else f'{s["pf"]:.2f}'
    kp = [ui.kpi("Expectancy", f'{s["exp"]:+.2f}R', "average result per trade", ui.signed_color(s["exp"])),
          ui.kpi("Win rate", f'{s["win"]:.0f}%', f'{s["n"]} trades'),
          ui.kpi("Average win / loss", f'<span style="color:{ui.GREEN}">{s["avg_win"]:+.2f}R</span> / <span style="color:{ui.RED}">{s["avg_loss"]:.2f}R</span>', "after costs"),
          ui.kpi("Profit factor", pf, "gross wins ÷ gross losses"),
          ui.kpi("SQN", f'{s["sqn"]:.2f}' if np.isfinite(s["sqn"]) else "—", "1.6+ good · 2.5+ excellent"),
          ui.kpi("Holding period", f'{s["avg_days"]:.0f} days', f'longest losing streak {s["max_losing_streak"]}')]
    if cs:
        kp += [ui.kpi("Portfolio CAGR", f'{cs["cagr"]:+.1f}%', f'{cs["years"]:.1f} years · {len(taken)} trades taken', ui.signed_color(cs["cagr"])),
               ui.kpi("Maximum drawdown", f'{cs["maxdd"]:.1f}%', f'{cs["dd_peak"]:%b %Y} to {cs["dd_trough"]:%b %Y}', ui.RED)]
        bench = res.get("bench_stats")
        if bench:
            kp.append(ui.kpi("NIFTY 50, same period", f'{bench["cagr"]:+.1f}%', f'max drawdown {bench["maxdd"]:.1f}%'))
    st.markdown('<div class="q-kpis">' + "".join(kp) + "</div>", unsafe_allow_html=True)

    curve = res["curve"]
    if len(curve) > 2:
        base = curve / curve.iloc[0] * 100
        bn = res.get("bench_curve")
        series = [(base.to_numpy(), ui.AMBER, 1.8, "Strategy")]
        if bn is not None:
            series.append((bn.to_numpy(), "#9A9AA0", 1.2, "NIFTY 50"))
        ch = ui.line_chart(base.index, series, height=260 if not ph else 200, log=True, area_first=True)
        dd = cs["dd_series"] * 100
        ddc = ui.line_chart(dd.index, [(dd.to_numpy(), ui.RED, 1.2, "Drawdown")], height=110, y_suffix="%", area_first=True)
        st.markdown(f'<div class="q-card"><div class="q-ch">Equity curve<em>₹{cfg["book"]["capital"]:,.0f} start · risk {cfg["book"]["risk_pct"]}% per trade · '
                    f'max {cfg["book"]["max_pos"]} positions · log scale</em></div>{ch}'
                    f'{ui.legend([("Strategy", ui.AMBER, False), ("NIFTY 50", "#9A9AA0", False)])}'
                    f'<div class="q-ch" style="margin-top:12px">Drawdown<em>% below the previous peak</em></div>{ddc}</div>', unsafe_allow_html=True)

    cards = []
    if res["mode"] == "walk":
        w = res["windows"]
        wfe = res.get("wfe", np.nan)
        rows = []
        for r in w.itertuples():
            ch_ = ", ".join(f"{bt.PARAM_LABEL.get(k, k).split(' ')[0] if k != 'trail' else 'Trail'} {v}" for k, v in (r.chosen or {}).items()) or "no variant had enough trades"
            rows.append(f'<tr><td>{r.test_from:%b %Y} to {r.test_to:%b %Y}</td><td style="text-align:left;white-space:normal">{ui.e(ch_)}</td>'
                        f'<td>{r.is_exp:+.2f}R</td><td style="color:{ui.signed_color(r.oos_exp) or ui.TX}">{(f"{r.oos_exp:+.2f}R") if np.isfinite(r.oos_exp) else "—"}</td><td>{r.oos_n}</td></tr>')
        wfe_txt = (f"Walk-forward efficiency <b style='color:#fff'>{wfe:.2f}</b>: the unseen months kept {wfe * 100:.0f}% of the in-sample edge. "
                   + ("Above 0.5 means the edge survives contact with new data." if wfe >= 0.5 else "Below 0.5 means much of the in-sample edge was curve-fitting.")
                   if np.isfinite(wfe) else "The tuned versions had no positive in-sample edge, so efficiency is not meaningful.")
        cards.append(f'<div class="q-card"><div class="q-ch">Walk-forward windows<em>tuned on {cfg["train"]} years, traded the next {cfg["test"]} months</em></div>'
                     f'<div class="q-sub" style="margin-bottom:8px">In-sample {res["is_exp"]:+.2f}R per trade vs out-of-sample {res["oos_exp"]:+.2f}R. {wfe_txt} '
                     f'{res["variants"]} variants tried per window; only the out-of-sample months count in every number on this page.</div>'
                     f'<div class="q-tw"><table class="q-t"><thead><tr><th>Unseen period</th><th style="text-align:left">Settings chosen from the years before</th>'
                     f'<th>In-sample</th><th>Out-of-sample</th><th>Trades</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></div>')
        gr = res["grid_results"]
        keys = [k for k in gr.columns if k not in ("trades", "exp", "win", "pf")]
        if len(keys) >= 2:
            cards.append(f'<div class="q-card"><div class="q-ch">Every variant over the whole period<em>average expectancy · broad green = robust, one green cell = luck</em></div>'
                         f'{_heat(gr, keys[0], keys[1])}</div>')
    else:
        sens = res.get("sensitivity")
        a = res.get("sens_axes") or []
        if sens is not None and len(a) == 2:
            cards.append(f'<div class="q-card"><div class="q-ch">Nearby settings<em>is your setting a plateau or a lucky peak?</em></div>{_heat(sens, a[0], a[1])}'
                         f'<div class="q-note">A robust rule stays positive when its settings move a little. If only your exact setting is green, expect it to fail live.</div></div>')
    for c in cards:
        st.markdown(c, unsafe_allow_html=True)

    mc = bt.monte_carlo(tr["r"].to_numpy(), cfg["book"]["risk_pct"], n_trades=100)
    ss = bt.stop_study(tr)
    left = right = ""
    if mc:
        hist = ui.histogram(mc["mdd_hist"], 26, ui.RED, 120, None, [(mc["dd_med"], "#fff", "typical"), (mc["dd_p95"], ui.AMBER, "bad luck 1 in 20")], "%")
        left = (f'<div class="q-card"><div class="q-ch">Monte Carlo · your next 100 trades<em>{mc["sims"]:,} reshuffles at {cfg["book"]["risk_pct"]}% risk</em></div>'
                f'<div class="q-sub">Typical drawdown <b style="color:#fff">{mc["dd_med"]:.1f}%</b>; 1 time in 20 it reaches <b style="color:{ui.RED}">{mc["dd_p95"]:.1f}%</b>. '
                f'Expect a losing streak of about {mc["streak_med"]:.0f} trades, and {mc["streak_p90"]:.0f} in a bad stretch. '
                f'Result after 100 trades: {mc["ret_p05"]:+.0f}% (bad luck) to {mc["ret_p95"]:+.0f}% (good luck), median {mc["ret_med"]:+.0f}%; '
                f'{mc["p_loss"]:.0f}% chance of being down after 100 trades.</div>{hist}'
                f'<div class="q-note">Distribution of maximum drawdowns. Decide now whether you can live with the amber line, because it will happen.</div></div>')
    if ss:
        x, e = ss["tight_stop"]
        better = e > ss["base_exp"] + 0.02
        right = (f'<div class="q-card"><div class="q-ch">Stop and exit study<em>from how far each trade went for and against you</em></div>'
                 f'<div class="q-rs" style="--c:{ui.BLUE}"><i></i><span>{ss["win_mae_share_05"]:.0f}% of winners never went more than 0.5R against you.</span></div>'
                 f'<div class="q-rs" style="--c:{ui.YELLOW if ss["giveback"] > 1 else ui.GREEN}"><i></i><span>Winners gave back {ss["giveback"]:.1f}R on average from their best point'
                 f'{" — a tighter trail or partial profits could keep more." if ss["giveback"] > 1 else "."}</span></div>'
                 f'<div class="q-rs" style="--c:{ui.YELLOW if ss["losers_hit_1r"] > 25 else ui.BLUE}"><i></i><span>{ss["losers_hit_1r"]:.0f}% of all trades were up 1R or more and still ended as losers'
                 f'{" — consider moving the stop to entry at +1R." if ss["losers_hit_1r"] > 25 else "."}</span></div>'
                 f'<div class="q-rs" style="--c:{ui.GREEN if better else ui.BLUE}"><i></i><span>'
                 + (f"A stop at {x:.2f}R instead of 1R would have lifted expectancy from {ss['base_exp']:+.2f}R to about {e:+.2f}R (estimate)." if better
                    else "Tighter stops (0.5R to 0.75R) would not have improved expectancy: your stop placement looks right.")
                 + "</span></div></div>")
    if left or right:
        st.markdown(f'<div class="q-two">{left}{right}</div>', unsafe_allow_html=True)

    tb = bt.add_buckets(tr, res.get("state"))
    parts = [_bd_table(tb, "regime", "By market regime at the signal", rg.STATES),
             _bd_table(tb, "rs_bucket", "By RS rating", ["Under 70", "70 to 79", "80 to 89", "90 to 99"]),
             _bd_table(tb, "ext_bucket", "By distance from the 21 EMA", ["Below 21 EMA", "0 to 2%", "2 to 5%", "5 to 8%", "Over 8%"]),
             _bd_table(tb, "year", "By year"), _bd_table(tb, "reason", "By exit")]
    sec = bt.breakdown(tb, "sector")
    if not sec.empty and sec.index.astype(str).str.len().max() > 0 and len(sec) > 1:
        sec = sec.sort_values("total", ascending=False)
        tb2 = tb[tb["sector"].isin(list(sec.index[:6]) + list(sec.index[-3:]))]
        parts.append(_bd_table(tb2, "sector", "By sector (best and worst)", list(dict.fromkeys(list(sec.index[:6]) + list(sec.index[-3:])))))
    parts = [x for x in parts if x]
    st.markdown('<div class="q-three">' + "".join(parts[:3]) + '</div><div class="q-three">' + "".join(parts[3:]) + "</div>", unsafe_allow_html=True)

    st.markdown('<div class="q-sec" style="font:600 14px Inter,sans-serif;margin:6px 2px 8px">Trades taken by the portfolio</div>', unsafe_allow_html=True)
    if taken is not None and not taken.empty:
        show = taken.sort_values("entry_i", ascending=False).head(400)
        view = pd.DataFrame({"Stock": show["symbol"], "Entry": pd.to_datetime(show["entry_date"]).dt.strftime("%d %b %Y"),
                             "Exit": [("open" if r == "Open at end" else pd.Timestamp(d).strftime("%d %b %Y")) for r, d in zip(show["reason"], show["exit_date"])],
                             "Entry ₹": show["entry"].round(2), "Stop ₹": show["stop"].round(2), "Exit ₹": show["exit"].round(2),
                             "R": show["r"].round(2), "P&L ₹": show["pnl"].round(0), "Days": show["days"], "Exit reason": show["reason"],
                             "RS": show["rs"].astype(int), "Qty": show["qty"]})
        st.dataframe(view, use_container_width=True, hide_index=True, height=360)
        c1, c2, c3 = st.columns([1.2, 1.2, 1], gap="small")
        c1.download_button("Download trades (CSV)", taken.drop(columns=[c for c in ("j",) if c in taken]).to_csv(index=False).encode(),
                           f"arka_backtest_{cfg['setup'].split()[0].lower()}.csv", "text/csv", key="bt_csv", use_container_width=True)
        if on_open:
            pick = c2.selectbox("Open a stock", list(dict.fromkeys(show["symbol"])), key="bt_open_pick", label_visibility="collapsed")
            if c3.button("Open chart", key="bt_open", use_container_width=True):
                on_open(pick)
    st.markdown(f'<div class="q-note">How to read this: R = profit or loss divided by the risk you took (entry minus stop); +2R means twice what you risked. '
                f'Signals use only data up to that day\'s close and are entered at the next open; stops are checked before targets; a gap through the stop '
                f'fills at the open; costs of {cfg["cost"]:.2f}% are taken off every round trip. '
                f'<b>Survivorship bias:</b> the universe is today\'s {ui.e(p_name)} members ({n_syms} stocks with data{f", {miss} returned none" if miss else ""}), so stocks that '
                f'were dropped or delisted are missing and results look somewhat better than reality. {ui.e(note)}. A research tool, not investment advice.</div>',
                unsafe_allow_html=True)


def _bench(res: dict, p: dict):
    curve = res["curve"]
    if curve is None or len(curve) < 2:
        return
    ic = pd.Series(p["idx_c"], index=p["dates"]).reindex(curve.index).ffill()
    res["bench_curve"] = ic / ic.iloc[0] * 100
    res["bench_stats"] = bt.curve_stats(ic)


def render_backtest(on_open=None):
    ui.css()
    # the page title and subtitle come from app.py (PAGE_META)
    cfg, run = _controls()
    key = json.dumps({k: v for k, v in cfg.items()}, sort_keys=True, default=str)
    if run:
        res, err = _run(cfg)
        if err:
            st.markdown(f'<div class="q-card"><div class="q-ch">Could not run</div><div class="q-sub">{ui.e(err)}</div></div>', unsafe_allow_html=True)
            return
        p, _ = get_panel(cfg["uni"], cfg["years"])
        _bench(res, p)
        st.session_state["bt_result"] = res
        st.session_state["bt_result_key"] = key
    res = st.session_state.get("bt_result")
    if res is None:
        st.markdown('<div class="q-card"><div class="q-ch">How it works</div><div class="q-sub">'
                    '1. Pick a setup and adjust its rules, stop, exit, risk and costs (sensible defaults are already set).<br>'
                    '2. Run. The first run of the day downloads prices (a minute or two for 500 stocks); later runs take seconds.<br>'
                    '3. Read the verdict, then the walk-forward table, the Monte Carlo drawdowns and the breakdowns by regime, RS and extension.<br><br>'
                    'Walk-forward mode tries several versions of the rule on each block of past years, picks the best using only those years, '
                    'then trades it on the next months it has never seen. Stitching those unseen months together gives the honest result.</div></div>',
                    unsafe_allow_html=True)
        return
    if st.session_state.get("bt_result_key") != key:
        st.markdown(f'<div class="q-note" style="color:{ui.YELLOW}">Settings changed since this result. Press Run backtest to update it.</div>', unsafe_allow_html=True)
    _results(res, on_open)
