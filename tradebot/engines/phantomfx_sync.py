"""
Pillar 1: Full Web Synchronization — PhantomFX Dashboard Data Feeder.

Pushes real-time system status via TWO channels:
  A) Local JSON files (consumed by dashboard_server.py on port 8768)
  B) HTTP Webhook POST to https://phantomfx.aitradepulse.com/id

Data pushed every cycle:
  - Live Trading Signals & Status Analysis ("Analyzing M15...", "Waiting for FVG...")
  - Real-time Win Rate, PnL, Total Active Users, Bot Users
  - System health: uptime, cycles run, errors, AI provider status
  - Heartbeat pulse for liveness monitoring

Usage:
    from tradebot.engines.phantomfx_sync import PhantomSync

    sync = PhantomSync(webhook_url="https://phantomfx.aitradepulse.com/id")
    sync.push_status("analyzing", {"pair": "XAUUSD", "tf": "M15"})
    sync.push_signal(signal_dict)
    sync.push_webhook()   # POST to external dashboard
    sync.push_heartbeat()  # lightweight keepalive
"""

import json
import time
import threading
import logging
import sqlite3
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

log = logging.getLogger("phantomsync")

WIB = timezone(timedelta(hours=7))
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "vilona_tradefx"
SYNC_FILE = DATA_DIR / "live_status.json"
SIGNAL_FEED = DATA_DIR / "signal_feed.json"
MEMBERS_DB = DATA_DIR / "members.db"

import os  # noqa: E402
DEFAULT_WEBHOOK_URL = os.environ.get(
    "PHANTOMFX_WEBHOOK_URL",
    "https://phantomfx.aitradepulse.com/api/webhook/snapshot"
)


class PhantomSync:
    """Feeds live data to the PhantomFX public dashboard (files + webhook)."""

    def __init__(self, webhook_url: str = ""):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.webhook_url = webhook_url or DEFAULT_WEBHOOK_URL
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
        self.active_users = 0
        self.bot_users = 0

    # ═══════════════════════════════════════════════════════════
    #  STATUS PUSHES
    # ═══════════════════════════════════════════════════════════

    def push_status(self, status: str, detail: dict | None = None):
        """Push current analysis status.

        Status values: 'idle', 'starting', 'fetching_price', 'fetching_ohlcv',
                       'analyzing', 'waiting_fvg', 'signal_generated',
                       'blocked', 'stopped'
        """
        self.current_status = status
        if detail:
            self.current_pair = detail.get("pair", self.current_pair)
            self.current_detail = detail.get("detail", "")
        self._write()

    def push_signal(self, signal: dict):
        """Record a generated signal to the feed and push to webhook."""
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
            if len(feed) > 200:
                feed = feed[-200:]
            SIGNAL_FEED.write_text(json.dumps(feed, indent=2, ensure_ascii=False))
        except Exception as e:
            log.error("push_signal feed write failed: %s", e)

        self._write()
        # Fire-and-forget webhook push (non-blocking)
        threading.Thread(target=self._webhook_post, args=(entry,), daemon=True).start()

    def push_health(self, **overrides):
        """Push system health snapshot — called every cycle."""
        self.total_cycles += 1
        for k, v in overrides.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self._write()

    def push_error(self, source: str, error: str):
        """Record an error for dashboard visibility."""
        self.total_errors += 1
        log.warning("PhantomSync error [%s]: %s", source, error)
        self._write()

    def push_heartbeat(self):
        """Lightweight heartbeat — logs uptime only (no full snapshot)."""
        hb = {
            "type": "heartbeat",
            "timestamp": datetime.now(WIB).isoformat(),
            "uptime_seconds": int(time.time() - self.start_time),
            "cycles": self.total_cycles,
            "signals": self.total_signals,
            "errors": self.total_errors,
            "status": self.current_status,
            "pair": self.current_pair,
        }
        threading.Thread(target=self._webhook_post, args=(hb,), daemon=True).start()

    # ═══════════════════════════════════════════════════════════
    #  WEBHOOK PUSH (HTTP POST to phantomfx.aitradepulse.com/id)
    # ═══════════════════════════════════════════════════════════

    def push_webhook(self):
        """Push full dashboard snapshot to external webhook endpoint."""
        self._refresh_user_counts()
        payload = self._build_snapshot()
        threading.Thread(target=self._webhook_post, args=(payload,), daemon=True).start()

    def _webhook_post(self, payload: dict):
        """Fire-and-forget HTTP POST. Never blocks the main loop."""
        if not self.webhook_url:
            return
        try:
            data = json.dumps(payload, ensure_ascii=False).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "X-PhantomFX-Source": "vilona-worker",
                    "X-PhantomFX-Version": "2.0",
                },
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            log.debug("Webhook push failed (non-critical): %s", e)

    # ═══════════════════════════════════════════════════════════
    #  USER COUNTING
    # ═══════════════════════════════════════════════════════════

    def _refresh_user_counts(self):
        """Count active members from SQLite DB."""
        try:
            if not MEMBERS_DB.exists():
                return
            conn = sqlite3.connect(str(MEMBERS_DB))
            cur = conn.cursor()
            # Total active users (status='active' or subscribed)
            cur.execute("SELECT COUNT(*) FROM members WHERE status='active'")
            self.active_users = cur.fetchone()[0] or 0
            # Bot users (have telegram_id)
            cur.execute(
                "SELECT COUNT(*) FROM members WHERE status='active' AND telegram_id IS NOT NULL"
            )
            self.bot_users = cur.fetchone()[0] or 0
            conn.close()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  TRADE OUTCOME
    # ═══════════════════════════════════════════════════════════

    def update_trade_outcome(self, outcome: dict):
        """Update win rate and PnL after trade closes."""
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

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL
    # ═══════════════════════════════════════════════════════════

    def _build_snapshot(self) -> dict:
        return {
            "type": "dashboard_snapshot",
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
            "users": {
                "active": self.active_users,
                "bot_users": self.bot_users,
            },
            "ai_status": self.ai_status,
            "prices": self.price_cache,
            "error_rate": round(self.total_errors / max(self.total_cycles, 1), 4),
        }

    def _write(self):
        """Atomically write live_status.json (local dashboard)."""
        snapshot = self._build_snapshot()
        try:
            tmp = SYNC_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
            tmp.rename(SYNC_FILE)
        except Exception as e:
            log.error("PhantomSync write failed: %s", e)


# Global singleton
SYNC = PhantomSync()
