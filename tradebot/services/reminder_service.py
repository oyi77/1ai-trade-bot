"""Bensin reminder service — automated donation reminders for stale subscribers.

Ported from scripts/bensin_reminder.py with full legacy fidelity.
Monday 08:00 WIB: checks donors > 30 days since last donation,
sends gentle DM reminder with days_since and last_amount.
Anti-spam: 1x per week per user.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger("tradebot.services.reminder")

WIB = timezone(timedelta(hours=7))
DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
)
STATE_PATH = DATA_DIR / "reminder_state.json"


def wib_now() -> datetime:
    return datetime.now(WIB)


def _load_state() -> dict[str, str | float]:
    """Load reminder state: last_sent_week (YYYY-WW) + per-user sent dates."""
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"last_week": "", "sent_users": {}}


def _save_state(state: dict) -> None:
    """Persist reminder state atomically."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_PATH)
    except OSError as e:
        LOG.warning("Failed to save reminder state: %s", e)


def get_stale_donors(min_days: int = 30) -> list[dict]:
    """Get donors whose last donation was > min_days ago.

    Returns list of dicts: {chat_id, username, last_donation, days_since, last_amount}.
    """
    try:
        from tradebot.services.members_service import get_stale_donors

        return get_stale_donors(min_days=min_days)
    except Exception as e:
        LOG.warning("Failed to get stale subscribers: %s", e)
        return []


def build_reminder(donor: dict) -> str:
    """Build gentle bensin reminder message for a stale subscriber."""
    days_since = donor.get("days_since", 0)
    last_amount = donor.get("last_amount", 0)
    username = donor.get("username", "Sobat")

    if days_since >= 60:
        urgency = "🔴 <b>Kritis!</b>"
    elif days_since >= 45:
        urgency = "🟡 <b>Hampir habis...</b>"
    else:
        urgency = "🟢 <b>Isi ulang yuk?</b>"

    amount_str = f"Rp{last_amount:,}" if last_amount else "? (data gak ada)"
    days_str = f"{days_since} hari" if days_since > 0 else "lama banget"

    return (
        f"⛽ <b>BENSIN REMINDER</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Halo {username}! 👋\n\n"
        f"{urgency}\n"
        f"Terakhir subscription: {days_str} lalu\n"
        f"Nominal terakhir: {amount_str}\n\n"
        f"Server AI butuh bahan bakar biar tetap jalan:\n"
        f"🔥 Analisa real-time\n"
        f"🤖 AI DeepSeek + GPT-4o\n"
        f"📰 Grok News dari X/Twitter\n\n"
        f"Yuk isi ulang biar bot tetap aktif:\n"
        f"💚 /subscribe — Mulai dari Rp50.000\n\n"
        f"Makasih udah dukung server AI! 🙏"
    )


async def send_bensin_reminders(bot_token: str) -> int:
    """Send bensin reminders to stale subscribers. Returns count sent.

    Args:
        bot_token: Telegram Bot API token.

    Anti-spam: only sends once per week (ISO week number).
    Skips users who were already messaged this week.
    """
    now = wib_now()
    current_week = now.strftime("%Y-W%V")

    # Weekend check
    if now.weekday() != 0:  # Monday only
        LOG.debug("Bensin reminder skipped: not Monday (today=%d)", now.weekday())
        return 0

    # Hour check: only 08:00-09:00 WIB
    if now.hour != 8:
        LOG.debug("Bensin reminder skipped: not 08:00 WIB (hour=%d)", now.hour)
        return 0

    state = _load_state()
    if state.get("last_week") == current_week:
        LOG.info("Bensin reminder already sent this week (%s)", current_week)
        return 0

    stale_donors = get_stale_donors(min_days=30)
    if not stale_donors:
        LOG.info("No stale subscribers found for reminder")
        state["last_week"] = current_week
        _save_state(state)
        return 0

    import urllib.request as ureq

    sent_count = 0
    already_sent = set(state.get("sent_users", {}).get(current_week, []))

    for subscriber in stale_donors:
        chat_id = donor.get("chat_id", "")
        if not chat_id or str(chat_id) in already_sent:
            continue

        reminder = build_reminder(donor)
        payload = json.dumps({
            "chat_id": str(chat_id),
            "text": reminder,
            "parse_mode": "HTML",
        }).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        try:
            req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
            ureq.urlopen(req, timeout=10)
            LOG.info("Bensin reminder sent to user %s (days_since=%d)", chat_id, donor.get("days_since", 0))
            sent_count += 1
            already_sent.add(str(chat_id))
        except Exception as e:
            LOG.warning("Failed to send reminder to %s: %s", chat_id, e)

        # Throttle: 1 second between DMs
        import asyncio
        await asyncio.sleep(1)

    state["last_week"] = current_week
    state["sent_users"] = {current_week: list(already_sent)}
    _save_state(state)

    LOG.info("Bensin reminder: sent %d / %d stale subscribers", sent_count, len(stale_donors))
    return sent_count
