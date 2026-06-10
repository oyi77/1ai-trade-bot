"""
Affiliate & Whitelabel system for StockityBot.

Affiliate:
  - Users get unique referral codes
  - Track referrals via /start ref_<code>
  - Earn commissions on referred user subscriptions

Whitelabel:
  - Users run their own bot instance with custom token
  - All commands mirrored, custom branding
  - Managed via /whitelabel command
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

DB_PATH = Path("data/affiliate.db")


# ── Database ──────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
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
            custom_name TEXT DEFAULT 'My Trading Bot',
            custom_description TEXT,
            active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_affiliates_code ON affiliates(referral_code);
        CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(referrer_code);
        CREATE INDEX IF NOT EXISTS idx_whitelabels_owner ON whitelabels(owner_user_id);
    """)
    conn.commit()
    conn.close()


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
    active: bool = True


def _generate_code(user_id: str) -> str:
    """Generate a unique 8-char referral code."""
    seed = f"{user_id}{secrets.token_hex(4)}"
    return hashlib.sha256(seed.encode()).hexdigest()[:8]


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

    # Check if already referred
    existing = conn.execute(
        "SELECT id FROM referrals WHERE referred_user_id = ?", (referred_user_id,)
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
        return {"error": "Not an affiliate yet. Use /affiliate to get started."}

    referrals = conn.execute(
        "SELECT * FROM referrals WHERE referrer_code = ? ORDER BY created_at DESC LIMIT 20",
        (aff["referral_code"],),
    ).fetchall()

    conn.close()

    return {
        "referral_code": aff["referral_code"],
        "referral_link": f"https://t.me/StockityBot?start=ref_{aff['referral_code']}",
        "commission_rate": aff["commission_rate"],
        "total_earned": aff["total_earned"],
        "total_referrals": aff["total_referrals"],
        "recent_referrals": [
            {
                "user_id": r["referred_user_id"],
                "subscribed": bool(r["subscribed"]),
                "tier": r["subscription_tier"],
                "commission": r["commission_paid"],
            }
            for r in referrals
        ],
    }


# ── Whitelabel API ────────────────────────────────────────────────────

def create_whitelabel(
    owner_user_id: str,
    bot_token: str,
    bot_username: str = "",
    custom_name: str = "Trading Bot",
) -> Whitelabel:
    """Register a whitelabel bot."""
    init_db()
    conn = _get_db()

    conn.execute(
        """INSERT OR REPLACE INTO whitelabels
           (owner_user_id, bot_token, bot_username, custom_name)
           VALUES (?, ?, ?, ?)""",
        (owner_user_id, bot_token, bot_username, custom_name),
    )
    conn.commit()
    conn.close()
    return Whitelabel(
        owner_user_id=owner_user_id,
        bot_token=bot_token,
        bot_username=bot_username,
        custom_name=custom_name,
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
        bot_username=row["bot_username"],
        custom_name=row["custom_name"],
        custom_description=row["custom_description"],
        active=bool(row["active"]),
    )


def deactivate_whitelabel(owner_user_id: str) -> bool:
    """Deactivate a whitelabel bot."""
    init_db()
    conn = _get_db()
    conn.execute(
        "UPDATE whitelabels SET active = 0 WHERE owner_user_id = ?",
        (owner_user_id,),
    )
    conn.commit()
    conn.close()
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
            bot_username=row["bot_username"],
            custom_name=row["custom_name"],
            custom_description=row["custom_description"],
            active=True,
        )
        for row in rows
    ]
