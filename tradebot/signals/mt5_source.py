"""MT5 market data source — OHLCV + ticker from MetaTrader 5.

Wraps the MetaTrader5 Python API (copy_rates_from_pos, symbol_info_tick)
as a BaseDataSource for the unified market data pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.mt5")

TIMEFRAME_MAP: dict[str, Any] = {}
SYMBOL_MAP: dict[str, str] = {
    "XAUUSD": "XAUUSD", "GOLD": "XAUUSD",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY", "USOIL": "USOIL", "WTI": "USOIL",
}


def _init_timeframes(mt5: Any) -> None:
    global TIMEFRAME_MAP
    TIMEFRAME_MAP = {
        "1m": mt5.TIMEFRAME_M1,
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "30m": mt5.TIMEFRAME_M30,
        "1h": mt5.TIMEFRAME_H1,
        "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1,
        "1w": mt5.TIMEFRAME_W1,
    }


class MT5Source(BaseDataSource):
    """Fetch OHLCV + ticker from MetaTrader 5.

    Requires MetaTrader5 package and a running MT5 terminal.
    """

    def __init__(self, login: int = 0, password: str = "", server: str = "", path: str = "") -> None:
        self._login = login
        self._password = password
        self._server = server
        self._path = path
        self._mt5: Any = None
        self._connected = False

    async def _connect(self) -> bool:
        if self._connected:
            return True
        try:
            import MetaTrader5 as mt5
        except ImportError:
            LOG.warning("MetaTrader5 package not installed")
            return False

        self._mt5 = mt5
        _init_timeframes(mt5)

        initialized = await asyncio.to_thread(mt5.initialize, path=self._path or None)
        if not initialized:
            LOG.warning("MT5 initialize failed: %s", mt5.last_error())
            return False

        if self._login:
            authorized = await asyncio.to_thread(mt5.login, self._login, password=self._password, server=self._server or None)
            if not authorized:
                LOG.warning("MT5 login failed: %s", mt5.last_error())
                return False

        self._connected = True
        return True

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        if not await self._connect():
            return []

        mt5_symbol = SYMBOL_MAP.get(symbol.upper(), symbol.upper())
        tf = TIMEFRAME_MAP.get(interval, self._mt5.TIMEFRAME_M1)
        raw = await asyncio.to_thread(self._mt5.copy_rates_from_pos, mt5_symbol, tf, 0, count)

        if raw is None:
            LOG.debug("MT5 copy_rates_from_pos returned None for %s", mt5_symbol)
            return []

        result = []
        for row in raw:
            result.append(OHLCV(
                timestamp=int(row.time),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.tick_volume),
            ))
        return result

    async def price(self, symbol: str) -> float | None:
        if not await self._connect():
            return None
        mt5_symbol = SYMBOL_MAP.get(symbol.upper(), symbol.upper())
        tick = await asyncio.to_thread(self._mt5.symbol_info_tick, mt5_symbol)
        if tick:
            return float(tick.ask or tick.bid or tick.last)
        return None

    async def close(self) -> None:
        if self._mt5 is not None:
            await asyncio.to_thread(self._mt5.shutdown)
        self._connected = False
