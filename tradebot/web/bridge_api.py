"""Bridge API — MT5 EA signal polling via FastAPI routes.

Provides the same endpoints as the standalone vilona_bridge HTTP server
but integrated into the main FastAPI app on port 9090.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from tradebot.config import settings

LOG = logging.getLogger("tradebot.web.bridge_api")

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


def _check_bridge_admin(request: Request) -> None:
    """Check admin access for bridge management endpoints."""
    from tradebot.bots.handlers import _default_admin_check

    user_id = request.headers.get("X-Admin-ID", "")
    if not _default_admin_check(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")

# ── Shared state (matches vilona_bridge.py) ──
HISTORY: deque[dict[str, Any]] = deque(maxlen=500)
PENDING: deque[dict[str, Any]] = deque(maxlen=100)
PENDING_BY_KEY: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
PENDING_BY_INSTANCE: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
LOCK = Lock()
ID_COUNTER = 0
ACKED: set[str] = set()
ACKED_BY_KEY: dict[str, set[str]] = defaultdict(set)
START_TIME = time.time()

INSTANCES: dict[str, dict[str, Any]] = {}
RATE_COUNTERS: dict[str, list[float]] = defaultdict(list)


def _default_keys_path() -> Path:
    return Path(settings.DATA_DIR) / "api_keys.json"


def _load_keys() -> dict[str, Any]:
    p = _default_keys_path()
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {"keys": {}, "tiers": {}, "default_tier": "starter"}


def _gen_id() -> str:
    global ID_COUNTER
    ID_COUNTER += 1
    return f"vtfx_{int(time.time() * 1000)}_{ID_COUNTER}"


def _validate_key(api_key: str) -> tuple[bool, dict[str, Any] | None]:
    if not api_key:
        return False, None
    config = _load_keys()
    key_data = config["keys"].get(api_key)
    if not key_data or not key_data.get("active"):
        return False, None
    return True, config["tiers"].get(key_data.get("tier", ""), {})


def _check_rate_limit(api_key: str) -> bool:
    config = _load_keys()
    key_data = config["keys"].get(api_key, {})
    limit = key_data.get("rate_limit", 3)
    if limit == 0:
        return True
    window = key_data.get("rate_window_seconds", 86400)
    now = time.time()
    timestamps = RATE_COUNTERS[api_key]
    timestamps[:] = [t for t in timestamps if now - t < window]
    if len(timestamps) >= limit:
        return False
    timestamps.append(now)
    return True


STOCKITY_REFERRAL = "7b8730c84b6450e3e0b02fd3fd864f69"
MASTER_INSTANCES: dict[str, dict[str, str]] = defaultdict(dict)


@router.get("/health")
async def bridge_health():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "queue_size": len(PENDING),
    }


@router.get("/status")
async def bridge_status():
    with LOCK:
        return {
            "pending": len(PENDING) > 0,
            "pending_id": PENDING[0]["signal_id"] if PENDING else None,
            "history_count": len(HISTORY),
            "last_signal_id": HISTORY[-1]["signal_id"] if HISTORY else None,
        }


@router.get("/signal")
async def bridge_poll_signal(
    api_key: str = Query(""),
    account_id: str = Query(None),
):
    is_valid, tier = _validate_key(api_key)
    if not is_valid:
        return JSONResponse({"error": "invalid_api_key", "action": "HOLD", "pending": False}, status_code=401)
    if not _check_rate_limit(api_key):
        return JSONResponse({"error": "rate_limited", "action": "HOLD", "pending": False}, status_code=429)

    with LOCK:
        if account_id:
            instance_id = f"{api_key}:{account_id}"
            if instance_id in PENDING_BY_INSTANCE and PENDING_BY_INSTANCE[instance_id]:
                sig = PENDING_BY_INSTANCE[instance_id].popleft()
                return _format_signal(sig, api_key)
        if api_key in PENDING_BY_KEY and PENDING_BY_KEY[api_key]:
            sig = PENDING_BY_KEY[api_key].popleft()
            return _format_signal(sig, api_key)
        if PENDING:
            sig = PENDING.popleft()
            return _format_signal(sig, api_key)

    return _empty_signal()


@router.post("/signal")
async def bridge_post_signal(request: Request):
    data = await request.json()
    api_key = request.query_params.get("api_key", "")

    sig_id = _gen_id()
    signal: dict[str, Any] = {
        "signal_id": sig_id,
        "symbol": data.get("symbol", "XAUUSD"),
        "action": data.get("action", "HOLD"),
        "entry": data.get("entry"),
        "sl": data.get("sl"),
        "tp": data.get("tp"),
        "tp1": data.get("tp1", data.get("tp")),
        "tp2": data.get("tp2"),
        "risk_percent": data.get("risk_percent", 1.0),
        "confidence": data.get("confidence"),
        "comment": data.get("comment", "VTFX/AI"),
        "source": data.get("source", "vtfx"),
        "timestamp": data.get("timestamp"),
        "received_at": time.time(),
        "status": "pending",
        "layers": data.get("layers", []),
        "target_user": data.get("target_user"),
    }

    broadcast_count = _broadcast_signal(signal, api_key)
    with LOCK:
        HISTORY.append(signal)

    return {
        "signal_id": sig_id,
        "status": "queued",
        "broadcast_count": broadcast_count,
    }


@router.get("/ack/{signal_id}")
async def bridge_ack(request: Request, signal_id: str, api_key: str = Query("")):
    _check_auth(request)
    with LOCK:
        ACKED.add(signal_id)
        if api_key:
            ACKED_BY_KEY[api_key].add(signal_id)
    return {"status": "ok", "signal_id": signal_id}


@router.get("/history")
async def bridge_history(request: Request):
    _check_auth(request)
    with LOCK:
        return {"count": len(HISTORY), "signals": list(HISTORY)}


@router.get("/accounts")
async def bridge_accounts(request: Request):
    _check_auth(request)
    with LOCK:
        now = time.time()
        active = {k: v for k, v in INSTANCES.items() if now - v.get("last_seen", 0) < 3600}
        return {
            "total": len(INSTANCES),
            "active": len(active),
            "instances": {
                k: {"last_seen": v.get("last_seen"), "label": v.get("label", k)}
                for k, v in INSTANCES.items()
            },
        }


@router.get("/keys")
async def bridge_keys():
    config = _load_keys()
    keys_safe = {
        k: {
            "tier": v["tier"],
            "label": v.get("label", ""),
            "rate_limit": v.get("rate_limit", "?"),
            "active": v["active"],
        }
        for k, v in config["keys"].items()
    }
    return {"keys": keys_safe, "tiers": config["tiers"]}


def _format_signal(sig: dict[str, Any], api_key: str) -> dict[str, Any]:
    config = _load_keys()
    key_data = config["keys"].get(api_key, {})
    tier_name = key_data.get("tier", "starter")
    tier_info = config["tiers"].get(tier_name, {})
    max_layers = tier_info.get("max_layers", 1)

    layers = sig.get("layers", [])
    if layers and len(layers) > max_layers:
        layers = layers[:max_layers]

    return {
        "signal_id": sig.get("signal_id", ""),
        "symbol": sig.get("symbol", ""),
        "action": sig.get("action", "HOLD"),
        "entry": sig.get("entry", 0),
        "sl": sig.get("sl", 0),
        "tp": sig.get("tp", 0),
        "tp1": sig.get("tp1", sig.get("tp", 0)),
        "tp2": sig.get("tp2", 0),
        "tp3": sig.get("tp3", 0),
        "tp4": sig.get("tp4", 0),
        "risk_percent": sig.get("risk_percent", 1.0),
        "comment": sig.get("comment", "VTFX/AI"),
        "confidence": sig.get("confidence", 0),
        "layers": layers,
        "layer_count": len(layers),
        "tier": tier_name,
        "pending": True,
    }


def _empty_signal() -> dict[str, Any]:
    return {
        "signal_id": "", "symbol": "", "action": "HOLD",
        "entry": 0, "sl": 0, "tp": 0,
        "tp1": 0, "tp2": 0, "tp3": 0, "tp4": 0,
        "risk_percent": 0, "comment": "", "confidence": 0,
        "layers": [], "layer_count": 0, "tier": "", "pending": False,
    }


def _broadcast_signal(signal: dict[str, Any], api_key: str) -> int:
    target_user = signal.get("target_user", "")
    if target_user and api_key:
        instance_key = f"{api_key}:{target_user}"
        if instance_key in INSTANCES:
            PENDING_BY_INSTANCE[instance_key].append(signal)
            return 1
        PENDING_BY_KEY[api_key].append(signal)
        return 1
    if api_key:
        PENDING_BY_KEY[api_key].append(signal)
        if api_key in MASTER_INSTANCES:
            for acc_id in list(MASTER_INSTANCES[api_key].keys()):
                inst_key = f"{api_key}:{acc_id}"
                if inst_key in INSTANCES:
                    PENDING_BY_INSTANCE[inst_key].append(signal)
            return len(MASTER_INSTANCES.get(api_key, {})) + 1
        return 1
    PENDING.append(signal)
    return 0
