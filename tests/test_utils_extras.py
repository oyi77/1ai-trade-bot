"""Tests for async_helpers and retry from tradebot/utils/."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tradebot.utils.async_helpers import (
    ManagedEventLoop,
    asyncify,
    cancel_all,
    run_periodic,
    timeout_wrapper,
)
from tradebot.utils.retry import RetryableError, async_retry

# ── async_helpers ──────────────────────────────────────────────────────


class TestCancelAll:
    """cancel_all() cancels and awaits tasks."""

    @pytest.mark.asyncio
    async def test_cancels_and_awaits_tasks(self):
        cancelled = []

        async def sleeper():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        tasks = [asyncio.create_task(sleeper()) for _ in range(3)]
        # Let tasks start
        await asyncio.sleep(0.01)

        await cancel_all(tasks)

        assert len(cancelled) == 3
        for t in tasks:
            assert t.done()

    @pytest.mark.asyncio
    async def test_empty_list(self):
        await cancel_all([])  # should not raise


class TestRunPeriodic:
    """run_periodic() calls factory at interval."""

    @pytest.mark.asyncio
    async def test_calls_factory_at_interval(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1

        task = asyncio.create_task(run_periodic(factory, interval=0.01))

        # Wait for ~3 calls
        await asyncio.sleep(0.07)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_continues_after_factory_exception(self):
        call_count = 0

        async def flaky_factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")

        task = asyncio.create_task(run_periodic(flaky_factory, interval=0.01))

        await asyncio.sleep(0.07)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Should have been called more than once despite the error
        assert call_count >= 2


class TestTimeoutWrapper:
    """timeout_wrapper() enforces timeouts."""

    @pytest.mark.asyncio
    async def test_returns_result_when_fast_enough(self):
        async def fast():
            return 42

        result = await timeout_wrapper(fast(), timeout=1.0)
        assert result == 42

    @pytest.mark.asyncio
    async def test_returns_default_on_timeout(self):
        async def slow():
            await asyncio.sleep(10)
            return "never"

        result = await timeout_wrapper(slow(), timeout=0.01, default="timed_out")
        assert result == "timed_out"

    @pytest.mark.asyncio
    async def test_default_is_none(self):
        async def slow():
            await asyncio.sleep(10)

        result = await timeout_wrapper(slow(), timeout=0.01)
        assert result is None


class TestAsyncify:
    """asyncify() runs sync function in thread."""

    @pytest.mark.asyncio
    async def test_runs_sync_in_thread(self):
        def sync_add(a, b):
            return a + b

        result = await asyncify(sync_add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_passes_kwargs(self):
        def sync_fn(x, multiplier=1):
            return x * multiplier

        result = await asyncify(sync_fn, 5, multiplier=3)
        assert result == 15


class TestManagedEventLoop:
    """ManagedEventLoop lifecycle."""

    def test_start_and_stop(self):
        mel = ManagedEventLoop()
        assert mel._stopping is False
        assert mel._loop is not None
        assert mel._tasks == []

        # Clean up the loop we created
        mel._loop.close()

    def test_add_task(self):
        mel = ManagedEventLoop()

        async def dummy():
            await asyncio.sleep(0.01)

        task = mel.add_task(dummy())
        assert task in mel._tasks
        assert isinstance(task, asyncio.Task)

        # Cleanup
        task.cancel()
        mel._loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        mel._loop.close()

    def test_shutdown_cancels_tasks(self):
        mel = ManagedEventLoop()
        cancelled = []

        async def sleeper():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        # Create a task on the managed loop manually
        task = mel._loop.create_task(sleeper())
        mel._tasks.append(task)

        # Run shutdown on the managed loop
        mel._loop.run_until_complete(mel.shutdown())

        assert mel._stopping is True
        assert len(cancelled) == 1

        mel._loop.close()


# ── retry ──────────────────────────────────────────────────────────────


class TestRetryableError:
    """RetryableError is a proper Exception subclass."""

    def test_is_exception_subclass(self):
        assert issubclass(RetryableError, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(RetryableError, match="test error"):
            raise RetryableError("test error")

    def test_message_preserved(self):
        err = RetryableError("specific message")
        assert str(err) == "specific message"


class TestAsyncRetry:
    """async_retry decorator behavior."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        call_count = 0

        @async_retry(max_attempts=3)
        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_retryable_error(self, mock_sleep):
        call_count = 0

        @async_retry(max_attempts=3, jitter=False)
        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RetryableError("not yet")
            return "done"

        result = await fail_then_succeed()
        assert result == "done"
        assert call_count == 3
        assert mock_sleep.await_count == 2  # slept between retries

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_timeout_error(self, mock_sleep):
        call_count = 0

        @async_retry(max_attempts=3, jitter=False)
        async def timeout_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("timed out")
            return "ok"

        result = await timeout_then_succeed()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_gives_up_after_max_attempts(self, mock_sleep):
        call_count = 0

        @async_retry(max_attempts=3, jitter=False)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise RetryableError("permanent failure")

        with pytest.raises(RetryableError, match="permanent failure"):
            await always_fail()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_errors(self):
        call_count = 0

        @async_retry(max_attempts=3)
        async def non_retryable():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await non_retryable()

        assert call_count == 1  # no retries

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_custom_retryable_predicate(self, mock_sleep):
        call_count = 0

        def only_value_errors(exc):
            return isinstance(exc, ValueError)

        @async_retry(max_attempts=3, retryable_predicate=only_value_errors, jitter=False)
        async def custom_retry():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("retry me")
            return "recovered"

        result = await custom_retry()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_exponential_backoff_delays(self, mock_sleep):
        call_count = 0

        @async_retry(max_attempts=4, base_delay=1.0, jitter=False, exponential_backoff=True)
        async def fail_three_times():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise RetryableError("retry")
            return "done"

        result = await fail_three_times()
        assert result == "done"

        # Check exponential backoff: 1.0, 2.0, 4.0
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_constant_delay_when_backoff_disabled(self, mock_sleep):
        call_count = 0

        @async_retry(max_attempts=3, base_delay=2.0, jitter=False, exponential_backoff=False)
        async def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RetryableError("retry")
            return "ok"

        result = await fail_twice()
        assert result == "ok"

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [2.0, 2.0]

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_max_delay_cap(self, mock_sleep):
        call_count = 0

        @async_retry(
            max_attempts=5,
            base_delay=10.0,
            max_delay=5.0,
            jitter=False,
            exponential_backoff=True,
        )
        async def fail_four_times():
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                raise RetryableError("retry")
            return "done"

        result = await fail_four_times()
        assert result == "done"

        # Delays: min(10, 5)=5, min(20, 5)=5, min(40, 5)=5, min(80, 5)=5
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert all(d == 5.0 for d in sleep_calls)

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        @async_retry(max_attempts=1)
        async def add(a, b, offset=0):
            return a + b + offset

        result = await add(1, 2, offset=10)
        assert result == 13
