"""
Pillar 1: Full Web Synchronization — PhantomFX Dashboard Data Feeder.

Pushes real-time system status to the dashboard data files every cycle:
  - Live Trading Signals & Status Analysis (e.g., "Analyzing M15...", "Waiting for FVG...")
  - Real-time Win Rate, PnL, Total Users, Bot Users
  - System health: uptime, cycles run, errors, AI provider status

All data is written to JSON files consumed by dashboard_server.py (port 8768).

Usage:
    from tradebot.engines.phantomfx_sync import PhantomSync

    sync = PhantomSync()
    sync.push_status("analyzing", {"pair": "XAUUSD", "tf": "M15"})
    sync.push_signal(signal_dict)
    sync.push_health()  # auto-called every cycle
"""

import json
import time
import os
import threading
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

log = logging.getLogger("phantomsync")

WIB = timezone(timedelta(hours=7))
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "vilona_tradefx"
SYNC_FILE = DATA_DIR / "live_status.json"       # Real-time system status
SIGNAL_FEED = DATA_DIR / "signal_feed.json"     # Signal history feed
MEMBERS_DB = DATA_DIR / "members.db"            # Members DB (read-only)


class PhantomSync:
    """Feeds live data to the PhantomFX public dashboard."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.start_time = time.time()
        self.total_cycles = 0
        self.total_signals = 0
        self.total_errors = 0
        self.current_status = "idle"
        self.current_pair = ""
        self.current_detail = ""
        self.ai_status = {}
        self.price_cache = {}
        self.win_rate = 0.0
        self.total_pnl = 0.0

    # ── Status push ──

    def push_status(self, status: str, detail: dict | None = None):
        """Push current analysis status: 'idle', 'analyzing', 'waiting_fvg', 'signal_generated'."""
        self.current_status = status
        if detail:
            self.current_pair = detail.get("pair", self.current_pair)
            self.current_detail = detail.get("detail", "")
        self._write()

    # ── Signal push ──

    def push_signal(self, signal: dict):
        """Record a generated signal to the feed."""
        self.total_signals += 1
        entry = {
            "signal_id": signal.get("signal_id", ""),
            "symbol": signal.get("symbol", "XAUUSD"),
            "action": signal.get("action", "HOLD"),
            "entry": signal.get("entry"),
            "sl": signal.get("sl"),
            "tp": signal.get("tp"),
            "confidence": signal.get("confidence"),
            "quality": signal.get("quality", "B"),
            "timestamp": datetime.now(WIB).isoformat(),
            "status": "active",
        }
        try:
            feed = []
            if SIGNAL_FEED.exists():
                feed = json.loads(SIGNAL_FEED.read_text())
            feed.append(entry)
            # Keep last 200
            if len(feed) > 200:
                feed = feed[-200:]
            SIGNAL_FEED.write_text(json.dumps(feed, indent=2, ensure_ascii=False))
        except Exception as e:
            log.error("push_signal failed: %s", e)
        self._write()

    # ── Health snapshot ──

    def push_health(self, **overrides):
        """Push system health — called every cycle."""
        self.total_cycles += 1
        for k, v in overrides.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self._write()

    # ── Error tracking ──

    def push_error(self, source: str, error: str):
        """Record an error for dashboard visibility."""
        self.total_errors += 1
        log.warning("PhantomSync error [%s]: %s", source, error)
        self._write()

    # ── Write to disk ──

    def _write(self):
        """Atomically write live_status.json."""
        snapshot = {
            "updated_at": datetime.now(WIB).isoformat(),
            "uptime_seconds": int(time.time() - self.start_time),
            "total_cycles": self.total_cycles,
            "total_signals": self.total_signals,
            "total_errors": self.total_errors,
            "status": {
                "state": self.current_status,
                "pair": self.current_pair,
                "detail": self.current_detail,
            },
            "performance": {
                "win_rate": self.win_rate,
                "total_pnl": self.total_pnl,
            },
            "ai_status": self.ai_status,
            "prices": self.price_cache,
            "error_rate": round(self.total_errors / max(self.total_cycles, 1), 4),
        }
        try:
            tmp = SYNC_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
            tmp.rename(SYNC_FILE)
        except Exception as e:
            log.error("PhantomSync write failed: %s", e)

    # ── Trade outcome update ──

    def update_trade_outcome(self, outcome: dict):
        """Update win rate and PnL after trade closes.

        Args:
            outcome: dict with 'result' ('WIN'/'LOSS'), 'pnl_pips', 'pnl_usd'.
        """
        # Read current trade log for stats
        trade_log = DATA_DIR.parent / "trade_log.json"
        wins = 0
        total = 0
        total_pnl = 0.0
        try:
            if trade_log.exists():
                data = json.loads(trade_log.read_text())
                trades = data if isinstance(data, list) else data.get("trades", [])
                for t in trades:
                    res = t.get("result", t.get("outcome", ""))
                    if res in ("WIN", "TP", "TP_HIT"):
                        wins += 1
                    if res in ("WIN", "LOSS", "TP", "SL", "TP_HIT", "SL_HIT"):
                        total += 1
                        total_pnl += float(t.get("pnl", t.get("pnl_usd", 0)))
        except Exception:
            pass

        self.win_rate = round(wins / max(total, 1), 4)
        self.total_pnl = round(total_pnl, 2)
        self._write()


# Global singleton
SYNC = PhantomSync()
