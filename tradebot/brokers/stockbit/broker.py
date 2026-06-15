"""
Stockbit Broker — Indonesian social trading platform.

Uses REST API with token authentication.
Supports stocks, bonds, mutual funds with social features.

API Base: https://api.stockbit.com
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from tradebot.brokers.base import (
    BaseBroker,
    BrokerPlatform,
    TradeDirection,
    TradeResult,
    TradeStatus,
)
from tradebot.config import settings

LOG = logging.getLogger("tradebot.brokers.stockbit")

STOCKBIT_API_BASE = "https://api.stockbit.com"
STOCKBIT_WS_BASE = "wss://ws.stockbit.com"


class StockbitBroker(BaseBroker):
    """Stockbit broker for Indonesian stock market with social features."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        sandbox: bool = False,
    ):
        super().__init__()
        self.email = email or settings.STOCKBIT_EMAIL
        self.password = password or settings.STOCKBIT_PASSWORD
        self._access_token = access_token
        self._sandbox = sandbox
        self._client: httpx.AsyncClient | None = None
        self._ws_client: any = None
        self._user_id: str | None = None

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.STOCKBIT

    @property
    def slug(self) -> str:
        return "stockbit"

    async def connect(self) -> bool:
        """Authenticate and establish session."""
        try:
            self._client = httpx.AsyncClient(
                base_url=STOCKBIT_API_BASE,
                timeout=30.0,
                headers={
                    "User-Agent": "Stockbit/4.0",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

            if self._access_token:
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                if await self._validate_token():
                    await self._fetch_user_info()
                    return True

            if self.email and self.password:
                return await self._login()

            return False

        except Exception as e:
            LOG.error(f"Stockbit connect failed: {e}")
            return False

    async def _login(self) -> bool:
        """Login with email/password."""
        try:
            payload = {
                "email": self.email,
                "password": self.password,
                "remember_me": True,
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{STOCKBIT_API_BASE}/api/v1/login",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

            if resp.status_code != 200:
                LOG.error(f"Stockbit login failed: {resp.text}")
                return False

            data = resp.json()
            self._access_token = data.get("data", {}).get("access_token")

            if not self._access_token:
                return False

            self._client.headers["Authorization"] = f"Bearer {self._access_token}"
            await self._fetch_user_info()
            return True

        except Exception as e:
            LOG.error(f"Stockbit login error: {e}")
            return False

    async def _validate_token(self) -> bool:
        """Check if access token is still valid."""
        try:
            resp = await self._client.get("/api/v1/user/profile")
            return resp.status_code == 200
        except Exception:
            return False

    async def _fetch_user_info(self) -> None:
        """Fetch user ID and basic info."""
        try:
            resp = await self._client.get("/api/v1/user/profile")
            if resp.status_code == 200:
                data = resp.json()
                self._user_id = data.get("data", {}).get("user", {}).get("id")
                LOG.info(f"Stockbit connected: {self._user_id}")
        except Exception as e:
            LOG.warning(f"Could not fetch user info: {e}")

    async def get_balance(self) -> dict[str, float]:
        """Get account balance."""
        try:
            resp = await self._client.get("/api/v1/account/balance")
            if resp.status_code == 200:
                data = resp.json()
                acc = data.get("data", {})
                return {
                    "total": float(acc.get("total_value", 0)),
                    "cash": float(acc.get("cash_balance", 0)),
                    "stocks_value": float(acc.get("stocks_value", 0)),
                    "currency": "IDR",
                }
        except Exception as e:
            LOG.error(f"Balance fetch failed: {e}")
        return {"total": 0.0, "cash": 0.0, "stocks_value": 0.0, "currency": "IDR"}

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get current stock positions."""
        try:
            resp = await self._client.get("/api/v1/portfolio/holdings")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("holdings", [])
        except Exception as e:
            LOG.error(f"Positions fetch failed: {e}")
        return []

    async def place_order(
        self,
        symbol: str,
        direction: TradeDirection,
        quantity: int,
        order_type: str = "market",
        price: float | None = None,
        **kwargs,
    ) -> TradeResult:
        """Place a buy/sell order for stocks."""
        try:
            if direction == TradeDirection.CALL:
                side = "buy"
            elif direction == TradeDirection.PUT:
                side = "sell"
            else:
                return TradeResult(success=False, order_id=None, symbol=symbol,
                                   direction=direction, quantity=quantity, price=0.0,
                                   status=TradeStatus.ERROR,
                                   message=f"Invalid direction: {direction}")

            payload = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                "validity": "DAY",
            }
            if price and order_type == "limit":
                payload["price"] = price

            resp = await self._client.post("/api/v1/orders", json=payload)

            if resp.status_code == 200:
                data = resp.json()
                order = data.get("data", {})
                return TradeResult(success=True, order_id=order.get("order_id"),
                                   symbol=symbol, direction=direction, quantity=quantity,
                                   price=order.get("executed_price", price or 0.0),
                                   status=TradeStatus.OPEN, timestamp=datetime.now(UTC),
                                   metadata=order)
            else:
                error_msg = resp.json().get("message", resp.text)
                return TradeResult(success=False, order_id=None, symbol=symbol,
                                   direction=direction, quantity=quantity, price=0.0,
                                   status=TradeStatus.ERROR,
                                   message=f"Order failed: {error_msg}")

        except Exception as e:
            LOG.error(f"Order placement failed: {e}")
            return TradeResult(success=False, order_id=None, symbol=symbol,
                               direction=direction, quantity=quantity, price=0.0,
                               status=TradeStatus.ERROR, message=str(e))

    async def get_order_status(self, order_id: str) -> TradeResult | None:
        """Get order status."""
        try:
            resp = await self._client.get(f"/api/v1/orders/{order_id}")
            if resp.status_code == 200:
                data = resp.json()
                order = data.get("data", {})
                return TradeResult(success=True, order_id=order.get("order_id"),
                                   symbol=order.get("symbol"),
                                   direction=TradeDirection.CALL if order.get("side") == "buy" else TradeDirection.PUT,  # noqa: E501
                                   quantity=float(order.get("quantity", 0)),
                                   price=float(order.get("executed_price", 0)),
                                   status=TradeStatus.FILLED if order.get("status") == "filled" else TradeStatus.OPEN,  # noqa: E501
                                   timestamp=datetime.fromisoformat(order.get("created_at").replace("Z", "+00:00")) if order.get("created_at") else datetime.now(UTC),  # noqa: E501
                                   metadata=order)
        except Exception as e:
            LOG.error(f"Order status fetch failed: {e}")
        return None


    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
        amount: float,
        duration: int | None = None,
    ) -> TradeResult:
        """Place a trade (BaseBroker interface)."""
        # Map amount to quantity, duration is not used for these brokers
        return await self.place_order(
            symbol=symbol,
            direction=direction,
            quantity=amount,
            order_type='market',
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        try:
            resp = await self._client.delete(f"/api/v1/orders/{order_id}")
            return resp.status_code == 200
        except Exception as e:
            LOG.error(f"Order cancel failed: {e}")
        return False

    async def get_market_data(self, symbol: str) -> dict[str, Any] | None:
        """Get current market data for a symbol."""
        try:
            resp = await self._client.get(f"/api/v1/market/stocks/{symbol}/quote")
            if resp.status_code == 200:
                return resp.json().get("data", {})
        except Exception as e:
            LOG.error(f"Market data fetch failed: {e}")
        return None

    async def search_symbols(self, query: str) -> list[dict[str, Any]]:
        """Search for symbols."""
        try:
            resp = await self._client.get(f"/api/v1/market/search?q={query}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("results", [])
        except Exception as e:
            LOG.error(f"Symbol search failed: {e}")
        return []

    async def get_history(self, symbol: str, interval: str = "1d", limit: int = 100) -> list[dict[str, Any]]:  # noqa: E501
        """Get historical price data."""
        try:
            resp = await self._client.get(
                f"/api/v1/market/stocks/{symbol}/candles",
                params={"interval": interval, "limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("candles", [])
        except Exception as e:
            LOG.error(f"History fetch failed: {e}")
        return []

    async def get_social_feed(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get social trading feed (stockbit-specific feature)."""
        try:
            resp = await self._client.get("/api/v1/social/feed", params={"limit": limit})
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("posts", [])
        except Exception as e:
            LOG.error(f"Social feed fetch failed: {e}")
        return []

    async def get_top_traders(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top traders by performance."""
        try:
            resp = await self._client.get("/api/v1/social/top-traders", params={"limit": limit})
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("traders", [])
        except Exception as e:
            LOG.error(f"Top traders fetch failed: {e}")
        return []

    async def close(self) -> None:
        """Close connections."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False


async def get_stockbit_broker(
    email: str | None = None,
    password: str | None = None,
    access_token: str | None = None,
    sandbox: bool = False,
) -> StockbitBroker | None:
    """Create and connect StockbitBroker."""
    broker = StockbitBroker(email=email, password=password, access_token=access_token, sandbox=sandbox)  # noqa: E501
    if await broker.connect():
        return broker
    await broker.close()
    return None
