#!/usr/bin/env python3
"""
Engine #9 — TradingView Technical Analysis
===========================================
Independent technical analysis engine using tradingview_ta (for crypto)
and custom pandas-based calculations (for XAUUSD/forex).

Provides BUY/SELL/HOLD signals with confidence scores for the MTF matrix.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("tv_engine")

# ═══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════

def analyze(symbol: str, ohlcv: list[dict]) -> dict:
    """
    Run TradingView-style analysis on OHLCV data.

    Args:
        symbol: Asset symbol (XAUUSD, BTCUSD, ETHUSD)
        ohlcv:  List of dicts with keys: timestamp, open, high, low, close, volume

    Returns:
        dict with engine result format:
        {
            "direction": "BUY" | "SELL" | "HOLD",
            "confidence": 0.0-1.0,
            "details": "summary string",
            "indicators": { ...raw indicators... }
        }
    """
    if not ohlcv or len(ohlcv) < 30:
        return _neutral("Insufficient data (<30 bars)")

    try:
        df = pd.DataFrame(ohlcv)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) < 30:
            return _neutral("Insufficient clean data")

        prices = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        volumes = df["volume"].values if "volume" in df.columns else None

        # ── Compute all indicators ──
        rsi_val = _rsi(prices, 14)
        macd_line, signal_line, macd_hist = _macd(prices)
        adx_val, plus_di, minus_di = _adx(highs, lows, prices, 14)
        bb_upper, bb_middle, bb_lower = _bb(prices, 20)
        ema_9 = _ema(prices, 9)
        ema_21 = _ema(prices, 21)
        ema_200 = _ema(prices, 200)
        stoch_k, stoch_d = _stochastic(highs, lows, prices, 14)
        mom_val = _momentum(prices, 10)
        atr_val = _atr(highs, lows, prices, 14)

        # ── Score components ──
        score = 0.0
        signals = []
        max_score = 0  # track max possible score for normalization

        # 1. RSI (weight: 15%)
        max_score += 15
        if rsi_val is not None:
            if rsi_val < 30:
                score += 15
                signals.append("RSI oversold")
            elif rsi_val > 70:
                pass  # overbought = bearish
                signals.append("RSI overbought")
            elif rsi_val < 45:
                score += 7
                signals.append(f"RSI {rsi_val:.1f} (bullish bias)")
            elif rsi_val > 55:
                signals.append(f"RSI {rsi_val:.1f} (bearish bias)")
            else:
                score += 5
                signals.append(f"RSI {rsi_val:.1f} (neutral)")

        # 2. MACD (weight: 20%)
        max_score += 20
        if macd_line is not None and signal_line is not None:
            if macd_line > signal_line and macd_hist > 0:
                score += 20
                signals.append("MACD bullish (+)")
            elif macd_line > signal_line:
                score += 10
                signals.append("MACD bullish-ish")
            elif macd_line < signal_line and macd_hist < 0:
                signals.append("MACD bearish (-)")
            else:
                signals.append("MACD bearish-ish")
                score += 5

        # 3. ADX + DI (weight: 15%)
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

        # 4. Bollinger Bands (weight: 10%)
        max_score += 10
        current_price = prices[-1]
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
                        signals.append(f"BB mid range ({bb_pos*100:.0f}%)")
                        score += 5

        # 5. EMA Crossover (weight: 15%)
        max_score += 15
        if ema_9 is not None and ema_21 is not None:
            if ema_9 > ema_21 and prices[-1] > ema_9:
                score += 15
                signals.append("EMA9 > EMA21 bullish align")
            elif ema_9 > ema_21:
                score += 7
                signals.append("EMA9 > EMA21 warn")
            elif ema_9 < ema_21 and prices[-1] < ema_9:
                signals.append("EMA9 < EMA21 bearish align")
            else:
                signals.append("EMA9 < EMA21 warn")
                score += 3

        # 6. EMA200 Trend (weight: 10%)
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

        # 7. Stochastic (weight: 10%)
        max_score += 10
        if stoch_k is not None and stoch_d is not None:
            if stoch_k < 20 and stoch_k > stoch_d:
                score += 10
                signals.append("Stoch oversold crossover")
            elif stoch_k < 20:
                score += 5
                signals.append("Stoch oversold")
            elif stoch_k > 80 and stoch_k < stoch_d:
                signals.append("Stoch overbought crossover")
            elif stoch_k > 80:
                signals.append("Stoch overbought")
            else:
                score += 5
                signals.append(f"Stoch {stoch_k:.0f} (neutral)")

        # 8. Momentum (weight: 5%)
        max_score += 5
        if mom_val is not None:
            if mom_val > 0:
                score += 5
                signals.append(f"Mom {mom_val:+.1f} (positive)")
            else:
                signals.append(f"Mom {mom_val:+.1f} (negative)")

        # ── Determine direction ──
        total_score = score / max_score * 100 if max_score > 0 else 50

        if total_score >= 65:
            direction = "BUY"
            confidence = min((total_score - 50) / 50, 0.95)
        elif total_score <= 35:
            direction = "SELL"
            confidence = min((50 - total_score) / 50, 0.95)
        else:
            direction = "HOLD"
            confidence = 0.5 + abs(50 - total_score) / 100

        # Build details
        detail_parts = signals[:4]  # top 4 signals
        detail_str = " | ".join(detail_parts) if detail_parts else "mixed signals"
        detail_str += f" | TV-score:{total_score:.0f}"

        # Build indicators dict
        indicators = {
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
            "stoch_d": stoch_d,
            "mom": mom_val,
            "atr": atr_val,
            "tv_score": round(total_score, 1),
        }

        return {
            "direction": direction,
            "confidence": round(confidence, 3),
            "details": detail_str,
            "indicators": indicators,
        }

    except Exception as e:
        logger.warning(f"TV engine error for {symbol}: {e}")
        return _neutral(f"calc error: {e}")


# ═══════════════════════════════════════════════════════════════════
#  INTERNAL: Indicator Calculations (pure numpy)
# ═══════════════════════════════════════════════════════════════════

def _neutral(reason: str) -> dict:
    return {"direction": "HOLD", "confidence": 0.5, "details": f"HOLD — {reason}", "indicators": {}}


def _rsi(prices: np.ndarray, period: int = 14) -> Optional[float]:
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


def _ema(prices: np.ndarray, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema_val = float(np.mean(prices[:period]))
    for p in prices[period:]:
        ema_val = (p - ema_val) * multiplier + ema_val
    return ema_val


def _macd(prices: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    if len(prices) < slow + signal:
        return None, None, None
    ema_fast = _ema(prices, fast)
    ema_slow = _ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line = ema_fast - ema_slow

    # For signal line, compute EMA of MACD values
    macd_values = []
    for i in range(slow - 1, len(prices)):
        ef = _ema(prices[:i + 1], fast)
        es = _ema(prices[:i + 1], slow)
        if ef is not None and es is not None:
            macd_values.append(ef - es)

    if len(macd_values) < signal:
        return macd_line, macd_line, 0.0

    signal_line = macd_values[-1]
    if len(macd_values) > signal:
        # Simple signal: mean of recent
        signal_line = float(np.mean(macd_values[-signal:]))

    return macd_line, signal_line, macd_line - signal_line


def _bb(prices: np.ndarray, period: int = 20):
    if len(prices) < period:
        return None, None, None
    recent = prices[-period:]
    mean = float(np.mean(recent))
    std = float(np.std(recent))
    return mean + 2 * std, mean, mean - 2 * std


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14):
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        trs.append(max(hl, hc, lc))
    if not trs:
        return None
    return float(np.mean(trs[-period:]))


def _adx(highs, lows, closes, period=14):
    if len(highs) < period * 2:
        return None, None, None

    up_moves = []
    down_moves = []
    trs = []

    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        up_moves.append(up if up > down and up > 0 else 0)
        down_moves.append(down if down > up and down > 0 else 0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    if len(trs) < period:
        return None, None, None

    atr = float(np.mean(trs[-period:]))
    plus_di = 100 * float(np.mean(up_moves[-period:])) / atr if atr > 0 else 0
    minus_di = 100 * float(np.mean(down_moves[-period:])) / atr if atr > 0 else 0

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx, plus_di, minus_di


def _stochastic(highs, lows, closes, period=14):
    if len(highs) < period:
        return None, None
    recent_high = float(np.max(highs[-period:]))
    recent_low = float(np.min(lows[-period:]))
    if recent_high == recent_low:
        return 50.0, 50.0
    k = 100 * (closes[-1] - recent_low) / (recent_high - recent_low)
    return k, k  # simplified


def _momentum(prices, period=10):
    if len(prices) < period + 1:
        return None
    return float(prices[-1] - prices[-period - 1])


# ═══════════════════════════════════════════════════════════════════
#  SELF-TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import yfinance as yf

    # Test with real XAUUSD data
    ticker = yf.Ticker("GC=F")
    hist = ticker.history(period="5d", interval="15m")
    if not hist.empty:
        bars = []
        for idx, row in hist.iterrows():
            bars.append({
                "timestamp": idx.isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if "Volume" in hist.columns else 0,
            })
        result = analyze("XAUUSD", bars)
        print(f"Direction: {result['direction']}")
        print(f"Confidence: {result['confidence']}")
        print(f"Details: {result['details']}")
        for k, v in result.get("indicators", {}).items():
            print(f"  {k}: {v}")
    else:
        print("No data from yfinance")
