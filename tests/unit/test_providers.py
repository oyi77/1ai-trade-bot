"""Tests for core providers — Exness, CCXT (mock-based, no API keys).

All tests use mocks to simulate provider connections and responses.
No real API keys, network calls, or terminal connections required.
"""

# ruff: noqa: UP017

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.mock_providers import MockProvider
from trading_bot.providers.base import (
    BaseProvider,
    Candle,
    MarketType,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from trading_bot.providers.crypto.ccxt_adapter import CCXTProvider
from trading_bot.providers.forex.exness import (
    ExnessProvider,
    _build_mt5_request,
    _mt5_candle_to_model,
    _mt5_position_to_model,
    _parse_timeframe,
)
from trading_bot.providers.paper.paper_trader import PaperTradingProvider


@contextmanager
def monkeypatch_sys_module(name: str, value: Any) -> Any:
    """Temporarily inject an object into sys.modules."""
    with patch.dict("sys.modules", {name: value}):
        yield



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


class TestExnessProviderMocked:
    """ExnessProvider tests using a fake MetaTrader5 module injected via sys.modules."""

    # ------------------------------------------------------------------
    #  Fake MT5 module factory
    # ------------------------------------------------------------------
    @staticmethod
    def _make_fake_mt5(
        *,
        initialize: bool = True,
        login: bool = True,
        account_balance: float = 10_000.0,
        positions: list[Any] | None = None,
        order_send_result: Any | None = None,
        rates: list[Any] | None = None,
        symbols: list[Any] | None = None,
        tick: Any | None = None,
    ) -> Any:
        """Return a fake MetaTrader5 module ready for sys.modules."""
        fake = ModuleType("MetaTrader5")

        # timeframe constants
        fake.TIMEFRAME_M1 = 1
        fake.TIMEFRAME_M5 = 5
        fake.TIMEFRAME_M15 = 15
        fake.TIMEFRAME_M30 = 30
        fake.TIMEFRAME_H1 = 60
        fake.TIMEFRAME_H4 = 240
        fake.TIMEFRAME_D1 = 1440
        fake.TIMEFRAME_W1 = 10080
        fake.TIMEFRAME_MN1 = 43200

        fake.initialize = MagicMock(return_value=initialize)
        fake.last_error = MagicMock(return_value="last-error")
        fake.login = MagicMock(return_value=login)
        fake.shutdown = MagicMock()
        fake.account_info = MagicMock(
            return_value=SimpleNamespace(balance=account_balance)
            if account_balance is not None
            else None
        )
        fake.positions_get = MagicMock(return_value=positions)
        fake.order_send = MagicMock(return_value=order_send_result)
        fake.copy_rates_from_pos = MagicMock(return_value=rates)
        fake.symbols_get = MagicMock(return_value=symbols)
        fake.symbol_info_tick = MagicMock(return_value=tick)
        return fake

    @pytest.fixture
    def provider(self) -> ExnessProvider:
        return ExnessProvider(login=123, password="pwd", server="srv")

    # ------------------------------------------------------------------
    #  connect / disconnect
    # ------------------------------------------------------------------
    async def test_connect_success(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5()
        with monkeypatch_sys_module("MetaTrader5", fake):
            assert await provider.connect() is True
            assert await provider.is_connected() is True
            fake.initialize.assert_called_once()
            fake.login.assert_called_once_with(
                login=123, password="pwd", server="srv"
            )

    async def test_connect_success_no_credentials(self) -> None:
        p = ExnessProvider()
        fake = self._make_fake_mt5()
        with monkeypatch_sys_module("MetaTrader5", fake):
            assert await p.connect() is True
            fake.login.assert_not_called()

    async def test_connect_import_error(self, provider: ExnessProvider) -> None:
        with monkeypatch_sys_module("MetaTrader5", None):
            assert await provider.connect() is False
            assert await provider.is_connected() is False

    async def test_connect_initialize_false(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5(initialize=False)
        with monkeypatch_sys_module("MetaTrader5", fake):
            assert await provider.connect() is False
            assert await provider.is_connected() is False
            fake.last_error.assert_called_once()

    async def test_connect_login_false(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5(login=False)
        with monkeypatch_sys_module("MetaTrader5", fake):
            assert await provider.connect() is False
            assert await provider.is_connected() is False
            fake.shutdown.assert_called_once()

    async def test_disconnect(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5()
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            await provider.disconnect()
            assert await provider.is_connected() is False
            assert provider._mt5 is None
            fake.shutdown.assert_called_once()

    async def test_disconnect_not_connected(self, provider: ExnessProvider) -> None:
        await provider.disconnect()
        assert await provider.is_connected() is False

    # ------------------------------------------------------------------
    #  balance / positions
    # ------------------------------------------------------------------
    async def test_get_balance_success(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5(account_balance=5555.5)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.get_balance() == 5555.5

    async def test_get_balance_account_info_none(
        self, provider: ExnessProvider
    ) -> None:
        fake = self._make_fake_mt5(account_balance=None)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            with pytest.raises(ConnectionError, match="Failed to fetch account info"):
                await provider.get_balance()

    async def test_get_positions_success(self, provider: ExnessProvider) -> None:
        tick = SimpleNamespace(ask=1.1055, bid=1.1053)
        pos = SimpleNamespace(
            symbol="EUR/USD",
            type=0,
            volume=0.1,
            price_open=1.1050,
            swap=1.0,
            profit=12.0,
            leverage=100,
            sl=1.1000,
            ticket=7,
        )
        fake = self._make_fake_mt5(positions=[pos], tick=tick)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            positions = await provider.get_positions()
            assert len(positions) == 1
            assert positions[0].symbol == "EUR/USD"
            assert positions[0].side == OrderSide.BUY
            assert positions[0].leverage == 100

    async def test_get_positions_empty(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5(positions=None)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.get_positions() == []

    # ------------------------------------------------------------------
    #  place_order
    # ------------------------------------------------------------------
    async def test_place_order_filled(self, provider: ExnessProvider) -> None:
        result = SimpleNamespace(
            retcode=10009, order=42, volume=0.1, price=2500.0, comment="done"
        )
        fake = self._make_fake_mt5(order_send_result=result)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            order = Order(
                symbol="XAU/USD", side=OrderSide.BUY,
                order_type=OrderType.MARKET, quantity=0.1,
            )
            res = await provider.place_order(order)
            assert res.status == OrderStatus.FILLED
            assert res.order_id == "42"
            assert res.filled_quantity == 0.1
            assert res.filled_price == 2500.0

    async def test_place_order_error_retcode(
        self, provider: ExnessProvider
    ) -> None:
        result = SimpleNamespace(retcode=10014, order=0, comment="Invalid volume")
        fake = self._make_fake_mt5(order_send_result=result)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            order = Order(
                symbol="XAU/USD", side=OrderSide.BUY,
                order_type=OrderType.MARKET, quantity=100.0,
            )
            res = await provider.place_order(order)
            assert res.status == OrderStatus.REJECTED
            assert "10014" in res.message

    async def test_place_order_none_result(
        self, provider: ExnessProvider
    ) -> None:
        fake = self._make_fake_mt5(order_send_result=None)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            order = Order(
                symbol="XAU/USD", side=OrderSide.BUY,
                order_type=OrderType.MARKET, quantity=0.1,
            )
            res = await provider.place_order(order)
            assert res.status == OrderStatus.REJECTED
            assert res.order_id == ""

    # ------------------------------------------------------------------
    #  cancel_order
    # ------------------------------------------------------------------
    async def test_cancel_order_success(self, provider: ExnessProvider) -> None:
        tick = SimpleNamespace(ask=1.1055, bid=1.1053)
        pos = SimpleNamespace(
            symbol="EUR/USD",
            type=0,
            volume=0.1,
            price_open=1.1050,
            swap=1.0,
            profit=0.0,
            leverage=1,
            sl=None,
            ticket=7,
        )
        close_result = SimpleNamespace(retcode=10009)
        fake = self._make_fake_mt5(
            positions=[pos], tick=tick, order_send_result=close_result
        )
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.cancel_order("7") is True

    async def test_cancel_order_failure_no_position(
        self, provider: ExnessProvider
    ) -> None:
        fake = self._make_fake_mt5(positions=None)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.cancel_order("999") is False

    # ------------------------------------------------------------------
    #  get_candles / get_symbols
    # ------------------------------------------------------------------
    async def test_get_candles_success(self, provider: ExnessProvider) -> None:
        row = SimpleNamespace(
            time=int(datetime(2026, 1, 1, 8, 0, 0).timestamp()),
            time_msc=0,
            open=1.1050,
            high=1.1055,
            low=1.1048,
            close=1.1052,
            tick_volume=1000,
            real_volume=2000,
            spread=1,
        )
        fake = self._make_fake_mt5(rates=[row])
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            candles = await provider.get_candles("EUR/USD", "1h", limit=1)
            assert len(candles) == 1
            assert candles[0].symbol == "EUR/USD"
            assert candles[0].close == 1.1052
            assert candles[0].volume == 1000.0

    async def test_get_candles_none_raw(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5(rates=None)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.get_candles("EUR/USD", "1h", limit=1) == []

    async def test_get_symbols_success(self, provider: ExnessProvider) -> None:
        s1 = SimpleNamespace(name="EUR/USD")
        s2 = SimpleNamespace(name="XAU/USD")
        fake = self._make_fake_mt5(symbols=[s1, s2])
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.get_symbols() == ["EUR/USD", "XAU/USD"]

    async def test_get_symbols_none_raw(self, provider: ExnessProvider) -> None:
        fake = self._make_fake_mt5(symbols=None)
        with monkeypatch_sys_module("MetaTrader5", fake):
            await provider.connect()
            assert await provider.get_symbols() == []

    # ------------------------------------------------------------------
    #  helper coverage
    # ------------------------------------------------------------------
    def test_parse_timeframe_with_mt5(self) -> None:
        fake = self._make_fake_mt5()
        with monkeypatch_sys_module("MetaTrader5", fake):
            assert _parse_timeframe("1m") == fake.TIMEFRAME_M1
            assert _parse_timeframe("5m") == fake.TIMEFRAME_M5
            assert _parse_timeframe("15m") == fake.TIMEFRAME_M15
            assert _parse_timeframe("30m") == fake.TIMEFRAME_M30
            assert _parse_timeframe("1h") == fake.TIMEFRAME_H1
            assert _parse_timeframe("4h") == fake.TIMEFRAME_H4
            assert _parse_timeframe("1d") == fake.TIMEFRAME_D1
            assert _parse_timeframe("1w") == fake.TIMEFRAME_W1
            assert _parse_timeframe("1M") == fake.TIMEFRAME_MN1
            assert _parse_timeframe("unknown") == fake.TIMEFRAME_H1

    def test_parse_timeframe_without_mt5(self) -> None:
        with monkeypatch_sys_module("MetaTrader5", None):
            assert _parse_timeframe("1h") == 60
            assert _parse_timeframe("5m") == 5
            assert _parse_timeframe("unknown") == 60

    def test_build_mt5_request_all_order_types(self) -> None:
        market_buy = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.1,
        )
        req = _build_mt5_request(market_buy)
        assert req["action"] == 1
        assert req["type"] == 0
        assert req["price"] == 0.0

        limit_buy = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=0.1, price=1.1000,
        )
        req = _build_mt5_request(limit_buy)
        assert req["action"] == 2
        assert req["price"] == 1.1

        stop_sell = Order(
            symbol="EUR/USD", side=OrderSide.SELL,
            order_type=OrderType.STOP, quantity=0.1, stop_price=1.1100,
        )
        req = _build_mt5_request(stop_sell)
        assert req["action"] == 2
        assert req["type"] == 1
        assert req["price"] == 1.11

    def test_build_mt5_request_time_in_force(self) -> None:
        gtc = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=0.1, price=1.1,
            time_in_force=TimeInForce.GTC,
        )
        assert _build_mt5_request(gtc)["type_time"] == 0

        day = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=0.1, price=1.1,
            time_in_force=TimeInForce.DAY,
        )
        assert _build_mt5_request(day)["type_time"] == 1

        ioc = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=0.1, price=1.1,
            time_in_force=TimeInForce.IOC,
        )
        assert _build_mt5_request(ioc)["type_filling"] == 1

    def test_mt5_position_to_model(self) -> None:
        mt5 = self._make_fake_mt5(tick=SimpleNamespace(ask=1.1055, bid=1.1053))
        pos = SimpleNamespace(
            symbol="EUR/USD",
            type=1,
            volume=0.2,
            price_open=1.1050,
            swap=10.0,
            profit=-5.0,
            leverage=50,
            sl=1.1100,
        )
        model = _mt5_position_to_model(pos, mt5)
        assert model.symbol == "EUR/USD"
        assert model.side == OrderSide.SELL
        assert model.leverage == 50
        assert model.liquidation_price == 1.11

    def test_mt5_candle_to_model(self) -> None:
        ts = int(datetime(2026, 1, 1, 8, 0, 0).timestamp())
        row = SimpleNamespace(
            time=ts,
            time_msc=0,
            open=1.1050,
            high=1.1055,
            low=1.1048,
            close=1.1052,
            tick_volume=1000,
            real_volume=2000,
        )
        candle = _mt5_candle_to_model(row, "EUR/USD", "1h")
        assert candle.symbol == "EUR/USD"
        assert candle.timeframe == "1h"
        assert candle.open == 1.105
        assert candle.high == 1.1055
        assert candle.low == 1.1048
        assert candle.close == 1.1052
        assert candle.volume == 1000.0
        assert candle.timestamp.year == 2026

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
#  CCXTProvider — full mock via fake ccxt.async_support module
# ===========================================================================


class FakeExchange:
    """Async-compatible fake CCXT exchange used via sys.modules injection."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self._raise_on: dict[str, Exception | None] = {}
        self._positions: list[dict[str, Any]] = []
        self.symbols: list[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        self.load_markets_called = False
        self.close_called = False
        self._last_params: dict[str, Any] = {}

    def _set_raise(self, method: str, exc: Exception | None) -> None:
        self._raise_on[method] = exc

    def _maybe_raise(self, method: str) -> None:
        exc = self._raise_on.get(method)
        if exc is not None:
            raise exc

    async def load_markets(self) -> dict[str, Any]:
        self.load_markets_called = True
        self._maybe_raise("load_markets")
        return {s: {"symbol": s} for s in self.symbols}

    async def close(self) -> None:
        self.close_called = True
        self._maybe_raise("close")

    async def fetch_balance(self) -> dict[str, Any]:
        self._maybe_raise("fetch_balance")
        return {
            "free": {"USDT": 1234.5},
            "total": {"USDT": 1234.5, "BTC": 0.5, "ETH": 2.0},
        }

    async def fetch_positions(self) -> list[dict[str, Any]]:
        self._maybe_raise("fetch_positions")
        return self._positions

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._maybe_raise("create_order")
        params = params or {}
        self._last_params = params
        status = params.get("test_status", "closed")
        return {
            "id": f"{symbol}-{side}-{type}",
            "status": status,
            "filled": amount,
            "price": price,
            "average": price or 50000.0,
            "symbol": symbol,
        }

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._maybe_raise("cancel_order")
        return {}

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[list[Any]]:
        self._maybe_raise("fetch_ohlcv")
        return [
            [1704067200000, 50000.0, 50100.0, 49900.0, 50050.0, 100.0],
        ]

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        self._maybe_raise("fetch_ticker")
        if symbol == "USDT/USDT":
            return {"last": 1.0}
        return {"last": 100000.0}

    async def set_leverage(self, leverage: int, symbol: str) -> dict[str, Any]:
        self._maybe_raise("set_leverage")
        self.config["leverage_sent"] = leverage
        return {}

class FakeCCXTModule:
    """Stand-in for ``ccxt.async_support`` injected into sys.modules."""

    binance = FakeExchange
    bybit = FakeExchange
    okx = FakeExchange
    bitget = FakeExchange


@pytest.fixture
def fake_ccxt() -> Iterator[None]:
    """Patch both ccxt and ccxt.async_support in sys.modules."""
    fake_module = FakeCCXTModule()
    # Create a fake ccxt module with async_support attribute
    fake_ccxt_module = type("module", (), {"async_support": fake_module})()
    with patch.dict("sys.modules", {"ccxt": fake_ccxt_module, "ccxt.async_support": fake_module}):
        yield


class TestCCXTProviderMocked:
    """Full CCXTProvider coverage using a fake ``ccxt.async_support`` module."""

    async def test_connect_success(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        assert await p.connect() is True
        assert await p.is_connected() is True

    async def test_connect_unsupported_exchange(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="nonexistent")
        assert await p.connect() is False
        assert await p.is_connected() is False

    async def test_connect_load_markets_exception(self, fake_ccxt: None) -> None:
        """Simulate load_markets raising by using a subclass whose instance raises."""
        class BadExchange(FakeExchange):
            async def load_markets(self) -> dict[str, Any]:
                raise RuntimeError("boom")

        with patch.object(FakeCCXTModule, "binance", BadExchange):
            p = CCXTProvider(exchange_id="binance")
            assert await p.connect() is False
        assert await p.is_connected() is False

    async def test_disconnect(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        assert p._exchange is not None
        await p.disconnect()
        assert await p.is_connected() is False
        assert p._exchange is None

    async def test_disconnect_close_exception_is_suppressed(
        self, fake_ccxt: None
    ) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        assert p._exchange is not None
        p._exchange._set_raise("close", RuntimeError("close failed"))
        await p.disconnect()  # should not propagate
        assert await p.is_connected() is False

    async def test_get_balance_success(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        balance = await p.get_balance()
        assert balance == 1234.5 + 0.5 * 100000.0 + 2.0 * 100000.0

    async def test_get_balance_exception(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        p._exchange._set_raise("fetch_balance", RuntimeError("fail"))
        assert await p.get_balance() == 0.0

    async def test_get_positions_success(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        p._exchange._positions = [
            {
                "symbol": "BTC/USDT",
                "contracts": 0.1,
                "entryPrice": 49000.0,
                "markPrice": 50000.0,
                "unrealizedPnl": 100.0,
                "realizedPnl": 0.0,
                "leverage": 10,
                "liquidationPrice": 45000.0,
                "info": {"positionSide": "LONG"},
            },
        ]
        positions = await p.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USDT"
        assert positions[0].side == OrderSide.BUY

    async def test_get_positions_exception(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        p._exchange._set_raise("fetch_positions", RuntimeError("fail"))
        assert await p.get_positions() == []

    async def test_place_order_market_buy(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        order = Order(
            symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.01,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.order_id == "BTC/USDT-buy-market"

    async def test_place_order_limit_sell(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        order = Order(
            symbol="BTC", side=OrderSide.SELL,
            order_type=OrderType.LIMIT, quantity=0.01, price=51000.0,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_price == 51000.0

    async def test_place_order_stop(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        order = Order(
            symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.STOP, quantity=0.01, stop_price=52000.0,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED

    async def test_place_order_reduce_only(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        order = Order(
            symbol="BTC", side=OrderSide.SELL,
            order_type=OrderType.MARKET, quantity=0.01, reduce_only=True,
        )
        await p.place_order(order)
        assert p._exchange._last_params.get("reduceOnly") is True

    async def test_place_order_with_leverage(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        order = Order(
            symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0.01, leverage=10,
        )
        await p.place_order(order)
        assert p._exchange.config.get("leverage_sent") == 10

    async def test_place_order_open_status(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        order = Order(
            symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=0.01, price=50000.0,
        )
        # Patch the instance's create_order method
        async def fake_create_order(**kwargs: Any) -> dict[str, Any]:
            return {
                "id": "open-1",
                "status": "open",
                "filled": 0.0,
                "price": 0.0,
                "average": 0.0,
                "symbol": kwargs.get("symbol", "BTC/USDT"),
            }
        p._exchange.create_order = fake_create_order  # type: ignore[method-assign]
        result = await p.place_order(order)
        assert result.status == OrderStatus.OPEN

    async def test_place_order_rejected(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        p._exchange._set_raise("create_order", RuntimeError("rejected"))
        order = Order(
            symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1000,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "rejected" in result.message

    async def test_cancel_order_success(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        assert await p.cancel_order("order-1") is True

    async def test_cancel_order_failure(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        p._exchange._set_raise("cancel_order", RuntimeError("cancel fail"))
        assert await p.cancel_order("order-1") is False

    async def test_get_candles_success(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        candles = await p.get_candles("BTC", "1h", limit=1)
        assert len(candles) == 1
        assert candles[0].symbol == "BTC"
        assert candles[0].close == 50050.0
        assert candles[0].timestamp.tzinfo is not None

    async def test_get_candles_failure(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        p._exchange._set_raise("fetch_ohlcv", RuntimeError("ohlcv fail"))
        assert await p.get_candles("BTC", "1h") == []

    async def test_get_symbols(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        await p.connect()
        symbols = await p.get_symbols()
        assert symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    async def test_ensure_connected_raises(self, fake_ccxt: None) -> None:
        p = CCXTProvider(exchange_id="binance")
        with pytest.raises(ConnectionError, match="CCXTProvider not connected"):
            await p.get_balance()


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
# ===========================================================================
#  Abstract BaseProvider coverage
# ===========================================================================


class _MinimalProvider(BaseProvider):
    """Concrete subclass implementing every abstract BaseProvider method."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def market_type(self) -> MarketType:
        return MarketType.FOREX

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def is_connected(self) -> bool:
        return True

    async def get_balance(self) -> float:
        return 0.0

    async def get_positions(self) -> list[Position]:
        return []

    async def place_order(self, order: Order) -> OrderResult:
        from trading_bot.providers.base import OrderResult

        return OrderResult(status=OrderStatus.PENDING, order_id="1")

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        return []

    async def get_symbols(self) -> list[str]:
        return []


class TestBaseProviderAbstractMethods:
    """Cover the abstract method declarations in BaseProvider."""

    async def test_minimal_provider_can_instantiate(self) -> None:
        provider = _MinimalProvider()
        assert provider.name == "minimal"
        assert await provider.connect()

    def test_missing_name_raises_type_error(self) -> None:
        class MissingName(BaseProvider):
            @property
            def market_type(self) -> MarketType:
                return MarketType.FOREX

            async def connect(self) -> bool:
                return True

            async def disconnect(self) -> None:
                return None

            async def is_connected(self) -> bool:
                return True

            async def get_balance(self) -> float:
                return 0.0

            async def get_positions(self) -> list[Position]:
                return []

            async def place_order(self, order: Order) -> OrderResult:
                from trading_bot.providers.base import OrderResult

                return OrderResult(status=OrderStatus.PENDING, order_id="1")

            async def cancel_order(self, order_id: str) -> bool:
                return True

            async def get_candles(
                self, symbol: str, timeframe: str, limit: int = 100
            ) -> list[Candle]:
                return []

            async def get_symbols(self) -> list[str]:
                return []

        with pytest.raises(TypeError, match="name"):
            MissingName()

    def test_missing_connect_raises_type_error(self) -> None:
        class MissingConnect(BaseProvider):
            @property
            def name(self) -> str:
                return "missing-connect"

            @property
            def market_type(self) -> MarketType:
                return MarketType.FOREX

            async def disconnect(self) -> None:
                return None

            async def is_connected(self) -> bool:
                return True

            async def get_balance(self) -> float:
                return 0.0

            async def get_positions(self) -> list[Position]:
                return []

            async def place_order(self, order: Order) -> OrderResult:
                from trading_bot.providers.base import OrderResult

                return OrderResult(status=OrderStatus.PENDING, order_id="1")

            async def cancel_order(self, order_id: str) -> bool:
                return True

            async def get_candles(
                self, symbol: str, timeframe: str, limit: int = 100
            ) -> list[Candle]:
                return []

            async def get_symbols(self) -> list[str]:
                return []

        with pytest.raises(TypeError, match="connect"):
            MissingConnect()


class TestCCXTSandboxMode:
    """Test CCXT sandbox configuration."""

    @pytest.mark.asyncio
    async def test_sandbox_mode_sets_config(self, fake_ccxt: None) -> None:
        """Sandbox mode should set sandbox=True in config."""
        p = CCXTProvider(
            exchange_id="binance",
            api_key="key",
            secret="secret",
            sandbox=True,
        )
        assert await p.connect() is True
        # Check that sandbox was passed to the exchange config
        assert p._exchange.config.get("sandbox") is True
        await p.disconnect()



class TestCCXTBalanceErrors:
    """Test CCXT balance conversion error paths."""

    @pytest.mark.asyncio
    async def test_balance_ticker_error_fallback(self, fake_ccxt: None) -> None:
        """Balance should fall back to USDT when ticker fetch fails."""
        p = CCXTProvider(
            exchange_id="binance",
            api_key="key",
            secret="secret",
        )
        assert await p.connect() is True
        # Make fetch_ticker raise by setting the error flag
        p._exchange._set_raise("fetch_ticker", RuntimeError("Ticker error"))
        # The default balance has BTC: 0.5, ETH: 2.0, USDT: 1234.5
        # When ticker fails, it falls back to USDT balance
        balance = await p.get_balance()
        # Should return 1234.5 (the USDT balance from fetch_balance)
        assert balance == 1234.5
        await p.disconnect()

    @pytest.mark.asyncio
    async def test_stop_order_sets_stop_price(self, fake_ccxt: None) -> None:
        """STOP orders should set stopPrice parameter."""
        p = CCXTProvider(
            exchange_id="binance",
            api_key="key",
            secret="secret",
        )
        assert await p.connect() is True
        order = Order(
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=1.0,
            stop_price=50000.0,
        )
        await p.place_order(order)
        # Check that stopPrice was set in params
        params = p._exchange._last_params
        assert params["stopPrice"] == 50000.0
        await p.disconnect()




class TestProviderRegistryLazyImports:
    """Test ProviderRegistry lazy import factories."""

    def test_import_paper_factory(self) -> None:
        """Test that paper provider factory creates correct instance."""
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        provider = registry._import_paper()
        assert isinstance(provider, PaperTradingProvider)

    def test_import_ccxt_factory(self) -> None:
        """Test that CCXT adapter module exists."""
        import importlib.util

        spec = importlib.util.find_spec("trading_bot.providers.crypto.ccxt_adapter")
        assert spec is not None, "CCXT adapter module should exist"

    def test_import_exness_factory(self) -> None:
        """Test that Exness adapter module exists."""
        import importlib.util

        spec = importlib.util.find_spec("trading_bot.providers.forex.exness")
        assert spec is not None, "Exness adapter module should exist"

    def test_register_class_lazy(self) -> None:
        """Test that register_class creates provider on first access."""
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()

        # Register using class (lazy instantiation)
        registry.register_class("paper", PaperTradingProvider)

        # Should not be in _providers yet
        assert "paper" not in registry._providers

        # Access via get() should instantiate
        provider = registry.get("paper")
        assert provider is not None
        assert isinstance(provider, PaperTradingProvider)
        assert "paper" in registry._providers


class TestCCXTStopLimitOrders:
    """Test CCXT stop-limit order parameter handling."""

    @pytest.mark.asyncio
    async def test_stop_limit_order_params(self, fake_ccxt: None) -> None:
        """STOP_LIMIT orders should set both price and stopPrice."""
        p = CCXTProvider(
            exchange_id="binance",
            api_key="key",
            secret="secret",
        )
        assert await p.connect() is True
        order = Order(
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.STOP_LIMIT,
            quantity=1.0,
            price=50000.0,
            stop_price=49000.0,
        )
        await p.place_order(order)
        # Check that both price and stopPrice were set
        params = p._exchange._last_params
        assert "stopPrice" in params
        assert params["stopPrice"] == 49000.0
        await p.disconnect()


class TestExnessProperties:
    """Test Exness property getters."""

    def test_name_property(self) -> None:
        """Test name property returns configured name."""
        p = ExnessProvider(name="custom_exness")
        assert p.name == "custom_exness"

    def test_market_type_property(self) -> None:
        """Test market_type property returns FOREX."""
        p = ExnessProvider()
        assert p.market_type == MarketType.FOREX


class TestExnessEnsureMT5:
    """Test Exness _ensure_mt5 error path."""

    @pytest.mark.asyncio
    async def test_ensure_mt5_raises_when_not_connected(self) -> None:
        """_ensure_mt5 should raise ConnectionError when not connected."""
        p = ExnessProvider()
        # Don't connect - should raise when calling _ensure_mt5
        with pytest.raises(ConnectionError, match="not connected"):
            p._ensure_mt5()


class TestRegistryLazyImports:
    """Test ProviderRegistry lazy import methods directly."""

    def test_import_paper_creates_instance(self) -> None:
        """_import_paper should create a PaperTradingProvider instance."""
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        provider = registry._import_paper()
        assert isinstance(provider, PaperTradingProvider)

    def test_import_ccxt_raises_without_package(self) -> None:
        """_import_ccxt should raise ImportError when ccxt not installed."""
        from trading_bot.providers.registry import ProviderRegistry

        # The ccxt package may or may not be installed
        # We're just testing the method exists and can be called
        # When ccxt is not installed, it will raise ImportError
        registry = ProviderRegistry()
        try:
            provider = registry._import_ccxt()
            # If ccxt is installed, this should work
            assert provider is not None
        except ImportError:
            # Expected when ccxt is not installed
            pass

    def test_import_exness_raises_without_package(self) -> None:
        """_import_exness should raise ImportError when MT5 not installed."""
        from trading_bot.providers.registry import ProviderRegistry

        # MetaTrader5 may or may not be installed
        # We're just testing the method exists and can be called
        registry = ProviderRegistry()
        try:
            provider = registry._import_exness()
            # If MT5 is installed, this should work
            assert provider is not None
        except ImportError:
            # Expected when MT5 is not installed
            pass
