"""
Binance public market data source — no authentication required.

Maps internal symbols (``BTC-USD``) to Binance symbols (``BTCUSDT``)
and fetches OHLCV klines via the public REST API.
"""

from __future__ import annotations

import logging

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.binance")

SYMBOL_MAP: dict[str, str] = {
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

INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

BASE_URL: str = settings.BINANCE_BASE_URL
DEFAULT_TIMEOUT: int = settings.BINANCE_TIMEOUT
KLINES_LIMIT: int = 100


class BinanceSource(BaseDataSource):
    """Fetch OHLCV data from Binance public API.

    Only supports crypto symbols that are present in :data:`SYMBOL_MAP`.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=httpx.Timeout(DEFAULT_TIMEOUT),
                headers={"User-Agent": "tradebot/1.0"},
            )
        return self._client

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch OHLCV klines from Binance.

        Args:
            symbol: Internal symbol (e.g. ``"BTC-USD"``).
            interval: Candle interval.
            count: Max candles (capped at 1000 by Binance).

        Returns:
            List of :class:`OHLCV` candles, or empty list on error.
        """
        bsym = SYMBOL_MAP.get(symbol.upper())
        if bsym is None:
            LOG.debug("Binance: no mapping for %s", symbol)
            return []

        bin_interval = INTERVAL_MAP.get(interval, "1m")
        limit = min(count, 1000)

        client = await self._get_client()
        try:
            resp = await client.get(
                "/api/v3/klines",
                params={"symbol": bsym, "interval": bin_interval, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            LOG.warning("Binance fetch failed for %s: %s", symbol, exc)
            return []

        if not data or not isinstance(data, list):
            return []

        candles: list[OHLCV] = []
        for k in data:
            try:
                candles.append(
                    OHLCV(
                        timestamp=int(k[0]) // 1000,  # ms → seconds
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=int(float(k[5])),
                        symbol=symbol,
                    )
                )
            except (IndexError, ValueError, TypeError) as exc:
                LOG.debug("Binance: skipping bad kline %s — %s", k, exc)
                continue

        return candles

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> BinanceSource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
