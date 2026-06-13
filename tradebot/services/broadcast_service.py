"""Broadcast service for scheduled Telegram announcements."""
from __future__ import annotations

import json
import logging
import urllib.request
import asyncio
from typing import Optional

from tradebot.services.members_service import _conn, init_db
from tradebot.services.trade_tracker_service import get_stats, get_recent_trades
from tradebot.analytics.charting import generate_signal_chart

LOG = logging.getLogger("tradebot.services.broadcast_service")


class BroadcastService:
    def __init__(self, bot=None):
        self.bot = bot
        self.channel_id = "-1003257064212"

    def _get_real_users(self) -> list[tuple[str, str]]:
        init_db()
        with _conn() as db:
            import sqlite3
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT chat_id, status FROM members").fetchall()
            
        real_users = []
        for r in rows:
            try:
                cid = str(r["chat_id"])
                if int(cid) > 0:
                    real_users.append((cid, r["status"]))
            except Exception:
                pass
        return real_users

    async def broadcast_levels(self, dry_run: bool) -> None:
        real_users = self._get_real_users()
        LOG.info(f"Broadcasting /levels to {len(real_users)} users")
        
        msg = (
            "🚀 <b>FITUR BARU! /levels — SnR + FIBO + Engine Deep Dive</b>\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "Sekarang kamu bisa analisa level support/resistance\\n"
            "langsung dari bot!\\n\\n"
            "📐 <b>Layer 1: Simple SnR + FIBO</b>\\n"
            "• Support & Resistance dengan multi-touch confirmation\\n"
            "• FIBO 38.2% / 50% / 61.8%\\n"
            "• Rekomendasi SL placement (aman dari wick)\\n\\n"
            "🏦 <b>Layer 2: Engine Deep Dive</b>\\n"
            "• SMC Order Blocks\\n"
            "• Fair Value Gaps (FVG)\\n"
            "• Liquidity Zones\\n"
            "• Session Levels\\n\\n"
            "👑 <b>Fitur Premium — Khusus Subscriber</b>\\n"
            "Free member bisa lihat command, akses penuh\\n"
            "setelah subscribe.\\n\\n"
            "🔥 Cobain sekarang: /levels xauusd\\n"
            "💚 Support AI: /subscribe"
        )

        if dry_run:
            print("=== BROADCAST LEVELS ===")
            print(msg)
            return

        sent = 0
        failed = 0
        for cid, _ in real_users:
            if self.bot:
                try:
                    await self.bot._tg_send(msg, cid)
                    sent += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    failed += 1
                    LOG.warning(f"Failed to send /levels to {cid}: {e}")
        LOG.info(f"Broadcast /levels complete: {sent} sent, {failed} failed")

    async def broadcast_tech_analysis(self, dry_run: bool) -> None:
        real_users = self._get_real_users()
        LOG.info(f"Broadcasting Technical Analysis to {len(real_users)} users")

        msg = (
            "🆕 <b>FITUR BARU — TECHNICAL ANALYSIS TERMINAL</b>\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n\\n"
            "Sekarang lu bisa analisa teknikal SMC\\n"
            "secara <b>deterministic (no AI hallucination)</b>:\\n\\n"
            "🧲 <b>/zones</b> — Order Blocks + FVG + Supply/Demand\\n"
            "🏗 <b>/structure</b> — BOS/CHoCH + Trend + MTF Alignment\\n"
            "🕐 <b>/session</b> — Killzone + Session High/Low + Range\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "🆓 <b>FREE:</b> Basic analysis (1 Timeframe)\\n"
            "👑 <b>DONOR:</b> Multi-TF + Full Depth Analysis\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "🔑 <b>Cara pake:</b> DM bot → ketik command\\n"
            "   <code>/zones xauusd</code>\\n"
            "   <code>/structure xauusd</code>\\n"
            "   <code>/session xauusd</code>\\n\\n"
            "📌 Support: XAUUSD · BTCUSD · ETHUSD · USOIL · Forex\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "💡 <i>Tools analisa, bukan sinyal.</i>\\n"
            "   Lu yang baca struktur, lu yang decide entry.\\n\\n"
            "👑 Multi-TF + Full Depth → <b>/subscribe</b>\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "⚡ Cobain sekarang — ketik /zones xauusd"
        )

        if dry_run:
            print("=== BROADCAST TECH ANALYSIS ===")
            print(msg)
            return

        sent = 0
        failed = 0
        for cid, _ in real_users:
            if self.bot:
                try:
                    await self.bot._tg_send(msg, cid)
                    sent += 1
                    await asyncio.sleep(0.6)
                except Exception as e:
                    failed += 1
                    LOG.warning(f"Failed to send tech analysis to {cid}: {e}")
        LOG.info(f"Broadcast tech analysis complete: {sent} sent, {failed} failed")

    async def broadcast_weekly_winrate(self, dry_run: bool) -> None:
        try:
            stats = get_stats()
            recent = get_recent_trades(5)
        except Exception as e:
            LOG.warning(f"Trade tracker error: {e}")
            stats, recent = None, []

        if not stats:
            LOG.warning("No stats available for weekly winrate")
            return

        wr = stats.get("win_rate", 0)
        total_idr = stats.get("total_profit_idr") or round(stats.get("total_profit_usd", 0) * 16350)
        
        if wr >= 55:
            perf_emoji, grade = "🟢", "EXCELLENT"
        elif wr >= 40:
            perf_emoji, grade = "🟡", "DECENT"
        else:
            perf_emoji, grade = "🔴", "NEED IMPROVEMENT"

        profit_idr_str = f"Rp {total_idr:+,}"
        if total_idr > 1_000_000:
            profit_idr_str += f" (Rp {total_idr/1_000_000:.1f}jt)"

        lines = [
            "📊 <b>WEEKLY PERFORMANCE REPORT</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "🗓 Minggu ini | Auto-generated",
            "",
            f"{perf_emoji} <b>Win Rate: {wr:.1f}%</b> — {grade}",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📈 Total Signals: {stats.get('total', 0)}",
            f"✅ Wins: {stats.get('wins', 0)} | ❌ Losses: {stats.get('losses', 0)}",
            f"📐 Total Pips: {stats.get('total_pips', 0):+.1f}",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Profit: ${stats.get('total_profit_usd', 0):+,.2f}</b>",
            f"💵 {profit_idr_str}",
        ]
        
        best_win = stats.get("best_win_pips", 0)
        worst_loss = stats.get("worst_loss_pips", 0)
        open_pos = stats.get("open_positions", 0)
        
        if best_win > 0:
            lines.append(f"🏆 Best Win: +{best_win:.1f} pips")
        if worst_loss > 0:
            lines.append(f"⚠️ Worst Loss: {worst_loss:.1f} pips")
        if open_pos > 0:
            lines.append(f"🔓 Open Positions: {open_pos}")

        if recent:
            lines.append("")
            lines.append("📋 <b>5 TRADE TERAKHIR:</b>")
            for t in recent:
                emoji = "✅" if t.get("outcome") == "TP_HIT" else "❌"
                pips = t.get("pips", 0)
                lines.append(f"  {emoji} {t.get('action','?')} {t.get('symbol','?')} | {pips:+.0f} pip | ${t.get('profit_usd',0):+.0f}")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "🔥 Mau dapetin sinyal ini real-time?",
            "⚡ <b>/subscribe</b> — Rp50rb/bulan (PRO)",
            "👑 <b>/subscribe</b> — Rp150rb/bulan (ELITE + GPT-4o + Grok)",
            "",
            "⚠️ <i>Past performance ≠ future results. NFA.</i>",
        ])

        msg = "\\n".join(lines)

        if dry_run:
            print("=== WEEKLY WINRATE ===")
            print(msg)
            return

        if self.bot and self.channel_id:
            try:
                await self.bot._tg_send(msg, self.channel_id)
                LOG.info("✅ Weekly winrate posted to channel")
            except Exception as e:
                LOG.warning(f"Failed to post weekly winrate: {e}")

    async def broadcast_btc_chart(self, dry_run: bool) -> None:
        caption = (
            "🟢 BUY BTCUSD ₿\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "📌 SETUP | Conf 49%\\n\\n"
            "📍 Entry: $63750\\n"
            "🔴 SL: $63100 | -650 pip\\n"
            "🟢 TP1: $64500 | +750 pip\\n"
            "🟢 TP2: $65200 | +1500 pip\\n"
            "📊 RR 1:1.4\\n"
            "━━━━━━━━━━━━━━━━━━━━━━\\n"
            "⚠️ Risk 1% per trade — verify sendiri."
        )

        chart_bytes = await generate_signal_chart(
            symbol="BINANCE:BTCUSDT",
            timeframe="15m",
            trend="BUY",
            entry=63750, sl=63100, tp1=64500, tp2=65200
        )

        if dry_run:
            print("=== BTC CHART ===")
            print(caption)
            print(f"Chart generated: {'YES' if chart_bytes else 'NO'} ({len(chart_bytes or b'')}) bytes")
            return

        if not chart_bytes:
            LOG.warning("BTC chart failed to generate bytes")
            return

        if self.bot and self.channel_id:
            try:
                import io
                await self.bot.bot.send_photo(
                    chat_id=self.channel_id,
                    photo=io.BytesIO(chart_bytes),
                    caption=caption,
                    parse_mode="HTML"
                )
                LOG.info("✅ BTC chart posted to channel")
            except Exception as e:
                LOG.warning(f"Failed to post BTC chart: {e}")
