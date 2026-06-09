"""
hermes_liquidity.py — Hermes Liquidity Hunter Engine

Migrated from: scripts/hermes_liquidity_hunter.py
Conforms to: tradebot.engines.base.Engine interface

Full pipeline: Session Levels → Sweep Detection → Liquidity Zone Mapping → Signal Output.
Implements Hermes SMC rules for Pre-NFP liquidity sweep trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

UTC = UTC

# ── Configurable defaults ──────────────────────────────────────────

_SL_BUFFER_PIPS: float = float(getattr(settings, "HERMES_SL_BUFFER_PIPS", 20.0))
_MIN_RR_RATIO: float = float(getattr(settings, "HERMES_MIN_RR_RATIO", 1.5))
_MIN_SWEEP_CONFIDENCE: float = float(getattr(settings, "HERMES_MIN_SWEEP_CONFIDENCE", 0.30))


# ── Helpers ────────────────────────────────────────────────────────


def _pip_value(price: float) -> float:
    if price > 1000:
        return 0.10
    if price > 100:
        return 0.01
    return 0.0001


def _is_nfp_friday(dt: datetime) -> bool:
    """Check if today is NFP Friday (first Friday of the month)."""
    wib = dt  # Simplified — assume UTC input
    if wib.weekday() != 4:
        return False
    return 1 <= wib.day <= 7


def _parse_timestamp(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts, UTC)
    if isinstance(ts, str):
        for fmt in [
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f",
        ]:
            try:
                return datetime.strptime(ts.replace("Z", "+00:00")[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
    return datetime.now(UTC)


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price, "high": t.price,
            "low": t.price, "close": t.price,
            "volume": 1, "timestamp": t.epoch,
        })
    return bars


# ── Session Level Calculator ───────────────────────────────────────


@dataclass
class SessionLevels:
    """Key session levels for sweep detection."""
    asia_high: float | None = None
    asia_low: float | None = None
    london_high: float | None = None
    london_low: float | None = None
    ny_high: float | None = None
    ny_low: float | None = None
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    today_high: float | None = None
    today_low: float | None = None
    is_nfp_friday: bool = False

    def to_dict(self) -> dict:
        return {
            "asia_high": self.asia_high, "asia_low": self.asia_low,
            "london_high": self.london_high, "london_low": self.london_low,
            "ny_high": self.ny_high, "ny_low": self.ny_low,
            "prev_day_high": self.prev_day_high, "prev_day_low": self.prev_day_low,
            "today_high": self.today_high, "today_low": self.today_low,
            "is_nfp_friday": self.is_nfp_friday,
        }


def _calculate_session_levels(ohlcv: list[dict]) -> SessionLevels:
    """Calculate session levels from OHLCV bars."""
    levels = SessionLevels()
    if not ohlcv:
        return levels

    now = datetime.now(UTC)
    levels.is_nfp_friday = _is_nfp_friday(now)

    asia_highs: list[float] = []
    asia_lows: list[float] = []
    london_highs: list[float] = []
    london_lows: list[float] = []
    ny_highs: list[float] = []
    ny_lows: list[float] = []
    today_highs: list[float] = []
    today_lows: list[float] = []
    prev_day_highs: list[float] = []
    prev_day_lows: list[float] = []

    today_date = now.date()
    yesterday_date = today_date - __import__("datetime").timedelta(days=1)

    for bar in ohlcv:
        try:
            ts = _parse_timestamp(bar.get("timestamp", 0))
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))
        except (KeyError, ValueError, TypeError):
            continue

        h = ts.hour
        bar_date = ts.date()

        if 0 <= h < 8:  # Asia: 00-07 UTC
            asia_highs.append(high)
            asia_lows.append(low)
        if 8 <= h < 12:  # London: 08-11 UTC
            london_highs.append(high)
            london_lows.append(low)
        if 12 <= h < 20:  # NY: 12-19 UTC
            ny_highs.append(high)
            ny_lows.append(low)
        if bar_date == today_date:
            today_highs.append(high)
            today_lows.append(low)
        if bar_date == yesterday_date:
            prev_day_highs.append(high)
            prev_day_lows.append(low)

    if asia_highs:
        levels.asia_high = max(asia_highs)
        levels.asia_low = min(asia_lows)
    if london_highs:
        levels.london_high = max(london_highs)
        levels.london_low = min(london_lows)
    if ny_highs:
        levels.ny_high = max(ny_highs)
        levels.ny_low = min(ny_lows)
    if today_highs:
        levels.today_high = max(today_highs)
        levels.today_low = min(today_lows)
    if prev_day_highs:
        levels.prev_day_high = max(prev_day_highs)
        levels.prev_day_low = min(prev_day_lows)

    return levels


# ── Sweep Detection (inline) ────────────────────────────────────────


@dataclass
class HermesSweep:
    """Simplified sweep signal for the Hermes pipeline."""
    direction: str = ""
    direction_short: str = ""
    level_name: str = ""
    level_price: float = 0.0
    entry_price: float = 0.0
    sweep_high: float = 0.0
    sweep_low: float = 0.0
    sweep_close: float = 0.0
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction, "direction_short": self.direction_short,
            "level_name": self.level_name, "level_price": self.level_price,
            "entry_price": self.entry_price, "sweep_high": self.sweep_high,
            "sweep_low": self.sweep_low, "confidence": self.confidence,
        }


@dataclass
class HermesLiquidityZone:
    """Simplified liquidity zone for Hermes pipeline."""
    zone_type: str
    midpoint: float
    direction: str
    mitigated: bool = False
    strength: float = 0.5

    def to_dict(self) -> dict:
        return {
            "zone_type": self.zone_type, "midpoint": self.midpoint,
            "direction": self.direction, "mitigated": self.mitigated,
            "strength": self.strength,
        }


@dataclass
class HermesLiquidityMap:
    """Simplified liquidity map for Hermes pipeline."""
    current_price: float
    equilibrium: HermesLiquidityZone | None = None
    nearest_target: HermesLiquidityZone | None = None
    secondary_target: HermesLiquidityZone | None = None

    def to_dict(self) -> dict:
        return {
            "current_price": self.current_price,
            "equilibrium": self.equilibrium.to_dict() if self.equilibrium else None,
            "nearest_target": self.nearest_target.to_dict() if self.nearest_target else None,
            "secondary_target": self.secondary_target.to_dict() if self.secondary_target else None,
        }


# ── Engine ─────────────────────────────────────────────────────────


class HermesLiquidityEngine(Engine):
    """Hermes Liquidity Hunter Engine.

    Full Pre-NFP liquidity sweep pipeline: session levels → sweep
    detection → zone mapping → signal output with SL/TP/RRR.
    """

    def __init__(self) -> None:
        self._sl_buffer: float = float(getattr(settings, "HERMES_SL_BUFFER_PIPS", _SL_BUFFER_PIPS))
        self._min_rr: float = float(getattr(settings, "HERMES_MIN_RR_RATIO", _MIN_RR_RATIO))
        self._min_conf: float = float(getattr(settings, "HERMES_MIN_SWEEP_CONFIDENCE", _MIN_SWEEP_CONFIDENCE))  # noqa: E501

    @property
    def name(self) -> str:
        return "hermes_liquidity_hunter"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Run the full Hermes Liquidity Hunter pipeline."""
        if not ticks or len(ticks) < 10:
            LOG.debug("Hermes: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            current_price = ticks[-1].price
            pip_value = _pip_value(current_price)

            # 1. Session levels
            levels = _calculate_session_levels(ohlcv)

            # 2. Simple sweep detection
            sweep = self._detect_sweep_inline(ohlcv, levels, current_price, pip_value)
            if not sweep or sweep.confidence < self._min_conf:
                return None

            # 3. Liquidity mapping
            liq_map = self._map_zones_inline(
                levels, current_price, sweep.direction, pip_value,
            )

            # 4. SL/TP calculation
            buffer = pip_value * self._sl_buffer
            if sweep.direction in ("BEARISH", "SELL"):
                sl = sweep.sweep_high + buffer
            else:
                sl = sweep.sweep_low - buffer

            tp1: float | None = None
            if liq_map.equilibrium and not liq_map.equilibrium.mitigated:
                eq_mid = liq_map.equilibrium.midpoint
                if sweep.direction_short == "SELL" and eq_mid < current_price or sweep.direction_short == "BUY" and eq_mid > current_price:  # noqa: E501
                    tp1 = eq_mid
            if not tp1 and liq_map.nearest_target:
                tp1 = liq_map.nearest_target.midpoint

            tp2: float | None = None
            if liq_map.secondary_target:
                tp2 = liq_map.secondary_target.midpoint
            elif liq_map.nearest_target:
                tp2 = liq_map.nearest_target.midpoint
            elif tp1 is not None:
                tp2 = tp1 + abs(tp1 - current_price) * 0.5 if sweep.direction_short == "BUY" else tp1 - abs(current_price - tp1) * 0.5  # noqa: E501

            # 5. RRR check
            risk = abs(current_price - sl)
            reward = abs((tp1 or current_price) - current_price)
            rr = round(reward / risk, 2) if risk > 0 else 0.0

            if rr < self._min_rr:
                LOG.debug("Hermes: RRR %.2f below minimum %.2f", rr, self._min_rr)
                return None

            signal_direction = sweep.direction_short  # "BUY" or "SELL"
            signal_dir = "CALL" if signal_direction == "BUY" else "PUT"

            return Signal(
                symbol="XAUUSD",
                direction=signal_dir,
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
                    "entry_price": current_price,
                    "stop_loss": round(sl, 2),
                    "take_profit_1": round(tp1, 2) if tp1 else None,
                    "take_profit_2": round(tp2, 2) if tp2 else None,
                    "risk_reward_ratio": rr,
                    "nfp_friday": levels.is_nfp_friday,
                    "asia_high": levels.asia_high,
                    "asia_low": levels.asia_low,
                    "session_levels": levels.to_dict(),
                },
            )
        except Exception as exc:
            LOG.warning("Hermes engine error: %s", exc)
            raise SignalError("Hermes liquidity analysis failed", details={"error": str(exc)}) from exc  # noqa: E501

    def _detect_sweep_inline(
        self, ohlcv: list[dict], levels: SessionLevels,
        current_price: float, pip_value: float,
    ) -> HermesSweep | None:
        """Simplified inline sweep detection."""
        bearish_levels: list[tuple[float, str]] = []
        bullish_levels: list[tuple[float, str]] = []

        if levels.asia_high:
            bearish_levels.append((levels.asia_high, "Asia High"))
        if levels.london_high:
            bearish_levels.append((levels.london_high, "London High"))
        if levels.prev_day_high:
            bearish_levels.append((levels.prev_day_high, "Prev Day High"))
        if levels.asia_low:
            bullish_levels.append((levels.asia_low, "Asia Low"))
        if levels.london_low:
            bullish_levels.append((levels.london_low, "London Low"))
        if levels.prev_day_low:
            bullish_levels.append((levels.prev_day_low, "Prev Day Low"))

        recent = ohlcv[-8:]
        best_sweep: dict | None = None

        for candle in recent:
            try:
                high = float(candle.get("high", 0))
                low = float(candle.get("low", 0))
                close = float(candle.get("close", 0))
            except (ValueError, TypeError):
                continue

            for level_price, level_name in bearish_levels:
                if high > level_price:
                    upper_wick = high - max(close, level_price)
                    if upper_wick >= pip_value * 0.5 and close < level_price - pip_value:
                        gap_pips = (level_price - close) / pip_value if pip_value > 0 else 0
                        gap_conf = min(1.0, gap_pips / 15)
                        conf = round(gap_conf * 0.6 + 0.2, 3)
                        if best_sweep is None or conf > best_sweep.get("confidence", 0):
                            best_sweep = {
                                "direction": "BEARISH", "direction_short": "SELL",
                                "level_name": level_name, "level_price": level_price,
                                "sweep_high": high, "sweep_low": low,
                                "sweep_close": close, "entry_price": current_price,
                                "confidence": conf,
                            }

            for level_price, level_name in bullish_levels:
                if low < level_price:
                    lower_wick = min(close, level_price) - low
                    if lower_wick >= pip_value * 0.5 and close > level_price + pip_value:
                        gap_pips = (close - level_price) / pip_value if pip_value > 0 else 0
                        gap_conf = min(1.0, gap_pips / 15)
                        conf = round(gap_conf * 0.6 + 0.2, 3)
                        if best_sweep is None or conf > best_sweep.get("confidence", 0):
                            best_sweep = {
                                "direction": "BULLISH", "direction_short": "BUY",
                                "level_name": level_name, "level_price": level_price,
                                "sweep_high": high, "sweep_low": low,
                                "sweep_close": close, "entry_price": current_price,
                                "confidence": conf,
                            }

        if not best_sweep:
            return None

        return HermesSweep(
            direction=best_sweep["direction"],
            direction_short=best_sweep["direction_short"],
            level_name=best_sweep["level_name"],
            level_price=best_sweep["level_price"],
            entry_price=best_sweep.get("entry_price", current_price),
            sweep_high=best_sweep.get("sweep_high", 0),
            sweep_low=best_sweep.get("sweep_low", 0),
            sweep_close=best_sweep.get("sweep_close", 0),
            confidence=best_sweep["confidence"],
        )

    def _map_zones_inline(
        self, levels: SessionLevels, current_price: float,
        sweep_direction: str, pip_value: float,
    ) -> HermesLiquidityMap:
        """Simplified inline liquidity zone mapping."""
        eq: HermesLiquidityZone | None = None
        if levels.asia_high and levels.asia_low:
            midpoint = (levels.asia_high + levels.asia_low) / 2
            eq = HermesLiquidityZone(
                zone_type="EQUILIBRIUM", midpoint=midpoint,
                direction="NEUTRAL", strength=0.7,
            )

        z: list[HermesLiquidityZone] = []
        if levels.london_high:
            z.append(HermesLiquidityZone(
                zone_type="SUPPLY", midpoint=levels.london_high,
                direction="BEARISH", strength=0.6,
            ))
        if levels.london_low:
            z.append(HermesLiquidityZone(
                zone_type="DEMAND", midpoint=levels.london_low,
                direction="BULLISH", strength=0.6,
            ))
        if levels.ny_high:
            z.append(HermesLiquidityZone(
                zone_type="SUPPLY", midpoint=levels.ny_high,
                direction="BEARISH", strength=0.5,
            ))
        if levels.ny_low:
            z.append(HermesLiquidityZone(
                zone_type="DEMAND", midpoint=levels.ny_low,
                direction="BULLISH", strength=0.5,
            ))

        nearest: HermesLiquidityZone | None = None
        secondary: HermesLiquidityZone | None = None

        if sweep_direction in ("BEARISH", "SELL"):
            targets = [zz for zz in z if zz.midpoint < current_price and zz.direction in ("BULLISH", "NEUTRAL")]  # noqa: E501
        else:
            targets = [zz for zz in z if zz.midpoint > current_price and zz.direction in ("BEARISH", "NEUTRAL")]  # noqa: E501

        targets.sort(key=lambda zz: abs(zz.midpoint - current_price))
        if targets:
            nearest = targets[0]
        if len(targets) > 1:
            secondary = targets[1]

        return HermesLiquidityMap(
            current_price=current_price, equilibrium=eq,
            nearest_target=nearest, secondary_target=secondary,
        )
