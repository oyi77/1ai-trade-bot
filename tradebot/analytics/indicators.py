"""Production technical analysis engine.

Computes real trading signals from OHLCV data using well-established
technical indicators. Designed to be deterministic, testable, and safe
to run in production.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

# ── Constants ──────────────────────────────────────────────────────

RSI_PERIOD: int = 14
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9
EMA_FAST_PERIOD: int = 9
EMA_SLOW_PERIOD: int = 21
BOLLINGER_PERIOD: int = 20
BOLLINGER_STD: float = 2.0
STOCH_K_PERIOD: int = 14
STOCH_D_PERIOD: int = 3
ATR_PERIOD: int = 14
ICHIMOKU_TENKAN: int = 9
ICHIMOKU_KIJUN: int = 26
ICHIMOKU_SENKOU_B: int = 52
SUPERTREND_PERIOD: int = 10
SUPERTREND_MULT: float = 3.0

RSI_OVERSOLD: float = 30.0
RSI_OVERBOUGHT: float = 70.0
STOCH_OVERSOLD: float = 20.0
STOCH_OVERBOUGHT: float = 80.0

MIN_CANDLES: int = 60
SCAN_SL_PCT: float = 0.02
SCAN_TP_PCT: float = 0.04
ATR_SL_MULTIPLIER: float = 1.5
ATR_TP_MULTIPLIER: float = 2.5


@dataclass(frozen=True)
class Candle:
    """Single OHLCV candle. Times must be monotonic."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def is_valid(self) -> bool:
        return self.high >= self.low > 0 and self.open > 0 and self.close > 0


# ── Core indicator implementations ─────────────────────────────────


def _to_arrays(candles: Sequence[Candle]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    opens = np.array([c.open for c in candles], dtype=np.float64)
    highs = np.array([c.high for c in candles], dtype=np.float64)
    lows = np.array([c.low for c in candles], dtype=np.float64)
    closes = np.array([c.close for c in candles], dtype=np.float64)
    volumes = np.array([c.volume for c in candles], dtype=np.float64)
    return opens, highs, lows, closes, volumes


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) < period:
        return np.full(len(values), np.nan)
    alpha = 2.0 / (period + 1)
    out = np.empty(len(values), dtype=np.float64)
    out[:period - 1] = np.nan
    seed = float(np.mean(values[:period]))
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi(closes: np.ndarray, period: int = RSI_PERIOD) -> np.ndarray:
    if len(closes) < period + 1:
        return np.full(len(closes), np.nan)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.empty(len(closes), dtype=np.float64)
    avg_loss = np.empty(len(closes), dtype=np.float64)
    avg_gain[:period] = np.nan
    avg_loss[:period] = np.nan
    avg_gain[period] = float(np.mean(gains[:period]))
    avg_loss[period] = float(np.mean(losses[:period]))
    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain) * 100.0,
                    where=avg_loss > 0)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema_fast = _ema(closes, MACD_FAST)
    ema_slow = _ema(closes, MACD_SLOW)
    macd_line = ema_fast - ema_slow
    signal = _ema(macd_line[MACD_SLOW - 1:], MACD_SIGNAL)
    signal_full = np.full(len(closes), np.nan)
    signal_full[MACD_SLOW - 1:] = signal
    histogram = macd_line - signal_full
    return macd_line, signal_full, histogram


def _bollinger(closes: np.ndarray, period: int = BOLLINGER_PERIOD,
                std_mult: float = BOLLINGER_STD) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(closes) < period:
        nan = np.full(len(closes), np.nan)
        return nan, nan, nan
    sma = np.empty(len(closes), dtype=np.float64)
    sma[:period - 1] = np.nan
    std = np.empty(len(closes), dtype=np.float64)
    std[:period - 1] = np.nan
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        sma[i] = float(np.mean(window))
        std[i] = float(np.std(window, ddof=0))
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper, sma, lower


def _stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 k_period: int = STOCH_K_PERIOD,
                 d_period: int = STOCH_D_PERIOD) -> tuple[np.ndarray, np.ndarray]:
    if len(closes) < k_period:
        nan = np.full(len(closes), np.nan)
        return nan, nan
    k = np.empty(len(closes), dtype=np.float64)
    k[:k_period - 1] = np.nan
    for i in range(k_period - 1, len(closes)):
        high_max = float(np.max(highs[i - k_period + 1: i + 1]))
        low_min = float(np.min(lows[i - k_period + 1: i + 1]))
        if high_max == low_min:
            k[i] = 50.0
        else:
            k[i] = ((closes[i] - low_min) / (high_max - low_min)) * 100.0
    d = _ema(k[k_period - 1:], d_period)
    d_full = np.full(len(closes), np.nan)
    d_full[k_period - 1:] = d
    return k, d_full


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
          period: int = ATR_PERIOD) -> np.ndarray:
    if len(closes) < period + 1:
        return np.full(len(closes), np.nan)
    tr = np.empty(len(closes), dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
    atr = np.empty(len(closes), dtype=np.float64)
    atr[:period] = np.nan
    atr[period] = float(np.mean(tr[1:period + 1]))
    for i in range(period + 1, len(closes)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _ichimoku(highs: np.ndarray, lows: np.ndarray) -> dict[str, np.ndarray]:
    n = len(highs)

    def midpoint(period: int, idx: int) -> float:
        if idx < period - 1:
            return np.nan
        h = float(np.max(highs[idx - period + 1: idx + 1]))
        l = float(np.min(lows[idx - period + 1: idx + 1]))
        return (h + l) / 2.0

    tenkan = np.array([midpoint(ICHIMOKU_TENKAN, i) for i in range(n)])
    kijun = np.array([midpoint(ICHIMOKU_KIJUN, i) for i in range(n)])
    senkou_a = (tenkan + kijun) / 2
    senkou_b = np.array([midpoint(ICHIMOKU_SENKOU_B, i) for i in range(n)])
    return {
        "tenkan": tenkan, "kijun": kijun,
        "senkou_a": senkou_a, "senkou_b": senkou_b,
    }


def _supertrend(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 period: int = SUPERTREND_PERIOD,
                 multiplier: float = SUPERTREND_MULT) -> tuple[np.ndarray, np.ndarray]:
    n = len(closes)
    if n < period + 1:
        nan = np.full(n, np.nan)
        return nan, nan

    atr = _atr(highs, lows, closes, period)
    hl2 = (highs + lows) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    final_upper = np.empty(n, dtype=np.float64)
    final_lower = np.empty(n, dtype=np.float64)
    trend = np.ones(n, dtype=np.int64)
    supertrend = np.empty(n, dtype=np.float64)

    final_upper[period] = upper[period]
    final_lower[period] = lower[period]
    
    # Initialize trend based on price position relative to midpoint
    mid = (upper[period] + lower[period]) / 2
    if closes[period] >= mid:
        trend[period] = 1
        supertrend[period] = lower[period]
    else:
        trend[period] = -1
        supertrend[period] = upper[period]

    for i in range(period + 1, n):
        if closes[i] > upper[i - 1]:
            trend[i] = 1
        elif closes[i] < lower[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

        if trend[i] == 1:
            final_lower[i] = lower[i] if trend[i - 1] == -1 else max(lower[i], final_lower[i - 1])
            final_upper[i] = upper[i]
            supertrend[i] = final_lower[i]
        else:
            final_upper[i] = upper[i] if trend[i - 1] == 1 else min(upper[i], final_upper[i - 1])
            final_lower[i] = lower[i]
            supertrend[i] = final_upper[i]
    return trend, supertrend


# ── Public API ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Latest values for all computed indicators."""

    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    ema_fast: float
    ema_slow: float
    bollinger_upper: float
    bollinger_middle: float
    bollinger_lower: float
    stochastic_k: float
    stochastic_d: float
    atr: float
    ichimoku_tenkan: float
    ichimoku_kijun: float
    ichimoku_senkou_a: float
    ichimoku_senkou_b: float
    supertrend_direction: int
    supertrend_value: float
    current_price: float


def compute_indicators(candles: Sequence[Candle]) -> IndicatorSnapshot:
    """Compute all technical indicators from candles."""
    if len(candles) < MIN_CANDLES:
        raise ValueError(
            f"Need at least {MIN_CANDLES} candles, got {len(candles)}"
        )
    opens, highs, lows, closes, _ = _to_arrays(candles)
    macd_line, macd_signal, macd_hist = _macd(closes)
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    stoch_k, stoch_d = _stochastic(highs, lows, closes)
    atr_arr = _atr(highs, lows, closes)
    ichi = _ichimoku(highs, lows)
    st_dir, st_val = _supertrend(highs, lows, closes)
    last = -1
    return IndicatorSnapshot(
        rsi=float(_rsi(closes)[last]),
        macd=float(macd_line[last]),
        macd_signal=float(macd_signal[last]),
        macd_histogram=float(macd_hist[last]),
        ema_fast=float(_ema(closes, EMA_FAST_PERIOD)[last]),
        ema_slow=float(_ema(closes, EMA_SLOW_PERIOD)[last]),
        bollinger_upper=float(bb_upper[last]),
        bollinger_middle=float(bb_mid[last]),
        bollinger_lower=float(bb_lower[last]),
        stochastic_k=float(stoch_k[last]),
        stochastic_d=float(stoch_d[last]),
        atr=float(atr_arr[last]),
        ichimoku_tenkan=float(ichi["tenkan"][last]),
        ichimoku_kijun=float(ichi["kijun"][last]),
        ichimoku_senkou_a=float(ichi["senkou_a"][last]),
        ichimoku_senkou_b=float(ichi["senkou_b"][last]),
        supertrend_direction=int(st_dir[last]),
        supertrend_value=float(st_val[last]) if not np.isnan(st_val[last]) else closes[last],
        current_price=float(closes[last]),
    )


def score_confidence(snapshot: IndicatorSnapshot,
                      requested_indicators: Sequence[str] | None = None) -> tuple[float, str]:
    """Score confidence (0–1) and return directional reason string.

    Each indicator votes bullish or bearish with a weight. Confidence is
    the absolute net score normalized. Reason summarizes confluence.
    """
    votes: list[tuple[str, int, float]] = []

    rsi_vote = 0
    rsi_strength = 0.0
    if snapshot.rsi < RSI_OVERSOLD:
        rsi_vote = 1
        rsi_strength = (RSI_OVERSOLD - snapshot.rsi) / RSI_OVERSOLD
    elif snapshot.rsi > RSI_OVERBOUGHT:
        rsi_vote = -1
        rsi_strength = (snapshot.rsi - RSI_OVERBOUGHT) / (100 - RSI_OVERBOUGHT)
    votes.append(("RSI", rsi_vote, min(rsi_strength, 1.0)))

    macd_vote = 0
    if not np.isnan(snapshot.macd_histogram):
        macd_vote = 1 if snapshot.macd_histogram > 0 else -1
    votes.append(("MACD", macd_vote, min(abs(snapshot.macd_histogram) / max(snapshot.current_price * 0.01, 0.01), 1.0)))

    ema_vote = 1 if snapshot.ema_fast > snapshot.ema_slow else -1
    votes.append(("EMA", ema_vote, 0.5))

    bb_vote = 0
    bb_strength = 0.0
    if snapshot.current_price < snapshot.bollinger_lower:
        bb_vote = 1
        bb_strength = (snapshot.bollinger_lower - snapshot.current_price) / snapshot.bollinger_lower
    elif snapshot.current_price > snapshot.bollinger_upper:
        bb_vote = -1
        bb_strength = (snapshot.current_price - snapshot.bollinger_upper) / snapshot.bollinger_upper
    votes.append(("Bollinger", bb_vote, min(bb_strength * 10, 1.0)))

    stoch_vote = 0
    stoch_strength = 0.0
    if not np.isnan(snapshot.stochastic_k):
        if snapshot.stochastic_k < STOCH_OVERSOLD:
            stoch_vote = 1
            stoch_strength = (STOCH_OVERSOLD - snapshot.stochastic_k) / STOCH_OVERSOLD
        elif snapshot.stochastic_k > STOCH_OVERBOUGHT:
            stoch_vote = -1
            stoch_strength = (snapshot.stochastic_k - STOCH_OVERBOUGHT) / (100 - STOCH_OVERBOUGHT)
    votes.append(("Stochastic", stoch_vote, min(stoch_strength, 1.0)))

    ichi_vote = 0
    if not np.isnan(snapshot.ichimoku_tenkan) and not np.isnan(snapshot.ichimoku_kijun):
        if snapshot.current_price > snapshot.ichimoku_senkou_a and snapshot.current_price > snapshot.ichimoku_senkou_b:
            ichi_vote = 1
        elif snapshot.current_price < snapshot.ichimoku_senkou_a and snapshot.current_price < snapshot.ichimoku_senkou_b:
            ichi_vote = -1
    votes.append(("Ichimoku", ichi_vote, 0.7))

    st_vote = snapshot.supertrend_direction
    votes.append(("SuperTrend", st_vote, 0.8))

    requested_set = {i.lower() for i in (requested_indicators or [])}
    if requested_set:
        weights = {"RSI": 0.20, "MACD": 0.20, "EMA": 0.15, "Bollinger": 0.10,
                    "Stochastic": 0.10, "Ichimoku": 0.10, "SuperTrend": 0.15}
        filtered = [(name, v, s) for name, v, s in votes
                     if name.lower() in requested_set
                     or any(ri in name.lower() for ri in requested_set)]
        if filtered:
            total_weight = sum(weights.get(name, 0.1) for name, _, _ in filtered)
            weighted_sum = sum(v * s * weights.get(name, 0.1) for name, v, s in filtered)
        else:
            return 0.5, "No matching indicators"
    else:
        total_weight = 7.0
        weighted_sum = sum(v * s for _, v, s in votes)

    if total_weight == 0:
        return 0.5, "No data"

    net = weighted_sum / total_weight
    confidence = min(abs(net), 1.0)
    direction = "bullish" if net > 0 else "bearish" if net < 0 else "neutral"
    reason = f"Confluence score: net={net:.2f}, {direction} bias, confidence={confidence:.0%}"
    return confidence, reason


def calculate_levels(snapshot: IndicatorSnapshot,
                      direction: int) -> dict[str, float]:
    """Calculate entry, stop loss, and take profit levels."""
    price = snapshot.current_price
    atr = snapshot.atr if snapshot.atr > 0 else price * SCAN_SL_PCT
    if direction == 1:
        entry = round(price, 6)
        stop_loss = round(price - atr * ATR_SL_MULTIPLIER, 6)
        tp1 = round(price + atr * ATR_TP_MULTIPLIER, 6)
        tp2 = round(price + atr * ATR_TP_MULTIPLIER * 1.5, 6)
        tp3 = round(price + atr * ATR_TP_MULTIPLIER * 2.0, 6)
    else:
        entry = round(price, 6)
        stop_loss = round(price + atr * ATR_SL_MULTIPLIER, 6)
        tp1 = round(price - atr * ATR_TP_MULTIPLIER, 6)
        tp2 = round(price - atr * ATR_TP_MULTIPLIER * 1.5, 6)
        tp3 = round(price - atr * ATR_TP_MULTIPLIER * 2.0, 6)
    risk = abs(entry - stop_loss)
    reward = abs(tp1 - entry)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 1.0
    return {
        "entry_price": entry, "stop_loss": stop_loss,
        "take_profit_1": tp1, "take_profit_2": tp2, "take_profit_3": tp3,
        "risk_reward_ratio": rr_ratio,
    }


def analyze(candles: Sequence[Candle],
            requested_indicators: Sequence[str] | None = None) -> dict:
    """Run full TA pipeline. Returns dict ready for Signal model.

    Raises ValueError if not enough candles.
    """
    snapshot = compute_indicators(candles)
    confidence, reason = score_confidence(snapshot, requested_indicators)
    direction = 1 if "bullish" in reason else -1 if "bearish" in reason else 0
    levels = calculate_levels(snapshot, direction)
    return {
        **levels,
        "confidence": round(confidence, 2),
        "reason": reason,
        "direction": direction,
    }
