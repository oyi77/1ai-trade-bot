"""tradebot.logging — structured JSON logging for 1ai-trade-bot."""

from .formatter import JSONFormatter
from .middleware import (
    CorrelationIDFilter,
    get_correlation_id,
    set_correlation_id,
)
from .setup import get_logger, setup_logging

__all__ = [
    "setup_logging",
    "get_logger",
    "JSONFormatter",
    "CorrelationIDFilter",
    "set_correlation_id",
    "get_correlation_id",
]
