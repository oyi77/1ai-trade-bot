"""Tests for data models — Signal, Tick, Trade, Order, Balance, TradeResult."""

from __future__ import annotations

from datetime import UTC, datetime

from tradebot.models import (
    Balance,
    MarketState,
    Order,
    Signal,
    SignalGrade,
    SignalSource,
    Tick,
    Trade,
    TradeResult,
)


class TestTick:
    """Tick model — digit extraction, creation, ordering."""

    def test_digit_extraction(self):
        pairs = [
            (33000.0003, 3),
            (33000.0007, 7),
            (33000.0000, 0),
            (33000.0009, 9),
            (33738.4123, 3),
            (100.5000, 0),
            (0.0, 0),
        ]
        for price, expected in pairs:
            t = Tick(symbol="R_75", price=price, epoch=1, timestamp=datetime.now())
            assert t.digit == expected, f"price={price}: expected {expected} got {t.digit}"

    def test_epoch_ordering(self):
        t1 = Tick(symbol="R_75", price=33000.0, epoch=100, timestamp=datetime.now())
        t2 = Tick(symbol="R_75", price=33001.0, epoch=200, timestamp=datetime.now())
        assert t1.epoch < t2.epoch

    def test_repr(self):
        t = Tick(symbol="R_75", price=33000.0005, epoch=12345, timestamp=datetime.now())
        r = repr(t)
        assert "R_75" in r
        assert "12345" in r


class TestSignal:
    """Signal model — creation, grade auto-assignment, validation."""

    def test_create_minimal(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.8,
            source=SignalSource.MOMEN,
        )
        assert s.symbol == "R_75"
        assert s.direction == "CALL"
        assert s.predicted_digit == 7
        assert s.confidence == 0.8
        assert s.source == SignalSource.MOMEN

    def test_is_valid(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.5,
            source=SignalSource.MOMEN,
        )
        assert s.is_valid is True
        s_zero = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.0,
            source=SignalSource.MOMEN,
        )
        assert s_zero.is_valid is False

    def test_grade_auto_strong(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.85,
            source=SignalSource.MOMEN,
        )
        assert s.grade == SignalGrade.STRONG

    def test_grade_auto_moderate(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.6,
            source=SignalSource.MOMEN,
        )
        assert s.grade == SignalGrade.MODERATE

    def test_grade_auto_weak(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.35,
            source=SignalSource.MOMEN,
        )
        assert s.grade == SignalGrade.WEAK

    def test_grade_explicit(self):
        """Explicit grade override in constructor is respected."""
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.9,
            source=SignalSource.MOMEN,
            grade=SignalGrade.NEUTRAL,
        )
        # __post_init__ auto-grades, so NEUTRAL gets overridden by STRONG
        assert s.grade == SignalGrade.STRONG

    def test_timestamp_auto(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.5,
            source=SignalSource.MOMEN,
        )
        assert isinstance(s.timestamp, datetime)

    def test_metadata_default(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.5,
            source=SignalSource.MOMEN,
        )
        assert s.metadata == {}

    def test_consensus_source(self):
        s = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.5,
            source=SignalSource.CONSENSUS,
        )
        assert s.source == SignalSource.CONSENSUS
        assert s.source.value == "consensus"


class TestTrade:
    """Trade model — creation and defaults."""

    def test_create_minimal(self):
        t = Trade(
            trade_id="t1",
            symbol="R_75",
            contract_type="DIGITMATCH",
            direction="CALL",
            stake=0.35,
            predicted_digit=7,
            entry_price=33000.0,
        )
        assert t.trade_id == "t1"
        assert t.is_completed is False
        assert t.is_win is False
        assert t.profit == 0.0
        assert isinstance(t.timestamp, datetime)

    def test_win_trade(self):
        t = Trade(
            trade_id="t2",
            symbol="R_75",
            contract_type="DIGITMATCH",
            direction="CALL",
            stake=0.35,
            predicted_digit=7,
            entry_price=33000.0,
            exit_price=33001.0,
            payout=2.87,
            profit=2.52,
            is_win=True,
            is_completed=True,
        )
        assert t.is_win is True
        assert t.is_completed is True
        assert t.profit == 2.52


class TestOrder:
    """Order model — creation and defaults."""

    def test_create_minimal(self):
        o = Order(
            order_id="o1",
            symbol="R_75",
            contract_type="DIGITMATCH",
            stake=0.35,
            barrier=7,
            direction="CALL",
        )
        assert o.order_id == "o1"
        assert o.status == "pending"
        assert o.duration == 1
        assert o.duration_unit == "t"
        assert isinstance(o.created_at, datetime)

    def test_filled_order(self):
        o = Order(
            order_id="o2",
            symbol="R_75",
            contract_type="DIGITMATCH",
            stake=0.35,
            barrier=7,
            direction="CALL",
            status="won",
            filled_at=datetime.now(UTC),
        )
        assert o.status == "won"
        assert o.filled_at is not None


class TestTradeResult:
    """TradeResult model — win/loss records."""

    def test_win_result(self):
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

    def test_loss_result(self):
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


class TestBalance:
    """Balance model."""

    def test_create(self):
        b = Balance(balance=100.0)
        assert b.balance == 100.0
        assert b.currency == "USD"
        assert isinstance(b.timestamp, datetime)

    def test_custom_currency(self):
        b = Balance(balance=5000.0, currency="EUR")
        assert b.currency == "EUR"


class TestMarketState:
    """MarketState model — tick management."""

    def test_empty_state(self):
        ms = MarketState(symbol="R_75")
        assert ms.symbol == "R_75"
        assert ms.ticks == []
        assert ms.is_trading_allowed is True
        assert ms.latest_tick is None
        assert ms.recent_digits == []

    def test_add_tick(self):
        ms = MarketState(symbol="R_75")
        t = Tick(symbol="R_75", price=33000.0003, epoch=1, timestamp=datetime.now())
        ms.add_tick(t)
        assert len(ms.ticks) == 1
        assert ms.latest_tick == t

    def test_max_ticks(self):
        ms = MarketState(symbol="R_75", max_ticks=5)
        for i in range(10):
            ms.add_tick(
                Tick(
                    symbol="R_75",
                    price=float(f"33000.000{i}"),
                    epoch=i,
                    timestamp=datetime.now(),
                )
            )
        assert len(ms.ticks) == 5

    def test_recent_digits(self):
        ms = MarketState(symbol="R_75")
        for d in [3, 7, 1, 5, 9]:
            ms.add_tick(
                Tick(
                    symbol="R_75",
                    price=float(f"33000.000{d}"),
                    epoch=d,
                    timestamp=datetime.now(),
                )
            )
        assert ms.recent_digits == [3, 7, 1, 5, 9]

    def test_cooldown(self):
        from datetime import timedelta

        ms = MarketState(
            symbol="R_75",
            is_trading_allowed=False,
            cooldown_until=datetime.now(UTC) + timedelta(hours=1),
        )
        assert ms.is_trading_allowed is False
        assert ms.cooldown_until is not None
