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
    """A trading signal from an external source (Firebase/TradingView/API)."""
    source: str                          # "firebase", "tradingview", "webhook"
    symbol: str                          # "BTC", "ETH", "XAUUSD"
    direction: str                       # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    leverage: int | None = None
    signal_type: str = "spot"           # "spot" or "futures"
    signal_id: str = ""
    is_premium: bool = False
    confidence: float = 0.5
    timestamp: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "BUY" if self.entry_price > self.stop_loss else "SELL"

    @property
    def is_valid(self) -> bool:
        return all([
            self.symbol, self.entry_price > 0,
            self.stop_loss > 0, self.take_profit_1 > 0,
        ])

    @classmethod
    def from_firestore(cls, doc: dict[str, Any]) -> ExternalSignal | None:
        try:
            fields = doc.get("fields", doc)
            symbol = fields.get("symbol", fields.get("pair", ""))
            entry = float(fields.get("buy", fields.get("entry", 0)))
            stop = float(fields.get("stop", 0))
            tp1 = float(fields.get("tp1", 0))
            tp2 = fields.get("tp2")
            tp3 = fields.get("tp3")
            lev = fields.get("leverage")
            return cls(
                source="firebase",
                symbol=symbol.upper() if symbol else "",
                direction="BUY" if entry > stop else "SELL",
                entry_price=entry,
                stop_loss=stop,
                take_profit_1=tp1,
                take_profit_2=float(tp2) if tp2 else None,
                take_profit_3=float(tp3) if tp3 else None,
                leverage=int(lev) if lev else None,
                signal_type=fields.get("type", "spot"),
                signal_id=doc.get("_id", doc.get("name", "")),
                is_premium=fields.get("isPremium", False),
                timestamp=fields.get("createdAt", ""),
                raw=fields,
            )
        except (ValueError, TypeError, KeyError) as e:
            LOG.warning("Failed to parse Firebase signal: %s", e)
            return None


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
