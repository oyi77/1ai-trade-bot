#!/usr/bin/env python3
"""
Deriv Telegram Notifications — Telethon Integration
====================================================
Sends trade notifications via the existing Telethon client.

Message types:
  - entry_signal 🟢   — Pattern detected, entry signal generated
  - buy_confirm ✅     — Contract bought successfully
  - settlement 💰     — Contract settlement result (win/loss)
  - daily_summary 📊  — End-of-day performance summary

Uses the same Telethon session and credentials as daily_recap.py:
  Session: ~/.openclaw/workspace/vilona_session
  API ID:  23647272
  API Hash: 1f69a4e0f03e5f51ddfa5b67ac7b5c49
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# ── Path setup ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # scripts/deriv/
_PARENT = os.path.dirname(_SCRIPT_DIR)                    # scripts/
_GRANDPARENT = os.path.dirname(_PARENT)                   # 1ai-trade-bot/
for p in (_PARENT, _GRANDPARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

LOG = logging.getLogger("deriv.telegram")

# ── Telethon credentials (matching daily_recap.py) ──
TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "23647272"))
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH",
                                    "1f69a4e0f03e5f51ddfa5b67ac7b5c49")
SESSION_PATH = os.path.expanduser(
    os.environ.get("TELEGRAM_SESSION_PATH",
                   "~/.openclaw/workspace/vilona_session")
)
CHANNEL_ID = int(os.environ.get("DERIV_TELEGRAM_CHANNEL",
                                os.environ.get("VILONA_MAPPING_CHANNEL",
                                               "-1003257064212")))

# ── Async Telethon client (lazy-init) ──

_telegram_client = None
_telegram_lock = asyncio.Lock()


async def _get_client():
    """Get or create the shared Telethon client."""
    global _telegram_client
    if _telegram_client is not None and _telegram_client.is_connected():
        return _telegram_client

    from telethon import TelegramClient

    async with _telegram_lock:
        # Double-check after acquiring lock
        if _telegram_client is not None and _telegram_client.is_connected():
            return _telegram_client

        client = TelegramClient(SESSION_PATH, TELEGRAM_API_ID,
                                TELEGRAM_API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            LOG.error("❌ Telethon session not authorized! "
                       "Run daily_recap.py or daily_mapping.py first.")
            await client.disconnect()
            return None

        _telegram_client = client
        LOG.info("✅ Telethon connected (session: %s)", SESSION_PATH)
        return client


async def _send_message(text: str, parse_mode: str = "html",
                        silent: bool = False) -> bool:
    """Send a message to the configured Telegram channel.

    Args:
        text: Message content (HTML allowed).
        parse_mode: Telethon parse mode (default: 'html').
        silent: If True, notification is silent (no sound).

    Returns:
        True if sent successfully, False otherwise.
    """
    client = await _get_client()
    if client is None:
        return False

    try:
        msg = await client.send_message(
            CHANNEL_ID, text, parse_mode=parse_mode,
            link_preview=False, silent=silent,
        )
        LOG.info("📤 Telegram sent! msg_id=%s", msg.id)
        return True
    except Exception as e:
        LOG.error("❌ Telegram send failed: %s", e)
        return False


# ── Message Builders ──


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telethon parse_mode='html'."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def build_entry_signal(symbol: str, direction: str, entry_price: float,
                       confidence: float, reason: str = "",
                       signal_id: str = "") -> str:
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
        f"<b>🚨 Deriv Entry Signal</b>",
        f"",
        f"{direction_emoji} <b>Direction:</b> {direction}",
        f"🎯 <b>Symbol:</b> {_escape_html(symbol)}",
        f"💰 <b>Price:</b> {entry_price:.5f}",
        f"📊 <b>Confidence:</b> {confidence:.1f}%",
    ]
    if reason:
        lines.append(f"📝 <b>Reason:</b> {_escape_html(reason)}")
    if signal_id:
        lines.append(f"🆔 <b>Signal ID:</b> <code>{_escape_html(signal_id)}</code>")
    lines.append(f"⏰ <b>Time:</b> {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    return "\n".join(lines)


def build_buy_confirm(symbol: str, contract_type: str, stake: float,
                      contract_id: int, barrier: Optional[int] = None,
                      entry_price: Optional[float] = None) -> str:
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
        f"<b>✅ Deriv Buy Executed</b>",
        f"",
        f"🎯 <b>Symbol:</b> {_escape_html(symbol)}",
        f"📋 <b>Contract:</b> {_escape_html(contract_type)}",
        f"💵 <b>Stake:</b> ${stake:.2f}",
        f"🆔 <b>Contract ID:</b> <code>{contract_id}</code>",
    ]
    if barrier is not None:
        lines.append(f"🎲 <b>Barrier:</b> {barrier}")
    if entry_price is not None:
        lines.append(f"💰 <b>Entry Price:</b> {entry_price:.5f}")
    lines.append(f"⏰ <b>Time:</b> {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    return "\n".join(lines)


def build_settlement(symbol: str, is_win: bool, profit: float,
                     stake: float, contract_type: str,
                     contract_id: int, payout: Optional[float] = None,
                     entry_tick: Optional[float] = None,
                     exit_tick: Optional[float] = None) -> str:
    """Build 💰 Settlement Result notification message.

    Args:
        symbol: Trading symbol.
        is_win: True if trade won, False if lost.
        profit: Profit/loss amount (positive for wins, negative for losses).
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
        f"",
        f"🎯 <b>Symbol:</b> {_escape_html(symbol)}",
        f"📋 <b>Contract:</b> {_escape_html(contract_type)}",
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
    lines.append(f"⏰ <b>Time:</b> {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    return "\n".join(lines)


def build_daily_summary(total_trades: int, wins: int, losses: int,
                        total_profit: float, start_balance: float,
                        end_balance: float, symbol: str = "",
                        win_rate: Optional[float] = None) -> str:
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
        f"",
    ]
    if symbol:
        lines.append(f"🎯 <b>Symbol:</b> {_escape_html(symbol)}")
    lines.extend([
        f"┌──────────────────────────",
        f"│ 📊 <b>Trades:</b> {total_trades}",
        f"│ ✅ <b>Wins:</b> {wins}",
        f"│ ❌ <b>Losses:</b> {losses}",
        f"│ 🏆 <b>Win Rate:</b> {win_rate:.1f}%",
        f"│ 💰 <b>P&amp;L:</b> {profit_sign}${total_profit:.2f}",
        f"├──────────────────────────",
        f"│ 🏦 <b>Start Balance:</b> ${start_balance:.2f}",
        f"│ 🏦 <b>End Balance:</b> ${end_balance:.2f}",
        f"│ 📈 <b>Change:</b> {change_sign}${balance_change:.2f}",
        f"└──────────────────────────",
        f"",
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    ])
    return "\n".join(lines)


# ── Public API ──


async def send_entry_signal(symbol: str, direction: str, entry_price: float,
                            confidence: float, reason: str = "",
                            signal_id: str = "",
                            silent: bool = False) -> bool:
    """Send 🟢 entry signal notification.

    Args:
        symbol: Trading symbol.
        direction: 'CALL' or 'PUT'.
        entry_price: Signal entry price.
        confidence: Signal confidence (0-100).
        reason: Optional pattern reason.
        signal_id: Optional signal identifier.
        silent: If True, send silently (no sound).

    Returns:
        True if sent successfully.
    """
    text = build_entry_signal(symbol, direction, entry_price, confidence,
                              reason, signal_id)
    return await _send_message(text, silent=silent)


async def send_buy_confirm(symbol: str, contract_type: str, stake: float,
                           contract_id: int, barrier: Optional[int] = None,
                           entry_price: Optional[float] = None,
                           silent: bool = False) -> bool:
    """Send ✅ buy confirmation notification.

    Args:
        symbol: Trading symbol.
        contract_type: e.g. 'DIGITMATCH'.
        stake: Contract stake amount.
        contract_id: Deriv contract ID.
        barrier: Optional digit barrier.
        entry_price: Optional entry price.
        silent: If True, send silently.

    Returns:
        True if sent successfully.
    """
    text = build_buy_confirm(symbol, contract_type, stake, contract_id,
                             barrier, entry_price)
    return await _send_message(text, silent=silent)


async def send_settlement(symbol: str, is_win: bool, profit: float,
                          stake: float, contract_type: str,
                          contract_id: int,
                          payout: Optional[float] = None,
                          entry_tick: Optional[float] = None,
                          exit_tick: Optional[float] = None,
                          silent: bool = False) -> bool:
    """Send 💰 settlement result notification.

    Args:
        symbol: Trading symbol.
        is_win: True if won, False if lost.
        profit: Profit/loss amount.
        stake: Stake amount.
        contract_type: e.g. 'DIGITMATCH'.
        contract_id: Deriv contract ID.
        payout: Optional payout amount.
        entry_tick: Optional entry price.
        exit_tick: Optional exit price.
        silent: If True, send silently.

    Returns:
        True if sent successfully.
    """
    text = build_settlement(symbol, is_win, profit, stake, contract_type,
                            contract_id, payout, entry_tick, exit_tick)
    return await _send_message(text, silent=silent)


async def send_daily_summary(total_trades: int, wins: int, losses: int,
                             total_profit: float, start_balance: float,
                             end_balance: float, symbol: str = "",
                             silent: bool = False) -> bool:
    """Send 📊 daily summary notification.

    Args:
        total_trades: Number of trades today.
        wins: Number of winning trades.
        losses: Number of losing trades.
        total_profit: Cumulative P&L.
        start_balance: Starting balance.
        end_balance: Current balance.
        symbol: Optional trading symbol.
        silent: If True, send silently.

    Returns:
        True if sent successfully.
    """
    text = build_daily_summary(total_trades, wins, losses, total_profit,
                               start_balance, end_balance, symbol)
    return await _send_message(text, silent=silent)


# ── Synchronous wrappers (for use in non-async contexts) ──


def send_entry_signal_sync(symbol: str, direction: str,
                           entry_price: float, confidence: float,
                           reason: str = "", signal_id: str = "") -> bool:
    """Synchronous wrapper for send_entry_signal."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # If there's a running loop, run in it
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                send_entry_signal(symbol, direction, entry_price,
                                  confidence, reason, signal_id),
                loop
            ).result(timeout=15)
    except RuntimeError:
        pass
    return asyncio.run(
        send_entry_signal(symbol, direction, entry_price,
                          confidence, reason, signal_id)
    )


def send_buy_confirm_sync(symbol: str, contract_type: str, stake: float,
                          contract_id: int,
                          barrier: Optional[int] = None,
                          entry_price: Optional[float] = None) -> bool:
    """Synchronous wrapper for send_buy_confirm."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                send_buy_confirm(symbol, contract_type, stake, contract_id,
                                 barrier, entry_price),
                loop
            ).result(timeout=15)
    except RuntimeError:
        pass
    return asyncio.run(
        send_buy_confirm(symbol, contract_type, stake, contract_id,
                         barrier, entry_price)
    )


def send_settlement_sync(symbol: str, is_win: bool, profit: float,
                         stake: float, contract_type: str,
                         contract_id: int,
                         payout: Optional[float] = None,
                         entry_tick: Optional[float] = None,
                         exit_tick: Optional[float] = None) -> bool:
    """Synchronous wrapper for send_settlement."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                send_settlement(symbol, is_win, profit, stake, contract_type,
                                contract_id, payout, entry_tick, exit_tick),
                loop
            ).result(timeout=15)
    except RuntimeError:
        pass
    return asyncio.run(
        send_settlement(symbol, is_win, profit, stake, contract_type,
                        contract_id, payout, entry_tick, exit_tick)
    )


def send_daily_summary_sync(total_trades: int, wins: int, losses: int,
                            total_profit: float, start_balance: float,
                            end_balance: float,
                            symbol: str = "") -> bool:
    """Synchronous wrapper for send_daily_summary."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                send_daily_summary(total_trades, wins, losses, total_profit,
                                   start_balance, end_balance, symbol),
                loop
            ).result(timeout=15)
    except RuntimeError:
        pass
    return asyncio.run(
        send_daily_summary(total_trades, wins, losses, total_profit,
                           start_balance, end_balance, symbol)
    )


# ── CLI Entrypoint ──

async def _cli_send_signal():
    """CLI: send a test entry signal."""
    ok = await send_entry_signal(
        symbol="R_75",
        direction="CALL",
        entry_price=1234.56789,
        confidence=72.5,
        reason="Momen 1/2 pattern detected at carrier 7",
        signal_id="sig_001",
    )
    print(f"{'✅' if ok else '❌'} Signal sent: {ok}")


async def _cli_send_buy():
    """CLI: send a test buy confirm."""
    ok = await send_buy_confirm(
        symbol="R_75",
        contract_type="DIGITMATCH",
        stake=0.35,
        contract_id=123456789,
        barrier=7,
        entry_price=1234.56789,
    )
    print(f"{'✅' if ok else '❌'} Buy confirm sent: {ok}")


async def _cli_send_settlement():
    """CLI: send a test settlement (win)."""
    ok = await send_settlement(
        symbol="R_75",
        is_win=True,
        profit=2.80,
        stake=0.35,
        contract_type="DIGITMATCH",
        contract_id=123456789,
        payout=3.15,
        entry_tick=1234.56789,
        exit_tick=1235.12345,
    )
    print(f"{'✅' if ok else '❌'} Settlement sent: {ok}")


async def _cli_send_loss():
    """CLI: send a test settlement (loss)."""
    ok = await send_settlement(
        symbol="R_75",
        is_win=False,
        profit=-0.35,
        stake=0.35,
        contract_type="DIGITMATCH",
        contract_id=123456790,
        entry_tick=1234.56789,
        exit_tick=1234.12345,
    )
    print(f"{'✅' if ok else '❌'} Loss settlement sent: {ok}")


async def _cli_daily_summary():
    """CLI: send a test daily summary."""
    ok = await send_daily_summary(
        total_trades=15,
        wins=9,
        losses=6,
        total_profit=12.45,
        start_balance=100.0,
        end_balance=112.45,
        symbol="R_75",
    )
    print(f"{'✅' if ok else '❌'} Daily summary sent: {ok}")


def main():
    """CLI entrypoint for testing Telegram notifications."""
    import argparse
    ap = argparse.ArgumentParser(description="Deriv Telegram Notifications — Test CLI")
    ap.add_argument("mode", nargs="?", default="signal",
                    choices=["signal", "buy", "settlement", "loss",
                             "daily", "all"],
                    help="Notification type to send (default: signal)")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = ap.parse_args()
    modes = {
        "signal": _cli_send_signal,
        "buy": _cli_send_buy,
        "settlement": _cli_send_settlement,
        "loss": _cli_send_loss,
        "daily": _cli_daily_summary,
        "all": lambda: asyncio.gather(
            _cli_send_signal(),
            _cli_send_buy(),
            _cli_send_settlement(),
            _cli_send_loss(),
            _cli_daily_summary(),
        ),
    }
    handler = modes.get(args.mode)
    if handler:
        asyncio.run(handler())
    else:
        print(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    main()
