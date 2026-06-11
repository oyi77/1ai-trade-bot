"""
Whitelabel Multi-Bot Runner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs multiple Telegram bots with DIFFERENT tokens but SHARED backend.
Each brand has its own bot, branding, and commission tracking.

Architecture:
  Vilona Bot (token A) ─┐
                         ├─→ Shared Backend
  1AI Bot (token B) ────┤    ├─ Signal Engine (Ollama)
                         │    ├─ Payment Processor
  Reseller Bot (token C)─┘    ├─ Commission Tracker
                               ├─ Referral System
                               └─ Whitelabel Manager

Commission Flow:
  User pays → Platform gets 70% → Reseller gets 20% → Referrer gets 10%
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import qrcode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

LOG = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "data/whitelabel"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Brand Registry ──────────────────────────────────────────────────────

@dataclass
class BrandConfig:
    brand_id: str
    name: str
    token: str
    username: str
    description: str
    primary_color: str = "#2563eb"
    logo: str = ""
    welcome_msg: str = ""
    owner_cut: float = 0.70       # Platform owner gets 70%
    reseller_cut: float = 0.20    # Reseller/whitelabel partner gets 20%
    referrer_cut: float = 0.10    # Referrer gets 10%
    payment_methods: list = field(default_factory=lambda: ["tripay", "qris"])
    is_active: bool = True

    @property
    def bot_link(self) -> str:
        return f"https://t.me/{self.username}"

# ── Registered Brands ──────────────────────────────────────────────────

BRANDS = {
    "vilona": BrandConfig(
        brand_id="vilona",
        name="Vilona TradeFX",
        token="8657491144:AAHDNmGD5TWBhfcDfqN6wTpIJII2xlsU-ag",
        username="vilona_tradefx_bot",
        description="Bot trading AI 24/7 untuk analisa market",
        primary_color="#1a73e8",
        welcome_msg="🔥 VILONA TRADEFX — AI POWERED TRADING",
        owner_cut=0.70,
        reseller_cut=0.20,
        referrer_cut=0.10,
    ),
    "1ai": BrandConfig(
        brand_id="1ai",
        name="1AI Agent",
        token="8343388239:AAFgeAkc9bvjywyCsHqRIa_RiJ6q-rp6uv0",
        username="agent_1ai2_bot",
        description="AI Trading Bot dengan analisa multi-indicator",
        primary_color="#10b981",
        welcome_msg="🔥 1AI TRADING AGENT — AI POWERED",
        owner_cut=0.70,
        reseller_cut=0.20,
        referrer_cut=0.10,
    ),
}

# ── Commission Tracker ─────────────────────────────────────────────────

COMMISSION_FILE = DATA_DIR / "commissions.json"
WHITELABEL_FILE = DATA_DIR / "whitelabel_resellers.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2))


def track_commission(
    payment_id: str,
    amount: float,
    user_id: str,
    brand_id: str,
    reseller_id: Optional[str] = None,
    referrer_id: Optional[str] = None,
):
    """Track payment commission distribution."""
    brand = BRANDS.get(brand_id)
    if not brand:
        LOG.warning("Unknown brand: %s", brand_id)
        return

    commissions = _load_json(COMMISSION_FILE)
    entry = {
        "payment_id": payment_id,
        "amount": amount,
        "user_id": user_id,
        "brand_id": brand_id,
        "timestamp": datetime.now().isoformat(),
        "distributions": [],
    }

    # Platform owner
    owner_share = round(amount * brand.owner_cut, 2)
    entry["distributions"].append({
        "to": "platform_owner",
        "role": "owner",
        "amount": owner_share,
        "percentage": brand.owner_cut * 100,
    })

    # Reseller/whitelabel partner
    if reseller_id:
        reseller_share = round(amount * brand.reseller_cut, 2)
        entry["distributions"].append({
            "to": reseller_id,
            "role": "reseller",
            "amount": reseller_share,
            "percentage": brand.reseller_cut * 100,
        })

    # Referrer
    if referrer_id:
        referrer_share = round(amount * brand.referrer_cut, 2)
        entry["distributions"].append({
            "to": referrer_id,
            "role": "referrer",
            "amount": referrer_share,
            "percentage": brand.referrer_cut * 100,
        })

    commissions[payment_id] = entry
    _save_json(COMMISSION_FILE, commissions)
    LOG.info("Commission tracked: payment=%s amount=%.2f brand=%s", payment_id, amount, brand_id)


def get_reseller_commission(reseller_id: str) -> float:
    """Get total commission earned by a reseller."""
    commissions = _load_json(COMMISSION_FILE)
    total = 0.0
    for entry in commissions.values():
        for dist in entry.get("distributions", []):
            if dist["to"] == reseller_id and dist["role"] == "reseller":
                total += dist["amount"]
    return total


def get_referrer_commission(referrer_id: str) -> float:
    """Get total commission earned by a referrer."""
    commissions = _load_json(COMMISSION_FILE)
    total = 0.0
    for entry in commissions.values():
        for dist in entry.get("distributions", []):
            if dist["to"] == referrer_id and dist["role"] == "referrer":
                total += dist["amount"]
    return total


# ── Whitelabel Reseller Manager ────────────────────────────────────────


def register_reseller(reseller_id: str, brand_id: str, name: str, token: str = "") -> bool:
    """Register a whitelabel reseller."""
    wl = _load_json(WHITELABEL_FILE)
    if reseller_id in wl:
        return False
    wl[reseller_id] = {
        "reseller_id": reseller_id,
        "brand_id": brand_id,
        "name": name,
        "token": token or f"new_bot_token_{reseller_id}",
        "commission_earned": 0.0,
        "registered_at": datetime.now().isoformat(),
        "status": "active",
    }
    _save_json(WHITELABEL_FILE, wl)
    return True


def get_reseller(reseller_id: str) -> Optional[dict]:
    return _load_json(WHITELABEL_FILE).get(reseller_id)


def get_all_resellers() -> list[dict]:
    return list(_load_json(WHITELABEL_FILE).values())


# ── Multi-Bot Runner ────────────────────────────────────────────────────


class WhitelabelBot:
    """A single bot instance for a specific brand."""

    def __init__(self, brand: BrandConfig):
        self.brand = brand
        self.app = Application.builder().token(brand.token).build()
        self._register_handlers()
        LOG.info("Bot %s initialized (token: ...%s)", brand.brand_id, brand.token[-6:])

    def _register_handlers(self):
        # Register brand-specific and shared handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("donate", self.cmd_donate))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

    async def _reply(self, update: Update, text: str, keyboard=None):
        try:
            if update.callback_query:
                await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
                await update.callback_query.answer()
            elif update.message:
                await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            LOG.warning("Reply error: %s", e)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        b = self.brand
        name = update.effective_user.first_name or "Trader"
        txt = (
            f"🔥 <b>{b.welcome_msg}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Halo <b>{name}</b>!\n"
            f"Selamat datang di <b>{b.name}</b>.\n"
            f"{b.description}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 /analyze — AI Market Scan\n"
            f"📱 /help — Semua command\n"
            f"💚 /donate — Dukung server AI"
        )
        await self._reply(update, txt, keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Analyze", callback_data="analyze"),
             InlineKeyboardButton("💚 Donate", callback_data="donate")],
            [InlineKeyboardButton("👤 Status", callback_data="status"),
             InlineKeyboardButton("📱 Help", callback_data="help")],
        ]))

    async def cmd_help(self, update, ctx):
        await self._reply(update,
            f"⚙️ <b>{self.brand.name} — COMMAND CENTER</b>\n━━━━━━━━━━━━━━━━\n"
            f"/start — Mulai\n/help — Bantuan\n/status — Status\n/donate — Donasi",
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali", callback_data="menu_main")]]))

    async def cmd_status(self, update, ctx):
        name = update.effective_user.first_name or "Trader"
        await self._reply(update,
            f"📊 <b>STATUS — {self.brand.name}</b>\n━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {name}\n"
            f"🏷️ <b>Brand:</b> {self.brand.name}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💡 Gunakan /donate untuk dukung server AI!",
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("« Menu", callback_data="menu_main")]]))

    async def cmd_donate(self, update, ctx):
        await self._reply(update,
            f"💚 <b>DONASI — {self.brand.name}</b>\n━━━━━━━━━━━━━━━━\n"
            f"Dukung server AI tetap hidup!\n\n"
            f"🏦 BCA 1234567890 a.n {self.brand.name}\n"
            f"📲 QRIS (semua e-wallet)\n\n"
            f"Setelah transfer, kirim bukti ke admin.",
            keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton("☕️ Kopi (Rp 15K)", callback_data="donate:coffee")],
                [InlineKeyboardButton("🚀 Bensin Full (Rp 50K)", callback_data="donate:fuel")],
                [InlineKeyboardButton("« Kembali", callback_data="menu_main")],
            ]))

    async def handle_callback(self, update, ctx):
        q = update.callback_query; d = q.data
        if d == "menu_main":
            name = q.from_user.first_name or "Trader"
            await self.cmd_start(update, ctx)
        elif d == "help":
            await self.cmd_help(update, ctx)
        elif d == "status":
            await self.cmd_status(update, ctx)
        elif d == "donate":
            await self.cmd_donate(update, ctx)
        elif d.startswith("donate:"):
            tier = d.split(":", 1)[1]
            amounts = {"coffee": 15000, "lunch": 25000, "fuel": 50000}
            amount = amounts.get(tier)
            if not amount:
                await q.answer("Nominal tidak valid")
                return
            await q.answer("Membuat pembayaran...")
            await self._reply(update,
                f"⏳ <b>MEMBUAT PEMBAYARAN...</b>\n━━━━━━━━━━━━━━━━\n"
                f"Mohon tunggu, sedang dibuatkan link pembayaran...")

            payment_url = None
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
                from scripts.payment_tripay import create_transaction
                user = update.effective_user
                cid = str(user.id)
                uname = user.username or user.first_name or "Trader"
                result = create_transaction(
                    user_id=cid, username=uname, amount=amount,
                    brand_id=self.brand.brand_id)
                if result.get("success") and result.get("data", {}).get("checkout_url"):
                    payment_url = result["data"]["checkout_url"]
            except Exception as e:
                LOG.warning("Tripay transaction failed: %s", e)

            if payment_url:
                try:
                    img = qrcode.make(payment_url)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    buf.seek(0)
                    price_str = f"Rp{amount:,}"
                    caption = (
                        f"💚 <b>PEMBAYARAN — {self.brand.name}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 <b>{price_str}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Scan QR di atas untuk bayar.\n\n"
                        f"⏳ Pembayaran kedaluwarsa dalam 1 jam.\n"
                        f"Setelah bayar, status akan otomatis ter-update.\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📞 Admin: @codergaboets"
                    )
                    if update.callback_query:
                        await update.callback_query.message.delete()
                    await update.effective_message.reply_photo(
                        photo=InputFile(buf, filename="payment_qr.png"),
                        caption=caption, parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 Buka Link", url=payment_url)],
                            [InlineKeyboardButton("« Kembali", callback_data="donate")],
                        ]))
                except Exception as e:
                    LOG.warning("QR send failed, falling back to text: %s", e)
                    await self._reply(update,
                        f"💚 <b>PEMBAYARAN — {self.brand.name}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 <b>{price_str}</b>\n\n"
                        f"🔗 <a href='{payment_url}'>Klik untuk bayar</a>\n\n"
                        f"⏳ Kedaluwarsa dalam 1 jam.",
                        keyboard=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 Bayar Sekarang", url=payment_url)],
                            [InlineKeyboardButton("« Kembali", callback_data="donate")],
                        ]))
            else:
                await self._reply(update,
                    f"💚 <b>DONASI — {self.brand.name}</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"Paket: {tier}\n"
                    f"Nominal: Rp{amount:,}\n\n"
                    f"🏦 BCA 1234567890 — {self.brand.name}\n"
                    f"📲 QRIS (semua e-wallet)\n\n"
                    f"📞 Admin: @codergaboets",
                    keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali", callback_data="donate")]]))
        else:
            await q.answer("Fitur dalam pengembangan...")

    def run(self):
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)


# ── Runner ──────────────────────────────────────────────────────────────


class WhitelabelRunner:
    """Runs all brand bots simultaneously."""

    def __init__(self, brand_ids: Optional[list[str]] = None):
        self.bots: dict[str, WhitelabelBot] = {}
        brand_ids = brand_ids or list(BRANDS.keys())
        for bid in brand_ids:
            brand = BRANDS.get(bid)
            if brand and brand.is_active:
                self.bots[bid] = WhitelabelBot(brand)

    def run_all(self):
        """Run all bots concurrently using asyncio."""
        import asyncio
        from telegram.ext import Application

        LOG.info("Starting %d whitelabel bots...", len(self.bots))

        async def start_bot(bid: str, bot: WhitelabelBot):
            await bot.app.initialize()
            await bot.app.start()
            LOG.info("%s bot started", bid)

        async def main():
            tasks = [start_bot(bid, bot) for bid, bot in self.bots.items()]
            await asyncio.gather(*tasks)
            LOG.info("All whitelabel bots running")
            # Keep running
            await asyncio.Event().wait()

        asyncio.run(main())


def run_single_brand(brand_id: str):
    """Run a single brand bot (for systemd service)."""
    brand = BRANDS.get(brand_id)
    if not brand:
        LOG.error("Unknown brand: %s", brand_id)
        return
    bot = WhitelabelBot(brand)
    bot.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    import sys
    if len(sys.argv) > 1:
        run_single_brand(sys.argv[1])
    else:
        WhitelabelRunner().run_all()
