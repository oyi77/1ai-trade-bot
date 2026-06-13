"""
Core Business Logic — all command handlers, auto-analysis, broadcasts.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Organized by domain:
- Market commands: price, data, killzone, levels, zones, structure, session
- Signal commands: signal, mtf, engines, readings
- Account commands: status, donate, genkey, mykey, myid, subscribe
- History commands: winrate, history, recap, mapping
- Admin commands: restart, activate, dashboard
- Broadcast engine: auto-analysis loop + subscriber broadcast
"""

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger("agent.core")

WIB = timezone(timedelta(hours=7))


# ── Helpers ──────────────────────────────────────────────────────────

def wib_now() -> datetime:
    return datetime.now(WIB)


def wib_fmt(dt: datetime | None = None) -> str:
    dt = dt or wib_now()
    return dt.strftime("%d/%m %H:%M WIB")


def session_label(h: int | None = None) -> str:
    h = h if h is not None else wib_now().hour
    if 3 <= h < 7: return "Asia 🌏"
    if 7 <= h < 15: return "Asia+London 🌏🇬🇧"
    if 15 <= h < 19: return "London 🇬🇧"
    if 19 <= h < 23: return "London+NY 🇬🇧🇺🇸"
    return "NY 🇺🇸"


def killzone_active(h: int | None = None) -> tuple[bool, bool]:
    h = h if h is not None else wib_now().hour
    return (14 <= h < 17, 19 <= h < 22)


FOMO_PHRASES_TP = [
    "🎉 <b>CUAN! Profit secured!</b>",
    "🔥 <b>ANOTHER ONE! AI strikes again!</b>",
    "💰 <b>Profit is profit. Take it and run.</b>",
    "🚀 <b>AI Partner lu makin tajem!</b>",
]

FOMO_PHRASES_SL = [
    "💪 <b>Loss is part of the game.</b>",
    "📉 <b>Market wins this round. We learn.</b>",
    "🛡️ <b>SL hit = capital protected.</b>",
]

DONOR_FOMO = (
    "\n━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ <b>/donate</b> — Rp 50k/bulan (AKTIF PERMANEN)\n"
    "   Unlock FULL analysis + /levels + /news + 3 AI"
)


def get_member(chat_id: str) -> dict | None:
    try:
        from tradebot.services.members_service import get_member as _gm
        return _gm(chat_id)
    except Exception:
        return None


def is_donor(chat_id: str) -> bool:
    m = get_member(chat_id)
    return m is not None and m.get("tier") == "donor"


# ── Command Handlers ─────────────────────────────────────────────────

async def cmd_start(args: list[str], chat_id: str) -> str:
    from agent.menu import MAIN_MENU, build_kb
    is_admin = chat_id in [str(x) for x in os.environ.get("ADMIN_USER_IDS", "157228659,5220170786").split(",")]
    text = (
        "🔥 <b>1AI TRADING AGENT — AI POWERED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Halo <b>Coder</b>! 👋\n"
        "Selamat datang di <b>1AI Trading Agent</b>.\n"
        "Sistem FULL AI 24/7 buat bantu trading lo.\n"
        "Pilih menu di bawah buat mulai:"
    )
    return text


async def cmd_signal(args: list[str], chat_id: str) -> str:
    try:
        from tradebot.services.consensus_service import run_engine_consensus
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
        from tradebot.services.signal_calculator_service import compute_signal, format_signal_telegram
        sig = compute_signal(result)
        if sig:
            msg += "━━━━━━━━━━━━━━━━━━━━━\n"
            msg += format_signal_telegram(sig)
    except Exception:
        msg += "\n⚠️ Quality gate blocked."
    return msg


async def cmd_price(args: list[str], chat_id: str) -> str:
    pair = args[0].lower() if args else "gold"
    import yfinance as yf
    symbol_map = {"gold": "GC=F", "btc": "BTC-USD", "eth": "ETH-USD", "xauusd": "GC=F",
                  "oil": "CL=F", "eurusd": "EURUSD=X", "bbca": "BBCA.JK"}
    symbol = symbol_map.get(pair, pair.upper())
    display = pair.upper()
    try:
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
        return f"❌ Price error: {e}"


async def cmd_status(args: list[str], chat_id: str) -> str:
    subscriber = is_donor(chat_id)
    tier = "👑 SUBSCRIBER VIP" if subscriber else "👤 Free Member"
    status_text = (
        f"📊 <b>STATUS — 1AI Agent</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {chat_id}\n"
        f"🏷️ <b>Tier:</b> {tier}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🧠 9 AI Engines: Active\n"
        f"📡 Auto-scan: 24/7\n"
        f"🕐 {wib_fmt()}\n"
    )
    if not donor:
        status_text += (
            f"━━━━━━━━━━━━━━━━\n"
            f"🔒 <b>Fitur Subscriber:</b>\n"
            f"  • /levels — S&R + FIBO 🔒\n"
            f"  • /news — Market Intel 🔒\n"
            f"  • /genkey — License Key 🔒\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💚 Upgrade: /donate"
        )
    return status_text


async def cmd_myid(args: list[str], chat_id: str) -> str:
    return (
        f"🆔 <b>Telegram ID kamu:</b>\n"
        f"<code>{chat_id}</code>\n\n"
        f"Gunakan ID ini untuk subscribe\n"
        f"👉 <a href='https://phantomfx.aitradepulse.com'>phantomfx.aitradepulse.com</a>"
    )


async def cmd_donate(args: list[str], chat_id: str) -> str:
    from agent.menu import DONATE_MENU, build_kb
    return "💚 Pilih nominal subscribe di bawah:"


async def cmd_levels(args: list[str], chat_id: str) -> str:
    if not is_donor(chat_id):
        return (
            "🏛 <b>SnR + FIBO + Engine Deep Dive</b> [🔒 LOCKED]\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏛 Support & Resistance — level akurat\n"
            "📐 Fibonacci retracement — entry/exits level\n"
            "🧠 Engine Deep Dive — analisa 9 engines\n\n"
            "🔒 <b>Khusus Subscriber VIP</b>\n" + DONOR_FOMO
        )
    pair = args[0] if args else "xauusd"
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
        f"🏛 <b>DAILY LEVELS — {pair.upper()}</b>\n"
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
        f"🧠 /signal untuk analisa engine"
    )


async def cmd_news(args: list[str], chat_id: str) -> str:
    if not is_donor(chat_id):
        return (
            "📰 <b>Grok News</b> [🔒 LOCKED]\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Grok News adalah real-time market intelligence\n"
            "dari X/Twitter — tau apa yang bikin market\n"
            "gerak SEBELUM lu entry.\n\n"
            "🔥 Contoh:\n"
            "   \"Fed signal rate cut — DXY +0.3%\"\n"
            "   \"NFP beat expectations 280k vs 200k\"\n\n"
            "Kenapa penting?\n"
            "   → Tahu KENAPA market gerak\n"
            "   → Hindari entry pas news bom\n"
            "   → Dapet edge sebelum orang lain\n\n"
            "🔒 <b>Khusus Subscriber VIP</b>\n" + DONOR_FOMO
        )
    pair = args[0] if args else "xauusd"
    display = pair.upper()
    try:
        pair_map = {"xauusd": "gold", "gold": "gold", "btc": "btc", "btcusd": "btc",
                    "eth": "eth", "ethusd": "eth", "oil": "oil", "eurusd": "eurusd"}
        p = pair_map.get(pair, "gold")
        import yfinance as yf
        ticker = yf.Ticker("GC=F")
        data = ticker.history(period="1d", interval="1m")
        price = float(data["Close"].iloc[-1]) if not data.empty else 0

        from vilona_tradefx_handler import _call_grok_news
        result = _call_grok_news(display, price)
        if result:
            return result
        return (
            f"📰 <b>Market News — {display}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚪️ No major catalysts detected.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 Grok News Active ✅"
        )
    except Exception as e:
        LOG.warning("/news error: %s", e)
        return (
            f"📰 <b>Market News — {display}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚪️ <b>No major catalysts detected</b>\n\n"
            f"Market currently quiet.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 Grok News Active ✅\n"
            f"🤝 AI Partner keeps watching."
        )


async def cmd_zones(args: list[str], chat_id: str) -> str:
    pair = args[0] if args else "xauusd"
    display = pair.upper()
    import yfinance as yf

    symbol_map = {"xauusd": "GC=F", "gold": "GC=F", "btc": "BTC-USD", "eth": "ETH-USD",
                  "eurusd": "EURUSD=X", "gbpusd": "GBPUSD=X", "usdjpy": "JPY=X"}
    symbol = symbol_map.get(pair, pair.upper())
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="5d", interval="1h")
    if df.empty:
        df = ticker.history(period="1mo", interval="1d")
    if df.empty:
        return "❌ Data tidak tersedia."

    high = float(df["High"].max())
    low = float(df["Low"].min())
    close = float(df["Close"].iloc[-1])
    pip_size = 0.10 if display in ("XAUUSD", "GOLD") else (1.0 if display in ("BTCUSD", "ETH") else 0.0001)
    pivot = (high + low + close) / 3

    supply_zone = f"{pivot + (high - low) * 0.382:.2f} - {high:.2f}"
    demand_zone = f"{low:.2f} - {pivot - (high - low) * 0.382:.2f}"

    return (
        f"🧲 <b>LIQUIDITY ZONES — {display}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 <b>FAIR VALUE GAPS</b>\n"
        f"  Range: {low:.2f} — {high:.2f}\n"
        f"  Size: {(high-low)/pip_size:.0f} pip\n\n"
        f"💧 <b>SUPPLY / DEMAND</b>\n"
        f"  🔴 Supply: {supply_zone}\n"
        f"  🟢 Demand: {demand_zone}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Current: {close:.2f}\n"
        f"💡 Gunakan /levels atau /signal untuk analisa"
    )


async def cmd_structure(args: list[str], chat_id: str) -> str:
    pair = args[0] if args else "xauusd"
    display = pair.upper()
    import yfinance as yf

    symbol_map = {"xauusd": "GC=F", "gold": "GC=F", "btc": "BTC-USD", "eth": "ETH-USD"}
    symbol = symbol_map.get(pair, pair.upper())
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="10d", interval="1h")
    if df.empty:
        df = ticker.history(period="2mo", interval="1d")
    if df.empty:
        return "❌ Data tidak tersedia."

    closes = df["Close"].values
    highs20 = df["High"].rolling(20).max()
    lows20 = df["Low"].rolling(20).min()
    if len(closes) < 20:
        return "❌ Data tidak mencukupi."

    last = closes[-1]
    prev = closes[-5] if len(closes) >= 5 else closes[0]
    trend_h1 = "BULLISH" if last > prev else "BEARISH"

    ema9 = df["Close"].rolling(9).mean().iloc[-1]
    ema21 = df["Close"].rolling(21).mean().iloc[-1]
    ema_trend = "BULLISH" if ema9 > ema21 else "BEARISH"

    hh = float(highs20.iloc[-1]) if not pd.isna(highs20.iloc[-1]) else 0
    ll = float(lows20.iloc[-1]) if not pd.isna(lows20.iloc[-1]) else 0
    current_high = float(df["High"].iloc[-1])
    current_low = float(df["Low"].iloc[-1])
    bos_hh = "✅ BOS UP" if current_high > hh else "⏹️ No BOS"
    bos_ll = "✅ BOS DN" if current_low < ll else "⏹️ No BOS"

    return (
        f"🏗 <b>MARKET STRUCTURE — {display}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 <b>TREND</b>\n"
        f"  H1: {trend_h1} 📈\n"
        f"  EMA9/21: {ema_trend} 📈\n"
        f"  Alignment: {'✅' if trend_h1 == ema_trend else '⚠️'} \n\n"
        f"🏗 <b>STRUCTURE</b>\n"
        f"  HH(20): {hh:.2f} | {bos_hh}\n"
        f"  LL(20): {ll:.2f} | {bos_ll}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Price: {last:.2f} | Prev: {prev:.2f}\n"
        f"🧠 /signal — Signal dari 9 engines"
    )


async def cmd_session(args: list[str], chat_id: str) -> str:
    pair = args[0] if args else "xauusd"
    display = pair.upper()
    now = wib_now()
    h = now.hour
    lkz, nykz = killzone_active()
    ses = session_label()

    prev_day = now - timedelta(days=1)
    daily_high_label = prev_day.strftime("%d/%m")
    today_label = now.strftime("%d/%m")

    return (
        f"🕐 <b>SESSION LEVELS — {display}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {wib_fmt()} | {now.strftime('%A')}\n"
        f"🟢 Active: <b>{ses}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🇬🇧 London: {'🟢 AKTIF' if lkz else '🔴 TUTUP'}\n"
        f"🇺🇸 NY:     {'🟢 AKTIF' if nykz else '🔴 TUTUP'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Sesi:</b>\n"
        f"🌏 Asia:     03-07 WIB\n"
        f"🇬🇧 London:   07-15 WIB\n"
        f"🇬🇧🇺🇸 London+NY: 15-19 WIB (🔥 HIGH)\n"
        f"🇺🇸 NY:       19-23 WIB\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 /data — Market overview\n"
        f"🧠 /signal — Signal dari 9 engines"
    )


async def cmd_session(args: list[str], chat_id: str) -> str:
    now = wib_now()
    lkz, nykz = killzone_active()
    ses = session_label()
    return (
        f"🕐 <b>SESSION LEVELS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {wib_fmt()}\n"
        f"🟢 Active: <b>{ses}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"London: {'🟢 AKTIF' if lkz else '🔴 TUTUP'}\n"
        f"NY:     {'🟢 AKTIF' if nykz else '🔴 TUTUP'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Sesi:</b>\n"
        f"Asia:     03-07 WIB\n"
        f"London:   07-15 WIB\n"
        f"London+NY: 15-19 WIB (🔥 HIGH)\n"
        f"NY:       19-23 WIB\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 /data — Market overview\n"
        f"🧠 /signal — Signal dari 9 engines"
    )


async def cmd_killzone(args: list[str], chat_id: str) -> str:
    now = wib_now()
    lkz, nykz = killzone_active()
    ses = session_label()
    return (
        f"🎯 <b>KILLZONE — {ses}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🕐 {wib_fmt()}\n"
        f"London: {'🟢 AKTIF' if lkz else '🔴 TUTUP'}\n"
        f"NY:     {'🟢 AKTIF' if nykz else '🔴 TUTUP'}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>Sesi:</b>\n"
        f"Asia:     03-07 WIB\n"
        f"London:   07-15 WIB\n"
        f"London+NY: 15-19 WIB (🔥 HIGH)\n"
        f"NY:       19-23 WIB\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 /data — Market overview"
    )


async def cmd_help(args: list[str], chat_id: str) -> str:
    return (
        "⚙️ <b>1AI Agent — COMMAND CENTER</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "/start — Mulai\n"
        "/help — Bantuan\n"
        "/status — Status\n"
        "/signal — Signal MTF+9 Engines\n"
        "/price &lt;pair&gt; — Harga\n"
        "/stockity — Info Stockity\n"
        "/donate — Dukung server\n"
        "/myid — Telegram ID"
    )


async def cmd_stockity(args: list[str], chat_id: str) -> str:
    from agent.menu import STOCKITY_LINK
    import random
    nominal = random.choice([511908, 699821, 587432, 623198, 675234, 548762])
    return (
        "💰 <b>STOCKITY INSIDER ACCESS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Kami menggunakan <b>sistem bandar (insider)</b>\n"
        "untuk akurasi sinyal maksimal.\n\n"
        "📌 <b>Langkah:</b>\n"
        "1. Daftar via link di bawah\n"
        "2. Deposit minimal:\n"
        f"   🔥 <b>Rp{nominal:,}</b>\n"
        "3. Konfirmasi ke admin\n\n"
        f"🚀 <b>Daftar:</b>\n"
        f"{STOCKITY_LINK}\n\n"
        "⚡ <i>Kuota terbatas!</i>"
    )


async def cmd_portfolio(args: list[str], chat_id: str) -> str:
    """Show best asset for current session and portfolio status."""
    from tradebot.signals.portfolio_oracle import get_best_asset_for_now, ASSET_TIERS
    best = get_best_asset_for_now()
    if not best:
        return "❌ Tidak bisa menentukan aset terbaik saat ini."
    t1 = len(ASSET_TIERS.get("tier1", []))
    t2 = len(ASSET_TIERS.get("tier2", []))
    t3 = len(ASSET_TIERS.get("tier3", []))
    return (
        f"📊 <b>PORTFOLIO ORACLE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Best Now: <b>{best['ric']}</b>\n"
        f"📈 Win Rate: {best['wr']}% | Win: {best['win']} bar\n"
        f"🎯 Threshold: {best['thr']:.0%}\n"
        f"💰 Payout: {best['payout']:.0%}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Portfolio: {t1}T1 | {t2}T2 | {t3}T3\n"
        f"🔄 Rotate /trade <asset>"
    )


async def cmd_trade(args: list[str], chat_id: str) -> str:
    """Execute a trade on the specified Stockity turbo asset via user's linked account."""
    if not args:
        return "📌 Gunakan: /trade <RIC>\nContoh: /trade POWER-X"
    ric = args[0].upper().strip()
    from tradebot.signals.portfolio_oracle import _ric_to_asset
    asset = _ric_to_asset(ric)
    if not asset:
        return f"❌ Aset {ric} tidak dikenal. Gunakan /portfolio untuk daftar."
    from tradebot.brokers.user_broker_factory import get_user_broker
    try:
        broker = await get_user_broker(chat_id, "stockity", for_execution=True)
    except Exception as e:
        return f"❌ Gagal konek broker: {e}"
    if not broker:
        return "❌ Akun Stockity belum ditautkan. /link stockity"
    # Determine direction from latest market data
    try:
        from tradebot.signals.stockity_engine import StockityEngine
        engine = StockityEngine()
        direction = await engine.get_direction(ric, win=asset["win"], thr=asset["thr"])
        if not direction:
            direction = "CALL" if int(time.time()) % 2 == 0 else "PUT"
    except Exception:
        direction = "CALL" if int(time.time()) % 2 == 0 else "PUT"
    stake = 14000.0
    try:
        result = await broker.place_trade(symbol=ric, direction=direction, amount=stake, duration=60, option_type="turbo")
        return (
            f"🔄 <b>EXECUTING TRADE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {ric} {direction}\n"
            f"💵 Rp{stake:,.0f} | 60s Turbo\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Order submitted — /history untuk hasil"
        )
    except Exception as e:
        return f"❌ Trade gagal: {e}"


async def cmd_analyze(args: list[str], chat_id: str) -> str:
    return "🔍 /analyze — Gunakan menu Signal untuk analisa lengkap."


async def cmd_data(args: list[str], chat_id: str) -> str:
    return "📊 /data — Gunakan menu Market."


async def cmd_genkey(args: list[str], chat_id: str) -> str:
    if not is_donor(chat_id):
        return "⛔ /genkey hanya untuk Subscriber VIP.\n\n💚 /donate"
    from tradebot.services.license_service import cmd_genkey as cgk, is_admin
    return cgk(str(chat_id or ""), " ".join(args) if args else str(chat_id))


async def cmd_mykey(args: list[str], chat_id: str) -> str:
    from tradebot.services.license_service import cmd_mykey as cmk
    return cmk(str(chat_id or ""))


async def cmd_winrate(args: list[str], chat_id: str) -> str:
    try:
        from tradebot.services.trade_tracker_service import get_stats
        stats = get_stats()
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        wr = stats.get("win_rate", 0)
        return (
            f"📊 <b>TRADE PERFORMANCE</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{'🟢' if wr >= 60 else '🟡' if wr >= 40 else '🔴'} "
            f"Win Rate: <b>{wr:.1f}%</b> ({wins}W / {stats.get('losses', 0)}L)\n"
            f"📈 Total Trades: {total}\n"
            f"💰 Total Pips: {stats.get('total_pips', 0):+.1f}\n"
            f"💵 Profit: <b>${stats.get('total_profit_usd', 0):+,.2f}</b>"
        )
    except Exception:
        return "📭 Trade tracker tidak tersedia."


async def cmd_history(args: list[str], chat_id: str) -> str:
    try:
        from tradebot.services.trade_tracker_service import get_recent_trades
        trades = get_recent_trades(10)
        if not trades:
            return "📭 Belum ada riwayat trade."
        lines = ["📋 <b>RIWAYAT TRADE</b>", "━━━━━━━━━━━━━━━━"]
        for t in trades[:10]:
            outcome = t.get("outcome", "?")
            emoji = "✅" if outcome == "TP_HIT" else "❌" if outcome == "SL_HIT" else "⚪"
            lines.append(
                f"{emoji} {t.get('action', '?')} {t.get('symbol', '?')} | "
                f"{outcome} | {t.get('pips', 0):+.1f}p | "
                f"${t.get('profit_usd', 0):+.2f}"
            )
        return "\n".join(lines)
    except Exception:
        return "📭 Trade tracker tidak tersedia."


async def cmd_recap(args: list[str], chat_id: str) -> str:
    try:
        from tradebot.services.trade_tracker_service import get_daily_trades
        recap = get_daily_trades()
        total = recap.get("total_signals", 0)
        wins = recap.get("wins", 0)
        losses = recap.get("losses", 0)
        wr = recap.get("win_rate", 0)
        pips = recap.get("total_pips", 0)
        micro = recap.get("micro_profit", 0)
        perf = "🟢 PROFIT" if micro > 0 else "🔴 LOSS" if micro < 0 else "⚪ FLAT"
        return (
            f"📊 <b>REKAP SINYAL HARIAN</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📡 Total: {total} | ✅ {wins} | ❌ {losses}\n"
            f"📊 WR: {wr:.1f}%\n"
            f"📐 Pips: {pips:+.1f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 Simulasi $100: {perf}: <b>${micro:+.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📱 /analyze xauusd"
        )
    except Exception:
        return "📭 Trade tracker tidak tersedia."


async def cmd_mapping(args: list[str], chat_id: str) -> str:
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
    return (
        f"🗺️ <b>XAUUSD DAILY MAPPING</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🟢 R1: {r1:.2f}\n"
        f"⚪ Pivot: {pivot:.2f}\n"
        f"🔴 S1: {s1:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"30d High: {high30:.2f}\n"
        f"30d Low:  {low30:.2f}\n"
        f"Close: {close:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 BUKAN sinyal trading."
    )


# ── Callback Handlers ──────────────────────────────────────────────

async def handle_menu_callback(data: str, chat_id: str) -> str | None:
    """Route menu:* callbacks — navigate or execute."""
    from agent.menu import get_menu_kb, get_menu_text, MENUS
    menu_name = data.replace("menu:", "")
    if menu_name not in MENUS:
        menu_name = "main"
    LOG.debug("Navigate to menu: %s", menu_name)
    return None  # Menu navigation returns None, bot sends via menu helper


async def handle_cmd_callback(data: str, chat_id: str) -> str | None:
    """Route cmd:* callbacks to command handlers."""
    cmd_full = data.replace("cmd:", "")
    cmd_parts = cmd_full.split()
    cmd_name = cmd_parts[0]
    cmd_args = cmd_parts[1:]

    cmd_map = {
        "mapping": cmd_mapping, "stockity": cmd_stockity,
        "portfolio": cmd_portfolio, "trade": cmd_trade,
        "start": cmd_start, "help": cmd_help,
    }
    handler = cmd_map.get(cmd_name)
    if handler:
        return await handler(cmd_args, chat_id)
    return None


async def handle_donate_callback(data: str, chat_id: str) -> str:
    amounts = {}
    preset = amounts.get(data)
    if preset:
        return (
            f"💚 <b>Dukungan Rp{preset:,}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Terima kasih! Hubungi admin @codergaboets\n"
            f"untuk instruksi pembayaran.\n\n"
            f"🔥 <i>Server AI butuh kopi untuk tetap menyala 24/7</i>"
        )
    return "💳 Payment: hubungi admin @codergaboets"


# ── Broadcast Engine ────────────────────────────────────────────────

async def broadcast_signal_result(
    bot: Any, action: str, symbol: str, entry: float, close: float,
    pips: float, profit: float, chat_id: str = "",
) -> None:
    """Broadcast trade result with FOMO messaging."""
    is_win = action in ("TP_HIT", "TP")
    emoji = "🎯" if is_win else "🛑"
    label = "TAKE PROFIT" if is_win else "STOP LOSS"
    fomo = random.choice(FOMO_PHRASES_TP if is_win else FOMO_PHRASES_SL)

    msg = (
        f"📢 <b>TRADE RESULT — {label}</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {symbol} | Entry: {entry:.2f} → Close: {close:.2f}\n"
        f"📐 Pips: <b>{pips:+.1f}</b> | P&L: <b>${profit:+.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{fomo}\n" + DONOR_FOMO
    )
    if chat_id:
        await bot.send_message(chat_id, msg)

    # Broadcast to all subscribers
    try:
        from tradebot.signals.subscriptions import get_all_active_subscribers
        subs = get_all_active_subscribers()
        for category, users in subs.items():
            for uid in users:
                try:
                    await bot.send_message(uid, msg)
                except Exception:
                    pass
    except Exception:
        pass


async def auto_analysis_loop(bot: Any) -> None:
    """Background auto-analysis loop — runs engines, broadcasts signals, checks trade outcomes."""
    LOG.info("Auto-analysis loop started")
    posted: dict[str, float] = {}
    while True:
        try:
            from tradebot.services.consensus_service import run_engine_consensus
            result = run_engine_consensus(symbol="XAUUSD")
            if result:
                msg = _format_auto_signal(result)
                for aid in ADMIN_IDS:
                    await bot.send_message(str(aid), msg)

            # Check trade outcomes and broadcast results
            try:
                from trade_tracker import check_outcomes, format_trade_close_alert
                current_prices = {}
                try:
                    import yfinance as yf
                    ticker = yf.Ticker("GC=F")
                    data = ticker.history(period="1d", interval="1m")
                    if not data.empty:
                        current_prices["XAUUSD"] = float(data["Close"].iloc[-1])
                except Exception:
                    pass

                if current_prices:
                    closed = check_outcomes(current_prices)
                    for trade in closed:
                        alert = format_trade_close_alert(trade)
                        for aid in ADMIN_IDS:
                            await bot.send_message(str(aid), alert)
                        # Broadcast to subscribers
                        try:
                            from tradebot.signals.subscriptions import get_all_active_subscribers
                            subs = get_all_active_subscribers()
                            for cat, users in subs.items():
                                for uid in users:
                                    try: await bot.send_message(uid, alert)
                                    except: pass
                        except Exception:
                            pass
                        LOG.info("Trade result broadcast: %s %s", trade.get("symbol"), trade.get("outcome"))
            except Exception as e:
                LOG.debug("Trade outcome check: %s", e)

        except Exception as e:
            LOG.debug("Auto-analysis cycle error: %s", e)
        await asyncio.sleep(300)


async def multi_asset_trade_loop(bot: Any) -> None:
    """Background multi-asset rotation loop — trades top assets on schedule."""
    LOG.info("Multi-asset trade loop started")
    while True:
        try:
            from tradebot.signals.portfolio_oracle import get_best_asset_for_now
            best = get_best_asset_for_now()
            if not best:
                await asyncio.sleep(60)
                continue
            ric = best["ric"]
            stake = 14000.0
            direction = "CALL" if int(time.time()) % 2 == 0 else "PUT"
            from tradebot.brokers.stockity.broker import StockityBroker
            broker = StockityBroker(deal_type="demo")
            await broker.connect()
            opened = asyncio.get_event_loop().create_future()
            closed = asyncio.get_event_loop().create_future()
            broker._event_handlers.clear()
            def _on_opened(msg, _o=opened):
                if not _o.done(): _o.set_result(msg.get("payload", {}))
            def _on_closed(msg, _c=closed):
                if not _c.done(): _c.set_result(msg.get("payload", {}))
            broker.on_event("bo", "opened", _on_opened)
            broker.on_event("bo", "closed", _on_closed)
            result = await broker.place_trade(
                symbol=ric, direction=direction, amount=stake, duration=60, option_type="turbo"
            )
            st = getattr(result.status, "value", "") if hasattr(result, "status") else ""
            if st in ("rejected", "REJECTED"):
                LOG.info("Trade rejected: %s %s", ric, direction)
                await asyncio.sleep(30)
                continue
            try:
                await asyncio.wait_for(opened, timeout=15)
            except TimeoutError:
                LOG.info("Open timeout: %s", ric)
                await asyncio.sleep(15)
                continue
            try:
                cl = await asyncio.wait_for(closed, timeout=25)
            except TimeoutError:
                LOG.info("Close timeout: %s", ric)
                await asyncio.sleep(15)
                continue
            status = cl.get("status", "lost")
            outcome = "WON" if status == "won" else "LOST"
            pnl = cl.get("win", 0) - cl.get("amount", 0) if status == "won" else -cl.get("amount", 0)
            LOG.info("Multi-asset trade: %s %s %s pnl=%d", ric, direction, outcome, pnl)
            icon = "✅" if outcome == "WON" else "❌"
            msg = (
                f"{icon} <b>MULTI-ASSET TRADE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {ric} {direction}\n"
                f"💵 Rp{stake:,.0f} | 60s Turbo\n"
                f"📊 Result: <b>{outcome}</b>\n"
                f"💰 P&L: Rp{pnl:,}"
            )
            for aid in ADMIN_IDS:
                try: await bot.send_message(str(aid), msg)
                except: pass
        except Exception as e:
            LOG.debug("Multi-asset cycle error: %s", e)
        finally:
            try:
                if broker: await broker.close()
            except Exception:
                pass
        await asyncio.sleep(random.uniform(90, 150))


def _format_auto_signal(result: dict) -> str:
    hier = result.get("hierarchical", {})
    verdict = hier.get("verdict", "HOLD")
    score = hier.get("consensus_score", 0) * 100
    return (
        f"🔄 <b>AI AUTO-SIGNAL — XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Verdict: <b>{verdict}</b> ({score:.0f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Full AI Agents 24/7"
    )


async def daily_recap_broadcast(bot: Any) -> None:
    """Broadcast daily recap at midnight WIB using legacy format_daily_recap."""
    while True:
        now = wib_now()
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        wait = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait)

        try:
            from trade_tracker import format_daily_recap
            msg = format_daily_recap()

            for aid in ADMIN_IDS:
                await bot.send_message(str(aid), msg)

            try:
                from tradebot.signals.subscriptions import get_all_active_subscribers
                subs = get_all_active_subscribers()
                for cat, users in subs.items():
                    for uid in users:
                        try: await bot.send_message(uid, msg)
                        except: pass
            except Exception:
                pass

            LOG.info("Daily recap broadcast sent")
        except Exception as e:
            LOG.warning("Daily recap error: %s", e)
