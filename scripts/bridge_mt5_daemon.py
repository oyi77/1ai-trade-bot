#!/usr/bin/env python3
"""Vilona Trade FX — MT5 Daemon (Client-Side).

Runs alongside MetaTrader 5 on the trader's VPS. Polls the bridge for trailing
SL updates, executes PositionModify via MT5 Python API, and posts results back
via /trade-status.

Usage:
  python3 bridge_mt5_daemon.py \
      --bridge https://your-server:8765 \
      --api-key VT-PRO-LAUNCH \
      --account-id MT5-183371455

Requirements:
  pip install MetaTrader5
"""

import argparse, hashlib, json, logging, os, platform, socket, sys, time, urllib.request
from urllib.error import URLError, HTTPError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("mt5-daemon")

MAX_RETRY = 3
POLL_INTERVAL = 5  # seconds
RECONNECT_DELAY = 10  # seconds between bridge reconnect attempts


def _post_json(url, data, timeout=10):
    """POST JSON to bridge endpoint. Returns (status_code, response_dict)."""
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except HTTPError as e:
        body = e.read()
        return e.code, json.loads(body) if body else {"error": str(e)}
    except URLError as e:
        return 0, {"error": f"connection failed: {e.reason}"}


def _get_json(url, timeout=10):
    """GET from bridge. Returns (status_code, response_dict)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, {}
    except URLError:
        return 0, {}


def register_daemon(bridge_url, daemon_id, api_key, account_id):
    """Register this daemon with the bridge. Returns True on success."""
    status, resp = _post_json(f"{bridge_url}/daemon/register", {
        "daemon_id": daemon_id,
        "account_id": account_id,
        "api_key": api_key,
        "hostname": socket.gethostname(),
        "mt5_version": "5.0",
    })
    if status == 200 and resp.get("status") == "ok":
        LOG.info(f"🔌 Registered: {daemon_id} (daemons online: {resp.get('daemon_count', '?')})")
        return True
    LOG.error(f"Registration failed: {resp}")
    return False


def poll_trailing(bridge_url, daemon_id, api_key, account_id):
    """Poll bridge for trailing SL updates. Returns signal dict or None."""
    qs = f"?mode=trailing&daemon_id={daemon_id}&api_key={api_key}&account_id={account_id}"
    status, resp = _get_json(f"{bridge_url}/signal{qs}")
    if status != 200:
        return None
    if resp.get("action", "HOLD") == "HOLD":
        return None
    return resp


def reconcile_position(mt5, ticket):
    """Query MT5 for actual position state. Returns dict or None."""
    pos = mt5.positions_get(ticket=ticket)
    if not pos or len(pos) == 0:
        return None
    p = pos[0]
    return {
        "ticket": p.ticket,
        "sl": p.sl,
        "tp": p.tp,
        "profit": p.profit,
        "volume": p.volume,
        "price_open": p.price_open,
        "price_current": p.price_current,
        "type": "BUY" if p.type == 0 else "SELL",
    }


def execute_trail(mt5, signal):
    """Execute PositionModify on MT5. Returns (success, retcode, comment)."""
    ticket = int(signal.get("ticket", 0) or signal.get("signal_id", "0").split("_")[-1])
    sl = float(signal.get("sl", 0))
    tp = float(signal.get("tp", 0))

    if ticket <= 0:
        # Try to find active position by matching direction/symbol
        symbol = signal.get("symbol", "XAUUSD")
        direction = signal.get("action", "")
        pos_type = 0 if direction == "BUY" else 1
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            for p in positions:
                if p.type == pos_type:
                    ticket = p.ticket
                    break
        if ticket <= 0:
            return False, -1, "no_active_position"

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl,
        "tp": tp if tp > 0 else 0,
        "symbol": signal.get("symbol", "XAUUSD"),
    }

    for attempt in range(1, MAX_RETRY + 1):
        result = mt5.order_send(request)
        if result is None:
            LOG.error(f"order_send returned None (MT5 connection issue?)")
            time.sleep(1)
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return True, result.retcode, "ok"
        if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
            # Update price and retry
            tick = mt5.symbol_info_tick(signal.get("symbol", "XAUUSD"))
            if tick:
                request["price"] = tick.ask  # not used for SLTP but may help
            LOG.warning(f"Requote on attempt {attempt}/{MAX_RETRY}: {result.comment}")
            time.sleep(0.5 * attempt)
            continue
        # Non-retryable error
        return False, result.retcode, result.comment

    return False, mt5.TRADE_RETCODE_REQUOTE, "max_retries_exceeded"


def report_status(bridge_url, daemon_id, api_key, account_id, ticket, status, reason, sl):
    """POST /trade-status to bridge. Returns True on success."""
    code, resp = _post_json(f"{bridge_url}/trade-status", {
        "daemon_id": daemon_id,
        "ticket": ticket,
        "status": status,
        "reason": reason,
        "actual_sl": sl,
        "api_key": api_key,
        "account_id": account_id,
    })
    return code == 200 and resp.get("status") == "ok"


def main():
    parser = argparse.ArgumentParser(description="Vilona MT5 Trailing Daemon")
    parser.add_argument("--bridge", required=True, help="Bridge URL (e.g. https://server:8765)")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--account-id", required=True, help="MT5 account ID")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL,
                        help=f"Poll interval in seconds (default: {POLL_INTERVAL})")
    args = parser.parse_args()

    bridge_url = args.bridge.rstrip("/")
    api_key = args.api_key
    account_id = args.account_id
    daemon_id = f"daemon-{platform.node()}-{os.getpid()}"

    LOG.info(f"🚀 MT5 Daemon starting | bridge={bridge_url} | account={account_id}")

    # ── Import MetaTrader5 (lazy — user must install on VPS) ──
    try:
        import MetaTrader5 as mt5
    except ImportError:
        LOG.critical("MetaTrader5 not installed. Run: pip install MetaTrader5")
        sys.exit(1)

    if not mt5.initialize():
        LOG.critical(f"MT5 initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    mt5_info = mt5.account_info()
    if mt5_info is None:
        LOG.critical("MT5 account_info() returned None — is MT5 logged in?")
        mt5.shutdown()
        sys.exit(1)

    LOG.info(f"✅ MT5 connected | login={mt5_info.login} | server={mt5_info.server} "
             f"| balance={mt5_info.balance} | leverage={mt5_info.leverage}")

    # ── Register with bridge ──
    while not register_daemon(bridge_url, daemon_id, api_key, account_id):
        LOG.warning(f"Retrying registration in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)

    last_health = time.time()

    # ── Main loop ──
    while True:
        try:
            # Periodic MT5 health check
            if time.time() - last_health > 30:
                if not mt5.terminal_info():
                    LOG.error("MT5 terminal disconnected — attempting reconnect...")
                    mt5.shutdown()
                    time.sleep(5)
                    if not mt5.initialize():
                        LOG.error("MT5 re-init failed")
                        time.sleep(RECONNECT_DELAY)
                        continue
                last_health = time.time()

            signal = poll_trailing(bridge_url, daemon_id, api_key, account_id)
            if signal is None:
                time.sleep(args.interval)
                continue

            action = signal.get("action", "HOLD")
            sig_id = signal.get("signal_id", "?")

            # ── Handle reconciliation request ──
            if action == "RECONCILE":
                ticket = signal.get("ticket", 0)
                req_sl = signal.get("bridge_sl", 0)
                pos_state = reconcile_position(mt5, ticket) if ticket > 0 else None
                if pos_state:
                    actual_sl = pos_state["sl"]
                    drift = abs(actual_sl - req_sl) if req_sl else 0
                    LOG.info(f"🔍 Reconcile: ticket={ticket} bridge_sl={req_sl} "
                            f"actual_sl={actual_sl} drift={drift:.3f}")
                    report_status(bridge_url, daemon_id, api_key, account_id,
                                  ticket, "reconciliation",
                                  f"drift={drift:.3f}" if drift > 0.001 else "ok",
                                  actual_sl)
                else:
                    LOG.warning(f"Reconcile: ticket={ticket} not found (position closed?)")
                    report_status(bridge_url, daemon_id, api_key, account_id,
                                  ticket, "closed", "position_not_found", 0)
                time.sleep(args.interval)
                continue

            # ── Normal trailing SL modification ──
            if action in ("BUY", "SELL"):
                ticket = signal.get("ticket", 0)
                success, retcode, comment = execute_trail(mt5, signal)

                # Resolve ticket from execute_trail if it wasn't provided
                if ticket <= 0:
                    # execute_trail found it internally — use any active position
                    positions = mt5.positions_get(symbol=signal.get("symbol", "XAUUSD"))
                    if positions:
                        pos_type = 0 if action == "BUY" else 1
                        for p in positions:
                            if p.type == pos_type:
                                ticket = p.ticket
                                break

                if success:
                    LOG.info(f"✅ Trail OK: {sig_id} ticket={ticket} SL→{signal.get('sl')}")
                    report_status(bridge_url, daemon_id, api_key, account_id,
                                  ticket, "ok", str(retcode), signal.get("sl", 0))
                else:
                    reason = f"{retcode} {comment}" if comment else str(retcode)
                    LOG.warning(f"⚠️ Trail FAIL: {sig_id} ticket={ticket} reason={reason}")
                    report_status(bridge_url, daemon_id, api_key, account_id,
                                  ticket, "rejected", reason, signal.get("sl", 0))

            time.sleep(args.interval)

        except KeyboardInterrupt:
            break
        except Exception as e:
            LOG.error(f"Loop error: {e}")
            time.sleep(args.interval)

    LOG.info("Daemon shutting down...")
    mt5.shutdown()


if __name__ == "__main__":
    main()
