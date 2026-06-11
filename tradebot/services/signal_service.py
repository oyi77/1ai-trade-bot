"""Signal feed service — unified signal storage and retrieval.

Absorbed from scripts/signal_feed.py so the tradebot package does not
depend on scripts/ for its data layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
FEED_FILE = DATA_DIR / "signal_feed.json"
FEED_FILE.parent.mkdir(parents=True, exist_ok=True)

MAX_FEED_ENTRIES = 500


def _load_feed() -> dict[str, Any]:
    try:
        if FEED_FILE.exists():
            raw = json.loads(FEED_FILE.read_text())
            if isinstance(raw, list):
                return {"signals": raw, "stats": {
                    "total": len(raw), "tp": 0, "sl": 0, "pending": 0,
                }}
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {"signals": [], "stats": {"total": 0, "tp": 0, "sl": 0, "pending": 0}}


def _save_feed(data: dict[str, Any]) -> None:
    try:
        if len(data.get("signals", [])) > MAX_FEED_ENTRIES:
            data["signals"] = data["signals"][-MAX_FEED_ENTRIES:]
        FEED_FILE.write_text(json.dumps(data, indent=2, default=str))
    except Exception:
        pass


def add_signal(
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    confidence: float = 0,
    rr_ratio: str | float = "?",
    engines: dict | None = None,
    source: str = "channel-auto",
    source_user: str = "",
    price: float = 0,
    grade: str = "",
    **kwargs: Any,
) -> str:
    now = datetime.now(WIB)
    signal_id = hashlib.md5(
        f"{symbol}|{direction}|{entry}|{now.isoformat()}".encode()
    ).hexdigest()[:12]

    entry_data: dict[str, Any] = {
        "id": signal_id,
        "symbol": symbol.upper(),
        "direction": direction,
        "entry": round(float(entry), 2),
        "sl": round(float(sl), 2),
        "tp": round(float(tp), 2),
        "confidence": round(float(confidence), 2),
        "rr_ratio": str(rr_ratio),
        "grade": grade,
        "engines": engines or {},
        "source": source,
        "source_user": source_user,
        "price_at_signal": round(float(price), 2),
        "timestamp": now.isoformat(),
        "status": "pending",
        "outcome_pips": 0,
        "outcome_time": None,
        **kwargs,
    }

    feed = _load_feed()
    feed["signals"].append(entry_data)
    feed["stats"]["total"] = feed["stats"].get("total", 0) + 1
    feed["stats"]["pending"] = feed["stats"].get("pending", 0) + 1

    if len(feed["signals"]) > MAX_FEED_ENTRIES:
        feed["signals"] = feed["signals"][-MAX_FEED_ENTRIES:]

    _save_feed(feed)
    return signal_id


def update_outcome(symbol: str, entry_price: float, result: str, pips: float) -> None:
    feed = _load_feed()
    updated = False
    sym = symbol.upper()
    if sym in ("XAUUSD", "GOLD"):
        threshold = 5.0
    elif sym in ("BTCUSD", "ETHUSD"):
        threshold = 200.0
    elif sym in ("USOIL", "OIL"):
        threshold = 0.3
    elif sym.endswith("JPY"):
        threshold = 0.5
    else:
        threshold = 0.005
    for sig in feed["signals"]:
        if (
            sig["symbol"] == symbol.upper()
            and abs(sig["entry"] - entry_price) < threshold
            and sig["status"] == "pending"
        ):
            sig["status"] = result.lower()
            sig["outcome_pips"] = round(float(pips), 1)
            sig["outcome_time"] = datetime.now(WIB).isoformat()
            if result.upper() == "TP":
                feed["stats"]["tp"] = feed["stats"].get("tp", 0) + 1
            else:
                feed["stats"]["sl"] = feed["stats"].get("sl", 0) + 1
            feed["stats"]["pending"] = max(0, feed["stats"].get("pending", 1) - 1)
            updated = True
            break
    if updated:
        _save_feed(feed)


def get_recent_signals(limit: int = 20) -> list[dict[str, Any]]:
    feed = _load_feed()
    signals: list[dict[str, Any]] = feed.get("signals", [])
    return signals[-limit:]


def get_user_signals(limit: int = 20) -> list[dict[str, Any]]:
    feed = _load_feed()
    signals: list[dict[str, Any]] = feed.get("signals", [])
    user_sigs = [s for s in signals if s.get("source") == "user-generate"]
    return user_sigs[-limit:]


def get_stats() -> dict[str, Any]:
    feed = _load_feed()
    result: dict[str, Any] = feed.get("stats", {})
    return result


__all__ = [
    "add_signal",
    "get_recent_signals",
    "get_stats",
    "get_user_signals",
    "update_outcome",
]
