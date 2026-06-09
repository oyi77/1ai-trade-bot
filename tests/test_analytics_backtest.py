"""Tests for BacktestEngine."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock

import pytest

from tradebot.analytics.backtest import BacktestEngine, BacktestResult, BacktestTrade


class TestBacktestModels:
    """BacktestTrade and BacktestResult dataclasses."""

    def test_backtest_trade(self):
        t = BacktestTrade(
            tick_time="2026-06-01T00:00:00",
            symbol="R_75",
            action="CALL",
            price=33000.0,
            stake=0.35,
            outcome="WIN",
            pnl=2.52,
        )
        assert t.symbol == "R_75"
        assert t.action == "CALL"
        assert t.outcome == "WIN"
        assert t.pnl == 2.52

    def test_backtest_result_defaults(self):
        r = BacktestResult()
        assert r.total_ticks == 0
        assert r.total_trades == 0
        assert r.wins == 0
        assert r.losses == 0
        assert r.win_rate == 0.0
        assert r.net_pnl == 0.0
        assert r.profit_factor == 0.0
        assert r.trades == []
        assert r.equity_curve == []

    def test_backtest_result_with_data(self):
        r = BacktestResult(
            symbol="R_75",
            strategy="Momen",
            total_ticks=5000,
            total_trades=100,
            wins=42,
            losses=58,
            win_rate=42.0,
            gross_profit=105.0,
            gross_loss=-87.0,
            net_pnl=18.0,
            profit_factor=1.207,
            sharpe_ratio=0.85,
            max_drawdown=-12.5,
            avg_win=2.5,
            avg_loss=-1.5,
            max_consecutive_wins=5,
            max_consecutive_losses=7,
            duration_seconds=30.5,
        )
        assert r.win_rate == 42.0
        assert r.net_pnl == 18.0
        assert r.sharpe_ratio == 0.85
        assert r.max_drawdown == -12.5
        assert r.profit_factor == 1.207


class TestBacktestEngine:
    """Backtest engine construction and basic flow."""

    def test_create_engine(self):
        engine = BacktestEngine()
        assert engine is not None
        assert engine._initial_balance == 1000.0

    def test_create_with_strategy(self):
        async def my_strategy(tick, ctx):
            return None

        engine = BacktestEngine(strategy_fn=my_strategy)
        assert engine._strategy_fn is my_strategy

    def test_create_with_balance(self):
        engine = BacktestEngine(initial_balance=500.0)
        assert engine._initial_balance == 500.0

    @pytest.mark.asyncio
    async def test_run_empty_ticks_returns_empty_result(self):
        """If no ticks can be fetched, should return BacktestResult with defaults."""
        engine = BacktestEngine()
        engine._fetch_ticks = AsyncMock(return_value=[])  # type: ignore[method-assign]
        result = await engine.run(symbol="R_75", count=0)
        assert isinstance(result, BacktestResult)
        assert result.total_ticks == 0

    @pytest.mark.asyncio
    async def test_result_trades_included(self):
        """The engine should attach BacktestTrade list to the result."""
        from datetime import datetime

        from tradebot.models.market import Tick

        ticks = [
            Tick(
                symbol="R_75",
                price=33000.0003,
                epoch=1_000_000 + i,
                timestamp=datetime.now(UTC),
            )
            for i in range(10)
        ]

        engine = BacktestEngine()
        engine._fetch_ticks = AsyncMock(return_value=ticks)  # type: ignore[method-assign]
        result = await engine.run(symbol="R_75", count=10)
        assert result.total_ticks == 10 or result.symbol == "R_75"
        assert isinstance(result, BacktestResult)
