"""Async retry utilities with exponential backoff and jitter."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class RetryableError(Exception):
    """Marker exception for errors that should be retried."""
    pass


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_backoff: bool = True,
    jitter: bool = True,
    retryable_predicate: Callable[[Exception], bool] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator that retries an async function on failure.

    Parameters
    ----------
    max_attempts:
        Maximum number of attempts (including the first).
    base_delay:
        Starting delay in seconds.
    max_delay:
        Maximum delay cap in seconds.
    exponential_backoff:
        If True, delay = min(base_delay * 2^(attempt-1), max_delay).
        If False, delay = base_delay (constant).
    jitter:
        If True, apply full jitter: delay = random.uniform(0, delay).
    retryable_predicate:
        Callable that receives the exception and returns True if it should
        be retried. Default: retry on TimeoutError, ConnectionError, and
        RetryableError.

    Returns
    -------
    Decorated async function that will retry according to the configured
    strategy.
    """

    if retryable_predicate is None:

        def _default_predicate(exc: Exception) -> bool:
            return isinstance(exc, (TimeoutError, ConnectionError, RetryableError))

        retryable_predicate = _default_predicate

    def decorator(
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_exc: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc

                    if not retryable_predicate(exc):
                        raise

                    if attempt == max_attempts:
                        raise

                    if exponential_backoff:
                        delay = min(
                            base_delay * (2 ** (attempt - 1)),
                            max_delay,
                        )
                    else:
                        delay = base_delay

                    if jitter:
                        delay = random.uniform(0, delay)

                    await asyncio.sleep(delay)

            # Should never reach here, but keep the type-checker happy
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
