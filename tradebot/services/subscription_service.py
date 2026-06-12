"""
Subscription Service — unified subscription tier management.

Separates signal-only vs signal+execute tiers with different pricing.
Tracks subscriptions per user per platform with quota enforcement.

Storage: subscription_tiers table in tradebot.db.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from tradebot.storage.repository import get_repo

LOG = logging.getLogger("tradebot.services.subscription_service")

# ── Pricing Configuration ────────────────────────────────────────────

SIGNAL_ONLY_PLANS = {
    "weekly": {"price_idr": 50_000, "days": 7, "quota_per_day": 50},
    "monthly": {"price_idr": 100_000, "days": 30, "quota_per_day": 200},
    "lifetime": {"price_idr": 300_000, "days": 36500, "quota_per_day": 999},
}

SIGNAL_EXECUTE_PLANS = {
    "weekly": {"price_idr": 75_000, "days": 7, "quota_per_day": 999},
    "monthly": {"price_idr": 200_000, "days": 30, "quota_per_day": 999},
    "lifetime": {"price_idr": 750_000, "days": 36500, "quota_per_day": 999},
}

PLANS: dict[str, dict[str, dict[str, int]]] = {
    "signal_only": SIGNAL_ONLY_PLANS,
    "signal_execute": SIGNAL_EXECUTE_PLANS,
}

TIER_LABELS: dict[str, str] = {
    "signal_only": "Signal Only",
    "signal_execute": "Signal + Auto-Execute",
}


def _storage():
    return get_repo()


def get_plan_config(tier_type: str, plan: str) -> dict[str, int] | None:
    """Get config for a specific tier_type/plan combo."""
    tier_plans = PLANS.get(tier_type)
    if not tier_plans:
        return None
    return tier_plans.get(plan)


def create_subscription(
    user_id: str,
    tier_type: str,
    plan: str,
    platform: str = "",
) -> dict[str, Any]:
    """Create a new subscription for a user."""
    config = get_plan_config(tier_type, plan)
    if not config:
        return {"error": f"Invalid plan: {tier_type}/{plan}"}

    now = int(time.time())
    expires_at = now + (config["days"] * 86400)

    store = _storage()
    store.execute(
        """INSERT INTO subscription_tiers
           (user_id, platform, tier_type, plan, status,
            started_at, expires_at, auto_renew, created_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?, 0, ?)""",
        (user_id, platform, tier_type, plan, now, expires_at, now),
    )

    LOG.info(
        "Subscription: user=%s tier=%s plan=%s platform=%s expires=%d",
        user_id,
        tier_type,
        plan,
        platform,
        expires_at,
    )

    return {
        "success": True,
        "user_id": user_id,
        "tier_type": tier_type,
        "plan": plan,
        "platform": platform,
        "expires_at": expires_at,
        "quota_per_day": config["quota_per_day"],
    }


def check_subscription(
    user_id: str,
    platform: str = "",
) -> dict[str, Any]:
    """Check a user's active subscription for a platform."""
    store = _storage()
    now = int(time.time())

    row = store.fetchone(
        """SELECT tier_type, plan, platform, expires_at, auto_renew
           FROM subscription_tiers
           WHERE user_id=? AND status='active' AND expires_at>?
             AND (platform=? OR platform='')
           ORDER BY
             CASE WHEN platform=? THEN 0 ELSE 1 END,
             expires_at DESC
           LIMIT 1""",
        (user_id, now, platform, platform),
    )

    if not row:
        return {"active": False, "tier_type": "", "plan": "", "quota_per_day": 0}

    tier_type, plan_name, sub_platform, expires_at, auto_renew = row
    config = get_plan_config(tier_type, plan_name)
    quota = config["quota_per_day"] if config else 0
    remaining_secs = expires_at - now
    remaining_days = remaining_secs // 86400 if remaining_secs > 0 else 0

    return {
        "active": True,
        "tier_type": tier_type,
        "plan": plan_name,
        "platform": sub_platform,
        "expires_at": expires_at,
        "remaining_days": remaining_days,
        "quota_per_day": quota,
        "auto_renew": bool(auto_renew),
    }


def get_quota_remaining(user_id: str, platform: str = "") -> int:
    """Get remaining signal quota for today for a user."""
    sub = check_subscription(user_id, platform)
    if not sub["active"]:
        return 3  # Free tier

    quota = sub["quota_per_day"]
    if quota >= 999:
        return 999

    store = _storage()
    today_start = int(time.time()) - 86400
    row = store.fetchone(
        "SELECT COUNT(*) FROM trades WHERE user_id=? AND created_at >= datetime(?, 'unixepoch')",
        (user_id, today_start),
    )
    used = row[0] if row else 0
    return max(0, quota - used)


def use_quota(user_id: str, platform: str = "") -> bool:
    """Check and decrement quota. Returns True if allowed."""
    remaining = get_quota_remaining(user_id, platform)
    return remaining > 0


def expire_subscriptions() -> int:
    """Mark expired subscriptions as expired. Returns count expired."""
    store = _storage()
    now = int(time.time())
    store.execute(
        "UPDATE subscription_tiers SET status='expired' WHERE status='active' AND expires_at<?",
        (now,),
    )
    expired = store.conn().total_changes
    if expired:
        LOG.info("Expired %d subscriptions", expired)
    return expired


def get_eligible_subscribers(
    platform: str = "",
    tier_type: str = "",
) -> list[str]:
    """Get user IDs eligible to receive signals for a platform."""
    store = _storage()
    now = int(time.time())

    conditions = ["status='active'", "expires_at>?"]
    params: list[Any] = [now]

    if platform:
        conditions.append("(platform=? OR platform='')")
        params.append(platform)
    if tier_type:
        conditions.append("tier_type=?")
        params.append(tier_type)

    query = f"SELECT DISTINCT user_id FROM subscription_tiers WHERE {' AND '.join(conditions)}"
    rows = store.fetchall(query, tuple(params))
    return [str(r[0]) for r in rows]


def format_plan_list() -> str:
    """Format available plans as HTML for Telegram."""
    lines = [
        "PAKET SUBSCRIPTION",
        "━━━━━━━━━━━━━━━━",
        "",
        "SIGNAL ONLY — Sinyal Telegram",
        f"  Weekly:     Rp{PLANS['signal_only']['weekly']['price_idr']:,}",
        f"  Monthly:    Rp{PLANS['signal_only']['monthly']['price_idr']:,}",
        f"  Lifetime:   Rp{PLANS['signal_only']['lifetime']['price_idr']:,}",
        "",
        "SIGNAL + AUTO-EXECUTE — Sinyal + Trading Otomatis",
        f"  Weekly:     Rp{PLANS['signal_execute']['weekly']['price_idr']:,}",
        f"  Monthly:    Rp{PLANS['signal_execute']['monthly']['price_idr']:,}",
        f"  Lifetime:   Rp{PLANS['signal_execute']['lifetime']['price_idr']:,}",
        "",
        "Gunakan /subscribe untuk memilih paket.",
    ]
    return "\n".join(lines)
