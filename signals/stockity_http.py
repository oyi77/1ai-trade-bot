"""
Stockity HTTP connector — fetches candle data via REST API instead of WebSocket.
Much simpler and more reliable than the Phoenix WS approach.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from core import Candle, Signal
from core.indicators import score_trend, classify_signal

LOG = logging.getLogger("signals.stockity_http")

CANDLE_API = "https://api.stockity.com/candles/v1/{ric}/{time}/1"

# RIC mapping: our symbol → Stockity RIC
# NOTE: Stockity hanya punya 1 index (Z-CRY/IDX).
# Semua nama lain adalah alias — kita cuma kirim 1x aja.
RIC_MAP: dict[str, str] = {
    "CRYPTO_IDX": "Z-CRY/IDX",
}

PLATFORM_ASSETS: set[str] = {"CRYPTO_IDX"}


def _load_auth() -> tuple[str, str]:
    """Load auth from .env — returns (authtoken, full_cookie)."""
    import os
    auth = os.environ.get("STOCKITY_AUTHTOKEN", "")
    cookie = os.environ.get("STOCKITY_FULL_COOKIE", "")
    if not auth or not cookie:
        env_paths = [
            Path(__file__).resolve().parent.parent / "archive" / "old-bots" / "subscription-bot" / ".env",
            Path(__file__).resolve().parent.parent / "archive" / "old-bots" / "stockity-bot" / ".env",
        ]
        for p in env_paths:
            if p.exists():
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("STOCKITY_AUTHTOKEN="):
                        auth = line.split("=", 1)[1].strip()
                    elif line.startswith("STOCKITY_FULL_COOKIE="):
                        if not cookie:
                            cookie = line.split("=", 1)[1].strip()
    return auth, cookie

async def get_candles(
    ric: str,
    minutes_back: int = 15,
    cookie: str = "",
    authtoken: str = "",
) -> list[Candle]:
    """
    Fetch 1-second candles from Stockity HTTP API.
    Returns candles sorted by time ascending.
    """
    if not cookie and not authtoken:
        authtoken, cookie = _load_auth()
    if not cookie and not authtoken:
        LOG.warning("No STOCKITY_AUTHTOKEN or STOCKITY_FULL_COOKIE available")
        return []

    now = datetime.now(timezone.utc)
    # Round to nearest 15-minute boundary and go back for more data
    # API only serves data at 15-min boundaries (**:00, :15, :30, :45)
    bucket_15 = (now.minute // 15) * 15
    # Go back enough 15-min blocks to get 15 minutes of data
    blocks_back = max(1, minutes_back // 15)
    target_minute = bucket_15 - (blocks_back * 15)
    # Handle negative minutes (e.g. 0 - 15 = -15) by rolling back an hour
    target_hour = now.hour
    if target_minute < 0:
        target_minute += 60
        target_hour -= 1
    # Handle negative hours (midnight rollover)
    if target_hour < 0:
        target_hour += 24
    time_str = now.replace(
        hour=target_hour, minute=target_minute, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:00")

    encoded_ric = urllib.parse.quote(ric, safe="")
    url = CANDLE_API.format(ric=encoded_ric, time=time_str)

    headers = {
        "Origin": "https://stockity.com",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    if cookie:
        headers["Cookie"] = cookie
    elif authtoken:
        headers["Authorization"] = f"Bearer {authtoken}"

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        LOG.warning("HTTP candle fetch failed for %s: %s", ric, exc)
        return []

    raw_candles = data.get("data", [])
    if not raw_candles:
        LOG.warning("No candle data for %s (empty response)", ric)
        return []

    out = []
    for item in raw_candles:
        try:
            ts_str = item.get("created_at", "")
            if ts_str:
                # Parse ISO time like "2026-06-08T11:30:00.000000Z"
                ts = int(
                    datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    * 1000
                )
            else:
                ts = int(now.timestamp() * 1000)

            out.append(
                Candle(
                    timestamp=ts,
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=0.0,
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            LOG.debug("Skipping bad candle: %s — %s", item, exc)

    return sorted(out, key=lambda c: c.timestamp)


def aggregate_candles(
    raw: list[Candle], period_s: int = 60
) -> list[Candle]:
    """
    Aggregate 1-second candles into larger timeframes.
    period_s=60 → 1-minute candles
    """
    if not raw:
        return []

    # Group by period bucket
    buckets: dict[int, list[Candle]] = {}
    for c in raw:
        bucket = (c.timestamp // (period_s * 1000)) * (period_s * 1000)
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append(c)

    result = []
    for ts in sorted(buckets.keys()):
        group = buckets[ts]
        result.append(
            Candle(
                timestamp=ts,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=0.0,
            )
        )
    return result


async def generate(
    symbol: str,
    cookie: str = "",
    authtoken: str = "",
) -> Optional[Signal]:
    """
    Generate a CALL/PUT signal for a Stockity platform asset.
    Uses HTTP candle API instead of WebSocket.
    
    Auth: either 'cookie' (full cookie string), 'authtoken' (Bearer token),
    or let it load from .env automatically.
    """
    sym_upper = symbol.upper()

    # Map symbol to RIC
    if sym_upper not in RIC_MAP:
        LOG.info("Unknown Stockity symbol: %s (not in RIC_MAP)", symbol)
        return None

    ric = RIC_MAP[sym_upper]
    if not cookie and not authtoken:
        authtoken, cookie = _load_auth()
    if not cookie and not authtoken:
        LOG.warning("No auth available for Stockity HTTP")
        return Signal(
            symbol=symbol,
            action="WAIT",
            confidence=0,
            price=0.0,
            reason="No auth (authtoken or cookie) — run stockity_login.py first",
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            source="stockity",
        )

    # Fetch 1-second candles for last 15 minutes
    raw = await get_candles(ric, minutes_back=15, cookie=cookie, authtoken=authtoken)
    if not raw or len(raw) < 30:
        LOG.warning("Not enough 1s candle data for %s: %d candles", ric, len(raw))
        return None

    LOG.info(
        "Got %d raw 1s candles for %s (%s)",
        len(raw),
        symbol,
        ric,
    )

    # Aggregate into 1-minute candles
    candles = aggregate_candles(raw, period_s=60)
    LOG.info("Aggregated into %d 1m candles", len(candles))

    if len(candles) < 2:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    price = closes[-1]

    # Use binary mode (optimized for short-term)
    score, reasons = score_trend(closes, highs, lows, mode="binary")
    sig = classify_signal(score, price, reasons, symbol, source="stockity")
    
    LOG.info(
        "Signal for %s: %s %d%% (score=%d, %s)",
        symbol,
        sig.action,
        sig.confidence,
        score,
        "; ".join(reasons[:3]),
    )
    return sig
