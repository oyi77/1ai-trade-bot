"""Chart image generation for Telegram signal posts.

Current default provider: chart-img.com (`CHART_IMG_KEY` env). Selected provider URL is generated here; Telegram bot upload is handled by vilona_tradefx_handler.py/tg_send_photo.
"""
from __future__ import annotations

import io
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _chart_spec(
    symbol: str,
    timeframe: str = "15",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    confidence: float = 0.65,
    grade: str = "B",
) -> dict[str, object]:
    color = {
        "BUY": "#22c55e",
        "SELL": "#ef4444",
        "CALL": "#22c55e",
        "PUT": "#ef4444",
    }.get(str(trend).upper(), "#3b82f6")
    grade_color = {
        "A": "#ff0055",
        "B": "#f59e0b",
        "C": "#94a3b8",
    }.get(str(grade).upper(), "#94a3b8")
    return {
        "type": "candlestick",
        "data": {
            "labels": [],
            "datasets": [
                {
                    "label": f"{symbol}",
                    "data": [0] * 25,
                    "borderColor": color,
                    "backgroundColor": f"{color}33",
                },
                {
                    "label": "SL",
                    "data": [0] * 25,
                    "borderColor": "#ef4444",
                    "showLine": True,
                    "fill": False,
                },
            ],
        },
        "options": {
            "responsive": False,
            "maintainAspectRatio": False,
            "plugins": {
                "legend": {"display": False},
                "title": {
                    "display": True,
                    "text": f"{symbol} • {timeframe} • {trend} • Grade {grade} • Conf {confidence * 100:.0f}%",
                    "color": "#e2e8f0",
                    "font": {"size": 13, "family": "Inter", "weight": "bold"},
                },
            },
            "scales": {
                "x": {"ticks": {"color": "#64748b"}, "grid": {"color": "rgba(255,255,255,0.05)"}},
                "y": {"ticks": {"color": "#64748b"}, "grid": {"color": "rgba(255,255,255,0.08)"}},
            },
        },
    }


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
    height: int = 420,
) -> str:
    key = os.environ.get("CHART_IMG_KEY", "")
    if not key or not symbol:
        return ""
    chart_symbol = f"BINANCE:{symbol}" if "/" not in symbol.upper() and symbol.upper().endswith("USD") else symbol
    studies = "RSI14,MACD"
    return (
        f"https://chart-img.com/api/v1/chart?symbol={chart_symbol}"
        f"&interval={timeframe}&theme=dark&width={width}&height={height}"
        f"&studies={studies}&key={key}"
    )


def fetch_chart_image(
    symbol: str,
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    timeframe: str = "15",
    confidence: float = 0.65,
    grade: str = "B",
) -> Optional[bytes]:
    url = build_chart_url(
        symbol=symbol,
        trend=trend,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        timeframe=timeframe,
        confidence=confidence,
        grade=grade,
    )
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VilonaBot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            content_type = r.headers.get("Content-Type", "")
            if "image" not in content_type:
                body = r.read()
                logger.warning("Chart provider returned non-image for %s: %s", symbol, url)
                return None
            return r.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chart fetch failed for %s: %s", symbol, exc)
        return None
