"""Deriv market data source — OHLCV + ticks from Deriv WebSocket API.

Wraps DerivWSClient's ticks_history and ohlcv methods as a BaseDataSource.
Provides tick and candle data for synthetic indices (R_75, R_100, etc.).
"""

from __future__ import annotations

import logging
from typing import Any

from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.deriv")

SYMBOL_PREFIXES = ("R_", "1HZ", "BOOM", "CRASH", "STP", "STABLE", "VOLATILE")


def is_deriv_symbol(symbol: str) -> bool:
    return symbol.upper().startswith(SYMBOL_PREFIXES)


class DerivSource(BaseDataSource):
    """Fetch OHLCV + tick data from Deriv WebSocket API.

    Works with synthetic indices: R_75, R_100, 1HZ10V, BOOM1000, etc.
    Uses the shared DerivWSClient singleton.
    """

    def __init__(self) -> None:
        self._client: Any = None

    async def _get_client(self):
        if self._client is None:
            from tradebot.brokers.deriv.client import DerivWSClient
            from tradebot.config import settings

            self._client = DerivWSClient(
                app_id=getattr(settings, "DERIV_APP_ID", ""),
                token=getattr(settings, "DERIV_PAT_TOKEN", ""),
            )
            await self._client.connect()
        return self._client

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        client = await self._get_client()
        interval_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        granularity = interval_map.get(interval, 60)

        try:
            candles = await client.get_ohlcv(symbol, granularity=granularity, count=count)
            result = []
            for c in candles:
                result.append(OHLCV(
                    timestamp=int(c.get("epoch", 0)),
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=0,
                ))
            return result
        except Exception as e:
            LOG.debug("Deriv fetch failed for %s: %s", symbol, e)
            return []

    async def price(self, symbol: str) -> float | None:
        client = await self._get_client()
        try:
            ticks = await client.get_ticks_history(symbol, count=1)
            if ticks:
                return float(ticks[-1].price)
        except Exception as e:
            LOG.debug("Deriv price failed for %s: %s", symbol, e)
        return None
