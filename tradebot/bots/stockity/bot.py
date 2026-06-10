"""StockityBot — Proactive Binary Options Signal Dispatcher.

Extracted and modularized from bots/stockity-bot/bot.py (391 LOC).
Auto-generates CALL (UP) / PUT (DOWN) signals for binary options.
Data sources: Binance (crypto, instant), Yahoo/Forex (rate-limited), Stockity WS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from tradebot.bots.base import BaseBot
from tradebot.config import settings

LOG = logging.getLogger("tradebot.bots.stockity.bot")

# ── Symbol configuration ──────────────────────────────────────────────────

DEFAULT_SYMBOLS: list[str] = [
    "EURUSD=X", "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
    "GC=F", "CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX",
]

SYMBOL_EMOJI: dict[str, str] = {
    "EURUSD=X": "💶", "GBPUSD=X": "💷", "USDJPY=X": "💴",
    "AUDUSD=X": "🇦🇺", "USDCAD=X": "🇨🇦", "NZDUSD=X": "🇳🇿", "USDCHF=X": "🇨🇭",
    "BTC-USD": "₿", "ETH-USD": "⟠", "SOL-USD": "◎", "XRP-USD": "✕",
    "DOGE-USD": "🐕", "ADA-USD": "🅰", "DOT-USD": "●", "LINK-USD": "⬡",
    "GC=F": "🥇",
    "CRYPTO_IDX": "📊", "BTC_IDX": "₿", "ETH_IDX": "⟠", "GOLD_IDX": "🥇",
}

SCAN_TIMEOUT: int = 40


@dataclass
class StockitySettings:
    """Settings for Stockity bot — loaded from tradebot.config.settings."""
    token: str = ""
    symbols: list[str] = field(default_factory=lambda: DEFAULT_SYMBOLS.copy())
    interval: str = "1m"
    lookback: str = "2d"
    expiry: str = "1m"
    scan_s: int = 300
    min_conf: int = 62
    authtoken: str = ""
    user_id: str = ""
    full_cookie: str = ""

    @classmethod
    def from_settings(cls) -> StockitySettings:
        return cls(
            token=settings.TELEGRAM_BOT_TOKEN,
            authtoken=settings.STOCKITY_AUTHTOKEN,
            full_cookie=settings.STOCKITY_FULL_COOKIE,
            user_id=settings.STOCKITY_USER_ID,
            interval="1m",
            expiry="1m",
            scan_s=300,
            min_conf=62,
        )

    def update_credentials(self, authtoken: str, user_id: str, full_cookie: str = "") -> None:
        self.authtoken = authtoken.strip()
        self.user_id = user_id.strip()
        self.full_cookie = full_cookie.strip()
        LOG.info("Credentials updated: authtoken=%s..., user_id=%s",
                 self.authtoken[:20] if self.authtoken else "empty", self.user_id)


class StockityBot(BaseBot):
    """Proactive binary-options signal dispatcher.

    Runs a background scan loop that checks all configured symbols
    and dispatches tradeable signals to the configured Telegram chat.
    """

    def __init__(self, name: str = "stockity-bot") -> None:
        super().__init__(name=name)
        self._settings = StockitySettings.from_settings()
        self._home_chat_id: int | None = None
        self._last_seen: dict[str, int] = {}
        self._app: Application | None = None
        self._signal_history: list[dict[str, Any]] = []
        self._load_history()

    # ── Signal history ──────────────────────────────────────────────────

    def _history_path(self) -> Path:
        return Path(settings.DATA_DIR) / "stockity_signal_history.json"

    def _load_history(self) -> None:
        p = self._history_path()
        if p.exists():
            try:
                self._signal_history = json.loads(p.read_text())
            except Exception:
                self._signal_history = []
        if len(self._signal_history) > 200:
            self._signal_history = self._signal_history[-200:]

    def _save_signal(self, sym: str, action: str, conf: int, price: float,
                     expiry: str, source: str, reason: str) -> None:
        self._signal_history.append({
            "ts": datetime.now(UTC).strftime("%H:%M UTC"),
            "sym": sym, "action": action, "conf": conf,
            "price": round(price, 6), "expiry": expiry,
            "source": source, "reason": reason[:60],
        })
        if len(self._signal_history) > 200:
            self._signal_history = self._signal_history[-200:]
        self._history_path().parent.mkdir(parents=True, exist_ok=True)
        self._history_path().write_text(json.dumps(self._signal_history, indent=2))

    # ── Lifecycle ───────────────────────────────────────────────────────

    def _register_commands(self) -> None:
        self._command_handlers = {
            "start": self._cmd_start,
            "help": self._cmd_start,
            "symbols": self._cmd_symbols,
            "signal": self._cmd_signal,
            "scan": self._cmd_scan,
            "stats": self._cmd_stats,
            "cookies": self._cmd_cookies,
            "balance": self._cmd_balance,
            "deposit": self._cmd_deposit,
        }
    async def start(self) -> None:
        await super().start()
        self._schedule_background(self._proactive_cycle())

    # ── Signal generation ───────────────────────────────────────────────

    async def _gen_signal(self, symbol: str) -> Any | None:
        """Generate signal for a single symbol."""
        try:
            from tradebot.signals import resolve  # type: ignore
            sig = await resolve(
                symbol,
                self._settings.interval,
                self._settings.lookback,
                self._settings.authtoken,
                self._settings.user_id,
                self._settings.full_cookie,
            )
            return sig
        except Exception as exc:
            LOG.warning("signal fail %s: %s", symbol, exc)
            return None

    async def _multi_scan(self) -> list[Any]:
        """Scan all symbols, return tradeable signals sorted by confidence."""
        results: list[Any | None] = []
        for sym in self._settings.symbols:
            try:
                sig = await asyncio.wait_for(self._gen_signal(sym), timeout=SCAN_TIMEOUT)
            except TimeoutError:
                LOG.warning("⏱️ %s timeout", sym)
                results.append(None)
                continue
            except Exception:
                results.append(None)
                continue
            results.append(sig)

        valid = [
            r for r in results
            if r and getattr(r, "is_tradeable", False) and getattr(r, "confidence", 0) >= self._settings.min_conf  # noqa: E501
        ]
        valid.sort(key=lambda s: getattr(s, "confidence", 0), reverse=True)
        return valid

    # ── Format helpers ──────────────────────────────────────────────────

    @staticmethod
    def _bar(conf: int, width: int = 8) -> str:
        filled = round(conf / 100 * width)
        return "█" * filled + "░" * (width - filled)

    def _format_signal(self, sig: Any, show_reason: bool = True) -> str:
        emoji = SYMBOL_EMOJI.get(getattr(sig, "symbol", ""), "📡")
        action = getattr(sig, "action", "WAIT")
        confidence = getattr(sig, "confidence", 0)
        price = getattr(sig, "price", 0)
        source = getattr(sig, "source_badge", "")
        reason = getattr(sig, "reason", "")

        if action == "CALL":
            dir_icon = "🟢"
            dir_text = "BUY (UP)"
        elif action == "PUT":
            dir_icon = "🔴"
            dir_text = "SELL (DOWN)"
        else:
            dir_icon = "⚪"
            dir_text = "WAIT"

        conf_bar = self._bar(confidence)
        reason_text = f"\n💡 *Why:* `{reason}`" if show_reason and reason else ""

        return (
            f"{emoji} *{getattr(sig, 'symbol', '?')}* — {dir_icon} *{dir_text}*\n"
            f"┌─────────────────────\n"
            f"│ Direction : *{action}*\n"
            f"│ Confidence: `{conf_bar} {confidence}%`\n"
            f"│ Current   : `{price:.6g}`\n"
            f"│ Expiry    : `{self._settings.expiry}`\n"
            f"│ Source    : {source}\n"
            f"└─────────────────────\n"
            f"{reason_text}"
        )

    def _format_signal_compact(self, sig: Any) -> str:
        emoji = SYMBOL_EMOJI.get(getattr(sig, "symbol", ""), "📡")
        action = getattr(sig, "action", "WAIT")
        confidence = getattr(sig, "confidence", 0)
        price = getattr(sig, "price", 0)
        source = getattr(sig, "source_badge", "")
        arrow = "⬆️" if action == "CALL" else "⬇️" if action == "PUT" else "➖"
        return f"{emoji} `{getattr(sig, 'symbol', '?')}` {arrow} **{action}** @ `{price:.6g}` [{confidence}%] {source}"  # noqa: E501

    # ── Background cycle ────────────────────────────────────────────────

    async def _proactive_cycle(self) -> None:
        """Background loop scanning all symbols at interval."""
        LOG.info("Stockity proactive cycle started: %d symbols scan=%ds",
                 len(self._settings.symbols), self._settings.scan_s)
        while self._running:
            try:
                signals = await self._multi_scan()
                for sig in signals:
                    sym = getattr(sig, "symbol", "")
                    conf = getattr(sig, "confidence", 0)
                    prev = self._last_seen.get(sym, 0)
                    if conf > prev + 4:
                        self._last_seen[sym] = conf
                        self._save_signal(
                            sym, getattr(sig, "action", ""), conf,
                            getattr(sig, "price", 0), self._settings.expiry,
                            getattr(sig, "source", ""), getattr(sig, "reason", ""),
                        )
                        msg = self._format_signal(sig)
                        if self._home_chat_id:
                            # Send via BaseBot's telegram service
                            await self._telegram.send_message(msg)
                        LOG.info("🚀 %s %s %d%% (src=%s)",
                                 sym, getattr(sig, "action", ""), conf,
                                 getattr(sig, "source", ""))
            except Exception as exc:
                LOG.error("proactive_cycle: %s", exc)
            await asyncio.sleep(self._settings.scan_s)

    # ── Command handlers ────────────────────────────────────────────────

    async def _cmd_start(self, args: list[str], chat_id: str | None = None) -> str:
        reply = (
            "🤖 *Stockity Binary Bot* — *AKTIF PERMANEN*\n"
            "Binary options: *CALL* = BUY (UP)  •  *PUT* = SELL (DOWN)\n\n"
            "*Commands:*\n"
            "`/scan` — full market scan\n"
            "`/signal SYMBOL` — check one symbol\n"
            "`/symbols` — list tracked\n"
            "`/stats` — signal history\n\n"
            f"*Tracked:* `{', '.join(self._settings.symbols[:14])}`\n"
            f"*Expiry:* `{self._settings.expiry}`  *Min Conf:* `{self._settings.min_conf}%`\n\n"
            f"*Credentials:* authtoken={'SET' if self._settings.authtoken else 'NOT SET'}"
        )
        return reply

    async def _cmd_symbols(self, args: list[str], chat_id: str | None = None) -> str:
        forex = [x for x in self._settings.symbols if "=X" in x or "=F" in x]
        crypto = [x for x in self._settings.symbols if "-USD" in x and "=F" not in x]
        stockity_only = [x for x in self._settings.symbols if "_IDX" in x]

        parts = []
        if forex:
            parts.append(f"💱 *Forex (+Gold):*\n`{'  '.join(forex)}`")
        if crypto:
            parts.append(f"₿ *Crypto:*\n`{'  '.join(crypto)}`")
        if stockity_only:
            parts.append(f"⚡ *Stockity:* `{'  '.join(stockity_only)}` *(WS auth needed)*")
        return "\n\n".join(parts) if parts else "No symbols."

    async def _cmd_signal(self, args: list[str], chat_id: str | None = None) -> str:
        symbol = args[0].strip().upper() if args else self._settings.symbols[0]
        sig = await self._gen_signal(symbol)
        if sig and getattr(sig, "is_tradeable", False):
            return self._format_signal(sig)
        elif sig and getattr(sig, "action", "") == "WAIT":
            return (
                f"⚪ *{symbol}* — WAIT (too risky)\n"
                f"Confidence: `{getattr(sig, 'confidence', 0)}%`\n"
                f"Reason: `{getattr(sig, 'reason', '')}`"
            )
        return f"⚠️ No data for `{symbol}`."

    async def _cmd_scan(self, args: list[str], chat_id: str | None = None) -> str:
        found = await self._multi_scan()
        if found:
            lines = ["📊 *Scan Results:*"]
            for sig in found[:10]:
                lines.append(self._format_signal_compact(sig))
            lines.append(f"\n_Expiry: `{self._settings.expiry}` | Generated {len(found)} tradeable signals_")  # noqa: E501
            return "\n".join(lines)
        return "⚪ No tradeable signals right now. Market is neutral."

    async def _cmd_stats(self, args: list[str], chat_id: str | None = None) -> str:
        total = len(self._signal_history)
        if total == 0:
            return "📊 No signal history yet. Signals will be tracked as they're dispatched."
        last10 = self._signal_history[-10:]
        lines = [f"📊 *Signal History* — {total} total signals\n"]
        for h in reversed(last10):
            arrow = "🟢" if h["action"] == "CALL" else "🔴" if h["action"] == "PUT" else "⚪"
            lines.append(f"{h['ts']} {arrow} `{h['sym']}` *{h['action']}* {h['conf']}% @{h['price']}")  # noqa: E501
            if h.get("reason"):
                lines.append(f"   └ {h['reason']}")
        return "\n".join(lines)

    async def _cmd_cookies(self, args: list[str], chat_id: str | None = None) -> str:
        if not args or len(args) < 2:
            return (
                "🔑 *Update Stockity WS Credentials*\n\n"
                "Format: `/cookies <authtoken> <userId>`\n\n"
                f"Current: authtoken=`{self._settings.authtoken[:20] if self._settings.authtoken else 'NOT SET'}`... "  # noqa: E501
                f"userId=`{self._settings.user_id or 'NOT SET'}`\n\n"
                "Get fresh cookies:\n"
                "1. Login to https://stockity.com\n"
                "2. DevTools → Application → Cookies → stockity.com\n"
                "3. Copy `authtoken` and `userId`"
            )
        authtoken = args[0].strip()
        user_id = args[1].strip()
        
        # Update in-memory
        self._settings.update_credentials(authtoken, user_id)
        
        # Persist to .env file
        try:
            env_path = Path(".env")
            content = env_path.read_text() if env_path.exists() else ""
            lines = content.split("\n")

            # Remove old STOCKITY_AUTHTOKEN and STOCKITY_USER_ID lines if exist
            lines = [
                line for line in lines
                if not line.startswith("STOCKITY_AUTHTOKEN=")
                and not line.startswith("STOCKITY_USER_ID=")
            ]
            # Add new lines
            lines.append(f"STOCKITY_AUTHTOKEN={authtoken}")
            lines.append(f"STOCKITY_USER_ID={user_id}")
            env_path.write_text("\n".join(lines))
            LOG.info("✅ Cookies persisted to .env")
        except Exception as e:
            LOG.error(f"Failed to persist cookies: {e}")
            return (
                f"✅ *Credentials Updated!* (but failed to persist to .env)\n"
                f"authtoken: `{authtoken[:20]}...`\n"
                f"userId: `{user_id}`\n\n"
                f"❌ Error: {e}"
            )

        return (
            "✅ *Credentials Updated & Persisted!*\n"
            f"authtoken: `{authtoken[:20]}...`\n"
            f"userId: `{user_id}`\n\n"
            "Next scan will use Stockity WS for CRYPTO_IDX, BTC_IDX, etc."
        )

    # ── PTB Integration ─────────────────────────────────────────────────

    def build_app(self) -> Application:
        """Build the PTB Application with all command handlers."""
        app = Application.builder().token(self.bot_token).build()
        app.bot_data["settings"] = self._settings
        app.bot_data["home_chat_id"] = None

        app.add_handler(CommandHandler(["start", "help"], self._ptb_cmd_start))
        app.add_handler(CommandHandler("symbols", self._ptb_cmd_symbols))
        app.add_handler(CommandHandler("signal", self._ptb_cmd_signal))
        app.add_handler(CommandHandler("scan", self._ptb_cmd_scan))
        app.add_handler(CommandHandler("stats", self._ptb_cmd_stats))
        app.add_handler(CommandHandler("cookies", self._ptb_cmd_cookies))

        app.add_handler(CommandHandler("balance", self._ptb_cmd_balance))
        app.add_handler(CommandHandler("deposit", self._ptb_cmd_deposit))

        self._app = app
        return app
    # ── PTB command wrappers ────────────────────────────────────────────

    async def _ptb_cmd_start(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._home_chat_id = upd.effective_chat.id
        LOG.info("📨 /start from chat_id=%s", upd.effective_chat.id)
        reply = await self._cmd_start([])
        await upd.message.reply_markdown(reply)

    async def _ptb_cmd_symbols(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        reply = await self._cmd_symbols([])
        await upd.message.reply_markdown(reply)

    async def _ptb_cmd_signal(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        symbol = args[0].strip().upper() if args else self._settings.symbols[0]
        await upd.message.reply_text(f"🔍 Checking `{symbol}`...")
        reply = await self._cmd_signal(args)
        await upd.message.reply_markdown(reply)

    async def _ptb_cmd_scan(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        LOG.info("📨 /scan from chat_id=%s", upd.effective_chat.id)
        msg = await upd.message.reply_text(f"🔍 Scanning {len(self._settings.symbols)} symbols...")
        reply = await self._cmd_scan([])
        await msg.edit_text(reply, parse_mode="Markdown")

    async def _ptb_cmd_stats(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        LOG.info("📨 /stats from chat_id=%s", upd.effective_chat.id)
        reply = await self._cmd_stats([])
        await upd.message.reply_markdown(reply)

    async def _ptb_cmd_cookies(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        LOG.info("📨 /cookies from chat_id=%s", upd.effective_chat.id)
        await upd.message.reply_markdown(await self._cmd_cookies(ctx.args or []))



        """Show account balance, positions, winrate."""
        broker = StockityBroker()
        try:
            await broker.connect()
            await asyncio.sleep(3)
            s = broker.stats
            lines = [
                "💰 *Stockity Account*",
                f"Balance: `{s['balance']:,}` {s['currency']} (~${s['balance_usd']:.2f})",
                f"Open: {s['open_positions']} | Closed: {s['total_trades']}",
                f"Wins: {s['wins']} | Losses: {s['losses']} | WR: {s['winrate']:.1f}%",
                f"P&L: `{s['total_pnl_raw']:,}` {s['currency']}",
            ]
            if broker.open_positions:
                lines.append("\n*Open Positions:*")
                for p in broker.open_positions[:5]:
                    lines.append(
                        f"  {p.get('trend','?').upper():4s} {p.get('option_type','?'):6s} "
                        f"@ {p.get('open_rate',0)} | {p.get('close_time','?')[:16]}"
                    )
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Balance: {e}"
        finally:
            await broker.close()

    async def _cmd_deposit(self, args: list[str], chat_id: str | None = None) -> str:
        """Generate QRIS deposit payment link."""
        if not args:
            return (
                "💳 *Deposit Stockity*\n\n"
                "`/deposit <amount>`\n"
                "Contoh: `/deposit 150000`\n\n"
                "Min: Rp 50,000 | via QRIS."
            )
        try:
            amount = int(args[0])
        except ValueError:
            return f"❌ Invalid: `{args[0]}`"
        if amount < 50000:
            return f"❌ Min Rp 50,000"

        api = StockityREST()
        try:
            r = await api.deposit(amount=amount, handler="qris")
            if r and r.get("success"):
                url = r.get("redirect_url", "")
                return (
                    f"💳 *Deposit Rp {amount:,}*\n\n"
                    f"[🔗 Bayar via QRIS]({url})\n\n"
                    f"`{url}`"
                )
            return "❌ Deposit gagal"
        finally:
            await api.close()

    # ── PTB wrappers: balance + deposit ─────────────────────────────────

    async def _ptb_cmd_balance(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = await upd.message.reply_text("⏳ Checking...")
        reply = await self._cmd_balance([])
        await msg.edit_text(reply, parse_mode="Markdown")

    async def _ptb_cmd_deposit(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = await upd.message.reply_text("⏳ Generating QRIS...")
        reply = await self._cmd_deposit(ctx.args or [])
        await msg.edit_text(reply, parse_mode="Markdown")

    # ── Run ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Build app and start polling (blocking)."""
        app = self.build_app()
        LOG.info("🤖 Starting — %d symbols scan=%ds min_conf=%d%% expiry=%s",
                 len(self._settings.symbols), self._settings.scan_s,
                 self._settings.min_conf, self._settings.expiry)
        LOG.info("   📈 Forex: %s", [s for s in self._settings.symbols if "=X" in s or "=F" in s])
        LOG.info("   ₿ Crypto: %s", [s for s in self._settings.symbols if "-USD" in s and "=F" not in s])  # noqa: E501
        LOG.info("   ⚡ Stockity: %s", [s for s in self._settings.symbols if "IDX" in s])

        loop = asyncio.get_event_loop()
        loop.create_task(self._proactive_cycle())
        app.run_polling(allowed_updates=Update.ALL_TYPES)