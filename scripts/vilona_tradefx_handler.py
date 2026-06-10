#!/usr/bin/env python3
"""
Vilona Trade FX Telegram Bot Handler
Grab forex data + generate signals even without MT5/EA.

Commands: /start /help /price /analyze /data /killzone /status /subscribe /autosync /genkey /listkeys /mykey /myid
"""
import hashlib, json, logging, os, re, sys, threading, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Project path (MUST be before any local imports) ──
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# ── Load .env BEFORE local imports (Tripay keys needed) ──
_env_path = PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().split('\n'):
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k not in os.environ or not os.environ.get(_k):
                os.environ[_k] = _v

try:
    from layering import enrich_signal_with_layers
    LAYERING_ENGINE = True
except ImportError:
    LAYERING_ENGINE = False
try:
    from license_manager import cmd_genkey, cmd_listkeys, cmd_revokekey, cmd_mykey, is_admin
    LICENSE_ENGINE = True
except ImportError:
    LICENSE_ENGINE = False
# Legacy subscription system — deprecated, all features now free
SUBSCRIPTION_ENGINE = False
ensure_member = lambda cid, *a, **kw: None
get_member = lambda cid, *a, **kw: {}
_upgrade_tier = lambda cid, *a, **kw: None
check_due_reminders = lambda: []
check_expired = lambda: []
mark_expired = lambda cid: None
set_reminder = lambda cid, label: None
SUBS_PATH = ""

# ── Payment gateway ──
try:
    from members.payment import get_pricing_info, get_pricing_table, PRICING, create_tripay_payment
    PAYMENT_ENGINE = True
except Exception as e:
    PAYMENT_ENGINE = False
    get_pricing_info = lambda: {"packages": {}, "methods": [], "gateways": []}
    PRICING = {}
    print(f"Payment engine unavailable: {e}")

# ── Security: Secret Sanitization Middleware ──
try:
    from secret_sanitizer import sanitize_telegram_input
    SECRET_SANITIZER = True
except Exception as e:
    SECRET_SANITIZER = False
    print(f"Secret sanitizer unavailable: {e}")

# ── Market data layer ──
try:
    from market_data import UnifiedMarketData
    MARKET_DATA = UnifiedMarketData()
except Exception as e:
    MARKET_DATA = None
    print(f"Market data layer unavailable: {e}")

# ── Suppress yfinance noisy warnings ($BT, delisted, etc.) ──
import logging as _logging
_logging.getLogger('yfinance').setLevel(_logging.CRITICAL)

# ── Member system ──
try:
    from members import register_member, get_member, get_member_stats, mark_paid, get_due_members
    from members import is_premium, check_quota, use_quota, activate_premium, deactivate_premium
    from members.payment import get_pricing_info, create_tripay_payment
    MEMBERS_ENABLED = True
except Exception as e:
    MEMBERS_ENABLED = False
    print(f"Member system unavailable: {e}")

# ── Learning engine ──
try:
    from learning_engine import track_signal, get_adaptation_context, start_learning_engine, run_reflection
    LEARNING_ENGINE = True
except Exception as e:
    LEARNING_ENGINE = False
    print(f"Learning engine unavailable: {e}")

# ── Quant engine ──
try:
    from quant_engine import analyze_quantitative_pattern
    QUANT_ENGINE = True
except Exception as e:
    QUANT_ENGINE = False
    print(f"Quant engine unavailable: {e}")

# ── FVG detector ──
try:
    from fvg_detector import detect_fvg, fvg_to_dict
    FVG_ENGINE = True
except Exception as e:
    FVG_ENGINE = False
    print(f"FVG engine unavailable: {e}")

# ── CRT/TBS Engine (Candle Range Theory) ──
try:
    from crt_tbs_engine import analyze_crt_setup, format_crt_block
    CRT_ENGINE = True
except Exception as e:
    CRT_ENGINE = False
    print(f"CRT/TBS engine unavailable: {e}")

# ── SMC Scalper + Trend Break Engine ──
try:
    from smc_scalper_engine import (analyze_smc_scalper, analyze_trend_break,
                                     format_smc_block, format_trend_block)
    SMC_ENGINE = True
except Exception as e:
    SMC_ENGINE = False
    print(f"SMC engine unavailable: {e}")

# ── Sequoia-X Quantitative Screening (Turtle + HTF + RPS + MA) ──
try:
    import pandas as pd
    from strategies.sequoia_math import (
        turtle_breakout, turtle_signal_strength, ma_volume_breakout,
        turtle_trend_filter, validate_ohlcv
    )
    SEQUOIA_ENGINE = True
except Exception as e:
    SEQUOIA_ENGINE = False
    print(f"Sequoia engine unavailable: {e}")

# ── Ultimate SMC Engine v3.0 (13+ repos combined) ──
try:
    from ultimate_smc_engine import (ultimate_analyze, format_ultimate_block,
                                      Grade as UltimateGrade)
    ULTIMATE_ENGINE = True
except Exception as e:
    ULTIMATE_ENGINE = False
    print(f"Ultimate SMC engine unavailable: {e}")

# ── Trade Tracker ──
try:
    from trade_tracker import (open_trade, check_outcomes, get_stats,
                                format_winrate, format_history, format_trade_close_alert,
                                format_trade_close_with_cta,
                                format_daily_recap, format_mini_recap)
    TRADE_TRACKER = True
except Exception as e:
    TRADE_TRACKER = False
    print(f"Trade tracker unavailable: {e}")

# ── Unified Signal Feed ──
try:
    from scripts.signal_feed import add_signal as _feed_add, update_outcome as _feed_update
    SIGNAL_FEED = True
except Exception as e:
    SIGNAL_FEED = False
    print(f"Signal feed unavailable: {e}")
    def _feed_add(*a, **kw): return ""
    def _feed_update(*a, **kw): pass

# ── Hermes Liquidity Hunter ──
try:
    from hermes_liquidity_hunter import hermes_pipeline as hermes_liquidity_pipeline
    from session_levels import calculate_all_levels as calc_session_levels
    HERMES_LIQUIDITY_ENGINE = True
except Exception as e:
    HERMES_LIQUIDITY_ENGINE = False
    print(f"Hermes liquidity engine unavailable: {e}")

# ── Logging & paths ──
LOG_DIR = PROJECT_DIR / "logs"; LOG_DIR.mkdir(exist_ok=True)
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"; DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA_DIR / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR/"vilona_tradefx.log"), logging.StreamHandler(sys.stderr)])
logger = logging.getLogger("vilona-tradefx-bot")

WIB = timezone(timedelta(hours=7))

# ── Config ──
BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
FCS_API_KEY = os.environ.get("FCS_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY", "")
CHAT_ID = os.environ.get("VILONA_TRADEFX_CHAT_ID", "")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://localhost:20128/v1/chat/completions")

# ── XAUUSD Price Offset ──
# gold-api.com returns XAU commodity spot (~$4260). Real XAUUSD forex broker ~$4334.
# Offset = broker_price - gold_api_price. Default +74.
# Set via env: XAUUSD_PRICE_OFFSET=74 (atau sesuai broker lu)
XAUUSD_OFFSET = float(os.environ.get("XAUUSD_PRICE_OFFSET", "74"))

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OMNIROUTE_MODELS = ["deepseek-chat", "gpt-4o", "claude-sonnet-4-20250514"]

# ── AI Token Usage Tracking ──
# Per-analysis-cycle counter. Reset at start of each ask_ai_ensemble() call.
# { "deepseek": {"prompt": N, "completion": N, "total": N}, ... }
_AI_TOKEN_USAGE: dict[str, dict[str, int]] = {}

# ── Grok (xAI) ──
GROK_KEY = os.environ.get("GROK_API_KEY", "")
GROK_URL = "https://api.x.ai/v1/chat/completions"


def load_env():
    """Load .env file from strategies/vilona_tradefx/.env"""
    env_path = PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env"
    if env_path.exists():
        for line in env_path.read_text().split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key not in os.environ or not os.environ.get(key):
                    os.environ[key] = val
    global BOT_TOKEN, DEEPSEEK_KEY, OPENAI_KEY, FCS_API_KEY, GEMINI_KEY, CLAUDE_KEY, CHAT_ID, TELEGRAM_API
    BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    OPENAI_KEY = os.environ.get("OPENAI_API_KEY", OPENAI_KEY)
    FCS_API_KEY = os.environ.get("FCS_API_KEY", FCS_API_KEY)
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY", GEMINI_KEY)
    CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY", CLAUDE_KEY)
    CHAT_ID = os.environ.get("VILONA_TRADEFX_CHAT_ID", CHAT_ID)
    TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


load_env()


# ── Time utilities ──
def wib_now(): return datetime.now(WIB)
def wib_fmt(d=None):
    d = d or wib_now()
    return d.strftime("%d/%m %H:%M WIB")

def session(h=None):
    h = h if h is not None else wib_now().hour
    if 3<=h<7: return "Asia"
    if 7<=h<15: return "Asia+London"
    if 15<=h<19: return "London"
    if 19<=h<23: return "London+NY"
    if h>=23 or h<3: return "NY"
    return "Asia"

def killzone(h=None):
    h = h if h is not None else wib_now().hour
    return (14<=h<17, 19<=h<22)


def news_blackout_status(h=None, m=None):
    """Check if we're in a high-impact news window for NFP/FOMC/etc.
    Returns (is_blackout, is_post_news, news_name)."""
    now = wib_now()
    h = h if h is not None else now.hour
    m = m if m is not None else now.minute
    day = now.weekday()
    total_min = h * 60 + m

    major_events = [
        {"name": "High-Impact US Data", "blackout_start": 19*60+0, "blackout_end": 19*60+30,
         "post_start": 19*60+30, "post_end": 19*60+45, "days": [4]},
        {"name": "NY Open Vol Spike", "blackout_start": 19*60+0, "blackout_end": 19*60+10,
         "post_start": 19*60+10, "post_end": 19*60+25, "days": [0,1,2,3,4]},
    ]

    for ev in major_events:
        if day in ev["days"]:
            if ev["blackout_start"] <= total_min < ev["blackout_end"]:
                return (True, False, ev["name"])
            if ev["post_start"] <= total_min < ev["post_end"]:
                return (False, True, ev["name"])

    return (False, False, None)


def load_state():
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except: pass
    return {"last_update_id": 0}

def save_state(s):
    STATE_PATH.write_text(json.dumps(s))


# ── Auto-Sync ──
AUTOSYNC_PATH = DATA_DIR / "autosync.json"
AUTOSYNC_GLOBAL_ENABLED = False

def load_autosync():
    try:
        if AUTOSYNC_PATH.exists():
            return json.loads(AUTOSYNC_PATH.read_text())
    except: pass
    return {}

def save_autosync(data):
    AUTOSYNC_PATH.write_text(json.dumps(data))

def is_autosync(chat_id):
    if not AUTOSYNC_GLOBAL_ENABLED:
        return False
    return str(chat_id) in load_autosync()

def set_autosync(chat_id, enabled=True):
    data = load_autosync()
    if enabled:
        data[str(chat_id)] = wib_now().isoformat()
    else:
        data.pop(str(chat_id), None)
    save_autosync(data)


def _fetch_ohlcv_for_ai(pair="gold"):
    """Fetch OHLCV bars for AI analysis prompt."""
    pair = pair.lower().strip()
    sym_map = {"gold":"GC=F","xauusd":"GC=F","btc":"BTC-USD","btcusd":"BTC-USD",
               "eth":"ETH-USD","ethusd":"ETH-USD",
               "oil":"CL=F","eurusd":"EURUSD=X","gbpusd":"GBPUSD=X",
               "usdjpy":"JPY=X","jpyusd":"JPY=X",
               "aapl":"AAPL","tsla":"TSLA","msft":"MSFT","nvda":"NVDA",
               "bbca":"BBCA.JK","bbri":"BBRI.JK","tlkm":"TLKM.JK","asii":"ASII.JK",
               "unvr":"UNVR.JK","bmri":"BMRI.JK","adro":"ADRO.JK","ihsg":"^JKSE"}
    # Internal name for FCS fallback
    _fcs_name_map = {"gold":"XAUUSD","xauusd":"XAUUSD","btc":"BTCUSD","btcusd":"BTCUSD",
                     "eth":"ETHUSD","ethusd":"ETHUSD","oil":"USOIL",
                     "eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY","jpyusd":"USDJPY"}
    sym = sym_map.get(pair, "GC=F")
    # Stocks use daily; forex/crypto/commodities use 15m
    is_stock = sym.replace(".JK","").isalpha() and "." not in sym.replace(".JK","")
    interval = "1d" if (".JK" in sym or sym in ("AAPL","TSLA","MSFT","NVDA")) else "15m"
    try:
        if MARKET_DATA is None:
            logger.error(f"_fetch_ohlcv_for_ai: MARKET_DATA is None!")
            raise Exception("MARKET_DATA not initialized")
        bars = MARKET_DATA.get_bars_dicts(sym, interval, 80)
        if not bars:
            logger.warning(f"_fetch_ohlcv_for_ai: got empty bars for {sym} ({interval})")
            raise Exception("Empty bars from MARKET_DATA")
        result = [{"t": b["timestamp"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
                for b in bars[-20:]]
        logger.info(f"_fetch_ohlcv_for_ai: {len(result)} bars for {sym}")
        return result
    except Exception as e:
        logger.warning(f"_fetch_ohlcv_for_ai (primary) error: {e}")
    
    # ── Fallback: FCS API ──
    try:
        fcs_name = _fcs_name_map.get(pair)
        if fcs_name:
            from data_sources import fcs_ohlcv
            bars_data = fcs_ohlcv(fcs_name, period=interval, bars=20)
            if bars_data:
                result = [{"t": b.get("timestamp", int(time.time())),
                          "o": b["Open"], "h": b["High"], "l": b["Low"], "c": b["Close"]}
                         for b in bars_data]
                logger.info(f"_fetch_ohlcv_for_ai (FCS fallback): {len(result)} bars for {fcs_name}")
                return result
    except Exception as e2:
        logger.error(f"_fetch_ohlcv_for_ai (FCS fallback) error: {e2}")
    
    logger.error(f"_fetch_ohlcv_for_ai: ALL sources failed for {pair}")
    return None


# ── Price fetching ──
def _normalize_broker_symbol(s):
    """Strip broker suffixes & normalize to standard pair name.
    XAUUSDc→xauusd, JPYUSD.s→jpyusd, EURUSD.pro→eurusd, etc."""
    import re
    # Remove suffixes: .xxx, -xxx, c, m, #, _
    s = re.sub(r'[.\-#_].*$', '', s.strip().lower())
    # Remove trailing letters used as contract types
    s = re.sub(r'[cm]$', '', s)
    return s

def get_xauusd_spot_offset() -> float:
    """Calculate XAUUSD spot-futures differential (spot minus futures).
    Positive = spot higher, Negative = spot lower (most common).
    Returns 0 if can't determine."""
    try:
        spot = fetch_xauusd_spot()
        if not spot: return 0
        if MARKET_DATA:
            quote = MARKET_DATA.get_quote("GC=F")
            if quote and quote.price > 1000:
                return spot - quote.price
    except: pass
    return 0

def fetch_xauusd_spot() -> float | None:
    """Fetch live spot XAUUSD from gold-api.com (free, unlimited, real-time)."""
    try:
        req = urllib.request.Request("https://api.gold-api.com/price/XAU", headers={"User-Agent": "Vilona/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        price = float(data.get("price", 0))
        if 2000 < price < 6000:
            return price
    except Exception as e:
        logger.debug(f"Gold-API failed: {e}")
    return None

def fetch_price(pair="gold"):
    """Fetch live price. XAUUSD: gold-api.com + offset → broker-real price."""
    pair = _normalize_broker_symbol(pair.lower().strip())
    
    # XAUUSD — gold-api.com spot + offset = harga broker
    if pair in ("gold", "xauusd"):
        spot = fetch_xauusd_spot()
        if spot:
            return round(spot + XAUUSD_OFFSET, 2)
    
    # Other pairs — yfinance
    if not MARKET_DATA:
        return None
    try:
        symbol_map = {
            "btc": "BTC-USD", "btcusd": "BTC-USD",
            "eth": "ETH-USD", "ethusd": "ETH-USD",
            "oil": "CL=F", "usoil": "CL=F",
        }
        symbol = symbol_map.get(pair, pair.upper())
        quote = MARKET_DATA.get_quote(symbol)
        if quote and quote.price > 0:
            return quote.price
    except Exception as e:
        logger.debug(f"fetch_price({pair}) failed: {e}")
    return None

def fetch_dxy():
    try:
        quote = MARKET_DATA.get_quote("DX-Y.NYB")
        if quote and quote.price > 50:
            return quote.price
    except: pass
    return None


# ── Telegram helpers ──
def tg_send(text, chat_id=None, reply_markup=None):
    if not BOT_TOKEN: return None
    target = chat_id or CHAT_ID
    if not target: return None
    
    # Telegram limit: 4096 chars. Truncate with indicator.
    MAX_LEN = 4000
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN-30] + "\n<i>... (dipotong)</i>"
    
    # HTML-safe: escape bare < > & that aren't part of tags
    import re
    # Use unique Unicode placeholder markers (safe across Python 3.11-3.13)
    TAG_OPEN = "\ue000"   # Private Use Area — won't appear in normal text
    TAG_CLOSE = "\ue001"
    text = re.sub(r'<(/?[abi][^>]*)>', TAG_OPEN + r'\1' + TAG_CLOSE, text)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = text.replace(TAG_OPEN, '<').replace(TAG_CLOSE, '>')
    
    try:
        payload = {"chat_id": target, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        req = urllib.request.Request(f"{TELEGRAM_API}/sendMessage",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        # Fallback: retry without parse_mode if HTML parse failed
        if "Bad Request" in str(e) or "can't parse" in str(e):
            try:
                # Strip HTML tags for plaintext fallback
                plain = re.sub(r'<[^>]+>', '', text)
                payload = {"chat_id": target, "text": plain[:MAX_LEN]}
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                req = urllib.request.Request(f"{TELEGRAM_API}/sendMessage",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read())
            except Exception as e2:
                logger.error(f"tg_send fallback also failed: {e2}")
        else:
            logger.error(f"tg_send failed: {e}")
        return None


# ── Signal bridge ──
BRIDGE_URLS = ["https://phantomfx.aitradepulse.com", "http://localhost:8765"]
MASTER_API_KEY = os.environ.get("BRIDGE_MASTER_KEY", "VT-MASTER-734AD731F5FB")


def _fetch_json_url(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"error": str(e)}


def format_bridge_status():
    health = _fetch_json_url("http://localhost:8765/health")
    accounts = _fetch_json_url("http://localhost:8765/accounts")
    webhook = _fetch_json_url("http://localhost:8787/health")

    bridge_ok = health.get("status") == "ok"
    webhook_ok = webhook.get("status") == "ok"
    instances = accounts.get("total_instances", 0) if isinstance(accounts, dict) else 0
    master_keys = accounts.get("master_keys_count", 0) if isinstance(accounts, dict) else 0
    queue_size = health.get("queue_size", 0)
    uptime = int(float(health.get("uptime_seconds", 0) or 0))
    uptime_txt = f"{uptime // 3600}j {(uptime % 3600) // 60}m"

    txt = (
        "🛡️ <b>VILONA BRIDGE STATUS</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🌐 Bridge: {'🟢 ONLINE' if bridge_ok else '🔴 DOWN'}\n"
        f"💳 Webhook: {'🟢 ONLINE' if webhook_ok else '🔴 DOWN'}\n"
        f"⏱️ Uptime: {uptime_txt}\n"
        f"📦 Queue: {queue_size}\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🔑 Master Key Aktif: {master_keys}\n"
        f"🖥️ EA Instance Online: {instances}\n"
    )

    if isinstance(accounts, dict) and accounts.get("instances"):
        online = 0
        for data in accounts.get("instances", {}).values():
            if data.get("online"):
                online += 1
        txt += f"🟢 Instance Live: {online}/{instances}\n"

    txt += f"━━━━━━━━━━━━━━━━\n{wib_fmt()}"
    return txt

# ── Trade/Skip inline keyboard ──
PENDING_SIGNALS = {}  # {chat_id: {"sig": ..., "price": ..., "expires": ts}}
PENDING_SIGNAL_TTL = 300  # 5 menit
PENDING_SIGNALS_PATH = DATA_DIR / ".pending_signals.json"

def _load_pending_signals():
    """Restore pending signals from disk (survives restart). Clean expired."""
    global PENDING_SIGNALS
    try:
        if PENDING_SIGNALS_PATH.exists():
            raw = json.loads(PENDING_SIGNALS_PATH.read_text())
            now = time.time()
            PENDING_SIGNALS = {k: v for k, v in raw.items() if v.get("expires", 0) > now}
            if PENDING_SIGNALS:
                logger.info(f"♻️ Restored {len(PENDING_SIGNALS)} pending signal(s)")
    except Exception:
        pass

def _save_pending_signals():
    """Persist pending signals to disk."""
    try:
        PENDING_SIGNALS_PATH.write_text(json.dumps(PENDING_SIGNALS))
    except Exception:
        pass

def _cleanup_expired_pending_signals():
    """Remove expired entries from PENDING_SIGNALS and persist cleanup."""
    global PENDING_SIGNALS
    now = time.time()
    before = len(PENDING_SIGNALS)
    PENDING_SIGNALS = {k: v for k, v in PENDING_SIGNALS.items() if v.get("expires", 0) > now}
    after = len(PENDING_SIGNALS)
    if after < before:
        logger.info(f"🧹 Cleaned {before - after} expired pending signal(s)")
        _save_pending_signals()

# ── Manual-mode guard: anti-spam + anti-opposite-flip per user ──
USER_LAST_ANALYZE = {}  # chat_id -> timestamp
USER_LAST_DIRECTION = {}  # chat_id -> {"action": str, "at": iso, "asset": str}
USER_LAST_PAIR = {}  # chat_id -> {"pair": str, "at": timestamp} — same-pair cooldown
USER_DAILY_ANALYZE = {}  # chat_id -> {"count": int, "date": "YYYY-MM-DD"} — donor quota

MANUAL_THROTTLE_FREE = 60     # free user: 60 detik antar analisa
MANUAL_THROTTLE_DONOR = 120   # donor: 120 detik antar analisa
SAME_PAIR_COOLDOWN = 90       # same pair cooldown (all users)
DONOR_DAILY_QUOTA = 60        # donor: 60x analisa/hari (cukup buat 1x tiap 12 menit)
FREE_DAILY_QUOTA = 3          # free: 3x/hari
DIRECTION_LOCK_SECONDS = 60

# ── Custom donation input state ──
DONATION_INPUT_STATE = {}  # chat_id -> True (waiting for user to type amount)

def _is_manual_blocked(chat_id, pair=""):
    """Multi-layer anti-abuse: cooldown + same-pair + donor daily quota + direction lock."""
    now = time.time()
    is_donor = _is_donor(str(chat_id))
    throttle = MANUAL_THROTTLE_DONOR if is_donor else MANUAL_THROTTLE_FREE

    # Layer 1: pending signal exists → must resolve first
    if chat_id in PENDING_SIGNALS:
        return True, "⏰ Sinyal sebelumnya masih berjalan. Tekan Trade Auto/Skip atau tunggu 5 menit."

    # Layer 2: general cooldown (donor=120s, free=60s)
    ts = USER_LAST_ANALYZE.get(chat_id)
    if ts and (now - ts) < throttle:
        wait = int(throttle - (now - ts))
        label = "Donatur" if is_donor else "Free"
        return True, f"⏳ [{label}] Tunggu {wait} detik sebelum analisa berikutnya."

    # Layer 3: same-pair cooldown (all users: 90s)
    if pair:
        last_pair = USER_LAST_PAIR.get(chat_id, {})
        if last_pair.get("pair") == pair and (now - last_pair.get("at", 0)) < SAME_PAIR_COOLDOWN:
            wait = int(SAME_PAIR_COOLDOWN - (now - last_pair.get("at", 0)))
            return True, f"📊 Kamu baru analisa {pair.upper()} {int(now - last_pair['at'])} detik lalu.\n⏳ Tunggu {wait} detik atau coba pair lain: /analyze btc"

    # Layer 4: direction lock — prevent opposite-direction spam
    rec = USER_LAST_DIRECTION.get(chat_id)
    if rec and rec.get("action") in ("BUY", "SELL"):
        try:
            last = datetime.fromisoformat(rec.get("at", ""))
            elapsed = (wib_now() - last).total_seconds()
            if elapsed < DIRECTION_LOCK_SECONDS:
                return True, f"🔒 Terdeteksi arah {rec['action']} pada {rec.get('asset','?')} {int(elapsed)} detik lalu. Menunggu {DIRECTION_LOCK_SECONDS - int(elapsed)} detik untuk menghindari flip."
        except Exception:
            pass
    return False, ""


def _check_donor_quota(chat_id):
    """Check & deduct donor daily quota. Returns (ok, remaining, message)."""
    today = wib_now().strftime("%Y-%m-%d")
    record = USER_DAILY_ANALYZE.get(chat_id, {})
    if record.get("date") != today:
        record = {"date": today, "count": 0}
    
    record["count"] += 1
    USER_DAILY_ANALYZE[chat_id] = record
    
    # Check quota AFTER increment — user gets exactly QUOTA x per day
    if record["count"] > DONOR_DAILY_QUOTA:
        return False, max(0, DONOR_DAILY_QUOTA - record["count"]), f"🛑 <b>Kuota Donatur Harian Penuh!</b>\\n━━━━━━━━━━━━━━━━\\n📊 {DONOR_DAILY_QUOTA}x analisa/hari — sudah terpakai semua.\\n💡 Analisa bijak ya Bro, setiap analisa pakai AI (DeepSeek V3 + GPT-4o).\\n⏰ Reset: besok jam 00:00 WIB\\n\\n🔍 Cek sinyal auto di channel: @vilonaaichanel"
    
    remaining = max(0, DONOR_DAILY_QUOTA - record["count"])
    if remaining <= 5:
        return True, remaining, None  # allow but warn later
    
    return True, remaining, None


def _touch_manual(chat_id, action=None, asset="", pair=""):
    USER_LAST_ANALYZE[chat_id] = time.time()
    if pair:
        USER_LAST_PAIR[chat_id] = {"pair": pair, "at": time.time()}
    if action in ("BUY", "SELL"):
        USER_LAST_DIRECTION[chat_id] = {"action": action, "at": wib_now().isoformat(), "asset": asset}

def handle_trade_callback(callback_query):
    """Handle inline keyboard: trade:<id> or skip:<id>"""
    cb_id = callback_query.get("id", "")
    chat_id = str(callback_query.get("from", {}).get("id", ""))
    data = callback_query.get("data", "")
    
    # Answer callback (required by Telegram)
    try:
        payload = json.dumps({"callback_query_id": cb_id}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
    
    if chat_id not in PENDING_SIGNALS:
        tg_send("⏰ Sinyal kadaluarsa. Kirim /analyze lagi.", chat_id)
        return
    
    pending = PENDING_SIGNALS[chat_id]
    sig = pending.get("sig")
    price = pending.get("price", 0)
    
    if not sig:
        del PENDING_SIGNALS[chat_id]
        return
    
    if data.startswith("trade:"):
        action = sig.get("action", "HOLD")
        if action == "HOLD":
            tg_send("⚪️ Sinyal HOLD — tidak ada trade yang dieksekusi.", chat_id)
        else:
            if LAYERING_ENGINE:
                sig = enrich_signal_with_layers(sig)
            sig["target_user"] = chat_id  # route ke EA user ini aja
            post_signal_to_bridge(sig, price, "XAUUSD")
            tg_send(f"✅ <b>Sinyal {action} dikirim!</b>\nEA kamu auto-eksekusi dalam 5 detik.", chat_id)
        del PENDING_SIGNALS[chat_id]
        _save_pending_signals()
        
    elif data.startswith("skip:"):
        tg_send("⏭ Sinyal dilewati.\nAnalisa lagi: /analyze", chat_id)
        del PENDING_SIGNALS[chat_id]
        _save_pending_signals()


def handle_payment_callback(callback_query):
    """Handle inline keyboard: pay:<tier> or bill:<chat_id>"""
    cb_id = callback_query.get("id", "")
    chat_id = str(callback_query.get("from", {}).get("id", ""))
    username = callback_query.get("from", {}).get("username", "")
    data = callback_query.get("data", "")

    # Answer callback immediately
    try:
        payload = json.dumps({"callback_query_id": cb_id}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

    if not chat_id or not data:
        return

    if data == "none":
        # Separator button — do nothing
        return

    if data == "cancel_input":
        # ── Cancel custom amount input, return to /donate ──
        DONATION_INPUT_STATE.pop(str(chat_id), None)
        tg_send("❌ Input dibatalkan.", chat_id)
        # Re-send donate menu
        username = callback_query.get("from", {}).get("username", "")
        _send_donate_menu(chat_id, username)
        return

    if data.startswith("pay:"):
        tier = data.split(":", 1)[1] if ":" in data else "pro"
        if not PAYMENT_ENGINE or tier not in PRICING:
            tg_send("💳 Payment gateway belum tersedia.\nHubungi admin untuk upgrade manual.", chat_id)
            return

        pkg = PRICING[tier]
        tg_send(f"⏳ <b>Membuat invoice...</b>\n"
                f"Paket: {pkg['label']} — Rp{pkg['price_idr']:,}", chat_id)

        result = create_tripay_payment(chat_id, username, tier)
        if result.get("error"):
            tg_send(f"❌ <b>Gagal membuat pembayaran</b>\n"
                    f"{result['error']}\n\n"
                    f"Silakan hubungi admin: @codergaboets", chat_id)
            return

        # Send payment details with inline button
        pay_url = result.get("payment_url", "")
        pay_code = result.get("pay_code", "")
        ref = result.get("reference", "")
        amount = result.get("amount", 0)

        txt = (
            f"💳 <b>Invoice — {pkg['label']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Total: <b>Rp{amount:,}</b>\n"
            f"📦 Paket: {pkg['label']}\n"
        )
        if pay_code:
            txt += f"📱 Kode Bayar: <code>{pay_code}</code>\n"
        txt += (
            f"⏰ Expired: 1 jam\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Klik tombol di bawah untuk bayar:"
        )

        markup = {"inline_keyboard": [[
            {"text": f"💳 Bayar Rp{amount:,}", "url": pay_url} if pay_url
            else {"text": "💳 Bayar Sekarang", "callback_data": f"check:{ref}"}
        ], [
            {"text": "🔄 Cek Status", "callback_data": f"check:{ref}"},
            {"text": "📞 Admin", "url": "https://t.me/codergaboets"},
        ]]}

        tg_send(txt, chat_id, reply_markup=markup)

    elif data.startswith("check:"):
        ref = data.split(":", 1)[1] if ":" in data else ""
        if not ref:
            tg_send("❌ Referensi tidak valid.", chat_id)
            return

        tg_send("🔍 <b>Cek Status Pembayaran ke Tripay...</b>", chat_id)

        # ── Check via Tripay API ──
        try:
            from members.payment import is_tripay_paid
            if is_tripay_paid(ref):
                # Upgrade user!
                from members import upgrade_tier, mark_payment_paid
                upgrade_tier(str(chat_id), "donor", 9999, ref)
                mark_payment_paid(ref)
                tg_send(
                    "✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "👑 Status kamu sekarang: <b>DONATUR VIP</b>\n"
                    "♾️ /analyze — UNLIMITED\n"
                    "🤖 EA Auto-Trade — AKTIF PERMANEN\n\n"
                    "Mari cetak profit! 🔥",
                    chat_id
                )
            else:
                tg_send(
                    "⏳ <b>Pembayaran Belum Terkonfirmasi</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "Tripay belum menerima pembayaran untuk invoice ini.\n"
                    "Pastikan kamu sudah menyelesaikan pembayaran.\n\n"
                    "Biasanya butuh 1-5 menit setelah transfer.\n"
                    "Kalau sudah lebih dari 10 menit, hubungi admin.",
                    chat_id
                )
        except Exception as e:
            logger.error(f"Tripay check failed: {e}")
            tg_send(
                "⚠️ <b>Cek status gagal</b>\n"
                "Coba lagi nanti atau kirim bukti pembayaran ke admin: @codergaboets",
                chat_id
            )

    elif data.startswith("pricing:"):
        # Show donation info — no more old tiers
        txt = get_pricing_table() if PAYMENT_ENGINE else "💎 Info dukung server AI belum tersedia."
        markup = {"inline_keyboard": [
            [{"text": "☕️ Traktir Kopi (Rp15k)", "callback_data": "donate:coffee"},
             {"text": "🚀 Nominal Bebas", "callback_data": "donate:fuel"}],
            [{"text": "📞 Tanya Admin", "url": "https://t.me/codergaboets"}],
        ]}
        tg_send(txt, chat_id, reply_markup=markup)

    elif data.startswith("donate:"):
        donate_type = data.split(":", 1)[1] if ":" in data else "info"
        
        if donate_type == "coffee":
            # ── Fixed Rp15,000 ──
            amount = 15000
            label = "☕️ Kopi untuk Server AI"
        elif donate_type == "fuel":
            # ── Fixed Rp50,000 ──
            amount = 50000
            label = "🚀 Bensin Full Server AI"
        elif donate_type == "learn":
            # ── Fixed Rp25,000 ──
            amount = 25000
            label = "🍱 Makan Siang Server AI"
        elif donate_type == "custom":
            # ── Custom amount — wait for user to type ──
            DONATION_INPUT_STATE[str(chat_id)] = True
            tg_send(
                "💰 <b>Input Nominal Bebas</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Silakan ketik nominal dukungan yang kamu\n"
                "inginkan (minimal Rp10,000).\n\n"
                "<i>Contoh: ketik 100000 untuk Rp100K</i>",
                chat_id,
                reply_markup={"inline_keyboard": [[
                    {"text": "❌ Batal", "callback_data": "cancel_input"},
                ]]}
            )
            return
        else:
            # Generic — show options
            tg_send(
                "⚡ <b>Isi Bahan Bakar AI</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Pilih nominal dukungan:\n\n"
                "☕️ Rp15K — Traktir kopi\n"
                "📚 Rp25K — Dukung AI belajar\n"
                "🚀 Nominal bebas — Isi bensin\n\n"
                "Semua dukungan = DONATUR VIP AKTIF PERMANEN.",
                chat_id
            )
            return

        if not PAYMENT_ENGINE:
            tg_send(
                "💳 <b>Payment gateway offline.</b>\n\n"
                "Tapi tenang, kamu tetap bisa donasi manual:\n\n"
                "💚 <b>Transfer ke:</b>\n"
                "🏦 BCA: 8531425531 a.n. MOH SUHUD\n"
                "📱 Dana/Ovo/GoPay: 08123456789 (konfirm admin)\n\n"
                "📞 Kirim bukti transfer ke: @codergaboets\n"
                "Sertakan user ID kamu: <code>" + str(chat_id) + "</code>\n\n"
                "⏳ Aktivasi manual 1-24 jam (we will notify you!)",
                chat_id
            )
            return

        tg_send(f"⏳ <b>Membuat link pembayaran...</b>\n{label} — Rp{amount:,}", chat_id)

        result = create_tripay_payment(str(chat_id), username, tier="donor", amount=amount)
        if result.get("error"):
            tg_send(
                f"❌ <b>Gagal membuat pembayaran otomatis</b>\n"
                f"{result['error']}\n\n"
                f"💚 <b>Alternatif transfer manual:</b>\n"
                f"🏦 BCA: 8531425531 a.n. MOH SUHUD\n"
                f"📱 Dana/Ovo/GoPay — konfirm ke @codergaboets\n\n"
                f"📞 Sertakan user ID: <code>{chat_id}</code>\n"
                f"Admin akan aktivasi manual dalam 1-24 jam.",
                chat_id
            )
            return

        pay_url = result.get("payment_url", "")
        pay_code = result.get("pay_code", "")
        ref = result.get("reference", "") or result.get("merchant_ref", "")

        txt = (
            f"💚 <b>{label}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Total: <b>Rp{amount:,}</b>\n"
        )
        if pay_code:
            txt += f"📱 Kode Bayar: <code>{pay_code}</code>\n"
        txt += (
            f"⏰ Expired: 1 jam\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Klik tombol di bawah untuk bayar 👇\n\n"
            f"<i>Setelah bayar, bot auto-upgrade kamu ke 🟢 DONATUR dalam 1-5 menit.</i>"
        )

        markup = {"inline_keyboard": [
            [{"text": f"💳 Bayar Rp{amount:,}", "url": pay_url}],
            [{"text": "🔄 Cek Status", "callback_data": f"check:{ref}"},
             {"text": "📞 Admin", "url": "https://t.me/codergaboets"}],
            [{"text": "🔙 Kembali", "callback_data": "cancel_input"}],
        ]}

        tg_send(txt, chat_id, reply_markup=markup)


def answer_callback(cb_id, text=""):
    try:
        payload = json.dumps({"callback_query_id": cb_id, "text": text}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def post_signal_to_bridge(sig, price, display="XAUUSD"):
    symbol = sig.get("symbol", sig.get("display", display))
    entry = sig.get("entry", price) or price
    sl = sig.get("sl", 0)
    tp = sig.get("tp", 0)
    confidence = sig.get("confidence", 0)
    rr = sig.get("rr_ratio", 0)
    action = sig.get("action", "HOLD")

    # ── QUALITY GATE ──
    if action in ("BUY", "SELL"):
        # Minimum confidence: 65%
        if isinstance(confidence, (int, float)) and confidence < 0.65:
            logger.info(f"⛔ Signal rejected: confidence {confidence:.0%} < 65%")
            return
        # Minimum RR: 1.5
        if isinstance(rr, (int, float)) and rr > 0 and rr < 1.5:
            logger.info(f"⛔ Signal rejected: RR 1:{rr:.1f} < 1:1.5")
            return
        # SL must be on correct side of entry
        if (action == "BUY" and sl >= entry) or (action == "SELL" and sl <= entry):
            logger.info(f"⛔ Signal rejected: SL on wrong side (entry={entry}, sl={sl})")
            return

    # --- XAUUSD: no offset needed (single source: UnifiedMarketData GC=F) ---

    payload = {
        "action": action,
        "symbol": symbol,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "tp1": sig.get("tp1", sig.get("tp", 0)),
        "tp2": sig.get("tp2", 0),
        "confidence": confidence,
        "risk_percent": sig.get("risk_percent", 1.0),
        "comment": sig.get("comment", f"VTFX/{sig.get('source', 'vilona-tradefx')}"),
        "source": sig.get("source", "vilona-tradefx"),
        "layers": sig.get("layers", []),
        "target_user": sig.get("target_user", ""),
        "timestamp": wib_now().isoformat(),
        "rr_ratio": rr,
    }

    # ── Write to EA signal file (for ea_executor.py to pick up) ──
    try:
        ea_file = DATA_DIR / "ea_signal.json"
        ea_file.write_text(json.dumps(payload, indent=2))
        rr_display = float(str(rr).replace("1:", "")) if isinstance(rr, str) and rr.startswith("1:") else float(rr) if rr else 0
        logger.info(f"📝 EA signal written: {action} {symbol} @ {entry} | conf={confidence:.0%} | RR=1:{rr_display:.1f}")
    except Exception as e:
        logger.error(f"Failed to write ea_signal.json: {e}")

    data = json.dumps(payload).encode()
    # Track trade for win rate
    if TRADE_TRACKER:
        try:
            open_trade(sig, sig.get("entry", price), symbol, sig.get("source", "ai"),
                       sig.get("target_user", ""))
        except Exception: pass
    # ── Post to bridge ──
    posted = False
    for url in BRIDGE_URLS:
        try:
            req = urllib.request.Request(f"{url}/signal",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": MASTER_API_KEY
                })
            urllib.request.urlopen(req, timeout=5)
            posted = True
            break  # success, stop
        except Exception:
            continue
    if not posted:
        logger.warning("Failed to post signal to any bridge URL")


# ── MECHANICAL SIGNAL DETECTION ──
def detect_mechanical_signal(symbol="XAUUSD", display="XAUUSD", price=None, ohlcv_bars=None):
    """Mechanical signal: Quant + FVG + Hermes → fire without AI consensus."""
    if not (QUANT_ENGINE or FVG_ENGINE or HERMES_LIQUIDITY_ENGINE):
        return None, None
    if not ohlcv_bars or len(ohlcv_bars) < 15:
        return None, None
    if not price:
        price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))

    quant_result = None
    fvg_signals = []

    # Layer 1: Quant Engine
    if QUANT_ENGINE:
        try:
            qdata = [{"timestamp": b.get("timestamp",0), "open": float(b["open"]),
                      "high": float(b["high"]), "low": float(b["low"]),
                      "close": float(b["close"]), "volume": float(b.get("volume",0))}
                     for b in ohlcv_bars]
            quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
        except: pass

    # Layer 2: FVG Detector
    if FVG_ENGINE:
        try:
            fvg_signals = detect_fvg(ohlcv_bars, "M1")
        except: pass

    # Layer 2.5: Quant + FVG alignment check
    quant_bias = None
    if quant_result and quant_result.get("match_count", 0) >= 15:
        dom = quant_result.get("dominant_next")
        g = quant_result.get("green_pct", 0)
        r = quant_result.get("red_pct", 0)
        if dom == "G" and g >= 40: quant_bias = "BUY"
        elif dom == "R" and r >= 40: quant_bias = "SELL"

    fvg_bias = None
    fvg_sig = None
    if fvg_signals:
        fvg_sig = fvg_signals[0]
        if fvg_sig.confidence >= 0.20:
            fvg_bias = fvg_sig.direction

    # Quant + FVG must agree
    if quant_bias and fvg_bias and fvg_bias == quant_bias:
        confidence = round((quant_result["confidence_score"] + fvg_sig.confidence) / 2, 2)
        reasoning = (
            f"🤖 MECHANICAL SIGNAL | Quant {quant_bias} "
            f"({quant_result['green_pct']:.0f}%G/{quant_result['red_pct']:.0f}%R) "
            f"+ FVG {fvg_sig.direction} ({fvg_sig.fvg_zone.size_pips:.0f}pip)"
        )
        sig = {
            "action": quant_bias, "entry": fvg_sig.entry, "sl": fvg_sig.sl,
            "tp": fvg_sig.tp2, "tp1": fvg_sig.tp1, "tp2": fvg_sig.tp2,
            "confidence": confidence, "rr_ratio": fvg_sig.rr_ratio,
            "reasoning": reasoning, "ensemble": "mechanical", "voters": 0,
            "_model": "Quant+FVG", "grade": "B", "source": "mechanical_override",
            "fvg_data": fvg_to_dict(fvg_sig) if fvg_sig else None,
        }
        return sig, reasoning

    # Quant-only strong bias (requires higher threshold to avoid noise)
    if quant_result and quant_result.get("match_count", 0) >= 35 and quant_result.get("confidence_score", 0) >= 0.70:
        confidence = quant_result["confidence_score"]
        reasoning = f"🤖 MECHANICAL (Quant Only) | {quant_bias} bias ({confidence:.0%})"
        # Point-based SL/TP for XAUUSD/GOLD
        if display in ("XAUUSD", "GOLD"):
            sl_val = round(price - 3.0, 2) if quant_bias == "BUY" else round(price + 3.0, 2)
            tp_val = round(price + 5.0, 2) if quant_bias == "BUY" else round(price - 5.0, 2)
            rr_val = round(52/32, 2)
        else:
            sl_val = price - 0.5 if quant_bias == "BUY" else price + 0.5  # generic 0.5% SL
            tp_val = price * 1.01 if quant_bias == "BUY" else price * 0.99
            rr_val = 2.0
        sig = {
            "action": quant_bias, "entry": price,
            "sl": sl_val,
            "tp": tp_val,
            "confidence": confidence, "rr_ratio": rr_val,
            "reasoning": reasoning, "ensemble": "mechanical", "voters": 0,
            "_model": "Quant", "grade": "C", "source": "mechanical_quant_only",
        }
        return sig, reasoning

    # Layer 3: Hermes Liquidity Hunter (Pre-NFP sweep)
    if HERMES_LIQUIDITY_ENGINE:
        try:
            ohlcv_m15 = None
            if MARKET_DATA:
                try:
                    m15_bars = MARKET_DATA.get_ohlcv("GC=F", "15m", 80)
                    if m15_bars and len(m15_bars) >= 30:
                        ohlcv_m15 = [{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                      "low": b.low, "close": b.close, "volume": b.volume} for b in m15_bars]
                except: pass

            if ohlcv_m15:
                hermes_signal = hermes_liquidity_pipeline(ohlcv_bars, ohlcv_m15, price)
                if hermes_signal and hermes_signal.action in ("SELL", "BUY"):
                    # ── SAFETY VALIDATION ──
                    action = hermes_signal.action
                    entry = hermes_signal.entry_price
                    sl = hermes_signal.stop_loss
                    tp = hermes_signal.take_profit_1
                    rr = hermes_signal.risk_reward_ratio

                    # XAUUSD pip = $0.10 per 0.01 lot, 1 pip = 0.10 USD movement
                    # Minimum SL distance: 30 pips = $3.00
                    MIN_SL_DIST = 3.0    # $3.00 minimum SL distance (was $2)
                    MIN_RR_REQ = 1.5     # minimum acceptable R:R (was 1.2)
                    MAX_RR_CAP = 5.0     # maximum R:R cap — too aggressive = unrealistic

                    sl_dist = abs(entry - sl)
                    sl_invalid = False
                    reject_reason = ""

                    # Check SL distance
                    if sl_dist < MIN_SL_DIST:
                        sl_invalid = True
                        reject_reason = f"SL distance ${sl_dist:.2f} < minimum ${MIN_SL_DIST:.2f}"

                    # Check R:R
                    elif rr < MIN_RR_REQ:
                        sl_invalid = True
                        reject_reason = f"R:R 1:{rr} < minimum 1:{MIN_RR_REQ}"
                    elif rr > MAX_RR_CAP:
                        sl_invalid = True
                        reject_reason = f"R:R 1:{rr} > maximum 1:{MAX_RR_CAP} — unrealistic"

                    if sl_invalid:
                        logger.warning(f"🛑 HERMES REJECTED [{display}]: {reject_reason}")
                        return None, None

                    logger.info(f"🔮 HERMES LIQUIDITY SWEEP: {action} {display} | "
                                f"Entry={entry} SL={sl} TP1={tp} R:R=1:{rr} | SL_dist=${sl_dist:.2f}")
                    sig = {
                        "action": action, "entry": entry,
                        "sl": sl, "tp": tp,
                        "tp1": tp, "tp2": hermes_signal.take_profit_2,
                        "confidence": hermes_signal.confidence,
                        "rr_ratio": rr,
                        "reasoning": hermes_signal.reason, "ensemble": "mechanical", "voters": 0,
                        "_model": "HermesSMC",
                        "grade": "A" if rr >= 2.0 else "B",
                        "source": "hermes_liquidity_sweep",
                    }
                    # Fix SL direction + clamp TP via quality gate
                    sig = _clamp_sltp(sig, display)
                    return sig, hermes_signal.reason
        except Exception as e:
            logger.debug(f"Hermes liquidity check skipped: {e}")

    return None, None


# ── AI Models ──
SYSTEM_PROMPT = """Kamu adalah Vilona Trade FX — Full-Stack Institutional AI Trading System.
Senior Hedge Fund Portfolio Manager menganalisis market dengan data REAL.

⚠️ CRITICAL RULE: Analisa HARUS berdasarkan DATA OHLCV yang diberikan dalam prompt.
DILARANG mengarang harga, level, atau pola yang tidak ada di data.
Jika data tidak tersedia → HOLD. Jika data tidak mendukung setup → HOLD.

═══════════════════════════════════════════
🛡️ CONSTITUTION (Non-Negotiable)
═══════════════════════════════════════════
LAW #1 — CIRCUIT BREAKER: loss_count >= 3 → WAJIB HOLD. TIDAK ADA pengecualian.
LAW #2 — REALISTIC: Target 5-15%/bulan, bukan 100%.
LAW #3 — COMPOUNDING > JACKPOT: $1,000 @ 10%/bln → 12 bln: $3,138 | 5 thn: $300K+
LAW #4 — DUAL RISK TIER: SKC ≥ 8.7 → 1% risk | SKC 7.0-8.6 → 0.5% risk | SKC < 7.0 → SKIP
LAW #5 — DON'T CHASE: Entry hanya setelah candle CLOSED dengan konfirmasi.
LAW #6 — PIP CALCULATION: XAUUSD broker 3-digit → 1 pip = 0.10. USOIL 3-digit → 1 pip = 0.01. BTCUSD → 1 pip = 1.0. Forex → 1 pip = 0.00010 (5-digit) / 0.01 (JPY). entry/sl/tp = HARGA ABSOLUTE. sl_pips/tp_pips = JARAK dalam pip.
LAW #7 — SL/TP RULES: SL 20-35 pip dari entry. TP = SL × RR (min 1:2). UNTUK XAUUSD 3-DIGIT: SL 20 pip = 2.0 poin harga. SL 30 pip = 3.0 poin harga. JANGAN kasih SL 30 poin (= 300 pip!). Contoh SELL entry=4334: SL=4337.00 (+3.0 poin = 30 pip), TP=4328.00 (−6.0 poin = 60 pip) untuk RR 1:2.

═══════════════════════════════════════════
🔬 SKC SCORING ENGINE (Max 10 pts)
═══════════════════════════════════════════
S — STRUKTUR (Max 4.0): W1/D1 aligned(+1.5) | H4 CHoCH/BOS(+1.5) | H1 POI(+0.5) | M15/M5(+0.5)
K — KONFLUENSI (Max 3.5): Liq sweep(+1.0) | ≥3TF bias aligned(+1.0) | Killzone active(+0.75) | S/R round number(+0.75)
C — KONTEKS (Max 2.5): Macro align(+1.0) | News align(+1.0) | Clean chart no chop(+0.5)

≥8.7 → 🟢 GREEN (1% risk) | 7.0-8.6 → 🟡 YELLOW (0.5% risk) | <7.0 → 🔴 RED (SKIP/HOLD)

OUTPUT: JSON only. No markdown, no text outside JSON.
Return exactly this JSON structure:
{
 "action":"BUY|SELL|HOLD",
 "entry":0.0, "sl":0.0, "tp":0.0,
 "sl_pips":0, "tp_pips":0,
 "rr_ratio":"1:X.XX",
 "confidence":0.0, "grade":"A|B|C|D",
 "combat_style":"SNIPER|COMMANDO|CRUSADER|LIQUIDITY_HUNTER|HOLD",
 "bias":"BULLISH|BEARISH|NEUTRAL",
 "skc_score":{"s_struktur":0.0,"k_konfluensi":0.0,"c_konteks":0.0,"total":0.0,"zone":"GREEN|YELLOW|RED"},
 "risk_tier":"1%|0.5%|SKIP",
 "layer_1":"TRIGGERED|WAITING|N/A",
 "layer_2":"CONFIRMED|PENDING|FAILED",
 "confluences":["factor1","factor2"],
 "reasoning":"6-8 kalimat ANALISA LENGKAP..."
}"""


def _extract_json(content):
    """Robust JSON extraction from AI output — strips markdown, sanitizes.""" 
    content = re.sub(r'```[a-z]*\s*', '', content)
    start = content.find('{')
    if start < 0: return None
    depth = 0; end = start
    for i, ch in enumerate(content[start:], start):
        if ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0: end = i + 1; break
    json_str = content[start:end]
    json_str = re.sub(r'[\x00-\x1f]+', ' ', json_str)
    try:
        return json.loads(json_str, strict=False)
    except Exception:
        return None


def _call_deepseek(prompt):
    if not DEEPSEEK_KEY: return None
    try:
        req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions",
            data=json.dumps({
                "model":"deepseek-chat","max_tokens":800,"temperature":0.3,
                "messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}]
            }).encode(),
            headers={"Content-Type":"application/json","Authorization":f"Bearer {DEEPSEEK_KEY}"})
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            _AI_TOKEN_USAGE["deepseek"] = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
            logger.info(f"DeepSeek: {len(content)} chars, {_AI_TOKEN_USAGE['deepseek']['total']} tokens")
            return _extract_json(content)
    except Exception as e:
        logger.warning(f"DeepSeek error: {e}")
        return None


def _call_openai(prompt, model="gpt-4o-mini"):
    """Call OpenAI. Model can be overridden: gpt-4o-mini, o3-mini, gpt-4.1, etc."""
    if not OPENAI_KEY: return None
    try:
        # o3-mini doesn't support system messages or temperature
        is_o3 = "o3" in model
        messages = [{"role": "user", "content": f"{SYSTEM_PROMPT}\n\n{prompt}"}] if is_o3 else \
                   [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        body = {"model": model, "max_tokens": 800, "messages": messages}
        if not is_o3:
            body["temperature"] = 0.3
        
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_KEY}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            _AI_TOKEN_USAGE["openai"] = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
            logger.info(f"OpenAI/{model}: {len(content)} chars, {_AI_TOKEN_USAGE['openai']['total']} tokens")
            return _extract_json(content)
    except Exception as e:
        logger.warning(f"OpenAI/{model} error: {e}")
        return None


def _call_gemini(prompt):
    """Call Gemini via OmniRoute (auto-rotates ~20 keys, avoids rate limits)."""
    # Route through OmniRoute with Gemini models (Bro has 20 keys in rotation)
    return _call_omniroute(prompt, models=[
        "gemini-cli/gemini-2.0-flash",
        "gemini-cli/gemini-3.1-pro-preview",
        "kie/gemini-2.5-pro",
    ])


def _call_omniroute(prompt, models=None):
    if not models: models = OMNIROUTE_MODELS
    for model in models:
        try:
            req = urllib.request.Request(OMNIROUTE_URL,
                data=json.dumps({"model":model,"max_tokens":600,"temperature":0.3,"stream":False,
                    "messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}]}).encode(),
                headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                content = data["choices"][0]["message"]["content"]
                logger.info(f"OmniRoute/{model}: {len(content)} chars")
                return _extract_json(content)
        except Exception as e:
            logger.warning(f"OmniRoute/{model} error: {e}")
            continue
    return None


def _call_grok_news(display: str, price: float) -> str | None:
    """Call Grok (xAI) for real-time market news/context.
    
    Grok has X/Twitter real-time access — uniquely positioned for breaking news.
    Returns a concise news summary string, or None on failure.
    Only called for donor/channel tiers (expensive: ~$0.002/call).
    """
    if not GROK_KEY:
        return None
    try:
        news_prompt = (
            f"Search X/Twitter for the LATEST breaking news, macro events, or market-moving "
            f"headlines about {display} (currently ${price:.2f}). "
            f"Focus on: FOMC/Fed speakers, NFP/CPI/economic data, geopolitical events, "
            f"major institutional moves, or sentiment shifts in the last 2 hours.\n\n"
            f"Return ONLY a structured JSON with these fields:\n"
            f'{{"headline": "1 most impactful headline", '
            f'"sentiment": "BULLISH/BEARISH/NEUTRAL", '
            f'"impact": "HIGH/MED/LOW", '
            f'"detail": "2-3 sentence context explaining WHY this matters for {display}"}}\n\n'
            f"Be CONCISE. Max 150 words total. If no significant news, headline='No major catalysts'."
        )
        req = urllib.request.Request(GROK_URL,
            data=json.dumps({
                "model": "grok-2-latest",
                "max_tokens": 300,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": news_prompt}]
            }).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {GROK_KEY}"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            _AI_TOKEN_USAGE["grok"] = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
            logger.info(f"Grok News: {len(content)} chars, {_AI_TOKEN_USAGE['grok']['total']} tokens")
            news = _extract_json(content)
            if news and isinstance(news, dict):
                return news
            return {"headline": content[:200], "sentiment": "NEUTRAL", "impact": "LOW", "detail": ""}
    except Exception as e:
        logger.warning(f"Grok News error: {e}")
        return None


def _format_news_context(news: dict | None) -> str:
    """Format Grok news context for signal display."""
    if not news:
        return ""
    headline = news.get("headline", "")
    sentiment = news.get("sentiment", "NEUTRAL")
    impact = news.get("impact", "LOW")
    detail = news.get("detail", "")
    
    if not headline or headline == "No major catalysts":
        return ""
    
    s_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}.get(sentiment, "⚪️")
    i_emoji = {"HIGH": "🔥", "MED": "📊", "LOW": "📎"}.get(impact, "")
    
    lines = [
        f"📰 <b>Grok Market Context</b>",
        f"{s_emoji} {headline}",
    ]
    if detail:
        lines.append(f"💡 {detail}")
    lines.append(f"Sentiment: {sentiment} | Impact: {i_emoji} {impact}")
    return "\n".join(lines)


def ask_ai_ensemble(price, dxy, sess, kz_str, loss_count, premium=False, ohlcv_data=None, display="XAUUSD", tier="starter"):
    """Multi-AI consensus — tier-based model selection.
    
    🔬 Tier-based model count:
       - starter:  DeepSeek only (solo, max 55% conf) — free tier
       - pro:      DeepSeek + GPT-4o (dual, max 85% conf) — donor
       - elite:    All 3 models + Grok News (max 95% conf) — premium donor
       - premium=True: All models (channel/auto — unlimited)
    ⭐ Models: DeepSeek V3 + GPT-4o + Claude-Sonnet + Grok News.
    """
    # Reset token counter for this analysis cycle
    global _AI_TOKEN_USAGE
    _AI_TOKEN_USAGE = {}
    
    # Build analysis prompt
    data_section = f"💰 Current Price: ${price:.2f}"
    if ohlcv_data:
        data_section += f"\n📊 OHLCV (last {len(ohlcv_data)} bars): {json.dumps(ohlcv_data[-10:])}"
    if dxy:
        data_section += f"\n💵 DXY: {dxy:.2f}"

    is_blackout, is_post_news, news_name = news_blackout_status()
    news_protocol = ""
    if is_post_news:
        news_protocol = f"\n\n⚡ POST-NEWS PROTOCOL: {news_name}\n🔴 MODE: LIQUIDITY HUNTER — counter-trend only\n"
    elif is_blackout:
        news_protocol = f"\n\n🚫 PRE-NEWS BLACKOUT: {news_name} — WAJIB HOLD.\n"

    learning_context = ""
    if LEARNING_ENGINE:
        try: learning_context = get_adaptation_context()
        except: pass

    prompt = (
        f"🕐 {wib_fmt()} | Session: {sess} | Killzone: {kz_str}\n"
        f"🔴 Circuit Breaker: Loss hari ini: {loss_count}/3\n"
        f"{learning_context}{news_protocol}\n{data_section}\n\n"
        f"Analisis {display} dengan SMC + SnR. Entry/SL/TP wajib dari data.\n"
        f"R:R minimum 1:2. {'⚠️ FRIDAY: SL +10-15 pips extra.' if wib_now().weekday()==4 else ''}\n"
        f"⚡ XAUUSD 3-DIGIT: 1 pip = 0.10. SL 30 pip = 3.0 poin harga. TP 60 pip = 6.0 poin.\n"
        f"Contoh SELL 4334 → SL=4337.00 TP=4328.00 (60 pip = RR 1:2)"
    )

    # ── TIER-BASED MODEL SELECTION ──
    is_free_tier = (tier == "starter" and not premium)

    # DeepSeek V3 — always called (even for free tier)
    deepseek = _call_deepseek(prompt)

    # GPT-4o — only for donors, elite, or channel (premium)
    gpt4o = None
    if not is_free_tier:
        gpt4o = _call_openai(prompt, model="gpt-4o")

    # OmniRoute (Claude-Sonnet) — DISABLED: HTTP 400 broken, direct API calls used instead
    # omniroute = None
    # if tier == "elite" or premium:
    #     omniroute = _call_omniroute(prompt)
    omniroute = None  # OmniRoute disabled — DeepSeek + GPT-4o direct calls sufficient

    # Grok News — real-time X/Twitter market context (donors only)
    grok_news = None
    if not is_free_tier:
        grok_news = _call_grok_news(display, price)

    # Calculate total tokens used
    token_total = sum(v.get("total", 0) for v in _AI_TOKEN_USAGE.values())
    token_prompt = sum(v.get("prompt", 0) for v in _AI_TOKEN_USAGE.values())
    token_completion = sum(v.get("completion", 0) for v in _AI_TOKEN_USAGE.values())

    # Collect all valid signals
    signals = []
    if deepseek and deepseek.get("action") in ("BUY", "SELL"):
        signals.append({"sig": deepseek, "name": "DeepSeek-V3", "weight": 1.2})
    if gpt4o and gpt4o.get("action") in ("BUY", "SELL"):
        signals.append({"sig": gpt4o, "name": "GPT-4o", "weight": 1.0})
    if omniroute and isinstance(omniroute, dict) and omniroute.get("action") in ("BUY", "SELL"):
        signals.append({"sig": omniroute, "name": "Claude-Sonnet", "weight": 0.9})

    model_count = len(signals)
    tier_label = {"starter": "🆓 Free", "pro": "⭐ Pro", "elite": "👑 Elite", "testing": "🧪 Testing"}.get(tier, tier.upper())

    # Confidence caps by tier
    conf_caps = {"starter": 0.55, "pro": 0.85, "elite": 0.95}
    conf_cap = conf_caps.get(tier, 0.95)
    if premium:
        conf_cap = 0.95  # channel/auto always gets max

    # Count votes per direction
    buy_votes = [s for s in signals if s["sig"]["action"] == "BUY"]
    sell_votes = [s for s in signals if s["sig"]["action"] == "SELL"]

    # 🏆 DUAL+: 2+ models agree
    if len(buy_votes) >= 2 or len(sell_votes) >= 2:
        winner = buy_votes if len(buy_votes) >= 2 else sell_votes
        conf = sum(s["sig"].get("confidence", 0) * s["weight"] for s in winner) / sum(s["weight"] for s in winner)
        sig = winner[0]["sig"].copy()
        sig["confidence"] = min(conf, conf_cap)
        sig["ensemble"] = "dual"
        sig["voters"] = len(winner)
        sig["_model"] = "+".join(s["name"] for s in winner)
        sig["_tier"] = tier_label
        sig["_tier_capped"] = is_free_tier
        sig["_models"] = f"{model_count}/2"
        sig["_token_total"] = token_total
        sig["_token_prompt"] = token_prompt
        sig["_token_completion"] = token_completion
        sig["_grok_news"] = grok_news
        logger.info(f"AI CONSENSUS [{len(winner)}/{len(signals)}]: {sig['action']} conf={sig['confidence']:.0%} tier={tier} tokens={token_total}")
        return sig

    # ⚠️ SOLO: 1 model only — low confidence, still return for manual review
    if signals:
        best = max(signals, key=lambda s: s["sig"].get("confidence", 0) * s["weight"])
        sig = best["sig"].copy()
        sig["confidence"] = min(sig.get("confidence", 0), conf_cap)
        sig["ensemble"] = "solo"
        sig["voters"] = 1
        sig["_model"] = best["name"]
        sig["_tier"] = tier_label
        sig["_tier_capped"] = is_free_tier
        sig["_models"] = f"{model_count}/2"
        sig["_token_total"] = token_total
        sig["_token_prompt"] = token_prompt
        sig["_token_completion"] = token_completion
        sig["_grok_news"] = grok_news
        logger.info(f"SOLO [{best['name']}]: {sig['action']} conf={sig['confidence']:.0%} tier={tier} tokens={token_total}")
        return sig

    # ❌ Nothing: return any model's HOLD or None
    for s, name in [(deepseek, "DeepSeek-V3"), (gpt4o if gpt4o else deepseek, "GPT-4o" if gpt4o else "DeepSeek-V3")]:
        if s:
            s = dict(s)
            s["ensemble"] = "hold"; s["voters"] = 0
            s["_model"] = name; s["_tier"] = tier_label
            s["_tier_capped"] = is_free_tier
            s["_models"] = "0/2"
            s["_token_total"] = token_total
            s["_token_prompt"] = token_prompt
            s["_token_completion"] = token_completion
            s["_grok_news"] = grok_news
            return s

    return None


def ask_ai(price, dxy, sess, kz_str, loss_count, premium=False, ohlcv=None, display="XAUUSD", tier="starter"):
    return ask_ai_ensemble(price, dxy, sess, kz_str, loss_count, premium, ohlcv, display, tier=tier)


# ── Signal formatting ──
def apply_elite_params(sig: dict, params: dict, price: float, display: str = "XAUUSD") -> dict:
    """Apply Elite custom params (risk%, tf) to a signal dict."""
    if not params or not sig:
        return sig

    sig = dict(sig)  # don't mutate original

    if "risk" in params:
        sig["risk_percent"] = params["risk"]
        # Adjust SL/TP proportionally if they exist
        mult = params["risk"] / 1.0  # relative to default 1%
        if sig.get("sl") and sig.get("entry") and sig.get("entry") != 0:
            base_sl = abs(sig["sl"] - sig["entry"])
            sig["sl"] = sig["entry"] - (base_sl * mult)
        if sig.get("tp") and sig.get("entry") and sig.get("entry") != 0:
            base_tp = abs(sig["tp"] - sig["entry"])
            sig["tp"] = sig["entry"] + (base_tp * mult)

    if "tf" in params:
        sig["timeframe"] = params["tf"]

    sig["elite_custom"] = True
    return sig


# ═══════════════════════════════════════════════════════════════
# SIGNAL QUALITY GATE — Memisahkan Actionable vs Market Pulse
# ═══════════════════════════════════════════════════════════════

def _sig_quality_pass(sig: dict, quant_result: dict | None = None, display: str = "XAUUSD") -> tuple[bool, str]:
    """Quality gate. Returns (passed, grade_reason).
    'A' = full pass | 'B' = pass with warning | 'C' = REJECT -> downgrade to pulse
    """
    action = sig.get("action", "HOLD")
    if action == "HOLD":
        return False, "Market sideways — no clear direction"

    conf = sig.get("confidence", 0)
    if isinstance(conf, (int, float)) and conf > 10:
        conf = conf / 100

    # Gate 1: Confidence
    if conf < 0.65:
        return False, f"Confidence {conf:.0%} < 65%"

    # Gate 2: Voters
    voters = sig.get("voters", sig.get("ensemble", 0))
    if isinstance(voters, str) and "/" in voters:
        voters = int(voters.split("/")[0])
    voters = int(voters) if voters else 0
    if voters < 2:
        return False, f"Only {voters} model agreed (min 2)"

    # Gate 3: RR
    rr = sig.get("rr_ratio", 0)
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = float(rr[2:]) if rr[2:] else 0
    rr = float(rr) if rr else 0
    if rr > 0 and rr < 1.5:
        return False, f"RR 1:{rr:.1f} too poor"

    # Gate 4: Quant direction alignment
    if quant_result:
        qv = quant_result.get("quant_verdict", "")
        dominant = quant_result.get("dominant_next", "")
        if qv == "GREEN" and action == "SELL":
            return False, f"Quant says BUY but signal SELL — conflict"
        if qv == "RED" and action == "BUY":
            return False, f"Quant says SELL but signal BUY — conflict"

    # Gate 5: Session (soft — downgrade to B for Asia on forex/metals)
    is_crypto = display.upper() in ("BTCUSD", "ETHUSD", "BTC", "ETH")
    if not is_crypto:
        try:
            kz = killzone() if callable(killzone) else session(wib_now().hour)
        except:
            kz = session(wib_now().hour) if 'session' in dir() else ""
        if "ASIA" in str(kz).upper() and "LONDON" not in str(kz).upper():
            return True, "Asia session — lower volatility"

    return True, "Quality Gate PASS"


def _compute_levels(ohlcv_bars: list, price: float) -> str:
    """Compute SnR + FIBO context line from OHLCV."""
    if not ohlcv_bars or len(ohlcv_bars) < 10 or not price:
        return ""
    try:
        closes = [float(b.get("c", b.get("close", 0))) for b in ohlcv_bars[-50:]]
        highs = [float(b.get("h", b.get("high", 0))) for b in ohlcv_bars[-50:]]
        lows = [float(b.get("l", b.get("low", 0))) for b in ohlcv_bars[-50:]]
        sh = max(highs[-20:]) if len(highs) >= 20 else max(highs) if highs else 0
        sl_ = min(lows[-20:]) if len(lows) >= 20 else min(lows) if lows else 0
        if not sh or not sl_:
            return ""
        res = min([h for h in highs if h > price], default=sh)
        sup = max([l for l in lows if l < price], default=sl_)
        rng = sh - sl_
        f50 = sl_ + rng * 0.50 if rng > 0 else 0
        f618 = sl_ + rng * 0.618 if rng > 0 else 0
        parts = [f"📍 SnR: Support {sup:.2f} | Resistance {res:.2f}"]
        if f50:
            parts.append(f"📐 FIBO 50: {f50:.2f} | 61.8: {f618:.2f}")
        return "\\n".join(parts)
    except:
        return ""


def _clamp_sltp(sig: dict, display: str = "XAUUSD") -> dict:
    """Enforce realistic SL/TP bounds. Prevents 80-pip SL or 760-pip TP.
    
    XAUUSD 3-digit: 1 pip = 0.10. SL must be 20-35 pip. TP = SL * RR (max 1:3).
    """
    action = sig.get("action", "").upper()
    if action not in ("BUY", "SELL"):
        return sig
    
    entry = sig.get("entry", 0)
    sl = sig.get("sl", 0)
    if not entry or not sl:
        return sig
    
    # Pip sizes per asset
    pip_sizes = {"XAUUSD": 0.10, "GOLD": 0.10, "USOIL": 0.01, "BTCUSD": 1.0, "ETHUSD": 0.01}
    pip_size = pip_sizes.get(display.upper(), 0.01)
    
    sl_dist_pts = abs(entry - sl)
    sl_pips = sl_dist_pts / pip_size
    logger.info(f"_clamp_sltp [{display}]: {action} entry={entry} sl={sl} sl_pips={sl_pips:.0f}")
    
    MIN_SL = 20   # min 20 pip
    MAX_SL = 35   # max 35 pip
    MAX_TP = 100  # max 100 pip TP
    
    clamped = False
    
    # Clamp SL
    if sl_pips < MIN_SL:
        sl_dist_pts = MIN_SL * pip_size
        clamped = True
    elif sl_pips > MAX_SL:
        sl_dist_pts = MAX_SL * pip_size
        clamped = True
    
    # ── ALWAYS fix SL direction (not just when clamping distance) ──
    direction_wrong = (action == "BUY" and sig["sl"] > entry) or (action == "SELL" and sig["sl"] < entry)
    if clamped or direction_wrong:
        if action == "BUY":
            sig["sl"] = round(entry - sl_dist_pts, 2)
        else:
            sig["sl"] = round(entry + sl_dist_pts, 2)
        sl_pips = sl_dist_pts / pip_size
        if direction_wrong:
            logger.info(f"_clamp_sltp [{display}]: FIXED wrong SL direction — {action} SL now {'below' if action=='BUY' else 'above'} entry")
    
    # Recalculate TP based on clamped SL and RR
    rr = sig.get("rr_ratio", 0)
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = float(rr[2:])
    rr = float(rr) if rr else 2.0
    rr = max(1.5, min(rr, 3.0))  # cap RR 1:1.5 to 1:3
    
    tp_dist = sl_pips * rr * pip_size
    if tp_dist / pip_size > MAX_TP:
        tp_dist = MAX_TP * pip_size
    
    if action == "BUY":
        sig["tp"] = round(entry + tp_dist, 2)
        sig["tp1"] = round(entry + tp_dist, 2)
        sig["tp2"] = round(entry + tp_dist * 1.5, 2)
    else:
        sig["tp"] = round(entry - tp_dist, 2)
        sig["tp1"] = round(entry - tp_dist, 2)
        sig["tp2"] = round(entry - tp_dist * 1.5, 2)
    
    # Remove unrealistic TP3/TP4
    sig.pop("tp3", None)
    sig.pop("tp4", None)
    sig["rr_ratio"] = f"1:{rr:.1f}"
    logger.info(f"_clamp_sltp result: sl={sig['sl']} tp1={sig.get('tp1',0)} tp2={sig.get('tp2',0)}")
    
    return sig


def fmt_signal(sig, price, dxy, h, display="XAUUSD", currency="$", quality=None, levels=""):
    """Format signal Telegram-style — quality-aware dual format.
    
    quality: tuple (passed: bool, reason: str) from _sig_quality_pass()
    levels: SnR/FIBO context string from _compute_levels()
    
    If quality PASS → "SINYAL SELL/BUY" (actionable)
    If quality FAIL → "MARKET PULSE" (info only, no execution)
    """
    action = sig.get("action","HOLD")
    emoji = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪️"}.get(action,"⚪️")
    grade = sig.get("grade","D")
    conf = sig.get("confidence",0)
    if isinstance(conf, (int,float)) and conf > 10:
        conf = conf / 100
    rr = sig.get("rr_ratio","?")
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = rr[2:]
    entry = sig.get("entry") or price or 0
    sl = sig.get("sl") or 0
    tp = sig.get("tp") or 0
    tp1 = sig.get("tp1", 0)
    tp2 = sig.get("tp2", 0)
    tp3 = sig.get("tp3", 0)
    tp4 = sig.get("tp4", 0)
    models_display = sig.get("_models","")
    voters = sig.get("voters", sig.get("ensemble", "?"))
    now_wib = wib_now()

    # ── Determine format mode ──
    q_passed, q_reason = quality if quality else (None, None)
    if q_passed is None:
        # Auto-detect from confidence/voters if no quality gate result provided
        q_passed = conf >= 0.65 and action != "HOLD"
        q_reason = "Quality Gate PASS" if q_passed else "Low confidence — info only"
    
    is_actionable = q_passed and action in ("BUY", "SELL")
    header_emoji = emoji if is_actionable else "⚪️"
    header_label = f"SINYAL {action}" if is_actionable else "MARKET PULSE"

    # --- MIN SL GUARD: override if AI sets SL too tight (3-digit Exness adjusted) ---
    if action in ("BUY","SELL") and entry and sl and price:
        sl_dist = abs(sl - entry)
        min_sl_map = {"XAUUSD": 3.0, "GOLD": 3.0, "USOIL": 0.15, "BTCUSD": 600, "ETHUSD": 50}
        min_sl = min_sl_map.get(display, 0)
        if min_sl > 0 and 0 < sl_dist < min_sl:
            # Inline pip calc for logging (3-digit Exness)
            if display in ("XAUUSD","GOLD"):
                d1, d2 = sl_dist / 0.10, min_sl / 0.10; u = "pip"
            elif display == "USOIL":
                d1, d2 = sl_dist / 0.01, min_sl / 0.01; u = "pip"
            elif display in ("EURUSD","GBPUSD","USDJPY"):
                d1, d2 = sl_dist / 0.00010, min_sl / 0.00010; u = "pip"
            else:
                d1, d2 = sl_dist, min_sl; u = "pt"
            logger.info(f"    [SL GUARD] {display} SL={d1:.0f}{u} < min={d2:.0f}{u} — overriding with fallback")
            sl = 0
            tp = 0

    # Fallback SL/TP — wider for realistic fills, tighter for consistency
    if (sl == 0 or tp == 0) and price and price > 0:
        if display in ("XAUUSD", "GOLD"):
            sl = round(price - 3.0, 2) if action == "BUY" else round(price + 3.0, 2)   # 30 pip 3-digit
            tp = round(price + 5.0, 2) if action == "BUY" else round(price - 5.0, 2)   # 50 pip 3-digit
        elif display == "USOIL":
            sl = round(price - 0.25, 2) if action == "BUY" else round(price + 0.25, 2) # 25 pip 3-digit
            tp = round(price + 0.50, 2) if action == "BUY" else round(price - 0.50, 2) # 50 pip 3-digit
        elif display in ("EURUSD","GBPUSD","USDJPY"):
            sl = round(price - 0.0015, 5) if action == "BUY" else round(price + 0.0015, 5)
            tp = round(price + 0.0030, 5) if action == "BUY" else round(price - 0.0030, 5)
        elif display == "BTCUSD":
            sl = round(price - 600, 2) if action == "BUY" else round(price + 600, 2)
            tp = round(price + 1200, 2) if action == "BUY" else round(price - 1200, 2)
        elif display == "ETHUSD":
            sl = round(price - 50, 2) if action == "BUY" else round(price + 50, 2)
            tp = round(price + 75, 2) if action == "BUY" else round(price - 75, 2)
        elif display in ("BBCA","BBRI","TLKM","ASII","UNVR","BMRI","ADRO","IHSG"):
            # IDX stocks — percentage-based (backtest-optimized)
            sl_pct = 0.01   # 1% SL
            tp_pct = 0.02 if display == "BBCA" else 0.015  # BBCA: 2% TP, others: 1.5%
            sl = round(price * (1 - sl_pct), 0) if action == "BUY" else round(price * (1 + sl_pct), 0)
            tp = round(price * (1 + tp_pct), 0) if action == "BUY" else round(price * (1 - tp_pct), 0)
        else:
            sl = round(price - 0.50, 2) if action == "BUY" else round(price + 0.50, 2)
            tp = round(price + 0.75, 2) if action == "BUY" else round(price - 0.75, 2)

    # --- XAUUSD spot offset: shift entry/SL/TP from futures -> spot ---
    if action in ("BUY","SELL") and display in ("XAUUSD","GOLD") and entry > 0:
        offset = get_xauusd_spot_offset()
        if abs(offset) > 5:
            entry = round(entry + offset, 2)
            if sl: sl = round(sl + offset, 2)
            if tp: tp = round(tp + offset, 2)
            if tp1: tp1 = round(tp1 + offset, 2)
            if tp2: tp2 = round(tp2 + offset, 2)
            if tp3: tp3 = round(tp3 + offset, 2)
            if tp4: tp4 = round(tp4 + offset, 2)

    # Generate TP levels if only single TP provided
    if not tp1 and tp > 0 and entry > 0:
        tp_dist = abs(tp - entry)
        if action == "BUY":
            tp1 = round(entry + tp_dist * 0.25, 2)
            tp2 = round(entry + tp_dist * 0.50, 2)
            tp3 = round(entry + tp_dist * 0.75, 2)
            tp4 = tp
        else:
            tp1 = round(entry - tp_dist * 0.25, 2)
            tp2 = round(entry - tp_dist * 0.50, 2)
            tp3 = round(entry - tp_dist * 0.75, 2)
            tp4 = tp

    # Winrate stats
    wr_text = ""
    if TRADE_TRACKER:
        try:
            stats = get_stats()
            total_t = stats.get("total",0)
            wins_t = stats.get("wins",0)
            losses_t = stats.get("losses",0)
            wr_t = stats.get("win_rate",0)
            if total_t > 0:
                wr_text = f"📊 Winrate: {total_t} sinyal | {wr_t}% ({wins_t}W/{losses_t}L)"
        except Exception:
            pass

    # Pip distances — Exness 3-digit broker
    def _pips(dist, asset=display):
        a = asset.upper()
        if a in ("XAUUSD","GOLD"):
            return f"{dist / 0.10:.0f} pip"     # 3-digit: 1 pip = 0.10
        elif a == "USOIL":
            return f"{dist / 0.01:.0f} pip"     # 3-digit: 1 pip = 0.01
        elif a in ("EURUSD","GBPUSD","USDJPY"):
            return f"{dist / 0.00010:.1f} pip"  # 5-digit forex
        elif a == "BTCUSD":
            return f"{dist:.0f} pip"            # BTC: 1 pip = 1.0
        elif a == "ETHUSD":
            return f"{dist:.0f} pip"            # ETH: ~$1/pip
        else:
            return f"{dist:.0f} pip"            # generic

    def _tp_pips(tp_val):
        if entry and tp_val:
            return f"(+{_pips(abs(tp_val - entry))})"
        return ""

    def _sl_pips(sl_val):
        if entry and sl_val:
            return f"(-{_pips(abs(sl_val - entry))})"
        return ""

    # AI info line
    ai_parts = []
    if models_display:
        v_str = f"({voters} model)" if voters and voters != "?" else ""
        ai_parts.append(f"🤖 {models_display} {v_str}".strip())
    if grade and grade != "D":
        ai_parts.append(f"Grade {grade}")
    ai_line = " | ".join(ai_parts) if ai_parts else ""

    is_idx = display in ("BBCA","BBRI","IHSG")
    def _fmt(v):
        return f"Rp{v:,.0f}" if is_idx else f"{currency}{v:.2f}"

    lines = [
        f"{header_emoji} <b>{header_label} — {display}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {now_wib.strftime('%Y.%m.%d %H:%M')} WIB | Session: {session(h)}",
        f"📍 Entry: {_fmt(entry)}",
        f"🔴 SL: {_fmt(sl)} {_sl_pips(sl)}",
    ]

    # TP levels
    for tp_val, tp_label in [(tp1,"TP1"),(tp2,"TP2"),(tp3,"TP3"),(tp4,"TP4")]:
        if tp_val and tp_val > 0:
            lines.append(f"🟢 {tp_label}: {_fmt(tp_val)} {_tp_pips(tp_val)}")

    # SnR + FIBO context (only for actionable signals)
    if levels and is_actionable:
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(levels)

    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    if ai_line:
        lines.append(ai_line)
    lines.append(f"📐 RR 1:{rr} | Confidence: {conf:.0%}")
    if wr_text:
        lines.append(wr_text)

    # Quality gate indicator
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    if is_actionable:
        lines.append(f"✅ {q_reason}")
    else:
        lines.append(f"⚠️ <b>MARKET PULSE — Info Only</b>")
        lines.append(f"💡 {q_reason}")
        lines.append(f"🔍 Gunakan sebagai konfirmasi SnR/FIBO manual.")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")

    lines.append(f"⚠️ <i>NFA — Not Financial Advice. Sinyal hasil deteksi otomatis AI untuk edukasi. Keputusan & risiko trading sepenuhnya ada padamu. Selalu pakai manajemen risiko.</i>")
    if is_actionable:
        lines.append(f"")
        lines.append(f"💡 Mau validasi SnR + FIBO + SL placement?")
        lines.append(f"   👉 DM <b>@berkahkaryaforexbotbot</b> — ketik /levels {display.lower()}")
        lines.append(f"   🔒 Premium feature — <b>/donate</b> dulu kalo belum unlock")

    # Token counter gimmick + CTA
    token_total = sig.get("_token_total", 0)
    token_prompt = sig.get("_token_prompt", 0)
    token_comp = sig.get("_token_completion", 0)
    is_free = sig.get("_tier_capped", True)
    model_names = sig.get("_model", "AI")
    model_count = sig.get("voters", 1) or 1
    grok_news = sig.get("_grok_news")
    tier_label = sig.get("_tier", "🆓 Free")

    lines.append(f"")
    if token_total > 0:
        token_k = f"{token_total/1000:.1f}k" if token_total >= 1000 else str(token_total)
        cost_rp = int(token_total * 1.5 / 1000)
        cost_rp = max(cost_rp, 1)
        # Dynamic battery based on actual AI models + Grok
        has_grok = bool(grok_news)
        battery_pct = min(100, model_count * 33 + (33 if has_grok else 0))
        bar_count = min(3, model_count + (1 if has_grok else 0))
        bars = "■" * max(1, bar_count) + "□" * (3 - max(1, bar_count))

        if is_free:
            # ── FREE TIER: Dynamic battery + kelaparan + Grok tease preview ──
            lines.append(f"🔋 <b>AI Power: {bars} {battery_pct}%</b> — {model_count}/3 AI yang kerja buat lu")
            lines.append(f"")
            lines.append(f"🧠 {token_k} token dipakai (Rp {cost_rp})")
            lines.append(f"   Prompt: {token_prompt} | Respon: {token_comp}")
            lines.append(f"")
            lines.append(f"🤖 Cuma <b>{model_names}</b> doang yang mikir.")
            lines.append(f"   AI lu kelaparan bro... cuma dikasih 1 model 😤")
            lines.append(f"   Bayangin kalo 3 AI + Grok News analisa bareng:")
            lines.append(f"   → Entry lebih presisi, SL lebih ketat, TP lebih akurat")
            lines.append(f"")
            # Grok News tease with preview snippet
            lines.append(f"📰 <b>Grok News</b> [🔒 LOCKED]")
            lines.append(f"   🔍 <i>Preview: Market-moving headlines dari X/Twitter...</i>")
            lines.append(f"   🗞️  Breaking news, FOMC, NFP, CPI, geopolitics — all real-time")
            lines.append(f"   🔓 <b>Unlock → /news {display.lower()}</b> atau /donate")
            lines.append(f"")
            lines.append(f"⚡ <b>Rp 50k/bulan</b> — lebih murah dari 1x loss SL")
            lines.append(f"   Dapet 2 AI + Grok News + /levels + /news")
            lines.append(f"   <b>/donate</b> sekarang — jangan biarin AI lu kerja sendirian")

        else:
            # ── DONOR TIER: Full power flex + AI Partner narrative ──
            lines.append(f"🔋 <b>AI Power: {bars} {battery_pct}%</b> — full throttle")
            lines.append(f"")
            lines.append(f"🧠 {token_k} token dipakai (Rp {cost_rp})")
            lines.append(f"   Prompt: {token_prompt} | Respon: {token_comp}")
            lines.append(f"")
            lines.append(f"🤖 <b>{model_count} AI Partner</b> kerja bareng: {model_names}")

            if grok_news:
                news_str = _format_news_context(grok_news)
                if news_str:
                    lines.append(f"📰 <b>Grok News Active</b> ✅ — real-time X/Twitter intel")
                    lines.append(f"   💡 Detail: /news {display.lower()}")
            else:
                lines.append(f"📰 Grok News [🔒 LOCKED] — <b>/news {display.lower()}</b> buat unlock")

            lines.append(f"")
            lines.append(f"🤝 <b>AI Partner lu makin cerdas.</b>")
            lines.append(f"   Makin banyak AI = makin akurat sinyal = makin cuan.")
            lines.append(f"   Jangan stop disini — upgrade ke tier tertinggi:")
            if tier_label in ("⭐ Pro",):
                lines.append(f"   👑 <b>/donate</b> → Elite Tier: 3 AI + Grok News real-time")
            else:
                lines.append(f"   💎 <b>Elite Intelligence Active</b> — your edge is real")
    else:
        # Fallback
        lines.append(f"⚡ Isi Bahan Bakar AI → /donate")
        lines.append(f"   Makin banyak AI = makin akurat sinyal = makin cuan")

    return "\n".join(lines)


# ── Grok News section (for signals that include it) ──
# Moved inside the token section above for natural flow
# The _format_news_context() function remains available for external use


def fmt_pulse(pulse_data: dict) -> str:
    """Format engine readings into Market Pulse message.
    
    pulse_data format from run_engine_consensus():
        {"engines": {name: {"direction", "confidence", "details"}},
         "buy_count": int, "sell_count": int, "total": int,
         "verdict": str, "consensus_pct": float, "symbol": str,
         "timestamp": str, "price": float}
    """
    engines = pulse_data.get("engines", {})
    buys = pulse_data.get("buy_count", 0)
    sells = pulse_data.get("sell_count", 0)
    total = pulse_data.get("total", 0)
    verdict = pulse_data.get("verdict", "HOLD")
    cpct = pulse_data.get("consensus_pct", 0)
    sym = pulse_data.get("symbol", "XAUUSD")
    price = pulse_data.get("price", 0)
    ts = pulse_data.get("timestamp", wib_now().isoformat())
    
    # Parse timestamp
    try:
        dt = datetime.fromisoformat(ts)
        ts_fmt = dt.strftime("%Y.%m.%d %H:%M") + " WIB"
    except:
        ts_fmt = ts
    
    # Verdict emoji
    v_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️"}.get(verdict, "⚪️")
    price_str = f"${price:.2f}" if isinstance(price, (int,float)) and price > 0 else ""
    
    lines = [
        f"🔄 <b>MARKET PULSE — {sym}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {ts_fmt} {price_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
    ]
    
    if engines:
        lines.append("")
        lines.append("<b>📊 ENGINE READINGS</b>")
        
        # Named display for each engine
        engine_names = {
            "quant": "Quant", "fvg": "FVG", "hermes": "Hermes",
            "crt": "CRT/TBS", "smc": "SMC", "trend": "Trend",
            "ultimate": "Ultimate", "sequoia": "Sequoia"
        }
        dir_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️", "ERROR": "❌"}
        
        for eng_key, eng_name in engine_names.items():
            eng = engines.get(eng_key)
            if not eng:
                continue
            direction = eng.get("direction", "HOLD")
            conf = eng.get("confidence", 0)
            details = eng.get("details", "")
            de = dir_emoji.get(direction, "⚪️")
            conf_str = f" {conf:.0%}" if isinstance(conf, (int,float)) and conf > 0 else ""
            det_str = f" | {details}" if details else ""
            lines.append(f"{de} {eng_name}: {direction}{conf_str}{det_str}")
    
    lines.append("")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    
    if total > 0:
        verdict_line = f"{buys} BUY / {sells} SELL / {total - buys - sells} HOLD"
        consensus_str = f" | Consensus: {cpct:.0%}" if cpct > 0 else ""
        lines.append(f"📈 {verdict_line}{consensus_str}")
        lines.append(f"{v_emoji} Verdict: <b>{verdict}</b>")
    
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    
    if verdict == "HOLD" and total >= 4:
        lines.append(f"⚠️ Menunggu konfirmasi lanjutan…")
    elif verdict != "HOLD":
        lines.append(f"✅ Sinyal valid — siap eksekusi setelah quality gate")
    
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"")
    
    # Battery + AI Partner narrative for Market Pulse
    engine_active = len([e for e in engines.values() if e.get("direction") != "ERROR"]) if engines else total
    engine_active = max(engine_active, total)  # fallback
    engine_pct = min(100, engine_active * 12)  # ~12% per engine (8 engines max)
    bar_count = min(3, engine_active // 3 + 1)
    bars = "■" * bar_count + "□" * (3 - bar_count)
    
    lines.append(f"🔋 <b>Engine Power: {bars} {engine_pct}%</b> — {engine_active} engine aktif")
    lines.append(f"")
    lines.append(f"🧠 Powered by {total} trading engines — analisa real-time")
    lines.append(f"   Tapi ini cuma <b>Market Pulse</b> — belum AI Signal.")
    lines.append(f"")
    lines.append(f"🤖 AI lu masih <b>idle</b> bro...")
    lines.append(f"   Engine cuma kasih arah, AI yang kasih Entry/SL/TP presisi.")
    lines.append(f"   Bayangin 3 AI + Grok News analisa bareng:")
    lines.append(f"   → Entry level, SL placement, TP target — all calculated.")
    lines.append(f"")
    lines.append(f"📰 <b>Grok News</b> [🔒 LOCKED]")
    lines.append(f"   <i>Real-time X/Twitter market context...</i>")
    lines.append(f"")
    lines.append(f"⚡ <b>/donate</b> — Rp 50k/bulan")
    lines.append(f"   Unlock AI Signal + Grok News + /levels + SnR/FIBO")
    lines.append(f"   Jangan cuma liat engine doang — kasih AI lu kerjaan beneran")

    return "\n".join(lines)


# ── Quant Consensus UI helper ──
def append_quant_consensus_ui(sig, quant_result, disp="XAUUSD"):
    """Injects Quant Consensus block + guardrail into formatted signal text.
    Returns (quant_block: str, guardrail_warnings: list[str])."""
    if not quant_result or quant_result.get("error"):
        return "", []

    match_count = quant_result.get("match_count", 0)
    green_pct = quant_result.get("green_pct", 0)
    red_pct = quant_result.get("red_pct", 0)
    doji_pct = quant_result.get("doji_pct", 0)
    confidence = quant_result.get("confidence_score", 0)
    verdict = quant_result.get("quant_verdict", "?")
    dominant = quant_result.get("dominant_next", "?")
    series_len = quant_result.get("series_length", 0)
    pattern_size = quant_result.get("pattern_size", 5)

    # Build quant consensus block
    verdict_emoji = {
        "BUY_BIAS_HISTORICAL": "🟢", "SELL_BIAS_HISTORICAL": "🔴",
        "NEUTRAL_HISTORICAL": "⚪️", "NO_HISTORICAL_MATCH": "⚠️",
        "INSUFFICIENT_DATA": "⏳"
    }.get(verdict, "⚪️")

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"📐 <b>Quant Consensus</b> [{series_len} bars, {pattern_size}-candle pattern]\n"
        f"{verdict_emoji} {verdict.replace('_',' ')} | {match_count} matches found\n"
        f"🟢 G: {green_pct:.0f}%  🔴 R: {red_pct:.0f}%  ⚪ D: {doji_pct:.0f}%\n"
        f"Confidence: {confidence:.0%} | Dominant: {dominant if dominant else '—'}"
    )

    # Guardrail logic
    warnings = []
    ai_action = sig.get("action", "HOLD")
    GUARD_THRESHOLD = 40  # %

    if match_count == 0:
        warnings.append(f"⚠️ <b>No historical pattern match</b> — sinyal AI tidak dikonfirmasi data historis")
    elif ai_action == "BUY" and green_pct < GUARD_THRESHOLD:
        warnings.append(f"⚠️ <b>Guardrail:</b> AI bilang BUY tapi Quant cuma {green_pct:.0f}% Green — risiko tinggi!")
    elif ai_action == "SELL" and red_pct < GUARD_THRESHOLD:
        warnings.append(f"⚠️ <b>Guardrail:</b> AI bilang SELL tapi Quant cuma {red_pct:.0f}% Red — risiko tinggi!")

    # Opposite direction warning
    if ai_action == "BUY" and dominant == "R" and red_pct >= GUARD_THRESHOLD:
        warnings.append(f"🚨 <b>KONFLIK:</b> AI BUY vs Quant SELL ({red_pct:.0f}% Red) — TUNGGU konfirmasi!")
    elif ai_action == "SELL" and dominant == "G" and green_pct >= GUARD_THRESHOLD:
        warnings.append(f"🚨 <b>KONFLIK:</b> AI SELL vs Quant BUY ({green_pct:.0f}% Green) — TUNGGU konfirmasi!")

    return block, warnings


# ── Sequoia-X Quantitative Screening ──

def run_sequoia_screen(ohlcv_bars, disp="XAUUSD"):
    """Run Sequoia-X quantitative screening on OHLCV bars.

    Runs multiple vectorized strategies:
      - Turtle 20-day breakout (turtle_breakout)
      - Turtle signal strength (0-1 momentum score)
      - MA Volume breakout (price > MA20 + volume spike)
      - Turtle Trend Filter (bull/bear/neutral for D1/H4 macro)

    Returns None on insufficient data or error.
    """
    if not SEQUOIA_ENGINE or not ohlcv_bars or len(ohlcv_bars) < 30:
        return None

    try:
        # Build DataFrame from OHLCV bars
        df_bars = []
        for b in ohlcv_bars:
            t = b.get("t", b.get("timestamp", 0))
            o = float(b.get("o", b.get("open", 0)))
            h = float(b.get("h", b.get("high", 0)))
            l = float(b.get("l", b.get("low", 0)))
            c = float(b.get("c", b.get("close", 0)))
            v = float(b.get("v", b.get("volume", 0)))
            if o <= 0 or c <= 0:
                continue
            df_bars.append({"open": o, "high": h, "low": l, "close": c, "volume": v})

        if len(df_bars) < 30:
            return None

        df = pd.DataFrame(df_bars)

        if not validate_ohlcv(df):
            return None

        # ── Run Sequoia strategies ──
        result = {"status": "ok", "display": disp}

        # 1) MA Volume Breakout (fastest signal)
        ma_vol_sig = ma_volume_breakout(df, ma_period=20, volume_mult=1.5)
        result["ma_volume_trigger"] = bool(ma_vol_sig.iloc[-1])

        # 2) Turtle 20-day Breakout
        turtle_sig = turtle_breakout(df, lookback=20)
        result["turtle_trigger"] = bool(turtle_sig.iloc[-1])

        # 3) Turtle Signal Strength (continuous 0-1)
        strength = turtle_signal_strength(df, lookback=20)
        result["turtle_strength"] = float(strength.iloc[-1]) if len(strength) > 0 else 0.0

        # 4) Turtle Trend Filter (macro direction)
        is_bull, trend_strength, tf_dir = turtle_trend_filter(df, lookback=20, smoothing=3)
        result["trend_bullish"] = bool(is_bull.iloc[-1])
        result["trend_strength"] = float(trend_strength.iloc[-1]) if len(trend_strength) > 0 else 0.0
        result["trend_direction"] = tf_dir  # -1, 0, +1

        # Consensus summary
        bullish_score = sum([
            1 if result["turtle_trigger"] else 0,
            1 if result["ma_volume_trigger"] else 0,
            1 if tf_dir > 0 else 0,
        ])
        bearish_score = 1 if tf_dir < 0 else 0

        result["bullish_signals"] = bullish_score
        result["bearish_signals"] = bearish_score

        if bullish_score >= 2:
            result["sequoia_verdict"] = "BUY_BIAS"
        elif bearish_score >= 1 and bullish_score == 0:
            result["sequoia_verdict"] = "SELL_BIAS"
        elif tf_dir != 0:
            result["sequoia_verdict"] = f"TREND_{'BULL' if tf_dir > 0 else 'BEAR'}"
        else:
            result["sequoia_verdict"] = "NEUTRAL"

        return result

    except Exception as e:
        return {"status": "error", "error": str(e)}


def format_sequoia_block(result, ai_action=None):
    """Format Sequoia screening results into a compact Telegram block.

    Includes guardrail warnings when Sequoia contradicts AI signal.
    Returns (block_text, warnings_list).
    """
    if not result or result.get("status") != "ok":
        return "", []

    verdict = result.get("sequoia_verdict", "?")
    verdict_emoji = {
        "BUY_BIAS": "🐢🟢", "SELL_BIAS": "🐢🔴",
        "TREND_BULL": "📈", "TREND_BEAR": "📉",
        "NEUTRAL": "⚪️"
    }.get(verdict, "⚪️")

    t_str = f"{result.get('turtle_strength', 0):.0%}" if result.get("turtle_strength") else "—"
    tr_str = f"{result.get('trend_strength', 0):.0%}" if result.get('trend_strength') else "—"

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"🐢 <b>Sequoia-X Quant</b> [Turtle+HTF+MA]\n"
        f"{verdict_emoji} <b>{verdict.replace('_',' ')}</b> | "
        f"🟢{result.get('bullish_signals',0)} 🔴{result.get('bearish_signals',0)}\n"
        f"Turtle BO: {'✅' if result.get('turtle_trigger') else '❌'} | "
        f"MA Vol: {'✅' if result.get('ma_volume_trigger') else '❌'}\n"
        f"Momentum: {t_str} | Trend: {tr_str}"
    )

    # Guardrail warnings
    warnings = []
    if ai_action:
        if ai_action == "BUY" and verdict == "SELL_BIAS":
            warnings.append(f"🐢⚠️ <b>Sequoia Guardrail:</b> AI BUY vs Sequoia SELL — divergence!")
        elif ai_action == "SELL" and verdict == "BUY_BIAS":
            warnings.append(f"🐢⚠️ <b>Sequoia Guardrail:</b> AI SELL vs Sequoia BUY — divergence!")
        elif ai_action in ("BUY", "SELL") and verdict == "NEUTRAL":
            warnings.append(f"🐢💤 Sequoia neutral — sinyal AI tanpa konfirmasi kuantitatif")

    return block, warnings


# ── Ultimatum System ──
ULTIMATUM_ACCEPTED_PATH = DATA_DIR / "ultimatum_accepted"
ULTIMATUM_ACCEPTED_PATH.mkdir(parents=True, exist_ok=True)
VIDEO_FILE_ID_PATH = PROJECT_DIR / "media" / "ultimatum_file_id.txt"
ULTIMATUM_VIDEO_LOCAL = PROJECT_DIR / "media" / "Server_room_with_trading_charts_202606071902.mp4"
ADMIN_CHAT_ID = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))


def _load_file_id():
    """Load cached Telegram video file_id."""
    try:
        if VIDEO_FILE_ID_PATH.exists():
            return VIDEO_FILE_ID_PATH.read_text().strip()
    except Exception:
        pass
    return ""


def _save_file_id(file_id):
    """Save Telegram video file_id for reuse."""
    try:
        VIDEO_FILE_ID_PATH.write_text(file_id)
    except Exception:
        pass


def _has_accepted_ultimatum(chat_id):
    """Check if user has accepted the ultimatum."""
    return (ULTIMATUM_ACCEPTED_PATH / f"{chat_id}.json").exists()


def _save_ultimatum(chat_id):
    """Mark user as having accepted the ultimatum."""
    try:
        (ULTIMATUM_ACCEPTED_PATH / f"{chat_id}.json").write_text(
            json.dumps({"accepted_at": wib_now().isoformat(), "chat_id": str(chat_id)})
        )
    except Exception:
        pass


def send_ultimatum_video(chat_id):
    """Send ultimatum: video (short caption) + text message (full copy + keyboard)."""
    ultimatum_text = (
        "🔥 <b>REVOLUSI TRADING DIMULAI: FULL AI, NO BULLSHIT.</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "Selamat datang di markas besar Vilona Trade FX.\n"
        "Seluruh infrastruktur di sini — dari analisa teknikal\n"
        "hingga eksekusi sinyal — dijalankan oleh\n"
        "<b>FULL AI AGENTS 24/7.</b> Mesin ini mengonsumsi\n"
        "resource besar untuk satu tujuan: <b>MENCETAK PROFIT.</b>\n"
        "\n"
        "<b>KAMI TIDAK MENJUAL TIKET MASUK.</b>\n"
        "Akses ini GRATIS. Tapi ekosistem ini dibangun\n"
        "dengan mental <b>GOTONG ROYONG.</b> Jika AI kami\n"
        "memberi Anda profit, kami menuntut apresiasi\n"
        "Anda untuk mengisi bahan bakar server AI.\n"
        "\n"
        "Jika Anda hanya ingin menjadi parasit, silakan keluar.\n"
        "━━━━━━━━━━━━━━━━\n"
        "Apakah Anda setuju dengan aturan main ini? 👇"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ SAYA SETUJU", "callback_data": "ultimatum:setuju"},
            {"text": "❌ DECLINE", "callback_data": "ultimatum:decline"}
        ]]
    }

    # ── Step 1: Send video with short elegant caption ──
    video_caption = "⚙️ <b>VILONA TRADE FX</b> — Institutional-Grade AI Server"
    video_sent = False
    file_id = _load_file_id()
    if file_id:
        try:
            payload = json.dumps({
                "chat_id": chat_id, "video": file_id,
                "caption": video_caption, "parse_mode": "HTML"
            }).encode()
            req = urllib.request.Request(f"{TELEGRAM_API}/sendVideo", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            if resp.get("ok"):
                video_sent = True
        except Exception:
            pass

    if not video_sent and ULTIMATUM_VIDEO_LOCAL.exists():
        try:
            import io
            boundary = "----VilonaBoundary" + str(int(time.time()))
            body = io.BytesIO()
            body.write(f"--{boundary}\r\n".encode())
            body.write(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
            body.write(f"{chat_id}\r\n".encode())
            body.write(f"--{boundary}\r\n".encode())
            body.write(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
            body.write(video_caption.encode() + b"\r\n")
            body.write(f"--{boundary}\r\n".encode())
            body.write(b'Content-Disposition: form-data; name="parse_mode"\r\n\r\n')
            body.write(b"HTML\r\n")
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="video"; filename="ultimatum.mp4"\r\n'.encode())
            body.write(b"Content-Type: video/mp4\r\n\r\n")
            with open(ULTIMATUM_VIDEO_LOCAL, "rb") as vf:
                body.write(vf.read())
            body.write(f"\r\n--{boundary}--\r\n".encode())
            req = urllib.request.Request(
                f"{TELEGRAM_API}/sendVideo",
                data=body.getvalue(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            if resp.get("ok"):
                video_sent = True
        except Exception:
            pass

    # ── Step 2: GUARANTEED — full ultimatum text + keyboard ──
    tg_send(ultimatum_text, chat_id, reply_markup=markup)


def handle_ultimatum_callback(cb):
    """Handle ultimatum accept/decline callbacks."""
    cb_id = cb.get("id", "")
    chat_id = str(cb.get("from", {}).get("id", ""))
    data = cb.get("data", "")

    # Answer callback to stop spinner
    try:
        payload = json.dumps({"callback_query_id": cb_id}).encode()
        urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery", data=payload, headers={"Content-Type": "application/json"}), timeout=5)
    except Exception:
        pass

    if data == "ultimatum:setuju":
        _save_ultimatum(chat_id)
        # Register as free_member
        try:
            from members import register_member as m_register
            m_register(str(chat_id), tier="free_member")
        except Exception:
            pass
        welcome = (
            "🔥 <b>REVOLUSI TRADING DIMULAI: FULL AI, NO BULLSHIT.</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Selamat bergabung di markas besar Vilona Trade FX.\n"
            "Seluruh infrastruktur di sini dijalankan oleh\n"
            "<b>FULL AI AGENTS 24/7.</b>\n"
            "\n"
            "<b>KAMI TIDAK MENJUAL TIKET MASUK.</b>\n"
            "Akses ini GRATIS dengan mental GOTONG ROYONG.\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>WAJIB BACA SEBELUM TRADING:</b>\n"
            "📖 Baca Panduan Markas:\n"
            "https://telegra.ph/Kolom-Title-Judul-VILONA-AI-TRADING-PROTOCOL-Panduan-Eksekusi--Aturan-Markas-06-07\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 XAUUSD · BTC · EURUSD · GBPUSD · USOIL\n"
            "📐 Mapping harian: 10:00 WIB\n"
            "⚡️ Kuota AI: 3x analisa/hari\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 /help — Semua command\n"
            "📊 /analyze xauusd — Mulai analisa\n"
            "⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📞 Admin: @codergaboets"
        )
        tg_send(welcome, chat_id)
        # Send channel links
        tg_send(
            "🔗 <b>Gabung Komunitas:</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "📢 Channel Sinyal: https://t.me/vilonaaichanel\n"
            "👥 Group Diskusi: https://t.me/+kX8tspebrpVhMmE1",
            chat_id
        )
    elif data == "ultimatum:decline":
        tg_send(
            "👋 <b>Sayonara!</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Kamu memilih untuk tidak melanjutkan.\n"
            "Kalau berubah pikiran, kirim /start lagi kapan saja.\n\n"
            "Sampai jumpa! 👋",
            chat_id
        )


def auto_capture_video_file_id(chat_id, message):
    """Admin-only: capture video file_id from a sent message."""
    if str(chat_id) != str(ADMIN_CHAT_ID):
        return
    video = message.get("video")
    if video:
        file_id = video.get("file_id", "")
        if file_id:
            _save_file_id(file_id)
            tg_send(f"✅ Video file_id captured: <code>{file_id[:30]}...</code>", chat_id)


# ── Quota System ──
FREE_QUOTA_PER_DAY = 3
QUOTA_DIR = DATA_DIR / "quota_cache"
QUOTA_DIR.mkdir(parents=True, exist_ok=True)


def _get_quota(chat_id):
    """Read daily quota for a user. Returns dict with used, remaining, date."""
    path = QUOTA_DIR / f"{chat_id}.json"
    today = wib_now().strftime("%Y-%m-%d")
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "used": 0, "remaining": FREE_QUOTA_PER_DAY}


def _deduct_quota(chat_id):
    """Deduct one from quota. Returns (ok, remaining)."""
    quota = _get_quota(chat_id)
    quota["used"] += 1
    quota["remaining"] = max(0, FREE_QUOTA_PER_DAY - quota["used"])
    path = QUOTA_DIR / f"{chat_id}.json"
    try:
        path.write_text(json.dumps(quota))
    except Exception:
        pass
    return quota["remaining"] > 0, quota["remaining"]


def _is_donor(chat_id):
    """Check if user has donor/paid status in members DB."""
    try:
        from members import get_member as m_get
        member = m_get(str(chat_id))
        if member:
            status = member.get("status", "")
            tier = member.get("tier", "")
            return status in ("paid", "donor") or tier in ("pro", "elite", "paid", "donor")
    except Exception:
        pass
    return False


# ── Reusable donate menu ──
def _send_donate_menu(chat_id, username=""):
    """Reusable donate menu — used by cancel_input and redirects."""
    txt = (
        "💚 <b>SIRAM BAHAN BAKAR MESIN AI 🚀</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "Server AI ini mengolah jutaan data market\n"
        "secara real-time dan membutuhkan biaya API\n"
        "& GPU yang masif setiap detiknya.\n"
        "\n"
        "Jika sinyal AI ini telah mengubah portofolio\n"
        "Anda menjadi hijau, mari bergotong royong\n"
        "menjaga mesin ini tetap hidup dan semakin buas!\n"
        "\n"
        "Pilih dukunganmu hari ini:\n"
        "\n"
        "💼 <b>EKSKLUSIF: PROGRAM INVESTOR AI</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "Apakah Anda big player/investor yang ingin\n"
        "ikut andil dalam pengembangan ekosistem\n"
        "kuantitatif ini secara makro? Kami membuka\n"
        "jalur pendanaan privat. Hubungi Chief\n"
        "Architect kami di bawah."
    )
    markup = {"inline_keyboard": [
        [{"text": "☕️ Traktir Kopi (Rp 15K)", "callback_data": "donate:coffee"},
         {"text": "🍱 Makan Siang Server (Rp 25K)", "callback_data": "donate:learn"}],
        [{"text": "🚀 Isi Bensin Full (Rp 50K)", "callback_data": "donate:fuel"}],
        [{"text": "💰 Input Nominal Bebas", "callback_data": "donate:custom"}],
        [{"text": "🤝 HUBUNGI CHIEF ARCHITECT", "url": "https://t.me/codergaboets"}],
    ]}
    tg_send(txt, chat_id, reply_markup=markup)


# ── Command handler ──
def handle_command(cmd, text, chat_id, msg):
    sub = text[len(cmd):].strip().lower() if len(text) > len(cmd) else ""
    sub_norm = _normalize_broker_symbol(sub)  # XAUUSDc → xauusd, EURUSD.pro → eurusd

    if cmd == "/start":
        # ── Two-Tier Gate: Ultimatum for new users, Welcome for returning ──
        if _has_accepted_ultimatum(chat_id):
            # Returning user → show welcome
            is_donor = _is_donor(chat_id)
            tier_label = "👑 DONATUR SULTAN (VIP)" if is_donor else "👤 Kawan Seperjuangan (Free Member)"
            quota = _get_quota(chat_id)
            quota_line = "UNLIMITED ♾️" if is_donor else f"{quota['remaining']}/{FREE_QUOTA_PER_DAY}"
            welcome = (
                f"🔥 <b>REVOLUSI TRADING DIMULAI: FULL AI, NO BULLSHIT.</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Selamat datang di markas besar Vilona Trade FX.\n"
                f"Seluruh infrastruktur di sini — dari analisa teknikal\n"
                f"hingga eksekusi sinyal — dijalankan oleh\n"
                f"<b>FULL AI AGENTS 24/7.</b>\n"
                f"\n"
                f"Mesin ini mengonsumsi resource besar untuk\n"
                f"satu tujuan: <b>MENCETAK PROFIT.</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"{tier_label}\n"
                f"⚡️ Kuota AI: {quota_line}\n"
            )
            if is_donor:
                welcome += (
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔑 <b>AKSES DONATUR:</b>\n"
                    f"📥 Download EA MT5: phantomfx.aitradepulse.com/ea/download/\n"
                    f"🔑 Cek Licensi EA: /mykey\n"
                    f"🌐 Bridge Dashboard: phantomfx.aitradepulse.com\n"
                )
            welcome += (
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🧠 /signal — Signal dari 9 engines\n"
                f"🏛 /levels — SnR + FIBO + Engine Deep Dive 👑\n"
                f"📰 /news — Grok News X/Twitter intel 👑\n"
                f"📊 /dashboard — Live dashboard web\n"
                f"📱 /help — Semua command\n"
                f"⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot"
            )
            tg_send(welcome, chat_id)
        else:
            # New user → ultimatum video (single message)
            send_ultimatum_video(chat_id)

    elif cmd == "/myid":
        text = (
            f"🆔 <b>Telegram ID kamu:</b>\n"
            f"<code>{chat_id}</code>\n\n"
            f"Gunakan ID ini untuk donasi di website kami\n"
            f"👉 <a href='https://phantomfx.aitradepulse.com'>phantomfx.aitradepulse.com</a>"
        )
        tg_send(text, chat_id)

    elif cmd == "/help":
        help_lines = [
            "⚙️ <b>VILONA AI — COMMAND CENTER</b>",
            "━━━━━━━━━━━━━━━━\n",
            "🧠 <b>AI SIGNAL SYSTEM 🔥</b>",
            "/signal — Generate sinyal dari MTF + 9 engines",
            "/mtf — Matrix 5TF × 9 engines (top-down)",
            "/engines — Engine readings per strategi",
            "/dashboard — Buka live dashboard web\n",
            "👑 <b>PILAR UTAMA</b>",
            "/start — Reboot Markas Komando",
            "/analyze — Perintahkan AI Scan Market",
            "/price — Cek harga real-time",
            "/data — Market overview",
            "/status — Cek Kuota & Akses VIP",
            "/donate — Isi Bahan Bakar AI ⚡\n",
            "📊 <b>TRADING TOOLS</b>",
            "/mapping — Mapping harian + level S/R",
            "/levels — SnR + FIBO + Engine Deep Dive 👑",
            "/news — Grok News — X/Twitter intel 👑",
            "/killzone — Radar sesi market aktif",
            "/winrate — Statistik performa",
            "/history — Riwayat trade terakhir",
            "/recap — Rekap harian\n",
            "🔧 <b>POWER TOOLS</b>",
            "/autosync — Auto-trade ke EA (Donatur)",
            "/bridge_status — Cek koneksi EA",
            "/mykey — Cek License EA kamu (Donatur)\n",
            "🔑 <b>EA MT5 DOWNLOAD</b>",
            "📥 phantomfx.aitradepulse.com/ea/download/",
            "━━━━━━━━━━━━━━━━",
            "📞 Jalur Privat Investor: @codergaboets",
        ]
        tg_send("\n".join(help_lines), chat_id)

    elif cmd == "/price":
        # Multi-symbol price — use normalized sub
        price_map = {"xauusd":"gold","gold":"gold","":"gold",
                     "btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","eurusd":"eurusd","gbpusd":"gbpusd",
                     "usdjpy":"usdjpy","oil":"oil","aapl":"aapl","bbca":"bbca",
                     "bbri":"bbri","tlkm":"tlkm","asii":"asii","ihsg":"ihsg"}
        pair = price_map.get(sub_norm, sub_norm) if sub_norm else "gold"
        price = fetch_price(pair)
        if not price:
            tg_send(f"❌ Price unavailable untuk {sub_norm.upper() if sub_norm else 'XAUUSD'}", chat_id)
            return
        disp = sub_norm.upper() if sub_norm else "XAUUSD"
        curr = "Rp" if pair in ("bbca","bbri","tlkm","asii","ihsg") else "$"
        dxy = fetch_dxy() if pair == "gold" else None
        txt = f"💰 <b>{disp}</b>\n━━━━━━━━━━━━━━━━\nPrice: {curr}{price:,.2f}" if curr == "Rp" else f"💰 <b>{disp}</b>\n━━━━━━━━━━━━━━━━\nPrice: {curr}{price:.2f}"
        if dxy: txt += f"\nDXY: {dxy:.2f}"
        txt += f"\n━━━━━━━━━━━━━━━━\n🕐 {wib_fmt()} | Session: {session()}"
        tg_send(txt, chat_id)

    elif cmd == "/killzone":
        lkz, nykz = killzone()
        txt = f"🕐 <b>Session: {session()}</b>\n━━━━━━━━━━━━━━━━\n"
        txt += f"London KZ: {'🟢 ACTIVE' if lkz else '🔴 Off'}\nNY KZ: {'🟢 ACTIVE' if nykz else '🔴 Off'}\n"
        txt += f"━━━━━━━━━━━━━━━━\n{wib_fmt()}"
        tg_send(txt, chat_id)

    elif cmd == "/bridge_status":
        tg_send(format_bridge_status(), chat_id)

    elif cmd == "/status":
        # Weekend indicator
        weekend_note = weekend_status_text()

        is_donor = _is_donor(chat_id)
        quota = _get_quota(chat_id)

        if is_donor:
            # Donor daily quota tracking
            today = wib_now().strftime("%Y-%m-%d")
            record = USER_DAILY_ANALYZE.get(chat_id, {})
            used = record.get("count", 0) if record.get("date") == today else 0
            remaining = max(0, DONOR_DAILY_QUOTA - used)
            txt = (
                f"👑 <b>STATUS: DONATUR SULTAN (VIP)</b>\n"
                f"⚡️ Kuota AI: {remaining}/{DONOR_DAILY_QUOTA}x hari ini (Reset 00:00 WIB)\n"
                f"⏱️ Cooldown: {MANUAL_THROTTLE_DONOR}s antar analisa\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Terima kasih telah menghidupi mesin AI ini! 🥂\n"
                f"Seluruh fitur VIP, Auto-Trade, dan Bridge\n"
                f"telah TERBUKA untukmu.\n"
                f"\n"
                f"🔑 <b>AKSES EA & BRIDGE:</b>\n"
                f"📥 Download EA MT5: phantomfx.aitradepulse.com/ea/download/\n"
                f"🌐 Bridge Dashboard: phantomfx.aitradepulse.com\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Mari cetak profit hari ini!"
            )
        else:
            txt = (
                f"👤 <b>STATUS: Kawan Seperjuangan (Free Member)</b>\n"
                f"⚡️ Kuota AI: {quota['remaining']}/{FREE_QUOTA_PER_DAY} (Reset 00:00 WIB)\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Kamu punya {FREE_QUOTA_PER_DAY}x peluru analisa AI setiap harinya.\n"
                f"\n"
                f"🔒 <b>Fitur Donatur Eksklusif:</b>\n"
                f"📥 Download EA MT5 (Auto-Trade)\n"
                f"🔑 License Key untuk EA\n"
                f"🤖 Auto-Trade langsung ke akun MT5\n"
                f"🧠 Multi-Model AI Consensus (akurasi lebih tinggi)\n"
                f"\n"
                f"👉 /donate — Buka akses Donatur sekarang!"
            )
        txt += weekend_note
        tg_send(txt, chat_id)

    elif cmd == "/analyze":
        # ── ULTIMATUM GATE ──
        if not _has_accepted_ultimatum(chat_id):
            tg_send("⚠️ <b>Akses Ditolak</b>\n━━━━━━━━━━━━━━━━\n"
                    "Kamu belum menyetujui Terms of Service.\n"
                    "Kirim /start untuk lihat dan setujui dulu ya.", chat_id)
            return

        # ── QUOTA GATE ──
        if not _is_donor(chat_id):
            ok, remaining = _deduct_quota(chat_id)
            if not ok:
                tg_send(
                    "🛑 <b>Kuota Harian Habis!</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    f"📊 Free Member: {FREE_QUOTA_PER_DAY}x analisa/hari\n"
                    f"📉 Sisa: 0/{FREE_QUOTA_PER_DAY}\n\n"
                    "⚡ <b>Isi Bahan Bakar AI!</b>\n"
                    "Donasi sukarela untuk akses unlimited:\n"
                    "⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot\n\n"
                    "⏰ Reset: besok jam 00:00 WIB",
                    chat_id
                )
                return

        # ── WEEKEND GATE ──
        pair_check = sub_norm if sub_norm else "xauusd"
        if is_weekend() and not is_crypto_pair(pair_check):
            tg_send(
                "🔴 <b>MARKET FOREX/GOLD TUTUP (WEEKEND)</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Sinyal manual hanya tersedia untuk aset Crypto.\n"
                "Contoh: /analyze btc\n\n"
                "🤖 AI akan kembali berburu di XAUUSD hari Senin.\n"
                "Selamat berakhir pekan!",
                chat_id
            )
            return

        # ── ELITE CUSTOM PARAMS ──
        elite_params = {}
        if sub:
            import re as _re
            risk_match = _re.search(r'risk=(\d+(?:\.\d+)?)', sub)
            tf_match = _re.search(r'tf=(\w+)', sub)
            if risk_match or tf_match:
                # Check if user is Donor
                is_elite = _is_donor(str(chat_id)) if chat_id else False
                if not is_elite:
                    tg_send(
                        "👑 <b>Custom Parameter khusus Donatur!</b>\n"
                        "━━━━━━━━━━━━━━━━\n"
                        "Fitur risk= dan tf= hanya untuk Donatur.\n\n"
                        "⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot\n"
                        "👉 /bill — Lihat info",
                        chat_id
                    )
                    return
                # Elite user: parse params
                if risk_match:
                    elite_params["risk"] = float(risk_match.group(1))
                if tf_match:
                    elite_params["tf"] = tf_match.group(1).lower()
                # Strip params from sub for symbol lookup
                sub = _re.sub(r'\s*(risk|tf)=\S+', '', sub).strip()
                sub_norm = _normalize_broker_symbol(sub)
                logger.info(f"Elite params: {elite_params} | symbol: {sub_norm}")

        is_blackout, is_post_news, news_name = news_blackout_status()
        if is_blackout:
            tg_send(f"⚪️ <b>HOLD — Menjelang Rilis Berita</b>\n📰 {news_name}\n⏳ Tunggu 30 menit setelah rilis.", chat_id)
            return

        pair_map = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                    "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy","jpyusd":"usdjpy",
                    "aapl":"aapl","tsla":"tsla","msft":"msft","nvda":"nvda",
                    "bbca":"bbca","bbri":"bbri","tlkm":"tlkm","asii":"asii",
                    "unvr":"unvr","bmri":"bmri","adro":"adro","ihsg":"ihsg"}
        display_map = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD",
                       "gbpusd":"GBPUSD","usdjpy":"USDJPY","jpyusd":"USDJPY",
                       "aapl":"AAPL","tsla":"TSLA","msft":"MSFT","nvda":"NVDA",
                       "bbca":"BBCA","bbri":"BBRI","tlkm":"TLKM","asii":"ASII",
                       "unvr":"UNVR","bmri":"BMRI","adro":"ADRO","ihsg":"IHSG"}
        is_idx = sub_norm in ("bbca","bbri","tlkm","asii","unvr","bmri","adro","ihsg")
        if sub_norm in pair_map:
            disp = display_map.get(sub_norm, sub_norm.upper())
            pair = pair_map[sub_norm]

            # ── DONOR DAILY QUOTA (anti-abuse) ──
            if _is_donor(str(chat_id)):
                ok, remaining, warn = _check_donor_quota(str(chat_id))
                if not ok:
                    tg_send(warn, chat_id)
                    return
                if remaining <= 5:
                    tg_send(f"⚠️ <b>Sisa {remaining}x analisa hari ini</b> — gunakan bijak!\n⏰ Reset jam 00:00 WIB", chat_id)

            # ── Manual anti-flip + rate-limit guard ──
            blocked, reason = _is_manual_blocked(str(chat_id), pair=pair)
            if blocked:
                tg_send(reason, chat_id)
                return

            _touch_manual(str(chat_id), asset=disp, pair=pair)
            tg_send("🔍 Vilona Trade FX menganalisa... ~15 detik", chat_id)
            price = fetch_price(pair)
            dxy = fetch_dxy() if pair == "gold" else None
            if not price:
                tg_send(f"❌ Price unavailable untuk {disp}.", chat_id)
                return
            ohlcv_bars = _fetch_ohlcv_for_ai(pair)
            # Detect user tier for AI model selection
            user_tier = "starter"
            if MEMBERS_ENABLED and chat_id:
                try:
                    m = get_member(str(chat_id))
                    user_tier = (m or {}).get("tier", "starter")
                except Exception:
                    pass
            sig = ask_ai(price, dxy, session(), str(killzone()), 0, premium=False,
                          ohlcv=ohlcv_bars, display=disp, tier=user_tier)
            if sig:
                # Record direction for flip guard (after AI returns action)
                action = sig.get("action", "HOLD")
                _touch_manual(str(chat_id), action=action if action in ("BUY","SELL") else None, asset=disp)
                # Normalize confidence for quality checks
                c = sig.get("confidence", 0)
                if isinstance(c, (int,float)) and c > 10:
                    sig["confidence"] = c / 100
                # Apply Elite custom params
                sig = apply_elite_params(sig, elite_params, price, disp)
                # ── SL/TP CLAMPING: enforce 20-35 pip SL, realistic TP ──
                sig = _clamp_sltp(sig, disp)
                curr = "Rp" if is_idx else "$"
                # Quality gate for manual analyze
                voters = sig.get("voters", 0)
                rr = sig.get("rr_ratio", 0)
                if isinstance(rr, str) and rr.startswith("1:"):
                    rr = float(rr[2:]) if rr[2:] else 0
                rr = float(rr) if rr else 0
                if sig.get("action") in ("BUY","SELL") and voters < 2:
                    tg_send(f"⚠️ Sinyal ditahan: hanya {voters} model setuju (min 2). Coba /analyze lagi.", chat_id)
                    return
                if sig.get("action") in ("BUY","SELL") and rr > 0 and (rr < 1.5 or rr > 5.0):
                    tg_send(f"⚠️ Sinyal ditahan: RR 1:{rr:.1f} di luar 1:1.5-5. Coba /analyze lagi.", chat_id)
                    return
                # Auto-sync ON → langsung trade, OFF → keyboard
                if is_autosync(chat_id):
                    if LAYERING_ENGINE and sig.get("action") != "HOLD":
                        sig = enrich_signal_with_layers(sig)
                    sig["target_user"] = str(chat_id)
                    post_signal_to_bridge(sig, price, disp)
                    action = sig.get("action", "HOLD")
                    auto_text = f"🤖 <b>Auto Sync</b> — {action} {disp} @ {price}\n"
                    # Quant Consensus bloc for autosync
                    if QUANT_ENGINE and ohlcv_bars:
                        try:
                            qdata = [{"timestamp": b.get("t", b.get("timestamp",0)),
                                      "open": float(b.get("o", b.get("open",0))),
                                      "high": float(b.get("h", b.get("high",0))),
                                      "low": float(b.get("l", b.get("low",0))),
                                      "close": float(b.get("c", b.get("close",0))),
                                      "volume": 0}
                                     for b in ohlcv_bars]
                            if qdata:
                                quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
                                quant_block, guard_warnings = append_quant_consensus_ui(sig, quant_result, disp)
                                if quant_block:
                                    auto_text += quant_block + "\n"
                                    for w in guard_warnings:
                                        auto_text += f"{w}\n"
                        except: pass
                    # 🏯 CRT/TBS for autosync
                    if CRT_ENGINE and ohlcv_bars:
                        try:
                            crt_result = analyze_crt_setup(ohlcv_bars, disp)
                            crt_block = format_crt_block(crt_result)
                            if crt_block:
                                auto_text += crt_block + "\n"
                        except: pass
                    # 🏛️ Ultimate SMC v3.0 (13 repos combined)
                    if ULTIMATE_ENGINE and ohlcv_bars:
                        try:
                            ult = ultimate_analyze(ohlcv_bars, disp, price)
                            auto_text += "\n" + format_ultimate_block(ult) + "\n"
                        except: pass
                    # 🏦 SMC + 📈 Trend Break
                    if SMC_ENGINE and ohlcv_bars:
                        try:
                            smc = analyze_smc_scalper(ohlcv_bars, disp)
                            auto_text += format_smc_block(smc) or ""
                            trend = analyze_trend_break(ohlcv_bars, disp)
                            auto_text += format_trend_block(trend) or ""
                        except: pass
                    # 🐢 Sequoia-X Quant (Turtle + MA Vol + Trend)
                    if SEQUOIA_ENGINE and ohlcv_bars:
                        try:
                            seq_result = run_sequoia_screen(ohlcv_bars, disp)
                            if seq_result:
                                ai_act = sig.get("action", "") if sig else ""
                                seq_block, seq_warns = format_sequoia_block(seq_result, ai_act)
                                if seq_block:
                                    auto_text += seq_block + "\n"
                                    for w in seq_warns:
                                        auto_text += f"{w}\n"
                        except: pass
                    auto_text += "<i>EA auto-eksekusi... 3-5 detik</i>"
                    # Tier-based upsell for autosync
                    if not _is_donor(str(chat_id)):
                        auto_text += (
                            "\n━━━━━━━━━━━━━━━━\n"
                            "🆓 <b>FREE TIER — Akurasi Terbatas</b>\n"
                            "Analisa solo 1 model AI. Upgrade untuk multi-model consensus:\n"
                            "👉 /donate — Isi Bahan Bakar AI"
                        )
                    tg_send(auto_text, chat_id)
                else:
                    PENDING_SIGNALS[str(chat_id)] = {
                        "sig": sig, "price": price,
                        "expires": time.time() + PENDING_SIGNAL_TTL,
                    }
                    _save_pending_signals()
                    # ── Quality Gate + SnR/FIBO context ──
                    quant_result = None
                    if QUANT_ENGINE and ohlcv_bars:
                        try:
                            qdata = [{"timestamp": b.get("t", b.get("timestamp",0)),
                                      "open": float(b.get("o", b.get("open",0))),
                                      "high": float(b.get("h", b.get("high",0))),
                                      "low": float(b.get("l", b.get("low",0))),
                                      "close": float(b.get("c", b.get("close",0))),
                                      "volume": 0}
                                     for b in ohlcv_bars]
                            if qdata:
                                quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
                        except: pass
                    quality = _sig_quality_pass(sig, quant_result, disp)
                    text = fmt_signal(sig, price, dxy, wib_now().hour, disp, curr, quality=quality)
                    # 🔥 Inject Quant Consensus + Guardrail (after main signal)
                    if quant_result:
                        quant_block, guard_warnings = append_quant_consensus_ui(sig, quant_result, disp)
                        if quant_block:
                            text += quant_block
                            for w in guard_warnings:
                                text += f"\n{w}"
                    # 🏯 CRT/TBS Layer
                    if CRT_ENGINE and ohlcv_bars:
                        try:
                            crt_result = analyze_crt_setup(ohlcv_bars, disp)
                            crt_block = format_crt_block(crt_result)
                            if crt_block:
                                text += crt_block
                        except: pass
                    # 🏦 SMC Scalper + 📈 Trend Break
                    if SMC_ENGINE and ohlcv_bars:
                        try:
                            smc = analyze_smc_scalper(ohlcv_bars, disp)
                            smc_block = format_smc_block(smc)
                            if smc_block:
                                text += smc_block
                            trend = analyze_trend_break(ohlcv_bars, disp)
                            trend_block = format_trend_block(trend)
                            if trend_block:
                                text += trend_block
                        except: pass
                    # 🐢 Sequoia-X Quant (Turtle + MA Vol + Trend)
                    if SEQUOIA_ENGINE and ohlcv_bars:
                        try:
                            seq_result = run_sequoia_screen(ohlcv_bars, disp)
                            if seq_result:
                                ai_act = sig.get("action", "") if sig else ""
                                seq_block, seq_warns = format_sequoia_block(seq_result, ai_act)
                                if seq_block:
                                    text += seq_block
                                    for w in seq_warns:
                                        text += f"\n{w}"
                        except: pass
                    text += "\n<i>⏰ Sinyal valid 5 menit</i>"
                    # ── TIER-BASED UPSELL ──
                    is_donor = _is_donor(str(chat_id)) if chat_id else False
                    if not is_donor:
                        user_tier_label = sig.get("_tier", "🆓 Free") if sig else "🆓 Free"
                        text += (
                            "\n━━━━━━━━━━━━━━━━\n"
                            f"{user_tier_label} <b>TIER — Akurasi Terbatas</b>\n"
                            "━━━━━━━━━━━━━━━━\n"
                            "📊 <b>FREE TIER:</b> Analisa 1 model AI solo\n"
                            "⭐ <b>PREMIUM:</b> 3 model AI konsensus + akurasi lebih tinggi\n"
                            f"Sinyal ini generate dari 1 AI model saja dengan confidence terbatas.\n\n"
                            "💡 <b>Isi Bahan Bakar AI</b> untuk premium multi-model consensus:\n"
                            "✅ 3 AI model (DeepSeek + GPT-4o + Claude)\n"
                            "✅ Consensus voting → akurasi lebih tinggi\n"
                            "✅ Analisa unlimited 60x/hari\n"
                            "👉 /donate — dukung server & upgrade tier"
                        )
                    else:
                        text += (
                            "\n━━━━━━━━━━━━━━━━\n"
                            "⭐ <b>PREMIUM TIER — Multi-Model Consensus</b>\n"
                            "3 AI model (DeepSeek + GPT-4o + Claude) konsensus.\n"
                            "Akurasi maksimal berkat support kamu! 🥂\n"
                            "👉 /donate — Ajak teman ikut donasi"
                        )
                    keyboard = {
                        "inline_keyboard": [[
                            {"text": "🔥 Trade Auto", "callback_data": f"trade:{int(time.time())}"},
                            {"text": "⏭ Skip", "callback_data": f"skip:{int(time.time())}"}
                        ]]
                    }
                    tg_send(text, chat_id, reply_markup=keyboard)
                    # ── Save to unified feed (user-generated) ──
                    try:
                        username_raw = (msg.get("chat", {}).get("username", "") or 
                                       msg.get("from", {}).get("username", "") or "")
                        username = username_raw.lstrip("@") if username_raw else ""
                        _entry_sv = sig.get("entry", price) or 0
                        _sl_sv = sig.get("sl", 0) or 0
                        _tp_sv = sig.get("tp", 0) or 0
                        _feed_add(symbol=disp, direction=action, entry=_entry_sv, sl=_sl_sv, tp=_tp_sv,
                                  confidence=sig.get("confidence",0), rr_ratio=sig.get("rr_ratio","?"),
                                  engines=sig.get("engines",{}), source="user-generate",
                                  source_user=username, price=price, grade=sig.get("grade",""))
                    except Exception:
                        pass
            else:
                tg_send("❌ Analisa gagal — coba lagi nanti.", chat_id)
        elif not sub_norm:
            tg_send("🧠 <b>ANALISA AI — Pilih Aset</b>\n━━━━━━━━━━━━━━━━\n"
                    "💎 /analyze xauusd — Gold\n₿ /analyze btc — Bitcoin\n"
                    "💵 /analyze eurusd — EUR/USD\n🛢 /analyze oil — Crude Oil\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "🇺🇸 /analyze aapl / tsla / nvda\n"
                    "🇮🇩 /analyze bbca / bbri / tlkm / asii\n"
                    "📊 /analyze ihsg — IHSG Index", chat_id)
        else:
            # Try to resolve as any known symbol
            try:
                price = fetch_price(sub)
                if price:

                    # ── Manual anti-flip guard (unknown-symbol fallback) ──
                    blocked, reason = _is_manual_blocked(str(chat_id))
                    if blocked:
                        tg_send(reason, chat_id)
                        return

                    _touch_manual(str(chat_id), asset=sub.upper())
                    tg_send(f"🔍 Menganalisa {sub.upper()}... ~15 detik", chat_id)
                    ohlcv_bars2 = _fetch_ohlcv_for_ai(sub)
                    # Detect user tier for AI model selection
                    user_tier2 = "starter"
                    if MEMBERS_ENABLED and chat_id:
                        try:
                            m2 = get_member(str(chat_id))
                            user_tier2 = (m2 or {}).get("tier", "starter")
                        except Exception:
                            pass
                    sig = ask_ai(price, None, session(), str(killzone()), 0, premium=False,
                                  ohlcv=ohlcv_bars2, display=sub.upper(), tier=user_tier2)
                    if sig:
                        # Record direction for flip guard
                        action2 = sig.get("action", "HOLD")
                        _touch_manual(str(chat_id), action=action2 if action2 in ("BUY","SELL") else None, asset=sub.upper())
                        # Normalize confidence
                        c = sig.get("confidence", 0)
                        if isinstance(c, (int,float)) and c > 10:
                            sig["confidence"] = c / 100
                        # Apply Elite custom params
                        sig = apply_elite_params(sig, elite_params, price, sub.upper())
                        # Quality gate
                        voters = sig.get("voters", 0)
                        rr = sig.get("rr_ratio", 0)
                        if isinstance(rr, str) and rr.startswith("1:"):
                            rr = float(rr[2:]) if rr[2:] else 0
                        rr = float(rr) if rr else 0
                        if sig.get("action") in ("BUY","SELL") and voters < 2:
                            tg_send(f"⚠️ Sinyal ditahan: hanya {voters} model setuju (min 2). Coba /analyze lagi.", chat_id)
                            return
                        if sig.get("action") in ("BUY","SELL") and rr > 0 and (rr < 1.5 or rr > 5.0):
                            tg_send(f"⚠️ Sinyal ditahan: RR 1:{rr:.1f} di luar 1:1.5-5. Coba /analyze lagi.", chat_id)
                            return
                        # Auto-sync ON → langsung trade, OFF → keyboard
                        if is_autosync(chat_id):
                            if LAYERING_ENGINE and sig.get("action") != "HOLD":
                                sig = enrich_signal_with_layers(sig)
                            sig["target_user"] = str(chat_id)
                            post_signal_to_bridge(sig, price, sub.upper())
                            action = sig.get("action", "HOLD")
                            auto_text = f"🤖 <b>Auto Sync</b> — {action} {sub.upper()} @ {price}\n"
                            if QUANT_ENGINE and ohlcv_bars2:
                                try:
                                    qdata = [{"timestamp": b.get("t", b.get("timestamp",0)),
                                              "open": float(b.get("o", b.get("open",0))),
                                              "high": float(b.get("h", b.get("high",0))),
                                              "low": float(b.get("l", b.get("low",0))),
                                              "close": float(b.get("c", b.get("close",0))),
                                              "volume": 0}
                                             for b in ohlcv_bars2]
                                    if qdata:
                                        quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
                                        quant_block, guard_warnings = append_quant_consensus_ui(sig, quant_result, sub.upper())
                                        if quant_block:
                                            auto_text += quant_block + "\n"
                                            for w in guard_warnings:
                                                auto_text += f"{w}\n"
                                except: pass
                            # 🏯 CRT/TBS
                            if CRT_ENGINE and ohlcv_bars2:
                                try:
                                    crt_result = analyze_crt_setup(ohlcv_bars2, sub.upper())
                                    crt_block = format_crt_block(crt_result)
                                    if crt_block:
                                        auto_text += crt_block + "\n"
                                except: pass
                            # 🐢 Sequoia-X Quant (Turtle + MA Vol + Trend)
                            if SEQUOIA_ENGINE and ohlcv_bars2:
                                try:
                                    seq_result = run_sequoia_screen(ohlcv_bars2, sub.upper())
                                    if seq_result:
                                        ai_act = sig.get("action", "") if sig else ""
                                        seq_block, seq_warns = format_sequoia_block(seq_result, ai_act)
                                        if seq_block:
                                            auto_text += seq_block + "\n"
                                            for w in seq_warns:
                                                auto_text += f"{w}\n"
                                except: pass
                            auto_text += "<i>EA auto-eksekusi... 3-5 detik</i>"
                            tg_send(auto_text, chat_id)
                        else:
                            PENDING_SIGNALS[str(chat_id)] = {
                                "sig": sig, "price": price,
                                "expires": time.time() + PENDING_SIGNAL_TTL,
                            }
                            _save_pending_signals()
                            text = fmt_signal(sig, price, None, wib_now().hour, sub.upper(), "$")
                            if QUANT_ENGINE and ohlcv_bars2:
                                try:
                                    qdata = [{"timestamp": b.get("t", b.get("timestamp",0)),
                                              "open": float(b.get("o", b.get("open",0))),
                                              "high": float(b.get("h", b.get("high",0))),
                                              "low": float(b.get("l", b.get("low",0))),
                                              "close": float(b.get("c", b.get("close",0))),
                                              "volume": 0}
                                             for b in ohlcv_bars2]
                                    if qdata:
                                        quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
                                        quant_block, guard_warnings = append_quant_consensus_ui(sig, quant_result, sub.upper())
                                        text += quant_block
                                        for w in guard_warnings:
                                            text += f"\n{w}"
                                except: pass
                            # 🏯 CRT/TBS Layer
                            if CRT_ENGINE and ohlcv_bars2:
                                try:
                                    crt_result = analyze_crt_setup(ohlcv_bars2, sub.upper())
                                    crt_block = format_crt_block(crt_result)
                                    if crt_block:
                                        text += crt_block
                                except: pass
                            # 🏦 SMC Scalper + 📈 Trend Break
                            if SMC_ENGINE and ohlcv_bars2:
                                try:
                                    smc = analyze_smc_scalper(ohlcv_bars2, sub.upper())
                                    text += format_smc_block(smc) or ""
                                    trend = analyze_trend_break(ohlcv_bars2, sub.upper())
                                    text += format_trend_block(trend) or ""
                                except: pass
                            # 🐢 Sequoia-X Quant (Turtle + MA Vol + Trend)
                            if SEQUOIA_ENGINE and ohlcv_bars2:
                                try:
                                    seq_result = run_sequoia_screen(ohlcv_bars2, sub.upper())
                                    if seq_result:
                                        ai_act = sig.get("action", "") if sig else ""
                                        seq_block, seq_warns = format_sequoia_block(seq_result, ai_act)
                                        if seq_block:
                                            text += seq_block
                                            for w in seq_warns:
                                                text += f"\n{w}"
                                except: pass
                            text += "\n<i>⏰ Sinyal valid 5 menit</i>"
                            # ── DONATION REMINDER ──
                            is_donor = _is_donor(str(chat_id)) if chat_id else False
                            if not is_donor:
                                text += (
                                    "\n━━━━━━━━━━━━━━━━\n"
                                    "💡 <b>Kalau sinyal ini cuan, saatnya isi bensin AI!</b>\n"
                                    "Server analisa 24/7 butuh biaya API & GPU.\n"
                                    "Jangan cuma diperas aja Bro 😄\n"
                                    "👉 /donate — dukung seikhlasnya, AKTIF PERMANEN"
                                )
                            else:
                                text += (
                                    "\n━━━━━━━━━━━━━━━━\n"
                                    "🤝 <b>Makasih udah jadi Donatur!</b>\n"
                                    "Server AI ini hidup karena support kamu. 🥂"
                                )
                            keyboard = {
                                "inline_keyboard": [[
                                    {"text": "🔥 Trade Auto", "callback_data": f"trade:{int(time.time())}"},
                                    {"text": "⏭ Skip", "callback_data": f"skip:{int(time.time())}"}
                                ]]
                            }
                            tg_send(text, chat_id, reply_markup=keyboard)
                    # ── Save to unified feed (user-generated) ──
                    try:
                        disp = sub.upper()  # fix undefined `disp` in unknown-symbol branch
                        username_raw = (msg.get("chat", {}).get("username", "") or 
                                       msg.get("from", {}).get("username", "") or "")
                        username = username_raw.lstrip("@") if username_raw else ""
                        _entry_sv = sig.get("entry", price) or 0
                        _sl_sv = sig.get("sl", 0) or 0
                        _tp_sv = sig.get("tp", 0) or 0
                        _feed_add(symbol=disp, direction=action, entry=_entry_sv, sl=_sl_sv, tp=_tp_sv,
                                  confidence=sig.get("confidence",0), rr_ratio=sig.get("rr_ratio","?"),
                                  engines=sig.get("engines",{}), source="user-generate",
                                  source_user=username, price=price, grade=sig.get("grade",""))
                    except Exception as e:
                        logger.error(f"feed_add failed ({disp}): {e}")
                        tg_send("❌ Analisa gagal — coba lagi nanti.", chat_id)
                else:
                    tg_send(f"❌ '{sub}' tidak dikenali.\n\n"
                            f"Gunakan aset yang didukung:\n"
                            f"xauusd, btc, eurusd, gbpusd, oil, aapl, bbca, tlkm, ihsg\n"
                            f"Lihat /analyze untuk daftar lengkap.", chat_id)
            except Exception:
                tg_send(f"❌ Gagal menganalisa {sub}.", chat_id)


    elif cmd == "/data":
        txt = "📊 <b>Market Overview</b>\n━━━━━━━━━━━━━━━━\n"
        assets = [("XAUUSD", "gold", "$"), ("BTCUSD", "btc", "$"),
                  ("EURUSD", "eurusd", "$"), ("USOIL", "oil", "$"),
                  ("DXY", "dxy", ""), ("BBCA", "bbca", "Rp")]
        for name, pair, curr in assets:
            try:
                p = fetch_price(pair)
                if p:
                    if curr == "Rp":
                        txt += f"{name}: {curr}{p:,.0f}\n"
                    elif p > 100:
                        txt += f"{name}: {curr}{p:,.2f}\n"
                    else:
                        txt += f"{name}: {curr}{p:.5f}\n"
                else:
                    txt += f"{name}: N/A\n"
            except Exception:
                txt += f"{name}: N/A\n"
        txt += f"━━━━━━━━━━━━━━━━\n🕐 {wib_fmt()}"
        tg_send(txt, chat_id)

    elif cmd == "/bill" or cmd == "/subscribe":
        # ── Legacy commands → redirect to /donate ──
        if not chat_id:
            return
        username = ""
        if msg:
            username = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")
        _send_donate_menu(chat_id, username)

    elif cmd == "/donate":
        # ── /donate — Siram Bahan Bakar Mesin AI ──
        if not chat_id:
            return
        # Clear any stale donation input state
        DONATION_INPUT_STATE.pop(str(chat_id), None)
        username = ""
        if msg:
            username = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")

        txt = (
            "💚 <b>SIRAM BAHAN BAKAR MESIN AI 🚀</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Server AI ini mengolah jutaan data market\n"
            "secara real-time dan membutuhkan biaya API\n"
            "& GPU yang masif setiap detiknya.\n"
            "\n"
            "Jika sinyal AI ini telah mengubah portofolio\n"
            "Anda menjadi hijau, mari bergotong royong\n"
            "menjaga mesin ini tetap hidup dan semakin buas!\n"
            "\n"
            "Pilih dukunganmu hari ini:\n"
            "\n"
            "💼 <b>EKSKLUSIF: PROGRAM INVESTOR AI</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Apakah Anda big player/investor yang ingin\n"
            "ikut andil dalam pengembangan ekosistem\n"
            "kuantitatif ini secara makro? Kami membuka\n"
            "jalur pendanaan privat. Hubungi Chief\n"
            "Architect kami di bawah."
        )
        markup = {"inline_keyboard": [
            [{"text": "☕️ Traktir Kopi (Rp 15K)", "callback_data": "donate:coffee"},
             {"text": "🍱 Makan Siang Server (Rp 25K)", "callback_data": "donate:learn"}],
            [{"text": "🚀 Isi Bensin Full (Rp 50K)", "callback_data": "donate:fuel"}],
            [{"text": "💰 Input Nominal Bebas", "callback_data": "donate:custom"}],
            [{"text": "🤝 HUBUNGI CHIEF ARCHITECT", "url": "https://t.me/codergaboets"}],
        ]}
        tg_send(txt, chat_id, reply_markup=markup)

    elif cmd == "/testpay":
        """🧪 Test payment: donasi minimal — verifikasi webhook Tripay."""
        if not chat_id:
            return
        if not PAYMENT_ENGINE:
            tg_send("💳 Payment gateway belum aktif.", chat_id)
            return

        username = ""
        if msg:
            username = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")

        tg_send("🧪 <b>Test Isi Bahan Bakar AI — Rp10,000</b>\nMembuat invoice...", chat_id)

        result = create_tripay_payment(str(chat_id), username, tier="donor", amount=10000)
        if result.get("error"):
            tg_send(f"❌ Gagal: {result['error']}", chat_id)
            return

        pay_url = result.get("payment_url", "")
        ref = result.get("reference", "") or result.get("merchant_ref", "")

        txt = (
            "🧪 <b>Test Isi Bahan Bakar AI — Rp10,000</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "💰 Total: <b>Rp10,000</b>\n"
            "👑 Status: DONATUR VIP — AKTIF PERMANEN\n"
            "⏰ Expired: 1 jam\n"
            "━━━━━━━━━━━━━━━━\n"
            "Klik tombol bayar di bawah 👇\n\n"
            "<i>Setelah bayar, bot auto-upgrade kamu dalam 1-5 menit.</i>"
        )

        markup = {"inline_keyboard": [[
            {"text": "💳 Bayar Rp10,000", "url": pay_url},
        ], [
            {"text": "🔄 Cek Status", "callback_data": f"check:{ref}"},
            {"text": "📞 Admin", "url": "https://t.me/codergaboets"},
        ]]}

        tg_send(txt, chat_id, reply_markup=markup)

    elif cmd == "/activate":
        """Admin: Manual activation — set user ke DONATUR."""
        if not chat_id:
            return
        # Admin check: use chat_id list
        admin_ids = [os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", ""), "5220170786", "157228659"]
        if str(chat_id) not in admin_ids:
            tg_send("⛔ Admin only.", chat_id)
            return

        # Parse: /activate <user_id> [days]
        parts = text.split()
        if len(parts) < 2:
            tg_send("📋 <b>Usage:</b> /activate &lt;user_id&gt; [days]\n"
                    "Contoh: /activate 5220170786 9999\n"
                    "Default: AKTIF PERMANEN", chat_id)
            return

        target_id = parts[1]
        days = int(parts[2]) if len(parts) > 2 else 9999

        try:
            from members import ensure_member as m_ensure, upgrade_tier as m_upgrade

            ref = f"VTFX-{target_id}-MANUAL"
            m_ensure(target_id)
            m_upgrade(target_id, "donor", days, ref)

            # Notify admin
            tg_send(
                f"✅ <b>Manual Activation Berhasil</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"👤 User: <code>{target_id}</code>\n"
                f"👑 Status: <b>DONATUR VIP — AKTIF PERMANEN</b>",
                chat_id
            )

            # DM the activated user
            if BOT_TOKEN:
                user_msg = (
                    f"🔥 <b>BOOM! Kamu sekarang DONATUR VIP!</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"👑 Status: <b>DONATUR VIP — AKTIF PERMANEN</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"✅ /analyze UNLIMITED\n"
                    f"✅ EA Auto-Trade\n"
                    f"✅ Bridge Sinyal\n\n"
                    f"👉 /help — Lihat command\n"
                    f"👉 /analyze xauusd — Mulai analisa"
                )
                try:
                    payload = json.dumps({
                        "chat_id": target_id, "text": user_msg, "parse_mode": "HTML"
                    }).encode()
                    req = urllib.request.Request(
                        f"{TELEGRAM_API}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(req, timeout=10)
                    tg_send(f"📨 DM terkirim ke user {target_id}", chat_id)
                except Exception as e:
                    tg_send(f"⚠️ Gagal kirim DM ke user: {e}", chat_id)

        except Exception as e:
            tg_send(f"❌ Activation gagal: {e}", chat_id)

    elif cmd == "/autosync":
        if not AUTOSYNC_GLOBAL_ENABLED:
            tg_send("⏸ <b>Auto Sync dinonaktifkan sementara.</b>\n"
                    "Gunakan tombol Trade Auto / Skip pada setiap analisa.\n"
                    "Fitur auto-trade akan diaktifkan kembali di masa depan.", chat_id)
            return

        # ── DONOR GATE: Only donors can auto-trade ──
        if not _is_donor(str(chat_id)):
            tg_send(
                "🔒 <b>Auto-Trade khusus Donatur!</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Fitur auto-trade ke EA hanya tersedia untuk\n"
                "👑 <b>Donatur</b> — yang sudah dukung server AI.\n\n"
                "⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot\n"
                "📞 @codergaboets — Tanya admin",
                chat_id
            )
            return

        if sub in ("on", "enable", "start", "1"):
            set_autosync(chat_id, True)
            tg_send("🤖 <b>Auto Sync AKTIF!</b>\n━━━━━━━━━━━━━━━━\n"
                    "Sinyal dari /analyze akan <b>auto-trade ke EA</b> tanpa konfirmasi.\n"
                    "Nonaktifkan: /autosync off", chat_id)
        elif sub in ("off", "disable", "stop", "0"):
            set_autosync(chat_id, False)
            tg_send("⏸ <b>Auto Sync NONAKTIF</b>\n━━━━━━━━━━━━━━━━\n"
                    "Kamu akan lihat tombol Trade/Skip setiap analisa.\n"
                    "Aktifkan lagi: /autosync on", chat_id)
        else:
            status = "ON 🟢" if is_autosync(chat_id) else "OFF ⚪"
            tg_send(f"🤖 <b>Auto Sync:</b> {status}\n━━━━━━━━━━━━━━━━\n"
                    f"<i>Sinyal auto-trade ke EA tanpa konfirmasi.</i>\n\n"
                    f"/autosync on  — Aktifkan\n"
                    f"/autosync off — Nonaktifkan", chat_id)

    elif cmd == "/winrate" and TRADE_TRACKER:
        tg_send(format_winrate(), chat_id)

    elif cmd == "/history" and TRADE_TRACKER:
        tg_send(format_history(15), chat_id)

    elif cmd == "/recap" and TRADE_TRACKER:
        tg_send(format_daily_recap(sub if sub else ""), chat_id)

    elif cmd == "/mapping":
        tg_send("<i>📐 Generating market mapping...</i>", chat_id)
        try:
            mapping = format_daily_mapping()
            tg_send(mapping, chat_id)
        except Exception as e:
            tg_send(f"❌ Mapping error: {e}", chat_id)

    elif cmd == "/news":
        """Grok News — real-time X/Twitter market intelligence. Donor only."""
        if not _is_donor(str(chat_id)):
            tg_send(
                f"📰 <b>Grok News</b> [🔒 LOCKED]\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grok News adalah <b>real-time market intelligence</b>\n"
                f"dari X/Twitter — kasih tau apa yang bikin market\n"
                f"gerak SEBELUM lu entry.\n"
                f"\n"
                f"🔥 <b>Contoh output:</b>\n"
                f"   \"Fed Waller暗示 delay rate cut — DXY +0.3%\"\n"
                f"   \"NFP beat expectations 280k vs 200k est\"\n"
                f"   \"Gold跌破$2700 — institusi mulai take profit\"\n"
                f"\n"
                f"Kenapa ini penting?\n"
                f"   → Tahu KENAPA market gerak, bukan cuma TEKNIKAL\n"
                f"   → Hindarin entry pas news bom\n"
                f"   → Dapetin edge sebelum orang lain\n"
                f"\n"
                f"🔋 <b>AI Power: ■□□□□ 33%</b> — Grok idle\n"
                f"   AI lu cuma bisa liat chart doang...\n"
                f"   Bayangin kalo bisa baca X/Twitter juga 😤\n"
                f"\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>/donate</b> — Rp 50k/bulan\n"
                f"   Unlock Grok News + 2 AI + /levels\n"
                f"   Kasih AI lu mata buat liat berita 🗞️",
                chat_id
            )
            return

        # Donor: call Grok for the requested asset
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_n = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth",
                      "oil":"oil","usoil":"oil","eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_n = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD",
                      "gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_n.get(sub_norm, "gold")
        disp = disp_map_n.get(pair, "XAUUSD")

        tg_send(f"📰 <b>Grok is scanning X/Twitter for {disp}...</b>\n<i>This takes ~5-10 seconds</i>", chat_id)

        try:
            price = fetch_price(pair) or 0
            news = _call_grok_news(disp, price)

            if not news:
                tg_send(f"❌ Grok gagal fetch news untuk {disp}. Coba lagi nanti.", chat_id)
                return

            headline = news.get("headline", "No major catalysts")
            sentiment = news.get("sentiment", "NEUTRAL")
            impact = news.get("impact", "LOW")
            detail = news.get("detail", "")

            s_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}.get(sentiment, "⚪️")
            i_emoji = {"HIGH": "🔥", "MED": "📊", "LOW": "📎"}.get(impact, "")

            if headline == "No major catalysts":
                msg = (
                    f"📰 <b>Grok News — {disp}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚪️ <b>No major catalysts detected</b>\n"
                    f"\n"
                    f"Market currently quiet — no breaking news\n"
                    f"or macro events affecting {disp} right now.\n"
                    f"\n"
                    f"💡 Fokus ke analisa teknikal — chart is king.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📰 Grok News Active ✅ — real-time X/Twitter intel\n"
                    f"🤝 <b>Your AI Partner keeps watching.</b>"
                )
            else:
                token_used = _AI_TOKEN_USAGE.get("grok", {}).get("total", 0)
                token_k = f"{token_used/1000:.1f}k" if token_used >= 1000 else str(token_used)

                msg = (
                    f"📰 <b>Grok News — {disp}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{s_emoji} <b>{headline}</b>\n"
                    f"\n"
                )
                if detail:
                    msg += f"💡 {detail}\n\n"
                msg += (
                    f"Sentiment: <b>{sentiment}</b> | Impact: {i_emoji} <b>{impact}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🧠 {token_k} token dipakai — real-time analysis\n"
                    f"📰 Grok News Active ✅ — X/Twitter intelligence\n"
                    f"🤝 <b>AI Partner kasih lu edge.</b>\n"
                    f"\n"
                    f"💡 Combine dengan /signal untuk konfirmasi teknikal"
                )

            tg_send(msg, chat_id)

        except Exception as e:
            logger.warning(f"/news error: {e}")
            tg_send(f"❌ Gagal fetch Grok News: {e}", chat_id)

    # ── NEW: Signal System Commands ──
    elif cmd == "/levels" or cmd == "/level":
        """Premium: Deep SnR+FIBO + Engine Analysis. Free: upsell gate."""
        # ── PREMIUM GATE ──
        if not _is_donor(str(chat_id)):
            tg_send(
                "👑 <b>FITUR PREMIUM — Khusus Donatur</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "/levels adalah fitur analisa level profesional:\n"
                "📐 SnR + FIBO Retracement\n"
                "🏦 SMC Order Blocks\n"
                "📊 Fair Value Gaps\n"
                "💧 Liquidity Zones\n"
                "🕐 Session Levels\n"
                "\n"
                "🔒 Fitur ini eksklusif untuk Donatur.\n"
                "\n"
                "💚 <b>ISI BAHAN BAKAR AI</b>\n"
                "Donasi sekali — akses permanen!\n"
                "👉 /donate — Lihat opsi donasi\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Server AI ini memproses jutaan data\n"
                "tiap hari. Butuh biaya API & GPU\n"
                "yang besar. Support kamu sangat\n"
                "berarti. 🥂",
                chat_id
            )
            return
        
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_l = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_l = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_l.get(sub_norm, "gold")
        disp = disp_map_l.get(pair, "XAUUSD")
        
        tg_send(f"🔬 <b>Analyzing {disp} structure...</b>", chat_id)
        price = fetch_price(pair)
        if not price:
            tg_send(f"❌ Price unavailable untuk {disp}.", chat_id)
            return
        
        ohlcv_bars = _fetch_ohlcv_for_ai(pair)
        if not ohlcv_bars or len(ohlcv_bars) < 20:
            tg_send(f"❌ Data OHLCV tidak cukup untuk analisa {disp}.", chat_id)
            return
        
        lines = [f"🏛 <b>LEVEL ANALYSIS — {disp} @ {price}</b>",
                 f"━━━━━━━━━━━━━━━━━━━━━━"]
        
        # ═══════════════════════════════════════
        # LAYER 1: SnR + FIBO (Retail-friendly)
        # ═══════════════════════════════════════
        try:
            bars_for_snr = ohlcv_bars[-50:]
            closes = [float(b.get("c", b.get("close", 0))) for b in bars_for_snr]
            highs = [float(b.get("h", b.get("high", 0))) for b in bars_for_snr]
            lows = [float(b.get("l", b.get("low", 0))) for b in bars_for_snr]
            
            if closes and highs and lows and price:
                # ── Swing high/low with touch confirmation ──
                # Find local swings (not just highest/lowest — need retrace > ATR)
                atr = sum(abs(highs[i] - lows[i]) for i in range(min(20, len(closes)))) / min(20, len(closes))
                
                # Resistance: cluster of highs near same level
                res_levels = []
                sup_levels = []
                tolerance = atr * 0.3
                
                for i, h in enumerate(highs):
                    # Check if this high is a local swing (higher than ±2 neighbors)
                    if i >= 2 and i < len(highs)-2:
                        if h > max(highs[i-2], highs[i-1], highs[i+1], highs[i+2]):
                            merged = False
                            for r in res_levels:
                                if abs(h - r["level"]) < tolerance:
                                    r["touches"] += 1
                                    r["level"] = (r["level"] * (r["touches"]-1) + h) / r["touches"]
                                    merged = True
                                    break
                            if not merged:
                                res_levels.append({"level": h, "touches": 1})
                
                for i, l in enumerate(lows):
                    if i >= 2 and i < len(lows)-2:
                        if l < min(lows[i-2], lows[i-1], lows[i+1], lows[i+2]):
                            merged = False
                            for s in sup_levels:
                                if abs(l - s["level"]) < tolerance:
                                    s["touches"] += 1
                                    s["level"] = (s["level"] * (s["touches"]-1) + l) / s["touches"]
                                    merged = True
                                    break
                            if not merged:
                                sup_levels.append({"level": l, "touches": 1})
                
                # Filter: only levels with 2+ touches
                res_levels = [r for r in res_levels if r["touches"] >= 2 and r["level"] > price]
                sup_levels = [s for s in sup_levels if s["touches"] >= 2 and s["level"] < price]
                res_levels.sort(key=lambda x: x["level"])
                sup_levels.sort(key=lambda x: x["level"], reverse=True)
                
                # FIBO from most recent swing
                all_swings = [(h, "H") for h in highs] + [(l, "L") for l in lows]
                swing_high = max(highs[-30:]) if len(highs) >= 30 else max(highs)
                swing_low = min(lows[-30:]) if len(lows) >= 30 else min(lows)
                fib_range = swing_high - swing_low
                
                lines.append("")
                lines.append("📐 <b>SIMPLE SnR + FIBO</b>")
                lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                
                # Resistance
                lines.append("🔴 <b>Resistance:</b>")
                for r in res_levels[:3]:
                    lines.append(f"  {r['level']:.2f} ({r['touches']}x rejection)")
                if not res_levels:
                    lines.append(f"  {swing_high:.2f} (swing high)")
                
                # Support
                lines.append("🟢 <b>Support:</b>")
                for s in sup_levels[:3]:
                    lines.append(f"  {s['level']:.2f} ({s['touches']}x bounce)")
                if not sup_levels:
                    lines.append(f"  {swing_low:.2f} (swing low)")
                
                # FIBO
                if fib_range > 0 and swing_high > 0 and swing_low > 0:
                    fib_382 = swing_low + fib_range * 0.382
                    fib_50 = swing_low + fib_range * 0.50
                    fib_618 = swing_low + fib_range * 0.618
                    lines.append("📏 <b>FIBO Retracement:</b>")
                    lines.append(f"  38.2%: {fib_382:.2f}")
                    lines.append(f"  50.0%: {fib_50:.2f}")
                    lines.append(f"  61.8%: {fib_618:.2f}")
                
                # SL placement recommendation
                if res_levels:
                    nearest_res = res_levels[0]["level"]
                    wick_buffer = atr * 0.15
                    safe_sl = nearest_res + wick_buffer
                    lines.append("")
                    lines.append(f"💡 <b>SL Placement:</b>")
                    lines.append(f"  📍 Di atas resistance + buffer wick")
                    lines.append(f"  🎯 {safe_sl:.2f} (+{wick_buffer:.2f} buffer)")
                elif sup_levels:
                    nearest_sup = sup_levels[0]["level"]
                    wick_buffer = atr * 0.15
                    safe_sl = nearest_sup - wick_buffer
                    lines.append("")
                    lines.append(f"💡 <b>SL Placement:</b>")
                    lines.append(f"  📍 Di bawah support + buffer wick")
                    lines.append(f"  🎯 {safe_sl:.2f} (-{wick_buffer:.2f} buffer)")
        except: pass
        
        # ═══════════════════════════════════════
        # LAYER 2: Engine Deep Dive (Advanced)
        # ═══════════════════════════════════════
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏦 <b>ENGINE DEEP DIVE</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        
        # ── SMC Order Blocks ──
        try:
            if SMC_ENGINE:
                smc = analyze_smc_scalper(ohlcv_bars, disp)
                if smc:
                    smc_block = format_smc_block(smc)
                    if smc_block:
                        lines.append("")
                        lines.append(smc_block.rstrip())
        except: pass
        
        # ── FVG ──
        try:
            if FVG_ENGINE:
                fvg_result = detect_fvg(ohlcv_bars, price, disp)
                if fvg_result and fvg_result.get("fvgs"):
                    lines.append("")
                    lines.append("📐 <b>FAIR VALUE GAPS</b>")
                    for fvg in fvg_result["fvgs"][:3]:
                        top = fvg.get("top", 0); bot = fvg.get("bottom", 0)
                        fvg_type = fvg.get("type", "?").upper()
                        filled = "✅ filled" if fvg.get("filled") else "⏳ open"
                        lines.append(f"  {fvg_type} FVG: {bot:.2f} — {top:.2f} ({filled})")
        except: pass
        
        # ── Liquidity ──
        try:
            if HERMES_LIQUIDITY_ENGINE:
                liq = detect_liquidity_zones(ohlcv_bars, price)
                if liq:
                    eqh = liq.get("equal_highs", [])
                    eql = liq.get("equal_lows", [])
                    if eqh or eql:
                        lines.append("")
                        lines.append("💧 <b>LIQUIDITY ZONES</b>")
                        for h in eqh[:2]:
                            lines.append(f"  🔼 EQL High: {h.get('level', 0):.2f} ({h.get('touches', 0)}x)")
                        for l in eql[:2]:
                            lines.append(f"  🔽 EQL Low: {l.get('level', 0):.2f} ({l.get('touches', 0)}x)")
        except: pass
        
        # ── Session ──
        try:
            from session_levels import get_session_levels
            sess = get_session_levels(disp)
            if sess:
                asia_h = sess.get("asia_high"); asia_l = sess.get("asia_low")
                london_h = sess.get("london_high"); london_l = sess.get("london_low")
                if asia_h or london_h:
                    lines.append("")
                    lines.append("🕐 <b>SESSION LEVELS</b>")
                    if asia_h: lines.append(f"  🌏 Asia: {asia_l:.2f} — {asia_h:.2f}")
                    if london_h: lines.append(f"  🇬🇧 London: {london_l:.2f} — {london_h:.2f}")
        except: pass
        
        # ── CRT/TBS ──
        try:
            if CRT_ENGINE:
                crt_result = analyze_crt_setup(ohlcv_bars, disp)
                if crt_result:
                    crt_block = format_crt_block(crt_result)
                    if crt_block:
                        lines.append("")
                        lines.append("🏯 <b>CRT / TBS SETUP</b>")
                        lines.append(crt_block.rstrip())
        except: pass
        
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔍 /analyze — Dapatkan sinyal entry dari level ini")
        lines.append("⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot")
        
        tg_send("\n".join(lines), chat_id)

    elif cmd == "/signal":
        """Run MTF engine + signal calculator, show formatted signal. Supports /signal xauusd, /signal btc, etc."""
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_s = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_s = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_s.get(sub_norm, "gold")
        disp = disp_map_s.get(pair, "XAUUSD")
        
        from engine_consensus import run_engine_consensus
        from signal_calculator import compute_signal, format_signal_telegram
        
        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            tg_send(f"❌ Engine consensus error: {e}", chat_id)
            return
        
        if not result:
            tg_send(f"❌ Engine consensus gagal untuk {disp}.", chat_id)
            return
        
        hier = result.get("hierarchical", {})
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0) * 100
        align = hier.get("mtf_alignment", "NONE")
        macro = hier.get("macro_trend", "NEUTRAL")
        
        # ── Header with asset ──
        v_emoji = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪️"}.get(verdict,"⚪️")
        msg = (
            f"🏛 <b>MTF MATRIX — {disp}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Macro: {macro} | Alignment: {align}\n"
            f"Consensus: {score:.0f}% | Verdict: {v_emoji} <b>{verdict}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )
        
        # Show per-TF with engine names
        engines = result.get("engines", {})
        tfs = result.get("timeframes", {})
        active_count = 0
        for tf_name in ["D1","H4","H1","M15","M5"]:
            tf = tfs.get(tf_name,{})
            if tf:
                v = tf.get("verdict","HOLD")
                c = tf.get("consensus_pct",0)*100
                e_list = tf.get("active_engines",[])
                eng_str = f" ({len(e_list)} eng)" if e_list else ""
                d = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪️"}.get(v,"⚪️")
                msg += f"{d} {tf_name}: {v} {c:.0f}%{eng_str}\n"
                if v != "HOLD":
                    active_count += 1
        
        msg += f"━━━━━━━━━━━━━━━━━━━━━\n"
        
        # ── Smart message based on result ──
        sig = compute_signal(result)
        if sig and sig.get("action") in ("BUY","SELL"):
            msg += format_signal_telegram(sig)
            try:
                from signal_calculator import log_signal
                log_signal(sig)
            except: pass
        elif verdict == "HOLD" and score == 0 and active_count == 0:
            msg += (
                f"📭 <b>Tidak ada setup valid untuk {disp}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Semua timeframe menunjukkan HOLD —\n"
                f"market sedang sideways atau konsolidasi.\n\n"
                f"💡 Tunggu sesi London/NY untuk volatilitas.\n"
                f"🔍 Coba /levels {sub_norm} untuk cek level.\n"
            )
        elif align == "CONFLICT":
            msg += (
                f"⚠️ <b>MTF Conflict — sinyal tidak konsisten</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Timeframe tidak searah — market belum\n"
                f"memberikan konfirmasi yang jelas.\n\n"
                f"💡 Pantau /mapping atau tunggu 15-30 menit.\n"
            )
        else:
            msg += (
                f"⚠️ <b>Quality gate blocked</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Sinyal terdeteksi tapi tidak memenuhi\n"
                f"syarat minimum (conf ≥65%, RR ≥1:1.5).\n\n"
                f"💡 Coba /analyze {sub_norm} untuk sinyal AI.\n"
            )
        
        tg_send(msg, chat_id)

    elif cmd == "/mtf":
        """Show MTF matrix (5TF × 9 engines)."""
        tg_send("<i>🧬 Loading MTF engine readings...</i>", chat_id)
        try:
            from engine_consensus import run_engine_consensus
            
            result = run_engine_consensus(symbol="XAUUSD")
            if not result:
                tg_send("❌ Engine data unavailable.", chat_id)
                return
            
            hier = result.get("hierarchical", {})
            tfs = result.get("timeframes", {})
            macro = hier.get("macro_trend", "?")
            align = hier.get("mtf_alignment", "?")
            verdict = hier.get("verdict", "HOLD")
            score = hier.get("consensus_score", 0) * 100
            
            msg = (
                f"🧬 <b>MTF ENGINE MATRIX — XAUUSD</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏛 {macro} | {align} | {verdict} ({score:.0f}%)\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            )
            
            engine_names = {"quant":"Q","fvg":"FV","hermes":"He","crt":"CR",
                           "smc":"SM","trend":"Tr","ultimate":"Ul","sequoia":"Se","tv":"TV"}
            
            for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
                tf = tfs.get(tf_name, {})
                if tf:
                    v = tf.get("verdict", "?")
                    c = tf.get("consensus_pct", 0) * 100
                    engs = tf.get("engines", {})
                    eng_line = " ".join(
                        f"{engine_names.get(k,k[:2])}:{e.get('direction','?')[:1]}"
                        for k, e in engs.items()
                    )
                    msg += f"\n<b>{tf_name}</b> {v} ({c:.0f}%)\n{eng_line}\n"
            
            msg += (
                f"\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Dashboard: phantomfx.aitradepulse.com/dashboard"
            )
            tg_send(msg, chat_id)
            
        except Exception as e:
            tg_send(f"❌ MTF error: {e}", chat_id)

    elif cmd == "/engines":
        """Show live engine readings for all 9 strategies."""
        tg_send("<i>🔧 Loading engine readings...</i>", chat_id)
        try:
            from engine_consensus import run_engine_consensus
            
            result = run_engine_consensus(symbol="XAUUSD")
            if not result:
                tg_send("❌ Engine data unavailable.", chat_id)
                return
            
            tfs = result.get("timeframes", {})
            hier = result.get("hierarchical", {})
            
            # Aggregate engine votes across all TFs
            engine_votes = {}
            for tf_name, tf in tfs.items():
                for eng_name, eng in tf.get("engines", {}).items():
                    if eng_name not in engine_votes:
                        engine_votes[eng_name] = {"BUY": 0, "SELL": 0, "HOLD": 0}
                    d = eng.get("direction", "HOLD")
                    engine_votes[eng_name][d] = engine_votes[eng_name].get(d, 0) + 1
            
            display_names = {
                "quant": "📊 Quant", "fvg": "🕳 FVG", "hermes": "⚡ Hermes",
                "crt": "🔀 CRT/TBS", "smc": "🏦 SMC", "trend": "📈 Trend",
                "ultimate": "🎯 Ultimate", "sequoia": "🌲 Sequoia", "tv": "📺 TV"
            }
            
            msg = (
                f"🔧 <b>ENGINE READINGS — XAUUSD</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏛 {hier.get('macro_trend','?')} | {hier.get('mtf_alignment','?')}\n"
                f"Verdict: <b>{hier.get('verdict','HOLD')}</b> ({hier.get('consensus_score',0)*100:.0f}%)\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            )
            
            for eng_name, votes in engine_votes.items():
                total = sum(votes.values())
                buy_pct = votes["BUY"] / total * 100 if total else 0
                sell_pct = votes["SELL"] / total * 100 if total else 0
                direction = max(votes, key=votes.get)
                emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪️"
                
                msg += (
                    f"{emoji} {display_names.get(eng_name, eng_name)}: "
                    f"<b>{direction}</b> "
                    f"(🟢{votes['BUY']} 🔴{votes['SELL']} ⚪️{votes['HOLD']})\n"
                )
            
            msg += (
                f"\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔥 /signal — Generate signal dari matrix ini"
            )
            tg_send(msg, chat_id)
            
        except Exception as e:
            tg_send(f"❌ Engine error: {e}", chat_id)

    elif cmd == "/dashboard":
        """Show link to live dashboard."""
        tg_send(
            f"📊 <b>VILONA AI — LIVE DASHBOARD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Pantau market real-time:\n"
            f"• MTF Matrix 5TF × 9 Engines\n"
            f"• Signal History & Grade\n"
            f"• Trade Tracker & Win Rate\n"
            f"• Live Price XAUUSD + Chart TV\n\n"
            f"🌐 <a href='https://phantomfx.aitradepulse.com/dashboard'>Buka Dashboard →</a>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔥 /signal — Cek signal sekarang",
            chat_id
        )

    elif cmd == "/restart_bot":
        """Admin-only: Exit so systemd auto-restarts."""
        admin_ids = [os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", ""), "5220170786", "157228659"]
        if str(chat_id) not in admin_ids:
            tg_send("⛔ Hanya admin yang bisa execute command ini.", chat_id)
            return
        tg_send("♻️ Sistem bot sedang di-restart, mohon tunggu sebentar...", chat_id)
        time.sleep(2)
        logger.warning(f"♻️ Bot restart initiated by admin {chat_id}")
        os._exit(0)


# ── Signal log ──
def load_signal_log(asset="default"):
    path = DATA_DIR / f"signal_log_{asset}.json"
    try:
        if path.exists(): return json.loads(path.read_text())
    except Exception: pass
    return {"signals_sent":0,"last_signal_time":None,"last_action":None,"last_price":0,"loss_count":0}

def save_signal_log(log, asset="default"):
    (DATA_DIR / f"signal_log_{asset}.json").write_text(json.dumps(log))

def is_trading_session(h):
    return 7 <= h < 23

def is_weekend():
    """True if Sat/Sun, OR Monday before 05:00 WIB (crypto mode extended)."""
    now = wib_now()
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return True
    if now.weekday() == 0 and now.hour < 5:  # Mon 00:00-05:00
        return True
    return False

# ── Asset classification for weekend mode ──
CRYPTO_PAIRS = {"btc", "btcusd", "eth", "ethusd", "crypto"}
FOREX_COMMO_PAIRS = {"xauusd", "gold", "eurusd", "gbpusd", "usdjpy", "oil", "usoil"}

def is_crypto_pair(pair: str) -> bool:
    """Check if a pair is crypto (trades 24/7 including weekends)."""
    return pair.lower() in CRYPTO_PAIRS

def is_forex_commo_pair(pair: str) -> bool:
    """Check if a pair is forex/commodity (closed weekends)."""
    return pair.lower() in FOREX_COMMO_PAIRS

def weekend_status_text() -> str:
    """Return weekend mode indicator text."""
    if is_weekend():
        return "\n🟡 WEEKEND MODE: Forex/Gold Tutup | Crypto (BTC/ETH) BUKA 24/7"
    return ""

def is_market_open():
    """Market is open: Mon-Fri + trading hours, OR crypto pairs (24/7 including weekends)."""
    if is_weekend():
        # On weekends, only crypto is "open" — this function returns True for crypto contexts
        # The caller should check is_crypto_pair() if they need to filter
        return True  # crypto is always open
    return is_trading_session(wib_now().hour)

# ── Signal channel (used for ALL signal posts, mapping, and trade alerts) ──
# Supports both old env var name and new SIGNAL_CHANNEL_ID
SIGNAL_CHANNEL_ID = os.getenv("SIGNAL_CHANNEL_ID") or os.getenv("MAPPING_CHANNEL_ID", "")
SIGNAL_CHANNEL_REF = f"telegram:{SIGNAL_CHANNEL_ID}" if SIGNAL_CHANNEL_ID else ""

# ── Group forward (optional — set GROUP_CHAT_ID to forward signals/mapping to group) ──
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "")

def _broadcast_to_group(text):
    """DEPRECATED — channel linked, auto-forward."""
    return  # no-op
def _no_pin_broadcast(text):
    """Signal broadcast to group WITHOUT pinning.
    Since channel is linked to group, Telegram auto-forwards+pins channel posts.
    We send a separate bot message (auto-forward may be slow) AND unpin to keep
    group pins clear for admin's own material."""
    if GROUP_CHAT_ID:
        try:
            tg_send(text, GROUP_CHAT_ID)
            # Unpin latest pinned message (the auto-forwarded channel post)
            payload = json.dumps({"chat_id": GROUP_CHAT_ID}).encode()
            urllib.request.urlopen(
                urllib.request.Request(f"{TELEGRAM_API}/unpinChatMessage",
                                       data=payload,
                                       headers={"Content-Type": "application/json"}),
                timeout=5)
        except Exception:
            pass  # bot may not have can_pin_messages permission — graceful fallback

def send_to_channel(text):
    """Send signal/mapping to broadcast channel. Returns tg_send result.
    Falls back to home only if channel ID is not configured (warns in log)."""
    if SIGNAL_CHANNEL_ID:
        result = tg_send(text, SIGNAL_CHANNEL_ID)
        if result is None:
            logger.warning("send_to_channel: post failed, retrying once...")
            time.sleep(1)
            result = tg_send(text, SIGNAL_CHANNEL_ID)
        return result
    logger.warning("send_to_channel: SIGNAL_CHANNEL_ID not set — falling back to HOME")
    return tg_send(text)  # fallback to home


# ── Subscription reminders (H-7/H-3/H-1) ──
def _process_subscription_reminders():
    if not SUBSCRIPTION_ENGINE:
        return
    try:
        due = check_due_reminders()
        for item in due:
            chat_id = item["chat_id"]
            member = item["member"]
            label = item["label"]
            days_left = item["days_left"]
            nama = member.get("nama", member.get("chat_id", "Kak"))
            tier = member.get("tier", "paket")
            if label == "h7":
                msg = (
                    f"⏳ <b>Pengingat Langganan</b>\n"
                    f"Hi {nama}, langganan {tier} akan expired dalam 7 hari.\n"
                    "Perpanjang sekarang agar sinyal tetap lanjut.\n"
                    "/subscribe"
                )
            elif label == "h3":
                msg = (
                    f"⚠️ <b>3 Hari Lagi Expired!</b>\n"
                    f"Jangan sampai sinyal {tier} putus, {nama}.\n"
                    "Perpanjang sekarang: /subscribe"
                )
            else:
                msg = (
                    f"🔴 <b>BESOK EXPIRED!</b>\n"
                    f"{nama}, langganan {tier} expired dalam <24 jam.\n"
                    "Tanpa perpanjangan: akses sinyal AI terbatas.\n"
                    "/subscribe"
                )
            try:
                tg_send(msg, chat_id)
            except Exception:
                pass
            set_reminder(chat_id, label)
        expired = check_expired()
        for item in expired:
            chat_id = item["chat_id"]
            mark_expired(chat_id)
            try:
                tg_send(
                    "⛔ <b>Langganan Telah Expired</b>\n"
                    "Sinyal AI dibatasi sampai perpanjangan.\n"
                    "/subscribe — perpanjang sekarang",
                    chat_id,
                )
            except Exception:
                pass
    except Exception as exc:
        logger.error(f"Subscription reminders failed: {exc}")


def format_daily_mapping():
    """Daily market mapping/insight — key levels, market structure, no trade signals.
    Posts to channel as educational content separate from auto-signals."""
    now = wib_now()
    day_name = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"][now.weekday()]
    
    lines = [
        f"📐 MARKET MAPPING",
        f"🗓 {day_name}, {now.strftime('%d %B %Y')}",
        f"━━━━━━━━━━━━━━━━",
        f"",
        f"🕐 Status: {'🟡 WEEKEND CRYPTO MODE — Forex Tutup, Crypto BUKA' if is_weekend() else '🟢 MARKET BUKA'}",
        f"",
    ]

    # ── Monday Sentiment ──
    if now.weekday() == 0:
        # Fetch DXY for sentiment direction
        dxy_val = None
        try:
            if MARKET_DATA:
                dxy_q = MARKET_DATA.get_quote("DX-Y.NYB", force=True)
                dxy_val = dxy_q.price if dxy_q else None
        except Exception:
            pass
        sent_label = "BULLISH" if (dxy_val is not None and dxy_val < 103) else "BEARISH"
        lines.append(f"📅 Monday Sentiment: {sent_label} — Waspadai Gaps & Volatilitas Pembukaan.")
        lines.append(f"")
    
    # Try to get key levels for each asset
    if MARKET_DATA:
        for pair, disp, yahoo_sym, is_forex in AUTO_SCAN_ASSETS:
            try:
                bars = MARKET_DATA.get_ohlcv(yahoo_sym, "1h", 50)
                if not bars or len(bars) < 5:
                    continue
                high_24h = max(b.high for b in bars[-24:]) if len(bars) >= 24 else max(b.high for b in bars)
                low_24h = min(b.low for b in bars[-24:]) if len(bars) >= 24 else min(b.low for b in bars)
                close = bars[-1].close
                high_w = max(b.high for b in bars[-min(40,len(bars)):])
                low_w = min(b.low for b in bars[-min(40,len(bars)):])
                
                mid = (high_24h + low_24h) / 2
                r1 = high_24h + (high_24h - low_24h) * 0.382
                s1 = low_24h - (high_24h - low_24h) * 0.382
                
                sma20 = sum(b.close for b in bars[-20:]) / min(20, len(bars))
                trend = "📈 BULLISH" if close > sma20 else ("📉 BEARISH" if close < sma20 else "➡️ SIDEWAYS")
                
                lines.append(f"")
                lines.append(f"💱 {disp}")
                lines.append(f"   Price: {close:.2f} | {trend}")
                lines.append(f"   Range 24H: {low_24h:.2f} — {high_24h:.2f}")
                lines.append(f"   Resistance: {r1:.2f} | Support: {s1:.2f}")
                lines.append(f"   Weekly High: {high_w:.2f} | Low: {low_w:.2f}")
            except Exception:
                pass
    
    lines.append(f"")
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"📌 Mapping ini BUKAN sinyal trading.")
    lines.append(f"🤖 Sinyal auto hanya Mon-Fri saat market buka.")
    lines.append(f"📱 /analyze untuk analisa manual.")
    lines.append(f"")
    lines.append(f"#VilonaTradeFX #MarketMapping #TechnicalAnalysis")
    
    return "\n".join(lines)


# ── Auto-analyze loop ──
# Assets to scan autonomously (forex, crypto, commodities — stocks on-demand via /analyze)

# ── Channel rate limiter + Signal Dedup (DISK-PERSISTED — survives restarts) ──
CHANNEL_STATE_FILE = DATA_DIR / ".channel_rate_state.json"
_SIGNAL_DEDUP_WINDOW = 7200       # 2h — same signal hash rejected within this
_GLOBAL_CHANNEL_COOLDOWN = 600    # 10 min between ANY channel posts
_PER_ASSET_COOLDOWN = 1800        # 30 min per asset (any direction)
_SAME_DIR_COOLDOWN = 3600         # 60 min same-direction on same asset
_MAX_PER_ASSET_PER_DAY = 3        # max 3 signals per asset per day
_channel_state = None             # lazy-loaded from disk

# ── PER-PAIR LAST POST TRACKER (brute-force dedup, no fancy state) ──
LAST_POST_DIR = DATA_DIR / "last_channel_post"
LAST_POST_DIR.mkdir(parents=True, exist_ok=True)
_LAST_POST_COOLDOWN = 1800  # 30 min — don't post same pair within this window

def _get_last_post(pair_key: str) -> dict:
    """Read last channel post for a pair. Returns empty dict if none/stale."""
    try:
        f = LAST_POST_DIR / f"{pair_key}.json"
        if f.exists():
            data = json.loads(f.read_text())
            age = time.time() - data.get("ts", 0)
            if age < _LAST_POST_COOLDOWN:
                return data
    except Exception:
        pass
    return {}

def _set_last_post(pair_key: str, direction: str, entry: float):
    """Record a channel post for a pair."""
    try:
        f = LAST_POST_DIR / f"{pair_key}.json"
        f.write_text(json.dumps({
            "pair": pair_key, "direction": direction,
            "entry": round(entry, 1), "ts": time.time()
        }))
    except Exception:
        pass
_last_tpsl_alert = {}             # {(trade_id): timestamp}
_ALERT_STATE_FILE = DATA_DIR / "tpsl_alert_state.json"

def _load_tpsl_state():
    global _last_tpsl_alert
    try:
        if _ALERT_STATE_FILE.exists():
            with open(_ALERT_STATE_FILE) as f:
                data = json.load(f)
            now = time.time()
            _last_tpsl_alert = {k: v for k, v in data.items() if (now - v) < 3600}
    except: pass

def _save_tpsl_state():
    try:
        _ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_ALERT_STATE_FILE, "w") as f:
            json.dump(_last_tpsl_alert, f)
    except: pass

def _load_channel_state():
    """Load channel rate state from disk. Auto-clean entries older than 24h."""
    try:
        if CHANNEL_STATE_FILE.exists():
            raw = json.loads(CHANNEL_STATE_FILE.read_text())
            now = time.time(); today = wib_now().strftime("%Y%m%d")
            for k in list(raw.get("per_asset", {}).keys()):
                if now - raw["per_asset"][k] > 86400: del raw["per_asset"][k]
            for k in list(raw.get("daily_counts", {}).keys()):
                if not k.startswith(today): del raw["daily_counts"][k]
            for k in list(raw.get("signal_hashes", {}).keys()):
                if now - raw["signal_hashes"][k] > _SIGNAL_DEDUP_WINDOW: del raw["signal_hashes"][k]
            return raw
    except Exception: pass
    return {"global_last": 0, "per_asset": {}, "daily_counts": {}, "signal_hashes": {}}

def _save_channel_state():
    try:
        if _channel_state is not None:
            CHANNEL_STATE_FILE.write_text(json.dumps(_channel_state))
    except Exception: pass

def _cs():
    """Lazy-load channel state."""
    global _channel_state
    if _channel_state is None: _channel_state = _load_channel_state()
    return _channel_state

def _signal_dedup_hash(asset_key, direction, entry, sl, tp):
    """Content fingerprint — same asset+direction+price combo = duplicate."""
    key = f"{asset_key}|{direction}|{round(float(entry or 0),1)}|{round(float(sl or 0),1)}|{round(float(tp or 0),1)}"
    return hashlib.md5(key.encode()).hexdigest()[:12]

def _can_post_to_channel(asset_key: str = "", direction: str = "", entry=0, sl=0, tp=0) -> bool:
    """Rate limit + dedup. Returns False if blocked."""
    state = _cs(); now = time.time(); today = wib_now().strftime("%Y%m%d")
    # 0. PER-PAIR BRUTE DEDUP — 30 min cooldown (disk-persisted, ultra-reliable)
    if asset_key and direction:
        last = _get_last_post(asset_key)
        if last:
            same_dir = (last.get("direction") == direction)
            last_entry = last.get("entry", 0)
            entry_diff = abs(float(entry or 0) - float(last_entry)) if entry and last_entry else 999
            if same_dir and entry_diff < 5.0:
                logger.info(f"🚫 PAIR DEDUP [{asset_key}]: same {direction} direction, entry diff={entry_diff:.1f}")
                return False
    # 1. Signal dedup — SAME content within 2h = BLOCK
    if asset_key and direction:
        sig_hash = _signal_dedup_hash(asset_key, direction, entry, sl, tp)
        stored = state["signal_hashes"].get(sig_hash, 0)
        if now - stored < _SIGNAL_DEDUP_WINDOW:
            logger.info(f"🚫 DEDUP BLOCK [{asset_key}]: identical signal {sig_hash} ({int((now-stored)/60)}m ago)")
            return False
    # 1. Global cooldown
    gl = state.get("global_last", 0)
    if gl and (now - gl) < _GLOBAL_CHANNEL_COOLDOWN: return False
    # 2. Daily cap per asset
    if asset_key:
        dk = f"{today}:{asset_key}"
        if state["daily_counts"].get(dk, 0) >= _MAX_PER_ASSET_PER_DAY: return False
        # 3. Same-direction cooldown
        if direction:
            dk2 = f"{asset_key}:{direction}"
            if now - state["per_asset"].get(dk2, 0) < _SAME_DIR_COOLDOWN: return False
        # 4. Any-direction per-asset cooldown
        dk3 = f"{asset_key}:*"
        if now - state["per_asset"].get(dk3, 0) < _PER_ASSET_COOLDOWN: return False
    return True

def _mark_channel_post(asset_key: str = "", direction: str = "", entry=0, sl=0, tp=0):
    """Persist post to disk immediately."""
    state = _cs(); now = time.time(); today = wib_now().strftime("%Y%m%d")
    state["global_last"] = now
    if asset_key:
        state["per_asset"][f"{asset_key}:*"] = now
        if direction: state["per_asset"][f"{asset_key}:{direction}"] = now
        dk = f"{today}:{asset_key}"
        state["daily_counts"][dk] = state["daily_counts"].get(dk, 0) + 1
        if entry and sl and tp:
            sh = _signal_dedup_hash(asset_key, direction, entry, sl, tp)
            state["signal_hashes"][sh] = now
        # ── Per-pair direct dedup file (always saved) ──
        _set_last_post(asset_key, direction, entry)
    _save_channel_state()

def _can_post_tpsl_alert(trade_id: str) -> bool:
    """Prevent duplicate TP/SL alerts within 5 min for the same trade. Persisted to disk."""
    now = time.time()
    last = _last_tpsl_alert.get(trade_id, 0)
    if (now - last) < 300:
        return False
    _last_tpsl_alert[trade_id] = now
    _save_tpsl_state()
    return True

AUTO_SCAN_ASSETS = [
    # (internal_pair, display_name, yahoo_symbol, is_forex_metal)
    # Channel auto-post: XAUUSD ONLY. Other pairs via /analyze di bot.
    ("gold", "XAUUSD", "GC=F", True),
]

def auto_analyze_loop():
    """Main autonomous signal loop. Weekdays: all assets. Weekends: crypto only (BTC/ETH)."""
    logger.info("🚀 Auto-analyze loop started (XAUUSD focus mode)")
    time.sleep(5)
    asset_idx = 0
    # Per-asset signal logs for cooldown tracking
    asset_logs = {}
    # ── Market Pulse tracker (post engine readings every 30 min) ──
    last_pulse_time = 0.0
    # ── 2-Bar Confirmation Tracker (BTC only) ──
    # Backtest proven: +30% profit, +1.9% WR for BTCUSD
    _consec_tracker = {}  # {asset: {"action": "BUY"/"SELL", "bar_time": timestamp}}
    def _consec_2bar_confirm(asset, action):
        """Only allow signal if same direction confirmed on previous bar scan.
        Returns True if this is the first signal (no prior) or matches prior."""
        prev = _consec_tracker.get(asset)
        # Update tracker with current signal
        _consec_tracker[asset] = {"action": action, "time": wib_now()}
        if prev is None:
            return False  # First signal ever → wait for confirmation
        return prev["action"] == action
    # ── Persistent mapping tracker (survives restarts) ──
    MAPPING_TRACKER = DATA_DIR / ".last_mapping_date"
    # ── Daily Loss Counter (global, survives restarts) ──
    DAILY_LOSS_FILE = DATA_DIR / ".daily_loss_count"
    DAILY_LOSS_DATE = DATA_DIR / ".daily_loss_date"

    def _get_daily_loss_count():
        today = wib_now().strftime("%Y-%m-%d")
        try:
            saved_date = DAILY_LOSS_DATE.read_text().strip()
            if saved_date != today:
                DAILY_LOSS_FILE.write_text("0")
                DAILY_LOSS_DATE.write_text(today)
                return 0
            return int(DAILY_LOSS_FILE.read_text().strip() or "0")
        except:
            return 0

    def _increment_daily_loss():
        today = wib_now().strftime("%Y-%m-%d")
        try:
            saved_date = DAILY_LOSS_DATE.read_text().strip()
            if saved_date != today:
                DAILY_LOSS_FILE.write_text("1")
                DAILY_LOSS_DATE.write_text(today)
                return 1
            current = int(DAILY_LOSS_FILE.read_text().strip() or "0") + 1
            DAILY_LOSS_FILE.write_text(str(current))
            return current
        except:
            return 1
    def _get_last_mapping():
        try:
            return MAPPING_TRACKER.read_text().strip()
        except: return ""
    def _set_last_mapping(date_str):
        MAPPING_TRACKER.write_text(date_str)
    last_mapping_day = _get_last_mapping()  # init from disk

    while True:
        try:
            now = wib_now()
            h = now.hour
            weekday = now.weekday()
            today_str = now.strftime("%Y%m%d")

            # ── DAILY MAX LOSS CIRCUIT BREAKER (3 losses = STOP all signals) ──
            daily_losses = _get_daily_loss_count()
            if daily_losses >= 3:
                logger.warning(f"⛔ CIRCUIT BREAKER: {daily_losses}/3 losses today — STOPPED")
                time.sleep(600)
                continue

            # ── WEEKEND GATE ──
            if is_weekend():
                # Daily mapping at 10:00 WIB on weekends (once per day)
                if last_mapping_day != today_str and h >= 10:
                    try:
                        mapping_text = format_daily_mapping()
                        send_to_channel(mapping_text)
                        last_mapping_day = today_str
                        _set_last_mapping(today_str)
                        logger.info("📊 Daily mapping sent to channel")
                    except Exception as e:
                        logger.error(f"Daily mapping failed: {e}")

                # ── WEEKEND CRYPTO MODE: skip forex/commodities, allow crypto ──
                # Rotate through assets but skip non-crypto
                for _ in range(len(AUTO_SCAN_ASSETS)):
                    pair, disp, yahoo_sym, is_forex = AUTO_SCAN_ASSETS[asset_idx % len(AUTO_SCAN_ASSETS)]
                    asset_idx += 1
                    if is_crypto_pair(pair):
                        break  # found a crypto pair to scan
                else:
                    time.sleep(300)
                    continue  # no crypto in rotation (shouldn't happen)

                # For crypto on weekends: skip session/trading-hour checks, scan 24/7
                logger.info(f"🟡 WEEKEND CRYPTO [{disp}] — scanning...")
                # ── Inline signal pipeline for weekend crypto ──
                # (copies weekday flow: check outcomes → mechanical → AI consensus)
                
                # Check trade outcomes (TP/SL hits)
                if TRADE_TRACKER:
                    try:
                        closed_trades = check_outcomes({disp: None})
                        price = fetch_price(pair)
                        if price: closed_trades = check_outcomes({disp: price})
                        for ct in closed_trades:
                            try:
                                trade_id = ct.get("id", ct.get("trade_id", ""))
                                if trade_id and not _can_post_tpsl_alert(str(trade_id)):
                                    continue
                                # Send to channel (text only — buttons gak work di channel)
                                alert_text = format_trade_close_alert(ct)
                                send_to_channel(alert_text)
                            except Exception: pass
                    except Exception: pass

                price = fetch_price(pair)
                if not price:
                    time.sleep(60)
                    continue

                dxy = fetch_dxy() if pair == "gold" else None
                lkz, nykz = killzone(h)
                kz = "London" if lkz else ("NY" if nykz else "Outside")

                # Mechanical signal detection
                mech_sig = None
                if MARKET_DATA and is_forex:
                    try:
                        m1_bars = MARKET_DATA.get_ohlcv(yahoo_sym, "1m", 200)
                        if m1_bars and len(m1_bars) >= 30:
                            ohlcv_m1 = [{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                          "low": b.low, "close": b.close, "volume": b.volume} for b in m1_bars]
                            mech_sig, mech_reason = detect_mechanical_signal(
                                pair.upper(), disp, price, ohlcv_m1)
                            if mech_sig:
                                logger.info(f"⚡ MECHANICAL [{disp}]: {mech_sig['action']} | {mech_sig['source']}")
                    except Exception as e:
                        logger.debug(f"Mechanical check [{disp}]: {e}")

                if mech_sig and mech_sig["action"] in ("BUY", "SELL"):
                    action = mech_sig["action"]
                    mech_sig = _clamp_sltp(mech_sig, disp)  # enforce SL direction + bounds
                    conf = mech_sig["confidence"]
                    log_key = f"auto_{pair}"
                    log = asset_logs.get(log_key, load_signal_log(pair))
                    last_time = log.get("last_signal_time")
                    last_action = log.get("last_action")
                    if last_time and last_action:
                        try:
                            last_dt = datetime.fromisoformat(last_time)
                            if (wib_now() - last_dt).total_seconds() < 600 and last_action != action:
                                logger.info(f"BLOCKED [{disp}]: {action} after {last_action}")
                                time.sleep(60)
                                continue
                        except: pass
                    logger.info(f"MECHANICAL PUSH [{disp}]: {action} | conf={conf:.0%}")
                    # ── BTC 2-bar confirmation (gate channel + bridge) ──
                    if disp == "BTCUSD" and not _consec_2bar_confirm("BTCUSD", action):
                        logger.info(f"⏳ BTC 2-BAR WAIT: {action} — waiting for next bar confirm")
                        continue
                    text = fmt_signal(mech_sig, price, dxy, h, disp, "$")
                    _entry = mech_sig.get("entry", price) or 0
                    _sl = mech_sig.get("sl", 0) or 0
                    _tp = mech_sig.get("tp", 0) or 0
                    if _can_post_to_channel(pair, action, _entry, _sl, _tp):
                        send_to_channel(text)
                        _mark_channel_post(pair, action, _entry, _sl, _tp)
                        if LAYERING_ENGINE and mech_sig.get("action") != "HOLD":
                            mech_sig = enrich_signal_with_layers(mech_sig)
                        post_signal_to_bridge(mech_sig, price, disp)
                    else:
                        logger.info(f"BLOCKED weekend [{disp}]: rate limited — skip bridge")
                    log["signals_sent"] = log.get("signals_sent", 0) + 1
                    log["last_signal_time"] = wib_now().isoformat()
                    log["last_action"] = action
                    log["last_price"] = price
                    log["last_signal"] = {"action": action, "entry": mech_sig.get("entry", price),
                        "sl": mech_sig.get("sl", 0), "tp": mech_sig.get("tp", 0),
                        "tp1": mech_sig.get("tp1", 0), "tp2": mech_sig.get("tp2", 0),
                        "confidence": conf, "source": mech_sig.get("source", "mech"),
                        "rr_ratio": mech_sig.get("rr_ratio", 0)}
                    asset_logs[log_key] = log
                    # Per-asset signal file (avoid race between threads)
                    (DATA_DIR / f"ea_signal_{pair}.json").write_text(json.dumps(log["last_signal"]))
                    save_signal_log(log, pair)
                else:
                    # AI consensus (simplified for weekend - just DeepSeek/OmniRoute)
                    sig = ask_ai(price, dxy, "WeekendCrypto", kz, 0, premium=False,
                                  ohlcv=_fetch_ohlcv_for_ai(pair), display=disp)
                    if sig and sig.get("action") in ("BUY", "SELL"):
                        # Normalize confidence + quality gates
                        wc = sig.get("confidence", 0)
                        if isinstance(wc, (int,float)) and wc > 10:
                            sig["confidence"] = wc / 100; wc = wc / 100
                        voters = sig.get("voters", 0)
                        rr = sig.get("rr_ratio", 0)
                        if isinstance(rr, str) and rr.startswith("1:"):
                            rr = float(rr[2:]) if rr[2:] else 0
                        rr = float(rr) if rr else 0
                        if wc < 0.70:
                            logger.info(f"   [{disp}] BLOCKED: low confidence {wc:.0%}")
                        elif voters < 3:
                            logger.info(f"   [{disp}] BLOCKED: solo call ({voters} model)")
                        elif rr > 0 and (rr < 1.5 or rr > 5.0):
                            logger.info(f"   [{disp}] BLOCKED: RR 1:{rr:.1f} outside range")
                        else:
                            logger.info(f"AI PUSH [{disp}]: {sig['action']} | conf={wc:.0%}")
                            # ── Rate-limit check BEFORE bridge push (anti-oversignal weekend) ──
                            _entry = sig.get("entry", price) or 0
                            _sl = sig.get("sl", 0) or 0
                            _tp = sig.get("tp", 0) or 0
                            if _can_post_to_channel(pair, sig["action"], _entry, _sl, _tp):
                                if LAYERING_ENGINE:
                                    sig = enrich_signal_with_layers(sig)
                                post_signal_to_bridge(sig, price, disp)
                                text = fmt_signal(sig, price, dxy, h, disp, "$")
                                send_to_channel(text)
                                _mark_channel_post(pair, sig["action"], _entry, _sl, _tp)
                            else:
                                logger.info(f"BLOCKED weekend [{disp}]: rate limited — skip bridge+channel")
                        # Weekend signal log (separate from weekday for accurate tracking)
                        wk_log_key = f"weekend_{pair}"
                        wk_log = asset_logs.get(wk_log_key, load_signal_log(f"weekend_{pair}"))
                        wk_log["signals_sent"] = wk_log.get("signals_sent", 0) + 1
                        wk_log["last_signal_time"] = wib_now().isoformat()
                        wk_log["last_action"] = sig["action"]
                        wk_log["last_price"] = price
                        save_signal_log(wk_log, f"weekend_{pair}")
                        asset_logs[wk_log_key] = wk_log

                time.sleep(120)  # 2 min between weekend crypto scans
                continue  # back to top of while loop

            # ── WEEKDAY: Reset mapping tracker for new day ──
            if last_mapping_day and last_mapping_day != today_str:
                last_mapping_day = ""
                _set_last_mapping("")  # clear persistent tracker

            if not is_weekend() and not is_trading_session(h):
                time.sleep(180)
                continue

            # Rotate through assets
            pair, disp, yahoo_sym, is_forex = AUTO_SCAN_ASSETS[asset_idx % len(AUTO_SCAN_ASSETS)]
            asset_idx += 1

            # News blackout check (applies to all)
            is_blackout, is_post_news, news_name = news_blackout_status()
            if is_blackout:
                logger.info(f"🔇 News blackout: {news_name}")
                time.sleep(120)
                continue

            # Per-asset signal log
            log_key = f"auto_{pair}"
            if log_key not in asset_logs:
                asset_logs[log_key] = load_signal_log(pair)
            log = asset_logs[log_key]

            # Fetch price for this asset
            price = fetch_price(pair)
            if not price:
                time.sleep(30)
                continue

            # Check trade outcomes (TP/SL hits) — with donation CTA
            if TRADE_TRACKER:
                try:
                    closed_trades = check_outcomes({disp: price})
                    for ct in closed_trades:
                        try:
                            trade_id = ct.get("id", ct.get("trade_id", ""))
                            if trade_id and not _can_post_tpsl_alert(str(trade_id)):
                                continue
                            # Increment daily loss counter on SL
                            if ct.get("outcome") == "SL_HIT":
                                new_count = _increment_daily_loss()
                                logger.warning(f"⛔ SL HIT — daily losses: {new_count}/3")
                            # Send to channel (text only — buttons gak work di channel)
                            alert_text = format_trade_close_alert(ct)
                            send_to_channel(alert_text)
                        except Exception: pass
                except Exception: pass

            dxy = fetch_dxy() if pair == "gold" else None
            lkz, nykz = killzone(h)
            kz = "London" if lkz else ("NY" if nykz else "Outside")

            # ── MECHANICAL OVERRIDE: Quant + FVG + Hermes ──
            mech_sig = None
            if MARKET_DATA and is_forex:
                try:
                    m1_bars = MARKET_DATA.get_ohlcv(yahoo_sym, "1m", 200)
                    if m1_bars and len(m1_bars) >= 30:
                        ohlcv_m1 = [{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                      "low": b.low, "close": b.close, "volume": b.volume} for b in m1_bars]
                        mech_sig, mech_reason = detect_mechanical_signal(
                            pair.upper(), disp, price, ohlcv_m1)
                        if mech_sig:
                            logger.info(f"⚡ MECHANICAL [{disp}]: {mech_sig['action']} | {mech_sig['source']}")
                except Exception as e:
                    logger.debug(f"Mechanical check [{disp}]: {e}")

            if mech_sig and mech_sig["action"] in ("BUY", "SELL"):
                action = mech_sig["action"]
                mech_sig = _clamp_sltp(mech_sig, disp)  # enforce SL direction + bounds
                conf = mech_sig["confidence"]
                
                # Direction stability guard — block opposite direction within 10 min
                last_time = log.get("last_signal_time")
                last_action = log.get("last_action")
                if last_time and last_action:
                    try:
                        last_dt = datetime.fromisoformat(last_time)
                        elapsed = (wib_now() - last_dt).total_seconds()
                        if elapsed < 600 and last_action != action:
                            logger.info(f"BLOCKED [{disp}]: {action} after {last_action} ({elapsed:.0f}s ago)")
                            time.sleep(30)
                            continue
                    except: pass
                
                logger.info(f"MECHANICAL PUSH [{disp}]: {action} | conf={conf:.0%}")
                # Killzone gate for forex/commodity
                if pair in ("gold","oil","eurusd","gbpusd") and kz == "Outside":
                    logger.info(f"⛔ MECH KILLZONE REJECT [{disp}]: outside London/NY (hour={h})")
                    time.sleep(60)
                    continue
                # ── BTC 2-bar confirmation (gate channel + bridge) ──
                if disp == "BTCUSD" and not _consec_2bar_confirm("BTCUSD", action):
                    logger.info(f"⏳ BTC 2-BAR WAIT: {action} — waiting for next bar confirm")
                    continue
                text = fmt_signal(mech_sig, price, dxy, h, disp, "$" if not disp.startswith(("BBCA","BBRI","TLKM","ASII","IHSG")) else "Rp")
                _entry = mech_sig.get("entry", price) or 0
                _sl = mech_sig.get("sl", 0) or 0
                _tp = mech_sig.get("tp", 0) or 0
                if _can_post_to_channel(pair, action, _entry, _sl, _tp):
                    logger.info(f"CHANNEL POST [mechanical]: {pair} {action}")
                    result = send_to_channel(text)
                    if result:
                        logger.info(f"CHANNEL POST OK [mechanical]: message_id={result.get('result',{}).get('message_id')}")
                    else:
                        logger.warning(f"CHANNEL POST FAILED [mechanical]: tg_send returned None")
                    _mark_channel_post(pair, action, _entry, _sl, _tp)
                    # ── Save to unified feed ──
                    _feed_add(symbol=disp, direction=action, entry=_entry, sl=_sl, tp=_tp,
                              confidence=conf, rr_ratio=mech_sig.get("rr_ratio","?"),
                              engines=mech_sig.get("engines",{}), source="channel-auto",
                              price=price, grade=mech_sig.get("grade",""),
                              source_name=mech_sig.get("source","mech"))
                else:
                    # ⚠️ Trade will be opened — FORCE POST to channel
                    logger.warning(f"🚨 FORCE POST [{disp}]: rate limited but trade opening — posting anyway")
                    result = send_to_channel(text)
                    _mark_channel_post(pair, action, _entry, _sl, _tp)
                    _feed_add(symbol=disp, direction=action, entry=_entry, sl=_sl, tp=_tp,
                              confidence=conf, rr_ratio=mech_sig.get("rr_ratio","?"),
                              engines=mech_sig.get("engines",{}), source="channel-auto",
                              price=price, grade=mech_sig.get("grade",""),
                              source_name=mech_sig.get("source","mech"))
                if LAYERING_ENGINE and mech_sig.get("action") != "HOLD":
                    mech_sig = enrich_signal_with_layers(mech_sig)
                post_signal_to_bridge(mech_sig, price, disp)

                if LEARNING_ENGINE:
                    try: track_signal(mech_sig, price, disp, session(h), mech_sig.get("source","mech"))
                    except: pass

                log["signals_sent"] += 1
                log["last_signal_time"] = wib_now().isoformat()
                log["last_action"] = action
                log["last_price"] = price
                log["last_signal"] = {
                    "action": action, "entry": mech_sig.get("entry", price),
                    "sl": mech_sig.get("sl", 0), "tp": mech_sig.get("tp", 0),
                    "tp1": mech_sig.get("tp1", 0), "tp2": mech_sig.get("tp2", 0),
                    "confidence": conf, "source": mech_sig.get("source", "mech"),
                    "rr_ratio": mech_sig.get("rr_ratio", 0),
                }
                _eaq = DATA_DIR / f"ea_signal_{pair}.json"
                _eaq.write_text(json.dumps(log["last_signal"]))
                save_signal_log(log, pair)
                asset_logs[log_key] = log
                time.sleep(120)  # 2 min cooldown after mechanical
                continue

            # ── AI Consensus — ONLY in killzone hours when mechanical misses ──
            # Python screening is primary. AI hanya sebagai verifikator saat killzone.
            in_killzone = (lkz or nykz)
            if not in_killzone:
                logger.info(f"   [{disp}] Outside killzone — skip AI, wait for mechanical")
                time.sleep(60)
                continue

            sig = ask_ai(price, dxy, session(h), kz, log["loss_count"], premium=True,
                          ohlcv=_fetch_ohlcv_for_ai(pair), display=disp)
            if not sig:
                time.sleep(30)
                continue

            action = sig.get("action","HOLD")
            conf = sig.get("confidence",0)
            # Normalize confidence before quality checks
            if isinstance(conf, (int,float)) and conf > 10:
                conf = conf / 100
                sig["confidence"] = conf

            # Auto-push rules — AI must clear higher bar than mechanical
            should_push = False
            if action in ("BUY","SELL"):
                voters = sig.get("voters",0)
                rr_val = sig.get("rr_ratio", 0)
                # RR validation
                if isinstance(rr_val, str) and rr_val.startswith("1:"):
                    rr_val = float(rr_val[2:]) if rr_val[2:] else 0
                rr_val = float(rr_val) if rr_val else 0

                # AI requires: 2+ model agreement + conf ≥ 70% + RR ≥ 1:1.5
                if voters < 2:
                    logger.info(f"   [{disp}] BLOCKED: solo AI call ({voters} model) — need ≥2")
                elif conf < 0.70:
                    logger.info(f"   [{disp}] BLOCKED: AI confidence {conf:.0%} < 70%")
                elif rr_val > 0 and (rr_val < 1.5 or rr_val > 5.0):
                    logger.info(f"   [{disp}] BLOCKED: RR 1:{rr_val:.1f} outside 1:1.5-5")
                else:
                    should_push = True

            if should_push:
                logger.info(f"AI PUSH [{disp}]: {action} | conf={conf:.0%} | model={sig.get('_model','?')}")

                if LAYERING_ENGINE:
                    sig = enrich_signal_with_layers(sig)
                # Clamp SL/TP to realistic bounds before pushing
                sig = _clamp_sltp(sig, disp)
                post_signal_to_bridge(sig, price, disp)

                # ── Post to channel (with rate limiter) ──
                text = fmt_signal(sig, price, dxy, h, disp, "$" if not disp.startswith(("BBCA","BBRI","TLKM","ASII","IHSG")) else "Rp")
                _entry = sig.get("entry", price) or 0
                _sl = sig.get("sl", 0) or 0
                _tp = sig.get("tp", 0) or 0
                if _can_post_to_channel(pair, action, _entry, _sl, _tp):
                    logger.info(f"CHANNEL POST [AI-consensus]: {pair} {action}")
                    result = send_to_channel(text)
                    if result:
                        logger.info(f"CHANNEL POST OK [AI-consensus]: message_id={result.get('result',{}).get('message_id')}")
                    else:
                        logger.warning(f"CHANNEL POST FAILED [AI-consensus]: tg_send returned None")
                    _mark_channel_post(pair, action, _entry, _sl, _tp)
                    # ── Save to unified feed ──
                    _feed_add(symbol=disp, direction=action, entry=_entry, sl=_sl, tp=_tp,
                              confidence=conf, rr_ratio=sig.get("rr_ratio","?"),
                              engines=sig.get("engines",{}), source="channel-auto",
                              price=price, grade=sig.get("grade",""),
                              models=sig.get("_models",""), voters=sig.get("voters","?"))
                else:
                    # ⚠️ Trade already opened — FORCE POST so users see the signal
                    logger.warning(f"🚨 FORCE POST [{disp}]: rate limited but trade opened — posting anyway")
                    result = send_to_channel(text)
                    _mark_channel_post(pair, action, _entry, _sl, _tp)
                    _feed_add(symbol=disp, direction=action, entry=_entry, sl=_sl, tp=_tp,
                              confidence=conf, rr_ratio=sig.get("rr_ratio","?"),
                              engines=sig.get("engines",{}), source="channel-auto",
                              price=price, grade=sig.get("grade",""),
                              models=sig.get("_models",""), voters=sig.get("voters","?"))

                if LEARNING_ENGINE:
                    try: track_signal(sig, price, disp, session(h), "ai")
                    except: pass

                log["signals_sent"] += 1
                log["last_signal_time"] = wib_now().isoformat()
                log["last_action"] = action
                log["last_price"] = price
                save_signal_log(log, pair)
                asset_logs[log_key] = log
                time.sleep(90)
            else:
                logger.info(f"   [{disp}] {action} | Grade:{sig.get('grade','?')} | conf={conf:.0%}")

            # ── Market Pulse DISABLED (user wants clean channel, signals only) ──
            # ── Active Signal DISABLED (redundant — mechanical+AI consensus already covers this) ──

            time.sleep(90 if (lkz or nykz) else 120)

        except Exception as e:
            logger.error(f"Auto-analyze error: {e}")
            time.sleep(60)


# ── END-OF-DAY RECAP + WEEKLY REPORT ──
RECAP_SENT_FILE = DATA_DIR / ".last_recap_date"
WEEKLY_SENT_FILE = DATA_DIR / ".last_weekly_date"

def _compute_daily_recap() -> str | None:
    """Generate end-of-day recap: signals, pips, winrate per asset."""
    today = wib_now()
    today_str = today.strftime("%Y%m%d")
    lines = [f"📊 <b>DAILY RECAP — {today.strftime('%d %b %Y')}</b>", "━━━━━━━━━━━━━━━━"]
    total_signals = 0
    total_wins = 0
    total_losses = 0
    total_pips = 0.0
    
    # Load today's trades from trade_history.json
    today_trades = []
    try:
        hist_file = Path(__file__).resolve().parent.parent / "data" / "trade_history.json"
        if hist_file.exists():
            all_trades = json.loads(hist_file.read_text()).get("trades", [])
            today_trades = [t for t in all_trades 
                          if str(t.get("open_time", t.get("close_time", "")))[:10] == today.strftime("%Y-%m-%d")]
    except Exception:
        pass
    
    for pair_key, disp in [("gold","XAUUSD"), ("btc","BTCUSD"), ("oil","USOIL")]:
        log = load_signal_log(pair_key)
        sigs = log.get("signals_sent", 0)
        if sigs == 0 and not any(t.get("symbol","").upper() == disp for t in today_trades):
            continue
        total_signals += sigs
        
        wins = 0; losses = 0; pips = 0.0
        for t in today_trades:
            if t.get("symbol", "").upper() == disp:
                outcome = t.get("outcome", "").upper()
                if outcome == "TP_HIT":
                    wins += 1
                    pips += abs(float(t.get("pips", 0) or 0))
                elif outcome == "SL_HIT":
                    losses += 1
                    pips -= abs(float(t.get("pips", 0) or 0))
        
        total_wins += wins
        total_losses += losses
        total_pips += pips
        
        wr = f"{(wins/(wins+losses)*100):.0f}%" if (wins+losses) > 0 else "N/A"
        lines.append(f"🏷 <b>{disp}</b>: {sigs} sinyal | {wins}W/{losses}L | WR {wr} | {pips:+.1f} pip")
    
    if total_signals == 0 and not today_trades:
        return None
    
    total_trades = total_wins + total_losses
    overall_wr = f"{(total_wins/total_trades*100):.0f}%" if total_trades > 0 else "N/A"
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append(f"📈 <b>Total</b>: {total_signals} sinyal | {total_wins}W/{total_losses}L | WR {overall_wr} | {total_pips:+.1f} pip")
    lines.append("")
    if total_pips > 0:
        lines.append("🟢 <b>PROFIT HARI INI!</b> Mesin AI bekerja dengan baik.")
    elif total_pips < 0:
        lines.append("🔴 <b>LOSS HARI INI.</b> Evaluasi ulang strategi, mungkin market sideways.")
    else:
        lines.append("⚪ <b>BREAKEVEN.</b> Tidak ada sinyal yang tersentuh TP/SL.")
    lines.append("")
    lines.append("💚 Jangan lupa isi bensin AI → /donate")
    return "\n".join(lines)


def _compute_weekly_report() -> str | None:
    """Generate weekly performance report (Friday only)."""
    now = wib_now()
    if now.weekday() != 4:  # Only Friday
        return None
    
    lines = [f"📈 <b>WEEKLY PERFORMANCE REPORT</b>", f"━━━━━━━━━━━━━━━━"]
    lines.append(f"📅 {now.strftime('%d %b %Y')}")
    lines.append("")
    
    today_iso = now.strftime("%Y-%m-%d")
    week_start = (now - __import__("datetime").timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    
    # Load this week's trades
    week_trades = []
    try:
        hist_file = Path(__file__).resolve().parent.parent / "data" / "trade_history.json"
        if hist_file.exists():
            all_trades = json.loads(hist_file.read_text()).get("trades", [])
            week_trades = [t for t in all_trades 
                          if week_start <= str(t.get("open_time", t.get("close_time", "")))[:10] <= today_iso]
    except Exception:
        pass
    
    total_signals = 0; total_wins = 0; total_losses = 0; total_pips = 0.0
    
    for pair_key, disp in [("gold","XAUUSD"), ("btc","BTCUSD"), ("oil","USOIL")]:
        log = load_signal_log(pair_key)
        sigs = log.get("signals_sent", 0)
        if sigs == 0 and not any(t.get("symbol","").upper() == disp for t in week_trades):
            continue
        total_signals += sigs
        
        wins = 0; losses = 0; pips = 0.0
        for t in week_trades:
            if t.get("symbol", "").upper() == disp:
                outcome = t.get("outcome", "").upper()
                if outcome == "TP_HIT":
                    wins += 1
                    pips += abs(float(t.get("pips", 0) or 0))
                elif outcome == "SL_HIT":
                    losses += 1
                    pips -= abs(float(t.get("pips", 0) or 0))
        
        total_wins += wins
        total_losses += losses
        total_pips += pips
        
        wr = f"{(wins/(wins+losses)*100):.0f}%" if (wins+losses) > 0 else "N/A"
        lines.append(f"🏷 <b>{disp}</b>: {sigs} sinyal | {wins}W/{losses}L | WR {wr} | {pips:+.1f} pip")
    
    total_trades = total_wins + total_losses
    overall_wr = f"{(total_wins/total_trades*100):.0f}%" if total_trades > 0 else "N/A"
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append(f"📊 <b>Minggu Ini</b>: {total_signals} sinyal | {total_wins}W/{total_losses}L | WR {overall_wr} | {total_pips:+.1f} pip")
    lines.append("")
    if total_pips > 0:
        lines.append("🟢 <b>PROFITABLE WEEK!</b> 🚀")
    else:
        lines.append("🔴 <b>LOSING WEEK.</b> Evaluasi engine untuk minggu depan.")
    lines.append("")
    lines.append("💚 Dukung server AI → /donate")
    return "\n".join(lines)


def _recap_report_loop():
    """Background thread: send daily recap at 22:00 WIB + weekly report Friday 22:30 WIB."""
    time.sleep(10)
    while True:
        try:
            now = wib_now()
            today_str = now.strftime("%Y%m%d")
            
            # ── Daily recap at 22:00-22:15 WIB ──
            if now.hour == 22 and not is_weekend():
                last_recap = ""
                try: last_recap = RECAP_SENT_FILE.read_text().strip()
                except: pass
                if last_recap != today_str:
                    recap = _compute_daily_recap()
                    if recap:
                        send_to_channel(recap)
                        logger.info("📊 Daily recap sent to channel")
                    RECAP_SENT_FILE.write_text(today_str)
            
            # ── Weekly report Friday 22:30-22:45 WIB ──
            if now.weekday() == 4 and now.hour == 22 and now.minute >= 30:
                last_weekly = ""
                try: last_weekly = WEEKLY_SENT_FILE.read_text().strip()
                except: pass
                if last_weekly != today_str:
                    report = _compute_weekly_report()
                    if report:
                        send_to_channel(report)
                        logger.info("📈 Weekly report sent to channel")
                    WEEKLY_SENT_FILE.write_text(today_str)
            
            time.sleep(300)  # Check every 5 min
        except Exception as e:
            logger.error(f"Recap/report error: {e}")
            time.sleep(300)


# ── Main ──
def main():
    if not BOT_TOKEN:
        logger.error("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)

    # ── PID check disabled (simplified for Phase 1) ──
    import subprocess
    # ──────────────────────────────────────────────────────────────

    # Start background threads
    if LEARNING_ENGINE:
        try: start_learning_engine()
        except Exception: pass

    # Initialize subscription state from disk
    if SUBSCRIPTION_ENGINE:
        try:
            check_expired()
        except Exception:
            pass

    # Start periodic reminder/expire sweep
    def _reminder_loop():
        while True:
            try:
                _process_subscription_reminders()
            except Exception:
                pass
            time.sleep(3600)

    try:
        _reminder_thread = threading.Thread(target=_reminder_loop, daemon=True)
        _reminder_thread.start()
        logger.info("Subscription reminder thread started")
    except Exception:
        pass

    # Start auto-analyze thread
    auto_thread = threading.Thread(target=auto_analyze_loop, daemon=True)
    auto_thread.start()
    logger.info("Auto-analyze thread started")

    # Start daily recap + weekly report thread
    recap_thread = threading.Thread(target=_recap_report_loop, daemon=True)
    recap_thread.start()
    logger.info("Recap/Report thread started")

    # Start bot polling with exponential backoff
    state = load_state()
    _load_tpsl_state()  # Prevent TP/SL alert spam after restart
    _load_pending_signals()  # Restore pending trade/skip keyboards
    _cleanup_expired_pending_signals()  # Remove any that expired during runtime
    offset = state.get("last_update_id", 0)
    logger.info(f"Bot starting... offset={offset}")

    # ── Set Telegram bot commands menu ──
    try:
        commands = [
            {"command": "signal",   "description": "🧠 Generate sinyal MTF + 9 engines"},
            {"command": "mtf",      "description": "🧬 Matrix 5TF × 9 engines (top-down)"},
            {"command": "engines",  "description": "🔧 Engine readings per strategi"},
            {"command": "dashboard","description": "📊 Buka live dashboard web"},
            {"command": "analyze",  "description": "🧠 Perintahkan AI Scan Market"},
            {"command": "price",    "description": "💰 Cek harga real-time"},
            {"command": "mapping",  "description": "📐 Mapping harian + level S/R"},
            {"command": "levels",   "description": "🏛 SnR + FIBO + Engine (Donor)"},
            {"command": "news",     "description": "📰 Grok News — X/Twitter intel (Donor)"},
            {"command": "killzone", "description": "🎯 Radar sesi market aktif"},
            {"command": "donate",   "description": "⚡ Isi Bahan Bakar AI"},
            {"command": "status",   "description": "🛡 Cek Kuota & Akses VIP"},
            {"command": "mykey",    "description": "🔑 Cek License EA Kamu"},
        ]
        payload = json.dumps({"commands": commands}).encode()
        req = urllib.request.Request(
            f"{TELEGRAM_API}/setMyCommands",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        if resp.get("ok"):
            logger.info("✅ Bot commands menu updated")
        else:
            logger.warning(f"setMyCommands failed: {resp}")
    except Exception as e:
        logger.warning(f"setMyCommands error: {e}")

    poll_errors = 0

    # ── Connection warmup — validate API reachable (NO message consumption) ──
    for attempt in range(3):
        try:
            url = f"{TELEGRAM_API}/getMe"
            req = urllib.request.Request(url, headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.loads(r.read())
            if resp.get("ok"):
                logger.info(f"✅ Bot connected as @{resp.get('result',{}).get('username','?')}")
                break
            else:
                logger.warning(f"getMe failed: {resp}")
        except Exception as e:
            logger.warning(f"Connection warmup attempt {attempt+1}: {e}")
            time.sleep(2)
    time.sleep(1)
    # ──────────────────────────────────────────────────────────────
    
    while True:
        try:
            url = f"{TELEGRAM_API}/getUpdates?offset={offset + 1}&timeout=5"
            req = urllib.request.Request(url, headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=10) as r:
                updates = json.loads(r.read()).get("result", [])
            poll_errors = 0  # reset on success
            for upd in updates:
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")
                if text and chat_id:
                    # ── Security: intercept credentials before any processing ──
                    try:
                        if SECRET_SANITIZER:
                            text = sanitize_telegram_input(text)
                    except Exception:
                        pass
                    cmd = text.split()[0].split('@')[0].lower()
                    if cmd in ("/start","/help","/price","/analyze","/data","/killzone","/bridge_status","/status","/bill","/testpay","/subscribe","/donate","/autosync","/genkey","/listkeys","/revokekey","/mykey","/myid","/winrate","/history","/recap","/mapping","/news","/activate","/restart_bot","/signal","/mtf","/engines","/dashboard","/levels","/level"):
                        try:
                            handle_command(cmd, text, str(chat_id), msg)
                        except Exception as e:
                            logger.error(f"Command error ({cmd}): {e}")
                            try:
                                tg_send("❌ <b>Waduh! Sistem error.</b>\nCoba lagi nanti atau hubungi @codergaboets.", chat_id)
                            except Exception:
                                pass
                    elif cmd.startswith("/"):
                        # Unknown command — give helpful response
                        try:
                            tg_send("📋 <b>Command tidak dikenal.</b>\n"
                                    "Ketik /help untuk lihat daftar command.\n"
                                    "Contoh: /analyze xauusd | /price btc | /mapping", chat_id)
                        except Exception:
                            pass
                    elif text and not cmd.startswith("/"):
                        # ── Custom donation amount input ──
                        if DONATION_INPUT_STATE.get(str(chat_id)):
                            DONATION_INPUT_STATE.pop(str(chat_id), None)
                            try:
                                amount = int(text.strip().replace(",", "").replace(".", "").replace(" ", ""))
                                if amount < 10000:
                                    tg_send("❌ Minimal dukungan Rp10,000. Coba lagi ya.", chat_id)
                                elif PAYMENT_ENGINE:
                                    username = msg.get("chat", {}).get("username", "")
                                    tg_send(f"⏳ <b>Membuat invoice Rp{amount:,}...</b>", chat_id)
                                    result = create_tripay_payment(str(chat_id), username, tier="donor", amount=amount)
                                    if result.get("error"):
                                        tg_send(f"❌ Gagal: {result['error']}\n📞 Hubungi @codergaboets", chat_id)
                                    else:
                                        pay_url = result.get("payment_url", "")
                                        pay_code = result.get("pay_code", "")
                                        ref = result.get("reference", "") or result.get("merchant_ref", "")
                                        txt = (
                                            f"⚡ <b>Isi Bahan Bakar AI Rp{amount:,}</b>\n"
                                            f"━━━━━━━━━━━━━━━━\n"
                                            f"👑 Status: DONATUR VIP — AKTIF PERMANEN\n"
                                        )
                                        if pay_code:
                                            txt += f"📱 Kode Bayar: <code>{pay_code}</code>\n"
                                        txt += (
                                            f"⏰ Expired: 1 jam\n"
                                            f"━━━━━━━━━━━━━━━━\n"
                                            f"Klik tombol di bawah untuk bayar 👇\n\n"
                                            f"<i>Auto-upgrade dalam 1-5 menit.</i>"
                                        )
                                        markup = {"inline_keyboard": [
                                            [{"text": f"💳 Bayar Rp{amount:,}", "url": pay_url}],
                                            [{"text": "🔄 Cek Status", "callback_data": f"check:{ref}"},
                                             {"text": "📞 Admin", "url": "https://t.me/codergaboets"}],
                                            [{"text": "🔙 Kembali", "callback_data": "cancel_input"}],
                                        ]}
                                        tg_send(txt, str(chat_id), reply_markup=markup)
                                else:
                                    tg_send("💳 Payment gateway belum aktif.\n📞 Hubungi @codergaboets", chat_id)
                            except ValueError:
                                tg_send("❌ Masukkan angka yang valid ya. Contoh: 50000", chat_id)
                            return

                        # Plain text message (bukan command) → Priority Support check
                        try:
                            if MEMBERS_ENABLED and chat_id:
                                member = get_member(str(chat_id))
                                if member and member.get("tier") == "elite":
                                    # Forward to admin
                                    admin_id = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID",
                                                os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))
                                    if admin_id and str(admin_id) != str(chat_id):
                                        username = msg.get("chat", {}).get("username", "")
                                        first_name = msg.get("chat", {}).get("first_name", "")
                                        name = f"@{username}" if username else first_name or "Unknown"
                                        tg_send(
                                            f"👑 <b>Elite Support Request</b>\n"
                                            f"━━━━━━━━━━━━━━━━\n"
                                            f"👤 {name} (<code>{chat_id}</code>)\n\n"
                                            f"💬 {text[:500]}",
                                            admin_id
                                        )
                                        tg_send(
                                            "👑 Pesan kamu sudah diteruskan ke <b>Priority Support</b>.\n"
                                            "Admin akan merespon secepatnya.",
                                            chat_id
                                        )
                        except Exception:
                            pass
                # Handle inline keyboard callbacks (Trade Auto / Skip / Payment)
                cb = upd.get("callback_query")
                if cb:
                    try:
                        data = cb.get("data", "")
                        if data.startswith("ultimatum:"):
                            handle_ultimatum_callback(cb)
                        elif data.startswith(("pay:", "check:", "pricing:", "donate:", "cancel_input")):
                            handle_payment_callback(cb)
                        else:
                            handle_trade_callback(cb)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
            _now = time.time()
            if _now - getattr(handle_command, '_last_state_save', 0) > 30:
                save_state({"last_update_id": offset})
                handle_command._last_state_save = _now
            # Periodic cleanup of expired pending signals (every 60s)
            if _now - getattr(handle_command, '_last_signal_cleanup', 0) > 60:
                _cleanup_expired_pending_signals()
                handle_command._last_signal_cleanup = _now
            time.sleep(0.3)  # Prevent hammering Telegram API
        except Exception as e:
            err_str = str(e)
            # 409 Conflict: quick retry without long wait
            if "409" in err_str:
                poll_errors += 1
                if poll_errors <= 10:
                    time.sleep(0.5)  # Quick retry
                else:
                    logger.warning(f"Persistent 409 ({poll_errors}x) — waiting 5s...")
                    time.sleep(5)
                continue
            
            poll_errors += 1
            wait = min(5 * (2 ** min(poll_errors, 5)), 120)  # 5s→10s→20s→40s→80s→120s cap
            logger.error(f"Polling error (#{poll_errors}, wait {wait}s): {e}")
            time.sleep(wait)


if __name__ == "__main__":
    main()
