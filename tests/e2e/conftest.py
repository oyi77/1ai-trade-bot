"""Shared pytest fixtures for VilonaBot E2E tests.

Uses Telethon async client with event-based response capture.
Target bot: @agent_1ai2_bot (unified bot).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# Try to import telethon, skip tests if not available
try:
    from telethon import TelegramClient, events
    from telethon.tl.custom import Message
except ImportError:
    pytestmark = pytest.mark.skip(reason="telethon not installed: pip install telethon")
    TelegramClient = None  # type: ignore
    Message = None  # type: ignore
    events = None  # type: ignore


# ── Configuration from environment ─────────────────────────────────────────

API_ID = int(os.environ.get("TELEGRAM_API_ID", "23913448"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "78d168f985edf365a5cd9679a917a0b2")
BOT_USERNAME = os.environ.get("TELETHON_BOT_USERNAME", "agent_1ai2_bot")
SESSION_PATH = Path(
    os.environ.get("TELETHON_SESSION", Path.home() / ".telethon_session" / "alwayscuanbos.session")
)
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "5365607425")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "30"))
MAX_RESPONSE_TIME = float(os.environ.get("MAX_RESPONSE_TIME", "6.0"))

if not API_ID or not API_HASH:
    pytestmark = pytest.mark.skip(reason="Set TELEGRAM_API_ID and TELEGRAM_API_HASH env vars")


# ── Test Result Tracking ───────────────────────────────────────────────────


@dataclass
class CommandResult:
    """Result of a single command test."""

    command: str
    success: bool
    response_text: str | None = None
    response_time: float = 0.0
    error: str | None = None
    category: str = "core"
    has_buttons: bool = False
    buttons: list[list[dict[str, str]]] = field(default_factory=list)
    message: Message | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallbackResult:
    """Result of a callback button interaction."""

    callback_data: str
    success: bool
    response_text: str | None = None
    response_time: float = 0.0
    error: str | None = None
    category: str = "callback"
    has_buttons: bool = False
    buttons: list[list[dict[str, str]]] = field(default_factory=list)
    message: Message | None = None


# ── Rate Limiting Helper ──────────────────────────────────────────────────


class RateLimiter:
    """Enforce minimum delay between commands to avoid Telegram flood limits."""

    def __init__(self, min_delay: float = 2.0):
        self.min_delay = min_delay
        self._last_command_time = 0.0

    def wait(self) -> None:
        """Wait until minimum delay has elapsed since last command."""
        elapsed = time.time() - self._last_command_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_command_time = time.time()


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Return a rate limiter with 2s default delay."""
    return RateLimiter(min_delay=2.0)


@pytest_asyncio.fixture
async def telethon_client() -> Any:  # type: ignore[misc]
    """Create and connect a Telethon client using the test session."""
    session_str = str(SESSION_PATH).replace(".session", "")
    client = TelegramClient(session_str, API_ID, API_HASH)
    await client.start()

    if not await client.is_user_authorized():
        await client.disconnect()
        pytest.skip("Telethon session not authorized. Run: python scripts/setup_session.py")

    yield client
    await client.disconnect()


@pytest_asyncio.fixture
async def bot_entity(telethon_client: Any) -> Any:
    """Get the bot entity for the unified bot."""
    return await telethon_client.get_entity(BOT_USERNAME)


@pytest.fixture
def admin_user_id() -> str:
    """Return admin user ID for testing admin-only commands."""
    return ADMIN_CHAT_ID


# ── Test Helper Functions ──────────────────────────────────────────────────


def _extract_buttons(message: Message | None) -> list[list[dict[str, str]]]:
    """Extract inline keyboard buttons from a message."""
    if not message or not message.reply_markup:
        return []
    rows = message.reply_markup.rows if hasattr(message.reply_markup, "rows") else []
    buttons: list[list[dict[str, str]]] = []
    for row in rows:
        row_buttons: list[dict[str, str]] = []
        for button in row.buttons:
            info: dict[str, str] = {"text": button.text}
            if hasattr(button, "data"):
                info["callback_data"] = button.data.decode("utf-8", errors="ignore")
            if hasattr(button, "url") and button.url:
                info["url"] = button.url
            row_buttons.append(info)
        buttons.append(row_buttons)
    return buttons


def _find_button(message: Message, text_substring: str) -> tuple[Any, str] | tuple[None, None]:
    """Find a button containing text_substring in the message."""
    if not message.reply_markup:
        return None, None
    for row in message.reply_markup.rows:
        for button in row.buttons:
            if not hasattr(button, "data"):
                continue
            if text_substring.lower() in button.text.lower():
                return button, button.data.decode("utf-8", errors="ignore")
    return None, None


async def _wait_for_response(
    client: TelegramClient,
    bot: Any,
    trigger_action: Any,
    timeout: float = 10.0,
) -> Message | None:
    """Wait for a bot response after an action using events.

    Captures both new messages and edited messages because the bot often edits
    the inline menu in place.
    """
    bot_id = bot.id if hasattr(bot, "id") else int(bot)
    future: asyncio.Future[Message] = asyncio.get_event_loop().create_future()

    def _is_from_bot(msg: Message) -> bool:
        sender = getattr(msg, "sender_id", None)
        return sender == bot_id

    @client.on(events.NewMessage(from_users=[bot_id]))
    async def new_handler(event: events.NewMessage.Event) -> None:
        if not future.done() and _is_from_bot(event.message):
            future.set_result(event.message)

    @client.on(events.MessageEdited(from_users=[bot_id]))
    async def edit_handler(event: events.MessageEdited.Event) -> None:
        if not future.done() and _is_from_bot(event.message):
            future.set_result(event.message)

    try:
        start = time.time()
        await trigger_action()

        try:
            msg = await asyncio.wait_for(future, timeout=timeout)
            elapsed = time.time() - start
            setattr(msg, "_response_time_ms", elapsed * 1000)  # type: ignore
            return msg
        except TimeoutError:
            return None
    finally:
        client.remove_event_handler(new_handler)
        client.remove_event_handler(edit_handler)


async def send_command(
    client: TelegramClient,
    bot: Any,
    command: str,
    rate_limiter: RateLimiter | None = None,
    timeout: float = 10.0,
) -> CommandResult:
    """Send a command to the bot and capture the response."""
    if rate_limiter:
        rate_limiter.wait()

    start_time = time.time()

    try:
        msg = await _wait_for_response(
            client,
            bot,
            lambda: client.send_message(bot, command),
            timeout=timeout,
        )

        if msg is None:
            return CommandResult(
                command=command,
                success=False,
                error="Timeout waiting for bot response",
                response_time=time.time() - start_time,
            )

        text = (msg.message or "").strip()
        buttons = _extract_buttons(msg)

        return CommandResult(
            command=command,
            success=True,
            response_text=text,
            response_time=time.time() - start_time,
            has_buttons=len(buttons) > 0,
            buttons=buttons,
            message=msg,
            details={"message_id": msg.id},
        )

    except Exception as e:
        return CommandResult(
            command=command,
            success=False,
            error=str(e),
            response_time=time.time() - start_time,
        )


async def send_callback(
    client: TelegramClient,
    bot: Any,
    callback_data: str,
    rate_limiter: RateLimiter | None = None,
    timeout: float = 10.0,
) -> CallbackResult:
    """Click an inline button with the given callback data.

    The bot often edits the existing message instead of sending a new one.
    """
    if rate_limiter:
        rate_limiter.wait()

    start_time = time.time()

    try:
        messages = await client.get_messages(bot, limit=20)
        target_msg: Message | None = None
        for msg in messages:
            if not msg.reply_markup:
                continue
            for row in msg.reply_markup.rows:
                for button in row.buttons:
                    if not hasattr(button, "data"):
                        continue
                    data = button.data.decode("utf-8", errors="ignore")
                    if data == callback_data:
                        target_msg = msg
                        break
                if target_msg:
                    break
            if target_msg:
                break

        if target_msg is None:
            return CallbackResult(
                callback_data=callback_data,
                success=False,
                error=f"No message found with callback_data={callback_data}",
                response_time=time.time() - start_time,
            )

        msg = await _wait_for_response(
            client,
            bot,
            lambda: target_msg.click(data=callback_data),
            timeout=timeout,
        )

        if msg is None:
            return CallbackResult(
                callback_data=callback_data,
                success=False,
                error="Timeout waiting for callback response",
                response_time=time.time() - start_time,
            )

        text = (msg.message or "").strip()
        buttons = _extract_buttons(msg)

        return CallbackResult(
            callback_data=callback_data,
            success=True,
            response_text=text,
            response_time=time.time() - start_time,
            has_buttons=len(buttons) > 0,
            buttons=buttons,
            message=msg,
        )
    try:
        messages = await client.get_messages(bot, limit=20)
        target_msg: Message | None = None
        target_button = None
        # Prefer the newest message with an inline keyboard to avoid stale menus
        for msg in sorted(messages, key=lambda m: m.id or 0, reverse=True):
            if not msg.reply_markup:
                continue
            for row in msg.reply_markup.rows:
                for button in row.buttons:
                    if not hasattr(button, "data"):
                        continue
                    if text_substring.lower() in button.text.lower():
                        target_msg = msg
                        target_button = button
                        break
                if target_button:
                    break
            if target_button:
                break

    try:
        messages = await client.get_messages(bot, limit=20)
        target_msg: Message | None = None
        target_button = None
        for msg in messages:
            if not msg.reply_markup:
                continue
            for row in msg.reply_markup.rows:
                for button in row.buttons:
                    if not hasattr(button, "data"):
                        continue
                    if text_substring.lower() in button.text.lower():
                        target_msg = msg
                        target_button = button
                        break
                if target_button:
                    break
            if target_button:
                break

        if target_msg is None or target_button is None:
            return CallbackResult(
                callback_data=f"text:{text_substring}",
                success=False,
                error=f"No button containing text '{text_substring}' found",
                response_time=time.time() - start_time,
            )

        callback_data = target_button.data.decode("utf-8", errors="ignore")
        msg = await _wait_for_response(
            client,
            bot,
            lambda: target_msg.click(data=callback_data),
            timeout=timeout,
        )

        if msg is None:
            return CallbackResult(
                callback_data=callback_data,
                success=False,
                error="Timeout waiting for button click response",
                response_time=time.time() - start_time,
            )

        text = (msg.message or "").strip()
        buttons = _extract_buttons(msg)

        return CallbackResult(
            callback_data=callback_data,
            success=True,
            response_text=text,
            response_time=time.time() - start_time,
            has_buttons=len(buttons) > 0,
            buttons=buttons,
            message=msg,
        )

    except Exception as e:
        return CallbackResult(
            callback_data=f"text:{text_substring}",
            success=False,
            error=str(e),
            response_time=time.time() - start_time,
        )


async def wait_for_message_after(
    client: TelegramClient,
    bot: Any,
    after_id: int,
    timeout: float = 10.0,
) -> Message | None:
    """Wait for a new bot message after a specific message ID."""
    poll_interval = 0.3
    elapsed = 0.0
    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        messages = await client.get_messages(bot, limit=1)
        if messages and messages[0].id > after_id:
            return messages[0]
    return None


def assert_response_contains(
    result: CommandResult | CallbackResult, keywords: list[str]
) -> bool:
    """Assert that response contains all expected keywords (case-insensitive)."""
    if not result.response_text:
        return False
    text = result.response_text.lower()
    return all(k.lower() in text for k in keywords)


def assert_response_time(
    result: CommandResult | CallbackResult, max_seconds: float = MAX_RESPONSE_TIME
) -> bool:
    """Assert that response time is within acceptable limits."""
    return result.response_time <= max_seconds


def assert_has_buttons(result: CommandResult | CallbackResult) -> bool:
    """Assert that response has inline buttons."""
    return result.has_buttons and len(result.buttons) > 0


def get_button_by_text(
    result: CommandResult | CallbackResult, text_substring: str
) -> dict[str, str] | None:
    """Find a button whose text contains the given substring."""
    for row in result.buttons:
        for button in row:
            if text_substring.lower() in button.get("text", "").lower():
                return button
    return None


def get_button_by_callback(
    result: CommandResult | CallbackResult, callback_data: str
) -> dict[str, str] | None:
    """Find a button with the exact callback_data."""
    for row in result.buttons:
        for button in row:
            if button.get("callback_data") == callback_data:
                return button
    return None
