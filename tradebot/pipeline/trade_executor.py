"""Trade Executor — Signal → Broker → Trade with lifecycle tracking.

Handles the full trade lifecycle: validation, risk checks, order placement,
result tracking, and event emission.  Uses configurable stake from settings
and supports middleware hooks for signal pre/post processing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tradebot.brokers.base import Broker
from tradebot.config.settings import settings
from tradebot.events import bus as event_bus
from tradebot.logging import get_logger
from tradebot.models import Order, Signal, Trade, TradeResult

from .middleware import MiddlewareChain

LOG = get_logger(__name__)

# ---------------------------------------------------------------------------
#  Event callbacks
# ---------------------------------------------------------------------------

TradeEvent = Callable[[Trade], Awaitable[None]]
"""Signature for ``on_trade_open`` / ``on_trade_complete`` callbacks."""
TradeErrorEvent = Callable[[Signal, Exception | None], Awaitable[None]]
"""Signature for ``on_trade_error`` callbacks."""


# ---------------------------------------------------------------------------
#  Trade lifecycle tracker
# ---------------------------------------------------------------------------


@dataclass
class TradeLifecycle:
    """Tracks the full lifecycle state for a batch of trades.

    Maintains cumulative P&L, win/loss counts, and streak information
    that risk checks and middleware can query.
    """

    trades: list[Trade] = field(default_factory=list)
    total_profit: float = 0.0
    total_stake: float = 0.0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    max_drawdown: float = 0.0
    peak_balance: float = 0.0
    symbol: str = ""
    contract_type: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def current_streak(self) -> str:
        """Return 'wins' or 'losses' based on the last trade outcome."""
        if not self.trades:
            return "none"
        return "wins" if self.trades[-1].is_win else "losses"

    @property
    def win_rate(self) -> float:
        """Fraction of completed trades that were wins."""
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    def record_trade(self, trade: Trade) -> None:
        """Add a completed trade and update all derived counters."""
        self.trades.append(trade)
        self.total_profit += trade.profit
        self.total_stake += trade.stake

        if trade.is_win:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
            if self.consecutive_losses > self.max_consecutive_losses:
                self.max_consecutive_losses = self.consecutive_losses

        # Track drawdown
        running_balance = self.total_profit
        if running_balance > self.peak_balance:
            self.peak_balance = running_balance
        current_dd = self.peak_balance - running_balance
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd

    def to_trade_result(self) -> TradeResult:
        """Produce a TradeResult snapshot from current lifecycle state."""
        total = self.wins + self.losses
        return TradeResult(
            profit=self.total_profit,
            total_stake=self.total_stake,
            trades=total,
            wins=self.wins,
            losses=self.losses,
            win_rate=self.win_rate,
            symbol=self.symbol,
            contract_type=self.contract_type,
        )


# ---------------------------------------------------------------------------
#  Main executor
# ---------------------------------------------------------------------------


class TradeExecutor:
    """Executes trading signals through a broker interface.

    Flow:
      1. Receive a validated Signal
      2. Resolve stake (from signal metadata or settings)
      3. *(pre-process middleware chain — filters/enriches signal)*
      4. Place order via Broker
      5. Resolve trade outcome
      6. Update lifecycle & risk state
      7. Emit events (on_trade_open, on_trade_complete, on_trade_error)
      8. Return TradeResult with win/loss tracking
    """

    def __init__(
        self,
        broker: Broker,
        middleware: MiddlewareChain | None = None,
        *,
        default_stake: float | None = None,
        contract_type: str | None = None,
        on_trade_open: TradeEvent | None = None,
        on_trade_complete: TradeEvent | None = None,
        on_trade_error: TradeErrorEvent | None = None,
    ) -> None:
        self.broker = broker
        self.middleware = middleware or MiddlewareChain()
        self.default_stake = (
            default_stake
            if default_stake is not None
            else settings.BROKER_DEFAULT_STAKE
        )
        self.contract_type = contract_type or settings.DERIV_CONTRACT_TYPE
        self.lifecycle = TradeLifecycle()

        # Event callbacks
        self._on_trade_open = on_trade_open
        self._on_trade_complete = on_trade_complete
        self._on_trade_error = on_trade_error

        # Risk state — reset daily
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0

    # ------------------------------------------------------------------
    #  Risk state accessors (for RiskCheckMiddleware)
    # ------------------------------------------------------------------

    def get_daily_pnl(self) -> float:
        """Return current daily P&L."""
        return self._daily_pnl

    def get_consecutive_losses(self) -> int:
        """Return current consecutive loss streak."""
        return self.lifecycle.consecutive_losses

    def get_drawdown(self) -> float:
        """Return current drawdown from peak balance."""
        return self.lifecycle.max_drawdown

    def reset_daily_risk(self) -> None:
        """Reset daily P&L and trade count (call at start of each trading day)."""
        self._daily_pnl = 0.0
        self._daily_trades = 0
        LOG.info("Daily risk counters reset")

    # ------------------------------------------------------------------
    #  Core execution logic
    # ------------------------------------------------------------------

    async def execute(self, signal: Signal) -> TradeResult | None:
        """Execute a signal through the broker and return the result.

        Parameters
        ----------
        signal : Signal
            A validated trading signal produced by the pipeline.

        Returns
        -------
        TradeResult or None
            Trade result with win/loss tracking, or None if the trade was
            rejected by middleware, risk checks, or broker failure.
        """
        if not signal.is_valid:
            LOG.warning("Invalid signal: confidence=%.2f", signal.confidence)
            return None

        # ── 1. Middleware pre-process on the signal ────────────────────
        # Run the middleware chain as a Signal→Signal filter.
        # If any middleware rejects the signal, the chain returns None.
        async def _passthrough(sig: Signal) -> Signal | None:
            return sig

        processed_signal: Signal | None
        try:
            processed_signal = await self.middleware.run(signal, _passthrough)
        except Exception as exc:
            LOG.exception("Trade middleware pre-process failed")
            await self._emit_trade_error(signal, exc)
            return None

        if processed_signal is None:
            LOG.info("Trade rejected by middleware for signal %s", signal.symbol)
            return None

        # ── 2. Resolve stake ───────────────────────────────────────────
        stake = self._resolve_stake(processed_signal)

        # ── 3. Place order ─────────────────────────────────────────────
        order = await self._place_order(processed_signal, stake)
        if order is None:
            return None

        # ── 4. Build Trade record ──────────────────────────────────────
        trade = Trade(
            trade_id=order.order_id,
            symbol=order.symbol,
            contract_type=order.contract_type,
            direction=order.direction,
            stake=order.stake,
            predicted_digit=processed_signal.predicted_digit,
            entry_price=processed_signal.entry_price or 0.0,
            is_completed=False,
            metadata={
                "signal_source": processed_signal.source.value,
                "signal_confidence": processed_signal.confidence,
                "signal_grade": processed_signal.grade.name,
            },
        )

        # Emit open event
        await self._emit_trade_open(trade)

        # ── 5. Resolve trade outcome ───────────────────────────────────
        completed = await self._resolve_trade(trade, order)
        if completed is None:
            return None

        # ── 6. Update lifecycle & risk state ───────────────────────────
        self.lifecycle.record_trade(completed)
        self.lifecycle.symbol = completed.symbol
        self.lifecycle.contract_type = completed.contract_type
        self._daily_pnl += completed.profit
        self._daily_trades += 1

        # Emit complete event
        await self._emit_trade_complete(completed)

        LOG.info(
            "✅ Trade %s completed: profit=%.2f stake=%.2f win=%s "
            "(daily_pnl=%.2f, streak=%d)",
            completed.trade_id,
            completed.profit,
            completed.stake,
            completed.is_win,
            self._daily_pnl,
            self.lifecycle.consecutive_losses,
        )

        result = self.lifecycle.to_trade_result()
        LOG.info(
            "📊 TradeResult: profit=%.2f stake=%.2f trades=%d "
            "wins=%d losses=%d win_rate=%.1f%%",
            result.profit,
            result.total_stake,
            result.trades,
            result.wins,
            result.losses,
            result.win_rate * 100,
        )

        return result

    # ------------------------------------------------------------------
    #  Internal: order placement
    # ------------------------------------------------------------------

    async def _place_order(self, signal: Signal, stake: float) -> Order | None:
        """Place an order via broker and handle errors."""
        try:
            order = await self.broker.place_order(
                symbol=signal.symbol,
                contract_type=self.contract_type,
                barrier=signal.predicted_digit,
                stake=stake,
            )
        except Exception as exc:
            LOG.exception("Broker place_order failed for %s", signal)
            await self._emit_trade_error(signal, exc)
            return None

        if order is None:
            LOG.error("Broker returned None for signal: %s", signal)
            await self._emit_trade_error(signal, None)
            return None

        LOG.info(
            "📊 Order placed: %s %s @ digit=%d stake=%.2f (id=%s)",
            order.symbol,
            order.direction,
            signal.predicted_digit,
            order.stake,
            order.order_id,
        )
        return order

    async def _resolve_trade(
        self, trade: Trade, order: Order
    ) -> Trade | None:
        """Resolve a trade outcome.

        In a live setup this would subscribe to broker contract-update
        events.  For now it wraps the order metadata into a completed
        Trade record with placeholder values for payout/exit_price.

        Subclasses should override this method for real trade resolution.
        """
        # Default: mark as completed with break-even outcome.
        # A real broker integration would update these fields from
        # a contract-settled event.
        trade.is_completed = True
        trade.exit_price = trade.entry_price  # placeholder
        trade.payout = 0.0
        trade.profit = 0.0
        trade.is_win = False
        trade.metadata["order_status"] = order.status
        return trade

    # ------------------------------------------------------------------
    #  Stake resolution
    # ------------------------------------------------------------------

    def _resolve_stake(self, signal: Signal) -> float:
        """Determine the stake for a signal.

        Priority:
          1. Signal metadata override (``stake`` key)
          2. Settings default (``BROKER_DEFAULT_STAKE``)
          3. Fallback to 0.35
        """
        stake_override = signal.metadata.get("stake")
        if stake_override is not None and isinstance(stake_override, (int, float)):
            return float(stake_override)
        return self.default_stake

    # ------------------------------------------------------------------
    #  Event emitters
    # ------------------------------------------------------------------

    async def _emit_trade_open(self, trade: Trade) -> None:
        """Fire ``on_trade_open`` callback + event bus."""
        event_bus.publish("trade_opened", trade=trade)
        if self._on_trade_open is not None:
            try:
                await self._on_trade_open(trade)
            except Exception:
                LOG.exception("on_trade_open callback failed")

    async def _emit_trade_complete(self, trade: Trade) -> None:
        """Fire ``on_trade_complete`` callback + event bus."""
        event_bus.publish("trade_closed", trade=trade)
        if self._on_trade_complete is not None:
            try:
                await self._on_trade_complete(trade)
            except Exception:
                LOG.exception("on_trade_complete callback failed")

    async def _emit_trade_error(
        self, signal: Signal, exc: Exception | None
    ) -> None:
        """Fire ``on_trade_error`` callback + event bus."""
        event_bus.publish("trade_error", signal=signal, exception=exc)
        if self._on_trade_error is not None:
            try:
                await self._on_trade_error(signal, exc)
            except Exception:
                LOG.exception("on_trade_error callback failed")

    # ------------------------------------------------------------------
    #  Utilities
    # ------------------------------------------------------------------

    def reset_lifecycle(self) -> None:
        """Reset the trade lifecycle tracker (e.g. at start of new cycle)."""
        self.lifecycle = TradeLifecycle()

    def __repr__(self) -> str:
        return (
            f"TradeExecutor(broker={type(self.broker).__name__}, "
            f"stake={self.default_stake}, "
            f"contract={self.contract_type}, "
            f"middleware={len(self.middleware.items)}, "
            f"trades={self.lifecycle.wins + self.lifecycle.losses}, "
            f"pnl={self.lifecycle.total_profit:.2f})"
        )
