"""
Binance public market data source — no auth needed.
Works for crypto symbols: BTC-USD, ETH-USD, etc.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from core import Candle, Signal
from core.indicators import score_trend, classify_signal

LOG = logging.getLogger("signals.binance")

# Map our symbol format → Binance symbol
SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "BNB-USD": "BNBUSDT",
    "SOL-USD": "SOLUSDT",
    "XRP-USD": "XRPUSDT",
    "ADA-USD": "ADAUSDT",
    "DOGE-USD": "DOGEUSDT",
    "DOT-USD": "DOTUSDT",
    "MATIC-USD": "MATICUSDT",
    "AVAX-USD": "AVAXUSDT",
    "LINK-USD": "LINKUSDT",
    "UNI-USD": "UNIUSDT",
    "ATOM-USD": "ATOMUSDT",
    "LTC-USD": "LTCUSDT",
    "BCH-USD": "BCHUSDT",
}

INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# How many candles to fetch
LIMIT = 100

BASE_URL = "https://api.binance.com"


def fetch_ohlc(symbol: str, interval: str = "1m", period: str = "2d") -> list[Candle]:
    """Fetch OHLCV candles from Binance public API."""
    bsym = SYMBOL_MAP.get(symbol.upper())
    if not bsym:
        LOG.info("Binance: no mapping for %s", symbol)
        return []

    bin_interval = INTERVAL_MAP.get(interval, "1m")

    url = f"{BASE_URL}/api/v3/klines"
    params = {"symbol": bsym, "interval": bin_interval, "limit": LIMIT}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        LOG.warning("Binance fetch fail %s: %s", symbol, e)
        return []

    if not data or not isinstance(data, list):
        return []

    candles = []
    for k in data:
        try:
            candles.append(Candle(
                timestamp=int(k[0]) // 1000,  # ms → s
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            ))
        except (IndexError, ValueError):
            continue

    return candles


def generate(symbol: str, interval: str = "1m", period: str = "2d") -> Signal:
    """Generate a signal using Binance data."""
    candles = fetch_ohlc(symbol, interval, period)
    if not candles or len(candles) < 30:
        return Signal(
            symbol=symbol,
            action="WAIT",
            confidence=0,
            price=0.0,
            reason="Insufficient data from Binance",
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="binance",
        )

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    price = closes[-1]

    score, reasons = score_trend(closes, highs, lows, mode="binary")
    return classify_signal(score, price, reasons, symbol, source="binance")
