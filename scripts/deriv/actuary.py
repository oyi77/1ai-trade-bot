#!/usr/bin/env python3
"""
Deriv Actuary — Multi-Symbol Pattern Detection & Cognitive Learning
====================================================================

From Project Arbiter v4.0 (deriv-digit-match-bot):
  - MultiStreamActuary: process ticks across symbols, detect cold digits + adjacency
  - CognitiveDB: self-learning pattern optimization (threshold, win rate, blacklist)
  - Heatmap generation

Fixes:
  - Old ~/.openclaw/workspace/ → ~/projects/1ai-trade-bot/
"""

import json
import logging
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .client import DerivTick
from .config import (
    SYNTHETIC_INDICES, SYMBOL_LABELS, TICK_HISTORY,
    ANTI_FLOOD_WINDOW, ANTI_FLOOD_MAX, DEFAULT_MIN_THRESHOLD,
    PATTERN_BLACKLIST_HOURS, LATENCY_TRAP_MS, LATENCY_TRAP_LIMIT,
    MARKET_WIN_COOLDOWN_MIN, MARKET_LOSS_BLACKLIST_MIN,
    DAILY_TP, DAILY_SL, LOCK_TP_HOURS, LOCK_SL_HOURS,
    MAX_SHOTS, INITIAL_STAKE, PAYOUT_MULTIPLIER,
)

LOG = logging.getLogger("deriv.actuary")

# ── Paths (FIXED: old workspace → 1ai-trade-bot) ──
LOG_DIR = Path.home() / "projects" / "1ai-trade-bot" / "data" / "deriv"
COG_DB = LOG_DIR / "cognitive_memory.db"
STATE_DB = LOG_DIR / "actuary_state.db"


# ═══════════════════════════════════════════════════════════════════════
# COGNITIVE DB — Self-Learning Pattern Memory
# ═══════════════════════════════════════════════════════════════════════

class CognitiveDB:
    """Self-learning database for pattern optimization.

    Features:
    - Records win/loss per pattern → adjusts threshold automatically
    - Blacklists patterns with WR < 15% for 24hr
    - Tracks consecutive losses per market
    - Win cooldowns + loss blacklists
    - Latency trap detection → auto-shifts to Tick+2
    """

    DB_PATH = COG_DB

    @classmethod
    def init_db(cls):
        """Create cognitive_memory + market_state + latency_traps tables."""
        cls.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(cls.DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cognitive_memory (
                market TEXT NOT NULL,
                pattern_string TEXT NOT NULL,
                total_attempts INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,
                min_threshold INTEGER DEFAULT 3,
                latency_offset INTEGER DEFAULT 0,
                cooldown_until TEXT,
                blacklisted_until TEXT,
                consecutive_losses INTEGER DEFAULT 0,
                last_updated TEXT,
                PRIMARY KEY (market, pattern_string)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_state (
                market TEXT PRIMARY KEY,
                win_cooldown_until TEXT,
                loss_blacklist_until TEXT,
                consecutive_losses INTEGER DEFAULT 0,
                latency_trap_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS latency_traps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                trigger_tick_time TEXT,
                executed_tick_time TEXT,
                latency_ms INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_counters (
                date TEXT PRIMARY KEY,
                profit REAL DEFAULT 0.0,
                trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                last_updated TEXT
            )
        """)
        conn.commit()
        conn.close()
        LOG.info("🧠 Cognitive DB ready: %s", cls.DB_PATH)

    @staticmethod
    def conn():
        return sqlite3.connect(str(CognitiveDB.DB_PATH))

    @staticmethod
    def record_pattern_result(market: str, pattern_str: str, won: bool):
        """Update win/loss for a pattern. Auto-adjust threshold based on WR."""
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT * FROM cognitive_memory WHERE market=? AND pattern_string=?",
                (market, pattern_str),
            ).fetchone()
            if row:
                total = row[2] + 1
                wins = row[3] + (1 if won else 0)
                wr = wins / total
                threshold = row[5]
                blacklisted = row[8]

                if total >= 5 and wr < 0.30 and threshold < 4:
                    threshold = min(threshold + 1, 8)
                    LOG.info("🧠 [%s] %s WR=%.0f%% < 30%% → threshold=%d",
                             market, pattern_str, wr * 100, threshold)
                if total >= 5 and wr < 0.15 and not blacklisted:
                    blacklisted = (datetime.now(timezone.utc) +
                                   timedelta(hours=PATTERN_BLACKLIST_HOURS)).isoformat()
                    LOG.info("🧠 [%s] %s WR=%.0f%% < 15%% → BLACKLISTED %dh",
                             market, pattern_str, wr * 100, PATTERN_BLACKLIST_HOURS)
                if wr > 0.45 and threshold > 3:
                    threshold = 3
                    LOG.info("🧠 [%s] %s WR=%.0f%% > 45%% → threshold reset to 3",
                             market, pattern_str, wr * 100)

                conn.execute("""
                    UPDATE cognitive_memory SET total_attempts=?, wins=?, win_rate=?,
                    min_threshold=?, blacklisted_until=?, last_updated=?
                    WHERE market=? AND pattern_string=?
                """, (total, wins, round(wr, 3), threshold, blacklisted, now, market, pattern_str))
            else:
                wr = 1.0 if won else 0.0
                conn.execute("""
                    INSERT INTO cognitive_memory
                    (market, pattern_string, total_attempts, wins, win_rate, min_threshold, last_updated)
                    VALUES (?,?,1,?,?,3,?)
                """, (market, pattern_str, 1 if won else 0, wr, now))
            conn.commit()

    @staticmethod
    def should_lock_pattern(market: str, pattern_str: str, freq: int) -> bool:
        """Check if pattern meets learned threshold and isn't blacklisted."""
        now_dt = datetime.now(timezone.utc)
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT min_threshold, blacklisted_until FROM cognitive_memory WHERE market=? AND pattern_string=?",
                (market, pattern_str),
            ).fetchone()
            if row:
                threshold = row[0]
                blacklisted = row[1]
                if blacklisted:
                    try:
                        if now_dt < datetime.fromisoformat(blacklisted):
                            return False
                    except ValueError:
                        pass
                return freq >= threshold
            return freq >= DEFAULT_MIN_THRESHOLD

    @staticmethod
    def record_market_result(market: str, won: bool):
        """Track consecutive wins/losses per market for cooldowns."""
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT * FROM market_state WHERE market=?", (market,)
            ).fetchone()
            if not row:
                conn.execute("INSERT INTO market_state VALUES (?,NULL,NULL,0,0)", (market,))
                conn.commit()
                return

            if won:
                cd = (datetime.now(timezone.utc) + timedelta(minutes=MARKET_WIN_COOLDOWN_MIN)).isoformat()
                conn.execute("UPDATE market_state SET win_cooldown_until=?, consecutive_losses=0 WHERE market=?",
                             (cd, market))
            else:
                cons_losses = row[3] + 1
                if cons_losses >= 2:
                    bl = (datetime.now(timezone.utc) + timedelta(minutes=MARKET_LOSS_BLACKLIST_MIN)).isoformat()
                    conn.execute("UPDATE market_state SET loss_blacklist_until=?, consecutive_losses=? WHERE market=?",
                                 (bl, cons_losses, market))
                else:
                    conn.execute("UPDATE market_state SET consecutive_losses=? WHERE market=?",
                                 (cons_losses, market))
            conn.commit()

    @staticmethod
    def is_market_cooled(market: str) -> bool:
        """Check if market is in cooldown (return True if cooled)."""
        now_dt = datetime.now(timezone.utc)
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT win_cooldown_until, loss_blacklist_until FROM market_state WHERE market=?", (market,)
            ).fetchone()
            if row:
                for val in [row[0], row[1]]:
                    if val:
                        try:
                            if now_dt < datetime.fromisoformat(val):
                                return False
                        except ValueError:
                            pass
            return True

    @staticmethod
    def get_daily_counter(date: str = None) -> dict:
        """Get daily P&L counters from cognitive_memory.db.

        Returns:
            dict with keys: profit, trades, wins, losses
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT profit, trades, wins, losses FROM daily_counters WHERE date=?",
                (date,)
            ).fetchone()
            if row:
                return {
                    "profit": row[0],
                    "trades": row[1],
                    "wins": row[2],
                    "losses": row[3],
                }
            return {"profit": 0.0, "trades": 0, "wins": 0, "losses": 0}

    @staticmethod
    def update_daily_counter(profit_delta: float, won: bool, date: str = None):
        """Record a trade result in the daily counter.

        Args:
            profit_delta: P&L change from this trade (signed float).
            won: True if trade was a win.
            date: YYYY-MM-DD string (defaults to UTC today).
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT profit, trades, wins, losses FROM daily_counters WHERE date=?",
                (date,)
            ).fetchone()
            if row:
                pnl = round(row[0] + profit_delta, 2)
                trades = row[1] + 1
                wins = row[2] + (1 if won else 0)
                losses = row[3] + (0 if won else 1)
                conn.execute(
                    "UPDATE daily_counters SET profit=?, trades=?, wins=?, losses=?, last_updated=? WHERE date=?",
                    (pnl, trades, wins, losses, now, date)
                )
            else:
                conn.execute(
                    "INSERT INTO daily_counters (date, profit, trades, wins, losses, last_updated) VALUES (?,?,?,?,?,?)",
                    (date, round(profit_delta, 2), 1, 1 if won else 0, 0 if won else 1, now)
                )
            conn.commit()

    @staticmethod
    def reset_daily_counter(date: str = None):
        """Zero out the daily counter for a given date.

        Args:
            date: YYYY-MM-DD string (defaults to UTC today).
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_counters (date, profit, trades, wins, losses, last_updated) VALUES (?,0,0,0,0,?)",
                (date, now)
            )
            conn.commit()

    @staticmethod
    def record_latency_trap(market: str, trigger_time, exec_time) -> bool:
        """Log latency trap. Returns True if should shift to Tick+2."""
        latency_ms = (exec_time - trigger_time).total_seconds() * 1000
        now = datetime.now(timezone.utc).isoformat()
        with CognitiveDB.conn() as conn:
            conn.execute(
                "INSERT INTO latency_traps (market, timestamp, trigger_tick_time, executed_tick_time, latency_ms) VALUES (?,?,?,?,?)",
                (market, now,
                 trigger_time.isoformat() if hasattr(trigger_time, 'isoformat') else str(trigger_time),
                 exec_time.isoformat() if hasattr(exec_time, 'isoformat') else str(exec_time),
                 round(latency_ms, 1)),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM latency_traps WHERE market=? AND latency_ms < ?",
                (market, LATENCY_TRAP_MS),
            ).fetchone()[0]
            if count >= LATENCY_TRAP_LIMIT:
                conn.execute("UPDATE market_state SET latency_trap_count=? WHERE market=?",
                             (count, market))
                LOG.info("🧠 [%s] %d latency traps → auto-shift to Tick+2", market, count)
                conn.commit()
                return True
            conn.commit()
        return False


# ═══════════════════════════════════════════════════════════════════════
# MULTI-STREAM ACTUARY — Multi-Symbol Pattern Detection
# ═══════════════════════════════════════════════════════════════════════

class MultiStreamActuary:
    """Multi-symbol pattern detection engine.

    Monitors ALL volatility indices simultaneously.
    Track consecutive digit pairs, detect cold digits + adjacency patterns.
    """

    def __init__(self, symbols: list[str] = None, tick_history: int = TICK_HISTORY):
        self.symbols = symbols or list(SYNTHETIC_INDICES)
        self.tick_history = tick_history

        # Per-symbol rolling windows
        self.ticks: dict[str, deque] = {
            s: deque(maxlen=tick_history) for s in self.symbols
        }
        self.last_print: dict[str, float] = {}

        # Cold digit tracking (global)
        self.digit_heatmap: dict[str, list[int]] = defaultdict(lambda: [0] * 10)
        self.total_ticks_per_symbol: dict[str, int] = defaultdict(int)

    def add_symbol(self, symbol: str):
        """Add a new symbol to track for pattern detection.

        Creates a fresh tick deque and heatmap entry.
        Safe to call if symbol already tracked (no-op).
        """
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.ticks[symbol] = deque(maxlen=self.tick_history)
            self.digit_heatmap[symbol] = [0] * 10
            self.last_print[symbol] = 0.0
            LOG.info("📡 MultiStreamActuary added symbol: %s", symbol)

    def remove_symbol(self, symbol: str):
        """Remove a symbol from tracking. Cleans up all per-symbol state."""
        if symbol in self.symbols:
            self.symbols.remove(symbol)
            self.ticks.pop(symbol, None)
            self.digit_heatmap.pop(symbol, None)
            self.total_ticks_per_symbol.pop(symbol, None)
            self.last_print.pop(symbol, None)
            LOG.info("📡 MultiStreamActuary removed symbol: %s", symbol)

    def get_ticks(self, symbol: str) -> list:
        """Return list of buffered ticks for a symbol, oldest first.

        Returns empty list if symbol is not tracked.
        """
        return list(self.ticks.get(symbol, deque()))

    def process_tick(self, symbol: str, quote: float, epoch: int) -> dict:
        """Process an incoming tick and return action dict.

        Returns:
            dict with keys:
              - action: "none" | "anomaly" | "trade_signal" | "entry"
              - cold: the cold digit (if anomaly)
              - entry: dict of trade entry info (if entry)
              - lock: reason string (if locked)
        """
        tick = DerivTick(symbol=symbol, price=float(quote), epoch=int(epoch))
        digit = tick.digit
        self.ticks[symbol].append(tick)
        self.total_ticks_per_symbol[symbol] += 1

        # Update heatmap
        self.digit_heatmap[symbol][digit] += 1
        self.digit_heatmap[symbol] = [
            self.digit_heatmap[symbol][d]
            for d in range(10)
        ]  # Ensure 0-9

        # Don't analyze until we have enough ticks
        if len(self.ticks[symbol]) < 20:
            return {"action": "none"}

        # Check cold digit (statistical anomaly)
        cold_result = self._detect_cold(symbol)
        if cold_result and cold_result.get("cold") is not None:
            return cold_result

        return {"action": "none"}

    def _detect_cold(self, symbol: str) -> Optional[dict]:
        """Detect if a digit is abnormally cold (hasn't appeared recently)."""
        ticks_list = list(self.ticks[symbol])
        recent = ticks_list[-20:]
        recent_digits = [t.digit for t in recent]

        # Which digits are missing entirely from recent ticks?
        missing = [d for d in range(10) if d not in recent_digits]
        if not missing:
            return None

        # Check anti-flood: target should not be overrepresented
        target = missing[0]  # First missing digit
        window = [t.digit for t in ticks_list[-ANTI_FLOOD_WINDOW - 10:]]
        if window.count(target) > ANTI_FLOOD_MAX:
            return None

        return {
            "action": "anomaly",
            "cold": target,
            "symbol": symbol,
            "label": SYMBOL_LABELS.get(symbol, symbol),
        }

    def get_digit_frequencies(self, symbol: str) -> list[int]:
        """Get digit frequency counts for a symbol (0-9)."""
        return self.digit_heatmap.get(symbol, [0] * 10)

    def get_global_heatmap(self) -> dict:
        """Generate global digit heatmap across all symbols."""
        hm = {}
        for symbol in self.symbols:
            hm[symbol] = {
                "freq": self.digit_heatmap.get(symbol, [0] * 10),
                "total": self.total_ticks_per_symbol.get(symbol, 0),
                "label": SYMBOL_LABELS.get(symbol, symbol),
            }
        return hm


# ── Init on import ──
CognitiveDB.init_db()
