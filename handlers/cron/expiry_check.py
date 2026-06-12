#!/usr/bin/env python3
"""
Daily expiry checker — runs at 08:00 WIB.

- Queries members with status='paid' and expiry < NOW
- Kicks from premium groups via Telegram Bot API (ban_chat_member + unban)
- Logs activity
- Updates status to 'expired'
- Sends DM: 'Masa aktif {tier} kamu sudah habis. Upgrade lagi? /subscribe'
"""
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logger = logging.getLogger("vtfx-expiry-check")
WIB = timezone(timedelta(hours=7))

BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
PREMIUM_GROUP_ID = os.environ.get("PREMIUM_GROUP_ID", "") or os.environ.get("GROUP_CHAT_ID", "")
PREMIUM_CHANNEL_ID = os.environ.get("PREMIUM_CHANNEL_ID", os.environ.get("SIGNAL_CHANNEL_ID", ""))

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "members.db"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _tg_api(method: str, payload: dict) -> dict | None:
    """Call Telegram Bot API."""
    if not TELEGRAM_API:
        return None
    try:
        req = urllib.request.Request(
            f"{TELEGRAM_API}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error("Telegram API %s failed: %s", method, e)
        return None


def _kick_user(chat_target_id: str, user_id: str) -> bool:
    """Kick user from group/channel via ban+unban."""
    ban = _tg_api("banChatMember", {
        "chat_id": chat_target_id,
        "user_id": int(user_id),
    })
    if not ban or not ban.get("ok"):
        return False
    time.sleep(1)
    _tg_api("unbanChatMember", {
        "chat_id": chat_target_id,
        "user_id": int(user_id),
        "only_if_banned": True,
    })
    return True


def _send_dm(chat_id: str, text: str) -> bool:
    """Send a DM to a user."""
    result = _tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    })
    return result is not None and result.get("ok", False)


def _update_status(chat_id: str, status: str = "expired"):
    """Update member status AND reset tier to 'free' in DB."""
    try:
        with _conn() as db:
            db.execute(
                "UPDATE members SET status=?, tier='free' WHERE chat_id=?",
                (status, str(chat_id)),
            )
        logger.info("Downgraded %s: status=%s tier=free", chat_id, status)
    except Exception as e:
        logger.error("Failed to update status for %s: %s", chat_id, e)


def run_expiry_check() -> list[str]:
    """Run the daily expiry check. Returns list of expired chat IDs."""
    now = datetime.now(WIB)
    now_iso = now.isoformat()
    expired_users: list[str] = []

    try:
        with _conn() as db:
            rows = db.execute(
                "SELECT chat_id, tier, expiry FROM members WHERE status='paid' AND expiry < ?",
                (now_iso,),
            ).fetchall()

        logger.info("Expiry check: %d members with paid status and past expiry", len(rows))

        for row in rows:
            chat_id = row["chat_id"]
            tier = row.get("tier", "")

            logger.info("Processing expired member: %s (%s)", chat_id, tier)

            # Kick from premium group
            if PREMIUM_GROUP_ID:
                _kick_user(PREMIUM_GROUP_ID, chat_id)

            # Kick from premium channel
            if PREMIUM_CHANNEL_ID:
                _kick_user(PREMIUM_CHANNEL_ID, chat_id)

            # Log activity
            try:
                from tradebot.tracking.activity import log_activity
                log_activity(chat_id, chat_id, "", "subscription_expired", tier, {})
            except Exception as e:
                logger.warning("Activity log failed for %s: %s", chat_id, e)

            # Update status to expired
            _update_status(chat_id, "expired")

            # Send DM
            tier_display = tier.upper() if tier else "PREMIUM"
            dm = (
                f"⏰ <b>Masa Aktif Habis</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Masa aktif <b>{tier_display}</b> kamu sudah habis.\n\n"
                f"Fitur premium sudah dinonaktifkan.\\n"
                f"Jangan sampai ketinggalan sinyal AI!\n\n"
                f"🔥 Upgrade lagi?\n"
                f"👉 /subscribe\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📞 Admin: @codergaboets"
            )
            _send_dm(chat_id, dm)

            expired_users.append(chat_id)
            logger.info("Expired user processed: %s", chat_id)

    except Exception as e:
        logger.error("Expiry check failed: %s", e)

    return expired_users


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("Starting daily expiry check...")

    # Load .env
    env_path = Path(__file__).resolve().parent.parent.parent / "strategies" / "vilona_tradefx" / ".env"
    if env_path.exists():
        for line in env_path.read_text().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

    expired = run_expiry_check()
    logger.info("Expiry check complete: %d users expired", len(expired))
    for uid in expired:
        logger.info("  - %s", uid)


if __name__ == "__main__":
    main()
