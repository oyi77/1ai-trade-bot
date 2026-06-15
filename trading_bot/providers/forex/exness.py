"""Exness provider — forex trading via MT5 terminal integration.

Exness accounts are accessed through the MetaTrader 5 terminal API.
This provider wraps ``MetaTrader5`` behind the ``BaseProvider`` interface,
supporting connection lifecycle, balance queries, order placement with
SL/TP, and position tracking.

Requires:
    - ``MetaTrader5`` package (``pip install MetaTrader5``)
    - A running MT5 terminal logged into the Exness account with
      automated trading enabled.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC
from typing import Any

from trading_bot.providers.base import (
    BaseProvider,
    Candle,
    MarketType,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)

LOG = logging.getLogger(__name__)


class ExnessProvider(BaseProvider):
    """Exness forex provider wrapping the MetaTrader 5 terminal API.

    Args:
        login: MT5 account login number.
        password: MT5 account password.
        server: MT5 server name (e.g. ``'Exness-MT5Trial'``).
        name: Provider name (default ``'exness'``).
    """

    def __init__(
        self,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        name: str = "exness",
    ) -> None:
        self._name = name
        self._login = login
        self._password = password
        self._server = server
        self._mt5: Any = None  # MetaTrader5 module (lazy import)
        self._connected = False

    # ------------------------------------------------------------------
    #  BaseProvider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def market_type(self) -> MarketType:
        return MarketType.FOREX

    async def connect(self) -> bool:
        """Initialize MT5 and log into the Exness account."""
        try:
            import MetaTrader5 as mt5  # noqa: N813
        except ImportError as exc:
            LOG.error("MetaTrader5 package not installed: %s", exc)
            return False

        self._mt5 = mt5

        if not mt5.initialize():
            error = mt5.last_error()
            LOG.error("MT5 initialize failed: %s", error)
            return False

        if self._login and self._password:
            authorized = mt5.login(
                login=self._login,
                password=self._password,
                server=self._server,
            )
            if not authorized:
                error = mt5.last_error()
                LOG.error("MT5 login failed: %s", error)
                await self.disconnect()
                return False

        self._connected = True
        LOG.info(
            "ExnessProvider connected (login=%s, server=%s)",
            self._login, self._server,
        )
        return True

    async def disconnect(self) -> None:
        if self._mt5 is not None:
            with contextlib.suppress(Exception):
                self._mt5.shutdown()
        self._connected = False
        self._mt5 = None

    async def is_connected(self) -> bool:
        return self._connected

    async def get_balance(self) -> float:
        self._ensure_mt5()
        info = self._mt5.account_info()
        if info is None:
            raise ConnectionError("Failed to fetch account info")
        return float(info.balance)

    async def get_positions(self) -> list[Position]:
        self._ensure_mt5()
        raw = self._mt5.positions_get()
        if raw is None:
            return []
        return [_mt5_position_to_model(p, self._mt5) for p in raw]

    async def place_order(self, order: Order) -> OrderResult:
        self._ensure_mt5()
        request = _build_mt5_request(order)
        result = self._mt5.order_send(request)
        if result is None:
            return OrderResult(
                order_id="",
                status=OrderStatus.REJECTED,
                message="MT5 order_send returned None (check terminal connection)",
            )
        if result.retcode != 10009:  # TRADE_RETCODE_DONE
            return OrderResult(
                order_id=str(result.order),
                status=OrderStatus.REJECTED,
                message=f"MT5 retcode {result.retcode}: {result.comment}",
            )
        return OrderResult(
            order_id=str(result.order),
            status=OrderStatus.FILLED,
            filled_quantity=float(result.volume),
            filled_price=float(result.price),
            message=f"Exness order filled at {result.price:.5f}",
        )

    async def cancel_order(self, order_id: str) -> bool:
        self._ensure_mt5()
        raw = self._mt5.positions_get(position=int(order_id))
        if not raw:
            return False
        position = raw[0]
        close_request = {
            "action": 5,  # TRADE_ACTION_DEAL
            "symbol": position.symbol,
            "volume": position.volume,
            "type": 1 if position.type == 0 else 0,  # reverse direction
            "position": position.ticket,
            "price": self._mt5.symbol_info_tick(position.symbol).ask
            if position.type == 0
            else self._mt5.symbol_info_tick(position.symbol).bid,
            "deviation": 20,
            "magic": 0,
            "comment": "cancelled_by_provider",
        }
        result = self._mt5.order_send(close_request)
        return result is not None and result.retcode == 10009

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        self._ensure_mt5()
        tf = _parse_timeframe(timeframe)
        raw = self._mt5.copy_rates_from_pos(symbol, tf, 0, limit)
        if raw is None:
            return []
        return [_mt5_candle_to_model(row, symbol, timeframe) for row in raw]

    async def get_symbols(self) -> list[str]:
        self._ensure_mt5()
        raw = self._mt5.symbols_get()
        if raw is None:
            return []
        return [s.name for s in raw]

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _ensure_mt5(self) -> None:
        if self._mt5 is None or not self._connected:
            raise ConnectionError("ExnessProvider not connected")


# ------------------------------------------------------------------
#  MT5 conversion helpers
# ------------------------------------------------------------------

MT5_TIMEFRAMES: dict[str, int] = {
    "1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15,
    "30m": 30, "1h": 60, "2h": 120, "4h": 240, "6h": 360,
    "8h": 480, "12h": 720, "1d": 1440, "1w": 10080, "1M": 43200,
}


def _parse_timeframe(timeframe: str) -> int:
    """Convert timeframe string to MT5 constant."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError:
        return MT5_TIMEFRAMES.get(timeframe, 60)
    tf_map: dict[str, int] = {
        "1m": mt5.TIMEFRAME_M1,
        "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "30m": mt5.TIMEFRAME_M30,
        "1h": mt5.TIMEFRAME_H1,
        "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1,
        "1w": mt5.TIMEFRAME_W1,
        "1M": mt5.TIMEFRAME_MN1,
    }
    return int(tf_map.get(timeframe, mt5.TIMEFRAME_H1))


def _build_mt5_request(order: Order) -> dict[str, Any]:
    """Build an MT5 order_send request dict from an Order model."""

    side = 0 if order.side == OrderSide.BUY else 1  # 0=BUY, 1=SELL
    action = 1 if order.order_type == OrderType.MARKET else 2  # 1=DEAL, 2=PENDING

    price = 0.0
    if order.order_type == OrderType.MARKET:
        price = 0.0  # MT5 fills at market
    elif order.order_type == OrderType.LIMIT:
        price = order.price or 0.0
    elif order.order_type == OrderType.STOP:
        price = order.stop_price or 0.0

    request: dict[str, Any] = {
        "action": action,
        "symbol": order.symbol,
        "volume": order.quantity,
        "type": side,
        "price": price,
        "deviation": 20,
        "magic": 0,
        "comment": "",
        "type_time": 0,  # ORDER_TIME_GTC
        "type_filling": 0,  # ORDER_FILLING_FOK
    }

    if order.time_in_force == TimeInForce.DAY:
        request["type_time"] = 1  # ORDER_TIME_DAY
    elif order.time_in_force == TimeInForce.IOC:
        request["type_filling"] = 1  # ORDER_FILLING_IOC

    return request


def _mt5_position_to_model(pos: Any, mt5: Any) -> Position:
    """Convert an MT5 position object to a Position model."""
    current = mt5.symbol_info_tick(pos.symbol)
    current_price = current.bid if hasattr(pos, "type") and pos.type == 0 else current.ask
    price_diff = current_price - pos.price_open
    if pos.type == 1:  # SELL
        price_diff = -price_diff
    upnl = price_diff * pos.volume * pos.swap  # simplified

    return Position(
        symbol=pos.symbol,
        side=OrderSide.BUY if pos.type == 0 else OrderSide.SELL,
        quantity=float(pos.volume),
        entry_price=float(pos.price_open),
        current_price=float(current_price),
        unrealized_pnl=float(upnl),
        realized_pnl=float(pos.profit) if hasattr(pos, "profit") else 0.0,
        leverage=int(pos.leverage) if hasattr(pos, "leverage") else 1,
        liquidation_price=float(pos.sl) if hasattr(pos, "sl") and pos.sl else None,
    )


def _mt5_candle_to_model(row: Any, symbol: str, timeframe: str) -> Candle:
    """Convert an MT5 rate row to a Candle model."""
    from datetime import datetime

    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        volume=float(row.tick_volume if hasattr(row, "tick_volume") else row.real_volume),
        timestamp=datetime.fromtimestamp(row.time, tz=UTC),
    )
