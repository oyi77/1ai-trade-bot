"""
Wallet domain models.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class WalletBalance:
    """Balance snapshot for a single asset."""

    symbol: str
    total: float = 0.0
    free: float = 0.0
    locked: float = 0.0
    usd_value: float = 0.0


@dataclass
class WalletProfile:
    """Aggregate profile of a wallet across chains/assets."""

    address: str
    chain: str = ""
    total_usd_value: float = 0.0
    balances: Dict[str, WalletBalance] = field(default_factory=dict)
    is_connected: bool = False
