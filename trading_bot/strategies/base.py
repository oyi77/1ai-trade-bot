"""Base strategy abstraction — strategy interface and signal data model.

Every strategy consumes candle data through a ``BaseProvider`` and produces
optional ``StrategySignal`` results.  Strategies never depend on concrete
provider implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading_bot.providers.base import (
    BaseProvider,
    Candle,
    OrderSide,
)

# ---------------------------------------------------------------------------
#  StrategySignal — lightweight signal produced by strategies
# ---------------------------------------------------------------------------


@dataclass
class StrategySignal:
    """Trading signal emitted by a strategy after analysis.

    Attributes:
        symbol: Trading symbol (e.g. ``'XAU/USD'``).
        direction: Expected price direction (BUY or SELL).
        confidence: Signal strength in ``[0.0, 1.0]``.
        price: The price at which the signal was generated, if known.
        strategy_name: Name of the strategy that produced this signal.
        metadata: Arbitrary extra info (indicators, grid levels, …).
        timestamp: When the signal was generated.
    """

    symbol: str
    direction: OrderSide
    confidence: float
    price: float | None
    strategy_name: str
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)  # noqa: UP017
    )


# ---------------------------------------------------------------------------
#  BaseStrategy — abstract strategy interface
# ---------------------------------------------------------------------------


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    A strategy receives candle data through its attached provider and
    produces trading signals.  Lifecycle hooks (``on_start`` / ``on_stop``)
    allow strategies to initialise state and clean up resources.
    """

    def __init__(
        self,
        provider: BaseProvider,
        params: dict | None = None,
    ) -> None:
        self._provider = provider
        self._params = params if params is not None else {}

    # ── abstract ──────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy identifier."""
        ...

    @abstractmethod
    async def analyze(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> StrategySignal | None:
        """Run strategy logic and return a signal if conditions are met.

        Args:
            symbol: Trading symbol to analyse.
            timeframe: Candle timeframe (e.g. ``'1h'``, ``'15m'``, ``'1d'``).

        Returns:
            A ``StrategySignal`` when entry/exit conditions are satisfied,
            ``None`` when no actionable signal is found.
        """
        ...

    # ── lifecycle hooks (optional) ────────────────────────────────────

    async def on_start(self) -> None:
        """Called once before the first ``analyze`` call.

        Use to initialise grid levels, load historical data, etc.
        """
        return None

    async def on_stop(self) -> None:
        """Called once after the last ``analyze`` call.

        Use to cancel pending orders, persist state, etc.
        """
        return None

    # ── helpers ───────────────────────────────────────────────────────

    async def _fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[Candle]:
        """Shortcut to fetch candles from the attached provider."""
        return await self._provider.get_candles(symbol, timeframe, limit)
