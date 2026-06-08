"""
Stockity trade client — places binary options trades via REST and Phoenix Channels WebSocket.

Approach order:
1. Try REST endpoints (POST /api/v1/trade, /api/place-order)
2. Fall back to Phoenix Channels WebSocket at wss://ws.stockity.com/socket/websocket
3. If both fail, queue the trade intent for later retry
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

LOG = logging.getLogger("subscription_bot.trade_client")

# ── Enums & Data ────────────────────────────────────────────────────────────

class Direction(Enum):
    CALL = "CALL"
    PUT = "PUT"
    UP = "CALL"       # alias
    DOWN = "PUT"      # alias


class TradeStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    WON = "won"
    LOST = "lost"
    ERROR = "error"
    QUEUED = "queued"


@dataclass
class TradeOrder:
    """A binary options trade order."""
    symbol: str
    direction: Direction
    amount: int                # in IDR
    duration_min: int = 1      # 1-60 minutes
    user_auth_token: str = ""
    user_id: str = ""
    signal_confidence: int = 0
    signal_reason: str = ""
    trade_type: str = "manual"  # manual, auto, signal
    note: str = ""
    created_at: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = int(time.time())

    @property
    def direction_api(self) -> str:
        return self.direction.value


@dataclass
class TradeResult:
    status: TradeStatus
    trade_id: str = ""
    order_id: str = ""
    message: str = ""
    entry_price: float = 0.0
    raw_response: dict = field(default_factory=dict)


# ── REST Trade Client ───────────────────────────────────────────────────────

class RestTradeClient:
    """Try REST API endpoints for placing trades."""

    ENDPOINTS = [
        "https://api.stockity.com/api/v1/trade",
        "https://api.stockity.com/api/place-order",
        "https://api.stockity.id/api/v1/trade",
    ]

    def __init__(self, master_auth_token: str = ""):
        self.master_auth = master_auth_token

    def place(self, order: TradeOrder) -> TradeResult:
        """Try each REST endpoint. Returns first successful result or last failure."""
        if not order.user_auth_token and not self.master_auth:
            return TradeResult(
                status=TradeStatus.ERROR,
                message="No auth token available for trade",
            )

        auth = order.user_auth_token or self.master_auth

        payload = {
            "symbol": order.symbol,
            "direction": order.direction_api.lower(),
            "amount": order.amount,
            "duration": order.duration_min,
            "type": "binary",
        }

        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Origin": "https://stockity.com",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        }

        last_error = ""
        for url in self.ENDPOINTS:
            try:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url, data=data, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = json.loads(resp.read())
                    LOG.info("REST trade OK via %s: %s", url, body)
                    return TradeResult(
                        status=TradeStatus.OPEN,
                        trade_id=str(body.get("id", body.get("trade_id", ""))),
                        order_id=str(body.get("order_id", "")),
                        message="Trade placed via REST",
                        raw_response=body,
                    )
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.read().decode(errors='ignore')[:200]}"
                LOG.warning("REST trade failed %s: %s", url, last_error)
            except Exception as exc:
                last_error = str(exc)[:200]
                LOG.warning("REST trade error %s: %s", url, last_error)

        return TradeResult(
            status=TradeStatus.ERROR,
            message=f"REST trade failed: {last_error}",
        )


# ── Phoenix Channel Trade Client ────────────────────────────────────────────

class PhoenixTradeClient:
    """
    Place binary options trades via Stockity's Phoenix Channels WebSocket.

    Protocol:
      - Connect to wss://ws.stockity.com/socket/websocket?token=<auth>&vsn=2.0.0
      - Send phx_join on the trading channel
      - Send trade payload on the channel
      - Listen for phx_reply or trade confirmation
    """

    WS_URL = "wss://ws.stockity.com/socket/websocket"

    def __init__(self, master_auth_token: str = ""):
        self.master_auth = master_auth_token
        self._ws = None
        self._ref_counter = 0
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _next_ref(self) -> str:
        self._ref_counter += 1
        return str(self._ref_counter)

    async def connect(self, auth_token: str = "") -> bool:
        """Connect to Phoenix WebSocket."""
        import websockets

        auth = auth_token or self.master_auth
        if not auth:
            LOG.error("No auth token for WS connection")
            return False

        url = f"{self.WS_URL}?token={auth}&vsn=2.0.0"
        try:
            self._ws = await websockets.connect(url, ping_interval=30, ping_timeout=10)
            self._loop = asyncio.get_event_loop()
            LOG.info("Connected to Stockity Phoenix WS")
            # Start heartbeat listener in background
            asyncio.create_task(self._heartbeat_loop())
            return True
        except Exception as exc:
            LOG.error("WS connect failed: %s", exc)
            self._ws = None
            return False

    async def _heartbeat_loop(self):
        """Send periodic heartbeats to keep connection alive."""
        while self._ws and not self._ws.closed:
            try:
                await asyncio.sleep(25)
                if self._ws and not self._ws.closed:
                    await self._send("phoenix", "heartbeat", {})
            except Exception:
                break

    async def _send(self, channel: str, event: str, payload: dict, ref: str = "") -> str:
        """Send a Phoenix Channels message."""
        ref = ref or self._next_ref()
        msg = json.dumps({
            "topic": channel,
            "event": event,
            "payload": payload,
            "ref": ref,
        })
        if self._ws and not self._ws.closed:
            await self._ws.send(msg)
            LOG.debug("WS sent [%s] %s %s", ref, event, channel)
        return ref

    async def _recv(self, timeout: float = 10.0) -> Optional[dict]:
        """Receive a single message with timeout."""
        if not self._ws or self._ws.closed:
            return None
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            return json.loads(raw)
        except asyncio.TimeoutError:
            LOG.warning("WS recv timeout")
            return None
        except Exception as exc:
            LOG.warning("WS recv error: %s", exc)
            return None

    async def close(self):
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

    async def join_trading_channel(self, channel_name: str = "trade:lobby") -> bool:
        """Join the trading channel (phx_join)."""
        if not self._ws or self._ws.closed:
            LOG.error("Cannot join channel: WS not connected")
            return False

        ref = await self._send(channel_name, "phx_join", {})
        resp = await self._recv(timeout=5.0)
        if resp and resp.get("event") == "phx_reply" and resp.get("ref") == ref:
            status = resp.get("payload", {}).get("status", "")
            if status == "ok":
                LOG.info("Joined trading channel: %s", channel_name)
                return True
        LOG.warning("Failed to join channel %s: %s", channel_name, resp)
        return False

    async def place_trade(self, order: TradeOrder) -> TradeResult:
        """
        Place a binary options trade via Phoenix Channels.

        Attempts:
          1. Connect WS (if not connected)
          2. Join trading channel
          3. Send trade payload
          4. Wait for confirmation
        """
        auth = order.user_auth_token or self.master_auth
        if not auth:
            return TradeResult(
                status=TradeStatus.ERROR,
                message="No auth token for trade",
            )

        # 1. Ensure connected
        if not self._ws or self._ws.closed:
            connected = await self.connect(auth)
            if not connected:
                return TradeResult(
                    status=TradeStatus.ERROR,
                    message="Failed to connect to Stockity WS",
                )

        # 2. Join trading channel
        channel = "trade:lobby"
        joined = await self.join_trading_channel(channel)
        if not joined:
            # Try channel with user scope
            if order.user_id:
                channel = f"trade:{order.user_id}"
                joined = await self.join_trading_channel(channel)
            if not joined:
                return TradeResult(
                    status=TradeStatus.ERROR,
                    message="Failed to join trading channel",
                )

        # 3. Send trade — try different event names
        trade_payload = {
            "symbol": order.symbol,
            "direction": order.direction_api.lower(),
            "amount": order.amount,
            "duration": order.duration_min,
            "type": "binary",
            "user_id": order.user_id,
        }

        trade_events = [
            "place_trade",
            "new_trade",
            "trade:new",
            "binary_trade",
        ]

        for event in trade_events:
            ref = await self._send(channel, event, trade_payload)
            resp = await self._recv(timeout=8.0)
            if resp:
                payload = resp.get("payload", {})
                status = payload.get("status", "")
                LOG.info("WS trade response for %s: %s", event, resp)
                if status in ("ok", "success") or resp.get("event") == "phx_reply":
                    return TradeResult(
                        status=TradeStatus.OPEN,
                        trade_id=str(payload.get("id", payload.get("trade_id", ref))),
                        order_id=str(payload.get("order_id", "")),
                        message=f"Trade placed via WS ({event})",
                        entry_price=float(payload.get("price", 0)),
                        raw_response=resp,
                    )

        return TradeResult(
            status=TradeStatus.QUEUED,
            message="WS trade sent but no confirmation received — queued for verification",
        )


# ── Composite Trade Client ──────────────────────────────────────────────────

class TradeClient:
    """
    Composite trade client: tries REST first, then WS, then queues.
    """

    def __init__(self, master_auth_token: str = ""):
        self.master_auth = master_auth_token
        self._rest = RestTradeClient(master_auth_token)
        self._phoenix = PhoenixTradeClient(master_auth_token)
        self._queue: list[TradeOrder] = []

    async def place(self, order: TradeOrder) -> TradeResult:
        """Place a trade — REST → WS → queue."""
        LOG.info(
            "Placing trade: %s %s %s Rp%d %dmin",
            order.symbol, order.direction_api, order.trade_type,
            order.amount, order.duration_min,
        )

        # Try REST (blocking, run in thread)
        result = await asyncio.to_thread(self._rest.place, order)
        if result.status != TradeStatus.ERROR:
            return result

        # Try WebSocket
        result = await self._phoenix.place_trade(order)
        if result.status != TradeStatus.ERROR and result.status != TradeStatus.QUEUED:
            return result

        # Queue as last resort
        if result.status == TradeStatus.QUEUED or result.status == TradeStatus.ERROR:
            self._queue.append(order)
            LOG.info("Trade queued for retry: %s %s", order.symbol, order.direction_api)
            return TradeResult(
                status=TradeStatus.QUEUED,
                message="Trade queued. Will retry automatically.",
            )

        return result

    def get_queued(self) -> list[TradeOrder]:
        return list(self._queue)

    def clear_queue(self):
        self._queue.clear()

    async def flush_queue(self):
        """Retry all queued trades."""
        remaining = []
        for order in self._queue:
            result = await self.place(order)
            if result.status == TradeStatus.QUEUED or result.status == TradeStatus.ERROR:
                remaining.append(order)
        self._queue = remaining
        LOG.info("Queue flush: %d remaining", len(self._queue))
