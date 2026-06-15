"""Tests for SignalExecutor — signal-to-order execution pipeline."""

from __future__ import annotations

import pytest

from tests.fixtures.mock_providers import MockProvider
from trading_bot.engine import (
    EventBus,
    PortfolioTracker,
    RiskManager,
    SignalExecutor,
)
from trading_bot.engine.events import SIGNAL, signal_event
from trading_bot.providers.base import OrderSide
from trading_bot.strategies.base import StrategySignal


class TestSignalExecutor:
    """SignalExecutor construction and execution."""

    @pytest.fixture
    def executor(self) -> SignalExecutor:
        provider = MockProvider()
        risk = RiskManager()
        portfolio = PortfolioTracker(initial_balance=10_000.0)
        return SignalExecutor(
            provider=provider,
            risk_manager=risk,
            portfolio=portfolio,
            event_bus=EventBus(),
        )

    async def test_initial_state(self, executor: SignalExecutor) -> None:
        status = executor.get_status()
        assert status["running"] is False
        assert status["last_order"] is None

    async def test_start_stop(self, executor: SignalExecutor) -> None:
        await executor.start()
        assert executor._running is True
        await executor.stop()
        assert executor._running is False

    async def test_execute_rejected_when_not_running(
        self, executor: SignalExecutor,
    ) -> None:
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.7,
            price=2500.0,
            strategy_name="test",
        )
        result = await executor.execute(signal)
        assert result is None

    async def test_execute_success(self, executor: SignalExecutor) -> None:
        await executor.start()
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.7,
            price=2500.0,
            strategy_name="test",
        )
        result = await executor.execute(signal)
        assert result is not None
        assert result["status"] in ("filled", "pending")
        assert result["symbol"] == "XAU/USD"
        assert result["strategy"] == "test"
        assert result["order_id"] is not None

    async def test_execute_creates_position(self, executor: SignalExecutor) -> None:
        await executor.start()
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.7,
            price=2500.0,
            strategy_name="test",
        )
        await executor.execute(signal)
        positions = executor._portfolio.get_positions("XAU/USD")
        assert len(positions) == 1
        assert positions[0].side == OrderSide.BUY

    async def test_execute_updates_last_order(self, executor: SignalExecutor) -> None:
        await executor.start()
        signal = StrategySignal(
            symbol="BTC/USD",
            direction=OrderSide.SELL,
            confidence=0.8,
            price=30000.0,
            strategy_name="trend",
        )
        await executor.execute(signal)
        status = executor.get_status()
        assert status["last_order"] is not None
        assert status["last_order"]["symbol"] == "BTC/USD"

    async def test_execute_zero_balance_rejected(self) -> None:
        provider = MockProvider()
        risk = RiskManager()
        portfolio = PortfolioTracker(initial_balance=0.0)
        execr = SignalExecutor(
            provider=provider,
            risk_manager=risk,
            portfolio=portfolio,
            event_bus=EventBus(),
        )
        await execr.start()
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.5,
            price=2500.0,
            strategy_name="test",
        )
        result = await execr.execute(signal)
        # Size will be 0 due to zero balance.
        assert result is None

    async def test_event_bus_integration(self, executor: SignalExecutor) -> None:
        """SignalExecutor subscribes to SIGNAL events and processes them."""
        await executor.start()
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.7,
            price=2500.0,
            strategy_name="grid",
        )
        await executor._event_bus.publish(
            SIGNAL,
            **signal_event(signal).data,
        )
        # The executor should have processed the signal.
        positions = executor._portfolio.get_positions("XAU/USD")
        assert len(positions) == 1

    async def test_multiple_signals(self, executor: SignalExecutor) -> None:
        await executor.start()
        for i in range(3):
            signal = StrategySignal(
                symbol=f"SYM/{i}",
                direction=OrderSide.BUY,
                confidence=0.5,
                price=100.0,
                strategy_name=f"strat_{i}",
            )
            await executor.execute(signal)
        assert executor._portfolio.total_positions == 3

    async def test_get_status_after_execution(self, executor: SignalExecutor) -> None:
        await executor.start()
        signal = StrategySignal(
            symbol="XAU/USD",
            direction=OrderSide.BUY,
            confidence=0.7,
            price=2500.0,
            strategy_name="test",
        )
        await executor.execute(signal)
        status = executor.get_status()
        assert status["running"] is True
        assert status["last_order"] is not None
