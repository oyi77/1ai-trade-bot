"""Chart image generation for Telegram signal posts.

Primary provider: chart-img.com via shared key pool
Fallback: mplfinance local generation (caller handles)

Shared key pool: ~/projects/1ai-trade-bot/config/chart_img_pool.json
Pool module:    scripts/chart_img_pool.py

All bots (Vilona Tradefx, IDX, etc.) import fetch_chart_image() from here.
The pool auto-rotates keys and tracks daily usage.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_chart_image(
    symbol: str,
    timeframe: str = "15m",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    confidence: float = 0.65,
    grade: str = "B",
    width: int = 800,
    height: int = 600,
) -> Optional[bytes]:
    try:
        from scripts.chart_img_pool import fetch_chart_bytes
        result = fetch_chart_bytes(
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
        if result:
            return result
    except ImportError:
        logger.debug("chart_img_pool not available, falling back")
    except Exception as exc:
        logger.warning("chart_img_pool failed: %s", exc)

    logger.info("No chart image for %s — caller should use mplfinance fallback", symbol)
    return None


def build_chart_url(
    symbol: str,
    timeframe: str = "15",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    confidence: float = 0.65,
    grade: str = "B",
    width: int = 800,
    height: int = 600,
) -> str:
    return ""
