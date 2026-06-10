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

# ── Killzone routing ───────────────────────────────────────────────
FOREX_METAL_PAIRS = {"XAUUSD", "USOIL"}       # London/NY only
CRYPTO_PAIRS = {"BTCUSD", "ETHUSD"}            # 24/7 — bypass killzone

def killzone_active_wib(now=None):
    """Return (london_active, ny_active). London=14-17WIB, NY=19-22WIB."""
    if now is None:
        now = datetime.now(WIB)
    h = now.hour
    return (14 <= h < 17, 19 <= h < 22)

def trading_allowed(symbol):
    """Check if trading is allowed for this symbol based on killzone routing."""
    if symbol in CRYPTO_PAIRS:
        return True  # Crypto 24/7
    if symbol in FOREX_METAL_PAIRS:
        lkz, nykz = killzone_active_wib()
        return lkz or nykz  # Only London or NY
    return True  # Stocks, others — always allowed during weekdays

def load_trade_log():
    path = os.path.join(PROJECT_DIR, "..", "data", "trade_log.json")
    try:
        with open(path) as f:
            return json.load(f)
    except: return []

def is_duplicate(log, signal):
    """Cek apakah signal sudah pernah dipost (dedup by symbol + action + time gap)."""
    action = signal.get("action", "")
    symbol = signal.get("symbol", "")
    now = time.time()
    
    for s in log[-30:]:  # cek 30 terakhir
        if (s.get("symbol") == symbol and 
            s.get("action") == action):
            # Cek time gap — minimal 180 menit (3 jam) untuk sinyal yang sama
            try:
                t1 = datetime.fromisoformat(s.get("timestamp", "")).timestamp()
                if now - t1 < 10800:  # 180 menit
                    return True
            except:
                pass  # kalau gak bisa parse, lanjut cek
    return False

# ── Persistent dedup cache (survives across cron runs) ──────────────
DEDUP_CACHE_PATH = os.path.join(PROJECT_DIR, "..", "data", "dedup_cache.json")
DEDUP_WINDOW_SEC = 7200  # 2 jam — jangan kirim sinyal yg sama dalam 2 jam

def load_dedup_cache():
    try:
        with open(DEDUP_CACHE_PATH) as f:
            return json.load(f)
    except: return {}

def save_dedup_cache(cache):
    os.makedirs(os.path.dirname(DEDUP_CACHE_PATH), exist_ok=True)
    with open(DEDUP_CACHE_PATH, "w") as f:
        json.dump(cache, f)

def is_cached_duplicate(symbol, action):
    """Check persistent dedup cache — lebih reliable dari trade_log."""
    cache = load_dedup_cache()
    key = f"{symbol}:{action}"
    last_ts = cache.get(key, 0)
    if time.time() - last_ts < DEDUP_WINDOW_SEC:
        return True
    cache[key] = time.time()
    save_dedup_cache(cache)
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

    # ── MTF Scan — Multi-Asset + Killzone Routing ──
    assets = [
        {"symbol": "XAUUSD", "class": "forex_metal"},
    ]
    results = {}
    
    for asset in assets:
        sym = asset["symbol"]
        asset_class = asset["class"]
        
        # Killzone gate for forex/metals
        if asset_class == "forex_metal" and not trading_allowed(sym):
            if not args.quiet:
                lkz, nykz = killzone_active_wib()
                log.info(f"  {sym}: SKIPPED — outside London/NY killzone (London:{'🟢' if lkz else '🔴'} NY:{'🟢' if nykz else '🔴'})")  # noqa: E501
            continue
        
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
            # 1. Persistent cache check (survives cron restarts)
            if is_cached_duplicate(sig.get("symbol", sym), sig.get("action", "")):
                if not args.quiet:
                    log.info(f"  {sym}: CACHED duplicate — skipped")
                continue
            # 2. Trade log check (backward compat)
            trade_log = load_trade_log()
            if is_duplicate(trade_log, sig):
                if not args.quiet:
                    log.info(f"  {sym}: Duplicate signal — skipped")
                continue
        
        # ── Post to Telegram ──
        text = format_signal_telegram(sig)
        
        try:
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
        
        # ── Post to local bridge directly ──
        try:
            import urllib.request, json
            bridge_url = "http://localhost:8765/signal?api_key=VT-MASTER-734AD731F5FB"
            bridge_sig = dict(sig)
            bridge_sig["tp"] = sig.get("tp1", 0)  # EA baca field tp, bukan tp1
            req = urllib.request.Request(
                bridge_url,
                data=json.dumps(bridge_sig).encode(),
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
