"""
meta_orchestrator.py — VilonaMetaOrchestrator — The Supreme Signal Arbitrator

Unifies three disjointed signal paths into a single executable Signal for TradeExecutor:

  Path A — Flat Engine Consensus  : Signal from EngineConsensus / SignalPipeline / QualityGate
  Path B — MTF Hierarchical       : dict from MTFConsensus.analyze()
  Path C — Harmonic MTF Gate      : ConsensusVerdict from MTFConsensusGate

Conflict Resolution Matrix:
  ┌─────────────────────┬──────────────────┬──────────────────┬───────────────┐
  │ Harmonic State      │ MTF Macro Align  │ Flat Signal      │ Verdict       │
  ├─────────────────────┼──────────────────┼──────────────────┼───────────────┤
  │ EXECUTE             │ ALIGNED          │ any              │ GOLDEN SYNERGY│
  │ EXECUTE             │ MIXED/CONFLICT   │ any              │ EXECUTE (B)   │
  │ HUNT_MODE           │ any              │ OPPOSES hunt     │ VETO flat     │
  │ HUNT_MODE           │ any              │ ALIGNS  hunt     │ ENTER hunt    │
  │ IDLE / REJECT       │ ALIGNED/MIXED    │ valid            │ STANDARD      │
  │ IDLE / REJECT       │ CONFLICT         │ valid            │ DOWNGRADE     │
  │ IDLE / REJECT       │ any              │ None             │ MTF-ONLY (C)  │
  │ any                 │ any              │ all None         │ HOLD          │
  └─────────────────────┴──────────────────┴──────────────────┴───────────────┘

Stake Multiplier:
  Golden Synergy (all 3 paths aligned)  → 2.0x
  Harmonic EXECUTE (no flat backup)     → 1.5x
  Standard (flat + MTF aligned)         → 1.0x
  Mixed / counter-trend                 → 0.5x
  Weak confidence (< 0.5)               → 0.0x (HOLD)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tradebot.engines.mtf_consensus import (
    ConsensusVerdict,
    GateState,
    HuntSession,
    MTFConsensusGate,
)
from tradebot.models import Signal, SignalGrade, SignalSource

LOG = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

# MTF alignment: how many timeframes agree with macro trend
_ALIGNED = "ALIGNED"
_MIXED = "MIXED"
_CONFLICT = "CONFLICT"
_NONE = "NONE"

# Macro trend values
_BULLISH = "BULLISH"
_BEARISH = "BEARISH"
_NEUTRAL = "NEUTRAL"

# Direction mapping — normalise the various string forms
_DIRECTION_MAP: dict[str, str] = {
    "BULLISH": "BULLISH",
    "BEARISH": "BEARISH",
    "BUY": "BULLISH",
    "SELL": "BEARISH",
    "CALL": "BULLISH",
    "PUT": "BEARISH",
    "LONG": "BULLISH",
    "SHORT": "BEARISH",
}


def _normalise_direction(raw: str | None) -> str:
    """Normalise direction strings to canonical BULLISH/BEARISH."""
    if raw is None:
        return "UNKNOWN"
    return _DIRECTION_MAP.get(raw.upper(), raw.upper())


def _directions_match(a: str, b: str) -> bool:
    """Check if two direction strings refer to the same bias."""
    return _normalise_direction(a) == _normalise_direction(b)


# ═══════════════════════════════════════════════════════════════════════
#  RESOLUTION RESULT
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class OrchestratorDecision:
    """Internal result produced by the conflict-resolution matriX."""

    signal: Signal | None
    stake_multiplier: float
    resolution_path: str  # human-readable explanation
    metadata: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
#  META ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════


class VilonaMetaOrchestrator:
    """
    Supreme signal arbitrator — resolves three parallel signal paths
    into a single executable Signal for TradeExecutor.

    Usage::

        orch = VilonaMetaOrchestrator(gate)
        signal = await orch.resolve_signals(
            symbol="XAUUSD",
            flat_signal=pipeline_output,
            mtf_verdict=mtf_result,
            harmonic_verdict=gate_verdict,
        )
        if signal:
            trade_executor.execute(signal)  # blindly trust
    """

    def __init__(
        self,
        gate: MTFConsensusGate | None = None,
        *,
        strict_counter_trend: bool = True,
        min_fallback_confidence: float = 0.50,
        macro_veto_threshold: float = 0.60,
    ):
        """
        Parameters
        ----------
        gate:
            MTFConsensusGate instance — needed for Hunt Mode shield
            (query whether a symbol is actively hunting).
        strict_counter_trend:
            If True, reject flat signals that oppose the MTF macro trend
            when harmonic is IDLE.  If False, downgrade them.
        min_fallback_confidence:
            Minimum confidence for a flat signal to be considered valid
            in the standard fallback path.
        macro_veto_threshold:
            MTF consensus_score above which a CONFLICT alignment can
            veto a counter-trend flat signal.
        """
        self._gate = gate
        self._strict_ct = strict_counter_trend
        self._min_conf = min_fallback_confidence
        self._macro_veto = macro_veto_threshold

    # ── Public API ─────────────────────────────────────────────────

    async def resolve_signals(
        self,
        symbol: str,
        flat_signal: Signal | None = None,
        mtf_verdict: dict[str, Any] | None = None,
        harmonic_verdict: ConsensusVerdict | None = None,
    ) -> Signal | None:
        """
        Resolve three signal paths into a single executable Signal.

        Parameters
        ----------
        symbol:
            Trading symbol (e.g. "XAUUSD").
        flat_signal:
            Signal from EngineConsensus / SignalPipeline / QualityGate.
            May be *None* if no flat engines produced a signal.
        mtf_verdict:
            Dict from ``MTFConsensus.analyze()``.  Keys include
            ``macro_trend``, ``mtf_alignment``, ``consensus_score``,
            ``verdict``, ``counter_trend_flags``.
        harmonic_verdict:
            ``ConsensusVerdict`` from ``MTFConsensusGate``, or *None*
            if no harmonic pattern is active.

        Returns
        -------
        Signal or None
            A single unified Signal that TradeExecutor can blindly
            trust, enriched with ``orchestrator_verdict`` in metadata.
            Returns *None* (HOLD) when all paths conflict or no valid
            signal exists.
        """
        # ── Normalise inputs ─────────────────────────────────────────
        hstate = self._classify_harmonic(harmonic_verdict, symbol)
        mtrend = self._extract_macro_trend(mtf_verdict)
        malign = self._extract_mtf_alignment(mtf_verdict)
        mscore = self._extract_mtf_score(mtf_verdict)
        fdir = _normalise_direction(flat_signal.direction if flat_signal else None)
        hdir = _normalise_direction(
            harmonic_verdict.direction if harmonic_verdict else None
        )

        LOG.debug(
            "Orchestrator: %s hstate=%s mtrend=%s malign=%s flat=%s",
            symbol, hstate, mtrend, malign, fdir,
        )

        # ── HOLD — nothing to resolve ────────────────────────────────
        if hstate == "IDLE" and flat_signal is None and malign == _NONE:
            return self._hold(
                symbol, "No valid signal from any path — all engines silent"
            )

        # ── GOLDEN SYNERGY (max priority) ────────────────────────────
        if hstate == "EXECUTE":
            decision = self._resolve_harmonic_execute(
                symbol, harmonic_verdict, hdir, mtrend, malign, flat_signal, fdir
            )
            if decision is not None:
                return self._finalise(decision)

        # ── HUNT MODE SHIELD ─────────────────────────────────────────
        if hstate == "HUNT_MODE":
            decision = self._resolve_hunt_mode(
                symbol, harmonic_verdict, hdir, flat_signal, fdir, mtrend
            )
            if decision is not None:
                return self._finalise(decision)

        # ── STANDARD FALLBACK ────────────────────────────────────────
        if flat_signal is not None:
            decision = self._resolve_standard_fallback(
                symbol, flat_signal, fdir, mtrend, malign, mscore
            )
            if decision is not None:
                return self._finalise(decision)

        # ── MTF-ONLY FALLBACK (C-path without flat) ──────────────────
        if mtf_verdict and malign != _NONE:
            return self._resolve_mtf_only(symbol, mtrend, malign)

        # ── Unresolvable ─────────────────────────────────────────────
        return self._hold(symbol, "Unresolvable conflict — all paths exhausted")

    def get_hunt_status(self, symbol: str) -> dict[str, Any]:
        """Query whether a symbol is currently in Hunt Mode."""
        if self._gate is None:
            return {"hunting": False, "state": "IDLE"}
        session = self._gate.active_sessions.get(symbol)
        if session is None:
            return {"hunting": False, "state": "IDLE"}
        return {
            "hunting": session.is_active,
            "state": session.state.name,
            "direction": session.meso.direction,
            "pattern": session.meso.pattern,
            "prz_upper": session.meso.prz_upper,
            "prz_lower": session.meso.prz_lower,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "trigger_count": len(session.triggers),
        }

    # ── Conflict Resolution Methods ─────────────────────────────────

    def _resolve_harmonic_execute(
        self,
        symbol: str,
        verdict: ConsensusVerdict | None,
        hdir: str,
        mtrend: str,
        malign: str,
        flat_signal: Signal | None,
        fdir: str,
    ) -> OrchestratorDecision | None:
        """
        Harmonic EXECUTE is the highest-priority signal.

        GOLDEN SYNERGY: EXECUTE + MTF macro aligns → STRONG, 2.0x
        EXECUTE only: MTF conflict/neutral → MODERATE, 1.5x
        """
        if verdict is None:
            return None

        signal = verdict.to_signal()
        if signal is None:
            LOG.warning("Orchestrator: EXECUTE verdict failed to_signal()")
            return None

        # Determine synergy level
        macro_aligned = _directions_match(hdir, mtrend)
        flat_aligned = flat_signal is not None and _directions_match(hdir, fdir)

        if macro_aligned and flat_aligned:
            # All three paths agree — Golden Synergy
            signal.grade = SignalGrade.STRONG
            signal.confidence = max(signal.confidence, 0.90)
            multiplier = 2.0
            path_desc = (
                f"Harmonic {verdict.metadata.get('pattern', '?')} EXECUTE "
                f"aligned with D1/H4 macro {mtrend} AND flat consensus "
                f"({fdir}) — Golden Synergy"
            )
        elif macro_aligned:
            signal.grade = SignalGrade.STRONG
            signal.confidence = max(signal.confidence, 0.82)
            multiplier = 1.5
            path_desc = (
                f"Harmonic {verdict.metadata.get('pattern', '?')} EXECUTE "
                f"aligned with D1/H4 macro {mtrend} — macro synergy"
            )
        else:
            signal.grade = SignalGrade.MODERATE
            multiplier = 1.0
            path_desc = (
                f"Harmonic {verdict.metadata.get('pattern', '?')} EXECUTE — "
                f"MTF macro {mtrend}/{malign}, executing on pattern strength alone"
            )

        # Merge flat signal metadata if available
        if flat_signal is not None and flat_signal.metadata:
            # Merge levels (harmonic overrides, flat fills gaps)
            for key in ("sl", "tp1", "tp2", "rr", "pips_sl", "pips_target"):
                if key not in signal.metadata and key in flat_signal.metadata:
                    signal.metadata[key] = flat_signal.metadata[key]
            # Merge custom metadata keys not already set by harmonic
            for key, val in flat_signal.metadata.items():
                if key not in signal.metadata and key not in (
                    "sl", "tp1", "tp2", "rr", "pips_sl", "pips_target",
                ):
                    signal.metadata[key] = val

        return OrchestratorDecision(
            signal=signal,
            stake_multiplier=multiplier,
            resolution_path=path_desc,
            metadata={
                "harmonised": True,
                "macro_aligned": macro_aligned,
                "flat_aligned": flat_aligned,
            },
        )

    def _resolve_hunt_mode(
        self,
        symbol: str,
        verdict: ConsensusVerdict | None,
        hdir: str,
        flat_signal: Signal | None,
        fdir: str,
        mtrend: str,
    ) -> OrchestratorDecision | None:
        """
        Hunt Mode Shield: the orchestrator MUST veto any opposing flat
        signal that would ruin a harmonic trap.

        If flat signal ALIGNS with hunt direction → let both stand, but
        do NOT execute yet (harmonic path needs micro trigger first).
        Return HOLD with hunt metadata so downstream knows.

        If flat signal OPPOSES hunt → VETO the flat signal. Force HOLD
        to protect the harmonic setup.

        If verdict is None but gate session is active → derive hunt
        direction from the session's meso state.
        """
        # Derive hunt direction from gate session if no verdict
        hunt_dir = hdir
        if hdir == "UNKNOWN" and self._gate is not None:
            session = self._gate.active_sessions.get(symbol)
            if session is not None:
                hunt_dir = session.meso.direction

        if flat_signal is None:
            return OrchestratorDecision(
                signal=None,
                stake_multiplier=0.0,
                resolution_path=(
                    f"HUNT_MODE: {hunt_dir} PRZ active — "
                    f"waiting for micro trigger, no flat signal to block"
                ),
                metadata={
                    "hunt_mode": True,
                    "prz_upper": (
                        verdict.metadata.get("prz_upper") if verdict else None
                    ),
                    "prz_lower": (
                        verdict.metadata.get("prz_lower") if verdict else None
                    ),
                },
            )

        if _directions_match(hunt_dir, fdir):
            LOG.info(
                "Orchestrator: %s HUNT_MODE %s — flat signal %s is supportive, "
                "holding for micro trigger",
                symbol, hunt_dir, fdir,
            )
            return OrchestratorDecision(
                signal=None,
                stake_multiplier=0.0,
                resolution_path=(
                    f"HUNT_MODE: {hunt_dir} PRZ active — flat signal "
                    f"{fdir} is supportive, holding for micro trigger confirmation"
                ),
                metadata={
                    "hunt_mode": True,
                    "flat_supportive": True,
                },
            )
        else:
            reason = (
                f"HUNT_MODE SHIELD: {hunt_dir} harmonic trap active — "
                f"VETOING opposing flat signal {fdir} to protect the setup"
            )
            LOG.warning("Orchestrator: %s %s", symbol, reason)
            return OrchestratorDecision(
                signal=None,
                stake_multiplier=0.0,
                resolution_path=reason,
                metadata={
                    "hunt_mode": True,
                    "flat_vetoed": True,
                    "flat_direction": fdir,
                    "harmon_direction": hunt_dir,
                },
            )

    def _resolve_standard_fallback(
        self,
        symbol: str,
        flat_signal: Signal,
        fdir: str,
        mtrend: str,
        malign: str,
        mscore: float,
    ) -> OrchestratorDecision | None:
        """
        Standard Quality Gate Fallback: harmonic is IDLE/REJECT.

        Cross-reference flat direction with MTF macro trend.
        """
        if flat_signal.confidence < self._min_conf:
            return self._hold(
                symbol,
                f"Flat signal confidence {flat_signal.confidence:.2f} "
                f"below minimum {self._min_conf}",
            )

        macro_aligned = _directions_match(fdir, mtrend)

        if macro_aligned:
            # Good — flat agrees with macro
            grade = (
                SignalGrade.STRONG if flat_signal.confidence >= 0.70
                else SignalGrade.MODERATE
            )
            flat_signal.grade = grade
            flat_signal.confidence = max(flat_signal.confidence, 0.60)
            flat_signal.source = SignalSource.CONSENSUS

            return OrchestratorDecision(
                signal=flat_signal,
                stake_multiplier=1.0,
                resolution_path=(
                    f"Standard fallback: flat signal {fdir} aligned with "
                    f"MTF macro {mtrend} ({malign})"
                ),
                metadata={"macro_aligned": True},
            )
        elif mtrend == _NEUTRAL:
            # Macro neutral — accept flat with moderate conviction
            flat_signal.grade = SignalGrade.MODERATE
            flat_signal.source = SignalSource.CONSENSUS

            return OrchestratorDecision(
                signal=flat_signal,
                stake_multiplier=0.75,
                resolution_path=(
                    f"Standard fallback: flat signal {fdir} — MTF macro "
                    f"neutral ({malign}), moderate confidence"
                ),
                metadata={"macro_aligned": False, "macro_neutral": True},
            )
        else:
            # Counter-trend — flat opposes macro
            if self._strict_ct or mscore >= self._macro_veto:
                return self._hold(
                    symbol,
                    f"Counter-trend REJECT: flat signal {fdir} opposes "
                    f"MTF macro {mtrend} ({malign}, score={mscore:.2f})"
                )
            else:
                # Downgrade
                flat_signal.grade = SignalGrade.WEAK
                flat_signal.confidence = min(flat_signal.confidence, 0.40)
                flat_signal.source = SignalSource.CONSENSUS

                return OrchestratorDecision(
                    signal=flat_signal,
                    stake_multiplier=0.5,
                    resolution_path=(
                        f"Counter-trend DOWNGRADE: flat signal {fdir} opposes "
                        f"MTF macro {mtrend} (weak, score={mscore:.2f})"
                    ),
                    metadata={"counter_trend": True, "macro_aligned": False},
                )

    def _resolve_mtf_only(
        self, symbol: str, mtrend: str, malign: str
    ) -> Signal | None:
        """
        C-path fallback: MTF data exists but no flat signal.

        Produces an 'observation-only' Signal — graded WEAK/NEUTRAL,
        meant for monitoring, not execution.
        """
        if mtrend == _NEUTRAL:
            return None  # truly nothing to say

        return None  # MTF-only without harmonic = observation, not execution

    # ── Helpers ────────────────────────────────────────────────────

    def _classify_harmonic(
        self, verdict: ConsensusVerdict | None, symbol: str
    ) -> str:
        """
        Classify harmonic state: EXECUTE, HUNT_MODE, REJECT, or IDLE.

        Checks the gate's live session state in addition to the verdict
        because HUNT_MODE sessions persist across calls.
        """
        if verdict is not None:
            if verdict.decision == GateState.EXECUTE:
                return "EXECUTE"
            if verdict.decision == GateState.HUNT_MODE:
                return "HUNT_MODE"
            if verdict.decision == GateState.REJECT:
                return "REJECT"

        # Check live gate sessions — a hunt may have been activated
        # in a previous cycle and not yet resolved
        if self._gate is not None:
            session = self._gate.active_sessions.get(symbol)
            if session is not None and session.is_active:
                return "HUNT_MODE"

        return "IDLE"

    @staticmethod
    def _extract_macro_trend(mtf_verdict: dict | None) -> str:
        """Extract macro trend from MTF verdict dict."""
        if mtf_verdict is None:
            return _NEUTRAL
        hier = mtf_verdict.get("hierarchical", {})
        trend = hier.get("macro_trend", mtf_verdict.get("macro_trend", _NEUTRAL))
        return trend if trend in (_BULLISH, _BEARISH, _NEUTRAL) else _NEUTRAL

    @staticmethod
    def _extract_mtf_alignment(mtf_verdict: dict | None) -> str:
        """Extract MTF alignment from MTF verdict dict."""
        if mtf_verdict is None:
            return _NONE
        hier = mtf_verdict.get("hierarchical", {})
        return hier.get("mtf_alignment", mtf_verdict.get("mtf_alignment", _NONE))

    @staticmethod
    def _extract_mtf_score(mtf_verdict: dict | None) -> float:
        """Extract consensus score from MTF verdict dict."""
        if mtf_verdict is None:
            return 0.0
        hier = mtf_verdict.get("hierarchical", {})
        return float(
            hier.get("consensus_score", mtf_verdict.get("consensus_score", 0.0))
        )

    def _finalise(self, decision: OrchestratorDecision) -> Signal | None:
        """
        Finalise an orchestrator decision into a Signal.

        Enriches metadata with the orchestrator_verdict block.
        Returns None if HOLD.
        """
        if decision.signal is None:
            return None

        # Build orchestrator verdict block
        orch_block: dict[str, Any] = {
            "resolution_path": decision.resolution_path,
            "stake_multiplier": decision.stake_multiplier,
            **decision.metadata,
        }

        # Merge into signal metadata (don't clobber existing keys)
        existing = decision.signal.metadata
        existing["orchestrator_verdict"] = orch_block
        decision.signal.metadata = existing

        decision.signal.source = SignalSource.CONSENSUS

        return decision.signal

    @staticmethod
    def _hold(symbol: str, reason: str) -> None:
        """Produce a HOLD (None) with logging."""
        LOG.info("Orchestrator HOLD: %s — %s", symbol, reason)
        return None

    # ── Utility: Stake Multiplier ──────────────────────────────────

    def calculate_stake_multiplier(
        self,
        flat_signal: Signal | None,
        mtf_verdict: dict | None,
        harmonic_verdict: ConsensusVerdict | None,
    ) -> float:
        """
        Calculate recommended stake multiplier (0.0 to 2.0).

        Convenience wrapper that runs the resolve_signals path without
        mutating state, returning only the multiplier.

        Golden Synergy (all 3 paths aligned) → 2.0x
        Harmonic EXECUTE + macro aligned         → 1.5x
        Standard flat + MTF aligned              → 1.0x
        Mixed / neutral                           → 0.75x
        Counter-trend (downgrade)                 → 0.5x
        Weak (< min) / HOLD                       → 0.0x
        """
        hstate = self._classify_harmonic(harmonic_verdict, "")
        mtrend = self._extract_macro_trend(mtf_verdict)
        malign = self._extract_mtf_alignment(mtf_verdict)
        mscore = self._extract_mtf_score(mtf_verdict)
        fdir = _normalise_direction(flat_signal.direction if flat_signal else None)
        hdir = _normalise_direction(
            harmonic_verdict.direction if harmonic_verdict else None
        )

        # Golden Synergy
        if hstate == "EXECUTE":
            macro_ok = _directions_match(hdir, mtrend)
            flat_ok = flat_signal is not None and _directions_match(hdir, fdir)
            if macro_ok and flat_ok:
                return 2.0
            if macro_ok:
                return 1.5
            return 1.0

        # Hunt Mode — no execution
        if hstate == "HUNT_MODE":
            return 0.0

        # Standard
        if flat_signal is not None:
            if flat_signal.confidence < self._min_conf:
                return 0.0
            if _directions_match(fdir, mtrend):
                return 1.0
            if mtrend == _NEUTRAL:
                return 0.75
            if self._strict_ct or mscore >= self._macro_veto:
                return 0.0
            return 0.5

        return 0.0
