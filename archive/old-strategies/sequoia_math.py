"""
sequoia_math.py — Pure pandas vectorized screening & ranking functions.

Adapted from Sequoia-X (sngyai/Sequoia-X) quantitative screening strategies
for A-Share markets. Refactored and calibrated for:
  - IDX (Indonesia Stock Exchange) — ARA/ARB limits
  - Crypto (Altcoin vs BTC relative momentum)
  - XAUUSD / Komoditas (trend filter)

All functions consume a pandas DataFrame with OHLCV columns:
    ['open', 'high', 'low', 'close', 'volume']
and may accept an optional 'turnover' column (amount / Rp / USD).

CONTRACT:
  - Vectorized only — no iterrows, no loops over rows.
  - No database, no API calls, no side effects.
  - Input df is NEVER mutated (new Series returned).
  - NaN/insufficient data → gracefully returns False array.

CRITICAL SAFETY:
  This module is STRICTLY ADDITIVE. It does NOT touch, import, or
  modify any existing SMC/SnR sniper, Multi-Account Bridge, or
  Payment Webhook code.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Optional, Literal


# ─────────────────────────────────────────────
# IDX ARA/ARB Configuration
# ─────────────────────────────────────────────
# Bursa Efek Indonesia (IDX) daily price limits:
#   Papan Utama (Main Board):   ARA = +35%, ARB = -7%
#   Papan Pengembangan (Dev):   ARA = +35%, ARB = -10%
#   Papan Akselerasi (Ace):     ARA = +30%, ARB = -15%
#
# Default below uses Main Board (35%/7%).
# Pass custom (up_pct, down_pct) via kwargs to override.

IDX_LIMITS: dict[str, tuple[float, float]] = {
    "main":     (0.35, 0.07),
    "dev":      (0.35, 0.10),
    "ace":      (0.30, 0.15),
}

# A-Share China original: (0.10, 0.10) — symmetric 10%
# Crypto (no daily limit): pass (None, None)


# ═════════════════════════════════════════════
# SECTION 1 — TURTLE BREAKOUT
# ═════════════════════════════════════════════

def turtle_breakout(
    df: pd.DataFrame,
    lookback: int = 20,
    turnover_min: Optional[float] = None,
    require_bullish: bool = True,
) -> pd.Series:
    """Turtle Trading breakout — 20-day high + volume/liquidity filters.

    Original (Sequoia-X, A-Share):
      1. Close > max(high[-lookback-1:-1])  — shift(1) then rolling
      2. turnover > 100_000_000              — liquidity
      3. close > open AND close > prev_close — bullish candle

    Adapted: all params exposed. Set turnover_min=None to skip
    turnover filter (useful for crypto where "turnover" has different
    semantics, or use volume median multiplier instead).

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: 'open', 'high', 'low', 'close', 'volume'.
        May also contain 'turnover' (float, in local currency).
    lookback : int
        Lookback window for the rolling high (default 20).
    turnover_min : float | None
        Minimum turnover/amount to filter liquidity. None = skip filter.
    require_bullish : bool
        If True, require close > open AND close > prev_close.

    Returns
    -------
    pd.Series
        Boolean mask aligned to df.index. True where breakout fires.
        NaN rows (insufficient data) → False.
    """
    result = pd.Series(False, index=df.index)

    if len(df) < lookback + 2:
        return result

    # 1) Rolling high exclude-0 (shift(1) so current bar not in window)
    high_20 = df["high"].shift(1).rolling(lookback).max()

    # Base breakout: today's close exceeds the lookback high
    breakout = df["close"] > high_20

    # 2) Liquidity filter (optional)
    if turnover_min is not None and "turnover" in df.columns:
        liquid = df["turnover"] > turnover_min
    else:
        liquid = True

    # 3) Bullish candle filter (optional)
    if require_bullish:
        # Real body bullish + close above prev close (avoid fake-out)
        bullish = (df["close"] > df["open"]) & (df["close"] > df["close"].shift(1))
    else:
        bullish = True

    result = breakout & liquid & bullish
    return result.fillna(False).astype(bool)


def turtle_signal_strength(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.Series:
    """Continuous breakout strength score (0.0 – 1.0).

    Useful as a trend filter (not just binary):
      - How far is close above the lookback high, normalised?
      - How much did volume spike vs its own median?
    Higher = stronger breakout conviction.

    Returns
    -------
    pd.Series
        Float [0, 1] where 0 = no breakout, 1 = extreme breakout.
    """
    if len(df) < lookback + 2:
        return pd.Series(0.0, index=df.index)

    high_roll = df["high"].shift(1).rolling(lookback).max()
    price_ratio = (df["close"] - high_roll) / (high_roll + 1e-10)

    vol_median = df["volume"].rolling(lookback * 2, min_periods=lookback).median()
    vol_ratio = df["volume"] / (vol_median + 1e-10)

    # Clamp and combine
    price_score = price_ratio.clip(0, 0.15) / 0.15       # 15% above = max
    vol_score   = vol_ratio.clip(0, 5.0) / 5.0            # 5x volume = max
    strength    = (price_score * 0.6 + vol_score * 0.4)

    return strength.clip(0, 1).fillna(0.0)


# ═════════════════════════════════════════════
# SECTION 2 — HIGH TIGHT FLAG
# ═════════════════════════════════════════════

def high_tight_flag(
    df: pd.DataFrame,
    momentum_window: int = 40,
    momentum_ratio: float = 1.6,
    consolidation_window: int = 10,
    consolidation_amplitude: float = 1.15,
    hold_ratio: float = 0.80,
    volume_shrink_factor: float = 0.60,
) -> pd.Series:
    """High Tight Flag — strong momentum followed by tight consolidation.

    Original (Sequoia-X, William O'Neil):
      1. Momentum: high_40 / low_40 > 1.6   (60%+ run)
      2. Consolidation: high_10 / low_10 < 1.15  (< 15% narrow)
      3. High level hold: low_10 >= high_40 * 0.80
      4. Volume shrink: volume < ma20_vol * 0.60

    Parameters
    ----------
    momentum_window : int   — lookback for the impulsive run (default 40)
    consolidation_window    — recent window for tight flag (default 10)
    momentum_ratio          — min high/low ratio to qualify as run (1.6 = 60%)
    consolidation_amplitude — max high/low ratio for tight flag (1.15 = 15%)
    hold_ratio              — min flag low vs run high (0.80 = 80%)
    volume_shrink_factor    — volume multiplier vs 20d avg (0.60 = 40% shrink)

    Returns
    -------
    pd.Series — boolean mask.
    """
    result = pd.Series(False, index=df.index)

    n = max(momentum_window, consolidation_window + 20)
    if len(df) < n:
        return result

    tail_m = df.tail(momentum_window)
    tail_c = df.tail(consolidation_window)

    high_m = tail_m["high"].max()
    low_m  = tail_m["low"].min()
    high_c = tail_c["high"].max()
    low_c  = tail_c["low"].min()

    if low_m == 0 or low_c == 0:
        return result

    momentum       = (high_m / low_m) > momentum_ratio
    consolidation  = (high_c / low_c) < consolidation_amplitude
    hold_level     = low_c >= (high_m * hold_ratio)

    # Volume: avg of last 20 bars EXCLUDING current
    vol_ma = df["volume"].iloc[-21:-1].mean() if len(df) >= 21 else df["volume"].mean()
    shrink = df["volume"].iloc[-1] < (vol_ma * volume_shrink_factor)

    if momentum and consolidation and hold_level and shrink:
        result.iloc[-1] = True

    return result


# ═════════════════════════════════════════════
# SECTION 3 — RPS (RELATIVE PRICE STRENGTH)
# ═════════════════════════════════════════════

def rps_score(
    df_multi: pd.DataFrame,
    period: int = 120,
    price_col: str = "close",
) -> pd.Series:
    """Relative Price Strength (RPS) percentile — William O'Neil.

    Ranks the return of each symbol over `period` bars against all
    other symbols in the universe on the latest date.

    Parameters
    ----------
    df_multi : pd.DataFrame
        Multi-symbol daily data. MUST have columns 'symbol' (str) and
        `price_col` (float), sorted by (symbol, date).
    period : int
        Lookback for the return calculation (default 120 ≈ 6 months).
    price_col : str
        Column to use for return calc ('close' or 'adjusted_close').

    Returns
    -------
    pd.Series
        RPS percentile [0, 100] for each symbol on the latest date.
        Index = symbol (str). Empty if insufficient data.
    """
    required = {"symbol", "date", price_col}
    if not required.issubset(df_multi.columns):
        raise ValueError(f"df_multi must contain columns: {required}")

    df = df_multi.copy()
    df = df.sort_values(["symbol", "date"])

    # Shift return within each symbol group
    df["ret"] = df.groupby("symbol")[price_col].transform(
        lambda s: s.pct_change(period)
    )

    # Latest date only
    latest = df["date"].max()
    latest_df = df[df["date"] == latest].dropna(subset=["ret"]).copy()

    if latest_df.empty:
        return pd.Series(dtype=float)

    # Cross-sectional rank → percentile
    latest_df["rps"] = latest_df["ret"].rank(pct=True) * 100.0
    return latest_df.set_index("symbol")["rps"]


def rps_breakout(
    df_multi: pd.DataFrame,
    period: int = 120,
    rps_threshold: float = 90.0,
    near_high_pct: float = 0.90,
) -> pd.Series:
    """RPS Breakout — top percentile + near rolling high.

    Combines RPS (top `rps_threshold` %tile) with price within
    `near_high_pct` of the `period`-day rolling high.

    Parameters
    ----------
    rps_threshold : float — minimum RPS percentile (default 90 = top 10%)
    near_high_pct : float — minimum fraction of rolling high (0.90 = 90%)

    Returns
    -------
    pd.Series — boolean per symbol.
    """
    required = {"symbol", "date", "high", "close"}
    if not required.issubset(df_multi.columns):
        raise ValueError(f"df_multi must contain: {required}")

    df = df_multi.copy()
    df = df.sort_values(["symbol", "date"])

    # Rolling high per symbol
    df["roll_high"] = df.groupby("symbol")["high"].transform(
        lambda s: s.rolling(period, min_periods=max(period // 2, 20)).max()
    )

    # RPS percentile
    rps_series = rps_score(df, period=period)
    if rps_series.empty:
        return pd.Series(dtype=bool)

    # Merge roll_high for latest date
    latest = df["date"].max()
    latest_roll = df[df["date"] == latest][["symbol", "roll_high", "close"]].drop_duplicates("symbol")
    latest_roll = latest_roll.set_index("symbol")

    mask = (rps_series >= rps_threshold)
    joined = pd.concat({"rps": rps_series, "roll_high": latest_roll["roll_high"],
                        "close": latest_roll["close"]}, axis=1)

    near_high = joined["close"] >= (joined["roll_high"] * near_high_pct)
    result = mask & near_high
    return result.fillna(False).astype(bool)


# ═════════════════════════════════════════════
# SECTION 4 — LIMIT UP SHAKEOUT (IDX CALIBRATED)
# ═════════════════════════════════════════════

def limit_up_shakeout(
    df: pd.DataFrame,
    up_limit_pct: float = 0.35,
    down_limit_pct: float = 0.07,
    volume_surge: float = 2.0,
) -> pd.Series:
    """Limit-up shakeout — yesterday limit-up, today bearish shakeout.

    Original (Sequoia-X, A-Share):
      - Yesterday close >= prev_close * 1.095  (≈+10% with rounding)
      - Today close < open                      (bearish candle)
      - Today volume > prev_volume * 2.0         (volume surge)
      - Today low >= yesterday_close             (support holds)

    IDX Calibration:
      - up_limit_pct = 0.35  (ARA = Auto Rejection Atas +35%)
      - down_limit_pct = 0.07 (ARB = Auto Rejection Bawah -7%)
      The limit_up condition uses (1 + up_limit_pct - epsilon)
      so that close >= prev_close * (1 + up_limit_pct - 0.005).

    Parameters
    ----------
    up_limit_pct : float
        Daily upward price limit as decimal (0.35 = +35% for IDX main).
    down_limit_pct : float
        Daily downward limit (0.07 = -7% for ARB).
    volume_surge : float
        Multiplier for today volume vs yesterday (default 2.0 = 2x).

    Returns
    -------
    pd.Series — boolean mask aligned to df.index.
    """
    result = pd.Series(False, index=df.index)
    if len(df) < 3:
        return result

    prev2 = df.iloc[-3]   # day before yesterday
    prev1 = df.iloc[-2]   # yesterday
    today = df.iloc[-1]   # today

    # Yesterday hit limit up (ARA equivalent)
    limit_up_yest = prev1["close"] >= prev2["close"] * (1 + up_limit_pct - 0.005)
    # Today bearish
    bearish_today = today["close"] < today["open"]
    # Volume surge
    vol_surge = today["volume"] > prev1["volume"] * volume_surge
    # Support held: low >= yesterday close
    support_hold = today["low"] >= prev1["close"]

    if limit_up_yest and bearish_today and vol_surge and support_hold:
        result.iloc[-1] = True

    return result


# ═════════════════════════════════════════════
# SECTION 5 — CRYPTO ALTCOIN SCREENER (RPS)
# ═════════════════════════════════════════════

def altcoin_rps_screener(
    df_multi: pd.DataFrame,
    btc_symbol: str = "BTCUSDT",
    period: int = 30,
) -> pd.DataFrame:
    """Rank altcoins by relative momentum against BTC.

    Computes risk-adjusted relative return:
      1. Return of each altcoin over `period`
      2. Return of BTC over same period
      3. Relative return = alt_ret - btc_ret  (excess return)
      4. Also compute Sharpe-like ratio: ret / vol

    Parameters
    ----------
    df_multi : pd.DataFrame
        Columns: symbol, date, close. MUST include `btc_symbol`.
    btc_symbol : str  — symbol identifying BTC in the dataset.
    period : int       — lookback for return calc (default 30 ≈ 1 month).

    Returns
    -------
    pd.DataFrame
        Columns: symbol, ret, btc_ret, excess_ret, volatility, sharpe, rps
        Sorted by excess_ret descending. Empty if BTC not in data.
    """
    required = {"symbol", "date", "close"}
    if not required.issubset(df_multi.columns):
        raise ValueError(f"df_multi must contain: {required}")

    df = df_multi.copy()
    df = df.sort_values(["symbol", "date"])

    # Per-symbol return
    df["ret"] = df.groupby("symbol")["close"].transform(
        lambda s: s.pct_change(period)
    )
    # Volatility over period
    df["daily_ret"] = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    df["volatility"] = df.groupby("symbol")["daily_ret"].transform(
        lambda s: s.rolling(period, min_periods=period // 2).std()
    )

    latest = df["date"].max()
    latest_df = df[df["date"] == latest].copy()

    if latest_df.empty:
        return pd.DataFrame()

    btc_row = latest_df[latest_df["symbol"] == btc_symbol]
    if btc_row.empty:
        return pd.DataFrame()
    btc_ret = btc_row["ret"].iloc[0]

    # Compute
    altcoins = latest_df[latest_df["symbol"] != btc_symbol].copy()
    altcoins["btc_ret"] = btc_ret
    altcoins["excess_ret"] = altcoins["ret"] - btc_ret
    altcoins["sharpe"] = altcoins["ret"] / (altcoins["volatility"] + 1e-10)
    altcoins["rps"] = altcoins["excess_ret"].rank(pct=True) * 100

    result = altcoins[
        ["symbol", "ret", "btc_ret", "excess_ret", "volatility", "sharpe", "rps"]
    ].sort_values("excess_ret", ascending=False)

    return result.reset_index(drop=True)


# ═════════════════════════════════════════════
# SECTION 6 — XAUUSD TREND FILTER (TURTLE D1/H4)
# ═════════════════════════════════════════════

def turtle_trend_filter(
    df: pd.DataFrame,
    lookback: int = 20,
    smoothing: int = 3,
) -> tuple[pd.Series, pd.Series, float]:
    """Trend filter for XAUUSD / komoditas using Turtle breakout logic.

    Returns three values for SMC/SnR context:
      1. is_bullish — bool Series, True when in uptrend
      2. strength   — float Series [0, 1], trend conviction
      3. tf_direction — -1 (bear), 0 (neutral), +1 (bull)

    Logic:
      - Bullish if close > rolling(lookback).max() within last `smoothing` bars
      - Bearish if close < rolling(lookback).min() within last `smoothing` bars
      - Otherwise neutral

    Unlike the binary turtle_breakout(), this function looks BACK
    over `smoothing` periods for confirmation, making it suitable
    as a D1/H4 macro trend filter before executing SnR entries.

    Parameters
    ----------
    lookback : int   — rolling window for high/low (default 20 ≈ 1 month D1)
    smoothing : int  — confirmation bars (default 3)

    Returns
    -------
    is_bullish : pd.Series — True if confirmed uptrend
    strength   : pd.Series — 0..1 trend conviction score
    tf_direction : int     — -1 / 0 / +1 for quick reference
    """
    n = lookback + smoothing
    if len(df) < n:
        neutral = pd.Series(False, index=df.index)
        return neutral, pd.Series(0.0, index=df.index), 0

    # Smoothed high/low: rolling min/max of the HIGH/LOW columns
    high_roll = df["high"].shift(1).rolling(lookback).max()
    low_roll  = df["low"].shift(1).rolling(lookback).min()

    # Check if close broke above high_roll within last `smoothing` bars
    recent = df.tail(smoothing)
    bullish_condition = (recent["close"] > high_roll.tail(smoothing)).any()
    bearish_condition = (recent["close"] < low_roll.tail(smoothing)).any()

    is_bullish = pd.Series(False, index=df.index)
    is_bearish = pd.Series(False, index=df.index)

    if bullish_condition and not bearish_condition:
        is_bullish.iloc[-1] = True
        tf_direction = 1
    elif bearish_condition and not bullish_condition:
        is_bearish.iloc[-1] = True
        tf_direction = -1
    else:
        tf_direction = 0

    # Strength: how far recent closes are above/below the rolling midpoint
    mid = (high_roll + low_roll) / 2
    recent_close = df["close"].iloc[-1]
    dist_from_mid = (recent_close - mid.iloc[-1]) / (mid.iloc[-1] + 1e-10)
    strength_val = min(abs(dist_from_mid) * 10, 1.0)

    strength = pd.Series(strength_val, index=df.index).fillna(0.0)

    return is_bullish, strength, tf_direction


# ═════════════════════════════════════════════
# SECTION 7 — UTILITY HELPERS
# ═════════════════════════════════════════════

def validate_ohlcv(df: pd.DataFrame, require_turnover: bool = False) -> bool:
    """Validate that DataFrame has the minimum OHLCV columns."""
    base = {"open", "high", "low", "close", "volume"}
    ok = base.issubset(df.columns)
    if require_turnover:
        ok = ok and "turnover" in df.columns
    return ok


def ma_volume_breakout(
    df: pd.DataFrame,
    ma_period: int = 20,
    volume_mult: float = 1.5,
) -> pd.Series:
    """Moving Average volume breakout — close above MA + volume spike.

    From Sequoia-X MaVolume strategy:
      - Close > MA(ma_period)
      - Volume > MA(volume) * volume_mult

    Returns
    -------
    pd.Series — boolean mask.
    """
    if len(df) < ma_period + 1:
        return pd.Series(False, index=df.index)

    ma_close = df["close"].rolling(ma_period).mean()
    ma_vol   = df["volume"].rolling(ma_period).mean()

    result = (
        (df["close"] > ma_close)
        & (df["volume"] > ma_vol * volume_mult)
    )
    return result.fillna(False).astype(bool)
