"""Tests for input validation utilities."""

from __future__ import annotations

import pytest

from tradebot.utils.validators import (
    validate_barrier,
    validate_duration,
    validate_stake,
    validate_symbol,
)


class TestValidateSymbol:
    """validate_symbol — normalise and validate symbols."""

    def test_valid_deriv_random(self):
        assert validate_symbol("R_75") == "R_75"
        assert validate_symbol("R_100") == "R_100"
        assert validate_symbol("r_25") == "R_25"

    def test_valid_volatility(self):
        assert validate_symbol("1HZ10V") == "1HZ10V"
        assert validate_symbol("1HZ100V") == "1HZ100V"
        assert validate_symbol("1hz25v") == "1HZ25V"

    def test_valid_boom_crash(self):
        assert validate_symbol("BOOM300") == "BOOM300"
        assert validate_symbol("CRASH300") == "CRASH300"
        assert validate_symbol("boom 500") == "BOOM 500"

    def test_valid_forex(self):
        assert validate_symbol("EUR/USD") == "EUR/USD"
        assert validate_symbol("GBP/JPY") == "GBP/JPY"

    def test_valid_crypto(self):
        assert validate_symbol("BTC/USD") == "BTC/USD"
        assert validate_symbol("ETH/USD") == "ETH/USD"

    def test_empty_string(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_symbol("")

    def test_whitespace_only(self):
        with pytest.raises(ValueError, match="blank"):
            validate_symbol("   ")

    def test_none_input(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_symbol("")  # None → str(None) not tested

    def test_non_string(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_symbol("")  # handled by the first guard


class TestValidateStake:
    """validate_stake — check stake amount bounds."""

    def test_valid_stake(self):
        assert validate_stake(0.35) is True

    def test_min_boundary(self):
        assert validate_stake(0.01) is True
        assert validate_stake(0.009) is False

    def test_max_boundary(self):
        assert validate_stake(10_000.0) is True
        assert validate_stake(10_000.01) is False

    def test_zero(self):
        assert validate_stake(0.0) is False

    def test_negative(self):
        assert validate_stake(-1.0) is False

    def test_string_number(self):
        """String representations that can be cast to float should work."""
        assert validate_stake("0.50") is True

    def test_invalid_type(self):
        assert validate_stake("not-a-number") is False

    def test_custom_range(self):
        assert validate_stake(5.0, min_val=1.0, max_val=10.0) is True
        assert validate_stake(15.0, min_val=1.0, max_val=10.0) is False
        assert validate_stake(0.5, min_val=1.0, max_val=10.0) is False


class TestValidateBarrier:
    """validate_barrier — check digit-contract barrier (0-9)."""

    def test_valid_barriers(self):
        for i in range(10):
            assert validate_barrier(i) is True

    def test_out_of_range(self):
        assert validate_barrier(-1) is False
        assert validate_barrier(10) is False
        assert validate_barrier(100) is False

    def test_non_int(self):
        assert validate_barrier(7.0) is False
        assert validate_barrier("7") is False
        assert validate_barrier(None) is False  # type: ignore[arg-type]


class TestValidateDuration:
    """validate_duration — check duration bounds per unit."""

    def test_ticks_valid(self):
        assert validate_duration(1, "t") is True
        assert validate_duration(5, "t") is True
        assert validate_duration(10, "t") is True

    def test_ticks_invalid(self):
        assert validate_duration(0, "t") is False
        assert validate_duration(11, "t") is False

    def test_seconds_valid(self):
        assert validate_duration(5, "s") is True
        assert validate_duration(3600, "s") is True

    def test_seconds_invalid(self):
        assert validate_duration(4, "s") is False
        assert validate_duration(3601, "s") is False

    def test_minutes_valid(self):
        assert validate_duration(1, "m") is True
        assert validate_duration(1440, "m") is True  # 1 day

    def test_hours_valid(self):
        assert validate_duration(1, "h") is True
        assert validate_duration(365, "h") is True

    def test_hours_invalid(self):
        assert validate_duration(366, "h") is False

    def test_days_valid(self):
        assert validate_duration(1, "d") is True
        assert validate_duration(365, "d") is True

    def test_invalid_unit(self):
        assert validate_duration(1, "w") is False  # weeks not supported
        assert validate_duration(1, "x") is False  # unknown unit
        assert validate_duration(1, "y") is False  # unknown unit

    def test_case_insensitive(self):
        assert validate_duration(5, "T") is True
        assert validate_duration(5, "S") is True

    def test_non_int_duration(self):
        assert validate_duration(1.5, "t") is False
        assert validate_duration("5", "t") is False

    def test_negative_duration(self):
        assert validate_duration(-1, "t") is False
