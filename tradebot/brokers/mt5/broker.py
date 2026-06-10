"""
MT5Broker — Broker ABC implementation for MetaTrader 5.
=========================================================

Wraps the MetaTrader5 terminal API behind the async ``Broker`` interface.
Supports connection lifecycle, tick subscription, balance queries, and
order placement with SL/TP.

Requires ``MetaTrader5`` package (``pip install MetaTrader5``) and
a running MT5 terminal with automated trading enabled.
"""

from __future__ import annotations

import asyncio
import logging

from tradebot.brokers.base import (
    BaseBroker,
    BrokerPlatform,
    TradeDirection,
    TradeResult,
    TradeStatus,
)
from tradebot.config import settings
from tradebot.models import Balance, Order

LOG = logging.getLogger(__name__)
class MT5Broker(BaseBroker):
    """MetaTrader 5 broker adapter implementing the async Broker ABC.

    Parameters
    ----------
    login : int, optional
        MT5 account login. Falls back to ``settings.MT5_LOGIN``.
    password : str, optional
        MT5 account password. Falls back to ``settings.MT5_PASSWORD``.
    server : str, optional
        MT5 broker server. Falls back to ``settings.MT5_SERVER``.
    path : str, optional
        Path to terminal.exe. Falls back to ``settings.MT5_PATH``.
    dry_run : bool, optional
        Paper-trade mode. Falls back to ``settings.BROKER_DRY_RUN``.
    """

    def __init__(
        self,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        path: str | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self._login = login or int(settings.MT5_LOGIN) if settings.MT5_LOGIN else None
        self._password = password or settings.MT5_PASSWORD or None
        self._server = server or settings.MT5_SERVER or None
        self._path = path or settings.MT5_PATH or None
        self._dry_run = dry_run if dry_run is not None else settings.BROKER_DRY_RUN
        self._connected = False
        self._mt5 = None  # MetaTrader5 module (lazy import)

    # ── Connection lifecycle ──

    async def connect(self) -> bool:
        """Initialize MT5 Python API and log into the terminal."""
        try:
            import MetaTrader5 as mt5  # noqa: N813
        except ImportError:
            LOG.error("MetaTrader5 package not installed. Run: pip install MetaTrader5")
            return False

        self._mt5 = mt5

        # Initialize the terminal connection
        kwargs = {}
        if self._path:
            kwargs["path"] = self._path

        initialized = await asyncio.to_thread(mt5.initialize, **kwargs)
        if not initialized:
            LOG.error("MT5 initialize() failed: %s", mt5.last_error())
            return False

        # Login if credentials are provided
        if self._login and self._password:
            login_kwargs = {
                "login": self._login,
                "password": self._password,
            }
            if self._server:
                login_kwargs["server"] = self._server
            authorized = await asyncio.to_thread(mt5.login, **login_kwargs)
            if not authorized:
                LOG.error("MT5 login() failed: %s", mt5.last_error())
                return False

        self._connected = True
        LOG.info(
            "MT5Broker connected | login=%s server=%s dry_run=%s",
            self._login, self._server, self._dry_run,
        )
        return True

    async def close(self) -> None:
        """Close connection (alias for disconnect)."""
        await self.disconnect()

    async def disconnect(self) -> None:
        """Shut down the MT5 API connection."""
        if self._mt5 is not None:
            await asyncio.to_thread(self._mt5.shutdown)
        self._connected = False
        self._mt5 = None
        LOG.info("MT5Broker disconnected")

    # ── Properties ──

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.MT5

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Balance ──

    async def get_balance(self) -> Balance | None:
        """Fetch current MT5 account balance via AccountInfo."""
        if not self._connected or self._mt5 is None:
            return None
        try:
            info = await asyncio.to_thread(self._mt5.account_info)
            if info is None:
                LOG.warning("MT5 account_info() returned None")
                return None
            return Balance(
                balance=info.balance,
                currency=info.currency,
            )
        except Exception as exc:
            LOG.error("Failed to fetch MT5 balance: %s", exc)
            return None

    # ── Order placement ──

    async def place_order(
        self,
        symbol: str,
        contract_type: str,
        barrier: int,
        stake: float,
        **kwargs,
    ) -> Order | None:
        """Place an MT5 order with optional SL/TP.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g. "XAUUSD", "EURUSD").
        contract_type : str
            Currently mapped to MT5 order type:
            ``"BUY"`` / ``"buy"`` → ORDER_TYPE_BUY,
            ``"SELL"`` / ``"sell"`` → ORDER_TYPE_SELL.
        barrier : int
            (Ignored for MT5; kept for ABC compatibility.)
        stake : float
            Order volume in lots.
        **kwargs
            Extra parameters forwarded to the MT5 order request:
            - sl : float — stop loss price
            - tp : float — take profit price
            - magic : int — EA magic number
            - comment : str — order comment
            - slippage : int — max slippage in points (default: MT5_SLIPPAGE)
        """
        if not self._connected or self._mt5 is None:
            LOG.error("MT5 not connected, cannot place order")
            return None

        order_type_str = contract_type.upper()
        if order_type_str == "BUY":
            order_type = self._mt5.ORDER_TYPE_BUY
        elif order_type_str == "SELL":
            order_type = self._mt5.ORDER_TYPE_SELL
        else:
            LOG.error("Unsupported MT5 order type: %s", contract_type)
            return None

        # Fetch current price for order
        tick = await asyncio.to_thread(self._mt5.symbol_info_tick, symbol)
        if tick is None:
            LOG.error("Cannot get tick for symbol %s", symbol)
            return None

        price = tick.ask if order_type == self._mt5.ORDER_TYPE_BUY else tick.bid

        if self._dry_run:
            LOG.info(
                "📝 PAPER MT5: %s %s %.2f lots @ %.5f | SL=%s TP=%s",
                symbol, order_type_str, stake, price,
                kwargs.get("sl", "none"), kwargs.get("tp", "none"),
            )
            return Order(
                order_id=f"paper_{symbol}_{int(asyncio.get_running_loop().time() * 1000)}",
                symbol=symbol,
                contract_type=order_type_str,
                direction=order_type_str,
                stake=stake,
                barrier=0,
                status="PAPER_FILLED",
            )

        # Build the trade request
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": stake,
            "type": order_type,
            "price": price,
            "sl": kwargs.get("sl", 0.0),
            "tp": kwargs.get("tp", 0.0),
            "deviation": kwargs.get("slippage", int(settings.MT5_SLIPPAGE)),
            "magic": kwargs.get("magic", int(settings.MT5_MAGIC_NUMBER)),
            "comment": kwargs.get("comment", "tradebot"),
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        result = await asyncio.to_thread(self._mt5.order_send, request)
        if result is None or result.retcode != self._mt5.TRADE_RETCODE_DONE:
            err_msg = f"MT5 order failed: retcode={getattr(result, 'retcode', 'N/A')}"
            LOG.error(err_msg)
            return None

        LOG.info(
            "✅ MT5 ORDER FILLED: %s %s %.2f lots @ %.5f (order=%d)",
            symbol, order_type_str, stake, price, result.order,
        )
        return Order(
            order_id=str(result.order),
            symbol=symbol,
            contract_type=order_type_str,
            direction=order_type_str,
            stake=stake,
            barrier=0,
            status="FILLED",
        )

    # ── Tick subscription ──


    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
        amount: float,
        duration: int | None = None,
    ) -> TradeResult:
        """Place a trade (wrapper for place_order)."""
        order_type = 0 if direction == TradeDirection.CALL else 1  # BUY=0, SELL=1
        lots = amount / 100000  # Convert stake to lots (simplified)

        result = await self.place_order(
            symbol=symbol,
            order_type=order_type,
            volume=lots,
        )

        if not result:
            return TradeResult(
                platform=self.platform,
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                status=TradeStatus.REJECTED,
                error="Order rejected by MT5",
            )

        return TradeResult(
            platform=self.platform,
            order_id=str(result.get("order", "")),
            symbol=symbol,
            direction=direction,
            amount=amount,
            duration=duration,
            status=TradeStatus.OPENED,
            metadata=result,
        )

async def subscribe_ticks(self, symbol: str) -> bool:
        """Subscribe to real-time MT5 ticks for a symbol.

        This enables MT5's tick cache for the symbol. Actual streaming
        is handled by polling ``symbol_info_tick`` from the executor loop.
        """
        if not self._connected or self._mt5 is None:
            return False
        try:
            # Ensure the symbol is visible in MarketWatch
            selected = await asyncio.to_thread(self._mt5.symbol_select, symbol, True)
            if not selected:
                LOG.warning("Failed to select symbol %s: %s", symbol, self._mt5.last_error())
                return False
            LOG.info("Subscribed to MT5 ticks: %s", symbol)
            return True
        except Exception as exc:
            LOG.error("Error subscribing to MT5 ticks: %s", exc)
            return False
