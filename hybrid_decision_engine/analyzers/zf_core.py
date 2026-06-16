"""
ZF-Core Analyzer — Hybrid Decision Engine
==========================================
Z-Score based mean-reversion detector with 68/32 threshold system.

Algorithm:
  1. Compute Z-Score of closing prices over N-bar lookback
  2. Compute Z-Score of volume (anomaly detection)
  3. Combined ZF-Score = weighted(price_zscore, volume_zscore)
  4. Thresholds:
     - ZF > 0.68 (upper) → Overbought zone → SELL signal
     - ZF < 0.32 (lower) → Oversold zone → BUY signal
     - ZF > 0.90 / < 0.10 → Extreme → high confidence reversal
     - 0.32 ≤ ZF ≤ 0.68 → Neutral zone → HOLD

Reference: https://en.wikipedia.org/wiki/Standard_score
68-95-99.7 rule: 68% of data within ±1σ → 0.32-0.68 covers the middle band.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseAnalyzer, AnalysisResult
from .. import config

logger = logging.getLogger("hybrid.zf_core")


class ZFCoreAnalyzer(BaseAnalyzer):
    """Z-Score 68/32 mean-reversion analyzer."""

    name = "zf_core"

    def analyze(self, ohlcv: pd.DataFrame, symbol: str, **kwargs) -> AnalysisResult:
        if len(ohlcv) < config.ZF_LOOKBACK:
            return AnalysisResult(
                analyzer=self.name,
                action="HOLD",
                confidence=0.0,
                reasoning=f"Insufficient data: {len(ohlcv)} candles (need ≥{config.ZF_LOOKBACK})",
            )

        closes = ohlcv["close"].astype(float).values
        volumes = ohlcv["volume"].astype(float).values

        # ── Price Z-Score ──
        lookback = min(config.ZF_LOOKBACK, len(closes))
        price_window = closes[-lookback:]
        price_mean = np.mean(price_window)
        price_std = np.std(price_window, ddof=1) if lookback > 1 else 1.0
        if price_std < 1e-10:
            price_std = 1.0

        current_price = closes[-1]
        price_zscore = (current_price - price_mean) / price_std

        # Normalize to 0-1 range: Z=0 → 0.5, Z=±1 → 0.16/0.84, Z=±2 → ~0.025/0.975
        price_pct = 1 / (1 + np.exp(-price_zscore))  # sigmoid normalization

        # ── Volume Z-Score ──
        vol_lookback = min(config.ZF_VOLUME_LOOKBACK, len(volumes))
        vol_window = volumes[-vol_lookback:]
        vol_mean = np.mean(vol_window)
        vol_std = np.std(vol_window, ddof=1) if vol_lookback > 1 else 1.0
        if vol_std < 1e-10:
            vol_std = 1.0

        current_volume = volumes[-1]
        vol_zscore = (current_volume - vol_mean) / vol_std

        # Volume anomaly: high volume with no price move = potential spoofing
        vol_anomaly = abs(vol_zscore) > 2.0

        # ── Combined ZF-Score (70% price, 30% volume) ──
        zf_score = 0.70 * price_pct + 0.30 * (1 / (1 + np.exp(-vol_zscore)))

        # ── Direction detection ──
        # Check last 3 bars for momentum direction
        if len(closes) >= 3:
            recent_direction = closes[-1] - closes[-3]
        else:
            recent_direction = 0

        # ── Signal generation ──
        if zf_score > config.ZF_EXTREME_UPPER:
            # Extreme overbought
            action = "SELL"
            confidence = 0.85 + min((zf_score - config.ZF_EXTREME_UPPER) * 0.5, 0.10)
            reasoning = (
                f"EXTREME OVERBOUGHT: ZF={zf_score:.4f} > {config.ZF_EXTREME_UPPER}. "
                f"Price Z={price_zscore:+.2f}σ (pct={price_pct:.3f}). "
                f"Vol Z={vol_zscore:+.2f}σ. "
                f"Strong mean-reversion reversal expected."
            )
        elif zf_score > config.ZF_UPPER_THRESHOLD:
            # Overbought zone
            action = "SELL"
            confidence = 0.65 + (zf_score - config.ZF_UPPER_THRESHOLD) * 1.5
            confidence = min(confidence, 0.85)
            reasoning = (
                f"OVERBOUGHT ZONE: ZF={zf_score:.4f} (threshold={config.ZF_UPPER_THRESHOLD}). "
                f"Price Z={price_zscore:+.2f}σ. "
                f"{'Volume anomaly detected.' if vol_anomaly else 'Volume normal.'}"
            )
        elif zf_score < config.ZF_EXTREME_LOWER:
            # Extreme oversold
            action = "BUY"
            confidence = 0.85 + min((config.ZF_EXTREME_LOWER - zf_score) * 0.5, 0.10)
            reasoning = (
                f"EXTREME OVERSOLD: ZF={zf_score:.4f} < {config.ZF_EXTREME_LOWER}. "
                f"Price Z={price_zscore:+.2f}σ (pct={price_pct:.3f}). "
                f"Vol Z={vol_zscore:+.2f}σ. "
                f"Strong mean-reversion bounce expected."
            )
        elif zf_score < config.ZF_LOWER_THRESHOLD:
            # Oversold zone
            action = "BUY"
            confidence = 0.65 + (config.ZF_LOWER_THRESHOLD - zf_score) * 1.5
            confidence = min(confidence, 0.85)
            reasoning = (
                f"OVERSOLD ZONE: ZF={zf_score:.4f} (threshold={config.ZF_LOWER_THRESHOLD}). "
                f"Price Z={price_zscore:+.2f}σ. "
                f"{'Volume anomaly detected.' if vol_anomaly else 'Volume normal.'}"
            )
        else:
            # Neutral zone — no signal
            action = "HOLD"
            confidence = 0.3 + abs(zf_score - 0.5) * 0.4
            reasoning = (
                f"NEUTRAL ZONE: ZF={zf_score:.4f} (range={config.ZF_LOWER_THRESHOLD}-{config.ZF_UPPER_THRESHOLD}). "
                f"Price Z={price_zscore:+.2f}σ. No edge detected."
            )

        # ── SL / TP based on ATR ──
        highs = ohlcv["high"].astype(float).values
        lows = ohlcv["low"].values
        tr = np.maximum(
            highs[-20:] - lows[-20:],
            np.maximum(
                np.abs(highs[-20:] - np.roll(closes, 1)[-20:]),
                np.abs(lows[-20:] - np.roll(closes, 1)[-20:])
            )
        )
        atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))

        if action == "BUY":
            sl = round(current_price - atr * 1.5, 2)
            tp = round(current_price + atr * 2.0, 2)
        elif action == "SELL":
            sl = round(current_price + atr * 1.5, 2)
            tp = round(current_price - atr * 2.0, 2)
        else:
            sl = tp = None

        # ── Mean-reversion targets ──
        reversion_target = round(float(price_mean), 2)
        distance_to_mean = round(abs(current_price - reversion_target), 2)
        distance_pct = round(distance_to_mean / current_price * 100, 4)

        return AnalysisResult(
            analyzer=self.name,
            action=action,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            metadata={
                "zf_score": round(zf_score, 6),
                "price_zscore": round(price_zscore, 4),
                "price_pct": round(price_pct, 4),
                "volume_zscore": round(vol_zscore, 4),
                "volume_anomaly": vol_anomaly,
                "price_mean": round(price_mean, 2),
                "price_std": round(price_std, 4),
                "reversion_target": reversion_target,
                "distance_to_mean": distance_to_mean,
                "distance_pct": distance_pct,
                "current_price": round(current_price, 2),
                "atr": round(atr, 4),
                "sl": sl,
                "tp": tp,
                "lookback": lookback,
                "thresholds": {
                    "extreme_upper": config.ZF_EXTREME_UPPER,
                    "upper": config.ZF_UPPER_THRESHOLD,
                    "lower": config.ZF_LOWER_THRESHOLD,
                    "extreme_lower": config.ZF_EXTREME_LOWER,
                },
            },
        )
