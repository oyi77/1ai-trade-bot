"""Unified provider abstraction — base provider interface and data models.

Every market provider (forex, crypto, stocks, commodities, DEX) implements the
:class:`BaseProvider` interface. Shared data models (:class:`Order`,
:class:`Position`, :class:`Candle`) are defined here so strategies and engines
never import provider-specific types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum, auto
from typing import Any

# ---------------------------------------------------------------------------
#  Enums
# ---------------------------------------------------------------------------


class MarketType(StrEnum):
    """Asset class / market type."""

    FOREX = auto()
    CRYPTO = auto()
    STOCKS = auto()
    COMMODITIES = auto()
    BONDS = auto()
    DEX = auto()


class OrderType(StrEnum):
    """Order type."""

    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()


class OrderSide(StrEnum):
    """Order direction."""

    BUY = auto()
    SELL = auto()


class OrderStatus(StrEnum):
    """Order lifecycle status."""

    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(StrEnum):
    """Time-in-force instructions."""

    GTC = "GTC"  # Good Till Cancelled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    DAY = "DAY"  # Day order


# ---------------------------------------------------------------------------
#  Data models
# ---------------------------------------------------------------------------


@dataclass
class Candle:
    """OHLCV candle."""

    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # noqa: UP017


@dataclass
class Order:
    """A trade order submitted to a provider."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None  # None for MARKET orders
    stop_price: float | None = None  # For STOP / STOP_LIMIT
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    leverage: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """An open position."""

    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float
    leverage: int = 1
    liquidation_price: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # noqa: UP017
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    """Result of order placement."""

    order_id: str
    status: OrderStatus
    filled_quantity: float = 0.0
    filled_price: float | None = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
#  Abstract provider interface
# ---------------------------------------------------------------------------


class BaseProvider(ABC):
    """Abstract base class for all market providers.

    Every provider (forex, crypto, stocks, paper) implements this interface.
    Strategies and the orchestration layer depend only on ``BaseProvider``,
    never on concrete provider classes.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name identifier (e.g. ``'exness'``, ``'binance'``, ``'paper'``)."""
        ...

    @property
    @abstractmethod
    def market_type(self) -> MarketType:
        """Primary market type this provider supports."""
        ...

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the provider.

        Returns:
            True if the connection was successful.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection and clean up resources."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if the provider connection is active."""
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        """Return current account balance in base currency."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Return all open positions."""
        ...

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult:
        """Submit an order for execution."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order by ID.

        Returns:
            True if the order was found and cancelled.
        """
        ...

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        """Fetch OHLCV candles for a symbol."""
        ...

    @abstractmethod
    async def get_symbols(self) -> list[str]:
        """Return list of tradable symbols."""
        ...
