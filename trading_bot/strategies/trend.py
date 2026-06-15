"""Trend-following strategy — SMA crossover with optional volume confirmation.

Computes fast and slow simple moving averages on the most recent candles
and generates a signal when a crossover is detected.  An optional volume
filter can suppress signals when volume is below the average.
"""

import logging
from typing import Any

from trading_bot.providers.base import BaseProvider, OrderSide
from trading_bot.strategies.base import BaseStrategy, StrategySignal

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Trend configuration
# ---------------------------------------------------------------------------


class TrendConfig:
    """Parameters that control trend-following behaviour.

    Attributes:
        fast_period: Number of candles for the fast moving average.
        slow_period: Number of candles for the slow moving average.
        min_confidence: Minimum confidence (0.0–1.0) below which signals
            are suppressed.
        use_ema: Use EMA instead of SMA when ``True``.
        volume_filter: Require volume > N-period average when ``True``.
        volume_period: Period for volume average calculation.
    """

    def __init__(  # noqa: PLR0913
        self,
        fast_period: int = 10,
        slow_period: int = 30,
        min_confidence: float = 0.4,
        use_ema: bool = False,
        volume_filter: bool = False,
        volume_period: int = 20,
    ) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.min_confidence = min_confidence
        self.use_ema = use_ema
        self.volume_filter = volume_filter
        self.volume_period = volume_period


# ---------------------------------------------------------------------------
#  Trend strategy
# ---------------------------------------------------------------------------


class TrendStrategy(BaseStrategy):
    """Moving-average crossover trend-following strategy.

    Generates a BUY signal when the fast MA crosses above the slow MA,
    and a SELL signal when the fast MA crosses below the slow MA.

    Example params::

        {
            "fast_period": 10,
            "slow_period": 30,
            "use_ema": True,
            "volume_filter": True,
        }
    """

    def __init__(
        self,
        provider: BaseProvider,
        params: dict | None = None,
    ) -> None:
        super().__init__(provider, params)
        cfg_raw = self._params
        self._config = TrendConfig(
            fast_period=int(cfg_raw.get("fast_period", 10)),
            slow_period=int(cfg_raw.get("slow_period", 30)),
            min_confidence=float(cfg_raw.get("min_confidence", 0.4)),
            use_ema=bool(cfg_raw.get("use_ema", False)),
            volume_filter=bool(cfg_raw.get("volume_filter", False)),
            volume_period=int(cfg_raw.get("volume_period", 20)),
        )
        # Keep the last two computed MA values so we detect actual
        # crossovers (not just current-relative position).
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    # ── BaseStrategy ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "trend"

    async def analyze(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> StrategySignal | None:
        candles = await self._fetch_candles(
            symbol,
            timeframe,
            limit=self._config.slow_period + 5,
        )
        if len(candles) < self._config.slow_period:
            return None

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles] if self._config.volume_filter else []

        fast_ma = self._ma(closes, self._config.fast_period)
        slow_ma = self._ma(closes, self._config.slow_period)

        if fast_ma is None or slow_ma is None:
            return None

        # Crossover detection.
        prev_fast = self._prev_fast
        prev_slow = self._prev_slow
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

        if prev_fast is None or prev_slow is None:
            return None

        fast_crossed_above = prev_fast <= prev_slow and fast_ma > slow_ma
        fast_crossed_below = prev_fast >= prev_slow and fast_ma < slow_ma

        if not fast_crossed_above and not fast_crossed_below:
            return None

        # Determine direction and confidence.
        direction = OrderSide.BUY if fast_crossed_above else OrderSide.SELL
        raw_spread = abs(fast_ma - slow_ma) / max(slow_ma, 1e-10)
        confidence = min(raw_spread * 10, 0.95)

        if confidence < self._config.min_confidence:
            LOG.debug("Trend signal confidence %.3f below threshold", confidence)
            return None

        # Volume filter.
        if self._config.volume_filter and volumes:
            recent_volume = volumes[-1]
            avg_volume = sum(volumes[-self._config.volume_period:]) / max(
                len(volumes[-self._config.volume_period:]), 1
            )
            if avg_volume > 0 and recent_volume < avg_volume:
                LOG.debug("Trend signal suppressed by volume filter")
                return None

        current_price = closes[-1]

        return StrategySignal(
            symbol=symbol,
            strategy_name=self.name,
            direction=direction,
            confidence=confidence,
            price=current_price,
            metadata={
                "fast_ma": round(fast_ma, 6),
                "slow_ma": round(slow_ma, 6),
                "fast_period": self._config.fast_period,
                "slow_period": self._config.slow_period,
                "use_ema": self._config.use_ema,
            },
        )

    # ── helpers ───────────────────────────────────────────────────────

    def _ma(self, values: list[float], period: int) -> float | None:
        """Compute SMA or EMA over the last ``period`` values."""
        if len(values) < period:
            return None
        recent = values[-period:]
        if self._config.use_ema:
            return self._ema(recent)
        return sum(recent) / period

    @staticmethod
    def _ema(values: list[float]) -> float:
        """Exponential moving average (last = most recent)."""
        k = 2.0 / (len(values) + 1)
        ema = values[0]
        for v in values[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    # ── public helpers ────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return current MA values and signal state."""
        return {
            "prev_fast": self._prev_fast,
            "prev_slow": self._prev_slow,
            "fast_period": self._config.fast_period,
            "slow_period": self._config.slow_period,
        }
