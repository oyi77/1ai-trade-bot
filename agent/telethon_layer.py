"""
Telethon Layer — async Telegram client with session persistence.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Handles:
- Client lifecycle (connect, reconnect with exponential backoff)
- Message routing to command/callback handlers
- Session persistence via .session file
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Coroutine

from telethon import TelegramClient, events
from telethon.tl.types import Message, UpdateShortMessage

LOG = logging.getLogger("agent.telethon")

API_ID = int(os.environ.get("TELEGRAM_API_ID", "23647272"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "1f69a4e0f03e5f51ddfa5b67ac7b5c49")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8343388239:AAFgeAkc9bvjywyCsHqRIa_RiJ6q-rp6uv0")
SESSION_PATH = Path(os.environ.get("AGENT_SESSION", "/home/openclaw/projects/paijo.session"))
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_USER_IDS", "157228659,5220170786").split(",") if x]


class TelethonLayer:
    """Production Telegram client with automatic reconnection."""

    def __init__(self):
        self.client: TelegramClient | None = None
        self._running = False
        self._cmd_router: dict[str, Callable] = {}
        self._callback_router: dict[str, Callable] = {}
        self._message_handler: Callable | None = None
        self._admins: set[int] = set(ADMIN_IDS)
        self._bot_username: str = ""

    def register_command(self, cmd: str, handler: Callable) -> None:
        self._cmd_router[cmd.lower()] = handler

    def register_callback_prefix(self, prefix: str, handler: Callable) -> None:
        self._callback_router[prefix] = handler

    def set_message_handler(self, handler: Callable) -> None:
        self._message_handler = handler

    async def start(self) -> None:
        self._running = True
        retries = 0

        while self._running:
            try:
                self.client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
                await self.client.start(bot_token=BOT_TOKEN)
                me = await self.client.get_me()
                self._bot_username = me.username or "agent_1ai2_bot"
                LOG.info("Connected as @%s (ID: %s)", me.username, me.id)

                @self.client.on(events.NewMessage)
                async def on_message(event):
                    await self._route_message(event)

                @self.client.on(events.CallbackQuery)
                async def on_callback(event):
                    await self._route_callback(event)

                retries = 0
                LOG.info("TelethonLayer listening...")
                await self.client.run_until_disconnected()

            except Exception as e:
                retries += 1
                wait = min(60, 2 ** retries)
                LOG.error("Connection error (retry %d): %s — waiting %ds", retries, e, wait)
                await asyncio.sleep(wait)

    async def stop(self) -> None:
        self._running = False
        if self.client:
            await self.client.disconnect()

    async def _route_message(self, event) -> None:
        try:
            sender = await event.get_sender()
            chat_id = event.chat_id if event.chat_id else (sender.id if sender else 0)
            text = (event.text or "").strip()

            if not text:
                return

            # Check for callback query data (inline button press)
            if event.message and event.message.reply_markup:
                pass

            # Route to command handler
            parts = text.split()
            cmd = parts[0].lower().lstrip("/").split("@")[0]

            if cmd in self._cmd_router:
                args = parts[1:]
                LOG.debug("Command: /%s from %s", cmd, chat_id)
                try:
                    response = await self._cmd_router[cmd](args, str(chat_id))
                    if response:
                        await self._send_message(str(chat_id), response)
                except Exception as e:
                    LOG.error("Command /%s error: %s", cmd, e)
                    await self._send_message(str(chat_id), f"❌ Error: {e}")
            elif self._message_handler:
                await self._message_handler(text, str(chat_id))

        except Exception as e:
            LOG.error("Route error: %s", e)

    async def _route_callback(self, event) -> None:
        try:
            data = (event.data or b"").decode()
            chat_id = str(event.chat_id or event.sender_id or "")
            LOG.debug("Callback: %s from %s", data, chat_id)

            await event.answer()

            for prefix, handler in self._callback_router.items():
                if data.startswith(prefix):
                    response = await handler(data, chat_id)
                    if response:
                        await self._send_message(chat_id, response)
                    return

            LOG.warning("No handler for callback: %s", data)
        except Exception as e:
            LOG.error("Callback route error: %s", e)

    async def _send_message(self, chat_id: str, text: str, reply_markup: Any = None) -> bool:
        if not self.client:
            return False
        try:
            MAX_LEN = 4000
            if len(text) > MAX_LEN:
                text = text[:MAX_LEN - 30] + "\n<i>... (dipotong)</i>"

            parse_mode = "html" if "<" in text and ">" in text else None
            await self.client.send_message(
                int(chat_id),
                text,
                parse_mode=parse_mode,
                buttons=reply_markup,
                link_preview=False,
            )
            return True
        except Exception as e:
            LOG.warning("Send failed to %s: %s", chat_id, e)
            return False

    async def send_message(self, chat_id: str, text: str, buttons: Any = None) -> bool:
        return await self._send_message(chat_id, text, buttons)

    @property
    def is_admin(self, user_id: str) -> bool:
        return int(user_id) in self._admins
