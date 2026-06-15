"""trading_bot — Unified trading provider abstraction layer."""

from __future__ import annotations

from trading_bot.cli import main
from trading_bot.config import BotConfig, load_config
from trading_bot.engine import (
    EngineState,
    Event,
    EventBus,
    PortfolioTracker,
    RiskConfig,
    RiskManager,
    SignalExecutor,
    TradingOrchestrator,
)
from trading_bot.persistence import PersistenceStore
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
    "BotConfig",
    "Candle",
    "CCXTProvider",
    "DEFAULT_BALANCE",
    "EngineState",
    "Event",
    "EventBus",
    "ExnessProvider",
    "MarketType",
    "Order",
    "OrderResult",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PaperTradingProvider",
    "PersistenceStore",
    "PortfolioTracker",
    "Position",
    "ProviderRegistry",
    "RiskConfig",
    "RiskManager",
    "SignalExecutor",
    "TimeInForce",
    "TradingOrchestrator",
    "load_config",
    "main",
]
