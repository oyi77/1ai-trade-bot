"""Paper trading provider — simulated trading for testing and strategy validation.

Maintains virtual balance, simulates order fills, and tracks positions with
calculated P&L. Used for strategy backtesting and integration tests without
exposing real capital.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from trading_bot.providers.base import (
    BaseProvider,
    Candle,
    MarketType,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_BALANCE = 10_000.0


class PaperTradingProvider(BaseProvider):
    """Simulated provider for testing strategies without real money.

    Args:
        initial_balance: Starting virtual balance in base currency.
        name: Provider name (default ``'paper'``).
    """

    def __init__(
        self,
        initial_balance: float = DEFAULT_BALANCE,
        name: str = "paper",
    ) -> None:
        self._name = name
        self._balance = initial_balance
        self._equity = initial_balance
        self._positions: list[Position] = []
        self._orders: dict[str, Order] = {}
        self._order_results: dict[str, OrderResult] = {}
        self._candles: dict[str, list[Candle]] = {}
        self._connected = False
        self._symbols: list[str] = []

    # ------------------------------------------------------------------
    #  BaseProvider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def market_type(self) -> MarketType:
        return MarketType.FOREX

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected

    async def get_balance(self) -> float:
        return self._balance

    async def get_positions(self) -> list[Position]:
        self._recalculate_positions()
        return list(self._positions)

    async def place_order(self, order: Order) -> OrderResult:
        self._validate_order(order)
        order_id = _new_id()
        self._orders[order_id] = order

        result = self._simulate_fill(order_id, order)
        self._order_results[order_id] = result
        return result

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            result = self._order_results.get(order_id)
            if result and result.status in (
                OrderStatus.PENDING,
                OrderStatus.OPEN,
            ):
                result.status = OrderStatus.CANCELLED
            del self._orders[order_id]
            return True
        return False

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        candles = self._candles.get(symbol, [])
        return candles[-limit:]

    async def get_symbols(self) -> list[str]:
        return list(self._symbols)

    # ------------------------------------------------------------------
    #  Test helpers (not part of BaseProvider)
    # ------------------------------------------------------------------

    def set_balance(self, balance: float) -> None:
        """Set virtual balance directly (for test setup)."""
        self._balance = balance

    def inject_candles(self, symbol: str, candles: Sequence[Candle]) -> None:
        """Inject candle data for a symbol."""
        self._candles.setdefault(symbol, []).extend(candles)
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    def get_order_result(self, order_id: str) -> OrderResult | None:
        """Get result for a previously placed order."""
        return self._order_results.get(order_id)

    @property
    def equity(self) -> float:
        """Current equity = balance + unrealized P&L."""
        self._recalculate_positions()
        return self._equity

    # ------------------------------------------------------------------
    #  Internal
    # ------------------------------------------------------------------

    def _validate_order(self, order: Order) -> None:
        if order.quantity <= 0:
            raise ValueError(f"Invalid quantity: {order.quantity}")
        if not order.symbol:
            raise ValueError("Symbol is required")

    def _simulate_fill(self, order_id: str, order: Order) -> OrderResult:
        """Simulate filling an order. Returns the fill result."""
        candles = self._candles.get(order.symbol, [])
        current_price = candles[-1].close if candles else 100.0

        if order.order_type == OrderType.MARKET:
            return self._fill_market(order_id, order, current_price)
        if order.order_type == OrderType.LIMIT:
            return self._fill_limit(order_id, order, current_price)
        if order.order_type == OrderType.STOP:
            return self._fill_stop(order_id, order, current_price)
        return OrderResult(
            order_id=order_id,
            status=OrderStatus.REJECTED,
            message=f"Unsupported order type: {order.order_type.value}",
        )

    def _fill_market(self, order_id: str, order: Order, price: float) -> OrderResult:
        fill_price = price
        cost = fill_price * order.quantity

        if self._balance < cost:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                message=f"Insufficient balance: need {cost:.2f}, have {self._balance:.2f}",
            )

        self._balance -= cost
        pos = Position(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            entry_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            leverage=order.leverage,
        )
        self._positions.append(pos)

        return OrderResult(
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            filled_price=fill_price,
            message=f"Market {order.side.value} filled at {fill_price:.2f}",
        )

    def _fill_limit(self, order_id: str, order: Order, current_price: float) -> OrderResult:
        if order.price is None:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                message="Limit order requires a price",
            )

        triggered = (
            order.side == OrderSide.BUY and current_price <= order.price
        ) or (
            order.side == OrderSide.SELL and current_price >= order.price
        )
        if triggered:
            return self._fill_market(order_id, order, order.price)

        return OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            message=f"Limit not reached: current={current_price:.2f}, limit={order.price:.2f}",
        )

    def _fill_stop(self, order_id: str, order: Order, current_price: float) -> OrderResult:
        if order.stop_price is None:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                message="Stop order requires a stop price",
            )

        triggered = (
            order.side == OrderSide.BUY and current_price >= order.stop_price
        ) or (
            order.side == OrderSide.SELL and current_price <= order.stop_price
        )
        if triggered:
            fill_price = order.price or current_price
            return self._fill_market(order_id, order, fill_price)

        return OrderResult(
            order_id=order_id,
            status=OrderStatus.PENDING,
            message=f"Stop not triggered: current={current_price:.2f}, stop={order.stop_price:.2f}",
        )

    def _recalculate_positions(self) -> None:
        """Update position current_price, unrealized P&L from latest candle close."""
        total_upnl = 0.0
        for pos in self._positions:
            candles = self._candles.get(pos.symbol)
            if candles:
                pos.current_price = candles[-1].close
            price_diff = pos.current_price - pos.entry_price
            if pos.side == OrderSide.SELL:
                price_diff = -price_diff
            pos.unrealized_pnl = price_diff * pos.quantity * pos.leverage
            total_upnl += pos.unrealized_pnl

        self._equity = self._balance + total_upnl


def _new_id() -> str:
    return uuid.uuid4().hex[:12]
