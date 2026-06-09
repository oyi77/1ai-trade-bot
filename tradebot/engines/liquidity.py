"""
liquidity.py — Liquidity Zone Mapping Engine

Migrated from: scripts/liquidity_zones.py
Conforms to: tradebot.engines.base.Engine interface
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


@dataclass
class LiquidityZone:
    """A detected liquidity / supply-demand zone."""
    zone_type: str
    top: float
    bottom: float
    direction: str
    mitigated: bool = False
    strength: float = 0.5
    distance_pips: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

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
    """Complete liquidity zone map."""
    current_price: float
    sweep_direction: str
    zones: list[LiquidityZone] = field(default_factory=list)
    equilibrium: LiquidityZone | None = None
    nearest_target: LiquidityZone | None = None
    secondary_target: LiquidityZone | None = None

    def to_dict(self) -> dict:
        return {
            "current_price": self.current_price, "sweep_direction": self.sweep_direction,
            "zones": [z.to_dict() for z in self.zones],
            "equilibrium": self.equilibrium.to_dict() if self.equilibrium else None,
            "nearest_target": self.nearest_target.to_dict() if self.nearest_target else None,
            "secondary_target": self.secondary_target.to_dict() if self.secondary_target else None,
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
            "volume": 1,
        })
    return bars


# ── Detection Functions ────────────────────────────────────────────


def _detect_order_blocks(ohlcv_h1: list[dict], current_price: float) -> list[LiquidityZone]:
    zones: list[LiquidityZone] = []
    if len(ohlcv_h1) < 5:
        return zones
    for i in range(len(ohlcv_h1) - 3):
        try:
            c0, c1, c2 = ohlcv_h1[i], ohlcv_h1[i + 1], ohlcv_h1[i + 2]
            o0, h0, l0, cl0 = float(c0.get("open", 0)), float(c0.get("high", 0)), float(c0.get("low", 0)), float(c0.get("close", 0))  # noqa: E501
            cl1, cl2 = float(c1.get("close", 0)), float(c2.get("close", 0))
        except (KeyError, ValueError, TypeError):
            continue
        _body0 = abs(cl0 - o0)
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


def _detect_supply_demand(ohlcv_h1: list[dict], current_price: float) -> list[LiquidityZone]:
    zones: list[LiquidityZone] = []
    if len(ohlcv_h1) < 6:
        return zones
    swing_highs: list[dict] = []
    swing_lows: list[dict] = []
    for i in range(2, len(ohlcv_h1) - 2):
        try:
            curr_h = float(ohlcv_h1[i]["high"])
            prev2_h = float(ohlcv_h1[i - 2]["high"])
            prev1_h = float(ohlcv_h1[i - 1]["high"])
            next1_h = float(ohlcv_h1[i + 1]["high"])
            next2_h = float(ohlcv_h1[i + 2]["high"])
            curr_l = float(ohlcv_h1[i]["low"])
            prev2_l = float(ohlcv_h1[i - 2]["low"])
            prev1_l = float(ohlcv_h1[i - 1]["low"])
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


def _calculate_equilibrium(
    asia_high: float | None, asia_low: float | None,
    london_high: float | None, london_low: float | None,
    current_price: float,
) -> LiquidityZone | None:
    if asia_high and asia_low:
        midpoint = (asia_high + asia_low) / 2
        return LiquidityZone(
            zone_type="EQUILIBRIUM", top=midpoint + 1.0, bottom=midpoint - 1.0,
            direction="NEUTRAL", mitigated=abs(current_price - midpoint) < 3.0, strength=0.7,
        )
    if london_high and london_low:
        midpoint = (london_high + london_low) / 2
        return LiquidityZone(
            zone_type="EQUILIBRIUM", top=midpoint + 1.0, bottom=midpoint - 1.0,
            direction="NEUTRAL", mitigated=abs(current_price - midpoint) < 3.0, strength=0.5,
        )
    return None


def _map_zones(
    ohlcv: list[dict], current_price: float, sweep_direction: str,
    asia_high: float | None = None, asia_low: float | None = None,
    london_high: float | None = None, london_low: float | None = None,
) -> LiquidityMap:
    pip_val = _pip_value(current_price)
    all_zones: list[LiquidityZone] = []
    all_zones.extend(_detect_order_blocks(ohlcv, current_price))
    all_zones.extend(_detect_supply_demand(ohlcv, current_price))
    eq = _calculate_equilibrium(asia_high, asia_low, london_high, london_low, current_price)

    for zone in all_zones:
        zone.distance_pips = abs(current_price - zone.midpoint) / pip_val if pip_val > 0 else 0.0

    if sweep_direction in ("BEARISH", "SELL"):
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


def _find_tp_targets(
    liquidity_map: LiquidityMap, sweep_direction: str,
) -> tuple[float | None, float | None]:
    tp1: float | None = None
    tp2: float | None = None
    eq = liquidity_map.equilibrium
    price = liquidity_map.current_price
    if eq and not eq.mitigated:  # noqa: SIM102
        if sweep_direction in ("BEARISH", "SELL") and eq.midpoint < price or sweep_direction in ("BULLISH", "BUY") and eq.midpoint > price:  # noqa: E501
            tp1 = eq.midpoint
    if not tp1 and liquidity_map.nearest_target:
        tp1 = liquidity_map.nearest_target.midpoint
    if liquidity_map.secondary_target:
        tp2 = liquidity_map.secondary_target.midpoint
    elif liquidity_map.nearest_target:
        tp2 = liquidity_map.nearest_target.midpoint
    elif tp1 is not None:
        if sweep_direction in ("SELL", "BEARISH"):
            tp2 = tp1 - abs(price - tp1) * 0.5
        else:
            tp2 = tp1 + abs(tp1 - price) * 0.5
    return tp1, tp2


# ── Engine ─────────────────────────────────────────────────────────


class LiquidityEngine(Engine):
    """Liquidity Zone Mapping Engine.

    Detects Order Blocks, Supply/Demand zones, and equilibrium levels.
    """

    @property
    def name(self) -> str:
        return "liquidity_zones"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for liquidity zones and TP targets."""
        if not ticks or len(ticks) < 6:
            LOG.debug("Liquidity: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            current_price = ticks[-1].price

            liquidity_map = _map_zones(
                ohlcv, current_price, sweep_direction="BULLISH",
            )
            tp1, tp2 = _find_tp_targets(liquidity_map, "BULLISH")

            strong_zones = [z for z in liquidity_map.zones if z.strength > 0.5 and not z.mitigated]
            if not strong_zones:
                return None

            best_zone = max(strong_zones, key=lambda z: z.strength)
            direction = "CALL" if best_zone.direction in ("BULLISH", "NEUTRAL") else "PUT"
            conf_pct = best_zone.strength

            return Signal(
                symbol="XAUUSD",
                direction=direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=conf_pct,
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if conf_pct >= 0.7 else (
                    SignalGrade.MODERATE if conf_pct >= 0.5 else SignalGrade.WEAK
                ),
                metadata={
                    "engine": self.name,
                    "zone_count": len(liquidity_map.zones),
                    "best_zone_type": best_zone.zone_type,
                    "best_zone_strength": best_zone.strength,
                    "best_zone_top": best_zone.top,
                    "best_zone_bottom": best_zone.bottom,
                    "tp1": tp1,
                    "tp2": tp2,
                    "equilibrium": liquidity_map.equilibrium.midpoint if liquidity_map.equilibrium else None,  # noqa: E501
                    "direction_raw": best_zone.direction,
                },
            )
        except Exception as exc:
            LOG.warning("Liquidity engine error: %s", exc)
            raise SignalError("Liquidity analysis failed", details={"error": str(exc)}) from exc
