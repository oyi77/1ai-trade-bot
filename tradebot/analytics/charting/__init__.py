"""Charting module — local candlestick charts via yfinance + matplotlib.

No external API dependencies. No API keys required.
"""
from __future__ import annotations

from tradebot.analytics.charting.local import (
    fetch_ohlcv,
    generate_signal_chart,
    overlay_signal_lines,
    render_candlestick_chart,
)

__all__ = [
    "fetch_ohlcv",
    "generate_signal_chart",
    "overlay_signal_lines",
    "render_candlestick_chart",
]