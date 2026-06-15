"""DerivEngine — Deriv binary options engine for MTF consensus.

Polls external DerivSignalBridge HTTP endpoint and produces
standard Engine output compatible with tradebot.engines.consensus.

Bridge runs as a separate process (tradebot.brokers.deriv.bridge).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from tradebot.engines.base import Engine
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger("tradebot.brokers.deriv.engine")

_DERIV_BRIDGE_HOST = "127.0.0.1"
_DERIV_BRIDGE_PORT = 8082
_DERIV_SIGNAL_URL = f"http://{_DERIV_BRIDGE_HOST}:{_DERIV_BRIDGE_PORT}/signal"
_DERIV_STATUS_URL = f"http://{_DERIV_BRIDGE_HOST}:{_DERIV_BRIDGE_PORT}/status"
_CACHE_TTL = 10
_cache: dict[str, tuple[float, dict]] = {}


def _bridge_is_alive() -> bool:
    try:
        req = urllib.request.Request(_DERIV_STATUS_URL, headers={"User-Agent": "DerivEngine/1.0"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()).get("connected", False)
    except Exception:
        return False


def _fetch_signal() -> dict | None:
    now = time.time()
    cached = _cache.get("signal")
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]
    try:
        req = urllib.request.Request(_DERIV_SIGNAL_URL, headers={"User-Agent": "DerivEngine/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            payload = json.loads(r.read())
        bridge_signal = payload.get("signal") if isinstance(payload, dict) else None
        if not bridge_signal:
            return None
        _cache["signal"] = (now, bridge_signal)
        return bridge_signal
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError, json.JSONDecodeError):
        return None


class DerivEngine(Engine):
    """Deriv binary options engine — polls external bridge for signals."""

    @property
    def name(self) -> str:
        return "Deriv"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        raw = _fetch_signal()
        if raw is None:
            return None

        bridge_dir = raw.get("direction", "NEUTRAL")
        bridge_conf = raw.get("confidence", 0.0)
        bridge_price = raw.get("entry_price", 0.0)
        bridge_symbol = raw.get("symbol", "R_75")

        confidence_01 = min(max(float(bridge_conf) / 100.0, 0.0), 1.0)

        direction: str = "BUY" if bridge_dir.upper() == "CALL" else ("SELL" if bridge_dir.upper() == "PUT" else "HOLD")
        if direction == "HOLD":
            return None

        return Signal(
            symbol=bridge_symbol,
            direction=direction,
            predicted_digit=int(raw.get("predicted_digit", 0)),
            confidence=round(confidence_01, 4),
            source=SignalSource.CONSENSUS,
            entry_price=bridge_price,
            grade=SignalGrade.STRONG if confidence_01 >= 0.7 else (
                SignalGrade.MODERATE if confidence_01 >= 0.5 else SignalGrade.WEAK
            ),
        )

    @property
    def is_bridge_connected(self) -> bool:
        return _bridge_is_alive()
