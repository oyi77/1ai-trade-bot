"""
Unified signal resolution — tries Stockity first for platform assets,
then Binance for crypto, forex-api for FX, Yahoo as universal fallback.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from core import Signal
from core.indicators import classify_signal
from signals import binance, forex, yahoo
from signals import stockity as stockity_sig

LOG = logging.getLogger("signals.resolver")

# Assets that should try Stockity first
PLATFORM_ASSETS = {"CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"}

# Crypto symbols → Binance (faster, no rate limit)
CRYPTO_SYMBOLS = {
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "DOGE-USD", "DOT-USD", "MATIC-USD", "AVAX-USD",
    "LINK-USD", "UNI-USD", "ATOM-USD", "LTC-USD", "BCH-USD",
}

# Forex symbols → forex-api (no rate limit, free)
FOREX_SYMBOLS_PREFIXES = ("EUR", "GBP", "USD", "JPY", "CHF", "AUD", "CAD", "NZD", "XAU", "XAG")


def _is_forex(symbol: str) -> bool:
    s = symbol.upper().rstrip("=X")
    return s.startswith(FOREX_SYMBOLS_PREFIXES) and "=" in symbol


async def resolve(
    symbol: str,
    interval: str = "1m",
    period: str = "2d",
    stockity_auth: str = "",
    stockity_user: str = "",
    stockity_full_cookie: str = "",
) -> Signal:
    """Resolve a signal from the best available source."""
    sym_upper = symbol.upper()

    # 1. Try Stockity first for platform assets
    if sym_upper in PLATFORM_ASSETS or sym_upper.startswith("CRYPTO"):
        if stockity_auth:
            sig = await stockity_sig.generate(sym_upper, stockity_auth, stockity_user, stockity_full_cookie)
            if sig:
                return sig
        # No auth or no data — skip Yahoo fallback (Stockity assets don't exist on Yahoo)
        LOG.info("Stockity unavailable for %s — skipping (no Yahoo data exists for platform assets)", symbol)
        return Signal(
            symbol=symbol,
            action="WAIT",
            confidence=0,
            price=0.0,
            reason="Stockity auth required — platform asset not available on public APIs",
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="stockity",
        )

    # 2. Crypto → Binance (no rate limits, fast)
    if sym_upper in CRYPTO_SYMBOLS:
        try:
            return await asyncio.to_thread(binance.generate, symbol, interval, period)
        except Exception as exc:
            LOG.warning("Binance fallback failed for %s: %s", symbol, exc)

    # 3. Forex → forex module (rate-limited Yahoo, 20s intervals)
    if _is_forex(sym_upper):
        try:
            return await asyncio.to_thread(forex.generate, symbol, interval, period)
        except Exception as exc:
            LOG.warning("Forex signal fail %s: %s", symbol, exc)

    # 4. Yahoo fallback (everything else)
    try:
        return await asyncio.to_thread(yahoo.generate, symbol, interval, period)
    except Exception as exc:
        LOG.warning("Yahoo fallback failed for %s: %s", symbol, exc)
        # Return a WAIT signal instead of crashing
        return Signal(
            symbol=symbol,
            action="WAIT",
            confidence=0,
            price=0.0,
            reason=f"No data available: {exc}",
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="yahoo",
        )


def resolve_sync(
    symbol: str,
    interval: str = "1m",
    period: str = "2d",
) -> Signal:
    """Synchronous resolution (for tools that don't use asyncio)."""
    sym_upper = symbol.upper()
    if sym_upper in CRYPTO_SYMBOLS:
        return binance.generate(symbol, interval, period)
    if _is_forex(sym_upper):
        return forex.generate(symbol, interval, period)
    return yahoo.generate(symbol, interval, period)
