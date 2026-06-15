"""
Engine Consensus Adapter — wraps scripts/engine_consensus.py.

Provides a unified async interface to the MTF (Multi-Timeframe) engine
consensus pipeline: 5-timeframe analysis, 9-engine weighted voting,
and signal generation with quality grading.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)

# Ensure scripts/ is on sys.path for engine module imports
_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


@dataclass
class ConsensusConfig:
    """Configuration for the engine consensus adapter."""

    symbol: str = "XAUUSD"
    timeframes: list[str] = field(
        default_factory=lambda: ["D1", "H4", "H1", "M15", "M5"]
    )
    fetch_timeout: int = 90
    cache_ttl_d1: int = 14400
    cache_ttl_h4: int = 900


@dataclass
class EngineVote:
    """A single engine's vote on a timeframe."""

    engine: str = ""
    direction: str = "HOLD"
    confidence: float = 0.0
    details: str = ""


@dataclass
class TFAnalysis:
    """Analysis result for a single timeframe."""

    timeframe: str = ""
    engines: dict[str, EngineVote] = field(default_factory=dict)
    buy_count: int = 0
    sell_count: int = 0
    total: int = 0
    verdict: str = "HOLD"
    consensus_pct: float = 0.0


@dataclass
class ConsensusResult:
    """Full consensus output across all timeframes."""

    symbol: str = ""
    timestamp: str = ""
    macro_trend: str = "NEUTRAL"
    macro_strength: float = 0.0
    macro_detail: dict = field(default_factory=dict)
    tf_results: dict[str, TFAnalysis] = field(default_factory=dict)
    final_verdict: str = "HOLD"
    final_confidence: float = 0.0
    signal: Optional[dict] = None
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class EngineConsensusAdapter:
    """
    Adapter wrapping the engine_consensus.py MTF pipeline.

    Usage in UnifiedBot:
        consensus = EngineConsensusAdapter(config)
        await consensus.initialize()
        result = await consensus.analyze(symbol="XAUUSD")
    """

    def __init__(self, config: Optional[ConsensusConfig] = None):
        self.config = config or ConsensusConfig()
        self._initialized = False

    async def initialize(self) -> bool:
        """Ensure the scripts path and lazy-load engines."""
        try:
            if _SCRIPTS_DIR not in sys.path:
                sys.path.insert(0, _SCRIPTS_DIR)
            self._initialized = True
            LOG.info("EngineConsensusAdapter initialized (symbol: %s)", self.config.symbol)
            return True
        except Exception as e:
            LOG.error("EngineConsensusAdapter init failed: %s", e)
            return False

    async def analyze(self, symbol: Optional[str] = None) -> ConsensusResult:
        """
        Run the full MTF engine consensus analysis.

        Returns a ConsensusResult with per-TF analysis and final verdict.
        """
        if not self._initialized:
            await self.initialize()

        sym = symbol or self.config.symbol
        result = ConsensusResult(symbol=sym)
        start = time.time()

        try:
            result = await asyncio.to_thread(self._run_consensus, sym, result)
        except Exception as e:
            result.errors.append(f"Consensus analysis failed: {e}")
            LOG.error("Consensus analysis error: %s", e)

        result.elapsed_seconds = round(time.time() - start, 1)
        return result

    def _run_consensus(self, symbol: str, result: ConsensusResult) -> ConsensusResult:
        """Synchronous consensus run — dispatched to thread pool."""
        try:
            from engine_consensus import (
                fetch_mtf_ohlcv,
                _run_engines_on_tf,
                _vectorized_macro_trend,
                TIMEFRAMES,
                TF_WEIGHTS,
            )
        except ImportError as e:
            result.errors.append(f"Cannot import engine_consensus: {e}")
            return result

        from datetime import datetime, timezone

        result.timestamp = datetime.now(timezone.utc).isoformat()

        # 1. Fetch multi-timeframe OHLCV data
        try:
            ohlcv_data = fetch_mtf_ohlcv(symbol)
        except Exception as e:
            result.errors.append(f"OHLCV fetch failed: {e}")
            return result

        # 2. Run engines on each timeframe
        import yfinance as yf  # for current price

        try:
            yf_sym = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
                       "USOIL": "CL=F"}.get(symbol, symbol)
            ticker = yf.Ticker(yf_sym)
            price = ticker.fast_info.last_price or 0.0
        except Exception:
            price = 0.0

        tf_results: dict[str, TFAnalysis] = {}
        for tf in TIMEFRAMES:
            bars = ohlcv_data.get(tf, [])
            if not bars or len(bars) < 50:
                tf_results[tf] = TFAnalysis(
                    timeframe=tf,
                    engines={},
                    buy_count=0,
                    sell_count=0,
                    total=0,
                    verdict="HOLD",
                    consensus_pct=0.0,
                )
                continue

            try:
                raw = _run_engines_on_tf(bars, price, symbol, tf)
                engines = {}
                for eng_name, eng_data in raw.get("engines", {}).items():
                    engines[eng_name] = EngineVote(
                        engine=eng_name,
                        direction=eng_data.get("direction", "HOLD"),
                        confidence=float(eng_data.get("confidence", 0) or 0),
                        details=str(eng_data.get("details", "")),
                    )
                tf_results[tf] = TFAnalysis(
                    timeframe=tf,
                    engines=engines,
                    buy_count=raw.get("buy_count", 0),
                    sell_count=raw.get("sell_count", 0),
                    total=raw.get("total", 0),
                    verdict=raw.get("verdict", "HOLD"),
                    consensus_pct=raw.get("consensus_pct", 0.0),
                )
            except Exception as e:
                tf_results[tf] = TFAnalysis(
                    timeframe=tf,
                    engines={},
                    buy_count=0,
                    sell_count=0,
                    total=0,
                    verdict="HOLD",
                    consensus_pct=0.0,
                )
                result.errors.append(f"TF {tf} analysis error: {e}")

        result.tf_results = tf_results

        # 3. Macro trend (D1 + H4)
        try:
            d1_bars = ohlcv_data.get("D1", [])
            if d1_bars and len(d1_bars) >= 50:
                macro = _vectorized_macro_trend(d1_bars, price)
                result.macro_trend = macro.get("trend", "NEUTRAL")
                result.macro_strength = macro.get("strength", 0.0)
                result.macro_detail = macro
        except Exception as e:
            result.errors.append(f"Macro trend error: {e}")

        # 4. Final verdict: weighted MTF consensus
        try:
            result.final_verdict, result.final_confidence = self._compute_final(
                tf_results, result.macro_trend, TF_WEIGHTS
            )
        except Exception as e:
            result.errors.append(f"Final verdict error: {e}")

        # 5. Build signal dict if we have a directional verdict
        if result.final_verdict in ("BUY", "SELL") and result.final_confidence > 0.4:
            result.signal = self._build_signal(
                symbol, result, price, tf_results
            )

        return result

    def _compute_final(
        self,
        tf_results: dict[str, TFAnalysis],
        macro_trend: str,
        tf_weights: dict[str, float],
    ) -> tuple[str, float]:
        """Compute weighted final verdict from all TFs."""
        buy_score = 0.0
        sell_score = 0.0
        total_weight = 0.0

        for tf_name, tf_res in tf_results.items():
            w = tf_weights.get(tf_name, 0.1)
            if tf_res.total > 0:
                if tf_res.verdict == "BUY":
                    buy_score += w * tf_res.consensus_pct
                elif tf_res.verdict == "SELL":
                    sell_score += w * tf_res.consensus_pct
            total_weight += w

        if total_weight == 0:
            return "HOLD", 0.0

        norm_buy = buy_score / total_weight
        norm_sell = sell_score / total_weight

        # Macro trend bias
        if macro_trend == "BULLISH":
            norm_buy *= 1.2
            norm_sell *= 0.8
        elif macro_trend == "BEARISH":
            norm_sell *= 1.2
            norm_buy *= 0.8

        if norm_buy > norm_sell and norm_buy > 0.25:
            return "BUY", min(norm_buy, 1.0)
        elif norm_sell > norm_buy and norm_sell > 0.25:
            return "SELL", min(norm_sell, 1.0)
        return "HOLD", 0.0

    def _build_signal(
        self,
        symbol: str,
        result: ConsensusResult,
        price: float,
        tf_results: dict[str, TFAnalysis],
    ) -> dict:
        """Build a structured signal dict from consensus results."""
        direction = result.final_verdict
        confidence = result.final_confidence

        # Simple TP/SL based on direction
        if direction == "BUY":
            sl = price * 0.995 if price > 0 else 0
            tp = price * 1.01 if price > 0 else 0
        else:
            sl = price * 1.005 if price > 0 else 0
            tp = price * 0.99 if price > 0 else 0

        # Collect layers from trigger TFs (M5, M15)
        layers = []
        for tf_name in ("M5", "M15"):
            tf_res = tf_results.get(tf_name)
            if tf_res and tf_res.verdict == direction:
                engine_names = [
                    e.engine
                    for e in tf_res.engines.values()
                    if e.direction == direction
                ]
                layers.append(
                    {
                        "timeframe": tf_name,
                        "consensus_pct": tf_res.consensus_pct,
                        "engines": engine_names,
                    }
                )

        return {
            "signal_id": f"consensus_{symbol}_{int(time.time())}",
            "symbol": symbol,
            "action": direction,
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "confidence": round(confidence, 2),
            "macro_trend": result.macro_trend,
            "layers": layers,
            "comment": f"MTF Consensus | {len(layers)} layers",
            "timestamp": result.timestamp,
        }

    async def shutdown(self) -> None:
        self._initialized = False
        LOG.info("EngineConsensusAdapter shutdown")
