"""SQLite persistence for trading signals, orders, and positions.

The store is fully async.  ``aiosqlite`` is used when available; otherwise a
thin fallback wraps the standard threaded ``sqlite3`` module with
``asyncio.to_thread`` so callers always see the same async API.
"""

from __future__ import annotations

import asyncio
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path
from typing import Any

from trading_bot.providers.base import Order, OrderResult, Position
from trading_bot.strategies.base import StrategySignal

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

try:
    import aiosqlite

    _AIOSQLITE_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback exercised when absent
    aiosqlite = None  # type: ignore[assignment]
    _AIOSQLITE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Current UTC timestamp."""
    return datetime.now(timezone.utc)  # noqa: UP017


def _to_iso(dt: datetime | None) -> str:
    """Serialize a datetime as an ISO 8601 string, preserving timezone info."""
    if dt is None:
        return _now().isoformat()
    return dt.isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row into a plain dict."""
    return dict(row)


# ---------------------------------------------------------------------------
# Async backend interface
# ---------------------------------------------------------------------------

class _AsyncConnection(ABC):
    """Minimal async connection contract shared by aiosqlite and the fallback."""

    @abstractmethod
    async def connect(self, path: str | Path) -> None:
        """Open the database and configure the connection."""

    @abstractmethod
    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | None = None,
    ) -> Any:
        """Execute a single SQL statement and return the cursor."""

    @abstractmethod
    async def close(self) -> None:
        """Close the connection."""

    @abstractmethod
    async def fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute ``sql`` and return all rows as dicts."""


class _AiosqliteConnection(_AsyncConnection):
    """Async wrapper around the real ``aiosqlite`` package."""

    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None

    async def connect(self, path: str | Path) -> None:
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")

    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | None = None,
    ) -> Any:
        if self._conn is None:
            raise RuntimeError("Database connection is not open")
        params = parameters if parameters is not None else ()
        return await self._conn.execute(sql, params)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = await self.execute(sql, parameters)
        rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]


class _ThreadedConnection(_AsyncConnection):
    """Async wrapper around ``sqlite3`` using ``asyncio.to_thread``."""

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None

    async def connect(self, path: str | Path) -> None:
        def _open() -> sqlite3.Connection:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        self._conn = await asyncio.to_thread(_open)

    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | None = None,
    ) -> Any:
        if self._conn is None:
            raise RuntimeError("Database connection is not open")
        params = parameters if parameters is not None else ()

        def _exec(conn: sqlite3.Connection) -> sqlite3.Cursor:
            return conn.execute(sql, params)

        return await asyncio.to_thread(_exec, self._conn)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = await self.execute(sql, parameters)

        def _fetch() -> list[sqlite3.Row]:
            return cursor.fetchall()  # type: ignore[no-any-return]

        rows = await asyncio.to_thread(_fetch)
        return [_row_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Public store
# ---------------------------------------------------------------------------

class PersistenceStore:
    """Async SQLite store for trading signals, orders and positions.

    The class is also an async context manager; entering opens the connection
    and exits closes it automatically.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: _AsyncConnection = (
            _AiosqliteConnection() if _AIOSQLITE_AVAILABLE else _ThreadedConnection()
        )

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the database and create tables if they do not exist."""
        await self._conn.connect(self._db_path)

        create_signals = """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            price REAL,
            strategy_name TEXT NOT NULL
        )
        """

        create_orders = """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL,
            order_id TEXT NOT NULL,
            status TEXT NOT NULL,
            filled_quantity REAL NOT NULL,
            filled_price REAL
        )
        """

        create_positions = """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            status TEXT NOT NULL
        )
        """

        await self._conn.execute(create_signals)
        await self._conn.execute(create_orders)
        await self._conn.execute(create_positions)

    async def close(self) -> None:
        """Close the database connection."""
        await self._conn.close()

    async def __aenter__(self) -> PersistenceStore:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ── writers ────────────────────────────────────────────────────────────

    async def save_signal(
        self,
        signal: StrategySignal,
        timestamp: datetime | None = None,
    ) -> int:
        """Persist a strategy signal and return the inserted row id."""
        ts = _to_iso(timestamp or signal.timestamp)
        cursor = await self._conn.execute(
            """
            INSERT INTO signals (timestamp, symbol, direction, confidence, price, strategy_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                signal.symbol,
                str(signal.direction),
                float(signal.confidence),
                float(signal.price) if signal.price is not None else None,
                signal.strategy_name,
            ),
        )
        return cursor.lastrowid or 0

    async def save_order(
        self,
        order: Order,
        result: OrderResult,
        timestamp: datetime | None = None,
    ) -> int:
        """Persist an order together with its execution result."""
        ts = _to_iso(timestamp)
        cursor = await self._conn.execute(
            """
            INSERT INTO orders (
                timestamp, symbol, side, order_type, quantity, price,
                order_id, status, filled_quantity, filled_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                order.symbol,
                str(order.side),
                str(order.order_type),
                float(order.quantity),
                float(order.price) if order.price is not None else None,
                result.order_id,
                str(result.status),
                float(result.filled_quantity),
                float(result.filled_price) if result.filled_price is not None else None,
            ),
        )
        return cursor.lastrowid or 0

    async def save_position(
        self,
        position: Position,
        timestamp: datetime | None = None,
    ) -> int:
        """Persist a position snapshot (default status ``open``)."""
        ts = _to_iso(timestamp or position.timestamp)
        cursor = await self._conn.execute(
            """
            INSERT INTO positions (
                timestamp, symbol, side, quantity, entry_price, current_price,
                unrealized_pnl, realized_pnl, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                position.symbol,
                str(position.side),
                float(position.quantity),
                float(position.entry_price),
                float(position.current_price),
                float(position.unrealized_pnl),
                float(position.realized_pnl),
                "open",
            ),
        )
        return cursor.lastrowid or 0

    # ── readers ────────────────────────────────────────────────────────────

    async def get_signals(
        self,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent signals, optionally filtered by ``symbol``."""
        if symbol is None:
            return await self._conn.fetchall(
                "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?",
                (int(limit),),
            )
        return await self._conn.fetchall(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
            (symbol, int(limit)),
        )

    async def get_orders(
        self,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent orders, optionally filtered by ``symbol``."""
        if symbol is None:
            return await self._conn.fetchall(
                "SELECT * FROM orders ORDER BY timestamp DESC LIMIT ?",
                (int(limit),),
            )
        return await self._conn.fetchall(
            "SELECT * FROM orders WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
            (symbol, int(limit)),
        )

    async def get_positions(self, status: str = "open") -> list[dict[str, Any]]:
        """Return positions filtered by ``status`` (default ``open``)."""
        return await self._conn.fetchall(
            "SELECT * FROM positions WHERE status = ? ORDER BY timestamp DESC",
            (status,),
        )
