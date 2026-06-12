"""
ML Feedback Loop — Autonomous Learning Signal Tracker (Shadow Mode).

Records every signal's anatomy (scores, entry, SL/TP, regime) and
tracks its journey through MFE/MAE monitoring via M15 bar updates.
Runs in a background thread — never blocks the main Telegram handler.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from members import _conn, DB_PATH, DATA_DIR

logger = logging.getLogger("vtfx-ml-feedback")

WIB = timezone(timedelta(hours=7))
_loop_running = False
_loop_thread: threading.Thread | None = None
_loop_interval: float = 60.0  # check every 60s (M15 bars arrive every 900s)


# ── Signal logging ──────────────────────────────────────────────────────────

def log_signal(
    *,
    signal_id: str = "",
    pair: str = "XAUUSD",
    market_regime: str = "",
    score_smc: float = 0,
    score_liquidity: float = 0,
    score_macro: float = 0,
    total_score: float = 0,
    is_broadcasted: bool = False,
    entry_price: float = 0,
    sl_price: float = 0,
    tp_target: float = 0,
) -> str:
    """Insert a signal into ml_feedback_loop for autonomous tracking.

    Returns the signal_id (generated if not provided).
    """
    sid = signal_id or f"sig-{uuid.uuid4().hex[:12]}"
    now = datetime.now(WIB).isoformat()
    try:
        with _conn() as db:
            db.execute(
                """INSERT OR REPLACE INTO ml_feedback_loop
                   (signal_id, timestamp, pair, market_regime,
                    score_smc, score_liquidity, score_macro, total_score,
                    is_broadcasted, entry_price, sl_price, tp_target, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""",
                (sid, now, pair, market_regime,
                 score_smc, score_liquidity, score_macro, total_score,
                 1 if is_broadcasted else 0,
                 entry_price, sl_price, tp_target),
            )
        logger.debug("Logged signal %s (%s broadcast=%s)", sid, pair, is_broadcasted)
    except Exception as exc:
        logger.error("Failed to log signal %s: %s", sid, exc)
    return sid


# ── State persistence worker ────────────────────────────────────────────────

def update_signal_states(current_price: float | None = None, pair: str = "XAUUSD"):
    """Update MFE/MAE and status for all OPEN signals.

    Compares each OPEN signal's entry_price against the current
    OHLCV candle. Updates mfe (max favorable excursion) and mae
    (max adverse excursion). If price hits SL or TP, status flips.

    Called by background loop every ~60s or directly when a new M15
    bar OHLCV is available.

    Args:
        current_price: Latest bid/ask price. If None, uses mid-price
                       from the data feed.
        pair: Asset pair to filter on (default XAUUSD).
    """
    if current_price is None:
        try:
            from scripts.vilona_tradefx_handler import fetch_price
            current_price = fetch_price("gold")
        except Exception:
            return  # can't get price, skip this tick

    if not current_price or current_price <= 0:
        return

    try:
        with _conn() as db:
            rows = db.execute(
                "SELECT * FROM ml_feedback_loop WHERE status='OPEN' AND pair=?",
                (pair,),
            ).fetchall()

            updated = 0
            for row in rows:
                sig = dict(row)
                entry = sig.get("entry_price", 0)
                sl = sig.get("sl_price", 0)
                tp = sig.get("tp_target", 0)
                current_mfe = sig.get("mfe", 0) or 0
                current_mae = sig.get("mae", 0) or 0

                if not entry or entry <= 0:
                    continue

                # ── Calculate new MFE / MAE ──
                if tp and tp > entry:  # BUY signal
                    new_mfe = max(current_mfe, current_price - entry)
                    new_mae = min(current_mae, current_price - entry)
                elif tp and tp < entry:  # SELL signal
                    new_mfe = max(current_mfe, entry - current_price)
                    new_mae = min(current_mae, entry - current_price)
                else:
                    new_mfe = current_mfe
                    new_mae = current_mae

                # ── Check SL / TP hit ──
                new_status = "OPEN"
                if sl and sl > 0:
                    if (tp and tp > entry and current_price <= sl) or \
                       (tp and tp < entry and current_price >= sl):
                        new_status = "SL_HIT"
                if tp and tp > 0:
                    if (tp > entry and current_price >= tp) or \
                       (tp < entry and current_price <= tp):
                        new_status = "TP_HIT"

                # ── Persist update ──
                db.execute(
                    "UPDATE ml_feedback_loop SET mfe=?, mae=?, status=? WHERE signal_id=?",
                    (round(new_mfe, 2), round(new_mae, 2), new_status, sig["signal_id"]),
                )
                updated += 1

            if updated:
                logger.debug("Updated %d OPEN signal(s) for %s", updated, pair)

    except Exception as exc:
        logger.error("update_signal_states failed: %s", exc)


# ── Background loop ─────────────────────────────────────────────────────────

def _background_loop():
    """Run update_signal_states every _loop_interval seconds."""
    global _loop_running
    logger.info("ML Feedback Loop background worker started (interval=%ds)", _loop_interval)
    while _loop_running:
        try:
            update_signal_states()
        except Exception as exc:
            logger.error("Background loop tick failed: %s", exc)
        # Sleep in 5s chunks so shutdown is responsive
        for _ in range(int(_loop_interval / 5)):
            if not _loop_running:
                break
            time.sleep(5)
    logger.info("ML Feedback Loop background worker stopped")


def start_loop(interval: float = 60.0):
    """Start the background MFE/MAE tracking thread.

    Call once at handler startup. Thread is daemon — exits when
    the main process exits.
    """
    global _loop_running, _loop_thread, _loop_interval
    if _loop_running:
        logger.warning("ML Feedback Loop already running")
        return
    _loop_interval = interval
    _loop_running = True
    _loop_thread = threading.Thread(target=_background_loop, daemon=True, name="ml-feedback")
    _loop_thread.start()
    logger.info("ML Feedback Loop thread started")


def stop_loop():
    """Stop the background tracking thread."""
    global _loop_running
    _loop_running = False
    if _loop_thread:
        _loop_thread.join(timeout=5)
    logger.info("ML Feedback Loop thread stopped")


def get_open_signals(pair: str = "XAUUSD") -> list[dict]:
    """Return all OPEN signals for a given pair."""
    try:
        with _conn() as db:
            rows = db.execute(
                "SELECT * FROM ml_feedback_loop WHERE status='OPEN' AND pair=?",
                (pair,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_signal_summary(days: int = 7) -> dict:
    """Return summary stats for the last N days."""
    try:
        cutoff = (datetime.now(WIB) - timedelta(days=days)).isoformat()
        with _conn() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM ml_feedback_loop WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]
            tp_count = db.execute(
                "SELECT COUNT(*) FROM ml_feedback_loop WHERE status='TP_HIT' AND timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]
            sl_count = db.execute(
                "SELECT COUNT(*) FROM ml_feedback_loop WHERE status='SL_HIT' AND timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]
            broadcasted = db.execute(
                "SELECT COUNT(*) FROM ml_feedback_loop WHERE is_broadcasted=1 AND timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]
        return {
            "total_signals": total,
            "tp_hit": tp_count,
            "sl_hit": sl_count,
            "open": total - tp_count - sl_count,
            "broadcasted": broadcasted,
            "win_rate": round(tp_count / max(tp_count + sl_count, 1) * 100, 1),
        }
    except Exception:
        return {"total_signals": 0, "tp_hit": 0, "sl_hit": 0, "open": 0, "broadcasted": 0, "win_rate": 0}


# ── Convenience: quick signal log from handler output dict ──

def log_from_handler(sig: dict, is_broadcasted: bool = False) -> str:
    """Quick adapter: log a signal dict from the handler into ml_feedback_loop.

    Args:
        sig: Signal dict with keys: action, entry, sl, tp1, score, pair, etc.
        is_broadcasted: True if sent to Telegram / bridge.

    Returns:
        signal_id
    """
    entry = float(sig.get("entry", 0) or 0)
    sl_price = float(sig.get("sl", 0) or 0)
    tp = float(sig.get("tp1", 0) or 0)
    score_smc = float(sig.get("score_smc", 0) or 0)
    score_liq = float(sig.get("score_liquidity", 0) or 0)
    score_macro = float(sig.get("score_macro", 0) or 0)
    total = float(sig.get("score", 0) or 0)

    return log_signal(
        signal_id=sig.get("signal_id", ""),
        pair=str(sig.get("pair", "XAUUSD")).upper(),
        market_regime=str(sig.get("regime", "") or ""),
        score_smc=score_smc,
        score_liquidity=score_liq,
        score_macro=score_macro,
        total_score=total,
        is_broadcasted=is_broadcasted,
        entry_price=entry,
        sl_price=sl_price,
        tp_target=tp,
    )
