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
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from tradebot.bots.handlers import register_standard_commands
from tradebot.config import settings
from tradebot.services.plans import get_user_plan

LOG = logging.getLogger("tradebot.bots.telegram")


class UnifiedBot:
    """One Telegram bot to rule them all.

    Registers:
      - Trading commands (/signal, /scan, /symbols, /stats, /cookies)
      - Account commands (/balance, /deposit)
      - Shared commands via register_standard_commands()
        (/plans, /upgrade, /donate, /subscribe, /affiliate, /whitelabel, etc.)
      - Platform routing: auto-detects from user's linked platform
    """

    def __init__(self, token: str | None = None):
        self._token = token or settings.TELEGRAM_BOT_TOKEN
        self._app: Application | None = None
        self._running = False

    # ── Build ──────────────────────────────────────────────────────

    def build(self) -> Application:
        """Build PTB Application with all handlers."""
        app = Application.builder().token(self._token).build()

        # Core commands
        app.add_handler(CommandHandler(["start", "help"], self._h_start))
        app.add_handler(CommandHandler("symbols", self._h_symbols))
        app.add_handler(CommandHandler("signal", self._h_signal))
        app.add_handler(CommandHandler("scan", self._h_scan))
        app.add_handler(CommandHandler("stats", self._h_stats))

        # All shared commands (plans, signals, affiliate, whitelabel, admin)
        register_standard_commands(app)

        # Referral deep link handler
        app.add_handler(CommandHandler("start", self._h_ref_start))

        LOG.info("UnifiedBot built with all handlers")
        return app

    # ── Start / Stop ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling."""
        self._app = self.build()
        self._running = True
        LOG.info("🤖 UnifiedBot starting...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        LOG.info("✅ UnifiedBot running")

        # Keep alive
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            LOG.info("UnifiedBot stopped")

    # ── Command Handlers ───────────────────────────────────────────

    async def _h_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start — welcome message with referral handling."""
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
        await update.message.reply_markdown(text)

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
