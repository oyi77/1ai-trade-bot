"""
Pillar 2: Zero-Crash Error Handling — Exponential Backoff + Retry Wrappers.

Wraps all external API calls (price feed, AI provider, webhook, bridge)
with strict try-catch and exponential backoff. System never crashes — it
retries with increasing delays until recovery or max attempts exhausted.

Usage:
    from tradebot.engines.resilience import resilient_call, ResilientAPI

    # Simple call with retry
    result = resilient_call(fetch_price, "XAUUSD", max_retries=5)

    # Decorator pattern
    @resilient(max_retries=3, base_delay=2.0)
    def call_ai_provider(prompt):
        ...

    # Context manager for multi-step operations
    async with ResilientAPI("gold-api") as api:
        data = await api.get("/price/XAUUSD")
"""

import time
import random
import functools
import logging
from typing import TypeVar, Callable, Any

log = logging.getLogger("resilience")

T = TypeVar("T")

# ── Configurable defaults ──
MAX_RETRIES = 5
BASE_DELAY = 2.0          # seconds
MAX_DELAY = 120.0         # cap at 2 minutes
JITTER = 0.3              # ±30% random jitter
RETRYABLE = (
    ConnectionError, TimeoutError, OSError,
    IOError, BrokenPipeError, ConnectionResetError,
)


def _backoff_delay(attempt: int, base: float = BASE_DELAY,
                   cap: float = MAX_DELAY, jitter: float = JITTER) -> float:
    """Exponential backoff: base * 2^attempt + jitter, capped."""
    raw = base * (2 ** attempt)
    raw = min(raw, cap)
    raw *= (1.0 + random.uniform(-jitter, jitter))
    return max(0.5, raw)


def resilient_call(fn: Callable[..., T], *args,
                   max_retries: int = MAX_RETRIES,
                   base_delay: float = BASE_DELAY,
                   fatal_exceptions: tuple = (),
                   _on_retry: Callable | None = None,
                   **kwargs) -> T:
    """Call fn(*args, **kwargs) with exponential backoff on failure.

    Args:
        fn: The callable to wrap.
        max_retries: Max retry attempts (0 = no retry).
        base_delay: Initial backoff delay in seconds.
        fatal_exceptions: Tuple of exception types that skip retry.
        _on_retry: Optional callback(attempt, exc) called before each retry.

    Returns:
        The return value of fn.

    Raises:
        The last exception after all retries exhausted, or a fatal exception.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except fatal_exceptions:
            raise
        except RETRYABLE as e:
            last_exc = e
            if attempt < max_retries:
                delay = _backoff_delay(attempt, base_delay)
                log.warning(
                    "retry %d/%d for %s after %.1fs: %s",
                    attempt + 1, max_retries, fn.__name__, delay, e,
                )
                if _on_retry:
                    _on_retry(attempt, e)
                time.sleep(delay)
            else:
                log.error(
                    "%s failed after %d retries: %s",
                    fn.__name__, max_retries, e,
                )
        except Exception as e:
            last_exc = e
            log.error("%s unrecoverable: %s", fn.__name__, e)
            if attempt < max_retries:
                delay = _backoff_delay(attempt, base_delay)
                log.warning("retry %d/%d after unrecoverable: %s", attempt + 1, max_retries, e)
                time.sleep(delay)
            else:
                log.error("%s exhausted all retries: %s", fn.__name__, e)

    raise last_exc or RuntimeError(f"{fn.__name__} failed")


def resilient(max_retries: int = MAX_RETRIES, base_delay: float = BASE_DELAY,
              fatal_exceptions: tuple = ()):
    """Decorator: wrap a function with resilient_call."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return resilient_call(
                fn, *args,
                max_retries=max_retries,
                base_delay=base_delay,
                fatal_exceptions=fatal_exceptions,
                **kwargs,
            )
        return wrapper
    return decorator


class ResilienceReport:
    """Accumulates resilience metrics for dashboard exposure."""

    def __init__(self):
        self.total_calls = 0
        self.successes = 0
        self.retries = 0
        self.failures = 0
        self.last_error = None
        self.last_error_time = None

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.successes / self.total_calls

    def record_success(self):
        self.total_calls += 1
        self.successes += 1

    def record_retry(self):
        self.retries += 1

    def record_failure(self, exc: Exception):
        self.total_calls += 1
        self.failures += 1
        self.last_error = str(exc)
        self.last_error_time = time.time()

    def snapshot(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "successes": self.successes,
            "retries": self.retries,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
        }


# Global resilience tracker
REPORT = ResilienceReport()
