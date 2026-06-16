"""Exhaustive E2E command tests for unified bot @agent_1ai2_bot.

Covers every registered /command plus sad-flow and edge cases.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    assert_has_buttons,
    assert_response_contains,
    assert_response_time,
    send_command,
)


class TestStartAndHelp:
    """Core onboarding commands."""

    @pytest.mark.asyncio
    async def test_start_welcome_and_buttons(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/start", rate_limiter)
        assert result.success, f"/start failed: {result.error}"
        assert result.response_text
        assert assert_response_time(result, max_seconds=5.0)
        assert assert_response_contains(result, ["vilona", "ai", "trading"])
        assert assert_has_buttons(result)

    @pytest.mark.asyncio
    async def test_start_empty_args(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/start extra", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_help_command_list(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/help", rate_limiter)
        assert result.success, f"/help failed: {result.error}"
        assert result.response_text
        assert assert_response_time(result)
        assert assert_response_contains(result, ["vilona", "command", "center"])


class TestStatusAndIdentity:
    """User status and identity commands."""

    @pytest.mark.asyncio
    async def test_status_response(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/status", rate_limiter)
        assert result.success, f"/status failed: {result.error}"
        assert result.response_text
        assert assert_response_contains(result, ["vilona", "status"])

    @pytest.mark.asyncio
    async def test_myid_returns_id(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/myid", rate_limiter)
        assert result.success, f"/myid failed: {result.error}"
        assert result.response_text
        assert assert_response_contains(result, ["telegram id"])

    @pytest.mark.asyncio
    async def test_symbols_list(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/symbols", rate_limiter)
        assert result.success, f"/symbols failed: {result.error}"
        assert result.response_text
        assert assert_response_contains(result, ["available", "symbol"])


class TestSignalCommands:
    """Signal generation commands — full input-to-output coverage."""

    @pytest.mark.asyncio
    async def test_signal_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal gold", rate_limiter)
        assert result.success, f"/signal gold failed: {result.error}"
        assert result.response_text
        text = result.response_text.lower()
        assert "entry" in text or "buy" in text or "sell" in text, "Missing signal action"
        assert "sl" in text or "stop" in text, "Missing SL"
        assert "tp" in text or "take" in text, "Missing TP"

    @pytest.mark.asyncio
    async def test_signal_btc(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal btc", rate_limiter)
        assert result.success, f"/signal btc failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_signal_eth(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal eth", rate_limiter)
        assert result.success, f"/signal eth failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_signal_no_args(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal", rate_limiter)
        assert result.success, f"/signal no args failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_signal_invalid_symbol(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal xyz999", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_mtf_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/mtf gold", rate_limiter)
        assert result.success, f"/mtf failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_engines_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/engines gold", rate_limiter)
        assert result.success, f"/engines failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_readings_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/readings gold", rate_limiter)
        assert result.success, f"/readings failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_pulse_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/pulse gold", rate_limiter)
        assert result.success, f"/pulse failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_analyze_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/analyze gold", rate_limiter)
        assert result.success, f"/analyze failed: {result.error}"
        assert result.response_text


class TestMarketDataCommands:
    """Market data and analysis commands."""

    @pytest.mark.asyncio
    async def test_price_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/price gold", rate_limiter)
        assert result.success, f"/price gold failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_price_btc(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/price btc", rate_limiter)
        assert result.success, f"/price btc failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_price_no_args(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/price", rate_limiter)
        assert result.success, f"/price no args failed: {result.error}"
        assert result.response_text
        assert assert_response_contains(result, ["gunakan", "pair"])

    @pytest.mark.asyncio
    async def test_data(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/data", rate_limiter)
        assert result.success, f"/data failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_killzone(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/killzone", rate_limiter)
        assert result.success, f"/killzone failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_session(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/session gold", rate_limiter)
        assert result.success, f"/session failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_structure_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/structure gold", rate_limiter)
        assert result.success, f"/structure failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_zones_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/zones gold", rate_limiter)
        assert result.success, f"/zones failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_levels_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/levels gold", rate_limiter)
        assert result.success, f"/levels failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_fvg_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/fvg gold", rate_limiter)
        assert result.success, f"/fvg failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_sweep_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/sweep gold", rate_limiter)
        assert result.success, f"/sweep failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_whale_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/whale gold", rate_limiter)
        assert result.success, f"/whale failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_briefing(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/briefing", rate_limiter)
        assert result.success, f"/briefing failed: {result.error}"
        assert result.response_text


class TestHistoryAndStats:
    """History, stats, and recap commands."""

    @pytest.mark.asyncio
    async def test_history(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/history", rate_limiter)
        assert result.success, f"/history failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_recap(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/recap", rate_limiter)
        assert result.success, f"/recap failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_winrate(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/winrate", rate_limiter)
        assert result.success, f"/winrate failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_dashboard(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/dashboard", rate_limiter)
        assert result.success, f"/dashboard failed: {result.error}"
        assert result.response_text


class TestSubscriptionAndPayment:
    """Subscription, donation, and payment-related commands."""

    @pytest.mark.asyncio
    async def test_subscribe(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/subscribe", rate_limiter)
        assert result.success, f"/subscribe failed: {result.error}"
        assert result.response_text
        assert assert_response_contains(result, ["paket", "langganan", "basic", "pro"])

    @pytest.mark.asyncio
    async def test_autosync(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/autosync", rate_limiter)
        assert result.success, f"/autosync failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_donate(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/donate", rate_limiter, timeout=15.0
        )
        assert result.success, f"/donate failed: {result.error}"
        assert result.response_text is not None

    @pytest.mark.asyncio
    async def test_settings(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/settings", rate_limiter)
        assert result.success, f"/settings failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_mykey(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/mykey", rate_limiter)
        assert result.success, f"/mykey failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_buykey(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/buykey", rate_limiter)
        assert result.success, f"/buykey failed: {result.error}"
        assert result.response_text


class TestBridgeAndEA:
    """Bridge, EA, and Stockity commands."""

    @pytest.mark.asyncio
    async def test_bridge_status(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/bridge_status", rate_limiter)
        assert result.success, f"/bridge_status failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_ea(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/ea", rate_limiter)
        assert result.success, f"/ea failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_stockity(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/stockity", rate_limiter)
        assert result.success, f"/stockity failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_mapping_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/mapping gold", rate_limiter)
        assert result.success, f"/mapping failed: {result.error}"
        assert result.response_text


class TestAdvancedAndAhz:
    """Advanced features: AHZ, hunt, S-tier, referral, portfolio."""

    @pytest.mark.asyncio
    async def test_ahz_radar(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/ahz_radar", rate_limiter)
        assert result.success, f"/ahz_radar failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_ahz_patterns(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/ahz_patterns", rate_limiter)
        assert result.success, f"/ahz_patterns failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_hunt_toggle(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/hunt_toggle", rate_limiter)
        assert result.success, f"/hunt_toggle failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_stier(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/stier", rate_limiter)
        assert result.success, f"/stier failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_referral(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/referral", rate_limiter)
        assert result.success, f"/referral failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_portfolio(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/portfolio", rate_limiter)
        assert result.success, f"/portfolio failed: {result.error}"
        assert result.response_text


class TestPanduanCommands:
    """Panduan (guide) commands."""

    @pytest.mark.asyncio
    async def test_panduan(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/panduan", rate_limiter)
        assert result.success, f"/panduan failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cara_analisa(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/cara_analisa", rate_limiter)
        assert result.success, f"/cara_analisa failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cara_baca(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/cara_baca", rate_limiter)
        assert result.success, f"/cara_baca failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cara_pasang(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/cara_pasang", rate_limiter)
        assert result.success, f"/cara_pasang failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cara_ea(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/cara_ea", rate_limiter)
        assert result.success, f"/cara_ea failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_cara_trailing(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/cara_trailing", rate_limiter)
        assert result.success, f"/cara_trailing failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_alasan_sinyal(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/alasan_sinyal", rate_limiter)
        assert result.success, f"/alasan_sinyal failed: {result.error}"
        assert result.response_text


class TestSadFlowAndEdgeCases:
    """Invalid commands, edge cases, and error handling."""

    @pytest.mark.asyncio
    async def test_invalid_command(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/notacommand", rate_limiter)
        assert result.success, "Bot should respond to unknown command"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_empty_command_ignored(self, telethon_client, bot_entity, rate_limiter):
        # Telegram often does not route bare "/" to any handler.
        result = await send_command(telethon_client, bot_entity, "/", rate_limiter, timeout=5.0)
        # No crash is the real invariant.
        assert (
            "error" not in (result.error or "").lower() or "timeout" in (result.error or "").lower()
        )

    @pytest.mark.asyncio
    async def test_very_long_command(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/signal " + "a" * 2000, rate_limiter
        )
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_unicode_command(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/start 🎉", rate_limiter)
        assert result.success
        assert result.response_text

    @pytest.mark.asyncio
    async def test_command_with_leading_space(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "  /start", rate_limiter)
        assert result.success
        assert result.response_text
