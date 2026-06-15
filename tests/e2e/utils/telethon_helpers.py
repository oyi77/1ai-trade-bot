"""Telethon test utilities for E2E testing.

Provides helpers for creating clients, waiting for responses, and parsing bot output.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# Try to import telethon
try:
    from telethon.sync import TelegramClient
    from telethon.tl.custom import Message
    from telethon.tl.types import User
except ImportError:
    TelegramClient = None  # type: ignore
    Message = None  # type: ignore
    User = None  # type: ignore


def create_test_client(
    session_path: Path | str,
    api_id: int,
    api_hash: str,
) -> TelegramClient:
    """Create and connect a Telethon client for testing.

    Args:
        session_path: Path to .session file (without .session extension)
        api_id: Telegram API ID
        api_hash: Telegram API hash

    Returns:
        Connected TelegramClient instance
    """
    if TelegramClient is None:
        raise ImportError("telethon not installed: pip install telethon")

    session_str = str(session_path).replace(".session", "")
    client = TelegramClient(session_str, api_id, api_hash)
    client.connect()

    if not client.is_user_authorized():
        raise PermissionError(
            "Telethon session not authorized. Run: python scripts/setup_telethon_session.py"
        )

    return client


def wait_for_message(
    client: TelegramClient,
    bot: Any,
    timeout: float = 5.0,
    poll_interval: float = 0.5,
) -> Message | None:
    """Wait for a new message from the bot.

    Args:
        client: Telethon client
        bot: Bot entity
        timeout: Maximum wait time in seconds
        poll_interval: Time between polls

    Returns:
        Latest message from bot, or None if timeout
    """
    start_time = time.time()
    last_msg_time = 0.0

    while time.time() - start_time < timeout:
        messages = client.get_messages(bot, limit=1)
        if messages:
            msg = messages[0]
            if msg.date.timestamp() > last_msg_time:
                return msg
        time.sleep(poll_interval)

    return None


def extract_inline_buttons(message: Message) -> list[dict[str, str]]:
    """Extract inline keyboard buttons from a message.

    Args:
        message: Telethon Message object

    Returns:
        List of button dicts with 'text' and 'data' keys
    """
    if not message.buttons:
        return []

    buttons = []
    for row in message.buttons:
        for button in row:
            if hasattr(button, "text") and hasattr(button, "data"):
                buttons.append(
                    {
                        "text": button.text,
                        "data": button.data.decode()
                        if isinstance(button.data, bytes)
                        else str(button.data),
                    }
                )

    return buttons


def find_button_by_text(
    message: Message,
    text: str,
) -> dict[str, str] | None:
    """Find an inline button by its text.

    Args:
        message: Telethon Message object
        text: Button text to search for

    Returns:
        Button dict or None if not found
    """
    buttons = extract_inline_buttons(message)
    for button in buttons:
        if button["text"].lower() == text.lower():
            return button
    return None


def find_button_by_data(
    message: Message,
    data: str,
) -> dict[str, str] | None:
    """Find an inline button by its callback data.

    Args:
        message: Telethon Message object
        data: Button callback data to search for

    Returns:
        Button dict or None if not found
    """
    buttons = extract_inline_buttons(message)
    for button in buttons:
        if button["data"] == data:
            return button
    return None


def click_button(
    client: TelegramClient,
    message: Message,
    button_data: str,
) -> Message | None:
    """Click an inline button and wait for response.

    Args:
        client: Telethon client
        message: Message containing buttons
        button_data: Callback data of button to click

    Returns:
        Response message from bot, or None if timeout
    """
    # Click the button
    client.click(message, data=button_data)

    # Wait for response
    time.sleep(1.0)
    bot = message.sender
    return wait_for_message(client, bot, timeout=5.0)


def parse_signal_response(text: str) -> dict[str, Any]:
    """Parse a signal response from the bot.

    Args:
        text: Bot response text

    Returns:
        Dict with signal data (direction, confidence, entry, sl, tp)
    """
    result: dict[str, Any] = {
        "direction": None,
        "confidence": None,
        "entry": None,
        "sl": None,
        "tp": None,
        "symbol": None,
    }

    text_lower = text.lower()

    # Direction
    if "buy" in text_lower or "long" in text_lower:
        result["direction"] = "BUY"
    elif "sell" in text_lower or "short" in text_lower:
        result["direction"] = "SELL"

    # Confidence (look for percentage)
    import re

    confidence_match = re.search(r"(\d+)%", text)
    if confidence_match:
        result["confidence"] = int(confidence_match.group(1))

    # Entry price (look for entry: XAUUSD X,XXX)
    entry_match = re.search(r"entry[:\s]+(\d+[.,]?\d*)", text, re.IGNORECASE)
    if entry_match:
        result["entry"] = float(entry_match.group(1).replace(",", ""))

    # Stop loss
    sl_match = re.search(r"sl[:\s]+(\d+[.,]?\d*)", text, re.IGNORECASE)
    if sl_match:
        result["sl"] = float(sl_match.group(1).replace(",", ""))

    # Take profit
    tp_match = re.search(r"tp[:\s]+(\d+[.,]?\d*)", text, re.IGNORECASE)
    if tp_match:
        result["tp"] = float(tp_match.group(1).replace(",", ""))

    # Symbol
    symbols = ["xauusd", "eurusd", "gbpusd", "usdjpy", "audusd", "nzdusd", "usdcad", "usdchf"]
    for symbol in symbols:
        if symbol in text_lower:
            result["symbol"] = symbol.upper()
            break

    return result


def parse_price_response(text: str) -> dict[str, Any]:
    """Parse a price response from the bot.

    Args:
        text: Bot response text

    Returns:
        Dict with price data (price, symbol, session)
    """
    import re

    result: dict[str, Any] = {
        "price": None,
        "symbol": None,
        "session": None,
    }

    # Price (look for numbers with decimals)
    price_match = re.search(r"\$?(\d+[.,]\d+)", text)
    if price_match:
        result["price"] = float(price_match.group(1).replace(",", ""))

    # Session
    sessions = ["asia", "london", "new york", "ny"]
    text_lower = text.lower()
    for session in sessions:
        if session in text_lower:
            result["session"] = session.upper()
            break

    return result


def is_error_response(text: str) -> bool:
    """Check if bot response is an error message.

    Args:
        text: Bot response text

    Returns:
        True if response indicates an error
    """
    error_keywords = [
        "error",
        "failed",
        "unavailable",
        "not support",
        "not found",
        "invalid",
        "try again",
        "wait",
    ]

    text_lower = text.lower()
    return any(keyword in text_lower for keyword in error_keywords)


def is_rate_limit_response(text: str) -> bool:
    """Check if bot response is a rate limit message.

    Args:
        text: Bot response text

    Returns:
        True if response indicates rate limiting
    """
    rate_limit_keywords = [
        "wait",
        "seconds",
        "minute",
        "rate limit",
        "too many",
        "slow down",
        "try again in",
    ]

    text_lower = text.lower()
    return any(keyword in text_lower for keyword in rate_limit_keywords)
