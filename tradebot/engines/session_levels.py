"""
session_levels.py — Key Session Level Calculator Engine

Migrated from: scripts/session_levels.py
Conforms to: tradebot.engines.base.Engine interface

Calculates session High/Low for Pre-NFP Liquidity Sweep strategy.

Sessions (WIB / UTC+7):
  - Asia:   00:00–07:00 UTC
  - London: 08:00–11:00 UTC
  - NY:     12:00–19:00 UTC
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

UTC = UTC


@dataclass
class SessionLevels:
    """All key session levels for sweep detection."""
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
    timestamp: str | None = None
    bars_scanned: int = 0
    is_nfp_friday: bool = False
    asia_bars: int = 0
    london_bars: int = 0
    ny_bars: int = 0

    def to_dict(self) -> dict:
        return {
            "asia_high": self.asia_high, "asia_low": self.asia_low,
            "london_high": self.london_high, "london_low": self.london_low,
            "ny_high": self.ny_high, "ny_low": self.ny_low,
            "prev_day_high": self.prev_day_high, "prev_day_low": self.prev_day_low,
            "today_high": self.today_high, "today_low": self.today_low,
            "timestamp": self.timestamp, "bars_scanned": self.bars_scanned,
            "is_nfp_friday": self.is_nfp_friday,
            "asia_bars": self.asia_bars, "london_bars": self.london_bars,
            "ny_bars": self.ny_bars,
        }

    @property
    def asia_range(self) -> float | None:
        if self.asia_high is not None and self.asia_low is not None:
            return self.asia_high - self.asia_low
        return None

    @property
    def london_range(self) -> float | None:
        if self.london_high is not None and self.london_low is not None:
            return self.london_high - self.london_low
        return None


# ── Helpers ────────────────────────────────────────────────────────


def _parse_timestamp(ts: object) -> datetime:
    """Parse various timestamp formats into a datetime."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts, UTC)
    if isinstance(ts, str):
        for fmt in [
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S.%f",
        ]:
            try:
                return datetime.strptime(ts.replace("Z", "+00:00"), fmt)
            except (ValueError, AttributeError):
                continue
    return datetime.now(UTC)


def _is_nfp_friday(dt: datetime) -> bool:
    """Check if date is NFP Friday (first Friday of the month)."""
    if dt.weekday() != 4:
        return False
    return 1 <= dt.day <= 7


def _pip_value(price: float) -> float:
    if price > 1000:
        return 0.10
    if price > 100:
        return 0.01
    return 0.0001


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    """Convert ticks to OHLCV-like bar dicts with timestamps."""
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price, "high": t.price,
            "low": t.price, "close": t.price,
            "volume": 1, "timestamp": t.epoch,
        })
    return bars


def calculate_all_levels(ohlcv_bars: list[dict], current_time: datetime | None = None) -> SessionLevels:  # noqa: E501
    """Calculate all session key levels from OHLCV data."""
    levels = SessionLevels()
    if not ohlcv_bars:
        return levels

    now = current_time or datetime.now(UTC)
    levels.timestamp = now.isoformat()
    levels.is_nfp_friday = _is_nfp_friday(now)
    levels.bars_scanned = len(ohlcv_bars)

    today_utc = now.date()
    yesterday_utc = today_utc - timedelta(days=1)

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

    for bar in ohlcv_bars:
        try:
            ts = _parse_timestamp(bar.get("timestamp", 0))
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, ValueError, TypeError):
            continue

        utc_h = ts.hour
        bar_date = ts.date()

        if 0 <= utc_h < 8:  # Asia session
            asia_highs.append(high)
            asia_lows.append(low)
        if 8 <= utc_h < 12:  # London session
            london_highs.append(high)
            london_lows.append(low)
        if 12 <= utc_h < 20:  # NY session
            ny_highs.append(high)
            ny_lows.append(low)
        if bar_date == today_utc:
            today_highs.append(high)
            today_lows.append(low)
        if bar_date == yesterday_utc:
            prev_day_highs.append(high)
            prev_day_lows.append(low)

    if asia_highs:
        levels.asia_high = max(asia_highs)
        levels.asia_low = min(asia_lows)
        levels.asia_bars = len(asia_highs)
    if london_highs:
        levels.london_high = max(london_highs)
        levels.london_low = min(london_lows)
        levels.london_bars = len(london_highs)
    if ny_highs:
        levels.ny_high = max(ny_highs)
        levels.ny_low = min(ny_lows)
        levels.ny_bars = len(ny_highs)
    if today_highs:
        levels.today_high = max(today_highs)
        levels.today_low = min(today_lows)
    if prev_day_highs:
        levels.prev_day_high = max(prev_day_highs)
        levels.prev_day_low = min(prev_day_lows)

    return levels


class SessionLevelsEngine(Engine):
    """Session Level Calculator Engine.

    Calculates key session High/Low levels for Asia, London, and NY
    sessions — used by sweep and liquidity engines for Pre-NFP
    liquidity sweep strategies.
    """

    @property
    def name(self) -> str:
        return "session_levels"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks and calculate session levels."""
        if not ticks:
            LOG.debug("SessionLevels: no ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            levels = calculate_all_levels(ohlcv)

            current_price = ticks[-1].price

            # Determine bias from session levels
            nearest_level: tuple[str, float] | None = None
            min_dist = float("inf")

            for name, val in [
                ("asia_high", levels.asia_high), ("asia_low", levels.asia_low),
                ("london_high", levels.london_high), ("london_low", levels.london_low),
                ("prev_day_high", levels.prev_day_high), ("prev_day_low", levels.prev_day_low),
                ("today_high", levels.today_high), ("today_low", levels.today_low),
            ]:
                if val is not None:
                    dist = abs(current_price - val)
                    if dist < min_dist:
                        min_dist = dist
                        nearest_level = (name, val)

            # Simple directional bias
            if nearest_level:
                name, val = nearest_level
                if "high" in name and current_price < val:
                    direction = "PUT"  # Price near resistance
                    confidence = min(0.6, 1.0 - (min_dist / (val * 0.01)))
                elif "low" in name and current_price > val:
                    direction = "CALL"  # Price near support
                    confidence = min(0.6, 1.0 - (min_dist / (val * 0.01)))
                else:
                    direction = "CALL"
                    confidence = 0.5
            else:
                direction = "CALL"
                confidence = 0.5

            return Signal(
                symbol="XAUUSD",
                direction=direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=round(confidence, 3),
                source=SignalSource.MOMEN,
                grade=SignalGrade.MODERATE,
                metadata={
                    "engine": self.name,
                    "session_levels": levels.to_dict(),
                    "asia_high": levels.asia_high,
                    "asia_low": levels.asia_low,
                    "london_high": levels.london_high,
                    "london_low": levels.london_low,
                    "ny_high": levels.ny_high,
                    "ny_low": levels.ny_low,
                    "prev_day_high": levels.prev_day_high,
                    "prev_day_low": levels.prev_day_low,
                    "today_high": levels.today_high,
                    "today_low": levels.today_low,
                    "is_nfp_friday": levels.is_nfp_friday,
                    "nearest_level": nearest_level,
                    "asia_range": levels.asia_range,
                    "london_range": levels.london_range,
                },
            )
        except Exception as exc:
            LOG.warning("SessionLevels engine error: %s", exc)
            raise SignalError("Session levels analysis failed", details={"error": str(exc)}) from exc  # noqa: E501
