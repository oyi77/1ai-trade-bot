"""
Abstract Broker interface — all broker implementations must conform to this.
"""

from abc import ABC, abstractmethod

from tradebot.models import Balance, Order


class Broker(ABC):
    """Abstract base class for trading broker integrations."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the broker."""
        ...

    @abstractmethod
    async def disconnect(self):
        """Cleanly disconnect from the broker."""
        ...

    @abstractmethod
    async def get_balance(self) -> Balance | None:
        """Fetch current account balance."""
        ...

    @abstractmethod
    async def place_order(self, symbol: str, contract_type: str,
                          barrier: int, stake: float, **kwargs) -> Order | None:
        """Place a trade order."""
        ...

    @abstractmethod
    async def subscribe_ticks(self, symbol: str) -> bool:
        """Subscribe to real-time tick data."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker is connected."""
        ...
