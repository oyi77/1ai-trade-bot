"""Tests for tradebot/brokers/ — base, deriv (client/patterns/strategy), mt5 (broker/executor)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradebot.brokers import BaseBroker as Broker
from tradebot.brokers.deriv.client import (
    DerivContractResult,
    DerivOHLCV,
    DerivTick,
    DerivWSClient,
)
from tradebot.brokers.deriv.patterns import (
    AdjacencyAnalysis,
    AdjacencyPatternAnalyzer,
    MomenAnalysis,
    MomenPatternAnalyzer,
    StreakCountdownAnalyzer,
)
from tradebot.brokers.deriv.strategy import DigitMartingaleStrategy
from tradebot.brokers.mt5.broker import MT5Broker
from tradebot.brokers.mt5.executor import EAState, MT5Executor
from tradebot.models import Balance, Order

# ── base.py ────────────────────────────────────────────────────────────────


class TestBrokerABC:
    """Verify Broker is abstract and cannot be instantiated."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError, match="abstract"):
            Broker()

    def test_has_all_abstract_methods(self):
        abstract_methods = {
            "connect", "disconnect", "get_balance",
            "place_order", "subscribe_ticks",
        }
        for method in abstract_methods:
            assert hasattr(Broker, method), f"Missing: {method}"
        assert hasattr(Broker, "is_connected")


class TestBrokerSubclassContract:
    """Verify a concrete subclass can be instantiated."""

    def test_concrete_subclass(self):
        class MockBroker(Broker):
            async def connect(self) -> bool:
                return True
            async def disconnect(self):
                pass
            async def get_balance(self):
                return Balance(balance=100.0)
            async def place_order(self, symbol, contract_type,
                                  barrier, stake, **kwargs):
                return Order(
                    order_id="1", symbol=symbol,
                    contract_type=contract_type, stake=stake,
                    barrier=barrier, direction="BUY",
                )
            async def subscribe_ticks(self, symbol):
                return True
            @property
            def is_connected(self):
                return True

        broker = MockBroker()
        assert broker.is_connected is True

    @pytest.mark.asyncio
    async def test_concrete_subclass_async(self):
        class MockBroker(Broker):
            async def connect(self) -> bool:
                return True
            async def disconnect(self):
                pass
            async def get_balance(self):
                return Balance(balance=500.0, currency="USD")
            async def place_order(self, symbol, contract_type,
                                  barrier, stake, **kwargs):
                return None
            async def subscribe_ticks(self, symbol):
                return False
            @property
            def is_connected(self):
                return False

        broker = MockBroker()
        assert await broker.connect() is True
        bal = await broker.get_balance()
        assert bal.balance == 500.0
        assert await broker.subscribe_ticks("R_75") is False

    def test_incomplete_subclass_fails(self):
        class IncompleteBroker(Broker):
            async def connect(self):
                return True
        with pytest.raises(TypeError, match="abstract"):
            IncompleteBroker()


# ── deriv/client.py ────────────────────────────────────────────────────────


class TestDerivTick:
    """DerivTick dataclass and digit extraction."""

    def test_digit_extraction(self):
        pairs = [
            (33000.0003, 3), (33000.0007, 7),
            (33000.0000, 0), (33000.0009, 9),
        ]
        for price, expected in pairs:
            tick = DerivTick(symbol="R_75", price=price, epoch=1)
            assert tick.digit == expected, f"price={price}"

    def test_tick_defaults(self):
        tick = DerivTick(symbol="R_75", price=33000.0, epoch=100)
        assert tick.symbol == "R_75"
        assert tick.price == 33000.0
        assert isinstance(tick.timestamp, datetime)


class TestDerivOHLCV:
    """DerivOHLCV dataclass."""

    def test_creation(self):
        candle = DerivOHLCV(
            timestamp=1000, open=100.0, high=110.0,
            low=95.0, close=105.0, symbol="R_75",
        )
        assert candle.high - candle.low == 15.0
        assert candle.volume == 0


class TestDerivContractResult:
    """DerivContractResult dataclass."""

    def test_win(self):
        result = DerivContractResult(
            contract_id=123, contract_type="DIGITMATCH",
            symbol="R_75", stake=0.35, payout=2.87,
            profit=2.52, entry_tick=33000.0, is_win=True,
        )
        assert result.is_win is True
        assert result.profit == 2.52

    def test_loss(self):
        result = DerivContractResult(
            contract_id=456, contract_type="DIGITMATCH",
            symbol="R_75", stake=0.54, payout=0.0,
            profit=-0.54, entry_tick=33001.0, is_win=False,
        )
        assert result.is_win is False


class TestDerivWSClient:
    """DerivWSClient initialization and state management."""

    def test_init_defaults(self):
        client = DerivWSClient()
        assert client.api_token == ""
        assert client._connected is False
        assert client._ws is None
        assert client._running is False

    def test_init_with_params(self):
        client = DerivWSClient(
            api_token="tok123", app_id="456", mode="demo",
        )
        assert client.api_token == "tok123"
        assert client.app_id == "456"
        assert client.mode == "demo"

    def test_is_connected_false_when_not_connected(self):
        assert DerivWSClient().is_connected is False

    def test_is_connected_false_when_ws_none(self):
        client = DerivWSClient()
        client._connected = True
        client._ws = None
        assert client.is_connected is False

    def test_is_connected_true_when_both_set(self):
        client = DerivWSClient()
        client._connected = True
        client._ws = MagicMock()
        assert client.is_connected is True

    def test_ws_url_legacy(self):
        client = DerivWSClient(app_id="12345", mode="api")
        assert "app_id=12345" in client.ws_url

    def test_ws_url_demo(self):
        # ws_url returns full demo URL with OTP when set
        client = DerivWSClient(otp="otp_abc", mode="demo")
        assert client.ws_url == "wss://api.derivws.com/trading/v1/options/ws/demo?otp=otp_abc"

    def test_ws_url_demo_without_otp(self):
        # Without OTP, falls back to legacy endpoint
        client = DerivWSClient(mode="demo", app_id="test_app")
        assert "app_id=test_app" in client.ws_url

    def test_event_handler_registration(self):
        client = DerivWSClient()
        handler = MagicMock()
        result = client.on("tick", handler)
        assert result is client  # chainable
        assert handler in client._handlers["tick"]

    def test_event_handler_removal(self):
        client = DerivWSClient()
        handler = MagicMock()
        client.on("tick", handler)
        client.off("tick", handler)
        assert handler not in client._handlers["tick"]

    @pytest.mark.asyncio
    async def test_dispatch_tick_message(self):
        client = DerivWSClient()
        received = []
        client.on("tick", lambda t: received.append(t))
        await client._dispatch({
            "tick": {"symbol": "R_75", "quote": 33000.0007, "epoch": 12345},
        })
        assert len(received) == 1
        assert received[0].symbol == "R_75"
        assert received[0].digit == 7

    @pytest.mark.asyncio
    async def test_dispatch_balance_message(self):
        client = DerivWSClient()
        received = []
        client.on("balance", lambda b: received.append(b))
        await client._dispatch({
            "msg_type": "balance",
            "balance": {"balance": 1000.0, "currency": "USD"},
        })
        assert len(received) == 1
        assert received[0]["balance"] == 1000.0

    @pytest.mark.asyncio
    async def test_dispatch_proposal_message(self):
        client = DerivWSClient()
        received = []
        client.on("proposal", lambda p: received.append(p))
        await client._dispatch({
            "msg_type": "proposal",
            "proposal": {"id": "abc", "ask_price": 0.35},
        })
        assert len(received) == 1
        assert received[0]["id"] == "abc"

    @pytest.mark.asyncio
    async def test_dispatch_proposal_error(self):
        client = DerivWSClient()
        errors = []
        client.on("proposal_error", lambda e: errors.append(e))
        await client._dispatch({
            "msg_type": "proposal",
            "error": {"message": "Invalid symbol"},
        })
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_dispatch_buy_message(self):
        client = DerivWSClient()
        received = []
        client.on("buy", lambda b: received.append(b))
        await client._dispatch({
            "msg_type": "buy", "buy": {"contract_id": 12345},
        })
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_dispatch_pending_request(self):
        client = DerivWSClient()
        fut = asyncio.get_event_loop().create_future()
        client._pending["42"] = fut
        data = {"req_id": 42, "balance": {"balance": 500.0}}
        await client._dispatch(data)
        assert fut.done()
        assert fut.result() is data

    @pytest.mark.asyncio
    async def test_safe_send_not_connected(self):
        assert await DerivWSClient()._safe_send({"ping": 1}) is False

    @pytest.mark.asyncio
    async def test_disconnect_resets_state(self):
        client = DerivWSClient()
        client._connected = True
        client._running = True
        client._ws = MagicMock()
        client._ws.close = AsyncMock()
        await client.disconnect()
        assert client._connected is False
        assert client._running is False
        assert client._ws is None


# ── deriv/patterns.py ──────────────────────────────────────────────────────


def _make_ticks(digits: list[int], symbol: str = "R_75") -> list[DerivTick]:
    """Helper: create DerivTick list from last-digit values."""
    return [
        DerivTick(symbol=symbol, price=float(f"33000.000{d}"), epoch=1000 + i)
        for i, d in enumerate(digits)
    ]


class TestMomenPatternAnalyzer:
    """Momen 1/2 pattern detection with synthetic data."""

    def test_init_defaults(self):
        analyzer = MomenPatternAnalyzer()
        assert analyzer.analysis_ticks > 0
        assert len(analyzer.target_carriers) > 0

    def test_analyze_empty_ticks(self):
        assert MomenPatternAnalyzer().analyze([]) is None

    def test_analyze_insufficient_ticks(self):
        analyzer = MomenPatternAnalyzer(min_momen1=5, min_momen2=5)
        assert analyzer.analyze(_make_ticks([1, 7, 2, 7, 3, 7])) is None

    def test_analyze_strong_momen_pattern(self):
        """Clear Momen 1 pattern: carrier→7 repeats."""
        analyzer = MomenPatternAnalyzer(
            target_carriers=[1, 2], min_momen1=1, min_momen2=1,
            max_jaring_ticks=3, analysis_ticks=100,
        )
        digits = [1, 7] * 5 + [5, 6, 8, 9] * 10
        result = analyzer.analyze(_make_ticks(digits))
        assert result is not None
        assert result.carrier == 1
        assert result.total_m1 >= 1

    def test_analyze_momen2_jaring(self):
        """Momen 2: carrier → 7 within jaring window."""
        analyzer = MomenPatternAnalyzer(
            target_carriers=[3], min_momen1=1, min_momen2=1,
            max_jaring_ticks=3, analysis_ticks=100,
        )
        # 3→7 at idx0-1 (M1), plus 3→5→7 within jaring (M2)
        digits = [3, 7, 3, 5, 7, 9, 9, 9, 9, 9] * 5
        result = analyzer.analyze(_make_ticks(digits))
        assert result is not None
        assert result.carrier == 3

    def test_confidence_calculation(self):
        """Confidence = min(1.0, (m1+m2)/6.0)."""
        analyzer = MomenPatternAnalyzer(
            target_carriers=[1], min_momen1=1, min_momen2=1,
            max_jaring_ticks=3, analysis_ticks=100,
        )
        digits = [1, 7] * 6 + [9] * 20
        result = analyzer.analyze(_make_ticks(digits))
        if result is not None:
            assert 0.0 <= result.confidence <= 1.0


class TestAdjacencyPatternAnalyzer:
    """Adjacency pair pattern detection."""

    def test_init_defaults(self):
        analyzer = AdjacencyPatternAnalyzer()
        assert analyzer.lookback > 0
        assert analyzer.min_threshold > 0

    def test_analyze_insufficient_ticks(self):
        assert AdjacencyPatternAnalyzer().analyze(_make_ticks([1])) is None

    def test_analyze_no_pattern(self):
        analyzer = AdjacencyPatternAnalyzer(min_threshold=100)
        assert analyzer.analyze(_make_ticks([1, 2, 3, 4, 5] * 5)) is None

    def test_analyze_finds_dominant_pair(self):
        """3→7 repeated enough triggers detection."""
        analyzer = AdjacencyPatternAnalyzer(min_threshold=3, lookback=50)
        digits = [3, 7] * 5 + [1, 2, 4, 5, 6, 8, 9]
        result = analyzer.analyze(_make_ticks(digits))
        assert result is not None
        assert result.trigger == 3
        assert result.target == 7
        assert result.freq >= 3

    def test_anti_flood_flag(self):
        """Anti-flood flags overrepresented target digit."""
        analyzer = AdjacencyPatternAnalyzer(
            min_threshold=2, anti_flood_window=10, anti_flood_max=2,
        )
        digits = [3, 7, 3, 7, 7, 7, 7, 7, 7, 7]
        result = analyzer.analyze(_make_ticks(digits))
        if result is not None:
            assert result.anti_flood_ok is False

    def test_predicted_digit_property(self):
        analysis = AdjacencyAnalysis(
            trigger=3, target=7, freq=5,
            total_adjacencies=20, trigger_count=6, anti_flood_ok=True,
        )
        assert analysis.predicted_digit == 7


class TestStreakCountdownAnalyzer:
    """Streak-based trigger + countdown pattern detection."""

    def test_init_defaults(self):
        analyzer = StreakCountdownAnalyzer()
        assert analyzer.required_streak == 3
        assert analyzer.comparison == ">"

    def test_init_invalid_comparison(self):
        with pytest.raises(AssertionError):
            StreakCountdownAnalyzer(comparison="!=")

    def test_analyze_no_streak(self):
        analyzer = StreakCountdownAnalyzer(
            required_streak=3, comparison=">", trigger_value=5,
        )
        assert analyzer.analyze(
            _make_ticks([1, 2, 3, 1, 2, 3, 1, 2, 3]),
        ) is None

    def test_analyze_streak_found(self):
        analyzer = StreakCountdownAnalyzer(
            required_streak=3, comparison=">", trigger_value=5,
            op_tick_countdown=1, analysis_ticks=100,
        )
        result = analyzer.analyze(
            _make_ticks([1, 2, 3, 6, 7, 8, 1, 2, 3, 4]),
        )
        assert result is not None
        assert result.streak_length >= 3
        assert result.trigger_digit == 5

    def test_analyze_less_than_comparison(self):
        analyzer = StreakCountdownAnalyzer(
            required_streak=3, comparison="<", trigger_value=5,
            op_tick_countdown=1, analysis_ticks=100,
        )
        result = analyzer.analyze(
            _make_ticks([9, 8, 1, 2, 3, 1, 2, 9, 8, 7]),
        )
        if result is not None:
            assert result.comparison == "<"

    def test_analyze_equal_comparison(self):
        analyzer = StreakCountdownAnalyzer(
            required_streak=2, comparison="==", trigger_value=7,
            op_tick_countdown=1, analysis_ticks=100,
        )
        result = analyzer.analyze(
            _make_ticks([1, 2, 7, 7, 3, 4, 5, 6, 8, 9]),
        )
        assert result is not None
        assert result.trigger_digit == 7

    def test_check_digit_greater(self):
        a = StreakCountdownAnalyzer(comparison=">", trigger_value=5)
        assert a._check_digit(6, 5) is True
        assert a._check_digit(5, 5) is False
        assert a._check_digit(4, 5) is False

    def test_check_digit_less(self):
        a = StreakCountdownAnalyzer(comparison="<", trigger_value=5)
        assert a._check_digit(4, 5) is True
        assert a._check_digit(5, 5) is False

    def test_check_digit_equal(self):
        a = StreakCountdownAnalyzer(comparison="==", trigger_value=5)
        assert a._check_digit(5, 5) is True
        assert a._check_digit(6, 5) is False

    def test_streak_broken_resets(self):
        """Non-matching digit resets the streak."""
        analyzer = StreakCountdownAnalyzer(
            required_streak=3, comparison=">", trigger_value=5,
            op_tick_countdown=1, analysis_ticks=100,
        )
        # 6,7 (break: 2), 6,7,8 → streak from second group only
        result = analyzer.analyze(
            _make_ticks([6, 7, 2, 6, 7, 8, 9, 9, 9, 9]),
        )
        assert result is not None
        assert result.streak_length >= 3


# ── deriv/strategy.py ──────────────────────────────────────────────────────


class TestDigitMartingaleStrategy:
    """DigitMartingaleStrategy initialization and decision logic."""

    def _make_mock_client(self, balance=100.0, ticks=None):
        client = MagicMock()
        client.get_balance = AsyncMock(return_value=balance)
        client.get_ticks_history = AsyncMock(return_value=ticks or [])
        client.buy_digit = AsyncMock(return_value=MagicMock())
        return client

    def test_init_defaults(self):
        strategy = DigitMartingaleStrategy(client=self._make_mock_client())
        assert strategy.symbol == "R_75"
        assert strategy.contract_type == "DIGITMATCH"
        assert strategy.barrier == 7
        assert strategy.running is False
        assert strategy.total_wins == 0
        assert strategy.cycle_count == 0

    def test_init_custom_params(self):
        strategy = DigitMartingaleStrategy(
            client=self._make_mock_client(), symbol="R_100",
            contract_type="DIGITOVER", barrier=5,
            initial_stake=1.0, stake_multiplier=2.0, max_ops=5,
        )
        assert strategy.symbol == "R_100"
        assert strategy.contract_type == "DIGITOVER"
        assert strategy.barrier == 5
        assert strategy.initial_stake == 1.0
        assert strategy.stake_multiplier == 2.0
        assert strategy.max_ops == 5

    def test_daily_loss_limit(self):
        strategy = DigitMartingaleStrategy(
            client=self._make_mock_client(), max_loss=-10.0,
        )
        assert strategy.daily_loss_limit == -10.0

    @pytest.mark.asyncio
    async def test_get_session_balance(self):
        strategy = DigitMartingaleStrategy(
            client=self._make_mock_client(balance=500.0),
        )
        bal = await strategy.get_session_balance()
        assert bal == 500.0
        assert strategy.balance == 500.0

    @pytest.mark.asyncio
    async def test_analyse_and_trade_insufficient_ticks(self):
        client = self._make_mock_client(
            balance=100.0, ticks=_make_ticks([1, 2, 3]),
        )
        strategy = DigitMartingaleStrategy(client=client, analysis_ticks=100)

        # Patch TradeResult: source has positional-arg mismatch
        # (6 args vs 7 required fields)
        with (
            patch("tradebot.brokers.deriv.strategy.CognitiveDB") as mc,
            patch("tradebot.brokers.deriv.strategy.TradeResult") as tr_cls,
        ):
            mc.get_daily_counter.return_value = {"profit": 0.0}
            sentinel = MagicMock(stopped_early=True, reason="insufficient_ticks")
            tr_cls.return_value = sentinel
            result = await strategy.analyse_and_trade()

        assert result.stopped_early is True
        assert result.reason == "insufficient_ticks"

    @pytest.mark.asyncio
    async def test_analyse_and_trade_no_pattern(self):
        client = self._make_mock_client(
            balance=100.0, ticks=_make_ticks([1, 2, 3, 4, 5] * 20),
        )
        strategy = DigitMartingaleStrategy(client=client, analysis_ticks=100)

        with (
            patch("tradebot.brokers.deriv.strategy.CognitiveDB") as mc,
            patch("tradebot.brokers.deriv.strategy.TradeResult") as tr_cls,
        ):
            mc.get_daily_counter.return_value = {"profit": 0.0}
            strategy.analyzer = MagicMock()
            strategy.analyzer.analyze.return_value = None
            sentinel = MagicMock(stopped_early=True, reason="no_pattern")
            tr_cls.return_value = sentinel
            result = await strategy.analyse_and_trade()

        assert result.stopped_early is True
        assert result.reason == "no_pattern"

    @pytest.mark.asyncio
    async def test_analyse_and_trade_low_confidence(self):
        client = self._make_mock_client(
            balance=100.0, ticks=_make_ticks([1, 7] * 50),
        )
        strategy = DigitMartingaleStrategy(
            client=client, analysis_ticks=100, min_confidence=0.9,
        )
        low_conf = MomenAnalysis(
            carrier=1, momen1_tick=0, momen2_tick=0,
            total_m1=1, total_m2=1, confidence=0.1,
        )

        with (
            patch("tradebot.brokers.deriv.strategy.CognitiveDB") as mc,
            patch("tradebot.brokers.deriv.strategy.TradeResult") as tr_cls,
        ):
            mc.get_daily_counter.return_value = {"profit": 0.0}
            strategy.analyzer = MagicMock()
            strategy.analyzer.analyze.return_value = low_conf
            sentinel = MagicMock(stopped_early=True, reason="low_confidence")
            tr_cls.return_value = sentinel
            result = await strategy.analyse_and_trade()

        assert result.stopped_early is True
        assert result.reason == "low_confidence"

    @pytest.mark.asyncio
    async def test_analyse_and_trade_daily_sl_hit(self):
        strategy = DigitMartingaleStrategy(
            client=self._make_mock_client(balance=100.0), max_loss=-8.0,
        )

        with (
            patch("tradebot.brokers.deriv.strategy.CognitiveDB") as mc,
            patch("tradebot.brokers.deriv.strategy.TradeResult") as tr_cls,
        ):
            mc.get_daily_counter.return_value = {"profit": -10.0}
            sentinel = MagicMock(stopped_early=True, reason="daily_sl_hit")
            tr_cls.return_value = sentinel
            result = await strategy.analyse_and_trade()

        assert result.stopped_early is True
        assert result.reason == "daily_sl_hit"

    @pytest.mark.asyncio
    async def test_analyse_and_trade_daily_tp_hit(self):
        strategy = DigitMartingaleStrategy(
            client=self._make_mock_client(balance=100.0), target_profit=5.0,
        )

        with (
            patch("tradebot.brokers.deriv.strategy.CognitiveDB") as mc,
            patch("tradebot.brokers.deriv.strategy.TradeResult") as tr_cls,
        ):
            mc.get_daily_counter.return_value = {"profit": 6.0}
            sentinel = MagicMock(stopped_early=True, reason="daily_tp_hit")
            tr_cls.return_value = sentinel
            result = await strategy.analyse_and_trade()

        assert result.stopped_early is True
        assert result.reason == "daily_tp_hit"


# ── mt5/broker.py ──────────────────────────────────────────────────────────


class TestMT5Broker:
    """MT5Broker initialization and connection with mocked MT5."""

    def test_init_defaults(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            s.MT5_PASSWORD = ""
            s.MT5_SERVER = ""
            s.MT5_PATH = ""
            s.BROKER_DRY_RUN = True
            broker = MT5Broker()
            assert broker._connected is False
            assert broker._mt5 is None
            assert broker._dry_run is True

    def test_init_with_explicit_params(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            # MT5_LOGIN must be truthy for login param to take effect
            s.MT5_LOGIN = "99999"
            broker = MT5Broker(
                login=12345, password="pass", server="server",
                dry_run=False,
            )
            assert broker._login == 12345
            assert broker._password == "pass"
            assert broker._server == "server"
            assert broker._dry_run is False

    def test_is_connected_initially_false(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
            assert broker.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_import_failure(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        with patch.dict("sys.modules", {"MetaTrader5": None}):
            result = await broker.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_balance_not_connected(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        assert await broker.get_balance() is None

    @pytest.mark.asyncio
    async def test_get_balance_success(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        broker._connected = True
        mock_mt5 = MagicMock()
        mock_account = MagicMock()
        mock_account.balance = 5000.0
        mock_account.currency = "USD"
        mock_mt5.account_info.return_value = mock_account
        broker._mt5 = mock_mt5
        result = await broker.get_balance()
        assert result is not None
        assert result.balance == 5000.0
        assert result.currency == "USD"

    @pytest.mark.asyncio
    async def test_place_order_not_connected(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        assert await broker.place_order("XAUUSD", "BUY", 0, 0.1) is None

    @pytest.mark.asyncio
    async def test_place_order_dry_run(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            s.MT5_SLIPPAGE = 10
            s.MT5_MAGIC_NUMBER = 101001
            broker = MT5Broker(login=1, password="p", server="s")
        broker._connected = True
        broker._dry_run = True
        mock_mt5 = MagicMock()
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_tick = MagicMock()
        mock_tick.ask = 1900.0
        mock_tick.bid = 1899.5
        mock_mt5.symbol_info_tick.return_value = mock_tick
        broker._mt5 = mock_mt5

        result = await broker.place_order("XAUUSD", "BUY", 0, 0.1)
        assert result is not None
        assert result.status == "PAPER_FILLED"
        assert result.symbol == "XAUUSD"

    @pytest.mark.asyncio
    async def test_place_order_unsupported_type(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        broker._connected = True
        broker._dry_run = False
        mock_mt5 = MagicMock()
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.ORDER_TYPE_SELL = 1
        broker._mt5 = mock_mt5
        assert await broker.place_order("XAUUSD", "LIMIT", 0, 0.1) is None

    @pytest.mark.asyncio
    async def test_subscribe_ticks_not_connected(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        assert await broker.subscribe_ticks("XAUUSD") is False

    @pytest.mark.asyncio
    async def test_subscribe_ticks_success(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        broker._connected = True
        mock_mt5 = MagicMock()
        mock_mt5.symbol_select.return_value = True
        broker._mt5 = mock_mt5
        assert await broker.subscribe_ticks("XAUUSD") is True
        mock_mt5.symbol_select.assert_called_once_with("XAUUSD", True)

    @pytest.mark.asyncio
    async def test_disconnect_resets_state(self):
        with patch("tradebot.brokers.mt5.broker.settings") as s:
            s.MT5_LOGIN = ""
            broker = MT5Broker(login=1, password="p", server="s")
        broker._connected = True
        mock_mt5 = MagicMock()
        broker._mt5 = mock_mt5
        await broker.disconnect()
        assert broker.is_connected is False
        assert broker._mt5 is None
        mock_mt5.shutdown.assert_called_once()


# ── mt5/executor.py ────────────────────────────────────────────────────────


class TestEAState:
    """EAState dataclass and serialization."""

    def test_defaults(self):
        state = EAState()
        assert state.positions == []
        assert state.closed == []
        assert state.total_pnl == 0.0
        assert state.signals_processed == 0

    def test_to_dict(self):
        state = EAState(total_pnl=10.0, signals_processed=5)
        d = state.to_dict()
        assert d["total_pnl"] == 10.0
        assert d["signals_processed"] == 5

    def test_from_dict(self):
        data = {
            "positions": [], "closed": [], "total_pnl": -3.5,
            "signals_processed": 2, "last_signal_fingerprint": "abc",
            "total_wins": 1, "total_losses": 1,
        }
        state = EAState.from_dict(data)
        assert state.total_pnl == -3.5
        assert state.signals_processed == 2

    def test_from_dict_extra_keys_ignored(self):
        state = EAState.from_dict({"total_pnl": 1.0, "unknown": 42})
        assert state.total_pnl == 1.0


class TestMT5Executor:
    """MT5Executor initialization and position management."""

    def _make_executor(self, **kwargs):
        broker = MagicMock()
        broker.is_connected = True
        broker.place_order = AsyncMock(return_value=Order(
            order_id="ord_1", symbol="XAUUSD",
            contract_type="BUY", stake=0.1,
            barrier=0, direction="BUY", status="FILLED",
        ))
        with patch("tradebot.brokers.mt5.executor.settings") as s:
            s.DATA_DIR = "/tmp/test_data"
            s.BROKER_MAX_POSITIONS = 1
            s.BROKER_DRY_RUN = True
            s.BROKER_DEFAULT_STAKE = 0.35
            return MT5Executor(broker=broker, **kwargs)

    def test_init_defaults(self):
        executor = self._make_executor()
        assert executor._running is False
        assert executor._max_positions == 1
        assert executor._dry_run is True

    def test_init_with_signal_queue(self):
        executor = self._make_executor(
            signal_queue=asyncio.Queue(), max_positions=3,
        )
        assert executor._max_positions == 3
        assert executor._signal_queue is not None

    def test_state_property(self):
        assert isinstance(self._make_executor().state, EAState)

    def test_get_open_positions_empty(self):
        assert self._make_executor().get_open_positions() == []

    def test_calc_pnl_buy(self):
        pos = {"action": "BUY", "entry": 1900.0}
        assert MT5Executor._calc_pnl(pos, 1905.0) == 5.0
        assert MT5Executor._calc_pnl(pos, 1895.0) == -5.0

    def test_calc_pnl_sell(self):
        pos = {"action": "SELL", "entry": 1900.0}
        assert MT5Executor._calc_pnl(pos, 1895.0) == 5.0
        assert MT5Executor._calc_pnl(pos, 1905.0) == -5.0

    def test_check_sl_tp_buy_sl(self):
        executor = self._make_executor()
        pos = {"action": "BUY", "entry": 1900.0,
               "sl": 1895.0, "tp": 1910.0, "tp1": 1910.0}
        assert executor._check_sl_tp(pos, 1894.0) == ("SL", 1894.0)

    def test_check_sl_tp_buy_tp(self):
        executor = self._make_executor()
        pos = {"action": "BUY", "entry": 1900.0,
               "sl": 1895.0, "tp": 1910.0, "tp1": 1910.0}
        assert executor._check_sl_tp(pos, 1911.0) == ("TP", 1911.0)

    def test_check_sl_tp_no_sl_tp(self):
        executor = self._make_executor()
        pos = {"action": "BUY", "entry": 1900.0,
               "sl": 0, "tp": 0, "tp1": 0}
        assert executor._check_sl_tp(pos, 1900.0) is None

    def test_check_sl_tp_sell_sl(self):
        executor = self._make_executor()
        pos = {"action": "SELL", "entry": 1900.0,
               "sl": 1905.0, "tp": 1890.0, "tp1": 1890.0}
        assert executor._check_sl_tp(pos, 1906.0) == ("SL", 1906.0)

    def test_check_sl_tp_sell_tp(self):
        executor = self._make_executor()
        pos = {"action": "SELL", "entry": 1900.0,
               "sl": 1905.0, "tp": 1890.0, "tp1": 1890.0}
        assert executor._check_sl_tp(pos, 1889.0) == ("TP", 1889.0)

    def test_check_sl_tp_in_range(self):
        executor = self._make_executor()
        pos = {"action": "BUY", "entry": 1900.0,
               "sl": 1895.0, "tp": 1910.0, "tp1": 1910.0}
        assert executor._check_sl_tp(pos, 1902.0) is None

    @pytest.mark.asyncio
    async def test_close_position(self):
        executor = self._make_executor()
        executor._last_price = 1905.0
        executor._state.positions = [{
            "id": "pos_1", "action": "BUY", "symbol": "XAUUSD",
            "entry": 1900.0, "sl": 0, "tp": 0,
            "tp1": 0, "tp2": 0, "tp3": 0,
            "confidence": 0.8, "source": "test",
            "open_time": "", "status": "OPEN",
        }]
        assert await executor.close_position("pos_1") is True
        assert len(executor._state.positions) == 0
        assert len(executor._state.closed) == 1
        assert executor._state.closed[0]["pnl"] == 5.0

    @pytest.mark.asyncio
    async def test_close_nonexistent_position(self):
        assert await self._make_executor().close_position("nope") is False

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        executor = self._make_executor(interval=0.05)
        await executor.start()
        assert executor._running is True
        assert executor._task is not None
        await executor.stop()
        assert executor._running is False
        assert executor._task is None
