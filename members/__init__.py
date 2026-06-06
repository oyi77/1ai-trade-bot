#!/usr/bin/env python3
"""
Vilona Trade FX — Member Database
CRUD untuk tabel members di SQLite.
"""
import json, logging, os, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("vtfx-members")
WIB = timezone(timedelta(hours=7))

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "members.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                chat_id TEXT PRIMARY KEY,
                nama TEXT DEFAULT '',
                username TEXT DEFAULT '',
                tier TEXT DEFAULT 'starter',
                status TEXT DEFAULT 'trial',
                joined_at TEXT DEFAULT '',
                expiry TEXT DEFAULT '',
                payment_ref TEXT DEFAULT '',
                autosync INTEGER DEFAULT 0,
                quota_used INTEGER DEFAULT 0,
                quota_date TEXT DEFAULT ''
            )
        """)
        # Add columns if they don't exist (migration for existing DBs)
        for col in [("quota_used", "INTEGER DEFAULT 0"), ("quota_date", "TEXT DEFAULT ''")]:
            try:
                db.execute(f"ALTER TABLE members ADD COLUMN {col[0]} {col[1]}")
            except sqlite3.OperationalError:
                pass  # column already exists
        db.execute("""
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_ref TEXT UNIQUE,
                chat_id TEXT,
                amount INTEGER,
                product_key TEXT,
                gateway TEXT DEFAULT 'tripay',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT '',
                paid_at TEXT DEFAULT '',
                payload TEXT DEFAULT ''
            )
        """)


def ensure_member(chat_id: str, nama: str = "", username: str = "") -> dict:
    now = datetime.now(WIB)
    trial_end = (now + timedelta(days=7))
    with _conn() as db:
        row = db.execute("SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)).fetchone()
        if row:
            return dict(row)
        db.execute(
            "INSERT INTO members (chat_id, nama, username, tier, status, joined_at, expiry) "
            "VALUES (?, ?, ?, 'starter', 'trial', ?, ?)",
            (str(chat_id), nama or f"User-{chat_id}", username or "",
             now.isoformat(), trial_end.isoformat())
        )
        return {"chat_id": str(chat_id), "nama": nama, "username": username,
                "tier": "starter", "status": "trial",
                "joined_at": now.isoformat(), "expiry": trial_end.isoformat()}


def get_member(chat_id: str) -> dict | None:
    with _conn() as db:
        row = db.execute("SELECT * FROM members WHERE chat_id = ?", (str(chat_id),)).fetchone()
        return dict(row) if row else None


def upgrade_tier(chat_id: str, tier: str, days: int = 30, payment_ref: str = ""):
    now = datetime.now(WIB)
    expiry = (now + timedelta(days=days))
    with _conn() as db:
        existing = db.execute(
            "SELECT chat_id FROM members WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE members SET tier=?, status='paid', expiry=?, payment_ref=? WHERE chat_id=?",
                (tier, expiry.isoformat(), payment_ref, str(chat_id))
            )
        else:
            db.execute(
                "INSERT INTO members (chat_id, tier, status, expiry, payment_ref, joined_at) "
                "VALUES (?, ?, 'paid', ?, ?, ?)",
                (str(chat_id), tier, expiry.isoformat(), payment_ref, now.isoformat())
            )


def insert_payment_order(merchant_ref: str, chat_id: str, amount: int,
                         product_key: str, gateway: str = "tripay",
                         payload: dict = None) -> dict:
    now = datetime.now(WIB).isoformat()
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO payment_orders "
            "(merchant_ref, chat_id, amount, product_key, gateway, status, created_at, payload) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (merchant_ref, str(chat_id), amount, product_key, gateway,
             now, json.dumps(payload or {}))
        )
        return {"merchant_ref": merchant_ref, "status": "pending", "created_at": now}


def mark_payment_paid(merchant_ref: str):
    now = datetime.now(WIB).isoformat()
    with _conn() as db:
        db.execute(
            "UPDATE payment_orders SET status='paid', paid_at=? WHERE merchant_ref=?",
            (now, merchant_ref)
        )


# ── Legacy aliases (compat with handler's MEMBERS_ENABLED import) ──

def register_member(chat_id: str, **kwargs) -> dict:
    """Alias for ensure_member."""
    return ensure_member(str(chat_id), kwargs.get("nama", ""), kwargs.get("username", ""))


def get_member_stats(chat_id: str = None) -> dict:
    """Return member stats."""
    with _conn() as db:
        total = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        return {"total": total}


def mark_paid(chat_id: str, tier: str = "pro", days: int = 30):
    """Mark member as paid."""
    upgrade_tier(str(chat_id), tier, days)


def get_due_members() -> list:
    """Return members nearing expiry."""
    now = datetime.now(WIB)
    due = []
    with _conn() as db:
        rows = db.execute("SELECT chat_id, expiry, tier FROM members WHERE status != 'expired'").fetchall()
        for row in rows:
            try:
                exp = datetime.fromisoformat(row["expiry"])
                days_left = (exp - now).days
                if days_left <= 3:
                    due.append({"chat_id": row["chat_id"], "tier": row["tier"], "days_left": days_left})
            except (ValueError, TypeError):
                pass
    return due


def is_premium(chat_id: str) -> bool:
    member = get_member(str(chat_id))
    return member is not None and member.get("status") == "paid"


def check_quota(chat_id: str) -> dict:
    """Check daily quota: starter=3, pro=50, elite=999."""
    quotas = {"starter": 3, "pro": 50, "elite": 999}
    today = datetime.now(WIB).strftime("%Y-%m-%d")

    with _conn() as db:
        row = db.execute(
            "SELECT tier, quota_used, quota_date FROM members WHERE chat_id = ?",
            (str(chat_id),)
        ).fetchone()

    if not row:
        return {"used": 0, "total": 3, "tier": "starter"}

    tier = row["tier"] or "starter"
    total = quotas.get(tier, 3)

    # Reset if new day
    if row["quota_date"] != today:
        return {"used": 0, "total": total, "tier": tier}

    return {"used": row["quota_used"] or 0, "total": total, "tier": tier}


def use_quota(chat_id: str) -> bool:
    """Consume one quota slot. Returns True if still within limit."""
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    chat_id = str(chat_id)

    quotas = {"starter": 3, "pro": 50, "elite": 999}

    with _conn() as db:
        row = db.execute(
            "SELECT tier, quota_used, quota_date FROM members WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()

        if not row:
            # Auto-register
            ensure_member(chat_id)
            db.execute(
                "UPDATE members SET quota_used=1, quota_date=? WHERE chat_id=?",
                (today, chat_id)
            )
            return True

        tier = row["tier"] or "starter"
        total = quotas.get(tier, 999)
        used = (row["quota_used"] or 0) if row["quota_date"] == today else 0
        used += 1

        db.execute(
            "UPDATE members SET quota_used=?, quota_date=? WHERE chat_id=?",
            (used, today, chat_id)
        )

        return used <= total


def activate_premium(chat_id: str, tier: str = "pro", days: int = 30):
    mark_paid(chat_id, tier, days)


def deactivate_premium(chat_id: str):
    mark_expired(chat_id)


def mark_expired(chat_id: str):
    with _conn() as db:
        db.execute("UPDATE members SET status='expired' WHERE chat_id=?", (str(chat_id),))


init_db()
