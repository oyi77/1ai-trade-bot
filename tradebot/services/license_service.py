"""License manager service — wraps scripts/license_manager.

Temporary proxy until scripts/license_manager is absorbed into
tradebot.services.
"""

from __future__ import annotations

from scripts.license_manager import (  # type: ignore[import-not-found]
    cmd_genkey,
    cmd_mykey,
    is_admin,
)

__all__ = ["cmd_genkey", "cmd_mykey", "is_admin"]
