"""
Expiry Notifier — proactive subscription expiry notifications.

Sends daily reminders starting from H-7 before subscription expires.
Uses Telegram bot to send personalized messages to users.

Integrated with subscription_service.py and VilonaBot.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from tradebot.storage.repository import get_repo

LOG = logging.getLogger("tradebot.services.expiry_notifier")

# Reminder schedule: send daily from H-7 to expiry
REMINDER_START_DAYS = 7

EXPIRY_FOMO_PHRASES = [
    "Jangan sampai sinyal premium kamu mati!",
    "Masih ada {days} hari — perpanjang sekarang biar trading lancar!",
    "Banyak member udah cuan, masa kamu mau ketinggalan?",
    "Upgrade sekarang, dapatkan akses ke 9 AI engines!",
    "Harga spesial untuk perpanjangan — cuma Rp{price:,}/bulan!",
    "Signal akurasi tinggi setiap hari — jangan sampai putus!",
    "Member aktif dapat bonus credit. Perpanjang sekarang!",
    "Koneksi EA kamu akan terputus jika subscription expired!",
    "Hanya {days} hari lagi! Segera perpanjang /subscribe",
    "Jangan sampai trading kamu berhenti. Perpanjang subscription!",
]


def _storage():
    return get_repo()


def get_expiring_subscriptions(days_remaining: int) -> list[dict[str, Any]]:
    """Get subscriptions that expire in exactly `days_remaining` days.

    Used by the daily notifier to find users who need reminders.

    Args:
        days_remaining: Number of days until expiry (1-7)

    Returns:
        List of subscription dicts with user_id, tier_type, plan, expires_at.
    """
    now = int(time.time())
    target_start = now + ((days_remaining - 1) * 86400)
    target_end = now + (days_remaining * 86400)

    rows = _storage().fetchall(
        """SELECT user_id, tier_type, plan, platform, expires_at
           FROM subscription_tiers
           WHERE status='active'
             AND expires_at BETWEEN ? AND ?
           ORDER BY expires_at""",
        (target_start, target_end),
    )
    return [dict(r) for r in rows]


def get_expired_today() -> list[dict[str, Any]]:
    """Get subscriptions that expired in the last 24 hours."""
    now = int(time.time())
    yesterday = now - 86400

    rows = _storage().fetchall(
        """SELECT user_id, tier_type, plan, platform, expires_at
           FROM subscription_tiers
           WHERE status='active'
             AND expires_at BETWEEN ? AND ?
           ORDER BY expires_at""",
        (yesterday, now),
    )
    return [dict(r) for r in rows]


def format_expiry_message(sub: dict[str, Any], days_remaining: int) -> str:
    """Format an expiry reminder message with FOMO.

    Args:
        sub: Subscription dict with user_id, tier_type, plan, expires_at
        days_remaining: Days until expiry (1-7, or 0 for expired today)

    Returns:
        HTML-formatted message string.
    """
    tier_label = "Signal Only" if sub.get("tier_type") == "signal_only" else "Signal+Execute"
    plan_label = sub.get("plan", "monthly")
    price = 50_000 if sub.get("tier_type") == "signal_only" else 75_000
    if plan_label == "monthly":
        price = 100_000 if sub.get("tier_type") == "signal_only" else 200_000
    elif plan_label == "lifetime":
        price = 300_000 if sub.get("tier_type") == "signal_only" else 750_000

    fomo = random.choice(EXPIRY_FOMO_PHRASES).format(days=days_remaining, price=price)

    if days_remaining <= 0:
        header = "⚠️ <b>SUBSCRIPTION EXPIRED</b>"
        body = (
            f"Subscription {tier_label} kamu sudah berakhir.\n"
            f"Semua fitur premium dan koneksi EA telah dinonaktifkan."
        )
    else:
        header = f"⏰ <b>SUBSCRIPTION REMINDER — H-{days_remaining}</b>"
        body = (
            f"Subscription {tier_label} kamu akan berakhir "
            f"dalam <b>{days_remaining} hari</b>."
        )

    return (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{body}\n\n"
        f"🔥 <i>{fomo}</i>\n\n"
        f"💳 Perpanjang sekarang: /subscribe"
    )


def get_daily_reminder_targets() -> list[tuple[int, dict[str, Any]]]:
    """Get all users who need reminders today.

    Returns:
        List of (days_remaining, subscription_dict) tuples.
    """
    targets: list[tuple[int, dict[str, Any]]] = []

    for days in range(1, REMINDER_START_DAYS + 1):
        subs = get_expiring_subscriptions(days)
        for sub in subs:
            targets.append((days, sub))

    # Also get expired today
    expired = get_expired_today()
    for sub in expired:
        targets.append((0, sub))

    return targets
