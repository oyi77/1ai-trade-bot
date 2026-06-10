"""
hermes_liquidity_hunter.py — Pre-NFP Liquidity Sweep Signal Generator
====================================================================
Full pipeline: Session Levels → Sweep Detection → Liquidity Zone Mapping → Signal Output

Implements Hermes SMC rules:
  1. SL: sweep_high + 20 pips (bearish) / sweep_low - 20 pips (bullish)
  2. TP1: Equilibrium level from session range
  3. TP2: Nearest unmitigated OB or FVG in target direction
  4. RRR check: TP1 must be >= 1:1.5, else HOLD

Usage:
    from hermes_liquidity_hunter import generate_signal, hermes_pipeline
    signal = hermes_pipeline(ohlcv_h1, ohlcv_m15, current_price)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
import sys, os, json, math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from session_levels import (
    SessionLevels, calculate_all_levels, _pip_value, _is_nfp_friday, _parse_timestamp,
)
from sweep_detector import SweepSignal, detect_sweep
from liquidity_zones import LiquidityMap, map_zones, find_tp_targets, LiquidityZone

SL_BUFFER_PIPS: float = 20.0
MIN_RR_RATIO: float = 1.5
MIN_SWEEP_CONFIDENCE: float = 0.30


@dataclass
class HermesSignal:
    signal_id: str = "LIQUIDITY_HUNTER_NFP"
    asset: str = "XAUUSD"
    action: str = "HOLD"
    reason: str = ""
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    risk_reward_ratio: float = 0.0
    confidence: float = 0.0
    sweep_direction: str = ""
    alert_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id, "asset": self.asset,
            "action": self.action, "reason": self.reason,
            "entry_price": self.entry_price, "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1, "take_profit_2": self.take_profit_2,
            "risk_reward_ratio": self.risk_reward_ratio, "confidence": self.confidence,
            "sweep_direction": self.sweep_direction, "alert_message": self.alert_message,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str, ensure_ascii=False)


def _calculate_sl(sweep: SweepSignal, pip_value: float) -> float:
    buffer = pip_value * SL_BUFFER_PIPS
    if sweep.direction == "BEARISH":
        return sweep.sweep_high + buffer
    else:
        return sweep.sweep_low - buffer


def _calculate_rr(entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def _build_alert_message(signal: HermesSignal) -> str:
    emoji = "🔴" if signal.action == "SELL" else ("🟢" if signal.action == "BUY" else "⚪️")
    direction_emoji = "📉" if signal.action == "SELL" else ("📈" if signal.action == "BUY" else "⏸️")
    return (
        f"🚨 *HERMES LIQUIDITY SWEEP* 🚨\n\n"
        f"*Pair:* XAUUSD\n"
        f"*Direction:* {emoji} {signal.action}\n"
        f"*Confidence:* {signal.confidence:.0%}\n"
        f"{direction_emoji} *Entry:* ${signal.entry_price:.2f}\n"
        f"🛑 *SL:* ${signal.stop_loss:.2f}\n"
        f"🎯 *TP1:* ${signal.take_profit_1:.2f} (Equilibrium)\n"
        f"🎯 *TP2:* ${signal.take_profit_2:.2f} (OB/FVG)\n"
        f"📊 *R:R:* 1:{signal.risk_reward_ratio}\n\n"
        f"_{signal.reason}_\n\n"
        f"#XAUUSD #Gold #SMC #LiquiditySweep"
    )


def generate_signal(sweep: SweepSignal, liquidity_map: LiquidityMap,
                    session_levels: SessionLevels, current_price: float) -> HermesSignal:
    pip_value = _pip_value(current_price)
    direction_short = sweep.direction_short

    sl = _calculate_sl(sweep, pip_value)
    sl_pips = abs(current_price - sl) / pip_value

    tp1 = None
    eq = liquidity_map.equilibrium
    if eq and not eq.mitigated:
        eq_mid = eq.midpoint
        if direction_short == "SELL" and eq_mid < current_price:
            tp1 = eq_mid
        elif direction_short == "BUY" and eq_mid > current_price:
            tp1 = eq_mid
    if not tp1 and liquidity_map.nearest_target:
        tp1 = liquidity_map.nearest_target.midpoint

    tp2 = None
    if liquidity_map.secondary_target:
        tp2 = liquidity_map.secondary_target.midpoint
    elif liquidity_map.nearest_target:
        tp2 = liquidity_map.nearest_target.midpoint
    elif tp1 and direction_short in ("SELL", "BUY"):
        if direction_short == "SELL":
            tp2 = tp1 - abs(current_price - tp1) * 0.5
        else:
            tp2 = tp1 + abs(tp1 - current_price) * 0.5
    else:
        tp2 = tp1

    rr = 0.0
    if tp1 and tp1 != current_price:
        rr = _calculate_rr(current_price, sl, tp1)

    if rr >= MIN_RR_RATIO and sweep.confidence >= MIN_SWEEP_CONFIDENCE:
        action = direction_short
        reason = (
            f"{sweep.direction.lower().replace('bearish','Bearish').replace('bullish','Bullish')} "
            f"sweep di {sweep.level_name} terkonfirmasi, "
            f"target pullback ke {'Equilibrium' if eq else 'demand zone'} "
            f"+ OB/FVG. RR 1:{rr}"
        )
    else:
        action = "HOLD"
        if rr < MIN_RR_RATIO:
            reason = f"HOLD — RRR 1:{rr} di bawah minimum 1:{MIN_RR_RATIO}. Sinyal tidak valid."
        elif sweep.confidence < MIN_SWEEP_CONFIDENCE:
            reason = f"HOLD — confidence {sweep.confidence:.0%} di bawah minimum {MIN_SWEEP_CONFIDENCE:.0%}."
        else:
            reason = "HOLD — kondisi tidak memenuhi kriteria entry."

    if len(reason) > 100:
        reason = reason[:97] + "..."

    signal = HermesSignal(
        signal_id="LIQUIDITY_HUNTER_NFP", asset="XAUUSD", action=action, reason=reason,
        entry_price=current_price, stop_loss=round(sl, 2),
        take_profit_1=round(tp1, 2) if tp1 else 0.0,
        take_profit_2=round(tp2, 2) if tp2 else 0.0,
        risk_reward_ratio=rr, confidence=round(sweep.confidence, 3),
        sweep_direction=sweep.direction,
        metadata={
            "sweep": sweep.to_dict(),
            "session_levels": session_levels.to_dict(),
            "liquidity_zones": liquidity_map.to_dict(),
            "sl_pips": round(sl_pips, 1),
            "entry_pips_from_level": round(abs(current_price - sweep.level_price) / pip_value, 1),
            "nfp_friday": session_levels.is_nfp_friday,
        },
    )
    signal.alert_message = _build_alert_message(signal)
    return signal


def hermes_pipeline(
    ohlcv_h1: List[dict], ohlcv_m15: List[dict],
    current_price: Optional[float] = None,
    asset: str = "XAUUSD", require_nfp_friday: bool = False,
) -> Optional[HermesSignal]:
    """Full Hermes Liquidity Hunter pipeline."""
    if not ohlcv_h1 or not ohlcv_m15:
        return None
    if not current_price:
        current_price = float(ohlcv_h1[-1].get("close", ohlcv_h1[-1].get("open", 0)))
    if not current_price:
        return None

    session_levels = calculate_all_levels(ohlcv_m15)
    if require_nfp_friday and not session_levels.is_nfp_friday:
        return None

    sweep = detect_sweep(ohlcv_h1, session_levels, current_price)
    if not sweep:
        return None

    liquidity_map = map_zones(ohlcv_h1, session_levels, current_price, sweep.direction)
    signal = generate_signal(sweep, liquidity_map, session_levels, current_price)
    signal.asset = asset
    return signal
