"""
Persistence — SQLite database for whitelabel configs, trades, and user data.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tables:
- whitelabels: feature flags per instance (ALL, FOREX, STOCKITY, DERIV, CRYPTO)
- trades: full trade lifecycle tracking
- users: per-user configuration
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

LOG = logging.getLogger("agent.db")

DB_PATH = Path("/home/openclaw/projects/1ai-trade-bot/data/agent.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = Lock()

WIB = timezone(timedelta(hours=7))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _lock:
        conn = _conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whitelabels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                bot_token TEXT NOT NULL DEFAULT '',
                features TEXT NOT NULL DEFAULT 'ALL',
                is_active BOOLEAN DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whitelabel_id INTEGER DEFAULT 0,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL DEFAULT 0,
                take_profit_1 REAL DEFAULT 0,
                take_profit_2 REAL DEFAULT 0,
                take_profit_3 REAL DEFAULT 0,
                quantity REAL DEFAULT 1.0,
                status TEXT NOT NULL DEFAULT 'OPEN',
                open_time TEXT NOT NULL,
                close_time TEXT,
                close_price REAL,
                pips REAL DEFAULT 0,
                profit_usd REAL DEFAULT 0,
                outcome TEXT DEFAULT '',
                leverage INTEGER DEFAULT 1,
                signal_source TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_trades_whitelabel ON trades(whitelabel_id);

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL UNIQUE,
                username TEXT DEFAULT '',
                whitelabel_id INTEGER DEFAULT 0,
                features TEXT DEFAULT 'ALL',
                risk_percent REAL DEFAULT 2.0,
                max_leverage INTEGER DEFAULT 10,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id);
        """)
        conn.commit()
        conn.close()
        LOG.info("Database initialized: %s", DB_PATH)


# ── Whitelabel Feature Flags ───────────────────────────────────────

FEATURE_ALL = "ALL"
FEATURE_FOREX = "FOREX"
FEATURE_STOCKITY = "STOCKITY"
FEATURE_DERIV = "DERIV"
FEATURE_CRYPTO = "CRYPTO"

ALL_FEATURES = {FEATURE_ALL, FEATURE_FOREX, FEATURE_STOCKITY, FEATURE_DERIV, FEATURE_CRYPTO}


def parse_features(features_str: str) -> set[str]:
    """Parse feature string into a set of active flags."""
    if not features_str:
        return {FEATURE_ALL}
    parts = {f.strip().upper() for f in features_str.split(",") if f.strip()}
    if FEATURE_ALL in parts:
        return ALL_FEATURES
    return parts & ALL_FEATURES


def create_whitelabel(name: str, bot_token: str = "", features: str = "ALL") -> dict[str, Any]:
    with _lock:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT INTO whitelabels (name, bot_token, features) VALUES (?, ?, ?)",
                (name, bot_token, features),
            )
            conn.commit()
            return {"id": cur.lastrowid, "name": name, "features": features, "is_active": True}
        except sqlite3.IntegrityError:
            conn.close()
            return {"error": f"Whitelabel '{name}' already exists"}
        finally:
            conn.close()


def get_whitelabel(name: str) -> dict[str, Any] | None:
    with _lock:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM whitelabels WHERE name = ?", (name,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
        return None


def get_all_whitelabels() -> list[dict[str, Any]]:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM whitelabels ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def update_whitelabel_features(name: str, features: str) -> bool:
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE whitelabels SET features = ?, updated_at = datetime('now') WHERE name = ?",
            (features, name),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0


def set_whitelabel_active(name: str, active: bool) -> bool:
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE whitelabels SET is_active = ?, updated_at = datetime('now') WHERE name = ?",
            (1 if active else 0, name),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0


def is_feature_active(name: str, feature: str) -> bool:
    """Check if a specific market feature is active for a whitelabel."""
    wl = get_whitelabel(name)
    if not wl or not wl.get("is_active"):
        return False
    features = parse_features(wl.get("features", "ALL"))
    return feature in features


# ── Trade State Machine ────────────────────────────────────────────

TRADE_OPEN = "OPEN"
TRADE_TP1 = "TP1_HIT"
TRADE_TP2 = "TP2_HIT"
TRADE_TP3 = "TP3_HIT"
TRADE_SL = "SL_HIT"
TRADE_CLOSE = "CLOSED"


def create_trade(
    user_id: str, symbol: str, direction: str, entry_price: float,
    stop_loss: float = 0, take_profit_1: float = 0,
    take_profit_2: float = 0, take_profit_3: float = 0,
    quantity: float = 1.0, leverage: int = 1,
    whitelabel_id: int = 0, signal_source: str = "",
) -> dict[str, Any]:
    now = datetime.now(WIB).isoformat()
    with _lock:
        conn = _conn()
        cur = conn.execute(
            """INSERT INTO trades (whitelabel_id, user_id, symbol, direction,
               entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3,
               quantity, leverage, status, open_time, signal_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
            (whitelabel_id, user_id, symbol.upper(), direction.upper(),
             entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3,
             quantity, leverage, now, signal_source),
        )
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()
        return get_trade(trade_id)


def get_trade(trade_id: int) -> dict[str, Any] | None:
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        if row:
            return dict(row)
        return None


def get_open_trades(user_id: str | None = None, whitelabel_id: int | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM trades WHERE status = 'OPEN'"
    params: list[Any] = []
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    if whitelabel_id is not None:
        query += " AND whitelabel_id = ?"
        params.append(whitelabel_id)
    query += " ORDER BY open_time DESC"
    with _lock:
        conn = _conn()
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_all_trades(user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = "SELECT * FROM trades"
    params: list[Any] = []
    if user_id:
        query += " WHERE user_id = ?"
        params.append(user_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _lock:
        conn = _conn()
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def close_trade(trade_id: int, close_price: float, outcome: str = "CLOSED") -> dict[str, Any] | None:
    with _lock:
        conn = _conn()
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade or trade["status"] != "OPEN":
            conn.close()
            return None

        entry = trade["entry_price"]
        direction = trade["direction"]
        pips = (close_price - entry) if direction == "BUY" else (entry - close_price)
        profit_usd = pips * trade["quantity"] * trade["leverage"]

        now = datetime.now(WIB).isoformat()
        conn.execute(
            """UPDATE trades SET status = ?, close_time = ?, close_price = ?,
               pips = ?, profit_usd = ?, outcome = ? WHERE id = ?""",
            (outcome, now, close_price, round(pips, 2), round(profit_usd, 2), outcome, trade_id),
        )
        conn.commit()
        result = dict(conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone())
        conn.close()
        return result


def check_tp_sl(trade_id: int, current_price: float) -> dict[str, Any] | None:
    """Check if a trade has hit TP or SL. Updates state if so."""
    with _lock:
        conn = _conn()
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade or trade["status"] != "OPEN":
            conn.close()
            return None

        entry = trade["entry_price"]
        direction = trade["direction"]
        sl = trade["stop_loss"]
        tp1 = trade["take_profit_1"]
        tp3 = trade["take_profit_3"]

        hit = None
        if direction == "BUY":
            if sl > 0 and current_price <= sl:
                hit = "SL_HIT"
            elif tp1 > 0 and current_price >= tp1:
                if tp3 > 0 and current_price >= tp3:
                    hit = "TP3_HIT"
                elif tp3 > 0 and current_price >= (tp1 + tp3) / 2:
                    hit = "TP2_HIT"
                else:
                    hit = "TP1_HIT"
        else:
            if sl > 0 and current_price >= sl:
                hit = "SL_HIT"
            elif tp1 > 0 and current_price <= tp1:
                if tp3 > 0 and current_price <= tp3:
                    hit = "TP3_HIT"
                elif tp3 > 0 and current_price <= (tp1 + tp3) / 2:
                    hit = "TP2_HIT"
                else:
                    hit = "TP1_HIT"

        if not hit:
            conn.close()
            return None

        outcome = hit
        pips = (current_price - entry) if direction == "BUY" else (entry - current_price)
        profit_usd = pips * trade["quantity"] * trade["leverage"]
        now = datetime.now(WIB).isoformat()

        conn.execute(
            """UPDATE trades SET status = ?, close_time = ?, close_price = ?,
               pips = ?, profit_usd = ?, outcome = ? WHERE id = ?""",
            (outcome, now, current_price, round(pips, 2), round(profit_usd, 2), outcome, trade_id),
        )
        conn.commit()
        result = dict(conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone())
        conn.close()
        return result


def get_stats(user_id: str | None = None) -> dict[str, float]:
    """Calculate win rate and P&L stats."""
    with _lock:
        conn = _conn()
        if user_id:
            rows = conn.execute(
                "SELECT outcome, COUNT(*) as cnt, SUM(pips) as total_pips, SUM(profit_usd) as total_profit "
                "FROM trades WHERE user_id = ? AND outcome != '' GROUP BY outcome",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT outcome, COUNT(*) as cnt, SUM(pips) as total_pips, SUM(profit_usd) as total_profit "
                "FROM trades WHERE outcome != '' GROUP BY outcome"
            ).fetchall()
        conn.close()

    wins = 0
    losses = 0
    total_pips = 0.0
    total_profit = 0.0

    for row in rows:
        if row["outcome"] in ("TP1_HIT", "TP2_HIT", "TP3_HIT"):
            wins += row["cnt"]
        elif row["outcome"] == "SL_HIT":
            losses += row["cnt"]
        total_pips += row["total_pips"] or 0
        total_profit += row["total_profit"] or 0

    total = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "total": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
        "total_pips": round(total_pips, 1),
        "total_profit": round(total_profit, 2),
    }


# ── User Config ────────────────────────────────────────────────────

def get_or_create_user(telegram_id: str, username: str = "") -> dict[str, Any]:
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            if username and row["username"] != username:
                conn.execute("UPDATE users SET username = ? WHERE telegram_id = ?",
                             (username, telegram_id))
                conn.commit()
            conn.close()
            return dict(row)

        conn.execute(
            "INSERT INTO users (telegram_id, username) VALUES (?, ?)",
            (telegram_id, username or f"User{telegram_id}"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        conn.close()
        return dict(row)


def update_user_features(telegram_id: str, features: str) -> bool:
    with _lock:
        conn = _conn()
        cur = conn.execute("UPDATE users SET features = ? WHERE telegram_id = ?",
                          (features, telegram_id))
        conn.commit()
        conn.close()
        return cur.rowcount > 0


def get_user_features(telegram_id: str) -> set[str]:
    user = get_or_create_user(telegram_id)
    return parse_features(user.get("features", "ALL"))
