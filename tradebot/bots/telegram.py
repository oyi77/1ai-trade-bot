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
from tradebot.bots.platforms.vilona.commands import register_vilona_commands
from tradebot.storage.subscription import SubscriptionDatabase
from tradebot.bots.platforms.vilona.bot import VilonaBot

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
        """Build PTB Application with all handlers."""
        self.db.create_tables()

        app = Application.builder().token(self._token).build()

        # Core commands
        app.add_handler(CommandHandler(["start", "help"], self._h_start))
        app.add_handler(CommandHandler("symbols", self._h_symbols))
        app.add_handler(CommandHandler("signal", self._h_signal))
        app.add_handler(CommandHandler("scan", self._h_scan))
        app.add_handler(CommandHandler("stats", self._h_stats))

        # Account linking
        app.add_handler(CommandHandler("link", self._h_link))
        app.add_handler(CommandHandler("unlink", self._h_unlink))

        # All shared commands (plans, signals, affiliate, whitelabel, admin)
        register_standard_commands(app)

        # Register Vilona-specific commands (Signal system, Market data, Trading tools, Admin)
        register_vilona_commands(app, self)

        # Referral deep link handler
        app.add_handler(CommandHandler("start", self._h_ref_start))

        # Callback queries
        app.add_handler(CallbackQueryHandler(self._h_callback))

        LOG.info("UnifiedBot built with all handlers")
        return app

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
                BotCommand("signal", "🧠 Generate sinyal MTF + 9 engines"),
                BotCommand("mtf", "🧬 Matrix 5TF × 9 engines (top-down)"),
                BotCommand("engines", "🔧 Engine readings per strategi"),
                BotCommand("dashboard", "📊 Buka live dashboard web"),
                BotCommand("analyze", "🧠 Perintahkan AI Scan Market"),
                BotCommand("price", "💰 Cek harga real-time"),
                BotCommand("mapping", "📐 Mapping harian + level S/R"),
                BotCommand("levels", "🏛 SnR + FIBO + Engine (Subscriber)"),
                BotCommand("news", "📰 Grok News — X/Twitter intel (Subscriber)"),
                BotCommand("killzone", "🎯 Radar sesi market aktif"),
                BotCommand("zones", "🧲 Order Blocks + FVG Scanner"),
                BotCommand("structure", "🏗 BOS/CHoCH + MTF Alignment"),
                BotCommand("stier", "💀 S-TIER Zone — Triple Confluence GOD TIER"),
                BotCommand("subscribe", "⭐ Upgrade ke PRO/ELITE/LIFETIME"),
                BotCommand("status", "🛡 Cek Kuota & Status"),
                BotCommand("mykey", "🔑 Cek License EA Kamu"),
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

    async def _h_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/signal <symbol> — generate signal for a symbol."""
        args = context.args or []
        syms = getattr(settings, "SYMBOLS", "CRYPTO_IDX")
        symbol_list = [s.strip() for s in syms.split(",") if s.strip()]
        symbol = args[0].strip().upper() if args else (symbol_list[0] if symbol_list else "CRYPTO_IDX")

        msg = await update.message.reply_text(f"🔍 Analyzing `{symbol}`...")

        try:
            from tradebot.brokers.stockity.broker import StockityBroker
            from tradebot.signals.stockity import StockitySource

            async with StockitySource() as src:
                ticks = await src.fetch_ticks(symbol.split("_")[0] if "_" in symbol else symbol)
                if not ticks:
                    await msg.edit_text(f"❌ No data for `{symbol}`", parse_mode="Markdown")
                    return

            broker = StockityBroker()
            async with broker:
                balance = await broker.get_balance()
                pos = broker.open_positions

            # Run engines
            from tradebot.engines.registry import Registry
            reg = Registry()
            engines = reg.discover()
            signals_found = []

            for name, engine in engines.items():
                try:
                    result = engine.analyze(ticks)
                    if result:
                        signals_found.append(f"  • {name}: {result.direction} ({result.confidence:.0%})")
                except Exception:
                    pass

            lines = [f"📡 *Signal — {symbol}*\n"]
            if signals_found:
                lines.append("*Engines:*")
                lines.extend(signals_found[:10])
            else:
                lines.append("_No strong signals detected_")
            lines.append(f"\n💰 Balance: {balance or 'N/A'}")
            if pos:
                lines.append(f"📊 Open positions: {len(pos)}")

            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}", parse_mode="Markdown")

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

    async def _h_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        args = context.args
        existing = self.db.get_linked_accounts(user_id)
        if len(existing) >= 3:
            await self._reply(update, "⚠️ Maximum 3 linked accounts. Use /unlink to remove one first.")
            return
        if not args:
            await self._reply(
                update,
                "🔗 *Link Your Stockity Account*\n\n"
                "Usage: `/link <your_auth_token>`\n\n"
                "Get your token from stockity.com → DevTools → Application → Local Storage.",
            )
            return
        auth_token = args[0].strip()
        if len(auth_token) < 20:
            await self._reply(
                update,
                "❌ That doesn't look like a valid token. Use /link without arguments for instructions.",
            )
            return
        existing_link = self.db.get_linked_account_by_auth(auth_token)
        if existing_link:
            await self._reply(update, "⚠️ This token is already linked to another account.")
            return
        label = f"account_{len(existing) + 1}"
        if len(args) >= 2:
            label = args[1]
        link_id = self.db.link_account(user_id, auth_token, label=label)
        await self._reply(
            update,
            f"✅ *Account Linked!*\n\nLabel: `{label}`\nID: `{link_id}`\n\nYour Stockity account is now connected.",
        )

    # ── Command: /unlink ─────────────────────────────────────────────────

    async def _h_unlink(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        accounts = self.db.get_linked_accounts(user_id)
        if not accounts:
            await self._reply(
                update,
                "ℹ️ You don't have any linked accounts.\nUse /link to connect your Stockity account.",
            )
            return
        if context.args:
            target = context.args[0].strip()
            for acct in accounts:
                if str(acct["id"]) == target or acct["account_label"] == target:
                    self.db.unlink_account(acct["id"], user_id)
                    await self._reply(
                        update,
                        f"✅ Account *{acct['account_label']}* (ID: {acct['id']}) unlinked.",
                    )
                    return
            await self._reply(
                update,
                f"❌ No linked account matches `{target}`.\nUse /unlink to see your accounts.",
            )
            return
        lines = ["🔗 *Your Linked Accounts*\n"]
        for acct in accounts:
            lines.append(
                f"  `{acct['id']}` — {acct['account_label']}\n"
                f"         Token: `{acct['stockity_auth'][:12]}...`\n"
            )
        lines.append("\nUse `/unlink <id>` to remove one.\nExample: `/unlink 1`")
        await self._reply(update, "".join(lines))

    # ── Signal dispatch ──────────────────────────────────────────────────

    async def _dispatch_signal_to_subscribers(self, sig: Any) -> None:
        """Send a signal to all active subscribers (category dispatch)."""
        subscribers = self.db.get_active_subscribers()
        if not subscribers:
            return
        text = f"📡 *Signal Alert*\n\n{self._signal_to_text(sig)}"
        sent = 0
        for user in subscribers:
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
