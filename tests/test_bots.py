"""Tests for the bot framework — BaseBot ABC and bot importability."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Concrete test double for BaseBot
# ---------------------------------------------------------------------------
from tradebot.bots.base import BaseBot


class StubBot(BaseBot):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._register_commands()

    def _register_commands(self) -> None:
        self._command_handlers["ping"] = lambda args: "pong"
        self._command_handlers["echo"] = lambda args: " ".join(args) if args else ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_telegram_cls():
    """Patch TelegramService so BaseBot.__init__ never hits the real class."""
    with patch("tradebot.bots.base.TelegramService") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.send_message = AsyncMock(return_value=True)
        mock_instance.send_signal_alert = AsyncMock(return_value=True)
        mock_cls.return_value = mock_instance
        yield mock_cls, mock_instance


@pytest.fixture
def bot(mock_telegram_cls) -> StubBot:
    """Create a StubBot with mocked TelegramService."""
    return StubBot(bot_token="fake-token", chat_id="12345", name="testbot")


# ===================================================================
# Tests 1–11  —  BaseBot behaviour
# ===================================================================


class TestBaseBotAbstract:
    """BaseBot cannot be instantiated directly."""

    def test_instantiation_raises(self):
        with pytest.raises(TypeError):
            BaseBot(bot_token="t", chat_id="c")  # type: ignore[abstract]


class TestStubBotInit:
    """StubBot initialisation stores attributes correctly."""

    def test_name(self, bot):
        assert bot.name == "testbot"

    def test_bot_token(self, bot):
        assert bot.bot_token == "fake-token"

    def test_chat_id(self, bot):
        assert bot.chat_id == "12345"

    def test_commands_registered(self, bot):
        assert "ping" in bot._command_handlers
        assert "echo" in bot._command_handlers

    def test_initial_state(self, bot):
        assert bot.is_running is False


class TestStartStop:
    """start() / stop() lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running(self, bot):
        await bot.start()
        assert bot.is_running is True
        await bot.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, bot):
        await bot.start()
        await bot.stop()
        assert bot.is_running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self, bot):
        await bot.start()
        await bot.start()  # second start should be a no-op
        assert bot.is_running is True
        await bot.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_background_tasks(self, bot):
        async def dummy():
            await asyncio.sleep(100)

        await bot.start()
        task = bot._schedule_background(dummy())
        assert len(bot._background_tasks) == 1
        await bot.stop()
        assert task.cancelled()
        assert len(bot._background_tasks) == 0


class TestSendMessage:
    """send_message delegates to TelegramService."""

    @pytest.mark.asyncio
    async def test_delegates_to_telegram(self, bot, mock_telegram_cls):
        _, mock_instance = mock_telegram_cls
        result = await bot.send_message("hello")
        mock_instance.send_message.assert_awaited_once_with("hello")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_failure(self, bot, mock_telegram_cls):
        _, mock_instance = mock_telegram_cls
        mock_instance.send_message = AsyncMock(return_value=False)
        result = await bot.send_message("fail")
        assert result is False


class TestSendSignal:
    """send_signal formats and delegates to TelegramService."""

    @pytest.mark.asyncio
    async def test_delegates_with_formatting(self, bot, mock_telegram_cls):
        _, mock_instance = mock_telegram_cls
        result = await bot.send_signal(
            "EURUSD", "BUY", "1.1000", "1.0950", "1.1050",
        )
        mock_instance.send_signal_alert.assert_awaited_once()
        assert result is True


class TestThrottle:
    """_check_throttle rate-limits repeated requests."""

    def test_first_call_allowed(self, bot):
        assert bot._check_throttle(user_id=1, cooldown_sec=5) is True

    def test_immediate_second_call_blocked(self, bot):
        bot._check_throttle(user_id=1, cooldown_sec=9999)
        assert bot._check_throttle(user_id=1, cooldown_sec=9999) is False

    def test_different_users_independent(self, bot):
        bot._check_throttle(user_id=1, cooldown_sec=9999)
        assert bot._check_throttle(user_id=2, cooldown_sec=9999) is True

    def test_cooldown_expiry(self, bot):
        import time
        bot._user_last_interaction[99] = time.time() - 10
        with patch.object(type(bot), "_now", return_value=time.time()):
            assert bot._check_throttle(user_id=99, cooldown_sec=1) is True


class TestHandleCommand:
    """handle_command dispatches to registered handlers."""

    def test_dispatches_to_handler(self, bot):
        assert bot.handle_command("/ping", []) == "pong"

    def test_passes_args(self, bot):
        assert bot.handle_command("/echo", ["hello", "world"]) == "hello world"

    def test_unknown_command_returns_none(self, bot):
        assert bot.handle_command("/unknown", []) is None

    def test_strips_slash(self, bot):
        assert bot.handle_command("/ping", []) == "pong"


class TestIsRunning:
    """is_running property reflects internal _running state."""

    def test_initially_false(self, bot):
        assert bot.is_running is False

    @pytest.mark.asyncio
    async def test_true_after_start(self, bot):
        await bot.start()
        assert bot.is_running is True
        await bot.stop()

    @pytest.mark.asyncio
    async def test_false_after_stop(self, bot):
        await bot.start()
        await bot.stop()
        assert bot.is_running is False


# ===================================================================
# Tests 12–14  —  Bot importability
# ===================================================================


class TestImports:
    """Concrete bot classes are importable from their packages."""

    def test_vilona_bot_importable(self):
        from tradebot.bots.vilona import VilonaBot
        assert VilonaBot is not None

    def test_stockity_bot_importable(self):
        from tradebot.bots.stockity import StockityBot
        assert StockityBot is not None

    def test_subscription_trading_bot_importable(self):
        from tradebot.bots.subscription import SubscriptionTradingBot
        assert SubscriptionTradingBot is not None


# ===================================================================
# Tests 15+  —  VilonaBot command registration & handlers
# ===================================================================

import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from tradebot.bots.vilona.handler import (  # noqa: E402
    WIB,
    VilonaBot,
    extract_json,
    is_weekend,
    killzone_active,
    normalize_symbol,
    resolve_yahoo_symbol,
    session_label,
    weekend_status_text,
    wib_fmt,
    wib_now,
)
from tradebot.bots.vilona.signal_bridge import (  # noqa: E402
    RATE_COUNTERS,
    VilonaSignalBridge,
    check_rate_limit,
    gen_id,
    validate_key,
)

# ---------------------------------------------------------------------------
# Fixtures for VilonaBot tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_telegram_service(monkeypatch):
    """Patch TelegramService globally for VilonaBot tests so no real API calls."""
    mock_cls = MagicMock()
    mock_instance = MagicMock()
    mock_instance.send_message = AsyncMock(return_value=True)
    mock_instance.send_signal_alert = AsyncMock(return_value=True)
    mock_cls.return_value = mock_instance
    monkeypatch.setattr(
        "tradebot.bots.base.TelegramService", mock_cls,
    )
    return mock_cls, mock_instance


@pytest.fixture
def vilona_bot():
    """Create a VilonaBot with mocked TelegramService and no real engines."""
    with patch.dict("os.environ", {}, clear=False):
        bot = VilonaBot(name="vilona-test")
    # Force no market data and no engines to isolate command logic
    bot._market_data = None
    bot._engines = {k: False for k in bot._engines}
    # Register commands (normally done by start())
    bot._register_commands()
    return bot


# ===================================================================
# Test class: VilonaBot command registration
# ===================================================================


class TestVilonaCommandRegistration:
    """All 24 commands are registered and callable."""

    EXPECTED_COMMANDS = [
        "start", "help", "price", "analyze", "status",
        "subscribe", "autosync", "donate", "genkey", "listkeys",
        "mykey", "data", "killzone", "bridge_status",
        "history", "recap", "winrate", "mapping",
        "signal", "mtf", "engines", "dashboard",
        "restart_bot", "activate",
    ]

    def test_all_commands_registered(self, vilona_bot):
        """Every expected command has a handler entry."""
        for cmd in self.EXPECTED_COMMANDS:
            assert cmd in vilona_bot._command_handlers, f"Missing: {cmd}"

    def test_command_count(self, vilona_bot):
        """Exactly 24 commands registered."""
        assert len(vilona_bot._command_handlers) == 24

    def test_all_handlers_callable(self, vilona_bot):
        """Each registered handler is callable (async or sync)."""
        for cmd, handler in vilona_bot._command_handlers.items():
            assert callable(handler), f"Handler for '{cmd}' is not callable"


# ===================================================================
# Test class: Command handler returns (mocked market data + AI)
# ===================================================================


class TestVilonaCommandHandlers:
    """Verify return values of key command handlers."""

    @pytest.mark.asyncio
    async def test_cmd_start_welcome(self, vilona_bot):
        result = await vilona_bot._cmd_start([])
        assert "REVOLUSI TRADING" in result
        assert "/signal" in result
        assert "/dashboard" in result

    @pytest.mark.asyncio
    async def test_cmd_help_categories(self, vilona_bot):
        result = await vilona_bot._cmd_help([])
        assert "COMMAND CENTER" in result
        assert "/signal" in result
        assert "/analyze" in result
        assert "/killzone" in result
        assert "/dashboard" in result
        assert "AI SIGNAL SYSTEM" in result
        assert "PILAR UTAMA" in result
        assert "TRADING TOOLS" in result
        assert "POWER TOOLS" in result

    @pytest.mark.asyncio
    async def test_cmd_price_no_market_data(self, vilona_bot):
        """With no market data layer, _cmd_price returns unavailable error."""
        result = await vilona_bot._cmd_price(["gold"])
        assert "unavailable" in result.lower()

    @pytest.mark.asyncio
    async def test_cmd_data_no_market_data(self, vilona_bot):
        """With no market data, _cmd_data lists N/A for each asset."""
        result = await vilona_bot._cmd_data([])
        assert "Market Overview" in result
        assert "N/A" in result

    @pytest.mark.asyncio
    async def test_cmd_killzone_returns_session(self, vilona_bot):
        result = await vilona_bot._cmd_killzone([])
        assert "Session:" in result
        assert "London KZ:" in result
        assert "NY KZ:" in result

    @pytest.mark.asyncio
    async def test_cmd_status_returns_info(self, vilona_bot):
        result = await vilona_bot._cmd_status([])
        assert "VILONA BOT STATUS" in result
        assert "Bot:" in result
        assert "Market Data:" in result
        assert "Engines:" in result

    @pytest.mark.asyncio
    async def test_cmd_subscribe_returns_text(self, vilona_bot):
        result = await vilona_bot._cmd_subscribe([])
        assert "Vilona Trade FX" in result
        assert "GRATIS" in result or "donate" in result.lower()

    @pytest.mark.asyncio
    async def test_cmd_donate_returns_text(self, vilona_bot):
        result = await vilona_bot._cmd_donate([])
        assert "Dukung Server" in result
        assert "Rp" in result

    @pytest.mark.asyncio
    async def test_cmd_dashboard_returns_link(self, vilona_bot):
        result = await vilona_bot._cmd_dashboard([])
        assert "DASHBOARD" in result
        assert "phantomfx.aitradepulse.com" in result

    @pytest.mark.asyncio
    async def test_cmd_engines_returns_info(self, vilona_bot):
        result = await vilona_bot._cmd_engines([])
        assert "ENGINE" in result.upper() or "engine" in result.lower()

    @pytest.mark.asyncio
    async def test_cmd_autosync_toggle(self, vilona_bot):
        """Autosync toggles on and off for a given chat_id."""
        cid = "999"
        on_result = await vilona_bot._cmd_autosync([], chat_id=cid)
        assert "diaktifkan" in on_result
        off_result = await vilona_bot._cmd_autosync([], chat_id=cid)
        assert "dimatikan" in off_result


# ===================================================================
# Test class: Module-level helpers
# ===================================================================


class TestModuleHelpers:
    """Test standalone helper functions from the handler module."""

    def test_wib_now_timezone(self):
        """wib_now returns a timezone-aware datetime with UTC+7 offset."""
        now = wib_now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(hours=7)

    def test_wib_fmt_returns_string(self):
        """wib_fmt returns a formatted date/time string."""
        result = wib_fmt()
        assert isinstance(result, str)
        assert "WIB" in result
        assert "/" in result
        assert ":" in result

    def test_wib_fmt_custom_datetime(self):
        """wib_fmt accepts a custom datetime argument."""
        dt = datetime(2025, 1, 15, 10, 30, tzinfo=WIB)
        result = wib_fmt(dt)
        assert "15/01" in result
        assert "10:30" in result

    def test_session_label_hours(self):
        """session_label returns correct session name for various hours."""
        assert session_label(4) == "Asia"
        assert session_label(10) == "Asia+London"
        assert session_label(16) == "London"
        assert session_label(20) == "London+NY"
        assert session_label(1) == "NY"

    def test_killzone_active_returns_tuple(self):
        """killzone_active returns (london_kz, ny_kz) booleans."""
        lkz, nykz = killzone_active(15)
        assert isinstance(lkz, bool)
        assert isinstance(nykz, bool)
        assert lkz is True   # 15 is in London KZ window 14-17
        assert nykz is False

    def test_killzone_active_ny(self):
        """NY killzone is active 19-22 WIB."""
        lkz, nykz = killzone_active(20)
        assert lkz is False
        assert nykz is True

    def test_killzone_active_neither(self):
        """Outside both killzones."""
        lkz, nykz = killzone_active(5)
        assert lkz is False
        assert nykz is False

    def test_normalize_symbol_strips_suffixes(self):
        """normalize_symbol removes broker suffixes like .m, -, #."""
        assert normalize_symbol("XAUUSD.m") == "xauusd"
        assert normalize_symbol("EURUSD-c") == "eurusd"
        assert normalize_symbol("BTCUSD#raw") == "btcusd"
        assert normalize_symbol("GBPUSD_raw") == "gbpusd"
        assert normalize_symbol("gold") == "gold"

    def test_resolve_yahoo_symbol_known(self):
        """resolve_yahoo_symbol maps known pairs to Yahoo symbols."""
        assert resolve_yahoo_symbol("gold") == "GC=F"
        assert resolve_yahoo_symbol("xauusd") == "GC=F"
        assert resolve_yahoo_symbol("btcusd") == "BTC-USD"
        assert resolve_yahoo_symbol("ethusd") == "ETH-USD"
        assert resolve_yahoo_symbol("eurusd") == "EURUSD=X"
        assert resolve_yahoo_symbol("aapl") == "AAPL"
        assert resolve_yahoo_symbol("bbca") == "BBCA.JK"

    def test_resolve_yahoo_symbol_unknown(self):
        """Unknown pair gets uppercased as fallback."""
        assert resolve_yahoo_symbol("xyz") == "XYZ"

    def test_is_weekend_returns_bool(self):
        """is_weekend returns a boolean."""
        result = is_weekend()
        assert isinstance(result, bool)

    def test_weekend_status_text_returns_string(self):
        """weekend_status_text always returns a string."""
        result = weekend_status_text()
        assert isinstance(result, str)

    def test_weekend_status_text_during_weekend(self):
        """During weekend, status text is non-empty."""
        fake_sat = datetime(2025, 1, 4, 12, 0, tzinfo=WIB)  # Saturday
        with patch("tradebot.bots.vilona.handler.wib_now", return_value=fake_sat):
            assert is_weekend() is True
            text = weekend_status_text()
            assert "WEEKEND" in text

    def test_weekend_status_text_during_weekday(self):
        """During weekday business hours, status text is empty."""
        fake_wed = datetime(2025, 1, 8, 14, 0, tzinfo=WIB)  # Wednesday 14:00
        with patch("tradebot.bots.vilona.handler.wib_now", return_value=fake_wed):
            assert is_weekend() is False
            text = weekend_status_text()
            assert text == ""

    def test_extract_json_valid(self):
        """extract_json parses valid JSON from AI output."""
        content = 'Some text before {"action": "BUY", "confidence": 0.85} after'
        result = extract_json(content)
        assert result is not None
        assert result["action"] == "BUY"
        assert result["confidence"] == 0.85

    def test_extract_json_markdown_wrapped(self):
        """extract_json strips markdown fences."""
        content = '```json\n{"action": "SELL", "entry": 2650.0}\n```'
        result = extract_json(content)
        assert result is not None
        assert result["action"] == "SELL"

    def test_extract_json_no_json(self):
        """extract_json returns None when no JSON found."""
        result = extract_json("Just plain text with no JSON at all.")
        assert result is None

    def test_extract_json_malformed(self):
        """extract_json returns None for malformed JSON."""
        result = extract_json("Broken { json: not valid }")
        assert result is None

    def test_extract_json_nested(self):
        """extract_json handles nested objects."""
        content = '{"outer": {"inner": 42}, "list": [1, 2, 3]}'
        result = extract_json(content)
        assert result is not None
        assert result["outer"]["inner"] == 42
        assert result["list"] == [1, 2, 3]


# ===================================================================
# Test class: Signal bridge module-level functions
# ===================================================================


class TestSignalBridgeHelpers:
    """Test gen_id, validate_key, check_rate_limit, VilonaSignalBridge."""

    def test_gen_id_unique(self):
        """gen_id returns unique IDs with vtfx_ prefix."""
        id1 = gen_id()
        id2 = gen_id()
        assert id1 != id2
        assert id1.startswith("vtfx_")
        assert id2.startswith("vtfx_")

    def test_gen_id_contains_counter(self):
        """gen_id includes a monotonically increasing counter."""
        ids = [gen_id() for _ in range(5)]
        counters = [int(id_.rsplit("_", 1)[-1]) for id_ in ids]
        assert counters == sorted(counters)
        assert len(set(counters)) == 5

    def test_validate_key_empty_string(self):
        """Empty key returns (False, None)."""
        valid, info = validate_key("")
        assert valid is False
        assert info is None

    def test_validate_key_none_like(self):
        """Falsy key returns (False, None)."""
        valid, info = validate_key("")
        assert valid is False

    @patch("tradebot.bots.vilona.signal_bridge.load_keys")
    def test_validate_key_unknown_key(self, mock_load):
        """Unknown key returns (False, None)."""
        mock_load.return_value = {
            "keys": {"real-key": {"active": True, "tier": "pro"}},
            "tiers": {"pro": {"max_layers": 5}},
        }
        valid, info = validate_key("unknown-key")
        assert valid is False
        assert info is None

    @patch("tradebot.bots.vilona.signal_bridge.load_keys")
    def test_validate_key_inactive(self, mock_load):
        """Inactive key returns (False, None)."""
        mock_load.return_value = {
            "keys": {"expired": {"active": False, "tier": "starter"}},
            "tiers": {},
        }
        valid, info = validate_key("expired")
        assert valid is False

    @patch("tradebot.bots.vilona.signal_bridge.load_keys")
    def test_validate_key_valid(self, mock_load):
        """Active key returns (True, tier_info)."""
        tier_info = {"max_layers": 5, "features": ["all"]}
        mock_load.return_value = {
            "keys": {"good-key": {"active": True, "tier": "pro"}},
            "tiers": {"pro": tier_info},
        }
        valid, info = validate_key("good-key")
        assert valid is True
        assert info == tier_info

    @patch("tradebot.bots.vilona.signal_bridge.load_keys")
    def test_check_rate_limit_no_history(self, mock_load):
        """With no prior requests, check_rate_limit returns True."""
        mock_load.return_value = {
            "keys": {"key1": {"rate_limit": 10, "rate_window_seconds": 86400}},
            "tiers": {},
        }
        RATE_COUNTERS.pop("key1", None)
        assert check_rate_limit("key1") is True

    @patch("tradebot.bots.vilona.signal_bridge.load_keys")
    def test_check_rate_limit_exceeded(self, mock_load):
        """Exceeding rate limit returns False."""
        mock_load.return_value = {
            "keys": {"limited": {"rate_limit": 2, "rate_window_seconds": 86400}},
            "tiers": {},
        }
        RATE_COUNTERS["limited"] = [time.time(), time.time()]
        assert check_rate_limit("limited") is False

    @patch("tradebot.bots.vilona.signal_bridge.load_keys")
    def test_check_rate_limit_unlimited(self, mock_load):
        """rate_limit=0 means unlimited — always True."""
        mock_load.return_value = {
            "keys": {"vip": {"rate_limit": 0}},
            "tiers": {},
        }
        RATE_COUNTERS["vip"] = [time.time() for _ in range(999)]
        assert check_rate_limit("vip") is True

    def test_post_signal_unreachable_urls(self):
        """post_signal returns False when all bridge URLs are unreachable."""
        bridge = VilonaSignalBridge(bridge_urls=["http://127.0.0.1:1"])
        sig = {"action": "BUY", "entry": 2650.0, "sl": 2640.0, "tp": 2670.0}
        result = bridge.post_signal(sig, price=2650.0)
        assert result is False
    def test_post_signal_default_urls_fallback(self):
        """Passing None for bridge_urls uses default production URLs."""
        bridge = VilonaSignalBridge()
        assert len(bridge.bridge_urls) == 2
        assert "localhost:8765" in bridge.bridge_urls[1]
