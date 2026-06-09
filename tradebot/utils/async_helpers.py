"""Async utility helpers for task management and lifecycle."""

import asyncio
import signal
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


async def cancel_all(tasks: list[asyncio.Task[Any]]) -> None:
    """Cancel and await all tasks in the list, ignoring cancellation errors."""
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_periodic(
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    interval: float,
    name: str = "",
) -> None:
    """Run a coroutine repeatedly at *interval* seconds.

    The coroutine is awaited (not scheduled concurrently), so overlapping
    executions are prevented automatically. If *coro_factory* raises, the
    loop stops.
    """
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Log but don't crash — keep the loop going unless cancelled
            import logging
            logger = logging.getLogger(f"run_periodic.{name}" if name else "run_periodic")
            logger.exception("Unhandled error in periodic task %r", name)

        await asyncio.sleep(interval)


async def timeout_wrapper(
    coro: Awaitable[T],
    timeout: float,
    default: T = None,  # type: ignore[assignment]
) -> T:
    """Run *coro* with a timeout. Returns *default* on timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        return default  # type: ignore[return-value]


async def asyncify(sync_fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous function in a thread pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: sync_fn(*args, **kwargs))


class ManagedEventLoop:
    """Manages an asyncio event loop for application lifecycle.

    Usage::

        loop = ManagedEventLoop()
        loop.add_task(some_coro())
        loop.add_task(another_coro())
        loop.run()
        # On SIGINT/SIGTERM loop.shutdown() is called automatically.
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[Any]] = []
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stopping = False

    def add_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Schedule *coro* as a task and track it."""
        task = self._loop.create_task(coro)
        self._tasks.append(task)
        return task

    async def shutdown(self) -> None:
        """Cancel all tracked tasks and stop the loop."""
        if self._stopping:
            return
        self._stopping = True
        await cancel_all(self._tasks)
        self._loop.stop()

    def _signal_handler(self, sig: signal.Signals) -> None:
        """Handle termination signals by scheduling shutdown."""
        if not self._stopping:
            asyncio.ensure_future(self.shutdown(), loop=self._loop)

    def run(self) -> None:
        """Start the event loop. Blocks until shutdown is triggered."""
        try:
            self._loop.add_signal_handler(
                signal.SIGINT, self._signal_handler, signal.SIGINT
            )
            self._loop.add_signal_handler(
                signal.SIGTERM, self._signal_handler, signal.SIGTERM
            )
        except (ValueError, RuntimeError):
            # Not on the main thread — signals can't be set here
            pass

        try:
            self._loop.run_forever()
        finally:
            self._loop.close()
