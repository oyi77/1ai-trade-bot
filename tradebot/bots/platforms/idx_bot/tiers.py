"""
IDX Bot Tier Gating System.

Enforces feature access based on user subscription tier:
    - free: basic fundamental analysis
    - pro: bandar score, peer comparison, screener
    - premium: anomaly detection, backtest, sector scanner

Usage::

    from tradebot.bots.platforms.idx_bot.tiers import (
        TierGate, SUBSCRIPTION_TIERS, check_tier,
    )

    gate = TierGate()
    if gate.can_access("pro", "bandar_score"):
        result = await bandar_engine.analyze(symbol)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    PREMIUM = "premium"
    LIFETIME = "lifetime"


# Feature → minimum tier required
FEATURE_TIERS: dict[str, Tier] = {
    # Free features
    "price": Tier.FREE,
    "name": Tier.FREE,
    "sector": Tier.FREE,
    "fundamental_basic": Tier.FREE,
    "fundamental_score": Tier.FREE,
    "help": Tier.FREE,
    "start": Tier.FREE,
    "pricing": Tier.FREE,
    # Pro features
    "peer_comparison": Tier.PRO,
    "bandar_score": Tier.PRO,
    "sector_average": Tier.PRO,
    "screener_basic": Tier.PRO,
    "watchlist": Tier.PRO,
    # Premium features
    "anomaly_detection": Tier.PREMIUM,
    "backtest": Tier.PREMIUM,
    "sector_scanner": Tier.PREMIUM,
    "alert_real_time": Tier.PREMIUM,
    "ai_priority": Tier.PREMIUM,
    "auto_report": Tier.PREMIUM,
}

# Tier display info
TIER_INFO: dict[str, dict[str, str]] = {
    "free": {
        "name": "Free",
        "emoji": "🆓",
        "price": "Rp0",
        "color": "#888888",
    },
    "pro": {
        "name": "Pro",
        "emoji": "💎",
        "price": "Rp49k/bln",
        "color": "#4FC3F7",
    },
    "premium": {
        "name": "Premium",
        "emoji": "👑",
        "price": "Rp149k/bln",
        "color": "#FFD700",
    },
    "lifetime": {
        "name": "Lifetime",
        "emoji": "🌟",
        "price": "Rp1.999k",
        "color": "#FF6B35",
    },
}

# Upgrade CTA templates
UPGRADE_CTA: dict[str, str] = {
    "pro": (
        "🔒 <b>Fitur Pro</b>\n"
        "Upgrade ke 💎 <b>Pro (Rp49k/bln)</b> untuk akses:\n"
        "• 🐳 Bandar Accumulation Score\n"
        "• 👥 Peer comparison dalam 1 sektor\n"
        "• 📈 Sector average comparison\n"
        "• 🔍 Screener 958 saham IDX\n\n"
        "👉 /pricing untuk lihat semua paket"
    ),
    "premium": (
        "🔒 <b>Fitur Premium</b>\n"
        "Upgrade ke 👑 <b>Premium (Rp149k/bln)</b> untuk akses:\n"
        "• 🚨 Anomaly detection real-time\n"
        "• 📊 Backtest akurasi sinyal 3 tahun\n"
        "• 🔍 Sector scanner (deteksi saham anomali per sektor)\n"
        "• ⚡ AI priority response\n"
        "• 📋 Auto-report harian watchlist\n\n"
        "👉 /pricing untuk lihat semua paket"
    ),
}


@dataclass
class TierCheck:
    allowed: bool
    user_tier: str
    required_tier: str
    feature: str
    message: str = ""


class TierGate:
    """Check feature access based on user tier."""

    @staticmethod
    def can_access(user_tier: str, feature: str) -> bool:
        required = FEATURE_TIERS.get(feature, Tier.FREE)
        tier_order = {Tier.FREE: 0, Tier.PRO: 1, Tier.PREMIUM: 2, Tier.LIFETIME: 3}
        user_level = tier_order.get(Tier(user_tier), 0)
        required_level = tier_order.get(required, 0)
        return user_level >= required_level

    @staticmethod
    def check(user_tier: str, feature: str) -> TierCheck:
        required = FEATURE_TIERS.get(feature, Tier.FREE)
        allowed = TierGate.can_access(user_tier, feature)

        if allowed:
            return TierCheck(
                allowed=True,
                user_tier=user_tier,
                required_tier=required.value,
                feature=feature,
            )

        # Build upgrade message
        if required == Tier.PRO:
            msg = UPGRADE_CTA["pro"]
        else:
            msg = UPGRADE_CTA["premium"]

        return TierCheck(
            allowed=False,
            user_tier=user_tier,
            required_tier=required.value,
            feature=feature,
            message=msg,
        )

    @staticmethod
    def get_upgrade_target(user_tier: str) -> str:
        """Return the next tier to upgrade to."""
        if user_tier in ("free", Tier.FREE.value):
            return "pro"
        if user_tier in ("pro", Tier.PRO.value):
            return "premium"
        return ""


def format_tier_badge(tier: str) -> str:
    """Format tier badge emoji."""
    badges = {"free": "🆓", "pro": "💎", "premium": "👑", "lifetime": "🌟"}
    return badges.get(tier, "🆓")


def format_locked_feature(feature_name: str, required_tier: str) -> str:
    """Format a locked feature with upgrade prompt."""
    info = TIER_INFO.get(required_tier, TIER_INFO["pro"])
    return (
        f"🔒 <b>{feature_name}</b> — {info['emoji']} {info['name']} "
        f"({info['price']})\n"
        f"<i>Kirim /pricing untuk upgrade</i>"
    )
