#!/usr/bin/env python3
"""
MT5 Position Reporter — Report ALL open positions to Vilona bridge for trailing.
Runs as a 15-second cycle daemon. Connect to MT5, fetch all positions,
POST to bridge /report_positions.
"""
import json, os, sys, time, urllib.request
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────
BRIDGE_URL = os.environ.get("VILONA_BRIDGE_URL", "http://localhost:8765")
API_KEY = os.environ.get("VILONA_API_KEY", "VT-PRO-LAUNCH")
ACCOUNT_ID = os.environ.get("MT5_ACCOUNT_ID", "MT5-183371455")
MT5_LOGIN = int(os.environ.get("MT5_LOGIN", "0"))
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER = os.environ.get("MT5_SERVER", "")
MT5_PATH = os.environ.get("MT5_TERMINAL_PATH", "")
CYCLE_SECONDS = int(os.environ.get("MT5_REPORT_CYCLE", "15"))

log = lambda msg: print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def connect_mt5():
    """Connect to MT5 terminal. Returns True if connected."""
    try:
        import MetaTrader5 as mt5
        if MT5_PATH:
            mt5.initialize(path=MT5_PATH)
        elif MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
            mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        else:
            mt5.initialize()
        if mt5.terminal_info() is None:
            log("❌ MT5 not running — start MT5 first")
            mt5.shutdown()
            return None
        log(f"✅ Connected to MT5 — build {mt5.terminal_info().build}")
        return mt5
    except ImportError:
        log("❌ MetaTrader5 package not installed: pip install MetaTrader5")
        return None
    except Exception as e:
        log(f"❌ MT5 connect failed: {e}")
        return None


def fetch_positions(mt5):
    """Fetch ALL open positions from MT5. Returns list of position dicts."""
    positions = mt5.positions_get()
    if positions is None or len(positions) == 0:
        return []
    
    result = []
    for p in positions:
        result.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "direction": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "entry": round(p.price_open, 2),
            "sl": round(p.sl, 2) if p.sl > 0 else 0,
            "tp": round(p.tp, 2) if p.tp > 0 else 0,
            "volume": round(p.volume, 2),
            "profit": round(p.profit, 2),
            "comment": p.comment or "",
            "source": "mt5_reporter",
        })
    return result


def report_to_bridge(positions):
    """POST positions to bridge /report_positions."""
    url = f"{BRIDGE_URL}/report_positions?api_key={API_KEY}&account_id={ACCOUNT_ID}"
    data = json.dumps({"positions": positions}).encode()
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read())
            return result
    except Exception as e:
        log(f"⚠️ Bridge POST failed: {e}")
        return None


def main():
    log("🚀 MT5 Position Reporter started")
    log(f"   Bridge: {BRIDGE_URL}")
    log(f"   API Key: {API_KEY[:8]}...")
    log(f"   Account: {ACCOUNT_ID}")
    log(f"   Cycle: {CYCLE_SECONDS}s")
    
    mt5 = None
    error_count = 0
    
    while True:
        try:
            # Connect/reconnect
            if mt5 is None:
                mt5 = connect_mt5()
                if mt5 is None:
                    time.sleep(15)
                    continue
            
            # Fetch positions
            positions = fetch_positions(mt5)
            
            if positions:
                # Report to bridge
                result = report_to_bridge(positions)
                if result:
                    status = result.get("status", "?")
                    added = result.get("added", 0)
                    updated = result.get("updated", 0)
                    removed = result.get("removed", 0)
                    log(f"📊 {len(positions)} positions → bridge | +{added} ~{updated} -{removed}")
                    error_count = 0
                else:
                    error_count += 1
            else:
                # No positions — still report empty to trigger cleanup
                result = report_to_bridge([])
                if result:
                    removed = result.get("removed", 0)
                    if removed:
                        log(f"🧹 Cleaned up {removed} stale positions")
            
            # Too many errors → reconnect
            if error_count >= 5:
                log("🔌 Too many errors — reconnecting MT5...")
                mt5.shutdown()
                mt5 = None
                error_count = 0
            
        except Exception as e:
            log(f"❌ Loop error: {e}")
            error_count += 1
            if error_count >= 10:
                if mt5:
                    mt5.shutdown()
                mt5 = None
                error_count = 0
        
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
