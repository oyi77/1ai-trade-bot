#!/usr/bin/env python3
"""daily_mapping.py — Daily Gold Market Mapping + auto-post to Telegram.

Sends structured mapping message to the Vilona trading channel.
If --send flag: also posts via tg_send to SIGNAL_CHANNEL_ID.
"""
import json, logging, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily-mapping")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))


def fetch_gold_price() -> dict:
    """Fetch XAUUSD spot from gold-api.com."""
    req = urllib.request.Request(
        "https://api.gold-api.com/price/XAU",
        headers={"User-Agent": "VilonaMapping/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def wib_fmt(ts: float | None = None) -> str:
    ts = ts if ts else time.time()
    return datetime.fromtimestamp(ts, WIB).strftime("%Y.%m.%d %H:%M")


def calculate_daily_range(cache_file: Path) -> tuple[float, float, float, float, float, float, float]:
    """Return (open, now, change_pct, high, low) for today."""
    now_data = fetch_gold_price()
    now_price = now_data.get("price", 0)

    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        prev_close = cached.get("prev_close", now_price)
        daily_high = cached.get("daily_high", now_price)
        daily_low = cached.get("daily_low", now_price)
    else:
        prev_close = now_price
        daily_high = now_price
        daily_low = now_price

    daily_high = max(daily_high, now_price)
    daily_low = min(daily_low, now_price)
    change_pct = ((now_price - prev_close) / prev_close * 100) if prev_close else 0

    # Save daily state
    cache = {
        "prev_close": prev_close,
        "daily_high": daily_high,
        "daily_low": daily_low,
        "last_update": time.time(),
        "last_price": now_price,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache))

    high_pips = (daily_high - prev_close) / 0.10
    low_pips = (prev_close - daily_low) / 0.10
    return prev_close, now_price, change_pct, daily_high, daily_low, high_pips, low_pips


def killzone_label() -> str:
    now = datetime.now(WIB)
    h = now.hour
    if 14 <= h < 17:
        return "🇬🇧 LONDON KILLZONE"
    elif 19 <= h < 22:
        return "🇺🇸 NEW YORK KILLZONE"
    else:
        return "⏸️ Outside Killzone"


def build() -> str:
    cache_file = PROJECT_DIR / "data" / "vilona_tradefx" / "daily_state.json"
    data = fetch_gold_price()
    prev_close, now_price, change_pct, daily_high, daily_low, high_pips, low_pips = \
        calculate_daily_range(cache_file)

    bid = data.get("price", 0)
    change_24h = data.get("change_percent", 0)
    direction = "📈" if change_pct >= 0 else "📉"

    lines = [
        "📊 <b>DAILY GOLD MAPPING</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {wib_fmt()} WIB | {killzone_label()}",
        f"",
        f"💰 <b>XAUUSD: ${bid:,.2f}</b>",
        f"{direction} Hari ini: {change_pct:+.2f}%",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📐 <b>Daily Range:</b>",
        f"   High: ${daily_high:,.2f} (+{high_pips:.0f} pip)",
        f"   Low:  ${daily_low:,.2f} (-{low_pips:.0f} pip)",
        f"",
        f"🎯 <b>Key Levels:</b>",
        f"   Resistance: ${daily_high:,.2f}",
        f"   Support:    ${daily_low:,.2f}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"⚡ /subscribe — Dapetin sinyal real-time",
        f"",
        f"<i>Auto-generated daily mapping.</i>",
    ]

    # Yesterday close
    if prev_close and prev_close != now_price:
        yday_change = (now_price - prev_close) / prev_close * 100
        lines.insert(6, f"📌 Yesterday close: ${prev_close:,.2f} | Change: {yday_change:+.2f}%")

    return "\n".join(lines)


if __name__ == "__main__":
    text = build()
    send = "--send" in sys.argv

    if send:
        from vilona_tradefx_handler import tg_send, SIGNAL_CHANNEL_ID
        tg_send(text, SIGNAL_CHANNEL_ID)
        log.info(f"📤 Daily mapping sent to channel")
    else:
        print(text)
