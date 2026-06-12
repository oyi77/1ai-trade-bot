#!/usr/bin/env python3
"""abandoned_cart_followup.py — Follow-up DM for unpaid invoices.

Runs every hour via cron (crontab: 0 * * * *).
- Queries payment_orders where status='pending' and age > 12 hours
- Sends ONE reminder DM per invoice (tracked via followup_sent)
- Never spams — sets followup_sent=1 after first DM
"""
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("abandoned-cart")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SENT_CACHE = DATA_DIR / "abandoned_cart_sent.json"


def _load_env():
    env_paths = [
        PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env",
        PROJECT_DIR / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

_load_env()

BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

# Spam guard: cache of already-reminded merchant_refs
def _load_sent() -> set:
    if SENT_CACHE.exists():
        try:
            return set(json.loads(SENT_CACHE.read_text()))
        except Exception:
            pass
    return set()


def _save_sent(s: set):
    SENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SENT_CACHE.write_text(json.dumps(list(s)))


def _tg_send(chat_id: str, text: str) -> bool:
    """Send a DM via Telegram Bot API."""
    if not TELEGRAM_API:
        return False
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"{TELEGRAM_API}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("ok", False)
    except Exception as e:
        logger.warning("DM to %s failed: %s", chat_id, e)
        return False


def get_abandoned_invoices() -> list[dict]:
    """Find payment_orders where:
    - status='pending'
    - created_at > 12 hours ago
    - followup_sent != '1' (not already reminded)
    Returns list of dicts.
    """
    try:
        from members import _conn
        cutoff = (datetime.now(WIB) - timedelta(hours=12)).isoformat()
        with _conn() as db:
            rows = db.execute("""
                SELECT id, merchant_ref, chat_id, amount, product_key, created_at
                FROM payment_orders
                WHERE status='pending'
                  AND created_at < ?
                  AND (followup_sent != '1' OR followup_sent IS NULL)
                ORDER BY created_at
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Query failed: %s", e)
        return []


def mark_followup_sent(order_id: int):
    """Set followup_sent='1' on a payment order."""
    try:
        from members import _conn
        with _conn() as db:
            db.execute(
                "UPDATE payment_orders SET followup_sent='1' WHERE id=?",
                (order_id,),
            )
    except Exception as e:
        logger.warning("Failed to mark followup for order %s: %s", order_id, e)


def main():
    logger.info("Scanning for abandoned invoices...")

    invoices = get_abandoned_invoices()
    if not invoices:
        logger.info("No abandoned invoices found")
        return

    sent_cache = _load_sent()
    count = 0

    for inv in invoices:
        merchant_ref = inv["merchant_ref"]
        chat_id = inv["chat_id"]
        tier_label = (inv.get("product_key") or "PRO").upper()

        # Spam guard — skip if already reminded via cache
        if merchant_ref in sent_cache:
            continue

        msg = (
            f"⏰ <b>Halo bro!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Tiket tier <b>{tier_label}</b> kamu masih menggantung\n"
            f"nih dan sebentar lagi kadaluarsa.\n\n"
            f"Ada kendala pas pembayaran QRIS/VA kemarin?\n"
            f"Kalau butuh bantuan, langsung reply pesan ini ya,\n"
            f"atau ketik <b>/subscribe</b> lagi untuk buat invoice baru! 🚀\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📞 Admin: @codergaboets"
        )

        if _tg_send(str(chat_id), msg):
            # Mark as reminded in DB + cache
            mark_followup_sent(inv["id"])
            sent_cache.add(merchant_ref)
            count += 1
            logger.info("Reminded: %s (%s)", chat_id, tier_label)
            time.sleep(1)  # Telegram rate limit: 30 msg/sec, 1 sec is safe

    _save_sent(sent_cache)
    logger.info("Abandoned cart follow-up: %d DMs sent", count)


if __name__ == "__main__":
    main()
