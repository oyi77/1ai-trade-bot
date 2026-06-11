"""
Technical analysis indicator constants — centralized magic numbers.

All thresholds, periods, and confidence factors defined here to prevent
magic numbers scattered throughout the codebase.
"""

# ─────────────────────────────────────────────────────────────────────
# RSI (Relative Strength Index)
# ─────────────────────────────────────────────────────────────────────

RSI_PERIOD: int = 14
RSI_OVERBOUGHT: float = 70.0
RSI_OVERSOLD: float = 30.0
RSI_EXTREME_OVERBOUGHT: float = 80.0
RSI_EXTREME_OVERSOLD: float = 20.0

# ─────────────────────────────────────────────────────────────────────
# MACD (Moving Average Convergence Divergence)
# ─────────────────────────────────────────────────────────────────────

MACD_FAST_PERIOD: int = 12
MACD_SLOW_PERIOD: int = 26
MACD_SIGNAL_PERIOD: int = 9

# ─────────────────────────────────────────────────────────────────────
# EMA (Exponential Moving Average)
# ─────────────────────────────────────────────────────────────────────

EMA_FAST_PERIOD: int = 9
EMA_MEDIUM_PERIOD: int = 21
EMA_SLOW_PERIOD: int = 50
EMA_LONG_PERIOD: int = 200

# ─────────────────────────────────────────────────────────────────────
# Bollinger Bands
# ─────────────────────────────────────────────────────────────────────

BB_PERIOD: int = 20
BB_STD_DEV: float = 2.0
BB_MA_TYPE: str = "SMA"  # Simple Moving Average

# ─────────────────────────────────────────────────────────────────────
# ATR (Average True Range) — Volatility Measure
# ─────────────────────────────────────────────────────────────────────

ATR_PERIOD: int = 14
ATR_MULTIPLIER_SL: float = 1.5  # Stop Loss = ATR * 1.5
ATR_MULTIPLIER_TP: float = 3.0  # Take Profit = ATR * 3.0

# ─────────────────────────────────────────────────────────────────────
# Stochastic Oscillator
# ─────────────────────────────────────────────────────────────────────

STOCHASTIC_K_PERIOD: int = 14
STOCHASTIC_D_PERIOD: int = 3
STOCHASTIC_OVERBOUGHT: float = 80.0
STOCHASTIC_OVERSOLD: float = 20.0

# ─────────────────────────────────────────────────────────────────────
# Ichimoku Cloud
# ─────────────────────────────────────────────────────────────────────

ICHIMOKU_CONVERSION_PERIOD: int = 9
ICHIMOKU_BASE_PERIOD: int = 26
ICHIMOKU_SPAN_PERIOD: int = 52
ICHIMOKU_DISPLACEMENT: int = 26

# ─────────────────────────────────────────────────────────────────────
# SuperTrend
# ─────────────────────────────────────────────────────────────────────

SUPERTREND_PERIOD: int = 10
SUPERTREND_MULTIPLIER: float = 3.0

# ─────────────────────────────────────────────────────────────────────
# VWAP (Volume Weighted Average Price)
# ─────────────────────────────────────────────────────────────────────

VWAP_LOOKBACK: int = 50  # Bars to use for VWAP calculation

# ─────────────────────────────────────────────────────────────────────
# Fibonacci Levels (for support/resistance)
# ─────────────────────────────────────────────────────────────────────

FIBONACCI_LEVELS: dict[str, float] = {
    "level_0": 0.0,
    "level_236": 0.236,
    "level_382": 0.382,
    "level_500": 0.500,
    "level_618": 0.618,
    "level_786": 0.786,
    "level_1": 1.0,
}

# ─────────────────────────────────────────────────────────────────────
# Signal Confidence Scoring
# ─────────────────────────────────────────────────────────────────────

# Minimum number of indicators that must agree for a strong signal
CONFLUENCE_MIN_AGREEMENT: int = 4

# Confidence thresholds by signal grade
CONFIDENCE_STRONG: float = 0.70
CONFIDENCE_MODERATE: float = 0.50
CONFIDENCE_WEAK: float = 0.30

# Weight per indicator when calculating confluence score
INDICATOR_WEIGHT: float = 0.10  # 1.0 / 10 indicators = 0.10 per indicator

# ─────────────────────────────────────────────────────────────────────
# Data Fetching
# ─────────────────────────────────────────────────────────────────────

# Minimum bars required for technical analysis
MIN_BARS_FOR_TA: int = 100

# OHLCV fetch timeout (seconds)
OHLCV_FETCH_TIMEOUT: int = 30

# Max historical candles to request at once
MAX_CANDLES_PER_REQUEST: int = 500

# ─────────────────────────────────────────────────────────────────────
# Entry/Exit Level Calculation
# ─────────────────────────────────────────────────────────────────────

# Support/Resistance level tolerance (percentage)
SUPPORT_RESISTANCE_TOLERANCE: float = 0.02  # 2%

# Swing high/low period for support/resistance detection
SWING_PERIOD: int = 5
