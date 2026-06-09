"""
HealthProbe — simple HTTP health endpoint server.

Provides liveness, readiness, and startup probe endpoints
for orchestration systems (Kubernetes, Docker healthchecks, etc.).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from tradebot.config import settings

LOG = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler serving liveness/readiness/startup probes."""

    server_state: dict = {
        "liveness": True,
        "readiness": False,
        "startup": False,
    }
    extra_checks: Callable[[], dict[str, Any]] | None = None

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug("HealthProbe HTTP %s", fmt % args)

    def _handle_request(self) -> None:
        path = self.path.rstrip("/")

        if path == "/healthz" or path == "/livez":
            alive = self.server_state.get("liveness", True)
            self._json({"status": "ok" if alive else "down"}, 200 if alive else 503)

        elif path == "/readyz":
            ready = self.server_state.get("readiness", False)
            details: dict = {}
            if ready and self.extra_checks:
                try:
                    details = self.extra_checks()
                    ready = all(v.get("status") == "ok" for v in details.values())
                except Exception:
                    ready = False
                    details = {"error": "extra_checks_failed"}

            self._json(
                {"status": "ok" if ready else "not_ready", "checks": details},
                200 if ready else 503,
            )

        elif path == "/startupz":
            started = self.server_state.get("startup", False)
            self._json(
                {"status": "ok" if started else "starting_up"},
                200 if started else 503,
            )

        elif path == "/":
            self._json({
                "service": "tradebot",
                "endpoints": {
                    "/healthz": "Liveness probe",
                    "/livez": "Liveness probe (alias)",
                    "/readyz": "Readiness probe",
                    "/startupz": "Startup probe",
                },
            })

        else:
            self._json({"error": "not_found"}, 404)

    def do_GET(self) -> None:
        self._handle_request()

    def do_HEAD(self) -> None:
        self._handle_request()


class HealthProbe:
    """Simple HTTP server exposing health probe endpoints.

    Usage:
        probe = HealthProbe()
        probe.start_background()  # non-blocking start
        # ... or probe.start() for blocking (Ctrl+C to stop)

    Probe endpoints:
      GET /healthz  — Liveness (200=alive, 503=dead)
      GET /livez    — Alias for /healthz
      GET /readyz   — Readiness (200=ready, 503=not ready)
      GET /startupz — Startup (200=started, 503=starting)
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        extra_checks: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.host = host or "127.0.0.1"
        self.port = port or settings.MONITORING_PROMETHEUS_PORT
        self._httpd: HTTPServer | None = None
        self._extra_checks = extra_checks

        HealthHandler.extra_checks = extra_checks

    # ── State setters ──

    @staticmethod
    def set_liveness(alive: bool) -> None:
        HealthHandler.server_state["liveness"] = alive

    @staticmethod
    def set_readiness(ready: bool) -> None:
        HealthHandler.server_state["readiness"] = ready

    @staticmethod
    def set_startup(started: bool) -> None:
        HealthHandler.server_state["startup"] = started

    # ── Lifecycle ──

    def start(self) -> None:
        """Start the HTTP server (blocks until KeyboardInterrupt)."""
        self._httpd = HTTPServer((self.host, self.port), HealthHandler)
        LOG.info("🏥 Health probe listening on %s:%d", self.host, self.port)
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            self.stop()

    def start_background(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        import threading

        self._httpd = HTTPServer((self.host, self.port), HealthHandler)
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()
        LOG.info("🏥 Health probe background on %s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            LOG.info("Health probe stopped")

    @property
    def running(self) -> bool:
        return self._httpd is not None


__all__ = [
    "HealthProbe",
]
