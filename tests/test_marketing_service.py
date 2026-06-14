from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradebot.services.marketing_service import MarketingService


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot._tg_send = AsyncMock()
    return bot


@pytest.fixture
def service(mock_bot):
    return MarketingService(bot=mock_bot)


def test_get_tier_counts(service):
    with patch("tradebot.services.marketing_service._conn") as mock_conn:
        mock_db = MagicMock()
        mock_conn.return_value.__enter__.return_value = mock_db
        mock_db.execute.return_value.fetchall.return_value = [
            {"tier": "starter", "n": 10},
            {"tier": "pro", "n": 5},
        ]
        counts = service.get_tier_counts()
        assert counts == {"starter": 10, "pro": 5}


def test_fmt_weekly_pnl(service):
    stats = {
        "wins": 10,
        "losses": 5,
        "total": 15,
        "win_rate": 66.6,
        "total_pips": 100,
        "total_profit_usd": 500,
    }
    recent = [
        {"outcome": "TP_HIT", "action": "BUY", "symbol": "XAUUSD", "pips": 50, "profit_usd": 100}
    ]

    out = service.fmt_weekly_pnl(stats, recent)
    assert "Win Rate: 66.6%" in out
    assert "BULLISH" in out
    assert "XAUUSD" in out


@pytest.mark.asyncio
async def test_execute_blast_weekly(service):
    with (
        patch("tradebot.services.marketing_service.get_stats") as mock_stats,
        patch("tradebot.services.marketing_service.get_recent_trades") as mock_recent,
        patch.object(service, "get_free_users", return_value=[]),
        patch.object(service, "get_premium_count", return_value=0),
        patch.object(service, "get_tier_counts", return_value={}),
    ):
        mock_stats.return_value = {"wins": 10, "win_rate": 66.6, "total_profit_usd": 500}
        mock_recent.return_value = []

        await service.execute_blast("weekly", dry_run=False)
        service.bot._tg_send.assert_called_once()
        args, kwargs = service.bot._tg_send.call_args
        assert "WEEKLY S-TIER PERFORMANCE" in args[0]


@pytest.mark.asyncio
async def test_execute_blast_flash(service):
    with (
        patch.object(service, "get_free_users", return_value=[{"chat_id": "12345"}]),
        patch.object(service, "get_premium_count", return_value=0),
        patch.object(service, "get_tier_counts", return_value={}),
    ):
        await service.execute_blast("flash", dry_run=False)
        assert service.bot._tg_send.call_count == 2  # 1 for channel, 1 for user
