"""Tests for core providers — Exness, CCXT (mock-based, no API keys).

All tests use mocks to simulate provider connections and responses.
No real API keys, network calls, or terminal connections required.
"""

# ruff: noqa: UP017

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.mock_providers import MockProvider
from trading_bot.providers.base import (
    Candle,
    MarketType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from trading_bot.providers.crypto.ccxt_adapter import CCXTProvider
from trading_bot.providers.forex.exness import ExnessProvider
from trading_bot.providers.paper.paper_trader import PaperTradingProvider

# ===========================================================================
#  MockProvider — shared test double
# ===========================================================================


class TestMockProvider:
    """Verify MockProvider itself behaves correctly (foundation for other tests)."""

    @pytest.fixture
    def mock(self) -> MockProvider:
        return MockProvider()

    async def test_name(self, mock: MockProvider) -> None:
        assert mock.name == "mock"

    async def test_custom_name(self) -> None:
        assert MockProvider(name="test").name == "test"

    async def test_market_type(self, mock: MockProvider) -> None:
        assert mock.market_type == MarketType.FOREX

    async def test_connect(self, mock: MockProvider) -> None:
        assert await mock.connect() is True
        assert await mock.is_connected() is True

    async def test_connect_failure(self) -> None:
        p = MockProvider(fail_connect=True)
        assert await p.connect() is False
        assert await p.is_connected() is False

    async def test_disconnect(self, mock: MockProvider) -> None:
        await mock.connect()
        await mock.disconnect()
        assert await mock.is_connected() is False

    async def test_balance(self, mock: MockProvider) -> None:
        assert await mock.get_balance() == 10_000.0
        mock.set_balance(500.0)
        assert await mock.get_balance() == 500.0

    async def test_place_order_filled(self, mock: MockProvider) -> None:
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=100,
        )
        result = await mock.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.order_id == "mock-order-001"

    async def test_place_order_invalid_quantity(self, mock: MockProvider) -> None:
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0,
        )
        result = await mock.place_order(order)
        assert result.status == OrderStatus.REJECTED

    async def test_place_order_empty_symbol(self, mock: MockProvider) -> None:
        order = Order(
            symbol="", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1,
        )
        result = await mock.place_order(order)
        assert result.status == OrderStatus.REJECTED

    async def test_cancel_order(self, mock: MockProvider) -> None:
        assert await mock.cancel_order("id-1") is True

    async def test_last_order_tracked(self, mock: MockProvider) -> None:
        order = Order(
            symbol="BTC/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.01,
        )
        await mock.place_order(order)
        assert mock.last_order is order

    async def test_symbols(self, mock: MockProvider) -> None:
        symbols = await mock.get_symbols()
        assert "EUR/USD" in symbols
        assert "BTC/USD" in symbols

    async def test_candles_empty(self, mock: MockProvider) -> None:
        assert await mock.get_candles("XAU/USD", "1h") == []

    async def test_positions_empty(self, mock: MockProvider) -> None:
        assert await mock.get_positions() == []

    async def test_add_position(self, mock: MockProvider) -> None:
        pos = Position(
            symbol="EUR/USD", side=OrderSide.BUY, quantity=1000,
            entry_price=1.10, current_price=1.11,
            unrealized_pnl=10.0, realized_pnl=0.0,
        )
        mock.add_position(pos)
        positions = await mock.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "EUR/USD"

    async def test_clear(self, mock: MockProvider) -> None:
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1,
        )
        await mock.place_order(order)
        mock.set_balance(0)
        mock.clear()
        assert mock.last_order is None
        assert await mock.get_balance() == 10_000.0


# ===========================================================================
#  ExnessProvider
# ===========================================================================


class TestExnessProvider:
    """ExnessProvider connect/disconnect, errors, and MT5 wrapper."""

    @pytest.fixture
    def provider(self) -> ExnessProvider:
        return ExnessProvider()

    async def test_name(self, provider: ExnessProvider) -> None:
        assert provider.name == "exness"

    async def test_market_type(self, provider: ExnessProvider) -> None:
        assert provider.market_type == MarketType.FOREX

    async def test_connect_mt5_not_installed(self, provider: ExnessProvider) -> None:
        """When MetaTrader5 is not installed, connect returns False."""
        result = await provider.connect()
        assert result is False

    async def test_disconnect_not_connected(self, provider: ExnessProvider) -> None:
        """Disconnect without connect is a no-op (no crash)."""
        await provider.disconnect()
        assert await provider.is_connected() is False

    async def test_get_balance_not_connected(self, provider: ExnessProvider) -> None:
        with pytest.raises(ConnectionError, match="not connected"):
            await provider.get_balance()

    async def test_place_order_not_connected(self, provider: ExnessProvider) -> None:
        order = Order(symbol="XAU/USD", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=0.1)
        with pytest.raises(ConnectionError, match="not connected"):
            await provider.place_order(order)

    async def test_get_symbols_not_connected(self, provider: ExnessProvider) -> None:
        with pytest.raises(ConnectionError, match="not connected"):
            await provider.get_symbols()

    async def test_connect_with_mocked_mt5(self) -> None:
        """Simulate successful MT5 connect."""
        p = ExnessProvider(login=123, password="pwd", server="srv")
        with patch.dict("sys.modules", {"MetaTrader5": MagicMock()}):
            import MetaTrader5 as mt5  # noqa: N813

            mt5.initialize.return_value = True
            mt5.login.return_value = True
            result = await p.connect()
            assert result is True
            assert await p.is_connected() is True

    async def test_connect_mt5_login_failure(self) -> None:
        """When MT5 login fails, connect returns False."""
        p = ExnessProvider(login=123, password="bad", server="srv")
        with patch.dict("sys.modules", {"MetaTrader5": MagicMock()}):
            import MetaTrader5 as mt5  # noqa: N813

            mt5.initialize.return_value = True
            mt5.login.return_value = False
            mt5.last_error.return_value = "Auth failed"
            result = await p.connect()
            assert result is False

    async def test_get_balance_mocked(self) -> None:
        p = ExnessProvider()
        mock_mt5 = MagicMock()
        mock_mt5.account_info.return_value = MagicMock(balance=5000.0)
        p._mt5 = mock_mt5
        p._connected = True
        balance = await p.get_balance()
        assert balance == 5000.0

    async def test_place_order_mocked_success(self) -> None:
        p = ExnessProvider()
        mock_mt5 = MagicMock()
        mock_result = MagicMock()
        mock_result.retcode = 10009
        mock_result.order = 42
        mock_result.volume = 0.1
        mock_result.price = 2500.0
        mock_result.comment = "done"
        mock_mt5.order_send.return_value = mock_result
        p._mt5 = mock_mt5
        p._connected = True

        order = Order(
            symbol="XAU/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.1,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 0.1
        assert result.filled_price == 2500.0

    async def test_place_order_mocked_rejected(self) -> None:
        p = ExnessProvider()
        mock_mt5 = MagicMock()
        mock_result = MagicMock()
        mock_result.retcode = 10014
        mock_result.order = 0
        mock_result.comment = "Invalid volume"
        mock_mt5.order_send.return_value = mock_result
        p._mt5 = mock_mt5
        p._connected = True

        order = Order(
            symbol="XAU/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=100.0,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED

    async def test_get_candles_mocked(self) -> None:
        from datetime import datetime

        p = ExnessProvider()
        mock_mt5 = MagicMock()
        mock_mt5.copy_rates_from_pos.return_value = [
            MagicMock(
                time=int(datetime(2026, 1, 1, 8, 0, 0).timestamp()),
                open=1.1050, high=1.1055, low=1.1048, close=1.1052,
                tick_volume=1000, spread=1,
            ),
        ]
        p._mt5 = mock_mt5
        p._connected = True

        candles = await p.get_candles("EUR/USD", "1h", limit=1)
        assert len(candles) == 1
        assert candles[0].symbol == "EUR/USD"
        assert candles[0].close == 1.1052

    async def test_get_symbols_mocked(self) -> None:
        p = ExnessProvider()
        mock_mt5 = MagicMock()
        s1, s2 = MagicMock(), MagicMock()
        s1.name = "EUR/USD"
        s2.name = "XAU/USD"
        mock_mt5.symbols_get.return_value = [s1, s2]
        p._mt5 = mock_mt5
        p._connected = True

        symbols = await p.get_symbols()
        assert symbols == ["EUR/USD", "XAU/USD"]


# ===========================================================================
#  CCXTProvider
# ===========================================================================


class TestCCXTProvider:
    """CCXTProvider connect/disconnect, mocked exchange calls."""

    @pytest.fixture
    def provider(self) -> CCXTProvider:
        return CCXTProvider()

    async def test_name(self, provider: CCXTProvider) -> None:
        assert provider.name == "binance"

    async def test_custom_name(self) -> None:
        p = CCXTProvider(exchange_id="bybit", name="my_bybit")
        assert p.name == "my_bybit"

    async def test_market_type(self, provider: CCXTProvider) -> None:
        assert provider.market_type == MarketType.CRYPTO

    async def test_connect_unsupported_exchange(self) -> None:
        p = CCXTProvider(exchange_id="nonexistent")
        result = await p.connect()
        assert result is False

    async def test_connect_mocked(self) -> None:
        """Set exchange directly (avoids mocking ccxt import)."""
        p = CCXTProvider(exchange_id="binance")
        mock_exchange = AsyncMock()
        mock_exchange.load_markets = AsyncMock()
        mock_exchange.symbols = ["BTC/USDT", "ETH/USDT"]
        p._exchange = mock_exchange
        p._connected = True

        assert await p.is_connected() is True
        # Use the exchange via another method to verify it works
        symbols = await p.get_symbols()
        assert "BTC/USDT" in symbols
        mock_exchange.load_markets.assert_not_called()  # was already set

    async def test_get_balance_mocked(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.fetch_balance = AsyncMock(
            return_value={"free": {"USDT": 5000.0}, "total": {}}
        )
        p._exchange = mock_exchange
        p._connected = True

        balance = await p.get_balance()
        assert balance == 5000.0

    async def test_place_order_mocked(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(
            return_value={
                "id": "ccxt-001",
                "status": "closed",
                "filled": 0.01,
                "price": 50000.0,
                "average": 50000.0,
            }
        )
        p._exchange = mock_exchange
        p._connected = True

        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.01,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.order_id == "ccxt-001"

    async def test_place_order_rejected(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.create_order = AsyncMock(
            side_effect=Exception("Insufficient funds")
        )
        p._exchange = mock_exchange
        p._connected = True

        order = Order(
            symbol="BTC/USDT", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1000,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED

    async def test_cancel_order_mocked(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.cancel_order = AsyncMock(return_value={})
        p._exchange = mock_exchange
        p._connected = True

        assert await p.cancel_order("order-1") is True

    async def test_get_candles_mocked(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv = AsyncMock(
            return_value=[
                [1704067200000, 50000.0, 50100.0, 49900.0, 50050.0, 100.0],
            ]
        )
        p._exchange = mock_exchange
        p._connected = True

        candles = await p.get_candles("BTC/USDT", "1h", limit=1)
        assert len(candles) == 1
        assert candles[0].close == 50050.0
        assert candles[0].symbol == "BTC"

    async def test_get_symbols_mocked(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        p._exchange = mock_exchange
        p._connected = True

        symbols = await p.get_symbols()
        assert len(symbols) == 3
        assert "BTC/USDT" in symbols

    async def test_disconnect_mocked(self) -> None:
        p = CCXTProvider()
        mock_exchange = AsyncMock()
        mock_exchange.close = AsyncMock()
        p._exchange = mock_exchange
        p._connected = True

        await p.disconnect()
        mock_exchange.close.assert_awaited_once()
        assert await p.is_connected() is False

    async def test_methods_not_connected_raise(self) -> None:
        p = CCXTProvider()
        with pytest.raises(ConnectionError):
            await p.get_balance()
        with pytest.raises(ConnectionError):
            await p.get_positions()
        with pytest.raises(ConnectionError):
            await p.get_candles("BTC/USDT", "1h")
        with pytest.raises(ConnectionError):
            await p.get_symbols()




# ===========================================================================
#  Cross-provider acceptance: all implement BaseProvider fully
# ===========================================================================

PROVIDER_CLASSES: dict[str, type] = {
    "ExnessProvider": ExnessProvider,
    "CCXTProvider": CCXTProvider,
}

REQUIRED_METHODS = [
    "connect", "disconnect", "is_connected",
    "get_balance", "get_positions", "place_order",
    "cancel_order", "get_candles", "get_symbols",
]


class TestAllProvidersImplementBaseProvider:
    """Each provider implements the full BaseProvider interface."""

    async def test_all_have_name_property(self) -> None:
        for name, cls in PROVIDER_CLASSES.items():
            assert hasattr(cls, "name"), f"{name} missing 'name' property"

    async def test_all_have_market_type_property(self) -> None:
        for name, cls in PROVIDER_CLASSES.items():
            assert hasattr(cls, "market_type"), f"{name} missing 'market_type'"

    async def test_all_have_required_methods(self) -> None:
        for name, cls in PROVIDER_CLASSES.items():
            for method in REQUIRED_METHODS:
                assert hasattr(cls, method), f"{name} missing '{method}'"
                assert callable(getattr(cls, method)), f"{name}.{method} not callable"


# ===========================================================================
#  Coverage helpers — test utility/helper functions directly
# ===========================================================================


class TestExnessHelpers:
    """Coverage for Exness helper functions."""

    async def test_parse_timeframe_default(self) -> None:
        from trading_bot.providers.forex.exness import _parse_timeframe

        assert _parse_timeframe("1h") == 60
        assert _parse_timeframe("5m") == 5
        assert _parse_timeframe("1d") == 1440

    async def test_parse_timeframe_fallback_without_mt5(self) -> None:
        from trading_bot.providers.forex.exness import _parse_timeframe

        # When MT5 is not available, uses dict lookup
        assert _parse_timeframe("unknown") == 60  # fallback to 1h



class TestCCXTHelpers:
    """Coverage for CCXT helper functions."""

    async def test_ccxt_symbol_already_formatted(self) -> None:
        from trading_bot.providers.crypto.ccxt_adapter import _ccxt_symbol

        assert _ccxt_symbol("BTC/USDT") == "BTC/USDT"

    async def test_ccxt_symbol_maps_dash_usd(self) -> None:
        from trading_bot.providers.crypto.ccxt_adapter import _ccxt_symbol

        assert _ccxt_symbol("BTC-USD", "binance") == "BTC/USDT"

    async def test_ccxt_symbol_default_suffix(self) -> None:
        from trading_bot.providers.crypto.ccxt_adapter import _ccxt_symbol

        assert _ccxt_symbol("BTC") == "BTC/USDT"

    async def test_ccxt_timeframe(self) -> None:
        from trading_bot.providers.crypto.ccxt_adapter import (
            _ccxt_timeframe,
        )

        assert _ccxt_timeframe("1m") == "1m"
        assert _ccxt_timeframe("1d") == "1d"
        assert _ccxt_timeframe("unknown") == "1h"  # fallback

    async def test_ccxt_candle_to_model(self) -> None:
        from trading_bot.providers.crypto.ccxt_adapter import (
            _ccxt_candle_to_model,
        )

        row = [1704067200000, 50000.0, 50100.0, 49900.0, 50050.0, 100.0]
        candle = _ccxt_candle_to_model(row, "BTC/USDT", "1h")
        assert candle.symbol == "BTC"
        assert candle.close == 50050.0
        assert candle.timeframe == "1h"


class TestPaperTraderCoverage:
    """Coverage edge cases for PaperTradingProvider."""

    async def test_unsupported_order_type(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        p.inject_candles("XAU/USD", [
            Candle("XAU/USD", "1h", 2500.0, 2510.0, 2490.0, 2505.0, 5000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0)),
        ])
        order = Order(
            symbol="XAU/USD", side=OrderSide.BUY,
            order_type=OrderType.STOP_LIMIT, quantity=1,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED

    async def test_stop_order_without_stop_price(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.STOP, quantity=1,
            stop_price=None,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED

    async def test_limit_without_price(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=1,
            price=None,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED


class TestRegistryCoverage:
    """Coverage edge cases for ProviderRegistry."""

    async def test_list_by_type_with_matches(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        p = PaperTradingProvider(name="test")
        registry.register(p)
        forex = registry.list_by_type("forex")
        assert len(forex) == 1
        crypto = registry.list_by_type("crypto")
        assert len(crypto) == 0

    async def test_get_all_empty(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        assert registry.get_all() == []

    async def test_unregister_nonexistent(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        registry.unregister("nonexistent")  # should not raise
        assert registry.count == 0
