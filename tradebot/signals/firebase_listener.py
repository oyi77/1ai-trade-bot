"""External signal service — ingests signals from Firebase (Playstore app via RE).

Provides firebase_listener that polls Firebase Firestore REST API
for new trading signals from the reverse-engineered Playstore app.

Integrates with the unified signal pipeline and subscriber broadcast system.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import httpx

LOG = logging.getLogger(__name__)

SIGNAL_TYPES = {
    "spot": "spot",
    "futures": "futures",
}

FIREBASE_AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"
FIREBASE_SIGNAL_URL = (
    "https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/signals"
)


@dataclass
class ExternalSignal:
    """A trading signal from an external source (Firebase/TradingView/API).

    Handles TWO signal types:

    1. **Binary options** (Stockity, Pocket Option, etc.):
       - Predict UP (CALL) or DOWN (PUT) within an expiry time
       - Expiry: 5s (blitz), 30s, 60s, 2m, 5m, 15m, 30m, 60m
       - Win = fixed payout (~90%), Loss = 100% of stake
       - No SL/TP — binary outcome at expiry
       - Compensation: recovery direction if signal is wrong
       - Market: CRYPTO_IDX, BTC_IDX, ETH_IDX, GOLD_IDX, forex, etc.

    2. **Crypto spot/futures** (signals_trading_bot, Firebase):
       - Direction: BUY or SELL
       - Entry price, Stop Loss, Take Profit levels
       - Leverage for futures
    """
    # Source identification
    source: str = "firebase"              # "firebase", "tradingview", "webhook"
    signal_type: str = "binary"          # "binary", "spot", "futures"
    signal_id: str = ""
    timestamp: str = ""

    # Binary-specific fields
    direction: str = "CALL"              # "CALL" (UP) or "PUT" (DOWN) for binary
    symbol: str = "CRYPTO_IDX"           # Asset being traded
    entry_price: float = 0               # Current price at signal time
    expiry_time: str = ""                # Exact expiry time e.g. "12:58" (WIB/GMT+7)
    expiry_duration: str = ""            # Expiry length: "5s", "60s", "5m", "15m", etc.
    mode: str = "standard"               # "blitz" (5s) or "standard" (1m-60m)

    # Compensation (recovery if wrong)
    compensation: str = "SEARAH"         # "SEARAH" (same) or "BERLAWANAN" (opposite)
    max_risk_level: str = "K2"           # Risk/martingale level: "K1", "K2", "K3"

    # Premium
    is_premium: bool = False

    # Crypto spot/futures fields
    stop_loss: float = 0
    take_profit_1: float = 0
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    leverage: int | None = None
    confidence: float = 0.5

    # Raw data
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_label(self) -> str:
        """Human-readable direction label."""
        if self.direction in ("CALL", "UP", "BUY"):
            return "CALL" if self.signal_type == "binary" else "BUY"
        return "PUT" if self.signal_type == "binary" else "SELL"

    @property
    def is_up(self) -> bool:
        return self.direction in ("CALL", "UP", "BUY")

    @property
    def is_valid(self) -> bool:
        if self.signal_type == "binary":
            return bool(self.symbol) and self.entry_price > 0
        return all([self.symbol, self.entry_price > 0, self.stop_loss > 0, self.take_profit_1 > 0])

    @property
    def side(self) -> str:
        """Alias for direction_label."""
        return self.direction_label

    def compensation_direction(self) -> str:
        """If this signal expires wrong, which direction to trade next."""
        if self.compensation == "BERLAWANAN":
            return "PUT" if self.is_up else "CALL"
        return self.direction  # SEARAH = same direction

    @classmethod
    def from_firestore(cls, doc: dict[str, Any]) -> ExternalSignal | None:
        """Parse a Firebase Firestore document into an ExternalSignal.

        Handles both:
        - Binary options signals (CALL/PUT with expiry)
        - Crypto signals (BUY/SELL with entry/SL/TP)
        """
        try:
            fields = doc.get("fields", doc)
            sig_type = fields.get("type", "spot")
            direction = fields.get("direction", "")
            symbol = fields.get("symbol", fields.get("pair", "CRYPTO_IDX"))

            # Detect binary options signal
            is_binary = sig_type == "binary" or direction in ("CALL", "PUT", "UP", "DOWN")

            if is_binary:
                entry = float(fields.get("entry", fields.get("buy", fields.get("price", 0))))
                return cls(
                    source="firebase",
                    signal_type="binary",
                    signal_id=doc.get("_id", doc.get("name", "")),
                    timestamp=fields.get("createdAt", fields.get("timestamp", "")),
                    direction=direction or "CALL",
                    symbol=symbol.upper(),
                    entry_price=entry,
                    expiry_time=fields.get("expiry", fields.get("time", fields.get("expired_at", ""))),
                    expiry_duration=fields.get("duration", fields.get("expiry_duration", "")),
                    mode="blitz" if fields.get("duration") == "5s" else "standard",
                    compensation=fields.get("compensation", fields.get("kompensasi", "SEARAH")).upper(),
                    max_risk_level=fields.get("max_risk", fields.get("risk_level", "K2")).upper(),
                    is_premium=fields.get("isPremium", fields.get("premium", False)),
                    raw=fields,
                )

            # Crypto signal (spot/futures from signals_trading_bot)
            entry = float(fields.get("buy", fields.get("entry", 0)))
            stop = float(fields.get("stop", 0))
            tp1 = float(fields.get("tp1", 0))
            return cls(
                source="firebase",
                signal_type=sig_type,
                signal_id=doc.get("_id", doc.get("name", "")),
                timestamp=fields.get("createdAt", ""),
                direction="BUY" if entry > stop else "SELL",
                symbol=symbol.upper(),
                entry_price=entry,
                stop_loss=stop,
                take_profit_1=tp1,
                take_profit_2=float(fields.get("tp2")) if fields.get("tp2") else None,
                take_profit_3=float(fields.get("tp3")) if fields.get("tp3") else None,
                leverage=int(fields.get("leverage", 1)) if fields.get("leverage") else None,
                is_premium=fields.get("isPremium", False),
                raw=fields,
            )
        except (ValueError, TypeError, KeyError) as e:
            LOG.warning("Failed to parse Firebase signal: %s", e)
            return None

    def format_telegram(self) -> str:
        """Format as Telegram message matching competitor proven format."""
        if self.signal_type == "binary":
            icon = "🟩📈" if self.is_up else "🔻📉"
            arrow = "🔼" if self.is_up else "🔽"
            dir_short = "B" if self.is_up else "S"
            comp_label = "SEARAH" if self.compensation == "SEARAH" else "LAWAN"
            time_display = self.expiry_time or "NOW"
            date_str = self.timestamp[:10] if self.timestamp else ""

            lines = [
                f"{icon} {'CALL' if self.is_up else 'PUT'} NOW {arrow} |⌚ {date_str}",
                f"━━━━━━━━━━━━━━━━━━",
                f"👉 {time_display}  {dir_short}",
                f"📊 MARKET: {self.symbol}",
                f"━━━━━━━━━━━━━━━━━━",
                f"⚠️ MAXIMAL {self.max_risk_level} | KOMPENSASI {comp_label}",
                f"⚠️ LIHAT JAM DI GMT+7",
                f"⚠️ -1 MENIT SEBELUM SIGNAL",
                f"━━━━━━━━━━━━━━━━━━",
                f"🔄 /start untuk Cek Signal Berikutnya",
            ]
            return "\n".join(lines)

        icon = "🟢" if self.is_up else "🔴"
        return (
            f"{icon} <b>{self.direction} — {self.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Entry: {self.entry_price:.2f}\n"
            f"SL: {self.stop_loss:.2f} | TP: {self.take_profit_1:.2f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ /subscribe all — Dapat signal real-time!"
        )


class FirebaseSignalListener:
    """Polls Firebase Firestore for new trading signals.

    Uses Firebase REST API (no SDK required).
    Polls every N seconds for new signals, calls callback on each.
    """

    def __init__(
        self,
        project_id: str,
        api_key: str,
        poll_interval: float = 5.0,
        callback: Callable[[ExternalSignal], None] | None = None,
    ):
        self.project_id = project_id
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.callback = callback
        self._running = False
        self._last_check: str = ""
        self._seen_ids: set[str] = set()
        self._http = httpx.AsyncClient(timeout=15)

    async def start(self) -> None:
        self._running = True
        LOG.info("Firebase signal listener started (poll=%ss)", self.poll_interval)
        while self._running:
            try:
                await self._poll()
            except Exception as e:
                LOG.warning("Firebase poll error: %s", e)
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _poll(self) -> None:
        url = FIREBASE_SIGNAL_URL.format(project_id=self.project_id)
        params = {
            "pageSize": 50,
            "orderBy": "createdAt desc",
            "key": self.api_key,
        }

        try:
            resp = await self._http.get(url, params=params)
            if resp.status_code != 200:
                LOG.debug("Firebase API returned %s", resp.status_code)
                return

            data = resp.json()
            documents = data.get("documents", [])
            for doc in documents:
                sig = ExternalSignal.from_firestore(doc)
                if sig and sig.signal_id and sig.signal_id not in self._seen_ids:
                    self._seen_ids.add(sig.signal_id)
                    if sig.is_valid and self.callback:
                        self.callback(sig)
        except Exception as e:
            LOG.debug("Firebase poll failed: %s", e)


async def listen_for_signals(
    project_id: str = "",
    api_key: str = "",
    callback: Callable[[ExternalSignal], None] | None = None,
) -> None:
    """Convenience: create and start a FirebaseSignalListener."""
    from tradebot.config import settings

    pid = project_id or getattr(settings, "FIREBASE_PROJECT_ID", "")
    key = api_key or getattr(settings, "FIREBASE_API_KEY", "")

    if not pid or not key:
        LOG.warning("Firebase not configured — set FIREBASE_PROJECT_ID and FIREBASE_API_KEY")
        return

    listener = FirebaseSignalListener(
        project_id=pid,
        api_key=key,
        callback=callback,
    )
    await listener.start()
