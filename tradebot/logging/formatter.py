"""JSON formatter for structured logging.

Outputs log records as newline-delimited JSON objects suitable for
ingestion by log aggregators (ELK, Loki, Datadog, etc.).
"""

import json
import logging
import traceback as tb_module
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Produces one JSON line per log record.

    The output object contains:
        - time         ISO-8601 UTC timestamp  (str)
        - level        Log level name            (str)
        - name         Logger name               (str)
        - message      Formatted log message     (str)
        - correlation_id                         (str, may be empty)
        - exc_info     Traceback text *or* None  (Optional[str])
        - extra        Arbitrary extra fields    (dict)

    Any keyword arguments passed to the logger (e.g. logger.info("msg",
    extra={"key": "val"})) that are *not* standard LogRecord attributes
    are merged into the top-level JSON object.

    Standard LogRecord attributes that are never included:
        args, exc_text, created, msecs, relativeCreated, process,
        processName, thread, threadName, module, lineno, funcName,
        filename, pathname.
    """

    # Attributes we *do* carry into the JSON output from the LogRecord.
    _INCLUDED_ATTRS = frozenset({
        "name", "message", "correlation_id",
    })

    def __init__(
        self,
        *,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        validate: bool = True,
        ensure_ascii: bool = False,
        **json_kwargs: Any,
    ) -> None:  # type: ignore[override]
        super().__init__(fmt, datefmt, style, validate)
        self._ensure_ascii = ensure_ascii
        self._json_kwargs = json_kwargs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def format(self, record: logging.LogRecord) -> str:
        """Return a single JSON line for *record*."""
        return json.dumps(
            self._build_obj(record),
            ensure_ascii=self._ensure_ascii,
            default=str,
            **self._json_kwargs,
        )

    def formatTime(  # noqa: N802
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Return an ISO-8601 UTC timestamp string."""
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        return dt.isoformat()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_obj(self, record: logging.LogRecord) -> dict[str, Any]:
        """Assemble the final JSON-serialisable dictionary for *record*."""
        obj: dict[str, Any] = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Copy included standard attributes.
        for attr in self._INCLUDED_ATTRS:
            val = getattr(record, attr, None)
            if val is not None:
                obj[attr] = val
            else:
                obj[attr] = ""

        # Exception info.
        exc = self._format_exc(record)
        if exc is not None:
            obj["exc_info"] = exc

        # Extra / custom fields — anything on the record that isn't part
        # of the stdlib's known set.
        extras = self._extra_fields(record)
        if extras:
            obj["extra"] = extras

        return obj

    @staticmethod
    def _format_exc(record: logging.LogRecord) -> str | None:
        """Return formatted traceback string or *None*."""
        if record.exc_info:
            return "".join(
                tb_module.format_exception(*record.exc_info)
            )
        if record.exc_text:
            return record.exc_text
        return None

    @staticmethod
    def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
        """Return custom attributes that are not built-in LogRecord fields.

        We consider everything a custom field *except* the known stdlib
        attributes and the ones we explicitly handle ourselves.
        """
        KNOWN = frozenset({  # noqa: N806
            "args", "asctime", "created", "exc_info", "exc_text",
            "filename", "funcName", "levelname", "levelno", "lineno",
            "module", "msecs", "message", "name", "pathname",
            "process", "processName", "relativeCreated",
            "stack_info", "thread", "threadName",
            # our own
            "correlation_id",
        })
        return {
            k: v
            for k, v in record.__dict__.items()
            if k not in KNOWN
        }
