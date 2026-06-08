#!/usr/bin/env python3
"""
Deriv Persistence — SQLite State, Paper Mode & Reconciliation
=============================================================

From Project Arbiter (deriv-digit-match-bot/tick_streamer.py):
  - SQLite state persistence for sequence/shot tracking
  - Paper mode for virtual trading
  - Daily TP/SL limits with DB-persistent lock
  - Reconciliation protocol for reconnects
  - Momentum timeout (15s)
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DAILY_TP, DAILY_SL, INITIAL_STAKE
from .client import DerivWSClient, DerivTick

LOG = logging.getLogger("deriv.persistence")

# Paths
STATE_DIR = Path.home() / "projects" / "1ai-trade-bot" / "data" / "deriv"
STATE_DB = STATE_DIR / "actuary_state.db"
PAPER_BALANCE = 100.0

# Momentum timeout for reconciliation
MOMENTUM_TIMEOUT_SECONDS = 15


# ═══════════════════════════════════════════════════════════════════════
# DATABASE INIT
# ═══════════════════════════════════════════════════════════════════════

def init_db():
    """Create all persistence tables."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            trigger_digit INTEGER,
            target_digit INTEGER,
            actual_digit INTEGER,
            shot_number INTEGER,
            virtual_pnl REAL,
            win INTEGER,
            balance_after REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
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
    conn.execute("""
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()
    LOG.info("🗄️  Persistence DB ready: %s", STATE_DB)


# ═══════════════════════════════════════════════════════════════════════
# DB HELPERS
# ═══════════════════════════════════════════════════════════════════════

def db_conn():
    return sqlite3.connect(str(STATE_DB))


def set_system_state(key: str, value: str):
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO system_state VALUES (?,?)", (key, value))
        conn.commit()


def get_system_state(key: str, default: str = "") -> str:
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


# ── Sequence Tracking ──

def save_sequence_start(seq_id: str, symbol: str, digit: int):
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sequences VALUES (?,?,?,?,?,?,?)",
            (seq_id, symbol, digit, 8, "ACTIVE", now, now),
        )
        conn.commit()


def save_shot(seq_id: str, shot_num: int, shot_id: str, digit: int, contract_id: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO shots VALUES (?,?,?,?,?,?,?,?,?,?)",
            (shot_id, seq_id, shot_num, contract_id, digit, None, "PENDING", 0.0, now, None),
        )
        conn.execute("UPDATE sequences SET updated_at=? WHERE sequence_id=?", (now, seq_id))
        conn.commit()


def resolve_shot(shot_id: str, actual_digit: int, status: str, pnl: float):
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        conn.execute(
            "UPDATE shots SET actual_digit=?, status=?, pnl=?, resolved_at=? WHERE shot_id=?",
            (actual_digit, status, pnl, now, shot_id),
        )
        conn.commit()


def resolve_sequence(seq_id: str, status: str):
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        conn.execute("UPDATE sequences SET status=?, updated_at=? WHERE sequence_id=?",
                     (status, now, seq_id))
        conn.commit()


def get_last_unconfirmed_shot() -> Optional[dict]:
    with db_conn() as conn:
        row = conn.execute("""
            SELECT s.*, seq.symbol, seq.target_digit FROM shots s
            JOIN sequences seq ON s.sequence_id = seq.sequence_id
            WHERE s.status='PENDING' ORDER BY s.created_at DESC LIMIT 1
        """).fetchone()
        if row:
            return {
                "shot_id": row[0], "sequence_id": row[1], "shot_number": row[2],
                "contract_id": row[3], "predicted_digit": row[4], "actual_digit": row[5],
                "status": row[6], "pnl": row[7], "created_at": row[8],
                "symbol": row[11], "target_digit": row[12],
            }
    return None


def get_all_active_sequences() -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM sequences WHERE status='ACTIVE'").fetchall()
        return [{"id": r[0], "symbol": r[1], "digit": r[2], "shots": r[3]} for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# DAILY PNL LIMITS
# ═══════════════════════════════════════════════════════════════════════

def is_locked() -> bool:
    return get_system_state("daily_lock_reason", "") != ""


def get_lock_reason() -> str:
    return get_system_state("daily_lock_reason", "")


def set_lock(reason: str):
    set_system_state("daily_lock_reason", reason)
    LOG.info("🔒 SYSTEM LOCKED: %s", reason)


def check_daily_limits(balance: float) -> bool:
    """Check TP/SL and set lock if triggered. Returns True if locked."""
    open_bal = float(get_system_state("session_open_balance", str(balance)))
    if open_bal == 0:
        set_system_state("session_open_balance", str(balance))
        set_system_state("session_last_balance", str(balance))
        return False

    if is_locked():
        return True

    pnl = balance - open_bal
    if pnl >= DAILY_TP:
        set_lock(f"TP ${DAILY_TP:.0f} reached ($+{pnl:.2f})")
        return True
    if pnl <= DAILY_SL:
        set_lock(f"SL ${abs(DAILY_SL):.0f} reached (${pnl:.2f})")
        return True

    set_system_state("session_last_balance", str(balance))
    return False


# ═══════════════════════════════════════════════════════════════════════
# PAPER MODE
# ═══════════════════════════════════════════════════════════════════════

PAPER_MODE = False  # Set True for virtual trades

async def execute_paper_trade(symbol: str, digit: int, label: str,
                               seq_id: str, shot_num: int, ws) -> Optional[dict]:
    """Execute paper (virtual) DIGITMATCH trade. Uses next live tick to settle."""
    from deriv.config import INITIAL_STAKE
    global PAPER_BALANCE

    LOG.info("📄 [%s] [PAPER] Virtual shot #%d for digit %d | Bal: $%.2f",
             label, shot_num, digit, PAPER_BALANCE)

    # Read next tick from live stream to settle
    import asyncio
    import json
    next_digit = -1
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(raw)
        t = msg.get("tick")
        if t and t.get("symbol") == symbol:
            quote = float(t.get("quote", 0))
            next_digit = int(f"{quote:.4f}".replace(".", "")[-1])
        else:
            raw2 = await asyncio.wait_for(ws.recv(), timeout=5)
            msg2 = json.loads(raw2)
            t2 = msg2.get("tick", {})
            quote2 = float(t2.get("quote", 0))
            next_digit = int(f"{quote2:.4f}".replace(".", "")[-1])
    except:
        LOG.warning("[%s] [PAPER] Timeout waiting for settlement tick", label)

    win = next_digit == digit
    pnl = 7.33 if win else -float(INITIAL_STAKE)
    global BALANCE
    PAPER_BALANCE += pnl
    res = "🟢WIN" if win else "🔴LOST"
    LOG.info("💰 [%s] [PAPER] %s | Pred %d → Act %d | $%+.2f | VBal: $%.2f",
             label, res, digit, next_digit, pnl, PAPER_BALANCE)

    # Save to DB
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO paper_trades (timestamp, symbol, trigger_digit, target_digit, actual_digit, shot_number, virtual_pnl, win, balance_after) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), symbol, 0, digit, next_digit,
             shot_num, round(pnl, 2), 1 if win else 0, round(PAPER_BALANCE, 2)),
        )
        conn.commit()

    check_daily_limits(PAPER_BALANCE)
    return {"win": win, "pnl": pnl, "actual": next_digit}


# ═══════════════════════════════════════════════════════════════════════
# RECONCILIATION PROTOCOL
# ═══════════════════════════════════════════════════════════════════════

async def reconcile_on_reconnect(client: DerivWSClient, token: str = "") -> bool:
    """After WS reconnect, check last shot status before resuming."""
    LOG.info("🔄 Running reconciliation protocol...")
    last_shot = get_last_unconfirmed_shot()
    if not last_shot:
        LOG.info("  ✅ No unconfirmed shots — clean slate")
        return True

    import asyncio, json, ssl
    from deriv.config import WS_LEGACY as WS

    shot_time_str = last_shot["created_at"]
    try:
        shot_time = datetime.fromisoformat(shot_time_str)
    except:
        shot_time = datetime.now(timezone.utc)
    delta = (datetime.now(timezone.utc) - shot_time).total_seconds()

    LOG.info("  📋 Last shot: %s (seq %s)", last_shot["shot_id"], last_shot["sequence_id"])
    LOG.info("  ⏱️  Time since last shot: %.1fs", delta)

    if delta > MOMENTUM_TIMEOUT_SECONDS:
        LOG.warning("  ⏰ MOMENTUM TIMEOUT: %.0fs > %ds", delta, MOMENTUM_TIMEOUT_SECONDS)
        resolve_sequence(last_shot["sequence_id"], "HALTED_DUE_TO_TIMEOUT")
        resolve_shot(last_shot["shot_id"], -1, "HALTED_TIMEOUT", -1.0)
        return False

    # Check real transaction status via Deriv API
    try:
        import ssl
        import websockets
        async with websockets.connect(WS, ssl=ssl.create_default_context(), ping_interval=20) as ws:
            await ws.send(json.dumps({"authorize": token}))
            auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if auth.get("error"):
                LOG.warning("  ⚠️ Recon auth: %s", auth["error"]["message"])
                return True

            cid = last_shot.get("contract_id", "")
            if cid:
                await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
                for _ in range(10):
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    poc = json.loads(raw).get("proposal_open_contract", {})
                    if poc.get("contract_id") == cid and poc.get("status") in ("won", "lost"):
                        status = poc["status"]
                        profit = float(poc.get("profit", 0))
                        exit_t = poc.get("exit_tick", 0)
                        actual = int(str(exit_t).replace(".", "")[-1]) if exit_t else 0
                        result_str = "🟢WIN" if status == "won" else "🔴LOST"
                        LOG.info("  %s — Last shot resolved as %s | PnL $%.2f",
                                 result_str, status, profit)
                        resolve_shot(last_shot["shot_id"], actual, status.upper(), profit)
                        if status == "won":
                            resolve_sequence(last_shot["sequence_id"], "WON")
                            LOG.info("  🏆 Sequence %s WON — halting", last_shot["sequence_id"])
                            return False
                        else:
                            LOG.info("  💀 Shot lost — sequence continues")
                            return True
    except Exception as e:
        LOG.warning("  ⚠️ Recon check failed: %s", e)
    return True  # Safe default: resume


# ── Init DB on import ──
init_db()
