"""Shared API key pool for chart-img.com — used by ALL trading bots.

Central config: ~/projects/1ai-trade-bot/config/chart_img_pool.json

Usage:
    from scripts.chart_img_pool import get_chart_image
    png_bytes = get_chart_image(
        symbol="BINANCE:BTCUSDT",
        timeframe="15m",
        trend="BUY",
        entry=63750, sl=63100, tp1=64500, tp2=65200,
    )

Each bot (Vilona Tradefx, IDX, etc.) imports this module.
Keys rotate automatically. Counters reset at midnight UTC.
Fallback returns None → caller should use mplfinance or skip chart.

FREE plan: max 3 studies per request (NO drawings — paid feature only).
Studies used: RSI (1). Caller overlays Entry/SL/TP lines via Pillow.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "chart_img_pool.json"
API_URL = "https://api.chart-img.com/v2/tradingview/advanced-chart"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_key() -> str:
    return _now_utc().strftime("%Y-%m-%d")


def load_pool() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"keys": [], "reset_hour_utc": 0}


def save_pool(pool: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(pool, indent=2))


def _maybe_reset_counters(pool: dict) -> bool:
    today = _today_key()
    changed = False
    for entry in pool["keys"]:
        if entry.get("date") != today:
            entry["used_today"] = 0
            entry["date"] = today
            changed = True
    return changed


def pick_key(pool: dict) -> Optional[dict]:
    _maybe_reset_counters(pool)
    limit = pool.get("daily_limit", 50)
    available = [k for k in pool["keys"] if k.get("used_today", 0) < limit]
    if not available:
        return None
    available.sort(key=lambda k: k.get("used_today", 0))
    return available[0]


def increment_usage(pool: dict, key_value: str) -> None:
    for entry in pool["keys"]:
        if entry["key"] == key_value:
            entry["used_today"] = entry.get("used_today", 0) + 1
            entry["last_used"] = _now_utc().isoformat()
            break
    save_pool(pool)


def fetch_chart_bytes(
    symbol: str,
    timeframe: str = "15m",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    width: int = 800,
    height: int = 600,
) -> Optional[bytes]:
    pool = load_pool()
    key_entry = pick_key(pool)
    if not key_entry:
        logger.warning("chart_img_pool: all keys exhausted for today")
        return None

    api_key = key_entry["key"]

    payload = {
        "symbol": symbol,
        "interval": timeframe,
        "theme": "dark",
        "width": width,
        "height": height,
        "studies": [
            {"name": "Relative Strength Index", "override": {"showLastValue": False}},
        ],
    }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if b"\x89PNG" in data[:8] or "image" in resp.headers.get("Content-Type", ""):
                increment_usage(pool, api_key)
                logger.info(
                    "chart-img.com OK: %s %s → %d bytes (key %d/%d today)",
                    symbol, timeframe, len(data),
                    key_entry.get("used_today", 0) + 1,
                    pool.get("daily_limit", 50),
                )
                return data
            return None
    except Exception as exc:
        logger.warning("chart-img.com failed for %s: %s", symbol, exc)
        return None


def get_chart_image(
    symbol: str,
    timeframe: str = "15m",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    width: int = 800,
    height: int = 600,
) -> Optional[bytes]:
    return fetch_chart_bytes(
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


def status() -> dict:
    pool = load_pool()
    _maybe_reset_counters(pool)
    today = _today_key()
    limit = pool.get("daily_limit", 50)
    total_keys = len(pool["keys"])
    total_used = sum(k.get("used_today", 0) for k in pool["keys"])
    total_capacity = total_keys * limit
    return {
        "date": today,
        "total_keys": total_keys,
        "daily_limit_per_key": limit,
        "total_used_today": total_used,
        "total_capacity": total_capacity,
        "remaining": total_capacity - total_used,
        "keys": [
            {
                "label": f"key_{i+1}",
                "used": k.get("used_today", 0),
                "limit": limit,
                "last_used": k.get("last_used"),
            }
            for i, k in enumerate(pool["keys"])
        ],
    }
