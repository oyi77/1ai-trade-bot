"""
Forex market data source — primary: Yahoo Finance (intraday),
fallback: Frankfurter API (daily rates, no rate limit).

Uses a global async rate limiter to stay within Yahoo's generous
but still rate-limited free tier.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource
from .yahoo import YahooSource

LOG = logging.getLogger("tradebot.signals.forex")

# ── Frankfurter API symbol mapping: Yahoo symbol → (base, target) ──
FRANKFURTER_MAP: dict[str, tuple[str, str]] = {
    "EURUSD=X": ("EUR", "USD"),
    "GBPUSD=X": ("GBP", "USD"),
    "USDJPY=X": ("USD", "JPY"),
    "AUDUSD=X": ("AUD", "USD"),
    "USDCAD=X": ("USD", "CAD"),
    "NZDUSD=X": ("NZD", "USD"),
    "USDCHF=X": ("USD", "CHF"),
}

FRANKFURTER_BASE = "https://api.frankfurter.app"


class _AsyncRateLimiter:
    """Simple per-key async rate limiter."""

    def __init__(self, min_interval: float = 20.0) -> None:
        self._min_interval: float = min_interval
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Block until the minimum interval has elapsed since last call."""
        async with self._lock:
            now = time.monotonic()
            since_last = now - self._last_call
            if since_last < self._min_interval:
                wait = self._min_interval - since_last
                LOG.debug("Rate limiter: waiting %.1fs", wait)
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


_yahoo_limiter = _AsyncRateLimiter(min_interval=settings.YAHOO_MIN_INTERVAL)


class ForexSource(BaseDataSource):
    """Forex OHLCV data source.

    Priority:
        1. Yahoo Finance via :class:`YahooSource` (intraday).
        2. Frankfurter API (daily close only, no auth, no rate limit).

    Yahoo calls are globally rate-limited to prevent 429 responses.
    """

    def __init__(self) -> None:
        self._yahoo = YahooSource()
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=FRANKFURTER_BASE,
                timeout=httpx.Timeout(10.0),
                headers={"User-Agent": "tradebot/1.0"},
            )
        return self._http

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch forex OHLCV candles.

        Tries Yahoo first for intraday data; if that returns fewer than
        30 candles falls back to the Frankfurter API for the daily close.

        Args:
            symbol: Forex pair (e.g. ``"EURUSD=X"``).
            interval: Candle interval.
            count: Maximum number of candles.

        Returns:
            List of :class:`OHLCV` candles.
        """
        # 1. Try Yahoo for intraday candles
        await _yahoo_limiter.wait()
        candles = await self._yahoo.fetch(symbol, interval=interval, count=count)

        if candles and len(candles) >= 30:
            return candles

        # 2. Fallback: Frankfurter API (daily close only)
        frank_candles = await self._fetch_frankfurter(symbol)
        if frank_candles:
            LOG.info(
                "Forex %s: Yahoo returned %d candles, using Frankfurter daily",
                symbol,
                len(candles),
            )
            return frank_candles

        # 3. Return whatever Yahoo gave (even if < 30)
        LOG.warning("Forex %s: no data from Yahoo or Frankfurter", symbol)
        return candles if candles else []

    async def _fetch_frankfurter(self, symbol: str) -> list[OHLCV]:
        """Fetch the latest daily close from Frankfurter API."""
        mapping = FRANKFURTER_MAP.get(symbol)
        if mapping is None:
            LOG.debug("Frankfurter: no mapping for %s", symbol)
            return []

        base, target = mapping
        client = await self._get_http()
        try:
            resp = await client.get("/latest", params={"from": base, "to": target})
            resp.raise_for_status()
            data = resp.json()
            rate = float(data["rates"][target])
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            LOG.warning("Frankfurter fetch failed for %s: %s", symbol, exc)
            return []

        now_ts = int(time.time())
        return [
            OHLCV(
                timestamp=now_ts,
                open=rate,
                high=rate,
                low=rate,
                close=rate,
                volume=0,
                symbol=symbol,
            )
        ]

    async def close(self) -> None:
        await self._yahoo.close()
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> ForexSource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
