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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ASSET_CONFIG = {
    # PhantomFX standard: 1 pip = 0.10 untuk XAUUSD (Exness 3-digit)
    # Contoh: Entry 4458.500 → TP1 4446.500 = +120.0 pips (12.000 / 0.10)
    # SOP 30 pip = 30 × 0.10 = $3.00 SL distance
    "XAUUSD": {
        "pip_value": 0.10,  # Exness 3-digit: 1 pip = 0.10 ✅ sesuai PhantomFX
        "min_sl_pts": 28,  # Minimum SL (~30 pip SOP = $3.00)
        "max_sl_pts": 35,  # Max SL (35 pip = $3.50)
        "min_rr": 1.5,  # Minimum risk:reward
        "max_rr": 5.0,  # Maximum risk:reward
        "atr_period": 14,
        "sl_buffer_atr": 0.5,  # SL = structure + 0.5x ATR
        "entry_slip": 0.5,  # Entry slip in pips
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
        "pip_value": 0.01,  # USOIL = 0.01 per pip (Exness 3-digit)
        "min_sl_pts": 15,  # Minimum SL for oil
        "max_sl_pts": 25,  # Max SL cap
        "min_rr": 1.5,  # Minimum risk:reward
        "max_rr": 5.0,  # Maximum risk:reward
        "atr_period": 14,
        "sl_buffer_atr": 0.5,  # SL = structure + 0.5x ATR
        "entry_slip": 0.5,  # Entry slip in pips
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
        if action == "BUY" and macro == "BEARISH" or action == "SELL" and macro == "BULLISH":
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
    if action == "BUY" and macro == "BEARISH" or action == "SELL" and macro == "BULLISH":
        macro_ok = score >= 0.75  # Allow strong counter-trend
    checks["macro_alignment"] = {"passed": macro_ok, "macro": macro}

    # Overall
    passed = score_ok and align_ok and ct_ok and macro_ok and eng_ok

    if not passed:
        failures = [k for k, v in checks.items() if not v.get("passed", True)]
        reasons = {
            "consensus_threshold": f"Consensus {checks['consensus_threshold']['value'] * 100:.0f}% < 50%",
            "alignment": f"MTF alignment conflict ({alignment})",
            "counter_trend": f"Counter-trend: {flags[0] if flags else 'unknown'}",
            "macro_alignment": f"Action {action} vs macro {macro}",
            "engine_agreement": f"Only {checks['engine_agreement'].get('agree', 0)}/{checks['engine_agreement'].get('non_hold', 0)} non-HOLD engines agree (need 50%+)",
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
            "XAUUSD": 1.5,  # real M5 ATR ~$1-2
            "BTCUSD": 150.0,  # real M5 ATR ~$100-200
            "ETHUSD": 8.0,  # real M5 ATR ~$5-10
            "USOIL": 0.15,  # real M5 ATR ~$0.10-0.20
        }
        atr_val = atr_fallback.get(symbol, 1.0)
        LOG.info(f"{symbol}: ATR not found in engines, using fallback={atr_val}")

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
    macro_aligned = (action == "BUY" and macro == "BULLISH") or (
        action == "SELL" and macro == "BEARISH"
    )

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


LOG = logging.getLogger("signal_calculator_service")

WIB = timezone(timedelta(hours=7))
TRADE_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "trade_log.json",
)

# ---- Signal feed file path (mirrors tradebot.services.signal_service path) ----
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
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
            feed["stats"]["pending"] = max(0, feed["stats"].get("pending", 1) - 1)
            updated = True
            break
    if updated:
        _save_feed(feed)
        # ── Learning Engine: track outcome ──
        try:
            from tradebot.analytics.learning import record_trade_outcome
            trade = {
                "symbol": symbol.upper(),
                "entry": entry_price,
                "entry_price": entry_price,
                "pips": float(pips),
                "outcome": f"{result.upper()}_HIT" if result.upper() in ("TP", "SL") else result.upper(),
                "action": "BUY" if float(pips) >= 0 else "SELL",
                "grade": "B",
                "confidence": 50,
            }
            record_trade_outcome(trade, current_price=0)
        except Exception:
            pass


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
                round(agreeing_engines / non_hold_engines * 100) if non_hold_engines > 0 else 0
            )
            reason_parts.append(
                f"{agreeing_engines}/{total_engines} engines | {active_pct}% of active"
            )
        else:
            reason_parts.append(f"{agreeing_engines}/{total_engines} engines agree ({pct}%)")

    if flags:
        reason_parts.append(f"\u26a0\ufe0f {'; '.join(flags[:2])}")

    reason = " | ".join(reason_parts) if reason_parts else f"{action} signal"

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
        f"{grade_label} | <b>Conf {conf * 100:.0f}%</b>",
        "",
        f"\U0001f3af <b>Entry:</b> <code>${entry:.2f}</code>",
        f"\U0001f6d1 <b>SL:</b> <code>${sl:.2f}</code> | -{pips_sl}pt",
        f"\u2705 <b>TP1:</b> <code>${tp1:.2f}</code> | +{pips_target}pt",
        f"\u2705 <b>TP2:</b> <code>${tp2:.2f}</code> | +{pips_target * 2}pt",
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
