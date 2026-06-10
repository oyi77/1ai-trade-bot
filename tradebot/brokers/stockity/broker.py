"""
Stockity trade execution broker.

Uses the Stockity WebSocket (wss://ws.stockity.com/?v=2&vsn=2.0.0) which
runs on the Phoenix Channels protocol. Trades are placed by joining the
'bo' (binary options) topic and sending a 'create' event.

Architecture (reverse-engineered from HAR):
    1. Connect to Phoenix WebSocket
    2. Join topics: connection, bo, account
    3. To trade: send {"topic": "bo", "event": "create", "payload": {...}}
    4. Receive trade confirmation (phx_reply → opened → closed)

Auth: Requires STOCKITY_FULL_COOKIE (browser session cookie) in .env.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import websockets
from websockets.protocol import State

from tradebot.brokers.base import TradeResult
from tradebot.brokers.base import TradeStatus as BaseTradeStatus
from tradebot.config import settings

LOG = logging.getLogger("tradebot.brokers.stockity")

STOCKITY_PHOENIX_WS = "wss://ws.stockity.com/?v=2&vsn=2.0.0"
STOCKITY_LEGACY_WS = "wss://as.stockity.com/"


class TradeStatus(StrEnum):
    """Stockity trade status values."""
    PENDING = "pending"
    OPENED = "opened"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class StockityBroker:
    """Trade execution broker for Stockity.

    Uses Phoenix Channels WebSocket to join the 'bo' (binary options) topic
    and place trades via the 'create' event.

    Example:
        broker = StockityBroker()
        await broker.connect()
        result = await broker.place_trade(
            symbol="CRYPTO_IDX",
            direction="CALL",
            amount=1.0,
            duration=60,
        )
        print(result)
        await broker.close()
    """

    def __init__(self) -> None:
        self._cookie: str = settings.STOCKITY_FULL_COOKIE
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._ref_counter: int = 0
        self._listener_task: asyncio.Task[None] | None = None
        self._connected: bool = False

    def _next_ref(self) -> str:
        self._ref_counter += 1
        return str(self._ref_counter)

    async def connect(self) -> None:
        """Connect to Phoenix Channels WebSocket and join required topics."""
        if self._connected and self._ws is not None and self._ws.state == State.OPEN:
            return

        if not self._cookie:
            raise RuntimeError("STOCKITY_FULL_COOKIE not set in .env")

        headers = {
            "Cookie": self._cookie,
            "Origin": "https://stockity.com",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        }
        LOG.info("Connecting to %s", STOCKITY_PHOENIX_WS)
        self._ws = await websockets.connect(STOCKITY_PHOENIX_WS, additional_headers=headers)
        LOG.info("✓ Connected to Phoenix Channels")

        for topic in ["connection", "bo", "account"]:
            await self._join_topic(topic)

        self._listener_task = asyncio.create_task(self._listen())
        self._connected = True
        LOG.info("✓ StockityBroker ready")

    async def _join_topic(self, topic: str) -> None:
        """Join a Phoenix topic."""
        if self._ws is None:
            return
        ref = self._next_ref()
        msg = {
            "topic": topic,
            "event": "phx_join",
            "payload": {},
            "ref": ref,
            "join_ref": ref,
        }
        await self._ws.send(json.dumps(msg))
        LOG.info("Joined topic: %s", topic)

    async def _send_event(
        self,
        topic: str,
        event: str,
        payload: dict[str, Any],
    ) -> str:
        """Send a Phoenix event and return its ref."""
        if self._ws is None or self._ws.state != State.OPEN:
            await self.connect()
        assert self._ws is not None
        ref = self._next_ref()
        msg = {
            "topic": topic,
            "event": event,
            "payload": payload,
            "ref": ref,
            "join_ref": self._ref_counter,
        }
        await self._ws.send(json.dumps(msg))
        return ref

    async def _listen(self) -> None:
        """Background listener for Phoenix messages."""
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_message(data)
        except websockets.exceptions.ConnectionClosed:
            LOG.warning("Stockity WebSocket closed")
            self._connected = False
        except Exception as e:
            LOG.error("Listener error: %s", e)
            self._connected = False

    async def _handle_message(self, data: dict[str, Any]) -> None:
        """Handle incoming Phoenix message."""
        event = data.get("event", "")
        topic = data.get("topic", "")
        payload = data.get("payload", {})

        if event == "opened" and topic.startswith("bo"):
            LOG.info("Trade opened: %s", payload)
        elif event == "closed" and topic.startswith("bo"):
            LOG.info("Trade closed: %s", payload)
        elif event == "balance_changed":
            LOG.debug("Balance changed: %s", payload)
        elif event == "phx_reply":
            status = payload.get("status", "")
            if status == "error":
                LOG.error("Phoenix error on %s: %s", topic, payload.get("response"))
        elif event == "phx_error":
            LOG.error("Phoenix channel error: %s", data)
        elif event not in ("phx_reply", "heartbeat", "ping"):
            LOG.debug("Phoenix event: %s on %s", event, topic)

    async def get_balance(self) -> float | None:
        """Get current account balance from the 'account' topic."""
        LOG.debug("get_balance: not fully implemented yet")
        return None

    async def place_trade(
        self,
        symbol: str,
        direction: str,
        amount: float,
        duration: int | None = None,
        option_type: str = "blitz",
    ) -> TradeResult:
        """Place a binary options trade on Stockity.

        Args:
            symbol: Asset symbol (e.g. "CRYPTO_IDX").
            direction: "CALL" (price up) or "PUT" (price down).
            amount: Stake amount in account currency.
            duration: Trade duration in seconds.

        Returns:
            Trade object with status and result.
        """
        if not self._connected:
            await self.connect()

        # RULE: Binary options MUST expire at :00 or :30 minute marks
        # (top of hour or half past)
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)

        # Find next valid boundary (:00 or :30)
        minute = now.minute
        if minute < 30:
            # Next boundary is :30 of current hour
            expire_dt = now.replace(minute=30, second=0, microsecond=0)
        else:
            # Next boundary is :00 of next hour
            expire_dt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        expire_ts = int(expire_dt.timestamp())  # Unix timestamp in SECONDS

        payload = {
            "ric": _symbol_to_ric(symbol),
            "amount": int(amount * 100000000),  # HAR shows 7400000000 for $74
            "created_at": now_ms,
            "deal_type": "demo",
            "expire_at": expire_ts,  # Unix timestamp (seconds)
            "option_type": "binary",
            "trend": direction.lower(),
            "tournament_id": None,
            "is_state": False,
        }

        try:
            ref = await self._send_event("bo", "create", payload)
            LOG.info(
                "Trade placed: ref=%s %s %s $%.2f %ds",
                ref, symbol, direction, amount, duration,
            )
            return TradeResult(
                platform="stockity",
                order_id=ref,
                symbol=symbol,
                direction=direction,
                amount=amount,
                duration=duration,
                status=BaseTradeStatus.PENDING,
            )
        except Exception as e:
            LOG.error("Trade failed: %s", e)
            return TradeResult(
                platform="stockity",
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                duration=duration,
                status=BaseTradeStatus.REJECTED,
                error=str(e),
            )

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._listener_task is not None:
            self._listener_task.cancel()
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        self._connected = False
        LOG.info("StockityBroker closed")

    async def __aenter__(self) -> StockityBroker:
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def _symbol_to_ric(symbol: str) -> str:
    """Map platform symbol to Stockity RIC."""
    from tradebot.signals.stockity import RIC_MAP
    return RIC_MAP.get(symbol.upper(), symbol)
