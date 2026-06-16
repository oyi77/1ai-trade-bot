"""Connection resilience and rate-limiting E2E tests.

Also covers duplicate-message prevention and flood protection.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.e2e.conftest import (
    RateLimiter,
    send_command,
)


class TestRateLimiting:
    """Flood protection and rate limiting."""

    @pytest.mark.asyncio
    async def test_rapid_commands_do_not_crash(self, telethon_client, bot_entity, rate_limiter):
        results = []
        for _ in range(5):
            result = await send_command(telethon_client, bot_entity, "/start", rate_limiter)
            results.append(result)

        assert all(r.success for r in results), "Some rapid commands failed"

    @pytest.mark.asyncio
    async def test_no_rate_limiter_can_cause_flood(self, telethon_client, bot_entity):
        # This intentionally sends commands quickly to verify Telegram behavior.
        # We expect at least some responses, but not necessarily all.
        results = []
        for _ in range(3):
            result = await send_command(
                telethon_client, bot_entity, "/start", rate_limiter=None, timeout=8.0
            )
            results.append(result)
            await asyncio.sleep(0.5)

        # At least one must succeed; bot should not crash.
        assert any(r.success for r in results), "Bot crashed under rapid commands"

    @pytest.mark.asyncio
    async def test_rate_limiter_enforces_delay(self, telethon_client, bot_entity):
        limiter = RateLimiter(min_delay=2.0)
        start = time.time()
        for _ in range(2):
            await send_command(telethon_client, bot_entity, "/myid", limiter)
        elapsed = time.time() - start
        assert elapsed >= 2.0, "Rate limiter did not enforce delay"


class TestConnectionResilience:
    """Bot reconnects and continues serving after a restart cycle."""

    @pytest.mark.asyncio
    async def test_command_works_after_bot_restart(self, telethon_client, bot_entity, rate_limiter):
        # Verify normal operation before restart
        before = await send_command(telethon_client, bot_entity, "/start", rate_limiter)
        assert before.success, f"Pre-restart /start failed: {before.error}"

        # Restart the bot process
        import subprocess

        subprocess.run(["pm2", "restart", "agent-1ai2-bot"], check=True)
        await asyncio.sleep(10)

        # Verify operation after restart
        after = await send_command(
            telethon_client, bot_entity, "/start", rate_limiter, timeout=15.0
        )
        assert after.success, f"Post-restart /start failed: {after.error}"

    @pytest.mark.asyncio
    async def test_no_duplicate_welcome_messages(self, telethon_client, bot_entity, rate_limiter):
        result1 = await send_command(telethon_client, bot_entity, "/start", rate_limiter)
        result2 = await send_command(telethon_client, bot_entity, "/start", rate_limiter)
        assert result1.success and result2.success
        assert result1.response_text and result2.response_text

        # Normalize to avoid formatting differences
        a = result1.response_text.replace(" ", "").replace("\n", "")
        b = result2.response_text.replace(" ", "").replace("\n", "")
        assert a != b, "Bot sent duplicate welcome messages"


class TestSessionResilience:
    """Telethon session reconnects if needed."""

    @pytest.mark.asyncio
    async def test_session_stays_authorized(self, telethon_client, bot_entity, rate_limiter):
        # Run several commands and verify the session stays valid.
        for cmd in ["/start", "/status", "/myid", "/help"]:
            result = await send_command(telethon_client, bot_entity, cmd, rate_limiter)
            assert result.success, f"{cmd} failed during resilience check"
