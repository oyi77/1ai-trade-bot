"""trading_bot — Unified trading provider abstraction layer."""

from __future__ import annotations

from trading_bot.providers.base import (
    BaseProvider,
    Candle,
    MarketType,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from trading_bot.providers.crypto.ccxt_adapter import CCXTProvider
from trading_bot.providers.forex.exness import ExnessProvider
from trading_bot.providers.paper.paper_trader import DEFAULT_BALANCE, PaperTradingProvider
from trading_bot.providers.registry import ProviderRegistry

__all__ = [
    "BaseProvider",
    "Candle",
    "CCXTProvider",
    "DEFAULT_BALANCE",
    "ExnessProvider",
    "MarketType",
    "Order",
    "OrderResult",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PaperTradingProvider",
    "Position",
    "ProviderRegistry",
    "TimeInForce",
]
