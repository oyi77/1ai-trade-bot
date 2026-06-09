"""Tests for tradebot.events — EventBus publish/subscribe."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from tradebot.events import EventBus, bus

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def ebus() -> EventBus:
    """Fresh EventBus instance per test."""
    return EventBus()


# ------------------------------------------------------------------
# publish / subscribe basics
# ------------------------------------------------------------------

def test_publish_no_subscribers_no_crash(ebus: EventBus):
    """publish() with zero subscribers must silently succeed."""
    ebus.publish("tick", symbol="XAUUSD", bid=1950.0)  # no exception


def test_subscribe_and_publish_delivers(ebus: EventBus):
    """Handler receives event_type keyword and all published data."""
    handler = MagicMock()
    ebus.subscribe("tick", handler)
    ebus.publish("tick", symbol="XAUUSD", bid=1950.0)

    handler.assert_called_once_with(event_type="tick", symbol="XAUUSD", bid=1950.0)


def test_unsubscribe_removes_handler(ebus: EventBus):
    """After unsubscribe, the handler is no longer called."""
    handler = MagicMock()
    sid = ebus.subscribe("tick", handler)
    ebus.unsubscribe(sid)

    ebus.publish("tick", data=1)
    handler.assert_not_called()


def test_unsubscribe_nonexistent_is_noop(ebus: EventBus):
    """Unsubscribing an unknown ID must not raise."""
    ebus.unsubscribe("does_not_exist")  # no exception


def test_clear_removes_all_subscriptions(ebus: EventBus):
    """clear() drops every handler on every event type."""
    h1 = MagicMock()
    h2 = MagicMock()
    ebus.subscribe("tick", h1)
    ebus.subscribe("trade_opened", h2)

    ebus.clear()

    ebus.publish("tick", data=1)
    ebus.publish("trade_opened", data=2)
    h1.assert_not_called()
    h2.assert_not_called()


def test_multiple_subscribers_same_event(ebus: EventBus):
    """All handlers for a given event_type are invoked."""
    h1 = MagicMock()
    h2 = MagicMock()
    ebus.subscribe("tick", h1)
    ebus.subscribe("tick", h2)

    ebus.publish("tick", price=100)

    h1.assert_called_once_with(event_type="tick", price=100)
    h2.assert_called_once_with(event_type="tick", price=100)


def test_handler_exception_does_not_crash_bus(ebus: EventBus):
    """A raising handler must not prevent other handlers from running."""
    bad = MagicMock(side_effect=RuntimeError("boom"))
    good = MagicMock()
    ebus.subscribe("tick", bad)
    ebus.subscribe("tick", good)

    ebus.publish("tick", x=1)  # must not propagate

    bad.assert_called_once()
    good.assert_called_once()


# ------------------------------------------------------------------
# Async handler support
# ------------------------------------------------------------------

def test_async_handler_called_via_sync_publish(ebus: EventBus):
    """Coroutine functions are detected and executed (no running loop)."""
    results: list[str] = []

    async def handler(event_type: str, **kw):
        results.append(event_type)

    ebus.subscribe("tick", handler)
    ebus.publish("tick", val=42)

    assert results == ["tick"]


@pytest.mark.asyncio
async def test_async_handler_scheduled_in_running_loop(ebus: EventBus):
    """When an event loop is already running, coroutine is scheduled."""
    event = asyncio.Event()

    async def handler(event_type: str, **kw):
        event.set()

    ebus.subscribe("tick", handler)
    ebus.publish("tick")

    await asyncio.wait_for(event.wait(), timeout=2.0)


# ------------------------------------------------------------------
# Thread-safety
# ------------------------------------------------------------------

def test_concurrent_publish_subscribe_thread_safe(ebus: EventBus):
    """Concurrent subscribe + publish from multiple threads must not crash
    and every handler must be invoked at least once."""
    thread_count = 20
    publishes_per = 50
    received: list[str] = []
    lock = threading.Lock()

    def handler(event_type: str, tid: int = 0, **kw):
        with lock:
            received.append(f"t{tid}")

    # Pre-subscribe many handlers
    for _ in range(thread_count):
        ebus.subscribe("tick", handler)

    errors: list[Exception] = []

    def publisher(tid: int):
        try:
            for _ in range(publishes_per):
                ebus.publish("tick", tid=tid)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=publisher, args=(i,)) for i in range(thread_count)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Unexpected errors in threads: {errors}"
    # Each handler called thread_count * publishes_per times
    assert len(received) == thread_count * thread_count * publishes_per


def test_concurrent_subscribe_unsubscribe_thread_safe(ebus: EventBus):
    """Rapid subscribe/unsubscribe from multiple threads must not raise."""
    n = 100

    def sub_unsub(tid: int):
        for _ in range(n):
            sid = ebus.subscribe("tick", lambda event_type, **kw: None)
            ebus.unsubscribe(sid)

    threads = [
        threading.Thread(target=sub_unsub, args=(i,)) for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

def test_module_level_bus_singleton_exists():
    """The module exports a ready-to-use `bus` instance."""
    assert isinstance(bus, EventBus)
