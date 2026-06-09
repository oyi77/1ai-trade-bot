"""
TieredCache — two-level hot/cold cache with TTL support.

Architecture
------------
*Layer 1 (hot):*  ``dict`` in memory — sub-microsecond lookups.
*Layer 2 (cold):*  SQLite on disk — survives restarts.

A ``get()`` first hits the in-memory dict; on miss it falls through to
SQLite, promotes the value back into memory, and returns it.  Writes always
hit both tiers so the in-memory tier always has the freshest data.

Thread-safety
-------------
All public methods are guarded by a ``threading.RLock`` so the cache is
safe for concurrent access from multiple threads (e.g. fastapi workers,
background pollers, CLI commands called in threads).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from tradebot.config import settings

LOG = logging.getLogger(__name__)

#: Default TTL when none is supplied (seconds).
_DEFAULT_TTL = 300  # 5 minutes


class TieredCache:
    """Two-tier thread-safe cache with TTL.

    Typical usage::

        cache = TieredCache()
        cache.set("my_key", {"price": 1.2345}, ttl=60)
        value = cache.get("my_key")
        cache.delete("my_key")
        cache.clear()          # nuke everything
        values = cache.get_multi(["k1", "k2", "k3"])   # bulk read

    Parameters
    ----------
    db_path : str or Path, optional
        Path to the SQLite cold-storage file.  Defaults to
        ``DATA_DIR / "cache.db"``.
    default_ttl : int
        Default TTL in seconds when ``set()`` receives no explicit TTL.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        default_ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._lock = threading.RLock()
        self._default_ttl = default_ttl

        # ── Tier 1 (hot) ─────────────────────────────────────────────────
        self._memory: dict[str, _CacheEntry] = {}

        # ── Tier 2 (cold) ────────────────────────────────────────────────
        self._db_path = (
            Path(db_path)
            if db_path
            else Path(settings.DATA_DIR) / "cache.db"
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

        LOG.debug("TieredCache ready  db=%s", self._db_path)

    # ── Public API ───────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Retrieve a value by key.

        Returns ``None`` when the key is missing or expired.
        """
        with self._lock:
            # 1. Try hot tier
            entry = self._memory.get(key)
            if entry is not None:
                if entry.is_expired:
                    del self._memory[key]
                else:
                    return entry.value

            # 2. Fall through to cold tier
            row = self._fetch_sqlite(key)
            if row is None:
                return None

            stored_value, expires_at = row
            if expires_at is not None and time.monotonic() > expires_at:
                self._delete_sqlite(key)
                return None

            # 3. Promote back into hot tier
            self._memory[key] = _CacheEntry(
                value=stored_value,
                expires_at=expires_at,
            )
            return stored_value

    def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
    ) -> None:
        """Store a value.

        Args:
            key: Cache key (str).
            value: Any JSON-serialisable value.
            ttl: Time-to-live in seconds.  ``None`` = default TTL.
        """
        expires_at: float | None = None
        if ttl is None:
            ttl = self._default_ttl
        if ttl > 0:  # noqa: SIM108
            expires_at = time.monotonic() + ttl
        else:
            expires_at = None  # never expires

        with self._lock:
            # Write to both tiers atomically
            self._memory[key] = _CacheEntry(value=value, expires_at=expires_at)
            self._upsert_sqlite(key, value, expires_at)

    def delete(self, key: str) -> bool:
        """Remove a single key from both tiers.

        Returns:
            ``True`` if the key existed in at least one tier.
        """
        with self._lock:
            mem_deleted = self._memory.pop(key, None) is not None
            sql_deleted = self._delete_sqlite(key)
            return mem_deleted or sql_deleted

    def clear(self) -> int:
        """Remove **all** keys from both tiers.

        Returns:
            Total number of entries removed.
        """
        with self._lock:
            mem_count = len(self._memory)
            self._memory.clear()
            sql_count = self._clear_sqlite()
            LOG.debug("TieredCache cleared  (%d mem, %d sqlite)", mem_count, sql_count)
            return mem_count + sql_count

    def get_multi(self, keys: list[str]) -> dict[str, Any | None]:
        """Bulk read — returns a ``{key: value_or_None}`` dict.

        More efficient than N individual ``get()`` calls when the keys
        share the same SQLite page.
        """
        with self._lock:
            result: dict[str, Any | None] = {}
            missed: list[str] = []

            # 1. Satisfy from hot tier
            for k in keys:
                entry = self._memory.get(k)
                if entry is not None and not entry.is_expired:
                    result[k] = entry.value
                elif entry is not None and entry.is_expired:
                    del self._memory[k]
                    missed.append(k)
                else:
                    missed.append(k)

            # 2. Bulk-fetch misses from cold tier
            if missed:
                rows = self._fetch_sqlite_multi(missed)
                for k in missed:
                    row = rows.get(k)
                    if row is None:
                        result[k] = None
                    else:
                        stored_value, expires_at = row
                        if expires_at is not None and time.monotonic() > expires_at:
                            self._delete_sqlite(k)
                            result[k] = None
                        else:
                            # Promote
                            self._memory[k] = _CacheEntry(
                                value=stored_value,
                                expires_at=expires_at,
                            )
                            result[k] = stored_value

            return result

    # ── SQLite internals ─────────────────────────────────────────────────

    def _init_sqlite(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    expires_at  REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)"
            )
            conn.commit()

    def _fetch_sqlite(self, key: str) -> tuple[Any, float | None] | None:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT value, expires_at FROM cache WHERE key = ?",
                    (key,),
                ).fetchone()
        except sqlite3.Error as exc:
            LOG.warning("TieredCache SQLite read error: %s", exc)
            return None

        if row is None:
            return None
        return _deserialize(row[0]), row[1]

    def _fetch_sqlite_multi(
        self,
        keys: list[str],
    ) -> dict[str, tuple[Any, float | None]]:
        rows: dict[str, tuple[Any, float | None]] = {}
        if not keys:
            return rows

        placeholders = ", ".join("?" for _ in keys)
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                for row in conn.execute(
                    f"SELECT key, value, expires_at FROM cache WHERE key IN ({placeholders})",
                    keys,
                ):
                    rows[row[0]] = (_deserialize(row[1]), row[2])
        except sqlite3.Error as exc:
            LOG.warning("TieredCache SQLite multi-read error: %s", exc)

        return rows

    def _upsert_sqlite(
        self,
        key: str,
        value: Any,
        expires_at: float | None,
    ) -> None:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache (key, value, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, _serialize(value), expires_at),
                )
                conn.commit()
        except sqlite3.Error as exc:
            LOG.warning("TieredCache SQLite write error: %s", exc)

    def _delete_sqlite(self, key: str) -> bool:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return cur.rowcount > 0
        except sqlite3.Error as exc:
            LOG.warning("TieredCache SQLite delete error: %s", exc)
            return False

    def _clear_sqlite(self) -> int:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute("DELETE FROM cache")
                conn.commit()
                return cur.rowcount
        except sqlite3.Error as exc:
            LOG.warning("TieredCache SQLite clear error: %s", exc)
            return 0


# ── Internal helpers ────────────────────────────────────────────────────


class _CacheEntry:
    """Individual cache entry stored in the in-memory dict."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, expires_at: float | None) -> None:
        self.value = value
        self.expires_at = expires_at

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.monotonic() > self.expires_at


def _serialize(value: Any) -> str:
    """JSON-serialise a value for SQLite storage."""
    return json.dumps(value, default=str)


def _deserialize(raw: str) -> Any:
    """JSON-deserialise a value from SQLite storage."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


__all__ = [
    "TieredCache",
]
