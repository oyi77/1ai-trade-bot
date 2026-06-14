"""Bridge to tradebot.analytics.learning — Vilona Autonomous Learning Engine."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("learning_engine")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tradebot.analytics.learning import (
        DEFAULT_WEIGHTS,
        _determine_root_cause,
        _learn_from_sl,
        _learn_from_tp,
        _load_lessons,
        _load_patterns,
        _save_lessons,
        _save_patterns,
        fetch_historical_data,
        format_learning_report,
        learn_from_sl_stats,
        learn_from_tp_stats,
        record_trade_outcome,
        run_learning_pipeline,
    )
except ImportError as exc:
    logger.error("Failed to import learning engine: %s", exc)
    raise


def track_signal(signal_id: str, outcome: str, pips: float, entry: float,
                 sl: float, tp: float, symbol: str = "XAUUSD",
                 action: str = "BUY", grade: str = "B",
                 confidence: int = 50, open_time: str = "",
                 close_time: str = "", close_price: float = 0) -> dict | None:
    """Track a signal outcome — called by signal publisher after trade closes.

    This is the main entry point from auto_signal_publisher / executor.
    Delegates to record_trade_outcome with normalized trade dict.
    """
    trade = {
        "id": signal_id,
        "symbol": symbol,
        "action": action,
        "entry": entry,
        "entry_price": entry,
        "sl": sl,
        "tp": tp,
        "pips": pips,
        "grade": grade,
        "confidence": confidence,
        "outcome": outcome,
        "open_time": open_time,
        "close_time": close_time,
        "close_price": close_price,
    }
    return record_trade_outcome(trade, current_price=close_price)


__all__ = [
    "track_signal",
    "record_trade_outcome",
    "run_learning_pipeline",
    "format_learning_report",
    "fetch_historical_data",
    "learn_from_tp_stats",
    "learn_from_sl_stats",
    "DEFAULT_WEIGHTS",
    "_load_lessons",
    "_load_patterns",
    "_save_lessons",
    "_save_patterns",
    "_determine_root_cause",
    "_learn_from_sl",
    "_learn_from_tp",
]
