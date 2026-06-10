"""
fvg_detector.py — Fair Value Gap Mechanical Detector
=====================================================
Pure price-action FVG/Imbalance detection on any timeframe.
No AI needed — mechanical pattern recognition.

FVG Types:
  - Bullish FVG (Buy opportunity): Price gaps UP → wait for retrace into gap → BUY
  - Bearish FVG (Sell opportunity): Price gaps DOWN → wait for retrace into gap → SELL

Usage:
    from fvg_detector import detect_fvg, FVGSignal
    signals = detect_fvg(ohlcv_bars, timeframe="M5")
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional

# Tunables
MIN_FVG_SIZE_PIPS: float = 2.0
MAX_FVG_AGE_BARS: int = 30
MITIGATION_THRESHOLD: float = 0.80
CONFLUENCE_WINDOW: int = 3


@dataclass
class FVGZone:
    """A detected Fair Value Gap zone."""
    bar_index: int
    top: float
    bottom: float
    direction: str
    size_pips: float
    timestamp: Optional[int] = None
    mitigated: bool = False
    mitigation_bar: int = -1


@dataclass
class FVGSignal:
    """A tradable signal derived from FVG analysis."""
    direction: str           # "BUY" or "SELL"
    fvg_zone: FVGZone        # The FVG that generated this signal
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr_ratio: float
    confidence: float
    reasoning: str


def _calculate_pips(price: float, gap: float) -> float:
    """Convert price gap to pips based on asset type."""
    if price > 1000:        # Gold, indices
        return abs(gap) * 10
    elif price > 100:       # Crypto, some stocks
        return abs(gap)
    return abs(gap) * 100   # Forex


def _calculate_sl_pips(price: float) -> float:
    """Dynamic SL based on asset type."""
    if price > 1000:
        return 15.0   # Gold: 15 pips SL
    elif price > 100:
        return 50.0   # Crypto
    return 20.0       # Forex


def detect_fvg_zones(ohlcv_bars: list[dict], max_age: int = MAX_FVG_AGE_BARS) -> List[FVGZone]:
    """Detect all FVG zones from OHLCV bars."""
    zones: List[FVGZone] = []
    if len(ohlcv_bars) < 3:
        return zones

    n = len(ohlcv_bars)
    scan_start = max(0, n - max_age - 2)

    for i in range(scan_start, n - 1):
        try:
            curr = ohlcv_bars[i]
            prev = ohlcv_bars[i - 1] if i > 0 else None
            if prev is None:
                continue

            prev_high = float(prev["high"])
            prev_low = float(prev["low"])
            curr_high = float(curr["high"])
            curr_low = float(curr["low"])
            curr_open = float(curr.get("open", curr_low))
            curr_close = float(curr.get("close", curr_high))
        except (KeyError, ValueError, TypeError, IndexError):
            continue

        price = (curr_high + curr_low) / 2

        # Bullish FVG: current low > previous high (gap UP)
        if curr_low > prev_high:
            gap = curr_low - prev_high
            pips = _calculate_pips(price, gap)
            if pips >= MIN_FVG_SIZE_PIPS:
                zone = FVGZone(
                    bar_index=n - 1 - i,
                    top=curr_low,
                    bottom=prev_high,
                    direction="BUY",
                    size_pips=pips,
                )
                check_mitigation(zone, ohlcv_bars[i+1:])
                zones.append(zone)

        # Bearish FVG: current high < previous low (gap DOWN)
        elif curr_high < prev_low:
            gap = prev_low - curr_high
            pips = _calculate_pips(price, gap)
            if pips >= MIN_FVG_SIZE_PIPS:
                zone = FVGZone(
                    bar_index=n - 1 - i,
                    top=prev_low,
                    bottom=curr_high,
                    direction="SELL",
                    size_pips=pips,
                )
                check_mitigation(zone, ohlcv_bars[i+1:])
                zones.append(zone)

    return zones


def check_mitigation(zone: FVGZone, future_bars: list[dict]) -> bool:
    """Check if an FVG zone has been mitigated by subsequent price action."""
    for idx, bar in enumerate(future_bars):
        try:
            close = float(bar.get("close", bar.get("open", 0)))
        except (KeyError, ValueError, TypeError):
            continue

        if zone.direction == "BUY":
            # Bullish FVG mitigated if close goes BELOW the zone
            if close < zone.bottom:
                zone.mitigated = True
                zone.mitigation_bar = idx
                return True
        else:
            # Bearish FVG mitigated if close goes ABOVE the zone
            if close > zone.top:
                zone.mitigated = True
                zone.mitigation_bar = idx
                return True

    return False


def detect_fvg(ohlcv_bars: list[dict], timeframe: str = "M5",
               current_price: float = None, max_age: int = MAX_FVG_AGE_BARS) -> List[FVGSignal]:
    """Main entry point: detect FVGs and generate actionable signals."""
    if current_price is None and ohlcv_bars:
        current_price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))

    if not current_price:
        return []

    zones = detect_fvg_zones(ohlcv_bars, max_age)
    signals: List[FVGSignal] = []

    for zone in zones:
        # Only recent/unmitigated FVGs
        if zone.bar_index > max_age // 2:
            continue
        if zone.bar_index > 8:
            continue
        if zone.mitigated:
            continue

        # Check if price is near the zone (retracing into it)
        in_zone = zone.bottom <= current_price <= zone.top

        if zone.direction == "BUY" and current_price <= zone.top:
            # Bullish FVG: BUY at zone
            entry = min(current_price, zone.top)
            sl_pips = _calculate_sl_pips(current_price)
            sl = entry - sl_pips * (0.10 if current_price > 1000 else 0.0001)
            gap_height = zone.top - zone.bottom
            tp1 = entry + gap_height * 0.5
            tp2 = entry + gap_height
            tp3 = entry + gap_height * 2.0
            rr = gap_height / (sl_pips * (0.10 if current_price > 1000 else 0.0001))
            confidence = min(1.0, zone.size_pips / 15.0) if in_zone else min(0.5, zone.size_pips / 20.0)

            signals.append(FVGSignal(
                direction="BUY", fvg_zone=zone,
                entry=round(entry, 2), sl=round(sl, 2),
                tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2),
                rr_ratio=round(rr, 2), confidence=round(confidence, 2),
                reasoning=f"Bullish FVG ({zone.size_pips:.0f}pip) @ {zone.bottom:.2f}-{zone.top:.2f}"
            ))

        elif zone.direction == "SELL" and current_price >= zone.bottom:
            # Bearish FVG: SELL at zone
            entry = max(current_price, zone.bottom)
            sl_pips = _calculate_sl_pips(current_price)
            sl = entry + sl_pips * (0.10 if current_price > 1000 else 0.0001)
            gap_height = zone.top - zone.bottom
            tp1 = entry - gap_height * 0.5
            tp2 = entry - gap_height
            tp3 = entry - gap_height * 2.0
            rr = gap_height / (sl_pips * (0.10 if current_price > 1000 else 0.0001))
            confidence = min(1.0, zone.size_pips / 15.0) if in_zone else min(0.5, zone.size_pips / 20.0)

            signals.append(FVGSignal(
                direction="SELL", fvg_zone=zone,
                entry=round(entry, 2), sl=round(sl, 2),
                tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2),
                rr_ratio=round(rr, 2), confidence=round(confidence, 2),
                reasoning=f"Bearish FVG ({zone.size_pips:.0f}pip) @ {zone.bottom:.2f}-{zone.top:.2f}"
            ))

    return signals


def fvg_to_dict(signal: FVGSignal) -> dict:
    """Convert FVGSignal to dict for serialization."""
    return {
        "direction": signal.direction,
        "entry": signal.entry, "sl": signal.sl,
        "tp1": signal.tp1, "tp2": signal.tp2, "tp3": signal.tp3,
        "rr_ratio": signal.rr_ratio, "confidence": signal.confidence,
        "reasoning": signal.reasoning,
        "fvg_zone": {
            "top": signal.fvg_zone.top, "bottom": signal.fvg_zone.bottom,
            "size_pips": signal.fvg_zone.size_pips,
            "direction": signal.fvg_zone.direction,
            "mitigated": signal.fvg_zone.mitigated,
            "bar_index": signal.fvg_zone.bar_index,
        }
    }
