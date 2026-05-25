"""
Strategy-related domain errors.
"""


class StrategyError(Exception):
    """Base error for all strategy-level failures."""

    def __init__(self, message: str = "", strategy: str = "") -> None:
        self.strategy = strategy
        super().__init__(message)


class InvalidTransitionError(StrategyError):
    """Raised when a strategy attempts an illegal state transition."""

    def __init__(
        self,
        message: str = "Invalid state transition",
        strategy: str = "",
        from_state: str = "",
        to_state: str = "",
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(message, strategy)


class ConfigurationError(StrategyError):
    """Raised when strategy configuration is invalid or incomplete."""

    def __init__(
        self,
        message: str = "Configuration error",
        strategy: str = "",
        missing_keys: tuple = (),
    ) -> None:
        self.missing_keys = missing_keys
        super().__init__(message, strategy)
