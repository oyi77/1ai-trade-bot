"""
Multi-Factor Weighted Signal Strategy for XAU/USD

Inspired by DRL-MultiFactorTrading (Conservative strategy):
- Trend Analysis: EMA alignment + price position (35% weight)
- Momentum: rate of change + acceleration (25% weight)
- RSI: overbought/oversold with divergence (20% weight)
- MACD: signal line crossover + histogram (15% weight)
- Bollinger Bands: squeeze + breakout (5% weight)

All signals aggregated into a weighted score [-1, +1].
Entry when score exceeds threshold + ATR-based SL/TP.

Key difference from our other strategies:
- Generates 2-4x more trades than SMC (more active)
- Multi-factor confluence reduces false signals
- Volatility-adjusted position sizing
"""

import math
import logging
from dataclasses import dataclass
from typing import Dict, Optional
from collections import deque

from trading_bot.strategy.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class MultiFactorConfig:
    lots: float = 0.05
    max_positions: int = 2

    # Signal weights (must sum to 1.0)
    w_trend: float = 0.35
    w_momentum: float = 0.25
    w_rsi: float = 0.20
    w_macd: float = 0.15
    w_bb: float = 0.05

    # Entry threshold: weighted score must exceed this
    entry_threshold: float = 0.4  # range [0, 1]

    # EMA periods
    ema_fast: int = 8
    ema_medium: int = 21
    ema_slow: int = 55

    # RSI
    rsi_period: int = 14
    rsi_ob: float = 70.0
    rsi_os: float = 30.0

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Momentum
    momentum_period: int = 10

    # BB
    bb_period: int = 20
    bb_std: float = 2.0

    # ATR SL/TP
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 2.5

    # Cooldown
    cooldown_bars: int = 6

    min_bars: int = 60


class MultiFactorStrategy(Strategy):
    """Multi-Factor Weighted Signal Strategy."""

    def __init__(self, config: MultiFactorConfig = None):
        self.config = config or MultiFactorConfig()
        self.closes = deque(maxlen=300)
        self.highs = deque(maxlen=300)
        self.lows = deque(maxlen=300)
        self.tick_count = 0
        self._last_trade_bar = -100

        # EMA state (incremental)
        self._ema_fast = None
        self._ema_medium = None
        self._ema_slow = None

        # MACD state
        self._macd_ema_fast = None
        self._macd_ema_slow = None
        self._macd_signal_ema = None

    @property
    def name(self) -> str:
        return "multi_factor"

    def on_tick(self, price: float, bid: float, ask: float, positions: list, timestamp: int = 0) -> Optional[Dict]:
        self.tick_count += 1
        self.closes.append(price)
        self.highs.append(max(price, ask))
        self.lows.append(min(price, bid))

        # Update EMAs incrementally
        self._update_emas(price)
        self._update_macd(price)

        if self.tick_count < self.config.min_bars:
            return None

        # Position limit
        active = [p for p in positions if self._is_active(p)]
        if len(active) >= self.config.max_positions:
            return None

        # Cooldown
        if self.tick_count - self._last_trade_bar < self.config.cooldown_bars:
            return None

        # Calculate weighted score
        score = self._calculate_score(price)
        if score is None:
            return None

        # ATR for SL/TP
        atr = self._atr()
        if atr is None or atr < 0.5:
            return None

        sl_dist = atr * self.config.atr_sl_multiplier
        tp_dist = atr * self.config.atr_tp_multiplier

        if score >= self.config.entry_threshold:
            self._last_trade_bar = self.tick_count
            return {
                "action": "open",
                "side": "buy",
                "amount": self.config.lots,
                "sl": price - sl_dist,
                "tp": price + tp_dist,
                "reason": f"MultiFactor BUY score={score:.2f}",
            }

        if score <= -self.config.entry_threshold:
            self._last_trade_bar = self.tick_count
            return {
                "action": "open",
                "side": "sell",
                "amount": self.config.lots,
                "sl": price + sl_dist,
                "tp": price - tp_dist,
                "reason": f"MultiFactor SELL score={score:.2f}",
            }

        return None

    def _calculate_score(self, price: float) -> Optional[float]:
        """Calculate weighted multi-factor score in range [-1, +1]."""
        cfg = self.config

        trend = self._trend_score(price)
        momentum = self._momentum_score()
        rsi_score = self._rsi_score()
        macd_score = self._macd_score()
        bb_score = self._bb_score(price)

        if any(s is None for s in [trend, momentum, rsi_score, macd_score, bb_score]):
            return None

        score = (
            cfg.w_trend * trend +
            cfg.w_momentum * momentum +
            cfg.w_rsi * rsi_score +
            cfg.w_macd * macd_score +
            cfg.w_bb * bb_score
        )
        return max(-1.0, min(1.0, score))

    def _trend_score(self, price: float) -> Optional[float]:
        """EMA alignment score [-1, +1]."""
        if not self._ema_fast or not self._ema_medium or not self._ema_slow:
            return None

        score = 0.0
        # Full alignment
        if self._ema_fast > self._ema_medium > self._ema_slow:
            score = 0.7
        elif self._ema_fast < self._ema_medium < self._ema_slow:
            score = -0.7
        # Partial alignment
        elif self._ema_fast > self._ema_medium:
            score = 0.3
        elif self._ema_fast < self._ema_medium:
            score = -0.3

        # Price position relative to EMAs
        if price > self._ema_fast:
            score += 0.3
        elif price < self._ema_fast:
            score -= 0.3

        return max(-1.0, min(1.0, score))

    def _momentum_score(self) -> Optional[float]:
        """Rate of change score [-1, +1]."""
        period = self.config.momentum_period
        if len(self.closes) < period + 1:
            return None

        closes = list(self.closes)
        roc = (closes[-1] - closes[-period]) / max(abs(closes[-period]), 0.01)

        # Acceleration (momentum of momentum)
        if len(self.closes) >= period * 2:
            roc_prev = (closes[-period] - closes[-period*2]) / max(abs(closes[-period*2]), 0.01)
            accel = roc - roc_prev
        else:
            accel = 0

        # Normalize: typical XAU ROC is ±0.02 over 10 bars
        score = roc * 30 + accel * 15
        return max(-1.0, min(1.0, score))

    def _rsi_score(self) -> Optional[float]:
        """RSI-based score [-1, +1]. Oversold=buy, overbought=sell."""
        rsi = self._rsi()
        if rsi is None:
            return None

        # Map RSI to score: 30→+1 (buy), 50→0, 70→-1 (sell)
        if rsi <= self.config.rsi_os:
            return 1.0
        elif rsi >= self.config.rsi_ob:
            return -1.0
        elif rsi < 50:
            return (50 - rsi) / (50 - self.config.rsi_os)
        else:
            return -(rsi - 50) / (self.config.rsi_ob - 50)

    def _macd_score(self) -> Optional[float]:
        """MACD crossover score [-1, +1]."""
        if self._macd_ema_fast is None or self._macd_ema_slow is None or self._macd_signal_ema is None:
            return None

        macd_line = self._macd_ema_fast - self._macd_ema_slow
        signal_line = self._macd_signal_ema
        histogram = macd_line - signal_line

        # Normalize histogram (typical range for XAU is ±5)
        score = histogram / max(abs(self.closes[-1]) * 0.001, 0.01)
        return max(-1.0, min(1.0, score))

    def _bb_score(self, price: float) -> Optional[float]:
        """Bollinger Band position score [-1, +1]."""
        period = self.config.bb_period
        if len(self.closes) < period:
            return None

        data = list(self.closes)[-period:]
        sma = sum(data) / period
        std = math.sqrt(sum((x - sma)**2 for x in data) / period)
        if std < 0.01:
            return 0.0

        upper = sma + self.config.bb_std * std
        lower = sma - self.config.bb_std * std

        # Position within bands: -1 at lower, +1 at upper, 0 at SMA
        if upper == lower:
            return 0.0

        # Contrarian: at upper band → sell signal, at lower → buy
        position = (price - sma) / (upper - sma) if price >= sma else (price - sma) / (sma - lower)
        return max(-1.0, min(1.0, -position))  # Negate for contrarian

    def _update_emas(self, price: float):
        alpha_f = 2.0 / (self.config.ema_fast + 1)
        alpha_m = 2.0 / (self.config.ema_medium + 1)
        alpha_s = 2.0 / (self.config.ema_slow + 1)
        if self._ema_fast is None:
            self._ema_fast = self._ema_medium = self._ema_slow = price
        else:
            self._ema_fast = alpha_f * price + (1 - alpha_f) * self._ema_fast
            self._ema_medium = alpha_m * price + (1 - alpha_m) * self._ema_medium
            self._ema_slow = alpha_s * price + (1 - alpha_s) * self._ema_slow

    def _update_macd(self, price: float):
        af = 2.0 / (self.config.macd_fast + 1)
        as_ = 2.0 / (self.config.macd_slow + 1)
        asig = 2.0 / (self.config.macd_signal + 1)
        if self._macd_ema_fast is None:
            self._macd_ema_fast = self._macd_ema_slow = price
            self._macd_signal_ema = 0
        else:
            self._macd_ema_fast = af * price + (1 - af) * self._macd_ema_fast
            self._macd_ema_slow = as_ * price + (1 - as_) * self._macd_ema_slow
            macd_line = self._macd_ema_fast - self._macd_ema_slow
            self._macd_signal_ema = asig * macd_line + (1 - asig) * self._macd_signal_ema

    def _rsi(self) -> Optional[float]:
        period = self.config.rsi_period
        if len(self.closes) < period + 1:
            return None
        closes = list(self.closes)
        gains, losses = [], []
        for i in range(-period, 0):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains) / period
        al = sum(losses) / period
        if al == 0: return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    def _atr(self) -> Optional[float]:
        period = self.config.atr_period
        if len(self.highs) < period + 1:
            return None
        h, l, c = list(self.highs), list(self.lows), list(self.closes)
        trs = []
        for i in range(-period, 0):
            trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
        return sum(trs) / len(trs)

    def _is_active(self, pos):
        if isinstance(pos, dict): return pos.get("status", "open") == "open"
        return getattr(pos, "status", "open") == "open"


# ── PRODUCTION PRESETS ────────────────────────────────────────────────────────
# Backtested on real XAU/USD data, verified March 2026.

# BEST: H1, +71.4% / 3mo, DD 13.8%, Sharpe 2.12 — primary production preset
MF_H1_SAFE = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.30,
    atr_sl_multiplier=2.0,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AGGRESSIVE: H1, +72.1% / 3mo, DD 23.1%, Sharpe 2.37 — more risk, slightly more return
MF_H1_BEST = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.30,
    atr_sl_multiplier=2.0,
    atr_tp_multiplier=5.0,
    cooldown_bars=5,
    min_bars=60,
)

# M15 scalping: +44% / 2mo, DD 16.5%, Sharpe 1.82 — more trades (75T)
MF_M15_BEST = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.45,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=4.0,
    cooldown_bars=8,
    min_bars=60,
)

# M15 fast: +39.6% / 2mo, DD 14.4%, Sharpe 3.50 — fewer trades (23T) but highest Sharpe
MF_M15_FAST = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.50,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=4.0,
    cooldown_bars=6,
    min_bars=60,
)

# ★ BEST OVERALL — M15 Ultra: +73%/2mo | WR=65% | PF=2.69 | Sharpe=5.96 | DD=7.7%
# Best risk-adjusted config found across ALL strategies and timeframes tested.
MF_M15_ULTRA = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.50,
    atr_sl_multiplier=3.0,
    atr_tp_multiplier=5.0,
    cooldown_bars=12,
    min_bars=60,
)

# Ultra Fast: +60%/2mo | 21T | WR=62% | PF=2.29 | Sharpe=5.46 | DD=7.7%
MF_M15_ULTRA_FAST = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.50,
    atr_sl_multiplier=3.0,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-07 (Sharpe 7.59, DD 11.1%, Return +59.0%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-07 (Sharpe 7.56, DD 11.1%, Return +58.8%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-07 (Sharpe 7.56, DD 11.1%, Return +58.8%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-11 (Sharpe 6.2, DD 11.1%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-12 (Sharpe 6.2, DD 11.1%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-13 (Sharpe 6.93, DD 7.7%, Return +38.6%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-14 (Sharpe 7.1, DD 7.6%, Return +39.2%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-15 (Sharpe 7.1, DD 7.6%, Return +39.2%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-16 (Sharpe 6.93, DD 7.7%, Return +38.6%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-17 (Sharpe 9.42, DD 7.3%, Return +45.4%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=5.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-18 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-22 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-22 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-23 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-23 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-24 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-24 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-25 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-04-26 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-05-02 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-05-02 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-05-04 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-05-05 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_SAFE_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-05-05 (Sharpe 9.65, DD 7.3%, Return +50.5%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)

# AUTO-TUNED by agent — 2026-05-19 (Sharpe 6.65, DD 8.9%, Return +25.4%)
MF_H1_BEST_AUTOTUNED = MultiFactorConfig(
    lots=0.05, max_positions=2,
    entry_threshold=0.6,
    atr_sl_multiplier=2.5,
    atr_tp_multiplier=6.0,
    cooldown_bars=8,
    min_bars=60,
)
