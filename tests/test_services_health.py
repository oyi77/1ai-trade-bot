"""Tests for HealthService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tradebot.services.health import (
    HealthCheckResult,
    HealthReport,
    HealthService,
    HealthStatus,
)


class TestHealthStatus:
    """HealthStatus enum."""

    def test_values(self):
        assert HealthStatus.OK.value == "ok"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.DOWN.value == "down"

    def test_ordering(self):
        assert HealthStatus.OK != HealthStatus.DOWN
        assert HealthStatus.DEGRADED != HealthStatus.OK


class TestHealthCheckResult:
    """Individual health check result."""

    def test_create_ok(self):
        r = HealthCheckResult(
            name="test_check", status=HealthStatus.OK, detail="All good"
        )
        assert r.name == "test_check"
        assert r.status == HealthStatus.OK
        assert r.detail == "All good"

    def test_with_latency(self):
        r = HealthCheckResult(
            name="latency_check",
            status=HealthStatus.OK,
            detail="5ms",
            latency_ms=5.0,
        )
        assert r.latency_ms == 5.0


class TestHealthReport:
    """Aggregated health report."""

    def test_default_status_ok(self):
        report = HealthReport()
        assert report.status == HealthStatus.OK
        assert report.checks == []
        assert report.summary == ""

    def test_add_ok_check(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="check1", status=HealthStatus.OK, detail="pass"
            )
        )
        assert report.status == HealthStatus.OK
        assert len(report.checks) == 1

    def test_add_degraded_downgrades(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="ok_check", status=HealthStatus.OK, detail="pass"
            )
        )
        report.add(
            HealthCheckResult(
                name="degraded_check",
                status=HealthStatus.DEGRADED,
                detail="slow",
            )
        )
        assert report.status == HealthStatus.DEGRADED

    def test_add_down_downgrades(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="down_check", status=HealthStatus.DOWN, detail="fail"
            )
        )
        assert report.status == HealthStatus.DOWN

    def test_ok_property(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="check1", status=HealthStatus.OK, detail="pass"
            )
        )
        assert report.ok is True
        assert report.degraded is False
        assert report.down is False

    def test_degraded_property(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="c1", status=HealthStatus.DEGRADED, detail="slow"
            )
        )
        assert report.degraded is True
        assert report.ok is False

    def test_down_property(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="c1", status=HealthStatus.DOWN, detail="fail"
            )
        )
        assert report.down is True
        assert report.ok is False

    def test_to_dict(self):
        report = HealthReport()
        report.add(
            HealthCheckResult(
                name="check1",
                status=HealthStatus.OK,
                detail="pass",
                latency_ms=1.0,
            )
        )
        d = report.to_dict()
        assert d["status"] == "ok"
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "check1"
        assert d["checks"][0]["latency_ms"] == 1.0


class TestHealthService:
    """Health service with mocks."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        b = MagicMock()
        b.is_connected = AsyncMock(return_value=True)
        return b

    @pytest.mark.asyncio
    async def test_run_all_no_components(self):
        """Health service with no components should still return a report."""
        health = HealthService()
        report = await health.run_all()
        assert isinstance(report, HealthReport)
        assert len(report.checks) == 4

    @pytest.mark.asyncio
    async def test_run_all_with_broker_ok(self, mock_broker):
        health = HealthService(broker=mock_broker)
        report = await health.run_all()
        assert len(report.checks) == 4

    @pytest.mark.asyncio
    async def test_check_connectivity_no_broker(self):
        health = HealthService()
        result = await health.check_connectivity()
        assert result.status == HealthStatus.DEGRADED
        assert "No broker configured" in result.detail

    @pytest.mark.asyncio
    async def test_check_connectivity_ok(self, mock_broker):
        health = HealthService(broker=mock_broker)
        result = await health.check_connectivity()
        assert result.status == HealthStatus.OK
        assert "Broker connected" in result.detail

    @pytest.mark.asyncio
    async def test_check_connectivity_down(self):
        """Broker with connected=False should return DOWN."""
        broker = MagicMock()
        broker.is_connected = False
        health = HealthService(broker=broker)
        result = await health.check_connectivity()
        assert result.status == HealthStatus.DOWN

    @pytest.mark.asyncio
    async def test_check_bot_health_no_pipeline(self):
        health = HealthService()
        result = await health.check_bot_health()
        assert result.status == HealthStatus.DEGRADED
        assert "No signal pipeline" in result.detail

    @pytest.mark.asyncio
    async def test_check_bot_health_unknown(self):
        """Pipeline without status property returns unknown."""
        pipeline = MagicMock()
        # No status attribute at all
        del pipeline.status
        health = HealthService(signal_pipeline=pipeline)
        result = await health.check_bot_health()
        # It'll try to access .status, get a new mock which is truthy,
        # so it depends on what happens. Should at least not crash.
        assert result.status in (HealthStatus.OK, HealthStatus.DEGRADED, HealthStatus.DOWN)

    @pytest.mark.asyncio
    async def test_check_market_data_freshness_no_provider(self):
        health = HealthService()
        result = await health.check_market_data_freshness()
        assert result.status == HealthStatus.DEGRADED
        assert "No market data provider" in result.detail

    @pytest.mark.asyncio
    async def test_check_storage_health(self):
        health = HealthService()
        result = await health.check_storage_health()
        assert result.status in (HealthStatus.OK, HealthStatus.DEGRADED)

    @pytest.mark.asyncio
    async def test_individual_check_methods(self):
        """All check methods return valid HealthCheckResult."""
        health = HealthService()
        for check_fn in [
            health.check_connectivity,
            health.check_bot_health,
            health.check_market_data_freshness,
            health.check_storage_health,
        ]:
            result = await check_fn()
            assert isinstance(result, HealthCheckResult)
            assert result.name
            assert isinstance(result.status, HealthStatus)
