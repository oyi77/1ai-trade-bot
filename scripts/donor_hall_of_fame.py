#!/usr/bin/env python3
"""
Weekly Subscriber Hall of Fame — posted every Monday 09:00 WIB.
Queries payment_orders for last 7 days paid entries, ranks top 3 by amount,
and posts to the Telegram channel.
"""
import os
import sys
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hall_of_fame")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR.parent / "data" / "vilona_tradefx"
DB_PATH = DATA_DIR / "members.db"
STATE_FILE = DATA_DIR / ".last_hall_of_fame"

# ── hardcoded channel per task spec ──
SIGNAL_CHANNEL = "-1001960875019"


def _current_week() -> str:
    """Return ISO year-week string, e.g. '2026-W24'."""
    return datetime.now(WIB).strftime("%G-W%V")


def _already_sent(week: str) -> bool:
    try:
        return STATE_FILE.read_text().strip() == week
    except Exception:
        return False


def _mark_sent(week: str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(week)


def parse_paid_at(paid_at: str) -> datetime | None:
    """Parse various paid_at formats into an aware datetime (WIB)."""
    if not paid_at:
        return None
    # Try ISO format with timezone: 2026-06-10T12:33:02.323255+07:00
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(paid_at, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=WIB)
            return dt
        except ValueError:
            continue
    log.warning("Could not parse paid_at: %s", paid_at)
    return None


def get_weekly_donors():
    """Query paid payment_orders from the last 7 days, return ranked list."""
    now = datetime.now(WIB)
    cutoff = now - timedelta(days=7)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT p.chat_id, p.amount, p.paid_at, m.nama
        FROM payment_orders p
        LEFT JOIN members m ON p.chat_id = m.chat_id
        WHERE p.status = 'paid'
        ORDER BY p.paid_at DESC
        """
    ).fetchall()
    conn.close()

    # Filter by last 7 days (parse paid_at)
    weekly = []
    for r in rows:
        paid_dt = parse_paid_at(r["paid_at"])
        if paid_dt and paid_dt >= cutoff:
            name = r["nama"] or f"User-{r['chat_id']}"
            weekly.append({"chat_id": r["chat_id"], "amount": r["amount"], "name": name})

    if not weekly:
        return []

    # Sort by amount descending
    weekly.sort(key=lambda x: x["amount"], reverse=True)
    return weekly


def build_message(donors: list) -> str | None:
    """Build the Subscriber Hall of Fame message from ranked subscriber list."""
    if not donors:
        return None

    top1_name = donors[0]["name"]
    top1_amt = donors[0]["amount"]

    top2_name = donors[1]["name"] if len(donors) > 1 else ""
    top2_amt = donors[1]["amount"] if len(donors) > 1 else 0

    top3_name = donors[2]["name"] if len(donors) > 2 else ""
    top3_amt = donors[2]["amount"] if len(donors) > 2 else 0

    remaining = len(donors) - 3
    if remaining < 0:
        remaining = 0

    parts = [
        f"🏆 DONOR HALL OF FAME — Minggu Ini",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🥇 {top1_name} — Rp{top1_amt:,}",
    ]
    if top2_name:
        parts.append(f"🥈 {top2_name} — Rp{top2_amt:,}")
    if top3_name:
        parts.append(f"🥉 {top3_name} — Rp{top3_amt:,}")

    if remaining > 0:
        parts.append(f"💚 +{remaining} subscriber lain")

    parts.extend([
        f"",
        f"⚡ Server AI kita jalan karena lo semua.",
        f"Belum isi? /donate",
    ])

    return "\n".join(parts)


def send_to_channel(text: str) -> bool:
    """Send to channel via Telethon."""
    api_id = int(os.environ.get("TG_API_ID", "0"))
    api_hash = os.environ.get("TG_API_HASH", "")
    channel = os.environ.get(
        "SIGNAL_CHANNEL_ID",
        os.environ.get("VILONA_TRADEFX_CHAT_ID", SIGNAL_CHANNEL),
    )

    if not api_id or not api_hash or not channel:
        log.error("Telethon credentials or channel ID not set")
        return False

    async def _send():
        from telethon import TelegramClient

        client = TelegramClient(
            str(PROJECT_DIR.parent / "data" / "premarket_session"),
            api_id,
            api_hash,
        )
        await client.start()
        await client.send_message(int(channel), text, parse_mode="html")
        await client.disconnect()

    try:
        asyncio.run(_send())
        log.info("✅ Subscriber Hall of Fame posted to channel")
        return True
    except Exception as e:
        log.error("Channel post failed: %s", e)
        return False


def main():
    week = _current_week()

    # Anti-spam: skip if already sent this week
    if _already_sent(week):
        log.info("Hall of Fame already sent for week %s — skip", week)
        return

    donors = get_weekly_donors()

    # Only run if at least 1 subscriber this week
    if not donors:
        log.info("No donors this week — skipping Hall of Fame")
        return

    message = build_message(donors)
    if not message:
        log.warning("Failed to build message — skipping")
        return

    log.info("Top donor: %s Rp%d", donors[0]["name"], donors[0]["amount"])
    log.info("Total donors this week: %d", len(donors))

    if send_to_channel(message):
        _mark_sent(week)


if __name__ == "__main__":
    main()
