"""CCXT broker package — unified crypto exchange trading.

Provides:
- ``CCXTBroker`` — broker interface implementation via CCXT unified API
"""

from .broker import CCXTBroker

__all__ = ["CCXTBroker"]
