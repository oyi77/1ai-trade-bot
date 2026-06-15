"""
Tests for tradebot.engines.harmonic — XABCD Harmonic Pattern Detection Engine.

Covers: pivot detection, Fibonacci validation, AHZ generation, risk management,
mock data demo, and Engine ABC compliance.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tradebot.engines.harmonic import (
    Bias,
    FibRule,
    HarmonicEngine,
    PatternMatch,
    PatternType,
    XABCDPoints,
    Pivot,
    _any_matches,
    _calculate_confidence,
    _calculate_prz,
    _calculate_sl,
    _calculate_tp,
    _extension,
    _retracement,
    detect_pivots,
    find_xabcd,
    validate_pattern,
    PATTERN_RULES,
)


# ── Pivot Detection ────────────────────────────────────────────────────


class TestPivotDetection:
    def test_returns_empty_for_short_data(self):
        bars = [{"high": 100, "low": 90, "open": 95, "close": 95}] * 3
        assert detect_pivots(bars, fractal_period=5) == []

    def test_detects_swing_high(self):
        # Peak at index 15 with fractal_period=3: must be max in [12, 18]
        bars = []
        for i in range(22):
            if i < 15:
                bars.append({"high": 100 + i, "low": 98 + i, "open": 99 + i, "close": 99 + i})
            elif i == 15:
                bars.append({"high": 130, "low": 128, "open": 129, "close": 129})
            else:
                bars.append({"high": 130 - (i - 15), "low": 128 - (i - 15), "open": 129 - (i - 15), "close": 129 - (i - 15)})

        pivots = detect_pivots(bars, fractal_period=3)
        highs = [p for p in pivots if p.is_high]
        assert len(highs) >= 1
        assert any(p.price == 130 for p in highs)

    def test_detects_swing_low(self):
        # Valley at index 15
        bars = []
        for i in range(22):
            if i < 15:
                bars.append({"high": 130 - i, "low": 128 - i, "open": 129 - i, "close": 129 - i})
            elif i == 15:
                bars.append({"high": 100, "low": 98, "open": 99, "close": 99})
            else:
                bars.append({"high": 100 + (i - 15), "low": 98 + (i - 15), "open": 99 + (i - 15), "close": 99 + (i - 15)})

        pivots = detect_pivots(bars, fractal_period=3)
        lows = [p for p in pivots if not p.is_high]
        assert len(lows) >= 1
        assert any(p.price == 98 for p in lows)

    def test_sorted_by_index(self):
        bars = [{"high": 100 + (i % 5) * 2, "low": 90 + (i % 3) * 2, "open": 95, "close": 95}
                for i in range(25)]
        pivots = detect_pivots(bars, fractal_period=3)
        indices = [p.index for p in pivots]
        assert indices == sorted(indices)


# ── Fibonacci Utilities ────────────────────────────────────────────────


class TestFibonacciMath:
    def test_retracement_exact(self):
        # XA leg: 100 → 150 (50 point move)
        # B at 130.9: (130.9 - 150) / (150 - 100) = -19.1/50 = 0.382
        assert abs(_retracement(100, 150, 130.9) - 0.382) < 0.001

    def test_retracement_618(self):
        # 150 - 50 * 0.618 = 119.1
        assert abs(_retracement(100, 150, 119.1) - 0.618) < 0.001

    def test_retracement_zero_denominator(self):
        assert _retracement(100, 100, 50) == 0.0

    def test_extension_beyond(self):
        # XA: 100→150, D at 194.3: (194.3-150)/50 = 0.886
        assert abs(_extension(100, 150, 194.3) - 0.886) < 0.001

    def test_extension_zero_denominator(self):
        assert _extension(100, 100, 150) == 0.0

    def test_fib_rule_matches_within_tolerance(self):
        rule = FibRule(target=0.618, tolerance=0.05)
        assert rule.matches(0.618)
        assert rule.matches(0.58)
        assert rule.matches(0.65)
        assert not rule.matches(0.70)

    def test_fib_rule_exact_match(self):
        rule = FibRule(target=0.382, tolerance=0.0)
        assert rule.matches(0.382)
        assert not rule.matches(0.383)

    def test_any_matches(self):
        rules = [FibRule(0.382, 0.05), FibRule(0.50, 0.05)]
        assert _any_matches(0.40, rules)
        assert _any_matches(0.52, rules)
        assert not _any_matches(0.60, rules)


# ── Pattern Validation ─────────────────────────────────────────────────


class TestPatternValidation:
    def _make_bullish_gartley(self) -> XABCDPoints:
        """
        Bullish Gartley with exact Fibonacci ratios:
        X=1900(low), A=2000(high), B=1938.2(low), C=1993.0(high), D=1921.4(low)
        ab_retrace=0.618, bc_retrace≈0.886, xd_ext=0.786
        """
        return XABCDPoints(
            x=Pivot(index=0, price=1900.0, is_high=False),
            a=Pivot(index=20, price=2000.0, is_high=True),
            b=Pivot(index=35, price=1938.2, is_high=False),
            c=Pivot(index=45, price=1993.0, is_high=True),
            d=Pivot(index=55, price=1921.4, is_high=False),
        )

    def _make_bearish_bat(self) -> XABCDPoints:
        """
        Bearish Bat with exact Fibonacci ratios:
        X=2000(high), A=1900(low), B=1950(high), C=1930.9(low), D=1988.6(high)
        ab_retrace=0.50, bc_retrace=0.382, xd_ext=0.886
        """
        return XABCDPoints(
            x=Pivot(index=0, price=2000.0, is_high=True),
            a=Pivot(index=20, price=1900.0, is_high=False),
            b=Pivot(index=35, price=1950.0, is_high=True),
            c=Pivot(index=45, price=1930.9, is_high=False),
            d=Pivot(index=55, price=1988.6, is_high=True),
        )

    def test_bullish_gartley_detection(self):
        points = self._make_bullish_gartley()
        match = validate_pattern(points, PatternType.GARTLEY)
        assert match is not None
        assert match.bias == Bias.BULLISH
        assert match.pattern == PatternType.GARTLEY
        assert match.confidence > 0.5
        assert match.sl < points.d.price  # SL below D for bullish
        assert match.tp1 > points.d.price
        assert match.tp2 > match.tp1

    def test_bearish_bat_detection(self):
        points = self._make_bearish_bat()
        match = validate_pattern(points, PatternType.BAT)
        assert match is not None
        assert match.bias == Bias.BEARISH
        assert match.pattern == PatternType.BAT
        assert match.sl > points.d.price  # SL above D for bearish
        assert match.tp1 < points.d.price
        assert match.tp2 < match.tp1

    def test_invalid_pattern_returns_none(self):
        # Random points that don't match any Fibonacci ratios
        points = XABCDPoints(
            x=Pivot(index=0, price=100.0, is_high=False),
            a=Pivot(index=20, price=200.0, is_high=True),
            b=Pivot(index=35, price=110.0, is_high=False),  # 90% retrace — no pattern matches
            c=Pivot(index=45, price=190.0, is_high=True),
            d=Pivot(index=55, price=120.0, is_high=False),
        )
        for ptype in PatternType:
            assert validate_pattern(points, ptype) is None

    def test_same_type_xa_returns_none(self):
        # X and A both highs — not an impulse
        points = XABCDPoints(
            x=Pivot(index=0, price=100.0, is_high=True),
            a=Pivot(index=20, price=110.0, is_high=True),
            b=Pivot(index=35, price=105.0, is_high=False),
            c=Pivot(index=45, price=108.0, is_high=True),
            d=Pivot(index=55, price=102.0, is_high=False),
        )
        assert validate_pattern(points, PatternType.GARTLEY) is None

    def test_ahz_zone_is_narrow(self):
        points = self._make_bullish_gartley()
        match = validate_pattern(points, PatternType.GARTLEY)
        assert match is not None
        # AHZ should be a tight zone around D
        assert match.ahz_upper > match.ahz_lower
        assert (match.ahz_upper - match.ahz_lower) < abs(points.a.price - points.x.price) * 0.02

    def test_pattern_match_to_dict(self):
        points = self._make_bullish_gartley()
        match = validate_pattern(points, PatternType.GARTLEY)
        assert match is not None
        d = match.to_dict()
        assert d["pattern"] == "gartley"
        assert d["bias"] == "bullish"
        assert "prz" in d
        assert "sl" in d
        assert "tp1" in d
        assert "tp2" in d
        assert "points" in d
        assert "ratios" in d


# ── Risk Management ────────────────────────────────────────────────────


class TestRiskManagement:
    def test_bullish_sl_below_prz(self):
        ahz_upper, ahz_lower = 1911.0, 1910.0
        sl = _calculate_sl(ahz_upper, ahz_lower, Bias.BULLISH, 50.0)
        assert sl < ahz_lower

    def test_bearish_sl_above_prz(self):
        ahz_upper, ahz_lower = 1989.0, 1988.0
        sl = _calculate_sl(ahz_upper, ahz_lower, Bias.BEARISH, 100.0)
        assert sl > ahz_upper

    def test_bullish_tp_direction(self):
        tp1, tp2 = _calculate_tp(1900.0, 1921.4, Bias.BULLISH, 78.6)
        assert tp1 > 1921.4
        assert tp2 > tp1

    def test_bearish_tp_direction(self):
        tp1, tp2 = _calculate_tp(2000.0, 1988.6, Bias.BEARISH, 100.0)
        assert tp1 < 1988.6
        assert tp2 < tp1

    def test_tp1_is_382(self):
        # AD range = 50, buy at D=1910.7
        tp1, _ = _calculate_tp(1900.0, 1910.7, Bias.BULLISH, 10.7)
        expected = 1910.7 + 10.7 * 0.382
        assert abs(tp1 - expected) < 0.01

    def test_tp2_is_618(self):
        _, tp2 = _calculate_tp(1900.0, 1910.7, Bias.BULLISH, 10.7)
        expected = 1910.7 + 10.7 * 0.618
        assert abs(tp2 - expected) < 0.01


# ── Confidence Scoring ─────────────────────────────────────────────────


class TestConfidence:
    def test_perfect_ratios_high_confidence(self):
        ratios = {"ab_retrace": 0.618, "bc_retrace": 0.382, "cd_ext": 1.272, "xd_ext": 0.786}
        rules = PATTERN_RULES[PatternType.GARTLEY]
        conf = _calculate_confidence(ratios, rules)
        assert conf > 0.8

    def test_far_ratios_low_confidence(self):
        ratios = {"ab_retrace": 0.90, "bc_retrace": 0.90, "cd_ext": 0.50, "xd_ext": 0.50}
        rules = PATTERN_RULES[PatternType.GARTLEY]
        conf = _calculate_confidence(ratios, rules)
        # Strict legs are far off → low score. Quality legs may add small bonus.
        assert conf < 0.25

    def test_mixed_ratios_medium_confidence(self):
        ratios = {"ab_retrace": 0.618, "bc_retrace": 0.90, "cd_ext": 1.272, "xd_ext": 0.90}
        rules = PATTERN_RULES[PatternType.GARTLEY]
        conf = _calculate_confidence(ratios, rules)
        assert 0.2 < conf < 0.8


# ── XABCD Point Finding ───────────────────────────────────────────────


class TestFindXABCD:
    def test_returns_none_for_few_pivots(self):
        pivots = [Pivot(0, 100, False), Pivot(5, 110, True)]
        assert find_xabcd(pivots) is None

    def test_finds_alternating_pattern(self):
        pivots = [
            Pivot(0, 100, False),   # X low
            Pivot(10, 120, True),   # A high
            Pivot(20, 108, False),  # B low
            Pivot(30, 118, True),   # C high
            Pivot(40, 104, False),  # D low
        ]
        result = find_xabcd(pivots)
        assert result is not None
        assert result.x.price == 100
        assert result.a.price == 120
        assert result.d.price == 104

    def test_rejects_non_alternating(self):
        pivots = [
            Pivot(0, 100, False),
            Pivot(10, 120, False),  # same type as X
            Pivot(20, 108, True),
            Pivot(30, 118, False),
            Pivot(40, 104, True),
        ]
        result = find_xabcd(pivots)
        # Should skip the first 4 since X and A are both lows
        # and try pivots 1-5 if they exist
        # With only 5 pivots and X/A same type, result is None
        assert result is None


# ── Engine Integration ─────────────────────────────────────────────────


class TestHarmonicEngine:
    def test_engine_name(self):
        engine = HarmonicEngine()
        assert engine.name == "harmonic"

    def test_analyze_returns_none_for_empty_ticks(self):
        engine = HarmonicEngine()
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.analyze([]))
        assert result is None

    def test_analyze_returns_none_for_few_ticks(self):
        engine = HarmonicEngine()
        ticks = [MagicMock(symbol="XAUUSD", price=1900.0, epoch=i) for i in range(3)]
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(engine.analyze(ticks))
        assert result is None

    def test_engine_is_abstract_compliant(self):
        from tradebot.engines.base import Engine
        engine = HarmonicEngine()
        assert isinstance(engine, Engine)
        assert hasattr(engine, "analyze")
        assert hasattr(engine, "name")
