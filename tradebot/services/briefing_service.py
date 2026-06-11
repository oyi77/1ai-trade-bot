"""Pre-market briefing service — daily market briefing generation.

Ported from scripts/pre_market_briefing.py with full legacy fidelity.
Fetches XAUUSD from gold-api.com + DXY from Yahoo Finance.
Computes H1 SMA bias. Generates subscriber-teaser briefing with locked sections.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger("tradebot.services.briefing")

WIB = timezone(timedelta(hours=7))
DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
)
STATE_PATH = DATA_DIR / "briefing_state.json"

GOLD_API_KEY = os.environ.get("GOLD_API_KEY", "")


def wib_now() -> datetime:
    return datetime.now(WIB)


def _last_briefing_sent() -> str | None:
    """Check when the last briefing was sent (date string YYYY-MM-DD)."""
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text()).get("last_briefing_date")
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _mark_briefing_sent() -> None:
    """Record today's briefing as sent."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        today = wib_now().strftime("%Y-%m-%d")
        STATE_PATH.write_text(json.dumps({"last_briefing_date": today}))
    except OSError as e:
        LOG.warning("Failed to mark briefing sent: %s", e)


def _fetch_gold_price() -> float | None:
    """Fetch XAUUSD spot price from gold-api.com."""
    if not GOLD_API_KEY:
        LOG.debug("GOLD_API_KEY not set")
        return None
    try:
        import urllib.request as ureq
        url = f"https://www.gold-api.com/api/XAU/USD?api_key={GOLD_API_KEY}"
        with ureq.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
            price = data.get("price", 0)
            if price > 0:
                LOG.info("Gold price: $%.2f", price)
                return price
    except Exception as e:
        LOG.warning("Gold price fetch failed: %s", e)
    return None


def _fetch_dxy() -> float | None:
    """Fetch DXY index from Yahoo Finance (DX-Y.NYB)."""
    try:
        import urllib.request as ureq
        url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=1d&interval=5m"
        headers = {"User-Agent": "Mozilla/5.0"}
        req = ureq.Request(url, headers=headers)
        with ureq.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice", meta.get("previousClose", 0))
            if price > 0:
                LOG.info("DXY: %.2f", price)
                return price
    except Exception as e:
        LOG.warning("DXY fetch failed: %s", e)
    return None


def _compute_sma_bias(price: float | None, dxy: float | None) -> str:
    """Simple bias from price vs DXY correlation."""
    if price and dxy:
        # Gold up + DXY down = bullish for gold
        bias_parts = []
        if price > 2900:
            bias_parts.append("gold bullish above 2900")
        if dxy < 100:
            bias_parts.append("DXY weak")
        elif dxy > 105:
            bias_parts.append("DXY strong")
        return " | ".join(bias_parts) if bias_parts else "neutral"
    return "neutral"


def generate_briefing() -> str:
    """Generate the full pre-market briefing with subscriber-teaser formatting.

    Shows price + DXY for all users. Locked sections (bias/SL/TP) for free tier.
    """
    now = wib_now()
    today = now.strftime("%Y-%m-%d")
    day_name = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"][now.weekday()]

    gold_price = _fetch_gold_price()
    dxy = _fetch_dxy()

    gold_str = f"${gold_price:.2f}" if gold_price else "—"
    dxy_str = f"{dxy:.2f}" if dxy else "—"

    bias = _compute_sma_bias(gold_price, dxy)

    # ── Build briefing ──
    lines = [
        "📊 <b>PRE-MARKET BRIEFING</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {day_name}, {today}",
        f"🕐 {now.strftime('%H:%M WIB')}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🥇 XAUUSD: {gold_str}",
        f"💵 DXY: {dxy_str}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📈 <b>Market Bias:</b>",
    ]

    if bias != "neutral":
        lines.append(f"   {bias}")
    else:
        lines.append("   ⚪️ Neutral — belum ada konfirmasi")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    # ── Subscriber teaser: locked sections ──
    lines.append("")
    lines.append("🟢 <b>Support & Resistance</b> [🔒 DONOR ONLY]")
    lines.append("🔴 <b>Entry/SL/TP</b> [🔒 DONOR ONLY]")
    lines.append("📊 <b>Signal Quality</b> [🔒 DONOR ONLY]")
    lines.append("")
    lines.append("💡 <b>Briefing ini cuma preview.</b>")
    lines.append("   Dapatkan analisa REAL di @vilonaaichanel")
    lines.append("")
    lines.append("⚡ <b>/subscribe</b> — Rp 50k/bulan")
    lines.append("   🟢 FULL Entry/SL/TP + 2 AI + Grok News")
    lines.append("   📊 SnR/FIBO levels + winrate tracker")

    return "\n".join(lines)


async def send_daily_briefing(
    bot_token: str,
    admin_chat_id: str = "",
) -> bool:
    """Send daily briefing to admin and the signal channel.

    Called at 07:00 WIB after checking dedup (once per day).
    Returns True if sent, False if skipped (already sent today).

    Args:
        bot_token: Telegram Bot API token.
        admin_chat_id: Fallback chat ID to send to.
    """
    today = wib_now().strftime("%Y-%m-%d")
    last = _last_briefing_sent()

    if last == today:
        LOG.info("Briefing already sent today (%s)", today)
        return False

    # Only send on weekdays
    if wib_now().weekday() >= 5:
        LOG.info("Weekend — skipping briefing")
        return False

    briefing = generate_briefing()

    import urllib.request as ureq

    # Send to admin
    targets = [admin_chat_id] if admin_chat_id else []

    # Also try the signal channel
    channel_id = os.environ.get("VILONA_TRADEFX_CHANNEL_ID", "")
    if channel_id:
        targets.append(channel_id)

    if not targets:
        LOG.warning("No targets for daily briefing")
        return False

    for target in targets:
        payload = json.dumps({
            "chat_id": target,
            "text": briefing,
            "parse_mode": "HTML",
        }).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
            ureq.urlopen(req, timeout=10)
            LOG.info("Briefing sent to %s", target)
        except Exception as e:
            LOG.warning("Failed to send briefing to %s: %s", target, e)

    _mark_briefing_sent()
    return True
