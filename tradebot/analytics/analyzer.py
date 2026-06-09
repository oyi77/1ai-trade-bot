"""
MarketAnalyzer — daily mapping, session levels, and price action analysis.

Provides clean, structured market analysis drawing from daily mapping
logic and session-level calculations. Designed to be used by report
generators and dashboard views.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

LOG = logging.getLogger(__name__)

# WIB timezone offset
WIB = timezone(timedelta(hours=7))

# Pip sizes per symbol
PIP_SIZES: dict[str, float] = {
    "XAUUSD": 0.1, "GOLD": 0.1,
    "EURUSD": 0.0001, "GBPUSD": 0.0001,
    "USDJPY": 0.01, "BTCUSD": 1.0,
    "USOIL": 0.01, "OIL": 0.01,
}


@dataclass
class SessionLevels:
    """Key price levels for each trading session."""

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "asia_high": self.asia_high, "asia_low": self.asia_low,
            "london_high": self.london_high, "london_low": self.london_low,
            "ny_high": self.ny_high, "ny_low": self.ny_low,
            "prev_day_high": self.prev_day_high, "prev_day_low": self.prev_day_low,
            "today_high": self.today_high, "today_low": self.today_low,
            "timestamp": self.timestamp, "bars_scanned": self.bars_scanned,
        }


@dataclass
class SupportResistanceZone:
    """Support or resistance zone with price levels."""

    zone_type: str  # "support" or "resistance"
    price: float
    zone_low: float
    zone_high: float
    strength: int = 0  # 1-3, higher = stronger


@dataclass
class DailyMapping:
    """Complete daily market mapping data."""

    date: str
    current_session: str
    dxy: float | None = None
    prices: dict[str, dict] = field(default_factory=dict)
    zones: dict[str, list[SupportResistanceZone]] = field(default_factory=dict)
    session_levels: SessionLevels | None = None
    is_nfp_friday: bool = False
    momentum: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL


class MarketAnalyzer:
    """Market analysis — session levels, daily mapping, price action.

    Usage:
        analyzer = MarketAnalyzer(price_provider=my_provider)
        mapping = await analyzer.get_daily_mapping()
        levels = analyzer.calculate_session_levels(ohlcv_bars)
    """

    def __init__(self, price_provider: Any = None) -> None:
        self._provider = price_provider

    # ── Session Levels ──

    def calculate_session_levels(
        self,
        ohlcv_bars: list[dict],
        current_time: datetime | None = None,
    ) -> SessionLevels:
        """Calculate session high/low from OHLCV bars.

        Session definitions (WIB / UTC+7):
          - Asia:     07:00–15:00 WIB
          - London:   15:00–19:00 WIB
          - NY:       19:00–03:00 WIB
          - PrevDay:  Yesterday 00:00–23:59 WIB

        Args:
            ohlcv_bars: List of OHLCV dicts with keys:
                timestamp, high, low, (optional: open, close).
            current_time: Override current time (defaults to now).

        Returns:
            SessionLevels with computed highs/lows.
        """
        levels = SessionLevels()
        if not ohlcv_bars:
            return levels

        now = current_time or datetime.now()
        levels.timestamp = now.isoformat()
        levels.bars_scanned = len(ohlcv_bars)

        wib_delta = timedelta(hours=7)
        today_wib = (now + wib_delta).date()
        yesterday_wib = today_wib - timedelta(days=1)

        asia_highs: list[float] = []
        asia_lows: list[float] = []
        london_highs: list[float] = []
        london_lows: list[float] = []
        ny_highs: list[float] = []
        ny_lows: list[float] = []
        today_highs: list[float] = []
        today_lows: list[float] = []
        prev_highs: list[float] = []
        prev_lows: list[float] = []

        for bar in ohlcv_bars:
            try:
                ts = self._parse_timestamp(bar.get("timestamp", 0))
                high = float(bar["high"])
                low = float(bar["low"])
            except (KeyError, ValueError, TypeError):
                continue

            bar_wib = ts + wib_delta
            bar_hour = bar_wib.hour
            bar_date = bar_wib.date()

            if 7 <= bar_hour < 15:
                asia_highs.append(high)
                asia_lows.append(low)
            if 15 <= bar_hour < 19:
                london_highs.append(high)
                london_lows.append(low)
            if bar_hour >= 19 or bar_hour < 3:
                ny_highs.append(high)
                ny_lows.append(low)
            if bar_date == today_wib:
                today_highs.append(high)
                today_lows.append(low)
            if bar_date == yesterday_wib:
                prev_highs.append(high)
                prev_lows.append(low)

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
        if prev_highs:
            levels.prev_day_high = max(prev_highs)
            levels.prev_day_low = min(prev_lows)

        return levels

    # ── Daily Mapping ──

    async def get_daily_mapping(
        self,
        symbols: list[str] | None = None,
    ) -> DailyMapping:
        """Generate a complete daily market mapping.

        Args:
            symbols: List of symbols to include (defaults to major pairs).

        Returns:
            DailyMapping with prices, zones, session info.
        """
        if symbols is None:
            symbols = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "USOIL"]

        now = datetime.now(WIB)
        mapping = DailyMapping(
            date=now.strftime("%Y-%m-%d"),
            current_session=self._get_session_name(now),
            is_nfp_friday=self._is_nfp_friday(now),
        )

        if self._provider is None:
            LOG.warning("No price provider — mapping will be empty")
            return mapping

        try:
            prices = await self._fetch_prices(symbols)
            mapping.prices = prices

            dxy = prices.get("DX-Y.NYB", {}).get("price")
            if dxy:
                mapping.dxy = float(dxy)

            # Calculate zones per symbol
            for sym in symbols:
                ohlcv = await self._fetch_ohlcv(sym)
                if ohlcv:
                    zones = self._calculate_zones(ohlcv, prices.get(sym, {}).get("price", 0), sym)
                    if zones:
                        mapping.zones[sym] = zones

            # Determine momentum
            mapping.momentum = self._determine_momentum(mapping)

        except Exception as exc:
            LOG.error("Failed to generate daily mapping: %s", exc)

        return mapping

    # ── Price Action Analysis ──

    def analyze_price_action(
        self,
        price: float,
        levels: SessionLevels,
        lookback_prices: list[float] | None = None,
    ) -> dict[str, Any]:
        """Analyze current price action relative to key levels.

        Args:
            price: Current price.
            levels: SessionLevels from calculate_session_levels().
            lookback_prices: Recent price data for momentum calc.

        Returns:
            Dict with analysis fields.
        """
        result: dict[str, Any] = {
            "price": price,
            "momentum": "SIDEWAYS",
            "near_level": None,
            "breakout": None,
            "range_pct": 0.0,
        }

        # Determine momentum from lookback prices
        if lookback_prices and len(lookback_prices) >= 5:
            recent = lookback_prices[-5:]
            if all(recent[i] <= recent[i + 1] for i in range(len(recent) - 1)):
                result["momentum"] = "BULLISH"
            elif all(recent[i] >= recent[i + 1] for i in range(len(recent) - 1)):
                result["momentum"] = "BEARISH"

        # Check proximity to key levels
        for level_name, level_value in [
            ("asia_high", levels.asia_high),
            ("asia_low", levels.asia_low),
            ("london_high", levels.london_high),
            ("london_low", levels.london_low),
            ("ny_high", levels.ny_high),
            ("ny_low", levels.ny_low),
            ("prev_day_high", levels.prev_day_high),
            ("prev_day_low", levels.prev_day_low),
        ]:
            if level_value is None:
                continue
            pip_size = self._pip_size(price)
            distance_pips = abs(price - level_value) / pip_size if pip_size > 0 else float("inf")

            if distance_pips < 5:
                zone_type = "resistance" if level_value >= price else "support"
                result["near_level"] = {
                    "name": level_name,
                    "price": level_value,
                    "distance_pips": round(distance_pips, 1),
                    "type": zone_type,
                }

            # Check breakout
            if distance_pips < 1:
                result["breakout"] = {
                    "level": level_name,
                    "price": level_value,
                    "direction": "ABOVE" if price > level_value else "BELOW",
                }

        # Calculate range
        if levels.asia_high and levels.asia_low and levels.asia_high > levels.asia_low:
            result["range_pct"] = round(
                (price - levels.asia_low) / (levels.asia_high - levels.asia_low) * 100, 1
            )

        return result

    # ── Internal helpers ──

    @staticmethod
    def _parse_timestamp(ts: Any) -> datetime:
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

    @staticmethod
    def _get_session_name(now: datetime) -> str:
        hour = now.hour
        if 7 <= hour < 15:
            return "Asia"
        if 15 <= hour < 19:
            return "London"
        return "NY" if hour >= 19 else "Pre-Asia"

    @staticmethod
    def _is_nfp_friday(dt: datetime) -> bool:
        wib = dt + timedelta(hours=7)
        if wib.weekday() != 4:
            return False
        return 1 <= wib.day <= 7

    async def _fetch_prices(self, symbols: list[str]) -> dict[str, dict]:
        prices: dict[str, dict] = {}
        if not hasattr(self._provider, "get_quote"):
            return prices

        for sym in symbols:
            try:
                q = await self._provider.get_quote(sym, force=True) if hasattr(
                    self._provider.get_quote, "__await__"
                ) else self._provider.get_quote(sym)
                if q:
                    prices[sym] = {
                        "price": float(getattr(q, "price", 0)),
                        "bid": float(getattr(q, "bid", 0)),
                        "ask": float(getattr(q, "ask", 0)),
                        "change": float(getattr(q, "change_pct", 0)),
                    }
            except Exception as exc:
                LOG.debug("Failed to fetch price for %s: %s", sym, exc)

        return prices

    async def _fetch_ohlcv(self, symbol: str, timeframe: str = "4h", count: int = 50) -> list[dict]:
        if not hasattr(self._provider, "get_ohlcv"):
            return []
        try:
            bars = await self._provider.get_ohlcv(symbol, timeframe, count, force=True) if hasattr(
                self._provider.get_ohlcv, "__await__"
            ) else self._provider.get_ohlcv(symbol, timeframe, count)
            if bars:
                return [
                    {"timestamp": str(getattr(b, "timestamp", "")),
                     "open": float(getattr(b, "open", 0)),
                     "high": float(getattr(b, "high", 0)),
                     "low": float(getattr(b, "low", 0)),
                     "close": float(getattr(b, "close", 0))}
                    for b in bars
                ]
        except Exception as exc:
            LOG.debug("Failed to fetch OHLCV for %s: %s", symbol, exc)
        return []

    def _calculate_zones(
        self, bars: list[dict], current_price: float, symbol: str,
    ) -> list[SupportResistanceZone]:
        if not bars or len(bars) < 5:
            return []

        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        pip_size = self._pip_size(current_price)
        zone_half_range = pip_size * 50  # ~5 pips zone width

        zones: list[SupportResistanceZone] = []

        # Swing lows (support)
        for i in range(2, len(lows) - 2):
            if (lows[i] <= lows[i - 1] and lows[i] <= lows[i - 2]  # noqa: SIM102
                    and lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]):
                if lows[i] < current_price:
                    strength = 3 if i < len(lows) - 4 and lows[i] <= lows[i + 3] else 2
                    zones.append(SupportResistanceZone(
                        zone_type="support",
                        price=round(lows[i], 2),
                        zone_low=round(lows[i] - zone_half_range, 2),
                        zone_high=round(lows[i] + zone_half_range, 2),
                        strength=strength,
                    ))

        # Swing highs (resistance)
        for i in range(2, len(highs) - 2):
            if (highs[i] >= highs[i - 1] and highs[i] >= highs[i - 2]  # noqa: SIM102
                    and highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]):
                if highs[i] > current_price:
                    strength = 3 if i < len(highs) - 4 and highs[i] >= highs[i + 3] else 2
                    zones.append(SupportResistanceZone(
                        zone_type="resistance",
                        price=round(highs[i], 2),
                        zone_low=round(highs[i] - zone_half_range, 2),
                        zone_high=round(highs[i] + zone_half_range, 2),
                        strength=strength,
                    ))

        # Sort and deduplicate
        supports = sorted(
            [z for z in zones if z.zone_type == "support"],
            key=lambda z: z.price, reverse=True,
        )[:3]
        resistances = sorted(
            [z for z in zones if z.zone_type == "resistance"],
            key=lambda z: z.price,
        )[:3]

        return supports + resistances

    @staticmethod
    def _pip_size(price: float) -> float:
        if price > 1000:
            return 0.10
        if price > 100:
            return 0.01
        return 0.0001

    @staticmethod
    def _determine_momentum(mapping: DailyMapping) -> str:
        if mapping.dxy is not None:
            if mapping.dxy < 102:
                return "BULLISH"
            if mapping.dxy > 104:
                return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def format_mapping_text(mapping: DailyMapping) -> str:
        """Format a daily mapping as a Telegram-friendly text message."""
        lines: list[str] = []
        lines.append("📊 <b>DAILY MARKET MAPPING</b>")
        lines.append(f"🗓 {mapping.date} | Session: {mapping.current_session}")
        lines.append("━" * 20)
        lines.append("")

        if mapping.dxy:
            emoji = "🟢" if mapping.dxy < 103 else "🔴"
            lines.append(f"💵 <b>DXY:</b> {emoji} {mapping.dxy:.2f}")
            if mapping.dxy < 102:
                lines.append("   → DXY weak → bullish for Gold & majors")
            elif mapping.dxy > 104:
                lines.append("   → DXY strong → bearish pressure for Gold & majors")
            else:
                lines.append("   → DXY neutral — wait for breakout")
            lines.append("")

        if mapping.prices:
            lines.append("━" * 20)
            lines.append("💰 <b>KEY PRICES</b>")
            lines.append("")
            for sym, info in sorted(mapping.prices.items()):
                price = info.get("price", 0)
                chg = info.get("change", 0)
                emoji = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
                lines.append(f"   {emoji} <b>{sym}:</b> {price:.4f} ({chg:+.2f}%)")
            lines.append("")

        for sym, zones in mapping.zones.items():
            if not zones:
                continue
            lines.append("━" * 20)
            lines.append(f"🥇 <b>{sym} ZONES (H4)</b>")
            lines.append("")
            for z in zones:
                label = "Support" if z.zone_type == "support" else "Resistance"
                icon = "📈" if z.zone_type == "support" else "📉"
                lines.append(
                    f"   {icon} <b>{label}:</b> {z.zone_low}-{z.zone_high}"
                )
            lines.append("")

        if mapping.momentum != "NEUTRAL":
            emoji = "🟢" if mapping.momentum == "BULLISH" else "🔴"
            lines.append(f"🚀 <b>Momentum:</b> {emoji} {mapping.momentum}")
            lines.append("")

        if mapping.is_nfp_friday:
            lines.append("⚠️ <b>NFP Friday</b> — Expect high volatility! Trade cautiously.")
            lines.append("")

        return "\n".join(lines)


__all__ = [
    "MarketAnalyzer",
    "SessionLevels",
    "SupportResistanceZone",
    "DailyMapping",
]
