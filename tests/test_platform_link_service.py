"""Tests for tradebot/services/platform_link_service.py — encrypted credentials."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tradebot.security.crypto import FernetEncryptor, reset_encryptor
from tradebot.services.platform_link_service import PlatformLinkError, PlatformLinkService

# Shared test key
_TEST_KEY = Fernet.generate_key().decode()


def _enc(val: str) -> str:
    """Encrypt a value with the test key for use in mocked DB returns."""
    return Fernet(FernetEncryptor._ensure_urlsafe(_TEST_KEY.encode())).encrypt(val.encode()).decode()


@pytest.fixture(autouse=True)
def _setup_crypto():
    """Set VILONA_MASTER_KEY for every test and reset encryptor cache."""
    os.environ["VILONA_MASTER_KEY"] = _TEST_KEY
    reset_encryptor()
    yield
    reset_encryptor()
    os.environ.pop("VILONA_MASTER_KEY", None)


class TestPlatformLinkService:
    """PlatformLinkService: encrypted credential storage."""

    @pytest.fixture(autouse=True)
    def _mock_repo(self):
        with patch("tradebot.services.platform_link_service.get_repo") as m:
            self.repo = MagicMock()
            m.return_value = self.repo
            yield

    @pytest.fixture
    def svc(self) -> PlatformLinkService:
        return PlatformLinkService()

    # ── Stockity ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_link_stockity_calls_login_and_stores_encrypted_credentials(self, svc):
        """link_stockity() POSTs to login API, stores encrypted credentials."""
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

        # Balance GET called
        mock_client.get.assert_called_once()

        # DB insert — params should be encrypted
        self.repo.execute.assert_called_once()
        sql, params = self.repo.execute.call_args[0]
        assert "INSERT OR REPLACE INTO user_platforms" in sql
        assert params[0] == "user1"
        assert params[1] == "stockity"

        # email, password, credentials are Fernet tokens
        assert FernetEncryptor.is_encrypted(params[3])  # email
        assert FernetEncryptor.is_encrypted(params[4])  # password
        assert FernetEncryptor.is_encrypted(params[5])  # credentials

        # Decrypt to verify content
        enc = FernetEncryptor(key=_TEST_KEY)
        assert enc.decrypt_string(params[3]) == "a@b.com"
        assert enc.decrypt_string(params[4]) == "secret"
        creds = json.loads(enc.decrypt_string(params[5]))
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
        login_resp.json.return_value = {"data": {}}

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
    async def test_link_deriv_stores_encrypted_credentials(self, svc):
        """link_deriv() stores app_id and secret as encrypted JSON."""
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
        # credentials should be encrypted
        assert FernetEncryptor.is_encrypted(params[3])
        enc = FernetEncryptor(key=_TEST_KEY)
        creds = json.loads(enc.decrypt_string(params[3]))
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
    async def test_link_ccxt_stores_encrypted_keys(self, svc):
        """link_ccxt() stores exchange and keys as encrypted JSON."""
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
        assert FernetEncryptor.is_encrypted(params[3])
        enc = FernetEncryptor(key=_TEST_KEY)
        creds = json.loads(enc.decrypt_string(params[3]))
        assert creds["exchange"] == "binance"
        assert creds["api_key"] == "key123"
        assert creds["api_secret"] == "sec456"

    # ── MT5 ──────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_link_mt5_stores_encrypted_ea_key(self, svc):
        """link_mt5() stores ea_key as encrypted JSON."""
        result = await svc.link_mt5("user1", "ea-key-xyz")

        assert result["success"] is True
        assert result["platform"] == "mt5"
        assert result["currency"] == "USD"
        assert result["label"] == "main"

        self.repo.execute.assert_called_once()
        sql, params = self.repo.execute.call_args[0]
        assert params[0] == "user1"
        assert params[1] == "mt5"
        assert FernetEncryptor.is_encrypted(params[3])
        enc = FernetEncryptor(key=_TEST_KEY)
        creds = json.loads(enc.decrypt_string(params[3]))
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
    async def test_get_linked_platforms_decrypts_on_read(self, svc):
        """get_linked_platforms() returns decrypted values."""
        row1 = ("1", "user1", "stockity", "", _enc("a@b.com"), _enc("secret"),
                _enc('{"cookie":"abc"}'), "IDR", "uid456", "active", "2024-01-01", "2024-01-01")
        row2 = ("2", "user1", "deriv", "", "", "",
                _enc('{"app_id":"123","secret":"sec"}'), "USD", "", "active", "2024-01-01", "2024-01-01")
        self.repo.fetchall.return_value = [row1, row2]

        result = await svc.get_linked_platforms("user1")

        assert len(result) == 2
        assert result[0]["platform"] == "stockity"
        assert result[0]["email"] == "a@b.com"
        assert result[0]["password"] == "secret"
        assert "abc" in result[0]["credentials"]

        assert result[1]["platform"] == "deriv"
        assert result[1]["email"] == ""
        assert "app_id" in result[1]["credentials"]

    @pytest.mark.asyncio
    async def test_get_linked_platforms_returns_empty_list_when_none(self, svc):
        """get_linked_platforms() returns empty list when no platforms."""
        self.repo.fetchall.return_value = []

        result = await svc.get_linked_platforms("user1")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_platform_credentials_decrypts_on_read(self, svc):
        """get_platform_credentials() returns decrypted values."""
        row = ("1", "user1", "stockity", "", _enc("a@b.com"), _enc("secret"),
               _enc('{"cookie":"tok123"}'), "IDR", "uid456", "active", "2024-01-01", "2024-01-01")
        self.repo.fetchone.return_value = row

        result = await svc.get_platform_credentials("user1", "stockity")

        assert result is not None
        assert result["email"] == "a@b.com"
        assert result["password"] == "secret"
        assert "tok123" in result["credentials"]

    @pytest.mark.asyncio
    async def test_get_platform_credentials_returns_none_when_not_found(self, svc):
        """get_platform_credentials() returns None when no matching row."""
        self.repo.fetchone.return_value = None

        result = await svc.get_platform_credentials("user1", "nonexistent")

        assert result is None

    # ── Decryption safety ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_decrypt_row_passes_through_empty_fields(self, svc):
        """Empty strings in sensitive columns pass through decryption unscathed."""
        row = ("1", "user1", "stockity", "", "", "", "", "USD", "",
               "active", "2024-01-01", "2024-01-01")
        self.repo.fetchone.return_value = row

        result = await svc.get_platform_credentials("user1", "stockity")

        assert result is not None
        assert result["email"] == ""
        assert result["password"] == ""
        assert result["credentials"] == ""

    @pytest.mark.asyncio
    async def test_decrypt_row_passes_through_plaintext(self, svc):
        """Plaintext (non-encrypted) values pass through unchanged during migration."""
        # Simulate a row from before encryption was applied
        row = ("1", "user1", "stockity", "", "old@email.com", "hunter2",
               '{"cookie":"old_cookie"}', "IDR", "", "active", "2024-01-01", "2024-01-01")
        self.repo.fetchone.return_value = row

        result = await svc.get_platform_credentials("user1", "stockity")

        assert result is not None
        # decrypt_string returns plaintext as-is when not a Fernet token
        assert result["email"] == "old@email.com"
        assert result["password"] == "hunter2"
        assert "old_cookie" in result["credentials"]

    # ── Cookie refresh ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_refresh_stockity_cookie_stores_encrypted_cookie(self, svc):
        """refresh_stockity_cookie() re-logins and stores encrypted cookie."""
        # Mock decrypted return (decrypt passthrough since it's plaintext)
        self.repo.fetchone.return_value = (
            "1", "user1", "stockity", "", "a@b.com", "secret",
            '{"cookie":"old_cookie"}', "IDR", "uid456", "active",
            "2024-01-01", "2024-01-01",
        )
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

        # Verify DB update received encrypted credentials
        update_calls = [
            c for c in self.repo.execute.call_args_list
            if "UPDATE user_platforms" in c[0][0]
        ]
        assert len(update_calls) == 1
        sql, params = update_calls[0][0]
        assert FernetEncryptor.is_encrypted(params[0])
        enc = FernetEncryptor(key=_TEST_KEY)
        creds = json.loads(enc.decrypt_string(params[0]))
        assert "newtok" in creds["cookie"]
        assert params[2] == "user1"

    @pytest.mark.asyncio
    async def test_refresh_stockity_cookie_returns_none_when_no_stored_creds(self, svc):
        """refresh_stockity_cookie() returns None when no stored credentials."""
        self.repo.fetchone.return_value = None

        cookie = await svc.refresh_stockity_cookie("user1")

        assert cookie is None
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
