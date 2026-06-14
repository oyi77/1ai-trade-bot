"""Quality Gate — signal validation, level calculation, grading, and formatting.

Absorbed from ``scripts/signal_calculator.py`` and adapted to work with
the ``Signal`` dataclass instead of raw dicts.

Flow:
  1. ``validate(signal)`` — quality gate checks (consensus, alignment, etc.)
  2. ``compute_levels(signal)`` — ATR-based TP/SL written into signal metadata
  3. ``grade(signal)`` — A/B/C grading based on confluence
  4. ``format_telegram(signal)`` — Telegram channel message
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from tradebot.logging import get_logger
from tradebot.models import Signal

LOG = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  ASSET CONFIG
# ═══════════════════════════════════════════════════════════════════

ASSET_CONFIG: dict[str, dict] = {
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

DEFAULT_CONFIG: dict = {
    "pip_value": 0.01,
    "min_sl_pts": 32,
    "min_rr": 1.5,
    "max_rr": 5.0,
    "atr_period": 14,
    "sl_buffer_atr": 0.5,
    "entry_slip": 0.5,
}

# Asset-specific M5 ATR fallback values (realistic ranges)
_ATR_FALLBACK: dict[str, float] = {
    "XAUUSD": 1.5,    # real M5 ATR ~$1-2
    "BTCUSD": 150.0,  # real M5 ATR ~$100-200
    "ETHUSD": 8.0,    # real M5 ATR ~$5-10
    "USOIL": 0.15,    # real M5 ATR ~$0.10-0.20
}

# Canonical timeframe search order for ATR extraction
_TF_ORDER = ("M5", "M15", "H1", "H4", "D1")


class QualityGate:
    """Signal validation, ATR-based level calculation, grading, and formatting.

    Works with the ``Signal`` dataclass.  MTF engine data is expected
    inside ``signal.metadata`` under these keys:

    * ``consensus_score``   — float 0-1
    * ``mtf_alignment``     — "ALIGNED" | "MIXED" | "CONFLICT" | "NONE"
    * ``macro_trend``       — "BULLISH" | "BEARISH" | "NEUTRAL"
    * ``counter_trend_flags`` — list[str]
    * ``timeframes``        — dict mapping TF name → engine/structure data
    * ``current_price``     — float (market price at signal time)
    """

    # ------------------------------------------------------------------
    #  Validation (quality gate)
    # ------------------------------------------------------------------

    def validate(self, signal: Signal) -> bool:
        """Run quality gate checks. Returns True if signal passes.

        Checks:
        1. Consensus score >= 50%
        2. MTF alignment is not CONFLICT
        3. Counter-trend guard (requires 75% score for counter-trend)
        4. Minimum engine agreement (50% of non-HOLD engines)
        5. Macro alignment guard
        """
        meta = signal.metadata
        direction = signal.direction  # "CALL" → BUY, "PUT" → SELL
        action = "BUY" if direction in ("CALL", "BUY") else "SELL"

        hier_score = meta.get("consensus_score", 0.0)
        alignment = meta.get("mtf_alignment", "NONE")
        flags = meta.get("counter_trend_flags", [])
        macro = meta.get("macro_trend", "NEUTRAL")
        tfs = meta.get("timeframes", {})

        # ── Check 1: consensus score threshold (50%) ──
        score_ok = hier_score >= 0.50

        # ── Check 2: MTF alignment ──
        align_ok = alignment != "CONFLICT"

        # ── Check 3: counter-trend guard ──
        ct_ok = True
        if flags:
            if (
                (action == "BUY" and macro == "BEARISH")
                or (action == "SELL" and macro == "BULLISH")
            ):
                ct_ok = hier_score >= 0.75
            elif macro == "NEUTRAL":
                ct_ok = False

        # ── Check 4: engine agreement ──
        eng_ok = True
        total_eng = 0
        agree_eng = 0
        non_hold = 0
        for tf_name in _TF_ORDER:
            tf = tfs.get(tf_name, {})
            for eng_data in tf.get("engines", {}).values():
                total_eng += 1
                d = eng_data.get("direction")
                if d in ("BUY", "SELL"):
                    non_hold += 1
                if d == action:
                    agree_eng += 1

        if total_eng > 0:
            participation_ok = non_hold >= max(6, total_eng * 0.3)
            agree_pct = agree_eng / non_hold if non_hold > 0 else 0.0
            agreement_ok = agree_pct >= 0.50
            eng_ok = participation_ok and agreement_ok

        # ── Check 5: macro alignment ──
        macro_ok = True
        if (action == "BUY" and macro == "BEARISH") or (action == "SELL" and macro == "BULLISH"):
            macro_ok = hier_score >= 0.75

        passed = score_ok and align_ok and ct_ok and macro_ok and eng_ok

        if not passed:
            # Build diagnostic reason
            if not score_ok:
                reason = f"Consensus {hier_score*100:.0f}% < 50%"
            elif not align_ok:
                reason = f"MTF alignment conflict ({alignment})"
            elif not ct_ok:
                reason = f"Counter-trend: {flags[0] if flags else 'unknown'}"
            elif not macro_ok:
                reason = f"Action {action} vs macro {macro}"
            elif not eng_ok:
                reason = (
                    f"Only {agree_eng}/{non_hold} non-HOLD engines agree (need 50%+)"
                )
            else:
                reason = "Quality gate failed"
            LOG.info("Signal blocked by quality gate: %s", reason)

        # Store check details back into metadata for downstream use
        signal.metadata["quality_gate"] = {
            "passed": passed,
            "checks": {
                "consensus_threshold": {
                    "passed": score_ok,
                    "value": round(hier_score, 3),
                    "min": 0.50,
                },
                "alignment": {"passed": align_ok, "value": alignment},
                "counter_trend": {
                    "passed": ct_ok,
                    "flags": flags[:2] if flags else [],
                },
                "macro_alignment": {"passed": macro_ok, "macro": macro},
                "engine_agreement": {
                    "passed": eng_ok,
                    "agree": agree_eng,
                    "total": total_eng,
                    "non_hold": non_hold,
                    "agree_pct": (
                        round(agree_eng / non_hold, 3) if non_hold > 0 else 0.0
                    ),
                },
            },
        }

        return passed

    # ------------------------------------------------------------------
    #  Level calculation
    # ------------------------------------------------------------------

    def compute_levels(self, signal: Signal) -> bool:
        """Compute entry, SL, TP1, TP2 and write them into signal metadata.

        Returns True if levels were computed successfully, False if
        required data is missing.

        Requires ``signal.metadata["current_price"]`` to be set.
        """
        meta = signal.metadata
        price = meta.get("current_price", 0.0)
        symbol = signal.symbol
        action = "BUY" if signal.direction in ("CALL", "BUY") else "SELL"

        if not price:
            LOG.warning("compute_levels: no current_price in metadata for %s", symbol)
            return False

        cfg = ASSET_CONFIG.get(symbol, DEFAULT_CONFIG)
        tfs = meta.get("timeframes", {})
        # ── Get ATR from M5 → M15 → H1 fallback ──
        atr_val = self._get_atr(tfs)
        if not atr_val or atr_val <= 0:
            atr_val = _ATR_FALLBACK.get(symbol) or (price * 0.005 if price else 1.0)
            LOG.info("%s: ATR not found in engines, using dynamic fallback=%.2f", symbol, atr_val)

        # ── Entry = current market price (EA market execution) ──
        entry = price

        # ── Calculate SL, TP1, TP2 ──
        if action == "BUY":
            sl_buffer = max(
                atr_val * cfg["sl_buffer_atr"],
                cfg["min_sl_pts"] * cfg["pip_value"],
            )
            raw_sl = entry - sl_buffer
            pips_sl_raw = abs(entry - raw_sl) / cfg["pip_value"]
            if pips_sl_raw < cfg["min_sl_pts"]:
                raw_sl = entry - (cfg["min_sl_pts"] * cfg["pip_value"])
            sl = raw_sl
            max_sl_pips = cfg.get("max_sl_pts", 50)
            pips_sl = abs(entry - sl) / cfg["pip_value"]
            if pips_sl > max_sl_pips:
                sl = entry - (max_sl_pips * cfg["pip_value"])
            pips_sl = abs(entry - sl) / cfg["pip_value"]
            tp1_price = entry + (pips_sl * cfg["min_rr"] * cfg["pip_value"])
            tp2_price = entry + (pips_sl * cfg["min_rr"] * 2 * cfg["pip_value"])
        else:  # SELL
            sl_buffer = max(
                atr_val * cfg["sl_buffer_atr"],
                cfg["min_sl_pts"] * cfg["pip_value"],
            )
            raw_sl = entry + sl_buffer
            pips_sl_raw = abs(raw_sl - entry) / cfg["pip_value"]
            if pips_sl_raw < cfg["min_sl_pts"]:
                raw_sl = entry + (cfg["min_sl_pts"] * cfg["pip_value"])
            sl = raw_sl
            max_sl_pips = cfg.get("max_sl_pts", 50)
            pips_sl = abs(sl - entry) / cfg["pip_value"]
            if pips_sl > max_sl_pips:
                sl = entry + (max_sl_pips * cfg["pip_value"])
            pips_sl = abs(sl - entry) / cfg["pip_value"]
            tp1_price = entry - (pips_sl * cfg["min_rr"] * cfg["pip_value"])
            tp2_price = entry - (pips_sl * cfg["min_rr"] * 2 * cfg["pip_value"])

        # ── Compute RR and validate ──
        pips_target = abs(entry - tp1_price) / cfg["pip_value"]
        rr = round(pips_target / pips_sl, 2) if pips_sl > 0 else 0.0

        min_rr = cfg["min_rr"]
        max_rr = cfg["max_rr"]
        if rr < min_rr:
            if action == "BUY":
                tp1_price = entry + (pips_sl * min_rr * cfg["pip_value"])
            else:
                tp1_price = entry - (pips_sl * min_rr * cfg["pip_value"])
            pips_target = abs(entry - tp1_price) / cfg["pip_value"]
            rr = min_rr

        if rr > max_rr:
            rr = max_rr
            if action == "BUY":
                tp1_price = entry + (pips_sl * max_rr * cfg["pip_value"])
            else:
                tp1_price = entry - (pips_sl * max_rr * cfg["pip_value"])
            pips_target = abs(entry - tp1_price) / cfg["pip_value"]

        # TP2 = 2x TP1 distance from entry
        if action == "BUY":
            tp2_price = entry + (pips_target * 2 * cfg["pip_value"])
        else:
            tp2_price = entry - (pips_target * 2 * cfg["pip_value"])

        # ── Write into signal ──
        signal.entry_price = round(entry, 2)
        meta["sl"] = round(sl, 2)
        meta["tp1"] = round(tp1_price, 2)
        meta["tp2"] = round(tp2_price, 2)
        meta["rr"] = round(rr, 2)
        meta["pips_sl"] = round(pips_sl)
        meta["pips_target"] = round(pips_target)

        return True

    # ------------------------------------------------------------------
    #  Grading
    # ------------------------------------------------------------------

    def grade(self, signal: Signal) -> str:
        """Determine grade (A/B/C) and update signal confidence & grade.

        Grade A: MTF ALIGNED, score >= 80%, macro aligned, >= 65% engines
        Grade B: MTF ALIGNED/MIXED, score >= 50%, no counter-trend
        Grade C: All other valid signals

        Returns the grade letter ("A", "B", or "C").
        """
        from tradebot.models import SignalGrade

        meta = signal.metadata
        action = "BUY" if signal.direction in ("CALL", "BUY") else "SELL"

        hier_score = meta.get("consensus_score", 0.0)
        alignment = meta.get("mtf_alignment", "NONE")
        macro = meta.get("macro_trend", "NEUTRAL")
        flags = meta.get("counter_trend_flags", [])
        tfs = meta.get("timeframes", {})

        # Count engine agreement
        total_eng = 0
        agree_eng = 0
        for tf_name in _TF_ORDER:
            tf = tfs.get(tf_name, {})
            for eng_data in tf.get("engines", {}).values():
                total_eng += 1
                if eng_data.get("direction") == action:
                    agree_eng += 1

        eng_pct = agree_eng / total_eng if total_eng > 0 else 0.0

        macro_aligned = (
            (action == "BUY" and macro == "BULLISH")
            or (action == "SELL" and macro == "BEARISH")
        )
        counter_trend = len(flags) > 0

        if (
            alignment == "ALIGNED"
            and hier_score >= 0.80
            and macro_aligned
            and eng_pct >= 0.65
        ):
            grade_letter = "A"
            confidence = min(0.65 + hier_score * 0.35, 0.95)
        elif (
            alignment in ("ALIGNED", "MIXED")
            and hier_score >= 0.50
            and not counter_trend
        ):
            grade_letter = "B"
            confidence = 0.5 + hier_score * 0.3
        else:
            grade_letter = "C"
            confidence = 0.4 + hier_score * 0.3

        confidence = min(confidence, 0.98)

        # Map letter grade to SignalGrade enum
        grade_map = {
            "A": SignalGrade.STRONG,
            "B": SignalGrade.MODERATE,
            "C": SignalGrade.WEAK,
        }
        signal.confidence = round(confidence, 3)
        signal.grade = grade_map.get(grade_letter, SignalGrade.NEUTRAL)
        meta["grade"] = grade_letter

        return grade_letter

    # ------------------------------------------------------------------
    #  Order type
    # ------------------------------------------------------------------

    @staticmethod
    def get_order_type(
        action: str, entry: float, price: float, threshold: float = 0.5
    ) -> str:
        """Determine pending order type based on entry vs current price.

        For SELL:
          entry > price → SELL LIMIT
          entry < price → SELL STOP
          entry ≈ price → SELL

        For BUY:
          entry < price → BUY LIMIT
          entry > price → BUY STOP
          entry ≈ price → BUY
        """
        diff = entry - price
        if abs(diff) <= threshold:
            return action  # MARKET / near market
        if action == "SELL":
            return "SELL LIMIT" if diff > 0 else "SELL STOP"
        else:  # BUY
            return "BUY LIMIT" if diff < 0 else "BUY STOP"

    # ------------------------------------------------------------------
    #  Telegram formatter
    # ------------------------------------------------------------------

    def format_telegram(self, signal: Signal) -> str:
        """Format signal for Telegram channel posting."""
        meta = signal.metadata
        action = "BUY" if signal.direction in ("CALL", "BUY") else "SELL"
        symbol = signal.symbol
        entry = signal.entry_price or meta.get("current_price", 0.0)
        emoji = "🟢" if action == "BUY" else "🔴"
        grade_letter = meta.get("grade", "C")
        sl = meta.get("sl", 0.0)
        tp1 = meta.get("tp1", 0.0)
        tp2 = meta.get("tp2", 0.0)
        rr = meta.get("rr", 0.0)
        pips_target = meta.get("pips_target", 0)
        pips_sl = meta.get("pips_sl", 0)
        conf = signal.confidence
        reason = html.escape(meta.get("reason", f"{action} signal"))
        macro = html.escape(meta.get("macro_trend", ""))
        align = html.escape(meta.get("mtf_alignment", ""))

        now = datetime.now(timezone(timedelta(hours=7)))
        wib = now.strftime("%Y.%m.%d %H:%M")

        lines = [
            f"{emoji} <b>{action} {symbol}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🕐 {wib} WIB | Grade: <b>{grade_letter}</b> | Conf: {conf*100:.0f}%",
            "",
            f"<b>🎯 Entry:</b> ${entry:.2f}",
            f"<b>🛑 SL:</b> ${sl:.2f} ({pips_sl}pt)",
            f"<b>✅ TP1:</b> ${tp1:.2f} (+{pips_target}pt)",
            f"<b>✅ TP2:</b> ${tp2:.2f} (+{pips_target*2}pt)",
            f"<b>📊 RR:</b> 1:{rr}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🏛 {macro} | {align}",
            f"📈 {reason}",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if grade_letter == "A":
            lines.append("🔥 <b>HIGH CONVICTION</b> — siap eksekusi!")
        elif grade_letter == "B":
            lines.append("⚡ Signal valid — pantau entry area")
        else:
            lines.append("📌 Sinyal standar — atur risk management")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    #  Reason builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_reason(signal: Signal) -> str:
        """Build a human-readable reason string from signal metadata."""
        meta = signal.metadata
        action = "BUY" if signal.direction in ("CALL", "BUY") else "SELL"
        grade_letter = meta.get("grade", "C")
        alignment = meta.get("mtf_alignment", "NONE")
        macro = meta.get("macro_trend", "NEUTRAL")
        flags = meta.get("counter_trend_flags", [])
        tfs = meta.get("timeframes", {})

        reason_parts: list[str] = []
        if grade_letter == "A":
            reason_parts.append(f"MTF {alignment} | {macro}")
        elif grade_letter == "B":
            reason_parts.append(f"{macro} bias")

        # Count engine agreement
        total_engines = 0
        agreeing_engines = 0
        non_hold_engines = 0
        for tf_name in _TF_ORDER:
            tf = tfs.get(tf_name, {})
            for eng_data in tf.get("engines", {}).values():
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
                    f"{agreeing_engines}/{total_engines} engines | {active_pct}% of active"
                )
            else:
                reason_parts.append(
                    f"{agreeing_engines}/{total_engines} engines agree ({pct}%)"
                )

        if flags:
            reason_parts.append(f"⚠️ {'; '.join(flags[:2])}")

        reason = " | ".join(reason_parts) if reason_parts else f"{action} signal"
        meta["reason"] = reason
        return reason

    # ------------------------------------------------------------------
    #  Full pipeline step (validate + levels + grade + reason)
    # ------------------------------------------------------------------

    async def process(self, signal: Signal) -> Signal | None:
        """Run the full quality gate pipeline on a signal.

        Steps:
          1. Validate — reject if quality gate fails
          2. Compute levels — ATR-based TP/SL
          3. Grade — assign A/B/C grade
          4. Build reason — human-readable explanation

        Returns the enriched signal, or None if rejected.
        """
        # Step 1: validate
        if not self.validate(signal):
            return None

        # Step 2: compute levels
        if not self.compute_levels(signal):
            return None

        # Step 3: grade
        self.grade(signal)

        # Step 4: reason
        self.build_reason(signal)

        return signal

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_atr(tfs: dict) -> float | None:
        """Extract ATR from timeframes, trying M5 → M15 → H1."""
        for tf_name in ("M5", "M15", "H1"):
            tf = tfs.get(tf_name, {})
            for eng in tf.get("engines", {}).values():
                ind = eng.get("indicators", {})
                if isinstance(ind, dict):
                    atr = ind.get("atr")
                    if atr is not None:
                        return float(atr)
        return None
