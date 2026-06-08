#!/usr/bin/env python3
"""Smoke test for Vilona Trade FX — runs after deploy/restart to verify everything works.
Exits 0 if all checks pass, 1 if any fail."""
import os, sys, json, time, subprocess, logging, urllib.request, importlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path("/home/openclaw/projects/1ai-trade-bot")
LOG_DIR = PROJECT_DIR / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] SMOKE: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "smoke_test.log"), logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("smoke-test")

def wib_fmt():
    return datetime.now(WIB).strftime("%d/%m %H:%M WIB")

def check(name, passed, detail=""):
    icon = "✅" if passed else "❌"
    msg = f"  {icon} {name}"
    if detail and not passed:
        msg += f" — {detail}"
    print(msg)
    if not passed:
        logger.error(f"FAIL: {name} — {detail}")
    return passed

def main():
    print(f"🧪 Vilona Smoke Test — {wib_fmt()}")
    print("━" * 40)
    all_ok = True

    # 1. Env loading
    env_path = PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env"
    env_ok = env_path.exists()
    all_ok &= check("Env file exists", env_ok, str(env_path))

    if env_ok:
        for line in env_path.read_text().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

    # 2. Critical env vars
    token = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
    tk_ok = len(token) >= 40
    all_ok &= check("Bot token valid", tk_ok, f"len={len(token)}")

    deepseek = os.environ.get("DEEPSEEK_API_KEY", "")
    ds_ok = len(deepseek) > 10
    all_ok &= check("DeepSeek key", ds_ok)

    openai = os.environ.get("OPENAI_API_KEY", "")
    oai_ok = len(openai) > 10
    all_ok &= check("OpenAI key", oai_ok)

    # 3. Systemd services
    services = {
        "vilona-tradefx-bot": "Bot handler",
        "vtfx-signal-bridge": "Signal bridge",
    }
    for svc, label in services.items():
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
            svc_ok = r.stdout.strip() == "active"
            all_ok &= check(f"Service: {label}", svc_ok, r.stdout.strip())
        except Exception as e:
            all_ok &= check(f"Service: {label}", False, str(e))

    # 4. Unique process check (no duplicates)
    try:
        r = subprocess.run(["pgrep", "-cf", "vilona_tradefx_handler.py"], capture_output=True, text=True, timeout=3)
        count = int(r.stdout.strip() or 0)
        dup_ok = count == 1
        all_ok &= check("Single bot instance", dup_ok, f"found {count}")
    except Exception as e:
        all_ok &= check("Single bot instance", False, str(e))

    # 5. Bridge API
    try:
        url = "http://localhost:8765/health"
        with urllib.request.urlopen(url, timeout=5) as r:
            bridge = json.loads(r.read())
        bridge_ok = bridge.get("status") == "ok"
        all_ok &= check("Bridge API", bridge_ok, f"uptime={bridge.get('uptime_seconds', 0)}s")
    except Exception as e:
        all_ok &= check("Bridge API", False, str(e))

    # 6. Telegram API reachable
    if token:
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            with urllib.request.urlopen(url, timeout=5) as r:
                resp = json.loads(r.read())
            tg_ok = resp.get("ok", False)
            username = resp.get("result", {}).get("username", "?")
            all_ok &= check("Telegram API", tg_ok, f"@{username}")
        except Exception as e:
            all_ok &= check("Telegram API", False, str(e))

    # 7. Webhook config
    if token:
        try:
            url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
            with urllib.request.urlopen(url, timeout=5) as r:
                wh = json.loads(r.read())
            allowed = wh.get("result", {}).get("allowed_updates", [])
            wh_ok = "message" in allowed
            all_ok &= check("Webhook config", wh_ok, str(allowed))
        except Exception as e:
            all_ok &= check("Webhook config", False, str(e))

    # 8. Data sources
    try:
        from data_sources import SYMBOL_MAP, FCS_KEY
        yahoo_ok = True  # Already imported via module
        all_ok &= check("Yahoo Finance module", True)
        fcs_ok = len(FCS_KEY or "") > 5
        all_ok &= check("FCS API key", fcs_ok, "available" if fcs_ok else "not set (optional)")
    except Exception as e:
        all_ok &= check("Data sources module", False, str(e))

    # 9. Critical files
    files = [
        ("scripts/vilona_tradefx_handler.py", "Bot handler"),
        ("scripts/engine_consensus.py", "Engine consensus"),
        ("scripts/data_sources.py", "Data sources"),
        ("scripts/health_check.py", "Health check"),
        ("scripts/watchdog.py", "Watchdog"),
    ]
    for fpath, label in files:
        f_ok = (PROJECT_DIR / fpath).exists()
        all_ok &= check(f"File: {label}", f_ok)

    # 10. Disk space
    try:
        r = subprocess.run(["df", "-h", str(PROJECT_DIR)], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        if len(lines) > 1:
            parts = lines[1].split()
            pct = parts[4] if len(parts) > 4 else "?"
            disk_ok = not pct.endswith("100%") and not pct.startswith("9")
            all_ok &= check(f"Disk space", disk_ok, f"{pct} used")
    except Exception:
        pass

    # ── Summary ──
    print("━" * 40)
    if all_ok:
        print(f"✅ ALL CHECKS PASSED — {wib_fmt()}")
        logger.info("Smoke test PASSED")
        return 0
    else:
        print(f"❌ SOME CHECKS FAILED — {wib_fmt()}")
        logger.warning("Smoke test FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
