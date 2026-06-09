"""Tests for BinanceSource with mocked httpx."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradebot.signals.binance import BinanceSource


@pytest.fixture
def mock_httpx_client() -> MagicMock:
    """Return a mock httpx.AsyncClient."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value=[
            [1700000000000, "42000.0", "42500.0", "41800.0", "42200.0", "100.0"],
            [1700000060000, "42200.0", "42600.0", "42100.0", "42400.0", "150.0"],
        ]
    )
    client.get = AsyncMock(return_value=mock_response)
    return client


@pytest.fixture
def source() -> BinanceSource:
    return BinanceSource()


class TestBinanceSource:
    """Binance public market data source."""

    def test_symbol_map(self):
        """SYMBOL_MAP should map internal to Binance symbols."""
        from tradebot.signals.binance import SYMBOL_MAP

        assert SYMBOL_MAP["BTC-USD"] == "BTCUSDT"
        assert SYMBOL_MAP["ETH-USD"] == "ETHUSDT"
        assert SYMBOL_MAP["SOL-USD"] == "SOLUSDT"

    def test_interval_map(self):
        """INTERVAL_MAP should map interval names."""
        from tradebot.signals.binance import INTERVAL_MAP

        assert INTERVAL_MAP["1m"] == "1m"
        assert INTERVAL_MAP["1d"] == "1d"

    @pytest.mark.asyncio
    async def test_fetch_returns_ohlcvs(self, source, mock_httpx_client):
        """Fetch should return OHLCV candles."""
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            candles = await source.fetch("BTC-USD", interval="1m", count=2)
            assert len(candles) == 2
            assert candles[0].symbol == "BTC-USD"
            assert candles[0].open == 42000.0
            assert candles[0].high == 42500.0
            assert candles[0].low == 41800.0
            assert candles[0].close == 42200.0

    @pytest.mark.asyncio
    async def test_fetch_correct_url(self, source, mock_httpx_client):
        """Should call Binance klines endpoint with correct params."""
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            await source.fetch("BTC-USD", interval="1m", count=100)
            call_args = mock_httpx_client.get.call_args
            assert call_args is not None
            url = call_args[0][0]
            assert "/api/v3/klines" in url
            # Symbol is in query params, not path
            kwargs = call_args[1]
            assert kwargs["params"]["symbol"] == "BTCUSDT"
            assert kwargs["params"]["interval"] == "1m"

    @pytest.mark.asyncio
    async def test_fetch_unknown_symbol(self, source, mock_httpx_client):
        """Unknown symbol should return empty list."""
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            candles = await source.fetch("UNKNOWN-USD")
            assert candles == []

    @pytest.mark.asyncio
    async def test_price_convenience(self, source, mock_httpx_client):
        """price() should return the latest close."""
        with patch("httpx.AsyncClient", return_value=mock_httpx_client):
            price = await source.price("BTC-USD")
            assert price == 42400.0

    @pytest.mark.asyncio
    async def test_price_no_data(self, source):
        """price() returns None when no data."""
        empty_client = MagicMock()
        empty_client.__aenter__ = AsyncMock(return_value=empty_client)
        empty_client.__aexit__ = AsyncMock(return_value=None)
        empty_response = MagicMock()
        empty_response.raise_for_status = MagicMock()
        empty_response.json = MagicMock(return_value=[])
        empty_client.get = AsyncMock(return_value=empty_response)

        with patch("httpx.AsyncClient", return_value=empty_client):
            price = await source.price("BTC-USD")
            assert price is None
