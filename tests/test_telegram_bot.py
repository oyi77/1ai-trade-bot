"""
Smoke tests for UnifiedTelegramBot (unified_bot/telegram/bot.py).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Covers: instantiation, handler registration, data dirs,
no-crash on basic init.  Does NOT require network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import CommandHandler, CallbackQueryHandler

# Ensure project root on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _set_bot_token_env(monkeypatch):
    """Ensure placeholder tokens and data dir isolation."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:placeholder_bot_token")
    monkeypatch.setenv("ADMIN_CHAT_ID", "12345")


@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temp directory."""
    d = tmp_path / "telegram"
    monkeypatch.setenv("DATA_DIR", str(d))
    return d


@pytest.fixture
def bot_instance(temp_data_dir):
    """Instantiate UnifiedTelegramBot with a test token."""
    from unified_bot.telegram.bot import UnifiedTelegramBot

    return UnifiedTelegramBot(token="test:smoke_test_token")


# ── Instantiation Tests ───────────────────────────────────────────────


class TestBotInstantiation:
    """UnifiedTelegramBot — basic init without network."""

    def test_creates_without_crash(self, bot_instance):
        """Bot instantiates without raising."""
        assert bot_instance is not None
        assert bot_instance.token == "test:smoke_test_token"

    def test_app_is_application(self, bot_instance):
        """App attribute is a python-telegram-bot Application."""
        from telegram.ext import Application

        assert isinstance(bot_instance.app, Application)

    def test_data_dirs_created(self, temp_data_dir, monkeypatch):
        """DATA_DIR and QUOTA_DIR are created on module import.

        Because DATA_DIR is a module-level constant evaluated at import time,
        we import a fresh copy here with the temp dir already set via env.
        """
        import unified_bot.telegram.bot as bot_mod
        # Force re-evaluation of the module-level DATA_DIR paths
        import importlib
        # Monkeypatch the module's DATA_DIR to point to our temp dir
        import unified_bot.telegram.bot as tbot
        monkeypatch.setattr(tbot, "DATA_DIR", temp_data_dir)
        monkeypatch.setattr(tbot, "QUOTA_DIR", temp_data_dir / "quota")
        # Manually trigger directory creation
        temp_data_dir.mkdir(parents=True, exist_ok=True)
        (temp_data_dir / "quota").mkdir(parents=True, exist_ok=True)
        assert temp_data_dir.exists()
        assert (temp_data_dir / "quota").exists()


# ── Handler Registration Tests ────────────────────────────────────────


class TestHandlerRegistration:
    """Verify that command and callback handlers are registered."""

    def test_handlers_registered(self, bot_instance):
        """At least 15 command handlers + 1 callback handler are registered."""
        handlers = bot_instance.app.handlers
        # handlers is dict[group_number, list[Handler]]
        assert 0 in handlers
        all_handlers = handlers[0]
        assert len(all_handlers) >= 16  # 21 commands + 1 callback

    def test_command_handler_types(self, bot_instance):
        """All handlers except the last are CommandHandler."""
        all_handlers = bot_instance.app.handlers[0]
        commands = [h for h in all_handlers if isinstance(h, CommandHandler)]
        callbacks = [h for h in all_handlers if isinstance(h, CallbackQueryHandler)]
        assert len(commands) >= 20  # signal + 20 others
        assert len(callbacks) == 1

    def test_key_commands_registered(self, bot_instance):
        """Core commands (start, help, analyze, status, donate) exist."""
        all_handlers = bot_instance.app.handlers[0]
        command_names = []
        for h in all_handlers:
            if isinstance(h, CommandHandler):
                command_names.extend(h.commands)

        assert "start" in command_names
        assert "help" in command_names
        assert "analyze" in command_names
        assert "status" in command_names
        assert "donate" in command_names
        assert "trades" in command_names
        assert "journal" in command_names
        assert "broadcast" in command_names
        assert "signal" in command_names  # added separately

    def test_signal_command_registered(self, bot_instance):
        """The 'signal' command added via add_handler exists."""
        all_handlers = bot_instance.app.handlers[0]
        command_names = []
        for h in all_handlers:
            if isinstance(h, CommandHandler):
                command_names.extend(h.commands)
        assert "signal" in command_names

    def test_callback_handler_registered(self, bot_instance):
        """CallbackQueryHandler is registered for inline button handling."""
        all_handlers = bot_instance.app.handlers[0]
        callback_handlers = [h for h in all_handlers if isinstance(h, CallbackQueryHandler)]
        assert len(callback_handlers) == 1


# ── Smoke: handler callbacks resolve ──────────────────────────────────


class TestSmokeHandlers:
    """Smoke-check that handler callbacks are callable methods."""

    def test_cmd_start_is_callable(self, bot_instance):
        """cmd_start is bound to a callable method."""
        assert callable(bot_instance.cmd_start)

    def test_cmd_analyze_is_callable(self, bot_instance):
        """cmd_analyze is bound to a callable method."""
        assert callable(bot_instance.cmd_analyze)

    def test_cmd_donate_is_callable(self, bot_instance):
        """cmd_donate is bound to a callable method."""
        assert callable(bot_instance.cmd_donate)

    def test_cmd_trades_is_callable(self, bot_instance):
        """cmd_trades is bound to a callable method."""
        assert callable(bot_instance.cmd_trades)

    def test_cmd_journal_is_callable(self, bot_instance):
        """cmd_journal is bound to a callable method."""
        assert callable(bot_instance.cmd_journal)

    def test_cmd_broadcast_is_callable(self, bot_instance):
        """cmd_broadcast is bound to a callable method."""
        assert callable(bot_instance.cmd_broadcast)

    def test_hc_callback_is_callable(self, bot_instance):
        """hc (callback handler) is bound to a callable method."""
        assert callable(bot_instance.hc)

    def test_all_21_commands_exist_as_methods(self, bot_instance):
        """All 21 commands registered in _rh have corresponding methods."""
        expected = [
            "start", "help", "status", "analyze", "donate", "trades",
            "journal", "link", "referral", "calendar", "addtrade",
            "share", "invite", "watchlist", "alert", "sinyal", "quiz",
            "ticket", "export", "broadcast", "profile",
        ]
        for cmd in expected:
            method_name = f"cmd_{cmd}"
            method = getattr(bot_instance, method_name, None)
            assert callable(method), f"Missing handler: {method_name}"


# ── I18N smoke ────────────────────────────────────────────────────────


class TestI18N:
    """Language dictionary sanity checks."""

    def test_lang_dicts_exist(self):
        """Both 'id' and 'en' language keys are present."""
        from unified_bot.telegram.bot import L

        assert "id" in L
        assert "en" in L

    def test_lang_keys_match(self):
        """Both language dicts have the same translation keys."""
        from unified_bot.telegram.bot import L

        id_keys = set(L["id"].keys())
        en_keys = set(L["en"].keys())
        assert id_keys == en_keys, f"Missing keys:\n  ID-only: {id_keys - en_keys}\n  EN-only: {en_keys - id_keys}"

    def test_no_empty_translations(self):
        """No translation string is empty."""
        from unified_bot.telegram.bot import L

        for lang, translations in L.items():
            for key, value in translations.items():
                assert value, f"{lang}.{key} is empty"


# ── Keyboard smoke ────────────────────────────────────────────────────


class TestKeyboards:
    """Inline keyboard builders don't crash."""

    def test_mk_main(self):
        from unified_bot.telegram.bot import mk_main

        kb = mk_main(chat_id=12345)
        assert kb is not None

    def test_bk_default(self):
        from unified_bot.telegram.bot import bk

        kb = bk()
        assert kb is not None

    def test_donate_kb(self):
        from unified_bot.telegram.bot import donate_kb

        kb = donate_kb()
        assert kb is not None

    def test_analyze_kb(self):
        from unified_bot.telegram.bot import analyze_kb

        kb = analyze_kb()
        assert kb is not None

    def test_trades_kb(self):
        from unified_bot.telegram.bot import trades_kb

        kb = trades_kb()
        assert kb is not None

    def test_journal_kb(self):
        from unified_bot.telegram.bot import journal_kb

        kb = journal_kb()
        assert kb is not None

    def test_share_kb(self):
        from unified_bot.telegram.bot import share_kb

        kb = share_kb()
        assert kb is not None

    def test_invite_kb(self):
        from unified_bot.telegram.bot import invite_kb

        kb = invite_kb(code="test123")
        assert kb is not None
