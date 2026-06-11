"""Monitoring API Router — real-time system monitoring endpoints.

Endpoints:
  GET  /api/monitoring/engines    — Engine registry + active status
  GET  /api/monitoring/brokers    — Broker connection status
  GET  /api/monitoring/metrics    — MetricsCollector + PipelineMetrics snapshot
  GET  /api/monitoring/trades/live — Open positions from TradeTracker
  GET  /api/monitoring/status      — Consolidated system status
  GET  /api/monitoring/errors      — Error rate / error log summary

These endpoints are designed to work without dedicated singleton wiring.
When a component is unavailable the response includes a descriptive
``available: false`` field rather than raising an error.
"""

from __future__ import annotations

import logging
import platform
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from tradebot.engines.registry import Registry
from tradebot.monitoring import MetricsCollector, TradeTracker

LOG = logging.getLogger("tradebot.web.monitoring")

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

# ── Injectable references (set by server.py at startup) ──────────────
_metrics: MetricsCollector | None = None
_tracker: TradeTracker | None = None
_extra_health_checks: Callable[[], dict[str, Any]] | None = None
_uptime_start: float = time.monotonic()


def wire_metrics(collector: MetricsCollector) -> None:
    global _metrics
    _metrics = collector


def wire_tracker(tracker: TradeTracker) -> None:
    global _tracker
    _tracker = tracker


def wire_health_checks(fn: Callable[[], dict[str, Any]]) -> None:
    global _extra_health_checks
    _extra_health_checks = fn


# ── Helper ───────────────────────────────────────────────────────────


def _fmt_dt(ts: float | None) -> str:
    if ts is None or ts == 0:
        return ""
    return datetime.fromtimestamp(ts, tz=datetime.UTC).isoformat()


def _engine_health() -> list[dict[str, Any]]:
    """Discover registered engines and return their status list."""
    try:
        reg = Registry()
        engines = reg.discover()
        return [
            {
                "name": name,
                "available": True,
                "type": type(engine).__module__.split(".")[-1],
            }
            for name, engine in engines.items()
        ]
    except Exception as exc:
        LOG.warning("Engine discovery failed: %s", exc)
        return [{"name": "discovery_error", "available": False, "detail": str(exc)[:200]}]


def _broker_status() -> list[dict[str, Any]]:
    """Return known broker connection status.

    This endpoint reports *last-known* connection state for each broker
    platform. Real-time checks require the broker instance to be wired.
    """
    return [
        {
            "platform": "deriv",
            "available": False,
            "detail": "No DerivWSClient instance wired — status is unknown",
        },
        {
            "platform": "mt5",
            "available": False,
            "detail": "No MT5Broker instance wired — status is unknown",
        },
        {
            "platform": "stockity",
            "available": False,
            "detail": "No StockityBroker instance wired — status is unknown",
        },
        {
            "platform": "ccxt",
            "available": False,
            "detail": "No CCXTBroker instance wired — status is unknown",
        },
    ]


def _metrics_snapshot() -> dict[str, Any]:
    """Return MetricsCollector snapshot or a zero placeholder."""
    if _metrics is not None:
        snap = _metrics.snapshot()
        return snap.to_dict()
    return {
        "timestamp": 0.0,
        "signals": {"total": 0, "by_source": {}, "by_symbol": {}},
        "trades": {
            "total": 0,
            "won": 0,
            "lost": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "streak": "none_0",
            "total_pnl": 0.0,
        },
        "latency": {"avg_ms": 0.0, "max_ms": 0.0, "count": 0, "buckets": {}},
        "engine_votes": {},
        "errors": 0,
    }


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/engines")
async def api_engine_health():
    """List all registered engines and their availability."""
    return {"engines": _engine_health()}


@router.get("/brokers")
async def api_broker_status():
    """List known broker platforms and their connection status."""
    return {"brokers": _broker_status()}


@router.get("/metrics")
async def api_metrics():
    """Return MetricsCollector snapshot (signal/trade/latency counts)."""
    return _metrics_snapshot()


@router.get("/trades/live")
async def api_live_trades():
    """Return open positions from TradeTracker."""
    if _tracker is None:
        return {"trades": [], "count": 0, "available": False}

    try:
        trades = _tracker.get_open_trades()
        return {
            "trades": [t._asdict() if hasattr(t, "_asdict") else dict(t) for t in trades],
            "count": len(trades),
            "available": True,
        }
    except Exception as exc:
        LOG.warning("Failed to fetch live trades: %s", exc)
        return {"trades": [], "count": 0, "available": False, "error": str(exc)[:200]}


@router.get("/status")
async def api_system_status():
    """Consolidated system health — single-call dashboard snapshot."""
    elapsed = time.monotonic() - _uptime_start
    host_info = {
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }

    extra = {}
    if _extra_health_checks is not None:
        try:
            extra = _extra_health_checks()
        except Exception as exc:
            extra = {"error": str(exc)[:200]}

    return {
        "uptime_seconds": round(elapsed, 1),
        "uptime_human": _fmt_uptime(elapsed),
        "host": host_info,
        "engines": _engine_health(),
        "brokers": _broker_status(),
        "metrics": _metrics_snapshot(),
        "extra": extra,
        "timestamp": datetime.now(datetime.UTC).isoformat(),
    }


@router.get("/errors")
async def api_errors():
    """Return error summary from MetricsCollector."""
    snap = _metrics_snapshot()
    return {
        "total_errors": snap.get("errors", 0),
        "signals_total": snap.get("signals", {}).get("total", 0),
        "error_rate": (
            round(snap["errors"] / snap["signals"]["total"], 4)
            if snap.get("signals", {}).get("total", 0) > 0
            else 0.0
        ),
    }


def _fmt_uptime(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)
