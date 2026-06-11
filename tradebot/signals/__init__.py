"""
tradebot.signals — Market data source abstraction layer.

Provides async data source implementations for Binance, Yahoo Finance,
forex APIs (Frankfurter), and Stockity platform, plus a unified
MarketAggregator with multi-source fallback chain.

Usage:
    from tradebot.signals import MarketAggregator, FallbackChain
    from tradebot.signals import BinanceSource, YahooSource

    aggregator = MarketAggregator()
    ohlcv = await aggregator.fetch("BTC-USD")
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC

from .base import BaseDataSource
from .binance import BinanceSource
from .forex import ForexSource
from .market import FallbackChain, MarketAggregator
from .stockity import StockitySource
from .yahoo import YahooSource

LOG = logging.getLogger("tradebot.signals")

PLATFORM_ASSETS: set[str] = {"CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"}
CRYPTO_SYMBOLS: set[str] = {
    "BTC-USD",
    "ETH-USD",
    "BNB-USD",
    "SOL-USD",
    "XRP-USD",
    "ADA-USD",
    "DOGE-USD",
    "DOT-USD",
    "MATIC-USD",
    "AVAX-USD",
    "LINK-USD",
    "UNI-USD",
    "ATOM-USD",
    "LTC-USD",
    "BCH-USD",
}
FOREX_PREFIXES: tuple[str, ...] = (
    "EUR",
    "GBP",
    "USD",
    "JPY",
    "CHF",
    "AUD",
    "CAD",
    "NZD",
    "XAU",
    "XAG",
)


def _is_forex(symbol: str) -> bool:
    s = symbol.upper().rstrip("=X")
    return s.startswith(FOREX_PREFIXES) and "=" in symbol


async def resolve(
    symbol: str,
    interval: str = "1m",
    period: str = "2d",
    stockity_auth: str = "",
    stockity_user: str = "",
    stockity_full_cookie: str = "",
    mode: str = "turbo",
) -> Signal:  # noqa: F821
    """Resolve a signal from the best available source (legacy compatibility).

    Tries Stockity for platform assets, then Yahoo for everything else.
    Crypto/forex also fall back to Yahoo signal generation.
    """
    from datetime import datetime

    from core import Signal, _compute_expire_at

    sym_upper = symbol.upper()

    expire_at = _compute_expire_at(mode)

    if sym_upper in PLATFORM_ASSETS or sym_upper.startswith("CRYPTO"):
        if stockity_auth or stockity_full_cookie:
            try:
                from signals.stockity_http import generate as stockity_generate

                sig = await stockity_generate(
                    sym_upper,
                    cookie=stockity_full_cookie,
                    authtoken=stockity_auth,
                    mode=mode,
                )
                if sig:
                    return sig
            except Exception as exc:
                LOG.warning("Stockity signal fail for %s: %s", symbol, exc)
        LOG.info("Stockity unavailable for %s — no Yahoo alternative", symbol)
        return Signal(
            symbol=symbol,
            action="WAIT",
            confidence=0,
            price=0.0,
            reason="Stockity auth required — platform asset not on public APIs",
            timestamp_utc=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="stockity",
            expire_at=expire_at,
            mode=mode,
        )

    try:
        from signals.yahoo import generate as yahoo_generate

        sig = await asyncio.to_thread(yahoo_generate, symbol, interval, period)
        if sig:
            from core import Signal as SigCls

            return SigCls(
                symbol=sig.symbol,
                action=sig.action,
                confidence=sig.confidence,
                price=sig.price,
                reason=sig.reason,
                timestamp_utc=sig.timestamp_utc,
                source=sig.source,
                expire_at=expire_at,
                mode=mode,
            )
    except Exception as exc:
        LOG.warning("Yahoo signal fail for %s: %s", symbol, exc)

    return Signal(
        symbol=symbol,
        action="WAIT",
        confidence=0,
        price=0.0,
        reason="No data available",
        timestamp_utc=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        source="yahoo",
        expire_at=expire_at,
        mode=mode,
    )


__all__ = [
    "BaseDataSource",
    "BinanceSource",
    "ForexSource",
    "YahooSource",
    "StockitySource",
    "MarketAggregator",
    "FallbackChain",
    "resolve",
]
