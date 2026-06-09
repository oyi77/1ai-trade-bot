"""Yahoo Finance signal source for the subscription bot."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

from core import Signal

LOG = logging.getLogger("subscription_bot.signals.yahoo")

# Symbol -> Yahoo Finance ticker mapping
YAHOO_SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "SPX500": "^GSPC",
    "NDX100": "^NDX",
    "USOIL": "CL=F",
    "NGAS": "NG=F",
}


def _load_auth() -> tuple[str, str]:
    import os
    from pathlib import Path
    auth = os.environ.get("STOCKITY_AUTHTOKEN", "")
    cookie = os.environ.get("STOCKITY_FULL_COOKIE", "")
    if not auth or not cookie:
        env_paths = [
            Path(__file__).resolve().parent.parent.parent.parent / "archive" / "old-bots" / "subscription-bot" / ".env",
            Path(__file__).resolve().parent.parent.parent.parent / "archive" / "old-bots" / "stockity-bot" / ".env",
            Path(__file__).resolve().parent.parent.parent.parent / "strategies" / "vilona_tradefx" / ".env",
        ]
        for p in env_paths:
            if p.exists():
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("STOCKITY_AUTHTOKEN=") and not auth:
                        auth = line.split("=", 1)[1].strip()
                    elif line.startswith("STOCKITY_FULL_COOKIE=") and not cookie:
                        cookie = line.split("=", 1)[1].strip()
    return auth, cookie


async def generate(symbol: str) -> Optional["Signal"]:
    """Generate CALL/PUT/WAIT for a Yahoo-mapped symbol."""
    try:
        ticker = YAHOO_SYMBOLS.get(symbol.upper())
        if not ticker:
            LOG.info("Unknown Yahoo symbol: %s", symbol)
            return None
        import asyncio
        df = await asyncio.to_thread(
            yf.download,
            tickers=ticker,
            interval="1m",
            period="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if df.empty or len(df) < 20:
            return None
        import pandas as pd
        close = df["Close"].squeeze()
        if hasattr(close, "tail"):
            close = close.tail(20)
        price = float(close.iloc[-1])
        score, reasons = _score_series(close)
        sig = _classify(symbol, price, score, reasons)
        return sig
    except Exception as exc:
        LOG.warning("Yahoo signal error for %s: %s", symbol, exc)
        return None


def _score_series(close: "pd.Series") -> tuple[int, list[str]]:
    ema_fast = close.ewm(span=9, adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()
    now_fast = float(ema_fast.iloc[-1])
    now_slow = float(ema_slow.iloc[-1])
    prev_fast = float(ema_fast.iloc[-2])
    prev_slow = float(ema_slow.iloc[-2])
    score = 50
    reasons: list[str] = []
    if now_fast > now_slow:
        score += 8
        reasons.append("EMA9 above EMA21")
    else:
        score -= 8
        reasons.append("EMA9 below EMA21")
    if prev_fast <= prev_slow and now_fast > now_slow:
        score += 10
        reasons.append("bullish crossover")
    elif prev_fast >= prev_slow and now_fast < now_slow:
        score -= 10
        reasons.append("bearish crossover")
    return int(max(0, min(100, score))), reasons


def _classify(symbol: str, price: float, score: int, reasons: list[str]) -> "Signal":
    if score >= 62:
        action = "CALL"
        confidence = score
    elif score <= 38:
        action = "PUT"
        confidence = 100 - score
    else:
        action = "WAIT"
        confidence = max(score, 100 - score)
    return Signal(
        symbol=symbol,
        action=action,
        confidence=confidence,
        price=price,
        reason="; ".join(reasons if reasons else ["Neutral"]),
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        source="yahoo",
    )
