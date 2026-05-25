"""
Position domain model.
"""

from dataclasses import dataclass
from enum import Enum


class PositionSide(Enum):
    """Direction of an open position."""

    LONG = "long"
    SHORT = "short"


class PositionStatus(Enum):
    """Lifecycle states a position can be in."""

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"


@dataclass
class Position:
    """Represents an open (or recently closed) position."""

    id: str
    symbol: str
    side: str  # PositionSide value
    entry_price: float
    amount: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    leverage: float = 1.0
    margin: float = 0.0
    status: str = "open"  # PositionStatus value
    open_time: int = 0
    close_time: int = 0
    exchange: str = ""
