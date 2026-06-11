#!/usr/bin/env python3
"""
Pre-Market Briefing — DONOR TEASER posted at 07:00 WIB daily.
Posts teaser briefing to channel with locked donor-only sections.
Converts free users: "I want to know what's behind those locks."
"""
import os, sys, json, time, logging, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("premarket")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR.parent / "data" / "vilona_tradefx"
STATE_FILE = DATA_DIR / ".last_premarket_sent"

def _already_sent(today: str) -> bool:
    try:
        return STATE_FILE.read_text().strip() == today
    except Exception:
        return False

def _mark_sent(today: str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(today)

def fetch_xauusd():
    """Fetch XAUUSD spot price from gold-api.com."""
    try:
        req = urllib.request.Request(
            "https://api.gold-api.com/price/XAU",
            headers={"User-Agent": "VilonaPremarket/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return float(data.get("price", 0))
    except Exception as e:
        log.warning(f"gold-api.com failed: {e}")
    return None

def fetch_dxy():
    """Fetch DXY from Yahoo Finance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("DX-Y.NYB")
        hist = ticker.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning(f"DXY fetch failed: {e}")
    return None

def compute_xauusd_bias():
    """Compute XAUUSD bias using H1 SMA."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("GC=F")
        h1 = ticker.history(period="5d", interval="1h")
        if len(h1) < 20:
            return None, None
        
        closes = h1["Close"].tolist()
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-min(50, len(closes)):]) / min(50, len(closes))
        current = closes[-1]
        
        if current > sma20 > sma50:
            bias = "BULLISH 📈"
        elif current < sma20 < sma50:
            bias = "BEARISH 📉"
        else:
            bias = "CHOPPY ↔️"
        
        # Key levels: recent high/low
        recent_high = max(h1["High"].tail(24))
        recent_low = min(h1["Low"].tail(24))
        
        return bias, {"r1": recent_high, "s1": recent_low}
    except Exception as e:
        log.warning(f"Bias compute failed: {e}")
        return None, None

def generate_briefing():
    """Generate the pre-market briefing teaser message."""
    now = datetime.now(WIB)
    price = fetch_xauusd()
    dxy = fetch_dxy()
    bias, levels = compute_xauusd_bias()
    
    lines = [
        f"🌅 <b>PRE-MARKET BRIEFING</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 {now.strftime('%A, %d %B %Y')} | 07:00 WIB",
        f"",
        f"🏅 <b>XAUUSD</b> @ ${price:,.2f}" if price else f"🏅 <b>XAUUSD</b>",
        f"💵 <b>DXY</b>: {dxy:.2f}" if dxy else "",
        f"",
        f"📊 <b>BIAS HARI INI:</b> {bias or '—'}" if bias else "",
        f"",
    ]
    
    # ── DONOR-ONLY TEASER SECTIONS ──
    lines.extend([
        f"🔒 <b>[DONOR ONLY]</b> Key Levels:",
        f"   └ Support: <b>🔒 Unlock →</b> /donate",
        f"   └ Resistance: <b>🔒 Unlock →</b> /donate",
        f"",
        f"🔒 <b>[DONOR ONLY]</b> Entry Strategy:",
        f"   └ Buy Zone: <b>🔒 Unlock →</b> /donate",
        f"   └ Sell Zone: <b>🔒 Unlock →</b> /donate",
        f"",
        f"🔒 <b>[DONOR ONLY]</b> Risk Parameters:",
        f"   └ SL Placement: <b>🔒 Unlock →</b> /donate",
        f"   └ TP Targets: <b>🔒 Unlock →</b> /donate",
        f"",
        f"📰 <b>[DONOR ONLY]</b> News Risk Today:",
        f"   └ High-impact events: <b>🔒 Unlock →</b> /donate",
        f"   └ Red folder sessions: <b>🔒 Unlock →</b> /donate",
        f"",
    ])
    
    # ── CTA ──
    lines.extend([
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"👑 <b>SUBSCRIBER</b> dapet briefing LENGKAP tiap pagi:",
        f"   ✅ Key levels presisi (bukan nebak)",
        f"   ✅ Entry strategy + SL/TP placement",
        f"   ✅ News risk assessment (hindari jebakan)",
        f"   ✅ Bias harian dengan confidence score",
        f"",
        f"⚡ <b>Rp 50k/bulan</b> — lebih murah dari 1x loss SL.",
        f"   👉 <b>/donate</b> — Unlock briefing lengkap",
        f"",
        f"📱 Live signal: /analyze xauusd",
        f"",
        f"<i>#VilonaTradeFX #XAUUSD #Premarket #TradingSignals</i>",
    ])
    
    return "\n".join(lines)

def send_to_channel(text: str):
    """Send to channel via Telethon."""
    import asyncio
    from telethon import TelegramClient
    
    api_id = int(os.environ.get("TG_API_ID", "0"))
    api_hash = os.environ.get("TG_API_HASH", "")
    channel = os.environ.get("SIGNAL_CHANNEL_ID", os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))
    
    if not api_id or not api_hash or not channel:
        log.error("Telethon credentials or channel ID not set")
        return False
    
    async def _send():
        client = TelegramClient(
            str(PROJECT_DIR.parent / "data" / "premarket_session"),
            api_id, api_hash
        )
        await client.start()
        await client.send_message(int(channel), text, parse_mode="html")
        await client.disconnect()
    
    try:
        asyncio.run(_send())
        log.info("✅ Pre-market briefing posted to channel")
        return True
    except Exception as e:
        log.error(f"Channel post failed: {e}")
        return False

def main():
    today = datetime.now(WIB).strftime("%Y%m%d")
    if _already_sent(today):
        log.info(f"Premarket already sent today ({today}) — skip")
        return
    
    # Weekday check
    if datetime.now(WIB).weekday() >= 5:
        log.info("Weekend — skipping pre-market briefing")
        return
    
    briefing = generate_briefing()
    if send_to_channel(briefing):
        _mark_sent(today)

if __name__ == "__main__":
    main()
