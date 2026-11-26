"""
Arka Trades · Arka Model v2 (arka_model.py)

A machine-learning desk for F&O stocks, run every evening by GitHub Actions (workflow: model.yml). Nothing here is typed in
by hand: every number on the Signals page comes out of this file.

What it does
  1. Reads the F&O history in arka_data/fno (futures, open interest, option chain summary, implied volatility, who holds
     what) and NSE cash prices with delivery (the same sources the old signal engine used).
  2. Turns them into about 190 readings per stock per day, all known at that day's close.
  3. STOCK MODEL   gradient-boosted trees rank every F&O stock by the expected result of one fixed trade:
                   buy at the next open · stop 1.5 ATR below · target 2.5 ATR above · out after 5 sessions · 0.15% a side.
  4. MARKET GAUGE  a second model: do the next few sessions look supportive or defensive for that trade?
  5. NIFTY TILT    a third model: NIFTY 50's next session, up or down.
  6. THE RECORD    walk-forward only. A model is trained on the past, then scored on the following 63 sessions it has
                   never seen, then retrained, and so on. Tonight's ranking comes from exactly that procedure. Design
                   choices were made on 2023-24; everything from 2025-01-01 is shown separately as "unseen".
  7. SIMULATIONS   the same book run with random stock picks (is it skill?), a block bootstrap (how wide is luck?) and
                   higher costs.
v2 adds: an independent SHORT model and short book (stock futures), a rule-based market REGIME with its reasons, a
calibrated win PROBABILITY for every signal (isotonic, fitted only on earlier out-of-sample scores, with its Brier score),
a SECTOR CAP in each book, professional benchmarks (12-month momentum with the same trade), Sortino, Calmar and the
deflated Sharpe ratio, and results split by regime. One decision per stock: BUY, SHORT, WATCH, AVOID or WAIT.
Files written to arka_data/model/: model.json (the page), charts.json (candles and trade marks), log.csv (every night's
picks as they were issued: the real forward record).

  python arka_model.py --eod eod2_data            Needs: pandas, numpy, scikit-learn, requests. Not investment advice.
"""
from __future__ import annotations
import argparse, glob, json, math, os, pickle, time, warnings
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import signal_engine as SE
try:
    import sector_map as SM
    SECTOR = dict(SM._BY_SYMBOL)
except Exception:
    SECTOR = {}

warnings.filterwarnings("ignore")
VERSION = 3
FNO, OUT = Path("arka_data") / "fno", Path("arka_data") / "model"
STOP_K, TGT_K, HOLD, COST = 1.5, 2.5, 5, 0.0015
FIRST, UNSEEN = "2019-01-01", "2025-01-01"          # first scored day · start of the period no choice was made on
STEP, PURGE, SEEDS = 63, 6, (7, 11)
FORWARD_DAYS = 60                                     # sessions of signals logged before the open that a book needs before it can be approved
MAX_TRAIN, SECTOR_CAP, TRIALS = 220_000, 3, 30
BLOCK = {"long": (), "short": ("High volatility", "Strong bearish")}      # regimes in which a book takes no new trade
DEF_NEW = {"long": 0, "short": 1}                     # new trades allowed on a day that book's gauge is defensive
# Both rules were chosen on the design period only (2019-2024), from 24 book configurations: longs only when the long gauge is
# supportive; no new short in a panic or after the market has already broken down (shorts were squeezed there).
SIMS = float(os.environ.get("ARKA_SIMS", "1"))        # scale for the simulation counts (research runs use less)
CM = None                                             # session x stock closing prices, for marking open trades to market        # rows per fit (random sample of the past) · most names from one sector in a book · configurations tried, for the deflated Sharpe
SLOTS, NEW_SUP, NEW_DEF, TOP = 10, 5, 1, 5
GB = dict(max_depth=4, learning_rate=0.03, max_iter=300, min_samples_leaf=300, l2_regularization=1.0, max_features=0.5)
NOT_X = {"date", "sym", "y", "xt", "why", "live", "fin", "ys", "xts", "whys", "lives", "fins", "o1", "c", "atr", "ni_c", "ba_c", "h", "l", "o"}


def trade(o, h, l, c, atr, side=1):
    """Signal at close t -> trade at open t+1 (side 1 buys, -1 sells short). Returns the result net of costs, exit index,
    reason (0 time, 1 stop, 2 target) and whether it is finished. When stop and target are both inside one day's range the
    stop is assumed first."""
    n = len(c); ret = np.full(n, np.nan); xt = np.full(n, -1); why = np.zeros(n, dtype="int8"); done = np.zeros(n, dtype=bool)
    for t in range(n - 1):
        a, e = atr[t], o[t + 1]
        if not (a > 0) or not (e > 0):
            continue
        sl, tg, last, x, w, j = e - side * STOP_K * a, e + side * TGT_K * a, min(n - 1, t + HOLD), np.nan, 0, 0
        for j in range(t + 1, last + 1):
            if side > 0:
                if j > t + 1 and o[j] <= sl: x, w = o[j], 1; break
                if l[j] <= sl: x, w = sl, 1; break
                if j > t + 1 and o[j] >= tg: x, w = o[j], 2; break
                if h[j] >= tg: x, w = tg, 2; break
            else:
                if j > t + 1 and o[j] >= sl: x, w = o[j], 1; break
                if h[j] >= sl: x, w = sl, 1; break
                if j > t + 1 and o[j] <= tg: x, w = o[j], 2; break
                if l[j] <= tg: x, w = tg, 2; break
        if w == 0:
            x, j = c[last], last
        ret[t], xt[t], why[t], done[t] = side * (x / e - 1) - 2 * COST, j, w, (w > 0 or last == t + HOLD)
    return ret, xt, why, done


def stock_frame(sym, d, u):
    O, H, L, C, V, DQ = (d[k] for k in ("O", "H", "L", "C", "V", "DQ")); pc = C.shift(1)
    tr = np.fmax(np.fmax((H - L).values, (H - pc).abs().values), (L - pc).abs().values)
    atr = pd.Series(tr, index=C.index).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    F = {}
    for k in (1, 2, 3, 5, 10, 21, 63, 126): F[f"r{k}"] = C / C.shift(k) - 1
    F["m12"] = C.shift(21) / C.shift(252) - 1; F["atrp"] = atr / C
    F["r5a"], F["r1a"] = F["r5"] / F["atrp"], F["r1"] / F["atrp"]
    for k in (10, 20, 50, 200): F[f"d{k}"] = C / C.rolling(k).mean() - 1
    F["hi252"] = C / H.rolling(252, min_periods=120).max() - 1; F["lo252"] = C / L.rolling(252, min_periods=120).min() - 1
    F["hi20"] = C / H.rolling(20).max() - 1; F["rng"] = (C - L) / (H - L).replace(0, np.nan); F["gap"] = O / pc - 1; F["body"] = (C - O) / atr
    F["volx"] = V / V.rolling(50, min_periods=30).mean().shift(1); F["volx5"] = V.rolling(5).mean() / V.rolling(50, min_periods=30).mean().shift(5)
    F["dshare"] = DQ / V; F["dshare_x"] = F["dshare"] / F["dshare"].rolling(20, min_periods=10).mean().shift(1); F["dqx"] = DQ / DQ.rolling(20, min_periods=15).mean().shift(1)
    F["vol20"] = F["r1"].rolling(20).std(); F["volr"] = F["vol20"] / F["r1"].rolling(120).std(); F["up5"] = (F["r1"] > 0).rolling(5).sum(); F["turn"] = np.log((C * V).rolling(20).median())
    u = u.set_index("date").sort_index().reindex(C.index); oi = u["fut_oi"].astype(float); tot = (u["ce_oi"] + u["pe_oi"]).astype(float)
    F["basis"] = u["fut_next"] / u["fut"] - 1                 # next month against this month: both from the F&O file, so a later split cannot distort it
    F["oi1"], F["oi5"], F["oi20"] = oi / oi.shift(1) - 1, oi / oi.shift(5) - 1, oi / oi.rolling(20).mean() - 1
    F["oiz"] = (oi - oi.rolling(60).mean()) / oi.rolling(60).std(); F["bu1"] = np.sign(F["r1"]) * F["oi1"]
    F["longbu"] = ((F["r1"] > 0) & (F["oi1"] > 0)).astype(float) * F["oi1"]; F["shortbu"] = ((F["r1"] < 0) & (F["oi1"] > 0)).astype(float) * F["oi1"]
    F["shortcov"] = ((F["r1"] > 0) & (F["oi1"] < 0)).astype(float) * -F["oi1"]; F["longunw"] = ((F["r1"] < 0) & (F["oi1"] < 0)).astype(float) * -F["oi1"]
    F["bu5"] = np.sign(F["r5"]) * F["oi5"]; F["roll"] = u["fut_oi_next"] / (u["fut_oi_near"] + u["fut_oi_next"]).replace(0, np.nan)
    F["fvolx"] = u["fut_vol"] / u["fut_vol"].rolling(20).mean().shift(1)
    F["pcr"] = u["pe_oi"] / u["ce_oi"].replace(0, np.nan); F["pcr5"] = F["pcr"] - F["pcr"].shift(5); F["pcrz"] = (F["pcr"] - F["pcr"].rolling(60).mean()) / F["pcr"].rolling(60).std()
    F["ceadd"], F["peadd"] = u["ce_oi_chg"] / tot, u["pe_oi_chg"] / tot; F["netadd"] = F["peadd"] - F["ceadd"]; F["pcv"] = u["pe_vol"] / u["ce_vol"].replace(0, np.nan)
    ov = u["ce_vol"] + u["pe_vol"]; F["optx"] = ov / ov.rolling(20).mean().shift(1); F["opt_fut"] = tot / oi.replace(0, np.nan)
    iv = u["iv"].astype(float); F["iv"], F["iv1"], F["iv5"] = iv, iv - iv.shift(1), iv - iv.shift(5); F["ivpct"] = iv.rolling(120, min_periods=60).rank(pct=True)
    F["ivrv"] = iv / (F["vol20"] * np.sqrt(252)); F["skew"] = u["iv_put"] - u["iv_call"]; F["skew5"] = F["skew"] - F["skew"].shift(5)
    F["straddle"], F["dte"] = u["straddle"], u["dte"]; F["mp"], F["cw"], F["pw"] = u["max_pain"] / u["fut"] - 1, u["call_wall"] / u["fut"] - 1, u["put_wall"] / u["fut"] - 1      # strikes against the futures price of the same day (never the split-adjusted cash price)
    F["wallr"] = u["put_wall_oi"] / u["call_wall_oi"].replace(0, np.nan)
    ret, xt, why, done = trade(O.values, H.values, L.values, C.values, atr.values)
    n = len(C); full = np.arange(n) + HOLD <= n - 1
    rs, xs, ws, ds = trade(O.values, H.values, L.values, C.values, atr.values, -1)
    F = pd.DataFrame(F); F["y"] = np.where(full, ret, np.nan); F["live"] = ret; F["xt"] = xt; F["why"] = why; F["fin"] = done
    F["ys"] = np.where(full, rs, np.nan); F["lives"] = rs; F["xts"] = xs; F["whys"] = ws; F["fins"] = ds
    F["o1"], F["c"], F["atr"], F["o"], F["h"], F["l"] = O.shift(-1), C, atr, O, H, L
    F = F[u["fut"].notna().values & C.notna().values]; F["sym"] = sym
    return F.rename_axis("date").reset_index()


def market(U, part):
    M = {}
    for s in ("NIFTY", "BANKNIFTY"):
        u = U[U.sym == s].set_index("date").sort_index(); p = s.lower()[:2]; sp = u["spot"].astype(float).fillna(u["fut"]); M[p + "_c"] = sp
        for k in (1, 3, 5, 10, 21): M[f"{p}_r{k}"] = sp / sp.shift(k) - 1
        M[p + "_d50"] = sp / sp.rolling(50).mean() - 1; M[p + "_d200"] = sp / sp.rolling(200, min_periods=100).mean() - 1
        M[p + "_basis"] = (u["fut"] / sp - 1) / u["dte"].clip(lower=1) * 30; oi = u["fut_oi"]; M[p + "_oi1"], M[p + "_oi5"] = oi / oi.shift(1) - 1, oi / oi.shift(5) - 1
        M[p + "_bu1"] = np.sign(M[f"{p}_r1"]) * M[p + "_oi1"]; pcr = u["pe_oi"] / u["ce_oi"]
        M[p + "_pcr"], M[p + "_pcr1"], M[p + "_pcrz"] = pcr, pcr - pcr.shift(1), (pcr - pcr.rolling(60).mean()) / pcr.rolling(60).std(); M[p + "_wpcr"] = u["w_pe_oi"] / u["w_ce_oi"]
        M[p + "_iv"], M[p + "_iv1"], M[p + "_iv5"] = u["iv"], u["iv"] - u["iv"].shift(1), u["iv"] - u["iv"].shift(5); M[p + "_ivpct"] = u["iv"].rolling(120, min_periods=60).rank(pct=True)
        M[p + "_skew"] = u["iv_put"] - u["iv_call"]; M[p + "_mp"], M[p + "_wmp"] = u["max_pain"] / sp - 1, u["w_max_pain"] / sp - 1
        M[p + "_cw"], M[p + "_pw"] = u["w_call_wall"].fillna(u["call_wall"]) / sp - 1, u["w_put_wall"].fillna(u["put_wall"]) / sp - 1
        M[p + "_netadd"] = (u["pe_oi_chg"] - u["ce_oi_chg"]) / (u["ce_oi"] + u["pe_oi"]); M[p + "_dte"] = u["dte"]
        M[p + "_rv"] = M[f"{p}_r1"].rolling(20).std() * np.sqrt(252); M[p + "_ivrv"] = u["iv"] / M[p + "_rv"]
    M = pd.DataFrame(M)
    P = part.pivot_table(index="date", columns="who", values=[c for c in part.columns if c not in ("date", "who")])
    for who in ("FII", "Client", "Pro", "DII"):
        w = who.lower()[:2]; fl, fs = P["fi_long"][who], P["fi_short"][who]
        M[f"{w}_ils"] = fl / (fl + fs); M[f"{w}_ils5"], M[f"{w}_ils1"] = M[f"{w}_ils"] - M[f"{w}_ils"].shift(5), M[f"{w}_ils"] - M[f"{w}_ils"].shift(1)
        M[f"{w}_ilspct"] = M[f"{w}_ils"].rolling(250, min_periods=100).rank(pct=True); sl_, ss = P["fs_long"][who], P["fs_short"][who]
        M[f"{w}_sls"] = sl_ / (sl_ + ss); M[f"{w}_sls5"] = M[f"{w}_sls"] - M[f"{w}_sls"].shift(5)
        bull, bear = P["oi_call_long"][who] + P["oi_put_short"][who], P["oi_put_long"][who] + P["oi_call_short"][who]
        M[f"{w}_opt"] = (bull - bear) / (bull + bear).replace(0, np.nan); M[f"{w}_opt1"] = M[f"{w}_opt"] - M[f"{w}_opt"].shift(1)
    return M


def dataset(P, say=print):
    U = pd.concat([pd.read_csv(f, parse_dates=["date"]) for f in sorted(glob.glob(str(FNO / "u" / "*.csv")))], ignore_index=True).drop_duplicates(["date", "sym"], keep="last")
    part = pd.read_csv(FNO / "participants.csv", parse_dates=["date"])
    U = U[U.date <= P["C"].index[-1]]; frames, miss = [], []
    for sym, u in U[U.kind == "STK"].groupby("sym"):
        if sym not in P["C"].columns or P["C"][sym].notna().sum() < 300:
            miss.append(sym); continue
        d = {k: P[k][sym].astype(float) for k in ("O", "H", "L", "C", "V", "DQ")}
        frames.append(stock_frame(sym, d, u))
    D = pd.concat(frames, ignore_index=True); M = market(U, part); g = D.groupby("date")
    B = pd.DataFrame({"br_up": g["r1"].apply(lambda x: (x > 0).mean()), "br_d50": g["d50"].apply(lambda x: (x > 0).mean()), "br_d200": g["d200"].apply(lambda x: (x > 0).mean()),
                      "br_r5": g["r5"].median(), "br_oi1": g["oi1"].median(), "br_iv": g["iv"].median(), "br_pcr": g["pcr"].median(), "br_longbu": g["longbu"].apply(lambda x: (x > 0).mean()),
                      "br_shortbu": g["shortbu"].apply(lambda x: (x > 0).mean()), "br_disp": g["r5"].std()})
    B["br_iv5"], B["br_d50_5"] = B["br_iv"] - B["br_iv"].shift(5), B["br_d50"] - B["br_d50"].shift(5)
    R = {"x_" + c: g[c].rank(pct=True) for c in ("r1", "r5", "r21", "r63", "m12", "oi1", "oi5", "iv", "ivpct", "pcr", "skew", "volx", "dshare_x", "atrp", "basis", "d50", "hi252", "netadd", "ivrv", "r126")}
    D = pd.concat([D, pd.DataFrame(R)], axis=1).merge(M, left_on="date", right_index=True, how="left").merge(B, left_on="date", right_index=True, how="left")
    D = pd.concat([D, pd.DataFrame({"rel5": D["r5"] - D["ni_r5"], "rel21": D["r21"] - D["ni_r21"], "rel1": D["r1"] - D["ni_r1"], "dow": D["date"].dt.dayofweek})], axis=1)
    D = D[D.date >= D.date.min() + pd.Timedelta(days=85)].sort_values(["date", "sym"]).reset_index(drop=True)      # the F&O readings need about three months of their own history
    M = M.join(B); M = M[M.index >= D.date.min()].copy()
    M["t_mkt"] = D.groupby("date").y.mean().reindex(M.index); M["t_mkts"] = D.groupby("date").ys.mean().reindex(M.index); M["t_n1"] = M["ni_c"].shift(-1) / M["ni_c"] - 1
    say(f"dataset: {len(D):,} stock-days · {D.sym.nunique()} stocks · {D.date.nunique()} sessions {D.date.min().date()} to {D.date.max().date()} · no cash history for {len(miss)}: {', '.join(miss[:25])}")
    return D, M, miss


def walk_stock(D, say=print):
    """Two independent models: one ranks stocks for the long trade, one for the short trade. Each is scored only on sessions after its training data."""
    X = [c for c in D.columns if c not in NOT_X]; days = np.array(sorted(D.date.unique())); i0 = int(np.searchsorted(days, np.datetime64(FIRST))); rng = np.random.default_rng(3)
    T = {"score": D.y - D.groupby("date").y.transform("mean"), "score_s": D.ys - D.groupby("date").ys.transform("mean")}; out = {k: pd.Series(np.nan, index=D.index) for k in T}
    for a in range(i0, len(days), STEP):
        te = (D.date >= days[a]) & (D.date <= days[min(a + STEP, len(days)) - 1]); past = np.flatnonzero((D.date <= days[a - PURGE - 1]).to_numpy() & T["score"].notna().to_numpy())
        if len(past) > MAX_TRAIN: past = np.sort(rng.choice(past, MAX_TRAIN, replace=False))
        Xtr = D.iloc[past][X]; use = [c for c in X if Xtr[c].notna().sum() >= 200 and Xtr[c].nunique() > 1]; Xtr, Xte = Xtr[use], D.loc[te, use]      # a reading with no history yet (weekly options before 2019) is left out of that fit
        for k, tgt in T.items():
            p = np.zeros(int(te.sum()))
            for sd in SEEDS: p += HistGradientBoostingRegressor(**GB, random_state=sd).fit(Xtr, tgt.iloc[past]).predict(Xte)
            out[k][te] = p / len(SEEDS)
        say(f"  stock models: trained to {str(days[a - PURGE - 1])[:10]} on {len(past):,} rows, scored {str(days[a])[:10]} onward")
    return out["score"], out["score_s"], len(X)


def walk_market(M):
    X = [k for k in M.columns if not k.startswith("t_") and k not in ("ni_c", "ba_c")]; days = M.index; i0 = int(days.searchsorted(FIRST)); G = pd.DataFrame(index=days, columns=["g_mkt", "g_mkts", "g_n1"], dtype=float)
    gb = lambda: HistGradientBoostingRegressor(max_depth=2, learning_rate=0.03, max_iter=150, min_samples_leaf=25, l2_regularization=5, max_features=0.4, random_state=7)
    mk = {"g_mkt": ("t_mkt", gb), "g_mkts": ("t_mkts", gb), "g_n1": ("t_n1", lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), RidgeCV(alphas=[100, 300, 1000, 3000, 10000])))}
    for a in range(i0, len(days), 21):
        for col, (tgt, f) in mk.items():
            tr = M.iloc[:a - PURGE]; tr = tr[tr[tgt].notna()]; use = [c for c in X if tr[c].notna().sum() >= 60 and tr[c].nunique() > 1]
            G.iloc[a:a + 21, G.columns.get_loc(col)] = f().fit(tr[use], tr[tgt]).predict(M.iloc[a:a + 21][use])
    return G, len(X)


REGIMES = ["Strong bullish", "Weak bullish", "Sideways", "Distribution", "Strong bearish", "High volatility"]
def regime_of(r):
    """Fixed, readable rules on the market readings (not a fitted model). Returns the regime, its reasons and the stance."""
    up200, up50, br, brc, ivp, r5 = r.ni_d200 > 0, r.ni_d50 > 0, r.br_d50, r.br_d50_5, r.ni_ivpct, r.ni_r5
    why = [f"NIFTY is {abs(r.ni_d200) * 100:.1f}% {'above' if up200 else 'below'} its 200-day average and {abs(r.ni_d50) * 100:.1f}% {'above' if up50 else 'below'} its 50-day",
           f"{br * 100:.0f}% of F&O stocks are above their 50-day average ({'+' if brc >= 0 else '−'}{abs(brc) * 100:.0f} points in a week)",
           f"NIFTY implied volatility is at its {ivp * 100:.0f}th percentile of the last 120 sessions" if ivp == ivp else "NIFTY implied volatility percentile not available",
           f"FIIs hold {r.fi_ils * 100:.0f}% of their index futures long ({'+' if r.fi_ils5 >= 0 else '−'}{abs(r.fi_ils5) * 100:.0f} points in a week)" if r.fi_ils == r.fi_ils else "FII positioning not available",
           f"NIFTY put-call ratio {r.ni_pcr:.2f}" if r.ni_pcr == r.ni_pcr else ""]
    if ivp == ivp and ivp >= 0.9 and r5 <= -0.02: reg, act = "High volatility", "Sharp fall with volatility near its high. Smallest size. Wait for the range to settle before fresh longs."
    elif up200 and up50 and br >= 0.6: reg, act = "Strong bullish", "Trend and breadth agree. Full long book; shorts only on the weakest names."
    elif up200 and up50: reg, act = "Weak bullish", "The index is up but fewer stocks are taking part. Be selective with longs."
    elif up200 and not up50 and (br < 0.45 or brc <= -0.1): reg, act = "Distribution", "The index lost its 50-day average and breadth is falling. Cut aggressive longs; short-side research first."
    elif not up200 and not up50 and br < 0.4: reg, act = "Strong bearish", "Trend and breadth are both down. Longs are against the tide; the short book leads."
    else: reg, act = "Sideways", "No clear trend. Few trades; no trade is a valid decision."
    return reg, [w for w in why if w], act


# ───────────── the books and their statistics ─────────────
SIDE = {"long": dict(score="score", live="live", xt="_xt", fin="fin", y="y", why="why", g="g_mkt", sign=1), "short": dict(score="score_s", live="lives", xt="_xts", fin="fins", y="ys", why="whys", g="g_mkts", sign=-1)}
def make_days(D, side, order=None):
    K = SIDE[side]; S = D[D[K["score"]].notna() & D[K["live"]].notna()]; by = {}
    for t, g in S.groupby("t"):
        g = g.sort_values(order or K["score"], ascending=False)
        by[t] = (g.k.to_numpy(), g[K["live"]].to_numpy(), g[K["xt"]].to_numpy().astype(int), bool(g[K["g"]].iloc[0] > 0), g[K["fin"]].to_numpy(), g.index.to_numpy(), g.sec.to_numpy(), g.o1.to_numpy(), g.regime.iloc[0] in BLOCK[side], K["sign"], DEF_NEW[side])
    return by


def book(by, t0, t1, rng=None, gauge=True):
    """SLOTS equal slots; no stock twice; at most SECTOR_CAP names from one sector; no new trade in a blocked regime.
    The curve is marked to market every session: open trades are valued at that day's close."""
    eq, curve, open_, trades = 1.0, np.ones(t1 - t0), [], []
    for t in range(t0, t1):
        if open_:
            keep = []
            for o in open_:
                if o[4] and o[0] <= t: eq += o[3] * o[2]
                else: keep.append(o)
            open_ = keep
        mtm = 0.0
        for o in open_:
            px = CM[t, o[1]]
            if o[6] < t and px == px and o[8] > 0: mtm += o[3] * (o[9] * (px / o[8] - 1) - COST)
        curve[t - t0] = eq + mtm; g = by.get(t)
        if g is None or g[8]: continue
        code, y, xt, sup, fin, idx, sec, o1, _, sign, nd = g; cap = min(NEW_SUP if (sup or not gauge) else nd, SLOTS - len(open_))
        if cap <= 0: continue
        held, k, secs = {o[1] for o in open_}, 0, {}
        for o in open_: secs[o[7]] = secs.get(o[7], 0) + 1
        for i in (rng.permutation(len(code)) if rng is not None else range(len(code))):
            if k >= cap: break
            if code[i] in held or (sec[i] >= 0 and secs.get(sec[i], 0) >= SECTOR_CAP): continue
            open_.append((xt[i], code[i], y[i], eq / SLOTS, fin[i], idx[i], t, sec[i], o1[i], sign)); trades.append((t, code[i], y[i], sup, fin[i], idx[i], xt[i])); secs[sec[i]] = secs.get(sec[i], 0) + 1; k += 1
    return curve, trades, open_


def tstats(y):
    y = np.asarray([v for v in y if v == v], float)
    if not len(y): return {"n": 0}
    w, l = y[y > 0].sum(), -y[y < 0].sum()
    return {"n": int(len(y)), "mean": round(float(y.mean()) * 100, 3), "win": round(float((y > 0).mean()) * 100, 1), "pf": round(float(w / l), 2) if l > 0 else None,
            "avg_win": round(float(y[y > 0].mean()) * 100, 2) if (y > 0).any() else None, "avg_loss": round(float(y[y < 0].mean()) * 100, 2) if (y < 0).any() else None}


def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def _ninv(p):                                         # inverse normal, Acklam's approximation (enough for the deflated Sharpe)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    q = p - 0.5; r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def cstats(c):
    r = np.diff(c) / c[:-1]; yrs = max(len(c) / 250, 1e-9); sd = r.std(); dn = r[r < 0]; dd = float((c / np.maximum.accumulate(c) - 1).min()); cagr = float((c[-1] / c[0]) ** (1 / yrs) - 1)
    sr = float(r.mean() / sd) if sd > 0 else 0.0; n = len(r); dsr = None
    if sd > 0 and n > 60:                             # deflated Sharpe: the chance the true Sharpe is above what the best of TRIALS lucky tries would show
        z = (r - r.mean()) / sd; sk, ku = float((z ** 3).mean()), float((z ** 4).mean()); e = 0.5772156649
        sr0 = math.sqrt(1 / n) * ((1 - e) * _ninv(1 - 1 / TRIALS) + e * _ninv(1 - 1 / (TRIALS * math.e)))
        den = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr * sr)); dsr = round(_ncdf((sr - sr0) * math.sqrt(n - 1) / den) * 100, 1)
    return {"total": round(float(c[-1] / c[0] - 1) * 100, 1), "cagr": round(cagr * 100, 1), "maxdd": round(dd * 100, 1), "sharpe": round(sr * math.sqrt(250), 2),
            "sortino": round(float(r.mean() / dn.std() * math.sqrt(250)), 2) if len(dn) > 5 and dn.std() > 0 else None, "calmar": round(cagr / abs(dd), 2) if dd < 0 else None, "dsr": dsr}


def record(D, days, side, by, by_mom, G, t0, t1, sims, rng):
    K = SIDE[side]; yc, sc = K["y"], K["score"]; sub = D[(D.t >= t0) & (D.t < t1) & D[sc].notna() & D[yc].notna()]; rk = sub.groupby("t")[sc].rank(ascending=False, method="first"); rkb = sub.groupby("t")[sc].rank(method="first")
    top = sub[rk <= TOP]; sup = sub[K["g"]] > 0; c, tr, _ = book(by, t0, t1); c0, _, _ = book(by, t0, t1, gauge=False); cm, trm, _ = book(by_mom, t0, t1)
    sims = max(20, int(sims * SIMS)); R = np.array([book(by, t0, t1, rng=rng)[0] for _ in range(sims)]); end = R[:, -1]; ni = D.groupby("t").ni_c.first().reindex(range(t0, t1)).ffill().bfill().to_numpy()
    rets = np.diff(c) / c[:-1]; n, blk, B = len(rets), 10, []
    for _ in range(sims):
        st = rng.integers(0, max(1, n - blk), size=n // blk + 1); B.append(float(np.prod(1 + np.concatenate([rets[s:s + blk] for s in st])[:n])))
    B = np.array(B); done = [x[2] for x in tr if x[4]]; reg = D.loc[[x[5] for x in tr if x[4]], "regime"].to_numpy() if done else np.array([])
    cal = sub[sub["p_" + side].notna() & (rk <= 10)]; bins = []
    if len(cal) > 200:
        q = pd.qcut(cal["p_" + side].rank(method="first"), 5, labels=False)
        for b in range(5):
            x = cal[q == b]; bins.append({"said": round(float(x["p_" + side].mean()) * 100, 1), "won": round(float((x[yc] > 0).mean()) * 100, 1), "n": int(len(x))})
    out = {"from": str(days[t0])[:10], "to": str(days[t1 - 1])[:10], "sessions": int(t1 - t0), "book": cstats(c), "book_no_gauge": cstats(c0), "momentum": {**cstats(cm), "trades": tstats([x[2] for x in trm if x[4]])}, "trades": tstats(done),
           "picks": tstats(top[yc]), "picks_sup": tstats(top[yc][sup[top.index]]), "picks_def": tstats(top[yc][~sup[top.index]]), "avoid": tstats(sub[yc][rkb <= 20]), "all": tstats(sub[yc]),
           "sup_share": round(float(sup.groupby(sub.t).first().mean()) * 100, 0), "all_sup": f_(sub[yc][sup].mean() * 100, 3) if sup.any() else None, "all_def": f_(sub[yc][~sup].mean() * 100, 3) if (~sup).any() else None,
           "nifty_change": round(float(ni[-1] / ni[0] - 1) * 100, 1),
           "random": {"sims": int(sims), "median": round(float(np.median(end) - 1) * 100, 1), "p5": round(float(np.quantile(end, .05) - 1) * 100, 1), "p95": round(float(np.quantile(end, .95) - 1) * 100, 1), "beats": round(float((c[-1] > end).mean()) * 100, 1),
                      "hist": np.histogram((end - 1) * 100, bins=24)[0].tolist(), "lo": round(float(end.min() - 1) * 100, 1), "hi": round(float(end.max() - 1) * 100, 1)},
           "bootstrap": {"p5": round(float(np.quantile(B, .05) - 1) * 100, 1), "median": round(float(np.median(B) - 1) * 100, 1), "p95": round(float(np.quantile(B, .95) - 1) * 100, 1), "loss": round(float((B < 1).mean()) * 100, 1)},
           "cost": [{"extra": e * 100, **tstats([v - 2 * e for v in done])} for e in (0.0005, 0.001, 0.002)],
           "by_regime": [{"regime": g, **tstats(np.asarray(done)[reg == g])} for g in REGIMES if (reg == g).any()],
           "calibration": {"bins": bins, "brier": round(float(((cal["p_" + side] - (cal[yc] > 0)) ** 2).mean()), 4) if len(cal) else None, "brier_base": round(float(((cal[yc] > 0).mean() - (cal[yc] > 0)).pow(2).mean()), 4) if len(cal) else None, "n": int(len(cal))},
           "_curve": c, "_rand": np.median(R, axis=0), "_ni": ni / ni[0], "_mom": cm}
    return out


def f_(x, nd=2):
    return None if x is None or x != x or abs(x) == float("inf") else round(float(x), nd)


def facts(r):
    bu = "long build-up" if r.r1 > 0 and r.oi1 > 0 else "short covering" if r.r1 > 0 else "short build-up" if r.oi1 > 0 else "long unwinding"
    return {"strength": f_(r.x_m12 * 100, 0), "d200": f_(r.d200 * 100, 1), "r5": f_(r.r5 * 100, 1), "r21": f_(r.r21 * 100, 1), "oi5": f_(r.oi5 * 100, 1), "buildup": bu, "ivpct": f_(r.ivpct * 100, 0), "iv": f_(r.iv * 100, 1),
            "pcr": f_(r.pcr, 2), "deliv": f_(r.dshare * 100, 0), "volx": f_(r.volx, 2), "hi252": f_(r.hi252 * 100, 1), "atrp": f_(r.atrp * 100, 2), "sector": SECTOR.get(r.sym, "")}


def calibrate(D, days, t0):
    """Win probability for each signal: expected result (stock score + market reading) -> share of such trades that won, learned
    by isotonic regression on EARLIER out-of-sample scores only, refitted every STEP sessions."""
    for side, K in SIDE.items():
        e = (D[K["score"]] + D[K["g"]]).to_numpy(); won = (D[K["y"]] > 0).to_numpy().astype(float); okv = (D[K["score"]].notna() & D[K["g"]].notna()).to_numpy(); lab = D[K["y"]].notna().to_numpy(); t = D.t.to_numpy(); p = np.full(len(D), np.nan); n = np.zeros(len(D))
        for a in range(t0 + 250, len(days), STEP):
            tr = okv & lab & (t >= t0) & (t <= a - PURGE - 1); te = okv & (t >= a) & (t < a + STEP)
            if tr.sum() < 5000 or not te.any(): continue
            p[te] = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds="clip").fit(e[tr], won[tr]).predict(e[te]); n[te] = tr.sum()
        D["p_" + side] = p; D["pn_" + side] = n
    return D


def forward_days():
    try:
        return int(pd.read_csv(OUT / "log.csv").query("side == 'long'").date.nunique())
    except Exception:
        return 0


def build(P, say=print):
    global CM
    t_start = time.time(); cache = os.environ.get("ARKA_CACHE")
    if cache and os.path.exists(cache):
        D, M, miss, G, nx, nm = pickle.load(open(cache, "rb")); say("scores read from " + cache)
    else:
        D, M, miss = dataset(P, say); D["score"], D["score_s"], nx = walk_stock(D, say); G, nm = walk_market(M)
        if cache: pickle.dump((D, M, miss, G, nx, nm), open(cache, "wb"))
    for k in ("t_n1", "t_mkt", "t_mkts"): G[k] = M[k]
    M["regime"] = [regime_of(r)[0] for r in M.itertuples()]
    D = D.merge(G[["g_mkt", "g_mkts", "g_n1"]], left_on="date", right_index=True, how="left").merge(M[["regime"]], left_on="date", right_index=True, how="left")
    days = np.array(sorted(D.date.unique())); ix = {d: i for i, d in enumerate(days)}; D["t"] = D.date.map(ix); pidx = P["C"].index.to_numpy(); big = len(days) + 99
    for a, b in (("xt", "_xt"), ("xts", "_xts")): D[b] = [int(np.searchsorted(days, pidx[x])) if 0 <= x < len(pidx) else big for x in D[a]]
    D["k"] = D.sym.astype("category").cat.codes; names = dict(zip(D.k, D.sym)); secs = {s: i for i, s in enumerate(sorted(set(SECTOR.values())))}; D["sec"] = [secs.get(SECTOR.get(s), -1) for s in D.sym]
    t0, tu, t1 = int(np.searchsorted(days, np.datetime64(FIRST))), int(np.searchsorted(days, np.datetime64(UNSEEN))), len(days); D = calibrate(D, days, t0)
    CM = D.pivot_table(index="t", columns="k", values="c").reindex(index=range(len(days)), columns=range(int(D.k.max()) + 1)).ffill().to_numpy()
    D["mom_l"], D["mom_s"] = D.x_m12, -D.x_m12; G = G.reindex(days); curves = {}; rng = np.random.default_rng(11); last_full = int(D[D.y.notna()].t.max()) + 1
    out = {"v": VERSION, "asof": str(days[-1])[:10], "built": datetime.now(SE.IST).isoformat(timespec="minutes"),
           "rules": {"stop_atr": STOP_K, "target_atr": TGT_K, "hold": HOLD, "cost": COST * 100, "slots": SLOTS, "new_sup": NEW_SUP, "new_def": NEW_DEF, "retrain": STEP, "first": FIRST, "unseen": UNSEEN, "sector_cap": SECTOR_CAP, "trials": TRIALS},
           "data": {"stocks": int(D.sym.nunique()), "rows": int(len(D)), "sessions": int(len(days)), "from": str(days[0])[:10], "features": nx, "market_features": nm, "missing": miss, "seconds": None}, "books": {}}
    T = D[D.t == t1 - 1]; g = G.iloc[-1]; mrow = M.iloc[-1]; reg, why, act = regime_of(mrow); hist = G.iloc[t0:]; charts, want, marks = {}, [], {}
    gn = G.iloc[t0:last_full].dropna(subset=["g_n1", "t_n1"]); upc = gn.g_n1 > 0; gu = gn[gn.index >= UNSEEN]
    rc = M["regime"].reindex(days[t0:]).value_counts()
    out["market"] = {"regime": reg, "why": why, "action": act, "regime_days": [{"regime": k, "share": round(float(rc.get(k, 0) / max(1, rc.sum())) * 100, 0)} for k in REGIMES],
                     "regime_last": [{"d": str(d)[:10], "r": r} for d, r in M["regime"].iloc[-60:].items()],
                     "nifty_up": bool(g.g_n1 > 0), "nifty_pct": f_(float((hist.g_n1 < g.g_n1).mean()) * 100, 0), "nifty": f_(float(M.ni_c.iloc[-1])),
                     "nifty_rec": {"n": int(len(gn)), "right": round(float(((gn.t_n1 > 0) == upc).mean()) * 100, 1), "always_up": round(float((gn.t_n1 > 0).mean()) * 100, 1), "after_up": f_(gn.t_n1[upc].mean() * 100, 3), "after_down": f_(gn.t_n1[~upc].mean() * 100, 3),
                                   "unseen_right": round(float(((gu.t_n1 > 0) == (gu.g_n1 > 0)).mean()) * 100, 1) if len(gu) else None, "unseen_always_up": round(float((gu.t_n1 > 0).mean()) * 100, 1) if len(gu) else None},
                     "last": [{"d": str(d)[:10], "up": bool(v.g_n1 > 0), "next": f_(v.t_n1 * 100, 2)} for d, v in G.iloc[-40:].iterrows() if v.g_n1 == v.g_n1]}
    for side, K in SIDE.items():
        sg_ = K["sign"]; by = make_days(D, side); by_mom = make_days(D, side, "mom_l" if side == "long" else "mom_s")
        rec = {"design": record(D, days, side, by, by_mom, G, t0, min(tu, last_full), 500, rng), "all": record(D, days, side, by, by_mom, G, t0, last_full, 500, rng)}
        if last_full - tu > 60: rec["unseen"] = record(D, days, side, by, by_mom, G, tu, last_full, 1000, rng)
        A = rec["all"]; n = len(A["_curve"]); step = max(1, n // 300); pick = list(range(0, n, step)); pick = pick if pick[-1] == n - 1 else pick + [n - 1]
        curve = {"d": [str(days[t0 + i])[:10] for i in pick], **{k: [round(float(A[c][i]) * 100, 2) for i in pick] for k, c in (("model", "_curve"), ("random", "_rand"), ("nifty", "_ni"), ("momentum", "_mom"))}}
        cfull, trades, open_ = book(by, t0, t1); curves[side] = cfull; cmom = A["_mom"]; years = []
        for y in sorted({str(d)[:4] for d in days[t0:last_full]}):
            a, b = max(t0, int(np.searchsorted(days, np.datetime64(y + "-01-01")))), min(last_full, int(np.searchsorted(days, np.datetime64(str(int(y) + 1) + "-01-01"))))
            if b - a < 20: continue
            c = cfull[a - t0:b - t0]; ty = [x[2] for x in trades if a <= x[0] < b and x[4]]; sub = D[(D.t >= a) & (D.t < b) & D[K["y"]].notna() & D[K["score"]].notna()]; ni = A["_ni"]
            years.append({"year": y, "unseen": y >= UNSEEN[:4], "book": round(float(c[-1] / c[0] - 1) * 100, 1), "maxdd": round(float((c / np.maximum.accumulate(c) - 1).min()) * 100, 1), **{k: v for k, v in tstats(ty).items() if k in ("n", "mean", "win", "pf")},
                          "avg_stock": f_(sub[K["y"]].mean() * 100, 3), "momentum": round(float(cmom[b - t0 - 1] / cmom[a - t0] - 1) * 100, 1), "random": round(float(A["_rand"][b - t0 - 1] / A["_rand"][a - t0] - 1) * 100, 1), "nifty": round(float(ni[b - t0 - 1] / ni[a - t0] - 1) * 100, 1)})
        for r in rec.values():
            for k in [k for k in r if k.startswith("_")]: del r[k]
        Ts = T.sort_values(K["score"], ascending=False); sup = bool(g[K["g"]] > 0); held = {names[o[1]] for o in open_}; free = max(SLOTS - len(open_), 0); blocked = reg in BLOCK[side]; cap = 0 if blocked else min(NEW_SUP if sup else DEF_NEW[side], free)
        def row(r, i):
            a_ = r.atr; return {"sym": r.sym, "rank": int(i + 1), "close": f_(r.c), "atr": f_(a_), "stop": f_(r.c - sg_ * STOP_K * a_), "target": f_(r.c + sg_ * TGT_K * a_), "stop_pct": f_(-sg_ * STOP_K * a_ / r.c * 100, 1), "target_pct": f_(sg_ * TGT_K * a_ / r.c * 100, 1),
                                "prob": f_(getattr(r, "p_" + side) * 100, 0), "prob_n": int(getattr(r, "pn_" + side)), "exp": f_((getattr(r, K["score"]) + g[K["g"]]) * 100, 2), "held": r.sym in held, **facts(r)}
        ranked = [row(r, i) for i, r in enumerate(Ts.itertuples())]; picks, k, sc = [], 0, {}
        for o in open_: sc[o[7]] = sc.get(o[7], 0) + 1
        for r in ranked:
            if k >= cap: break
            s_ = secs.get(r["sector"], -1)
            if r["held"] or (s_ >= 0 and sc.get(s_, 0) >= SECTOR_CAP): continue
            picks.append(r["sym"]); sc[s_] = sc.get(s_, 0) + 1; k += 1
        pos = []
        for o in open_:
            r = D.loc[o[5]]; e = float(r.o1) if r.o1 == r.o1 else None; cnow = float(P["C"][r.sym].dropna().iloc[-1]); ref = e if e else float(r.c)
            pos.append({"sym": r.sym, "signal": str(r.date)[:10], "entry": f_(e), "stop": f_(ref - sg_ * STOP_K * r.atr), "target": f_(ref + sg_ * TGT_K * r.atr), "price": f_(cnow), "pnl": f_(sg_ * (cnow / e - 1) * 100, 2) if e else None,
                        "days": int(t1 - 1 - o[6]), "left": int(max(0, HOLD - (t1 - 1 - o[6]))), "pending": e is None, "sector": SECTOR.get(r.sym, "")})
        recent = []
        for x in [x for x in trades if x[4]][-40:][::-1]:
            r = D.loc[x[5]]; recent.append({"sym": r.sym, "signal": str(r.date)[:10], "exit": str(days[min(x[6], t1 - 1)])[:10], "entry": f_(r.o1), "ret": f_(x[2] * 100, 2), "why": ["time", "stop", "target"][int(r[K["why"]])], "regime": r.regime})
        U = rec.get("unseen"); okb = bool(U and U["trades"].get("pf") and U["trades"]["pf"] >= 1.2 and U["book"]["total"] > 0 and U["random"]["beats"] >= 95 and U["book"]["total"] > U["momentum"]["total"])
        dsr = (U or {}).get("book", {}).get("dsr"); fwd = forward_days(); stage = "live" if (okb and dsr is not None and dsr >= 95 and fwd >= FORWARD_DAYS) else "forward" if okb else "test"
        why_ = "Unseen period: profit factor %s, book %s%%, 12-month-momentum book %s%%, better than %s%% of random-pick books, deflated Sharpe %s%%. Forward log: %d of %d sessions. Approval needs profit factor 1.2 or more, a positive book that beats the momentum book, 95%% of random books, a deflated Sharpe of 95%% or more, and %d sessions of signals logged before the open." % (
            (U or {}).get("trades", {}).get("pf"), (U or {}).get("book", {}).get("total"), (U or {}).get("momentum", {}).get("total"), (U or {}).get("random", {}).get("beats"), dsr, fwd, FORWARD_DAYS, FORWARD_DAYS)
        out["books"][side] = {"status": stage, "status_why": why_, "gauge": "Supportive" if sup else "Defensive", "gauge_pct": f_(float((hist[K["g"]] < g[K["g"]]).mean()) * 100, 0), "new": int(cap), "slots_free": int(free), "blocked": bool(blocked), "new_def": DEF_NEW[side],
                              "picks": picks, "ranked": ranked[:25], "avoid": ranked[::-1][:15] if side == "long" else [], "positions": pos, "recent": recent, "record": rec, "years": years, "curve": curve}
        want += picks + [p["sym"] for p in pos] + [r["sym"] for r in ranked[:8]] + [r["sym"] for r in recent[:6]]
        for x in trades:
            r = D.loc[x[5]]; m = marks.setdefault(r.sym, [])
            if x[0] + 1 >= t1 or r.o1 != r.o1: continue
            m.append({"d": str(days[x[0] + 1])[:10], "k": "b" if side == "long" else "ss", "p": f_(r.o1)})
            if x[4]: m.append({"d": str(days[min(x[6], t1 - 1)])[:10], "k": "s" if side == "long" else "sc", "p": f_(r.o1 * (1 + sg_ * (x[2] + 2 * COST))), "r": f_(x[2] * 100, 2), "why": ["time", "stop", "target"][int(r[K["why"]])]})
    cl, cs = curves["long"], curves["short"]
    def both(a, b):
        return 1 + (cl[a - t0:b - t0] / cl[a - t0] - 1) + (cs[a - t0:b - t0] / cs[a - t0] - 1)
    cb = {"design": (t0, min(tu, last_full)), "all": (t0, last_full)}
    if last_full - tu > 60: cb["unseen"] = (tu, last_full)
    out["combined"] = {"record": {k: {"from": str(days[a])[:10], "to": str(days[b - 1])[:10], **cstats(both(a, b)), "long": cstats(cl[a - t0:b - t0]), "short": cstats(cs[a - t0:b - t0]),
                                        "corr": f_(np.corrcoef(np.diff(cl[a - t0:b - t0]) / cl[a - t0:b - t0 - 1], np.diff(cs[a - t0:b - t0]) / cs[a - t0:b - t0 - 1])[0, 1], 2)} for k, (a, b) in cb.items()}, "years": []}
    for y in sorted({str(d)[:4] for d in days[t0:last_full]}):
        a, b = max(t0, int(np.searchsorted(days, np.datetime64(y + "-01-01")))), min(last_full, int(np.searchsorted(days, np.datetime64(str(int(y) + 1) + "-01-01"))))
        if b - a < 20: continue
        c = both(a, b); out["combined"]["years"].append({"year": y, "unseen": y >= UNSEEN[:4], "both": round(float(c[-1] - 1) * 100, 1), "maxdd": round(float((c / np.maximum.accumulate(c) - 1).min()) * 100, 1),
                                                         "long": round(float(cl[b - t0 - 1] / cl[a - t0] - 1) * 100, 1), "short": round(float(cs[b - t0 - 1] / cs[a - t0] - 1) * 100, 1)})
    ca = both(t0, last_full); n = len(ca); step = max(1, n // 300); pick = list(range(0, n, step))
    out["combined"]["curve"] = {"d": [str(days[t0 + i])[:10] for i in pick], "both": [round(float(ca[i]) * 100, 2) for i in pick]}
    out["rules"]["block"] = {k: list(v) for k, v in BLOCK.items()}
    L, Sh = out["books"]["long"], out["books"]["short"]; dec = {}
    for s in L["picks"]: dec[s] = "BUY"
    for s in Sh["picks"]: dec.setdefault(s, "SHORT")
    for r in L["ranked"][:10]: dec.setdefault(r["sym"], "WATCH")
    for r in Sh["ranked"][:10]: dec.setdefault(r["sym"], "WATCH SHORT")
    for r in L["avoid"]: dec.setdefault(r["sym"], "AVOID")
    out["decisions"] = dec; out["decision"] = "NO TRADE" if not (L["picks"] or Sh["picks"]) else " · ".join(([f"{len(L['picks'])} BUY"] if L["picks"] else []) + ([f"{len(Sh['picks'])} SHORT"] if Sh["picks"] else []))
    for sym in list(dict.fromkeys(want))[:40]:
        d = pd.DataFrame({k: P[k][sym] for k in ("O", "H", "L", "C")}).dropna().iloc[-150:]; lo = str(d.index[0])[:10]
        charts[sym] = {"d": [str(i)[:10] for i in d.index], "o": [f_(v) for v in d.O], "h": [f_(v) for v in d.H], "l": [f_(v) for v in d.L], "c": [f_(v) for v in d.C], "m": [m for m in marks.get(sym, []) if m["d"] >= lo]}
    out["data"]["seconds"] = int(time.time() - t_start)
    return out, charts


def save(out, charts):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "model.json").write_text(json.dumps(SE._clean(out), separators=(",", ":"), allow_nan=False)); (OUT / "charts.json").write_text(json.dumps(SE._clean(charts), separators=(",", ":"), allow_nan=False))
    log = OUT / "log.csv"; head = "date,regime,nifty,side,rank,sym,close,stop,target,prob,signal\n"; have = log.read_text() if log.exists() else head
    if not have.startswith(head): have = head                                   # v1 log had other columns
    if f"\n{out['asof']}," not in "\n" + have:
        m = out["market"]
        for side, B in out["books"].items():
            for r in B["ranked"][:10]:
                have += f"{out['asof']},{m['regime']},{'up' if m['nifty_up'] else 'down'},{side},{r['rank']},{r['sym']},{r['close']},{r['stop']},{r['target']},{r['prob']},{int(r['sym'] in B['picks'])}\n"
        log.write_text(have)


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--eod", default="eod2_data"); ap.add_argument("--no-topup", action="store_true"); a = ap.parse_args(argv)
    P = SE.build_panels(a.eod)
    if not a.no_topup:
        print("added from NSE:", SE.top_up(P, datetime.now(SE.IST).date(), SE.http_fetch) or "nothing newer")
    SE.finish(P); out, charts = build(P); save(out, charts); B = out["books"]
    print(f"Arka Model v{VERSION} to {out['asof']} · regime {out['market']['regime']} · decision {out['decision']} · long {B['long']['status']} {B['long']['record'].get('unseen', {}).get('book')} · short {B['short']['status']} {B['short']['record'].get('unseen', {}).get('book')} · {out['data']['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
