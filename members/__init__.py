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

    # NOTE: CAPI Purchase is fired exclusively from the webhook handler
    # (payment_webhook.py) — the single entry point for payment confirmation — to
    # prevent double-firing.


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


def get_pending_order(chat_id: str, tier: str = None) -> dict | None:
    """Get active pending payment order for a user. Returns None if none exists."""
    with _conn() as db:
        if tier:
            row = db.execute(
                "SELECT * FROM payment_orders WHERE chat_id=? AND status='pending' AND product_key=? "
                "ORDER BY id DESC LIMIT 1",
                (str(chat_id), tier)
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM payment_orders WHERE chat_id=? AND status='pending' "
                "ORDER BY id DESC LIMIT 1",
                (str(chat_id),)
            ).fetchone()
        return dict(row) if row else None


def get_payment_order_by_ref(merchant_ref: str) -> dict | None:
    """Get a payment order by merchant_ref (any status)."""
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM payment_orders WHERE merchant_ref=? ORDER BY id DESC LIMIT 1",
            (merchant_ref,)
        ).fetchone()
        return dict(row) if row else None


def expire_old_pending_orders(hours: int = 24):
    """Mark pending orders older than N hours as expired."""
    cutoff = (datetime.now(WIB) - timedelta(hours=hours)).isoformat()
    with _conn() as db:
        count = db.execute(
            "UPDATE payment_orders SET status='expired' WHERE status='pending' AND created_at < ?",
            (cutoff,)
        ).rowcount
        return count


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
    quotas = {"starter": 5, "pro": 20, "elite": 999, "lifetime": 999, "donor": 999}
    today = datetime.now(WIB).strftime("%Y-%m-%d")

    with _conn() as db:
        row = db.execute(
            "SELECT tier, quota_used, quota_date FROM members WHERE chat_id = ?",
            (str(chat_id),)
        ).fetchone()

    if not row:
        return {"used": 0, "total": 5, "tier": "starter"}

    tier = row["tier"] or "starter"
    total = quotas.get(tier, 5)

    # Reset if new day
    if row["quota_date"] != today:
        return {"used": 0, "total": total, "tier": tier}

    return {"used": row["quota_used"] or 0, "total": total, "tier": tier}


def use_quota(chat_id: str) -> bool:
    """Consume one quota slot. Returns True if still within limit."""
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    chat_id = str(chat_id)

    quotas = {"starter": 5, "pro": 20, "elite": 999, "lifetime": 999, "donor": 999}

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


def get_total_donations() -> int:
    """Return sum of all paid donation amounts."""
    with _conn() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payment_orders WHERE status='paid'"
        ).fetchone()
        return row[0] if row else 0


def get_monthly_fuel_stats() -> dict:
    """Return monthly donation stats for fuel gauge.
    Returns: {total: int, donor_count: int, month: str}"""
    month_start = datetime.now(WIB).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _conn() as db:
        total = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payment_orders WHERE status='paid' AND paid_at >= ?",
            (month_start,)
        ).fetchone()[0]
        donors = db.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM payment_orders WHERE status='paid' AND paid_at >= ?",
            (month_start,)
        ).fetchone()[0]
    return {
        "total": total or 0,
        "donor_count": donors or 0,
        "month": datetime.now(WIB).strftime("%B %Y"),
    }


def get_user_last_donation(chat_id: str) -> dict | None:
    """Return user's last donation info. {days_ago: int, amount: int} or None."""
    with _conn() as db:
        row = db.execute(
            "SELECT amount, paid_at FROM payment_orders WHERE chat_id=? AND status='paid' ORDER BY paid_at DESC LIMIT 1",
            (str(chat_id),)
        ).fetchone()
        if not row:
            return None
        paid_dt = datetime.fromisoformat(row["paid_at"])
        days_ago = (datetime.now(WIB) - paid_dt).days
        return {"days_ago": days_ago, "amount": row["amount"]}


def get_stale_donors(min_days: int = 30) -> list[dict]:
    """Find all subscriber members whose last donation was > min_days ago.
    Returns lista of {chat_id, days_since_last, last_amount, username}"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(WIB)
    cutoff = now - timedelta(days=min_days)
    cutoff_str = cutoff.isoformat()
    stale = []
    with _conn() as db:
        rows = db.execute(
            "SELECT chat_id, username, nama FROM members WHERE tier IN ('pro','elite','lifetime','donor')"
        ).fetchall()
        for r in rows:
            last = db.execute(
                "SELECT amount, paid_at FROM payment_orders WHERE chat_id=? AND status='paid' ORDER BY paid_at DESC LIMIT 1",
                (r["chat_id"],)
            ).fetchone()
            if last and last["paid_at"] < cutoff_str:
                paid_dt = datetime.fromisoformat(last["paid_at"])
                days_since = (now - paid_dt).days
                stale.append({
                    "chat_id": r["chat_id"],
                    "username": r["username"] or r["nama"] or r["chat_id"],
                    "days_since": days_since,
                    "last_amount": last["amount"],
                })
    return stale


init_db()
