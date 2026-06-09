"""VilonaBot — multi-asset AI trading signal bot.

Extracted and modularized from bots/vilona-bot/handler.py (3,489 LOC).
Provides:
- Auto-analysis loop (market scanning with AI + mechanical engines)
- Command routing (/analyze, /price, /status, etc.)
- User management
- Signal generation and dispatch via bridge
- All config from tradebot.config.settings
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Load .env explicitly (handler reads keys via os.environ.get) ────────
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed — rely on systemd EnvironmentFile

from tradebot.bots.base import BaseBot
from tradebot.bots.vilona.signal_bridge import VilonaSignalBridge

LOG = logging.getLogger("tradebot.bots.vilona.handler")

# ── Constants ──────────────────────────────────────────────────────────────

WIB = timezone(timedelta(hours=7))

DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "gold": "GC=F", "xauusd": "GC=F",
    "btc": "BTC-USD", "btcusd": "BTC-USD",
    "eth": "ETH-USD", "ethusd": "ETH-USD",
    "oil": "CL=F", "eurusd": "EURUSD=X", "gbpusd": "GBPUSD=X",
    "usdjpy": "JPY=X", "jpyusd": "JPY=X",
    "aapl": "AAPL", "tsla": "TSLA", "msft": "MSFT", "nvda": "NVDA",
    "bbca": "BBCA.JK", "bbri": "BBRI.JK", "tlkm": "TLKM.JK", "asii": "ASII.JK",
    "unvr": "UNVR.JK", "bmri": "BMRI.JK", "adro": "ADRO.JK", "ihsg": "^JKSE",
}

SUPPORTED_PAIRS: list[str] = [
    "gold", "btc", "eth", "oil", "eurusd", "gbpusd", "usdjpy",
    "aapl", "tsla", "msft", "nvda",
    "bbca", "bbri", "tlkm", "asii", "unvr", "bmri", "adro", "ihsg",
]

AUTO_SCAN_ASSETS: list[tuple[str, str, str, bool]] = [
    # (internal_pair, display_name, yahoo_symbol, is_forex_metal)
    ("gold", "XAUUSD", "GC=F", True),
    ("btc", "BTCUSD", "BTC-USD", False),
    ("oil", "USOIL", "CL=F", True),   # Oil = London/NY killzone (per user directive)
]

# ── Market session helpers ─────────────────────────────────────────────────

def wib_now() -> datetime:
    return datetime.now(WIB)


def wib_fmt(d: datetime | None = None) -> str:
    d = d or wib_now()
    return d.strftime("%d/%m %H:%M WIB")


def session_label(h: int | None = None) -> str:
    h = h if h is not None else wib_now().hour
    if 3 <= h < 7:
        return "Asia"
    if 7 <= h < 15:
        return "Asia+London"
    if 15 <= h < 19:
        return "London"
    if 19 <= h < 23:
        return "London+NY"
    return "NY"


def killzone_active(h: int | None = None) -> tuple[bool, bool]:
    h = h if h is not None else wib_now().hour
    return (14 <= h < 17, 19 <= h < 22)


def news_blackout_status(h: int | None = None, m: int | None = None) -> tuple[bool, bool, str | None]:  # noqa: E501
    """Check if in high-impact news window. Returns (is_blackout, is_post_news, news_name)."""
    now = wib_now()
    h = h if h is not None else now.hour
    m = m if m is not None else now.minute
    day = now.weekday()
    total_min = h * 60 + m

    major_events = [
        {"name": "High-Impact US Data", "blackout_start": 19 * 60 + 0, "blackout_end": 19 * 60 + 30,
         "post_start": 19 * 60 + 30, "post_end": 19 * 60 + 45, "days": [4]},
        {"name": "NY Open Vol Spike", "blackout_start": 19 * 60 + 0, "blackout_end": 19 * 60 + 10,
         "post_start": 19 * 60 + 10, "post_end": 19 * 60 + 25, "days": [0, 1, 2, 3, 4]},
    ]

    for ev in major_events:
        if day in ev["days"]:
            if ev["blackout_start"] <= total_min < ev["blackout_end"]:
                return True, False, ev["name"]
            if ev["post_start"] <= total_min < ev["post_end"]:
                return False, True, ev["name"]
    return False, False, None


def is_weekend() -> bool:
    """True if Sat/Sun, OR Monday before 05:00 WIB (crypto mode extended)."""
    now = wib_now()
    return now.weekday() >= 5 or (now.weekday() == 0 and now.hour < 5)


def weekend_status_text() -> str:
    """Return weekend mode indicator text."""
    if is_weekend():
        return "\n🟡 WEEKEND MODE: Forex/Gold Tutup | Crypto (BTC/ETH) BUKA 24/7"
    return ""


# ── Signal normalization ──────────────────────────────────────────────────

def normalize_symbol(s: str) -> str:
    """Strip broker suffixes and normalize to standard pair name."""
    s = re.sub(r"[.\-#_].*$", "", s.strip().lower())
    s = re.sub(r"[cm]$", "", s)
    return s


def resolve_yahoo_symbol(pair: str) -> str:
    """Map common names to Yahoo Finance symbols."""
    pair = normalize_symbol(pair)
    return DEFAULT_SYMBOL_MAP.get(pair, pair.upper())


# ── Signal formatting ────────────────────────────────────────────────────

def format_signal_basic(sig: dict[str, Any], price: float, display: str) -> str:
    """Human-readable signal message from analysis result."""
    action = sig.get("action", "HOLD")
    confidence = sig.get("confidence", 0)
    reasoning = sig.get("reasoning", "")
    entry = sig.get("entry", price)
    sl = sig.get("sl", 0)
    tp = sig.get("tp", 0)
    rr = sig.get("rr_ratio", 0)
    grade = sig.get("grade", "?")
    model = sig.get("_model", sig.get("ensemble", "ai"))

    if action == "HOLD":
        return (
            f"⚪ <b>{display.upper()}</b> — HOLD\n"
            f"━━━━━━━━━━━━━━\n"
            f"💡 <i>{reasoning or 'No strong setup detected.'}</i>\n"
            f"📊 Confidence: {confidence:.0%}"
        )

    icon = "🟢" if action == "BUY" else "🔴"
    msg = (
        f"{icon} <b>{display.upper()}</b> — {action}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Entry: <code>{entry:.4g}</code>\n"
        f"SL:    <code>{sl:.4g}</code>\n"
        f"TP:    <code>{tp:.4g}</code>\n"
        f"R:R:   1:{rr:.2f}\n"
        f"Grade: {grade}\n"
        f"Conf:  {confidence:.0%}\n"
        f"Model: {model}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💡 <i>{reasoning[:200]}</i>\n"
        f"\n"
        f"⚡ 1% risk only. Full AI — verify sendiri.\n"
        f"💚 Server ini GRATIS — dukung via /donate | @berkahkaryaforexbotbot"
    )
    return msg


# ── JSON extraction from AI output ────────────────────────────────────────

def _parse_sse(raw: str) -> str | None:
    """Parse Server-Sent Events (SSE) streaming response from OmniRoute.
    Extracts concatenated content from `data:` lines.
    Returns None if the response is not SSE format."""
    if "data: " not in raw:
        return None
    parts: list[str] = []
    for line in raw.split("\n"):
        if line.startswith("data: "):
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if "content" in delta:
                    parts.append(delta["content"])
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
    return "".join(parts) if parts else None


def extract_json(content: str) -> dict[str, Any] | None:
    """Robust JSON extraction from AI output — strips markdown, sanitizes."""
    content = re.sub(r"```[a-z]*\s*", "", content)
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    end = start
    for i, ch in enumerate(content[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    json_str = content[start:end]
    json_str = re.sub(r"[\x00-\x1f]+", " ", json_str)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


# ── VilonaBot ──────────────────────────────────────────────────────────────

class VilonaBot(BaseBot):
    """Multi-asset AI-powered trading signal bot.

    Features:
    - Auto-analysis loop scanning gold, BTC, ETH, and more
    - Mechanical signal detection (Quant + FVG + Hermes Liquidity)
    - AI-powered analysis via OmniRoute (DeepSeek/OpenAI/Gemini)
    - Signal dispatch to MT5 EAs via bridge
    - User management with licenses and subscriptions
    """

    def __init__(self, name: str = "vilona-tradefx") -> None:
        super().__init__(name=name)

        # ── API keys (from env via settings or fallback env reads) ──────
        self.deepseek_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
        self.openai_key: str = os.environ.get("OPENAI_API_KEY", "")
        self.gemini_key: str = os.environ.get("GEMINI_API_KEY", "")
        self.claude_key: str = os.environ.get("CLAUDE_API_KEY", "")
        self.omniroute_url: str = os.environ.get(
            "OMNIROUTE_URL", "http://localhost:20128/v1/chat/completions"
        )

        # ── Signal bridge ──────────────────────────────────────────────
        self.bridge = VilonaSignalBridge()

        # ── Market data stub (injected or auto-discovered) ─────────────
        self._market_data: Any = None
        self._try_init_market_data()

        # ── Engines (lazy-imported) ────────────────────────────────────
        self._engines: dict[str, bool] = {}
        self._init_engines()

        # ── State ──────────────────────────────────────────────────────
        self._state: dict[str, Any] = {"last_update_id": 0}
        self._pending_signals: dict[str, dict[str, Any]] = {}
        self._user_last_analyze: dict[str, float] = {}
        self._user_last_direction: dict[str, dict[str, Any]] = {}
        self._autosync_users: set[str] = set()
        self._autosync_enabled = False

        # ── Scan interval for auto-loop ────────────────────────────────
        self._scan_interval_sec: int = 300
        self._default_pair: str = "gold"

    # ── Engine initialization ────────────────────────────────────────────

    def _try_init_market_data(self) -> None:
        """Attempt to load the UnifiedMarketData module."""
        try:
            from market_data import UnifiedMarketData  # type: ignore
            self._market_data = UnifiedMarketData()
            LOG.info("Market data layer loaded")
        except ImportError:
            LOG.warning("Market data layer unavailable")

    def _init_engines(self) -> None:
        """Lazy-import and flag engine availability."""
        engine_checks: dict[str, str] = {
            "layering": "layering",
            "quant": "quant_engine",
            "fvg": "fvg_detector",
            "crt": "crt_tbs_engine",
            "smc": "smc_scalper_engine",
            "hermes_liquidity": "hermes_liquidity_hunter",
            "learning": "learning_engine",
            "trade_tracker": "trade_tracker",
            "ultimate_smc": "ultimate_smc_engine",
        }
        for name, mod in engine_checks.items():
            try:
                __import__(mod)
                self._engines[name] = True
            except ImportError:
                self._engines[name] = False
        LOG.info("Engines loaded: %s", {k: v for k, v in self._engines.items() if v})

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        await super().start()
        self._schedule_background(self._auto_analysis_loop())

    def _register_commands(self) -> None:
        self._command_handlers = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "price": self._cmd_price,
            "analyze": self._cmd_analyze,
            "status": self._cmd_status,
            "subscribe": self._cmd_subscribe,
            "autosync": self._cmd_autosync,
            "donate": self._cmd_donate,
            "genkey": self._cmd_genkey,
            "listkeys": self._cmd_listkeys,
            "mykey": self._cmd_mykey,
            # Group 1 — Market Info
            "data": self._cmd_data,
            "killzone": self._cmd_killzone,
            "bridge_status": self._cmd_bridge_status,
            # Group 2 — Trade History & Stats
            "history": self._cmd_history,
            "recap": self._cmd_recap,
            "winrate": self._cmd_winrate,
            "mapping": self._cmd_mapping,
            # Group 3 — Signal System
            "signal": self._cmd_signal,
            "mtf": self._cmd_mtf,
            "engines": self._cmd_engines,
            "dashboard": self._cmd_dashboard,
            # Group 4 — Admin
            "restart_bot": self._cmd_restart_bot,
            "activate": self._cmd_activate,
        }

    # ── Telegram message sending ─────────────────────────────────────────

    async def _tg_send(self, text: str, chat_id: str | None = None) -> bool:
        """Send a message via raw Telegram API (supports HTML)."""
        target = chat_id or self.chat_id
        if not target or not self.bot_token:
            return False

        # Telegram 4096 char limit
        MAX_LEN = 4000  # noqa: N806
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 30] + "\n<i>... (dipotong)</i>"

        # HTML-safe: escape bare < > & that aren't part of tags
        TAG_OPEN = "\ue000"  # noqa: N806
        TAG_CLOSE = "\ue001"  # noqa: N806
        text = re.sub(r"<(/?[abi][^>]*)>", TAG_OPEN + r"\1" + TAG_CLOSE, text)
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(TAG_OPEN, "<").replace(TAG_CLOSE, ">")

        try:
            payload = {"chat_id": target, "text": text, "parse_mode": "HTML"}
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return bool(json.loads(r.read()))
        except Exception as e:
            if "Bad Request" in str(e) or "can't parse" in str(e):
                try:
                    plain = re.sub(r"<[^>]+>", "", text)
                    payload = {"chat_id": target, "text": plain[:MAX_LEN]}
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return bool(json.loads(r.read()))
                except Exception as e2:
                    LOG.error("tg_send fallback failed: %s", e2)
            else:
                LOG.error("tg_send failed: %s", e)
            return False

    async def _tg_answer_callback(self, cb_id: str, text: str = "") -> None:
        try:
            payload = json.dumps({"callback_query_id": cb_id, "text": text}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            LOG.warning("Webhook notification failed: %s", e)

    # ── Auto-analysis loop ───────────────────────────────────────────────

    async def _auto_analysis_loop(self) -> None:
        """Background loop scanning markets and dispatching alerts."""
        LOG.info("Auto-analysis loop started")
        while self._running:
            try:
                for pair in SUPPORTED_PAIRS[:5]:  # scan top 5
                    if not self._running:
                        break
                    sig, reason = self._detect_mechanical_signal(pair)
                    if reason and reason.startswith("⏳"):  # Killzone skip
                        LOG.info("Auto-analysis skipped: %s", reason)
                    elif sig and sig.get("action") != "HOLD":
                        display = pair.upper()
                        price = sig.get("entry", 0)
                        msg = format_signal_basic(sig, price, display)
                        await self._tg_send(msg)
                        self.bridge.post_signal(sig, price)
                        LOG.info("Auto signal: %s %s | %s", display, sig["action"], reason)
                    await asyncio.sleep(2)
            except Exception as e:
                LOG.error("Auto-analysis error: %s", e)
            await asyncio.sleep(self._scan_interval_sec)

    # ── Mechanical signal detection ──────────────────────────────────────

    def _detect_mechanical_signal(self, pair: str = "gold", price: float | None = None, ohlcv_bars: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, str | None]:  # noqa: E501
        """Mechanical signal: Quant + FVG + Hermes → fire without AI consensus.

        Killzone routing:
          - Forex/Metals (XAUUSD, USOIL): London/NY only
          - Crypto (BTC-USD, ETH-USD): 24/7, bypass
        """
        symbol = resolve_yahoo_symbol(pair)
        display = pair.upper()

        # ── Killzone gate ──────────────────────────────────────────
        is_forex_metal = display in ("XAUUSD", "USOIL")
        if is_forex_metal:
            lkz, nykz = killzone_active()
            if not (lkz or nykz):
                return None, f"⏳ {display} mechanical signal SKIPPED — outside London/NY killzone"

        if not self._market_data:
            return None, None
        if not ohlcv_bars:
            try:
                ohlcv_bars = self._market_data.get_bars_dicts(symbol, "15m", 80)  # type: ignore
            except Exception:
                return None, None
        if not ohlcv_bars or len(ohlcv_bars) < 15:
            return None, None
        if not price:
            price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))

        quant_result = None
        fvg_signals = []

        # Layer 1: Quant Engine
        if self._engines.get("quant"):
            try:
                from quant_engine import analyze_quantitative_pattern  # type: ignore
                qdata = [
                    {
                        "timestamp": b.get("timestamp", 0),
                        "open": float(b["open"]),
                        "high": float(b["high"]),
                        "low": float(b["low"]),
                        "close": float(b["close"]),
                        "volume": float(b.get("volume", 0)),
                    }
                    for b in ohlcv_bars
                ]
                quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
            except Exception as e:
                LOG.debug("Quantitative pattern analysis failed: %s", e)

        # Layer 2: FVG Detector
        if self._engines.get("fvg"):
            try:
                from fvg_detector import detect_fvg  # type: ignore
                fvg_signals = detect_fvg(ohlcv_bars, "M1")
            except Exception as e:
                LOG.debug("FVG detection failed: %s", e)

        # Quant + FVG alignment
        quant_bias = None
        if quant_result and quant_result.get("match_count", 0) >= 15:
            dom = quant_result.get("dominant_next")
            g = quant_result.get("green_pct", 0)
            r = quant_result.get("red_pct", 0)
            if dom == "G" and g >= 40:
                quant_bias = "BUY"
            elif dom == "R" and r >= 40:
                quant_bias = "SELL"

        fvg_bias = None
        fvg_sig_obj = None
        if fvg_signals:
            fvg_sig_obj = fvg_signals[0]
            if hasattr(fvg_sig_obj, "confidence") and fvg_sig_obj.confidence >= 0.20:
                fvg_bias = fvg_sig_obj.direction

        if quant_bias and fvg_bias and fvg_bias == quant_bias and fvg_sig_obj:
            confidence = round((quant_result["confidence_score"] + fvg_sig_obj.confidence) / 2, 2)  # type: ignore
            reasoning = (
                f"🤖 MECHANICAL SIGNAL | Quant {quant_bias} "
                f"({quant_result['green_pct']:.0f}%G/{quant_result['red_pct']:.0f}%R) "
                f"+ FVG {fvg_sig_obj.direction} ({fvg_sig_obj.fvg_zone.size_pips:.0f}pip)"
            )
            sig = {
                "action": quant_bias, "entry": fvg_sig_obj.entry, "sl": fvg_sig_obj.sl,
                "tp": fvg_sig_obj.tp2, "tp1": fvg_sig_obj.tp1, "tp2": fvg_sig_obj.tp2,
                "confidence": confidence, "rr_ratio": fvg_sig_obj.rr_ratio,
                "reasoning": reasoning, "ensemble": "mechanical", "voters": 0,
                "_model": "Quant+FVG", "grade": "B", "source": "mechanical_override",
                "symbol": symbol,
            }
            return sig, reasoning

        # Layer 3: Hermes Liquidity Hunter
        if self._engines.get("hermes_liquidity"):
            try:
                from hermes_liquidity_hunter import hermes_pipeline  # type: ignore
                ohlcv_m15 = None
                if self._market_data:
                    try:
                        m15_bars = self._market_data.get_ohlcv(symbol, "15m", 80)  # type: ignore
                        if m15_bars and len(m15_bars) >= 30:
                            ohlcv_m15 = [
                                {"timestamp": b.timestamp, "open": b.open, "high": b.high,
                                 "low": b.low, "close": b.close, "volume": b.volume}
                                for b in m15_bars
                            ]
                    except Exception as e:
                        LOG.debug("M15 bar fetch failed: %s", e)

                if ohlcv_m15:
                    hermes_signal = hermes_pipeline(ohlcv_bars, ohlcv_m15, price)
                    if hermes_signal and hermes_signal.action in ("SELL", "BUY"):
                        action = hermes_signal.action
                        entry = hermes_signal.entry_price
                        sl = hermes_signal.stop_loss
                        tp = hermes_signal.take_profit_1
                        rr = hermes_signal.risk_reward_ratio

                        # Safety checks
                        MIN_SL_DIST = 2.0  # noqa: N806
                        MIN_RR_REQ = 1.2  # noqa: N806
                        sl_dist = abs(entry - sl)

                        if (action == "BUY" and sl >= entry) or (action == "SELL" and sl <= entry):
                            return None, None
                        if sl_dist < MIN_SL_DIST:
                            return None, None
                        if rr < MIN_RR_REQ:
                            return None, None

                        sig = {
                            "action": action, "entry": entry,
                            "sl": sl, "tp": tp,
                            "tp1": tp, "tp2": hermes_signal.take_profit_2,
                            "confidence": hermes_signal.confidence,
                            "rr_ratio": rr,
                            "reasoning": hermes_signal.reason,
                            "ensemble": "mechanical", "voters": 0,
                            "_model": "HermesSMC",
                            "grade": "A" if rr >= 2.0 else "B",
                            "source": "hermes_liquidity_sweep",
                            "symbol": symbol,
                        }
                        return sig, hermes_signal.reason
            except Exception as e:
                LOG.debug("Hermes liquidity sweep failed: %s", e)

        return None, None

    # ── AI-powered analysis ──────────────────────────────────────────────

    async def _ai_analyze(self, pair: str = "gold") -> tuple[dict[str, Any] | None, str | None]:
        """
        Analyze a pair using 2-Tier AI pipeline:
          Tier 1 (Workhorse = Gemini): scores every setup. HALT if < 75%.
          Tier 2 (Sniper = DeepSeek via OmniRoute): cross-check only Tier-1-passed setups.
          
        Killzone routing:
          - Forex/Metals (XAUUSD, USOIL): London/NY only
          - Crypto (BTC, ETH): 24/7 bypass
        """
        symbol = resolve_yahoo_symbol(pair)
        display = pair.upper()

        # ── Killzone gate ──────────────────────────────────────────
        is_forex_metal = display in ("XAUUSD", "USOIL")
        if is_forex_metal:
            lkz, nykz = killzone_active()
            if not (lkz or nykz):
                LOG.info("⏳ AI analysis SKIPPED for %s — outside London/NY killzone", display)
                return None, f"⏳ {display} AI analysis SKIPPED — outside London/NY killzone"

        # Fetch OHLCV
        ohlcv_bars = None
        if self._market_data:
            try:
                is_stock = pair.upper() in ("AAPL", "TSLA", "MSFT", "NVDA") or pair.upper().endswith(".JK")  # noqa: E501
                interval = "1d" if is_stock else "15m"
                bars = self._market_data.get_bars_dicts(symbol, interval, 80)  # type: ignore
                if bars:
                    ohlcv_bars = [
                        {"t": b["timestamp"], "o": b["open"], "h": b["high"],
                         "l": b["low"], "c": b["close"]}
                        for b in bars[-20:]
                    ]
            except Exception as e:
                LOG.debug("OHLCV bar fetch for AI analysis failed: %s", e)

        if not ohlcv_bars:
            return None, "No market data available."

        # Build prompt
        bars_text = json.dumps(ohlcv_bars, indent=2)
        user_prompt = (
            f"Analyze {display} ({symbol}) for BUY/SELL/HOLD decision.\n\n"
            f"OHLCV Data (last 20 bars):\n{bars_text}\n\n"
            f"Current session: {session_label()}\n"
            f"Return valid JSON only."
        )

        # ════════════════════════════════════════════════════════════════════
        # TIER 1 — WORKHORSE: Gemini (native API key from .env)
        # ════════════════════════════════════════════════════════════════════
        tier1_result = await self._call_tier1_gemini(user_prompt)
        if tier1_result is None:
            return None, "Gemini unreachable."

        tier1_conf = tier1_result.get("confidence", 0)
        if tier1_conf < 75:
            LOG.info("⏭ Gemini confidence %.0f%% < 75%% — pipeline halted. No sniper call.", tier1_conf)
            return {"action": "HOLD", "confidence": tier1_conf, "grade": "C",
                    "reasoning": f"Gemini workhorse confidence {tier1_conf:.0f}% below threshold",
                    "symbol": symbol, "source": "gemini-workhorse"}, None

        LOG.info("✅ Gemini workhorse passed (%.0f%%). Escalating to sniper...", tier1_conf)

        # ════════════════════════════════════════════════════════════════════
        # TIER 2 — SNIPER: DeepSeek via OmniRoute (premium quota protected)
        # ════════════════════════════════════════════════════════════════════
        sniper_prompt = (
            f"Gemini (Tier 1) analyzed {display} and found a potential setup with "
            f"{tier1_conf:.0f}% confidence. Cross-check this trade:\n\n"
            f"{user_prompt}\n\n"
            f"Gemini findings: {json.dumps(tier1_result)}"
        )
        try:
            sniper_response = await self._call_ai(
                self._build_system_prompt(), sniper_prompt
            )
            sig = extract_json(sniper_response)
            if sig and sig.get("action") in ("BUY", "SELL", "HOLD"):
                sig["symbol"] = symbol
                sig["source"] = "gemini→deepseek-sniper"
                sig["gemini_confidence"] = tier1_conf
                LOG.info("🎯 Sniper confirmed: %s @ %.0f%%", sig["action"], sig.get("confidence", 0))
                return sig, sig.get("reasoning", "")
        except Exception as e:
            LOG.error("Sniper (DeepSeek) call failed: %s", e)

        # If sniper fails but Gemini was confident, return Gemini's finding as fallback
        return tier1_result, tier1_result.get("reasoning", "Sniper unavailable — Gemini workhorse only.")

    async def _call_tier1_gemini(self, user_prompt: str) -> dict[str, Any] | None:
        """
        Tier 1 Workhorse — native Gemini API (direct, no pool overhead).
        Key: os.getenv('GEMINI_API_KEY').
        Falls back to OmniRoute pool if native key is missing or fails.
        """
        SYSTEM_PROMPT = (  # noqa: N806
            "You are Vilona Trade FX Tier-1 SMC Workhorse (Gemini). "
            "Analyze the OHLCV data using Smart Money Concepts: BOS, FVG, liquidity sweeps, "
            "order blocks, and market structure. "
            "Return ONLY valid JSON with action, confidence (0-100), entry, sl, tp, reasoning. "
            "If the setup is unclear or structure is messy, be honest and return HOLD with low confidence."
        )
        analysis_prompt = (
            f"{SYSTEM_PROMPT}\n\n{user_prompt}\n\n"
            'Return JSON: {"action":"BUY|SELL|HOLD","entry":0.0,"sl":0.0,"tp":0.0,'
            '"confidence":0,"grade":"A|B|C|D","reasoning":"..."}'
        )

        gemini_key = self.gemini_key
        if gemini_key:
            try:
                result = await self._call_gemini_native(gemini_key, analysis_prompt)
                if result:
                    return result
                LOG.warning("Gemini native call returned None — trying OmniRoute pool.")
            except Exception as e:
                LOG.warning("Gemini native call failed: %s — falling back to OmniRoute pool.", e)
        else:
            LOG.info("No GEMINI_API_KEY set — using OmniRoute pool for Tier 1.")

        # Fallback: OmniRoute pool with gemini model
        try:
            resp = await self._call_ai_with_model("gemini-2.0-flash", analysis_prompt)
            return extract_json(resp)
        except Exception as e:
            LOG.error("Gemini Tier 1 (pool fallback) failed: %s", e)
            return None

    async def _call_gemini_native(self, api_key: str, prompt: str) -> dict[str, Any] | None:
        """Call Gemini REST API directly (not via OmniRoute)."""
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent"
            f"?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1024,
            },
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json(text)

    async def _call_ai_with_model(self, model: str, user_content: str) -> str:
        """Call OmniRoute with a specific model override (handles SSE)."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.deepseek_key or self.gemini_key or 'sk-no-...ured'}",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        req = urllib.request.Request(
            self.omniroute_url,
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
        content = _parse_sse(raw)
        if content:
            return content
        return json.loads(raw)["choices"][0]["message"]["content"]

    def _build_system_prompt(self) -> str:
        return (
            "Kamu adalah Vilona Trade FX — Full-Stack Institutional AI Trading System.\n"
            "⚠️ CRITICAL RULE: Analisa HARUS berdasarkan DATA OHLCV yang diberikan.\n"
            "DILARANG mengarang harga, level, atau pola yang tidak ada di data.\n"
            "Jika data tidak tersedia → HOLD.\n\n"
            "OUTPUT: JSON only. No markdown, no text outside JSON.\n"
            '{"action":"BUY|SELL|HOLD","entry":0.0,"sl":0.0,"tp":0.0,'
            '"confidence":0.0,"grade":"A|B|C|D",'
            '"reasoning":"6-8 kalimat analisa lengkap..."}'
        )

    async def _call_ai(self, system: str, user: str) -> str:
        """Call OmniRoute AI endpoint (handles SSE streaming)."""
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.deepseek_key or 'sk-no-...ired'}",
        }
        req = urllib.request.Request(
            self.omniroute_url,
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read().decode()
            # Parse SSE streaming response
            content = _parse_sse(raw)
            if content:
                return content
            # Fallback: try direct JSON
            return json.loads(raw)["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"AI call failed: {e}")

    # ── Command handlers ─────────────────────────────────────────────────

    async def _cmd_start(self, args: list[str], chat_id: str | None = None) -> str:
        _target = chat_id or self.chat_id
        lines = [
            "🔥 <b>REVOLUSI TRADING DIMULAI: FULL AI, NO BULLSHIT.</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "Selamat datang di markas besar Vilona Trade FX.",
            "Seluruh infrastruktur di sini — dari analisa teknikal",
            "hingga eksekusi sinyal — dijalankan oleh",
            "<b>FULL AI AGENTS 24/7.</b>",
            "",
            "🧠 /signal — Signal dari 9 engines",
            "📊 /dashboard — Live dashboard web",
            "📱 /help — Semua command",
            "━━━━━━━━━━━━━━━━━━━━━",
            "⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot",
        ]
        return "\n".join(lines)

    async def _cmd_help(self, args: list[str], chat_id: str | None = None) -> str:
        lines = [
            "⚙️ <b>VILONA AI — COMMAND CENTER</b>",
            "━━━━━━━━━━━━━━━━",
            "",
            "🧠 <b>AI SIGNAL SYSTEM 🔥</b>",
            "/signal — Generate sinyal dari MTF + 9 engines",
            "/mtf — Matrix 5TF × 9 engines (top-down)",
            "/engines — Engine readings per strategi",
            "/dashboard — Buka live dashboard web",
            "",
            "👑 <b>PILAR UTAMA</b>",
            "/start — Reboot Markas Komando",
            "/analyze — Perintahkan AI Scan Market",
            "/price — Cek harga real-time",
            "/data — Market overview",
            "/status — Cek Kuota & Akses VIP",
            "/donate — Isi Bahan Bakar AI ⚡",
            "",
            "📊 <b>TRADING TOOLS</b>",
            "/mapping — Mapping harian + level S/R",
            "/killzone — Radar sesi market aktif",
            "/winrate — Statistik performa",
            "/history — Riwayat trade terakhir",
            "/recap — Rekap harian",
            "",
            "🔧 <b>POWER TOOLS</b>",
            "/autosync — Auto-trade ke EA",
            "/bridge_status — Cek koneksi EA",
            "",
            "━━━━━━━━━━━━━━━━",
            "📞 Jalur Privat Investor: @codergaboets",
        ]
        return "\n".join(lines)

    async def _cmd_price(self, args: list[str], chat_id: str | None = None) -> str:
        pair = args[0] if args else self._default_pair
        symbol = resolve_yahoo_symbol(pair)

        if not self._market_data:
            return "❌ Market data unavailable."

        try:
            quote = self._market_data.get_quote(symbol)  # type: ignore
            if quote and quote.price > 0:
                return (
                    f"💰 <b>{pair.upper()}</b> — {symbol}\n"
                    f"Price: <code>{quote.price:.4g}</code>\n"
                    f"Time: {wib_fmt()}"
                )
        except Exception as e:
            LOG.error("Price fetch error: %s", e)

        return f"❌ Could not fetch price for {pair.upper()}."

    async def _cmd_analyze(self, args: list[str], chat_id: str | None = None) -> str:
        pair = args[0] if args else self._default_pair
        display = pair.upper()

        # Check manual-mode guard
        target = chat_id or ""
        if target and target in self._pending_signals:
            return "⏰ Sinyal sebelumnya masih berjalan. Tunggu 5 menit."

        last_time = self._user_last_analyze.get(target, 0)
        if last_time and (time.time() - last_time) < 60:
            remaining = int(60 - (time.time() - last_time))
            return f"⏳ Tunggu {remaining} detik sebelum analisa berikutnya."

        msg_lines = [f"🔍 <b>Analyzing {display}...</b>\nPlease wait 10-20 seconds."]
        await self._tg_send("\n".join(msg_lines), chat_id=target)

        self._user_last_analyze[target] = time.time()

        # Try mechanical first, then AI
        sig, reason = self._detect_mechanical_signal(pair)
        if not sig:
            sig, reason = await self._ai_analyze(pair)

        if not sig or sig.get("action") == "HOLD":
            return f"⚪ <b>{display}</b> — HOLD\n💡 <i>{reason or 'No strong setup.'}</i>"

        entry_price = sig.get("entry", 0)
        msg = format_signal_basic(sig, entry_price, display)

        # Store pending signal for trade/skip
        if target:
            self._pending_signals[target] = {
                "sig": sig,
                "price": entry_price,
                "expires": time.time() + 300,
            }

        return msg

    async def _cmd_status(self, args: list[str], chat_id: str | None = None) -> str:
        """Bot and bridge health status."""
        lines = [
            "🛡️ <b>VILONA BOT STATUS</b>",
            "━━━━━━━━━━━━━━━━",
            f"🤖 Bot: {'🟢 ACTIVE' if self._running else '🔴 STOPPED'}",
            f"📊 Market Data: {'🟢 OK' if self._market_data else '🔴 N/A'}",
            f"⚙️ Engines: {sum(1 for v in self._engines.values() if v)}/{len(self._engines)} loaded",
            f"⏱️ Session: {wib_fmt()}",
        ]

        # Check bridge health
        try:
            req = urllib.request.Request("http://localhost:8765/health")
            with urllib.request.urlopen(req, timeout=5) as r:
                health = json.loads(r.read())
            bridge_ok = health.get("status") == "ok"
            lines.append(f"🌐 Bridge: {'🟢 ONLINE' if bridge_ok else '🔴 DOWN'}")
            if bridge_ok:
                uptime = int(float(health.get("uptime_seconds", 0) or 0))
                lines.append(f"⏱️ Bridge Uptime: {uptime // 3600}j {(uptime % 3600) // 60}m")
                lines.append(f"📦 Queue: {health.get('queue_size', 0)}")
        except Exception:
            lines.append("🌐 Bridge: 🔴 DOWN")

        return "\n".join(lines)

    async def _cmd_subscribe(self, args: list[str], chat_id: str | None = None) -> str:
        return (
            "💎 <b>Vilona Trade FX</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Semua fitur saat ini GRATIS dan AKTIF PERMANEN.\n\n"
            "📡 Auto-analysis berjalan otomatis.\n"
            "🔑 License keys untuk EA tersedia.\n\n"
            "Dukung server: /donate"
        )

    async def _cmd_autosync(self, args: list[str], chat_id: str | None = None) -> str:
        target = chat_id or ""
        if not target:
            return "❌ Chat ID tidak ditemukan."
        if target in self._autosync_users:
            self._autosync_users.discard(target)
            return "🔇 Auto-sync dimatikan."
        self._autosync_users.add(target)
        return "🔊 Auto-sync diaktifkan! Sinyal akan dikirim otomatis."

    async def _cmd_donate(self, args: list[str], chat_id: str | None = None) -> str:
        return (
            "💚 <b>Dukung Server AI</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Bot ini berjalan di server AI 24/7.\n"
            "Dukung biaya operasional:\n\n"
            "☕️ Rp15K — Traktir kopi\n"
            "📚 Rp25K — Dukung AI belajar\n"
            "🚀 Rp50K — Bensin full server\n\n"
            "📞 Admin: @codergaboets\n\n"
            "Semua donatur = 🟢 DONATUR AKTIF PERMANEN."
        )

    async def _cmd_genkey(self, args: list[str], chat_id: str | None = None) -> str:
        return "🔑 License key generation: use admin panel or POST /admin/generate-key"

    async def _cmd_listkeys(self, args: list[str], chat_id: str | None = None) -> str:
        return "🔑 License keys: use admin panel on localhost:8765/admin/keys"

    async def _cmd_mykey(self, args: list[str], chat_id: str | None = None) -> str:
        return "🔑 Your license key: check with admin or use /genkey"

    # ── Group 1: Market Info ──────────────────────────────────────────────

    async def _cmd_data(self, args: list[str], chat_id: str | None = None) -> str:
        """Multi-asset market overview."""
        lines = ["📊 <b>Market Overview</b>", "━━━━━━━━━━━━━━━━"]
        assets = [
            ("XAUUSD", "gold", "$"), ("BTCUSD", "btc", "$"),
            ("EURUSD", "eurusd", "$"), ("USOIL", "oil", "$"),
            ("DXY", "dxy", ""), ("BBCA", "bbca", "Rp"),
        ]
        for name, pair, curr in assets:
            try:
                p = self._fetch_price(pair)
                if p:
                    if curr == "Rp":
                        lines.append(f"{name}: {curr}{p:,.0f}")
                    elif p > 100:
                        lines.append(f"{name}: {curr}{p:,.2f}")
                    else:
                        lines.append(f"{name}: {curr}{p:.5f}")
                else:
                    lines.append(f"{name}: N/A")
            except Exception:
                lines.append(f"{name}: N/A")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"🕐 {wib_fmt()}")
        return "\n".join(lines)

    async def _cmd_killzone(self, args: list[str], chat_id: str | None = None) -> str:
        """Current session & killzone status."""
        lkz, nykz = killzone_active()
        lines = [
            f"🕐 <b>Session: {session_label()}</b>",
            "━━━━━━━━━━━━━━━━",
            f"London KZ: {'🟢 ACTIVE' if lkz else '🔴 Off'}",
            f"NY KZ: {'🟢 ACTIVE' if nykz else '🔴 Off'}",
            "━━━━━━━━━━━━━━━━",
            wib_fmt(),
        ]
        return "\n".join(lines)

    async def _cmd_bridge_status(self, args: list[str], chat_id: str | None = None) -> str:
        """Signal bridge health."""
        try:
            req = urllib.request.Request("http://localhost:8765/health")
            with urllib.request.urlopen(req, timeout=5) as r:
                health = json.loads(r.read())
        except Exception:
            health = {}
        try:
            req = urllib.request.Request("http://localhost:8765/accounts")
            with urllib.request.urlopen(req, timeout=5) as r:
                accounts = json.loads(r.read())
        except Exception:
            accounts = {}
        try:
            req = urllib.request.Request("http://localhost:8787/health")
            with urllib.request.urlopen(req, timeout=5) as r:
                webhook = json.loads(r.read())
        except Exception:
            webhook = {}

        bridge_ok = health.get("status") == "ok"
        webhook_ok = webhook.get("status") == "ok"
        instances = accounts.get("total_instances", 0) if isinstance(accounts, dict) else 0
        master_keys = accounts.get("master_keys_count", 0) if isinstance(accounts, dict) else 0
        queue_size = health.get("queue_size", 0)
        uptime = int(float(health.get("uptime_seconds", 0) or 0))
        uptime_txt = f"{uptime // 3600}j {(uptime % 3600) // 60}m"

        lines = [
            "🛡️ <b>VILONA BRIDGE STATUS</b>",
            "━━━━━━━━━━━━━━━━",
            f"🌐 Bridge: {'🟢 ONLINE' if bridge_ok else '🔴 DOWN'}",
            f"💳 Webhook: {'🟢 ONLINE' if webhook_ok else '🔴 DOWN'}",
            f"⏱️ Uptime: {uptime_txt}",
            f"📦 Queue: {queue_size}",
            "━━━━━━━━━━━━━━━━",
            f"🔑 Master Key Aktif: {master_keys}",
            f"🖥️ EA Instance Online: {instances}",
        ]

        if isinstance(accounts, dict) and accounts.get("instances"):
            online = 0
            for data in accounts.get("instances", {}).values():
                if data.get("online"):
                    online += 1
            lines.append(f"🟢 Instance Live: {online}/{instances}")

        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(wib_fmt())
        return "\n".join(lines)

    # ── Group 2: Trade History & Stats ────────────────────────────────────

    async def _cmd_history(self, args: list[str], chat_id: str | None = None) -> str:
        """Last 15 trades with emoji grades."""
        try:
            from tradebot.monitoring.tracker import TradeTracker
            tracker = TradeTracker()
            trades = tracker.get_recent_trades(15)
        except Exception:
            try:
                from trade_tracker import get_recent_trades  # type: ignore
                trades = get_recent_trades(15)
            except Exception:
                return "📭 Trade tracker tidak tersedia."

        if not trades:
            return "📭 Belum ada riwayat trade."

        lines = ["📋 <b>RIWAYAT TRADE</b>", "━━━━━━━━━━━━━━━━"]
        for t in trades[:15]:
            outcome = t.get("outcome", "?")
            emoji = "✅" if outcome == "TP_HIT" else "❌" if outcome == "SL_HIT" else "⚪"
            pips = t.get("pips", 0)
            usd = t.get("profit_usd", 0)
            idr = t.get("profit_idr", 0)
            action = t.get("action", "?")
            sym = t.get("symbol", "?")
            close_t = t.get("close_time", "")[:16].replace("T", " ")
            lines.append(
                f"{emoji} {action} {sym} | {outcome}\n"
                f"   Pips: {pips:+.1f} | ${usd:+.2f} (Rp {idr:+,})\n"
                f"   {close_t}"
            )
        return "\n".join(lines)

    async def _cmd_recap(self, args: list[str], chat_id: str | None = None) -> str:
        """Daily recap with P&L summary."""
        date_str = args[0] if args else ""
        try:
            from tradebot.monitoring.tracker import TradeTracker
            tracker = TradeTracker()
            recap = tracker.get_daily_trades(date_str)
        except Exception:
            try:
                from trade_tracker import get_daily_trades  # type: ignore
                recap = get_daily_trades(date_str)
            except Exception:
                return "📭 Trade tracker tidak tersedia."

        if not date_str:
            date_str = datetime.now(WIB).strftime("%Y-%m-%d")

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
            date_display = f"{day_names[dt.weekday()]}, {dt.strftime('%d %B %Y')}"
        except Exception:
            date_display = date_str

        total = recap.get("total_signals", 0)
        wins = recap.get("wins", 0)
        losses = recap.get("losses", 0)
        wr = recap.get("win_rate", 0)
        pips = recap.get("total_pips", 0)
        micro = recap.get("micro_profit", 0)
        micro_pct = recap.get("micro_profit_pct", 0)
        micro_idr = recap.get("micro_profit_idr", 0)

        perf = "🟢 PROFIT" if micro > 0 else "🔴 LOSS" if micro < 0 else "⚪ FLAT"

        lines = [
            "📊 <b>REKAP SINYAL HARIAN</b>",
            f"🗓 {date_display}",
            "━━━━━━━━━━━━━━━━",
            "",
            f"📡 <b>Total Sinyal:</b> {total}",
            f"✅ Win: {wins} | ❌ Loss: {losses} | 📊 WR: {wr:.1f}%",
            "",
            "━━━━━━━━━━━━━━━━",
            f"📐 <b>Total Pips:</b> {pips:+.1f}",
            "",
        ]

        pairs = recap.get("pairs", {})
        if pairs:
            lines.append("💱 <b>Pair yang Di-trade:</b>")
            for sym, stats in sorted(pairs.items()):
                p_emoji = "✅" if stats.get("pips", 0) >= 0 else "❌"
                lines.append(
                    f"   {p_emoji} {sym}: {stats.get('total', 0)} sinyal | "
                    f"{stats.get('pips', 0):+.1f} pips | "
                    f"{stats.get('wins', 0)}W/{stats.get('losses', 0)}L"
                )

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━",
            "💵 <b>SIMULASI MODAL $100 (0.01 Lot)</b>",
            "",
            f"{perf}: <b>${micro:+.2f}</b> (Rp {micro_idr:+,})",
            f"Return: <b>{micro_pct:+.1f}%</b> dalam 1 hari",
            "",
            "━━━━━━━━━━━━━━━━",
            "",
            "⚡ <i>Ini simulasi — bukan hasil trading sebenarnya.</i>",
            "📱 Trading real: /analyze xauusd",
            "🤖 Auto-trade: /autosync on",
            "",
            "<i>#VilonaTradeFX #AITrading #XAUUSD</i>",
        ])
        return "\n".join(lines)

    async def _cmd_winrate(self, args: list[str], chat_id: str | None = None) -> str:
        """Win rate statistics."""
        try:
            from tradebot.monitoring.tracker import TradeTracker
            tracker = TradeTracker()
            stats = tracker.get_stats()
        except Exception:
            try:
                from trade_tracker import get_stats  # type: ignore
                stats = get_stats()
            except Exception:
                return "📭 Trade tracker tidak tersedia."

        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr = stats.get("win_rate", 0)
        open_pos = stats.get("open_positions", 0)

        perf = "🟢" if wr >= 60 else "🟡" if wr >= 40 else "🔴"
        lines = [
            "📊 <b>TRADE PERFORMANCE</b>",
            "━━━━━━━━━━━━━━━━",
            f"{perf} Win Rate: <b>{wr:.1f}%</b> ({wins}W / {losses}L)",
            f"📈 Total Trades: {total} | Open: {open_pos}",
            "━━━━━━━━━━━━━━━━",
            f"💰 Total Pips: {stats.get('total_pips', 0):+.1f}",
            f"💵 Profit: <b>${stats.get('total_profit_usd', 0):+,.2f}</b> (Rp {stats.get('total_profit_idr', 0):+,.0f})",  # noqa: E501
        ]
        if wins > 0:
            lines.append(f"✅ Best Win: +{stats.get('best_win_pips', 0):.1f} pips")
        if losses > 0:
            lines.append(f"❌ Worst Loss: -{stats.get('worst_loss_pips', 0):.1f} pips")
        return "\n".join(lines)

    async def _cmd_mapping(self, args: list[str], chat_id: str | None = None) -> str:
        """Daily market mapping / S/R levels."""
        now = wib_now()
        day_name = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"][now.weekday()]

        lines = [
            "📐 MARKET MAPPING",
            f"🗓 {day_name}, {now.strftime('%d %B %Y')}",
            "━━━━━━━━━━━━━━━━",
            "",
            f"🕐 Status: {'🟡 WEEKEND CRYPTO MODE — Forex Tutup, Crypto BUKA' if is_weekend() else '🟢 MARKET BUKA'}",  # noqa: E501
            "",
        ]

        # Monday Sentiment
        if now.weekday() == 0 and self._market_data:
            try:
                dxy_q = self._market_data.get_quote("DX-Y.NYB", force=True)  # type: ignore
                dxy_val = dxy_q.price if dxy_q else None
                sent_label = "BULLISH" if (dxy_val is not None and dxy_val < 103) else "BEARISH"
                lines.append(f"📅 Monday Sentiment: {sent_label} — Waspadai Gaps & Volatilitas Pembukaan.")  # noqa: E501
                lines.append("")
            except Exception:
                pass

        # Key levels per asset
        if self._market_data:
            for pair, disp, yahoo_sym, _is_forex in AUTO_SCAN_ASSETS:
                try:
                    bars = self._market_data.get_ohlcv(yahoo_sym, "1h", 50)  # type: ignore
                    if not bars or len(bars) < 5:
                        continue
                    high_24h = max(b.high for b in bars[-24:]) if len(bars) >= 24 else max(b.high for b in bars)  # noqa: E501
                    low_24h = min(b.low for b in bars[-24:]) if len(bars) >= 24 else min(b.low for b in bars)  # noqa: E501
                    close = bars[-1].close
                    high_w = max(b.high for b in bars[-min(40, len(bars)):])
                    low_w = min(b.low for b in bars[-min(40, len(bars)):])

                    r1 = high_24h + (high_24h - low_24h) * 0.382
                    s1 = low_24h - (high_24h - low_24h) * 0.382

                    sma20 = sum(b.close for b in bars[-20:]) / min(20, len(bars))
                    trend = "📈 BULLISH" if close > sma20 else ("📉 BEARISH" if close < sma20 else "➡️ SIDEWAYS")  # noqa: E501

                    lines.append("")
                    lines.append(f"💱 {disp}")
                    lines.append(f"   Price: {close:.2f} | {trend}")
                    lines.append(f"   Range 24H: {low_24h:.2f} — {high_24h:.2f}")
                    lines.append(f"   Resistance: {r1:.2f} | Support: {s1:.2f}")
                    lines.append(f"   Weekly High: {high_w:.2f} | Low: {low_w:.2f}")
                except Exception:
                    pass

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━",
            "📌 Mapping ini BUKAN sinyal trading.",
            "🤖 Sinyal auto hanya Mon-Fri saat market buka.",
            "📱 /analyze untuk analisa manual.",
            "",
            "#VilonaTradeFX #MarketMapping #TechnicalAnalysis",
        ])
        return "\n".join(lines)

    # ── Group 3: Signal System ────────────────────────────────────────────

    async def _cmd_signal(self, args: list[str], chat_id: str | None = None) -> str:
        """Generate signal from MTF + 9 engines."""
        try:
            from engine_consensus import run_engine_consensus  # type: ignore
            from signal_calculator import compute_signal, format_signal_telegram  # type: ignore
        except ImportError:
            return "❌ Signal engine tidak tersedia. Pastikan engine_consensus dan signal_calculator terinstalasi."  # noqa: E501

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ Engine consensus error: {e}"

        if not result:
            return "❌ Engine consensus gagal — coba lagi nanti."

        hier = result.get("hierarchical", {})
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0) * 100
        align = hier.get("mtf_alignment", "NONE")
        macro = hier.get("macro_trend", "NEUTRAL")

        msg = (
            f"🏛 <b>MTF TOP-DOWN MATRIX</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Macro: {macro}\n"
            f"Alignment: {align}\n"
            f"Consensus: {score:.0f}%\n"
            f"Verdict: <b>{verdict}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        tfs = result.get("timeframes", {})
        for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
            tf = tfs.get(tf_name, {})
            if tf:
                v = tf.get("verdict", "?")
                c = tf.get("consensus_pct", 0) * 100
                msg += f"{tf_name}: {v} ({c:.0f}%)\n"

        try:
            sig = compute_signal(result)
        except Exception:
            sig = None

        if sig:
            msg += "━━━━━━━━━━━━━━━━━━━━━\n"
            msg += format_signal_telegram(sig)
            # Log to trade log
            try:
                from signal_calculator import log_signal  # type: ignore
                log_signal(sig)
            except Exception:
                pass
        else:
            msg += "\n⚠️ Quality gate blocked — belum memenuhi syarat entry."

        return msg

    async def _cmd_mtf(self, args: list[str], chat_id: str | None = None) -> str:
        """MTF matrix display (5TF × 9 engines)."""
        try:
            from engine_consensus import run_engine_consensus  # type: ignore
        except ImportError:
            return "❌ Engine consensus tidak tersedia."

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ MTF error: {e}"

        if not result:
            return "❌ Engine data unavailable."

        hier = result.get("hierarchical", {})
        tfs = result.get("timeframes", {})
        macro = hier.get("macro_trend", "?")
        align = hier.get("mtf_alignment", "?")
        verdict = hier.get("verdict", "HOLD")
        score = hier.get("consensus_score", 0) * 100

        engine_names = {
            "quant": "Q", "fvg": "FV", "hermes": "He", "crt": "CR",
            "smc": "SM", "trend": "Tr", "ultimate": "Ul", "sequoia": "Se", "tv": "TV",
        }

        msg = (
            f"🧬 <b>MTF ENGINE MATRIX — XAUUSD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏛 {macro} | {align} | {verdict} ({score:.0f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
            tf = tfs.get(tf_name, {})
            if tf:
                v = tf.get("verdict", "?")
                c = tf.get("consensus_pct", 0) * 100
                engs = tf.get("engines", {})
                eng_line = " ".join(
                    f"{engine_names.get(k, k[:2])}:{e.get('direction', '?')[:1]}"
                    for k, e in engs.items()
                )
                msg += f"\n<b>{tf_name}</b> {v} ({c:.0f}%)\n{eng_line}\n"

        msg += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 Dashboard: phantomfx.aitradepulse.com/dashboard"
        )
        return msg

    async def _cmd_engines(self, args: list[str], chat_id: str | None = None) -> str:
        """Live engine readings for all 9 strategies."""
        try:
            from engine_consensus import run_engine_consensus  # type: ignore
        except ImportError:
            return "❌ Engine consensus tidak tersedia."

        try:
            result = run_engine_consensus(symbol="XAUUSD")
        except Exception as e:
            return f"❌ Engine error: {e}"

        if not result:
            return "❌ Engine data unavailable."

        tfs = result.get("timeframes", {})
        hier = result.get("hierarchical", {})

        # Aggregate engine votes across all TFs
        engine_votes: dict[str, dict[str, int]] = {}
        for tf_name, tf in tfs.items():
            for eng_name, eng in tf.get("engines", {}).items():
                if eng_name not in engine_votes:
                    engine_votes[eng_name] = {"BUY": 0, "SELL": 0, "HOLD": 0}
                d = eng.get("direction", "HOLD")
                engine_votes[eng_name][d] = engine_votes[eng_name].get(d, 0) + 1

        display_names = {
            "quant": "📊 Quant", "fvg": "🕳 FVG", "hermes": "⚡ Hermes",
            "crt": "🔀 CRT/TBS", "smc": "🏦 SMC", "trend": "📈 Trend",
            "ultimate": "🎯 Ultimate", "sequoia": "🌲 Sequoia", "tv": "📺 TV",
        }

        msg = (
            f"🔧 <b>ENGINE READINGS — XAUUSD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏛 {hier.get('macro_trend', '?')} | {hier.get('mtf_alignment', '?')}\n"
            f"Verdict: <b>{hier.get('verdict', 'HOLD')}</b> ({hier.get('consensus_score', 0) * 100:.0f}%)\n"  # noqa: E501
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        for eng_name, votes in engine_votes.items():
            direction = max(votes, key=votes.get)  # type: ignore[arg-type]
            emoji = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪️"
            msg += (
                f"{emoji} {display_names.get(eng_name, eng_name)}: "
                f"<b>{direction}</b> "
                f"(🟢{votes['BUY']} 🔴{votes['SELL']} ⚪️{votes['HOLD']})\n"
            )

        msg += (
            "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 /signal — Generate signal dari matrix ini"
        )
        return msg

    async def _cmd_dashboard(self, args: list[str], chat_id: str | None = None) -> str:
        """Dashboard link."""
        return (
            "📊 <b>VILONA AI — LIVE DASHBOARD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Pantau market real-time:\n"
            "• MTF Matrix 5TF × 9 Engines\n"
            "• Signal History & Grade\n"
            "• Trade Tracker & Win Rate\n"
            "• Live Price XAUUSD + Chart TV\n\n"
            "🌐 <a href='https://phantomfx.aitradepulse.com/dashboard'>Buka Dashboard →</a>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 /signal — Cek signal sekarang"
        )

    # ── Group 4: Admin ────────────────────────────────────────────────────

    ADMIN_IDS = {"5220170786", "157228659"}

    def _is_admin(self, chat_id: str) -> bool:
        admin_env = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", "")
        return chat_id in self.ADMIN_IDS or (admin_env and chat_id == admin_env)

    async def _cmd_restart_bot(self, args: list[str], chat_id: str | None = None) -> str:
        """Admin restart — triggers systemd auto-restart."""
        target = chat_id or ""
        if not self._is_admin(target):
            return "⛔ Hanya admin yang bisa execute command ini."
        LOG.warning("♻️ Bot restart initiated by admin %s", target)
        # Send restart notice before exiting
        await self._tg_send("♻️ Sistem bot sedang di-restart, mohon tunggu sebentar...", chat_id)
        await asyncio.sleep(2)
        os._exit(0)
        return ""  # unreachable

    async def _cmd_activate(self, args: list[str], chat_id: str | None = None) -> str:
        """Admin: manual user activation."""
        target = chat_id or ""
        if not self._is_admin(target):
            return "⛔ Admin only."

        if not args:
            return (
                "📋 <b>Usage:</b> /activate &lt;user_id&gt; [days]\n"
                "Contoh: /activate 5220170786 9999\n"
                "Default: AKTIF PERMANEN"
            )

        target_id = args[0]
        days = int(args[1]) if len(args) > 1 else 9999

        try:
            from members import ensure_member, upgrade_tier  # type: ignore
            ref = f"VTFX-{target_id}-MANUAL"
            ensure_member(target_id)
            upgrade_tier(target_id, "donor", days, ref)

            # DM the activated user
            user_msg = (
                "🔥 <b>BOOM! Kamu sekarang DONATUR VIP!</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "👑 Status: <b>DONATUR VIP — AKTIF PERMANEN</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "✅ /analyze UNLIMITED\n"
                "✅ EA Auto-Trade\n"
                "✅ Bridge Sinyal\n\n"
                "👉 /help — Lihat command\n"
                "👉 /analyze xauusd — Mulai analisa"
            )
            try:
                await self._tg_send(user_msg, chat_id=target_id)
            except Exception as e:
                LOG.warning("Failed to DM activated user %s: %s", target_id, e)

            return (
                f"✅ <b>Manual Activation Berhasil</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"👤 User: <code>{target_id}</code>\n"
                f"👑 Status: <b>DONATUR VIP — AKTIF PERMANEN</b>"
            )
        except ImportError:
            return "❌ Member system tidak tersedia."
        except Exception as e:
            return f"❌ Activation gagal: {e}"

    # ── Price helper (for /data and others) ───────────────────────────────

    def _fetch_price(self, pair: str) -> float | None:
        """Fetch price via market data layer. Returns None on failure."""
        symbol = resolve_yahoo_symbol(pair)
        if not self._market_data:
            return None
        try:
            quote = self._market_data.get_quote(symbol)  # type: ignore
            if quote and quote.price > 0:
                return quote.price
        except Exception:
            pass
        return None

    # ── Incoming message dispatcher ──────────────────────────────────────

    async def handle_update(self, update: dict[str, Any]) -> str | None:
        """Process an incoming Telegram update (polling mode)."""
        message = update.get("message", {})
        callback_query = update.get("callback_query", {})

        if callback_query:
            return await self._handle_callback(callback_query)

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        if not text:
            return None

        # Parse command
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        handler = self._command_handlers.get(cmd.lstrip("/"))
        if handler:
            response = await handler(args, chat_id=chat_id)
            if response:
                await self._tg_send(response, chat_id=chat_id)
            return response

        # Check donation input state (module-level dict at bottom of this file)
        if chat_id in DONATION_INPUT_STATE:  # noqa: F821
            return await self._handle_donation_input(chat_id, text)

        # Fallback
        fallback = (
            f"❌ Unknown command: <code>{cmd}</code>\n"
            f"Use /start for available commands."
        )
        await self._tg_send(fallback, chat_id=chat_id)
        return fallback

    async def _handle_callback(self, callback_query: dict[str, Any]) -> str | None:
        """Handle inline keyboard callbacks."""
        cb_id = callback_query.get("id", "")
        chat_id = str(callback_query.get("from", {}).get("id", ""))
        data = callback_query.get("data", "")

        await self._tg_answer_callback(cb_id)

        if data.startswith("trade:") or data.startswith("skip:"):
            return self._handle_trade_callback(chat_id, data)

        if data.startswith("pay:") or data.startswith("check:") or data.startswith("donate:"):
            return self._handle_payment_callback(chat_id, data)

        return None

    def _handle_trade_callback(self, chat_id: str, data: str) -> str:
        if chat_id not in self._pending_signals:
            return "⏰ Sinyal kadaluarsa. Kirim /analyze lagi."

        pending = self._pending_signals.pop(chat_id, {})
        sig = pending.get("sig")
        price = pending.get("price", 0)

        if not sig:
            return ""

        if data.startswith("trade:"):
            action = sig.get("action", "HOLD")
            if action == "HOLD":
                return "⚪ Sinyal HOLD — tidak ada trade."
            sig["target_user"] = chat_id
            self.bridge.post_signal(sig, price)
            return f"✅ <b>Sinyal {action} dikirim!</b>\nEA kamu auto-eksekusi dalam 5 detik."
        else:
            return "⏭ Sinyal dilewati. Analisa lagi: /analyze"

    def _handle_payment_callback(self, chat_id: str, data: str) -> str:
        return "💳 Payment gateway: hubungi admin @codergaboets"

    async def _handle_donation_input(self, chat_id: str, text: str) -> str:
        try:
            amount = int(text.replace(".", "").replace(",", ""))
            if amount < 10000:
                return "💰 Minimal Rp10,000. Silakan ketik nominal lain."
            DONATION_INPUT_STATE.pop(chat_id, None)
            return (
                f"💚 <b>Dukungan Rp{amount:,}</b>\n"
                f"Terima kasih! Hubungi admin @codergaboets untuk instruksi pembayaran."
            )
        except ValueError:
            return "❌ Nominal tidak valid. Ketik angka saja (contoh: 50000)."


# ── Donation input state (module-level for easy import) ───────────────────
DONATION_INPUT_STATE: dict[str, bool] = {}
