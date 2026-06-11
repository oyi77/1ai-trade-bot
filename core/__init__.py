"""Reusable Pydantic-style dataclasses for the trading system."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class Candle:
    timestamp: int      # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Tick:
    asset: str
    price: float
    timestamp: int
    direction: str = ""


def _now_ts() -> int:
    return int(datetime.now(UTC).timestamp())


def _compute_expire_at(mode: str) -> int:
    now = datetime.now(UTC)
    now_ts = int(now.timestamp())
    if mode == "blitz":
        return now_ts + 5
    elif mode == "binary":
        minute = now.minute
        if minute < 30:
            expire_dt = now.replace(minute=30, second=0, microsecond=0)
        else:
            expire_dt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return int(expire_dt.timestamp())
    else:
        return now_ts + 60


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str          # CALL, PUT, WAIT
    confidence: int      # 0–100
    price: float
    reason: str
    timestamp_utc: str
    source: str = "yahoo"   # yahoo, stockity, vilona
    expire_at: int | None = None  # unix timestamp (seconds) when signal expires
    mode: str = "turbo"  # blitz (5s), turbo (1m), binary (30m boundary)

    @property
    def is_tradeable(self) -> bool:
        return self.action in ("CALL", "PUT") and self.confidence >= 60

    @property
    def is_expired(self) -> bool:
        if self.expire_at is None:
            return False
        return _now_ts() > self.expire_at

    @property
    def seconds_left(self) -> int:
        if self.expire_at is None:
            return 0
        remaining = self.expire_at - _now_ts()
        return max(0, remaining)

    @property
    def mode_label(self) -> str:
        return {"blitz": "⚡ Blitz 5s", "turbo": "🚀 Turbo 1m", "binary": "🎯 Binary 30m"}.get(self.mode, self.mode)

    @property
    def emoji(self) -> str:
        return {"CALL": "🟢", "PUT": "🔴", "WAIT": "⚪"}.get(self.action, "⚪")

    @property
    def source_badge(self) -> str:
        return {"yahoo": "📈Y", "stockity": "⚡S", "vilona": "🔶V"}.get(self.source, "❓")

    def pretty(self) -> str:
        expiry_line = ""
        if self.expire_at is not None:
            sl = self.seconds_left
            expiry_line = f"\nExpiry: `{self.mode_label}` · `{sl}s left`"
        return (
            f"{self.emoji} *{self.symbol}* — *{self.action}* ({self.source_badge})\n"
            f"Price: `{self.price:.6g}`\n"
            f"Confidence: *{self.confidence}%*"
            f"{expiry_line}\n"
            f"Why: {self.reason}\n"
            f"`{self.timestamp_utc}`"
        )

    def short(self) -> str:
        mode_tag = f" [{self.mode_label}]" if self.mode != "turbo" else ""
        return (
            f"{self.emoji} {self.symbol} {self.action} "
            f"@{self.price:.5g} [{self.confidence}%]{mode_tag}"
        )
