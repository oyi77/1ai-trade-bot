"""
Exchange-related domain errors.
"""


class ExchangeError(Exception):
    """Base error for all exchange-level failures."""

    def __init__(self, message: str = "", exchange: str = "") -> None:
        self.exchange = exchange
        super().__init__(message)


class InsufficientBalanceError(ExchangeError):
    """Raised when an order cannot be placed due to insufficient funds."""

    def __init__(
        self,
        message: str = "Insufficient balance",
        exchange: str = "",
        required: float = 0.0,
        available: float = 0.0,
    ) -> None:
        self.required = required
        self.available = available
        super().__init__(message, exchange)


class ExchangeConnectionError(ExchangeError):
    """Raised when a connection to the exchange cannot be established."""

    pass


class ExchangeTimeoutError(ExchangeError):
    """Raised when an exchange operation takes too long."""

    def __init__(
        self,
        message: str = "Exchange operation timed out",
        exchange: str = "",
        timeout_seconds: float = 0.0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(message, exchange)
