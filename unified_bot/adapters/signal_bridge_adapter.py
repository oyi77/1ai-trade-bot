"""
Signal Bridge Adapter — wraps scripts/vilona_tradefx_signal_bridge.py.

Provides a unified interface to the Vilona Trade FX signal bridge:
- License validation (SQLite-backed)
- Signal polling (per user / per instance)
- MT5 daemon registration and SL modification
- Trading status reporting
- Admin key management
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)


@dataclass
class BridgeConfig:
    """Configuration for the signal bridge adapter."""

    project_dir: str = ""
    tiers_file: str = ""
    db_path: str = ""
    admin_secret: str = ""
    telegram_bot_token: str = ""
    telegram_alert_chat_id: str = ""
    dashboard_dir: str = "/var/www/phantomfx-dashboard/dist"
    cache_ttl: int = 60
    signal_dedup_ttl: int = 60


@dataclass
class LicenseInfo:
    """Normalized license information."""

    api_key: str = ""
    tier: str = "starter"
    label: str = ""
    active: bool = False
    rate_limit: int = 3
    rate_window_seconds: int = 86400
    expires: str = "2026-12-31"
    features: list[str] = field(default_factory=list)
    max_layers: int = 1


@dataclass
class SignalData:
    """Normalized signal data for EA consumption."""

    signal_id: str = ""
    symbol: str = ""
    action: str = "HOLD"
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    tp4: float = 0.0
    risk_percent: float = 1.0
    comment: str = "VTFX/AI"
    confidence: float = 0.0
    layers: list = field(default_factory=list)
    layer_count: int = 0
    tier: str = ""
    pending: bool = False


@dataclass
class DaemonInfo:
    """Information about a connected MT5 daemon."""

    daemon_id: str = ""
    account_id: str = ""
    api_key: str = ""
    last_seen: float = 0.0
    hostname: str = ""
    mt5_version: str = ""
    active_ticket: int = 0


class SignalBridgeAdapter:
    """
    Adapter wrapping the vilona_tradefx_signal_bridge.py functionality.

    Uses the same SQLite license database and in-memory signal queues
    as the standalone bridge server, but callable directly from UnifiedBot.

    Usage in UnifiedBot:
        bridge = SignalBridgeAdapter(config)
        await bridge.initialize()
        valid, tier_info = bridge.validate_key(api_key)
        signal = bridge.poll_signal(api_key, account_id)
        bridge.push_signal(signal_data)
    """

    def __init__(self, config: Optional[BridgeConfig] = None):
        self.config = config or BridgeConfig()
        self._initialized = False
        self._db_conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()

        # License cache
        self._license_cache: dict[str, dict] = {}
        self._license_cache_time: float = 0.0

        # Signal queues
        self._pending: deque = deque(maxlen=100)
        self._pending_by_key: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._pending_by_instance: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )
        self._signal_lock = threading.Lock()

        # History / dedup
        self._history: deque = deque(maxlen=500)
        self._acked: set = set()
        self._acked_by_key: dict[str, set] = defaultdict(set)
        self._signal_dedup_cache: dict[str, float] = {}

        # Daemon registry
        self._daemons: dict[str, dict] = {}
        self._daemon_sl_queue: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=10)
        )
        self._trade_reports: deque = deque(maxlen=200)

        # Rate counters
        self._rate_counters: dict[str, list[float]] = defaultdict(list)
        self._rate_lock = threading.Lock()

        # ID counter
        self._id_counter: int = 0

        # Tiers cache
        self._tiers_cache: dict = {}
        self._tiers_cache_time: float = 0.0

    async def initialize(self) -> bool:
        """Open SQLite database and load tiers."""
        try:
            cfg = self.config

            if not cfg.project_dir:
                cfg.project_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            if not cfg.tiers_file:
                cfg.tiers_file = os.path.join(cfg.project_dir, "config", "vilona_tiers.json")
            if not cfg.db_path:
                cfg.db_path = os.path.join(cfg.project_dir, "data", "vilona_licenses.db")
            if not cfg.admin_secret:
                cfg.admin_secret = os.environ.get("VILONA_ADMIN_SECRET", "")
            if not cfg.telegram_bot_token:
                cfg.telegram_bot_token = os.environ.get(
                    "VILONA_TELEGRAM_BOT_TOKEN", ""
                )

            os.makedirs(os.path.dirname(cfg.db_path), exist_ok=True)

            conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS licenses (
                    api_key TEXT PRIMARY KEY,
                    tier TEXT NOT NULL DEFAULT 'starter',
                    label TEXT DEFAULT '',
                    rate_limit INTEGER DEFAULT 3,
                    rate_window_seconds INTEGER DEFAULT 86400,
                    expires TEXT DEFAULT '2026-12-31',
                    active INTEGER DEFAULT 1,
                    features TEXT DEFAULT '[]'
                )
            """)
            conn.commit()

            count = conn.execute(
                "SELECT COUNT(*) as n FROM licenses"
            ).fetchone()["n"]
            self._db_conn = conn
            self._initialized = True
            LOG.info("SignalBridgeAdapter initialized | %d keys in DB", count)
            return True
        except Exception as e:
            LOG.error("SignalBridgeAdapter init failed: %s", e)
            return False

    # ── Tiers ──

    def _load_tiers(self) -> dict:
        now = time.time()
        if self._tiers_cache and (now - self._tiers_cache_time) < 300:
            return self._tiers_cache

        tiers_file = self.config.tiers_file
        tiers = {
            "tiers": {
                "starter": {"max_layers": 1, "features": []},
                "pro": {"max_layers": 3, "features": ["trailing", "broadcast"]},
                "elite": {
                    "max_layers": 5,
                    "features": ["trailing", "broadcast", "ea_download"],
                },
            },
            "default_tier": "starter",
        }
        if os.path.exists(tiers_file):
            try:
                with open(tiers_file) as f:
                    loaded = json.load(f)
                tiers = loaded
            except Exception:
                pass
        self._tiers_cache = tiers
        self._tiers_cache_time = now
        return tiers

    # ── License Validation ──

    def validate_key(self, api_key: str) -> tuple[bool, Optional[dict]]:
        """
        Validate an API key against the license database.

        Returns (valid: bool, tier_info: dict | None)
        """
        if not api_key:
            return False, None

        now = time.time()
        ttl = self.config.cache_ttl

        if now - self._license_cache_time < ttl:
            cached = self._license_cache.get(api_key)
            if cached is not None:
                return cached["active"], cached["tier_info"]

        with self._db_lock:
            row = self._db_conn.execute(
                "SELECT tier, active, features FROM licenses WHERE api_key = ?",
                [api_key],
            ).fetchone()

        if not row or not row["active"]:
            self._license_cache[api_key] = {"active": False, "tier_info": None}
            return False, None

        try:
            features = json.loads(row["features"])
        except (json.JSONDecodeError, TypeError):
            features = []

        tiers = self._load_tiers()
        tier_name = row["tier"]
        starter = tiers.get("tiers", {}).get("starter", {"max_layers": 1, "features": []})
        tier_info = dict(tiers.get("tiers", {}).get(tier_name, starter))
        tier_info.setdefault("features", features)
        tier_info["tier_name"] = tier_name

        self._license_cache[api_key] = {"active": True, "tier_info": tier_info}
        return True, tier_info

    def bust_license_cache(self, api_key: Optional[str] = None) -> None:
        """Invalidate license cache entries."""
        if api_key:
            self._license_cache.pop(api_key, None)
        else:
            self._license_cache.clear()
            self._license_cache_time = 0

    def check_rate_limit(self, api_key: str) -> bool:
        """Check if a key is within its rate limit."""
        with self._rate_lock:
            tiers = self._load_tiers()
            keys_data = self._get_keys_from_db()
            key_data = keys_data.get(api_key, {})
            limit = key_data.get("rate_limit", 3)
            if limit == 0:
                return True
            window = key_data.get("rate_window_seconds", 86400)

            now = time.time()
            timestamps = self._rate_counters[api_key]
            timestamps[:] = [t for t in timestamps if now - t < window]
            if len(timestamps) >= limit:
                return False
            timestamps.append(now)
            return True

    def _get_keys_from_db(self) -> dict:
        """Load all active keys from SQLite."""
        with self._db_lock:
            rows = self._db_conn.execute(
                "SELECT api_key, tier, label, rate_limit, rate_window_seconds, "
                "expires, active, features FROM licenses"
            ).fetchall()
        keys = {}
        for r in rows:
            try:
                features = json.loads(r["features"])
            except (json.JSONDecodeError, TypeError):
                features = []
            keys[r["api_key"]] = {
                "tier": r["tier"],
                "label": r["label"],
                "rate_limit": r["rate_limit"],
                "rate_window_seconds": r["rate_window_seconds"],
                "expires": r["expires"],
                "active": bool(r["active"]),
                "features": features,
            }
        return keys

    # ── Admin Key Management ──

    def is_admin(self, secret: str) -> bool:
        """Check if a secret matches the admin secret."""
        return bool(self.config.admin_secret and secret == self.config.admin_secret)

    def generate_key(
        self, tier: str = "starter", label: str = "", days: int = 365
    ) -> str:
        """Generate a new API key and store in the license DB."""
        import secrets

        key = f"VT-{secrets.token_hex(8).upper()}"
        expires = time.strftime(
            "%Y-%m-%d",
            time.gmtime(time.time() + days * 86400),
        )
        with self._db_lock:
            self._db_conn.execute(
                "INSERT OR REPLACE INTO licenses VALUES (?,?,?,?,?,?,?,?)",
                [key, tier, label, 3, 86400, expires, 1, "[]"],
            )
            self._db_conn.commit()
        self.bust_license_cache(key)
        LOG.info("Generated key %s (tier=%s)", key, tier)
        return key

    def revoke_key(self, api_key: str) -> bool:
        """Revoke/deactivate an API key."""
        with self._db_lock:
            cur = self._db_conn.execute(
                "UPDATE licenses SET active = 0 WHERE api_key = ?", [api_key]
            )
            self._db_conn.commit()
            changed = cur.rowcount > 0
        if changed:
            self.bust_license_cache(api_key)
        return changed

    def list_keys(self) -> list[dict]:
        """List all license keys."""
        with self._db_lock:
            rows = self._db_conn.execute(
                "SELECT api_key, tier, label, active, expires FROM licenses"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Signal Queue ──

    def _gen_signal_id(self) -> str:
        self._id_counter += 1
        return f"vtfx_{int(time.time() * 1000)}_{self._id_counter}"

    def push_signal(
        self, signal: dict, target_key: Optional[str] = None, instance_id: Optional[str] = None
    ) -> str:
        """
        Push a signal into the queue.

        Returns the signal_id.
        """
        sig = dict(signal)
        if not sig.get("signal_id"):
            sig["signal_id"] = self._gen_signal_id()

        sig_hash = json.dumps(
            {k: sig.get(k) for k in ("symbol", "action", "entry", "sl", "tp")},
            sort_keys=True,
        )
        now = time.time()
        ttl = self.config.signal_dedup_ttl

        with self._signal_lock:
            cached_time = self._signal_dedup_cache.get(sig_hash)
            if cached_time and (now - cached_time) < ttl:
                LOG.debug("Signal dedup: %s", sig["signal_id"])
                return sig["signal_id"]
            self._signal_dedup_cache[sig_hash] = now

            self._history.append(sig)

            if instance_id:
                self._pending_by_instance[instance_id].append(sig)
            elif target_key:
                self._pending_by_key[target_key].append(sig)
            else:
                self._pending.append(sig)

        LOG.info("Signal queued: %s %s %s", sig["signal_id"], sig.get("symbol"), sig.get("action"))
        return sig["signal_id"]

    def poll_signal(
        self, api_key: str, account_id: Optional[str] = None
    ) -> SignalData:
        """
        Poll for the next pending signal.

        Priority: instance queue > key queue > global queue.
        """
        instance_id = f"{api_key}:{account_id}" if account_id else None

        with self._signal_lock:
            sig = None

            if instance_id and instance_id in self._pending_by_instance:
                if self._pending_by_instance[instance_id]:
                    sig = self._pending_by_instance[instance_id].popleft()

            if sig is None and api_key in self._pending_by_key:
                if self._pending_by_key[api_key]:
                    sig = self._pending_by_key[api_key].popleft()

            if sig is None and self._pending:
                sig = self._pending.popleft()

        if sig is None:
            return SignalData(pending=False)

        # Format for EA consumption
        return self._format_signal(sig, api_key)

    def _format_signal(self, sig: dict, api_key: str) -> SignalData:
        """Format raw signal for EA consumption, filtering by tier."""
        tiers = self._load_tiers()
        keys = self._get_keys_from_db()
        key_data = keys.get(api_key, {})
        tier_name = key_data.get("tier", "starter")
        tier_info = tiers.get("tiers", {}).get(tier_name, {"max_layers": 1, "features": []})
        max_layers = tier_info.get("max_layers", 1)

        layers = sig.get("layers", [])
        if layers and len(layers) > max_layers:
            layers = layers[:max_layers]

        return SignalData(
            signal_id=sig.get("signal_id", ""),
            symbol=sig.get("symbol", ""),
            action=sig.get("action", "HOLD"),
            entry=sig.get("entry", 0),
            sl=sig.get("sl", 0),
            tp=sig.get("tp", 0),
            tp1=sig.get("tp1", sig.get("tp", 0)),
            tp2=sig.get("tp2", 0),
            tp3=sig.get("tp3", 0),
            tp4=sig.get("tp4", 0),
            risk_percent=sig.get("risk_percent", 1.0),
            comment=sig.get("comment", "VTFX/AI"),
            confidence=sig.get("confidence", 0),
            layers=layers,
            layer_count=len(layers),
            tier=tier_name,
            pending=True,
        )

    # ── Daemon Registry ──

    def register_daemon(
        self, daemon_id: str, account_id: str, api_key: str, hostname: str = "",
        mt5_version: str = ""
    ) -> None:
        """Register an MT5 daemon instance."""
        self._daemons[daemon_id] = {
            "daemon_id": daemon_id,
            "account_id": account_id,
            "api_key": api_key,
            "last_seen": time.time(),
            "hostname": hostname,
            "mt5_version": mt5_version,
            "active_ticket": 0,
        }

    def get_daemons(self) -> list[dict]:
        """List all registered daemons."""
        return [
            {
                "daemon_id": d["daemon_id"],
                "account_id": d["account_id"],
                "last_seen": d["last_seen"],
                "hostname": d["hostname"],
            }
            for d in self._daemons.values()
        ]

    def push_sl_modification(self, daemon_id: str, ticket: int, new_sl: float) -> None:
        """Queue an SL modification for a daemon."""
        self._daemon_sl_queue[daemon_id].append({
            "ticket": ticket,
            "new_sl": new_sl,
            "timestamp": time.time(),
        })

    def poll_sl_modifications(self, daemon_id: str) -> list[dict]:
        """Get pending SL modifications for a daemon."""
        queue = self._daemon_sl_queue[daemon_id]
        items = list(queue)
        queue.clear()
        return items

    def report_trade_status(self, report: dict) -> None:
        """Record a trade status report from a daemon."""
        self._trade_reports.append(report)

    # ── Health ──

    def get_health(self) -> dict:
        """Return bridge health status."""
        return {
            "db_connected": self._db_conn is not None,
            "pending_signals": len(self._pending),
            "registered_daemons": len(self._daemons),
            "key_cache_entries": len(self._license_cache),
        }

    async def shutdown(self) -> None:
        """Close database connection and clean up."""
        try:
            if self._db_conn:
                self._db_conn.close()
                self._db_conn = None
            self._initialized = False
            LOG.info("SignalBridgeAdapter shutdown")
        except Exception as e:
            LOG.error("SignalBridgeAdapter shutdown error: %s", e)
