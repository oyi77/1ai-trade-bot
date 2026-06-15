"""In-process event system — typed event dataclasses and async EventBus.

The event bus decouples signal producers (strategies) from consumers
(portfolio tracker, risk manager, executor).  Handlers are async
callables that receive an ``Event`` instance.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from trading_bot.providers.base import OrderResult, Position
from trading_bot.strategies.base import StrategySignal

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Event types (string constants)
# ---------------------------------------------------------------------------

SIGNAL = "signal"          # A strategy produced a signal
ORDER_PLACED = "order"     # An order was placed
ORDER_FILLED = "filled"    # An order was filled
POSITION_OPENED = "pos_open"   # A position was opened
POSITION_CLOSED = "pos_close"  # A position was closed
ERROR = "error"            # An error occurred
ENGINE_STATE = "state"     # Engine state changed

# ---------------------------------------------------------------------------
#  Event dataclass
# ---------------------------------------------------------------------------


@dataclass
class Event:
    """A single event in the engine's event system.

    Attributes:
        type: Event type string (one of the module-level constants).
        data: Arbitrary payload carried with the event.
        timestamp: When the event was created.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),  # noqa: UP017
    )


# Convenience factories


def signal_event(signal: StrategySignal) -> Event:
    """Create an event for a new trading signal."""
    return Event(
        type=SIGNAL,
        data={
            "symbol": signal.symbol,
            "direction": signal.direction.value,
            "confidence": signal.confidence,
            "price": signal.price,
            "strategy": signal.strategy_name,
        },
    )


def order_event(result: OrderResult, action: str = "placed") -> Event:
    """Create an event for an order lifecycle change."""
    return Event(
        type=ORDER_PLACED if action == "placed" else ORDER_FILLED,
        data={
            "order_id": result.order_id,
            "status": result.status.value,
            "filled_quantity": result.filled_quantity,
            "filled_price": result.filled_price,
            "action": action,
        },
    )


def position_event(position: Position, action: str = "opened") -> Event:
    """Create an event for a position lifecycle change."""
    return Event(
        type=POSITION_OPENED if action == "opened" else POSITION_CLOSED,
        data={
            "symbol": position.symbol,
            "side": position.side.value,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "current_price": position.current_price,
            "unrealized_pnl": position.unrealized_pnl,
            "realized_pnl": position.realized_pnl,
        },
    )


# ---------------------------------------------------------------------------
#  Async event bus
# ---------------------------------------------------------------------------

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Async publish/subscribe event bus.

    Handlers are awaited in registration order.  A failing handler does
    not prevent subsequent handlers from running — errors are logged.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register an async handler for *event_type*."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, **data: Any) -> None:
        """Publish an event to all subscribers of *event_type*.

        Additional keyword arguments are passed as the event's data dict.
        """
        event = Event(type=event_type, data=data)
        for handler in list(self._handlers.get(event_type, [])):
            try:
                await handler(event)
            except Exception:
                LOG.exception("Event handler failed for %s", event_type)

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()
