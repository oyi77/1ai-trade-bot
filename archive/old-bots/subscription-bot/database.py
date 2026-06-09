"""
SQLite database layer for the subscription bot.

Tables:
  - users           : Telegram user registration
  - subscriptions   : Active / expired subscription records
  - linked_accounts : User-linked Stockity trading accounts
  - trade_history   : Executed trades (manual + auto)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import Config

LOG = logging.getLogger("subscription_bot.database")


# ── Helpers ────────────────────────────────────────────────────────────────

def _utc_ts() -> int:
    """Current UTC unix timestamp (seconds)."""
    return int(time.time())


def _fmt_ts(ts: int) -> str:
    """Format unix timestamp to human-readable UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Database ────────────────────────────────────────────────────────────────

class Database:
    """Thread-safe SQLite database for subscription bot."""

    def __init__(self, db_path: str = ""):
        self._path = Path(db_path or Config.DB_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        LOG.info("Database path: %s", self._path)

    # ── Connection ──────────────────────────────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Schema ──────────────────────────────────────────────────────────

    def create_tables(self):
        """Create all tables if they don't exist."""
        cur = self.conn.executescript("""
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
        """)
        self.conn.commit()
        LOG.info("Database tables ready")
        return cur

    # ── User ops ────────────────────────────────────────────────────────

    def register_user(
        self,
        user_id: int,
        chat_id: int,
        username: str = "",
        first_name: str = "",
        language_code: str = "en",
    ) -> bool:
        """Insert or update user record. Returns True if new user."""
        cur = self.conn.execute(
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
        self.conn.commit()
        return cur.rowcount > 0  # rough indicator

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        return cur.fetchone()

    def get_all_users(self) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM users WHERE is_active = 1 ORDER BY joined_at DESC"
        )
        return cur.fetchall()

    def get_active_subscribers(self) -> list[sqlite3.Row]:
        """Get all users with active, non-expired subscriptions."""
        now = _utc_ts()
        cur = self.conn.execute(
            """SELECT DISTINCT u.* FROM users u
               JOIN subscriptions s ON u.user_id = s.user_id
               WHERE s.status = 'active' AND s.expires_at > ?
               ORDER BY u.joined_at""",
            (now,),
        )
        return cur.fetchall()

    # ── Subscription ops ────────────────────────────────────────────────

    def create_subscription(
        self,
        user_id: int,
        plan: str,
        amount_paid: int,
        expires_at: int,
        auto_renew: bool = False,
    ) -> int:
        """Create a new subscription. Returns the subscription ID."""
        now = _utc_ts()
        cur = self.conn.execute(
            """INSERT INTO subscriptions
               (user_id, plan, status, amount_paid, started_at, expires_at, auto_renew, created_at)
               VALUES (?, ?, 'active', ?, ?, ?, ?, ?)""",
            (user_id, plan, amount_paid, now, expires_at, int(auto_renew), now),
        )
        self.conn.commit()
        sub_id = cur.lastrowid
        LOG.info("Subscription #%d created for user %d: %s -> %s", sub_id, user_id, plan, _fmt_ts(expires_at))
        return sub_id

    def get_active_subscription(self, user_id: int) -> Optional[sqlite3.Row]:
        now = _utc_ts()
        cur = self.conn.execute(
            """SELECT * FROM subscriptions
               WHERE user_id = ? AND status = 'active' AND expires_at > ?
               ORDER BY expires_at DESC LIMIT 1""",
            (user_id, now),
        )
        return cur.fetchone()

    def get_all_subscriptions(self, user_id: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return cur.fetchall()

    def expire_subscription(self, sub_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE subscriptions SET status = 'expired' WHERE id = ?",
            (sub_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def cancel_subscription(self, sub_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE subscriptions SET status = 'cancelled' WHERE id = ?",
            (sub_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def expire_stale_subscriptions(self) -> int:
        """Mark all expired subscriptions. Returns count expired."""
        now = _utc_ts()
        cur = self.conn.execute(
            """UPDATE subscriptions SET status = 'expired'
               WHERE status = 'active' AND expires_at < ?""",
            (now,),
        )
        self.conn.commit()
        return cur.rowcount

    # ── Account linking ─────────────────────────────────────────────────

    def link_account(
        self,
        user_id: int,
        auth_token: str,
        stockity_user_id: str = "",
        label: str = "main",
    ) -> int:
        """Link a Stockity account to a Telegram user. Returns link ID."""
        now = _utc_ts()
        cur = self.conn.execute(
            """INSERT INTO linked_accounts
               (user_id, account_label, stockity_auth, stockity_user_id, is_active, linked_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (user_id, label, auth_token, stockity_user_id, now),
        )
        self.conn.commit()
        LOG.info("Account linked: user=%d label=%s", user_id, label)
        return cur.lastrowid

    def get_linked_accounts(self, user_id: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM linked_accounts WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        return cur.fetchall()

    def unlink_account(self, link_id: int, user_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE linked_accounts SET is_active = 0 WHERE id = ? AND user_id = ?",
            (link_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_linked_account_by_auth(self, auth_token: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM linked_accounts WHERE stockity_auth = ? AND is_active = 1",
            (auth_token,),
        )
        return cur.fetchone()

    # ── Trade history ───────────────────────────────────────────────────

    def record_trade(
        self,
        user_id: int,
        symbol: str,
        direction: str,
        amount: int,
        duration_min: int = 1,
        entry_price: float = 0.0,
        confidence: int = 0,
        trade_type: str = "manual",
        note: str = "",
    ) -> int:
        now = _utc_ts()
        cur = self.conn.execute(
            """INSERT INTO trade_history
               (user_id, symbol, direction, amount, duration_min, entry_price,
                status, signal_confidence, trade_type, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (user_id, symbol, direction, amount, duration_min, entry_price,
             confidence, trade_type, note, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def resolve_trade(
        self, trade_id: int, status: str, result_pnl: float = 0.0
    ) -> bool:
        cur = self.conn.execute(
            """UPDATE trade_history
               SET status = ?, result_pnl = ?, resolved_at = ?
               WHERE id = ?""",
            (status, result_pnl, _utc_ts(), trade_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_user_trades(
        self, user_id: int, limit: int = 20
    ) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            """SELECT * FROM trade_history
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return cur.fetchall()

    def get_recent_trades(self, limit: int = 50) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM trade_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    def get_user_stats(self, user_id: int) -> dict:
        """Return summary stats for a user."""
        cur = self.conn.execute(
            """SELECT
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) as losses,
                   SUM(result_pnl) as total_pnl
               FROM trade_history
               WHERE user_id = ?""",
            (user_id,),
        )
        row = cur.fetchone()
        sub = self.get_active_subscription(user_id)
        links = len(self.get_linked_accounts(user_id))
        return {
            "total_trades": row["total_trades"] if row else 0,
            "wins": row["wins"] if row else 0,
            "losses": row["losses"] if row else 0,
            "total_pnl": row["total_pnl"] if row else 0.0,
            "has_subscription": sub is not None,
            "subscription_plan": sub["plan"] if sub else "none",
            "subscription_expires": _fmt_ts(sub["expires_at"]) if sub else "N/A",
            "linked_accounts": links,
        }

    # ── Pending payment ops ──────────────────────────────────────────

    def create_pending_payment(
        self, user_id: int, merchant_ref: str, plan: str, amount: int,
        method: str = "QRIS2", payment_url: str = "",
    ) -> int:
        now = _utc_ts()
        cur = self.conn.execute(
            """INSERT INTO pending_payments
               (user_id, merchant_ref, plan, amount, status, method, payment_url, created_at)
               VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?)""",
            (user_id, merchant_ref, plan, amount, method, payment_url, now),
        )
        self.conn.commit()
        LOG.info("Pending payment #%d: user=%d ref=%s plan=%s amount=%d",
                 cur.lastrowid, user_id, merchant_ref, plan, amount)
        return cur.lastrowid

    def get_pending_payment(self, merchant_ref: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM pending_payments WHERE merchant_ref = ?", (merchant_ref,),
        )
        return cur.fetchone()

    def get_user_pending_payments(
        self, user_id: int, status: str = "PENDING",
    ) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM pending_payments WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
            (user_id, status),
        )
        return cur.fetchall()

    def get_all_pending_payments(self, status: str = "PENDING") -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM pending_payments WHERE status = ? ORDER BY created_at ASC",
            (status,),
        )
        return cur.fetchall()

    def mark_payment_paid(self, merchant_ref: str) -> bool:
        now = _utc_ts()
        cur = self.conn.execute(
            "UPDATE pending_payments SET status = 'PAID', paid_at = ? WHERE merchant_ref = ?",
            (now, merchant_ref),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def expire_stale_payments(self, max_age: int = 3600) -> int:
        """Mark pending payments older than max_age seconds as EXPIRED."""
        cutoff = _utc_ts() - max_age
        cur = self.conn.execute(
            "UPDATE pending_payments SET status = 'EXPIRED' WHERE status = 'PENDING' AND created_at < ?",
            (cutoff,),
        )
        self.conn.commit()
        return cur.rowcount
