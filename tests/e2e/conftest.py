"""Shared pytest fixtures for VilonaBot E2E tests.

Uses Telethon async client for testing real bot behavior.
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
    from telethon import TelegramClient
    from telethon.tl.custom import Message
except ImportError:
    pytestmark = pytest.mark.skip(reason="telethon not installed: pip install telethon")
    TelegramClient = None  # type: ignore
    Message = None  # type: ignore


# ── Configuration from environment ─────────────────────────────────────────

API_ID = int(os.environ.get("TELEGRAM_API_ID", "23647272"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "1f69a4e0f03e5f51ddfa5b67ac7b5c49")
BOT_USERNAME = os.environ.get("TELETHON_BOT_USERNAME", "agent_1ai2_bot")
SESSION_PATH = Path(
    os.environ.get("TELETHON_SESSION", Path.home() / ".telethon_session" / "vilona_session_fixed.session")
)
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "5365607425")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "30"))
MAX_RESPONSE_TIME = float(os.environ.get("MAX_RESPONSE_TIME", "5.0"))

# Skip all tests if credentials not set
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
    """Create and connect a Telethon client using the test session.

    Uses async TelegramClient (not telethon.sync) for pytest-asyncio compatibility.
    """
    session_str = str(SESSION_PATH).replace(".session", "")
    client = TelegramClient(session_str, API_ID, API_HASH)
    await client.start()

    # Verify authorization
    if not await client.is_user_authorized():
        await client.disconnect()
        pytest.skip(
            "Telethon session not authorized. Run: python scripts/setup_telethon_session.py"
        )

    yield client

    # Cleanup
    await client.disconnect()


@pytest_asyncio.fixture
async def bot_entity(telethon_client: Any) -> Any:
    """Get the bot entity for VilonaBot."""
    return await telethon_client.get_entity(BOT_USERNAME)


@pytest.fixture
def admin_user_id() -> str:
    """Return admin user ID for testing admin-only commands."""
    return ADMIN_CHAT_ID


# ── Test Helper Functions ──────────────────────────────────────────────────


async def send_command(
    client: TelegramClient,
    bot: Any,
    command: str,
    rate_limiter: RateLimiter | None = None,
) -> CommandResult:
    """Send a command to the bot and return the result.

    Args:
        client: Telethon client
        bot: Bot entity
        command: Command string (e.g., "/start")
        rate_limiter: Optional rate limiter

    Returns:
        CommandResult with success, response, timing
    """
    if rate_limiter:
        rate_limiter.wait()

    start_time = time.time()

    try:
        # Get current last message ID before sending
        last_msg = await client.get_messages(bot, limit=1)
        last_msg_id = last_msg[0].id if last_msg else 0

        # Send command
        await client.send_message(bot, command)

        # Wait for bot's response
        # When we send a message, it gets ID: last_msg_id + 1
        # Bot's response gets ID: last_msg_id + 2 (or higher if multiple messages)
        # We need to skip our own message and get the bot's response
        max_wait = 5.0  # Maximum wait time in seconds
        poll_interval = 0.3  # Poll every 300ms
        elapsed = 0.0

        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            # Get latest message
            messages = await client.get_messages(bot, limit=1)
            if messages and messages[0].id > last_msg_id:
                # Found a new message
                response = messages[0]
                response_text = response.message.strip()

                # Check if this is a bot response (not just our command echo)
                # Bot responses should be different from the command we sent
                # Command: "/start" → Response should NOT be just "/start"
                command_normalized = command.strip().lower()
                response_normalized = response_text.lower()

                # Accept if response is NOT just the command itself
                # This filters out our own message echo
                if response_normalized != command_normalized:
                    # This is a real bot response
                    response_time = time.time() - start_time

                    return CommandResult(
                        command=command,
                        success=True,
                        response_text=response_text,
                        response_time=response_time,
                        details={"message_id": response.id},
                    )

        # Timeout - no valid bot response received
        return CommandResult(
            command=command,
            success=False,
            error="Timeout waiting for bot response",
            response_time=time.time() - start_time,
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
) -> CallbackResult:
    """Send a callback query to the bot and return the result.

    This requires finding a message with inline buttons first.
    """
    if rate_limiter:
        rate_limiter.wait()

    start_time = time.time()

    try:
        # Get recent messages to find inline buttons
        messages = await client.get_messages(bot, limit=5)

        if not messages:
            return CallbackResult(
                callback_data=callback_data,
                success=False,
                error="No messages with inline buttons found",
            )

        # Find message with inline buttons
        for msg in messages:
            if msg.buttons:
                # Click the button matching callback_data
                for row in msg.buttons:
                    for button in row:
                        if hasattr(button, "data") and button.data == callback_data.encode():
                            start_time = time.time()
                            await client.click(msg, data=callback_data)
                            await asyncio.sleep(1)

                            response_msgs = await client.get_messages(bot, limit=1)
                            response_time = time.time() - start_time

                            if response_msgs:
                                return CallbackResult(
                                    callback_data=callback_data,
                                    success=True,
                                    response_text=response_msgs[0].message,
                                    response_time=response_time,
                                )

        return CallbackResult(
            callback_data=callback_data,
            success=False,
            error="Callback button not found",
            response_time=time.time() - start_time,
        )

    except Exception as e:
        return CallbackResult(
            callback_data=callback_data,
            success=False,
            error=str(e),
            response_time=time.time() - start_time,
        )


def assert_response_contains(result: CommandResult, keywords: list[str]) -> bool:
    """Assert that response contains all expected keywords."""
    if not result.response_text:
        return False

    text = result.response_text.lower()
    return all(keyword.lower() in text for keyword in keywords)


def assert_response_time(result: CommandResult, max_seconds: float = MAX_RESPONSE_TIME) -> bool:
    """Assert that response time is within acceptable limits."""
    return result.response_time <= max_seconds