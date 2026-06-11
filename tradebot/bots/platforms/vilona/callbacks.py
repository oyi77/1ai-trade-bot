"""CallbackHandlersMixin — menu navigation, trade, and payment callbacks."""

from __future__ import annotations

import logging
from typing import Any

from tradebot.bots.base import BaseBot
from tradebot.bots.platforms.vilona.helpers import DONATION_INPUT_STATE

LOG = logging.getLogger("tradebot.bots.vilona.callbacks")


class CallbackHandlersMixin(BaseBot):
    """Mixin providing callback query handlers for VilonaBot."""

    async def _handle_callback(self, callback_query: dict[str, Any]) -> str | None:
        cb_id = callback_query.get("id", "")
        chat_id = str(callback_query.get("from", {}).get("id", ""))
        data = callback_query.get("data", "")

        await self._tg_answer_callback(cb_id)

        # Menu navigation
        if data.startswith("menu:"):
            menu_name = data.replace("menu:", "")
            return await self._handle_menu_nav(menu_name, chat_id)

        # Command routing from buttons
        response: str | None = None
        if data.startswith("cmd:"):
            cmd_full = data.replace("cmd:", "")
            cmd_parts = cmd_full.split()
            cmd_name = cmd_parts[0]
            cmd_args = cmd_parts[1:]
            handler = self._command_handlers.get(cmd_name)
            if handler:
                response = await handler(cmd_args, chat_id=chat_id)

        if response:
            await self._tg_send(response, chat_id=chat_id)
            return response

        # Legacy callbacks (trade, payment)
        if data.startswith("trade:") or data.startswith("skip:"):
            return self._handle_trade_callback(chat_id, data)

        if data.startswith("pay:") or data.startswith("check:") or data.startswith("donate:"):
            return self._handle_payment_callback(chat_id, data)

        return None

    async def _handle_menu_nav(self, menu_name: str, chat_id: str) -> str:
        from tradebot.services.menu import get_inline_keyboard, get_menu_text

        text = get_menu_text(menu_name)
        kb = get_inline_keyboard(menu_name)
        await self._tg_send(text, chat_id=chat_id, reply_markup=kb)
        return ""

    def _handle_trade_callback(self, chat_id: str, data: str) -> str:
        if chat_id not in self._pending_signals:
            return "⏰ Sinyal kadaluarsa. Kirim /analyze lagi."

        pending = self._pending_signals.pop(chat_id, {})
        sig = pending.get("sig")
        price = pending.get("price", 0)

        if not sig:
            return ""

        if data.startswith("trade:"):
            action = sig.get("action", "HOLD")
            if action == "HOLD":
                return "⚪ Sinyal HOLD — tidak ada trade."
            sig["target_user"] = chat_id
            self.bridge.post_signal(sig, price)
            return f"✅ <b>Sinyal {action} dikirim!</b>\nEA kamu auto-eksekusi dalam 5 detik."
        else:
            return "⏭ Sinyal dilewati. Analisa lagi: /analyze"

    def _handle_payment_callback(self, chat_id: str, data: str) -> str:
        if data == "donate:coffee":
            amount = 15000
            DONATION_INPUT_STATE.pop(chat_id, None)
            return (
                "💚 <b>Dukungan Rp15,000 (Kopi)</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Terima kasih! Hubungi admin @codergaboets\n"
                "untuk instruksi pembayaran.\n\n"
                "🔥 <i>Server AI butuh kopi biar makin ganas!</i>"
            )
        if data == "donate:fuel":
            amount = 50000
            DONATION_INPUT_STATE.pop(chat_id, None)
            return (
                "💚 <b>Dukungan Rp50,000 (Bensin Full)</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Terima kasih! Hubungi admin @codergaboets\n"
                "untuk instruksi pembayaran.\n\n"
                "🚀 <i>Bensin full! AI siap cetak profit!</i>"
            )
        if data == "donate:custom":
            DONATION_INPUT_STATE[chat_id] = True
            return (
                "💚 <b>Dukung Server AI</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Server AI 24/7 butuh biaya API & GPU.\n\n"
                "💰 Ketik nominal donasi:\n"
                "Contoh: 50000 (Rp50.000)\n\n"
                "Atau hubungi admin: @codergaboets"
            )
        return "💳 Payment gateway: hubungi admin @codergaboets"

    async def _handle_donation_input(self, chat_id: str, text: str) -> str:
        try:
            amount = int(text.replace(".", "").replace(",", ""))
            if amount < 10000:
                return "💰 Minimal Rp10,000. Silakan ketik nominal lain."
            DONATION_INPUT_STATE.pop(chat_id, None)
            # Try Tripay payment engine
            from tradebot.services.payment_service import create_tripay_invoice
            try:
                invoice = create_tripay_invoice(
                    chat_id, amount, f"Donasi {chat_id} - Rp{amount:,}"
                )
                if invoice and invoice.get("pay_url"):
                    return (
                        f"💚 <b>Dukungan Rp{amount:,}</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"🔗 <a href='{invoice['pay_url']}'>Klik bayar di sini</a>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"Terima kasih atas dukunganmu! 🙏"
                    )
            except Exception:
                pass
            # Payment engine offline — show manual transfer instructions
            return (
                f"💚 <b>Dukungan Rp{amount:,}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"<b>Manual Transfer</b>\n\n"
                f"🏦 BCA: <b>8531425531</b>\n"
                f"   a.n. <b>MOH SUHUD</b>\n\n"
                f"📱 Dana/GoPay:\n"
                f"Hubungi admin @codergaboets\n\n"
                f"📸 Kirim bukti transfer ke admin.\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Terima kasih atas dukunganmu! 🙏"
            )
        except ImportError:
            DONATION_INPUT_STATE.pop(chat_id, None)
            return (
                f"💚 <b>Dukungan Rp{amount:,}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"<b>Manual Transfer</b>\n\n"
                f"🏦 BCA: <b>8531425531</b>\n"
                f"   a.n. <b>MOH SUHUD</b>\n\n"
                f"📱 Dana/GoPay:\n"
                f"Hubungi admin @codergaboets\n\n"
                f"📸 Kirim bukti transfer ke admin.\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Terima kasih atas dukunganmu! 🙏"
            )
        except ValueError:
            return "❌ Nominal tidak valid. Ketik angka saja (contoh: 50000)."
