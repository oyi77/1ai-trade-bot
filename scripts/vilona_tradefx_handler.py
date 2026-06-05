#!/usr/bin/env python3
"""
Vilona Trade FX Telegram Bot Handler
Grab forex data + generate signals even without MT5/EA.

Commands: /start /help /price /analyze /stocks /data /killzone /status
"""
import json, logging, os, re, sys, threading, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Project path ──
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# ── Market data layer ──
try:
    from market_data import UnifiedMarketData
    MARKET_DATA = UnifiedMarketData()
except Exception as e:
    MARKET_DATA = None
    print(f"Market data layer unavailable: {e}")

# ── Member system ──
try:
    from members import register_member, get_member, get_member_stats, mark_paid, get_due_members
    from members import is_premium, check_quota, use_quota, activate_premium, deactivate_premium
    from members.payment import get_pricing_info
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
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY", "")
CHAT_ID = os.environ.get("VILONA_TRADEFX_CHAT_ID", "")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://localhost:20129/v1/chat/completions")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OMNIROUTE_MODELS = ["nemotron-3-super-free", "qwen3.6-plus-free", "ling-2.6-1t-free"]


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
    global BOT_TOKEN, DEEPSEEK_KEY, OPENAI_KEY, CLAUDE_KEY, CHAT_ID, TELEGRAM_API
    BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    OPENAI_KEY = os.environ.get("OPENAI_API_KEY", OPENAI_KEY)
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


# ── Price fetching ──
def fetch_price(pair="gold"):
    pair = pair.lower().strip()
    if pair in ("gold", "xauusd"):
        try:
            quote = MARKET_DATA.get_quote("GC=F")
            if quote and quote.price > 1000:
                return quote.price
        except: pass
        try:
            with urllib.request.urlopen("https://api.gold-api.com/price/XAU", timeout=10) as r:
                data = json.loads(r.read())
                return float(data.get("price", 0))
        except: pass
    elif MARKET_DATA:
        try:
            quote = MARKET_DATA.get_quote(pair.upper())
            if quote and quote.price > 0:
                return quote.price
        except: pass
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
    try:
        payload = {"chat_id": target, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        req = urllib.request.Request(f"{TELEGRAM_API}/sendMessage",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error(f"tg_send failed: {e}")
        return None


# ── Signal bridge ──
def post_signal_to_bridge(sig, price):
    try:
        payload = {"action": sig.get("action","HOLD"), "entry": sig.get("entry",price),
                   "sl": sig.get("sl",0), "tp": sig.get("tp",0), "confidence": sig.get("confidence",0),
                   "source": sig.get("source","vilona-tradefx"), "timestamp": wib_now().isoformat()}
        req = urllib.request.Request("http://localhost:8765/signal",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except: pass


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
        sig = {
            "action": quant_bias, "entry": price,
            "sl": price * 0.995 if quant_bias == "BUY" else price * 1.005,
            "tp": price * 1.01 if quant_bias == "BUY" else price * 0.99,
            "confidence": confidence, "rr_ratio": 2.0,
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
                    logger.info(f"🔮 HERMES LIQUIDITY SWEEP: {hermes_signal.action} {display} | "
                                f"Entry={hermes_signal.entry_price} SL={hermes_signal.stop_loss} "
                                f"TP1={hermes_signal.take_profit_1} R:R=1:{hermes_signal.risk_reward_ratio}")
                    sig = {
                        "action": hermes_signal.action, "entry": hermes_signal.entry_price,
                        "sl": hermes_signal.stop_loss, "tp": hermes_signal.take_profit_1,
                        "tp1": hermes_signal.take_profit_1, "tp2": hermes_signal.take_profit_2,
                        "confidence": hermes_signal.confidence,
                        "rr_ratio": hermes_signal.risk_reward_ratio,
                        "reasoning": hermes_signal.reason, "ensemble": "mechanical", "voters": 0,
                        "_model": "HermesSMC",
                        "grade": "A" if hermes_signal.risk_reward_ratio >= 2.0 else "B",
                        "source": "hermes_liquidity_sweep",
                    }
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
    except:
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
            logger.info(f"DeepSeek: {len(content)} chars")
            return _extract_json(content)
    except Exception as e:
        logger.warning(f"DeepSeek error: {e}")
        return None


def _call_openai(prompt):
    if not OPENAI_KEY: return None
    try:
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
            data=json.dumps({
                "model":"gpt-4o-mini","max_tokens":800,"temperature":0.3,
                "messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}]
            }).encode(),
            headers={"Content-Type":"application/json","Authorization":f"Bearer {OPENAI_KEY}"})
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"]
            logger.info(f"GPT-4o: {len(content)} chars")
            return _extract_json(content)
    except Exception as e:
        logger.warning(f"OpenAI error: {e}")
        return None


def _call_omniroute(prompt, models=None):
    if not models: models = OMNIROUTE_MODELS
    for model in models:
        try:
            req = urllib.request.Request(OMNIROUTE_URL,
                data=json.dumps({"model":model,"max_tokens":600,"temperature":0.3,
                    "messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}]}).encode(),
                headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                content = data["choices"][0]["message"]["content"]
                return _extract_json(content)
        except: continue
    return None


def ask_ai_ensemble(price, dxy, sess, kz_str, loss_count, premium=False, ohlcv_data=None, display="XAUUSD"):
    """Dual AI consensus: DeepSeek + GPT-4o. Fallback to OmniRoute."""
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
        f"R:R minimum 1:2. {'⚠️ FRIDAY: SL +10-15 pips extra.' if wib_now().weekday()==4 else ''}"
    )

    # Try DeepSeek (primary)
    ds = _call_deepseek(prompt)
    # Try GPT-4o (secondary)
    gpt = _call_openai(prompt)

    # Consensus: both must agree
    if ds and gpt and ds.get("action") == gpt.get("action") and ds.get("action") in ("BUY","SELL"):
        conf = (ds.get("confidence",0) + gpt.get("confidence",0)) / 2
        sig = ds.copy()
        sig["confidence"] = conf
        sig["ensemble"] = "dual"
        sig["voters"] = 2
        sig["_model"] = "DeepSeek+GPT-4o"
        logger.info(f"DUAL CONSENSUS: {sig['action']} conf={conf:.0%}")
        return sig

    # Solo decision (DeepSeek preferred)
    if ds:
        ds["ensemble"] = "solo"; ds["voters"] = 1
        ds["_model"] = "DeepSeek"
        return ds
    if gpt:
        gpt["ensemble"] = "solo"; gpt["voters"] = 1
        gpt["_model"] = "GPT-4o"
        return gpt

    # Fallback: OmniRoute
    logger.info("Falling back to OmniRoute...")
    return _call_omniroute(prompt)


def ask_ai(price, dxy, sess, kz_str, loss_count, premium=False, ohlcv=None, display="XAUUSD"):
    return ask_ai_ensemble(price, dxy, sess, kz_str, loss_count, premium, ohlcv, display)


# ── Signal formatting ──
def fmt_signal(sig, price, dxy, h, display="XAUUSD", currency="$"):
    action = sig.get("action","HOLD")
    emoji = {"BUY":"🟢","SELL":"🔴","HOLD":"⚪️"}.get(action,"⚪️")
    grade = sig.get("grade","D")
    conf = sig.get("confidence",0)
    rr = sig.get("rr_ratio","?")
    entry = sig.get("entry", price)
    sl = sig.get("sl", 0) or 0
    tp = sig.get("tp", 0) or 0
    reason = sig.get("reasoning","")[:300]

    return (
        f"{emoji} <b>{action} {display}</b> | Grade:{grade} | RR 1:{rr}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Confidence: {conf:.0%} | Ensemble: {sig.get('ensemble','?')}\n"
        f"Entry: {currency}{entry:.2f} | SL: {currency}{sl:.2f} | TP: {currency}{tp:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 {reason}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🕐 {wib_fmt()} | Session: {session(h)}"
    )


# ── Command handler ──
def handle_command(cmd, text, chat_id, msg):
    sub = text[len(cmd):].strip().lower() if len(text) > len(cmd) else ""

    if cmd == "/start":
        welcome = "🤖 <b>Vilona Trade FX</b>\n━━━━━━━━━━━━━━━━\nAI Trading Assistant — Auto-scan XAUUSD 24/7"
        # Quick welcome first
        tg_send(welcome, chat_id)
        # Then load member info in background
        if MEMBERS_ENABLED and chat_id:
            try:
                member = get_member(str(chat_id))
                is_vip = is_premium(str(chat_id))
                quota = check_quota(str(chat_id))
                if is_vip:
                    tg_send(f"⭐ <b>VIP Active</b> | Kuota: {quota.get('used',0)}/{quota.get('total',5)}\n"
                            f"Kirim /analyze xauusd untuk mulai.", chat_id)
                else:
                    tg_send(f"👤 <b>Free User</b> | Kuota: {quota.get('used',0)}/{quota.get('total',3)}\n"
                            f"Upgrade ke VIP via /subscribe", chat_id)
            except: pass

    elif cmd == "/help":
        tg_send(
            "📋 <b>Commands</b>\n━━━━━━━━━━━━━━━━\n"
            "/start — Info bot\n/help — Bantuan\n"
            "/price — Cek harga XAUUSD\n/analyze — AI analisa (xauusd/btc/oil/aapl/bbca)\n"
            "/data — Info pasar\n/killzone — Cek sesi trading\n/status — Status langganan\n"
            "/subscribe — Upgrade VIP", chat_id)

    elif cmd == "/price":
        price = fetch_price()
        dxy = fetch_dxy()
        txt = f"💰 <b>XAUUSD</b>\n━━━━━━━━━━━━━━━━\nPrice: ${price:.2f}" if price else "❌ Price unavailable"
        if dxy: txt += f"\nDXY: {dxy:.2f}"
        txt += f"\n━━━━━━━━━━━━━━━━\n🕐 {wib_fmt()} | Session: {session()}"
        tg_send(txt, chat_id)

    elif cmd == "/killzone":
        lkz, nykz = killzone()
        txt = f"🕐 <b>Session: {session()}</b>\n━━━━━━━━━━━━━━━━\n"
        txt += f"London KZ: {'🟢 ACTIVE' if lkz else '🔴 Off'}\nNY KZ: {'🟢 ACTIVE' if nykz else '🔴 Off'}\n"
        txt += f"━━━━━━━━━━━━━━━━\n{wib_fmt()}"
        tg_send(txt, chat_id)

    elif cmd == "/status":
        if MEMBERS_ENABLED and chat_id:
            try:
                q = check_quota(str(chat_id))
                is_vip = is_premium(str(chat_id))
                txt = (f"⭐ VIP Active" if is_vip else "👤 Free") + f" | Kuota: {q.get('used',0)}/{q.get('total',0)}"
                tg_send(txt, chat_id)
            except:
                tg_send("❌ Gagal memuat status.", chat_id)
        else:
            tg_send("👤 Member system not active.", chat_id)

    elif cmd == "/analyze":
        is_blackout, is_post_news, news_name = news_blackout_status()
        if is_blackout:
            tg_send(f"⚪️ <b>HOLD — Menjelang Rilis Berita</b>\n📰 {news_name}\n⏳ Tunggu 30 menit setelah rilis.", chat_id)
            return

        pair_map = {"xauusd":"gold","gold":"gold","eurusd":"eurusd","gbpusd":"gbpusd","usdjpy":"usdjpy"}
        if sub in pair_map:
            tg_send("🔍 Vilona Trade FX menganalisa... ~15 detik", chat_id)
            price = fetch_price(pair_map[sub])
            dxy = fetch_dxy() if sub in ("xauusd","gold") else None
            if not price:
                tg_send("❌ Price unavailable.", chat_id)
                return
            sig = ask_ai(price, dxy, session(), str(killzone()), 0, premium=False)
            if sig:
                tg_send(fmt_signal(sig, price, dxy, wib_now().hour, sub.upper()), chat_id)
            else:
                tg_send("❌ Analisa gagal — coba lagi nanti.", chat_id)
        elif not sub:
            tg_send("🧠 <b>ANALISA AI — Pilih</b>\n\n/analyze xauusd — XAUUSD\n/analyze btc — Bitcoin\n"
                    "/analyze oil — Crude Oil\n/analyze aapl — Apple\n/analyze bbca — BBCA", chat_id)
        else:
            tg_send(f"🔍 Menganalisa {sub.upper()}...", chat_id)


    elif cmd == "/data":
        price = fetch_price()
        dxy = fetch_dxy()
        txt = f"📊 <b>Market Data</b>\n━━━━━━━━━━━━━━━━\n"
        if price: txt += f"XAUUSD: ${price:.2f}\n"
        if dxy: txt += f"DXY: {dxy:.2f}\n"
        txt += f"━━━━━━━━━━━━━━━━\n🕐 {wib_fmt()}"
        tg_send(txt, chat_id)

    elif cmd == "/subscribe":
        if MEMBERS_ENABLED:
            try:
                info = get_pricing_info()
                txt = (f"💎 <b>Upgrade VIP</b>\n━━━━━━━━━━━━━━━━\n"
                       f"💰 {info.get('vip_price','75K')}/bulan\n"
                       f"✓ Analisa unlimited\n✓ Sinyal real-time\n✓ Priority support\n"
                       f"━━━━━━━━━━━━━━━━\nComing soon: Payment gateway")
                tg_send(txt, chat_id)
            except:
                tg_send("💎 VIP upgrade system loading...", chat_id)


# ── Signal log ──
def load_signal_log():
    path = DATA_DIR / "signal_log.json"
    try:
        if path.exists(): return json.loads(path.read_text())
    except: pass
    return {"signals_sent":0,"last_signal_time":None,"last_action":None,"last_price":0,"loss_count":0}

def save_signal_log(log):
    (DATA_DIR / "signal_log.json").write_text(json.dumps(log))

def is_trading_session(h):
    return 7 <= h < 23


# ── Auto-analyze loop ──
def auto_analyze_loop():
    """Main autonomous signal loop. Runs 24/7 with mechanical + AI."""
    logger.info("🚀 Auto-analyze loop started")
    time.sleep(5)

    while True:
        try:
            h = wib_now().hour
            if not is_trading_session(h):
                time.sleep(180)
                continue

            log = load_signal_log()
            price = fetch_price()
            if not price:
                time.sleep(60)
                continue

            # ── News blackout check ──
            is_blackout, is_post_news, news_name = news_blackout_status()
            if is_blackout:
                logger.info(f"🔇 News blackout: {news_name}")
                time.sleep(120)
                continue

            dxy = fetch_dxy()
            lkz, nykz = killzone(h)
            kz = "London" if lkz else ("NY" if nykz else "Outside")

            # ── MECHANICAL OVERRIDE: Quant + FVG + Hermes ──
            mech_sig = None
            if MARKET_DATA:
                try:
                    m1_bars = MARKET_DATA.get_ohlcv("GC=F", "1m", 200)
                    if m1_bars and len(m1_bars) >= 30:
                        ohlcv_m1 = [{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                      "low": b.low, "close": b.close, "volume": b.volume} for b in m1_bars]
                        mech_sig, mech_reason = detect_mechanical_signal(
                            "XAUUSD", "XAUUSD", price, ohlcv_m1)
                        if mech_sig:
                            logger.info(f"⚡ MECHANICAL: {mech_sig['action']} | {mech_sig['source']}")
                except Exception as e:
                    logger.debug(f"Mechanical check: {e}")

            if mech_sig and mech_sig["action"] in ("BUY", "SELL"):
                action = mech_sig["action"]
                conf = mech_sig["confidence"]
                
                # Direction stability guard — block opposite direction within 10 min
                last_time = log.get("last_signal_time")
                last_action = log.get("last_action")
                if last_time and last_action:
                    try:
                        last_dt = datetime.fromisoformat(last_time)
                        elapsed = (wib_now() - last_dt).total_seconds()
                        if elapsed < 600 and last_action != action:
                            logger.info(f"BLOCKED: {action} after {last_action} ({elapsed:.0f}s ago)")
                            time.sleep(60)
                            continue
                    except: pass
                
                logger.info(f"MECHANICAL PUSH: {action} XAUUSD | conf={conf:.0%}")
                text = fmt_signal(mech_sig, price, dxy, h) + f"\n<i>[{mech_sig.get('source','mech')}] override</i>"
                tg_send(text)
                post_signal_to_bridge(mech_sig, price)

                if LEARNING_ENGINE:
                    try: track_signal(mech_sig, price, "XAUUSD", session(h), mech_sig.get("source","mech"))
                    except: pass

                log["signals_sent"] += 1
                log["last_signal_time"] = wib_now().isoformat()
                log["last_action"] = action
                log["last_price"] = price
                # Save full signal for EA consumption
                log["last_signal"] = {
                    "action": action, "entry": mech_sig.get("entry", price),
                    "sl": mech_sig.get("sl", 0), "tp": mech_sig.get("tp", 0),
                    "tp1": mech_sig.get("tp1", 0), "tp2": mech_sig.get("tp2", 0),
                    "confidence": conf, "source": mech_sig.get("source", "mech"),
                    "rr_ratio": mech_sig.get("rr_ratio", 0),
                }
                # Write EA queue
                _eaq = DATA_DIR / "ea_signal.json"
                _eaq.write_text(json.dumps(log["last_signal"]))
                save_signal_log(log)
                time.sleep(300)  # 5 min cooldown after mechanical signal
                continue

            # ── AI Consensus ──
            sig = ask_ai(price, dxy, session(h), kz, log["loss_count"], premium=(lkz or nykz))
            if not sig:
                time.sleep(60)
                continue

            action = sig.get("action","HOLD")
            conf = sig.get("confidence",0)

            # Auto-push rules
            should_push = False
            if action in ("BUY","SELL"):
                if (lkz or nykz) and sig.get("voters",0) >= 2 and conf >= 0.70:
                    should_push = True
                elif not (lkz or nykz) and conf >= 0.65:
                    should_push = True

            if should_push:
                logger.info(f"AI PUSH: {action} | conf={conf:.0%} | model={sig.get('_model','?')}")
                text = fmt_signal(sig, price, dxy, h)
                tg_send(text)
                post_signal_to_bridge(sig, price)

                if LEARNING_ENGINE:
                    try: track_signal(sig, price, "XAUUSD", session(h), "ai")
                    except: pass

                log["signals_sent"] += 1
                log["last_signal_time"] = wib_now().isoformat()
                log["last_action"] = action
                log["last_price"] = price
                save_signal_log(log)
                time.sleep(90)
            else:
                logger.info(f"   {action} | Grade:{sig.get('grade','?')} | SKC={sig.get('skc_score',{}).get('total',0)}")

            time.sleep(90 if (lkz or nykz) else 120)

        except Exception as e:
            logger.error(f"Auto-analyze error: {e}")
            time.sleep(60)


# ── Main ──
def main():
    if not BOT_TOKEN:
        logger.error("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)

    # Start background threads
    if LEARNING_ENGINE:
        try: start_learning_engine()
        except: pass

    # Start auto-analyze thread
    auto_thread = threading.Thread(target=auto_analyze_loop, daemon=True)
    auto_thread.start()
    logger.info("Auto-analyze thread started")

    # Start bot polling
    state = load_state()
    offset = state.get("last_update_id", 0)
    logger.info(f"Bot starting... offset={offset}")

    while True:
        try:
            url = f"{TELEGRAM_API}/getUpdates?offset={offset + 1}&timeout=30"
            with urllib.request.urlopen(url, timeout=35) as r:
                updates = json.loads(r.read()).get("result", [])
            for upd in updates:
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")
                if text and chat_id:
                    cmd = text.split()[0].split('@')[0].lower()
                    if cmd in ("/start","/help","/price","/analyze","/data","/killzone","/status","/subscribe"):
                        try:
                            handle_command(cmd, text, str(chat_id), msg)
                        except Exception as e:
                            logger.error(f"Command error: {e}")
            save_state({"last_update_id": offset})
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
