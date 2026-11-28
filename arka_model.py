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
Stage 37 (28 Nov 2026) gives every book a stop AND a target, chosen on 2019-24 and the first 60% of the five-minute days only:
intraday shorts 0.75 / 2 ATR from the open, intraday longs (paper) 0.5 / 0.5, swing longs 1.5 / 4 with the keep rule, swing
shorts 1.5 / 2.5. Levels are on NSE's tick grid. Backtest numbers for the website's 60-session paper check ("audit"), futures
lots, contract months and the F&O ban list travel in live.json, and the evening plan goes to Telegram when its two secrets are set.
Files written to arka_data/model/: model.json (the page), charts.json (candles and trade marks), log.csv (every night's
picks as they were issued: the real forward record), live.json (what the website's journal and alerts read), calls.csv, tg_sent.txt.

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
from sklearn.linear_model import LogisticRegression, RidgeCV
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
MAX_TRAIN, SECTOR_CAP, TRIALS = 220_000, 3, 311        # TRIALS: every rule variant ever compared on this data, for the deflated Sharpe (160 after the first 28 Nov 2026 round, 230 after the second, 311 after the stop/target round of stage 37)
KEEP_RANK, MAX_HOLD, TGT_L = 80, 20, 4.0                # long book (from 28 Nov 2026): a trade is kept while the model still ranks the stock in its top KEEP_RANK,
                                                       # stage 37 adds a TGT_L ATR target (chosen on 2019-24 among target-bearing rules; it costs ~2 points a year there and ~0.5 unseen),
                                                       # at most MAX_HOLD sessions, with the same stop. Same signals, fewer round trips: 0.30% every 5 sessions was ~15% a year of cost
BLOCK = {"long": (), "short": ("High volatility", "Strong bearish")}      # regimes in which a book takes no new trade
VOL_SIZE = False                                      # size each slot by the stock's volatility (calmer stock, bigger slot; between half and one and a half slots)
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


def trade_keep(o, h, l, c, atr, rk):
    """The long book's trade from 28 Nov 2026. Signal at close t -> bought at open t+1. Out at the stop (a gap through it exits at the
    open), at the TGT_L ATR target (stage 37; the stop is assumed first when both are inside one day), or at the next open once a close's
    rank is worse than KEEP_RANK, or at the close of the MAX_HOLD-th session.
    rk[t] is the stock's long rank at the close of panel day t (nan when it was not scored: the trade is kept). Reasons: 0 time, 1 stop, 3 rank."""
    n = len(c); ret = np.full(n, np.nan); xt = np.full(n, -1); why = np.zeros(n, dtype="int8"); done = np.zeros(n, dtype=bool)
    for t in range(n - 1):
        a, e = atr[t], o[t + 1]
        if not (a > 0) or not (e > 0) or not (rk[t] == rk[t]):
            continue
        sl, tg, last, x, w, j, fin = e - STOP_K * a, (e + TGT_L * a if TGT_L else np.inf), min(n - 1, t + MAX_HOLD), np.nan, 0, t + 1, False
        for j in range(t + 1, last + 1):
            if j > t + 1 and o[j] <= sl: x, w, fin = o[j], 1, True; break
            if l[j] <= sl: x, w, fin = sl, 1, True; break
            if j > t + 1 and o[j] >= tg: x, w, fin = o[j], 2, True; break
            if h[j] >= tg: x, w, fin = tg, 2, True; break
            if j == last: x, fin = c[j], last == t + MAX_HOLD; break
            if rk[j] == rk[j] and rk[j] > KEEP_RANK and o[j + 1] > 0: x, w, j, fin = o[j + 1], 3, j + 1, True; break
        if not (x > 0): continue
        ret[t], xt[t], why[t], done[t] = x / e - 1 - 2 * COST, j, w, fin
    return ret, xt, why, done


def keep_labels(D, P):
    """The long book's trades under the keep rule, for every scored row: columns livek, xtk (panel index), whyk, fink and yk (the result once finished)."""
    pidx = P["C"].index.to_numpy(); pos = pd.Series(np.arange(len(pidx)), index=pidx); pp = pos.reindex(D.date.to_numpy()).to_numpy().astype(int)
    rk = D.groupby("date").score.rank(ascending=False, method="first").to_numpy(); new = {k: np.full(len(D), np.nan) for k in ("livek", "xtk", "whyk")}; fin = np.zeros(len(D), bool)
    for sym, idx in D.groupby("sym").indices.items():
        o, h, l, c = (P[k][sym].to_numpy(float) for k in "OHLC"); a = np.full(len(pidx), np.nan); r = np.full(len(pidx), np.nan); a[pp[idx]] = D.atr.to_numpy()[idx]; r[pp[idx]] = rk[idx]
        ret, xt, why, done = trade_keep(o, h, l, c, a, r)
        new["livek"][idx], new["xtk"][idx], new["whyk"][idx], fin[idx] = ret[pp[idx]], xt[pp[idx]], why[pp[idx]], done[pp[idx]]
    out = pd.DataFrame(new, index=D.index); out["fink"] = fin; out["yk"] = np.where(fin, out.livek, np.nan); out["xtk"] = out.xtk.fillna(-1).astype(int); out["whyk"] = out.whyk.fillna(0).astype("int8"); out["lrank"] = rk
    return out


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
    D = pd.concat(frames, ignore_index=True); M = market(U, part)
    g = D.groupby("date")
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
           f"NIFTY implied volatility is at its {ordinal(ivp * 100)} percentile of the last 120 sessions" if ivp == ivp else "NIFTY implied volatility percentile not available",
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
SIDE = {"long": dict(score="score", live="livek", xt="_xtk", fin="fink", y="yk", why="whyk", g="g_mkt", sign=1), "short": dict(score="score_s", live="lives", xt="_xts", fin="fins", y="ys", why="whys", g="g_mkts", sign=-1)}
def make_days(D, side, order=None):
    K = SIDE[side]; S = D[D[K["score"]].notna() & D[K["live"]].notna()]; by = {}
    for t, g in S.groupby("t"):
        g = g.sort_values(order or K["score"], ascending=False)
        by[t] = (g.k.to_numpy(), g[K["live"]].to_numpy(), g[K["xt"]].to_numpy().astype(int), bool(g[K["g"]].iloc[0] > 0), g[K["fin"]].to_numpy(), g.index.to_numpy(), g.sec.to_numpy(), g.o1.to_numpy(), g.regime.iloc[0] in BLOCK[side], K["sign"], DEF_NEW[side], (np.clip(np.nanmedian(g.atrp) / g.atrp.to_numpy(), 0.5, 1.5) if VOL_SIZE else np.ones(len(g))))
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
        code, y, xt, sup, fin, idx, sec, o1, _, sign, nd, wt = g; cap = min(NEW_SUP if (sup or not gauge) else nd, SLOTS - len(open_))
        if cap <= 0: continue
        held, k, secs = {o[1] for o in open_}, 0, {}
        for o in open_: secs[o[7]] = secs.get(o[7], 0) + 1
        for i in (rng.permutation(len(code)) if rng is not None else range(len(code))):
            if k >= cap: break
            if code[i] in held or (sec[i] >= 0 and secs.get(sec[i], 0) >= SECTOR_CAP): continue
            open_.append((xt[i], code[i], y[i], eq / SLOTS * wt[i], fin[i], idx[i], t, sec[i], o1[i], sign)); trades.append((t, code[i], y[i], sup, fin[i], idx[i], xt[i])); secs[sec[i]] = secs.get(sec[i], 0) + 1; k += 1
    return curve, trades, open_


def tstats(y):
    y = np.asarray([v for v in y if v == v], float)
    if not len(y): return {"n": 0}
    w, l = y[y > 0].sum(), -y[y < 0].sum()
    return {"n": int(len(y)), "mean": round(float(y.mean()) * 100, 3), "sd": round(float(y.std(ddof=1)) * 100, 3) if len(y) > 1 else None, "win": round(float((y > 0).mean()) * 100, 1), "pf": round(float(w / l), 2) if l > 0 else None,
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
    under = c / np.maximum.accumulate(c) < 1 - 1e-12; ddlen = run = 0
    for u_ in under: run = run + 1 if u_ else 0; ddlen = max(ddlen, run)
    q = np.sort(r)[: max(1, int(len(r) * 0.05))] if len(r) else np.array([np.nan])
    return {"total": round(float(c[-1] / c[0] - 1) * 100, 1), "cagr": round(cagr * 100, 1), "maxdd": round(dd * 100, 1), "sharpe": round(sr * math.sqrt(250), 2),
            "sortino": round(float(r.mean() / dn.std() * math.sqrt(250)), 2) if len(dn) > 5 and dn.std() > 0 else None, "calmar": round(cagr / abs(dd), 2) if dd < 0 else None, "dsr": dsr,
            "ddlen": int(ddlen), "cvar5": round(float(q.mean()) * 100, 2) if len(r) else None, "worst": round(float(r.min()) * 100, 2) if len(r) else None}


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
           "audit": audit_stats([x[2] for x in tr if x[4]], [x[0] + 1 for x in tr if x[4]]),
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


def ordinal(x):
    k = int(round(x)); return f"{k}{'th' if 11 <= k % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def f_(x, nd=2):
    return None if x is None or x != x or abs(x) == float("inf") else round(float(x), nd)


def next_results(asof: str, ahead: int = 45) -> dict:
    """Each stock's next results date after the model's date, from arka_data/results/dates.csv (written by results_dates.py).
    Shown beside a trade so that it is not held through an announcement by surprise. Upcoming dates are Yahoo's and can move."""
    try:
        r = pd.read_csv(Path("arka_data") / "results" / "dates.csv", usecols=["sym", "when"], dtype=str).dropna()
        end = (pd.Timestamp(asof) + pd.Timedelta(days=ahead)).strftime("%Y-%m-%d"); r = r[(r["when"].str.slice(0, 10) > asof) & (r["when"].str.slice(0, 10) <= end)]
        return {s: w[:16] for s, w in r.sort_values("when").groupby("sym")["when"].first().items()}
    except Exception as e:                            # no file yet or an unreadable one: the trades are shown without the date
        print(f"  results dates not used: {type(e).__name__}: {str(e)[:120]}"); return {}


def facts(r):
    bu = "long build-up" if r.r1 > 0 and r.oi1 > 0 else "short covering" if r.r1 > 0 else "short build-up" if r.oi1 > 0 else "long unwinding"
    return {"strength": f_(r.x_m12 * 100, 0), "d200": f_(r.d200 * 100, 1), "r5": f_(r.r5 * 100, 1), "r21": f_(r.r21 * 100, 1), "oi5": f_(r.oi5 * 100, 1), "buildup": bu, "ivpct": f_(r.ivpct * 100, 0), "iv": f_(r.iv * 100, 1),
            "pcr": f_(r.pcr, 2), "deliv": f_(r.dshare * 100, 0), "volx": f_(r.volx, 2), "hi252": f_(r.hi252 * 100, 1), "atrp": f_(r.atrp * 100, 2), "sector": SECTOR.get(r.sym, "")}


def calibrate(D, days, t0):
    """Win probability for each signal: expected result (stock score + market reading) -> share of such trades that won, learned
    by isotonic regression on EARLIER out-of-sample scores only, refitted every STEP sessions."""
    for side, K in SIDE.items():
        e = (D[K["score"]] + D[K["g"]]).to_numpy(); won = (D[K["y"]] > 0).to_numpy().astype(float); okv = (D[K["score"]].notna() & D[K["g"]].notna()).to_numpy(); lab = D[K["y"]].notna().to_numpy(); t = D.t.to_numpy(); p = np.full(len(D), np.nan); n = np.zeros(len(D))
        pg = MAX_HOLD + 1 if K["y"] == "yk" else PURGE      # a keep-rule trade can run MAX_HOLD sessions: only learn from trades that had ended before the scored days
        for a in range(t0 + 250, len(days), STEP):
            tr = okv & lab & (t >= t0) & (t <= a - pg - 1); te = okv & (t >= a) & (t < a + STEP)
            if tr.sum() < 5000 or not te.any(): continue
            p[te] = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds="clip").fit(e[tr], won[tr]).predict(e[te]); n[te] = tr.sum()
        D["p_" + side] = p; D["pn_" + side] = n
    return D


def forward_days():
    try:
        return int(pd.read_csv(OUT / "log.csv").query("side == 'long'").date.nunique())
    except Exception:
        return 0


DAY_N, DAY_COST, DAY_STOP, DAY_TGT = 10, 0.0016, 0.75, 2.0   # the day book: names a day, cost of one round trip (sell and buy back), stop and target in ATRs from the open
# Stage 37: stop 0.75 ATR and target 2 ATR, chosen on 2019-24 daily bars and the first 60% of five-minute days only (stage 36: stop 1 ATR, no target).
# On daily bars the stop is assumed first when both are inside the day's range; five-minute bars resolve the order (stops placed at 9:20).
DAY_X = ("i1", "i5", "i20", "n1", "n5", "n20", "up20", "dn20", "oh20")      # the day model's own readings: open-to-close and overnight returns over 1, 5 and 20 sessions, the usual rise and fall from the open, how often the open was the day's high


def day_frame(D, P):
    """Next session's open, high, low and close for every row, and the day model's readings (all known at the signal close)."""
    pidx = P["C"].index.to_numpy(); pos = pd.Series(np.arange(len(pidx)), index=pidx); n = len(pidx); pp = pos.reindex(D.date.to_numpy()).to_numpy().astype(int); new = {k: np.full(len(D), np.nan) for k in ("o1x", "h1x", "l1x", "c1x") + DAY_X}
    for sym, idx in D.groupby("sym").indices.items():
        o, h, l, c = (P[k][sym].to_numpy(float) for k in "OHLC"); j = np.minimum(pp[idx] + 1, n - 1); ok = pp[idx] + 1 < n
        for k, a in (("o1x", o), ("h1x", h), ("l1x", l), ("c1x", c)): new[k][idx] = np.where(ok, a[j], np.nan)
        idr = pd.Series(c / o - 1); ovn = pd.Series(np.r_[np.nan, o[1:] / c[:-1] - 1]); up = pd.Series(h / o - 1); dn = pd.Series(1 - l / o); oh = pd.Series((o >= h * 0.999).astype(float))
        for k, v in (("i1", idr), ("i5", idr.rolling(5).mean()), ("i20", idr.rolling(20).mean()), ("n1", ovn), ("n5", ovn.rolling(5).mean()), ("n20", ovn.rolling(20).mean()), ("up20", up.rolling(20).mean()), ("dn20", dn.rolling(20).mean()), ("oh20", oh.rolling(20).mean())): new[k][idx] = v.to_numpy()[pp[idx]]
    return pd.DataFrame(new, index=D.index)


def walk_day(D, F, say=print):
    """A third model, for the day book only: it learns the next session's open-to-close fall (against that day's average stock),
    walk-forward on the same calendar as the other two. Scored only on sessions after its training data."""
    X = [c for c in D.columns if c not in NOT_X and c not in ("score", "score_s")]; W = pd.concat([D[X], F[list(DAY_X)]], axis=1); X = list(W.columns); yd = -(F.c1x / F.o1x - 1); y = yd - yd.groupby(D.date).transform("mean")
    days = np.array(sorted(D.date.unique())); i0 = int(np.searchsorted(days, np.datetime64(FIRST))); rng = np.random.default_rng(3); out = pd.Series(np.nan, index=D.index)
    for a in range(i0, len(days), STEP):
        te = ((D.date >= days[a]) & (D.date <= days[min(a + STEP, len(days)) - 1])).to_numpy(); past = np.flatnonzero((D.date <= days[a - 3]).to_numpy() & y.notna().to_numpy())      # the target is known one session later, so two sessions are left out before the test block
        if len(past) > MAX_TRAIN: past = np.sort(rng.choice(past, MAX_TRAIN, replace=False))
        Xtr = W.iloc[past]; use = [c for c in X if Xtr[c].notna().sum() >= 200 and Xtr[c].nunique() > 1]; p = np.zeros(int(te.sum()))
        for sd in SEEDS: p += HistGradientBoostingRegressor(**GB, random_state=sd).fit(Xtr[use], y.iloc[past]).predict(W.loc[te, use])
        out[te] = p / len(SEEDS)
    say(f"  day model: {len(X)} readings, scored {str(days[i0])[:10]} onward"); return out


def day_rank(S):
    """Rank for the day book inside one frame of rows (columns t, sym, sc, sd): the 5-session short score and the day model's score, each as
    a percentile of that day, added. Then at most SECTOR_CAP names from one sector among the names taken."""
    S = S.copy(); S["bl"] = S.groupby("t").sc.rank(pct=True) + S.groupby("t").sd.rank(pct=True).fillna(S.groupby("t").sc.rank(pct=True)); S["r0"] = S.groupby("t").bl.rank(ascending=False, method="first")
    S = S.sort_values(["t", "r0"]); S["sec"] = [SECTOR.get(x) or x for x in S.sym]; S["ns"] = S.groupby(["t", "sec"]).cumcount(); S["rk"] = np.where(S.ns < SECTOR_CAP, 1.0, np.nan); S["rk"] = S.groupby("t").rk.cumsum().where(S.ns < SECTOR_CAP)
    return S


def day_exit(o, h, l, c, atr, side, stop, tgt):
    """One intraday trade on one day's bar, from the open: side -1 sells short, +1 buys. Stop and target in ATRs from the open; when both
    are inside the day's range the stop is assumed first. For the shorts' 0.75 and 2 ATR that is rare (five-minute bars agree); for the longs'
    tight 0.5 and 0.5 ATR bracket it happens often, so their daily result is a pessimistic bound and five-minute prices decide."""
    o, h, l, c, atr = (np.asarray(v, float) for v in (o, h, l, c, atr))
    if side < 0:
        sh = h >= o + stop * atr; th = (l <= o - tgt * atr) if tgt else np.zeros(len(o), bool)
        return np.where(sh, -stop * atr / o, np.where(th, tgt * atr / o, -(c / o - 1)))
    sh = l <= o - stop * atr; th = (h >= o + tgt * atr) if tgt else np.zeros(len(o), bool)
    return np.where(sh, -stop * atr / o, np.where(th, tgt * atr / o, c / o - 1))


def audit_stats(r, t, tmin=None):
    """What the 60-session paper check compares against: per-trade and per-session (equal-weight average of that session's trades) mean,
    spread and win rate from the backtest, and a bootstrap of the session average over n sessions (1st and 5th percentiles)."""
    r = np.asarray(r, float); t = np.asarray(t); ok = np.isfinite(r) & ((t >= tmin) if tmin is not None else True); r, t = r[ok], t[ok]
    if len(r) < 30: return None
    sess = pd.Series(r).groupby(t).mean().to_numpy(); rng = np.random.default_rng(5); cone = {}
    for n in (10, 20, 30, 40, 60):
        mm = rng.choice(sess, size=(2000, n), replace=True).mean(axis=1); cone[str(n)] = [round(float(np.percentile(mm, 1)) * 100, 3), round(float(np.percentile(mm, 5)) * 100, 3)]
    return {"trade": {"n": int(len(r)), "mean": round(float(r.mean()) * 100, 3), "sd": round(float(r.std(ddof=1)) * 100, 3), "win": round(float((r > 0).mean()) * 100, 1)},
            "session": {"n": int(len(sess)), "mean": round(float(sess.mean()) * 100, 3), "sd": round(float(sess.std(ddof=1)) * 100, 3)}, "cone": cone}


def day_book(D, F, days, t0, tu, call=None):
    """The day book. Research on the 10 years showed where the short model's edge sits: its picks fall between the open and the close and
    rise again overnight ("tug of war", Lou, Polk and Skouras 2019). So this book sells the DAY_N best ranks at the next open and buys
    them back at that day's close: no position is held overnight. Every day, no gauge, no regime rule. Returns the record and the history of picks."""
    S = pd.DataFrame({"t": D.t.to_numpy(), "sym": D.sym.to_numpy(), "sc": D.score_s.to_numpy(), "sd": D.score_d.to_numpy(), "atr": D.atr.to_numpy(), "o": F.o1x.to_numpy(), "h": F.h1x.to_numpy(), "l": F.l1x.to_numpy(), "c": F.c1x.to_numpy()}); S = day_rank(S[(S.t >= t0) & S.sc.notna()])
    hist = S[S.rk <= 2 * DAY_N]; T = S[(S.rk <= DAY_N) & (S.o > 0) & (S.c > 0)].copy()
    T["gross"] = -(T.c / T.o - 1); hit = T.h >= T.o + DAY_STOP * T.atr; thit = ~hit & (T.l <= T.o - DAY_TGT * T.atr); T["r"] = day_exit(T.o, T.h, T.l, T.c, T.atr, -1, DAY_STOP, DAY_TGT); span = np.arange(t0 + 1, len(days))
    cs_ = np.full(len(days), np.nan) if call is None else np.asarray(call, float); size = lambda tt: np.where(cs_[np.asarray(tt, int)] == 1, DAY_BULL_SIZE, 1.0)      # half size the day after a BULLISH NIFTY call
    def daily(cost, col="r", frame=T, sized=True):
        w = size(frame.t) if sized else 1.0
        g = ((frame[col] - cost) * w).groupby(frame.t).sum() / DAY_N; g.index = g.index + 1; return g.reindex(span).fillna(0).to_numpy()
    x = daily(DAY_COST); x_full = daily(DAY_COST, sized=False); a = span < tu; curve = np.concatenate([[1.0], np.cumprod(1 + x)]); cut = int(a.sum()) + 1
    cspan = cs_[span - 1]; by_call = {nm_: {lab: f_(x_full[m & (cspan == sv)].mean() * 100, 3) for lab, sv in (("bull", 1), ("chop", 0), ("bear", -1))} for nm_, m in (("design", a), ("unseen", ~a))}
    part = lambda v: {"days": int(len(v)), "won": f_((v > 0).mean() * 100, 0), "avg": f_(v.mean() * 100, 3)} if len(v) else None
    R = S[S.o.notna() & S.c.notna()].copy(); R["rk2"] = R.groupby("t").sc.transform(lambda v: np.random.default_rng(len(v) + int(v.index[0])).permutation(len(v)) + 1); R = R[R.rk2 <= DAY_N]; R["r"] = day_exit(R.o, R.h, R.l, R.c, R.atr, -1, DAY_STOP, DAY_TGT); xr = daily(DAY_COST, "r", R)
    old = S[S.o.notna() & S.c.notna()].copy(); old["rk3"] = old.groupby("t").sc.rank(ascending=False, method="first"); old = old[old.rk3 <= DAY_N]; old["r"] = day_exit(old.o, old.h, old.l, old.c, old.atr, -1, DAY_STOP, DAY_TGT); xo = daily(DAY_COST, "r", old)
    eq = lambda v: np.concatenate([[1.0], np.cumprod(1 + v)]); yr = pd.to_datetime(days[span]).year; nn = len(curve); step = max(1, nn // 300); pick = list(range(0, nn, step)); pick = pick if pick[-1] == nn - 1 else pick + [nn - 1]
    two = lambda v: {"design": cstats(eq(v[a]))["cagr"], "unseen": cstats(eq(v[~a]))["cagr"] if (~a).sum() > 20 else None}
    cf = np.concatenate([[1.0], np.cumprod(1 + x_full)]); tr_ = T.r - DAY_COST; td = (T.t + 1 < tu).to_numpy()
    trades = {nm_: {"n": int(m.sum()), "avg": f_(tr_[m].mean() * 100, 3), "sd": f_(tr_[m].std() * 100, 3), "won": f_((tr_[m] > 0).mean() * 100, 0)} if m.any() else None for nm_, m in (("design", td), ("unseen", ~td))}
    out = {"rules": {"n": DAY_N, "cost": DAY_COST * 100, "stop_atr": DAY_STOP, "target_atr": DAY_TGT, "sector_cap": SECTOR_CAP, "bull_size": DAY_BULL_SIZE},
           "audit": audit_stats(tr_.to_numpy(), (T.t + 1).to_numpy(), tu), "targets_hit": f_(thit.mean() * 100, 1), "record": {"design": cstats(curve[:cut]), "unseen": cstats(curve[cut - 1:]) if (~a).sum() > 20 else None, "all": cstats(curve)},
           "record_full": {"design": cstats(cf[:cut]), "unseen": cstats(cf[cut - 1:]) if (~a).sum() > 20 else None}, "by_call": by_call, "trades": trades,
           "daily": {"design": part(x[a]), "unseen": part(x[~a])}, "gross": {"design": f_(T[T.t < tu - 1].gross.mean() * 100, 3), "unseen": f_(T[T.t >= tu - 1].gross.mean() * 100, 3)}, "stopped": f_(hit.mean() * 100, 0),
           "costs": [{"cost": c * 100, **two(daily(c))} for c in (0.0010, 0.0016, 0.0020, 0.0030, 0.0040)], "random": two(xr), "short_score_only": two(xo),
           "ranks": [{"from": lo, "to": hi, "design": f_(-(v[v.t < tu - 1].c / v[v.t < tu - 1].o - 1).mean() * 100, 3), "unseen": f_(-(v[v.t >= tu - 1].c / v[v.t >= tu - 1].o - 1).mean() * 100, 3)} for lo, hi in ((1, 5), (6, 10), (11, 20), (21, 40), (41, 80), (81, 999)) for v in [S[(S.r0 >= lo) & (S.r0 <= hi) & (S.o > 0)]]],
           "years": [{"year": int(y), "unseen": str(y) >= UNSEEN[:4], "ret": f_((np.prod(1 + x[yr == y]) - 1) * 100, 1), "maxdd": f_(float((eq(x[yr == y]) / np.maximum.accumulate(eq(x[yr == y])) - 1).min()) * 100, 1), "won": f_((x[yr == y] > 0).mean() * 100, 0)} for y in sorted(set(yr))],
           "curve": {"d": [str(days[t0 + i])[:10] for i in pick], "model": [round(float(curve[i]) * 100, 2) for i in pick], "random": [round(float(eq(xr)[i]) * 100, 2) for i in pick]}}
    hist = hist.assign(date=[str(days[t])[:10] for t in hist.t], rank=hist.rk.astype(int), atr=hist.atr.round(4))[["date", "sym", "rank", "atr"]].sort_values(["date", "rank"]); out["_curve"] = curve      # atr: the lab sets the same stops and targets
    return out, hist


# ───────────── the evening NIFTY call, intraday longs, exact levels ─────────────
NCALL_FIRST, NCALL_HI, NCALL_LO = "2018-07-01", 0.70, 0.30   # the call: tonight's prediction against its own last 250; top 30% BULLISH, bottom 30% BEARISH, the rest CHOP
DAY_BULL_SIZE = 0.5                                   # intraday shorts at half size after a BULLISH call: in 2019-24 and 2025-26 the short book earned far less on those days
DAYL_N, DAYL_GAP, DAYL_MAX = 40, 0.01, 10             # intraday longs: a pre-open buy limit 1% under the close on the 40 best long ranks; at most 10 fills a day
DAYL_STOP, DAYL_TGT = 0.5, 0.5                        # stage 37: their stop and target in ATRs from the fill (paper only: they lose on five-minute prices since Aug 2025)
NC_LABEL = {  # reading -> (words, unit): only these are named as reasons for the call
    "ni_r1": ("NIFTY's move today", "pct"), "ni_r5": ("NIFTY's move this week", "pct"), "ni_r21": ("NIFTY's move this month", "pct"), "ni_d50": ("NIFTY against its 50-day average", "pct"),
    "ni_d200": ("NIFTY against its 200-day average", "pct"), "nc_rsi2": ("2-day RSI", "num0"), "nc_cl0": ("where NIFTY closed in today's range (0 low, 100 high)", "pct0"),
    "nc_gap0": ("today's opening gap", "pct"), "nc_id0": ("NIFTY's move from today's open", "pct"), "nc_rng0": ("today's range in ATRs", "num"), "nc_vix": ("India VIX", "num"),
    "nc_vix1": ("India VIX change today", "num"), "nc_vix5": ("India VIX change this week", "num"), "nc_vixpct": ("India VIX against its year", "pct0"), "nc_streak": ("days up (+) or down (-) in a row", "sint"),
    "nc_hi20": ("distance below the 20-day high", "pct"), "nc_lo20": ("distance above the 20-day low", "pct"), "nc_bkrel1": ("BANK NIFTY against NIFTY today", "pct"),
    "fi_ils": ("FII long share of index futures", "pct0"), "fi_ils1": ("FII long share, change today", "pts"), "fi_ils5": ("FII long share, change this week", "pts"),
    "cl_ils": ("retail long share of index futures", "pct0"), "cl_ils1": ("retail long share, change today", "pts"), "cl_ils5": ("retail long share, change this week", "pts"),
    "pr_ils": ("prop traders' long share of index futures", "pct0"), "di_ils": ("DII long share of index futures", "pct0"), "fi_opt": ("FII option positioning (+ bullish)", "num"),
    "fi_opt1": ("FII option positioning, change today", "num"), "cl_opt": ("retail option positioning (+ bullish)", "num"), "ni_pcr": ("NIFTY put-call ratio", "num"),
    "ni_pcr1": ("put-call ratio change today", "num"), "ni_pcrz": ("put-call ratio against its 60 days (z)", "num"), "ni_wpcr": ("weekly-expiry put-call ratio", "num"),
    "ni_iv": ("NIFTY implied volatility", "pct"), "ni_iv1": ("implied volatility change today", "pct"), "ni_ivpct": ("implied volatility against its 120 days", "pct0"),
    "ni_skew": ("put minus call implied volatility", "pct"), "ni_cw": ("distance to the call wall", "pct"), "ni_pw": ("distance to the put wall", "pct"), "ni_wmp": ("distance to weekly max pain", "pct"),
    "ni_basis": ("futures premium (30-day)", "pct"), "ni_oi1": ("NIFTY futures open interest, change today", "pct"), "ni_oi5": ("NIFTY futures open interest, change this week", "pct"),
    "br_up": ("share of F&O stocks up today", "pct0"), "br_d50": ("share of F&O stocks above their 50-day average", "pct0"), "br_d50_5": ("that share, change this week", "pts"),
    "br_d200": ("share above their 200-day average", "pct0"), "br_longbu": ("share of stocks with long build-up", "pct0"), "br_shortbu": ("share of stocks with short build-up", "pct0"),
    "ni_ivrv": ("implied against realised volatility", "num")}


LOTS, BAN, BAN_FOR, EXPS = {}, set(), None, {}
NSE_HOLIDAYS = {"2026-12-25"}       # weekday exchange holidays still ahead (NSE circular of Dec 2025); add the 2027 list when NSE publishes it


def sessions_after(d, n):
    """The date n NSE sessions after day d (weekends and the known holidays skipped)."""
    x = pd.Timestamp(d)
    for _ in range(n):
        x += pd.offsets.BDay(1)
        while str(x)[:10] in NSE_HOLIDAYS: x += pd.offsets.BDay(1)
    return x


def fut_contract(sym, asof, hold):
    """Which stock future to sell for a swing short opened at the next session and bought back on session `hold`: this month's
    contract, or next month's when this month's expires on or before the last session (no roll-over inside a trade)."""
    e = EXPS.get(sym)
    if not e: return None
    ex = pd.Timestamp(e); last = sessions_after(asof, hold); nxt = ex + pd.offsets.MonthBegin(1)
    return {"month": f"{ex:%b}" if ex > last else f"{nxt:%b}", "expiry": e if ex > last else None, "which": "this month" if ex > last else "next month"}


def fno_extras(asof=None):
    """Futures lot sizes (latest arka_data/fno/u file) and the F&O ban list for the next session (arka_data/fno/meta.json, written by
    fno_data.py from NSE's fo_secban.csv). A ban list dated on or before the model's day is old and is ignored."""
    lots, ban, ban_for = {}, set(), None
    global EXPS
    try:
        f = sorted((FNO / "u").glob("*.csv"))[-1]; u = pd.read_csv(f, usecols=["date", "sym", "kind", "lot", "m_exp"]); u = u[(u.kind == "STK") & (u.date == u.date.max()) & (u.lot > 0)]
        lots = {str(k): int(v) for k, v in zip(u.sym, u.lot)}; EXPS = {str(k): str(v)[:10] for k, v in zip(u.sym, u.m_exp) if v == v}
    except Exception:
        pass
    try:
        m = json.loads((FNO / "meta.json").read_text()); bf = pd.to_datetime(m.get("ban_for"), format="%d-%b-%Y", errors="coerce")
        if bf == bf and (asof is None or bf > pd.Timestamp(asof)): ban, ban_for = {str(x) for x in (m.get("ban") or [])}, str(bf.date())
    except Exception:
        pass
    return lots, ban, ban_for


def tick_of(px):
    """NSE tick size for a cash-market price (and stock futures) since 15 Apr 2025."""
    return 0.01 if px < 250 else 0.05 if px <= 1000 else 0.10 if px <= 5000 else 0.50 if px <= 10000 else 1.0 if px <= 20000 else 5.0


def to_tick(px, down=True):
    """A price on the exchange's tick grid. NSE fixes each share's tick from a month-end close, so the tick of a price 20% higher is used:
    a coarser tick is always a whole multiple of the finer one, so the price stays valid after a fall of up to a sixth since month end."""
    if px is None or not (px == px) or px <= 0:
        return None
    t = tick_of(px * 1.2); q = math.floor(px / t + 1e-9) if down else math.ceil(px / t - 1e-9); return round(q * t, 2)


def nifty_frame(P, M):
    """NIFTY's own end-of-day readings (open, high, low, close, VIX) beside the market frame, and the next session's open-to-close move."""
    I = P["IDX"]; n = I["NIFTY"].reindex(M.index); O, H, L, C = (n[k].astype(float) for k in ("Open", "High", "Low", "Close"))
    flat = (H == L) & (H == C); O, H, L = O.mask(flat), H.mask(flat), L.mask(flat)      # a day patched from the F&O close only has no real open, high or low
    C = C.fillna(M["ni_c"]); pc = C.shift(1); tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()], axis=1).max(axis=1); atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    F = pd.DataFrame(index=M.index); F["nc_gap0"], F["nc_id0"], F["nc_cl0"], F["nc_rng0"], F["nc_atrp"] = O / pc - 1, C / O - 1, (C - L) / (H - L).replace(0, np.nan), (H - L) / atr, atr / C
    d = C.diff(); up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean(); F["nc_rsi2"] = 100 - 100 / (1 + up / dn)
    s = np.sign(C.diff()).fillna(0); F["nc_streak"] = (s.groupby((s != s.shift()).cumsum()).cumcount() + 1) * s
    F["nc_hi20"], F["nc_lo20"] = C / H.rolling(20, min_periods=10).max() - 1, C / L.rolling(20, min_periods=10).min() - 1
    vx = I["VIX"]["Close"].reindex(M.index).astype(float) if "VIX" in I else pd.Series(np.nan, index=M.index)
    F["nc_vix"], F["nc_vix1"], F["nc_vix5"], F["nc_vixpct"] = vx, vx - vx.shift(1), vx - vx.shift(5), vx.rolling(250, min_periods=100).rank(pct=True)
    F["nc_dow"] = M.index.dayofweek; b = I["BANKNIFTY"]["Close"].reindex(M.index).astype(float) if "BANKNIFTY" in I else pd.Series(np.nan, index=M.index); F["nc_bkrel1"] = b / b.shift(1) - C / pc
    Y = C.shift(-1) / O.shift(-1) - 1
    return F, Y, pd.DataFrame({"O": O, "H": H, "L": L, "C": C, "atr": atr})


def walk_ncall(M, F, Y):
    """The call model: a heavily regularised logistic regression on every market reading, refitted every 21 sessions on the sessions
    whose next-day result was already known (2 purged), scored only on the sessions after. Returns the score, the last fit and its readings."""
    X = M.drop(columns=[c for c in M.columns if c.startswith("t_")] + ["ni_c", "ba_c", "regime"], errors="ignore").select_dtypes("number"); W = pd.concat([X, F], axis=1)
    days = M.index; i0 = int(days.searchsorted(pd.Timestamp(NCALL_FIRST))); p = pd.Series(np.nan, index=days); fit, use = None, []
    for a in range(i0, len(days), 21):
        y = Y.iloc[:a - 2]; ok = y.notna().to_numpy(); tr = W.iloc[:a - 2][ok]
        use = [c for c in W.columns if tr[c].notna().sum() >= 120 and tr[c].nunique() > 1]
        fit = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.02, max_iter=500)).fit(tr[use], (y[ok] > 0).astype(int))
        p.iloc[a:a + 21] = fit.predict_proba(W.iloc[a:a + 21][use])[:, 1] - 0.5
    return p, fit, use, W


def ncall_of(pct):
    return "BULLISH" if pct >= NCALL_HI else "BEARISH" if pct <= NCALL_LO else "CHOP"


def nc_fmt(v, unit):
    if v is None or v != v: return "n/a"
    return {"pct": f"{v * 100:+.2f}%", "pct0": f"{v * 100:.0f}%", "pts": f"{v * 100:+.1f} points", "num": f"{v:.2f}", "num0": f"{v:.0f}", "sint": f"{v:+.0f}"}.get(unit, f"{v:.2f}")


def nifty_call(P, M, days, t0, tu, last_full):
    """The evening call for NIFTY's next session: BULLISH, BEARISH or CHOP, its reasons, the levels and its walk-forward record."""
    F, Y, N = nifty_frame(P, M); p, fit, use, W = walk_ncall(M, F, Y)
    pct = p.rolling(250, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    state = pd.Series(np.where(pct >= NCALL_HI, 1, np.where(pct <= NCALL_LO, -1, 0)), index=p.index).where(pct.notna())
    x = W.iloc[[-1]][use]; z = fit[1].transform(fit[0].transform(x))[0]; co = fit[2].coef_[0]; contrib = pd.Series(co * z, index=use)
    named = contrib[[c for c in use if c in NC_LABEL]].sort_values(); raw = W.iloc[-1]
    rs = lambda idx: [{"what": NC_LABEL[c][0], "value": nc_fmt(float(raw[c]) if raw[c] == raw[c] else None, NC_LABEL[c][1]), "weight": round(float(contrib[c]), 3)} for c in idx]
    bulls = rs([c for c in named.index[::-1] if named[c] > 0][:4]); bears = rs([c for c in named.index if named[c] < 0][:4])
    last = N.iloc[-1]; H, L, C, A = last.H, last.L, last.C, last.atr; full = H == H and L == L
    Pv = (H + L + C) / 3 if full else None; mrow = M.iloc[-1]
    lv = {"close": f_(C, 2), "high": f_(H, 2) if full else None, "low": f_(L, 2) if full else None, "atr": f_(A, 1), "pivot": f_(Pv, 1) if full else None,
          "r1": f_(2 * Pv - L, 1) if full else None, "s1": f_(2 * Pv - H, 1) if full else None, "r2": f_(Pv + (H - L), 1) if full else None, "s2": f_(Pv - (H - L), 1) if full else None,
          "call_wall": f_(C * (1 + mrow.ni_cw), 0) if mrow.ni_cw == mrow.ni_cw else None, "put_wall": f_(C * (1 + mrow.ni_pw), 0) if mrow.ni_pw == mrow.ni_pw else None,
          "max_pain": f_(C * (1 + mrow.ni_wmp), 0) if mrow.ni_wmp == mrow.ni_wmp else None, "range_lo": f_(C - A, 0), "range_hi": f_(C + A, 0)}
    rec = {}
    for nm_, a, b in (("design", FIRST, UNSEEN), ("unseen", UNSEEN, "2100-01-01")):
        m = (p.index >= a) & (p.index < b) & state.notna().to_numpy() & Y.notna().to_numpy(); r = {}
        for lab, sv in (("bull", 1), ("chop", 0), ("bear", -1)):
            y = Y[m & (state == sv).to_numpy()]
            r[lab] = {"n": int(len(y)), "right": f_(((y > 0) if sv > 0 else (y < 0)).mean() * 100, 1) if len(y) and sv != 0 else None, "avg": f_(y.mean() * 100, 3) if len(y) else None, "move": f_(y.abs().mean() * 100, 3) if len(y) else None}
        yb = Y[m & (state == -1).to_numpy()]; v = -yb - 0.0007
        r["nifty_short"] = {"n": int(len(v)), "avg": f_(v.mean() * 100, 3), "t": f_(v.mean() / v.std() * math.sqrt(len(v)), 2) if len(v) > 2 else None}
        y_all = Y[(p.index >= a) & (p.index < b) & Y.notna().to_numpy()]; r["up_share"] = f_((y_all > 0).mean() * 100, 1)
        tr_up = (M.ni_d200 > 0).reindex(p.index).to_numpy()                   # stage 37 audit: bullish calls had no skill while NIFTY was above its 200-day average
        for lab, tv in (("bull_above", True), ("bull_below", False)):
            sel = m & (state == 1).to_numpy() & (tr_up == tv); yy = Y[sel]; base = Y[m & (tr_up == tv)]
            r[lab] = {"n": int(len(yy)), "right": f_((yy > 0).mean() * 100, 1) if len(yy) else None, "base": f_((base > 0).mean() * 100, 1) if len(base) else None}
        rec[nm_] = r
    hist = [{"d": str(d)[:10], "c": int(s), "next": f_(float(Y.loc[d]) * 100, 2) if Y.loc[d] == Y.loc[d] else None} for d, s in state.iloc[-30:].items() if s == s]
    fwd = {"n": 0, "calls": 0, "right": None, "need": FORWARD_DAYS}
    try:                                                                        # the calls logged each evening since this rule went live, against what NIFTY then did
        lg = pd.read_csv(OUT / "calls.csv", parse_dates=["date"]); yy = Y.reindex(lg.date); m = yy.notna().to_numpy(); c_ = lg.call.to_numpy()[m]; v = yy.to_numpy()[m]; d_ = c_ != "CHOP"
        fwd = {"n": int(m.sum()), "calls": int(d_.sum()), "right": f_(np.where(c_ == "BULLISH", v > 0, v < 0)[d_].mean() * 100, 1) if d_.any() else None, "need": FORWARD_DAYS}
    except Exception:
        pass
    st = state.reindex(pd.to_datetime(days)).to_numpy()            # by the panel's session index (t), for the day books
    call = ncall_of(float(pct.iloc[-1])) if pct.iloc[-1] == pct.iloc[-1] else "CHOP"
    trend = "above" if M.ni_d200.iloc[-1] == M.ni_d200.iloc[-1] and M.ni_d200.iloc[-1] > 0 else "below"
    return {"call": call, "pct": f_(float(pct.iloc[-1]) * 100, 0) if pct.iloc[-1] == pct.iloc[-1] else None, "asof": str(M.index[-1])[:10], "for": "next session, from the open",
            "trend": trend, "weak": bool(call == "BULLISH" and trend == "above"),
            "up": bulls, "down": bears, "levels": lv, "record": rec, "forward": fwd, "last": hist, "rules": {"hi": NCALL_HI, "lo": NCALL_LO, "first": NCALL_FIRST, "short_size_bull": DAY_BULL_SIZE}}, st


def day_long_book(D, days, t0, tu):
    """Intraday LONGS, the one shape that held in daily data: a pre-open BUY LIMIT DAYL_GAP under the close on the DAYL_N best long ranks
    (low short score, low predicted day fall). It fills at the official open only when the stock opens at or under it; stop DAYL_STOP ATR
    under the open and target DAYL_TGT ATR over it (stage 37: 0.5 and 0.5); out at the close. At most DAYL_MAX fills a day, 1/10 of the capital each. Returns the record, tonight's list and the history."""
    S = pd.DataFrame({"t": D.t.to_numpy(), "sym": D.sym.to_numpy(), "sc": D.score_s.to_numpy(), "sd": D.score_d.to_numpy(), "atr": D.atr.to_numpy(), "c0": D.c.to_numpy(),
                      "o": D.o1x.to_numpy(), "h": D.h1x.to_numpy(), "l": D.l1x.to_numpy(), "c": D.c1x.to_numpy()})
    S = S[(S.t >= t0) & S.sc.notna()].copy(); g = S.groupby("t")
    S["bl"] = g.sc.rank(pct=True, ascending=False).fillna(0.5) + g.sd.rank(pct=True, ascending=False).fillna(0.5); S["r0"] = S.groupby("t").bl.rank(ascending=False, method="first")
    span = np.arange(t0 + 1, len(days)); a = span < tu
    def fills(Z, col="r0"):
        T = Z[(Z[col] <= DAYL_N) & (Z.o > 0) & (Z.c > 0) & (Z.c0 > 0)]; T = T[T.o <= T.c0 * (1 - DAYL_GAP)].sort_values(["t", col]); return T[T.groupby("t").cumcount() < DAYL_MAX].copy()
    def res(T, cost=DAY_COST):
        return pd.Series(day_exit(T.o, T.h, T.l, T.c, T.atr, 1, DAYL_STOP, DAYL_TGT) - cost, index=T.index)
    def daily(T, r):
        v = (r * 0.1).groupby(T.t + 1).sum().reindex(span).fillna(0).to_numpy(); return v
    T = fills(S); r = res(T); x = daily(T, r); eq = lambda v: np.concatenate([[1.0], np.cumprod(1 + v)]); curve = eq(x); cut = int(a.sum()) + 1
    def per(m):
        v = r[m]; return {"n": int(len(v)), "avg": f_(v.mean() * 100, 3), "won": f_((v > 0).mean() * 100, 0), "t": f_(v.mean() / v.std() * math.sqrt(len(v)), 2) if len(v) > 2 else None} if len(v) else None
    two = lambda v: {"design": cstats(eq(v[a]))["cagr"], "unseen": cstats(eq(v[~a]))["cagr"] if (~a).sum() > 20 else None}
    rnd, rnd1 = [], []                                                          # the same rule on random picks of the day's stocks: is it the ranking or just the gap?
    shape = lambda Z: pd.Series(day_exit(Z.o, Z.h, Z.l, Z.c, Z.atr, 1, 1.0, 0) - DAY_COST, index=Z.index)      # the stage 36 exit: stop 1 ATR, no target
    for sd_ in range(8):
        rng = np.random.default_rng(17 + sd_); R = S.copy(); R["rr"] = R.groupby("t").sc.transform(lambda v: rng.permutation(len(v)) + 1); TR = fills(R, "rr"); rnd.append(two(daily(TR, res(TR)))); rnd1.append(two(daily(TR, shape(TR))))
    out = {"rules": {"n": DAYL_N, "gap": DAYL_GAP * 100, "max": DAYL_MAX, "stop_atr": DAYL_STOP, "target_atr": DAYL_TGT, "cost": DAY_COST * 100}, "audit": audit_stats(r.to_numpy(), (T.t + 1).to_numpy(), tu),
           "record": {"design": cstats(curve[:cut]), "unseen": cstats(curve[cut - 1:]) if (~a).sum() > 20 else None, "all": cstats(curve)},
           "trades": {"design": per((T.t + 1 < tu).to_numpy()), "unseen": per((T.t + 1 >= tu).to_numpy())}, "fills_a_day": f_(len(T) / max(1, len(span)), 2),
           "random": {k: f_(float(np.median([q[k] for q in rnd if q[k] is not None])), 1) if any(q[k] is not None for q in rnd) else None for k in ("design", "unseen")},
           "costs": [{"cost": round(c * 100, 2), **two(daily(T, res(T, c)))} for c in (0.0016, 0.0025, 0.0035)],
           # daily bars cannot order a 0.5 ATR stop and a 0.5 ATR target touched on the same day, and count the stop: a pessimistic bound. The shape itself, with
           # the stage 36 exit (stop 1 ATR, no target), and the share of such two-sided days, so the page can say both. Five-minute prices decide (intraday_lab.py).
           "shape": two(daily(T, shape(T))),
           "random_shape": {k: f_(float(np.median([q[k] for q in rnd1 if q[k] is not None])), 1) if any(q[k] is not None for q in rnd1) else None for k in ("design", "unseen")},
           "both_touched": f_(float(((T.l <= T.o - DAYL_STOP * T.atr) & (T.h >= T.o + DAYL_TGT * T.atr)).mean()) * 100, 1) if len(T) else None}
    last_t = int(S.t.max()); tonight = S[(S.t == last_t) & (S.r0 <= DAYL_N)].sort_values("r0")
    picks = [{"sym": q.sym, "rank": int(q.r0), "close": f_(q.c0), "limit": to_tick(q.c0 * (1 - DAYL_GAP)), "atr": f_(q.atr), "stop_at_limit": to_tick(q.c0 * (1 - DAYL_GAP) - DAYL_STOP * q.atr), "target_at_limit": to_tick(q.c0 * (1 - DAYL_GAP) + DAYL_TGT * q.atr), "sector": SECTOR.get(q.sym, "")} for q in tonight.itertuples()]
    hist = S[(S.r0 <= DAYL_N) & (S.t >= int(np.searchsorted(days, np.datetime64("2024-11-01"))))].assign(date=lambda z: [str(days[t])[:10] for t in z.t], rank=lambda z: z.r0.astype(int), limit=lambda z: (z.c0 * (1 - DAYL_GAP)).round(2), atr=lambda z: z.atr.round(4))[["date", "sym", "rank", "limit", "atr"]].sort_values(["date", "rank"])
    return out, picks, hist


def build(P, say=print):
    global CM, LOTS, BAN, BAN_FOR
    t_start = time.time(); cache = os.environ.get("ARKA_CACHE")
    if cache and os.path.exists(cache):
        D, M, miss, G, nx, nm = pickle.load(open(cache, "rb")); say("scores read from " + cache)
    else:
        D, M, miss = dataset(P, say); D["score"], D["score_s"], nx = walk_stock(D, say); G, nm = walk_market(M)
        if cache: pickle.dump((D, M, miss, G, nx, nm), open(cache, "wb"))
    D = D.reset_index(drop=True); DF = day_frame(D, P); dcache = cache + ".day" if cache else None
    if dcache and os.path.exists(dcache): sd = pickle.load(open(dcache, "rb")); say("day scores read from " + dcache)
    else:
        sd = walk_day(D, DF, say)
        if dcache: pickle.dump(sd, open(dcache, "wb"))
    D["score_d"] = np.asarray(sd); D = pd.concat([D, DF[["o1x", "h1x", "l1x", "c1x"]]], axis=1)       # the next session's prices travel with each row from here on
    for k in ("t_n1", "t_mkt", "t_mkts"): G[k] = M[k]
    M["regime"] = [regime_of(r)[0] for r in M.itertuples()]
    D = D.merge(G[["g_mkt", "g_mkts", "g_n1"]], left_on="date", right_index=True, how="left").merge(M[["regime"]], left_on="date", right_index=True, how="left")
    days = np.array(sorted(D.date.unique())); ix = {d: i for i, d in enumerate(days)}; D["t"] = D.date.map(ix); pidx = P["C"].index.to_numpy(); big = len(days) + 99
    D = pd.concat([D, keep_labels(D, P)], axis=1); say("long book: keep-rule trades labelled (top %d, at most %d sessions, stop %.1f ATR, target %.1f ATR)" % (KEEP_RANK, MAX_HOLD, STOP_K, TGT_L))
    for a, b in (("xt", "_xt"), ("xts", "_xts"), ("xtk", "_xtk")): D[b] = [int(np.searchsorted(days, pidx[x])) if 0 <= x < len(pidx) else big for x in D[a]]
    D["k"] = D.sym.astype("category").cat.codes; names = dict(zip(D.k, D.sym)); secs = {s: i for i, s in enumerate(sorted(set(SECTOR.values())))}; D["sec"] = [secs.get(SECTOR.get(s), -1) for s in D.sym]
    t0, tu, t1 = int(np.searchsorted(days, np.datetime64(FIRST))), int(np.searchsorted(days, np.datetime64(UNSEEN))), len(days); D = calibrate(D, days, t0)
    CM = D.pivot_table(index="t", columns="k", values="c").reindex(index=range(len(days)), columns=range(int(D.k.max()) + 1)).ffill().to_numpy()
    D["mom_l"], D["mom_s"] = D.x_m12, -D.x_m12; G = G.reindex(days); curves = {}; rng = np.random.default_rng(11); last_full = int(D[D.y.notna()].t.max()) + 1
    out = {"v": VERSION, "asof": str(days[-1])[:10], "built": datetime.now(SE.IST).isoformat(timespec="minutes"),
           "rules": {"stop_atr": STOP_K, "target_atr": TGT_K, "hold": HOLD, "cost": COST * 100, "slots": SLOTS, "new_sup": NEW_SUP, "new_def": NEW_DEF, "retrain": STEP, "first": FIRST, "unseen": UNSEEN, "sector_cap": SECTOR_CAP, "trials": TRIALS,
                     "long": {"stop_atr": STOP_K, "target_atr": TGT_L, "keep_rank": KEEP_RANK, "max_hold": MAX_HOLD}, "short": {"stop_atr": STOP_K, "target_atr": TGT_K, "hold": HOLD}},
           "data": {"stocks": int(D.sym.nunique()), "rows": int(len(D)), "sessions": int(len(days)), "from": str(days[0])[:10], "features": nx, "market_features": nm, "missing": miss, "seconds": None}, "books": {}}
    T = D[D.t == t1 - 1]; g = G.iloc[-1]; mrow = M.iloc[-1]; reg, why, act = regime_of(mrow); hist = G.iloc[t0:]; charts, want, marks = {}, [], {}
    LOTS, BAN, BAN_FOR = fno_extras(str(days[-1])[:10]); out["fno"] = {"ban_for": BAN_FOR, "ban": sorted(BAN), "lots": len(LOTS)}
    if BAN: say(f"F&O ban list for {BAN_FOR}: {', '.join(sorted(BAN))} (no new swing short there)")
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
        hs = D[(D.t >= t0) & (D.t < last_full) & D[K["y"]].notna() & D[K["score"]].notna()]; hs = hs[hs.groupby("t")[K["score"]].rank(ascending=False, method="first") <= 10]; outcomes = []
        for rg in REGIMES:                                  # what happened to the ten best-ranked signals a day, by the regime they were issued in
            x = hs[hs.regime == rg]
            if len(x) >= 100: outcomes.append({"regime": rg, "n": int(len(x)), "stop": round(float((x[K["why"]] == 1).mean()) * 100, 0), "target": round(float((x[K["why"]] == 2).mean()) * 100, 0), "rank": round(float((x[K["why"]] == 3).mean()) * 100, 0), "time": round(float((x[K["why"]] == 0).mean()) * 100, 0), "won": round(float((x[K["y"]] > 0).mean()) * 100, 0), "mean": f_(x[K["y"]].mean() * 100, 2)})
        Ts = T.sort_values(K["score"], ascending=False); sup = bool(g[K["g"]] > 0); held = {names[o[1]] for o in open_}; free = max(SLOTS - len(open_), 0); blocked = reg in BLOCK[side]; cap = 0 if blocked else min(NEW_SUP if sup else DEF_NEW[side], free)
        keep = side == "long"                              # the long book: keep rule (rank exit) with the TGT_L target; the short book: 2.5 ATR target, 5 sessions
        tk = TGT_L if keep else TGT_K
        def row(r, i):
            a_ = r.atr; lot = LOTS.get(r.sym); return {"sym": r.sym, "rank": int(i + 1), "close": f_(r.c), "atr": f_(a_), "stop": to_tick(r.c - sg_ * STOP_K * a_, down=sg_ > 0), "target": to_tick(r.c + sg_ * tk * a_, down=sg_ > 0) if tk else None, "stop_pct": f_(-sg_ * STOP_K * a_ / r.c * 100, 1), "target_pct": f_(sg_ * tk * a_ / r.c * 100, 1) if tk else None,
                                **({"lot": int(lot), "lot_value": f_(lot * r.c, 0), "ban": r.sym in BAN, "fut": fut_contract(r.sym, str(days[-1])[:10], HOLD)} if side == "short" and lot else {"ban": r.sym in BAN} if side == "short" else {}),
                                "prob": f_(getattr(r, "p_" + side) * 100, 0), "prob_n": int(getattr(r, "pn_" + side)), "exp": f_((getattr(r, K["score"]) + g[K["g"]]) * 100, 2), "held": r.sym in held, **facts(r)}
        ranked = [row(r, i) for i, r in enumerate(Ts.itertuples())]; picks, k, sc = [], 0, {}
        for o in open_: sc[o[7]] = sc.get(o[7], 0) + 1
        for r in ranked:
            if k >= cap: break
            s_ = secs.get(r["sector"], -1)
            if r["held"] or (s_ >= 0 and sc.get(s_, 0) >= SECTOR_CAP) or r.get("ban"): continue      # no new futures position in a stock in the F&O ban period
            picks.append(r["sym"]); sc[s_] = sc.get(s_, 0) + 1; k += 1
        pos = []
        for o in open_:
            r = D.loc[o[5]]; e = float(r.o1) if r.o1 == r.o1 else None; cnow = float(P["C"][r.sym].dropna().iloc[-1]); ref = e if e else float(r.c)
            rk_now = T.loc[T.sym == r.sym, "lrank"]; rk_now = float(rk_now.iloc[0]) if len(rk_now) and rk_now.iloc[0] == rk_now.iloc[0] else None
            pos.append({"sym": r.sym, "signal": str(r.date)[:10], "entry": f_(e), "stop": to_tick(ref - sg_ * STOP_K * r.atr, down=sg_ > 0), "target": to_tick(ref + sg_ * tk * r.atr, down=sg_ > 0) if tk else None, "atr": f_(r.atr), "price": f_(cnow), "pnl": f_(sg_ * (cnow / e - 1) * 100, 2) if e else None,
                        "days": int(t1 - 1 - o[6]), "left": int(max(0, (MAX_HOLD if keep else HOLD) - (t1 - 1 - o[6]))), "pending": e is None, "sector": SECTOR.get(r.sym, ""),
                        **({"rank_now": int(rk_now) if rk_now else None, "exit_next": bool(rk_now and rk_now > KEEP_RANK and e)} if keep else {})})
        recent = []
        for x in [x for x in trades if x[4]][-40:][::-1]:
            r = D.loc[x[5]]; recent.append({"sym": r.sym, "signal": str(r.date)[:10], "exit": str(days[min(x[6], t1 - 1)])[:10], "entry": f_(r.o1), "ret": f_(x[2] * 100, 2), "why": ["time", "stop", "target", "rank"][int(r[K["why"]])], "regime": r.regime})
        U = rec.get("unseen"); okb = bool(U and U["trades"].get("pf") and U["trades"]["pf"] >= 1.2 and U["book"]["total"] > 0 and U["random"]["beats"] >= 95 and U["book"]["total"] > U["momentum"]["total"])
        dsr = (U or {}).get("book", {}).get("dsr"); fwd = forward_days(); stage = "live" if (okb and dsr is not None and dsr >= 95 and fwd >= FORWARD_DAYS) else "forward" if okb else "test"
        why_ = "Unseen period: profit factor %s, book %s%%, 12-month-momentum book %s%%, better than %s%% of random-pick books, deflated Sharpe %s%%. Forward log: %d of %d sessions. Approval needs profit factor 1.2 or more, a positive book that beats the momentum book, 95%% of random books, a deflated Sharpe of 95%% or more, and %d sessions of signals logged before the open." % (
            (U or {}).get("trades", {}).get("pf"), (U or {}).get("book", {}).get("total"), (U or {}).get("momentum", {}).get("total"), (U or {}).get("random", {}).get("beats"), dsr, fwd, FORWARD_DAYS, FORWARD_DAYS)
        out["books"][side] = {"status": stage, "status_why": why_, "gauge": "Supportive" if sup else "Defensive", "gauge_pct": f_(float((hist[K["g"]] < g[K["g"]]).mean()) * 100, 0), "new": int(cap), "slots_free": int(free), "blocked": bool(blocked), "new_def": DEF_NEW[side],
                              "outcomes": outcomes, "picks": picks, "ranked": ranked[:25], "avoid": ranked[::-1][:15] if side == "long" else [], "positions": pos, "recent": recent, "record": rec, "years": years, "curve": curve}
        want += picks + [p["sym"] for p in pos] + [r["sym"] for r in ranked[:8]] + [r["sym"] for r in recent[:6]]
        for x in trades:
            r = D.loc[x[5]]; m = marks.setdefault(r.sym, [])
            if x[0] + 1 >= t1 or r.o1 != r.o1: continue
            m.append({"d": str(days[x[0] + 1])[:10], "k": "b" if side == "long" else "ss", "p": f_(r.o1)})
            if x[4]: m.append({"d": str(days[min(x[6], t1 - 1)])[:10], "k": "s" if side == "long" else "sc", "p": f_(r.o1 * (1 + sg_ * (x[2] + 2 * COST))), "r": f_(x[2] * 100, 2), "why": ["time", "stop", "target", "rank"][int(r[K["why"]])]})
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
    # every stock scored tonight, for the live desk: yesterday's levels, usual volume and where each model ranks it
    res = next_results(out["asof"]); rkl = {r.sym: i + 1 for i, r in enumerate(T.sort_values("score", ascending=False).itertuples())}; rks = {r.sym: i + 1 for i, r in enumerate(T.sort_values("score_s", ascending=False).itertuples())}
    out["universe"] = [{"sym": r.sym, "c": f_(r.c), "atr": f_(r.atr), "adv": f_(math.exp(r.turn) / r.c, 0) if r.turn == r.turn and r.c > 0 else None, "sec": SECTOR.get(r.sym, ""), "rl": rkl.get(r.sym), "rs": rks.get(r.sym),
                        "pl": f_(r.p_long * 100, 0), "ps": f_(r.p_short * 100, 0), "str": f_(r.x_m12 * 100, 0), "pdh": f_(r.h), "pdl": f_(r.l), "hi20": f_(r.c / (1 + r.hi20)) if r.hi20 == r.hi20 else None, "d50": f_(r.d50 * 100, 1), "oi5": f_(r.oi5 * 100, 1), **{k: v for k, v in facts(r).items() if k in ("buildup", "deliv", "ivpct", "pcr", "volx")}, **({"res": res[r.sym]} if r.sym in res else {})} for r in T.itertuples()]
    nc, nc_state = nifty_call(P, M, days, t0, tu, last_full); say(f"NIFTY call for the next session: {nc['call']} (score at its {ordinal(nc['pct']) if nc['pct'] is not None else '?'} percentile)")
    day, day_hist = day_book(D, D, days, t0, tu, call=nc_state); TS = day_rank(pd.DataFrame({"t": T.t.to_numpy(), "sym": T.sym.to_numpy(), "sc": T.score_s.to_numpy(), "sd": T.score_d.to_numpy()}, index=T.index)); TS = T.loc[TS[TS.rk <= DAY_N].sort_values("rk").index]
    day["size"] = DAY_BULL_SIZE if nc["call"] == "BULLISH" else 1.0
    day["picks"] = [{"sym": r.sym, "rank": i + 1, "close": f_(r.c), "atr": f_(r.atr), "stop_pct": f_(DAY_STOP * r.atr / r.c * 100, 1), "stop_at_close": to_tick(r.c + DAY_STOP * r.atr, down=False), "target_at_close": to_tick(r.c - DAY_TGT * r.atr, down=False), "sector": SECTOR.get(r.sym, ""), **facts(r)} for i, r in enumerate(TS.itertuples())]
    day["status"], day["status_why"] = "test", "Backtested on daily opening and closing prices, confirmed on two years of five-minute prices. Paper only until 60 sessions of the paper journal match the backtest: the whole result depends on selling at the opening price."
    dl, dl_picks, dl_hist = day_long_book(D, days, t0, tu)
    out["day_long"] = {**dl, "picks": dl_picks, "status": "test", "status_why": "The buy-limit shape worked on daily prices in 2019-24, but on five-minute prices it has lost money since August 2025, with or without a target. Paper only: the journal follows it so you can see whether it recovers."}
    out["nifty"] = nc
    cdy = day.pop("_curve"); n_ = min(len(cl), len(cdy))                # the long book and the day book on the same capital: long positions overnight, the ten shorts only inside the day
    ld = lambda a, b: 1 + (cl[a - t0:b - t0] / cl[a - t0] - 1) + (cdy[a - t0:b - t0] / cdy[a - t0] - 1); cb2 = {k: (a, min(b, t0 + n_)) for k, (a, b) in cb.items()}
    day["with_long"] = {"record": {k: {"from": str(days[a])[:10], "to": str(days[b - 1])[:10], **cstats(ld(a, b))} for k, (a, b) in cb2.items()},
                        "corr": f_(np.corrcoef(np.diff(cl[:n_]) / cl[:n_ - 1], np.diff(cdy[:n_]) / cdy[:n_ - 1])[0, 1], 2), "years": []}
    for y in sorted({str(d)[:4] for d in days[t0:last_full]}):
        a, b = max(t0, int(np.searchsorted(days, np.datetime64(y + "-01-01")))), min(last_full, t0 + n_, int(np.searchsorted(days, np.datetime64(str(int(y) + 1) + "-01-01"))))
        if b - a < 20: continue
        c = ld(a, b); day["with_long"]["years"].append({"year": y, "unseen": y >= UNSEEN[:4], "both": round(float(c[-1] - 1) * 100, 1), "maxdd": round(float((c / np.maximum.accumulate(c) - 1).min()) * 100, 1)})
    out["day"] = day; out["_day_hist"] = day_hist; out["_day_long_hist"] = dl_hist
    out["decisions"] = dec; out["decision"] = "NO TRADE" if not (L["picks"] or Sh["picks"]) else " · ".join(([f"{len(L['picks'])} BUY"] if L["picks"] else []) + ([f"{len(Sh['picks'])} SHORT"] if Sh["picks"] else []))
    for sym in list(dict.fromkeys(want))[:40]:
        d = pd.DataFrame({k: P[k][sym] for k in ("O", "H", "L", "C")}).dropna().iloc[-150:]; lo = str(d.index[0])[:10]
        charts[sym] = {"d": [str(i)[:10] for i in d.index], "o": [f_(v) for v in d.O], "h": [f_(v) for v in d.H], "l": [f_(v) for v in d.L], "c": [f_(v) for v in d.C], "m": [m for m in marks.get(sym, []) if m["d"] >= lo]}
    out["data"]["seconds"] = int(time.time() - t_start)
    return out, charts


def save(out, charts):
    OUT.mkdir(parents=True, exist_ok=True); hist = out.pop("_day_hist", None)
    if hist is not None: hist.to_csv(OUT / "day_picks.csv", index=False)       # every past day's 20 best short ranks: the intraday lab checks them on five-minute prices
    lh = out.pop("_day_long_hist", None)
    if lh is not None: lh.to_csv(OUT / "day_long_picks.csv", index=False)      # every past day's 40 best long ranks and their buy limits: the lab checks the fills on five-minute prices
    live = {"v": out["v"], "asof": out["asof"], "regime": out["market"]["regime"], "rules": {**{k: out["rules"][k] for k in ("stop_atr", "target_atr", "hold", "cost")}, "long": out["rules"].get("long"), "short": out["rules"].get("short")},
            "ranks": {u["sym"]: u["rl"] for u in out.get("universe", []) if u.get("rl")}, "universe": [u["sym"] for u in out.get("universe", [])], "picks": [], "nifty": {"call": out["nifty"]["call"], "pct": out["nifty"]["pct"], "trend": out["nifty"].get("trend"), "weak": out["nifty"].get("weak")} if out.get("nifty") else None,
            "day": {"rules": out["day"]["rules"], "size": out["day"].get("size", 1.0), "picks": [{"sym": x["sym"], "rank": x["rank"], "atr": x["atr"], "close": x["close"]} for x in out["day"]["picks"]],
                    "long_rules": (out.get("day_long") or {}).get("rules"),
                    "longs": [{"sym": x["sym"], "rank": x["rank"], "limit": x["limit"], "atr": x["atr"]} for x in (out.get("day_long") or {}).get("picks", [])][:DAYL_N]} if out.get("day") else None,
            "audit": {"day": out["day"].get("audit"), "dayl": (out.get("day_long") or {}).get("audit"), **{k: ((B_.get("record") or {}).get("unseen") or {}).get("audit") for k, B_ in out["books"].items()}},       # the unseen years (2025 on), as for the day books
            "fno": out.get("fno"), "exits_open": [p_["sym"] for p_ in out["books"]["long"]["positions"] if p_.get("exit_next")],
            "last_session": [{"sym": p_["sym"], "side": sd_} for sd_, B_ in out["books"].items() for p_ in B_["positions"] if not p_.get("pending") and p_.get("left") == 1 and not p_.get("exit_next")]}
    if out.get("nifty"): live["nifty"]["levels"] = {k: out["nifty"]["levels"].get(k) for k in ("pivot", "r1", "s1", "atr")}
    for side, B in out["books"].items():
        by = {r["sym"]: r for r in B["ranked"]}
        live["picks"] += [{"sym": x, "side": side, "rank": by[x]["rank"], "prob": by[x]["prob"], "atr": by[x]["atr"], "close": by[x]["close"], **({k: by[x][k] for k in ("lot", "lot_value", "fut") if by[x].get(k)})} for x in B["picks"] if x in by]
    (OUT / "live.json").write_text(json.dumps(SE._clean(live), separators=(",", ":"), allow_nan=False))     # the small file the Worker's journal reads every few minutes
    (OUT / "model.json").write_text(json.dumps(SE._clean(out), separators=(",", ":"), allow_nan=False)); (OUT / "charts.json").write_text(json.dumps(SE._clean(charts), separators=(",", ":"), allow_nan=False))
    log = OUT / "log.csv"; head = "date,regime,nifty,side,rank,sym,close,stop,target,prob,signal\n"; have = log.read_text() if log.exists() else head
    if not have.startswith(head): have = head                                   # v1 log had other columns
    if f"\n{out['asof']}," not in "\n" + have:
        m = out["market"]
        for side, B in out["books"].items():
            for r in B["ranked"][:10]:
                have += f"{out['asof']},{m['regime']},{'up' if m['nifty_up'] else 'down'},{side},{r['rank']},{r['sym']},{r['close']},{r['stop']},{'' if r['target'] is None else r['target']},{r['prob']},{int(r['sym'] in B['picks'])}\n"
        log.write_text(have)
    if out.get("nifty"):                                                        # every evening's NIFTY call, for its forward record
        cl = OUT / "calls.csv"; h2 = "date,call,pct,close\n"; txt = cl.read_text() if cl.exists() else h2
        if f"\n{out['asof']}," not in "\n" + txt: txt += f"{out['asof']},{out['nifty']['call']},{out['nifty']['pct']},{out['nifty']['levels']['close']}\n"
        cl.write_text(txt)


def telegram_plan(out):
    """The evening plan for the next session, sent to Telegram from the Arka Model job (GitHub secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID,
    the same ones the Daily brief uses). Sent once a model day: the late run at 23:50 does not send it again. Exact stops and targets follow at
    9:16 am from the website's timer, worked out from the real opening prices. Never sends holdings, watchlists or anything private."""
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return "Telegram secrets not set: plan not sent"
    mark = OUT / "tg_sent.txt"
    if mark.exists() and mark.read_text().strip() == out["asof"]: return "plan already sent for " + out["asof"]
    now = pd.Timestamp(datetime.now(SE.IST).replace(tzinfo=None)); want = now.normalize()
    if want.weekday() >= 5 or str(want)[:10] in NSE_HOLIDAYS or now.hour < 18:       # before 6 pm (or on a day off) the newest close is the session before
        want -= pd.offsets.BDay(1)
        while str(want)[:10] in NSE_HOLIDAYS: want -= pd.offsets.BDay(1)
    if out["asof"] != str(want)[:10]: return f"plan not sent: the data ends {out['asof']}, the newest session is {str(want)[:10]} (the late run will try again)"
    fm = lambda v: "—" if v is None else (f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.1f}" if abs(v) < 10000 else f"{v:,.0f}")     # stock prices at their tick size
    d0 = pd.Timestamp(out["asof"]); nx = d0 + pd.offsets.BDay(1)
    while str(nx)[:10] in NSE_HOLIDAYS: nx += pd.offsets.BDay(1)
    L = [f"ARKA · Plan for {nx:%a %d %b} (from the close of {d0:%a %d %b})"]
    n = out.get("nifty")
    if n:
        lv = n.get("levels") or {}; weak = n["call"] == "BULLISH" and (n.get("trend") == "above")
        L.append(f"NIFTY call: {n['call'].title()}{' (weak: NIFTY is above its 200-day average)' if weak else ''} · score at its {ordinal(n['pct'])} percentile of 250 evenings")
        pt = lambda v: "—" if v is None else f"{v:,.0f}"
        L.append(f"Levels: R1 {pt(lv.get('r1'))} · pivot {pt(lv.get('pivot'))} · S1 {pt(lv.get('s1'))} · typical day {pt(lv.get('atr'))} points. No NIFTY trade.")
    Y = out.get("day")
    if Y and Y.get("picks"):
        R = Y["rules"]; half = Y.get("size", 1) < 1
        L += ["", f"9:00-9:05 am · INTRADAY SHORTS · SELL · intraday (MIS) · market order in the pre-open · {len(Y['picks'])} stocks, each {'half of a tenth (bullish call: half size)' if half else 'a tenth'} of the intraday capital",
              f"Stop: your fill + {R['stop_atr']} ATR · target: fill - {R['target_atr']} ATR · both from 9:20 · buy back by 3:05 pm (Dhan squares off at 3:10). The F&O ban does not apply to these cash trades."]
        for x in Y["picks"]:
            L.append(f" {x['rank']:>2} {x['sym']} close {fm(x['close'])} · stop ~{fm(x.get('stop_at_close'))} (fill + {fm(x['atr'] * R['stop_atr'])}) · target ~{fm(x.get('target_at_close'))} (fill - {fm(x['atr'] * R['target_atr'])})")
    for side, B in out["books"].items():
        by = {r["sym"]: r for r in B["ranked"]}; pk = [by[s_] for s_ in B["picks"] if s_ in by]
        if not pk: continue
        rl = out["rules"][side]
        if side == "long":
            L += ["", f"9:00-9:05 am · SWING LONGS · BUY · delivery (CNC) · market order · a tenth of the swing capital each",
                  f"Stop: fill - {rl['stop_atr']} ATR · target: fill + {rl['target_atr']} ATR · sold at the next open once ranked outside the top {rl['keep_rank']} · at most {rl['max_hold']} sessions · stop re-entered every morning"]
        else:
            L += ["", f"9:00-9:05 am · SWING SHORTS · SELL · stock futures (whole lots) · market order",
                  f"Stop: fill + {rl['stop_atr']} ATR · target: fill - {rl['target_atr']} ATR · out before 3:40 pm on session {rl['hold']} · stop re-entered every morning"]
        for x in pk:
            lot = (f" · lot {x['lot']} (Rs {x['lot_value'] / 1e5:.1f} lakh)" if x.get("lot") else "") + (f" · sell the {x['fut']['month']} future" if x.get("fut") else "")
            L.append(f" {x['sym']} close {fm(x['close'])} · stop ~{fm(x['stop'])} · target ~{fm(x['target'])}{lot}")
    ex = [p_["sym"] for p_ in out["books"]["long"]["positions"] if p_.get("exit_next")]
    if ex: L += ["", "9:00-9:05 am · SELL at the open (rank fell out of the top 80): " + ", ".join(ex)]
    last = [f"{p_['sym']} ({sd_})" for sd_, B_ in out["books"].items() for p_ in B_["positions"] if not p_.get("pending") and p_.get("left") == 1 and not p_.get("exit_next")]
    if last: L += ["", "Last session tomorrow: swing longs sell in the closing auction 3:20-3:25 pm; swing shorts buy back futures before 3:40 pm: " + ", ".join(last)]
    if BAN: L.append(f"F&O ban for {BAN_FOR}: {', '.join(sorted(BAN))} (no new swing short there)")
    DL = out.get("day_long")
    if DL and DL.get("picks"): L.append(f"Intraday longs: {len(DL['picks'])} buy limits on the Intraday tab · paper only, not placed")
    L += ["", "9:15-9:20 am: place every stop and target from your own fill (exact prices follow at 9:16) and re-enter the stops of open swing trades.",
          "Paper trading until each book passes 60 journal sessions. Not investment advice."]
    text, chunks, cur = "\n".join(L), [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800: chunks.append(cur); cur = ""
        cur += line + "\n"
    chunks.append(cur)
    import urllib.request, urllib.parse
    for ch in chunks:
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=urllib.parse.urlencode({"chat_id": chat, "text": ch, "disable_web_page_preview": "true"}).encode())
        urllib.request.urlopen(req, timeout=20).read()
    mark.write_text(out["asof"]); return f"plan sent to Telegram ({len(chunks)} message{'s' if len(chunks) > 1 else ''})"


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--eod", default="eod2_data"); ap.add_argument("--no-topup", action="store_true"); a = ap.parse_args(argv)
    P = SE.build_panels(a.eod)
    if not a.no_topup:
        print("added from NSE:", SE.top_up(P, datetime.now(SE.IST).date(), SE.http_fetch) or "nothing newer")
    SE.finish(P); out, charts = build(P); save(out, charts); B = out["books"]
    try:
        print(telegram_plan(out))
    except Exception as e:                                                      # a Telegram problem never fails the model job
        print("Telegram plan not sent:", type(e).__name__, str(e)[:120].replace(os.environ.get("TELEGRAM_BOT_TOKEN") or "~", "***"))
    print(f"Arka Model v{VERSION} to {out['asof']} · regime {out['market']['regime']} · decision {out['decision']} · long {B['long']['status']} {B['long']['record'].get('unseen', {}).get('book')} · short {B['short']['status']} {B['short']['record'].get('unseen', {}).get('book')} · {out['data']['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
