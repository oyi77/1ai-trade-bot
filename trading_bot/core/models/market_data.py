"""
Market-data domain models.
"""

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass(frozen=True)
class Candle:
    """Single OHLCV candle — immutable value object."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class OrderBook:
    """Snapshot of an order-book at a point in time.

    ``bids`` and ``asks`` are lists of ``(price, quantity)`` tuples,
    sorted best-first (highest bid first, lowest ask first).
    """

    symbol: str
    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    timestamp: int = 0

    @property
    def best_bid(self) -> float:
        """Highest bid price, or 0.0 if empty."""
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        """Lowest ask price, or 0.0 if empty."""
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread(self) -> float:
        """Absolute spread between best ask and best bid."""
        if self.bids and self.asks:
            return self.best_ask - self.best_bid
        return 0.0


@dataclass
class MarketData:
    """Aggregated market data for a trading pair."""

    symbol: str
    price: float = 0.0
    volume_24h: float = 0.0
    change_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    market_cap: float = 0.0
    timestamp: int = 0
