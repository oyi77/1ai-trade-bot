"""
Signal Generator — Hybrid Decision Engine
==========================================
Cross-validates analyzer outputs and produces a raw signal decision.

Rules:
  1. Integrity BLOCK → no signal (hard gate)
  2. Integrity WARN → penalty on confidence
  3. DUAL consensus (LSTM + ZF agree) → boosted confidence
  4. CONFLICT (LSTM ≠ ZF) → no signal
  5. SOLO mode (one failed) → strict threshold ≥0.85
  6. No valid output → no signal

This module is STATELESS — pure function, thread-safe.
"""
from __future__ import annotations

import logging
from typing import Optional

from .analyzers.base import AnalysisResult
from . import config

logger = logging.getLogger("hybrid.signal")


class SignalDecision:
    """Structured output from the signal generator."""

    __slots__ = (
        "signal", "confidence", "mode", "reasoning",
        "sl", "tp", "lstm_confidence", "zf_confidence",
        "integrity_status",
    )

    def __init__(
        self,
        signal: Optional[str] = None,
        confidence: float = 0.0,
        mode: str = "NO_DATA",
        reasoning: str = "",
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        lstm_confidence: Optional[float] = None,
        zf_confidence: Optional[float] = None,
        integrity_status: str = "UNKNOWN",
    ):
        self.signal = signal
        self.confidence = confidence
        self.mode = mode
        self.reasoning = reasoning
        self.sl = sl
        self.tp = tp
        self.lstm_confidence = lstm_confidence
        self.zf_confidence = zf_confidence
        self.integrity_status = integrity_status

    @property
    def is_valid(self) -> bool:
        return self.signal in ("BUY", "SELL") and self.confidence > 0

    def to_dict(self) -> dict:
        return {
            "signal": self.signal,
            "confidence": round(self.confidence, 4),
            "mode": self.mode,
            "reasoning": self.reasoning,
            "sl": self.sl,
            "tp": self.tp,
            "lstm_confidence": self.lstm_confidence,
            "zf_confidence": self.zf_confidence,
            "integrity_status": self.integrity_status,
        }


def generate_signal(
    analyzer_results: dict[str, Optional[AnalysisResult]],
    current_price: Optional[float] = None,
) -> SignalDecision:
    """
    Cross-validate analyzer outputs and produce final signal decision.

    Args:
        analyzer_results: {"lstm": AnalysisResult, "zf_core": AnalysisResult, "integrity": AnalysisResult}
        current_price: Current market price (used for SL/TP fallback)

    Returns:
        SignalDecision with signal, confidence, mode, reasoning, SL/TP
    """
    lstm = analyzer_results.get("lstm")
    zf = analyzer_results.get("zf_core")
    integrity = analyzer_results.get("integrity")

    # ── Rule 1: Integrity BLOCK → hard gate ──
    if integrity and integrity.blocked:
        logger.info("⛔ INTEGRITY BLOCK: %s", integrity.block_reason)
        return SignalDecision(
            confidence=0.0,
            mode="BLOCKED",
            reasoning=f"⛔ INTEGRITY BLOCK: {integrity.block_reason}",
            integrity_status="BLOCKED",
        )

    # ── Rule 2: Integrity WARN → penalty ──
    integrity_penalty = 0.0
    integrity_status = "OK"
    if integrity:
        integrity_status = integrity.action
        if integrity.action == "WARN":
            integrity_penalty = config.INTEGRITY_PENALTY
            logger.info("⚠️ INTEGRITY WARN: penalty=%.2f", integrity_penalty)

    # ── Rule 3: DUAL consensus ──
    if (lstm and lstm.success and lstm.action in ("BUY", "SELL")
            and zf and zf.success and zf.action in ("BUY", "SELL")):
        if lstm.action == zf.action:
            avg_conf = (lstm.confidence + zf.confidence) / 2
            boosted = min(avg_conf * config.DUAL_CONFIDENCE_BOOST, 0.95)
            boosted = max(boosted - integrity_penalty, 0.0)

            # Prefer ZF SL/TP (more statistically grounded), fallback to LSTM
            sl = zf.metadata.get("sl") or lstm.metadata.get("sl")
            tp = zf.metadata.get("tp") or lstm.metadata.get("tp")

            reasoning = (
                f"DUAL CONSENSUS: LSTM({lstm.confidence:.2f}) ∩ "
                f"ZF({zf.confidence:.2f}) agree → {lstm.action}"
            )
            if integrity_penalty > 0:
                reasoning += f" [integrity penalty: -{integrity_penalty:.2f}]"

            logger.info("✅ DUAL: %s conf=%.2f", lstm.action, boosted)
            return SignalDecision(
                signal=lstm.action,
                confidence=round(boosted, 4),
                mode="DUAL",
                reasoning=reasoning,
                sl=sl,
                tp=tp,
                lstm_confidence=lstm.confidence,
                zf_confidence=zf.confidence,
                integrity_status=integrity_status,
            )
        else:
            reasoning = (
                f"CONFLICT: LSTM={lstm.action}({lstm.confidence:.2f}) vs "
                f"ZF={zf.action}({zf.confidence:.2f})"
            )
            logger.info("❌ CONFLICT: %s", reasoning)
            return SignalDecision(
                confidence=0.0,
                mode="CONFLICT",
                reasoning=reasoning,
                integrity_status=integrity_status,
            )

    # ── Rule 4: SOLO mode — one analyzer failed/timed out ──
    solo = None
    if lstm and lstm.success and lstm.action in ("BUY", "SELL"):
        solo = lstm
    elif zf and zf.success and zf.action in ("BUY", "SELL"):
        solo = zf

    if solo:
        if solo.confidence >= config.MIN_CONFIDENCE_SOLO:
            final_conf = max(solo.confidence - integrity_penalty, 0.0)
            reasoning = (
                f"SOLO {solo.analyzer.upper()}: {solo.action} "
                f"(conf={solo.confidence:.2f} ≥ {config.MIN_CONFIDENCE_SOLO} min)"
            )
            if integrity_penalty > 0:
                reasoning += f" [integrity penalty: -{integrity_penalty:.2f}]"

            logger.info("🔶 SOLO %s: %s conf=%.2f", solo.analyzer, solo.action, final_conf)
            return SignalDecision(
                signal=solo.action,
                confidence=round(final_conf, 4),
                mode="SOLO",
                reasoning=reasoning,
                sl=solo.metadata.get("sl"),
                tp=solo.metadata.get("tp"),
                lstm_confidence=lstm.confidence if lstm == solo else None,
                zf_confidence=zf.confidence if zf == solo else None,
                integrity_status=integrity_status,
            )
        else:
            reasoning = (
                f"SOLO {solo.analyzer.upper()} BLOCKED: {solo.action} "
                f"confidence {solo.confidence:.2f} < {config.MIN_CONFIDENCE_SOLO} min"
            )
            logger.info("🚫 %s", reasoning)
            return SignalDecision(
                confidence=0.0,
                mode="SOLO_BLOCKED",
                reasoning=reasoning,
                integrity_status=integrity_status,
            )

    # ── Rule 5: No valid output ──
    return SignalDecision(
        confidence=0.0,
        mode="NO_DATA",
        reasoning="NO SIGNAL: insufficient analyzer output",
        integrity_status=integrity_status,
    )
