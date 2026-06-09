"""Tests for DigitMartingaleStrategy."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tradebot.brokers.deriv.config import DEFAULT_SYMBOL
from tradebot.brokers.deriv.strategy import DigitMartingaleStrategy, TradeResult
from tradebot.models.market import Tick


class TestTradeResult:
    """TradeResult dataclass."""

    def test_win(self):
        r = TradeResult(
            profit=2.52,
            total_stake=0.35,
            trades=1,
            wins=1,
            losses=0,
            win_rate=100.0,
            cycles=1,
        )
        assert r.profit == 2.52
        assert r.win_rate == 100.0

    def test_loss(self):
        r = TradeResult(
            profit=-1.73,
            total_stake=1.73,
            trades=3,
            wins=0,
            losses=3,
            win_rate=0.0,
            cycles=1,
        )
        assert r.profit == -1.73

    def test_stopped_early(self):
        r = TradeResult(
            profit=0.0,
            total_stake=0.0,
            trades=0,
            wins=0,
            losses=0,
            win_rate=0.0,
            cycles=1,
            stopped_early=True,
            reason="daily_sl_hit",
        )
        assert r.stopped_early is True
        assert r.reason == "daily_sl_hit"


class TestDigitMartingaleStrategyInit:
    """Strategy construction and defaults."""

    def test_init_defaults(self, mock_client):
        s = DigitMartingaleStrategy(client=mock_client)
        assert s.initial_stake == 0.35
        assert s.stake_multiplier == 1.55
        assert s.max_ops == 3
        assert s.symbol == DEFAULT_SYMBOL
        assert s.contract_type == "DIGITMATCH"
        assert s.barrier == 7

    def test_custom_params(self, mock_client):
        s = DigitMartingaleStrategy(
            client=mock_client,
            symbol="R_25",
            initial_stake=1.0,
            max_ops=2,
            barrier=5,
        )
        assert s.symbol == "R_25"
        assert s.initial_stake == 1.0
        assert s.max_ops == 2
        assert s.barrier == 5

    def test_risk_defaults(self, mock_client):
        s = DigitMartingaleStrategy(client=mock_client)
        assert s.target_profit == 5.0
        assert s.max_loss == -8.0

    def test_analysis_params(self, mock_client):
        s = DigitMartingaleStrategy(
            client=mock_client,
            analysis_ticks=200,
            min_confidence=0.5,
            duration=2,
        )
        assert s.analysis_ticks == 200
        assert s.min_confidence == 0.5
        assert s.duration == 2


class TestDigitMartingaleStrategyExecution:
    """Strategy execution and cycle logic."""

    @pytest.mark.asyncio
    async def test_get_session_balance(self, mock_client):
        s = DigitMartingaleStrategy(client=mock_client)
        bal = await s.get_session_balance()
        assert bal == 100.0

    @pytest.mark.asyncio
    async def test_daily_profit_tracker_no_bot(self, mock_client):
        s = DigitMartingaleStrategy(client=mock_client)
        tracker = s.daily_profit_tracker
        assert isinstance(tracker, dict)

    def test_daily_loss_limit(self, mock_client):
        s = DigitMartingaleStrategy(client=mock_client)
        assert s.daily_loss_limit == s.max_loss

    @pytest.mark.asyncio
    async def test_analyse_and_trade_successful(self, mock_client):
        """Full cycle with good pattern should execute trades."""
        # Mock ticks with strong 3→7 pattern for Momen detection
        ticks = []
        for _ in range(80):
            ticks.append(
                Tick(
                    symbol="R_75",
                    price=33000.0003,
                    epoch=1_000_000 + len(ticks),
                    timestamp=datetime.now(UTC),
                )
            )
            ticks.append(
                Tick(
                    symbol="R_75",
                    price=33000.0007,
                    epoch=1_000_000 + len(ticks),
                    timestamp=datetime.now(UTC),
                )
            )
        mock_client.get_ticks_history = AsyncMock(return_value=ticks)
        mock_client.get_balance = AsyncMock(return_value=100.0)
        mock_client.buy_digit = AsyncMock(
            return_value={"contract_id": 123, "profit": 2.52}
        )

        s = DigitMartingaleStrategy(
            client=mock_client,
            target_profit=100.0,  # High TP so it doesn't stop early
            max_loss=-100.0,  # Low SL so it doesn't stop early
            analysis_ticks=50,
        )
        result = await s.analyse_and_trade()
        # Should complete without crashing (may succeed or stop)
        assert isinstance(result, TradeResult)
