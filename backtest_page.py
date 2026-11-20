"""
Arka Trades — backtest_page.py (v2): Backtest lab with your own rules, on the stocks you choose.

  1. Stocks   every stock of an index, or only the stocks you pick (any NSE symbol).
  2. Rules    build the entry from conditions you add, edit and remove. Templates only pre-fill the list; nothing
              runs that is not shown on the page.
  3. Trade    entry type, stop, exits, capital, risk, costs, optional market-regime filter.
  4. Run      "My exact rules" tests them as they are. Walk-forward (optional) tries only the alternative values you type.

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
REGIME_MODES = ["Off", "Skip corrections", "Scale by regime"]
MODES = ["My exact rules", "Walk-forward: try the values I list"]


# ═════════════════════════ rule builder state ═════════════════════════
def _next_id() -> int:
    st.session_state["bt_rule_seq"] = st.session_state.get("bt_rule_seq", 0) + 1
    return st.session_state["bt_rule_seq"]


def _set_rules(rules: list):
    st.session_state["bt_rules"] = [{**r, "id": _next_id()} for r in rules]


def _cb_load():
    _set_rules([dict(r) for r in bt.TEMPLATES[st.session_state.get("bt_tpl", "Breakout")]])


def _cb_add():
    st.session_state.setdefault("bt_rules", []).append({**bt.new_rule(st.session_state["bt_add_type"]), "id": _next_id()})


def _cb_remove(rid):
    st.session_state["bt_rules"] = [r for r in st.session_state.get("bt_rules", []) if r["id"] != rid]


def _rule_inputs(rule: dict, n: int):
    """One condition: its sentence, its settings, a remove button. Values are written back into the rule."""
    grp, label, specs, _ = bt.RULES[rule["type"]]
    rid = rule["id"]
    st.markdown(f'<div class="q-rule"><em>{n}</em>{ui.e(label)}</div>', unsafe_allow_html=True)
    widths = [1.3] * len(specs) + [max(0.6, 5.2 - 1.3 * len(specs)), 0.8]
    cols = st.columns(widths, gap="small")
    for c, spec in zip(cols, specs):
        key = f"bt_r{rid}_{spec[0]}"
        if isinstance(spec[3], list) and isinstance(spec[2], list):
            rule[spec[0]] = c.multiselect(spec[1], spec[3], default=rule[spec[0]], key=key) or list(spec[2])
        elif isinstance(spec[3], list):
            rule[spec[0]] = c.selectbox(spec[1], spec[3], index=spec[3].index(rule[spec[0]]), key=key)
        elif isinstance(spec[2], int):
            rule[spec[0]] = int(c.number_input(spec[1], min_value=int(spec[3]), max_value=int(spec[4]), value=int(rule[spec[0]]), step=int(spec[5]), key=key))
        else:
            rule[spec[0]] = float(c.number_input(spec[1], min_value=float(spec[3]), max_value=float(spec[4]), value=float(rule[spec[0]]),
                                                 step=float(spec[5]), key=key))
    cols[-1].button("Remove", key=f"bt_rm{rid}", on_click=_cb_remove, args=(rid,), use_container_width=True)


def _parse_values(text: str, as_int: bool) -> list:
    out = []
    for tok in str(text or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(float(tok)) if as_int else float(tok))
        except ValueError:
            pass
    return list(dict.fromkeys(out))[:5]


# ═════════════════════════ controls ═════════════════════════
def _controls():
    ph = ui.phone()
    if "bt_rules" not in st.session_state:
        _set_rules([dict(r) for r in bt.TEMPLATES["Breakout"]])

    with st.container(key="q_ctrl_stocks"):
        st.markdown('<div class="q-step"><em>1</em>Stocks</div>', unsafe_allow_html=True)
        a, b = st.columns([1.4, 1], gap="small")
        uni = a.selectbox("Index (also the group RS rating is ranked against)", UNIVERSES, key="bt_uni",
                          help="Today's members of the index. RS rating compares each stock with every stock in this index.")
        years = b.selectbox("History", [10, 8, 5, 12, 3], key="bt_years", format_func=lambda y: f"{y} years")
        scope = ui.choice("Scope", ["Every stock in the index", "Only the stocks I pick"], "bt_scope")
        picked, extra = [], []
        if scope == "Only the stocks I pick":
            members, _ = qd.universe(uni, st.session_state.get("watchlist", []))
            picked = st.multiselect(f"Pick from {uni}", sorted(members), key="bt_pick", placeholder="Type to search: RELIANCE, TCS …")
            other = st.text_input("Other NSE symbols, comma separated (optional)", key="bt_other", placeholder="e.g. ECLERX, STYL, URBANCO")
            extra = [s.strip().upper() for s in other.replace(";", ",").split(",") if s.strip()]
            picked = list(dict.fromkeys(list(picked) + extra))
            if not picked:
                st.markdown('<div class="q-note">Pick at least one stock, or switch to every stock in the index.</div>', unsafe_allow_html=True)

    with st.container(key="q_ctrl_rules"):
        st.markdown('<div class="q-step"><em>2</em>Your entry rules<span style="font-weight:500;font-size:12.5px;color:#8A8A90;margin-left:10px">'
                    'a stock gives a signal on a day only when every condition below is true at that day\'s close</span></div>', unsafe_allow_html=True)
        t1, t2 = st.columns([3, 1], gap="small")
        t1.selectbox("Start from a template (it only fills the list; edit or remove anything)", list(bt.TEMPLATES), key="bt_tpl")
        t2.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        t2.button("Load template", key="bt_tpl_go", on_click=_cb_load, use_container_width=True)
        rules = st.session_state["bt_rules"]
        if not rules:
            st.markdown('<div class="q-note">No conditions yet. Add your first one below.</div>', unsafe_allow_html=True)
        for n, r in enumerate(rules, 1):
            _rule_inputs(r, n)
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        a1, a2 = st.columns([3, 1], gap="small")
        a1.selectbox("Add a condition", list(bt.RULES), key="bt_add_type", format_func=lambda k: f"{bt.RULES[k][0]} · {bt.RULES[k][1]}")
        a2.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        a2.button("Add condition", key="bt_add_go", on_click=_cb_add, use_container_width=True, type="primary")

    with st.container(key="q_ctrl_trade"):
        st.markdown('<div class="q-step"><em>3</em>How the trade is taken and managed</div>', unsafe_allow_html=True)
        ex = dict(bt.EXIT_DEFAULT)
        e1, e2 = st.columns([1.6, 1], gap="small")
        ex["entry"] = e1.selectbox("Entry", bt.ENTRIES, key="bt_entry",
                                   help="Second option: a buy-stop just above the signal day's high, valid for the next session only.")
        ex["gap_max"] = e2.number_input("Skip if the next open gaps up more than %", 0.5, 30.0, 4.0, 0.5, key="bt_gap")
        s1, s2, s3 = st.columns(3, gap="small")
        ex["stop"] = s1.selectbox("Initial stop", bt.STOPS, key="bt_stop")
        if ex["stop"] == bt.STOPS[1]:
            ex["stop_n"] = int(s2.number_input("N days (including the signal day)", 2, 60, 5, 1, key="bt_sn"))
        elif ex["stop"] == bt.STOPS[2]:
            ex["stop_atr"] = s2.number_input("ATR × below entry", 0.5, 6.0, 1.5, 0.25, key="bt_satr")
        elif ex["stop"] == bt.STOPS[3]:
            ex["stop_pct"] = s2.number_input("% below entry", 1.0, 25.0, 7.0, 0.5, key="bt_spct")
        else:
            s2.markdown('<div class="q-note" style="margin-top:30px">Stop at the low of the signal day.</div>', unsafe_allow_html=True)
        ex["max_risk"] = s3.number_input("Never risk more than % per share", 2.0, 25.0, 10.0, 0.5, key="bt_mrisk",
                                         help="If the stop is further away than this, it is pulled up to this distance.")
        x = st.columns(5, gap="small") if not ph else (st.columns(2) + st.columns(2) + st.columns(1))
        ex["trail_kind"] = x[0].selectbox("Trailing average", ["EMA", "SMA"], key="bt_tk")
        ex["trail_n"] = int(x[1].number_input("Exit on a close below length (0 = off)", 0, 250, 21, 1, key="bt_tn"))
        ex["target_r"] = x[2].number_input("Profit target in R (0 = none)", 0.0, 30.0, 0.0, 0.5, key="bt_tgt")
        ex["breakeven_r"] = x[3].number_input("Stop to entry at +R (0 = never)", 0.0, 10.0, 0.0, 0.5, key="bt_be")
        ex["max_days"] = int(x[4].number_input("Time stop, sessions", 2, 400, 40, 1, key="bt_days"))
        k = st.columns(5, gap="small") if not ph else (st.columns(2) + st.columns(2) + st.columns(1))
        book = bt.Book()
        book.capital = float(k[0].number_input("Capital ₹", 10_000.0, 1e10, 1_000_000.0, 50_000.0, key="bt_cap", format="%.0f"))
        book.risk_pct = float(k[1].number_input("Risk per trade %", 0.1, 10.0, 1.0, 0.1, key="bt_risk"))
        book.max_pos = int(k[2].number_input("Maximum open positions", 1, 50, 8, 1, key="bt_maxpos"))
        book.max_pos_pct = float(k[3].number_input("Maximum position size %", 5.0, 100.0, 25.0, 5.0, key="bt_maxpct"))
        cost = float(k[4].number_input("Costs + slippage, % round trip", 0.0, 3.0, bt.COST_DEFAULT, 0.05, key="bt_cost",
                                       help="Delivery: STT 0.1% each side, stamp duty, exchange and SEBI fees, GST and DP charges ≈ 0.25%, plus ≈ 0.1% slippage. "
                                            "Use 0.5% or more for small caps."))
        book.regime_mode = st.selectbox("Market regime filter on new trades", REGIME_MODES, key="bt_reg",
                                        help="Off: the regime plays no part (unless you added a Market condition yourself). Skip corrections: no new trades when "
                                             "the signal day is in a Correction. Scale by regime: risk 100 / 50 / 25 / 0% of normal by state.")

    with st.container(key="q_ctrl_run"):
        st.markdown('<div class="q-step"><em>4</em>Test</div>', unsafe_allow_html=True)
        mode = ui.choice("Mode", MODES, "bt_mode")
        grid, nearby, train, test = {}, False, 3, 6
        if mode == MODES[0]:
            nearby = st.toggle("Also check nearby settings (each number moved about 25% down and up; takes longer)", value=True, key="bt_nearby")
        else:
            st.markdown('<div class="q-note">Walk-forward does not invent rules. It tries only the alternative values you type here on each block of past '
                        'years, then trades the best one on the next unseen months. Leave a box empty to keep your value fixed.</div>', unsafe_allow_html=True)
            opts = [(i, sp[0], f"{i + 1}. {bt.RULES[r['type']][1]} · {sp[1]} (now {r[sp[0]]})", isinstance(sp[2], int))
                    for i, r in enumerate(rules) for sp in bt.RULES[r["type"]][2] if not isinstance(sp[2], (list, str))]
            labels = ["—"] + [o[2] for o in opts]
            for slot in (1, 2):
                g1, g2 = st.columns([2, 1.2], gap="small")
                pick = g1.selectbox(f"Setting {slot} to vary", labels, key=f"bt_wf_p{slot}")
                vals = g2.text_input("Values to try, comma separated", key=f"bt_wf_v{slot}", placeholder="e.g. 20, 50")
                if pick != "—":
                    o = opts[labels.index(pick) - 1]
                    vv = _parse_values(vals, o[3])
                    if vv:
                        grid[(o[0], o[1])] = vv
            tv = _parse_values(st.text_input("Trailing-exit lengths to try (optional)", key="bt_wf_trail", placeholder="e.g. 10, 21, 50"), True)
            if tv:
                grid[("exit", "trail_n")] = tv
            w1, w2 = st.columns(2, gap="small")
            train = w1.selectbox("Tune on", [3, 2, 4, 5], key="bt_train", format_func=lambda y: f"{y} years")
            test = w2.selectbox("Then trade unseen", [6, 3, 12], key="bt_test", format_func=lambda m: f"{m} months")
        run = st.button("Run backtest", key="bt_run", type="primary", use_container_width=True)

    clean = [{k: v for k, v in r.items() if k != "id"} for r in rules]
    cfg = {"uni": uni, "years": int(years), "scope": scope, "picked": picked, "extra": extra, "rules": clean, "ex": ex,
           "book": book.__dict__.copy(), "cost": cost, "mode": mode, "grid": [[list(k), v] for k, v in grid.items()], "nearby": bool(nearby),
           "train": int(train), "test": int(test)}
    return cfg, run


def _problems(cfg) -> list:
    out = []
    if not cfg["rules"]:
        out.append("Add at least one entry condition.")
    if cfg["scope"] == "Only the stocks I pick" and not cfg["picked"]:
        out.append("Pick at least one stock.")
    for r in cfg["rules"]:
        if r["type"] == "ma_above_ma" and r["fast"] >= r["slow"]:
            out.append("In 'Fast average above slow average' the fast length must be smaller than the slow length.")
        if r["type"] in ("day_change", "pullback_depth") and r["lo"] > r["hi"]:
            out.append(f"In '{bt.RULES[r['type']][1]}' the first % must not be larger than the second.")
    if cfg["mode"] == MODES[1] and not cfg["grid"]:
        out.append("Walk-forward needs at least one setting with values to try. Otherwise use 'My exact rules'.")
    n = 1
    for _, v in cfg["grid"]:
        n *= len(v)
    if n > 60:
        out.append(f"{n} combinations is too many (and invites curve-fitting). Keep it to 60 or fewer.")
    return out


# ═════════════════════════ run ═════════════════════════
def _run(cfg: dict):
    p, err = get_panel(cfg["uni"], cfg["years"], False, cfg["extra"])
    if p is None:
        return None, f"Could not load prices: {err}"
    cols, not_found = None, []
    if cfg["scope"] == "Only the stocks I pick":
        pos = {s: i for i, s in enumerate(p["symbols"])}
        cols = [pos[s] for s in cfg["picked"] if s in pos]
        not_found = [s for s in cfg["picked"] if s not in pos]
        if not cols:
            return None, "None of the picked stocks returned prices from Yahoo: " + ", ".join(not_found) + ". Check the NSE symbols."
    df = _regime_for(p)
    state = df["state"].reindex(p["dates"]).to_numpy(dtype=object)
    book = bt.Book(**cfg["book"])
    bar = st.progress(0.0, text="Testing…")
    try:
        if cfg["mode"] == MODES[1]:
            grid = {tuple(k): v for k, v in cfg["grid"]}
            res = bt.run_walk_forward(p, cfg["rules"], cfg["ex"], book, cfg["cost"], grid, cfg["train"], cfg["test"], state, cols,
                                      progress=lambda f, m: bar.progress(min(1.0, f), text=m))
        else:
            bar.progress(0.15, text="Simulating every signal")
            res = bt.run_single(p, cfg["rules"], cfg["ex"], book, cfg["cost"], state, cols)
            if cfg["nearby"]:
                res["sensitivity"] = bt.sensitivity(p, cfg["rules"], cfg["ex"], cfg["cost"], state, cols,
                                                    progress=lambda f, m: bar.progress(min(1.0, 0.2 + 0.8 * f), text=m))
    finally:
        bar.empty()
    curve = res["curve"]
    if curve is not None and len(curve) > 1:
        ic = pd.Series(p["idx_c"], index=p["dates"]).reindex(curve.index).ffill()
        res["bench_curve"] = ic / ic.iloc[0] * 100
        res["bench_stats"] = bt.curve_stats(ic)
    n_scope = len(cols) if cols is not None else len(p["symbols"])
    res.update(state=state, cfg=cfg, not_found=not_found,
               info={"n_syms": len(p["symbols"]), "n_scope": n_scope, "last": str(p["dates"][-1].date()), "note": p.get("note", ""),
                     "missing": len(p.get("missing") or []), "first": str(p["dates"][res["start_i"]].date())})
    return res, None


# ═════════════════════════ results ═════════════════════════
def _bd_table(tb: pd.DataFrame, by: str, title: str, order=None) -> str:
    d = bt.breakdown(tb, by, order)
    if d.empty:
        return ""
    rows = "".join(f'<tr><td>{ui.e(i)}</td><td>{int(r.trades)}</td><td>{r.win:.0f}%</td>'
                   f'<td style="color:{ui.signed_color(r.exp) or ui.TX}">{r.exp:+.2f}R</td><td style="color:{ui.signed_color(r.total) or ui.TX}">{r.total:+.0f}R</td></tr>'
                   for i, r in d.iterrows())
    return (f'<div class="q-card"><div class="q-ch">{ui.e(title)}</div><div class="q-tw"><table class="q-t"><thead><tr><th></th><th>Trades</th><th>Win</th>'
            f'<th>Expectancy</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table></div></div>')


def _exp(v, n=None):
    if v is None or not np.isfinite(v):
        return "—"
    return f'<span style="color:{ui.signed_color(v) or ui.TX}">{v:+.2f}R</span>' + (f' <span style="color:{ui.TX3}">· {int(n)}</span>' if n is not None else "")


def _combo_text(cfg, cmb) -> str:
    parts = []
    for (a, b), v in cmb.items():
        if a == "exit":
            parts.append(f"trail {v}")
        else:
            sp = next(s for s in bt.RULES[cfg["rules"][a]["type"]][2] if s[0] == b)
            parts.append(f"{a + 1}. {sp[1]} {v}")
    return ", ".join(parts)


def _stock_chart(res, sym):
    cfg = res["cfg"]
    p, err = get_panel(cfg["uni"], cfg["years"], False, cfg["extra"])
    if p is None or sym not in p["symbols"]:
        return ""
    j = p["symbols"].index(sym)
    tr = res["trades"]
    tr = tr[tr["symbol"] == sym]
    s0 = res["start_i"]
    c = p["c"][s0:, j].astype(float)
    dates = p["dates"][s0:]
    series = [(c, "#F2F2F5", 1.4, "Close")]
    tn = int(cfg["ex"].get("trail_n") or 0)
    if tn:
        series.append((bt.ma(p, cfg["ex"]["trail_kind"], tn)[s0:, j].astype(float), ui.AMBER, 1.0, "Trail"))
    marks = [(int(r.entry_i) - s0, r.entry, ui.GREEN, "up") for r in tr.itertuples()] + \
            [(int(r.exit_i) - s0, r.exit, ui.RED, "down") for r in tr.itertuples() if r.reason != "Open at end"]
    ch = ui.line_chart(dates, series, height=280 if not ui.phone() else 220, log=True, markers=marks)
    s = bt.trade_stats(tr["r"]) if len(tr) else {"n": 0}
    sub = f'{s["n"]} trades · win {s["win"]:.0f}% · {s["exp"]:+.2f}R per trade · total {s["total_r"]:+.1f}R' if s["n"] else "no trades"
    leg = ui.legend([("Close (adjusted)", "#F2F2F5", False)] + ([(f'{tn} {cfg["ex"]["trail_kind"]}', ui.AMBER, False)] if tn else [])
                    + [("Entry ▲", ui.GREEN, True), ("Exit ▼", ui.RED, True)])
    return f'<div class="q-card"><div class="q-ch">{ui.e(sym)}<em>{sub}</em></div>{ch}{leg}</div>'


def _results(res: dict, on_open=None):
    cfg, info = res["cfg"], res["info"]
    ph = ui.phone()
    tr, taken = res["trades"], res["taken"]
    tone, head, body = bt.verdict(res)
    col = {"good": ui.GREEN, "warn": ui.YELLOW, "bad": ui.RED}[tone]
    scope = (f'{info["n_scope"]} picked stock{"s" if info["n_scope"] != 1 else ""}' if cfg["scope"] != "Every stock in the index"
             else f'{cfg["uni"]} ({info["n_scope"]} stocks)')
    st.markdown(f'<div class="q-verdict" style="--c:{col}"><b>{ui.e(head)}</b><p>{ui.e(body)}</p>'
                f'<p style="color:{ui.TX3};font-size:12px">{ui.e(scope)} · {len(cfg["rules"])} conditions · '
                f'{"walk-forward, unseen months only" if res["mode"] == "walk" else "your exact rules"} · {info["first"]} to {info["last"]} · '
                f'costs {cfg["cost"]:.2f}% per round trip</p></div>', unsafe_allow_html=True)
    if res.get("not_found"):
        st.warning("No price data for: " + ", ".join(res["not_found"]) + ". They were left out; check the NSE symbols.")
    rules_txt = "".join(f'<div class="q-rs" style="--c:{ui.BLUE}"><i></i><span>{ui.e(bt.describe(r))}</span></div>' for r in cfg["rules"])
    ex = cfg["ex"]
    stop_txt = {bt.STOPS[0]: "signal-day low", bt.STOPS[1]: f'lowest low of {ex.get("stop_n")} days', bt.STOPS[2]: f'{ex.get("stop_atr")} × ATR',
                bt.STOPS[3]: f'{ex.get("stop_pct")}%'}[ex["stop"]]
    exits = ", ".join(x for x in (f'close below {ex["trail_n"]} {ex["trail_kind"]}' if ex.get("trail_n") else "",
                                  f'target {ex["target_r"]}R' if ex.get("target_r") else "", f'stop to entry at +{ex["breakeven_r"]}R' if ex.get("breakeven_r") else "",
                                  f'time stop {ex["max_days"]} sessions') if x)
    with st.expander("What was tested (your rules, exactly)"):
        st.markdown(f'<div class="q-root">{rules_txt}<div class="q-rs" style="--c:{ui.AMBER}"><i></i><span>Entry: {ui.e(ex["entry"].lower())}; skip gaps over '
                    f'{ex["gap_max"]}%. Stop: {ui.e(stop_txt)}, never more than {ex["max_risk"]}% per share. Exits: {ui.e(exits)}. '
                    f'Regime filter: {ui.e(cfg["book"]["regime_mode"].lower())}.</span></div></div>', unsafe_allow_html=True)
    if tr is None or tr.empty:
        st.markdown('<div class="q-card"><div class="q-sub">No trades. Your conditions were never all true on the same day for these stocks, or every '
                    'signal was skipped at entry. Remove or loosen a condition and run again.</div></div>', unsafe_allow_html=True)
        return
    s = bt.trade_stats(tr["r"], tr["days"])
    cs = bt.curve_stats(res["curve"])
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
        if res.get("bench_stats"):
            kp.append(ui.kpi("NIFTY 50, same period", f'{res["bench_stats"]["cagr"]:+.1f}%', f'max drawdown {res["bench_stats"]["maxdd"]:.1f}%'))
    st.markdown('<div class="q-kpis">' + "".join(kp) + "</div>", unsafe_allow_html=True)

    curve = res["curve"]
    if cs and len(curve) > 2:
        base = curve / curve.iloc[0] * 100
        series = [(base.to_numpy(), ui.AMBER, 1.8, "Strategy")]
        if res.get("bench_curve") is not None:
            series.append((res["bench_curve"].to_numpy(), "#9A9AA0", 1.2, "NIFTY 50"))
        ch = ui.line_chart(base.index, series, height=260 if not ph else 200, log=True, area_first=True)
        dd = cs["dd_series"] * 100
        ddc = ui.line_chart(dd.index, [(dd.to_numpy(), ui.RED, 1.2, "Drawdown")], height=110, y_suffix="%", area_first=True)
        idle = " With only a few stocks most of the capital sits in cash most of the time, so compare expectancy, not CAGR." if info["n_scope"] <= 10 else ""
        st.markdown(f'<div class="q-card"><div class="q-ch">Equity curve<em>₹{cfg["book"]["capital"]:,.0f} start · risk {cfg["book"]["risk_pct"]}% per trade · '
                    f'max {cfg["book"]["max_pos"]} positions · log scale</em></div>{ch}'
                    f'{ui.legend([("Strategy", ui.AMBER, False), ("NIFTY 50", "#9A9AA0", False)])}'
                    f'<div class="q-ch" style="margin-top:12px">Drawdown<em>% below the previous peak</em></div>{ddc}'
                    f'<div class="q-note">{len(tr)} trades simulated, {len(taken)} taken by the portfolio (the rest had no free slot or cash).{idle}</div></div>',
                    unsafe_allow_html=True)

    # robustness
    if res["mode"] == "walk":
        w = res["windows"]
        wfe = res.get("wfe", np.nan)
        rows = "".join(
            f'<tr><td>{r.test_from:%b %Y} to {r.test_to:%b %Y}</td><td style="text-align:left;white-space:normal">'
            f'{ui.e(_combo_text(cfg, r.chosen) if isinstance(r.chosen, dict) else r.note)}</td><td>{_exp(r.is_exp)}</td><td>{_exp(r.oos_exp)}</td><td>{r.oos_n}</td></tr>'
            for r in w.itertuples())
        wfe_txt = (f"Walk-forward efficiency <b style='color:#fff'>{wfe:.2f}</b>: the unseen months kept {wfe * 100:.0f}% of the in-sample edge. "
                   + ("Above 0.5 means the edge survives new data." if wfe >= 0.5 else "Below 0.5 means much of the in-sample edge was curve-fitting.")
                   if np.isfinite(wfe) else "Efficiency cannot be computed (no window had a positive in-sample edge and unseen trades).")
        allv = "".join(f'<tr><td style="text-align:left">{ui.e(_combo_text(cfg, g["combo"]))}</td><td>{g["trades"]}</td><td>{g["win"]:.0f}%</td><td>{_exp(g["exp"])}</td></tr>'
                       for g in res["grid_results"])
        st.markdown(f'<div class="q-card"><div class="q-ch">Walk-forward windows<em>tuned on {cfg["train"]} years, traded the next {cfg["test"]} months</em></div>'
                    f'<div class="q-sub" style="margin-bottom:8px">In-sample {res["is_exp"]:+.2f}R per trade vs unseen {res["oos_exp"]:+.2f}R. {wfe_txt} '
                    f'{res["variants"]} variants from the values you listed; every number on this page counts unseen months only.</div>'
                    f'<div class="q-tw"><table class="q-t"><thead><tr><th>Unseen period</th><th style="text-align:left">Chosen from the years before</th>'
                    f'<th>In-sample</th><th>Unseen</th><th>Trades</th></tr></thead><tbody>{rows}</tbody></table></div></div>'
                    f'<div class="q-card"><div class="q-ch">Every variant over the whole period<em>mostly green = robust · one green row = luck</em></div>'
                    f'<div class="q-tw"><table class="q-t"><thead><tr><th style="text-align:left">Variant</th><th>Trades</th><th>Win</th><th>Expectancy</th></tr></thead>'
                    f'<tbody>{allv}</tbody></table></div></div>', unsafe_allow_html=True)
    else:
        hv = bt.halves(tr)
        left = right = ""
        if hv:
            p_dates = pd.to_datetime(tr["entry_date"])
            ok = hv["a_exp"] > 0 and hv["b_exp"] > 0
            left = (f'<div class="q-card"><div class="q-ch">First half vs second half<em>is the edge stable over time?</em></div>'
                    f'<div class="q-tw"><table class="q-t"><thead><tr><th></th><th>Trades</th><th>Expectancy</th></tr></thead><tbody>'
                    f'<tr><td>First half (from {p_dates.min():%b %Y})</td><td>{hv["a_n"]}</td><td>{_exp(hv["a_exp"])}</td></tr>'
                    f'<tr><td>Second half (to {p_dates.max():%b %Y})</td><td>{hv["b_n"]}</td><td>{_exp(hv["b_exp"])}</td></tr></tbody></table></div>'
                    f'<div class="q-note">{"Positive in both halves: a good sign." if ok else "Not positive in both halves: the edge may come from one lucky stretch."}</div></div>')
        sens = res.get("sensitivity")
        if sens is not None and len(sens):
            base = s["exp"]
            rows, flips = [], 0
            for r in sens.itertuples():
                for v in (r.lower_exp, r.higher_exp):
                    if np.isfinite(v) and base > 0 and v <= 0:
                        flips += 1
                lo_txt = "—" if pd.isna(r.lower) else f"{r.lower:g}: " + _exp(r.lower_exp, r.lower_n)
                hi_txt = "—" if pd.isna(r.higher) else f"{r.higher:g}: " + _exp(r.higher_exp, r.higher_n)
                rows.append(f'<tr><td style="text-align:left;white-space:normal">{ui.e(r.rule)} · {ui.e(r.setting)}</td>'
                            f'<td>{lo_txt}</td><td>{r.yours:g}: {_exp(base)}</td><td>{hi_txt}</td></tr>')
            msg = ("Every nearby setting stays positive: the result does not hang on one exact number." if base > 0 and flips == 0 else
                   f"{flips} nearby setting{'s' if flips != 1 else ''} turn the result negative: fragile, treat with caution." if base > 0 else
                   "Your rules lose money as set; green cells show which direction helps, but re-test before believing them.")
            right = (f'<div class="q-card"><div class="q-ch">Nearby settings<em>each number moved about 25%, one at a time · expectancy · trades</em></div>'
                     f'<div class="q-tw"><table class="q-t"><thead><tr><th style="text-align:left">Setting</th><th>Lower</th><th>Yours</th><th>Higher</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div><div class="q-note">{msg}</div></div>')
        if left or right:
            st.markdown(f'<div class="q-two">{left}{right}</div>', unsafe_allow_html=True)

    mc = bt.monte_carlo(tr["r"].to_numpy(), cfg["book"]["risk_pct"], n_trades=100)
    ss = bt.stop_study(tr)
    left = right = ""
    if mc:
        hist = ui.histogram(mc["mdd_hist"], 26, ui.RED, 120, None, [(mc["dd_med"], "#fff", "typical"), (mc["dd_p95"], ui.AMBER, "bad luck 1 in 20")], "%")
        left = (f'<div class="q-card"><div class="q-ch">Monte Carlo · your next 100 trades<em>{mc["sims"]:,} reshuffles at {cfg["book"]["risk_pct"]}% risk</em></div>'
                f'<div class="q-sub">Typical drawdown <b style="color:#fff">{mc["dd_med"]:.1f}%</b>; 1 time in 20 it reaches <b style="color:{ui.RED}">{mc["dd_p95"]:.1f}%</b>. '
                f'Expect a losing streak of about {mc["streak_med"]:.0f} trades, and {mc["streak_p90"]:.0f} in a bad stretch. '
                f'After 100 trades: {mc["ret_p05"]:+.0f}% (bad luck) to {mc["ret_p95"]:+.0f}% (good luck), median {mc["ret_med"]:+.0f}%; '
                f'{mc["p_loss"]:.0f}% chance of being down. This assumes every trade risks {cfg["book"]["risk_pct"]}% and future trades resemble these.</div>{hist}</div>')
    if ss:
        x, e = ss["tight_stop"]
        better = e > ss["base_exp"] + 0.02
        right = (f'<div class="q-card"><div class="q-ch">Stop and exit study<em>from how far each trade went for and against you</em></div>'
                 f'<div class="q-rs" style="--c:{ui.BLUE}"><i></i><span>{ss["win_mae_share_05"]:.0f}% of winners never went more than 0.5R against you.</span></div>'
                 f'<div class="q-rs" style="--c:{ui.YELLOW if ss["giveback"] > 1 else ui.GREEN}"><i></i><span>Winners gave back {ss["giveback"]:.1f}R on average from their best point.</span></div>'
                 f'<div class="q-rs" style="--c:{ui.YELLOW if ss["losers_hit_1r"] > 25 else ui.BLUE}"><i></i><span>{ss["losers_hit_1r"]:.0f}% of all trades were up 1R or more and still ended as losers.</span></div>'
                 f'<div class="q-rs" style="--c:{ui.GREEN if better else ui.BLUE}"><i></i><span>'
                 + (f"A stop at {x:.2f}R instead of 1R might have lifted expectancy from {ss['base_exp']:+.2f}R to about {e:+.2f}R. This is a rough estimate that ignores gaps: change the stop above and re-run to confirm."
                    if better else "Tighter stops (0.5R to 0.75R) would not have improved expectancy.")
                 + "</span></div></div>")
    if left or right:
        st.markdown(f'<div class="q-two">{left}{right}</div>', unsafe_allow_html=True)

    tb = bt.add_buckets(tr, res.get("state"))
    parts = [x for x in (_bd_table(tb, "regime", "By market regime at the signal", rg.STATES), _bd_table(tb, "year", "By year"),
                         _bd_table(tb, "reason", "By exit"), _bd_table(tb, "rs_bucket", "By RS rating", ["Under 70", "70 to 79", "80 to 89", "90 to 99"]),
                         _bd_table(tb, "ext_bucket", "By distance from the 21 EMA", ["Below 21 EMA", "0 to 2%", "2 to 5%", "5 to 8%", "Over 8%"])) if x]
    st.markdown('<div class="q-three">' + "".join(parts[:3]) + '</div><div class="q-three">' + "".join(parts[3:]) + "</div>", unsafe_allow_html=True)

    ps = bt.per_stock(tr)
    if len(ps):
        st.markdown('<div class="q-step" style="margin-top:6px">Stock by stock</div>', unsafe_allow_html=True)
        view = ps.reset_index().rename(columns={"symbol": "Stock", "trades": "Trades", "win": "Win %", "exp": "Expectancy R", "total": "Total R",
                                                "best": "Best R", "worst": "Worst R"}).round(2)
        st.dataframe(view, use_container_width=True, hide_index=True, height=min(360, 60 + 35 * len(view)))
        c1, c2 = st.columns([3, 1], gap="small")
        if st.session_state.get("bt_chart_sym") not in ps.index:
            st.session_state["bt_chart_sym"] = ps.index[0]
        sym = c1.selectbox("Chart a stock with its entries and exits", list(ps.index), key="bt_chart_sym")
        if on_open and c2.button("Open in Arka", key="bt_open", use_container_width=True):
            on_open(sym)
        st.markdown(_stock_chart(res, sym), unsafe_allow_html=True)

    st.markdown('<div class="q-step" style="margin-top:6px">Every simulated trade</div>', unsafe_allow_html=True)
    show = tr.sort_values("entry_i", ascending=False)
    view = pd.DataFrame({"Stock": show["symbol"], "Entry": pd.to_datetime(show["entry_date"]).dt.strftime("%d %b %Y"),
                         "Exit": [("open" if r == "Open at end" else pd.Timestamp(d).strftime("%d %b %Y")) for r, d in zip(show["reason"], show["exit_date"])],
                         "Entry ₹": show["entry"].round(2), "Stop ₹": show["stop"].round(2), "Exit ₹": show["exit"].round(2), "R": show["r"].round(2),
                         "Return %": show["ret_pct"].round(2), "Days": show["days"], "Exit reason": show["reason"], "RS": show["rs"].round(0)})
    st.dataframe(view.head(1000), use_container_width=True, hide_index=True, height=360)
    st.download_button("Download all trades (CSV)", show.drop(columns=["j"]).to_csv(index=False).encode(), "arka_backtest_trades.csv", "text/csv", key="bt_csv")
    drop = res.get("dropped", 0)
    miss_txt = f"; {info['missing']} symbols returned no data" if info["missing"] else ""
    st.markdown(f'<div class="q-note">R = profit or loss divided by the risk taken (entry minus stop). Prices are adjusted for splits, bonuses and dividends, so old '
                f'prices differ from what the chart showed then. Signals use only data up to that day\'s close; entry is the next session; stops are checked before '
                f'targets; a gap through the stop fills at the open; {cfg["cost"]:.2f}% is taken off every round trip. '
                + (f'{drop} trade{"s" if drop != 1 else ""} crossing a suspect candle (a one-day move beyond −35% / +50%, usually an unadjusted corporate action) were removed. ' if drop else "")
                + f'<b>Survivorship bias:</b> index lists are today\'s members, so stocks that were dropped or delisted are missing and results look somewhat better than '
                f'reality. <b>Picked stocks:</b> choosing stocks you already know went up makes any rule look good; that is hindsight, not an edge. '
                f'{ui.e(info["note"])}{miss_txt}. A research tool, not investment advice.</div>',
                unsafe_allow_html=True)


def render_backtest(on_open=None):
    ui.css()
    cfg, run = _controls()
    key = json.dumps(cfg, sort_keys=True, default=str)
    if run:
        probs = _problems(cfg)
        if probs:
            for m in probs:
                st.warning(m)
            return
        res, err = _run(cfg)
        if err:
            st.markdown(f'<div class="q-card"><div class="q-ch">Could not run</div><div class="q-sub">{ui.e(err)}</div></div>', unsafe_allow_html=True)
            return
        st.session_state["bt_result"] = res
        st.session_state["bt_result_key"] = key
    res = st.session_state.get("bt_result")
    if res is None:
        st.markdown('<div class="q-card"><div class="q-ch">How it works</div><div class="q-sub">'
                    '1. Choose the stocks: a whole index, or only the ones you pick.<br>'
                    '2. Build your entry: add, edit or remove conditions until the list says exactly what you trade. Templates are only a starting point.<br>'
                    '3. Set the entry type, stop, exits, risk and costs.<br>'
                    '4. Run. The first run of the day downloads prices (a minute or two for 500 stocks); later runs take seconds.<br><br>'
                    'Nothing is tuned for you. "My exact rules" tests what is on the page. Walk-forward is optional and tries only the values you type.</div></div>',
                    unsafe_allow_html=True)
        return
    if st.session_state.get("bt_result_key") != key:
        st.markdown(f'<div class="q-note" style="color:{ui.YELLOW}">Settings changed since this result. Press Run backtest to update it.</div>', unsafe_allow_html=True)
    _results(res, on_open)
