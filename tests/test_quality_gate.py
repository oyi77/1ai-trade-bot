"""Tests for QualityGate — validation, level calculation, grading, formatting."""

from __future__ import annotations

import pytest

from tradebot.models import Signal, SignalGrade, SignalSource
from tradebot.pipeline.quality_gate import ASSET_CONFIG, DEFAULT_CONFIG, QualityGate

# ── Helpers ──────────────────────────────────────────────────────────


def _make_signal(
    *,
    symbol: str = "XAUUSD",
    direction: str = "CALL",
    confidence: float = 0.6,
    consensus_score: float = 0.70,
    mtf_alignment: str = "ALIGNED",
    macro_trend: str = "BULLISH",
    counter_trend_flags: list[str] | None = None,
    timeframes: dict | None = None,
    current_price: float = 2000.0,
    **extra_meta,
) -> Signal:
    """Build a Signal with the metadata keys QualityGate expects."""
    meta = {
        "consensus_score": consensus_score,
        "mtf_alignment": mtf_alignment,
        "macro_trend": macro_trend,
        "counter_trend_flags": counter_trend_flags or [],
        "timeframes": timeframes or {},
        "current_price": current_price,
        **extra_meta,
    }
    return Signal(
        symbol=symbol,
        direction=direction,
        predicted_digit=5,
        confidence=confidence,
        source=SignalSource.CONSENSUS,
        metadata=meta,
    )


def _engines_with_direction(action: str, count: int = 10, agree: int = 8) -> dict:
    """Build a timeframes dict with engines that mostly agree."""
    engines = {}
    for i in range(count):
        d = action if i < agree else ("SELL" if action == "BUY" else "BUY")
        engines[f"eng_{i}"] = {"direction": d, "indicators": {"atr": 1.5}}
    return {
        "M5": {"engines": engines},
        "M15": {"engines": {}},
        "H1": {"engines": {}},
    }


# ── ASSET_CONFIG ─────────────────────────────────────────────────────


class TestAssetConfig:
    def test_xauusd_config_present(self):
        assert "XAUUSD" in ASSET_CONFIG
        cfg = ASSET_CONFIG["XAUUSD"]
        assert cfg["pip_value"] == 0.10
        assert cfg["min_sl_pts"] == 28
        assert cfg["max_sl_pts"] == 35

    def test_btcusd_config_present(self):
        assert "BTCUSD" in ASSET_CONFIG
        cfg = ASSET_CONFIG["BTCUSD"]
        assert cfg["pip_value"] == 0.1
        assert cfg["min_sl_pts"] == 600

    def test_ethusd_config_present(self):
        assert "ETHUSD" in ASSET_CONFIG

    def test_usoil_config_present(self):
        assert "USOIL" in ASSET_CONFIG

    def test_default_config_has_required_keys(self):
        for key in ("pip_value", "min_sl_pts", "min_rr", "max_rr", "sl_buffer_atr"):
            assert key in DEFAULT_CONFIG


# ── QualityGate.validate ─────────────────────────────────────────────


class TestValidate:
    @pytest.fixture
    def qg(self):
        return QualityGate()

    def test_passes_with_good_signal(self, qg):
        sig = _make_signal(
            consensus_score=0.70,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        assert qg.validate(sig) is True

    def test_fails_low_consensus(self, qg):
        sig = _make_signal(consensus_score=0.30)
        assert qg.validate(sig) is False

    def test_fails_alignment_conflict(self, qg):
        sig = _make_signal(mtf_alignment="CONFLICT")
        assert qg.validate(sig) is False

    def test_fails_counter_trend_bearish_macro_buy(self, qg):
        sig = _make_signal(
            direction="BUY",
            macro_trend="BEARISH",
            counter_trend_flags=["CT_FLAG"],
            consensus_score=0.60,  # below 0.75 threshold for counter-trend
        )
        assert qg.validate(sig) is False

    def test_passes_counter_trend_strong_score(self, qg):
        sig = _make_signal(
            direction="BUY",
            macro_trend="BEARISH",
            counter_trend_flags=["CT_FLAG"],
            consensus_score=0.80,
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        assert qg.validate(sig) is True

    def test_fails_counter_trend_neutral_macro(self, qg):
        sig = _make_signal(
            macro_trend="NEUTRAL",
            counter_trend_flags=["CT_FLAG"],
            consensus_score=0.80,
        )
        assert qg.validate(sig) is False

    def test_fails_low_engine_agreement(self, qg):
        # Only 2 out of 10 agree — below 50% threshold
        sig = _make_signal(
            consensus_score=0.70,
            timeframes=_engines_with_direction("BUY", 10, 2),
        )
        assert qg.validate(sig) is False

    def test_passes_exactly_50_percent_agreement(self, qg):
        sig = _make_signal(
            consensus_score=0.70,
            timeframes=_engines_with_direction("BUY", 10, 5),
        )
        assert qg.validate(sig) is True

    def test_fails_low_participation(self, qg):
        # Only 1 engine total — below max(6, total*0.3) = 6
        sig = _make_signal(
            consensus_score=0.70,
            timeframes=_engines_with_direction("BUY", 1, 1),
        )
        assert qg.validate(sig) is False

    def test_quality_gate_metadata_written(self, qg):
        sig = _make_signal(
            consensus_score=0.70,
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        qg.validate(sig)
        assert "quality_gate" in sig.metadata
        assert sig.metadata["quality_gate"]["passed"] is True
        assert "consensus_threshold" in sig.metadata["quality_gate"]["checks"]

    def test_sell_direction_mapped(self, qg):
        sig = _make_signal(
            direction="PUT",
            macro_trend="BEARISH",
            consensus_score=0.70,
            timeframes=_engines_with_direction("SELL", 10, 8),
        )
        assert qg.validate(sig) is True

    def test_counter_trend_sell_vs_bullish(self, qg):
        sig = _make_signal(
            direction="PUT",
            macro_trend="BULLISH",
            counter_trend_flags=["CT"],
            consensus_score=0.60,
        )
        assert qg.validate(sig) is False

    def test_passes_counter_trend_sell_strong(self, qg):
        sig = _make_signal(
            direction="PUT",
            macro_trend="BULLISH",
            counter_trend_flags=["CT"],
            consensus_score=0.80,
            timeframes=_engines_with_direction("SELL", 10, 8),
        )
        assert qg.validate(sig) is True

    def test_fails_macro_alignment_buy_bearish(self, qg):
        # No counter_trend_flags, but macro mismatch still checked
        sig = _make_signal(
            direction="BUY",
            macro_trend="BEARISH",
            consensus_score=0.60,
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        # macro_ok check: BUY vs BEARISH needs score >= 0.75
        assert qg.validate(sig) is False

    def test_empty_timeframes(self, qg):
        sig = _make_signal(
            consensus_score=0.70,
            timeframes={},
        )
        # No engines = eng_ok stays True (skipped), other checks pass
        assert qg.validate(sig) is True


# ── QualityGate.compute_levels ───────────────────────────────────────


class TestComputeLevels:
    @pytest.fixture
    def qg(self):
        return QualityGate()

    def test_buy_levels(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            current_price=2000.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 2.0}}}}},
        )
        assert qg.compute_levels(sig) is True
        assert sig.entry_price == 2000.0
        assert sig.metadata["sl"] < 2000.0  # SL below entry for BUY
        assert sig.metadata["tp1"] > 2000.0  # TP1 above entry for BUY
        assert sig.metadata["tp2"] > sig.metadata["tp1"]
        assert sig.metadata["rr"] >= DEFAULT_CONFIG["min_rr"]
        assert sig.metadata["pips_sl"] > 0
        assert sig.metadata["pips_target"] > 0

    def test_sell_levels(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="PUT",
            current_price=2000.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 2.0}}}}},
        )
        assert qg.compute_levels(sig) is True
        assert sig.entry_price == 2000.0
        assert sig.metadata["sl"] > 2000.0  # SL above entry for SELL
        assert sig.metadata["tp1"] < 2000.0  # TP1 below entry for SELL
        assert sig.metadata["tp2"] < sig.metadata["tp1"]
        assert sig.metadata["rr"] >= DEFAULT_CONFIG["min_rr"]

    def test_btcusd_levels(self, qg):
        sig = _make_signal(
            symbol="BTCUSD",
            direction="BUY",
            current_price=100000.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 150.0}}}}},
        )
        assert qg.compute_levels(sig) is True
        cfg = ASSET_CONFIG["BTCUSD"]
        assert sig.metadata["pips_sl"] >= cfg["min_sl_pts"]

    def test_ethusd_levels(self, qg):
        sig = _make_signal(
            symbol="ETHUSD",
            direction="BUY",
            current_price=3500.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 8.0}}}}},
        )
        assert qg.compute_levels(sig) is True
        assert sig.metadata["rr"] >= ASSET_CONFIG["ETHUSD"]["min_rr"]

    def test_usoil_levels(self, qg):
        sig = _make_signal(
            symbol="USOIL",
            direction="SELL",
            current_price=75.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 0.15}}}}},
        )
        assert qg.compute_levels(sig) is True
        assert sig.metadata["rr"] >= ASSET_CONFIG["USOIL"]["min_rr"]

    def test_fallback_atr_when_missing(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            current_price=2000.0,
            timeframes={},  # No engines → uses fallback ATR
        )
        assert qg.compute_levels(sig) is True
        assert sig.metadata["sl"] < 2000.0

    def test_fails_without_price(self, qg):
        sig = _make_signal(current_price=0.0)
        assert qg.compute_levels(sig) is False

    def test_unknown_symbol_uses_default(self, qg):
        sig = _make_signal(
            symbol="EURUSD",
            direction="BUY",
            current_price=1.1000,
            timeframes={},
        )
        assert qg.compute_levels(sig) is True
        assert sig.metadata["rr"] >= DEFAULT_CONFIG["min_rr"]

    def test_rr_capped_at_max(self, qg):
        # Very small ATR relative to price → high RR before capping
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            current_price=2000.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 0.01}}}}},
        )
        assert qg.compute_levels(sig) is True
        assert sig.metadata["rr"] <= ASSET_CONFIG["XAUUSD"]["max_rr"]

    def test_sl_respects_min_sl_pts(self, qg):
        cfg = ASSET_CONFIG["XAUUSD"]
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            current_price=2000.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 0.01}}}}},
        )
        assert qg.compute_levels(sig) is True
        # pips_sl should be at least min_sl_pts
        assert sig.metadata["pips_sl"] >= cfg["min_sl_pts"]

    def test_sl_capped_at_max_sl_pts(self, qg):
        cfg = ASSET_CONFIG["XAUUSD"]
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            current_price=2000.0,
            timeframes={"M5": {"engines": {"e1": {"indicators": {"atr": 100.0}}}}},
        )
        assert qg.compute_levels(sig) is True
        assert sig.metadata["pips_sl"] <= cfg["max_sl_pts"]


# ── QualityGate.grade ────────────────────────────────────────────────


class TestGrade:
    @pytest.fixture
    def qg(self):
        return QualityGate()

    def test_grade_a(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.85,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            timeframes=_engines_with_direction("BUY", 10, 8),  # 80% agree
        )
        grade = qg.grade(sig)
        assert grade == "A"
        assert sig.grade == SignalGrade.STRONG
        assert sig.confidence > 0.8
        assert sig.metadata["grade"] == "A"

    def test_grade_b(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.55,
            mtf_alignment="MIXED",
            macro_trend="NEUTRAL",
            timeframes=_engines_with_direction("BUY", 10, 6),
        )
        grade = qg.grade(sig)
        assert grade == "B"
        assert sig.grade == SignalGrade.MODERATE

    def test_grade_c_fallback(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.40,
            mtf_alignment="NONE",
            macro_trend="NEUTRAL",
            timeframes=_engines_with_direction("BUY", 10, 3),
        )
        grade = qg.grade(sig)
        assert grade == "C"
        assert sig.grade == SignalGrade.WEAK

    def test_grade_c_with_counter_trend(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.60,
            mtf_alignment="MIXED",
            macro_trend="NEUTRAL",
            counter_trend_flags=["CT"],
            timeframes=_engines_with_direction("BUY", 10, 6),
        )
        grade = qg.grade(sig)
        # Counter-trend present → not eligible for B
        assert grade == "C"

    def test_grade_a_requires_macro_aligned(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.85,
            mtf_alignment="ALIGNED",
            macro_trend="NEUTRAL",  # not BULLISH
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        grade = qg.grade(sig)
        assert grade != "A"  # macro not aligned

    def test_grade_a_requires_eng_pct(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.85,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            timeframes=_engines_with_direction("BUY", 10, 5),  # 50% < 65%
        )
        grade = qg.grade(sig)
        assert grade != "A"

    def test_confidence_capped(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.99,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            timeframes=_engines_with_direction("BUY", 10, 10),
        )
        qg.grade(sig)
        assert sig.confidence <= 0.98


# ── QualityGate.get_order_type ───────────────────────────────────────


class TestGetOrderType:
    def test_buy_at_market(self):
        assert QualityGate.get_order_type("BUY", 100.0, 100.0) == "BUY"

    def test_buy_limit_below(self):
        assert QualityGate.get_order_type("BUY", 99.0, 100.0) == "BUY LIMIT"

    def test_buy_stop_above(self):
        assert QualityGate.get_order_type("BUY", 101.0, 100.0) == "BUY STOP"

    def test_sell_at_market(self):
        assert QualityGate.get_order_type("SELL", 100.0, 100.0) == "SELL"

    def test_sell_limit_above(self):
        assert QualityGate.get_order_type("SELL", 101.0, 100.0) == "SELL LIMIT"

    def test_sell_stop_below(self):
        assert QualityGate.get_order_type("SELL", 99.0, 100.0) == "SELL STOP"

    def test_threshold_respected(self):
        # Within threshold → market
        assert QualityGate.get_order_type("BUY", 100.3, 100.0, threshold=0.5) == "BUY"
        # Outside threshold → limit/stop
        assert (
            QualityGate.get_order_type("BUY", 100.6, 100.0, threshold=0.5) == "BUY STOP"
        )


# ── QualityGate.format_telegram ──────────────────────────────────────


class TestFormatTelegram:
    @pytest.fixture
    def qg(self):
        return QualityGate()

    def test_buy_format(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            confidence=0.85,
            current_price=2000.0,
        )
        sig.entry_price = 2000.0
        sig.metadata.update(
            {
                "grade": "A",
                "sl": 1997.0,
                "tp1": 2004.5,
                "tp2": 2009.0,
                "rr": 1.5,
                "pips_sl": 30,
                "pips_target": 45,
                "reason": "MTF ALIGNED | BULLISH",
                "macro_trend": "BULLISH",
                "mtf_alignment": "ALIGNED",
            }
        )
        text = qg.format_telegram(sig)
        assert "🟢" in text
        assert "BUY" in text
        assert "XAUUSD" in text
        assert "Grade: <b>A</b>" in text
        assert "HIGH CONVICTION" in text
        assert "$2000.00" in text

    def test_sell_format(self, qg):
        sig = _make_signal(
            symbol="BTCUSD",
            direction="PUT",
            confidence=0.60,
            current_price=100000.0,
        )
        sig.entry_price = 100000.0
        sig.metadata.update(
            {
                "grade": "B",
                "sl": 100600.0,
                "tp1": 99100.0,
                "tp2": 98200.0,
                "rr": 1.5,
                "pips_sl": 600,
                "pips_target": 900,
                "reason": "NEUTRAL bias",
                "macro_trend": "NEUTRAL",
                "mtf_alignment": "MIXED",
            }
        )
        text = qg.format_telegram(sig)
        assert "🔴" in text
        assert "SELL" in text
        assert "BTCUSD" in text
        assert "Grade: <b>B</b>" in text
        assert "pantau entry area" in text

    def test_grade_c_format(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            confidence=0.45,
            current_price=2000.0,
        )
        sig.entry_price = 2000.0
        sig.metadata.update(
            {
                "grade": "C",
                "sl": 1997.0,
                "tp1": 2001.5,
                "tp2": 2003.0,
                "rr": 1.5,
                "pips_sl": 30,
                "pips_target": 15,
                "reason": "standard signal",
                "macro_trend": "NEUTRAL",
                "mtf_alignment": "NONE",
            }
        )
        text = qg.format_telegram(sig)
        assert "Sinyal standar" in text

    def test_html_escaped_reason(self, qg):
        sig = _make_signal(
            direction="BUY",
            current_price=2000.0,
        )
        sig.entry_price = 2000.0
        sig.metadata.update(
            {
                "grade": "C",
                "sl": 1997.0,
                "tp1": 2001.5,
                "tp2": 2003.0,
                "rr": 1.5,
                "pips_sl": 30,
                "pips_target": 15,
                "reason": "<script>alert('xss')</script>",
            }
        )
        text = qg.format_telegram(sig)
        assert "<script>" not in text
        assert "&lt;script&gt;" in text


# ── QualityGate.build_reason ─────────────────────────────────────────


class TestBuildReason:
    @pytest.fixture
    def qg(self):
        return QualityGate()

    def test_grade_a_reason(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.85,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        sig.metadata["grade"] = "A"
        reason = qg.build_reason(sig)
        assert "MTF ALIGNED" in reason
        assert "BULLISH" in reason
        assert "8/10" in reason

    def test_grade_b_reason(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.55,
            macro_trend="NEUTRAL",
            timeframes=_engines_with_direction("BUY", 10, 6),
        )
        sig.metadata["grade"] = "B"
        reason = qg.build_reason(sig)
        assert "NEUTRAL bias" in reason

    def test_counter_trend_flagged(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.70,
            macro_trend="BULLISH",
            counter_trend_flags=["Bearish divergence on H4", "Weak momentum"],
            timeframes=_engines_with_direction("BUY", 10, 7),
        )
        sig.metadata["grade"] = "B"
        reason = qg.build_reason(sig)
        assert "⚠️" in reason
        assert "Bearish divergence" in reason

    def test_reason_written_to_metadata(self, qg):
        sig = _make_signal(
            direction="BUY",
            consensus_score=0.70,
            macro_trend="BULLISH",
            timeframes=_engines_with_direction("BUY", 10, 7),
        )
        sig.metadata["grade"] = "B"
        qg.build_reason(sig)
        assert "reason" in sig.metadata
        assert len(sig.metadata["reason"]) > 0


# ── QualityGate.process (full pipeline) ──────────────────────────────


class TestProcess:
    @pytest.fixture
    def qg(self):
        return QualityGate()

    @pytest.mark.asyncio
    async def test_full_pipeline_buy(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            consensus_score=0.85,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            current_price=2000.0,
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        result = await qg.process(sig)
        assert result is not None
        assert result.entry_price == 2000.0
        assert "sl" in result.metadata
        assert "tp1" in result.metadata
        assert "tp2" in result.metadata
        assert "grade" in result.metadata
        assert "reason" in result.metadata
        assert result.metadata["grade"] in ("A", "B", "C")

    @pytest.mark.asyncio
    async def test_full_pipeline_sell(self, qg):
        sig = _make_signal(
            symbol="BTCUSD",
            direction="PUT",
            consensus_score=0.70,
            mtf_alignment="MIXED",
            macro_trend="BEARISH",
            current_price=100000.0,
            timeframes=_engines_with_direction("SELL", 10, 7),
        )
        result = await qg.process(sig)
        assert result is not None
        assert result.metadata["sl"] > 100000.0  # SELL → SL above
        assert result.metadata["tp1"] < 100000.0

    @pytest.mark.asyncio
    async def test_rejected_by_quality_gate(self, qg):
        sig = _make_signal(
            consensus_score=0.30,  # below 50%
            current_price=2000.0,
        )
        result = await qg.process(sig)
        assert result is None

    @pytest.mark.asyncio
    async def test_rejected_by_missing_price(self, qg):
        sig = _make_signal(
            consensus_score=0.70,
            current_price=0.0,
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        # Passes validation but fails level computation
        result = await qg.process(sig)
        assert result is None

    @pytest.mark.asyncio
    async def test_signal_enriched_with_all_fields(self, qg):
        sig = _make_signal(
            symbol="XAUUSD",
            direction="BUY",
            consensus_score=0.85,
            mtf_alignment="ALIGNED",
            macro_trend="BULLISH",
            current_price=2000.0,
            timeframes=_engines_with_direction("BUY", 10, 8),
        )
        result = await qg.process(sig)
        assert result is not None
        # Check all expected metadata keys
        expected = (
            "sl", "tp1", "tp2", "rr", "pips_sl",
            "pips_target", "grade", "reason", "quality_gate",
        )
        for key in expected:
            assert key in result.metadata, f"Missing metadata key: {key}"


# ── Integration: QualityGate in SignalPipeline ───────────────────────


class TestPipelineIntegration:
    def test_pipeline_with_quality_gate(self):
        """QualityGate wired into SignalPipeline as post-consensus step."""
        from tradebot.pipeline.signal_pipeline import SignalPipeline

        qg = QualityGate()
        pipeline = SignalPipeline(quality_gate=qg)
        assert pipeline.quality_gate is qg

    @pytest.mark.asyncio
    async def test_pipeline_without_quality_gate(self):
        """Pipeline works normally without a quality gate."""
        from tradebot.pipeline.signal_pipeline import SignalPipeline

        pipeline = SignalPipeline()
        assert pipeline.quality_gate is None

    def test_quality_gate_exported_from_pipeline(self):
        from tradebot.pipeline import QualityGate as Imported

        assert Imported is QualityGate
