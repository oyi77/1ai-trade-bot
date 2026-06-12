#!/usr/bin/env python3
"""Tracking database schema for Vilona Trade FX LP attribution and CAPI events."""

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("vtfx-tracking")

WIB = timezone(timedelta(hours=7))

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "tracking.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_tracking_db(db_path=None):
    """Create all tracking tables if they don't exist."""
    path = db_path or str(DB_PATH)
    with sqlite3.connect(path) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fbclid TEXT DEFAULT '',
                utm_source TEXT DEFAULT '',
                utm_medium TEXT DEFAULT '',
                utm_campaign TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                tracking_id TEXT UNIQUE NOT NULL,
                telegram_user_id TEXT DEFAULT NULL,
                landing_url TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS subscriber_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                username TEXT DEFAULT '',
                action TEXT DEFAULT '',
                tier TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT ''
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS group_membership (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                group_type TEXT DEFAULT 'channel',
                action TEXT DEFAULT '',
                tier_at_action TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)
        db.commit()
    logger.info("Tracking DB initialized at %s", path)


# Auto-init on import
init_tracking_db()
