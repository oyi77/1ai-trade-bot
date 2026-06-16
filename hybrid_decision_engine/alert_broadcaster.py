
"""
Alert Broadcaster — Hybrid Decision Engine
===========================================
File-watcher + Telegram broadcaster for hybrid signals.

Architecture:
  1. Polls data/signals/hybrid_signal.json every N seconds
  2. Checks file mtime to detect new signals
  3. Validates grade (A or B only for broadcast)
  4. Formats message via message_formatter
  5. Sends via Telegram Bot API (httpx)
  6. Archives signal to data/signals/sent/ (dedup)

State Management:
  - Signal file: hybrid_signal.json (written by decision_engine)
  - After broadcast: moved to sent/hybrid_signal_{timestamp}.json
  - Duplicate prevention: mtime check + archive naming
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx

from . import config
from .message_formatter import format_signal_message, format_no_signal_message

logger = logging.getLogger("hybrid.alert")

WIB = timezone(timedelta(hours=7))

BROADCASTABLE_GRADES = {"A", "B"}


class AlertBroadcaster:

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        shadow_mode: bool = True,
        min_grade: str = "B",
    ):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("SIGNAL_CHANNEL_ID", "")
        self.shadow_mode = shadow_mode
        self.min_grade = min_grade

        self.signal_file = config.SIGNALS_DIR / "hybrid_signal.json"
        self.sent_dir = config.SIGNALS_DIR / "sent"
        self.sent_dir.mkdir(parents=True, exist_ok=True)

        self._last_mtime: float = 0.0
        self._api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else ""

        enabled = bool(self.bot_token and self.chat_id)
        logger.info(
            "AlertBroadcaster init: enabled=%s, shadow=%s, min_grade=%s",
            enabled, shadow_mode, min_grade,
        )

    @property
    def is_enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def check_and_broadcast(self) -> bool:
        if not self.signal_file.exists():
            return False

        current_mtime = self.signal_file.stat().st_mtime
        if current_mtime <= self._last_mtime:
            return False

        logger.info("New signal detected (mtime=%.1f)", current_mtime)

        try:
            data = json.loads(self.signal_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read signal file: %s", e)
            self._last_mtime = current_mtime
            return False

        decision = data.get("decision", {})
        grade = decision.get("grade", "D")
        signal = decision.get("signal")
        mode = decision.get("mode", "UNKNOWN")

        if grade not in BROADCASTABLE_GRADES:
            logger.info("Signal grade %s < min %s — skipping (mode=%s)", grade, self.min_grade, mode)
            self._archive_signal(data, "skipped_grade")
            self._last_mtime = current_mtime
            return False

        if signal in ("BUY", "SELL"):
            message = format_signal_message(data, shadow_mode=self.shadow_mode)
        else:
            message = format_no_signal_message(data, shadow_mode=self.shadow_mode)
            if message is None:
                self._archive_signal(data, "no_signal_live")
                self._last_mtime = current_mtime
                return False

        success = self._send_telegram(message)

        if success:
            self._archive_signal(data, "sent")
            logger.info("Signal broadcast: %s %s (grade=%s)", decision.get("symbol"), signal, grade)
        else:
            logger.error("Failed to broadcast: %s %s", decision.get("symbol"), signal)
            self._archive_signal(data, "send_failed")

        self._last_mtime = current_mtime
        return success

    def _send_telegram(self, text: str) -> bool:
        if not self._api_url:
            logger.warning("Telegram not configured — message not sent")
            return False

        try:
            resp = httpx.post(
                self._api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("ok"):
                    msg_id = result.get("result", {}).get("message_id")
                    logger.info("Telegram sent: message_id=%s", msg_id)
                    return True
                else:
                    logger.error("Telegram API error: %s", result.get("description", "unknown"))
                    return False
            else:
                logger.error("Telegram HTTP %d: %s", resp.status_code, resp.text[:200])
                return False
        except httpx.TimeoutException:
            logger.error("Telegram send timeout (15s)")
            return False
        except Exception as e:
            logger.error("Telegram send exception: %s", e)
            return False

    def _archive_signal(self, data: dict, reason: str) -> None:
        now = datetime.now(WIB)
        ts = now.strftime("%Y%m%d_%H%M%S")
        decision = data.get("decision", {})
        symbol = decision.get("symbol", "unknown")
        sig = decision.get("signal", "none")

        archive_name = f"{symbol}_{sig}_{ts}_{reason}.json"
        archive_path = self.sent_dir / archive_name

        try:
            shutil.move(str(self.signal_file), str(archive_path))
            logger.info("Archived: %s", archive_name)
        except OSError as e:
            logger.error("Archive failed: %s", e)
            try:
                self.signal_file.unlink()
            except OSError:
                pass

    def get_stats(self) -> dict:
        sent_files = list(self.sent_dir.glob("*.json"))
        return {
            "enabled": self.is_enabled,
            "shadow_mode": self.shadow_mode,
            "min_grade": self.min_grade,
            "signal_file_exists": self.signal_file.exists(),
            "last_mtime": self._last_mtime,
            "total_broadcast": len([f for f in sent_files if "_sent_" in f.name]),
            "total_skipped": len([f for f in sent_files if "_skipped_" in f.name]),
            "total_failed": len([f for f in sent_files if "_failed_" in f.name]),
        }
