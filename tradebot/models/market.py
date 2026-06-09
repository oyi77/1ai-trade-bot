"""
Market data models — ticks, OHLCV candles, and market state.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Tick:
    """A single market tick."""
    symbol: str
    price: float
    epoch: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def digit(self) -> int:
        """Get last digit of price (0-9)."""
        s = f"{self.price:.4f}"
        if '.' in s:
            dec = s.split('.')[1]
            dec = dec[:4].ljust(4, '0')
            return int(dec[-1])
        return 0


@dataclass
class OHLCV:
    """OHLCV candle."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    symbol: str = ""
    volume: int = 0


@dataclass
class MarketState:
    """
    Current market state — holds recent ticks, derived state, and cooldown info.
    """
    symbol: str
    ticks: list[Tick] = field(default_factory=list)
    max_ticks: int = 100
    is_trading_allowed: bool = True
    cooldown_until: datetime | None = None
    metadata: dict = field(default_factory=dict)

    def add_tick(self, tick: Tick):
        self.ticks.append(tick)
        if len(self.ticks) > self.max_ticks:
            self.ticks = self.ticks[-self.max_ticks:]

    @property
    def latest_tick(self) -> Tick | None:
        return self.ticks[-1] if self.ticks else None

    @property
    def recent_digits(self) -> list[int]:
        return [t.digit for t in self.ticks[-20:]]
