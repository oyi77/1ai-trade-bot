"""
LSTM Analyzer — Hybrid Decision Engine
=======================================
Structured placeholder for ONNX LSTM model inference.

Phase 2: Returns mock predictions with realistic structure.
Phase 3: Load real ONNX model, run inference on OHLCV features.

Output:
  - action: BUY / SELL / HOLD
  - confidence: 0.0 — 1.0
  - prediction: predicted close price
  - support_levels: list of S/R levels
  - reasoning: human-readable explanation
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseAnalyzer, AnalysisResult

logger = logging.getLogger("hybrid.lstm")


class LSTMAnalyzer(BaseAnalyzer):
    """LSTM price prediction analyzer — mock for Phase 2, ONNX for Phase 3."""

    name = "lstm"

    # Simulated inference delay (ms) to test ThreadPoolExecutor timeout
    MOCK_DELAY_MS = 1500

    def analyze(self, ohlcv: pd.DataFrame, symbol: str, **kwargs) -> AnalysisResult:
        if len(ohlcv) < 20:
            return AnalysisResult(
                analyzer=self.name,
                action="HOLD",
                confidence=0.0,
                reasoning=f"Insufficient data: only {len(ohlcv)} candles (need ≥20)",
            )

        # Simulate model inference delay
        time.sleep(self.MOCK_DELAY_MS / 1000)

        closes = ohlcv["close"].astype(float).values
        highs = ohlcv["high"].astype(float).values
        lows = ohlcv["low"].astype(float).values

        current_price = closes[-1]
        recent_closes = closes[-20:]

        # ── Mock LSTM features ──
        # Moving averages
        sma_5 = np.mean(recent_closes[-5:])
        sma_10 = np.mean(recent_closes[-10:])
        sma_20 = np.mean(recent_closes)

        # Price momentum (last 5 bars)
        momentum = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) > 6 else 0

        # Volatility (ATR proxy)
        tr = np.maximum(
            highs[-20:] - lows[-20:],
            np.maximum(
                np.abs(highs[-20:] - np.roll(closes, 1)[-20:]),
                np.abs(lows[-20:] - np.roll(closes, 1)[-20:])
            )
        )
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)

        # ── Mock prediction (replace with ONNX inference) ──
        # Simulate: slight upward bias for uptrend, downward for downtrend
        trend = 1 if sma_5 > sma_10 else -1
        noise = random.gauss(0, atr * 0.05)
        predicted_price = current_price + (trend * atr * 0.3) + noise

        # ── Direction + Confidence ──
        price_diff_pct = (predicted_price - current_price) / current_price * 100

        if price_diff_pct > 0.01:
            action = "BUY"
            confidence = min(0.5 + abs(price_diff_pct) * 2, 0.92)
        elif price_diff_pct < -0.01:
            action = "SELL"
            confidence = min(0.5 + abs(price_diff_pct) * 2, 0.92)
        else:
            action = "HOLD"
            confidence = max(0.3, 1.0 - abs(price_diff_pct) * 10)

        # Boost confidence when MAs agree
        if sma_5 > sma_10 > sma_20 and action == "BUY":
            confidence = min(confidence * 1.1, 0.95)
        elif sma_5 < sma_10 < sma_20 and action == "SELL":
            confidence = min(confidence * 1.1, 0.95)

        # ── Support / Resistance levels ──
        lookback = min(50, len(ohlcv))
        recent_lows = lows[-lookback:]
        recent_highs = highs[-lookback:]

        support_levels = sorted([
            round(float(np.percentile(recent_lows, p)), 2)
            for p in [10, 25, 50]
        ])
        resistance_levels = sorted([
            round(float(np.percentile(recent_highs, p)), 2)
            for p in [50, 75, 90]
        ])

        # ── SL / TP calculation ──
        if action == "BUY":
            sl = round(current_price - atr * 1.5, 2)
            tp = round(current_price + atr * 2.5, 2)
        elif action == "SELL":
            sl = round(current_price + atr * 1.5, 2)
            tp = round(current_price - atr * 2.5, 2)
        else:
            sl = tp = None

        reasoning_parts = [
            f"LSTM mock prediction: {action}",
            f"Current={current_price:.2f} → Predicted={predicted_price:.2f} ({price_diff_pct:+.3f}%)",
            f"Momentum={momentum:+.3f}% | ATR={atr:.2f}",
            f"SMA5={sma_5:.2f} > SMA10={sma_10:.2f} > SMA20={sma_20:.2f}" if sma_5 > sma_10 > sma_20
            else f"SMA5={sma_5:.2f} < SMA10={sma_10:.2f} < SMA20={sma_20:.2f}" if sma_5 < sma_10 < sma_20
            else f"SMA5={sma_5:.2f} SMA10={sma_10:.2f} SMA20={sma_20:.2f} (mixed)",
            f"S/R: Support {support_levels} | Resistance {resistance_levels}",
        ]

        return AnalysisResult(
            analyzer=self.name,
            action=action,
            confidence=round(confidence, 4),
            reasoning=" | ".join(reasoning_parts),
            metadata={
                "predicted_price": round(predicted_price, 2),
                "price_diff_pct": round(price_diff_pct, 4),
                "momentum": round(momentum, 4),
                "atr": round(atr, 4),
                "sma_5": round(sma_5, 2),
                "sma_10": round(sma_10, 2),
                "sma_20": round(sma_20, 2),
                "support_levels": support_levels,
                "resistance_levels": resistance_levels,
                "sl": sl,
                "tp": tp,
                "model": "MOCK_PLACEHOLDER",
            },
        )
