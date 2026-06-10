"""
Stockity REST API client.

Supports both stockity.id and stockity.com domains.
Provides account management: deposit, payments, transaction history.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

import httpx

from tradebot.config import settings

LOG = logging.getLogger(__name__)

# Both domains work
API_BASE = "https://api.stockity.id"
API_BASE_ALT = "https://api.stockity.com"
PLATFORM = f"{API_BASE}/platform/private"


class StockityREST:
    """REST API client for Stockity account management."""

    def __init__(self, cookie: str | None = None, domain: str = "id") -> None:
        self._cookie = cookie or settings.STOCKITY_FULL_COOKIE
        self._base = API_BASE if domain == "id" else API_BASE_ALT
        self._platform = f"{self._base}/platform/private"
        self._client: httpx.AsyncClient | None = None

    @property
    def headers(self) -> dict[str, str]:
        # Extract authtoken from cookie for authorization-token header
        import re
        auth_token = ""
        dev_id = ""
        tz = "Asia/Jakarta"
        match = re.search(r'authtoken=([^;]+)', self._cookie)
        if match:
            auth_token = match.group(1)
        match = re.search(r'device_id=([^;]+)', self._cookie)
        if match:
            dev_id = match.group(1)
        match = re.search(r'user_timezone=([^;]+)', self._cookie)
        if match:
            tz = match.group(1).replace('%2F', '/')

        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "authorization-token": auth_token,
            "device-id": dev_id,
            "device-type": "web",
            "user-timezone": tz,
            "origin": self._base.replace("api.", ""),
            "referer": f"{self._base.replace('api.', '')}/",
            "cookie": self._cookie,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36"
            ),
        }

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any] | None:
        """GET request to Stockity API."""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=15)
        url = f"{self._platform}{path}"
        try:
            resp = await self._client.get(url, params=params, headers=self.headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            LOG.error("GET %s failed: %s", path, e)
            return None

    async def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """POST request to Stockity API."""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=15)
        url = f"{self._platform}{path}"
        try:
            resp = await self._client.post(url, json=data, headers=self.headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            LOG.error("POST %s failed: %s", path, e)
            return None

    async def _patch(self, path: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """PATCH request to Stockity API."""
        if not self._client:
            self._client = httpx.AsyncClient(timeout=15)
        url = f"{self._platform}{path}"
        try:
            resp = await self._client.patch(url, json=data, headers=self.headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            LOG.error("PATCH %s failed: %s", path, e)
            return None

    # ── Profile ──────────────────────────────────────────────

    async def get_profile(self) -> dict[str, Any] | None:
        """Get user display profile."""
        return await self._get("/display_profile")

    async def get_payment_profile(self) -> dict[str, Any] | None:
        """Get payment profile."""
        return await self._get("/payment_profile")

    # ── Payment Methods ──────────────────────────────────────

    async def get_payment_methods(self) -> dict[str, Any] | None:
        """Get available payment methods."""
        return await self._get("/payments")

    async def get_payment_countries(self) -> dict[str, Any] | None:
        """Get available countries for payments."""
        return await self._get("/payments/countries")

    async def get_qris_payments(self) -> dict[str, Any] | None:
        """Get QRIS payment options."""
        return await self._get("/payments/qris")

    # ── Deposit ──────────────────────────────────────────────

    async def deposit(
        self,
        amount: int,
        handler: str = "qris",
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        phone: str = "",
        country_iso: str = "ID",
        coupon_code: str = "",
        bonus: str = "none",
    ) -> dict[str, Any] | None:
        """Create a deposit and get payment redirect URL.

        Args:
            amount: Deposit amount in smallest currency unit.
                    e.g., 150000 = 150,000 IDR
            handler: Payment handler (qris, bank_transfer, etc.)
            first_name, last_name, email, phone: User details
            country_iso: ISO country code (default: ID)
            coupon_code: Coupon code for bonus
            bonus: Bonus type (none, welcome, etc.)

        Returns:
            Payment data including redirect URL to payment processor.
        """
        payload = {
            "handler": handler,
            "coupon_code": coupon_code,
            "country_iso": country_iso,
            "amount": amount,
            "bonus": bonus,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "get_bonus": False,
            "get_coupon": False,
            "fingerprint": {
                "color_depth": 32,
                "language": "en-US",
                "screen_height": 1080,
                "screen_width": 1920,
                "window_height": 910,
                "window_width": 1278,
                "time_zone_offset": -420,
                "java_enabled": False,
                "javascript_enabled": True,
            },
        }

        result = await self._post(
            "/payments?repeat=true&locale=en&ab_payment=new_card_flow",
            payload,
        )

        if result and result.get("success"):
            data = result.get("data", {})
            if isinstance(data, dict):
                redirect_url = data.get("url", "")
                LOG.info("Deposit redirect: %s", redirect_url)
                return {
                    "success": True,
                    "redirect_url": redirect_url,
                    "method": data.get("method", "GET"),
                    "window_type": data.get("window_type", ""),
                    "guid": data.get("guid", ""),
                    "raw": result,
                }
            if isinstance(data, str):
                LOG.info("Deposit redirect URL: %s", data)
                return {"success": True, "redirect_url": data}

        LOG.error("Deposit failed: %s", result)
        return result

    # ── Transactions ─────────────────────────────────────────

    async def get_transactions(self) -> dict[str, Any] | None:
        """Get transaction history."""
        return await self._get("/transactions")

    # ── Coupons ──────────────────────────────────────────────

    async def get_coupons(self) -> dict[str, Any] | None:
        """Get available coupons."""
        return await self._get("/coupons")

    # ── Recurring Payments ───────────────────────────────────

    async def get_recurring_payments(self) -> dict[str, Any] | None:
        """Get recurring payment settings."""
        return await self._get("/v3/recurring_payment")

    # ── Payment Profile ──────────────────────────────────────

    async def activate_custom_deposits(self) -> dict[str, Any] | None:
        """Activate custom deposit amounts."""
        return await self._patch("/payment_profile/activate_custom_deposits", {})

    # ── Candles (REST) ───────────────────────────────────────

    async def get_candles(
        self,
        ric: str = "Z-CRY%2FIDX",
        date: str | None = None,
        interval: int = 5,
    ) -> dict[str, Any] | None:
        """Fetch candles via REST API.

        Args:
            ric: RIC code (URL-encoded)
            date: ISO date string (default: today 00:00:00)
            interval: Candle interval in seconds (5, 60, etc.)
        """
        from datetime import datetime

        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")

        path = f"/candles/v1/{ric}/{date}/{interval}"
        url = f"{self._base}{path}"
        if not self._client:
            self._client = httpx.AsyncClient(timeout=15)
        try:
            resp = await self._client.get(
                url, params={"locale": "en"}, headers=self.headers
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            LOG.error("Candles failed: %s", e)
            return None

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
