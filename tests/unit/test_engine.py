"""Tests for trading engine — EventBus, PortfolioTracker, RiskManager, TradingOrchestrator.

All tests use MockProvider; no real API keys or network calls.
"""

from __future__ import annotations

import pytest

from tests.fixtures.mock_providers import MockProvider
from trading_bot.engine import (
    EngineState,
    Event,
    EventBus,
    PortfolioTracker,
    RiskConfig,
    RiskManager,
    TradingOrchestrator,
)
from trading_bot.providers.base import Order, OrderSide, OrderType, Position
from trading_bot.strategies.base import StrategySignal

# ===========================================================================
#  Event / EventBus
# ===========================================================================


class TestEvent:
    """Event dataclass construction."""

    def test_minimal(self) -> None:
        e = Event(type="test")
        assert e.type == "test"
        assert e.data == {}
        assert e.timestamp is not None

    def test_with_data(self) -> None:
        e = Event(type="signal", data={"symbol": "XAU/USD"})
        assert e.data["symbol"] == "XAU/USD"


class TestEventBus:
    """EventBus publish/subscribe."""

    @pytest.fixture
    def bus(self) -> EventBus:
        return EventBus()

    async def test_subscribe_and_publish(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test_event", handler)
        await bus.publish("test_event", value=42)
        assert len(received) == 1
        assert received[0].data["value"] == 42

    async def test_multiple_handlers(self, bus: EventBus) -> None:
        results: list[int] = []

        async def h1(event: Event) -> None:
            results.append(1)

        async def h2(event: Event) -> None:
            results.append(2)

        bus.subscribe("ev", h1)
        bus.subscribe("ev", h2)
        await bus.publish("ev")
        assert results == [1, 2]

    async def test_unsubscribe(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("ev", handler)
        bus.unsubscribe("ev", handler)
        await bus.publish("ev")
        assert len(received) == 0

    async def test_handler_error_does_not_crash_bus(self, bus: EventBus) -> None:
        """A failing handler does not affect other handlers."""
        results: list[str] = []

        async def failing(event: Event) -> None:
            msg = "fail"
            raise ValueError(msg)

        async def ok(event: Event) -> None:
            results.append("ok")

        bus.subscribe("ev", failing)
        bus.subscribe("ev", ok)
        await bus.publish("ev")
        assert results == ["ok"]

    async def test_clear(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("ev", handler)
        bus.clear()
        await bus.publish("ev")
        assert len(received) == 0

    async def test_no_subscribers_no_error(self, bus: EventBus) -> None:
        """Publishing with zero subscribers is a no-op."""
        await bus.publish("unregistered_event")  # should not raise

    async def test_different_event_types(self, bus: EventBus) -> None:
        received_a: list[Event] = []
        received_b: list[Event] = []

        async def ha(event: Event) -> None:
            received_a.append(event)

        async def hb(event: Event) -> None:
            received_b.append(event)

        bus.subscribe("a", ha)
        bus.subscribe("b", hb)
        await bus.publish("a")
        await bus.publish("b")
        assert len(received_a) == 1
        assert len(received_b) == 1


# ===========================================================================
#  PortfolioTracker
# ===========================================================================


class TestPortfolioTracker:
    """PortfolioTracker balance, positions, P&L."""

    @pytest.fixture
    def portfolio(self) -> PortfolioTracker:
        return PortfolioTracker(initial_balance=10_000.0)

    def test_initial_state(self, portfolio: PortfolioTracker) -> None:
        assert portfolio.balance == 10_000.0
        assert portfolio.initial_balance == 10_000.0
        assert portfolio.total_positions == 0
        assert portfolio.unrealized_pnl() == 0.0
        assert portfolio.total_equity() == 10_000.0

    def test_set_balance(self, portfolio: PortfolioTracker) -> None:
        portfolio.set_balance(5_000.0)
        assert portfolio.balance == 5_000.0

    def test_add_and_get_positions(self, portfolio: PortfolioTracker) -> None:
        pos = Position(symbol="XAU/USD", side=OrderSide.BUY, quantity=1.0,
                       entry_price=2500.0, current_price=2510.0,
                       unrealized_pnl=10.0, realized_pnl=0.0)
        portfolio.add_position(pos)
        assert portfolio.total_positions == 1
        positions = portfolio.get_positions("XAU/USD")
        assert len(positions) == 1
        assert positions[0].entry_price == 2500.0

    def test_get_positions_all(self, portfolio: PortfolioTracker) -> None:
        p1 = Position(symbol="XAU/USD", side=OrderSide.BUY, quantity=1.0,
                      entry_price=2500.0, current_price=2510.0,
                      unrealized_pnl=10.0, realized_pnl=0.0)
        p2 = Position(symbol="BTC/USD", side=OrderSide.BUY, quantity=0.1,
                      entry_price=30000.0, current_price=31000.0,
                      unrealized_pnl=100.0, realized_pnl=0.0)
        portfolio.add_position(p1)
        portfolio.add_position(p2)
        all_pos = portfolio.get_positions()
        assert len(all_pos) == 2

    def test_get_positions_by_symbol(self, portfolio: PortfolioTracker) -> None:
        p1 = Position(symbol="XAU/USD", side=OrderSide.BUY, quantity=1.0,
                      entry_price=2500.0, current_price=2510.0,
                      unrealized_pnl=10.0, realized_pnl=0.0)
        p2 = Position(symbol="BTC/USD", side=OrderSide.BUY, quantity=0.1,
                      entry_price=30000.0, current_price=31000.0,
                      unrealized_pnl=100.0, realized_pnl=0.0)
        portfolio.add_position(p1)
        portfolio.add_position(p2)
        btc = portfolio.get_positions("BTC/USD")
        assert len(btc) == 1
        assert btc[0].current_price == 31000.0
        empty = portfolio.get_positions("ETH/USD")
        assert empty == []

    def test_unrealized_pnl(self, portfolio: PortfolioTracker) -> None:
        p1 = Position(symbol="A", side=OrderSide.BUY, quantity=1.0,
                      entry_price=100, current_price=110,
                      unrealized_pnl=10.0, realized_pnl=0.0)
        p2 = Position(symbol="B", side=OrderSide.BUY, quantity=1.0,
                      entry_price=100, current_price=90,
                      unrealized_pnl=-10.0, realized_pnl=0.0)
        portfolio.add_position(p1)
        portfolio.add_position(p2)
        assert portfolio.unrealized_pnl() == 0.0  # 10 + (-10) = 0

    def test_realized_pnl(self, portfolio: PortfolioTracker) -> None:
        p1 = Position(symbol="A", side=OrderSide.BUY, quantity=1.0,
                      entry_price=100, current_price=0,
                      unrealized_pnl=0.0, realized_pnl=50.0)
        p2 = Position(symbol="B", side=OrderSide.BUY, quantity=1.0,
                      entry_price=100, current_price=0,
                      unrealized_pnl=0.0, realized_pnl=-20.0)
        portfolio._closed_positions = [p1, p2]
        assert portfolio.realized_pnl() == 30.0

    def test_total_equity(self, portfolio: PortfolioTracker) -> None:
        portfolio.set_balance(10_000.0)
        p = Position(symbol="A", side=OrderSide.BUY, quantity=1.0,
                     entry_price=100, current_price=110,
                     unrealized_pnl=500.0, realized_pnl=0.0)
        portfolio.add_position(p)
        assert portfolio.total_equity() == 10_500.0

    def test_drawdown(self, portfolio: PortfolioTracker) -> None:
        portfolio.set_balance(10_000.0)
        assert portfolio.drawdown() == 0.0
        # Manually lower peak for testing.
        portfolio._equity_peak = 12_000.0
        portfolio.set_balance(10_000.0)
        assert portfolio.drawdown() == 2_000.0
        assert portfolio.drawdown_pct() == pytest.approx(16.67, rel=0.01)

    def test_drawdown_no_peak(self) -> None:
        pt = PortfolioTracker(initial_balance=0.0)
        assert pt.drawdown() == 0.0
        assert pt.drawdown_pct() == 0.0

    def test_get_summary(self, portfolio: PortfolioTracker) -> None:
        summary = portfolio.get_summary()
        assert summary["initial_balance"] == 10_000.0
        assert summary["open_positions"] == 0
        assert summary["closed_positions"] == 0

    def test_close_position_by_symbol(self, portfolio: PortfolioTracker) -> None:
        p = Position(symbol="XAU/USD", side=OrderSide.BUY, quantity=1.0,
                     entry_price=2500.0, current_price=2510.0,
                     unrealized_pnl=10.0, realized_pnl=0.0)
        portfolio.add_position(p)
        closed = portfolio.close_position("XAU/USD")
        assert closed is not None
        assert closed.symbol == "XAU/USD"
        assert portfolio.total_positions == 0

    def test_close_position_not_found(self, portfolio: PortfolioTracker) -> None:
        result = portfolio.close_position("NONEXISTENT")
        assert result is None


# ===========================================================================
#  RiskManager
# ===========================================================================


class TestRiskConfig:
    """RiskConfig defaults and construction."""

    def test_defaults(self) -> None:
        cfg = RiskConfig()
        assert cfg.max_risk_per_trade_pct == 1.0
        assert cfg.max_drawdown_pct == 20.0
        assert cfg.max_open_positions == 5

    def test_custom(self) -> None:
        cfg = RiskConfig(
            max_risk_per_trade_pct=2.0,
            max_drawdown_pct=15.0,
            max_open_positions=3,
        )
        assert cfg.max_risk_per_trade_pct == 2.0
        assert cfg.max_drawdown_pct == 15.0
        assert cfg.max_open_positions == 3


class TestRiskManager:
    """RiskManager position sizing, validation, drawdown."""

    @pytest.fixture
    def risk(self) -> RiskManager:
        return RiskManager(RiskConfig(
            max_risk_per_trade_pct=1.0,
            max_drawdown_pct=20.0,
            max_open_positions=3,
            max_position_size_pct=10.0,
            max_exposure_per_symbol_pct=30.0,
        ))

    def test_config_property(self, risk: RiskManager) -> None:
        assert risk.config.max_risk_per_trade_pct == 1.0

    # ── position sizing ──

    def test_position_size_fixed_fractional(self, risk: RiskManager) -> None:
        """With no Kelly config, size is based on risk % of balance."""
        size = risk.calculate_position_size(balance=10_000.0, price=100.0)
        # 1% of 10_000 = 100, capped at 10% = 1000 → min is 100
        assert size == 100.0

    def test_position_size_capped_by_max(self, risk: RiskManager) -> None:
        size = risk.calculate_position_size(balance=1_000.0, price=100.0)
        # 1% of 1000 = 10, max position size = 10% of 1000 = 100 → min is 10
        assert size == 10.0

    def test_position_size_kelly(self) -> None:
        risk = RiskManager(RiskConfig(
            kelly_fraction=0.5,
            max_risk_per_trade_pct=10.0,
            max_position_size_pct=50.0,
        ))
        size = risk.calculate_position_size(
            balance=10_000.0, price=100.0,
            win_rate=0.6, avg_win=200.0, avg_loss=100.0,
        )
        # Kelly % = 0.6 - (0.4 / 2.0) = 0.6 - 0.2 = 0.4 (40%)
        # Fraction: 0.4 * 0.5 = 0.2 = 20%
        # size = 10_000 * 0.2 = 2_000, capped at 50% = 5_000 → 2_000
        assert size == pytest.approx(2_000.0, rel=0.01)

    def test_position_size_kelly_negative(self) -> None:
        """When Kelly % is negative, size is 0."""
        risk = RiskManager(RiskConfig(kelly_fraction=0.5))
        size = risk.calculate_position_size(
            balance=10_000.0, price=100.0,
            win_rate=0.3, avg_win=100.0, avg_loss=200.0,
        )
        # Kelly = 0.3 - (0.7 / 0.5) = 0.3 - 1.4 = -1.1 → clamped to 0
        assert size == 0.0

    # ── order validation ──

    def test_validate_order_approved(self, risk: RiskManager) -> None:
        order = Order(symbol="XAU/USD", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=1.0, price=2500.0)
        ok, reason = risk.validate_order(order, [], 10_000.0)
        assert ok is True
        assert reason == "approved"

    def test_validate_order_max_positions(self, risk: RiskManager) -> None:
        order = Order(symbol="XAU/USD", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=1.0, price=2500.0)
        existing = [
            Position(symbol="A", side=OrderSide.BUY, quantity=1.0,
                     entry_price=100, current_price=100,
                     unrealized_pnl=0.0, realized_pnl=0.0),
            Position(symbol="B", side=OrderSide.BUY, quantity=1.0,
                     entry_price=100, current_price=100,
                     unrealized_pnl=0.0, realized_pnl=0.0),
            Position(symbol="C", side=OrderSide.BUY, quantity=1.0,
                     entry_price=100, current_price=100,
                     unrealized_pnl=0.0, realized_pnl=0.0),
        ]
        ok, reason = risk.validate_order(order, existing, 10_000.0)
        assert ok is False
        assert "max open positions" in reason

    def test_validate_order_exposure_limit(self, risk: RiskManager) -> None:
        order = Order(symbol="XAU/USD", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=100.0, price=2500.0)
        existing = [
            Position(symbol="XAU/USD", side=OrderSide.BUY, quantity=1.0,
                     entry_price=2500.0, current_price=2500.0,
                     unrealized_pnl=0.0, realized_pnl=0.0),
        ]
        ok, reason = risk.validate_order(order, existing, 10_000.0)
        # exposure: 1*2500 + 100*2500 = 252_500; limit: 30% of 10k = 3_000
        assert ok is False
        assert "exceeds" in reason

    # ── drawdown ──

    def test_check_drawdown_ok(self, risk: RiskManager) -> None:
        ok, reason = risk.check_drawdown(equity=9_000.0, peak=10_000.0)
        assert ok is True
        assert reason == "ok"

    def test_check_drawdown_exceeded(self, risk: RiskManager) -> None:
        ok, reason = risk.check_drawdown(equity=7_000.0, peak=10_000.0)
        assert ok is False
        assert "20.0%" in reason

    def test_check_drawdown_zero_peak(self, risk: RiskManager) -> None:
        ok, reason = risk.check_drawdown(equity=0.0, peak=0.0)
        assert ok is True

    # ── position limits ──

    def test_check_position_limits_ok(self, risk: RiskManager) -> None:
        ok, reason = risk.check_position_limits([], 10_000.0)
        assert ok is True

    def test_check_position_limits_exceeded(self, risk: RiskManager) -> None:
        positions = [Position(symbol="A", side=OrderSide.BUY, quantity=1.0,
                              entry_price=100, current_price=100,
                              unrealized_pnl=0.0, realized_pnl=0.0)] * 3
        ok, reason = risk.check_position_limits(positions, 10_000.0)
        assert ok is False
        assert "max open positions" in reason

    # ── status ──

    def test_get_status(self, risk: RiskManager) -> None:
        status = risk.get_status()
        assert "max_risk_per_trade_pct" in status
        assert "max_drawdown_pct" in status
        assert status["max_risk_per_trade_pct"] == 1.0


# ===========================================================================
#  TradingOrchestrator
# ===========================================================================


class TestTradingOrchestrator:
    """Engine state machine, lifecycle, cycle."""

    @pytest.fixture
    def orchestrator(self) -> TradingOrchestrator:
        return TradingOrchestrator(
            event_bus=EventBus(),
            risk_manager=RiskManager(),
            portfolio=PortfolioTracker(initial_balance=10_000.0),
        )

    def test_initial_state(self, orchestrator: TradingOrchestrator) -> None:
        assert orchestrator.state == EngineState.IDLE
        assert orchestrator._cycle_count == 0
        assert orchestrator.get_status()["state"] == "IDLE"

    async def test_start_transitions(self, orchestrator: TradingOrchestrator) -> None:
        await orchestrator.start()
        assert orchestrator.state == EngineState.RUNNING

    async def test_start_idempotent(self, orchestrator: TradingOrchestrator) -> None:
        await orchestrator.start()
        await orchestrator.start()  # should be no-op
        assert orchestrator.state == EngineState.RUNNING

    async def test_stop(self, orchestrator: TradingOrchestrator) -> None:
        await orchestrator.start()
        await orchestrator.stop()
        assert orchestrator.state == EngineState.STOPPED

    async def test_pause_resume(self, orchestrator: TradingOrchestrator) -> None:
        await orchestrator.start()
        await orchestrator.pause()
        assert orchestrator.state == EngineState.PAUSED
        await orchestrator.resume()
        assert orchestrator.state == EngineState.RUNNING

    async def test_pause_when_not_running(self, orchestrator: TradingOrchestrator) -> None:
        await orchestrator.pause()  # IDLE → no-op
        assert orchestrator.state == EngineState.IDLE

    async def test_cycle_not_running_returns_empty(
        self, orchestrator: TradingOrchestrator,
    ) -> None:
        signals = await orchestrator.run_cycle("XAU/USD")
        assert signals == []

    async def test_register_strategy(self, orchestrator: TradingOrchestrator) -> None:
        strategy = _make_mock_strategy("test_strat")
        orchestrator.register_strategy(strategy)
        assert "test_strat" in orchestrator.strategies

    async def test_unregister_strategy(self, orchestrator: TradingOrchestrator) -> None:
        strategy = _make_mock_strategy("test_strat")
        orchestrator.register_strategy(strategy)
        orchestrator.unregister_strategy("test_strat")
        assert "test_strat" not in orchestrator.strategies

    async def test_get_strategy(self, orchestrator: TradingOrchestrator) -> None:
        strategy = _make_mock_strategy("test_strat")
        orchestrator.register_strategy(strategy)
        assert orchestrator.get_strategy("test_strat") is strategy
        assert orchestrator.get_strategy("nonexistent") is None

    async def test_run_cycle_with_strategy(self, orchestrator: TradingOrchestrator) -> None:
        strategy = _make_mock_strategy("grid")
        orchestrator.register_strategy(strategy)
        await orchestrator.start()
        signals = await orchestrator.run_cycle("XAU/USD", "1h")
        assert len(signals) == 1
        assert signals[0].strategy_name == "grid"

    async def test_run_cycle_strategy_returns_none(
        self, orchestrator: TradingOrchestrator,
    ) -> None:
        strategy = _make_mock_strategy("grid", return_none=True)
        orchestrator.register_strategy(strategy)
        await orchestrator.start()
        signals = await orchestrator.run_cycle("XAU/USD")
        assert signals == []

    async def test_get_status_after_cycles(self, orchestrator: TradingOrchestrator) -> None:
        await orchestrator.start()
        status = orchestrator.get_status()
        assert status["state"] == "RUNNING"
        assert "strategies" in status


# ===========================================================================
#  Helpers
# ===========================================================================


def _make_mock_strategy(name: str, return_none: bool = False) -> MockProvider:
    """Create a MockProvider subclass that acts as a BaseStrategy for testing.

    Returns the mock instance which doubles as a strategy.
    """
    from trading_bot.strategies.base import BaseStrategy

    class MockStrategy(BaseStrategy):
        def __init__(self) -> None:
            super().__init__(MockProvider())
            self._name = name
            self._return_none = return_none

        @property
        def name(self) -> str:
            return self._name

        async def analyze(
            self, symbol: str, timeframe: str = "1h",
        ) -> StrategySignal | None:
            if self._return_none:
                return None
            return StrategySignal(
                symbol=symbol,
                direction=OrderSide.BUY,
                confidence=0.7,
                price=100.0,
                strategy_name=self._name,
            )

    return MockStrategy()
