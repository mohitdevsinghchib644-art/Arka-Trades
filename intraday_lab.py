"""Arka Trades · intraday lab.

Tests a short, fixed list of intraday ideas on the five-minute candles that intraday_data.py collects, and writes lab.json.
Nothing here is a signal: an idea is only reported as "holds" when it makes money after costs in the full history AND in
both halves of it (the first 60% of days is where an idea is allowed to look good, the last 40% has to confirm it).

The list was fixed before any five-minute data was seen, from published research and from the daily-data finding:
  1. Intraday book      the model's ten best short ranks, sold for the day. Daily data says they fall from the open to the
                        close. This checks it on real five-minute prices and shows how much is left when the sale is late.
  2. Opening range      "stocks in play" (Zarattini, Barbon, Aziz 2024): the ten stocks with the highest opening volume
                        against their own usual opening volume, traded in the direction of the first candle on a break of it.
  3. Half-hour momentum (Gao, Han, Li, Zhou 2018): the move from yesterday's close to 9:45 predicts the last half hour.
  4. Late rebound       the day's biggest losers by 3:00 pm bought for the last half hour.

Three more ideas were found later, by looking at this same data (27 Nov 2026): only the five best ranks, leaving out picks
that gap down at the open, and a late sale of picks that are already falling. Data cannot confirm an idea that was found in
it, so these are marked "unclear" whatever their numbers and are judged only on the sessions after that day (see later()).

    python intraday_lab.py --data DIR --picks arka_data/model/day_picks.csv --out DIR/lab.json
"""
from __future__ import annotations
import argparse, glob, json, math, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd

BARS, LAST, EXIT_BAR, LATE = 75, 73, 71, 69         # 75 five-minute bars a day; 15:20-15:25 is the last one used; 15:10-15:15 closes day trades; 15:00 starts the last half hour
COST_DAY, COST_ORB, COST_IX = 0.0016, 0.0010, 0.0004  # one round trip: cash day trade as in the daily test · day trade with small size · index futures
INDEX = ("NIFTY", "BANKNIFTY")
FOUND, NEED = "2026-11-27", 120                       # ideas found by looking at the data up to this day are judged only on sessions after it, once there are this many
G_DAY, G_RISK, G_IDEA = "Intraday book: ten best short ranks, sold for the day", "Intraday book: risk rules (sold at 9:15)", "Intraday book: ideas found in this data, followed from here on (not rules yet)"


def load(folder: str):
    files = sorted(glob.glob(str(Path(folder) / "m5" / "*.csv.gz")))
    if not files:
        raise SystemExit("No five-minute files found in " + folder + "/m5: run the collector first.")
    df = pd.concat((pd.read_csv(f, dtype={"sym": "category", "t": "string", "o": "float64", "h": "float64", "l": "float64", "c": "float64", "v": "float64"}) for f in files), ignore_index=True)
    df["day"] = df.t.str.slice(0, 10); mins = df.t.str.slice(11, 13).astype(int) * 60 + df.t.str.slice(14, 16).astype(int); df["b"] = (mins - 555) // 5
    df = df[(df.b >= 0) & (df.b < BARS) & (df.o > 0) & (df.c > 0)]
    days = np.array(sorted(df.day.unique())); syms = np.array(sorted(df.sym.astype(str).unique())); di = pd.Series(np.arange(len(days)), index=days); si = pd.Series(np.arange(len(syms)), index=syms)
    d, s, b = di.reindex(df.day.to_numpy()).to_numpy().astype(int), si.reindex(df.sym.astype(str).to_numpy()).to_numpy().astype(int), df.b.to_numpy().astype(int)
    A = {}
    for k in "ohlcv":
        a = np.full((len(days), len(syms), BARS), np.nan, dtype="float32"); a[d, s, b] = df[k].to_numpy(); A[k] = a
    return days, syms, A


def ffill(a):                                          # along the bars: the last price seen so far
    fin = np.isfinite(a); idx = np.where(fin, np.arange(a.shape[-1]), 0); np.maximum.accumulate(idx, axis=-1, out=idx); out = np.take_along_axis(a, idx, axis=-1); out[~np.logical_or.accumulate(fin, axis=-1)] = np.nan
    return out


def stats(x, trades=None):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = len(x)
    if n < 20:
        return {"days": n, "verdict": "too little data", "why": f"only {n} days"}
    def part(v):
        sd = v.std(ddof=1) if len(v) > 1 else 0.0; return {"days": int(len(v)), "mean": round(float(v.mean()) * 100, 4), "t": round(float(v.mean() / (sd / math.sqrt(len(v)))), 2) if sd > 0 else 0.0, "won": round(float((v > 0).mean()) * 100, 0)}
    cut = int(n * 0.6); full, disc, val = part(x), part(x[:cut]), part(x[cut:]); eq = np.cumprod(1 + x); dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    if full["mean"] > 0 and full["t"] >= 2 and disc["mean"] > 0 and val["mean"] > 0 and val["t"] >= 1:
        verdict, why = "holds", "positive after costs in the whole history and in both parts of it"
    elif full["mean"] <= 0 or val["mean"] <= 0:
        verdict, why = "fails", "loses after costs" if full["mean"] <= 0 else "worked early, not in the later 40% of days"
    else:
        verdict, why = "unclear", "positive but too weak to tell from luck"
    return {**full, "year": round(float(x.mean()) * 250 * 100, 1), "maxdd": round(dd * 100, 1), "first": disc, "later": val, "trades": int(trades) if trades is not None else None, "verdict": verdict, "why": why}


def later(t, series, days):
    """An idea that was found in this same data cannot be confirmed by it. Its numbers for the whole history are shown, the verdict comes from later sessions only."""
    new = [x for x, d in zip(series, days) if str(d) > FOUND and x is not None and np.isfinite(x)]; when = pd.Timestamp(FOUND).strftime("%d %b %Y").lstrip("0")
    t["since"] = {"from": FOUND, "days": len(new), "need": NEED, "mean": round(float(np.mean(new)) * 100, 4) if new else None}
    if len(new) >= NEED:
        s2 = stats(new); t["verdict"], t["why"] = s2["verdict"], f"on the {len(new)} sessions after {when}: {s2['why']}"
    elif t.get("verdict") in ("holds", "unclear"):
        t["why"] = ("passes on the whole history, but " if t["verdict"] == "holds" else "") + f"it was found by looking at this same data on {when}: only later sessions count, {len(new)} of {NEED} so far"; t["verdict"] = "unclear"
    return t


def run(days, syms, A, picks):
    o, h, l, c, v = (A[k] for k in "ohlcv"); nd, ns, _ = c.shape; cf = ffill(c); stock = np.array([s not in INDEX for s in syms])
    O = o[:, :, 0].astype("float64"); H = np.nanmax(h, axis=2); L = np.nanmin(l, axis=2); C = cf[:, :, LAST].astype("float64")
    prevC = np.vstack([np.full((1, ns), np.nan), C[:-1]]); tr = np.nanmax(np.stack([H - L, np.abs(H - prevC), np.abs(L - prevC)]), axis=0)
    atr = pd.DataFrame(tr).rolling(14, min_periods=5).mean().shift(1).to_numpy()            # known before the open
    tests, extra = [], {}

    # 1 · the day book on five-minute prices
    if picks is not None and len(picks):
        sidx = {s: i for i, s in enumerate(syms)}; sig = sorted(picks.date.unique()); by = {d: g for d, g in picks.groupby("date")}; entries = {"9:15 (the open)": 0, "9:20": 1, "9:30": 3, "10:00": 9}; daily = {k: [] for k in entries}; paths, n_tr, used = [], 0, 0; risk = {"stop": [], "basket": []}; n_stop = n_bask = 0; late, n_late = [], 0; top5, nogap, n5, n_ng, n_skip = [], [], 0, 0, 0
        for di in range(nd):
            prior = [x for x in sig if x < days[di]]
            if not prior or (di > 0 and prior[-1] != days[di - 1]) or (di == 0 and (pd.Timestamp(str(days[di])) - pd.Timestamp(str(prior[-1]))).days > 5):
                for k in entries: daily[k].append(np.nan)
                for k in risk: risk[k].append(np.nan)
                late.append(np.nan); top5.append(np.nan); nogap.append(np.nan); continue
            g = by[prior[-1]]; g = g[(g["rank"] <= 10) & g.sym.isin(sidx)]; ix = [sidx[s] for s in g.sym]; rks = g["rank"].to_numpy(); used += 1
            for k, b in entries.items():
                e = o[di, ix, b].astype("float64"); x = cf[di, ix, LAST].astype("float64"); r = -(x / e - 1) - COST_DAY; r = r[np.isfinite(r)]; daily[k].append(r.sum() / 10 if len(r) else np.nan); n_tr += len(r) if b == 0 else 0
            e0 = o[di, ix, 0].astype("float64"); p = cf[di, ix, :].astype("float64") / e0[:, None] - 1; good = np.isfinite(e0)
            # the open was missed: sold at 9:20, and only the picks that fell 0.5% or more in their first five minutes (found in the data on 27 Nov 2026 and followed since then)
            e1 = o[di, ix, 1].astype("float64"); fall = c[di, ix, 0].astype("float64") / e0 - 1; m1 = np.isfinite(e1) & np.isfinite(fall) & (fall <= -0.005)
            if m1.any():
                hi1 = np.fmax.reduce(np.nan_to_num(h[di, ix, 1:LAST + 1].astype("float64"), nan=-1.0), axis=1); lim1 = atr[di, ix] / e1; lim1 = np.where(np.isfinite(lim1) & (lim1 > 0), lim1, 9.0)
                r1 = np.where(hi1 >= e1 * (1 + lim1), -lim1, -(cf[di, ix, LAST].astype("float64") / e1 - 1)) - COST_DAY; late.append(float(np.nansum(r1[m1])) / 10); n_late += int(m1.sum())
            else: late.append(0.0)
            # the risk rules, on the trade sold at 9:15: each stock is bought back if it trades 1 ATR above its sale price; with the basket rule everything is bought back once the ten together are down 1%
            if good.any():
                pg = p[good]; hi = np.fmax.accumulate(np.nan_to_num(h[di, ix, :].astype("float64")[good] / e0[good][:, None] - 1, nan=-9.0), axis=1); lim = (atr[di, ix][good] / e0[good]); lim = np.where(np.isfinite(lim) & (lim > 0), lim, 9.0)
                hit = hi >= lim[:, None]; first = np.where(hit.any(axis=1), hit.argmax(axis=1), BARS); pnl = np.where(np.arange(BARS)[None, :] >= first[:, None], -lim[:, None], -pg)            # each stock's result so far, bar by bar
                risk["stop"].append((pnl[:, LAST] - COST_DAY).sum() / 10); n_stop += int((first <= LAST).sum())
                bk = np.nansum(pnl[:, :LAST + 1], axis=0) / 10; out_bar = int(np.argmax(bk <= -0.01)) if (bk <= -0.01).any() else LAST; risk["basket"].append((pnl[:, out_bar] - COST_DAY).sum() / 10); n_bask += int(out_bar < LAST)
                # two ideas found in this data, on the same trade (sold at 9:15, stop 1 ATR): only the five best ranks at twice the size; and leaving out the picks that open more than 0.3% below yesterday's close
                res = pnl[:, LAST] - COST_DAY; t5 = rks[good] <= 5; top5.append(float(res[t5].sum()) / 5 if t5.any() else 0.0); n5 += int(t5.sum())
                gap = e0[good] / prevC[di, ix][good] - 1; keep = ~(gap < -0.003); nogap.append(float(res[keep].sum()) / 10); n_ng += int(keep.sum()); n_skip += int((~keep).sum())
            else:
                for k in risk: risk[k].append(np.nan)
                top5.append(np.nan); nogap.append(np.nan)
            p = p[good]
            if len(p): paths.append(np.nanmean(p, axis=0))
        for k in entries:
            tests.append({"id": "day_" + k.split(" ")[0].replace(":", ""), "group": G_DAY, "name": f"Sold at {k}, bought back at 3:25 pm", "cost": COST_DAY * 100, **stats(daily[k], n_tr if k.startswith("9:15") else None)})
        tests.append({"id": "day_stop", "group": G_RISK, "name": "Each stock bought back if it trades 1 ATR above the sale price", "cost": COST_DAY * 100, **stats(risk["stop"], n_tr), "note": f"{n_stop} stops hit"})
        tests.append({"id": "day_basket", "group": G_RISK, "name": "The same, and everything bought back once the ten together are down 1%", "cost": COST_DAY * 100, **stats(risk["basket"], n_tr), "note": f"the basket rule ended {n_bask} days early"})
        tests.append(later({"id": "day_top5", "group": G_IDEA, "name": "Only the five best ranks, twice the size each, stop 1 ATR", "cost": COST_DAY * 100, **stats(top5, n5), "note": "half as many stocks, so one bad stock hurts twice as much"}, top5, days))
        tests.append(later({"id": "day_nogap", "group": G_IDEA, "name": "Leaving out the picks that open more than 0.3% below yesterday's close, stop 1 ATR", "cost": COST_DAY * 100, **stats(nogap, n_ng), "note": f"{n_skip:,} of {n_ng + n_skip:,} trades left out, their slots stay empty"}, nogap, days))
        tests.append(later({"id": "day_920_fell", "group": G_IDEA, "name": "The open was missed: sold at 9:20, only the picks that fell 0.5% or more in their first five minutes, stop 1 ATR", "cost": COST_DAY * 100, **stats(late, n_late), "note": "most of the ten slots stay empty"}, late, days))
        if paths:
            pm = np.nanmean(np.array(paths), axis=0) * 100; extra["day_path"] = {"t": [f"{(555 + 5 * (i + 1)) // 60}:{(555 + 5 * (i + 1)) % 60:02d}" for i in range(LAST + 1)], "pct": [round(float(x), 3) for x in pm[:LAST + 1]], "days": used,
                                                                                 "note": "Average move of the ten picks from the 9:15 price, before costs. A falling line is good for a short. If most of the fall is in the first minutes, a late sale misses it."}
        allp = cf[:, stock, :].astype("float64") / O[:, stock, None] - 1; extra["all_path"] = [round(float(x) * 100, 3) for x in np.nanmean(np.nanmean(allp, axis=1), axis=0)[:LAST + 1]]

    # 2 · opening range breakout on stocks in play
    for nb, label in ((1, "5-minute"), (3, "15-minute"), (6, "30-minute")):
        rv = np.nansum(v[:, :, :nb], axis=2); usual = pd.DataFrame(rv).rolling(14, min_periods=5).mean().shift(1).to_numpy(); rvol = rv / usual; rh = np.nanmax(h[:, :, :nb], axis=2); rl = np.nanmin(l[:, :, :nb], axis=2); dirn = np.sign(cf[:, :, nb - 1] - o[:, :, 0])
        day_r, n_tr, rs = [], 0, []
        for di in range(nd):
            ok = stock & np.isfinite(rvol[di]) & (rvol[di] >= 1) & (dirn[di] != 0) & np.isfinite(atr[di]) & (atr[di] > 0); cand = np.where(ok)[0]
            if not len(cand):
                day_r.append(np.nan); continue
            top = cand[np.argsort(-rvol[di, cand])[:10]]; tot = 0.0
            for si in top:
                sg, lvl, risk = dirn[di, si], (rh[di, si] if dirn[di, si] > 0 else rl[di, si]), 0.10 * atr[di, si]; e = x = None
                for b in range(nb, EXIT_BAR + 1):
                    ob, hb, lb = o[di, si, b], h[di, si, b], l[di, si, b]
                    if not np.isfinite(ob): continue
                    if e is None:
                        if (sg > 0 and hb > lvl) or (sg < 0 and lb < lvl):
                            e = float(max(ob, lvl) if sg > 0 else min(ob, lvl)); stop = e - sg * risk
                            if (sg > 0 and lb <= stop) or (sg < 0 and hb >= stop): x = stop; break          # the entry candle also reached the stop: counted as stopped
                        continue
                    if (sg > 0 and ob <= stop) or (sg < 0 and ob >= stop): x = float(ob); break
                    if (sg > 0 and lb <= stop) or (sg < 0 and hb >= stop): x = stop; break
                if e is None: continue
                if x is None: x = float(cf[di, si, EXIT_BAR])
                r = sg * (x / e - 1) - COST_ORB; tot += r; n_tr += 1; rs.append(r)
            day_r.append(tot / 10)
        tests.append({"id": f"orb{nb * 5}", "group": "Opening range breakout on stocks in play", "name": f"{label} range, ten highest opening volumes, stop 10% of ATR, out at 3:15 pm", "cost": COST_ORB * 100, **stats(day_r, n_tr), "per_trade": round(float(np.mean(rs)) * 100, 3) if rs else None, "won_trades": round(float((np.array(rs) > 0).mean()) * 100, 0) if rs else None})

    # 3 · first half hour -> last half hour
    r1 = cf[:, :, 5].astype("float64") / prevC - 1; last = cf[:, :, LAST].astype("float64") / o[:, :, LATE].astype("float64") - 1
    for name in INDEX:
        if name in syms:
            i = int(np.where(syms == name)[0][0]); x = np.sign(r1[:, i]) * last[:, i] - COST_IX; x[~np.isfinite(r1[:, i])] = np.nan
            tests.append({"id": "hh_" + name.lower(), "group": "Half-hour momentum", "name": f"{name}: trade the last half hour in the direction of the move to 9:45", "cost": COST_IX * 100, **stats(x, int(np.isfinite(x).sum()))})
    xs, n_tr = [], 0
    for di in range(nd):
        ok = stock & np.isfinite(r1[di]) & np.isfinite(last[di]); idx = np.where(ok)[0]
        if len(idx) < 30: xs.append(np.nan); continue
        k = max(3, len(idx) // 10); order = idx[np.argsort(r1[di, idx])]; xs.append((last[di, order[-k:]].mean() - last[di, order[:k]].mean()) / 2 - COST_ORB); n_tr += 2 * k
    tests.append({"id": "hh_stocks", "group": "Half-hour momentum", "name": "Stocks: buy the strongest tenth by 9:45 and sell the weakest tenth, for the last half hour", "cost": COST_ORB * 100, **stats(xs, n_tr)})

    # 4 · the day's losers in the last half hour
    rday = o[:, :, LATE].astype("float64") / O - 1; xs, n_tr = [], 0
    for di in range(nd):
        ok = stock & np.isfinite(rday[di]) & np.isfinite(last[di]); idx = np.where(ok)[0]
        if len(idx) < 30: xs.append(np.nan); continue
        k = max(3, len(idx) // 10); order = idx[np.argsort(rday[di, idx])]; xs.append(last[di, order[:k]].mean() - COST_ORB); n_tr += k
    tests.append({"id": "late_losers", "group": "Late rebound", "name": "Buy the weakest tenth of the day at 3:00 pm, sell at 3:25 pm", "cost": COST_ORB * 100, **stats(xs, n_tr)})
    return tests, extra


def clean(x):                                          # no NaN in the file: the site reads it as plain JSON
    if isinstance(x, dict): return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [clean(v) for v in x]
    if isinstance(x, (float, np.floating)): return None if not math.isfinite(x) else float(x)
    if isinstance(x, np.integer): return int(x)
    return x


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--picks", default="arka_data/model/day_picks.csv"); ap.add_argument("--out", required=True); a = ap.parse_args()
    days, syms, A = load(a.data); picks = pd.read_csv(a.picks, dtype={"date": "string", "sym": "string"}) if Path(a.picks).exists() else None
    print(f"{len(days)} days ({days[0]} to {days[-1]}), {len(syms)} symbols" + ("" if picks is not None else " · no day_picks.csv yet: the day-book test is skipped until the Arka Model job has run"))
    tests, extra = run(days, syms, A, picks)
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "data": {"days": int(len(days)), "first": str(days[0]), "last": str(days[-1]), "symbols": int(len(syms)), "split": int(len(days) * 0.6)}, "tests": tests, **extra,
           "rule": "An idea holds only if it is positive after costs in the whole history (t of 2 or more) and in both parts: the first 60% of days and the later 40%."}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(clean(out), separators=(",", ":"), allow_nan=False))
    for t in tests:
        print(f"  {t['verdict']:8s} {t['name'][:78]:78s} " + (f"{t['mean']:+.3f}%/day t {t['t']:+.1f} · first part {t['first']['mean']:+.3f}, later {t['later']['mean']:+.3f}" if "mean" in t else t.get("why", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
