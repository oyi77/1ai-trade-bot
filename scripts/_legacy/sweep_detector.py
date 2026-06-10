"""
sweep_detector.py — False Breakout / Liquidity Sweep Detector
=============================================================
Algorithmic detection of Pre-NFP liquidity sweeps on H1 timeframe.

False Breakout Definition:
  Bearish: H1 candle wick breaches session HIGH → Close comes back BELOW session high.
  Bullish: H1 candle wick breaches session LOW  → Close comes back ABOVE session low.

Usage:
    from sweep_detector import detect_sweep, SweepSignal
    from session_levels import calculate_all_levels
    levels = calculate_all_levels(ohlcv_m15)
    sweep = detect_sweep(ohlcv_h1, levels)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_levels import SessionLevels, _pip_value, _parse_timestamp

SWEEP_MAX_CANDLES_BACK: int = 8
SWEEP_WICK_PCT_MIN: float = 0.5


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
    metadata: Dict[str, Any] = field(default_factory=dict)

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


def _check_bearish_sweep(candle: dict, level_price: float, level_name: str, pip_value: float) -> Optional[Dict[str, Any]]:
    try:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
    except (KeyError, ValueError, TypeError):
        return None
    if high <= level_price:
        return None
    upper_wick = high - max(close, level_price)
    if upper_wick < pip_value * SWEEP_WICK_PCT_MIN:
        return None
    if close >= level_price - pip_value * 1:
        return None
    gap_pips = (level_price - close) / pip_value
    range_size = high - low
    wick_conf = min(1.0, (upper_wick / range_size) / SWEEP_WICK_PCT_MIN) if range_size > 0 else 0
    gap_conf = min(1.0, gap_pips / 15)
    confidence = round(wick_conf * 0.4 + gap_conf * 0.6, 3)
    return {
        "direction": "BEARISH", "level_name": level_name, "level_price": level_price,
        "sweep_high": high, "sweep_close": close,
        "gap_pips": round(gap_pips, 1), "wick_pips": round(upper_wick / pip_value, 1),
        "confidence": confidence,
    }


def _check_bullish_sweep(candle: dict, level_price: float, level_name: str, pip_value: float) -> Optional[Dict[str, Any]]:
    try:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
    except (KeyError, ValueError, TypeError):
        return None
    if low >= level_price:
        return None
    lower_wick = min(close, level_price) - low
    if lower_wick < pip_value * SWEEP_WICK_PCT_MIN:
        return None
    if close <= level_price + pip_value * 1:
        return None
    gap_pips = (close - level_price) / pip_value
    range_size = high - low
    wick_conf = min(1.0, (lower_wick / range_size) / SWEEP_WICK_PCT_MIN) if range_size > 0 else 0
    gap_conf = min(1.0, gap_pips / 15)
    confidence = round(wick_conf * 0.4 + gap_conf * 0.6, 3)
    return {
        "direction": "BULLISH", "level_name": level_name, "level_price": level_price,
        "sweep_low": low, "sweep_close": close,
        "gap_pips": round(gap_pips, 1), "wick_pips": round(lower_wick / pip_value, 1),
        "confidence": confidence,
    }


def detect_sweep(
    ohlcv_h1: List[dict], session_levels: SessionLevels,
    current_price: Optional[float] = None,
    max_candles_back: int = SWEEP_MAX_CANDLES_BACK,
) -> Optional[SweepSignal]:
    """Main entry: detect Pre-NFP liquidity sweep on H1."""
    if not ohlcv_h1 or len(ohlcv_h1) < 3:
        return None
    recent_candles = ohlcv_h1[-max_candles_back:] if len(ohlcv_h1) >= max_candles_back else ohlcv_h1
    if not current_price and recent_candles:
        current_price = float(recent_candles[-1].get("close", recent_candles[-1].get("open", 0)))
    if not current_price:
        return None
    pip_value = _pip_value(current_price)

    bearish_levels = []
    bullish_levels = []
    if session_levels.asia_high:
        bearish_levels.append((session_levels.asia_high, "Asia High", "key"))
    if session_levels.london_high:
        bearish_levels.append((session_levels.london_high, "London High", "key"))
    if session_levels.prev_day_high:
        bearish_levels.append((session_levels.prev_day_high, "Previous Day High", "key"))
    if session_levels.ny_high:
        bearish_levels.append((session_levels.ny_high, "NY High", "secondary"))
    if session_levels.today_high:
        bearish_levels.append((session_levels.today_high, "Today High", "secondary"))
    if session_levels.asia_low:
        bullish_levels.append((session_levels.asia_low, "Asia Low", "key"))
    if session_levels.london_low:
        bullish_levels.append((session_levels.london_low, "London Low", "key"))
    if session_levels.prev_day_low:
        bullish_levels.append((session_levels.prev_day_low, "Previous Day Low", "key"))
    if session_levels.ny_low:
        bullish_levels.append((session_levels.ny_low, "NY Low", "secondary"))
    if session_levels.today_low:
        bullish_levels.append((session_levels.today_low, "Today Low", "secondary"))

    best_sweep: Optional[Dict] = None
    best_candle_idx = -1

    for offset, candle in enumerate(reversed(recent_candles)):
        actual_idx = len(ohlcv_h1) - 1 - offset
        for level_price, level_name, level_type in bearish_levels:
            result = _check_bearish_sweep(candle, level_price, level_name, pip_value)
            if result:
                result["level_type"] = level_type
                if level_type == "key":
                    result["confidence"] = min(1.0, result["confidence"] * 1.2)
                if best_sweep is None or result["confidence"] > best_sweep.get("confidence", 0):
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
                    result["confidence"] = min(1.0, result["confidence"] * 1.2)
                if best_sweep is None or result["confidence"] > best_sweep.get("confidence", 0):
                    best_sweep = result
                    best_candle_idx = actual_idx
                    best_sweep["sweep_low"] = float(candle.get("low", 0))
                    best_sweep["sweep_high"] = float(candle.get("high", 0))
                    best_sweep["entry_price"] = current_price

    if not best_sweep:
        return None

    signal = SweepSignal(
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
            "session_levels": session_levels.to_dict(),
        },
    )
    return signal
