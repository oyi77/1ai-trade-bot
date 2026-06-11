"""Members service — member/ subscriber database access.

Provides member lookup, creation, and tier upgrades
using the SQLite database at the project root.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

_MEMBERS_DB: Path | None = None


def _resolve_db() -> Path:
    global _MEMBERS_DB
    if _MEMBERS_DB is None:
        root = Path(__file__).resolve().parent.parent.parent
        _MEMBERS_DB = root / "members.db"
    return _MEMBERS_DB


def _conn():
    """Get a read-only SQLite connection."""
    import sqlite3

    db_path = _resolve_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_member(chat_id: str, nama: str = "", username: str = "") -> dict[str, Any]:
    now = datetime.now(WIB)
    trial_end = (now + timedelta(days=7))
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
        if row:
            return dict(row)
        db.execute(
            "INSERT INTO members (chat_id, nama, username, tier, status, joined_at, expiry) "
            "VALUES (?, ?, ?, 'starter', 'trial', ?, ?)",
            (
                str(chat_id),
                nama or f"User-{chat_id}",
                username or "",
                now.isoformat(),
                trial_end.isoformat(),
            ),
        )
        return {
            "chat_id": str(chat_id),
            "nama": nama,
            "username": username,
            "tier": "starter",
            "status": "trial",
            "joined_at": now.isoformat(),
            "expiry": trial_end.isoformat(),
        }


def get_member(chat_id: str) -> dict[str, Any] | None:
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
        return dict(row) if row else None


def upgrade_tier(
    chat_id: str, tier: str, days: int = 30, payment_ref: str = ""
) -> None:
    now = datetime.now(WIB)
    expiry = (now + timedelta(days=days))
    with _conn() as db:
        existing = db.execute(
            "SELECT chat_id FROM members WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE members SET tier=?, status='paid', expiry=?, payment_ref=? WHERE chat_id=?",
                (tier, expiry.isoformat(), payment_ref, str(chat_id)),
            )
        else:
            db.execute(
                "INSERT INTO members (chat_id, tier, status, expiry, payment_ref, joined_at) "
                "VALUES (?, ?, 'paid', ?, ?, ?)",
                (str(chat_id), tier, expiry.isoformat(), payment_ref, now.isoformat()),
            )


def get_total_donations() -> int:
    with _conn() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payment_orders WHERE status='paid'"
        ).fetchone()
        return row[0] if row else 0


def activate_premium(chat_id: str, tier: str = "pro", days: int = 30) -> bool:
    """Activate premium tier for a member (wrapper for payment webhook)."""
    try:
        upgrade_tier(str(chat_id), tier, days)
        return True
    except Exception as exc:
        LOG.error("activate_premium(%s) failed: %s", chat_id, exc)
        return False


def mark_payment_paid(merchant_ref: str) -> bool:
    """Mark a payment order as paid (wrapper for payment webhook)."""
    import sqlite3
    from datetime import datetime as _dt
    try:
        now = _dt.now(WIB).isoformat()
        with _conn() as db:
            db.execute(
                "UPDATE payment_orders SET status='paid', paid_at=? WHERE merchant_ref=?",
                (now, merchant_ref),
            )
        return True
    except Exception as exc:
        LOG.error("mark_payment_paid(%s) failed: %s", merchant_ref, exc)
        return False


__all__ = [
    "activate_premium",
    "ensure_member",
    "get_member",
    "get_total_donations",
    "mark_payment_paid",
    "upgrade_tier",
]
