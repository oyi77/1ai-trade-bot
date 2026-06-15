"""Tests for trading_bot.persistence — SQLite store."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_bot.persistence import PersistenceStore
from trading_bot.providers.base import (
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from trading_bot.strategies.base import StrategySignal


@pytest.fixture
async def store(tmp_path: Path) -> PersistenceStore:
    """Yield an open persistence store backed by a temp database."""
    db = PersistenceStore(tmp_path / "test.db")
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


class TestPersistenceStoreLifecycle:
    """Store connect / close / context manager."""

    async def test_connect_creates_tables(self, tmp_path: Path) -> None:
        db = PersistenceStore(tmp_path / "tables.db")
        await db.connect()
        tables = await db._conn.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        names = {row["name"] for row in tables}
        assert {"signals", "orders", "positions"}.issubset(names)
        await db.close()

    async def test_async_context_manager(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ctx.db"
        async with PersistenceStore(db_path) as db:
            tables = await db._conn.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            assert any(row["name"] == "signals" for row in tables)


class TestSignalPersistence:
    """Save and retrieve signals."""

    async def test_save_signal(self, store: PersistenceStore) -> None:
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.85,
            price=2500.0,
            strategy_name="grid",
        )
        row_id = await store.save_signal(signal)
        assert row_id > 0

    async def test_save_signal_with_timestamp(self, store: PersistenceStore) -> None:
        signal = StrategySignal(
            symbol="BTC/USD",
            direction=OrderSide.SELL,
            confidence=0.5,
            price=None,
            strategy_name="trend",
        )
        ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
        await store.save_signal(signal, timestamp=ts)
        rows = await store.get_signals(symbol="BTC/USD")
        assert len(rows) == 1
        assert rows[0]["timestamp"] == ts.isoformat()

    async def test_get_signals_by_symbol(self, store: PersistenceStore) -> None:
        for sym in ("XAU/USD", "BTC/USD"):
            await store.save_signal(StrategySignal(
                symbol=sym,
                direction=OrderSide.BUY,
                confidence=0.5,
                price=100.0,
                strategy_name="test",
            ))
        rows = await store.get_signals(symbol="XAU/USD")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "XAU/USD"

    async def test_get_signals_limit(self, store: PersistenceStore) -> None:
        for i in range(5):
            await store.save_signal(StrategySignal(
                symbol="XAU/USD",
                direction=OrderSide.BUY,
                confidence=0.5,
                price=float(i),
                strategy_name="test",
            ))
        rows = await store.get_signals(limit=2)
        assert len(rows) == 2


class TestOrderPersistence:
    """Save and retrieve orders."""

    async def test_save_order(self, store: PersistenceStore) -> None:
        order = Order(
            symbol="XAU/USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            price=2500.0,
        )
        result = OrderResult(
            order_id="ord-1",
            status=OrderStatus.FILLED,
            filled_quantity=1.0,
            filled_price=2500.0,
        )
        row_id = await store.save_order(order, result)
        assert row_id > 0

    async def test_get_orders(self, store: PersistenceStore) -> None:
        order = Order(
            symbol="BTC/USD",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.1,
        )
        result = OrderResult(order_id="ord-2", status=OrderStatus.REJECTED)
        await store.save_order(order, result)
        rows = await store.get_orders(symbol="BTC/USD")
        assert len(rows) == 1
        assert rows[0]["order_id"] == "ord-2"
        assert rows[0]["status"] == "rejected"


class TestPositionPersistence:
    """Save and retrieve positions."""

    async def test_save_position(self, store: PersistenceStore) -> None:
        position = Position(
            symbol="XAU/USD",
            side=OrderSide.BUY,
            quantity=1.0,
            entry_price=2500.0,
            current_price=2510.0,
            unrealized_pnl=10.0,
            realized_pnl=0.0,
        )
        row_id = await store.save_position(position)
        assert row_id > 0

    async def test_get_positions_open(self, store: PersistenceStore) -> None:
        position = Position(
            symbol="BTC/USD",
            side=OrderSide.BUY,
            quantity=0.5,
            entry_price=30_000.0,
            current_price=31_000.0,
            unrealized_pnl=500.0,
            realized_pnl=0.0,
        )
        await store.save_position(position)
        rows = await store.get_positions(status="open")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC/USD"

class TestConnectionBackends:
    """Backend-specific coverage for aiosqlite and threaded fallback."""

    async def test_aiosqlite_execute_without_connect(self) -> None:
        """Calling execute on an unopened _AiosqliteConnection raises RuntimeError."""
        from trading_bot.persistence import _AiosqliteConnection

        conn = _AiosqliteConnection()
        with pytest.raises(RuntimeError, match="not open"):
            await conn.execute("SELECT 1")

    async def test_threaded_execute_without_connect(self) -> None:
        """Calling execute on an unopened _ThreadedConnection raises RuntimeError."""
        from trading_bot.persistence import _ThreadedConnection

        conn = _ThreadedConnection()
        with pytest.raises(RuntimeError, match="not open"):
            await conn.execute("SELECT 1")

    async def test_threaded_fallback(self, tmp_path: Path) -> None:
        """PersistenceStore falls back to the threaded sqlite3 backend."""
        from trading_bot.persistence import _AIOSQLITE_AVAILABLE

        original = _AIOSQLITE_AVAILABLE
        import trading_bot.persistence as persistence_module

        persistence_module._AIOSQLITE_AVAILABLE = False
        try:
            db = PersistenceStore(tmp_path / "fallback.db")
            await db.connect()
            try:
                signal = StrategySignal(
                    timestamp=datetime.now(timezone.utc),
                    symbol="ETH/USD",
                    direction="long",
                    confidence=0.85,
                    price=1800.0,
                    strategy_name="test-fallback",
                )
                row_id = await db.save_signal(signal)
                assert row_id > 0

                rows = await db.get_signals(symbol="ETH/USD")
                assert len(rows) == 1
                assert rows[0]["symbol"] == "ETH/USD"
                assert rows[0]["direction"] == "long"
            finally:
                await db.close()
        finally:
            persistence_module._AIOSQLITE_AVAILABLE = original
