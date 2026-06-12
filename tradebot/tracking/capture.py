#!/usr/bin/env python3
"""Tracking capture: generates tracking IDs and links LP visitors to Telegram users."""

import hashlib
import logging
import secrets
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("vtfx-tracking-capture")

WIB = timezone(timedelta(hours=7))
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
DB_PATH = DATA_DIR / "tracking.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def generate_tracking_id(fbclid=""):
    """Generate a unique SHA256 tracking ID from fbclid + timestamp + random."""
    ts = str(int(time.time() * 1000))
    rand = secrets.token_hex(8)
    raw = f"{fbclid}_{ts}_{rand}"
    return hashlib.sha256(raw.encode()).hexdigest()


def create_tracking_record(fbclid="", utm_source="", utm_medium="",
                           utm_campaign="", ip_address="", user_agent="",
                           landing_url=""):
    """Insert a new tracking record and return the tracking_id."""
    tracking_id = generate_tracking_id(fbclid)
    now = datetime.now(WIB).isoformat()
    with _conn() as db:
        db.execute(
            "INSERT INTO user_tracking "
            "(fbclid, utm_source, utm_medium, utm_campaign, ip_address, "
            "user_agent, tracking_id, landing_url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fbclid, utm_source, utm_medium, utm_campaign,
             ip_address, user_agent, tracking_id, landing_url, now)
        )
        db.commit()
    logger.info("Tracking record created: %s", tracking_id)
    return tracking_id


def link_telegram_user(tracking_id, telegram_user_id):
    """Link a tracking_id to a Telegram user after deep link click."""
    now = datetime.now(WIB).isoformat()
    with _conn() as db:
        db.execute(
            "UPDATE user_tracking SET telegram_user_id = ? "
            "WHERE tracking_id = ?",
            (str(telegram_user_id), tracking_id)
        )
        db.commit()
    logger.info("Tracking linked: %s -> %s", tracking_id, telegram_user_id)


def get_tracking_by_telegram(telegram_user_id):
    """Query tracking records by telegram_user_id for CAPI event enrichment."""
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM user_tracking WHERE telegram_user_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (str(telegram_user_id),)
        ).fetchall()
    return [dict(r) for r in rows]
