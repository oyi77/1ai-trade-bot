"""
quant_engine.py — Lightweight Quantitative Pattern Engine (Fuzzy Sliding Window)

Public API:
    analyze_quantitative_pattern(ohlcv_data, pattern_size=5, min_similarity=0.6)

Input:
    ohlcv_data: list[dict] with keys: timestamp, open, high, low, close, volume
    pattern_size: int, number of recent candles to treat as the target pattern.
    min_similarity: float 0..1, minimum fraction of matching positions for a window to count.

Output:
    dict with: pattern_size, series_length, window_count, match_count,
    green_pct, red_pct, doji_pct, next_candle_prob, dominant_next,
    confidence_score, quant_verdict, matching_method
"""
from __future__ import annotations
from collections import Counter
from typing import Any

_DEFAULT_PATTERN_SIZE: int = 5
_MIN_HISTORY: int = 15
_BODY_THRESHOLD_FRAC: float = 0.15
_DEFAULT_MIN_SIMILARITY: float = 0.6


def _classify_candle(open_: float, high: float, low: float, close: float) -> str:
    body = abs(close - open_)
    rng = (high - low) if (high - low) > 0 else (abs(close) * _BODY_THRESHOLD_FRAC or 1e-9)
    if rng <= 0:
        return "D"
    if body <= rng * _BODY_THRESHOLD_FRAC:
        return "D"
    if close > open_:
        return "G"
    elif close < open_:
        return "R"
    return "D"


def _build_series(ohlcv_data: list[dict[str, Any]]) -> list[str]:
    series: list[str] = []
    for bar in ohlcv_data:
        try:
            series.append(_classify_candle(
                float(bar["open"]), float(bar["high"]),
                float(bar["low"]), float(bar["close"]),
            ))
        except (TypeError, ValueError, KeyError):
            continue
    return series


def analyze_quantitative_pattern(
    ohlcv_data: list[dict[str, Any]],
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
    history = series[: len(series) - 1]

    match_count = 0
    next_candle_counts: Counter = Counter()
    n = len(history)

    for i in range(n - pattern_size):
        window = history[i : i + pattern_size]
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

    green_pct = prob["G"]
    red_pct = prob["R"]
    doji_pct = prob["D"]

    dominant = max(prob, key=prob.get)
    conf = prob[dominant] / 100.0
    dominant_pattern = {"G": "G", "R": "R", "D": "D"}.get(dominant, "?")

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
        "match_count": match_count, "green_pct": green_pct,
        "red_pct": red_pct, "doji_pct": doji_pct,
        "next_candle_counts": {
            "G": next_candle_counts.get("G", 0),
            "R": next_candle_counts.get("R", 0),
            "D": next_candle_counts.get("D", 0),
        },
        "next_candle_prob": prob,
        "dominant_next": dominant if match_count else None,
        "dominant_pattern": dominant_pattern,
        "confidence_score": round(conf, 4),
        "quant_verdict": verdict,
        "matching_method": "fuzzy",
        "min_similarity": min_similarity,
    }
