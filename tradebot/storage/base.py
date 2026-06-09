"""
AbstractStorage — abstract base class for tradebot persistence.

Defines a minimum CRUD contract that every storage backend (SQLite, Redis,
in-memory, etc.) must implement.  Carries structured exception types from
``tradebot.exceptions`` so callers never need to catch raw DB errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AbstractStorage(ABC):
    """Abstract base class for all tradebot storage backends.

    Implementations must provide safe (thread-safe where applicable) CRUD
    for arbitrary records, plus a small set of lifecycle helpers.

    Conventions
    -----------
    * All public methods raise ``StorageError`` (or a subclass thereof) on
      failure — never raw ``sqlite3.OperationalError`` or similar.
    * ``collection`` is loosely equivalent to a SQL table or a Redis set.
    * ``record_id`` is a primary key (UUID string, integer, etc.) —
      callers choose the scheme; the backend indexes it.
    """

    # ── Lifecycle ────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> bool:
        """Open / initialise the backend connection.

        Returns:
            ``True`` if the connection was established (or was already
            alive), ``False`` otherwise.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully close the backend connection and release resources."""
        ...

    @property
    @abstractmethod
    def connected(self) -> bool:
        """``True`` when the backend connection is alive and usable."""
        ...

    # ── CRUD ─────────────────────────────────────────────────────────────

    @abstractmethod
    def create(
        self,
        collection: str,
        record: dict[str, Any],
        *,
        record_id: str | None = None,
    ) -> str:
        """Insert a new record.

        Args:
            collection: Target namespace (table / bucket / collection).
            record: Key-value data to persist.
            record_id: Optional explicit primary key.  When omitted the
                backend auto-generates one.

        Returns:
            The record ID of the newly created record.

        Raises:
            StorageError: On any backend failure.
        """
        ...

    @abstractmethod
    def read(
        self,
        collection: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a single record by ID.

        Returns:
            The record dict, or ``None`` when the record does not exist.

        Raises:
            StorageError: On any backend failure.
        """
        ...

    @abstractmethod
    def update(
        self,
        collection: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """Partially update an existing record.

        Args:
            collection: Target namespace.
            record_id: Primary key of the record to modify.
            updates: Key-value pairs to merge into the existing record.

        Returns:
            ``True`` if a record was actually updated, ``False`` if it did
            not exist (no-op).

        Raises:
            StorageError: On any backend failure.
        """
        ...

    @abstractmethod
    def delete(
        self,
        collection: str,
        record_id: str,
    ) -> bool:
        """Remove a single record.

        Returns:
            ``True`` if a record was actually deleted, ``False`` if it did
            not exist (no-op).

        Raises:
            StorageError: On any backend failure.
        """
        ...

    @abstractmethod
    def list(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List records in a collection with optional filtering.

        Args:
            collection: Target namespace.
            filters: Key-value pairs to match (exact equality).
            limit: Maximum number of records to return.
            offset: Number of records to skip.

        Returns:
            A list of matching record dicts (may be empty).

        Raises:
            StorageError: On any backend failure.
        """
        ...

    # ── Utilities ────────────────────────────────────────────────────────

    @abstractmethod
    def count(self, collection: str) -> int:
        """Return the total number of records in *collection*.

        Raises:
            StorageError: On any backend failure.
        """
        ...

    @abstractmethod
    def clear(self, collection: str) -> int:
        """Remove **all** records from *collection*.

        Returns:
            The number of records removed.

        Raises:
            StorageError: On any backend failure.
        """
        ...


__all__ = [
    "AbstractStorage",
]
