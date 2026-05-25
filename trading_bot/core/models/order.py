"""
Order-related domain models.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    """Direction of an order."""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Type of order execution."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Lifecycle states an order can be in."""

    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SignalAction(Enum):
    """What a trade signal is asking for."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


@dataclass(frozen=True)
class TradeSignal:
    """Immutable output of strategy analysis.

    Represents an intent to trade — not yet an order.
    """

    symbol: str
    action: str  # SignalAction value ("buy", "sell", "hold", "close")
    confidence: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    timestamp: int = 0
    metadata: tuple = ()  # frozen-compatible; use tuple of (k, v) pairs


@dataclass
class Order:
    """Represents an order placed on an exchange."""

    id: str
    symbol: str
    side: str  # OrderSide value
    order_type: str  # OrderType value
    amount: float
    price: float = 0.0
    stop_price: float = 0.0
    filled_amount: float = 0.0
    average_price: float = 0.0
    status: str = "pending"  # OrderStatus value
    fee: float = 0.0
    created_at: int = 0
    updated_at: int = 0
    exchange: str = ""
    client_order_id: str = ""
