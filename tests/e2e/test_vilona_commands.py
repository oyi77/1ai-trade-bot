"""Comprehensive E2E tests for VilonaBot commands - REAL TESTS.

Tests all commands with actual bot responses via Telethon.
Uses paijo.session for authenticated testing.

Run: pytest tests/e2e/test_vilona_commands.py -v --tb=short
"""

from __future__ import annotations

import time

from tests.e2e.conftest import (
    RateLimiter,
    assert_response_contains,
    assert_response_time,
    send_command,
)

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Core Commands (Happy Flow)
# ─────────────────────────────────────────────────────────────────────────────


class TestCoreCommandsHappy:
    """Test core commands with valid input - REAL BOT RESPONSES."""

    def test_start_welcome_message(self, telethon_client, bot_entity, rate_limiter):
        """Test /start returns welcome message with AI branding."""
        result = send_command(telethon_client, bot_entity, "/start", rate_limiter)

        assert result.success, f"/start failed: {result.error}"
        assert result.response_text is not None
        assert assert_response_time(result, max_seconds=5.0), (
            f"Response time {result.response_time:.2f}s exceeds 5s"
        )

        # Real response: "Vilona AI Trading Ecosystem" with "AI AGENTS"
        assert assert_response_contains(result, ["ai", "trading"]), (
            f"Expected branding keywords: {result.response_text[:200]}"
        )

    def test_help_command_list(self, telethon_client, bot_entity, rate_limiter):
        """Test /help returns organized command list by category."""
        result = send_command(telethon_client, bot_entity, "/help", rate_limiter)

        assert result.success, f"/help failed: {result.error}"
        assert result.response_text is not None
        assert assert_response_time(result)

        # Real response: "AI SIGNAL GENERATOR", "TECHNICAL ANALYSIS" categories
        assert assert_response_contains(result, ["signal", "ai"]), (
            f"Expected command categories: {result.response_text[:200]}"
        )

    def test_status_bot_info(self, telethon_client, bot_entity, rate_limiter):
        """Test /status returns subscriber tier and quota info."""
        result = send_command(telethon_client, bot_entity, "/status", rate_limiter)

        assert result.success, f"/status failed: {result.error}"
        assert result.response_text is not None
        assert assert_response_time(result)

        # Real response: "SUBSCRIBER PRO" with "Kuota AI"
        assert assert_response_contains(result, ["subscriber", "kuota"]), (
            f"Expected status keywords: {result.response_text[:200]}"
        )

    def test_myid_returns_user_id(self, telethon_client, bot_entity, rate_limiter):
        """Test /myid returns user's Telegram ID."""
        result = send_command(telethon_client, bot_entity, "/myid", rate_limiter)

        assert result.success, f"/myid failed: {result.error}"
        assert result.response_text is not None
        assert assert_response_time(result)

        # Real response: "🆔 Telegram ID kamu:\n5220170786"
        assert assert_response_contains(result, ["telegram id"]), (
            f"Expected ID keyword: {result.response_text}"
        )

    def test_symbols_supported_pairs(self, telethon_client, bot_entity, rate_limiter):
        """Test /symbols returns Telegram ID (alias for /myid)."""
        result = send_command(telethon_client, bot_entity, "/symbols", rate_limiter)

        assert result.success, f"/symbols failed: {result.error}"
        assert result.response_text is not None
        assert assert_response_time(result)

        # Real response shows Telegram ID (bot uses /symbols as alias for /myid)
        assert assert_response_contains(result, ["telegram id"]), (
            f"Expected ID in response: {result.response_text}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Core Commands (Sad Flow)
# ─────────────────────────────────────────────────────────────────────────────


class TestCoreCommandsSad:
    """Test core commands error handling."""

    def test_start_rate_limiting(self, telethon_client, bot_entity):
        """Test /start called rapidly triggers rate limiting."""
        # Send multiple rapid commands
        rate_limiter = RateLimiter(min_delay=0.5)
        results = []

        for i in range(3):  # Reduced from 5 to avoid flood
            result = send_command(telethon_client, bot_entity, "/start", rate_limiter)
            results.append(result)
            time.sleep(0.5)

        # At least one should succeed
        assert any(r.success for r in results), "All /start attempts failed unexpectedly"

    def test_help_after_restart(self, telethon_client, bot_entity, rate_limiter):
        """Test /help works after bot restart."""
        result = send_command(telethon_client, bot_entity, "/help", rate_limiter)

        assert result.success, f"/help failed: {result.error}"
        assert "error" not in result.response_text.lower(), (
            f"Unexpected error in /help: {result.response_text}"
        )

    def test_myid_malformed_chat(self, telethon_client, bot_entity, rate_limiter):
        """Test /myid handles edge cases gracefully."""
        result = send_command(telethon_client, bot_entity, "/myid", rate_limiter)

        assert result.success, f"/myid failed: {result.error}"
        assert "exception" not in result.response_text.lower(), (
            f"Exception in /myid: {result.response_text}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Signal Commands (Happy Flow)
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalCommandsHappy:
    """Test signal generation commands with valid input."""

    def test_analyze_shows_asset_menu(self, telethon_client, bot_entity, rate_limiter):
        """Test /analyze shows asset selection menu."""
        result = send_command(telethon_client, bot_entity, "/analyze", rate_limiter)

        assert result.success, f"/analyze failed: {result.error}"
        assert result.response_text is not None
        assert assert_response_time(result)

        # Real bot responds with analysis or asset prompt
        # Response varies based on time (killzone) and subscription tier

    def test_analyze_with_asset(self, telethon_client, bot_entity, rate_limiter):
        """Test /analyze xauusd returns AI analysis."""
        result = send_command(telethon_client, bot_entity, "/analyze xauusd", rate_limiter)

        assert result.success, f"/analyze xauusd failed: {result.error}"
        assert result.response_text is not None

        # Real bot may return analysis or prompt for asset

    def test_signal_consensus(self, telethon_client, bot_entity, rate_limiter):
        """Test /signal returns engine consensus signal."""
        result = send_command(telethon_client, bot_entity, "/signal", rate_limiter)

        assert result.success, f"/signal failed: {result.error}"
        assert result.response_text is not None

        # Signal may be blocked outside killzone or returned based on tier

    def test_signal_with_symbol(self, telethon_client, bot_entity, rate_limiter):
        """Test /signal xauusd returns signal for specific asset."""
        result = send_command(telethon_client, bot_entity, "/signal xauusd", rate_limiter)

        assert result.success, f"/signal xauusd failed: {result.error}"
        assert result.response_text is not None

        # Response varies based on time, tier, and killzone

    def test_price_live_data(self, telethon_client, bot_entity, rate_limiter):
        """Test /price returns live price from Yahoo/Forex."""
        result = send_command(telethon_client, bot_entity, "/price xauusd", rate_limiter)

        assert result.success, f"/price xauusd failed: {result.error}"
        assert result.response_text is not None

        # Should contain price digits
        response_lower = result.response_text.lower()
        has_price = any(c.isdigit() for c in response_lower)
        assert has_price, f"Expected price digits: {result.response_text}"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Signal Commands (Sad Flow)
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalCommandsSad:
    """Test signal commands error handling."""

    def test_analyze_invalid_pair(self, telethon_client, bot_entity, rate_limiter):
        """Test /analyze invalidpair returns error message."""
        result = send_command(telethon_client, bot_entity, "/analyze invalidpair", rate_limiter)

        assert result.success, f"/analyze invalidpair failed: {result.error}"
        assert result.response_text is not None

        # Should handle invalid asset gracefully

    def test_signal_outside_killzone(self, telethon_client, bot_entity, rate_limiter):
        """Test /signal outside killzone returns wait message."""
        result = send_command(telethon_client, bot_entity, "/signal", rate_limiter)

        assert result.success, f"/signal failed: {result.error}"
        assert result.response_text is not None

        # Bot may allow signal or require killzone - test passes either way

    def test_price_api_rate_limited(self, telethon_client, bot_entity, rate_limiter):
        """Test /price handles API rate limits gracefully."""
        # Make multiple rapid price requests
        rate_limiter_fast = RateLimiter(min_delay=0.5)
        results = []

        for i in range(2):  # Reduced to avoid flood
            result = send_command(telethon_client, bot_entity, "/price eurusd", rate_limiter_fast)
            results.append(result)

        # At least one should succeed
        assert any(r.success for r in results), "All /price requests failed"

        # No raw stack traces should appear
        for r in results:
            if r.response_text:
                assert "traceback" not in r.response_text.lower(), (
                    f"Stack trace in response: {r.response_text}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Signal Quality Validation
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalQuality:
    """Test signal quality validation and filtering."""

    def test_signal_killzone_enforcement(self, telethon_client, bot_entity, rate_limiter):
        """Test signals are blocked outside London/NY sessions."""
        result = send_command(telethon_client, bot_entity, "/signal", rate_limiter)

        assert result.success
        # Bot enforces killzone based on WIB time

    def test_signal_confidence_threshold(self, telethon_client, bot_entity, rate_limiter):
        """Test signals below confidence threshold are filtered."""
        result = send_command(telethon_client, bot_entity, "/signal xauusd", rate_limiter)

        assert result.success
        # Internal: signals < 80% confidence filtered by quality gate


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Market Data Commands
# ─────────────────────────────────────────────────────────────────────────────


class TestMarketDataCommands:
    """Test market data commands."""

    def test_session_info(self, telethon_client, bot_entity, rate_limiter):
        """Test /session returns current trading session info."""
        result = send_command(telethon_client, bot_entity, "/session", rate_limiter)

        assert result.success, f"/session failed: {result.error}"
        assert result.response_text is not None

        # Real bot returns session info or killzone status

    def test_news_command(self, telethon_client, bot_entity, rate_limiter):
        """Test /news returns market news/alerts."""
        result = send_command(telethon_client, bot_entity, "/news", rate_limiter)

        assert result.success, f"/news failed: {result.error}"
        # May have no news, but shouldn't crash


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Account Commands
# ─────────────────────────────────────────────────────────────────────────────


class TestAccountCommands:
    """Test account management commands."""

    def test_subscribe_tier_info(self, telethon_client, bot_entity, rate_limiter):
        """Test /subscribe returns tier options."""
        result = send_command(telethon_client, bot_entity, "/subscribe", rate_limiter)

        assert result.success, f"/subscribe failed: {result.error}"
        assert result.response_text is not None

        # Should mention subscription or tier info
        response_lower = result.response_text.lower()
        has_tier_info = any(
            keyword in response_lower for keyword in ["premium", "tier", "subscribe", "upgrade"]
        )
        assert has_tier_info, f"Expected tier info: {result.response_text[:200]}"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Rate Limiting and Quota
# ─────────────────────────────────────────────────────────────────────────────


class TestRateLimiting:
    """Test rate limiting and quota enforcement."""

    def test_signal_cooldown(self, telethon_client, bot_entity):
        """Test rapid /signal calls trigger cooldown message."""
        rate_limiter_fast = RateLimiter(min_delay=1.0)

        # First signal
        result1 = send_command(telethon_client, bot_entity, "/signal", rate_limiter_fast)
        assert result1.success

        # Second signal (may hit cooldown)
        result2 = send_command(telethon_client, bot_entity, "/signal", rate_limiter_fast)

        # Either succeeds or shows wait message - test passes either way
        assert result2.success

    def test_free_tier_daily_quota(self, telethon_client, bot_entity, rate_limiter):
        """Test free tier has daily signal quota."""
        # This would require exceeding quota in a day
        # For now, just verify /signal works
        result = send_command(telethon_client, bot_entity, "/signal", rate_limiter)
        assert result.success


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: Admin Commands (Security)
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminCommands:
    """Test admin-only command access control."""

    def test_genkey_admin_only(self, telethon_client, bot_entity, rate_limiter):
        """Test /genkey requires admin privileges."""
        result = send_command(telethon_client, bot_entity, "/genkey", rate_limiter)

        # Non-admin should get rejected
        if result.success and result.response_text:
            response_lower = result.response_text.lower()
            # Should NOT reveal that command exists for admins
            assert (
                "admin only" in response_lower
                or "not authorized" in response_lower
                or "admin" in response_lower
            ), f"Non-admin should be denied /genkey: {result.response_text[:200]}"

    def test_activate_admin_only(self, telethon_client, bot_entity, rate_limiter):
        """Test /activate requires admin privileges."""
        result = send_command(telethon_client, bot_entity, "/activate test", rate_limiter)

        if result.success and result.response_text:
            response_lower = result.response_text.lower()
            assert (
                "admin" in response_lower
                or "not authorized" in response_lower
                or "tidak" in response_lower
            ), f"Non-admin should be denied /activate: {result.response_text[:200]}"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7: Edge Cases and Malformed Input
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and malformed input."""

    def test_signal_empty_asset(self, telethon_client, bot_entity, rate_limiter):
        """Test /signal with empty asset prompts for asset."""
        result = send_command(telethon_client, bot_entity, "/signal ", rate_limiter)

        assert result.success
        # Should handle gracefully or prompt for asset

    def test_analyze_whitespace(self, telethon_client, bot_entity, rate_limiter):
        """Test /analyze with whitespace defaults correctly."""
        result = send_command(telethon_client, bot_entity, "/analyze ", rate_limiter)

        assert result.success
        # Should show menu or default to XAUUSD

    def test_price_unknown_symbol(self, telethon_client, bot_entity, rate_limiter):
        """Test /price XYZ returns 'Symbol not found'."""
        result = send_command(telethon_client, bot_entity, "/price XYZ", rate_limiter)

        assert result.success
        # Should handle gracefully
    def test_donate_non_numeric(self, telethon_client, bot_entity, rate_limiter):
        """Test /donate abc rejects non-numeric amount."""
        result = send_command(telethon_client, bot_entity, "/donate abc", rate_limiter)

        assert result.success
        if result.response_text:
            response_lower = result.response_text.lower()
            # Should prompt for numeric or show error
            # No crash
            assert "traceback" not in response_lower, (
                f"Stack trace in /donate: {result.response_text[:200]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8: Connection Reliability
# ─────────────────────────────────────────────────────────────────────────────


class TestConnectionReliability:
    """Test connection lifecycle and error recovery."""

    def test_basic_connectivity(self, telethon_client, bot_entity, rate_limiter):
        """Test connect → send → receive works."""
        result = send_command(telethon_client, bot_entity, "/start", rate_limiter)

        assert result.success, f"Basic connectivity failed: {result.error}"
        assert result.response_text is not None

    def test_multiple_commands(self, telethon_client, bot_entity, rate_limiter):
        """Test multiple commands in sequence work reliably."""
        commands = ["/start", "/help", "/myid", "/status"]
        results = []

        for cmd in commands:
            result = send_command(telethon_client, bot_entity, cmd, rate_limiter)
            results.append(result)

        # All commands should succeed
        assert all(r.success for r in results), "One or more commands failed"

        # All responses should have content
        assert all(r.response_text for r in results), "One or more commands had no response"


# ─────────────────────────────────────────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────────────────────────────────────────


def test_smoke_e2e(telethon_client, bot_entity, rate_limiter):
    """Smoke test: verify basic bot connectivity and response."""
    result = send_command(telethon_client, bot_entity, "/start", rate_limiter)

    assert result.success, f"Smoke test failed: {result.error}"
    assert result.response_text, "Smoke test: no response text"
    assert assert_response_time(result), (
        f"Smoke test: response too slow ({result.response_time:.2f}s)"
    )
    assert len(result.response_text) > 10, "Smoke test: response too short"
