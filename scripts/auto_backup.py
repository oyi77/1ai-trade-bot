#!/usr/bin/env python3
"""auto_backup.py — Daily database backup sent to Admin via Telegram.

Runs at 03:00 WIB via cron.
- Zips data/vilona_tradefx/ (members.db, tracking.db, lessons.json, quota_cache/)
- Sends .zip via Telegram sendDocument API to ADMIN_CHAT_ID
- Keeps 7 days of local backups, auto-cleans older ones
"""
import json
import logging
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("auto-backup")

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
BACKUP_DIR = PROJECT_DIR / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _load_env():
    env_paths = [
        PROJECT_DIR / "strategies" / "vilona_tradefx" / ".env",
        PROJECT_DIR / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

_load_env()

BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID",
                               os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def _tg_upload(zip_path: Path) -> dict | None:
    """Send zip file to admin via Telegram sendDocument."""
    if not TELEGRAM_API_BASE or not ADMIN_CHAT_ID:
        logger.error("BOT_TOKEN or ADMIN_CHAT_ID not configured")
        return None

    import io as _io
    zip_data = zip_path.read_bytes()
    boundary = "FormBoundary" + str(int(time.time()))
    body = _io.BytesIO()

    # document file part
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="document"; filename="{zip_path.name}"\r\n'.encode())
    body.write("Content-Type: application/zip\r\n\r\n".encode())
    body.write(zip_data)
    body.write(b"\r\n")

    # chat_id part
    body.write(f"--{boundary}\r\n".encode())
    body.write('Content-Disposition: form-data; name="chat_id"\r\n\r\n'.encode())
    body.write(ADMIN_CHAT_ID.encode())
    body.write(b"\r\n")

    # caption part
    now = datetime.now(WIB)
    size_kb = len(zip_data) / 1024
    caption = (
        "🗄️ Vilona DB Backup\n"
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} WIB\n"
        f"📦 {zip_path.name}\n"
        f"📏 {size_kb:.1f} KB"
    )
    body.write(f"--{boundary}\r\n".encode())
    body.write('Content-Disposition: form-data; name="caption"\r\n\r\n'.encode())
    body.write(caption.encode())
    body.write(b"\r\n")

    body.write(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/sendDocument",
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    logger.info("Starting daily backup...")

    if not DATA_DIR.exists():
        logger.error("Data dir not found: %s", DATA_DIR)
        sys.exit(1)

    now = datetime.now(WIB)
    zip_name = f"vilona_backup_{now.strftime('%Y%m%d_%H%M')}.zip"
    zip_path = BACKUP_DIR / zip_name

    # Create zip
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in DATA_DIR.rglob("*"):
                if f.is_file() and "__pycache__" not in str(f) and ".log" not in f.suffix:
                    zf.write(f, str(f.relative_to(DATA_DIR.parent)))
        size_kb = zip_path.stat().st_size / 1024
        logger.info("Backup created: %s (%.1f KB)", zip_name, size_kb)
    except Exception as e:
        logger.error("Zip creation failed: %s", e)
        sys.exit(1)

    # Send to Telegram
    try:
        result = _tg_upload(zip_path)
        if result and result.get("ok"):
            logger.info("Backup sent to admin chat %s", ADMIN_CHAT_ID)
        else:
            logger.warning("Backup created but NOT sent: %s", result)
    except Exception as e:
        logger.error("Telegram upload failed: %s", e)

    # Cleanup old backups (keep 7 days)
    cutoff = time.time() - 7 * 86400
    for f in BACKUP_DIR.glob("vilona_backup_*.zip"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            logger.debug("Cleaned old backup: %s", f.name)

    logger.info("Daily backup complete")


if __name__ == "__main__":
    main()
