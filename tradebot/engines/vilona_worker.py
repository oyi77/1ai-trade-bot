"""
4-Pillar Autonomous Production Worker — Standalone Entry Point.

Starts the AutonomousWorker as a systemd-managed daemon.
All 4 pillars active:
  1. PhantomSync → real-time dashboard data
  2. Resilience → exponential backoff on all external calls
  3. AutonomousWorker → 24/7 non-stop loop with heartbeat
  4. DataGate → SOP enforcement (SL/TP clamp, OHLCV validation, killzone)

Usage:
    PYTHONPATH=. python3 tradebot/engines/vilona_worker.py
"""

import logging
import signal
import sys
import os
import importlib.util

# Configure logging
LOG_DIR = os.path.expanduser("~/projects/1ai-trade-bot/data/vilona_tradefx")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "worker.log")),
    ],
)
log = logging.getLogger("vilona-worker")

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_engine(name):
    """Load engine module without triggering heavy tradebot __init__."""
    path = os.path.join(PROJECT_DIR, "tradebot", "engines", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"tradebot.engines.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    # Load all 4 pillars
    resilience_mod = _load_engine("resilience")
    data_gate_mod = _load_engine("data_gate")
    phantomfx_mod = _load_engine("phantomfx_sync")
    worker_mod = _load_engine("autonomous_worker")

    AutonomousWorker = worker_mod.AutonomousWorker

    log.info("=" * 60)
    log.info("🚀 VILONA AUTONOMOUS PRODUCTION WORKER")
    log.info("   4-Pillar Protocol: ACTIVE")
    log.info("   Pillar 1: Web Sync → PhantomFX dashboard")
    log.info("   Pillar 2: Resilience → Zero-crash backoff")
    log.info("   Pillar 3: Daemon Loop → 24/7 Heartbeat")
    log.info("   Pillar 4: Data Gate → SL/TP Clamp + OHLCV validation")
    log.info("=" * 60)

    worker = AutonomousWorker(assets=[
        ("gold", "XAUUSD", "GC=F", True),
        ("btc", "BTCUSD", "BTC-USD", False),
        ("oil", "USOIL", "CL=F", True),
    ])

    # Graceful shutdown on SIGTERM/SIGINT
    def _shutdown(signum, frame):
        log.info("Received signal %d — shutting down", signum)
        worker.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        worker.run()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Worker exited. Total cycles: %d, signals: %d",
                 worker.sync.total_cycles, worker.sync.total_signals)


if __name__ == "__main__":
    main()
