"""
CCXT Multi-Exchange Broker — trade on Binance, Bitget, Bybit, OKX, KuCoin, etc.

Implements BaseBroker interface via CCXT unified API.
Supports spot, futures, and margin trading across 100+ exchanges.
"""

from __future__ import annotations

import logging
from typing import Any

import ccxt.async_support as ccxt_async

from tradebot.brokers.base import (
    BaseBroker,
    BrokerPlatform,
    TradeDirection,
    TradeResult,
    TradeStatus,
)
from tradebot.config import settings

LOG = logging.getLogger(__name__)

# ── Exchange Configuration ────────────────────────────────────────────

SUPPORTED_EXCHANGES: dict[str, str] = {
    "binance": "Binance",
    "bitget": "Bitget",
    "bybit": "Bybit",
    "okx": "OKX",
    "kucoin": "KuCoin",
    "mexc": "MEXC",
    "gate": "Gate.io",
    "bingx": "BingX",
    "htx": "HTX (Huobi)",
    "coinbase": "Coinbase",
}

# CCXT symbol mapping: internal → CCXT standard
CCXT_SYMBOL_MAP: dict[str, dict[str, str]] = {
    "binance": {"BTC-USD": "BTC/USDT", "ETH-USD": "ETH/USDT", "SOL-USD": "SOL/USDT"},
    "bitget": {"BTC-USD": "BTC/USDT", "ETH-USD": "ETH/USDT", "SOL-USD": "SOL/USDT"},
    "bybit": {"BTC-USD": "BTC/USDT", "ETH-USD": "ETH/USDT", "SOL-USD": "SOL/USDT"},
    "okx": {"BTC-USD": "BTC/USDT", "ETH-USD": "ETH/USDT", "SOL-USD": "SOL/USDT"},
    "kucoin": {"BTC-USD": "BTC-USDT", "ETH-USD": "ETH-USDT", "SOL-USD": "SOL-USDT"},
}


def _get_ccxt_symbol(exchange_id: str, symbol: str) -> str:
    """Map internal symbol to exchange-specific CCXT format."""
    mapping = CCXT_SYMBOL_MAP.get(exchange_id, {}).get(symbol, "")
    if mapping:
        return mapping
    # Auto-derive: BTC-USD → BTC/USDT
    return symbol.replace("-USD", "/USDT").replace("-", "/")


# ── CCXT Broker ───────────────────────────────────────────────────────

class CCXTBroker(BaseBroker):
    """Trade on any CCXT-supported exchange via unified API.

    Supports: Binance, Bitget, Bybit, OKX, KuCoin, MEXC, Gate, HTX, Coinbase

    Usage:
        broker = CCXTBroker(exchange="bitget", api_key="...", secret="...")
        async with broker:
            balance = await broker.get_balance()
            result = await broker.place_trade("BTC-USD", TradeDirection.CALL, 10.0)
    """

    def __init__(
        self,
        exchange: str = "bitget",
        api_key: str = "",
        secret: str = "",
        password: str = "",
        sandbox: bool = True,
    ) -> None:
        self._exchange_id = exchange.lower()
        self._api_key = api_key or getattr(settings, f"{exchange.upper()}_API_KEY", "")
        self._secret = secret or getattr(settings, f"{exchange.upper()}_SECRET", "")
        self._password = password or getattr(settings, f"{exchange.upper()}_PASSWORD", "")
        self._sandbox = sandbox
        self._client: ccxt_async.Exchange | None = None
        self._connected = False

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.CEX

    @property
    def exchange_name(self) -> str:
        return SUPPORTED_EXCHANGES.get(self._exchange_id, self._exchange_id.title())

    # ── Connection ─────────────────────────────────────────────────

    async def connect(self) -> None:
        if not ccxt_async:
            raise ImportError("ccxt.async_support not available")

        exchange_class = getattr(ccxt_async, self._exchange_id, None)
        if not exchange_class:
            raise ValueError(f"Exchange not found: {self._exchange_id}. Available: {list(SUPPORTED_EXCHANGES)}")

        config: dict[str, Any] = {
            "apiKey": self._api_key,
            "secret": self._secret,
            "enableRateLimit": True,
        }
        if self._password:
            config["password"] = self._password

        # Sandbox/testnet
        if self._sandbox:
            if self._exchange_id == "binance":
                config["urls"] = {"api": {"public": "https://testnet.binance.vision"}}
            elif self._exchange_id == "bitget":
                config["urls"] = {"api": {"public": "https://api-sandbox.bitget.com"}}
            elif self._exchange_id == "bybit":
                config["urls"] = {"api": {"public": "https://api-testnet.bybit.com"}}

        self._client = exchange_class(config)
        await self._client.load_markets()
        self._connected = True
        LOG.info("✅ Connected to %s (%s) [%s]",
                 self.exchange_name, self._exchange_id, "sandbox" if self._sandbox else "live")

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
        self._connected = False

    # ── Balance ────────────────────────────────────────────────────

    async def get_balance(self) -> float | None:
        if not self._client:
            return None
        try:
            balance = await self._client.fetch_balance()
            total = balance.get("total", {})
            usdt = total.get("USDT", 0)
            return float(usdt) if usdt else 0.0
        except Exception as e:
            LOG.error("Balance fetch failed: %s", e)
            return None

    # ── Trade Execution ─────────────────────────────────────────────

    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
        amount: float,
        duration: int | None = None,
    ) -> TradeResult:
        if not self._client:
            return self._error_result(symbol, direction, amount, "Not connected")

        ccxt_symbol = _get_ccxt_symbol(self._exchange_id, symbol)
        side = "buy" if direction == TradeDirection.CALL else "sell"

        try:
            order = await self._client.create_market_order(ccxt_symbol, side, amount)

            return TradeResult(
                platform=BrokerPlatform.CEX,
                order_id=str(order.get("id", "")),
                symbol=symbol,
                direction=direction,
                amount=amount,
                status=TradeStatus.OPENED,
                metadata={
                    "exchange": self._exchange_id,
                    "filled": order.get("filled", 0),
                    "price": order.get("price", 0),
                    "cost": order.get("cost", 0),
                },
            )
        except Exception as e:
            LOG.error("Trade failed on %s: %s", self._exchange_id, e)
            return self._error_result(symbol, direction, amount, str(e))

    def _error_result(self, symbol: str, direction: TradeDirection, amount: float, error: str) -> TradeResult:
        return TradeResult(
            platform=BrokerPlatform.CEX,
            order_id="",
            symbol=symbol,
            direction=direction,
            amount=amount,
            status=TradeStatus.ERROR,
            error=error,
        )

    # ── Market Data ─────────────────────────────────────────────────

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles via CCXT."""
        if not self._client:
            return []
        try:
            ccxt_symbol = _get_ccxt_symbol(self._exchange_id, symbol)
            ohlcv = await self._client.fetch_ohlcv(ccxt_symbol, timeframe, limit=limit)
            return [
                {"timestamp": row[0], "open": row[1], "high": row[2],
                 "low": row[3], "close": row[4], "volume": row[5]}
                for row in ohlcv
            ]
        except Exception as e:
            LOG.error("OHLCV fetch failed: %s", e)
            return []

    async def fetch_ticker(self, symbol: str) -> dict[str, Any] | None:
        """Fetch current ticker."""
        if not self._client:
            return None
        try:
            ccxt_symbol = _get_ccxt_symbol(self._exchange_id, symbol)
            return await self._client.fetch_ticker(ccxt_symbol)
        except Exception as e:
            LOG.error("Ticker fetch failed: %s", e)
            return None


# ── Factory ────────────────────────────────────────────────────────────

def get_ccxt_broker(
    exchange: str = "bitget",
    api_key: str = "",
    secret: str = "",
    sandbox: bool = True,
) -> CCXTBroker:
    """Create a CCXT broker for any supported exchange."""
    return CCXTBroker(exchange=exchange, api_key=api_key, secret=secret, sandbox=sandbox)
