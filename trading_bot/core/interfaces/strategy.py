"""
Strategy port — defines the contract every trading strategy must satisfy.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IStrategy(ABC):
    """Abstract interface for trading strategies.

    Concrete strategies (grid, trend-following, hedging, etc.) implement
    this port.  The core engine drives strategies exclusively through
    these methods.
    """

    # -- lifecycle -----------------------------------------------------------

    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> None:
        """One-time setup with strategy-specific configuration.

        Args:
            config: Strategy parameters (timeframe, thresholds, …).

        Raises:
            ConfigurationError: on invalid / missing config keys.
        """

    @abstractmethod
    async def cleanup(self) -> None:
        """Release resources held by the strategy."""

    # -- core loop -----------------------------------------------------------

    @abstractmethod
    async def analyze(self, market_data: Any) -> Optional[Any]:
        """Analyze market data and optionally produce a signal.

        Args:
            market_data: MarketData / OrderBook / raw dict — the strategy
                         decides what it needs.

        Returns:
            A TradeSignal when an opportunity is detected, else ``None``.
        """

    @abstractmethod
    async def execute(self, signal: Any) -> Optional[Any]:
        """Act on a trade signal (place orders, adjust positions, …).

        Args:
            signal: TradeSignal produced by ``analyze``.

        Returns:
            Execution result / order confirmation, or ``None``.
        """

    # -- event hooks ---------------------------------------------------------

    @abstractmethod
    async def on_price_update(
        self, symbol: str, price: float, timestamp: int
    ) -> None:
        """Called on every price tick.

        Args:
            symbol:    Pair that ticked.
            price:     Latest price.
            timestamp: Unix-ms timestamp of the tick.
        """

    @abstractmethod
    async def on_tx_confirmation(
        self, tx_hash: str, status: str, details: Dict[str, Any]
    ) -> None:
        """Called when an on-chain transaction confirms.

        Args:
            tx_hash: Transaction hash.
            status:  "confirmed" | "failed" | "reverted".
            details: Chain-specific metadata.
        """

    @abstractmethod
    async def on_error(self, error: Exception) -> None:
        """Called when the engine catches an unhandled error.

        The strategy may choose to pause, retry, or escalate.

        Args:
            error: The caught exception.
        """
