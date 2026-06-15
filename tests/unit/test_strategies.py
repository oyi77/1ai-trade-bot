"""Tests for trading strategies — GridStrategy and TrendStrategy.

All tests use MockProvider; no real API keys or network calls.
"""

from __future__ import annotations

import datetime

import pytest

from tests.fixtures.mock_providers import MockProvider
from trading_bot.providers.base import Candle, OrderSide
from trading_bot.strategies.base import StrategySignal
from trading_bot.strategies.grid import GridStrategy
from trading_bot.strategies.trend import TrendStrategy

# ===========================================================================
#  Helpers
# ===========================================================================


def _candle(close: float, volume: float = 1000.0) -> Candle:
    """Build a single candle with a given close price."""
    return Candle(
        symbol="XAU/USD",
        timeframe="1h",
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=volume,
        timestamp=datetime.datetime.now(datetime.timezone.utc),  # noqa: UP017
    )


def _candles(closes: list[float], volume: float = 1000.0) -> list[Candle]:
    """Build a list of candles from a sequence of close prices."""
    return [_candle(c, volume) for c in closes]


# ===========================================================================
#  StrategySignal
# ===========================================================================


class TestStrategySignal:
    """StrategySignal dataclass construction."""

    def test_minimal(self) -> None:
        s = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.7,
            price=None,
            strategy_name="test",
        )
        assert s.symbol == "XAU/USD"
        assert s.direction == OrderSide.BUY
        assert s.confidence == 0.7
        assert s.price is None

    def test_full(self) -> None:
        s = StrategySignal(
            symbol="BTC/USD",
            direction=OrderSide.SELL,
            confidence=0.85,
            price=45000.0,
            strategy_name="grid",
            metadata={"grid_level": 44000.0},
        )
        assert s.strategy_name == "grid"
        assert s.metadata["grid_level"] == 44000.0
        assert s.timestamp is not None


# ===========================================================================
#  GridStrategy
# ===========================================================================


class TestGridStrategyInit:
    """GridStrategy construction and config."""

    def test_default_params(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [1.0, 1.1, 1.2], "order_size": 100})
        assert gs.name == "grid"
        assert gs._config.order_size == 100.0
        assert gs._config.levels == [1.0, 1.1, 1.2]

    def test_cooldown_ticks_default(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [1.0], "order_size": 1})
        assert gs._config.cooldown_ticks == 5

    def test_custom_params(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {
            "levels": [10, 20],
            "order_size": 0.5,
            "take_profit_pips": 50,
            "stop_loss_pips": 25,
            "max_active_orders": 3,
            "cooldown_ticks": 10,
        })
        assert gs._config.take_profit_pips == 50.0
        assert gs._config.stop_loss_pips == 25.0
        assert gs._config.max_active_orders == 3
        assert gs._config.cooldown_ticks == 10

    def test_levels_sorted(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [3, 1, 2], "order_size": 1})
        assert gs._config.levels == [1, 2, 3]

    async def test_on_start_creates_levels(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [1.0, 1.1, 1.2], "order_size": 100})
        await gs.on_start()
        assert len(gs._levels) == 3
        assert gs._levels[0].is_buy is True
        assert gs._levels[1].is_buy is False
        assert gs._levels[2].is_buy is True

    async def test_on_stop_cancels_orders(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [1.0], "order_size": 100})
        await gs.on_start()
        gs._levels[0].order_id = "mock-active"
        await gs.on_stop()
        assert gs._levels[0].order_id is None


class TestGridStrategyAnalyze:
    """GridStrategy.analyze() behaviour."""

    @pytest.fixture
    def grid(self) -> GridStrategy:
        p = MockProvider()
        return GridStrategy(p, {
            "levels": [100, 105, 110],
            "order_size": 10,
        })

    async def test_no_candles_returns_none(self, grid: GridStrategy) -> None:
        result = await grid.analyze("XAU/USD")
        assert result is None

    async def test_no_cross_signal(self, grid: GridStrategy) -> None:
        await grid.on_start()
        grid._provider._inject_candles(_candles([107.0]))
        result = await grid.analyze("XAU/USD")
        assert result is None

    async def test_cross_buy_level(self, grid: GridStrategy) -> None:
        await grid.on_start()
        grid._provider._inject_candles(_candles([100.05]))
        result = await grid.analyze("XAU/USD")
        assert result is not None
        assert result.direction == OrderSide.BUY
        assert result.strategy_name == "grid"

    async def test_cross_sell_level(self, grid: GridStrategy) -> None:
        await grid.on_start()
        grid._provider._inject_candles(_candles([104.95]))
        result = await grid.analyze("XAU/USD")
        assert result is not None
        assert result.direction == OrderSide.SELL

    async def test_cooldown_suppresses_signal(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [100], "order_size": 10, "cooldown_ticks": 5})
        await gs.on_start()
        p._inject_candles(_candles([100.05]))
        result1 = await gs.analyze("XAU/USD")
        assert result1 is not None
        result2 = await gs.analyze("XAU/USD")
        assert result2 is None

    async def test_max_active_orders_cap(self) -> None:
        p = MockProvider()
        gs = GridStrategy(
            p, {"levels": [100, 105, 110], "order_size": 10, "max_active_orders": 1},
        )
        await gs.on_start()
        p._inject_candles(_candles([100.05]))
        r1 = await gs.analyze("XAU/USD")
        assert r1 is not None
        p._inject_candles(_candles([104.95]))
        r2 = await gs.analyze("XAU/USD")
        assert r2 is None

    async def test_get_status(self, grid: GridStrategy) -> None:
        await grid.on_start()
        status = grid.get_status()
        assert "levels" in status
        assert "active_orders" in status
        assert status["last_price"] is None

    async def test_cancel_all(self, grid: GridStrategy) -> None:
        await grid.on_start()
        grid._levels[0].order_id = "mock-order"
        n = await grid.cancel_all()
        assert n == 1
        assert grid._levels[0].order_id is None


class TestGridStrategyFindCrossing:
    """_find_crossing edge cases."""

    async def test_no_cross_when_far(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [100, 200], "order_size": 1})
        await gs.on_start()
        result = gs._find_crossing(150.0)
        assert result is None

    async def test_cross_ignores_filled(self) -> None:
        p = MockProvider()
        gs = GridStrategy(p, {"levels": [100, 110], "order_size": 1})
        await gs.on_start()
        gs._levels[0].filled = True
        gs._levels[0].order_id = None
        result = gs._find_crossing(100.05)
        assert result is None


# ===========================================================================
#  TrendStrategy
# ===========================================================================


class TestTrendStrategyInit:
    """TrendStrategy construction."""

    def test_default_config(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p)
        assert ts.name == "trend"
        assert ts._config.fast_period == 10
        assert ts._config.slow_period == 30

    def test_custom_config(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p, {
            "fast_period": 5,
            "slow_period": 15,
            "min_confidence": 0.6,
            "use_ema": True,
            "volume_filter": True,
        })
        assert ts._config.fast_period == 5
        assert ts._config.slow_period == 15
        assert ts._config.min_confidence == 0.6
        assert ts._config.use_ema is True
        assert ts._config.volume_filter is True


class TestTrendStrategyAnalyze:
    """TrendStrategy.analyze() — SMA/EMA crossover detection.

    Crossover detection compares prev MA values (from the previous call)
    against current MA values.  Tests must inject **different** candles
    between the priming call and the signal call.
    """

    @pytest.fixture
    def trend(self) -> TrendStrategy:
        return TrendStrategy(MockProvider(), {
            "fast_period": 3,
            "slow_period": 7,
            "min_confidence": 0.0,
        })

    async def test_insufficient_candles(self, trend: TrendStrategy) -> None:
        result = await trend.analyze("XAU/USD")
        assert result is None

    async def test_no_crossover_no_signal(self, trend: TrendStrategy) -> None:
        """Flat prices produce no signal even after priming."""
        trend._provider._inject_candles(_candles([100.0] * 10))
        await trend.analyze("XAU/USD")
        result = await trend.analyze("XAU/USD")
        assert result is None

    async def test_buy_signal_on_fast_above_slow(self, trend: TrendStrategy) -> None:
        """Inject flat prices first (fast≈slow), then uptrend (fast>slow)."""
        # Phase 1: flat — no crossover signal.
        trend._provider._inject_candles(_candles([100.0] * 10))
        await trend.analyze("XAU/USD")

        # Phase 2: uptrend — fast MA crosses above slow MA.
        trend._provider._inject_candles(_candles(
            [100.0] * 7 + [102.0, 103.0, 104.0]
        ))
        result = await trend.analyze("XAU/USD")
        assert result is not None, (
            "Expected BUY signal when fast MA crosses above slow MA"
        )
        assert result.direction == OrderSide.BUY
        assert result.confidence > 0.0

    async def test_sell_signal_on_fast_below_slow(self, trend: TrendStrategy) -> None:
        """Inject flat first, then downtrend (fast<slow)."""
        trend._provider._inject_candles(_candles([100.0] * 10))
        await trend.analyze("XAU/USD")

        trend._provider._inject_candles(_candles(
            [100.0] * 7 + [98.0, 97.0, 96.0]
        ))
        result = await trend.analyze("XAU/USD")
        assert result is not None
        assert result.direction == OrderSide.SELL

    async def test_crossover_resets_after_flat(self, trend: TrendStrategy) -> None:
        """After a crossover, subsequent flat bars produce no signal."""
        trend._provider._inject_candles(_candles([100.0] * 10))
        await trend.analyze("XAU/USD")

        trend._provider._inject_candles(_candles(
            [100.0] * 7 + [102.0, 103.0, 104.0]
        ))
        r1 = await trend.analyze("XAU/USD")
        assert r1 is not None  # crossover detected

        # Back to flat — no new crossover.
        trend._provider._inject_candles(_candles([104.0] * 10))
        r2 = await trend.analyze("XAU/USD")
        assert r2 is None

    async def test_confidence_threshold_suppresses_signal(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p, {
            "fast_period": 3,
            "slow_period": 7,
            "min_confidence": 0.9,  # very high
        })
        p._inject_candles(_candles([100.0] * 10))
        await ts.analyze("XAU/USD")
        p._inject_candles(_candles([100.0] * 7 + [102.0, 103.0, 104.0]))
        result = await ts.analyze("XAU/USD")
        assert result is None  # confidence too low

    async def test_ema_mode(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p, {
            "fast_period": 3,
            "slow_period": 7,
            "use_ema": True,
            "min_confidence": 0.0,
        })
        p._inject_candles(_candles([100.0] * 10))
        await ts.analyze("XAU/USD")
        p._inject_candles(_candles([100.0] * 7 + [102.0, 103.0, 104.0]))
        result = await ts.analyze("XAU/USD")
        assert result is not None
        assert result.strategy_name == "trend"

    async def test_volume_filter_suppresses(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p, {
            "fast_period": 3,
            "slow_period": 7,
            "min_confidence": 0.0,
            "volume_filter": True,
            "volume_period": 5,
        })
        p._inject_candles(_candles([100.0] * 10, volume=1000))
        await ts.analyze("XAU/USD")

        # Uptrend but last candle has low volume.
        candles = _candles([100.0] * 7 + [102.0, 103.0, 104.0], volume=1000)
        candles[-1] = _candle(104.0, volume=10)
        p._inject_candles(candles)
        result = await ts.analyze("XAU/USD")
        assert result is None

    async def test_volume_filter_allows(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p, {
            "fast_period": 3,
            "slow_period": 7,
            "min_confidence": 0.0,
            "volume_filter": True,
        })
        p._inject_candles(_candles([100.0] * 10, volume=5000))
        await ts.analyze("XAU/USD")

        p._inject_candles(_candles(
            [100.0] * 7 + [102.0, 103.0, 104.0], volume=5000,
        ))
        result = await ts.analyze("XAU/USD")
        assert result is not None

    async def test_get_status(self) -> None:
        p = MockProvider()
        ts = TrendStrategy(p)
        status = ts.get_status()
        assert "fast_period" in status
        assert "slow_period" in status

    async def test_inject_candles_method(self) -> None:
        """Verify that _inject_candles makes candles available through get_candles."""
        p = MockProvider()
        p._inject_candles(_candles([1.0, 2.0, 3.0]))
        candles = await p.get_candles("XAU/USD", "1h")
        assert len(candles) == 3
