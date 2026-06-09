"""Tests for AsyncRateLimiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from tradebot.utils.rate_limiter import AsyncRateLimiter


class TestAsyncRateLimiter:
    """Token-bucket rate limiter tests."""

    @pytest.mark.asyncio
    async def test_acquire_immediate(self):
        """Acquiring 1 token when bucket is full returns 0.0 wait."""
        limiter = AsyncRateLimiter(max_tokens=10, refill_rate=1, refill_interval=0.1)
        wait = await limiter.acquire("test")
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_key_isolation(self):
        """Different keys have independent buckets."""
        limiter = AsyncRateLimiter(max_tokens=2, refill_rate=1, refill_interval=1.0)
        # Drain key_a
        await limiter.acquire("key_a")
        await limiter.acquire("key_a")
        # key_b should still have full tokens
        wait = await limiter.acquire("key_b")
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_blocks_when_empty(self):
        """Acquiring from an empty bucket blocks until tokens are available."""
        limiter = AsyncRateLimiter(
            max_tokens=1, refill_rate=1, refill_interval=0.05
        )
        await limiter.acquire("test")  # drain
        start = time.monotonic()
        await limiter.acquire("test")  # should block briefly
        elapsed = time.monotonic() - start
        # Should have waited at least ~50ms for refill
        assert elapsed >= 0.04, f"Expected wait >= 40ms, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_acquire_bulk_tokens(self):
        """Acquire multiple tokens in one call."""
        limiter = AsyncRateLimiter(
            max_tokens=10, refill_rate=2, refill_interval=0.05
        )
        wait = await limiter.acquire("test", tokens=5)
        assert wait == 0.0
        # Should have 5 tokens remaining
        wait = await limiter.acquire("test", tokens=5)
        assert wait == 0.0
        # Empty - next call should block
        start = time.monotonic()
        await limiter.acquire("test", tokens=1)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.02

    @pytest.mark.asyncio
    async def test_concurrent_keys(self):
        """Multiple keys can be acquired concurrently without deadlock."""
        limiter = AsyncRateLimiter(
            max_tokens=5, refill_rate=5, refill_interval=0.05
        )

        async def use_key(name: str) -> float:
            total_wait = 0.0
            for _ in range(3):
                w = await limiter.acquire(name)
                total_wait += w
            return total_wait

        results = await asyncio.gather(use_key("a"), use_key("b"), use_key("c"))
        for total_wait in results:
            assert isinstance(total_wait, float)

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Cleanup removes all buckets."""
        limiter = AsyncRateLimiter(max_tokens=10, refill_rate=1, refill_interval=1.0)
        await limiter.acquire("a")
        await limiter.acquire("b")
        assert len(limiter._buckets) == 2
        limiter.cleanup()
        assert len(limiter._buckets) == 0

    @pytest.mark.asyncio
    async def test_custom_max_tokens(self):
        """Custom max_tokens should be respected."""
        limiter = AsyncRateLimiter(max_tokens=3, refill_rate=1, refill_interval=1.0)
        for _ in range(3):
            wait = await limiter.acquire("test")
            assert wait == 0.0
        # Should block on 4th
        start = time.monotonic()
        await limiter.acquire("test")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.9
