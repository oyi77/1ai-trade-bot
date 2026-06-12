"""
Abstract Repository Interface — database-agnostic persistence layer.

All services MUST depend on this interface, never on SQLiteStorage directly.
Switch DB backend by changing settings.DATABASE_BACKEND.

Methods match what services actually need (from analysis of 5 services).
Add methods here as new requirements emerge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Repository(ABC):
    """Abstract database repository."""

    @abstractmethod
    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a write statement (INSERT/UPDATE/DELETE/CREATE)."""

    @abstractmethod
    def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        """Fetch a single row."""

    @abstractmethod
    def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Fetch all matching rows."""

    @abstractmethod
    def insert(self, table: str, data: dict) -> int:
        """Insert a row and return rowid."""

    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the current schema."""


class SQLiteRepository(Repository):
    """SQLite implementation via existing SQLiteStorage wrapper."""

    def __init__(self) -> None:
        from tradebot.storage.sqlite import SQLiteStorage
        self._store = SQLiteStorage()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        return self._store.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        return self._store.fetchone(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self._store.fetchall(sql, params)

    def insert(self, table: str, data: dict) -> int:
        return self._store.insert(table, data)

    def table_exists(self, table_name: str) -> bool:
        return self._store.table_exists(table_name)


_BACKENDS: dict[str, type[Repository]] = {
    "sqlite": SQLiteRepository,
}


def get_repo(backend: str | None = None) -> Repository:
    """Get a Repository instance for the configured backend.

    Backend is determined by:
        1. Explicit `backend` argument
        2. settings.DATABASE_BACKEND (from .env)
        3. Default: "sqlite"

    To switch to PostgreSQL/SQLModel later:
        1. Create PostgresRepository(Repository)
        2. Register it: _BACKENDS["postgres"] = PostgresRepository
        3. Set DATABASE_BACKEND=postgres in .env
    """
    if backend is None:
        from tradebot.config import settings
        backend = getattr(settings, "DATABASE_BACKEND", "sqlite")

    cls = _BACKENDS.get(backend)
    if not cls:
        msg = f"Unknown database backend: {backend!r} (available: {list(_BACKENDS.keys())})"
        raise ValueError(msg)
    return cls()