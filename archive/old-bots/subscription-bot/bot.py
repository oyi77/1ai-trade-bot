"""
Stockity Signal Subscription Telegram Bot

Commands:
  /start       — Welcome + registration
  /subscribe   — Start a subscription
  /unsubscribe — Cancel subscription
  /plans       — Show pricing plans
  /link        — Link your Stockity account
  /unlink      — Unlink a Stockity account
  /signal      — Get a live signal now
  /scan        — Scan all symbols for signals
  /stats       — Your trading stats
  /trades      — Your recent trade history
  /admin       — Admin broadcast (admin only)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Add project root so we can import core & signals
# archive/old-bots/subscription-bot/../../.. = project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # archive/old-bots/subscription-bot/../../.. = project root
sys.path.insert(0, str(_PROJECT_ROOT))

from core import Signal
from config import Config
from database import Database
from trade_client import TradeClient, TradeOrder, Direction
from signaler import ProactiveSignaler, DerivSignaler
import payment as tripay

LOG = logging.getLogger("subscription_bot.bot")


# ── Duration helpers ────────────────────────────────────────────────────────

_DURATION_SECONDS = {
    "daily": 86400,
    "weekly": 86400 * 7,
    "monthly": 86400 * 30,
}


def _expires_at(plan: str) -> int:
    delta = _DURATION_SECONDS.get(plan, 86400)
    return int(time.time()) + delta


def _signal_to_text(sig: Signal) -> str:
    return sig.pretty()


def _format_trade(row) -> str:
    ts = datetime.fromtimestamp(row["created_at"], tz=timezone.utc).strftime(
        "%m/%d %H:%M"
    )
    direction_emoji = "🟢" if row["direction"] == "CALL" else "🔴"
    status_emoji = {
        "pending": "⏳",
        "open": "🟡",
        "won": "✅",
        "lost": "❌",
        "error": "⚠️",
        "queued": "📋",
    }.get(row["status"], "❓")
    return (
        f"#{row['id']} {direction_emoji} *{row['symbol']}* {row['direction']} "
        f"Rp{row['amount']:,} {status_emoji}\n"
        f"   Entry: `{row['entry_price']:.4g}` | Conf: {row['signal_confidence']}% | "
        f"PnL: `{row['result_pnl']:+.2f}`\n"
        f"   `{ts}` | {row['trade_type']}"
    )


# ── Bot ─────────────────────────────────────────────────────────────────────

class SubscriptionBot:
    """Main bot class — wires all components together."""

    def __init__(self):
        self.db = Database()
        self.trade_client = TradeClient(
            master_auth_token=Config.STOCKITY_AUTHTOKEN,
        )
        self.signaler = ProactiveSignaler(
            db=self.db,
            trade_client=self.trade_client,
            authtoken=Config.STOCKITY_AUTHTOKEN,
            cookie=Config.STOCKITY_FULL_COOKIE,
        )
        try:
            self.deriv_signaler = DerivSignaler(
                db=self.db,
                trade_client=self.trade_client,
            )
        except Exception as exc:
            LOG.warning("DerivSignaler init skipped: %s", exc)
            self.deriv_signaler = None
        self._app: Optional[Application] = None

        # Wire Tripay payment handler
        tripay.set_payment_handler(self._on_payment_success)

        # Webhook server
        self._webhook_server = None

    # ── Setup ───────────────────────────────────────────────────────────

    def build_app(self) -> Application:
        """Build the PTB Application with all handlers."""
        self.db.create_tables()

        app = (
            Application.builder()
            .token(Config.TELEGRAM_BOT_TOKEN)
            .concurrent_updates(True)
            .post_init(self._post_init)
            .build()
        )

        # ── Commands ────────────────────────────────────────────────────
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

        # Callback queries for inline keyboards
        app.add_handler(CallbackQueryHandler(self.handle_callback))

        self._app = app
        return app

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _ensure_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Register/update user in DB. Returns user_id."""
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
        return user and (user["is_admin"] == 1 or user_id == Config.ADMIN_CHAT_ID)

    async def _reply(
        self,
        update: Update,
        text: str,
        keyboard: Optional[list[list[InlineKeyboardButton]]] = None,
        parse_mode: str = "Markdown",
    ):
        """Reply to a message (supports both message & callback query)."""
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        elif update.message:
            await update.message.reply_text(
                text=text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        else:
            # Fallback: try context.bot.send_message
            try:
                chat_id = update.effective_chat.id
                await update.get_bot().send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
            except Exception:
                LOG.warning("Could not reply — no chat context")

    # ── Command: /start ─────────────────────────────────────────────────

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
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
            [
                InlineKeyboardButton("📊 Plans", callback_data="plans"),
                InlineKeyboardButton("🔗 Link Account", callback_data="link"),
            ],
            [
                InlineKeyboardButton("📡 Signal Now", callback_data="signal_now"),
                InlineKeyboardButton("📈 My Stats", callback_data="stats"),
            ],
        ]
        await self._reply(update, text, keyboard=keyboard)

    # ── Command: /plans ─────────────────────────────────────────────────

    async def cmd_plans(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        await self._ensure_user(update, context)
        await self._reply(update, Config.pricing_text())

    # ── Command: /subscribe ─────────────────────────────────────────────

    async def cmd_subscribe(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)

        args = context.args
        if not args:
            text = (
                "Usage: `/subscribe <plan>`\n\n"
                "Plans: `daily`, `weekly`, `monthly`\n\n"
                "Example: `/subscribe daily`\n\n"
                "View pricing: /plans"
            )
            await self._reply(update, text)
            return

        plan = args[0].strip().lower()
        if plan not in _DURATION_SECONDS:
            await self._reply(
                update,
                f"❌ Unknown plan: `{plan}`\n\n"
                "Available: `daily`, `weekly`, `monthly`",
            )
            return

        # Check for existing active subscription
        existing = self.db.get_active_subscription(user_id)
        if existing:
            expires = datetime.fromtimestamp(
                existing["expires_at"], tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            await self._reply(
                update,
                f"ℹ️ You already have an active *{existing['plan']}* subscription "
                f"expiring {expires}.\n\n"
                f"Use /unsubscribe first if you want to change plans.",
            )
            return

        # Pricing
        prices = {
            "daily": Config.PRICE_DAILY,
            "weekly": Config.PRICE_WEEKLY,
            "monthly": Config.PRICE_MONTHLY,
        }
        amount = prices[plan]
        username = update.effective_user.username or update.effective_user.first_name or ""

        # Check pending payment yg belum expire
        pendings = self.db.get_user_pending_payments(user_id, "PENDING")
        if pendings:
            # Ada yg pending — tampilkan
            p = pendings[0]
            exp = datetime.fromtimestamp(p["created_at"] + 3600, tz=timezone.utc)
            await self._reply(
                update,
                f"⏳ Kamu punya pembayaran yg pending:\\n\\n"
                f"   Ref: `{p['merchant_ref']}`\\n"
                f"   Plan: *{p['plan']}* — Rp{amount:,}\\n"
                f"   Status: ⏳ Menunggu pembayaran\\n"
                f"   Expired: {exp.strftime('%H:%M UTC')}\\n\\n"
                f"Untuk cek status: `/confirm {p['merchant_ref']}`\\n"
                f"Kalo udah expired: `/subscribe {plan}` lagi.",
            )
            return

        # ── Create Tripay invoice ──
        await self._reply(
            update,
            f"⏳ Membuat invoice *{plan.capitalize()}*...",
        )

        result = tripay.create_invoice(user_id, username, plan, amount)

        if not result.get("success"):
            err = result.get("error", "Unknown error")
            LOG.error("Tripay create_invoice failed: %s", err)
            await self._reply(
                update,
                f"❌ Gagal buat pembayaran: `{err}`\\n\\n"
                f"Coba lagi nanti ya Bro.",
            )
            return

        # Extract payment data
        data = result.get("data", {})
        merchant_ref = data.get("merchant_ref", "")
        payment_url = data.get("checkout_url", "") or data.get("pay_code", "")
        qr_url = data.get("qr_url", "")
        instructions = data.get("instructions", [])
        channel = data.get("payment_name", "QRIS")

        # Store pending payment in DB
        self.db.create_pending_payment(
            user_id=user_id,
            merchant_ref=merchant_ref,
            plan=plan,
            amount=amount,
            method=channel,
            payment_url=payment_url,
        )

        # Build payment message
        lines = [
            f"🧾 *Invoice Subscription*\\n",
            f"Plan: *{plan.capitalize()}*\\n",
            f"Harga: *Rp {amount:,}*\\n",
            f"Ref: `{merchant_ref}`\\n",
            f"Metode: {channel}\\n",
            f"\\n",
            f"💳 *Pembayaran:*\\n",
            f"[Bayar Sekarang]({payment_url})\\n",
        ]
        if qr_url:
            lines.append(f"![QRIS]({qr_url})\\n")
        if instructions:
            lines.append(f"\\n📋 *Cara Bayar:*\\n")
            for i, inst in enumerate(instructions[:5], 1):
                title = inst.get("title", f"Langkah {i}")
                steps = inst.get("steps", [])
                lines.append(f"  *{i}. {title}*")
                for step in steps[:3]:
                    lines.append(f"     {step}")

        lines.append(
            f"\\n⏳ Invoice berlaku 1 jam.\\n"
            f"Ketik `/confirm {merchant_ref}` untuk cek status."
        )

        keyboard = [
            [InlineKeyboardButton("💳 Bayar Sekarang", url=payment_url)],
            [InlineKeyboardButton("🔄 Cek Status", callback_data=f"check_{merchant_ref}")],
        ]

        await self._reply(update, "\n".join(lines), keyboard=keyboard)

    # ── Command: /confirm ────────────────────────────────────────────

    async def cmd_confirm(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Check payment status."""
        user_id = await self._ensure_user(update, context)
        args = context.args

        if not args:
            # Show user's pending payments
            pendings = self.db.get_user_pending_payments(user_id)
            if not pendings:
                await self._reply(
                    update,
                    "ℹ️ Tidak ada pembayaran pending.\\n"
                    "Gunakan `/subscribe <plan>` untuk mulai.",
                )
                return
            lines = ["⏳ *Pembayaran Pending:*\\n"]
            for p in pendings:
                lines.append(
                    f"  • `{p['merchant_ref'][:20]}...` — {p['plan']} Rp{p['amount']:,}\\n"
                    f"    `/confirm {p['merchant_ref']}`"
                )
            await self._reply(update, "\n".join(lines))
            return

        merchant_ref = args[0].strip()
        pending = self.db.get_pending_payment(merchant_ref)
        if not pending:
            await self._reply(
                update,
                f"❌ Ref `{merchant_ref}` tidak ditemukan.",
            )
            return

        if pending["status"] == "PAID":
            await self._reply(
                update,
                f"✅ *Pembayaran LUNAS!*\\n\\n"
                f"Subscription *{pending['plan']}* sudah aktif.\\n"
                f"Kamu akan mulai menerima signal trading. 📡",
            )
            return

        if pending["status"] == "EXPIRED":
            await self._reply(
                update,
                f"⏰ Invoice *EXPIRED*.\\n"
                f"Gunakan `/subscribe {pending['plan']}` untuk buat baru.",
            )
            return

        # Check with Tripay API
        await self._reply(
            update,
            f"⏳ Cek status pembayaran...",
        )

        if tripay.is_paid(merchant_ref):
            # Activate!
            self._activate_subscription(user_id, pending["plan"], merchant_ref)
            self.db.mark_payment_paid(merchant_ref)
            await self._reply(
                update,
                f"✅ *Pembayaran TERKONFIRMASI!*\\n\\n"
                f"Subscription *{pending['plan']}* aktif.\\n"
                f"Signal trading akan dikirim otomatis. 📡",
            )
        else:
            await self._reply(
                update,
                f"⏳ Pembayaran *belum* terkonfirmasi.\\n"
                f"Silakan selesaikan pembayaran dulu.\\n\\n"
                f"Ketik `/confirm {merchant_ref}` lagi nanti.",
            )

    # ── Payment success handler ──────────────────────────────────────

    def _on_payment_success(self, user_id: int, plan: str, merchant_ref: str):
        """Called by webhook server when Tripay confirms payment."""
        LOG.info("Payment success: user=%d plan=%s ref=%s", user_id, plan, merchant_ref)

        # Mark payment as PAID
        self.db.mark_payment_paid(merchant_ref)

        # Activate subscription
        self._activate_subscription(user_id, plan, merchant_ref)

        # Notify user via Telegram
        try:
            asyncio.create_task(self._notify_payment_success(user_id, plan))
        except Exception as exc:
            LOG.warning("Failed to notify user %d: %s", user_id, exc)

    def _activate_subscription(self, user_id: int, plan: str, merchant_ref: str):
        """Activate subscription after payment confirmed."""
        amount = {
            "daily": Config.PRICE_DAILY,
            "weekly": Config.PRICE_WEEKLY,
            "monthly": Config.PRICE_MONTHLY,
        }.get(plan, Config.PRICE_DAILY)
        expires_at = _expires_at(plan)

        # Cek kalo udah ada active subscription
        existing = self.db.get_active_subscription(user_id)
        if existing:
            LOG.info("User %d already has active sub — skipping", user_id)
            return

        self.db.create_subscription(user_id, plan, amount, expires_at)
        LOG.info("Subscription activated: user=%d plan=%s ref=%s expires=%d",
                 user_id, plan, merchant_ref, expires_at)

    async def _notify_payment_success(self, user_id: int, plan: str):
        """Send payment success notification to user."""
        chat_id = user_id  # user_id == chat_id for Telegram DMs
        try:
            # Get user's actual chat_id
            user = self.db.get_user(user_id)
            chat_id = user["chat_id"] if user else user_id

            await self._app.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ *Pembayaran BERHASIL!* 🎉\\n\\n"
                    f"Subscription *{plan.capitalize()}* kamu sekarang *AKTIF*.\\n"
                    f"\\n"
                    f"📡 Signal trading akan dikirim otomatis.\\n"
                    f"🔗 Link akun Stockity dengan /link untuk auto-trade.\\n"
                    f"\\n"
                    f"Terima kasih sudah mendukung! 🙌"
                ),
                parse_mode="Markdown",
            )
        except Exception as exc:
            LOG.warning("Failed to send payment notification to %d: %s", user_id, exc)

    # ── Command: /unsubscribe ───────────────────────────────────────────

    async def cmd_unsubscribe(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
        sub = self.db.get_active_subscription(user_id)
        if not sub:
            await self._reply(
                update,
                "ℹ️ You don't have an active subscription.",
            )
            return

        self.db.cancel_subscription(sub["id"])
        await self._reply(
            update,
            "❌ *Subscription Cancelled*\n\n"
            f"Your *{sub['plan']}* subscription has been cancelled.\n"
            f"You'll stop receiving signals when it expires.\n\n"
            f"Resubscribe anytime with /subscribe",
        )

    # ── Command: /link ──────────────────────────────────────────────────

    async def cmd_link(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
        args = context.args

        existing = self.db.get_linked_accounts(user_id)
        if len(existing) >= 3:
            await self._reply(
                update,
                "⚠️ Maximum 3 linked accounts. Use /unlink to remove one first.",
            )
            return

        if not args:
            text = (
                "🔗 *Link Your Stockity Account*\n\n"
                "To link your Stockity account for auto-trading, send your "
                "Stockity auth token.\n\n"
                "Usage: `/link <your_auth_token>`\n\n"
                "How to get your token:\n"
                "1. Go to stockity.com and login\n"
                "2. Open browser DevTools (F12)\n"
                "3. Find the `authtoken` from Application → Local Storage\n"
                "4. Copy and paste it here\n\n"
                "Your token is stored securely and used only for your trades."
            )
            await self._reply(update, text)
            return

        auth_token = args[0].strip()
        if len(auth_token) < 20:
            await self._reply(
                update,
                "❌ That doesn't look like a valid token. "
                "It should be a long string (60+ characters).\n"
                "Use /link without arguments for instructions.",
            )
            return

        # Check if this token is already linked (by any user)
        existing_link = self.db.get_linked_account_by_auth(auth_token)
        if existing_link:
            await self._reply(
                update,
                "⚠️ This token is already linked to another account. "
                "Each token can only be linked once.",
            )
            return

        label = f"account_{len(existing) + 1}"
        if len(args) >= 2:
            label = args[1]

        link_id = self.db.link_account(user_id, auth_token, label=label)
        await self._reply(
            update,
            f"✅ *Account Linked!*\n\n"
            f"Label: `{label}`\n"
            f"ID: `{link_id}`\n\n"
            f"Your Stockity account is now connected. "
            f"High-confidence signals will be auto-traded on this account.\n\n"
            f"Use /unlink to remove it anytime.\n"
            f"Use /trades to see your trade history.",
        )

    # ── Command: /unlink ────────────────────────────────────────────────

    async def cmd_unlink(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
        accounts = self.db.get_linked_accounts(user_id)

        if not accounts:
            await self._reply(
                update,
                "ℹ️ You don't have any linked accounts.\n"
                "Use /link to connect your Stockity account.",
            )
            return

        if context.args:
            # Try to unlink by ID or label
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
                f"❌ No linked account matches `{target}`.\n"
                f"Use /unlink to see your accounts.",
            )
            return

        # Show list
        lines = ["🔗 *Your Linked Accounts*\n"]
        for acct in accounts:
            lines.append(
                f"  `{acct['id']}` — {acct['account_label']}\n"
                f"         Token: `{acct['stockity_auth'][:12]}...`\n"
            )
        lines.append(
            "\nUse `/unlink <id>` to remove one.\n"
            "Example: `/unlink 1`"
        )
        await self._reply(update, "".join(lines))

    # ── Command: /signal ────────────────────────────────────────────────

    async def cmd_signal(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
        args = context.args

        symbol = (args[0] if args else "CRYPTO_IDX").upper()
        valid_symbols = [
            "CRYPTO_IDX",
            "BTCUSD",
            "ETHUSD",
            "XAUUSD",
            "USOIL",
            "EURUSD",
            "GBPUSD",
            "USDJPY",
        ]

        if symbol not in valid_symbols:
            await self._reply(
                update,
                f"❌ Unknown symbol: `{symbol}`\n\n"
                f"Valid: {', '.join(valid_symbols)}",
            )
            return

        await self._reply(
            update, f"⏳ Analyzing *{symbol}*... Stand by..."
        )

        sig = await self.signaler._generator.generate(symbol)
        if sig is None:
            await self._reply(
                update,
                f"⚠️ Could not generate signal for *{symbol}*. "
                f"Check that Stockity auth is configured.",
            )
            return

        text = _signal_to_text(sig)
        keyboard = None
        if sig.is_tradeable and sig.confidence >= Config.MIN_CONFIDENCE:
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"🟢 Trade CALL" if sig.action == "CALL" else "🔴 Trade PUT",
                        callback_data=f"trade_{sig.action}_{symbol}",
                    )
                ]
            ]

        await self._reply(update, text, keyboard=keyboard)

    # ── Command: /scan ──────────────────────────────────────────────────

    async def cmd_scan(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)

        await self._reply(
            update, "🔍 *Scanning all assets...*\n\nThis may take a moment."
        )

        signals = await self.signaler.manual_scan()
        if not signals:
            await self._reply(
                update,
                "⚠️ No signals generated. Check Stockity auth configuration.",
            )
            return

        lines = ["📡 *Market Scan Results*\n"]
        tradeable = []
        for sig in signals:
            lines.append(f"\n{_signal_to_text(sig)}")
            if sig.is_tradeable and sig.confidence >= Config.MIN_CONFIDENCE:
                tradeable.append(sig)

        lines.append(
            f"\n---\n"
            f"Scanned: {len(signals)} symbols\n"
            f"Tradeable: {len(tradeable)}"
        )
        await self._reply(update, "".join(lines))

    # ── Command: /stats ─────────────────────────────────────────────────

    async def cmd_stats(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
        stats = self.db.get_user_stats(user_id)

        win_rate = 0
        if stats["total_trades"] > 0:
            win_rate = round(stats["wins"] / stats["total_trades"] * 100, 1)

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

    async def cmd_trades(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = await self._ensure_user(update, context)
        trades = self.db.get_user_trades(user_id, limit=10)

        if not trades:
            await self._reply(
                update,
                "ℹ️ No trades yet.\n\n"
                "Use /signal to get a trading signal.\n"
                "Link your account with /link for auto-trading.",
            )
            return

        lines = ["📋 *Recent Trades*\n"]
        for row in trades:
            lines.append(f"\n{_format_trade(row)}")

        lines.append(f"\n\n---\nShowing last {len(trades)} trades")
        await self._reply(update, "".join(lines))

    # ── Command: /admin ─────────────────────────────────────────────────

    async def cmd_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
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
                f"💰 Total PnL: `{total_pnl:+.2f}`\n"
                f"🤖 Auto-scan: {'🟢 ON' if self.signaler.is_running else '🔴 OFF'}"
            )
            await self._reply(update, text)

        elif sub_cmd == "broadcast" and len(args) >= 2:
            msg = " ".join(args[1:])
            users = self.db.get_all_users()
            sent = 0
            failed = 0
            for u in users:
                try:
                    await context.bot.send_message(
                        chat_id=u["chat_id"],
                        text=f"📢 *Broadcast*\n\n{msg}",
                        parse_mode="Markdown",
                    )
                    sent += 1
                except Exception as exc:
                    LOG.warning("Broadcast fail to %d: %s", u["user_id"], exc)
                    failed += 1
                await asyncio.sleep(0.05)  # rate limit
            await self._reply(
                update,
                f"📢 *Broadcast Complete*\n\n"
                f"Sent: {sent}\n"
                f"Failed: {failed}\n"
                f"Total: {len(users)}",
            )

        elif sub_cmd == "signal_on":
            self.signaler.start()
            await self._reply(update, "🟢 *Auto-scan started*")

        elif sub_cmd == "signal_off":
            await self.signaler.stop()
            await self._reply(update, "🔴 *Auto-scan stopped*")

        else:
            await self._reply(update, f"❌ Unknown admin command: `{sub_cmd}`")

    # ── Callback handler ────────────────────────────────────────────────

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        if not query:
            return
        data = query.data
        user_id = update.effective_user.id
        await query.answer()

        if data == "plans":
            await self._reply(update, Config.pricing_text())

        elif data == "link":
            await self._reply(
                update,
                "🔗 Use `/link <your_auth_token>` to connect your Stockity account.\n\n"
                "Need help? Run /link without arguments.",
            )

        elif data == "signal_now":
            await self._reply(update, "⏳ Scanning... Stand by.")
            sig = await self.signaler._generator.generate("BTCUSD")
            if sig:
                text = _signal_to_text(sig)
                keyboard = None
                if sig.is_tradeable:
                    keyboard = [[InlineKeyboardButton(
                        "🟢 Trade" if sig.action == "CALL" else "🔴 Trade",
                        callback_data=f"trade_{sig.action}_{sig.symbol}",
                    )]]
                await self._reply(update, text, keyboard=keyboard)
            else:
                await self._reply(update, "⚠️ No signal available.")

        elif data == "stats":
            await self.cmd_stats(update, context)

        elif data.startswith("trade_"):
            # Trade callback: trade_CALL_CRYPTO_IDX
            parts = data.split("_")
            if len(parts) >= 3:
                direction = parts[1]
                symbol = parts[2]
                await self._execute_trade(update, user_id, symbol, direction)

        elif data.startswith("confirm_trade_"):
            parts = data.split("_")
            if len(parts) >= 4:
                symbol = parts[2]
                direction = parts[3]
                await self._execute_trade(update, user_id, symbol, direction)

        elif data.startswith("check_"):
            # Check payment status callback: check_{merchant_ref}
            merchant_ref = data[6:]
            if merchant_ref:
                # Simulate /confirm
                context.args = [merchant_ref]
                await self.cmd_confirm(update, context)

    # ── Trade execution ─────────────────────────────────────────────────

    async def _execute_trade(
        self,
        update: Update,
        user_id: int,
        symbol: str,
        direction: str,
        amount: int = 14000,
        duration_min: int = 1,
    ):
        """Execute a trade for the user."""
        # Check subscription
        sub = self.db.get_active_subscription(user_id)
        if not sub:
            await self._reply(
                update,
                "❌ You need an active subscription to trade.\n"
                "Use /subscribe to get started.",
            )
            return

        # Get linked account for user's auth token
        accounts = self.db.get_linked_accounts(user_id)
        user_auth = ""
        user_id_str = ""
        if accounts:
            user_auth = accounts[0]["stockity_auth"]
            user_id_str = accounts[0].get("stockity_user_id", "")

        order = TradeOrder(
            symbol=symbol,
            direction=Direction(direction),
            amount=amount,
            duration_min=duration_min,
            user_auth_token=user_auth,
            user_id=user_id_str,
            signal_confidence=0,
            trade_type="manual",
            note=f"Manual trade from Telegram",
        )

        # Record in DB
        trade_id = self.db.record_trade(
            user_id=user_id,
            symbol=symbol,
            direction=direction,
            amount=amount,
            duration_min=duration_min,
            trade_type="manual",
            note="Manual trade via bot",
        )

        await self._reply(
            update,
            f"⏳ *Placing trade...*\n"
            f"{'🟢' if direction == 'CALL' else '🔴'} {symbol} {direction}\n"
            f"Amount: Rp{amount:,}\n"
            f"Duration: {duration_min} min",
        )

        result = await self.trade_client.place(order)

        # Update DB
        status = result.status.value
        pnl = 0.0
        if result.status.value == "open":
            pnl = 0.0
        elif result.status.value == "won":
            pnl = amount * 0.82  # +82% payout
        elif result.status.value == "lost":
            pnl = -amount

        self.db.resolve_trade(trade_id, status, pnl)

        if result.status.value in ("open", "queued"):
            await self._reply(
                update,
                f"✅ *Trade Placed*\n\n"
                f"{'🟢' if direction == 'CALL' else '🔴'} {symbol} {direction}\n"
                f"Rp{amount:,} | {duration_min}min\n"
                f"ID: `{result.trade_id or trade_id}`\n"
                f"Message: {result.message}",
            )
        else:
            await self._reply(
                update,
                f"⚠️ *Trade Issue*\n\n"
                f"Status: {result.status.value}\n"
                f"Message: {result.message}\n\n"
                f"The trade has been queued and will retry automatically.",
            )

    # ── Signal dispatch ─────────────────────────────────────────────────

    async def _dispatch_signal_to_subscribers(self, sig: Signal):
        """Send a signal to all active subscribers."""
        subscribers = self.db.get_active_subscribers()
        text = f"📡 *Signal Alert*\n\n{_signal_to_text(sig)}"

        keyboard = None
        if sig.is_tradeable and sig.confidence >= Config.MIN_CONFIDENCE:
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"🟢 Trade CALL ({sig.confidence}%)",
                        callback_data=f"confirm_trade_{sig.symbol}_CALL",
                    ),
                    InlineKeyboardButton(
                        f"🔴 Trade PUT ({100 - sig.confidence}%)",
                        callback_data=f"confirm_trade_{sig.symbol}_PUT",
                    ),
                ]
            ]

        sent = 0
        for user in subscribers:
            try:
                await self._app.bot.send_message(
                    chat_id=user["chat_id"],
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
                )
                sent += 1
            except Exception as exc:
                LOG.warning("Signal dispatch fail to %d: %s", user["user_id"], exc)
            await asyncio.sleep(0.03)

        LOG.info("Signal dispatched to %d/%d subscribers", sent, len(subscribers))

    async def _auto_trade_on_signal(self, sig: Signal):
        """Auto-trade high-confidence signals for users with linked accounts."""
        if not sig.is_tradeable or sig.confidence < Config.MIN_CONFIDENCE:
            return

        subscribers = self.db.get_active_subscribers()
        for user in subscribers:
            accounts = self.db.get_linked_accounts(user["user_id"])
            if not accounts:
                continue

            amount = 14000  # minimum trade amount
            direction = sig.action

            for acct in accounts:
                order = TradeOrder(
                    symbol=sig.symbol,
                    direction=Direction(direction),
                    amount=amount,
                    duration_min=1,
                    user_auth_token=acct["stockity_auth"],
                    user_id=acct.get("stockity_user_id", ""),
                    signal_confidence=sig.confidence,
                    signal_reason=sig.reason,
                    trade_type="auto",
                    note=f"Auto-trade on signal: {sig.reason}",
                )

                trade_id = self.db.record_trade(
                    user_id=user["user_id"],
                    symbol=sig.symbol,
                    direction=direction,
                    amount=amount,
                    duration_min=1,
                    entry_price=sig.price,
                    confidence=sig.confidence,
                    trade_type="auto",
                    note=f"Auto: {sig.reason[:100]}",
                )

                result = await self.trade_client.place(order)

                status = result.status.value
                pnl = 0.0
                if result.status.value == "won":
                    pnl = amount * 0.82
                elif result.status.value == "lost":
                    pnl = -amount
                self.db.resolve_trade(trade_id, status, pnl)

                LOG.info(
                    "Auto-trade for user %d: %s %s -> %s (trade #%d)",
                    user["user_id"], sig.symbol, direction, status, trade_id,
                )

                await asyncio.sleep(0.5)  # rate limit between trades

    # ── Post-init ───────────────────────────────────────────────────────

    async def _post_init(self, app: Application):
        """Called by PTB after app initializes and starts polling."""
        self._app = app

        # Wire signal dispatch for Stockity
        self.signaler.set_dispatcher(self._dispatch_signal_to_subscribers)
        self.signaler.set_auto_trade_dispatcher(self._auto_trade_on_signal)

        # Wire signal dispatch for Deriv
        self.deriv_signaler.set_dispatcher(self._dispatch_signal_to_subscribers)
        self.deriv_signaler.set_auto_trade_dispatcher(self._auto_trade_on_signal)

        # Start proactive scanning
        self.signaler.start()
        self.deriv_signaler.start()

        # Start Tripay webhook server
        try:
            self._webhook_server = tripay.start_webhook()
            LOG.info("Tripay webhook started")
        except Exception as exc:
            LOG.warning("Tripay webhook start failed: %s", exc)

        LOG.info("Bot is running!")

        # Notify admin
        try:
            await app.bot.send_message(
                chat_id=Config.ADMIN_CHAT_ID,
                text=(
                    "🤖 *Subscription Bot Online*\n\n"
                    "Proactive signal scanning: 🟢 ON\n"
                    "Deriv signal scanning: 🟢 ON\n"
                    f"Interval: {Config.SCAN_INTERVAL}s\n"
                    f"Min confidence: {Config.MIN_CONFIDENCE}%"
                ),
                parse_mode="Markdown",
            )
        except Exception as exc:
            LOG.warning("Admin notification failed: %s", exc)

    # ── Run ─────────────────────────────────────────────────────────────

    def run(self):
        """Build app and start polling (blocking)."""
        app = self.build_app()
        app.run_polling(
            poll_interval=0.5,
            timeout=30,
            drop_pending_updates=True,
        )


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                "/tmp/stockity_subscription_bot.log",
                encoding="utf-8",
            ),
        ],
    )
    bot = SubscriptionBot()
    bot.run()


if __name__ == "__main__":
    main()
