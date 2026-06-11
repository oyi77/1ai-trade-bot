"""CCXT market data source — OHLCV + ticker from 108+ crypto exchanges.

Wraps CCXT broker's fetch_ohlcv/fetch_ticker as a BaseDataSource.
Supports all exchanges: Binance, Bitget, Bybit, OKX, KuCoin, etc.
"""

from __future__ import annotations

import logging
from typing import Any

from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.ccxt")

SUPPORTED_EXCHANGES: dict[str, str] = {
    "binance": "Binance",
    "bitget": "Bitget",
    "bybit": "Bybit",
    "okx": "OKX",
    "kucoin": "KuCoin",
    "mexc": "MEXC",
    "gate": "Gate.io",
    "bingx": "BingX",
    "htx": "HTX (Huobi)",
    "coinbase": "Coinbase",
}


class CCXTSource(BaseDataSource):
    """Fetch OHLCV + ticker from any CCXT exchange.

    Defaults to Binance. Pass exchange_id to use a different exchange.
    """

    def __init__(self, exchange_id: str = "binance") -> None:
        self._exchange_id = exchange_id
        self._broker: Any = None

    async def _get_broker(self):
        if self._broker is None:
            from tradebot.brokers.ccxt.broker import CCXTBroker
            from tradebot.config import settings

            self._broker = CCXTBroker(
                exchange=self._exchange_id,
                api_key=getattr(settings, "CCXT_API_KEY", ""),
                secret=getattr(settings, "CCXT_SECRET", ""),
                sandbox=True,
            )
            await self._broker.connect()
        return self._broker

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        broker = await self._get_broker()
        raw = await broker.fetch_ohlcv(symbol, timeframe=interval, limit=count)
        result = []
        for row in raw:
            ts = row.get("timestamp", 0)
            result.append(OHLCV(
                timestamp=int(ts / 1000) if ts > 1e12 else int(ts),
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
            ))
        return result

    async def price(self, symbol: str) -> float | None:
        broker = await self._get_broker()
        ticker = await broker.fetch_ticker(symbol)
        if ticker:
            return float(ticker.get("last", ticker.get("close", 0)))
        return None


def get_exchanges_list() -> list[str]:
    return list(SUPPORTED_EXCHANGES.keys())
