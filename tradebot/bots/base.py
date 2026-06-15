"""BaseBot — abstract base for all Telegram trading bots.

Provides common patterns:
- send_message / send_signal via tradebot.services.telegram
- handle_command routing stub
- User tracking and rate-limit helpers
- start / stop lifecycle
"""

from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any

from tradebot.config import settings
from tradebot.services.telegram import TelegramService

LOG = logging.getLogger(__name__)


class BaseBot(abc.ABC):
    """Abstract base bot with common Telegram bot infrastructure.

    Subclasses override ``_register_commands``, ``_register_handlers``,
    and optionally ``_background_loop`` for proactive scanning/dispatch.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        name: str = "basebot",
    ) -> None:
        self.name = name
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self._telegram = TelegramService(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
        )
        self._running = False
        self._background_tasks: list[asyncio.Task[Any]] = []
        self._command_handlers: dict[str, Any] = {}
        self._user_last_interaction: dict[int, float] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the bot — registers handlers and kicks off background loop."""
        if self._running:
            LOG.warning("%s already running", self.name)
            return
        self._running = True
        self._register_commands()
        await self._on_start()
        LOG.info("%s started", self.name)

    async def stop(self) -> None:
        """Gracefully stop the bot and cancel background tasks."""
        self._running = False
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        await self._on_stop()
        LOG.info("%s stopped", self.name)

    async def _on_start(self) -> None:
        """Optional hook called after start()."""

    async def _on_stop(self) -> None:
        """Optional hook called during stop()."""

    @abc.abstractmethod
    def _register_commands(self) -> None:
        """Register Telegram command handlers. Called once during startup."""

    async def _background_loop(self) -> None:
        """Optional background loop for proactive scanning / dispatch.
        Subclasses should override and call _schedule_background(task)."""

    def _schedule_background(self, coro: Any) -> asyncio.Task[Any]:
        """Schedule a background coroutine and track it for cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        return task

    # ── Messaging ────────────────────────────────────────────────────────

    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Send a text message to a chat (or the default configured chat)."""
        chat = chat_id or self.chat_id
        if not chat:
            LOG.warning("No chat_id configured for %s", self.name)
            return False
        ok, _ = await self._telegram.send_message(text)
        return ok

    async def send_signal(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        price: float,
        chat_id: str | None = None,
    ) -> bool:
        """Send a formatted trading signal alert."""
        return await self._telegram.send_signal_alert(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            price=price,
        )

    # ── User management ──────────────────────────────────────────────────

    def _check_throttle(self, user_id: int, cooldown_sec: int = 5) -> bool:
        """Return True if the user is allowed to proceed (not throttled)."""
        now = self._now()
        last = self._user_last_interaction.get(user_id, 0.0)
        if now - last < cooldown_sec:
            return False
        self._user_last_interaction[user_id] = now
        return True

    @staticmethod
    def _now() -> float:
        import time
        return time.time()

    # ── Utilities ────────────────────────────────────────────────────────

    def handle_command(self, command: str, args: list[str]) -> Any:
        """Dispatch a command to its registered handler."""
        handler = self._command_handlers.get(command.lstrip("/"))
        if handler is None:
            LOG.debug("No handler for command %s", command)
            return None
        return handler(args)

    @property
    def is_running(self) -> bool:
        return self._running
