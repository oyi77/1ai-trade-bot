"""Tests for the exception hierarchy."""

from __future__ import annotations

from tradebot.exceptions import (
    AuthError,
    ConfigurationError,
    ConnectionError,
    HealthCheckFailed,
    InsufficientFundsError,
    OrderError,
    PipelineError,
    RateLimitError,
    SignalError,
    StorageError,
    SymbolError,
    TradebotError,
)


class TestTradebotError:
    """Base exception tests."""

    def test_base_exception(self):
        e = TradebotError("something went wrong")
        assert str(e) == "something went wrong"
        assert e.message == "something went wrong"
        assert e.details == {}

    def test_with_details(self):
        e = TradebotError("failed", details={"code": 42, "reason": "timeout"})
        assert e.details["code"] == 42
        assert e.details["reason"] == "timeout"

    def test_empty_message(self):
        e = TradebotError()
        assert str(e) == ""
        assert e.details == {}


class TestExceptionHierarchy:
    """All custom exceptions inherit correctly."""

    def test_configuration_error(self):
        e = ConfigurationError("bad config")
        assert isinstance(e, TradebotError)
        assert str(e) == "bad config"

    def test_connection_error(self):
        e = ConnectionError("connection lost")
        assert isinstance(e, TradebotError)
        assert str(e) == "connection lost"

    def test_auth_error(self):
        e = AuthError("token expired")
        assert isinstance(e, ConnectionError)
        assert isinstance(e, TradebotError)

    def test_rate_limit_error(self):
        e = RateLimitError("too fast", retry_after=5.0)
        assert isinstance(e, ConnectionError)
        assert e.retry_after == 5.0
        assert str(e) == "too fast"

    def test_rate_limit_no_retry(self):
        e = RateLimitError("rate limited")
        assert e.retry_after is None

    def test_symbol_error(self):
        e = SymbolError("invalid symbol")
        assert isinstance(e, TradebotError)

    def test_insufficient_funds(self):
        e = InsufficientFundsError("balance too low")
        assert isinstance(e, TradebotError)

    def test_order_error(self):
        e = OrderError("order failed")
        assert isinstance(e, TradebotError)

    def test_signal_error(self):
        e = SignalError("indicator error", details={"indicator": "RSI"})
        assert isinstance(e, TradebotError)
        assert e.details["indicator"] == "RSI"

    def test_pipeline_error(self):
        e = PipelineError("stage failed")
        assert isinstance(e, TradebotError)

    def test_health_check_failed(self):
        e = HealthCheckFailed("broker unreachable")
        assert isinstance(e, TradebotError)

    def test_storage_error(self):
        e = StorageError("disk full")
        assert isinstance(e, TradebotError)


class TestExceptionAll:
    """All exception types are importable from the public API."""

    def test_all_exported(self):
        names = {
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
        }
        from tradebot.exceptions import __all__ as exported  # type: ignore[attr-defined]

        assert set(exported) == names
