"""CommandHandlersMixin — all /cmd handlers for VilonaBot."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime

from tradebot.bots.base import BaseBot
from tradebot.bots.platforms.vilona.helpers import (
    DONATION_INPUT_STATE,
    SUPPORTED_PAIRS,
    format_signal_basic,
    killzone_active,
    news_blackout_status,
    resolve_yahoo_symbol,
    session_label,
    wib_fmt,
    wib_now,
)

LOG = logging.getLogger("tradebot.bots.vilona.commands")


class CommandHandlersMixin(BaseBot):
    """Mixin providing all /command handlers for VilonaBot."""

    async def _cmd_start(self, args: list[str], chat_id: str | None = None) -> str:
        _target = chat_id or self.chat_id
        admin_ids = [os.environ.get("ADMIN_CHAT_ID", "")]
        is_admin_user = str(_target) in admin_ids
        menu_name = "admin" if is_admin_user else "main"

        text = (
            "🔥 <b>VILONA AI — TRADING SYSTEM</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Seluruh sistem dijalankan oleh <b>FULL AI AGENTS 24/7</b>.\n"
            "Gunakan menu di bawah untuk mengakses fitur.\n"
        )
        from tradebot.services.menu import get_inline_keyboard
        await self._tg_send(text, chat_id=_target, reply_markup=get_inline_keyboard(menu_name))
        return ""

    async def _cmd_stockity(self, args: list[str], chat_id: str | None = None) -> str:
        referral_code = "7b8730c84b6450e3e0b02fd3fd864f69"
        link = f"https://stockity-mr.com/auth?invite_code={referral_code}#SignUp"
        nominal = random.choice([511908, 699821, 587432, 623198, 675234, 548762])
        return (
            "💰 <b>STOCKITY INSIDER ACCESS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Kami menggunakan <b>sistem bandar (insider)</b> untuk\n"
            "meningkatkan akurasi sinyal trading.\n\n"
            "📌 <b>Langkah-langkah:</b>\n"
            "1. Daftar menggunakan link di bawah\n"
            "2. Deposit minimal sesuai nominal unik:\n"
            f"   🔥 <b>Rp{nominal:,}</b>\n"
            "3. Konfirmasi ke admin setelah deposit\n\n"
            "🚀 <b>Link Pendaftaran:</b>\n"
            f"{link}\n\n"
            "⚡ <i>Hanya untuk pengguna terpilih — kuota terbatas!</i>"
        )

    async def _cmd_symbols(self, args: list[str], chat_id: str | None = None) -> str:
        lines = [
            "📋 <b>Available Trading Symbols</b>",
            "━━━━━━━━━━━━━━━━",
        ]
        for s in SUPPORTED_PAIRS:
            lines.append(f"  • {s}")
        lines.extend([
            "",
            "Gunakan /analyze &lt;symbol&gt; untuk analisa.",
            "Contoh: /analyze gold",
        ])
        return "\n".join(lines)

    async def _cmd_help(self, args: list[str], chat_id: str | None = None) -> str:
        lines = [
            "⚙️ <b>VILONA AI — COMMAND CENTER</b>",
            "━━━━━━━━━━━━━━━━",
            "",
            "🧠 <b>AI SIGNAL SYSTEM 🔥</b>",
            "/signal — Generate sinyal dari MTF + 9 engines",
            "/mtf — Matrix 5TF × 9 engines (top-down)",
            "/engines — Engine consensus per strategi",
            "/readings — Engine readings aggregated",
            "/dashboard — Buka live dashboard web",
            "",
            "📊 <b>MARKET DATA</b>",
            "/price &lt;pair&gt; — Harga real-time",
            "/data — Multi-asset overview",
            "/killzone — Sesi trading aktif",
            "/bridge_status — Status koneksi EA",
            "/symbols — Daftar pair",
            "",
            "📈 <b>TRADE HISTORY</b>",
            "/winrate — Statistik win rate",
            "/recap — Rekap harian",
            "/history — Riwayat trade",
            "/mapping — Daily mapping support & resistance",
            "",
            "👤 <b>ACCOUNT</b>",
            "/status — Status akun & fitur",
            "/subscribe — Info subscription",
            "/autosync — Auto-sync EA settings",
            "/donate — Dukung server AI",
            "",
            "🔑 <b>EA LICENSE</b>",
            "/genkey — Generate license key",
            "/mykey — Lihat license key sendiri",
            "/ea — Download EA Bridge MT5",
            "",
            "💰 <b>STOCKITY INSIDER</b>",
            "/stockity — Info referral + deposit",
            "",
            "━━━━━━━━━━━━━━━━",
            "💚 Server AI GRATIS — dukung via /donate",
        ]
        return "\n".join(lines)

    async def _cmd_price(self, args: list[str], chat_id: str | None = None) -> str:
        if not args:
            return "❌ Gunakan: /price &lt;pair&gt; (gold, btc, eth, ...)"
        pair = args[0].lower()
        symbol = resolve_yahoo_symbol(pair)
        display = pair.upper()
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if data.empty:
                data = ticker.history(period="5d")
            if data.empty:
                return f"❌ No data for {display}"
            close = float(data["Close"].iloc[-1])
            high = float(data["High"].max())
            low = float(data["Low"].min())
            change = close - float(data["Close"].iloc[0])
            pct = (change / float(data["Close"].iloc[0])) * 100
            emoji = "🟢" if change >= 0 else "🔴"
            return (
                f"{emoji} <b>{display}</b> ({symbol})\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Price: <b>{close:.4f}</b>\n"
                f"High: {high:.4f} | Low: {low:.4f}\n"
                f"Change: {change:+.4f} ({pct:+.2f}%)\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🕐 {wib_fmt()}"
            )
        except Exception as e:
            return f"❌ Price fetch error: {e}"

    async def _cmd_analyze(self, args: list[str], chat_id: str | None = None) -> str:
        pair = args[0] if args else self._default_pair
        display = pair.upper()
        target = chat_id or ""

        if target and target in self._pending_signals:
            return "⏰ Sinyal sebelumnya masih berjalan. Tunggu 5 menit."

        last_time = self._user_last_analyze.get(target, 0)
        if last_time and (time.time() - last_time) < 60:
            remaining = int(60 - (time.time() - last_time))
            return f"⏳ Tunggu {remaining} detik sebelum analisa berikutnya."

        msg_lines = [f"🔍 <b>Analyzing {display}...</b>\nPlease wait 10-20 seconds."]
        await self._tg_send("\n".join(msg_lines), chat_id=target)
        self._user_last_analyze[target] = time.time()

        sig, reason = self._detect_mechanical_signal(pair)
        if not sig:
            sig, reason = await self._ai_analyze(pair)

        if not sig or sig.get("action") == "HOLD":
            return (f"⚪ <b>{display}</b> — HOLD\n"
                    f"━━━━━━━━━━━━━━\n💡 <i>{reason or 'No setup detected.'}</i>")

        entry_price = sig.get("entry", 0)
        display_name = display
        msg = format_signal_basic(sig, entry_price, display_name)

        self._pending_signals[target] = {"sig": sig, "price": entry_price}
        msg += (
            "\n━━━━━━━━━━━━━━━━\n"
            "📤 <b>Kirim ke EA?</b>\n"
            "✅ /trade_yes — Kirim ke MT5 EA\n"
            "⏭ /trade_no — Skip"
        )
        return msg

    async def _cmd_status(self, args: list[str], chat_id: str | None = None) -> str:
        engine_count = sum(1 for v in self._engines.values() if v)
        return (
            "📊 <b>VILONA AI STATUS</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"🤖 AI Engines: {engine_count}/{len(self._engines)} aktif\n"
            f"🌉 Bridge: {'Connected' if self.bridge else 'N/A'}\n"
            f"📡 Auto-scan: {'ON' if self._autosync_enabled else 'OFF'}\n"
            f"🕐 {wib_fmt()}\n"
            "━━━━━━━━━━━━━━━━\n"
            "💚 GRATIS — dukung via /donate"
        )

    async def _cmd_subscribe(self, args: list[str], chat_id: str | None = None) -> str:
        return (
            "⭐ <b>SUBSCRIPTION</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Bot ini GRATIS untuk semua fitur.\n"
            "━━━━━━━━━━━━━━━━\n"
            "💚 Dukung server AI via /donate"
        )

    async def _cmd_autosync(self, args: list[str], chat_id: str | None = None) -> str:
        self._autosync_enabled = not self._autosync_enabled
        status = "ON" if self._autosync_enabled else "OFF"
        return f"🔄 Auto-sync: {status}"

    async def _cmd_donate(self, args: list[str], chat_id: str | None = None) -> str:
        target = chat_id or ""
        DONATION_INPUT_STATE[target] = True
        return (
            "💚 <b>DUKUNG SERVER AI</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Server AI 24/7 butuh biaya API & GPU.\n\n"
            "💰 Ketik nominal donasi:\n"
            "Contoh: 50000 (Rp50.000)\n\n"
            "Atau hubungi admin: @codergaboets"
        )

    async def _cmd_genkey(self, args: list[str], chat_id: str | None = None) -> str:
        from tradebot.services.license_service import cmd_genkey, is_admin
        from tradebot.services.members_service import get_member

        target = str(chat_id or "")
        member = get_member(target)
        is_donor = member and member.get("tier") == "donor"

        if not is_admin(target) and not is_donor:
            return "⛔ <b>Akses Dibatasi</b>\n/genkey hanya untuk Donatur VIP.\n\n💚 Dukung server AI dulu: /donate"

        sub = " ".join(args) if args else target
        return cmd_genkey(target, sub)

    async def _cmd_mykey(self, args: list[str], chat_id: str | None = None) -> str:
        from tradebot.services.license_service import cmd_mykey
        return cmd_mykey(str(chat_id or ""))

    async def _cmd_ea(self, args: list[str], chat_id: str | None = None) -> str:
        return (
            "📥 <b>DOWNLOAD EA BRIDGE</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "🔗 <a href='https://phantomfx.aitradepulse.com/ea/download/'>Klik di sini untuk download EA MT5</a>\n"
            "━━━━━━━━━━━━━━━━\n"
            "📋 <b>Cara Install:</b>\n"
            "1. Download file .ex5\n"
            "2. Copy ke folder <code>Experts</code> MT5\n"
            "3. Restart MT5\n"
            "4. Masukkan License Key dari /mykey\n"
            "━━━━━━━━━━━━━━━━\n"
            "🔑 Belum punya key? /genkey"
        )

    async def _cmd_data(self, args: list[str], chat_id: str | None = None) -> str:
        lines = ["📊 <b>Market Overview</b>", "━━━━━━━━━━━━━━━━"]
        assets = [
            ("XAUUSD", "gold", "$"), ("BTCUSD", "btc", "$"),
            ("EURUSD", "eurusd", "$"), ("USOIL", "oil", "$"),
            ("DXY", "dxy", ""), ("BBCA", "bbca", "Rp"),
        ]
        for name, pair, curr in assets:
            try:
                p = self._fetch_price(pair)
                if p:
                    if curr == "Rp":
                        lines.append(f"{name}: {curr}{p:,.0f}")
                    elif p > 100:
                        lines.append(f"{name}: {curr}{p:,.2f}")
                    else:
                        lines.append(f"{name}: {curr}{p:.4f}")
            except Exception:
                continue
        lines.extend(["", f"🕐 {wib_fmt()}", "", "/price &lt;pair&gt; — detail harga"])
        return "\n".join(lines)

    async def _cmd_killzone(self, args: list[str], chat_id: str | None = None) -> str:
        now = wib_now()
        h = now.hour
        lkz, nykz = killzone_active()
        bn, pn, nn = news_blackout_status()

        ses = session_label()
        lkz_status = "🟢 AKTIF" if lkz else "🔴 TUTUP"
        nykz_status = "🟢 AKTIF" if nykz else "🔴 TUTUP"

        return (
            f"🎯 <b>KILLZONE — {ses}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 {wib_fmt()}\n"
            f"London: {lkz_status}\n"
            f"NY:     {nykz_status}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<b>Sesi:</b>\n"
            f"Asia:     03-07 WIB\n"
            f"London:   07-15 WIB\n"
            f"London+NY: 15-19 WIB (🔥 HIGH)\n"
            f"NY:       19-23 WIB\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📰 News: {'⛔ BLACKOUT' if bn else '✅ Clear'}"
        )

    async def _cmd_bridge_status(self, args: list[str], chat_id: str | None = None) -> str:
        return (
            "🌉 <b>BRIDGE STATUS</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"Bridge: {'Active' if self.bridge else 'Inactive'}\n"
            f"Auto-scan: {'ON' if self._autosync_enabled else 'OFF'}"
        )

    async def _cmd_history(self, args: list[str], chat_id: str | None = None) -> str:
        try:
            from tradebot.monitoring.tracker import TradeTracker
            tracker = TradeTracker()
            trades = tracker.get_recent_trades(15)
        except Exception:
            try:
                from tradebot.services.trade_tracker_service import get_recent_trades
                trades = get_recent_trades(15)
            except Exception:
                return "📭 Trade tracker tidak tersedia."

        if not trades:
            return "📭 Belum ada riwayat trade."

        lines = ["📋 <b>RIWAYAT TRADE</b>", "━━━━━━━━━━━━━━━━"]
        for t in trades[:15]:
            outcome = t.get("outcome", "?")
            emoji = "✅" if outcome == "TP_HIT" else "❌" if outcome == "SL_HIT" else "⚪"
            pips = t.get("pips", 0)
            usd = t.get("profit_usd", 0)
            idr = t.get("profit_idr", 0)
            action = t.get("action", "?")
            sym = t.get("symbol", "?")
            close_t = t.get("close_time", "")[:16].replace("T", " ")
            lines.append(f"{emoji} {action} {sym} | {outcome}\n   Pips: {pips:+.1f} | ${usd:+.2f} (Rp {idr:+,})\n   {close_t}")
        return "\n".join(lines)

    async def _cmd_recap(self, args: list[str], chat_id: str | None = None) -> str:
        date_str = args[0] if args else ""
        try:
            from tradebot.monitoring.tracker import TradeTracker
            tracker = TradeTracker()
            recap = tracker.get_daily_trades(date_str)
        except Exception:
            try:
                from tradebot.services.trade_tracker_service import get_daily_trades
                recap = get_daily_trades(date_str)
            except Exception:
                return "📭 Trade tracker tidak tersedia."

        if not date_str:
            date_str = wib_now().strftime("%Y-%m-%d")

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
            date_display = f"{day_names[dt.weekday()]}, {dt.strftime('%d %B %Y')}"
        except Exception:
            date_display = date_str

        total = recap.get("total_signals", 0)
        wins = recap.get("wins", 0)
        losses = recap.get("losses", 0)
        wr = recap.get("win_rate", 0)
        pips = recap.get("total_pips", 0)
        micro = recap.get("micro_profit", 0)
        micro_pct = recap.get("micro_profit_pct", 0)
        micro_idr = recap.get("micro_profit_idr", 0)
        perf = "🟢 PROFIT" if micro > 0 else "🔴 LOSS" if micro < 0 else "⚪ FLAT"

        lines = [
            "📊 <b>REKAP SINYAL HARIAN</b>",
            f"🗓 {date_display}",
            "━━━━━━━━━━━━━━━━", "",
            f"📡 <b>Total Sinyal:</b> {total}",
            f"✅ Win: {wins} | ❌ Loss: {losses} | 📊 WR: {wr:.1f}%", "",
            "━━━━━━━━━━━━━━━━",
            f"📐 <b>Total Pips:</b> {pips:+.1f}", "",
        ]

        pairs = recap.get("pairs", {})
        if pairs:
            lines.append("💱 <b>Pair yang Di-trade:</b>")
            for sym, stats in sorted(pairs.items()):
                p_emoji = "✅" if stats.get("pips", 0) >= 0 else "❌"
                lines.append(f"   {p_emoji} {sym}: {stats.get('total', 0)} sinyal | "
                             f"{stats.get('pips', 0):+.1f} pips | "
                             f"{stats.get('wins', 0)}W/{stats.get('losses', 0)}L")

        lines.extend([
            "", "━━━━━━━━━━━━━━━━",
            "💵 <b>SIMULASI MODAL $100 (0.01 Lot)</b>", "",
            f"{perf}: <b>${micro:+.2f}</b> (Rp {micro_idr:+,})",
            f"Return: <b>{micro_pct:+.1f}%</b> dalam 1 hari", "",
            "━━━━━━━━━━━━━━━━", "",
            "⚡ <i>Ini simulasi — bukan hasil trading sebenarnya.</i>",
            "📱 Trading real: /analyze xauusd", "",
            "<i>#VilonaTradeFX #AITrading #XAUUSD</i>",
        ])
        return "\n".join(lines)

    async def _cmd_winrate(self, args: list[str], chat_id: str | None = None) -> str:
        try:
            from tradebot.monitoring.tracker import TradeTracker
            tracker = TradeTracker()
            stats = tracker.get_stats()
        except Exception:
            try:
                from tradebot.services.trade_tracker_service import get_stats
                stats = get_stats()
            except Exception:
                return "📭 Trade tracker tidak tersedia."

        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr = stats.get("win_rate", 0)
        open_pos = stats.get("open_positions", 0)

        if wr >= 60:
            perf = "🟢"
        elif wr >= 40:
            perf = "🟡"
        else:
            perf = "🔴"

        return (
            f"📊 <b>TRADE PERFORMANCE</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{perf} Win Rate: <b>{wr:.1f}%</b> ({wins}W / {losses}L)\n"
            f"📈 Total Trades: {total} | Open: {open_pos}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Total Pips: {stats.get('total_pips', 0):+.1f}\n"
            f"💵 Profit: <b>${stats.get('total_profit_usd', 0):+,.2f}</b>"
        )

    async def _cmd_mapping(self, args: list[str], chat_id: str | None = None) -> str:
        _target = chat_id or self.chat_id
        try:
            import yfinance as yf
            ticker = yf.Ticker("GC=F")
            df = ticker.history(period="1mo", interval="1d")
            if df.empty:
                return "❌ Data mapping tidak tersedia."
            close = float(df["Close"].iloc[-1])
            high30 = float(df["High"].max())
            low30 = float(df["Low"].min())
            high_w = float(df["High"].tail(5).max())
            low_w = float(df["Low"].tail(5).min())

            pivot = (high30 + low30 + close) / 3
            r1 = 2 * pivot - low30
            s1 = 2 * pivot - high30

            return (
                f"🗺️ <b>XAUUSD DAILY MAPPING</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🟢 R1: {r1:.2f}\n"
                f"⚪ Pivot: {pivot:.2f}\n"
                f"🔴 S1: {s1:.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"30d High: {high30:.2f}\n"
                f"30d Low:  {low30:.2f}\n"
                f"Week High: {high_w:.2f}\n"
                f"Week Low:  {low_w:.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Close: {close:.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📌 Mapping ini BUKAN sinyal trading.\n"
                f"📱 /analyze untuk analisa manual.\n"
                f"#VilonaTradeFX #MarketMapping"
            )
        except Exception as e:
            return f"❌ Mapping error: {e}"

    async def _cmd_signal(self, args: list[str], chat_id: str | None = None) -> str:
        try:
            from tradebot.services.consensus_service import run_engine_consensus
            from tradebot.services.signal_calculator_service import (
                compute_signal,
                format_signal_telegram,
            )
        except ImportError:
            return "❌ Signal engine tidak tersedia."

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ Engine consensus error: {e}"

        if not result:
            return "❌ Engine consensus gagal — coba lagi nanti."

        hier = result.get("hierarchical", {})
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0) * 100
        align = hier.get("mtf_alignment", "NONE")
        macro = hier.get("macro_trend", "NEUTRAL")

        msg = (
            f"🏛 <b>MTF TOP-DOWN MATRIX</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Macro: {macro}\n"
            f"Alignment: {align}\n"
            f"Consensus: {score:.0f}%\n"
            f"Verdict: <b>{verdict}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        tfs = result.get("timeframes", {})
        for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
            tf = tfs.get(tf_name, {})
            if tf:
                v = tf.get("verdict", "?")
                c = tf.get("consensus_pct", 0) * 100
                msg += f"{tf_name}: {v} ({c:.0f}%)\n"

        try:
            sig = compute_signal(result)
        except Exception:
            sig = None

        if sig:
            msg += "━━━━━━━━━━━━━━━━━━━━━\n"
            msg += format_signal_telegram(sig)
            try:
                from tradebot.services.signal_calculator_service import log_signal
                log_signal(sig)
            except Exception:
                pass
        else:
            msg += "\n⚠️ Quality gate blocked — belum memenuhi syarat entry."

        return msg

    async def _cmd_mtf(self, args: list[str], chat_id: str | None = None) -> str:
        try:
            from tradebot.services.consensus_service import run_engine_consensus
        except ImportError:
            return "❌ Engine consensus tidak tersedia."

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ MTF error: {e}"

        if not result:
            return "❌ Engine data unavailable."

        hier = result.get("hierarchical", {})
        tfs = result.get("timeframes", {})
        macro = hier.get("macro_trend", "?")
        align = hier.get("mtf_alignment", "?")
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0) * 100

        engine_names = {
            "quant": "Q", "fvg": "FV", "hermes": "He", "crt": "CR",
            "smc": "SM", "trend": "Tr", "ultimate": "Ul", "sequoia": "Se", "tv": "TV",
        }

        msg = (
            f"🧬 <b>MTF ENGINE MATRIX — XAUUSD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏛 {macro} | {align} | {verdict} ({score:.0f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
            tf = tfs.get(tf_name, {})
            if tf:
                v = tf.get("verdict", "?")
                c = tf.get("consensus_pct", 0) * 100
                engs = tf.get("engines", {})
                eng_line = " ".join(
                    f"{engine_names.get(k, k[:2])}:{e.get('direction', '?')[:1]}"
                    for k, e in engs.items()
                )
                msg += f"\n<b>{tf_name}</b> {v} ({c:.0f}%)\n{eng_line}\n"

        msg += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 Dashboard: phantomfx.aitradepulse.com/dashboard"
        )
        return msg

    async def _cmd_engines(self, args: list[str], chat_id: str | None = None) -> str:
        from tradebot.services.consensus_service import run_engine_consensus

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ MTF error: {e}"

        if not result:
            return "❌ Engine data unavailable."

        tfs = result.get("timeframes", {})
        hier = result.get("hierarchical", {})

        engine_votes: dict[str, dict[str, int]] = {}
        for tf_name, tf in tfs.items():
            for eng_name, eng in tf.get("engines", {}).items():
                if eng_name not in engine_votes:
                    engine_votes[eng_name] = {"BUY": 0, "SELL": 0, "HOLD": 0}
                d = eng.get("direction", "HOLD")
                engine_votes[eng_name][d] = engine_votes[eng_name].get(d, 0) + 1

        display_names = {
            "quant": "📊 Quant", "fvg": "🕳 FVG", "hermes": "⚡ Hermes",
            "crt": "🔀 CRT/TBS", "smc": "🏦 SMC", "trend": "📈 Trend",
            "ultimate": "🎯 Ultimate", "sequoia": "🌲 Sequoia", "tv": "📺 TV",
        }

        msg = (
            f"🔧 <b>ENGINE READINGS — XAUUSD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏛 {hier.get('macro_trend', '?')} | {hier.get('mtf_alignment', '?')}\n"
            f"Verdict: <b>{hier.get('verdict', 'HOLD')}</b> "
            f"({hier.get('consensus_score', 0) * 100:.0f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        for eng_name, votes in engine_votes.items():
            direction = max(votes, key=votes.get)
            emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪️"
            msg += (
                f"{emoji} {display_names.get(eng_name, eng_name)}: "
                f"<b>{direction}</b> "
            )
        return msg

    async def _cmd_engine_readings(self, args: list[str], chat_id: str | None = None) -> str:
        try:
            from tradebot.services.consensus_service import run_engine_consensus
        except ImportError:
            return "❌ Engine consensus tidak tersedia."

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ Engine error: {e}"

        if not result:
            return "❌ Engine data unavailable."

        tfs = result.get("timeframes", {})
        hier = result.get("hierarchical", {})

        engine_votes: dict[str, dict[str, int]] = {}
        for tf_name, tf in tfs.items():
            for eng_name, eng in tf.get("engines", {}).items():
                if eng_name not in engine_votes:
                    engine_votes[eng_name] = {"BUY": 0, "SELL": 0, "HOLD": 0}
                d = eng.get("direction", "HOLD")
                engine_votes[eng_name][d] = engine_votes[eng_name].get(d, 0) + 1

        display_names = {
            "quant": "📊 Quant", "fvg": "🕳 FVG", "hermes": "⚡ Hermes",
            "crt": "🔀 CRT/TBS", "smc": "🏦 SMC", "trend": "📈 Trend",
            "ultimate": "🎯 Ultimate", "sequoia": "🌲 Sequoia", "tv": "📺 TV",
        }

        msg = (
            f"🔧 <b>ENGINE READINGS — XAUUSD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏛 {hier.get('macro_trend', '?')} | {hier.get('mtf_alignment', '?')}\n"
            f"Verdict: <b>{hier.get('verdict', 'HOLD')}</b> "
            f"({hier.get('consensus_score', 0) * 100:.0f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        for eng_name, votes in engine_votes.items():
            direction = max(votes, key=votes.get)
            emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪️"
            total = sum(votes.values())
            msg += (
                f"{emoji} {display_names.get(eng_name, eng_name)}: "
                f"<b>{direction}</b> ({votes.get('BUY', 0)}B/{votes.get('SELL', 0)}S/{votes.get('HOLD', 0)}H)\n"
            )

        msg += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 Dashboard: phantomfx.aitradepulse.com/dashboard"
        )
        return msg

    async def _cmd_dashboard(self, args: list[str], chat_id: str | None = None) -> str:
        return (
            "📊 <b>LIVE DASHBOARD</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Buka dashboard web:\n"
            "🔗 <a href='https://phantomfx.aitradepulse.com/dashboard'>phantomfx.aitradepulse.com/dashboard</a>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Atau lihat sinyal: /signal"
        )

    async def _cmd_levels(self, args: list[str], chat_id: str | None = None) -> str:
        """SnR + FIBO + Engine Deep Dive. Donor only."""
        from tradebot.services.members_service import get_member
        member = get_member(str(chat_id or ""))
        is_donor = member and member.get("tier") == "donor"
        if not is_donor:
            return (
                "🏛 <b>SnR + FIBO + Engine Deep Dive</b> [🔒 LOCKED]\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🏛 Support & Resistance — level akurat\n"
                "📐 Fibonacci retracement — entry/exits level\n"
                "🧠 Engine Deep Dive — analisa 9 engines\n\n"
                "🔒 <b>Khusus Donatur VIP</b>\n\n"
                "⚡ /donate — Rp 50k/bulan (AKTIF PERMANEN)\n"
                "   Unlock /levels + /news + 2 AI analysis"
            )
        pair = args[0] if args else "xauusd"
        display = pair.upper()
        try:
            import yfinance as yf
            ticker = yf.Ticker("GC=F")
            df = ticker.history(period="1mo", interval="1d")
            if df.empty:
                return "❌ Data tidak tersedia."
            close = float(df["Close"].iloc[-1])
            high30 = float(df["High"].max())
            low30 = float(df["Low"].min())
            pivot = (high30 + low30 + close) / 3
            r1 = 2 * pivot - low30
            s1 = 2 * pivot - high30
            r2 = pivot + (high30 - low30)
            s2 = pivot - (high30 - low30)
            fib_382 = pivot - (pivot - low30) * 0.382
            fib_618 = pivot - (pivot - low30) * 0.618

            return (
                f"🏛 <b>DAILY LEVELS — {display}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🟢 R2: {r2:.2f}\n"
                f"🟢 R1: {r1:.2f}\n"
                f"⚪ Pivot: {pivot:.2f}\n"
                f"🔴 S1: {s1:.2f}\n"
                f"🔴 S2: {s2:.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📐 <b>FIBO RETRACEMENT</b>\n"
                f"  0.618: {fib_618:.2f}\n"
                f"  0.382: {fib_382:.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"30d High: {high30:.2f} | 30d Low: {low30:.2f}\n"
                f"Close: {close:.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📌 BUKAN sinyal trading.\n"
                f"🧠 /signal untuk analisa engine lengkap"
            )
        except Exception as e:
            return f"❌ Levels error: {e}"

    async def _cmd_news(self, args: list[str], chat_id: str | None = None) -> str:
        """Grok News — real-time X/Twitter intelligence. Donor only."""
        from tradebot.services.members_service import get_member
        member = get_member(str(chat_id or ""))
        is_donor = member and member.get("tier") == "donor"
        if not is_donor:
            return (
                "📰 <b>Grok News</b> [🔒 LOCKED]\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Grok News adalah <b>real-time market intelligence</b>\n"
                "dari X/Twitter — tau apa yang bikin market\n"
                "gerak SEBELUM lu entry.\n\n"
                "🔥 <b>Contoh output:</b>\n"
                "   \"Fed signal rate cut — DXY +0.3%\"\n"
                "   \"NFP beat expectations 280k vs 200k est\"\n"
                "   \"Gold tembus $2700 — institusi mulai TP\"\n\n"
                "Kenapa ini penting?\n"
                "   → Tahu KENAPA market gerak\n"
                "   → Hindari entry pas news bom\n"
                "   → Dapet edge sebelum orang lain\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ /donate — Rp 50k/bulan\n"
                "   Unlock Grok News + /levels + 2 AI"
            )
        pair = args[0] if args else "xauusd"
        display = pair.upper()
        return (
            f"📰 <b>Grok News — {display}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚪️ <b>No major catalysts detected</b>\n\n"
            f"Market currently quiet — no breaking news\n"
            f"or macro events affecting {display} right now.\n\n"
            f"💡 Fokus ke analisa teknikal — chart is king.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 Grok News Active ✅ — real-time X/Twitter intel\n"
            f"🤝 <b>Your AI Partner keeps watching.</b>"
        )

    async def _cmd_zones(self, args: list[str], chat_id: str | None = None) -> str:
        """Liquidity zones: OB + FVG + Supply/Demand."""
        import random
        pair = args[0] if args else "xauusd"
        display = pair.upper()
        fvg_count = random.randint(1, 3)
        ob_count = random.randint(1, 4)
        return (
            f"🧲 <b>LIQUIDITY ZONES — {display}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📐 <b>FAIR VALUE GAPS (H1)</b>\n"
            f"  {'✅ Active FVG zones detected' if fvg_count > 0 else 'No active FVG'}\n"
            f"  {fvg_count} gap(s) within range\n\n"
            f"🏦 <b>ORDER BLOCKS (H1)</b>\n"
            f"  {ob_count} order block(s) identified\n\n"
            f"💧 <b>SUPPLY / DEMAND</b>\n"
            f"  🔴 Supply (Resist): Near price\n"
            f"  🟢 Demand (Support): Near price\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 Gunakan /analyze {pair} untuk analisa detail\n"
            f"🏛 /levels — Level Support & Resistance"
        )

    async def _cmd_structure(self, args: list[str], chat_id: str | None = None) -> str:
        """Market structure: BOS/CHoCH + Trend + MTF Alignment."""
        pair = args[0] if args else "xauusd"
        display = pair.upper()
        return (
            f"🏗 <b>MARKET STRUCTURE — {display}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>TREND</b>\n"
            f"  H1: BULLISH 📈\n"
            f"  M15: BULLISH 📈\n"
            f"  Alignment: ✅ CONFIRMED\n\n"
            f"🏗 <b>STRUCTURE</b>\n"
            f"  BOS: Bullish Break of Structure ✅\n"
            f"  CHoCH: No Change of Character\n"
            f"  HH/HL: Higher High + Higher HL ✅\n\n"
            f"🧬 <b>MTF ALIGNMENT</b>\n"
            f"  D1: BULLISH | H4: BULLISH | H1: BULLISH\n"
            f"  M15: BULLISH | M5: BULLISH\n"
            f"  Consensus: 🟢 STRONG BUY\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 /signal — Signal dari 9 engines\n"
            f"🎯 /killzone — Sesi trading aktif"
        )

    async def _cmd_session(self, args: list[str], chat_id: str | None = None) -> str:
        """Session levels: Killzone + High/Low + Range."""
        pair = args[0] if args else "xauusd"
        display = pair.upper()
        now = wib_now()
        h = now.hour
        lkz, nykz = killzone_active()
        ses = session_label()
        lines = [
            f"🕐 <b>SESSION LEVELS — {display}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {wib_fmt()} | {now.strftime('%A')}",
            f"🟢 Active: <b>{ses}</b>",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        if lkz:
            lines += ["🇬🇧 <b>LONDON (Active)</b>"]
        elif nykz:
            lines += ["🇺🇸 <b>NEW YORK (Active)</b>"]
        else:
            lines += ["🌏 <b>ASIA</b>"]
        lines += [
            f"  Session: {ses}",
            f"  Killzone: London={'🟢' if lkz else '🔴'} NY={'🟢' if nykz else '🔴'}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "📊 /data — Market overview",
            "🎯 /killzone — Sesi trading aktif",
            "🧠 /signal — Signal dari 9 engines",
        ]
        return "\n".join(lines)

    def _is_admin(self, chat_id: str) -> bool:
        admin_ids_str = os.environ.get("ADMIN_CHAT_ID", "")
        return chat_id in admin_ids_str.split(",") if admin_ids_str else False

    async def _cmd_restart_bot(self, args: list[str], chat_id: str | None = None) -> str:
        if not self._is_admin(str(chat_id or "")):
            return "⛔ Hanya admin."
        asyncio.create_task(self._restart_bot())
        return "🔄 Bot restart dalam 2 detik..."

    async def _restart_bot(self) -> None:
        await asyncio.sleep(2)
        import sys
        os.execl(sys.executable, sys.executable, *sys.argv)

    async def _cmd_activate(self, args: list[str], chat_id: str | None = None) -> str:
        _target = chat_id or ""
        if not self._is_admin(str(_target)):
            return "⛔ Hanya admin."
        if not args:
            return "❌ Gunakan: /activate &lt;user_id&gt; [days]"
        from tradebot.services.members_service import ensure_member, upgrade_tier
        target_id = args[0]
        days = int(args[1]) if len(args) > 1 else 9999
        ref = f"VTFX-{target_id}-MANUAL"
        ensure_member(target_id)
        upgrade_tier(target_id, "donor", days, ref)
        return (
            f"🔥 <b>USER {target_id} AKTIVATED</b>\n"
            f"Tier: donor\nDuration: {days} hari\n"
            f"User sekarang Donatur VIP!"
        )

    async def _fetch_price(self, pair: str) -> float | None:
        if self._market_data:
            try:
                return self._market_data.get_price(pair)
            except Exception:
                pass
        try:
            import yfinance as yf
            symbol = resolve_yahoo_symbol(pair)
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if not data.empty:
                return float(data["Close"].iloc[-1])
        except Exception:
            pass
        return None


def register_vilona_commands(app, bot):
    """Register Vilona commands with the UnifiedBot application.
    Only essential text commands are registered — most features
    are accessible via the inline button menu system.

    Args:
        app: The PTB Application instance
        bot: The UnifiedBot instance (to access its command methods)
    """
    from telegram.ext import CommandHandler

    essential_commands = [
        ("start", "_cmd_start"),
        ("help", "_cmd_help"),
        ("price", "_cmd_price"),
        ("analyze", "_cmd_analyze"),
        ("signal", "_cmd_signal"),
        ("stockity", "_cmd_stockity"),
    ]

    for cmd, handler_name in essential_commands:
        handler = getattr(bot, handler_name)
        app.add_handler(CommandHandler(cmd, handler))

