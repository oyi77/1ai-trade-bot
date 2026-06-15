"""
Expiry Reminder Adapter — wraps scripts/expiry_reminder.py.

Checks for users with expiring subscriptions (24h window) and
builds reminder messages. Does NOT send Telegram messages directly;
returns reminder items for the bot to dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))


@dataclass
class ExpiryConfig:
    db_path: str = ""
    sent_file: str = ""
    reminder_window_hours: int = 24


@dataclass
class ReminderUser:
    chat_id: str = ""
    name: str = ""
    username: str = ""
    tier: str = "starter"
    hours_left: float = 0.0
    expiry: str = ""


class ExpiryReminderAdapter:
    """
    Adapter wrapping expiry_reminder.py logic.

    Usage in UnifiedBot:
        er = ExpiryReminderAdapter(config)
        await er.initialize()
        users = await er.find_expiring_users()
        for u in users:
            msg = er.build_message(u)
            # bot sends msg to u.chat_id
        er.mark_reminded([u.chat_id for u in users])
    """

    def __init__(self, config: Optional[ExpiryConfig] = None):
        self.config = config or ExpiryConfig()
        self._initialized = False
        self._db_path: Path = Path("data/vilona_tradefx/members.db")
        self._sent_file: Path = Path("data/vilona_tradefx/expiry_reminded.json")

    async def initialize(self) -> bool:
        try:
            cfg = self.config
            if cfg.db_path:
                self._db_path = Path(cfg.db_path)
            else:
                self._db_path = (
                    Path(__file__).resolve().parent.parent.parent
                    / "data"
                    / "vilona_tradefx"
                    / "members.db"
                )
            if cfg.sent_file:
                self._sent_file = Path(cfg.sent_file)
            else:
                self._sent_file = self._db_path.parent / "expiry_reminded.json"
            self._sent_file.parent.mkdir(parents=True, exist_ok=True)
            self._initialized = True
            LOG.info("ExpiryReminderAdapter initialized")
            return True
        except Exception as e:
            LOG.error("ExpiryReminderAdapter init failed: %s", e)
            return False

    def _load_reminded(self) -> set[str]:
        if self._sent_file.exists():
            try:
                return set(json.loads(self._sent_file.read_text()))
            except Exception:
                pass
        return set()

    def _save_reminded(self, reminded: set[str]) -> None:
        self._sent_file.parent.mkdir(parents=True, exist_ok=True)
        self._sent_file.write_text(json.dumps(list(reminded)))

    async def find_expiring_users(self) -> list[ReminderUser]:
        """
        Find users whose trial expires within the reminder window.

        Returns list of ReminderUser objects.
        """
        if not self._initialized:
            await self.initialize()

        if not self._db_path.exists():
            LOG.warning("No members.db found at %s", self._db_path)
            return []

        return await asyncio.to_thread(self._find_expiring_sync)

    def _find_expiring_sync(self) -> list[ReminderUser]:
        conn = sqlite3.connect(str(self._db_path))
        now = datetime.now(WIB)
        cutoff = now + timedelta(hours=self.config.reminder_window_hours)
        reminded = self._load_reminded()
        results: list[ReminderUser] = []

        try:
            c = conn.cursor()
            c.execute(
                "SELECT chat_id, nama, username, tier, expiry FROM members WHERE tier = 'starter'"
            )
            for chat_id, nama, username, tier, expiry_str in c.fetchall():
                if not expiry_str:
                    continue
                try:
                    exp = datetime.fromisoformat(expiry_str)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=WIB)
                except ValueError:
                    continue

                hours_left = (exp - now).total_seconds() / 3600
                if 0 < hours_left <= self.config.reminder_window_hours and str(chat_id) not in reminded:
                    results.append(
                        ReminderUser(
                            chat_id=str(chat_id),
                            name=nama or username or str(chat_id),
                            username=username or "",
                            tier=tier or "starter",
                            hours_left=hours_left,
                            expiry=exp.isoformat(),
                        )
                    )
        finally:
            conn.close()

        return results

    def build_message(self, user: ReminderUser) -> str:
        """Build a reminder message for a user."""
        jam = int(user.hours_left)
        return (
            f"<b>Halo {user.name}!</b>\n\n"
            f"Masa trial sinyal XAUUSD lu <b>habis dalam {jam} jam</b>\n\n"
            f"Jangan sampe ketinggalan sinyal selanjutnya bro!\n\n"
            f"/subscribe — lanjutkan akses\n\n"
            f"<i>Auto-reminder system. Balas /subscribe untuk lanjut.</i>"
        )

    def mark_reminded(self, chat_ids: list[str]) -> None:
        """Mark users as reminded so they don't get duplicate reminders."""
        reminded = self._load_reminded()
        for cid in chat_ids:
            reminded.add(str(cid))
        self._save_reminded(reminded)

    async def shutdown(self) -> None:
        self._initialized = False
        LOG.info("ExpiryReminderAdapter shutdown")
