"""Correlation ID support for structured logging.

Provides per-thread correlation_id context via threading.local,
so that log records can be labelled with a request-scoped identifier.
"""

import logging
import threading

_local = threading.local()


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current thread."""
    _local.correlation_id = correlation_id


def get_correlation_id() -> str | None:
    """Return the correlation ID for the current thread, or None."""
    return getattr(_local, "correlation_id", None)


class CorrelationIDFilter(logging.Filter):
    """LogFilter that injects the thread-local correlation_id into LogRecords.

    Usage:
        handler.addFilter(CorrelationIDFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        cid = get_correlation_id()
        record.correlation_id = cid if cid is not None else ""
        return True
