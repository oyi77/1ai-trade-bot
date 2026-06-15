"""
Telegram notification service — sends trading alerts via Telethon/Telegram Bot API.
"""

import logging
from datetime import UTC, datetime

from tradebot.config import settings

LOG = logging.getLogger(__name__)


class TelegramService:
    """Sends trading notifications via Telegram Bot API.

    Uses the python-telegram-bot library or simple HTTP calls.
    Configure via TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
    """
    _SENTINEL = object()

    def __init__(
        self,
        bot_token: str | object = _SENTINEL,
        chat_id: str | object = _SENTINEL,
    ):
        # Explicit empty string means disabled; None/sentinel means use settings
        if bot_token is not self._SENTINEL and chat_id is not self._SENTINEL:
            # Both explicitly provided (could be empty strings)
            self.bot_token = bot_token if bot_token else ""
            self.chat_id = chat_id if chat_id else ""
            self._enabled = bool(self.bot_token and self.chat_id)
        else:
            # Fall back to settings
            self.bot_token = settings.TELEGRAM_BOT_TOKEN if settings.TELEGRAM_BOT_TOKEN else ""
            self.chat_id = settings.TELEGRAM_CHAT_ID if settings.TELEGRAM_CHAT_ID else ""
            self._enabled = bool(self.bot_token and self.chat_id)

    async def send_message(self, text: str) -> tuple[bool, int | None]:
        """Send a text message to the configured Telegram chat.

        Returns (success, message_id) where message_id is None on failure."""
        if not self._enabled:
            LOG.debug("Telegram not configured — skipping message: %s", text[:50])
            return False, None

        try:
            import httpx
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                })
                if resp.status_code != 200:
                    LOG.warning("Telegram send failed: %s", resp.text[:200])
                    return False, None
                data = resp.json()
                message_id = data.get("result", {}).get("message_id") if data.get("ok") else None
                return True, message_id
        except Exception as e:
            LOG.warning("Telegram send error: %s", e)
            return False, None

    async def send_signal_alert(self, symbol: str, direction: str,
                                 confidence: float, price: float):
        """Send a formatted trading signal alert."""
        emoji = "🟢" if direction == "CALL" else ("🔴" if direction == "PUT" else "⚪")
        text = (
            f"{emoji} <b>Signal Alert</b>\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Confidence: {confidence:.1f}%\n"
            f"Price: {price:.5f}"
        )
        ok, _ = await self.send_message(text)
        return ok

    async def send_trade_result(self, profit: float, win: bool,
                                 symbol: str, details: str = ""):
        """Send a formatted trade result notification."""
        emoji = "✅" if win else "❌"
        text = (
            f"{emoji} <b>Trade Result</b>\n"
            f"Symbol: {symbol}\n"
            f"P&L: ${profit:+.2f}\n"
            f"{details}"
        )
        ok, _ = await self.send_message(text)
        return ok


# ── HTML Escaping ─────────────────────────────────────────────────


def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram parse_mode='html'."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


# ── Message Builders (from scripts/deriv/deriv_telegram.py) ──────


def build_entry_signal(
    symbol: str, direction: str, entry_price: float,
    confidence: float, reason: str = "", signal_id: str = "",
) -> str:
    """Build 🟢 Entry Signal notification message.

    Args:
        symbol: Trading symbol (e.g. 'R_75').
        direction: 'CALL' or 'PUT'.
        entry_price: Signal entry price.
        confidence: Signal confidence (0-100).
        reason: Optional pattern/fundamental reason.
        signal_id: Optional unique signal identifier.

    Returns:
        Formatted HTML message string.
    """
    direction_emoji = "🟢" if direction.upper() == "CALL" else "🔴"
    lines = [
        "<b>🚨 Deriv Entry Signal</b>",
        "",
        f"{direction_emoji} <b>Direction:</b> {direction}",
        f"🎯 <b>Symbol:</b> {escape_html(symbol)}",
        f"💰 <b>Price:</b> {entry_price:.5f}",
        f"📊 <b>Confidence:</b> {confidence:.1f}%",
    ]
    if reason:
        lines.append(f"📝 <b>Reason:</b> {escape_html(reason)}")
    if signal_id:
        lines.append(f"🆔 <b>Signal ID:</b> <code>{escape_html(signal_id)}</code>")
    lines.append(f"⏰ <b>Time:</b> {datetime.now(UTC).strftime('%H:%M UTC')}")
    return "\n".join(lines)


def build_buy_confirm(
    symbol: str, contract_type: str, stake: float,
    contract_id: int, barrier: int | None = None,
    entry_price: float | None = None,
) -> str:
    """Build ✅ Buy Confirmation notification message.

    Args:
        symbol: Trading symbol.
        contract_type: e.g. 'DIGITMATCH', 'DIGITOVER'.
        stake: Contract stake amount.
        contract_id: Deriv contract ID.
        barrier: Optional digit barrier.
        entry_price: Optional entry price.

    Returns:
        Formatted HTML message string.
    """
    lines = [
        "<b>✅ Deriv Buy Executed</b>",
        "",
        f"🎯 <b>Symbol:</b> {escape_html(symbol)}",
        f"📋 <b>Contract:</b> {escape_html(contract_type)}",
        f"💵 <b>Stake:</b> ${stake:.2f}",
        f"🆔 <b>Contract ID:</b> <code>{contract_id}</code>",
    ]
    if barrier is not None:
        lines.append(f"🎲 <b>Barrier:</b> {barrier}")
    if entry_price is not None:
        lines.append(f"💰 <b>Entry Price:</b> {entry_price:.5f}")
    lines.append(f"⏰ <b>Time:</b> {datetime.now(UTC).strftime('%H:%M UTC')}")
    return "\n".join(lines)


def build_settlement(
    symbol: str, is_win: bool, profit: float,
    stake: float, contract_type: str,
    contract_id: int, payout: float | None = None,
    entry_tick: float | None = None, exit_tick: float | None = None,
) -> str:
    """Build 💰 Settlement Result notification message.

    Args:
        symbol: Trading symbol.
        is_win: True if trade won, False if lost.
        profit: P&L amount (positive for wins, negative for losses).
        stake: Stake amount.
        contract_type: e.g. 'DIGITMATCH'.
        contract_id: Deriv contract ID.
        payout: Optional payout amount.
        entry_tick: Optional entry price.
        exit_tick: Optional exit price.

    Returns:
        Formatted HTML message string.
    """
    emoji = "✅ WIN" if is_win else "❌ LOSS"
    profit_sign = "+" if profit > 0 else ""
    lines = [
        f"<b>{emoji} — Deriv Settlement</b>",
        "",
        f"🎯 <b>Symbol:</b> {escape_html(symbol)}",
        f"📋 <b>Contract:</b> {escape_html(contract_type)}",
        f"💵 <b>Stake:</b> ${stake:.2f}",
        f"📊 <b>P&amp;L:</b> {profit_sign}${profit:.2f}",
    ]
    if payout is not None:
        lines.append(f"💰 <b>Payout:</b> ${payout:.2f}")
    if entry_tick is not None:
        lines.append(f"📈 <b>Entry:</b> {entry_tick:.5f}")
    if exit_tick is not None:
        lines.append(f"📉 <b>Exit:</b> {exit_tick:.5f}")
    lines.append(f"🆔 <b>ID:</b> <code>{contract_id}</code>")
    lines.append(f"⏰ <b>Time:</b> {datetime.now(UTC).strftime('%H:%M UTC')}")
    return "\n".join(lines)


def build_daily_summary(
    total_trades: int, wins: int, losses: int,
    total_profit: float, start_balance: float,
    end_balance: float, symbol: str = "",
    win_rate: float | None = None,
) -> str:
    """Build 📊 Daily Summary notification message.

    Args:
        total_trades: Number of trades today.
        wins: Number of winning trades.
        losses: Number of losing trades.
        total_profit: Cumulative P&L.
        start_balance: Starting account balance.
        end_balance: Current account balance.
        symbol: Optional trading symbol.
        win_rate: Optional win rate (0-100). Auto-calculated if None.

    Returns:
        Formatted HTML message string.
    """
    if win_rate is None and total_trades > 0:
        win_rate = round(wins / total_trades * 100, 1)
    elif win_rate is None:
        win_rate = 0.0

    profit_sign = "+" if total_profit >= 0 else ""
    status_emoji = "🎉" if total_profit > 0 else ("😞" if total_profit < 0 else "🤝")
    balance_change = end_balance - start_balance
    change_sign = "+" if balance_change >= 0 else ""

    lines = [
        f"<b>{status_emoji} Deriv Daily Summary</b>",
        "",
    ]
    if symbol:
        lines.append(f"🎯 <b>Symbol:</b> {escape_html(symbol)}")
    lines.extend([
        "┌──────────────────────────",
        f"│ 📊 <b>Trades:</b> {total_trades}",
        f"│ ✅ <b>Wins:</b> {wins}",
        f"│ ❌ <b>Losses:</b> {losses}",
        f"│ 🏆 <b>Win Rate:</b> {win_rate:.1f}%",
        f"│ 💰 <b>P&amp;L:</b> {profit_sign}${total_profit:.2f}",
        "├──────────────────────────",
        f"│ 🏦 <b>Start Balance:</b> ${start_balance:.2f}",
        f"│ 🏦 <b>End Balance:</b> ${end_balance:.2f}",
        f"│ 📈 <b>Change:</b> {change_sign}${balance_change:.2f}",
        "└──────────────────────────",
        "",
        f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
    ])
    return "\n".join(lines)


def build_tp_sl_alert(
    symbol: str, alert_type: str, value: float,
    current_price: float | None = None,
) -> str:
    """Build ⚠️ TP/SL Alert notification message.

    Args:
        symbol: Trading symbol.
        alert_type: 'TP' or 'SL'.
        value: The TP/SL value.
        current_price: Optional current price.

    Returns:
        Formatted HTML message string.
    """
    emoji = "🎯" if alert_type.upper() == "TP" else "🛡️"
    label = "Take Profit" if alert_type.upper() == "TP" else "Stop Loss"
    lines = [
        f"<b>{emoji} {label} Alert</b>",
        "",
        f"🎯 <b>Symbol:</b> {escape_html(symbol)}",
        f"📊 <b>{label}:</b> ${value:.2f}",
    ]
    if current_price is not None:
        lines.append(f"💰 <b>Current Price:</b> {current_price:.5f}")
    lines.append(f"⏰ <b>Time:</b> {datetime.now(UTC).strftime('%H:%M UTC')}")
    return "\n".join(lines)
