"""Logging initialisation for 1ai-trade-bot.

Provides a single entry point — *setup_logging()* — that configures the
Python ``logging`` root logger for both production (JSON) and development
(human-readable) output.

Environment variables
---------------------
LOG_LEVEL : str
    One of ``DEBUG``, ``INFO`` (default), ``WARNING``, ``ERROR``,
    ``CRITICAL``.
LOG_FORMAT : str
    ``json`` (default) or ``console``.
LOG_DIR : str
    Directory for log files (default ``logs/`` under the project root).
"""

import logging
import os
import sys
from pathlib import Path

from .formatter import JSONFormatter
from .middleware import CorrelationIDFilter

#: Default project-relative log directory.
_DEFAULT_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)

#: Mapped log levels keyed by name.
_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(
    *,
    level: str | None = None,
    log_format: str | None = None,
    log_dir: str | None = None,
    json_fallback: bool = True,
) -> logging.Logger:
    """Configure the root logger for structured logging.

    Parameters
    ----------
    level : str, optional
        Override the log level.  Falls back to the ``LOG_LEVEL`` env var,
        then ``INFO``.
    log_format : str, optional
        ``"json"`` (default) or ``"console"``.  Falls back to the
        ``LOG_FORMAT`` env var.
    log_dir : str, optional
        Path to the log directory.  Falls back to the ``LOG_DIR`` env var,
        then ``<project_root>/logs/``.
    json_fallback : bool
        If *True* (default) and ``log_format`` is ``"json"`` but the log
        directory cannot be written, fall back to stderr JSON output
        instead of raising.

    Returns
    -------
    logging.Logger
        The root logger (already configured).
    """
    # ----- resolve config ---------------------------------------------------
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    log_level = _LEVEL_MAP.get(level_name, logging.INFO)

    fmt = (log_format or os.environ.get("LOG_FORMAT") or "json").lower()
    log_dir_path = Path(log_dir or os.environ.get("LOG_DIR") or _DEFAULT_LOG_DIR)

    # ----- remove any pre-existing handlers ---------------------------------
    root = logging.getLogger()
    root.handlers.clear()

    root.setLevel(log_level)

    # ----- build handler(s) -------------------------------------------------
    if fmt == "console":
        handler = _build_console_handler(log_level)
    else:
        handler = _build_json_handler(log_level, log_dir_path, json_fallback)

    # ----- attach correlation ID filter -------------------------------------
    handler.addFilter(CorrelationIDFilter())
    root.addHandler(handler)

    # ----- silence noisy third-party loggers --------------------------------
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return root


def get_logger(name: str | None = None) -> logging.Logger:
    """Convenience wrapper around ``logging.getLogger``.

    Usage::

        from tradebot.logutils import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _build_console_handler(level: int) -> logging.Handler:
    """Create a human-readable coloured-ish stream handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def _build_json_handler(
    level: int,
    log_dir: Path,
    fallback: bool,
) -> logging.Handler:
    """Create a file handler writing JSON lines into *log_dir*.

    If *log_dir* cannot be created/written and *fallback* is *True*,
    falls back to ``StreamHandler(sys.stderr)`` with JSON format.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        if fallback:
            handler = logging.StreamHandler(sys.stderr)
            handler.setLevel(level)
            handler.setFormatter(JSONFormatter())
            return handler
        raise

    # RotatingFileHandler would be better for production, but keeping it
    # minimal for now — pure stdlib only.
    log_file = log_dir / "tradebot.log"
    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())
    return handler
