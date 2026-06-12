"""Members service — member/ subscriber database access.

Provides member lookup, creation, and tier upgrades
using the SQLite database at the project root.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tradebot.services.members_service")

WIB = timezone(timedelta(hours=7))
FREE_DAILY_QUOTA = 3

_MEMBERS_DB: Path | None = None


def _resolve_db() -> Path:
    global _MEMBERS_DB
    if _MEMBERS_DB is None:
        root = Path(__file__).resolve().parent.parent.parent
        _MEMBERS_DB = root / "members.db"
    return _MEMBERS_DB


def _conn() -> sqlite3.Connection:
    """Get a SQLite connection."""
    db_path = _resolve_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create members + payment_orders tables if they do not exist."""
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                chat_id TEXT PRIMARY KEY,
                nama TEXT,
                username TEXT,
                tier TEXT DEFAULT 'starter',
                status TEXT DEFAULT 'trial',
                joined_at TEXT,
                expiry TEXT,
                quota_used INTEGER DEFAULT 0,
                last_quota_reset TEXT,
                risk_percent REAL DEFAULT 1.0,
                timeframe TEXT DEFAULT 'H1'
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS payment_orders (
                merchant_ref TEXT PRIMARY KEY,
                chat_id TEXT,
                amount INTEGER,
                product_key TEXT,
                gateway TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                paid_at TEXT,
                payload TEXT
            )
        """)
        # Schema migration checks
        cursor = db.execute("PRAGMA table_info(members)")
        cols = [row["name"] for row in cursor.fetchall()]
        for col, col_type in [
            ("quota_used", "INTEGER DEFAULT 0"),
            ("last_quota_reset", "TEXT"),
            ("risk_percent", "REAL DEFAULT 1.0"),
            ("timeframe", "TEXT DEFAULT 'H1'"),
        ]:
            if col not in cols:
                db.execute(f"ALTER TABLE members ADD COLUMN {col} {col_type}")


def ensure_member(chat_id: str, nama: str = "", username: str = "") -> dict[str, Any]:
    now = datetime.now(WIB)
    trial_end = now + timedelta(days=7)
    init_db()
    with _conn() as db:
        row = db.execute("SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)).fetchone()
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
            "quota_used": 0,
            "last_quota_reset": None,
            "risk_percent": 1.0,
            "timeframe": "H1",
        }


def get_member(chat_id: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as db:
        row = db.execute("SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)).fetchone()
        return dict(row) if row else None


def upgrade_tier(chat_id: str, tier: str, days: int = 30, payment_ref: str = "") -> None:
    now = datetime.now(WIB)
    expiry = now + timedelta(days=days)
    init_db()
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


def activate_premium(chat_id: str, tier: str = "pro", days: int = 30) -> bool:
    """Activate premium tier for a member (wrapper for payment webhook)."""
    try:
        upgrade_tier(str(chat_id), tier, days)
        return True
    except Exception as exc:
        LOG.error("activate_premium(%s) failed: %s", chat_id, exc)
        return False


def deactivate_premium(chat_id: str) -> bool:
    init_db()
    try:
        with _conn() as db:
            db.execute(
                "UPDATE members SET tier='starter', status='expired' WHERE chat_id=?",
                (str(chat_id),),
            )
        return True
    except Exception as exc:
        LOG.error("deactivate_premium(%s) failed: %s", chat_id, exc)
        return False


def mark_expired(chat_id: str) -> bool:
    return deactivate_premium(chat_id)


def get_total_donations() -> int:
    init_db()
    with _conn() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payment_orders WHERE status='paid'"
        ).fetchone()
        return row[0] if row else 0


def insert_payment_order(
    merchant_ref: str,
    chat_id: str,
    amount: int,
    product_key: str,
    gateway: str,
    payload: dict | str = "",
) -> dict:
    init_db()
    now = datetime.now(WIB).isoformat()
    payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO payment_orders "
            "(merchant_ref, chat_id, amount, product_key, gateway, status, created_at, payload) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (merchant_ref, str(chat_id), amount, product_key, gateway, now, payload_str),
        )
    return {
        "merchant_ref": merchant_ref,
        "chat_id": str(chat_id),
        "amount": amount,
        "product_key": product_key,
        "gateway": gateway,
        "status": "pending",
        "created_at": now,
    }


def mark_payment_paid(merchant_ref: str) -> bool:
    """Mark a payment order as paid (wrapper for payment webhook)."""
    init_db()
    try:
        now = datetime.now(WIB).isoformat()
        with _conn() as db:
            db.execute(
                "UPDATE payment_orders SET status='paid', paid_at=? WHERE merchant_ref=?",
                (now, merchant_ref),
            )
            order = db.execute(
                "SELECT * FROM payment_orders WHERE merchant_ref = ?", (merchant_ref,)
            ).fetchone()
            if order:
                upgrade_tier(
                    chat_id=order["chat_id"],
                    tier=order["product_key"],
                    days=9999 if order["product_key"] == "lifetime" else 30,
                    payment_ref=merchant_ref,
                )
        return True
    except Exception as exc:
        LOG.error("mark_payment_paid(%s) failed: %s", merchant_ref, exc)
        return False


def get_member_stats(chat_id: str | None = None) -> dict[str, Any]:
    init_db()
    with _conn() as db:
        if chat_id:
            row = db.execute("SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)).fetchone()
            return dict(row) if row else {}
        total = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM members WHERE status='paid'").fetchone()[0]
        return {"total_members": total, "active_premium": active}


def get_due_members() -> list[dict[str, Any]]:
    init_db()
    now = datetime.now(WIB)
    three_days_later = (now + timedelta(days=3)).isoformat()
    now_str = now.isoformat()
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM members WHERE status='paid' AND expiry BETWEEN ? AND ?",
            (now_str, three_days_later),
        ).fetchall()
        return [dict(r) for r in rows]


def is_premium(chat_id: str) -> bool:
    member = get_member(chat_id)
    if not member:
        return False
    if member.get("status") == "paid":
        # Check expiry
        expiry = member.get("expiry")
        if expiry:
            try:
                exp_dt = datetime.fromisoformat(expiry)
                if exp_dt > datetime.now(WIB):
                    return True
                else:
                    deactivate_premium(chat_id)
            except Exception:
                pass
    return False


def check_quota(chat_id: str) -> dict[str, Any]:
    member = get_member(chat_id)
    if not member:
        return {"allowed": False, "reason": "No member found", "limit": 0, "used": 0}
    tier = member.get("tier", "starter")
    status = member.get("status", "trial")

    limit = FREE_DAILY_QUOTA
    if status == "paid":
        limit = 50 if tier == "pro" else 999  # elite / lifetime gets 999

    today = datetime.now(WIB).strftime("%Y-%m-%d")
    used = member.get("quota_used", 0)
    last_reset = member.get("last_quota_reset")

    if last_reset != today:
        used = 0
        with _conn() as db:
            db.execute(
                "UPDATE members SET quota_used=0, last_quota_reset=? WHERE chat_id=?",
                (today, str(chat_id)),
            )

    return {
        "allowed": used < limit,
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "tier": tier,
    }


def use_quota(chat_id: str) -> bool:
    quota = check_quota(chat_id)
    if not quota["allowed"]:
        return False
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    with _conn() as db:
        db.execute(
            "UPDATE members SET quota_used = quota_used + 1, last_quota_reset=? WHERE chat_id=?",
            (today, str(chat_id)),
        )
    return True


def get_monthly_fuel_stats() -> dict[str, Any]:
    init_db()
    with _conn() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payment_orders WHERE status='paid'"
        ).fetchone()
        total = row[0] if row else 0
        # Get count
        cnt = db.execute("SELECT COUNT(*) FROM payment_orders WHERE status='paid'").fetchone()[0]
        return {"total_amount": total, "total_orders": cnt}


def get_user_last_donation(chat_id: str) -> dict[str, Any] | None:
    init_db()
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM payment_orders WHERE chat_id = ? AND status='paid' "
            "ORDER BY paid_at DESC LIMIT 1",
            (str(chat_id),),
        ).fetchone()
        return dict(row) if row else None


def get_stale_donors(min_days: int = 30) -> list[dict[str, Any]]:
    init_db()
    now = datetime.now(WIB)
    limit_date = (now - timedelta(days=min_days)).isoformat()
    with _conn() as db:
        # Get members with status='paid' whose expiry or last donation was long ago
        rows = db.execute(
            "SELECT m.*, MAX(p.paid_at) as last_paid, p.amount as last_amount "
            "FROM members m "
            "LEFT JOIN payment_orders p ON m.chat_id = p.chat_id AND p.status='paid' "
            "WHERE m.status='paid' "
            "GROUP BY m.chat_id "
            "HAVING last_paid IS NULL OR last_paid < ?",
            (limit_date,),
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            last_paid = d.get("last_paid")
            if last_paid:
                last_paid_dt = datetime.fromisoformat(last_paid)
                days_since = (now - last_paid_dt).days
            else:
                days_since = 99
            d["days_since"] = days_since
            results.append(d)
        return results


__all__ = [
    "init_db",
    "ensure_member",
    "get_member",
    "upgrade_tier",
    "activate_premium",
    "deactivate_premium",
    "mark_expired",
    "get_total_donations",
    "insert_payment_order",
    "mark_payment_paid",
    "get_member_stats",
    "get_due_members",
    "is_premium",
    "check_quota",
    "use_quota",
    "get_monthly_fuel_stats",
    "get_user_last_donation",
    "get_stale_donors",
]
