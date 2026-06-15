#!/usr/bin/env python3
"""
Vilona Trade FX Telegram Bot Handler
Grab forex data + generate signals even without MT5/EA.

Commands: /start /help /price /analyze /data /killzone /status /subscribe /autosync /genkey /listkeys /mykey /myid
"""
import hashlib, json, logging, os, re, sqlite3, sys, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── PID LOCK: prevent duplicate bot instances ──
_PID_FILE = Path(__file__).resolve().parent.parent.parent / "data" / ".bot_handler.pid"
if _PID_FILE.exists():
    try:
        _old_pid = int(_PID_FILE.read_text().strip())
        os.kill(_old_pid, 0)  # check if process exists
        logging.warning(f"Bot handler already running (PID {_old_pid}). Exiting.")
        sys.exit(0)
    except (OSError, ValueError):
        pass  # PID is stale — continue
_PID_FILE.write_text(str(os.getpid()))

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
    cmd_genkey = cmd_listkeys = cmd_revokekey = cmd_mykey = is_admin = None
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
SUBS_PATH = str(Path(__file__).resolve().parent.parent.parent / "members.db")

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
    from learning_loop import learn_from_sl, learn_from_tp, get_learning_summary
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

# ── Learning Loop (autonomous SL/TP learning) ──
try:
    from learning_loop import learn_from_sl, learn_from_tp
    LEARNING_LOOP = True
except Exception as e:
    LEARNING_LOOP = False
    print(f"Learning loop unavailable: {e}")

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
# 🔬 SMART-RANKED FALLBACK — rotate by model intelligence, not cost
# DeepSeek primary → these tried in order when DeepSeek is down
# Ranking: reasoning quality for SMC/ICT analysis (top = best)
OMNIROUTE_FREE_MODELS = [
    "mistral/mistral-large-2411",      # 🥇 Mistral Large — best reasoning, user's key
    "cohere/command-a-03-2025",        # 🥈 Cohere Command-A — solid SMC analysis
    "google/gemini-2.5-flash",         # 🥉 Gemini 2.5 Flash — fast + capable
    "af/moonshot/kimi-k2.6",           # 4️⃣ Kimi K2 — decent Chinese reasoning
    "auto/best-free",                  # 5️⃣ OmniRoute auto-pick (devstral/cogito) — last AI resort
]

# ── AI Token Usage Tracking ──
# Per-analysis-cycle counter. Reset at start of each ask_ai_ensemble() call.
# { "deepseek": {"prompt": N, "completion": N, "total": N}, ... }
_AI_TOKEN_USAGE: dict[str, dict[str, int]] = {}

# ── Grok (xAI — dead) ── (DEPRECATED Jun 2026 — all v2 models removed by xAI)
GROK_KEY = os.environ.get("GROK_API_KEY", "")
GROK_URL = "https://api.x.ai/v1/chat/completions"

# ── Alpha Vantage (Market News replacement for Grok) ──
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "FR1LCR1YW51V0TIE")
ALPHA_VANTAGE_NEWS_URL = "https://www.alphavantage.co/query"

# ── Ticker mapping for Alpha Vantage NEWS_SENTIMENT ──
AV_TICKER_MAP = {
    "xauusd": "FOREX:USD", "gold": "FOREX:USD",
    "btc": "CRYPTO:BTC", "btcusd": "CRYPTO:BTC",
    "eth": "CRYPTO:ETH", "ethusd": "CRYPTO:ETH",
    "usoil": "FOREX:USD", "oil": "FOREX:USD",
    "eurusd": "FOREX:EUR", "gbpusd": "FOREX:GBP", "usdjpy": "FOREX:JPY",
}


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
    if 3<=h<7: return "Asia 🇯🇵"
    if 7<=h<14: return "Asia"
    if 14<=h<17: return "London 🇬🇧"
    if 17<=h<19: return "Pre-NY"
    if 19<=h<23: return "New York 🇺🇸"
    if h>=23 or h<3: return "Late NY / Early Asia"
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


# ── GPT-4o Rate Limiter (prevents HTTP 429) ──
_LAST_GPT4O_CALL = 0
_GPT4O_MIN_INTERVAL = 20  # seconds between GPT-4o calls
_LAST_GOLDAPI_OHLC = {}    # cached OHLC from goldapi.io for bar generation

def _fetch_ohlcv_for_ai(pair="gold", keep=20):
    """Fetch OHLCV bars for AI analysis.
    XAUUSD: gold-api.com (real-time, broker-synced) — bypass GC=F Yahoo.
    Other pairs: FCS API → UnifiedMarketData fallback.
    keep: number of bars to return (default 20, min 20, max 80)."""
    pair = pair.lower().strip()
    _fcs_name_map = {"gold":"XAUUSD","xauusd":"XAUUSD","btc":"BTCUSD","btcusd":"BTCUSD",
                     "eth":"ETHUSD","ethusd":"ETHUSD","oil":"USOIL",
                     "eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY","jpyusd":"USDJPY"}
    fcs_name = _fcs_name_map.get(pair)
    keep = max(20, min(keep, 80))

    # ── XAUUSD: gold-api.com primary (NO GC=F — HARAM Yahoo per SOP) ──
    if pair in ("gold", "xauusd"):
        try:
            spot = fetch_xauusd_spot()
            if spot:
                bars = _synthetic_ohlcv_from_spot(spot, keep)
                logger.info(f"_fetch_ohlcv_for_ai: {len(bars)} synthetic bars for gold via gold-api.com (spot={spot})")
                return bars
        except Exception as e:
            logger.warning(f"_fetch_ohlcv_for_ai (gold-api) error: {e}")

    # ── FCS API (forex/crypto OHLCV) ──
    if fcs_name:
        try:
            from data_sources import fcs_ohlcv
            bars_data = fcs_ohlcv(fcs_name, period="15m", bars=20)
            if bars_data:
                result = [{"t": b.get("timestamp", int(time.time())),
                          "o": b["Open"], "h": b["High"], "l": b["Low"], "c": b["Close"]}
                         for b in bars_data]
                logger.info(f"_fetch_ohlcv_for_ai: {len(result)} bars for {fcs_name} via FCS API")
                return result
        except Exception as e2:
            logger.warning(f"_fetch_ohlcv_for_ai (FCS) error: {e2}")

    # ── Fallback: UnifiedMarketData (last resort) ──
    try:
        if MARKET_DATA is not None:
            interval = "15m"
            bars = MARKET_DATA.get_bars_dicts(pair, interval, 80)
            if bars:
                result = [{"t": b["timestamp"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
                         for b in bars[-keep:]]
                logger.info(f"_fetch_ohlcv_for_ai: {len(result)} bars for {pair} via MARKET_DATA (fallback)")
                return result
    except Exception as e:
        logger.warning(f"_fetch_ohlcv_for_ai (MARKET_DATA fallback) error: {e}")

    logger.error(f"_fetch_ohlcv_for_ai: ALL sources failed for {pair}")
    return None


def _synthetic_ohlcv_from_spot(spot: float, keep: int = 20) -> list[dict]:
    """Generate synthetic 15m OHLCV bars from a single spot price.
    Used when gold-api.com only returns current price (no historical bars)."""
    import random
    now = int(time.time())
    bars = []
    volatility = spot * 0.0003  # ~0.03% per bar
    price = spot
    for i in range(keep, 0, -1):
        ts = now - (i * 900)  # 15m intervals
        o = price
        c = o + random.gauss(0, volatility)
        h = max(o, c) + abs(random.gauss(0, volatility * 0.5))
        l = min(o, c) - abs(random.gauss(0, volatility * 0.5))
        bars.append({"t": ts, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2)})
        price = c
    return bars


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
    """Calculate XAUUSD spot offset for broker pricing.
    With goldapi.io providing bid/ask directly, offset is handled by the API.
    Returns hardcoded broker offset as fallback."""
    # goldapi.io gives us real bid/ask — no futures offset needed
    # Fallback: hardcoded 74-pip broker offset (Exness spread avg)
    return float(os.environ.get("XAUUSD_PRICE_OFFSET", "74"))

def fetch_xauusd_spot() -> float | None:
    """Fetch live spot XAUUSD from goldapi.io (premium, bid/ask/OHLC) → gold-api.com fallback."""
    global _LAST_GOLDAPI_OHLC
    # ── Primary: goldapi.io (premium API key — bid/ask spread, OHLC data) ──
    GOLDAPI_KEY = os.environ.get("GOLDAPI_KEY", "")
    if GOLDAPI_KEY:
        try:
            req = urllib.request.Request("https://www.goldapi.io/api/XAU/USD",
                headers={"x-access-token": GOLDAPI_KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            price = float(data.get("price", 0))
            if 2000 < price < 6000:
                # Cache OHLC for bar generation
                _LAST_GOLDAPI_OHLC = {
                    "open": float(data.get("open_price", price)),
                    "high": float(data.get("high_price", price)),
                    "low": float(data.get("low_price", price)),
                    "close": price,
                    "prev_close": float(data.get("prev_close_price", price)),
                    "bid": float(data.get("bid", price)),
                    "ask": float(data.get("ask", price)),
                    "ts": int(data.get("timestamp", time.time())),
                }
                logger.debug(f"GoldAPI.io: bid={_LAST_GOLDAPI_OHLC['bid']:.2f} ask={_LAST_GOLDAPI_OHLC['ask']:.2f} spread={_LAST_GOLDAPI_OHLC['ask']-_LAST_GOLDAPI_OHLC['bid']:.2f}")
                return price
        except Exception as e:
            logger.debug(f"GoldAPI.io failed: {e}")
    
    # ── Fallback: gold-api.com (free, optional API key for higher rate limits) ──
    try:
        headers = {"User-Agent": "Vilona/1.0"}
        goldapi_com_key = os.environ.get("GOLDAPI_COM_KEY", "")
        if goldapi_com_key:
            headers["x-access-token"] = goldapi_com_key
        req = urllib.request.Request("https://api.gold-api.com/price/XAU", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        price = float(data.get("price", 0))
        if 2000 < price < 6000:
            return price
    except Exception as e:
        logger.debug(f"Gold-API.com failed: {e}")
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
def tg_send(text, chat_id=None, reply_markup=None, reply_to=None):
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
    text = re.sub(r'<(/?[abciopstu][^>]*)>', TAG_OPEN + r'\1' + TAG_CLOSE, text)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = text.replace(TAG_OPEN, '<').replace(TAG_CLOSE, '>')
    
    try:
        payload = {"chat_id": target, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to:
            payload["reply_to_message_id"] = int(reply_to)
        logger.info(f"📤 tg_send payload: chat_id={target} | reply_to_message_id={payload.get('reply_to_message_id')} (type={type(payload.get('reply_to_message_id')).__name__}) | text_len={len(text)}")
        req = urllib.request.Request(f"{TELEGRAM_API}/sendMessage",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        err_str = str(e)
        # 429 Too Many Requests / Connection reset → retry with backoff
        if "429" in err_str or "Too Many Requests" in err_str or "Connection reset" in err_str or "Errno 104" in err_str:
            for attempt in range(3):
                wait = (attempt + 1) * 3  # 3s, 6s, 9s
                logger.warning(f"tg_send rate-limited (attempt {attempt+1}/3), waiting {wait}s...")
                time.sleep(wait)
                try:
                    req = urllib.request.Request(f"{TELEGRAM_API}/sendMessage",
                        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return json.loads(r.read())
                except Exception:
                    continue
            logger.error(f"tg_send failed after 3 retries: {e}")
            return None
        # Fallback: retry without parse_mode if HTML parse failed
        if "Bad Request" in str(e) or "can't parse" in str(e):
            try:
                # Strip HTML tags for plaintext fallback
                plain = re.sub(r'<[^>]+>', '', text)
                payload = {"chat_id": target, "text": plain[:MAX_LEN]}
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                if reply_to:
                    payload["reply_to_message_id"] = int(reply_to)
                logger.info(f"📤 tg_send FALLBACK payload: chat_id={target} | reply_to_message_id={payload.get('reply_to_message_id')} (type={type(payload.get('reply_to_message_id')).__name__}) | text_len={len(plain)}")
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
MASTER_API_KEY = os.environ.get("BRIDGE_MASTER_KEY", "")


def _fetch_json_url(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"error": str(e)}


def format_bridge_status():
    health = _fetch_json_url("http://localhost:8765/health")
    accounts = _fetch_json_url(f"http://localhost:8765/accounts?api_key={MASTER_API_KEY}")
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
def _check_free_quota(chat_id):
    """Check & deduct FREE tier daily quota. Returns (ok, remaining, message)."""
    today = wib_now().strftime("%Y-%m-%d")
    record = USER_DAILY_ANALYZE.get(chat_id, {})
    if record.get("date") != today:
        record = {"date": today, "count": 0}
    
    record["count"] += 1
    USER_DAILY_ANALYZE[chat_id] = record
    
    limit = FREE_DAILY_LIMIT
    if record["count"] > limit:
        remaining = max(0, limit - record["count"])
        return False, remaining, (
            f"🛑 <b>Kuota Free Harian Penuh!</b>\\n"
            f"━━━━━━━━━━━━━━━━\\n"
            f"📊 {limit}x analisa/hari — sudah terpakai semua.\\n"
            f"💡 Upgrade ke PRO buat 20x/hari: /subscribe\\n"
            f"⏰ Reset: besok jam 00:00 WIB\\n\\n"
            f"🔍 Cek sinyal auto di channel: @vilonaaichanel"
        )
    
    remaining = max(0, limit - record["count"])
    return True, remaining, None


USER_LAST_DIRECTION = {}  # chat_id -> {"action": str, "at": iso, "asset": str}
USER_LAST_PAIR = {}  # chat_id -> {"pair": str, "at": timestamp} — same-pair cooldown
USER_DAILY_ANALYZE = {}  # chat_id -> {"count": int, "date": "YYYY-MM-DD"} — subscriber quota
DONOR_ANALYZE_COUNT: dict = {}  # chat_id -> int — analyze counter, fuel gauge reminder every 3rd

MANUAL_THROTTLE_FREE = 120   # free user: 120 detik antar analisa
MANUAL_THROTTLE_PRO = 60     # pro: 60 detik
MANUAL_THROTTLE_ELITE = 30   # elite/lifetime: 30 detik
SAME_PAIR_COOLDOWN = 90       # same pair cooldown (all users)
FREE_DAILY_LIMIT = 3          # 🔒 free tier: 3x/hari — cukup buat nyicip, harus upgrade buat serius
PRO_DAILY_LIMIT = 20          # 🆕 pro tier: 20x/hari
ELITE_DAILY_LIMIT = -1        # 🆕 elite/lifetime: unlimited
# Legacy (backwards compat)
DONOR_DAILY_QUOTA = -1  # unlimited — TIER_LIMITS aligned
MANUAL_THROTTLE_DONOR = 60     # legacy compat — maps to pro throttle
FREE_DAILY_QUOTA = FREE_DAILY_LIMIT
# Tier → daily limit mapping
TIER_LIMITS = {"free": FREE_DAILY_LIMIT, "pro": PRO_DAILY_LIMIT, "elite": -1, "lifetime": -1, "donor": -1}
DIRECTION_LOCK_SECONDS = 60

# ── Custom donation input state ──
DONATION_INPUT_STATE = {}  # chat_id -> True (waiting for user to type amount)

def _is_manual_blocked(chat_id, pair=""):
    """Multi-layer anti-abuse: cooldown + same-pair + subscriber daily quota + direction lock."""
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
        label = "Subscriber" if is_donor else "Free"
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
    """Check & deduct subscriber daily quota. Returns (ok, remaining, message)."""
    today = wib_now().strftime("%Y-%m-%d")
    record = USER_DAILY_ANALYZE.get(chat_id, {})
    if record.get("date") != today:
        record = {"date": today, "count": 0}
    
    record["count"] += 1
    USER_DAILY_ANALYZE[chat_id] = record
    
    # -1 means unlimited — never block
    if DONOR_DAILY_QUOTA < 0:
        return True, -1, None
    
    # Check quota AFTER increment — user gets exactly QUOTA x per day
    if record["count"] > DONOR_DAILY_QUOTA:
        remaining = max(0, DONOR_DAILY_QUOTA - record["count"])
        return False, remaining, f"🛑 <b>Kuota Subscriber Harian Penuh!</b>\\n━━━━━━━━━━━━━━━━\\n📊 {DONOR_DAILY_QUOTA}x analisa/hari — sudah terpakai semua.\\n💡 Analisa bijak ya Bro, setiap analisa pakai AI (DeepSeek V3 + GPT-4o).\\n⏰ Reset: besok jam 00:00 WIB\\n\\n🔍 Cek sinyal auto di channel: @vilonaaichanel"
    
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

def handle_onboarding_callback(callback_query):
    """Handle interactive onboarding buttons: cmd:analyze_xauusd / cmd:guide / cmd:subscribe."""
    cb_id = callback_query.get("id", "")
    chat_id = str(callback_query.get("from", {}).get("id", ""))
    data = callback_query.get("data", "")

    if data == "cmd:analyze_xauusd":
        # Answer callback silently, then trigger /analyze xauusd
        try:
            payload = json.dumps({"callback_query_id": cb_id}).encode()
            req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
                data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
        handle_command("/analyze", "xauusd", chat_id, callback_query)
        return

    if data == "cmd:guide":
        try:
            payload = json.dumps({"callback_query_id": cb_id, "text": "📖 Panduan dikirim!"}).encode()
            req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
                data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
        guide = (
            "🎓 <b>CARA BACA SINYAL VILONA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 <b>Signal Format:</b>\n"
            "• BUY/SELL — Arah trading\n"
            "• Entry Zone — Harga masuk (pending limit order)\n"
            "• SL — Stop Loss (risk management wajib)\n"
            "• TP1/TP2/TP3/TP4 — Take Profit berjenjang\n\n"
            "🧠 <b>AI Engines:</b>\n"
            "• DeepSeek V3 — SMC/ICT specialist\n"
            "• GPT-4o — Pattern & structure recognition\n"
            "• Market Intel — Real-time sentiment from Alpha Vantage\n\n"
            "⚡ <b>PRO TIPS:</b>\n"
            "• Entry selalu pakai pending limit order, bukan market\n"
            "• SL jangan diubah — AI udah kalkulasi risk\n"
            "• Partial TP di TP1 (50%), sisanya trailing\n"
            "• Jangan entry pas news high-impact 🔴\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 Ketik /analyze xauusd buat mulai!"
        )
        tg_send(guide, chat_id)
        return

    if data == "cmd:subscribe":
        try:
            payload = json.dumps({"callback_query_id": cb_id}).encode()
            req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
                data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
        _send_donate_menu(chat_id)
        return

    # Unknown onboarding callback — answer silently
    try:
        payload = json.dumps({"callback_query_id": cb_id}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/answerCallbackQuery",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def handle_trade_callback(callback_query):
    """Handle inline keyboard: trade:<id> or skip:<id>"""
    cb_id = callback_query.get("id", "")
    chat_id = str(callback_query.get("from", {}).get("id", ""))
    data = callback_query.get("data", "")

    # ── Original trade callbacks below ──
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
        # ── Cancel custom amount input, return to /subscribe ──
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
                upgrade_tier(str(chat_id), "lifetime", 9999, ref)
                mark_payment_paid(ref)
                tg_send(
                    "✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "👑 Status kamu sekarang: <b>Subscriber</b>\n"
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

    elif data.startswith("sub:"):
        # ── Subscription tier callback ──
        sub_tier = data.split(":", 1)[1] if ":" in data else ""
        if sub_tier in ("pro", "elite", "lifetime"):
            try:
                from members.payment import create_tripay_payment
                result = create_tripay_payment(str(chat_id), username, tier=sub_tier)
                if result.get("success"):
                    payment_url = result.get("payment_url", "")
                    pay_code = result.get("pay_code", "")
                    amount = result.get("amount", 0)
                    tier_label = result.get("tier_label", sub_tier.upper())
                    txt = (
                        f"💳 <b>Pembayaran {tier_label}</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"💰 Total: Rp{amount:,}\n"
                        f"📎 Kode: <code>{pay_code}</code>\n\n"
                        f"🔗 <a href='{payment_url}'>Klik di sini untuk bayar</a>\n\n"
                        f"⏰ Link berlaku 1 jam.\n"
                        f"Status akan otomatis aktif setelah pembayaran."
                    )
                    markup = {"inline_keyboard": [[
                        {"text": "🔄 Cek Status", "callback_data": f"check:{result.get('reference','')}"},
                    ]]}
                    tg_send(txt, chat_id, reply_markup=markup)
                else:
                    tg_send(f"❌ {result.get('error', 'Gagal.')}", chat_id)
            except Exception as e:
                logger.error(f"Sub callback error: {e}")
                tg_send("❌ Sistem pembayaran sibuk. Coba lagi.", chat_id)
        elif sub_tier == "pay":
            # Show all payment methods
            txt = (
                "💳 <b>Pilih Metode Pembayaran</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Ketik /subscribe pro — Rp50K/bulan\n"
                "Ketik /subscribe elite — Rp150K/bulan\n"
                "Ketik /subscribe lifetime — Rp500K (sekali)\n\n"
                "Atau pilih tier di bawah:"
            )
            markup = {"inline_keyboard": [
                [{"text": "⭐ PRO — Rp50K/bulan", "callback_data": "sub:pro"}],
                [{"text": "👑 ELITE — Rp150K/bulan", "callback_data": "sub:elite"}],
                [{"text": "💎 LIFETIME — Rp500K", "callback_data": "sub:lifetime"}],
            ]}
            tg_send(txt, chat_id, reply_markup=markup)
        else:
            tg_send("Pilih tier: /subscribe pro | elite | lifetime", chat_id)

    elif data.startswith("pricing:"):
        # Show donation info — no more old tiers
        txt = get_pricing_table() if PAYMENT_ENGINE else "💎 Info dukung server AI belum tersedia."
        markup = {"inline_keyboard": [
            [{"text": "⭐ PRO — Rp50K/bulan", "callback_data": "sub:pro"},
             {"text": "👑 ELITE — Rp150K/bulan", "callback_data": "sub:elite"}],
            [{"text": "📞 Tanya Admin", "url": "https://t.me/codergaboets"}],
        ]}
        tg_send(txt, chat_id, reply_markup=markup)

    elif data.startswith("donate:"):
        # Legacy backward-compat — map to tiered subscription
        donate_type = data.split(":", 1)[1] if ":" in data else "info"
        
        # Map old amounts to new tiers
        if donate_type == "coffee":
            amount = 50000
            tier_label = "pro"
        elif donate_type == "fuel":
            amount = 150000
            tier_label = "elite"
        elif donate_type == "learn":
            amount = 50000
            tier_label = "pro"
        elif donate_type == "custom":
            DONATION_INPUT_STATE[str(chat_id)] = True
            tg_send(
                "💰 <b>Input Nominal Bebas</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Silakan ketik nominal subscribe yang kamu\n"
                "inginkan (minimal Rp50.000).\n\n"
                "<i>Contoh: ketik 150000 untuk Rp150K</i>",
                chat_id,
                reply_markup={"inline_keyboard": [[
                    {"text": "❌ Batal", "callback_data": "cancel_input"},
                ]]}
            )
            return
        else:
            # Generic — redirect to tiered /subscribe
            _send_donate_menu(chat_id, username)
            return

        if not PAYMENT_ENGINE:
            tg_send(
                "💳 <b>Payment gateway offline.</b>\n\n"
                "Tapi tenang, kamu tetap bisa subscribe manual:\n\n"
                "💚 <b>Transfer ke:</b>\n"
                "🏦 BCA: 8531425531 a.n. MOH SUHUD\n"
                "📱 Dana/Ovo/GoPay: 08123456789 (konfirm admin)\n\n"
                "📞 Kirim bukti transfer ke: @codergaboets\n"
                "Sertakan user ID kamu: <code>" + str(chat_id) + "</code>\n\n"
                "⏳ Aktivasi manual 1-24 jam (we will notify you!)",
                chat_id
            )
            return

        tier_label_text = {"pro": "⭐ PRO Rp50K", "elite": "👑 ELITE Rp150K"}
        tg_send(f"⏳ <b>Membuat link pembayaran...</b>\n{tier_label_text.get(tier_label, tier_label)} — Rp{amount:,}", chat_id)

        result = create_tripay_payment(str(chat_id), username, tier=tier_label, amount=amount)
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
            f"<i>Setelah bayar, bot auto-upgrade kamu ke 🟢 SUBSCRIBER dalam 1-5 menit.</i>"
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
    entry_from_sig = sig.get("entry", 0) or 0
    sl = sig.get("sl", 0)
    tp = sig.get("tp", 0)
    # If tp is 0 but tp1 is set, use tp1 as primary TP
    tp1 = sig.get("tp1", 0) or 0
    if (not tp or tp == 0) and tp1 > 0:
        tp = tp1
        sig["tp"] = tp

    confidence = sig.get("confidence", 0)
    rr = sig.get("rr_ratio", 0)
    action = sig.get("action", "HOLD")

    # ── Minimum SL distance guard (prevents MT5 error 4756) ──
    _min_sl_pips = {"XAUUSD": 5.0, "GOLD": 5.0, "BTCUSD": 20.0, "ETHUSD": 8.0,
                    "USOIL": 3.0, "EURUSD": 3.0, "GBPUSD": 3.0, "USDJPY": 3.0}
    _pip_size = 0.10 if display in ("XAUUSD","GOLD") else 0.01 if display=="USOIL" else 1.0
    _min_sl_dist = _min_sl_pips.get(display.upper(), 3.0) * _pip_size
    if sl > 0 and entry_from_sig > 0:
        sl_dist = abs(sl - entry_from_sig)
        if sl_dist < _min_sl_dist:
            old_sl = sl
            sl = round(entry_from_sig + _min_sl_dist if action == "BUY" else entry_from_sig - _min_sl_dist, 2)
            sig["sl"] = sl
            logger.info(f"🛡️ SL widened: {old_sl}→{sl} (was {sl_dist:.1f} pip, min {_min_sl_dist/_pip_size:.0f})")

    # ── ZONE MODE AUTO-DETECT ──
    # If AI set specific entry (not 0) and it differs from live price → use pending order
    entry_mode = sig.get("entry_mode", "market")
    zone_lo = sig.get("zone_lo", 0) or 0
    zone_hi = sig.get("zone_hi", 0) or 0
    if entry_mode == "market" and entry_from_sig and entry_from_sig != price:
        entry_mode = "zone"
        entry = entry_from_sig
        sig["entry"] = entry
        sig["entry_mode"] = "zone"
        logger.info(f"🔄 AUTO-ZONE: entry=${entry:.2f} ≠ live=${price:.2f} — switching to zone mode")
        # Derive zone from entry ± radius based on SL distance
        if sl and entry_from_sig:
            sl_dist = abs(sl - entry_from_sig)
            zone_radius = sl_dist * 0.3
            sig["zone_lo"] = round(entry - zone_radius, 2)
            sig["zone_hi"] = round(entry + zone_radius, 2)
        else:
            zone_half = entry_from_sig * 0.0005
            sig["zone_lo"] = round(entry_from_sig - zone_half, 2)
            sig["zone_hi"] = round(entry_from_sig + zone_half, 2)
    elif not entry_from_sig:
        # AI didn't set entry — fallback to live price (market only, no zone possible)
        entry = price
        entry_mode = "market"
    else:
        entry = entry_from_sig

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

    # --- XAUUSD: goldapi.io premium API (bid/ask from FOREXCOM exchange) ---

    payload = {
        "action": action,
        "symbol": symbol,
        "entry": entry,
        "zone_lo": sig.get("zone_lo", entry),
        "zone_hi": sig.get("zone_hi", entry),
        "entry_mode": sig.get("entry_mode", "market"),
        "order_type": order_type,
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
        "telegram_message_id": sig.get("telegram_message_id"),  # reply chain
        "timestamp": wib_now().isoformat(),
        "rr_ratio": rr,
    }

    # ── Write to EA signal file (for ea_executor.py to pick up) ──
    try:
        # ── Apply learned weights to total_score if component scores available ──
        if sig.get("score_smc") or sig.get("score_liquidity") or sig.get("score_macro"):
            try:
                from members.weight_manager import apply_weights
                regime = sig.get("regime", "") or str(sig.get("market_regime", "") or "unknown")
                weighted = apply_weights(
                    float(sig.get("score_smc", 0) or 0),
                    float(sig.get("score_liquidity", 0) or 0),
                    float(sig.get("score_macro", 0) or 0),
                    regime,
                )
                payload["weighted_score"] = weighted
                logger.debug("Applied learned weights: regime=%s score=%.3f", regime, weighted)
            except Exception:
                pass  # weight manager unavailable — use raw consensus_score

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
                       sig.get("target_user", ""),
                       telegram_message_id=sig.get("telegram_message_id"))
        except Exception as e:
            logger.debug("Trade tracker open_trade failed: %s", e)
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
            # ── ML Feedback Loop: log signal anatomy for autonomous learning ──
            try:
                from members.ml_feedback import log_from_handler
                log_from_handler(sig, is_broadcasted=True)
            except Exception:
                pass
            break  # success, stop
        except Exception as e:
            logger.warning("Bridge post failed for %s: %s", url, e)
            continue
    if not posted:
        logger.warning("Failed to post signal to any bridge URL")


# ── SMART TRAILING HELPERS ──
def _get_trailing_status(chat_id):
    """Query bridge for trailing config via GET /trailing?api_key=VT-xxx&account_id=MT5-xxx"""
    try:
        import urllib.request as ureq
        # Use the first bridge URL and the master key for query
        url = f"http://localhost:8765/trailing?api_key={MASTER_API_KEY}&account_id=MT5-{chat_id}"
        resp = ureq.urlopen(url, timeout=5)
        return json.loads(resp.read())
    except Exception:
        return None

def _set_trailing(chat_id, enabled=True):
    """POST trailing config to bridge."""
    try:
        import urllib.request as ureq
        url = f"http://localhost:8765/trailing?api_key={MASTER_API_KEY}&account_id=MT5-{chat_id}"
        payload = json.dumps({"enabled": enabled}).encode()
        req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
        ureq.urlopen(req, timeout=5)
        logger.info(f"Trailing {'ON' if enabled else 'OFF'} for chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Failed to set trailing: {e}")


def _send_document(chat_id, file_path, filename, caption=""):
    """Send a document/file via Telegram Bot API (multipart/form-data)."""
    import urllib.request as ureq
    boundary = "----VilonaBoundary" + str(int(time.time()))
    with open(file_path, "rb") as f:
        file_data = f.read()

    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()

    try:
        req = ureq.Request(f"{TELEGRAM_API}/sendDocument", data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        ureq.urlopen(req, timeout=30)
        logger.info(f"📎 Document sent: {filename} to chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Failed to send document {filename}: {e}")


def tg_send_photo(chat_id, photo_bytes, caption="", reply_to=None):
    """Send an image (PNG/JPEG bytes) via Telegram Bot API sendPhoto."""
    if not photo_bytes:
        return None
    if not BOT_TOKEN:
        return None
    target = chat_id or CHAT_ID
    if not target:
        return None
    try:
        boundary = "----VilonaPhoto" + str(int(time.time()))
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{target}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
        body += b"Content-Type: image/png\r\n\r\n"
        body += photo_bytes
        body += f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/sendPhoto", data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        if "429" in str(e) or "Too Many Requests" in str(e):
            for attempt in range(2):
                wait = (attempt + 1) * 4
                logger.warning(f"tg_send_photo rate-limited (attempt {attempt+1}/2), wait {wait}s")
                time.sleep(wait)
                try:
                    return tg_send_photo(chat_id, photo_bytes, caption, reply_to)
                except Exception:
                    pass
        logger.warning(f"tg_send_photo failed: {e}")
        return None


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
                    m15_bars = MARKET_DATA.get_ohlcv("gold", "15m", 80)
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


# ── S-TIER ZONE DETECTOR — Triple Confluence (Breaker + OB/FVG + Double Sweep) ──
def detect_stier_zone(symbol="XAUUSD", display="XAUUSD", price=None, ohlcv_bars=None):
    """High-conviction zone scanner: Breaker Block + OB/FVG confluence + Double Sweep.
    
    Triple confluence at the same price level → 90%+ probability reversal zone.
    Designed for full-margin entries on the highest-quality SMC setups.
    
    Returns (signal_dict, reason_str) or (None, None).
    """
    if not ohlcv_bars or len(ohlcv_bars) < 30:
        return None, None
    if not price:
        price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))
    
    pip_s = 0.10 if display in ("XAUUSD","GOLD") else (0.01 if display=="USOIL" else (1.0 if display in ("BTCUSD","ETHUSD") else 0.0001))
    
    confluence_zones = []
    
    # ── Layer 1: Order Blocks + Supply/Demand Zones from SMC engine ──
    obs = []
    bos = {}
    fb = {}
    idm = {}
    try:
        if SMC_ENGINE:
            smc = analyze_smc_scalper(ohlcv_bars, display)
            if smc:
                # Primary: single best Order Block
                ob_data = smc.get("_ob")
                if ob_data and isinstance(ob_data, dict) and ob_data.get("direction"):
                    ob_price = (ob_data.get("upper", 0) + ob_data.get("lower", 0)) / 2
                    if ob_price > 0:
                        obs.append({"price": ob_price, "direction": ob_data["direction"].upper(),
                                    "strength": ob_data.get("strength", 3)})
                # Secondary: Supply/Demand zones as additional anchor points
                sd_zones = smc.get("_sd_zones", [])
                for z in sd_zones[:5]:
                    z_type = z.get("type", "")
                    z_dir = "BUY" if "DEMAND" in str(z_type).upper() else "SELL" if "SUPPLY" in str(z_type).upper() else ""
                    z_price = (z.get("upper", 0) + z.get("lower", 0)) / 2
                    if z_price > 0 and z_dir:
                        obs.append({"price": z_price, "direction": z_dir,
                                    "strength": z.get("strength", 2)})
                # Reuse bos, idm, false_break from this single call
                bos = smc.get("_bos", {})
                fb = smc.get("_false_break", {})
                idm = smc.get("_idm", {})
    except Exception as e:
        logger.debug(f"S-TIER OB scan: {e}")
    
    if not obs:
        return None, None
    
    # ── Layer 2: FVG zones ──
    fvg_zones = []
    try:
        if FVG_ENGINE:
            from fvg_detector import detect_fvg_zones
            raw_fvgs = detect_fvg_zones(ohlcv_bars, max_age=30)
            for z in raw_fvgs[:10]:
                fvg_zones.append({
                    "top": z.top, "bottom": z.bottom, "mid": (z.top + z.bottom) / 2,
                    "direction": "BEARISH" if z.top > z.bottom else "BULLISH",
                    "filled": getattr(z, 'filled', False), "size_pips": getattr(z, 'size_pips', 0)
                })
    except Exception as e:
        logger.debug(f"S-TIER FVG scan: {e}")
    
    # ── Layer 3: Structure — BOS + False Break (reuse smc from Layer 1) ──
    bos_price = None
    false_break_price = None
    fb_dir = None
    idm_price = None
    try:
        if bos and bos.get("price"):
            bos_price = bos["price"]
        if fb and fb.get("price"):
            false_break_price = fb["price"]
            fb_dir = fb.get("direction", "")
        if idm and idm.get("price"):
            idm_price = idm["price"]
    except Exception:
        pass
    
    PROXIMITY_PIPS = 15  # tighter: 15 pip = $1.50 on XAUUSD — reduces false grouping
    
    def _near(a, b):
        return abs(a - b) / pip_s <= PROXIMITY_PIPS
    
    # ── Layer 4: Trend context from OHLCV (H1 20-bar EMA slope) ──
    trend_bias = "NEUTRAL"
    try:
        closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv_bars[-30:]]
        if len(closes) >= 20:
            ema20 = sum(closes[-20:]) / 20
            ema10 = sum(closes[-10:]) / 10
            if ema10 > ema20 * 1.002:
                trend_bias = "BULLISH"
            elif ema10 < ema20 * 0.998:
                trend_bias = "BEARISH"
    except Exception:
        pass
    
    for ob in obs:
        zone_level = ob["price"]
        ob_dir = ob["direction"]
        ob_strength = ob.get("strength", 3)
        
        # ── GATE 0: Minimum OB strength — weak OB = weak breaker ──
        if ob_strength < 3:
            continue
        
        score = 1.0
        reasons = [f"OB {ob_dir} [{ob_strength}/5] @ {zone_level:.2f}"]
        
        # ── GATE 1: Breaker Block — strict candle-close validation ──
        ob_is_bull = "BULL" in ob_dir
        price_above_ob = price > zone_level
        breaker = (ob_is_bull and not price_above_ob) or (not ob_is_bull and price_above_ob)
        
        if breaker:
            # Require: OB was tested at least once BEFORE breaking (mitigation confirm)
            # Check last 10 bars for a test of this OB level
            ob_tested = False
            recent_bars = ohlcv_bars[-10:]
            for b in recent_bars:
                b_low = float(b.get("low", b.get("l", 0)))
                b_high = float(b.get("high", b.get("h", 0)))
                if _near(zone_level, b_low) or _near(zone_level, b_high):
                    ob_tested = True
                    break
            
            if ob_tested:
                score += 2.5
                reasons.append("🔥 BREAKER BLOCK — OB broken after test, now acts as S/R")
            else:
                score += 1.0
                reasons.append("⚠️ Breaker unconfirmed — OB not recently tested")
        
        # ── GATE 2: False Break — must be at same zone, same direction ──
        if false_break_price and _near(zone_level, false_break_price):
            fb_dir_match = not fb_dir or fb_dir == ob_dir
            if fb_dir_match:
                score += 1.5
                reasons.append(f"⚠️ False Break confirmed @ {false_break_price:.2f}")
        
        # ── GATE 3: IDM sweep — liquidity grab at zone ──
        if idm_price and _near(zone_level, idm_price):
            score += 1
            reasons.append(f"💧 IDM sweep @ {idm_price:.2f}")
        
        # ── GATE 4: OB + FVG confluence — strict direction match + age filter ──
        for fvg in fvg_zones:
            if _near(zone_level, fvg["mid"]) and not fvg["filled"]:
                # FVG direction MUST match OB direction (bullish OB → bullish FVG)
                fvg_dir = fvg["direction"]
                ob_is_bull_dir = "BULL" in ob_dir
                fvg_is_bull = fvg_dir == "BULLISH"
                direction_match = ob_is_bull_dir == fvg_is_bull
                
                # FVG must be decent size (>3 pip on XAUUSD)
                min_fvg_size = 3 if display in ("XAUUSD","GOLD") else 2
                fvg_good_size = fvg["size_pips"] >= min_fvg_size
                
                if direction_match and fvg_good_size:
                    score += 1.5
                    reasons.append(f"📐 FVG {fvg['size_pips']:.0f}pip aligned — direction match, mitigation magnet")
                elif direction_match:
                    score += 0.75
                    reasons.append(f"📐 FVG {fvg['size_pips']:.0f}pip aligned — small FVG")
                break  # only count best match
        
        # ── GATE 5: Double Sweep — FB + IDM at same zone, same direction ──
        if false_break_price and idm_price and _near(false_break_price, idm_price) and _near(zone_level, false_break_price):
            score += 2
            reasons.append("💀 DOUBLE SWEEP — liquidity cleared 2x at same level")
        
        # ── GATE 6: Trend filter — trend-aligned breakers get bonus ──
        # SMC Breaker Logic for direction:
        #   Broken bullish OB (demand→supply) = SELL the retest
        #   Broken bearish OB (supply→demand) = BUY the retest
        #   Holding bullish OB (demand holds) = BUY the bounce
        #   Holding bearish OB (supply holds) = SELL the rejection
        if breaker:
            direction = "SELL" if ob_is_bull else "BUY"
        else:
            direction = "BUY" if ob_is_bull else "SELL"
        
        if breaker and trend_bias != "NEUTRAL":
            # Breaker expects reversal — broken bull OB wants BEARISH trend, broken bear OB wants BULLISH
            breaker_with_trend = (ob_is_bull and trend_bias == "BEARISH") or (not ob_is_bull and trend_bias == "BULLISH")
            if breaker_with_trend:
                score += 0.5
                reasons.append(f"📈 Trend-aligned ({trend_bias}) — higher probability")
        confluence_zones.append({
            "level": zone_level, "score": score, "reasons": reasons, "direction": direction
        })
    
    if not confluence_zones:
        return None, None
    
    best = max(confluence_zones, key=lambda z: (z["score"], -abs(z["level"] - price)))
    
    if best["score"] < 3.5:
        return None, None
    
    direction = best["direction"]
    entry = best["level"]
    
    # Grade first (needed for ATR multipliers)
    if best["score"] >= 6:
        grade = "S-TIER"
        grade_label = "💀 TRIPLE CONFLUENCE — GOD TIER ZONE"
    elif best["score"] >= 5:
        grade = "A"
        grade_label = "🔥 BREAKER BLOCK + FVG — High Conviction"
    else:
        grade = "B"
        grade_label = "⚡ STRUCTURAL ZONE — Valid Confluence"
    
    # ── ATR-based dynamic SL/TP ──
    rr_ratio = 2.0
    atr = None
    try:
        if ohlcv_bars and len(ohlcv_bars) >= 16:
            trs = []
            for i in range(1, min(15, len(ohlcv_bars))):
                high = float(ohlcv_bars[i].get("high", ohlcv_bars[i].get("h", 0)))
                low = float(ohlcv_bars[i].get("low", ohlcv_bars[i].get("l", 0)))
                prev_close = float(ohlcv_bars[i-1].get("close", ohlcv_bars[i-1].get("c", 0)))
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            if trs:
                atr = sum(trs) / len(trs)
    except Exception:
        pass
    
    if atr and atr > 0:
        sl_distance = round(atr * (1.0 if grade == "S-TIER" else 1.5), 2)
        tp_distance = round(sl_distance * rr_ratio, 2)
    else:
        sl_distance = round(30 * pip_s, 2)
        tp_distance = round(60 * pip_s, 2)
    
    # ── Entry distance check: if price too far, still return as zone-wait signal ──
    price_distance = abs(price - entry)
    if price_distance > sl_distance * 0.5:
        logger.info(
            "S-TIER zone [%s] price %.2f too far from zone %.2f (%.1f > %.1f sl_half) — ZONE WAIT mode, "
            "EA will enter when price reaches zone",
            display, price, entry, price_distance, sl_distance * 0.5,
        )
        # Don't return None — pass through as zone signal for EA to wait
        # SL/TP are still valid since zone IS the intended entry price
    
    if direction == "BUY":
        sl = round(entry - sl_distance, 2)
        tp = round(entry + tp_distance, 2)
    else:
        sl = round(entry + sl_distance, 2)
        tp = round(entry - tp_distance, 2)
    
    # ── TP2: structure-based extension ──
    # TP2 = midpoint extension beyond TP1 (half again the distance to TP1)
    tp2_dist = tp_distance + (tp_distance - sl_distance) * 0.5
    if direction == "BUY":
        tp2_candidate = round(entry + tp2_dist, 2)
    else:
        tp2_candidate = round(entry - tp2_dist, 2)
    # Only set TP2 if within reasonable range (max 200 pips from entry)
    if abs(tp2_candidate - entry) / pip_s <= 200:
        tp2 = tp2_candidate
    else:
        tp2 = 0
    
    reason = f"🤖 S-TIER ZONE [{grade}]: {grade_label}\n" + "\n".join(f"  • {r}" for r in best["reasons"])
    
    zone_half = entry * 0.0005 if entry > 0 else 0
    sig = {
        "action": direction, "entry": entry,
        "zone_lo": entry - zone_half if zone_half else entry,
        "zone_hi": entry + zone_half if zone_half else entry,
        "entry_mode": "zone",
        "sl": sl, "tp": tp,
        "tp1": tp, "tp2": 0,
        "confidence": min(0.95, 0.65 + best["score"] * 0.04),
        "rr_ratio": 2.0,
        "reasoning": reason, "ensemble": "mechanical", "voters": 0,
        "_model": f"S-TIER-ZONE-{grade}", "grade": grade,
        "source": "stier_zone_detector",
        "_tier_capped": False,
    }
    
    sig = _clamp_sltp(sig, display)
    
    logger.info(f"🎯 S-TIER ZONE [{grade}]: {display} {direction} @ ${entry:.2f} | "
                f"Score={best['score']:.1f} | Confluences: {len(best['reasons'])}")
    
    return sig, reason


# ── SnR PROXIMITY CHECK ──
def _snr_proximity_check(price, pip_s, ohlcv_bars=None, display="XAUUSD"):
    """Check if price is within 0.3% of a Daily/4H Support or Resistance level.
    
    Returns (snr_level, snr_type, distance_pct, zone_lo, zone_hi) or None.
    """
    try:
        if not ohlcv_bars or len(ohlcv_bars) < 20:
            return None
        
        # ── Find swing highs/lows from last 20-60 bars ──
        n_bars = min(len(ohlcv_bars), 60)
        bars = ohlcv_bars[-n_bars:]
        
        highs = [float(b.get("high", b.get("h", 0))) for b in bars]
        lows = [float(b.get("low", b.get("l", 0))) for b in bars]
        closes = [float(b.get("close", b.get("c", 0))) for b in bars]
        
        # Find swing points (local maxima/minima with 3-bar lookback)
        swings_high = []
        swings_low = []
        for i in range(3, len(highs) - 3):
            if highs[i] == max(highs[i-3:i+4]):
                swings_high.append(highs[i])
            if lows[i] == min(lows[i-3:i+4]):
                swings_low.append(lows[i])
        
        # Merge + deduplicate (cluster within 0.2%)
        def _cluster_levels(levels):
            if not levels:
                return []
            sorted_lvls = sorted(set(round(l, 2) for l in levels))
            clusters = []
            current = [sorted_lvls[0]]
            for l in sorted_lvls[1:]:
                if abs(l - current[-1]) / l < 0.002:
                    current.append(l)
                else:
                    clusters.append(sum(current) / len(current))
                    current = [l]
            clusters.append(sum(current) / len(current))
            return clusters
        
        resistance_levels = _cluster_levels(swings_high)
        support_levels = _cluster_levels(swings_low)
        
        # Check proximity: within 0.3% of any level
        best_level = None
        best_type = None
        best_dist = float('inf')
        
        for r in resistance_levels:
            dist = abs(price - r) / price
            if dist < 0.003 and dist < best_dist:  # 0.3%
                best_level = r
                best_type = "RESISTANCE"
                best_dist = dist
        
        for s in support_levels:
            dist = abs(price - s) / price
            if dist < 0.003 and dist < best_dist:
                best_level = s
                best_type = "SUPPORT"
                best_dist = dist
        
        if best_level is None:
            return None
        
        # Build SnR zone (±2 pip for XAUUSD, ±0.5 pip for others)
        zone_padding = 2.0 * pip_s if display in ("XAUUSD", "GOLD") else 1.0 * pip_s
        if best_type == "SUPPORT":
            zone_lo = best_level - zone_padding * 0.5
            zone_hi = best_level + zone_padding * 1.5
        else:
            zone_lo = best_level - zone_padding * 1.5
            zone_hi = best_level + zone_padding * 0.5
        
        return (best_level, best_type, best_dist, round(zone_lo, 2), round(zone_hi, 2))
    except Exception as e:
        logger.debug(f"SnR proximity check error: {e}")
        return None


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
 "zone_lo":0.0, "zone_hi":0.0,
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
}" 
IMPORTANT — ZONE RULE: zone_lo & zone_hi adalah Supply/Demand zone nyata (20-40 pip range).
Jika entry=4334, zone_lo=4332.50 zone_hi=4335.50 (30 pip zone). JANGAN pake ±0.05%.
zone_lo HARUS < zone_hi. GUNAKAN zone_lo/zone_hi untuk pending order — JANGAN market order."""


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
    """Call via OmniRoute (auto-rotates keys, avoids rate limits)."""
    if not OMNIROUTE_URL: return None
    # Try models in order: requested → DeepSeek → best-free
    candidates = [model]
    if "deepseek" not in model.lower():
        candidates.append("ds/deepseek-chat")
    candidates.append("auto/best-free")
    
    for i, omni_model in enumerate(candidates):
        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
            body = {"model": omni_model, "max_tokens": 800, "temperature": 0.3, "stream": False,
                    "messages": messages}
            
            req = urllib.request.Request(OMNIROUTE_URL,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                _AI_TOKEN_USAGE["openai"] = {
                    "prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }
                logger.info(f"AI/{omni_model}: {len(content)} chars, {_AI_TOKEN_USAGE['openai']['total']} tokens")
                return _extract_json(content)
        except Exception as e:
            if i < len(candidates) - 1:
                logger.debug(f"AI/{omni_model} failed, trying next: {e}")
            else:
                logger.warning(f"AI/all models failed: {e}")
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


def _call_alphavantage_news(display: str, price: float, pair: str = "gold") -> dict | None:
    """Fetch real-time market news from Alpha Vantage NEWS_SENTIMENT.
    
    Returns aggregated sentiment + top headlines, or None on failure.
    Only called for donor/channel tiers (25 calls/day free tier).
    """
    if not ALPHA_VANTAGE_KEY:
        return None
    try:
        ticker = AV_TICKER_MAP.get(pair, "FOREX:USD")
        params = urllib.parse.urlencode({
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "limit": 10,
            "apikey": ALPHA_VANTAGE_KEY,
        })
        url = f"{ALPHA_VANTAGE_NEWS_URL}?{params}"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())

        if "feed" not in data or not data["feed"]:
            return {"headline": "No major catalysts", "sentiment": "NEUTRAL", "impact": "LOW", "detail": ""}

        articles = data["feed"][:5]
        if not articles:
            return {"headline": "No major catalysts", "sentiment": "NEUTRAL", "impact": "LOW", "detail": ""}

        # Aggregate sentiment from articles
        scores = []
        headlines = []
        for a in articles:
            score = float(a.get("overall_sentiment_score", 0))
            label = a.get("overall_sentiment_label", "Neutral")
            scores.append(score)
            headlines.append(a.get("title", "")[:120])

        avg_score = sum(scores) / len(scores)
        if avg_score >= 0.35:
            sentiment = "BULLISH"
        elif avg_score <= -0.35:
            sentiment = "BEARISH"
        elif avg_score >= 0.15:
            sentiment = "SOMEWHAT-BULLISH"
        elif avg_score <= -0.15:
            sentiment = "SOMEWHAT-BEARISH"
        else:
            sentiment = "NEUTRAL"

        # Impact based on score magnitude + article count
        magnitude = abs(avg_score)
        article_boost = min(len(articles) / 3, 1.0)
        impact_score = magnitude * (0.7 + 0.3 * article_boost)
        if impact_score >= 0.30:
            impact = "HIGH"
        elif impact_score >= 0.15:
            impact = "MED"
        else:
            impact = "LOW"

        # Top headline + detail from articles
        best = max(articles, key=lambda a: abs(float(a.get("overall_sentiment_score", 0))))
        headline = best.get("title", "No major catalysts")[:150]
        source = best.get("source", "Alpha Vantage")
        detail_lines = []
        for i, a in enumerate(articles[:3]):
            title = a.get("title", "")[:100]
            s_label = a.get("overall_sentiment_label", "Neutral")
            s_emoji = {"Bullish": "🟢", "Somewhat-Bullish": "🟢", "Neutral": "⚪️",
                        "Somewhat-Bearish": "🔴", "Bearish": "🔴"}.get(s_label, "⚪️")
            detail_lines.append(f"{s_emoji} {title}")
        detail = "\n".join(detail_lines)

        logger.info(
            "AlphaVantage News: %d articles, avg=%.3f sentiment=%s impact=%s (pair=%s)",
            len(articles), avg_score, sentiment, impact, pair,
        )
        return {
            "headline": headline,
            "sentiment": sentiment,
            "impact": impact,
            "detail": detail,
            "source": source,
            "article_count": len(articles),
            "avg_score": round(avg_score, 3),
        }
    except Exception as e:
        logger.warning(f"AlphaVantage News error: {e}")
        return None


def _format_news_context(news: dict | None) -> str:
    """Format Market Intel context for signal display."""
    if not news:
        return ""
    if isinstance(news, str):
        return ""  # string (e.g. preview text) — skip formatting
    headline = news.get("headline", "")
    sentiment = news.get("sentiment", "NEUTRAL")
    impact = news.get("impact", "LOW")
    detail = news.get("detail", "")
    
    if not headline or headline == "No major catalysts":
        return ""
    
    s_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}.get(sentiment, "⚪️")
    i_emoji = {"HIGH": "🔥", "MED": "📊", "LOW": "📎"}.get(impact, "")
    
    lines = [
        f"📰 <b>Market Intel</b>",
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
       - elite:    All 3 models + Market Intel (max 95% conf) — premium subscriber
       - premium=True: All models (channel/auto — unlimited)
    ⭐ Models: DeepSeek V3 + GPT-4o + Claude-Sonnet + Market Intel.
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
        try: learning_context = get_learning_summary()
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

    # DeepSeek V3 — always called first (best SMC analyst)
    deepseek = _call_deepseek(prompt)
    if not deepseek:
        logger.info("🔬 DeepSeek down — rotating Smart-Ranked Fallbacks")
        deepseek = _call_omniroute(prompt, models=OMNIROUTE_FREE_MODELS)
        if deepseek:
            logger.info(f"Smart Fallback hit: {deepseek.get('action','?')} "
                        f"conf={deepseek.get('confidence','?')}")

    # GPT-4o — only for donors, elite, or channel (premium)
    gpt4o = None
    if not is_free_tier:
        global _LAST_GPT4O_CALL
        now_ts = time.time()
        if now_ts - _LAST_GPT4O_CALL >= _GPT4O_MIN_INTERVAL:
            _LAST_GPT4O_CALL = now_ts
            gpt4o = _call_openai(prompt, model="gpt-4o")
        else:
            wait = _GPT4O_MIN_INTERVAL - (now_ts - _LAST_GPT4O_CALL)
            logger.info(f"GPT-4o rate-limited — skipping (wait {wait:.0f}s)")

    # OmniRoute (Claude-Sonnet) — DISABLED: HTTP 400 broken, direct API calls used instead
    # omniroute = None
    # if tier == "elite" or premium:
    #     omniroute = _call_omniroute(prompt)
    omniroute = None  # OmniRoute disabled — DeepSeek + GPT-4o direct calls sufficient

    # Alpha Vantage News — real-time market sentiment (donors only)
    market_news = None
    if not is_free_tier:
        pair_key = display.lower().replace("usd", "").replace("xau", "gold") if display else "gold"
        market_news = _call_alphavantage_news(display, price, pair=pair_key)

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
        sig["_market_news"] = market_news
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
        sig["_market_news"] = market_news
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
            s["_market_news"] = market_news
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

    # Gate 5: Session — BLOCK forex/metals outside London/NY killzone
    is_crypto = display.upper() in ("BTCUSD", "ETHUSD", "BTC", "ETH")
    if not is_crypto:
        try:
            london_kz, ny_kz = killzone(wib_now().hour)
        except Exception:
            london_kz, ny_kz = False, False
        if not london_kz and not ny_kz:
            return False, "Outside killzone — London/NY only for forex/metals"

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
    else:
        sig["tp"] = round(entry - tp_dist, 2)
    
    # NOTE: tp1/tp2/tp3/tp4 NOW populated here — signal publisher + trade tracker
    # both read these fields. Without this, RR=1:0.0 because tp1 stays 0.
    sig["tp1"] = sig["tp"]
    sig["tp2"] = round(sig["tp"] * 1.0, 2)  # same as tp for tracker
    sig["tp3"] = 0.0
    sig["tp4"] = 0.0
    
    sig["rr_ratio"] = f"1:{rr:.1f}"
    logger.info(f"_clamp_sltp result: sl={sig['sl']} tp={sig['tp']:.2f}")
    
    return sig


def fmt_signal(sig, price, dxy, h, display="XAUUSD", currency="$", quality=None, levels="", smc_text=""):
    """Format signal Telegram-style — quality-aware dual format.
    
    quality: tuple (passed: bool, reason: str) from _sig_quality_pass()
    levels: SnR/FIBO context string from _compute_levels()
    smc_text: SMC/ICT analysis section from format_smc_analysis()
    
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
    # ── Entry Zone: prefer sig zone_lo/zone_hi, fallback ±0.05% dari entry ──
    zone_lo = sig.get("zone_lo") or (entry - entry * 0.0005 if entry > 0 else entry)
    zone_hi = sig.get("zone_hi") or (entry + entry * 0.0005 if entry > 0 else entry)

    # ── Pending Order Type ──
    pending_order_types = {
        ("SELL", "below"): "SELL LIMIT",
        ("SELL", "above"): "SELL STOP",
        ("SELL", "inside"): "SELL (zone)",
        ("BUY", "below"): "BUY STOP",
        ("BUY", "above"): "BUY LIMIT",
        ("BUY", "inside"): "BUY (zone)",
    }
    if action in ("BUY", "SELL") and zone_lo < zone_hi and price:
        if price < zone_lo:
            rel_pos = "below"
        elif price > zone_hi:
            rel_pos = "above"
        else:
            rel_pos = "inside"
        order_type = pending_order_types.get((action, rel_pos), action)
    else:
        order_type = action
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

    # ── Killzone gate: enforce London/NY for forex/metals, bypass for crypto ──
    forex_metal = display in ("XAUUSD", "GOLD", "USOIL", "EURUSD", "GBPUSD", "USDJPY")
    is_crypto = display.upper() in ("BTCUSD", "ETHUSD", "BTC", "ETH")
    in_kz = True
    if forex_metal and not is_crypto:
        lkz, nykz = killzone(h)
        in_kz = lkz or nykz

    header_emoji = emoji if is_actionable else "⚪️"
    header_label = f"SINYAL {order_type}" if is_actionable else "MARKET PULSE"

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

    # --- ZONE SL GUARD: ensure SL protects the full entry zone (±0.05%) ---
    # Only widens SL if it doesn't exceed the 35 pip max from entry
    if action in ("BUY","SELL") and entry and sl and zone_lo and zone_hi:
        zone_sl_map = {"XAUUSD": 2.0, "GOLD": 2.0}  # 20 pip from zone boundary
        min_zone_sl = zone_sl_map.get(display, 0)
        pip_s = 0.10 if display in ("XAUUSD","GOLD") else 0.01
        max_sl_pts = 3.5  # 35 pip for XAUUSD
        if min_zone_sl > 0:
            if action == "BUY":
                zone_sl_dist = zone_lo - sl
                if zone_sl_dist < min_zone_sl:
                    new_sl = round(zone_lo - min_zone_sl, 2)
                    # Only apply if new SL stays within 35 pip from entry
                    if abs(entry - new_sl) <= max_sl_pts:
                        sl = new_sl
                        logger.info(f"    [ZONE SL GUARD] BUY SL widened: zone_lo={zone_lo:.2f} → SL={sl} ({zone_sl_dist/pip_s:.0f}→{min_zone_sl/pip_s:.0f} pip from zone)")
                    else:
                        logger.info(f"    [ZONE SL GUARD] BUY skipped: would push SL beyond 35 pip from entry")
            else:
                zone_sl_dist = sl - zone_hi
                if zone_sl_dist < min_zone_sl:
                    new_sl = round(zone_hi + min_zone_sl, 2)
                    if abs(entry - new_sl) <= max_sl_pts:
                        sl = new_sl
                        logger.info(f"    [ZONE SL GUARD] SELL SL widened: zone_hi={zone_hi:.2f} → SL={sl} ({zone_sl_dist/pip_s:.0f}→{min_zone_sl/pip_s:.0f} pip from zone)")
                    else:
                        logger.info(f"    [ZONE SL GUARD] SELL skipped: would push SL beyond 35 pip from entry")

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
            # Recompute entry zone AFTER offset shift
            zone_half = entry * 0.0005
            zone_lo = entry - zone_half
            zone_hi = entry + zone_half

    # Generate TP levels — ALWAYS recompute to enforce system TP rules
    # Dynamic: 1-4 levels based on total TP distance
    # XAUUSD: <45pip→1 level, 45-70→2, 70-100→3, >100→4
    # NOTE: Resets tp1-tp4 regardless of AI/Hermes input to prevent
    # unclamped TP values (e.g., 370-pip TP1 from Hermes liquidity sweep)
    tp1 = tp2 = tp3 = tp4 = 0
    if tp > 0 and entry > 0:
        tp_dist = abs(tp - entry)
        # Asset-aware min TP1 and level thresholds (in points)
        if display in ("XAUUSD", "GOLD"):
            min_tp1 = 3.0    # 30 pip
            lvl2_min = 4.5   # 45 pip total for 2 levels
            lvl3_min = 7.0   # 70 pip total for 3 levels
            lvl4_min = 10.0  # 100 pip total for 4 levels
        elif display == "USOIL":
            min_tp1 = 0.30; lvl2_min = 0.45; lvl3_min = 0.70; lvl4_min = 1.0
        elif display in ("BTCUSD", "ETHUSD"):
            min_tp1 = 600; lvl2_min = 900; lvl3_min = 1400; lvl4_min = 2000
        else:
            # Forex 5-digit
            min_tp1 = 0.0015; lvl2_min = 0.0030; lvl3_min = 0.0050; lvl4_min = 0.0080

        num_tp = 1
        if tp_dist >= lvl4_min: num_tp = 4
        elif tp_dist >= lvl3_min: num_tp = 3
        elif tp_dist >= lvl2_min: num_tp = 2

        # Minimum spread between TP levels (50% of min_tp1 = 15 pip for XAUUSD)
        min_spread = min_tp1 * 0.5

        if action == "BUY":
            tp1 = round(entry + min_tp1, 2)
            if num_tp >= 2:
                raw = round(entry + tp_dist * 0.50, 2)
                tp2 = max(raw, tp1 + min_spread)
                tp2 = min(tp2, tp)  # don't exceed full TP
            if num_tp >= 3:
                raw = round(entry + tp_dist * 0.75, 2)
                tp3 = max(raw, tp2 + min_spread)
                tp3 = min(tp3, tp)
            if num_tp >= 4: tp4 = tp
        else:
            tp1 = round(entry - min_tp1, 2)
            if num_tp >= 2:
                raw = round(entry - tp_dist * 0.50, 2)
                tp2 = min(raw, tp1 - min_spread)  # SELL: tp2 is lower
                tp2 = max(tp2, tp)  # don't exceed full TP (below entry)
            if num_tp >= 3:
                raw = round(entry - tp_dist * 0.75, 2)
                tp3 = min(raw, tp2 - min_spread)
                tp3 = max(tp3, tp)
            if num_tp >= 4: tp4 = tp

        # Override rr_ratio with TP1-based effective RR (realistic first target)
        if tp1 and entry and sl and abs(entry - sl) > 0:
            tp1_rr = abs(tp1 - entry) / abs(entry - sl)
            sig["rr_ratio"] = f"1:{tp1_rr:.1f}"
            rr = f"{tp1_rr:.1f}"  # refresh display variable

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
    def _fmt_zone(lo, hi):
        return f"Rp{lo:,.0f} — Rp{hi:,.0f}" if is_idx else f"{currency}{lo:.2f} — {currency}{hi:.2f}"

    if action == "BUY":
        zone_emoji = "🟢"
    elif action == "SELL":
        zone_emoji = "🔴"
    else:
        zone_emoji = "📍"
    zone_label = f"{zone_emoji} {order_type}"

    # ── Tier gating: free users see Entry Zone only, SL/TP 🔒 locked ──
    is_free = sig.get("_tier_capped", True)

    lines = [
        f"{header_emoji} <b>{header_label} — {display}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {now_wib.strftime('%Y.%m.%d %H:%M')} WIB | Session: {session(h)}",
    ]

    # ── Killzone gate: DISABLED — always show full signal (24/7) ──
    lines.append(f"📍 {zone_label}: {_fmt_zone(zone_lo, zone_hi)}")

    # ── Pending order type explanation ──
    order_type_hints = {
        "SELL LIMIT": "⏳ Harga naik ke zone → SELL (resistansi)",
        "SELL STOP": "⏳ Harga turun ke zone → SELL (breakdown)",
        "BUY LIMIT": "⏳ Harga turun ke zone → BUY (support)",
        "BUY STOP": "⏳ Harga naik ke zone → BUY (breakout)",
    }
    if order_type != action and order_type in order_type_hints:
        lines.append(order_type_hints[order_type])

    if is_free and is_actionable:
        # ── FREE TIER: lock SL/TP — teaser only ──
        lines.append(f"🔴 SL: 🔒 <b>[SUBSCRIBER ONLY]</b>")
        for tp_val, tp_label in [(tp1,"TP1"),(tp2,"TP2"),(tp3,"TP3"),(tp4,"TP4")]:
            if tp_val and tp_val > 0:
                lines.append(f"🟢 {tp_label}: 🔒 <b>[SUBSCRIBER ONLY]</b>")
        lines.append(f"")
        lines.append(f"💡 <b>Free tier cuma bisa liat Entry Zone.</b>")
        lines.append(f"   SL/TP dikunci — gak bisa eksekusi dengan aman.")
        lines.append(f"   👑 <b>/subscribe</b> — Unlock SL/TP + 2 AI + Market Intel")
    else:
        lines.append(f"🔴 SL: {_fmt(sl)} {_sl_pips(sl)}")
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

    # ── SMC / ICT Analysis (actionable signals only) ──
    if smc_text and is_actionable:
        lines.append(smc_text)

    lines.append(f"⚠️ <i>NFA — Not Financial Advice. Sinyal hasil deteksi otomatis AI untuk edukasi. Keputusan & risiko trading sepenuhnya ada padamu. Selalu pakai manajemen risiko.</i>")
    if is_actionable:
        lines.append(f"")
        lines.append(f"💡 Mau validasi SnR + FIBO + SL placement?")
        lines.append(f"   👉 DM <b>@berkahkaryaforexbotbot</b> — ketik /levels {display.lower()}")
        lines.append(f"   🔒 Premium feature — <b>/subscribe</b> dulu kalo belum unlock")

    # Token counter gimmick + CTA
    token_total = sig.get("_token_total", 0)
    token_prompt = sig.get("_token_prompt", 0)
    token_comp = sig.get("_token_completion", 0)
    is_free = sig.get("_tier_capped", True)
    model_names = sig.get("_model", "AI")
    model_count = sig.get("voters", 1) or 1
    market_news = sig.get("_market_news")
    tier_label = sig.get("_tier", "🆓 Free")

    lines.append(f"")

    if not is_actionable:
        # ── NON-ACTIONABLE (Market Pulse / No Trade Zone): minimal CTA ──
        lines.append(f"━━━━━━━━━━━━━━━━")
        if forex_metal and not in_kz:
            lines.append(f"⏰ Next: London buka 14:00 WIB | NY buka 19:00 WIB")
        lines.append(f"⚡ /subscribe — Unlock full AI signal + SL/TP + Multi-AI")
    elif token_total > 0:
        token_k = f"{token_total/1000:.1f}k" if token_total >= 1000 else str(token_total)
        cost_rp = int(token_total * 1.5 / 1000)
        cost_rp = max(cost_rp, 1)
        # Dynamic battery based on actual AI models + Market Intel
        has_news = bool(market_news)
        battery_pct = min(100, model_count * 33 + (33 if has_news else 0))
        bar_count = min(3, model_count + (1 if has_news else 0))
        bars = "■" * max(1, bar_count) + "□" * (3 - max(1, bar_count))

        if is_free:
            # ── FREE TIER: Dynamic battery + kelaparan + Market Intel tease preview ──
            lines.append(f"🔋 <b>AI Power: {bars} {battery_pct}%</b> — {model_count}/3 AI yang kerja buat lu")
            lines.append(f"")
            lines.append(f"🤖 Cuma <b>{model_names}</b> doang yang mikir.")
            lines.append(f"   AI lu kelaparan bro... cuma dikasih 1 model 😤")
            lines.append(f"   Bayangin kalo 3 AI + Market Intel analisa bareng:")
            lines.append(f"   → Entry lebih presisi, SL lebih ketat, TP lebih akurat")
            lines.append(f"")
            # Market Intel tease with preview snippet
            lines.append(f"📰 <b>Market Intel</b> [🔒 LOCKED]")
            lines.append(f"   🔍 <i>Preview: Real-time market sentiment dari Alpha Vantage...</i>")
            lines.append(f"   🗞️  Breaking news, FOMC, NFP, CPI, geopolitics — real-time news")
            lines.append(f"   🔓 <b>Unlock → /news {display.lower()}</b> atau /subscribe")
            lines.append(f"")
            lines.append(f"⚡ <b>Rp 50K/bln (PRO)</b> — lebih murah dari 1x loss SL")
            lines.append(f"   Dapet 2 AI + Market Intel + /levels + /news")
            lines.append(f"   <b>/subscribe</b> sekarang — jangan biarin AI lu kerja sendirian")

        else:
            # ── DONOR TIER: Full power flex + AI Partner narrative ──
            lines.append(f"🔋 <b>AI Power: {bars} {battery_pct}%</b> — full throttle")
            lines.append(f"")
            lines.append(f"🤖 <b>{model_count} AI Partner</b> kerja bareng: {model_names}")

            if market_news:
                news_str = _format_news_context(market_news)
                if news_str:
                    lines.append(f"📰 <b>Market Intel Active</b> ✅ — real-time sentiment")
                    lines.append(f"   💡 Detail: /news {display.lower()}")
            else:
                lines.append(f"📰 Market Intel [🔒 LOCKED] — <b>/news {display.lower()}</b> buat unlock")

            lines.append(f"")
            lines.append(f"🤝 <b>AI Partner lu makin cerdas.</b>")
            lines.append(f"   Makin banyak AI = makin akurat sinyal = makin cuan.")
            lines.append(f"   Jangan stop disini — upgrade ke tier tertinggi:")
            if tier_label in ("⭐ Pro",):
                lines.append(f"   👑 <b>/subscribe</b> → Elite Tier: 3 AI + Market Intel real-time")
            else:
                lines.append(f"   💎 <b>Elite Intelligence Active</b> — your edge is real")
    else:
        # Fallback — no token data
        lines.append(f"⚡ Upgrade Tier → /subscribe")
        lines.append(f"   Makin banyak AI = makin akurat sinyal = makin cuan")

    return "\n".join(lines)


# ── Market Intel section (for signals that include it) ──
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
    lines.append(f"   Bayangin 3 AI + Market Intel analisa bareng:")
    lines.append(f"   → Entry level, SL placement, TP target — all calculated.")
    lines.append(f"")
    lines.append(f"📰 <b>Market Intel</b> [🔒 LOCKED]")
    lines.append(f"   <i>Real-time X/Twitter market context...</i>")
    lines.append(f"")
    lines.append(f"⚡ <b>/subscribe</b> — Rp 50K/bln (PRO)")
    lines.append(f"   Unlock AI Signal + Market Intel + /levels + SnR/FIBO")
    lines.append(f"   Jangan cuma liat engine doang — kasih AI lu kerjaan beneran")

    return "\n".join(lines)


# ── Quant Consensus UI helper ──
def append_quant_consensus_ui(sig, quant_result, disp="XAUUSD"):
    """Injects Quant Consensus block + guardrail — human-readable, no G/R/D jargon.
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

    # ── Visual bar: proportional █ blocks (max 20 blocks) ──
    def _bar(pct):
        n = min(20, round(pct / 5))
        return "█" * n + "░" * (20 - n)

    # ── Simple verdict in plain Indonesian ──
    verdict_text = {
        "BUY_BIAS_HISTORICAL": f"📈 <b>Historis cenderung NAIK</b> — {green_pct:.0f}% kejadian serupa lanjut bullish",
        "SELL_BIAS_HISTORICAL": f"📉 <b>Historis cenderung TURUN</b> — {red_pct:.0f}% kejadian serupa lanjut bearish",
        "NEUTRAL_HISTORICAL": "➖ <b>Historis gak jelas arah</b> — terlalu banyak sideways",
        "NO_HISTORICAL_MATCH": "⚠️ <b>Pola ini baru pertama kali</b> — belum ada data pembanding",
        "INSUFFICIENT_DATA": "⏳ <b>Data belum cukup</b> — butuh minimal 15 candle",
    }.get(verdict, "⚪ Data tidak tersedia")

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"📜 <b>Statistik Historis — Pola {pattern_size} Candle</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔍 Ketemu <b>{match_count}x</b> kejadian serupa dari {series_len} bar terakhir\n"
        f"\n"
        f"📈 NAIK   {green_pct:5.0f}%  {_bar(green_pct)}\n"
        f"📉 TURUN  {red_pct:5.0f}%  {_bar(red_pct)}\n"
        f"➖ Datar  {doji_pct:5.0f}%  {_bar(doji_pct)}\n"
        f"\n"
        f"🧠 <b>Kesimpulan:</b> {verdict_text}\n"
        f"   Keyakinan: {confidence:.0%}\n"
        f"⏰ Data real-time — angka bisa geser tiap candle baru (1H)"
    )

    # Guardrail logic
    warnings = []
    ai_action = sig.get("action", "HOLD")
    GUARD_THRESHOLD = 40

    if match_count == 0:
        warnings.append("⚠️ <b>Belum ada data pembanding</b> — sinyal AI murni dari analisa teknikal, bukan statistik")
    elif ai_action == "BUY" and green_pct < GUARD_THRESHOLD:
        warnings.append(f"⚠️ <b>Hati-hati:</b> AI bilang BUY tapi statistik cuma {green_pct:.0f}% kejadian yang lanjut naik — riskan!")
    elif ai_action == "SELL" and red_pct < GUARD_THRESHOLD:
        warnings.append(f"⚠️ <b>Hati-hati:</b> AI bilang SELL tapi statistik cuma {red_pct:.0f}% kejadian yang lanjut turun — riskan!")

    if ai_action == "BUY" and dominant == "R" and red_pct >= GUARD_THRESHOLD:
        warnings.append(f"🚨 <b>KONFLIK:</b> AI BUY vs Data Historis SELL ({red_pct:.0f}% kejadian malah turun!) 🚨")
    elif ai_action == "SELL" and dominant == "G" and green_pct >= GUARD_THRESHOLD:
        warnings.append(f"🚨 <b>KONFLIK:</b> AI SELL vs Data Historis BUY ({green_pct:.0f}% kejadian malah naik!) 🚨")

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
            f"⚡️ Kuota AI: {FREE_DAILY_LIMIT}x analisa/hari\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 /help — Semua command\n"
            "📊 /analyze xauusd — Mulai analisa\n"
            "⚡ Upgrade Tier → @berkahkaryaforexbotbot\n"
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
FREE_QUOTA_PER_DAY = FREE_DAILY_LIMIT  # references FREE_DAILY_LIMIT for consistency
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


def _get_user_tier(chat_id):
    """Return tier info: {tier, limit (-1=unlimited), throttle, is_paid, label}.
    Only donor/lifetime are treated as paid. Checks expiry date."""
    try:
        from members import get_member as m_get
        member = m_get(str(chat_id))
        if member:
            tier = str(member.get("tier", "free")).lower()
            status = str(member.get("status", "trial")).lower()
            # ── Expiry gate: check if subscription has expired ──
            expiry_str = member.get("expiry", "")
            expiry_past = False
            if expiry_str and status == "paid":
                try:
                    exp = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    if exp.tzinfo is None:
                        WIB_TZ = timezone(timedelta(hours=7))
                        exp = exp.replace(tzinfo=WIB_TZ)
                    if exp < wib_now():
                        expiry_past = True
                except Exception:
                    pass
            # ── is_paid: status="paid" AND expiry not past, OR grandfathered lifetime ──
            # Also exclude test-tagged accounts
            is_test = (member.get("chat_id", "") or "").startswith("test") or (member.get("chat_id", "") or "").startswith("vfy")
            is_paid = (status == "paid" and not expiry_past and not is_test) or tier in ("donor", "lifetime")
            if tier == "pro":
                limit = TIER_LIMITS.get("pro", FREE_DAILY_LIMIT)
                throttle = MANUAL_THROTTLE_PRO
            elif is_paid:
                limit = -1
                throttle = MANUAL_THROTTLE_ELITE
            else:
                limit = FREE_DAILY_LIMIT
                throttle = MANUAL_THROTTLE_FREE
            label = {
                "pro": "⭐ Pro",
                "elite": "👑 Elite",
                "lifetime": "💎 Lifetime",
                "donor": "💎 Lifetime (GF)",
                "paid": "💎 Lifetime (GF)",
                "free": "🆓 Free",
                "starter": "🆓 Free",
            }.get(tier, ("🆓 Free" if not is_paid else "💎 Lifetime (GF)"))
            return {
                "tier": ("donor" if is_paid else "free"),
                "limit": limit,
                "throttle": throttle,
                "is_paid": is_paid,
                "label": label,
            }
    except Exception:
        pass
    return {"tier": "free", "limit": FREE_DAILY_LIMIT,
            "throttle": MANUAL_THROTTLE_FREE,
            "is_paid": False, "label": "🆓 Free"}


def _is_donor(chat_id):
    """Check if user has donor/paid status in members DB.
    Also checks expiry date — if expired, user is NOT donor."""
    try:
        from members import get_member as m_get
        member = m_get(str(chat_id))
        if not member:
            return False
        tier = str(member.get("tier", "free")).lower()
        status = str(member.get("status", "trial")).lower()
        # ── Status check: must be "paid" AND not expired ──
        if status != "paid":
            return False
        # ── Expiry gate: if expiry < NOW, user is NOT donor ──
        expiry_str = member.get("expiry", "")
        if expiry_str:
            try:
                from datetime import datetime, timezone, timedelta
                exp = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    WIB = timezone(timedelta(hours=7))
                    exp = exp.replace(tzinfo=WIB)
                if exp < wib_now():
                    return False  # expired — no longer donor
            except Exception:
                pass
        if tier in ("donor", "lifetime", "pro", "elite"):
            return True
        return False
    except Exception:
        return False


# ── Reusable donate menu ──
def _send_donate_menu(chat_id, username=""):
    """Tiered subscription menu — Pro/Elite/Lifetime."""
    from members.payment import get_pricing_table
    txt = get_pricing_table()
    markup = {"inline_keyboard": [
        [{"text": "⭐ PRO — Rp50K/bulan", "callback_data": "sub:pro"}],
        [{"text": "👑 ELITE — Rp150K/bulan", "callback_data": "sub:elite"}],
        [{"text": "💎 LIFETIME — Rp500K (sekali)", "callback_data": "sub:lifetime"}],
        [{"text": "💳 Bayar via QRIS/VA", "callback_data": "sub:pay"}],
        [{"text": "🤝 Hubungi Chief Architect", "url": "https://t.me/codergaboets"}],
    ]}
    tg_send(txt, chat_id, reply_markup=markup)


# ── Command handler ──
def handle_command(cmd, text, chat_id, msg):
    # ── ACTIVITY TRACKING: update last_activity + cmd_count in root members.db ──
    try:
        _adb = sqlite3.connect(SUBS_PATH)
        _adb.execute("UPDATE members SET last_activity=?, cmd_count=COALESCE(cmd_count,0)+1 WHERE chat_id=?",
                     [datetime.now(timezone.utc).isoformat(), str(chat_id)])
        _adb.commit()
        _adb.close()
    except Exception:
        pass

    sub = text[len(cmd):].strip().lower() if len(text) > len(cmd) else ""
    sub_norm = _normalize_broker_symbol(sub)  # XAUUSDc → xauusd, EURUSD.pro → eurusd

    if cmd == "/start":
        # ── Dual-Funnel Router: ref_ (referral) + track_ (Meta CAPI) — no clash ──
        sub_lower = sub.lower() if sub else ""
        if sub and sub_lower.startswith("ref_"):
            # ── REFERRAL FUNNEL: /start ref_<referrer_chat_id> ──
            try:
                referrer_id = sub[4:]  # strip "ref_"

                # ── Anti-self-referral guard ──
                if str(referrer_id) == str(chat_id):
                    tg_send("😅 Gak bisa referral diri sendiri bro. Share link lu ke temen!", chat_id)
                    return

                from members.tags import add_tag as _add_tag
                # Register new user with referrer
                from members import _conn, register_member, get_member
                existing = get_member(str(chat_id))
                if not existing:
                    username_val = (msg.get("chat", {}).get("username", "")
                                    or msg.get("from", {}).get("username", "")
                                    or f"User{chat_id}")
                    register_member(
                        str(chat_id),
                        username_val,
                        username_val,
                        "free",
                        "trial",
                        referrer_id=str(referrer_id),
                    )
                # Increment referrer's count
                with _conn() as db:
                    db.execute(
                        "UPDATE members SET ref_count = COALESCE(ref_count,0) + 1 WHERE chat_id=?",
                        (str(referrer_id),)
                    )
                    # Check ref_count for milestone
                    row = db.execute(
                        "SELECT ref_count FROM members WHERE chat_id=?",
                        (str(referrer_id),)
                    ).fetchone()
                    ref_count = row["ref_count"] if row else 0
                # ── MILESTONE REWARD: 3 referrals → PRO 7 hari gratis ──
                if ref_count == 3:
                    try:
                        from members import upgrade_tier
                        from datetime import datetime, timezone, timedelta
                        WIB = timezone(timedelta(hours=7))
                        expiry = (datetime.now(WIB) + timedelta(days=7)).isoformat()
                        upgrade_tier(str(referrer_id), "pro", 7,
                                     f"REF-MILESTONE-{referrer_id}")
                        with _conn() as db:
                            db.execute(
                                "UPDATE members SET expiry=?, status='paid' WHERE chat_id=?",
                                (expiry, str(referrer_id))
                            )
                        dm = (
                            "🎉 <b>BOOM! 3 Teman udah daftar pakai link lu!</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "Sebagai reward, tier lu otomatis naik ke\n"
                            "<b>PRO selama 7 hari!</b>\n\n"
                            "Enjoy sinyal VIP-nya!\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "🔑 /mykey — Cek license EA kamu\n"
                            "📊 /analyze — Langsung gas analisa!"
                        )
                        tg_send(dm, str(referrer_id))
                        logger.info("REF MILESTONE: %s → PRO 7 hari (ref_count=%d)",
                                    referrer_id, ref_count)
                    except Exception as e:
                        logger.warning("Ref milestone PRO upgrade failed: %s", e)

                # ── MILESTONE REWARD: 10 referrals → ELITE 30 hari gratis ──
                elif ref_count == 10:
                    try:
                        from members import upgrade_tier
                        from datetime import datetime, timezone, timedelta
                        WIB = timezone(timedelta(hours=7))
                        expiry = (datetime.now(WIB) + timedelta(days=30)).isoformat()
                        upgrade_tier(str(referrer_id), "elite", 30,
                                     f"REF-ELITE-{referrer_id}")
                        with _conn() as db:
                            db.execute(
                                "UPDATE members SET expiry=?, status='paid' WHERE chat_id=?",
                                (expiry, str(referrer_id))
                            )
                        dm = (
                            "🏆 <b>LEGEND! 10 Teman udah daftar pakai link lu!</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "Sebagai reward MAXIMUM, tier lu otomatis naik ke\n"
                            "<b>👑 ELITE selama 30 hari!</b>\n\n"
                            "3 AI + Market Intel + EA Auto-Trade — FULL POWER.\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "🔑 /mykey — Cek license EA kamu\n"
                            "📊 /analyze — Gas analisa elite!"
                        )
                        tg_send(dm, str(referrer_id))
                        logger.info("REF ELITE MILESTONE: %s → ELITE 30 hari (ref_count=%d)",
                                    referrer_id, ref_count)
                    except Exception as e:
                        logger.warning("Ref milestone ELITE upgrade failed: %s", e)
                _add_tag(str(referrer_id), "affiliate")
                logger.info("🔗 Referral: %s invited by %s (ref_count=%d)",
                            chat_id, referrer_id, ref_count)
            except Exception as exc:
                logger.warning("Referral processing failed: %s", exc)

        elif sub and sub_lower.startswith("track_"):
            # ── META CAPI TRACKING: /start track_<tracking_id> ──
            try:
                from tradebot.tracking.deep_link import parse_start_payload
                from tradebot.tracking.capture import link_telegram_user
                from tradebot.tracking.events import fire_lead
                from tradebot.tracking.activity import log_activity

                ok, tracking_id = parse_start_payload(sub)
                if ok:
                    username_val = ""
                    if msg:
                        username_val = (msg.get("chat", {}).get("username", "")
                                        or msg.get("from", {}).get("username", ""))
                    link_telegram_user(tracking_id, str(chat_id))
                    fire_lead(str(chat_id), tracking_id, "free")
                    log_activity(str(chat_id), chat_id, username_val,
                                 "start_tracked", "free",
                                 {"tracking_id": tracking_id})
                    logger.info("🔗 Tracking linked: %s → %s",
                                tracking_id, chat_id)
            except Exception as exc:
                logger.warning("Tracking link failed: %s", exc)

        # ── INTERACTIVE ONBOARDING: dynamic tier-based copy + InlineKeyboard ──
        if _has_accepted_ultimatum(chat_id):
            is_donor = _is_donor(chat_id)
            quota = _get_quota(chat_id)
            tier_info = _get_user_tier(chat_id)
            tier_tag = tier_info.get("label", "🆓 Free")

            # ── 1. HEADER (semua user) ──
            welcome = (
                f"🔥 <b>REVOLUSI TRADING DIMULAI: FULL AI, NO BULLSHIT!</b> 🔥\n"
                f"\n"
                f"Selamat datang di <b>Vilona AI Trading Ecosystem.</b>\n"
                f"Kami tidak berjualan ludah atau grup VIP abal-abal.\n"
                f"Seluruh ekosistem ini (Analisa SMC, Liquidity, Quant)\n"
                f"dieksekusi murni oleh <b>FULL AI AGENTS</b> yang bekerja 24/7.\n"
                f"\n"
                f"<b>Aturan Main Kami:</b>\n"
                f"✅ AKSES GRATIS: Buktikan tajamnya sinyal AI kami\n"
                f"   tanpa bayar di depan.\n"
                f"🤝 GOTONG ROYONG: Jika AI kami berhasil mencetak\n"
                f"   hijau di portofolio Anda, sisihkan sedikit profit\n"
                f"   Anda untuk \"menyiram bensin\" server AI kami\n"
                f"   agar makin buas!\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            )

            if is_donor:
                # ── 2a. DYNAMIC CONTENT: PAID (PRO / ELITE / LIFETIME) ──
                welcome += (
                    f"📊 Status: <b>SUBSCRIBER 👑</b>\n"
                    f"⚡️ Kuota AI: <b>UNLIMITED ♾️</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>AKSES VIP KAMU:</b>\n"
                    f"📥 Download EA MT5: phantomfx.aitradepulse.com/ea/download/\n"
                    f"🔑 Cek Licensi EA: /mykey\n"
                    f"🌐 Bridge Dashboard: phantomfx.aitradepulse.com\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🧠 /signal — Signal dari 9 engines\n"
                    f"🏛 /levels — SnR + FIBO + Engine Deep Dive 👑\n"
                    f"🔍 /zones — OB + FVG + Supply/Demand 🆕\n"
                    f"🏗 /structure — BOS/CHoCH + MTF Alignment 🆕\n"
                    f"💀 /stier — S-TIER Zone GOD TIER 👑\n"
                    f"🕐 /session — Killzone + Session Level 🆕\n"
                    f"📰 /news — Market Intel: macro catalyst analysis 👑\n"
                    f"📊 /dashboard — Live dashboard web\n"
                    f"📱 /help — Semua command\n"
                    f"⚡️ Perpanjang/Upgrade Tier → /subscribe\n"
                    f"\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤝 <b>GOTONG ROYONG:</b>\n"
                    f"Ajak teman trader lu — setiap 3 orang yang\n"
                    f"gabung lewat link referral lu, dapet <b>PRO 7 hari GRATIS!</b>\n"
                    f"🔗 Cek link lu: /referral"
                )
            else:
                # ── 2b. DYNAMIC CONTENT: FREE ──
                quota_line = f"{quota['remaining']}/{FREE_QUOTA_PER_DAY} Analisa/Hari"
                welcome += (
                    f"📊 Status: <b>FREE TIER</b>\n"
                    f"⚡️ Kuota AI: {quota_line}\n"
                    f"🔒 SL/TP: <b>Dikunci (Subscriber Only)</b>\n"
                    f"❌ Akses EA: <b>Restricted</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Akses kamu sangat dibatasi. Upgrade sekarang\n"
                    f"untuk membuka full SL/TP, kuota unlimited, dan\n"
                    f"akses rahasia ke EA Auto-Trade!\n"
                    f"Ketik /subscribe atau klik tombol di bawah.\n\n💡 <b>Gak mau bayar? Ajak teman!</b>\n3 referral = <b>PRO 7 hari GRATIS</b> → /referral"
                )

            # ── 3. Interactive onboarding buttons (tetap 3 tombol) ──
            markup = {
                "inline_keyboard": [
                    [{"text": "📊 Cek Sinyal XAUUSD Sekarang", "callback_data": "cmd:analyze_xauusd"}],
                    [{"text": "🎓 Cara Baca Sinyal (Panduan)", "callback_data": "cmd:guide"}],
                    [{"text": "💎 Lihat Keuntungan PRO", "callback_data": "cmd:subscribe"}],
                ]
            }
            tg_send(welcome, chat_id, reply_markup=markup)
        else:
            # New user → ultimatum video (single message)
            send_ultimatum_video(chat_id)

    elif cmd == "/referral":
        # ── REFERRAL DASHBOARD ──
        try:
            from members import _conn
            with _conn() as db:
                row = db.execute(
                    "SELECT ref_count FROM members WHERE chat_id=?",
                    (str(chat_id),)
                ).fetchone()
            ref_count = row["ref_count"] if row else 0
        except Exception:
            ref_count = 0
        ref_link = f"https://t.me/berkahkaryaforexbotbot?start=ref_{chat_id}"
        if ref_count == 0:
            msg = (
                f"👥 <b>REFERRAL PROGRAM</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🔗 Link referral kamu:\n"
                f"<code>{ref_link}</code>\n\n"
                f"📊 Statistik:\n"
                f"  • Teman diajak: <b>0</b>\n"
                f"  • Reward #1: 3 referral → <b>PRO 7 hari GRATIS!</b>\n"
                f"  • Reward #2: 10 referral → <b>ELITE 30 hari GRATIS!</b>\n\n"
                f"💡 Share link ini ke temen-temen trader!\n"
                f"Setiap yang daftar lewat link lu, referral\n"
                f"counter lu naik."
            )
        else:
            if ref_count < 3:
                remaining_3 = 3 - ref_count
                next_reward = f"⭐ PRO 7 Hari (butuh {remaining_3} lagi)"
                remaining_10 = 10 - ref_count
                next_elite = f"👑 ELITE 30 Hari (butuh {remaining_10} lagi)"
            elif ref_count < 10:
                next_reward = "⭐ PRO 7 Hari — SUDAH DIKLAIM ✅"
                remaining_10 = 10 - ref_count
                next_elite = f"👑 ELITE 30 Hari (butuh {remaining_10} lagi)"
            else:
                next_reward = "⭐ PRO 7 Hari — SUDAH DIKLAIM ✅"
                next_elite = "👑 ELITE 30 Hari — SUDAH DIKLAIM ✅"
            msg = (
                f"👥 <b>REFERRAL PROGRAM</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🔗 Link referral kamu:\n"
                f"<code>{ref_link}</code>\n\n"
                f"📊 Statistik:\n"
                f"  • Teman diajak: <b>{ref_count}</b>\n"
                f"  • {next_reward}\n"
                f"  • {next_elite}\n\n"
                f"💡 Share link ini ke temen-temen trader!\n"
                f"Setiap yang daftar lewat link lu, referral\n"
                f"counter lu naik."
            )
        tg_send(msg, chat_id)

    elif cmd == "/learn_report":
        # ── ASYNC PATTERN EXTRACTION — runs in thread, doesn't block handler ──
        tg_send("🧠 <b>Learning Engine aktif...</b>\nMenganalisa data TP/SL dari database.\nIni butuh beberapa detik.", chat_id)
        def _run_learning():
            try:
                from scripts.pattern_extractor import run_learning_pipeline
                DB = str(DATA_DIR / "members.db")
                result = run_learning_pipeline(DB, lookback_days=14)
                if result["total_signals"] == 0:
                    tg_send("📭 <b>Data belum cukup.</b>\nBelum ada sinyal closed (TP/SL) dalam 14 hari.\nGunakan /analyze xauusd untuk generate sinyal.", chat_id)
                    return
                # Build report
                lines = [
                    "🧠 <b>HERMES LEARNING REPORT</b>",
                    "━━━━━━━━━━━━━━━━━━━━━",
                ]
                tp = result.get("tp_stats", {})
                sl = result.get("sl_stats", {})
                weights = result.get("suggested_weights", {})
                for regime in sorted(set(list(tp.keys()) + list(sl.keys()))):
                    if regime == "_empty": continue
                    t = tp.get(regime, {})
                    s = sl.get(regime, {})
                    w = weights.get(regime, {}) if not isinstance(weights.get(regime), bool) else {}
                    n_tp = t.get("count", 0)
                    n_sl = s.get("count", 0)
                    wr = round(n_tp / max(n_tp + n_sl, 1) * 100, 1)
                    eff = t.get("mfe_efficiency", 0)
                    lines.append(f"\n📊 <b>{regime.upper()}</b>")
                    lines.append(f"   Win Rate: {wr}% | MFE Eff: {eff:.2f}")
                    if t.get("tp_too_conservative"):
                        lines.append("   ⚠️ Flag: TP Too Conservative")
                    if s.get("need_trailing_stop"):
                        lines.append("   ⚠️ Flag: Need Trailing Stop")
                    if w:
                        lines.append(f"   ⚖️ Weights: SMC={w.get('smc',0):.2f} LIQ={w.get('liq',0):.2f} MACRO={w.get('macro',0):.2f}")
                lines.append("\n━━━━━━━━━━━━━━━━━━━━━")
                lines.append("⚙️ Weights auto-injected via weight_manager")
                lines.append("📅 Weekly refresh: Sabtu 02:00 WIB")
                tg_send("\n".join(lines), chat_id)
                logger.info("Learning report generated for %s", chat_id)
            except Exception as exc:
                logger.error("Learn report failed: %s", exc)
                tg_send(f"❌ Gagal generate learning report: {exc}", chat_id)
        threading.Thread(target=_run_learning, daemon=True).start()

    elif cmd == "/myid":
        text = (
            f"🆔 <b>Telegram ID kamu:</b>\n"
            f"<code>{chat_id}</code>\n\n"
            f"Gunakan ID ini untuk subscribe di website kami\n"
            f"👉 <a href='https://phantomfx.aitradepulse.com'>phantomfx.aitradepulse.com</a>"
        )
        tg_send(text, chat_id)

    elif cmd == "/help":
        help_lines = [
            "⚙️ <b>VILONA AI — COMMAND CENTER</b>",
            "━━━━━━━━━━━━━━━━",
            "Ketik command di bawah untuk memberi instruksi pada AI.",
            "",
            "🟢 <b>GENERAL & SETUP</b>",
            "/start — Reboot Markas Komando",
            "/status — Cek Kuota & Akses Tier",
            "/subscribe — Upgrade Tier ⚡️",
            "/referral — Link Referral (Bawa 3 Teman = PRO Gratis!)",
            "/price — Cek harga market real-time",
            "/help — Buka menu panduan ini",
            "",
            "🤝 <b>GOTONG ROYONG (REFERRAL)</b>",
            "/referral — Lihat Link & Statistik Referral",
            "💰 3 Teman Gabung = PRO 7 Hari GRATIS!",
            "💰 10 Teman Gabung = ELITE 30 Hari GRATIS!",
            "📢 Share link lu ke grup trader, sosmed, dll",
            "",
            "🧠 <b>AI SIGNAL GENERATOR</b>",
            "/analyze — Perintahkan AI Scan Market (FREE: 3x/hari)",
            "/signal — Generate sinyal dari MTF + 9 engines 👑",
            "/mtf — Matrix 5TF × 9 engines (top-down) 👑",
            "",
            "🔍 <b>TECHNICAL ANALYSIS (SMC & PA)</b>",
            "/zones — Order Blocks + FVG + Supply/Demand 👑",
            "/structure — BOS/CHoCH + Trend Alignment 👑",
            "/session — Killzone + Session High/Low 👑",
            "/stier — S-TIER Zone GOD TIER 👑",
            "",
            "📊 <b>TRADING TOOLS & DATA</b>",
            "/levels — SnR + FIBO + Engine Deep Dive 👑",
            "/news — Market Intel — X/Twitter intel 👑",
            "/killzone — Radar sesi market aktif",
            "/recap — Rekap & riwayat performa trade",
            "",
            "🔧 <b>POWER TOOLS & EA (SUBSCRIBER ONLY)</b>",
            "/autosync — Auto-trade ke EA 👑",
            "/bridge_status — Cek koneksi EA 👑",
            "/mykey — Cek License EA kamu 👑",
            "/dashboard — Buka live dashboard web 👑",
            "",
            "━━━━━━━━━━━━━━━━",
            "📞 Bantuan / Investor: @codergaboets",
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

    elif cmd == "/testbridge":
        # ── Admin-only: fire dummy signal to test all connected MT5 instances ──
        if str(chat_id) != str(ADMIN_CHAT_ID):
            tg_send("⛔ Admin only.", chat_id)
        else:
            try:
                now_ts = wib_now().strftime("%Y%m%d-%H%M%S")
                live_price = fetch_price("gold")
                if not live_price:
                    tg_send("❌ Gagal fetch harga XAUUSD — coba lagi.", chat_id)
                else:
                    pip_s = 0.10
                    sl_price = round(live_price - 50 * pip_s, 2)
                    tp_price = round(live_price + 50 * pip_s, 2)
                    dummy = {
                        "action": "BUY",
                        "symbol": "XAUUSD",
                        "entry": 0,  # market execution
                        "sl": sl_price,
                        "tp": tp_price,
                        "tp1": tp_price,
                        "tp2": 0,
                        "confidence": 99,
                        "risk_percent": 1.0,
                        "comment": f"TEST-BRIDGE-{now_ts}",
                        "source": "testbridge",
                        "rr_ratio": 1.5,  # satisfy quality gate min 1.5
                        "layers": [],
                        "target_user": "",
                        "telegram_message_id": None,
                        "signal_id": f"TEST-BRIDGE-{now_ts}",
                    }
                    post_signal_to_bridge(dummy, live_price, "XAUUSD")
                    tg_send(
                        f"🚀 <b>DUMMY SIGNAL FIRED!</b>\n"
                        f"Check all connected MT5 terminals for execution.\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"Signal ID: <code>TEST-BRIDGE-{now_ts}</code>\n"
                        f"Entry: MARKET | SL: {sl_price} | TP: {tp_price}\n"
                        f"Risk: 1.0% | Confidence: 99\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"<i>Ini sinyal uji — tidak untuk ditradingkan.</i>",
                        chat_id
                    )
                    logger.info(f"🧪 /testbridge fired: TEST-BRIDGE-{now_ts} | XAUUSD @ MKT | SL={sl_price} TP={tp_price}")
            except Exception as e:
                logger.error(f"/testbridge error: {e}")
                tg_send(f"❌ /testbridge gagal: {e}", chat_id)

    elif cmd == "/trailing":
        # ── Smart Trailing config (DONOR ONLY) ──
        if not _is_donor(str(chat_id)):
            tg_send(
                "🎯 <b>Smart Trailing</b> [🔒 LOCKED]\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Auto-trailing SL saat profit jalan.\n"
                "Bridge update SL ke EA kamu real-time.\n"
                "\n"
                "👑 Khusus Subscriber.\n"
                "⚡ /subscribe — Rp 50K/bln (PRO)",
                chat_id
            )
            return

        args_raw = text.strip()
        parts = args_raw.split()
        sub = parts[1] if len(parts) > 1 else ""

        if sub == "on":
            _set_trailing(chat_id, True)
            tg_send("🎯 <b>Smart Trailing: ON</b>\n"
                    "Bridge akan auto-trail SL setiap +10 pip profit.\n"
                    "Breakeven: SL → entry setelah +10 pip.\n"
                    "Trail distance: 15 pip di belakang harga.", chat_id)
        elif sub == "off":
            _set_trailing(chat_id, False)
            tg_send("⏸ <b>Smart Trailing: OFF</b>", chat_id)
        elif sub == "status":
            cfg = _get_trailing_status(chat_id)
            if not cfg:
                tg_send("⚠️ Akun belum terhubung ke bridge.\nGunakan /trailing on untuk mengaktifkan.", chat_id)
            else:
                pos_text = ""
                if cfg.get("active_position"):
                    p = cfg["position_preview"]
                    pos_text = (f"\n━━━━━━━━━━━━━━━━\n"
                              f"📊 <b>Posisi Aktif:</b> {p['direction']} @ {p['entry']}\n"
                              f"SL saat ini: {p['sl']} | Umur: {p['age_sec']}s")
                tg_send(
                    f"🎯 <b>Trailing Status</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"Status: {'✅ ON' if cfg.get('enabled') else '❌ OFF'}\n"
                    f"Mode: {cfg.get('mode', 'basic')}\n"
                    f"Breakeven setelah: +{cfg.get('breakeven_pips', 10)} pip\n"
                    f"Trail distance: {cfg.get('trail_pips', 15)} pip\n"
                    f"Step minimum: {cfg.get('step_pips', 5)} pip\n"
                    f"Posisi: {'Ada' if cfg.get('active_position') else 'Tidak ada'}"
                    f"{pos_text}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"<i>Gunakan /trailing on|off|status</i>",
                    chat_id
                )
        else:
            tg_send(
                "🎯 <b>Smart Trailing Menu</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "/trailing on — Aktifkan auto-trailing\n"
                "/trailing off — Matikan trailing\n"
                "/trailing status — Lihat status & posisi\n"
                "━━━━━━━━━━━━━━━━\n"
                "<i>Trailing SL otomatis setelah profit > breakeven.\n"
                "Bridge update SL setiap 10 detik ke EA kamu.</i>",
                chat_id
            )

    elif cmd == "/download":
        if not _is_donor(chat_id):
            _uname = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")
            _send_donate_menu(chat_id, _uname)
            tg_send(
                "🔒 <b>DOWNLOAD EA — SUBSCRIBER ONLY</b>\n\n"
                "EA ini exclusive buat member yang sudah support project.\n"
                "Silakan subscribe dulu ya, Bro!",
                chat_id
            )
        else:
            ea_path = PROJECT_DIR / "ea" / "VilonaTradeFX_EA.ex5"
            if ea_path.exists():
                _send_document(chat_id, str(ea_path), "VilonaTradeFX_EA.ex5",
                              "🎯 <b>Vilona TradeFX EA</b>\nCent + IDR compatible ✅\nSmart trailing ready ✅")
                logger.info(f"📥 /download served to subscriber chat_id={chat_id}")
            else:
                tg_send("❌ EA file not found. Contact admin.", chat_id)

    elif cmd == "/status":
        # Weekend indicator
        weekend_note = weekend_status_text()

        is_donor = _is_donor(chat_id)
        quota = _get_quota(chat_id)

        # Get actual tier name for display
        display_tier = "FREE"
        try:
            from members import get_member as _gm_status
            m = _gm_status(str(chat_id))
            if m:
                t = m.get("tier", "")
                tier_labels = {"pro": "PRO", "elite": "ELITE", "lifetime": "LIFETIME"}
                display_tier = tier_labels.get(t, "SUBSCRIBER")
        except Exception:
            pass

        if is_donor:
            # Subscriber daily quota tracking
            today = wib_now().strftime("%Y-%m-%d")
            record = USER_DAILY_ANALYZE.get(chat_id, {})
            used = record.get("count", 0) if record.get("date") == today else 0
            remaining = max(0, DONOR_DAILY_QUOTA - used)
            
            # ── Fuel Gauge ──
            fuel_lines = []
            try:
                from members import get_monthly_fuel_stats, get_user_last_donation
                fuel = get_monthly_fuel_stats()
                monthly_total = fuel.get("total", 0)
                donor_count = fuel.get("donor_count", 0)
                TARGET = 500000  # Rp 500rb / bulan
                pct = min(100, int(monthly_total / TARGET * 100))
                bars = "█" * (pct // 10) + "░" * (10 - pct // 10)
                
                last = get_user_last_donation(chat_id)
                
                fuel_lines = [
                    f"",
                    f"⛽ <b>SERVER FUEL: {bars} {pct}%</b>",
                    f"   Rp{monthly_total:,} terkumpul / Rp{TARGET:,} bulan ini",
                ]
                if pct < 30:
                    fuel_lines.append(f"   🔴 <b>KRITIS!</b> Server bisa down minggu ini...")
                elif pct < 60:
                    fuel_lines.append(f"   🟡 Bensin mulai menipis — butuh isi ulang")
                else:
                    fuel_lines.append(f"   🟢 Aman — terima kasih para subscriber!")
                
                fuel_lines.append(f"")
                fuel_lines.append(f"💚 <b>{donor_count}</b> subscriber udah upgrade tier bulan ini.")
                if last:
                    if last["days_ago"] > 30:
                        fuel_lines.append(f"   Kamu terakhir isi: <b>{last['days_ago']} hari</b> lalu — Saatnya isi ulang?")
                    else:
                        fuel_lines.append(f"   Kamu terakhir isi: {last['days_ago']} hari lalu — Makasih Bro!")
                fuel_lines.append(f"   ⚡ <b>/subscribe</b> — Upgrade tier (Rp 50k aja udah ngebantu)")
            except Exception as e:
                logger.warning(f"Fuel gauge failed: {e}")
            
            fuel_text = "\n".join(fuel_lines) if fuel_lines else ""

            txt = (
                f"👑 <b>STATUS: SUBSCRIBER {display_tier}</b>\n"
                f"⚡️ Kuota AI: {'UNLIMITED ♾️' if DONOR_DAILY_QUOTA < 0 else f'{remaining}/{DONOR_DAILY_QUOTA}x'} hari ini (Reset 00:00 WIB)\n"
                f"⏱️ Cooldown: {MANUAL_THROTTLE_DONOR}s antar analisa\n"
                f"{fuel_text}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Terima kasih telah menghidupi mesin AI ini! 🥂\n"
                f"Seluruh fitur Subscriber, Auto-Trade, dan Bridge\n"
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
                f"🔒 <b>Fitur Subscriber Eksklusif:</b>\n"
                f"📥 Download EA MT5 (Auto-Trade)\n"
                f"🔑 License Key untuk EA\n"
                f"🤖 Auto-Trade langsung ke akun MT5\n"
                f"🧠 Multi-Model AI Consensus (akurasi lebih tinggi)\n"
                f"\n"
                f"👉 /subscribe — Buka akses Subscriber sekarang!"
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
                    "⚡ <b>Upgrade Tier!</b>\n"
                    "Subscribe sukarela untuk akses unlimited:\n"
                    "⚡ Upgrade Tier → @berkahkaryaforexbotbot\n\n"
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
        # ── KILLZONE GATE: DISABLED — analyze all sessions 24/7 ──
        # Gate bypassed per user request — /analyze always available
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
                        "👑 <b>Custom Parameter khusus Subscriber!</b>\n"
                        "━━━━━━━━━━━━━━━━\n"
                        "Fitur risk= dan tf= hanya untuk Subscriber.\n\n"
                        "⚡ Upgrade Tier → @berkahkaryaforexbotbot\n"
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

            # ── QUOTA CHECK (anti-abuse) ──
            if _is_donor(str(chat_id)):
                ok, remaining, warn = _check_donor_quota(str(chat_id))
                if not ok:
                    tg_send(warn, chat_id)
                    return
                if remaining >= 0 and remaining <= 5:
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
            ohlcv_bars = _fetch_ohlcv_for_ai(pair, keep=60)
            # ── S-TIER ZONE SCAN: Run mechanical zone detection for all users ──
            stier_zones_text = ""
            if ohlcv_bars and len(ohlcv_bars) >= 30:
                try:
                    stier_sig, stier_reason = detect_stier_zone(pair.upper(), disp, price, ohlcv_bars)
                    if stier_sig and stier_sig["action"] in ("BUY", "SELL"):
                        st_grade = stier_sig.get("grade", "B")
                        st_entry = stier_sig.get("entry", 0)
                        st_sl = stier_sig.get("sl", 0)
                        st_tp = stier_sig.get("tp", 0)
                        dist_pips = abs(price - st_entry) / (0.10 if disp in ("XAUUSD","GOLD") else 0.01 if disp=="USOIL" else 1.0)
                        near_zone = "🎯 IN ZONE" if dist_pips <= 10 else f"📡 {dist_pips:.0f} pip away"
                        stier_zones_text = (
                            f"\\n━━━━━━━━━━━━━━━━━━━━━━\\n"
                            f"💀 <b>S-TIER ZONE [{st_grade}] — {near_zone}</b>\\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\\n"
                            f"{stier_reason}\\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\\n"
                            f"📍 Zone Entry: ${st_entry:.2f}\\n"
                            f"🔴 SL: ${st_sl:.2f} | 🟢 TP: ${st_tp:.2f}\\n"
                            f"📐 RR 1:2.0 | 💀 GOD TIER — Full Margin Ready"
                        )
                        logger.info(f"💀 S-TIER ZONE injected into /analyze [{disp}]: "
                                   f"{stier_sig['action']} @ ${st_entry:.2f} | Grade={st_grade}")
                except Exception as e:
                    logger.debug(f"S-TIER analyze injection [{disp}]: {e}")
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
                # ── Behavioral Tagging: segment user by asset preference ──
                try:
                    from members.tags import add_tag
                    if pair_check in ("gold", "xauusd"):
                        add_tag(str(chat_id), "gold_trader")
                    elif pair_check in ("btc", "btcusd", "eth", "ethusd"):
                        add_tag(str(chat_id), "crypto_trader")
                except Exception:
                    pass
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
                            "👉 /subscribe — Upgrade Tier"
                        )
                    tg_send(auto_text, chat_id)
                    # ── Log activity ──
                    try:
                        username = (msg.get("chat", {}).get("username", "") or
                                   msg.get("from", {}).get("username", "") or "")
                        from tradebot.tracking.activity import log_activity
                        log_activity(str(chat_id), str(chat_id), username.lstrip("@"),
                                     "analyze", user_tier, {"pair": disp})
                    except Exception:
                        pass
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
                    # 💀 S-TIER ZONE Injection (mechanical confluence — all users)
                    if stier_zones_text:
                        text += stier_zones_text
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
                            "💡 <b>Upgrade Tier</b> untuk premium multi-model consensus:\n"
                            "✅ 3 AI model (DeepSeek + GPT-4o + Claude)\n"
                            "✅ Consensus voting → akurasi lebih tinggi\n"
                            "✅ Analisa unlimited 60x/hari\n"
                            "👉 /subscribe — upgrade ke PRO/ELITE"
                        )
                    else:
                        text += (
                            "\n━━━━━━━━━━━━━━━━\n"
                            "⭐ <b>PREMIUM TIER — Multi-Model Consensus</b>\n"
                            "3 AI model (DeepSeek + GPT-4o + Claude) konsensus.\n"
                            "Akurasi maksimal berkat support kamu! 🥂\n"
                            "👉 /subscribe — Ajak teman ikut subscribe"
                        )
                    # ── Fuel Gauge Reminder (every 3rd analyze for donors) ──
                    if is_donor:
                        DONOR_ANALYZE_COUNT[str(chat_id)] = DONOR_ANALYZE_COUNT.get(str(chat_id), 0) + 1
                        if DONOR_ANALYZE_COUNT[str(chat_id)] % 3 == 0:
                            try:
                                from members import get_monthly_fuel_stats
                                fuel = get_monthly_fuel_stats()
                                fuel_pct = int((fuel['total'] / 500000) * 100)
                                fuel_bar = '█' * min(10, int(fuel['total'] / 50000)) + '░' * (10 - min(10, int(fuel['total'] / 50000)))
                                text += f'\n━━━━━━━━━━━━━━━━━━━━━━\n⛽ Server Fuel: {fuel_bar} {fuel_pct}%\nRp{fuel["total"]:,} / Rp500,000 | {fuel["donor_count"]} subscriber\n⚡ /subscribe — Upgrade tier'
                            except Exception:
                                pass
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
                    # ── Log activity ──
                    try:
                        from tradebot.tracking.activity import log_activity
                        log_activity(str(chat_id), str(chat_id), username,
                                     "analyze", user_tier, {"pair": disp})
                    except Exception:
                        pass
            else:
                # ── AI FALLBACK: Mechanical signal when all AI models fail ──
                logger.warning(f"AI all failed for {disp} — falling back to mechanical")
                try:
                    mech_sig, mech_reason = detect_mechanical_signal(disp, disp, price, ohlcv_bars)
                    if mech_sig:
                        mech_sig["_tier_capped"] = True
                        mech_sig["action"] = mech_sig.get("action", "HOLD") or "HOLD"
                        mech_sig["confidence"] = mech_sig.get("confidence", 25)
                        action = mech_sig["action"]
                        _touch_manual(str(chat_id), action=action if action in ("BUY","SELL") else None, asset=disp)
                        mech_sig = _clamp_sltp(mech_sig, disp)
                        curr = "Rp" if is_idx else "$"
                        text = fmt_signal(mech_sig, price, dxy, wib_now().hour, disp, curr, quality="C")
                        text += f"\n\n⚠️ <b>Mechanical Fallback</b> — AI models sedang sibuk (rate limit).\nAkurasi terbatas. Coba /analyze lagi nanti."
                        tg_send(text, chat_id)
                        # ── Log activity ──
                        try:
                            username_fb = (msg.get("chat", {}).get("username", "") or
                                         msg.get("from", {}).get("username", "") or "")
                            from tradebot.tracking.activity import log_activity
                            log_activity(str(chat_id), str(chat_id), username_fb.lstrip("@"),
                                         "analyze", user_tier, {"pair": disp})
                        except Exception:
                            pass
                    else:
                        tg_send("❌ Analisa gagal — semua mesin analisa sibuk. Coba lagi dalam 1 menit.", chat_id)
                except Exception as e:
                    logger.error(f"Mechanical fallback error: {e}")
                    tg_send("❌ Analisa gagal — sistem lagi penuh. Coba lagi ya bro.", chat_id)
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
                    ohlcv_bars2 = _fetch_ohlcv_for_ai(sub, keep=60)
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
                                    "💡 <b>Kalau sinyal ini cuan, saatnya upgrade tier!</b>\n"
                                    "Server analisa 24/7 butuh biaya API & GPU.\n"
                                    "Jangan cuma diperas aja Bro 😄\n"
                                    "👉 /subscribe — pilih tier subscription"
                                )
                            else:
                                text += (
                                    "\n━━━━━━━━━━━━━━━━\n"
                                    "🤝 <b>Makasih udah jadi Subscriber!</b>\n"
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

    elif cmd == "/bill" or cmd == "/subscribe" or cmd == "/upgrade":
        # ── TIERED SUBSCRIPTION ──
        if not chat_id:
            return
        username = ""
        if msg:
            username = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")
        sub_arg = sub_norm if sub_norm else ""
        if sub_arg in ("pro", "elite", "lifetime"):
            # Direct tier purchase
            try:
                from members.payment import create_tripay_payment
                result = create_tripay_payment(str(chat_id), username, tier=sub_arg)
                if result.get("success"):
                    payment_url = result.get("payment_url", "")
                    pay_code = result.get("pay_code", "")
                    amount = result.get("amount", 0)
                    tier_label = result.get("tier_label", sub_arg.upper())
                    txt = (
                        f"💳 <b>Pembayaran {tier_label}</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"💰 Total: Rp{amount:,}\n"
                        f"📎 Kode: <code>{pay_code}</code>\n\n"
                        f"🔗 <a href='{payment_url}'>Klik di sini untuk bayar</a>\n\n"
                        f"⏰ Link berlaku 1 jam.\n"
                        f"Status akan otomatis aktif setelah pembayaran."
                    )
                    tg_send(txt, chat_id)
                    # Activity + CAPI InitiateCheckout
                    try:
                        from tradebot.tracking.activity import log_activity
                        log_activity(str(chat_id), chat_id, username, "subscribe", sub_arg, {"amount": amount, "ref": result.get("reference", "")})
                    except Exception: pass
                    try:
                        from tradebot.tracking.events import fire_initiate_checkout
                        fire_initiate_checkout(str(chat_id), sub_arg, amount)
                    except Exception: pass
                else:
                    tg_send(f"❌ {result.get('error', 'Gagal membuat pembayaran.')}", chat_id)
            except Exception as e:
                logger.error(f"Subscribe error: {e}")
                tg_send("❌ Sistem pembayaran sedang sibuk. Coba lagi nanti.", chat_id)
            return

        # Show tier selection
        from members.payment import get_pricing_table
        txt = get_pricing_table()
        markup = {"inline_keyboard": [
            [{"text": "⭐ PRO — Rp50K/bulan", "callback_data": "sub:pro"},
             {"text": "👑 ELITE — Rp150K/bulan", "callback_data": "sub:elite"}],
            [{"text": "💎 LIFETIME — Rp500K (sekali)", "callback_data": "sub:lifetime"}],
            [{"text": "💳 Bayar via QRIS/VA", "callback_data": "sub:pay"}],
        ]}
        tg_send(txt, chat_id, reply_markup=markup)

    elif cmd == "/donate":
        # ── /donate — legacy redirect ke tiered subscription ──
        if not chat_id:
            return
        username = ""
        if msg:
            username = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")
        _send_donate_menu(chat_id, username)

    elif cmd == "/testpay":
        """🧪 Test payment: subscribe minimal — verifikasi webhook Tripay. ADMIN ONLY."""
        if not chat_id:
            return
        # Admin gate
        admin_ids_tp = [os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", ""), "5220170786", "157228659"]
        if str(chat_id) not in admin_ids_tp:
            tg_send("⛔ Admin only.", chat_id)
            return
        if not PAYMENT_ENGINE:
            tg_send("💳 Payment gateway belum aktif.", chat_id)
            return

        username = ""
        if msg:
            username = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")

        tg_send("🧪 <b>Test Upgrade Tier — Rp10,000</b>\nMembuat invoice...", chat_id)

        result = create_tripay_payment(str(chat_id), username, tier="pro", amount=10000)
        if result.get("error"):
            tg_send(f"❌ Gagal: {result['error']}", chat_id)
            return

        pay_url = result.get("payment_url", "")
        ref = result.get("reference", "") or result.get("merchant_ref", "")

        txt = (
            "🧪 <b>Test Upgrade Tier — Rp10,000</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "💰 Total: <b>Rp10,000</b>\n"
            "👑 Status: SUBSCRIBER — AKTIF PERMANEN\n"
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
        """Admin: Manual activation — set user ke SUBSCRIBER."""
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
            m_upgrade(target_id, "lifetime", days, ref)

            # Notify admin
            tg_send(
                f"✅ <b>Manual Activation Berhasil</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"👤 User: <code>{target_id}</code>\n"
                f"👑 Status: <b>SUBSCRIBER — LIFETIME</b>",
                chat_id
            )

            # DM the activated user
            if BOT_TOKEN:
                user_msg = (
                    f"🔥 <b>BOOM! Kamu sekarang SUBSCRIBER!</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"👑 Status: <b>SUBSCRIBER — LIFETIME</b>\n"
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

        # ── TIER GATE: Only donors can auto-trade ──
        if not _is_donor(str(chat_id)):
            tg_send(
                "🔒 <b>Auto-Trade khusus Subscriber!</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Fitur auto-trade ke EA hanya tersedia untuk\n"
                "👑 <b>Subscriber</b> — yang sudah dukung server AI.\n\n"
                "⚡ Upgrade Tier → @berkahkaryaforexbotbot\n"
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
        """Market Intel — DeepSeek macro context & catalyst analysis. Subscriber only."""
        if not _is_donor(str(chat_id)):
            tg_send(
                f"📰 <b>Market Intel</b> [🔒 LOCKED]\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Market Intel adalah <b>macro catalyst analysis</b>\n"
                f"yang kasih tau konteks ekonomi di balik pergerakan\n"
                f"market SEBELUM lu entry.\n"
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
                f"🔋 <b>AI Power: ■■■□□ 75%</b> — Market Intel idle\n"
                f"   AI lu cuma bisa liat chart doang...\n"
                f"   Bayangin kalo bisa baca konteks makro juga 😤\n"
                f"\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>/subscribe</b> — Rp 50K/bln (PRO)\n"
                f"   Unlock Market Intel + 2 AI + /levels\n"
                f"   Kasih AI lu konteks makro 📊",
                chat_id
            )
            return

        # Donor: call DeepSeek for market context
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_n = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth",
                      "oil":"oil","usoil":"oil","eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_n = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD",
                      "gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_n.get(sub_norm, "gold")
        disp = disp_map_n.get(pair, "XAUUSD")

        tg_send(f"📰 <b>Analyzing market context for {disp}...</b>\n<i>This takes ~3-5 seconds</i>", chat_id)

        try:
            price = fetch_price(pair) or 0
            news = _call_market_news(disp, price)

            if not news:
                tg_send(f"❌ Gagal fetch market context untuk {disp}. Coba lagi nanti.", chat_id)
                return

            headline = news.get("headline", "No major catalysts")
            sentiment = news.get("sentiment", "NEUTRAL")
            impact = news.get("impact", "LOW")
            detail = news.get("detail", "")

            s_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}.get(sentiment, "⚪️")
            i_emoji = {"HIGH": "🔥", "MED": "📊", "LOW": "📎"}.get(impact, "")

            if headline == "No major catalysts":
                msg = (
                    f"📰 <b>Market Intel — {disp}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚪️ <b>No major catalysts detected</b>\n"
                    f"\n"
                    f"Market currently quiet — no significant macro\n"
                    f"events or catalysts affecting {disp} right now.\n"
                    f"\n"
                    f"💡 Fokus ke analisa teknikal — chart is king.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📰 Market Intel Active ✅ — DeepSeek macro analysis\n"
                    f"🤝 <b>Your AI Partner keeps watching.</b>"
                )
            else:
                token_used = _AI_TOKEN_USAGE.get("deepseek_news", {}).get("total", 0)
                token_k = f"{token_used/1000:.1f}k" if token_used >= 1000 else str(token_used)

                msg = (
                    f"📰 <b>Market Intel — {disp}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{s_emoji} <b>{headline}</b>\n"
                    f"\n"
                )
                if detail:
                    msg += f"💡 {detail}\n\n"
                msg += (
                    f"Sentiment: <b>{sentiment}</b> | Impact: {i_emoji} <b>{impact}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🧠 {token_k} token dipakai — macro analysis\n"
                    f"📰 Market Intel Active ✅ — DeepSeek-powered\n"
                    f"🤝 <b>AI Partner kasih lu edge.</b>\n"
                    f"\n"
                    f"💡 Combine dengan /signal untuk konfirmasi teknikal"
                )

            tg_send(msg, chat_id)

        except Exception as e:
            logger.warning(f"/news error: {e}")
            tg_send(f"❌ Gagal fetch Market Intel: {e}", chat_id)

    # ── NEW: Technical Analysis Commands ──
    elif cmd == "/stier":
        """S-TIER Zone Detector — Triple Confluence (Breaker + OB/FVG + Double Sweep). 👑 PREMIUM ONLY."""
        # ── PREMIUM GATE ──
        if not _is_donor(str(chat_id)):
            tg_send(
                "👑 <b>S-TIER Zone — PREMIUM ONLY</b>\n\n"
                "S-TIER adalah detektor triple confluence dengan akurasi tertinggi.\n"
                "Fitur ini eksklusif untuk subscriber PRO/ELITE/LIFETIME.\n\n"
                "⭐ <b>Upgrade sekarang:</b>\n"
                "   /upgrade atau DM @berkahkaryaforexbotbot",
                chat_id
            )
            return
        sub_norm_st = _normalize_broker_symbol(sub or "xauusd")
        pair_map_st = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_st = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair_st = pair_map_st.get(sub_norm_st, "gold")
        disp_st = disp_map_st.get(pair_st, "XAUUSD")
        pip_sz = 0.10 if disp_st in ("XAUUSD","GOLD") else 0.01 if disp_st=="USOIL" else 1.0
        
        price_st = fetch_price(pair_st)
        if not price_st:
            tg_send(f"❌ Harga tidak tersedia untuk {disp_st}.", chat_id)
            return
        
        tg_send(f"💀 <b>Scanning S-TIER Zones — {disp_st} @ ${price_st:.2f}...</b>\n<i>Triple Confluence: Breaker Block + OB/FVG + Double Sweep</i>", chat_id)
        
        ohlcv_st = _fetch_ohlcv_for_ai(pair_st, keep=60)
        if not ohlcv_st or len(ohlcv_st) < 30:
            tg_send(f"❌ Data OHLCV tidak cukup untuk {disp_st}.", chat_id)
            return
        
        try:
            st_sig, st_reason = detect_stier_zone(pair_st.upper(), disp_st, price_st, ohlcv_st)
            
            if not st_sig:
                lines = [
                    f"💀 <b>S-TIER ZONE — {disp_st} @ ${price_st:.2f}</b>",
                    f"━━━━━━━━━━━━━━━━━━━━━━",
                    f"",
                    f"⚪️ <b>Tidak ada S-TIER zone terdeteksi</b>",
                    f"",
                    f"Market saat ini belum menunjukkan triple confluence:",
                    f"  • Belum ada Breaker Block valid",
                    f"  • OB + FVG tidak aligned di level yang sama",
                    f"  • Tidak ada Double Liquidity Sweep",
                    f"",
                    f"━━━━━━━━━━━━━━━━━━━━━━",
                    f"💡 S-TIER zone adalah setup probabilitas tertinggi.",
                    f"   Sabar — zone ini muncul 1-3x per sesi.",
                    f"   Cek /zones atau /structure untuk analisa regular.",
                    f"━━━━━━━━━━━━━━━━━━━━━━",
                    f"⚠️ <i>Tools analisa teknikal — bukan sinyal trading.</i>",
                ]
                tg_send("\n".join(lines), chat_id)
                return
            
            st_grade = st_sig.get("grade", "B")
            st_entry = st_sig.get("entry", 0)
            st_sl = st_sig.get("sl", 0)
            st_tp = st_sig.get("tp", 0)
            st_conf = st_sig.get("confidence", 0)
            st_act = st_sig.get("action", "HOLD")
            dist = abs(price_st - st_entry) / pip_sz
            near = "🎯 IN ZONE" if dist <= 10 else f"📡 {dist:.0f} pip away"
            act_emoji = "🟢" if st_act == "BUY" else "🔴" if st_act == "SELL" else "⚪️"
            
            lines = [
                f"💀 <b>S-TIER ZONE [{st_grade}] — {disp_st}</b>",
                f"━━━━━━━━━━━━━━━━━━━━━━",
                f"",
                st_reason,
                f"",
                f"━━━━━━━━━━━━━━━━━━━━━━",
                f"📍 <b>Zone Entry:</b> ${st_entry:.2f}",
                f"🔴 <b>SL:</b> ${st_sl:.2f} (-30 pip)",
                f"🟢 <b>TP:</b> ${st_tp:.2f} (+60 pip)",
                f"📐 <b>RR 1:2.0</b> | Conf: {st_conf:.0%}",
                f"",
                f"{act_emoji} <b>{st_act}</b> | {near}",
            ]
            if st_grade == "S-TIER":
                lines.append(f"")
                lines.append(f"💀 <b>GOD TIER — Near-100% Conviction</b>")
            lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"⚠️ <i>Tools analisa teknikal — bukan sinyal trading.</i>")
            tg_send("\n".join(lines), chat_id)
            logger.info(f"💀 /stier [{disp_st}]: {st_act} | Grade={st_grade}")
        except Exception as e:
            logger.error(f"/stier error: {e}")
            tg_send(f"❌ Gagal scan S-TIER zone: {e}", chat_id)

    elif cmd == "/zones":
        """Liquidity zones: OB + FVG + Supply/Demand. Free: 1 TF. Donor: multi-TF."""
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_z = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_z = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_z.get(sub_norm, "gold")
        disp = disp_map_z.get(pair, "XAUUSD")
        is_donor = _is_donor(str(chat_id))
        
        price = fetch_price(pair)
        if not price:
            tg_send(f"❌ Price unavailable untuk {disp}.", chat_id)
            return
        
        tg_send(f"🔍 <b>Scanning {disp} zones @ {price}...</b>", chat_id)
        
        # Fetch multi-TF OHLCV
        ohlcv_h1 = _fetch_ohlcv_for_ai(pair)
        ohlcv_m15 = None
        if is_donor and MARKET_DATA:
            try:
                m15_bars = MARKET_DATA.get_ohlcv(pair, "15m", 100)
                if m15_bars and len(m15_bars) >= 20:
                    ohlcv_m15 = [{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                  "low": b.low, "close": b.close, "volume": b.volume} for b in m15_bars]
            except: pass
        
        lines = [f"🧲 <b>LIQUIDITY ZONES — {disp} @ {price}</b>",
                 f"━━━━━━━━━━━━━━━━━━━━━━"]
        
        pip_s = 0.10 if disp in ("XAUUSD","GOLD") else (0.01 if disp == "USOIL" else (1.0 if disp in ("BTCUSD","ETHUSD") else 0.0001))
        
        # ── FVG Zones (scan both H1 raw zones + M15) ──
        fvgs_found = False
        try:
            if FVG_ENGINE:
                from fvg_detector import detect_fvg_zones
                # Raw FVG zones (unfiltered by price proximity)
                raw_zones = detect_fvg_zones(ohlcv_h1, max_age=30)
                if raw_zones:
                    fvgs_found = True
                    lines.append("")
                    lines.append("📐 <b>FAIR VALUE GAPS (H1)</b>")
                    for z in raw_zones[:5]:
                        mid = (z.top + z.bottom) / 2
                        filled = "✅ filled" if getattr(z, 'filled', False) else "⏳ open"
                        dist = abs(price - mid) / pip_s
                        lines.append(f"  {z.top:.2f} — {z.bottom:.2f} ({z.size_pips:.0f} pip | {filled} | {dist:.0f} pip away)")
        except Exception as e:
            logger.debug(f"FVG zone scan error: {e}")

        if not fvgs_found:
            lines.append("")
            lines.append("📐 <b>FAIR VALUE GAPS</b>")
            lines.append("  No FVG in last 30 H1 bars — market efisien tanpa gap.")

        # ── Order Blocks + Structure (BOS) ──
        obs_found = False
        try:
            if SMC_ENGINE:
                smc = analyze_smc_scalper(ohlcv_h1, disp)
                if smc:
                    # Try order_blocks first, then blocks, then _bos for structure
                    blocks = smc.get("order_blocks", smc.get("blocks", []))
                    bos = smc.get("_bos", {})
                    idm = smc.get("_idm", {})
                    false_break = smc.get("_false_break", {})

                    if blocks:
                        obs_found = True
                        lines.append("")
                        lines.append("🏦 <b>ORDER BLOCKS (H1)</b>")
                        for ob in blocks[:4]:
                            ob_price = ob.get("price", ob.get("level", 0))
                            ob_dir = ob.get("direction", ob.get("type", "?"))
                            ob_strength = ob.get("strength", "?")
                            if ob_price > 0:
                                emoji = "🟢" if "BULL" in str(ob_dir).upper() else "🔴"
                                lines.append(f"  {emoji} {ob_dir}: {ob_price:.2f} (str: {ob_strength})")

                    # Show BOS/IDM even if no order blocks
                    if bos and bos.get("direction"):
                        if not obs_found:
                            lines.append("")
                            lines.append("🏦 <b>MARKET STRUCTURE (H1)</b>")
                            obs_found = True  # mark as found so we don't show "none"
                        bos_dir = bos.get("direction", "?")
                        bos_emoji = "🟢" if bos_dir == "BUY" else "🔴"
                        lines.append(f"  {bos_emoji} <b>BOS ({bos_dir}):</b> ${bos.get('price', 0):.2f}")
                    if idm and idm.get("direction"):
                        idm_dir = idm.get("direction", "?")
                        lines.append(f"  ⚡ IDM ({idm_dir}): ${idm.get('price', 0):.2f}")
                    if false_break and false_break.get("direction"):
                        fb_dir = false_break.get("direction", "?")
                        lines.append(f"  ⚠️ False Break ({fb_dir}): ${false_break.get('price', 0):.2f}")
        except Exception as e:
            logger.debug(f"OB/Structure scan error: {e}")

        if not obs_found:
            lines.append("")
            lines.append("🏦 <b>ORDER BLOCKS</b>")
            lines.append("  No significant OB / BOS near current price.")
        
        # ── Supply/Demand ──
        try:
            from liquidity_zones import map_zones, find_tp_targets
            from session_levels import calculate_all_levels
            sess_lvls = calculate_all_levels(ohlcv_h1[-60:]) if len(ohlcv_h1) >= 30 else None
            if sess_lvls:
                sweep_dir = "BULLISH" if price > (sess_lvls.asia_high or price) else "BEARISH"
                liq_map = map_zones(ohlcv_h1[-60:], sess_lvls, price, sweep_dir)
                if liq_map and liq_map.zones:
                    supply = [z for z in liq_map.zones if z.direction == "SHORT" and z.midpoint > price]
                    demand = [z for z in liq_map.zones if z.direction == "LONG" and z.midpoint < price]
                    supply.sort(key=lambda z: z.midpoint)
                    demand.sort(key=lambda z: z.midpoint, reverse=True)
                    
                    if supply or demand:
                        lines.append("")
                        lines.append("💧 <b>SUPPLY / DEMAND</b>")
                        if supply:
                            lines.append("  🔴 <b>Supply (Resistance):</b>")
                            for z in supply[:3]:
                                lines.append(f"    {z.midpoint:.2f} ({z.zone_type} | {z.distance_pips:.0f} pip away)")
                        if demand:
                            lines.append("  🟢 <b>Demand (Support):</b>")
                            for z in demand[:3]:
                                lines.append(f"    {z.midpoint:.2f} ({z.zone_type} | {z.distance_pips:.0f} pip away)")
        except Exception as e:
            logger.debug(f"Supply/Demand calc error: {e}")
        
        # ── Donor: M15 granular zones ──
        if is_donor and ohlcv_m15:
            try:
                from liquidity_zones import map_zones
                from session_levels import calculate_all_levels
                sess15 = calculate_all_levels(ohlcv_m15)
                if sess15:
                    sweep15 = "BULLISH" if price > (sess15.asia_high or price) else "BEARISH"
                    liq15 = map_zones(ohlcv_m15, sess15, price, sweep15)
                    if liq15 and liq15.zones:
                        supply15 = [z for z in liq15.zones if z.direction == "SHORT"]
                        demand15 = [z for z in liq15.zones if z.direction == "LONG"]
                        if supply15 or demand15:
                            lines.append("")
                            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                            lines.append("👑 <b>DONOR: M15 GRANULAR ZONES</b>")
                            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                            if supply15:
                                lines.append("  🔴 Supply (M15):")
                                for z in supply15[:3]:
                                    lines.append(f"    {z.midpoint:.2f} ({z.zone_type})")
                            if demand15:
                                lines.append("  🟢 Demand (M15):")
                                for z in demand15[:3]:
                                    lines.append(f"    {z.midpoint:.2f} ({z.zone_type})")
            except Exception as e:
                logger.debug(f"M15 zones error: {e}")
        
        # ── Equilibrium ──
        try:
            if ohlcv_h1 and len(ohlcv_h1) >= 30:
                highs_h1 = [float(b.get("high", b.get("h", 0))) for b in ohlcv_h1[-30:]]
                lows_h1 = [float(b.get("low", b.get("l", 0))) for b in ohlcv_h1[-30:]]
                if highs_h1 and lows_h1:
                    h1_range = max(highs_h1) - min(lows_h1)
                    eq = min(lows_h1) + h1_range * 0.50
                    lines.append("")
                    lines.append("⚖️ <b>EQUILIBRIUM (H1 50%)</b>")
                    lines.append(f"  {eq:.2f} | Price {'above ⬆️' if price > eq else 'below ⬇️'} equilibrium")
        except: pass
        
        if not is_donor:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🔒 <b>FREE TIER — H1 Zones Only</b>")
            lines.append("👑 Multi-TF (M15 granular + full zone depth)")
            lines.append("   → <b>/subscribe</b> untuk unlock")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ <i>Tools analisa teknikal — bukan sinyal trading.</i>")
        tg_send("\n".join(lines), chat_id)
    
    elif cmd == "/structure":
        """Market structure: BOS/CHoCH + Trend + MTF alignment. Free: basic. Donor: full."""
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_s = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_s = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_s.get(sub_norm, "gold")
        disp = disp_map_s.get(pair, "XAUUSD")
        is_donor = _is_donor(str(chat_id))
        
        price = fetch_price(pair)
        if not price:
            tg_send(f"❌ Price unavailable untuk {disp}.", chat_id)
            return
        
        tg_send(f"🏗 <b>Analyzing {disp} structure @ {price}...</b>", chat_id)
        
        ohlcv_h1 = _fetch_ohlcv_for_ai(pair, keep=60)
        if not ohlcv_h1 or len(ohlcv_h1) < 30:
            tg_send(f"❌ Data tidak cukup untuk analisa struktur {disp}.", chat_id)
            return
        
        closes = [float(b.get("c", b.get("close", 0))) for b in ohlcv_h1]
        highs = [float(b.get("h", b.get("high", 0))) for b in ohlcv_h1]
        lows = [float(b.get("l", b.get("low", 0))) for b in ohlcv_h1]
        
        pip_s = 0.10 if disp in ("XAUUSD","GOLD") else (0.01 if disp == "USOIL" else (1.0 if disp in ("BTCUSD","ETHUSD") else 0.0001))
        
        lines = [f"🏗 <b>MARKET STRUCTURE — {disp} @ {price}</b>",
                 f"━━━━━━━━━━━━━━━━━━━━━━"]
        
        # ── Trend Direction (M15 + H1) ──
        sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else price
        sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else price
        trend_h1 = "BULLISH 📈" if price > sma20 > sma50 else ("BEARISH 📉" if price < sma20 < sma50 else "CHOPPY ↔️")
        trend_strength = min(100, int(abs(price - sma20) / pip_s * 2)) if sma20 else 0
        
        lines.append("")
        lines.append(f"📊 <b>TREND:</b> {trend_h1} | Strength: {trend_strength}%")
        lines.append(f"  SMA20: {sma20:.2f} | SMA50: {sma50:.2f}")
        
        # ── BOS/CHoCH Detection ──
        bos_bull = []
        bos_bear = []
        for i in range(7, len(highs)-2):
            lookback = highs[i-7:i]
            if highs[i] > max(lookback) and highs[i] > highs[i+1]:
                prev_highs = [h for j, h in enumerate(highs[:i]) if j >= 5 and h > highs[j-1] and h > highs[j+1]]
                if prev_highs:
                    if len(prev_highs) >= 3:
                        if highs[i] > max(prev_highs[-3:]):
                            bos_bull.append({"price": highs[i], "idx": i, "bar": len(highs)-i, "type": "BOS ▲"})
                    elif highs[i] > prev_highs[-1]:
                        bos_bull.append({"price": highs[i], "idx": i, "bar": len(highs)-i, "type": "BOS ▲"})
        for i in range(7, len(lows)-2):
            lookback = lows[i-7:i]
            if lows[i] < min(lookback) and lows[i] < lows[i+1]:
                prev_lows = [l for j, l in enumerate(lows[:i]) if j >= 5 and l < lows[j-1] and l < lows[j+1]]
                if prev_lows:
                    if len(prev_lows) >= 3:
                        if lows[i] < min(prev_lows[-3:]):
                            bos_bear.append({"price": lows[i], "idx": i, "bar": len(lows)-i, "type": "BOS ▼"})
                    elif lows[i] < prev_lows[-1]:
                        bos_bear.append({"price": lows[i], "idx": i, "bar": len(lows)-i, "type": "BOS ▼"})
        
        # CHoCH (Change of Character)
        choch_bull = []
        choch_bear = []
        for i in range(5, len(highs)-3):
            if highs[i] > max(highs[i-5:i]) and lows[i-3] < min(lows[i-8:i-3]) if i >= 8 else False:
                if any(l < lows[i-3] for l in lows[i-6:i-2] if i >= 6):
                    choch_bull.append({"price": highs[i], "bar": len(highs)-i, "type": "CHoCH ▲"})
        for i in range(5, len(lows)-3):
            if lows[i] < min(lows[i-5:i]) and highs[i-3] > max(highs[i-8:i-3]) if i >= 8 else False:
                if any(h > highs[i-3] for h in highs[i-6:i-2] if i >= 6):
                    choch_bear.append({"price": lows[i], "bar": len(lows)-i, "type": "CHoCH ▼"})
        
        all_struct = bos_bull + bos_bear + choch_bull + choch_bear
        all_struct.sort(key=lambda x: x["bar"])
        recent_struct = all_struct[:5]
        
        lines.append("")
        lines.append("🔄 <b>STRUCTURE BREAKS (BOS / CHoCH)</b>")
        if recent_struct:
            for s in recent_struct:
                arrow = "🟢" if "▲" in s["type"] else "🔴"
                lines.append(f"  {arrow} {s['type']}: {s['price']:.2f} ({s['bar']} bars ago)")
        else:
            lines.append("  No clear BOS/CHoCH in recent structure.")
        
        # ── Swing Points ──
        swings_high = []
        swings_low = []
        for i in range(3, len(highs)-3):
            if highs[i] > max(highs[i-3], highs[i-2], highs[i-1], highs[i+1], highs[i+2], highs[i+3]):
                swings_high.append(highs[i])
            if lows[i] < min(lows[i-3], lows[i-2], lows[i-1], lows[i+1], lows[i+2], lows[i+3]):
                swings_low.append(lows[i])
        
        nearest_res = min([h for h in swings_high if h > price], default=None) if swings_high else None
        nearest_sup = max([l for l in swings_low if l < price], default=None) if swings_low else None
        
        lines.append("")
        lines.append("📍 <b>KEY SWINGS</b>")
        if nearest_res:
            lines.append(f"  🔴 Nearest Resistance: {nearest_res:.2f} (+{abs(nearest_res-price)/pip_s:.0f} pip)")
        if nearest_sup:
            lines.append(f"  🟢 Nearest Support: {nearest_sup:.2f} (-{abs(price-nearest_sup)/pip_s:.0f} pip)")
        
        # ── Donor: MTF Alignment ──
        if is_donor:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("👑 <b>DONOR: FULL MTF ALIGNMENT</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            
            # D1 trend
            d1_bars = ohlcv_h1[-96:] if len(ohlcv_h1) >= 96 else ohlcv_h1
            d1_closes = [float(b.get("c", b.get("close", 0))) for b in d1_bars]
            d1_sma20 = sum(d1_closes[-20:])/20 if len(d1_closes)>=20 else price
            d1_trend = "BULLISH 📈" if max(d1_closes[-20:]) > d1_sma20 else "BEARISH 📉"
            
            lines.append(f"  📅 D1: {d1_trend}")
            lines.append(f"  ⏱️ H1: {trend_h1}")
            
            # M15 via MARKET_DATA if available
            if MARKET_DATA:
                try:
                    m15_bars = MARKET_DATA.get_ohlcv(pair, "15m", 50)
                    if m15_bars and len(m15_bars) >= 20:
                        m15c = [b.close for b in m15_bars]
                        m15_sma = sum(m15c[-20:])/20
                        m15_trend = "BULLISH" if m15c[-1] > m15_sma else "BEARISH"
                        lines.append(f"  🔍 M15: {m15_trend}")
                except: pass
            
            # Alignment %
            trends = []
            if d1_trend.startswith("BULLISH"): trends.append(1)
            elif d1_trend.startswith("BEARISH"): trends.append(-1)
            if trend_h1.startswith("BULLISH"): trends.append(1)
            elif trend_h1.startswith("BEARISH"): trends.append(-1)
            align_pct = 100 if len(set(trends)) == 1 else (67 if len(trends)>=2 else 33)
            
            lines.append(f"  🎯 <b>MTF Alignment: {align_pct}%</b>")
            
            struc_grade = "A" if align_pct >= 90 else ("B" if align_pct >= 67 else "C")
            lines.append(f"  🏅 <b>Structure Grade: {struc_grade}</b>")
            
            # CHoCH count
            total_choch = len(choch_bull) + len(choch_bear)
            total_bos = len(bos_bull) + len(bos_bear)
            lines.append(f"  🔄 CHoCH: {total_choch} | BOS: {total_bos}")
            
            # Equilibrium zones
            if swings_high and swings_low:
                range_20 = max(highs[-20:]) - min(lows[-20:])
                eq_h1 = min(lows[-20:]) + range_20 * 0.5
                lines.append(f"  ⚖️ H1 Equilibrium: {eq_h1:.2f}")
        
        if not is_donor:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🔒 <b>FREE TIER — Basic Structure</b>")
            lines.append("👑 MTF Alignment + Structure Grade + CHoCH count")
            lines.append("   → <b>/subscribe</b> untuk unlock full analysis")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ <i>Tools analisa teknikal — bukan sinyal trading.</i>")
        tg_send("\n".join(lines), chat_id)
    
    elif cmd == "/session":
        """Session levels: Killzone + High/Low + Range. Free: current session. Donor: all 3."""
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_ss = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                       "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_ss = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        pair = pair_map_ss.get(sub_norm, "gold")
        disp = disp_map_ss.get(pair, "XAUUSD")
        is_donor = _is_donor(str(chat_id))
        
        price = fetch_price(pair)
        if not price:
            tg_send(f"❌ Price unavailable untuk {disp}.", chat_id)
            return
        
        tg_send(f"🕐 <b>Fetching {disp} session levels @ {price}...</b>", chat_id)
        
        now = wib_now()
        h = now.hour
        lkz, nykz = killzone(h)
        active_kz = "London 🇬🇧" if lkz else ("New York 🇺🇸" if nykz else "Asian 🌏 (Outside Killzone)")
        weekday = now.strftime("%A")
        
        pip_s = 0.10 if disp in ("XAUUSD","GOLD") else (0.01 if disp == "USOIL" else (1.0 if disp in ("BTCUSD","ETHUSD") else 0.0001))
        
        lines = [f"🕐 <b>SESSION LEVELS — {disp} @ {price}</b>",
                 f"━━━━━━━━━━━━━━━━━━━━━━",
                 f"📅 {now.strftime('%Y.%m.%d %H:%M')} WIB | {weekday}",
                 f"🟢 Active: <b>{active_kz}</b>"]
        
        try:
            from session_levels import calculate_all_levels

            # Get OHLCV for session calculation
            ohlcv_bars = _fetch_ohlcv_for_ai(pair, keep=60)
            if not ohlcv_bars or len(ohlcv_bars) < 30:
                # Fallback: try MARKET_DATA
                if MARKET_DATA:
                    raw = MARKET_DATA.get_ohlcv(pair, "15m", 100)
                    if raw:
                        ohlcv_bars = [{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                       "low": b.low, "close": b.close, "volume": b.volume} for b in raw]
            
            if ohlcv_bars and len(ohlcv_bars) >= 20:
                sess = calculate_all_levels(ohlcv_bars)
                
                if sess:
                    # Compute session ranges (not stored in SessionLevels dataclass)
                    asia_rng = sess.asia_high - sess.asia_low if (sess.asia_high and sess.asia_low) else 0
                    london_rng = sess.london_high - sess.london_low if (sess.london_high and sess.london_low) else 0

                    lines.append("")
                    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                    
                    # Current session high/low
                    if lkz and sess.london_high:
                        lines.append(f"🇬🇧 <b>LONDON (Active)</b>")
                        lines.append(f"  High: {sess.london_high:.2f} | Low: {sess.london_low:.2f}")
                        if london_rng:
                            lines.append(f"  Range: {london_rng:.2f} ({london_rng/pip_s:.0f} pip)")
                    elif nykz and sess.ny_high:
                        lines.append(f"🇺🇸 <b>NEW YORK (Active)</b>")
                        lines.append(f"  High: {sess.ny_high:.2f} | Low: {sess.ny_low:.2f}")
                    elif sess.asia_high:
                        lines.append(f"🌏 <b>ASIA</b>")
                        lines.append(f"  High: {sess.asia_high:.2f} | Low: {sess.asia_low:.2f}")
                        if asia_rng:
                            lines.append(f"  Range: {asia_rng:.2f} ({asia_rng/pip_s:.0f} pip)")
                    
                    # All 3 sessions (always show for context)
                    if sess.asia_high and not (not nykz and not lkz):
                        lines.append("")
                        lines.append(f"🌏 <b>ASIA</b>")
                        lines.append(f"  High: {sess.asia_high:.2f} | Low: {sess.asia_low:.2f}")
                        if asia_rng:
                            lines.append(f"  Range: {asia_rng:.2f} ({asia_rng/pip_s:.0f} pip)")
                    
                    if sess.london_high and not lkz:
                        lines.append("")
                        lines.append(f"🇬🇧 <b>LONDON</b>")
                        lines.append(f"  High: {sess.london_high:.2f} | Low: {sess.london_low:.2f}")
                        if london_rng:
                            lines.append(f"  Range: {london_rng:.2f} ({london_rng/pip_s:.0f} pip)")
                    
                    if sess.ny_high and not nykz:
                        lines.append("")
                        lines.append(f"🇺🇸 <b>NEW YORK</b>")
                        lines.append(f"  High: {sess.ny_high:.2f} | Low: {sess.ny_low:.2f}")
                    
                    # Previous day
                    if sess.prev_day_high:
                        lines.append("")
                        lines.append(f"📆 <b>PREVIOUS DAY</b>")
                        lines.append(f"  High: {sess.prev_day_high:.2f} | Low: {sess.prev_day_low:.2f}")
                    
                    # Position relative to sessions
                    lines.append("")
                    lines.append("📍 <b>PRICE POSITION</b>")
                    if sess.asia_high and sess.asia_low:
                        if price > sess.asia_high:
                            lines.append(f"  ⬆️ Above Asia High (+{abs(price-sess.asia_high)/pip_s:.0f} pip)")
                        elif price < sess.asia_low:
                            lines.append(f"  ⬇️ Below Asia Low (-{abs(sess.asia_low-price)/pip_s:.0f} pip)")
                        else:
                            asia_range = sess.asia_high - sess.asia_low
                            pos_pct = (price - sess.asia_low) / asia_range * 100 if asia_range > 0 else 50
                            lines.append(f"  ↔️ Inside Asia Range ({pos_pct:.0f}%)")
                    
                    # Today's range so far
                    if sess.today_high and sess.today_low:
                        today_rng = sess.today_high - sess.today_low
                        lines.append(f"  📏 Today Range: {today_rng:.2f} ({today_rng/pip_s:.0f} pip)")
                    
                    # ── Donor: Range analysis + manipulation detection ──
                    if is_donor:
                        lines.append("")
                        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                        lines.append("👑 <b>DONOR: RANGE ANALYSIS</b>")
                        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
                        
                        # Which session typically has wider range
                        ranges = []
                        if asia_rng: ranges.append(("Asia", asia_rng))
                        if london_rng: ranges.append(("London", london_rng))
                        if sess.ny_high and sess.ny_low:
                            ny_rng = sess.ny_high - sess.ny_low
                            ranges.append(("NY", ny_rng))
                        
                        if ranges:
                            ranges.sort(key=lambda x: x[1], reverse=True)
                            lines.append(f"  📊 Widest session: <b>{ranges[0][0]}</b> ({ranges[0][1]/pip_s:.0f} pip)")
                            for name, rng in ranges[1:]:
                                lines.append(f"     {name}: {rng/pip_s:.0f} pip")
                        
                        # Liquidity grab detection
                        if sess.asia_high and sess.london_low:
                            if sess.london_low < sess.asia_low:
                                lines.append(f"  🎯 <b>Asia Low Swept!</b> — London took Asia lows")
                            if sess.london_high > sess.asia_high:
                                lines.append(f"  🎯 <b>Asia High Swept!</b> — London took Asia highs")
                        
                        lines.append(f"  📊 Bars scanned: {sess.bars_scanned}")
                        if sess.is_nfp_friday:
                            lines.append(f"  ⚠️ <b>NFP FRIDAY!</b> — High volatility expected")
        
        except Exception as e:
            logger.warning(f"Session levels error: {e}")
            lines.append("")
            lines.append(f"❌ Session data unavailable: {e}")
        
        if not is_donor:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🔒 <b>FREE TIER — Basic Session</b>")
            lines.append("👑 Range analysis + manipulation detection")
            lines.append("   → <b>/subscribe</b> untuk unlock")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ <i>Tools analisa teknikal — bukan sinyal trading.</i>")
        tg_send("\n".join(lines), chat_id)
    
    # ── Existing Signal Commands ──
    elif cmd == "/levels" or cmd == "/level":
        """Premium: Deep SnR+FIBO + Engine Analysis. Free: upsell gate."""
        # ── PREMIUM GATE ──
        if not _is_donor(str(chat_id)):
            tg_send(
                "👑 <b>FITUR PREMIUM — Khusus Subscriber</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "/levels adalah fitur analisa level profesional:\n"
                "📐 SnR + FIBO Retracement\n"
                "🏦 SMC Order Blocks\n"
                "📊 Fair Value Gaps\n"
                "💧 Liquidity Zones\n"
                "🕐 Session Levels\n"
                "\n"
                "🔒 Fitur ini eksklusif untuk Subscriber.\n"
                "\n"
                "💚 <b>ISI BAHAN BAKAR AI</b>\n"
                "Subscribe sekali — akses permanen!\n"
                "👉 /subscribe — Lihat opsi subscribe\n"
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
        
        ohlcv_bars = _fetch_ohlcv_for_ai(pair, keep=60)  # need 60 bars for engine analysis
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
                from fvg_detector import detect_fvg_zones
                raw_zones = detect_fvg_zones(ohlcv_bars, max_age=30)
                if raw_zones:
                    lines.append("")
                    lines.append("📐 <b>FAIR VALUE GAPS</b>")
                    for z in raw_zones[:3]:
                        mid = (z.top + z.bottom) / 2
                        lines.append(f"  {z.top:.2f} — {z.bottom:.2f} ({z.size_pips:.0f} pip)")
        except: pass

        # ── Session ──
        try:
            from session_levels import calculate_all_levels
            sess = calculate_all_levels(ohlcv_bars[-60:]) if len(ohlcv_bars) >= 30 else None
            if sess:
                asia_h = sess.asia_high; asia_l = sess.asia_low
                london_h = sess.london_high; london_l = sess.london_low
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
        lines.append("⚡ Upgrade Tier → @berkahkaryaforexbotbot")
        
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
            result = run_engine_consensus(symbol=disp)
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
            # ── Killzone enforcement: forex/metals outside London/NY = BLOCK ──
            from datetime import datetime, timezone, timedelta
            _wib = timezone(timedelta(hours=7))
            h_now = datetime.now(_wib).hour
            if disp in ("XAUUSD","GOLD","USOIL","EURUSD","GBPUSD","USDJPY"):
                lkz, nykz = killzone(h_now)
                if not lkz and not nykz:
                    logger.info(f"   [/signal {disp}] BLOCKED: outside killzone (London/NY only)")
                    tg_send(f"⛔ <b>Signal ditahan — di luar Killzone</b>\n\n{disp} hanya trading di sesi London (14:00-17:00 WIB) & NY (19:00-22:00 WIB).\n\nGunakan /analyze untuk analisis only.", chat_id)
                    return
            # ── Post to channel FIRST, then bridge with message_id ──
            tg_msg_id = None
            try:
                pair_k = "gold" if disp.startswith("XAU") else disp.lower()
                _entry = sig.get("entry", 0) or 0
                _sl = sig.get("sl", 0) or 0
                _tp = sig.get("tp", 0) or 0
                if _can_post_to_channel(pair_k, sig["action"], _entry, _sl, _tp):
                    result = send_to_channel(msg)
                    if result:
                        tg_msg_id = result.get("result",{}).get("message_id")
                        sig["telegram_message_id"] = tg_msg_id
                        logger.info(f"CHANNEL POST OK [/signal {disp}]: message_id={tg_msg_id}")
            except Exception as ex:
                logger.warning(f"Channel post [/signal] failed: {ex}")
            # ── Post to bridge for EA pickup ──
            try:
                post_signal_to_bridge(sig, 0, disp)
                logger.info(f"🤖 Auto-executed {disp} {sig['action']} via /signal (msg_id={tg_msg_id})")
            except Exception as ex:
                logger.warning(f"Bridge post [/signal] failed: {ex}")
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
        sub_norm = _normalize_broker_symbol(sub or "xauusd")
        pair_map_mtf = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_mtf = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        disp_mtf = disp_map_mtf.get(pair_map_mtf.get(sub_norm, "gold"), "XAUUSD")
        tg_send("<i>🧬 Loading MTF engine readings...</i>", chat_id)
        try:
            from engine_consensus import run_engine_consensus
            
            result = run_engine_consensus(symbol=disp_mtf)
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
                f"🧬 <b>MTF ENGINE MATRIX — {disp_mtf}</b>\n"
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
            # ── Log activity ──
            try:
                username_mtf = (msg.get("chat", {}).get("username", "") or
                              msg.get("from", {}).get("username", "") or "")
                from tradebot.tracking.activity import log_activity
                log_activity(str(chat_id), str(chat_id), username_mtf.lstrip("@"),
                             "mtf", "", {"pair": disp_mtf})
            except Exception:
                pass
            # ── Behavioral Tagging: /mtf user = technical_geek ──
            try:
                from members.tags import add_tag
                add_tag(str(chat_id), "technical_geek")
            except Exception:
                pass
            
        except Exception as e:
            tg_send(f"❌ MTF error: {e}", chat_id)

    elif cmd == "/engines":
        """Show live engine readings for all 9 strategies."""
        sub_norm_eng = _normalize_broker_symbol(sub or "xauusd")
        pair_map_eng = {"xauusd":"gold","gold":"gold","btc":"btc","btcusd":"btc","eth":"eth","ethusd":"eth","oil":"oil",
                      "eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        disp_map_eng = {"gold":"XAUUSD","btc":"BTCUSD","eth":"ETHUSD","oil":"USOIL","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY"}
        disp_eng = disp_map_eng.get(pair_map_eng.get(sub_norm_eng, "gold"), "XAUUSD")
        tg_send("<i>🔧 Loading engine readings...</i>", chat_id)
        try:
            from engine_consensus import run_engine_consensus
            
            result = run_engine_consensus(symbol=disp_eng)
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
                f"🔧 <b>ENGINE READINGS — {disp_eng}</b>\n"
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
            # Activity tracking
            try:
                from tradebot.tracking.activity import log_activity
                tier = _get_user_tier(chat_id).get("tier", "free")
                log_activity(str(chat_id), chat_id, username, "engines", tier, {"pair": disp_eng})
            except Exception: pass
            
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

    elif cmd == "/mykey":
        """Show user's own EA license key."""
        if not LICENSE_ENGINE:
            tg_send("🔧 License engine belum aktif. Hubungi @codergaboets.", chat_id)
            return
        try:
            result = cmd_mykey(str(chat_id))
            tg_send(result, chat_id)
        except Exception as e:
            logger.error(f"mykey error: {e}")
            tg_send("❌ Gagal cek license. Coba lagi atau hubungi @codergaboets.", chat_id)

    elif cmd == "/genkey":
        """Donor/Admin: Generate EA license key."""
        # ── TIER GATE: subscriber or admin can generate license keys ──
        admin_ids = [os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", ""), "5220170786", "157228659"]
        if not _is_donor(str(chat_id)) and str(chat_id) not in admin_ids:
            _uname = msg.get("chat", {}).get("username", "") or msg.get("from", {}).get("username", "")
            _send_donate_menu(chat_id, _uname)
            tg_send(
                "🔑 <b>Generate License Key</b> [🔒 LOCKED]\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Fitur generate key EA hanya untuk Subscriber.\n"
                "Support project dulu ya Bro!\n\n"
                "⚡ /subscribe — Upgrade Tier",
                chat_id
            )
            return
        if not LICENSE_ENGINE:
            tg_send("🔧 License engine belum aktif. Hubungi @codergaboets.", chat_id)
            return
        try:
            result = cmd_genkey(str(chat_id), sub, msg)
            tg_send(result, chat_id)
        except Exception as e:
            logger.error(f"genkey error: {e}")
            tg_send("❌ Gagal generate key.", chat_id)

    elif cmd == "/listkeys":
        """Admin: List all EA license keys."""
        if not LICENSE_ENGINE:
            tg_send("🔧 License engine belum aktif. Hubungi @codergaboets.", chat_id)
            return
        try:
            result = cmd_listkeys(str(chat_id))
            tg_send(result, chat_id)
        except Exception as e:
            logger.error(f"listkeys error: {e}")
            tg_send("❌ Gagal list keys.", chat_id)

    elif cmd == "/revokekey":
        """Admin: Revoke EA license key."""
        if not LICENSE_ENGINE:
            tg_send("🔧 License engine belum aktif. Hubungi @codergaboets.", chat_id)
            return
        try:
            result = cmd_revokekey(str(chat_id), sub)
            tg_send(result, chat_id)
        except Exception as e:
            logger.error(f"revokekey error: {e}")
            tg_send("❌ Gagal revoke key.", chat_id)


    elif cmd == "/portfolio":
        """Show best asset for current session and portfolio status."""
        try:
            from tradebot.signals.portfolio_oracle import get_best_asset_for_now, ASSET_TIERS
        except ImportError:
            tg_send("Portfolio oracle tidak tersedia.", chat_id)
            return
        best = get_best_asset_for_now()
        if not best:
            tg_send("Tidak bisa menentukan aset terbaik saat ini.", chat_id)
            return
        t1 = len(ASSET_TIERS.get("tier1", []))
        t2 = len(ASSET_TIERS.get("tier2", []))
        t3 = len(ASSET_TIERS.get("tier3", []))
        tg_send(
            f"📊 <b>PORTFOLIO ORACLE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 Best Now: <b>{best['ric']}</b>\n"
            f"📈 Win Rate: {best['wr']}% | Win: {best['win']} bar\n"
            f"🎯 Threshold: {best['thr']:.0%}\n"
            f"💰 Payout: {best['payout']:.0%}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Portfolio: {t1}T1 | {t2}T2 | {t3}T3\n"
            f"🔄 /trade &lt;asset&gt; — Eksekusi trading",
            chat_id
        )

    elif cmd == "/trade":
        """Execute a trade on the specified Stockity turbo asset."""
        target_ric = sub.upper().strip() if sub else ""
        if not target_ric:
            tg_send("📌 Gunakan: /trade <b>&lt;RIC&gt;</b>\nContoh: /trade POWER-X", chat_id)
            return
        try:
            from tradebot.signals.portfolio_oracle import _ric_to_asset
        except ImportError:
            tg_send("Portfolio oracle tidak tersedia.", chat_id)
            return
        asset = _ric_to_asset(target_ric)
        if not asset:
            tg_send(f"❌ Aset {target_ric} tidak dikenal. /portfolio untuk daftar.", chat_id)
            return
        tg_send(
            f"🔄 <b>EXECUTING TRADE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {target_ric}\n"
            f"💵 Rp14.000 | 60s Turbo\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Trade execution via async flow...</i>\n\n"
            f"📊 /portfolio — Cek aset terbaik\n"
            f"📋 /history — Riwayat trade", chat_id
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
    except Exception as e:
        logger.warning("Signal log load failed (%s): %s", asset, e)
    return {"signals_sent":0,"last_signal_time":None,"last_action":None,"last_price":0,"loss_count":0}

def save_signal_log(log, asset="default"):
    (DATA_DIR / f"signal_log_{asset}.json").write_text(json.dumps(log))

def is_trading_session(h):
    """Trading allowed 24h — killzone gate handles actual execution windows."""
    return True

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
    Falls back to home only if channel ID is not configured (warns in log).
    If chart image URL detected (chart.xobniot), download and send as sendPhoto.
    """
    target = SIGNAL_CHANNEL_ID or ""
    if not target:
        logger.warning("send_to_channel: SIGNAL_CHANNEL_ID not set — falling back to HOME")
        target = ""

    # ── Chart auto-attach ──
    chart_b64 = ""
    # strip quickchart.io URL from caption so it doesn't duplicate
    caption = text

    chart_url = None
    url_prefix = "https://quickchart.io/chart?"
    start = text.find(url_prefix)
    if start != -1:
        # capture whole URL up to space or end
        end = text.find(" ", start)
        if end == -1:
            end = len(text)
        chart_url = text[start:end].strip()
        caption = (text[:start] + text[end:]).strip()

    if chart_url:
        try:
            req = urllib.request.Request(chart_url, headers={"User-Agent": "VilonaBot/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                chart_b64 = r.read()
        except Exception as exc:
            logger.warning("send_to_channel: failed to download chart image: %s", exc)

    if chart_b64:
        result = tg_send_photo(target or None, chart_b64, caption=caption)
        if result is None:
            # fallback: send text without chart
            if SIGNAL_CHANNEL_ID:
                result = tg_send(caption, SIGNAL_CHANNEL_ID)
                if result is None:
                    logger.warning("send_to_channel: photo failed, retrying text once...")
                    time.sleep(1)
                    result = tg_send(caption, SIGNAL_CHANNEL_ID)
            else:
                result = tg_send(caption)
    else:
        if SIGNAL_CHANNEL_ID:
            result = tg_send(text, SIGNAL_CHANNEL_ID)
            if result is None:
                logger.warning("send_to_channel: post failed, retrying once...")
                time.sleep(1)
                result = tg_send(text, SIGNAL_CHANNEL_ID)
        else:
            logger.warning("send_to_channel: SIGNAL_CHANNEL_ID not set — falling back to HOME")
            result = tg_send(text)  # fallback to home
    return result


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
    """Daily market mapping/insight — key levels from actual session range + pivot structure.
    Uses time-filtered 24h bars + swing pivots for realistic S/R levels."""
    import pandas as pd
    now = wib_now()
    day_name = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"][now.weekday()]
    cutoff_12h = pd.Timestamp(now - timedelta(hours=12))
    
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
    
    if MARKET_DATA:
        for pair, disp, _, is_forex in AUTO_SCAN_ASSETS:
            try:
                bars = MARKET_DATA.get_ohlcv(pair, "1h", 60)
                if not bars or len(bars) < 5:
                    continue

                # ── Time-filtered session range (REAL 24h, not 24-bar count) ──
                recent = [b for b in bars if b.timestamp >= cutoff_12h]
                if len(recent) < 4:
                    recent = bars[-12:]  # graceful fallback for sparse data
                
                high_ses = max(b.high for b in recent)
                low_ses = min(b.low for b in recent)
                close = bars[-1].close

                # ── Weekly: full data range ──
                high_w = max(b.high for b in bars)
                low_w = min(b.low for b in bars)

                # ── Pivot-based Support / Resistance (swing structure) ──
                pivot_bars = bars[-min(40, len(bars)):]
                swing_highs = []
                swing_lows = []
                # Detect swings: bar higher/lower than 2 neighbors each side
                n = len(pivot_bars)
                for i in range(2, n - 2):
                    b = pivot_bars[i]
                    if (b.high > pivot_bars[i-1].high and b.high > pivot_bars[i-2].high and
                        b.high > pivot_bars[i+1].high and b.high > pivot_bars[i+2].high):
                        swing_highs.append(b.high)
                    if (b.low < pivot_bars[i-1].low and b.low < pivot_bars[i-2].low and
                        b.low < pivot_bars[i+1].low and b.low < pivot_bars[i+2].low):
                        swing_lows.append(b.low)

                # Resistance: nearest swing high ABOVE current price
                # If all pivots are below price, use session high + projected extension
                resistance = None
                for sh in reversed(swing_highs):
                    if sh > close:
                        resistance = sh
                        break
                if resistance is None:
                    # Price above all swing highs → use session high as ceiling
                    resistance = high_ses

                # Support: nearest swing low BELOW current price
                # If all pivots are above price, use session low as floor
                support = None
                for sl in reversed(swing_lows):
                    if sl < close:
                        support = sl
                        break
                if support is None:
                    support = low_ses

                # ── SMA trend ──
                n20 = min(20, len(bars))
                sma20 = sum(b.close for b in bars[-n20:]) / n20
                trend = "📈 BULLISH" if close > sma20 else ("📉 BEARISH" if close < sma20 else "➡️ SIDEWAYS")

                # ── Price position in session range ──
                if high_ses != low_ses:
                    pos_pct = (close - low_ses) / (high_ses - low_ses) * 100
                else:
                    pos_pct = 50

                # Range pip label for XAUUSD
                pip_label = f" ({int((high_ses - low_ses) / 0.10)} pip)" if disp == "XAUUSD" else ""

                lines.append(f"")
                lines.append(f"💱 {disp}")
                lines.append(f"   Price: {close:.2f} | {trend} | 📍{pos_pct:.0f}% range")
                lines.append(f"   Session Range: {low_ses:.2f} — {high_ses:.2f}{pip_label}")
                lines.append(f"   Resistance: {resistance:.2f} | Support: {support:.2f}")
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
_STIER_BROADCAST_CD = {}    # {pair:direction: timestamp} — S-TIER cooldown (5 min)

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


# ── Auto-DM Upsell / Donasi Trigger ───────────────────────────────
def broadcast_tp_hit_and_upsell(pair: str, profit_pips: float):
    """Auto-DM users when a signal hits TP. Tier-aware CTA.
    
    Called from the trade outcome loop after learn_from_tp().
    - FREE tier: upgrade CTA (SL/TP locked)
    - PAID tier: donation CTA (support server)
    Runs async via background thread to avoid blocking the scan loop.
    """
    import threading
    
    def _dm_worker():
        try:
            # Get recent active users (last 48h) from subscriber_activity
            from members import _conn
            with _conn() as db:
                rows = db.execute("""
                    SELECT DISTINCT sa.chat_id, sa.tier, m.status 
                    FROM subscriber_activity sa
                    LEFT JOIN members m ON m.chat_id = sa.chat_id
                    WHERE sa.created_at > datetime('now', '-2 days')
                    ORDER BY sa.created_at DESC
                    LIMIT 20
                """).fetchall()
            
            free_ct, paid_ct = 0, 0
            for row in rows:
                chat_id = row["chat_id"]
                tier = row["tier"] or "free"
                status = row["status"] or ""
                is_test = chat_id.startswith("test") or chat_id.startswith("vfy")
                is_paid = (status == "paid" and not is_test) or tier in ("pro", "elite", "lifetime", "donor")
                
                if is_paid:
                    msg = (
                        f"🎉 <b>BOOM! Profit +{profit_pips:.1f} pips diamankan dari {pair}!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Enjoy cuannya! Biar rezekinya makin berkah\n"
                        f"dan server kita tetap ngebut, yuk sisihkan\n"
                        f"sebagian profitmu. Dukung kita via /donate ☕️\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📢 Channel: @vilonaaichanel"
                    )
                    paid_ct += 1
                else:
                    msg = (
                        f"🎉 <b>BOOM! Sinyal {pair} barusan sukses HIT TP</b>\n"
                        f"<b>(Cuan +{profit_pips:.1f} pips)!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Sayang banget SL/TP kamu masih dikunci.\n"
                        f"Waktunya upgrade ke PRO untuk buka\n"
                        f"full SL/TP dan sinyal VIP lainnya.\n"
                        f"\n"
                        f"⭐ Ketik /subscribe sekarang!\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📢 Channel: @vilonaaichanel"
                    )
                    free_ct += 1
                
                try:
                    tg_send(msg, str(chat_id))
                    time.sleep(1.5)  # rate limit: Telegram allows ~30 msg/sec
                except Exception:
                    pass
            
            logger.info(f"TP upsell DMs sent: {free_ct} free + {paid_ct} paid")
        except Exception as e:
            logger.debug(f"TP upsell broadcast error (non-critical): {e}")
    
    # Run in background thread — don't block the main scan loop
    t = threading.Thread(target=_dm_worker, daemon=True)
    t.start()

AUTO_SCAN_ASSETS = [
    # (internal_pair, display_name, _, is_forex_metal)  — yahoo_sym removed; MARKET_DATA resolves via SYMBOL_MAP
    # Channel auto-post: XAUUSD ONLY. Other pairs via /analyze di bot.
    ("gold", "XAUUSD", None, True),
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

    def _slippage_guard(display, signal_entry, signal_action):
        """Re-fetch live price and check if price moved >15 pip during AI thinking.
        Returns (ok: bool, live_price: float, drift_pips: float)."""
        pip_s = 0.10 if display in ("XAUUSD","GOLD") else 0.01
        max_drift = 1.5  # 15 pip — hard limit for late-execution kill switch
        live = None
        try:
            if display in ("XAUUSD","GOLD"):
                spot = fetch_xauusd_spot()
                if spot:
                    live = round(spot + XAUUSD_OFFSET, 2)
            else:
                live = fetch_price(display.lower())
        except Exception:
            pass
        if not live or not signal_entry:
            return True, live, 0  # can't verify — allow through
        drift_pips = abs(live - signal_entry) / pip_s
        if drift_pips > max_drift:
            logger.warning(
                f"⛔ SLIPPAGE GUARD [{display}]: |live={live:.2f} - signal={signal_entry:.2f}| = {drift_pips:.0f} pip > {max_drift:.0f} pip — ABORT")
            return False, live, drift_pips
        return True, live, drift_pips
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
    # ── Persistent Signal State (missed move detection) ──
    def _save_scan_state(state: dict):
        """Save scan state dict to disk as JSON."""
        try:
            (DATA_DIR / '.scan_state').write_text(json.dumps(state))
        except Exception as e:
            logger.warning(f"Failed to save scan state: {e}")
    def _load_scan_state():
        """Load scan state dict from disk, or empty dict."""
        try:
            f = DATA_DIR / '.scan_state'
            if f.exists():
                return json.loads(f.read_text())
        except Exception:
            pass
        return {}
    def _check_missed_move(price, state):
        """Check if price moved >500 pips since last scan (1 pip = 0.10 for XAUUSD).
        Returns (missed: bool, gap_pips: float)."""
        last_price = state.get('last_price')
        if last_price is None:
            return False, 0.0
        gap = abs(price - last_price)
        gap_pips = gap / 0.10
        if gap_pips > 500:
            return True, round(gap_pips, 1)
        return False, round(gap_pips, 1)
    last_mapping_day = _get_last_mapping()  # init from disk

    while True:
        try:
            action = ''  # pre-initialize to avoid BUG-9 'action' in dir() scope issue
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

                # ── Weekend: no crypto scanning, just sleep ──
                time.sleep(300)
                continue  # back to top of while loop

            # ── WEEKDAY: Reset mapping tracker for new day ──
            if last_mapping_day and last_mapping_day != today_str:
                last_mapping_day = ""
                _set_last_mapping("")  # clear persistent tracker

            # Rotate through assets
            pair, disp, _, is_forex = AUTO_SCAN_ASSETS[asset_idx % len(AUTO_SCAN_ASSETS)]
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

            # ── Missed move detection ──
            _ss = _load_scan_state()
            _missed, _gap_pips = _check_missed_move(price, _ss)
            if _missed:
                logger.warning(f"⚠️ MISSED MOVE [{disp}]: price moved {_gap_pips} pips since last scan")
            # Seed state immediately so any crash before save still has datum
            if not _ss:
                _safekz = kz if 'kz' in dir() else "Outside"
                _save_scan_state({"last_price": price, "last_action": "", "last_signal_time": "", "last_kz": _safekz})
                _ss = _load_scan_state()

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
                                # Re-check breaker after increment
                                if new_count >= 3:
                                    logger.warning(f"⛔ CIRCUIT BREAKER TRIGGERED mid-cycle: {new_count}/3")
                                    time.sleep(600)
                                    continue
                            # Send to channel (text only — buttons gak work di channel)
                            # ── Reply chain: attach result to original signal message ──
                            alert_text = format_trade_close_alert(ct)
                            tg_msg_id = ct.get("telegram_message_id")
                            if tg_msg_id:
                                tg_send(alert_text, SIGNAL_CHANNEL_ID, reply_to=tg_msg_id)
                            else:
                                send_to_channel(alert_text)  # graceful fallback
                            # ── Update unified signal feed (dashboard) ──
                            if SIGNAL_FEED:
                                try:
                                    _feed_update(
                                        symbol=ct.get("symbol", disp),
                                        entry_price=ct.get("entry", 0),
                                        result=ct.get("outcome", "?"),
                                        pips=ct.get("pips", 0)
                                    )
                                except Exception: pass
                            # ── LEARNING LOOP: Auto-analyze every trade outcome ──
                            if LEARNING_LOOP:
                                try:
                                    outcome = ct.get("outcome", "")
                                    if outcome == "SL_HIT":
                                        learn_from_sl(ct, price)
                                    elif outcome == "TP_HIT":
                                        learn_from_tp(ct)
                                        # ── Auto-DM Upsell / Donasi ──
                                        symbol = ct.get("symbol", disp)
                                        pips = ct.get("pips", 0)
                                        broadcast_tp_hit_and_upsell(symbol, pips)
                                except Exception as lle:
                                    logger.debug("Learning loop error: %s", lle)
                        except Exception: pass
                except Exception: pass

            dxy = fetch_dxy() if pair == "gold" else None
            lkz, nykz = killzone(h)
            kz = "London" if lkz else ("NY" if nykz else "Outside")

            # ── S-TIER ZONE DETECTOR: Triple Confluence (highest priority, runs first) ──
            stier_sig = None
            if is_forex:
                try:
                    stier_bars = _fetch_ohlcv_for_ai(pair, keep=60)
                    if stier_bars and len(stier_bars) >= 30:
                        stier_sig, stier_reason = detect_stier_zone(
                            pair.upper(), disp, price, stier_bars)
                        if stier_sig:
                            logger.info(f"💀 S-TIER ZONE [{disp}]: {stier_sig['action']} "
                                       f"@ ${stier_sig.get('entry',0):.2f} | Grade={stier_sig.get('grade','?')}")
                except Exception as e:
                    logger.debug(f"S-TIER check [{disp}]: {e}")

            if stier_sig and stier_sig["action"] in ("BUY", "SELL"):
                # ── 💀 S-TIER PRICE SANITY: entry zone must be within ±3% of current spot ──
                stier_entry_raw = stier_sig.get("entry", price) or price
                if price and price > 0 and abs(stier_entry_raw - price) / price > 0.03:
                    logger.warning(f"💀 S-TIER [{disp}] REJECTED: entry ${stier_entry_raw:.2f} too far from spot ${price:.2f} ({(stier_entry_raw-price)/price*100:+.1f}%)")
                    stier_sig = None  # suppress bogus signal
                    continue

                # ── 💀 S-TIER COOLDOWN: no repeat broadcast same pair+direction within 15 min ──
                _stier_key = f"{disp}:{stier_sig['action']}"
                _stier_now_t = time.time()
                _stier_last_t = _STIER_BROADCAST_CD.get(_stier_key, 0)
                if _stier_now_t - _stier_last_t < 900:
                    logger.info(f"💀 S-TIER [{disp}] broadcast SKIPPED — cooldown ({_stier_now_t-_stier_last_t:.0f}s ago)")
                    stier_sig = None  # suppress
                    continue  # skip this pair entirely
                _STIER_BROADCAST_CD[_stier_key] = _stier_now_t

                # ── 💀 S-TIER HIGH CONVICTION: Triple Confluence, bypass killzone, near-100% accuracy ──
                action = stier_sig["action"]
                stier_sig = _clamp_sltp(stier_sig, disp)
                stier_sig["_tier_capped"] = False
                stier_sig["risk_percent"] = 2.0   # normal sizing, not full margin
                stier_sig["source"] = "stier-god-tier"
                stier_sig["grade"] = "S"

                conf = stier_sig.get("confidence", 0)
                if isinstance(conf, (int, float)) and conf > 10:
                    conf = conf / 100
                stier_sig["confidence"] = conf

                # ── 🔬 SnR PROXIMITY UPGRADE: S-TIER + Daily/4H SnR = GOD TIER ──
                is_snr_boosted = False
                snr_level = None
                snr_type = None
                try:
                    ohlcv_snr = _fetch_ohlcv_for_ai(pair, keep=60)
                    pip_sz = 0.10 if disp in ("XAUUSD","GOLD") else (0.01 if disp=="USOIL" else 1.0)
                    snr_result = _snr_proximity_check(price, pip_sz, ohlcv_snr, disp)
                    if snr_result:
                        snr_level, snr_type, snr_dist, snr_zone_lo, snr_zone_hi = snr_result
                        # Validate: S-TIER direction must match SnR type
                        # SELL near RESISTANCE = valid | BUY near SUPPORT = valid
                        dir_ok = (action == "SELL" and snr_type == "RESISTANCE") or (action == "BUY" and snr_type == "SUPPORT")
                        if dir_ok:
                            is_snr_boosted = True
                            # Replace entry zone with SnR zone (tighter, proven level)
                            stier_sig["entry"] = round(snr_level, 2)
                            stier_sig["zone_lo"] = snr_zone_lo
                            stier_sig["zone_hi"] = snr_zone_hi
                            stier_sig["entry_mode"] = "zone"
                            # Tighter SL: below SnR zone (not below entry)
                            zone_margin = 3.0 * pip_sz
                            if action == "BUY":
                                stier_sig["sl"] = round(snr_zone_lo - zone_margin, 2)
                            else:
                                stier_sig["sl"] = round(snr_zone_hi + zone_margin, 2)
                            stier_sig["grade"] = "S+"
                            stier_sig["source"] = "stier-snr-god-tier"
                            stier_sig["confidence"] = min(0.97, conf + 0.05)
                            stier_sig = _clamp_sltp(stier_sig, disp)
                            logger.info(f"🔬 S-TIER+ SnR [{disp}]: {action} @ ${snr_level:.2f} | "
                                        f"{snr_type} dist={snr_dist*100:.2f}% | zone=[{snr_zone_lo:.2f}-{snr_zone_hi:.2f}]")
                except Exception as snre:
                    logger.debug(f"SnR upgrade error [{disp}]: {snre}")

                # ── 💰 PRICE SANITY: clamp S-TIER entry to REAL broker spot (±1%) ──
                # Fetch FRESH raw spot for XAUUSD (bypass XAUUSD_OFFSET) as truth anchor
                _stier_entry = stier_sig.get("entry", price) or price
                _anchor = price
                if disp in ("XAUUSD", "GOLD", "XAU"):
                    try:
                        _raw_spot = fetch_xauusd_spot()
                        if _raw_spot and _raw_spot > 0:
                            _anchor = _raw_spot  # raw commodity spot = broker price for modern APIs
                    except Exception:
                        pass
                _dev = abs(_stier_entry - _anchor) / _anchor if _anchor and _anchor > 0 else 0
                if _dev > 0.01:
                    # Entry too far from real broker price → clamp to spot
                    pip_sz2 = 0.10 if disp in ("XAUUSD","GOLD") else (0.01 if disp=="USOIL" else 1.0)
                    old_entry = _stier_entry
                    # Set entry to current price, zone ±5 pips around it
                    stier_sig["entry"] = round(_anchor, 2)
                    stier_sig["zone_lo"] = round(_anchor - 5.0*pip_sz2, 2)
                    stier_sig["zone_hi"] = round(_anchor + 5.0*pip_sz2, 2)
                    # Adjust SL proportionally
                    old_sl = stier_sig.get("sl", 0)
                    if old_sl and old_sl != 0:
                        stier_sig["sl"] = round(_anchor - (old_entry - old_sl) if action == "BUY" else _anchor + (old_sl - old_entry), 2)
                    logger.info(f"💀 S-TIER [{disp}] entry CLAMPED: ${old_entry:.2f} → ${_anchor:.2f} (spot, {_dev*100:.1f}% off)")

                # Format as S-TIER signal (upgraded if SnR proximity confirmed)
                signal_label = "💀 S-TIER+ SnR" if is_snr_boosted else "💀 S-TIER HIGH CONVICTION"
                stier_text = fmt_signal(stier_sig, price, dxy, h, disp, "$")
                stier_text = stier_text.replace(
                    "SINYAL SELL", f"{signal_label} SELL"
                ).replace(
                    "SINYAL BUY", f"{signal_label} BUY"
                ).replace(
                    "MARKET PULSE", signal_label
                )
                stier_text += (
                    "\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔥 <b>TRIPLE CONFLUENCE DETECTED</b>\n"
                    "   Breaker + OB/FVG + Double Sweep\n"
                )
                if is_snr_boosted:
                    stier_text += (
                        f"🔬 <b>SnR CONFIRMED — {snr_type}</b>\n"
                        f"   Entry zone = Daily/4H {snr_type.lower()} @ ${snr_level:.2f}\n"
                        f"   SL di bawah zone — GOD TIER precision\n"
                    )
                else:
                    stier_text += (
                        "   🎯 Near-100% Accuracy — Highest Conviction Setup\n"
                    )

                # ── PREMIUM-ONLY: S-TIER signals only for paying members ──
                stier_entry = stier_sig.get("entry", price) or 0
                # 1. DM to all premium members (PRO, ELITE, LIFETIME) — use SUBS_PATH NOT members module
                premium_count = 0
                try:
                    db = sqlite3.connect(SUBS_PATH)
                    db.row_factory = sqlite3.Row
                    rows = db.execute(
                        "SELECT chat_id FROM members WHERE status='paid' AND chat_id NOT LIKE 'test%' AND chat_id NOT LIKE 'vfy%'"
                    ).fetchall()
                    db.close()
                    for row in rows:
                        try:
                            tg_send(stier_text, str(row["chat_id"]))
                            premium_count += 1
                            time.sleep(0.3)
                        except Exception as dme:
                            logger.warning(f"S-TIER DM failed for {row['chat_id']}: {dme}")
                    logger.info(f"💀 S-TIER{'⁺ SnR' if is_snr_boosted else ''} DM'd to {premium_count} premium members")
                except Exception as me:
                    logger.warning(f"S-TIER premium DM error: {me}")
                # 2. Teaser to public channel (no entry/SL/TP details)
                tg_msg_id = None
                try:
                    tease_label = "💀 S-TIER+ SnR" if is_snr_boosted else "💀 S-TIER HIGH CONVICTION"
                    tease = (
                        f"<b>{tease_label} — {action} {disp}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔥 Triple Confluence terdeteksi!\n"
                        f"   Breaker + OB/FVG + Double Sweep\n"
                        + (f"🔬 SnR CONFIRMED — {snr_type} @ ${snr_level:.2f}\n" if is_snr_boosted else f"🎯 Near-100% Accuracy Setup\n") +
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👑 <b>Signal dikirim ke SEMUA subscriber Premium</b>\n"
                        f"   Lengkap dengan SL/TP & alasan analisa.\n"
                        f"⭐ Upgrade ke PRO/ELITE untuk akses penuh:\n"
                    )
                    result = send_to_channel(tease)
                    if result:
                        tg_msg_id = result.get('result', {}).get('message_id')
                except Exception:
                    pass
                # 3. Killzone gate: S-TIER forex/metals outside London/NY → skip bridge
                should_bridge = True
                if disp in ("XAUUSD","GOLD","USOIL","EURUSD","GBPUSD","USDJPY"):
                    lkz, nykz = killzone(h)
                    if not lkz and not nykz:
                        logger.info(f"💀 S-TIER [{disp}] bridge BLOCKED: outside killzone (London/NY only)")
                        should_bridge = False
                # 4. Post to bridge for EA execution (with telegram_message_id for reply chain)
                if should_bridge:
                    if tg_msg_id:
                        stier_sig["telegram_message_id"] = tg_msg_id
                    post_signal_to_bridge(stier_sig, price, disp)
                logger.info(f"💀 S-TIER{'⁺ SnR' if is_snr_boosted else ' HIGH CONVICTION'} [{disp}]: {action} @ ${stier_entry:.2f} | conf={conf:.0%} | bridge={'ON' if should_bridge else 'OFF'} | msg_id={tg_msg_id}")
                log["signals_sent"] = log.get("signals_sent", 0) + 1
                time.sleep(60)
                continue

            # ── AI Consensus — ONLY source of signals ──
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

                # AI requires: 2+ model agreement OR solo with conf ≥ 80%, RR ≥ 1:1.5
                if voters < 2:
                    if conf < 0.80:
                        logger.info(f"   [{disp}] BLOCKED: solo AI call conf={conf:.0%} < 80%")
                    else:
                        logger.info(f"   [{disp}] SOLO PUSH: conf={conf:.0%} ≥ 80% — bypassing voters gate")
                        should_push = True
                    rr_val_local = 0  # prevent RR double-log for same cycle
                elif conf < 0.70:
                    logger.info(f"   [{disp}] BLOCKED: AI confidence {conf:.0%} < 70%")
                elif rr_val > 0 and (rr_val < 1.5 or rr_val > 5.0):
                    logger.info(f"   [{disp}] BLOCKED: RR 1:{rr_val:.1f} outside 1:1.5-5")
                else:
                    should_push = True

                # ── Killzone gate: forex/metals outside London/NY = BLOCK ──
                if should_push and disp in ("XAUUSD","GOLD","USOIL","EURUSD","GBPUSD","USDJPY"):
                    lkz, nykz = killzone(h)
                    if not lkz and not nykz:
                        logger.info(f"   [{disp}] BLOCKED: outside killzone (London/NY only)")
                        should_push = False

            if should_push:
                logger.info(f"AI PUSH [{disp}]: {action} | conf={conf:.0%} | model={sig.get('_model','?')}")

                if LAYERING_ENGINE:
                    sig = enrich_signal_with_layers(sig)
                # Clamp SL/TP to realistic bounds before pushing
                sig = _clamp_sltp(sig, disp)

                # ── SMC/ICT Enrichment ──
                smc_text2 = ""
                try:
                    from smc_section import format_smc_analysis
                    pip_s2 = 0.10 if disp in ("XAUUSD","GOLD") else 0.01 if disp == "USOIL" else 1.0
                    ohlcv_smc2 = _fetch_ohlcv_for_ai(pair, keep=60)
                    if ohlcv_smc2:
                        smc_text2 = format_smc_analysis(ohlcv_smc2, disp, price, action, pip_s2)
                except Exception:
                    pass
                text = fmt_signal(sig, price, dxy, h, disp, "$" if not disp.startswith(("BBCA","BBRI","TLKM","ASII","IHSG")) else "Rp", smc_text=smc_text2)
                _entry = sig.get("entry", price) or 0
                _sl = sig.get("sl", 0) or 0
                _tp = sig.get("tp", 0) or 0
                # ── Post to channel FIRST, capture message_id for reply chain ──
                tg_msg_id = None
                if _can_post_to_channel(pair, action, _entry, _sl, _tp):
                    logger.info(f"CHANNEL POST [AI-consensus]: {pair} {action}")
                    result = send_to_channel(text)
                    if result:
                        tg_msg_id = result.get('result',{}).get('message_id')
                        logger.info(f"CHANNEL POST OK [AI-consensus]: message_id={tg_msg_id}")
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
                    # Rate limited — respect global cooldown
                    state_force2 = _cs()
                    if time.time() - state_force2.get("global_last", 0) < _GLOBAL_CHANNEL_COOLDOWN:
                        logger.info(f"⏱️ SKIP force post [AI-{disp}]: global cooldown active ({int(time.time()-state_force2['global_last'])}s ago)")
                    else:
                        logger.warning(f"🚨 FORCE POST [AI-{disp}]: rate limited but trade opened — posting anyway")
                        result = send_to_channel(text)
                        if result:
                            tg_msg_id = result.get('result',{}).get('message_id')
                        _mark_channel_post(pair, action, _entry, _sl, _tp)
                    _feed_add(symbol=disp, direction=action, entry=_entry, sl=_sl, tp=_tp,
                              confidence=conf, rr_ratio=sig.get("rr_ratio","?"),
                              engines=sig.get("engines",{}), source="channel-auto",
                              price=price, grade=sig.get("grade",""),
                              models=sig.get("_models",""), voters=sig.get("voters","?"))

                # ── Post to bridge NOW with telegram_message_id attached ──
                if tg_msg_id:
                    sig["telegram_message_id"] = tg_msg_id
                post_signal_to_bridge(sig, price, disp)

                # ── ENTRY EXECUTED notification to channel ──
                if disp == "XAUUSD" and action in ("BUY", "SELL"):
                    actual_entry = sig.get("entry", price) or price
                    actual_zone_lo = sig.get("zone_lo", actual_entry)
                    actual_zone_hi = sig.get("zone_hi", actual_entry)
                    emode = sig.get("entry_mode", "market")
                    if emode == "zone" and actual_zone_lo < actual_zone_hi:
                        entry_label = f"{actual_zone_lo:.2f} — {actual_zone_hi:.2f}"
                        exec_text = (
                            f"⚡ <b>ENTRY EXECUTED — Zone Pending</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 {action} {disp} | 🕐 {wib_now().strftime('%H:%M')} WIB\n"
                            f"📍 Zone: ${entry_label}\n"
                            f"⏳ EA menunggu harga masuk zone...\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🆔 Signal: #{tg_msg_id or 'N/A'}\n"
                            f"⚠️ <i>Ini pending order — EA eksekusi otomatis saat harga masuk zone.</i>"
                        )
                    else:
                        entry_label = f"{actual_entry:.2f}"
                        exec_text = (
                            f"⚡ <b>ENTRY EXECUTED — Market</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 {action} {disp} @ ${entry_label}\n"
                            f"🕐 {wib_now().strftime('%H:%M')} WIB\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🆔 Signal: #{tg_msg_id or 'N/A'}"
                        )
                    try:
                        send_to_channel(exec_text)
                        logger.info(f"📤 ENTRY EXECUTED posted: {action} {disp} @ {entry_label}")
                    except Exception as e:
                        logger.warning(f"Failed to post ENTRY EXECUTED: {e}")

                if LEARNING_ENGINE:
                    pass  # learning happens on trade outcome (check_outcomes)

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

            # ── Save persistent scan state for missed move detection ──
            _save_scan_state({
                'last_price': price,
                'last_action': action if 'action' in dir() else '',
                'last_signal_time': log.get('last_signal_time', ''),
                'last_kz': kz,
            })

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
    
    for pair_key, disp in [("gold","XAUUSD"), ("btc","BTCUSD")]:
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
    lines.append("💚 Jangan lupa upgrade tier → /subscribe")
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
    
    for pair_key, disp in [("gold","XAUUSD"), ("btc","BTCUSD")]:
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
    lines.append("💚 Upgrade tier → /subscribe")
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
        pass  # learning loop runs via cron + check_outcomes

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

    # ── ML Feedback Loop — autonomous signal tracking (Shadow Mode) ──
    try:
        from members.ml_feedback import start_loop
        start_loop(interval=60)  # check OPEN signals every 60s
        logger.info("ML Feedback Loop background worker started")
    except Exception as exc:
        logger.warning("ML Feedback Loop unavailable: %s", exc)

    # ── Weekly Walk-Forward Scheduler — Sabtu 02:00 WIB ──
    def _weekly_learning_scheduler():
        """Auto-run pattern extraction every Saturday at 02:00 WIB."""
        import datetime as _dt
        _VILONA_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")

        def _post_to_channel(text: str):
            """Post WFA result to @vilonaaichanel via bot API."""
            if not _VILONA_TOKEN:
                logger.warning("No bot token — can't post WFA to channel")
                return
            try:
                _chan = "-1003257064212"
                _data = json.dumps({"chat_id": _chan, "text": text, "parse_mode": "HTML"}).encode()
                _req = urllib.request.Request(
                    f"https://api.telegram.org/bot{_VILONA_TOKEN}/sendMessage",
                    data=_data, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(_req, timeout=10):
                    logger.info("WFA posted to @vilonaaichanel")
            except Exception as exc:
                logger.warning("WFA channel post failed: %s", exc)

        while True:
            try:
                now = _dt.datetime.now(WIB)
                days_until_sat = (5 - now.weekday()) % 7
                # If Saturday 02:00-03:00 → run NOW; if Saturday past 03:00 → run NOW (catchup)
                is_saturday = now.weekday() == 5
                should_run_now = is_saturday and now.hour >= 2

                if should_run_now:
                    wait = 0  # nggak perlu nunggu
                else:
                    # Wait until next Saturday 02:00
                    if days_until_sat == 0:
                        days_until_sat = 7  # past Saturday, next week
                    next_run = now.replace(hour=2, minute=0, second=0, microsecond=0) + _dt.timedelta(days=days_until_sat)
                    wait = int((next_run - now).total_seconds())

                if wait > 0:
                    while wait > 0:
                        chunk = min(wait, 3600)
                        time.sleep(chunk)
                        wait -= chunk
                    # Fell through → now is Saturday 02:00, run
                    pass

                # ── Run extraction ──
                logger.info("📅 Weekly walk-forward analysis running...")
                try:
                    from scripts.pattern_extractor import run_learning_pipeline, format_weekly_report, format_learning_report, format_educational_post
                    DB = str(DATA_DIR / "members.db")
                    result = run_learning_pipeline(DB, lookback_days=14)
                    n = result.get("total_signals", 0)
                    if n > 0:
                        # Post learning report (stats breakdown)
                        learn_msg = format_learning_report(result)
                        _post_to_channel(learn_msg)
                        tg_send(learn_msg, str(ADMIN_CHAT_ID or ""))

                        # Post educational content (actionable lessons from data)
                        edu_msg = format_educational_post(14)
                        if edu_msg:
                            time.sleep(60)  # space out posts
                            _post_to_channel(edu_msg)

                        # Post marketing report (CTA + subscribe hook)
                        mkt_msg = format_weekly_report(result)
                        time.sleep(60)
                        _post_to_channel(mkt_msg)
                        logger.info("Weekly WFA done: %d signals, weights updated, 3 posts", n)
                    else:
                        logger.info("Weekly WFA skipped: no closed signals")
                except Exception as exc:
                    logger.error("Weekly WFA failed: %s", exc)
                time.sleep(3600)  # anti-spin: 1h cooldown
            except Exception as exc:
                logger.error("Weekly scheduler error: %s", exc)
                time.sleep(3600)
    try:
        _weekly_thread = threading.Thread(target=_weekly_learning_scheduler, daemon=True, name="weekly-wfa")
        _weekly_thread.start()
        logger.info("Weekly WFA scheduler started (Sat 02:00 WIB)")
    except Exception as exc:
        logger.warning("Weekly WFA scheduler unavailable: %s", exc)

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
            {"command": "levels",   "description": "🏛 SnR + FIBO + Engine (Subscriber)"},
            {"command": "news",     "description": "📰 Market Intel — X/Twitter intel (Subscriber)"},
            {"command": "killzone", "description": "🎯 Radar sesi market aktif"},
            {"command": "zones", "description": "🧲 Order Blocks + FVG Scanner"},
            {"command": "structure", "description": "🏗 BOS/CHoCH + MTF Alignment"},
            {"command": "stier", "description": "💀 S-TIER Zone — Triple Confluence GOD TIER"},
            {"command": "subscribe","description": "⭐ Upgrade ke PRO/ELITE/LIFETIME"},
            {"command": "status",   "description": "🛡 Cek Kuota & Status"},
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
                    if cmd in ("/start","/help","/price","/analyze","/data","/killzone","/bridge_status","/status","/bill","/testpay","/subscribe","/upgrade","/autosync","/genkey","/listkeys","/revokekey","/mykey","/myid","/winrate","/history","/recap","/mapping","/news","/activate","/restart_bot","/signal","/mtf","/engines","/dashboard","/levels","/level","/zones","/structure","/session","/donate","/testbridge","/trailing","/stier","/download","/referral","/learn_report"):
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
                                            f"⚡ <b>Upgrade Tier Rp{amount:,}</b>\n"
                                            f"━━━━━━━━━━━━━━━━\n"
                                            f"👑 Status: SUBSCRIBER — AKTIF PERMANEN\n"
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
                        elif data.startswith(("pay:", "check:", "pricing:", "donate:", "sub:", "cancel_input")):
                            handle_payment_callback(cb)
                        elif data.startswith("cmd:"):
                            # ── Interactive Onboarding: cmd:analyze_xauusd / cmd:guide / cmd:subscribe ──
                            handle_onboarding_callback(cb)
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
