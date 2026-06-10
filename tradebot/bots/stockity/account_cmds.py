"""
Stockity Account Commands — deposit, balance, account management.

Add to StockityBot via the register_account_commands() function.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from tradebot.brokers.stockity.broker import StockityBroker
from tradebot.brokers.stockity.rest import StockityREST

LOG = logging.getLogger(__name__)


# ── Handlers ──────────────────────────────────────────────────────────

async def cmd_balance(bot, args: list[str], chat_id: str | None = None) -> str:
    """Show Stockity account balance and positions."""
    broker = StockityBroker()
    try:
        await broker.connect()
        await asyncio.sleep(3)
        s = broker.stats
        lines = [
            "💰 *Stockity Account*",
            "",
            f"Balance: `{s['balance']:,}` {s['currency']}",
            f"  (~${s['balance_usd']:.2f})",
            f"Open: {s['open_positions']} | Closed: {s['total_trades']}",
            f"Wins: {s['wins']} | Losses: {s['losses']}",
            f"Win Rate: {s['winrate']:.1f}%",
            f"P&L: `{s['total_pnl_raw']:,}` {s['currency']}",
        ]
        if broker.open_positions:
            lines.append("")
            lines.append("*Open Positions:*")
            for p in broker.open_positions[:5]:
                trend = p.get('trend', '?').upper()
                otype = p.get('option_type', '?')
                rate = p.get('open_rate', 0)
                expires = p.get('close_time', '?')[:16]
                lines.append(f"  {trend:4s} {otype:6s} @ {rate} | {expires}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Balance check failed: {e}"
    finally:
        await broker.close()


async def cmd_deposit(bot, args: list[str], chat_id: str | None = None) -> str:
    """Generate QRIS deposit payment.

    Usage: /deposit <amount_in_idr>
    Example: /deposit 150000
    """
    if not args:
        return (
            "💳 *Deposit ke Stockity*\n\n"
            "Format: `/deposit <amount>`\n"
            "Contoh: `/deposit 150000`\n\n"
            "Minimal deposit: Rp 50,000\n"
            "Pembayaran via QRIS."
        )

    try:
        amount = int(args[0])
    except ValueError:
        return f"❌ Invalid amount: `{args[0]}`"

    if amount < 50000:
        return f"❌ Minimal Rp 50,000. You entered Rp {amount:,}"

    api = StockityREST()
    try:
        result = await api.deposit(amount=amount, handler="qris")
        if result and result.get("success"):
            url = result.get("redirect_url", "")
            return (
                f"💳 *Deposit Rp {amount:,}*\n\n"
                f"✅ Link pembayaran QRIS:\n\n"
                f"[🔗 Klik di sini untuk membayar]({url})\n\n"
                f"Atau buka:\n`{url}`\n\n"
                f"⏱️ Saldo terupdate otomatis setelah bayar."
            )
        return f"❌ Deposit gagal"
    finally:
        await api.close()


# ── PTB Wrappers ──────────────────────────────────────────────────────

async def ptb_balance(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.info("📨 /balance from chat_id=%s", upd.effective_chat.id)
    msg = await upd.message.reply_text("⏳ Checking balance...")
    reply = await cmd_balance(None, [])
    await msg.edit_text(reply, parse_mode="Markdown")


async def ptb_deposit(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.info("📨 /deposit from chat_id=%s", upd.effective_chat.id)
    msg = await upd.message.reply_text("⏳ Generating QRIS payment...")
    reply = await cmd_deposit(None, ctx.args or [])
    await msg.edit_text(reply, parse_mode="Markdown")


# ── Registration ──────────────────────────────────────────────────────

def register_account_commands(bot, cmd_handlers: dict) -> None:
    """Register balance and deposit commands on a StockityBot instance.

    Call this from StockityBot._register_commands():
        from tradebot.bots.stockity.account_cmds import register_account_commands
        register_account_commands(self, self._command_handlers)
    """
    cmd_handlers["balance"] = lambda args, chat_id=None: cmd_balance(bot, args, chat_id)
    cmd_handlers["deposit"] = lambda args, chat_id=None: cmd_deposit(bot, args, chat_id)


def register_account_ptb(bot, app) -> None:
    """Register PTB handlers for account commands."""
    app.add_handler(CommandHandler("balance", ptb_balance))
    app.add_handler(CommandHandler("deposit", ptb_deposit))
