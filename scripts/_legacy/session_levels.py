"""
session_levels.py — Key Session Level Calculator
=================================================
Calculates session High/Low for Pre-NFP Liquidity Sweep strategy.
Deterministic, no AI dependency.

Sessions (WIB / UTC+7):
  - Asia:   07:00–15:00 WIB  (00:00–08:00 UTC)
  - London: 15:00–19:00 WIB  (08:00–12:00 UTC) — pre-NY overlap
  - NY:     19:00–03:00 WIB  (12:00–20:00 UTC next day)
  - PrevDay: Yesterday 00:00–23:59 WIB

Usage:
    from session_levels import calculate_all_levels
    levels = calculate_all_levels(ohlcv_m15)
    print(levels.asia_high, levels.asia_low)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional
import math

WIB_OFFSET = timedelta(hours=7)


@dataclass
class SessionLevels:
    """All key session levels for sweep detection."""
    asia_high: Optional[float] = None
    asia_low: Optional[float] = None
    london_high: Optional[float] = None
    london_low: Optional[float] = None
    ny_high: Optional[float] = None
    ny_low: Optional[float] = None
    prev_day_high: Optional[float] = None
    prev_day_low: Optional[float] = None
    today_high: Optional[float] = None
    today_low: Optional[float] = None
    timestamp: Optional[str] = None
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
            "asia_bars": self.asia_bars, "london_bars": self.london_bars, "ny_bars": self.ny_bars,
        }

    @property
    def asia_range(self) -> Optional[float]:
        if self.asia_high and self.asia_low:
            return self.asia_high - self.asia_low
        return None

    @property
    def london_range(self) -> Optional[float]:
        if self.london_high and self.london_low:
            return self.london_high - self.london_low
        return None


def _parse_timestamp(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts)
    if isinstance(ts, str):
        for fmt in [
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S.%f",
        ]:
            try:
                return datetime.strptime(ts.replace("Z", "+00:00"), fmt)
            except ValueError:
                continue
    return datetime.now()


def _is_nfp_friday(dt: datetime) -> bool:
    wib = dt + WIB_OFFSET
    if wib.weekday() != 4:
        return False
    return 1 <= wib.day <= 7


def _pip_value(price: float) -> float:
    if price > 1000:
        return 0.10
    elif price > 100:
        return 0.01
    return 0.0001


def calculate_all_levels(
    ohlcv_bars: List[dict],
    current_time: Optional[datetime] = None,
) -> SessionLevels:
    """Calculate all session key levels from M15 OHLCV data."""
    levels = SessionLevels()
    if not ohlcv_bars:
        return levels

    now = current_time or datetime.now()
    levels.timestamp = now.isoformat()
    levels.is_nfp_friday = _is_nfp_friday(now)
    levels.bars_scanned = len(ohlcv_bars)

    today_wib = (now + WIB_OFFSET).date()
    yesterday_wib = today_wib - timedelta(days=1)

    asia_highs, asia_lows = [], []
    london_highs, london_lows = [], []
    ny_highs, ny_lows = [], []
    today_highs, today_lows = [], []
    prev_day_highs, prev_day_lows = [], []

    for bar in ohlcv_bars:
        try:
            ts = _parse_timestamp(bar.get("timestamp", 0))
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, ValueError, TypeError):
            continue

        bar_wib = ts + WIB_OFFSET
        wib_h = bar_wib.hour
        bar_date = bar_wib.date()

        if 7 <= wib_h < 15:
            asia_highs.append(high)
            asia_lows.append(low)
        if 15 <= wib_h < 19:
            london_highs.append(high)
            london_lows.append(low)
        if wib_h >= 19 or wib_h < 3:
            ny_highs.append(high)
            ny_lows.append(low)
        if bar_date == today_wib:
            today_highs.append(high)
            today_lows.append(low)
        if bar_date == yesterday_wib:
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
