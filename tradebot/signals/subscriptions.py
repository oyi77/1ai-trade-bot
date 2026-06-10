"""
Signal Subscription System — users pick which signal types they receive.

Signal Categories:
  smc         Smart Money Concepts (FVG, liquidity, sweeps, SMC)
  trend       Trend analysis (TV engine, chaos filter)
  structure   Market structure (CRT/TBS, layering, session levels)
  quant       Quantitative pattern analysis
  consensus   Multi-engine consensus signals
  all         All signal types combined
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
import sqlite3
from pathlib import Path

from tradebot.models.signal import Signal

LOG = logging.getLogger(__name__)

# ── Signal Categories ─────────────────────────────────────────────────

class SignalCategory(StrEnum):
    ALL = "all"
    SMC = "smc"
    TREND = "trend"
    STRUCTURE = "structure"
    QUANT = "quant"
    CONSENSUS = "consensus"


CATEGORY_ENGINES: dict[SignalCategory, list[str]] = {
    SignalCategory.SMC: [
        "smc_scalper", "fvg_detector", "liquidity_zones",
        "sweep_detector", "hermes_liquidity_hunter",
    ],
    SignalCategory.TREND: [
        "tv_engine", "chaos_filter",
    ],
    SignalCategory.STRUCTURE: [
        "crt_tbs", "layering", "session_levels",
    ],
    SignalCategory.QUANT: [
        "quant_pattern",
    ],
    SignalCategory.CONSENSUS: [
        "consensus",
    ],
    SignalCategory.ALL: [
        # Will be computed at runtime from all engines
    ],
}

CATEGORY_DESCRIPTIONS: dict[SignalCategory, str] = {
    SignalCategory.SMC: "🏦 Smart Money — FVG, liquidity zones, sweeps, SMC scalping",
    SignalCategory.TREND: "📈 Trend — EMA/MACD/Bollinger, chaos filter, TV engine",
    SignalCategory.STRUCTURE: "🏗 Structure — CRT/TBS, supply/demand layering, session levels",
    SignalCategory.QUANT: "🔢 Quant — Statistical pattern recognition",
    SignalCategory.CONSENSUS: "🤝 Consensus — Multi-engine agreement signals (highest accuracy)",
    SignalCategory.ALL: "🌐 All — Every signal type combined",
}

CATEGORY_EMOJI: dict[SignalCategory, str] = {
    SignalCategory.SMC: "🏦",
    SignalCategory.TREND: "📈",
    SignalCategory.STRUCTURE: "🏗",
    SignalCategory.QUANT: "🔢",
    SignalCategory.CONSENSUS: "🤝",
    SignalCategory.ALL: "🌐",
}


# ── Signal Subscription ───────────────────────────────────────────────

@dataclass
class SignalChannel:
    """A signal channel that users can subscribe to."""
    category: SignalCategory
    min_confidence: float = 0.5
    symbols: list[str] = field(default_factory=lambda: ["CRYPTO_IDX"])
    active: bool = True


@dataclass
class UserSubscription:
    """A user's signal subscription preferences."""
    user_id: str
    channels: list[SignalCategory]
    min_confidence: float = 0.5
    active: bool = True
    created_at: str = ""




DB_PATH = Path("data/subscriptions.db")


def _get_sub_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_sub_db() -> None:
    conn = _get_sub_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signal_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            min_confidence REAL DEFAULT 0.5,
            active BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, category)
        );
        CREATE INDEX IF NOT EXISTS idx_subs_user ON signal_subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_subs_active ON signal_subscriptions(active);
    """)
    conn.commit()
    conn.close()


def subscribe_user(user_id: str, category: SignalCategory, min_conf: float = 0.5) -> bool:
    """Subscribe a user to a signal category."""
    init_sub_db()
    conn = _get_sub_db()
    conn.execute(
        """INSERT OR REPLACE INTO signal_subscriptions (user_id, category, min_confidence, active)
           VALUES (?, ?, ?, 1)""",
        (user_id, category.value, min_conf),
    )
    conn.commit()
    conn.close()
    LOG.info("User %s subscribed to %s", user_id, category.value)
    return True


def unsubscribe_user(user_id: str, category: str | None = None) -> bool:
    """Unsubscribe from a category or all."""
    init_sub_db()
    conn = _get_sub_db()
    if category:
        conn.execute(
            "UPDATE signal_subscriptions SET active = 0 WHERE user_id = ? AND category = ?",
            (user_id, category),
        )
    else:
        conn.execute(
            "UPDATE signal_subscriptions SET active = 0 WHERE user_id = ?",
            (user_id,),
        )
    conn.commit()
    conn.close()
    return True


def get_user_subscriptions(user_id: str) -> list[SignalCategory]:
    """Get active signal categories for a user."""
    init_sub_db()
    conn = _get_sub_db()
    rows = conn.execute(
        "SELECT category FROM signal_subscriptions WHERE user_id = ? AND active = 1",
        (user_id,),
    ).fetchall()
    conn.close()
    return [SignalCategory(row["category"]) for row in rows]


def get_subscribers(category: SignalCategory) -> list[str]:
    """Get all user IDs subscribed to a category."""
    init_sub_db()
    conn = _get_sub_db()
    rows = conn.execute(
        "SELECT user_id FROM signal_subscriptions WHERE category = ? AND active = 1",
        (category.value,),
    ).fetchall()
    conn.close()
    return [row["user_id"] for row in rows]


def get_all_active_subscribers() -> dict[SignalCategory, list[str]]:
    """Get all active subscribers grouped by category."""
    init_sub_db()
    conn = _get_sub_db()
    rows = conn.execute(
        "SELECT user_id, category FROM signal_subscriptions WHERE active = 1"
    ).fetchall()
    conn.close()

    result: dict[SignalCategory, list[str]] = {}
    for row in rows:
        cat = SignalCategory(row["category"])
        if cat not in result:
            result[cat] = []
        result[cat].append(row["user_id"])
    return result


# ── Signal Router ─────────────────────────────────────────────────────

def categorize_signal(signal: Signal, engine_name: str) -> list[SignalCategory]:
    """Map an engine signal to its categories."""
    categories: list[SignalCategory] = []
    for cat, engines in CATEGORY_ENGINES.items():
        if cat == SignalCategory.ALL:
            continue
        if engine_name in engines:
            categories.append(cat)
    if categories:
        categories.append(SignalCategory.ALL)  # ALL includes everything
    return categories


def signal_passes_filter(signal: Signal, min_confidence: float) -> bool:
    """Check if signal meets minimum confidence threshold."""
    return signal.confidence >= min_confidence


def format_signal_message(signal: Signal, engine_name: str, categories: list[SignalCategory]) -> str:
    """Format a signal for Telegram delivery."""
    emoji = "🟢" if signal.direction.upper() == "CALL" else "🔴"
    cat_emojis = " ".join(CATEGORY_EMOJI.get(c, "") for c in categories)

    return (
        f"{emoji} *{signal.direction.upper()}* — {signal.symbol}\n"
        f"Confidence: {signal.confidence:.0%} | Grade: {signal.grade.name}\n"
        f"Engine: `{engine_name}` | {cat_emojis}\n"
        f"Price: {signal.entry_price or 'N/A'}\n"
        f"_via {signal.source.value}_"
    )
