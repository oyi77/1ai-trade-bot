"""
chaos.py — Chaos / Fractal Filter Engine

Migrated from: scripts/chaos_filter.py
Conforms to: tradebot.engines.base.Engine interface

Implements 3 non-linear filters:
  1. Shannon Entropy — return distribution disorder
  2. Hurst Exponent (R/S approximation) — fractal memory
  3. Volume Spoof Detection — anomalous volume + rejection wicks
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


@dataclass
class ChaosGateResult:
    """Output of chaos_gate() — determines if a signal should proceed."""
    chaos_score: int = 0
    entropy: dict = field(default_factory=dict)
    hurst: dict = field(default_factory=dict)
    spoof: dict = field(default_factory=dict)
    recommendation: str = "TRADE"
    penalty: int = 0
    reasons: list[str] = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────


def _extract_closes(ohlcv: list[dict]) -> list[float]:
    return [float(b.get("close", b.get("c", 0))) for b in ohlcv]


def _extract_volumes(ohlcv: list[dict]) -> list[float]:
    return [float(b.get("volume", b.get("v", 0))) for b in ohlcv]


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    """Convert ticks to OHLCV-like bar dicts."""
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price, "high": t.price,
            "low": t.price, "close": t.price,
            "volume": 1,
        })
    return bars


# ── 1. Shannon Entropy ──────────────────────────────────────────────


def calculate_shannon_entropy(ohlcv: list[dict], bins: int = 10) -> dict:
    """Shannon Entropy of log-return distribution."""
    if len(ohlcv) < 20:
        return {"entropy": 0.0, "normalized": 0.0, "interpretation": "data insufficient"}

    closes = _extract_closes(ohlcv)
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]

    if len(log_returns) < bins:
        return {"entropy": 0.0, "normalized": 0.0, "interpretation": "data insufficient"}

    r_mean = float(np.mean(log_returns))
    r_std = float(np.std(log_returns))
    if r_std > 0:
        clipped = np.clip(log_returns, r_mean - 3 * r_std, r_mean + 3 * r_std)
    else:
        clipped = np.array(log_returns)

    hist, _ = np.histogram(clipped, bins=bins)
    total = hist.sum()
    if total == 0:
        return {"entropy": 0.0, "normalized": 0.0, "interpretation": "no variation"}
    probs = hist[hist > 0] / total

    entropy = -float(np.sum(probs * np.log2(probs)))
    max_entropy = math.log2(len(probs)) if len(probs) > 0 else 1.0
    normalized = entropy / max_entropy if max_entropy > 0 else 0.0

    if normalized < 0.45:
        interp = "low entropy — high predictability, edge exists"
    elif normalized < 0.70:
        interp = "moderate entropy — acceptable risk"
    elif normalized < 0.85:
        interp = "high entropy — approaching chaos, reduce size"
    else:
        interp = "very high entropy — chaotic market, skip"

    return {
        "entropy": round(entropy, 4),
        "normalized": round(normalized, 4),
        "interpretation": interp,
    }


# ── 2. Hurst Exponent ─────────────────────────────────────────────


def _rs_analysis(series: list[float], min_window: int = 8) -> float:
    """Simplified R/S (Rescaled Range) analysis for Hurst exponent."""
    n = len(series)
    if n < 16:
        return 0.5

    windows: list[int] = []
    w = min_window
    while w <= n // 2:
        windows.append(w)
        w *= 2

    if len(windows) < 3:
        return 0.5

    rs_values: list[float] = []
    log_windows: list[float] = []

    for w in windows:
        num_sub = n // w
        if num_sub < 2:
            continue
        rs_sum = 0.0
        valid = 0
        for i in range(num_sub):
            start = i * w
            end = start + w
            sub = series[start:end]
            mean = float(np.mean(sub))
            if mean == 0:
                continue
            dev = [s - mean for s in sub]
            cum_dev = np.cumsum(dev)
            R = float(max(cum_dev) - min(cum_dev))  # noqa: N806
            S = float(np.std(sub, ddof=1))  # noqa: N806
            if S <= 0:
                continue
            rs_sum += R / S
            valid += 1
        if valid > 0:
            rs_values.append(math.log(rs_sum / valid))
            log_windows.append(math.log(w))

    if len(rs_values) < 3:
        return 0.5

    log_w = np.array(log_windows)
    log_rs = np.array(rs_values)
    cov = np.cov(log_w, log_rs)
    if cov.shape != (2, 2):
        return 0.5
    slope = cov[0, 1] / cov[0, 0] if cov[0, 0] > 0 else 0.5
    return max(0.1, min(1.0, slope))


def calculate_hurst_exponent(ohlcv: list[dict], lookback: int = 80) -> dict:
    """Hurst Exponent approximated via R/S analysis on log returns."""
    if len(ohlcv) < 30:
        return {"hurst": 0.5, "regime": "insufficient data", "confidence": "low"}

    closes = _extract_closes(ohlcv[-lookback:])
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]

    if len(log_returns) < 16:
        return {"hurst": 0.5, "regime": "insufficient data", "confidence": "low"}

    H = _rs_analysis(log_returns)  # noqa: N806

    if H > 0.60:
        regime = "trending — momentum signals reliable"
        confidence = "high"
    elif H > 0.55:
        regime = "mildly trending — signals usable"
        confidence = "medium"
    elif H > 0.45:
        regime = "random walk — no clear edge"
        confidence = "low"
    elif H > 0.35:
        regime = "mildly mean-reverting — fade extremes"
        confidence = "medium"
    else:
        regime = "strongly mean-reverting — range-bound, avoid breakouts"
        confidence = "high"

    return {"hurst": round(H, 3), "regime": regime, "confidence": confidence}


# ── 3. Volume Spoof Detection ─────────────────────────────────────


def detect_volume_spoof(ohlcv: list[dict], lookback: int = 30) -> dict:
    """Detect potential spoofing / liquidity trap patterns."""
    if len(ohlcv) < lookback:
        return {"spoof_detected": False, "confidence": 0.0,
                "patterns": [], "recommendation": "insufficient data"}

    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    opens = [float(b.get("open", b.get("o", 0))) for b in bars]
    volumes = [float(b.get("volume", b.get("v", 0))) for b in bars]

    patterns: list[str] = []
    confidence_scores: list[float] = []

    # a) Volume Spike + Rejection Wick
    if volumes and sum(volumes) > 0:
        vol_mean = float(np.mean(volumes))
        vol_std = float(np.std(volumes))
        if vol_mean > 0:
            for i in range(max(1, len(bars) - 10), len(bars)):
                if vol_std > 0 and volumes[i] > vol_mean + 2.5 * vol_std:
                    body = abs(closes[i] - opens[i])
                    candle_range = highs[i] - lows[i]
                    if candle_range > 0:
                        body_ratio = body / candle_range
                        upper_wick = highs[i] - max(opens[i], closes[i])
                        upper_wick_ratio = upper_wick / candle_range
                        lower_wick = min(opens[i], closes[i]) - lows[i]
                        lower_wick_ratio = lower_wick / candle_range
                        if body_ratio < 0.35:
                            patterns.append(
                                f"batman_trap_vol_spike_bar{i}: vol={volumes[i]:.0f} "
                                f"(+{(volumes[i]/vol_mean - 1)*100:.0f}% above mean), "
                                f"body_ratio={body_ratio:.2f}"
                            )
                            confidence_scores.append(min(0.75, body_ratio * 2.0 + 0.3))
                        if upper_wick_ratio > 0.5:
                            patterns.append(
                                f"bearish_stop_hunt_bar{i}: vol spike + upper wick {upper_wick_ratio:.0%}"  # noqa: E501
                            )
                            confidence_scores.append(0.65)
                        if lower_wick_ratio > 0.5:
                            patterns.append(
                                f"bullish_stop_hunt_bar{i}: vol spike + lower wick {lower_wick_ratio:.0%}"  # noqa: E501
                            )
                            confidence_scores.append(0.65)

    # b) Consecutive Volume Anomaly + Tight Range
    if len(volumes) >= 5 and sum(volumes[-10:]) > 0:
        vol_mean_10 = float(np.mean(volumes[-10:]))
        if vol_mean_10 > 0:
            anomaly_count = 0
            for i in range(len(bars) - 5, len(bars)):
                candle_range = highs[i] - lows[i]
                avg_range = float(np.mean([highs[j] - lows[j] for j in range(max(0, i - 10), i)]))
                if avg_range > 0:
                    range_ratio = candle_range / avg_range
                    vol_ratio = volumes[i] / vol_mean_10
                    if vol_ratio > 1.5 and range_ratio < 0.5:
                        anomaly_count += 1
            if anomaly_count >= 2:
                patterns.append(
                    f"algo_accumulation: {anomaly_count} bars with high vol + tight range"
                )
                confidence_scores.append(0.55)

    # c) Wick Breach without Body Close
    for i in range(max(1, len(bars) - 5), len(bars)):
        candle_range = highs[i] - lows[i]
        if candle_range <= 0:
            continue
        upper_wick = highs[i] - max(opens[i], closes[i])
        if upper_wick > candle_range * 0.4:
            recent_high = max(highs[max(0, i - 10): i]) if i > 0 else highs[i]
            if highs[i] > recent_high * 1.001:
                patterns.append(
                    f"swept_high_bar{i}: wick={highs[i]:.2f} > recent_high={recent_high:.2f}, "
                    f"body closed at {closes[i]:.2f}"
                )
                confidence_scores.append(0.60)
        lower_wick = min(opens[i], closes[i]) - lows[i]
        if lower_wick > candle_range * 0.4:
            recent_low = min(lows[max(0, i - 10): i]) if i > 0 else lows[i]
            if lows[i] < recent_low * 0.999:
                patterns.append(
                    f"swept_low_bar{i}: wick={lows[i]:.2f} < recent_low={recent_low:.2f}, "
                    f"body closed at {closes[i]:.2f}"
                )
                confidence_scores.append(0.60)

    spoof_detected = len(patterns) > 0
    avg_confidence = float(np.mean(confidence_scores)) if confidence_scores else 0.0

    if avg_confidence >= 0.65:
        recommendation = "SKIP — likely manipulation zone"
    elif avg_confidence >= 0.40:
        recommendation = "CAUTION — reduce position size, widen SL"
    else:
        recommendation = "TRADE — no obvious spoof patterns"

    return {
        "spoof_detected": spoof_detected,
        "confidence": round(avg_confidence, 3),
        "patterns": patterns,
        "recommendation": recommendation,
    }


# ── 4. Aggregate Gate ──────────────────────────────────────────────


def chaos_gate(
    ohlcv: list[dict],
    entropy_weight: float = 0.33,
    hurst_weight: float = 0.33,
    spoof_weight: float = 0.34,
) -> ChaosGateResult:
    """Run all three non-linear filters and produce a chaos score that gates signals."""
    result = ChaosGateResult()

    # Entropy check
    entropy = calculate_shannon_entropy(ohlcv)
    result.entropy = entropy
    ent_norm = entropy.get("normalized", 0.5)
    if ent_norm >= 0.85:
        result.chaos_score += 4
        result.reasons.append(f"Entropy sangat tinggi ({ent_norm:.2f}): pasar kacau, skip")
    elif ent_norm >= 0.70:
        result.chaos_score += 2
        result.reasons.append(f"Entropy tinggi ({ent_norm:.2f}): pasar kurang terarah")
    elif ent_norm >= 0.45:
        result.chaos_score += 1

    # Hurst regime
    hurst = calculate_hurst_exponent(ohlcv)
    result.hurst = hurst
    H = hurst.get("hurst", 0.5)  # noqa: N806
    if 0.45 <= H <= 0.55:
        result.chaos_score += 3
        result.reasons.append(f"Hurst={H:.2f}: random walk — tidak ada edge statistik")
    elif H < 0.30:
        result.chaos_score += 2
        result.reasons.append(f"Hurst={H:.2f}: strongly mean-reverting — hindari breakout")
    elif H < 0.45:
        result.chaos_score += 1

    # Spoof detection
    spoof = detect_volume_spoof(ohlcv)
    result.spoof = spoof
    if spoof.get("spoof_detected"):
        conf = spoof.get("confidence", 0)
        if conf >= 0.65:
            result.chaos_score += 4
            result.reasons.append(f"Spoof terdeteksi (confidence={conf:.2f}): jebakan Batman!")
        elif conf >= 0.40:
            result.chaos_score += 2
            result.reasons.append(f"Potensi spoof (confidence={conf:.2f}): hati-hati")
    for pattern in spoof.get("patterns", [])[:3]:
        result.reasons.append(f"   └ {pattern}")

    result.chaos_score = min(result.chaos_score, 10)

    if result.chaos_score >= 7:
        result.recommendation = "SKIP"
        result.penalty = 6
    elif result.chaos_score >= 4:
        result.recommendation = "CAUTION"
        result.penalty = 3
    else:
        result.recommendation = "TRADE"
        result.penalty = 0

    result.reasons.insert(
        0, f"Chaos Gate: score={result.chaos_score}/10 → {result.recommendation}"
    )
    return result


# ── Engine ─────────────────────────────────────────────────────────


class ChaosEngine(Engine):
    """Chaos / Fractal Filter Engine.

    Determines if current market conditions are tradeable by measuring
    entropy, fractal memory (Hurst exponent), and spoof patterns.
    """

    def __init__(self) -> None:
        self._max_chaos_score: int = int(getattr(settings, "CHAOS_MAX_SCORE", 6))

    @property
    def name(self) -> str:
        return "chaos_filter"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for market chaos level and return a gating signal."""
        if not ticks or len(ticks) < 20:
            LOG.debug("Chaos: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            gate = chaos_gate(ohlcv)

            if gate.recommendation == "SKIP":
                LOG.info("Chaos gate: SKIP (score=%d)", gate.chaos_score)
                return None

            current_price = ticks[-1].price
            conf_pct = 1.0 - (gate.chaos_score / 10.0)

            return Signal(
                symbol="XAUUSD",
                direction="CALL",  # Chaos filter doesn't dictate direction
                predicted_digit=int(current_price * 10) % 10,
                confidence=conf_pct,
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if gate.recommendation == "TRADE" else SignalGrade.MODERATE,  # noqa: E501
                metadata={
                    "engine": self.name,
                    "chaos_score": gate.chaos_score,
                    "recommendation": gate.recommendation,
                    "penalty": gate.penalty,
                    "entropy": gate.entropy,
                    "hurst": gate.hurst,
                    "spoof": {
                        "detected": gate.spoof.get("spoof_detected"),
                        "confidence": gate.spoof.get("confidence"),
                    },
                    "reasons": gate.reasons,
                },
            )
        except Exception as exc:
            LOG.warning("Chaos engine error: %s", exc)
            raise SignalError("Chaos analysis failed", details={"error": str(exc)}) from exc
