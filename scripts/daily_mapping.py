#!/usr/bin/env python3
"""Daily Market Mapping — sent at 10:00 WIB. One analysis for the whole day.
Features:
- Fetches live market prices + DXY
- Calculates XAUUSD H4 swing zones (support/resistance)
- Monday Sentiment logic (BULLISH/BEARISH based on DXY)
- State tracker anti-spam: sends only once per day
"""
import os, sys, json, time, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mapping")

WIB = timezone(timedelta(hours=7))

# ── State Tracker (anti-spam) ──
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
STATE_FILE = DATA_DIR / ".last_mapping_sent"

def _mapping_already_sent(today_str: str) -> bool:
    """Check if mapping was already sent today."""
    try:
        return STATE_FILE.read_text().strip() == today_str
    except Exception:
        return False

def _mark_mapping_sent(today_str: str):
    """Persist today's date so we don't re-send."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(today_str)

# ── Symbol info ──
SYMBOLS = {
    "XAUUSD": {"name": "Gold / XAUUSD", "dxy": True, "dec": 2, "currency": "$"},
    "EURUSD": {"name": "EUR/USD", "dxy": False, "dec": 5, "currency": "$"},
    "GBPUSD": {"name": "GBP/USD", "dxy": False, "dec": 5, "currency": "$"},
    "USDJPY": {"name": "USD/JPY", "dxy": False, "dec": 3, "currency": "¥"},
}


def fetch_market():
    """Fetch current prices + DXY + OHLCV for analysis."""
    from market_data import UnifiedMarketData
    md = UnifiedMarketData()

    prices = {}
    for sym, info in SYMBOLS.items():
        q = md.get_quote(sym, force=True)
        if q:
            prices[sym] = {"price": q.price, "change": q.change_pct, "bid": q.bid, "ask": q.ask}

    dxy_q = md.get_quote("DX-Y.NYB", force=True)
    dxy = dxy_q.price if dxy_q else None

    # OHLCV for swing zones (H4)
    xau_bars = md.get_ohlcv("gold", "4h", 50, force=True)

    return prices, dxy, {"XAUUSD": xau_bars}


def calc_zones(bars, current_price, dec=2):
    """Calculate support/resistance zones from H4 bars."""
    if not bars or len(bars) < 5:
        return [], []

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]

    # Simple S/R from recent swing highs/lows
    supports = []
    resistances = []

    # Find local swing lows (support)
    for i in range(2, len(lows) - 2):
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            if lows[i] < current_price:
                supports.append(round(lows[i], dec))

    # Find local swing highs (resistance)
    for i in range(2, len(highs) - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            if highs[i] > current_price:
                resistances.append(round(highs[i], dec))

    # Deduplicate and sort
    supports = sorted(set(supports), reverse=True)[:3]
    resistances = sorted(set(resistances))[:3]

    return supports, resistances


def generate_mapping():
    """Generate the daily mapping message."""
    now = datetime.now(WIB)

    try:
        prices, dxy, all_bars = fetch_market()
    except Exception as e:
        log.error(f"Failed to fetch market data: {e}")
        return f"❌ Gagal generate mapping: {e}"

    xau_price = prices.get("XAUUSD", {}).get("price", 0)
    xau_change = prices.get("XAUUSD", {}).get("change", 0)
    xau_sup, xau_res = calc_zones(all_bars.get("XAUUSD"), xau_price) if all_bars.get("XAUUSD") else ([], [])

    lines = []
    lines.append(f"📊 <b>DAILY MARKET MAPPING</b>")
    lines.append(f"🟢 <b>MARKET BUKA</b> — Vilona AI v2.0 aktif")
    lines.append(f"🗓 {now.strftime('%A, %d %B %Y')} | 10:00 WIB")
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append("")

    # ── DXY ──
    if dxy:
        dxy_emoji = "🟢" if dxy < 103 else "🔴"
        lines.append(f"💵 <b>DXY:</b> {dxy_emoji} {dxy:.2f}")
        if dxy < 102:
            lines.append(f"   → DXY lemah → bullish untuk Gold & majors")
        elif dxy > 104:
            lines.append(f"   → DXY kuat → bearish pressure untuk Gold & majors")
        else:
            lines.append(f"   → DXY netral — tunggu breakout")
        lines.append("")

    # ── Key Prices ──
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"💰 <b>HARGA UTAMA</b>")
    lines.append("")
    for sym, info in prices.items():
        name = SYMBOLS.get(sym, {}).get("name", sym)
        curr = SYMBOLS.get(sym, {}).get("currency", "$")
        dec = SYMBOLS.get(sym, {}).get("dec", 2)
        chg = info.get("change", 0)
        chg_emoji = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        lines.append(f"   {chg_emoji} <b>{name}:</b> {curr}{info['price']:.{dec}f} ({chg:+.2f}%)")
    lines.append("")

    # ── XAUUSD Zones (main focus) ──
    if xau_price > 0:
        lines.append(f"━━━━━━━━━━━━━━━━")
        lines.append(f"🥇 <b>XAUUSD SWING ZONES (H4)</b>")
        lines.append(f"   Current: <b>${xau_price:.2f}</b>")
        lines.append("")

        if xau_sup:
            for i, s in enumerate(xau_sup[:3]):
                zone_low = round(s - 5, 2)
                zone_high = round(s + 5, 2)
                label = ["Support Terdekat", "Qm Buy + RBS", "Last Support H4"][i] if i < 3 else f"Support {i+1}"
                lines.append(f"📈 <b>ZONE BUY {zone_low}-{zone_high}:</b>")
                lines.append(f"   {label}")
                lines.append("")

        if xau_res:
            for i, r in enumerate(xau_res[:3]):
                zone_low = round(r - 5, 2)
                zone_high = round(r + 5, 2)
                label = ["Resisten Terdekat", "Break + Retest Resisten", "SBR + TL H4"][i] if i < 3 else f"Resistance {i+1}"
                lines.append(f"📉 <b>ZONE SELL {zone_low}-{zone_high}:</b>")
                lines.append(f"   {label}")
                lines.append("")


    # ── Session Info ──
    hour = now.hour
    if 7 <= hour < 15:
        session = "Asia → London overlap"
    elif 15 <= hour < 21:
        session = "London → NY overlap 🔥"
    elif 21 <= hour < 24:
        session = "NY Session"
    else:
        session = "Pre-Asia"

    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ <b>Session Aktif:</b> {session}")
    lines.append(f"")

    # ── Monday Sentiment ──
    if now.weekday() == 0:
        sent_label = "BULLISH" if (dxy is not None and dxy < 103) else "BEARISH"
        lines.append(f"📅 Monday Sentiment: {sent_label} — Waspadai Gaps & Volatilitas Pembukaan.")
        lines.append(f"")

    # ── Fundamental Notes ──
    lines.append(f"📰 <b>FUNDAMENTAL WATCH:</b>")
    lines.append(f"   • Monitor DXY untuk arah XAUUSD")
    lines.append(f"   • News high-impact: cek ForexFactory kalender")
    lines.append(f"   • Support/resistance H4 sebagai acuan utama")
    lines.append(f"")

    # ── CTA ──
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"⚡ Monitor zona Buy/Sell di atas — tunggu konfirmasi setup sebelum entry!")
    lines.append(f"📱 Live signal: /analyze xauusd")
    lines.append(f"🤖 Auto-trade: /autosync on")
    lines.append(f"")
    lines.append(f"<i>#VilonaTradeFX #XAUUSD #DailyMapping #TradingSignals</i>")

    return "\n".join(lines)


def send_mapping_to_channel():
    """Send mapping to the channel via Telethon. Anti-spam: once per day."""
    today_str = datetime.now(WIB).strftime("%Y%m%d")
    if _mapping_already_sent(today_str):
        log.info(f"Mapping already sent today ({today_str}) — skip (anti-spam)")
        return

    import asyncio
    from telethon import TelegramClient

    mapping_text = generate_mapping()
    log.info(f"Generated mapping ({len(mapping_text)} chars)")

    CHANNEL_ID = int(os.environ.get("VILONA_MAPPING_CHANNEL", "-1003257064212"))
    SESSION = os.path.expanduser("~/.openclaw/workspace/vilona_session")
    api_id = int(os.environ.get("TELEGRAM_API_ID", "23647272"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "1f69a4e0f03e5f51ddfa5b67ac7b5c49")

    async def send():
        client = TelegramClient(SESSION, api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Telethon not authorized!")
            return

        try:
            msg = await client.send_message(CHANNEL_ID, mapping_text, parse_mode='html', link_preview=False)
            log.info(f"Mapping sent! msg_id={msg.id}")
            _mark_mapping_sent(today_str)
            log.info(f"State tracker updated: {today_str}")
        except Exception as e:
            log.error(f"Failed to send mapping: {e}")
        finally:
            await client.disconnect()

    asyncio.run(send())


if __name__ == "__main__":
    # Test: just print, don't send
    if "--send" in sys.argv:
        send_mapping_to_channel()
    else:
        print(generate_mapping())
