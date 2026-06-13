"""Chart image generation for Telegram signal posts.

Uses quickchart.io for programmatic chart images with:
  - Entry, SL, TP overlay
  - Grade-colored accents

Output: PNG 800x420, dark background #0a0a14
"""
import io
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

QUICKCHART_API_KEY = "283oPdK3mka8WPbcJnWPcawWM40N7ZkZaaxYb0LB"


def _chart_spec(
    symbol: str,
    timeframe: str = "M15",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    confidence: float = 0.65,
    grade: str = "B",
) -> dict[str, Any]:
    color = {"BUY": "#22c55e", "SELL": "#ef4444", "CALL": "#22c55e", "PUT": "#ef4444"}.get(trend.upper(), "#3b82f6")
    grade_color = {"A": "#ff0055", "B": "#f59e0b", "C": "#94a3b8"}.get(grade.upper(), "#94a3b8")

    return {
        "type": "bar",
        "data": {
            "labels": ["-5", "-4", "-3", "-2", "-1", "ENTRY", "TP1", "TP2"],
            "datasets": [
                {
                    "label": f"{symbol}",
                    "data": [0, 0, 0, 0, 0, entry, tp1, tp2],
                    "borderColor": color,
                    "backgroundColor": f"{color}22",
                    "borderWidth": 2,
                    "pointRadius": [0, 0, 0, 0, 0, 9, 7, 5],
                    "pointBackgroundColor": ["#ffffff", "#ffffff", "#ffffff", "#ffffff", "#ffffff", grade_color, color, color],
                },
                {
                    "label": "SL",
                    "data": [0, 0, 0, 0, 0, sl, 0, 0],
                    "borderColor": "#ef4444",
                    "borderWidth": 2,
                    "borderDash": [5, 5],
                    "pointRadius": [0, 0, 0, 0, 0, 7, 0, 0],
                    "pointBackgroundColor": "#ef4444",
                    "showLine": True,
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
                    "text": f"{symbol} • {timeframe} • {trend} • Grade {grade} • Conf {confidence*100:.0f}%",
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
    timeframe: str = "M15",
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
    if not QUICKCHART_API_KEY:
        return ""
    spec = _chart_spec(
        symbol=symbol, timeframe=timeframe, trend=trend,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2,
        confidence=confidence, grade=grade,
    )
    encoded = json.dumps(spec, separators=(",", ":"))
    return (
        f"https://quickchart.io/chart?key={QUICKCHART_API_KEY}"
        f"&c={urllib.parse.quote(encoded)}"
        f"&width={width}&height={height}"
        f"&format=png&backgroundColor=0a0a14"
    )


def fetch_chart_image(
    symbol: str,
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    timeframe: str = "M15",
    confidence: float = 0.65,
    grade: str = "B",
) -> Optional[bytes]:
    url = build_chart_url(
        symbol=symbol, trend=trend, entry=entry, sl=sl,
        tp1=tp1, tp2=tp2, timeframe=timeframe,
        confidence=confidence, grade=grade,
    )
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VilonaBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read()
    except Exception as exc:
        logger.warning("Chart fetch failed for %s: %s", symbol, exc)
        return None
