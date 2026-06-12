"""Subscription database — SQLite storage for users, subs, and trades.

Migrated from bots/subscription-bot/database.py.
Uses tradebot.storage.sqlite for the underlying connection.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradebot.config import settings
from tradebot.storage.sqlite import SQLiteStorage

LOG = logging.getLogger("tradebot.storage.subscription")


def _utc_ts() -> int:
    return int(time.time())


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class SubscriptionDatabase:
    """Thread-safe SQLite database for subscription bot."""

    def __init__(self, db_path: str | None = None) -> None:
        path = db_path or settings.STORAGE_DB_PATH or str(Path(settings.DATA_DIR) / "subscription_bot.db")  # noqa: E501
        self._storage = SQLiteStorage(db_path=Path(path))
        LOG.info("SubscriptionDatabase path: %s", path)

    # ── Connection ──────────────────────────────────────────────────────

    @property
    def _conn(self):
        """Get SQLite connection via underlying storage."""
        import sqlite3
        conn = sqlite3.connect(str(self._storage.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def close(self) -> None:
        pass

    # ── Schema ──────────────────────────────────────────────────────────

    def create_tables(self) -> None:
        with self._conn as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id         INTEGER PRIMARY KEY,
                    chat_id         INTEGER NOT NULL,
                    username        TEXT DEFAULT '',
                    first_name      TEXT DEFAULT '',
                    joined_at       INTEGER NOT NULL,
                    is_admin        INTEGER DEFAULT 0,
                    is_active       INTEGER DEFAULT 1,
                    language_code   TEXT DEFAULT 'en'
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    plan            TEXT NOT NULL CHECK(plan IN ('daily','weekly','monthly')),
                    status          TEXT NOT NULL DEFAULT 'active'
                                    CHECK(status IN ('active','expired','cancelled')),
                    amount_paid     INTEGER NOT NULL,
                    started_at      INTEGER NOT NULL,
                    expires_at      INTEGER NOT NULL,
                    auto_renew      INTEGER DEFAULT 0,
                    created_at      INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sub_status ON subscriptions(status);
                CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscriptions(expires_at);

                CREATE TABLE IF NOT EXISTS linked_accounts (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    account_label   TEXT DEFAULT 'main',
                    stockity_auth   TEXT NOT NULL,
                    stockity_user_id TEXT DEFAULT '',
                    is_active       INTEGER DEFAULT 1,
                    linked_at       INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_link_user ON linked_accounts(user_id);

                CREATE TABLE IF NOT EXISTS trade_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    symbol          TEXT NOT NULL,
                    direction       TEXT NOT NULL CHECK(direction IN ('CALL','PUT')),
                    amount          INTEGER NOT NULL,
                    duration_min    INTEGER NOT NULL DEFAULT 1,
                    entry_price     REAL DEFAULT 0.0,
                    status          TEXT NOT NULL DEFAULT 'pending'
                                    CHECK(status IN ('pending','open','won','lost','error')),
                    result_pnl      REAL DEFAULT 0.0,
                    signal_confidence INTEGER DEFAULT 0,
                    trade_type      TEXT DEFAULT 'manual',
                    note            TEXT DEFAULT '',
                    created_at      INTEGER NOT NULL,
                    resolved_at     INTEGER DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_trade_user ON trade_history(user_id);
                CREATE INDEX IF NOT EXISTS idx_trade_status ON trade_history(status);

                CREATE TABLE IF NOT EXISTS pending_payments (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    merchant_ref    TEXT NOT NULL UNIQUE,
                    plan            TEXT NOT NULL CHECK(plan IN ('daily','weekly','monthly')),
                    amount          INTEGER NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'PENDING'
                                    CHECK(status IN ('PENDING','PAID','EXPIRED','FAILED')),
                    method          TEXT DEFAULT 'QRIS2',
                    payment_url     TEXT DEFAULT '',
                    created_at      INTEGER NOT NULL,
                    paid_at         INTEGER DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_payments(user_id);
                CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_payments(status);
                CREATE INDEX IF NOT EXISTS idx_pending_ref ON pending_payments(merchant_ref);

                CREATE TABLE IF NOT EXISTS user_signal_preferences (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    symbol          TEXT NOT NULL DEFAULT 'ALL',
                    min_confidence  REAL DEFAULT 0.6,
                    direction       TEXT DEFAULT 'BOTH',
                    enabled         INTEGER DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    UNIQUE(user_id, symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_sig_pref_user ON user_signal_preferences(user_id);
            """)
            conn.commit()
            LOG.info("Subscription database tables ready")

    # ── User ops ────────────────────────────────────────────────────────

    def register_user(
        self, user_id: int, chat_id: int, username: str = "",
        first_name: str = "", language_code: str = "en",
    ) -> bool:
        with self._conn as conn:
            cur = conn.execute(
                """INSERT INTO users (user_id, chat_id, username, first_name,
                                      joined_at, language_code)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       chat_id = excluded.chat_id,
                       username = excluded.username,
                       first_name = excluded.first_name,
                       language_code = excluded.language_code,
                       is_active = 1""",
                (user_id, chat_id, username, first_name, _utc_ts(), language_code),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._conn as conn:
            cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> list[dict[str, Any]]:
        with self._conn as conn:
            cur = conn.execute("SELECT * FROM users WHERE is_active = 1 ORDER BY joined_at DESC")
            return [dict(r) for r in cur.fetchall()]

    def get_active_subscribers(self) -> list[dict[str, Any]]:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                """SELECT DISTINCT u.* FROM users u
                   JOIN subscriptions s ON u.user_id = s.user_id
                   WHERE s.status = 'active' AND s.expires_at > ?
                   ORDER BY u.joined_at""",
                (now,),
            )
            return [dict(r) for r in cur.fetchall()]

    # ── Subscription ops ────────────────────────────────────────────────

    def get_active_subscription(self, user_id: int) -> dict[str, Any] | None:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                """SELECT * FROM subscriptions
                   WHERE user_id = ? AND status = 'active' AND expires_at > ?
                   ORDER BY id DESC LIMIT 1""",
                (user_id, now),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def create_subscription(self, user_id: int, plan: str, amount: int, expires_at: int) -> int:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                """INSERT INTO subscriptions (user_id, plan, status, amount_paid,
                                              started_at, expires_at, created_at)
                   VALUES (?, ?, 'active', ?, ?, ?, ?)""",
                (user_id, plan, amount, now, expires_at, now),
            )
            conn.commit()
            LOG.info("Subscription created: user=%d plan=%s expires=%d", user_id, plan, expires_at)
            return cur.lastrowid

    def cancel_subscription(self, sub_id: int) -> None:
        with self._conn as conn:
            conn.execute(
                "UPDATE subscriptions SET status='cancelled' WHERE id=?",
                (sub_id,),
            )
            conn.commit()

    def expire_subscriptions(self) -> int:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                "UPDATE subscriptions SET status='expired' WHERE status='active' AND expires_at <= ?",  # noqa: E501
                (now,),
            )
            conn.commit()
            return cur.rowcount

    # ── Account linking ─────────────────────────────────────────────────

    def get_linked_accounts(self, user_id: int) -> list[dict[str, Any]]:
        with self._conn as conn:
            cur = conn.execute(
                "SELECT * FROM linked_accounts WHERE user_id = ? AND is_active = 1",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_linked_account_by_auth(self, auth_token: str) -> dict[str, Any] | None:
        with self._conn as conn:
            cur = conn.execute(
                "SELECT * FROM linked_accounts WHERE stockity_auth = ? AND is_active = 1 LIMIT 1",
                (auth_token,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def link_account(self, user_id: int, auth_token: str, label: str = "main") -> int:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                """INSERT INTO linked_accounts (user_id, account_label, stockity_auth,
                                                linked_at)
                   VALUES (?, ?, ?, ?)""",
                (user_id, label, auth_token, now),
            )
            conn.commit()
            return cur.lastrowid

    def unlink_account(self, link_id: int, user_id: int) -> None:
        with self._conn as conn:
            conn.execute(
                "UPDATE linked_accounts SET is_active=0 WHERE id=? AND user_id=?",
                (link_id, user_id),
            )
            conn.commit()

    # ── Trade history ───────────────────────────────────────────────────

    def record_trade(
        self, user_id: int, symbol: str, direction: str, amount: int,
        duration_min: int = 1, entry_price: float = 0.0, confidence: int = 0,
        trade_type: str = "manual", note: str = "",
    ) -> int:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                """INSERT INTO trade_history (user_id, symbol, direction, amount,
                                              duration_min, entry_price, status,
                                              signal_confidence, trade_type, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (user_id, symbol, direction, amount, duration_min, entry_price,
                 confidence, trade_type, note, now),
            )
            conn.commit()
            LOG.info("Trade recorded: user=%d %s %s amount=%d", user_id, symbol, direction, amount)
            return cur.lastrowid

    def resolve_trade(self, trade_id: int, status: str, pnl: float) -> None:
        now = _utc_ts()
        with self._conn as conn:
            conn.execute(
                "UPDATE trade_history SET status=?, result_pnl=?, resolved_at=? WHERE id=?",
                (status, pnl, now, trade_id),
            )
            conn.commit()

    def get_user_trades(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn as conn:
            cur = conn.execute(
                """SELECT * FROM trade_history
                   WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_recent_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn as conn:
            cur = conn.execute(
                "SELECT * FROM trade_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_user_stats(self, user_id: int) -> dict[str, Any]:
        sub = self.get_active_subscription(user_id)
        accounts = self.get_linked_accounts(user_id)
        with self._conn as conn:
            cur = conn.execute(
                """SELECT COUNT(*) as total,
                          COALESCE(SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), 0) as wins,
                          COALESCE(SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END), 0) as losses,
                          COALESCE(SUM(result_pnl), 0) as total_pnl
                   FROM trade_history WHERE user_id = ?""",
                (user_id,),
            )
            row = cur.fetchone()
            stats = dict(row) if row else {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0}
        return {
            "total_trades": stats["total"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "total_pnl": stats["total_pnl"],
            "has_subscription": sub is not None,
            "subscription_plan": sub["plan"] if sub else "-",
            "subscription_expires": _fmt_ts(sub["expires_at"]) if sub else "-",
            "linked_accounts": len(accounts),
        }

    # ── Payment tracking ────────────────────────────────────────────────

    def create_pending_payment(
        self, user_id: int, merchant_ref: str, plan: str,
        amount: int, method: str = "QRIS2", payment_url: str = "",
    ) -> int:
        now = _utc_ts()
        with self._conn as conn:
            cur = conn.execute(
                """INSERT INTO pending_payments (user_id, merchant_ref, plan, amount,
                                                  method, payment_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, merchant_ref, plan, amount, method, payment_url, now),
            )
            conn.commit()
            return cur.lastrowid

    def get_pending_payment(self, merchant_ref: str) -> dict[str, Any] | None:
        with self._conn as conn:
            cur = conn.execute(
                "SELECT * FROM pending_payments WHERE merchant_ref = ?",
                (merchant_ref,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_user_pending_payments(self, user_id: int, status: str = "") -> list[dict[str, Any]]:
        with self._conn as conn:
            if status:
                cur = conn.execute(
                    "SELECT * FROM pending_payments WHERE user_id = ? AND status = ? ORDER BY created_at DESC",  # noqa: E501
                    (user_id, status),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM pending_payments WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )
            return [dict(r) for r in cur.fetchall()]

    def mark_payment_paid(self, merchant_ref: str) -> None:
        now = _utc_ts()
        with self._conn as conn:
            conn.execute(
                "UPDATE pending_payments SET status='PAID', paid_at=? WHERE merchant_ref=?",
                (now, merchant_ref),
            )
            conn.commit()
    def get_user_signal_preferences(self, user_id: int) -> list[dict[str, Any]]:
        with self._conn as conn:
            cur = conn.execute(
                "SELECT * FROM user_signal_preferences WHERE user_id = ?",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def set_user_signal_preference(
        self, user_id: int, symbol: str, min_confidence: float, direction: str, enabled: int
    ) -> None:
        with self._conn as conn:
            conn.execute(
                """INSERT INTO user_signal_preferences (user_id, symbol, min_confidence, direction, enabled)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, symbol) DO UPDATE SET
                       min_confidence=excluded.min_confidence,
                       direction=excluded.direction,
                       enabled=excluded.enabled""",
                (user_id, symbol, min_confidence, direction, enabled),
            )
            conn.commit()
