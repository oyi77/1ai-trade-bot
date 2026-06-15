"""
Tests for tradebot.engines.meta_orchestrator — VilonaMetaOrchestrator.

Covers: Golden Synergy, Hunt Mode Shield, Standard Fallback,
Counter-Trend Rejection, Stake Multiplier, HOLDs, and edge cases.
"""
from __future__ import annotations

import pytest

from tradebot.engines.meta_orchestrator import (
    OrchestratorDecision,
    VilonaMetaOrchestrator,
)
from tradebot.engines.mtf_consensus import (
    ConsensusVerdict,
    GateState,
    MTFConsensusGate,
    MesoState,
    MicroTrigger,
    TriggerType,
)
from tradebot.models import Signal, SignalGrade, SignalSource


# ── Factories ──────────────────────────────────────────────────────────


def _flat_signal(**overrides) -> Signal:
    d = dict(
        symbol="XAUUSD", direction="BULLISH", predicted_digit=5,
        confidence=0.72, source=SignalSource.CONSENSUS,
        grade=SignalGrade.MODERATE, entry_price=2645.0,
        metadata={
            "sl": 2635.0, "tp1": 2658.0, "tp2": 2670.0,
            "rr": 2.0, "reason": "MTF ALIGNED | 8/11 engines agree",
        },
    )
    d.update(overrides)
    return Signal(**d)


def _harmonic_execute(**overrides) -> ConsensusVerdict:
    d = dict(
        decision=GateState.EXECUTE, symbol="XAUUSD", direction="BULLISH",
        entry_price=1922.5, sl=1918.0, tp1=1940.0, tp2=1955.0,
        confidence=0.80, macro_alignment=True,
        reason="CONFIRMED: gartley BULLISH AHZ → smc_choch at 1922.5",
        metadata={"pattern": "gartley", "ahz_upper": 1925.0, "ahz_lower": 1920.0},
    )
    d.update(overrides)
    return ConsensusVerdict(**d)


def _harmonic_hunt(**overrides) -> ConsensusVerdict:
    d = dict(
        decision=GateState.HUNT_MODE, symbol="XAUUSD", direction="BULLISH",
        reason="Hunt Mode active — awaiting micro confirmation",
        metadata={"pattern": "gartley", "ahz_upper": 1925.0, "ahz_lower": 1920.0},
    )
    d.update(overrides)
    return ConsensusVerdict(**d)


def _mtf_aligned_macro(mtrend="BULLISH") -> dict:
    return {
        "macro_trend": mtrend,
        "mtf_alignment": "ALIGNED",
        "consensus_score": 0.78,
        "verdict": "BUY",
        "counter_trend_flags": [],
        "hierarchical": {
            "macro_trend": mtrend,
            "mtf_alignment": "ALIGNED",
            "consensus_score": 0.78,
            "verdict": "BUY",
            "counter_trend_flags": [],
        },
    }


def _mtf_bearish() -> dict:
    return {
        "macro_trend": "BEARISH",
        "mtf_alignment": "ALIGNED",
        "consensus_score": 0.75,
        "verdict": "SELL",
        "counter_trend_flags": [],
        "hierarchical": {
            "macro_trend": "BEARISH",
            "mtf_alignment": "ALIGNED",
            "consensus_score": 0.75,
            "verdict": "SELL",
            "counter_trend_flags": [],
        },
    }


def _mtf_neutral() -> dict:
    return {
        "macro_trend": "NEUTRAL",
        "mtf_alignment": "MIXED",
        "consensus_score": 0.35,
        "verdict": "HOLD",
        "counter_trend_flags": [],
        "hierarchical": {
            "macro_trend": "NEUTRAL",
            "mtf_alignment": "MIXED",
            "consensus_score": 0.35,
            "verdict": "HOLD",
            "counter_trend_flags": [],
        },
    }


# ── Helpers ────────────────────────────────────────────────────────────


async def _resolve(orch, symbol="XAUUSD", flat=None, mtf=None, harm=None):
    return await orch.resolve_signals(
        symbol=symbol, flat_signal=flat, mtf_verdict=mtf, harmonic_verdict=harm,
    )


# ═══════════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestGoldenSynergy:
    """Harmonic EXECUTE + MTF aligned + flat aligned = MAX priority."""

    async def test_all_three_aligned(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BULLISH"),
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=_harmonic_execute(direction="BULLISH"),
        )
        assert sig is not None
        assert sig.direction == "BULLISH"
        assert sig.grade == SignalGrade.STRONG
        assert sig.confidence >= 0.90
        assert sig.source == SignalSource.CONSENSUS
        ov = sig.metadata["orchestrator_verdict"]
        assert "Golden Synergy" in ov["resolution_path"]
        assert ov["stake_multiplier"] == 2.0

    async def test_execute_with_macro_but_no_flat(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=None,
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=_harmonic_execute(direction="BULLISH"),
        )
        assert sig is not None
        assert sig.direction == "BULLISH"
        assert sig.grade == SignalGrade.STRONG
        ov = sig.metadata["orchestrator_verdict"]
        assert "macro synergy" in ov["resolution_path"] or "macro" in ov["resolution_path"]
        assert ov["stake_multiplier"] == 1.5

    async def test_execute_mtf_conflict_still_executes(self):
        """EXECUTE takes priority even if MTF disagrees."""
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=None,
            mtf=_mtf_bearish(),
            harm=_harmonic_execute(direction="BULLISH"),
        )
        assert sig is not None
        assert sig.direction == "BULLISH"
        assert sig.grade == SignalGrade.MODERATE
        ov = sig.metadata["orchestrator_verdict"]
        assert ov["stake_multiplier"] == 1.0

    async def test_flat_metadata_merged_into_harmonic(self):
        orch = VilonaMetaOrchestrator()
        flat = _flat_signal(metadata={"sl": 2635.0, "tp1": 2658.0, "tp2": 2670.0, "extra": 42})
        sig = await _resolve(
            orch,
            flat=flat,
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=_harmonic_execute(),
        )
        assert sig is not None
        assert sig.metadata["sl"] == 1918.0  # harmonic SL preserved
        assert sig.metadata["tp1"] == 1940.0  # harmonic TP preserved
        assert sig.metadata["extra"] == 42  # flat metadata merged


class TestHuntModeShield:
    """HUNT_MODE must veto opposing flat signals."""

    async def test_opposing_flat_vetoed(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BEARISH"),
            mtf=_mtf_aligned_macro("BEARISH"),
            harm=_harmonic_hunt(direction="BULLISH"),
        )
        assert sig is None  # VETOED

    async def test_supportive_flat_holds(self):
        """Flat agrees with hunt → hold, don't execute yet."""
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BULLISH"),
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=_harmonic_hunt(direction="BULLISH"),
        )
        assert sig is None  # HOLD — waiting for trigger

    async def test_hunt_no_flat_holds(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=None,
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=_harmonic_hunt(),
        )
        assert sig is None

    async def test_gate_session_hunt_shield(self):
        """Hunt shield works via live gate session, not just verdict."""
        gate = MTFConsensusGate()
        meso = MesoState(
            ahz_active=True, ahz_upper=1925.0, ahz_lower=1920.0,
            direction="BULLISH", pattern="gartley",
            sl=1918.0, tp1=1940.0, tp2=1955.0,
            confidence=0.85, symbol="XAUUSD",
        )
        gate.activate_hunt(meso=meso)

        orch = VilonaMetaOrchestrator(gate=gate)
        # No harmonic verdict passed, but gate session is active
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BEARISH"),
            mtf=_mtf_bearish(),
            harm=None,  # no verdict
        )
        assert sig is None  # VETOED by live hunt session


class TestStandardFallback:
    """Harmonic IDLE → rely on flat signal with MTF cross-reference."""

    async def test_flat_aligned_with_macro(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BULLISH"),
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=None,
        )
        assert sig is not None
        assert sig.direction == "BULLISH"
        ov = sig.metadata["orchestrator_verdict"]
        assert ov["stake_multiplier"] == 1.0
        assert "Standard fallback" in ov["resolution_path"]

    async def test_flat_neutral_macro(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BULLISH"),
            mtf=_mtf_neutral(),
            harm=None,
        )
        assert sig is not None
        ov = sig.metadata["orchestrator_verdict"]
        assert ov["stake_multiplier"] == 0.75

    async def test_counter_trend_rejected(self):
        orch = VilonaMetaOrchestrator(strict_counter_trend=True)
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BULLISH"),
            mtf=_mtf_bearish(),
            harm=None,
        )
        assert sig is None  # REJECTED

    async def test_counter_trend_downgraded(self):
        orch = VilonaMetaOrchestrator(
            strict_counter_trend=False, macro_veto_threshold=0.90
        )
        sig = await _resolve(
            orch,
            flat=_flat_signal(direction="BULLISH"),
            mtf=_mtf_bearish(),  # score 0.75 < veto 0.90 → downgrade
            harm=None,
        )
        assert sig is not None
        assert sig.grade == SignalGrade.WEAK
        ov = sig.metadata["orchestrator_verdict"]
        assert ov["stake_multiplier"] == 0.5

    async def test_low_confidence_flat_rejected(self):
        orch = VilonaMetaOrchestrator(min_fallback_confidence=0.60)
        sig = await _resolve(
            orch,
            flat=_flat_signal(confidence=0.35),
            mtf=_mtf_aligned_macro("BULLISH"),
            harm=None,
        )
        assert sig is None

    async def test_no_flat_no_mtf_returns_none(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(orch, flat=None, mtf=None, harm=None)
        assert sig is None


class TestStakeMultiplier:
    """calculate_stake_multiplier() standalone function."""

    def test_golden_synergy(self):
        orch = VilonaMetaOrchestrator()
        m = orch.calculate_stake_multiplier(
            _flat_signal(direction="BULLISH"),
            _mtf_aligned_macro("BULLISH"),
            _harmonic_execute(direction="BULLISH"),
        )
        assert m == 2.0

    def test_harmonic_execute_macro_only(self):
        orch = VilonaMetaOrchestrator()
        m = orch.calculate_stake_multiplier(
            None, _mtf_aligned_macro("BULLISH"),
            _harmonic_execute(direction="BULLISH"),
        )
        assert m == 1.5

    def test_hunt_mode_zero(self):
        orch = VilonaMetaOrchestrator()
        m = orch.calculate_stake_multiplier(
            _flat_signal(), _mtf_aligned_macro(), _harmonic_hunt(),
        )
        assert m == 0.0

    def test_standard_flat_aligned(self):
        orch = VilonaMetaOrchestrator()
        m = orch.calculate_stake_multiplier(
            _flat_signal(direction="BULLISH"),
            _mtf_aligned_macro("BULLISH"),
            None,
        )
        assert m == 1.0

    def test_counter_trend_zero_strict(self):
        orch = VilonaMetaOrchestrator(strict_counter_trend=True)
        m = orch.calculate_stake_multiplier(
            _flat_signal(direction="BULLISH"),
            _mtf_bearish(),
            None,
        )
        assert m == 0.0

    def test_counter_trend_half_nonstrict(self):
        orch = VilonaMetaOrchestrator(
            strict_counter_trend=False, macro_veto_threshold=0.90
        )
        m = orch.calculate_stake_multiplier(
            _flat_signal(direction="BULLISH"),
            _mtf_bearish(),  # macro bearish, score 0.75 < veto 0.90
            None,
        )
        assert m == 0.5

    def test_nothing_zero(self):
        orch = VilonaMetaOrchestrator()
        assert orch.calculate_stake_multiplier(None, None, None) == 0.0


class TestDirectionNormalisation:
    """_normalise_direction handles all variant forms."""

    def test_buy_call_long_all_bullish(self):
        for raw in ("BUY", "CALL", "LONG", "bullish"):
            from tradebot.engines.meta_orchestrator import _normalise_direction
            assert _normalise_direction(raw) == "BULLISH"

    def test_sell_put_short_all_bearish(self):
        for raw in ("SELL", "PUT", "SHORT", "bearish"):
            from tradebot.engines.meta_orchestrator import _normalise_direction
            assert _normalise_direction(raw) == "BEARISH"

    def test_unknown_passthrough(self):
        from tradebot.engines.meta_orchestrator import _normalise_direction
        assert _normalise_direction("SIDEWAYS") == "SIDEWAYS"
        assert _normalise_direction(None) == "UNKNOWN"


class TestOrchestratorDecision:
    """OrchestratorDecision dataclass validity."""

    def test_hold_decision(self):
        d = OrchestratorDecision(
            signal=None, stake_multiplier=0.0,
            resolution_path="HOLD — no signal",
        )
        assert d.signal is None
        assert d.stake_multiplier == 0.0

    def test_execute_decision(self):
        sig = _flat_signal()
        d = OrchestratorDecision(
            signal=sig, stake_multiplier=1.5,
            resolution_path="Harmonic EXECUTE aligned",
            metadata={"macro_aligned": True},
        )
        assert d.signal is not None
        assert d.stake_multiplier == 1.5
        assert d.metadata["macro_aligned"] is True


class TestSignalSource:
    """All orchestrated signals use SignalSource.CONSENSUS."""

    async def test_harmonic_execute_source(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            harm=_harmonic_execute(direction="BULLISH"),
            mtf=_mtf_aligned_macro("BULLISH"),
            flat=_flat_signal(direction="BULLISH"),
        )
        assert sig.source == SignalSource.CONSENSUS

    async def test_standard_fallback_source(self):
        orch = VilonaMetaOrchestrator()
        sig = await _resolve(
            orch,
            flat=_flat_signal(),
            mtf=_mtf_aligned_macro(),
        )
        assert sig.source == SignalSource.CONSENSUS
