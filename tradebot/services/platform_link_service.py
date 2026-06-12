"""
Platform Link Service — account linking and credential management.

Handles linking/unlinking user broker accounts across all platforms:
- Stockity: email/password → session cookie + currency detection
- Deriv: APP_ID + secret
- CCXT: API key + secret (varies by exchange)
- MT5: EA bridge generated key

Architecture:
    Per-user credentials stored in user_platforms table (tradebot.db).
    Global STOCKITY_FULL_COOKIE used as market data fallback only
    (not for trade execution — that requires per-user valid credentials).

    Priority for market data:
        1. Per-user cookie (from email/pass login) → primary
        2. Global STOCKITY_FULL_COOKIE → fallback
        3. Error if both fail
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from tradebot.storage.sqlite import SQLiteStorage

LOG = logging.getLogger("tradebot.services.platform_link")

WIB = timezone(timedelta(hours=7))
STOCKITY_AUTH_URL = "https://api.stockity.id/passport/v2/sign_in?locale=id"
STOCKITY_BALANCE_URL = "https://api.stockity.id/bank/v1/read"


def _now() -> str:
    return datetime.now(WIB).isoformat()


def _storage() -> SQLiteStorage:
    return SQLiteStorage()


class PlatformLinkError(Exception):
    """Raised when platform linking fails."""


class PlatformLinkService:
    """Account linking service for all supported platforms."""

    # ── Stockity ─────────────────────────────────────────────────────────

    async def link_stockity(
        self,
        user_id: str,
        email: str,
        password: str,
        label: str = "main",
    ) -> dict[str, Any]:
        """Link a Stockity account by email/password login.

        Flow:
            1. POST to Stockity passport/sign_in with credentials
            2. Extract authtoken and user_id from response
            3. Build full cookie from auth components
            4. GET /bank/v1/read to detect currency and account type
            5. Store credentials + currency in user_platforms table

        Args:
            user_id: Telegram chat_id
            email: Stockity account email
            password: Stockity account password
            label: Account label (default: "main")

        Returns:
            Dict with success status, detected currency, and account info.

        Raises:
            PlatformLinkError: On invalid credentials or network failure.
        """
        try:
            authtoken, broker_user_id = await self._stockity_login(email, password)
        except Exception as e:
            raise PlatformLinkError(f"Login gagal: {e}") from e

        cookie = self._build_stockity_cookie(authtoken, broker_user_id)

        # Detect currency from balance API
        currency = await self._detect_stockity_currency(cookie)
        LOG.info("Stockity currency detected: %s for user %s", currency, user_id)

        # Save to user_platforms table
        store = _storage()
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, email, password, credentials,
                currency, broker_user_id, status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                user_id,
                "stockity",
                label,
                email,
                password,
                json.dumps({"cookie": cookie}),
                currency,
                broker_user_id,
                _now(),
                _now(),
            ),
        )

        return {
            "success": True,
            "platform": "stockity",
            "currency": currency,
            "broker_user_id": broker_user_id,
            "label": label,
        }

    async def _stockity_login(self, email: str, password: str) -> tuple[str, str]:
        """Login to Stockity and get authtoken + user_id."""
        payload = json.dumps({"email": email, "password": password}).encode()
        headers = {
            "Content-Type": "application/json",
            "Device-Id": "d79220637a3516ea5350ea509df42828",
            "Device-Type": "web",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
            ),
            "Origin": "https://stockity.id",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                STOCKITY_AUTH_URL,
                content=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            authtoken: str = data.get("authtoken", "")
            broker_user_id: str = data.get("user_id", "")
            if not authtoken or not broker_user_id:
                raise PlatformLinkError(
                    f"Invalid response: authtoken={'set' if authtoken else 'missing'}, "
                    f"user_id={'set' if broker_user_id else 'missing'}"
                )
            return authtoken, broker_user_id

    def _build_stockity_cookie(self, authtoken: str, user_id: str) -> str:
        """Build full cookie string from auth components."""
        return (
            f"_stockity_session_v3={authtoken}; authtoken={authtoken}; user_id={user_id}; locale=en"
        )

    async def _detect_stockity_currency(self, cookie: str) -> str:
        """Detect account currency from Stockity balance API.

        Returns:
            Currency code (e.g. "IDR", "USD"), defaults to "IDR" on failure.
        """
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cookie": cookie,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36"
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    STOCKITY_BALANCE_URL,
                    params={"locale": "en"},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                accounts = data.get("data", [])
                for acc in accounts:
                    currency = acc.get("currency", "")
                    if currency:
                        LOG.info(
                            "Detected currency: %s (type=%s)", currency, acc.get("account_type")
                        )
                        return currency
        except Exception as e:
            LOG.warning("Currency detection failed, defaulting to IDR: %s", e)

        return "IDR"

    # ── Deriv ────────────────────────────────────────────────────────────

    async def link_deriv(
        self,
        user_id: str,
        app_id: str,
        secret: str,
        label: str = "main",
    ) -> dict[str, Any]:
        """Link a Deriv account via APP_ID and secret."""
        store = _storage()
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, credentials, currency,
                status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'USD', 'active', ?, ?)""",
            (
                user_id,
                "deriv",
                label,
                json.dumps({"app_id": app_id, "secret": secret}),
                _now(),
                _now(),
            ),
        )
        return {
            "success": True,
            "platform": "deriv",
            "currency": "USD",
            "label": label,
        }

    # ── CCXT ─────────────────────────────────────────────────────────────

    async def link_ccxt(
        self,
        user_id: str,
        exchange: str,
        api_key: str,
        api_secret: str,
        label: str = "main",
    ) -> dict[str, Any]:
        """Link a CCXT exchange account."""
        store = _storage()
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, credentials, currency,
                status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'USD', 'active', ?, ?)""",
            (
                user_id,
                "ccxt",
                label,
                json.dumps({"exchange": exchange, "api_key": api_key, "api_secret": api_secret}),
                _now(),
                _now(),
            ),
        )
        return {
            "success": True,
            "platform": "ccxt",
            "exchange": exchange,
            "currency": "USD",
            "label": label,
        }

    # ── MT5 ──────────────────────────────────────────────────────────────

    async def link_mt5(
        self,
        user_id: str,
        ea_key: str,
        label: str = "main",
    ) -> dict[str, Any]:
        """Link an MT5 account via EA bridge generated key."""
        store = _storage()
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, credentials, currency,
                status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'USD', 'active', ?, ?)""",
            (
                user_id,
                "mt5",
                label,
                json.dumps({"ea_key": ea_key}),
                _now(),
                _now(),
            ),
        )
        return {
            "success": True,
            "platform": "mt5",
            "currency": "USD",
            "label": label,
        }

    # ── Unlink ───────────────────────────────────────────────────────────

    async def unlink_platform(self, user_id: str, platform: str) -> bool:
        """Remove a platform link for a user."""
        store = _storage()
        store.execute(
            "DELETE FROM user_platforms WHERE user_id=? AND platform=?",
            (user_id, platform),
        )
        LOG.info("Platform unlinked: user=%s platform=%s", user_id, platform)
        return True

    # ── Query ────────────────────────────────────────────────────────────

    async def get_linked_platforms(self, user_id: str) -> list[dict[str, Any]]:
        """Get all linked platforms for a user."""
        rows = _storage().fetchall(
            "SELECT * FROM user_platforms WHERE user_id=? AND status='active' ORDER BY linked_at",
            (user_id,),
        )
        return [dict(r) for r in rows]

    async def get_platform_credentials(self, user_id: str, platform: str) -> dict[str, Any] | None:
        """Get stored credentials for a specific platform."""
        row = _storage().fetchone(
            "SELECT * FROM user_platforms WHERE user_id=? AND platform=? AND status='active'",
            (user_id, platform),
        )
        return dict(row) if row else None

    # ── Cookie refresh ───────────────────────────────────────────────────

    async def refresh_stockity_cookie(self, user_id: str) -> str | None:
        """Refresh stale Stockity cookie by re-logging in with stored credentials.

        Called by user_broker_factory when a 401/connection error is detected.

        Returns:
            New cookie string, or None if refresh failed.
        """
        creds = await self.get_platform_credentials(user_id, "stockity")
        if not creds or not creds.get("email") or not creds.get("password"):
            LOG.warning("No stored credentials for user %s stockity — cannot refresh", user_id)
            return None

        try:
            authtoken, broker_user_id = await self._stockity_login(
                creds["email"], creds["password"]
            )
        except Exception as e:
            LOG.error("Cookie refresh failed for user %s: %s", user_id, e)
            return None

        cookie = self._build_stockity_cookie(authtoken, broker_user_id)

        # Update stored cookie
        store = _storage()
        store.execute(
            "UPDATE user_platforms SET credentials=?, updated_at=? WHERE user_id=? AND platform='stockity'",
            (json.dumps({"cookie": cookie}), _now(), user_id),
        )

        LOG.info("Cookie refreshed for user %s", user_id)
        return cookie
