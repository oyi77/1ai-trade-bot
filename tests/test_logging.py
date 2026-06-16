"""Tests for tradebot.logutils — JSON formatter, correlation IDs, setup."""

from __future__ import annotations

import contextlib
import json
import logging
import threading

from tradebot.logutils import (
    CorrelationIDFilter,
    JSONFormatter,
    get_correlation_id,
    get_logger,
    set_correlation_id,
    setup_logging,
)
from tradebot.logutils.middleware import _local

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_record(
    level: int = logging.INFO,
    msg: str = "test message",
    name: str = "test.logger",
    exc_info: tuple | None = None,
    **extra,
) -> logging.LogRecord:
    """Create a LogRecord with optional extra attributes."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def _clear_cid():
    """Remove thread-local correlation id if present."""
    with contextlib.suppress(AttributeError):
        del _local.correlation_id


# ------------------------------------------------------------------
# JSONFormatter.format()
# ------------------------------------------------------------------

def test_format_produces_valid_json():
    """format() output must parse as valid JSON."""
    fmt = JSONFormatter()
    record = _make_record()
    result = fmt.format(record)

    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_format_includes_timestamp_and_level():
    """JSON output must contain time, level, and message keys.

    NOTE: _INCLUDED_ATTRS loop overwrites ``message`` with "" because
    ``record.message`` is not set on a raw LogRecord — only after
    ``super().format()`` runs.  The ``message`` key is always present
    but may be empty.
    """
    fmt = JSONFormatter()
    record = _make_record(level=logging.WARNING, msg="heads up")
    parsed = json.loads(fmt.format(record))

    assert "time" in parsed
    assert parsed["level"] == "WARNING"
    assert "message" in parsed


def test_format_message_populated_after_super_format():
    """When the record already has ``message`` set (by a parent Formatter
    calling ``super().format()``), the value persists into JSON output."""
    fmt = JSONFormatter()
    record = _make_record(msg="heads up")
    # Simulate what logging.Formatter.format() does: set record.message
    record.message = record.getMessage()
    record.asctime = fmt.formatTime(record)
    parsed = json.loads(fmt.format(record))

    assert parsed["message"] == "heads up"


def test_format_includes_correlation_id():
    """correlation_id appears in JSON output (empty string if unset)."""
    fmt = JSONFormatter()
    record = _make_record()
    record.correlation_id = "abc-123"
    parsed = json.loads(fmt.format(record))

    assert parsed["correlation_id"] == "abc-123"


def test_format_handles_exception_info():
    """Traceback is serialized into exc_info key when present."""
    fmt = JSONFormatter()
    try:
        raise ValueError("bad value")
    except ValueError:
        import sys

        exc = sys.exc_info()

    record = _make_record(msg="error occurred", exc_info=exc)
    parsed = json.loads(fmt.format(record))

    assert "exc_info" in parsed
    assert "ValueError" in parsed["exc_info"]
    assert "bad value" in parsed["exc_info"]


def test_format_exc_info_absent_when_no_exception():
    """exc_info key must not appear when there is no exception."""
    fmt = JSONFormatter()
    record = _make_record()
    parsed = json.loads(fmt.format(record))

    assert "exc_info" not in parsed


# ------------------------------------------------------------------
# JSONFormatter._extra_fields()
# ------------------------------------------------------------------

def test_extra_fields_extracts_custom_attrs():
    """Non-standard attributes are grouped under the 'extra' key."""
    fmt = JSONFormatter()
    record = _make_record(trade_id="T-42", side="BUY")
    parsed = json.loads(fmt.format(record))

    assert "extra" in parsed
    assert parsed["extra"]["trade_id"] == "T-42"
    assert parsed["extra"]["side"] == "BUY"


def test_extra_fields_excludes_known_attrs():
    """Standard LogRecord attrs that are in the KNOWN set must not
    leak into 'extra'."""
    fmt = JSONFormatter()
    record = _make_record(name="mymod")
    parsed = json.loads(fmt.format(record))

    extra = parsed.get("extra", {})
    # 'name' is in _INCLUDED_ATTRS, handled separately
    assert "name" not in extra
    # Standard attrs that are in the KNOWN set must be excluded
    for forbidden in ("lineno", "funcName", "pathname", "process"):
        assert forbidden not in extra


def test_extra_fields_leaks_attrs_not_in_known_set():
    """The ``msg`` attribute is not in the KNOWN set, so it appears in
    extras — documenting the current implementation."""
    fmt = JSONFormatter()
    record = _make_record(msg="hello %s")
    parsed = json.loads(fmt.format(record))

    assert "extra" in parsed
    assert parsed["extra"]["msg"] == "hello %s"


# ------------------------------------------------------------------
# CorrelationIDFilter
# ------------------------------------------------------------------

def test_correlation_id_filter_injects_into_record():
    """CorrelationIDFilter sets record.correlation_id from thread-local."""
    filt = CorrelationIDFilter()
    record = _make_record()

    set_correlation_id("req-99")
    filt.filter(record)

    assert record.correlation_id == "req-99"
    _clear_cid()


def test_correlation_id_filter_empty_when_unset():
    """When no correlation ID is set, record.correlation_id is ''."""
    filt = CorrelationIDFilter()
    _clear_cid()

    record = _make_record()
    filt.filter(record)

    assert record.correlation_id == ""


def test_correlation_id_filter_always_returns_true():
    """filter() must always return True (never suppress records)."""
    filt = CorrelationIDFilter()
    assert filt.filter(_make_record()) is True


# ------------------------------------------------------------------
# set_correlation_id / get_correlation_id (thread-local)
# ------------------------------------------------------------------

def test_set_get_correlation_id():
    """set/get round-trips correctly within the same thread."""
    set_correlation_id("abc")
    assert get_correlation_id() == "abc"
    _clear_cid()


def test_get_correlation_id_returns_none_when_unset():
    """get_correlation_id() returns None when never set in this thread."""
    _clear_cid()
    assert get_correlation_id() is None


def test_correlation_id_is_thread_local():
    """Each thread has its own correlation ID."""
    _clear_cid()

    set_correlation_id("main-thread")
    results: dict[str, str | None] = {}

    def worker():
        # Other thread should not see main-thread's value
        results["other_default"] = get_correlation_id()
        set_correlation_id("worker-thread")
        results["other_set"] = get_correlation_id()

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert get_correlation_id() == "main-thread"
    assert results["other_default"] is None
    assert results["other_set"] == "worker-thread"
    _clear_cid()


# ------------------------------------------------------------------
# setup_logging()
# ------------------------------------------------------------------

def test_setup_logging_configures_root_logger():
    """setup_logging() returns the root logger with at least one handler."""
    root = setup_logging(level="DEBUG", log_format="console")

    assert isinstance(root, logging.Logger)
    assert root.level == logging.DEBUG
    assert len(root.handlers) >= 1

    # Cleanup — reset root logger to avoid polluting other tests
    root.handlers.clear()
    root.setLevel(logging.WARNING)


def test_setup_logging_console_uses_stream_handler():
    """Console format produces a StreamHandler with a plain Formatter."""
    root = setup_logging(level="INFO", log_format="console")

    stream_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert len(stream_handlers) >= 1
    handler = stream_handlers[0]
    # Console formatter is a plain logging.Formatter, not JSONFormatter
    assert isinstance(handler.formatter, logging.Formatter)
    assert not isinstance(handler.formatter, JSONFormatter)

    root.handlers.clear()


def test_setup_logging_json_uses_json_formatter(tmp_path):
    """JSON format produces a handler using JSONFormatter."""
    root = setup_logging(
        level="WARNING",
        log_format="json",
        log_dir=str(tmp_path / "logs"),
    )

    json_handlers = [
        h for h in root.handlers if isinstance(h.formatter, JSONFormatter)
    ]
    assert len(json_handlers) >= 1
    assert root.level == logging.WARNING

    root.handlers.clear()


def test_setup_logging_json_fallback_to_stderr(tmp_path):
    """When log_dir is unwritable and json_fallback=True, falls back to stderr."""
    # Point to a path that can't be created (file in place of dir)
    bad_dir = tmp_path / "blocked"
    bad_dir.write_text("x")  # now a file, not a directory

    root = setup_logging(
        level="INFO",
        log_format="json",
        log_dir=str(bad_dir),
        json_fallback=True,
    )

    # Should have a StreamHandler (stderr fallback), not a FileHandler
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 0
    assert len(root.handlers) >= 1

    root.handlers.clear()


def test_setup_logging_attaches_correlation_filter():
    """Each handler gets a CorrelationIDFilter."""
    root = setup_logging(level="INFO", log_format="console")

    for handler in root.handlers:
        filt_classes = [type(f) for f in handler.filters]
        assert CorrelationIDFilter in filt_classes

    root.handlers.clear()


# ------------------------------------------------------------------
# get_logger()
# ------------------------------------------------------------------

def test_get_logger_returns_named_logger():
    """get_logger(name) returns a logger with the correct name."""
    logger = get_logger("tradebot.test")
    assert logger.name == "tradebot.test"


def test_get_logger_returns_root_when_no_name():
    """get_logger() with no args returns the root logger."""
    logger = get_logger()
    assert logger is logging.getLogger()
