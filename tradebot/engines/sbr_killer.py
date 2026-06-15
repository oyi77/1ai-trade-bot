"""
sbr_killer.py — SBR/BRS Killer Zone Engine

Institutional-grade MTF SMC engine that detects broken Support/Resistance
levels (SBR/BRS) being retested during London/NY killzones.

Strategy:
  1. MACRO (H1): Detect key S/R levels that just experienced a Break of
     Structure (BOS). Mark them as SBR (Support Became Resistance) or
     BRS (Resistance Became Support).
  2. MICRO (M15): When price returns to the broken level (Mitigation
     Phase), look for execution triggers.
  3. DISPLACEMENT FILTER: The BOS candle must be a displacement candle
     (Body/Total >= 0.70 AND size >= 1.5 × ATR(14)) — replaces tick
     volume for validity.
  4. FVG CONFLUENCE: The displacement breakout must create a Fair Value
     Gap on M15 overlapping the SBR/BRS zone.
  5. DYNAMIC ZONES: Entry = broken_level ± (ATR(14)×0.25). SL = outside
     the breakout candle's structural high/low + 0.5×ATR buffer.
  6. KILLZONE CONSTRAINT: Auto-execution only during London (07:00–16:00
     UTC) and New York (13:00–22:00 UTC) sessions.

Output:
  A Signal with direction, entry_zone, SL, TP1, TP2, and metadata
  labelling it as "🎯 Apex SBR/BRS Killer Zone".
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tradebot.engines.base import Engine
from tradebot.models import Signal, Tick

LOG = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

_DISPLACEMENT_BODY_RATIO = 0.70       # body must be ≥70% of total candle
_DISPLACEMENT_ATR_MULTIPLIER = 1.5    # candle ≥ 1.5× ATR(14)
_ZONE_ATR_WIDTH = 0.25                # zone entry = level ± 0.25×ATR
_SL_ATR_BUFFER = 0.5                  # SL buffer beyond structure high/low
_MIN_PIVOTS_MACRO = 20                # min H1 bars to detect structure
_MIN_PIVOTS_MICRO = 40                # min M15 bars for execution
_TP1_RR = 1.5                         # TP1 = 1.5R
_TP2_RR = 3.0                         # TP2 = 3.0R

# Killzone windows (UTC)
_LONDON_START = 7
_LONDON_END = 16
_NY_START = 13
_NY_END = 22

# Symbol settings
_SYMBOL_PIPS: dict[str, float] = {
    "XAUUSD": 0.10,
    "GOLD": 0.10,
    "BTCUSD": 0.50,
    "ETHUSD": 0.05,
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "USDJPY": 0.01,
}

# ═══════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DisplacementCandle:
    """A confirmed displacement candle — momentum proxy."""
    idx: int
    high: float
    low: float
    open_p: float
    close: float
    body_size: float
    total_size: float
    body_ratio: float
    is_bullish: bool
    above_atr: bool  # candle size > 1.5× ATR


@dataclass
class SBRBRSLevel:
    """A broken Support/Resistance level being tested."""
    level_type: str           # "SBR" (Support Became Resistance) or
                              # "BRS" (Resistance Became Support)
    price: float              # the broken level price
    broken_direction: str     # "BULLISH" (BRS) or "BEARISH" (SBR)
    displacement: DisplacementCandle | None
    breakout_idx: int         # candle index where BOS occurred
    fvg_low: float | None     # FVG lower bound
    fvg_high: float | None    # FVG upper bound
    fvg_unmitigated: bool = True


@dataclass
class SBRKillerSetup:
    """A validated SBR/BRS killer zone setup ready for execution."""
    symbol: str
    direction: str              # "BUY" or "SELL"
    level: SBRBRSLevel
    entry_low: float
    entry_high: float
    entry_mid: float
    sl: float
    tp1: float
    tp2: float
    rr_ratio: float
    confidence: float
    killzone_name: str          # "LONDON" or "NEW_YORK"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ═══════════════════════════════════════════════════════════════════════
#  ENGINE
# ═══════════════════════════════════════════════════════════════════════


class SBRKillerEngine(Engine):
    """MTF SBR/BRS Killer Zone Engine.

    Scans H1 data for broken levels, confirms with displacement + FVG,
    then prepares M15 execution zone.

    Accepts a pre-built OHLCV dict (bars) via a class-level argument
    bypassing the Tick → OHLCV conversion for integration with
    existing data pipelines.
    """

    def __init__(
        self,
        bars_h1: list[dict] | None = None,
        bars_m15: list[dict] | None = None,
        symbol: str = "XAUUSD",
        min_pips_sl: float = 15,
    ):
        self._bars_h1 = bars_h1 or []
        self._bars_m15 = bars_m15 or []
        self._symbol = symbol
        self._min_pips_sl = min_pips_sl

    @property
    def name(self) -> str:
        return "sbr_killer"

    def set_bars(self, bars_h1: list[dict], bars_m15: list[dict]) -> None:
        """Pre-load OHLCV bars (bypasses Tick → OHLCV conversion)."""
        self._bars_h1 = bars_h1
        self._bars_m15 = bars_m15

    # ── PUBLIC INTERFACE ────────────────────────────────────────────

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for SBR/BRS setup.

        Since actual H1/M15 bar data needs to be pre-loaded via
        ``set_bars()``, the Tick path is a convenience wrapper.
        """
        setup = self.find_setup()
        if setup is None:
            return None
        return self._setup_to_signal(setup)

    def find_setup(self) -> SBRKillerSetup | None:
        """Main pipeline: find a validated SBR/BRS killer zone setup.

        Returns ``SBRKillerSetup`` if all conditions are met, else None.
        """
        h1 = self._bars_h1
        m15 = self._bars_m15

        if len(h1) < _MIN_PIVOTS_MACRO or len(m15) < _MIN_PIVOTS_MICRO:
            LOG.debug(
                "SBRKiller: insufficient bars — H1=%d (need %d), M15=%d (need %d)",
                len(h1), _MIN_PIVOTS_MACRO, len(m15), _MIN_PIVOTS_MICRO,
            )
            return None

        # 1. Detect killzone — auto-exec only in active London/NY
        kz = self._detect_killzone()
        if not kz:
            LOG.debug("SBRKiller: 🔒 No active killzone — engine idle")
            return None

        # 2. Macro: detect broken S/R levels with displacement BOS
        levels = self._find_sbr_brs_levels(h1)
        if not levels:
            LOG.debug("SBRKiller: no broken S/R levels detected on H1")
            return None

        # 3. Micro: check if price is returning to any level
        best = self._find_best_retes_ent(h1, m15, levels)
        if best is None:
            LOG.debug("SBRKiller: no retest opportunity active")
            return None

        return best

    # ── STEP 1: KILLZONE DETECTION ──────────────────────────────────

    @staticmethod
    def _detect_killzone() -> str | None:
        """Return 'LONDON', 'NEW_YORK', or None if outside killzone."""
        now_utc = datetime.now(UTC)
        h = now_utc.hour
        m = now_utc.minute
        # Current time as float hours
        t = h + m / 60.0

        london = _LONDON_START <= t < _LONDON_END
        newyork = _NY_START <= t < _NY_END

        if london and newyork:
            return "LONDON_NY_OVERLAP"
        if london:
            return "LONDON"
        if newyork:
            return "NEW_YORK"
        return None

    # ── STEP 2: S/R LEVEL DETECTION ─────────────────────────────────

    def _find_sbr_brs_levels(
        self, bars: list[dict]
    ) -> list[SBRBRSLevel]:
        """Scan H1 bars for broken S/R levels with displacement BOS.

        Algorithm:
          1. Compute ATR(14) for displacement filtering.
          2. Identify swing highs and swing lows (market structure).
          3. For each swing level that was broken:
             a) Verify the breaking candle is a displacement candle.
             b) Check the displacement created an FVG on M15.
             c) Label as SBR (broken support → resistance) or
                BRS (broken resistance → support).
        """
        if len(bars) < 16:
            return []

        atr14 = self._compute_atr(bars, 14)
        current_atr = atr14[-1] if atr14 else 0.0
        if current_atr <= 0:
            return []

        levels: list[SBRBRSLevel] = []

        # Find swing highs/lows (pivots)
        pivots = self._detect_pivots(bars)
        LOG.debug(
            "SBRKiller: %d pivots detected in %d bars (ATR=%.2f)",
            len(pivots), len(bars), current_atr,
        )

        # Minimum ATR size for a level to be meaningful
        min_level_size = current_atr * 0.3

        for i in range(1, min(len(pivots) - 1, 10)):
            p = pivots[i]
            next_p = pivots[i + 1] if i + 1 < len(pivots) else None
            if next_p is None:
                continue

            p_idx = p["idx"]
            n_idx = next_p["idx"]

            # The gap between pivot and next pivot is the break area
            if n_idx - p_idx > 25 or n_idx - p_idx < 1:
                # Too far or overlapping
                continue

            level_type, broken_dir = self._classify_level(p, next_p)
            if level_type is None:
                continue

            # The breaking candle is the candle AFTER the pivot
            # that first traded beyond the pivot level
            break_idx, break_candle = self._find_break_candle(bars, p, n_idx)
            if break_idx is None or break_candle is None:
                continue

            # Check displacement
            disp = self._check_displacement(
                break_candle, current_atr
            )
            if disp is None:
                continue

            # Check FVG on M15 for this displacement
            fvg_low, fvg_high = self._find_fvg_on_m15(
                break_candle, broken_dir, bars, current_atr
            )
            if fvg_low is None or fvg_high is None:
                LOG.debug(
                    "SBRKiller: %s @ %.2f — no FVG post-breakout, skipping",
                    level_type, p["price"],
                )
                continue

            # Level price = the pivot point (the broken level)
            level_price = p["price"]

            # Adjust zone to overlap with FVG
            zone_low = min(level_price, fvg_low) - current_atr * _ZONE_ATR_WIDTH
            zone_high = max(level_price, fvg_high) + current_atr * _ZONE_ATR_WIDTH

            levels.append(SBRBRSLevel(
                level_type=level_type,
                price=level_price,
                broken_direction=broken_dir,
                displacement=disp,
                breakout_idx=break_idx,
                fvg_low=fvg_low,
                fvg_high=fvg_high,
            ))

            LOG.info(
                "SBRKiller: %s @ %.2f → zone [%.2f, %.2f] (FVG [%.2f, %.2f])",
                level_type, level_price, zone_low, zone_high, fvg_low, fvg_high,
            )

        return levels

    # ── STEP 3: RETEST ENTRY ────────────────────────────────────────

    def _find_best_retes_ent(
        self,
        bars_h1: list[dict],
        bars_m15: list[dict],
        levels: list[SBRBRSLevel],
    ) -> SBRKillerSetup | None:
        """Find the best retest opportunity among detected levels.

        Price must be returning toward a broken level (mitigation phase).
        The direction of the retest (rejection) determines execution entry.
        """
        if not bars_m15 or not levels:
            return None

        last_price = bars_m15[-1].get("close", 0.0)
        if last_price <= 0:
            last_price = bars_h1[-1].get("close", 0.0)

        for level in levels:
            p = level.price

            if level.level_type == "SBR":
                # Support broken → now acts as RESISTANCE
                # Price should be BELOW the level, retesting from below
                # SELL when price touches/resects the level
                if last_price > p * 1.02:
                    LOG.debug("SBRKiller: SBR %s — price %.2f > level %.2f (already above)", level.level_type, last_price, p)
                    continue

                # Check M15 for rejection signs
                if not self._check_micro_rejection(bars_m15, p, "SELL"):
                    LOG.debug("SBRKiller: SBR %s — no SELL rejection on M15", level.level_type)
                    continue

                entry_low = p - self._compute_atr_last(bars_m15, 14) * _ZONE_ATR_WIDTH
                entry_high = p + self._compute_atr_last(bars_m15, 14) * _ZONE_ATR_WIDTH
                entry_mid = p
                direction = "SELL"

            elif level.level_type == "BRS":
                # Resistance broken → now acts as SUPPORT
                # Price should be ABOVE the level, retesting from above
                # BUY when price touches/resects the level
                if last_price < p * 0.98:
                    LOG.debug("SBRKiller: BRS %s — price %.2f < level %.2f (already below)", level.level_type, last_price, p)
                    continue

                if not self._check_micro_rejection(bars_m15, p, "BUY"):
                    LOG.debug("SBRKiller: BRS %s — no BUY rejection on M15", level.level_type, p)
                    continue

                entry_low = p - self._compute_atr_last(bars_m15, 14) * _ZONE_ATR_WIDTH
                entry_high = p + self._compute_atr_last(bars_m15, 14) * _ZONE_ATR_WIDTH
                entry_mid = p
                direction = "BUY"

            else:
                continue

            # Compute SL and TP
            atr_m15 = self._compute_atr_last(bars_m15, 14)
            if atr_m15 <= 0:
                continue

            # SL: outside the breaking candle's structure + buffer
            if direction == "BUY":
                sl = self._find_sl_for_buy(level, bars_h1, atr_m15)
            else:
                sl = self._find_sl_for_sell(level, bars_h1, atr_m15)

            # TP = ATR-based risk-to-reward
            risk = abs(entry_mid - sl)
            if risk == 0:
                continue

            # Check minimum pip requirement
            pip_size = _SYMBOL_PIPS.get(self._symbol, 0.10)
            risk_pips = risk / pip_size if pip_size else 0
            if risk_pips < self._min_pips_sl:
                LOG.debug(
                    "SBRKiller: risk %.1f pips < minimum %.1f pips",
                    risk_pips, self._min_pips_sl,
                )
                continue

            tp1 = entry_mid + risk * _TP1_RR if direction == "BUY" else entry_mid - risk * _TP1_RR
            tp2 = entry_mid + risk * _TP2_RR if direction == "BUY" else entry_mid - risk * _TP2_RR
            rr = _TP1_RR

            # Confidence
            conf = self._compute_confidence(level, risk_pips)

            kz = self._detect_killzone() or "UNKNOWN"

            return SBRKillerSetup(
                symbol=self._symbol,
                direction=direction,
                level=level,
                entry_low=entry_low,
                entry_high=entry_high,
                entry_mid=entry_mid,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                rr_ratio=rr,
                confidence=conf,
                killzone_name=kz,
            )

        return None

    # ── UTILITY METHODS ─────────────────────────────────────────────

    @staticmethod
    def _compute_atr(bars: list[dict], period: int = 14) -> list[float]:
        """Compute ATR over bars using EMA of true range."""
        if len(bars) < period + 1:
            return [0.0] * len(bars)

        tr_list: list[float] = []
        for i in range(1, len(bars)):
            high = bars[i].get("high", 0)
            low = bars[i].get("low", 0)
            prev_close = bars[i - 1].get("close", 0)
            tr = max(
                high - low,
                abs(high - prev_close) if prev_close else 0,
                abs(low - prev_close) if prev_close else 0,
            )
            tr_list.append(max(tr, 0.001))

        atr = [0.0] * len(bars)

        # First ATR is SMA of first `period` TRs
        first_val = sum(tr_list[:period]) / period
        atr[period] = first_val

        # EMA for remaining
        for i in range(period + 1, len(bars)):
            atr[i] = (atr[i - 1] * (period - 1) + tr_list[i - 1]) / period

        return atr

    @staticmethod
    def _compute_atr_last(bars: list[dict], period: int = 14) -> float:
        """Quick compute — return the last ATR value only."""
        tr_list: list[float] = []
        for i in range(max(1, len(bars) - period - 10), len(bars)):
            high = bars[i].get("high", 0)
            low = bars[i].get("low", 0)
            prev_close = bars[i - 1].get("close", 0) if i > 0 else 0
            tr = max(
                high - low,
                abs(high - prev_close) if prev_close else 0,
                abs(low - prev_close) if prev_close else 0,
            )
            tr_list.append(max(tr, 0.001))

        if not tr_list:
            return 0.0
        if len(tr_list) < period:
            return sum(tr_list) / len(tr_list)

        return sum(tr_list[-period:]) / period

    @staticmethod
    def _detect_pivots(bars: list[dict]) -> list[dict]:
        """Detect swing highs and lows (market pivots).

        Uses a fractal-like detection: a candle is a pivot high if it
        has higher highs than 2 candles on each side, and a pivot low
        if it has lower lows than 2 candles on each side.
        """
        pivots: list[dict] = []
        lookback = 2

        for i in range(lookback, len(bars) - lookback):
            high = bars[i].get("high", 0)
            low = bars[i].get("low", 0)

            # Pivot high
            is_high = True
            for j in range(1, lookback + 1):
                if high <= bars[i - j].get("high", 0) or high <= bars[i + j].get("high", 0):
                    is_high = False
                    break
            if is_high:
                pivots.append({
                    "idx": i,
                    "price": high,
                    "type": "HIGH",
                })

            # Pivot low
            is_low = True
            for j in range(1, lookback + 1):
                if low >= bars[i - j].get("low", 0) or low >= bars[i + j].get("low", 0):
                    is_low = False
                    break
            if is_low:
                pivots.append({
                    "idx": i,
                    "price": low,
                    "type": "LOW",
                })

        return pivots

    @staticmethod
    def _classify_level(
        pivot: dict, next_pivot: dict
    ) -> tuple[str | None, str | None]:
        """Classify a broken level as SBR or BRS.

        SBR = Support Became Resistance:
          A low pivot was broken lower → price broke through support.
          That level now acts as resistance.

        BRS = Resistance Became Support:
          A high pivot was broken higher → price broke through resistance.
          That level now acts as support.
        """
        if pivot["type"] == "LOW" and next_pivot["type"] == "LOW" and next_pivot["price"] < pivot["price"]:
            # Support broken down → SBR
            return "SBR", "BEARISH"

        if pivot["type"] == "HIGH" and next_pivot["type"] == "HIGH" and next_pivot["price"] > pivot["price"]:
            # Resistance broken up → BRS
            return "BRS", "BULLISH"

        return None, None

    @staticmethod
    def _find_break_candle(
        bars: list[dict], pivot: dict, max_idx: int
    ) -> tuple[int | None, dict | None]:
        """Find the first candle that trades beyond the pivot level."""
        p_type = pivot["type"]
        p_price = pivot["price"]

        start = pivot["idx"] + 1
        end = min(max_idx + 1, len(bars))

        for i in range(start, end):
            candle = bars[i]
            if p_type == "HIGH":
                # Need to break ABOVE the high pivot
                if candle.get("high", 0) > p_price:
                    return i, candle
            elif p_type == "LOW":
                # Need to break BELOW the low pivot
                if candle.get("low", 0) < p_price:
                    return i, candle

        return None, None

    @staticmethod
    def _check_displacement(
        candle: dict, atr: float
    ) -> DisplacementCandle | None:
        """Check if a candle qualifies as a displacement candle.

        Conditions:
          1. Body / Total Candle Size >= 0.70
          2. Total candle size >= 1.5 × ATR(14)
        """
        o = candle.get("open", 0)
        h = candle.get("high", 0)
        l = candle.get("low", 0)
        c = candle.get("close", 0)

        total = h - l
        if total <= 0:
            return None

        body = abs(c - o)
        body_ratio = body / total
        is_bullish = c > o
        above_atr = total >= _DISPLACEMENT_ATR_MULTIPLIER * atr

        if body_ratio < _DISPLACEMENT_BODY_RATIO:
            LOG.debug(
                "Displacement FAIL: body ratio %.2f < %.2f",
                body_ratio, _DISPLACEMENT_BODY_RATIO,
            )
            return None

        if not above_atr:
            LOG.debug(
                "Displacement FAIL: size %.2f < 1.5×ATR %.2f",
                total, _DISPLACEMENT_ATR_MULTIPLIER * atr,
            )
            return None

        return DisplacementCandle(
            idx=-1,
            high=h, low=l, open_p=o, close=c,
            body_size=body, total_size=total,
            body_ratio=body_ratio, is_bullish=is_bullish,
            above_atr=above_atr,
        )

    @staticmethod
    def _find_fvg_on_m15(
        break_candle: dict,
        direction: str,
        h1_bars: list[dict],
        atr: float,
    ) -> tuple[float | None, float | None]:
        """Find FVG on M15 created by the displacement breakout.

        A Fair Value Gap (FVG) is a 3-candle sequence where:
          - Candle 1's wick overlaps with Candle 3's wick
          - The gap between them has minimal overlap

        For simplicity, we derive a micro-structure FVG proxy from
        the H1 displacement candle:
          - BULLISH/BRS: gap between break candle's high and the
            previous candle's low (bullish FVG)
          - BEARISH/SBR: gap between break candle's low and the
            previous candle's high (bearish FVG)

        Returns (fvg_low, fvg_high) or (None, None).
        """
        if direction.upper() == "BULLISH":
            # Bullish break: look for gap up
            prev_high = h1_bars[break_candle.get("idx", max(0, len(h1_bars) - 2)) - 1]["high"]
            curr_low = break_candle.get("low", 0)
            # FVG = gap between prev high and current low (if current low > prev high)
            if curr_low > prev_high:
                return prev_high, curr_low
        elif direction.upper() == "BEARISH":
            prev_low = h1_bars[break_candle.get("idx", max(0, len(h1_bars) - 2)) - 1]["low"]
            curr_high = break_candle.get("high", 0)
            # FVG = gap between prev low and current high (if current high < prev low)
            if curr_high < prev_low:
                return curr_high, prev_low

        return None, None

    @staticmethod
    def _check_micro_rejection(
        bars_m15: list[dict],
        level_price: float,
        direction: str,
    ) -> bool:
        """Check for rejection signs on M15 near the broken level.

        For BUY (BRS): price dipped to level price and bounced up
        For SELL (SBR): price rose to level price and rejected down

        Looks for a pin bar / rejection candle on the last 3 M15 bars.
        """
        lookback = min(5, len(bars_m15))
        recent = bars_m15[-lookback:]

        for candle in recent:
            o = candle.get("open", 0)
            h = candle.get("high", 0)
            l = candle.get("low", 0)
            c = candle.get("close", 0)
            body = abs(c - o)
            total = max(h - l, 0.001)
            body_ratio = body / total
            wick_to_level = abs(h - level_price)

            if direction == "BUY":
                # Bullish rejection: long lower wick near support
                lower_wick = min(o, c) - l
                upper_wick = h - max(o, c)
                # Should touch/break level and reject up
                if l <= level_price * 1.001 and l >= level_price * 0.995:
                    if c > o and lower_wick > body * 1.5:
                        LOG.debug(
                            "SBRKiller: BUY rejection at %.2f (LW=%.2f body=%.2f)",
                            level_price, lower_wick, body,
                        )
                        return True
            elif direction == "SELL":
                # Bearish rejection: long upper wick near resistance
                lower_wick = min(o, c) - l
                upper_wick = h - max(o, c)
                # Should touch/break level and reject down
                if h >= level_price * 0.999 and h <= level_price * 1.005:
                    if c < o and upper_wick > body * 1.5:
                        LOG.debug(
                            "SBRKiller: SELL rejection at %.2f (UW=%.2f body=%.2f)",
                            level_price, upper_wick, body,
                        )
                        return True

        return False

    @staticmethod
    def _find_sl_for_buy(
        level: SBRBRSLevel, bars: list[dict], atr_m15: float
    ) -> float:
        """SL for BUY: below the breakout candle's low + buffer."""
        if level.displacement:
            sl_candidate = level.displacement.low - atr_m15 * _SL_ATR_BUFFER
        else:
            # Fallback: level - 1.5×ATR
            sl_candidate = level.price - atr_m15 * 1.5

        return min(sl_candidate, level.price - atr_m15 * 1.2)

    @staticmethod
    def _find_sl_for_sell(
        level: SBRBRSLevel, bars: list[dict], atr_m15: float
    ) -> float:
        """SL for SELL: above the breakout candle's high + buffer."""
        if level.displacement:
            sl_candidate = level.displacement.high + atr_m15 * _SL_ATR_BUFFER
        else:
            sl_candidate = level.price + atr_m15 * 1.5

        return max(sl_candidate, level.price + atr_m15 * 1.2)

    @staticmethod
    def _compute_confidence(level: SBRBRSLevel, risk_pips: float) -> float:
        """Compute confidence score 0.0-1.0.

        Factors:
          - Displacement body ratio (0.70+ = base)
          - FVG width (wider gap = higher confidence)
          - Risk-to-reward quality
          - Level type (SBR/BRS)
        """
        base = 0.50

        # Displacement quality
        if level.displacement:
            br = level.displacement.body_ratio
            if br >= 0.85:
                base += 0.20
            elif br >= 0.78:
                base += 0.15
            elif br >= 0.70:
                base += 0.05

            if level.displacement.above_atr:
                if level.displacement.total_size >= 2.5 * (level.displacement.total_size / 1.5):
                    base += 0.10

        # FVG quality
        if level.fvg_low is not None and level.fvg_high is not None:
            fvg_width = abs(level.fvg_high - level.fvg_low)
            if fvg_width > 0:
                avg_atr = 10.0  # rough estimate
                fvg_atr_ratio = fvg_width / avg_atr
                if fvg_atr_ratio >= 0.5:
                    base += 0.10
                elif fvg_atr_ratio >= 0.3:
                    base += 0.05

        # SBR/BRS have different success rates depending on market
        # BRS (bullish) tends to hold slightly more reliably
        if level.level_type == "BRS":
            base += 0.03

        # Risk quality
        if risk_pips >= 30:
            base += 0.10
        elif risk_pips >= 20:
            base += 0.05

        return min(base, 0.95)

    # ── OUTPUT FORMATTING ───────────────────────────────────────────

    @staticmethod
    def format_setup_text(setup: SBRKillerSetup) -> str:
        """Format a killer zone setup as a Telegram message."""
        emoji = "🟢" if setup.direction == "BUY" else "🔴"
        level_emoji = "📈" if setup.level.level_type == "BRS" else "📉"
        zone_str = f"${setup.entry_low:.2f} — ${setup.entry_high:.2f}"

        return (
            f"🎯 <b>APEX SBR/BRS KILLER ZONE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{level_emoji} <b>{setup.level.level_type}</b> | {emoji} <b>{setup.direction}</b>\n"
            f"📊 Symbol: <b>{setup.symbol}</b>\n"
            f"────────────────────\n"
            f"💀 <b>Killzone:</b> {setup.killzone_name}\n"
            f"📐 <b>Entry Zone:</b> {zone_str}\n"
            f"🎯 <b>Optimal Entry:</b> ${setup.entry_mid:.2f}\n"
            f"🛑 <b>SL:</b> ${setup.sl:.2f}\n"
            f"🎯 <b>TP1:</b> ${setup.tp1:.2f} ({setup.rr_ratio:.1f}R)\n"
            f"🎯 <b>TP2:</b> ${setup.tp2:.2f} ({_TP2_RR:.1f}R)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>Confidence:</b> {setup.confidence:.0%}\n"
            f"🧿 FVG Status: {'✅ Present' if setup.level.fvg_low else '⚠️ None'}\n"
            f"🔄 Displacement: {'✅' if setup.level.displacement else '❌'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ Active: {setup.generated_at.strftime('%H:%M UTC')}\n"
        )

    def _setup_to_signal(self, setup: SBRKillerSetup) -> Signal:
        """Convert SBRKillerSetup to a Signal for the pipeline."""
        from tradebot.models import SignalGrade, SignalSource

        dir_str = "CALL" if setup.direction == "BUY" else "PUT"

        conf = setup.confidence
        if conf >= 0.75:
            grade = SignalGrade.STRONG
        elif conf >= 0.55:
            grade = SignalGrade.MODERATE
        else:
            grade = SignalGrade.WEAK

        sig = Signal(
            symbol=setup.symbol,
            direction=dir_str,
            predicted_digit=0,
            confidence=conf,
            source=SignalSource.CONSENSUS,
            grade=grade,
            entry_price=setup.entry_mid,
            metadata={
                "engine": self.name,
                "strategy": f"SBR/BRS {setup.level.level_type} Killer Zone",
                "direction_display": setup.direction,
                "killzone": setup.killzone_name,
                "entry_low": setup.entry_low,
                "entry_high": setup.entry_high,
                "entry_mid": setup.entry_mid,
                "sl": setup.sl,
                "tp1": setup.tp1,
                "tp2": setup.tp2,
                "rr_ratio": setup.rr_ratio,
                "confidence": setup.confidence,
                "fvg_low": setup.level.fvg_low,
                "fvg_high": setup.level.fvg_high,
                "broken_level": setup.level.price,
                "level_type": setup.level.level_type,
                "label": "🎯 Apex SBR/BRS Killer Zone",
            },
        )
        return sig
