"""CallbackHandlersMixin — menu navigation, trade, and payment callbacks."""

from __future__ import annotations

import logging
from typing import Any

from tradebot.bots.base import BaseBot
from tradebot.bots.platforms.vilona.helpers import DONATION_INPUT_STATE

LOG = logging.getLogger("tradebot.bots.vilona.callbacks")
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    class _BotProtocol(BaseBot):
        async def _tg_send(self, text: str, chat_id: str | None = None, reply_markup: dict | None = None, reply_to: int | None = None) -> bool: ...
        async def _tg_send_photo(self, photo_bytes: bytes, caption: str = "", chat_id: str | None = None, reply_markup: dict | None = None) -> bool: ...
        async def _tg_answer_callback(self, cb_id: str, text: str = "", show_alert: bool = False) -> bool: ...
        _default_pair: str
        _pending_signals: dict
        
        async def _cmd_analyze(self, args: list[str], chat_id: str | None = None) -> str: ...
        async def _cmd_engines(self, args: list[str], chat_id: str | None = None) -> str: ...
        async def _cmd_portfolio(self, args: list[str], chat_id: str | None = None) -> str: ...
        async def _cmd_trade(self, args: list[str], chat_id: str | None = None) -> str: ...
        async def _cmd_autotrade(self, args: list[str], chat_id: str | None = None) -> str: ...
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

        await self._tg_answer_callback(cb_id)

        if not chat_id or not data:
            return None

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

    async def _handle_menu_nav(self, menu_name: str, chat_id: str) -> str:
        from tradebot.services.menu import get_inline_keyboard, get_menu_text

        text = get_menu_text(menu_name)
        kb = get_inline_keyboard(menu_name)
        await self._tg_send(text, chat_id=chat_id, reply_markup=kb)
        return ""

    async def _handle_trade_callback(self, chat_id: str, data: str) -> str:
        if chat_id not in self._pending_signals:
            msg = "⏰ Sinyal kadaluarsa. Kirim /analyze lagi."
            await self._tg_send(msg, chat_id=chat_id)
            return msg

        pending = self._pending_signals.pop(chat_id, {})
        sig = pending.get("sig")
        price = pending.get("price", 0)

        if not sig:
            return ""

        if data.startswith("trade:"):
            action = sig.get("action", "HOLD")
            if action == "HOLD":
                msg = "⚪ Sinyal HOLD — tidak ada trade."
                await self._tg_send(msg, chat_id=chat_id)
                return msg
            sig["target_user"] = chat_id
            from tradebot.bots.platforms.vilona.helpers import post_signal_to_bridge

            post_signal_to_bridge(sig, price)
            msg = f"✅ <b>Sinyal {action} dikirim!</b>\nEA kamu auto-eksekusi dalam 5 detik."
            await self._tg_send(msg, chat_id=chat_id)
            self._save_pending_signals()
            return msg
        else:
            msg = "⏭ Sinyal dilewati. Analisa lagi: /analyze"
            await self._tg_send(msg, chat_id=chat_id)
            self._save_pending_signals()
            return msg

    async def _handle_payment_callback(self, chat_id: str, username: str, data: str) -> str:
        if data == "cancel_input":
            DONATION_INPUT_STATE.pop(chat_id, None)
            msg = "❌ Input dibatalkan."
            await self._tg_send(msg, chat_id=chat_id)
            await self._handle_menu_nav("donate", chat_id)
            return msg

        # Pricing plans info
        pricing = {
            "pro": {"label": "⭐ PRO", "price": 50000},
            "elite": {"label": "👑 ELITE", "price": 150000},
            "lifetime": {"label": "💎 LIFETIME", "price": 500000},
        }

        if data.startswith("pay:") or data.startswith("donate:"):
            tier = "pro"
            amount = 50000
            if ":" in data:
                tier = data.split(":", 1)[1]
            if tier == "coffee":
                tier = "pro"
                amount = 15000  # legacy compat Rp15K
            elif tier == "learn":
                tier = "pro"
                amount = 25000  # legacy compat Rp25K
            elif tier == "fuel":
                tier = "elite"
                amount = 150000
            elif tier == "custom":
                DONATION_INPUT_STATE[chat_id] = True
                msg = (
                    "💰 <b>Ketik nominal donasi kamu:</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "Silakan ketik angka saja (contoh: <code>50000</code>)\n"
                    "Minimal nominal Rp10.000."
                )
                markup = {
                    "inline_keyboard": [[{"text": "❌ Batal", "callback_data": "cancel_input"}]]
                }
                await self._tg_send(msg, chat_id=chat_id, reply_markup=markup)
                return msg
            else:
                pkg = pricing.get(tier, pricing["pro"])
                amount = pkg["price"]

            msg_creating = f"⏳ <b>Membuat invoice...</b>\nPaket: {tier.upper()} — Rp{amount:,}"
            await self._tg_send(msg_creating, chat_id=chat_id)

            try:
                from tradebot.services.payment import PaymentService

                svc = PaymentService()
                result = await svc.create_tripay_transaction(
                    user_id=chat_id,
                    username=username,
                    amount=amount,
                    method="QRIS2",
                )
                if result.get("success") is False:
                    raise ValueError(result.get("error", "Failed"))

                pay_url = result.get("data", {}).get("checkout_url", "")
                pay_code = result.get("data", {}).get("pay_code", "")
                ref = result.get("data", {}).get("reference", "")

                txt = (
                    f"💳 <b>Invoice — {tier.upper()}</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"💰 Total: <b>Rp{amount:,}</b>\n"
                )
                if pay_code:
                    txt += f"📱 Kode Bayar: <code>{pay_code}</code>\n"
                txt += "⏰ Expired: 1 jam\n━━━━━━━━━━━━━━━━\nKlik tombol di bawah untuk bayar:"

                markup = {
                    "inline_keyboard": [
                        [{"text": f"💳 Bayar Rp{amount:,}", "url": pay_url}] if pay_url else [],
                        [
                            {"text": "🔄 Cek Status", "callback_data": f"check:{ref}"},
                            {"text": "📞 Admin", "url": "https://t.me/codergaboets"},
                        ],
                    ]
                }
                await self._tg_send(txt, chat_id=chat_id, reply_markup=markup)
                return txt
            except Exception as e:
                LOG.error("Failed to create Tripay payment: %s", e)
                fallback = (
                    "💚 <b>Sistem Pembayaran Sibuk</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "<b>Manual Transfer</b>\n\n"
                    "🏦 BCA: <b>8531425531</b>\n"
                    "   a.n. <b>MOH SUHUD</b>\n\n"
                    "📱 Dana/GoPay:\n"
                    "Hubungi admin @codergaboets untuk konfirmasi manual.\n\n"
                    "Terima kasih atas dukunganmu! 🙏"
                )
                await self._tg_send(fallback, chat_id=chat_id)
                return fallback

        elif data.startswith("check:"):
            ref = data.split(":", 1)[1] if ":" in data else ""
            if not ref:
                msg = "❌ Referensi tidak valid."
                await self._tg_send(msg, chat_id=chat_id)
                return msg

            await self._tg_send("🔍 <b>Cek Status Pembayaran ke Tripay...</b>", chat_id=chat_id)

            try:
                from tradebot.services.payment import PaymentService

                svc = PaymentService()
                result = await svc.get_tripay_transaction(ref)

                status = "PENDING"
                if result.get("success") and result.get("data"):
                    status = result["data"].get("status", "").upper()

                if status in ("PAID", "SUCCESS", "SETTLEMENT"):
                    from tradebot.services.members_service import (
                        activate_premium,
                        mark_payment_paid,
                    )

                    activate_premium(chat_id, "donor", 9999)
                    mark_payment_paid(ref)

                    msg = (
                        "✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
                        "━━━━━━━━━━━━━━━━\n"
                        "👑 Status kamu sekarang: <b>DONATUR VIP</b>\n"
                        "♾️ /analyze — UNLIMITED\n"
                        "🤖 EA Auto-Trade — AKTIF PERMANEN\n\n"
                        "Download EA: https://bit.ly/vilona-ea\n"
                        "Channel: @vilonaaichanel\n"
                        "Group: @vilona_tradefx_group\n\n"
                        "Mari cetak profit! 🔥"
                    )
                    await self._tg_send(msg, chat_id=chat_id)
                    return msg
                else:
                    msg = (
                        "⏳ <b>Pembayaran Belum Terkonfirmasi</b>\n"
                        "━━━━━━━━━━━━━━━━\n"
                        "Tripay belum menerima pembayaran untuk invoice ini.\n"
                        "Pastikan kamu sudah menyelesaikan pembayaran.\n\n"
                        "Biasanya butuh 1-5 menit setelah transfer.\n"
                        "Kalau sudah lebih dari 10 menit, hubungi admin."
                    )
                    await self._tg_send(msg, chat_id=chat_id)
                    return msg
            except Exception as e:
                LOG.error("Tripay check status failed: %s", e)
                msg = "⚠️ <b>Cek status gagal.</b> Coba lagi nanti atau kirim bukti pembayaran ke admin: @codergaboets"
                await self._tg_send(msg, chat_id=chat_id)
                return msg

        elif data.startswith("pricing:"):
            # Resend donate menu with pricing options
            await self._handle_menu_nav("donate", chat_id)
            return ""

        return "💳 Hubungi @codergaboets untuk subscribe"

    async def _handle_donation_input(self, chat_id: str, text: str) -> str:
        try:
            amount = int(text.replace(".", "").replace(",", ""))
            if amount < 10000:
                return "💰 Minimal Rp10.000. Silakan ketik nominal lain."
            DONATION_INPUT_STATE.pop(chat_id, None)

            try:
                from tradebot.services.payment import PaymentService

                svc = PaymentService()
                result = await svc.create_tripay_transaction(
                    user_id=chat_id,
                    username=f"User{chat_id}",
                    amount=amount,
                    method="QRIS2",
                )
                if result.get("success") is False:
                    raise ValueError(result.get("error", "Failed"))

                pay_url = result.get("data", {}).get("checkout_url", "")
                pay_code = result.get("data", {}).get("pay_code", "")
                ref = result.get("data", {}).get("reference", "")

                txt = f"💚 <b>Dukungan Rp{amount:,}</b>\n━━━━━━━━━━━━━━━━\n"
                if pay_code:
                    txt += f"📱 Kode Bayar: <code>{pay_code}</code>\n"
                txt += "⏰ Expired: 1 jam\n━━━━━━━━━━━━━━━━\nKlik tombol di bawah untuk bayar:"

                markup = {
                    "inline_keyboard": [
                        [{"text": f"💳 Bayar Rp{amount:,}", "url": pay_url}] if pay_url else [],
                        [
                            {"text": "🔄 Cek Status", "callback_data": f"check:{ref}"},
                            {"text": "📞 Admin", "url": "https://t.me/codergaboets"},
                        ],
                    ]
                }
                await self._tg_send(txt, chat_id=chat_id, reply_markup=markup)
                return ""
            except Exception as e:
                LOG.error("Tripay custom donation invoice failed: %s", e)
                # Payment engine offline — show manual transfer instructions
                return (
                    f"💚 <b>Dukungan Rp{amount:,}</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"<b>Manual Transfer</b>\n\n"
                    f"🏦 BCA: <b>8531425531</b>\n"
                    f"   a.n. <b>MOH SUHUD</b>\n\n"
                    f"📱 Dana/GoPay:\n"
                    f"Hubungi admin @codergaboets untuk konfirmasi manual.\n\n"
                    f"Terima kasih atas dukunganmu! 🙏"
                )
        except ValueError:
            return "❌ Nominal tidak valid. Ketik angka saja (contoh: 50000)."
