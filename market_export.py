"""
Arka Trades — market_export.py (v1.4)

v1.4: NSE deliveries (python market_export.py delivery): delivered and traded quantity for every stock over the last 260 sessions,
      one small file per stock (dlv/SYMBOL.json), for the Research DLV, PB, SC and OV screens of the new app.

v1.3: names.json, NSE's company names by symbol (news tagging and filings in the new app).
v1.2: Money flow (python market_export.py flow): the rotation engine of money_flow.py with the classic page's default data options
      (Arka groups, against NIFTY 500, 5 years, ₹0.5 Cr a day), every model setting the page offers, and the NSDL FPI fortnights.

v1.1: meta.json also carries a one-line summary of each regime (state, score, date) for the Home page chip.

Runs the same Python engines the Streamlit app uses and saves their results as small JSON files in arka_data/market/,
so the new app shows exactly the same readings without running Python itself. Started by .github/workflows/market-data.yml
after the market closes.

    python market_export.py regime            # Regime for Nifty 500, Nifty 200 and Nifty 100
    python market_export.py names             # NSE company names by symbol (names.json)
    python market_export.py delivery          # NSE deliveries per stock (dlv/SYMBOL.json), only new days are downloaded
    python market_export.py flow              # Money flow: flow.json (page), flow_sectors.json (sector view), flow_fpi.json
    python market_export.py regime --out DIR

Only public market data is used (NSE index lists, Yahoo Finance prices). Nothing here touches a broker account.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import quant_data as qd
import regime_engine as rg

OUT = Path("arka_data/market")
REGIME_UNIVERSES = ["Nifty 500", "Nifty 200", "Nifty 100"]
SERIES = ["close", "ema50", "sma200", "score", "pct50", "pct200", "nh", "nl", "mcclellan"]


def _num(v, d=2):
    """JSON-safe number: NaN and infinity become null."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, d) if math.isfinite(f) else None


def _day(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")


def subset_panel(p: dict, members: list, name: str, note: str) -> dict:
    """A smaller universe cut out of a bigger panel. Same prices, same indicators, so the result equals a separate download."""
    keep = [j for j, s in enumerate(p["symbols"]) if s in set(members)]
    T, N = len(p["dates"]), len(p["symbols"])
    out = {}
    for k, v in p.items():
        if isinstance(v, np.ndarray) and v.ndim == 2 and v.shape == (T, N):
            out[k] = v[:, keep]
        else:
            out[k] = v
    out["symbols"] = [p["symbols"][j] for j in keep]
    out["industry"] = [p["industry"][j] for j in keep]
    out["name"], out["note"] = name, note
    out["missing"] = [s for s in (p.get("missing") or []) if s in set(members)]
    return out


def regime_json(p: dict) -> dict:
    """Everything the Regime page shows, computed by regime_engine exactly as in Streamlit."""
    df = rg.compute(p)
    if df.empty:
        raise RuntimeError("Not enough history to read the regime yet.")
    row = df.iloc[-1]
    eps = rg.episodes(df)
    state = row["state"]
    fwd = {}
    for hz in (10, 20, 60):
        ft = rg.forward_table(df, hz)
        fwd[str(hz)] = [{"state": r["state"], "days": int(r["days"]), **({k: _num(r[k]) for k in ("share", "nifty_avg", "nifty_up", "ew_avg", "ew_up", "worst_avg")}
                                                                       if r["days"] else {})} for _, r in ft.iterrows()]
    dd_days = df.index[-25:][df["dd_flag"].iloc[-25:].to_numpy()]
    ftds = df.index[df["ftd"].to_numpy()][-3:]
    keys = ["close", "ret", "score", "pct20", "pct50", "pct200", "nh", "nl", "dd_count", "vix", "vix_pct", "vix_chg", "adv", "dec", "mcclellan",
            "dd_from_hi", "ema21", "ema50", "sma200"] + [f"c_{k}" for k in rg.WEIGHTS]
    return {
        "universe": p.get("name", ""), "note": p.get("note", ""), "n": len(p["symbols"]), "missing": len(p.get("missing") or []),
        "built": p.get("built", ""), "asof": _day(df.index[-1]), "years": round(len(df) / 252, 1),
        "state": state, "exposure": int(rg.EXPOSURE[state] * 100), "guide": rg.GUIDE[state],
        "since": _day(eps[-1][1]), "days_in": int(eps[-1][3]),
        "row": {k: _num(row[k], 4) for k in keys if k in row.index},
        "weights": rg.WEIGHTS, "states": rg.STATES,
        "reasons": [[t, x] for t, x in rg.reasons(row, df)],
        "forward": fwd,
        "episodes": [[s, _day(a), _day(b), int(n), _num(mv)] for s, a, b, n, mv in eps[-10:][::-1]],
        "dd_days": [_day(x) for x in dd_days], "ftd": [_day(x) for x in ftds],
        "series": {"d": [_day(x) for x in df.index], "state": [rg.STATES.index(s) for s in df["state"]],
                   **{k: [_num(v, 2) for v in df[k].to_numpy()] for k in SERIES}},
    }


def run_regime(out: Path, panel=None) -> list:
    """Download Nifty 500 once, cut Nifty 200 and Nifty 100 out of it, write one file per universe."""
    out.mkdir(parents=True, exist_ok=True)
    big = panel or qd.load_panel("Nifty 500", None, 10, None, True)
    written, summary = [], {}
    for name in REGIME_UNIVERSES:
        if name == "Nifty 500":
            p = big
        else:
            members, note = qd.universe(name)
            p = subset_panel(big, list(members), name, note)
        try:
            data = regime_json(p)
        except Exception as exc:                                   # keep yesterday's file rather than writing a broken one
            print(f"{name}: skipped ({exc})", file=sys.stderr)
            continue
        f = out / f"regime_{name.replace(' ', '').lower()}.json"
        f.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
        written.append(f.name)
        summary[name] = {"state": data["state"], "score": data["row"]["score"], "asof": data["asof"], "exposure": data["exposure"]}
        print(f"{name}: {data['state']} · score {data['row']['score']} · {data['n']} stocks · {f.stat().st_size // 1024} KB")
    meta = out / "meta.json"
    m = json.loads(meta.read_text()) if meta.exists() else {}
    m["regime"] = {"built": datetime.now(qd.IST).isoformat(timespec="minutes"), "files": written, "now": {**(m.get("regime") or {}).get("now", {}), **summary}}
    meta.write_text(json.dumps(m, indent=1))
    return written


# ═════════════════════════ Money flow ═════════════════════════
FLOW_N, FLOW_EVERY, FLOW_COST = [2, 3, 4, 5, 6, 8, 10], [10, 21, 42, 63], 0.30
# The data choices of the classic page (money_flow.render_money_flow), each built as its own set of files:
FLOW_KINDS = {"Arka groups": "arka", "NSE indices": "nse"}
FLOW_BENCHES = {"NIFTY 500": "n500", "NIFTY 50": "n50", "Equal-weight market": "ew"}
FLOW_PERIODS = ["5y", "3y", "2y"]
FLOW_MIN_TURN = [0.5, 0.25, 1.0, 2.0, 5.0]
FLOW_COSTS = [0.10, 0.20, 0.30, 0.50, 0.75, 1.00]             # round-trip cost choices (the page's default is 0.30)
FLOW_DEFAULT = ("Arka groups", "NIFTY 500", "5y", 0.5)


def flow_key(kind, bench, period, min_turn) -> str:
    """'arka-n500-5y-0.5': the name the page asks for (flow@<key>.json)."""
    return f"{FLOW_KINDS[kind]}-{FLOW_BENCHES[bench]}-{period}-{min_turn:g}"


def _ser(x, d=2):
    return [_num(v, d) for v in np.asarray(x, dtype=float)]


def _thin(s: pd.Series, step=5):
    """Every `step`-th point of a curve, always keeping the last one."""
    keep = list(range(0, len(s), step))
    if keep and keep[-1] != len(s) - 1:
        keep.append(len(s) - 1)
    return s.iloc[keep]


def _metrics_json(m):
    return {k: _num(v, 3) for k, v in (m or {}).items()}


def _pf_json(pf, base=None, stab=None):
    if not pf.get("ok"):
        return {"ok": False, "why": pf.get("why", "")}
    curves = pd.DataFrame({"model": (1 + pf["returns"]).cumprod() * 100, "bench": (1 + pf["bench_returns"]).cumprod() * 100,
                           "ew": (1 + pf["ew_returns"]).cumprod() * 100})
    if base is not None and base.get("ok"):
        curves["base"] = (1 + base["returns"].reindex(curves.index).fillna(0)).cumprod() * 100
    c = curves.iloc[[*range(0, len(curves), 5)] + ([len(curves) - 1] if (len(curves) - 1) % 5 else [])]
    last = next((x for x in reversed(pf["log"]) if not x.get("pending")), None)
    return {"ok": True, "held": pf["held"], "metrics": _metrics_json(pf["metrics"]), "bench_metrics": _metrics_json(pf["bench_metrics"]),
            "ew_metrics": _metrics_json(pf["ew_metrics"]), "turnover": _num(pf["turnover_per_year"], 2), "reviews": pf["reviews"],
            "sessions": len(pf["returns"]), "missing_bar_days": int(pf.get("missing_bar_days") or 0), "last_review": _day(last["date"]) if last else None,
            "base_metrics": _metrics_json(base["metrics"]) if base is not None and base.get("ok") else None,
            "curve": {"d": [_day(x) for x in c.index], **{k: _ser(c[k], 2) for k in c.columns}},
            "log": [{"date": _day(x["date"]), "pending": bool(x.get("pending")), "regime": x.get("regime"), "slots": x["slots"], "in": x["in"], "out": x["out"],
                     "holdings": x["holdings"]} for x in pf["log"][-12:][::-1]],
            "stability": stab}


def flow_models(H, F, reg, cost) -> dict:
    """Every model setting the page offers (sectors to hold x review every x regime filter), at one round-trip cost."""
    from arka_flow import rotation as rot
    models = {}
    idx = F["score"].dropna(how="all").index
    cut = idx[int(len(idx) * 0.6)] if len(idx) > 120 else None
    for every in FLOW_EVERY:
        for n in FLOW_N:
            base = rot.run_portfolio(H, F, reg, n=n, every=every, cost_pct=cost, use_regime=False, score_key="momentum_only")
            for use_reg in (True, False):
                pf = rot.run_portfolio(H, F, reg, n=n, every=every, cost_pct=cost, use_regime=use_reg)
                stab = None
                if cut is not None:
                    stab = []
                    for kw in ({"end": cut}, {"start": cut}):
                        x = rot.run_portfolio(H, F, reg, n=n, every=every, cost_pct=cost, use_regime=use_reg, **kw)
                        stab.append(_metrics_json(x.get("metrics")))
                snap = rot.snapshot(H, F, reg, pf, n)
                j = _pf_json(pf, base, stab)
                j["target"] = snap.loc[snap["target"], "sector"].tolist()
                j["actions"] = {a: snap.loc[snap["action"] == a, "sector"].tolist() for a in ("Hold", "Rotate in", "Rotate out", "Watch: improving")}
                models[f"{n}|{every}|{int(use_reg)}"] = j
    return models


def run_flow(out: Path, E=None, fpi_n: int = 48, choice=FLOW_DEFAULT, tag: str = "", with_fpi: bool = True) -> list:
    import money_flow as mf
    from arka_flow import MODEL_VERSION, fpi, report, rotation as rot, state as rstate
    from arka_flow.config import MIN_SECTOR_COVERAGE
    from arka_flow.data import validation as val
    out.mkdir(parents=True, exist_ok=True)
    kind, bench, period, min_turn = choice
    if E is None:
        meta, source = mf._universe_cached()
        E = mf._engine(tuple(meta["symbol"]), period, kind, bench, float(min_turn))
    else:
        source = E.get("_source", "test")
    H, F, reg, q, tbl = E["H"], E["F"], E["reg"], E["q"], E["table"]
    n_sec = len(H["sectors"])
    d_n = 5 if n_sec >= 30 else 4 if n_sec >= 15 else 3
    rg_row = reg.iloc[-1]
    rg = rg_row["regime"]
    # one snapshot for the model-independent columns (rank, score, components, rotation map, trend, returns vs benchmark)
    base_snap = rot.snapshot(H, F, reg, None, d_n)
    cols = ["sector", "score", "rank", "rank_20_ago", "rank_change_20", "rank_pct", *rot.WEIGHTS, "n_parts", "rs_ratio", "rs_mom", "quadrant", "quadrant_10_ago",
            "trend_ok", "dist_trend", "rel_21", "rel_63", "rel_126", "pct50", "d50", "consistency_raw", "vol"]
    sectors = []
    for r in base_snap.to_dict("records"):
        row = {k: (r[k] if isinstance(r[k], str) else _num(r[k], 4)) for k in cols}
        r2 = dict(r, action="")
        row["why"] = rot.explain(pd.Series(r2), n_sec, H["bench_name"], rg)
        row["contrib"] = {k: _num(v, 4) for k, v in rot.contributions(pd.Series(r)).items()}
        sectors.append(row)
    # every model setting the page offers (at the page's default cost; other costs: flow_models@<key>@<cost>.json)
    models, ic = flow_models(H, F, reg, FLOW_COST), {}
    for every in FLOW_EVERY:
        t = rot.score_test(H, F, h=every)
        idx = F["score"].dropna(how="all").index
        cut = idx[int(len(idx) * 0.6)] if len(idx) > 120 else None
        halves = []
        if cut is not None:
            for kw in ({"end": cut}, {"start": cut}):
                tt = rot.score_test(H, F, h=every, keys=["score"], **kw)
                halves.append(_num(tt["ic"]["mean_ic"].iloc[0], 4) if len(tt["ic"]) else None)
        ic[str(every)] = {"ic": [{k: (x[k] if k == "factor" else _num(x[k], 4)) for k in ("factor", "dates", "mean_ic", "t_stat", "pct_positive")} for x in t["ic"].to_dict("records")],
                          "quintiles": [{k: (x[k] if k == "quintile" else _num(x[k], 4)) for k in ("quintile", "mean_excess", "beat_pct", "dates")} for x in t["quintiles"].to_dict("records")],
                          "dates": t["dates"], "spread_mean": _num(t["spread_mean"], 4), "spread_beat": _num(t["spread_beat"], 2),
                          "cut": _day(cut) if cut is not None else None, "half_ic": halves}
    tail = 20
    rrg = {"d": [_day(x) for x in F["rs_ratio"].index[-tail:]], "s": {s: [_ser(F["rs_ratio"][s].iloc[-tail:], 3), _ser(F["rs_mom"][s].iloc[-tail:], 3)] for s in H["levels"].columns}}
    c = E["close"]
    mk = {n: ((c > c.ewm(span=n, adjust=False, min_periods=n).mean()) & c.notna()).sum(axis=1) / c.notna().sum(axis=1).replace(0, np.nan) * 100 for n in (20, 50, 200)}
    mk = pd.DataFrame({f"p{n}": v for n, v in mk.items()}).tail(250)
    tcols = ["sector", "state", "confidence", "n_bar", "pct20", "d20_stocks_10", "pct50", "d50_stocks_20", "pct200", "adv", "dec", "new_high20", "new_low20", "breakout20",
             "coverage_status", "traded_value_cr", "rel_activity", "turnover_accel", "up_share5", "down_share5", "abs_5", "rel_20", "rel_60", "pctile_20", "members", "with_prices", "coverage"]
    table = [{k: (r[k] if isinstance(r.get(k), str) else _num(r.get(k), 4)) for k in tcols if k in r} for r in tbl.to_dict("records")]
    stale_by = val.stale_sessions(H["asof"], q["session"]["last_complete_session"])
    page = {"v": 1, "built": datetime.now(qd.IST).isoformat(timespec="minutes"), "asof": str(H["asof"])[:10], "bench": H["bench_name"], "kind": kind, "period": period,
            "min_turn": min_turn, "cost": FLOW_COST, "n_sectors": n_sec, "default": {"n": d_n, "every": 21, "regime": True}, "options": {"n": FLOW_N, "every": FLOW_EVERY},
            "regime": {"label": rg, "bench_vs_sma": _num((rg_row["bench"] / rg_row["sma200"] - 1) * 100 if pd.notna(rg_row["sma200"]) else np.nan),
                       "above": None if pd.isna(rg_row["above_sma200"]) else int(rg_row["above_sma200"]), "breadth200": _num(rg_row["breadth200"]),
                       "vol20": _num(rg_row["vol20"]), "vol_pct": _num(rg_row["vol_pct"]), "drawdown": _num(rg_row["drawdown"]), "slots_share": rot.REGIME_SLOTS},
            "weights": rot.WEIGHTS, "labels": rot.FACTOR_LABEL, "sectors": sectors, "models": models, "ic": ic, "rrg": rrg, "table": table,
            "market": {"d": [_day(x) for x in mk.index], **{k: _ser(mk[k], 1) for k in mk.columns}}, "value_basis": H.get("value_basis", ""),
            "quality": {"source": q["source"], "adjusted": q["adjusted"], "period": q["period"], "loaded_at": str(q["loaded_at"]), "universe": source,
                        "requested": q["requested"], "loaded": q["loaded"], "used": q["used"], "last_complete": q["session"]["last_complete_session"],
                        "holiday_calendar": q["session"]["holiday_calendar"], "illiquid": len(q["illiquid"]), "stale": len(q["stale"]),
                        "small_groups": q["small_groups"], "index_missing": q["index_missing"], "dropped_sessions": q["dropped_sessions"], "stale_by": stale_by,
                        "failures": [[k, str(v)] for k, v in list(q["failures"].items())[:200]], "n_failures": len(q["failures"]),
                        "jumps": [{k: (v if isinstance(v, str) else _num(v, 2) if isinstance(v, (int, float, np.floating, np.integer)) else str(v)) for k, v in j.items()} for j in q["jumps"][:200]],
                        "min_coverage": MIN_SECTOR_COVERAGE, "rot_version": rot.ROT_VERSION, "model_version": MODEL_VERSION},
            "states_doc": rstate.__doc__ or "", "rules_doc": rot.__doc__ or ""}
    # sector view: one year of history and the member table for every sector (loaded only when that view opens)
    L, b, T = H["levels"], H["bench"], F["trend_sma"]
    d252 = L.index[-252:]
    det = {}
    for s in L.columns:
        lv = pd.DataFrame({"s": L[s], "b": b, "t": T[s]}).loc[d252]
        s0, b0 = lv["s"].dropna().iloc[0] if lv["s"].notna().any() else np.nan, lv["b"].iloc[0]
        mem = E["members"].get(s, [])
        stk = report.stock_table(E["close"], E["high"], E["volume"], mem, b, L[s]) if mem else pd.DataFrame()
        keep = ["symbol", "price", "ret_20", "ret_60", "rel_bench_60", "rel_sector_60", "above_50", "above_200", "from_52w_high", "rel_volume", "avg_value_cr", "setup"]
        det[s] = {"lv": _ser(lv["s"] / s0 * 100), "bv": _ser(lv["b"] / b0 * 100), "tv": _ser(lv["t"] / s0 * 100), "rank": _ser(F["rank_pct"][s].reindex(d252), 1),
                  "rr": _ser(F["rs_ratio"][s].iloc[-180:], 3), "rm": _ser(F["rs_mom"][s].iloc[-180:], 3),
                  "p20": _ser(H["pct20"][s].iloc[-250:], 1) if s in H["pct20"] else [], "p50": _ser(H["pct50"][s].iloc[-250:], 1) if s in H["pct50"] else [],
                  "p200": _ser(H["pct200"][s].iloc[-250:], 1) if s in H["pct200"] else [], "n_members": len(mem),
                  "stocks": [{k: (r.get(k) if isinstance(r.get(k), (str, bool)) or r.get(k) is None else _num(r.get(k), 2)) for k in keep} for r in stk.to_dict("records")]}
    sect = {"d252": [_day(x) for x in d252], "d180": [_day(x) for x in F["rs_ratio"].index[-180:]], "d250": [_day(x) for x in H["pct50"].index[-250:]], "s": det}
    page["choice"] = {"kind": kind, "bench": bench, "period": period, "min_turn": min_turn, "key": flow_key(kind, bench, period, min_turn)}
    (out / f"flow{tag}.json").write_text(json.dumps(page, separators=(",", ":"), allow_nan=False, default=str))
    (out / f"flow_sectors{tag}.json").write_text(json.dumps(sect, separators=(",", ":"), allow_nan=False, default=str))
    written = [f"flow{tag}.json", f"flow_sectors{tag}.json"]
    if tag or not with_fpi:
        return written
    # NSDL fortnightly FPI flows by sector (context only, as on the classic page)
    try:
        from concurrent.futures import ThreadPoolExecutor
        dates = [str(d)[:10] for d in fpi.nsdl_report_dates(fpi_n)]
        fetch = getattr(mf._nsdl_fetch, "__wrapped__", mf._nsdl_fetch)
        with ThreadPoolExecutor(max_workers=4) as pool:
            got = list(pool.map(fetch, dates))
        net, auc = fpi.nsdl_panels(got)
        if not net.empty:
            fj = {"dates": [_day(x) for x in net.index], "tried": len(dates), "ok": sum(g is not None for g in got),
                  "net": {s: _ser(net[s], 1) for s in net.columns}, "auc": {s: _ser(auc[s], 1) for s in auc.columns}}
            (out / "flow_fpi.json").write_text(json.dumps(fj, separators=(",", ":"), allow_nan=False))
            written.append("flow_fpi.json")
    except Exception as exc:
        print(f"FPI: skipped ({exc})", file=sys.stderr)
    for f in written:
        print(f"{f}: {(out / f).stat().st_size // 1024} KB")
    meta = out / "meta.json"
    m = json.loads(meta.read_text()) if meta.exists() else {}
    m["flow"] = {"built": page["built"], "asof": page["asof"], "files": written,
                 "top": [{"sector": x, "action": a} for a in ("Rotate in", "Hold") for x in models[f"{d_n}|21|1"]["actions"].get(a, [])][:8],
                 "regime": rg}
    meta.write_text(json.dumps(m, indent=1))
    return written


def run_names(out: Path) -> list:
    """{SYMBOL: company name} from NSE's equity list (EQUITY_L.csv), the list the classic app's search and news use."""
    import io
    import requests
    out.mkdir(parents=True, exist_ok=True)
    for u in ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "https://archives.nseindia.com/content/equities/EQUITY_L.csv"):
        try:
            r = requests.get(u, headers={"User-Agent": qd.UA, "Accept": "text/csv,*/*"}, timeout=20)
            if r.status_code == 200 and "SYMBOL" in r.text[:300].upper():
                df = pd.read_csv(io.StringIO(r.text), dtype=str)
                df.columns = [str(c).strip().upper() for c in df.columns]
                name_col = next(c for c in df.columns if "NAME" in c)
                names = {str(a).strip().upper(): str(b).strip() for a, b in zip(df["SYMBOL"], df[name_col]) if str(a).strip() and str(b).strip()}
                if len(names) > 500:
                    (out / "names.json").write_text(json.dumps(names, separators=(",", ":")))
                    print(f"names.json: {len(names)} companies")
                    return ["names.json"]
        except Exception as exc:
            print(f"names: {u} failed ({exc})", file=sys.stderr)
    return []


def run_delivery(out: Path, sessions: int = 260, max_new: int = 330) -> list:
    """Delivered vs traded quantity per stock from NSE's daily security-wise bhavcopy (research_data.parse_bhavcopy, the file the
    classic DLV screen reads). A rolling store (dlv/_store.csv.gz) keeps every day already fetched, so after the first run only
    the new day is downloaded. Writes dlv/SYMBOL.json = {"d": dates, "c": close, "p": previous close, "v": traded qty, "q": delivered qty}."""
    import time
    from datetime import timedelta
    import requests
    import research_data as rd
    folder = out / "dlv"
    folder.mkdir(parents=True, exist_ok=True)
    store_f, miss_f = folder / "_store.csv.gz", folder / "_missing.json"
    cols = ["symbol", "date", "close", "prev_close", "volume", "deliv_qty"]
    store = pd.read_csv(store_f, dtype={"symbol": str, "date": str}) if store_f.exists() else pd.DataFrame(columns=cols)
    missing = set(json.loads(miss_f.read_text())) if miss_f.exists() else set()
    have = set(store["date"].astype(str))
    now = datetime.now(qd.IST)
    today = now.date()
    days = [today - timedelta(days=i) for i in range(0, int(sessions * 1.5) + 10)]
    floor = min(have) if len(have) >= sessions else ""             # once the store is full, older days are not fetched again
    days = [d for d in days if d.weekday() < 5 and d.isoformat() > floor and d.isoformat() not in have and d.isoformat() not in missing and not (d == today and now.hour < 19)]
    got, errors, frames = 0, 0, []
    for d in days[:max_new]:                                        # newest first
        res = None
        for u in rd.BHAV_URLS:
            try:
                r = requests.get(u.format(d=f"{d:%d%m%Y}"), headers={"User-Agent": rd.UA, "Accept": "text/csv,*/*"}, timeout=20)
            except Exception:
                continue
            if r.status_code == 404:
                res = "missing"
                break
            if r.status_code == 200 and "SYMBOL" in r.text[:200].upper():
                res = rd.parse_bhavcopy(r.text)
                break
        if isinstance(res, pd.DataFrame):
            res = res.assign(o=res["series"].map(lambda v: 0 if v == "EQ" else 1)).sort_values("o").drop_duplicates("symbol")
            frames.append(res[cols])
            got, errors = got + 1, 0
        elif res == "missing":
            if (today - d).days > 4:                                # a holiday (NSE sometimes publishes a day late, so recent gaps are retried)
                missing.add(d.isoformat())
        else:
            errors += 1
            if errors >= 6:
                print("delivery: NSE archive not answering, stopping for today", file=sys.stderr)
                break
        time.sleep(0.25)
    if frames:
        store = pd.concat([store] + frames, ignore_index=True)
    if store.empty:
        print("delivery: nothing fetched", file=sys.stderr)
        return []
    store["date"] = store["date"].astype(str)
    store = store.drop_duplicates(["symbol", "date"], keep="last")
    keep = sorted(store["date"].unique())[-sessions:]
    store = store[store["date"].isin(keep)].sort_values(["symbol", "date"])
    store.to_csv(store_f, index=False, compression="gzip")
    miss_f.write_text(json.dumps(sorted(m for m in missing if m >= keep[0])))
    n = lambda v: None if pd.isna(v) else (int(v) if float(v).is_integer() else round(float(v), 2))
    written = set()
    for sym, g in store.groupby("symbol"):
        if not sym or not all(ch.isalnum() or ch in "&-_." for ch in sym):
            continue
        (folder / f"{sym}.json").write_text(json.dumps({"d": g["date"].tolist(), "c": [n(v) for v in g["close"]], "p": [n(v) for v in g["prev_close"]],
                                                       "v": [n(v) for v in g["volume"]], "q": [n(v) for v in g["deliv_qty"]]}, separators=(",", ":")))
        written.add(f"{sym}.json")
    for old in folder.glob("*.json"):
        if not old.name.startswith("_") and old.name not in written:
            old.unlink()
    print(f"delivery: {got} new day(s), {len(keep)} sessions kept, {len(written)} stocks")
    return ["dlv/"]


def run_flow_all(out: Path, budget_s: float = 45 * 60) -> list:
    """The default Money flow files (as before, with FPI and meta), then every other data choice and round-trip cost the classic
    page offers, each as its own files, and flow_index.json listing what was built. A choice that fails is left out, never faked."""
    import time
    import money_flow as mf
    t0 = time.time()
    written = run_flow(out)
    if not written:
        return written
    meta, source = mf._universe_cached()
    syms = tuple(meta["symbol"])
    kind0, bench0, period0, turn0 = FLOW_DEFAULT
    choices = [FLOW_DEFAULT]
    for period in FLOW_PERIODS:                              # one price download per history length, in this order
        if period == period0:
            choices += [(k, b, period0, turn0) for k in FLOW_KINDS for b in FLOW_BENCHES if (k, b) != (kind0, bench0)]
            choices += [(kind0, bench0, period0, t) for t in FLOW_MIN_TURN if t != turn0]
        else:
            choices.append((kind0, bench0, period, turn0))
    built, failed = [], []
    for ch in choices:
        key = flow_key(*ch)
        if time.time() - t0 > budget_s:
            failed.append([key, "time budget used up"])
            continue
        try:
            E = mf._engine(syms, ch[2], ch[0], ch[1], float(ch[3]))
            E.setdefault("_source", source)
            if ch != FLOW_DEFAULT:
                written += run_flow(out, E=E, choice=ch, tag="@" + key, with_fpi=False)
            costs = []
            for c in FLOW_COSTS:
                if abs(c - FLOW_COST) < 1e-9:
                    costs.append(c)
                    continue
                f = out / f"flow_models@{key}@{c:g}.json"
                f.write_text(json.dumps(flow_models(E["H"], E["F"], E["reg"], c), separators=(",", ":"), allow_nan=False, default=str))
                written.append(f.name)
                costs.append(c)
            built.append({"key": key, "kind": ch[0], "bench": ch[1], "period": ch[2], "min_turn": ch[3], "costs": costs})
            print(f"flow choice {key}: ok ({time.time() - t0:.0f} s)")
        except Exception as exc:
            failed.append([key, f"{type(exc).__name__}: {exc}"[:200]])
            print(f"flow choice {key}: skipped ({exc})", file=sys.stderr)
    idx = {"built": datetime.now(qd.IST).isoformat(timespec="minutes"), "default": flow_key(*FLOW_DEFAULT), "default_cost": FLOW_COST,
           "kinds": list(FLOW_KINDS), "benches": list(FLOW_BENCHES), "periods": FLOW_PERIODS, "min_turn": FLOW_MIN_TURN, "costs": FLOW_COSTS,
           "choices": built, "failed": failed}
    (out / "flow_index.json").write_text(json.dumps(idx, indent=1))
    written.append("flow_index.json")
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["regime", "flow", "names", "delivery"])
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    got = {"regime": run_regime, "flow": run_flow_all, "names": run_names, "delivery": run_delivery}[a.what](Path(a.out))
    sys.exit(0 if got else 1)
