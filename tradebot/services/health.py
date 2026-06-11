"""
HealthService — connectivity and component health checks.

Provides structured health reports (ok / degraded / down) covering:
- Broker connections (WebSocket alive)
- Bot responsiveness (can signal be generated?)
- Market data freshness (recent ticks?)
- Storage / disk health
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from tradebot.config import settings

LOG = logging.getLogger(__name__)


class HealthStatus(str, Enum):  # noqa: UP042
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class HealthCheckResult:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    detail: str = ""
    latency_ms: float = 0.0


@dataclass
class HealthReport:
    """Aggregated health report from all checks."""

    status: HealthStatus = HealthStatus.OK
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    checks: list[HealthCheckResult] = field(default_factory=list)
    summary: str = ""

    def add(self, result: HealthCheckResult) -> None:
        self.checks.append(result)
        # Downgrade overall status when any check is worse
        rank = {HealthStatus.OK: 0, HealthStatus.DEGRADED: 1, HealthStatus.DOWN: 2}
        current = rank[self.status]
        candidate = rank[result.status]
        if candidate > current:
            self.status = result.status

    @property
    def ok(self) -> bool:
        return self.status == HealthStatus.OK

    @property
    def degraded(self) -> bool:
        return self.status == HealthStatus.DEGRADED

    @property
    def down(self) -> bool:
        return self.status == HealthStatus.DOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "detail": c.detail,
                    "latency_ms": c.latency_ms,
                }
                for c in self.checks
            ],
        }


class HealthService:
    """Runs on-demand or periodic health checks across all bot subsystems.

    Usage:
        health = HealthService(broker=my_broker, signal_pipeline=my_pipeline)
        report = await health.run_all()
        print(report.to_dict())
    """

    def __init__(
        self,
        broker: Any = None,
        signal_pipeline: Any = None,
        market_data_provider: Any = None,
        storage: Any = None,
    ) -> None:
        self._broker = broker
        self._signal_pipeline = signal_pipeline
        self._market_data = market_data_provider
        self._storage = storage

    # ── Public API ──

    async def run_all(self) -> HealthReport:
        """Run every health check and return an aggregated report."""
        report = HealthReport()
        results = await self._run_checks()
        for r in results:
            report.add(r)
        report.summary = self._build_summary(report)
        if settings.MONITORING_HEALTH_LOG:
            LOG.info("HealthReport: %s (%d checks)", report.status.value, len(report.checks))
        return report

    async def check_connectivity(self) -> HealthCheckResult:
        """Check broker connections and WebSocket liveness."""
        return await self._check_broker()

    async def check_bot_health(self) -> HealthCheckResult:
        """Check whether the bot is responsive (can generate a signal)."""
        return await self._check_bot()

    async def check_market_data_freshness(self) -> HealthCheckResult:
        """Verify market data is recent (last tick within acceptable window)."""
        return await self._check_market_data()

    async def check_storage_health(self) -> HealthCheckResult:
        """Check disk space and database accessibility."""
        return self._check_storage()

    # ── Internal ──

    async def _run_checks(self) -> list[HealthCheckResult]:
        results: list[HealthCheckResult] = []

        results.append(await self.check_connectivity())
        results.append(await self.check_bot_health())
        results.append(await self.check_market_data_freshness())
        results.append(await self.check_storage_health())

        return results

    async def _check_broker(self) -> HealthCheckResult:
        name = "broker_connectivity"
        if self._broker is None:
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DEGRADED,
                detail="No broker configured — skipped",
            )

        import time

        start = time.monotonic()
        try:
            if hasattr(self._broker, "is_connected"):
                connected = (
                    await self._broker.is_connected()
                    if hasattr(self._broker.is_connected, "__await__")
                    else self._broker.is_connected
                )
            else:
                connected = getattr(self._broker, "connected", False)

            latency = (time.monotonic() - start) * 1000

            if connected:
                return HealthCheckResult(
                    name=name,
                    status=HealthStatus.OK,
                    detail="Broker connected",
                    latency_ms=round(latency, 1),
                )
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DOWN,
                detail="Broker reports disconnected",
                latency_ms=round(latency, 1),
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DOWN,
                detail=f"Broker check failed: {exc}",
                latency_ms=round(latency, 1),
            )

    async def _check_bot(self) -> HealthCheckResult:
        name = "bot_health"
        if self._signal_pipeline is None:
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DEGRADED,
                detail="No signal pipeline configured — skipped",
            )

        import time

        start = time.monotonic()
        try:
            # Probe the pipeline by checking if it can produce a status
            if hasattr(self._signal_pipeline, "status"):
                status = (
                    await self._signal_pipeline.status()
                    if hasattr(self._signal_pipeline.status, "__await__")
                    else self._signal_pipeline.status()
                )
            else:
                status = "unknown"

            latency = (time.monotonic() - start) * 1000
            return HealthCheckResult(
                name=name,
                status=HealthStatus.OK,
                detail=f"Pipeline status: {status}",
                latency_ms=round(latency, 1),
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DOWN,
                detail=f"Bot health check failed: {exc}",
                latency_ms=round(latency, 1),
            )

    async def _check_market_data(self) -> HealthCheckResult:
        name = "market_data_freshness"
        if self._market_data is None:
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DEGRADED,
                detail="No market data provider configured — skipped",
            )

        import time

        start = time.monotonic()
        try:
            last_tick_time: datetime | None = None
            if hasattr(self._market_data, "last_tick_time"):
                last_tick_time = self._market_data.last_tick_time
            elif hasattr(self._market_data, "last_update"):
                last_tick_time = self._market_data.last_update

            latency = (time.monotonic() - start) * 1000

            if last_tick_time is None:
                return HealthCheckResult(
                    name=name,
                    status=HealthStatus.DEGRADED,
                    detail="No tick timestamp available",
                    latency_ms=round(latency, 1),
                )

            now = datetime.now(UTC)
            age_seconds = (now - last_tick_time).total_seconds() if last_tick_time.tzinfo else -1

            if age_seconds < 0:
                return HealthCheckResult(
                    name=name,
                    status=HealthStatus.DEGRADED,
                    detail="Tick time is naive datetime — cannot compute age",
                    latency_ms=round(latency, 1),
                )

            stale_threshold = settings.WS_TIMEOUT * 3  # 3x WS timeout as freshness cutoff
            if age_seconds <= stale_threshold:
                return HealthCheckResult(
                    name=name,
                    status=HealthStatus.OK,
                    detail=f"Last tick {age_seconds:.0f}s ago (threshold: {stale_threshold}s)",
                    latency_ms=round(latency, 1),
                )
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DEGRADED,
                detail=f"Market data stale — last tick {age_seconds:.0f}s ago",
                latency_ms=round(latency, 1),
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DOWN,
                detail=f"Market data check failed: {exc}",
                latency_ms=round(latency, 1),
            )

    def _check_storage(self) -> HealthCheckResult:
        name = "storage_health"
        try:
            data_dir = Path(settings.DATA_DIR)
            data_dir.mkdir(parents=True, exist_ok=True)

            usage = shutil.disk_usage(data_dir)
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            free_pct = (usage.free / usage.total) * 100

            if free_gb < 0.5:
                return HealthCheckResult(
                    name=name,
                    status=HealthStatus.DOWN,
                    detail=f"Critically low disk space: {free_gb:.1f}GB free ({free_pct:.1f}%)",
                )

            if free_gb < 2.0:
                return HealthCheckResult(
                    name=name,
                    status=HealthStatus.DEGRADED,
                    detail=f"Low disk space: {free_gb:.1f}GB free ({free_pct:.1f}%) of {total_gb:.1f}GB",  # noqa: E501
                )

            # Quick DB accessibility check if storage provided
            db_ok = True
            if self._storage is not None:
                try:
                    if hasattr(self._storage, "conn"):
                        with self._storage.conn() as c:
                            c.execute("SELECT 1")
                except Exception as exc:
                    db_ok = False
                    return HealthCheckResult(
                        name=name,
                        status=HealthStatus.DEGRADED,
                        detail=f"Database check failed: {exc}",
                    )

            return HealthCheckResult(
                name=name,
                status=HealthStatus.OK,
                detail=f"Disk: {free_gb:.1f}GB free ({free_pct:.1f}%) — DB: {'ok' if db_ok else 'not checked'}",  # noqa: E501
            )
        except Exception as exc:
            return HealthCheckResult(
                name=name,
                status=HealthStatus.DOWN,
                detail=f"Storage check error: {exc}",
            )

    @staticmethod
    def _build_summary(report: HealthReport) -> str:
        total = len(report.checks)
        ok_count = sum(1 for c in report.checks if c.status == HealthStatus.OK)
        degraded_count = sum(1 for c in report.checks if c.status == HealthStatus.DEGRADED)
        down_count = sum(1 for c in report.checks if c.status == HealthStatus.DOWN)

        parts = [f"{total} checks"]
        if ok_count:
            parts.append(f"{ok_count} ok")
        if degraded_count:
            parts.append(f"{degraded_count} degraded")
        if down_count:
            parts.append(f"{down_count} down")

        return f"Health: {'/'.join(parts)}. Overall: {report.status.value}"


# ═══════════════════════════════════════════════════════════════════
#  Convenience — standalone health check (FastAPI /health endpoint)
# ═══════════════════════════════════════════════════════════════════


async def check_all() -> dict:
    """Run every health check and return the report as a dict.

    Creates a ``HealthService`` with no pre-configured dependencies;
    checks that require a broker or pipeline will report *degraded*
    with a descriptive message.

    Returns:
        ``HealthReport.to_dict()`` — always a dict, never raises.
    """
    service = HealthService()
    report = await service.run_all()
    return report.to_dict()


__all__ = [
    "HealthService",
    "HealthReport",
    "HealthCheckResult",
    "HealthStatus",
    "check_all",
]
