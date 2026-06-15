#!/usr/bin/env python3
"""Launch VilonaBot — unified Telegram bot with full polling loop.

Replaces: scripts/vilona_tradefx_handler.py (legacy, 5844 LOC)
Uses: tradebot/bots/platforms/vilona/VilonaBot (unified, mixin-based)
"""

import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

import dotenv

dotenv.load_dotenv(Path(__file__).resolve().parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent / "data"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).resolve().parent / "logs" / "vilona_bot.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
LOG = logging.getLogger("vilona-bot-launcher")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OFFSET_FILE = Path(__file__).resolve().parent / "data" / "offset.txt"
BOT_LOCK = "/tmp/vilona_bot.lock"


def acquire_bot_lock() -> bool:
    """Acquire PID lock to prevent duplicate bot processes. Returns True if lock acquired."""
    if os.path.exists(BOT_LOCK):
        try:
            with open(BOT_LOCK) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            LOG.warning("⚠️ Bot already running (PID %s) — EXITING", old_pid)
            return False
        except (OSError, ValueError):
            os.remove(BOT_LOCK)
    with open(BOT_LOCK, "w") as f:
        f.write(str(os.getpid()))
    return True


def load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def main():
    LOG.info("VilonaBot launcher starting...")

    if not acquire_bot_lock():
        LOG.critical("Duplicate bot detected — exiting to prevent double-polling")
        sys.exit(1)

    from tradebot.bots.platforms.vilona import VilonaBot

    bot = VilonaBot(name="vilona-tradefx")
    bot.bot_token = BOT_TOKEN

    import asyncio

    async def run():
        await bot.start()
        LOG.info("VilonaBot started")

        offset = load_offset()
        poll_errors = 0

        while bot._running:
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
                payload = {"offset": offset, "timeout": 10}

                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())

                for update in data.get("result", []):
                    new_offset = update["update_id"] + 1
                    if new_offset > offset:
                        offset = new_offset
                    try:
                        await bot.handle_update(update)
                    except Exception as e:
                        LOG.error("Update error: %s", e)

                if data.get("result"):
                    save_offset(offset)
                    poll_errors = 0
                else:
                    await asyncio.sleep(0.5)

            except Exception as e:
                poll_errors += 1
                wait = min(60, poll_errors * 10)
                LOG.error("Poll error #%d: %s (wait %ds)", poll_errors, e, wait)
                await asyncio.sleep(wait)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOG.info("Shutdown requested")
    except Exception as e:
        LOG.critical("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
