"""
tradebot — Unified Trading Bot Framework
=========================================

A modern, modular trading bot package for Deriv synthetic indices, MT5,
and other brokers. Provides broker abstraction, signal analysis engines,
pipeline orchestration, persistence, and long-running services.

Usage:
    from tradebot.config import settings
    from tradebot.models import Signal, Trade, Tick
    from tradebot.brokers.deriv import DerivWSClient
    from tradebot.brokers.mt5 import MT5Executor
"""

# ── Backward Compatibility: Deprecated Aliases ───────────────────────
# These re-export old locations to ease migration without breaking
# callers.  A DeprecationWarning is emitted on the first import of
# each legacy path.
#
# Old:  from scripts.deriv.client import DerivWSClient
# New:  from tradebot.brokers.deriv import DerivWSClient
#
# Old:  from scripts.deriv.config import ...
# New:  from tradebot.config import settings
#
# Old:  from scripts.deriv.derive_engine import DeriveEngine
# New:  from tradebot.engines import Engine
#
# Old:  from scripts.deriv.strategy import DigitMartingaleStrategy
# New:  from tradebot.brokers.deriv import DigitMartingaleStrategy
#
# Old:  from scripts.deriv.patterns import MomenPatternAnalyzer, ...
# New:  from tradebot.brokers.deriv import MomenPatternAnalyzer, ...
#
# Old:  from scripts.deriv.persistence import ...
# New:  from tradebot.storage import SQLiteStorage, CognitiveDB
#
# Old:  from scripts.deriv.backtest import DigitBacktestEngine
# New:  from tradebot.analytics import ...  (use tradebot.analytics)
#
# Old:  from scripts.deriv.actuary import ...
# New:  from tradebot.analytics import ...
#
# Old:  from scripts.deriv.deriv_telegram import ...
# New:  from tradebot.services import TelegramService
#
# Old:  from scripts.deriv.deriv_signal_bridge import ...
# New:  from tradebot.services import BridgeServer
#
# Old:  from bots import ...
# New:  from tradebot.bots import ...
#
# Old:  from signals import ...
# New:  from tradebot.signals import ...
#
# Old:  from core import ...
# New:  from tradebot.models import ...
#
# Old:  from members import ...
# New:  from tradebot.services import ...
#
# Old:  from strategies import ...
# New:  from tradebot.engines import ...
#
# Old:  from engines import ...
# New:  from tradebot.engines import ...
#
# Old:  from brokers import ...
# New:  from tradebot.brokers import ...
import warnings as _warnings

# ── Brokers ──
from tradebot.brokers import Broker, MT5Broker
from tradebot.brokers.deriv import (
    AdjacencyPatternAnalyzer,
    DerivContractResult,
    DerivOHLCV,
    DerivTick,
    DerivWSClient,
    DigitMartingaleStrategy,
    MomenPatternAnalyzer,
    StreakCountdownAnalyzer,
)
from tradebot.brokers.mt5 import MT5Executor

# ── Configuration ──
from tradebot.config import settings

# ── Engines ──
from tradebot.engines import Engine, EngineConsensus, Registry

# ── Exceptions ──
from tradebot.exceptions import (
    AuthError,
    ConfigurationError,
    ConnectionError,
    HealthCheckFailed,
    InsufficientFundsError,
    OrderError,
    PipelineError,
    RateLimitError,
    SignalError,
    StorageError,
    SymbolError,
    TradebotError,
)

# ── Logging ──
from tradebot.logging import (
    CorrelationIDFilter,
    JSONFormatter,
    get_correlation_id,
    get_logger,
    set_correlation_id,
    setup_logging,
)

# ── Domain Models ──
from tradebot.models import (
    OHLCV,
    Account,
    Balance,
    MarketState,
    Order,
    Signal,
    SignalGrade,
    SignalSource,
    Tick,
    Trade,
    TradeResult,
)

# ── Pipeline ──
from tradebot.pipeline import SignalPipeline, TradeExecutor

# ── Services ──
from tradebot.services import TelegramService

# ── Storage ──
from tradebot.storage import AbstractStorage, CognitiveDB, SQLiteStorage, TieredCache

# ── Utilities ──
from tradebot.utils import (
    AsyncRateLimiter,
    ManagedEventLoop,
    RetryableError,
    async_retry,
    asyncify,
    cancel_all,
    run_periodic,
    timeout_wrapper,
    validate_barrier,
    validate_duration,
    validate_stake,
    validate_symbol,
)

from .__version__ import __version__


def __getattr__(name: str):
    """Catch-all deprecation for legacy top-level import paths.

    If something tries ``from tradebot import SomeOldName`` and it isn't
    in ``__all__``, emit a helpful warning.
    """
    _warnings.warn(
        f"``tradebot.{name}`` is not a direct export. "
        f"Use the full submodule path (e.g. tradebot.brokers.deriv.{name}) "
        f"or check the migration guide in ARCHITECTURE.md.",
        DeprecationWarning,
        stacklevel=2,
    )
    raise AttributeError(
        f"module 'tradebot' has no attribute {name!r}. "
        f"See the backward-compat comment in tradebot/__init__.py for mappings."
    )


__all__ = [
    # Version
    "__version__",
    # Config
    "settings",
    # Models
    "Signal",
    "SignalGrade",
    "SignalSource",
    "Trade",
    "TradeResult",
    "Order",
    "Tick",
    "OHLCV",
    "MarketState",
    "Account",
    "Balance",
    # Exceptions
    "TradebotError",
    "ConfigurationError",
    "ConnectionError",
    "AuthError",
    "RateLimitError",
    "SymbolError",
    "InsufficientFundsError",
    "OrderError",
    "SignalError",
    "PipelineError",
    "HealthCheckFailed",
    "StorageError",
    # Logging
    "setup_logging",
    "get_logger",
    "JSONFormatter",
    "CorrelationIDFilter",
    "set_correlation_id",
    "get_correlation_id",
    # Utils
    "AsyncRateLimiter",
    "async_retry",
    "RetryableError",
    "cancel_all",
    "run_periodic",
    "timeout_wrapper",
    "asyncify",
    "ManagedEventLoop",
    "validate_symbol",
    "validate_stake",
    "validate_barrier",
    "validate_duration",
    # Brokers
    "Broker",
    "MT5Broker",
    "DerivWSClient",
    "DerivTick",
    "DerivOHLCV",
    "DerivContractResult",
    "MomenPatternAnalyzer",
    "AdjacencyPatternAnalyzer",
    "StreakCountdownAnalyzer",
    "DigitMartingaleStrategy",
    "MT5Executor",
    # Engines
    "Engine",
    "EngineConsensus",
    "Registry",
    # Pipeline
    "SignalPipeline",
    "TradeExecutor",
    # Services
    "TelegramService",
    "BridgeServer",
    # Storage
    "AbstractStorage",
    "SQLiteStorage",
    "CognitiveDB",
    "TieredCache",
]
