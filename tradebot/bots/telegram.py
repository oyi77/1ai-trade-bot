"""
Unified Telegram Bot — single PTB instance, all platforms, all commands.

Replaces:
  - StockityBot (tradebot/bots/stockity/bot.py)
  - SubscriptionBot (tradebot/bots/subscription/bot.py)
  - VilonaHandler (tradebot/bots/vilona/handler.py)

One bot, one token, ALL features.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from tradebot.bots.handlers import register_standard_commands
from tradebot.config import settings
from tradebot.services.plans import get_user_plan
from tradebot.bots.platforms.vilona.bot import VilonaBot
from tradebot.storage.subscription import SubscriptionDatabase

LOG = logging.getLogger("tradebot.bots.telegram")

# ── Duration helpers ──────────────────────────────────────────────────────

_DURATION_SECONDS: dict[str, int] = {
    "daily": 86400,
    "weekly": 86400 * 7,
    "monthly": 86400 * 30,
}

PRICE_MAP: dict[str, int] = {
    "daily": 15_000,
    "weekly": 75_000,
    "monthly": 200_000,
}

VALID_SYMBOLS: list[str] = ["CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"]

PLATFORM_PAIRS = {
    "deriv": ["R_10", "R_25", "R_50", "R_75", "R_100"],
    "binance": ["BTCUSDT", "ETHUSDT"],
    "forex": ["XAUUSD", "EURUSD", "GBPUSD"],
    "stockity": ["CRYPTO_IDX", "EUR_USD_OTC", "GBP_USD_OTC"],
}

PLATFORM_LABELS = {
    "deriv": "Deriv (Binary Options)",
    "binance": "CCXT (Binance Crypto)",
    "forex": "Forex (Yahoo Finance)",
    "stockity": "Stockity (OTC Markets)",
}

def get_platform_keyboard(flow_type: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(PLATFORM_LABELS["deriv"], callback_data=f"flow:{flow_type}_platform:deriv")],
        [InlineKeyboardButton(PLATFORM_LABELS["binance"], callback_data=f"flow:{flow_type}_platform:binance")],
        [InlineKeyboardButton(PLATFORM_LABELS["forex"], callback_data=f"flow:{flow_type}_platform:forex")],
        [InlineKeyboardButton(PLATFORM_LABELS["stockity"], callback_data=f"flow:{flow_type}_platform:stockity")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_pair_keyboard(flow_type: str, platform: str) -> InlineKeyboardMarkup:
    pairs = PLATFORM_PAIRS.get(platform, [])
    keyboard = []
    row = []
    for pair in pairs:
        row.append(InlineKeyboardButton(pair, callback_data=f"flow:{flow_type}_pair:{platform}:{pair}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"flow:{flow_type}_back")])
    return InlineKeyboardMarkup(keyboard)

def _expires_at(plan: str) -> int:
    delta = _DURATION_SECONDS.get(plan, 86400)
    return int(time.time()) + delta


def _format_trade(row: dict[str, Any]) -> str:
    ts = datetime.fromtimestamp(row["created_at"], tz=UTC).strftime("%m/%d %H:%M")
    emoji = "✅" if row["status"] == "won" else ("❌" if row["status"] == "lost" else "⏳")
    return (
        f"{emoji} `{row['symbol']}` {row['direction']} "
        f"| {ts} | PnL: {row['result_pnl']:+.2f}"
    )


class UnifiedBot(VilonaBot):
    """One Telegram bot to rule them all.

    Registers:
      * Trading commands (/signal, /scan, /symbols, /stats, /cookies)
      * Account commands (/balance, /deposit)
      * Shared commands via register_standard_commands()
        (/plans, /upgrade, /subscribe, /subscribe, /affiliate, /whitelabel, etc.)
      * Platform routing: auto-detects from user's linked platform
    """

    def __init__(self, token: str | None = None):
        super().__init__(name="unified-bot")
        self._token = token or settings.TELEGRAM_BOT_TOKEN
        self._app: Application | None = None
        self._running = False
        self.db = SubscriptionDatabase(
            db_path=settings.STORAGE_DB_PATH or ""
        )
        self._admin_chat_id: int = int(
            os.environ.get("ADMIN_CHAT_ID", "5220170786")
        )

    def is_admin(self, uid: str | int) -> bool:
        admin_ids = [str(x).strip() for x in os.environ.get("ADMIN_USER_IDS", "5220170786").split(",") if x.strip()]
        admin_ids.append(str(self._admin_chat_id))
        return str(uid) in admin_ids

    async def _handle_menu_nav(self, menu_name: str, chat_id: str) -> str:
        if menu_name == "main" and self.is_admin(chat_id):
            menu_name = "admin"
        return await super()._handle_menu_nav(menu_name, chat_id)

    # ── Build ──────────────────────────────────────────────────────

    def build(self) -> Application:
        """Build PTB Application with payment/subscription-only handlers.

        ALL core forex commands (/start, /help, /analyze, /price, /signal, etc.)
        are handled exclusively by the legacy handler (vilona_tradefx_handler.py).
        This bot ONLY handles payment flows to prevent double-output bugs.
        """
        self.db.create_tables()

        app = Application.builder().token(self._token).build()

        # ── PAYMENT & SUBSCRIPTION ONLY ──
        # NON-PAYMENT COMMANDS (/start, /help, /analyze, /signal, /price,
        # etc.) have NO registered handlers here. PTB silently ignores
        # them — the legacy handler processes them exclusively.
        # This is structural segregation, not filtering.
        #
        # Whitelist: /subscribe, /donate, /settings,
        # /plans, /upgrade, /confirm, /signals, /unsubscribe,
        # /affiliate, /whitelabel, /set_share, /set_rate, /set_plan
        # These are the ONLY commands this bot handles.
        # Everything else is exclusively processed by the legacy handler.
        app.add_handler(CommandHandler("subscribe", self._h_subscribe_cmd))
        app.add_handler(CommandHandler("donate", self._h_donate))
        app.add_handler(CommandHandler("settings", self._h_settings))
        # Portfolio & trading commands
        app.add_handler(CommandHandler("portfolio", self._h_portfolio))
        app.add_handler(CommandHandler("trade", self._h_trade))
        app.add_handler(CommandHandler("link", self._h_link))
        app.add_handler(CommandHandler("unlink", self._h_unlink))
        app.add_handler(CommandHandler("platforms", self._h_platforms))
        app.add_handler(CommandHandler("autotrade", self._h_autotrade))

        # All shared payment/affiliate commands (plans, upgrade, confirm, signals,
        # unsubscribe, affiliate, whitelabel, set_share, set_rate, set_plan)
        register_standard_commands(app)

        # Callback queries — all supported prefixes
        app.add_handler(CallbackQueryHandler(
            self._h_callback,
            pattern=r'^(plans|link|check_|cmd:|menu:|pref_|pay:|check:|pricing:|donate:|sub:|cancel_input|portfolio:|autotrade:)'
        ))

    # ── Start / Stop ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling and background loops."""
        self._load_pending_signals()
        self._load_autosync()

        self._app = self.build()
        self._running = True
        LOG.info("🤖 UnifiedBot starting...")
        await self._app.initialize()
        await self._app.start()
        try:
            from telegram import BotCommand
            commands = [
                BotCommand("start", "📖 Mulai bot & Tampilkan menu utama"),
                BotCommand("help", "📚 Panduan & Bantuan penggunaan"),
                BotCommand("signal", "🎯 Generate sinyal trading (CEX, Forex, dll)"),
                BotCommand("price", "💰 Cek harga real-time (CEX, Forex, dll)"),
                BotCommand("status", "🛡 Cek kuota & status akun aktif"),
                BotCommand("subscribe", "⭐ Upgrade langganan PRO/ELITE/LIFETIME"),
                BotCommand("dashboard", "📊 Akses Web Dashboard"),
            ]
            await self._app.bot.set_my_commands(commands)
            LOG.info("✅ Telegram commands menu updated successfully via set_my_commands")
        except Exception as exc:
            LOG.warning("Failed to set bot commands: %s", exc)
        await self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        LOG.info("✅ UnifiedBot running")

        # Start the background tasks
        self._schedule_background(self._auto_analysis_loop())
        self._schedule_background(self._outcome_check_loop())
        self._schedule_background(self._autosync_loop())
        self._schedule_background(self._reminder_loop())

        # Keep alive
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        await super().stop()
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            LOG.info("UnifiedBot stopped")

    # ── Command Handlers ───────────────────────────────────────────

    async def _h_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start — welcome message with referral handling and inline keyboard."""
        user_id = str(update.effective_chat.id)
        plan = get_user_plan(user_id)

        # Handle referral deep link: /start ref_XXXXXXXX
        if context.args and context.args[0].startswith("ref_"):
            ref_code = context.args[0][4:]
            from tradebot.bots.stockity.affiliate import get_affiliate_by_code, record_referral
            referrer = get_affiliate_by_code(ref_code)
            if referrer:
                record_referral(user_id, ref_code)
                LOG.info("Referral recorded: %s → %s", user_id, ref_code)

        text = (
            "📡 *1ai-trade-bot*\n\n"
            "AI-powered trading signals across all platforms.\n\n"
            f"Plan: *{plan.value.upper()}*\n\n"
            "⚡ *Commands:*\n"
            "/signal <symbol> — Get live signal\n"
            "/scan — Scan all symbols\n"
            "/symbols — List available symbols\n"
            "/stats — Trading statistics\n"
            "/balance — Check balance\n\n"
            "💳 /plans — Subscription plans\n"
            "📡 /signals — Signal categories\n"
            "🤝 /affiliate — Earn commissions\n\n"
            "_Use /help for all commands_"
        )

        from tradebot.services.menu import get_inline_keyboard
        is_admin = self.is_admin(user_id)
        menu_name = "admin" if is_admin else "main"
        kb_dict = get_inline_keyboard(menu_name)
        kb = kb_dict.get("inline_keyboard", [])

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        rows = []
        for r in kb:
            row = []
            for b in r:
                if "url" in b:
                    row.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
                else:
                    row.append(InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]))
            rows.append(row)
        reply_markup = InlineKeyboardMarkup(rows)

        await update.message.reply_markdown(text, reply_markup=reply_markup)

    async def _h_ref_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handled by _h_start — this is a no-op to prevent double-handling."""
        pass

    async def _h_symbols(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/symbols — list available trading symbols."""
        syms = getattr(settings, "SYMBOLS", "CRYPTO_IDX,BTC_IDX,ETH_IDX,GOLD_IDX")
        symbol_list = [s.strip() for s in syms.split(",") if s.strip()]
        lines = ["📋 *Available Symbols*\n"]
        for s in symbol_list:
            lines.append(f"  • `{s}`")
        lines.append(f"\nTotal: {len(symbol_list)} symbols")
        lines.append("\nUse `/signal <symbol>` to get a signal")
        await update.message.reply_markdown("\n".join(lines))

    async def _h_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/portfolio — show best asset for current session."""
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        try:
            resp = await self._cmd_portfolio([], chat_id=chat_id)
            await update.message.reply_html(resp)
        except Exception as e:
            LOG.error("_h_portfolio error: %s", e)
            await update.message.reply_html(f"Error: {e}")

    async def _h_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/trade <RIC> — execute turbo trade on Stockity."""
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        args = context.args or []
        try:
            resp = await self._cmd_trade(args, chat_id=chat_id)
            await update.message.reply_html(resp)
        except Exception as e:
            LOG.error("_h_trade error: %s", e)
            await update.message.reply_html(f"Error: {e}")

    async def _h_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/link <platform> <credentials> — link broker account."""
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        args = context.args or []
        try:
            resp = await self._cmd_link(args, chat_id=chat_id)
            await update.message.reply_html(resp)
        except Exception as e:
            LOG.error("_h_link error: %s", e)
            await update.message.reply_html(f"Error: {e}")

    async def _h_unlink(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/unlink <platform> — unlink broker account."""
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        args = context.args or []
        try:
            resp = await self._cmd_unlink(args, chat_id=chat_id)
            await update.message.reply_html(resp)
        except Exception as e:
            LOG.error("_h_unlink error: %s", e)
            await update.message.reply_html(f"Error: {e}")

    async def _h_platforms(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/platforms — show linked platforms."""
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        try:
            resp = await self._cmd_platforms([], chat_id=chat_id)
            await update.message.reply_html(resp)
        except Exception as e:
            LOG.error("_h_platforms error: %s", e)
            await update.message.reply_html(f"Error: {e}")

    async def _h_autotrade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/autotrade [on|off] — toggle auto-execution for your linked account."""
        if not update.message:
            return
        chat_id = str(update.effective_chat.id)
        args = context.args or []
        try:
            resp = await self._cmd_autotrade(args, chat_id=chat_id)
            await update.message.reply_html(resp)
        except Exception as e:
            LOG.error("_h_autotrade error: %s", e)
            await update.message.reply_html(f"Error: {e}")

    async def _h_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/signal <symbol> — generate signal for a symbol (or start flow)."""
        args = context.args or []
        if not args:
            reply_markup = get_platform_keyboard("sig")
            await update.message.reply_html(
                "🎯 <b>SIGNAL GENERATOR</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih platform pasar di bawah ini:",
                reply_markup=reply_markup
            )
            return

        symbol = args[0].strip().upper()
        from tradebot.signals.market import MarketAggregator
        symbol = MarketAggregator._resolve_alias(symbol)
        msg = await update.message.reply_html(f"🔍 Analyzing <code>{symbol}</code>...")
        resp = await self._cmd_signal([symbol])
        await msg.edit_text(resp, parse_mode="HTML")

    async def _h_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/price <symbol> — get real-time price (or start flow)."""
        args = context.args or []
        if not args:
            reply_markup = get_platform_keyboard("prc")
            await update.message.reply_html(
                "💰 <b>CEK HARGA REAL-TIME</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih platform pasar di bawah ini:",
                reply_markup=reply_markup
            )
            return

        symbol = args[0].strip().upper()
        from tradebot.signals.market import MarketAggregator
        symbol = MarketAggregator._resolve_alias(symbol)
        msg = await update.message.reply_html(f"💰 Fetching price for <code>{symbol}</code>...")
        resp = await self._get_generic_price(symbol)
        await msg.edit_text(resp, parse_mode="HTML")

    async def _get_generic_price(self, symbol: str) -> str:
        from tradebot.signals.market import MarketAggregator
        try:
            agg = MarketAggregator()
            candles = await agg.fetch(symbol, interval="1m", count=5)
            if not candles:
                return f"❌ Data harga untuk <b>{symbol}</b> tidak ditemukan."
            
            candle = candles[-1]
            close = candle.close
            high = candle.high
            low = candle.low
            open_val = candle.open
            change = close - open_val
            pct = (change / open_val) * 100 if open_val > 0 else 0
            emoji = "🟢" if change >= 0 else "🔴"
            
            from tradebot.bots.platforms.vilona.helpers import wib_fmt
            return (
                f"{emoji} <b>{symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Price: <b>{close:.4f}</b>\n"
                f"High: {high:.4f} | Low: {low:.4f}\n"
                f"Change: {change:+.4f} ({pct:+.2f}%)\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🕐 {wib_fmt()}"
            )
        except Exception as e:
            return f"❌ Gagal mengambil harga {symbol}: {str(e)}"

    async def _h_donate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/donate — public donation menu."""
        chat_id = str(update.effective_chat.id)
        await self._handle_menu_nav("donate", chat_id=chat_id)

    async def _h_subscribe_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/subscribe — premium subscription packages menu."""
        chat_id = str(update.effective_chat.id)
        await self._handle_menu_nav("subscribe", chat_id=chat_id)

    async def _h_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/settings — pro-active signal settings menu."""
        user_id = str(update.effective_chat.id)
        text = self.get_settings_text(user_id)
        reply_markup = self.get_settings_keyboard(user_id)
        await update.message.reply_html(text, reply_markup=reply_markup)

    def _get_or_create_sig_pref(self, user_id: str | int) -> dict[str, Any]:
        uid = int(user_id)
        prefs = self.db.get_user_signal_preferences(uid)
        if prefs:
            return prefs[0]
        # Create default
        self.db.set_user_signal_preference(uid, "ALL", 0.6, "BOTH", 1)
        return {"user_id": uid, "symbol": "ALL", "min_confidence": 0.6, "direction": "BOTH", "enabled": 1}

    def get_settings_keyboard(self, user_id: str | int) -> InlineKeyboardMarkup:
        pref = self._get_or_create_sig_pref(user_id)
        enabled_label = "🔔 Pro-active Signals: ON" if pref["enabled"] else "🔕 Pro-active Signals: OFF"
        symbol_label = f"🎯 Asset: {pref['symbol']}"
        dir_label = f"📈 Arah: {pref['direction']}"
        conf_label = f"⭐ Min Confidence: {int(pref['min_confidence'] * 100)}%"
        
        keyboard = [
            [InlineKeyboardButton(enabled_label, callback_data="pref_toggle:enabled")],
            [InlineKeyboardButton(symbol_label, callback_data="pref_cycle:symbol")],
            [InlineKeyboardButton(dir_label, callback_data="pref_cycle:direction")],
            [InlineKeyboardButton(conf_label, callback_data="pref_cycle:min_confidence")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu:account")],
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_settings_text(self, user_id: str | int) -> str:
        pref = self._get_or_create_sig_pref(user_id)
        status = "AKTIF 🟢" if pref["enabled"] else "NONAKTIF ⚪"
        return (
            f"⚙️ <b>PENGATURAN NOTIFIKASI PROAKTIF</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Konfigurasikan notifikasi sinyal otomatis yang ingin Anda terima:\n\n"
            f"• Status Notifikasi: <b>{status}</b>\n"
            f"• Filter Aset/Pair: <b>{pref['symbol']}</b>\n"
            f"• Kriteria Arah: <b>{pref['direction']}</b>\n"
            f"• Min. Confidence: <b>{int(pref['min_confidence'] * 100)}%</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Klik tombol di bawah untuk mengubah nilai:"
        )

    async def _handle_pref_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        query = update.callback_query
        user_id = str(query.from_user.id)
        
        parts = data.split(":")
        field = parts[1]
        
        pref = self._get_or_create_sig_pref(user_id)
        
        if field == "enabled":
            pref["enabled"] = 0 if pref["enabled"] else 1
        elif field == "symbol":
            symbols = ["ALL", "XAUUSD", "BTCUSD", "R_75", "CRYPTO_IDX"]
            current = pref["symbol"]
            next_idx = (symbols.index(current) + 1) % len(symbols) if current in symbols else 0
            pref["symbol"] = symbols[next_idx]
        elif field == "direction":
            dirs = ["BOTH", "BUY", "SELL"]
            current = pref["direction"]
            next_idx = (dirs.index(current) + 1) % len(dirs) if current in dirs else 0
            pref["direction"] = dirs[next_idx]
        elif field == "min_confidence":
            confs = [0.6, 0.7, 0.8, 0.9]
            current = pref["min_confidence"]
            next_idx = (confs.index(current) + 1) % len(confs) if current in confs else 0
            pref["min_confidence"] = confs[next_idx]
            
        # Save to DB
        self.db.set_user_signal_preference(
            int(user_id),
            pref["symbol"],
            pref["min_confidence"],
            pref["direction"],
            pref["enabled"]
        )
        
        text = self.get_settings_text(user_id)
        reply_markup = self.get_settings_keyboard(user_id)
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)

    async def _handle_flow_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        query = update.callback_query
        parts = data.split(":")
        action = parts[1]
        
        if action == "sig_back":
            reply_markup = get_platform_keyboard("sig")
            await query.edit_message_text(
                "🎯 <b>SIGNAL GENERATOR</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih platform pasar di bawah ini:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif action == "prc_back":
            reply_markup = get_platform_keyboard("prc")
            await query.edit_message_text(
                "💰 <b>CEK HARGA REAL-TIME</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih platform pasar di bawah ini:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif action == "sig_platform":
            platform = parts[2]
            reply_markup = get_pair_keyboard("sig", platform)
            await query.edit_message_text(
                f"🧠 <b>ANALISIS {PLATFORM_LABELS[platform].upper()}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Pilih pair/aset di bawah ini:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif action == "prc_platform":
            platform = parts[2]
            reply_markup = get_pair_keyboard("prc", platform)
            await query.edit_message_text(
                f"💰 <b>HARGA {PLATFORM_LABELS[platform].upper()}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Pilih pair/aset di bawah ini:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif action == "sig_pair":
            platform = parts[2]
            pair = parts[3]
            await query.edit_message_text(f"🔍 Analyzing <code>{pair}</code> on {PLATFORM_LABELS[platform]}...", parse_mode="HTML")
            resp = await self._cmd_signal([pair])
            try:
                await query.edit_message_text(resp, parse_mode="HTML")
            except Exception:
                await query.message.reply_html(resp)
        elif action == "prc_pair":
            platform = parts[2]
            pair = parts[3]
            await query.edit_message_text(f"💰 Fetching price for <code>{pair}</code> on {PLATFORM_LABELS[platform]}...", parse_mode="HTML")
            resp = await self._get_generic_price(pair)
            try:
                await query.edit_message_text(resp, parse_mode="HTML")
            except Exception:
                await query.message.reply_html(resp)

    async def _h_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/scan — scan all symbols for signals."""
        syms = getattr(settings, "SYMBOLS", "CRYPTO_IDX,BTC_IDX,ETH_IDX,GOLD_IDX")
        symbol_list = [s.strip() for s in syms.split(",") if s.strip()]
        msg = await update.message.reply_text(f"🔍 Scanning {len(symbol_list)} symbols...")

        try:
            from tradebot.engines.registry import Registry
            reg = Registry()
            engines = reg.discover()

            results = []
            for symbol in symbol_list[:8]:  # Limit to 8 to avoid timeout
                try:
                    from tradebot.signals.stockity import StockitySource
                    async with StockitySource() as src:
                        ticks = await src.fetch_ticks(symbol)
                        if not ticks:
                            continue
                    for name, engine in engines.items():
                        try:
                            result = engine.analyze(ticks)
                            if result and result.confidence >= 0.5:
                                results.append(f"  • `{symbol}`: {result.direction} "
                                               f"({name}, {result.confidence:.0%})")
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

            lines = [f"📊 *Scan Results* ({len(symbol_list)} symbols)\n"]
            if results:
                lines.extend(results[:15])
            else:
                lines.append("_No strong signals across any symbol_")
            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Scan error: {e}", parse_mode="Markdown")

    async def _h_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/stats — trading + plan statistics."""
        user_id = str(update.effective_chat.id)
        plan = get_user_plan(user_id)

        from tradebot.services.plans import get_plan_stats, get_total_revenue
        stats = get_plan_stats()
        revenue = get_total_revenue()

        text = (
            "📊 *Trading Stats*\n\n"
            f"Your Plan: *{plan.value.upper()}*\n"
            f"Total Users: {sum(stats.values())}\n"
            f"Total Revenue: Rp {revenue:,}\n\n"
            "⚡ *Platform:* Stockity (live)\n"
            "📡 *Engines:* 11 active\n"
            "💳 /plans — Upgrade\n"
            "📡 /signals — Browse signals"
        )
        await update.message.reply_markdown(text)

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _ensure_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return 0
        self.db.register_user(
            user_id=user.id,
            chat_id=chat.id,
            username=user.username or "",
            first_name=user.first_name or "",
            language_code=user.language_code or "en",
        )
        return user.id

    def _is_admin(self, user_id: int) -> bool:
        user = self.db.get_user(user_id)
        return bool(user and (user.get("is_admin") == 1 or user_id == self._admin_chat_id))

    async def _reply(
        self,
        update: Update,
        text: str,
        keyboard: list[list[InlineKeyboardButton]] | None = None,
        parse_mode: str = "Markdown",
    ) -> None:
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
            except Exception:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(
                    text=text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
        elif update.message:
            await update.message.reply_text(
                text=text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        else:
            try:
                chat_id = update.effective_chat.id
                await update.get_bot().send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
            except Exception:
                LOG.warning("Could not reply — no chat context")

    # ── Command: /link ───────────────────────────────────────────────────


    # ── Signal dispatch ──────────────────────────────────────────────────

    async def _dispatch_signal_to_subscribers(self, sig: Any) -> None:
        """Send a signal to all active subscribers with pro-active filter settings."""
        subscribers = self.db.get_active_subscribers()
        if not subscribers:
            return

        sig_symbol = getattr(sig, "symbol", "").strip().upper()
        sig_direction = getattr(sig, "direction", "").strip().upper()
        sig_confidence = getattr(sig, "confidence", 1.0)
        if hasattr(sig, "metadata") and isinstance(sig.metadata, dict):
            sig_confidence = sig.metadata.get("confidence", sig_confidence)

        text = f"📡 *Signal Alert*\n\n{self._signal_to_text(sig)}"
        sent = 0

        for user in subscribers:
            user_id = user["user_id"]
            try:
                prefs = self.db.get_user_signal_preferences(user_id)
                if prefs:
                    pref = prefs[0]
                    if not pref.get("enabled", 1):
                        continue
                    pref_symbol = pref.get("symbol", "ALL").strip().upper()
                    if pref_symbol != "ALL" and pref_symbol != sig_symbol:
                        continue
                    pref_dir = pref.get("direction", "BOTH").strip().upper()
                    if pref_dir != "BOTH" and pref_dir != sig_direction:
                        continue
                    pref_conf = pref.get("min_confidence", 0.6)
                    if sig_confidence < pref_conf:
                        continue
            except Exception as e:
                LOG.warning("Failed to load/apply preferences for user %d: %s", user_id, e)

            try:
                await self._app.bot.send_message(
                    chat_id=user["chat_id"], text=text, parse_mode="Markdown",
                )
                sent += 1
            except Exception as exc:
                LOG.warning("Signal dispatch fail to %d: %s", user["user_id"], exc)
            await asyncio.sleep(0.03)
        LOG.info("Signal dispatched to %d/%d subscribers", sent, len(subscribers))
    @staticmethod
    def _signal_to_text(sig: Any) -> str:
        """Format a signal object to human-readable text."""
        try:
            return sig.pretty()
        except Exception:
            return str(sig)

    # ── Callback handler ─────────────────────────────────────────────────
    async def _tg_send(
        self,
        text: str,
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        if hasattr(self, "_active_query") and self._active_query:
            query = self._active_query
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            kb = None
            if reply_markup and "inline_keyboard" in reply_markup:
                rows = []
                for r in reply_markup["inline_keyboard"]:
                    row = []
                    for b in r:
                        if "url" in b:
                            row.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
                        else:
                            row.append(InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]))
                    rows.append(row)
                kb = InlineKeyboardMarkup(rows)
            try:
                await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=kb)
                return True
            except Exception as e:
                LOG.warning("Failed to edit message in-place: %s", e)

        return await super()._tg_send(text, chat_id, reply_markup)

    # ── Callback handler ─────────────────────────────────────────────────

    async def _h_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        data = query.data
        await query.answer()

        self._active_query = query
        try:
            if data == "plans":
                await self._reply(update, self._pricing_text())
            elif data == "link":
                await self._reply(
                    update,
                    "🔗 Use `/link <your_auth_token>` to connect your Stockity account.\n\n"
                    "Need help? Run /link without arguments.",
                )
            elif data == "signal_now":
                await self._reply(update, "⏳ Scanning... Stand by.")
                await self._reply(update, "⚠️ Live signal requires Stockity auth. Use /signal <symbol>.")
            elif data == "stats":
                await self._h_stats(update, context)
            elif data.startswith("check_"):
                merchant_ref = data[6:]
                if merchant_ref:
                    from tradebot.bots.handlers import _h_confirm
                    context.args = [merchant_ref]
                    await _h_confirm(update, context)
            elif data == "cmd:settings" or data == "menu:settings":
                user_id = str(query.from_user.id)
                text = self.get_settings_text(user_id)
                reply_markup = self.get_settings_keyboard(user_id)
                await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
            elif data.startswith("pref_"):
                await self._handle_pref_callback(update, context, data)
            elif data.startswith("flow:"):
                await self._handle_flow_callback(update, context, data)
            else:
                cb_dict = {
                    "id": query.id,
                    "data": query.data,
                    "from": {
                        "id": query.from_user.id,
                        "username": query.from_user.username or "",
                        "first_name": query.from_user.first_name or "",
                    },
                    "message": {
                        "message_id": query.message.message_id if query.message else None,
                        "chat": {"id": query.message.chat.id if query.message else None},
                    }
                }
                await self._handle_callback(cb_dict)
        finally:
            self._active_query = None
    @staticmethod
    def _pricing_text() -> str:
        return (
            "📊 *Subscription Plans (IDR)*\n\n"
            f"📅 *Daily* — Rp {PRICE_MAP['daily']:,}\n"
            f"📆 *Weekly* — Rp {PRICE_MAP['weekly']:,} (save 28%)\n"
            f"🗓 *Monthly* — Rp {PRICE_MAP['monthly']:,} (save 55%)\n\n"
            "Use /subscribe <plan> to start.\n"
            "Example: `/subscribe daily`"
        )
