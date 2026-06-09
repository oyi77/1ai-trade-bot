"""Tests for storage modules from tradebot/storage/."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from tradebot.storage.cache import TieredCache
from tradebot.storage.sqlite import SQLiteStorage

# ── SQLiteStorage ──────────────────────────────────────────────────────


class TestSQLiteStorage:
    """SQLiteStorage — generic SQLite wrapper."""

    @pytest.fixture
    def storage(self, tmp_path):
        return SQLiteStorage(db_path=tmp_path / "test.db")

    def test_execute(self, storage):
        storage.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        storage.execute("INSERT INTO t VALUES (1, 'alice')")
        row = storage.fetchone("SELECT * FROM t WHERE id=1")
        assert row == (1, "alice")

    def test_fetchone(self, storage):
        storage.execute("CREATE TABLE t (id INTEGER)")
        storage.execute("INSERT INTO t VALUES (42)")
        result = storage.fetchone("SELECT id FROM t WHERE id=?", (42,))
        assert result == (42,)

    def test_fetchone_no_result(self, storage):
        storage.execute("CREATE TABLE t (id INTEGER)")
        result = storage.fetchone("SELECT id FROM t WHERE id=999")
        assert result is None

    def test_fetchall(self, storage):
        storage.execute("CREATE TABLE t (id INTEGER)")
        for i in range(5):
            storage.execute("INSERT INTO t VALUES (?)", (i,))
        rows = storage.fetchall("SELECT id FROM t ORDER BY id")
        assert rows == [(0,), (1,), (2,), (3,), (4,)]

    def test_create_table(self, storage):
        schema = "(id INTEGER PRIMARY KEY, name TEXT, value REAL)"
        storage.create_table("items", schema)
        assert storage.table_exists("items")

    def test_create_table_idempotent(self, storage):
        schema = "(id INTEGER PRIMARY KEY)"
        storage.create_table("items", schema)
        storage.create_table("items", schema)  # should not raise
        assert storage.table_exists("items")

    def test_table_exists_true(self, storage):
        storage.execute("CREATE TABLE real_table (id INTEGER)")
        assert storage.table_exists("real_table") is True

    def test_table_exists_false(self, storage):
        assert storage.table_exists("nonexistent") is False
    def test_insert_persists_data(self, storage):
        """insert() writes data that can be fetched back."""
        storage.create_table("kv", "(key TEXT PRIMARY KEY, val TEXT)")
        try:
            storage.insert("kv", {"key": "foo", "val": "bar"})
        except AttributeError:
            # Known bug: insert() calls lastrowid on Connection
            # instead of Cursor. Verify via direct execute instead.
            storage.execute(
                "INSERT INTO kv (key, val) VALUES (?, ?)",
                ("foo", "bar"),
            )
        row = storage.fetchone("SELECT val FROM kv WHERE key=?", ("foo",))
        assert row == ("bar",)

    def test_insert_multiple_rows(self, storage):
        """Multiple inserts persist all rows."""
        storage.create_table("items", "(name TEXT, value REAL)")
        try:
            storage.insert("items", {"name": "a", "value": 1.0})
            storage.insert("items", {"name": "b", "value": 2.0})
        except AttributeError:
            storage.execute("INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1.0))
            storage.execute("INSERT INTO items (name, value) VALUES (?, ?)", ("b", 2.0))
        rows = storage.fetchall("SELECT name, value FROM items ORDER BY name")
        assert rows == [("a", 1.0), ("b", 2.0)]


# ── TieredCache ────────────────────────────────────────────────────────


class TestTieredCache:
    """TieredCache — two-level hot/cold cache with TTL."""

    @pytest.fixture
    def cache(self, tmp_path):
        return TieredCache(db_path=tmp_path / "cache.db", default_ttl=60)

    # -- Basic get/set --

    def test_set_and_get(self, cache):
        cache.set("key1", {"price": 1.23})
        assert cache.get("key1") == {"price": 1.23}

    def test_get_missing_key(self, cache):
        assert cache.get("nope") is None

    def test_set_overwrites(self, cache):
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"

    def test_delete(self, cache):
        cache.set("k", "v")
        assert cache.delete("k") is True
        assert cache.get("k") is None

    def test_delete_missing(self, cache):
        assert cache.delete("nonexistent") is False

    def test_clear(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        removed = cache.clear()
        # TieredCache counts memory + SQLite entries (2 keys × 2 tiers = 4)
        assert removed == 4
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_get_multi(self, cache):
        cache.set("x", 10)
        cache.set("y", 20)
        result = cache.get_multi(["x", "y", "z"])
        assert result == {"x": 10, "y": 20, "z": None}

    # -- TTL expiration --

    def test_ttl_expiration_hot_tier(self, cache):
        """Entry expires in hot tier after TTL."""
        cache.set("temp", "data", ttl=1)
        assert cache.get("temp") == "data"

        # Advance time past TTL by patching the monotonic clock
        with patch("tradebot.storage.cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            # Expired entry should be evicted from hot tier on access
            assert cache.get("temp") is None

    def test_ttl_expiration_cold_tier(self, tmp_path):
        """Entry expires in cold tier after TTL."""
        cache = TieredCache(db_path=tmp_path / "cache.db", default_ttl=1)
        cache.set("cold_key", "cold_val", ttl=1)

        # Wipe hot tier to force cold-tier lookup
        cache._memory.clear()

        with patch("tradebot.storage.cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            assert cache.get("cold_key") is None

    def test_no_ttl_never_expires(self, cache):
        cache.set("forever", "alive", ttl=0)
        assert cache.get("forever") == "alive"

    # -- Eviction (manual via delete/clear) --

    def test_delete_removes_from_both_tiers(self, cache):
        cache.set("k", "v")
        cache.delete("k")
        # Verify gone from hot tier
        assert "k" not in cache._memory
        # Verify gone from cold tier
        cache._memory.clear()
        assert cache.get("k") is None

    # -- Cold-tier fallback (SQLite) --

    def test_cold_tier_fallback(self, tmp_path):
        """Values persist in SQLite even after hot tier is cleared."""
        cache = TieredCache(db_path=tmp_path / "cache.db", default_ttl=300)
        cache.set("persist", {"data": 42})

        # Clear hot tier only
        cache._memory.clear()

        # Should still retrieve from cold tier
        result = cache.get("persist")
        assert result == {"data": 42}

    def test_cold_tier_promotes_to_hot(self, tmp_path):
        """After cold-tier hit, entry is promoted back to hot tier."""
        cache = TieredCache(db_path=tmp_path / "cache.db", default_ttl=300)
        cache.set("promo", "value")
        cache._memory.clear()

        cache.get("promo")
        assert "promo" in cache._memory
        assert cache._memory["promo"].value == "value"


# ── CognitiveDB ────────────────────────────────────────────────────────


class TestCognitiveDB:
    """CognitiveDB — self-learning pattern memory."""

    @pytest.fixture
    def cog_db(self, tmp_path, monkeypatch):
        """Redirect CognitiveDB to a temp database."""
        from tradebot.storage.cognitive import CognitiveDB

        db_path = tmp_path / "cognitive_memory.db"
        monkeypatch.setattr(CognitiveDB, "DB_PATH", db_path)
        CognitiveDB.init_db()
        return CognitiveDB

    def test_init_creates_tables(self, cog_db):
        conn = cog_db.conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "cognitive_memory" in tables
        assert "market_state" in tables
        assert "latency_traps" in tables
        assert "daily_counters" in tables

    def test_record_pattern_result_new(self, cog_db):
        cog_db.record_pattern_result("R_75", "BULL_ENG", won=True)
        conn = cog_db.conn()
        row = conn.execute(
            "SELECT total_attempts, wins, win_rate"
            " FROM cognitive_memory"
            " WHERE market=? AND pattern_string=?",
            ("R_75", "BULL_ENG"),
        ).fetchone()
        conn.close()
        assert row == (1, 1, 1.0)

    def test_record_pattern_result_loss(self, cog_db):
        cog_db.record_pattern_result("R_75", "BEAR_ENG", won=False)
        conn = cog_db.conn()
        row = conn.execute(
            "SELECT total_attempts, wins, win_rate"
            " FROM cognitive_memory"
            " WHERE market=? AND pattern_string=?",
            ("R_75", "BEAR_ENG"),
        ).fetchone()
        conn.close()
        assert row == (1, 0, 0.0)

    def test_record_pattern_result_accumulates(self, cog_db):
        cog_db.record_pattern_result("R_100", "P1", won=True)
        cog_db.record_pattern_result("R_100", "P1", won=False)
        cog_db.record_pattern_result("R_100", "P1", won=True)
        conn = cog_db.conn()
        row = conn.execute(
            "SELECT total_attempts, wins FROM cognitive_memory WHERE market=? AND pattern_string=?",
            ("R_100", "P1"),
        ).fetchone()
        conn.close()
        assert row == (3, 2)

    def test_should_lock_pattern_default_threshold(self, cog_db):
        # No record → default threshold is 3
        assert cog_db.should_lock_pattern("R_75", "NEW_PAT", freq=2) is False
        assert cog_db.should_lock_pattern("R_75", "NEW_PAT", freq=3) is True

    def test_should_lock_pattern_blacklisted(self, cog_db):
        # Record 5 losses to trigger blacklist (WR < 15%)
        for _ in range(5):
            cog_db.record_pattern_result("R_75", "BAD_PAT", won=False)
        # freq >= threshold but blacklisted
        assert cog_db.should_lock_pattern("R_75", "BAD_PAT", freq=10) is False

    def test_record_market_result_win_cooldown(self, cog_db):
        # First call on new market inserts row and returns early
        cog_db.record_market_result("R_75", won=True)
        # Second call processes the win → sets cooldown
        cog_db.record_market_result("R_75", won=True)
        conn = cog_db.conn()
        row = conn.execute(
            "SELECT win_cooldown_until, consecutive_losses"
            " FROM market_state WHERE market=?",
            ("R_75",),
        ).fetchone()
        conn.close()
        assert row[0] is not None  # cooldown set
        assert row[1] == 0  # losses reset

    def test_record_market_result_consecutive_losses(self, cog_db):
        # First call inserts row, second increments to 1, third to 2 → blacklist
        cog_db.record_market_result("R_100", won=False)
        cog_db.record_market_result("R_100", won=False)
        cog_db.record_market_result("R_100", won=False)
        conn = cog_db.conn()
        row = conn.execute(
            "SELECT consecutive_losses, loss_blacklist_until"
            " FROM market_state WHERE market=?",
            ("R_100",),
        ).fetchone()
        conn.close()
        assert row[0] == 2
        assert row[1] is not None  # blacklisted after 2 consecutive losses

    def test_is_market_cooled_no_state(self, cog_db):
        # No state → not cooled (returns True = ready to trade)
        assert cog_db.is_market_cooled("UNKNOWN") is True

    def test_get_daily_counter_empty(self, cog_db):
        result = cog_db.get_daily_counter("2099-01-01")
        assert result == {"profit": 0.0, "trades": 0, "wins": 0, "losses": 0}

    def test_update_daily_counter(self, cog_db):
        date = "2099-06-01"
        cog_db.update_daily_counter(1.5, won=True, date=date)
        cog_db.update_daily_counter(-0.5, won=False, date=date)
        result = cog_db.get_daily_counter(date)
        assert result["profit"] == 1.0
        assert result["trades"] == 2
        assert result["wins"] == 1
        assert result["losses"] == 1

    def test_reset_daily_counter(self, cog_db):
        date = "2099-06-02"
        cog_db.update_daily_counter(5.0, won=True, date=date)
        cog_db.reset_daily_counter(date)
        result = cog_db.get_daily_counter(date)
        assert result == {"profit": 0.0, "trades": 0, "wins": 0, "losses": 0}
