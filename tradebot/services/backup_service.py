"""Backup utility for local databases."""
import json
import logging
import urllib.request
import zipfile
import time
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tradebot.config import settings

LOG = logging.getLogger("tradebot.services.backup_service")
WIB = timezone(timedelta(hours=7))

async def execute_backup() -> None:
    LOG.info("Starting daily backup...")
    
    data_dir = Path(settings.DATA_DIR)
    if not data_dir.exists():
        LOG.error(f"Data dir not found: {data_dir}")
        return

    backup_dir = data_dir.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(WIB)
    zip_name = f"vilona_backup_{now.strftime('%Y%m%d_%H%M')}.zip"
    zip_path = backup_dir / zip_name

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in data_dir.rglob("*"):
                if f.is_file() and "__pycache__" not in str(f) and ".log" not in f.suffix:
                    zf.write(f, str(f.relative_to(data_dir.parent)))
        size_kb = zip_path.stat().st_size / 1024
        LOG.info("Backup created: %s (%.1f KB)", zip_name, size_kb)
    except Exception as e:
        LOG.error("Zip creation failed: %s", e)
        return

    admin_chat_id = settings.ADMIN_CHAT_ID
    bot_token = settings.TELEGRAM_BOT_TOKEN
    
    if admin_chat_id and bot_token:
        try:
            zip_data = zip_path.read_bytes()
            boundary = "FormBoundary" + str(int(time.time()))
            body = io.BytesIO()

            body.write(f"--{boundary}\\r\\n".encode())
            body.write(f'Content-Disposition: form-data; name="document"; filename="{zip_path.name}"\\r\\n'.encode())
            body.write("Content-Type: application/zip\\r\\n\\r\\n".encode())
            body.write(zip_data)
            body.write(b"\\r\\n")

            body.write(f"--{boundary}\\r\\n".encode())
            body.write('Content-Disposition: form-data; name="chat_id"\\r\\n\\r\\n'.encode())
            body.write(admin_chat_id.encode())
            body.write(b"\\r\\n")

            caption = (
                "🗄️ Vilona DB Backup\\n"
                f"📅 {now.strftime('%Y-%m-%d %H:%M')} WIB\\n"
                f"📦 {zip_path.name}\\n"
                f"📏 {size_kb:.1f} KB"
            )
            body.write(f"--{boundary}\\r\\n".encode())
            body.write('Content-Disposition: form-data; name="caption"\\r\\n\\r\\n'.encode())
            body.write(caption.encode())
            body.write(b"\\r\\n")
            body.write(f"--{boundary}--\\r\\n".encode())

            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendDocument",
                data=body.getvalue(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            
            if result and result.get("ok"):
                LOG.info("Backup sent to admin chat %s", admin_chat_id)
            else:
                LOG.warning("Backup created but NOT sent: %s", result)
        except urllib.error.HTTPError as e:
            if e.code == 413:
                LOG.warning("Backup created but NOT sent (File too large)")
            else:
                LOG.error("Telegram upload failed: %s", e)
        except Exception as e:
            LOG.error("Telegram upload failed: %s", e)
    else:
        LOG.warning("Backup created but not sent (ADMIN_CHAT_ID or TELEGRAM_BOT_TOKEN missing)")

    cutoff = time.time() - 7 * 86400
    for f in backup_dir.glob("vilona_backup_*.zip"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            LOG.debug("Cleaned old backup: %s", f.name)

    LOG.info("Daily backup complete")
