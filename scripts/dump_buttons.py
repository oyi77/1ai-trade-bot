"""Dump current inline-keyboard labels from @agent_1ai2_bot for E2E alignment."""

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


async def get_latest_with_buttons(client, bot):
    messages = await client.get_messages(bot, limit=10)
    for msg in messages:
        if msg.reply_markup:
            return msg
    return None


async def click_latest_button(client, bot, text_substring):
    msg = await get_latest_with_buttons(client, bot)
    if not msg:
        return None
    for row in msg.reply_markup.rows:
        for btn in row.buttons:
            if not hasattr(btn, "data"):
                continue
            if text_substring.lower() in btn.text.lower():
                await msg.click(data=btn.data)
                return btn.text
    return None


async def dump(client, bot, label):
    await asyncio.sleep(2)
    msg = await get_latest_with_buttons(client, bot)
    print(f"\n=== {label} ===")
    if not msg:
        print("  (no message with buttons)")
        return
    print(f"Message id={msg.id}: {msg.message[:70].replace(chr(10), ' ')}...")
    for r, row in enumerate(msg.reply_markup.rows):
        for c, button in enumerate(row.buttons):
            text = button.text
            data = getattr(button, "data", None)
            data_str = data.decode("utf-8", errors="ignore") if data else ""
            print(f"  [{r},{c}] text='{text}' data='{data_str}'")


async def main() -> None:
    session_str = str(SESSION_PATH).replace(".session", "")
    async with TelegramClient(session_str, API_ID, API_HASH) as client:
        bot = await client.get_entity(BOT_USERNAME)

        await client.send_message(bot, "/start")
        await dump(client, bot, "/start")

        # Main menu paths and their sub-buttons to explore one level deeper
        paths = {
            "SIGNAL": ["Live Signal", "Whale", "Technical"],
            "MARKET": ["XAUUSD", "BTC", "ETH", "IDX", "Binary"],
            "HISTORY": ["Win Rate", "Recap", "History", "Stats"],
            "ACCOUNT": ["Status", "Subscribe", "My Key", "Trailing", "My ID", "Donate"],
            "WHITELABEL": ["Back"],
            "PANDUAN": ["Cara", "Signal", "Analisa"],
            "HELP": ["Commands", "Panduan", "EA", "Symbols", "Bridge"],
        }

        for menu_label, sub_labels in paths.items():
            # Return to main first
            await click_latest_button(client, bot, "HOME")
            await dump(client, bot, f"HOME (before {menu_label})")
            clicked = await click_latest_button(client, bot, menu_label)
            if not clicked:
                print(f"\n=== {menu_label}: NOT FOUND ===")
                continue
            await dump(client, bot, menu_label)
            for sub in sub_labels:
                await click_latest_button(client, bot, sub)
                await dump(client, bot, f"{menu_label} -> {sub}")
                # Return to parent menu
                await click_latest_button(client, bot, "BACK")
                await dump(client, bot, f"{menu_label} (back)")


if __name__ == "__main__":
    asyncio.run(main())
