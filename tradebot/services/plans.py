"""
Plan & Subscription Service — monetization with tiered plans and donations.

Plans:
  Free      Rp 0      1 signal category, 5/day, no auto-trade
  Pro       Rp 50k    4 categories, unlimited, auto-trade 1 platform
  Elite     Rp 150k   All 6 categories, unlimited, all platforms, priority
  Whale     Rp 500k   Everything + custom strategies + API access

Donation: one-time any amount → Supporter badge
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradebot.config import settings
from tradebot.signals.subscriptions import SignalCategory

LOG = logging.getLogger(__name__)

# ── Plan Tiers ────────────────────────────────────────────────────────

class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"
    ELITE = "elite"
    WHALE = "whale"


PLAN_DETAILS: dict[Plan, dict[str, Any]] = {
    Plan.FREE: {
        "name": "Free",
        "emoji": "🆓",
        "price_idr": 0,
        "price_display": "Gratis",
        "signal_categories": [SignalCategory.TREND],
        "max_signals_per_day": 5,
        "auto_trade": False,
        "max_platforms": 0,
        "history_days": 1,
        "priority_support": False,
        "api_access": False,
        "custom_strategies": False,
        "description": "Basic trend signals, 5/day",
    },
    Plan.PRO: {
        "name": "Pro",
        "emoji": "⭐",
        "price_idr": 50_000,
        "price_display": "Rp 50.000",
        "signal_categories": [
            SignalCategory.SMC, SignalCategory.TREND,
            SignalCategory.STRUCTURE, SignalCategory.QUANT,
        ],
        "max_signals_per_day": 999,
        "auto_trade": True,
        "max_platforms": 1,
        "history_days": 7,
        "priority_support": False,
        "api_access": False,
        "custom_strategies": False,
        "description": "4 categories, unlimited signals, auto-trade",
    },
    Plan.ELITE: {
        "name": "Elite",
        "emoji": "💎",
        "price_idr": 150_000,
        "price_display": "Rp 150.000",
        "signal_categories": list(SignalCategory),
        "max_signals_per_day": 999,
        "auto_trade": True,
        "max_platforms": 3,
        "history_days": 30,
        "priority_support": True,
        "api_access": False,
        "custom_strategies": False,
        "description": "All categories, 3 platforms, priority support",
    },
    Plan.WHALE: {
        "name": "Whale",
        "emoji": "🐋",
        "price_idr": 500_000,
        "price_display": "Rp 500.000",
        "signal_categories": list(SignalCategory),
        "max_signals_per_day": 999,
        "auto_trade": True,
        "max_platforms": 4,
        "history_days": 90,
        "priority_support": True,
        "api_access": True,
        "custom_strategies": True,
        "description": "Everything + custom strategies + API access",
    },
}

PLAN_UPGRADE_PATH: dict[Plan, Plan | None] = {
    Plan.FREE: Plan.PRO,
    Plan.PRO: Plan.ELITE,
    Plan.ELITE: Plan.WHALE,
    Plan.WHALE: None,
}


def get_plan_features(plan: Plan) -> dict[str, Any]:
    return PLAN_DETAILS.get(plan, PLAN_DETAILS[Plan.FREE])


def can_access_category(plan: Plan, category: SignalCategory) -> bool:
    """Check if a plan has access to a signal category."""
    features = get_plan_features(plan)
    return category in features["signal_categories"]


def can_auto_trade(plan: Plan) -> bool:
    return get_plan_features(plan)["auto_trade"]


def plan_from_str(name: str) -> Plan:
    try:
        return Plan(name.lower())
    except ValueError:
        return Plan.FREE


# ── Subscription Database ─────────────────────────────────────────────

DB_PATH = Path(settings.DATA_DIR) / "plans.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_plan_db() -> None:
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_plans (
            user_id         TEXT PRIMARY KEY,
            plan            TEXT NOT NULL DEFAULT 'free',
            started_at      INTEGER NOT NULL,
            expires_at      INTEGER,
            auto_renew      INTEGER DEFAULT 0,
            payment_ref     TEXT DEFAULT '',
            upgraded_from   TEXT DEFAULT '',
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS donations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            amount_idr      INTEGER NOT NULL,
            message         TEXT DEFAULT '',
            payment_ref     TEXT DEFAULT '',
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS payment_invoices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            merchant_ref    TEXT UNIQUE NOT NULL,
            plan            TEXT NOT NULL,
            amount_idr      INTEGER NOT NULL,
            status          TEXT DEFAULT 'pending'
                            CHECK(status IN ('pending','paid','expired','cancelled')),
            payment_url     TEXT DEFAULT '',
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            paid_at         INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_plans_user ON user_plans(user_id);
        CREATE INDEX IF NOT EXISTS idx_plans_plan ON user_plans(plan);
        CREATE INDEX IF NOT EXISTS idx_donations_user ON donations(user_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_user ON payment_invoices(user_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_ref ON payment_invoices(merchant_ref);

        CREATE TABLE IF NOT EXISTS plan_config (
            plan            TEXT PRIMARY KEY,
            price_idr       INTEGER NOT NULL,
            updated_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
    """)
    conn.commit()
    conn.close()
    LOG.info("Plan database ready at %s", DB_PATH)


# ── User Plan Operations ──────────────────────────────────────────────

def get_user_plan(user_id: str) -> Plan:
    """Get current plan for a user. Defaults to FREE."""
    init_plan_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT plan, expires_at FROM user_plans WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()

    if not row:
        # Auto-create free plan
        set_user_plan(user_id, Plan.FREE, expires_at=None)
        return Plan.FREE

    plan = Plan(row["plan"])
    expires = row["expires_at"]

    # Check expiry for paid plans
    if plan != Plan.FREE and expires and expires < int(time.time()):
        set_user_plan(user_id, Plan.FREE, expires_at=None)
        LOG.info("User %s plan expired — downgraded to FREE", user_id)
        return Plan.FREE

    return plan


def set_user_plan(
    user_id: str,
    plan: Plan,
    expires_at: int | None = None,
    payment_ref: str = "",
    upgraded_from: str = "",
) -> None:
    init_plan_db()
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO user_plans
           (user_id, plan, started_at, expires_at, payment_ref, upgraded_from)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, plan.value, int(time.time()), expires_at, payment_ref, upgraded_from),
    )
    conn.commit()
    conn.close()
    LOG.info("User %s plan set to %s (expires: %s)", user_id, plan.value, expires_at)


# ── Payment Invoices ──────────────────────────────────────────────────

def create_invoice(
    user_id: str, plan: Plan, amount_idr: int, payment_url: str = ""
) -> str:
    """Create a payment invoice. Returns merchant_ref."""
    init_plan_db()
    raw = f"{user_id}:{plan.value}:{time.time()}"
    merchant_ref = f"INV-{hashlib.sha256(raw.encode()).hexdigest()[:12].upper()}"
    conn = _get_db()
    conn.execute(
        """INSERT INTO payment_invoices (user_id, merchant_ref, plan, amount_idr, payment_url)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, merchant_ref, plan.value, amount_idr, payment_url),
    )
    conn.commit()
    conn.close()
    LOG.info("Invoice %s created for user %s — %s Rp %s", merchant_ref, user_id, plan.value, amount_idr)
    return merchant_ref


def confirm_payment(merchant_ref: str) -> dict[str, Any] | None:
    """Mark invoice as paid and activate plan."""
    init_plan_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM payment_invoices WHERE merchant_ref = ? AND status = 'pending'",
        (merchant_ref,),
    ).fetchone()
    if not row:
        conn.close()
        return None

    conn.execute(
        "UPDATE payment_invoices SET status = 'paid', paid_at = ? WHERE merchant_ref = ?",
        (int(time.time()), merchant_ref),
    )

    # Activate subscription
    user_id = row["user_id"]
    plan = Plan(row["plan"])
    duration = 30 * 86400  # 30 days for all paid plans
    expires_at = int(time.time()) + duration
    old_plan = get_user_plan(user_id)
    set_user_plan(
        user_id, plan, expires_at=expires_at,
        payment_ref=merchant_ref, upgraded_from=old_plan.value,
    )
    conn.commit()
    conn.close()

    LOG.info("Payment confirmed: %s → %s (%s)", user_id, plan.value, merchant_ref)
    return {
        "user_id": user_id,
        "plan": plan.value,
        "amount": row["amount_idr"],
        "expires_at": expires_at,
    }


def get_pending_invoice(merchant_ref: str) -> dict[str, Any] | None:
    init_plan_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM payment_invoices WHERE merchant_ref = ?",
        (merchant_ref,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_invoices(user_id: str) -> list[dict[str, Any]]:
    init_plan_db()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM payment_invoices WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Donations ─────────────────────────────────────────────────────────

def add_donation(user_id: str, amount_idr: int, message: str = "", payment_ref: str = "") -> int:
    """Record a donation. Returns donation ID."""
    init_plan_db()
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO donations (user_id, amount_idr, message, payment_ref)
           VALUES (?, ?, ?, ?)""",
        (user_id, amount_idr, message, payment_ref),
    )
    conn.commit()
    donation_id = cur.lastrowid
    conn.close()
    LOG.info("Donation #%d: user=%s amount=%s", donation_id, user_id, amount_idr)
    return donation_id


def get_user_donations(user_id: str) -> list[dict[str, Any]]:
    init_plan_db()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM donations WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_total_donations(user_id: str) -> int:
    init_plan_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_idr), 0) as total FROM donations WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["total"] if row else 0


# ── Configurable Pricing ──────────────────────────────────────────────

def get_plan_price(plan: Plan) -> int:
    """Get price for a plan, checking DB overrides first."""
    init_plan_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT price_idr FROM plan_config WHERE plan = ?", (plan.value,)
    ).fetchone()
    conn.close()
    if row:
        return int(row["price_idr"])
    return PLAN_DETAILS[plan]["price_idr"]


def set_plan_price(plan: Plan, price_idr: int) -> bool:
    """Admin: override a plan's price."""
    init_plan_db()
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO plan_config (plan, price_idr, updated_at)
           VALUES (?, ?, strftime('%s','now'))""",
        (plan.value, max(0, price_idr)),
    )
    conn.commit()
    conn.close()
    LOG.info("Plan %s price set to Rp %s", plan.value, price_idr)
    return True


def get_all_plan_prices() -> dict[str, int]:
    """Get all current plan prices (with overrides)."""
    return {p.value: get_plan_price(p) for p in Plan}


 # ── Plan Stats ────────────────────────────────────────────────────────

def get_plan_stats() -> dict[str, int]:
    """Get count of users per plan."""
    init_plan_db()
    conn = _get_db()
    rows = conn.execute(
        "SELECT plan, COUNT(*) as cnt FROM user_plans GROUP BY plan"
    ).fetchall()
    conn.close()
    stats = {p.value: 0 for p in Plan}
    for row in rows:
        stats[row["plan"]] = row["cnt"]
    return stats


def get_total_revenue() -> int:
    """Get total revenue from paid invoices."""
    init_plan_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_idr), 0) as total FROM payment_invoices WHERE status = 'paid'"
    ).fetchone()
    conn.close()
    return row["total"] if row else 0
