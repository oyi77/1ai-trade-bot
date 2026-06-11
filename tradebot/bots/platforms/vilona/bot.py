"""VilonaBot — core class: init, lifecycle, Telegram API, update dispatch."""

from __future__ import annotations
from datetime import datetime

import asyncio
import json
import logging
import os
import re
import time
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
        self.fcs_api_key = os.environ.get("FCS_API_KEY", "")
        self.grok_api_key = os.environ.get("GROK_API_KEY", "")
        self.omniroute_url = os.environ.get(
            "OMNIROUTE_URL", "http://localhost:20128/v1/chat/completions"
        )
        self.omniroute_models: list[str] = ["deepseek-chat", "gpt-4o", "claude-sonnet-4-20250514"]
        self.xauusd_offset = float(os.environ.get("XAUUSD_PRICE_OFFSET", "74"))
        self._ai_token_usage: dict[str, dict[str, int]] = {}
        self.grok_url = "https://api.x.ai/v1/chat/completions"

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
        self._pending_signal_ttl = 300
        self._user_last_analyze: dict[str, float] = {}
        self._user_last_direction: dict[str, dict[str, Any]] = {}
        self._user_last_pair: dict[str, dict[str, Any]] = {}
        self._user_daily_analyze: dict[str, dict[str, Any]] = {}
        self._autosync_users: set[str] = set()
        self._autosync_enabled = False
        self._autosync_data: dict[str, str] = {}

        # Manual throttle constants
        self._manual_throttle_free = 120
        self._manual_throttle_donor = 60
        self._same_pair_cooldown = 90
        self._donor_daily_quota = 20
        self._free_daily_quota = 5
        self._direction_lock_seconds = 60

        # Ultimatum system
        self._ultimatum_accepted_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "data", "vilona_tradefx", "ultimatum_accepted"
        )
        self._video_file_id_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "media", "ultimatum_file_id.txt"
        )
        self._ultimatum_video_local = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "media", "Server_room_with_trading_charts_202606071902.mp4"
        )
        self._cached_video_file_id: str = ""
        self._admin_chat_id = os.environ.get(
            "VILONA_TRADEFX_ADMIN_CHAT_ID",
            os.environ.get("VILONA_TRADEFX_CHAT_ID", ""),
        )

        # Persisted state paths
        self._data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "data", "vilona_tradefx",
        )
        self._autosync_path = os.path.join(self._data_dir, "autosync.json")
        self._pending_signal_path = os.path.join(self._data_dir, ".pending_signals.json")

        # Load persisted state
        self._load_pending_signals()
        self._load_autosync()

        # Load cached video file_id
        self._cached_video_file_id = self._load_file_id()

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
            "sequoia": "sequoia_x_screener",
            "tv_engine": "tv_engine",
            "sweep_detector": "sweep_detector",
            "signal_feed": "scripts.signal_feed",
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
        self._load_pending_signals()
        self._load_autosync()
        self._schedule_background(self._auto_analysis_loop())
        self._schedule_background(self._outcome_check_loop())
        self._schedule_background(self._autosync_loop())
        self._schedule_background(self._reminder_loop())

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
            "donation_input": self._cmd_donation_input,
            "genkey": self._cmd_genkey,
            "listkeys": self._cmd_listkeys,
            "revokekey": self._cmd_revokekey,
            "mykey": self._cmd_mykey,
            "myid": self._cmd_myid,
            "ea": self._cmd_ea,
            "download": self._cmd_ea,
            "symbols": self._cmd_symbols,
            "data": self._cmd_data,
            "killzone": self._cmd_killzone,
            "bridge_status": self._cmd_bridge_status,
            "bridge_full_status": self._cmd_bridge_full_status,
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
            "trade_yes": self._cmd_trade_yes,
            "trade_no": self._cmd_trade_no,
            "trailing": self._cmd_trailing,
            "settrailing": self._cmd_settrailing,
            "autotrade": self._cmd_autotrade,
            "pulse": self._cmd_pulse,
            "briefing": self._cmd_briefing,
            "reminder": self._cmd_reminder,
            "ultimatum": self._cmd_ultimatum,
            "settings": self._cmd_settings,
            "elite_params": self._cmd_elite_params,
            "fvg": self._cmd_fvg,
            "sweep": self._cmd_sweep,
        }

    # ── Pending signals disk persistence ────────────────────────────────

    def _load_pending_signals(self) -> None:
        try:
            if os.path.exists(self._pending_signal_path):
                raw = json.loads(open(self._pending_signal_path).read())
                now = time.time()
                self._pending_signals = {
                    k: v for k, v in raw.items() if v.get("expires", 0) > now
                }
                LOG.info("Restored %d pending signal(s)", len(self._pending_signals))
        except Exception:
            self._pending_signals = {}

    def _save_pending_signals(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._pending_signal_path), exist_ok=True)
            open(self._pending_signal_path, "w").write(json.dumps(self._pending_signals))
        except Exception:
            pass

    def _cleanup_expired_pending_signals(self) -> None:
        now = time.time()
        expired = [k for k, v in self._pending_signals.items() if v.get("expires", 0) <= now]
        if expired:
            for k in expired:
                self._pending_signals.pop(k, None)
            self._save_pending_signals()

    # ── Autosync persistence ────────────────────────────────────────────

    def _load_autosync(self) -> None:
        try:
            if os.path.exists(self._autosync_path):
                self._autosync_data = json.loads(open(self._autosync_path).read())
                LOG.info("Restored autosync data for %d user(s)", len(self._autosync_data))
        except Exception:
            self._autosync_data = {}

    def _save_autosync(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._autosync_path), exist_ok=True)
            open(self._autosync_path, "w").write(json.dumps(self._autosync_data))
        except Exception:
            pass

    def _is_autosync(self, chat_id: str) -> bool:
        if not self._autosync_enabled:
            return False
        return str(chat_id) in self._autosync_data

    def _set_autosync(self, chat_id: str, enabled: bool = True) -> None:
        if enabled:
            self._autosync_data[str(chat_id)] = str(time.time())
        else:
            self._autosync_data.pop(str(chat_id), None)
        self._save_autosync()

    # ── Background loops ────────────────────────────────────────────────

    async def _outcome_check_loop(self) -> None:
        while True:
            try:
                if self._engines.get("trade_tracker"):
                    import importlib
                    tt = importlib.import_module("trade_tracker")
                    if hasattr(tt, "check_outcomes"):
                        tt.check_outcomes()
            except Exception:
                pass
            await asyncio.sleep(60)

    async def _autosync_loop(self) -> None:
        while True:
            try:
                if self._autosync_enabled and self.bridge:
                    pass  # autosync bridge polling handled by signal dispatch
            except Exception:
                pass
            await asyncio.sleep(30)

    async def _reminder_loop(self) -> None:
        while True:
            try:
                pass  # placeholder for due/expired member checks
            except Exception:
                pass
            await asyncio.sleep(3600)

    # ── Subscriber / anti-abuse checks ───────────────────────────────────────

    def _is_donor(self, chat_id: str) -> bool:
        try:
            from members import get_member as m_get
            member = m_get(str(chat_id))
            if member:
                status = member.get("status", "")
                tier = member.get("tier", "")
                return status in ("paid",) or tier in ("pro", "elite", "lifetime", "paid")
        except Exception:
            pass
        return False

    def _is_manual_blocked(self, chat_id: str, pair: str = "") -> tuple[bool, str]:
        now = time.time()
        is_donor = self._is_donor(str(chat_id))
        throttle = self._manual_throttle_donor if is_donor else self._manual_throttle_free

        # Layer 1: pending signal exists
        if chat_id in self._pending_signals:
            return True, "⏰ Sinyal sebelumnya masih berjalan. Tekan Trade Auto/Skip atau tunggu 5 menit."

        # Layer 2: general cooldown
        ts = self._user_last_analyze.get(chat_id)
        if ts and (now - ts) < throttle:
            wait = int(throttle - (now - ts))
            label = "Subscriber" if is_donor else "Free"
            return True, f"⏳ [{label}] Tunggu {wait} detik sebelum analisa berikutnya."

        # Layer 3: same-pair cooldown
        if pair:
            last_pair = self._user_last_pair.get(chat_id, {})
            if last_pair.get("pair") == pair and (now - last_pair.get("at", 0)) < self._same_pair_cooldown:
                return True, f"📊 Kamu baru analisa {pair.upper()} — coba pair lain: /analyze btc"

        # Layer 4: direction lock
        rec = self._user_last_direction.get(chat_id)
        if rec and rec.get("action") in ("BUY", "SELL"):
            try:
                last = datetime.fromisoformat(rec.get("at", ""))
                elapsed = (datetime.now() - last).total_seconds()
                if elapsed < self._direction_lock_seconds:
                    return True, (
                        f"🔒 Terdeteksi arah {rec['action']} pada {rec.get('asset', '?')}. "
                        f"Tunggu {int(self._direction_lock_seconds - elapsed)} detik."
                    )
            except Exception:
                pass
        return False, ""

    def _check_donor_quota(self, chat_id: str) -> tuple[bool, int, str | None]:
        today = datetime.now().strftime("%Y-%m-%d")
        record = self._user_daily_analyze.get(chat_id, {})
        if record.get("date") != today:
            record = {"date": today, "count": 0}
        record["count"] += 1
        self._user_daily_analyze[chat_id] = record
        if record["count"] > self._donor_daily_quota:
            return (
                False,
                0,
                f"🛑 Kuota Subscriber Harian Penuh! {self._donor_daily_quota}x/hari. Reset besok 00:00 WIB.",
            )
        remaining = max(0, self._donor_daily_quota - record["count"])
        return True, remaining, None

    def _touch_manual(
        self, chat_id: str, action: str | None = None, asset: str = "", pair: str = ""
    ) -> None:
        self._user_last_analyze[chat_id] = time.time()
        if pair:
            self._user_last_pair[chat_id] = {"pair": pair, "at": time.time()}
        if action in ("BUY", "SELL"):
            self._user_last_direction[chat_id] = {
                "action": action,
                "at": datetime.now().isoformat(),
                "asset": asset,
            }

    # ── Free quota system ──────────────────────────────────────────────

    def _get_quota(self, chat_id: str) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            qp = os.path.join(self._data_dir, "quota_cache", f"{chat_id}.json")
            if os.path.exists(qp):
                data = json.loads(open(qp).read())
                if data.get("date") == today:
                    return data
        except Exception:
            pass
        return {"date": today, "used": 0, "remaining": self._free_daily_quota}

    def _deduct_quota(self, chat_id: str) -> tuple[bool, int]:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            qd = os.path.join(self._data_dir, "quota_cache")
            os.makedirs(qd, exist_ok=True)
            qp = os.path.join(qd, f"{chat_id}.json")
            data = self._get_quota(chat_id)
            if data["date"] != today:
                data = {"date": today, "used": 0, "remaining": self._free_daily_quota}
            data["used"] += 1
            data["remaining"] = max(0, self._free_daily_quota - data["used"])
            open(qp, "w").write(json.dumps(data))
            return data["remaining"] > 0, data["remaining"]
        except Exception:
            return True, self._free_daily_quota

    # ── Ultimatum system ───────────────────────────────────────────────

    def _load_file_id(self) -> str:
        try:
            if os.path.exists(self._video_file_id_path):
                return open(self._video_file_id_path).read().strip()
        except Exception:
            pass
        return ""

    def _save_file_id(self, file_id: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._video_file_id_path), exist_ok=True)
            open(self._video_file_id_path, "w").write(file_id)
        except Exception:
            pass

    def _has_accepted_ultimatum(self, chat_id: str) -> bool:
        return os.path.exists(os.path.join(self._ultimatum_accepted_path, f"{chat_id}.json"))

    def _save_ultimatum(self, chat_id: str) -> None:
        try:
            os.makedirs(self._ultimatum_accepted_path, exist_ok=True)
            p = os.path.join(self._ultimatum_accepted_path, f"{chat_id}.json")
            open(p, "w").write(
                json.dumps({
                    "accepted_at": datetime.now().isoformat(),
                    "chat_id": str(chat_id),
                })
            )
        except Exception:
            pass

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
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
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

    async def _tg_send_video_file(self, chat_id: str, video: str) -> bool:
        """Send a video to a chat. video can be file_id or local path."""
        target = chat_id or self.chat_id
        if not target or not self.bot_token:
            return False
        try:
            import urllib.request
            if os.path.exists(video):
                boundary = "----FormBoundary7MA4YWxkTrZu0gW"
                import mimetypes
                mime_type = mimetypes.guess_type(video)[0] or "video/mp4"
                with open(video, "rb") as f:
                    file_data = f.read()
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{target}\r\n'
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="video"; filename="{os.path.basename(video)}"\r\n'
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{self.bot_token}/sendVideo",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
            else:
                payload = json.dumps({"chat_id": target, "video": video}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{self.bot_token}/sendVideo",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
            with urllib.request.urlopen(req, timeout=30) as r:
                return bool(json.loads(r.read()))
        except Exception as e:
            LOG.warning("_tg_send_video_file failed: %s", e)
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