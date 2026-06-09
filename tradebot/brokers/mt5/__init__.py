"""MT5 broker package — MetaTrader 5 integration.

Provides:
- ``MT5Broker`` — broker interface implementation (async)
- ``MT5Executor`` — high-level EA executor with position management & SL/TP tracking
"""

from .broker import MT5Broker
from .executor import MT5Executor

__all__ = ["MT5Broker", "MT5Executor"]
