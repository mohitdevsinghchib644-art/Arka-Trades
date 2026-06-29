"""
╔══════════════════════════════════════════════════════════════════════╗
║           INSTITUTIONAL QUANT ANALYSIS ENGINE — EQUITIES            ║
║           Data: yfinance  |  Mode: Research & Backtesting           ║
╚══════════════════════════════════════════════════════════════════════╝

Modules:
  1. Quantitative Core & Math Engine
     - GBM / SDE Price Path Simulation
     - Monte Carlo VaR
     - Black-Scholes-Merton Option Pricing
     - ARIMA + GARCH Volatility Forecasting

  2. Market Microstructure Analytics
     - Synthetic LOB Imbalance Model
     - VWAP / TWAP Execution Engines
     - VPIN Order-Flow Toxicity

  3. Alpha Strategies
     - Statistical Arbitrage / Pairs Trading (Engle-Granger)
     - Cross-Asset Spread Monitor
     - Sentiment Score (TextBlob NLP)

  4. Portfolio Optimization & Risk
     - Markowitz Mean-Variance / Efficient Frontier
     - Black-Litterman Model

  5. Backtester
     - Signal-based P&L engine with transaction costs
     - Sharpe, Sortino, Max Drawdown reporting
"""

# ─────────────────────────────────────────────
#  DEPENDENCIES  (pip install all below)
# ─────────────────────────────────────────────
# pip install yfinance numpy pandas scipy statsmodels arch
#             matplotlib seaborn scikit-learn textblob cvxpy

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns
from scipy import stats
from scipy.optimize import minimize
from scipy.stats import norm
import yfinance as yf
from datetime import datetime, timedelta
import itertools

# ── optional heavy deps (graceful fallback) ─────────────────────────
try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.stattools import coint, adfuller
    STATSMODELS_OK = True
except ImportError:
    STATSMODELS_OK = False
    print("[WARN] statsmodels not installed — ARIMA/cointegration disabled.")

try:
    from arch import arch_model
    ARCH_OK = True
except ImportError:
    ARCH_OK = False
    print("[WARN] arch not installed — GARCH disabled.")

try:
    from textblob import TextBlob
    NLP_OK = True
except ImportError:
    NLP_OK = False
    print("[WARN] textblob not installed — NLP sentiment disabled.")

try:
    import cvxpy as cp
    CVXPY_OK = True
except ImportError:
    CVXPY_OK = False
    print("[WARN] cvxpy not installed — using scipy for portfolio opt.")


# ═══════════════════════════════════════════════════════════════════
#  MODULE 0 — DATA LAYER
# ═══════════════════════════════════════════════════════════════════

class DataLoader:
    """Pulls OHLCV equity data from Yahoo Finance via yfinance."""

    def __init__(self, tickers: list, start: str = "2020-01-01", end: str = None):
        self.tickers = tickers if isinstance(tickers, list) else [tickers]
        self.start    = start
        self.end      = end or datetime.today().strftime("%Y-%m-%d")
        self._raw: dict = {}

    def fetch(self) -> dict:
        """Returns dict of {ticker: OHLCV DataFrame}."""
        print(f"\n[DataLoader] Fetching {self.tickers} from {self.start} to {self.end} …")
        for t in self.tickers:
            df = yf.download(t, start=self.start, end=self.end,
                             auto_adjust=True, progress=False)
            df.index = pd.to_datetime(df.index)
            self._raw[t] = df
            print(f"  ✓ {t}: {len(df)} rows")
        return self._raw

    def close_prices(self) -> pd.DataFrame:
        """Returns aligned Close prices for all tickers."""
        closes = {t: self._raw[t]["Close"] for t in self._raw}
        return pd.DataFrame(closes).dropna()

    def log_returns(self) -> pd.DataFrame:
        closes = self.close_prices()
        return np.log(closes / closes.shift(1)).dropna()


# ═══════════════════════════════════════════════════════════════════
#  MODULE 1A — STOCHASTIC CALCULUS  (GBM / SDE Price Paths)
# ═══════════════════════════════════════════════════════════════════

class SDESimulator:
    """
    Geometric Brownian Motion:
        dS = μS dt + σS dW
    Simulates N paths of horizon T using daily steps.
    """

    def __init__(self, S0: float, mu: float, sigma: float,
                 T: int = 252, N: int = 1000, dt: float = 1/252):
        self.S0    = S0       # current price
        self.mu    = mu       # annualised drift  (use hist mean log-return * 252)
        self.sigma = sigma    # annualised vol    (use hist std  log-return * √252)
        self.T     = T        # time horizon in trading days
        self.N     = N        # number of paths
        self.dt    = dt       # time step (1 trading day = 1/252 year)

    def simulate(self) -> np.ndarray:
        """Returns (T, N) array of simulated price paths."""
        paths = np.zeros((self.T, self.N))
        paths[0] = self.S0
        Z = np.random.standard_normal((self.T - 1, self.N))
        for t in range(1, self.T):
            paths[t] = paths[t-1] * np.exp(
                (self.mu - 0.5 * self.sigma**2) * self.dt
                + self.sigma * np.sqrt(self.dt) * Z[t-1]
            )
        return paths

    def summary(self, paths: np.ndarray):
        final = paths[-1]
        print(f"\n[GBM Simulation] S0={self.S0:.2f}  μ={self.mu:.4f}  σ={self.sigma:.4f}")
        print(f"  Paths: {self.N}  |  Horizon: {self.T} days")
        print(f"  E[S_T]  = {final.mean():.2f}")
        print(f"  Median  = {np.median(final):.2f}")
        print(f"  5th %   = {np.percentile(final, 5):.2f}")
        print(f"  95th %  = {np.percentile(final, 95):.2f}")

    def plot(self, paths: np.ndarray, ticker: str = "Asset"):
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        fig.suptitle(f"GBM Simulation — {ticker}", fontsize=14, fontweight="bold")

        # left: path spaghetti
        axes[0].plot(paths[:, :200], alpha=0.15, linewidth=0.7, color="steelblue")
        axes[0].plot(paths.mean(axis=1), color="red", linewidth=2, label="Mean path")
        axes[0].set_title("Price Paths (first 200)")
        axes[0].set_xlabel("Trading Days")
        axes[0].set_ylabel("Price ($)")
        axes[0].legend()

        # right: terminal distribution
        axes[1].hist(paths[-1], bins=60, color="steelblue", edgecolor="white", alpha=0.8)
        axes[1].axvline(np.percentile(paths[-1], 5),  color="red",   linestyle="--", label="5th %ile")
        axes[1].axvline(np.percentile(paths[-1], 95), color="green", linestyle="--", label="95th %ile")
        axes[1].set_title("Terminal Price Distribution")
        axes[1].set_xlabel("Final Price ($)")
        axes[1].legend()

        plt.tight_layout()
        plt.show()


# ═══════════════════════════════════════════════════════════════════
#  MODULE 1B — MONTE CARLO  VaR  &  CVaR
# ═══════════════════════════════════════════════════════════════════

class MonteCarloRisk:
    """
    Uses GBM paths to compute portfolio-level:
      - Value at Risk (VaR) at confidence α
      - Conditional VaR / Expected Shortfall (CVaR)
    """

    def __init__(self, portfolio_value: float, returns: pd.Series,
                 horizon: int = 10, n_sims: int = 100_000, confidence: float = 0.99):
        self.V          = portfolio_value
        self.returns    = returns
        self.horizon    = horizon
        self.n_sims     = n_sims
        self.confidence = confidence

    def compute(self):
        mu    = self.returns.mean()
        sigma = self.returns.std()

        # Simulate h-day cumulative returns
        Z    = np.random.standard_normal((self.n_sims, self.horizon))
        r    = (mu - 0.5 * sigma**2) + sigma * Z
        cum  = np.exp(r.sum(axis=1)) - 1          # total return per sim
        pnl  = self.V * cum                        # P&L in $

        var  = np.percentile(pnl, (1 - self.confidence) * 100)
        cvar = pnl[pnl <= var].mean()

        print(f"\n[Monte Carlo VaR]  Portfolio = ${self.V:,.0f}")
        print(f"  Horizon    : {self.horizon} days")
        print(f"  Confidence : {self.confidence*100:.0f}%")
        print(f"  VaR        : ${var:,.2f}  (worst expected loss)")
        print(f"  CVaR (ES)  : ${cvar:,.2f}  (average loss beyond VaR)")
        return {"VaR": var, "CVaR": cvar, "pnl_dist": pnl}

    def plot(self, pnl: np.ndarray):
        var = np.percentile(pnl, (1 - self.confidence) * 100)
        plt.figure(figsize=(10, 5))
        plt.hist(pnl, bins=100, color="steelblue", edgecolor="white", alpha=0.7)
        plt.axvline(var, color="red", linewidth=2,
                    label=f"VaR {self.confidence*100:.0f}% = ${var:,.0f}")
        plt.title("Monte Carlo P&L Distribution")
        plt.xlabel("P&L ($)")
        plt.ylabel("Frequency")
        plt.legend()
        plt.tight_layout()
        plt.show()


# ═══════════════════════════════════════════════════════════════════
#  MODULE 1C — BLACK-SCHOLES-MERTON  Option Pricer
# ═══════════════════════════════════════════════════════════════════

class BlackScholes:
    """
    European Call / Put pricing and Greeks.
    Identifies mispriced options when market_price ≠ theoretical.
    """

    def __init__(self, S: float, K: float, T: float, r: float, sigma: float):
        """
        S     : current stock price
        K     : strike price
        T     : time to expiry in years  (e.g. 30 days → 30/252)
        r     : risk-free rate (annualised, e.g. 0.05)
        sigma : implied / historical volatility (annualised)
        """
        self.S, self.K, self.T, self.r, self.sigma = S, K, T, r, sigma
        self.d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        self.d2 = self.d1 - sigma * np.sqrt(T)

    def call_price(self) -> float:
        return (self.S * norm.cdf(self.d1)
                - self.K * np.exp(-self.r * self.T) * norm.cdf(self.d2))

    def put_price(self) -> float:
        return (self.K * np.exp(-self.r * self.T) * norm.cdf(-self.d2)
                - self.S * norm.cdf(-self.d1))

    def greeks(self) -> dict:
        delta_c =  norm.cdf(self.d1)
        delta_p = -norm.cdf(-self.d1)
        gamma   =  norm.pdf(self.d1) / (self.S * self.sigma * np.sqrt(self.T))
        vega    =  self.S * norm.pdf(self.d1) * np.sqrt(self.T) / 100
        theta_c = (-(self.S * norm.pdf(self.d1) * self.sigma) / (2 * np.sqrt(self.T))
                   - self.r * self.K * np.exp(-self.r * self.T) * norm.cdf(self.d2)) / 252
        rho_c   =  self.K * self.T * np.exp(-self.r * self.T) * norm.cdf(self.d2) / 100
        return dict(delta_call=delta_c, delta_put=delta_p,
                    gamma=gamma, vega=vega, theta_call=theta_c, rho=rho_c)

    def misprice_signal(self, market_price: float, option_type: str = "call") -> dict:
        """Returns BUY/SELL/HOLD signal if market deviates from model."""
        model  = self.call_price() if option_type == "call" else self.put_price()
        edge   = model - market_price
        pct    = edge / model * 100
        signal = "BUY" if edge > 0 else ("SELL" if edge < 0 else "HOLD")
        print(f"\n[BSM Mispricing]  Type={option_type.upper()}  Market=${market_price:.4f}")
        print(f"  Model price : ${model:.4f}")
        print(f"  Edge        : ${edge:.4f}  ({pct:.2f}%)")
        print(f"  Signal      : ► {signal}")
        return {"model": model, "market": market_price, "edge": edge, "signal": signal}

    def print_summary(self):
        g = self.greeks()
        print(f"\n[BSM Summary]  S={self.S}  K={self.K}  T={self.T:.4f}yr  σ={self.sigma:.2%}")
        print(f"  Call  = ${self.call_price():.4f}   Put = ${self.put_price():.4f}")
        print(f"  Δcall = {g['delta_call']:.4f}   Δput = {g['delta_put']:.4f}")
        print(f"  Γ     = {g['gamma']:.6f}")
        print(f"  Vega  = {g['vega']:.4f}  (per 1% vol move)")
        print(f"  Θcall = {g['theta_call']:.4f}  (per day)")


# ═══════════════════════════════════════════════════════════════════
#  MODULE 1D — ARIMA + GARCH   Volatility Forecasting
# ═══════════════════════════════════════════════════════════════════

class VolatilityForecaster:
    """ARIMA for returns mean + GARCH(1,1) for conditional variance."""

    def __init__(self, returns: pd.Series):
        self.returns = (returns * 100).dropna()   # GARCH needs % scale

    def fit_garch(self, p: int = 1, q: int = 1):
        if not ARCH_OK:
            print("[VolatilityForecaster] arch not installed.")
            return None
        model  = arch_model(self.returns, vol="Garch", p=p, q=q, dist="Normal")
        result = model.fit(disp="off")
        print(f"\n[GARCH({p},{q}) Results]")
        print(result.summary().tables[1])
        return result

    def forecast_vol(self, result, horizon: int = 10):
        if result is None:
            return None
        fcast    = result.forecast(horizon=horizon)
        vol_fore = np.sqrt(fcast.variance.values[-1]) / 100   # back to decimal
        print(f"\n[GARCH Volatility Forecast — next {horizon} days]")
        for i, v in enumerate(vol_fore, 1):
            ann = v * np.sqrt(252) * 100
            print(f"  Day {i:2d}: daily σ = {v:.5f}  |  annualised = {ann:.2f}%")
        return vol_fore

    def fit_arima(self, order=(1, 1, 1)):
        if not STATSMODELS_OK:
            print("[VolatilityForecaster] statsmodels not installed.")
            return None
        model  = ARIMA(self.returns, order=order)
        result = model.fit()
        print(f"\n[ARIMA{order} AIC={result.aic:.2f}  BIC={result.bic:.2f}]")
        return result


# ═══════════════════════════════════════════════════════════════════
#  MODULE 2A — LIMIT ORDER BOOK  Imbalance
# ═══════════════════════════════════════════════════════════════════

class LOBImbalanceModel:
    """
    Synthetic LOB built from tick data or daily OHLCV proxies.
    Order Imbalance  OI = (bid_vol - ask_vol) / (bid_vol + ask_vol)
    OI > 0  →  buying pressure  →  price likely to tick UP
    OI < 0  →  selling pressure →  price likely to tick DOWN
    """

    @staticmethod
    def compute_imbalance(bid_vol: float, ask_vol: float) -> dict:
        oi = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        bias = "BUY PRESSURE ↑" if oi > 0.1 else ("SELL PRESSURE ↓" if oi < -0.1 else "NEUTRAL")
        print(f"\n[LOB Imbalance]  Bid={bid_vol:,.0f}  Ask={ask_vol:,.0f}")
        print(f"  Imbalance OI = {oi:.4f}  →  {bias}")
        return {"oi": oi, "bias": bias}

    @staticmethod
    def proxy_from_ohlcv(df: pd.DataFrame) -> pd.Series:
        """
        Approximate LOB imbalance from daily OHLCV:
        Up-volume fraction ≈ (Close - Low) / (High - Low)
        """
        up_frac  = (df["Close"] - df["Low"]) / (df["High"] - df["Low"] + 1e-9)
        bid_vol  = df["Volume"] * up_frac
        ask_vol  = df["Volume"] * (1 - up_frac)
        oi       = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        return oi.rename("LOB_Imbalance")


# ═══════════════════════════════════════════════════════════════════
#  MODULE 2B — VWAP  /  TWAP  Execution Engines
# ═══════════════════════════════════════════════════════════════════

class ExecutionEngine:
    """VWAP and TWAP calculation and slicing for large orders."""

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        """Standard VWAP: cumulative (price × volume) / cumulative volume."""
        pv   = ((df["High"] + df["Low"] + df["Close"]) / 3) * df["Volume"]
        vwap = pv.cumsum() / df["Volume"].cumsum()
        return vwap.rename("VWAP")

    @staticmethod
    def twap(df: pd.DataFrame) -> pd.Series:
        """TWAP: simple average of (H+L+C)/3 over the period."""
        return (((df["High"] + df["Low"] + df["Close"]) / 3)
                .expanding().mean().rename("TWAP"))

    @staticmethod
    def slice_order(total_qty: int, n_slices: int,
                    strategy: str = "TWAP") -> pd.DataFrame:
        """Breaks a large order into time-equal or volume-equal slices."""
        if strategy == "TWAP":
            qty = [total_qty // n_slices] * n_slices
            qty[-1] += total_qty - sum(qty)       # remainder goes to last
        else:                                      # VWAP shape — uniform here
            qty = [total_qty // n_slices] * n_slices
        slices = pd.DataFrame({"Slice": range(1, n_slices + 1), "Qty": qty})
        print(f"\n[{strategy} Order Slicing]  Total={total_qty}  Slices={n_slices}")
        print(slices.to_string(index=False))
        return slices


# ═══════════════════════════════════════════════════════════════════
#  MODULE 2C — VPIN  (Order Flow Toxicity)
# ═══════════════════════════════════════════════════════════════════

class VPINModel:
    """
    VPIN ≈ |buy_vol - sell_vol| / total_vol  per bucket.
    High VPIN (> 0.5) → toxic flow → widen spreads / reduce exposure.
    """

    @staticmethod
    def compute(df: pd.DataFrame, bucket_size: int = 50) -> pd.Series:
        up_frac  = (df["Close"] - df["Low"]) / (df["High"] - df["Low"] + 1e-9)
        buy_vol  = df["Volume"] * up_frac
        sell_vol = df["Volume"] * (1 - up_frac)
        imb      = (buy_vol - sell_vol).abs()
        total    = buy_vol + sell_vol

        # rolling bucket approximation
        vpin = (imb.rolling(bucket_size).sum()
                / total.rolling(bucket_size).sum()).rename("VPIN")
        print(f"\n[VPIN]  Bucket size={bucket_size} days")
        print(f"  Latest VPIN = {vpin.dropna().iloc[-1]:.4f}  "
              f"({'TOXIC ⚠' if vpin.dropna().iloc[-1] > 0.5 else 'NORMAL'})")
        return vpin


# ═══════════════════════════════════════════════════════════════════
#  MODULE 3A — STATISTICAL ARBITRAGE / PAIRS TRADING
# ═══════════════════════════════════════════════════════════════════

class PairsTrader:
    """
    Engle-Granger cointegration test → OLS hedge ratio → Z-score mean reversion.
    Signal:  Z > +2 → SHORT spread   |   Z < -2 → LONG spread
    """

    def __init__(self, price_a: pd.Series, price_b: pd.Series,
                 entry_z: float = 2.0, exit_z: float = 0.5):
        self.A       = price_a
        self.B       = price_b
        self.entry_z = entry_z
        self.exit_z  = exit_z

    def test_cointegration(self):
        if not STATSMODELS_OK:
            print("[PairsTrader] statsmodels needed for cointegration test.")
            return None
        score, pval, _ = coint(self.A, self.B)
        print(f"\n[Cointegration Test]  {self.A.name} ↔ {self.B.name}")
        print(f"  t-stat = {score:.4f}   p-value = {pval:.4f}  "
              f"{'✓ COINTEGRATED' if pval < 0.05 else '✗ NOT cointegrated'}")
        return pval

    def build_spread(self) -> pd.Series:
        """OLS hedge ratio β: spread = A − β·B"""
        beta  = np.polyfit(self.B, self.A, 1)[0]
        spread = self.A - beta * self.B
        return spread.rename("Spread"), beta

    def z_score(self, spread: pd.Series, window: int = 60) -> pd.Series:
        mu  = spread.rolling(window).mean()
        sig = spread.rolling(window).std()
        return ((spread - mu) / sig).rename("Z_Score")

    def signals(self, z: pd.Series) -> pd.Series:
        sig = pd.Series(0, index=z.index, name="Signal")
        sig[z >  self.entry_z] = -1    # SHORT spread
        sig[z < -self.entry_z] =  1    # LONG spread
        sig[(z > -self.exit_z) & (z < self.exit_z)] = 0   # EXIT
        return sig

    def run(self):
        pval          = self.test_cointegration()
        spread, beta  = self.build_spread()
        z             = self.z_score(spread)
        sig           = self.signals(z)
        print(f"\n[Pairs Trading]  Hedge ratio β = {beta:.4f}")
        print(f"  Current Z = {z.dropna().iloc[-1]:.4f}  "
              f"→  Signal: {['SHORT','HOLD','LONG'][sig.iloc[-1]+1]}")
        return spread, z, sig

    def plot(self, spread: pd.Series, z: pd.Series, sig: pd.Series):
        fig, axes = plt.subplots(2, 1, figsize=(13, 8))
        fig.suptitle(f"Pairs Trading: {self.A.name} / {self.B.name}", fontweight="bold")

        axes[0].plot(spread, color="navy", linewidth=1)
        axes[0].set_title("Spread")
        axes[0].axhline(spread.mean(), color="red", linestyle="--")

        axes[1].plot(z, color="darkorange", linewidth=1)
        axes[1].axhline( self.entry_z, color="red",   linestyle="--", label=f"+{self.entry_z}σ (Short)")
        axes[1].axhline(-self.entry_z, color="green", linestyle="--", label=f"-{self.entry_z}σ (Long)")
        axes[1].axhline(0, color="grey", linestyle=":")
        axes[1].fill_between(z.index, z, self.entry_z,  where=(z >  self.entry_z), alpha=0.2, color="red")
        axes[1].fill_between(z.index, z, -self.entry_z, where=(z < -self.entry_z), alpha=0.2, color="green")
        axes[1].set_title("Z-Score")
        axes[1].legend()

        plt.tight_layout()
        plt.show()


# ═══════════════════════════════════════════════════════════════════
#  MODULE 3B — NLP SENTIMENT ENGINE
# ═══════════════════════════════════════════════════════════════════

class SentimentEngine:
    """
    Scores text (earnings call snippets, news headlines) using TextBlob.
    Polarity:  +1 = max bullish  |  -1 = max bearish
    """

    @staticmethod
    def score(text: str) -> dict:
        if not NLP_OK:
            print("[SentimentEngine] textblob not installed.")
            return {}
        blob       = TextBlob(text)
        polarity   = blob.sentiment.polarity
        subjectivity = blob.sentiment.subjectivity
        label      = ("BULLISH 📈" if polarity > 0.1 else
                      "BEARISH 📉" if polarity < -0.1 else "NEUTRAL ➡")
        result = {"polarity": polarity, "subjectivity": subjectivity, "label": label}
        print(f"\n[NLP Sentiment]")
        print(f"  Text      : \"{text[:80]}…\"")
        print(f"  Polarity  : {polarity:.4f}  |  Subjectivity: {subjectivity:.4f}")
        print(f"  Signal    : {label}")
        return result

    @staticmethod
    def batch_score(texts: list) -> pd.DataFrame:
        rows = []
        for t in texts:
            if not NLP_OK:
                break
            b    = TextBlob(t)
            rows.append({"text": t[:60], "polarity": b.sentiment.polarity,
                         "subjectivity": b.sentiment.subjectivity})
        df = pd.DataFrame(rows)
        if not df.empty:
            df["signal"] = df["polarity"].apply(
                lambda p: "BULL" if p > 0.1 else ("BEAR" if p < -0.1 else "NEUTRAL"))
        return df


# ═══════════════════════════════════════════════════════════════════
#  MODULE 4A — MARKOWITZ EFFICIENT FRONTIER
# ═══════════════════════════════════════════════════════════════════

class EfficientFrontier:
    """
    Mean-Variance Portfolio Optimisation.
    Finds: Minimum Variance, Maximum Sharpe, and the full frontier curve.
    """

    def __init__(self, returns: pd.DataFrame, risk_free: float = 0.04):
        self.ret  = returns
        self.rf   = risk_free
        self.mu   = returns.mean() * 252          # annualised
        self.cov  = returns.cov()  * 252          # annualised
        self.n    = len(self.mu)

    def _port_stats(self, w: np.ndarray):
        ret = w @ self.mu.values
        vol = np.sqrt(w @ self.cov.values @ w)
        sharpe = (ret - self.rf) / vol
        return ret, vol, sharpe

    def max_sharpe(self) -> dict:
        def neg_sharpe(w):
            _, _, s = self._port_stats(w)
            return -s
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        bounds      = [(0, 1)] * self.n
        w0          = np.ones(self.n) / self.n
        res         = minimize(neg_sharpe, w0, method="SLSQP",
                               bounds=bounds, constraints=constraints)
        r, v, s     = self._port_stats(res.x)
        result      = {"weights": dict(zip(self.ret.columns, res.x)),
                       "return": r, "volatility": v, "sharpe": s}
        print(f"\n[Max Sharpe Portfolio]")
        for t, w in result["weights"].items():
            print(f"  {t:<6}: {w*100:6.2f}%")
        print(f"  Return={r:.2%}  Vol={v:.2%}  Sharpe={s:.4f}")
        return result

    def min_variance(self) -> dict:
        def port_vol(w):
            return np.sqrt(w @ self.cov.values @ w)
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        bounds      = [(0, 1)] * self.n
        w0          = np.ones(self.n) / self.n
        res         = minimize(port_vol, w0, method="SLSQP",
                               bounds=bounds, constraints=constraints)
        r, v, s     = self._port_stats(res.x)
        result      = {"weights": dict(zip(self.ret.columns, res.x)),
                       "return": r, "volatility": v, "sharpe": s}
        print(f"\n[Min Variance Portfolio]")
        for t, w in result["weights"].items():
            print(f"  {t:<6}: {w*100:6.2f}%")
        print(f"  Return={r:.2%}  Vol={v:.2%}  Sharpe={s:.4f}")
        return result

    def frontier_curve(self, n_points: int = 50) -> pd.DataFrame:
        target_rets = np.linspace(self.mu.min(), self.mu.max(), n_points)
        vols        = []
        for target in target_rets:
            constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1},
                           {"type": "eq", "fun": lambda w, t=target: w @ self.mu.values - t}]
            bounds = [(0, 1)] * self.n
            res    = minimize(lambda w: np.sqrt(w @ self.cov.values @ w),
                              np.ones(self.n) / self.n, method="SLSQP",
                              bounds=bounds, constraints=constraints)
            vols.append(res.fun if res.success else np.nan)
        return pd.DataFrame({"Return": target_rets, "Volatility": vols})

    def plot(self):
        frontier = self.frontier_curve()
        ms       = self.max_sharpe()
        mv       = self.min_variance()

        plt.figure(figsize=(10, 6))
        plt.plot(frontier["Volatility"], frontier["Return"],
                 "b-", linewidth=2, label="Efficient Frontier")
        plt.scatter(ms["volatility"], ms["return"], marker="*", s=300,
                    color="gold", zorder=5, label="Max Sharpe")
        plt.scatter(mv["volatility"], mv["return"], marker="^", s=200,
                    color="red", zorder=5, label="Min Variance")
        plt.xlabel("Annualised Volatility")
        plt.ylabel("Annualised Return")
        plt.title("Markowitz Efficient Frontier")
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()


# ═══════════════════════════════════════════════════════════════════
#  MODULE 4B — BLACK-LITTERMAN MODEL
# ═══════════════════════════════════════════════════════════════════

class BlackLitterman:
    """
    Blends market-equilibrium returns (CAPM) with analyst views.
    τ  : uncertainty scalar (typically 0.025–0.05)
    P  : view matrix  (k views × n assets)
    Q  : expected returns for each view
    Ω  : view uncertainty (diagonal matrix)
    """

    def __init__(self, returns: pd.DataFrame, mkt_caps: dict,
                 risk_free: float = 0.04, tau: float = 0.025):
        self.ret    = returns
        self.tickers = list(returns.columns)
        self.n      = len(self.tickers)
        self.rf     = risk_free
        self.tau    = tau

        # Market-cap weights (normalised)
        total       = sum(mkt_caps.values())
        self.w_mkt  = np.array([mkt_caps.get(t, 1) for t in self.tickers]) / total

        self.sigma  = returns.cov().values * 252
        lam         = 2.5          # risk-aversion coefficient
        self.pi     = lam * self.sigma @ self.w_mkt   # equilibrium excess returns

    def posterior(self, P: np.ndarray, Q: np.ndarray,
                  omega: np.ndarray = None) -> dict:
        """
        Returns posterior expected returns μ_BL.
        P : (k, n) view matrix
        Q : (k,)   view expected returns
        Ω : (k, k) view uncertainty (default: diagonal scaled by τ·P·Σ·P')
        """
        if omega is None:
            omega = np.diag(np.diag(self.tau * P @ self.sigma @ P.T))

        inv_tau_sig = np.linalg.inv(self.tau * self.sigma)
        inv_omega   = np.linalg.inv(omega)
        M           = inv_tau_sig + P.T @ inv_omega @ P
        mu_bl       = np.linalg.inv(M) @ (inv_tau_sig @ self.pi + P.T @ inv_omega @ Q)

        print("\n[Black-Litterman Posterior Returns]")
        eq_dict = {}
        for i, t in enumerate(self.tickers):
            print(f"  {t:<6}: equilib={self.pi[i]:.4f}  BL={mu_bl[i]:.4f}")
            eq_dict[t] = {"equilibrium": self.pi[i], "BL": mu_bl[i]}
        return {"mu_bl": mu_bl, "detail": eq_dict}


# ═══════════════════════════════════════════════════════════════════
#  MODULE 5 — BACKTESTER
# ═══════════════════════════════════════════════════════════════════

class Backtester:
    """
    Signal-based P&L engine.
    signal = +1 (long), -1 (short), 0 (flat)
    Includes transaction cost per trade.
    """

    def __init__(self, prices: pd.Series, signals: pd.Series,
                 capital: float = 100_000, cost_bps: float = 5):
        self.prices  = prices.dropna()
        self.signals = signals.reindex(self.prices.index).fillna(0)
        self.capital = capital
        self.cost    = cost_bps / 10_000       # basis points → decimal

    def run(self) -> pd.DataFrame:
        ret      = self.prices.pct_change().shift(-1)   # fwd 1-day return
        strat    = self.signals * ret

        # transaction cost: applied on position change
        trades   = self.signals.diff().abs()
        tc       = trades * self.cost
        net      = strat - tc

        equity   = self.capital * (1 + net).cumprod()
        drawdown = (equity / equity.cummax() - 1)

        res = pd.DataFrame({
            "price"   : self.prices,
            "signal"  : self.signals,
            "ret"     : ret,
            "strat"   : strat,
            "net"     : net,
            "equity"  : equity,
            "drawdown": drawdown,
        })
        self._print_stats(net, equity, drawdown)
        return res

    def _print_stats(self, net: pd.Series, equity: pd.Series, dd: pd.Series):
        total_ret = (equity.iloc[-1] / self.capital - 1) * 100
        ann_ret   = net.mean() * 252 * 100
        ann_vol   = net.std() * np.sqrt(252) * 100
        sharpe    = (net.mean() / net.std()) * np.sqrt(252) if net.std() else 0
        downside  = net[net < 0].std() * np.sqrt(252) * 100
        sortino   = (net.mean() * 252 * 100) / downside if downside else 0
        max_dd    = dd.min() * 100
        win_rate  = (net > 0).mean() * 100
        n_trades  = (self.signals.diff().abs() > 0).sum()

        print("\n" + "═"*50)
        print("  BACKTEST RESULTS")
        print("═"*50)
        print(f"  Total Return    : {total_ret:+.2f}%")
        print(f"  Ann. Return     : {ann_ret:+.2f}%")
        print(f"  Ann. Volatility : {ann_vol:.2f}%")
        print(f"  Sharpe Ratio    : {sharpe:.4f}")
        print(f"  Sortino Ratio   : {sortino:.4f}")
        print(f"  Max Drawdown    : {max_dd:.2f}%")
        print(f"  Win Rate        : {win_rate:.1f}%")
        print(f"  # Trades        : {n_trades}")
        print("═"*50)

    def plot(self, res: pd.DataFrame, title: str = "Backtest"):
        fig, axes = plt.subplots(3, 1, figsize=(13, 10))
        fig.suptitle(title, fontweight="bold")

        axes[0].plot(res["equity"], color="navy", linewidth=1.5)
        axes[0].set_title("Equity Curve")
        axes[0].set_ylabel("Portfolio Value ($)")

        axes[1].plot(res["price"] / res["price"].iloc[0] * 100000,
                     color="grey", linewidth=1, label="Buy & Hold")
        axes[1].plot(res["equity"], color="navy", linewidth=1, label="Strategy")
        axes[1].legend()
        axes[1].set_title("Strategy vs Buy & Hold")

        axes[2].fill_between(res.index, res["drawdown"] * 100, 0,
                             color="red", alpha=0.4)
        axes[2].set_title("Drawdown (%)")
        axes[2].set_ylabel("%")

        plt.tight_layout()
        plt.show()


# ═══════════════════════════════════════════════════════════════════
#  DEMO  —  Run Everything End-to-End
# ═══════════════════════════════════════════════════════════════════

def run_full_demo():
    print("\n" + "█"*60)
    print("  QUANT ANALYSIS ENGINE — FULL DEMO")
    print("█"*60)

    # ── 0. DATA ──────────────────────────────────────────────────
    TICKERS = ["AAPL", "MSFT", "JPM", "XOM"]
    PAIR    = ["CVX", "XOM"]
    START   = "2021-01-01"

    loader = DataLoader(TICKERS + PAIR, start=START)
    raw    = loader.fetch()
    closes = loader.close_prices()
    log_ret = loader.log_returns()

    # ── 1A. SDE / GBM  ───────────────────────────────────────────
    aapl_ret = log_ret["AAPL"]
    mu_aapl  = aapl_ret.mean() * 252
    sig_aapl = aapl_ret.std()  * np.sqrt(252)
    S0_aapl  = closes["AAPL"].iloc[-1]

    sde   = SDESimulator(S0=S0_aapl, mu=mu_aapl, sigma=sig_aapl, T=252, N=2000)
    paths = sde.simulate()
    sde.summary(paths)
    sde.plot(paths, ticker="AAPL")

    # ── 1B. Monte Carlo VaR  ─────────────────────────────────────
    mc   = MonteCarloRisk(portfolio_value=1_000_000,
                          returns=aapl_ret, horizon=10, n_sims=100_000)
    res  = mc.compute()
    mc.plot(res["pnl_dist"])

    # ── 1C. Black-Scholes  ───────────────────────────────────────
    bsm = BlackScholes(S=S0_aapl, K=S0_aapl * 1.05,
                       T=30/252, r=0.05, sigma=sig_aapl)
    bsm.print_summary()
    bsm.misprice_signal(market_price=bsm.call_price() * 0.95, option_type="call")

    # ── 1D. GARCH  ───────────────────────────────────────────────
    vf     = VolatilityForecaster(aapl_ret)
    g_res  = vf.fit_garch()
    if g_res:
        vf.forecast_vol(g_res, horizon=10)

    # ── 2A. LOB Imbalance  ───────────────────────────────────────
    aapl_df = raw["AAPL"]
    oi_series = LOBImbalanceModel.proxy_from_ohlcv(aapl_df)
    last_row  = aapl_df.iloc[-1]
    up_frac   = (last_row["Close"] - last_row["Low"]) / (last_row["High"] - last_row["Low"] + 1e-9)
    LOBImbalanceModel.compute_imbalance(
        bid_vol=last_row["Volume"] * up_frac,
        ask_vol=last_row["Volume"] * (1 - up_frac)
    )

    # ── 2B. VWAP / TWAP  ─────────────────────────────────────────
    vwap_series = ExecutionEngine.vwap(aapl_df)
    twap_series = ExecutionEngine.twap(aapl_df)
    ExecutionEngine.slice_order(total_qty=100_000, n_slices=10, strategy="TWAP")
    print(f"\n[VWAP latest] ${vwap_series.iloc[-1]:.2f}  "
          f"[TWAP latest] ${twap_series.iloc[-1]:.2f}")

    # ── 2C. VPIN  ────────────────────────────────────────────────
    vpin_series = VPINModel.compute(aapl_df, bucket_size=20)

    # ── 3A. Pairs Trading  ───────────────────────────────────────
    cvx_prices = raw["CVX"]["Close"].rename("CVX")
    xom_prices = raw["XOM"]["Close"].rename("XOM")
    aligned    = pd.concat([cvx_prices, xom_prices], axis=1).dropna()

    pairs = PairsTrader(aligned["CVX"], aligned["XOM"])
    spread, z, sig = pairs.run()
    pairs.plot(spread, z, sig)

    # ── 3B. NLP Sentiment  ───────────────────────────────────────
    headlines = [
        "Apple reports record quarterly earnings, beats all estimates decisively",
        "Recession fears grow as Fed signals aggressive rate hikes ahead",
        "Market closes flat amid mixed economic signals and uncertainty",
    ]
    batch = SentimentEngine.batch_score(headlines)
    if not batch.empty:
        print("\n[NLP Batch Scores]")
        print(batch.to_string(index=False))

    # ── 4A. Markowitz Frontier  ──────────────────────────────────
    port_ret = log_ret[["AAPL", "MSFT", "JPM", "XOM"]]
    ef = EfficientFrontier(port_ret)
    ef.max_sharpe()
    ef.min_variance()
    ef.plot()

    # ── 4B. Black-Litterman  ─────────────────────────────────────
    mkt_caps = {"AAPL": 3e12, "MSFT": 2.8e12, "JPM": 5e11, "XOM": 4.5e11}
    bl = BlackLitterman(port_ret, mkt_caps)
    # View: AAPL will outperform MSFT by 5% annually
    P = np.array([[1, -1, 0, 0]])
    Q = np.array([0.05])
    bl.posterior(P, Q)

    # ── 5. BACKTEST (Pairs Z-Score Strategy on CVX/XOM)  ─────────
    # Signal: long CVX when Z < -2, short when Z > +2
    bt = Backtester(prices=aligned["CVX"],
                    signals=sig.reindex(aligned.index).fillna(0),
                    capital=500_000, cost_bps=5)
    bt_res = bt.run()
    bt.plot(bt_res, title="Pairs Strategy Backtest: CVX/XOM")

    print("\n✅  Full demo complete.\n")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    run_full_demo()
