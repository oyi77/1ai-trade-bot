
"""
Alert Daemon — Hybrid Decision Engine
======================================
Standalone polling loop that watches for new signals and broadcasts them.

Usage:
    python -m hybrid_decision_engine.alert_daemon

Environment:
    TELEGRAM_BOT_TOKEN    — Bot token for sending
    SIGNAL_CHANNEL_ID     — Target chat/channel ID
    HYBRID_SHADOW_MODE    — "1" for shadow, "0" for live (default: 1)
    HYBRID_POLL_INTERVAL  — Seconds between checks (default: 5)
    HYBRID_MIN_GRADE      — Minimum grade to broadcast (default: B)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ENGINE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from hybrid_decision_engine import config
from hybrid_decision_engine.alert_broadcaster import AlertBroadcaster

POLL_INTERVAL = int(os.environ.get("HYBRID_POLL_INTERVAL", "5"))
SHADOW_MODE = os.environ.get("HYBRID_SHADOW_MODE", "1") == "1"
MIN_GRADE = os.environ.get("HYBRID_MIN_GRADE", "B")

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_DIR / "hybrid_alert.log"),
    ],
)
logger = logging.getLogger("hybrid.daemon")

_running = True

def _shutdown(signum, frame):
    global _running
    logger.info("Shutdown signal received (%s)", signum)
    _running = False

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


def main():
    logger.info("=" * 60)
    logger.info("Hybrid Alert Daemon starting")
    logger.info("  Poll interval: %ds", POLL_INTERVAL)
    logger.info("  Shadow mode: %s", SHADOW_MODE)
    logger.info("  Min grade: %s", MIN_GRADE)
    logger.info("  Signal dir: %s", config.SIGNALS_DIR)
    logger.info("=" * 60)

    broadcaster = AlertBroadcaster(
        shadow_mode=SHADOW_MODE,
        min_grade=MIN_GRADE,
    )

    if not broadcaster.is_enabled:
        logger.warning("Telegram not configured — daemon running in WATCH-ONLY mode")

    cycle = 0
    while _running:
        cycle += 1
        try:
            broadcaster.check_and_broadcast()
        except Exception as e:
            logger.error("Cycle %d error: %s", cycle, e, exc_info=True)
        time.sleep(POLL_INTERVAL)

    logger.info("Hybrid Alert Daemon stopped (ran %d cycles)", cycle)


if __name__ == "__main__":
    main()
