"""
CognitiveDB — Self-Learning Pattern Memory

From Project Arbiter v4.0:
Records win/loss per pattern → adjusts threshold automatically.
Blacklists patterns with WR < 15% for 24hr.
Tracks consecutive losses per market.
Win cooldowns + loss blacklists.
Latency trap detection → auto-shifts to Tick+2.

Copied and cleaned from scripts/deriv/actuary.py CognitiveDB class.
"""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradebot.config import settings

LOG = logging.getLogger("tradebot.storage.cognitive")

COG_DB = Path(settings.DATA_DIR) / "deriv" / "cognitive_memory.db"


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
        now = datetime.now(UTC).isoformat()
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
                    blacklisted = (datetime.now(UTC) +
                                   timedelta(hours=24)).isoformat()
                    LOG.info("🧠 [%s] %s WR=%.0f%% < 15%% → BLACKLISTED 24h",
                             market, pattern_str, wr * 100)
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
                    (market, pattern_string, total_attempts,
                     wins, win_rate, min_threshold, last_updated)
                    VALUES (?,?,1,?,?,3,?)
                """, (market, pattern_str, 1 if won else 0, wr, now))
            conn.commit()

    @staticmethod
    def should_lock_pattern(market: str, pattern_str: str, freq: int) -> bool:
        """Check if pattern meets learned threshold and isn't blacklisted."""
        now_dt = datetime.now(UTC)
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT min_threshold, blacklisted_until FROM cognitive_memory WHERE market=? AND pattern_string=?",  # noqa: E501
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
            return freq >= 3

    @staticmethod
    def record_market_result(market: str, won: bool):
        """Track consecutive wins/losses per market for cooldowns."""
        _now = datetime.now(UTC).isoformat()
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT * FROM market_state WHERE market=?", (market,)
            ).fetchone()
            if not row:
                conn.execute("INSERT INTO market_state VALUES (?,NULL,NULL,0,0)", (market,))
                conn.commit()
                return

            if won:
                cd = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
                conn.execute("UPDATE market_state SET win_cooldown_until=?, consecutive_losses=0 WHERE market=?",  # noqa: E501
                             (cd, market))
            else:
                cons_losses = row[3] + 1
                if cons_losses >= 2:
                    bl = (datetime.now(UTC) + timedelta(minutes=60)).isoformat()
                    conn.execute("UPDATE market_state SET loss_blacklist_until=?, consecutive_losses=? WHERE market=?",  # noqa: E501
                                 (bl, cons_losses, market))
                else:
                    conn.execute("UPDATE market_state SET consecutive_losses=? WHERE market=?",
                                 (cons_losses, market))
            conn.commit()

    @staticmethod
    def is_market_cooled(market: str) -> bool:
        """Check if market is in cooldown (return True if cooled)."""
        now_dt = datetime.now(UTC)
        with CognitiveDB.conn() as conn:
            row = conn.execute(
                "SELECT win_cooldown_until, loss_blacklist_until FROM market_state WHERE market=?", (market,)  # noqa: E501
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
        """Get daily P&L counters from cognitive_memory.db."""
        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
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
        """Record a trade result in the daily counter."""
        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        now = datetime.now(UTC).isoformat()
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
                    "UPDATE daily_counters SET profit=?, trades=?, wins=?, losses=?, last_updated=? WHERE date=?",  # noqa: E501
                    (pnl, trades, wins, losses, now, date)
                )
            else:
                conn.execute(
                    "INSERT INTO daily_counters (date, profit, trades, wins, losses, last_updated) VALUES (?,?,?,?,?,?)",  # noqa: E501
                    (date, round(profit_delta, 2), 1, 1 if won else 0, 0 if won else 1, now)
                )
            conn.commit()

    @staticmethod
    def reset_daily_counter(date: str = None):
        """Zero out the daily counter for a given date."""
        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        now = datetime.now(UTC).isoformat()
        with CognitiveDB.conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_counters (date, profit, trades, wins, losses, last_updated) VALUES (?,0,0,0,0,?)",  # noqa: E501
                (date, now)
            )
            conn.commit()

    @staticmethod
    def record_latency_trap(market: str, trigger_time, exec_time) -> bool:
        """Log latency trap. Returns True if should shift to Tick+2."""
        latency_ms = (exec_time - trigger_time).total_seconds() * 1000
        now = datetime.now(UTC).isoformat()
        with CognitiveDB.conn() as conn:
            conn.execute(
                "INSERT INTO latency_traps (market, timestamp, trigger_tick_time, executed_tick_time, latency_ms) VALUES (?,?,?,?,?)",  # noqa: E501
                (market, now,
                 trigger_time.isoformat() if hasattr(trigger_time, 'isoformat') else str(trigger_time),  # noqa: E501
                 exec_time.isoformat() if hasattr(exec_time, 'isoformat') else str(exec_time),
                 round(latency_ms, 1)),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM latency_traps WHERE market=? AND latency_ms < ?",
                (market, 350),
            ).fetchone()[0]
            if count >= 2:
                conn.execute("UPDATE market_state SET latency_trap_count=? WHERE market=?",
                             (count, market))
                LOG.info("🧠 [%s] %d latency traps → auto-shift to Tick+2", market, count)
                conn.commit()
                return True
            conn.commit()
        return False


# ── Init on import ──
CognitiveDB.init_db()
