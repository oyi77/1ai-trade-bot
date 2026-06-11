"""Watchdog service — process health monitoring and auto-restart.

Ported from scripts/watchdog.py with full legacy fidelity.
Checks: (a) process existence via pgrep, (b) recent log update < 5 min,
(c) Telegram Bot API getMe reachable.

After MAX_FAILS_BEFORE_RESTART consecutive failures, auto-restarts via
systemctl and alerts admin via Telegram.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

LOG = logging.getLogger("tradebot.monitoring.watchdog")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
STATE_PATH = DATA_DIR / "watchdog_state.json"
LOG_PATH = DATA_DIR.parent.parent / "logs" / "bot.log"

MAX_FAILS_BEFORE_RESTART = 3
RESTART_COOLDOWN = 300  # 5 min between restarts
PROCESS_NAME = "vilona_tradefx_bot"  # systemd service name


def _load_state() -> dict:
    """Load watchdog state from disk. Returns default if missing/invalid."""
    try:
        if STATE_PATH.exists():
            raw = json.loads(STATE_PATH.read_text())
            return {
                "consecutive_fails": raw.get("consecutive_fails", 0),
                "total_restarts": raw.get("total_restarts", 0),
                "last_restart": raw.get("last_restart", 0),
                "last_check": raw.get("last_check", 0),
                "last_ok": raw.get("last_ok", 0),
            }
    except (json.JSONDecodeError, OSError):
        pass
    return {
        "consecutive_fails": 0,
        "total_restarts": 0,
        "last_restart": 0,
        "last_check": 0,
        "last_ok": 0,
    }


def _save_state(state: dict) -> None:
    """Persist watchdog state atomically."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_PATH)
    except OSError as e:
        LOG.error("Failed to save watchdog state: %s", e)


def _process_exists(name: str = PROCESS_NAME) -> bool:
    """Check if process with systemd service name exists."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", f"{name}.service"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fallback to pgrep
        pass
    try:
        result = subprocess.run(
            ["pgrep", "-f", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _log_recent(seconds: int = 300) -> bool:
    """Check if log file was modified within <seconds> ago."""
    try:
        if not LOG_PATH.exists():
            return False
        mtime = LOG_PATH.stat().st_mtime
        return (time.time() - mtime) < seconds
    except OSError:
        return False


def _telegram_reachable() -> bool:
    """Check if Telegram Bot API is reachable via getMe."""
    import urllib.request as ureq

    token = os.environ.get("VILONA_TRADEFX_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with ureq.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
            return data.get("ok", False)
    except Exception:
        return False


def _restart_service(name: str = PROCESS_NAME) -> bool:
    """Restart the systemd service. Returns True on success."""
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", f"{name}.service"],
            capture_output=True,
            timeout=30,
        )
        time.sleep(2)
        return _process_exists(name)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        LOG.error("Failed to restart service %s: %s", name, e)
        return False


def _send_telegram_alert(message: str) -> None:
    """Send alert to admin via Telegram Bot API."""
    import urllib.request as ureq

    token = os.environ.get("VILONA_TRADEFX_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    admin_id = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", "")
    if not token or not admin_id:
        LOG.warning("Cannot send alert: missing TELEGRAM_BOT_TOKEN or ADMIN_CHAT_ID")
        return
    payload = json.dumps(
        {
            "chat_id": admin_id,
            "text": message,
            "parse_mode": "HTML",
        }
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
        ureq.urlopen(req, timeout=10)
    except Exception as e:
        LOG.error("Failed to send alert: %s", e)


def format_bridge_status() -> str:
    """Format bridge + webhook status into HTML block for Telegram."""
    import json
    import urllib.request as ureq

    bridge_health = {"status": "error"}
    webhook_health = {"status": "error"}
    accounts_data = {"total_instances": 0, "master_keys_count": 0}

    try:
        with ureq.urlopen("http://localhost:8765/health", timeout=5) as r:
            bridge_health = json.loads(r.read().decode())
    except Exception:
        pass

    try:
        master_key = os.environ.get("BRIDGE_MASTER_KEY", "VT-MASTER-734AD731F5FB")
        url = f"http://localhost:8765/accounts?api_key={master_key}"
        with ureq.urlopen(url, timeout=5) as r:
            accounts_data = json.loads(r.read().decode())
    except Exception:
        pass

    try:
        with ureq.urlopen("http://localhost:8787/health", timeout=5) as r:
            webhook_health = json.loads(r.read().decode())
    except Exception:
        pass

    bridge_ok = bridge_health.get("status") == "ok"
    webhook_ok = webhook_health.get("status") == "ok"
    instances = accounts_data.get("total_instances", 0) if isinstance(accounts_data, dict) else 0
    master_keys = (
        accounts_data.get("master_keys_count", 0) if isinstance(accounts_data, dict) else 0
    )
    queue_size = bridge_health.get("queue_size", 0)
    uptime = int(float(bridge_health.get("uptime_seconds", 0) or 0))
    uptime_txt = f"{uptime // 3600}j {(uptime % 3600) // 60}m" if uptime > 0 else "—"

    txt = (
        "🛡️ <b>VILONA BRIDGE STATUS</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🌐 Bridge: {'🟢 ONLINE' if bridge_ok else '🔴 DOWN'}\n"
        f"💳 Webhook: {'🟢 ONLINE' if webhook_ok else '🔴 DOWN'}\n"
        f"⏱️ Uptime: {uptime_txt}\n"
        f"📦 Queue: {queue_size}\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🔑 Master Key Aktif: {master_keys}\n"
        f"🖥️ EA Instance Online: {instances}\n"
    )

    if isinstance(accounts_data, dict) and accounts_data.get("instances"):
        online = sum(1 for data in accounts_data["instances"].values() if data.get("online"))
        txt += f"🟢 Instance Live: {online}/{instances}\n"

    return txt


def run_watchdog_cycle() -> dict:
    """Run one watchdog check cycle. Returns state dict."""
    state = _load_state()
    now = time.time()
    state["last_check"] = now

    process_ok = _process_exists()
    log_ok = _log_recent()
    tg_ok = _telegram_reachable()

    all_ok = process_ok and log_ok and tg_ok

    if all_ok:
        state["consecutive_fails"] = 0
        state["last_ok"] = now
        LOG.info("Watchdog OK: process=%s log=%s tg=%s", process_ok, log_ok, tg_ok)
    else:
        state["consecutive_fails"] += 1
        LOG.warning(
            "Watchdog FAIL #%d: process=%s log=%s tg=%s",
            state["consecutive_fails"],
            process_ok,
            log_ok,
            tg_ok,
        )

    if (
        state["consecutive_fails"] >= MAX_FAILS_BEFORE_RESTART
        and (now - state.get("last_restart", 0)) > RESTART_COOLDOWN
    ):
        LOG.error("MAX_FAILS reached (%d) — restarting service", MAX_FAILS_BEFORE_RESTART)
        alert = (
            "🚨 <b>Watchdog Auto-Restart</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Consecutive fails: {state['consecutive_fails']}\n"
            f"Process: {'🟢' if process_ok else '🔴'}\n"
            f"Log: {'🟢' if log_ok else '🔴'}\n"
            f"Telegram: {'🟢' if tg_ok else '🔴'}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Restart #{state['total_restarts'] + 1}..."
        )
        _send_telegram_alert(alert)

        if _restart_service():
            state["total_restarts"] += 1
            state["last_restart"] = now
            state["consecutive_fails"] = 0
            _send_telegram_alert(f"✅ <b>Restart Berhasil</b> — restart #{state['total_restarts']}")
        else:
            _send_telegram_alert("❌ <b>Restart Gagal!</b> — perlu intervensi manual.")

    _save_state(state)
    return state
