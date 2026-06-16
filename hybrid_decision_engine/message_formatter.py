"""
Message Formatter — Hybrid Decision Engine
===========================================
Builds clean, professional Telegram messages from signal JSON.

Shadow Mode: Messages prefixed with 🔬 to distinguish from production signals.
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))


def format_signal_message(data: dict, shadow_mode: bool = True) -> str:
    """
    Build a Telegram-ready message from hybrid_signal.json content.

    Args:
        data: Parsed JSON dict from hybrid_signal.json
        shadow_mode: If True, prefix with 🔬 and "SHADOW" tag

    Returns:
        HTML-formatted string for Telegram parse_mode=HTML
    """
    decision = data.get("decision", {})
    signal = decision.get("signal")
    grade = decision.get("grade", "D")
    mode = decision.get("mode", "UNKNOWN")
    confidence = decision.get("confidence", 0)
    symbol = decision.get("symbol", "???")
    timeframe = decision.get("timeframe", "?")
    current_price = decision.get("current_price", 0)
    sl = decision.get("sl")
    tp = decision.get("tp")
    sl_pips = decision.get("sl_pips")
    tp_pips = decision.get("tp_pips")
    risk_reward = decision.get("risk_reward")
    reasoning = decision.get("reasoning", "N/A")
    integrity = decision.get("integrity_status", "?")
    timestamp = decision.get("generated_at") or data.get("generated_at", "")

    # ── Header ──
    if shadow_mode:
        header = "🔬 <b>HYBRID ENGINE — SHADOW MODE</b>"
    else:
        header = "⚡ <b>HYBRID ENGINE — LIVE</b>"

    # ── Action emoji ──
    if signal == "BUY":
        action_emoji = "🟢"
        action_text = "BUY"
    elif signal == "SELL":
        action_emoji = "🔴"
        action_text = "SELL"
    else:
        action_emoji = "⚪"
        action_text = "NO SIGNAL"

    # ── Grade badge ──
    grade_badge = _grade_badge(grade)

    # ── Confidence bar ──
    conf_bar = _confidence_bar(confidence)

    # ── Build message ──
    lines = [
        header,
        "",
        f"{action_emoji} <b>{action_text}</b> {symbol} <code>{timeframe}</code>",
        f"Grade: {grade_badge}  |  Mode: <code>{mode}</code>",
        "",
        f"💰 <b>Entry:</b> <code>{current_price:,.2f}</code>",
    ]

    if sl:
        lines.append(f"🛑 <b>SL:</b> <code>{sl:,.2f}</code>" + (f" ({sl_pips} pips)" if sl_pips else ""))
    if tp:
        lines.append(f"🎯 <b>TP:</b> <code>{tp:,.2f}</code>" + (f" ({tp_pips} pips)" if tp_pips else ""))
    if risk_reward:
        lines.append(f"⚖️ <b>R:R:</b> <code>1:{risk_reward}</code>")

    lines.extend([
        "",
        f"📊 <b>Confidence:</b> {conf_bar} <code>{confidence:.1%}</code>",
        f"🛡️ <b>Integrity:</b> {integrity}",
        "",
        f"📝 <i>{reasoning[:200]}</i>",
    ])

    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp)
            lines.append(f"\n🕐 {dt.strftime('%Y-%m-%d %H:%M:%S')} WIB")
        except (ValueError, TypeError):
            pass

    lines.append(f"\n<i>v{data.get('version', '?')} | Hybrid Decision Engine</i>")

    return "\n".join(lines)


def format_no_signal_message(data: dict, shadow_mode: bool = True) -> Optional[str]:
    """
    Build a message for no-signal decisions (only in debug/shadow mode).
    Returns None in live mode to avoid spam.
    """
    decision = data.get("decision", {})
    mode = decision.get("mode", "UNKNOWN")
    reasoning = decision.get("reasoning", "")
    symbol = decision.get("symbol", "???")

    if shadow_mode and mode in ("SOLO_BLOCKED", "CONFLICT", "BLOCKED", "NO_DATA"):
        return (
            f"🔬 <b>SHADOW — No Signal</b>\n"
            f"{symbol} | <code>{mode}</code>\n"
            f"<i>{reasoning[:150]}</i>"
        )
    return None


def _grade_badge(grade: str) -> str:
    """HTML badge for signal grade."""
    badges = {
        "A": "🅰️ <b>A</b> (Premium)",
        "B": "🅱️ <b>B</b> (Strong)",
        "C": "©️ <b>C</b> (Moderate)",
        "D": "🅳 <b>D</b> (Weak)",
    }
    return badges.get(grade, f"<b>{grade}</b>")


def _confidence_bar(confidence: float) -> str:
    """Visual confidence bar using Unicode blocks."""
    filled = int(confidence * 10)
    empty = 10 - filled
    if confidence >= 0.85:
        color = "🟩"
    elif confidence >= 0.70:
        color = "🟨"
    elif confidence >= 0.50:
        color = "🟧"
    else:
        color = "🟥"
    return color * filled + "⬜" * empty
