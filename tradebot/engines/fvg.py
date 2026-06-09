"""
fvg.py — Fair Value Gap Engine

Migrated from: scripts/fvg_detector.py
Conforms to: tradebot.engines.base.Engine interface
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

# ── Configurable defaults ──────────────────────────────────────────

_MIN_FVG_SIZE_PIPS: float = float(getattr(settings, "FVG_MIN_SIZE_PIPS", 2.0))
_MAX_FVG_AGE_BARS: int = int(getattr(settings, "FVG_MAX_AGE_BARS", 30))
_MITIGATION_THRESHOLD: float = float(getattr(settings, "FVG_MITIGATION_THRESHOLD", 0.80))


@dataclass
class FVGZone:
    """A detected Fair Value Gap zone."""
    bar_index: int
    top: float
    bottom: float
    direction: str
    size_pips: float
    mitigated: bool = False
    mitigation_bar: int = -1


@dataclass
class FVGSignal:
    """A tradable signal derived from FVG analysis."""
    direction: str
    fvg_zone: FVGZone
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr_ratio: float
    confidence: float
    reasoning: str


# ── Helpers ────────────────────────────────────────────────────────


def _calculate_pips(price: float, gap: float) -> float:
    """Convert price gap to pips based on asset type."""
    if price > 1000:
        return abs(gap) * 10
    if price > 100:
        return abs(gap)
    return abs(gap) * 100


def _calculate_sl_pips(price: float) -> float:
    """Dynamic SL based on asset type."""
    if price > 1000:
        return 15.0
    if price > 100:
        return 50.0
    return 20.0


def _pip_to_price(price: float, pips: float) -> float:
    """Convert pips to price units based on asset type."""
    if price > 1000:
        return pips * 0.10
    if price > 100:
        return pips * 1.0
    return pips * 0.0001


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    """Convert ticks to OHLCV-like bar dicts."""
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price,
            "high": t.price,
            "low": t.price,
            "close": t.price,
        })
    return bars


# ── Detection ──────────────────────────────────────────────────────


def detect_fvg_zones(ohlcv_bars: list[dict], max_age: int = _MAX_FVG_AGE_BARS) -> list[FVGZone]:
    """Detect all FVG zones from OHLCV bars."""
    zones: list[FVGZone] = []
    if len(ohlcv_bars) < 3:
        return zones

    n = len(ohlcv_bars)
    scan_start = max(0, n - max_age - 2)

    for i in range(scan_start, n - 1):
        try:
            prev = ohlcv_bars[i - 1] if i > 0 else None
            curr = ohlcv_bars[i]
            if prev is None:
                continue
            prev_high = float(prev["high"])
            prev_low = float(prev["low"])
            curr_high = float(curr["high"])
            curr_low = float(curr["low"])
        except (KeyError, ValueError, TypeError, IndexError):
            continue

        price = (curr_high + curr_low) / 2

        # Bullish FVG: current low > previous high
        if curr_low > prev_high:
            gap = curr_low - prev_high
            pips = _calculate_pips(price, gap)
            if pips >= _MIN_FVG_SIZE_PIPS:
                zone = FVGZone(
                    bar_index=n - 1 - i,
                    top=curr_low,
                    bottom=prev_high,
                    direction="BUY",
                    size_pips=pips,
                )
                _check_mitigation(zone, ohlcv_bars[i + 1:])
                zones.append(zone)

        # Bearish FVG: current high < previous low
        elif curr_high < prev_low:
            gap = prev_low - curr_high
            pips = _calculate_pips(price, gap)
            if pips >= _MIN_FVG_SIZE_PIPS:
                zone = FVGZone(
                    bar_index=n - 1 - i,
                    top=prev_low,
                    bottom=curr_high,
                    direction="SELL",
                    size_pips=pips,
                )
                _check_mitigation(zone, ohlcv_bars[i + 1:])
                zones.append(zone)

    return zones


def _check_mitigation(zone: FVGZone, future_bars: list[dict]) -> bool:
    """Check if an FVG zone has been mitigated by subsequent price action."""
    for idx, bar in enumerate(future_bars):
        try:
            close = float(bar.get("close", bar.get("open", 0)))
        except (KeyError, ValueError, TypeError):
            continue

        if zone.direction == "BUY":
            if close < zone.bottom:
                zone.mitigated = True
                zone.mitigation_bar = idx
                return True
        else:
            if close > zone.top:
                zone.mitigated = True
                zone.mitigation_bar = idx
                return True
    return False


def _detect_fvg(
    ohlcv_bars: list[dict],
    current_price: float | None = None,
    max_age: int = _MAX_FVG_AGE_BARS,
) -> list[FVGSignal]:
    """Detect FVGs and generate actionable signals."""
    if current_price is None and ohlcv_bars:
        current_price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))
    if not current_price:
        return []

    zones = detect_fvg_zones(ohlcv_bars, max_age)
    signals: list[FVGSignal] = []

    for zone in zones:
        if zone.bar_index > max_age // 2:
            continue
        if zone.bar_index > 8:
            continue
        if zone.mitigated:
            continue

        in_zone = zone.bottom <= current_price <= zone.top

        if zone.direction == "BUY" and current_price <= zone.top:
            entry = min(current_price, zone.top)
            sl_pips = _calculate_sl_pips(current_price)
            sl = entry - _pip_to_price(current_price, sl_pips)
            gap_height = zone.top - zone.bottom
            tp1 = entry + gap_height * 0.5
            tp2 = entry + gap_height
            tp3 = entry + gap_height * 2.0
            rr = gap_height / _pip_to_price(current_price, sl_pips) if _pip_to_price(current_price, sl_pips) > 0 else 0  # noqa: E501
            confidence = min(1.0, zone.size_pips / 15.0) if in_zone else min(0.5, zone.size_pips / 20.0)  # noqa: E501

            signals.append(FVGSignal(
                direction="BUY", fvg_zone=zone,
                entry=round(entry, 2), sl=round(sl, 2),
                tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2),
                rr_ratio=round(rr, 2), confidence=round(confidence, 2),
                reasoning=f"Bullish FVG ({zone.size_pips:.0f} pip) @ {zone.bottom:.2f}-{zone.top:.2f}",  # noqa: E501
            ))

        elif zone.direction == "SELL" and current_price >= zone.bottom:
            entry = max(current_price, zone.bottom)
            sl_pips = _calculate_sl_pips(current_price)
            sl = entry + _pip_to_price(current_price, sl_pips)
            gap_height = zone.top - zone.bottom
            tp1 = entry - gap_height * 0.5
            tp2 = entry - gap_height
            tp3 = entry - gap_height * 2.0
            rr = gap_height / _pip_to_price(current_price, sl_pips) if _pip_to_price(current_price, sl_pips) > 0 else 0  # noqa: E501
            confidence = min(1.0, zone.size_pips / 15.0) if in_zone else min(0.5, zone.size_pips / 20.0)  # noqa: E501

            signals.append(FVGSignal(
                direction="SELL", fvg_zone=zone,
                entry=round(entry, 2), sl=round(sl, 2),
                tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2),
                rr_ratio=round(rr, 2), confidence=round(confidence, 2),
                reasoning=f"Bearish FVG ({zone.size_pips:.0f} pip) @ {zone.bottom:.2f}-{zone.top:.2f}",  # noqa: E501
            ))

    return signals


# ── Engine ─────────────────────────────────────────────────────────


class FVGEngine(Engine):
    """Fair Value Gap Engine — detects price imbalances for trade entries."""

    def __init__(self) -> None:
        self._max_age: int = int(getattr(settings, "FVG_MAX_AGE_BARS", _MAX_FVG_AGE_BARS))

    @property
    def name(self) -> str:
        return "fvg_detector"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for Fair Value Gaps and generate signals."""
        if not ticks or len(ticks) < 3:
            LOG.debug("FVG: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            current_price = ticks[-1].price
            signals = _detect_fvg(ohlcv, current_price, self._max_age)

            if not signals:
                return None

            # Pick the best signal
            best = max(signals, key=lambda s: s.confidence)
            signal_direction = "CALL" if best.direction == "BUY" else "PUT"

            return Signal(
                symbol="XAUUSD",
                direction=signal_direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=best.confidence,
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if best.confidence >= 0.7 else (
                    SignalGrade.MODERATE if best.confidence >= 0.5 else SignalGrade.WEAK
                ),
                metadata={
                    "engine": self.name,
                    "fvg_top": best.fvg_zone.top,
                    "fvg_bottom": best.fvg_zone.bottom,
                    "fvg_size_pips": best.fvg_zone.size_pips,
                    "fvg_direction": best.direction,
                    "entry": best.entry,
                    "sl": best.sl,
                    "tp1": best.tp1,
                    "tp2": best.tp2,
                    "rr_ratio": best.rr_ratio,
                    "reasoning": best.reasoning,
                },
            )
        except Exception as exc:
            LOG.warning("FVG engine error: %s", exc)
            raise SignalError("FVG analysis failed", details={"error": str(exc)}) from exc
