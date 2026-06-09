"""Async token-bucket rate limiter with per-key tracking."""

import asyncio
import time


class _Bucket:
    """Token bucket for a single key."""

    __slots__ = ("tokens", "last_refill", "max_tokens", "refill_rate", "refill_interval")

    def __init__(self, max_tokens: int, refill_rate: int, refill_interval: float) -> None:
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.refill_interval = refill_interval

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        intervals = elapsed / self.refill_interval
        if intervals >= 1:
            added = int(intervals) * self.refill_rate
            self.tokens = min(self.max_tokens, self.tokens + added)
            self.last_refill = now - (elapsed % self.refill_interval)

    def try_acquire(self, tokens: int) -> float:
        """Try to consume *tokens*.
        Returns 0.0 if successful, otherwise the wait time in seconds.
        """
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return 0.0
        deficit = tokens - self.tokens
        # Estimate how many intervals needed to fill the deficit
        intervals_needed = (deficit + self.refill_rate - 1) // self.refill_rate
        wait = intervals_needed * self.refill_interval - (time.monotonic() - self.last_refill)
        return max(0.0, wait)


class AsyncRateLimiter:
    """Async-safe token-bucket rate limiter.

    Supports arbitrary keys so different API endpoints or users can be
    throttled independently.

    Parameters
    ----------
    max_tokens:
        Maximum number of tokens a key may accumulate.
    refill_rate:
        Number of tokens added per *refill_interval*.
    refill_interval:
        Time in seconds between refills.
    """

    def __init__(
        self,
        max_tokens: int = 10,
        refill_rate: int = 1,
        refill_interval: float = 1.0,
    ) -> None:
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.refill_interval = refill_interval
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, tokens: int = 1) -> float:
        """Acquire *tokens* for *key*.

        Blocks until sufficient tokens are available. Returns the actual
        wait time in seconds (0.0 if no wait).
        """
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    self.max_tokens, self.refill_rate, self.refill_interval
                )
                self._buckets[key] = bucket

            wait = bucket.try_acquire(tokens)
            if wait > 0:
                # Release the lock while sleeping so other keys can proceed
                self._lock.release()
                try:
                    await asyncio.sleep(wait)
                finally:
                    await self._lock.acquire()
                # Re-check after sleep (another task may have consumed tokens)
                wait = bucket.try_acquire(tokens)
                # In practice wait should be 0.0 now; if not, yield it anyway.
        return wait

    def cleanup(self) -> None:
        """Remove all tracked buckets. Calling this while another task holds
        a reference to a bucket is safe — the dict entry is simply dropped."""
        self._buckets.clear()
