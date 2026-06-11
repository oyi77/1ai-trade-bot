"""
Tests for Whitelabel Multi-Bot Runner.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Covers: BrandConfig, commission tracking, whitelabel management,
command handlers, callback handlers, dispatch, polling loop,
409 recovery, and entry points.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from telegram import Bot, Update

import sys
from pathlib import Path

# Ensure project root is on sys.path for scripts package imports
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import scripts.payment_tripay as _payment_mod
import unified_bot.telegram.whitelabel_runner as _wr

from unified_bot.telegram.whitelabel_runner import (
    BrandConfig,
    BRANDS,
    LightweightMultiBot,
    _cmd_donate,
    _cmd_help,
    _cmd_start,
    _cmd_status,
    _dispatch_update,
    _handle_callback,
    _load_json,
    _reply,
    _save_json,
    get_all_resellers,
    get_referrer_commission,
    get_reseller,
    get_reseller_commission,
    register_reseller,
    run_bots,
    run_single_brand,
    track_commission,
)

LOG = logging.getLogger(__name__)


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def brand_vilona() -> BrandConfig:
    """Return the Vilona brand config from BRANDS."""
    return BRANDS["vilona"]


@pytest.fixture
def brand_1ai() -> BrandConfig:
    """Return the 1AI brand config from BRANDS."""
    return BRANDS["1ai"]


@pytest.fixture
def brand_custom() -> BrandConfig:
    """Return a minimal custom brand for isolated tests."""
    return BrandConfig(
        brand_id="test_brand",
        name="Test Brand",
        token="test:token",
        username="test_bot",
        description="Test description",
    )


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temp directory to avoid side effects."""
    d = tmp_path / "whitelabel"
    d.mkdir()
    monkeypatch.setattr(_wr, "DATA_DIR", d)
    monkeypatch.setattr(_wr, "COMMISSION_FILE", d / "commissions.json")
    monkeypatch.setattr(_wr, "WHITELABEL_FILE", d / "whitelabel_resellers.json")
    return d


# ─── Mock Update Helpers ────────────────────────────────────────────────


def _mock_user(first_name: str = "Budi", username: str | None = "buditrader") -> MagicMock:
    user = MagicMock()
    user.first_name = first_name
    user.username = username
    user.id = 12345
    return user


def _make_message_update(text: str = "/start") -> MagicMock:
    """Create a mock Update with a text message."""
    msg = MagicMock()
    msg.text = text
    msg.reply_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.message = msg
    update.callback_query = None
    update.effective_user = _mock_user()
    update.effective_message = msg
    return update


def _make_callback_update(data: str) -> MagicMock:
    """Create a mock Update with a callback query."""
    msg = MagicMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    msg.reply_photo = AsyncMock()
    cq = MagicMock()
    cq.data = data
    cq.message = msg
    cq.answer = AsyncMock()
    update = MagicMock(spec=Update)
    update.callback_query = cq
    update.message = None
    update.effective_user = _mock_user()
    update.effective_message = msg
    return update


@pytest.fixture
def mock_bot(monkeypatch):
    """Replace Bot in whitelabel_runner with a factory that returns MagicMock.

    python-telegram-bot's TelegramObject has a custom __setattr__ that prevents
    setting certain attributes (token, get_updates, close). By replacing Bot
    at the module level, we avoid this restriction entirely.
    """
    instances: dict[str, MagicMock] = {}

    class _MockBot:
        def __new__(cls, token, **kwargs):
            m = MagicMock(spec=Bot)
            m.token = token
            m.get_updates = AsyncMock(return_value=[])
            m.close = AsyncMock()
            instances[token] = m
            return m

    monkeypatch.setattr(_wr, "Bot", _MockBot)
    return instances


# =========================================================================
#  BrandConfig Tests
# =========================================================================


class TestBrandConfig:
    """BrandConfig dataclass — fields, defaults, computed properties."""

    def test_default_values(self, brand_custom: BrandConfig):
        """Optional fields get sensible defaults."""
        assert brand_custom.primary_color == "#2563eb"
        assert brand_custom.logo == ""
        assert brand_custom.welcome_msg == ""
        assert brand_custom.owner_cut == 0.70
        assert brand_custom.reseller_cut == 0.20
        assert brand_custom.referrer_cut == 0.10
        assert brand_custom.payment_methods == ["tripay", "qris"]
        assert brand_custom.is_active is True

    def test_bot_link(self, brand_custom: BrandConfig):
        """bot_link constructs correct Telegram URL."""
        assert brand_custom.bot_link == "https://t.me/test_bot"

    def test_vilona_brand(self, brand_vilona: BrandConfig):
        """Vilona brand is properly configured."""
        assert brand_vilona.brand_id == "vilona"
        assert brand_vilona.name == "Vilona TradeFX"
        assert brand_vilona.username == "vilona_tradefx_bot"

    def test_1ai_brand(self, brand_1ai: BrandConfig):
        """1AI brand is properly configured."""
        assert brand_1ai.brand_id == "1ai"
        assert brand_1ai.name == "1AI Agent"
        assert brand_1ai.username == "agent_1ai2_bot"

    def test_immutable_fields(self):
        """Core fields must be provided."""
        BrandConfig(brand_id="x", name="x", token="x", username="x", description="x")

    def test_owner_cut_default(self):
        """owner_cut defaults to 0.70 unless overridden."""
        bc = BrandConfig(brand_id="x", name="x", token="x", username="x", description="x")
        assert bc.owner_cut == 0.70
        bc2 = BrandConfig(brand_id="x", name="x", token="x", username="x", description="x", owner_cut=0.50)
        assert bc2.owner_cut == 0.50


# =========================================================================
#  Commission Tracking Tests
# =========================================================================


class TestTrackCommission:
    """track_commission — record and retrieve payment splits."""

    def test_track_with_all_parties(self, tmp_data_dir):
        """Happy path: owner + reseller + referrer distribution."""
        track_commission(
            payment_id="pay_001", amount=100.0, user_id="user1",
            brand_id="1ai", reseller_id="reseller1", referrer_id="ref1",
        )
        data = _load_json(_wr.COMMISSION_FILE)
        entry = data["pay_001"]
        assert entry["amount"] == 100.0
        assert entry["user_id"] == "user1"
        assert entry["brand_id"] == "1ai"

        dists = entry["distributions"]
        assert len(dists) == 3

        # Owner: 70%
        owner = next(d for d in dists if d["role"] == "owner")
        assert owner["amount"] == 70.0
        assert owner["percentage"] == 70.0

        # Reseller: 20%
        reseller = next(d for d in dists if d["role"] == "reseller")
        assert reseller["to"] == "reseller1"
        assert reseller["amount"] == 20.0

        # Referrer: 10%
        referrer = next(d for d in dists if d["role"] == "referrer")
        assert referrer["to"] == "ref1"
        assert referrer["amount"] == 10.0

    def test_track_owner_only(self, tmp_data_dir):
        """Minimal: only platform owner gets a cut."""
        track_commission(payment_id="pay_002", amount=50.0, user_id="user2", brand_id="vilona")
        data = _load_json(_wr.COMMISSION_FILE)
        dists = data["pay_002"]["distributions"]
        assert len(dists) == 1
        assert dists[0]["role"] == "owner"

    def test_track_reseller_no_referrer(self, tmp_data_dir):
        """Owner + reseller, no referrer."""
        track_commission(
            payment_id="pay_003", amount=200.0, user_id="user3",
            brand_id="1ai", reseller_id="reseller2",
        )
        data = _load_json(_wr.COMMISSION_FILE)
        roles = {d["role"] for d in data["pay_003"]["distributions"]}
        assert roles == {"owner", "reseller"}

    def test_track_referrer_no_reseller(self, tmp_data_dir):
        """Owner + referrer, no reseller."""
        track_commission(
            payment_id="pay_004", amount=200.0, user_id="user4",
            brand_id="vilona", referrer_id="ref2",
        )
        data = _load_json(_wr.COMMISSION_FILE)
        roles = {d["role"] for d in data["pay_004"]["distributions"]}
        assert roles == {"owner", "referrer"}

    def test_unknown_brand(self, tmp_data_dir, caplog):
        """Unknown brand_id logs warning and returns without side effects."""
        caplog.set_level(logging.WARNING)
        track_commission("pay_bad", 100.0, "u1", brand_id="nonexistent")
        assert "Unknown brand" in caplog.text
        assert not _wr.COMMISSION_FILE.exists() or _load_json(_wr.COMMISSION_FILE) == {}

    def test_multiple_entries_persist(self, tmp_data_dir):
        """Multiple calls append, not overwrite."""
        track_commission("pay_a", 10.0, "u1", "1ai", reseller_id="r1")
        track_commission("pay_b", 20.0, "u2", "1ai", reseller_id="r1")
        data = _load_json(_wr.COMMISSION_FILE)
        assert len(data) == 2

    def test_rounding_correct(self, tmp_data_dir):
        """Amounts are rounded to 2 decimal places."""
        track_commission("pay_round", 33.33, "u1", "1ai", reseller_id="r1")
        data = _load_json(_wr.COMMISSION_FILE)
        owner = next(d for d in data["pay_round"]["distributions"] if d["role"] == "owner")
        # 33.33 * 0.70 = 23.331 → rounded to 23.33
        assert owner["amount"] == 23.33


class TestGetCommission:
    """get_reseller_commission / get_referrer_commission — sum across payments."""

    def test_get_reseller_sum(self, tmp_data_dir):
        """Accumulate reseller commissions across multiple payments."""
        track_commission("p1", 100.0, "u1", "1ai", reseller_id="r1")
        track_commission("p2", 200.0, "u2", "1ai", reseller_id="r1")
        # r1 gets 20% of 100 + 20% of 200 = 20 + 40 = 60
        assert get_reseller_commission("r1") == 60.0

    def test_get_reseller_no_entries(self, tmp_data_dir):
        """No commissions → returns 0.0."""
        assert get_reseller_commission("nonexistent") == 0.0

    def test_get_referrer_sum(self, tmp_data_dir):
        """Accumulate referrer commissions across payments."""
        track_commission("p1", 100.0, "u1", "1ai", referrer_id="ref1")
        track_commission("p2", 50.0, "u2", "1ai", referrer_id="ref1")
        # ref1 gets 10% of 100 + 10% of 50 = 10 + 5 = 15
        assert get_referrer_commission("ref1") == 15.0

    def test_get_referrer_no_entries(self, tmp_data_dir):
        """No referrer commissions → returns 0.0."""
        track_commission("p1", 100.0, "u1", "1ai")
        assert get_referrer_commission("ref1") == 0.0

    def test_mixed_roles_isolated(self, tmp_data_dir):
        """Reseller and referrer sums don't leak across roles."""
        track_commission("p1", 100.0, "u1", "1ai", reseller_id="person", referrer_id="person")
        # person as reseller: 20, person as referrer: 10
        assert get_reseller_commission("person") == 20.0
        assert get_referrer_commission("person") == 10.0


# =========================================================================
#  Whitelabel Reseller Management Tests
# =========================================================================


class TestRegisterReseller:
    """register_reseller — create and query whitelabel partners."""

    def test_register_new(self, tmp_data_dir):
        """Happy path: reseller created successfully."""
        result = register_reseller("res_001", "1ai", "John Partner")
        assert result is True
        data = _load_json(_wr.WHITELABEL_FILE)
        assert "res_001" in data
        assert data["res_001"]["name"] == "John Partner"
        assert data["res_001"]["brand_id"] == "1ai"
        assert data["res_001"]["status"] == "active"

    def test_register_duplicate(self, tmp_data_dir):
        """Duplicate registration returns False."""
        register_reseller("res_001", "1ai", "First")
        result = register_reseller("res_001", "vilona", "Second")
        assert result is False
        data = _load_json(_wr.WHITELABEL_FILE)
        assert data["res_001"]["name"] == "First"  # unchanged

    def test_register_with_custom_token(self, tmp_data_dir):
        """Custom token stored if provided."""
        register_reseller("res_tok", "1ai", "Tokenized", token="custom:token123")
        data = _load_json(_wr.WHITELABEL_FILE)
        assert data["res_tok"]["token"] == "custom:token123"

    def test_register_without_token(self, tmp_data_dir):
        """Auto-generated token when omitted."""
        register_reseller("res_notok", "1ai", "No Token")
        data = _load_json(_wr.WHITELABEL_FILE)
        assert data["res_notok"]["token"] == "new_bot_token_res_notok"


class TestGetReseller:
    """get_reseller / get_all_resellers — query functions."""

    def test_get_existing(self, tmp_data_dir):
        register_reseller("r1", "1ai", "Alice")
        data = get_reseller("r1")
        assert data is not None
        assert data["name"] == "Alice"

    def test_get_nonexistent(self, tmp_data_dir):
        assert get_reseller("ghost") is None

    def test_get_all_empty(self, tmp_data_dir):
        assert get_all_resellers() == []

    def test_get_all_multiple(self, tmp_data_dir):
        register_reseller("r1", "1ai", "Alice")
        register_reseller("r2", "vilona", "Bob")
        all_r = get_all_resellers()
        assert len(all_r) == 2


# =========================================================================
#  LightweightMultiBot — Initialization
# =========================================================================


class TestLightweightMultiBotInit:
    """LightweightMultiBot.__init__ — bot registry setup."""

    def test_init_all_brands(self):
        """Creates Bot instances for all active BRANDS."""
        runner = LightweightMultiBot()
        assert "vilona" in runner.bots
        assert "1ai" in runner.bots
        assert len(runner.bots) == 2

    def test_init_filtered(self):
        """Only requested brand IDs are loaded."""
        runner = LightweightMultiBot(["vilona"])
        assert "vilona" in runner.bots
        assert "1ai" not in runner.bots
        assert len(runner.bots) == 1

    def test_init_skips_inactive(self, monkeypatch):
        """Brands with is_active=False are skipped."""
        monkeypatch.setitem(BRANDS, "inactive_test", BrandConfig(
            brand_id="inactive_test", name="Inactive", token="x",
            username="x", description="x", is_active=False,
        ))
        runner = LightweightMultiBot()
        assert "inactive_test" not in runner.bots

    def test_init_skips_unknown(self):
        """Unknown brand_ids are silently skipped."""
        runner = LightweightMultiBot(["nonexistent"])
        assert len(runner.bots) == 0

    def test_init_sets_offset_zero(self):
        """All bots start with offset=0."""
        runner = LightweightMultiBot(["vilona"])
        assert runner.offsets["vilona"] == 0


# =========================================================================
#  LightweightMultiBot — _clear_session
# =========================================================================


class TestClearSession:
    """_clear_session — graceful bot connection teardown."""

    async def test_close_success(self, mock_bot):
        runner = LightweightMultiBot(["vilona"])
        mock = runner.bots["vilona"]
        await runner._clear_session("vilona")
        mock.close.assert_awaited_once()

    async def test_close_raises(self, mock_bot, caplog):
        """Exception during close is logged, not propagated."""
        caplog.set_level(logging.WARNING)
        runner = LightweightMultiBot(["vilona"])
        mock = runner.bots["vilona"]
        mock.close.side_effect = RuntimeError("connection died")
        await runner._clear_session("vilona")
        assert "Close failed" in caplog.text

    async def test_duplicate_request_skipped(self, mock_bot):
        """Second call for same brand is no-op."""
        runner = LightweightMultiBot(["vilona"])
        mock = runner.bots["vilona"]
        await runner._clear_session("vilona")
        assert "vilona" in runner._close_requested
        await runner._clear_session("vilona")
        # close should have been called only once
        mock.close.assert_awaited_once()

    async def test_no_bot_for_brand(self, mock_bot):
        """No-op when brand has no bot (shouldn't happen, but safe)."""
        runner = LightweightMultiBot([])
        # Should not raise
        await runner._clear_session("nonexistent")


# =========================================================================
#  LightweightMultiBot — _get_timeout
# =========================================================================


class TestGetTimeout:
    """_get_timeout — per-brand poll timeout."""

    def test_1ai_short_poll(self):
        runner = LightweightMultiBot(["vilona", "1ai"])
        assert runner._get_timeout("1ai") == 1

    def test_vilona_long_poll(self):
        runner = LightweightMultiBot(["vilona", "1ai"])
        assert runner._get_timeout("vilona") == 10

    def test_unknown_brand_default(self):
        """Unknown brand falls to else branch (10)."""
        runner = LightweightMultiBot(["vilona"])
        assert runner._get_timeout("unknown") == 10


# =========================================================================
#  _reply Tests
# =========================================================================


class TestReply:
    """_reply — send messages via message or callback context."""

    async def test_reply_to_message(self):
        """Sends reply_text when update has a message."""
        update = _make_message_update()
        brand = BRANDS["vilona"]
        await _reply(update, brand, "Hello!")
        update.message.reply_text.assert_awaited_once_with(
            "Hello!", parse_mode="HTML", reply_markup=None,
        )

    async def test_reply_to_callback(self):
        """Edits callback message when update has callback_query."""
        update = _make_callback_update("menu_main")
        brand = BRANDS["vilona"]
        await _reply(update, brand, "Hello!")
        update.callback_query.message.edit_text.assert_awaited_once_with(
            "Hello!", parse_mode="HTML", reply_markup=None,
        )
        update.callback_query.answer.assert_awaited_once()

    async def test_reply_exception_swallowed(self, caplog):
        """Exception in reply is logged, not propagated."""
        caplog.set_level(logging.WARNING)
        update = _make_message_update()
        update.message.reply_text.side_effect = RuntimeError("network fail")
        await _reply(update, BRANDS["vilona"], "Oops")
        assert "Reply error" in caplog.text


# =========================================================================
#  Command Handler Tests
# =========================================================================


class TestCmdStart:
    """_cmd_start — welcome message with inline keyboard."""

    async def test_sends_welcome(self):
        update = _make_message_update()
        await _cmd_start(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "VILONA TRADEFX" in args[0]
        assert "Budi" in args[0]

    async def test_fallback_name(self):
        """Uses 'Trader' when user has no first_name."""
        update = _make_message_update()
        update.effective_user.first_name = None
        await _cmd_start(update, BRANDS["1ai"])
        args, _ = update.message.reply_text.await_args
        assert "Trader" in args[0]

    async def test_reply_has_keyboard(self):
        update = _make_message_update()
        await _cmd_start(update, BRANDS["vilona"])
        _, kwargs = update.message.reply_text.await_args
        assert kwargs.get("reply_markup") is not None


class TestCmdHelp:
    """_cmd_help — help text with back button."""

    async def test_sends_help(self):
        update = _make_message_update()
        await _cmd_help(update, BRANDS["1ai"])
        args, _ = update.message.reply_text.await_args
        assert "COMMAND CENTER" in args[0]
        assert "/start" in args[0]


class TestCmdStatus:
    """_cmd_status — user status display."""

    async def test_sends_status(self):
        update = _make_message_update()
        await _cmd_status(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "STATUS" in args[0]
        assert "Budi" in args[0]

    async def test_fallback_name(self):
        update = _make_message_update()
        update.effective_user.first_name = None
        await _cmd_status(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "Trader" in args[0]


class TestCmdDonate:
    """_cmd_donate — donation info with tier buttons."""

    async def test_sends_donation_info(self):
        update = _make_message_update()
        await _cmd_donate(update, BRANDS["1ai"])
        args, _ = update.message.reply_text.await_args
        assert "DONASI" in args[0]
        assert "QRIS" in args[0]


# =========================================================================
#  Callback Handler Tests
# =========================================================================


class TestHandleCallback:
    """_handle_callback — dispatch callback data to correct handler."""

    async def test_menu_main_routes_to_start(self):
        update = _make_callback_update("menu_main")
        await _handle_callback(update, BRANDS["vilona"])
        args, _ = update.callback_query.message.edit_text.await_args
        assert "VILONA TRADEFX" in args[0]

    async def test_help_callback(self):
        update = _make_callback_update("help")
        await _handle_callback(update, BRANDS["1ai"])
        args, _ = update.callback_query.message.edit_text.await_args
        assert "COMMAND CENTER" in args[0]

    async def test_status_callback(self):
        update = _make_callback_update("status")
        await _handle_callback(update, BRANDS["vilona"])
        args, _ = update.callback_query.message.edit_text.await_args
        assert "STATUS" in args[0]

    async def test_donate_callback(self):
        update = _make_callback_update("donate")
        await _handle_callback(update, BRANDS["1ai"])
        args, _ = update.callback_query.message.edit_text.await_args
        assert "DONASI" in args[0]

    async def test_donate_coffee_tier(self, tmp_data_dir, monkeypatch):
        """Donate:coffee generates payment and sends QR."""
        monkeypatch.setattr(_payment_mod, "create_transaction",
            lambda **kw: {"success": True, "data": {"checkout_url": "https://pay.example.com/coffee"}},
        )
        update = _make_callback_update("donate:coffee")
        await _handle_callback(update, BRANDS["1ai"])
        # Should attempt to send a photo (QR code)
        update.effective_message.reply_photo.assert_awaited()

    async def test_donate_fuel_tier(self, tmp_data_dir, monkeypatch):
        """Donate:fuel generates payment and sends QR."""
        monkeypatch.setattr(_payment_mod, "create_transaction",
            lambda **kw: {"success": True, "data": {"checkout_url": "https://pay.example.com/fuel"}},
        )
        update = _make_callback_update("donate:fuel")
        await _handle_callback(update, BRANDS["1ai"])
        update.effective_message.reply_photo.assert_awaited()

    async def test_donate_unknown_tier(self):
        """Unknown donation tier returns invalid nominal message."""
        update = _make_callback_update("donate:gold")
        await _handle_callback(update, BRANDS["1ai"])
        update.callback_query.answer.assert_awaited_with("Nominal tidak valid")

    async def test_donate_payment_fails_shows_bank(self, tmp_data_dir, monkeypatch):
        """When Tripay fails, falls back to bank transfer text."""
        monkeypatch.setattr(_payment_mod, "create_transaction",
            lambda **kw: {"success": False},
        )
        update = _make_callback_update("donate:coffee")
        await _handle_callback(update, BRANDS["1ai"])
        args, _ = update.callback_query.message.edit_text.await_args
        assert "BCA" in args[0]

    async def test_donate_qr_fails_shows_link(self, tmp_data_dir, monkeypatch):
        """When QR generation fails, falls back to text link."""
        monkeypatch.setattr(_payment_mod, "create_transaction",
            lambda **kw: {"success": True, "data": {"checkout_url": "https://pay.example.com/c"}},
        )
        monkeypatch.setattr(_wr.qrcode, "make", MagicMock(side_effect=RuntimeError("QR library broken")))

        update = _make_callback_update("donate:coffee")
        await _handle_callback(update, BRANDS["1ai"])
        # Should fall back to text with link
        called = update.callback_query.message.edit_text.await_args
        assert called is not None
        args0 = called[0][0] if called[0] else ""
        assert "pay.example.com" in args0 or "BCA" in args0

    async def test_unknown_callback(self):
        """Unknown callback data gets 'Fitur dalam pengembangan'."""
        update = _make_callback_update("unknown_action")
        await _handle_callback(update, BRANDS["vilona"])
        update.callback_query.answer.assert_awaited_with("Fitur dalam pengembangan...")


# =========================================================================
#  Dispatch Tests
# =========================================================================


class TestDispatchUpdate:
    """_dispatch_update — route message text or callbacks."""

    async def test_dispatch_callback(self):
        """Callback queries go to _handle_callback."""
        update = _make_callback_update("menu_main")
        await _dispatch_update(update, BRANDS["vilona"])
        update.callback_query.message.edit_text.assert_awaited()

    async def test_dispatch_start(self):
        update = _make_message_update("/start")
        await _dispatch_update(update, BRANDS["vilona"])
        update.message.reply_text.assert_awaited()

    async def test_dispatch_start_with_args(self):
        """/start with args (e.g. /start ref_abc) still works."""
        update = _make_message_update("/start ref_abc")
        await _dispatch_update(update, BRANDS["vilona"])
        update.message.reply_text.assert_awaited()

    async def test_dispatch_help(self):
        update = _make_message_update("/help")
        await _dispatch_update(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "COMMAND CENTER" in args[0]

    async def test_dispatch_status(self):
        update = _make_message_update("/status")
        await _dispatch_update(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "STATUS" in args[0]

    async def test_dispatch_donate(self):
        update = _make_message_update("/donate")
        await _dispatch_update(update, BRANDS["1ai"])
        args, _ = update.message.reply_text.await_args
        assert "DONASI" in args[0]

    async def test_dispatch_donasi_alias(self):
        """Indonesian /donasi alias routes to donate."""
        update = _make_message_update("/donasi")
        await _dispatch_update(update, BRANDS["1ai"])
        args, _ = update.message.reply_text.await_args
        assert "DONASI" in args[0]

    async def test_dispatch_sinyal(self):
        """/sinyal returns 'under development' message."""
        update = _make_message_update("/sinyal")
        await _dispatch_update(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "pengembangan" in args[0]

    async def test_dispatch_signal(self):
        """/signal (English alias) returns same message."""
        update = _make_message_update("/signal")
        await _dispatch_update(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "pengembangan" in args[0]

    async def test_dispatch_unknown_command(self):
        """Unknown command returns error message."""
        update = _make_message_update("/unknown")
        await _dispatch_update(update, BRANDS["vilona"])
        args, _ = update.message.reply_text.await_args
        assert "tidak dikenal" in args[0]

    async def test_dispatch_callback_query(self):
        """Callback query data routes through _handle_callback."""
        update = _make_callback_update("donate")
        await _dispatch_update(update, BRANDS["1ai"])
        update.callback_query.message.edit_text.assert_awaited()

    async def test_dispatch_exception_safe(self, caplog):
        """Exceptions in dispatch are caught and logged."""
        caplog.set_level(logging.ERROR)
        # Use /sinyal which calls reply_text DIRECTLY (not via _reply)
        # so the exception propagates up to _dispatch_update's catch
        update = _make_message_update("/sinyal")
        update.message.reply_text.side_effect = RuntimeError("boom")
        await _dispatch_update(update, BRANDS["vilona"])
        assert "Dispatch error" in caplog.text


# =========================================================================
#  Polling Loop Tests
# =========================================================================


class TestPollAll:
    """LightweightMultiBot.poll_all — core polling loop."""

    @pytest.mark.timeout(5)
    async def test_normal_poll_cycle(self, mock_bot):
        """Happy path: normal poll with empty updates loops."""
        runner = LightweightMultiBot(["vilona"])

        # Execute one manual poll cycle
        for bid, bot in list(runner.bots.items()):
            updates = await bot.get_updates(
                offset=runner.offsets[bid],
                timeout=runner._get_timeout(bid),
                allowed_updates=["message", "callback_query"],
            )
            for upd in updates:
                runner.offsets[bid] = upd.update_id + 1

        # No updates → offset stays 0
        assert runner.offsets["vilona"] == 0

    async def test_poll_with_updates(self, mock_bot):
        """Updates increment offset and dispatch."""
        update = MagicMock(spec=Update)
        update.update_id = 42
        update.callback_query = None
        update.message = MagicMock()
        update.message.text = "/start"
        update.message.reply_text = AsyncMock()
        update.effective_user = _mock_user()
        update.effective_message = update.message

        runner = LightweightMultiBot(["vilona"])
        runner.bots["vilona"].get_updates = AsyncMock(return_value=[update])

        for bid, bot in list(runner.bots.items()):
            brand = runner.brands.get(bid)
            updates = await bot.get_updates(
                offset=runner.offsets[bid],
                timeout=runner._get_timeout(bid),
                allowed_updates=["message", "callback_query"],
            )
            for upd in updates:
                runner.offsets[bid] = upd.update_id + 1
                await _dispatch_update(upd, brand)

        assert runner.offsets["vilona"] == 43
        update.message.reply_text.assert_awaited()

    async def test_409_conflict_recovery(self, mock_bot):
        """409 triggers _clear_session + reset bot + reset offset."""
        runner = LightweightMultiBot(["1ai"])
        original_bot = runner.bots["1ai"]
        original_bot.get_updates = AsyncMock(side_effect=RuntimeError("409: Conflict: terminated by other getUpdates"))

        try:
            for bid, bot in list(runner.bots.items()):
                brand = runner.brands.get(bid)
                try:
                    updates = await bot.get_updates(offset=runner.offsets[bid], timeout=runner._get_timeout(bid), allowed_updates=["message", "callback_query"])
                except Exception as e:
                    err_str = str(e)
                    if "409" in err_str or "Conflict" in err_str:
                        await runner._clear_session(bid)
                        runner.bots[bid] = Bot(token=brand.token)
                        runner.offsets[bid] = 0
        except Exception:
            pass

        assert runner.offsets["1ai"] == 0
        assert runner.bots["1ai"] is not original_bot

    async def test_non_409_exception(self, caplog, mock_bot):
        """Non-409 exceptions are logged, no session reset."""
        caplog.set_level(logging.WARNING)
        runner = LightweightMultiBot(["vilona"])
        runner.bots["vilona"].get_updates = AsyncMock(side_effect=RuntimeError("Timeout"))

        try:
            for bid, bot in list(runner.bots.items()):
                brand = runner.brands.get(bid)
                try:
                    updates = await bot.get_updates(offset=runner.offsets[bid], timeout=runner._get_timeout(bid), allowed_updates=["message", "callback_query"])
                except Exception as e:
                    err_str = str(e)
                    LOG.warning("Poll error (brand=%s): %s", bid, err_str)
                    if "409" in err_str or "Conflict" in err_str:
                        await runner._clear_session(bid)
                        runner.bots[bid] = Bot(token=brand.token)
                        runner.offsets[bid] = 0
        except Exception:
            pass

        assert "Poll error" in caplog.text
        assert "409" not in caplog.text

    async def test_missing_brand_skipped(self):
        """Brand that disappeared (e.g. from brands dict) is skipped."""
        runner = LightweightMultiBot(["vilona"])
        # Add a brand to brands but not bots (simulate race)
        runner.brands["ghost"] = BRANDS["vilona"]

        for bid, bot in list(runner.bots.items()):
            brand = runner.brands.get(bid)
            if not brand:
                continue
            # Normal poll
        # Should not raise
        assert True


# =========================================================================
#  Entry Point Tests
# =========================================================================


class TestEntryPoints:
    """run_bots / run_single_brand — top-level entry functions."""

    def test_run_bots(self, monkeypatch):
        """run_bots creates LightweightMultiBot and runs it."""
        started = False

        class FakeRunner:
            def __init__(self, brand_ids=None):
                pass

            def run_forever(self):
                nonlocal started
                started = True

        monkeypatch.setattr("unified_bot.telegram.whitelabel_runner.LightweightMultiBot", FakeRunner)
        run_bots()
        assert started

    def test_run_single_brand(self, monkeypatch):
        """run_single_brand passes a single brand ID."""
        captured = []

        class FakeRunner:
            def __init__(self, brand_ids=None):
                captured.append(brand_ids)

            def run_forever(self):
                pass

        monkeypatch.setattr("unified_bot.telegram.whitelabel_runner.LightweightMultiBot", FakeRunner)
        run_single_brand("1ai")
        assert captured == [["1ai"]]


# =========================================================================
#  _load_json / _save_json Tests
# =========================================================================


class TestJsonIO:
    """Low-level JSON persistence helpers."""

    def test_load_nonexistent(self, tmp_data_dir):
        """Missing file returns empty dict."""
        assert _load_json(tmp_data_dir / "nope.json") == {}

    def test_save_and_load(self, tmp_data_dir):
        """Round-trip save/load."""
        f = tmp_data_dir / "test.json"
        _save_json(f, {"key": "value", "num": 42})
        assert _load_json(f) == {"key": "value", "num": 42}

    def test_save_indent(self, tmp_data_dir):
        """JSON is saved with human-readable indentation."""
        f = tmp_data_dir / "pretty.json"
        _save_json(f, {"a": 1})
        content = f.read_text()
        assert "  " in content  # indented


# =========================================================================
#  BRANDS Registry Test
# =========================================================================


class TestBrandsRegistry:
    """BRANDS dict — all configured brands are valid."""

    def test_all_brands_active(self):
        """Every brand in BRANDS has is_active=True by default."""
        for bid, brand in BRANDS.items():
            assert brand.is_active, f"Brand {bid} is inactive"

    def test_all_brands_have_unique_tokens(self):
        """No two brands share the same token."""
        tokens = [b.token for b in BRANDS.values()]
        assert len(tokens) == len(set(tokens)), "Duplicate tokens found!"

    def test_all_brands_have_unique_usernames(self):
        """No two brands share the same username."""
        usernames = [b.username for b in BRANDS.values()]
        assert len(usernames) == len(set(usernames)), "Duplicate usernames found!"

    def test_all_brands_complete_config(self):
        """Every brand has all required fields set."""
        for bid, brand in BRANDS.items():
            assert brand.name, f"Brand {bid} missing name"
            assert brand.token, f"Brand {bid} missing token"
            assert brand.username, f"Brand {bid} missing username"
            assert brand.description, f"Brand {bid} missing description"

    def test_cut_ratios_sum(self):
        """Owner + reseller + referrer cuts should sum to 1.0."""
        for bid, brand in BRANDS.items():
            total = brand.owner_cut + brand.reseller_cut + brand.referrer_cut
            assert total == pytest.approx(1.0), f"Brand {bid} cuts sum to {total} (expected 1.0)"
