"""Shared constants and utility functions for the VilonaBot package.

Ported from legacy scripts/vilona_tradefx_handler.py with full fidelity.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tradebot.bots.vilona.helpers")

WIB = timezone(timedelta(hours=7))

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "gold": "GC=F",
    "xauusd": "GC=F",
    "btc": "BTC-USD",
    "btcusd": "BTC-USD",
    "eth": "ETH-USD",
    "ethusd": "ETH-USD",
    "oil": "CL=F",
    "usoil": "CL=F",
    "eurusd": "EURUSD=X",
    "gbpusd": "GBPUSD=X",
    "usdjpy": "JPY=X",
    "jpyusd": "JPY=X",
    "aapl": "AAPL",
    "tsla": "TSLA",
    "msft": "MSFT",
    "nvda": "NVDA",
    "bbca": "BBCA.JK",
    "bbri": "BBRI.JK",
    "tlkm": "TLKM.JK",
    "asii": "ASII.JK",
    "unvr": "UNVR.JK",
    "bmri": "BMRI.JK",
    "adro": "ADRO.JK",
    "ihsg": "^JKSE",
}

SUPPORTED_PAIRS: list[str] = [
    "gold",
    "btc",
    "eth",
    "oil",
    "eurusd",
    "gbpusd",
    "usdjpy",
    "aapl",
    "tsla",
    "msft",
    "nvda",
    "bbca",
    "bbri",
    "tlkm",
    "asii",
    "unvr",
    "bmri",
    "adro",
    "ihsg",
]

AUTO_SCAN_ASSETS: list[tuple[str, str, str, bool]] = [
    ("gold", "XAUUSD", "GC=F", True),
    ("btc", "BTCUSD", "BTC-USD", False),
    ("oil", "USOIL", "CL=F", True),
]

DONATION_INPUT_STATE: dict[str, bool] = {}

XAUUSD_OFFSET = float(os.environ.get("XAUUSD_PRICE_OFFSET", "74"))

_pip_sizes = {
    "XAUUSD": 0.10,
    "GOLD": 0.10,
    "USOIL": 0.01,
    "BTCUSD": 1.0,
    "ETHUSD": 0.01,
    "EURUSD": 0.00010,
    "GBPUSD": 0.00010,
    "USDJPY": 0.01,
}

BRIDGE_URLS = ["https://phantomfx.aitradepulse.com", "http://localhost:8765"]
MASTER_API_KEY = os.environ.get("BRIDGE_MASTER_KEY", "VT-MASTER-734AD731F5FB")

# Donor / Premium constants
DONOR_DAYS = 9999
MIN_DONATION = 10000
MANUAL_THROTTLE_FREE = 120
MANUAL_THROTTLE_DONOR = 60
SAME_PAIR_COOLDOWN = 90
DONOR_DAILY_QUOTA = 60
FREE_DAILY_QUOTA = 3
DIRECTION_LOCK_SECONDS = 60

FOMO_PHRASES = [
    "🔥 Sinyal ini cuma untuk yang FAST RESPONSE!",
    "⚡ 9 engines udah konsensus — tinggal kamu yang belum action!",
    "💰 Orang lain udah cuan, kamu masih tunggu apa?",
    "🎯 Setiap detik delay = profit yang hilang!",
    "🚀 Ini bukan latihan. Ini real signal.",
    "💎 DIAMOND ALERT — Jangan sampai kelewatan!",
    "⚡ Signal premium detected! Upgrade buat akses FULL analysis!",
    "🔥 90% orang yang subscribe cuan tiap hari. Kamu kapan?",
    "💰 Udah 15 member lain yg eksekusi signal ini. Lo ketinggalan!",
    "🎯 Sinyal akurasi tinggi — cuma buat subscriber PREMIUM.",
]

_last_offset = 0.0
_last_offset_time = 0.0


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


def session(h: int | None = None) -> str:
    h = h if h is not None else wib_now().hour
    if 3 <= h < 7:
        return "Asia 🇯🇵"
    if 7 <= h < 14:
        return "Asia+London 🇯🇵🇬🇧"
    if 14 <= h < 19:
        return "London 🇬🇧"
    if 19 <= h < 22:
        return "London+NY 🇬🇧🇺🇸"
    return "NY 🇺🇸"


def killzone_active(h: int | None = None) -> tuple[bool, bool]:
    h = h if h is not None else wib_now().hour
    return (14 <= h < 17, 19 <= h < 22)


def killzone(h: int | None = None) -> tuple[bool, bool]:
    return killzone_active(h)


def news_blackout_status(
    h: int | None = None, m: int | None = None
) -> tuple[bool, bool, str | None]:
    now = wib_now()
    h = h if h is not None else now.hour
    m = m if m is not None else now.minute
    day = now.weekday()
    total_min = h * 60 + m

    major_events = [
        {
            "name": "High-Impact US Data",
            "blackout_start": 19 * 60 + 0,
            "blackout_end": 19 * 60 + 30,
            "post_start": 19 * 60 + 30,
            "post_end": 19 * 60 + 45,
            "days": [4],
        },
        {
            "name": "NY Open Vol Spike",
            "blackout_start": 19 * 60 + 0,
            "blackout_end": 19 * 60 + 10,
            "post_start": 19 * 60 + 10,
            "post_end": 19 * 60 + 25,
            "days": [0, 1, 2, 3, 4],
        },
    ]

    for ev in major_events:
        if day not in ev["days"]:
            continue
        if ev["blackout_start"] <= total_min < ev["blackout_end"]:
            return True, False, ev["name"]
        if ev["post_start"] <= total_min < ev["post_end"]:
            return False, True, ev["name"]
    return False, False, None


def resolve_yahoo_symbol(pair: str) -> str:
    return DEFAULT_SYMBOL_MAP.get(pair.lower().strip(), pair.upper())


def format_signal_basic(sig: dict[str, Any], price: float, display: str) -> str:
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
        f"💚 Server ini GRATIS — dukung via /subscribe | @berkahkaryaforexbotbot"
    )
    return msg


def extract_json(content: str) -> dict[str, Any] | None:
    import re

    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
    else:
        candidate = content.strip()
    s = candidate.find("{")
    e = candidate.rfind("}")
    if s >= 0 and e > s:
        candidate = candidate[s : e + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _to_pips(price_diff: float, symbol: str) -> float:
    sz = _pip_sizes.get(symbol.upper(), 0.01)
    return price_diff / sz


def get_xauusd_spot_offset() -> float:
    global _last_offset, _last_offset_time
    now = time.time()
    if now - _last_offset_time < 300:
        return _last_offset
    try:
        spot = fetch_xauusd_spot()
        if not spot:
            return _last_offset
        from market_data import UnifiedMarketData

        md = UnifiedMarketData()
        quote = md.get_quote("GC=F")
        if quote and quote.price > 1000:
            _last_offset = spot - quote.price
            _last_offset_time = now
            return _last_offset
    except Exception:
        pass
    return _last_offset


def fetch_xauusd_spot() -> float | None:
    try:
        req = urllib.request.Request(
            "https://api.gold-api.com/price/XAU", headers={"User-Agent": "Vilona/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        price = float(data.get("price", 0))
        if 2000 < price < 6000:
            return price
    except Exception as e:
        LOG.debug("Gold-API failed: %s", e)
    return None


def _normalize_broker_symbol(s: str) -> str:
    s = s.upper().strip()
    for suffix in [".EX", ".EXNESS", "_EX", "_EXNESS"]:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def fetch_price(pair: str = "gold") -> float | None:
    pair_norm = _normalize_broker_symbol(pair)
    if pair_norm in ("GOLD", "XAUUSD"):
        spot = fetch_xauusd_spot()
        if spot:
            return round(spot + XAUUSD_OFFSET, 2)
    try:
        from market_data import UnifiedMarketData

        md = UnifiedMarketData()
        sym = resolve_yahoo_symbol(pair)
        quote = md.get_quote(sym)
        if quote:
            return quote.price
    except Exception as e:
        LOG.debug("fetch_price(%s) failed: %s", pair, e)
    return None


def _send_document(chat_id: str, file_path: str | Path, filename: str, caption: str = "") -> None:
    token = os.environ.get("VILONA_TRADEFX_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    if not token:
        LOG.warning("Cannot send document: VILONA_TRADEFX_TOKEN not set")
        return
    boundary = f"----VilonaBoundary{int(time.time())}"
    with open(file_path, "rb") as f:
        file_data = f.read()

    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=30)
        LOG.info("📎 Document sent: %s to chat_id=%s", filename, chat_id)
    except Exception as e:
        LOG.error("Failed to send document %s: %s", filename, e)


def _format_news_context(news: str | list | dict) -> str:
    if not news:
        return ""
    if isinstance(news, list):
        return "\n".join(f"• {x}" for x in news[:3])
    if isinstance(news, dict):
        items = news.get("items", news.get("articles", []))
        if items:
            return "\n".join(f"• {x.get('title', '')}" for x in items[:3])
        return str(news)
    return str(news)


def _compute_levels(ohlcv_bars: list, price: float) -> str:
    if not ohlcv_bars or len(ohlcv_bars) < 10 or not price:
        return ""
    try:
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
        return "\n".join(parts)
    except Exception:
        return ""


def _sig_quality_pass(
    sig: dict, quant_result: dict | None = None, display: str = "XAUUSD"
) -> tuple[bool, str]:
    action = sig.get("action", "HOLD")
    if action == "HOLD":
        return False, "Market sideways — no clear direction"

    conf = sig.get("confidence", 0)
    if isinstance(conf, (int, float)) and conf > 10:
        conf = conf / 100

    if conf < 0.65:
        return False, f"Confidence {conf:.0%} < 65%"

    voters = sig.get("voters", sig.get("ensemble", 0))
    if isinstance(voters, str) and "/" in voters:
        voters = int(voters.split("/")[0])
    voters = int(voters) if voters else 0
    if voters < 2:
        return False, f"Only {voters} model agreed (min 2)"

    rr = sig.get("rr_ratio", 0)
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = float(rr[2:]) if rr[2:] else 0
    rr = float(rr) if rr else 0
    if rr > 0 and rr < 1.5:
        return False, f"RR 1:{rr:.1f} too poor"

    if quant_result:
        qv = quant_result.get("quant_verdict", "")
        if qv == "GREEN" and action == "SELL":
            return False, "Quant says BUY but signal SELL — conflict"
        if qv == "RED" and action == "BUY":
            return False, "Quant says SELL but signal BUY — conflict"

    is_crypto = display.upper() in ("BTCUSD", "ETHUSD", "BTC", "ETH")
    if not is_crypto:
        london_kz, ny_kz = killzone_active(wib_now().hour)
        if not london_kz and not ny_kz:
            return True, "Asia session — lower volatility"

    return True, "Quality Gate PASS"


def _clamp_sltp(sig: dict, display: str = "XAUUSD") -> dict:
    action = sig.get("action", "").upper()
    if action not in ("BUY", "SELL"):
        return sig

    entry = sig.get("entry", 0)
    sl = sig.get("sl", 0)
    if not entry or not sl:
        return sig

    pip_size = _pip_sizes.get(display.upper(), 0.01)
    sl_dist_pts = abs(entry - sl)
    sl_pips = sl_dist_pts / pip_size
    LOG.info(
        "_clamp_sltp [%s]: %s entry=%s sl=%s sl_pips=%.0f", display, action, entry, sl, sl_pips
    )

    MIN_SL = 20
    MAX_SL = 35
    MAX_TP = 100

    clamped = False
    if sl_pips < MIN_SL:
        sl_dist_pts = MIN_SL * pip_size
        clamped = True
    elif sl_pips > MAX_SL:
        sl_dist_pts = MAX_SL * pip_size
        clamped = True

    direction_wrong = (action == "BUY" and sig["sl"] > entry) or (
        action == "SELL" and sig["sl"] < entry
    )
    if clamped or direction_wrong:
        if action == "BUY":
            sig["sl"] = round(entry - sl_dist_pts, 2)
        else:
            sig["sl"] = round(entry + sl_dist_pts, 2)
        sl_pips = sl_dist_pts / pip_size
        if direction_wrong:
            LOG.info(
                "_clamp_sltp [%s]: FIXED wrong SL direction — %s SL now %s entry",
                display,
                action,
                "below" if action == "BUY" else "above",
            )

    rr = sig.get("rr_ratio", 0)
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = float(rr[2:])
    rr = float(rr) if rr else 2.0
    rr = max(1.5, min(rr, 3.0))

    tp_dist = sl_pips * rr * pip_size
    if tp_dist / pip_size > MAX_TP:
        tp_dist = MAX_TP * pip_size

    if action == "BUY":
        sig["tp"] = round(entry + tp_dist, 2)
    else:
        sig["tp"] = round(entry - tp_dist, 2)

    return sig


def apply_elite_params(sig: dict, params: dict, price: float, display: str = "XAUUSD") -> dict:
    sig = dict(sig)
    if "risk" in params:
        sig["risk_percent"] = params["risk"]
        mult = params["risk"] / 1.0
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


def format_bridge_status() -> str:
    try:
        from tradebot.monitoring.watchdog import format_bridge_status as _fbs

        return _fbs()
    except Exception:
        return "🔴 Bridge Status: Unknown (service failed to load)"


def post_signal_to_bridge(sig: dict, price: float, display: str = "XAUUSD") -> None:
    symbol = sig.get("symbol", sig.get("display", display))
    entry = sig.get("entry", price) or price
    sl = sig.get("sl", 0)
    tp = sig.get("tp", 0)
    confidence = sig.get("confidence", 0)
    rr = sig.get("rr_ratio", 0)
    action = sig.get("action", "HOLD")

    if action in ("BUY", "SELL"):
        if isinstance(confidence, (int, float)) and confidence < 0.65:
            LOG.info("⛔ Signal rejected: confidence %.0f%% < 65%%", confidence * 100)
            return
        if isinstance(rr, (int, float)) and rr > 0 and rr < 1.5:
            LOG.info("⛔ Signal rejected: RR 1:%.1f < 1:1.5", rr)
            return
        if (action == "BUY" and sl >= entry) or (action == "SELL" and sl <= entry):
            LOG.info("⛔ Signal rejected: SL on wrong side (entry=%s, sl=%s)", entry, sl)
            return

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
        "telegram_message_id": sig.get("telegram_message_id"),
        "timestamp": wib_now().isoformat(),
        "rr_ratio": rr,
    }

    try:
        ea_file = DATA_DIR / "ea_signal.json"
        ea_file.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        LOG.error("Failed to write ea_signal.json: %s", e)

    data = json.dumps(payload).encode()

    try:
        from tradebot.services.trade_tracker_service import open_trade

        open_trade(
            sig,
            sig.get("entry", price),
            symbol,
            sig.get("source", "ai"),
            str(sig.get("target_user", "")),
            telegram_message_id=sig.get("telegram_message_id"),
        )
    except Exception as e:
        LOG.debug("Failed to track trade: %s", e)

    posted = False
    for url in BRIDGE_URLS:
        try:
            req = urllib.request.Request(
                f"{url}/signal",
                data=data,
                headers={"Content-Type": "application/json", "X-API-Key": MASTER_API_KEY},
            )
            urllib.request.urlopen(req, timeout=5)
            posted = True
            break
        except Exception:
            continue
    if not posted:
        LOG.warning("Failed to post signal to any bridge URL")


def fmt_pulse(pulse_data: dict) -> str:
    """Format full market pulse diagnostic page."""
    if not pulse_data:
        return "⚠️ Data Market Pulse tidak tersedia."

    symbol = pulse_data.get("symbol", "GC=F")
    price = pulse_data.get("price", 0)
    verdict = pulse_data.get("verdict", "HOLD")
    consensus = pulse_data.get("consensus", {})
    details = pulse_data.get("details", {})

    lines = [
        "🔄 <b>MARKET PULSE DIAGNOSTIC</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🥇 Asset: <b>{symbol}</b> | Price: ${price:.2f}",
        f"🕐 Time: {wib_fmt()} | Verdict: <b>{verdict}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📊 <b>Engine Voter Summary:</b>",
    ]

    for engine, decision in consensus.items():
        engine_clean = engine.replace("_", " ").title()
        lines.append(f"• {engine_clean}: <code>{decision}</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ Upgrade Tier → /subscribe")
    lines.append("   Makin banyak AI = makin akurat sinyal = makin cuan")

    return "\n".join(lines)


def fmt_signal(
    sig: dict,
    price: float,
    dxy: float | None,
    h: int,
    display: str = "XAUUSD",
    currency: str = "$",
    quality: tuple | None = None,
    levels: str = "",
) -> str:
    action = sig.get("action", "HOLD")
    emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️"}.get(action, "⚪️")
    grade = sig.get("grade", "D")
    conf = sig.get("confidence", 0)
    if isinstance(conf, (int, float)) and conf > 10:
        conf = conf / 100
    rr = sig.get("rr_ratio", "?")
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = rr[2:]
    entry = sig.get("entry") or price or 0

    zone_half = entry * 0.0005 if entry > 0 else 0
    zone_lo = entry - zone_half if zone_half else entry
    zone_hi = entry + zone_half if zone_half else entry
    sl = sig.get("sl") or 0
    tp = sig.get("tp") or 0
    tp1 = sig.get("tp1", 0)
    tp2 = sig.get("tp2", 0)
    tp3 = sig.get("tp3", 0)
    tp4 = sig.get("tp4", 0)
    models_display = sig.get("_models", "")
    voters = sig.get("voters", sig.get("ensemble", "?"))
    now_wib = wib_now()

    q_passed, q_reason = quality if quality else (None, None)
    if q_passed is None:
        q_passed = conf >= 0.65 and action != "HOLD"
        q_reason = "Quality Gate PASS" if q_passed else "Low confidence — info only"

    is_actionable = q_passed and action in ("BUY", "SELL")

    forex_metal = display.upper() in ("XAUUSD", "GOLD", "USOIL", "EURUSD", "GBPUSD", "USDJPY")
    in_kz = True
    if forex_metal:
        lkz, nykz = killzone_active(h)
        in_kz = lkz or nykz
        if not in_kz:
            is_actionable = False
            q_reason = f"🔴 NO TRADE ZONE — {display} only during London (14:00-17:00 WIB) & NY (19:00-22:00 WIB)"

    header_emoji = emoji if is_actionable else "⚪️"
    header_label = f"SINYAL {action}" if is_actionable else "MARKET PULSE"

    if action in ("BUY", "SELL") and entry and sl and price:
        sl_dist = abs(sl - entry)
        min_sl_map = {"XAUUSD": 3.0, "GOLD": 3.0, "USOIL": 0.15, "BTCUSD": 600, "ETHUSD": 50}
        min_sl = min_sl_map.get(display.upper(), 0)
        if min_sl > 0 and 0 < sl_dist < min_sl:
            sl = 0
            tp = 0

    if action in ("BUY", "SELL") and entry and sl and zone_lo and zone_hi:
        zone_sl_map = {"XAUUSD": 2.0, "GOLD": 2.0}
        min_zone_sl = zone_sl_map.get(display.upper(), 0)
        max_sl_pts = 3.5
        if min_zone_sl > 0:
            if action == "BUY":
                zone_sl_dist = zone_lo - sl
                if zone_sl_dist < min_zone_sl:
                    new_sl = round(zone_lo - min_zone_sl, 2)
                    if abs(entry - new_sl) <= max_sl_pts:
                        sl = new_sl
            else:
                zone_sl_dist = sl - zone_hi
                if zone_sl_dist < min_zone_sl:
                    new_sl = round(zone_hi + min_zone_sl, 2)
                    if abs(entry - new_sl) <= max_sl_pts:
                        sl = new_sl

    if (sl == 0 or tp == 0) and price and price > 0:
        d_upper = display.upper()
        if d_upper in ("XAUUSD", "GOLD"):
            sl = round(price - 3.0, 2) if action == "BUY" else round(price + 3.0, 2)
            tp = round(price + 5.0, 2) if action == "BUY" else round(price - 5.0, 2)
        elif d_upper == "USOIL":
            sl = round(price - 0.25, 2) if action == "BUY" else round(price + 0.25, 2)
            tp = round(price + 0.50, 2) if action == "BUY" else round(price - 0.50, 2)
        elif d_upper in ("EURUSD", "GBPUSD", "USDJPY"):
            sl = round(price - 0.0015, 5) if action == "BUY" else round(price + 0.0015, 5)
            tp = round(price + 0.0030, 5) if action == "BUY" else round(price - 0.0030, 5)
        elif d_upper == "BTCUSD":
            sl = round(price - 600, 2) if action == "BUY" else round(price + 600, 2)
            tp = round(price + 1200, 2) if action == "BUY" else round(price - 1200, 2)
        elif d_upper == "ETHUSD":
            sl = round(price - 50, 2) if action == "BUY" else round(price + 50, 2)
            tp = round(price + 75, 2) if action == "BUY" else round(price - 75, 2)
        elif d_upper in ("BBCA", "BBRI", "TLKM", "ASII", "UNVR", "BMRI", "ADRO", "IHSG"):
            sl_pct = 0.01
            tp_pct = 0.02 if d_upper == "BBCA" else 0.015
            sl = (
                round(price * (1 - sl_pct), 0)
                if action == "BUY"
                else round(price * (1 + sl_pct), 0)
            )
            tp = (
                round(price * (1 + tp_pct), 0)
                if action == "BUY"
                else round(price * (1 - tp_pct), 0)
            )
        else:
            sl = round(price - 0.50, 2) if action == "BUY" else round(price + 0.50, 2)
            tp = round(price + 0.75, 2) if action == "BUY" else round(price - 0.75, 2)

    if action in ("BUY", "SELL") and display.upper() in ("XAUUSD", "GOLD") and entry > 0:
        offset = get_xauusd_spot_offset()
        if abs(offset) > 5:
            entry = round(entry + offset, 2)
            if sl:
                sl = round(sl + offset, 2)
            if tp:
                tp = round(tp + offset, 2)
            if tp1:
                tp1 = round(tp1 + offset, 2)
            if tp2:
                tp2 = round(tp2 + offset, 2)
            if tp3:
                tp3 = round(tp3 + offset, 2)
            if tp4:
                tp4 = round(tp4 + offset, 2)
            zone_half = entry * 0.0005
            zone_lo = entry - zone_half
            zone_hi = entry + zone_half

    tp1 = tp2 = tp3 = tp4 = 0
    if tp > 0 and entry > 0:
        tp_dist = abs(tp - entry)
        d_upper = display.upper()
        if d_upper in ("XAUUSD", "GOLD"):
            min_tp1 = 3.0
            lvl2_min = 4.5
            lvl3_min = 7.0
            lvl4_min = 10.0
        elif d_upper == "USOIL":
            min_tp1 = 0.30
            lvl2_min = 0.45
            lvl3_min = 0.70
            lvl4_min = 1.0
        elif d_upper in ("BTCUSD", "ETHUSD"):
            min_tp1 = 600
            lvl2_min = 900
            lvl3_min = 1400
            lvl4_min = 2000
        else:
            min_tp1 = 0.0015
            lvl2_min = 0.0030
            lvl3_min = 0.0050
            lvl4_min = 0.0080

        num_tp = 1
        if tp_dist >= lvl4_min:
            num_tp = 4
        elif tp_dist >= lvl3_min:
            num_tp = 3
        elif tp_dist >= lvl2_min:
            num_tp = 2

        min_spread = min_tp1 * 0.5

        if action == "BUY":
            tp1 = round(entry + min_tp1, 2)
            if num_tp >= 2:
                tp2 = max(round(entry + tp_dist * 0.50, 2), tp1 + min_spread)
                tp2 = min(tp2, tp)
            if num_tp >= 3:
                tp3 = max(round(entry + tp_dist * 0.75, 2), tp2 + min_spread)
                tp3 = min(tp3, tp)
            if num_tp >= 4:
                tp4 = tp
        else:
            tp1 = round(entry - min_tp1, 2)
            if num_tp >= 2:
                tp2 = min(round(entry - tp_dist * 0.50, 2), tp1 - min_spread)
                tp2 = max(tp2, tp)
            if num_tp >= 3:
                tp3 = min(round(entry - tp_dist * 0.75, 2), tp2 - min_spread)
                tp3 = max(tp3, tp)
            if num_tp >= 4:
                tp4 = tp

        if tp1 and entry and sl and abs(entry - sl) > 0:
            tp1_rr = abs(tp1 - entry) / abs(entry - sl)
            sig["rr_ratio"] = f"1:{tp1_rr:.1f}"
            rr = f"{tp1_rr:.1f}"

    wr_text = ""
    try:
        from tradebot.services.trade_tracker_service import get_stats

        stats = get_stats()
        total_t = stats.get("total", 0)
        wins_t = stats.get("wins", 0)
        losses_t = stats.get("losses", 0)
        wr_t = stats.get("win_rate", 0)
        if total_t > 0:
            wr_text = f"📊 Winrate: {total_t} sinyal | {wr_t}% ({wins_t}W/{losses_t}L)"
    except Exception:
        pass

    def _pips(dist: float, asset: str = display) -> str:
        a = asset.upper()
        if a in ("XAUUSD", "GOLD"):
            return f"{dist / 0.10:.0f} pip"
        elif a == "USOIL":
            return f"{dist / 0.01:.0f} pip"
        elif a in ("EURUSD", "GBPUSD", "USDJPY"):
            return f"{dist / 0.00010:.1f} pip"
        elif a == "BTCUSD" or a == "ETHUSD":
            return f"{dist:.0f} pip"
        return f"{dist:.0f} pip"

    def _tp_pips(tp_val: float) -> str:
        if entry and tp_val:
            return f"(+{_pips(abs(tp_val - entry))})"
        return ""

    def _sl_pips(sl_val: float) -> str:
        if entry and sl_val:
            return f"(-{_pips(abs(sl_val - entry))})"
        return ""

    ai_parts = []
    if models_display:
        v_str = f"({voters} model)" if voters and voters != "?" else ""
        ai_parts.append(f"🤖 {models_display} {v_str}".strip())
    if grade and grade != "D":
        ai_parts.append(f"Grade {grade}")
    ai_line = " | ".join(ai_parts) if ai_parts else ""

    is_idx = display.upper() in ("BBCA", "BBRI", "IHSG")

    def _fmt(v: float) -> str:
        return f"Rp{v:,.0f}" if is_idx else f"{currency}{v:.2f}"

    def _fmt_zone(lo: float, hi: float) -> str:
        return (
            f"Rp{lo:,.0f} — Rp{hi:,.0f}" if is_idx else f"{currency}{lo:.2f} — {currency}{hi:.2f}"
        )

    zone_label = (
        "🟢 BUY ZONE"
        if action == "BUY"
        else ("🔴 SELL ZONE" if action == "SELL" else "📍 Entry Zone")
    )
    is_free = sig.get("_tier_capped", True)

    lines = [
        f"{header_emoji} <b>{header_label} — {display.upper()}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {now_wib.strftime('%Y.%m.%d %H:%M')} WIB | Session: {session(h)}",
    ]

    if forex_metal and not in_kz:
        lines.append("")
        lines.append("🔴 <b>NO TRADE ZONE</b>")
        lines.append(
            f"💡 {display.upper()} hanya trading saat London (14:00-17:00 WIB) & NY (19:00-22:00 WIB)"
        )
        lines.append("   Sabar bro... sinyal muncul pas killzone aktif.")
    else:
        lines.append(f"📍 {zone_label}: {_fmt_zone(zone_lo, zone_hi)}")
        if is_free and is_actionable:
            lines.append("🔴 SL: 🔒 <b>[DONOR ONLY]</b>")
            for tp_val, tp_label in [(tp1, "TP1"), (tp2, "TP2"), (tp3, "TP3"), (tp4, "TP4")]:
                if tp_val and tp_val > 0:
                    lines.append(f"🟢 {tp_label}: 🔒 <b>[DONOR ONLY]</b>")
            lines.append("")
            lines.append("💡 <b>Free tier cuma bisa liat Entry Zone.</b>")
            lines.append("   SL/TP dikunci — gak bisa eksekusi dengan aman.")
            lines.append("   👑 <b>/subscribe</b> — Unlock SL/TP + 2 AI + Grok News")
        else:
            lines.append(f"🔴 SL: {_fmt(sl)} {_sl_pips(sl)}")
            for tp_val, tp_label in [(tp1, "TP1"), (tp2, "TP2"), (tp3, "TP3"), (tp4, "TP4")]:
                if tp_val and tp_val > 0:
                    lines.append(f"🟢 {tp_label}: {_fmt(tp_val)} {_tp_pips(tp_val)}")

    if levels and is_actionable:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(levels)

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    if ai_line:
        lines.append(ai_line)
    lines.append(f"📐 RR 1:{rr} | Confidence: {conf:.0%}")
    if wr_text:
        lines.append(wr_text)

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    if is_actionable:
        lines.append(f"✅ {q_reason}")
    else:
        lines.append("⚠️ <b>MARKET PULSE — Info Only</b>")
        lines.append(f"💡 {q_reason}")
        lines.append("🔍 Gunakan sebagai konfirmasi SnR/FIBO manual.")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    try:
        from scripts.smc_section import format_smc_analysis

        smc_text = format_smc_analysis(display)
    except Exception:
        smc_text = ""

    if smc_text and is_actionable:
        lines.append(smc_text)

    lines.append(
        "⚠️ <i>NFA — Not Financial Advice. Sinyal hasil deteksi otomatis AI untuk edukasi. Keputusan & risiko trading sepenuhnya ada padamu. Selalu pakai manajemen risiko.</i>"
    )
    if is_actionable:
        lines.append("")
        lines.append("💡 Mau validasi SnR + FIBO + SL placement?")
        lines.append(f"   👉 DM <b>@berkahkaryaforexbotbot</b> — ketik /levels {display.lower()}")
        lines.append("   🔒 Premium feature — <b>/subscribe</b> dulu kalo belum unlock")

    token_total = sig.get("_token_total", 0)
    model_names = sig.get("_model", "AI")
    model_count = sig.get("voters", 1) or 1
    grok_news = sig.get("_grok_news")
    tier_label = sig.get("_tier", "🆓 Free")

    lines.append("")

    if not is_actionable:
        lines.append("━━━━━━━━━━━━━━━━")
        if forex_metal and not in_kz:
            lines.append("⏰ Next: London buka 14:00 WIB | NY buka 19:00 WIB")
        lines.append("⚡ /subscribe — Unlock full AI signal + SL/TP + Multi-AI")
    elif token_total > 0:
        has_grok = bool(grok_news)
        battery_pct = min(100, model_count * 33 + (33 if has_grok else 0))
        bar_count = min(3, model_count + (1 if has_grok else 0))
        bars = "■" * max(1, bar_count) + "□" * (3 - max(1, bar_count))

        if is_free:
            lines.append(
                f"🔋 <b>AI Power: {bars} {battery_pct}%</b> — {model_count}/3 AI yang kerja buat lu"
            )
            lines.append("")
            lines.append(f"🤖 Cuma <b>{model_names}</b> doang yang mikir.")
            lines.append("   AI lu kelaparan bro... cuma dikasih 1 model 😤")
            lines.append("   Bayangin kalo 3 AI + Grok News analisa bareng:")
            lines.append("   → Entry lebih presisi, SL lebih ketat, TP lebih akurat")
            lines.append("")
            lines.append("📰 <b>Grok News</b> [🔒 LOCKED]")
            lines.append("   🔍 <i>Preview: Market-moving headlines dari X/Twitter...</i>")
            lines.append("   🗞️  Breaking news, FOMC, NFP, CPI, geopolitics — all real-time")
            lines.append(f"   🔓 <b>Unlock → /news {display.lower()}</b> atau /subscribe")
            lines.append("")
            lines.append("⚡ <b>Rp 50K/bln (PRO)</b> — lebih murah dari 1x loss SL")
            lines.append("   Dapet 2 AI + Grok News + /levels + /news")
            lines.append("   <b>/subscribe</b> sekarang — jangan biarin AI lu kerja sendirian")
        else:
            lines.append(f"🔋 <b>AI Power: {bars} {battery_pct}%</b> — full throttle")
            lines.append("")
            lines.append(f"🤖 <b>{model_count} AI Partner</b> kerja bareng: {model_names}")
            if grok_news:
                news_str = _format_news_context(grok_news)
                if news_str:
                    lines.append("📰 <b>Grok News Active</b> ✅ — real-time X/Twitter intel")
                    lines.append(f"   💡 Detail: /news {display.lower()}")
            else:
                lines.append(
                    f"📰 Grok News [🔒 LOCKED] — <b>/news {display.lower()}</b> buat unlock"
                )
            lines.append("")
            lines.append("🤝 <b>AI Partner lu makin cerdas.</b>")
            lines.append("   Makin banyak AI = makin akurat sinyal = makin cuan.")
            lines.append("   Jangan stop disini — upgrade ke tier tertinggi:")
            if tier_label in ("⭐ Pro",):
                lines.append("   👑 <b>/subscribe</b> → Elite Tier: 3 AI + Grok News real-time")
            else:
                lines.append("   💎 <b>Elite Intelligence Active</b> — your edge is real")
    else:
        lines.append("⚡ Upgrade Tier → /subscribe")
        lines.append("   Makin banyak AI = makin akurat sinyal = makin cuan")

    return "\n".join(lines)
