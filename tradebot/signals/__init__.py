"""
tradebot.signals — Market data source abstraction layer.

Provides async data source implementations for Binance, Yahoo Finance,
forex APIs (Frankfurter), and Stockity platform, plus a unified
MarketAggregator with multi-source fallback chain.

Usage:
    from tradebot.signals import MarketAggregator, FallbackChain
    from tradebot.signals import BinanceSource, YahooSource

    aggregator = MarketAggregator()
    ohlcv = await aggregator.fetch("BTC-USD")
"""

from .base import BaseDataSource
from .binance import BinanceSource
from .forex import ForexSource
from .market import FallbackChain, MarketAggregator
from .stockity import StockitySource
from .yahoo import YahooSource

__all__ = [
    "BaseDataSource",
    "BinanceSource",
    "ForexSource",
    "YahooSource",
    "StockitySource",
    "MarketAggregator",
    "FallbackChain",
]
