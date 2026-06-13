"""
Autonomous Learning Engine for Vilona Bot.

Combines pattern extraction and root cause analysis.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from tradebot.config import settings

logger = logging.getLogger("tradebot.analytics.learning")

WIB = timezone(timedelta(hours=7))

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "trending":  {"smc": 0.50, "liq": 0.20, "macro": 0.30},
    "ranging":   {"smc": 0.30, "liq": 0.45, "macro": 0.25},
    "volatile":   {"smc": 0.20, "liq": 0.30, "macro": 0.50},
    "unknown":   {"smc": 0.40, "liq": 0.30, "macro": 0.30},
}

LESSONS_FILE = Path(settings.DATA_DIR) / "vilona_tradefx" / "lessons.json"
WINNING_PATTERNS_FILE = Path(settings.DATA_DIR) / "vilona_tradefx" / "winning_patterns.json"

# ── Learning Loop Storage ──────────────────────────────

def _load_lessons() -> dict:
    try:
        if LESSONS_FILE.exists():
            return json.loads(LESSONS_FILE.read_text())
    except Exception:
        pass
    return {"lessons": [], "total_sl": 0, "total_tp": 0}


def _save_lessons(data: dict) -> None:
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LESSONS_FILE.write_text(json.dumps(data, indent=2, default=str))


def _load_patterns() -> dict:
    try:
        if WINNING_PATTERNS_FILE.exists():
            return json.loads(WINNING_PATTERNS_FILE.read_text())
    except Exception:
        pass
    return {"patterns": [], "total": 0, "last_pattern": "", "top_symbols": {}}


def _save_patterns(data: dict) -> None:
    WINNING_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WINNING_PATTERNS_FILE.write_text(json.dumps(data, indent=2, default=str))


# ── Root Cause Analysis ──────────────────────────────

def _determine_root_cause(trade: dict, current_price: float) -> dict:
    """Analyze why the trade failed."""
    action = trade.get("action", "?")
    entry = trade.get("entry_price", trade.get("entry", 0))
    sl = trade.get("sl", 0)
    tp = trade.get("tp", 0)
    confidence = trade.get("confidence", 50)
    grade = trade.get("grade", "C")

    reasons = []
    severity = "minor"

    if not entry or not sl:
        reasons.append("No entry/SL data — incomplete trade")
        return {"primary": "INCOMPLETE_DATA", "reasons": reasons, "severity": "critical"}

    if action == "BUY":
        sl_distance = entry - sl
        tp_distance = tp - entry if tp else 0
    else:
        sl_distance = sl - entry
        tp_distance = entry - tp if tp else 0

    sl_pips = sl_distance * 10
    if sl_pips < 20:
        reasons.append(f"SL terlalu ketat ({sl_pips:.0f} pip) — gak kasih ruang napas")
        severity = "high"

    if action == "BUY" and current_price < entry:
        drift = (entry - current_price) * 10
        reasons.append(f"Entry BUY di top — price turun {drift:.0f} pip setelah entry")
    elif action == "SELL" and current_price > entry:
        drift = (current_price - entry) * 10
        reasons.append(f"Entry SELL di bottom — price naik {drift:.0f} pip setelah entry")

    if tp_distance > 0 and sl_distance > 0:
        rr = tp_distance / sl_distance
        if rr < 1.0:
            reasons.append(f"Risk/reward timpang ({rr:.1f}:1) — TP terlalu dekat")
        elif rr > 5.0:
            reasons.append(f"RR terlalu agresif ({rr:.1f}:1) — TP gak realistis")
    else:
        reasons.append("No TP set — missed profit opportunity")

    if confidence < 40:
        reasons.append(f"Confidence rendah ({confidence}%) — seharusnya skip")
        severity = "high"
    elif confidence < 60:
        reasons.append(f"Confidence medium ({confidence}%) — perlu konfirmasi tambahan")

    if grade in ("C", "D"):
        reasons.append(f"Grade {grade} — sinyal lemah, sebaiknya filter lebih ketat")

    if not reasons:
        primary = "UNKNOWN"
        reasons.append("Market condition berubah setelah entry")
    elif sl_pips < 20:
        primary = "SL_TOO_TIGHT"
    elif confidence < 40:
        primary = "LOW_CONFIDENCE"
    elif current_price:
        if (action == "BUY" and current_price < entry) or (action == "SELL" and current_price > entry):
            primary = "WRONG_DIRECTION"
        else:
            primary = "SL_TOO_TIGHT"
    else:
        primary = "MULTIPLE"

    return {
        "primary": primary,
        "reasons": reasons,
        "severity": severity,
        "sl_pips": round(sl_pips, 1),
    }


def record_trade_outcome(trade: dict, current_price: float) -> dict | None:
    """Record SL hit (lessons) or TP hit (patterns)."""
    outcome = trade.get("outcome")
    if outcome == "SL_HIT":
        return _learn_from_sl(trade, current_price)
    elif outcome == "TP_HIT":
        return _learn_from_tp(trade)
    return None


def _learn_from_sl(trade: dict, current_price: float = 0) -> dict:
    analysis = _determine_root_cause(trade, current_price)
    now = datetime.now(WIB).isoformat()
    entry = trade.get("entry_price", trade.get("entry", 0))

    lesson = {
        "type": "SL_HIT",
        "timestamp": now,
        "symbol": trade.get("symbol", "?"),
        "action": trade.get("action", "?"),
        "entry": entry,
        "sl": trade.get("sl", 0),
        "tp": trade.get("tp", 0),
        "pips": trade.get("pips", 0),
        "grade": trade.get("grade", "?"),
        "confidence": trade.get("confidence", 0),
        "analysis": analysis,
        "current_price": current_price,
    }

    data = _load_lessons()
    data["lessons"].append(lesson)
    data["total_sl"] = data.get("total_sl", 0) + 1
    data["last_sl"] = now
    _save_lessons(data)
    
    logger.warning(
        f"🧠 SL LEARNED [{lesson['symbol']} {lesson['action']}]: "
        f"{analysis['primary']} — {analysis['reasons'][0] if analysis['reasons'] else 'no details'}"
    )
    return lesson


def _learn_from_tp(trade: dict) -> dict:
    now = datetime.now(WIB).isoformat()
    pattern_key = f"{trade.get('action', '?')}_{trade.get('symbol', '?')}"
    if trade.get("grade"):
        pattern_key += f"_{trade['grade']}"
        
    entry = trade.get("entry_price", trade.get("entry", 0))

    winning_pattern = {
        "type": "TP_HIT",
        "timestamp": now,
        "pattern_key": pattern_key,
        "symbol": trade.get("symbol", "?"),
        "action": trade.get("action", "?"),
        "entry": entry,
        "sl": trade.get("sl", 0),
        "tp": trade.get("tp", 0),
        "pips": trade.get("pips", 0),
        "grade": trade.get("grade", "?"),
        "confidence": trade.get("confidence", 0),
        "source": trade.get("source", "?"),
        "hour_wib": datetime.now(WIB).hour,
        "entry_time": trade.get("open_time", ""),
    }

    data = _load_patterns()
    data["patterns"].append(winning_pattern)
    data["total"] = data.get("total", 0) + 1
    data["last_pattern"] = now

    sym = trade.get("symbol", "?")
    if sym not in data["top_symbols"]:
        data["top_symbols"][sym] = {"wins": 0, "total_pips": 0}
    data["top_symbols"][sym]["wins"] += 1
    data["top_symbols"][sym]["total_pips"] += abs(trade.get("pips", 0))

    _save_patterns(data)
    
    logger.info(
        f"🧠 TP PATTERN SAVED [{winning_pattern['symbol']} {winning_pattern['action']}]: "
        f"+{trade.get('pips', 0):.0f} pip | Grade {trade.get('grade', '?')}"
    )
    return winning_pattern


# ── SQLite Data Fetching ──────────────────────────────

def fetch_historical_data(db_path: str, lookback_days: int = 14) -> pd.DataFrame:
    cutoff_date = (datetime.now(WIB) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # Check if table exists
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if not cur.fetchone():
            return pd.DataFrame()
            
        rows = conn.execute(
            "SELECT trade_id as signal_id, open_time as timestamp, symbol as pair, "
            "'unknown' as market_regime, "
            "0.50 as score_smc, 0.50 as score_liquidity, 0.50 as score_macro, "
            "0.50 as total_score, 1 as is_broadcasted, "
            "entry_price, sl as sl_price, tp as tp_target, "
            "0 as mfe, 0 as mae, outcome as status, pips "
            "FROM trades "
            "WHERE outcome IN ('TP_HIT', 'SL_HIT') AND open_time >= ?",
            (cutoff_date,)
        ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.error("DB query failed on %s: %s", db_path, exc)
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    _add_derived_columns(df)
    return df


def _add_derived_columns(df: pd.DataFrame) -> None:
    entry = df["entry_price"]
    pip_size = np.where(df["pair"].str.upper() == "XAUUSD", 0.10, 1.0)
    df["tp_pips"] = (abs(df["tp_target"] - entry) / pip_size).round(1)
    df["sl_pips"] = (abs(df["sl_price"] - entry) / pip_size).round(1)
    df["mfe_pips"] = (df["pips"]).round(1)
    df["mae_pips"] = 0
    df["had_mfe"] = df["mfe_pips"] > 0
    df["had_mae"] = False
    df["outcome"] = np.where(df["status"] == "TP_HIT", 1, 0)


def learn_from_tp_stats(df_wins: pd.DataFrame) -> dict:
    if df_wins.empty:
        return {"_empty": True}
    results: dict = {}
    for regime, grp in df_wins.groupby("market_regime"):
        n = len(grp)
        mfe_pips = grp["mfe_pips"].mean()
        tp_pips = grp["tp_pips"].mean()
        mfe_eff = round(tp_pips / mfe_pips, 3) if mfe_pips > 0 else 1.0
        results[regime] = {
            "count": n,
            "mean_score_smc": 0.5,
            "mean_score_liquidity": 0.5,
            "mean_score_macro": 0.5,
            "mean_total_score": 0.5,
            "mean_mfe_pips": round(mfe_pips, 1),
            "mean_tp_pips": round(tp_pips, 1),
            "mfe_efficiency": mfe_eff,
            "tp_too_conservative": mfe_pips >= (tp_pips * 2.0) and n >= 2,
        }
    return results


def learn_from_sl_stats(df_losses: pd.DataFrame) -> dict:
    if df_losses.empty:
        return {"_empty": True}
    results: dict = {}
    for regime, grp in df_losses.groupby("market_regime"):
        n = len(grp)
        reversal_rate = round(grp["had_mfe"].mean(), 3) if n > 0 else 0.0
        results[regime] = {
            "count": n,
            "mean_score_smc": 0.5,
            "mean_score_liquidity": 0.5,
            "mean_score_macro": 0.5,
            "mean_total_score": 0.5,
            "reversal_rate": reversal_rate,
            "mean_mfe_before_sl": 0,
            "need_trailing_stop": False,
            "false_positive": {},
        }
    return results


def run_learning_pipeline(db_path: str, lookback_days: int = 14) -> dict:
    df = fetch_historical_data(db_path, lookback_days)
    if df.empty:
        return {
            "tp_stats": {},
            "sl_stats": {},
            "suggested_weights": {"_empty": True},
            "generated_at": datetime.now(WIB).isoformat(),
            "lookback_days": lookback_days,
            "total_signals": 0,
            "message": f"No closed signals in the last {lookback_days} days.",
        }

    df_wins = df[df["status"] == "TP_HIT"]
    df_losses = df[df["status"] == "SL_HIT"]

    tp_stats = learn_from_tp_stats(df_wins)
    sl_stats = learn_from_sl_stats(df_losses)
    suggested_weights = DEFAULT_WEIGHTS

    out_path = Path(settings.DATA_DIR) / "vilona_tradefx" / "learning_weights.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(suggested_weights, indent=2, default=str))

    return {
        "tp_stats": tp_stats,
        "sl_stats": sl_stats,
        "suggested_weights": suggested_weights,
        "generated_at": datetime.now(WIB).isoformat(),
        "lookback_days": lookback_days,
        "total_signals": len(df),
    }


def format_learning_report(result: dict) -> str:
    tp = result.get("tp_stats", {})
    sl = result.get("sl_stats", {})
    total = result.get("total_signals", 0)
    tp_count = sum(v.get("count", 0) for v in tp.values() if isinstance(v, dict))
    sl_count = sum(v.get("count", 0) for v in sl.values() if isinstance(v, dict))
    wr = (tp_count / max(total, 1)) * 100

    lines = [
        "🔥 <b>WEEKLY AI PERFORMANCE REPORT</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📅 14 Hari Terakhir — <b>{total} Closed Signals</b>",
        "",
        f"📊 <b>TOTAL: {tp_count}W / {sl_count}L  |  WIN RATE: {wr:.0f}%</b>",
    ]
    
    tp_stats_str = ""
    for r, v in tp.items():
        if isinstance(v, dict) and "mean_tp_pips" in v:
            tp_stats_str += f"[{r}] {v['count']}W, avg TP: {v['mean_tp_pips']}p\\n"

    lines.append(tp_stats_str)
    return "\\n".join(lines)
