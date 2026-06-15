"""Pluang Broker — Indonesian digital gold & crypto trading platform.

Uses REST API with API key/secret authentication (HMAC-SHA256).
Supports gold, crypto, and fractional stocks.

API Base: https://api.pluang.com
Auth Base: https://auth.pluang.com
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
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

PLUANG_API_BASE = "https://api.pluang.com"
PLUANG_AUTH_BASE = "https://auth.pluang.com"

LOG = logging.getLogger("tradebot.brokers.pluang")


class PluangBroker(BaseBroker):
    """Pluang broker for digital gold and crypto."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        sandbox: bool = False,
    ):
        super().__init__()
        self.api_key = api_key or settings.PLUANG_API_KEY
        self.api_secret = api_secret or settings.PLUANG_API_SECRET
        self._access_token = access_token
        self._sandbox = sandbox
        self._client: httpx.AsyncClient | None = None
        self._account_id: str | None = None
        self._user_id: str | None = None

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.PLUANG

    @property
    def slug(self) -> str:
        return "pluang"

    def _sign_request(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Generate HMAC signature for Pluang API."""
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method.upper()}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

        return {
            "X-API-KEY": self.api_key,
            "X-TIMESTAMP": timestamp,
            "X-SIGNATURE": signature,
        }

    async def connect(self) -> bool:
        """Authenticate and establish session."""
        try:
            self._client = httpx.AsyncClient(
                base_url=PLUANG_API_BASE,
                timeout=30.0,
                headers={
                    "User-Agent": "Pluang/1.0",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

            if self._access_token:
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                if await self._validate_token():
                    await self._fetch_account_info()
                    return True

            return False

        except Exception as e:
            LOG.error(f"Pluang connect failed: {e}")
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
                self._user_id = data.get("user", {}).get("id")
                self._account_id = data.get("account", {}).get("id")
                LOG.info(f"Pluang connected: {self._user_id}")
        except Exception as e:
            LOG.warning(f"Could not fetch account info: {e}")

    async def get_balance(self) -> dict[str, float]:
        """Get account balance (gold, crypto, cash)."""
        try:
            resp = await self._client.get("/v1/account/balance")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "gold_gram": float(data.get("gold", {}).get("gram", 0)),
                    "gold_value_idr": float(data.get("gold", {}).get("value_idr", 0)),
                    "crypto_value_idr": float(data.get("crypto", {}).get("value_idr", 0)),
                    "cash_idr": float(data.get("cash", {}).get("value_idr", 0)),
                    "total_idr": float(data.get("total", {}).get("value_idr", 0)),
                    "currency": "IDR",
                }
        except Exception as e:
            LOG.error(f"Balance fetch failed: {e}")
        return {"gold_gram": 0.0, "gold_value_idr": 0.0,
                "crypto_value_idr": 0.0, "cash_idr": 0.0,
                "total_idr": 0.0, "currency": "IDR"}

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get current holdings (gold, crypto)."""
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
        asset_type: str = "gold",
        **kwargs,
    ) -> TradeResult:
        """Place a buy/sell order for gold, crypto, or stocks."""
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
                "asset_type": asset_type,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
            }
            if price and order_type == "limit":
                payload["price"] = price

            body = json.dumps(payload)
            signatures = self._sign_request("POST", "/v1/orders", body)

            headers = await self._prepare_headers(signatures)
            resp = await self._client.post("/v1/orders", json=payload, headers=headers)

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
                return TradeResult(success=False, order_id=None, symbol=symbol,
                                   direction=direction, quantity=quantity, price=0.0,
                                   status=TradeStatus.ERROR,
                                   message=f"Order failed: {error_msg}")

        except Exception as e:
            LOG.error(f"Order placement failed: {e}")
            return TradeResult(success=False, order_id=None, symbol=symbol,
                               direction=direction, quantity=quantity, price=0.0,
                               status=TradeStatus.ERROR, message=str(e))

    async def _prepare_headers(self, signatures: dict[str, str]) -> dict[str, str]:
        """Prepare request headers with auth."""
        headers = {
            "User-Agent": "Pluang/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        headers.update(signatures)
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def get_order_status(self, order_id: str) -> TradeResult | None:
        """Get order status."""
        try:
            path = f"/v1/orders/{order_id}"
            signatures = self._sign_request("GET", path)
            headers = await self._prepare_headers(signatures)

            resp = await self._client.get(path, headers=headers)
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
                    timestamp=datetime.fromisoformat(order.get("created_at").replace("Z", "+00:00"))  # noqa: E501
                        if order.get("created_at") else datetime.now(UTC),
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
            path = f"/v1/orders/{order_id}"
            signatures = self._sign_request("DELETE", path)
            headers = await self._prepare_headers(signatures)

            resp = await self._client.delete(path, headers=headers)
            return resp.status_code == 200
        except Exception as e:
            LOG.error(f"Order cancel failed: {e}")
        return False

    async def get_market_data(self, symbol: str, asset_type: str = "gold") -> dict[str, Any] | None:
        """Get current market data for a symbol."""
        try:
            resp = await self._client.get(f"/v1/market/quote/{asset_type}/{symbol}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            LOG.error(f"Market data fetch failed: {e}")
        return None

    async def search_symbols(self, query: str, asset_type: str = "gold") -> list[dict[str, Any]]:
        """Search for symbols."""
        try:
            resp = await self._client.get(f"/v1/market/search?asset_type={asset_type}&q={query}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
        except Exception as e:
            LOG.error(f"Symbol search failed: {e}")
        return []

    async def get_history(  # noqa: E501
        self, symbol: str, asset_type: str = "gold", interval: str = "1d", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get historical price data."""
        try:
            resp = await self._client.get(
                f"/v1/market/history/{asset_type}/{symbol}",
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


async def get_pluang_broker(
    api_key: str | None = None,
    api_secret: str | None = None,
    access_token: str | None = None,
    sandbox: bool = False,
) -> PluangBroker | None:
    """Create and connect PluangBroker."""
    broker = PluangBroker(api_key=api_key, api_secret=api_secret,
                          access_token=access_token, sandbox=sandbox)
    if await broker.connect():
        return broker
    await broker.close()
    return None
