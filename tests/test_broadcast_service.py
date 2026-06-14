from unittest.mock import AsyncMock, patch

import pytest

from tradebot.services.broadcast_service import BroadcastService


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot._tg_send = AsyncMock()
    return bot


@pytest.fixture
def service(mock_bot):
    return BroadcastService(bot=mock_bot)


@pytest.mark.asyncio
async def test_broadcast_levels(service):
    with patch.object(service, "_get_real_users", return_value=[("12345", "active")]):
        await service.broadcast_levels(dry_run=False)
        service.bot._tg_send.assert_called_once()
        args, kwargs = service.bot._tg_send.call_args
        assert "/levels" in args[0]
        assert args[1] == "12345"


@pytest.mark.asyncio
async def test_broadcast_tech_analysis(service):
    with patch.object(service, "_get_real_users", return_value=[("12345", "active")]):
        await service.broadcast_tech_analysis(dry_run=False)
        service.bot._tg_send.assert_called_once()
        args, kwargs = service.bot._tg_send.call_args
        assert "/zones" in args[0]
        assert args[1] == "12345"


@pytest.mark.asyncio
async def test_broadcast_weekly_winrate(service):
    with (
        patch("tradebot.services.broadcast_service.get_stats") as mock_stats,
        patch("tradebot.services.broadcast_service.get_recent_trades") as mock_recent,
    ):
        mock_stats.return_value = {"win_rate": 70.0, "total_profit_usd": 1000}
        mock_recent.return_value = []

        await service.broadcast_weekly_winrate(dry_run=False)
        service.bot._tg_send.assert_called_once()
        args, kwargs = service.bot._tg_send.call_args
        assert "WEEKLY PERFORMANCE REPORT" in args[0]
        assert "70.0%" in args[0]
