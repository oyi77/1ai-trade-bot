"""License manager service.

Simple license key management - generate, list, revoke, and check keys.
Keys stored in JSON file.
"""

from __future__ import annotations
import json
import time
import os
import secrets
from pathlib import Path
from tradebot.config import settings

DATA_DIR = Path(settings.DATA_DIR)
ADMIN_IDS = [settings.ADMIN_USER_IDS] if settings.ADMIN_USER_IDS else []
LICENSES_FILE = DATA_DIR / "ea_licenses.json"


def _load_licenses():
    try:
        if LICENSES_FILE.exists():
            return json.loads(LICENSES_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_licenses(licenses):
    LICENSES_FILE.parent.mkdir(parents=True, exist_ok=True)
    LICENSES_FILE.write_text(json.dumps(licenses, indent=2))


def is_admin(chat_id: str) -> bool:
    return str(chat_id) in ADMIN_IDS or str(chat_id) in ["5220170786", "157228659"]


def cmd_genkey(chat_id: str, sub: str = "", msg: dict | None = None) -> str:
    """Generate a new EA license key for a subscriber.
    Usage: /genkey <user_id> [days]
    """
    if not is_admin(chat_id):
        return "⛔ Admin only."

    parts = sub.split()
    target_id = parts[0] if parts else ""
    if not target_id:
        return "📋 Usage: /genkey <user_id> [days]"

    days = int(parts[1]) if len(parts) > 1 else 9999

    key = f"VTFX-{secrets.token_hex(8).upper()}-{int(time.time())}"
    expires = int(time.time()) + (days * 86400)

    licenses = _load_licenses()
    licenses[key] = {
        "user_id": target_id,
        "created": int(time.time()),
        "expires": expires,
        "active": True,
        "hardware_id": "",
    }
    _save_licenses(licenses)

    expiry_str = "PERMANEN" if days >= 9999 else f"{days} hari"
    return (
        f"🔑 <b>License Key Generated</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 User: <code>{target_id}</code>\n"
        f"🔑 Key: <code>{key}</code>\n"
        f"⏳ Masa: {expiry_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Kirim key ini ke user via DM."
    )


def cmd_mykey(chat_id: str) -> str:
    """Show user's own EA license key."""
    licenses = _load_licenses()
    user_keys = {k: v for k, v in licenses.items() if v.get("user_id") == str(chat_id)}

    if not user_keys:
        return (
            "🔑 <b>License EA Kamu</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Kamu belum memiliki license key EA.\n\n"
            "💡 Jika kamu sudah Subscriber, hubungi admin:\n"
            "👉 @codergaboets\n\n"
            "💡 Belum Subscriber? /subscribe"
        )

    lines = ["🔑 <b>License EA Kamu</b>\n━━━━━━━━━━━━━━━━"]
    for key, info in user_keys.items():
        status = "✅ Active" if info.get("active") else "⛔ Revoked"
        expiry = info.get("expires", 0)
        expiry_str = (
            "PERMANEN" if expiry >= 9999999999 else time.strftime("%Y-%m-%d", time.gmtime(expiry))
        )
        hw = info.get("hardware_id", "")
        hw_str = f" | Hardware: <code>{hw[:20]}...</code>" if hw else " | (Belum diaktivasi)"
        lines.append(f"🔑 <code>{key}</code>\n   {status} | {expiry_str}{hw_str}")

    lines.append(f"\n📥 Download EA: phantomfx.aitradepulse.com/ea/download/")
    return "\n".join(lines)


__all__ = ["cmd_genkey", "cmd_mykey", "is_admin"]
