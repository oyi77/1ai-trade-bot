"""
Smart Money / Bandar Accumulation Score Engine.

Detects institutional accumulation and distribution patterns in IDX stocks
using volume-price analysis and statistical scoring.

Methodology adapted from:
    dananghilalkurniawan/smart-money-accumulation-idx
    (Bandar Accumulation Score — composite of foreign flow, bid-offer
    imbalance, volume spikes, and price returns)

Metrics (all normalized 0-100):
    1. Volume Surge — 5d vs 20d avg volume ratio (35 pts)
    2. Close Location — where price closes vs daily range (20 pts)
    3. Volume-Price Trend — accumulation pattern detection (25 pts)
    4. Momentum — 7-day price return (10 pts)
    5. Ease of Movement — price change per unit volume (10 pts)

Scoring tiers:
    80-100  🐳 Strong Accumulation
    60-79   📈 Moderate Accumulation
    40-59   ➡️  Neutral
    20-39   📉 Moderate Distribution
    0-19    🚨 Strong Distribution
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]

from tradebot.signals.idx_encyclopedia import get_name, is_idx_stock, resolve_code

LOG = logging.getLogger("tradebot.signals.idx_smart_money")


@dataclass
class BandarResult:
    code: str
    name: str = ""
    bandar_score: int = 0
    interpretation: str = ""
    signals: list[str] = field(default_factory=list)
    volume_surge_ratio: float = 0.0
    close_location: float = 0.0
    momentum_7d: float = 0.0
    volume_price_trend: float = 0.0
    ease_of_movement: float = 0.0
    days_analyzed: int = 30
    avg_volume: float = 0.0
    latest_volume: float = 0.0
    latest_price: float = 0.0


class SmartMoneyEngine:
    """Detect bandar accumulation/distribution in IDX stocks."""

    def __init__(self, lookback_days: int = 30) -> None:
        self.lookback = lookback_days

    async def analyze(self, symbol: str) -> BandarResult | None:
        code = resolve_code(symbol)
        if not is_idx_stock(code):
            return None

        yahoo_symbol = f"{code}.JK"
        result = BandarResult(code=code, name=get_name(code))

        df = await self._fetch_data(yahoo_symbol)
        if df is None or len(df) < 20:
            LOG.warning("Insufficient data for %s", code)
            return result

        result.latest_price = float(df["Close"].iloc[-1])  # type: ignore[index]
        result.latest_volume = float(df["Volume"].iloc[-1])  # type: ignore[index]
        result.avg_volume = float(df["Volume"].rolling(20).mean().iloc[-1])  # type: ignore[index]
        result.days_analyzed = len(df)

        # Compute individual metrics
        result.volume_surge_ratio = _volume_surge(df)
        result.close_location = _close_location(df)
        result.momentum_7d = _momentum_7d(df)
        result.volume_price_trend = _volume_price_trend(df)
        result.ease_of_movement = _ease_of_movement(df)

        # Composite Bandar Score (weighted)
        result.bandar_score = int(
            result.volume_surge_ratio * 0.30
            + result.close_location * 0.15
            + result.volume_price_trend * 0.30
            + result.momentum_7d * 0.15
            + result.ease_of_movement * 0.10
        )
        result.bandar_score = min(100, max(0, result.bandar_score))

        # Interpretation
        result.interpretation = _interpret(result.bandar_score)

        # Signal detection
        result.signals = _detect_signals(result, df)

        return result

    async def _fetch_data(self, symbol: str) -> pd.DataFrame | None:
        try:
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            df = await asyncio.to_thread(
                lambda: ticker.history(period=f"{self.lookback}d")
            )
            if df.empty:
                return None
            return df
        except Exception as exc:
            LOG.warning("Yahoo fetch failed for %s: %s", symbol, exc)
            return None


# ── Individual Metrics (0-100) ──────────────────────────────────────


def _volume_surge(df: pd.DataFrame) -> float:
    """Volume surge — 5d avg vs 20d avg.

    >1.3 = strong accumulation interest, <0.7 = declining interest.
    """
    vol = df["Volume"]
    if vol.sum() == 0:
        return 50.0
    avg5 = float(vol.tail(5).mean())
    avg20 = float(vol.tail(20).mean())
    if avg20 == 0:
        return 50.0
    ratio = avg5 / avg20
    # Map 0.5→30, 1.0→50, 1.5→75, 2.0→100
    return min(100.0, max(0.0, 50.0 + (ratio - 1.0) * 50.0))


def _close_location(df: pd.DataFrame) -> float:
    """Close Location — where did price close vs day's range?

    Close near high = buying pressure (accumulation).
    Close near low = selling pressure (distribution).
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    daily_range = high - low
    if daily_range.sum() == 0:
        return 50.0
    clv = (close - low) / daily_range.replace(0, np.nan)
    avg_clv = float(clv.tail(5).mean())
    if np.isnan(avg_clv):
        return 50.0
    return min(100.0, max(0.0, avg_clv * 100.0))


def _momentum_7d(df: pd.DataFrame) -> float:
    """7-day price momentum. 0% = 50, +5% = 100, -5% = 0."""
    close = df["Close"]
    if len(close) < 7:
        return 50.0
    c0, c7 = float(close.iloc[-1]), float(close.iloc[-7])  # type: ignore[index]
    if c7 == 0:
        return 50.0
    ret = (c0 / c7 - 1) * 100
    return min(100.0, max(0.0, 50.0 + ret * 10.0))


def _volume_price_trend(df: pd.DataFrame) -> float:
    """Volume-Price Trend — accumulation = price UP on HIGH volume.

    Score based on: what % of up-days had above-median volume?
    High % = classic accumulation pattern.
    """
    close = df["Close"]
    vol = df["Volume"]
    price_chg = close.pct_change().dropna()
    vol_slice = vol.iloc[1:]

    if len(price_chg) < 5:
        return 50.0

    median_vol = vol_slice.median()
    if median_vol == 0:
        return 50.0

    up_days = price_chg > 0
    up_on_high_vol = up_days & (vol_slice.values > median_vol)
    down_on_low_vol = (~up_days) & (vol_slice.values < median_vol)

    # Score: accumulation = up on high vol + down on low vol
    acc_signal = (up_on_high_vol.sum() + down_on_low_vol.sum()) / len(price_chg)
    return min(100.0, max(0.0, acc_signal * 100.0))


def _ease_of_movement(df: pd.DataFrame) -> float:
    """Ease of Movement — percentile rank within the lookback window.

    High EOM percentile = price moves easily (accumulation).
    Low EOM percentile = price struggles (distribution).
    """
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"].replace(0, np.nan)
    mid_pt = (high + low) / 2
    box_ratio = (vol / 1_000_000) / (high - low).replace(0, np.nan)
    eom_raw = mid_pt.diff() / box_ratio.replace(0, np.nan)

    valid = eom_raw.dropna()
    if len(valid) < 10:
        return 50.0

    latest = float(valid.iloc[-1])
    # Percentile rank within window
    rank = (valid < latest).sum() / len(valid)
    return min(100.0, max(0.0, rank * 100.0))


# ── Interpretation ──────────────────────────────────────────────────


def _interpret(score: int) -> str:
    if score >= 80:
        return "🐳 Strong Accumulation"
    if score >= 60:
        return "📈 Moderate Accumulation"
    if score >= 40:
        return "➡️  Neutral"
    if score >= 20:
        return "📉 Moderate Distribution"
    return "🚨 Strong Distribution"


def _detect_signals(result: BandarResult, df: pd.DataFrame) -> list[str]:
    signals: list[str] = []

    if result.volume_surge_ratio > 70:
        signals.append("volume_surge")
    if result.close_location > 65:
        signals.append("price_holding_high")
    elif result.close_location < 35:
        signals.append("price_holding_low")
    if result.volume_price_trend > 55:
        signals.append("accumulation_pattern")
    elif result.volume_price_trend < 45:
        signals.append("distribution_pattern")
    if result.momentum_7d > 70:
        signals.append("strong_momentum")
    elif result.momentum_7d < 30:
        signals.append("weak_momentum")
    if result.ease_of_movement > 65:
        signals.append("easy_accumulation")

    if not signals:
        signals.append("no_clear_signal")

    return signals


SIGNAL_LABELS: dict[str, str] = {
    "volume_surge": "Volume spike — strong institutional interest",
    "price_holding_high": "Closing near high — persistent buying pressure",
    "price_holding_low": "Closing near low — selling/distribution pressure",
    "accumulation_pattern": "Up on high volume — classic accumulation",
    "distribution_pattern": "Down on high volume — classic distribution",
    "strong_momentum": "Strong 7-day momentum — capital flowing in",
    "weak_momentum": "Weak 7-day momentum — capital rotating out",
    "easy_accumulation": "Price rises easily — low selling resistance",
    "no_clear_signal": "No clear bandar signal — wait for confirmation",
}
