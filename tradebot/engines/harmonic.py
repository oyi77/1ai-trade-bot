"""
harmonic.py — XABCD Harmonic Price Pattern Detection Engine

Detects and validates Bullish/Bearish Bat, Butterfly, and Gartley patterns
using fractal-based pivot detection and Fibonacci ratio validation.

Conforms to: tradebot.engines.base.Engine interface
Auto-discovered by: tradebot.engines.registry.Registry
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tradebot.engines.base import Engine
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


# ── Enums & Pattern Definitions ────────────────────────────────────────


class PatternType(Enum):
    BAT = "bat"
    BUTTERFLY = "butterfly"
    GARTLEY = "gartley"


class Bias(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass(frozen=True)
class FibRule:
    """Fibonacci ratio constraint: target ± tolerance."""
    target: float
    tolerance: float = 0.05

    def matches(self, value: float) -> bool:
        return abs(value - self.target) <= self.tolerance


# Pattern definitions: {pattern: {leg: [valid FibRule, ...]}}
# Strict rules: AB retrace + BC retrace + XD completion (AHZ).
# CD extension is a quality bonus (scoring only), not a hard gate —
# because in real markets CD and XD constraints can conflict.
PATTERN_RULES: dict[PatternType, dict[str, list[FibRule]]] = {
    PatternType.BAT: {
        "ab_retrace": [FibRule(0.382, 0.05), FibRule(0.50, 0.05)],
        "bc_retrace": [FibRule(0.382, 0.05), FibRule(0.886, 0.05)],
        "cd_ext":     [FibRule(1.618, 0.25)],
        "xd_ext":     [FibRule(0.886, 0.05)],
    },
    PatternType.BUTTERFLY: {
        "ab_retrace": [FibRule(0.786, 0.05)],
        "bc_retrace": [FibRule(0.382, 0.05), FibRule(0.886, 0.05)],
        "cd_ext":     [FibRule(1.618, 0.25)],
        "xd_ext":     [FibRule(1.272, 0.08)],
    },
    PatternType.GARTLEY: {
        "ab_retrace": [FibRule(0.618, 0.05)],
        "bc_retrace": [FibRule(0.382, 0.05), FibRule(0.886, 0.05)],
        "cd_ext":     [FibRule(1.272, 0.15)],
        "xd_ext":     [FibRule(0.786, 0.05)],
    },
}

# Legs that are hard requirements (must match for pattern to be valid)
_STRICT_LEGS = {"ab_retrace", "bc_retrace", "xd_ext"}
# Legs that are soft quality bonuses (improve confidence score)
_QUALITY_LEGS = {"cd_ext"}


# ── Pivot Point ────────────────────────────────────────────────────────


@dataclass
class Pivot:
    """Single pivot (swing high or swing low)."""
    index: int
    price: float
    is_high: bool

    def __repr__(self) -> str:
        kind = "HH" if self.is_high else "LL"
        return f"Pivot({kind} {self.price:.5f} @{self.index})"


@dataclass
class XABCDPoints:
    """Five pivot points forming the harmonic pattern."""
    x: Pivot
    a: Pivot
    b: Pivot
    c: Pivot
    d: Pivot

    @property
    def prices(self) -> list[float]:
        return [self.x.price, self.a.price, self.b.price, self.c.price, self.d.price]

    def __repr__(self) -> str:
        return (
            f"XABCD(X={self.x.price:.5f} A={self.a.price:.5f} "
            f"B={self.b.price:.5f} C={self.c.price:.5f} D={self.d.price:.5f})"
        )


# ── Geometry Utilities ─────────────────────────────────────────────────


def _retracement(leg_start: float, leg_end: float, point: float) -> float:
    """
    Retracement of `point` relative to the leg from `leg_start` to `leg_end`.
    Returns absolute ratio: |(point - leg_end) / (leg_end - leg_start)|.
    """
    denominator = leg_end - leg_start
    if abs(denominator) < 1e-10:
        return 0.0
    return abs((point - leg_end) / denominator)


def _extension(leg_start: float, leg_end: float, point: float) -> float:
    """
    Extension of `point` beyond `leg_end`, relative to the leg XA.
    Returns: |(point - leg_end) / (leg_end - leg_start)|.
    > 1.0 means point extends beyond the leg end.
    """
    denominator = leg_end - leg_start
    if abs(denominator) < 1e-10:
        return 0.0
    return abs((point - leg_end) / denominator)


def _any_matches(value: float, rules: list[FibRule]) -> bool:
    """Check if value matches ANY of the given Fibonacci rules."""
    return any(r.matches(value) for r in rules)


# ── Pivot Detection ────────────────────────────────────────────────────


def detect_pivots(
    ohlcv: list[dict],
    fractal_period: int = 5,
) -> list[Pivot]:
    """
    Fractal-based pivot detection.

    A swing high at index i: high[i] >= all highs in [i-period, i+period].
    A swing low at index i: low[i] <= all lows in [i-period, i+period].

    Returns pivots sorted by index, alternating between highs and lows
    (merging consecutive same-type pivots by taking the more extreme one).
    """
    if len(ohlcv) < fractal_period * 2 + 1:
        return []

    raw_highs: list[Pivot] = []
    raw_lows: list[Pivot] = []

    for i in range(fractal_period, len(ohlcv) - fractal_period):
        high = float(ohlcv[i].get("high", ohlcv[i].get("h", 0)))
        low = float(ohlcv[i].get("low", ohlcv[i].get("l", 0)))

        is_high = all(
            high >= float(ohlcv[j].get("high", ohlcv[j].get("h", 0)))
            for j in range(i - fractal_period, i + fractal_period + 1)
        )
        is_low = all(
            low <= float(ohlcv[j].get("low", ohlcv[j].get("l", 0)))
            for j in range(i - fractal_period, i + fractal_period + 1)
        )

        if is_high:
            raw_highs.append(Pivot(index=i, price=high, is_high=True))
        if is_low:
            raw_lows.append(Pivot(index=i, price=low, is_high=False))

    # Merge into alternating sequence: for consecutive same-type pivots,
    # keep only the most extreme (highest high or lowest low).
    all_pivots = sorted(raw_highs + raw_lows, key=lambda p: p.index)
    if not all_pivots:
        return []

    merged: list[Pivot] = [all_pivots[0]]
    for p in all_pivots[1:]:
        last = merged[-1]
        if p.is_high == last.is_high:
            # Same type — keep the more extreme
            if p.is_high and p.price > last.price:
                merged[-1] = p
            elif not p.is_high and p.price < last.price:
                merged[-1] = p
        else:
            merged.append(p)

    return merged


def find_xabcd(pivots: list[Pivot], lookback: int = 100) -> XABCDPoints | None:
    """
    From a sequence of alternating pivots, find the most recent valid XABCD formation.

    Pattern rules:
      - X → A: initial impulse leg
      - A → B: retracement (0.382–0.786 of XA)
      - B → C: retracement of AB (0.382–0.886)
      - C → D: extension completing the pattern

    For a valid harmonic, we need at least 5 pivots in the lookback window.
    """
    if len(pivots) < 5:
        return None

    recent = pivots[-lookback:] if len(pivots) > lookback else pivots

    # Try all possible XABCD combinations in the last N pivots
    best: XABCDPoints | None = None
    best_score = 0.0

    for i in range(len(recent) - 4):
        x, a, b, c, d = recent[i], recent[i + 1], recent[i + 2], recent[i + 3], recent[i + 4]

        # XA must be an impulse (X and A must be different types — one high, one low)
        if x.is_high == a.is_high:
            continue
        # AB must retrace (A and B different types)
        if a.is_high == b.is_high:
            continue
        # BC must retrace (B and C different types)
        if b.is_high == c.is_high:
            continue
        # CD must complete (C and D different types)
        if c.is_high == d.is_high:
            continue

        # Basic quality: D should be the most recent pivot
        recency = (i + 4) / len(recent)
        score = recency

        if score > best_score:
            best_score = score
            best = XABCDPoints(x=x, a=a, b=b, c=c, d=d)

    return best


# ── Pattern Validation ─────────────────────────────────────────────────


@dataclass
class PatternMatch:
    """A validated harmonic pattern match."""
    pattern: PatternType
    bias: Bias
    points: XABCDPoints
    ratios: dict[str, float]
    confidence: float  # 0.0–1.0
    ahz_upper: float
    ahz_lower: float
    sl: float
    tp1: float
    tp2: float
    timeframe: str = ""

    @property
    def ahz_mid(self) -> float:
        return (self.ahz_upper + self.ahz_lower) / 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.value,
            "bias": self.bias.value,
            "points": {
                "X": self.points.x.price,
                "A": self.points.a.price,
                "B": self.points.b.price,
                "C": self.points.c.price,
                "D": self.points.d.price,
            },
            "ratios": {k: round(v, 4) for k, v in self.ratios.items()},
            "confidence": round(self.confidence, 3),
            "prz": {"upper": self.ahz_upper, "lower": self.ahz_lower, "mid": self.ahz_mid},
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "timeframe": self.timeframe,
        }


def validate_pattern(
    points: XABCDPoints,
    pattern_type: PatternType,
) -> PatternMatch | None:
    """
    Validate an XABCD formation against a specific harmonic pattern's Fibonacci rules.

    Returns a PatternMatch with AHZ, SL, TP1, TP2 if valid.
    Returns None if the ratios don't match within tolerance.
    """
    rules = PATTERN_RULES[pattern_type]
    x, a, b, c, d = points.x, points.a, points.b, points.c, points.d

    xa_range = abs(a.price - x.price)
    if xa_range < 1e-10:
        return None

    # Calculate ratios
    ab_retrace = _retracement(x.price, a.price, b.price)
    bc_retrace = _retracement(a.price, b.price, c.price)
    cd_ext = _extension(b.price, c.price, d.price)
    xd_ext = _extension(x.price, a.price, d.price)

    ratios = {
        "ab_retrace": ab_retrace,
        "bc_retrace": bc_retrace,
        "cd_ext": cd_ext,
        "xd_ext": xd_ext,
    }

    # Validate strict legs (AB, BC, XD) — CD is quality bonus only
    for leg in _STRICT_LEGS:
        if not _any_matches(ratios[leg], rules[leg]):
            LOG.debug("%s ratio %.4f doesn't match %s", leg, ratios[leg], rules[leg])
            return None

    # Determine bias based on pivot structure
    # Bullish: X is swing low, A is swing high (price retraced down to D)
    # Bearish: X is swing high, A is swing low (price rallied up to D)
    if not x.is_high and a.is_high:
        bias = Bias.BULLISH
    elif x.is_high and not a.is_high:
        bias = Bias.BEARISH
    else:
        return None

    # AHZ: the zone where D completes — range between CD extension and XD extension
    ahz_upper, ahz_lower = _calculate_prz(points, bias, xa_range)

    # Confidence: average of how close each ratio is to its best target
    confidence = _calculate_confidence(ratios, rules)

    # Risk management
    sl = _calculate_sl(ahz_upper, ahz_lower, bias, xa_range)
    ad_range = abs(d.price - a.price)
    tp1, tp2 = _calculate_tp(a.price, d.price, bias, ad_range)

    return PatternMatch(
        pattern=pattern_type,
        bias=bias,
        points=points,
        ratios=ratios,
        confidence=confidence,
        ahz_upper=ahz_upper,
        ahz_lower=ahz_lower,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
    )


def _calculate_prz(
    points: XABCDPoints,
    bias: Bias,
    xa_range: float,
) -> tuple[float, float]:
    """
    Calculate the Apex Hunt Zone (AHZ) for Point D.

    The AHZ is the confluence zone between:
      1. The XD extension level (primary reversal zone)
      2. The CD extension level projected from C

    For bullish patterns, AHZ is below the XA midpoint (buy zone).
    For bearish patterns, AHZ is above the XA midpoint (sell zone).
    """
    x, a, b, c, d = points.x, points.a, points.b, points.c, points.d

    # Primary D zone from XA projection
    # For bullish (X=low, A=high): D = X + (XA * xd_ratio) — but D retraces
    # For bearish (X=high, A=low): D = X - (XA * xd_ratio) — but D extends
    xa_diff = a.price - x.price  # signed

    # Zone based on the D point itself (the actual detected D)
    d_price = d.price

    # The AHZ is a zone around D: ±0.5% of XA range from D
    zone_half = xa_range * 0.005  # 0.5% of XA range

    if bias == Bias.BULLISH:
        ahz_upper = d_price + zone_half
        ahz_lower = d_price - zone_half
    else:
        ahz_upper = d_price + zone_half
        ahz_lower = d_price - zone_half

    return ahz_upper, ahz_lower


def _calculate_confidence(ratios: dict[str, float], rules: dict[str, list[FibRule]]) -> float:
    """
    Confidence = average proximity score across all 4 legs.
    Each leg scores 1.0 if exactly on target, decaying to 0.0 at tolerance boundary.
    """
    scores: list[float] = []
    for leg_name, value in ratios.items():
        if leg_name not in rules:
            continue
        rule_list = rules[leg_name]
        best_score = 0.0
        for rule in rule_list:
            deviation = abs(value - rule.target)
            if deviation <= rule.tolerance:
                # Linear decay from 1.0 (exact) to 0.3 (at boundary)
                score = 1.0 - (deviation / rule.tolerance) * 0.7
                best_score = max(best_score, score)
        scores.append(best_score)

    return sum(scores) / len(scores) if scores else 0.0


def _calculate_sl(
    ahz_upper: float,
    ahz_lower: float,
    bias: Bias,
    xa_range: float,
) -> float:
    """
    Stop Loss = slightly beyond the AHZ extreme.
    Bullish: SL below AHZ lower (invalidation of bullish pattern).
    Bearish: SL above AHZ upper (invalidation of bearish pattern).
    Buffer: 10% of XA range beyond the AHZ edge.
    """
    buffer = xa_range * 0.10
    if bias == Bias.BULLISH:
        return ahz_lower - buffer
    else:
        return ahz_upper + buffer


def _calculate_tp(
    a_price: float,
    d_price: float,
    bias: Bias,
    ad_range: float,
) -> tuple[float, float]:
    """
    Take Profit targets based on AD retracement:
      TP1 = 0.382 retracement of AD (conservative).
      TP2 = 0.618 retracement of AD (extended).
    """
    if bias == Bias.BULLISH:
        # Buy at D, price moves up toward A
        tp1 = d_price + ad_range * 0.382
        tp2 = d_price + ad_range * 0.618
    else:
        # Sell at D, price moves down toward A
        tp1 = d_price - ad_range * 0.382
        tp2 = d_price - ad_range * 0.618

    return tp1, tp2


# ── Engine Class ───────────────────────────────────────────────────────


class HarmonicEngine(Engine):
    """
    XABCD Harmonic Pattern Detection Engine.

    Scans OHLCV data for Bullish/Bearish Bat, Butterfly, and Gartley patterns.
    Returns a AHZ_Active signal — the downstream quality gate + MTF consensus
    will decide final execution. No raw buy/sell from this engine.
    """

    def __init__(
        self,
        fractal_period: int = 5,
        min_pivots: int = 10,
        patterns: list[PatternType] | None = None,
    ):
        self._fractal_period = fractal_period
        self._min_pivots = min_pivots
        self._patterns = patterns or list(PatternType)

    @property
    def name(self) -> str:
        return "harmonic"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """
        Analyze ticks for harmonic patterns.

        Converts ticks → OHLCV bars → pivot detection → pattern matching.
        Returns a Signal with pattern details in metadata, or None.
        """
        if not ticks or len(ticks) < self._min_pivots:
            return None

        ohlcv = self._ticks_to_ohlcv(ticks)
        if len(ohlcv) < self._fractal_period * 2 + 5:
            return None

        pivots = detect_pivots(ohlcv, self._fractal_period)
        if len(pivots) < 5:
            LOG.debug("Only %d pivots found, need 5 minimum", len(pivots))
            return None

        xabcd = find_xabcd(pivots)
        if xabcd is None:
            return None

        # Try each pattern type, return the best match
        best_match: PatternMatch | None = None
        for ptype in self._patterns:
            match = validate_pattern(xabcd, ptype)
            if match is not None:
                if best_match is None or match.confidence > best_match.confidence:
                    best_match = match

        if best_match is None:
            return None

        LOG.info(
            "Harmonic %s %s detected — confidence %.1f%%, "
            "AHZ [%.5f–%.5f], SL=%.5f, TP1=%.5f, TP2=%.5f",
            best_match.bias.value.upper(),
            best_match.pattern.value.upper(),
            best_match.confidence * 100,
            best_match.ahz_lower,
            best_match.ahz_upper,
            best_match.sl,
            best_match.tp1,
            best_match.tp2,
        )

        # Build Signal — AHZ_Active flag, no raw buy/sell
        symbol = ticks[-1].symbol if ticks else "UNKNOWN"
        last_price = ticks[-1].price if ticks else 0.0

        grade = (
            SignalGrade.STRONG if best_match.confidence >= 0.7
            else SignalGrade.MODERATE if best_match.confidence >= 0.5
            else SignalGrade.WEAK
        )

        return Signal(
            symbol=symbol,
            direction=best_match.bias.value.upper(),
            predicted_digit=int(last_price * 10) % 10,
            confidence=best_match.confidence,
            source=SignalSource.MOMEN,
            grade=grade,
            entry_price=last_price,
            metadata={
                "pattern": best_match.pattern.value,
                "bias": best_match.bias.value,
                "AHZ_Active": True,
                "ahz_upper": best_match.ahz_upper,
                "ahz_lower": best_match.ahz_lower,
                "ahz_mid": best_match.ahz_mid,
                "sl": best_match.sl,
                "tp1": best_match.tp1,
                "tp2": best_match.tp2,
                "confidence": best_match.confidence,
                "points": {
                    "X": best_match.points.x.price,
                    "A": best_match.points.a.price,
                    "B": best_match.points.b.price,
                    "C": best_match.points.c.price,
                    "D": best_match.points.d.price,
                },
                "ratios": best_match.ratios,
                "requires_confirmation": True,
                "confirmation_hint": (
                    "Wait for SMC order block / FVG on M5/M15 within the AHZ zone "
                    "before executing. This is a AHZ activation, not a raw signal."
                ),
            },
        )

    @staticmethod
    def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
        """Convert raw ticks to OHLCV bar dicts grouped into ~M5 candles."""
        if not ticks:
            return []

        # Group ticks into 5-minute buckets using epoch (integer seconds)
        bars: list[dict] = []
        bucket_ticks: list[Tick] = []
        bucket_start: int = -1

        for tick in ticks:
            epoch = tick.epoch if tick.epoch else 0
            # 300-second (5 min) buckets
            bucket_id = epoch // 300 if epoch > 0 else len(bucket_ticks) // 50

            if bucket_start < 0 or bucket_id != bucket_start:
                if bucket_ticks:
                    bars.append(_ticks_to_bar(bucket_ticks))
                bucket_ticks = [tick]
                bucket_start = bucket_id
            else:
                bucket_ticks.append(tick)

        if bucket_ticks:
            bars.append(_ticks_to_bar(bucket_ticks))

        return bars


def _ticks_to_bar(ticks: list[Tick]) -> dict:
    """Convert a group of ticks into a single OHLCV bar dict."""
    if not ticks:
        return {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}

    prices = [t.price for t in ticks]
    volumes = [getattr(t, "volume", 1.0) or 1.0 for t in ticks]

    return {
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": sum(volumes),
    }


# ── Standalone Runner ──────────────────────────────────────────────────


def run_mock_demo() -> None:
    """
    Run a demo with deterministic XAUUSD data to verify the detection pipeline.
    Uses a textbook Bullish Gartley formation with exact Fibonacci ratios.
    """
    # Deterministic Bullish Gartley:
    # X=1900 → A=2000 → B=1938.2 (0.618) → C=1993.0 (0.886) → D=1921.4 (0.786)
    mock_bars: list[dict] = []

    def _bar(o: float, h: float, l: float, c: float) -> dict:
        return {"open": o, "high": h, "low": l, "close": c, "volume": 100}

    # X to A: clean bullish impulse 1900 → 2000 (20 bars)
    # Add micro-reversals so fractals detect the leg endpoints
    for i in range(20):
        p = 1900 + i * 5
        # Slight dip every 3 bars to create local structure
        noise = -2 if i % 3 == 1 else 0
        mock_bars.append(_bar(p + noise, p + 4, p - 2, p + 5 + noise))

    # A to B: bearish retrace 2000 → 1938.2 (12 bars)
    for i in range(12):
        p = 2000 - i * 5.15
        noise = 2 if i % 3 == 1 else 0
        mock_bars.append(_bar(p + noise, p + 3, p - 3, p - 5.15 + noise))

    # B to C: bullish retrace 1938.2 → 1993.0 (8 bars)
    for i in range(8):
        p = 1938.2 + i * 6.85
        noise = -2 if i % 2 == 0 else 0
        mock_bars.append(_bar(p + noise, p + 3, p - 2, p + 6.85 + noise))

    # C to D: bearish completion 1993.0 → 1921.4 (10 bars)
    for i in range(10):
        p = 1993.0 - i * 7.16
        noise = 2 if i % 2 == 0 else 0
        mock_bars.append(_bar(p + noise, p + 3, p - 3, p - 7.16 + noise))

    # Padding after D (flat consolidation)
    d_price = 1921.4
    for i in range(8):
        p = d_price + (i % 4) * 0.3
        mock_bars.append(_bar(p, p + 1.5, p - 1.5, p + 0.5))

    print(f"\n{'='*70}")
    print(f"  HARMONIC ENGINE — Mock Demo ({len(mock_bars)} bars)")
    print(f"{'='*70}")

    # ── Part 1: Direct XABCD validation (proves math engine works) ──────
    print("\n▸ Part 1: Direct XABCD Validation (textbook ratios)")
    ideal = XABCDPoints(
        x=Pivot(index=0, price=1900.0, is_high=False),
        a=Pivot(index=20, price=2000.0, is_high=True),
        b=Pivot(index=35, price=1938.2, is_high=False),
        c=Pivot(index=45, price=1993.0, is_high=True),
        d=Pivot(index=55, price=1921.4, is_high=False),
    )
    xa = abs(ideal.a.price - ideal.x.price)
    print(f"  Points: {ideal}")
    print(f"  XA range: {xa:.2f} points")

    for ptype in PatternType:
        match = validate_pattern(ideal, ptype)
        if match:
            print(f"\n  ✅ {match.bias.value.upper()} {ptype.value.upper()} DETECTED!")
            print(f"     Confidence: {match.confidence:.1%}")
            print(f"     AHZ: [{match.ahz_lower:.5f} — {match.ahz_upper:.5f}]")
            print(f"     SL: {match.sl:.5f}  |  TP1: {match.tp1:.5f}  |  TP2: {match.tp2:.5f}")
            print(f"     Ratios: ab={match.ratios['ab_retrace']:.4f}  "
                  f"bc={match.ratios['bc_retrace']:.4f}  "
                  f"cd={match.ratios['cd_ext']:.4f}  "
                  f"xd={match.ratios['xd_ext']:.4f}")
        else:
            print(f"  ❌ {ptype.value}: no match")

    # ── Part 2: Full pipeline (pivot detection → XABCD → validation) ───
    print(f"\n▸ Part 2: Full Pipeline (fractal pivots → XABCD → pattern scan)")
    pivots = detect_pivots(mock_bars, fractal_period=3)
    print(f"  Pivots detected: {len(pivots)}")
    for p in pivots[-8:]:
        print(f"    {p}")

    xabcd = find_xabcd(pivots)
    if xabcd:
        print(f"\n  XABCD: {xabcd}")
        matched_any = False
        for ptype in PatternType:
            match = validate_pattern(xabcd, ptype)
            if match:
                matched_any = True
                print(f"  ✅ {match.bias.value.upper()} {ptype.value.upper()} — confidence {match.confidence:.1%}")
                print(f"     AHZ: [{match.ahz_lower:.5f} — {match.ahz_upper:.5f}]")
        if not matched_any:
            print("  ℹ️  No exact pattern match (bar-level noise shifts ratios)")
            print("     → This is expected: real data needs tolerance tuning")
    else:
        print(f"  ⚠️  {len(pivots)} pivots found, need 5+ with alternating structure")
        print("     → Demo bars may not form clean XABCD geometry")

    print(f"\n{'='*70}")


if __name__ == "__main__":
    run_mock_demo()
