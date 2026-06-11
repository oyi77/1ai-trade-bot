#!/usr/bin/env python3
"""
Bensin AI Reminder — DM otomatis ke subscriber yang >30 hari gak isi ulang.
Jalan setiap Senin pagi 08:00 WIB via cron. Gentle reminder, no pressure.
"""
import os, sys, json, time, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bensin-reminder")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR.parent / "data" / "vilona_tradefx"
STATE_FILE = DATA_DIR / ".last_bensin_reminder_week"

def _already_sent_this_week():
    """Anti-spam: hanya 1x per minggu."""
    today = datetime.now(WIB)
    # Monday of current week
    mon = today - timedelta(days=today.weekday())
    week_key = mon.strftime("%Y%m%d")
    try:
        return STATE_FILE.read_text().strip() == week_key
    except Exception:
        return False

def _mark_sent():
    """Save current week key."""
    today = datetime.now(WIB)
    mon = today - timedelta(days=today.weekday())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(mon.strftime("%Y%m%d"))

def get_bot_token():
    token = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
    if not token:
        token = os.environ.get("BOT_TOKEN", "")
    if not token:
        log.error("BOT_TOKEN not set")
        return None
    return token

def send_telegram_dm(chat_id: str, text: str, token: str) -> bool:
    """Send DM via Telegram Bot API."""
    import urllib.request
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            return resp.get("ok", False)
    except Exception as e:
        log.error(f"DM failed to {chat_id}: {e}")
        return False

def build_reminder(username: str, days_since: int, last_amount: int) -> str:
    """Build gentle reminder message."""
    # Pick social proof based on days
    if days_since > 60:
        vibe = "Lama banget nih..."
        urgency = "bensin udah bener-bener menipis"
    elif days_since > 45:
        vibe = "Udah lewat sebulan lebih..."
        urgency = "server butuh isi ulang"
    else:
        vibe = "Cuma pengingat kecil"
        urgency = "bensin mulai berkurang"
    
    return (
        f"🤖 Hai <b>{username}</b>! AI Partner lu di sini…\n"
        f"\n"
        f"{vibe}. Udah <b>{days_since} hari</b> sejak terakhir lu "
        f"subscription server (Rp{last_amount:,}).\n"
        f"\n"
        f"💚 Akses VIP lu <b>TETAP AKTIF permanen</b> — gak bakal ilang. "
        f"Tapi kalau lu mau bantu subscription biar server tetep jalan "
        f"24/7 nih, gue sangat berterima kasih.\n"
        f"\n"
        f"⚡ <b>/donate</b> — Subscription (Rp 50k aja)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total subscriber bulan ini lagi di cek..\n"
        f"   Kamu terakhir isi: <b>{days_since} hari</b> lalu\n"
        f"\n"
        f"Makasih Bro! 🙏"
    )

def main():
    today = datetime.now(WIB)
    # Only run on Monday
    if today.weekday() != 0:
        log.info("Bukan hari Senin — skip")
        return
    
    if _already_sent_this_week():
        log.info("Reminder udah dikirim minggu ini — skip (anti-spam)")
        return
    
    token = get_bot_token()
    if not token:
        return
    
    from members import get_stale_donors
    stale = get_stale_donors(min_days=30)
    
    if not stale:
        log.info("✅ Semua subscriber aktif bulan ini — no reminder needed")
        _mark_sent()
        return
    
    log.info(f"📨 Akan reminder {len(stale)} subscriber yang >30 hari")
    sent_count = 0
    for subscriber in stale:
        text = build_reminder(
            donor["username"],
            donor["days_since"],
            donor["last_amount"]
        )
        ok = send_telegram_dm(donor["chat_id"], text, token)
        if ok:
            sent_count += 1
            log.info(f"  ✅ DM terkirim ke {donor['username']} ({donor['days_since']} hari)")
        else:
            log.warning(f"  ❌ Gagal DM ke {donor['username']}")
        # Gentle delay biar gak kena rate limit
        time.sleep(0.5)
    
    log.info(f"📨 Selesai: {sent_count}/{len(stale)} DM terkirim")
    _mark_sent()

if __name__ == "__main__":
    main()
