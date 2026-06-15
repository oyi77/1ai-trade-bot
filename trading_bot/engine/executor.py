"""Signal executor — converts strategy signals into orders and manages execution.

Subscribes to signal events on the EventBus, sizes and validates orders
through the RiskManager, places them via a provider, and tracks the
result through the PortfolioTracker.
"""

from __future__ import annotations

import logging
from typing import Any

from trading_bot.engine.events import (
    ORDER_PLACED,
    POSITION_OPENED,
    SIGNAL,
    Event,
    EventBus,
    order_event,
    position_event,
)
from trading_bot.engine.portfolio import PortfolioTracker
from trading_bot.engine.risk import RiskManager
from trading_bot.providers.base import (
    BaseProvider,
    Order,
    OrderSide,
    OrderType,
    Position,
)
from trading_bot.strategies.base import StrategySignal

LOG = logging.getLogger(__name__)


class SignalExecutor:
    """Listens for strategy signals and executes them as trades.

    The executor subscribes to ``SIGNAL`` events on the bus, sizes the
    position using ``RiskManager``, validates the resulting order, places
    it through the provider, and records the result in the portfolio.
    """

    def __init__(
        self,
        provider: BaseProvider,
        risk_manager: RiskManager,
        portfolio: PortfolioTracker,
        event_bus: EventBus | None = None,
    ) -> None:
        self._provider = provider
        self._risk = risk_manager
        self._portfolio = portfolio
        self._event_bus = event_bus
        self._running = False
        self._last_order_result: dict[str, Any] | None = None

    # ── lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe to signal events and begin processing."""
        if self._event_bus is not None:
            self._event_bus.subscribe(SIGNAL, self._on_signal)
        self._running = True
        LOG.info("SignalExecutor started")

    async def stop(self) -> None:
        """Unsubscribe and stop processing."""
        if self._event_bus is not None:
            self._event_bus.unsubscribe(SIGNAL, self._on_signal)
        self._running = False
        LOG.info("SignalExecutor stopped")

    # ── direct execution (bypasses event bus) ─────────────────────────

    async def execute(self, signal: StrategySignal) -> dict[str, Any] | None:
        """Execute a strategy signal directly.

        This is the main entry point — called either from the event
        handler or directly by the orchestrator.

        Returns:
            A dict with execution details (order_id, status, filled price)
            or ``None`` if the signal was rejected.
        """
        if not self._running:
            LOG.warning("Executor not running, rejecting signal")
            return None

        # 1. Calculate position size (returns dollar risk amount).
        balance = self._portfolio.balance
        price = signal.price or 0.0
        risk_amount = self._risk.calculate_position_size(
            balance=balance,
            price=price,
        )
        if risk_amount <= 0:
            LOG.debug("Signal %s rejected: position size is 0", signal.strategy_name)
            return None

        # 2. Convert risk amount to units (lots / shares / contracts).
        units = risk_amount / price if price > 0 else risk_amount
        if units <= 0:
            return None

        # 3. Build order.
        order = Order(
            symbol=signal.symbol,
            side=signal.direction,
            order_type=OrderType.MARKET,
            quantity=units,
            price=price if price > 0 else None,
        )

        # 3. Validate with risk manager.
        positions = self._portfolio.get_positions(signal.symbol)
        approved, reason = self._risk.validate_order(order, positions, balance)
        if not approved:
            LOG.debug("Signal %s rejected by risk: %s", signal.strategy_name, reason)
            return None

        # 4. Place order.
        result = await self._provider.place_order(order)
        self._last_order_result = {
            "order_id": result.order_id,
            "status": result.status.value,
            "filled_quantity": result.filled_quantity,
            "filled_price": result.filled_price,
            "symbol": signal.symbol,
            "strategy": signal.strategy_name,
        }

        # 5. Emit events.
        if self._event_bus is not None:
            await self._event_bus.publish(
                ORDER_PLACED,
                **order_event(result, "placed").data,
            )

        # 6. Track position if filled.
        if result.status.value in ("filled", "pending"):
            pos = Position(
                symbol=signal.symbol,
                side=signal.direction,
                quantity=result.filled_quantity,
                entry_price=result.filled_price or price,
                current_price=result.filled_price or price,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
            )
            self._portfolio.add_position(pos)

            if self._event_bus is not None:
                await self._event_bus.publish(
                    POSITION_OPENED,
                    **position_event(pos, "opened").data,
                )

        return self._last_order_result

    # ── event handler ─────────────────────────────────────────────────

    async def _on_signal(self, event: Event) -> None:
        """Event bus callback — unwrap signal data and execute."""
        data = event.data
        signal = StrategySignal(
            symbol=data.get("symbol", ""),
            direction=OrderSide(data.get("direction", "buy")),
            confidence=data.get("confidence", 0.5),
            price=data.get("price"),
            strategy_name=data.get("strategy", "unknown"),
        )
        await self.execute(signal)

    # ── status ────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return current executor state."""
        return {
            "running": self._running,
            "last_order": self._last_order_result,
        }
