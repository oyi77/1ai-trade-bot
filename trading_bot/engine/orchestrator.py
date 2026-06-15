"""Trading orchestrator — state machine, strategy lifecycle, and cycle dispatch."""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any

from trading_bot.engine.events import ERROR, SIGNAL, EventBus, signal_event
from trading_bot.engine.portfolio import PortfolioTracker
from trading_bot.engine.risk import RiskManager
from trading_bot.strategies.base import BaseStrategy, StrategySignal

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Engine state
# ---------------------------------------------------------------------------


class EngineState(Enum):
    """Lifecycle states for the trading engine."""

    IDLE = auto()
    INITIALIZING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    ERROR = auto()


# ---------------------------------------------------------------------------
#  Trading orchestrator
# ---------------------------------------------------------------------------


class TradingOrchestrator:
    """Central coordinator that manages strategy lifecycle and trade execution.

    The orchestrator:
    - Maintains an explicit state machine (IDLE → INITIALIZING → RUNNING → …).
    - Runs analysis cycles, dispatching signals through the event bus.
    - Delegates order validation to ``RiskManager`` and position tracking
      to ``PortfolioTracker``.
    """

    def __init__(
        self,
        event_bus: EventBus,
        risk_manager: RiskManager,
        portfolio: PortfolioTracker,
    ) -> None:
        self._event_bus = event_bus
        self._risk = risk_manager
        self._portfolio = portfolio
        self._state = EngineState.IDLE
        self._strategies: dict[str, BaseStrategy] = {}
        self._cycle_count: int = 0
        self._last_error: str | None = None

    # ── state machine ─────────────────────────────────────────────────

    @property
    def state(self) -> EngineState:
        return self._state

    def _transition(self, new_state: EngineState) -> None:
        old = self._state
        self._state = new_state
        LOG.info("Engine state: %s -> %s", old.name, new_state.name)

    # ── lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Transition IDLE → INITIALIZING → RUNNING."""
        if self._state not in (EngineState.IDLE, EngineState.STOPPED):
            LOG.warning("Cannot start from state %s", self._state.name)
            return

        self._transition(EngineState.INITIALIZING)
        try:
            for name, strategy in self._strategies.items():
                LOG.info("Starting strategy: %s", name)
                await strategy.on_start()
            self._transition(EngineState.RUNNING)
        except Exception as exc:
            LOG.exception("Initialisation failed")
            self._last_error = str(exc)
            self._transition(EngineState.ERROR)
            await self._emit_error(str(exc))

    async def stop(self) -> None:
        """Stop all strategies and transition to STOPPED."""
        if self._state not in (EngineState.RUNNING, EngineState.PAUSED, EngineState.ERROR):
            return
        for name, strategy in self._strategies.items():
            try:
                await strategy.on_stop()
                LOG.info("Strategy stopped: %s", name)
            except Exception as exc:
                LOG.warning("Error stopping strategy %s: %s", name, exc)
        self._transition(EngineState.STOPPED)

    async def pause(self) -> None:
        """Pause the engine (RUNNING → PAUSED)."""
        if self._state != EngineState.RUNNING:
            return
        self._transition(EngineState.PAUSED)

    async def resume(self) -> None:
        """Resume the engine (PAUSED → RUNNING)."""
        if self._state != EngineState.PAUSED:
            return
        self._transition(EngineState.RUNNING)

    # ── strategy management ───────────────────────────────────────────

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Register a strategy for execution."""
        self._strategies[strategy.name] = strategy
        LOG.info("Strategy registered: %s", strategy.name)

    def unregister_strategy(self, name: str) -> None:
        """Remove a registered strategy."""
        self._strategies.pop(name, None)

    def get_strategy(self, name: str) -> BaseStrategy | None:
        return self._strategies.get(name)

    @property
    def strategies(self) -> dict[str, BaseStrategy]:
        return dict(self._strategies)

    # ── cycle ─────────────────────────────────────────────────────────

    async def run_cycle(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> list[StrategySignal]:
        """Execute one analysis cycle across all registered strategies.

        Each strategy is analysed in sequence.  Signals are emitted
        through the event bus.  The orchestrator does basic risk checks
        (drawdown, max positions) before accepting signals; detailed
        per-order validation is done by the execution layer.

        Returns:
            The list of signals produced during this cycle.
        """
        if self._state != EngineState.RUNNING:
            LOG.warning("run_cycle called while %s", self._state.name)
            return []

        self._cycle_count += 1
        signals: list[StrategySignal] = []

        for name, strategy in self._strategies.items():
            try:
                signal = await strategy.analyze(symbol, timeframe)
                if signal is None:
                    continue

                # Basic risk checks — drawdown and position count.
                equity = self._portfolio.total_equity()
                peak = getattr(self._portfolio, "_equity_peak", equity)
                dd_ok, dd_reason = self._risk.check_drawdown(equity, peak)
                if not dd_ok:
                    LOG.debug("Signal %s rejected: %s", name, dd_reason)
                    continue

                positions = self._portfolio.get_positions(symbol)
                pos_ok, pos_reason = self._risk.check_position_limits(
                    positions, self._portfolio.balance,
                )
                if not pos_ok:
                    LOG.debug("Signal %s rejected: %s", name, pos_reason)
                    continue

                # Emit signal event on the bus so the executor can act.
                await self._event_bus.publish(
                    SIGNAL,
                    **signal_event(signal).data,
                )
                signals.append(signal)

            except Exception as exc:
                LOG.exception("Strategy %s failed: %s", name, exc)
                self._last_error = str(exc)
                await self._emit_error(f"{name}: {exc}")

        return signals

    # ── error handling ────────────────────────────────────────────────

    async def _emit_error(self, message: str) -> None:
        self._last_error = message
        await self._event_bus.publish(ERROR, message=message)

    # ── status ────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of the engine state."""
        return {
            "state": self._state.name,
            "cycle_count": self._cycle_count,
            "strategies": list(self._strategies.keys()),
            "last_error": self._last_error,
        }
