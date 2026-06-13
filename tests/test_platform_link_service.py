"""Tests for tradebot/services/platform_link_service.py — account linking."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradebot.services.platform_link_service import PlatformLinkError, PlatformLinkService


class TestPlatformLinkService:
    """PlatformLinkService: linking, unlinking, querying, and cookie refresh."""

    @pytest.fixture(autouse=True)
    def _mock_repo(self):
        """Replace get_repo with a MagicMock for every test."""
        with patch("tradebot.services.platform_link_service.get_repo") as m:
            self.repo = MagicMock()
            m.return_value = self.repo
            yield

    @pytest.fixture
    def svc(self) -> PlatformLinkService:
        return PlatformLinkService()

    # ── Stockity ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_link_stockity_calls_login_and_stores_credentials(self, svc):
        """link_stockity() POSTs to login API, extracts authtoken/user_id, stores."""
        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.json.return_value = {
            "data": {"authtoken": "tok123", "user_id": "uid456"}
        }
        balance_resp = MagicMock()
        balance_resp.json.return_value = {
            "data": [{"currency": "IDR", "account_type": "real"}]
        }

        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.get = AsyncMock(return_value=balance_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tradebot.services.platform_link_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await svc.link_stockity("user1", "a@b.com", "secret")

        assert result["success"] is True
        assert result["platform"] == "stockity"
        assert result["currency"] == "IDR"
        assert result["broker_user_id"] == "uid456"
        assert result["label"] == "main"

        # Login POST called
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args[1]
        payload = json.loads(call_kwargs["content"])
        assert payload["email"] == "a@b.com"
        assert payload["password"] == "secret"

        # Balance GET called
        mock_client.get.assert_called_once()

        # DB insert
        self.repo.execute.assert_called_once()
        sql, params = self.repo.execute.call_args[0]
        assert "INSERT OR REPLACE INTO user_platforms" in sql
        assert params[0] == "user1"
        assert params[1] == "stockity"
        assert params[3] == "a@b.com"
        assert params[4] == "secret"
        creds = json.loads(params[5])
        assert "cookie" in creds
        assert "tok123" in creds["cookie"]
        assert params[6] == "IDR"
        assert params[7] == "uid456"

    @pytest.mark.asyncio
    async def test_link_stockity_detects_currency_from_balance_api(self, svc):
        """link_stockity() detects currency from balance API response."""
        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.json.return_value = {
            "data": {"authtoken": "tok1", "user_id": "uid1"}
        }
        balance_resp = MagicMock()
        balance_resp.json.return_value = {
            "data": [{"currency": "USD", "account_type": "real"}]
        }

        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.get = AsyncMock(return_value=balance_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tradebot.services.platform_link_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await svc.link_stockity("user1", "a@b.com", "secret")

        assert result["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_link_stockity_defaults_to_idr_on_balance_failure(self, svc):
        """link_stockity() defaults to IDR when balance API fails."""
        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.json.return_value = {
            "data": {"authtoken": "tok1", "user_id": "uid1"}
        }
        # Balance API raises
        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.get = AsyncMock(side_effect=Exception("API down"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tradebot.services.platform_link_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await svc.link_stockity("user1", "a@b.com", "secret")

        assert result["currency"] == "IDR"

    @pytest.mark.asyncio
    async def test_link_stockity_raises_on_invalid_login(self, svc):
        """PlatformLinkError raised on invalid stockity login (missing tokens)."""
        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.json.return_value = {"data": {}}  # no authtoken/user_id

        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "tradebot.services.platform_link_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(PlatformLinkError, match="Login gagal"),
        ):
            await svc.link_stockity("user1", "bad@b.com", "wrong")

    @pytest.mark.asyncio
    async def test_link_stockity_raises_on_http_error(self, svc):
        """PlatformLinkError raised when login API returns HTTP error."""
        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.raise_for_status.side_effect = Exception("HTTP 401")

        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "tradebot.services.platform_link_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(PlatformLinkError, match="Login gagal"),
        ):
            await svc.link_stockity("user1", "bad@b.com", "wrong")

    # ── Deriv ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_link_deriv_stores_app_id_and_secret(self, svc):
        """link_deriv() stores app_id and secret in DB."""
        result = await svc.link_deriv("user1", "12345", "mysecret")

        assert result["success"] is True
        assert result["platform"] == "deriv"
        assert result["currency"] == "USD"
        assert result["label"] == "main"

        self.repo.execute.assert_called_once()
        sql, params = self.repo.execute.call_args[0]
        assert "INSERT OR REPLACE INTO user_platforms" in sql
        assert params[0] == "user1"
        assert params[1] == "deriv"
        creds = json.loads(params[3])
        assert creds["app_id"] == "12345"
        assert creds["secret"] == "mysecret"

    @pytest.mark.asyncio
    async def test_link_deriv_with_custom_label(self, svc):
        """link_deriv() accepts a custom label."""
        result = await svc.link_deriv("user1", "12345", "secret", label="trading")

        assert result["label"] == "trading"
        _, params = self.repo.execute.call_args[0]
        assert params[2] == "trading"

    # ── CCXT ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_link_ccxt_stores_exchange_and_keys(self, svc):
        """link_ccxt() stores exchange, api_key, api_secret in DB."""
        result = await svc.link_ccxt("user1", "binance", "key123", "sec456")

        assert result["success"] is True
        assert result["platform"] == "ccxt"
        assert result["exchange"] == "binance"
        assert result["currency"] == "USD"
        assert result["label"] == "main"

        self.repo.execute.assert_called_once()
        sql, params = self.repo.execute.call_args[0]
        assert params[0] == "user1"
        assert params[1] == "ccxt"
        creds = json.loads(params[3])
        assert creds["exchange"] == "binance"
        assert creds["api_key"] == "key123"
        assert creds["api_secret"] == "sec456"

    # ── MT5 ──────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_link_mt5_stores_ea_key(self, svc):
        """link_mt5() stores ea_key in DB."""
        result = await svc.link_mt5("user1", "ea-key-xyz")

        assert result["success"] is True
        assert result["platform"] == "mt5"
        assert result["currency"] == "USD"
        assert result["label"] == "main"

        self.repo.execute.assert_called_once()
        sql, params = self.repo.execute.call_args[0]
        assert params[0] == "user1"
        assert params[1] == "mt5"
        creds = json.loads(params[3])
        assert creds["ea_key"] == "ea-key-xyz"

    # ── Unlink ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_unlink_platform_removes_platform_link(self, svc):
        """unlink_platform() deletes the platform link from DB."""
        result = await svc.unlink_platform("user1", "stockity")

        assert result is True
        self.repo.execute.assert_called_once_with(
            "DELETE FROM user_platforms WHERE user_id=? AND platform=?",
            ("user1", "stockity"),
        )

    # ── Query ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_linked_platforms_returns_linked_platforms(self, svc):
        """get_linked_platforms() returns all active platforms for a user."""
        row1 = {"user_id": "user1", "platform": "stockity", "status": "active"}
        row2 = {"user_id": "user1", "platform": "deriv", "status": "active"}
        self.repo.fetchall.return_value = [row1, row2]

        result = await svc.get_linked_platforms("user1")

        assert len(result) == 2
        assert result[0]["platform"] == "stockity"
        assert result[1]["platform"] == "deriv"
        self.repo.fetchall.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_linked_platforms_returns_empty_list_when_none(self, svc):
        """get_linked_platforms() returns empty list when no platforms linked."""
        self.repo.fetchall.return_value = []

        result = await svc.get_linked_platforms("user1")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_platform_credentials_returns_stored_credentials(self, svc):
        """get_platform_credentials() returns stored credentials for a platform."""
        row = {
            "user_id": "user1",
            "platform": "stockity",
            "email": "a@b.com",
            "password": "secret",
        }
        self.repo.fetchone.return_value = row

        result = await svc.get_platform_credentials("user1", "stockity")

        assert result is not None
        assert result["email"] == "a@b.com"
        assert result["password"] == "secret"
        self.repo.fetchone.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_platform_credentials_returns_none_when_not_found(self, svc):
        """get_platform_credentials() returns None when no matching row."""
        self.repo.fetchone.return_value = None

        result = await svc.get_platform_credentials("user1", "nonexistent")

        assert result is None

    # ── Cookie refresh ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_refresh_stockity_cookie_re_logs_in_and_updates_cookie(self, svc):
        """refresh_stockity_cookie() re-logins and updates stored cookie."""
        self.repo.fetchone.return_value = {
            "user_id": "user1",
            "platform": "stockity",
            "email": "a@b.com",
            "password": "secret",
        }

        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.json.return_value = {
            "data": {"authtoken": "newtok", "user_id": "uid456"}
        }
        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tradebot.services.platform_link_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            cookie = await svc.refresh_stockity_cookie("user1")

        assert cookie is not None
        assert "newtok" in cookie

        # Verify DB update
        update_calls = [
            c for c in self.repo.execute.call_args_list
            if "UPDATE user_platforms" in c[0][0]
        ]
        assert len(update_calls) == 1
        sql, params = update_calls[0][0]
        assert params[0] is not None
        creds = json.loads(params[0])
        assert "newtok" in creds["cookie"]
        assert params[2] == "user1"

    @pytest.mark.asyncio
    async def test_refresh_stockity_cookie_returns_none_when_no_stored_creds(self, svc):
        """refresh_stockity_cookie() returns None when no stored credentials."""
        self.repo.fetchone.return_value = None

        cookie = await svc.refresh_stockity_cookie("user1")

        assert cookie is None
        # No DB update should happen
        for call in self.repo.execute.call_args_list:
            assert "UPDATE" not in call[0][0]

    @pytest.mark.asyncio
    async def test_refresh_stockity_cookie_returns_none_on_login_failure(self, svc):
        """refresh_stockity_cookie() returns None when re-login fails."""
        self.repo.fetchone.return_value = {
            "user_id": "user1",
            "platform": "stockity",
            "email": "a@b.com",
            "password": "secret",
        }

        mock_client = AsyncMock()
        login_resp = MagicMock()
        login_resp.raise_for_status.side_effect = Exception("HTTP 401")
        mock_client.post = AsyncMock(return_value=login_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tradebot.services.platform_link_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            cookie = await svc.refresh_stockity_cookie("user1")

        assert cookie is None
