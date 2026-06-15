"""
mtf_consensus.py — Multi-Timeframe Consensus Gate

Three-tier validation gate that evaluates AHZ_Active signals from the
Harmonic Engine against macro trend (H1/H4), meso setup (M15), and
micro trigger (M1/M5) timeframes before authorizing execution.

Architecture:
  Macro (Trend)  → H1/H4 bias alignment (SMC market structure)
  Meso (Setup)   → M15 harmonic AHZ activation (the trap)
  Micro (Trigger) → M1/M5 confirmation within AHZ bounds (the entry)

State Machine:
  IDLE → HUNT_MODE (AHZ detected) → EXECUTE (confirmed) or REJECT (invalidated)

Conforms to: tradebot pipeline stage (not an Engine — it's a gate, not a signal source).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine

from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


# ── Enums ──────────────────────────────────────────────────────────────


class GateState(Enum):
    """State of the consensus gate for a given symbol+setup."""
    IDLE = auto()
    HUNT_MODE = auto()
    EXECUTE = auto()
    REJECT = auto()


class Timeframe(Enum):
    """Supported analysis timeframes."""
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


class TriggerType(Enum):
    """Micro-confirmation trigger types from existing engines."""
    SMC_CHOCH = "smc_choch"          # Change of Character on M1/M5
    SMC_OB_TAP = "smc_ob_tap"        # Order Block tap within AHZ
    FVG_FILL = "fvg_fill"            # FVG fill inside AHZ zone
    LIQUIDITY_SWEEP = "liq_sweep"    # Liquidity sweep of AHZ extreme
    SMC_BOS = "smc_bos"              # Break of Structure on M5


class MacroBias(Enum):
    """Macro trend direction from H1/H4 analysis."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# ── Data Structures ────────────────────────────────────────────────────


@dataclass
class MacroState:
    """
    Macro timeframe analysis result (H1/H4).
    Produced by SMC engine or trend engine on higher timeframe.
    """
    bias: MacroBias
    confidence: float  # 0.0–1.0
    source_engine: str  # e.g. "smc_scalper", "trend_engine"
    market_structure: str = ""  # e.g. "HH-HL", "LH-LL"
    order_flow: str = ""  # e.g. "bullish_imbalance"
    metadata: dict = field(default_factory=dict)


@dataclass
class MesoState:
    """
    Meso timeframe analysis result (M15).
    The Harmonic Engine's AHZ activation lives here.
    """
    ahz_active: bool
    ahz_upper: float
    ahz_lower: float
    direction: str  # "BULLISH" or "BEARISH"
    pattern: str  # "gartley", "bat", "butterfly"
    sl: float  # Harmonic SL (invalidation level)
    tp1: float
    tp2: float
    confidence: float
    symbol: str = ""
    timeframe: str = "M15"
    metadata: dict = field(default_factory=dict)

    @property
    def ahz_mid(self) -> float:
        return (self.ahz_upper + self.ahz_lower) / 2

    @property
    def ahz_height(self) -> float:
        return self.ahz_upper - self.ahz_lower


@dataclass
class MicroTrigger:
    """
    Micro timeframe trigger event (M1/M5).
    Produced by SMC, FVG, or Liquidity engines on lower timeframes.
    """
    trigger_type: TriggerType
    symbol: str
    price: float  # Price where the trigger occurred
    direction: str  # Must match meso direction
    confidence: float
    source_engine: str
    within_ahz: bool = False  # Computed: is this trigger inside the AHZ?
    timeframe: str = "M5"
    metadata: dict = field(default_factory=dict)


@dataclass
class ConsensusVerdict:
    """
    Final output of the MTF Consensus Gate.
    Either EXECUTE with full coordinates, or REJECT with reason.
    """
    decision: GateState
    symbol: str
    direction: str
    entry_price: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    confidence: float = 0.0
    reason: str = ""
    macro_alignment: bool = False
    micro_trigger: MicroTrigger | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_execution(self) -> bool:
        return self.decision == GateState.EXECUTE

    def to_signal(self) -> Signal | None:
        """Convert verdict to a Signal for downstream pipeline."""
        if not self.is_execution or self.entry_price is None:
            return None

        grade = (
            SignalGrade.STRONG if self.confidence >= 0.7
            else SignalGrade.MODERATE if self.confidence >= 0.5
            else SignalGrade.WEAK
        )

        return Signal(
            symbol=self.symbol,
            direction=self.direction,
            predicted_digit=int(self.entry_price * 10) % 10,
            confidence=self.confidence,
            source=SignalSource.CONSENSUS,
            grade=grade,
            entry_price=self.entry_price,
            metadata={
                "consensus": True,
                "sl": self.sl,
                "tp1": self.tp1,
                "tp2": self.tp2,
                "macro_alignment": self.macro_alignment,
                "micro_trigger": self.micro_trigger.trigger_type.value if self.micro_trigger else None,
                "gate_reason": self.reason,
                **self.metadata,
            },
        )


# ── Hunt Mode State Manager ───────────────────────────────────────────


@dataclass
class HuntSession:
    """
    Active hunt session for one symbol.
    Created when AHZ_Active fires, tracks state until EXECUTE or REJECT.
    """
    symbol: str
    meso: MesoState
    macro: MacroState | None = None
    triggers: list[MicroTrigger] = field(default_factory=list)
    state: GateState = GateState.HUNT_MODE
    created_at: float = 0.0  # epoch
    expires_at: float = 0.0  # epoch — auto-expire stale hunts
    metadata: dict = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state == GateState.HUNT_MODE

    @property
    def best_trigger(self) -> MicroTrigger | None:
        """Best confirmed trigger within AHZ bounds."""
        valid = [t for t in self.triggers if t.within_ahz]
        if not valid:
            return None
        return max(valid, key=lambda t: t.confidence)


# ── Core Gate Logic ────────────────────────────────────────────────────


class MTFConsensusGate:
    """
    Multi-Timeframe Consensus Gate.

    Evaluates AHZ_Active signals through three tiers:
      1. Macro (H1/H4) — trend alignment check
      2. Meso (M15) — harmonic AHZ activation
      3. Micro (M1/M5) — trigger confirmation within AHZ

    Manages Hunt Mode state per symbol with configurable TTL.
    """

    def __init__(
        self,
        hunt_ttl_seconds: float = 3600.0,
        require_macro: bool = False,
        min_macro_confidence: float = 0.4,
        min_trigger_confidence: float = 0.5,
    ):
        self._hunt_ttl = hunt_ttl_seconds
        self._require_macro = require_macro
        self._min_macro_conf = min_macro_confidence
        self._min_trigger_conf = min_trigger_confidence
        self._sessions: dict[str, HuntSession] = {}  # symbol → HuntSession
        self._verdict_history: list[ConsensusVerdict] = []

    @property
    def active_sessions(self) -> dict[str, HuntSession]:
        return dict(self._sessions)

    @property
    def hunt_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.is_active)

    def activate_hunt(
        self,
        meso: MesoState,
        macro: MacroState | None = None,
        now: float | None = None,
    ) -> ConsensusVerdict:
        """
        Enter Hunt Mode when Harmonic Engine fires AHZ_Active.

        Returns immediate REJECT if macro alignment fails (if required).
        Returns HUNT_MODE status if macro passes (hunt begins).
        """
        import time
        t = now if now is not None else time.time()

        if not meso.ahz_active:
            return ConsensusVerdict(
                decision=GateState.REJECT,
                symbol=meso.symbol,
                direction=meso.direction,
                reason="AHZ not active — no harmonic pattern detected",
            )

        # Tier 1: Macro alignment check
        macro_aligned = self._check_macro_alignment(meso, macro)
        if not macro_aligned:
            reason = (
                f"Macro bias {macro.bias.value if macro else 'UNKNOWN'} "
                f"conflicts with {meso.direction} harmonic direction"
            )
            LOG.info("Gate REJECT (macro): %s — %s", meso.symbol, reason)
            verdict = ConsensusVerdict(
                decision=GateState.REJECT,
                symbol=meso.symbol,
                direction=meso.direction,
                macro_alignment=False,
                reason=reason,
            )
            self._verdict_history.append(verdict)
            return verdict

        # Create hunt session
        session = HuntSession(
            symbol=meso.symbol,
            meso=meso,
            macro=macro,
            state=GateState.HUNT_MODE,
            created_at=t,
            expires_at=t + self._hunt_ttl,
            metadata={"macro_aligned": macro_aligned},
        )
        self._sessions[meso.symbol] = session

        LOG.info(
            "Gate HUNT_MODE: %s %s AHZ [%.5f–%.5f] — "
            "macro=%s, waiting for micro trigger",
            meso.direction, meso.symbol,
            meso.ahz_lower, meso.ahz_upper,
            "aligned" if macro_aligned else "skipped",
        )

        hunt_verdict = ConsensusVerdict(
            decision=GateState.HUNT_MODE,
            symbol=meso.symbol,
            direction=meso.direction,
            entry_price=meso.ahz_mid,
            sl=meso.sl,
            tp1=meso.tp1,
            tp2=meso.tp2,
            macro_alignment=macro_aligned,
            reason="Hunt Mode active — awaiting micro confirmation",
            metadata={
                "ahz_upper": meso.ahz_upper,
                "ahz_lower": meso.ahz_lower,
                "pattern": meso.pattern,
                "confidence": meso.confidence,
            },
        )
        self._verdict_history.append(hunt_verdict)
        return hunt_verdict

    def process_micro_trigger(
        self,
        trigger: MicroTrigger,
        current_price: float | None = None,
        now: float | None = None,
    ) -> ConsensusVerdict | None:
        """
        Process a micro timeframe trigger event (SMC ChoCh, FVG fill, etc.).

        If no active hunt for this trigger's symbol → ignored (returns None).
        If price is outside AHZ → trigger ignored.
        If trigger confirms within AHZ → EXECUTE with full coordinates.

        Returns None if no active session exists for this symbol.
        """
        import time
        t = now if now is not None else time.time()

        session = self._sessions.get(trigger.symbol)
        if session is None or not session.is_active:
            return None

        # Expire stale hunts
        if t > session.expires_at:
            LOG.info("Gate REJECT (expired): %s hunt timed out", trigger.symbol)
            session.state = GateState.REJECT
            verdict = ConsensusVerdict(
                decision=GateState.REJECT,
                symbol=trigger.symbol,
                direction=session.meso.direction,
                reason="Hunt session expired — no micro trigger within TTL",
            )
            self._verdict_history.append(verdict)
            return verdict

        # Check if trigger is inside AHZ bounds
        price = current_price or trigger.price
        in_ahz = self._is_within_ahz(price, session.meso)
        trigger.within_ahz = in_ahz

        if not in_ahz:
            LOG.debug(
                "Trigger %s at %.5f outside AHZ [%.5f–%.5f] for %s",
                trigger.trigger_type.value, price,
                session.meso.ahz_lower, session.meso.ahz_upper,
                trigger.symbol,
            )
            session.triggers.append(trigger)
            return None

        # Check trigger confidence
        if trigger.confidence < self._min_trigger_conf:
            LOG.debug(
                "Trigger %s confidence %.2f below minimum %.2f",
                trigger.trigger_type.value, trigger.confidence,
                self._min_trigger_conf,
            )
            session.triggers.append(trigger)
            return None

        # Check direction alignment
        if trigger.direction != session.meso.direction:
            LOG.debug(
                "Trigger direction %s mismatches meso %s",
                trigger.direction, session.meso.direction,
            )
            session.triggers.append(trigger)
            return None

        # ✅ All checks passed — EXECUTE
        session.triggers.append(trigger)
        session.state = GateState.EXECUTE

        # Calculate composite confidence
        composite_conf = self._compute_composite_confidence(session, trigger)

        # Re-validate macro still holds
        macro_still_valid = self._check_macro_alignment(
            session.meso, session.macro
        )

        verdict = ConsensusVerdict(
            decision=GateState.EXECUTE,
            symbol=trigger.symbol,
            direction=session.meso.direction,
            entry_price=price,
            sl=session.meso.sl,
            tp1=session.meso.tp1,
            tp2=session.meso.tp2,
            confidence=composite_conf,
            macro_alignment=macro_still_valid,
            micro_trigger=trigger,
            reason=(
                f"CONFIRMED: {session.meso.pattern} {session.meso.direction} "
                f"AHZ → {trigger.trigger_type.value} at {price:.5f}"
            ),
            metadata={
                "ahz_upper": session.meso.ahz_upper,
                "ahz_lower": session.meso.ahz_lower,
                "pattern": session.meso.pattern,
                "harmonic_confidence": session.meso.confidence,
                "macro_bias": session.macro.bias.value if session.macro else "unknown",
                "trigger_count": len(session.triggers),
                "hunt_duration_s": t - session.created_at,
            },
        )

        LOG.info(
            "Gate EXECUTE: %s %s @ %.5f — SL=%.5f TP1=%.5f TP2=%.5f "
            "(conf=%.1f%%, trigger=%s, hunt=%.0fs)",
            verdict.direction, verdict.symbol, price,
            verdict.sl, verdict.tp1, verdict.tp2,
            composite_conf * 100,
            trigger.trigger_type.value,
            t - session.created_at,
        )

        self._verdict_history.append(verdict)
        return verdict

    def check_invalidation(
        self,
        current_price: float,
        symbol: str,
    ) -> ConsensusVerdict | None:
        """
        Check if current price has invalidated the AHZ (hit SL level).
        Called on every tick for active hunts.

        Returns REJECT if price breached the harmonic SL, None otherwise.
        """
        session = self._sessions.get(symbol)
        if session is None or not session.is_active:
            return None

        meso = session.meso

        # Bullish: SL is below AHZ — price dropping below SL = invalidation
        # Bearish: SL is above AHZ — price rising above SL = invalidation
        invalidated = False
        if meso.direction == "BULLISH" and current_price <= meso.sl:
            invalidated = True
        elif meso.direction == "BEARISH" and current_price >= meso.sl:
            invalidated = True

        if invalidated:
            session.state = GateState.REJECT
            reason = (
                f"AHZ invalidated: {meso.direction} SL {meso.sl:.5f} "
                f"breached by price {current_price:.5f}"
            )
            LOG.info("Gate REJECT (invalidation): %s — %s", symbol, reason)

            verdict = ConsensusVerdict(
                decision=GateState.REJECT,
                symbol=symbol,
                direction=meso.direction,
                entry_price=current_price,
                sl=meso.sl,
                reason=reason,
                metadata={
                    "invalidation_price": current_price,
                    "sl_level": meso.sl,
                    "pattern": meso.pattern,
                },
            )
            self._verdict_history.append(verdict)
            return verdict

        return None

    def cleanup_expired(self, now: float | None = None) -> list[ConsensusVerdict]:
        """Remove expired hunt sessions. Returns REJECT verdicts for each."""
        import time
        t = now if now is not None else time.time()
        expired: list[ConsensusVerdict] = []

        for symbol in list(self._sessions.keys()):
            session = self._sessions[symbol]
            if session.is_active and t > session.expires_at:
                session.state = GateState.REJECT
                verdict = ConsensusVerdict(
                    decision=GateState.REJECT,
                    symbol=symbol,
                    direction=session.meso.direction,
                    reason=f"Hunt session expired after {self._hunt_ttl:.0f}s",
                    metadata={"expired_at": t, "created_at": session.created_at},
                )
                expired.append(verdict)
                self._verdict_history.append(verdict)
                del self._sessions[symbol]

        return expired

    def get_history(
        self, symbol: str | None = None, limit: int = 20
    ) -> list[ConsensusVerdict]:
        """Get recent verdict history, optionally filtered by symbol."""
        hist = self._verdict_history
        if symbol:
            hist = [v for v in hist if v.symbol == symbol]
        return hist[-limit:]

    def reset(self, symbol: str | None = None) -> None:
        """Clear sessions (for testing or manual override)."""
        if symbol:
            self._sessions.pop(symbol, None)
        else:
            self._sessions.clear()

    # ── Internal Logic ─────────────────────────────────────────────────

    def _check_macro_alignment(
        self, meso: MesoState, macro: MacroState | None
    ) -> bool:
        """
        Check if macro bias aligns with meso harmonic direction.

        Returns True (passes) when:
          - No macro data available (treated as unknown, not blocking)
          - Macro confidence below threshold (insufficient data)
          - Macro bias is NEUTRAL (no directional conflict)
          - Macro bias matches meso direction

        Returns False only when macro bias actively CONFLICTS with meso.
        """
        if macro is None:
            return True

        if macro.confidence < self._min_macro_conf:
            LOG.debug(
                "Macro confidence %.2f below minimum %.2f — treating as unavailable",
                macro.confidence, self._min_macro_conf,
            )
            return True

        if macro.bias == MacroBias.NEUTRAL:
            return True

        meso_bullish = meso.direction == "BULLISH"
        macro_bullish = macro.bias == MacroBias.BULLISH

        aligned = meso_bullish == macro_bullish
        if not aligned:
            LOG.info(
                "Macro %s CONFLICTS with meso %s — rejecting",
                macro.bias.value, meso.direction,
            )
        return aligned

    def _is_within_ahz(self, price: float, meso: MesoState) -> bool:
        """Check if a price falls within the AHZ zone (with 5% buffer)."""
        buffer = meso.ahz_height * 0.05
        return (meso.ahz_lower - buffer) <= price <= (meso.ahz_upper + buffer)

    def _compute_composite_confidence(
        self, session: HuntSession, trigger: MicroTrigger
    ) -> float:
        """
        Composite confidence = weighted average of three tiers:
          Macro:  25% (if available)
          Meso:  40% (harmonic pattern quality)
          Micro: 35% (trigger strength)
        """
        scores: list[tuple[float, float]] = []

        # Meso (harmonic) — always present
        scores.append((session.meso.confidence, 0.40))

        # Micro (trigger)
        scores.append((trigger.confidence, 0.35))

        # Macro — optional
        if session.macro and session.macro.confidence > 0:
            scores.append((session.macro.confidence, 0.25))
        else:
            # Redistribute macro weight to meso
            scores[0] = (session.meso.confidence, 0.65)

        total_weight = sum(w for _, w in scores)
        if total_weight <= 0:
            return 0.0

        return sum(s * w for s, w in scores) / total_weight


# ── Convenience: Build MesoState from Harmonic Signal ──────────────────


def meso_from_signal(signal: Signal) -> MesoState | None:
    """
    Extract MesoState from a Harmonic Engine signal.
    Returns None if the signal doesn't carry AHZ_Active metadata.
    """
    meta = signal.metadata
    if not meta.get("AHZ_Active"):
        return None

    return MesoState(
        ahz_active=True,
        ahz_upper=meta.get("ahz_upper", 0.0),
        ahz_lower=meta.get("ahz_lower", 0.0),
        direction=signal.direction,
        pattern=meta.get("pattern", "unknown"),
        sl=meta.get("sl", 0.0),
        tp1=meta.get("tp1", 0.0),
        tp2=meta.get("tp2", 0.0),
        confidence=meta.get("confidence", signal.confidence),
        symbol=signal.symbol,
    )


# ── Convenience: Build MacroState from SMC Signal ──────────────────────


def macro_from_signal(signal: Signal) -> MacroState | None:
    """
    Extract MacroState from an SMC/trend engine signal on H1/H4.
    Checks metadata for market structure indicators.
    """
    meta = signal.metadata
    direction = signal.direction.upper()

    if direction == "BULLISH":
        bias = MacroBias.BULLISH
    elif direction == "BEARISH":
        bias = MacroBias.BEARISH
    else:
        bias = MacroBias.NEUTRAL

    return MacroState(
        bias=bias,
        confidence=signal.confidence,
        source_engine=signal.source.value if hasattr(signal.source, "value") else str(signal.source),
        market_structure=meta.get("market_structure", ""),
        order_flow=meta.get("order_flow", ""),
        metadata=meta,
    )
