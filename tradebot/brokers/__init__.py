"""Broker abstractions — unified interface for trading brokers."""

from .base import (
    BaseBroker,
    BrokerPlatform,
    TradeDirection,
    TradeResult,
    TradeStatus,
    get_broker,
)
from .ccxt.broker import CCXTBroker
from .mt5.broker import MT5Broker
from .stockity.broker import StockityBroker

# Re-export for backwards compatibility
Broker = BaseBroker

__all__ = [
    "BaseBroker",
    "Broker",
    "BrokerPlatform",
    "CCXTBroker",
    "MT5Broker",
    "StockityBroker",
    "TradeDirection",
    "TradeResult",
    "TradeStatus",
    "get_broker",
]
