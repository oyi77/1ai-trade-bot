"""VilonaBot — core class: init, lifecycle, Telegram API, update dispatch."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Any

from tradebot.bots.platforms.vilona.analysis import AnalysisHandlersMixin
from tradebot.bots.platforms.vilona.callbacks import CallbackHandlersMixin
from tradebot.bots.platforms.vilona.commands import CommandHandlersMixin
from tradebot.bots.platforms.vilona.helpers import (
    DONATION_INPUT_STATE,
)
from tradebot.bots.platforms.vilona_bridge import VilonaSignalBridge

LOG = logging.getLogger("tradebot.bots.vilona.handler")


class VilonaBot(
    CommandHandlersMixin,
    CallbackHandlersMixin,
    AnalysisHandlersMixin,
):
    """Multi-asset AI-powered trading signal bot.

    Features:
    - Auto-analysis loop scanning gold, BTC, ETH, and more
    - Mechanical signal detection (Quant + FVG + Hermes Liquidity)
    - AI-powered analysis via OmniRoute (DeepSeek/OpenAI/Gemini)
    - Signal dispatch to MT5 EAs via bridge
    - User management with licenses and subscriptions
    - Categorized inline button menus with role-based views
    """

    def __init__(self, name: str = "vilona-tradefx") -> None:
        super().__init__(name=name)

        # API keys
        self.deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.claude_key = os.environ.get("CLAUDE_API_KEY", "")
        self.omniroute_url = os.environ.get(
            "OMNIROUTE_URL", "http://localhost:20128/v1/chat/completions"
        )

        # Signal bridge
        self.bridge = VilonaSignalBridge()

        # Market data stub
        self._market_data: Any = None
        self._try_init_market_data()

        # Engines
        self._engines: dict[str, bool] = {}
        self._init_engines()

        # State
        self._state: dict[str, Any] = {"last_update_id": 0}
        self._pending_signals: dict[str, dict[str, Any]] = {}
        self._user_last_analyze: dict[str, float] = {}
        self._user_last_direction: dict[str, dict[str, Any]] = {}
        self._autosync_users: set[str] = set()
        self._autosync_enabled = False

        # Scan interval
        self._scan_interval_sec = 300
        self._default_pair = "gold"
        self._posted_signals: dict[str, float] = {}

    # ── Engine initialization ────────────────────────────────────────────

    def _try_init_market_data(self) -> None:
        try:
            from market_data import UnifiedMarketData
            self._market_data = UnifiedMarketData()
            LOG.info("Market data layer loaded")
        except ImportError:
            LOG.warning("Market data layer unavailable")

    def _init_engines(self) -> None:
        engine_checks: dict[str, str] = {
            "layering": "layering",
            "quant": "quant_engine",
            "fvg": "fvg_detector",
            "crt": "crt_tbs_engine",
            "smc": "smc_scalper_engine",
            "hermes_liquidity": "hermes_liquidity_hunter",
            "learning": "learning_engine",
            "trade_tracker": "trade_tracker",
            "ultimate_smc": "ultimate_smc_engine",
        }
        for name, mod in engine_checks.items():
            try:
                __import__(mod)
                self._engines[name] = True
            except ImportError:
                self._engines[name] = False
        LOG.info("Engines loaded: %s", {k: v for k, v in self._engines.items() if v})

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        await super().start()
        self._schedule_background(self._auto_analysis_loop())

    def _register_commands(self) -> None:
        self._command_handlers = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "price": self._cmd_price,
            "analyze": self._cmd_analyze,
            "status": self._cmd_status,
            "subscribe": self._cmd_subscribe,
            "autosync": self._cmd_autosync,
            "donate": self._cmd_donate,
            "genkey": self._cmd_genkey,
            "listkeys": self._cmd_genkey,
            "mykey": self._cmd_mykey,
            "myid": self._cmd_myid,
            "ea": self._cmd_ea,
            "download": self._cmd_ea,
            "symbols": self._cmd_symbols,
            "data": self._cmd_data,
            "killzone": self._cmd_killzone,
            "bridge_status": self._cmd_bridge_status,
            "stockity": self._cmd_stockity,
            "history": self._cmd_history,
            "recap": self._cmd_recap,
            "winrate": self._cmd_winrate,
            "mapping": self._cmd_mapping,
            "signal": self._cmd_signal,
            "mtf": self._cmd_mtf,
            "engines": self._cmd_engines,
            "readings": self._cmd_engine_readings,
            "dashboard": self._cmd_dashboard,
            "levels": self._cmd_levels,
            "news": self._cmd_news,
            "zones": self._cmd_zones,
            "structure": self._cmd_structure,
            "session": self._cmd_session,
            "restart_bot": self._cmd_restart_bot,
            "activate": self._cmd_activate,
        }

    # ── Telegram message sending ─────────────────────────────────────────

    async def _tg_send(
        self,
        text: str,
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        target = chat_id or self.chat_id
        if not target or not self.bot_token:
            return False

        MAX_LEN = 4000
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 30] + "\n<i>... (dipotong)</i>"

        TAG_OPEN = "\ue000"
        TAG_CLOSE = "\ue001"
        text = re.sub(r"<(/?[abi][^>]*)>", TAG_OPEN + r"\1" + TAG_CLOSE, text)
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(TAG_OPEN, "<").replace(TAG_CLOSE, ">")

        payload: dict[str, Any] = {
            "chat_id": target, "text": text, "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return bool(json.loads(r.read()))
        except Exception as e:
            if "Bad Request" in str(e) or "can't parse" in str(e):
                try:
                    plain = re.sub(r"<[^>]+>", "", text)
                    payload = {"chat_id": target, "text": plain[:MAX_LEN]}
                    if reply_markup:
                        payload["reply_markup"] = reply_markup
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return bool(json.loads(r.read()))
                except Exception as e2:
                    LOG.error("tg_send fallback failed: %s", e2)
            else:
                LOG.error("tg_send failed: %s", e)
            return False

    async def _tg_answer_callback(self, cb_id: str, text: str = "") -> None:
        try:
            payload = json.dumps({"callback_query_id": cb_id, "text": text}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            LOG.warning("Webhook notification failed: %s", e)

    # ── Incoming message dispatcher ──────────────────────────────────────

    async def handle_update(self, update: dict[str, Any]) -> str | None:
        message = update.get("message", {})
        callback_query = update.get("callback_query", {})

        if callback_query:
            return await self._handle_callback(callback_query)

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        if not text:
            return None

        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        handler = self._command_handlers.get(cmd.lstrip("/"))
        if handler:
            response = await handler(args, chat_id=chat_id)
            if response:
                await self._tg_send(response, chat_id=chat_id)
            return response

        if chat_id in DONATION_INPUT_STATE:
            return await self._handle_donation_input(chat_id, text)

        fallback = (
            f"❌ Unknown command: <code>{cmd}</code>\n"
            f"Use /start for available commands."
        )
        await self._tg_send(fallback, chat_id=chat_id)
        return fallback
