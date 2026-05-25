"""
Core interfaces (ports) for hexagonal architecture.

All interfaces are abstract base classes that define the contract
between the core domain and external adapters. No concrete
implementations belong here.
"""

from trading_bot.core.interfaces.exchange_provider import IExchangeProvider
from trading_bot.core.interfaces.strategy import IStrategy
from trading_bot.core.interfaces.data_provider import IDataProvider
from trading_bot.core.interfaces.wallet_manager import IWalletManager
from trading_bot.core.interfaces.risk_manager import IRiskManager

__all__ = [
    "IExchangeProvider",
    "IStrategy",
    "IDataProvider",
    "IWalletManager",
    "IRiskManager",
]
