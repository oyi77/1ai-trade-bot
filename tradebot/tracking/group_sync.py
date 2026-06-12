#!/usr/bin/env python3
"""Premium group/channel sync for subscribers."""
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("vtfx-group-sync")
WIB = timezone(timedelta(hours=7))

BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
PREMIUM_GROUP_ID = os.environ.get("PREMIUM_GROUP_ID", "")
PREMIUM_CHANNEL_ID = os.environ.get("PREMIUM_CHANNEL_ID", "")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "members.db"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _init_table():
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS group_membership (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                tier TEXT DEFAULT '',
                chat_type TEXT DEFAULT '',
                chat_target_id TEXT DEFAULT '',
                action TEXT DEFAULT 'added',
                created_at TEXT DEFAULT ''
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_gm_chat_id
            ON group_membership(chat_id)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_gm_created_at
            ON group_membership(created_at)
        """)


def _tg_api(method: str, payload: dict) -> dict | None:
    """Call Telegram Bot API."""
    if not TELEGRAM_API:
        logger.warning("BOT_TOKEN not set — Telegram API unavailable")
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


def _create_invite_link(chat_id: str) -> str | None:
    """Create an invite link for a group/channel. Returns the link or None."""
    result = _tg_api("createChatInviteLink", {
        "chat_id": chat_id,
        "member_limit": 1,
        "creates_join_request": False,
    })
    if result and result.get("ok"):
        return result["result"].get("invite_link")
    return None


def _send_dm(chat_id: str, text: str) -> bool:
    """Send a DM to a user."""
    result = _tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    return result is not None and result.get("ok", False)


def _ban_unban(chat_target_id: str, user_id: str) -> bool:
    """Kick a user from a group/channel by banning then unbanning."""
    result = _tg_api("banChatMember", {
        "chat_id": chat_target_id,
        "user_id": int(user_id),
    })
    if not result or not result.get("ok"):
        logger.warning("banChatMember failed for %s in %s", user_id, chat_target_id)
        return False

    # Small delay then unban
    time.sleep(1)
    _tg_api("unbanChatMember", {
        "chat_id": chat_target_id,
        "user_id": int(user_id),
        "only_if_banned": True,
    })
    return True


def _log_membership(chat_id: str, tier: str, chat_type: str, chat_target_id: str, action: str):
    """Log group membership action."""
    try:
        now = datetime.now(WIB).isoformat()
        with _conn() as db:
            db.execute(
                "INSERT INTO group_membership (chat_id, tier, chat_type, chat_target_id, action, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(chat_id), tier, chat_type, str(chat_target_id), action, now),
            )
    except Exception as e:
        logger.warning("group_membership log failed: %s", e)


def add_to_premium_groups(chat_id: str, tier: str) -> dict[str, str | None]:
    """Add a subscriber to premium groups/channels.

    Creates invite links for premium group and channel, DMs the user,
    and logs the membership.
    """
    results: dict[str, str | None] = {"group": None, "channel": None}

    if PREMIUM_GROUP_ID:
        invite = _create_invite_link(PREMIUM_GROUP_ID)
        if invite:
            msg = (
                "👥 <b>Link Grup Premium</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                f"Klik untuk gabung grup {tier.upper()}:\n"
                f"{invite}\n\n"
                "<i>Link ini cuma bisa dipakai 1x ya!</i>"
            )
            _send_dm(chat_id, msg)
            _log_membership(chat_id, tier, "group", PREMIUM_GROUP_ID, "added")
            results["group"] = invite
            logger.info("Group invite sent to %s (%s)", chat_id, tier)
        else:
            logger.warning("Failed to create group invite for %s", chat_id)

    if PREMIUM_CHANNEL_ID:
        invite = _create_invite_link(PREMIUM_CHANNEL_ID)
        if invite:
            msg = (
                "📢 <b>Link Channel Premium</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                f"Klik untuk gabung channel {tier.upper()}:\n"
                f"{invite}\n\n"
                "<i>Link ini cuma bisa dipakai 1x ya!</i>"
            )
            _send_dm(chat_id, msg)
            _log_membership(chat_id, tier, "channel", PREMIUM_CHANNEL_ID, "added")
            results["channel"] = invite
            logger.info("Channel invite sent to %s (%s)", chat_id, tier)
        else:
            logger.warning("Failed to create channel invite for %s", chat_id)

    return results


def kick_from_premium_groups(chat_id: str) -> dict[str, bool]:
    """Kick a user from all premium groups/channels.

    Bans then unbans the user (effectively kicking them),
    and logs the removal.
    """
    results = {"group": False, "channel": False}
    uid = str(chat_id)

    if PREMIUM_GROUP_ID:
        ok = _ban_unban(PREMIUM_GROUP_ID, uid)
        if ok:
            _log_membership(chat_id, "", "group", PREMIUM_GROUP_ID, "kicked")
            logger.info("User %s kicked from group %s", chat_id, PREMIUM_GROUP_ID)
        results["group"] = ok

    if PREMIUM_CHANNEL_ID:
        ok = _ban_unban(PREMIUM_CHANNEL_ID, uid)
        if ok:
            _log_membership(chat_id, "", "channel", PREMIUM_CHANNEL_ID, "kicked")
            logger.info("User %s kicked from channel %s", chat_id, PREMIUM_CHANNEL_ID)
        results["channel"] = ok

    return results


def check_membership_expiry() -> list:
    """Check group_membership for users added > 30 days ago with tier in (pro, elite).

    Kicks users if no renewal found in subscriber_activity.
    Returns list of kicked user IDs.
    """
    cutoff = (datetime.now(WIB) - timedelta(days=30)).isoformat()
    kicked = []

    try:
        with _conn() as db:
            rows = db.execute(
                "SELECT DISTINCT chat_id, tier FROM group_membership "
                "WHERE action='added' AND created_at < ? AND tier IN ('pro', 'elite') "
                "ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()

        for row in rows:
            cid = row["chat_id"]
            tier = row["tier"]

            # Check for renewal in subscriber_activity
            renewal = db.execute(
                "SELECT id FROM subscriber_activity "
                "WHERE chat_id=? AND event='payment_success' AND tier IN (?, 'pro', 'elite') "
                "AND created_at > ? LIMIT 1",
                (cid, tier, cutoff),
            ).fetchone()

            if not renewal:
                logger.info("Membership expired for %s (%s) — kicking from groups", cid, tier)
                kick_from_premium_groups(cid)
                kicked.append(cid)

    except Exception as e:
        logger.error("check_membership_expiry failed: %s", e)

    return kicked


# Auto-initialize on import
_init_table()
