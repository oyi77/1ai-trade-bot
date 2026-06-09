"""
Proactive signaler — scans for signals at regular intervals and dispatches to
all active subscribers via the Telegram bot.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

from core import Signal
from config import Config
from database import Database
from trade_client import TradeClient, TradeOrder, Direction

LOG = logging.getLogger("subscription_bot.signaler")


# Assets to scan on Stockity + Yahoo fallback for real multi-market
STOCKITY_SYMBOLS = ["CRYPTO_IDX"]
YAHOO_SYMBOLS = ["BTCUSD", "ETHUSD", "XAUUSD", "USOIL", "EURUSD", "GBPUSD", "USDJPY"]
SCAN_SYMBOLS = STOCKITY_SYMBOLS + YAHOO_SYMBOLS


class MultiSourceSignalGenerator:
    def __init__(self, authtoken: str = "", cookie: str = ""):
        self.authtoken = authtoken or Config.STOCKITY_AUTHTOKEN
        self.cookie = cookie or Config.STOCKITY_FULL_COOKIE

    async def generate(self, symbol: str) -> Optional[Signal]:
        symbol_u = symbol.upper()
        try:
            if symbol_u in STOCKITY_SYMBOLS:
                from signals.stockity_http import generate as stockity_generate
                return await stockity_generate(symbol_u, cookie=self.cookie, authtoken=self.authtoken)
            from signals.yahoo import generate as yahoo_generate
            return await yahoo_generate(symbol_u)
        except ImportError as exc:
            LOG.error("Signal source import error for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            LOG.error("Signal generation error for %s: %s", symbol, exc)
            return None


# Backward-compat alias (some code may reference the old name)
SignalGenerator = MultiSourceSignalGenerator


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


# ── DerivSignaler ────────────────────────────────────────────────────────────

class DerivSignaler:
    """Generates trade signals from the Deriv WS client and dispatches
    via the existing Telegram dispatch infrastructure.

    Connects to Deriv WebSocket, subscribes to synthetic index ticks,
    analyzes tick patterns, and produces CALL/PUT/WAIT signals
    matching the core.Signal format expected by the subscription bot.
    """

    # Deriv synthetic indices to scan
    SCAN_SYMBOLS = ["R_75", "R_100", "R_50", "1HZ50V"]

    def __init__(
        self,
        db: Optional[Database] = None,
        trade_client: Optional[TradeClient] = None,
        pat_token: str = "",
        app_id: str = "",
        account_id: str = "",
    ):
        self.db = db
        self.trade_client = trade_client
        self.pat_token = pat_token or os.environ.get("DERIV_PAT_TOKEN", "")
        self.app_id = app_id or os.environ.get("DERIV_APP_ID", "33uQ6fU4eIRvJc6jkYeEa")
        self.account_id = account_id or os.environ.get("DERIV_ACCOUNT_ID", "")
        self._dispatch: Optional[Callable[[Signal], Awaitable[None]]] = None
        self._auto_dispatch: Optional[Callable[[Signal], Awaitable[None]]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client = None  # DerivWSClient instance
        self._last_signals: dict[str, tuple[str, int, int]] = {}
        LOG.info("DerivSignaler initialized (symbols: %s)", ", ".join(self.SCAN_SYMBOLS))

    def set_dispatcher(self, cb: Callable[[Signal], Awaitable[None]]):
        """Set the callback that dispatches signals via Telegram."""
        self._dispatch = cb

    def set_auto_trade_dispatcher(self, cb: Callable[[Signal], Awaitable[None]]):
        """Set the callback that triggers auto-trade on signals."""
        self._auto_dispatch = cb

    async def _connect(self) -> bool:
        """Establish Deriv WebSocket connection."""
        try:
            from deriv.client import DerivWSClient

            self._client = DerivWSClient(
                pat_token=self.pat_token,
                app_id=self.app_id,
                account_id=self.account_id,
            )
            ok = await self._client.connect()
            if not ok:
                LOG.error("DerivSignaler: WS connect failed")
                return False
            LOG.info("DerivSignaler: WS connected ✓")
            return True
        except Exception as exc:
            LOG.error("DerivSignaler connect error: %s", exc)
            return False

    async def _fetch_ticks(self, symbol: str, count: int = 50) -> list:
        """Fetch tick history for a symbol."""
        if not self._client or not self._client.is_connected:
            LOG.warning("DerivSignaler: client not connected")
            return []
        try:
            ticks = await self._client.get_ticks_history(symbol, count=count)
            return ticks or []
        except Exception as exc:
            LOG.debug("DerivSignaler fetch_ticks %s error: %s", symbol, exc)
            return []

    async def generate_signal(self, symbol: str) -> Optional[Signal]:
        """Generate a CALL/PUT signal for a Deriv symbol from tick data.

        Analyzes recent tick momentum and produces a core.Signal
        compatible with the existing Telegram dispatch system.
        """
        ticks = await self._fetch_ticks(symbol, count=100)
        if len(ticks) < 20:
            LOG.debug("DerivSignaler %s: only %d ticks, skipping", symbol, len(ticks))
            return None

        current_price = ticks[-1].price if hasattr(ticks[-1], "price") else float(ticks[-1])
        prev_price = ticks[-10].price if len(ticks) >= 10 and hasattr(ticks[-10], "price") else (
            float(ticks[-10]) if len(ticks) >= 10 else current_price
        )
        if prev_price <= 0:
            return None

        # Determine direction
        price_change = current_price - prev_price
        direction = "CALL" if price_change > 0 else ("PUT" if price_change < 0 else "NEUTRAL")
        if direction == "NEUTRAL":
            return None  # No clear direction

        # Confidence based on momentum strength (0-100)
        change_pct = abs(price_change) / prev_price * 100
        confidence = min(int(change_pct * 10), 95)
        confidence = max(confidence, 5)  # minimum floor

        # Run Momen pattern analysis if available
        try:
            from deriv.patterns import MomenPatternAnalyzer
            analyzer = MomenPatternAnalyzer(analysis_ticks=len(ticks))
            momen = analyzer.analyze(ticks)
            if momen:
                momen_conf = int(momen.confidence * 100) if hasattr(momen, "confidence") else 0
                if momen_conf > 0:
                    confidence = (confidence + momen_conf) // 2
        except Exception:
            pass

        reason = f"Momentum {change_pct:.2f}% over 10 ticks"
        if confidence >= 70:
            reason += " (strong)"
        elif confidence >= 50:
            reason += " (moderate)"

        sig = Signal(
            symbol=symbol,
            action=direction,
            confidence=confidence,
            price=round(current_price, 5),
            reason=reason,
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="deriv",
        )
        return sig

    async def scan_once(self) -> list[Signal]:
        """Scan all configured Deriv symbols for new signals."""
        if not self._client or not self._client.is_connected:
            ok = await self._connect()
            if not ok:
                return []

        signals = []
        for symbol in self.SCAN_SYMBOLS:
            sig = await self.generate_signal(symbol)
            if sig is None:
                continue
            signals.append(sig)

            # Dedup check: is this meaningfully different from last signal?
            last = self._last_signals.get(symbol)
            now = int(time.time())
            is_new = (
                last is None
                or last[0] != sig.action
                or abs(last[1] - sig.confidence) > 10
                or (now - last[2]) > 3600
            )

            if is_new:
                LOG.info(
                    "DerivSignaler NEW: %s %s %d%%",
                    symbol, sig.action, sig.confidence,
                )
                self._last_signals[symbol] = (sig.action, sig.confidence, now)

                if self._dispatch:
                    await self._dispatch(sig)

                if (
                    self._auto_dispatch
                    and sig.is_tradeable
                    and sig.confidence >= 60
                ):
                    await self._auto_dispatch(sig)

        return signals

    async def _loop(self):
        """Main scanning loop for Deriv signals."""
        LOG.info(
            "DerivSignaler loop started (interval=%ds, symbols=%s)",
            getattr(Config, "SCAN_INTERVAL", 30),
            ", ".join(self.SCAN_SYMBOLS),
        )
        interval = getattr(Config, "SCAN_INTERVAL", 30)

        while self._running:
            try:
                await self.scan_once()
            except Exception as exc:
                LOG.error("DerivSignaler loop error: %s", exc, exc_info=True)
                # On repeated errors, try to reconnect
                if self._client:
                    try:
                        await self._client.reconnect()
                    except Exception:
                        pass
            await asyncio.sleep(interval)

    def start(self):
        """Start the background Deriv scan loop."""
        if self._running:
            LOG.warning("DerivSignaler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        LOG.info("DerivSignaler started")

    async def stop(self):
        """Stop the background Deriv scan loop and disconnect."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        LOG.info("DerivSignaler stopped")

    async def manual_scan(self, symbols: Optional[list[str]] = None) -> list[Signal]:
        """Trigger a one-off scan for the given symbols (or all configured)."""
        targets = symbols or self.SCAN_SYMBOLS
        if not self._client or not self._client.is_connected:
            ok = await self._connect()
            if not ok:
                return []
        results = []
        for sym in targets:
            sig = await self.generate_signal(sym)
            if sig:
                results.append(sig)
        return results

    @property
    def is_running(self) -> bool:
        return self._running
