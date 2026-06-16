"""Exhaustive inline-button and menu navigation E2E tests.

Tests every visible inline button reachable from the main menu of
@agent_1ai2_bot using the actual bot labels observed via Telethon.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    assert_has_buttons,
    assert_response_contains,
    click_button_text,
    send_callback,
    send_command,
)


async def _start(client, bot, rate_limiter):
    return await send_command(client, bot, "/start", rate_limiter, timeout=15.0)


class TestMainMenuNavigation:
    """Navigate through every top-level menu and back."""

    @pytest.mark.asyncio
    async def test_start_shows_main_menu(self, telethon_client, bot_entity, rate_limiter):
        result = await _start(telethon_client, bot_entity, rate_limiter)
        assert result.success
        assert assert_has_buttons(result)
        assert assert_response_contains(result, ["vilona", "trading", "system"])

    @pytest.mark.asyncio
    async def test_menu_signals(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "SIGNAL", rate_limiter)
        assert result.success, f"Signals menu failed: {result.error}"
        assert assert_response_contains(result, ["signal", "system"])
        assert assert_has_buttons(result)

    @pytest.mark.asyncio
    async def test_menu_market(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "MARKET", rate_limiter)
        assert result.success, f"Market menu failed: {result.error}"
        assert assert_response_contains(result, ["market", "data"])

    @pytest.mark.asyncio
    async def test_menu_history(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "HISTORY", rate_limiter)
        assert result.success
        assert assert_response_contains(result, ["history"])

    @pytest.mark.asyncio
    async def test_menu_account(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "ACCOUNT", rate_limiter)
        assert result.success
        assert assert_response_contains(result, ["account"])

    @pytest.mark.asyncio
    async def test_menu_whitelabel(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "WHITELABEL", rate_limiter)
        assert result.success
        assert assert_response_contains(result, ["whitelabel", "reseller"])

    @pytest.mark.asyncio
    async def test_menu_help(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "HELP", rate_limiter)
        assert result.success
        assert assert_response_contains(result, ["help", "command"])

    @pytest.mark.asyncio
    async def test_menu_panduan(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "PANDUAN", rate_limiter)
        assert result.success
        assert assert_response_contains(result, ["panduan"])

    @pytest.mark.asyncio
    async def test_menu_back_to_main(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "SIGNAL", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "BACK", rate_limiter)
        assert result.success, f"Back to main failed: {result.error}"
        assert assert_response_contains(result, ["vilona"])

    @pytest.mark.asyncio
    async def test_menu_home_from_market(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "MARKET", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "HOME", rate_limiter)
        assert result.success, f"Home failed: {result.error}"
        assert assert_response_contains(result, ["vilona"])


class TestSignalMenuButtons:
    """All buttons under Signal System menu."""

    @pytest.mark.asyncio
    async def test_cmd_signal_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "SIGNAL", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Live Signal", rate_limiter)
        assert result.success, f"Live Signal failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_whale_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "SIGNAL", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Whale", rate_limiter)
        assert result.success, f"Whale Scan failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_menu_analysis_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "SIGNAL", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Technical Analysis", rate_limiter
        )
        assert result.success
        assert assert_response_contains(result, ["analysis"])


class TestMarketMenuButtons:
    """Buttons under Market Data menu."""

    @pytest.mark.asyncio
    async def test_cmd_data_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "MARKET", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Market Data", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_price_gold_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "MARKET", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Gold", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_killzone_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "MARKET", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Killzone", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_session_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "MARKET", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Session Levels", rate_limiter
        )
        assert result.success
        assert result.response_text


class TestHistoryMenuButtons:
    """Buttons under Trade History menu."""

    @pytest.mark.asyncio
    async def test_cmd_winrate_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "HISTORY", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Win Rate", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_recap_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "HISTORY", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "Daily Recap", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_history_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "HISTORY", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Trade History", rate_limiter
        )
        assert result.success
        assert result.response_text


class TestAccountMenuButtons:
    """Buttons under Account menu."""

    @pytest.mark.asyncio
    async def test_cmd_status_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "ACCOUNT", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "My Status", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_myid_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "ACCOUNT", rate_limiter)
        result = await click_button_text(telethon_client, bot_entity, "My ID", rate_limiter)
        assert result.success
        assert result.response_text


class TestWhitelabelMenuButtons:
    """Buttons under Whitelabel menu."""

    @pytest.mark.asyncio
    async def test_wl_status_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "WHITELABEL", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Status Whitelabel", rate_limiter, timeout=30.0
        )
        assert result.success, f"wl_status failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_wl_referral_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "WHITELABEL", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Link Referral", rate_limiter, timeout=30.0
        )
        assert result.success, f"wl_referral failed: {result.error}"
        assert result.response_text


class TestHelpAndPanduanButtons:
    """Help and Panduan menu buttons."""

    @pytest.mark.asyncio
    async def test_cmd_help_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "HELP", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "All Commands", rate_limiter
        )
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cmd_panduan_button(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "HELP", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Panduan Lengkap", rate_limiter, timeout=30.0
        )
        assert result.success, f"panduan button failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_menu_panduan_then_analisa(self, telethon_client, bot_entity, rate_limiter):
        await _start(telethon_client, bot_entity, rate_limiter)
        await click_button_text(telethon_client, bot_entity, "PANDUAN", rate_limiter)
        result = await click_button_text(
            telethon_client, bot_entity, "Cara Analisa", rate_limiter, timeout=30.0
        )
        assert result.success, f"cara analisa failed: {result.error}"
        assert result.response_text


class TestCallbackErrorHandling:
    """Invalid / unknown callback data."""

    @pytest.mark.asyncio
    async def test_invalid_callback(self, telethon_client, bot_entity, rate_limiter):
        result = await send_callback(
            telethon_client, bot_entity, "menu:nonexistent", rate_limiter
        )
        error = (result.error or "").lower()
        assert result.success or "not found" in error or "no message found" in error
