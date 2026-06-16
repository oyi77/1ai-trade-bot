"""Debug chained button clicks to understand message edits."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telethon import TelegramClient

API_ID = int(os.environ.get("TELEGRAM_API_ID", "23913448"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "78d168f985edf365a5cd9679a917a0b2")
BOT_USERNAME = os.environ.get("TELETHON_BOT_USERNAME", "agent_1ai2_bot")
SESSION_PATH = Path(
    os.environ.get("TELETHON_SESSION", Path.home() / ".telethon_session" / "alwayscuanbos.session")
)


async def main() -> None:
    session_str = str(SESSION_PATH).replace(".session", "")
    async with TelegramClient(session_str, API_ID, API_HASH) as client:
        bot = await client.get_entity(BOT_USERNAME)

        def dump_buttons(msg, label):
            print(f"\n=== {label} (id={msg.id}) ===")
            print(f"Text: {msg.message[:80].replace(chr(10), ' ')}")
            if not msg.reply_markup:
                print("  (no buttons)")
                return
            for r, row in enumerate(msg.reply_markup.rows):
                for c, button in enumerate(row.buttons):
                    data = getattr(button, "data", None)
                    data_str = data.decode("utf-8", errors="ignore") if data else ""
                    print(f"  [{r},{c}] '{button.text}' data='{data_str}'")

        msg = await client.send_message(bot, "/start")
        await asyncio.sleep(2)
        msg = (await client.get_messages(bot, limit=1))[0]
        dump_buttons(msg, "after /start")

        # click SIGNAL on this specific message
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if "SIGNAL" in btn.text:
                    await msg.click(data=btn.data)
                    break
        await asyncio.sleep(2)
        latest = (await client.get_messages(bot, limit=3))
        for m in latest:
            dump_buttons(m, f"latest after SIGNAL (id={m.id})")


if __name__ == "__main__":
    asyncio.run(main())
