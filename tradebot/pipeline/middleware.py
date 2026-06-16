"""Pipeline middleware — composable pre/post processing hooks.

Provides an abstract base ``Middleware`` that components can subclass to
inspect, enrich, filter, or reject signals at different pipeline stages.
A ``Chain`` runs a sequence of middleware in registration order, short-
circuiting on any rejection.
"""

from __future__ import annotations

import contextlib
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from tradebot.config.settings import settings
from tradebot.exceptions import PipelineError
from tradebot.logutils import get_logger
from tradebot.models import Signal
from tradebot.utils.rate_limiter import AsyncRateLimiter

LOG = get_logger(__name__)

# ---------------------------------------------------------------------------
#  Types
# ---------------------------------------------------------------------------

NextHandler = Callable[[Signal], Awaitable[Signal | None]]
"""Signature for the ``next`` callable that a middleware may invoke to
continue the chain — or skip entirely to short-circuit."""

# ---------------------------------------------------------------------------
#  Middleware base
# ---------------------------------------------------------------------------


class Middleware(ABC):
    """Abstract middleware that wraps or filters pipeline signals.

    Subclasses may override either or both of *pre_process* / *post_process*.
    Return ``None`` from *pre_process* to **reject** the signal (short-circuit).
    """

    @abstractmethod
    def __repr__(self) -> str:
        ...

    async def pre_process(self, signal: Signal) -> Signal | None:
        """Inspect / transform the signal **before** it reaches the next
        middleware or the pipeline core.

        Returning *None* aborts the entire chain for this signal.
        """
        return signal

    async def post_process(
        self, signal: Signal, result: Signal | None
    ) -> Signal | None:
        """Inspect / transform the result **after** the core produced it.

        *result* is the return value of the next handler (may be *None*).
        Return *None* to suppress the final output.
        """
        return result


# ---------------------------------------------------------------------------
#  Concrete middleware implementations
# ---------------------------------------------------------------------------


class LoggingMiddleware(Middleware):
    """Log every signal entering and leaving the chain."""

    def __repr__(self) -> str:
        return "LoggingMiddleware"

    async def pre_process(self, signal: Signal) -> Signal | None:
        LOG.info(
            "mw:pre  signal=%s dir=%s conf=%.2f digit=%d source=%s",
            signal.symbol,
            signal.direction,
            signal.confidence,
            signal.predicted_digit,
            signal.source.value,
        )
        return signal

    async def post_process(
        self, signal: Signal, result: Signal | None
    ) -> Signal | None:
        if result is not None:
            LOG.info(
                "mw:post signal=%s dir=%s conf=%.2f -> ACCEPTED",
                result.symbol,
                result.direction,
                result.confidence,
            )
        else:
            LOG.info(
                "mw:post signal=%s -> REJECTED",
                signal.symbol,
            )
        return result


class RateLimitMiddleware(Middleware):
    """Rate-limit signal processing using a token-bucket limiter per symbol.

    When tokens are exhausted the signal is **rejected** (returned *None*)
    instead of blocking, keeping pipeline latency predictable.
    """

    def __init__(
        self,
        max_signals: int = 5,
        refill_rate: int = 1,
        refill_interval: float = 1.0,
    ) -> None:
        self._limiter = AsyncRateLimiter(
            max_tokens=max_signals,
            refill_rate=refill_rate,
            refill_interval=refill_interval,
        )

    def __repr__(self) -> str:
        return (
            f"RateLimitMiddleware(max={self._limiter.max_tokens}, "
            f"refill={self._limiter.refill_rate}/{self._limiter.refill_interval}s)"
        )

    async def pre_process(self, signal: Signal) -> Signal | None:
        wait = await self._limiter.acquire(signal.symbol, tokens=1)
        if wait > 0:
            LOG.warning(
                "mw:ratelimit signal=%s rejected (wait=%.2fs)", signal.symbol, wait
            )
            return None
        return signal


class ValidationMiddleware(Middleware):
    """Validate signal fields against configured thresholds.

    Rejects signals whose confidence is below ``SIGNAL_MIN_CONFIDENCE``
    or when strict mode is enabled and critical fields are missing.
    """

    def __init__(
        self,
        min_confidence: float | None = None,
        strict: bool | None = None,
    ) -> None:
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else settings.SIGNAL_MIN_CONFIDENCE
        )
        self.strict = strict if strict is not None else settings.SIGNAL_VALIDATION_STRICT

    def __repr__(self) -> str:
        return f"ValidationMiddleware(min_conf={self.min_confidence}, strict={self.strict})"

    async def pre_process(self, signal: Signal) -> Signal | None:
        if signal.confidence < self.min_confidence:
            LOG.info(
                "mw:validate signal=%s conf=%.2f < min=%.2f -> REJECTED",
                signal.symbol,
                signal.confidence,
                self.min_confidence,
            )
            return None

        if self.strict:
            if not signal.symbol:
                LOG.warning("mw:validate signal missing symbol -> REJECTED")
                return None
            if not signal.direction:
                LOG.warning("mw:validate signal missing direction -> REJECTED")
                return None
            if signal.predicted_digit < 0:
                LOG.warning("mw:validate signal invalid digit -> REJECTED")
                return None

        return signal


class DedupMiddleware(Middleware):
    """Prevent duplicate signals for the same symbol+direction+digit within
    a configurable deduplication window (seconds).

    The first unique signal passes through; subsequent duplicates within
    the window are silently dropped.
    """

    def __init__(self, window: int | None = None) -> None:
        self.window = window if window is not None else settings.SIGNAL_DEDUP_WINDOW
        self._seen: dict[str, float] = {}

    def __repr__(self) -> str:
        return f"DedupMiddleware(window={self.window}s, tracked={len(self._seen)})"

    async def pre_process(self, signal: Signal) -> Signal | None:
        key = f"{signal.symbol}:{signal.direction}:{signal.predicted_digit}"
        now = time.monotonic()

        last = self._seen.get(key)
        if last is not None and (now - last) < self.window:
            LOG.debug(
                "mw:dedup signal=%s key=%s dropped (%.1fs < window=%ds)",
                signal.symbol,
                key,
                now - last,
                self.window,
            )
            return None

        self._seen[key] = now
        return signal

    def cleanup(self) -> None:
        """Evict stale entries to prevent unbounded memory growth."""
        now = time.monotonic()
        stale = [k for k, t in self._seen.items() if (now - t) >= self.window]
        for k in stale:
            del self._seen[k]


class RiskCheckMiddleware(Middleware):
    """Reject signals when risk limits are breached.

    Checks (via injected callbacks):
    - Daily P&L limit (max allowed loss)
    - Maximum consecutive losses
    - Maximum drawdown

    Supply callables that return current risk-state values.  If a callback
    is *None* the corresponding check is skipped.
    """

    def __init__(
        self,
        max_daily_loss: float | None = None,
        max_consecutive_losses: int | None = None,
        max_drawdown: float | None = None,
        get_daily_pnl: Callable[[], float] | None = None,
        get_consecutive_losses: Callable[[], int] | None = None,
        get_drawdown: Callable[[], float] | None = None,
    ) -> None:
        self.max_daily_loss = (
            max_daily_loss
            if max_daily_loss is not None
            else settings.DAILY_STOP_LOSS
        )
        self.max_consecutive_losses = (
            max_consecutive_losses
            if max_consecutive_losses is not None
            else settings.PAT_MAX_CONSECUTIVE_LOSSES
        )
        self.max_drawdown = max_drawdown  # optional — no default in settings

        self._get_daily_pnl = get_daily_pnl
        self._get_consecutive_losses = get_consecutive_losses
        self._get_drawdown = get_drawdown

    def __repr__(self) -> str:
        return (
            f"RiskCheckMiddleware(daily_loss={self.max_daily_loss}, "
            f"consec_losses={self.max_consecutive_losses}, "
            f"drawdown={self.max_drawdown})"
        )

    async def pre_process(self, signal: Signal) -> Signal | None:
        # Daily P&L guard
        if self._get_daily_pnl is not None:
            try:
                pnl = self._get_daily_pnl()
                if pnl <= self.max_daily_loss:
                    LOG.warning(
                        "mw:risk signal=%s rejected — daily P&L %.2f <= %.2f",
                        signal.symbol,
                        pnl,
                        self.max_daily_loss,
                    )
                    return None
            except Exception:
                LOG.exception("mw:risk daily P&L callback failed")

        # Consecutive losses guard
        if self._get_consecutive_losses is not None:
            try:
                losses = self._get_consecutive_losses()
                if losses >= self.max_consecutive_losses:
                    LOG.warning(
                        "mw:risk signal=%s rejected — %d consecutive losses >= %d",
                        signal.symbol,
                        losses,
                        self.max_consecutive_losses,
                    )
                    return None
            except Exception:
                LOG.exception("mw:risk consecutive-losses callback failed")

        # Drawdown guard
        if self._get_drawdown is not None and self.max_drawdown is not None:
            try:
                dd = self._get_drawdown()
                if abs(dd) >= abs(self.max_drawdown):
                    LOG.warning(
                        "mw:risk signal=%s rejected — drawdown %.2f >= %.2f",
                        signal.symbol,
                        dd,
                        self.max_drawdown,
                    )
                    return None
            except Exception:
                LOG.exception("mw:risk drawdown callback failed")

        return signal


# ---------------------------------------------------------------------------
#  Chain — runs middleware in order
# ---------------------------------------------------------------------------


class MiddlewareChain:
    """Composable chain of ``Middleware`` instances.

    Usage::

        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        chain.add(ValidationMiddleware())
        chain.add(RateLimitMiddleware())

        result = await chain.run(signal)
    """

    def __init__(self) -> None:
        self._middleware: list[Middleware] = []

    def add(self, mw: Middleware) -> MiddlewareChain:
        """Register a middleware (appended at end).  Returns self for chaining."""
        self._middleware.append(mw)
        LOG.debug("middleware added: %s", mw)
        return self

    def remove(self, mw: Middleware) -> None:
        """Unregister a middleware instance."""
        with contextlib.suppress(ValueError):
            self._middleware.remove(mw)

    @property
    def items(self) -> list[Middleware]:
        """Read-only view of registered middleware."""
        return list(self._middleware)

    async def run(self, signal: Signal, core: NextHandler) -> Signal | None:
        """Run the middleware chain plus the core handler.

        Each middleware's *pre_process* is called in order.  If any returns
        *None* the chain short-circuits.  Then *core* is called.  Finally
        each middleware's *post_process* is called in reverse order.
        """
        # ── pre-process phase ──
        current: Signal | None = signal
        for mw in self._middleware:
            try:
                current = await mw.pre_process(current)
            except Exception as exc:
                LOG.exception("Middleware %s.pre_process raised", mw)
                raise PipelineError(
                    f"Middleware {mw}.pre_process failed",
                    details={"middleware": repr(mw), "error": str(exc)},
                ) from exc
            if current is None:
                LOG.debug("Chain short-circuited by %s", mw)
                return None

        # ── core ──
        try:
            result = await core(current)
        except Exception as exc:
            LOG.exception("Core handler raised")
            raise PipelineError(
                "Core handler in middleware chain failed",
                details={"error": str(exc)},
            ) from exc

        # ── post-process phase (reverse order) ──
        for mw in reversed(self._middleware):
            try:
                result = await mw.post_process(signal, result)
            except Exception as exc:
                LOG.exception("Middleware %s.post_process raised", mw)
                raise PipelineError(
                    f"Middleware {mw}.post_process failed",
                    details={"middleware": repr(mw), "error": str(exc)},
                ) from exc
            if result is None:
                LOG.debug("Chain suppressed by %s.post_process", mw)
                return None

        return result

    def __repr__(self) -> str:
        items = ", ".join(repr(m) for m in self._middleware)
        return f"MiddlewareChain[{items}]"
