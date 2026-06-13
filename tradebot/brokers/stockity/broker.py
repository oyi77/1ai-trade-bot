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
from typing import Any

import websockets
from websockets.protocol import State

from tradebot.brokers.base import BaseBroker, BrokerPlatform, TradeDirection, TradeResult
from tradebot.brokers.base import TradeStatus as BaseTradeStatus
from tradebot.config import settings

LOG = logging.getLogger("tradebot.brokers.stockity")

STOCKITY_PHOENIX_WS = "wss://ws.stockity.com/?v=2&vsn=2.0.0"
STOCKITY_LEGACY_WS = "wss://as.stockity.com/"




class StockityBroker(BaseBroker):
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
    platform: BrokerPlatform = BrokerPlatform.STOCKITY



    def __init__(
        self,
        deal_type: str = "demo",
        cookie: str | None = None,
        currency: str | None = None,
    ) -> None:
        """Initialize Stockity broker.

        Args:
            deal_type: "demo" or "real"
            cookie: Per-user session cookie. Falls back to global
                    STOCKITY_FULL_COOKIE from settings when None.
            currency: Account currency (e.g. "IDR", "USD"). Auto-detected
                      from balance API during connect() when None.
        """
        self._cookie: str = cookie or settings.STOCKITY_FULL_COOKIE
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._ref_counter: int = 0
        self._listener_task: asyncio.Task[None] | None = None
        self._connected: bool = False
        self._deal_type: str = deal_type  # "demo" or "real"
        # Balance tracking (native currency, no conversion)
        self._balance_raw: int = 0
        self._balance_currency: str = currency or settings.STOCKITY_CURRENCY
        self._balance_version: int = 0
        self._account_type: str = ""
        # Position tracking
        self._open_positions: dict[str, dict] = {}
        self._closed_positions: list[dict] = []
        self._total_pnl: int = 0
        self._total_wins: int = 0
        self._total_losses: int = 0
        # Subscriptions
        self._subscribed_rics: set[str] = set()
        self._tick_callbacks: list = []
        # Event handlers
        self._event_handlers: dict[str, list] = {}
        # Pending topic joins: ref -> asyncio.Future
        self._pending_joins: dict[str, asyncio.Future] = {}
    def _next_ref(self) -> str:
        self._ref_counter += 1
        return str(self._ref_counter)

    async def connect(self) -> None:
        """Connect to Phoenix Channels WebSocket and join required topics."""
        if self._connected and self._ws is not None and self._ws.state == State.OPEN:
            return

        # Cancel old listener before reconnecting
        if self._listener_task is not None and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None

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

        # Start listener FIRST to catch phx_replies
        self._listener_task = asyncio.create_task(self._listen())

        # Send ALL joins first (don't wait between them)
        refs = []
        for topic in ["connection", "bo", "account"]:
            ref = self._next_ref()
            fut = asyncio.get_event_loop().create_future()
            self._pending_joins[ref] = fut
            refs.append((topic, ref, fut))
            msg = {
                "topic": topic,
                "event": "phx_join",
                "payload": {},
                "ref": ref,
                "join_ref": ref,
            }
            await self._ws.send(json.dumps(msg))
            LOG.info("Joined topic: %s (ref=%s)", topic, ref)

        # Now wait for ALL phx_replies
        for topic, ref, fut in refs:
            try:
                await asyncio.wait_for(fut, timeout=15.0)
                # LOG.debug("Join confirmed: %s", topic)
            except asyncio.TimeoutError:
                LOG.warning("Join timeout: %s (reply may arrive later)", topic)
            finally:
                self._pending_joins.pop(ref, None)

        # Select the active balance type (demo or real)
        await self._send_event("account", "change_type", {"type": self._deal_type})

        # Fetch initial balances from REST API to seed the WebSocket state
        try:
            from tradebot.brokers.stockity.rest import StockityREST
            rest = StockityREST()
            balances_data = await rest.get_balances()
            if balances_data and balances_data.get("data"):
                for acc in balances_data["data"]:
                    if acc.get("account_type") == self._deal_type:
                        self._balance_raw = acc.get("balance", 0)
                        self._balance_currency = acc.get("currency", settings.STOCKITY_CURRENCY)
                        self._account_type = acc.get("account_type", "")
                        LOG.info(
                            "Initial Balance (REST): %.0f %s (%s)",
                            self._balance_raw,
                            self._balance_currency,
                            self._account_type,
                        )
            await rest.close()
        except Exception as e:
            LOG.error("Failed to fetch initial balance from REST API: %s", e)

        # Asset topic will be joined dynamically per trade in place_order

        self._listener_task = asyncio.create_task(self._listen())

        self._connected = True
        LOG.info("✓ StockityBroker ready")


    async def subscribe_asset(self, asset: str) -> None:
        """Subscribe to asset topic for majority_opinion + social_trading."""
        if not self._ws:
            return
        topic = f"asset:{asset}"
        await self._join_topic(topic)
        LOG.info("Subscribed to asset: %s", topic)

    def on_tick(self, callback) -> None:
        """Register callback for real-time tick data."""
        self._tick_callbacks.append(callback)

    def on_event(self, topic: str, event: str, callback) -> None:
        """Register handler for specific topic/event."""
        key = f"{topic}:{event}"
        if key not in self._event_handlers:
            self._event_handlers[key] = []
        self._event_handlers[key].append(callback)

    @property
    def currency_factor(self) -> int:
        """Return the multiplier factor for the current currency."""
        curr = self._balance_currency.upper()
        if curr == "IDR":
            return 100
        return 100_000_000

    @property
    def balance(self) -> float:
        """Current account balance in native units."""
        factor = self.currency_factor
        return self._balance_raw / factor if self._balance_raw > 0 else 0.0

    @property
    def balance_currency(self) -> str:
        """Account currency (detected dynamically from balance_changed events)."""
        return self._balance_currency

    @property
    def balance_usd(self) -> float:
        """Estimated balance in USD."""
        curr = self._balance_currency.upper()
        if curr == "IDR":
            return self.balance / 16350.0
        return self._balance_raw / 100_000_000 if self._balance_raw > 0 else 0.0

    @property
    def open_positions(self) -> list[dict]:
        """Currently open positions."""
        return list(self._open_positions.values())

    @property
    def closed_positions(self) -> list[dict]:
        """Completed trades."""
        return self._closed_positions

    @property
    def stats(self) -> dict:
        """Trading statistics."""
        return {
            "balance": self._balance_raw,
            "currency": self._balance_currency,
            "balance_usd": self.balance_usd,
            "open_positions": len(self._open_positions),
            "total_trades": self._total_wins + self._total_losses,
            "wins": self._total_wins,
            "losses": self._total_losses,
            "winrate": (self._total_wins / (self._total_wins + self._total_losses) * 100)
            if (self._total_wins + self._total_losses) > 0 else 0,
            "total_pnl_raw": self._total_pnl,
        }
        return self._balance_currency

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
        # LOG.debug("_listen task started")
        try:
            async for raw in self._ws:
                # LOG.debug("WS recv: %s", raw[:200])  # Debug: raw message
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Handle phoenix heartbeat
                if data.get("topic") == "phoenix" and data.get("event") == "heartbeat":
                    await self._ws.send(json.dumps({
                        "topic": "phoenix",
                        "event": "heartbeat",
                        "payload": {},
                        "ref": self._next_ref(),
                    }))
                    continue
                # Handle connection ping
                if data.get("topic") == "connection" and data.get("event") == "ping":
                    await self._ws.send(json.dumps({
                        "topic": "connection",
                        "event": "pong",
                        "payload": {},
                        "ref": self._next_ref(),
                    }))
                    continue
                await self._handle_message(data)
        except websockets.exceptions.ConnectionClosed:
            LOG.warning("Stockity WebSocket closed")
            self._connected = False
        except Exception as e:
            LOG.error("Listener error: %s", e)
            self._connected = False

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Process incoming Phoenix message."""
        topic = msg.get("topic", "")
        event = msg.get("event", "")
        payload = msg.get("payload", {})

        # Balance updates (native currency, no conversion)
        if topic == "account" and event == "balance_changed":
            self._balance_raw = payload.get("balance", 0)
            self._balance_currency = payload.get("currency", "")
            self._balance_version = payload.get("balance_version", 0)
            self._account_type = payload.get("account_type", "")
            LOG.info("Balance: %.0f %s (v%d)", self._balance_raw, self._balance_currency, self._balance_version)
        # Account balance from phx_reply (additional handling)
        if topic == "account" and event == "phx_reply":
            resp_data = payload.get("response", {})
            if "balance" in resp_data:
                self._balance_raw = resp_data.get("balance", 0)
                self._balance_currency = resp_data.get("currency", "")
                self._balance_version = resp_data.get("balance_version", 0)
                self._account_type = resp_data.get("account_type", "")
                LOG.info("Initial Balance: %.0f %s (v%d)", self._balance_raw, self._balance_currency, self._balance_version)
        # Real-time tick data
        if not topic and "data" in msg:
            for item in msg.get("data", []):
                if "assets" in item:
                    for asset in item["assets"]:
                        tick = {
                            "rate": asset.get("rate"), "ask": asset.get("ask"),
                            "bid": asset.get("bid"), "time": asset.get("sent_at"),
                        }
                        for cb in self._tick_callbacks:
                            try: cb(tick)
                            except Exception as e: LOG.warning("Error: %s", e)
            return

        # Resolve pending topic joins (general phx_reply)
        elif event == "phx_reply":
            ref = msg.get("ref")
            # LOG.debug("phx_reply: topic=%s ref=%s", topic, ref)
            if ref in self._pending_joins:
                fut = self._pending_joins.pop(ref)
                if not fut.done():
                    fut.set_result(payload)
                # else: pass
                # LOG.debug("Join confirmed: %s", topic)
            # else: pass

        # Event handlers (user-registered)
        key = f"{topic}:{event}"
        for handler in self._event_handlers.get(key, []):
            try:
                handler(msg)
            except Exception as exc:
                LOG.warning("Event handler failed: %s", exc)

        # Position tracking
        if topic == "bo":
            if event == "opened":
                uuid = payload.get("uuid", "")
                self._open_positions[uuid] = {
                    "id": payload.get("id"), "uuid": uuid,
                    "option_type": payload.get("option_type"),
                    "trend": payload.get("trend"),
                    "amount": payload.get("amount"),
                    "open_rate": payload.get("open_rate"),
                    "payment_rate": payload.get("payment_rate"),
                    "potential_payout": payload.get("payment"),
                    "open_time": payload.get("created_at"),
                    "close_time": payload.get("close_quote_created_at"),
                }
                LOG.info("Opened: #%s %s %s", payload.get("id"), payload.get("option_type"), payload.get("trend"))

            elif event == "closed":
                uuid = payload.get("uuid", "")
                pos = self._open_positions.pop(uuid, {})
                result = {
                    "uuid": uuid, "option_type": payload.get("option_type"),
                    "trend": payload.get("trend"),
                    "amount": payload.get("amount"),
                    "status": payload.get("status"),
                    "win": payload.get("win", 0),
                    "end_rate": payload.get("end_rate"),
                    "open_time": payload.get("created_at"),
                    "close_time": payload.get("finished_at"),
                    **pos,
                }
                self._closed_positions.append(result)
                if payload.get("status") == "won":
                    self._total_wins += 1
                    self._total_pnl += payload.get("win", 0)
                else:
                    self._total_losses += 1
                    self._total_pnl -= payload.get("amount", 0)
                LOG.info("Closed: %s win=%s P&L=%d", payload.get("status"), payload.get("win"), self._total_pnl)

            elif event == "close_deal_batch":
                for deal in payload.get("deals", []):
                    uuid = deal.get("uuid", "")
                    self._open_positions.pop(uuid, None)

        # Error handling
        elif event == "phx_reply":
            if payload.get("status") == "error":
                LOG.error("Phoenix error on %s: %s", topic, payload.get("response"))
        elif event == "phx_error":
            LOG.error("Phoenix channel error: %s", msg)

    async def get_balance(self) -> float | None:
        """Return current account balance in native units."""
        return self.balance if self.balance > 0 else None

    async def place_trade(
        self,
        symbol: str,
        direction: TradeDirection,
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
        """
        if not self._connected:
            await self.connect()

        # Ensure asset topic is joined for this symbol
        ric = _symbol_to_ric(symbol)
        asset_topic = f"asset:{ric}"
        if not hasattr(self, "_joined_assets"):
            self._joined_assets = set()
        if asset_topic not in self._joined_assets:
            await self._join_topic(asset_topic)
            self._joined_assets.add(asset_topic)

        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)

        # Calculate expire_at based on option_type
        if option_type == "blitz":
            expire_val = now_ms + 5000  # 5 seconds in ms
        elif option_type == "binary":
            # Next :00 or :30 boundary, expire in seconds
            minute = now.minute
            if minute < 30:
                expire_dt = now.replace(minute=30, second=0, microsecond=0)
            else:
                expire_dt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            expire_val = int(expire_dt.timestamp())
        else:
            # Default to next boundary
            minute = now.minute
            if minute < 30:
                expire_dt = now.replace(minute=30, second=0, microsecond=0)
            else:
                expire_dt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            expire_val = int(expire_dt.timestamp())

        payload = {
            "ric": _symbol_to_ric(symbol),
            "amount": int(amount * self.currency_factor),
            "created_at": now_ms,
            "deal_type": self._deal_type,
            "expire_at": expire_val,
            "option_type": option_type,
            "trend": direction.lower(),
            "tournament_id": None,
            "is_state": False,
        }

        try:
            ref = await self._send_event("bo", "create", payload)
            LOG.info(
                "Trade placed: ref=%s %s %s %.2f %s %ds",
                ref, symbol, direction, amount, self._balance_currency, duration,
            )
            return TradeResult(
                platform=BrokerPlatform.STOCKITY,
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
                platform=BrokerPlatform.STOCKITY,
                order_id="",
                symbol=symbol,
                direction=direction,
                amount=amount,
                duration=duration,
                status=BaseTradeStatus.REJECTED,
                error=str(e),
            )


    @property
    def deal_type(self) -> str:
        """Account type: demo or real."""
        return self._deal_type

    @property
    def trade_history(self) -> list[dict]:
        """Complete trade history (closed positions)."""
        return self._closed_positions

    def get_history(
        self,
        option_type: str | None = None,
        trend: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Filter trade history by option_type, trend, or status."""
        results = self._closed_positions
        if option_type:
            results = [t for t in results if t.get("option_type") == option_type]
        if trend:
            results = [t for t in results if t.get("trend") == trend]
        if status:
            results = [t for t in results if t.get("status") == status]
        return results[-limit:]
    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._listener_task is not None:
            self._listener_task.cancel()
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        self._connected = False
        self._subscribed_rics.clear()
        self._tick_callbacks.clear()
        self._event_handlers.clear()
        LOG.info("StockityBroker closed")

    async def __aenter__(self) -> StockityBroker:
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def _symbol_to_ric(symbol: str) -> str:
    # Map platform symbol to Stockity RIC
    from tradebot.signals.stockity import RIC_MAP
    return RIC_MAP.get(symbol.upper(), symbol)
