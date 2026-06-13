"""
quant_analysis.py — Statistical Arbitrage Pairs Trading with Meta-Labeling
==========================================================================
Methodology:
  - Ernest Chan, "Quantitative Trading": cointegration-based pairs trading,
    half-life of mean reversion, z-score entries, stop-loss, transaction costs.
  - Marcos López de Prado, "Advances in Financial Machine Learning":
    fractional differentiation, triple-barrier labeling, meta-labeling,
    purged k-fold CV with embargo, MDA feature importance.

Pipeline:
  Data -> Pair Selection -> Primary Signal (z-score) -> Features ->
  Triple-Barrier Meta-Labels -> Purged CV Model -> Filtered/Sized Signals ->
  Backtest with costs & stops.

Dependencies: pandas, numpy, scikit-learn, statsmodels, yfinance, streamlit
"""

import numpy as np
import pandas as pd
import yfinance as yf
from itertools import combinations
from dataclasses import dataclass

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, log_loss
from sklearn.model_selection._split import _BaseKFold
from statsmodels.tsa.stattools import coint, adfuller
import statsmodels.api as sm


# ════════════════════════════════════════════════════════════
# 1. DATA INGESTION
# ════════════════════════════════════════════════════════════

def get_data(tickers: list, start: str = "2018-01-01", end: str = None) -> pd.DataFrame:
    """Download adjusted close prices, aligned and cleaned."""
    px = yf.download(tickers, start=start, end=end, auto_adjust=True,
                     progress=False)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    px = px.dropna(how="all").ffill().dropna()
    return px


# ════════════════════════════════════════════════════════════
# 2. PAIR SELECTION  (Chan: cointegration + half-life)
# ════════════════════════════════════════════════════════════

@dataclass
class Pair:
    y: str            # dependent leg
    x: str            # hedge leg
    pvalue: float     # Engle-Granger cointegration p-value
    hedge: float      # OLS hedge ratio
    half_life: float  # mean-reversion half-life (days)


def half_life_of_mean_reversion(spread: pd.Series) -> float:
    """Chan: fit OU process dS = lambda*(S - mu)dt; HL = -ln(2)/lambda."""
    s = spread.dropna()
    lag = s.shift(1).dropna()
    ret = s.diff().dropna()
    lag, ret = lag.align(ret, join="inner")
    beta = sm.OLS(ret, sm.add_constant(lag)).fit().params.iloc[1]
    if beta >= 0:
        return np.inf
    return -np.log(2) / beta


class PairSelector:
    """Engle-Granger test across all pairs; keep cointegrated, tradeable ones."""

    def __init__(self, pval_threshold=0.05, hl_range=(2, 60)):
        self.pval_threshold = pval_threshold
        self.hl_range = hl_range

    def fit(self, prices: pd.DataFrame) -> list:
        pairs = []
        logp = np.log(prices)
        for a, b in combinations(prices.columns, 2):
            score, pval, _ = coint(logp[a], logp[b])
            if pval > self.pval_threshold:
                continue
            hedge = sm.OLS(logp[a], sm.add_constant(logp[b])).fit().params.iloc[1]
            spread = logp[a] - hedge * logp[b]
            # confirm stationarity of the spread itself
            if adfuller(spread.dropna())[1] > self.pval_threshold:
                continue
            hl = half_life_of_mean_reversion(spread)
            if not (self.hl_range[0] <= hl <= self.hl_range[1]):
                continue
            pairs.append(Pair(a, b, pval, hedge, hl))
        return sorted(pairs, key=lambda p: p.pvalue)


# ════════════════════════════════════════════════════════════
# 3. PRIMARY STRATEGY  (Chan: z-score mean reversion)
# ════════════════════════════════════════════════════════════

class PrimaryStrategy:
    """
    Side generation only (meta-labeling separates side from size, AFML 3.6):
      z = (spread - rolling_mean) / rolling_std,  lookback = ~half-life
      Long spread  (long y, short hedge*x) when z < -entry_z
      Short spread (short y, long hedge*x) when z > +entry_z
    """

    def __init__(self, pair: Pair, entry_z=2.0):
        self.pair = pair
        self.entry_z = entry_z
        self.lookback = max(int(round(pair.half_life)), 5)

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        lp = np.log(prices)
        spread = lp[self.pair.y] - self.pair.hedge * lp[self.pair.x]
        mu = spread.rolling(self.lookback).mean()
        sd = spread.rolling(self.lookback).std()
        z = (spread - mu) / sd
        side = pd.Series(0, index=z.index)
        side[z < -self.entry_z] = 1     # long the spread
        side[z > self.entry_z] = -1     # short the spread
        return pd.DataFrame({"spread": spread, "z": z, "side": side})


# ════════════════════════════════════════════════════════════
# 4. FEATURES  (AFML Ch.5: fractional differentiation + regime feats)
# ════════════════════════════════════════════════════════════

def get_ffd_weights(d: float, threshold: float = 1e-4) -> np.ndarray:
    """Fixed-width window fractional differentiation weights (AFML 5.4)."""
    w, k = [1.0], 1
    while abs(w[-1]) > threshold:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
    return np.array(w[::-1])


def frac_diff_ffd(series: pd.Series, d: float = 0.4) -> pd.Series:
    """Stationary yet memory-preserving transform of a price/spread series."""
    w = get_ffd_weights(d)
    width = len(w)
    vals = series.ffill().values
    out = np.full(len(vals), np.nan)
    for i in range(width - 1, len(vals)):
        out[i] = np.dot(w, vals[i - width + 1: i + 1])
    return pd.Series(out, index=series.index)


def build_features(prices: pd.DataFrame, sig: pd.DataFrame, pair: Pair) -> pd.DataFrame:
    """Features the meta-model sees when deciding to act on a primary signal."""
    lp = np.log(prices)
    spread, z = sig["spread"], sig["z"]
    lb = max(int(round(pair.half_life)), 5)

    feats = pd.DataFrame(index=spread.index)
    feats["z"] = z
    feats["z_abs"] = z.abs()
    feats["z_chg_3"] = z.diff(3)
    feats["ffd_spread"] = frac_diff_ffd(spread, d=0.4)
    feats["spread_vol"] = spread.diff().rolling(lb).std()
    feats["vol_regime"] = (feats["spread_vol"]
                           / spread.diff().rolling(lb * 4).std())
    feats["corr"] = (lp[pair.y].diff()
                     .rolling(lb * 2).corr(lp[pair.x].diff()))
    feats["mom_y"] = lp[pair.y].diff(lb)
    feats["mom_x"] = lp[pair.x].diff(lb)
    # rolling half-life drift: is the relationship decaying?
    feats["hurst_proxy"] = (np.log(spread.rolling(lb * 2).std())
                            - np.log(spread.rolling(lb).std()))
    return feats


# ════════════════════════════════════════════════════════════
# 5. TRIPLE-BARRIER LABELING  (AFML Ch.3)
# ════════════════════════════════════════════════════════════

def daily_vol(series: pd.Series, span: int = 50) -> pd.Series:
    return series.diff().ewm(span=span).std()


def triple_barrier_labels(spread: pd.Series, events: pd.DatetimeIndex,
                          side: pd.Series, pt_mult=1.0, sl_mult=1.0,
                          max_hold=20) -> pd.DataFrame:
    """
    For each primary-signal event, walk forward until:
      profit-take barrier (pt_mult * vol), stop-loss barrier (sl_mult * vol),
      or vertical barrier (max_hold days).
    Meta-label bin: 1 if the primary signal made money, else 0.
    """
    vol = daily_vol(spread)
    rows = []
    for t0 in events:
        if t0 not in spread.index:
            continue
        v = vol.loc[t0]
        if pd.isna(v) or v <= 0:
            continue
        s = side.loc[t0]
        path = spread.loc[t0:].iloc[1:max_hold + 1]
        if path.empty:
            continue
        ret_path = (path - spread.loc[t0]) * s   # signed P&L path of spread
        pt, sl = pt_mult * v, -sl_mult * v
        hit_pt = ret_path[ret_path >= pt].index.min()
        hit_sl = ret_path[ret_path <= sl].index.min()
        t1 = min([x for x in [hit_pt, hit_sl, path.index[-1]] if x is not None])
        ret = ret_path.loc[t1]
        rows.append({"t0": t0, "t1": t1, "ret": ret,
                     "bin": int(ret > 0), "side": s})
    return pd.DataFrame(rows).set_index("t0") if rows else pd.DataFrame()


# ════════════════════════════════════════════════════════════
# 6. PURGED K-FOLD WITH EMBARGO  (AFML Ch.7)
# ════════════════════════════════════════════════════════════

class PurgedKFold(_BaseKFold):
    """
    K-Fold that purges training samples whose label intervals [t0, t1]
    overlap the test fold, plus an embargo after the test set.
    Prevents leakage from overlapping triple-barrier labels.
    """

    def __init__(self, n_splits=5, t1: pd.Series = None, pct_embargo=0.02):
        super().__init__(n_splits, shuffle=False, random_state=None)
        self.t1 = t1
        self.pct_embargo = pct_embargo

    def split(self, X, y=None, groups=None):
        indices = np.arange(X.shape[0])
        embargo = int(X.shape[0] * self.pct_embargo)
        test_starts = [(i[0], i[-1] + 1) for i in
                       np.array_split(indices, self.n_splits)]
        for st, end in test_starts:
            test_idx = indices[st:end]
            t0_test = self.t1.index[st]
            t1_test_max = self.t1.iloc[test_idx].max()
            train_mask = (
                (self.t1 < t0_test) |                     # labels end before test
                (self.t1.index > t1_test_max)             # labels start after test
            )
            # embargo: drop training samples right after the test window
            if embargo > 0 and end + embargo < len(indices):
                emb_idx = self.t1.index[end:end + embargo]
                train_mask.loc[emb_idx] = False
            train_idx = indices[train_mask.values]
            yield train_idx, test_idx


# ════════════════════════════════════════════════════════════
# 7. META-LABELING MODEL + MDA FEATURE IMPORTANCE  (AFML Ch.3, 8)
# ════════════════════════════════════════════════════════════

class MetaLabeler:
    """Secondary model: P(primary signal is profitable). Filters + sizes bets."""

    def __init__(self, n_splits=5, pct_embargo=0.02, prob_threshold=0.55):
        self.n_splits = n_splits
        self.pct_embargo = pct_embargo
        self.prob_threshold = prob_threshold
        self.model = RandomForestClassifier(
            n_estimators=400, max_depth=4, min_samples_leaf=0.05,
            max_features="sqrt", class_weight="balanced_subsample",
            random_state=42, n_jobs=-1)
        self.cv_scores_ = []

    def fit(self, X: pd.DataFrame, labels: pd.DataFrame):
        y = labels["bin"]
        cv = PurgedKFold(self.n_splits, t1=labels["t1"], pct_embargo=self.pct_embargo)
        for tr, te in cv.split(X):
            if len(tr) < 30 or y.iloc[tr].nunique() < 2:
                continue
            self.model.fit(X.iloc[tr], y.iloc[tr])
            p = self.model.predict_proba(X.iloc[te])[:, 1]
            self.cv_scores_.append(f1_score(y.iloc[te], (p > 0.5).astype(int),
                                            zero_division=0))
        self.model.fit(X, y)   # final fit on full set
        return self

    def predict_size(self, X: pd.DataFrame) -> pd.Series:
        """Bet sizing from predicted probability (AFML 10.2 simplified):
        size = 0 below threshold, else scaled (p - 0.5) * 2."""
        p = pd.Series(self.model.predict_proba(X)[:, 1], index=X.index)
        size = ((p - 0.5) * 2).clip(0, 1)
        size[p < self.prob_threshold] = 0.0
        return size


def feature_importance_mda(model, X: pd.DataFrame, labels: pd.DataFrame,
                           n_splits=5, pct_embargo=0.02) -> pd.Series:
    """Mean Decrease Accuracy: permute each feature out-of-sample,
    measure log-loss degradation under purged CV (AFML 8.3)."""
    y = labels["bin"]
    cv = PurgedKFold(n_splits, t1=labels["t1"], pct_embargo=pct_embargo)
    imp = pd.DataFrame(columns=X.columns, dtype=float)
    base = pd.Series(dtype=float)
    for i, (tr, te) in enumerate(cv.split(X)):
        if len(tr) < 30 or y.iloc[tr].nunique() < 2:
            continue
        model.fit(X.iloc[tr], y.iloc[tr])
        p = model.predict_proba(X.iloc[te])
        base.loc[i] = -log_loss(y.iloc[te], p, labels=model.classes_)
        for col in X.columns:
            Xp = X.iloc[te].copy()
            Xp[col] = np.random.permutation(Xp[col].values)
            pp = model.predict_proba(Xp)
            imp.loc[i, col] = -log_loss(y.iloc[te], pp, labels=model.classes_)
    imp = (-imp).add(base, axis=0)             # degradation per feature
    return imp.mean().sort_values(ascending=False)


# ════════════════════════════════════════════════════════════
# 8. BACKTESTER  (Chan: costs, stop-loss, dollar-neutral execution)
# ════════════════════════════════════════════════════════════

class Backtester:
    """
    Event-driven simulation of the pair:
      - dollar-neutral legs scaled by meta-model bet size
      - per-leg transaction costs in bps (commission + slippage)
      - hard stop-loss in % of allocated capital (Chan's catastrophic stop)
      - exit on z mean reversion (|z| < exit_z), stop, or max holding period
    """

    def __init__(self, cost_bps=5.0, stop_loss_pct=0.03,
                 exit_z=0.25, max_hold=20, capital=100_000):
        self.cost = cost_bps / 1e4
        self.stop = stop_loss_pct
        self.exit_z = exit_z
        self.max_hold = max_hold
        self.capital = capital

    def run(self, prices: pd.DataFrame, sig: pd.DataFrame,
            size: pd.Series, pair: Pair) -> dict:
        y_px, x_px = prices[pair.y], prices[pair.x]
        z, side = sig["z"], sig["side"]
        equity = pd.Series(self.capital, index=prices.index, dtype=float)
        pos = None
        trades = []

        for i, t in enumerate(prices.index[1:], start=1):
            equity.iloc[i] = equity.iloc[i - 1]

            # ── mark-to-market open position ──
            if pos:
                pnl = (pos["sy"] * (y_px.loc[t] - pos["py"]) * pos["qy"]
                       + pos["sx"] * (x_px.loc[t] - pos["px"]) * pos["qx"])
                pos["days"] += 1
                unreal = pnl / pos["alloc"]
                stop_hit = unreal <= -self.stop
                reverted = abs(z.loc[t]) < self.exit_z
                timed_out = pos["days"] >= self.max_hold
                if stop_hit or reverted or timed_out:
                    exit_cost = (abs(y_px.loc[t] * pos["qy"])
                                 + abs(x_px.loc[t] * pos["qx"])) * self.cost
                    equity.iloc[i] += pnl - exit_cost
                    trades.append({"entry": pos["t"], "exit": t, "pnl": pnl - exit_cost,
                                   "reason": "STOP" if stop_hit else
                                             "REVERT" if reverted else "TIME"})
                    pos = None

            # ── new entry: primary side + meta-model approval ──
            if pos is None and t in size.index and size.loc[t] > 0 and side.loc[t] != 0:
                s = side.loc[t]
                alloc = equity.iloc[i] * 0.5 * size.loc[t]   # bet-sized allocation
                qy = alloc / y_px.loc[t]
                qx = (alloc * pair.hedge) / x_px.loc[t]
                entry_cost = (alloc + alloc * pair.hedge) * self.cost
                equity.iloc[i] -= entry_cost
                pos = {"t": t, "sy": s, "sx": -s, "qy": qy, "qx": qx,
                       "py": y_px.loc[t], "px": x_px.loc[t],
                       "alloc": alloc, "days": 0}

        return self._metrics(equity, trades)

    def _metrics(self, equity: pd.Series, trades: list) -> dict:
        rets = equity.pct_change().dropna()
        sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
        dd = (equity / equity.cummax() - 1).min()
        wins = [tr for tr in trades if tr["pnl"] > 0]
        return {
            "equity": equity,
            "trades": pd.DataFrame(trades),
            "total_return_pct": (equity.iloc[-1] / equity.iloc[0] - 1) * 100,
            "sharpe": sharpe,
            "max_drawdown_pct": dd * 100,
            "n_trades": len(trades),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        }


# ════════════════════════════════════════════════════════════
# 9. FULL PIPELINE  (returns structured results; logs via callback)
# ════════════════════════════════════════════════════════════

def run_pipeline(tickers: list, start="2018-01-01", train_frac=0.7, log=print):
    """
    Run the full stat-arb meta-labeling pipeline.
    `log` is a callback(str) so the UI can stream progress. Returns a dict:
      {"status": "ok"|"error", "message": str, "pair": Pair, "results": dict,
       "cv_scores": list, "feature_importance": pd.Series}
    """
    log(f"[1/6] Downloading {len(tickers)} tickers...")
    prices = get_data(tickers, start=start)
    if prices.shape[1] < 2:
        return {"status": "error",
                "message": "Need at least 2 tickers with overlapping price history."}

    log("[2/6] Selecting cointegrated pairs (Engle-Granger + ADF + half-life)...")
    pairs = PairSelector().fit(prices.iloc[: int(len(prices) * train_frac)])
    if not pairs:
        return {"status": "error",
                "message": "No tradeable cointegrated pairs found."}
    pair = pairs[0]
    log(f"      Best pair: {pair.y}/{pair.x}  p={pair.pvalue:.4f}  "
        f"hedge={pair.hedge:.3f}  HL={pair.half_life:.1f}d")

    log("[3/6] Primary signals + features...")
    sig = PrimaryStrategy(pair).compute(prices)
    feats = build_features(prices, sig, pair)

    log("[4/6] Triple-barrier meta-labels...")
    events = sig.index[sig["side"] != 0]
    labels = triple_barrier_labels(sig["spread"], events, sig["side"])
    if labels.empty or len(labels) < 50:
        return {"status": "error",
                "message": f"Insufficient labeled events ({0 if labels.empty else len(labels)}). "
                           f"Need at least 50."}

    X = feats.loc[labels.index].dropna()
    labels = labels.loc[X.index]

    split_t = X.index[int(len(X) * train_frac)]
    X_tr, lab_tr = X[X.index < split_t], labels[labels.index < split_t]
    X_te = X[X.index >= split_t]

    log(f"[5/6] Meta-model: purged 5-fold CV w/ embargo ({len(X_tr)} train events)...")
    meta = MetaLabeler().fit(X_tr, lab_tr)
    log(f"      CV F1 scores: {[f'{s:.2f}' for s in meta.cv_scores_]}")

    fi = feature_importance_mda(
        RandomForestClassifier(n_estimators=200, max_depth=4,
                               min_samples_leaf=0.05, random_state=42, n_jobs=-1),
        X_tr, lab_tr)

    log("[6/6] Out-of-sample backtest with costs + stop-loss...")
    size = meta.predict_size(X_te)
    oos_prices = prices[prices.index >= split_t]
    oos_sig = sig[sig.index >= split_t]
    results = Backtester(cost_bps=5, stop_loss_pct=0.03).run(
        oos_prices, oos_sig, size, pair)

    return {"status": "ok", "message": "Pipeline complete.",
            "pair": pair, "results": results,
            "cv_scores": meta.cv_scores_, "feature_importance": fi}


# ════════════════════════════════════════════════════════════
# 10. STREAMLIT UI
# ════════════════════════════════════════════════════════════

DEFAULT_UNIVERSE = ("HDFCBANK.NS, ICICIBANK.NS, KOTAKBANK.NS, "
                    "AXISBANK.NS, SBIN.NS, INDUSINDBK.NS")


def render_quant_analysis():
    import streamlit as st

    st.markdown("### Statistical Arbitrage — Pairs Trading with Meta-Labeling")
    st.caption("Cointegration pair selection (Chan) + triple-barrier meta-labeling "
               "and purged CV (López de Prado). Out-of-sample backtest with costs "
               "and stop-loss.")

    with st.form("quant_form"):
        c1, c2, c3 = st.columns([3, 1, 1])
        tickers_raw = c1.text_input(
            "Tickers (comma separated, use .NS for NSE)",
            value=DEFAULT_UNIVERSE,
            help="Provide at least 2 tickers. The model hunts for the best "
                 "cointegrated pair among them.")
        start = c2.text_input("Start date", value="2018-01-01")
        train_frac = c3.slider("Train fraction", 0.5, 0.9, 0.7, 0.05)
        run = st.form_submit_button("Run Analysis", type="primary",
                                    use_container_width=True)

    if not run:
        st.info("Enter tickers and click **Run Analysis** to start. "
                "Indian banking names are pre-filled as a classic cointegration set.")
        return

    tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
    if len(tickers) < 2:
        st.error("Please enter at least 2 tickers.")
        return

    log_box = st.empty()
    logs = []

    def log(msg):
        logs.append(msg)
        log_box.code("\n".join(logs))

    with st.spinner("Running pipeline... this can take a minute on first run."):
        try:
            out = run_pipeline(tickers, start=start, train_frac=train_frac, log=log)
        except Exception as e:
            import traceback
            st.error(f"Pipeline crashed: {e}")
            st.code(traceback.format_exc())
            return

    if out["status"] != "ok":
        st.warning(out["message"])
        return

    pair = out["pair"]
    res = out["results"]

    st.success(out["message"])

    # ── Selected pair ──
    st.markdown("#### Selected Pair")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Pair", f"{pair.y} / {pair.x}")
    p2.metric("Coint p-value", f"{pair.pvalue:.4f}")
    p3.metric("Hedge ratio", f"{pair.hedge:.3f}")
    p4.metric("Half-life (days)", f"{pair.half_life:.1f}")

    # ── Out-of-sample performance ──
    st.markdown("#### Out-of-Sample Performance")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Return", f"{res['total_return_pct']:+.2f}%")
    m2.metric("Sharpe Ratio", f"{res['sharpe']:.2f}")
    m3.metric("Max Drawdown", f"{res['max_drawdown_pct']:.2f}%")
    m4.metric("Trades / Win%", f"{res['n_trades']} / {res['win_rate']:.0f}%")

    # ── Equity curve ──
    st.markdown("#### Equity Curve (OOS)")
    equity = res["equity"]
    st.line_chart(equity.rename("Equity"))

    # ── CV scores ──
    if out["cv_scores"]:
        st.markdown("#### Meta-Model CV F1 Scores")
        cv_df = pd.DataFrame({
            "Fold": [f"Fold {i+1}" for i in range(len(out["cv_scores"]))],
            "F1": [round(s, 3) for s in out["cv_scores"]],
        }).set_index("Fold")
        st.bar_chart(cv_df)

    # ── Feature importance ──
    fi = out.get("feature_importance")
    if fi is not None and len(fi):
        st.markdown("#### Feature Importance (MDA)")
        st.bar_chart(fi.rename("Importance"))

    # ── Trades ──
    st.markdown("#### Trade Log")
    trades = res["trades"]
    if isinstance(trades, pd.DataFrame) and not trades.empty:
        st.dataframe(trades, use_container_width=True)
    else:
        st.caption("No trades were generated in the out-of-sample window.")


# ════════════════════════════════════════════════════════════
# 11. CLI ENTRY POINT
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Indian banking sector — classic cointegration hunting ground (use .NS for NSE)
    UNIVERSE = ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS",
                "AXISBANK.NS", "SBIN.NS", "INDUSINDBK.NS"]
    run_pipeline(UNIVERSE)
