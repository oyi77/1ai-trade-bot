"""
WatchdogService — periodic health checks with auto-restart and alerting.

Runs health checks on a configurable interval (N seconds). When a
component is down, it can attempt to restart it and sends rate-limited
telegram alerts so the operator isn't spammed.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from tradebot.config import settings
from tradebot.services.health import HealthService, HealthStatus

LOG = logging.getLogger(__name__)


class RateLimiter:
    """Simple per-key rate limiter to prevent alert spam."""

    def __init__(self, default_cooldown: float = 300.0) -> None:
        self._cooldown = default_cooldown
        self._last_sent: dict[str, float] = {}

    def can_send(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        return (now - last) >= self._cooldown

    def mark_sent(self, key: str) -> None:
        self._last_sent[key] = time.monotonic()

    def reset(self, key: str) -> None:
        self._last_sent.pop(key, None)


class WatchdogService:
    """Periodically checks bot health and reacts to failures.

    Usage:
        watchdog = WatchdogService(
            health_service=health_svc,
            on_restart=my_restart_callback,
            send_alert=my_alert_function,
        )
        await watchdog.start()
        # ... later ...
        await watchdog.stop()
    """

    def __init__(
        self,
        health_service: HealthService | None = None,
        on_restart: Callable[[str], Any] | None = None,
        send_alert: Callable[[str], Any] | None = None,
        interval: float = 0.0,
    ) -> None:
        self._health = health_service or HealthService()
        self._on_restart = on_restart
        self._send_alert = send_alert
        self._interval = interval or float(settings.MONITORING_HEARTBEAT_INTERVAL)
        self._rate_limiter = RateLimiter(default_cooldown=300.0)
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._restart_attempts: dict[str, int] = {}
        self._max_restarts_per_hour = 5

    # ── Lifecycle ──

    async def start(self) -> None:
        """Start the periodic watchdog loop in a background task."""
        if self._running:
            LOG.warning("Watchdog already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        LOG.info("🐕 Watchdog started (interval=%ds)", self._interval)

    async def stop(self) -> None:
        """Stop the watchdog loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        LOG.info("🐕 Watchdog stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── One-shot check (can be called independently) ──

    async def check_once(self) -> dict:
        """Run a single health check cycle and return the report dict."""
        report = await self._health.run_all()
        await self._handle_results(report)
        return report.to_dict()

    # ── Internal loop ──

    async def _loop(self) -> None:
        while self._running:
            try:
                report = await self._health.run_all()
                await self._handle_results(report)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                LOG.error("Watchdog cycle error: %s", exc, exc_info=True)

            await asyncio.sleep(self._interval)

    async def _handle_results(self, report) -> None:
        """Evaluate health results and take action on failures."""
        for check in report.checks:
            if check.status == HealthStatus.OK:
                # Reset restart counter on recovery
                self._rate_limiter.reset(check.name)
                self._restart_attempts.pop(check.name, None)
                continue

            if check.status == HealthStatus.DOWN:
                await self._handle_down(check)

            elif check.status == HealthStatus.DEGRADED:
                await self._handle_degraded(check)

    async def _handle_down(self, check) -> None:
        name = check.name
        LOG.warning("⚠️  Watchdog: %s is DOWN — %s", name, check.detail)

        # Rate-limited alert
        alert_key = f"down:{name}"
        if self._rate_limiter.can_send(alert_key) and self._send_alert:
            try:
                msg = (
                    f"⚠️ <b>Watchdog Alert</b>\n"
                    f"Component: {name}\n"
                    f"Status: DOWN\n"
                    f"Detail: {check.detail}\n"
                    f"Latency: {check.latency_ms}ms"
                )
                if hasattr(self._send_alert, "__await__"):
                    await self._send_alert(msg)
                else:
                    self._send_alert(msg)
                self._rate_limiter.mark_sent(alert_key)
            except Exception as exc:
                LOG.error("Watchdog alert send failed: %s", exc)

        # Auto-restart
        attempts = self._restart_attempts.get(name, 0)
        if attempts < self._max_restarts_per_hour and self._on_restart:
            self._restart_attempts[name] = attempts + 1
            try:
                LOG.info("🔄 Watchdog restarting %s (attempt %d/%d)", name, attempts + 1, self._max_restarts_per_hour)  # noqa: E501
                result = self._on_restart(name)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                LOG.error("Watchdog restart failed for %s: %s", name, exc)

    async def _handle_degraded(self, check) -> None:
        name = check.name
        LOG.info("⚠️  Watchdog: %s is DEGRADED — %s", name, check.detail)

        # Only alert for degraded if it persists (rate-limited separately)
        alert_key = f"degraded:{name}"
        if self._rate_limiter.can_send(alert_key) and self._send_alert:
            try:
                msg = (
                    f"⚠️ <b>Watchdog Notice</b>\n"
                    f"Component: {name}\n"
                    f"Status: DEGRADED\n"
                    f"Detail: {check.detail}"
                )
                if hasattr(self._send_alert, "__await__"):
                    await self._send_alert(msg)
                else:
                    self._send_alert(msg)
                self._rate_limiter.mark_sent(alert_key)
            except Exception as exc:
                LOG.error("Watchdog degraded alert failed: %s", exc)


__all__ = [
    "WatchdogService",
    "RateLimiter",
]
