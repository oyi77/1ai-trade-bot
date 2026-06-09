#!/usr/bin/env python3
"""
Signal Calculator — MTF-driven signal generation with TP/SL
============================================================
Takes MTF matrix output from engine_consensus → generates:
  - Entry price (structure-based limit order)
  - Stop Loss (ATR + structure + asset-specific minimums)
  - Take Profit 1 & 2 (RR-based)
  - Confidence grade (A/B/C)
  - Quality gate validation
"""

import html
import logging
import math
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("signal_calculator")

# ═══════════════════════════════════════════════════════════════════
#  ASSET CONFIG
# ═══════════════════════════════════════════════════════════════════

ASSET_CONFIG = {
    # PhantomFX standard: 1 pip = 0.10 untuk XAUUSD (Exness 3-digit)
    # Contoh: Entry 4458.500 → TP1 4446.500 = +120.0 pips (12.000 / 0.10)
    # SOP 30 pip = 30 × 0.10 = $3.00 SL distance
    "XAUUSD": {
        "pip_value": 0.10,      # Exness 3-digit: 1 pip = 0.10 ✅ sesuai PhantomFX
        "min_sl_pts": 28,       # Minimum SL (~30 pip SOP = $3.00)
        "max_sl_pts": 35,       # Max SL (35 pip = $3.50)
        "min_rr": 1.5,          # Minimum risk:reward
        "max_rr": 5.0,          # Maximum risk:reward
        "atr_period": 14,
        "sl_buffer_atr": 0.5,   # SL = structure + 0.5x ATR
        "entry_slip": 0.5,      # Entry slip in pips
    },
    "BTCUSD": {
        "pip_value": 0.1,
        "min_sl_pts": 600,
        "max_sl_pts": 800,
        "min_rr": 1.5,
        "max_rr": 5.0,
        "atr_period": 14,
        "sl_buffer_atr": 0.3,
        "entry_slip": 5.0,
    },
    "ETHUSD": {
        "pip_value": 0.01,
        "min_sl_pts": 50,
        "max_sl_pts": 80,
        "min_rr": 1.5,
        "max_rr": 5.0,
        "atr_period": 14,
        "sl_buffer_atr": 0.3,
        "entry_slip": 2.0,
    },
    "USOIL": {
        "pip_value": 0.01,      # USOIL = 0.01 per pip (Exness 3-digit)
        "min_sl_pts": 15,       # Minimum SL for oil
        "max_sl_pts": 25,       # Max SL cap
        "min_rr": 1.5,          # Minimum risk:reward
        "max_rr": 5.0,          # Maximum risk:reward
        "atr_period": 14,
        "sl_buffer_atr": 0.5,   # SL = structure + 0.5x ATR
        "entry_slip": 0.5,      # Entry slip in pips
    },
}

DEFAULT_CONFIG = {
    "pip_value": 0.01,
    "min_sl_pts": 32,
    "min_rr": 1.5,
    "max_rr": 5.0,
    "atr_period": 14,
    "sl_buffer_atr": 0.5,
    "entry_slip": 0.5,
}

# ═══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════


def compute_signal(mtf_result: dict) -> dict | None:
    """
    Compute a complete signal from MTF matrix result.

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

    # ── Determine direction from hierarchical verdict ──
    verdict = hier.get("verdict", "HOLD")
    if verdict == "HOLD":
        return None

    action = verdict  # "BUY" or "SELL"
    macro = hier.get("macro_trend", "NEUTRAL")
    alignment = hier.get("mtf_alignment", "NONE")
    score = hier.get("consensus_score", 0)
    flags = hier.get("counter_trend_flags", [])

    # ── Quality Gate ──
    qg = _run_quality_gate(mtf_result, action)
    if not qg["passed"]:
        logger.info(f"Signal blocked by quality gate: {qg['reason']}")
        return None

    # ── Get config ──
    cfg = ASSET_CONFIG.get(symbol, DEFAULT_CONFIG)

    # ── Calculate Entry, SL, TP ──
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

    # ── Confidence grade ──
    grade, conf = _grade_signal(mtf_result, action, qg)

    # ── Build reason string ──
    reason_parts = []
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
        for eng_name, eng_data in tf.get("engines", {}).items():
            total_engines += 1
            direction = eng_data.get("direction")
            if direction == action:
                agreeing_engines += 1
            if direction in ("BUY", "SELL"):
                non_hold_engines += 1

    if total_engines > 0:
        pct = round(agreeing_engines / total_engines * 100)
        # Show active participation if many HOLD
        if non_hold_engines > 0 and non_hold_engines < total_engines * 0.8:
            active_pct = round(agreeing_engines / non_hold_engines * 100) if non_hold_engines > 0 else 0
            reason_parts.append(f"{agreeing_engines}/{total_engines} engines | {active_pct}% of active")
        else:
            reason_parts.append(f"{agreeing_engines}/{total_engines} engines agree ({pct}%)")

    if flags:
        reason_parts.append(f"⚠️ {'; '.join(flags[:2])}")

    reason = " | ".join(reason_parts) if reason_parts else f"{action} signal"

    # ── Build result ──
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


# ═══════════════════════════════════════════════════════════════════
#  QUALITY GATE
# ═══════════════════════════════════════════════════════════════════


def _run_quality_gate(mtf_result: dict, action: str) -> dict:
    """
    Run quality checks. Returns {passed: bool, checks: dict, reason: str}

    Checks:
    1. Consensus score > threshold (50%)
    2. MTF alignment not CONFLICT
    3. No counter-trend without strong evidence
    4. Minimum engine agreement (50%)
    """
    hier = mtf_result.get("hierarchical", {})
    score = hier.get("consensus_score", 0)
    alignment = hier.get("mtf_alignment", "NONE")
    flags = hier.get("counter_trend_flags", [])
    macro = hier.get("macro_trend", "NEUTRAL")
    tfs = mtf_result.get("timeframes", {})

    checks = {}

    # Count engine agreement
    total_eng = 0
    agree_eng = 0
    for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
        tf = tfs.get(tf_name, {})
        engines = tf.get("engines", {})
        for eng_name, eng_data in engines.items():
            total_eng += 1
            if eng_data.get("direction") == action:
                agree_eng += 1

    # Check 1: Consensus score threshold (50%)
    score_ok = score >= 0.50
    checks["consensus_threshold"] = {"passed": score_ok, "value": round(score, 3), "min": 0.50}

    # Check 2: MTF alignment
    align_ok = alignment != "CONFLICT"
    checks["alignment"] = {"passed": align_ok, "value": alignment}

    # Check 3: Counter-trend
    ct_ok = True
    if flags:
        if action == "BUY" and macro == "BEARISH":
            ct_ok = score >= 0.75
        elif action == "SELL" and macro == "BULLISH":
            ct_ok = score >= 0.75
        elif macro == "NEUTRAL":
            ct_ok = False
    checks["counter_trend"] = {"passed": ct_ok, "flags": flags[:2] if flags else []}

    # Check 4: Minimum engine agreement (50% of active voters)
    # HOLD = abstain, not rejection. Only count engines with BUY/SELL opinion.
    eng_ok = True
    if total_eng > 0:
        # Count non-HOLD engines (those with BUY/SELL opinion)
        non_hold = 0
        for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
            tf = tfs.get(tf_name, {})
            engines = tf.get("engines", {})
            for eng_data in engines.values():
                d = eng_data.get("direction")
                if d in ("BUY", "SELL"):
                    non_hold += 1
        
        # Requirement 1: at least 30% of total engines must have an opinion
        participation_ok = non_hold >= max(6, total_eng * 0.3)
        # Requirement 2: of those with opinion, at least 50% must agree with action
        agree_pct = agree_eng / non_hold if non_hold > 0 else 0
        agreement_ok = agree_pct >= 0.50
        
        eng_ok = participation_ok and agreement_ok
        checks["engine_agreement"] = {
            "passed": eng_ok,
            "agree_pct": round(agree_pct, 3),
            "agree": agree_eng,
            "total": total_eng,
            "non_hold": non_hold,
            "participation_ok": participation_ok,
            "agreement_ok": agreement_ok,
            "min_participation": 0.30,
            "min_agreement": 0.50,
        }

    # Check 4: Macro vs action alignment
    macro_ok = True
    if action == "BUY" and macro == "BEARISH":
        macro_ok = score >= 0.75  # Allow strong counter-trend
    elif action == "SELL" and macro == "BULLISH":
        macro_ok = score >= 0.75  # Allow strong counter-trend
    checks["macro_alignment"] = {"passed": macro_ok, "macro": macro}

    # Overall
    passed = score_ok and align_ok and ct_ok and macro_ok and eng_ok

    if not passed:
        failures = [k for k, v in checks.items() if not v.get("passed", True)]
        reasons = {
            "consensus_threshold": f"Consensus {checks['consensus_threshold']['value']*100:.0f}% < 50%",
            "alignment": f"MTF alignment conflict ({alignment})",
            "counter_trend": f"Counter-trend: {flags[0] if flags else 'unknown'}",
            "macro_alignment": f"Action {action} vs macro {macro}",
            "engine_agreement": f"Only {checks['engine_agreement'].get('agree',0)}/{checks['engine_agreement'].get('non_hold',0)} non-HOLD engines agree (need 50%+)",
        }
        first_fail = failures[0]
        reason = reasons.get(first_fail, "Quality gate failed")
    else:
        reason = "All checks passed"

    return {
        "passed": passed,
        "checks": checks,
        "reason": reason,
    }


# ═══════════════════════════════════════════════════════════════════
#  LEVEL CALCULATOR
# ═══════════════════════════════════════════════════════════════════


def _calculate_levels(mtf_result: dict, action: str, cfg: dict) -> dict | None:
    """Calculate entry, SL, TP1, TP2 from structure + ATR."""
    price = mtf_result.get("price", 0)
    symbol = mtf_result.get("symbol", "XAUUSD")
    tfs = mtf_result.get("timeframes", {})

    if not price:
        return None

    # ── Get ATR from M5 or M15 (most granular available) ──
    atr_val = _get_atr_from_tf(tfs, "M5")
    if not atr_val:
        atr_val = _get_atr_from_tf(tfs, "M15")
    if not atr_val:
        atr_val = _get_atr_from_tf(tfs, "H1")
    if not atr_val or atr_val <= 0:
        # Asset-specific M5 ATR fallback — realistis, bukan 10.0
        atr_fallback = {
            "XAUUSD": 1.5,    # real M5 ATR ~$1-2
            "BTCUSD": 150.0,  # real M5 ATR ~$100-200
            "ETHUSD": 8.0,    # real M5 ATR ~$5-10
            "USOIL": 0.15,    # real M5 ATR ~$0.10-0.20
        }
        atr_val = atr_fallback.get(symbol, 1.0)
        logger.info(f"{symbol}: ATR not found in engines, using fallback={atr_val}")

    # ── Get structure levels ──
    support = None
    resistance = None

    # Check M15 for structure (most relevant for entry)
    m15 = tfs.get("M15", {})
    m15_struct = m15.get("structure", {})
    if m15_struct:
        support = m15_struct.get("s1") or support
        resistance = m15_struct.get("r1") or resistance

    # Fallback to H1
    if not support or not resistance:
        h1 = tfs.get("H1", {})
        h1_struct = h1.get("structure", {})
        support = support or h1_struct.get("s1") or (price - atr_val * 2)
        resistance = resistance or h1_struct.get("r1") or (price + atr_val * 2)

    if not support:
        support = price - atr_val * 2
    if not resistance:
        resistance = price + atr_val * 2

    slip = cfg.get("entry_slip", 0.5)

    # ── EA MARKET EXECUTION ──
    # EA Exness MAU EKSEKUSI SEKARANG, bukan pending order.
    # Entry = harga saat ini, SL/TP dihitung dari entry.
    # Struktur/resistance/support cuma dipake buat validasi arah, BUKAN entry price.
    entry = price

    if action == "BUY":
        # SL: di bawah entry — pakai max(ATR buffer, min SOP)
        sl_buffer = max(atr_val * cfg["sl_buffer_atr"], cfg["min_sl_pts"] * cfg["pip_value"])
        raw_sl = entry - sl_buffer
        # Apply min SL
        pips_sl_raw = abs(entry - raw_sl) / cfg["pip_value"]
        min_sl_pips = cfg["min_sl_pts"]
        if pips_sl_raw < min_sl_pips:
            raw_sl = entry - (min_sl_pips * cfg["pip_value"])
        sl = raw_sl
        # Cap SL sesuai max_sl_pts SOP
        max_sl_pips = cfg.get("max_sl_pts", 50)
        pips_sl = abs(entry - sl) / cfg["pip_value"]
        if pips_sl > max_sl_pips:
            sl = entry - (max_sl_pips * cfg["pip_value"])
        pips_sl = abs(entry - sl) / cfg["pip_value"]
        tp1_price = entry + (pips_sl * cfg["min_rr"] * cfg["pip_value"])  # 1:1.5
        tp2_price = entry + (pips_sl * cfg["min_rr"] * 2 * cfg["pip_value"])  # 1:3

    else:  # SELL
        sl_buffer = max(atr_val * cfg["sl_buffer_atr"], cfg["min_sl_pts"] * cfg["pip_value"])
        raw_sl = entry + sl_buffer
        pips_sl_raw = abs(raw_sl - entry) / cfg["pip_value"]
        min_sl_pips = cfg["min_sl_pts"]
        if pips_sl_raw < min_sl_pips:
            raw_sl = entry + (min_sl_pips * cfg["pip_value"])
        sl = raw_sl
        # Cap SL sesuai max_sl_pts SOP
        max_sl_pips = cfg.get("max_sl_pts", 50)
        pips_sl = abs(sl - entry) / cfg["pip_value"]
        if pips_sl > max_sl_pips:
            sl = entry + (max_sl_pips * cfg["pip_value"])
        pips_sl = abs(sl - entry) / cfg["pip_value"]
        tp1_price = entry - (pips_sl * cfg["min_rr"] * cfg["pip_value"])
        tp2_price = entry - (pips_sl * cfg["min_rr"] * 2 * cfg["pip_value"])

    # Compute RR
    pips_target = abs(entry - tp1_price) / cfg["pip_value"]
    rr = round(pips_target / pips_sl, 2) if pips_sl > 0 else 0

    # Validate RR
    min_rr = cfg["min_rr"]
    max_rr = cfg["max_rr"]
    if rr < min_rr:
        # Adjust TP1 to meet minimum RR
        if action == "BUY":
            tp1_price = entry + (pips_sl * min_rr * cfg["pip_value"])
        else:
            tp1_price = entry - (pips_sl * min_rr * cfg["pip_value"])
        pips_target = abs(entry - tp1_price) / cfg["pip_value"]
        rr = min_rr

    if rr > max_rr:
        rr = max_rr
        # Cap TP
        if action == "BUY":
            tp1_price = entry + (pips_sl * max_rr * cfg["pip_value"])
        else:
            tp1_price = entry - (pips_sl * max_rr * cfg["pip_value"])
        pips_target = abs(entry - tp1_price) / cfg["pip_value"]

    # TP2 = 2x TP1 from entry
    if action == "BUY":
        tp2_price = entry + (pips_target * 2 * cfg["pip_value"])
    else:
        tp2_price = entry - (pips_target * 2 * cfg["pip_value"])

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1_price,
        "tp2": tp2_price,
        "rr": rr,
        "pips_sl": int(pips_sl),
        "pips_tp": int(pips_target),
    }


def _get_atr_from_tf(tfs: dict, tf_name: str) -> float | None:
    """Extract ATR from a timeframe's engines."""
    tf = tfs.get(tf_name, {})
    for eng in tf.get("engines", {}).values():
        ind = eng.get("indicators", {})
        if isinstance(ind, dict):
            atr = ind.get("atr")
            if atr is not None:
                return float(atr)
    return None


# ═══════════════════════════════════════════════════════════════════
#  GRADE CALCULATOR
# ═══════════════════════════════════════════════════════════════════


def _grade_signal(mtf_result: dict, action: str, qg: dict) -> tuple:
    """
    Determine signal grade (A/B/C) and confidence.

    Grade A: MTF ALIGNED, score >= 80%, macro aligned
    Grade B: MTF MIXED, score >= 50%, macro aligned
    Grade C: All other valid signals
    """
    hier = mtf_result.get("hierarchical", {})
    score = hier.get("consensus_score", 0)
    alignment = hier.get("mtf_alignment", "NONE")
    macro = hier.get("macro_trend", "NEUTRAL")
    flags = hier.get("counter_trend_flags", [])
    tfs = mtf_result.get("timeframes", {})

    # Count engines
    total_eng = 0
    agree_eng = 0
    for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
        tf = tfs.get(tf_name, {})
        for en, ed in tf.get("engines", {}).items():
            total_eng += 1
            if ed.get("direction") == action:
                agree_eng += 1

    eng_pct = agree_eng / total_eng if total_eng > 0 else 0

    # Check macro alignment
    macro_aligned = (action == "BUY" and macro == "BULLISH") or \
                    (action == "SELL" and macro == "BEARISH")

    counter_trend = len(flags) > 0

    if alignment == "ALIGNED" and score >= 0.80 and macro_aligned and eng_pct >= 0.65:
        grade = "A"
        confidence = min(0.65 + score * 0.35, 0.95)
    elif alignment in ("ALIGNED", "MIXED") and score >= 0.50 and not counter_trend:
        grade = "B"
        confidence = 0.5 + score * 0.3
    else:
        grade = "C"
        confidence = 0.4 + score * 0.3

    return grade, min(confidence, 0.98)


# ═══════════════════════════════════════════════════════════════════
#  FORMATTER
# ═══════════════════════════════════════════════════════════════════


def _get_order_type(action: str, entry: float, price: float, threshold: float = 0.5) -> str:
    """Determine pending order type based on entry vs current price.
    
    For SELL:
      entry > price → SELL LIMIT (nunggu harga naik)
      entry < price → SELL STOP  (kejar harga turun)
      entry ≈ price → SELL
      
    For BUY:
      entry < price → BUY LIMIT (nunggu harga turun)
      entry > price → BUY STOP  (kejar harga naik)
      entry ≈ price → BUY
    """
    diff = entry - price
    if abs(diff) <= threshold:
        return action  # MARKET / near market
    if action == "SELL":
        return "SELL LIMIT" if diff > 0 else "SELL STOP"
    else:  # BUY
        return "BUY LIMIT" if diff < 0 else "BUY STOP"


def format_signal_telegram(signal: dict) -> str:
    """Format signal for Telegram channel posting."""
    if not signal:
        return ""

    action = signal["action"]
    symbol = signal["symbol"]
    entry = signal["entry"]
    price = signal.get("price", entry)
    order_type = action  # EA market execution — selalu MARKET
    emoji = "🟢" if action == "BUY" else "🔴"
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

    lines = [
        f"{emoji} <b>{order_type} {symbol}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {wib} WIB | Grade: <b>{grade}</b> | Conf: {conf*100:.0f}%",
        f"",
        f"<b>🎯 Entry:</b> ${entry:.2f}",
        f"<b>🛑 SL:</b> ${sl:.2f} ({pips_sl}pt)",
        f"<b>✅ TP1:</b> ${tp1:.2f} (+{pips_target}pt)",
        f"<b>✅ TP2:</b> ${tp2:.2f} (+{pips_target*2}pt)",
        f"<b>📊 RR:</b> 1:{rr}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🏛 {macro} | {align}",
        f"📈 {reason}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if grade == "A":
        lines.append(f"🔥 <b>HIGH CONVICTION</b> — siap eksekusi!")
    elif grade == "B":
        lines.append(f"⚡ Signal valid — pantau entry area")
    else:
        lines.append(f"📌 Sinyal standar — atur risk management")

    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  PERSISTENCE
# ═══════════════════════════════════════════════════════════════════

TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade_log.json")


def log_signal(signal: dict):
    """Append signal to trade log for dashboard display. Uses atomic write."""
    try:
        log_path = os.path.normpath(TRADE_LOG_PATH)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        log = []
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
        os.replace(tmp_path, log_path)  # Atomic on Linux (same filesystem)

        logger.info(f"Signal logged: {sig_id} {signal['action']} {signal['symbol']}")
    except Exception as e:
        logger.warning(f"Failed to log signal: {e}")


# ═══════════════════════════════════════════════════════════════════
#  SELF-TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Simulate MTF result to test
    from engine_consensus import run_engine_consensus

    result = run_engine_consensus(symbol="XAUUSD")
    if result:
        signal = compute_signal(result)
        if signal:
            print(f"✅ SIGNAL GENERATED:")
            print(f"   Action: {signal['action']} | Grade: {signal['grade']}")
            print(f"   Entry: ${signal['entry']:.2f}")
            print(f"   SL: ${signal['sl']:.2f} | TP1: ${signal['tp1']:.2f} | TP2: ${signal['tp2']:.2f}")
            print(f"   RR: 1:{signal['rr']} | Conf: {signal['confidence']*100:.0f}%")
            print(f"   Reason: {signal['reason']}")
            print(f"   QG: {signal['quality_gate']['passed']}")
            print(f"\n{format_signal_telegram(signal)}")
        else:
            print("❌ No signal — quality gate blocked")
            # Show what blocked
            sym_result = run_engine_consensus(symbol="XAUUSD")
            if sym_result:
                hier = sym_result.get("hierarchical", {})
                print(f"   Verdict: {hier.get('verdict')} | Score: {hier.get('consensus_score',0)*100:.0f}%")
                print(f"   Alignment: {hier.get('mtf_alignment')} | Flags: {hier.get('counter_trend_flags')}")
            else:
                print("   (engine_consensus returned None)")
    else:
        print("❌ No MTF result")
