"""Shared constants and utility functions for the VilonaBot package."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

LOG = logging.getLogger("tradebot.bots.vilona.helpers")

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
    ("gold", "XAUUSD", "GC=F", True),
    ("btc", "BTCUSD", "BTC-USD", False),
    ("oil", "USOIL", "CL=F", True),
]

DONATION_INPUT_STATE: dict[str, bool] = {}


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


def news_blackout_status(
    h: int | None = None, m: int | None = None
) -> tuple[bool, bool, str | None]:
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
        if day not in ev["days"]:
            continue
        if ev["blackout_start"] <= total_min < ev["blackout_end"]:
            return True, False, ev["name"]
        if ev["post_start"] <= total_min < ev["post_end"]:
            return False, True, ev["name"]
    return False, False, None


def resolve_yahoo_symbol(pair: str) -> str:
    return DEFAULT_SYMBOL_MAP.get(pair, pair.upper())


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


def _parse_sse(raw: str) -> str | None:
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
    import re
    # Try ```json ... ``` blocks first
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
    else:
        candidate = content.strip()
    # Find first { and last }
    s = candidate.find("{")
    e = candidate.rfind("}")
    if s >= 0 and e > s:
        candidate = candidate[s : e + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
