"""
arka_flow - Arka Trades sector rotation engine (rebuild).

Phase 2 (data foundation) and Phase 3 (calculation engine). Pure Python / pandas.
Nothing in this package imports Streamlit, and nothing in it changes the existing Money Flow module.

Rules this package follows
  * No value is invented. A number that cannot be computed is NaN / None and is reported as NOT AVAILABLE.
  * No forward-filling of prices, no missing value turned into zero.
  * No BUY / SELL / AVOID / EXIT labels. Phase 3 produces measurements and plain descriptions of them.
    Labels come only after the Phase 6 backtest.
  * Every constant that is a judgement call is in config.py and marked PROVISIONAL.
"""
from .config import MODEL_VERSION

__all__ = ["MODEL_VERSION"]
