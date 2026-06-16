"""
Market Integrity Analyzer — Hybrid Decision Engine
===================================================
Detects market manipulation and unsafe conditions:

1. Liquidity Voids: Huge wicks/gaps without follow-through
   - Detects bars where wick > 3x body (potential fakeout)
   - Flags rapid price retracement within N bars

2. Spoofing Detection: Abnormal volume spikes without price movement
   - High volume + tiny body = potential order book manipulation
   - Volume Z-score > 2.5 + body < 20% of range = spoofing alert

3. Risk Assessment: Combined risk score from all integrity checks
   - Risk > 0.7 → BLOCK signal (too dangerous)
   - Risk 0.4-0.7 → WARN (reduced confidence)
   - Risk < 0.4 → OK (safe to trade)
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseAnalyzer, AnalysisResult

logger = logging.getLogger("hybrid.integrity")


class MarketIntegrityAnalyzer(BaseAnalyzer):
    """Market integrity checker — liquidity voids, spoofing, risk assessment."""

    name = "integrity"

    # Thresholds
    WICK_RATIO_THRESHOLD = 3.0      # wick > 3x body = suspicious
    BODY_RANGE_THRESHOLD = 0.20     # body < 20% of range = potential spoof
    VOLUME_Z_THRESHOLD = 2.5        # volume Z > 2.5 = anomaly
    SPIKE_RETRACE_BARS = 5          # retrace within 5 bars = void
    SPIKE_RETRACE_PCT = 0.50        # retrace > 50% of spike = void

    def analyze(self, ohlcv: pd.DataFrame, symbol: str, **kwargs) -> AnalysisResult:
        if len(ohlcv) < 20:
            return AnalysisResult(
                analyzer=self.name,
                action="HOLD",
                confidence=0.0,
                reasoning=f"Insufficient data: {len(ohlcv)} candles (need ≥20)",
            )

        opens = ohlcv["open"].astype(float).values
        highs = ohlcv["high"].astype(float).values
        lows = ohlcv["low"].astype(float).values
        closes = ohlcv["close"].astype(float).values
        volumes = ohlcv["volume"].astype(float).values

        issues = []
        risk_score = 0.0

        # ── 1. Liquidity Void Detection ──
        void_count = 0
        for i in range(-min(20, len(ohlcv)), 0):
            bar_range = highs[i] - lows[i]
            if bar_range < 1e-10:
                continue

            body = abs(closes[i] - opens[i])
            upper_wick = highs[i] - max(opens[i], closes[i])
            lower_wick = min(opens[i], closes[i]) - lows[i]
            max_wick = max(upper_wick, lower_wick)

            # Wick > 3x body = suspicious wick (fakeout/void)
            if body > 0 and max_wick / body > self.WICK_RATIO_THRESHOLD:
                void_count += 1
                if void_count <= 3:  # only report first 3
                    issues.append(
                        f"Liquidity void bar[{i}]: wick/body={max_wick/body:.1f}x "
                        f"(range={bar_range:.2f}, body={body:.2f})"
                    )

        # Check for spike-and-retrace pattern (void fill)
        retrace_count = 0
        for i in range(-min(15, len(ohlcv)), -2):
            spike = abs(closes[i] - opens[i])
            if spike < 1e-10:
                continue
            # Check if price retraced within next N bars
            for j in range(1, min(self.SPIKE_RETRACE_BARS, len(ohlcv) + i)):
                if i + j >= 0:
                    break
                retrace = abs(closes[i + j] - closes[i])
                if retrace > spike * self.SPIKE_RETRACE_PCT:
                    retrace_count += 1
                    break

        void_risk = min((void_count * 0.05) + (retrace_count * 0.08), 0.40)
        risk_score += void_risk

        if void_count > 0:
            issues.insert(0, f"Liquidity voids detected: {void_count} in last 20 bars, retraces: {retrace_count}")

        # ── 2. Spoofing Detection ──
        vol_mean = np.mean(volumes[-20:])
        vol_std = np.std(volumes[-20:], ddof=1)
        if vol_std < 1e-10:
            vol_std = 1.0

        spoof_count = 0
        for i in range(-min(20, len(ohlcv)), 0):
            vol_z = (volumes[i] - vol_mean) / vol_std
            bar_range = highs[i] - lows[i]
            if bar_range < 1e-10:
                continue

            body = abs(closes[i] - opens[i])
            body_ratio = body / bar_range

            # High volume + tiny body = potential spoof
            if vol_z > self.VOLUME_Z_THRESHOLD and body_ratio < self.BODY_RANGE_THRESHOLD:
                spoof_count += 1
                if spoof_count <= 3:
                    issues.append(
                        f"Spoofing bar[{i}]: vol_z={vol_z:.2f}, body_ratio={body_ratio:.2%}"
                    )

        spoof_risk = min(spoof_count * 0.08, 0.30)
        risk_score += spoof_risk

        if spoof_count > 0:
            issues.insert(0, f"Spoofing detected: {spoof_count} suspicious volume bars")

        # ── 3. Spread/Gap Analysis ──
        gaps = []
        for i in range(-min(20, len(ohlcv)) + 1, 0):
            gap = abs(opens[i] - closes[i - 1])
            avg_range = np.mean(highs[i-5:i] - lows[i-5:i]) if i >= 5 else 1.0
            if avg_range < 1e-10:
                avg_range = 1.0
            gap_ratio = gap / avg_range
            if gap_ratio > 1.5:
                gaps.append(round(gap_ratio, 2))

        gap_risk = min(len(gaps) * 0.05, 0.20)
        risk_score += gap_risk

        if gaps:
            issues.insert(0, f"Price gaps detected: {len(gaps)} (max ratio: {max(gaps)}x)")

        # ── 4. Volume Dry-Up Detection ──
        recent_vol = np.mean(volumes[-5:])
        baseline_vol = np.mean(volumes[-20:])
        if baseline_vol > 0:
            vol_ratio = recent_vol / baseline_vol
            if vol_ratio < 0.3:
                risk_score += 0.10
                issues.append(f"Volume dry-up: recent/baseline={vol_ratio:.2f}x (low liquidity)")

        # ── Final Risk Assessment ──
        risk_score = min(risk_score, 1.0)

        if risk_score > 0.7:
            action = "BLOCK"
            confidence = risk_score
            reasoning = f"⛔ HIGH RISK ({risk_score:.2f}): {' | '.join(issues)}"
        elif risk_score > 0.4:
            action = "WARN"
            confidence = 0.5
            reasoning = f"⚠️ MODERATE RISK ({risk_score:.2f}): {' | '.join(issues)}"
        else:
            action = "OK"
            confidence = 1.0 - risk_score
            if not issues:
                reasoning = f"✅ LOW RISK ({risk_score:.2f}): Market integrity acceptable"
            else:
                reasoning = f"✅ LOW RISK ({risk_score:.2f}): {' | '.join(issues)}"

        return AnalysisResult(
            analyzer=self.name,
            action=action,
            confidence=round(confidence, 4),
            blocked=risk_score > 0.7,
            block_reason=f"Market integrity risk too high: {risk_score:.2f}" if risk_score > 0.7 else "",
            reasoning=reasoning,
            metadata={
                "risk_score": round(risk_score, 4),
                "void_count": void_count,
                "retrace_count": retrace_count,
                "spoof_count": spoof_count,
                "gaps": gaps,
                "volume_ratio": round(recent_vol / baseline_vol, 4) if baseline_vol > 0 else 0,
                "issues": issues,
            },
        )
