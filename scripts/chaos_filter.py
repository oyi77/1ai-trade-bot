#!/usr/bin/env python3
"""
chaos_filter.py — Non-Linear Edge Module for Ultimate SMC Engine v3.0+
=====================================================================

Implements 3 non-linear filters to detect manipulation and regime shifts
that linear indicators (RSI, MACD, MA) miss:

  1. Shannon Entropy — Return distribution disorder
     High entropy = chaotic/unpredictable = low confidence zone

  2. Hurst Exponent (R/S approximation) — Fractal memory
     H > 0.5 = trending (momentum signals are reliable)
     H < 0.5 = mean-reverting (fade signals, range-bound)
     H ≈ 0.5 = random walk (no edge, skip)

  3. Volume Spoof Detection — Anomalous volume + rejection wicks
     Detects the "Batman Trap" pattern:
       - Volume spike 2.5x+ above SMA without direction follow-through
       - Long wick rejection (body/wick ratio < 0.4)
       - Price closes back inside previous candle range

These feed into chaos_gate() which outputs:
  - chaos_score (0-10, higher = more chaos = worse)
  - spoof_alert (bool — potential manipulation detected)
  - recommendation: "TRADE" | "CAUTION" | "SKIP"

Integration: import chaos_gate and call from ultimate_analyze().

Authoritative for: all symbols supported by ultimate_smc_engine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
import math
import numpy as np
from collections import deque


# ═══════════════════════════════════════════════════════════════════
# 1. SHANNON ENTROPY OF LOG RETURNS
# ═══════════════════════════════════════════════════════════════════

def _extract_closes(ohlcv: list[dict]) -> list[float]:
    """Extract close prices from OHLCV dict list. Robust to multiple key names."""
    return [float(b.get("close", b.get("c", 0))) for b in ohlcv]


def _extract_volumes(ohlcv: list[dict]) -> list[float]:
    """Extract volumes from OHLCV dict list."""
    return [float(b.get("volume", b.get("v", 0))) for b in ohlcv]


def calculate_shannon_entropy(ohlcv: list[dict], bins: int = 10) -> dict:
    """
    Shannon Entropy of log-return distribution.

    Theory:
      Pasar yang chaotic (high entropy) punya distribusi return yang
      flat — semua outcome equally likely. Ini = no edge zone.
      Low entropy = high predictability (baik trending atau ranging kuat).

    Returns:
      {"entropy": float (0-∞), "normalized": float (0-1), "interpretation": str}
    """
    if len(ohlcv) < 20:
        return {"entropy": 0.0, "normalized": 0.0, "interpretation": "data insufficient"}

    closes = _extract_closes(ohlcv)
    log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0 and closes[i] > 0]

    if len(log_returns) < bins:
        return {"entropy": 0.0, "normalized": 0.0, "interpretation": "data insufficient"}

    # Clip extreme outliers for stable histogram
    r_mean = np.mean(log_returns)
    r_std = np.std(log_returns)
    if r_std > 0:
        clipped = np.clip(log_returns, r_mean - 3 * r_std, r_mean + 3 * r_std)
    else:
        clipped = np.array(log_returns)

    # Histogram of returns (raw counts, not density)
    hist, _ = np.histogram(clipped, bins=bins)
    # Convert to probabilities
    total = hist.sum()
    if total == 0:
        return {"entropy": 0.0, "normalized": 0.0, "interpretation": "no variation"}
    probs = hist[hist > 0] / total

    # Shannon entropy: -Σ p(x) * log2(p(x))
    entropy = -np.sum(probs * np.log2(probs))

    # Normalize against maximum entropy (uniform distribution)
    max_entropy = math.log2(len(probs)) if len(probs) > 0 else 1.0
    normalized = entropy / max_entropy if max_entropy > 0 else 0.0

    # Interpretation
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


# ═══════════════════════════════════════════════════════════════════
# 2. HURST EXPONENT — R/S ANALYSIS APPROXIMATION
# ═══════════════════════════════════════════════════════════════════

def _rs_analysis(series: list[float], min_window: int = 8) -> float:
    """
    Simplified R/S (Rescaled Range) analysis for Hurst exponent.

    H = slope of log(R/S) vs log(window_size)

    H > 0.5: persistent / trending
    H < 0.5: anti-persistent / mean-reverting
    H ≈ 0.5: random walk (Brownian motion)
    """
    n = len(series)
    if n < 16:
        return 0.5  # neutral default

    # Window sizes: powers of 2 starting from min_window
    windows = []
    w = min_window
    while w <= n // 2:
        windows.append(w)
        w *= 2

    if len(windows) < 3:
        return 0.5

    rs_values = []
    log_windows = []

    for w in windows:
        # Number of sub-windows
        num_sub = n // w
        if num_sub < 2:
            continue

        rs_sum = 0.0
        valid = 0

        for i in range(num_sub):
            start = i * w
            end = start + w
            sub = series[start:end]

            mean = np.mean(sub)
            if mean == 0:
                continue

            # Deviations from mean
            dev = [s - mean for s in sub]
            # Cumulative deviations
            cum_dev = np.cumsum(dev)

            # Range
            R = max(cum_dev) - min(cum_dev)

            # Standard deviation
            S = np.std(sub, ddof=1)
            if S <= 0:
                continue

            rs_sum += R / S
            valid += 1

        if valid > 0:
            rs_values.append(math.log(rs_sum / valid))
            log_windows.append(math.log(w))

    if len(rs_values) < 3:
        return 0.5

    # Linear regression: slope = Hurst exponent
    log_w = np.array(log_windows)
    log_rs = np.array(rs_values)

    # OLS slope
    cov = np.cov(log_w, log_rs)
    if cov.shape != (2, 2):
        return 0.5
    slope = cov[0, 1] / cov[0, 0] if cov[0, 0] > 0 else 0.5

    # Clamp to reasonable range
    return max(0.1, min(1.0, slope))


def calculate_hurst_exponent(ohlcv: list[dict], lookback: int = 80) -> dict:
    """
    Hurst Exponent approximated via R/S analysis on log returns.

    Returns:
      {"hurst": float, "regime": str, "confidence": str}
    """
    if len(ohlcv) < 30:
        return {"hurst": 0.5, "regime": "insufficient data", "confidence": "low"}

    closes = _extract_closes(ohlcv[-lookback:])
    # Use log returns for stationarity
    log_returns = [math.log(closes[i] / closes[i-1])
                   for i in range(1, len(closes)) if closes[i-1] > 0 and closes[i] > 0]

    if len(log_returns) < 16:
        return {"hurst": 0.5, "regime": "insufficient data", "confidence": "low"}

    H = _rs_analysis(log_returns)

    # Regime classification
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

    return {
        "hurst": round(H, 3),
        "regime": regime,
        "confidence": confidence,
    }


# ═══════════════════════════════════════════════════════════════════
# 3. VOLUME SPOOF DETECTION — "Batman Trap"
# ═══════════════════════════════════════════════════════════════════

def detect_volume_spoof(ohlcv: list[dict], lookback: int = 30) -> dict:
    """
    Detect potential spoofing / liquidity trap patterns using OHLCV alone.

    Since we don't have Level 2 order book data (retail broker limitation),
    we use a proxy: anomalous volume + price behavior patterns.

    Patterns detected:
      a) Volume Spike + Rejection Wick
         — Large volume but price closes near open (no follow-through)
         — Indicates fake orders being pulled

      b) Consecutive Volume Anomaly + Tight Range
         — Multiple bars with high volume but tiny range
         — Indicates algo accumulation/distribution (smart money)

      c) Wick Breach without Body Close (Swept Level)
         — Price wicks through a level but body closes back
         — Classic stop-hunt pattern

    Returns:
      {"spoof_detected": bool, "confidence": float (0-1),
       "patterns": list[str], "recommendation": str}
    """
    if len(ohlcv) < lookback:
        return {"spoof_detected": False, "confidence": 0.0,
                "patterns": [], "recommendation": "insufficient data"}

    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    opens = [float(b.get("open", b.get("o", 0))) for b in bars]
    volumes = [float(b.get("volume", b.get("v", 0))) for b in bars]

    patterns = []
    confidence_scores = []

    # ── a) Volume Spike + Rejection Wick ──
    if volumes and sum(volumes) > 0:
        vol_mean = np.mean(volumes)
        vol_std = np.std(volumes)

        if vol_mean > 0:
            for i in range(max(1, len(bars) - 10), len(bars)):
                # Volume > 2.5 standard deviations above mean
                if vol_std > 0 and volumes[i] > vol_mean + 2.5 * vol_std:
                    # Check for rejection wick
                    body = abs(closes[i] - opens[i])
                    candle_range = highs[i] - lows[i]

                    if candle_range > 0:
                        body_ratio = body / candle_range

                        # Long upper wick (bearish rejection)
                        upper_wick = highs[i] - max(opens[i], closes[i])
                        upper_wick_ratio = upper_wick / candle_range

                        # Long lower wick (bullish rejection)
                        lower_wick = min(opens[i], closes[i]) - lows[i]
                        lower_wick_ratio = lower_wick / candle_range

                        # Volume spike + small body = no follow-through = fake
                        if body_ratio < 0.35:
                            patterns.append(
                                f"batman_trap_vol_spike_bar{i}: vol={volumes[i]:.0f} "
                                f"(+{(volumes[i]/vol_mean - 1)*100:.0f}% above mean), "
                                f"body_ratio={body_ratio:.2f}"
                            )
                            confidence_scores.append(min(0.75, body_ratio * 2.0 + 0.3))

                        # Volume spike + long wick = stop hunt
                        if upper_wick_ratio > 0.5:
                            patterns.append(
                                f"bearish_stop_hunt_bar{i}: vol spike + "
                                f"upper wick {upper_wick_ratio:.0%}"
                            )
                            confidence_scores.append(0.65)

                        if lower_wick_ratio > 0.5:
                            patterns.append(
                                f"bullish_stop_hunt_bar{i}: vol spike + "
                                f"lower wick {lower_wick_ratio:.0%}"
                            )
                            confidence_scores.append(0.65)

    # ── b) Consecutive Volume Anomaly + Tight Range ──
    if len(volumes) >= 5 and sum(volumes[-10:]) > 0:
        vol_mean_10 = np.mean(volumes[-10:])
        if vol_mean_10 > 0:
            anomaly_count = 0
            for i in range(len(bars) - 5, len(bars)):
                candle_range = highs[i] - lows[i]
                avg_range = np.mean([highs[j] - lows[j] for j in range(max(0, i-10), i)])

                if avg_range > 0:
                    range_ratio = candle_range / avg_range
                    vol_ratio = volumes[i] / vol_mean_10

                    # High volume + compressed range = algo accumulation
                    if vol_ratio > 1.5 and range_ratio < 0.5:
                        anomaly_count += 1

            if anomaly_count >= 2:
                patterns.append(
                    f"algo_accumulation: {anomaly_count} bars with high vol + tight range"
                )
                confidence_scores.append(0.55)

    # ── c) Wick Breach without Body Close ──
    for i in range(max(1, len(bars) - 5), len(bars)):
        candle_range = highs[i] - lows[i]
        if candle_range <= 0:
            continue

        # Upper wick breaches recent high but body closes below
        upper_wick = highs[i] - max(opens[i], closes[i])
        if upper_wick > candle_range * 0.4:
            # Check if wick went above recent swing high
            recent_high = max(highs[max(0, i-10):i]) if i > 0 else highs[i]
            if highs[i] > recent_high * 1.001:
                patterns.append(
                    f"swept_high_bar{i}: wick={highs[i]:.2f} > "
                    f"recent_high={recent_high:.2f}, body closed at {closes[i]:.2f}"
                )
                confidence_scores.append(0.60)

        # Lower wick breaches recent low but body closes above
        lower_wick = min(opens[i], closes[i]) - lows[i]
        if lower_wick > candle_range * 0.4:
            recent_low = min(lows[max(0, i-10):i]) if i > 0 else lows[i]
            if lows[i] < recent_low * 0.999:
                patterns.append(
                    f"swept_low_bar{i}: wick={lows[i]:.2f} < "
                    f"recent_low={recent_low:.2f}, body closed at {closes[i]:.2f}"
                )
                confidence_scores.append(0.60)

    # ── Aggregate ──
    spoof_detected = len(patterns) > 0
    avg_confidence = np.mean(confidence_scores) if confidence_scores else 0.0

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


# ═══════════════════════════════════════════════════════════════════
# 4. AGGREGATE GATE — Chaos Score + Signal Gate
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ChaosGateResult:
    """Output of chaos_gate() — determines if a signal should proceed."""
    chaos_score: int = 0          # 0-10, higher = worse
    entropy: dict = field(default_factory=dict)
    hurst: dict = field(default_factory=dict)
    spoof: dict = field(default_factory=dict)
    recommendation: str = "TRADE"  # TRADE | CAUTION | SKIP
    penalty: int = 0              # points to subtract from ultimate score (0-6)
    reasons: list[str] = field(default_factory=list)


def chaos_gate(ohlcv: list[dict],
               entropy_weight: float = 0.33,
               hurst_weight: float = 0.33,
               spoof_weight: float = 0.34) -> ChaosGateResult:
    """
    Main entry point — run all three non-linear filters and produce
    a chaos score that gates the ultimate signal.

    Args:
      ohlcv: list of OHLCV bar dicts (keys: open/high/low/close/volume or o/h/l/c/v)
      entropy_weight, hurst_weight, spoof_weight: relative weights

    Returns:
      ChaosGateResult with:
        - chaos_score (0-10): 0 = perfect, 10 = total chaos
        - recommendation: TRADE / CAUTION / SKIP
        - penalty: points to subtract from ultimate score (applied by caller)
        - reasons: human-readable explanations
    """
    result = ChaosGateResult()

    # ── 1. Entropy Check ──
    entropy = calculate_shannon_entropy(ohlcv)
    result.entropy = entropy

    ent_norm = entropy.get("normalized", 0.5)
    if ent_norm >= 0.85:
        result.chaos_score += 4
        result.reasons.append(f"⚡ Entropy sangat tinggi ({ent_norm:.2f}): pasar kacau, skip")
    elif ent_norm >= 0.70:
        result.chaos_score += 2
        result.reasons.append(f"⚠️ Entropy tinggi ({ent_norm:.2f}): pasar kurang terarah")
    elif ent_norm >= 0.45:
        result.chaos_score += 1
        # okay, minor

    # ── 2. Hurst Regime ──
    hurst = calculate_hurst_exponent(ohlcv)
    result.hurst = hurst

    H = hurst.get("hurst", 0.5)
    if 0.45 <= H <= 0.55:
        result.chaos_score += 3
        result.reasons.append(f"🎲 Hurst={H:.2f}: random walk — tidak ada edge statistik")
    elif H < 0.30:
        result.chaos_score += 2
        result.reasons.append(f"🔄 Hurst={H:.2f}: strongly mean-reverting — hindari breakout")
    elif H > 0.70:
        # Strong trend is good
        pass
    elif H < 0.45:
        result.chaos_score += 1
        # mild mean-reversion, acceptable

    # ── 3. Spoof Detection ──
    spoof = detect_volume_spoof(ohlcv)
    result.spoof = spoof

    if spoof.get("spoof_detected"):
        conf = spoof.get("confidence", 0)
        if conf >= 0.65:
            result.chaos_score += 4
            result.reasons.append(f"🦇 Spoof terdeteksi (confidence={conf:.2f}): jebakan Batman!")
        elif conf >= 0.40:
            result.chaos_score += 2
            result.reasons.append(f"👀 Potensi spoof (confidence={conf:.2f}): hati-hati")

    # Add spoof patterns to reasons
    for pattern in spoof.get("patterns", [])[:3]:  # max 3
        result.reasons.append(f"   └ {pattern}")

    # ── Aggregate ──
    # Normalize to 0-10 scale
    result.chaos_score = min(result.chaos_score, 10)

    # Recommendation
    if result.chaos_score >= 7:
        result.recommendation = "SKIP"
        result.penalty = 6  # heavy penalty
    elif result.chaos_score >= 4:
        result.recommendation = "CAUTION"
        result.penalty = 3
    else:
        result.recommendation = "TRADE"
        result.penalty = 0

    # Add summary
    result.reasons.insert(0,
        f"🔬 Chaos Gate: score={result.chaos_score}/10 → {result.recommendation}"
    )

    return result


# ═══════════════════════════════════════════════════════════════════
# 5. QUICK TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Generate synthetic OHLCV for smoke test
    np.random.seed(42)
    n = 120
    base = 2650.0

    # Scenario A: trending market (should pass)
    trend_up = base + np.cumsum(np.random.randn(n) * 2 + 0.3)
    ohlcv_trend = []
    for i in range(n):
        c = trend_up[i]
        o = c + np.random.randn() * 1
        h = max(o, c) + abs(np.random.randn()) * 3
        l = min(o, c) - abs(np.random.randn()) * 3
        v = abs(np.random.randn() * 1000 + 500)
        ohlcv_trend.append({"open": o, "high": h, "low": l, "close": c, "volume": v})

    # Scenario B: chaotic (should be gated)
    chaotic = base + np.cumsum(np.random.randn(n) * 5)
    ohlcv_chaos = []
    for i in range(n):
        c = chaotic[i]
        o = c + np.random.randn() * 8
        h = max(o, c) + abs(np.random.randn()) * 15
        l = min(o, c) - abs(np.random.randn()) * 15
        v = abs(np.random.randn() * 3000)
        ohlcv_chaos.append({"open": o, "high": h, "low": l, "close": c, "volume": v})

    print("=" * 60)
    print("CHAOS FILTER — Smoke Test")
    print("=" * 60)

    print("\n📈 TRENDING MARKET:")
    gate_trend = chaos_gate(ohlcv_trend)
    print(f"  Entropy: {gate_trend.entropy}")
    print(f"  Hurst:   {gate_trend.hurst}")
    print(f"  Spoof:   {gate_trend.spoof.get('spoof_detected')} (conf={gate_trend.spoof.get('confidence'):.3f})")
    print(f"  → Score: {gate_trend.chaos_score}/10, {gate_trend.recommendation}, penalty={gate_trend.penalty}")
    for r in gate_trend.reasons:
        print(f"    {r}")

    print("\n🌪️ CHAOTIC MARKET:")
    gate_chaos = chaos_gate(ohlcv_chaos)
    print(f"  Entropy: {gate_chaos.entropy}")
    print(f"  Hurst:   {gate_chaos.hurst}")
    print(f"  Spoof:   {gate_chaos.spoof.get('spoof_detected')} (conf={gate_chaos.spoof.get('confidence'):.3f})")
    print(f"  → Score: {gate_chaos.chaos_score}/10, {gate_chaos.recommendation}, penalty={gate_chaos.penalty}")
    for r in gate_chaos.reasons:
        print(f"    {r}")

    print("\n✅ Smoke test complete.")
