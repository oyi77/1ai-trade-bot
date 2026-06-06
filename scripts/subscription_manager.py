#!/usr/bin/env python3
"""
Subscription Manager — Vilona Trade FX
- Free trial onboarding (7 days)
- Reminder scheduler (H-7, H-3, H-1)
- Auto-expire + user state cleanup
- Uses existing DATA_DIR / autosync.json for active user tracking
"""

import json, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

WIB = timezone(timedelta(hours=7))

def wib_now():
    return datetime.now(WIB)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx"

logger = logging.getLogger("subscription-manager")

DATA_DIR.mkdir(parents=True, exist_ok=True)
SUBS_PATH = DATA_DIR / "subscriptions.json"
REMINDER_PATH = DATA_DIR / "reminder_log.json"


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2))


def _default_member(chat_id: str) -> dict:
    now = wib_now()
    trial_end = (now + timedelta(days=7)).isoformat()
    return {
        "chat_id": chat_id,
        "nama": f"User-{chat_id}",
        "tier": "starter",
        "status": "trial",
        "joined_at": now.isoformat(),
        "expiry": trial_end,
        "reminder_sent": {"h7": False, "h3": False, "h1": False},
        "autosync": False,
    }


def ensure_member(chat_id: str, nama: str | None = None) -> dict:
    subs = _read_json(SUBS_PATH)
    chat_id = str(chat_id)
    if chat_id not in subs:
        member = _default_member(chat_id)
        if nama:
            member["nama"] = nama
        subs[chat_id] = member
        _write_json(SUBS_PATH, subs)
        logger.info("New member onboard: %s", chat_id)
        return member
    return subs[chat_id]


def get_member(chat_id: str) -> dict | None:
    subs = _read_json(SUBS_PATH)
    return subs.get(str(chat_id))


def upgrade_tier(chat_id: str, tier: str, days: int = 30):
    subs = _read_json(SUBS_PATH)
    chat_id = str(chat_id)
    member = subs.get(chat_id)
    if not member:
        member = _default_member(chat_id)
    member["tier"] = tier
    member["status"] = "paid"
    member["expiry"] = (wib_now() + timedelta(days=days)).isoformat()
    member["reminder_sent"] = {"h7": False, "h3": False, "h1": False}
    subs[chat_id] = member
    _write_json(SUBS_PATH, subs)
    return member


def set_reminder(chat_id: str, label: str):
    subs = _read_json(SUBS_PATH)
    member = subs.get(str(chat_id))
    if member:
        member["reminder_sent"][label] = True
        _write_json(SUBS_PATH, subs)


def check_due_reminders() -> list[dict]:
    subs = _read_json(SUBS_PATH)
    due = []
    now = wib_now()
    for chat_id, member in list(subs.items()):
        expiry_str = member.get("expiry")
        if not expiry_str:
            continue
        try:
            expiry = datetime.fromisoformat(expiry_str)
        except ValueError:
            continue
        remaining = (expiry - now).total_seconds()
        days_left = remaining / 86400.0

        reminders = member.get("reminder_sent", {})
        if days_left <= 7 and not reminders.get("h7"):
            due.append({"chat_id": chat_id, "member": member, "label": "h7", "days_left": days_left})
        elif days_left <= 3 and not reminders.get("h3"):
            due.append({"chat_id": chat_id, "member": member, "label": "h3", "days_left": days_left})
        elif days_left <= 1 and not reminders.get("h1"):
            due.append({"chat_id": chat_id, "member": member, "label": "h1", "days_left": days_left})
    return due


def check_expired() -> list[dict]:
    subs = _read_json(SUBS_PATH)
    expired = []
    now = wib_now()
    for chat_id, member in list(subs.items()):
        expiry_str = member.get("expiry")
        if not expiry_str:
            continue
        try:
            expiry = datetime.fromisoformat(expiry_str)
        except ValueError:
            continue
        if now >= expiry and member.get("status") != "expired":
            expired.append({"chat_id": chat_id, "member": member})
    return expired


def mark_expired(chat_id: str):
    subs = _read_json(SUBS_PATH)
    member = subs.get(str(chat_id))
    if member:
        member["status"] = "expired"
        _write_json(SUBS_PATH, subs)
