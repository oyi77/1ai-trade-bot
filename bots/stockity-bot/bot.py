"""
📡 Stockity Binary Bot — Proactive Signal Dispatcher
────────────────────────────────────────────────────
Auto-generates CALL (UP) / PUT (DOWN) signals for binary options.
Data sources: Binance (crypto, instant), Yahoo/Forex (rate-limited), Stockity WS (needs auth).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core import Signal
from signals import resolve

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("stockity-bot")

# ── Config ────────────────────────────────────────────────────────────────

@dataclass
class Settings:
    token: str
    symbols: list[str]
    interval: str = "1m"
    lookback: str = "2d"
    expiry: str = "1m"
    scan_s: int = 300
    min_conf: int = 62
    authtoken: str = ""
    user_id: str = ""
    full_cookie: str = ""

    def update_credentials(self, authtoken: str, user_id: str, full_cookie: str = ""):
        self.authtoken = authtoken.strip()
        self.user_id = user_id.strip()
        self.full_cookie = full_cookie.strip()
        LOG.info("🔑 Credentials updated: authtoken=%s..., user_id=%s, full_cookie=%s",
                 self.authtoken[:20] if self.authtoken else "empty", self.user_id,
                 "SET" if self.full_cookie else "NOT SET")


DEFAULT_SYMBOLS = [
    # Forex (via Yahoo/forex module — throttled)
    "EURUSD=X",
    # Crypto (via Binance — instant, no rate limits!)
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
    # Gold (via GC=F on Yahoo — throttled)
    "GC=F",
    # Stockity platform (require fresh WS auth)
    "CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX",
]

SYMBOL_EMOJI = {
    "EURUSD=X": "💶", "GBPUSD=X": "💷", "USDJPY=X": "💴",
    "AUDUSD=X": "🇦🇺", "USDCAD=X": "🇨🇦", "NZDUSD=X": "🇳🇿", "USDCHF=X": "🇨🇭",
    "BTC-USD": "₿", "ETH-USD": "⟠", "SOL-USD": "◎", "XRP-USD": "✕",
    "DOGE-USD": "🐕", "ADA-USD": "🅰", "DOT-USD": "●", "LINK-USD": "⬡",
    "GC=F": "🥇",
    "CRYPTO_IDX": "📊", "BTC_IDX": "₿", "ETH_IDX": "⟠", "GOLD_IDX": "🥇",
}

# ── Signal History ────────────────────────────────────────────────────────

HISTORY_FILE = Path(__file__).parent / "signal_history.json"


def load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return []
    return []


def save_signal(sym: str, action: str, conf: int, price: float, expiry: str, source: str, reason: str):
    history = load_history()
    history.append({
        "ts": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "sym": sym, "action": action, "conf": conf,
        "price": round(price, 6), "expiry": expiry,
        "source": source, "reason": reason[:60],
    })
    # Keep last 200 signals
    if len(history) > 200:
        history = history[-200:]
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def win_rate(history: list[dict]) -> tuple[int, int, float]:
    """Simulated accuracy tracker — user marks CALL/PUT results manually."""
    # For now, just return total signal count
    total = len(history)
    return total, 0, 0.0


# ── Settings ──────────────────────────────────────────────────────────────

def parse_env() -> Settings:
    t = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not t or t.startswith("PLACE_"):
        raise SystemExit("❌ TELEGRAM_BOT_TOKEN not set in .env")

    raw = os.getenv("SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    symbols = [s.strip() for s in raw.split(",") if s.strip()]

    return Settings(
        token=t,
        symbols=symbols,
        interval=os.getenv("INTERVAL", "1m"),
        lookback=os.getenv("LOOKBACK_PERIOD", "2d"),
        expiry=os.getenv("EXPIRY", "1m"),
        scan_s=int(os.getenv("SCAN_SECONDS", "300")),
        min_conf=int(os.getenv("MIN_CONFIDENCE", "62")),
        authtoken=os.getenv("STOCKITY_AUTH_TOKEN", "").strip(),
        user_id=os.getenv("STOCKITY_USER_ID", "").strip(),
        full_cookie=os.getenv("STOCKITY_FULL_COOKIE", "").strip(),
    )


# ── Signal Generation ────────────────────────────────────────────────────

async def gen_signal(symbol: str, settings: Settings) -> Optional[Signal]:
    try:
        return await resolve(symbol, settings.interval, settings.lookback, settings.authtoken, settings.user_id, settings.full_cookie)
    except Exception as exc:
        LOG.warning("signal fail %s: %s", symbol, exc)
        return None


SCAN_TIMEOUT = 40


async def multi_scan(settings: Settings) -> list[Signal]:
    """Scan all symbols, return tradeable signals sorted by confidence."""
    results: list[Optional[Signal]] = []
    for sym in settings.symbols:
        try:
            sig = await asyncio.wait_for(gen_signal(sym, settings), timeout=SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            LOG.warning("⏱️ %s timeout", sym)
            results.append(None)
            continue
        except Exception:
            results.append(None)
            continue
        results.append(sig)

    valid = [r for r in results if r and r.is_tradeable and r.confidence >= settings.min_conf]
    valid.sort(key=lambda s: s.confidence, reverse=True)
    return valid


# ── Format Helpers ────────────────────────────────────────────────────────

def bar(conf: int, width: int = 8) -> str:
    """Visual confidence bar like ██████░░ 68%"""
    filled = round(conf / 100 * width)
    return "█" * filled + "░" * (width - filled)


def format_signal(sig: Signal, settings: Settings, show_reason: bool = True) -> str:
    """Professional binary-options signal message."""
    emoji = SYMBOL_EMOJI.get(sig.symbol, "📡")

    # Big direction indicator
    if sig.action == "CALL":
        dir_icon = "🟢"
        dir_text = "BUY (UP)"
    elif sig.action == "PUT":
        dir_icon = "🔴"
        dir_text = "SELL (DOWN)"
    else:
        dir_icon = "⚪"
        dir_text = "WAIT"

    conf_bar = bar(sig.confidence)
    reason_text = f"\n💡 *Why:* `{sig.reason}`" if show_reason and sig.reason else ""

    return (
        f"{emoji} *{sig.symbol}* — {dir_icon} *{dir_text}*\n"
        f"┌─────────────────────\n"
        f"│ Direction : *{sig.action}*\n"
        f"│ Confidence: `{conf_bar} {sig.confidence}%`\n"
        f"│ Current   : `{sig.price:.6g}`\n"
        f"│ Expiry    : `{settings.expiry}`\n"
        f"│ Source    : {sig.source_badge}\n"
        f"└─────────────────────\n"
        f"{reason_text}"
    )


def format_signal_compact(sig: Signal, settings: Settings) -> str:
    """Compact one-liner for history / quick view."""
    emoji = SYMBOL_EMOJI.get(sig.symbol, "📡")
    arrow = "⬆️" if sig.action == "CALL" else "⬇️" if sig.action == "PUT" else "➖"
    return f"{emoji} `{sig.symbol}` {arrow} **{sig.action}** @ `{sig.price:.6g}` [{sig.confidence}%] {sig.source_badge}"


# ── Proactive Dispatch ───────────────────────────────────────────────────

async def proactive_cycle(app: Application):
    settings: Settings = app.bot_data["settings"]
    last_seen: dict[str, int] = {}
    home = app.bot_data.get("home_chat_id")

    while True:
        try:
            signals = await multi_scan(settings)
            for sig in signals:
                prev = last_seen.get(sig.symbol, 0)
                if sig.confidence > prev + 4:
                    last_seen[sig.symbol] = sig.confidence
                    save_signal(sig.symbol, sig.action, sig.confidence, sig.price,
                                settings.expiry, sig.source, sig.reason)
                    msg = format_signal(sig, settings)
                    if home:
                        await app.bot.send_message(chat_id=home, text=msg, parse_mode="Markdown")
                    LOG.info("🚀 %s %s %d%% (src=%s)", sig.symbol, sig.action, sig.confidence, sig.source)
        except Exception as exc:
            LOG.error("proactive_cycle: %s", exc)
        await asyncio.sleep(settings.scan_s)


# ── Command Handlers ─────────────────────────────────────────────────────

async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s: Settings = ctx.application.bot_data["settings"]
    ctx.application.bot_data["home_chat_id"] = upd.effective_chat.id
    LOG.info("📨 /start from chat_id=%s thread_id=%s", upd.effective_chat.id, getattr(upd.message, 'message_thread_id', None))
    await upd.message.reply_markdown(
        "🤖 *Stockity Binary Bot* — *AKTIF PERMANEN*\n"
        "Binary options: *CALL* = BUY (UP)  •  *PUT* = SELL (DOWN)\n\n"
        "*Commands:*\n"
        "`/scan` — full market scan\n"
        "`/signal SYMBOL` — check one symbol\n"
        "`/symbols` — list tracked\n"
        "`/stats` — signal history\n\n"
        f"*Tracked:* `{', '.join(s.symbols[:14])}`\n"
        f"*Expiry:* `{s.expiry}`  *Min Conf:* `{s.min_conf}%`"
    )


async def cmd_symbols(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s: Settings = ctx.application.bot_data["settings"]
    LOG.info("📨 /symbols from chat_id=%s", upd.effective_chat.id)
    forex = [x for x in s.symbols if "=X" in x or "=F" in x]
    crypto = [x for x in s.symbols if "-USD" in x and "=F" not in x]
    stockity_only = [x for x in s.symbols if "_IDX" in x]

    parts = []
    if forex:
        parts.append(f"💱 *Forex (+Gold):*\n`{'  '.join(forex)}`")
    if crypto:
        parts.append(f"₿ *Crypto:*\n`{'  '.join(crypto)}`")
    if stockity_only:
        parts.append(f"⚡ *Stockity:* `{'  '.join(stockity_only)}` *(WS auth needed)*")

    await upd.message.reply_markdown("\n\n".join(parts) if parts else "No symbols.")


async def cmd_signal(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s: Settings = ctx.application.bot_data["settings"]
    LOG.info("📨 /signal from chat_id=%s args=%s", upd.effective_chat.id, ctx.args)
    symbol = ctx.args[0].strip().upper() if ctx.args else s.symbols[0]
    await upd.message.reply_text(f"🔍 Checking `{symbol}`...")
    sig = await gen_signal(symbol, s)
    if sig and sig.is_tradeable:
        await upd.message.reply_markdown(format_signal(sig, s))
    elif sig and sig.action == "WAIT":
        await upd.message.reply_markdown(
            f"⚪ *{symbol}* — WAIT (too risky)\n"
            f"Confidence: `{sig.confidence}%`\n"
            f"Reason: `{sig.reason}`"
        )
    else:
        await upd.message.reply_text(f"⚠️ No data for `{symbol}`.")


async def cmd_scan(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s: Settings = ctx.application.bot_data["settings"]
    LOG.info("📨 /scan from chat_id=%s", upd.effective_chat.id)
    msg = await upd.message.reply_text(f"🔍 Scanning {len(s.symbols)} symbols...")
    found = await multi_scan(s)
    if found:
        lines = ["📊 *Scan Results:*"]
        for sig in found[:10]:
            lines.append(format_signal_compact(sig, s))
        lines.append(f"\n_Expiry: `{s.expiry}` | Generated {len(found)} tradeable signals_")
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    else:
        await msg.edit_text("⚪ No tradeable signals right now. Market is neutral.")


async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    LOG.info("📨 /stats from chat_id=%s", upd.effective_chat.id)
    history = load_history()
    total = len(history)

    if total == 0:
        await upd.message.reply_text("📊 No signal history yet. Signals will be tracked as they're dispatched.")
        return

    # Last 10 signals
    last10 = history[-10:]
    lines = [f"📊 *Signal History* — {total} total signals\n"]
    for h in reversed(last10):
        arrow = "🟢" if h["action"] == "CALL" else "🔴" if h["action"] == "PUT" else "⚪"
        lines.append(f"{h['ts']} {arrow} `{h['sym']}` *{h['action']}* {h['conf']}% @{h['price']}")
        if h.get("reason"):
            lines.append(f"   └ {h['reason']}")

    await upd.message.reply_markdown("\n".join(lines))


async def cmd_cookies(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Update Stockity WS credentials: /cookies <authtoken> <userId>"""
    LOG.info("📨 /cookies from chat_id=%s", upd.effective_chat.id)
    s: Settings = ctx.application.bot_data["settings"]
    
    if not ctx.args or len(ctx.args) < 2:
        await upd.message.reply_markdown(
            "🔑 *Update Stockity WS Credentials*\n\n"
            "Format: `/cookies <authtoken> <userId>`\n\n"
            "Example:\n"
            "`/cookies 55e3e8db-97c4-44aa-8155-1ec38505ff4a 182897415`\n\n"
            f"Current: authtoken=`{s.authtoken[:20] if s.authtoken else 'NOT SET'}`... userId=`{s.user_id or 'NOT SET'}`\n\n"
            "Get fresh cookies:\n"
            "1. Login to https://stockity.com\n"
            "2. DevTools → Application → Cookies → stockity.com\n"
            "3. Copy `authtoken` and `userId`"
        )
        return
    
    authtoken = ctx.args[0].strip()
    user_id = ctx.args[1].strip()
    
    s.update_credentials(authtoken, user_id)
    
    await upd.message.reply_markdown(
        "✅ *Credentials Updated!*\n"
        f"authtoken: `{authtoken[:20]}...`\n"
        f"userId: `{user_id}`\n\n"
        "Next scan will use Stockity WS for CRYPTO_IDX, BTC_IDX, etc."
    )


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    settings = parse_env()
    app = Application.builder().token(settings.token).build()
    app.bot_data["settings"] = settings
    app.bot_data["home_chat_id"] = None

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("symbols", cmd_symbols))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("cookies", cmd_cookies))

    LOG.info("🤖 Starting — %d symbols scan=%ds min_conf=%d%% expiry=%s",
             len(settings.symbols), settings.scan_s, settings.min_conf, settings.expiry)
    LOG.info("   📈 Forex: %s", [s for s in settings.symbols if "=X" in s or "=F" in s])
    LOG.info("   ₿ Crypto: %s", [s for s in settings.symbols if "-USD" in s and "=F" not in s])
    LOG.info("   ⚡ Stockity: %s", [s for s in settings.symbols if "IDX" in s])

    loop = asyncio.get_event_loop()
    loop.create_task(proactive_cycle(app))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
