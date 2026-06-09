"""
Forex data source: FrankfurterAPI (primary, free, no rate limit!) + Yahoo fallback.
Frankfurter provides daily rates - for 1m binary we need Yahoo for intraday.
But Frankfurt is great for daily trend confirmation.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import yfinance as yf

from core import Candle, Signal
from core.indicators import score_trend, classify_signal

LOG = logging.getLogger("signals.forex")

# ── Global rate limiter for Yahoo Finance ────────────────────────────
_YAHOO_LOCK = threading.Lock()
_LAST_YAHOO_CALL: float = 0
_MIN_YAHOO_INTERVAL = 20.0  # seconds between Yahoo calls


def _throttled_yahoo(symbol: str, interval: str, period: str) -> list[Candle]:
    """Fetch OHLCV from Yahoo with global rate throttling."""
    global _LAST_YAHOO_CALL
    with _YAHOO_LOCK:
        now = time.time()
        since_last = now - _LAST_YAHOO_CALL
        if since_last < _MIN_YAHOO_INTERVAL:
            wait = _MIN_YAHOO_INTERVAL - since_last
            LOG.info("Yahoo throttle: waiting %.1fs for %s", wait, symbol)
            time.sleep(wait)
        _LAST_YAHOO_CALL = time.time()

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return []
        candles = []
        for idx, row in df.iterrows():
            candles.append(Candle(
                timestamp=int(idx.timestamp()),
                open=float(row.get("Open", 0)),
                high=float(row.get("High", 0)),
                low=float(row.get("Low", 0)),
                close=float(row.get("Close", 0)),
                volume=float(row.get("Volume", 0)),
            ))
        return candles
    except Exception as e:
        LOG.warning("Yahoo fetch fail %s: %s", symbol, e)
        return []


def _fetch_frankfurter(symbol: str) -> Optional[list[float]]:
    """Fetch daily close prices from Frankfurter API (no rate limit, free)."""
    # Map Yahoo symbol → base/target
    mapping = {
        "EURUSD=X": ("EUR", "USD"),
        "GBPUSD=X": ("GBP", "USD"),
        "USDJPY=X": ("USD", "JPY"),
        "AUDUSD=X": ("AUD", "USD"),
        "USDCAD=X": ("USD", "CAD"),
        "NZDUSD=X": ("NZD", "USD"),
        "USDCHF=X": ("USD", "CHF"),
    }
    if symbol not in mapping:
        return None
    base, target = mapping[symbol]
    try:
        url = f"https://api.frankfurter.app/latest?from={base}&to={target}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            rate = r.json()["rates"][target]
            LOG.info("Frankfurter %s: %.6f", symbol, rate)
            return [rate]
    except Exception as e:
        LOG.warning("Frankfurter fail %s: %s", symbol, e)
    return None


def generate(symbol: str, interval: str = "1m", period: str = "2d") -> Signal:
    """Generate forex signal - try Yahoo first (for intraday), then Frankfurter daily."""
    # Try Yahoo for intraday candles (binary 1m needs this)
    candles = _throttled_yahoo(symbol, interval, period)
    if not candles or len(candles) < 30:
        # Try Frankfurter as fallback - but only gives daily close
        rates = _fetch_frankfurter(symbol)
        if rates:
            price = rates[-1]
            # No intraday data from Frankfurter, use simple momentum check
            return Signal(
                symbol=symbol,
                action="WAIT",
                confidence=45,
                price=price,
                reason="Frankfurter daily only - need intraday for binary 1m",
                timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                source="forex",
            )
        return Signal(
            symbol=symbol,
            action="WAIT",
            confidence=0,
            price=0.0,
            reason="Data unavailable from Yahoo (rate limit/cooling) & Frankfurter",
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="forex",
        )

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    price = closes[-1]

    score, reasons = score_trend(closes, highs, lows, mode="binary")
    return classify_signal(score, price, reasons, symbol, source="forex")
