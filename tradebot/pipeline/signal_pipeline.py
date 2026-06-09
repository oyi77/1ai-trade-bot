"""Signal Pipeline — Ticks → Engines → Consensus → Signal.

Adds middleware chain, rate limiting, configurable timeouts,
metrics tracking, and event emission to the core pipeline.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .quality_gate import QualityGate

from tradebot.config.settings import settings
from tradebot.engines.consensus import EngineConsensus
from tradebot.events import bus as event_bus
from tradebot.exceptions import PipelineError
from tradebot.logging import get_logger
from tradebot.models import Signal, Tick

from .middleware import MiddlewareChain

LOG = get_logger(__name__)

# ---------------------------------------------------------------------------
#  Event callbacks
# ---------------------------------------------------------------------------

SignalEvent = Callable[[Signal], Awaitable[None]]
"""Signature for ``on_signal`` / ``on_complete`` callbacks."""
ErrorEvent = Callable[[Signal, Exception], Awaitable[None]]
"""Signature for ``on_error`` callbacks."""


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------


@dataclass
class PipelineMetrics:
    """Runtime metrics for the pipeline."""

    signals_received: int = 0
    signals_accepted: int = 0
    signals_rejected: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    last_latency_ms: float = 0.0

    @property
    def acceptance_rate(self) -> float:
        """Fraction of received signals that made it through the chain."""
        if self.signals_received == 0:
            return 0.0
        return self.signals_accepted / self.signals_received

    @property
    def avg_latency_ms(self) -> float:
        """Average processing latency in milliseconds."""
        if self.signals_accepted == 0:
            return 0.0
        return self.total_latency_ms / self.signals_accepted

    def record_latency(self, latency_ms: float) -> None:
        self.total_latency_ms += latency_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.last_latency_ms = latency_ms

    def snapshot(self) -> dict[str, Any]:
        return {
            "signals_received": self.signals_received,
            "signals_accepted": self.signals_accepted,
            "signals_rejected": self.signals_rejected,
            "errors": self.errors,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "min_latency_ms": (
                round(self.min_latency_ms, 2)
                if self.min_latency_ms != float("inf")
                else 0.0
            ),
            "last_latency_ms": round(self.last_latency_ms, 2),
        }


# ---------------------------------------------------------------------------
#  Main pipeline
# ---------------------------------------------------------------------------


class SignalPipeline:
    """Orchestrates tick collection → engine analysis → consensus signal.

    Supports middleware hooks, configurable timeouts per stage,
    metrics tracking, and event callbacks.

    Flow:
      1. Receive market ticks
      2. *(pre-process middleware chain)*
      3. Feed ticks through all registered engines
      4. Aggregate results via EngineConsensus
      5. *(post-process middleware chain)*
      6. Produce final Signal or None
    """

    def __init__(
        self,
        consensus: EngineConsensus | None = None,
        middleware: MiddlewareChain | None = None,
        quality_gate: QualityGate | None = None,
        *,
        stage_timeout: float | None = None,
        on_signal: SignalEvent | None = None,
        on_error: ErrorEvent | None = None,
        on_complete: SignalEvent | None = None,
    ) -> None:
        self.consensus = consensus or EngineConsensus()
        self.middleware = middleware or MiddlewareChain()
        self.quality_gate = quality_gate
        self.stage_timeout = (
            stage_timeout
            if stage_timeout is not None
            else settings.SIGNAL_PIPELINE_TIMEOUT
        )
        self.metrics = PipelineMetrics()

        # Event callbacks
        self._on_signal = on_signal
        self._on_error = on_error
        self._on_complete = on_complete

    # ------------------------------------------------------------------
    #  Engine registration helpers (delegated to consensus)
    # ------------------------------------------------------------------

    def register_engine(self, engine: Any, weight: float = 1.0) -> None:
        """Register an engine with the underlying consensus."""
        self.consensus.register(engine, weight=weight)

    def unregister_engine(self, name: str) -> None:
        """Unregister an engine by name."""
        self.consensus.unregister(name)

    @property
    def registered_engines(self) -> list[str]:
        """Return list of registered engine names."""
        return list(self.consensus._engines.keys())

    # ------------------------------------------------------------------
    #  Core logic
    # ------------------------------------------------------------------
    async def process(self, ticks: list[Tick]) -> Signal | None:
        """Process ticks through the pipeline and return a consensus signal.

        Steps:
          1. Edge guard — reject empty input
          2. Consensus analysis (with timeout)
          3. Quality gate — validation, levels, grading (if configured)
          4. Middleware chain (pre/post)
          5. Metrics recording
          6. Event emission

        Returns *None* when no signal is produced or the chain rejects it.
        """
        start = time.monotonic()

        # ── Stage 0: edge guard ────────────────────────────────────────
        if not ticks:
            LOG.debug("Pipeline: empty ticks, skipping")
            return None

        self.metrics.signals_received += 1

        # ── Stage 1: engine analysis / consensus ───────────────────────
        try:
            signal: Signal | None = await asyncio.wait_for(
                self.consensus.analyze(ticks),
                timeout=self.stage_timeout,
            )
        except TimeoutError:
            LOG.warning(
                "Pipeline: consensus timed out (timeout=%.1fs)", self.stage_timeout
            )
            self.metrics.errors += 1
            return None
        except Exception as exc:
            LOG.exception("Pipeline: consensus error")
            self.metrics.errors += 1
            await self._emit_error(None, exc)
            return None

        if signal is None:
            LOG.debug("Pipeline: no consensus signal produced")
            self.metrics.signals_rejected += 1
            return None

        # ── Stage 2: quality gate (optional) ─────────────────────────
        if self.quality_gate is not None:
            try:
                enriched = await self.quality_gate.process(signal)
            except Exception as exc:
                LOG.exception("Pipeline: quality gate error")
                self.metrics.errors += 1
                await self._emit_error(signal, exc)
                return None

            if enriched is None:
                LOG.info(
                    "Pipeline: signal %s %s rejected by quality gate",
                    signal.symbol,
                    signal.direction,
                )
                self.metrics.signals_rejected += 1
                return None
            signal = enriched

        # ── Stage 3: middleware chain ──────────────────────────────────
        async def _core(sig: Signal) -> Signal | None:
            """Minimal core handler for the middleware chain.

            In a simple pipeline the consensus output *is* the result,
            but subclassing pipelines may override this step (e.g. to
            add enrichment or transformation between pre/post middleware).
            """
            return sig

        try:
            result = await self.middleware.run(signal, _core)
        except PipelineError:
            self.metrics.errors += 1
            self.metrics.signals_rejected += 1
            await self._emit_error(signal, None)
            return None
        except Exception as exc:
            self.metrics.errors += 1
            self.metrics.signals_rejected += 1
            LOG.exception("Pipeline: middleware chain error")
            await self._emit_error(signal, exc)
            return None

        # ── Stage 3: metrics & events ──────────────────────────────────
        elapsed_ms = (time.monotonic() - start) * 1000.0
        self.metrics.record_latency(elapsed_ms)

        if result is not None:
            self.metrics.signals_accepted += 1
            LOG.info(
                "📡 Pipeline: signal %s %s conf=%.0f%% digit=%d "
                "(latency=%.1fms, accepted=%d/%d)",
                result.symbol,
                result.direction,
                result.confidence * 100,
                result.predicted_digit,
                elapsed_ms,
                self.metrics.signals_accepted,
                self.metrics.signals_received,
            )
            await self._emit_signal(result)
        else:
            self.metrics.signals_rejected += 1
            LOG.info(
                "Pipeline: signal %s %s rejected by middleware (latency=%.1fms)",
                signal.symbol,
                signal.direction,
                elapsed_ms,
            )

        await self._emit_complete(result)
        return result

    # ------------------------------------------------------------------
    #  Event emitters
    # ------------------------------------------------------------------

    async def _emit_signal(self, signal: Signal) -> None:
        """Fire ``on_signal`` callback + event bus."""
        event_bus.publish("signal_generated", signal=signal)
        if self._on_signal is not None:
            try:
                await self._on_signal(signal)
            except Exception:
                LOG.exception("on_signal callback failed")

    async def _emit_error(
        self, signal: Signal | None, exc: Exception | None
    ) -> None:
        """Fire ``on_error`` callback + event bus."""
        event_bus.publish("pipeline_error", signal=signal, exception=exc)
        if self._on_error is not None and signal is not None:
            try:
                await self._on_error(signal, exc)  # type: ignore[arg-type]
            except Exception:
                LOG.exception("on_error callback failed")

    async def _emit_complete(self, result: Signal | None) -> None:
        """Fire ``on_complete`` callback + event bus."""
        event_bus.publish("pipeline_complete", result=result)
        if self._on_complete is not None:
            try:
                await self._on_complete(result)  # type: ignore[arg-type]
            except Exception:
                LOG.exception("on_complete callback failed")

    # ------------------------------------------------------------------
    #  Utilities
    # ------------------------------------------------------------------

    def reset_metrics(self) -> None:
        """Reset runtime metrics to zero."""
        self.metrics = PipelineMetrics()

    def __repr__(self) -> str:
        return (
            f"SignalPipeline(engines={len(self.consensus._engines)}, "
            f"middleware={len(self.middleware.items)}, "
            f"timeout={self.stage_timeout}s, "
            f"metrics={{accepted={self.metrics.signals_accepted}, "
            f"rejected={self.metrics.signals_rejected}}})"
        )
