"""
User Broker Factory — per-user broker instance management.

Each user has their own broker instances with their own credentials.
Global STOCKITY_FULL_COOKIE is used ONLY as fallback for market data
when per-user credentials are unavailable or fail.

Architecture:
    get_user_broker(user_id, platform, for_execution=False)
        → Loads credentials from user_platforms table
        → Creates broker with user's cookies
        → If login fails AND not for_execution → fallback to global cookie
        → If login fails AND for_execution → return None (cannot execute)

    disposable_broker_context(user_id, platform)
        → Async context manager for one-off operations
        → Auto-closes on exit
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tradebot.brokers.base import BaseBroker
from tradebot.brokers.stockity.broker import StockityBroker
from tradebot.config import settings

LOG = logging.getLogger("tradebot.brokers.user_factory")


async def get_user_broker(
    user_id: str,
    platform: str,
    for_execution: bool = False,
) -> BaseBroker | None:
    """Get a broker instance configured for a specific user.

    Args:
        user_id: Telegram chat_id.
        platform: Broker platform (stockity, deriv, ccxt, mt5).
        for_execution: If True, user MUST have valid credentials.
                       If False, falls back to global cookie for market data.

    Returns:
        Broker instance, or None if no credentials available and execution
        is required.

    Priority chain (Stockity):
        1. User's own cookie (from stored session) → primary
        2. Global STOCKITY_FULL_COOKIE → fallback (market data only)
        3. None → cannot proceed
    """
    if platform == "stockity":
        return await _get_stockity_broker(user_id, for_execution)

    LOG.warning("Platform %s not yet supported for per-user brokers", platform)
    return None


async def _get_stockity_broker(user_id: str, for_execution: bool = False) -> StockityBroker | None:
    """Create a StockityBroker with user's cookie or global fallback."""
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "stockity")
    if creds:
        cookies = json.loads(creds.get("credentials", "{}"))
        cookie = cookies.get("cookie", "")
        currency = creds.get("currency", "IDR")
        if cookie:
            LOG.info("Using user's cookie for %s (currency=%s)", user_id, currency)
            broker = StockityBroker()
            broker._cookie = cookie
            broker._balance_currency = currency
            return broker

    if not for_execution:
        if settings.STOCKITY_FULL_COOKIE:
            LOG.info("Falling back to global cookie for user %s (market data)", user_id)
            broker = StockityBroker()
            return broker

    LOG.warning(
        "No credentials for user %s stockity (execute=%s)",
        user_id,
        for_execution,
    )
    return None


async def refresh_user_broker(user_id: str, platform: str) -> bool:
    """Refresh a user's broker session (re-login with stored credentials).

    Returns:
        True if refresh succeeded, False otherwise.
    """
    if platform == "stockity":
        from tradebot.services.platform_link_service import PlatformLinkService

        svc = PlatformLinkService()
        cookie = await svc.refresh_stockity_cookie(user_id)
        return cookie is not None

    LOG.warning("Cookie refresh not supported for platform: %s", platform)
    return False


@asynccontextmanager
async def disposable_broker_context(
    user_id: str,
    platform: str,
    for_execution: bool = False,
) -> AsyncIterator[BaseBroker | None]:
    """Async context manager for a one-off broker operation.

    Usage:
        async with disposable_broker_context("12345", "stockity") as broker:
            if broker:
                result = await broker.place_trade(...)
    """
    broker = None
    try:
        broker = await get_user_broker(user_id, platform, for_execution)
        yield broker
    finally:
        if broker:
            await broker.close()
