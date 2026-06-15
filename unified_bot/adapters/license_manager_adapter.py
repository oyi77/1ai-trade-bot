"""
License Manager Adapter — wraps scripts/license_manager.py.

Manages EA license keys: generate, list, revoke, and validate.
Stores keys in JSON file (ea_licenses.json) with plans to migrate to SQLite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)


@dataclass
class LicenseConfig:
    data_dir: str = ""
    admin_ids: list[str] = field(default_factory=list)


@dataclass
class LicenseRecord:
    key: str = ""
    user_id: str = ""
    created: int = 0
    expires: int = 0
    active: bool = True
    hardware_id: str = ""


class LicenseManagerAdapter:
    """
    Adapter wrapping license_manager.py EA license key management.

    Usage in UnifiedBot:
        lm = LicenseManagerAdapter(config)
        await lm.initialize()
        key = await lm.generate_key(chat_id, target_user_id, days=30)
        valid = await lm.validate_key(license_key)
    """

    def __init__(self, config: Optional[LicenseConfig] = None):
        self.config = config or LicenseConfig()
        self._initialized = False
        self._licenses_file: Path = Path("data/ea_licenses.json")
        self._admin_ids: list[str] = []

    async def initialize(self) -> bool:
        try:
            cfg = self.config
            if cfg.data_dir:
                self._licenses_file = Path(cfg.data_dir) / "ea_licenses.json"
            else:
                self._licenses_file = (
                    Path(__file__).resolve().parent.parent.parent
                    / "data"
                    / "ea_licenses.json"
                )
            self._licenses_file.parent.mkdir(parents=True, exist_ok=True)

            if cfg.admin_ids:
                self._admin_ids = cfg.admin_ids
            else:
                self._admin_ids = [
                    os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", ""),
                    "5220170786",
                    "157228659",
                ]
            self._initialized = True
            LOG.info("LicenseManagerAdapter initialized")
            return True
        except Exception as e:
            LOG.error("LicenseManagerAdapter init failed: %s", e)
            return False

    def _load_licenses(self) -> dict:
        try:
            if self._licenses_file.exists():
                return json.loads(self._licenses_file.read_text())
        except Exception:
            pass
        return {}

    def _save_licenses(self, licenses: dict) -> None:
        self._licenses_file.parent.mkdir(parents=True, exist_ok=True)
        self._licenses_file.write_text(json.dumps(licenses, indent=2))

    def is_admin(self, chat_id: str) -> bool:
        return str(chat_id) in self._admin_ids

    async def generate_key(
        self, admin_chat_id: str, target_user_id: str, days: int = 9999
    ) -> tuple[bool, str]:
        """
        Generate a new EA license key.

        Returns (success, message_or_key).
        """
        if not self._initialized:
            await self.initialize()

        if not self.is_admin(admin_chat_id):
            return False, "Admin only."

        if not target_user_id:
            return False, "Usage: generate_key <user_id> [days]"

        key = f"VTFX-{secrets.token_hex(8).upper()}-{int(time.time())}"
        expires = int(time.time()) + (days * 86400)

        licenses = self._load_licenses()
        licenses[key] = {
            "user_id": str(target_user_id),
            "created": int(time.time()),
            "expires": expires,
            "active": True,
            "hardware_id": "",
        }
        self._save_licenses(licenses)

        expiry_str = "PERMANEN" if days >= 9999 else f"{days} hari"
        LOG.info("License key generated: %s for user %s", key, target_user_id)
        return True, key

    async def list_keys(self, admin_chat_id: str) -> list[LicenseRecord]:
        """List all license keys (admin only)."""
        if not self._initialized:
            await self.initialize()

        if not self.is_admin(admin_chat_id):
            return []

        licenses = self._load_licenses()
        return [
            LicenseRecord(
                key=k,
                user_id=v.get("user_id", ""),
                created=v.get("created", 0),
                expires=v.get("expires", 0),
                active=v.get("active", True),
                hardware_id=v.get("hardware_id", ""),
            )
            for k, v in licenses.items()
        ]

    async def validate_key(self, license_key: str) -> tuple[bool, Optional[dict]]:
        """
        Validate an EA license key.

        Returns (valid, license_info).
        """
        if not self._initialized:
            await self.initialize()

        licenses = self._load_licenses()
        info = licenses.get(license_key)
        if not info:
            return False, None
        if not info.get("active", False):
            return False, info

        now = int(time.time())
        if info.get("expires", 0) < now:
            info["active"] = False
            self._save_licenses(licenses)
            return False, info

        return True, info

    async def revoke_key(self, admin_chat_id: str, license_key: str) -> bool:
        """Revoke/deactivate a license key (admin only)."""
        if not self._initialized:
            await self.initialize()

        if not self.is_admin(admin_chat_id):
            return False

        licenses = self._load_licenses()
        if license_key in licenses:
            licenses[license_key]["active"] = False
            self._save_licenses(licenses)
            LOG.info("License key revoked: %s", license_key)
            return True
        return False

    async def shutdown(self) -> None:
        self._initialized = False
        LOG.info("LicenseManagerAdapter shutdown")
