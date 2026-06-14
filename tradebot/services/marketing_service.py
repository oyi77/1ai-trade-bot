"""Marketing funnels and broadcasting tools."""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from tradebot.services.members_service import _conn, init_db
from tradebot.services.trade_tracker_service import get_recent_trades, get_stats

# Setup logging
LOG = logging.getLogger("tradebot.services.marketing_service")
WIB = timezone(timedelta(hours=7))


class MarketingService:
    def __init__(self, bot=None):
        """Optionally inject UnifiedBot or TelegramService instance."""
        self.bot = bot
        self.channel_id = "-1003257064212"  # Hardcoded in legacy script
        self.bot_username = "@berkahkaryaforexbotbot"
        self.usd_idr = 16350

    def get_free_users(self) -> list[dict]:
        """Get all free trial users (starter/trial, not paid)."""
        init_db()
        with _conn() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT chat_id, nama, tier, status FROM members "
                "WHERE tier IN ('starter', 'trial') OR status != 'active'"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_premium_count(self) -> int:
        init_db()
        with _conn() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT COUNT(*) as n FROM members WHERE tier NOT IN ('starter', 'trial') AND status = 'active'"
            ).fetchone()
            return row["n"] if row else 0

    def get_tier_counts(self) -> dict[str, int]:
        init_db()
        with _conn() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT tier, COUNT(*) as n FROM members GROUP BY tier").fetchall()
            return {r["tier"]: r["n"] for r in rows}

    def fmt_weekly_pnl(self, stats: dict, recent: list) -> str:
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = stats.get("total", 0)
        wr = stats.get("win_rate", 0)
        total_pips = stats.get("total_pips", 0)
        total_usd = stats.get("total_profit_usd", stats.get("micro_profit", 0))

        if wr >= 60:
            perf_emoji, grade = "🟢", "BULLISH"
        elif wr >= 45:
            perf_emoji, grade = "🟡", "NEUTRAL"
        else:
            perf_emoji, grade = "🔴", "ROUGH WEEK"

        lines = [
            "📊 <b>WEEKLY S-TIER PERFORMANCE</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🗓 {(datetime.now(WIB) - timedelta(days=7)).strftime('%d %b')} — {datetime.now(WIB).strftime('%d %b %Y')}",
            "",
            f"{perf_emoji} <b>Win Rate: {wr:.1f}%</b> — {grade}",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📈 Total Signals: {total}",
            f"✅ Wins: {wins} | ❌ Losses: {losses}",
            f"📐 Total Pips: {total_pips:+.1f}",
            f"💰 <b>Net P&L: ${total_usd:+,.2f}</b>",
            f"💵 ≈ Rp {total_usd * self.usd_idr:+,.0f}",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if recent:
            lines.append("📋 <b>5 TRADE TERAKHIR:</b>")
            for t in recent:
                emoji = "✅" if t.get("outcome") in ("TP_HIT", "WON") else "❌"
                pips = t.get("pips", 0)
                profit = t.get("profit_usd", 0)
                lines.append(
                    f"  {emoji} {t.get('action', '?')} {t.get('symbol', '?')} | {pips:+.0f} pip | ${profit:+.0f}"
                )
            lines.append("")

        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━━━━━",
                "👑 <b>PREMIUM MEMBERS ALREADY PROFITED</b>",
                "",
                "🔥 Mau sinyal ini real-time setiap hari?",
                "⭐ <b>/subscribe PRO</b> — Rp50rb/bulan (20 sinyal/hari)",
                "💀 <b>/subscribe ELITE</b> — Rp150rb/bulan (Unlimited + GPT-4o + Grok + S-TIER)",
                "",
                "⚠️ <i>Past performance ≠ future results.</i>",
            ]
        )

        return "\n".join(lines)

    def fmt_flash_sale(self, free_count: int, premium_count: int) -> str:
        slots_left = max(3, 15 - premium_count)
        return "\n".join(
            [
                "⚡ <b>FLASH PRO SALE — 24 JAM ONLY!</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
                f"👥 <b>{free_count}</b> free users — <b>{slots_left} slot</b> tersisa!",
                "",
                "🔥 <b>HARGA HARI INI:</b>",
                "⭐ PRO: <b>Rp25rb</b>/bulan (normal Rp50rb)",
                "   → 50% OFF — 20 sinyal/hari",
                "",
                "💀 ELITE: <b>Rp75rb</b>/bulan (normal Rp150rb)",
                "   → 50% OFF — Unlimited + GPT-4o + Grok + S-TIER",
                "",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "⏰ <b>HANYA 24 JAM — SLOT TERBATAS</b>",
                "🔑 Harga naik setelah slot penuh atau besok.",
                "",
                "👉 <b>/subscribe</b> sekarang sebelum kehabisan!",
                "",
                f"📲 DM {self.bot_username}",
            ]
        )

    def fmt_free_teaser(self, stats: dict, recent: list) -> str:
        if stats:
            wr = stats.get("win_rate", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            total_usd = stats.get("total_profit_usd", 0)
        else:
            wr, wins, losses, total_usd = 0, 0, 0, 0

        return "\n".join(
            [
                "🎁 <b>1 SINYAL GRATIS BUAT LO — dari Vilona</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "Lo belum pernah ngerasain S-TIER.",
                "",
                "📊 <b>TRACK RECORD MINGGU INI:</b>",
                f"   ✅ {wins} Wins | ❌ {losses} Losses",
                f"   📈 Win Rate: {wr:.1f}%",
                f"   💰 Net P&L: ${total_usd:+,.0f}",
                "",
                "🔥 <b>S-TIER SIGNAL LO HARI INI:</b>",
                "   💀 Triple Confluence SMC",
                "   🔬 SnR Precision Entry",
                "",
                "⚠️ <i>Signal dikirim terpisah — cek DM berikutnya.</i>",
                "",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "💡 <b>Mau dapet ini SETIAP HARI?</b>",
                "",
                "⭐ <b>PRO — Rp25rb/bulan (FLASH SALE)</b>",
                "   /subscribe",
                "",
                "👑 87% user masih FREE — lo bisa ahead.",
            ]
        )

    async def execute_blast(self, blast_type: str, dry_run: bool) -> None:
        free_users = self.get_free_users()
        premium_n = self.get_premium_count()
        tier_counts = self.get_tier_counts()

        try:
            stats = get_stats()
            recent = get_recent_trades(5)
        except Exception as e:
            LOG.warning(f"Trade tracker error: {e}")
            stats, recent = None, []

        LOG.info(f"Users: {len(free_users)} free | {premium_n} premium")
        LOG.info(f"Tiers: {tier_counts}")
        if stats:
            LOG.info(
                f"Weekly: {stats.get('wins', 0)}W/{stats.get('losses', 0)}L | WR={stats.get('win_rate', 0):.1f}%"
            )

        if blast_type == "weekly":
            if stats:
                text = self.fmt_weekly_pnl(stats, recent)
                if dry_run:
                    print("=== WEEKLY P&L ===")
                    print(text)
                else:
                    if self.bot:
                        await self.bot._tg_send(text, self.channel_id)
                        LOG.info("✅ Weekly P&L posted to channel")
            else:
                LOG.warning("No stats — skipping weekly P&L")

        elif blast_type == "flash":
            text = self.fmt_flash_sale(len(free_users), premium_n)
            if dry_run:
                print("\n=== FLASH SALE ===")
                print(text)
            else:
                if self.bot:
                    await self.bot._tg_send(text, self.channel_id)
                sent = 0
                for u in free_users:
                    cid = str(u.get("chat_id", ""))
                    if not cid or cid.startswith("test"):
                        continue
                    try:
                        if self.bot:
                            await self.bot._tg_send(text, cid)
                        sent += 1
                        await asyncio.sleep(0.35)
                    except Exception as e:
                        LOG.warning(f"Flash DM failed for {cid}: {e}")
                LOG.info(f"✅ Flash sale DM'd to {sent}/{len(free_users)} free users")

        elif blast_type == "freetier":
            text = self.fmt_free_teaser(stats or {}, recent)
            if dry_run:
                print("\n=== FREE TEASER ===")
                print(text)
            else:
                sent = 0
                for u in free_users:
                    cid = str(u.get("chat_id", ""))
                    if not cid or cid.startswith("test"):
                        continue
                    try:
                        if self.bot:
                            await self.bot._tg_send(text, cid)
                        await asyncio.sleep(0.35)
                    except Exception as e:
                        LOG.warning(f"Teaser DM failed for {cid}: {e}")
                LOG.info(f"Free teaser DM'd to {sent}/{len(free_users)} free users")

        elif blast_type == "referral":
            text = (
                "🤝 <b>GOTONG ROYONG — VILONA REFERRAL PROGRAM</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Udah ngerasain tajamnya sinyal AI Vilona?\n"
                "Ajak temen-temen trader lu gabung!\n\n"
                "💰 <b>REWARD REFERRAL:</b>\n"
                "  • 3 Teman Gabung → <b>PRO 7 Hari GRATIS!</b>\n"
                "  • 10 Teman Gabung → <b>ELITE 30 Hari GRATIS!</b>\n\n"
                "📋 <b>Cara dapetin link:</b>\n"
                "  1. Ketik /referral di bot\n"
                "  2. Copy link referral kamu\n"
                "  3. Share ke grup WA, Telegram, sosmed\n\n"
                "🎯 Semakin banyak trader pakai Vilona,\n"
                "   semakin besar data loop AI kita →\n"
                "   sinyal makin tajam buat semua member.\n\n"
                "<b>WIN-WIN. GOTONG ROYONG.</b> 🇮🇩\n\n"
                f"🔗 {self.bot_username} → /referral"
            )
            if dry_run:
                print("\n=== REFERRAL ===")
                print(text)
            else:
                if self.bot and self.channel_id:
                    await self.bot._tg_send(text, self.channel_id)
                sent = 0
                for u in free_users:
                    cid = str(u.get("chat_id", ""))
                    if not cid or cid.startswith("test"):
                        continue
                    try:
                        if self.bot:
                            await self.bot._tg_send(
                                "💡 <b>Gak perlu bayar buat upgrade!</b>\n\n"
                                "Ajak 3 teman trader join Vilona →\n"
                                "lu dapet <b>PRO 7 hari GRATIS!</b>\n\n"
                                "🔗 Cek link lu: /referral",
                                cid,
                            )
                        sent += 1
                        await asyncio.sleep(0.35)
                    except Exception as e:
                        LOG.warning(f"Referral DM failed for {cid}: {e}")
                LOG.info(f"Referral DM'd to {sent}/{len(free_users)} free users")
