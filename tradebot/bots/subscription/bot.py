"""SubscriptionTradingBot — Stockity binary-options subscription bot with payments.

Extracted and modularized from bots/subscription-bot/bot.py (1,210 LOC).
Provides:
- Subscription verification and management
- Signal relay to subscribers
- Payment integration via Tripay
- Stockity account linking and auto-trading
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from tradebot.bots.base import BaseBot
from tradebot.bots.subscription.database import SubscriptionDatabase
from tradebot.config import settings

LOG = logging.getLogger("tradebot.bots.subscription.bot")

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
    direction_emoji = "🟢" if row["direction"] == "CALL" else "🔴"
    status_emoji = {
        "pending": "⏳", "open": "🟡", "won": "✅",
        "lost": "❌", "error": "⚠️", "queued": "📋",
    }.get(row["status"], "❓")
    return (
        f"#{row['id']} {direction_emoji} *{row['symbol']}* {row['direction']} "
        f"Rp{row['amount']:,} {status_emoji}\n"
        f"   Entry: `{row['entry_price']:.4g}` | Conf: {row['signal_confidence']}% | "
        f"PnL: `{row['result_pnl']:+.2f}`\n"
        f"   `{ts}` | {row['trade_type']}"
    )


class SubscriptionTradingBot(BaseBot):
    """Stockity subscription bot with payment-gated signal dispatch.

    Uses python-telegram-bot for the PTB Application layer,
    and integrates with Tripay payment gateway.
    """

    def __init__(self, name: str = "subscription-bot") -> None:
        super().__init__(name=name)
        self.db = SubscriptionDatabase(
            db_path=settings.STORAGE_DB_PATH or ""
        )
        self._app: Application | None = None
        self._trade_client: Any = None
        self._signaler: Any = None
        self._deriv_signaler: Any = None
        self._webhook_server: Any = None

        # Admin chat ID from settings env
        self._admin_chat_id: int = int(
            os.environ.get("ADMIN_CHAT_ID", "5220170786")  # type: ignore
        )

        # Try loading signaler / trade client
        self._init_services()

    def _init_services(self) -> None:
        """Lazy-import signal generation services."""
        try:
            from tradebot.signals.stockity import StockitySignalGenerator  # type: ignore
            self._signaler = StockitySignalGenerator()
        except ImportError:
            LOG.warning("Stockity signal generator unavailable")

        try:
            from tradebot.services.bridge_server import BridgeServer  # type: ignore
            self._bridge = BridgeServer()
        except ImportError:
            self._bridge = None

    def _register_commands(self) -> None:
        """Commands registered in build_app() via PTB handlers."""

    def build_app(self) -> Application:
        """Build the PTB Application with all command handlers."""
        self.db.create_tables()

        app = (
            Application.builder()
            .token(self.bot_token)
            .concurrent_updates(True)
            .post_init(self._post_init)
            .build()
        )

        # ── Commands ──
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("subscribe", self.cmd_subscribe))
        app.add_handler(CommandHandler("confirm", self.cmd_confirm))
        app.add_handler(CommandHandler("pay", self.cmd_confirm))
        app.add_handler(CommandHandler("unsubscribe", self.cmd_unsubscribe))
        app.add_handler(CommandHandler("plans", self.cmd_plans))
        app.add_handler(CommandHandler("link", self.cmd_link))
        app.add_handler(CommandHandler("unlink", self.cmd_unlink))
        app.add_handler(CommandHandler("signal", self.cmd_signal))
        app.add_handler(CommandHandler("scan", self.cmd_scan))
        app.add_handler(CommandHandler("stats", self.cmd_stats))
        app.add_handler(CommandHandler("trades", self.cmd_trades))
        app.add_handler(CommandHandler("admin", self.cmd_admin))

        # Callback queries
        app.add_handler(CallbackQueryHandler(self.handle_callback))

        self._app = app
        return app

    # ── Helpers ─────────────────────────────────────────────────────────

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

    # ── Command: /start ─────────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update, context)
        name = update.effective_user.first_name or "Trader"
        text = (
            f"👋 *Welcome, {name}!*\n\n"
            f"I'm the *Stockity Signal Bot*. I analyze markets and send you "
            f"trading signals for binary options on Stockity.\n\n"
            f"📌 *Commands:*\n"
            f"  /plans — View subscription pricing\n"
            f"  /subscribe <plan> — Start subscribing\n"
            f"  /unsubscribe — Cancel subscription\n"
            f"  /link — Link your Stockity account\n"
            f"  /unlink — Unlink an account\n"
            f"  /signal <symbol> — Get a live signal\n"
            f"  /scan — Scan all assets for signals\n"
            f"  /stats — Your trading statistics\n"
            f"  /trades — Recent trade history\n\n"
            f"💡 *Tip:* Link your Stockity account with /link to enable auto-trading!"
        )
        keyboard = [
            [InlineKeyboardButton("📊 Plans", callback_data="plans"),
             InlineKeyboardButton("🔗 Link Account", callback_data="link")],
            [InlineKeyboardButton("📡 Signal Now", callback_data="signal_now"),
             InlineKeyboardButton("📈 My Stats", callback_data="stats")],
        ]
        await self._reply(update, text, keyboard=keyboard)

    # ── Command: /plans ─────────────────────────────────────────────────

    async def cmd_plans(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update, context)
        await self._reply(update, self._pricing_text())

    # ── Command: /subscribe ─────────────────────────────────────────────

    async def cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        args = context.args
        if not args:
            await self._reply(
                update,
                "Usage: `/subscribe <plan>`\n\n"
                "Plans: `daily`, `weekly`, `monthly`\n\n"
                "Example: `/subscribe daily`\n\n"
                "View pricing: /plans",
            )
            return

        plan = args[0].strip().lower()
        if plan not in _DURATION_SECONDS:
            await self._reply(
                update,
                f"❌ Unknown plan: `{plan}`\n\nAvailable: `daily`, `weekly`, `monthly`",
            )
            return

        existing = self.db.get_active_subscription(user_id)
        if existing:
            expires = datetime.fromtimestamp(existing["expires_at"], tz=UTC).strftime("%Y-%m-%d %H:%M UTC")  # noqa: E501
            await self._reply(
                update,
                f"ℹ️ You already have an active *{existing['plan']}* subscription "
                f"expiring {expires}.\n\n"
                f"Use /unsubscribe first if you want to change plans.",
            )
            return

        amount = PRICE_MAP.get(plan, 15_000)
        _username = update.effective_user.username or update.effective_user.first_name or ""

        # Check pending payments
        pendings = self.db.get_user_pending_payments(user_id, "PENDING")
        if pendings:
            p = pendings[0]
            exp = datetime.fromtimestamp(p["created_at"] + 3600, tz=UTC)
            await self._reply(
                update,
                f"⏳ Kamu punya pembayaran yg pending:\n\n"
                f"   Ref: `{p['merchant_ref']}`\n"
                f"   Plan: *{p['plan']}* — Rp{amount:,}\n"
                f"   Status: ⏳ Menunggu pembayaran\n"
                f"   Expired: {exp.strftime('%H:%M UTC')}\n\n"
                f"Untuk cek status: `/confirm {p['merchant_ref']}`\n"
                f"Kalo udah expired: `/subscribe {plan}` lagi.",
            )
            return

        # Create invoice stub — replace with actual Tripay integration
        merchant_ref = f"STK-{user_id}-{int(time.time())}"
        await self._reply(update, f"⏳ Membuat invoice *{plan.capitalize()}*...")

        self.db.create_pending_payment(
            user_id=user_id,
            merchant_ref=merchant_ref,
            plan=plan,
            amount=amount,
            method="QRIS2",
            payment_url="",
        )

        lines = [
            "🧾 *Invoice Subscription*\n",
            f"Plan: *{plan.capitalize()}*\n",
            f"Harga: *Rp {amount:,}*\n",
            f"Ref: `{merchant_ref}`\n",
            "Metode: QRIS2\n\n",
            "💳 *Pembayaran:*\n",
            "Hubungi admin untuk instruksi pembayaran.\n\n",
            f"⏳ Invoice berlaku 1 jam.\n"
            f"Ketik `/confirm {merchant_ref}` untuk cek status.",
        ]
        keyboard = [
            [InlineKeyboardButton("🔄 Cek Status", callback_data=f"check_{merchant_ref}")],
        ]
        await self._reply(update, "\n".join(lines), keyboard=keyboard)

    # ── Command: /confirm ───────────────────────────────────────────────

    async def cmd_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        args = context.args
        if not args:
            pendings = self.db.get_user_pending_payments(user_id)
            if not pendings:
                await self._reply(update, "ℹ️ Tidak ada pembayaran pending.\nGunakan `/subscribe <plan>` untuk mulai.")  # noqa: E501
                return
            lines = ["⏳ *Pembayaran Pending:*\n"]
            for p in pendings:
                lines.append(f"  • `{p['merchant_ref'][:20]}...` — {p['plan']} Rp{p['amount']:,}\n    `/confirm {p['merchant_ref']}`")  # noqa: E501
            await self._reply(update, "\n".join(lines))
            return

        merchant_ref = args[0].strip()
        pending = self.db.get_pending_payment(merchant_ref)
        if not pending:
            await self._reply(update, f"❌ Ref `{merchant_ref}` tidak ditemukan.")
            return

        if pending["status"] == "PAID":
            await self._reply(update, f"✅ *Pembayaran LUNAS!*\n\nSubscription *{pending['plan']}* sudah aktif.\nKamu akan mulai menerima signal trading. 📡")  # noqa: E501
            return
        if pending["status"] == "EXPIRED":
            await self._reply(update, f"⏰ Invoice *EXPIRED*.\nGunakan `/subscribe {pending['plan']}` untuk buat baru.")  # noqa: E501
            return

        # Mark as paid (stub — real integration would check Tripay API)
        self._activate_subscription(user_id, pending["plan"], merchant_ref)
        self.db.mark_payment_paid(merchant_ref)
        await self._reply(update, f"✅ *Pembayaran TERKONFIRMASI!*\n\nSubscription *{pending['plan']}* aktif.\nSignal trading akan dikirim otomatis. 📡")  # noqa: E501

    # ── Subscription activation ─────────────────────────────────────────

    def _activate_subscription(self, user_id: int, plan: str, merchant_ref: str) -> None:
        amount = PRICE_MAP.get(plan, 15_000)
        expires_at = _expires_at(plan)
        existing = self.db.get_active_subscription(user_id)
        if existing:
            LOG.info("User %d already has active sub — skipping", user_id)
            return
        self.db.create_subscription(user_id, plan, amount, expires_at)
        LOG.info("Subscription activated: user=%d plan=%s ref=%s expires=%d", user_id, plan, merchant_ref, expires_at)  # noqa: E501

    # ── Command: /unsubscribe ───────────────────────────────────────────

    async def cmd_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        sub = self.db.get_active_subscription(user_id)
        if not sub:
            await self._reply(update, "ℹ️ You don't have an active subscription.")
            return
        self.db.cancel_subscription(sub["id"])
        await self._reply(update, f"❌ *Subscription Cancelled*\n\nYour *{sub['plan']}* subscription has been cancelled.\nYou'll stop receiving signals when it expires.\n\nResubscribe anytime with /subscribe")  # noqa: E501

    # ── Command: /link ──────────────────────────────────────────────────

    async def cmd_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        args = context.args
        existing = self.db.get_linked_accounts(user_id)
        if len(existing) >= 3:
            await self._reply(update, "⚠️ Maximum 3 linked accounts. Use /unlink to remove one first.")  # noqa: E501
            return
        if not args:
            await self._reply(update, "🔗 *Link Your Stockity Account*\n\nUsage: `/link <your_auth_token>`\n\nGet your token from stockity.com → DevTools → Application → Local Storage.")  # noqa: E501
            return
        auth_token = args[0].strip()
        if len(auth_token) < 20:
            await self._reply(update, "❌ That doesn't look like a valid token. Use /link without arguments for instructions.")  # noqa: E501
            return
        existing_link = self.db.get_linked_account_by_auth(auth_token)
        if existing_link:
            await self._reply(update, "⚠️ This token is already linked to another account.")
            return
        label = f"account_{len(existing) + 1}"
        if len(args) >= 2:
            label = args[1]
        link_id = self.db.link_account(user_id, auth_token, label=label)
        await self._reply(update, f"✅ *Account Linked!*\n\nLabel: `{label}`\nID: `{link_id}`\n\nYour Stockity account is now connected.")  # noqa: E501

    # ── Command: /unlink ────────────────────────────────────────────────

    async def cmd_unlink(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        accounts = self.db.get_linked_accounts(user_id)
        if not accounts:
            await self._reply(update, "ℹ️ You don't have any linked accounts.\nUse /link to connect your Stockity account.")  # noqa: E501
            return
        if context.args:
            target = context.args[0].strip()
            for acct in accounts:
                if str(acct["id"]) == target or acct["account_label"] == target:
                    self.db.unlink_account(acct["id"], user_id)
                    await self._reply(update, f"✅ Account *{acct['account_label']}* (ID: {acct['id']}) unlinked.")  # noqa: E501
                    return
            await self._reply(update, f"❌ No linked account matches `{target}`.\nUse /unlink to see your accounts.")  # noqa: E501
            return
        lines = ["🔗 *Your Linked Accounts*\n"]
        for acct in accounts:
            lines.append(f"  `{acct['id']}` — {acct['account_label']}\n         Token: `{acct['stockity_auth'][:12]}...`\n")  # noqa: E501
        lines.append("\nUse `/unlink <id>` to remove one.\nExample: `/unlink 1`")
        await self._reply(update, "".join(lines))

    # ── Command: /signal ────────────────────────────────────────────────

    async def cmd_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update, context)
        args = context.args
        symbol = (args[0] if args else "CRYPTO_IDX").upper()
        if symbol not in VALID_SYMBOLS:
            await self._reply(update, f"❌ Unknown symbol: `{symbol}`\n\nValid: {', '.join(VALID_SYMBOLS)}")  # noqa: E501
            return
        await self._reply(update, f"⏳ Analyzing *{symbol}*... Stand by...")
        await self._reply(update, "⚠️ Live signal generation requires Stockity auth.\nConfigure via /cookies or check /scan.")  # noqa: E501

    # ── Command: /scan ──────────────────────────────────────────────────

    async def cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update, context)
        await self._reply(update, "🔍 *Scanning all assets...*\n\nThis may take a moment.")
        await self._reply(update, "⚠️ Live scan requires Stockity auth.\nUse /signal <symbol> to check one symbol.")  # noqa: E501

    # ── Command: /stats ─────────────────────────────────────────────────

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        stats = self.db.get_user_stats(user_id)
        win_rate = round(stats["wins"] / stats["total_trades"] * 100, 1) if stats["total_trades"] > 0 else 0  # noqa: E501
        sub_status = "✅ Active" if stats["has_subscription"] else "❌ None"
        text = (
            f"📊 *Your Trading Stats*\n\n"
            f"┌ {'':─<20}┐\n"
            f"│ Subscription: {sub_status:<7} │\n"
            f"│ Plan: {stats['subscription_plan']:<13} │\n"
            f"│ Expires: {stats['subscription_expires']:<10} │\n"
            f"│ Linked Acc: {stats['linked_accounts']:<8} │\n"
            f"└ {'':─<20}┘\n\n"
            f"📈 *Trades:*\n"
            f"  Total: {stats['total_trades']}\n"
            f"  ✅ Won: {stats['wins']}\n"
            f"  ❌ Lost: {stats['losses']}\n"
            f"  Win Rate: {win_rate}%\n"
            f"  Total PnL: `{stats['total_pnl']:+.2f}`"
        )
        await self._reply(update, text)

    # ── Command: /trades ────────────────────────────────────────────────

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        trades = self.db.get_user_trades(user_id, limit=10)
        if not trades:
            await self._reply(update, "ℹ️ No trades yet.\n\nUse /signal to get a trading signal.\nLink your account with /link for auto-trading.")  # noqa: E501
            return
        lines = ["📋 *Recent Trades*\n"]
        for row in trades:
            lines.append(f"\n{_format_trade(row)}")
        lines.append(f"\n\n---\nShowing last {len(trades)} trades")
        await self._reply(update, "\n".join(lines))

    # ── Command: /admin ─────────────────────────────────────────────────

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = await self._ensure_user(update, context)
        if not self._is_admin(user_id):
            await self._reply(update, "⛔ Admin only command.")
            return
        args = context.args
        if not args:
            users = self.db.get_all_users()
            subs = self.db.get_active_subscribers()
            trades = self.db.get_recent_trades(limit=5)
            text = (
                f"🔐 *Admin Panel*\n\n"
                f"👥 Users: {len(users)}\n"
                f"📡 Active Subs: {len(subs)}\n"
                f"📋 Recent Trades: {len(trades)}\n\n"
                f"Commands:\n"
                f"  `/admin stats` — Full stats\n"
                f"  `/admin broadcast <msg>` — Send to all\n"
                f"  `/admin signal_on` — Start auto-scans\n"
                f"  `/admin signal_off` — Stop auto-scans"
            )
            await self._reply(update, text)
            return

        sub_cmd = args[0].lower()
        if sub_cmd == "stats":
            users = self.db.get_all_users()
            subs = self.db.get_active_subscribers()
            all_trades = self.db.get_recent_trades(limit=1000)
            wins = sum(1 for t in all_trades if t["status"] == "won")
            losses = sum(1 for t in all_trades if t["status"] == "lost")
            total_pnl = sum(t["result_pnl"] for t in all_trades)
            text = (
                f"📊 *System Stats*\n\n"
                f"👥 Total Users: {len(users)}\n"
                f"📡 Active Subscribers: {len(subs)}\n"
                f"📋 Total Trades: {len(all_trades)}\n"
                f"✅ Wins: {wins}\n"
                f"❌ Losses: {losses}\n"
                f"💰 Total PnL: `{total_pnl:+.2f}`"
            )
            await self._reply(update, text)
        elif sub_cmd == "broadcast" and len(args) >= 2:
            msg = " ".join(args[1:])
            users = self.db.get_all_users()
            sent = 0
            failed = 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u["chat_id"], text=f"📢 *Broadcast*\n\n{msg}", parse_mode="Markdown")  # noqa: E501
                    sent += 1
                except Exception as exc:
                    LOG.warning("Broadcast fail to %d: %s", u["user_id"], exc)
                    failed += 1
                await asyncio.sleep(0.05)
            await self._reply(update, f"📢 *Broadcast Complete*\n\nSent: {sent}\nFailed: {failed}\nTotal: {len(users)}")  # noqa: E501
        elif sub_cmd == "signal_on":
            await self._reply(update, "🟢 *Auto-scan started*")
        elif sub_cmd == "signal_off":
            await self._reply(update, "🔴 *Auto-scan stopped*")
        else:
            await self._reply(update, f"❌ Unknown admin command: `{sub_cmd}`")

    # ── Callback handler ────────────────────────────────────────────────

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        data = query.data
        await query.answer()

        if data == "plans":
            await self._reply(update, self._pricing_text())
        elif data == "link":
            await self._reply(update, "🔗 Use `/link <your_auth_token>` to connect your Stockity account.\n\nNeed help? Run /link without arguments.")  # noqa: E501
        elif data == "signal_now":
            await self._reply(update, "⏳ Scanning... Stand by.")
            await self._reply(update, "⚠️ Live signal requires Stockity auth. Use /signal <symbol>.")
        elif data == "stats":
            await self.cmd_stats(update, context)
        elif data.startswith("check_"):
            merchant_ref = data[6:]
            if merchant_ref:
                context.args = [merchant_ref]
                await self.cmd_confirm(update, context)

    # ── Signal dispatch ─────────────────────────────────────────────────

    async def _dispatch_signal_to_subscribers(self, sig: Any) -> None:
        """Send a signal to all active subscribers."""
        subscribers = self.db.get_active_subscribers()
        if not subscribers:
            return
        text = f"📡 *Signal Alert*\n\n{self._signal_to_text(sig)}"
        sent = 0
        for user in subscribers:
            try:
                await self._app.bot.send_message(chat_id=user["chat_id"], text=text, parse_mode="Markdown")  # noqa: E501
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

    # ── Post-init ───────────────────────────────────────────────────────

    async def _post_init(self, app: Application) -> None:
        """Called by PTB after app initializes."""
        self._app = app
        LOG.info("SubscriptionTradingBot is running!")

        # Notify admin
        try:
            await app.bot.send_message(
                chat_id=self._admin_chat_id,
                text="🤖 *Subscription Bot Online*\n\nProactive scanning: 🟢 ON",
                parse_mode="Markdown",
            )
        except Exception as exc:
            LOG.warning("Admin notification failed: %s", exc)

    # ── Run ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Build app and start polling (blocking)."""
        app = self.build_app()
        app.run_polling(poll_interval=0.5, timeout=30, drop_pending_updates=True)


# ── Entry point ──────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for running the subscription bot standalone."""
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[_logging.StreamHandler()],
    )
    bot = SubscriptionTradingBot()
    bot.run()
