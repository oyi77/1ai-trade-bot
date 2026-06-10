#!/usr/bin/env python3
"""Watchdog for Vilona Trade FX bot — runs every 5 min.
Auto-restarts bot if unresponsive for 3+ consecutive checks."""
import os, sys, json, subprocess, logging, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path("/home/openclaw/projects/1ai-trade-bot")
LOG_DIR = PROJECT_DIR / "logs"
STATE_FILE = PROJECT_DIR / "data" / "vilona_tradefx" / "watchdog_state.json"
MAX_FAILS_BEFORE_RESTART = 3

env_path = PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env"
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "watchdog.log"), logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("watchdog")

def wib_fmt():
    return datetime.now(WIB).strftime("%d/%m %H:%M WIB")

def tg_send(text):
    if not TOKEN or not ADMIN_ID:
        return False
    try:
        payload = json.dumps({"chat_id": int(ADMIN_ID), "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        logger.error(f"tg_send failed: {e}")
        return False

def check_bot_responsive():
    """Quick check if bot is alive. Returns True if OK."""
    # 1. Process exists
    try:
        r = subprocess.run(["pgrep", "-cf", "vilona_tradefx_handler.py"], capture_output=True, text=True, timeout=3)
        count = int(r.stdout.strip() or 0)
        if count == 0:
            return False
        if count > 1:
            logger.warning(f"{count} instances detected")
    except Exception:
        return False

    # 2. Log file updated recently (< 5 min)
    try:
        log_file = LOG_DIR / "vilona_tradefx.log"
        if log_file.exists():
            age = (datetime.now(WIB) - datetime.fromtimestamp(log_file.stat().st_mtime, tz=WIB)).total_seconds()
            if age > 300:
                logger.warning(f"Log stale: {age:.0f}s old")
                return False
    except Exception:
        pass

    # 3. Telegram API reachable
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        with urllib.request.urlopen(url, timeout=5) as r:
            if not json.loads(r.read()).get("ok"):
                return False
    except Exception:
        return False

    return True

def restart_bot():
    """Restart the bot service. Returns True if successful."""
    try:
        subprocess.run(["sudo", "systemctl", "restart", "vilona-tradefx-bot"], timeout=15, check=True)
        logger.info("Bot restarted successfully")
        return True
    except Exception as e:
        logger.error(f"Restart failed: {e}")
        return False

def main():
    state = {"consecutive_fails": 0, "total_restarts": 0, "last_restart": None}
    try:
        if STATE_FILE.exists():
            saved = json.loads(STATE_FILE.read_text())
            state.update(saved)
    except Exception:
        pass

    is_alive = check_bot_responsive()

    if is_alive:
        if state["consecutive_fails"] > 0:
            logger.info(f"✅ Bot recovered after {state['consecutive_fails']} fails")
        state["consecutive_fails"] = 0
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
        return 0

    # Bot not responsive
    state["consecutive_fails"] += 1
    fails = state["consecutive_fails"]
    logger.warning(f"❌ Bot unresponsive (fail #{fails})")

    if fails >= MAX_FAILS_BEFORE_RESTART:
        logger.warning(f"🔄 Auto-restarting bot (fail #{fails})...")
        if restart_bot():
            state["total_restarts"] += 1
            state["last_restart"] = wib_fmt()
            state["consecutive_fails"] = 0

            # Alert admin
            if ADMIN_ID:
                msg = (
                    f"🔄 <b>Vilona Bot Auto-Restart</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"🕐 {wib_fmt()}\n"
                    f"📊 Restart #{state['total_restarts']}\n"
                    f"⚠️ Bot was unresponsive for {fails} consecutive checks ({(fails-1)*5}+ min)\n\n"
                    f"<i>Watchdog auto-recovery. Cek /status untuk verifikasi.</i>"
                )
                tg_send(msg)
        else:
            if ADMIN_ID:
                tg_send(f"🔴 <b>Watchdog gagal restart bot!</b>\n🕐 {wib_fmt()}\nCek manual dengan SSH.")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
    return 1 if fails >= MAX_FAILS_BEFORE_RESTART else 0

if __name__ == "__main__":
    sys.exit(main())
