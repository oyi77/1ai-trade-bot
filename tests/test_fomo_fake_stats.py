"""Tests for the FOMO Fake Stats Engine.

Tests cover determinism, monotonicity, formatting, and message generation.
No mocks for deterministic logic — only datetime is patched to control time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from tradebot.services.fomo_fake_stats import (
    _calc_days_since_epoch,
    _rng,
    _rng_daily,
    format_idr,
    get_all_fomo_messages,
    get_base_keys,
    get_base_users,
    get_daily_seed,
    get_fake_claim,
    get_fake_robot_users,
    get_fake_tp,
    get_fomo_claim_message,
    get_fomo_robot_message,
    get_fomo_tp_message,
)

# ---------------------------------------------------------------------------
#  Deterministic seed helpers
# ---------------------------------------------------------------------------


class TestDailySeed:
    def test_returns_int(self):
        seed = get_daily_seed()
        assert isinstance(seed, int)

    def test_encodes_year_month_day_hour(self):
        """Seed encodes YYYYMMDDHH so same hour → same seed."""
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            seed = get_daily_seed()
            # 2026*10_000_000 + 6*100_000 + 13*100 + 14 = 20_260_601_314
            assert seed == 20260601314

    def test_different_hour_different_seed(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            seed_a = get_daily_seed()
            mock_dt.now.return_value = datetime(2026, 6, 13, 15, 0, 0, tzinfo=UTC)
            seed_b = get_daily_seed()
            assert seed_a != seed_b


# ---------------------------------------------------------------------------
#  _calc_days_since_epoch
# ---------------------------------------------------------------------------


class TestCalcDaysSinceEpoch:
    def test_returns_non_negative_int(self):
        days = _calc_days_since_epoch()
        assert isinstance(days, int)
        assert days >= 0

    def test_zero_at_epoch(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _calc_days_since_epoch() == 0

    def test_increases_with_time(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 3, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _calc_days_since_epoch() == 2


# ---------------------------------------------------------------------------
#  _rng / _rng_daily determinism
# ---------------------------------------------------------------------------


class TestRng:
    def test_same_hour_same_values(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            r1 = _rng()
            r2 = _rng()
            assert r1.random() == r2.random()

    def test_different_hour_different_values(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            r1 = _rng()
            mock_dt.now.return_value = datetime(2026, 6, 13, 15, 0, 0, tzinfo=UTC)
            r2 = _rng()
            assert r1.random() != r2.random()


class TestRngDaily:
    def test_same_day_same_values(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            r1 = _rng_daily()
            mock_dt.now.return_value = datetime(2026, 6, 13, 23, 0, 0, tzinfo=UTC)
            r2 = _rng_daily()
            assert r1.random() == r2.random()

    def test_different_day_different_values(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            r1 = _rng_daily()
            mock_dt.now.return_value = datetime(2026, 6, 14, 14, 0, 0, tzinfo=UTC)
            r2 = _rng_daily()
            assert r1.random() != r2.random()


# ---------------------------------------------------------------------------
#  Monotonically increasing base counters
# ---------------------------------------------------------------------------


class TestBaseUsers:
    def test_returns_int(self):
        val = get_base_users()
        assert isinstance(val, int)
        assert val >= 50

    def test_monotonically_increasing(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            v0 = get_base_users()

            mock_dt.now.return_value = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)
            v1 = get_base_users()

            mock_dt.now.return_value = datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC)
            v2 = get_base_users()

            assert v0 <= v1 <= v2
            # Over enough days it should actually increase
            assert v2 > v0


class TestBaseKeys:
    def test_returns_int(self):
        val = get_base_keys()
        assert isinstance(val, int)
        assert val >= 75

    def test_monotonically_increasing(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            v0 = get_base_keys()

            mock_dt.now.return_value = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)
            v1 = get_base_keys()

            mock_dt.now.return_value = datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC)
            v2 = get_base_keys()

            assert v0 <= v1 <= v2
            assert v2 > v0


# ---------------------------------------------------------------------------
#  get_fake_claim
# ---------------------------------------------------------------------------


class TestFakeClaim:
    def test_returns_dict_with_expected_keys(self):
        claim = get_fake_claim()
        assert isinstance(claim, dict)
        assert set(claim.keys()) == {"username", "amount", "amount_formatted"}

    def test_username_is_censored(self):
        """Username ends with *****."""
        claim = get_fake_claim()
        assert claim["username"].endswith("*****")

    def test_amount_is_int(self):
        claim = get_fake_claim()
        assert isinstance(claim["amount"], int)

    def test_amount_in_range(self):
        claim = get_fake_claim()
        assert 5_000_000 <= claim["amount"] <= 50_000_000
    def test_amount_formatted_is_string(self):
        claim = get_fake_claim()
        assert isinstance(claim["amount_formatted"], str)
        assert claim["amount_formatted"].startswith("Rp")

    def test_amount_monotonically_increasing(self):
        """Amount trend is upward once past the 5M floor (~day 80)."""
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            v0 = get_fake_claim()["amount"]

            mock_dt.now.return_value = datetime(2026, 4, 2, 0, 0, 0, tzinfo=UTC)  # day 91, past floor
            v91 = get_fake_claim()["amount"]

            mock_dt.now.return_value = datetime(2026, 4, 12, 0, 0, 0, tzinfo=UTC)  # day 101
            v101 = get_fake_claim()["amount"]

            assert v0 <= v91 <= v101
            assert v101 > v0

    def test_amount_formatted_matches_amount(self):
        claim = get_fake_claim()
        assert claim["amount_formatted"] == format_idr(claim["amount"])

# ---------------------------------------------------------------------------
#  get_fake_tp
# ---------------------------------------------------------------------------


class TestFakeTp:
    def test_returns_dict_with_expected_keys(self):
        tp = get_fake_tp()
        assert isinstance(tp, dict)
        assert set(tp.keys()) == {"username", "symbol", "profit", "lots"}

    def test_username_is_censored(self):
        tp = get_fake_tp()
        assert tp["username"].endswith("*****")

    def test_symbol_is_valid(self):
        tp = get_fake_tp()
        assert tp["symbol"] in ("XAUUSD", "BTCUSD", "EURUSD")

    def test_profit_is_float(self):
        tp = get_fake_tp()
        assert isinstance(tp["profit"], float)

    def test_profit_capped_at_1000(self):
        tp = get_fake_tp()
        assert tp["profit"] <= 1000.0

    def test_lots_is_float(self):
        tp = get_fake_tp()
        assert isinstance(tp["lots"], float)

    def test_lots_in_range(self):
        tp = get_fake_tp()
        assert 0.1 <= tp["lots"] <= 2.0

    def test_profit_monotonically_increasing(self):
        """Profit trends upward over large timescales where growth dominates jitter."""
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            v0 = get_fake_tp()["profit"]

            mock_dt.now.return_value = datetime(2026, 4, 11, 0, 0, 0, tzinfo=UTC)  # day 100
            v100 = get_fake_tp()["profit"]

            mock_dt.now.return_value = datetime(2026, 7, 20, 0, 0, 0, tzinfo=UTC)  # day 200
            v200 = get_fake_tp()["profit"]

            assert v0 <= v100 <= v200
            assert v100 > v0


# ---------------------------------------------------------------------------
#  get_fake_robot_users
# ---------------------------------------------------------------------------


class TestFakeRobotUsers:
    def test_returns_dict_with_expected_keys(self):
        stats = get_fake_robot_users()
        assert isinstance(stats, dict)
        assert set(stats.keys()) == {
            "users",
            "keys",
            "total_profit",
            "total_profit_formatted",
        }

    def test_users_is_int(self):
        stats = get_fake_robot_users()
        assert isinstance(stats["users"], int)

    def test_keys_is_int(self):
        stats = get_fake_robot_users()
        assert isinstance(stats["keys"], int)

    def test_total_profit_is_int(self):
        stats = get_fake_robot_users()
        assert isinstance(stats["total_profit"], int)

    def test_total_profit_formatted_matches(self):
        stats = get_fake_robot_users()
        assert stats["total_profit_formatted"] == format_idr(stats["total_profit"])

    def test_total_profit_monotonically_increasing(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            v0 = get_fake_robot_users()["total_profit"]

            mock_dt.now.return_value = datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)
            v1 = get_fake_robot_users()["total_profit"]

            mock_dt.now.return_value = datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC)
            v2 = get_fake_robot_users()["total_profit"]

            assert v0 <= v1 <= v2
            assert v2 > v0


# ---------------------------------------------------------------------------
#  format_idr
# ---------------------------------------------------------------------------


class TestFormatIdr:
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            (0, "Rp0"),
            (1, "Rp1"),
            (1000, "Rp1.000"),
            (23508308, "Rp23.508.308"),
            (1_000_000_000, "Rp1.000.000.000"),
            (500_000_000, "Rp500.000.000"),
            (999, "Rp999"),
            (10_000, "Rp10.000"),
        ],
    )
    def test_formats_correctly(self, input_val, expected):
        assert format_idr(input_val) == expected


# ---------------------------------------------------------------------------
#  FOMO message builders
# ---------------------------------------------------------------------------


class TestFomoClaimMessage:
    def test_returns_non_empty_string(self):
        msg = get_fomo_claim_message()
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_contains_keywords(self):
        msg = get_fomo_claim_message()
        assert "penarikan" in msg or "komisi" in msg
        assert "Rp" in msg


class TestFomoTpMessage:
    def test_returns_non_empty_string(self):
        msg = get_fomo_tp_message()
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_contains_keywords(self):
        msg = get_fomo_tp_message()
        assert "Take Profit" in msg
        assert "$" in msg


class TestFomoRobotMessage:
    def test_returns_non_empty_string(self):
        msg = get_fomo_robot_message()
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_contains_keywords(self):
        msg = get_fomo_robot_message()
        assert "trader" in msg
        assert "EA" in msg
        assert "Rp" in msg


# ---------------------------------------------------------------------------
#  get_all_fomo_messages
# ---------------------------------------------------------------------------


class TestAllFomoMessages:
    def test_returns_3_messages(self):
        msgs = get_all_fomo_messages()
        assert isinstance(msgs, list)
        assert len(msgs) == 3

    def test_all_are_non_empty_strings(self):
        msgs = get_all_fomo_messages()
        for msg in msgs:
            assert isinstance(msg, str)
            assert len(msg) > 0

    def test_contains_one_of_each_type(self):
        """The 3 messages should contain claim, TP, and robot content."""
        msgs = get_all_fomo_messages()
        combined = " ".join(msgs)
        assert "penarikan" in combined or "komisi" in combined
        assert "Take Profit" in combined
        assert "EA" in combined


# ---------------------------------------------------------------------------
#  Determinism: same hour = same values
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_hour_same_claim(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            a = get_fake_claim()
            b = get_fake_claim()
            assert a == b

    def test_same_hour_same_tp(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            a = get_fake_tp()
            b = get_fake_tp()
            assert a == b

    def test_same_hour_same_robot_users(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            a = get_fake_robot_users()
            b = get_fake_robot_users()
            assert a == b

    def test_same_hour_same_all_messages(self):
        with patch(
            "tradebot.services.fomo_fake_stats.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 13, 14, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            a = get_all_fomo_messages()
            b = get_all_fomo_messages()
            assert a == b
