"""
Domain-specific exceptions.

Typed error hierarchy for trading operations:
- ExchangeError  → InsufficientBalanceError, ExchangeConnectionError, ExchangeTimeoutError
- StrategyError  → InvalidTransitionError, ConfigurationError
"""

from trading_bot.core.errors.exchange_errors import (
    ExchangeError,
    InsufficientBalanceError,
    ExchangeConnectionError,
    ExchangeTimeoutError,
)
from trading_bot.core.errors.strategy_errors import (
    StrategyError,
    InvalidTransitionError,
    ConfigurationError,
)

__all__ = [
    "ExchangeError",
    "InsufficientBalanceError",
    "ExchangeConnectionError",
    "ExchangeTimeoutError",
    "StrategyError",
    "InvalidTransitionError",
    "ConfigurationError",
]
