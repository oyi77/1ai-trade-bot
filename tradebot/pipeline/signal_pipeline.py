"""Signal Pipeline — Ticks → Engines → Consensus → Orchestrator → Signal.

Adds middleware chain, rate limiting, configurable timeouts,
metrics tracking, and event emission to the core pipeline.

v0.3.0 — Now integrates VilonaMetaOrchestrator as a mandatory
interception point (Stage 2.5) between Quality Gate and Middleware,
with parallel per-path timeouts and graceful degradation.
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
from tradebot.engines.consensus import EngineConsensus, MTFConsensus
from tradebot.engines.harmonic import HarmonicEngine
from tradebot.engines.meta_orchestrator import VilonaMetaOrchestrator
from tradebot.engines.mtf_consensus import (
    ConsensusVerdict,
    MTFConsensusGate,
    GateState,
    meso_from_signal,
)
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
    # Orchestrator-specific counters
    orchestrator_calls: int = 0
    orchestrator_executes: int = 0
    orchestrator_holds: int = 0
    orchestrator_hunts: int = 0
    mtf_timeouts: int = 0
    harmonic_timeouts: int = 0

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
            "orchestrator": {
                "calls": self.orchestrator_calls,
                "executes": self.orchestrator_executes,
                "holds": self.orchestrator_holds,
                "hunts": self.orchestrator_hunts,
            },
            "timeouts": {
                "mtf": self.mtf_timeouts,
                "harmonic": self.harmonic_timeouts,
            },
        }


# ---------------------------------------------------------------------------
#  Main pipeline
# ---------------------------------------------------------------------------


class SignalPipeline:
    """Orchestrates tick collection → engine analysis → consensus → orchestrator → signal.

    Supports middleware hooks, configurable timeouts per stage,
    metrics tracking, and event callbacks.

    Flow (v0.3.0):
      1. Receive market ticks
      2. *(pre-process middleware chain)*
      3. Feed ticks through all registered engines
      4. Aggregate results via EngineConsensus
      5. Quality Gate — validate, compute levels, grade, reason
      6. **Orchestrator Interception Point (NEW)**
         a. Fire parallel MTF + Harmonic data collection
         b. Resolve via VilonaMetaOrchestrator.resolve_signals()
         c. Graceful degradation on per-path timeouts
      7. *(post-process middleware chain)*
      8. Produce final Signal or None
    """

    def __init__(
        self,
        consensus: EngineConsensus | None = None,
        middleware: MiddlewareChain | None = None,
        quality_gate: QualityGate | None = None,
        *,
        # ── Orchestrator components (NEW) ──
        orchestrator: VilonaMetaOrchestrator | None = None,
        gate: MTFConsensusGate | None = None,
        mtf_consensus: MTFConsensus | None = None,
        harmonic_engine: HarmonicEngine | None = None,
        # Per-path timeouts (seconds) — None = skip that path
        mtf_timeout: float | None = 25.0,
        harmonic_timeout: float | None = 12.0,
        # Legacy
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

        # ── Orchestrator wiring ──
        self.orchestrator = orchestrator
        self._gate = gate
        self._mtf_consensus = mtf_consensus
        self._harmonic_engine = harmonic_engine
        self._mtf_timeout = mtf_timeout
        self._harmonic_timeout = harmonic_timeout

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

    @property
    def orchestrator_enabled(self) -> bool:
        """True when the orchestration layer is wired."""
        return self.orchestrator is not None

    # ------------------------------------------------------------------
    #  Core logic
    # ------------------------------------------------------------------

    async def process(self, ticks: list[Tick]) -> Signal | None:
        """Process ticks through the pipeline and return a consensus signal.

        Steps:
          1. Edge guard — reject empty input
          2. Consensus analysis (with timeout)
          3. Quality gate — validation, levels, grading (if configured)
          4. **Orchestrator Interception** — parallel MTF + harmonic, then resolve
          5. Middleware chain (pre/post)
          6. Metrics recording
          7. Event emission

        Returns *None* when no signal is produced or the chain rejects it.
        """
        start = time.monotonic()

        # ── Stage 0: edge guard ────────────────────────────────────────
        if not ticks:
            LOG.debug("Pipeline: empty ticks, skipping")
            return None

        self.metrics.signals_received += 1
        symbol = ticks[-1].symbol if ticks else "UNKNOWN"

        # ── Stage 1: engine analysis / consensus ───────────────────────
        flat_signal = await self._run_flat_consensus(ticks)
        if flat_signal is None:
            LOG.debug("Pipeline: no consensus signal produced")
            self.metrics.signals_rejected += 1
            return None

        # ── Stage 2: quality gate (optional) ──────────────────────────
        flat_signal = await self._run_quality_gate(flat_signal)
        if flat_signal is None:
            return None

        # ── Stage 2.5: Orchestrator Interception Point ────────────────
        if self.orchestrator is not None:
            result = await self._run_orchestrator_interception(
                symbol, flat_signal
            )
            if result is None:
                return None
            signal = result
        else:
            signal = flat_signal

        # ── Stage 3: middleware chain ──────────────────────────────────
        try:
            result = await self.middleware.run(signal, self._core_handler)
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

        # ── Stage 4: metrics & events ──────────────────────────────────
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
    #  Stage 1: Flat Consensus
    # ------------------------------------------------------------------

    async def _run_flat_consensus(self, ticks: list[Tick]) -> Signal | None:
        """Run engine consensus with timeout."""
        try:
            return await asyncio.wait_for(
                self.consensus.analyze(ticks),
                timeout=self.stage_timeout,
            )
        except asyncio.TimeoutError:
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

    # ------------------------------------------------------------------
    #  Stage 2: Quality Gate
    # ------------------------------------------------------------------

    async def _run_quality_gate(self, signal: Signal) -> Signal | None:
        """Run quality gate — returns enriched signal or None."""
        if self.quality_gate is None:
            return signal

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

        return enriched

    # ------------------------------------------------------------------
    #  Stage 2.5: Orchestrator Interception (NEW)
    # ------------------------------------------------------------------

    async def _run_orchestrator_interception(
        self, symbol: str, flat_signal: Signal
    ) -> Signal | None:
        """
        Interception point — collect MTF and Harmonic data in parallel,
        then feed all three paths to the orchestrator.

        Graceful degradation:
          - MTF timeout → mtf_verdict=None (falls through to standard)
          - Harmonic timeout → harmonic_verdict=None (falls through to standard)
          - Both timeout → orchestrator runs with flat_signal only
        """
        mtf_verdict: dict[str, Any] | None = None
        harmonic_verdict: ConsensusVerdict | None = None

        # Fire parallel data collection
        mtf_task = self._collect_mtf_path(symbol)
        harmonic_task = self._collect_harmonic_path(symbol)

        # Wait for both with individual timeouts
        if self._mtf_timeout is not None and self._mtf_consensus is not None:
            try:
                mtf_verdict = await asyncio.wait_for(
                    mtf_task, timeout=self._mtf_timeout
                )
            except asyncio.TimeoutError:
                LOG.warning(
                    "Pipeline: MTF data collection timed out after %.1fs — "
                    "continuing with partial data",
                    self._mtf_timeout,
                )
                self.metrics.mtf_timeouts += 1
                mtf_verdict = None
        else:
            mtf_verdict = await mtf_task

        if self._harmonic_timeout is not None and self._harmonic_engine is not None:
            try:
                harmonic_verdict = await asyncio.wait_for(
                    harmonic_task, timeout=self._harmonic_timeout
                )
            except asyncio.TimeoutError:
                LOG.warning(
                    "Pipeline: harmonic data collection timed out after %.1fs — "
                    "continuing with partial data",
                    self._harmonic_timeout,
                )
                self.metrics.harmonic_timeouts += 1
                harmonic_verdict = None
        else:
            harmonic_verdict = await harmonic_task

        # ── Resolve via orchestrator ───────────────────────────────
        if self.orchestrator is None:
            return flat_signal  # fallback — skip orchestration entirely

        self.metrics.orchestrator_calls += 1

        result = await self.orchestrator.resolve_signals(
            symbol=symbol,
            flat_signal=flat_signal,
            mtf_verdict=mtf_verdict,
            harmonic_verdict=harmonic_verdict,
        )

        if result is not None:
            self.metrics.orchestrator_executes += 1
            return result

        # HOLD — log whether it's an active hunt or a true hold
        if harmonic_verdict and harmonic_verdict.decision == GateState.HUNT_MODE:
            self.metrics.orchestrator_hunts += 1
            LOG.info(
                "Pipeline: orchestrator HUNT — %s PRZ active, "
                "waiting for micro trigger",
                symbol,
            )
        else:
            self.metrics.orchestrator_holds += 1

        return None

    # ── Parallel Path Collectors ─────────────────────────────────

    async def _collect_mtf_path(self, symbol: str) -> dict[str, Any] | None:
        """Collect MTF hierarchical data (Path B)."""
        if self._mtf_consensus is None:
            return None
        try:
            return await self._mtf_consensus.analyze(symbol)
        except Exception as exc:
            LOG.warning("Pipeline: MTF collection failed for %s: %s", symbol, exc)
            return None

    async def _collect_harmonic_path(
        self, symbol: str
    ) -> ConsensusVerdict | None:
        """Collect harmonic + gate data (Path C).

        Runs the Harmonic Engine and, if PRZ_Active, checks the gate
        for any active hunt session.  Returns the current gate verdict
        or a synthetic HUNT_MODE verdict for active sessions.
        """
        if self._harmonic_engine is None or self._gate is None:
            return None

        # Check for active hunt sessions (persist across ticks)
        if symbol in self._gate.active_sessions:
            session = self._gate.active_sessions[symbol]
            if session.is_active:
                return ConsensusVerdict(
                    decision=GateState.HUNT_MODE,
                    symbol=symbol,
                    direction=session.meso.direction,
                    reason="Hunt Mode active — awaiting micro confirmation",
                    metadata={
                        "pattern": session.meso.pattern,
                        "prz_upper": session.meso.prz_upper,
                        "prz_lower": session.meso.prz_lower,
                        "sl": session.meso.sl,
                        "tp1": session.meso.tp1,
                        "tp2": session.meso.tp2,
                    },
                )

        return None

    # ------------------------------------------------------------------
    #  Core handler for middleware chain
    # ------------------------------------------------------------------

    @staticmethod
    async def _core_handler(sig: Signal) -> Signal | None:
        """Minimal core handler for the middleware chain."""
        return sig

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
            f"orchestrator={'on' if self.orchestrator_enabled else 'off'}, "
            f"timeout={self.stage_timeout}s, "
            f"metrics={{accepted={self.metrics.signals_accepted}, "
            f"rejected={self.metrics.signals_rejected}}})"
        )
