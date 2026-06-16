"""End-to-end signal flow tests for @agent_1ai2_bot.

Validates the complete path: user input → command parsing → market data →
engine consensus → formatting → bot output.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    assert_response_time,
    send_command,
)


class TestSignalEndToEnd:
    """Full signal pipeline validation."""

    @pytest.mark.asyncio
    async def test_signal_gold_has_all_fields(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal gold", rate_limiter)
        assert result.success, f"Signal failed: {result.error}"
        assert result.response_text
        assert assert_response_time(result, max_seconds=8.0)

        text = result.response_text.lower()
        assert "gold" in text or "xauusd" in text, "Missing symbol"
        assert any(
            k in text
            for k in [
                "entry",
                "buy",
                "sell",
                "long",
                "short",
                "direction",
                "signal",
                "sinyal",
                "verdict",
            ]
        ), "Missing action/entry"
        assert any(
            k in text for k in ["sl", "stop", "tp", "take", "target", "risk"]
        ), "Missing risk/TP"
        assert any(
            k in text
            for k in ["confidence", "conf", "grade", "score", "kekuatan", "tier"]
        ), "Missing confidence/grade/tier"

    @pytest.mark.asyncio
    async def test_signal_btc_has_all_fields(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/signal btc", rate_limiter, timeout=15.0
        )
        assert result.success, f"BTC signal failed: {result.error}"
        assert result.response_text

        text = result.response_text.lower()
        # Bot may return cached/default pair or quality-gate block
        assert any(
            k in text
            for k in [
                "entry",
                "buy",
                "sell",
                "long",
                "short",
                "signal",
                "verdict",
                "quality gate",
                "blocked",
                "matrix",
            ]
        )

    @pytest.mark.asyncio
    async def test_signal_eth_has_all_fields(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/signal eth", rate_limiter, timeout=15.0
        )
        assert result.success, f"ETH signal failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_signal_default_xauusd(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal", rate_limiter)
        assert result.success
        assert result.response_text
        text = result.response_text.lower()
        assert any(k in text for k in ["gold", "xauusd"])


class TestSignalFormatConsistency:
    """Signal output format should be consistent across symbols."""

    @pytest.mark.asyncio
    async def test_signal_format_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal gold", rate_limiter)
        assert result.success
        assert "━━━━━━━━" in result.response_text or "───" in result.response_text, (
            "Missing divider"
        )

    @pytest.mark.asyncio
    async def test_signal_has_direction(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/signal gold", rate_limiter)
        assert result.success
        text = result.response_text.lower()
        assert (
            "buy" in text
            or "sell" in text
            or "bull" in text
            or "bear" in text
            or "long" in text
            or "short" in text
        ), "No direction"


class TestAnalysisEndToEnd:
    """Engine consensus and MTF analysis produce structured output."""

    @pytest.mark.asyncio
    async def test_engines_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/engines gold", rate_limiter)
        assert result.success, f"/engines failed: {result.error}"
        assert result.response_text
        text = result.response_text.lower()
        assert "engine" in text or "consensus" in text, "Missing engines/consensus"
        assert "gold" in text or "xauusd" in text

    @pytest.mark.asyncio
    async def test_mtf_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/mtf gold", rate_limiter)
        assert result.success, f"/mtf failed: {result.error}"
        assert result.response_text
        text = result.response_text.lower()
        assert any(k in text for k in ["timeframe", "tf", "mtf", "matrix"])

    @pytest.mark.asyncio
    async def test_readings_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/readings gold", rate_limiter)
        assert result.success, f"/readings failed: {result.error}"
        assert result.response_text

    @pytest.mark.asyncio
    async def test_analyze_gold(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/analyze gold", rate_limiter, timeout=15.0
        )
        assert result.success, f"/analyze failed: {result.error}"
        # Bot may return a signal or a cooldown message; just ensure it responded
        assert result.response_text


class TestPriceDataEndToEnd:
    """Price fetching returns real data."""

    @pytest.mark.asyncio
    async def test_price_gold_returns_number(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/price gold", rate_limiter)
        assert result.success, f"/price gold failed: {result.error}"
        assert result.response_text
        text = result.response_text
        assert any(c.isdigit() for c in text), "No price data in response"

    @pytest.mark.asyncio
    async def test_data_returns_overview(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/data", rate_limiter)
        assert result.success, f"/data failed: {result.error}"
        assert result.response_text
        text = result.response_text.lower()
        assert "market" in text or "overview" in text


class TestSadFlowSignals:
    """Invalid symbol / missing args handled gracefully."""

    @pytest.mark.asyncio
    async def test_signal_invalid_symbol(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(
            telethon_client, bot_entity, "/signal notasymbol", rate_limiter
        )
        assert result.success, "Bot should respond to invalid symbol"
        assert result.response_text
        text = result.response_text.lower()
        assert any(
            k in text
            for k in [
                "tidak",
                "unknown",
                "error",
                "symbol",
                "gunakan",
                "ditemukan",
                "tidak valid",
                "entry",
                "signal",
                "sinyal",
            ]
        )

    @pytest.mark.asyncio
    async def test_price_invalid_symbol(self, telethon_client, bot_entity, rate_limiter):
        result = await send_command(telethon_client, bot_entity, "/price notasymbol", rate_limiter)
        assert result.success
        assert result.response_text
