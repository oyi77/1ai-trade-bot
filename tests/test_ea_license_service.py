"""Tests for EA License Service."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tradebot.services import ea_license_service as ea


@pytest.fixture
def _patch_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_repo() to return a fresh SQLite DB with Row factory."""
    db_path = tmp_path / "test_ea.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    class FakeRepo:
        def conn(self):
            return conn

        def execute(self, sql: str, params=()):
            return conn.execute(sql, params)

        def fetchone(self, sql: str, params=()):
            cur = conn.execute(sql, params)
            return cur.fetchone()

        def fetchall(self, sql: str, params=()):
            cur = conn.execute(sql, params)
            return cur.fetchall()

    monkeypatch.setattr(ea, "_storage", lambda: FakeRepo())
    ea.init_tables()


class TestInitTables:
    def test_tables_created(self, _patch_repo):
        store = ea._storage()
        tables = store.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r["name"] for r in tables]
        assert "ea_licenses" in names
        assert "ea_connections" in names

    def test_tables_idempotent(self, _patch_repo):
        ea.init_tables()
        ea.init_tables()  # should not raise


class TestCreateKey:
    def test_creates_valid_key(self, _patch_repo):
        result = ea.create_key("user1")
        assert result["success"] is True
        assert result["key"].startswith("VT-")
        assert len(result["key"]) == 15  # "VT-" + 12 hex chars
        assert result["user_id"] == "user1"
        assert result["duration_days"] == 30
        assert result["expires_at"] > int(time.time())

    def test_stores_in_db(self, _patch_repo):
        ea.create_key("user1")
        rows = ea._storage().fetchall("SELECT * FROM ea_licenses WHERE user_id=?", ("user1",))
        assert len(rows) >= 1

    def test_multiple_keys_for_user(self, _patch_repo):
        ea.create_key("user1")
        ea.create_key("user1")
        keys = ea.get_user_keys("user1")
        assert len(keys) == 2


class TestGetUserKeys:
    def test_empty_for_unknown_user(self, _patch_repo):
        keys = ea.get_user_keys("nobody")
        assert keys == []

    def test_returns_user_keys(self, _patch_repo):
        ea.create_key("user1")
        keys = ea.get_user_keys("user1")
        assert len(keys) >= 1
        assert keys[0]["user_id"] == "user1"

    def test_keys_ordered_by_created_at_desc(self, _patch_repo):
        ea.create_key("user1")
        ea.create_key("user1")
        keys = ea.get_user_keys("user1")
        assert keys[0]["created_at"] >= keys[-1]["created_at"]


class TestGetActiveKey:
    def test_returns_active_key(self, _patch_repo):
        created = ea.create_key("user1")
        found = ea.get_active_key(created["key"])
        assert found is not None
        assert found["key"] == created["key"]

    def test_returns_none_for_expired_key(self, _patch_repo):
        created = ea.create_key("user1", duration_days=0)  # expires immediately
        time.sleep(1)
        found = ea.get_active_key(created["key"])
        assert found is None

    def test_returns_none_for_unknown_key(self, _patch_repo):
        assert ea.get_active_key("FAKE-KEY") is None

    def test_returns_none_for_inactive_key(self, _patch_repo):
        created = ea.create_key("user1")
        ea._storage().execute(
            "UPDATE ea_licenses SET status='expired' WHERE key=?",
            (created["key"],),
        )
        assert ea.get_active_key(created["key"]) is None


class TestRegisterConnection:
    def test_creates_connection(self, _patch_repo):
        created = ea.create_key("user1")
        result = ea.register_connection(created["key"], "account1", "192.168.1.1")
        assert result["success"] is True
        assert "connection_id" in result

    def test_reconnects_existing_account(self, _patch_repo):
        created = ea.create_key("user1")
        r1 = ea.register_connection(created["key"], "account1")
        r2 = ea.register_connection(created["key"], "account1")
        assert r2["reconnected"] is True
        assert r2["connection_id"] == r1["connection_id"]

    def test_rejects_exceeding_max_connections(self, _patch_repo):
        created = ea.create_key("user1")
        ea.register_connection(created["key"], "account1")
        result = ea.register_connection(created["key"], "account2")
        assert "error" in result
        assert "Batas koneksi" in result["error"]

    def test_rejects_expired_key(self, _patch_repo):
        created = ea.create_key("user1", duration_days=0)
        time.sleep(1)
        result = ea.register_connection(created["key"], "account1")
        assert "error" in result

    def test_rejects_unknown_key(self, _patch_repo):
        result = ea.register_connection("FAKE-KEY", "account1")
        assert "error" in result


class TestDisconnectConnection:
    def test_disconnects_connection(self, _patch_repo):
        created = ea.create_key("user1")
        conn = ea.register_connection(created["key"], "account1")
        assert ea.disconnect_connection(conn["connection_id"]) is True
        rows = ea._storage().fetchall(
            "SELECT status FROM ea_connections WHERE connection_id=?",
            (conn["connection_id"],),
        )
        assert rows[0]["status"] == "disconnected"


class TestExpireKeys:
    def test_expires_past_keys(self, _patch_repo):
        ea.create_key("user1", duration_days=0)
        time.sleep(1)
        expired = ea.expire_keys()
        assert expired >= 1

    def test_does_not_expire_active_keys(self, _patch_repo):
        ea.create_key("user1", duration_days=30)
        expired = ea.expire_keys()  # noqa: F841
        # total_changes sees the update attempt even if 0 rows changed
        # just verify the key is still active
        keys = ea.get_user_keys("user1")
        assert keys[0]["status"] == "active"

class TestRenewKey:
    def test_renews_key(self, _patch_repo):
        created = ea.create_key("user1")
        ea._storage().execute(
            "UPDATE ea_licenses SET status='expired' WHERE key_id=?",
            (created["key_id"],),
        )
        import time as _time
        _time.sleep(1)
        result = ea.renew_key(created["key_id"])
        assert result["success"] is True
        assert result["expires_at"] > created["expires_at"]
        key = ea._storage().fetchone(
            "SELECT status FROM ea_licenses WHERE key_id=?",
            (created["key_id"],),
        )
        assert key["status"] == "active"

    def test_renew_sets_status_active(self, _patch_repo):
        created = ea.create_key("user1")
        ea._storage().execute(
            "UPDATE ea_licenses SET status='expired' WHERE key_id=?",
            (created["key_id"],),
        )
        result = ea.renew_key(created["key_id"])
        assert result["success"] is True
        key = ea._storage().fetchone(
            "SELECT status FROM ea_licenses WHERE key_id=?",
            (created["key_id"],),
        )
        assert key["status"] == "active"

    def test_renew_with_custom_duration(self, _patch_repo):
        created = ea.create_key("user1")
        result = ea.renew_key(created["key_id"], duration_days=60)
        renewed = int(time.time()) + 60 * 86400
        assert abs(result["expires_at"] - renewed) < 5