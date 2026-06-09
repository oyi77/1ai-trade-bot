"""
TradeBot exception hierarchy.

All custom exceptions derive from ``TradebotError`` and accept an optional
``details`` dict carried as ``.details`` for structured error context.
"""

from __future__ import annotations

from typing import Any


class TradebotError(Exception):
    """Base exception for all TradeBot errors."""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        self._message = str(message)
        self.details = details or {}
        super().__init__(self._message)

    @property
    def message(self) -> str:
        return self._message

    def __str__(self) -> str:
        return self._message


class ConfigurationError(TradebotError):
    """Missing, malformed, or invalid configuration."""


class ConnectionError(TradebotError):
    """Generic broker / exchange connection failure."""


class AuthError(ConnectionError):
    """Authentication token failure (e.g. expired, revoked, malformed)."""


class RateLimitError(ConnectionError):
    """Rate limited by the exchange or API provider.

    Attributes:
        retry_after: Seconds to wait before retrying (populated when known).
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None, retry_after: float | None = None) -> None:  # noqa: E501
        self.retry_after = retry_after
        super().__init__(message, details=details)


class SymbolError(TradebotError):
    """Invalid, delisted, or unsupported trading symbol."""


class InsufficientFundsError(TradebotError):
    """Account balance is too low to place the requested order."""


class OrderError(TradebotError):
    """Order placement, amendment, or cancellation failed."""


class SignalError(TradebotError):
    """Signal generation failed (e.g. indicator error, missing data)."""


class PipelineError(TradebotError):
    """A stage in the trading pipeline raised an unrecoverable error."""


class HealthCheckFailed(TradebotError):  # noqa: N818
    """Health or liveness probe did not pass."""


class StorageError(TradebotError):
    """Database or file-storage operation failed."""


__all__ = [
    "TradebotError",
    "ConfigurationError",
    "ConnectionError",
    "AuthError",
    "RateLimitError",
    "SymbolError",
    "InsufficientFundsError",
    "OrderError",
    "SignalError",
    "PipelineError",
    "HealthCheckFailed",
    "StorageError",
]
