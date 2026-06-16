"""CallbackHandlersMixin — menu navigation, trade, and payment callbacks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from tradebot.bots.base import BaseBot

LOG = logging.getLogger("tradebot.bots.vilona.callbacks")

if TYPE_CHECKING:
    class _BotProtocol(BaseBot):
        async def _tg_send(
            self, text: str, chat_id: str | None = None,
            reply_markup: dict | None = None, reply_to: int | None = None,
        ) -> bool: ...
        async def _tg_edit_message(
            self, message_id: int, text: str, chat_id: str | None = None,
            reply_markup: dict | None = None,
        ) -> bool: ...
        async def _tg_send_photo(
            self, photo_bytes: bytes, caption: str = "", chat_id: str | None = None,
            reply_markup: dict | None = None,
        ) -> bool: ...
        async def _tg_answer_callback(
            self, cb_id: str, text: str = "", show_alert: bool = False,
        ) -> bool: ...
        _default_pair: str
        _pending_signals: dict
        async def _cmd_analyze(
            self, args: list[str], chat_id: str | None = None,
        ) -> str: ...
        async def _cmd_engines(
            self, args: list[str], chat_id: str | None = None,
        ) -> str: ...
        async def _cmd_portfolio(
            self, args: list[str], chat_id: str | None = None,
        ) -> str: ...
        async def _cmd_trade(
            self, args: list[str], chat_id: str | None = None,
        ) -> str: ...
        async def _cmd_autotrade(
            self, args: list[str], chat_id: str | None = None,
        ) -> str: ...
        def _get_best_asset_ric(self) -> str: ...

    _BaseCB = _BotProtocol
else:
    _BaseCB = BaseBot


class CallbackHandlersMixin(_BaseCB):
    """Mixin providing callback query handlers for VilonaBot."""

    async def _handle_callback(self, callback_query: dict[str, Any]) -> str | None:
        cb_id = callback_query.get("id", "")
        chat_id = str(callback_query.get("from", {}).get("id", ""))
        username = callback_query.get("from", {}).get("username", "")
        data = callback_query.get("data", "")
        message_id = callback_query.get("message", {}).get("message_id")

        await self._tg_answer_callback(cb_id)

        if not chat_id or not data:
            return None

        # Menu navigation — edit in-place when possible
        if data.startswith("menu:"):
            menu_name = data.replace("menu:", "")
            return await self._handle_menu_nav(menu_name, chat_id, message_id)

        # Command routing from buttons
        response: str | None = None
        if data.startswith("cmd:"):
            cmd_full = data.replace("cmd:", "")
            cmd_parts = cmd_full.split()
            cmd_name = cmd_parts[0]
            cmd_args = cmd_parts[1:]
            handler = self._command_handlers.get(cmd_name)
            if handler:
                raw_response = handler(cmd_args, chat_id=chat_id)
                if asyncio.iscoroutine(raw_response):
                    response = await raw_response
                else:
                    response = raw_response
            if response:
                await self._tg_send(response, chat_id=chat_id)
                return response

        # Portfolio actions
        if data.startswith("portfolio:"):
            action = data.replace("portfolio:", "")
            if action == "refresh":
                response = await self._cmd_portfolio([], chat_id=chat_id)
            elif action == "trade_best":
                best = self._get_best_asset_ric()
                if best:
                    response = await self._cmd_trade([best], chat_id=chat_id)
                else:
                    response = "Tidak ada aset yang bisa ditrading saat ini."
            if response:
                return response

        # Link actions (trigger button-followed input)
        if data.startswith("link:"):
            platform = data.replace("link:", "")
            response = (
                f"🔗 <b>LINK {platform.upper()}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Ketik:\n"
                f"/link {platform} &lt;credentials&gt;\n\n"
                f"Contoh:\n"
                f"/link stockity email@example.com password"
            )
            if response:
                return response

        # Autotrade toggle
        if data.startswith("autotrade:"):
            action = data.replace("autotrade:", "")
            response = await self._cmd_autotrade([action], chat_id=chat_id)
            if response:
                return response

        # Trade confirmation / skip callbacks
        if data.startswith("trade:") or data.startswith("skip:"):
            return await self._handle_trade_callback(chat_id, data)
        # Payment / subscription / address callbacks
        if (
            data.startswith("pay:")
            or data.startswith("sub:")
            or data.startswith("addr:")
            or data.startswith("check:")
            or data.startswith("donate:")
            or data.startswith("pricing:")
            or data == "cancel_input"
        ):
            return await self._handle_payment_callback(chat_id, username, data)

        return None

    async def _handle_menu_nav(
        self, menu_name: str, chat_id: str, message_id: int | None = None,
    ) -> str:
        from tradebot.services.menu import get_inline_keyboard, get_menu_text

        text = get_menu_text(menu_name)
        kb = get_inline_keyboard(menu_name)
        if message_id:
            try:
                edited = await self._tg_edit_message(
                    message_id, text, chat_id=chat_id, reply_markup=kb,
                )
                if edited:
                    return ""
            except Exception:
                pass
        await self._tg_send(text, chat_id=chat_id, reply_markup=kb)
        return ""

    async def _handle_trade_callback(self, chat_id: str, data: str) -> str:
        if chat_id not in self._pending_signals:
            return "Tidak ada sinyal yang menunggu konfirmasi."

        signal = self._pending_signals.pop(chat_id)
        if data.startswith("trade:"):
            action = data.replace("trade:", "")
            symbol = signal.get("symbol", self._default_pair)
            direction = signal.get("direction", "BUY")
            if action == "confirm":
                msg = (
                    f"✅ <b>Trade Confirmed</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Symbol: {symbol}\n"
                    f"Direction: {direction}\n"
                    f"Status: forwarded to execution bridge"
                )
                return msg
            elif action == "modify":
                return "🛠️ Gunakan /trade <symbol> <direction> untuk modifikasi manual."
        elif data.startswith("skip:"):
            return "⏭️ Sinyal dilewatkan. Tunggu sinyal berikutnya."
        return "Unknown trade action."

    async def _handle_payment_callback(self, chat_id: str, username: str, data: str) -> str:
        if data == "cancel_input":
            return "❌ Input dibatalkan."

        if data.startswith("sub:"):
            plan = data.replace("sub:", "")
            plans = {"basic": "Basic", "pro": "Pro", "premium": "Premium", "trial": "Trial"}
            plan_label = plans.get(plan.lower(), plan.capitalize())
            return (
                f"⭐ <b>Subscribe {plan_label}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Ketik /subscribe {plan} untuk aktivasi.\n"
                f"💬 Konfirmasi manual via @codergaboets"
            )

        if data.startswith("pay:donate:"):
            try:
                amount = int(data.replace("pay:donate:", ""))
            except ValueError:
                amount = 0
            if amount <= 0:
                return "❌ Nominal donasi tidak valid."
            return (
                f"💚 <b>Donasi Rp {amount:,}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Terima kasih! Hubungi @codergaboets untuk instruksi pembayaran."
            )

        if data.startswith("donate:"):
            try:
                amount = int(data.replace("donate:", ""))
            except ValueError:
                return "❌ Nominal donasi tidak valid."
            return f"💚 Donasi Rp {amount:,} — konfirmasi ke @codergaboets"

        if data.startswith("addr:"):
            crypto = data.replace("addr:", "")
            addresses = {
                "btc": "bc1q...btc_address",
                "eth": "0x...eth_address",
                "usdt": "T...usdt_address",
            }
            addr = addresses.get(crypto, "Hubungi @codergaboets")
            return (
                f"🏦 <b>Deposit {crypto.upper()}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Alamat: <code>{addr}</code>"
            )

        if data.startswith("check:"):
            tx = data.replace("check:", "")
            return f"🔍 Mengecek transaksi <code>{tx}</code>... (mock)"

        if data.startswith("pricing:"):
            plan = data.replace("pricing:", "")
            return f"📊 Pricing untuk plan {plan.upper()} — hubungi @codergaboets"

        return "💳 Hubungi @codergaboets untuk subscribe"

    async def _handle_donation_input(self, chat_id: str, text: str) -> str:
        try:
            amount = int(text.strip())
        except ValueError:
            return "❌ Nominal tidak valid. Ketik angka saja (contoh: 50000)."
        if amount < 1000:
            return "❌ Minimal donasi Rp 1.000."
        return (
            f"💚 <b>Konfirmasi Donasi Rp {amount:,}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Silakan transfer ke rekening yang akan dikirim admin."
        )
