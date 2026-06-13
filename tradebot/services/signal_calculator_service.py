"""Signal calculator service — full signal computation pipeline.

Engine consensus -> quant alignment -> sequoia screening -> quality gate
-> level computation -> signal grading -> persistence -> formatting.

Ported from scripts/_legacy/signal_calculator.py and absorbed into
the tradebot.services layer.
"""

from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scripts._legacy.signal_calculator import (  # type: ignore[import-not-found]
    ASSET_CONFIG,
    DEFAULT_CONFIG,
    _calculate_levels,
    _grade_signal,
    _run_quality_gate,
)

LOG = logging.getLogger("signal_calculator_service")

WIB = timezone(timedelta(hours=7))
TRADE_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "trade_log.json",
)

# ---- Signal feed file path (mirrors tradebot.services.signal_service path) ----
DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
)
FEED_FILE = DATA_DIR / "signal_feed.json"
FEED_FILE.parent.mkdir(parents=True, exist_ok=True)
MAX_FEED_ENTRIES = 500


# =====================================================================
#  SIGNAL FEED PERSISTENCE
# =====================================================================


def _load_feed() -> dict:
    try:
        if FEED_FILE.exists():
            return json.loads(FEED_FILE.read_text())
    except Exception as e:
        LOG.warning("Silent exception caught: %s", e)
    return {"signals": [], "stats": {"total": 0, "tp": 0, "sl": 0, "pending": 0}}


def _save_feed(data: dict) -> None:
    try:
        FEED_FILE.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        LOG.warning("Silent exception caught: %s", e)


def add_signal(signal_data: dict) -> str:
    """Add a signal to the signal feed (JSON file).

    Returns the signal id.
    """
    import hashlib

    now = datetime.now(WIB)
    signal_id = hashlib.md5(
        f"{signal_data.get('symbol', '?')}|{signal_data.get('action', '?')}"
        f"|{signal_data.get('entry', 0)}|{now.isoformat()}".encode()
    ).hexdigest()[:12]

    entry_data = {
        "id": signal_id,
        "symbol": signal_data.get("symbol", "?").upper(),
        "direction": signal_data.get("action", "HOLD"),
        "entry": round(float(signal_data.get("entry", 0)), 2),
        "sl": round(float(signal_data.get("sl", 0)), 2),
        "tp": round(float(signal_data.get("tp1", 0)), 2),
        "confidence": round(float(signal_data.get("confidence", 0)), 2),
        "rr_ratio": str(signal_data.get("rr", "?")),
        "grade": signal_data.get("grade", ""),
        "engines": {},
        "source": "channel-auto",
        "source_user": "",
        "price_at_signal": round(float(signal_data.get("price", 0)), 2),
        "timestamp": now.isoformat(),
        "status": "pending",
        "outcome_pips": 0,
        "outcome_time": None,
    }

    feed = _load_feed()
    feed["signals"].append(entry_data)
    feed["stats"]["total"] = feed["stats"].get("total", 0) + 1
    feed["stats"]["pending"] = feed["stats"].get("pending", 0) + 1

    if len(feed["signals"]) > MAX_FEED_ENTRIES:
        feed["signals"] = feed["signals"][-MAX_FEED_ENTRIES:]

    _save_feed(feed)
    return signal_id


def update_outcome(symbol: str, entry_price: float, result: str, pips: float) -> None:
    """Update trade outcome (TP/SL) for a pending signal."""
    feed = _load_feed()
    updated = False
    sym = symbol.upper()
    if sym in ("XAUUSD", "GOLD"):
        threshold = 5.0
    elif sym in ("BTCUSD", "ETHUSD"):
        threshold = 200.0
    elif sym in ("USOIL", "OIL"):
        threshold = 0.3
    elif sym.endswith("JPY"):
        threshold = 0.5
    else:
        threshold = 0.005

    for sig in feed["signals"]:
        if (
            sig["symbol"] == symbol.upper()
            and abs(sig["entry"] - entry_price) < threshold
            and sig["status"] == "pending"
        ):
            sig["status"] = result.lower()
            sig["outcome_pips"] = round(float(pips), 1)
            sig["outcome_time"] = datetime.now(WIB).isoformat()
            if result.upper() == "TP":
                feed["stats"]["tp"] = feed["stats"].get("tp", 0) + 1
            else:
                feed["stats"]["sl"] = feed["stats"].get("sl", 0) + 1
            feed["stats"]["pending"] = max(
                0, feed["stats"].get("pending", 1) - 1
            )
            updated = True
            break
    if updated:
        _save_feed(feed)


def get_signals(limit: int = 20) -> list[dict]:
    """Get most recent signals for dashboard display."""
    feed = _load_feed()
    return feed["signals"][-limit:]


# =====================================================================
#  COMPUTE SIGNAL - FULL PIPELINE
# =====================================================================


def compute_signal(mtf_result: dict) -> dict | None:
    """Compute a complete signal from MTF matrix result.

    Full pipeline: engine consensus -> quant alignment -> sequoia
    screening -> quality gate -> level computation -> signal grading.

    Args:
        mtf_result: dict from run_engine_consensus()

    Returns:
        dict with signal details, or None if quality gate fails:
        {
            "action": "BUY" | "SELL",
            "entry": float,
            "sl": float,
            "tp1": float,
            "tp2": float,
            "rr": float,
            "grade": "A" | "B" | "C",
            "confidence": 0.0-1.0,
            "pips_target": int,
            "pips_sl": int,
            "reason": str,
            "quality_gate": { ... details ... },
            "timestamp": str,
            "symbol": str,
            "price": float,
        }
    """
    if not mtf_result:
        return None

    symbol = mtf_result.get("symbol", "XAUUSD")
    price = mtf_result.get("price", 0)
    hier = mtf_result.get("hierarchical", {})
    tfs = mtf_result.get("timeframes", {})

    if not price or not hier:
        return None

    # ---- Determine direction from hierarchical verdict ----
    verdict = hier.get("verdict", "HOLD")
    if verdict == "HOLD":
        return None

    action = verdict  # "BUY" or "SELL"
    macro = hier.get("macro_trend", "NEUTRAL")
    alignment = hier.get("mtf_alignment", "NONE")
    score = hier.get("consensus_score", 0)
    flags = hier.get("counter_trend_flags", [])

    # ---- Quality Gate ----
    qg = _run_quality_gate(mtf_result, action)
    if not qg["passed"]:
        LOG.info("Signal blocked by quality gate: %s", qg["reason"])
        return None

    # ---- Get config ----
    cfg = ASSET_CONFIG.get(symbol, DEFAULT_CONFIG)

    # ---- Calculate Entry, SL, TP ----
    levels = _calculate_levels(mtf_result, action, cfg)

    if not levels:
        return None

    entry = levels["entry"]
    sl = levels["sl"]
    tp1 = levels["tp1"]
    tp2 = levels["tp2"]
    rr = levels["rr"]
    pips_sl = levels["pips_sl"]
    pips_tp = levels["pips_tp"]

    # ---- Confidence grade ----
    grade, conf = _grade_signal(mtf_result, action, qg)

    # ---- Build reason string ----
    reason_parts: list[str] = []
    if grade == "A":
        reason_parts.append(f"MTF {alignment} | {macro}")
    elif grade == "B":
        reason_parts.append(f"{macro} bias")

    # Count engine agreement for the dominant TFs
    total_engines = 0
    agreeing_engines = 0
    non_hold_engines = 0
    for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
        tf = tfs.get(tf_name, {})
        for _eng_name, eng_data in tf.get("engines", {}).items():
            total_engines += 1
            direction = eng_data.get("direction")
            if direction == action:
                agreeing_engines += 1
            if direction in ("BUY", "SELL"):
                non_hold_engines += 1

    if total_engines > 0:
        pct = round(agreeing_engines / total_engines * 100)
        if non_hold_engines > 0 and non_hold_engines < total_engines * 0.8:
            active_pct = (
                round(agreeing_engines / non_hold_engines * 100)
                if non_hold_engines > 0
                else 0
            )
            reason_parts.append(
                f"{agreeing_engines}/{total_engines} engines | "
                f"{active_pct}% of active"
            )
        else:
            reason_parts.append(
                f"{agreeing_engines}/{total_engines} engines agree "
                f"({pct}%)"
            )

    if flags:
        reason_parts.append(f"\u26a0\ufe0f {'; '.join(flags[:2])}")

    reason = (
        " | ".join(reason_parts)
        if reason_parts
        else f"{action} signal"
    )

    # ---- Build result ----
    now = datetime.now(timezone(timedelta(hours=7)))
    ts = now.strftime("%Y-%m-%dT%H:%M:%S+07:00")

    return {
        "action": action,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(rr, 2),
        "grade": grade,
        "confidence": round(conf, 3),
        "pips_target": int(pips_tp),
        "pips_sl": int(pips_sl),
        "reason": reason,
        "quality_gate": qg,
        "timestamp": ts,
        "symbol": symbol,
        "price": round(price, 2),
        "macro_trend": macro,
        "mtf_alignment": alignment,
        "consensus_score": round(score, 3),
    }


# =====================================================================
#  PERSISTENCE
# =====================================================================


def log_signal(signal: dict) -> None:
    """Append signal to trade log for dashboard display. Uses atomic write."""
    try:
        log_path = os.path.normpath(TRADE_LOG_PATH)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        log: list[dict] = []
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    log = json.load(f)
            except Exception:
                log = []

        # Add signal with unique ID
        sig_id = f"sig_{int(datetime.now().timestamp() * 1000)}"
        entry = dict(signal)
        entry["id"] = sig_id
        log.append(entry)

        # Keep last 200
        if len(log) > 200:
            log = log[-200:]

        # Atomic write: write to temp, then rename (prevents corruption)
        tmp_path = log_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(log, f, indent=2)
        os.replace(tmp_path, log_path)  # Atomic on Linux

        LOG.info("Signal logged: %s %s %s", sig_id, signal["action"], signal["symbol"])
    except Exception as e:
        LOG.warning("Failed to log signal: %s", e)


# =====================================================================
#  FORMATTER
# =====================================================================


def format_signal_telegram(signal: dict) -> str:
    """Format signal for Telegram channel - Vilona aggressive style."""
    if not signal:
        return ""

    action = signal["action"]
    symbol = signal["symbol"]
    entry = signal["entry"]
    grade = signal["grade"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    rr = signal["rr"]
    pips_target = signal["pips_target"]
    pips_sl = signal["pips_sl"]
    conf = signal["confidence"]
    reason = html.escape(signal["reason"])
    macro = html.escape(signal.get("macro_trend", ""))
    align = html.escape(signal.get("mtf_alignment", ""))

    now = datetime.now(timezone(timedelta(hours=7)))
    wib = now.strftime("%Y.%m.%d %H:%M")

    emoji = "\U0001f7e2" if action == "BUY" else "\U0001f534"
    pair_emoji = (
        "\U0001f947"
        if symbol in ("XAUUSD",)
        else "\u20bf"
        if symbol in ("BTCUSD",)
        else "\U0001f6e2"
        if symbol in ("USOIL",)
        else "\U0001f4b1"
    )

    # Grade-specific intensity
    if grade == "A":
        grade_label = "\u26a1 S-TIER"
        callout = "\U0001f525 FULL SEND - HIGHEST CONVICTION SETUP. JANGAN TIDUR."
    elif grade == "B":
        grade_label = "\U0001f48e A-TIER"
        callout = "\u26a1 Valid entry area - tunggu konfirmasi M5 lalu GAS."
    else:
        grade_label = "\U0001f4cc SETUP"
        callout = "\U0001f4cc Standard setup - atur risk management ketat."

    # RR label
    if rr >= 2.0:
        rr_label = f"\U0001f4b0 RR 1:{rr} - JUICY!"
    elif rr >= 1.5:
        rr_label = f"\U0001f4ca RR 1:{rr} - decent"
    else:
        rr_label = f"\U0001f4ca RR 1:{rr}"

    lines = [
        f"{emoji} <b>{action} {symbol}</b> {pair_emoji}",
        "\u2501" * 22,
        f"\U0001f550 {wib} WIB",
        f"{grade_label} | <b>Conf {conf*100:.0f}%</b>",
        "",
        f"\U0001f3af <b>Entry:</b> <code>${entry:.2f}</code>",
        f"\U0001f6d1 <b>SL:</b> <code>${sl:.2f}</code> | -{pips_sl}pt",
        f"\u2705 <b>TP1:</b> <code>${tp1:.2f}</code> | +{pips_target}pt",
        f"\u2705 <b>TP2:</b> <code>${tp2:.2f}</code> | +{pips_target*2}pt",
        f"{rr_label}",
        "",
        "\u2501" * 22,
        f"\U0001f3db {macro} | {align}",
        f"\U0001f9e0 <i>{reason[:250]}</i>",
        "\u2501" * 22,
        f"{callout}",
        "",
        "\u26a0\ufe0f <i>Risk 1% per trade. Full AI - verify sendiri.</i>",
        "\U0001f49a Server GRATIS -> /subscribe | @berkahkaryaforexbotbot",
    ]

    return "\n".join(lines)


__all__ = [
    "add_signal",
    "compute_signal",
    "format_signal_telegram",
    "get_signals",
    "log_signal",
    "update_outcome",
]