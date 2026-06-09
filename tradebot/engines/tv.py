"""
tv.py — TradingView-Style Technical Analysis Engine

Migrated from: scripts/tv_engine.py
Conforms to: tradebot.engines.base.Engine interface

Computes RSI, MACD, ADX, Bollinger Bands, EMA crossovers, Stochastic,
and Momentum — producing a composite BUY/SELL/HOLD signal.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)


def _ticks_to_df(ticks: list[Tick]) -> pd.DataFrame:
    """Convert ticks to DataFrame for analysis."""
    data = [{"open": t.price, "high": t.price, "low": t.price, "close": t.price, "volume": 1}
            for t in ticks]
    df = pd.DataFrame(data)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


# ── Indicator Calculations ─────────────────────────────────────────


def _rsi(prices: np.ndarray, period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices[-period - 1:])
    gains = deltas[deltas > 0].sum()
    losses = -deltas[deltas < 0].sum()
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    avg_gain = gains / period
    avg_loss = losses / period
    rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
    return 100.0 - 100.0 / (1.0 + rs)


def _ema(prices: np.ndarray, period: int) -> float | None:
    if len(prices) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema_val = float(np.mean(prices[:period]))
    for p in prices[period:]:
        ema_val = (p - ema_val) * multiplier + ema_val
    return ema_val


def _macd(prices: np.ndarray, fast: int = 12, slow: int = 26, signal_period: int = 9):
    if len(prices) < slow + signal_period:
        return None, None, None
    ema_fast = _ema(prices, fast)
    ema_slow = _ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line = ema_fast - ema_slow
    macd_values: list[float] = []
    for i in range(slow - 1, len(prices)):
        ef = _ema(prices[:i + 1], fast)
        es = _ema(prices[:i + 1], slow)
        if ef is not None and es is not None:
            macd_values.append(ef - es)
    if len(macd_values) < signal_period:
        return macd_line, macd_line, 0.0
    signal_line = float(np.mean(macd_values[-signal_period:]))
    return macd_line, signal_line, macd_line - signal_line


def _bb(prices: np.ndarray, period: int = 20):
    if len(prices) < period:
        return None, None, None
    recent = prices[-period:]
    mean = float(np.mean(recent))
    std = float(np.std(recent))
    return mean + 2 * std, mean, mean - 2 * std


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float | None:
    if len(highs) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(highs)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        trs.append(max(hl, hc, lc))
    return float(np.mean(trs[-period:])) if trs else None


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14):
    if len(highs) < period * 2:
        return None, None, None
    up_moves: list[float] = []
    down_moves: list[float] = []
    trs: list[float] = []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        up_moves.append(up if up > down and up > 0 else 0)
        down_moves.append(down if down > up and down > 0 else 0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))  # noqa: E501
    if len(trs) < period:
        return None, None, None
    atr_val = float(np.mean(trs[-period:]))
    plus_di = 100 * float(np.mean(up_moves[-period:])) / atr_val if atr_val > 0 else 0
    minus_di = 100 * float(np.mean(down_moves[-period:])) / atr_val if atr_val > 0 else 0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx, plus_di, minus_di


def _stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14):
    if len(highs) < period:
        return None, None
    recent_high = float(np.max(highs[-period:]))
    recent_low = float(np.min(lows[-period:]))
    if recent_high == recent_low:
        return 50.0, 50.0
    k = 100 * (closes[-1] - recent_low) / (recent_high - recent_low)
    return k, k


def _momentum(prices: np.ndarray, period: int = 10) -> float | None:
    if len(prices) < period + 1:
        return None
    return float(prices[-1] - prices[-period - 1])


# ── Engine ─────────────────────────────────────────────────────────


class TVEngine(Engine):
    """TradingView-Style Technical Analysis Engine.

    Computes a composite signal from RSI, MACD, ADX, Bollinger Bands,
    EMA crossovers, Stochastic, and Momentum indicators.
    """

    @property
    def name(self) -> str:
        return "tv_engine"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks using TV-style technical indicators."""
        if not ticks or len(ticks) < 30:
            LOG.debug("TV: insufficient ticks")
            return None

        try:
            df = _ticks_to_df(ticks)
            if len(df) < 30:
                return None

            prices = df["close"].values
            highs = df["high"].values
            lows = df["low"].values
            current_price = float(prices[-1])

            # Compute all indicators
            rsi_val = _rsi(prices, 14)
            macd_line, signal_line, macd_hist = _macd(prices)
            adx_val, plus_di, minus_di = _adx(highs, lows, prices, 14)
            bb_upper, bb_middle, bb_lower = _bb(prices, 20)
            ema_9 = _ema(prices, 9)
            ema_21 = _ema(prices, 21)
            ema_200 = _ema(prices, 200)
            stoch_k, stoch_d = _stochastic(highs, lows, prices, 14)
            mom_val = _momentum(prices, 10)

            # Score components
            score = 0.0
            signals: list[str] = []
            max_score = 0

            # 1. RSI (weight 15%)
            max_score += 15
            if rsi_val is not None:
                if rsi_val < 30:
                    score += 15
                    signals.append("RSI oversold")
                elif rsi_val < 45:
                    score += 7
                    signals.append(f"RSI {rsi_val:.1f} (bullish bias)")
                elif rsi_val > 70:
                    signals.append("RSI overbought")
                elif rsi_val > 55:
                    signals.append(f"RSI {rsi_val:.1f} (bearish bias)")
                else:
                    score += 5
                    signals.append(f"RSI {rsi_val:.1f} (neutral)")

            # 2. MACD (weight 20%)
            max_score += 20
            if macd_line is not None and signal_line is not None:
                if macd_line > signal_line and macd_hist is not None and macd_hist > 0:
                    score += 20
                    signals.append("MACD bullish (+)")
                elif macd_line > signal_line:
                    score += 10
                    signals.append("MACD bullish-ish")
                elif macd_line < signal_line:
                    signals.append("MACD bearish")
                else:
                    score += 5
                    signals.append("MACD bearish-ish")

            # 3. ADX + DI (weight 15%)
            max_score += 15
            if adx_val is not None and plus_di is not None and minus_di is not None:
                if adx_val >= 25 and plus_di > minus_di:
                    score += 15
                    signals.append(f"ADX {adx_val:.0f} strong uptrend")
                elif adx_val >= 25 and minus_di > plus_di:
                    signals.append(f"ADX {adx_val:.0f} strong downtrend")
                elif adx_val < 25:
                    score += 7
                    signals.append(f"ADX {adx_val:.0f} (weak trend)")

            # 4. Bollinger Bands (weight 10%)
            max_score += 10
            if bb_lower is not None and bb_upper is not None:
                bb_range = bb_upper - bb_lower
                if bb_range > 0:
                    bb_pos = (current_price - bb_lower) / bb_range
                    if bb_pos < 0.2:
                        score += 10
                        signals.append("BB lower band bounce (oversold)")
                    elif bb_pos > 0.8:
                        signals.append("BB upper band (overbought)")
                    else:
                        bb_width = bb_range / bb_middle * 100 if bb_middle and bb_middle > 0 else 0
                        if bb_width < 5:
                            signals.append(f"BB squeeze ({bb_width:.1f}%)")
                            score += 3
                        else:
                            signals.append(f"BB mid range ({bb_pos * 100:.0f}%)")
                            score += 5

            # 5. EMA Crossover (weight 15%)
            max_score += 15
            if ema_9 is not None and ema_21 is not None:
                if ema_9 > ema_21 and current_price > ema_9:
                    score += 15
                    signals.append("EMA9 > EMA21 bullish align")
                elif ema_9 > ema_21:
                    score += 7
                    signals.append("EMA9 > EMA21 warn")
                elif ema_9 < ema_21 and current_price < ema_9:
                    signals.append("EMA9 < EMA21 bearish align")
                else:
                    signals.append("EMA9 < EMA21 warn")
                    score += 3

            # 6. EMA200 Trend (weight 10%)
            max_score += 10
            if ema_200 is not None:
                ema200_dist = (current_price - ema_200) / ema_200 * 100
                if ema200_dist > 0 and current_price > ema_200:
                    score += 10
                    signals.append(f"Above EMA200 (+{ema200_dist:.1f}%)")
                elif ema200_dist > 0:
                    score += 5
                    signals.append(f"Near EMA200 ({ema200_dist:.1f}%)")
                else:
                    signals.append(f"Below EMA200 ({ema200_dist:.1f}%)")

            # 7. Stochastic (weight 10%)
            max_score += 10
            if stoch_k is not None and stoch_d is not None:
                if stoch_k < 20 and stoch_k > stoch_d:
                    score += 10
                    signals.append("Stoch oversold crossover")
                elif stoch_k < 20:
                    score += 5
                    signals.append("Stoch oversold")
                elif stoch_k > 80:
                    signals.append("Stoch overbought")
                else:
                    score += 5
                    signals.append(f"Stoch {stoch_k:.0f} (neutral)")

            # 8. Momentum (weight 5%)
            max_score += 5
            if mom_val is not None:
                if mom_val > 0:
                    score += 5
                    signals.append(f"Mom {mom_val:+.1f} (positive)")
                else:
                    signals.append(f"Mom {mom_val:+.1f} (negative)")

            # Determine direction
            total_score = score / max_score * 100 if max_score > 0 else 50

            if total_score >= 65:
                direction = "CALL"
                confidence = min((total_score - 50) / 50, 0.95)
            elif total_score <= 35:
                direction = "PUT"
                confidence = min((50 - total_score) / 50, 0.95)
            else:
                direction = "HOLD"
                confidence = 0.5 + abs(50 - total_score) / 100

            if direction == "HOLD":
                return None

            return Signal(
                symbol="XAUUSD",
                direction=direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=round(confidence, 3),
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if confidence >= 0.7 else (
                    SignalGrade.MODERATE if confidence >= 0.5 else SignalGrade.WEAK
                ),
                metadata={
                    "engine": self.name,
                    "tv_score": round(total_score, 1),
                    "rsi": rsi_val,
                    "macd": macd_line,
                    "macd_signal": signal_line,
                    "adx": adx_val,
                    "plus_di": plus_di,
                    "minus_di": minus_di,
                    "bb_upper": bb_upper,
                    "bb_lower": bb_lower,
                    "bb_middle": bb_middle,
                    "ema9": ema_9,
                    "ema21": ema_21,
                    "ema200": ema_200,
                    "stoch_k": stoch_k,
                    "mom": mom_val,
                    "atr": _atr(highs, lows, prices, 14),
                    "signals": signals[:4],
                },
            )
        except Exception as exc:
            LOG.warning("TV engine error: %s", exc)
            raise SignalError("TV analysis failed", details={"error": str(exc)}) from exc
