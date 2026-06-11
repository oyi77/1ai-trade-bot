"""
EngineConsensus — aggregates signals from multiple engines into a single decision.
MTFConsensus — multi-timeframe hierarchical consensus engine (D1→H4→H1→M15→M5).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from tradebot.engines.base import Engine
from tradebot.engines.registry import Registry
from tradebot.models import OHLCV, Signal, SignalSource, Tick
from tradebot.signals.yahoo import YahooSource

LOG = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# TIMEFRAME CONFIGURATION (mirrors scripts/engine_consensus.py)
# ════════════════════════════════════════════════════════════════

TIMEFRAMES: list[str] = ["D1", "H4", "H1", "M15", "M5"]

TF_WEIGHTS: dict[str, float] = {
    "D1": 0.35, "H4": 0.25, "H1": 0.20, "M15": 0.12, "M5": 0.08,
}

TF_CACHE_TTL: dict[str, int] = {
    "D1": 14400, "H4": 900, "H1": 900, "M15": 0, "M5": 0,  # seconds
}

TF_YF_INTERVAL: dict[str, str] = {
    "D1": "1d", "H4": "60m", "H1": "60m", "M15": "15m", "M5": "5m",
}

TF_YF_PERIOD: dict[str, str] = {
    "D1": "6mo", "H4": "1mo", "H1": "14d", "M15": "5d", "M5": "2d",
}

TF_BAR_COUNT: dict[str, int] = {
    "D1": 120, "H4": 240, "H1": 168, "M15": 480, "M5": 576,
}

TF_ENGINE_MIN: dict[str, int] = {
    "D1": 80, "H4": 100, "H1": 72, "M15": 96, "M5": 96,
}

SYMBOL_MAP: dict[str, str] = {
    "XAUUSD": "XAUUSD=X",   # spot, not GC=F futures (~$75 diff)
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "USOIL": "CL=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
}


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _ohlcv_to_dicts(bars: list[OHLCV]) -> list[dict]:
    """Convert OHLCV objects to plain dicts for vectorised functions."""
    return [
        {
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]


def _dicts_to_ticks(dicts: list[dict], symbol: str = "") -> list[Tick]:
    """Convert OHLCV dicts to Tick objects for Engine.analyze()."""
    return [
        Tick(
            symbol=symbol,
            price=float(d.get("close", 0)),
            epoch=i,
        )
        for i, d in enumerate(dicts)
    ]


# ════════════════════════════════════════════════════════════════
# ENGINE CONSENSUS (original — unchanged API)
# ════════════════════════════════════════════════════════════════


class EngineConsensus:
    """Aggregates signals from multiple engines into a single consensus signal.

    Strategy:
      - Collect signals from all registered engines
      - Weight signals by confidence and engine reliability
      - Produce a single consensus signal if threshold is met
    """

    def __init__(self, min_confidence: float = 0.5, min_engines: int = 1):
        self.min_confidence = min_confidence
        self.min_engines = min_engines
        self._engines: dict[str, Engine] = {}
        self._weights: dict[str, float] = {}

    def register(self, engine: Engine, weight: float = 1.0):
        """Register an engine with an optional weight factor."""
        self._engines[engine.name] = engine
        self._weights[engine.name] = weight
        LOG.info("Registered engine: %s (weight=%.1f)", engine.name, weight)

    def unregister(self, name: str):
        self._engines.pop(name, None)
        self._weights.pop(name, None)

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Run all registered engines and produce a consensus signal.

        Returns a consensus Signal if enough engines agree
        above the confidence threshold.
        """
        if not self._engines:
            return None

        signals: list[tuple[str, Signal]] = []
        for name, engine in self._engines.items():
            try:
                sig = await engine.analyze(ticks)
                if sig and sig.is_valid:
                    signals.append((name, sig))
            except Exception as e:
                LOG.warning("Engine %s error: %s", name, e)
                continue

        if len(signals) < self.min_engines:
            return None

        # Weighted average confidence — weight by engine name, not source.value
        total_weight = sum(self._weights.get(name, 1.0) for name, _s in signals)
        weighted_conf = sum(
            _s.confidence * self._weights.get(name, 1.0)
            for name, _s in signals
        ) / total_weight if total_weight > 0 else 0.0

        if weighted_conf < self.min_confidence:
            return None

        # Build consensus signal from the strongest individual signal
        best_name, best = max(signals, key=lambda ns: ns[1].confidence)
        return Signal(
            symbol=best.symbol,
            direction=best.direction,
            predicted_digit=best.predicted_digit,
            confidence=weighted_conf,
            source=SignalSource.CONSENSUS,
            grade=best.grade,
            metadata={
                "consensus_count": len(signals),
                "engines": [name for name, _s in signals],
                "individual_signals": [
                    {"source": name, "confidence": _s.confidence}
                    for name, _s in signals
                ],
            },
        )


# ════════════════════════════════════════════════════════════════
# MTF CONSENSUS — Multi-Timeframe Hierarchical Consensus
# ════════════════════════════════════════════════════════════════


class MTFConsensus:
    """Multi-Timeframe Hierarchical Consensus Engine.

    5-Timeframe (D1, H4, H1, M15, M5) top-down analysis:
      - D1 & H4 → Macro Trend Filter (weight 0.35 + 0.25 = 0.60)
      - H1 & M15 → Structure Setup — FVG, Liquidity, S/R (0.20 + 0.12 = 0.32)
      - M5 → Execution / Trigger — Sniper Entry (0.08)

    Features:
      - Smart caching (D1 4 h TTL, H4/H1 15 min, M15/M5 real-time)
      - Vectorised Pandas calculations for trend, S/R, entry triggers
      - Hierarchical verdict with counter-trend detection
      - Telegram pulse-text formatting

    Example::

        mtf = MTFConsensus(registry)
        result = await mtf.analyze("XAUUSD")
        text = MTFConsensus.format_pulse_text(result)
    """

    def __init__(self, registry: Registry | None = None):
        self._registry = registry
        self._cache: dict[str, list[dict]] = {}
        self._cache_time: dict[str, float] = {}
        self._last_result: dict[str, Any] | None = None

    # ── data fetching ──────────────────────────────────────────

    async def fetch_multi_tf(
        self, symbol: str = "XAUUSD",
    ) -> dict[str, list[dict]]:
        """Fetch OHLCV bars for all five timeframes via YahooSource.

        Respects smart-cache TTLs — D1/H4/H1 are served from cache
        when fresh; M15/M5 are always re-fetched.

        Returns ``{tf: [bar_dict, ...]}`` (may contain empty lists for
        timeframes that returned no data).
        """
        yf_symbol = SYMBOL_MAP.get(symbol, symbol)
        now = time.monotonic()
        result: dict[str, list[dict]] = {}
        to_fetch: list[str] = []

        for tf in TIMEFRAMES:
            ttl = TF_CACHE_TTL[tf]
            cache_key = f"{yf_symbol}/{tf}"
            if (ttl > 0 and cache_key in self._cache
                    and now - self._cache_time.get(cache_key, 0) < ttl):
                cached = self._cache[cache_key]
                if len(cached) >= TF_ENGINE_MIN.get(tf, 72):
                    result[tf] = cached
                    LOG.debug("cache-hit %s %s (%d bars)", yf_symbol, tf, len(cached))
                    continue
            to_fetch.append(tf)

        if to_fetch:
            async with YahooSource() as src:
                coros = {
                    tf: src.fetch(
                        symbol=yf_symbol,
                        interval=TF_YF_INTERVAL[tf],
                        count=TF_BAR_COUNT[tf],
                    )
                    for tf in to_fetch
                }
                outcomes = await asyncio.gather(
                    *coros.values(), return_exceptions=True,
                )
                for tf, outcome in zip(coros.keys(), outcomes):
                    if isinstance(outcome, Exception):
                        LOG.warning("fetch %s %s failed: %s", yf_symbol, tf, outcome)
                        result.setdefault(tf, [])
                        continue
                    dicts = _ohlcv_to_dicts(outcome)
                    ttl = TF_CACHE_TTL[tf]
                    cache_key = f"{yf_symbol}/{tf}"
                    if ttl > 0:
                        self._cache[cache_key] = dicts
                        self._cache_time[cache_key] = now
                    result[tf] = dicts

        for tf in TIMEFRAMES:
            result.setdefault(tf, [])

        return result

    # ── single-TF engine execution ─────────────────────────────

    async def _run_engines_on_tf(
        self, ohlcv: list[dict], symbol: str, tf: str,
    ) -> dict:
        """Run all registered engines on one timeframe's OHLCV.

        Returns ``{engines, buy_count, sell_count, total, verdict, consensus_pct}``.
        """
        engines: dict[str, dict] = {}
        buys = sells = active = 0
        registry = self._registry
        if registry is None:
            registry = Registry()
            registry.discover()
            self._registry = registry

        ticks = _dicts_to_ticks(ohlcv, symbol)
        all_engines = registry.all
        if not all_engines:
            return {
                "engines": engines,
                "buy_count": 0,
                "sell_count": 0,
                "total": 0,
                "verdict": "HOLD",
                "consensus_pct": 0.0,
            }

        for eng in all_engines.values():
            try:
                sig = await eng.analyze(ticks)
            except Exception as exc:
                LOG.warning("Engine %s error on %s: %s", eng.name, tf, exc)
                sig = None

            if sig and sig.is_valid and sig.direction in ("BUY", "SELL"):
                engines[eng.name] = {
                    "direction": sig.direction,
                    "confidence": sig.confidence,
                    "details": str(sig.metadata.get("details", "")),
                }
                if sig.direction == "BUY":
                    buys += 1
                else:
                    sells += 1
                active += 1
            else:
                engines[eng.name] = {
                    "direction": "HOLD",
                    "confidence": 0.0,
                    "details": "no signal",
                }

        # TF-local verdict (same logic as scripts/engine_consensus.py)
        verdict = "HOLD"
        consensus_pct = 0.0
        if active > 0:
            majority = active // 2 + 1
            min_votes = min(4, max(2, active // 2 + 1))
            threshold = max(majority, min_votes)
            max_votes = max(buys, sells)
            consensus_pct = max_votes / active
            if buys >= threshold and buys > sells:
                verdict = "BUY"
            elif sells >= threshold and sells > buys:
                verdict = "SELL"

        return {
            "engines": engines,
            "buy_count": buys,
            "sell_count": sells,
            "total": active,
            "verdict": verdict,
            "consensus_pct": consensus_pct,
        }

    # ── vectorised macro trend (D1 & H4) ───────────────────────

    @staticmethod
    def _vectorized_macro_trend(ohlcv: list[dict], price: float) -> dict:
        """Pandas-vectorised macro-trend analysis for D1/H4.

        Returns ``{trend, strength, ema200_dist, rsi, atr_pct, sma_cross}``.
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]
            df = pd.DataFrame(ohlcv)
            if len(df) < 50:
                return {"trend": "NEUTRAL", "strength": 0.0, "ema200_dist": 0.0}

            close = df["close"].astype(float)
            close_vals = close.values
            price_f = float(close_vals[-1])

            # EMA 200
            ema200 = pd.Series(close_vals).ewm(span=200, adjust=False).mean().iloc[-1]
            ema200_dist = (
                (price_f - ema200) / ema200 * 100
                if ema200 and not pd.isna(ema200) else 0.0
            )

            # SMA 20 / 50 cross
            sma20 = pd.Series(close_vals).rolling(20).mean()
            sma50 = pd.Series(close_vals).rolling(50).mean()
            sma_trend = "BULLISH" if sma20.iloc[-1] > sma50.iloc[-1] else "BEARISH"

            # HH / HL (last 10 bars)
            last_10 = close_vals[-10:]
            making_higher = last_10[-1] > last_10[0]
            making_lower = last_10[-1] < last_10[0]

            # ATR ratio
            atr = pd.Series(close_vals).diff().abs().rolling(14).mean().iloc[-1]
            atr_pct = atr / price_f * 100 if price_f > 0 else 0.0

            # RSI (14)
            delta = pd.Series(close_vals).diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, float("nan"))
            rsi = (100 - (100 / (1 + rs))).iloc[-1]

            # Score-based trend
            bullish_score = 0
            bearish_score = 0

            if sma_trend == "BULLISH":
                bullish_score += 2
            else:
                bearish_score += 2

            if price_f > ema200:
                bullish_score += 2
            else:
                bearish_score += 2

            if making_higher:
                bullish_score += 1
            if making_lower:
                bearish_score += 1

            if rsi > 50:
                bullish_score += 1
            else:
                bearish_score += 1

            total_score = bullish_score + bearish_score
            if total_score == 0:
                trend = "NEUTRAL"
                strength = 0.0
            elif bullish_score > bearish_score:
                trend = "BULLISH"
                strength = bullish_score / total_score
            elif bearish_score > bullish_score:
                trend = "BEARISH"
                strength = bearish_score / total_score
            else:
                trend = "NEUTRAL"
                strength = 0.0

            return {
                "trend": trend,
                "strength": round(strength, 2),
                "ema200_dist": round(ema200_dist, 2),
                "rsi": round(rsi, 1) if not pd.isna(rsi) else 50.0,
                "atr_pct": round(atr_pct, 2),
                "sma_cross": sma_trend,
            }
        except Exception as exc:
            LOG.warning("macro-trend calc error: %s", exc)
            return {"trend": "NEUTRAL", "strength": 0.0, "ema200_dist": 0.0}

    # ── vectorised structure / S/R (H1 & M15) ──────────────────

    @staticmethod
    def _vectorized_snr_levels(ohlcv: list[dict], price: float) -> dict:
        """Find support/resistance levels and structure boundaries.

        Returns ``{s1, r1, vwap, near_support, near_resistance, range_pct, structure_type}``.
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]
            df = pd.DataFrame(ohlcv)
            if len(df) < 30:
                return {
                    "s1": 0, "r1": 0,
                    "near_support": False, "near_resistance": False,
                    "structure_type": "unknown",
                }

            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            # Recent swing high/low (last 20 bars)
            recent_high = float(high.tail(20).max())
            recent_low = float(low.tail(20).min())
            range_pct = (recent_high - recent_low) / price * 100 if price > 0 else 0.0

            vol = (
                df["volume"].astype(float).tail(20)
                if "volume" in df.columns else pd.Series([1] * 20)
            )
            vwap = (close.tail(20) * vol).sum() / vol.sum() if vol.sum() > 0 else price

            # Distance to levels
            dist_to_high = (recent_high - price) / price * 100 if price > 0 else 0.0
            dist_to_low = (price - recent_low) / price * 100 if price > 0 else 0.0
            near_resistance = dist_to_high < 0.5
            near_support = dist_to_low < 0.5

            # Structure type
            if range_pct < 1.0:
                structure = "compressed"
            elif range_pct < 2.5:
                structure = "normal"
            else:
                structure = "expanded"

            return {
                "s1": round(recent_low, 2),
                "r1": round(recent_high, 2),
                "vwap": round(vwap, 2),
                "near_support": near_support,
                "near_resistance": near_resistance,
                "range_pct": round(range_pct, 2),
                "structure_type": structure,
            }
        except Exception as exc:
            LOG.warning("S/R calc error: %s", exc)
            return {
                "s1": 0, "r1": 0,
                "near_support": False, "near_resistance": False,
                "structure_type": "unknown",
            }

    # ── vectorised entry trigger (M5) ──────────────────────────

    @staticmethod
    def _vectorized_entry_trigger(ohlcv: list[dict], price: float) -> dict:
        """M5-level micro-structure detection — sweep & sniper entry.

        Returns ``{sweep_detected, sweep_type, sniper_entry, micro_trend, momentum, momentum_8}``.
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]
            df = pd.DataFrame(ohlcv)
            if len(df) < 20:
                return {
                    "sweep_detected": False, "sniper_entry": "NONE",
                    "micro_trend": "NEUTRAL", "momentum": 0,
                }

            close_vals = df["close"].astype(float).values
            high_vals = df["high"].astype(float).values
            low_vals = df["low"].astype(float).values

            # Recent 10-bar range
            recent_high = float(high_vals[-10:].max())
            recent_low = float(low_vals[-10:].min())

            # Sweep detection: price briefly broke recent swing then reversed
            sweep_high = (
                float(high_vals[-5:].max()) > recent_high * 1.001
                and close_vals[-1] < recent_high * 0.999
            )
            sweep_low = (
                float(low_vals[-5:].min()) < recent_low * 0.999
                and close_vals[-1] > recent_low * 1.001
            )

            # Momentum (rate of change)
            mom_3 = (
                (close_vals[-1] - close_vals[-3]) / close_vals[-3] * 100
                if len(close_vals) >= 3 and close_vals[-3] != 0 else 0.0
            )
            mom_8 = (
                (close_vals[-1] - close_vals[-8]) / close_vals[-8] * 100
                if len(close_vals) >= 8 and close_vals[-8] != 0 else 0.0
            )

            # Micro trend
            if mom_3 > 0.1 and mom_8 > 0.1:
                micro_trend = "BULLISH"
            elif mom_3 < -0.1 and mom_8 < -0.1:
                micro_trend = "BEARISH"
            else:
                micro_trend = "NEUTRAL"

            # Sniper entry signal
            sniper_entry = "NONE"
            if sweep_high and micro_trend == "BEARISH":
                sniper_entry = "SELL"
            elif sweep_low and micro_trend == "BULLISH":
                sniper_entry = "BUY"
            elif abs(mom_3) > 0.3:
                sniper_entry = "BUY" if mom_3 > 0 else "SELL"

            return {
                "sweep_detected": sweep_high or sweep_low,
                "sweep_type": (
                    "LIQUIDITY_HIGH" if sweep_high
                    else ("LIQUIDITY_LOW" if sweep_low else "NONE")
                ),
                "sniper_entry": sniper_entry,
                "micro_trend": micro_trend,
                "momentum": round(mom_3, 3),
                "momentum_8": round(mom_8, 3),
            }
        except Exception as exc:
            LOG.warning("entry-trigger calc error: %s", exc)
            return {
                "sweep_detected": False, "sniper_entry": "NONE",
                "micro_trend": "NEUTRAL", "momentum": 0,
            }

    # ── hierarchical verdict ────────────────────────────────────

    @staticmethod
    def _compute_hierarchical_verdict(tf_results: dict[str, dict]) -> dict:
        """Weighted hierarchical consensus across all timeframes.

        D1 & H4 = macro filter (weight 0.60)
        H1 & M15 = structure setup (weight 0.32)
        M5 = entry trigger (weight 0.08)

        Returns ``{verdict, consensus_score, weighted_buy, weighted_sell,
        mtf_alignment, counter_trend_flags, macro_trend}``.
        """
        weighted_buy = 0.0
        weighted_sell = 0.0
        total_weight = 0.0
        counter_trend_flags: list[str] = []
        macro_verdicts: list[str] = []

        # First pass: determine macro trend from D1 + H4
        for tf in ("D1", "H4"):
            if tf in tf_results:
                r = tf_results[tf]
                if r.get("verdict") in ("BUY", "SELL"):
                    macro_verdicts.append(r["verdict"])

        if not macro_verdicts:
            macro_trend = "NEUTRAL"
        elif all(v == "BUY" for v in macro_verdicts):
            macro_trend = "BULLISH"
        elif all(v == "SELL" for v in macro_verdicts):
            macro_trend = "BEARISH"
        else:
            # Mixed — check which side is stronger
            d1 = tf_results.get("D1", {})
            h4 = tf_results.get("H4", {})
            d1_bias = 1 if d1.get("buy_count", 0) > d1.get("sell_count", 0) else 0
            h4_bias = 1 if h4.get("buy_count", 0) > h4.get("sell_count", 0) else 0
            combined = d1_bias + h4_bias
            macro_trend = (
                "BULLISH" if combined > 0
                else ("BEARISH" if combined < 0 else "NEUTRAL")
            )

        # Second pass: weighted consensus with counter-trend detection
        alignment_count = 0
        total_tfs = 0

        for tf in TIMEFRAMES:
            if tf not in tf_results:
                continue
            r = tf_results[tf]
            weight = TF_WEIGHTS[tf]
            verdict = r.get("verdict", "HOLD")
            total_tfs += 1

            # Counter-trend: penalise 50 %
            if verdict == "BUY" and macro_trend == "BEARISH":
                counter_trend_flags.append(f"{tf} BUY counter-trend vs D1/H4 {macro_trend}")
                weight *= 0.5
            elif verdict == "SELL" and macro_trend == "BULLISH":
                counter_trend_flags.append(f"{tf} SELL counter-trend vs D1/H4 {macro_trend}")
                weight *= 0.5

            if verdict == "BUY":
                weighted_buy += weight
                if macro_trend == "BULLISH":
                    alignment_count += 1
            elif verdict == "SELL":
                weighted_sell += weight
                if macro_trend == "BEARISH":
                    alignment_count += 1

            total_weight += weight

        if total_weight == 0:
            return {
                "verdict": "HOLD",
                "consensus_score": 0.0,
                "mtf_alignment": "NONE",
                "counter_trend_flags": [],
                "macro_trend": macro_trend,
            }

        wb_norm = weighted_buy / total_weight
        ws_norm = weighted_sell / total_weight

        # Final verdict — need at least 35 % weighted consensus
        threshold = 0.35
        if wb_norm > threshold and wb_norm > ws_norm:
            verdict = "BUY"
            consensus_score = wb_norm
        elif ws_norm > threshold and ws_norm > wb_norm:
            verdict = "SELL"
            consensus_score = ws_norm
        else:
            verdict = "HOLD"
            consensus_score = max(wb_norm, ws_norm)

        # MTF alignment
        alignment_ratio = alignment_count / max(total_tfs, 1)
        if alignment_ratio >= 0.6:
            mtf_alignment = "ALIGNED"
        elif alignment_ratio >= 0.3:
            mtf_alignment = "MIXED"
        else:
            mtf_alignment = "CONFLICT"

        return {
            "verdict": verdict,
            "consensus_score": round(consensus_score, 4),
            "weighted_buy": round(wb_norm, 4),
            "weighted_sell": round(ws_norm, 4),
            "mtf_alignment": mtf_alignment,
            "counter_trend_flags": counter_trend_flags,
            "macro_trend": macro_trend,
        }

    # ── full MTF analysis ──────────────────────────────────────

    async def analyze(
        self, symbol: str = "XAUUSD", price: float | None = None,
    ) -> dict:
        """Run full MTF top-down analysis for *symbol*.

        Fetches data for all five timeframes, runs engines per TF,
        applies vectorised macro / structure / entry analysis, then
        computes the hierarchical verdict.

        Returns a dict with keys:
        ``symbol, price, timestamp, timeframes, macro_trends,
        structure_data, hierarchical, engines (M15 compat), verdict,
        consensus_pct, mtf_alignment, macro_trend, counter_trend_flags``.
        """
        mtf_data = await self.fetch_multi_tf(symbol)

        # Derive price from M15 close if not given
        if price is None:
            m15_bars = mtf_data.get("M15", [])
            if m15_bars:
                price = float(m15_bars[-1].get("close", 0))
            else:
                for tf in TIMEFRAMES:
                    bars = mtf_data.get(tf, [])
                    if bars:
                        price = float(bars[-1].get("close", 0))
                        break

        if price is None or price <= 0:
            LOG.warning("No price data available for %s", symbol)
            return {}

        # ── Per-timeframe analysis ──
        tf_results: dict[str, dict] = {}
        macro_trends: dict[str, dict] = {}
        structure_data: dict[str, dict] = {}

        for tf in TIMEFRAMES:
            bars = mtf_data.get(tf, [])
            if len(bars) < TF_ENGINE_MIN.get(tf, 30):
                LOG.warning("MTF %s %s: only %d bars, skipping", symbol, tf, len(bars))
                continue

            # Run all registered engines
            tf_result = await self._run_engines_on_tf(bars, symbol, tf)
            tf_results[tf] = tf_result

            # D1 & H4: macro trend analysis
            if tf in ("D1", "H4"):
                macro = self._vectorized_macro_trend(bars, price)
                macro_trends[tf] = macro
                tf_result["macro"] = macro
                tf_result["weight"] = TF_WEIGHTS[tf]

            # H1 & M15: structure / S/R levels
            if tf in ("H1", "M15"):
                struct = self._vectorized_snr_levels(bars, price)
                structure_data[tf] = struct
                tf_result["structure"] = struct
                tf_result["weight"] = TF_WEIGHTS[tf]

            # M5: entry trigger
            if tf == "M5":
                entry = self._vectorized_entry_trigger(bars, price)
                tf_result["entry"] = entry
                tf_result["weight"] = TF_WEIGHTS[tf]

            if "weight" not in tf_result:
                tf_result["weight"] = TF_WEIGHTS.get(tf, 0.0)

        # ── Hierarchical consensus ──
        hierarchical = self._compute_hierarchical_verdict(tf_results)
        verdict = hierarchical["verdict"]
        consensus_score = hierarchical["consensus_score"]

        # ── Build result ──
        result: dict[str, Any] = {
            "symbol": symbol,
            "price": round(price, 2),
            "timestamp": datetime.now(UTC).isoformat(),
            "timeframes": tf_results,
            "macro_trends": macro_trends,
            "structure_data": structure_data,
            "hierarchical": hierarchical,
            # Backwards-compat flat fields (from M15)
            "engines": tf_results.get("M15", {}).get("engines", {}),
            "buy_count": tf_results.get("M15", {}).get("buy_count", 0),
            "sell_count": tf_results.get("M15", {}).get("sell_count", 0),
            "total": tf_results.get("M15", {}).get("total", 0),
            "verdict": verdict,
            "consensus_pct": consensus_score,
            "mtf_alignment": hierarchical["mtf_alignment"],
            "macro_trend": hierarchical["macro_trend"],
            "counter_trend_flags": hierarchical["counter_trend_flags"],
        }

        self._last_result = result
        return result

    # ── Telegram formatting ─────────────────────────────────────

    @staticmethod
    def format_pulse_text(mtf_result: dict) -> str:
        """Generate a Telegram Market Pulse message from MTF result."""
        ts = mtf_result.get("timestamp", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"))
        price = mtf_result.get("price", 0)
        symbol = mtf_result.get("symbol", "XAUUSD")
        hier = mtf_result.get("hierarchical", {})
        macro_trend = hier.get("macro_trend", "NEUTRAL")
        mtf_alignment = hier.get("mtf_alignment", "MIXED")
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0)
        flags = hier.get("counter_trend_flags", [])

        # WIB timestamp
        try:
            wib = datetime.now(timezone(timedelta(hours=7))).strftime("%Y.%m.%d %H:%M")
        except Exception:
            wib = ts[:19]

        alignment_emoji = {"ALIGNED": "✅", "MIXED": "⚠️", "CONFLICT": "🔴", "NONE": "⚪️"}
        verdict_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️"}

        lines = [
            f"🔄 <b>MARKET PULSE — {symbol}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🕐 {wib} WIB ${price:.2f}",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        # Per-timeframe summary
        lines.append("<b>📊 MTF MATRIX</b>")
        for tf in TIMEFRAMES:
            if tf not in mtf_result.get("timeframes", {}):
                continue
            r = mtf_result["timeframes"][tf]
            tf_v = r.get("verdict", "HOLD")
            eng_icon = verdict_emoji.get(tf_v, "⚪️")
            buy_c = r.get("buy_count", 0)
            sell_c = r.get("sell_count", 0)
            total_c = r.get("total", 0)
            conf = r.get("consensus_pct", 0) * 100
            lines.append(
                f"{eng_icon} <b>{tf}</b> → {tf_v} ({conf:.0f}% | {buy_c}B/{sell_c}S/{total_c}T)"
            )

        macro_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🏛 <b>Macro: {macro_emoji.get(macro_trend, '⚪️')} {macro_trend}</b>",
            f"{alignment_emoji.get(mtf_alignment, '⚪️')} <b>MTF: {mtf_alignment}</b>",
            f"{verdict_emoji.get(verdict, '⚪️')} "
            f"<b>Hierarchical: {verdict}</b> ({score * 100:.0f}%)",
        ])

        if flags:
            for f in flags[:2]:
                lines.append(f"⚠️ {f}")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ <b>INI BUKAN SINYAL EKSEKUSI!</b>",
            "Market Pulse = engine status mentah (raw readings)",
            "Entry + TP/SL hanya muncul jika quality gate lolos → ACTIVE SIGNAL",
            "Jangan FOMO — tunggu konfirmasi resmi ya bro 💪",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot",
        ])
        return "\n".join(lines)

    async def get_pulse_text(self, symbol: str = "XAUUSD") -> str | None:
        """Convenience: run full MTF analysis and return formatted pulse text."""
        try:
            result = await self.analyze(symbol)
            if not result:
                return None
            return self.format_pulse_text(result)
        except Exception as exc:
            LOG.error("pulse-text error: %s", exc)
            return None
