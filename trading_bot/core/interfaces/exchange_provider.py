"""
Exchange provider port — defines the contract every exchange adapter must satisfy.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any


class IExchangeProvider(ABC):
    """Abstract interface for exchange connectivity.

    Adapters for Binance, Bybit, OKX, Exness, Ostium DEX, etc.
    must implement every method declared here.
    """

    # -- lifecycle -----------------------------------------------------------

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the exchange.

        Returns:
            True on success.

        Raises:
            ExchangeError: on connection failure.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the exchange connection."""

    # -- account -------------------------------------------------------------

    @abstractmethod
    async def get_balance(self, asset: str = "") -> Dict[str, Any]:
        """Return account balance.

        Args:
            asset: Optional specific asset (e.g. "USDT").
                   Empty string returns all balances.

        Returns:
            Dict with at least ``total``, ``free``, ``used`` keys.
        """

    # -- market data ---------------------------------------------------------

    @abstractmethod
    async def get_order_book(
        self, symbol: str, depth: int = 20
    ) -> Dict[str, Any]:
        """Fetch order-book snapshot.

        Args:
            symbol: Trading pair, e.g. "BTC/USDT".
            depth:  Number of levels per side.

        Returns:
            Dict with ``bids`` and ``asks`` lists of [price, qty].
        """

    # -- order management ----------------------------------------------------

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Place a new order on the exchange.

        Args:
            symbol:     Trading pair.
            side:       "buy" or "sell".
            order_type: "market", "limit", "stop", etc.
            amount:     Quantity.
            price:      Required for limit orders.
            params:     Exchange-specific extra parameters.

        Returns:
            Dict describing the created order (must contain ``id``).

        Raises:
            InsufficientBalanceError: when funds are lacking.
            ExchangeError:           on any exchange-level failure.
        """

    @abstractmethod
    async def cancel_order(
        self, order_id: str, symbol: str = ""
    ) -> bool:
        """Cancel an open order.

        Args:
            order_id: Exchange-assigned order id.
            symbol:   Some exchanges require the pair.

        Returns:
            True if cancellation succeeded.
        """

    # -- positions -----------------------------------------------------------

    @abstractmethod
    async def get_positions(
        self, symbol: str = ""
    ) -> List[Dict[str, Any]]:
        """List open positions.

        Args:
            symbol: Filter by pair.  Empty = all.

        Returns:
            List of position dicts.
        """
