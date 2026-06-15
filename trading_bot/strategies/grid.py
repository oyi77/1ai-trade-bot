"""Grid trading strategy — places buy/sell orders at predefined price levels.

The grid is defined by a list of price levels.  When the market price crosses
a level a corresponding order is placed (buy at support levels, sell at
resistance levels).  Each level can have a take-profit and stop-loss in pips.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from trading_bot.providers.base import BaseProvider, Order, OrderSide, OrderType
from trading_bot.strategies.base import BaseStrategy, StrategySignal

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Grid-level data model
# ---------------------------------------------------------------------------


@dataclass
class GridLevel:
    """A single grid line with its current order state.

    Attributes:
        price: The price of this grid line.
        order_id: Active order ID when an order is pending at this level,
            or ``None``.
        is_buy: ``True`` for buy (support) levels, ``False`` for sell
            (resistance) levels.
        take_profit: Take-profit price offset in price units, or ``None``.
        stop_loss: Stop-loss price offset in price units, or ``None``.
        filled: ``True`` when the order at this level has been filled.
    """

    price: float
    order_id: str | None = None
    is_buy: bool = True
    take_profit: float | None = None
    stop_loss: float | None = None
    filled: bool = False


# ---------------------------------------------------------------------------
#  Grid configuration
# ---------------------------------------------------------------------------


@dataclass
class GridConfig:
    """Parameters that control grid behaviour.

    Attributes:
        levels: Grid price levels (ascending order).
        order_size: Order quantity in units (same unit as the provider's
            ``place_order`` quantity).
        take_profit_pips: TP distance in pips, or ``None`` for no TP.
        stop_loss_pips: SL distance in pips, or ``None`` for no SL.
        max_active_orders: Cap on concurrently active orders.
            ``0`` means unlimited.
        cooldown_ticks: Number of bars to wait after a fill before
            re-arming the same level.
    """

    levels: list[float]
    order_size: float
    take_profit_pips: float | None = None
    stop_loss_pips: float | None = None
    max_active_orders: int = 0
    cooldown_ticks: int = 5


# ---------------------------------------------------------------------------
#  Grid strategy
# ---------------------------------------------------------------------------


class GridStrategy(BaseStrategy):
    """Grid trading strategy.

    Places limit orders at predefined price levels and manages their
    lifecycle.  Use ``on_start`` to initialise grid levels, then call
    ``analyze`` on each candle to react to price movements.
    """

    def __init__(
        self,
        provider: BaseProvider,
        params: dict | None = None,
    ) -> None:
        super().__init__(provider, params)
        cfg_raw = self._params
        self._config = GridConfig(
            levels=sorted(cfg_raw.get("levels", [])),
            order_size=float(cfg_raw.get("order_size", 0.01)),
            take_profit_pips=(
                float(cfg_raw["take_profit_pips"])
                if cfg_raw.get("take_profit_pips") is not None
                else None
            ),
            stop_loss_pips=(
                float(cfg_raw["stop_loss_pips"])
                if cfg_raw.get("stop_loss_pips") is not None
                else None
            ),
            max_active_orders=int(cfg_raw.get("max_active_orders", 0)),
            cooldown_ticks=int(cfg_raw.get("cooldown_ticks", 5)),
        )
        self._levels: list[GridLevel] = []
        self._cooldown_counters: dict[float, int] = {}
        self._last_price: float | None = None

    # ── BaseStrategy ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "grid"

    async def on_start(self) -> None:
        """Build internal grid level list from config."""
        cfg = self._config
        n = len(cfg.levels)
        # Alternate buy/sell — odd indices are buy (support),
        # even indices are sell (resistance) so the grid alternates.
        # First level (index 0) is the lowest → buy.
        self._levels = []
        for i, price in enumerate(cfg.levels):
            is_buy = (i % 2 == 0)
            self._levels.append(
                GridLevel(
                    price=price,
                    is_buy=is_buy,
                    take_profit=cfg.take_profit_pips,
                    stop_loss=cfg.stop_loss_pips,
                )
            )
        self._cooldown_counters = {p: 0 for p in cfg.levels}
        LOG.info(
            "GridStrategy initialised: %d levels, size=%.4f",
            n,
            cfg.order_size,
        )

    async def on_stop(self) -> None:
        """Cancel all active grid orders."""
        for level in self._levels:
            if level.order_id is not None:
                await self._provider.cancel_order(level.order_id)
                level.order_id = None
        LOG.info("GridStrategy stopped — all orders cancelled.")

    async def analyze(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> StrategySignal | None:
        """Check each grid level against the current price.

        When price crosses a level, place an order and return a signal.
        Only the strongest crossing level per call triggers a signal.
        """
        candles = await self._fetch_candles(symbol, timeframe, limit=3)
        if not candles:
            return None

        current_price = candles[-1].close
        self._last_price = current_price

        # Decrement cooldown counters.
        for lvl_price in list(self._cooldown_counters):
            if self._cooldown_counters[lvl_price] > 0:
                self._cooldown_counters[lvl_price] -= 1

        # Count active orders.
        active = sum(1 for lv in self._levels if lv.order_id is not None)
        cfg = self._config
        if cfg.max_active_orders > 0 and active >= cfg.max_active_orders:
            LOG.debug("Grid at max active orders (%d)", active)
            return None

        # Find the strongest crossing level.
        crossing = self._find_crossing(current_price)
        if crossing is None:
            return None

        level, direction = crossing

        # Check cooldown.
        if self._cooldown_counters.get(level.price, 0) > 0:
            LOG.debug("Level %.5f in cooldown", level.price)
            return None

        # Place limit order at this level.
        order = Order(
            symbol=symbol,
            side=direction,
            order_type=OrderType.LIMIT,
            quantity=cfg.order_size,
            price=level.price,
        )

        result = await self._provider.place_order(order)
        if result.status.value in ("filled", "pending"):
            level.order_id = result.order_id
            if result.status.value == "filled":
                level.filled = True
                self._cooldown_counters[level.price] = cfg.cooldown_ticks

            return StrategySignal(
                symbol=symbol,
                strategy_name=self.name,
                direction=direction,
                confidence=0.5,
                price=current_price,
                metadata={
                    "grid_price": level.price,
                    "order_id": result.order_id,
                    "order_status": result.status.value,
                },
            )

        LOG.warning("Grid order rejected at %.5f: %s", level.price, result)
        return None

    # ── public helpers ────────────────────────────────────────────────

    async def cancel_all(self) -> int:
        """Cancel every active grid order.

        Returns:
            Number of orders that were cancelled.
        """
        count = 0
        for level in self._levels:
            if level.order_id is not None:
                ok = await self._provider.cancel_order(level.order_id)
                if ok:
                    count += 1
                level.order_id = None
        return count

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of the current grid state."""
        return {
            "levels": [
                {
                    "price": lv.price,
                    "is_buy": lv.is_buy,
                    "order_id": lv.order_id,
                    "filled": lv.filled,
                }
                for lv in self._levels
            ],
            "active_orders": sum(
                1 for lv in self._levels if lv.order_id is not None
            ),
            "last_price": self._last_price,
        }

    # ── internal ──────────────────────────────────────────────────────

    def _find_crossing(
        self,
        current_price: float,
    ) -> tuple[GridLevel, OrderSide] | None:
        """Find the closest grid level that price just crossed.

        A level is considered crossed when the current price moves past it
        and the level has no active order and is not already filled.
        Returns the level and the order side (BUY for support, SELL for
        resistance).
        """
        best: tuple[GridLevel, OrderSide] | None = None
        best_dist: float = float("inf")

        for level in self._levels:
            if level.order_id is not None or level.filled:
                continue

            # Level was below price → resistance (sell) zone.
            # Level was above price → support (buy) zone.
            distance = abs(current_price - level.price)

            if distance < best_dist:
                best_dist = distance
                side = OrderSide.BUY if level.is_buy else OrderSide.SELL
                best = (level, side)

        # Only signal when the closest level is *very* close (within 0.5%
        # of current price) — otherwise wait for price to reach it.
        if best is not None and best_dist / max(current_price, 1e-10) > 0.005:
            return None

        return best
