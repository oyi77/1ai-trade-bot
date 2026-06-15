"""Comprehensive tests for all engine modules in tradebot/engines/.

Covers:
  - Engine base (abstract contract)
  - EngineConsensus (register/unregister, thresholds, weighted consensus)
  - Registry (discover, register, get)
  - All 11 individual engines (name, analyze with data, analyze empty)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from tradebot.engines import (
    ChaosEngine,
    CRTTBSEngine,
    Engine,
    EngineConsensus,
    FVGEngine,
    HermesLiquidityEngine,
    LayeringEngine,
    LiquidityEngine,
    QuantEngine,
    Registry,
    SessionLevelsEngine,
    SMCEngine,
    SweepEngine,
    TVEngine,
)
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _make_tick(price: float, symbol: str = "XAUUSD", epoch: int = 0) -> Tick:
    """Create a single Tick with the given price."""
    return Tick(
        symbol=symbol,
        price=price,
        epoch=epoch,
        timestamp=datetime.now(UTC),
    )


def gold_ticks(count: int, base: float = 3300.0, step: float = 0.5) -> list[Tick]:
    """Create ``count`` XAUUSD ticks with ascending prices.

    Prices form a gentle uptrend so that OHLCV bars derived from them
    contain realistic high/low/open/close relationships.
    """
    ticks: list[Tick] = []
    for i in range(count):
        # Oscillate slightly for realistic OHLCV
        offset = step * (i % 5 - 2)  # -2*step .. +2*step
        price = round(base + i * step * 0.1 + offset, 2)
        ticks.append(_make_tick(price, epoch=1_700_000_000 + i))
    return ticks


def downtrend_ticks(count: int, base: float = 3320.0, step: float = 0.5) -> list[Tick]:
    """Create ``count`` XAUUSD ticks with descending prices."""
    ticks: list[Tick] = []
    for i in range(count):
        offset = step * (i % 5 - 2)
        price = round(base - i * step * 0.1 + offset, 2)
        ticks.append(_make_tick(price, epoch=1_700_000_000 + i))
    return ticks


def high_volatility_ticks(count: int, base: float = 3300.0) -> list[Tick]:
    """Create ticks that alternate high/low for sweep/choch patterns."""
    ticks: list[Tick] = []
    for i in range(count):
        price = (base + (i % 20) * 2.0) if i % 2 == 0 else (base - (i % 15) * 1.5)
        ticks.append(_make_tick(round(price, 2), epoch=1_700_000_000 + i))
    return ticks


# A concrete Engine subclass for base-class tests
class _DummyEngine(Engine):
    @property
    def name(self) -> str:
        return "dummy"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        return None


class _SignalEngine(Engine):
    """Engine that always returns a fixed signal — useful for consensus tests."""

    def __init__(self, eng_name: str, confidence: float = 0.8, direction: str = "CALL"):
        self._name = eng_name
        self._confidence = confidence
        self._direction = direction

    @property
    def name(self) -> str:
        return self._name

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        if not ticks:
            return None
        return Signal(
            symbol="XAUUSD",
            direction=self._direction,
            predicted_digit=5,
            confidence=self._confidence,
            source=SignalSource.MOMEN,
            grade=SignalGrade.STRONG if self._confidence >= 0.7 else SignalGrade.MODERATE,
            metadata={"engine": self._name},
        )


class _ExceptionEngine(Engine):
    """Engine that always raises — for error-resilience tests."""

    @property
    def name(self) -> str:
        return "exploder"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        raise RuntimeError("boom")


# ===================================================================
# 1. Engine Base
# ===================================================================


class TestEngineBase:
    """Verify Engine ABC contract."""

    def test_engine_is_abstract_cannot_instantiate(self):
        """Engine is abstract — direct instantiation must raise TypeError."""
        with pytest.raises(TypeError, match="abstract"):
            Engine()

    def test_dummy_engine_has_name_property(self):
        """Concrete Engine subclass must expose a name property."""
        eng = _DummyEngine()
        assert eng.name == "dummy"

    async def test_dummy_engine_analyze_returns_none(self):
        """Concrete engine returning None on analyze()."""
        eng = _DummyEngine()
        result = await eng.analyze([])
        assert result is None

    def test_all_exported_engines_are_subclasses(self):
        """Every engine imported from tradebot.engines must subclass Engine."""
        from tradebot.engines import (
            ChaosEngine,
            CRTTBSEngine,
            FVGEngine,
            HermesLiquidityEngine,
            LayeringEngine,
            LiquidityEngine,
            QuantEngine,
            SessionLevelsEngine,
            SMCEngine,
            SweepEngine,
            TVEngine,
        )
        for cls in (
            SMCEngine, FVGEngine, LiquidityEngine, SweepEngine,
            ChaosEngine, CRTTBSEngine, TVEngine, QuantEngine,
            HermesLiquidityEngine, LayeringEngine, SessionLevelsEngine,
        ):
            assert issubclass(cls, Engine), f"{cls.__name__} does not subclass Engine"


# ===================================================================
# 2. EngineConsensus
# ===================================================================


class TestEngineConsensus:
    """EngineConsensus — aggregation, thresholds, error resilience."""

    def test_register_and_unregister(self):
        """register() adds, unregister() removes."""
        c = EngineConsensus()
        eng = _DummyEngine()
        c.register(eng, weight=2.0)
        assert "dummy" in c._engines
        assert c._weights["dummy"] == 2.0

        c.unregister("dummy")
        assert "dummy" not in c._engines
        assert "dummy" not in c._weights

    def test_unregister_nonexistent_is_noop(self):
        """unregister() with unknown name does not raise."""
        c = EngineConsensus()
        c.unregister("nonexistent")  # should not raise

    async def test_analyze_no_engines_returns_none(self):
        """No registered engines → analyze returns None."""
        c = EngineConsensus()
        result = await c.analyze(gold_ticks(10))
        assert result is None

    async def test_analyze_single_engine_produces_signal(self):
        """Single engine producing a valid signal → consensus signal."""
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_SignalEngine("e1", confidence=0.9))
        result = await c.analyze(gold_ticks(10))
        assert result is not None
        assert result.source == SignalSource.CONSENSUS
        assert result.confidence > 0

    async def test_analyze_multiple_engines_weighted_consensus(self):
        """Multiple engines with different confidences → weighted average.

        Consensus weights are keyed by engine name.
        """
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_SignalEngine("e1", confidence=0.9), weight=2.0)
        c.register(_SignalEngine("e2", confidence=0.5), weight=1.0)
        result = await c.analyze(gold_ticks(10))
        assert result is not None
        # Weighted average: (0.9 * 2.0 + 0.5 * 1.0) / 3.0 = 0.767
        assert result.confidence == pytest.approx(0.767, abs=0.01)
        assert result.metadata["consensus_count"] == 2
    async def test_min_engines_threshold(self):
        """If fewer engines produce signals than min_engines → None."""
        c = EngineConsensus(min_engines=3, min_confidence=0.1)
        c.register(_SignalEngine("e1", confidence=0.9))
        c.register(_SignalEngine("e2", confidence=0.8))
        # Only 2 engines, need 3
        result = await c.analyze(gold_ticks(10))
        assert result is None

    async def test_min_confidence_threshold(self):
        """Weighted confidence below min_confidence → None."""
        c = EngineConsensus(min_engines=1, min_confidence=0.95)
        c.register(_SignalEngine("e1", confidence=0.3))
        result = await c.analyze(gold_ticks(10))
        assert result is None

    async def test_engine_exception_doesnt_crash_consensus(self):
        """Engine raising an exception is skipped, not propagated."""
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_ExceptionEngine())
        c.register(_SignalEngine("good", confidence=0.8))
        result = await c.analyze(gold_ticks(10))
        assert result is not None
        assert result.metadata["consensus_count"] == 1

    async def test_all_engines_explode_returns_none(self):
        """If all engines raise, consensus returns None."""
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_ExceptionEngine())
        result = await c.analyze(gold_ticks(10))
        assert result is None

    async def test_engine_returning_none_skipped(self):
        """Engine returning None is excluded from consensus count."""
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_SignalEngine("good", confidence=0.8))
        # _DummyEngine returns None — should not count
        c.register(_DummyEngine())
        result = await c.analyze(gold_ticks(10))
        assert result is not None
        assert result.metadata["consensus_count"] == 1

    async def test_consensus_uses_best_signal_direction(self):
        """Consensus signal direction comes from highest-confidence engine."""
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_SignalEngine("weak", confidence=0.5, direction="PUT"))
        c.register(_SignalEngine("strong", confidence=0.9, direction="CALL"))
        result = await c.analyze(gold_ticks(10))
        assert result is not None
        assert result.direction == "CALL"

    async def test_consensus_metadata_has_engine_list(self):
        """Consensus metadata lists contributing engine names."""
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        c.register(_SignalEngine("alpha", confidence=0.7))
        c.register(_SignalEngine("beta", confidence=0.6))
        result = await c.analyze(gold_ticks(10))
        assert result is not None
        # metadata["engines"] contains engine names
        assert "alpha" in result.metadata["engines"]
        assert "beta" in result.metadata["engines"]
        assert result.metadata["consensus_count"] == 2

# ===================================================================
# 3. Registry
# ===================================================================


class TestRegistry:
    """Registry — discover, register, get."""

    def test_discover_finds_all_engines(self):
        """Registry.discover() should find all 12 concrete engines."""
        reg = Registry()
        engines = reg.discover()
        assert len(engines) == 13
        expected_names = {
            "smc_scalper",
            "fvg_detector",
            "liquidity_zones",
            "sweep_detector",
            "chaos_filter",
            "crt_tbs",
            "tv_engine",
            "quant_pattern",
            "hermes_liquidity_hunter",
            "layering",
            "session_levels",
            "whale_detector",
            "harmonic",
        }
        assert set(engines.keys()) == expected_names

    def test_register_adds_engine_manually(self):
        """Manual registration via register()."""
        reg = Registry()
        eng = _DummyEngine()
        reg.register(eng)
        assert reg.get("dummy") is eng

    def test_get_retrieves_by_name(self):
        """get() returns the registered engine instance."""
        reg = Registry()
        eng = _DummyEngine()
        reg.register(eng)
        assert reg.get("dummy") is not None
        assert reg.get("dummy").name == "dummy"

    def test_get_returns_none_for_unknown(self):
        """get() returns None for an unregistered name."""
        reg = Registry()
        assert reg.get("nonexistent") is None

    def test_all_property_returns_copy(self):
        """reg.all returns a dict copy, not the internal dict."""
        reg = Registry()
        eng = _DummyEngine()
        reg.register(eng)
        all_engines = reg.all
        assert "dummy" in all_engines
        # Mutating the copy shouldn't affect the registry
        all_engines.pop("dummy")
        assert reg.get("dummy") is not None

    def test_discover_nonexistent_package_returns_empty(self):
        """discover() with a bad package name returns empty dict."""
        reg = Registry()
        result = reg.discover(package="nonexistent.package.name")
        assert result == {}

    def test_register_overwrites_same_name(self):
        """Registering two engines with the same name → second wins."""
        reg = Registry()
        eng1 = _DummyEngine()
        eng2 = _SignalEngine("dummy", confidence=0.5)
        reg.register(eng1)
        reg.register(eng2)
        # eng2 has name "dummy" too
        assert reg.get("dummy") is eng2


# ===================================================================
# 4. Individual Engine Tests
# ===================================================================

# Common tick counts for engine minimums
_TICKS_60 = gold_ticks(60)
_TICKS_100 = gold_ticks(100)
_TICKS_200 = gold_ticks(200)


class TestSMCEngine:
    """SMC Scalper Engine tests."""

    def test_name(self):
        assert SMCEngine().name == "smc_scalper"

    async def test_analyze_empty_returns_none(self):
        result = await SMCEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """SMC requires >= 50 ticks."""
        result = await SMCEngine().analyze(gold_ticks(10))
        assert result is None

    async def test_analyze_with_enough_data_returns_signal_or_none(self):
        """With 200 ticks, SMC should return Signal or None (no crash)."""
        result = await SMCEngine().analyze(_TICKS_200)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_with_volatility_data(self):
        """High-volatility ticks should not crash the engine."""
        ticks = high_volatility_ticks(200)
        result = await SMCEngine().analyze(ticks)
        assert result is None or isinstance(result, Signal)


class TestFVGEngine:
    """Fair Value Gap Engine tests."""

    def test_name(self):
        assert FVGEngine().name == "fvg_detector"

    async def test_analyze_empty_returns_none(self):
        result = await FVGEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """FVG requires >= 3 ticks."""
        result = await FVGEngine().analyze([_make_tick(3300.0)])
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """FVG with 60 ticks → Signal or None."""
        result = await FVGEngine().analyze(_TICKS_60)
        assert result is None or isinstance(result, Signal)


class TestLiquidityEngine:
    """Liquidity Zone Mapping Engine tests."""

    def test_name(self):
        assert LiquidityEngine().name == "liquidity_zones"

    async def test_analyze_empty_returns_none(self):
        result = await LiquidityEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """Liquidity requires >= 6 ticks."""
        result = await LiquidityEngine().analyze(gold_ticks(3))
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """Liquidity with 60 ticks → Signal or None."""
        result = await LiquidityEngine().analyze(_TICKS_60)
        assert result is None or isinstance(result, Signal)


class TestSweepEngine:
    """Liquidity Sweep Detector Engine tests."""

    def test_name(self):
        assert SweepEngine().name == "sweep_detector"

    async def test_analyze_empty_returns_none(self):
        result = await SweepEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """Sweep requires >= 3 ticks."""
        result = await SweepEngine().analyze([_make_tick(3300.0)])
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """Sweep with 60 ticks → Signal or None."""
        result = await SweepEngine().analyze(_TICKS_60)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_with_volatility_returns_signal_or_none(self):
        """High-vol ticks may trigger sweep detection."""
        ticks = high_volatility_ticks(60)
        result = await SweepEngine().analyze(ticks)
        assert result is None or isinstance(result, Signal)


class TestChaosEngine:
    """Chaos / Fractal Filter Engine tests."""

    def test_name(self):
        assert ChaosEngine().name == "chaos_filter"

    async def test_analyze_empty_returns_none(self):
        result = await ChaosEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """Chaos requires >= 20 ticks."""
        result = await ChaosEngine().analyze(gold_ticks(5))
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """Chaos with 60 ticks → Signal or None."""
        result = await ChaosEngine().analyze(_TICKS_60)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_with_100_ticks(self):
        """More data for Hurst exponent calculation."""
        result = await ChaosEngine().analyze(_TICKS_100)
        assert result is None or isinstance(result, Signal)


class TestCRTTBSEngine:
    """CRT/TBS Engine tests."""

    def test_name(self):
        assert CRTTBSEngine().name == "crt_tbs"

    async def test_analyze_empty_returns_none(self):
        result = await CRTTBSEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """CRT requires >= 20 ticks."""
        result = await CRTTBSEngine().analyze(gold_ticks(5))
        assert result is None

    async def test_analyze_during_killzone(self):
        """CRT needs to be in a killzone (London 07-09, NY 13-16, LC 16-18 UTC).

        We mock datetime.now to return a London killzone time so the test
        is deterministic. The engine may still return None if Asian range
        or sweep conditions aren't met — we just verify no crash.
        """
        london_time = datetime(2026, 6, 9, 8, 0, 0, tzinfo=UTC)
        with patch("tradebot.engines.crt_tbs.datetime") as mock_dt:
            mock_dt.now.return_value = london_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await CRTTBSEngine().analyze(_TICKS_100)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_outside_killzone_returns_none(self):
        """Outside killzone (03:00 UTC) → engine returns None."""
        night_time = datetime(2026, 6, 9, 3, 0, 0, tzinfo=UTC)
        with patch("tradebot.engines.crt_tbs.datetime") as mock_dt:
            mock_dt.now.return_value = night_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await CRTTBSEngine().analyze(_TICKS_100)
        assert result is None


class TestTVEngine:
    """TradingView-Style Technical Analysis Engine tests."""

    def test_name(self):
        assert TVEngine().name == "tv_engine"

    async def test_analyze_empty_returns_none(self):
        result = await TVEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """TV requires >= 30 ticks."""
        result = await TVEngine().analyze(gold_ticks(10))
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """TV with 100 ticks → Signal or None."""
        result = await TVEngine().analyze(_TICKS_100)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_uptrend_ticks(self):
        """Uptrend data should not crash TV engine."""
        ticks = gold_ticks(100, base=3300.0, step=1.0)
        result = await TVEngine().analyze(ticks)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_downtrend_ticks(self):
        """Downtrend data should not crash TV engine."""
        ticks = downtrend_ticks(100, base=3350.0, step=1.0)
        result = await TVEngine().analyze(ticks)
        assert result is None or isinstance(result, Signal)


class TestQuantEngine:
    """Quantitative Pattern Engine tests."""

    def test_name(self):
        assert QuantEngine().name == "quant_pattern"

    async def test_analyze_empty_returns_none(self):
        result = await QuantEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """Quant requires >= 15 ticks (_MIN_HISTORY)."""
        result = await QuantEngine().analyze(gold_ticks(5))
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """Quant with 100 ticks → Signal or None."""
        result = await QuantEngine().analyze(_TICKS_100)
        assert result is None or isinstance(result, Signal)


class TestHermesLiquidityEngine:
    """Hermes Liquidity Hunter Engine tests."""

    def test_name(self):
        assert HermesLiquidityEngine().name == "hermes_liquidity_hunter"

    async def test_analyze_empty_returns_none(self):
        result = await HermesLiquidityEngine().analyze([])
        assert result is None

    async def test_analyze_insufficient_ticks_returns_none(self):
        """Hermes requires >= 10 ticks."""
        result = await HermesLiquidityEngine().analyze(gold_ticks(5))
        assert result is None

    async def test_analyze_with_data_returns_signal_or_none(self):
        """Hermes with 100 ticks → Signal or None."""
        result = await HermesLiquidityEngine().analyze(_TICKS_100)
        assert result is None or isinstance(result, Signal)

    async def test_analyze_with_volatility_returns_signal_or_none(self):
        """High-volatility data for sweep detection."""
        ticks = high_volatility_ticks(100, base=3300.0)
        result = await HermesLiquidityEngine().analyze(ticks)
        assert result is None or isinstance(result, Signal)


class TestLayeringEngine:
    """Smart Layering Engine tests."""

    def test_name(self):
        assert LayeringEngine().name == "layering"

    async def test_analyze_empty_returns_none(self):
        """Layering requires >= 1 tick, but empty list → None."""
        result = await LayeringEngine().analyze([])
        assert result is None

    async def test_analyze_with_single_tick_returns_signal(self):
        """Layering always returns a signal if at least 1 tick is given."""
        ticks = [_make_tick(3300.50)]
        result = await LayeringEngine().analyze(ticks)
        assert result is not None
        assert isinstance(result, Signal)
        assert result.direction == "CALL"
        assert result.metadata["engine"] == "layering"
        assert "layers" in result.metadata
        assert result.metadata["layer_count"] >= 1

    async def test_analyze_with_multiple_ticks(self):
        """Layering with multiple ticks — always produces a signal."""
        result = await LayeringEngine().analyze(gold_ticks(20))
        assert result is not None
        assert result.source == SignalSource.MOMEN
        assert "layer_spacing_pips" in result.metadata

    async def test_layering_metadata_has_layers_list(self):
        """Layering metadata contains the layer execution plan."""
        result = await LayeringEngine().analyze([_make_tick(3300.0)])
        assert result is not None
        layers = result.metadata["layers"]
        assert isinstance(layers, list)
        assert len(layers) >= 1
        # Each layer should have entry/sl/tp fields
        for layer in layers:
            assert "entry" in layer or "entry_price" in layer or "layer" in layer


class TestSessionLevelsEngine:
    """Session Level Calculator Engine tests."""

    def test_name(self):
        assert SessionLevelsEngine().name == "session_levels"

    async def test_analyze_empty_returns_none(self):
        result = await SessionLevelsEngine().analyze([])
        assert result is None

    async def test_analyze_with_data_returns_signal(self):
        """SessionLevels with enough ticks should return a signal."""
        result = await SessionLevelsEngine().analyze(_TICKS_100)
        assert result is not None
        assert isinstance(result, Signal)
        assert result.direction in ("CALL", "PUT")
        assert result.metadata["engine"] == "session_levels"

    async def test_analyze_single_tick(self):
        """Even a single tick should produce a signal (no minimum)."""
        result = await SessionLevelsEngine().analyze([_make_tick(3300.0)])
        assert result is not None

    async def test_session_levels_metadata(self):
        """Metadata should include session level data."""
        result = await SessionLevelsEngine().analyze(_TICKS_60)
        assert result is not None
        assert "session_levels" in result.metadata
        assert "is_nfp_friday" in result.metadata
        assert "nearest_level" in result.metadata

    async def test_session_levels_with_downtrend(self):
        """Downtrend ticks should work without crash."""
        ticks = downtrend_ticks(60, base=3350.0)
        result = await SessionLevelsEngine().analyze(ticks)
        assert result is not None
        assert isinstance(result, Signal)


# ===================================================================
# 5. Integration / Cross-cutting
# ===================================================================


class TestEngineIntegration:
    """Cross-cutting engine integration tests."""

    async def test_all_engines_handle_gold_ticks_without_crash(self):
        """Every engine must handle 100 realistic gold ticks without raising."""
        engines = [
            SMCEngine(), FVGEngine(), LiquidityEngine(), SweepEngine(),
            ChaosEngine(), TVEngine(), QuantEngine(),
            HermesLiquidityEngine(), LayeringEngine(), SessionLevelsEngine(),
        ]
        ticks = _TICKS_100
        for eng in engines:
            try:
                result = await eng.analyze(ticks)
                assert result is None or isinstance(result, Signal), (
                    f"{eng.name} returned unexpected type: {type(result)}"
                )
            except Exception as exc:
                pytest.fail(f"{eng.name} raised {type(exc).__name__}: {exc}")

    async def test_all_engines_handle_empty_ticks(self):
        """Every engine must return None (not crash) on empty tick list."""
        engines = [
            SMCEngine(), FVGEngine(), LiquidityEngine(), SweepEngine(),
            ChaosEngine(), CRTTBSEngine(), TVEngine(), QuantEngine(),
            HermesLiquidityEngine(), LayeringEngine(), SessionLevelsEngine(),
        ]
        for eng in engines:
            result = await eng.analyze([])
            assert result is None, f"{eng.name} did not return None for empty ticks"

    async def test_consensus_with_discovered_engines(self):
        """Registry-discovered engines can be registered with consensus."""
        reg = Registry()
        discovered = reg.discover()
        c = EngineConsensus(min_engines=1, min_confidence=0.1)
        for eng in discovered.values():
            c.register(eng)
        # Should not crash even if all engines return None
        result = await c.analyze(gold_ticks(100))
        assert result is None or isinstance(result, Signal)

    def test_registry_discover_returns_engine_instances(self):
        """All discovered engines are Engine instances with string names."""
        reg = Registry()
        engines = reg.discover()
        for name, eng in engines.items():
            assert isinstance(eng, Engine)
            assert isinstance(eng.name, str)
            assert eng.name == name

    async def test_engine_signal_has_required_fields(self):
        """Any signal returned must have the core fields populated."""
        engines_to_test = [
            LayeringEngine(),
            SessionLevelsEngine(),
        ]
        for eng in engines_to_test:
            result = await eng.analyze(_TICKS_60)
            if result is not None:
                assert result.symbol
                assert result.direction in ("CALL", "PUT")
                assert 0.0 <= result.confidence <= 1.0
                assert result.predicted_digit >= 0
                assert isinstance(result.metadata, dict)
