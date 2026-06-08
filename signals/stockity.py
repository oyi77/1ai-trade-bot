"""
Stockity platform signal source — HTTP REST API connector.

Uses Stockity's public candle REST API instead of the Phoenix Channels WebSocket
(which requires browser-level handshake and is session-pinned server-side).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import Candle, Signal
from signals.stockity_http import generate as http_generate

LOG = logging.getLogger("signals.stockity")

STOCKITY_ASSETS = {"CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"}

# Per-scan cache
_stockity_ok: bool | None = None


async def generate(
    asset: str,
    authtoken: str = "",
    user_id: str = "",
    full_cookie: str = "",
) -> Optional[Signal]:
    """
    Generate signal for a Stockity-native asset.
    Uses HTTP REST API (reliable) instead of WebSocket (session-pinned).

    Args:
        asset: Symbol like CRYPTO_IDX, BTC_IDX, etc.
        authtoken: Stockity auth token (Bearer token)
        user_id: Stockity user ID
        full_cookie: Full cookie string (preferred — more reliable)
    """
    global _stockity_ok

    asset_upper = asset.upper()
    if asset_upper not in STOCKITY_ASSETS and not asset_upper.startswith("CRYPTO"):
        return None

    try:
        sig = await http_generate(asset_upper, cookie=full_cookie, authtoken=authtoken)
        if sig:
            if sig.action != "WAIT" and sig.confidence > 0:
                _stockity_ok = True
            return sig
    except Exception as exc:
        LOG.warning("Stockity HTTP signal fail for %s: %s", asset, exc)
        _stockity_ok = False
        return None

    return None
