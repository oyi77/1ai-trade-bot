"""
Finnhub stock market data source.

Free tier: 60 API calls/min, registration required (no credit card).
Covers US stocks, forex, crypto, fundamentals.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.finnhub")
BASE_URL = "https://finnhub.io/api/v1"

INTERVAL_MAP: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1H": "60",
    "4h": "240",
    "4H": "240",
    "1d": "D",
    "1D": "D",
}


class FinnhubSource(BaseDataSource):
    """Fetch OHLCV data from Finnhub (US stocks, crypto, forex).

    Requires ``FINNHUB_API_KEY`` in settings (free, registration required).
    Skips gracefully when key is empty.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "tradebot/1.0"},
            )
        return self._client

    async def fetch(
        self,
        symbol: str,
        interval: str = "15m",
        count: int = 100,
    ) -> list[OHLCV]:
        api_key = settings.FINNHUB_API_KEY
        if not api_key:
            LOG.debug("Finnhub: no API key configured, skipping")
            return []

        mapped = INTERVAL_MAP.get(interval)
        if not mapped:
            LOG.debug("Finnhub: unsupported interval %s", interval)
            return []

        # Finnhub uses dot-separated symbols: AAPL → AAPL, IDX → BBCA.JK is fine
        sym = symbol.upper().strip()

        client = await self._get_client()
        try:
            resp = await client.get(
                f"{BASE_URL}/stock/candle",
                params={"symbol": sym, "resolution": mapped, "token": api_key, "count": min(count * 2, 500)},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            LOG.debug("Finnhub fetch failed for %s: %s", symbol, exc)
            return []

        if data.get("s") != "ok" or not data.get("t"):
            LOG.debug("Finnhub returned no data for %s", symbol)
            return []

        candles: list[OHLCV] = []
        timestamps = data.get("t", [])
        opens = data.get("o", [])
        highs = data.get("h", [])
        lows = data.get("l", [])
        closes = data.get("c", [])
        volumes = data.get("v", [])

        for i in range(len(timestamps)):
            try:
                candles.append(OHLCV(
                    timestamp=int(timestamps[i]),
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=int(float(volumes[i])),
                    symbol=symbol,
                ))
            except (ValueError, TypeError, IndexError):
                continue

        if count and len(candles) > count:
            candles = candles[-count:]

        return candles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> FinnhubSource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()