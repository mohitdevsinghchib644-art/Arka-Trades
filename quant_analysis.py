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
            # Fix: explicitly set group_by or unpack to avoid multi-index errors in new yfinance versions
            df = yf.download(t, start=self.start, end=self.end, auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            self._raw[t] = df
            print(f"  ✓ {t}: {len(df)} rows")
        return self._raw

    def close_prices(self) -> pd.DataFrame:
        """Returns aligned Close prices for all tickers."""
        closes = {}
        for t in self._raw:
            # Handle potential multi-index or simple series extractions safely
            series = self._raw[t]["Close"]
            if isinstance(series, pd.DataFrame):
                series = series.iloc[:, 0]
            closes[t] = series
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
        self.mu    = mu       # annualised drift
        self.sigma = sigma    # annualised vol
        self.T     = T        # time horizon in trading days
        self.N     = N        # number of paths
        self.dt    = dt       # time step

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

        axes[0].plot(paths[:, :200], alpha=0.15, linewidth=0.7, color="steelblue")
        axes[0].plot(paths.mean(axis=1), color="red", linewidth=2, label="Mean path")
        axes[0].set_title("Price Paths (first 200)")
        axes[0].set_xlabel("Trading Days")
        axes[0].set_ylabel("Price ($)")
        axes[0].legend()

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
    """Uses GBM paths to compute portfolio-level VaR and CVaR."""

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

        Z    = np.random.standard_normal((self.n_sims, self.horizon))
        r    = (mu - 0.5 * sigma**2) + sigma * Z
        cum  = np.exp(r.sum(axis=1)) - 1          
        pnl  = self.V * cum                        

        var  = np.percentile(pnl, (1 - self.confidence) * 100)
        cvar = pnl[pnl <= var].mean()

        print(f"\n[Monte Carlo Risk] Portfolio = ${self.V:,.0f}")
        print(f"  Horizon    : {self.horizon} days")
        print(f"  Confidence : {self.confidence*100:.0f}%")
        print(f"  VaR        : ${var:,.2f}")
        print(f"  CVaR (ES)  : ${cvar:,.2f}")
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
    def __init__(self, S: float, K: float, T: float, r: float, sigma: float):
        self.S, self.K, self.T, self.r, self.sigma = S, K, T, r, sigma
        self.d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        self.d2 = self.d1 - sigma * np.sqrt(T)

    def call_price(self) -> float:
        return (self.S * norm.cdf(self.d1) - self.K * np.exp(-self.r * self.T) * norm.cdf(self.d2))

    def put_price(self) -> float:
        return (self.K * np.exp(-self.r * self.T) * norm.cdf(-self.d2) - self.S * norm.cdf(-self.d1))

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
        model  = self.call_price() if option_type == "call" else self.put_price()
        edge   = model - market_price
        pct    = edge / model * 100
        signal = "BUY" if edge > 0 else ("SELL" if edge < 0 else "HOLD")
        print(f"\n[BSM Mispricing]  Type={option_type.upper()}  Market=${market_price:.4f}")
        print(f"  Model price : ${model:.4f} | Edge: ${edge:.4f} ({pct:.2f}%)")
        print(f"  Signal      : ► {signal}")
        return {"model": model, "market": market_price, "edge": edge, "signal": signal}


# ═══════════════════════════════════════════════════════════════════
#  MODULE 1D — ARIMA + GARCH Volatility Forecasting
# ═══════════════════════════════════════════════════════════════════

class VolatilityForecaster:
    def __init__(self, returns: pd.Series):
        self.returns = (returns * 100).dropna()   

    def fit_garch(self, p: int = 1, q: int = 1):
        if not ARCH_OK:
            print("[VolatilityForecaster] arch not installed.")
            return None
        model  = arch_model(self.returns, vol="Garch", p=p, q=q, dist="Normal")
        result = model.fit(disp="off")
        print(f"\n[GARCH({p},{q}) Fitted Successfully]")
        return result

    def forecast_vol(self, result, horizon: int = 5):
        if result is None: return None
        fcast    = result.forecast(horizon=horizon)
        vol_fore = np.sqrt(fcast.variance.values[-1]) / 100  
        print(f"\n[GARCH Volatility Forecast — Next {horizon} Days]")
        for i, v in enumerate(vol_fore[:horizon], 1):
            print(f"  Day {i}: Daily σ = {v:.5f} | Annualised = {v * np.sqrt(252) * 100:.2f}%")
        return vol_fore


# ═══════════════════════════════════════════════════════════════════
#  MODULE 2 — MARKET MICROSTRUCTURE
# ═══════════════════════════════════════════════════════════════════

class LOBImbalanceModel:
    @staticmethod
    def proxy_from_ohlcv(df: pd.DataFrame) -> pd.Series:
        up_frac  = (df["Close"] - df["Low"]) / (df["High"] - df["Low"] + 1e-9)
        bid_vol  = df["Volume"] * up_frac
        ask_vol  = df["Volume"] * (1 - up_frac)
        oi       = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        return oi.rename("LOB_Imbalance")

class ExecutionEngine:
    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        pv   = ((df["High"] + df["Low"] + df["Close"]) / 3) * df["Volume"]
        return (pv.cumsum() / df["Volume"].cumsum()).rename("VWAP")

class VPINModel:
    @staticmethod
    def compute(df: pd.DataFrame, bucket_size: int = 20) -> pd.Series:
        up_frac  = (df["Close"] - df["Low"]) / (df["High"] - df["Low"] + 1e-9)
        buy_vol  = df["Volume"] * up_frac
        sell_vol = df["Volume"] * (1 - up_frac)
        imb      = (buy_vol - sell_vol).abs()
        vpin     = (imb.rolling(bucket_size).sum() / df["Volume"].rolling(bucket_size).sum()).rename("VPIN")
        return vpin


# ═══════════════════════════════════════════════════════════════════
#  MODULE 3 — ALPHA STRATEGIES
# ═══════════════════════════════════════════════════════════════════

class PairsTrader:
    def __init__(self, price_a: pd.Series, price_b: pd.Series, entry_z: float = 2.0, exit_z: float = 0.5):
        self.A, self.B, self.entry_z, self.exit_z = price_a, price_b, entry_z, exit_z

    def run(self):
        beta   = np.polyfit(self.B, self.A, 1)[0]
        spread = (self.A - beta * self.B).rename("Spread")
        z      = ((spread - spread.rolling(60).mean()) / spread.rolling(60).std()).rename("Z_Score")
        
        sig = pd.Series(0, index=z.index, name="Signal")
        sig[z > self.entry_z] = -1   # Short spread
        sig[z < -self.entry_z] = 1   # Long spread
        return spread, z, sig


# ═══════════════════════════════════════════════════════════════════
#  MODULE 4 — PORTFOLIO OPTIMIZATION
# ═══════════════════════════════════════════════════════════════════

class EfficientFrontier:
    def __init__(self, returns: pd.DataFrame, risk_free: float = 0.04):
        self.ret  = returns
        self.rf   = risk_free
        self.mu   = returns.mean() * 252
        self.cov  = returns.cov() * 252
        self.n    = len(self.mu)

    def max_sharpe(self) -> dict:
        def neg_sharpe(w):
            r = w @ self.mu.values
            v = np.sqrt(w @ self.cov.values @ w)
            return -(r - self.rf) / v
        res = minimize(neg_sharpe, np.ones(self.n)/self.n, method="SLSQP", bounds=[(0,1)]*self.n, constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
        return {"weights": dict(zip(self.ret.columns, res.x)), "return": res.x @ self.mu.values, "volatility": np.sqrt(res.x @ self.cov.values @ res.x)}


# ═══════════════════════════════════════════════════════════════════
#  MODULE 5 — BACKTESTER
# ═══════════════════════════════════════════════════════════════════

class Backtester:
    def __init__(self, prices: pd.Series, signals: pd.Series, capital: float = 100_000, cost_bps: float = 5):
        self.prices  = prices.dropna()
        self.signals = signals.reindex(self.prices.index).fillna(0)
        self.capital = capital
        self.cost    = cost_bps / 10_000

    def run(self) -> pd.DataFrame:
        # Fix: fillna(0) to prevent NaN values from wiping out the cumulative engine metrics
        ret      = self.prices.pct_change().shift(-1).fillna(0)
        strat    = self.signals * ret
        trades   = self.signals.diff().abs().fillna(0)
        tc       = trades * self.cost
        net      = strat - tc

        equity   = self.capital * (1 + net).cumprod()
        drawdown = (equity / equity.cummax() - 1)

        res = pd.DataFrame({"price": self.prices, "signal": self.signals, "net": net, "equity": equity, "drawdown": drawdown})
        self._print_stats(net, equity, drawdown)
        return res

    def _print_stats(self, net: pd.Series, equity: pd.Series, dd: pd.Series):
        print("\n" + "═"*50 + "\n  BACKTEST RESULTS\n" + "═"*50)
        print(f"  Total Return    : {(equity.iloc[-1] / self.capital - 1) * 100:+.2f}%")
        print(f"  Max Drawdown    : {dd.min() * 100:.2f}%")
        print(f"  Sharpe Ratio    : {(net.mean() / (net.std() + 1e-9)) * np.sqrt(252):.4f}")
        print("═"*50)


# ═══════════════════════════════════════════════════════════════════
#  EXECUTION PIPELINE BLOCK
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("[Engine Activation] Initializing components...")
    
    # 1. Fetch live historical data
    loader = DataLoader(tickers=["AAPL", "MSFT"], start="2023-01-01", end="2025-01-01")
    raw_data = loader.fetch()
    closes = loader.close_prices()
    returns = loader.log_returns()
    
    # 2. Run Volatility Forecaster (GARCH)
    print("\n--- Volatility Engine Checking ---")
    forecaster = VolatilityForecaster(returns["AAPL"])
    garch_res = forecaster.fit_garch()
    if garch_res is not None:
        forecaster.forecast_vol(garch_res)

    # 3. Microstructure Proxies
    print("\n--- Microstructure Assessment ---")
    vpin = VPINModel.compute(raw_data["AAPL"])
    print(f"Latest AAPL VPIN Flow Toxicity Indicator Score: {vpin.iloc[-1]:.4f}")

    # 4. Generate Pair Trading Alpha Strategy signals
    print("\n--- Generating Statistical Arbitrage Strategy ---")
    pt = PairsTrader(closes["AAPL"], closes["MSFT"])
    spread, z_score, signals = pt.run()

    # 5. Optimize a basic portfolio allocation matrix
    print("\n--- Portfolio Optimization Layer ---")
    ef = EfficientFrontier(returns)
    opt_alloc = ef.max_sharpe()
    print(f"Optimal Allocation: {opt_alloc['weights']}")

    # 6. Execute complete Strategy Backtest
    print("\n--- Executing Backtest Engine ---")
    backtester = Backtester(prices=closes["AAPL"], signals=signals)
    performance_df = backtester.run()
