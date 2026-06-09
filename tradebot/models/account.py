"""
Account models — account info and balances.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Balance:
    """Account balance snapshot."""
    balance: float
    currency: str = "USD"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Account:
    """Trading account information."""
    account_id: str
    balance: Balance
    account_type: str = ""  # demo, real
    is_connected: bool = False
    metadata: dict = field(default_factory=dict)
