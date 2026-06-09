"""
sweep.py — Liquidity Sweep / False Breakout Detector Engine

Migrated from: scripts/sweep_detector.py
Conforms to: tradebot.engines.base.Engine interface
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

# ── Configurable defaults ──────────────────────────────────────────

_SWEEP_MAX_CANDLES_BACK: int = int(getattr(settings, "SWEEP_MAX_CANDLES_BACK", 8))
_SWEEP_WICK_PCT_MIN: float = float(getattr(settings, "SWEEP_WICK_PCT_MIN", 0.5))


@dataclass
class SweepSignal:
    """A detected liquidity sweep."""
    direction: str = ""
    level_name: str = ""
    level_price: float = 0.0
    entry_price: float = 0.0
    sweep_high: float = 0.0
    sweep_low: float = 0.0
    sweep_close: float = 0.0
    candle_index: int = -1
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_short(self) -> str:
        return "SELL" if self.direction == "BEARISH" else "BUY"

    def to_dict(self) -> dict:
        return {
            "direction": self.direction, "direction_short": self.direction_short,
            "level_name": self.level_name, "level_price": self.level_price,
            "entry_price": self.entry_price, "sweep_high": self.sweep_high,
            "sweep_low": self.sweep_low, "sweep_close": self.sweep_close,
            "candle_index": self.candle_index, "confidence": round(self.confidence, 3),
            "metadata": self.metadata,
        }


# ── Helpers ────────────────────────────────────────────────────────


def _pip_value(price: float) -> float:
    if price > 1000:
        return 0.10
    if price > 100:
        return 0.01
    return 0.0001


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    """Convert ticks to OHLCV-like bar dicts."""
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price, "high": t.price,
            "low": t.price, "close": t.price,
        })
    return bars


# ── Detection ──────────────────────────────────────────────────────


def _check_bearish_sweep(
    candle: dict, level_price: float, level_name: str, pip_value: float,
) -> dict | None:
    """Check for bearish sweep above a resistance level."""
    try:
        high = float(candle.get("high", 0))
        low = float(candle.get("low", 0))
        close = float(candle.get("close", 0))
    except (KeyError, ValueError, TypeError):
        return None
    if high <= level_price:
        return None
    upper_wick = high - max(close, level_price)
    if upper_wick < pip_value * _SWEEP_WICK_PCT_MIN:
        return None
    if close >= level_price - pip_value * 1:
        return None
    gap_pips = (level_price - close) / pip_value if pip_value > 0 else 0
    range_size = high - low
    wick_conf = min(1.0, (upper_wick / range_size) / _SWEEP_WICK_PCT_MIN) if range_size > 0 else 0
    gap_conf = min(1.0, gap_pips / 15)
    confidence = round(wick_conf * 0.4 + gap_conf * 0.6, 3)
    return {
        "direction": "BEARISH", "level_name": level_name, "level_price": level_price,
        "sweep_high": high, "sweep_close": close,
        "gap_pips": round(gap_pips, 1), "wick_pips": round(upper_wick / pip_value, 1) if pip_value > 0 else 0,  # noqa: E501
        "confidence": confidence,
    }


def _check_bullish_sweep(
    candle: dict, level_price: float, level_name: str, pip_value: float,
) -> dict | None:
    """Check for bullish sweep below a support level."""
    try:
        high = float(candle.get("high", 0))
        low = float(candle.get("low", 0))
        close = float(candle.get("close", 0))
    except (KeyError, ValueError, TypeError):
        return None
    if low >= level_price:
        return None
    lower_wick = min(close, level_price) - low
    if lower_wick < pip_value * _SWEEP_WICK_PCT_MIN:
        return None
    if close <= level_price + pip_value * 1:
        return None
    gap_pips = (close - level_price) / pip_value if pip_value > 0 else 0
    range_size = high - low
    wick_conf = min(1.0, (lower_wick / range_size) / _SWEEP_WICK_PCT_MIN) if range_size > 0 else 0
    gap_conf = min(1.0, gap_pips / 15)
    confidence = round(wick_conf * 0.4 + gap_conf * 0.6, 3)
    return {
        "direction": "BULLISH", "level_name": level_name, "level_price": level_price,
        "sweep_low": low, "sweep_close": close,
        "gap_pips": round(gap_pips, 1), "wick_pips": round(lower_wick / pip_value, 1) if pip_value > 0 else 0,  # noqa: E501
        "confidence": confidence,
    }


def _detect_sweep(
    ohlcv: list[dict],
    asia_high: float | None = None, asia_low: float | None = None,
    london_high: float | None = None, london_low: float | None = None,
    ny_high: float | None = None, ny_low: float | None = None,
    prev_day_high: float | None = None, prev_day_low: float | None = None,
    today_high: float | None = None, today_low: float | None = None,
    current_price: float | None = None,
    max_candles_back: int = _SWEEP_MAX_CANDLES_BACK,
) -> SweepSignal | None:
    """Detect liquidity sweeps across session levels."""
    if not ohlcv or len(ohlcv) < 3:
        return None
    recent_candles = ohlcv[-max_candles_back:] if len(ohlcv) >= max_candles_back else ohlcv
    if not current_price and recent_candles:
        current_price = float(recent_candles[-1].get("close", recent_candles[-1].get("open", 0)))
    if not current_price:
        return None
    pip_value = _pip_value(current_price)

    bearish_levels: list[tuple[float, str, str]] = []
    bullish_levels: list[tuple[float, str, str]] = []

    if asia_high:
        bearish_levels.append((asia_high, "Asia High", "key"))
    if london_high:
        bearish_levels.append((london_high, "London High", "key"))
    if prev_day_high:
        bearish_levels.append((prev_day_high, "Previous Day High", "key"))
    if ny_high:
        bearish_levels.append((ny_high, "NY High", "secondary"))
    if today_high:
        bearish_levels.append((today_high, "Today High", "secondary"))
    if asia_low:
        bullish_levels.append((asia_low, "Asia Low", "key"))
    if london_low:
        bullish_levels.append((london_low, "London Low", "key"))
    if prev_day_low:
        bullish_levels.append((prev_day_low, "Previous Day Low", "key"))
    if ny_low:
        bullish_levels.append((ny_low, "NY Low", "secondary"))
    if today_low:
        bullish_levels.append((today_low, "Today Low", "secondary"))

    best_sweep: dict | None = None
    best_candle_idx = -1

    for offset, candle in enumerate(reversed(recent_candles)):
        actual_idx = len(ohlcv) - 1 - offset
        for level_price, level_name, level_type in bearish_levels:
            result = _check_bearish_sweep(candle, level_price, level_name, pip_value)
            if result:
                result["level_type"] = level_type
                if level_type == "key":
                    result["confidence"] = min(1.0, result.get("confidence", 0) * 1.2)
                if best_sweep is None or result.get("confidence", 0) > best_sweep.get("confidence", 0):  # noqa: E501
                    best_sweep = result
                    best_candle_idx = actual_idx
                    best_sweep["sweep_low"] = float(candle.get("low", 0))
                    best_sweep["sweep_high"] = float(candle.get("high", 0))
                    best_sweep["entry_price"] = current_price
        for level_price, level_name, level_type in bullish_levels:
            result = _check_bullish_sweep(candle, level_price, level_name, pip_value)
            if result:
                result["level_type"] = level_type
                if level_type == "key":
                    result["confidence"] = min(1.0, result.get("confidence", 0) * 1.2)
                if best_sweep is None or result.get("confidence", 0) > best_sweep.get("confidence", 0):  # noqa: E501
                    best_sweep = result
                    best_candle_idx = actual_idx
                    best_sweep["sweep_low"] = float(candle.get("low", 0))
                    best_sweep["sweep_high"] = float(candle.get("high", 0))
                    best_sweep["entry_price"] = current_price

    if not best_sweep:
        return None

    return SweepSignal(
        direction=best_sweep["direction"],
        level_name=best_sweep["level_name"],
        level_price=best_sweep["level_price"],
        entry_price=best_sweep.get("entry_price", current_price),
        sweep_high=best_sweep.get("sweep_high", 0.0),
        sweep_low=best_sweep.get("sweep_low", 0.0),
        sweep_close=best_sweep["sweep_close"],
        candle_index=best_candle_idx,
        confidence=best_sweep["confidence"],
        metadata={
            "gap_pips": best_sweep.get("gap_pips"),
            "wick_pips": best_sweep.get("wick_pips"),
            "level_type": best_sweep.get("level_type", ""),
            "pip_value": pip_value,
        },
    )


# ── Engine ─────────────────────────────────────────────────────────


class SweepEngine(Engine):
    """Liquidity Sweep / False Breakout Detector Engine.

    Detects wick breaches of key session levels with body rejection —
    a classic liquidity grab / stop-hunt pattern.
    """

    def __init__(self) -> None:
        self._max_candles: int = int(getattr(settings, "SWEEP_MAX_CANDLES_BACK", _SWEEP_MAX_CANDLES_BACK))  # noqa: E501
        self._min_confidence: float = float(getattr(settings, "SWEEP_MIN_CONFIDENCE", 0.30))

    @property
    def name(self) -> str:
        return "sweep_detector"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for liquidity sweep patterns."""
        if not ticks or len(ticks) < 3:
            LOG.debug("Sweep: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            current_price = ticks[-1].price

            sweep = _detect_sweep(
                ohlcv, current_price=current_price,
                max_candles_back=self._max_candles,
            )

            if not sweep or sweep.confidence < self._min_confidence:
                return None

            signal_direction = "CALL" if sweep.direction_short == "BUY" else "PUT"

            return Signal(
                symbol="XAUUSD",
                direction=signal_direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=sweep.confidence,
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if sweep.confidence >= 0.7 else (
                    SignalGrade.MODERATE if sweep.confidence >= 0.5 else SignalGrade.WEAK
                ),
                metadata={
                    "engine": self.name,
                    "sweep_direction": sweep.direction,
                    "level_name": sweep.level_name,
                    "level_price": sweep.level_price,
                    "sweep_high": sweep.sweep_high,
                    "sweep_low": sweep.sweep_low,
                    "entry_price": sweep.entry_price,
                    "candle_index": sweep.candle_index,
                    "metadata": sweep.metadata,
                },
            )
        except Exception as exc:
            LOG.warning("Sweep engine error: %s", exc)
            raise SignalError("Sweep analysis failed", details={"error": str(exc)}) from exc
