"""
Anomaly Detection Engine for IDX Stocks.

Uses Isolation Forest to detect unusual price-volume patterns that may
indicate accumulation (bullish anomaly) or distribution (bearish anomaly).

Methodology adapted from:
    Vio-Shn/AnoPus-Anomaly-Detection-Energy-Sector-Stocks-Indonesia
    (Isolation Forest for anomaly detection + Buy/Hold/Sell signals)

Features extracted for anomaly detection:
    - Daily return (%)
    - Volume ratio (vs 20-day MA)
    - High-Low range (volatility)
    - Close location (buying/selling pressure)
    - Price acceleration (return change)

Anomaly classification:
    - Bullish: anomaly + positive return + volume surge = accumulation
    - Bearish: anomaly + negative return + volume surge = distribution
    - Neutral: anomaly without clear direction

Usage::

    from tradebot.signals.idx_anomaly import AnomalyEngine

    engine = AnomalyEngine()
    result = await engine.analyze("BBCA")
    # result.is_anomaly = True
    # result.anomaly_score = 0.82
    # result.signal = "BUY"
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from tradebot.signals.idx_encyclopedia import (
    all_stocks,
    get_name,
    get_sub_sector,
    is_idx_stock,
    resolve_code,
)

LOG = logging.getLogger("tradebot.signals.idx_anomaly")


@dataclass
class AnomalyResult:
    code: str
    name: str = ""
    is_anomaly: bool = False
    anomaly_score: float = 0.0
    anomaly_type: str = "none"
    signal: str = "HOLD"
    confidence: float = 0.0
    details: list[str] = field(default_factory=list)
    latest_price: float = 0.0
    daily_return: float = 0.0
    volume_surge: float = 0.0


class AnomalyEngine:
    """Isolation Forest-based anomaly detection for IDX stocks."""

    def __init__(self, lookback_days: int = 60, contamination: float = 0.05) -> None:
        self.lookback = lookback_days
        self.contamination = contamination

    async def analyze(self, symbol: str) -> AnomalyResult | None:
        code = resolve_code(symbol)
        if not is_idx_stock(code):
            return None

        yahoo_symbol = f"{code}.JK"
        result = AnomalyResult(code=code, name=get_name(code))

        df = await self._fetch_data(yahoo_symbol)
        if df is None or len(df) < 30:
            return result

        result.latest_price = float(df["Close"].iloc[-1])  # type: ignore[index]

        # Feature engineering
        features = _build_features(df)
        if features is None or len(features) < 20:
            return result

        # Train Isolation Forest on historical data (excluding last day)
        X = features.values
        scaler = RobustScaler()
        X_scaled = scaler.fit_transform(X)

        model = IsolationForest(
            contamination=self.contamination,
            random_state=42,
            n_estimators=100,
        )
        model.fit(X_scaled)

        # Predict anomaly on latest data point
        latest = X_scaled[-1:]

        # decision_function: higher = more normal, lower = more anomalous
        raw_score = float(model.decision_function(latest)[0])

        # Normalize to 0-1 where >0.5 = anomalous
        # decision_function typically ranges -0.5 to +0.5 for IsolationForest
        result.anomaly_score = max(0.0, min(1.0, 0.5 - raw_score))

        # Anomaly if score > 0.4 (more sensitive)
        result.is_anomaly = result.anomaly_score > 0.4

        # Daily context
        returns = df["Close"].pct_change()
        result.daily_return = float(returns.iloc[-1]) * 100 if not returns.empty else 0.0  # type: ignore[index]
        avg20_vol = float(df["Volume"].rolling(20).mean().iloc[-1])  # type: ignore[index]
        latest_vol = float(df["Volume"].iloc[-1])  # type: ignore[index]
        result.volume_surge = latest_vol / avg20_vol if avg20_vol > 0 else 1.0

        # Classify anomaly
        result.anomaly_type, result.signal = _classify_anomaly(result)

        # Details
        result.details = _build_details(result, df)
        result.confidence = min(0.95, result.anomaly_score + 0.1)

        return result

    async def scan_sector(self, sub_sector: str) -> list[AnomalyResult]:
        """Scan all stocks in a sub-sector for anomalies.

        Returns only stocks with detected anomalies, sorted by score.
        """
        stocks = all_stocks()
        targets = [
            code
            for code, info in stocks.items()
            if info.get("sub_sector") == sub_sector
        ]
        if not targets:
            return []

        results: list[AnomalyResult] = []
        for code in targets:
            r = await self.analyze(code)
            if r and r.is_anomaly:
                results.append(r)
            await asyncio.sleep(0.5)

        results.sort(key=lambda x: x.anomaly_score, reverse=True)
        return results

    async def _fetch_data(self, symbol: str) -> pd.DataFrame | None:
        try:
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            df = await asyncio.to_thread(
                lambda: ticker.history(period=f"{self.lookback}d")
            )
            if df.empty or len(df) < 20:
                return None
            return df
        except Exception as exc:
            LOG.warning("Fetch failed for %s: %s", symbol, exc)
            return None


# ── Feature Engineering ────────────────────────────────────────────


def _build_features(df: pd.DataFrame) -> pd.DataFrame | None:
    """Build features for anomaly detection."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    features = pd.DataFrame(index=df.index)

    # Daily return
    features["return"] = close.pct_change() * 100

    # Volume ratio vs 20-day MA
    vol_ma20 = vol.rolling(20).mean()
    features["vol_ratio"] = vol / vol_ma20.replace(0, np.nan)

    # High-Low range (normalized)
    features["hl_range"] = (high - low) / close

    # Close location (0-1)
    features["close_loc"] = (close - low) / (high - low).replace(0, np.nan)

    # Price acceleration (return change)
    features["acceleration"] = features["return"].diff()

    # Volume acceleration
    features["vol_accel"] = features["vol_ratio"].diff()

    return features.dropna()


# ── Anomaly Classification ──────────────────────────────────────────


def _classify_anomaly(result: AnomalyResult) -> tuple[str, str]:
    """Classify anomaly type and generate trading signal."""
    if not result.is_anomaly:
        return "none", "HOLD"

    ret = result.daily_return
    vol = result.volume_surge

    # Bullish anomaly: positive return + high volume
    if ret > 0.5 and vol > 1.2:
        return "bullish_accumulation", "BUY"

    # Bearish anomaly: negative return + high volume
    if ret < -0.5 and vol > 1.2:
        return "bearish_distribution", "SELL"

    # Volume anomaly without clear direction
    if vol > 1.5:
        return "volume_spike", "HOLD"

    # Price anomaly without volume confirmation
    if abs(ret) > 3:
        return "price_shock", "HOLD" if ret > 0 else "SELL"

    return "unusual_activity", "HOLD"


def _build_details(result: AnomalyResult, df: pd.DataFrame) -> list[str]:
    """Build human-readable detail strings."""
    details: list[str] = []

    if result.anomaly_score > 0.8:
        details.append(f"Strong anomaly detected (score: {result.anomaly_score:.0%})")
    elif result.is_anomaly:
        details.append(f"Anomaly detected (score: {result.anomaly_score:.0%})")

    if result.daily_return > 2:
        details.append(f"Price surged +{result.daily_return:.1f}% today")
    elif result.daily_return < -2:
        details.append(f"Price dropped {result.daily_return:.1f}% today")

    if result.volume_surge > 2.0:
        details.append(f"Volume {result.volume_surge:.1f}x normal — extreme activity")
    elif result.volume_surge > 1.5:
        details.append(f"Volume {result.volume_surge:.1f}x normal — elevated activity")

    anomaly_labels = {
        "bullish_accumulation": "Bullish — possible accumulation before breakout",
        "bearish_distribution": "Bearish — possible distribution before decline",
        "volume_spike": "Volume spike — watch for direction confirmation",
        "price_shock": "Price shock — may be news-driven, wait for stabilization",
        "unusual_activity": "Unusual activity — monitor closely",
    }
    label = anomaly_labels.get(result.anomaly_type, "")
    if label:
        details.append(label)

    return details


ANOMALY_SIGNAL_LABELS: dict[str, str] = {
    "bullish_accumulation": "🟢 Anomali Bullish — akumulasi terdeteksi",
    "bearish_distribution": "🔴 Anomali Bearish — distribusi terdeteksi",
    "volume_spike": "🟡 Volume Spike — tunggu konfirmasi arah",
    "price_shock": "⚡ Price Shock — kemungkinan news-driven",
    "unusual_activity": "👁️ Unusual Activity — monitor",
    "none": "✅ Normal — tidak ada anomali terdeteksi",
}
