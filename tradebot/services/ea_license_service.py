"""
EA License Service — key generation, connection tracking, and credit-based limits.

Each EA key = 1 connection. Additional connections require additional keys.
Keys are rented monthly (Rp25.000/month). Expired keys are auto-revoked.

Tables (in tradebot.db):
    ea_licenses: key_id, user_id, key, status, created_at, expires_at
    ea_connections: connection_id, key_id, account_id, ip, last_seen, status
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
import time
from typing import Any

from tradebot.storage.repository import get_repo

LOG = logging.getLogger("tradebot.services.ea_license")

EA_KEY_PRICE_IDR = 25_000  # Rp25.000/month per key
EA_KEY_DURATION_DAYS = 30


def _storage():
    return get_repo()


def init_tables() -> None:
    """Create EA license tables. Called from TradeTracker._init_db."""
    store = _storage()
    store.execute("""
        CREATE TABLE IF NOT EXISTS ea_licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL,
            key TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            max_connections INTEGER DEFAULT 1,
            auto_renew INTEGER DEFAULT 0
        )
    """)
    store.execute("""
        CREATE TABLE IF NOT EXISTS ea_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            connection_id TEXT UNIQUE NOT NULL,
            key_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            ip TEXT DEFAULT '',
            last_seen INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (key_id) REFERENCES ea_licenses(key_id)
        )
    """)
    # Migration: add columns if missing
    for col, col_type in [
        ("max_connections", "INTEGER DEFAULT 1"),
        ("auto_renew", "INTEGER DEFAULT 0"),
    ]:
        with contextlib.suppress(Exception):
            _storage().execute(f"ALTER TABLE ea_licenses ADD COLUMN {col} {col_type}")


def _generate_key(user_id: str) -> str:
    """Generate a unique EA key."""
    seed = f"{user_id}{secrets.token_hex(16)}{int(time.time())}"
    return f"VT-{hashlib.sha256(seed.encode()).hexdigest()[:12].upper()}"


def create_key(user_id: str, duration_days: int = EA_KEY_DURATION_DAYS) -> dict[str, Any]:
    """Create a new EA license key for a user.

    Args:
        user_id: Telegram chat_id
        duration_days: Key validity in days (default 30)

    Returns:
        Dict with key details or error.
    """
    now = int(time.time())
    expires_at = now + (duration_days * 86400)
    key_id = f"ek_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    key = _generate_key(user_id)

    store = _storage()
    store.execute(
        """INSERT INTO ea_licenses
           (key_id, user_id, key, status, created_at, expires_at)
           VALUES (?, ?, ?, 'active', ?, ?)""",
        (key_id, user_id, key, now, expires_at),
    )

    LOG.info("EA key created: user=%s key_id=%s expires=%d", user_id, key_id, expires_at)
    return {
        "success": True,
        "key_id": key_id,
        "key": key,
        "user_id": user_id,
        "expires_at": expires_at,
        "duration_days": duration_days,
    }


def get_user_keys(user_id: str) -> list[dict[str, Any]]:
    """Get all EA keys for a user."""
    rows = _storage().fetchall(
        "SELECT * FROM ea_licenses WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    )
    return [dict(r) for r in rows]


def get_active_key(key: str) -> dict[str, Any] | None:
    """Get an active key by its value."""
    row = _storage().fetchone(
        "SELECT * FROM ea_licenses WHERE key=? AND status='active' AND expires_at>?",
        (key, int(time.time())),
    )
    return dict(row) if row else None


def register_connection(key: str, account_id: str, ip: str = "") -> dict[str, Any]:
    """Register an EA connection for a key.

    Validates:
        1. Key exists and is active
        2. Key hasn't expired
        3. Connection count doesn't exceed max_connections

    Returns:
        Dict with connection status or error.
    """
    key_data = get_active_key(key)
    if not key_data:
        return {"error": "Key tidak valid atau sudah expired."}

    key_id = key_data["key_id"]
    now = int(time.time())

    # First check if this account_id already connected (reconnection bypasses limit)
    existing = _storage().fetchone(
        "SELECT * FROM ea_connections WHERE key_id=? AND account_id=? AND status='active'",
        (key_id, account_id),
    )
    if existing:
        # Update last_seen
        _storage().execute(
            "UPDATE ea_connections SET last_seen=?, ip=? WHERE id=?",
            (now, ip, existing[0]),
        )
        return {"success": True, "connection_id": existing[1], "reconnected": True}

    # Count active connections
    conns = _storage().fetchall(
        "SELECT * FROM ea_connections WHERE key_id=? AND status='active'",
        (key_id,),
    )
    max_conn = key_data.get("max_connections", 1)
    if len(conns) >= max_conn:
        return {
            "error": f"Batas koneksi tercapai ({max_conn}). Beli key tambahan untuk koneksi baru.",
            "active_connections": len(conns),
            "max_connections": max_conn,
        }

    # New connection
    conn_id = f"ec_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    _storage().execute(
        """INSERT INTO ea_connections
           (connection_id, key_id, account_id, ip, last_seen, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (conn_id, key_id, account_id, ip, now),
    )

    LOG.info("EA connection: key=%s account=%s conn_id=%s", key_id, account_id, conn_id)
    return {"success": True, "connection_id": conn_id, "reconnected": False}


def disconnect_connection(connection_id: str) -> bool:
    """Mark an EA connection as disconnected."""
    _storage().execute(
        "UPDATE ea_connections SET status='disconnected' WHERE connection_id=?",
        (connection_id,),
    )
    return True


def expire_keys() -> int:
    """Mark expired keys as expired. Returns count expired."""
    now = int(time.time())
    _storage().execute(
        "UPDATE ea_licenses SET status='expired' WHERE status='active' AND expires_at<?",
        (now,),
    )
    expired = _storage().conn().total_changes
    if expired:
        LOG.info("Expired %d EA keys", expired)
    return expired


def renew_key(key_id: str, duration_days: int = EA_KEY_DURATION_DAYS) -> dict[str, Any]:
    """Renew an EA key for another period."""
    now = int(time.time())
    new_expires = now + (duration_days * 86400)
    _storage().execute(
        "UPDATE ea_licenses SET status='active', expires_at=? WHERE key_id=?",
        (new_expires, key_id),
    )
    LOG.info("EA key renewed: key_id=%s expires=%d", key_id, new_expires)
    return {"success": True, "key_id": key_id, "expires_at": new_expires}


def format_key_list(user_id: str) -> str:
    """Format user's EA keys as HTML for Telegram."""
    keys = get_user_keys(user_id)
    if not keys:
        return "Belum punya EA key. /buykey untuk beli."

    now = int(time.time())
    lines = ["🔑 <b>EA LICENSE KEYS</b>", "━━━━━━━━━━━━━━━━"]
    for k in keys:
        status = "🟢" if k["status"] == "active" and k["expires_at"] > now else "🔴"
        expires = k["expires_at"]
        remaining = max(0, expires - now)
        days_left = remaining // 86400
        lines.append(f"{status} <code>{k['key'][:16]}...</code>")
        lines.append(f"   Sisa: {days_left} hari | Status: {k['status']}")
        lines.append("")
    return "\n".join(lines)
