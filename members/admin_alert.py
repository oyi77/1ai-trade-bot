#!/usr/bin/env python3
"""send_admin_alert — Global emergency DM for critical system failures."""
import json, logging, os, time, urllib.request

logger = logging.getLogger("admin-alert")
_ALERT_COOLDOWN: dict[str, float] = {}

def _cfg():
    return (
        os.environ.get("ADMIN_CHAT_ID", ""),
        os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", ""),
    )

def send_admin_alert(component: str, message: str, cooldown: int = 300):
    admin_id, bot_token = _cfg()
    if not admin_id or not bot_token:
        return
    now = time.time()
    last = _ALERT_COOLDOWN.get(component, 0)
    if now - last < cooldown:
        return
    _ALERT_COOLDOWN[component] = now
    text = (
        "🚨 <b>SYSTEM ALERT</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>{component}</b>\n"
        f"📋 {message}\n"
        "━━━━━━━━━━━━━━━━\n"
        "🕐 Auto-alert — cek log untuk detail."
    )
    try:
        payload = json.dumps({"chat_id": admin_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("ok"):
            logger.info("Admin alert sent: %s — %s", component, message[:80])
    except Exception as e:
        logger.warning("Admin alert delivery failed: %s", e)
