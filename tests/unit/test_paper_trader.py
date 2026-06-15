# ruff: noqa: UP017
"""Tests for PaperTradingProvider — simulated trading."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_bot.providers.base import (
    Candle,
    MarketType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from trading_bot.providers.paper.paper_trader import PaperTradingProvider

# UP017 suppressed: project forbids datetime.UTC on Python 3.11


class TestPaperTradingProvider:
    """PaperTradingProvider lifecycle and basic properties."""

    async def test_connect_disconnect(self, paper_provider: PaperTradingProvider) -> None:
        assert await paper_provider.connect() is True
        assert await paper_provider.is_connected() is True
        await paper_provider.disconnect()
        assert await paper_provider.is_connected() is False

    async def test_name(self, paper_provider: PaperTradingProvider) -> None:
        assert paper_provider.name == "paper"

    async def test_custom_name(self) -> None:
        p = PaperTradingProvider(name="test_provider")
        assert p.name == "test_provider"

    async def test_market_type(self, paper_provider: PaperTradingProvider) -> None:
        assert paper_provider.market_type == MarketType.FOREX

    async def test_initial_balance(self, paper_provider: PaperTradingProvider) -> None:
        balance = await paper_provider.get_balance()
        assert balance == 10_000.0

    async def test_custom_initial_balance(self) -> None:
        p = PaperTradingProvider(initial_balance=5000.0)
        assert await p.get_balance() == 5000.0

    async def test_set_balance(self, paper_provider: PaperTradingProvider) -> None:
        paper_provider.set_balance(999.99)
        assert await paper_provider.get_balance() == 999.99

    async def test_default_symbols_empty(self, paper_provider: PaperTradingProvider) -> None:
        symbols = await paper_provider.get_symbols()
        assert symbols == []

    async def test_default_candles_empty(self, paper_provider: PaperTradingProvider) -> None:
        candles = await paper_provider.get_candles("EUR/USD", "1h")
        assert candles == []

    async def test_equity_equals_balance_initially(
        self, paper_provider: PaperTradingProvider,
    ) -> None:
        assert paper_provider.equity == await paper_provider.get_balance()


class TestMarketOrder:
    """Market order placement and fill simulation."""

    async def _setup_with_candles(self) -> PaperTradingProvider:
        p = PaperTradingProvider()
        await p.connect()
        candles = [
            Candle(
                symbol="EUR/USD", timeframe="1h",
                open=1.1050, high=1.1055, low=1.1048, close=1.1052,
                volume=1000, timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        p.inject_candles("EUR/USD", candles)
        return p

    async def test_market_buy_filled(self) -> None:
        p = await self._setup_with_candles()
        order = Order(
            symbol="EUR/USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 100
        assert result.filled_price == 1.1052
        assert result.order_id

    async def test_market_buy_insufficient_balance(self) -> None:
        p = PaperTradingProvider(initial_balance=10.0)
        await p.connect()
        p.inject_candles("EUR/USD", [
            Candle("EUR/USD", "1h", 100.0, 101.0, 99.0, 100.0, 1000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        order = Order(
            symbol="EUR/USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,  # costs 100 > 10
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "Insufficient balance" in result.message

    async def test_market_sell_filled(self) -> None:
        p = await self._setup_with_candles()
        order = Order(
            symbol="EUR/USD",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=100,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_price == 1.1052

    async def test_market_order_deducts_balance(self) -> None:
        p = PaperTradingProvider(initial_balance=1000.0)
        await p.connect()
        p.inject_candles("XAU/USD", [
            Candle("XAU/USD", "1h", 2500.0, 2510.0, 2490.0, 2505.0, 5000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        order = Order(
            symbol="XAU/USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
        )
        await p.place_order(order)
        # cost = 2505.0 * 0.1 = 250.5
        assert await p.get_balance() == pytest.approx(1000.0 - 250.5)


class TestLimitOrder:
    """Limit order trigger logic."""

    async def _provider_with_price(
        self, close_price: float,
    ) -> PaperTradingProvider:
        p = PaperTradingProvider(initial_balance=10_000.0)
        await p.connect()
        p.inject_candles("BTC/USD", [
            Candle("BTC/USD", "1h", 50000.0, 50100.0, 49900.0, close_price, 100,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        return p

    async def test_buy_limit_below_price_pending(self) -> None:
        p = await self._provider_with_price(50000.0)
        order = Order(
            symbol="BTC/USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=49000.0,  # below current
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.PENDING
        assert "Limit not reached" in result.message

    async def test_buy_limit_at_or_below_price_triggered(self) -> None:
        p = await self._provider_with_price(50000.0)
        order = Order(
            symbol="BTC/USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=50000.0,  # equal to current, should trigger
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_price == 50000.0

    async def test_sell_limit_at_price_triggered(self) -> None:
        p = await self._provider_with_price(50000.0)
        order = Order(
            symbol="BTC/USD",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=50000.0,  # at current price, triggers immediately
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED

    async def test_limit_order_rejected_without_price(self) -> None:
        p = await self._provider_with_price(50000.0)
        order = Order(
            symbol="BTC/USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=None,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "requires a price" in result.message


class TestStopOrder:
    """Stop order trigger logic."""

    async def _provider_with_price(
        self, close_price: float,
    ) -> PaperTradingProvider:
        p = PaperTradingProvider(initial_balance=10_000.0)
        await p.connect()
        p.inject_candles("ETH/USD", [
            Candle("ETH/USD", "1h", 3000.0, 3050.0, 2980.0, close_price, 500,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        return p

    async def test_buy_stop_at_price_triggered(self) -> None:
        p = await self._provider_with_price(3000.0)
        order = Order(
            symbol="ETH/USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=1,
            stop_price=3000.0,  # at current price, triggers immediately
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED

    async def test_buy_stop_above_price_pending(self) -> None:
        p = await self._provider_with_price(3000.0)
        order = Order(
            symbol="ETH/USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=1,
            stop_price=3050.0,  # above current, price hasn't risen yet
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.PENDING
        assert "Stop not triggered" in result.message

    async def test_sell_stop_at_price_triggered(self) -> None:
        p = await self._provider_with_price(3000.0)
        order = Order(
            symbol="ETH/USD",
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            quantity=1,
            stop_price=3000.0,  # at current price, triggers immediately
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.FILLED

    async def test_stop_order_rejected_without_stop_price(self) -> None:
        p = await self._provider_with_price(3000.0)
        order = Order(
            symbol="ETH/USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=1,
            stop_price=None,
            price=3050.0,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "requires a stop price" in result.message


class TestCancelOrder:
    """Order cancellation."""

    async def test_cancel_pending_limit(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        p.inject_candles("EUR/USD", [
            Candle("EUR/USD", "1h", 1.10, 1.11, 1.09, 1.105, 1000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=1000, price=1.08,
        )
        result = await p.place_order(order)
        assert result.status == OrderStatus.PENDING
        cancelled = await p.cancel_order(result.order_id)
        assert cancelled is True
        r2 = p.get_order_result(result.order_id)
        assert r2 is not None
        assert r2.status == OrderStatus.CANCELLED

    async def test_cancel_nonexistent(self) -> None:
        p = PaperTradingProvider()
        assert await p.cancel_order("no-such-order") is False


class TestPositionTracking:
    """Open position management and P&L."""

    async def _buy_provider(self) -> PaperTradingProvider:
        p = PaperTradingProvider(initial_balance=10_000.0)
        await p.connect()
        p.inject_candles("XAU/USD", [
            Candle("XAU/USD", "1h", 2500.0, 2510.0, 2490.0, 2505.0, 5000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        order = Order(
            symbol="XAU/USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
        )
        await p.place_order(order)
        return p

    async def test_buy_creates_position(self) -> None:
        p = await self._buy_provider()
        positions = await p.get_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.symbol == "XAU/USD"
        assert pos.side == OrderSide.BUY
        assert pos.quantity == 1
        assert pos.entry_price == 2505.0
        assert pos.unrealized_pnl == 0.0
        assert pos.leverage == 1

    async def test_sell_creates_position(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        p.inject_candles("XAU/USD", [
            Candle("XAU/USD", "1h", 2500.0, 2510.0, 2490.0, 2505.0, 5000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        order = Order(
            symbol="XAU/USD", side=OrderSide.SELL,
            order_type=OrderType.MARKET, quantity=1,
        )
        await p.place_order(order)
        positions = await p.get_positions()
        assert len(positions) == 1
        assert positions[0].side == OrderSide.SELL

    async def test_unrealized_pnl_updates_with_new_candle(self) -> None:
        p = await self._buy_provider()
        # Add a candle where price moved up
        p.inject_candles("XAU/USD", [
            Candle("XAU/USD", "1h", 2510.0, 2520.0, 2505.0, 2515.0, 6000,
                   timestamp=datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)),
        ])
        positions = await p.get_positions()
        assert len(positions) == 1
        # entry=2505, current=2515 → diff=10 → upnl=10*1=10
        assert positions[0].unrealized_pnl == pytest.approx(10.0)
        assert positions[0].current_price == 2515.0

    async def test_equity_reflects_unrealized_pnl(self) -> None:
        p = await self._buy_provider()
        balance_before = await p.get_balance()
        # entry=2505, balance after buy = 10000 - 2505 = 7495
        p.inject_candles("XAU/USD", [
            Candle("XAU/USD", "1h", 2510.0, 2520.0, 2505.0, 2520.0, 6000,
                   timestamp=datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)),
        ])
        positions = await p.get_positions()
        upnl = positions[0].unrealized_pnl  # 15.0
        assert p.equity == pytest.approx(balance_before + upnl)


class TestValidation:
    """Order validation edge cases."""

    async def test_zero_quantity_rejected(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        p.inject_candles("EUR/USD", [
            Candle("EUR/USD", "1h", 1.10, 1.11, 1.09, 1.105, 1000,
                   timestamp=datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ])
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=0,
        )
        with pytest.raises(ValueError, match="Invalid quantity"):
            await p.place_order(order)

    async def test_negative_quantity_rejected(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        order = Order(
            symbol="EUR/USD", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=-1,
        )
        with pytest.raises(ValueError, match="Invalid quantity"):
            await p.place_order(order)

    async def test_empty_symbol_rejected(self) -> None:
        p = PaperTradingProvider()
        await p.connect()
        order = Order(
            symbol="", side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1,
        )
        with pytest.raises(ValueError, match="Symbol is required"):
            await p.place_order(order)


class TestProviderRegistry:
    """ProviderRegistry integration."""

    async def test_register_and_retrieve(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        p = PaperTradingProvider(name="my_paper")
        registry.register(p)
        assert registry.count == 1
        retrieved = registry.get("my_paper")
        assert retrieved is p

    async def test_get_nonexistent_returns_none(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        assert registry.get("nope") is None

    async def test_unregister_removes_provider(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        registry.register(PaperTradingProvider(name="p1"))
        registry.unregister("p1")
        assert registry.count == 0

    async def test_list_returns_all(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        registry.register(PaperTradingProvider(name="p1"))
        registry.register(PaperTradingProvider(name="p2"))
        providers = registry.get_all()
        assert len(providers) == 2

    async def test_clear_removes_all(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        registry.register(PaperTradingProvider(name="p1"))
        registry.clear()
        assert registry.count == 0

    async def test_list_by_type(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        p1 = PaperTradingProvider(name="p1")
        registry.register(p1)
        forex_providers = registry.list_by_type("forex")
        assert len(forex_providers) == 1
        crypto_providers = registry.list_by_type("crypto")
        assert len(crypto_providers) == 0

    async def test_duplicate_name_overwrites(self) -> None:
        from trading_bot.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        p1 = PaperTradingProvider(name="same")
        p2 = PaperTradingProvider(name="same")
        registry.register(p1)
        registry.register(p2)
        assert registry.count == 1
        assert registry.get("same") is p2
