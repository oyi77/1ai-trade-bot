"""
Affiliate + Whitelabel — referral tracking and white-label bots with revenue share.

Admin-configurable:
  - Whitelabel revenue share (default 10%)
  - Affiliate commission rate (default 20%)
  - Whitelabel eligibility: active paid plan OR donated ≥100K IDR
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

DB_PATH = Path("data/affiliate.db")
MIN_DONATION_FOR_WHITELABEL = 100_000  # IDR


# ── Database ──────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Idempotent."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS affiliates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            referral_code TEXT NOT NULL UNIQUE,
            commission_rate REAL DEFAULT 20.0,
            total_earned REAL DEFAULT 0.0,
            total_referrals INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referred_user_id TEXT NOT NULL,
            referrer_code TEXT NOT NULL,
            subscribed BOOLEAN DEFAULT 0,
            subscription_tier TEXT,
            commission_paid REAL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS whitelabels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL UNIQUE,
            bot_token TEXT NOT NULL,
            bot_username TEXT,
            custom_name TEXT DEFAULT 'Trading Bot',
            custom_description TEXT,
            revenue_share REAL DEFAULT 10.0,
            active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_affiliates_code ON affiliates(referral_code);
        CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(referrer_code);
        CREATE INDEX IF NOT EXISTS idx_whitelabels_owner ON whitelabels(owner_user_id);

        CREATE TABLE IF NOT EXISTS linked_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            auth_token TEXT NOT NULL,
            label TEXT DEFAULT 'default',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, auth_token)
        );

        CREATE INDEX IF NOT EXISTS idx_linked_user ON linked_accounts(user_id);
    """)
    # Migration: add columns if missing
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns to existing tables."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(whitelabels)").fetchall()]
    if "revenue_share" not in cols:
        conn.execute("ALTER TABLE whitelabels ADD COLUMN revenue_share REAL DEFAULT 10.0")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE whitelabels ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))")


# ── Models ────────────────────────────────────────────────────────────

@dataclass
class Affiliate:
    user_id: str
    referral_code: str = ""
    commission_rate: float = 20.0
    total_earned: float = 0.0
    total_referrals: int = 0

    def __post_init__(self) -> None:
        if not self.referral_code:
            self.referral_code = _generate_code(self.user_id)


@dataclass
class Whitelabel:
    owner_user_id: str
    bot_token: str
    bot_username: str = ""
    custom_name: str = "Trading Bot"
    custom_description: str = ""
    revenue_share: float = 10.0
    active: bool = True


def _generate_code(user_id: str) -> str:
    """Generate a unique 8-char referral code."""
    seed = f"{user_id}{secrets.token_hex(4)}"
    return hashlib.sha256(seed.encode()).hexdigest()[:8]


# ── Eligibility ───────────────────────────────────────────────────────

def can_use_whitelabel(user_id: str) -> tuple[bool, str]:
    """Check if user is eligible for whitelabel.

    Requirements (one of):
      - Active paid plan (Pro, Elite, Whale)
      - Total donations ≥ 100K IDR
    """
    from tradebot.services.plans import Plan, get_total_donations, get_user_plan

    plan = get_user_plan(user_id)
    if plan != Plan.FREE:
        return True, f"Active plan: {plan.value}"

    donated = get_total_donations(user_id)
    if donated >= MIN_DONATION_FOR_WHITELABEL:
        return True, f"Total donations: Rp {donated:,}"

    return False, (
        f"Whitelabel requires:\n"
        f"• Active paid plan (Pro/Elite/Whale)\n"
        f"• OR donations ≥ Rp {MIN_DONATION_FOR_WHITELABEL:,}\\\n\n"
        f"Your donations: Rp {donated:,}\\n"
        f"Upgrade: /plans | Subscribe: /subscribe"
    )


# ── Affiliate API ─────────────────────────────────────────────────────

def get_or_create_affiliate(user_id: str) -> Affiliate:
    """Get or create an affiliate for a user."""
    init_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM affiliates WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row:
        conn.close()
        return Affiliate(
            user_id=row["user_id"],
            referral_code=row["referral_code"],
            commission_rate=row["commission_rate"],
            total_earned=row["total_earned"],
            total_referrals=row["total_referrals"],
        )
    code = _generate_code(user_id)
    conn.execute(
        "INSERT INTO affiliates (user_id, referral_code) VALUES (?, ?)",
        (user_id, code),
    )
    conn.commit()
    conn.close()
    return Affiliate(user_id=user_id, referral_code=code)


def get_affiliate_by_code(code: str) -> Affiliate | None:
    """Look up affiliate by referral code."""
    init_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM affiliates WHERE referral_code = ?", (code,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Affiliate(
        user_id=row["user_id"],
        referral_code=row["referral_code"],
        commission_rate=row["commission_rate"],
        total_earned=row["total_earned"],
        total_referrals=row["total_referrals"],
    )


def record_referral(referred_user_id: str, referrer_code: str) -> bool:
    """Record a new referral. Returns True if first time."""
    init_db()
    conn = _get_db()
    existing = conn.execute(
        "SELECT id FROM referrals WHERE referred_user_id = ? AND referrer_code = ?",
        (referred_user_id, referrer_code),
    ).fetchone()
    if existing:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO referrals (referred_user_id, referrer_code) VALUES (?, ?)",
        (referred_user_id, referrer_code),
    )
    conn.execute(
        "UPDATE affiliates SET total_referrals = total_referrals + 1 WHERE referral_code = ?",
        (referrer_code,),
    )
    conn.commit()
    conn.close()
    return True


def get_referral_stats(user_id: str) -> dict[str, Any]:
    """Get affiliate statistics."""
    init_db()
    conn = _get_db()
    aff = conn.execute(
        "SELECT * FROM affiliates WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not aff:
        conn.close()
        return {"referrals": [], "total_referrals": 0, "total_earned": 0, "code": ""}
    refs = conn.execute(
        "SELECT * FROM referrals WHERE referrer_code = ? ORDER BY created_at DESC LIMIT 50",
        (aff["referral_code"],),
    ).fetchall()
    conn.close()
    return {
        "referrals": [dict(r) for r in refs],
        "total_referrals": aff["total_referrals"],
        "total_earned": aff["total_earned"],
        "code": aff["referral_code"],
        "commission_rate": aff["commission_rate"],
    }


def set_affiliate_rate(user_id: str, rate: float) -> bool:
    """Admin: set affiliate commission rate."""
    init_db()
    conn = _get_db()
    conn.execute(
        "UPDATE affiliates SET commission_rate = ? WHERE user_id = ?",
        (max(0, min(100, rate)), user_id),
    )
    conn.commit()
    conn.close()
    LOG.info("Affiliate rate for %s set to %.1f%%", user_id, rate)
    return True


# ── Whitelabel API ────────────────────────────────────────────────────

def create_whitelabel(
    owner_user_id: str,
    bot_token: str,
    bot_username: str = "",
    custom_name: str = "Trading Bot",
    revenue_share: float = 10.0,
) -> Whitelabel:
    """Register a whitelabel bot."""
    init_db()
    conn = _get_db()

    conn.execute(
        """INSERT OR REPLACE INTO whitelabels
           (owner_user_id, bot_token, bot_username, custom_name, revenue_share, active, updated_at)
           VALUES (?, ?, ?, ?, ?, 1, datetime('now'))""",
        (owner_user_id, bot_token, bot_username, custom_name, revenue_share),
    )
    conn.commit()
    conn.close()
    return Whitelabel(
        owner_user_id=owner_user_id,
        bot_token=bot_token,
        bot_username=bot_username,
        custom_name=custom_name,
        revenue_share=revenue_share,
        active=True,
    )


def get_whitelabel(owner_user_id: str) -> Whitelabel | None:
    """Get whitelabel config for a user."""
    init_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM whitelabels WHERE owner_user_id = ? AND active = 1",
        (owner_user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Whitelabel(
        owner_user_id=row["owner_user_id"],
        bot_token=row["bot_token"],
        bot_username=row["bot_username"] or "",
        custom_name=row["custom_name"] or "Trading Bot",
        custom_description=row["custom_description"] or "",
        revenue_share=float(row.get("revenue_share", 10.0)),
        active=bool(row["active"]),
    )


def deactivate_whitelabel(owner_user_id: str) -> bool:
    """Deactivate a whitelabel bot."""
    init_db()
    conn = _get_db()
    conn.execute(
        "UPDATE whitelabels SET active = 0, updated_at = datetime('now') WHERE owner_user_id = ?",
        (owner_user_id,),
    )
    conn.commit()
    conn.close()
    return True


def set_whitelabel_share(owner_user_id: str, share: float) -> bool:
    """Admin: set whitelabel revenue share percentage."""
    init_db()
    conn = _get_db()
    conn.execute(
        "UPDATE whitelabels SET revenue_share = ?, updated_at = datetime('now') WHERE owner_user_id = ?",
        (max(0, min(100, share)), owner_user_id),
    )
    conn.commit()
    conn.close()
    LOG.info("Whitelabel share for %s set to %.1f%%", owner_user_id, share)
    return True


def get_all_active_whitelabels() -> list[Whitelabel]:
    """Get all active whitelabel bots (for the server to spawn)."""
    init_db()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM whitelabels WHERE active = 1"
    ).fetchall()
    conn.close()
    return [
        Whitelabel(
            owner_user_id=row["owner_user_id"],
            bot_token=row["bot_token"],
            bot_username=row["bot_username"] or "",
            custom_name=row["custom_name"] or "Trading Bot",
            custom_description=row["custom_description"] or "",
            revenue_share=float(row.get("revenue_share", 10.0)),
            active=True,
        )
        for row in rows
    ]
