"""Charting and overlays module."""

from __future__ import annotations

import logging

from tradebot.analytics.charting.overlay import overlay_signal_lines
from tradebot.analytics.charting.pool import fetch_chart_bytes
from tradebot.signals.market import MarketAggregator

logger = logging.getLogger(__name__)


async def generate_signal_chart(
    symbol: str,
    timeframe: str = "15m",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    width: int = 800,
    height: int = 600,
) -> bytes | None:
    """Generate chart bytes from chart-img.com and overlay signal lines."""

    chart_bytes = fetch_chart_bytes(
        symbol=symbol,
        timeframe=timeframe,
        trend=trend,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        width=width,
        height=height,
    )
    if not chart_bytes:
        logger.warning(f"Failed to fetch base chart for {symbol}")
        return None

    aggregator = MarketAggregator()
    try:
        ohlcv = await aggregator.fetch(symbol, interval=timeframe, count=50)
        await aggregator.close()
    except Exception as e:
        logger.warning(f"Failed to fetch market data for {symbol} chart overlay: {e}")
        ohlcv = []

    if ohlcv:
        price_high = max([candle.high for candle in ohlcv])
        price_low = min([candle.low for candle in ohlcv])
    else:
        # Fallback heuristic
        prices = [p for p in [entry, sl, tp1, tp2] if p > 0]
        if prices:
            margin = max(prices) * 0.02
            price_high = max(prices) + margin
            price_low = min(prices) - margin
        else:
            return chart_bytes

    final_bytes = overlay_signal_lines(
        chart_png_bytes=chart_bytes,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        price_high=price_high,
        price_low=price_low,
        trend=trend,
    )
    return final_bytes or chart_bytes
