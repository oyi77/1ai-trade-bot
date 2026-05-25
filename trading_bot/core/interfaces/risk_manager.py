"""
Risk manager port — defines the contract for pre-trade risk checks.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class IRiskManager(ABC):
    """Abstract interface for risk management.

    Every order passes through risk checks before hitting the exchange.
    Adapters may enforce static limits, dynamic VAR models, or
    circuit-breaker patterns.
    """

    @abstractmethod
    async def check_risk(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        account_balance: float,
    ) -> Dict[str, Any]:
        """Evaluate whether a proposed trade passes risk rules.

        Args:
            symbol:          Trading pair.
            side:            "buy" or "sell".
            amount:          Proposed quantity.
            price:           Expected execution price.
            account_balance: Current free balance.

        Returns:
            Dict with at least:
            - ``approved`` (bool)
            - ``reason``   (str) — empty when approved
            - ``adjusted_amount`` (float) — possibly capped amount
        """

    @abstractmethod
    async def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        account_balance: float,
        risk_percent: float = 1.0,
    ) -> float:
        """Calculate the optimal position size given risk parameters.

        Args:
            symbol:          Trading pair.
            entry_price:     Planned entry.
            stop_loss:       Stop-loss level.
            account_balance: Equity available for risk.
            risk_percent:    Percent of equity to risk (default 1 %).

        Returns:
            Position size (units of the base asset).
        """

    @abstractmethod
    async def check_circuit_breaker(self) -> Dict[str, Any]:
        """Determine whether the global circuit breaker has tripped.

        Returns:
            Dict with at least:
            - ``tripped``  (bool)
            - ``reason``   (str)
            - ``cooldown`` (int) — seconds remaining before reset
        """
