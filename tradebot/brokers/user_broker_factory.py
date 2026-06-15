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
        platform: Broker platform (stockity, deriv, ccxt, mt5, ajaib, pluang, stockbit, robinhood).
        for_execution: If True, user MUST have valid credentials.
                       If False, falls back to global cookie for market data
                       (Stockity only).

    Returns:
        Broker instance, or None if no credentials available and execution
        is required.

    Priority chain:
        Stockity: user cookie → global cookie (market data only) → None
        Deriv/CCXT/MT5: stored user credentials → None (no fallback)
        Ajaib/Pluang/Stockbit/Robinhood: stored user credentials → None (no fallback)
    """
    if platform == "stockity":
        return await _get_stockity_broker(user_id, for_execution)
    elif platform == "deriv":
        return await _get_deriv_broker(user_id, for_execution)
    elif platform == "ccxt":
        return await _get_ccxt_broker(user_id, for_execution)
    elif platform == "mt5":
        return await _get_mt5_broker(user_id, for_execution)
    elif platform == "ajaib":
        return await _get_ajaib_broker(user_id, for_execution)
    elif platform == "pluang":
        return await _get_pluang_broker(user_id, for_execution)
    elif platform == "stockbit":
        return await _get_stockbit_broker(user_id, for_execution)
    elif platform == "robinhood":
        return await _get_robinhood_broker(user_id, for_execution)

    LOG.warning("Platform %s not supported for per-user brokers", platform)
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
            broker = StockityBroker(cookie=cookie, currency=currency)
            return broker

    if not for_execution and settings.STOCKITY_FULL_COOKIE:
            LOG.info("Falling back to global cookie for user %s (market data)", user_id)
            broker = StockityBroker()
            return broker

    LOG.warning(
        "No credentials for user %s stockity (execute=%s)",
        user_id,
        for_execution,
    )
    return None
async def _get_deriv_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create a DerivBrokerAdapter with user's API credentials."""
    from tradebot.brokers.base import DerivBrokerAdapter
    from tradebot.brokers.deriv.client import DerivWSClient
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "deriv")
    if not creds:
        LOG.warning("No credentials for user %s deriv (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    app_id = creds_json.get("app_id", "")
    secret = creds_json.get("secret", "")
    if not app_id or not secret:
        LOG.warning("Incomplete deriv credentials for user %s", user_id)
        return None

    LOG.info("Using user's deriv credentials for %s", user_id)
    client = DerivWSClient(api_token=secret, app_id=app_id)
    adapter = DerivBrokerAdapter(client)
    await adapter.connect()
    return adapter


async def _get_ccxt_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create a CCXTBroker with user's exchange API key."""
    from tradebot.brokers.ccxt.broker import CCXTBroker
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "ccxt")
    if not creds:
        LOG.warning("No credentials for user %s ccxt (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    exchange = creds_json.get("exchange", "")
    api_key = creds_json.get("api_key", "")
    api_secret = creds_json.get("api_secret", "")
    if not exchange or not api_key or not api_secret:
        LOG.warning("Incomplete ccxt credentials for user %s", user_id)
        return None

    LOG.info("Using user's ccxt credentials for %s (%s)", user_id, exchange)
    broker = CCXTBroker(exchange=exchange, api_key=api_key, secret=api_secret, sandbox=False)
    await broker.connect()
    return broker


async def _get_mt5_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create an MT5BrokerAdapter for a user.

    MT5 terminal credentials (login/password/server) come from global
    settings. The ea_key stored in user_platforms is verified before
    returning a broker.
    """
    from tradebot.brokers.base import MT5BrokerAdapter
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "mt5")
    if not creds:
        LOG.warning("No credentials for user %s mt5 (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    ea_key = creds_json.get("ea_key", "")
    if not ea_key:
        LOG.warning("No ea_key stored for user %s mt5", user_id)
        return None

    LOG.info("Using user's mt5 credentials for %s", user_id)
    broker = MT5BrokerAdapter()
    await broker.connect()
    return broker


async def _get_ajaib_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create an AjaibBroker with user's credentials."""
    from tradebot.brokers.ajaib.broker import AjaibBroker
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "ajaib")
    if not creds:
        LOG.warning("No credentials for user %s ajaib (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    email = creds_json.get("email", "")
    password = creds_json.get("password", "")
    access_token = creds_json.get("access_token", "")

    if not email or not password:
        LOG.warning("Incomplete credentials for user %s ajaib", user_id)
        return None

    broker = AjaibBroker(email=email, password=password, access_token=access_token or None)
    if await broker.connect():
        LOG.info("Using user's ajaib credentials for %s", user_id)
        return broker

    LOG.warning("Ajaib login failed for user %s", user_id)
    return None


async def _get_pluang_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create a PluangBroker with user's credentials."""
    from tradebot.brokers.pluang.broker import PluangBroker
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "pluang")
    if not creds:
        LOG.warning("No credentials for user %s pluang (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    api_key = creds_json.get("api_key", "")
    api_secret = creds_json.get("api_secret", "")
    access_token = creds_json.get("access_token", "")

    if not api_key or not api_secret:
        LOG.warning("Incomplete credentials for user %s pluang", user_id)
        return None

    broker = PluangBroker(api_key=api_key, api_secret=api_secret, access_token=access_token or None)
    if await broker.connect():
        LOG.info("Using user's pluang credentials for %s", user_id)
        return broker

    LOG.warning("Pluang login failed for user %s", user_id)
    return None


async def _get_stockbit_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create a StockbitBroker with user's credentials."""
    from tradebot.brokers.stockbit.broker import StockbitBroker
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "stockbit")
    if not creds:
        LOG.warning("No credentials for user %s stockbit (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    email = creds_json.get("email", "")
    password = creds_json.get("password", "")
    access_token = creds_json.get("access_token", "")

    if not email or not password:
        LOG.warning("Incomplete credentials for user %s stockbit", user_id)
        return None

    broker = StockbitBroker(email=email, password=password, access_token=access_token or None)
    if await broker.connect():
        LOG.info("Using user's stockbit credentials for %s", user_id)
        return broker

    LOG.warning("Stockbit login failed for user %s", user_id)
    return None


async def _get_robinhood_broker(user_id: str, for_execution: bool = False) -> BaseBroker | None:
    """Create a RobinhoodBroker with user's credentials."""
    from tradebot.brokers.robinhood.broker import RobinhoodBroker
    from tradebot.services.platform_link_service import PlatformLinkService

    svc = PlatformLinkService()
    creds = await svc.get_platform_credentials(user_id, "robinhood")
    if not creds:
        LOG.warning("No credentials for user %s robinhood (execute=%s)", user_id, for_execution)
        return None

    creds_json = json.loads(creds.get("credentials", "{}"))
    username = creds_json.get("username", "")
    password = creds_json.get("password", "")
    access_token = creds_json.get("access_token", "")
    refresh_token = creds_json.get("refresh_token", "")
    mfa_code = creds_json.get("mfa_code", "")

    if not username or not password:
        LOG.warning("Incomplete credentials for user %s robinhood", user_id)
        return None

    broker = RobinhoodBroker(
        username=username, password=password, mfa_code=mfa_code or None,
        access_token=access_token or None, refresh_token=refresh_token or None
    )
    if await broker.connect():
        LOG.info("Using user's robinhood credentials for %s", user_id)
        return broker

    LOG.warning("Robinhood login failed for user %s", user_id)
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
