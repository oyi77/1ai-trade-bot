"""
Generic SQLite storage — simple key-value and table-based persistence.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tradebot.config import settings

LOG = logging.getLogger(__name__)


class SQLiteStorage:
    """Generic SQLite storage wrapper with simple table management."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or Path(settings.DATA_DIR) / "tradebot.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.conn() as c:
            return c.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        with self.conn() as c:
            return c.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self.conn() as c:
            return c.execute(sql, params).fetchall()

    def table_exists(self, table_name: str) -> bool:
        row = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

    def create_table(self, table_name: str, schema: str):
        """Create a table if it doesn't exist.

        Args:
            table_name: Name of the table.
            schema: Full CREATE TABLE statement (without CREATE TABLE).
        """
        self.execute(f"CREATE TABLE IF NOT EXISTS {table_name} {schema}")

    def insert(self, table: str, data: dict) -> int:
        """Insert a row and return its rowid."""
        keys = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        values = tuple(data.values())
        with self.conn() as c:
            c.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", values)
            return c.lastrowid

    # ── System State (from scripts/deriv/persistence.py) ──────────

    def ensure_state_tables(self):
        """Create system_state, sequences, and shots tables if missing."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.execute("""
            CREATE TABLE IF NOT EXISTS sequences (
                sequence_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                target_digit INTEGER NOT NULL,
                max_shots INTEGER DEFAULT 8,
                status TEXT DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self.execute("""
            CREATE TABLE IF NOT EXISTS shots (
                shot_id TEXT PRIMARY KEY,
                sequence_id TEXT NOT NULL,
                shot_number INTEGER NOT NULL,
                contract_id TEXT,
                predicted_digit INTEGER NOT NULL,
                actual_digit INTEGER,
                status TEXT DEFAULT 'PENDING',
                pnl REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY (sequence_id) REFERENCES sequences(sequence_id)
            )
        """)

    def set_system_state(self, key: str, value: str):
        """Set a system state key-value pair (upsert)."""
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO system_state VALUES (?,?)", (key, value))
            c.commit()

    def get_system_state(self, key: str, default: str = "") -> str:
        """Get a system state value by key."""
        row = self.fetchone("SELECT value FROM system_state WHERE key=?", (key,))
        return row[0] if row else default

    # ── Sequence Tracking (from scripts/deriv/persistence.py) ─────

    def save_sequence_start(self, seq_id: str, symbol: str, digit: int, max_shots: int = 8):
        """Record the start of a new trading sequence."""
        now = datetime.now(UTC).isoformat()
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO sequences VALUES (?,?,?,?,?,?,?)",
                (seq_id, symbol, digit, max_shots, "ACTIVE", now, now),
            )
            c.commit()

    def save_shot(self, seq_id: str, shot_num: int, shot_id: str, digit: int, contract_id: str = ""):  # noqa: E501
        """Record a new shot within a sequence."""
        now = datetime.now(UTC).isoformat()
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO shots VALUES (?,?,?,?,?,?,?,?,?,?)",
                (shot_id, seq_id, shot_num, contract_id, digit, None, "PENDING", 0.0, now, None),
            )
            c.execute("UPDATE sequences SET updated_at=? WHERE sequence_id=?", (now, seq_id))
            c.commit()

    def resolve_shot(self, shot_id: str, actual_digit: int, status: str, pnl: float):
        """Mark a shot as resolved with actual digit, status, and P&L."""
        now = datetime.now(UTC).isoformat()
        with self.conn() as c:
            c.execute(
                "UPDATE shots SET actual_digit=?, status=?, pnl=?, resolved_at=? WHERE shot_id=?",
                (actual_digit, status, pnl, now, shot_id),
            )
            c.commit()

    def resolve_sequence(self, seq_id: str, status: str):
        """Mark a sequence as resolved with final status."""
        now = datetime.now(UTC).isoformat()
        with self.conn() as c:
            c.execute("UPDATE sequences SET status=?, updated_at=? WHERE sequence_id=?",
                      (status, now, seq_id))
            c.commit()

    def get_last_unconfirmed_shot(self) -> dict | None:
        """Get the most recent unconfirmed (PENDING) shot."""
        row = self.fetchone("""
            SELECT s.*, seq.symbol, seq.target_digit FROM shots s
            JOIN sequences seq ON s.sequence_id = seq.sequence_id
            WHERE s.status='PENDING' ORDER BY s.created_at DESC LIMIT 1
        """)
        if row:
            return {
                "shot_id": row[0], "sequence_id": row[1], "shot_number": row[2],
                "contract_id": row[3], "predicted_digit": row[4], "actual_digit": row[5],
                "status": row[6], "pnl": row[7], "created_at": row[8],
                "symbol": row[11], "target_digit": row[12],
            }
        return None

    def get_all_active_sequences(self) -> list[dict]:
        """Get all currently active trading sequences."""
        rows = self.fetchall("SELECT * FROM sequences WHERE status='ACTIVE'")
        return [{"id": r[0], "symbol": r[1], "digit": r[2], "shots": r[3]} for r in rows]
