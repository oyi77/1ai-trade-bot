#!/usr/bin/env python3
"""expiry_reminder.py — DM users 24h before trial expiry. Runs every 4 hours."""
import sys, os, json, logging, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("expiry-reminder")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_DIR / "data" / "vilona_tradefx" / "members.db"
SENT_FILE = PROJECT_DIR / "data" / "vilona_tradefx" / "expiry_reminded.json"

def load_reminded():
    if SENT_FILE.exists():
        return set(json.loads(SENT_FILE.read_text()))
    return set()

def save_reminded(s: set):
    SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SENT_FILE.write_text(json.dumps(list(s)))

def run():
    if not DB_PATH.exists():
        logger.warning("No members.db found")
        return []

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    now = datetime.now(WIB)
    cutoff = now + timedelta(hours=24)

    reminded = load_reminded()
    to_remind = []

    try:
        c.execute("SELECT chat_id, nama, username, tier, expiry FROM members WHERE tier = 'starter'")
        for chat_id, nama, user, tier, expiry_str in c.fetchall():
            if not expiry_str:
                continue
            try:
                exp = datetime.fromisoformat(expiry_str)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=WIB)
            except Exception:
                continue

            hours_left = (exp - now).total_seconds() / 3600

            if 0 < hours_left <= 24 and chat_id not in reminded:
                to_remind.append({
                    "chat_id": chat_id,
                    "name": nama or user or str(chat_id),
                    "hours_left": hours_left,
                    "expiry": exp.isoformat(),
                })
    finally:
        conn.close()

    return to_remind

def build_message(user: dict) -> str:
    name = user["name"]
    hours = user["hours_left"]
    jam = int(hours)
    
    msg = (
        f"⚠️ <b>Halo {name}!</b>\n\n"
        f"Masa trial sinyal XAUUSD lu <b>habis dalam {jam} jam</b> 🕐\n\n"
        f"📊 Minggu ini: Winrate 87%+ dengan trailing TP otomatis\n"
        f"🤖 AI DeepSeek + GPT-4o analisa 24/7\n\n"
        f"Jangan sampe ketinggalan sinyal selanjutnya bro!\n\n"
        f"🔥 <b>/subscribe</b> — Rp50rb/bulan (PRO)\n"
        f"   • 20 sinyal/hari\n"
        f"   • SL/TP lengkap\n"
        f"   • Entry zone + pending order\n\n"
        f"👑 <b>/subscribe</b> — Rp150rb/bulan (ELITE)\n"
        f"   • Unlimited sinyal\n"
        f"   • GPT-4o + Grok News AI\n"
        f"   • /levels SnR + FIBO\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Auto-reminder system. Balas /subscribe untuk lanjut.</i>"
    )
    return msg

if __name__ == "__main__":
    users = run()
    if not users:
        print("No users to remind.")
        sys.exit(0)

    sys.path.insert(0, str(PROJECT_DIR / "scripts" / "_legacy"))
    try:
        from vilona_tradefx_handler import tg_send
    except ImportError:
        token = None
        env_path = PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env"
        if env_path.exists():
            for raw in env_path.read_text(errors="ignore").splitlines():
                line = raw.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "VILONA_TRADEFX_TELEGRAM_BOT_TOKEN":
                    token = v.strip()
                    break
        if not token:
            logger.error("Missing VILONA_TRADEFX_TELEGRAM_BOT_TOKEN in strategies/vilona_tradefx/.env")
            sys.exit(1)

        def _tg_send(text, chat_id):
            import urllib.request, json
            payload = json.dumps({"chat_id": int(chat_id), "text": str(text), "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        tg_send = _tg_send

    reminded = load_reminded()
    for u in users:
        msg = build_message(u)
        try:
            tg_send(msg, chat_id=u["chat_id"])
            reminded.add(u["chat_id"])
            logger.info(f"✅ Reminded: {u['name']} ({u['hours_left']:.0f}h left)")
        except Exception as e:
            logger.error(f"❌ Failed to DM {u['chat_id']}: {e}")

    save_reminded(reminded)
