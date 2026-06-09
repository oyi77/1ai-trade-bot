"""
DeriveEngine — Deriv signal integration for engine_consensus.py
===============================================================
Reads signals from deriv_signal_bridge (HTTP at localhost:8082)
and produces SignalGrade-compatible output matching the standard
engine format: {direction, confidence, details}.

Can be imported directly by engine_consensus or used standalone.
"""
from __future__ import annotations
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Optional

# ── Path setup (so we can import sibling modules from scripts/) ──
import sys as _sys
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/deriv/
_PARENT = os.path.dirname(_SCRIPT_DIR)                      # scripts/
for _p in (_PARENT,):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from smc_scalper_engine import SignalGrade

LOG = logging.getLogger("deriv.engine")

# ── Connection defaults ──
_DERIV_BRIDGE_HOST = os.environ.get("DERIV_BRIDGE_HOST", "127.0.0.1")
_DERIV_BRIDGE_PORT = int(os.environ.get("DERIV_BRIDGE_PORT", "8082"))
_DERIV_SIGNAL_URL = f"http://{_DERIV_BRIDGE_HOST}:{_DERIV_BRIDGE_PORT}/signal"
_DERIV_STATUS_URL = f"http://{_DERIV_BRIDGE_HOST}:{_DERIV_BRIDGE_PORT}/status"

# TTL for bridge response cache (seconds)
_CACHE_TTL = 10  # Deriv ticks arrive frequently; keep fresh
_cache: dict[str, tuple[float, dict]] = {}  # key -> (timestamp, result)


def _bridge_is_alive() -> bool:
    """Quick health check — does the bridge respond?"""
    try:
        req = urllib.request.Request(_DERIV_STATUS_URL, headers={"User-Agent": "DeriveEngine/1.0"})
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read())
            return data.get("connected", False)
    except Exception:
        return False


def _fetch_signal_from_bridge() -> Optional[dict]:
    """Fetch the latest signal from the Deriv bridge HTTP endpoint.

    Returns the raw signal dict or None on failure.
    """
    # Check cache
    now = time.time()
    cached = _cache.get("signal")
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        req = urllib.request.Request(_DERIV_SIGNAL_URL, headers={"User-Agent": "DeriveEngine/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            payload = json.loads(r.read())

        bridge_signal = payload.get("signal") if isinstance(payload, dict) else None
        if not bridge_signal:
            LOG.debug("Bridge returned empty signal")
            return None

        # Cache it
        _cache["signal"] = (now, bridge_signal)
        return bridge_signal

    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError) as e:
        LOG.debug("Bridge fetch failed: %s", e)
        return None
    except json.JSONDecodeError as e:
        LOG.debug("Bridge returned invalid JSON: %s", e)
        return None


def _deriv_direction_to_std(direction: str) -> str:
    """Map Deriv CALL/PUT/NEUTRAL → consensus BUY/SELL/HOLD."""
    d = direction.upper()
    if d == "CALL":
        return "BUY"
    elif d == "PUT":
        return "SELL"
    return "HOLD"


def _confidence_to_signal_grade(confidence: float) -> SignalGrade:
    """Map a 0-100 confidence value to a SignalGrade enum."""
    if confidence >= 85:
        return SignalGrade.SANGAT_KUAT
    elif confidence >= 65:
        return SignalGrade.KUAT
    elif confidence >= 45:
        return SignalGrade.BAGUS
    elif confidence >= 25:
        return SignalGrade.CUKUP
    return SignalGrade.LEMAH


# ═══════════════════════════════════════════════════════════════════
# DeriveEngine — main class
# ═══════════════════════════════════════════════════════════════════

class DeriveEngine:
    """Deriv-based engine that reads signals from the bridge and produces
    consensus-compatible output.

    Usage (by engine_consensus.py):
        engine = DeriveEngine()
        result = engine.analyze(symbol="R_75")
        # → {"direction": "BUY"/"SELL"/"HOLD", "confidence": 0-1, "details": str}
    """

    def __init__(self, bridge_url: str = _DERIV_SIGNAL_URL):
        self.bridge_url = bridge_url
        self._last_signal_raw: Optional[dict] = None

    def analyze(self, symbol: str = "") -> dict:
        """Run analysis: fetch latest Deriv signal and return standard engine dict.

        Returns:
            {
                "direction": "BUY" | "SELL" | "HOLD" | "ERROR",
                "confidence": float (0.0-1.0),
                "details": str (human-readable explanation),
                "grade": SignalGrade or None,
            }
        """
        raw = _fetch_signal_from_bridge()
        if raw is None:
            return {
                "direction": "HOLD",
                "confidence": 0.0,
                "details": "Deriv bridge unreachable or no signal",
                "grade": None,
            }

        self._last_signal_raw = raw

        bridge_dir = raw.get("direction", "NEUTRAL")
        bridge_conf = raw.get("confidence", 0.0)
        bridge_price = raw.get("entry_price", 0.0)
        bridge_symbol = raw.get("symbol", "unknown")
        momen_conf = raw.get("momen_confidence")
        ticks_analyzed = raw.get("ticks_analyzed", 0)

        # Normalize confidence from 0-100 to 0-1 range
        confidence_01 = min(max(float(bridge_conf) / 100.0, 0.0), 1.0)

        direction = _deriv_direction_to_std(bridge_dir)
        grade = _confidence_to_signal_grade(float(bridge_conf))

        # Build details string
        parts = [f"Deriv/{bridge_symbol}", f"@{bridge_price}"]
        if momen_conf is not None:
            parts.append(f"Momen:{momen_conf}%")
        if ticks_analyzed:
            parts.append(f"ticks:{ticks_analyzed}")
        parts.append(f"grade:{grade.label_id}")

        return {
            "direction": direction,
            "confidence": round(confidence_01, 4),
            "details": " | ".join(parts),
            "grade": grade,
        }

    @property
    def last_raw_signal(self) -> Optional[dict]:
        """Return the last raw signal fetched from the bridge."""
        return self._last_signal_raw

    @property
    def is_bridge_connected(self) -> bool:
        """Check if the bridge is reachable and connected to Deriv."""
        return _bridge_is_alive()


# ── Convenience function for engine_consensus lazy-load ──

def analyze_deriv(symbol: str = "") -> dict:
    """Standalone convenience function — same signature pattern as other engines."""
    engine = DeriveEngine()
    return engine.analyze(symbol)
