"""MockProvider — fake implementation of BaseProvider for testing.

Simulates all provider operations with configurable responses
so real API keys / network connections are never needed.
"""

from __future__ import annotations

from trading_bot.providers.base import (
    BaseProvider,
    Candle,
    MarketType,
    Order,
    OrderResult,
    OrderStatus,
    Position,
)


class MockProvider(BaseProvider):
    """Fake provider for unit tests.

    Each method accepts kwargs to override specific return values
    on a per-call basis. Default responses simulate a healthy
    connected provider with a 10,000 balance.

    Args:
        name: Provider name.
        fail_connect: If True, ``connect()`` returns False.
    """

    def __init__(
        self,
        name: str = "mock",
        fail_connect: bool = False,
    ) -> None:
        self._name = name
        self._fail_connect = fail_connect
        self._connected = False
        self._balance = 10_000.0
        self._last_order: Order | None = None
        self._cancelled_order_ids: list[str] = []
        self._positions: list[Position] = []
        self._symbols: list[str] = ["EUR/USD", "BTC/USD", "XAU/USD"]
        self._stored_candles: list[Candle] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def market_type(self) -> MarketType:
        return MarketType.FOREX

    async def connect(self) -> bool:
        if self._fail_connect:
            return False
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected

    async def get_balance(self) -> float:
        return self._balance

    async def get_positions(self) -> list[Position]:
        return list(self._positions)

    async def place_order(self, order: Order) -> OrderResult:
        self._last_order = order
        if order.quantity <= 0:
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                message="Invalid quantity",
            )
        if not order.symbol:
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                message="Symbol required",
            )
        return OrderResult(
            order_id="mock-order-001",
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            filled_price=100.0,
            message="Mock order filled",
        )

    async def cancel_order(self, order_id: str) -> bool:
        self._cancelled_order_ids.append(order_id)
        return True

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        return self._stored_candles[:limit]

    async def get_symbols(self) -> list[str]:
        return list(self._symbols)

    # ------------------------------------------------------------------
    #  Test helpers
    # ------------------------------------------------------------------

    @property
    def last_order(self) -> Order | None:
        """Return the most recently placed order."""
        return self._last_order

    def set_balance(self, amount: float) -> None:
        """Set mock balance."""
        self._balance = amount

    def add_position(self, pos: Position) -> None:
        """Add a position to the mock position list."""
        self._positions.append(pos)

    def clear(self) -> None:
        """Reset mock state."""
        self._balance = 10_000.0
        self._last_order = None
        self._cancelled_order_ids.clear()
        self._positions.clear()
        self._symbols.clear()
        self._stored_candles.clear()

    def _inject_candles(self, candles: list[Candle]) -> None:
        """Inject candles that will be returned by get_candles."""
        self._stored_candles = candles
