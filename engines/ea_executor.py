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
            "signals_processed": 0, "last_signal_id": None,
            "pending_zone_signals": []}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))

# ── Price feed (symbol-aware) ──

def fetch_price(symbol="XAUUSD"):
    """Fetch live price for symbol.
    XAUUSD via gold-api.com, crypto via Binance public API.
    No Yahoo Finance — prices don't match broker.
    """
    sym = symbol.upper()
    try:
        if sym in ("XAUUSD", "GOLD"):
            r = urllib.request.urlopen("https://api.gold-api.com/price/XAU", timeout=10)
            spot = float(json.loads(r.read()).get("price", 0))
            if 2000 < spot < 6000:
                return round(spot + XAUUSD_OFFSET, 2)
            logger.warning("XAU spot %.2f outside range", spot)
        elif sym in ("BTCUSD", "BITCOIN"):
            r = urllib.request.urlopen("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
            price = float(json.loads(r.read())["price"])
            return round(price, 2)
        elif sym in ("ETHUSD", "ETHEREUM"):
            r = urllib.request.urlopen("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", timeout=10)
            price = float(json.loads(r.read())["price"])
            return round(price, 2)
        else:
            logger.warning("fetch_price(%s): unknown symbol", sym)
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

            # ── 1.5. Check pending zone signals (zone-wait mode) ──
            ZONE_TIMEOUT = 1800  # 30 min max wait for zone
            new_pending = []
            for pending in state.get("pending_zone_signals", []):
                sym = pending.get("symbol", "XAUUSD")
                p = fetch_price(sym)
                if not p:
                    new_pending.append(pending)
                    continue

                zone_lo = pending.get("zone_lo", 0)
                zone_hi = pending.get("zone_hi", 0)
                now_ts = time.time()
                created_ts = pending.get("created_ts", now_ts)
                elapsed = now_ts - created_ts
                timeout = pending.get("entry_timeout", ZONE_TIMEOUT)

                if elapsed > timeout:
                    logger.info(
                        f"⏰ ZONE TIMEOUT: {pending['action']} {sym} | "
                        f"zone=[${zone_lo:.2f}-${zone_hi:.2f}] never reached in {timeout/60:.0f} min — CANCELLED"
                    )
                    continue

                in_zone = (zone_lo <= p <= zone_hi) if zone_lo < zone_hi else abs(p - pending.get("entry", 0)) < 0.5
                if in_zone:
                    # Don't exceed max positions
                    if len(state["positions"]) >= 1:
                        logger.info(f"⏭️ ZONE: price entered zone=[${zone_lo:.2f}-${zone_hi:.2f}] but max position already open — skipping {pending['action']}")
                        continue
                    sig = pending["signal"]
                    pos = {
                        "id": f"ea_{int(time.time()*1000)}",
                        "action": sig.get("action", "HOLD"),
                        "symbol": sig.get("symbol", sym),
                        "entry": p,
                        "sl": sig.get("sl", 0) or pending.get("sl", 0),
                        "tp": sig.get("tp", 0) or pending.get("tp", 0),
                        "tp1": sig.get("tp1", sig.get("tp", 0)),
                        "tp2": sig.get("tp2", 0),
                        "confidence": sig.get("confidence", 0),
                        "source": sig.get("source", "zone_wait"),
                        "telegram_message_id": sig.get("telegram_message_id"),
                        "open_time": wib_now().isoformat(),
                        "status": "OPEN",
                    }
                    state["positions"].append(pos)
                    state["signals_processed"] += 1
                    sig_fp = f"{pos['action']}_{pos['entry']:.2f}_{pos['sl']:.2f}_{pos['tp']:.2f}"
                    state["last_signal_id"] = sig_fp
                    state["pending_zone_signals"] = state.get("pending_zone_signals", [])
                    state["pending_zone_signals"] = [x for x in state["pending_zone_signals"] if x.get("created_ts") != created_ts or id(x) == id(pending)]
                    save_state(state)
                    rr_str = f"RR=1:{sig.get('rr_ratio', '?'):.1f}" if isinstance(sig.get('rr_ratio'), (int,float)) and sig.get('rr_ratio',0) > 0 else ""
                    logger.info(
                        f"🎯 ZONE ENTRY: {pos['action']} {pos['symbol']} @ ${pos['entry']:.2f} | "
                        f"zone=[${zone_lo:.2f}-${zone_hi:.2f}] | "
                        f"SL=${pos['sl']:.2f} TP=${pos['tp']:.2f} | waited {elapsed:.0f}s {rr_str}"
                    )
                    # Don't re-add to new_pending — executed
                else:
                    new_pending.append(pending)
                    if int(elapsed) % 30 == 0 or elapsed < 5:
                        logger.info(
                            f"⏳ ZONE WAIT: {pending['action']} {sym} | "
                            f"price=${p:.2f} outside zone=[${zone_lo:.2f}-${zone_hi:.2f}] | "
                            f"waited {elapsed:.0f}s/{timeout:.0f}s"
                        )
            state["pending_zone_signals"] = new_pending

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

                        # ── Quality check (safe type conversion) ──
                        conf = sig.get("confidence", 0)
                        if isinstance(conf, str):
                            try: conf = float(conf)
                            except: conf = 0
                        rr = sig.get("rr_ratio", 0)
                        if isinstance(rr, str):
                            # Handle "1:X" format
                            if ":" in rr:
                                try: rr = float(rr.split(":")[-1])
                                except: rr = 0
                            else:
                                try: rr = float(rr)
                                except: rr = 0
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
                        entry_mode = sig.get("entry_mode", "market")
                        if live_check and sig_entry and entry_mode != "zone":
                            pip_s = _pip_size(sig_symbol)
                            drift_pips = abs(live_check - sig_entry) / pip_s
                            if drift_pips > 15:
                                logger.warning(
                                    f"⛔ SLIPPAGE ABORT: |live={live_check:.2f} - signal={sig_entry:.2f}| = "
                                    f"{drift_pips:.0f} pip > 15 pip — CANCELLED DUE TO SLIPPAGE")
                                continue
                            logger.info(f"✅ SLIPPAGE OK: drift={drift_pips:.1f} pip (max 15)")
                        elif entry_mode == "zone":
                            logger.info(f"⏭️ SLIPPAGE SKIP (zone mode): entry=${sig_entry:.2f} live=${live_check} — drift expected, EA will wait for zone")

                        # ── ZONE MODE: wait for price to enter zone instead of immediate entry ──
                        entry_mode = sig.get("entry_mode", "zone")
                        if entry_mode == "zone":
                            zone_lo = sig.get("zone_lo", 0)
                            zone_hi = sig.get("zone_hi", 0)
                            # ── No meaningful zone spread → execute as market ──
                            if zone_lo == zone_hi:
                                logger.info(f"🎯 ZONE MODE: zone_lo==zone_hi (${zone_lo:.2f}) — treating as market execution")
                            else:
                                live_check = live_check or fetch_price(sig_symbol)
                                if live_check and zone_lo < zone_hi and (zone_lo <= live_check <= zone_hi):
                                    # Price already in zone — execute immediately (fall through)
                                    logger.info(f"🎯 ZONE MODE: price=${live_check:.2f} already inside zone=[${zone_lo:.2f}-${zone_hi:.2f}] — executing now")
                                else:
                                    # Price outside zone — queue for zone-wait
                                    pending_entry = {
                                        "created_ts": time.time(),
                                        "symbol": sig_symbol,
                                        "entry": sig_entry,
                                        "zone_lo": zone_lo,
                                        "zone_hi": zone_hi,
                                        "sl": sig.get("sl", 0),
                                        "tp": sig.get("tp", 0),
                                        "signal": dict(sig),
                                        "action": sig.get("action", "HOLD"),
                                        "entry_timeout": sig.get("entry_timeout", 1800),
                                    }
                                    state.setdefault("pending_zone_signals", []).append(pending_entry)
                                    state["signals_processed"] += 1
                                    state["last_signal_id"] = sig_fp
                                    save_state(state)
                                    live_str = f"${live_check:.2f}" if live_check else "N/A"
                                    logger.info(
                                        f"⏳ ZONE PENDING: {sig['action']} {sig_symbol} | "
                                        f"zone=[${zone_lo:.2f}-${zone_hi:.2f}] | live={live_str} | "
                                        f"waiting for price to reach zone (timeout=30m)"
                                    )
                                    continue  # skip position creation — zone monitor will handle it

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
