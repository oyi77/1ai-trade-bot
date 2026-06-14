#!/usr/bin/env python3
"""
learning_loop.py — Autonomous Trade Learning Engine

Detects SL/TP outcomes and auto-generates lessons + winning patterns.
Fully autonomous — zero user command required.

SL HIT → Analyze root cause → Save lesson
TP HIT → Save winning pattern → Add to reusable strategy
"""
import json, logging, os, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("learning-loop")
WIB = timezone(timedelta(hours=7))

PROJECT_DIR = Path(__file__).resolve().parent.parent
LESSONS_FILE = PROJECT_DIR / "data" / "vilona_tradefx" / "lessons.json"
WINNING_PATTERNS_FILE = PROJECT_DIR / "data" / "vilona_tradefx" / "winning_patterns.json"


# ── Storage ──────────────────────────────────────────

def _load_lessons() -> dict:
    try:
        if LESSONS_FILE.exists():
            return json.loads(LESSONS_FILE.read_text())
    except Exception:
        pass
    return {"lessons": [], "total_sl": 0, "total_tp": 0}


def _save_lessons(data: dict):
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LESSONS_FILE.write_text(json.dumps(data, indent=2, default=str))


def _load_patterns() -> dict:
    try:
        if WINNING_PATTERNS_FILE.exists():
            return json.loads(WINNING_PATTERNS_FILE.read_text())
    except Exception:
        pass
    return {"patterns": [], "total": 0, "last_pattern": "", "top_symbols": {}}


def _save_patterns(data: dict):
    WINNING_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WINNING_PATTERNS_FILE.write_text(json.dumps(data, indent=2, default=str))


# ── Root Cause Analysis ──────────────────────────────

def _determine_root_cause(trade: dict, current_price: float) -> dict:
    """Analyze why the trade failed."""
    action = trade.get("action", "?")
    entry = trade.get("entry", 0)
    sl = trade.get("sl", 0)
    tp = trade.get("tp", 0)
    confidence = trade.get("confidence", 50)
    grade = trade.get("grade", "C")
    symbol = trade.get("symbol", "?")

    reasons = []
    severity = "minor"

    if not entry or not sl:
        reasons.append("No entry/SL data — incomplete trade")
        return {"primary": "INCOMPLETE_DATA", "reasons": reasons, "severity": "critical"}

    # Calculate SL distance in points
    if action == "BUY":
        sl_distance = entry - sl
        tp_distance = tp - entry if tp else 0
    else:
        sl_distance = sl - entry
        tp_distance = entry - tp if tp else 0

    # 1. SL too tight?
    sl_pips = sl_distance * 10  # XAUUSD: 0.1 pip per point
    if sl_pips < 20:
        reasons.append(f"SL terlalu ketat ({sl_pips:.0f} pip) — gak kasih ruang napas")
        severity = "high"

    # 2. Wrong direction (price went against)
    if action == "BUY" and current_price < entry:
        drift = (entry - current_price) * 10
        reasons.append(f"Entry BUY di top — price turun {drift:.0f} pip setelah entry")
    elif action == "SELL" and current_price > entry:
        drift = (current_price - entry) * 10
        reasons.append(f"Entry SELL di bottom — price naik {drift:.0f} pip setelah entry")

    # 3. Risk/reward imbalance
    if tp_distance > 0 and sl_distance > 0:
        rr = tp_distance / sl_distance
        if rr < 1.0:
            reasons.append(f"Risk/reward timpang ({rr:.1f}:1) — TP terlalu dekat")
        elif rr > 5.0:
            reasons.append(f"RR terlalu agresif ({rr:.1f}:1) — TP gak realistis")
    else:
        reasons.append("No TP set — missed profit opportunity")

    # 4. Low confidence signal
    if confidence < 40:
        reasons.append(f"Confidence rendah ({confidence}%) — seharusnya skip")
        severity = "high"
    elif confidence < 60:
        reasons.append(f"Confidence medium ({confidence}%) — perlu konfirmasi tambahan")

    # 5. Grade
    if grade in ("C", "D"):
        reasons.append(f"Grade {grade} — sinyal lemah, sebaiknya filter lebih ketat")

    # Determine primary cause
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


# ── Main Learning Functions ─────────────────────────

def learn_from_sl(trade: dict, current_price: float = 0):
    """Analyze SL hit and save lesson to file."""
    analysis = _determine_root_cause(trade, current_price)
    now = datetime.now(WIB).isoformat()

    lesson = {
        "type": "SL_HIT",
        "timestamp": now,
        "symbol": trade.get("symbol", "?"),
        "action": trade.get("action", "?"),
        "entry": trade.get("entry", 0),
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


def learn_from_tp(trade: dict):
    """Save winning entry pattern on TP hit."""
    now = datetime.now(WIB).isoformat()

    # Extract key features of this winning trade
    pattern_key = f"{trade.get('action', '?')}_{trade.get('symbol', '?')}"
    if trade.get("grade"):
        pattern_key += f"_{trade['grade']}"

    winning_pattern = {
        "type": "TP_HIT",
        "timestamp": now,
        "pattern_key": pattern_key,
        "symbol": trade.get("symbol", "?"),
        "action": trade.get("action", "?"),
        "entry": trade.get("entry", 0),
        "sl": trade.get("sl", 0),
        "tp": trade.get("tp", 0),
        "pips": trade.get("pips", 0),
        "grade": trade.get("grade", "?"),
        "confidence": trade.get("confidence", 0),
        "source": trade.get("source", "?"),
        # Time context
        "hour_wib": datetime.now(WIB).hour,
        "entry_time": trade.get("open_time", ""),
    }

    data = _load_patterns()
    data["patterns"].append(winning_pattern)
    data["total"] = data.get("total", 0) + 1
    data["last_pattern"] = now

    # Track per-symbol stats
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


def get_learning_summary() -> str:
    """Return a summary of all lessons learned."""
    lessons = _load_lessons()
    patterns = _load_patterns()

    sl_count = lessons.get("total_sl", 0)
    tp_count = patterns.get("total", 0)
    total_lessons = len(lessons.get("lessons", []))

    if total_lessons == 0 and tp_count == 0:
        return "🧠 <b>Learning Loop</b>\nBelum ada data. Mulai belajar setelah trade pertama."

    # Top SL causes
    sl_causes = {}
    for l in lessons.get("lessons", []):
        cause = l.get("analysis", {}).get("primary", "UNKNOWN")
        sl_causes[cause] = sl_causes.get(cause, 0) + 1
    top_causes = sorted(sl_causes.items(), key=lambda x: -x[1])[:3]

    # Top TP symbols
    top_symbols = sorted(patterns.get("top_symbols", {}).items(), key=lambda x: -x[1]["wins"])[:3]

    lines = [
        "🧠 <b>LEARNING LOOP SUMMARY</b>",
        f"━━━━━━━━━━━━━━━━━━",
        f"📉 SL dipelajari: {total_lessons}",
        f"📈 TP pola disimpan: {tp_count}",
    ]
    if top_causes:
        lines.append(f"\n🔴 <b>Top SL Causes:</b>")
        for c, n in top_causes:
            label = {"SL_TOO_TIGHT": "SL Terlalu Ketat", "WRONG_DIRECTION": "Salah Arah",
                     "LOW_CONFIDENCE": "Confidence Rendah"}.get(c, c)
            lines.append(f"  • {label}: {n}x")
    if top_symbols:
        lines.append(f"\n🟢 <b>Top Winning Symbols:</b>")
        for s, v in top_symbols:
            lines.append(f"  • {s}: {v['wins']} win ({v['total_pips']:.0f} total pip)")

    return "\n".join(lines)


# ── CLI Test ──
if __name__ == "__main__":
    print("🧠 Learning Loop Engine")
    print(get_learning_summary())
