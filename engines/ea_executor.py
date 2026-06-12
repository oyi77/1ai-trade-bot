#!/usr/bin/env python3
"""
ea_executor.py — Vilona EA Signal Executor (Paper + Live Ready)
================================================================
Reads ea_signal.json written by vilona_tradefx_handler.py.
Executes trades. Monitors SL/TP. Full audit log.

FASE 1+2 refactor (2026-06-10):
  - Uses SAME offset as handler: spot + XAUUSD_OFFSET (from env, default 74)
  - Close price = TP/SL price (not market price) — matches handler behavior
  - NO independent Telegram broadcast (single source of truth: handler)
  - Writes trade_result.json for handler to pick up and broadcast

Usage:
    python3 ea_executor.py          # paper trading (no broker integration yet)
"""

import fcntl, json, logging, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

PAPER_MODE = True  # always paper — no MT5/Deriv broker integration yet
WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
LOG_DIR = PROJECT_DIR / "logs"
SIGNAL_FILE = DATA_DIR / "ea_signal.json"
STATE_FILE = DATA_DIR / "ea_state.json"
TRADE_RESULT_FILE = DATA_DIR / "trade_result.json"  # for handler broadcast

# ── OFFSET: MATCHES HANDLER EXACTLY (spot + XAUUSD_OFFSET) ──
# Handler: fetch_price → spot + XAUUSD_OFFSET (env, default 74)
# ea_executor MUST use the same reference so entry/sl/tp are in the same price space
XAUUSD_OFFSET = float(os.environ.get("XAUUSD_PRICE_OFFSET", "74"))

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

# ── Per-symbol pip helpers (consistent with trade_tracker.py) ──

def _pip_size(symbol: str) -> float:
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"):   return 0.1
    if s in ("BTCUSD", "BTC"):    return 1.0
    if s in ("ETHUSD", "ETH"):    return 0.01
    if s.endswith("JPY"):         return 0.01
    if s in ("USOIL", "OIL", "CL"): return 0.01
    return 0.0001

def _pip_value(symbol: str) -> float:
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"): return 10.0   # $10 per pip (1 standard lot)
    if s in ("BTCUSD", "BTC"):  return 1.0
    if s in ("ETHUSD", "ETH"):  return 0.01
    if s.endswith("JPY"):       return 9.0
    return 10.0

def _pnl_from_pips(entry: float, close: float, symbol: str, is_loss: bool) -> float:
    ps = _pip_size(symbol)
    pips = abs(entry - close) / ps if ps > 0 else 0.0
    value = pips * _pip_value(symbol)
    return -value if is_loss else value

# ── State ──

def load_state():
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as e:
        logger.warning("load_state failed: %s", e)
    return {"positions": [], "closed": [], "total_pnl": 0.0,
            "signals_processed": 0, "last_signal_id": None}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))

# ── Price feed (symbol-aware) ──

def fetch_price(symbol="XAUUSD"):
    """Fetch live price for symbol. XAUUSD via gold-api.com + offset.
    Other symbols via Yahoo Finance. Returns offset-adjusted price for
    XAUUSD or raw price for everything else.
    """
    sym = symbol.upper()
    try:
        if sym in ("XAUUSD", "GOLD"):
            r = urllib.request.urlopen("https://api.gold-api.com/price/XAU", timeout=10)
            spot = float(json.loads(r.read()).get("price", 0))
            if 2000 < spot < 6000:
                return round(spot + XAUUSD_OFFSET, 2)
            logger.warning("XAU spot %.2f outside range", spot)
        else:
            # Yahoo Finance for USOIL, BTCUSD, etc.
            yf_sym = {"USOIL": "CL=F", "OIL": "CL=F", "BTCUSD": "BTC-USD",
                      "ETHUSD": "ETH-USD"}.get(sym, sym)
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1m"
            r = urllib.request.urlopen(url, timeout=10)
            data = json.loads(r.read())
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            return round(float(price), 2)
    except Exception as e:
        logger.warning("fetch_price(%s) failed: %s", sym, e)
    return None

def read_signal():
    if not SIGNAL_FILE.exists():
        return None
    try:
        raw = SIGNAL_FILE.read_text()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("read_signal JSON parse failed, retrying after 0.5s: %s", e)
        time.sleep(0.5)
        try:
            raw = SIGNAL_FILE.read_text()
            return json.loads(raw)
        except Exception as e2:
            logger.warning("read_signal retry failed: %s", e2)
            return None
    except Exception as e:
        logger.warning("read_signal failed: %s", e)
        return None

# ── Position checking (close_price = TP/SL price, NOT market price) ──

def check_position(pos, price):
    """Check if position hit SL or TP. Returns (reason, close_price).
    close_price = TP or SL price (not market price) — matches handler behavior.
    """
    if not price: return None
    entry = pos["entry"]
    sl = pos["sl"]
    tp = pos.get("tp", 0)
    tp1 = pos.get("tp1", tp)
    target_tp = tp1 if tp1 and tp1 > 0 else tp
    action = pos["action"]

    if action == "BUY":
        if price <= sl: return ("SL_HIT", sl)
        if price >= target_tp: return ("TP_HIT", target_tp)
    else:  # SELL
        if price >= sl: return ("SL_HIT", sl)
        if price <= target_tp: return ("TP_HIT", target_tp)
    return None

# ── Trade result file writer (for handler broadcast) ──

def _write_trade_result(pos: dict, reason: str):
    """Write trade result to file so handler can broadcast via send_to_channel.
    SINGLE SOURCE OF TRUTH — no independent Telegram sending from ea_executor.
    """
    entry = pos.get("entry", 0)
    close_price = pos.get("close_price", 0)
    if close_price in (0, None):
        logger.warning("_write_trade_result: close_price is %s for closed trade %s (reason=%s)",
                       close_price, pos.get("id", "?"), reason)
    symbol = pos.get("symbol", "XAUUSD")
    action = pos.get("action", "?")
    pnl = pos.get("pnl", 0)
    pips = abs(entry - close_price) / _pip_size(symbol) if _pip_size(symbol) > 0 else 0.0
    is_tp = reason == "TP_HIT"

    result = {
        "timestamp": wib_now().isoformat(),
        "action": action,
        "symbol": symbol,
        "entry": entry,
        "close_price": close_price,
        "sl": pos.get("sl", 0),
        "tp": pos.get("tp", 0),
        "pips": round(pips, 1),
        "pnl_usd": round(pnl, 2),
        "outcome": reason,
        "paper": PAPER_MODE,
        "telegram_message_id": pos.get("telegram_message_id"),  # reply chain
    }

    try:
        with open(TRADE_RESULT_FILE, 'a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                existing = json.load(f) if os.fstat(f.fileno()).st_size > 0 else []
            except json.JSONDecodeError:
                existing = []
            existing.append(result)
            if len(existing) > 100:
                existing = existing[-100:]
            f.seek(0)
            f.truncate()
            f.write(json.dumps(existing, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to write trade result: {e}")


# ══════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════

def main():
    mode = "EA EXECUTOR — PAPER TRADING"
    logger.info("=" * 60)
    logger.info(f"EA EXECUTOR STARTED | {mode}")
    logger.info(f"Offset: spot + {XAUUSD_OFFSET} (matches handler)")
    logger.info(f"Signal file: {SIGNAL_FILE}")
    logger.info(f"Trade results → {TRADE_RESULT_FILE} (for handler broadcast)")
    logger.info("Telegram broadcast: DISABLED (handler is single source of truth)")
    logger.info("=" * 60)

    state = load_state()
    logger.info(f"State: {len(state['positions'])} open, "
                f"{state['signals_processed']} processed, "
                f"PnL=${state['total_pnl']:.2f}")

    # Start with mtime=0 so the FIRST iteration always picks up an existing signal
    last_mtime = 0
    interval = 3

    while True:
        try:
            # ── 1. Check open positions ──
            new_positions = []
            for pos in state.get("positions", []):
                sym = pos.get("symbol", "XAUUSD")
                price = fetch_price(sym)
                if not price:
                    new_positions.append(pos)
                    continue
                result = check_position(pos, price)
                if result:
                    reason, close_price = result
                    is_loss = reason == "SL_HIT"
                    pnl = _pnl_from_pips(
                        pos["entry"], close_price,
                        pos.get("symbol", "XAUUSD"), is_loss
                    )
                    pos["status"] = reason
                    pos["close_price"] = close_price
                    pos["close_time"] = wib_now().isoformat()
                    pos["pnl"] = round(pnl, 2)

                    # Pips for logging
                    ps = _pip_size(pos.get("symbol", "XAUUSD"))
                    pips_closed = abs(pos["entry"] - close_price) / ps

                    emoji = "🟢" if reason == "TP_HIT" else "🔴"
                    logger.info(
                        f"{emoji} CLOSED: {pos['action']} {pos.get('symbol','?')} | {reason} | "
                        f"PnL=${pos['pnl']:.2f} ({pips_closed:.1f} pip) | "
                        f"Entry=${pos['entry']:.2f} → Close=${close_price:.2f} "
                        f"(SL=${pos['sl']:.2f} TP=${pos['tp']:.2f})"
                    )
                    if reason == "TP_HIT":
                        logger.info("🎉 TP HIT! CUAN! 🎉")

                    state["closed"].append(pos)
                    state["total_pnl"] += pos["pnl"]

                    # ── Write to trade_result.json (handler picks up for broadcast) ──
                    _write_trade_result(pos, reason)
                else:
                    new_positions.append(pos)
            state["positions"] = new_positions
            save_state(state)

            # ── 2. Check for new signals ──
            if SIGNAL_FILE.exists():
                mtime = SIGNAL_FILE.stat().st_mtime
                if mtime > last_mtime:
                    last_mtime = mtime
                    sig = read_signal()
                    if sig and sig.get("action") in ("BUY", "SELL"):
                        sig_fp = f"{sig.get('action','')}_{sig.get('entry',0):.2f}_{sig.get('sl',0):.2f}_{sig.get('tp',0):.2f}"
                        if state["last_signal_id"] == sig_fp:
                            continue

                        # Max 1 position
                        if len(state["positions"]) >= 1:
                            logger.info(f"⏭️ Max positions — skip {sig['action']}")
                            continue

                        # ── Quality check ──
                        conf = sig.get("confidence", 0)
                        rr = sig.get("rr_ratio", 0)
                        if conf < 0.65:
                            logger.info(f"⛔ Signal rejected: confidence {conf:.0%} < 65%")
                            continue
                        if rr > 0 and rr < 1.5:
                            logger.info(f"⛔ Signal rejected: RR 1:{rr:.1f} < 1:1.5")
                            continue

                        # ── SLIPPAGE GUARD: re-fetch live price before execution ──
                        sig_entry = sig.get("entry", 0) or 0
                        sig_symbol = sig.get("symbol", "XAUUSD")
                        live_check = fetch_price(sig_symbol) if sig_symbol else None
                        if live_check and sig_entry:
                            pip_s = _pip_size(sig_symbol)
                            drift_pips = abs(live_check - sig_entry) / pip_s
                            if drift_pips > 15:
                                logger.warning(
                                    f"⛔ SLIPPAGE ABORT: |live={live_check:.2f} - signal={sig_entry:.2f}| = "
                                    f"{drift_pips:.0f} pip > 15 pip — CANCELLED DUE TO SLIPPAGE")
                                continue
                            logger.info(f"✅ SLIPPAGE OK: drift={drift_pips:.1f} pip (max 15)")

                        pos = {
                            "id": f"ea_{int(time.time()*1000)}",
                            "action": sig["action"],
                            "symbol": sig.get("symbol", "XAUUSD"),
                            "entry": sig.get("entry", price or 0),
                            "sl": sig.get("sl", 0),
                            "tp": sig.get("tp", 0),
                            "tp1": sig.get("tp1", sig.get("tp", 0)),
                            "tp2": sig.get("tp2", 0),
                            "confidence": conf,
                            "source": sig.get("source", "unknown"),
                            "telegram_message_id": sig.get("telegram_message_id"),  # reply chain
                            "open_time": wib_now().isoformat(),
                            "status": "OPEN",
                        }

                        if PAPER_MODE:
                            logger.info(
                                f"📝 PAPER: {sig['action']} {pos['symbol']} @ ${pos['entry']:.2f} | "
                                f"SL=${pos['sl']:.2f} TP=${pos['tp']:.2f} | "
                                f"conf={conf:.0%} RR=1:{rr:.1f} | {sig.get('source','?')}"
                            )
                            state["positions"].append(pos)
                            state["signals_processed"] += 1
                            state["last_signal_id"] = sig_fp
                            save_state(state)
                            logger.info(f"✅ POSITION OPEN: {pos['action']} @ ${pos['entry']:.2f}")

            # ── 3. Status heartbeat ──
            if state["positions"]:
                p = state["positions"][0]
                hb_sym = p.get("symbol", "XAUUSD")
                hb_price = fetch_price(hb_sym)
                current = f"${hb_price:.2f}" if hb_price else "N/A"
                pnl_est = ""
                if hb_price and p["entry"]:
                    direction = 1 if p["action"] == "BUY" else -1
                    pnl_est = f" | Est.PnL=${(hb_price - p['entry']) * direction:.2f}"
                logger.info(
                    f"💼 {p['action']} {hb_sym} @ ${p['entry']:.2f} | Current={current}{pnl_est} | "
                    f"SL=${p['sl']:.2f} TP=${p['tp']:.2f}"
                )

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
