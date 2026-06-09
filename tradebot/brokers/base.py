"""
Unified broker interface for trade execution.

All brokers (Stockity, Deriv, MT5) implement this interface so the
autonomous agent can execute trades on any platform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BrokerPlatform(StrEnum):
    """Supported trading platforms."""
    STOCKITY = "stockity"
    DERIV = "deriv"
    MT5 = "mt5"


class TradeDirection(StrEnum):
    """Trade direction."""
    CALL = "CALL"  # Price up (binary options) / BUY (forex/CFD)
    PUT = "PUT"    # Price down (binary options) / SELL (forex/CFD)


class TradeStatus(StrEnum):
    """Trade lifecycle status."""
    PENDING = "pending"
    OPENED = "opened"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class TradeResult:
    """Result of a trade execution."""
    platform: BrokerPlatform
    order_id: str
    symbol: str
    direction: TradeDirection
    amount: float
    duration: int | None = None  # Binary options only (seconds)
    status: TradeStatus = TradeStatus.PENDING
    error: str | None = None
    payout: float | None = None  # Final payout (after close)
    metadata: dict[str, Any] | None = None


class BaseBroker(ABC):
    """Abstract base class for all brokers.

    Implement this interface to add support for new platforms.
    """

    @property
    @abstractmethod
    def platform(self) -> BrokerPlatform:
        """Return the platform this broker supports."""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the broker."""
        pass

    @abstractmethod
    async def get_balance(self) -> float | None:
        """Get current account balance."""
        pass

    @abstractmethod
    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
        amount: float,
        duration: int | None = None,
    ) -> TradeResult:
        """Place a trade.

        Args:
            symbol: Asset symbol (e.g. "CRYPTO_IDX", "R_75", "EURUSD").
            direction: CALL/PUT for binary, BUY/SELL for forex/CFD.
            amount: Stake/lot size.
            duration: Duration in seconds (binary options only).

        Returns:
            TradeResult with order_id and status.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close connection."""
        pass

    async def __aenter__(self) -> BaseBroker:
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def get_broker(platform: str | BrokerPlatform) -> BaseBroker:
    """Get a broker instance for the specified platform.

    Args:
        platform: Platform name ("stockity", "deriv", "mt5").

    Returns:
        Broker instance for that platform.

    Raises:
        ValueError: If platform is not supported.
    """
    platform_enum = BrokerPlatform(platform.lower()) if isinstance(platform, str) else platform

    if platform_enum == BrokerPlatform.STOCKITY:
        from tradebot.brokers.stockity.broker import StockityBroker
        return StockityBroker()
    elif platform_enum == BrokerPlatform.DERIV:
        from tradebot.brokers.deriv.client import DerivWSClient
        return DerivBrokerAdapter(DerivWSClient)
    elif platform_enum == BrokerPlatform.MT5:
        from tradebot.brokers.mt5.broker import MT5Broker
        return MT5Broker()
    else:
        raise ValueError(f"Unsupported platform: {platform}")


# Adapter for Deriv (wraps DerivWSClient to match BaseBroker interface)
class DerivBrokerAdapter(BaseBroker):
    """Adapter to make DerivWSClient conform to BaseBroker interface."""

    def __init__(self, client_class: type) -> None:
        self._client_class = client_class
        self._client: Any | None = None

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.DERIV

    async def connect(self) -> None:
        from tradebot.config import settings
        self._client = self._client_class(
            pat_token=settings.DERIV_PAT_TOKEN,
            account_id=settings.DERIV_ACCOUNT_ID,
            mode=settings.DERIV_MODE,
        )
        await self._client.connect()

    async def get_balance(self) -> float | None:
        if self._client is None:
            return None
        return await self._client.get_balance()

    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
        amount: float,
        duration: int | None = None,
    ) -> TradeResult:
        if self._client is None:
            return TradeResult(
                platform=self.platform,
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                status=TradeStatus.ERROR,
                error="Not connected",
            )

        try:
            # Map direction to Deriv contract type
            contract_type = "DIGITOVER" if direction == TradeDirection.CALL else "DIGITUNDER"
            barrier = 7  # Default barrier

            # Place trade via Deriv client
            result = await self._client.buy_digit(
                symbol=symbol,
                contract_type=contract_type,
                barrier=barrier,
                stake=amount,
            )

            if result is None:
                return TradeResult(
                    platform=self.platform,
                    order_id="",
                    symbol=symbol,
                    direction=direction,
                    amount=amount,
                    status=TradeStatus.REJECTED,
                    error="Trade rejected by Deriv",
                )

            return TradeResult(
                platform=self.platform,
                order_id=str(result.get("contract_id", "")),
                symbol=symbol,
                direction=direction,
                amount=amount,
                duration=duration,
                status=TradeStatus.OPENED,
                metadata=result,
            )
        except Exception as e:
            return TradeResult(
                platform=self.platform,
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                status=TradeStatus.ERROR,
                error=str(e),
            )

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# Adapter for MT5 (wraps MT5Broker to match BaseBroker interface)
class MT5BrokerAdapter(BaseBroker):
    """Adapter to make MT5 broker conform to BaseBroker interface."""

    def __init__(self) -> None:
        self._broker: Any | None = None

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.MT5

    async def connect(self) -> None:
        from tradebot.brokers.mt5.broker import MT5Broker
        self._broker = MT5Broker()
        await self._broker.connect()

    async def get_balance(self) -> float | None:
        if self._broker is None:
            return None
        return await self._broker.get_balance()

    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
        amount: float,
        duration: int | None = None,
    ) -> TradeResult:
        if self._broker is None:
            return TradeResult(
                platform=self.platform,
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                status=TradeStatus.ERROR,
                error="Not connected",
            )

        try:
            # MT5 uses lots, not stake amount
            lots = amount / 100000  # Convert stake to lots (simplified)
            order_type = 0 if direction == TradeDirection.CALL else 1  # BUY=0, SELL=1

            result = await self._broker.place_order(
                symbol=symbol,
                order_type=order_type,
                volume=lots,
            )

            if not result:
                return TradeResult(
                    platform=self.platform,
                    order_id="",
                    symbol=symbol,
                    direction=direction,
                    amount=amount,
                    status=TradeStatus.REJECTED,
                    error="Order rejected by MT5",
                )

            return TradeResult(
                platform=self.platform,
                order_id=str(result.get("order", "")),
                symbol=symbol,
                direction=direction,
                amount=amount,
                duration=duration,
                status=TradeStatus.OPENED,
                metadata=result,
            )
        except Exception as e:
            return TradeResult(
                platform=self.platform,
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                status=TradeStatus.ERROR,
                error=str(e),
            )

    async def close(self) -> None:
        if self._broker:
            await self._broker.close()
            self._broker = None
