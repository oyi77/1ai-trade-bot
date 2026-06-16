"""Admin-only command E2E tests for @agent_1ai2_bot.

Tests access control, admin commands, and fraud scenarios.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    assert_response_contains,
    send_command,
)


class TestAdminAccessControl:
    """Admin-only commands behavior for the current test account.

    The test session may or may not be configured as admin in the bot.
    These tests assert that the bot responds appropriately either way.
    """

    @pytest.mark.asyncio
    async def test_genkey_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/genkey testuser", rate_limiter)
        assert result.success
        assert result.response_text
        text = result.response_text.lower()
        assert any(k in text for k in ["license key generated", "hanya admin", "admin only", "⛔"])

    @pytest.mark.asyncio
    async def test_listkeys_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/listkeys", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_revokekey_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/revokekey abc", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_activate_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/activate key123", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_restart_bot_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/restart_bot", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_reminder_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/reminder hello", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_bridge_full_status_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/bridge_full_status", rate_limiter
        )
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_settrailing_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/settrailing on", rate_limiter)
        assert result.success
        assert result.response_text


class TestAdminCommandsForAdminSession:
    """These only pass if the test session belongs to ADMIN_CHAT_ID.

    Skipped by default because primary test session may not be the admin.
    """

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Test session may not be the admin user")
    async def test_genkey_admin(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/genkey testsession", rate_limiter
        )
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Test session may not be the admin user")
    async def test_listkeys_admin(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/listkeys", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Test session may not be the admin user")
    async def test_dashboard_admin(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/dashboard", rate_limiter)
        assert result.success
        assert result.response_text


class TestFraudScenarios:
    """Payment / subscription fraud and misuse."""

    @pytest.mark.asyncio
    async def test_trade_yes_without_pending_signal(
        self, telethon_client, bot_entity, rate_limiter
    ):
        result = await send_command(telethon_client, bot_entity, "/trade_yes", rate_limiter)
        assert result.success
        assert result.response_text
        assert assert_response_contains(result, ["tidak ada sinyal", "analyze"])

    @pytest.mark.asyncio
    async def test_trade_no_without_pending_signal(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/trade_no", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_donation_input_state_without_donate(
        self, telethon_client, bot_entity, rate_limiter
    ):
        result = await send_command(
            telethon_client, bot_entity, "/donation_input 999", rate_limiter
        )
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_link_missing_args(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/link stockity", rate_limiter)
        assert result.success
        assert result.response_text
        assert assert_response_contains(result, ["gunakan", "/link", "contoh"])

    @pytest.mark.asyncio
    async def test_claim_without_earnings(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/claim", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_autotrade_requires_donor(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/autotrade on", rate_limiter)
        assert result.success
        assert result.response_text
        assert assert_response_contains(result, ["subscriber", "subscribe"])

    @pytest.mark.asyncio
    async def test_trailing_requires_donor(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/trailing", rate_limiter)
        assert result.success
        assert result.response_text
        assert assert_response_contains(result, ["subscriber", "donor", "subscribe"])
