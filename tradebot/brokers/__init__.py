"""Broker abstractions — unified interface for trading brokers."""

from .base import (
    BaseBroker,
    BrokerPlatform,
    TradeDirection,
    TradeResult,
    TradeStatus,
    get_broker,
)
from .mt5.broker import MT5Broker

# Re-export for backwards compatibility
Broker = BaseBroker

__all__ = [
    "BaseBroker",
    "Broker",
    "BrokerPlatform",
    "MT5Broker",
    "TradeDirection",
    "TradeResult",
    "TradeStatus",
    "get_broker",
]
