"""
liquidity_zones.py — Liquidity Zone Mapper (Supply/Demand, Order Blocks, Equilibrium)
=====================================================================================
Algorithmic detection of institutional liquidity zones for TP targeting.

Zones detected:
  1. Order Blocks (OB): Last candle before a strong impulsive move
  2. Supply Zone: Untested resistance area above current price
  3. Demand Zone: Untested support area below current price
  4. Fair Value Gap (FVG): Imbalance zones (if fvg_detector available)
  5. Equilibrium: 50% midpoint of key session ranges

Usage:
    from liquidity_zones import map_zones, find_tp_targets, LiquidityMap
    zones = map_zones(ohlcv_h1, session_levels, current_price, sweep_direction)
    tp1, tp2 = find_tp_targets(zones, sweep_direction)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_levels import SessionLevels, _pip_value, _parse_timestamp

try:
    from fvg_detector import detect_fvg
    HAS_FVG = True
except ImportError:
    HAS_FVG = False


@dataclass
class LiquidityZone:
    zone_type: str
    top: float
    bottom: float
    direction: str
    mitigated: bool = False
    strength: float = 0.5
    distance_pips: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    def to_dict(self) -> dict:
        return {
            "zone_type": self.zone_type, "top": self.top, "bottom": self.bottom,
            "midpoint": round(self.midpoint, 2), "direction": self.direction,
            "mitigated": self.mitigated, "strength": round(self.strength, 3),
            "distance_pips": self.distance_pips, "metadata": self.metadata,
        }


@dataclass
class LiquidityMap:
    current_price: float
    sweep_direction: str
    zones: List[LiquidityZone] = field(default_factory=list)
    equilibrium: Optional[LiquidityZone] = None
    nearest_target: Optional[LiquidityZone] = None
    secondary_target: Optional[LiquidityZone] = None

    def to_dict(self) -> dict:
        return {
            "current_price": self.current_price, "sweep_direction": self.sweep_direction,
            "zones": [z.to_dict() for z in self.zones],
            "equilibrium": self.equilibrium.to_dict() if self.equilibrium else None,
            "nearest_target": self.nearest_target.to_dict() if self.nearest_target else None,
            "secondary_target": self.secondary_target.to_dict() if self.secondary_target else None,
        }


def _detect_order_blocks(ohlcv_h1: List[dict], current_price: float) -> List[LiquidityZone]:
    zones = []
    if len(ohlcv_h1) < 5:
        return zones
    for i in range(len(ohlcv_h1) - 3):
        try:
            c0 = ohlcv_h1[i]
            c1 = ohlcv_h1[i + 1]
            c2 = ohlcv_h1[i + 2]
            o0, h0, l0, cl0 = float(c0["open"]), float(c0["high"]), float(c0["low"]), float(c0["close"])
            cl1 = float(c1["close"])
            cl2 = float(c2["close"])
        except (KeyError, ValueError, TypeError):
            continue
        body0 = abs(cl0 - o0)
        range0 = h0 - l0
        if range0 <= 0:
            continue
        move1 = cl1 - cl0
        move2 = cl2 - cl1
        if move1 > 0 and move2 > 0 and (move1 + move2) > range0:
            zones.append(LiquidityZone(
                zone_type="OB", top=h0, bottom=l0, direction="BULLISH",
                strength=min(1.0, (move1 + move2) / (range0 * 3)),
                mitigated=min(cl1, cl2) < l0,
            ))
        elif move1 < 0 and move2 < 0 and abs(move1 + move2) > range0:
            zones.append(LiquidityZone(
                zone_type="OB", top=h0, bottom=l0, direction="BEARISH",
                strength=min(1.0, abs(move1 + move2) / (range0 * 3)),
                mitigated=max(cl1, cl2) > h0,
            ))
    return zones


def _detect_supply_demand(ohlcv_h1: List[dict], current_price: float) -> List[LiquidityZone]:
    zones = []
    if len(ohlcv_h1) < 6:
        return zones
    swing_highs = []
    swing_lows = []
    for i in range(2, len(ohlcv_h1) - 2):
        try:
            prev2_h = float(ohlcv_h1[i - 2]["high"])
            prev1_h = float(ohlcv_h1[i - 1]["high"])
            curr_h = float(ohlcv_h1[i]["high"])
            next1_h = float(ohlcv_h1[i + 1]["high"])
            next2_h = float(ohlcv_h1[i + 2]["high"])
            prev2_l = float(ohlcv_h1[i - 2]["low"])
            prev1_l = float(ohlcv_h1[i - 1]["low"])
            curr_l = float(ohlcv_h1[i]["low"])
            next1_l = float(ohlcv_h1[i + 1]["low"])
            next2_l = float(ohlcv_h1[i + 2]["low"])
        except (KeyError, ValueError, TypeError):
            continue
        if curr_h > max(prev2_h, prev1_h, next1_h, next2_h):
            swing_highs.append({"price": curr_h, "index": i})
        if curr_l < min(prev2_l, prev1_l, next1_l, next2_l):
            swing_lows.append({"price": curr_l, "index": i})
    for sh in swing_highs:
        if sh["price"] > current_price:
            tests = sum(1 for b in ohlcv_h1 if abs(float(b.get("high", 0)) - sh["price"]) < 2.0)
            zones.append(LiquidityZone(
                zone_type="SUPPLY", top=sh["price"] + 2.0, bottom=sh["price"] - 2.0,
                direction="BEARISH", mitigated=tests > 3,
                strength=1.0 if tests <= 2 else 0.5,
            ))
    for sl in swing_lows:
        if sl["price"] < current_price:
            tests = sum(1 for b in ohlcv_h1 if abs(float(b.get("low", 0)) - sl["price"]) < 2.0)
            zones.append(LiquidityZone(
                zone_type="DEMAND", top=sl["price"] + 2.0, bottom=sl["price"] - 2.0,
                direction="BULLISH", mitigated=tests > 3,
                strength=1.0 if tests <= 2 else 0.5,
            ))
    return zones


def _detect_fvg_zones(ohlcv_h1: List[dict], current_price: float) -> List[LiquidityZone]:
    if not HAS_FVG:
        return []
    try:
        fvg_signals = detect_fvg(ohlcv_h1, timeframe="H1", current_price=current_price)
    except Exception:
        return []
    zones = []
    for sig in fvg_signals:
        zones.append(LiquidityZone(
            zone_type="FVG", top=sig.fvg_zone.top, bottom=sig.fvg_zone.bottom,
            direction=sig.direction.upper(), mitigated=sig.fvg_zone.mitigated,
            strength=min(1.0, sig.confidence),
        ))
    return zones


def _calculate_equilibrium(session_levels: SessionLevels, current_price: float) -> Optional[LiquidityZone]:
    if session_levels.asia_high and session_levels.asia_low:
        midpoint = (session_levels.asia_high + session_levels.asia_low) / 2
        return LiquidityZone(
            zone_type="EQUILIBRIUM", top=midpoint + 1.0, bottom=midpoint - 1.0,
            direction="NEUTRAL", mitigated=abs(current_price - midpoint) < 3.0, strength=0.7,
        )
    if session_levels.london_high and session_levels.london_low:
        midpoint = (session_levels.london_high + session_levels.london_low) / 2
        return LiquidityZone(
            zone_type="EQUILIBRIUM", top=midpoint + 1.0, bottom=midpoint - 1.0,
            direction="NEUTRAL", mitigated=abs(current_price - midpoint) < 3.0, strength=0.5,
        )
    return None


def map_zones(ohlcv_h1: List[dict], session_levels: SessionLevels,
              current_price: float, sweep_direction: str) -> LiquidityMap:
    pip_val = _pip_value(current_price)
    all_zones: List[LiquidityZone] = []
    all_zones.extend(_detect_order_blocks(ohlcv_h1, current_price))
    all_zones.extend(_detect_supply_demand(ohlcv_h1, current_price))
    all_zones.extend(_detect_fvg_zones(ohlcv_h1, current_price))
    eq = _calculate_equilibrium(session_levels, current_price)

    for zone in all_zones:
        zone.distance_pips = abs(current_price - zone.midpoint) / pip_val

    if sweep_direction == "BEARISH":
        target_zones = [z for z in all_zones
                        if z.midpoint < current_price
                        and z.direction in ("BULLISH", "NEUTRAL")
                        and not z.mitigated]
    else:
        target_zones = [z for z in all_zones
                        if z.midpoint > current_price
                        and z.direction in ("BEARISH", "NEUTRAL")
                        and not z.mitigated]

    target_zones.sort(key=lambda z: z.distance_pips)
    ob_fvg_targets = [z for z in target_zones if z.zone_type in ("OB", "FVG")]
    ob_fvg_targets.sort(key=lambda z: z.distance_pips)

    nearest_target = target_zones[0] if target_zones else None
    secondary_target = ob_fvg_targets[0] if ob_fvg_targets else (
        target_zones[1] if len(target_zones) > 1 else None
    )

    return LiquidityMap(
        current_price=current_price, sweep_direction=sweep_direction,
        zones=all_zones, equilibrium=eq,
        nearest_target=nearest_target, secondary_target=secondary_target,
    )


def find_tp_targets(liquidity_map: LiquidityMap, sweep_direction: str) -> Tuple[Optional[float], Optional[float]]:
    tp1 = None
    tp2 = None
    eq = liquidity_map.equilibrium
    if eq and not eq.mitigated:
        if sweep_direction == "BEARISH" and eq.midpoint < liquidity_map.current_price:
            tp1 = eq.midpoint
        elif sweep_direction == "BULLISH" and eq.midpoint > liquidity_map.current_price:
            tp1 = eq.midpoint
    if not tp1 and liquidity_map.nearest_target:
        tp1 = liquidity_map.nearest_target.midpoint
    if liquidity_map.secondary_target:
        tp2 = liquidity_map.secondary_target.midpoint
    elif liquidity_map.nearest_target:
        tp2 = liquidity_map.nearest_target.midpoint
    elif tp1:
        if sweep_direction in ("SELL", "BEARISH"):
            tp2 = tp1 - abs(liquidity_map.current_price - tp1) * 0.5
        else:
            tp2 = tp1 + abs(tp1 - liquidity_map.current_price) * 0.5
    return tp1, tp2
