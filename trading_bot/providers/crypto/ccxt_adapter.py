"""CCXT Adapter — unified crypto exchange provider via CCXT.

Wraps ``ccxt.async_support`` behind the ``BaseProvider`` interface,
supporting spot, futures, and margin trading across 100+ exchanges
(Binance, Bybit, OKX, KuCoin, Bitget, etc.).

Uses the exchange's sandbox/testnet when available.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
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
)

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Exchange configuration
# ---------------------------------------------------------------------------

SUPPORTED_EXCHANGES: dict[str, str] = {
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
    "bitget": "Bitget",
    "kucoin": "KuCoin",
    "kucoinfutures": "KuCoin Futures",
    "gate": "Gate.io",
    "mexc": "MEXC",
    "bingx": "BingX",
    "coinbase": "Coinbase Advanced",
}

# ---------------------------------------------------------------------------
#  Provider
# ---------------------------------------------------------------------------


class CCXTProvider(BaseProvider):
    """Trade on any CCXT-supported exchange via unified API.

    Args:
        exchange_id: Exchange identifier (e.g. ``'binance'``, ``'bybit'``).
        api_key: API key for the exchange.
        secret: API secret for the exchange.
        password: API password/passphrase (required for some exchanges).
        sandbox: Use sandbox/testnet when available.
        name: Provider name (default ``'<exchange_id>'``).
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        secret: str = "",
        password: str = "",
        sandbox: bool = False,
        name: str | None = None,
    ) -> None:
        self._exchange_id = exchange_id
        self._api_key = api_key
        self._secret = secret
        self._password = password
        self._sandbox = sandbox
        self._name = name or exchange_id
        self._exchange: Any = None  # ccxt Exchange instance
        self._connected = False

    # ------------------------------------------------------------------
    #  BaseProvider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def market_type(self) -> MarketType:
        return MarketType.CRYPTO

    async def connect(self) -> bool:
        """Initialize the CCXT exchange client and load markets."""
        import ccxt.async_support as ccxt_async  # type: ignore[import-not-found]

        exchange_class = getattr(ccxt_async, self._exchange_id, None)
        if exchange_class is None:
            LOG.error("Unsupported exchange: %s", self._exchange_id)
            return False

        config: dict[str, Any] = {
            "apiKey": self._api_key,
            "secret": self._secret,
            "password": self._password,
            "enableRateLimit": True,
        }
        if self._sandbox:
            config["sandbox"] = True

        self._exchange = exchange_class(config)

        try:
            await self._exchange.load_markets()
        except Exception as exc:
            LOG.error("Failed to load markets for %s: %s", self._exchange_id, exc)
            await self.disconnect()
            return False

        self._connected = True
        LOG.info(
            "CCXTProvider connected: %s (sandbox=%s)",
            self._exchange_id, self._sandbox,
        )
        return True

    async def disconnect(self) -> None:
        if self._exchange is not None:
            with contextlib.suppress(Exception):
                await self._exchange.close()
        self._connected = False
        self._exchange = None

    async def is_connected(self) -> bool:
        return self._connected

    async def get_balance(self) -> float:
        self._ensure_connected()
        try:
            balance = await self._exchange.fetch_balance()
            total = balance.get("total", {})
            usd_equivalent = 0.0
            for currency, amount in total.items():
                if amount and amount > 0:
                    try:
                        ticker = await self._exchange.fetch_ticker(
                            f"{currency}/USDT"
                        )
                        usd_equivalent += float(amount) * ticker["last"]
                    except Exception:
                        pass
            return usd_equivalent or balance.get("free", {}).get("USDT", 0.0)
        except Exception as exc:
            LOG.error("Failed to fetch balance: %s", exc)
            return 0.0

    async def get_positions(self) -> list[Position]:
        self._ensure_connected()
        try:
            raw = await self._exchange.fetch_positions()
            return [_ccxt_position_to_model(p) for p in raw if float(p.get("contracts", 0)) > 0]
        except Exception as exc:
            LOG.error("Failed to fetch positions: %s", exc)
            return []

    async def place_order(self, order: Order) -> OrderResult:
        self._ensure_connected()

        symbol = _ccxt_symbol(order.symbol, self._exchange_id)
        side = "buy" if order.side == OrderSide.BUY else "sell"
        order_type = order.order_type.value.lower()  # market, limit, stop

        params: dict[str, Any] = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.leverage > 1:
            with contextlib.suppress(Exception):
                await self._exchange.set_leverage(order.leverage, symbol)
        price = None
        if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            price = order.price
            if order.order_type == OrderType.STOP_LIMIT:
                params["stopPrice"] = order.stop_price
        elif order.order_type == OrderType.STOP:
            params["stopPrice"] = order.stop_price

        try:
            result = await self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=order.quantity,
                price=price,
                params=params,
            )
            status = (
                OrderStatus.FILLED
                if result.get("status") in ("closed", "filled")
                else OrderStatus.OPEN
            )
            return OrderResult(
                order_id=str(result.get("id", "")),
                status=status,
                filled_quantity=float(result.get("filled", 0)),
                filled_price=float(result.get("price", 0) or result.get("average", 0)),
                message=f"CCXT {side} {symbol} via {self._exchange_id}",
            )
        except Exception as exc:
            LOG.error("Order failed on %s: %s", self._exchange_id, exc)
            return OrderResult(
                order_id="",
                status=OrderStatus.REJECTED,
                message=str(exc),
            )

    async def cancel_order(self, order_id: str) -> bool:
        self._ensure_connected()
        try:
            await self._exchange.cancel_order(order_id)
            return True
        except Exception:
            return False

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        self._ensure_connected()
        symbol = _ccxt_symbol(symbol, self._exchange_id)
        tf = _ccxt_timeframe(timeframe)
        try:
            raw = await self._exchange.fetch_ohlcv(symbol, tf, limit=limit)
            return [_ccxt_candle_to_model(row, symbol, timeframe) for row in raw]
        except Exception as exc:
            LOG.error("Failed to fetch candles for %s: %s", symbol, exc)
            return []

    async def get_symbols(self) -> list[str]:
        self._ensure_connected()
        return list(self._exchange.symbols) if self._exchange.symbols else []

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._exchange is None or not self._connected:
            raise ConnectionError("CCXTProvider not connected")


# ---------------------------------------------------------------------------
#  Conversion helpers
# ---------------------------------------------------------------------------


def _ccxt_symbol(symbol: str, exchange_id: str = "") -> str:
    """Map internal symbol to CCXT format."""
    if "/" in symbol:
        return symbol
    if exchange_id in ("binance", "bybit", "okx", "bitget"):
        # Convert BTC -> BTC/USDT, ETH-USD -> ETH/USDT
        if symbol.endswith("-USD"):
            return symbol.replace("-USD", "/USDT")
        return f"{symbol}/USDT"
    return f"{symbol}/USDT"


CCXT_TIMEFRAMES: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h",
    "8h": "8h", "12h": "12h", "1d": "1d", "3d": "3d",
    "1w": "1w", "1M": "1M",
}


def _ccxt_timeframe(timeframe: str) -> str:
    return CCXT_TIMEFRAMES.get(timeframe, "1h")


def _ccxt_candle_to_model(row: list, symbol: str, timeframe: str) -> Candle:
    return Candle(
        symbol=symbol.split("/")[0],
        timeframe=timeframe,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
    )


def _ccxt_position_to_model(p: dict[str, Any]) -> Position:
    pos_side_str = p.get("info", {}).get("positionSide", "BOTH")
    pos_side = (
        OrderSide.BUY
        if pos_side_str == "LONG"
        else OrderSide.SELL
    )
    liq_price = (
        float(p.get("liquidationPrice", 0))
        if p.get("liquidationPrice")
        else None
    )
    return Position(
        symbol=str(p.get("symbol", "")),
        side=pos_side,
        quantity=float(p.get("contracts", 0)),
        entry_price=float(p.get("entryPrice", 0)),
        current_price=float(p.get("markPrice", 0)),
        unrealized_pnl=float(p.get("unrealizedPnl", 0)),
        realized_pnl=float(p.get("realizedPnl", 0)),
        leverage=int(p.get("leverage", 1)),
        liquidation_price=liq_price,
    )
