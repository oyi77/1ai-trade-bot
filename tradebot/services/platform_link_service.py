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

from tradebot.security.crypto import get_encryptor
from tradebot.storage.repository import get_repo

LOG = logging.getLogger("tradebot.services.platform_link")

WIB = timezone(timedelta(hours=7))
STOCKITY_AUTH_URL = "https://api.stockity.id/passport/v2/sign_in?locale=id"
STOCKITY_BALANCE_URL = "https://api.stockity.id/bank/v1/read"


def _now() -> str:
    return datetime.now(WIB).isoformat()


def _storage():
    return get_repo()


class PlatformLinkError(Exception):
    """Raised when platform linking fails."""


class PlatformLinkService:
    """Account linking service for all supported platforms."""

    _ENCRYPTED_COLS = frozenset({"email", "password", "credentials"})

    @staticmethod
    def _encrypt_creds(data: dict[str, str]) -> dict[str, str]:
        """Encrypt sensitive fields in a row dict before storage."""
        enc = get_encryptor()
        out = dict(data)
        for col in PlatformLinkService._ENCRYPTED_COLS:
            if col in out and out[col]:
                out[col] = enc.encrypt_string(out[col])
        return out

    @staticmethod
    def _decrypt_row(row: dict[str, Any]) -> dict[str, Any]:
        """Decrypt sensitive fields in a row after retrieval."""
        enc = get_encryptor()
        out = dict(row)
        for col in PlatformLinkService._ENCRYPTED_COLS:
            if col in out and out[col]:
                out[col] = enc.decrypt_string(out[col])
        return out

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
            authtoken, broker_user_id, full_cookie = await self._stockity_login(email, password)
        except Exception as e:
            raise PlatformLinkError(f"Login gagal: {e}") from e

        cookie = full_cookie

        # Detect currency and balances from balance API
        currency, balances = await self._detect_stockity_currency(cookie)
        LOG.info("Stockity currency detected: %s for user %s", currency, user_id)

        # Save to user_platforms table (encrypted at rest)
        store = _storage()
        row_data = self._encrypt_creds({
            "email": email,
            "password": password,
            "credentials": json.dumps({"cookie": cookie}),
        })
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, email, password, credentials,
                currency, broker_user_id, status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                user_id,
                "stockity",
                label,
                row_data["email"],
                row_data["password"],
                row_data["credentials"],
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
            "balances": balances,
        }

    async def _stockity_login(self, email: str, password: str) -> tuple[str, str, str]:
        """Login to Stockity and get authtoken + user_id + full cookie.

        Returns:
            Tuple of (authtoken, broker_user_id, full_cookie_string).
        """
        from tradebot.config import settings as _cfg
        base_cookie = _cfg.STOCKITY_FULL_COOKIE
        device_id = "d79220637a3516ea5350ea509df42828"
        if base_cookie:
            import re as _re
            match = _re.search(r'device_id=([^;]+)', base_cookie)
            if match:
                device_id = match.group(1)

        payload = json.dumps({"email": email, "password": password}).encode()
        headers = {
            "Content-Type": "application/json",
            "Device-Id": device_id,
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
            # Build full cookie from fresh authtoken + global cookie base
            from tradebot.config import settings as _cfg
            base_cookie = _cfg.STOCKITY_FULL_COOKIE
            if base_cookie:
                # Replace authtoken in the global cookie
                parts = base_cookie.split(";")
                seen_authtoken = False
                cookie_parts = []
                for p in parts:
                    p = p.strip()
                    if p.startswith("authtoken="):
                        cookie_parts.append(f"authtoken={authtoken}")
                        seen_authtoken = True
                    elif p.startswith("userId="):
                        cookie_parts.append(f"userId={broker_user_id}")
                    elif p:
                        cookie_parts.append(p)
                if not seen_authtoken:
                    cookie_parts.append(f"authtoken={authtoken}")
                full_cookie = "; ".join(cookie_parts)
            else:
                full_cookie = (
                    f"authtoken={authtoken}; user_id={broker_user_id}; locale=en"
                )
            return authtoken, broker_user_id, full_cookie

    async def _detect_stockity_currency(self, cookie: str) -> tuple[str, dict[str, float]]:
        """Detect account currency and balances from Stockity balance API.

        Returns:
            Tuple of (Currency code, dict of account balances), defaults to ("IDR", {}) on failure.
        """
        import re as _re
        authtoken = ""
        dev_id = ""
        tz = "Asia%2FJakarta"
        m = _re.search(r'authtoken=([^;]+)', cookie)
        if m:
            authtoken = m.group(1)
        m = _re.search(r'device_id=([^;]+)', cookie)
        if m:
            dev_id = m.group(1)
        m = _re.search(r'user_timezone=([^;]+)', cookie)
        if m:
            tz = m.group(1)
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "authorization-token": authtoken,
            "device-id": dev_id,
            "device-type": "web",
            "user-timezone": tz.replace("%2F", "/"),
            "cookie": cookie,
            "origin": "https://stockity.id",
            "referer": "https://stockity.id/",
            "cache-control": "no-cache, no-store, must-revalidate",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36"
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
                currency = "IDR"
                balances = {}
                for acc in accounts:
                    c = acc.get("currency", "")
                    if c and currency == "IDR":
                        currency = c
                    
                    acc_type = acc.get("account_type", "unknown")
                    amount = acc.get("amount", 0) / 100.0  # Stockity uses cents
                    balances[acc_type] = amount
                
                LOG.info(
                    "Detected currency: %s, balances: %s", currency, balances
                )
                return currency, balances
        except Exception as e:
            LOG.warning("Currency detection failed, defaulting to IDR: %s", e)

        return "IDR", {}

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
        creds_enc = self._encrypt_creds({
            "credentials": json.dumps({"app_id": app_id, "secret": secret}),
        })
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, credentials, currency,
                status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'USD', 'active', ?, ?)""",
            (
                user_id,
                "deriv",
                label,
                creds_enc["credentials"],
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
        creds_enc = self._encrypt_creds({
            "credentials": json.dumps({"exchange": exchange, "api_key": api_key, "api_secret": api_secret}),
        })
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, credentials, currency,
                status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'USD', 'active', ?, ?)""",
            (
                user_id,
                "ccxt",
                label,
                creds_enc["credentials"],
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
        creds_enc = self._encrypt_creds({
            "credentials": json.dumps({"ea_key": ea_key}),
        })
        store.execute(
            """INSERT OR REPLACE INTO user_platforms
               (user_id, platform, label, credentials, currency,
                status, linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'USD', 'active', ?, ?)""",
            (
                user_id,
                "mt5",
                label,
                creds_enc["credentials"],
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
        """Get all linked platforms for a user (credentials decrypted in-memory)."""
        cols = ["id", "user_id", "platform", "label", "email", "password", "credentials",
                "currency", "broker_user_id", "status", "linked_at", "updated_at"]
        rows = _storage().fetchall(
            "SELECT * FROM user_platforms WHERE user_id=? AND status='active' ORDER BY linked_at",
            (user_id,),
        )
        return [self._decrypt_row(dict(zip(cols, r))) for r in rows]

    async def get_platform_credentials(self, user_id: str, platform: str) -> dict[str, Any] | None:
        """Get stored credentials for a specific platform (decrypted in-memory)."""
        cols = ["id", "user_id", "platform", "label", "email", "password", "credentials",
                "currency", "broker_user_id", "status", "linked_at", "updated_at"]
        row = _storage().fetchone(
            "SELECT * FROM user_platforms WHERE user_id=? AND platform=? AND status='active'",
            (user_id, platform),
        )
        return self._decrypt_row(dict(zip(cols, row))) if row else None

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
            authtoken, broker_user_id, full_cookie = await self._stockity_login(
                creds["email"], creds["password"]
            )
        except Exception as e:
            LOG.error("Cookie refresh failed for user %s: %s", user_id, e)
            return None

        cookie = full_cookie
        store = _storage()
        creds_enc = self._encrypt_creds({
            "credentials": json.dumps({"cookie": cookie}),
        })
        sql = (
            "UPDATE user_platforms SET credentials=?, updated_at=? "
            "WHERE user_id=? AND platform='stockity'"
        )
        store.execute(sql, (creds_enc["credentials"], _now(), user_id))

        LOG.info("Cookie refreshed for user %s", user_id)
        return cookie
