"""
Subscription Adapter — wraps scripts/subscription_manager.py.

Manages user subscriptions: trial onboarding, tier upgrades,
expiry checks, and reminder scheduling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))


def _wib_now() -> datetime:
    return datetime.now(WIB)


@dataclass
class SubscriptionConfig:
    data_dir: str = ""
    trial_days: int = 7
    default_tier: str = "starter"


@dataclass
class Member:
    chat_id: str = ""
    nama: str = ""
    tier: str = "starter"
    status: str = "trial"
    joined_at: str = ""
    expiry: str = ""
    reminder_sent: dict = field(default_factory=lambda: {"h7": False, "h3": False, "h1": False})
    autosync: bool = False


@dataclass
class ReminderItem:
    chat_id: str = ""
    member: dict = field(default_factory=dict)
    label: str = ""
    days_left: float = 0.0


class SubscriptionAdapter:
    """
    Adapter wrapping the subscription_manager.py logic.

    Usage in UnifiedBot:
        sub = SubscriptionAdapter(config)
        await sub.initialize()
        member = await sub.ensure_member(chat_id, name)
        due = await sub.check_due_reminders()
    """

    def __init__(self, config: Optional[SubscriptionConfig] = None):
        self.config = config or SubscriptionConfig()
        self._initialized = False
        self._data_dir: Path = Path("data/vilona_tradefx")
        self._subs_path: Path = Path("data/vilona_tradefx/subscriptions.json")
        self._reminder_path: Path = Path("data/vilona_tradefx/reminder_log.json")

    async def initialize(self) -> bool:
        try:
            if self.config.data_dir:
                self._data_dir = Path(self.config.data_dir)
            else:
                self._data_dir = (
                    Path(__file__).resolve().parent.parent.parent
                    / "data"
                    / "vilona_tradefx"
                )
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._subs_path = self._data_dir / "subscriptions.json"
            self._reminder_path = self._data_dir / "reminder_log.json"
            self._initialized = True
            LOG.info("SubscriptionAdapter initialized (data: %s)", self._data_dir)
            return True
        except Exception as e:
            LOG.error("SubscriptionAdapter init failed: %s", e)
            return False

    def _read_json(self, path: Path) -> dict:
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        return {}

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2))

    def _default_member(self, chat_id: str) -> dict:
        now = _wib_now()
        trial_end = (now + timedelta(days=self.config.trial_days)).isoformat()
        return {
            "chat_id": chat_id,
            "nama": f"User-{chat_id}",
            "tier": self.config.default_tier,
            "status": "trial",
            "joined_at": now.isoformat(),
            "expiry": trial_end,
            "reminder_sent": {"h7": False, "h3": False, "h1": False},
            "autosync": False,
        }

    async def ensure_member(self, chat_id: str, nama: Optional[str] = None) -> dict:
        if not self._initialized:
            await self.initialize()

        subs = self._read_json(self._subs_path)
        chat_id = str(chat_id)
        if chat_id not in subs:
            member = self._default_member(chat_id)
            if nama:
                member["nama"] = nama
            subs[chat_id] = member
            self._write_json(self._subs_path, subs)
            LOG.info("New member onboard: %s", chat_id)
            return member
        return subs[chat_id]

    async def get_member(self, chat_id: str) -> Optional[dict]:
        if not self._initialized:
            await self.initialize()
        subs = self._read_json(self._subs_path)
        return subs.get(str(chat_id))

    async def upgrade_tier(self, chat_id: str, tier: str, days: int = 30) -> dict:
        if not self._initialized:
            await self.initialize()

        subs = self._read_json(self._subs_path)
        chat_id = str(chat_id)
        member = subs.get(chat_id, self._default_member(chat_id))
        member["tier"] = tier
        member["status"] = "paid"
        member["expiry"] = (_wib_now() + timedelta(days=days)).isoformat()
        member["reminder_sent"] = {"h7": False, "h3": False, "h1": False}
        subs[chat_id] = member
        self._write_json(self._subs_path, subs)
        LOG.info("Member %s upgraded to %s (%d days)", chat_id, tier, days)
        return member

    async def set_reminder(self, chat_id: str, label: str) -> None:
        if not self._initialized:
            await self.initialize()
        subs = self._read_json(self._subs_path)
        member = subs.get(str(chat_id))
        if member:
            member["reminder_sent"][label] = True
            self._write_json(self._subs_path, subs)

    async def check_due_reminders(self) -> list[dict]:
        if not self._initialized:
            await self.initialize()

        subs = self._read_json(self._subs_path)
        due: list[dict] = []
        now = _wib_now()

        for chat_id, member in subs.items():
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
                due.append(
                    {
                        "chat_id": chat_id,
                        "member": member,
                        "label": "h7",
                        "days_left": days_left,
                    }
                )
            elif days_left <= 3 and not reminders.get("h3"):
                due.append(
                    {
                        "chat_id": chat_id,
                        "member": member,
                        "label": "h3",
                        "days_left": days_left,
                    }
                )
            elif days_left <= 1 and not reminders.get("h1"):
                due.append(
                    {
                        "chat_id": chat_id,
                        "member": member,
                        "label": "h1",
                        "days_left": days_left,
                    }
                )

        return due

    async def check_expired(self) -> list[dict]:
        if not self._initialized:
            await self.initialize()

        subs = self._read_json(self._subs_path)
        expired: list[dict] = []
        now = _wib_now()

        for chat_id, member in subs.items():
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

    async def mark_expired(self, chat_id: str) -> None:
        if not self._initialized:
            await self.initialize()
        subs = self._read_json(self._subs_path)
        member = subs.get(str(chat_id))
        if member:
            member["status"] = "expired"
            self._write_json(self._subs_path, subs)
            LOG.info("Member %s marked expired", chat_id)

    async def shutdown(self) -> None:
        self._initialized = False
        LOG.info("SubscriptionAdapter shutdown")
