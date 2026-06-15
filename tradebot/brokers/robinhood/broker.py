"""
Robinhood Broker — US commission-free trading platform.

Uses unofficial REST API with device token authentication.
Supports stocks, options, crypto, and fractional shares.

API Base: https://api.robinhood.com (unofficial)
Note: This uses unofficial API - use at own risk.
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

LOG = logging.getLogger("tradebot.brokers.robinhood")

ROBINHOOD_API_BASE = "https://api.robinhood.com"
ROBINHOOD_AUTH_URL = "https://api.robinhood.com/oauth2/token/"


class RobinhoodBroker(BaseBroker):
    """Robinhood broker for US stock, options, and crypto trading."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        mfa_code: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        device_token: str | None = None,
        sandbox: bool = False,
    ):
        super().__init__()
        self.username = username or settings.ROBINHOOD_USERNAME
        self.password = password or settings.ROBINHOOD_PASSWORD
        self._mfa_code = mfa_code
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._device_token = device_token or settings.ROBINHOOD_DEVICE_TOKEN
        self._sandbox = sandbox
        self._client: httpx.AsyncClient | None = None
        self._account_url: str | None = None

    @property
    def platform(self) -> BrokerPlatform:
        return BrokerPlatform.ROBINHOOD

    @property
    def slug(self) -> str:
        return "robinhood"

    async def connect(self) -> bool:
        """Authenticate and establish session."""
        try:
            self._client = httpx.AsyncClient(
                base_url=ROBINHOOD_API_BASE,
                timeout=30.0,
                headers={
                    "User-Agent": "Robinhood/823.0.1 (iPhone; iOS 15.6; Scale/3.00)",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

            if self._access_token:
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                if await self._validate_token():
                    await self._fetch_account_info()
                    return True

            # Try to refresh if we have refresh token
            if self._refresh_token and await self._refresh_access_token():
                await self._fetch_account_info()
                return True

            # Full login
            if self.username and self.password:
                return await self._login()

            return False

        except Exception as e:
            LOG.error(f"Robinhood connect failed: {e}")
            return False

    async def _login(self) -> bool:
        """Login with username/password and handle MFA."""
        try:
            payload = {
                "username": self.username,
                "password": self.password,
                "device_token": self._device_token,
                "grant_type": "password",
                "scope": "internal",
                "client_id": "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS",
            }
            if self._mfa_code:
                payload["mfa_code"] = self._mfa_code

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    ROBINHOOD_AUTH_URL,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

            if resp.status_code == 401:
                # MFA required
                LOG.warning("Robinhood MFA required")
                return False
            elif resp.status_code != 200:
                LOG.error(f"Robinhood login failed: {resp.text}")
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
            LOG.error(f"Robinhood login error: {e}")
            return False

    async def _validate_token(self) -> bool:
        """Check if access token is still valid."""
        try:
            resp = await self._client.get("/accounts/")
            return resp.status_code == 200
        except Exception:
            return False

    async def _refresh_access_token(self) -> bool:
        """Refresh access token using refresh token."""
        if not self._refresh_token:
            return False

        try:
            payload = {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    ROBINHOOD_AUTH_URL,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

            if resp.status_code != 200:
                return False

            data = resp.json()
            self._access_token = data.get("access_token")
            if self._access_token:
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                return True

            return False

        except Exception as e:
            LOG.error(f"Token refresh failed: {e}")
            return False

    async def _fetch_account_info(self) -> None:
        """Fetch account URL and basic info."""
        try:
            resp = await self._client.get("/accounts/")
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    self._account_url = results[0].get("url")
                    LOG.info(f"Robinhood connected: {self._account_url}")
        except Exception as e:
            LOG.warning(f"Could not fetch account info: {e}")

    async def get_balance(self) -> dict[str, float]:
        """Get account balance."""
        try:
            if not self._account_url:
                return {"total": 0.0, "cash": 0.0, "equity": 0.0, "currency": "USD"}

            resp = await self._client.get(self._account_url)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "total": float(data.get("portfolio_cash", 0)),
                    "cash": float(data.get("uncleared_deposits", 0)),
                    "equity": float(data.get("equity", 0)),
                    "currency": "USD",
                }
        except Exception as e:
            LOG.error(f"Balance fetch failed: {e}")
        return {"total": 0.0, "cash": 0.0, "equity": 0.0, "currency": "USD"}

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get current positions."""
        try:
            resp = await self._client.get("/positions/")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
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
        side: str | None = None,
        time_in_force: str = "gfd",
        **kwargs,
    ) -> TradeResult:
        """Place a buy/sell order (market/limit/stop)."""
        try:
            if direction == TradeDirection.CALL:
                buy_sell = "buy"
            elif direction == TradeDirection.PUT:
                buy_sell = "sell"
            else:
                return TradeResult(success=False, order_id=None, symbol=symbol,
                                   direction=direction, quantity=quantity, price=0.0,
                                   status=TradeStatus.ERROR,
                                   message=f"Invalid direction: {direction}")

            # Get instrument URL
            instrument = await self._get_instrument(symbol)
            if not instrument:
                return TradeResult(success=False, order_id=None, symbol=symbol,
                                   direction=direction, quantity=quantity, price=0.0,
                                   status=TradeStatus.ERROR,
                                   message=f"Instrument not found: {symbol}")

            payload = {
                "instrument": instrument["url"],
                "quantity": str(quantity),
                "side": buy_sell,
                "type": order_type,
                "time_in_force": time_in_force,
                "trigger": "immediate",
                "ref_id": None,
            }

            if price and order_type in ("limit", "stop_limit"):
                payload["price"] = str(price)

            resp = await self._client.post("/orders/", json=payload)

            if resp.status_code in (200, 201):
                data = resp.json()
                return TradeResult(success=True, order_id=data.get("id"),
                                   symbol=symbol, direction=direction, quantity=quantity,
                                   price=price or float(data.get("average_price", 0)),
                                   status=TradeStatus.OPEN, timestamp=datetime.now(UTC),
                                   metadata=data)
            else:
                error_msg = resp.json().get("detail", resp.text)
                return TradeResult(success=False, order_id=None, symbol=symbol,
                                   direction=direction, quantity=quantity, price=0.0,
                                   status=TradeStatus.ERROR,
                                   message=f"Order failed: {error_msg}")

        except Exception as e:
            LOG.error(f"Order placement failed: {e}")
            return TradeResult(success=False, order_id=None, symbol=symbol,
                               direction=direction, quantity=quantity, price=0.0,
                               status=TradeStatus.ERROR, message=str(e))

    async def _get_instrument(self, symbol: str) -> dict[str, Any] | None:
        """Get instrument URL for symbol."""
        try:
            resp = await self._client.get(
                "/instruments/",
                params={"query": symbol, "simple": "true"},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                for inst in results:
                    if inst.get("symbol") == symbol.upper():
                        return inst
        except Exception as e:
            LOG.error(f"Instrument lookup failed: {e}")
        return None

    async def get_order_status(self, order_id: str) -> TradeResult | None:
        """Get order status by ID or URL."""
        try:
            url = order_id if order_id.startswith("http") else f"/orders/{order_id}/"
            resp = await self._client.get(url)
            if resp.status_code == 200:
                order = resp.json()
                return TradeResult(success=True, order_id=order.get("id"),
                                   symbol=order.get("instrument", "").split("/")[-2],
                                   direction=TradeDirection.CALL if order.get("side") == "buy" else TradeDirection.PUT,  # noqa: E501
                                   quantity=float(order.get("quantity", 0)),
                                   price=float(order.get("average_price", 0)),
                                   status=TradeStatus.FILLED if order.get("state") == "filled" else TradeStatus.OPEN,  # noqa: E501
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
            url = order_id if order_id.startswith("http") else f"/orders/{order_id}/"
            resp = await self._client.post(url + "cancel/")
            return resp.status_code in (200, 204)
        except Exception as e:
            LOG.error(f"Order cancel failed: {e}")
        return False

    async def get_market_data(self, symbol: str) -> dict[str, Any] | None:
        """Get current market data for a symbol."""
        try:
            inst = await self._get_instrument(symbol)
            if not inst:
                return None

            resp = await self._client.get(f"/quotes/{inst.get('id')}/")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            LOG.error(f"Market data fetch failed: {e}")
        return None

    async def search_symbols(self, query: str) -> list[dict[str, Any]]:
        """Search for symbols."""
        try:
            resp = await self._client.get(
                "/instruments/",
                params={"query": query, "simple": "true"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
        except Exception as e:
            LOG.error(f"Symbol search failed: {e}")
        return []

    async def get_history(self, symbol: str, interval: str = "1d", limit: int = 100) -> list[dict[str, Any]]:  # noqa: E501  # noqa: E501
        """Get historical price data (not directly supported by Robinhood API)."""
        LOG.warning("Robinhood API does not support historical data directly")
        return []

    async def get_crypto_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Get crypto quotes (Robinhood-specific)."""
        try:
            ids = ",".join(symbols.upper() for symbol in symbols)
            resp = await self._client.get(f"/quotes/cryptocurrencies/?ids={ids}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
        except Exception as e:
            LOG.error(f"Crypto quotes fetch failed: {e}")
        return []

    async def close(self) -> None:
        """Close connections."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False


async def get_robinhood_broker(
    username: str | None = None,
    password: str | None = None,
    mfa_code: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    sandbox: bool = False,
) -> RobinhoodBroker | None:
    """Create and connect RobinhoodBroker."""
    broker = RobinhoodBroker(username=username, password=password, mfa_code=mfa_code,
                             access_token=access_token, refresh_token=refresh_token,
                             sandbox=sandbox)
    if await broker.connect():
        return broker
    await broker.close()
    return None
