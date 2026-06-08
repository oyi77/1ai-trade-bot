"""
Stockity Signal Bot — Dual data source (Yahoo for CFDs/forex, Stockity WS for platform assets).

Data sources:
  - Yahoo Finance: all standard symbols (EURUSD=X, BTC-USD, etc.)
  - Stockity WebSocket: platform-specific assets (CRYPTO_IDX, etc.) — requires valid auth token

Run:
  pip install -r requirements.txt
  cp .env.example .env
  # fill .env
  python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
import time

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    from stockity_connector import (
        StockityDataConnector,
        generate_stockity_signal,
        Candle,
    )
    HAS_STOCKITY_CONNECTOR = True
except ImportError:
    HAS_STOCKITY_CONNECTOR = False

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOG = logging.getLogger("stockity-signal-bot")


@dataclass(frozen=True)
class Settings:
    token: str
    symbols: list[str]
    stockity_symbols: list[str]
    interval: str = "1m"
    lookback_period: str = "2d"
    scan_seconds: int = 300
    min_confidence: int = 62
    stockity_auth_token: str = ""
    stockity_user_id: str = ""


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str       # CALL, PUT, WAIT
    confidence: int
    price: float
    reason: str
    timestamp_utc: str
    source: str = "yahoo"   # "yahoo" or "stockity"


def parse_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token.startswith("PUT_"):
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env or environment first.")

    symbols = [s.strip() for s in os.getenv("SYMBOLS", "EURUSD=X,BTC-USD").split(",") if s.strip()]
    stockity_syms = [s.strip() for s in os.getenv("STOCKITY_SYMBOLS", "CRYPTO_IDX").split(",") if s.strip()]

    return Settings(
        token=token,
        symbols=symbols,
        stockity_symbols=stockity_syms,
        interval=os.getenv("INTERVAL", "1m"),
        lookback_period=os.getenv("LOOKBACK_PERIOD", "2d"),
        scan_seconds=int(os.getenv("SCAN_SECONDS", "300")),
        min_confidence=int(os.getenv("MIN_CONFIDENCE", "62")),
        stockity_auth_token=os.getenv("STOCKITY_AUTH_TOKEN", "").strip(),
        stockity_user_id=os.getenv("STOCKITY_USER_ID", "").strip(),
    )


# ── Yahoo signal engine (unchanged) ──────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def fetch_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        interval=interval,
        period=period,
        progress=False,
        auto_adjust=True,
        threads=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna()


def generate_yahoo_signal(symbol: str, interval: str = "1m", period: str = "2d") -> Signal:
    df = fetch_ohlc(symbol, interval, period)
    if len(df) < 60:
        raise ValueError(f"Not enough candles for {symbol}: {len(df)}")

    close = df["Close"]
    price = float(close.iloc[-1])
    ema_fast = ema(close, 9)
    ema_slow = ema(close, 21)
    trend = ema(close, 50)
    rsi14 = rsi(close, 14)

    fast_now, slow_now, trend_now = float(ema_fast.iloc[-1]), float(ema_slow.iloc[-1]), float(trend.iloc[-1])
    fast_prev, slow_prev = float(ema_fast.iloc[-2]), float(ema_slow.iloc[-2])
    rsi_now = float(rsi14.iloc[-1])

    recent_high = float(df["High"].tail(20).max())
    recent_low = float(df["Low"].tail(20).min())
    range_pos = 50 if recent_high == recent_low else (price - recent_low) / (recent_high - recent_low) * 100

    score = 50
    reasons: list[str] = []

    if fast_now > slow_now:
        score += 8; reasons.append("EMA9 above EMA21")
    else:
        score -= 8; reasons.append("EMA9 below EMA21")

    if price > trend_now:
        score += 7; reasons.append("price above EMA50")
    else:
        score -= 7; reasons.append("price below EMA50")

    if fast_prev <= slow_prev and fast_now > slow_now:
        score += 10; reasons.append("fresh bullish crossover")
    elif fast_prev >= slow_prev and fast_now < slow_now:
        score -= 10; reasons.append("fresh bearish crossover")

    if 52 <= rsi_now <= 68:
        score += 7; reasons.append(f"RSI bullish ({rsi_now:.1f})")
    elif 32 <= rsi_now <= 48:
        score -= 7; reasons.append(f"RSI bearish ({rsi_now:.1f})")
    elif rsi_now > 75:
        score -= 5; reasons.append(f"RSI overbought ({rsi_now:.1f})")
    elif rsi_now < 25:
        score += 5; reasons.append(f"RSI oversold ({rsi_now:.1f})")
    else:
        reasons.append(f"RSI neutral ({rsi_now:.1f})")

    if range_pos > 90:
        score -= 4; reasons.append("near high")
    elif range_pos < 10:
        score += 4; reasons.append("near low")

    score = int(max(0, min(100, score)))
    if score >= 62:
        action = "CALL"; confidence = score
    elif score <= 38:
        action = "PUT"; confidence = 100 - score
    else:
        action = "WAIT"; confidence = max(score, 100 - score)

    return Signal(
        symbol=symbol, action=action, confidence=confidence, price=price,
        reason="; ".join(reasons[:5]),
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        source="yahoo",
    )


# ── Stockity signal engine ───────────────────────────────────────────

async def generate_stockity_signal_async(
    asset: str,
    settings: Settings,
) -> Optional[Signal]:
    """Generate signal for a Stockity-native asset via WebSocket."""
    if not HAS_STOCKITY_CONNECTOR or not settings.stockity_auth_token:
        return None

    try:
        async with StockityDataConnector(
            authtoken=settings.stockity_auth_token,
            user_id=settings.stockity_user_id,
        ) as conn:
            candles = await conn.get_candles(asset, period=60, count=100)
            if not candles:
                LOG.warning("No candles returned for %s", asset)
                return None

            result = generate_stockity_signal(candles)
            return Signal(
                symbol=asset,
                action=result.get("action", "WAIT"),
                confidence=result.get("confidence", 50),
                price=result.get("price", 0),
                reason=result.get("reason", "No data"),
                timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                source="stockity",
            )
    except Exception as exc:
        LOG.warning("Stockity connector failed for %s: %s", asset, exc)
        return None


# ── Signal resolution ────────────────────────────────────────────────

STOCKITY_ASSETS = {"CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"}

async def resolve_signal(symbol: str, settings: Settings) -> Signal:
    """Try Stockity data first for platform assets, fall back to Yahoo."""
    if symbol.upper() in STOCKITY_ASSETS or symbol.startswith("CRYPTO"):
        sig = await generate_stockity_signal_async(symbol.upper(), settings)
        if sig:
            return sig
        LOG.info("Stockity fetch failed for %s, trying Yahoo fallback", symbol)

    return await asyncio.to_thread(generate_yahoo_signal, symbol, settings.interval, settings.lookback_period)


def format_signal(sig: Signal) -> str:
    emoji = {"CALL": "🟢", "PUT": "🔴", "WAIT": "⚪"}[sig.action]
    source_badge = {"yahoo": "📈 Yahoo", "stockity": "⚡ Stockity"}.get(sig.source, sig.source)
    return (
        f"{emoji} *{sig.symbol}* — *{sig.action}* ({source_badge})\n"
        f"Price: `{sig.price:.6g}`\n"
        f"Confidence: *{sig.confidence}%*\n"
        f"Timeframe: short-term / 1–5 min observation\n"
        f"Why: {sig.reason}\n"
        f"Time: `{sig.timestamp_utc}`\n\n"
        "Risk note: PoC signal only, not financial advice. Test in demo mode first."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    text = (
        "🤖 Stockity Signal PoC Bot\n\n"
        "Commands:\n"
        "/signal SYMBOL — one signal, example `/signal EURUSD=X`\n"
        "/scan — scan configured symbols\n"
        "/symbols — show symbols\n"
        "/help — usage\n\n"
        f"Configured symbols: `{', '.join(settings.symbols)}`"
    )
    await update.message.reply_markdown(text)


async def symbols(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    await update.message.reply_markdown("Symbols:\n`" + ", ".join(settings.symbols) + "`")


async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    symbol = context.args[0].strip().upper() if context.args else settings.symbols[0]
    await update.message.reply_text(f"Checking {symbol}...")
    try:
        sig = await resolve_signal(symbol, settings)
        await update.message.reply_markdown(format_signal(sig))
    except Exception as exc:  # noqa: BLE001 - user-facing bot should not crash
        LOG.exception("signal failed")
        await update.message.reply_text(f"Could not generate signal for {symbol}: {exc}")


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    await update.message.reply_text("Scanning markets...")
    messages = []
    # scan Yahoo symbols
    for symbol in settings.symbols:
        try:
            sig = await resolve_signal(symbol, settings)
            if sig.action != "WAIT" and sig.confidence >= settings.min_confidence:
                messages.append(format_signal(sig))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("scan failed for %s: %s", symbol, exc)
    # scan Stockity symbols
    for symbol in settings.stockity_symbols:
        try:
            sig = await resolve_signal(symbol, settings)
            if sig.action != "WAIT" and sig.confidence >= settings.min_confidence:
                messages.append(format_signal(sig))
        except Exception as exc:
            LOG.warning("stockity scan failed for %s: %s", symbol, exc)
    if messages:
        for msg in messages[:8]:
            await update.message.reply_markdown(msg)
    else:
        await update.message.reply_text("No high-confidence CALL/PUT signals right now. WAIT is also a valid signal.")


async def auto_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Optional job: active per chat after /autoscan_on."""
    settings: Settings = context.application.bot_data["settings"]
    chat_id = context.job.chat_id
    found = []
    all_symbols = settings.symbols + settings.stockity_symbols
    for symbol in all_symbols:
        try:
            sig = await resolve_signal(symbol, settings)
            if sig.action != "WAIT" and sig.confidence >= settings.min_confidence:
                found.append(format_signal(sig))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("autoscan failed for %s: %s", symbol, exc)
    for msg in found[:5]:
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")


async def autoscan_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    chat_id = update.effective_chat.id
    name = f"autoscan:{chat_id}"
    for job in context.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    context.job_queue.run_repeating(auto_scan, interval=settings.scan_seconds, first=5, chat_id=chat_id, name=name)
    await update.message.reply_text(f"Auto-scan enabled every {settings.scan_seconds}s for this chat.")


async def autoscan_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    removed = 0
    for job in context.job_queue.get_jobs_by_name(f"autoscan:{chat_id}"):
        job.schedule_removal()
        removed += 1
    await update.message.reply_text("Auto-scan disabled." if removed else "Auto-scan was not active.")


def main() -> None:
    settings = parse_settings()
    app = Application.builder().token(settings.token).build()
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("symbols", symbols))
    app.add_handler(CommandHandler("signal", signal_cmd))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("autoscan_on", autoscan_on))
    app.add_handler(CommandHandler("autoscan_off", autoscan_off))

    LOG.info("Bot starting Yahoo syms=%s Stockity syms=%s", settings.symbols, settings.stockity_symbols)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
