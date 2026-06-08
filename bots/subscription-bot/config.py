"""
Subscription bot configuration — loads from .env with sensible defaults.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


ENV_PATH = Path(__file__).parent / ".env"


def _load_env(path: Path = ENV_PATH) -> None:
    """Load .env file into os.environ if not already loaded."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Only set if not already present
        if key not in os.environ:
            os.environ[key] = val


_load_env()


class Config:
    """Central config — all env-based."""

    # ── Telegram ──────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.environ.get(
        "TELEGRAM_BOT_TOKEN",
        "8343388239:AAFgeAkc9bvjywyCsHqRIa_RiJ6q-rp6uv0",
    )
    ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "5220170786"))

    # ── Stockity auth ─────────────────────────────────────────────────
    STOCKITY_AUTHTOKEN: str = os.environ.get("STOCKITY_AUTHTOKEN", "")
    STOCKITY_USER_ID: str = os.environ.get("STOCKITY_USER_ID", "182899260")
    STOCKITY_FULL_COOKIE: str = os.environ.get("STOCKITY_FULL_COOKIE", "")

    # ── Signal engine ─────────────────────────────────────────────────
    SCAN_INTERVAL: int = int(os.environ.get("SCAN_INTERVAL", "300"))
    MIN_CONFIDENCE: int = int(os.environ.get("MIN_CONFIDENCE", "62"))

    # ── Database ──────────────────────────────────────────────────────
    DB_PATH: str = os.environ.get(
        "SUBSCRIPTION_DB_PATH",
        str(Path(__file__).parent / "subscription_bot.db"),
    )

    # ── Stockity trade API ────────────────────────────────────────────
    STOCKITY_WS_URL: str = "wss://ws.stockity.com/socket/websocket"

    # ── Pricing (IDR) ─────────────────────────────────────────────────
    PRICE_DAILY: int = 15_000
    PRICE_WEEKLY: int = 75_000
    PRICE_MONTHLY: int = 200_000

    @classmethod
    def pricing_text(cls) -> str:
        return (
            "📊 *Subscription Plans (IDR)*\n\n"
            f"📅 *Daily* — Rp {cls.PRICE_DAILY:,}\n"
            f"📆 *Weekly* — Rp {cls.PRICE_WEEKLY:,} (save 28%)\n"
            f"🗓 *Monthly* — Rp {cls.PRICE_MONTHLY:,} (save 55%)\n\n"
            "Use /subscribe <plan> to start.\n"
            "Example: `/subscribe daily`"
        )
