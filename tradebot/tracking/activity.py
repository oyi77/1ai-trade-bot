#!/usr/bin/env python3
"""Activity logging for subscriber actions."""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("vtfx-activity")
WIB = timezone(timedelta(hours=7))

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "members.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS subscriber_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                target_id TEXT DEFAULT '',
                username TEXT DEFAULT '',
                event TEXT NOT NULL,
                tier TEXT DEFAULT '',
                meta TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_chat_id
            ON subscriber_activity(chat_id)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_event
            ON subscriber_activity(event)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_created_at
            ON subscriber_activity(created_at)
        """)


def log_activity(
    chat_id: str,
    target_id: str = "",
    username: str = "",
    event: str = "",
    tier: str = "",
    meta: dict | None = None,
) -> bool:
    """Log a subscriber activity event.

    Args:
        chat_id: The user's Telegram chat ID
        target_id: The target user's chat ID (same as chat_id for self-events)
        username: The user's Telegram username
        event: Event type (analyze, mtf, engines, payment_success, subscription_expired, etc.)
        tier: Subscription tier (pro, elite, lifetime, etc.)
        meta: Additional metadata dict
    """
    try:
        now = datetime.now(WIB).isoformat()
        meta_str = json.dumps(meta or {}, ensure_ascii=False)
        with _conn() as db:
            db.execute(
                "INSERT INTO subscriber_activity (chat_id, target_id, username, event, tier, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(chat_id), str(target_id or chat_id), username or "", event, tier, meta_str, now),
            )
        logger.debug("Activity logged: %s | %s | %s", chat_id, event, tier)
        return True
    except Exception as e:
        logger.warning("Activity log failed for %s: %s", chat_id, e)
        return False


def get_user_activity(chat_id: str, event: str = "", limit: int = 50) -> list:
    """Get recent activity for a user."""
    try:
        with _conn() as db:
            if event:
                rows = db.execute(
                    "SELECT * FROM subscriber_activity WHERE chat_id=? AND event=? "
                    "ORDER BY id DESC LIMIT ?",
                    (str(chat_id), event, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM subscriber_activity WHERE chat_id=? "
                    "ORDER BY id DESC LIMIT ?",
                    (str(chat_id), limit),
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("get_user_activity failed: %s", e)
        return []


def get_recent_events(event: str = "", hours: int = 24, limit: int = 100) -> list:
    """Get recent events across all users within time window."""
    try:
        cutoff = (datetime.now(WIB) - timedelta(hours=hours)).isoformat()
        with _conn() as db:
            if event:
                rows = db.execute(
                    "SELECT * FROM subscriber_activity WHERE event=? AND created_at >= ? "
                    "ORDER BY id DESC LIMIT ?",
                    (event, cutoff, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM subscriber_activity WHERE created_at >= ? "
                    "ORDER BY id DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("get_recent_events failed: %s", e)
        return []


# Auto-initialize on import
init_db()
