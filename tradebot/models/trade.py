"""
Trade execution models — trades, orders, and results.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Order:
    """An order placed with a broker."""
    order_id: str
    symbol: str
    contract_type: str
    stake: float
    barrier: int
    direction: str
    duration: int = 1
    duration_unit: str = "t"
    status: str = "pending"  # pending, open, won, lost, cancelled
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    filled_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TradeResult:
    """Result of a completed trade or trade cycle."""
    profit: float
    total_stake: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    cycles: int = 0
    stopped_early: bool = False
    reason: str = ""
    symbol: str = ""
    contract_type: str = ""


@dataclass
class Trade:
    """A single trade execution record."""
    trade_id: str
    symbol: str
    contract_type: str
    direction: str
    stake: float
    predicted_digit: int
    entry_price: float
    exit_price: float | None = None
    payout: float = 0.0
    profit: float = 0.0
    is_win: bool = False
    is_completed: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = field(default_factory=dict)
