"""Integration tests — end-to-end flow: strategy → engine → executor → provider."""

from __future__ import annotations

import pytest

from tests.fixtures.mock_providers import MockProvider
from trading_bot.engine import (
    EngineState,
    EventBus,
    PortfolioTracker,
    RiskConfig,
    RiskManager,
    SignalExecutor,
    TradingOrchestrator,
)
from trading_bot.providers.base import OrderSide, Position
from trading_bot.strategies.base import BaseStrategy, StrategySignal


class _TestStrategy(BaseStrategy):
    """Simple strategy that returns a fixed signal."""

    def __init__(self, provider: MockProvider) -> None:
        super().__init__(provider)
        self._call_count = 0

    @property
    def name(self) -> str:
        return "test_integration"

    async def analyze(
        self, symbol: str, timeframe: str = "1h",
    ) -> StrategySignal | None:
        self._call_count += 1
        return StrategySignal(
            symbol=symbol,
            direction=OrderSide.BUY,
            confidence=0.8,
            price=100.0,
            strategy_name=self.name,
        )

    @property
    def call_count(self) -> int:
        return self._call_count


class TestFullPipeline:
    """End-to-end integration test wiring all components together."""

    @pytest.fixture
    def components(self) -> dict:
        provider = MockProvider()
        provider._inject_candles([_candle(100.0)])

        event_bus = EventBus()
        portfolio = PortfolioTracker(initial_balance=10_000.0)
        risk = RiskManager(RiskConfig(
            max_risk_per_trade_pct=1.0,
            max_drawdown_pct=20.0,
            max_open_positions=5,
            max_position_size_pct=10.0,
        ))

        executor = SignalExecutor(
            provider=provider,
            risk_manager=risk,
            portfolio=portfolio,
            event_bus=event_bus,
        )

        orchestrator = TradingOrchestrator(
            event_bus=event_bus,
            risk_manager=risk,
            portfolio=portfolio,
        )

        strategy = _TestStrategy(provider)
        orchestrator.register_strategy(strategy)

        return {
            "provider": provider,
            "event_bus": event_bus,
            "portfolio": portfolio,
            "risk": risk,
            "executor": executor,
            "orchestrator": orchestrator,
            "strategy": strategy,
        }

    async def test_full_cycle(self, components: dict) -> None:
        """Start orchestrator + executor → run cycle → signal → execute."""
        orch = components["orchestrator"]
        execr = components["executor"]
        strategy = components["strategy"]

        await execr.start()
        await orch.start()
        assert orch.state == EngineState.RUNNING

        signals = await orch.run_cycle("XAU/USD", "1h")
        assert len(signals) == 1
        assert signals[0].strategy_name == "test_integration"
        assert strategy.call_count == 1

        # Executor should have processed the signal via event bus.
        portfolio = components["portfolio"]
        assert portfolio.total_positions == 1

        positions = portfolio.get_positions("XAU/USD")
        assert len(positions) == 1
        assert positions[0].side == OrderSide.BUY

    async def test_cycle_without_executor(self, components: dict) -> None:
        """Orchestrator cycle still works without executor running."""
        orch = components["orchestrator"]
        await orch.start()

        signals = await orch.run_cycle("XAU/USD", "1h")
        assert len(signals) == 1

        # No executor → no positions tracked.
        portfolio = components["portfolio"]
        assert portfolio.total_positions == 0

    async def test_stop_cleanup(self, components: dict) -> None:
        """Stop both orchestrator and executor gracefully."""
        orch = components["orchestrator"]
        execr = components["executor"]

        await execr.start()
        await orch.start()
        await orch.stop()
        await execr.stop()

        assert orch.state == EngineState.STOPPED
        assert execr._running is False

    async def test_multiple_cycles(self, components: dict) -> None:
        """Multiple cycles produce multiple signals and positions."""
        orch = components["orchestrator"]
        execr = components["executor"]
        strategy = components["strategy"]

        await execr.start()
        await orch.start()

        for _ in range(3):
            signals = await orch.run_cycle("XAU/USD", "1h")
            assert len(signals) == 1

        assert strategy.call_count == 3
        portfolio = components["portfolio"]
        assert portfolio.total_positions == 3

    async def test_status_reporting(self, components: dict) -> None:
        orch = components["orchestrator"]
        execr = components["executor"]

        await execr.start()
        await orch.start()

        orch_status = orch.get_status()
        assert orch_status["state"] == "RUNNING"
        assert "test_integration" in orch_status["strategies"]

        exec_status = execr.get_status()
        assert exec_status["running"] is True


# ===========================================================================
#  Helpers
# ===========================================================================


def _candle(close: float, volume: float = 1000.0) -> _candle_type:
    import datetime

    from trading_bot.providers.base import Candle

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


# Type alias for the return type annotation.
_candle_type = object


class TestPipelineEdgeCases:
    """Edge cases and error paths in the full pipeline."""

    async def test_engine_start_failure(self) -> None:
        """Strategy.on_start() raises → orchestrator transitions to ERROR."""
        provider = MockProvider()
        event_bus = EventBus()
        portfolio = PortfolioTracker(initial_balance=10_000.0)
        risk = RiskManager(RiskConfig(max_risk_per_trade_pct=1.0, max_drawdown_pct=20.0))

        class _FailingStrategy(BaseStrategy):
            """Strategy whose on_start() raises."""
            @property
            def name(self) -> str:
                return "failing"
            async def analyze(
                self, symbol: str, timeframe: str = "1h",
            ) -> StrategySignal | None:
                return None
            async def on_start(self) -> None:
                raise RuntimeError("start failed")

        orch = TradingOrchestrator(
            event_bus=event_bus, risk_manager=risk, portfolio=portfolio,
        )
        orch.register_strategy(_FailingStrategy(provider))
        await orch.start()
        assert orch.state == EngineState.ERROR
        assert "start failed" in (orch._last_error or "")

    async def test_run_cycle_drawdown_breach(self) -> None:
        """Run cycle rejects signals when drawdown exceeds limit."""
        provider = MockProvider()
        event_bus = EventBus()
        portfolio = PortfolioTracker(initial_balance=10_000.0)
        risk = RiskManager(RiskConfig(
            max_risk_per_trade_pct=1.0,
            max_drawdown_pct=10.0,
        ))

        # Inflate equity peak so current equity is in drawdown.
        portfolio._equity_peak = 20_000.0  # 100% drawdown (10k from 20k)

        orch = TradingOrchestrator(
            event_bus=event_bus, risk_manager=risk, portfolio=portfolio,
        )
        strategy = _TestStrategy(provider)
        orch.register_strategy(strategy)
        await orch.start()

        signals = await orch.run_cycle("XAU/USD", "1h")
        assert signals == []  # filtered by drawdown check

    async def test_run_cycle_position_limit_reached(self) -> None:
        """Run cycle rejects signals when max positions reached."""
        provider = MockProvider()
        event_bus = EventBus()
        portfolio = PortfolioTracker(initial_balance=10_000.0)
        risk = RiskManager(RiskConfig(
            max_risk_per_trade_pct=1.0,
            max_open_positions=1,
            max_position_size_pct=10.0,
        ))

        portfolio.add_position(Position(
            symbol="XAU/USD", side=OrderSide.BUY, quantity=1.0,
            entry_price=100.0, current_price=100.0,
            unrealized_pnl=0.0, realized_pnl=0.0,
        ))

        orch = TradingOrchestrator(
            event_bus=event_bus, risk_manager=risk, portfolio=portfolio,
        )
        strategy = _TestStrategy(provider)
        orch.register_strategy(strategy)
        await orch.start()

        signals = await orch.run_cycle("XAU/USD", "1h")
        assert signals == []
        status = orch.get_status()
        assert status["state"] == "RUNNING"
