#!/usr/bin/env python3
"""
auto_signal_publisher.py — Cron-based signal scanner & publisher.

FALLBACK system: berjalan independen dari handler's auto_analyze_loop.
Tiap 15 menit: scan MTF matrix → generate signal → post ke channel + log.

Usage:
    python3 auto_signal_publisher.py                    # run once
    python3 auto_signal_publisher.py --quiet            # silent mode (cron)
    python3 auto_signal_publisher.py --force             # skip dedup check
"""
import sys, os, json, logging, time, argparse
from datetime import datetime, timezone, timedelta

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("auto_signal")

WIB = timezone(timedelta(hours=7))

def load_trade_log():
    path = os.path.join(PROJECT_DIR, "data", "trade_log.json")
    try:
        with open(path) as f:
            return json.load(f)
    except: return []

def is_duplicate(log, signal):
    """Cek apakah signal sudah pernah dipost (dedup by entry price)."""
    entry = signal.get("entry", 0)
    for s in log[-5:]:  # cek 5 terakhir aja
        if abs(s.get("entry", 0) - entry) < 1.0 and s.get("action") == signal.get("action"):
            return True
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true", help="Silent mode (cron)")
    parser.add_argument("--force", action="store_true", help="Skip dedup check")
    args = parser.parse_args()

    try:
        from engine_consensus import run_engine_consensus
        from signal_calculator import compute_signal, format_signal_telegram, log_signal
    except ImportError as e:
        log.error(f"Import error: {e}")
        return 1

    # ── MTF Scan ──
    assets = ["XAUUSD", "BTCUSD", "USOIL"]
    results = {}
    
    for sym in assets:
        if not args.quiet:
            log.info(f"Scanning {sym}...")
        result = run_engine_consensus(symbol=sym)
        if result:
            results[sym] = result
            hier = result.get("hierarchical", {})
            verdict = hier.get("verdict", "HOLD")
            score = hier.get("consensus_score", 0)
            alignment = hier.get("mtf_alignment", "NONE")
            if not args.quiet:
                log.info(f"  {sym}: {hier.get('macro_trend','?')} | {alignment} | {verdict} ({score*100:.0f}%)")
    
    if not results:
        if not args.quiet:
            log.warning("No MTF results for any asset")
        return 0
    
    # Process each result
    any_signal = False
    for sym, result in results.items():
        hier = result.get("hierarchical", {})
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0)
        alignment = hier.get("mtf_alignment", "NONE")
        
        if verdict == "HOLD":
            if not args.quiet:
                log.info(f"  {sym}: HOLD — skipped")
            continue
        
        # ── Generate Signal ──
        sig = compute_signal(result)
        if not sig:
            if not args.quiet:
                log.info(f"  {sym}: Quality gate blocked")
            continue
        
        if sig.get("grade") not in ("A", "B"):
            if not args.quiet:
                log.info(f"  {sym}: Grade {sig.get('grade')} too low — skipped")
            continue
        
        # ── Dedup check ──
        if not args.force:
            trade_log = load_trade_log()
            if is_duplicate(trade_log, sig):
                if not args.quiet:
                    log.info(f"  {sym}: Duplicate signal — skipped")
                continue
        
        # ── Post to Telegram ──
        text = format_signal_telegram(sig)
        
        try:
            sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))
            from vilona_tradefx_handler import send_to_channel
            result = send_to_channel(text)
            if result:
                log.info(f"✅ Posted to channel: {sig['action']} {sig['symbol']} Grade {sig['grade']}")
            else:
                log.warning("Channel post returned None")
        except Exception as e:
            log.warning(f"Channel post error (trying tg_send): {e}")
            try:
                sys.path.insert(0, os.path.join(PROJECT_DIR, "strategies", "vilona_tradefx"))
                import telebot
                bot = telebot.TeleBot(os.getenv("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", ""))
                bot.send_message(os.getenv("MAPPING_CHANNEL_ID", ""), text, parse_mode="HTML")
                log.info("✅ Posted via telebot fallback")
            except Exception as e2:
                log.error(f"All posting methods failed: {e2}")
        
        # ── Log to trade log ──
        log_signal(sig)
        any_signal = True
        
        # ── Post to bridge ──
        try:
            import urllib.request, json
            bridge_url = "https://phantomfx.aitradepulse.com/signal?api_key=VT-DONOR-0"
            req = urllib.request.Request(
                bridge_url,
                data=json.dumps(sig).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
        
        if not args.quiet:
            log.info(f"🔥 {sym} signal published: {sig['action']} @ ${sig['entry']} Grade {sig['grade']}")
    
    if not any_signal and not args.quiet:
        log.info("No signals generated for any asset")
    return 0

if __name__ == "__main__":
    sys.exit(main())
