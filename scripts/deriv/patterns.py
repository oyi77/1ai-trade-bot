#!/usr/bin/env python3
"""
Deriv Pattern Analyzers — All Detection Strategies in One Place
===============================================================

1. MomenPatternAnalyzer — carrier digit (1-4) → 7 pattern (course bot v75)
2. AdjacencyPatternAnalyzer — trigger→target digit adjacency (actuary v4)
3. StreakCountdownAnalyzer — N-digit streak + countdown (GUI v5.9)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .client import DerivTick
from .config import (
    TICK_HISTORY, MAX_PATTERN_LOOKBACK, ANTI_FLOOD_WINDOW, ANTI_FLOOD_MAX,
    TARGET_CARRIERS, MAX_JARING_TICKS, MIN_MOMEN1, MIN_MOMEN2,
    DEFAULT_MIN_THRESHOLD,
)

LOG = logging.getLogger("deriv.patterns")


# ── Momen 1/2 Pattern ──

@dataclass
class MomenAnalysis:
    carrier: int
    momen1_tick: int
    momen2_tick: int
    total_m1: int
    total_m2: int
    confidence: float = 0.0


class MomenPatternAnalyzer:
    """Analyze digit patterns using Momen 1/2 strategy.

    Momen 1: carrier digit (1-4) → 7 on next tick
    Momen 2: carrier digit → 7 within N ticks (jaring)
    """

    def __init__(self, target_carriers: list[int] = None,
                 max_jaring_ticks: int = MAX_JARING_TICKS,
                 analysis_ticks: int = TICK_HISTORY,
                 min_momen1: int = MIN_MOMEN1,
                 min_momen2: int = MIN_MOMEN2):
        self.target_carriers = target_carriers or list(TARGET_CARRIERS)
        self.max_jaring_ticks = max_jaring_ticks
        self.analysis_ticks = analysis_ticks
        self.min_momen1 = min_momen1
        self.min_momen2 = min_momen2

    def analyze(self, ticks: list[DerivTick]) -> Optional[MomenAnalysis]:
        """Run Momen 1/2 analysis on a list of ticks.

        Returns MomenAnalysis with best carrier, or None if no pattern found.
        """
        if not ticks:
            return None

        # Track where digit 7 appears relative to carriers
        carrier_sevens = {c: [] for c in self.target_carriers}
        momen1_list = []
        momen2_list = []

        for idx, tick in enumerate(ticks[:self.analysis_ticks]):
            digit = tick.digit
            digit = digit if 0 <= digit <= 9 else int(str(tick.price)[-1])

            # Momen 2: count ticks since last carrier appearance
            for c in self.target_carriers:
                if idx > 0:
                    prev_digit = ticks[idx - 1].digit
                    if prev_digit in self.target_carriers:
                        pass  # will be checked in next tick

            # Scan for carrier → 7 patterns
            if idx + 1 < len(ticks):
                curr_digit = ticks[idx].digit
                next_digit = ticks[idx + 1].digit

                # Momen 1: carrier → 7 immediately next tick
                if curr_digit in self.target_carriers and next_digit == 7:
                    momen1_list.append((curr_digit, idx))
                    carrier_sevens[curr_digit].append(idx + 1)

                # Momen 2: carrier → 7 within jaring window
                for j in range(2, self.max_jaring_ticks + 1):
                    if idx + j < len(ticks):
                        if curr_digit in self.target_carriers and ticks[idx + j].digit == 7:
                            if (curr_digit, idx, j) not in momen2_list:
                                momen2_list.append((curr_digit, idx, j))

        # Find best carrier (earliest Momen 1 that also has Momen 2)
        best_carrier = None
        best_m1_tick = None
        best_m2_tick = None

        for carrier in self.target_carriers:
            m1 = [(c, t) for c, t in momen1_list if c == carrier]
            m2 = [(c, s, j) for c, s, j in momen2_list if c == carrier]
            if len(m1) >= self.min_momen1 and len(m2) >= self.min_momen2:
                if best_m1_tick is None or m1[0][1] < best_m1_tick:
                    best_m1_tick = m1[0][1]
                    best_m2_tick = m2[0][1]
                    best_carrier = carrier

        if best_carrier is not None:
            total_m1 = len([t for c, t in momen1_list if c == best_carrier])
            total_m2 = len([s for c, s, j in momen2_list if c == best_carrier])
            confidence = min(1.0, (total_m1 + total_m2) / 6.0)
            return MomenAnalysis(
                carrier=best_carrier,
                momen1_tick=best_m1_tick,
                momen2_tick=best_m2_tick,
                total_m1=total_m1,
                total_m2=total_m2,
                confidence=confidence,
            )
        return None


# ── Adjacency Pattern (Actuary v4) ──

@dataclass
class AdjacencyAnalysis:
    trigger: int       # trigger digit
    target: int        # target digit
    freq: int          # how many times trigger→target seen
    total_adjacencies: int
    trigger_count: int
    anti_flood_ok: bool


class AdjacencyPatternAnalyzer:
    """Adjacency pair pattern detection (trigger→target digit).

    Strategy (from Project Arbiter v4):
    - Track consecutive digit pairs in last N ticks
    - Find most frequent trigger→target pattern
    - Lock trigger + target when freq >= threshold
    - Anti-flood: skip if target overrepresented in recent ticks
    """

    def __init__(self, lookback: int = TICK_HISTORY,
                 min_threshold: int = DEFAULT_MIN_THRESHOLD,
                 anti_flood_window: int = ANTI_FLOOD_WINDOW,
                 anti_flood_max: int = ANTI_FLOOD_MAX):
        self.lookback = lookback
        self.min_threshold = min_threshold
        self.anti_flood_window = anti_flood_window
        self.anti_flood_max = anti_flood_max

    def analyze(self, ticks: list[DerivTick]) -> Optional[AdjacencyAnalysis]:
        """Find most frequent trigger→target adjacency pattern."""
        if len(ticks) < 2:
            return None

        ticks = ticks[-self.lookback:]
        adjacency_pairs: dict[tuple[int, int], int] = {}
        trigger_counts: dict[int, int] = {}
        recent_digits = [t.digit for t in ticks[-self.anti_flood_window:]]

        # Count adjacencies
        for i in range(len(ticks) - 1):
            trigger = ticks[i].digit
            target = ticks[i + 1].digit
            key = (trigger, target)
            adjacency_pairs[key] = adjacency_pairs.get(key, 0) + 1
            trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1

        if not adjacency_pairs:
            return None

        # Find best pattern meeting threshold
        best = None
        best_freq = 0

        for (trigger, target), freq in sorted(adjacency_pairs.items(),
                                               key=lambda x: -x[1]):
            if freq < self.min_threshold:
                continue

            # Anti-flood: skip if target overrepresented
            target_in_window = recent_digits.count(target)
            anti_flood_ok = target_in_window <= self.anti_flood_max

            if freq > best_freq:
                best = AdjacencyAnalysis(
                    trigger=trigger,
                    target=target,
                    freq=freq,
                    total_adjacencies=sum(adjacency_pairs.values()),
                    trigger_count=trigger_counts.get(trigger, 0),
                    anti_flood_ok=anti_flood_ok,
                )
                best_freq = freq

        return best


# ── Streak + Countdown Pattern (GUI v5.9) ──

@dataclass
class StreakAnalysis:
    trigger_digit: int
    streak_length: int
    required_streak: int
    comparison: str         # >, <, ==
    op_tick_countdown: int
    tick_to_fire: int       # absolute tick index to fire
    confidence: float


class StreakCountdownAnalyzer:
    """Streak-based trigger + countdown pattern.

    Strategy (from GUI V5.9 ANALISA TRADE DO):
    1. Wait for N consecutive digits matching a comparison (>, <, ==)
    2. Start countdown to OP tick
    3. Trade on the countdown tick
    """

    def __init__(self, required_streak: int = 3, comparison: str = ">",
                 trigger_value: int = 5, op_tick_countdown: int = 1,
                 analysis_ticks: int = TICK_HISTORY):
        assert comparison in (">", "<", "=="), "comparison must be >, <, or =="
        self.required_streak = required_streak
        self.comparison = comparison
        self.trigger_value = trigger_value
        self.op_tick_countdown = op_tick_countdown
        self.analysis_ticks = analysis_ticks

    def _check_digit(self, digit: int, trigger: int) -> bool:
        if self.comparison == ">":
            return digit > trigger
        elif self.comparison == "<":
            return digit < trigger
        else:
            return digit == trigger

    def analyze(self, ticks: list[DerivTick]) -> Optional[StreakAnalysis]:
        """Find streak pattern and determine fire tick."""
        ticks = ticks[-self.analysis_ticks:]
        current_streak = 0
        best_streak = 0
        best_trigger = None
        best_tick = None

        for idx, tick in enumerate(ticks):
            digit = tick.digit
            if self._check_digit(digit, self.trigger_value):
                current_streak += 1
                if current_streak >= self.required_streak:
                    fire_tick = idx + self.op_tick_countdown
                    if fire_tick < len(ticks):
                        return StreakAnalysis(
                            trigger_digit=self.trigger_value,
                            streak_length=current_streak,
                            required_streak=self.required_streak,
                            comparison=self.comparison,
                            op_tick_countdown=self.op_tick_countdown,
                            tick_to_fire=fire_tick,
                            confidence=min(1.0, current_streak / (self.required_streak * 2)),
                        )
            else:
                if current_streak > best_streak:
                    best_streak = current_streak
                    best_trigger = digit
                    best_tick = idx
                current_streak = 0

        return None
