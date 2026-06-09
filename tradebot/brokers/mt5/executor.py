"""
MT5Executor — Async EA executor with position management and SL/TP tracking.
=============================================================================

Models the consolidated logic from the legacy ``brokers/mt5_executor.py`` and
``engines/ea_executor.py`` scripts. Provides:

- ``MT5Executor`` — high-level async class that:
  - Reads signals from a queue or callback
  - Opens positions with SL/TP
  - Monitors open positions against current price
  - Manages EA state (persisted to JSON)
  - Tracks running PnL

Usage::

    executor = MT5Executor(broker=mt5_broker)
    await executor.start()
    # ... executor runs in background ...
    await executor.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.brokers.base import Broker
from tradebot.brokers.mt5.broker import MT5Broker
from tradebot.config import settings
from tradebot.models import Signal

LOG = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))

# ──────────────────────────────────────────────────────────────────
#  Data Structures
# ──────────────────────────────────────────────────────────────────


@dataclass
class EAPosition:
    """An open EA-managed position with SL/TP tracking."""

    id: str
    action: str                      # "BUY" | "SELL"
    symbol: str
    entry: float
    sl: float
    tp: float
    tp1: float = 0.0                 # First take-profit target
    tp2: float = 0.0                 # Second take-profit target
    tp3: float = 0.0                 # Third take-profit target
    confidence: float = 0.0
    source: str = "unknown"
    open_time: str = ""
    status: str = "OPEN"             # "OPEN" | "TP" | "SL" | "MANUAL"
    close_price: float = 0.0
    close_time: str = ""
    pnl: float = 0.0
    order_id: str = ""


@dataclass
class EAState:
    """Persistent state of the EA executor."""

    positions: list[dict] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    total_pnl: float = 0.0
    signals_processed: int = 0
    last_signal_fingerprint: str = ""
    total_wins: int = 0
    total_losses: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> EAState:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ──────────────────────────────────────────────────────────────────
#  Executor
# ──────────────────────────────────────────────────────────────────


class MT5Executor:
    """Async EA executor for MT5 with position lifecycle management.

    The executor reads signals (via a callback or queue), opens positions
    through the ``Broker`` interface, monitors them for SL/TP hits, and
    persists state for crash recovery.

    Parameters
    ----------
    broker : Broker
        An async broker implementation (MT5Broker or derived).
    state_file : str or Path, optional
        Path to persist EA state JSON. Defaults to ``DATA_DIR/ea_state.json``.
    signal_queue : asyncio.Queue, optional
        Queue from which signals are consumed. If not provided, use
        ``on_signal`` callback.
    on_signal : Callable, optional
        Async callback that returns a Signal or None. Called on each loop.
    max_positions : int, optional
        Maximum concurrent positions. Falls back to ``settings.BROKER_MAX_POSITIONS``.
    interval : float, optional
        Loop interval in seconds (default: 3).
    dry_run : bool, optional
        Paper-trade mode. Falls back to ``settings.BROKER_DRY_RUN``.
    """

    def __init__(
        self,
        broker: Broker,
        *,
        state_file: str | Path | None = None,
        signal_queue: asyncio.Queue | None = None,
        on_signal: Callable[[], Any] | None = None,
        max_positions: int | None = None,
        interval: float = 3.0,
        dry_run: bool | None = None,
    ) -> None:
        self.broker = broker
        self._state_file = Path(
            state_file or Path(settings.DATA_DIR) / "ea_state.json"
        )
        self._signal_queue = signal_queue
        self._on_signal = on_signal
        self._max_positions = max_positions or int(settings.BROKER_MAX_POSITIONS)
        self._interval = interval
        self._dry_run = dry_run if dry_run is not None else bool(settings.BROKER_DRY_RUN)

        self._state = EAState()
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_price: float | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ──

    async def start(self) -> None:
        """Start the executor background loop."""
        if self._running:
            LOG.warning("MT5Executor already running")
            return

        self._running = True
        self._state = await self._load_state()
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run_loop(), name="mt5-executor")
        LOG.info(
            "MT5Executor started | max_positions=%d interval=%.1fs dry_run=%s",
            self._max_positions, self._interval, self._dry_run,
        )

    async def stop(self) -> None:
        """Gracefully stop the executor loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._save_state()
        LOG.info(
            "MT5Executor stopped | processed=%d total_pnl=%.2f wins=%d losses=%d",
            self._state.signals_processed,
            self._state.total_pnl,
            self._state.total_wins,
            self._state.total_losses,
        )

    @property
    def state(self) -> EAState:
        return self._state

    @property
    def current_price(self) -> float | None:
        return self._last_price

    # ── Position management ──

    def get_open_positions(self) -> list[dict]:
        """Return a copy of the current open positions."""
        return list(self._state.positions)

    def get_closed_positions(self) -> list[dict]:
        """Return a copy of the closed positions list."""
        return list(self._state.closed)

    async def close_position(self, position_id: str) -> bool:
        """Manually close a specific open position by its ID."""
        async with self._lock:
            for i, pos in enumerate(self._state.positions):
                if pos["id"] == position_id:
                    price = self._last_price or pos["entry"]
                    pos["status"] = "MANUAL"
                    pos["close_price"] = price
                    pos["close_time"] = self._now_iso()
                    pos["pnl"] = self._calc_pnl(pos, price)
                    self._state.total_pnl += pos["pnl"]
                    self._state.closed.append(pos)
                    self._state.positions.pop(i)
                    await self._save_state()
                    LOG.info(
                        "📋 MANUAL CLOSE: %s %s | PnL=%.2f", pos["action"], pos["symbol"], pos["pnl"],  # noqa: E501
                    )
                    return True
        return False

    async def close_all_positions(self) -> int:
        """Close all open positions. Returns count closed."""
        closed = 0
        for pos in self._state.positions.copy():
            if await self.close_position(pos["id"]):
                closed += 1
        return closed

    # ── Internal loop ──

    async def _run_loop(self) -> None:
        """Main executor event loop."""
        while self._running:
            try:
                # 1. Fetch current price
                self._last_price = await self._fetch_price()

                # 2. Check open positions for SL/TP hits
                await self._check_positions(self._last_price)

                # 3. Check for new signals
                signal = await self._get_signal()
                if signal and signal.is_valid:
                    await self._process_signal(signal)

                # 4. Log heartbeat if positions exist
                if self._state.positions:
                    self._log_heartbeat()

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.exception("Executor loop error: %s", exc)

            await asyncio.sleep(self._interval)

    # ── Signal handling ──

    async def _get_signal(self) -> Signal | None:
        """Retrieve the next signal from queue or callback."""
        if self._signal_queue is not None:
            try:
                return self._signal_queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        if self._on_signal is not None:
            if asyncio.iscoroutinefunction(self._on_signal):
                return await self._on_signal()
            return self._on_signal()
        return None

    async def _process_signal(self, signal: Signal) -> None:
        """Validate and execute a trading signal."""
        fingerprint = f"{signal.symbol}_{signal.direction}_{signal.entry_price or 0:.4f}"
        if self._state.last_signal_fingerprint == fingerprint:
            LOG.debug("Duplicate signal skipped: %s", fingerprint)
            return

        if len(self._state.positions) >= self._max_positions:
            LOG.info("⏭️ Max positions reached — skip signal %s %s", signal.direction, signal.symbol)
            return

        # Extract optional SL/TP from signal metadata if present
        sl = signal.metadata.get("sl", 0.0) if signal.metadata else 0.0
        tp = signal.metadata.get("tp", 0.0) if signal.metadata else 0.0

        # Build the position dict
        pos = {
            "id": f"ea_{int(time.time() * 1000)}_{signal.symbol}",
            "action": signal.direction.upper(),
            "symbol": signal.symbol,
            "entry": signal.entry_price or self._last_price or 0.0,
            "sl": sl,
            "tp": tp,
            "tp1": signal.metadata.get("tp1", tp) if signal.metadata else tp,
            "tp2": signal.metadata.get("tp2", 0.0) if signal.metadata else 0.0,
            "tp3": signal.metadata.get("tp3", tp) if signal.metadata else tp,
            "confidence": signal.confidence,
            "source": signal.source.value if hasattr(signal.source, 'value') else str(signal.source),  # noqa: E501
            "open_time": self._now_iso(),
            "status": "OPEN",
        }

        if self._dry_run:
            LOG.info(
                "📝 PAPER: %s %s @ %.4f | SL=%.4f TP=%.4f | conf=%.0f%% | %s",
                pos["action"], pos["symbol"], pos["entry"],
                pos["sl"], pos["tp"],
                signal.confidence * 100 if signal.confidence else 0,
                pos["source"],
            )
        else:
            # Attempt real order via broker
            order = await self.broker.place_order(
                symbol=pos["symbol"],
                contract_type=pos["action"],
                barrier=0,
                stake=settings.BROKER_DEFAULT_STAKE,
                sl=pos["sl"] if pos["sl"] else None,
                tp=pos["tp"] if pos["tp"] else None,
                comment=f"ea_{signal.symbol}",
            )
            if order is None:
                LOG.error("❌ Failed to place real order for signal")
                return
            pos["order_id"] = order.order_id
            LOG.info(
                "✅ REAL ORDER: %s %s (order=%s)",
                pos["action"], pos["symbol"], order.order_id,
            )

        async with self._lock:
            self._state.positions.append(pos)
            self._state.signals_processed += 1
            self._state.last_signal_fingerprint = fingerprint
            await self._save_state()

    # ── Position monitoring ──

    async def _check_positions(self, price: float | None) -> None:
        """Check all open positions for SL/TP hits."""
        if not price or not self._state.positions:
            return

        async with self._lock:
            new_positions = []
            for pos in self._state.positions:
                result = self._check_sl_tp(pos, price)
                if result:
                    reason, close_price = result
                    pnl = self._calc_pnl(pos, close_price)
                    pos["status"] = reason
                    pos["close_price"] = close_price
                    pos["close_time"] = self._now_iso()
                    pos["pnl"] = round(pnl, 2)

                    emoji = "🟢" if reason == "TP" else "🔴"
                    LOG.info(
                        "%s CLOSED: %s %s | %s | PnL=%.2f | Entry=%.4f -> Close=%.4f",
                        emoji, pos["action"], pos["symbol"], reason,
                        pos["pnl"], pos["entry"], close_price,
                    )
                    self._state.closed.append(pos)
                    self._state.total_pnl += pos["pnl"]
                    if pos["pnl"] >= 0:
                        self._state.total_wins += 1
                    else:
                        self._state.total_losses += 1
                else:
                    new_positions.append(pos)

            if len(new_positions) != len(self._state.positions):
                self._state.positions = new_positions
                await self._save_state()

    def _check_sl_tp(self, pos: dict, price: float) -> tuple | None:
        """Return (reason, close_price) if SL or TP is hit, else None."""
        _entry = pos["entry"]
        sl = pos["sl"]
        tp = pos.get("tp1", pos.get("tp", 0)) or 0
        action = pos["action"].upper()

        if not sl and not tp:
            return None

        if action == "BUY":
            if sl and price <= sl:
                return ("SL", price)
            if tp and price >= tp:
                return ("TP", price)
        else:  # SELL
            if sl and price >= sl:
                return ("SL", price)
            if tp and price <= tp:
                return ("TP", price)
        return None

    # ── PnL calculation ──

    @staticmethod
    def _calc_pnl(pos: dict, close_price: float) -> float:
        """Calculate PnL for a position at the given close price."""
        diff = close_price - pos["entry"]
        if pos["action"].upper() == "SELL":
            diff = -diff
        return diff

    # ── Price fetching (override in subclass for real feeds) ──

    async def _fetch_price(self) -> float | None:
        """Fetch the latest price for monitoring.

        Override this method in a subclass to use a real price feed.
        The base implementation returns None (no price-based checks).
        """
        return None

    # ── State persistence ──

    async def _load_state(self) -> EAState:
        """Load EA state from disk, or return fresh state."""
        def _load() -> EAState:
            try:
                if self._state_file.exists():
                    data = json.loads(self._state_file.read_text())
                    return EAState.from_dict(data)
            except Exception as exc:
                LOG.warning("Failed to load EA state: %s", exc)
            return EAState()
        return await asyncio.to_thread(_load)

    async def _save_state(self) -> None:
        """Persist EA state to disk."""
        def _save() -> None:
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(
                    json.dumps(self._state.to_dict(), indent=2, default=str)
                )
            except Exception as exc:
                LOG.error("Failed to save EA state: %s", exc)
        await asyncio.to_thread(_save)

    # ── Helpers ──

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(WIB).isoformat()

    def _log_heartbeat(self) -> None:
        """Log a status line for active positions."""
        for pos in self._state.positions:
            price_str = f"{self._last_price:.4f}" if self._last_price else "N/A"
            est_pnl = ""
            if self._last_price and pos["entry"]:
                diff = abs(self._last_price - pos["entry"])
                if pos["action"].upper() == "SELL":
                    diff = pos["entry"] - self._last_price
                else:
                    diff = self._last_price - pos["entry"]
                est_pnl = f" | Est.PnL={diff:.4f}"
            LOG.info(
                "💼 %s %s @ %.4f | Current=%s%s | SL=%.4f TP=%.4f",
                pos["action"], pos["symbol"], pos["entry"],
                price_str, est_pnl, pos["sl"], pos["tp"],
            )


# ──────────────────────────────────────────────────────────────────
#  PriceFeed MT5Executor — Real MT5 price loop
# ──────────────────────────────────────────────────────────────────


class MT5PriceFeedExecutor(MT5Executor):
    """MT5Executor subclass that fetches price from the MT5 terminal."""

    def __init__(self, broker: MT5Broker, *, symbol: str = "XAUUSD", **kwargs):
        super().__init__(broker, **kwargs)
        self._symbol = symbol

    async def _fetch_price(self) -> float | None:
        """Fetch the latest tick price from MT5."""
        if not self.broker.is_connected:
            return None
        try:
            import MetaTrader5 as mt5  # noqa: F811,N813
            tick = await asyncio.to_thread(mt5.symbol_info_tick, self._symbol)
            if tick is not None:
                return (tick.ask + tick.bid) / 2
        except Exception as e:
            LOG.debug("MT5 price fetch failed: %s", e)
        return None
