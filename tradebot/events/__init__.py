"""
Event Bus — lightweight publish/subscribe for in-process events.

Thread-safe. Supports both sync and async (coroutine) handlers.
Handler errors are caught and logged; they never crash the bus.

Internal event types:
    - ``tick``              — Market tick received
    - ``signal_generated``  — A new trading signal was produced
    - ``trade_opened``      — A trade was opened
    - ``trade_closed``      — A trade was closed (win/loss)
    - ``error``             — An error occurred somewhere in the system
    - ``health_status_change`` — Health monitor status changed
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[..., Any | Coroutine[Any, Any, None]]


class EventBus:
    """In-process publish/subscribe event bus.

    Usage::

        bus = EventBus()

        # Subscribe
        sid = bus.subscribe("tick", my_handler)

        # Publish
        bus.publish("tick", symbol="XAUUSD", bid=1950.0, ask=1950.5)

        # Unsubscribe
        bus.unsubscribe(sid)

        # Clear all subscriptions
        bus.clear()
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, dict[str, Handler]] = defaultdict(dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, event_type: str, **data: Any) -> None:
        """Fire an event.  All handlers registered for *event_type* are
        called synchronously (blocking).  Async handlers are scheduled
        on the current event loop if one is running, otherwise a new
        event loop is created."""
        with self._lock:
            handlers = list(self._subscriptions[event_type].values())

        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    self._run_async(handler, event_type, data)
                else:
                    handler(event_type=event_type, **data)
            except Exception:
                logger.exception(
                    "EventBus: handler %r failed for event %r",
                    getattr(handler, "__name__", handler),
                    event_type,
                )

    def subscribe(self, event_type: str, handler: Handler) -> str:
        """Register *handler* for *event_type*.

        Returns a unique subscription ID that can be passed to
        :meth:`unsubscribe`.
        """
        sid = uuid.uuid4().hex
        with self._lock:
            self._subscriptions[event_type][sid] = handler
        return sid

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by its ID."""
        with self._lock:
            for handlers in self._subscriptions.values():
                if subscription_id in handlers:
                    del handlers[subscription_id]
                    return

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscriptions.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(
        handler: Handler,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Schedule or run an async handler."""
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — create one and run the coroutine
            asyncio.run(handler(event_type=event_type, **data))
        else:
            # Running loop — schedule the coroutine
            asyncio.ensure_future(
                handler(event_type=event_type, **data)
            )


# ------------------------------------------------------------------
# Module-level singleton (convenience)
# ------------------------------------------------------------------
bus: EventBus = EventBus()


__all__ = [
    "EventBus",
    "bus",
]
