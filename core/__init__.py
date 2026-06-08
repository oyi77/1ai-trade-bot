"""Reusable Pydantic-style dataclasses for the trading system."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str          # CALL, PUT, WAIT
    confidence: int      # 0–100
    price: float
    reason: str
    timestamp_utc: str
    source: str = "yahoo"   # yahoo, stockity, vilona

    @property
    def is_tradeable(self) -> bool:
        return self.action in ("CALL", "PUT") and self.confidence >= 60

    @property
    def emoji(self) -> str:
        return {"CALL": "🟢", "PUT": "🔴", "WAIT": "⚪"}.get(self.action, "⚪")

    @property
    def source_badge(self) -> str:
        return {"yahoo": "📈Y", "stockity": "⚡S", "vilona": "🔶V"}.get(self.source, "❓")

    def pretty(self) -> str:
        return (
            f"{self.emoji} *{self.symbol}* — *{self.action}* ({self.source_badge})\n"
            f"Price: `{self.price:.6g}`\n"
            f"Confidence: *{self.confidence}%*\n"
            f"Why: {self.reason}\n"
            f"`{self.timestamp_utc}`"
        )

    def short(self) -> str:
        return (
            f"{self.emoji} {self.symbol} {self.action} "
            f"@{self.price:.5g} [{self.confidence}%]"
        )
