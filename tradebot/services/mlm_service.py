"""
MLM Service — Multi-Level Marketing with Matahari (Sun) model.

Architecture:
    Every payment is split:
        - 70% → Platform (GUARANTEED, never touched by MLM)
        - 30% → MLM Commission Pool (distributed to upline tree)

    Pool distribution per level (of the 30% pool):
        Level 1 (direct upline):  40% of pool = 12% of total payment
        Level 2:                   20% of pool =  6% of total payment
        Level 3:                   10% of pool =  3% of total payment
        Level 4:                    5% of pool =  1.5% of total payment
        Level 5+:                   3.3% of pool = 1% of total payment

    Platform always keeps: 70% + (30% - distributed) = 70%+ minimum
    This ensures platform NEVER loses money regardless of tree depth.

Tables (in tradebot.db):
    mlm_tree: user_id, upline_id, level, role (whitelabel/affiliate), joined_at
    mlm_ledger: id, user_id, type (earn/spend/claim), amount, source,
                reference_id, created_at, description
    mlm_claims: id, user_id, amount, status (pending/approved/rejected),
                admin_id, created_at, resolved_at
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import time
from typing import Any

from tradebot.storage.repository import get_repo

LOG = logging.getLogger("tradebot.services.mlm_service")

# ── Revenue Split ────────────────────────────────────────────────────
# Platform always gets 70% of every payment. 30% goes to MLM pool.
PLATFORM_SHARE = 0.70
MLM_POOL_SHARE = 0.30

# Pool distribution per level (percentage of the 30% pool)
# Level 1 = direct upline, Level 2 = upline's upline, etc.
POOL_DISTRIBUTION = [0.40, 0.20, 0.10, 0.05, 0.033, 0.033, 0.033, 0.033, 0.033, 0.033]

# Verify: total pool distribution must not exceed 100% of pool
_pool_total = sum(POOL_DISTRIBUTION)
assert _pool_total <= 1.0, \
    f"Pool distribution sum ({_pool_total:.1%}) exceeds 100% of pool!"

# Effective commission rates (percentage of total payment per level)
# Level 1: 40% of 30% = 12% of total
# Level 2: 20% of 30% = 6% of total
# etc.
EFFECTIVE_RATES = [r * MLM_POOL_SHARE for r in POOL_DISTRIBUTION]

MIN_CLAIM_AMOUNT = 100_000  # Rp100.000 minimum claim


def _storage():
    return get_repo()


def init_tables() -> None:
    """Create MLM tables. Called from TradeTracker._init_db."""
    store = _storage()
    store.execute("""
        CREATE TABLE IF NOT EXISTS mlm_tree (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            upline_id TEXT DEFAULT '',
            level INTEGER DEFAULT 0,
            role TEXT DEFAULT 'affiliate',
            joined_at INTEGER NOT NULL
        )
    """)
    store.execute("""
        CREATE TABLE IF NOT EXISTS mlm_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            source TEXT DEFAULT '',
            reference_id TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at INTEGER NOT NULL
        )
    """)
    store.execute("""
        CREATE TABLE IF NOT EXISTS mlm_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_id TEXT DEFAULT '',
            admin_note TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            resolved_at INTEGER DEFAULT 0
        )
    """)
    # Migration: add columns if missing
    for col, col_type in [
        ("description", "TEXT DEFAULT ''"),
        ("role", "TEXT DEFAULT 'affiliate'"),
    ]:
        with contextlib.suppress(Exception):
            _storage().execute(f"ALTER TABLE mlm_ledger ADD COLUMN {col} {col_type}")
    with contextlib.suppress(Exception):
        _storage().execute("ALTER TABLE mlm_tree ADD COLUMN role TEXT DEFAULT 'affiliate'")


def register_user(user_id: str, referral_code: str = "") -> dict[str, Any]:
    """Register a user in the MLM tree.

    If referral_code is provided, find the referrer and set as upline.
    Otherwise, user is at root level (level 0).

    Returns:
        Dict with user_id, upline_id, level.
    """
    now = int(time.time())
    store = _storage()

    # Check if already registered
    existing = store.fetchone("SELECT * FROM mlm_tree WHERE user_id=?", (user_id,))
    if existing:
        return {"user_id": user_id, "upline_id": existing[2], "level": existing[3]}

    upline_id = ""
    level = 0

    if referral_code:
        # Find referrer by referral code
        from tradebot.bots.stockity.affiliate import get_affiliate_by_code
        referrer = get_affiliate_by_code(referral_code)
        if referrer:
            upline_id = referrer.user_id
            # Get referrer's level
            upline = store.fetchone("SELECT * FROM mlm_tree WHERE user_id=?", (upline_id,))
            if upline:
                level = upline[3] + 1
                if level >= len(COMMISSION_RATES):
                    level = len(COMMISSION_RATES) - 1

    store.execute(
        "INSERT INTO mlm_tree (user_id, upline_id, level, joined_at) VALUES (?, ?, ?, ?)",
        (user_id, upline_id, level, now),
    )

    LOG.info("MLM registered: user=%s upline=%s level=%d", user_id, upline_id, level)
    return {"user_id": user_id, "upline_id": upline_id, "level": level}


def record_commission(
    payer_user_id: str,
    amount_idr: int,
    source: str,
    reference_id: str = "",
) -> list[dict[str, Any]]:
    """Record a payment and distribute commissions up the MLM tree.

    Architecture:
        70% → Platform (guaranteed, never touched)
        30% → MLM Pool → distributed per POOL_DISTRIBUTION to upline levels

    Platform always keeps at least 70% of every payment.
    Any undistributed pool remainder also goes to platform.

    Args:
        payer_user_id: The user who made the payment
        amount_idr: Payment amount in IDR
        source: Source of payment (subscription, key_purchase, etc.)
        reference_id: Reference ID for tracking

    Returns:
        List of commission records distributed.
    """
    store = _storage()
    now = int(time.time())
    commissions: list[dict[str, Any]] = []

    # Calculate pool amount (30% of payment)
    pool_amount = int(amount_idr * MLM_POOL_SHARE)
    platform_amount = amount_idr - pool_amount  # 70% guaranteed
    pool_remaining = pool_amount

    # Walk up the tree
    current_id = payer_user_id
    level = 0

    while current_id and level < len(POOL_DISTRIBUTION):
        row = store.fetchone(
            "SELECT upline_id FROM mlm_tree WHERE user_id=?",
            (current_id,),
        )
        if not row:
            break

        upline_id = row[0]
        if not upline_id:
            break

        # Calculate commission from pool for this level
        pool_rate = POOL_DISTRIBUTION[level]
        commission_amount = int(pool_amount * pool_rate)

        # Don't exceed remaining pool
        if commission_amount > pool_remaining:
            commission_amount = pool_remaining

        if commission_amount > 0:
            store.execute(
                """INSERT INTO mlm_ledger
                   (user_id, type, amount, source, reference_id, description, created_at)
                   VALUES (?, 'earn', ?, ?, ?, ?, ?)""",
                (
                    upline_id,
                    commission_amount,
                    source,
                    reference_id,
                    f"Komisi level {level + 1} dari {payer_user_id}",
                    now,
                ),
            )

            # Update affiliate total_earned
            with contextlib.suppress(Exception):
                store.execute(
                    "UPDATE affiliates SET total_earned = total_earned + ? WHERE user_id=?",
                    (commission_amount, upline_id),
                )

            commissions.append({
                "user_id": upline_id,
                "level": level + 1,
                "pool_rate": pool_rate,
                "effective_rate": pool_rate * MLM_POOL_SHARE,
                "amount": commission_amount,
            })

            pool_remaining -= commission_amount
            LOG.info(
                "Commission: upline=%s level=%d pool_rate=%.1f%% amount=%d pool_remaining=%d source=%s",
                upline_id, level + 1, pool_rate * 100,
                commission_amount, pool_remaining, source,
            )

        current_id = upline_id
        level += 1

    # Any remaining pool goes to platform (bonus profit)
    if pool_remaining > 0:
        platform_amount += pool_remaining
        LOG.info("Pool remainder to platform: %d (total platform: %d)", pool_remaining, platform_amount)

    LOG.info(
        "Commission complete: payment=%d platform=%d pool=%d distributed=%d source=%s",
        amount_idr, platform_amount, pool_amount,
        pool_amount - pool_remaining, source,
    )

    return commissions



def get_balance(user_id: str) -> dict[str, Any]:
    """Get user's total earned, claimed, and available balance."""
    store = _storage()
    int(time.time())

    # Total earned
    earned_row = store.fetchone(
        "SELECT COALESCE(SUM(amount), 0) FROM mlm_ledger WHERE user_id=? AND type='earn'",
        (user_id,),
    )
    total_earned = earned_row[0] if earned_row else 0

    # Total claimed
    claimed_row = store.fetchone(
        "SELECT COALESCE(SUM(amount), 0) FROM mlm_ledger WHERE user_id=? AND type='claim'",
        (user_id,),
    )
    total_claimed = claimed_row[0] if claimed_row else 0

    # Pending claims
    pending_row = store.fetchone(
        "SELECT COALESCE(SUM(amount), 0) FROM mlm_claims WHERE user_id=? AND status='pending'",
        (user_id,),
    )
    pending_claims = pending_row[0] if pending_row else 0

    available = total_earned - total_claimed - pending_claims

    return {
        "total_earned": total_earned,
        "total_claimed": total_claimed,
        "pending_claims": pending_claims,
        "available": available,
        "can_claim": available >= MIN_CLAIM_AMOUNT,
    }


def get_downline(user_id: str) -> list[dict[str, Any]]:
    """Get all users directly referred by this user (level 1 downline)."""
    rows = _storage().fetchall(
        "SELECT * FROM mlm_tree WHERE upline_id=? ORDER BY joined_at DESC",
        (user_id,),
    )
    return [dict(r) for r in rows]


def get_ledger(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Get transaction history for a user."""
    rows = _storage().fetchall(
        "SELECT * FROM mlm_ledger WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def create_claim(user_id: str, amount: int) -> dict[str, Any]:
    """Create a claim request for available earnings.

    Args:
        user_id: Telegram chat_id
        amount: Amount to claim (must be >= MIN_CLAIM_AMOUNT)

    Returns:
        Dict with claim details or error.
    """
    balance = get_balance(user_id)
    if amount < MIN_CLAIM_AMOUNT:
        return {"error": f"Minimal claim Rp{MIN_CLAIM_AMOUNT:,}"}
    if amount > balance["available"]:
        return {
            "error": f"Saldo tidak mencukupi. Tersedia: Rp{balance['available']:,}",
        }

    now = int(time.time())
    claim_id = f"cl_{int(time.time() * 1000)}_{secrets.token_hex(4)}"

    store = _storage()
    store.execute(
        """INSERT INTO mlm_claims
           (claim_id, user_id, amount, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (claim_id, user_id, amount, now),
    )

    LOG.info("Claim created: user=%s amount=%d claim_id=%s", user_id, amount, claim_id)
    return {
        "success": True,
        "claim_id": claim_id,
        "amount": amount,
        "status": "pending",
    }


def approve_claim(claim_id: str, admin_id: str) -> dict[str, Any]:
    """Admin: approve a claim request."""
    store = _storage()
    now = int(time.time())

    row = store.fetchone(
        "SELECT * FROM mlm_claims WHERE claim_id=? AND status='pending'",
        (claim_id,),
    )
    if not row:
        return {"error": "Claim tidak ditemukan atau sudah diproses."}

    claim = dict(row)
    store.execute(
        """UPDATE mlm_claims SET status='approved', admin_id=?, resolved_at=?
           WHERE claim_id=?""",
        (admin_id, now, claim_id),
    )

    # Record in ledger
    store.execute(
        """INSERT INTO mlm_ledger
           (user_id, type, amount, source, reference_id, description, created_at)
           VALUES (?, 'claim', ?, 'claim_approved', ?, ?, ?)""",
        (claim["user_id"], claim["amount"], claim_id, f"Claim approved by {admin_id}", now),
    )

    LOG.info("Claim approved: claim_id=%s admin=%s amount=%d", claim_id, admin_id, claim["amount"])
    return {"success": True, "claim_id": claim_id, "status": "approved"}


def reject_claim(claim_id: str, admin_id: str, note: str = "") -> dict[str, Any]:
    """Admin: reject a claim request."""
    store = _storage()
    now = int(time.time())

    row = store.fetchone(
        "SELECT * FROM mlm_claims WHERE claim_id=? AND status='pending'",
        (claim_id,),
    )
    if not row:
        return {"error": "Claim tidak ditemukan atau sudah diproses."}

    store.execute(
        """UPDATE mlm_claims SET status='rejected', admin_id=?, admin_note=?, resolved_at=?
           WHERE claim_id=?""",
        (admin_id, note, now, claim_id),
    )

    LOG.info("Claim rejected: claim_id=%s admin=%s", claim_id, admin_id)
    return {"success": True, "claim_id": claim_id, "status": "rejected"}


def get_pending_claims() -> list[dict[str, Any]]:
    """Admin: get all pending claims."""
    rows = _storage().fetchall(
        "SELECT * FROM mlm_claims WHERE status='pending' ORDER BY created_at ASC",
    )
    return [dict(r) for r in rows]


def format_balance(user_id: str) -> str:
    """Format balance as HTML for Telegram."""
    bal = get_balance(user_id)
    lines = [
        "💰 <b>EARNINGS</b>",
        "━━━━━━━━━━━━━━━━",
        f"Total Earned:  <b>Rp{bal['total_earned']:,}</b>",
        f"Total Claimed: Rp{bal['total_claimed']:,}",
        f"Pending:       Rp{bal['pending_claims']:,}",
        "━━━━━━━━━━━━━━━━",
        f"Available:     <b>Rp{bal['available']:,}</b>",
        "",
    ]
    if bal["can_claim"]:
        lines.append("✅ /claim — Ajukan pencairan")
    else:
        lines.append(f"⏳ Minimal claim Rp{MIN_CLAIM_AMOUNT:,}")
    return "\n".join(lines)


def format_ledger(user_id: str) -> str:
    """Format transaction history as HTML for Telegram."""
    entries = get_ledger(user_id)
    if not entries:
        return "Belum ada transaksi."

    lines = ["📋 <b>TRANSACTION HISTORY</b>", "━━━━━━━━━━━━━━━━"]
    for e in entries:
        icon = "💰" if e["type"] == "earn" else "💳" if e["type"] == "claim" else "📌"
        amount_str = f"+Rp{e['amount']:,}" if e["type"] == "earn" else f"-Rp{e['amount']:,}"
        lines.append(f"{icon} {amount_str} — {e.get('description', e['source'])}")
    return "\n".join(lines)
