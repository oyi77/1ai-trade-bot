"""
Alpha Vantage stock market data source (free tier).

Free tier: 5 API calls/min, 500 calls/day. Registration required (no credit card).
Covers US stocks, forex, crypto. Used as secondary fallback for stocks.
"""
from __future__ import annotations

import logging

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.alpha_vantage")
BASE_URL = "https://www.alphavantage.co/query"

# Alpha Vantage expects symbols without .JK suffix for IDX stocks
# e.g. BBCA.JK → BBCA.JK (same)


class AlphaVantageSource(BaseDataSource):
    """Fetch OHLCV data from Alpha Vantage (US stocks, crypto, forex).

    Requires ``ALPHA_VANTAGE_API_KEY`` in settings (free, registration required).
    Skips gracefully when key is empty.

    Note: API rate limit is 5 calls/min. This source is intended as a
    secondary/tertiary fallback when primary sources fail.
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
        api_key = settings.ALPHA_VANTAGE_API_KEY
        if not api_key:
            LOG.debug("Alpha Vantage: no API key configured, skipping")
            return []

        # Map interval to Alpha Vantage outputsize
        # Alpha Vantage: 1min, 5min, 15min, 30min, 60min, daily, weekly, monthly
        interval_map = {
            "1m": "1min", "5m": "5min", "15m": "15min",
            "30m": "30min", "1h": "60min", "1H": "60min",
            "1d": "daily", "1D": "daily",
        }
        mapped = interval_map.get(interval)
        if not mapped:
            LOG.debug("Alpha Vantage: unsupported interval %s", interval)
            return []

        sym = symbol.upper().strip()
        # Alpha Vantage function selection
        if mapped == "daily":
            function = "TIME_SERIES_DAILY_ADJUSTED"
            data_key = "Time Series (Daily)"
            time_key = None
        elif mapped == "60min":
            function = "TIME_SERIES_INTRADAY"
            data_key = f"Time Series ({mapped})"
            time_key = mapped
        else:
            function = "TIME_SERIES_INTRADAY"
            data_key = f"Time Series ({mapped})"
            time_key = mapped

        params: dict[str, str] = {
            "function": function,
            "symbol": sym,
            "apikey": api_key,
            "outputsize": "compact",
        }
        if time_key:
            params["interval"] = mapped

        client = await self._get_client()
        try:
            resp = await client.get(BASE_URL, params=params, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            LOG.debug("Alpha Vantage fetch failed for %s: %s", symbol, exc)
            return []

        series = data.get(data_key)
        if not series:
            LOG.debug("Alpha Vantage returned no data for %s", symbol)
            return []

        candles: list[OHLCV] = []
        from datetime import datetime

        for date_str, vals in series.items():
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                ts = int(dt.timestamp())
            except ValueError:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    ts = int(dt.timestamp())
                except ValueError:
                    continue

            try:
                candles.append(OHLCV(
                    timestamp=ts,
                    open=float(vals.get("1. open", 0)),
                    high=float(vals.get("2. high", 0)),
                    low=float(vals.get("3. low", 0)),
                    close=float(vals.get("4. close", 0)),
                    volume=int(float(vals.get("6. volume", vals.get("5. volume", 0)))),
                    symbol=symbol,
                ))
            except (ValueError, TypeError):
                continue

        if not candles:
            return []

        candles.sort(key=lambda c: c.timestamp)

        if count and len(candles) > count:
            candles = candles[-count:]

        return candles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> AlphaVantageSource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
