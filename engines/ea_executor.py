#!/usr/bin/env python3
"""
ea_executor.py — Signal Queue EA (paper trading + real-ready)
==============================================================
Reads ea_signal.json written by vilona_tradefx_handler.py.
Executes simulated trades. Monitors SL/TP. Full audit log.

Usage:
    python3 ea_executor.py          # paper trading (default)
    python3 ea_executor.py --real    # real trading (needs API keys)
"""

import json, logging, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DRY_RUN = "--real" not in sys.argv
WIB = timezone(timedelta(hours=7))
DATA_DIR = Path("/home/openclaw/projects/1ai-trade-bot/data/vilona_tradefx")
LOG_DIR = Path("/home/openclaw/projects/1ai-trade-bot/logs")
SIGNAL_FILE = DATA_DIR / "ea_signal.json"
STATE_FILE = DATA_DIR / "ea_state.json"

LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ea_executor.log"),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger("ea-executor")


def wib_now(): return datetime.now(WIB)

def load_state():
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except: pass
    return {"positions": [], "closed": [], "total_pnl": 0.0, "signals_processed": 0, "last_signal_id": None}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))

def fetch_price():
    try:
        r = urllib.request.urlopen("https://api.gold-api.com/price/XAU", timeout=10)
        return float(json.loads(r.read()).get("price", 0))
    except:
        return None

def read_signal():
    if not SIGNAL_FILE.exists(): return None
    try:
        return json.loads(SIGNAL_FILE.read_text())
    except: return None

def check_position(pos, price):
    if not price: return None
    entry, sl, tp, action = pos["entry"], pos["sl"], pos.get("tp", pos.get("tp1", 0)), pos["action"]
    if action == "BUY":
        if price <= sl: return ("SL", price)
        if price >= tp: return ("TP", price)
    else:
        if price >= sl: return ("SL", price)
        if price <= tp: return ("TP", price)
    return None


def main():
    mode = "PAPER TRADING" if DRY_RUN else "LIVE TRADING"
    logger.info("=" * 50)
    logger.info(f"EA EXECUTOR STARTED | {mode}")
    logger.info(f"Signal file: {SIGNAL_FILE}")
    logger.info("=" * 50)

    state = load_state()
    logger.info(f"State: {len(state['positions'])} open, {state['signals_processed']} processed, PnL=${state['total_pnl']:.2f}")

    last_mtime = 0
    interval = 3

    while True:
        try:
            # 1. Check open positions
            price = fetch_price()
            if price and state["positions"]:
                new_positions = []
                for pos in state["positions"]:
                    result = check_position(pos, price)
                    if result:
                        reason, close_price = result
                        pnl = abs(pos["entry"] - close_price)
                        if (pos["action"] == "BUY" and reason == "SL") or (pos["action"] == "SELL" and reason == "SL"):
                            pnl = -pnl
                        pos["status"] = reason
                        pos["close_price"] = close_price
                        pos["close_time"] = wib_now().isoformat()
                        pos["pnl"] = round(pnl, 2)

                        emoji = "🟢" if reason == "TP" else "🔴"
                        logger.info(f"{emoji} CLOSED: {pos['action']} | {reason} | PnL=${pos['pnl']:.2f} | "
                                    f"Entry=${pos['entry']:.2f} → Close=${close_price:.2f}")
                        state["closed"].append(pos)
                        state["total_pnl"] += pos["pnl"]
                    else:
                        new_positions.append(pos)
                state["positions"] = new_positions
                save_state(state)

            # 2. Check for new signals
            if SIGNAL_FILE.exists():
                mtime = SIGNAL_FILE.stat().st_mtime
                if mtime > last_mtime:
                    last_mtime = mtime
                    sig = read_signal()
                    if sig and sig.get("action") in ("BUY", "SELL"):
                        sig_id = sig.get("confidence", 0)
                        # Skip if already processed
                        if state["last_signal_id"] and abs(state["last_signal_id"] - sig_id) < 0.001:
                            continue

                        # Max 1 position
                        if len(state["positions"]) >= 1:
                            logger.info(f"⏭️ Max positions — skip {sig['action']}")
                            continue

                        # Open position
                        pos = {
                            "id": f"ea_{int(time.time()*1000)}",
                            "action": sig["action"],
                            "entry": sig.get("entry", price or 0),
                            "sl": sig.get("sl", 0),
                            "tp": sig.get("tp", sig.get("tp1", 0)),
                            "confidence": sig.get("confidence", 0),
                            "source": sig.get("source", "unknown"),
                            "open_time": wib_now().isoformat(),
                            "status": "OPEN",
                        }

                        if DRY_RUN:
                            logger.info(f"📝 PAPER: {sig['action']} XAUUSD @ ${pos['entry']:.2f} | "
                                        f"SL=${pos['sl']:.2f} TP=${pos['tp']:.2f} | "
                                        f"conf={sig.get('confidence',0):.0%} | {sig.get('source','?')}")
                            state["positions"].append(pos)
                            state["signals_processed"] += 1
                            state["last_signal_id"] = sig_id
                            save_state(state)
                            logger.info(f"✅ POSITION OPEN: {pos['action']} @ ${pos['entry']:.2f}")

            # 3. Status heartbeat
            if state["positions"]:
                p = state["positions"][0]
                current = f"${price:.1f}" if price else "N/A"
                pnl_est = ""
                if price and p["entry"]:
                    est = abs(price - p["entry"])
                    pnl_est = f" | Est.PnL=${est:.2f}"
                logger.info(f"💼 {p['action']} @ ${p['entry']:.2f} | Current={current}{pnl_est} | "
                            f"SL=${p['sl']:.2f} TP=${p['tp']:.2f}")

            time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(10)

    logger.info(f"EA stopped. Total: {state['signals_processed']} signals, PnL=${state['total_pnl']:.2f}")


if __name__ == "__main__":
    main()
