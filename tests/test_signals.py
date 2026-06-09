"""Tests for signal sources from tradebot/signals/."""

from __future__ import annotations  # noqa: I001

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradebot.models.market import OHLCV
from tradebot.signals.base import BaseDataSource
from tradebot.signals.binance import BinanceSource
from tradebot.signals.yahoo import YahooSource
from tradebot.signals.forex import ForexSource
from tradebot.signals.stockity import StockitySource
from tradebot.signals.market import (
    MarketAggregator,
    FallbackChain,
    _is_forex,
)


# ═════════════════════════════════════════════════════════════════════
#  Fixtures & helpers
# ═════════════════════════════════════════════════════════════════════


def _make_ohlcv(
    symbol: str = "BTC-USD",
    timestamp: int = 1_700_000_000,
    close: float = 42000.0,
    open_: float = 41900.0,
    high: float = 42100.0,
    low: float = 41800.0,
    volume: int = 100,
) -> OHLCV:
    return OHLCV(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        symbol=symbol,
        volume=volume,
    )


def _make_klines(n: int = 3) -> list[list]:
    """Return Binance-style kline arrays."""
    base_ts = 1_700_000_000_000
    return [
        [
            str(base_ts + i * 60_000),
            "41900.00", "42100.00", "41800.00",
            f"{42000.00 + i}", "100.00",
            str(base_ts + (i + 1) * 60_000),
            "0", "50", "50.00", "25.00", "0",
        ]
        for i in range(n)
    ]


# ═════════════════════════════════════════════════════════════════════
#  BaseDataSource
# ═════════════════════════════════════════════════════════════════════


class _ConcreteSource(BaseDataSource):
    """Minimal concrete subclass for testing the base class."""

    def __init__(self, candles: list[OHLCV] | None = None) -> None:
        self._candles = candles or []

    async def fetch(
        self, symbol: str, interval: str = "1m", count: int = 100,
    ) -> list[OHLCV]:
        return self._candles[-count:] if count else self._candles


class TestBaseDataSource:
    """BaseDataSource.price() delegates to fetch()."""

    @pytest.mark.asyncio
    async def test_price_returns_last_close(self):
        candles = [
            _make_ohlcv(close=100.0),
            _make_ohlcv(close=200.0),
            _make_ohlcv(close=300.0),
        ]
        src = _ConcreteSource(candles)
        price = await src.price("TEST")
        assert price == 300.0

    @pytest.mark.asyncio
    async def test_price_returns_none_when_empty(self):
        src = _ConcreteSource([])
        price = await src.price("TEST")
        assert price is None

    @pytest.mark.asyncio
    async def test_price_single_candle(self):
        src = _ConcreteSource([_make_ohlcv(close=999.0)])
        price = await src.price("TEST")
        assert price == 999.0

    @pytest.mark.asyncio
    async def test_fetch_respects_count(self):
        candles = [
            _make_ohlcv(timestamp=1_700_000_000 + i) for i in range(10)
        ]
        src = _ConcreteSource(candles)
        result = await src.fetch("TEST", count=3)
        assert len(result) == 3


# ═════════════════════════════════════════════════════════════════════
#  BinanceSource
# ═════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_binance_client():
    client = MagicMock()
    client.is_closed = False
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=_make_klines(3))
    client.get = AsyncMock(return_value=mock_response)
    client.aclose = AsyncMock()
    return client


class TestBinanceSource:

    @pytest.mark.asyncio
    async def test_fetch_returns_ohlcvs(self, mock_binance_client):
        src = BinanceSource()
        src._client = mock_binance_client
        candles = await src.fetch("BTC-USD")
        assert len(candles) == 3
        assert candles[0].symbol == "BTC-USD"
        assert candles[0].close == 42000.0
        assert candles[1].close == 42001.0

    @pytest.mark.asyncio
    async def test_fetch_unknown_symbol_returns_empty(
        self, mock_binance_client
    ):
        src = BinanceSource()
        src._client = mock_binance_client
        candles = await src.fetch("UNKNOWN-XYZ")
        assert candles == []
        mock_binance_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_passes_correct_params(self, mock_binance_client):
        src = BinanceSource()
        src._client = mock_binance_client
        await src.fetch("ETH-USD", interval="5m", count=50)
        _, kwargs = mock_binance_client.get.call_args
        assert kwargs["params"]["symbol"] == "ETHUSDT"
        assert kwargs["params"]["interval"] == "5m"
        assert kwargs["params"]["limit"] == 50

    @pytest.mark.asyncio
    async def test_fetch_http_error_returns_empty(self, mock_binance_client):
        import httpx

        mock_binance_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
        )
        src = BinanceSource()
        src._client = mock_binance_client
        candles = await src.fetch("BTC-USD")
        assert candles == []

    @pytest.mark.asyncio
    async def test_fetch_empty_response_returns_empty(
        self, mock_binance_client
    ):
        mock_binance_client.get.return_value.json = MagicMock(
            return_value=[]
        )
        src = BinanceSource()
        src._client = mock_binance_client
        candles = await src.fetch("BTC-USD")
        assert candles == []

    @pytest.mark.asyncio
    async def test_close_closes_client(self):
        src = BinanceSource()
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        src._client = mock_client
        await src.close()
        mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_price_convenience(self, mock_binance_client):
        klines = _make_klines(1)
        klines[0][4] = "99999.0"
        mock_binance_client.get.return_value.json = MagicMock(
            return_value=klines
        )
        src = BinanceSource()
        src._client = mock_binance_client
        price = await src.price("BTC-USD")
        assert price == 99999.0


# ═════════════════════════════════════════════════════════════════════
#  YahooSource
# ═════════════════════════════════════════════════════════════════════


def _make_yahoo_df(n: int = 3):
    pd = pytest.importorskip("pandas")
    dates = [
        datetime(2024, 1, 1, 0, i, tzinfo=timezone.utc)  # noqa: UP017
        for i in range(n)
    ]
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [105.0 + i for i in range(n)],
            "Low": [95.0 + i for i in range(n)],
            "Close": [102.0 + i for i in range(n)],
            "Volume": [1000 + i for i in range(n)],
        },
        index=pd.DatetimeIndex(dates, name="Datetime"),
    )


class TestYahooSource:

    @pytest.mark.asyncio
    async def test_fetch_returns_ohlcvs(self):
        df = _make_yahoo_df(5)
        src = YahooSource()
        with patch("tradebot.signals.yahoo.yf") as mock_yf:
            mock_yf.download = MagicMock(return_value=df)
            candles = await src.fetch("AAPL")
        assert len(candles) == 5
        assert candles[0].symbol == "AAPL"
        assert candles[0].open == 100.0
        assert candles[0].close == 102.0

    @pytest.mark.asyncio
    async def test_fetch_empty_dataframe_returns_empty(self):
        pd = pytest.importorskip("pandas")
        empty_df = pd.DataFrame()
        src = YahooSource()
        with patch("tradebot.signals.yahoo.yf") as mock_yf:
            mock_yf.download = MagicMock(return_value=empty_df)
            candles = await src.fetch("AAPL")
        assert candles == []

    @pytest.mark.asyncio
    async def test_fetch_none_dataframe_returns_empty(self):
        src = YahooSource()
        with patch("tradebot.signals.yahoo.yf") as mock_yf:
            mock_yf.download = MagicMock(return_value=None)
            candles = await src.fetch("AAPL")
        assert candles == []

    @pytest.mark.asyncio
    async def test_fetch_exception_returns_empty(self):
        src = YahooSource()
        with patch("tradebot.signals.yahoo.yf") as mock_yf:
            mock_yf.download = MagicMock(
                side_effect=RuntimeError("network down")
            )
            candles = await src.fetch("AAPL")
        assert candles == []

    @pytest.mark.asyncio
    async def test_fetch_respects_count_limit(self):
        df = _make_yahoo_df(10)
        src = YahooSource()
        with patch("tradebot.signals.yahoo.yf") as mock_yf:
            mock_yf.download = MagicMock(return_value=df)
            candles = await src.fetch("AAPL", count=3)
        assert len(candles) == 3

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        src = YahooSource()
        await src.close()


# ═════════════════════════════════════════════════════════════════════
#  ForexSource
# ═════════════════════════════════════════════════════════════════════


class TestForexSource:

    @pytest.mark.asyncio
    async def test_returns_yahoo_when_enough_candles(self):
        yahoo_candles = [
            _make_ohlcv(symbol="EURUSD=X", close=1.1 + i * 0.001)
            for i in range(40)
        ]
        src = ForexSource()
        with patch.object(  # noqa: SIM117
            src._yahoo, "fetch",
            new_callable=AsyncMock, return_value=yahoo_candles,
        ):
            with patch.object(
                src, "_fetch_frankfurter", new_callable=AsyncMock,
            ) as mock_frank:
                candles = await src.fetch("EURUSD=X")
        assert len(candles) == 40
        mock_frank.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_frankfurter_when_yahoo_short(self):
        few = [_make_ohlcv(symbol="EURUSD=X", close=1.1)] * 5
        frank = [_make_ohlcv(symbol="EURUSD=X", close=1.0850)]
        src = ForexSource()
        with patch.object(  # noqa: SIM117
            src._yahoo, "fetch",
            new_callable=AsyncMock, return_value=few,
        ):
            with patch.object(
                src, "_fetch_frankfurter",
                new_callable=AsyncMock, return_value=frank,
            ):
                candles = await src.fetch("EURUSD=X")
        assert len(candles) == 1
        assert candles[0].close == 1.0850

    @pytest.mark.asyncio
    async def test_returns_empty_when_both_fail(self):
        src = ForexSource()
        with patch.object(  # noqa: SIM117
            src._yahoo, "fetch",
            new_callable=AsyncMock, return_value=[],
        ):
            with patch.object(
                src, "_fetch_frankfurter",
                new_callable=AsyncMock, return_value=[],
            ):
                candles = await src.fetch("EURUSD=X")
        assert candles == []

    @pytest.mark.asyncio
    async def test_frankfurter_fetch_with_mocked_http(self):
        src = ForexSource()
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"rates": {"USD": 1.0850}}
        )
        mock_client.get = AsyncMock(return_value=mock_response)
        src._http = mock_client

        candles = await src._fetch_frankfurter("EURUSD=X")
        assert len(candles) == 1
        assert candles[0].close == 1.0850
        assert candles[0].symbol == "EURUSD=X"

    @pytest.mark.asyncio
    async def test_frankfurter_unknown_symbol_returns_empty(self):
        src = ForexSource()
        candles = await src._fetch_frankfurter("UNKNOWN=X")
        assert candles == []


# ═════════════════════════════════════════════════════════════════════
#  StockitySource
# ═════════════════════════════════════════════════════════════════════


class TestStockitySource:

    @pytest.mark.asyncio
    async def test_fetch_returns_aggregated_candles(self):
        raw_candles = []
        for i in range(60):
            raw_candles.append({
                "created_at": f"2024-01-01T00:00:{i:02d}Z",
                "open": str(100.0 + i),
                "high": str(105.0 + i),
                "low": str(95.0 + i),
                "close": str(102.0 + i),
            })

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"data": raw_candles}
        )

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        src = StockitySource()
        src._authtoken = "test-token"
        src._client = mock_client

        candles = await src.fetch("CRYPTO_IDX")
        assert len(candles) >= 1
        assert candles[0].symbol == "CRYPTO_IDX"
        assert candles[0].open >= 100.0

    @pytest.mark.asyncio
    async def test_fetch_no_auth_returns_empty(self):
        src = StockitySource()
        src._authtoken = ""
        src._cookie = ""
        candles = await src.fetch("CRYPTO_IDX")
        assert candles == []

    @pytest.mark.asyncio
    async def test_fetch_unknown_ric_returns_empty(self):
        src = StockitySource()
        src._authtoken = "test-token"
        candles = await src.fetch("UNKNOWN_ASSET")
        assert candles == []

    @pytest.mark.asyncio
    async def test_fetch_http_error_returns_empty(self):
        import httpx

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Error",
                request=MagicMock(),
                response=MagicMock(status_code=403),
            )
        )
        mock_client.aclose = AsyncMock()
        src = StockitySource()
        src._authtoken = "test-token"
        src._cookie = "cookie-value"
        src._http = mock_client

        candles = await src.fetch("CRYPTO_IDX")
        assert candles == []

    def test_aggregate_candles(self):
        raw = [
            _make_ohlcv(
                timestamp=60, close=100.0, open_=99.0,
                high=101.0, low=98.0,
            ),
            _make_ohlcv(
                timestamp=61, close=102.0, open_=100.0,
                high=103.0, low=99.0,
            ),
            _make_ohlcv(
                timestamp=120, close=200.0, open_=199.0,
                high=201.0, low=198.0,
            ),
        ]
        result = StockitySource._aggregate_candles(raw, period_s=60)
        assert len(result) == 2
        assert result[0].open == 99.0
        assert result[0].close == 102.0
        assert result[0].high == 103.0
        assert result[0].low == 98.0

    def test_aggregate_candles_empty(self):
        assert StockitySource._aggregate_candles([]) == []


# ═════════════════════════════════════════════════════════════════════
#  MarketAggregator
# ═════════════════════════════════════════════════════════════════════


class TestMarketAggregator:

    def test_select_sources_crypto_routes_to_binance(self):
        agg = MarketAggregator()
        sources = agg._select_sources("BTC-USD")
        assert len(sources) >= 1
        assert isinstance(sources[0], BinanceSource)

    def test_select_sources_forex_routes_to_forex(self):
        agg = MarketAggregator()
        sources = agg._select_sources("EURUSD=X")
        assert len(sources) >= 1
        assert isinstance(sources[0], ForexSource)

    def test_select_sources_stock_routes_to_yahoo(self):
        agg = MarketAggregator()
        sources = agg._select_sources("AAPL")
        assert len(sources) == 1
        assert isinstance(sources[0], YahooSource)

    def test_select_sources_platform_asset_routes_to_stockity(self):
        agg = MarketAggregator()
        sources = agg._select_sources("CRYPTO_IDX")
        assert len(sources) == 1
        assert isinstance(sources[0], StockitySource)

    @pytest.mark.asyncio
    async def test_fetch_returns_first_non_empty_result(self):
        agg = MarketAggregator()
        candle = [_make_ohlcv(close=50000.0)]
        with patch.object(
            agg._binance, "fetch",
            new_callable=AsyncMock, return_value=candle,
        ):
            result = await agg.fetch("BTC-USD")
        assert len(result) == 1
        assert result[0].close == 50000.0

    @pytest.mark.asyncio
    async def test_fetch_fallback_when_primary_fails(self):
        agg = MarketAggregator()
        fb = [_make_ohlcv(close=49000.0)]
        with patch.object(  # noqa: SIM117
            agg._binance, "fetch",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Binance down"),
        ):
            with patch.object(
                agg._yahoo, "fetch",
                new_callable=AsyncMock, return_value=fb,
            ):
                result = await agg.fetch("BTC-USD")
        assert len(result) == 1
        assert result[0].close == 49000.0

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_when_all_sources_fail(self):
        agg = MarketAggregator()
        with patch.object(  # noqa: SIM117
            agg._binance, "fetch",
            new_callable=AsyncMock, return_value=[],
        ):
            with patch.object(
                agg._yahoo, "fetch",
                new_callable=AsyncMock, return_value=[],
            ):
                result = await agg.fetch("BTC-USD")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_skips_empty_and_uses_next_source(self):
        agg = MarketAggregator()
        candle = [_make_ohlcv(close=49000.0)]
        with patch.object(  # noqa: SIM117
            agg._binance, "fetch",
            new_callable=AsyncMock, return_value=[],
        ):
            with patch.object(
                agg._yahoo, "fetch",
                new_callable=AsyncMock, return_value=candle,
            ):
                result = await agg.fetch("BTC-USD")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_price_delegates_to_fetch(self):
        agg = MarketAggregator()
        candle = [_make_ohlcv(close=12345.0)]
        with patch.object(
            agg, "fetch",
            new_callable=AsyncMock, return_value=candle,
        ):
            price = await agg.price("BTC-USD")
        assert price == 12345.0

    @pytest.mark.asyncio
    async def test_price_returns_none_when_empty(self):
        agg = MarketAggregator()
        with patch.object(
            agg, "fetch",
            new_callable=AsyncMock, return_value=[],
        ):
            price = await agg.price("BTC-USD")
        assert price is None


# ═════════════════════════════════════════════════════════════════════
#  FallbackChain
# ═════════════════════════════════════════════════════════════════════


class TestFallbackChain:

    @pytest.mark.asyncio
    async def test_fetch_price_uses_currency_api_for_gold(self):
        chain = FallbackChain()
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"xau": {"usd": 2350.50}}
        )
        mock_client.get = AsyncMock(return_value=mock_response)
        chain._http = mock_client

        price = await chain.fetch_price("XAUUSD")
        assert price == 2350.50

    @pytest.mark.asyncio
    async def test_fetch_price_uses_coingecko_for_btc(self):
        chain = FallbackChain()
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"bitcoin": {"usd": 67000.0}}
        )
        mock_client.get = AsyncMock(return_value=mock_response)
        chain._http = mock_client

        price = await chain.fetch_price("BTCUSD")
        assert price == 67000.0

    @pytest.mark.asyncio
    async def test_fetch_price_falls_back_to_yahoo(self):
        chain = FallbackChain()
        with patch.object(  # noqa: SIM117
            chain, "_fetch_currency_api",
            new_callable=AsyncMock, return_value=None,
        ):
            with patch.object(
                chain._aggregator, "price",
                new_callable=AsyncMock, return_value=1500.0,
            ):
                price = await chain.fetch_price("XAUUSD")
        assert price == 1500.0

    @pytest.mark.asyncio
    async def test_fetch_price_returns_none_when_all_fail(self):
        chain = FallbackChain()
        with patch.object(  # noqa: SIM117
            chain, "_fetch_currency_api",
            new_callable=AsyncMock, return_value=None,
        ):
            with patch.object(
                chain._aggregator, "price",
                new_callable=AsyncMock, return_value=None,
            ):
                with patch(
                    "tradebot.signals.market.settings"
                ) as mock_settings:
                    mock_settings.FCS_API_KEY = ""
                    price = await chain.fetch_price("XAUUSD")
        assert price is None

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_delegates_to_aggregator(self):
        chain = FallbackChain()
        candle = [_make_ohlcv(close=67000.0)]
        mock_agg = AsyncMock(return_value=candle)
        with patch.object(chain._aggregator, "fetch", mock_agg):
            result = await chain.fetch_ohlcv("BTCUSD")
        assert len(result) == 1
        mock_agg.assert_awaited_once()


# ═════════════════════════════════════════════════════════════════════
#  Helper functions
# ═════════════════════════════════════════════════════════════════════


class TestHelpers:

    def test_is_forex_true_for_eurusd(self):
        assert _is_forex("EURUSD=X") is True

    def test_is_forex_true_for_gbpusd(self):
        assert _is_forex("GBPUSD=X") is True

    def test_is_forex_false_for_stock(self):
        assert _is_forex("AAPL") is False

    def test_is_forex_false_for_crypto(self):
        assert _is_forex("BTC-USD") is False
