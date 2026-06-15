#!/usr/bin/env python3
"""
Expiry Reminder Runner — Standalone script for cron.

Finds users whose free trial expires within 24 hours and sends
them Telegram DM reminders via the Vilona TradeFX bot.

Idempotent: tracks sent reminders in expiry_reminded.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger("expiry_reminder")
WIB = timezone(timedelta(hours=7))

# ── Paths (relative to project root) ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "vilona_tradefx" / "members.db"
SENT_FILE = DB_PATH.parent / "expiry_reminded.json"
REMINDER_WINDOW_H = 24


def load_reminded() -> set[str]:
    if SENT_FILE.exists():
        try:
            data = json.loads(SENT_FILE.read_text())
            if isinstance(data, list):
                return set(data)
        except Exception as exc:
            LOG.warning("Failed to load sent file: %s", exc)
    return set()


def save_reminded(reminded: set[str]) -> None:
    SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SENT_FILE.write_text(json.dumps(sorted(reminded)))
    LOG.info("Saved %d reminded chat IDs", len(reminded))


def find_expiring_users() -> list[dict]:
    """Return list of dicts with chat_id, name, username, tier, hours_left, expiry."""
    if not DB_PATH.exists():
        LOG.warning("No members.db at %s", DB_PATH)
        return []

    now = datetime.now(WIB)
    cutoff = now + timedelta(hours=REMINDER_WINDOW_H)
    reminded = load_reminded()
    results: list[dict] = []

    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT chat_id, nama, username, tier, expiry FROM members WHERE tier = 'starter'"
        )
        rows = c.fetchall()
        LOG.info("Found %d starter-tier members in DB", len(rows))

        for chat_id, nama, username, tier, expiry_str in rows:
            if not expiry_str:
                continue
            try:
                exp = datetime.fromisoformat(expiry_str)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=WIB)
            except ValueError:
                continue

            hours_left = (exp - now).total_seconds() / 3600
            if 0 < hours_left <= REMINDER_WINDOW_H and str(chat_id) not in reminded:
                results.append({
                    "chat_id": str(chat_id),
                    "name": nama or username or str(chat_id),
                    "username": username or "",
                    "tier": tier or "starter",
                    "hours_left": round(hours_left, 1),
                    "expiry": exp.isoformat(),
                })
    finally:
        conn.close()

    return results


def build_message(user: dict) -> str:
    jam = int(user["hours_left"])
    return (
        f"<b>Halo {user['name']}!</b>\n\n"
        f"Masa trial sinyal XAUUSD lu <b>habis dalam {jam} jam</b>\n\n"
        f"Jangan sampe ketinggalan sinyal selanjutnya bro!\n\n"
        f"/subscribe — lanjutkan akses\n\n"
        f"<i>Auto-reminder system. Balas /subscribe untuk lanjut.</i>"
    )


async def send_telegram(chat_id: str, message: str, token: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    import httpx

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if data.get("ok"):
                LOG.info("Sent reminder to %s (chat_id=%s)", chat_id, chat_id)
                return True
            else:
                LOG.warning(
                    "Failed to send to %s: %s (error_code=%s)",
                    chat_id,
                    data.get("description", "unknown"),
                    data.get("error_code", "?"),
                )
                return False
    except Exception as exc:
        LOG.error("Telegram API error for %s: %s", chat_id, exc)
        return False


async def main() -> int:
    LOG.info("=== Expiry Reminder Run ===")

    # Load bot token
    token = None
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not token:
        LOG.error("No Telegram bot token found in .env")
        return 1

    # Find expiring users
    users = find_expiring_users()
    if not users:
        LOG.info("No users expiring within %d hours — nothing to do.", REMINDER_WINDOW_H)
        return 0

    LOG.info("Found %d user(s) to remind", len(users))

    # Send messages
    sent: set[str] = set()
    for user in users:
        msg = build_message(user)
        ok = await send_telegram(user["chat_id"], msg, token)
        if ok:
            sent.add(user["chat_id"])

    # Persist sent
    if sent:
        existing = load_reminded()
        existing.update(sent)
        save_reminded(existing)

    LOG.info("=== Expiry Reminder Complete: %d/%d sent ===", len(sent), len(users))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
