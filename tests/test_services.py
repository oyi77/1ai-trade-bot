"""Tests for tradebot/services/ — health, watchdog, telegram, bridge_server."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradebot.services.health import (
    HealthCheckResult,
    HealthReport,
    HealthService,
    HealthStatus,
)
from tradebot.services.telegram import TelegramService
from tradebot.services.watchdog import RateLimiter, WatchdogService

# ── health.py ──────────────────────────────────────────────────────────────


class TestHealthStatusEnum:
    """Verify HealthStatus enum values and membership."""

    def test_values(self):
        assert HealthStatus.OK.value == "ok"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.DOWN.value == "down"

    def test_is_str_enum(self):
        assert isinstance(HealthStatus.OK, str)
        assert HealthStatus.OK == "ok"

    def test_member_count(self):
        assert len(HealthStatus) == 3


class TestHealthCheckResult:
    """HealthCheckResult dataclass defaults and construction."""

    def test_defaults(self):
        r = HealthCheckResult(name="test", status=HealthStatus.OK)
        assert r.name == "test"
        assert r.status == HealthStatus.OK
        assert r.detail == ""
        assert r.latency_ms == 0.0

    def test_full_construction(self):
        r = HealthCheckResult(
            name="broker", status=HealthStatus.DEGRADED,
            detail="slow", latency_ms=123.4,
        )
        assert r.detail == "slow"
        assert r.latency_ms == 123.4


class TestHealthReport:
    """HealthReport aggregation and properties."""

    def test_ok_property_true(self):
        report = HealthReport()
        assert report.ok is True
        assert report.degraded is False
        assert report.down is False

    def test_add_degrades_overall(self):
        report = HealthReport()
        report.add(HealthCheckResult(name="a", status=HealthStatus.OK))
        report.add(HealthCheckResult(name="b", status=HealthStatus.DEGRADED))
        assert report.ok is False
        assert report.degraded is True
        assert report.status == HealthStatus.DEGRADED

    def test_add_down_overrides_degraded(self):
        report = HealthReport()
        report.add(HealthCheckResult(name="a", status=HealthStatus.DEGRADED))
        report.add(HealthCheckResult(name="b", status=HealthStatus.DOWN))
        assert report.down is True
        assert report.status == HealthStatus.DOWN

    def test_down_does_not_downgrade_to_degraded(self):
        report = HealthReport()
        report.add(HealthCheckResult(name="a", status=HealthStatus.DOWN))
        report.add(HealthCheckResult(name="b", status=HealthStatus.DEGRADED))
        assert report.status == HealthStatus.DOWN

    def test_to_dict_structure(self):
        report = HealthReport()
        report.add(HealthCheckResult(
            name="x", status=HealthStatus.OK, detail="fine",
        ))
        d = report.to_dict()
        assert d["status"] == "ok"
        assert "timestamp" in d
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "x"
        assert d["checks"][0]["status"] == "ok"

    def test_timestamp_is_iso(self):
        report = HealthReport()
        datetime.fromisoformat(report.timestamp)


class TestHealthService:
    """HealthService initialization and individual checks."""

    def test_init_defaults(self):
        svc = HealthService()
        assert svc._broker is None
        assert svc._signal_pipeline is None
        assert svc._market_data is None
        assert svc._storage is None

    def test_init_with_mocks(self):
        broker = MagicMock()
        pipeline = MagicMock()
        svc = HealthService(broker=broker, signal_pipeline=pipeline)
        assert svc._broker is broker
        assert svc._signal_pipeline is pipeline

    @pytest.mark.asyncio
    async def test_check_connectivity_no_broker(self):
        svc = HealthService()
        result = await svc.check_connectivity()
        assert result.name == "broker_connectivity"
        assert result.status == HealthStatus.DEGRADED
        assert "No broker" in result.detail

    @pytest.mark.asyncio
    async def test_check_connectivity_broker_connected(self):
        broker = MagicMock()
        broker.is_connected = True
        svc = HealthService(broker=broker)
        result = await svc.check_connectivity()
        assert result.status == HealthStatus.OK
        assert "connected" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_check_connectivity_broker_disconnected(self):
        broker = MagicMock()
        broker.is_connected = False
        svc = HealthService(broker=broker)
        result = await svc.check_connectivity()
        assert result.status == HealthStatus.DOWN

    @pytest.mark.asyncio
    async def test_check_connectivity_broker_exception(self):
        broker = MagicMock()
        type(broker).is_connected = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        svc = HealthService(broker=broker)
        result = await svc.check_connectivity()
        assert result.status == HealthStatus.DOWN
        assert "boom" in result.detail

    @pytest.mark.asyncio
    async def test_check_connectivity_async_is_connected(self):
        broker = MagicMock()
        broker.is_connected = AsyncMock(return_value=True)
        svc = HealthService(broker=broker)
        result = await svc.check_connectivity()
        assert result.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_check_bot_health_no_pipeline(self):
        svc = HealthService()
        result = await svc.check_bot_health()
        assert result.name == "bot_health"
        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_bot_health_with_pipeline(self):
        pipeline = MagicMock()
        pipeline.status.return_value = "running"
        svc = HealthService(signal_pipeline=pipeline)
        result = await svc.check_bot_health()
        assert result.status == HealthStatus.OK
        assert "running" in result.detail

    @pytest.mark.asyncio
    async def test_check_bot_health_pipeline_exception(self):
        pipeline = MagicMock()
        pipeline.status.side_effect = RuntimeError("crash")
        svc = HealthService(signal_pipeline=pipeline)
        result = await svc.check_bot_health()
        assert result.status == HealthStatus.DOWN

    @pytest.mark.asyncio
    async def test_check_market_data_no_provider(self):
        svc = HealthService()
        result = await svc.check_market_data_freshness()
        assert result.name == "market_data_freshness"
        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_market_data_fresh_tick(self):
        provider = MagicMock()
        provider.last_tick_time = datetime.now(UTC)
        svc = HealthService(market_data_provider=provider)
        result = await svc.check_market_data_freshness()
        assert result.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_check_market_data_stale_tick(self):
        provider = MagicMock()
        provider.last_tick_time = (
            datetime.now(UTC) - timedelta(hours=1)
        )
        svc = HealthService(market_data_provider=provider)
        result = await svc.check_market_data_freshness()
        assert result.status == HealthStatus.DEGRADED
        assert "stale" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_run_all_aggregates(self):
        broker = MagicMock()
        broker.is_connected = True
        svc = HealthService(broker=broker)
        report = await svc.run_all()
        assert isinstance(report, HealthReport)
        assert len(report.checks) == 4
        assert report.summary != ""

    @pytest.mark.asyncio
    async def test_check_storage_health(self):
        svc = HealthService()
        result = await svc.check_storage_health()
        assert result.name == "storage_health"
        assert result.status in (
            HealthStatus.OK, HealthStatus.DEGRADED, HealthStatus.DOWN,
        )


# ── watchdog.py ────────────────────────────────────────────────────────────


class TestRateLimiter:
    """RateLimiter cooldown and reset logic."""

    def test_can_send_first_time(self):
        rl = RateLimiter(default_cooldown=5.0)
        assert rl.can_send("alert") is True

    def test_cannot_send_within_cooldown(self):
        rl = RateLimiter(default_cooldown=60.0)
        rl.mark_sent("alert")
        assert rl.can_send("alert") is False

    def test_can_send_after_cooldown(self):
        rl = RateLimiter(default_cooldown=0.01)
        rl.mark_sent("alert")
        time.sleep(0.02)
        assert rl.can_send("alert") is True

    def test_independent_keys(self):
        rl = RateLimiter(default_cooldown=60.0)
        rl.mark_sent("key_a")
        assert rl.can_send("key_a") is False
        assert rl.can_send("key_b") is True

    def test_reset_clears_key(self):
        rl = RateLimiter(default_cooldown=60.0)
        rl.mark_sent("alert")
        assert rl.can_send("alert") is False
        rl.reset("alert")
        assert rl.can_send("alert") is True

    def test_reset_nonexistent_key_no_error(self):
        rl = RateLimiter()
        rl.reset("nonexistent")


class TestWatchdogService:
    """WatchdogService initialization, restart tracking, and lifecycle."""

    def test_init_defaults(self):
        wd = WatchdogService()
        assert wd._health is not None
        assert wd._on_restart is None
        assert wd._send_alert is None
        assert wd._running is False
        assert wd._restart_attempts == {}
        assert wd._max_restarts_per_hour == 5

    def test_init_with_callbacks(self):
        restart_cb = MagicMock()
        alert_cb = MagicMock()
        health = MagicMock()
        wd = WatchdogService(
            health_service=health, on_restart=restart_cb,
            send_alert=alert_cb, interval=10.0,
        )
        assert wd._health is health
        assert wd._on_restart is restart_cb
        assert wd._send_alert is alert_cb
        assert wd._interval == 10.0

    def test_running_property(self):
        wd = WatchdogService()
        assert wd.running is False
        wd._running = True
        assert wd.running is True

    @pytest.mark.asyncio
    async def test_restart_attempt_tracking(self):
        restart_cb = MagicMock()
        wd = WatchdogService(on_restart=restart_cb)
        check = HealthCheckResult(
            name="broker", status=HealthStatus.DOWN, detail="dead",
        )
        for _ in range(5):
            await wd._handle_down(check)
        assert wd._restart_attempts["broker"] == 5
        assert restart_cb.call_count == 5
        # 6th attempt should NOT call restart (at limit)
        await wd._handle_down(check)
        assert restart_cb.call_count == 5

    @pytest.mark.asyncio
    async def test_restart_resets_on_recovery(self):
        restart_cb = MagicMock()
        wd = WatchdogService(on_restart=restart_cb)
        down = HealthCheckResult(
            name="svc", status=HealthStatus.DOWN, detail="x",
        )
        ok = HealthCheckResult(name="svc", status=HealthStatus.OK)

        await wd._handle_down(down)
        assert wd._restart_attempts.get("svc", 0) == 1

        report = HealthReport()
        report.add(ok)
        await wd._handle_results(report)
        assert "svc" not in wd._restart_attempts

    @pytest.mark.asyncio
    async def test_handle_down_alert_rate_limited(self):
        alert_cb = MagicMock()
        wd = WatchdogService(send_alert=alert_cb, interval=0.1)
        check = HealthCheckResult(
            name="x", status=HealthStatus.DOWN, detail="fail",
        )
        await wd._handle_down(check)
        assert alert_cb.call_count == 1
        # Second call within cooldown — no re-alert
        await wd._handle_down(check)
        assert alert_cb.call_count == 1

    @pytest.mark.asyncio
    async def test_handle_degraded_alert(self):
        alert_cb = MagicMock()
        wd = WatchdogService(send_alert=alert_cb)
        check = HealthCheckResult(
            name="x", status=HealthStatus.DEGRADED, detail="slow",
        )
        await wd._handle_degraded(check)
        assert alert_cb.call_count == 1
        msg = alert_cb.call_args[0][0]
        assert "DEGRADED" in msg
        assert "x" in msg

    @pytest.mark.asyncio
    async def test_handle_down_async_restart(self):
        restart_cb = AsyncMock()
        wd = WatchdogService(on_restart=restart_cb)
        check = HealthCheckResult(
            name="svc", status=HealthStatus.DOWN, detail="x",
        )
        await wd._handle_down(check)
        restart_cb.assert_called_once_with("svc")

    @pytest.mark.asyncio
    async def test_handle_down_async_alert(self):
        alert_cb = AsyncMock()
        wd = WatchdogService(send_alert=alert_cb)
        check = HealthCheckResult(
            name="svc", status=HealthStatus.DOWN, detail="x",
        )
        await wd._handle_down(check)
        alert_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_once_returns_dict(self):
        health = MagicMock()
        report = HealthReport()
        report.add(HealthCheckResult(name="a", status=HealthStatus.OK))
        report.summary = "ok"
        health.run_all = AsyncMock(return_value=report)
        wd = WatchdogService(health_service=health)
        result = await wd.check_once()
        assert isinstance(result, dict)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        wd = WatchdogService(interval=0.05)
        await wd.start()
        assert wd.running is True
        assert wd._task is not None
        await wd.stop()
        assert wd.running is False
        assert wd._task is None

    @pytest.mark.asyncio
    async def test_start_twice_noop(self):
        wd = WatchdogService(interval=0.05)
        await wd.start()
        task1 = wd._task
        await wd.start()
        assert wd._task is task1
        await wd.stop()


# ── telegram.py ────────────────────────────────────────────────────────────


class TestTelegramService:
    """TelegramService init, send_message, and send_signal_alert."""

    def test_init_with_explicit_params(self):
        svc = TelegramService(bot_token="tok123", chat_id="999")
        assert svc.bot_token == "tok123"
        assert svc.chat_id == "999"
        assert svc._enabled is True
    def test_init_disabled_when_no_token(self):
        svc = TelegramService(bot_token="", chat_id="12345")
        assert svc._enabled is False

    def test_init_disabled_when_no_chat_id(self):
        svc = TelegramService(bot_token="tok", chat_id="")
        assert svc._enabled is False

    def test_init_enabled_with_both_empty_when_settings_absent(self):
        from unittest.mock import patch
        with patch.object(__import__("tradebot.config.settings", fromlist=["settings"]).settings, "TELEGRAM_BOT_TOKEN", ""):
            with patch.object(__import__("tradebot.config.settings", fromlist=["settings"]).settings, "TELEGRAM_CHAT_ID", ""):
                svc = TelegramService()
                assert svc._enabled is False

    @pytest.mark.asyncio
    async def test_send_message_disabled(self):
        # Mock settings to simulate absent credentials
        from unittest.mock import patch
        with patch.object(__import__("tradebot.config.settings", fromlist=["settings"]).settings, "TELEGRAM_BOT_TOKEN", ""):
            with patch.object(__import__("tradebot.config.settings", fromlist=["settings"]).settings, "TELEGRAM_CHAT_ID", ""):
                svc = TelegramService()
                result = await svc.send_message("hello")
                assert result[0] is False

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        svc = TelegramService(bot_token="test_token", chat_id="12345")

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("tradebot.services.telegram.httpx", create=True) as m:
            m.AsyncClient.return_value = mock_client
            with patch.dict("sys.modules", {"httpx": m}):
                result = await svc.send_message("test message")
                assert result[0] is True
                mock_client.post.assert_called_once()
                call_args = mock_client.post.call_args
                assert "test_token" in call_args[0][0]
                assert call_args[1]["json"]["chat_id"] == "12345"
                assert call_args[1]["json"]["text"] == "test message"

    @pytest.mark.asyncio
    async def test_send_message_failure_status(self):
        svc = TelegramService(bot_token="test_token", chat_id="12345")

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("tradebot.services.telegram.httpx", create=True) as m:
            m.AsyncClient.return_value = mock_client
            with patch.dict("sys.modules", {"httpx": m}):
                result = await svc.send_message("fail")
                assert result[0] is False

    @pytest.mark.asyncio
    async def test_send_message_exception(self):
        svc = TelegramService(bot_token="test_token", chat_id="12345")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("network"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("tradebot.services.telegram.httpx", create=True) as m:
            m.AsyncClient.return_value = mock_client
            with patch.dict("sys.modules", {"httpx": m}):
                result = await svc.send_message("boom")
                assert result[0] is False

    @pytest.mark.asyncio
    async def test_send_signal_alert_formatting(self):
        svc = TelegramService(bot_token="tok", chat_id="123")
        svc.send_message = AsyncMock(return_value=(True, 123))

        await svc.send_signal_alert("R_75", "CALL", 85.5, 33000.12345)
        svc.send_message.assert_called_once()
        text = svc.send_message.call_args[0][0]
        assert "R_75" in text
        assert "CALL" in text
        assert "85.5%" in text
        assert "33000.12345" in text

    @pytest.mark.asyncio
    async def test_send_signal_alert_put(self):
        svc = TelegramService(bot_token="tok", chat_id="123")
        svc.send_message = AsyncMock(return_value=(True, 123))

        await svc.send_signal_alert("EURUSD", "PUT", 60.0, 1.0850)
        text = svc.send_message.call_args[0][0]
        assert "PUT" in text

    @pytest.mark.asyncio
    async def test_send_trade_result_win(self):
        svc = TelegramService(bot_token="tok", chat_id="123")
        svc.send_message = AsyncMock(return_value=(True, 123))

        await svc.send_trade_result(2.52, True, "R_75", "digit match")
        text = svc.send_message.call_args[0][0]
        assert "+2.52" in text
        assert "digit match" in text

    @pytest.mark.asyncio
    async def test_send_trade_result_loss(self):
        svc = TelegramService(bot_token="tok", chat_id="123")
        svc.send_message = AsyncMock(return_value=(True, 123))

        await svc.send_trade_result(-0.35, False, "R_75")
        text = svc.send_message.call_args[0][0]
        assert "-0.35" in text

