"""
whale.py — Whale/Bandar Detection Engine

Detects institutional accumulation/distribution signals from OHLCV data:

- Volume spikes (volume >> moving average)
- Accumulation/Distribution (price declining but volume increasing)
- Large candle body/wick anomalies
- On-Balance Volume (OBV) divergence
- Money Flow Index (MFI) analysis
- Block trade / whale candle patterns

Works across stocks, crypto, forex, and commodities.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from tradebot.engines.base import Engine
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


# ── Data Structures ────────────────────────────────────────────────────────


@dataclass
class WhaleSignal:
    """Whale/bandar detection result."""
    symbol: str
    detection_type: str  # "accumulation", "distribution", "volume_spike", "whale_candle", "obv_divergence", "mfi_divergence"
    confidence: float  # 0.0 - 1.0
    strength: str  # "S-TIER", "A", "B", "C"
    volume_ratio: float  # current volume / avg volume
    price_change_pct: float
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Detection Functions ─────────────────────────────────────────────────────


def _sma(data: list[float], period: int) -> list[float]:
    """Simple moving average."""
    result: list[float] = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(0.0)
        else:
            result.append(sum(data[i - period + 1:i + 1]) / period)
    return result


def _ema(data: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if not data:
        return []
    multiplier = 2.0 / (period + 1)
    result: list[float] = [data[0]]
    for i in range(1, len(data)):
        result.append((data[i] - result[-1]) * multiplier + result[-1])
    return result


def _detect_volume_spikes(
    volumes: list[float],
    period: int = 20,
    threshold: float = 2.0,
) -> list[dict[str, Any]]:
    """Detect volume spikes significantly above average.

    Returns list of spike events with index, volume ratio, and severity.
    """
    if len(volumes) < period + 1:
        return []

    avg_volumes = _sma(volumes, period)
    spikes: list[dict[str, Any]] = []

    for i in range(period, len(volumes)):
        avg = avg_volumes[i]
        if avg <= 0:
            continue
        ratio = volumes[i] / avg
        if ratio >= threshold:
            severity = (
                "S-TIER" if ratio >= 5.0
                else "A" if ratio >= 3.5
                else "B" if ratio >= 2.5
                else "C"
            )
            spikes.append({
                "index": i,
                "volume": volumes[i],
                "avg_volume": avg,
                "ratio": ratio,
                "severity": severity,
            })

    return spikes


def _detect_accumulation_distribution(
    closes: list[float],
    volumes: list[float],
    lookback: int = 14,
) -> list[dict[str, Any]]:
    """Detect accumulation (price down/sideways + volume up) and distribution.

    Accumulation: price declining or flat, volume rising → smart money buying.
    Distribution: price rising or flat, volume declining → smart money selling.
    """
    if len(closes) < lookback * 2 or len(volumes) < lookback * 2:
        return []

    signals: list[dict[str, Any]] = []
    half = lookback // 2

    for i in range(lookback, len(closes)):
        recent_close = closes[i]
        prev_close = closes[i - lookback]
        price_change_pct = ((recent_close - prev_close) / prev_close) * 100 if prev_close > 0 else 0

        recent_vol = volumes[i - half:i + 1]
        earlier_vol = volumes[i - lookback:i - half]

        avg_recent_vol = sum(recent_vol) / len(recent_vol) if recent_vol else 0
        avg_earlier_vol = sum(earlier_vol) / len(earlier_vol) if earlier_vol else 0

        if avg_earlier_vol <= 0:
            continue

        vol_change_pct = ((avg_recent_vol - avg_earlier_vol) / avg_earlier_vol) * 100

        # Accumulation: price flat/down (-5% to 2%), volume up > 30%
        if -5 <= price_change_pct <= 2 and vol_change_pct > 30:
            strength = "A" if vol_change_pct > 80 else "B" if vol_change_pct > 50 else "C"
            signals.append({
                "index": i,
                "type": "accumulation",
                "price_change_pct": price_change_pct,
                "vol_change_pct": vol_change_pct,
                "strength": strength,
                "detail": f"Price {price_change_pct:+.1f}% with volume +{vol_change_pct:.0f}% — smart money accumulation",
            })

        # Distribution: price flat/up (-1% to 6%), volume down > 30%
        if -1 <= price_change_pct <= 6 and vol_change_pct < -30:
            strength = "A" if vol_change_pct < -60 else "B" if vol_change_pct < -45 else "C"
            signals.append({
                "index": i,
                "type": "distribution",
                "price_change_pct": price_change_pct,
                "vol_change_pct": vol_change_pct,
                "strength": strength,
                "detail": f"Price {price_change_pct:+.1f}% with volume {vol_change_pct:.0f}% — distribution/sell-off",
            })

    return signals


def _detect_whale_candles(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    lookback: int = 20,
) -> list[dict[str, Any]]:
    """Detect disproportionately large candles (whale activity)."""
    if len(closes) < lookback + 1:
        return []

    avg_ranges: list[float] = []
    for i in range(len(closes)):
        avg_ranges.append(highs[i] - lows[i])

    avg_range = _sma(avg_ranges, lookback)
    results: list[dict[str, Any]] = []

    for i in range(lookback, len(closes)):
        candle_range = highs[i] - lows[i]
        body = abs(closes[i] - opens[i])
        upper_wick = highs[i] - max(opens[i], closes[i])
        lower_wick = min(opens[i], closes[i]) - lows[i]

        avg = avg_range[i]
        if avg <= 0:
            continue

        range_ratio = candle_range / avg
        if range_ratio < 2.0:
            continue

        # Determine candle type
        is_bullish = closes[i] > opens[i]
        body_ratio = body / candle_range if candle_range > 0 else 0
        is_long_wick = (upper_wick > body * 1.5 or lower_wick > body * 1.5) if body > 0 else False

        strength = (
            "S-TIER" if range_ratio >= 4.0
            else "A" if range_ratio >= 3.0
            else "B" if range_ratio >= 2.5
            else "C"
        )

        detail_parts: list[str] = []
        if is_bullish:
            detail_parts.append("🚀 BULLISH")
        else:
            detail_parts.append("🔻 BEARISH")

        if is_long_wick:
            if upper_wick > body * 1.5:
                detail_parts.append("long upper wick (rejection)")
            if lower_wick > body * 1.5 and not is_bullish:
                detail_parts.append("long lower wick (buyer stepping in)")

        detail_parts.append(f"range {range_ratio:.1f}x avg")

        # Check volume confirmation
        if i < len(volumes):
            avg_vol = sum(volumes[max(0, i - lookback):i]) / lookback if lookback > 0 else 0
            if avg_vol > 0 and volumes[i] / avg_vol > 1.5:
                detail_parts.append("volume confirmed")

        results.append({
            "index": i,
            "type": "whale_candle",
            "range_ratio": range_ratio,
            "is_bullish": is_bullish,
            "body_ratio": body_ratio,
            "strength": strength,
            "volume_ratio": volumes[i] / avg_vol if i < len(volumes) and avg_vol > 0 else 0,
            "detail": " ".join(detail_parts),
        })

    return results


def _obv(closes: list[float], volumes: list[float]) -> list[float]:
    """On-Balance Volume calculation."""
    if len(closes) < 2:
        return [0.0] * len(closes)

    obv_values: list[float] = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_values.append(obv_values[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv_values.append(obv_values[-1] - volumes[i])
        else:
            obv_values.append(obv_values[-1])
    return obv_values


def _detect_obv_divergence(
    closes: list[float],
    volumes: list[float],
    lookback: int = 14,
) -> list[dict[str, Any]]:
    """Detect divergence between price and On-Balance Volume.

    Bullish divergence: price makes lower low, OBV makes higher low.
    Bearish divergence: price makes higher high, OBV makes lower high.
    """
    if len(closes) < lookback * 2 or len(volumes) < lookback * 2:
        return []

    obv_values = _obv(closes, volumes)
    results: list[dict[str, Any]] = []

    for i in range(lookback * 2, len(closes)):
        price_slice = closes[i - lookback:i + 1]
        obv_slice = obv_values[i - lookback:i + 1]

        price_min = min(price_slice)
        price_max = max(price_slice)
        obv_min = min(obv_slice)
        obv_max = max(obv_slice)

        # Find last occurrence of price min/max and OBV min/max in the slice
        price_low_idx = price_slice.index(price_min)
        obv_low_idx = obv_slice.index(obv_min)
        price_high_idx = price_slice.index(price_max)
        obv_high_idx = obv_slice.index(obv_max)

        # Bullish divergence: price makes lower low recently, OBV made higher low earlier
        if price_low_idx > obv_low_idx and price_low_idx >= len(price_slice) - 3:
            # Price went lower recently than before, but OBV didn't follow
            price_low = price_min
            obv_at_price_low = obv_slice[price_low_idx]
            obv_prev = obv_slice[obv_low_idx]
            if obv_at_price_low > obv_prev:
                results.append({
                    "index": i,
                    "type": "bullish_obv_divergence",
                    "strength": "A",
                    "detail": f"📈 Bullish OBV divergence — price made lower low but OBV held higher low",
                    "price": price_low,
                })

        # Bearish divergence: price makes higher high recently, OBV made lower high earlier
        if price_high_idx > obv_high_idx and price_high_idx >= len(price_slice) - 3:
            price_high = price_max
            obv_at_price_high = obv_slice[price_high_idx]
            obv_prev = obv_slice[obv_high_idx]
            if obv_at_price_high < obv_prev:
                results.append({
                    "index": i,
                    "type": "bearish_obv_divergence",
                    "strength": "A",
                    "detail": f"📉 Bearish OBV divergence — price made higher high but OBV failed to confirm",
                    "price": price_high,
                })

    return results


def _mfi(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    period: int = 14,
) -> list[float]:
    """Money Flow Index calculation."""
    if len(closes) < period + 1:
        return [50.0] * len(closes)

    mfi_values: list[float] = [50.0] * period
    typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    money_flow = [tp * v for tp, v in zip(typical_prices, volumes)]

    for i in range(period, len(closes)):
        positive_flow = 0.0
        negative_flow = 0.0
        for j in range(i - period + 1, i + 1):
            if typical_prices[j] > typical_prices[j - 1]:
                positive_flow += money_flow[j]
            else:
                negative_flow += money_flow[j]

        if negative_flow == 0:
            mfi_values.append(100.0)
        else:
            mfi_ratio = positive_flow / negative_flow
            mfi_values.append(100.0 - (100.0 / (1.0 + mfi_ratio)))

    return mfi_values


def _detect_mfi_divergence(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    lookback: int = 14,
) -> list[dict[str, Any]]:
    """Detect MFI divergence — price and MFI moving in opposite directions."""
    if len(closes) < lookback * 2:
        return []

    mfi_values = _mfi(highs, lows, closes, volumes, period=lookback)
    if len(mfi_values) != len(closes):
        return []

    results: list[dict[str, Any]] = []

    for i in range(lookback * 2, len(closes)):
        price_slice = closes[i - lookback:i + 1]
        mfi_slice = mfi_values[i - lookback:i + 1]

        price_min = min(price_slice)
        price_max = max(price_slice)
        mfi_min = min(mfi_slice)
        mfi_max = max(mfi_slice)

        price_low_idx = price_slice.index(price_min)
        mfi_low_idx = mfi_slice.index(mfi_min)
        price_high_idx = price_slice.index(price_max)
        mfi_high_idx = mfi_slice.index(mfi_max)

        # Oversold bullish divergence
        if mfi_slice[-1] < 30 and price_low_idx > mfi_low_idx:
            results.append({
                "index": i,
                "type": "mfi_oversold_divergence",
                "strength": "A" if mfi_slice[-1] < 20 else "B",
                "detail": f"🟢 MFI oversold ({mfi_slice[-1]:.0f}) with bullish divergence — accumulation zone",
                "mfi": mfi_slice[-1],
            })

        # Overbought bearish divergence
        if mfi_slice[-1] > 70 and price_high_idx > mfi_high_idx:
            results.append({
                "index": i,
                "type": "mfi_overbought_divergence",
                "strength": "A" if mfi_slice[-1] > 80 else "B",
                "detail": f"🔴 MFI overbought ({mfi_slice[-1]:.0f}) with bearish divergence — distribution zone",
                "mfi": mfi_slice[-1],
            })

    return results


# ── Main Analyzer ──────────────────────────────────────────────────────────


def analyze_whale_activity(
    symbol: str,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> list[WhaleSignal]:
    """Run all whale detection algorithms and return ranked results."""
    if len(closes) < 30:
        return []

    signals: list[WhaleSignal] = []

    # 1. Volume spikes
    spikes = _detect_volume_spikes(volumes, period=20, threshold=2.0)
    for s in spikes[-3:]:  # last 3 spikes
        confidence = min(0.95, 0.5 + s["ratio"] * 0.1)
        signals.append(WhaleSignal(
            symbol=symbol,
            detection_type="volume_spike",
            confidence=confidence,
            strength=s["severity"],
            volume_ratio=s["ratio"],
            price_change_pct=0.0,
            detail=f"📊 Volume spike {s['ratio']:.1f}x avg ({s['severity']}) @ candle #{s['index']}",
            metadata={"index": s["index"], "volume": s["volume"], "avg_volume": s["avg_volume"]},
        ))

    # 2. Accumulation / Distribution
    acc_dist = _detect_accumulation_distribution(closes, volumes, lookback=14)
    for s in acc_dist[-3:]:
        confidence = 0.7 if s["strength"] == "A" else (0.6 if s["strength"] == "B" else 0.45)
        icon = "🟢" if s["type"] == "accumulation" else "🔴"
        signals.append(WhaleSignal(
            symbol=symbol,
            detection_type=s["type"],
            confidence=confidence,
            strength=s["strength"],
            volume_ratio=0.0,
            price_change_pct=s["price_change_pct"],
            detail=f"{icon} {s['detail']}",
            metadata=s,
        ))

    # 3. Whale candles
    candles = _detect_whale_candles(opens, highs, lows, closes, volumes, lookback=20)
    for s in candles[-3:]:
        confidence = min(0.9, 0.5 + s["range_ratio"] * 0.1)
        signals.append(WhaleSignal(
            symbol=symbol,
            detection_type="whale_candle",
            confidence=confidence,
            strength=s["strength"],
            volume_ratio=s["volume_ratio"],
            price_change_pct=0.0,
            detail=f"🐋 {s['detail']}",
            metadata=s,
        ))

    # 4. OBV divergence
    obv_divs = _detect_obv_divergence(closes, volumes, lookback=14)
    for s in obv_divs[-2:]:
        signals.append(WhaleSignal(
            symbol=symbol,
            detection_type=s["type"],
            confidence=0.75,
            strength=s["strength"],
            volume_ratio=0.0,
            price_change_pct=0.0,
            detail=s["detail"],
            metadata=s,
        ))

    # 5. MFI divergence
    mfi_divs = _detect_mfi_divergence(closes, highs, lows, volumes, lookback=14)
    for s in mfi_divs[-2:]:
        signals.append(WhaleSignal(
            symbol=symbol,
            detection_type=s["type"],
            confidence=0.72,
            strength=s["strength"],
            volume_ratio=0.0,
            price_change_pct=0.0,
            detail=s["detail"],
            metadata=s,
        ))

    # Sort by confidence descending
    signals.sort(key=lambda ws: ws.confidence, reverse=True)
    return signals


# ── Formatting for Telegram ────────────────────────────────────────────────


def format_whale_report(symbol: str, whale_signals: list[WhaleSignal]) -> str:
    """Format whale detection results for Telegram message."""
    if not whale_signals:
        return f"🐋 <b>{symbol}</b>\n━━━━━━━━━━━━\nNo whale/bandar activity detected."

    best = whale_signals[0]

    header_icon = {
        "accumulation": "🟢",
        "distribution": "🔴",
        "volume_spike": "📊",
        "whale_candle": "🐋",
        "bullish_obv_divergence": "📈",
        "bearish_obv_divergence": "📉",
        "mfi_oversold_divergence": "🟢",
        "mfi_overbought_divergence": "🔴",
    }.get(best.detection_type, "🐋")

    lines = [
        f"{header_icon} <b>{symbol}</b> — Whale/Bandar Scan",
        f"━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>{best.detection_type.replace('_', ' ').title()}</b>",
        f"📊 Confidence: {best.confidence:.0%} | Grade: <b>{best.strength}</b>",
        f"💬 {best.detail}",
        f"",
        f"━━━━ <b>All Signals</b> ━━━━",
    ]

    for i, ws in enumerate(whale_signals, 1):
        icon = {
            "accumulation": "🟢", "distribution": "🔴",
            "volume_spike": "📊", "whale_candle": "🐋",
            "bullish_obv_divergence": "📈", "bearish_obv_divergence": "📉",
            "mfi_oversold_divergence": "🟢", "mfi_overbought_divergence": "🔴",
        }.get(ws.detection_type, "•")
        lines.append(
            f"{icon} <b>#{i}</b> {ws.detection_type.replace('_', ' ').title()} "
            f"[{ws.strength}] — {ws.confidence:.0%}\n"
            f"   {ws.detail}"
        )

    lines.append("")
    lines.append("⚠️ NFA — Not Financial Advice")
    return "\n".join(lines)


# ── Engine Interface (for Registry compatibility) ─────────────────────────


class WhaleEngine(Engine):
    """Whale/Bandar detection engine — volume & smart money analysis.

    Conforms to the Engine interface for Registry auto-discovery.
    The real analysis is done via :func:`analyze_whale_activity` which
    can be called directly with OHLCV data from any source.
    """

    @property
    def name(self) -> str:
        return "whale_detector"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Adaptive analyze — requires OHLCV data via :func:`analyze_whale_activity`.

        This engine works best with OHLCV data from the aggregator,
        not raw ticks. Call :func:`analyze_whale_activity` directly
        with OHLCV arrays for full analysis.
        """
        return None  # Whale analysis needs OHLCV, not raw ticks