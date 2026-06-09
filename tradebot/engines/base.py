"""
Abstract Engine base class — all signal analysis engines must conform.
"""

from abc import ABC, abstractmethod

from tradebot.models import Signal, Tick


class Engine(ABC):
    """Abstract base for signal analysis engines.

    Each engine takes a list of market ticks and produces an optional Signal.
    """

    @abstractmethod
    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze market data and return a signal if conditions are met."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable engine name."""
        ...
