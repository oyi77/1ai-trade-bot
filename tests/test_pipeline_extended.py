"""Extended pipeline tests — middleware, SignalPipeline, TradeExecutor."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradebot.exceptions import PipelineError
from tradebot.models import (
    Order,
    Signal,
    SignalGrade,
    SignalSource,
    Tick,
    Trade,
    TradeResult,
)
from tradebot.pipeline.middleware import (
    DedupMiddleware,
    LoggingMiddleware,
    MiddlewareChain,
    RateLimitMiddleware,
    RiskCheckMiddleware,
    ValidationMiddleware,
)
from tradebot.pipeline.signal_pipeline import PipelineMetrics, SignalPipeline
from tradebot.pipeline.trade_executor import TradeExecutor, TradeLifecycle

# ═════════════════════════════════════════════════════════════════════
#  Fixtures
# ═════════════════════════════════════════════════════════════════════


@pytest.fixture
def valid_signal() -> Signal:
    return Signal(
        symbol="R_75",
        direction="CALL",
        predicted_digit=7,
        confidence=0.75,
        source=SignalSource.CONSENSUS,
        grade=SignalGrade.STRONG,
    )


@pytest.fixture
def low_confidence_signal() -> Signal:
    return Signal(
        symbol="R_75",
        direction="CALL",
        predicted_digit=3,
        confidence=0.1,
        source=SignalSource.CONSENSUS,
    )


@pytest.fixture
def mock_tick() -> Tick:
    return Tick(symbol="R_75", price=33000.0007, epoch=1_000_000)


def _make_order(**overrides) -> Order:
    defaults = dict(
        order_id="ord-001",
        symbol="R_75",
        contract_type="DIGITMATCH",
        stake=0.35,
        barrier=7,
        direction="CALL",
        status="open",
    )
    defaults.update(overrides)
    return Order(**defaults)


# ═════════════════════════════════════════════════════════════════════
#  LoggingMiddleware
# ═════════════════════════════════════════════════════════════════════


class TestLoggingMiddleware:

    @pytest.mark.asyncio
    async def test_pre_process_returns_signal_unchanged(self, valid_signal):
        mw = LoggingMiddleware()
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_post_process_returns_result_unchanged(self, valid_signal):
        mw = LoggingMiddleware()
        result = await mw.post_process(valid_signal, valid_signal)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_post_process_passes_through_none(self, valid_signal):
        mw = LoggingMiddleware()
        result = await mw.post_process(valid_signal, None)
        assert result is None

    def test_repr(self):
        assert repr(LoggingMiddleware()) == "LoggingMiddleware"


# ═════════════════════════════════════════════════════════════════════
#  RateLimitMiddleware
# ═════════════════════════════════════════════════════════════════════


class TestRateLimitMiddleware:

    @pytest.mark.asyncio
    async def test_allows_within_limit(self, valid_signal):
        mw = RateLimitMiddleware(
            max_signals=5, refill_rate=1, refill_interval=1.0,
        )
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal
    @pytest.mark.asyncio
    async def test_blocks_when_exceeded(self, valid_signal):
        """When tokens exhausted, acquire blocks (sleeps) until refill."""
        mw = RateLimitMiddleware(
            max_signals=1, refill_rate=1, refill_interval=60.0,
        )
        await mw.pre_process(valid_signal)
        # Second call should block for ~60s since bucket is empty.
        # Use a short timeout to prove it doesn't return immediately.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                mw.pre_process(valid_signal), timeout=0.5,
            )

    @pytest.mark.asyncio
    async def test_different_symbols_independent(self):
        mw = RateLimitMiddleware(
            max_signals=1, refill_rate=1, refill_interval=60.0,
        )
        sig1 = Signal(
            symbol="R_75", direction="CALL", predicted_digit=5,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        sig2 = Signal(
            symbol="R_100", direction="PUT", predicted_digit=3,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )

        result1 = await mw.pre_process(sig1)
        assert result1 is sig1

        result2 = await mw.pre_process(sig2)
        assert result2 is sig2


# ═════════════════════════════════════════════════════════════════════
#  ValidationMiddleware
# ═════════════════════════════════════════════════════════════════════


class TestValidationMiddleware:

    @pytest.mark.asyncio
    async def test_passes_valid_signal(self, valid_signal):
        mw = ValidationMiddleware(min_confidence=0.3, strict=True)
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_rejects_low_confidence(self, low_confidence_signal):
        mw = ValidationMiddleware(min_confidence=0.5, strict=False)
        result = await mw.pre_process(low_confidence_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_missing_symbol_in_strict_mode(self):
        sig = Signal(
            symbol="", direction="CALL", predicted_digit=5,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        mw = ValidationMiddleware(min_confidence=0.0, strict=True)
        result = await mw.pre_process(sig)
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_missing_direction_in_strict_mode(self):
        sig = Signal(
            symbol="R_75", direction="", predicted_digit=5,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        mw = ValidationMiddleware(min_confidence=0.0, strict=True)
        result = await mw.pre_process(sig)
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_negative_digit_in_strict_mode(self):
        sig = Signal(
            symbol="R_75", direction="CALL", predicted_digit=-1,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        mw = ValidationMiddleware(min_confidence=0.0, strict=True)
        result = await mw.pre_process(sig)
        assert result is None

    @pytest.mark.asyncio
    async def test_allows_missing_fields_in_non_strict_mode(self):
        sig = Signal(
            symbol="", direction="", predicted_digit=-1,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        mw = ValidationMiddleware(min_confidence=0.0, strict=False)
        result = await mw.pre_process(sig)
        assert result is sig


# ═════════════════════════════════════════════════════════════════════
#  DedupMiddleware
# ═════════════════════════════════════════════════════════════════════


class TestDedupMiddleware:

    @pytest.mark.asyncio
    async def test_first_signal_passes(self, valid_signal):
        mw = DedupMiddleware(window=60)
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_duplicate_within_window_is_blocked(self, valid_signal):
        mw = DedupMiddleware(window=60)
        await mw.pre_process(valid_signal)
        result = await mw.pre_process(valid_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_different_digit_passes(self):
        mw = DedupMiddleware(window=60)
        sig1 = Signal(
            symbol="R_75", direction="CALL", predicted_digit=7,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        sig2 = Signal(
            symbol="R_75", direction="CALL", predicted_digit=3,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )

        result1 = await mw.pre_process(sig1)
        result2 = await mw.pre_process(sig2)
        assert result1 is sig1
        assert result2 is sig2

    @pytest.mark.asyncio
    async def test_different_direction_passes(self):
        mw = DedupMiddleware(window=60)
        sig1 = Signal(
            symbol="R_75", direction="CALL", predicted_digit=7,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )
        sig2 = Signal(
            symbol="R_75", direction="PUT", predicted_digit=7,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )

        result1 = await mw.pre_process(sig1)
        result2 = await mw.pre_process(sig2)
        assert result1 is sig1
        assert result2 is sig2

    @pytest.mark.asyncio
    async def test_duplicate_after_window_expires_passes(self, valid_signal):
        mw = DedupMiddleware(window=0)
        await mw.pre_process(valid_signal)
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal

    def test_cleanup_evicts_stale(self):
        mw = DedupMiddleware(window=0)
        mw._seen["test:key:0"] = time.monotonic() - 1.0
        mw.cleanup()
        assert len(mw._seen) == 0


# ═════════════════════════════════════════════════════════════════════
#  RiskCheckMiddleware
# ═════════════════════════════════════════════════════════════════════


class TestRiskCheckMiddleware:

    @pytest.mark.asyncio
    async def test_passes_when_within_risk_limits(self, valid_signal):
        mw = RiskCheckMiddleware(
            max_daily_loss=-100.0,
            max_consecutive_losses=5,
            get_daily_pnl=lambda: -10.0,
            get_consecutive_losses=lambda: 1,
        )
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_blocks_when_daily_loss_exceeded(self, valid_signal):
        mw = RiskCheckMiddleware(
            max_daily_loss=-50.0,
            get_daily_pnl=lambda: -60.0,
        )
        result = await mw.pre_process(valid_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_blocks_when_consecutive_losses_exceeded(self, valid_signal):
        mw = RiskCheckMiddleware(
            max_consecutive_losses=3,
            get_consecutive_losses=lambda: 4,
        )
        result = await mw.pre_process(valid_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_blocks_when_drawdown_exceeded(self, valid_signal):
        mw = RiskCheckMiddleware(
            max_drawdown=-20.0,
            get_drawdown=lambda: -25.0,
        )
        result = await mw.pre_process(valid_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_when_no_callbacks_set(self, valid_signal):
        mw = RiskCheckMiddleware()
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_block(self, valid_signal):
        """If a callback raises, the check is skipped (not blocking)."""
        def bad_callback():
            raise RuntimeError("oops")

        mw = RiskCheckMiddleware(
            max_daily_loss=-50.0,
            get_daily_pnl=bad_callback,
        )
        result = await mw.pre_process(valid_signal)
        assert result is valid_signal


# ═════════════════════════════════════════════════════════════════════
#  MiddlewareChain
# ═════════════════════════════════════════════════════════════════════


class TestMiddlewareChain:

    @pytest.mark.asyncio
    async def test_runs_all_middlewares_in_order(self, valid_signal):
        """Middleware are called in registration order during pre_process."""
        mw1 = LoggingMiddleware()
        mw2 = ValidationMiddleware(min_confidence=0.0, strict=False)

        chain = MiddlewareChain()
        chain.add(mw1)
        chain.add(mw2)

        async def core(sig):
            return sig

        result = await chain.run(valid_signal, core)
        assert result is valid_signal

    @pytest.mark.asyncio
    async def test_short_circuits_on_none_from_pre_process(self, valid_signal):
        """Chain returns None when a middleware rejects the signal."""
        mw_reject = ValidationMiddleware(min_confidence=0.99, strict=False)
        mw_pass = LoggingMiddleware()

        chain = MiddlewareChain()
        chain.add(mw_reject)
        chain.add(mw_pass)

        core_called = False

        async def core(sig):
            nonlocal core_called
            core_called = True
            return sig

        result = await chain.run(valid_signal, core)
        assert result is None
        assert core_called is False

    @pytest.mark.asyncio
    async def test_core_receives_processed_signal(self, valid_signal):
        received_signal = None

        async def core(sig):
            nonlocal received_signal
            received_signal = sig
            return sig

        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        await chain.run(valid_signal, core)
        assert received_signal is valid_signal

    @pytest.mark.asyncio
    async def test_post_process_called_in_reverse_order(self):
        """post_process runs in reverse registration order."""
        call_order: list[str] = []

        class OrderTracker(LoggingMiddleware):
            def __init__(self, name: str):
                self._name = name

            def __repr__(self) -> str:
                return self._name

            async def post_process(self, signal, result):
                call_order.append(self._name)
                return result

        chain = MiddlewareChain()
        chain.add(OrderTracker("first"))
        chain.add(OrderTracker("second"))

        sig = Signal(
            symbol="R_75", direction="CALL", predicted_digit=5,
            confidence=0.8, source=SignalSource.CONSENSUS,
        )

        async def core(s):
            return s

        await chain.run(sig, core)
        assert call_order == ["second", "first"]

    @pytest.mark.asyncio
    async def test_raises_pipeline_error_on_middleware_exception(
        self, valid_signal
    ):
        class BrokenMiddleware(LoggingMiddleware):
            async def pre_process(self, signal):
                raise RuntimeError("broken")

        chain = MiddlewareChain()
        chain.add(BrokenMiddleware())

        async def core(s):
            return s

        with pytest.raises(PipelineError):
            await chain.run(valid_signal, core)

    def test_add_returns_self_for_chaining(self):
        chain = MiddlewareChain()
        result = chain.add(LoggingMiddleware())
        assert result is chain

    def test_remove_middleware(self):
        chain = MiddlewareChain()
        mw = LoggingMiddleware()
        chain.add(mw)
        assert len(chain.items) == 1
        chain.remove(mw)
        assert len(chain.items) == 0

    def test_remove_nonexistent_is_noop(self):
        chain = MiddlewareChain()
        chain.remove(LoggingMiddleware())

    def test_items_returns_copy(self):
        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        items = chain.items
        items.clear()
        assert len(chain.items) == 1

    def test_repr(self):
        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        r = repr(chain)
        assert "MiddlewareChain" in r
        assert "LoggingMiddleware" in r


# ═════════════════════════════════════════════════════════════════════
#  PipelineMetrics
# ═════════════════════════════════════════════════════════════════════


class TestPipelineMetrics:

    def test_snapshot_returns_expected_keys(self):
        m = PipelineMetrics()
        snap = m.snapshot()
        expected_keys = {
            "signals_received",
            "signals_accepted",
            "signals_rejected",
            "errors",
            "acceptance_rate",
            "avg_latency_ms",
            "max_latency_ms",
            "min_latency_ms",
            "last_latency_ms",
            "orchestrator_calls",
            "orchestrator_executes",
            "orchestrator_holds",
            "orchestrator_hunts",
            "mtf_timeouts",
            "harmonic_timeouts",
        }
        assert expected_keys == set(snap.keys())

    def test_snapshot_defaults(self):
        m = PipelineMetrics()
        snap = m.snapshot()
        assert snap["signals_received"] == 0
        assert snap["signals_accepted"] == 0
        assert snap["signals_rejected"] == 0
        assert snap["errors"] == 0
        assert snap["acceptance_rate"] == 0.0
        assert snap["avg_latency_ms"] == 0.0
        assert snap["min_latency_ms"] == 0.0

    def test_acceptance_rate(self):
        m = PipelineMetrics(signals_received=10, signals_accepted=7)
        assert m.acceptance_rate == pytest.approx(0.7)

    def test_acceptance_rate_zero_received(self):
        m = PipelineMetrics()
        assert m.acceptance_rate == 0.0

    def test_record_latency(self):
        m = PipelineMetrics()
        m.record_latency(10.0)
        m.record_latency(20.0)
        assert m.max_latency_ms == 20.0
        assert m.min_latency_ms == 10.0
        assert m.last_latency_ms == 20.0
        assert m.total_latency_ms == 30.0

    def test_avg_latency(self):
        m = PipelineMetrics(signals_accepted=2, total_latency_ms=30.0)
        assert m.avg_latency_ms == pytest.approx(15.0)


# ═════════════════════════════════════════════════════════════════════
#  SignalPipeline
# ═════════════════════════════════════════════════════════════════════


class TestSignalPipeline:

    @pytest.mark.asyncio
    async def test_process_with_valid_signal_through_middleware(
        self, mock_tick, valid_signal
    ):
        """Signal produced by consensus passes through middleware."""
        pipeline = SignalPipeline()
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        result = await pipeline.process([mock_tick])
        assert result is not None
        assert result.symbol == "R_75"
        assert result.direction == "CALL"

    @pytest.mark.asyncio
    async def test_process_rejected_by_middleware(self, mock_tick, valid_signal):
        """Signal rejected by validation middleware -> returns None."""
        chain = MiddlewareChain()
        chain.add(ValidationMiddleware(min_confidence=0.99, strict=False))
        pipeline = SignalPipeline(middleware=chain)
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        result = await pipeline.process([mock_tick])
        assert result is None

    @pytest.mark.asyncio
    async def test_process_updates_metrics_on_accepted(
        self, mock_tick, valid_signal
    ):
        pipeline = SignalPipeline()
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        await pipeline.process([mock_tick])
        assert pipeline.metrics.signals_received == 1
        assert pipeline.metrics.signals_accepted == 1
        assert pipeline.metrics.signals_rejected == 0

    @pytest.mark.asyncio
    async def test_process_updates_metrics_on_rejected(
        self, mock_tick, valid_signal
    ):
        chain = MiddlewareChain()
        chain.add(ValidationMiddleware(min_confidence=0.99, strict=False))
        pipeline = SignalPipeline(middleware=chain)
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        await pipeline.process([mock_tick])
        assert pipeline.metrics.signals_received == 1
        assert pipeline.metrics.signals_rejected == 1
        assert pipeline.metrics.signals_accepted == 0

    @pytest.mark.asyncio
    async def test_process_empty_ticks_returns_none(self):
        pipeline = SignalPipeline()
        result = await pipeline.process([])
        assert result is None

    @pytest.mark.asyncio
    async def test_process_no_consensus_signal(self, mock_tick):
        """Consensus returns None -> pipeline returns None."""
        pipeline = SignalPipeline()
        pipeline.consensus.analyze = AsyncMock(return_value=None)

        result = await pipeline.process([mock_tick])
        assert result is None
        assert pipeline.metrics.signals_received == 1
        assert pipeline.metrics.signals_rejected == 1

    @pytest.mark.asyncio
    async def test_process_records_latency(self, mock_tick, valid_signal):
        pipeline = SignalPipeline()
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        await pipeline.process([mock_tick])
        assert pipeline.metrics.last_latency_ms > 0
        assert pipeline.metrics.max_latency_ms > 0

    def test_metrics_snapshot_after_processing(self):
        """snapshot() returns expected keys after metrics are populated."""
        pipeline = PipelineMetrics(
            signals_received=5, signals_accepted=3,
            signals_rejected=2, errors=1,
        )
        snap = pipeline.snapshot()
        assert snap["signals_received"] == 5
        assert snap["signals_accepted"] == 3
        assert snap["signals_rejected"] == 2
        assert snap["errors"] == 1
        assert snap["acceptance_rate"] == pytest.approx(0.6)

    def test_reset_metrics(self):
        pipeline = SignalPipeline()
        pipeline.metrics.signals_received = 10
        pipeline.reset_metrics()
        assert pipeline.metrics.signals_received == 0

    @pytest.mark.asyncio
    async def test_on_signal_callback_fired(self, mock_tick, valid_signal):
        callback = AsyncMock()
        pipeline = SignalPipeline(on_signal=callback)
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        await pipeline.process([mock_tick])
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_complete_callback_fired(self, mock_tick, valid_signal):
        callback = AsyncMock()
        pipeline = SignalPipeline(on_complete=callback)
        pipeline.consensus.analyze = AsyncMock(return_value=valid_signal)

        await pipeline.process([mock_tick])
        callback.assert_awaited_once()

    def test_pipeline_repr(self):
        pipeline = SignalPipeline()
        r = repr(pipeline)
        assert "SignalPipeline" in r
        assert "engines=" in r
        assert "middleware=" in r


# ═════════════════════════════════════════════════════════════════════
#  TradeLifecycle
# ═════════════════════════════════════════════════════════════════════


class TestTradeLifecycle:

    def test_record_winning_trade(self):
        lc = TradeLifecycle(symbol="R_75")
        trade = Trade(
            trade_id="t1", symbol="R_75", contract_type="DIGITMATCH",
            direction="CALL", stake=1.0, predicted_digit=7,
            entry_price=100.0, profit=0.9, is_win=True, is_completed=True,
        )
        lc.record_trade(trade)
        assert lc.wins == 1
        assert lc.losses == 0
        assert lc.consecutive_losses == 0
        assert lc.total_profit == pytest.approx(0.9)

    def test_record_losing_trade(self):
        lc = TradeLifecycle(symbol="R_75")
        trade = Trade(
            trade_id="t1", symbol="R_75", contract_type="DIGITMATCH",
            direction="CALL", stake=1.0, predicted_digit=7,
            entry_price=100.0, profit=-1.0, is_win=False, is_completed=True,
        )
        lc.record_trade(trade)
        assert lc.wins == 0
        assert lc.losses == 1
        assert lc.consecutive_losses == 1

    def test_consecutive_losses_reset_on_win(self):
        lc = TradeLifecycle()
        lc.record_trade(Trade(
            trade_id="t1", symbol="R_75", contract_type="DIGITMATCH",
            direction="CALL", stake=1.0, predicted_digit=7,
            entry_price=100.0, profit=-1.0, is_win=False, is_completed=True,
        ))
        lc.record_trade(Trade(
            trade_id="t2", symbol="R_75", contract_type="DIGITMATCH",
            direction="CALL", stake=1.0, predicted_digit=7,
            entry_price=100.0, profit=-1.0, is_win=False, is_completed=True,
        ))
        assert lc.consecutive_losses == 2

        lc.record_trade(Trade(
            trade_id="t3", symbol="R_75", contract_type="DIGITMATCH",
            direction="CALL", stake=1.0, predicted_digit=7,
            entry_price=100.0, profit=0.9, is_win=True, is_completed=True,
        ))
        assert lc.consecutive_losses == 0

    def test_win_rate(self):
        lc = TradeLifecycle()
        for i in range(7):
            lc.record_trade(Trade(
                trade_id=f"t{i}", symbol="R_75",
                contract_type="DIGITMATCH", direction="CALL",
                stake=1.0, predicted_digit=7, entry_price=100.0,
                profit=0.9, is_win=True, is_completed=True,
            ))
        for i in range(3):
            lc.record_trade(Trade(
                trade_id=f"t{7 + i}", symbol="R_75",
                contract_type="DIGITMATCH", direction="CALL",
                stake=1.0, predicted_digit=7, entry_price=100.0,
                profit=-1.0, is_win=False, is_completed=True,
            ))
        assert lc.win_rate == pytest.approx(0.7)

    def test_to_trade_result(self):
        lc = TradeLifecycle(
            symbol="R_75", contract_type="DIGITMATCH"
        )
        lc.record_trade(Trade(
            trade_id="t1", symbol="R_75", contract_type="DIGITMATCH",
            direction="CALL", stake=1.0, predicted_digit=7,
            entry_price=100.0, profit=0.9, is_win=True, is_completed=True,
        ))
        result = lc.to_trade_result()
        assert isinstance(result, TradeResult)
        assert result.profit == pytest.approx(0.9)
        assert result.wins == 1
        assert result.trades == 1
        assert result.symbol == "R_75"


# ═════════════════════════════════════════════════════════════════════
#  TradeExecutor
# ═════════════════════════════════════════════════════════════════════


class TestTradeExecutor:

    def test_initialization(self):
        broker = MagicMock()
        broker.place_order = AsyncMock()
        executor = TradeExecutor(
            broker=broker,
            default_stake=1.0,
            contract_type="DIGITMATCH",
        )
        assert executor.default_stake == 1.0
        assert executor.contract_type == "DIGITMATCH"
        assert executor.lifecycle.wins == 0
        assert executor.lifecycle.losses == 0
        assert executor._daily_pnl == 0.0

    def test_initialization_defaults_from_settings(self):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker)
        assert executor.default_stake > 0
        assert executor.contract_type != ""

    @pytest.mark.asyncio
    async def test_execute_valid_signal(self, valid_signal):
        """Execute a valid signal: places order, creates trade."""
        broker = MagicMock()
        broker.place_order = AsyncMock(return_value=_make_order())

        executor = TradeExecutor(
            broker=broker,
            default_stake=0.35,
            contract_type="DIGITMATCH",
        )

        result = await executor.execute(valid_signal)
        assert result is not None
        assert isinstance(result, TradeResult)
        broker.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_invalid_signal_returns_none(self):
        """Signal with confidence <= 0 is rejected."""
        broker = MagicMock()
        broker.place_order = AsyncMock()
        executor = TradeExecutor(broker=broker)

        invalid = Signal(
            symbol="R_75", direction="CALL", predicted_digit=5,
            confidence=0.0, source=SignalSource.CONSENSUS,
        )
        result = await executor.execute(invalid)
        assert result is None
        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_rejected_by_middleware(self, valid_signal):
        """Middleware rejects -> no order placed."""
        broker = MagicMock()
        broker.place_order = AsyncMock()

        chain = MiddlewareChain()
        chain.add(ValidationMiddleware(min_confidence=0.99, strict=False))
        executor = TradeExecutor(broker=broker, middleware=chain)

        result = await executor.execute(valid_signal)
        assert result is None
        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_updates_lifecycle(self, valid_signal):
        broker = MagicMock()
        broker.place_order = AsyncMock(return_value=_make_order())
        executor = TradeExecutor(broker=broker, default_stake=1.0)

        await executor.execute(valid_signal)
        total = executor.lifecycle.wins + executor.lifecycle.losses
        assert total == 1

    @pytest.mark.asyncio
    async def test_execute_updates_daily_pnl(self, valid_signal):
        broker = MagicMock()
        broker.place_order = AsyncMock(return_value=_make_order())
        executor = TradeExecutor(broker=broker, default_stake=1.0)

        await executor.execute(valid_signal)
        assert executor._daily_pnl == 0.0

    @pytest.mark.asyncio
    async def test_execute_broker_failure_returns_none(self, valid_signal):
        broker = MagicMock()
        broker.place_order = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        executor = TradeExecutor(broker=broker)

        result = await executor.execute(valid_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_broker_returns_none(self, valid_signal):
        broker = MagicMock()
        broker.place_order = AsyncMock(return_value=None)
        executor = TradeExecutor(broker=broker)

        result = await executor.execute(valid_signal)
        assert result is None

    def test_resolve_stake_from_signal_metadata(self, valid_signal):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker, default_stake=1.0)
        valid_signal.metadata["stake"] = 5.0
        stake = executor._resolve_stake(valid_signal)
        assert stake == 5.0

    def test_resolve_stake_default(self, valid_signal):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker, default_stake=2.0)
        stake = executor._resolve_stake(valid_signal)
        assert stake == 2.0

    def test_get_risk_state_accessors(self):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker)
        assert executor.get_daily_pnl() == 0.0
        assert executor.get_consecutive_losses() == 0
        assert executor.get_drawdown() == 0.0

    def test_reset_daily_risk(self):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker)
        executor._daily_pnl = -50.0
        executor._daily_trades = 10
        executor.reset_daily_risk()
        assert executor._daily_pnl == 0.0
        assert executor._daily_trades == 0

    def test_reset_lifecycle(self):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker)
        executor.lifecycle.wins = 10
        executor.reset_lifecycle()
        assert executor.lifecycle.wins == 0

    def test_repr(self):
        broker = MagicMock()
        executor = TradeExecutor(broker=broker, default_stake=1.0)
        r = repr(executor)
        assert "TradeExecutor" in r
        assert "stake=" in r
