"""
Abstract base class for market data sources.

All data sources inherit from BaseDataSource and implement the
async fetch() method returning a list of OHLCV candles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tradebot.models.market import OHLCV


class BaseDataSource(ABC):
    """Abstract base for a market data source.

    Subclasses must implement :meth:`fetch` to return OHLCV candles
    for the requested symbol.  Sources that need authentication or
    initialisation can override :meth:`__init__` and/or support
    ``async with`` usage.
    """

    @abstractmethod
    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch OHLCV candles for *symbol*.

        Args:
            symbol: Ticker or pair name (e.g. ``"BTC-USD"``, ``"EURUSD=X"``).
            interval: Candle interval (``"1m"``, ``"5m"``, ``"15m"``, …).
            count: Maximum number of candles to return.

        Returns:
            List of :class:`OHLCV` candles, newest last.  May be empty
            if the source has no data for the symbol.
        """
        ...

    async def price(self, symbol: str) -> float | None:
        """Convenience: return the latest close price for *symbol*."""
        candles = await self.fetch(symbol, interval="1m", count=1)
        if candles:
            return candles[-1].close
        return None
