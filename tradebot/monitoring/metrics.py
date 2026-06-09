"""
MetricsCollector — in-memory metrics tracking with periodic flush.

Tracks signal counts, trade counts, win/loss ratios, latency distributions.
Supports exposing metrics for Prometheus scraping.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

LOG = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """Point-in-time snapshot of all tracked metrics."""

    timestamp: float = 0.0
    signals_total: int = 0
    signals_by_source: dict[str, int] = field(default_factory=dict)
    signals_by_symbol: dict[str, int] = field(default_factory=dict)
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    trades_breakeven: int = 0
    win_rate: float = 0.0
    current_streak: str = ""  # "win" / "loss" / "none"
    current_streak_count: int = 0
    total_pnl: float = 0.0
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    latency_count: int = 0
    latency_buckets: dict[str, int] = field(default_factory=dict)
    engine_votes: dict[str, int] = field(default_factory=dict)
    errors_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "signals": {
                "total": self.signals_total,
                "by_source": dict(self.signals_by_source),
                "by_symbol": dict(self.signals_by_symbol),
            },
            "trades": {
                "total": self.trades_total,
                "won": self.trades_won,
                "lost": self.trades_lost,
                "breakeven": self.trades_breakeven,
                "win_rate": self.win_rate,
                "streak": f"{self.current_streak}_{self.current_streak_count}",
                "total_pnl": self.total_pnl,
            },
            "latency": {
                "avg_ms": self.avg_latency_ms,
                "max_ms": self.max_latency_ms,
                "count": self.latency_count,
                "buckets": dict(self.latency_buckets),
            },
            "engine_votes": dict(self.engine_votes),
            "errors": self.errors_total,
        }


class MetricsCollector:
    """Thread-safe, in-memory metrics collector.

    Usage:
        metrics = MetricsCollector()
        metrics.record_signal(source="deriv", symbol="R_75")
        metrics.record_trade(won=True, pnl=1.25)
        metrics.record_latency(ms=45.2)
        snapshot = metrics.snapshot()
    """

    def __init__(self, flush_interval: int = 60) -> None:
        self._flush_interval = flush_interval
        self._last_flush = time.monotonic()
        self._lock = Lock()

        self._signals_total = 0
        self._signals_by_source: dict[str, int] = defaultdict(int)
        self._signals_by_symbol: dict[str, int] = defaultdict(int)

        self._trades_total = 0
        self._trades_won = 0
        self._trades_lost = 0
        self._trades_breakeven = 0
        self._total_pnl = 0.0
        self._current_streak: str = "none"
        self._current_streak_count = 0

        self._latency_total = 0.0
        self._latency_max = 0.0
        self._latency_count = 0
        self._latency_buckets: dict[str, int] = defaultdict(int)
        # Buckets: <50ms, <100ms, <200ms, <500ms, <1000ms, >=1000ms
        self._latency_thresholds = [50, 100, 200, 500, 1000]

        self._engine_votes: dict[str, int] = defaultdict(int)
        self._errors_total = 0

    # ── Recording ──

    def record_signal(self, source: str = "", symbol: str = "") -> None:
        """Record a signal event."""
        with self._lock:
            self._signals_total += 1
            if source:
                self._signals_by_source[source] += 1
            if symbol:
                self._signals_by_symbol[symbol] += 1

    def record_trade(self, won: bool, pnl: float = 0.0, breakeven: bool = False) -> None:
        """Record a trade outcome."""
        with self._lock:
            self._trades_total += 1
            self._total_pnl += pnl

            if breakeven:
                self._trades_breakeven += 1
            elif won:
                self._trades_won += 1
                if self._current_streak == "win":
                    self._current_streak_count += 1
                else:
                    self._current_streak = "win"
                    self._current_streak_count = 1
            else:
                self._trades_lost += 1
                if self._current_streak == "loss":
                    self._current_streak_count += 1
                else:
                    self._current_streak = "loss"
                    self._current_streak_count = 1

            self._maybe_flush()

    def record_latency(self, ms: float) -> None:
        """Record a latency measurement (e.g. tick processing time)."""
        with self._lock:
            self._latency_total += ms
            self._latency_count += 1
            if ms > self._latency_max:
                self._latency_max = ms

            # Assign to bucket
            bucket = ">=1000ms"
            for threshold in self._latency_thresholds:
                if ms < threshold:
                    bucket = f"<{threshold}ms"
                    break
            self._latency_buckets[bucket] += 1

    def record_engine_vote(self, engine_name: str) -> None:
        """Record a vote from a signal engine."""
        with self._lock:
            self._engine_votes[engine_name] += 1

    def record_error(self) -> None:
        """Record a processing error."""
        with self._lock:
            self._errors_total += 1

    # ── Snapshot ──

    def snapshot(self) -> MetricSnapshot:
        """Get a point-in-time snapshot of all metrics."""
        with self._lock:
            avg_latency = (
                self._latency_total / self._latency_count
                if self._latency_count > 0
                else 0.0
            )
            win_rate = (
                self._trades_won / max(self._trades_won + self._trades_lost, 1)
                if self._trades_total > 0
                else 0.0
            )

            return MetricSnapshot(
                timestamp=time.time(),
                signals_total=self._signals_total,
                signals_by_source=dict(self._signals_by_source),
                signals_by_symbol=dict(self._signals_by_symbol),
                trades_total=self._trades_total,
                trades_won=self._trades_won,
                trades_lost=self._trades_lost,
                trades_breakeven=self._trades_breakeven,
                win_rate=round(win_rate, 4),
                current_streak=self._current_streak,
                current_streak_count=self._current_streak_count,
                total_pnl=round(self._total_pnl, 2),
                avg_latency_ms=round(avg_latency, 2),
                max_latency_ms=round(self._latency_max, 2),
                latency_count=self._latency_count,
                latency_buckets=dict(self._latency_buckets),
                engine_votes=dict(self._engine_votes),
                errors_total=self._errors_total,
            )

    # ── Reset ──

    def reset(self) -> None:
        """Reset all metrics to zero."""
        with self._lock:
            self._signals_total = 0
            self._signals_by_source.clear()
            self._signals_by_symbol.clear()
            self._trades_total = 0
            self._trades_won = 0
            self._trades_lost = 0
            self._trades_breakeven = 0
            self._total_pnl = 0.0
            self._current_streak = "none"
            self._current_streak_count = 0
            self._latency_total = 0.0
            self._latency_max = 0.0
            self._latency_count = 0
            self._latency_buckets.clear()
            self._engine_votes.clear()
            self._errors_total = 0

    # ── Prometheus exposition ──

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format.

        Only yields non-zero metrics to minimize payload size.
        """
        snap = self.snapshot()
        lines: list[str] = [
            "# HELP tradebot_signals_total Total signals generated",
            "# TYPE tradebot_signals_total counter",
            f"tradebot_signals_total {snap.signals_total}",
            "",
            "# HELP tradebot_trades_total Total trades executed",
            "# TYPE tradebot_trades_total counter",
            f"tradebot_trades_total {snap.trades_total}",
            f"tradebot_trades_won_total {snap.trades_won}",
            f"tradebot_trades_lost_total {snap.trades_lost}",
            f"tradebot_trades_breakeven_total {snap.trades_breakeven}",
            "",
            "# HELP tradebot_win_rate Current win rate",
            "# TYPE tradebot_win_rate gauge",
            f"tradebot_win_rate {snap.win_rate}",
            "",
            "# HELP tradebot_total_pnl Total P&L",
            "# TYPE tradebot_total_pnl gauge",
            f"tradebot_total_pnl {snap.total_pnl}",
            "",
            "# HELP tradebot_latency_ms Latency metrics",
            "# TYPE tradebot_latency_ms gauge",
            f"tradebot_latency_avg_ms {snap.avg_latency_ms}",
            f"tradebot_latency_max_ms {snap.max_latency_ms}",
            f"tradebot_latency_count {snap.latency_count}",
        ]

        for bucket, count in snap.latency_buckets.items():
            label = bucket.replace("<", "").replace(">=", "ge_").replace("ms", "")
            lines.append(f'tradebot_latency_bucket{{le="{label}"}} {count}')

        lines.append("")
        for engine, count in snap.engine_votes.items():
            lines.append(f'tradebot_engine_votes{{engine="{engine}"}} {count}')

        lines.append("")
        lines.append(f"tradebot_errors_total {snap.errors_total}")

        return "\n".join(lines)

    # ── Internal ──

    def _maybe_flush(self) -> None:
        """Periodically reset if configured to do so."""
        if self._flush_interval <= 0:
            return
        now = time.monotonic()
        if now - self._last_flush >= self._flush_interval:
            LOG.debug("Metrics collector periodic flush")
            self._last_flush = now


__all__ = [
    "MetricsCollector",
    "MetricSnapshot",
]
