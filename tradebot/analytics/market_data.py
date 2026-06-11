"""Market data fetcher — pulls OHLCV candles from exchanges via ccxt."""
from __future__ import annotations

import logging
from typing import Sequence

import ccxt

from app.analytics.indicators import Candle, MIN_CANDLES

logger = logging.getLogger(__name__)

# ── Public configuration ──────────────────────────────────────────

TIMEFRAME_TO_CCXT: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}
DEFAULT_CANDLE_LIMIT: int = 200


class MarketDataError(Exception):
    """Raised when market data cannot be fetched."""


def _build_exchange() -> ccxt.Exchange:
    """Build a read-only exchange client (no auth needed for OHLCV)."""
    return ccxt.binance({
        "enableRateLimit": True,
        "timeout": 30_000,
        "options": {"defaultType": "spot"},
    })


def fetch_candles(symbol: str, timeframe: str,
                   limit: int = DEFAULT_CANDLE_LIMIT) -> list[Candle]:
    """Fetch OHLCV candles for symbol/timeframe.

    Raises:
        MarketDataError: on network/parsing failures or insufficient data.
    """
    if timeframe not in TIMEFRAME_TO_CCXT:
        raise MarketDataError(f"Unsupported timeframe: {timeframe}")

    exchange = _build_exchange()
    ccxt_timeframe = TIMEFRAME_TO_CCXT[timeframe]

    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=ccxt_timeframe, limit=limit)
    except ccxt.NetworkError as exc:
        raise MarketDataError(f"Network error fetching {symbol}: {exc}") from exc
    except ccxt.ExchangeError as exc:
        raise MarketDataError(f"Exchange error fetching {symbol}: {exc}") from exc
    except ccxt.BaseError as exc:
        raise MarketDataError(f"ccxt error fetching {symbol}: {exc}") from exc

    if not raw:
        raise MarketDataError(f"No candle data returned for {symbol} {timeframe}")

    candles: list[Candle] = []
    for row in raw:
        if len(row) < 5:
            continue
        ts, o, h, l, c = row[:5]
        v = row[5] if len(row) >= 6 else 0.0
        candle = Candle(
            timestamp=int(ts), open=float(o), high=float(h),
            low=float(l), close=float(c), volume=float(v),
        )
        if candle.is_valid():
            candles.append(candle)

    if len(candles) < MIN_CANDLES:
        raise MarketDataError(
            f"Insufficient candles for {symbol}: got {len(candles)}, need {MIN_CANDLES}"
        )
    logger.info("Fetched %d candles for %s %s", len(candles), symbol, timeframe)
    return candles


def fetch_candles_multi(symbols: Sequence[str], timeframe: str,
                         limit: int = DEFAULT_CANDLE_LIMIT) -> dict[str, list[Candle]]:
    """Fetch candles for multiple symbols. Returns dict symbol → candles.

    Skips symbols that fail (logged) so one bad symbol doesn't break the scan.
    """
    results: dict[str, list[Candle]] = {}
    for symbol in symbols:
        try:
            results[symbol] = fetch_candles(symbol, timeframe, limit)
        except MarketDataError as exc:
            logger.warning("Skipping %s: %s", symbol, exc)
    return results
