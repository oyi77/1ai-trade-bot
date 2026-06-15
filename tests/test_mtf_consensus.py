"""
Tests for tradebot.engines.mtf_consensus — MTF Consensus Gate.

Covers: state machine, macro alignment, micro trigger validation,
AHZ invalidation, expiry, composite confidence, and Signal conversion.
"""
from __future__ import annotations

import pytest

from tradebot.engines.mtf_consensus import (
    ConsensusVerdict,
    GateState,
    HuntSession,
    MacroBias,
    MacroState,
    MesoState,
    MicroTrigger,
    MTFConsensusGate,
    TriggerType,
    macro_from_signal,
    meso_from_signal,
)
from tradebot.models import Signal, SignalGrade, SignalSource


# ── Fixtures ───────────────────────────────────────────────────────────


def _bullish_meso(**overrides) -> MesoState:
    defaults = dict(
        ahz_active=True,
        ahz_upper=1925.0,
        ahz_lower=1920.0,
        direction="BULLISH",
        pattern="gartley",
        sl=1918.0,
        tp1=1940.0,
        tp2=1955.0,
        confidence=0.85,
        symbol="XAUUSD",
    )
    defaults.update(overrides)
    return MesoState(**defaults)


def _bearish_meso(**overrides) -> MesoState:
    defaults = dict(
        ahz_active=True,
        ahz_upper=2010.0,
        ahz_lower=2005.0,
        direction="BEARISH",
        pattern="bat",
        sl=2012.0,
        tp1=1990.0,
        tp2=1980.0,
        confidence=0.78,
        symbol="XAUUSD",
    )
    defaults.update(overrides)
    return MesoState(**defaults)


def _bullish_macro(**overrides) -> MacroState:
    defaults = dict(
        bias=MacroBias.BULLISH,
        confidence=0.7,
        source_engine="smc_scalper",
        market_structure="HH-HL",
    )
    defaults.update(overrides)
    return MacroState(**defaults)


def _bearish_macro(**overrides) -> MacroState:
    defaults = dict(
        bias=MacroBias.BEARISH,
        confidence=0.7,
        source_engine="smc_scalper",
        market_structure="LH-LL",
    )
    defaults.update(overrides)
    return MacroState(**defaults)


def _smc_choch_trigger(**overrides) -> MicroTrigger:
    defaults = dict(
        trigger_type=TriggerType.SMC_CHOCH,
        symbol="XAUUSD",
        price=1922.5,
        direction="BULLISH",
        confidence=0.8,
        source_engine="smc_scalper",
    )
    defaults.update(overrides)
    return MicroTrigger(**defaults)


def _fvg_trigger(**overrides) -> MicroTrigger:
    defaults = dict(
        trigger_type=TriggerType.FVG_FILL,
        symbol="XAUUSD",
        price=1921.0,
        direction="BULLISH",
        confidence=0.65,
        source_engine="fvg_detector",
    )
    defaults.update(overrides)
    return MicroTrigger(**defaults)


def _inactive_meso() -> MesoState:
    return _bullish_meso(ahz_active=False)


# ── Test: Gate State Machine ───────────────────────────────────────────


class TestGateStateMachine:
    def test_initial_state_is_idle(self):
        gate = MTFConsensusGate()
        assert gate.hunt_count == 0
        assert gate.active_sessions == {}

    def test_activate_hunt_creates_session(self):
        gate = MTFConsensusGate()
        result = gate.activate_hunt(
            meso=_bullish_meso(),
            macro=_bullish_macro(),
        )
        assert result.decision == GateState.HUNT_MODE
        assert gate.hunt_count == 1
        assert "XAUUSD" in gate.active_sessions

    def test_inactive_ahz_returns_reject(self):
        gate = MTFConsensusGate()
        result = gate.activate_hunt(meso=_inactive_meso())
        assert result.decision == GateState.REJECT
        assert "not active" in result.reason

    def test_micro_trigger_returns_none_without_session(self):
        gate = MTFConsensusGate()
        trigger = _smc_choch_trigger()
        result = gate.process_micro_trigger(trigger)
        assert result is None

    def test_execute_on_valid_trigger(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso(), macro=_bullish_macro())

        trigger = _smc_choch_trigger(price=1922.5)
        result = gate.process_micro_trigger(trigger, current_price=1922.5)
        assert result is not None
        assert result.decision == GateState.EXECUTE
        assert result.entry_price == 1922.5
        assert result.sl == 1918.0
        assert result.tp1 == 1940.0

    def test_multiple_symbols_independent(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(
            meso=_bullish_meso(symbol="XAUUSD"),
            macro=_bullish_macro(),
        )
        gate.activate_hunt(
            meso=_bearish_meso(symbol="BTCUSD"),
            macro=_bearish_macro(),
        )
        assert gate.hunt_count == 2

        trigger = _smc_choch_trigger(symbol="XAUUSD", price=1922.5)
        result = gate.process_micro_trigger(trigger, current_price=1922.5)
        assert result is not None and result.decision == GateState.EXECUTE
        assert gate.hunt_count == 1  # BTCUSD still hunting


# ── Test: Macro Alignment ──────────────────────────────────────────────


class TestMacroAlignment:
    def test_bullish_macro_aligns_bullish_harmonic(self):
        gate = MTFConsensusGate()
        result = gate.activate_hunt(
            meso=_bullish_meso(), macro=_bullish_macro()
        )
        assert result.decision == GateState.HUNT_MODE
        assert result.macro_alignment is True

    def test_bearish_macro_rejects_bullish_harmonic(self):
        gate = MTFConsensusGate()
        result = gate.activate_hunt(
            meso=_bullish_meso(), macro=_bearish_macro()
        )
        assert result.decision == GateState.REJECT
        assert result.macro_alignment is False
        assert "conflicts" in result.reason

    def test_neutral_macro_passes(self):
        gate = MTFConsensusGate()
        result = gate.activate_hunt(
            meso=_bullish_meso(),
            macro=MacroState(bias=MacroBias.NEUTRAL, confidence=0.5, source_engine="test"),
        )
        assert result.decision == GateState.HUNT_MODE

    def test_missing_macro_when_not_required(self):
        gate = MTFConsensusGate(require_macro=False)
        result = gate.activate_hunt(meso=_bullish_meso(), macro=None)
        assert result.decision == GateState.HUNT_MODE

    def test_missing_macro_when_required(self):
        gate = MTFConsensusGate(require_macro=True)
        result = gate.activate_hunt(meso=_bullish_meso(), macro=None)
        assert result.decision == GateState.HUNT_MODE  # No macro = no conflict

    def test_low_confidence_macro_skips_check(self):
        gate = MTFConsensusGate(min_macro_confidence=0.5)
        result = gate.activate_hunt(
            meso=_bullish_meso(),
            macro=_bearish_macro(confidence=0.3),
        )
        assert result.decision == GateState.HUNT_MODE


# ── Test: Micro Trigger Validation ─────────────────────────────────────


class TestMicroTrigger:
    def test_trigger_outside_ahz_ignored(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())

        trigger = _smc_choch_trigger(price=1950.0)  # Way above AHZ
        result = gate.process_micro_trigger(trigger, current_price=1950.0)
        assert result is None
        assert not trigger.within_ahz

    def test_trigger_within_ahz_executes(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())

        trigger = _smc_choch_trigger(price=1922.0)
        result = gate.process_micro_trigger(trigger, current_price=1922.0)
        assert result is not None
        assert result.decision == GateState.EXECUTE

    def test_low_confidence_trigger_ignored(self):
        gate = MTFConsensusGate(min_trigger_confidence=0.7)
        gate.activate_hunt(meso=_bullish_meso())

        trigger = _smc_choch_trigger(price=1922.0, confidence=0.3)
        result = gate.process_micro_trigger(trigger, current_price=1922.0)
        assert result is None

    def test_wrong_direction_trigger_ignored(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())

        trigger = _smc_choch_trigger(price=1922.0, direction="BEARISH")
        result = gate.process_micro_trigger(trigger, current_price=1922.0)
        assert result is None

    def test_ahz_buffer_allows_boundary(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())  # AHZ [1920, 1925]

        # 5% buffer: 1920 - 0.25 = 1919.75
        trigger = _smc_choch_trigger(price=1919.80)
        result = gate.process_micro_trigger(trigger, current_price=1919.80)
        assert result is not None

    def test_fvg_trigger_works(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())

        trigger = _fvg_trigger(price=1921.0)
        result = gate.process_micro_trigger(trigger, current_price=1921.0)
        assert result is not None
        assert result.decision == GateState.EXECUTE
        assert result.micro_trigger.trigger_type == TriggerType.FVG_FILL


# ── Test: AHZ Invalidation ─────────────────────────────────────────────


class TestAHZInvalidation:
    def test_bullish_invalidated_below_sl(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())  # SL=1918.0

        result = gate.check_invalidation(1917.5, "XAUUSD")
        assert result is not None
        assert result.decision == GateState.REJECT
        assert "breached" in result.reason

    def test_bullish_not_invalidated_above_sl(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())  # SL=1918.0

        result = gate.check_invalidation(1918.5, "XAUUSD")
        assert result is None

    def test_bearish_invalidated_above_sl(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bearish_meso())  # SL=2012.0

        result = gate.check_invalidation(2012.5, "XAUUSD")
        assert result is not None
        assert result.decision == GateState.REJECT

    def test_bearish_not_invalidated_below_sl(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bearish_meso())  # SL=2012.0

        result = gate.check_invalidation(2011.0, "XAUUSD")
        assert result is None

    def test_no_session_returns_none(self):
        gate = MTFConsensusGate()
        result = gate.check_invalidation(1917.0, "XAUUSD")
        assert result is None

    def test_rejected_session_ignores_further_checks(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso())

        # Invalidate
        gate.check_invalidation(1917.0, "XAUUSD")
        # Second check on same symbol — no active session
        result = gate.check_invalidation(1916.0, "XAUUSD")
        assert result is None


# ── Test: Expiry & Cleanup ─────────────────────────────────────────────


class TestExpiry:
    def test_expired_hunt_rejected(self):
        gate = MTFConsensusGate(hunt_ttl_seconds=60)
        gate.activate_hunt(meso=_bullish_meso(), now=1000.0)

        expired = gate.cleanup_expired(now=1070.0)
        assert len(expired) == 1
        assert expired[0].decision == GateState.REJECT
        assert "expired" in expired[0].reason
        assert gate.hunt_count == 0

    def test_active_hunt_not_cleaned(self):
        gate = MTFConsensusGate(hunt_ttl_seconds=60)
        gate.activate_hunt(meso=_bullish_meso(), now=1000.0)

        expired = gate.cleanup_expired(now=1030.0)
        assert len(expired) == 0
        assert gate.hunt_count == 1

    def test_stale_trigger_on_expired_session(self):
        gate = MTFConsensusGate(hunt_ttl_seconds=60)
        gate.activate_hunt(meso=_bullish_meso(), now=1000.0)

        trigger = _smc_choch_trigger(price=1922.5)
        result = gate.process_micro_trigger(trigger, current_price=1922.5, now=1070.0)
        assert result is not None
        assert result.decision == GateState.REJECT
        assert "expired" in result.reason


# ── Test: Composite Confidence ─────────────────────────────────────────


class TestCompositeConfidence:
    def test_all_three_tiers(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(
            meso=_bullish_meso(confidence=0.9),
            macro=_bullish_macro(confidence=0.8),
        )

        trigger = _smc_choch_trigger(price=1922.5, confidence=0.7)
        result = gate.process_micro_trigger(trigger, current_price=1922.5)
        assert result is not None
        # 0.9*0.4 + 0.7*0.35 + 0.8*0.25 = 0.36 + 0.245 + 0.2 = 0.805
        assert 0.75 < result.confidence < 0.85

    def test_without_macro_redistributes(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(
            meso=_bullish_meso(confidence=0.9),
            macro=None,
        )

        trigger = _smc_choch_trigger(price=1922.5, confidence=0.7)
        result = gate.process_micro_trigger(trigger, current_price=1922.5)
        assert result is not None
        # 0.9*0.65 + 0.7*0.35 = 0.585 + 0.245 = 0.83
        assert 0.78 < result.confidence < 0.88


# ── Test: Verdict to Signal ────────────────────────────────────────────


class TestVerdictToSignal:
    def test_execute_verdict_produces_signal(self):
        verdict = ConsensusVerdict(
            decision=GateState.EXECUTE,
            symbol="XAUUSD",
            direction="BULLISH",
            entry_price=1922.5,
            sl=1918.0,
            tp1=1940.0,
            tp2=1955.0,
            confidence=0.82,
        )
        signal = verdict.to_signal()
        assert signal is not None
        assert signal.symbol == "XAUUSD"
        assert signal.direction == "BULLISH"
        assert signal.entry_price == 1922.5
        assert signal.source == SignalSource.CONSENSUS
        assert signal.metadata["sl"] == 1918.0
        assert signal.metadata["tp1"] == 1940.0
        assert signal.metadata["consensus"] is True

    def test_reject_verdict_returns_none(self):
        verdict = ConsensusVerdict(
            decision=GateState.REJECT,
            symbol="XAUUSD",
            direction="BULLISH",
            reason="macro conflict",
        )
        assert verdict.to_signal() is None

    def test_hunt_verdict_returns_none(self):
        verdict = ConsensusVerdict(
            decision=GateState.HUNT_MODE,
            symbol="XAUUSD",
            direction="BULLISH",
        )
        assert verdict.to_signal() is None


# ── Test: Helper Functions ─────────────────────────────────────────────


class TestHelpers:
    def test_meso_from_signal_with_prz(self):
        signal = Signal(
            symbol="XAUUSD",
            direction="BULLISH",
            predicted_digit=5,
            confidence=0.85,
            source=SignalSource.MOMEN,
            metadata={
                "AHZ_Active": True,
                "ahz_upper": 1925.0,
                "ahz_lower": 1920.0,
                "sl": 1918.0,
                "tp1": 1940.0,
                "tp2": 1955.0,
                "pattern": "gartley",
                "confidence": 0.85,
            },
        )
        meso = meso_from_signal(signal)
        assert meso is not None
        assert meso.ahz_active is True
        assert meso.ahz_upper == 1925.0
        assert meso.direction == "BULLISH"
        assert meso.pattern == "gartley"

    def test_meso_from_signal_without_prz(self):
        signal = Signal(
            symbol="XAUUSD",
            direction="CALL",
            predicted_digit=5,
            confidence=0.6,
            source=SignalSource.MOMEN,
            metadata={},
        )
        assert meso_from_signal(signal) is None

    def test_macro_from_signal_bullish(self):
        signal = Signal(
            symbol="XAUUSD",
            direction="BULLISH",
            predicted_digit=5,
            confidence=0.7,
            source=SignalSource.CONSENSUS,
            metadata={"market_structure": "HH-HL"},
        )
        macro = macro_from_signal(signal)
        assert macro is not None
        assert macro.bias == MacroBias.BULLISH
        assert macro.confidence == 0.7

    def test_macro_from_signal_neutral(self):
        signal = Signal(
            symbol="XAUUSD",
            direction="NEUTRAL",
            predicted_digit=5,
            confidence=0.3,
            source=SignalSource.CONSENSUS,
        )
        macro = macro_from_signal(signal)
        assert macro is not None
        assert macro.bias == MacroBias.NEUTRAL


# ── Test: Reset & History ──────────────────────────────────────────────


class TestResetAndHistory:
    def test_reset_single_symbol(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso(symbol="XAUUSD"))
        gate.activate_hunt(meso=_bearish_meso(symbol="BTCUSD"))
        gate.reset(symbol="XAUUSD")
        assert gate.hunt_count == 1
        assert "BTCUSD" in gate.active_sessions

    def test_reset_all(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso(symbol="XAUUSD"))
        gate.activate_hunt(meso=_bearish_meso(symbol="BTCUSD"))
        gate.reset()
        assert gate.hunt_count == 0

    def test_history_tracking(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso(), macro=_bullish_macro())
        trigger = _smc_choch_trigger(price=1922.5)
        gate.process_micro_trigger(trigger, current_price=1922.5)

        history = gate.get_history()
        assert len(history) == 2  # HUNT_MODE + EXECUTE
        assert history[0].decision == GateState.HUNT_MODE
        assert history[1].decision == GateState.EXECUTE

    def test_history_filtered_by_symbol(self):
        gate = MTFConsensusGate()
        gate.activate_hunt(meso=_bullish_meso(symbol="XAUUSD"))
        gate.activate_hunt(meso=_bearish_meso(symbol="BTCUSD"))

        history = gate.get_history(symbol="XAUUSD")
        assert len(history) == 1
        assert history[0].symbol == "XAUUSD"


# ── Test: HuntSession ──────────────────────────────────────────────────


class TestHuntSession:
    def test_best_trigger_returns_highest_confidence(self):
        session = HuntSession(
            symbol="XAUUSD",
            meso=_bullish_meso(),
            state=GateState.HUNT_MODE,
        )
        low = _smc_choch_trigger(price=1922.0, confidence=0.5)
        low.within_ahz = True
        high = _fvg_trigger(price=1921.0, confidence=0.8)
        high.within_ahz = True
        session.triggers = [low, high]

        assert session.best_trigger is not None
        assert session.best_trigger.confidence == 0.8

    def test_best_trigger_skips_outside_prz(self):
        session = HuntSession(
            symbol="XAUUSD",
            meso=_bullish_meso(),
            state=GateState.HUNT_MODE,
        )
        outside = _smc_choch_trigger(price=1950.0, confidence=0.9)
        outside.within_ahz = False
        inside = _fvg_trigger(price=1921.0, confidence=0.6)
        inside.within_ahz = True
        session.triggers = [outside, inside]

        assert session.best_trigger is not None
        assert session.best_trigger.price == 1921.0

    def test_best_trigger_none_when_empty(self):
        session = HuntSession(
            symbol="XAUUSD",
            meso=_bullish_meso(),
            state=GateState.HUNT_MODE,
        )
        assert session.best_trigger is None
