"""Tests for Deriv pattern analyzers — Momen, Adjacency, Streak.

Imports from tradebot.brokers.deriv (not scripts/deriv).
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradebot.brokers.deriv.client import DerivTick
from tradebot.brokers.deriv.patterns import (
    AdjacencyPatternAnalyzer,
    MomenPatternAnalyzer,
    StreakCountdownAnalyzer,
)


def _make_tick(digit: int) -> DerivTick:
    """Create a tick whose last DECIMAL digit is {digit}."""
    return DerivTick(
        symbol="R_75",
        price=float(f"33000.000{digit}"),
        epoch=1_000_000 + digit,
        timestamp=datetime.now(UTC),
    )


def tick_sequence(digits: list[int]) -> list[DerivTick]:
    """Create ordered sequence of DerivTick from digit list."""
    ticks: list[DerivTick] = []
    for i, d in enumerate(digits):
        t = _make_tick(d)
        object.__setattr__(t, "epoch", 1_000_000 + i)
        ticks.append(t)
    return ticks


# ── Momen Tests ──


class TestMomenPatternAnalyzer:
    """Momen 1/2 pattern detection."""

    def test_detects_7_from_carrier_3(self):
        """Momen1: carrier=3 should predict digit 7."""
        ticks = []
        for _ in range(40):
            ticks.append(_make_tick(3))  # carrier
            ticks.append(_make_tick(7))  # target
        analyzer = MomenPatternAnalyzer(target_carriers=[3])
        result = analyzer.analyze(ticks)
        assert result is not None, "Momen should detect carrier=3"
        assert result.carrier == 3, f"Expected carrier=3, got {result.carrier}"
        assert result.confidence > 0, "Expected positive confidence"
        assert result.predicted_digit == 7

    def test_no_trigger_with_few_ticks(self):
        """<10 ticks should not trigger."""
        ticks = [_make_tick(i % 10) for i in range(5)]
        analyzer = MomenPatternAnalyzer(target_carriers=[3])
        result = analyzer.analyze(ticks)
        assert result is None, "Should return None with <10 ticks"

    def test_handles_all_zeros(self):
        """All zeros (no carrier pattern) should return None."""
        ticks = [_make_tick(0) for _ in range(110)]
        analyzer = MomenPatternAnalyzer(target_carriers=[3])
        result = analyzer.analyze(ticks)
        assert result is None or result.confidence == 0

    def test_multiple_carriers(self):
        """Test with multiple target carriers."""
        ticks = []
        for _ in range(30):
            ticks.append(_make_tick(2))
            ticks.append(_make_tick(7))
        analyzer = MomenPatternAnalyzer(target_carriers=[1, 2, 3, 4])
        result = analyzer.analyze(ticks)
        assert result is not None
        assert result.predicted_digit == 7

    def test_confidence_increases_with_frequency(self):
        """More carrier→7 occurrences should increase confidence."""
        # Low frequency
        ticks_low = tick_sequence([0, 1, 2, 3, 4, 5, 6, 7, 8, 9] * 10)
        # High frequency (3→7 repeated)
        ticks_high = []
        for _ in range(50):
            ticks_high.append(_make_tick(3))
            ticks_high.append(_make_tick(7))

        analyzer = MomenPatternAnalyzer(target_carriers=[3])
        result_low = analyzer.analyze(ticks_low)
        result_high = analyzer.analyze(ticks_high)

        if result_low and result_high:
            assert result_high.confidence >= result_low.confidence


# ── Adjacency Tests ──


class TestAdjacencyPatternAnalyzer:
    """Trigger→target digit adjacency mapping."""

    def test_basic_trigger_target(self):
        """Alternating 0→5 pattern should detect trigger=0, target=5."""
        ticks = []
        for _ in range(30):
            ticks.append(_make_tick(0))
            ticks.append(_make_tick(5))
        analyzer = AdjacencyPatternAnalyzer(lookback=100, min_threshold=3)
        result = analyzer.analyze(ticks)
        assert result is not None, "Adjacency should detect pattern"
        assert result.trigger == 0, f"Expected trigger=0, got {result.trigger}"
        assert result.target == 5, f"Expected target=5, got {result.target}"

    def test_no_trigger_without_min_threshold(self):
        """Need min_threshold triggers before detection."""
        ticks = [_make_tick(1), _make_tick(2)]
        analyzer = AdjacencyPatternAnalyzer(lookback=100, min_threshold=3)
        result = analyzer.analyze(ticks)
        assert result is None

    def test_different_trigger_target(self):
        """3→9 pattern."""
        ticks = []
        for _ in range(20):
            ticks.append(_make_tick(3))
            ticks.append(_make_tick(9))
        analyzer = AdjacencyPatternAnalyzer(lookback=100, min_threshold=3)
        result = analyzer.analyze(ticks)
        assert result is not None
        assert result.trigger == 3
        assert result.target == 9


# ── Streak Tests ──


class TestStreakCountdownAnalyzer:
    """Consecutive digit streak detection."""

    def test_detects_consecutive(self):
        """3+ consecutive digits >5 should trigger."""
        ticks = [_make_tick(7) for _ in range(10)]  # 10 consecutive 7s
        analyzer = StreakCountdownAnalyzer(
            required_streak=3, comparison=">", trigger_value=5
        )
        result = analyzer.analyze(ticks)
        assert result is not None, "Should detect streak"
        assert result.streak_length >= 3

    def test_no_false_positive(self):
        """Alternating digits should NOT trigger."""
        ticks = tick_sequence([1, 2, 1, 2, 1, 2, 1, 2, 1, 2])
        analyzer = StreakCountdownAnalyzer(required_streak=3)
        result = analyzer.analyze(ticks)
        assert result is None, "Alternating should not trigger"

    def test_below_required_streak(self):
        """Less than required_streak should not trigger."""
        ticks = [_make_tick(7) for _ in range(2)]
        analyzer = StreakCountdownAnalyzer(required_streak=3)
        result = analyzer.analyze(ticks)
        assert result is None

    def test_equal_comparison(self):
        """Streak with == comparison."""
        ticks = [_make_tick(0) for _ in range(5)]
        analyzer = StreakCountdownAnalyzer(
            required_streak=3, comparison="==", trigger_value=0
        )
        result = analyzer.analyze(ticks)
        assert result is not None
