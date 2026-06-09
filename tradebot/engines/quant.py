"""
quant.py — Quantitative Pattern Engine

Migrated from: scripts/quant_engine.py
Conforms to: tradebot.engines.base.Engine interface

Fuzzy sliding-window pattern matcher that identifies historical candle
patterns similar to the current formation, then predicts the next
candle outcome based on what happened historically.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

_DEFAULT_PATTERN_SIZE: int = int(getattr(settings, "QUANT_PATTERN_SIZE", 5))
_MIN_HISTORY: int = int(getattr(settings, "QUANT_MIN_HISTORY", 15))
_BODY_THRESHOLD_FRAC: float = float(getattr(settings, "QUANT_BODY_THRESHOLD", 0.15))
_DEFAULT_MIN_SIMILARITY: float = float(getattr(settings, "QUANT_MIN_SIMILARITY", 0.6))


def _classify_candle(open_: float, high: float, low: float, close: float) -> str:
    """Classify candle as Green (G), Red (R), or Doji (D)."""
    body = abs(close - open_)
    rng = max(high - low, abs(close) * _BODY_THRESHOLD_FRAC, 1e-9)
    if body <= rng * _BODY_THRESHOLD_FRAC:
        return "D"
    return "G" if close > open_ else "R" if close < open_ else "D"


def _build_series(ohlcv_data: list[dict]) -> list[str]:
    series: list[str] = []
    for bar in ohlcv_data:
        try:
            series.append(_classify_candle(
                float(bar.get("open", 0)), float(bar.get("high", 0)),
                float(bar.get("low", 0)), float(bar.get("close", 0)),
            ))
        except (TypeError, ValueError, KeyError):
            continue
    return series


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price, "high": t.price,
            "low": t.price, "close": t.price,
            "volume": 1,
        })
    return bars


def _analyze_quantitative_pattern(
    ohlcv_data: list[dict],
    pattern_size: int = _DEFAULT_PATTERN_SIZE,
    min_similarity: float = _DEFAULT_MIN_SIMILARITY,
) -> dict[str, Any]:
    """Fuzzy sliding-window pattern matcher."""
    series = _build_series(ohlcv_data)

    if len(series) < _MIN_HISTORY:
        return {
            "pattern_size": pattern_size, "series_length": len(series),
            "error": "insufficient_history", "window_count": 0, "match_count": 0,
            "green_pct": 0.0, "red_pct": 0.0, "doji_pct": 0.0,
            "next_candle_prob": {"G": 0.0, "R": 0.0, "D": 0.0},
            "dominant_next": None, "dominant_pattern": "?",
            "confidence_score": 0.0, "quant_verdict": "INSUFFICIENT_DATA",
            "matching_method": "fuzzy",
        }

    if pattern_size < 3:
        pattern_size = 3
    if len(series) - 1 < pattern_size:
        return {
            "pattern_size": pattern_size, "series_length": len(series),
            "error": "pattern_too_large", "window_count": 0, "match_count": 0,
            "green_pct": 0.0, "red_pct": 0.0, "doji_pct": 0.0,
            "next_candle_prob": {"G": 0.0, "R": 0.0, "D": 0.0},
            "dominant_next": None, "dominant_pattern": "?",
            "confidence_score": 0.0, "quant_verdict": "INSUFFICIENT_DATA",
            "matching_method": "fuzzy",
        }

    target = series[-pattern_size:]
    history = series[:len(series) - 1]

    match_count = 0
    next_candle_counts: Counter = Counter()
    n = len(history)

    for i in range(n - pattern_size):
        window = history[i:i + pattern_size]
        same = sum(1 for a, b in zip(window, target) if a == b)
        similarity = same / pattern_size
        if similarity >= min_similarity:
            nxt = history[i + pattern_size]
            next_candle_counts[nxt] += 1
            match_count += 1

    total = sum(next_candle_counts.values()) or 1
    prob = {
        "G": round(next_candle_counts.get("G", 0) / total * 100, 2),
        "R": round(next_candle_counts.get("R", 0) / total * 100, 2),
        "D": round(next_candle_counts.get("D", 0) / total * 100, 2),
    }

    dominant = max(prob, key=prob.get)
    conf = prob[dominant] / 100.0

    if match_count == 0:
        verdict = "NO_HISTORICAL_MATCH"
    elif dominant == "G":
        verdict = "BUY_BIAS_HISTORICAL"
    elif dominant == "R":
        verdict = "SELL_BIAS_HISTORICAL"
    else:
        verdict = "NEUTRAL_HISTORICAL"

    return {
        "pattern_size": pattern_size, "series_length": len(series),
        "target_pattern": target, "window_count": max(0, n - pattern_size),
        "match_count": match_count, "green_pct": prob["G"],
        "red_pct": prob["R"], "doji_pct": prob["D"],
        "next_candle_counts": {
            "G": next_candle_counts.get("G", 0),
            "R": next_candle_counts.get("R", 0),
            "D": next_candle_counts.get("D", 0),
        },
        "next_candle_prob": prob,
        "dominant_next": dominant if match_count else None,
        "dominant_pattern": {"G": "G", "R": "R", "D": "D"}.get(dominant, "?"),
        "confidence_score": round(conf, 4),
        "quant_verdict": verdict,
        "matching_method": "fuzzy",
        "min_similarity": min_similarity,
    }


class QuantEngine(Engine):
    """Quantitative Pattern Engine.

    Uses a fuzzy sliding-window approach to find historical patterns
    that match the current candle formation, then probabilistically
    predicts the next candle direction.
    """

    def __init__(self) -> None:
        self._pattern_size: int = int(getattr(settings, "QUANT_PATTERN_SIZE", _DEFAULT_PATTERN_SIZE))  # noqa: E501
        self._min_similarity: float = float(getattr(settings, "QUANT_MIN_SIMILARITY", _DEFAULT_MIN_SIMILARITY))  # noqa: E501
        self._min_confidence: float = float(getattr(settings, "QUANT_MIN_CONFIDENCE", 0.55))

    @property
    def name(self) -> str:
        return "quant_pattern"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for quantitative pattern match."""
        if not ticks or len(ticks) < _MIN_HISTORY:
            LOG.debug("Quant: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            result = _analyze_quantitative_pattern(
                ohlcv, pattern_size=self._pattern_size, min_similarity=self._min_similarity,
            )

            if result.get("error") or result.get("quant_verdict") in (
                "INSUFFICIENT_DATA", "NO_HISTORICAL_MATCH", "NEUTRAL_HISTORICAL",
            ):
                return None

            verdict = result["quant_verdict"]
            conf = result["confidence_score"]

            if conf < self._min_confidence:
                return None

            direction = "CALL" if verdict == "BUY_BIAS_HISTORICAL" else "PUT"
            current_price = ticks[-1].price

            return Signal(
                symbol="XAUUSD",
                direction=direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=conf,
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if conf >= 0.7 else (
                    SignalGrade.MODERATE if conf >= 0.5 else SignalGrade.WEAK
                ),
                metadata={
                    "engine": self.name,
                    "match_count": result["match_count"],
                    "window_count": result["window_count"],
                    "green_pct": result["green_pct"],
                    "red_pct": result["red_pct"],
                    "target_pattern": result.get("target_pattern"),
                    "dominant_next": result.get("dominant_next"),
                    "next_candle_prob": result.get("next_candle_prob"),
                    "pattern_size": self._pattern_size,
                    "min_similarity": self._min_similarity,
                },
            )
        except Exception as exc:
            LOG.warning("Quant engine error: %s", exc)
            raise SignalError("Quant analysis failed", details={"error": str(exc)}) from exc
