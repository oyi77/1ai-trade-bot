"""
Decision Engine — Hybrid Decision Engine
==========================================
Takes a raw SignalDecision and produces a final trading decision with:
  - Precise SL/TP levels (percentage-based fallback)
  - Risk/reward ratio validation
  - Confidence grade (A/B/C)
  - JSON output to data/hybrid_signal.json for Phase 4 alert pickup

SL/TP Logic:
  Priority 1: Use SL/TP from analyzer metadata (ATR-based)
  Priority 2: Percentage-based fallback from live price
    - Default SL: 0.5% (conservative)
    - Default TP: 1.0% (2:1 R:R minimum)
  Priority 3: Support/Resistance from LSTM metadata
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from . import config
from .signal_generator import SignalDecision

logger = logging.getLogger("hybrid.decision")

WIB = timezone(timedelta(hours=7))


class FinalDecision:
    """Complete trading decision with SL/TP, grade, and metadata."""

    __slots__ = (
        "signal", "confidence", "grade", "mode", "reasoning",
        "sl", "tp", "sl_pips", "tp_pips", "risk_reward",
        "symbol", "timeframe", "current_price",
        "lstm_confidence", "zf_confidence", "integrity_status",
        "timestamp", "pipeline_version",
    )

    def __init__(
        self,
        signal: Optional[str] = None,
        confidence: float = 0.0,
        grade: str = "D",
        mode: str = "NO_DATA",
        reasoning: str = "",
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        sl_pips: Optional[float] = None,
        tp_pips: Optional[float] = None,
        risk_reward: Optional[float] = None,
        symbol: str = "",
        timeframe: str = "",
        current_price: Optional[float] = None,
        lstm_confidence: Optional[float] = None,
        zf_confidence: Optional[float] = None,
        integrity_status: str = "UNKNOWN",
        timestamp: str = "",
        pipeline_version: str = "1.0.0",
    ):
        self.signal = signal
        self.confidence = confidence
        self.grade = grade
        self.mode = mode
        self.reasoning = reasoning
        self.sl = sl
        self.tp = tp
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips
        self.risk_reward = risk_reward
        self.symbol = symbol
        self.timeframe = timeframe
        self.current_price = current_price
        self.lstm_confidence = lstm_confidence
        self.zf_confidence = zf_confidence
        self.integrity_status = integrity_status
        self.timestamp = timestamp
        self.pipeline_version = pipeline_version

    @property
    def is_tradeable(self) -> bool:
        return self.signal in ("BUY", "SELL") and self.confidence >= 0.5 and self.grade in ("A", "B")

    def to_dict(self) -> dict:
        return {
            "signal": self.signal,
            "confidence": round(self.confidence, 4),
            "grade": self.grade,
            "mode": self.mode,
            "reasoning": self.reasoning,
            "sl": self.sl,
            "tp": self.tp,
            "sl_pips": self.sl_pips,
            "tp_pips": self.tp_pips,
            "risk_reward": self.risk_reward,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_price": self.current_price,
            "lstm_confidence": self.lstm_confidence,
            "zf_confidence": self.zf_confidence,
            "integrity_status": self.integrity_status,
            "timestamp": self.timestamp,
            "pipeline_version": self.pipeline_version,
        }


def calculate_sl_tp(
    signal: str,
    current_price: float,
    existing_sl: Optional[float] = None,
    existing_tp: Optional[float] = None,
    support_levels: Optional[list[float]] = None,
    resistance_levels: Optional[list[float]] = None,
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Calculate SL and TP levels.

    Returns: (sl, tp, sl_pips, tp_pips)
    """
    if current_price <= 0:
        return None, None, None, None

    # Priority 1: Use existing SL/TP from analyzers (ATR-based)
    if existing_sl and existing_tp:
        sl = existing_sl
        tp = existing_tp
    elif signal == "BUY":
        # Percentage-based fallback
        sl = round(current_price * (1 - config.DEFAULT_SL_PCT), 2)
        tp = round(current_price * (1 + config.DEFAULT_TP_PCT), 2)

        # Try S/R levels for better SL/TP
        if support_levels:
            nearest_support = max([s for s in support_levels if s < current_price], default=None)
            if nearest_support and abs(current_price - nearest_support) / current_price < 0.02:
                sl = nearest_support

        if resistance_levels:
            nearest_resistance = min([r for r in resistance_levels if r > current_price], default=None)
            if nearest_resistance:
                tp = nearest_resistance

    elif signal == "SELL":
        sl = round(current_price * (1 + config.DEFAULT_SL_PCT), 2)
        tp = round(current_price * (1 - config.DEFAULT_TP_PCT), 2)

        if resistance_levels:
            nearest_resistance = min([r for r in resistance_levels if r > current_price], default=None)
            if nearest_resistance and abs(nearest_resistance - current_price) / current_price < 0.02:
                sl = nearest_resistance

        if support_levels:
            nearest_support = max([s for s in support_levels if s < current_price], default=None)
            if nearest_support:
                tp = nearest_support

    else:
        return None, None, None, None

    # Calculate pip distances
    pip_size = _get_pip_size(current_price)
    sl_pips = round(abs(current_price - sl) / pip_size, 1) if sl else None
    tp_pips = round(abs(tp - current_price) / pip_size, 1) if tp else None

    # Validate minimum distance
    if sl_pips and sl_pips < config.MIN_SL_PIPS:
        logger.warning("SL too tight: %.1f pips < %d min", sl_pips, config.MIN_SL_PIPS)
        if signal == "BUY":
            sl = round(current_price - config.MIN_SL_PIPS * pip_size, 2)
        else:
            sl = round(current_price + config.MIN_SL_PIPS * pip_size, 2)
        sl_pips = config.MIN_SL_PIPS

    return sl, tp, sl_pips, tp_pips


def _get_pip_size(price: float) -> float:
    """Determine pip size based on price magnitude."""
    if price > 1000:
        return 0.1      # XAU, BTC — 1 pip = $0.10
    elif price > 100:
        return 0.01     # ETH, major pairs
    elif price > 1:
        return 0.001    # Minor pairs
    else:
        return 0.0001   # Micro


def calculate_grade(confidence: float, mode: str) -> str:
    """Grade the signal quality: A/B/C/D."""
    if mode == "DUAL" and confidence >= 0.85:
        return "A"
    elif mode == "DUAL" and confidence >= 0.70:
        return "B"
    elif mode == "SOLO" and confidence >= 0.85:
        return "B"
    elif mode == "DUAL" and confidence >= 0.55:
        return "C"
    else:
        return "D"


def evaluate_decision(
    signal: SignalDecision,
    symbol: str = "",
    timeframe: str = "",
    current_price: Optional[float] = None,
) -> FinalDecision:
    """
    Transform a raw SignalDecision into a FinalDecision with SL/TP, grade, and metadata.
    """
    now = datetime.now(WIB).isoformat()

    # No valid signal → return early
    if not signal.is_valid:
        return FinalDecision(
            signal=signal.signal,
            confidence=signal.confidence,
            grade="D",
            mode=signal.mode,
            reasoning=signal.reasoning,
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            lstm_confidence=signal.lstm_confidence,
            zf_confidence=signal.zf_confidence,
            integrity_status=signal.integrity_status,
            timestamp=now,
            pipeline_version=config.VERSION,
        )

    # Calculate SL/TP
    sl, tp, sl_pips, tp_pips = calculate_sl_tp(
        signal=signal.signal,
        current_price=current_price or 0,
        existing_sl=signal.sl,
        existing_tp=signal.tp,
    )

    # Risk/Reward ratio
    risk_reward = None
    if sl_pips and tp_pips and sl_pips > 0:
        risk_reward = round(tp_pips / sl_pips, 2)

    # Validate minimum R:R
    if risk_reward and risk_reward < config.MIN_RISK_REWARD:
        logger.warning("R:R too low: %.2f < %.1f min — adjusting TP", risk_reward, config.MIN_RISK_REWARD)
        if signal.signal == "BUY":
            tp = round((current_price or 0) + sl_pips * config.MIN_RISK_REWARD * _get_pip_size(current_price or 0), 2)
        else:
            tp = round((current_price or 0) - sl_pips * config.MIN_RISK_REWARD * _get_pip_size(current_price or 0), 2)
        tp_pips = round(sl_pips * config.MIN_RISK_REWARD, 1)
        risk_reward = config.MIN_RISK_REWARD

    # Grade
    grade = calculate_grade(signal.confidence, signal.mode)

    return FinalDecision(
        signal=signal.signal,
        confidence=signal.confidence,
        grade=grade,
        mode=signal.mode,
        reasoning=signal.reasoning,
        sl=sl,
        tp=tp,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        risk_reward=risk_reward,
        symbol=symbol,
        timeframe=timeframe,
        current_price=current_price,
        lstm_confidence=signal.lstm_confidence,
        zf_confidence=signal.zf_confidence,
        integrity_status=signal.integrity_status,
        timestamp=now,
        pipeline_version=config.VERSION,
    )


def write_signal_file(decision: FinalDecision) -> Path:
    """
    Write final decision to data/hybrid_signal.json for Phase 4 alert pickup.

    Returns the file path written.
    """
    signal_dir = config.DATA_DIR / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)

    signal_file = signal_dir / "hybrid_signal.json"

    payload = {
        "source": "hybrid_decision_engine",
        "version": config.VERSION,
        "generated_at": decision.timestamp,
        "decision": decision.to_dict(),
    }

    # Atomic write: temp → rename
    tmp_file = signal_file.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(payload, indent=2, default=str))
    tmp_file.rename(signal_file)

    logger.info("📄 Signal written: %s (signal=%s, grade=%s)", signal_file, decision.signal, decision.grade)
    return signal_file
