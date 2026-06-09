"""Tests for SignalPipeline with mocked engines."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tradebot.models import Signal, SignalGrade, SignalSource, Tick
from tradebot.pipeline.signal_pipeline import SignalPipeline


@pytest.fixture
def mock_tick() -> Tick:
    return Tick(symbol="R_75", price=33000.0003, epoch=1_000_000)


@pytest.fixture
def mock_signal() -> Signal:
    return Signal(
        symbol="R_75",
        direction="CALL",
        predicted_digit=7,
        confidence=0.8,
        source=SignalSource.MOMEN,
        grade=SignalGrade.STRONG,
    )


@pytest.fixture
def mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.name = "test_engine"
    engine.analyze = AsyncMock(
        return_value=Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.8,
            source=SignalSource.MOMEN,
        )
    )
    return engine


@pytest.fixture
def pipeline() -> SignalPipeline:
    return SignalPipeline()


class TestSignalPipeline:
    """Signal pipeline construction and basic flow."""

    def test_create_pipeline(self):
        p = SignalPipeline()
        assert p is not None
        assert p.consensus is not None
        assert p.middleware is not None
        assert p.metrics is not None

    def test_metrics_defaults(self, pipeline):
        assert pipeline.metrics.signals_received == 0
        assert pipeline.metrics.signals_accepted == 0
        assert pipeline.metrics.signals_rejected == 0
        assert pipeline.metrics.errors == 0
        assert pipeline.metrics.total_latency_ms == 0.0
        assert pipeline.metrics.acceptance_rate == 0.0
        assert pipeline.metrics.avg_latency_ms == 0.0

    @pytest.mark.asyncio
    async def test_process_rejects_low_confidence(self, pipeline):
        """A signal below min confidence should be rejected."""
        weak = Signal(
            symbol="R_75",
            direction="CALL",
            predicted_digit=7,
            confidence=0.01,
            source=SignalSource.MOMEN,
        )
        result = await pipeline.process(weak)
        assert result is None

    @pytest.mark.asyncio
    async def test_process_rejected_updates_metrics(self, pipeline, mock_signal):
        """Processing should update metrics counts."""
        await pipeline.process(mock_signal)
        assert pipeline.metrics.signals_received >= 1

    def test_metrics_snapshot(self, pipeline):
        snapshot = pipeline.metrics.snapshot()
        assert isinstance(snapshot, dict)
        assert "signals_received" in snapshot
        assert "signals_accepted" in snapshot
        assert "avg_latency_ms" in snapshot

    def test_pipeline_accepts_callbacks(self):
        """Pipeline constructor accepts on_signal, on_error, on_complete."""
        cb = AsyncMock()
        p = SignalPipeline(on_signal=cb, on_error=cb, on_complete=cb)
        assert p is not None
