"""
Ajaib Broker — Indonesian stock trading platform.

Uses REST API with JWT authentication.
Supports stocks, ETFs, mutual funds, and crypto.

API Base: https://api.ajaib.co.id
Docs: Not public - reverse engineered from mobile app
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

LOG = logging.getLogger("tradebot.brokers.ajaib")

AJAIB_API_BASE = "https://api.ajaib.co.id"
AJAIB_AUTH_BASE = "https://auth.ajaib.co.id"


class AjaibBroker(BaseBroker):
    """Ajaib broker for Indonesian stock market."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        device_id: str | None = None,
        sandbox: bool = False,
    ):
        super().__init__()
        self.email = email or settings.AJAIB_EMAIL
        self.password = password or settings.AJAIB_PASSWORD
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._device_id = device_id or settings.AJAIB_DEVICE_ID
        self._sandbox = sandbox
        self._client: httpx.AsyncClient | None = None
        self._account_id: str | None = None

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.AJAIB

    @property
    def slug(self) -> str:
        return "ajaib"

    async def connect(self) -> bool:
        """Authenticate and establish session."""
        try:
            self._client = httpx.AsyncClient(
                base_url=AJAIB_API_BASE,
                timeout=30.0,
                headers={
                    "User-Agent": "Ajaib/5.0 (Android; Mobile; En)",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

            if self._access_token:
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                if await self._validate_token():
                    await self._fetch_account_info()
                    return True

            # Need to login
            if self.email and self.password:
                return await self._login()
            return False

        except Exception as e:
            LOG.error(f"Ajaib connect failed: {e}")
            return False

    async def _login(self) -> bool:
        """Login with email/password."""
        try:
            payload = {
                "email": self.email,
                "password": self.password,
                "device_id": self._device_id,
                "grant_type": "password",
            }
            if self._sandbox:
                payload["environment"] = "sandbox"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{AJAIB_AUTH_BASE}/oauth/token",
                    json=payload,
                    headers={
                        "User-Agent": "Ajaib/5.0 (Android; Mobile; En)",
                        "Content-Type": "application/json",
                    },
                )

            if resp.status_code != 200:
                LOG.error(f"Ajaib login failed: {resp.text}")
                return False

            data = resp.json()
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")

            if not self._access_token:
                return False

            self._client.headers["Authorization"] = f"Bearer {self._access_token}"
            await self._fetch_account_info()
            return True

        except Exception as e:
            LOG.error(f"Ajaib login error: {e}")
            return False

    async def _validate_token(self) -> bool:
        """Check if access token is still valid."""
        try:
            resp = await self._client.get("/v1/user/profile")
            return resp.status_code == 200
        except Exception:
            return False

    async def _fetch_account_info(self) -> None:
        """Fetch account ID and basic info."""
        try:
            resp = await self._client.get("/v1/user/profile")
            if resp.status_code == 200:
                data = resp.json()
                self._account_id = data.get("user", {}).get("id")
                LOG.info(f"Ajaib connected: {self._account_id}")
        except Exception as e:
            LOG.warning(f"Could not fetch account info: {e}")

    async def refresh_token(self) -> bool:
        """Refresh access token using refresh token."""
        if not self._refresh_token:
            return False

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{AJAIB_AUTH_BASE}/oauth/token",
                    json={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "device_id": self._device_id,
                    },
                    headers={"Content-Type": "application/json"},
                )

            if resp.status_code != 200:
                return False

            data = resp.json()
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")

            if self._access_token:
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                return True

            return False

        except Exception as e:
            LOG.error(f"Token refresh failed: {e}")
            return False

    async def get_balance(self) -> dict[str, float]:
        """Get account balance."""
        try:
            resp = await self._client.get("/v1/portfolio/balance")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "total": float(data.get("total_value", 0)),
                    "cash": float(data.get("cash_balance", 0)),
                    "invested": float(data.get("invested_value", 0)),
                    "currency": "IDR",
                }
        except Exception as e:
            LOG.error(f"Balance fetch failed: {e}")
        return {"total": 0.0, "cash": 0.0, "invested": 0.0, "currency": "IDR"}

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get current positions/holdings."""
        try:
            resp = await self._client.get("/v1/portfolio/holdings")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("holdings", [])
        except Exception as e:
            LOG.error(f"Positions fetch failed: {e}")
        return []

    async def place_order(
        self,
        symbol: str,
        direction: TradeDirection,
        quantity: float,
        order_type: str = "market",
        price: float | None = None,
        **kwargs,
    ) -> TradeResult:
        """Place a buy/sell order."""
        try:
            if direction == TradeDirection.CALL:
                side = "buy"
            elif direction == TradeDirection.PUT:
                side = "sell"
            else:
                return TradeResult(
                    success=False,
                    order_id=None,
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    price=0.0,
                    status=TradeStatus.ERROR,
                    message=f"Invalid direction: {direction}",
                )

            payload = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
            }
            if price and order_type == "limit":
                payload["price"] = price

            resp = await self._client.post("/v1/orders", json=payload)

            if resp.status_code == 200:
                data = resp.json()
                order = data.get("order", {})
                return TradeResult(
                    success=True,
                    order_id=order.get("id"),
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    price=order.get("executed_price", price or 0.0),
                    status=TradeStatus.OPEN,
                    timestamp=datetime.now(UTC),
                    metadata=order,
                )
            else:
                error_msg = resp.json().get("message", resp.text)
                return TradeResult(
                    success=False,
                    order_id=None,
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    price=0.0,
                    status=TradeStatus.ERROR,
                    message=f"Order failed: {error_msg}",
                )

        except Exception as e:
            LOG.error(f"Order placement failed: {e}")
            return TradeResult(
                success=False,
                order_id=None,
                symbol=symbol,
                direction=direction,
                quantity=quantity,
                price=0.0,
                status=TradeStatus.ERROR,
                message=str(e),
            )

    async def get_order_status(self, order_id: str) -> TradeResult | None:
        """Get order status."""
        try:
            resp = await self._client.get(f"/v1/orders/{order_id}")
            if resp.status_code == 200:
                data = resp.json()
                order = data.get("order", {})
                return TradeResult(
                    success=True,
                    order_id=order.get("id"),
                    symbol=order.get("symbol"),
                    direction=TradeDirection.CALL if order.get("side") == "buy" else TradeDirection.PUT,  # noqa: E501
                    quantity=float(order.get("quantity", 0)),
                    price=float(order.get("executed_price", 0)),
                    status=TradeStatus.FILLED if order.get("status") == "filled" else TradeStatus.OPEN,  # noqa: E501
                    timestamp=datetime.fromisoformat(order.get("created_at").replace("Z", "+00:00")) if order.get("created_at") else datetime.now(UTC),  # noqa: E501
                    metadata=order,
                )
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
            resp = await self._client.delete(f"/v1/orders/{order_id}")
            return resp.status_code == 200
        except Exception as e:
            LOG.error(f"Order cancel failed: {e}")
        return False

    async def get_market_data(self, symbol: str) -> dict[str, Any] | None:
        """Get current market data for a symbol."""
        try:
            resp = await self._client.get(f"/v1/market/quote/{symbol}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            LOG.error(f"Market data fetch failed: {e}")
        return None

    async def search_symbols(self, query: str) -> list[dict[str, Any]]:
        """Search for symbols."""
        try:
            resp = await self._client.get(f"/v1/market/search?q={query}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
        except Exception as e:
            LOG.error(f"Symbol search failed: {e}")
        return []

    async def get_history(
        self, symbol: str, interval: str = "1d", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get historical price data."""
        try:
            resp = await self._client.get(
                f"/v1/market/history/{symbol}",
                params={"interval": interval, "limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("candles", [])
        except Exception as e:
            LOG.error(f"History fetch failed: {e}")
        return []

    async def close(self) -> None:
        """Close connections."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False


async def get_ajaib_broker(
    email: str | None = None,
    password: str | None = None,
    access_token: str | None = None,
    sandbox: bool = False,
) -> AjaibBroker | None:
    """Create and connect AjaibBroker."""
    broker = AjaibBroker(email=email, password=password, access_token=access_token, sandbox=sandbox)
    if await broker.connect():
        return broker
    await broker.close()
    return None
