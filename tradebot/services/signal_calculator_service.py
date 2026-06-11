"""Signal calculator service — wraps scripts/signal_calculator.

Temporary proxy until scripts/signal_calculator is absorbed into
tradebot.engines or tradebot.pipeline.
"""

from __future__ import annotations

from scripts.signal_calculator import (  # type: ignore[import-not-found]
    compute_signal,
    format_signal_telegram,
    log_signal,
)

__all__ = ["compute_signal", "format_signal_telegram", "log_signal"]
