#!/usr/bin/env python3
"""
ea_executor.py — Vilona EA Signal Executor (Paper + Live Ready)
================================================================
Reads ea_signal.json written by vilona_tradefx_handler.py.
Executes trades. Monitors SL/TP. Full audit log.

Usage:
    python3 ea_executor.py          # paper trading (default)
    python3 ea_executor.py --live    # live trading
"""

import json, logging, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DRY_RUN = "--live" not in sys.argv
WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
LOG_DIR = PROJECT_DIR / "logs"
SIGNAL_FILE = DATA_DIR / "ea_signal.json"
STATE_FILE = DATA_DIR / "ea_state.json"

# ── Telegram notification ──
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@vilonaaichanel")
TG_ENABLED = bool(TELEGRAM_BOT_TOKEN)

def tg_send(text: str) -> bool:
    """Send Telegram message to channel."""
    if not TG_ENABLED:
        return False
    try:
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False)
    except Exception as e:
        logger.error(f"tg_send failed: {e}")
        return False

# Same offset as bot handler — gold-api.com spot → broker price
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
    """Pip size for the symbol."""
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"):   return 0.1
    if s in ("BTCUSD", "BTC"):    return 1.0
    if s in ("ETHUSD", "ETH"):    return 0.01
    if s.endswith("JPY"):         return 0.01
    if s in ("USOIL", "OIL", "CL"): return 0.01
    return 0.0001

def _pip_value(symbol: str) -> float:
    """Pip value in USD for 1 standard lot."""
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"): return 1.0
    if s in ("BTCUSD", "BTC"):  return 1.0
    if s in ("ETHUSD", "ETH"):  return 0.01
    if s.endswith("JPY"):       return 9.0
    return 10.0

def _pnl_from_pips(entry: float, close: float, symbol: str, is_loss: bool) -> float:
    """Compute PnL in USD from entry/close prices (1 standard lot)."""
    ps = _pip_size(symbol)
    pips = abs(entry - close) / ps if ps > 0 else 0.0
    value = pips * _pip_value(symbol)
    return -value if is_loss else value

def load_state():
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except: pass
    return {"positions": [], "closed": [], "total_pnl": 0.0,
            "signals_processed": 0, "last_signal_id": None}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))

def fetch_price(symbol="XAUUSD"):
    """Fetch real-time XAUUSD: gold-api.com spot + offset."""
    try:
        r = urllib.request.urlopen("https://api.gold-api.com/price/XAU", timeout=10)
        spot = float(json.loads(r.read()).get("price", 0))
        if 2000 < spot < 6000:
            return round(spot + XAUUSD_OFFSET, 2)
    except Exception:
        pass
    return None

def read_signal():
    if not SIGNAL_FILE.exists(): return None
    try:
        return json.loads(SIGNAL_FILE.read_text())
    except: return None

def check_position(pos, price):
    if not price: return None
    entry = pos["entry"]
    sl = pos["sl"]
    tp = pos.get("tp", 0)
    tp1 = pos.get("tp1", tp)
    target_tp = tp1 if tp1 and tp1 > 0 else tp
    action = pos["action"]
    if action == "BUY":
        if price <= sl: return ("SL_HIT", price)
        if price >= target_tp: return ("TP_HIT", price)
    else:
        if price >= sl: return ("SL_HIT", price)
        if price <= target_tp: return ("TP_HIT", price)
    return None


def _send_trade_result(pos: dict, reason: str):
    """Send trade result notification to Telegram channel."""
    if not TG_ENABLED:
        return
    
    action = pos.get("action", "?")
    symbol = pos.get("symbol", "?")
    entry = pos.get("entry", 0)
    close_price = pos.get("close_price", 0)
    pnl = pos.get("pnl", 0)
    sl = pos.get("sl", 0)
    tp = pos.get("tp", 0)

    is_tp = reason == "TP_HIT"
    emoji = "✅" if is_tp else "❌"
    outcome_text = "TAKE PROFIT 🎯" if is_tp else "STOP LOSS 🛑"
    pnl_sign = "+" if pnl >= 0 else ""
    dir_emoji = "🟢" if action == "BUY" else "🔴"

    # Calculate pips using per-symbol pip size
    pips = abs(entry - close_price) / _pip_size(symbol) if _pip_size(symbol) > 0 else 0.0
    # Correct PnL: pips × pip_value (USOIL = $1/pip, XAUUSD = $10/pip, etc.)
    pip_val = _pip_value(symbol)
    pnl_usd = pips * pip_val
    if not is_tp:
        pnl_usd = -pnl_usd
    
    pips_text = f"{pips:.1f} pip" if pips < 100 else f"{pips:.0f} pip"
    msg = (
        f"{emoji} <b>TRADE CLOSED</b> — {outcome_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dir_emoji} <b>{action} {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry: <code>${entry:.2f}</code>\n"
        f"🛑 SL: <code>${sl:.2f}</code>\n"
        f"✅ TP: <code>${tp:.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 PnL: {pnl_usd:+,.2f} | 📉 {pips_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if is_tp:
        msg += (
            f"🎉 <b>CUAN! Profit secured!</b>\n"
            f"💰 Server GRATIS — dukung biaya AI:\n"
            f"/donate | @berkahkaryaforexbotbot\n"
        )
    else:
        msg += (
            f"🛑 SL terkena. Disiplin risk management.\n"
            f"Next setup tunggu konfirmasi ulang.\n"
            f"💚 Tetap semangat — /analyze untuk sinyal baru\n"
        )

    tg_send(msg)

def main():
    mode = "PAPER TRADING" if DRY_RUN else "🔴 LIVE TRADING"
    logger.info("=" * 50)
    logger.info(f"EA EXECUTOR STARTED | {mode}")
    logger.info(f"Signal file: {SIGNAL_FILE}")
    logger.info("=" * 50)

    state = load_state()
    logger.info(f"State: {len(state['positions'])} open, "
                f"{state['signals_processed']} processed, "
                f"PnL=${state['total_pnl']:.2f}")

    last_mtime = SIGNAL_FILE.stat().st_mtime if SIGNAL_FILE.exists() else 0
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
                        is_loss = reason == "SL_HIT"
                        pnl = _pnl_from_pips(pos["entry"], close_price, pos.get("symbol", "XAUUSD"), is_loss)
                        pos["status"] = reason
                        pos["close_price"] = close_price
                        pos["close_time"] = wib_now().isoformat()
                        pos["pnl"] = round(pnl, 2)

                        emoji = "🟢" if reason == "TP_HIT" else "🔴"
                        logger.info(f"{emoji} CLOSED: {pos['action']} | {reason} | "
                                    f"PnL=${pos['pnl']:.2f} | "
                                    f"Entry=${pos['entry']:.2f} → Close=${close_price:.2f}")
                        if reason == "TP_HIT":
                            logger.info(
                                "🎉🎉🎉 TP HIT! CUAN! 🎉🎉🎉 "
                                "Server ini GRATIS — bantu dukung biaya AI: /donate | @berkahkaryaforexbotbot"
                            )
                        state["closed"].append(pos)
                        state["total_pnl"] += pos["pnl"]
                        
                        # ── Telegram notification ──
                        _send_trade_result(pos, reason)
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
                        sig_fp = f"{sig.get('action','')}_{sig.get('entry',0):.2f}"
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
                            "open_time": wib_now().isoformat(),
                            "status": "OPEN",
                        }

                        if DRY_RUN:
                            logger.info(f"📝 PAPER: {sig['action']} {pos['symbol']} @ ${pos['entry']:.2f} | "
                                        f"SL=${pos['sl']:.2f} TP=${pos['tp']:.2f} | "
                                        f"conf={conf:.0%} RR=1:{rr:.1f} | {sig.get('source','?')}")
                            state["positions"].append(pos)
                            state["signals_processed"] += 1
                            state["last_signal_id"] = sig_fp
                            save_state(state)
                            logger.info(f"✅ POSITION OPEN: {pos['action']} @ ${pos['entry']:.2f}")

            # 3. Status heartbeat
            if state["positions"]:
                p = state["positions"][0]
                current = f"${price:.2f}" if price else "N/A"
                pnl_est = ""
                if price and p["entry"]:
                    est = abs(price - p["entry"])
                    direction = 1 if p["action"] == "BUY" else -1
                    pnl_est = f" | Est.PnL=${(price - p['entry']) * direction:.2f}"
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
