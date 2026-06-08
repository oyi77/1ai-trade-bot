"""
Proactive signaler — scans for signals at regular intervals and dispatches to
all active subscribers via the Telegram bot.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

from core import Signal
from config import Config
from database import Database
from trade_client import TradeClient, TradeOrder, Direction

LOG = logging.getLogger("subscription_bot.signaler")


# Assets to scan on Stockity
SCAN_SYMBOLS = ["CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"]


# ── Signal Generator ────────────────────────────────────────────────────────

class SignalGenerator:
    """
    Generate signals using the existing Stockity HTTP signal engine.
    """

    def __init__(self, authtoken: str = "", cookie: str = ""):
        self.authtoken = authtoken or Config.STOCKITY_AUTHTOKEN
        self.cookie = cookie or Config.STOCKITY_FULL_COOKIE

    async def generate(self, symbol: str) -> Optional[Signal]:
        """Generate a signal for the given symbol using stockity_http."""
        try:
            from signals.stockity_http import generate
            sig = await generate(symbol, cookie=self.cookie, authtoken=self.authtoken)
            return sig
        except ImportError as exc:
            LOG.error("Cannot import stockity_http: %s", exc)
            return None
        except Exception as exc:
            LOG.error("Signal generation error for %s: %s", symbol, exc)
            return None


# ── Proactive Signaler ──────────────────────────────────────────────────────

class ProactiveSignaler:
    """
    Periodically scans for signals and broadcasts to subscribers.

    The dispatcher callable is set by the bot after initialization.
    """

    def __init__(
        self,
        db: Database,
        trade_client: Optional[TradeClient] = None,
        authtoken: str = "",
        cookie: str = "",
    ):
        self.db = db
        self.trade_client = trade_client
        self._generator = SignalGenerator(authtoken=authtoken, cookie=cookie)
        self._dispatch: Optional[Callable[[Signal], Awaitable[None]]] = None
        self._auto_dispatch: Optional[Callable[[Signal], Awaitable[None]]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_signals: dict[str, tuple[str, int, int]] = {}  # symbol -> (action, confidence, timestamp)

    def set_dispatcher(self, cb: Callable[[Signal], Awaitable[None]]):
        """Set the callback that dispatches signals via Telegram."""
        self._dispatch = cb

    def set_auto_trade_dispatcher(self, cb: Callable[[Signal], Awaitable[None]]):
        """Set the callback that triggers auto-trade on signals."""
        self._auto_dispatch = cb

    async def scan_once(self) -> list[Signal]:
        """Scan all configured symbols and return new signals."""
        signals = []
        for symbol in SCAN_SYMBOLS:
            sig = await self._generator.generate(symbol)
            if sig is None:
                continue

            # Skip WAIT signals with low confidence
            if sig.action == "WAIT" and sig.confidence < Config.MIN_CONFIDENCE:
                continue

            signals.append(sig)

            # Check if this is a meaningful new signal (different from last)
            last = self._last_signals.get(symbol)
            now = int(time.time())
            is_new = (
                last is None
                or last[0] != sig.action
                or abs(last[1] - sig.confidence) > 10
                or (now - last[2]) > 3600  # force re-send after 1 hour
            )

            if is_new:
                LOG.info(
                    "NEW signal: %s %s %d%%",
                    symbol, sig.action, sig.confidence,
                )
                self._last_signals[symbol] = (sig.action, sig.confidence, now)

                # Dispatch to subscribers
                if self._dispatch:
                    await self._dispatch(sig)

                # Auto-trade for high-confidence signals
                if (
                    self._auto_dispatch
                    and sig.is_tradeable
                    and sig.confidence >= Config.MIN_CONFIDENCE
                ):
                    await self._auto_dispatch(sig)

        return signals

    async def _loop(self):
        """Main scanning loop."""
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
        """Start the background scan loop."""
        if self._running:
            LOG.warning("Signaler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        LOG.info("Proactive signaler started")

    async def stop(self):
        """Stop the background scan loop."""
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
        """Trigger a one-off scan for the given symbols (or all)."""
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
