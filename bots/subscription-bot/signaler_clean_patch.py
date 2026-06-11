from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional, Callable, Awaitable

from core import Signal
from config import Config
from database import Database
from trade_client import TradeClient, TradeOrder, Direction

LOG = logging.getLogger("subscription_bot.signaler")

STOCKITY_SYMBOLS = ["CRYPTO_IDX"]
SCAN_SYMBOLS = list(STOCKITY_SYMBOLS)


class StockitySignalGenerator:
    def __init__(self, authtoken: str = "", cookie: str = ""):
        self.authtoken = authtoken or Config.STOCKITY_AUTHTOKEN
        self.cookie = cookie or Config.STOCKITY_FULL_COOKIE

    async def generate(self, symbol: str) -> Optional[Signal]:
        symbol_u = symbol.upper()
        try:
            from signals.stockity_http import generate as stockity_generate
            return await stockity_generate(symbol_u, cookie=self.cookie, authtoken=self.authtoken)
        except ImportError as exc:
            LOG.error("Signal source import error for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            LOG.error("Signal generation error for %s: %s", symbol, exc)
            return None


class ProactiveSignaler:
    def __init__(
        self,
        db: Database,
        trade_client: Optional[TradeClient] = None,
        authtoken: str = "",
        cookie: str = "",
    ):
        self.db = db
        self.trade_client = trade_client
        self._generator = StockitySignalGenerator(authtoken=authtoken, cookie=cookie)
        self._dispatch: Optional[Callable[[Signal], Awaitable[None]]] = None
        self._auto_dispatch: Optional[Callable[[Signal], Awaitable[None]]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_signals: dict[str, tuple[str, int, int]] = {}

    def set_dispatcher(self, cb: Callable[[Signal], Awaitable[None]]):
        self._dispatch = cb

    def set_auto_trade_dispatcher(self, cb: Callable[[Signal], Awaitable[None]]):
        self._auto_dispatch = cb

    async def scan_once(self) -> list[Signal]:
        signals = []
        for symbol in SCAN_SYMBOLS:
            sig = await self._generator.generate(symbol)
            if sig is None:
                continue

            if sig.action == "WAIT" and sig.confidence < Config.MIN_CONFIDENCE:
                continue

            signals.append(sig)

            last = self._last_signals.get(symbol)
            now = int(time.time())
            is_new = (
                last is None
                or last[0] != sig.action
                or abs(last[1] - sig.confidence) > 10
                or (now - last[2]) > 3600
            )

            if is_new:
                LOG.info("NEW signal: %s %s %d%%", symbol, sig.action, sig.confidence)
                self._last_signals[symbol] = (sig.action, sig.confidence, now)

                if self._dispatch:
                    await self._dispatch(sig)

                if (
                    self._auto_dispatch
                    and sig.is_tradeable
                    and sig.confidence >= Config.MIN_CONFIDENCE
                ):
                    await self._auto_dispatch(sig)

        return signals

    async def _loop(self):
        LOG.info(
            "Proactive signaler started (interval=%ds, min_conf=%d%%)",
            Config.SCAN_INTERVAL,
            Config.MIN_CONFIDENCE,
        )
        while self._running:
            try:
                await self.scan_once()
            except Exception as exc:
                LOG.error("Scan loop error: %s", exc, exc_info=True)
            await asyncio.sleep(Config.SCAN_INTERVAL)

    def start(self):
        if self._running:
            LOG.warning("Signaler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        LOG.info("Proactive signaler started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        LOG.info("Proactive signaler stopped")

    async def manual_scan(self, symbols: Optional[list[str]] = None) -> list[Signal]:
        targets = symbols or SCAN_SYMBOLS
        results = []
        for sym in targets:
            sig = await self._generator.generate(sym)
            if sig:
                results.append(sig)
        return results

    @property
    def is_running(self) -> bool:
        return self._running


SignalGenerator = StockitySignalGenerator
