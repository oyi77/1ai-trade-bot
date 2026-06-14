#!/usr/bin/env python3
"""MT5 Bridge Daemon — executes trailing SL modifications and reports status.

Runs alongside the compiled VilonaTradeFX_EA.ex5.
EA handles trade ENTRY only. This daemon handles SL MODIFICATION.

Flow:
    1. Polls bridge GET /signal?mode=trailing  →  receive SL update
    2. mt5.PositionModify()  →  modify SL on broker
    3. POST /trade-status  →  report success/failure back to bridge

Requires: MetaTrader5 terminal running on same machine with Python connection.
          pip install MetaTrader5
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

LOG = logging.getLogger("mt5_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WIB = timezone(timedelta(hours=7))

# ── Config ──────────────────────────────────────────────────────────────────

BRIDGE_URL = os.environ.get("VILONA_BRIDGE_URL", "http://localhost:8765")
API_KEY = os.environ.get("VILONA_API_KEY", "")
ACCOUNT_ID = os.environ.get("VILONA_ACCOUNT_ID", "MT5-183371455")
POLL_INTERVAL = 2  # seconds — faster than EA's 5s, so we catch trailing updates quickly
RECONCILE_INTERVAL = 300  # every 5 minutes
DAEMON_ID = f"mt5d-{os.uname().nodename}-{os.getpid()}"


def _post(path: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    """POST JSON to bridge endpoint."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        LOG.warning("POST %s → %s", path, exc)
        return {"error": str(exc)}


def _get(path: str, timeout: int = 10) -> dict[str, Any]:
    """GET from bridge endpoint."""
    req = urllib.request.Request(f"{BRIDGE_URL}{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        LOG.warning("GET %s → %s", path, exc)
        return {"error": str(exc)}


# ── MT5 Connection ──────────────────────────────────────────────────────────


def connect_mt5(login: int | None = None, password: str | None = None,
                server: str | None = None, path: str | None = None) -> bool:
    """Initialize MT5 connection. Uses creds if provided, else existing terminal."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        LOG.error("MetaTrader5 not installed: pip install MetaTrader5")
        return False

    if not mt5.initialize(
        login=login or 0,
        password=password or "",
        server=server or "",
        path=path or "",
    ):
        LOG.error("MT5 init failed: %s", mt5.last_error())
        return False

    info = mt5.account_info()
    if info is None:
        LOG.error("Failed to get account info — wrong login/server?")
        return False

    LOG.info("MT5 connected: %s | %s | balance=%.2f %s",
             info.login, info.server, info.balance, info.currency)
    return True


# ── Position Tracking ───────────────────────────────────────────────────────


def get_position(ticket: int) -> dict[str, Any] | None:
    """Get position by ticket with SL/TP/entry."""
    import MetaTrader5 as mt5
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return None
    p = pos[0]
    return {
        "ticket": p.ticket,
        "symbol": p.symbol,
        "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
        "volume": p.volume,
        "open_price": p.price_open,
        "sl": p.sl,
        "tp": p.tp,
        "current_price": p.price_current,
        "profit": p.profit,
        "comment": p.comment,
    }


def modify_sl(ticket: int, new_sl: float, new_tp: float | None = None) -> dict[str, Any]:
    """Modify SL (and optionally TP) on an open position.
    Returns {"status": "ok"|"rejected", "reason": ..., "actual_sl": ...}
    """
    import MetaTrader5 as mt5

    pos = get_position(ticket)
    if pos is None:
        return {"status": "rejected", "reason": "position_not_found", "ticket": ticket}

    tp = new_tp if new_tp is not None else pos["tp"]

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos["symbol"],
        "position": ticket,
        "sl": new_sl,
        "tp": tp,
    }

    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return {"status": "rejected", "reason": "mt5_error", "error_code": err[0],
                "error_text": err[1], "ticket": ticket}

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        LOG.info("SL modified: ticket=%d sl=%.2f → %.2f", ticket, pos["sl"], new_sl)
        return {"status": "ok", "ticket": ticket, "old_sl": pos["sl"],
                "new_sl": new_sl, "retcode": result.retcode}

    # ── Error classification ──
    reason = "unknown_error"
    retry = False

    if result.retcode in (10016, 10017):  # TRADE_RETCODE_REQUOTE / TRADE_RETCODE_REJECT
        reason = "requote"
        retry = True
    elif result.retcode in (10019, 10027):  # TRADE_RETCODE_INVALID_STOPS / TRADE_RETCODE_INVALID_PRICE
        reason = "invalid_stops"
    elif result.retcode in (10022, 10024):  # TRADE_RETCODE_TIMEOUT / TRADE_RETCODE_PRICE_CHANGED
        reason = "price_changed"
        retry = True
    elif result.retcode == 10025:
        reason = "no_quotes"

    LOG.warning("SL modify REJECTED: ticket=%d retcode=%d reason=%s sl=%.2f",
                ticket, result.retcode, reason, new_sl)

    return {"status": "rejected", "reason": reason, "retcode": result.retcode,
            "ticket": ticket, "sl_requested": new_sl, "retry": retry}


# ── Retry with fresh price re-fetch ────────────────────────────────────────


def modify_sl_with_retry(ticket: int, new_sl: float, max_attempts: int = 3,
                         delay: float = 1.0) -> dict[str, Any]:
    """Modify SL with retry logic for requote/price_changed errors.
    On requote, re-fetches current price and adjusts SL."""
    for attempt in range(1, max_attempts + 1):
        result = modify_sl(ticket, new_sl)

        if result["status"] == "ok":
            return result

        if not result.get("retry") and result["reason"] not in ("requote", "price_changed"):
            return result  # non-retryable error

        if attempt < max_attempts:
            LOG.info("Retry %d/%d for ticket=%d (reason=%s) — waiting %.1fs",
                     attempt, max_attempts, ticket, result["reason"], delay)
            time.sleep(delay)
            # Re-fetch current price to adjust SL for next attempt
            pos = get_position(ticket)
            if pos:
                price = pos["current_price"]
                direction = pos["type"]
                # Adjust SL relative to current price
                price_dist = abs(new_sl - price)
                if direction == "BUY":
                    new_sl = price - price_dist
                else:
                    new_sl = price + price_dist
            delay *= 2  # exponential backoff

    return result


# ── Main Loop ───────────────────────────────────────────────────────────────


def main_loop():
    """Poll bridge for trailing SL updates, modify positions, report status."""
    LOG.info("MT5 Daemon started: id=%s bridge=%s account=%s",
             DAEMON_ID, BRIDGE_URL, ACCOUNT_ID)
    LOG.info("Registering with bridge...")

    _post("/daemon/register", {
        "daemon_id": DAEMON_ID,
        "account_id": ACCOUNT_ID,
        "api_key": API_KEY,
        "hostname": os.uname().nodename,
        "mt5_version": _get_mt5_version(),
    })

    last_reconcile = time.time()
    active_ticket: int | None = None  # ticket we're currently trailing

    while True:
        try:
            now = time.time()

            # ── Poll for trailing SL update ──
            params = f"?api_key={API_KEY}&account_id={ACCOUNT_ID}&mode=trailing&daemon_id={DAEMON_ID}"
            resp = _get(f"/signal{params}", timeout=5)

            if resp.get("action") and resp.get("status") == "trailing":
                ticket = resp.get("ticket")
                target_sl = resp.get("sl")
                target_tp = resp.get("tp")

                if ticket and target_sl:
                    LOG.info("Trailing update: ticket=%d sl=%.2f tp=%s",
                             ticket, target_sl, target_tp or "unchanged")

                    result = modify_sl_with_retry(ticket, target_sl, max_attempts=3)

                    # ── Callback to bridge with result ──
                    _post("/trade-status", {
                        "daemon_id": DAEMON_ID,
                        "account_id": ACCOUNT_ID,
                        "api_key": API_KEY,
                        "signal_id": resp.get("signal_id", ""),
                        "ticket": ticket,
                        "status": result["status"],
                        "reason": result.get("reason", ""),
                        "old_sl": result.get("old_sl", 0),
                        "new_sl": result.get("new_sl", target_sl),
                        "retcode": result.get("retcode", 0),
                        "timestamp": datetime.now(WIB).isoformat(),
                    })
                    LOG.info("Trade status reported: %s", result["status"])
                    active_ticket = ticket

            # ── Reconciliation: periodic position sync ──
            if now - last_reconcile >= RECONCILE_INTERVAL:
                if active_ticket:
                    pos = get_position(active_ticket)
                    if pos:
                        _post("/trade-status", {
                            "daemon_id": DAEMON_ID,
                            "account_id": ACCOUNT_ID,
                            "api_key": API_KEY,
                            "ticket": active_ticket,
                            "status": "ok",
                            "reason": "reconciliation",
                            "actual_sl": pos["sl"],
                            "actual_tp": pos["tp"],
                            "open_price": pos["open_price"],
                            "current_price": pos["current_price"],
                            "profit": pos["profit"],
                            "timestamp": datetime.now(WIB).isoformat(),
                        })
                        LOG.debug("Reconciliation: ticket=%d sl=%.2f tp=%.2f profit=%.2f",
                                  active_ticket, pos["sl"], pos["tp"], pos["profit"])
                    else:
                        # Position was closed — notify bridge
                        _post("/trade-status", {
                            "daemon_id": DAEMON_ID,
                            "account_id": ACCOUNT_ID,
                            "api_key": API_KEY,
                            "ticket": active_ticket,
                            "status": "closed",
                            "reason": "position_closed",
                            "timestamp": datetime.now(WIB).isoformat(),
                        })
                        LOG.info("Position closed: ticket=%d", active_ticket)
                        active_ticket = None
                last_reconcile = now

        except Exception as exc:
            LOG.error("Loop error: %s", exc)

        time.sleep(POLL_INTERVAL)


def _get_mt5_version() -> str:
    try:
        import MetaTrader5 as mt5
        return str(mt5.__version__) if mt5.__version__ else "unknown"
    except Exception:
        return "not_loaded"


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="MT5 Bridge Daemon — trailing SL executor")
    p.add_argument("--bridge", default=BRIDGE_URL, help="Bridge URL (default: http://localhost:8765)")
    p.add_argument("--api-key", default=API_KEY, help="Vilona API key")
    p.add_argument("--account-id", default=ACCOUNT_ID, help="MT5 account ID")
    p.add_argument("--mt5-login", type=int, default=0, help="MT5 login (0=use existing terminal)")
    p.add_argument("--mt5-password", default="", help="MT5 password")
    p.add_argument("--mt5-server", default="", help="MT5 server")
    p.add_argument("--poll", type=int, default=POLL_INTERVAL, help="Poll interval in seconds")
    args = p.parse_args()

    BRIDGE_URL = args.bridge.rstrip("/")
    API_KEY = args.api_key
    ACCOUNT_ID = args.account_id
    POLL_INTERVAL = args.poll

    if not connect_mt5(login=args.mt5_login, password=args.mt5_password,
                       server=args.mt5_server):
        LOG.error("Cannot start — MT5 connection failed")
        sys.exit(1)

    main_loop()
