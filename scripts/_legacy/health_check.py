#!/usr/bin/env python3
"""Health check for Vilona Trade FX bot — runs every 30 min via cron.
Alerts admin if bot is down, unresponsive, or misconfigured."""
import os, sys, json, time, logging, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path("/home/openclaw/projects/1ai-trade-bot")
LOG_DIR = PROJECT_DIR / "logs"
STATE_FILE = PROJECT_DIR / "data" / "vilona_tradefx" / "health_state.json"

# ── Load env ──
env_path = PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env"
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))
BOT_USERNAME = "berkahkaryaforexbotbot"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "health_check.log"), logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("health-check")

def wib_now():
    return datetime.now(WIB)

def wib_fmt(d=None):
    d = d or wib_now()
    return d.strftime("%d/%m %H:%M WIB")

def tg_send(text, chat_id):
    """Send Telegram message via bot API."""
    if not TOKEN or not chat_id:
        return False
    try:
        payload = json.dumps({
            "chat_id": int(chat_id),
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        logger.error(f"tg_send failed: {e}")
        return False

def load_state():
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {"last_ok": None, "consecutive_fails": 0, "alerts_sent": 0}

def save_state(state):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except Exception as e:
        logger.error(f"save_state failed: {e}")

def check_bot():
    """Returns (healthy, issues_list)."""
    issues = []

    # 1. Check systemd service
    import subprocess
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "vilona-tradefx-bot"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() != "active":
            issues.append("❌ Service tidak active")
    except Exception as e:
        issues.append(f"❌ Gagal cek systemd: {e}")

    # 2. Check webhook config (allowed_updates harus include message)
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo"
        with urllib.request.urlopen(url, timeout=5) as r:
            webhook = json.loads(r.read())
        if webhook.get("ok"):
            allowed = webhook["result"].get("allowed_updates", [])
            if "message" not in allowed:
                issues.append("❌ allowed_updates tidak include 'message'")
    except Exception as e:
        issues.append(f"⚠️ Gagal cek webhook: {e}")

    # 3. Check getMe (token valid)
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        with urllib.request.urlopen(url, timeout=5) as r:
            me = json.loads(r.read())
        if not me.get("ok"):
            issues.append("❌ Token bot invalid")
    except Exception as e:
        issues.append(f"❌ Gak bisa konek ke Telegram API: {e}")

    # 4. Check log recency (last entry < 10 min ago)
    try:
        log_file = LOG_DIR / "vilona_tradefx.log"
        if log_file.exists():
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=WIB)
            age_min = (wib_now() - mtime).total_seconds() / 60
            if age_min > 10:
                issues.append(f"⚠️ Log terakhir {age_min:.0f} menit lalu (mungkin bot freeze)")
    except Exception as e:
        issues.append(f"⚠️ Gagal cek log: {e}")

    # 5. Check double instances
    try:
        result = subprocess.run(
            ["pgrep", "-f", "vilona_tradefx_handler.py"],
            capture_output=True, text=True, timeout=5
        )
        pids = [p for p in result.stdout.strip().split("\n") if p]
        if len(pids) > 1:
            issues.append(f"⚠️ {len(pids)} instance bot jalan (seharusnya 1)")
        elif len(pids) == 0:
            issues.append("❌ Gak ada proses bot")
    except Exception:
        pass

    return len(issues) == 0, issues

def main():
    logger.info("🏥 Health check started")
    healthy, issues = check_bot()
    state = load_state()

    if healthy:
        logger.info("✅ Bot healthy")
        state["last_ok"] = wib_now().isoformat()
        state["consecutive_fails"] = 0
        save_state(state)
        return 0

    # ── Unhealthy ──
    state["consecutive_fails"] = state.get("consecutive_fails", 0) + 1
    fails = state["consecutive_fails"]

    issue_text = "\n".join(issues)
    logger.warning(f"❌ Bot UNHEALTHY (fail #{fails}):\n{issue_text}")
    save_state(state)

    # Alert throttle: send on fail #1, #3, #6, #12 (exponential backoff)
    alert_thresholds = [1, 3, 6, 12, 24]
    should_alert = fails in alert_thresholds or fails >= 24

    if should_alert and ADMIN_ID:
        emoji = "🟡" if fails < 3 else "🟠" if fails < 6 else "🔴"
        msg = (
            f"{emoji} <b>Vilona Bot Health Alert #{fails}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 {wib_fmt()}\n\n"
            f"{issue_text}\n\n"
            f"<i>Auto-check tiap 30 menit. Bot mungkin perlu restart.</i>"
        )
        if tg_send(msg, ADMIN_ID):
            state["alerts_sent"] = state.get("alerts_sent", 0) + 1
            save_state(state)
            logger.info(f"Alert sent to admin (fail #{fails})")

    return 1

if __name__ == "__main__":
    sys.exit(main())
