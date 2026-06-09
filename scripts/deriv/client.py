#!/usr/bin/env python3
"""
DerivWSClient — Production Async WebSocket Client
==================================================

Capabilities:
  - Connect/authorize via API token or OTP
  - Real-time tick subscription
  - Historical ticks (ticks_history)
  - OHLCV bar aggregation from ticks
  - Balance query + live subscription
  - Proposals + Buy execution (DIGITMATCH, DIGITOVER, etc.)
  - Auto-reconnect with exponential backoff
  - Event-driven handler system
  - Keep-alive ping every 20s
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

try:
    import httpx
except ImportError:
    httpx = None

try:
    import websockets
except ImportError:
    websockets = None

from .config import (
    WS_LEGACY, WS_NEW_DEMO, WS_NEW_REAL, REST_BASE,
    DEFAULT_APP_ID, PING_INTERVAL, PING_TIMEOUT, WS_TIMEOUT, MAX_SIZE,
    DEFAULT_CURRENCY, DEFAULT_BASIS, DEFAULT_STAKE,
)

LOG = logging.getLogger("deriv.client")


# ── Data Types ──

@dataclass
class DerivTick:
    symbol: str
    price: float
    epoch: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def digit(self) -> int:
        """Get last digit of price (0-9)."""
        s = f"{self.price:.4f}"
        if '.' in s:
            dec = s.split('.')[1]
            dec = dec[:4].ljust(4, '0')
            return int(dec[-1])
        return 0


@dataclass
class DerivOHLCV:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    symbol: str = ""
    volume: int = 0


@dataclass
class DerivContractResult:
    contract_id: int
    contract_type: str
    symbol: str
    stake: float
    payout: float
    profit: float
    entry_tick: float
    entry_digit: int = 0
    exit_tick: Optional[float] = None
    exit_digit: int = 0
    is_win: bool = False
    is_sold: bool = False
    settlement: Optional[float] = None


# ── WebSocket Client ──

class DerivWSClient:
    """Async WebSocket client for Deriv API.

    Usage:
        client = DerivWSClient(api_token="YOUR_TOKEN")
        await client.connect()
        tick = await client.get_ticks_history("R_75", count=10)
        result = await client.buy_digit("R_75", "DIGITMATCH", barrier=7, stake=0.35)
        await client.disconnect()
    """

    def __init__(self, api_token: str = "", app_id: str = DEFAULT_APP_ID,
                 otp: str = "", pat_token: str = "", mode: str = "api",
                 account_id: str = ""):
        self.api_token = api_token
        self.app_id = app_id
        self.otp = otp
        self.pat_token = pat_token
        self.mode = mode
        self.account_id = account_id

        self._ws = None
        self._connected = False
        self._running = False
        self._backoff = 2
        self._subs: dict[str, set] = {}
        self._handlers: dict[str, list[Callable]] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._msg_id = 0
        self._recv_task: Optional[asyncio.Task] = None

    # ── Properties ──

    @property
    def ws_url(self) -> str:
        if self.otp:
            # OTP URL already includes the full connection URL
            return self.otp
        if self.mode == "demo":
            return f"{WS_NEW_DEMO}?otp={self.otp}"
        return f"{WS_LEGACY}?app_id={self.app_id}"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    # ── OTP Auth ──

    async def get_otp(self) -> str:
        """Exchange PAT token for WebSocket OTP URL via REST."""
        if not self.pat_token or not self.account_id:
            raise ValueError("Both pat_token and account_id required for OTP auth")
        if httpx is None:
            raise ImportError("httpx package required. pip install httpx")
        url = f"{REST_BASE}/trading/v1/options/accounts/{self.account_id}/otp"
        headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers)
            if resp.status_code != 200:
                err = resp.text[:200]
                raise ConnectionError(f"OTP request failed ({resp.status_code}): {err}")
            data = resp.json()
            otp_url = data.get("data", {}).get("url", "")
            if not otp_url:
                raise ConnectionError(f"No OTP URL in response: {data}")
            LOG.info("OTP obtained ✓")
            return otp_url

    async def discover_accounts(self) -> list[dict]:
        """Fetch all accounts linked to this PAT token."""
        if not self.pat_token:
            raise ValueError("pat_token required to discover accounts")
        if httpx is None:
            raise ImportError("httpx package required. pip install httpx")
        headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Deriv-App-ID": self.app_id,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{REST_BASE}/trading/v1/options/accounts", headers=headers)
            if resp.status_code != 200:
                raise ConnectionError(f"Accounts fetch failed: {resp.text[:200]}")
            return resp.json().get("data", [])

    # ── Connect / Disconnect ──

    async def connect(self):
        """Establish WS connection and authorize.

        Auto-flow:
          1. If pat_token + account_id: get OTP first
          2. Connect via OTP URL (no separate authorize needed)
          3. If api_token: legacy auth via authorize message
        """
        if self.is_connected:
            return
        if websockets is None:
            raise ImportError("websockets package required. pip install websockets")

        # Auto-resolve OTP from PAT if needed
        if self.pat_token and not self.otp:
            if not self.account_id:
                accounts = await self.discover_accounts()
                if not accounts:
                    raise ConnectionError("No Deriv accounts found for this PAT")
                # Pick demo first, fallback to real
                demo = next((a for a in accounts if a.get("account_type") == "demo"), None)
                self.account_id = (demo or accounts[0])["account_id"]
                LOG.info("Auto-selected account: %s (%s %s)",
                         self.account_id, accounts[0].get("currency", "?"),
                         accounts[0].get("account_type", "?"))
            self.otp = await self.get_otp()

        LOG.info("Connecting to Deriv WS...")
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
                max_size=MAX_SIZE,
                open_timeout=WS_TIMEOUT,
            )
            self._connected = True
            self._backoff = 2
            LOG.info("✅ WS connected")

            if self.api_token and not self.otp:
                # Legacy auth — send authorize for api_token only
                resp = await self._send_await({"authorize": self.api_token}, "authorize")
                if not resp or "error" in resp:
                    err = resp.get("error", {}).get("message", "unknown") if resp else "no response"
                    LOG.error("❌ Authorize failed: %s", err)
                    await self.disconnect()
                    return False
                LOG.info("✅ Authorized")

            self._running = True
            self._recv_task = asyncio.create_task(self._receiver_loop())
            return True

        except Exception as e:
            LOG.error("Connect failed: %s", e)
            self._connected = False
            raise

    async def disconnect(self):
        """Clean disconnect — unsubscribe all, close WS."""
        self._running = False
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws:
            try:
                for stype in list(self._subs.keys()):
                    await self._safe_send({"forget_all": stype})
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        LOG.info("Disconnected")

    async def reconnect(self):
        """Auto-reconnect with exponential backoff (1s → 2s → 4s max)."""
        LOG.info("Reconnecting in %ds...", self._backoff)
        await asyncio.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, 4)
        await self.connect()
        if self.is_connected:
            for stype, symbols in self._subs.items():
                for sym in symbols:
                    await self._safe_send({stype: sym, "subscribe": 1})

    # ── Send / Await ──

    async def _safe_send(self, msg: dict) -> bool:
        if not self.is_connected:
            return False
        try:
            await self._ws.send(json.dumps(msg))
            return True
        except Exception as e:
            LOG.warning("Send error: %s", e)
            return False

    async def _send_await(self, msg: dict, expected_type: str,
                          timeout: float = 10) -> Optional[dict]:
        """Send and wait for response."""
        self._msg_id += 1
        req_id = self._msg_id
        msg["req_id"] = req_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[str(req_id)] = fut
        if not await self._safe_send(msg):
            self._pending.pop(str(req_id), None)
            return None
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(str(req_id), None)
            LOG.warning("Request %s timed out", req_id)
            return None

    # ── Receiver Loop ──

    async def _receiver_loop(self):
        """Process all incoming WS messages with auto-reconnect.

        On disconnect, attempts reconnect up to 3 times with
        exponential backoff (1s, 2s, 4s).  Resubscribes all active
        subscriptions on each successful reconnect.
        """
        while self._running:
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                if not self._running:
                    break
                # Keep-alive ping
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.send(json.dumps({"ping": 1}))
                    except Exception:
                        pass
                continue
            except websockets.ConnectionClosed as e:
                LOG.warning("WS closed (code=%s): %s", e.code, e.reason)
                self._connected = False
                if not self._running:
                    break
                # Attempt reconnect up to 3 times with exponential backoff
                reconnected = await self._reconnect_with_backoff()
                if not reconnected:
                    self._running = False
                break
            except Exception as e:
                LOG.error("WS recv error: %s", e)
                self._connected = False
                if self._running:
                    reconnected = await self._reconnect_with_backoff()
                    if not reconnected:
                        self._running = False
                break

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            await self._dispatch(data)

    async def _reconnect_with_backoff(self) -> bool:
        """Attempt reconnect up to 3 times with backoff 1s, 2s, 4s.

        Returns True if reconnected, False if all attempts failed.
        Resubscribes active subscriptions on each successful reconnect.
        """
        backoffs = [1, 2, 4]
        for attempt, delay in enumerate(backoffs, 1):
            LOG.info("Reconnect attempt %d/3 (waiting %ds)...", attempt, delay)
            await asyncio.sleep(delay)
            try:
                await self.connect()
            except Exception as e:
                LOG.warning("Reconnect attempt %d failed: %s", attempt, e)
                continue
            if self.is_connected:
                LOG.info("Reconnected on attempt %d ✓", attempt)
                # Resubscribe all active subscriptions
                for stype, symbols in self._subs.items():
                    for sym in symbols:
                        await self._safe_send({stype: sym, "subscribe": 1})
                return True
        LOG.error("All 3 reconnect attempts failed")
        return False

    async def _dispatch(self, data: dict):
        """Route incoming message to appropriate handler."""
        msg_type = data.get("msg_type", "")
        req_id = data.get("req_id") or data.get("subscription", {}).get("id")

        # Pending response
        str_id = str(req_id) if req_id else ""
        if str_id and str_id in self._pending:
            fut = self._pending.pop(str_id, None)
            if fut and not fut.done():
                fut.set_result(data)
                return

        # Tick
        if "tick" in data:
            from deriv.config import SYMBOL_LABELS
            t = data["tick"]
            tick = DerivTick(
                symbol=t.get("symbol", "?"),
                price=float(t["quote"]),
                epoch=int(t["epoch"]),
            )
            await self._fire("tick", tick)
            return

        # Balance
        if msg_type == "balance" or "balance" in data:
            bal = data.get("balance", {})
            await self._fire("balance", bal)
            return

        # Proposal
        if msg_type == "proposal":
            if "error" in data:
                await self._fire("proposal_error", data["error"])
            else:
                await self._fire("proposal", data["proposal"])
            return

        # Buy
        if msg_type == "buy":
            if "error" in data:
                await self._fire("buy_error", data["error"])
            else:
                await self._fire("buy", data["buy"])
            return

        # Contract result
        if msg_type == "proposal_open_contract":
            poc = data.get("proposal_open_contract", {})
            await self._fire("contract_result", poc)
            return

    async def _fire(self, event: str, data):
        """Call all handlers for an event."""
        for handler in self._handlers.get(event, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                LOG.error("Handler error for %s: %s", event, e)

    def on(self, event: str, handler: Callable):
        """Register event handler. Chainable: client.on('tick', fn).on('balance', fn)."""
        self._handlers.setdefault(event, []).append(handler)
        return self

    def off(self, event: str, handler: Callable):
        """Remove event handler."""
        self._handlers[event] = [h for h in self._handlers.get(event, [])
                                 if h is not handler]

    # ── Data API ──

    async def subscribe_ticks(self, symbol: str) -> bool:
        """Subscribe to real-time ticks."""
        self._subs.setdefault("ticks", set()).add(symbol)
        return await self._safe_send({"ticks": symbol, "subscribe": 1})

    async def unsubscribe_ticks(self, symbol: str):
        """Unsubscribe from ticks."""
        self._subs.get("ticks", set()).discard(symbol)
        await self._safe_send({"forget": symbol})

    async def subscribe_balance(self):
        """Subscribe to live balance updates."""
        return await self._safe_send({"balance": 1, "subscribe": 1})

    async def get_ticks_history(self, symbol: str, end: str = "latest",
                                count: int = 100) -> list[DerivTick]:
        """Fetch historical ticks."""
        resp = await self._send_await({
            "ticks_history": symbol, "end": end, "count": count, "style": "ticks",
        }, "history")
        if not resp:
            return []
        hist = resp.get("history", {})
        prices = hist.get("prices", [])
        times = hist.get("times", [])
        return [DerivTick(symbol=symbol, price=float(p), epoch=int(t))
                for p, t in zip(prices, times)]

    async def get_ohlcv(self, symbol: str, granularity: int = 60,
                        count: int = 100) -> list[DerivOHLCV]:
        """Fetch OHLCV candles natively from the Deriv API.

        Uses ticks_history with style='candles' for native candle data.

        Args:
            symbol: Trading symbol (e.g. "R_75").
            granularity: Candle interval in seconds (default 60 = 1 min).
            count: Number of candles to fetch (default 100, max 5000).

        Returns:
            List of DerivOHLCV objects sorted chronologically.
        """
        resp = await self._send_await({
            "ticks_history": symbol,
            "granularity": granularity,
            "style": "candles",
            "count": count,
        }, "candles")
        if not resp:
            return []
        candles_raw = resp.get("candles", [])
        return [
            DerivOHLCV(
                timestamp=int(c["epoch"]),
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                symbol=symbol,
            )
            for c in candles_raw
        ]

    async def get_balance(self) -> Optional[float]:
        """Fetch current account balance."""
        resp = await self._send_await({"balance": 1}, "balance")
        if resp:
            return float(resp.get("balance", {}).get("balance", 0))
        return None

    async def get_active_symbols(self) -> list[dict]:
        """List all active trading symbols."""
        resp = await self._send_await({"active_symbols": "brief"}, "active_symbols")
        return resp.get("active_symbols", []) if resp else []

    async def get_contracts_for(self, symbol: str) -> list[dict]:
        """List available contract types for a symbol."""
        resp = await self._send_await({"contracts_for": symbol}, "contracts_for")
        return resp.get("contracts_for", {}).get("available", []) if resp else []

    async def get_proposal(self, symbol: str, contract_type: str, barrier: str,
                           amount: float = DEFAULT_STAKE,
                           duration: int = 1, duration_unit: str = "t",
                           basis: str = DEFAULT_BASIS,
                           currency: str = DEFAULT_CURRENCY) -> Optional[dict]:
        """Get a price proposal for a contract.

        Note: New API (OTP mode) uses 'underlying_symbol', legacy uses 'symbol'.
        """
        params = {
            "proposal": 1, "amount": amount, "basis": basis,
            "contract_type": contract_type, "currency": currency,
            "duration": duration, "duration_unit": duration_unit,
            "barrier": barrier,
        }
        if self.otp:
            params["underlying_symbol"] = symbol
        else:
            params["symbol"] = symbol
        return await self._send_await(params, "proposal")

    async def buy_contract(self, proposal_id: str, price: float) -> Optional[dict]:
        """Execute a buy based on proposal ID."""
        return await self._send_await({"buy": proposal_id, "price": price}, "buy")

    async def buy_digit(self, symbol: str, contract_type: str, barrier: int,
                        stake: float = 0.35, duration: int = 1) -> Optional[DerivContractResult]:
        """Buy a digit contract and return result.

        Supports: DIGITMATCH, DIGITOVER, DIGITUNDER, DIGITODD, DIGITEVEN
        """
        # Validate digit barriers
        if contract_type == "DIGITOVER" and barrier >= 9:
            LOG.error("DIGITOVER barrier must be < 9")
            return None
        if contract_type == "DIGITUNDER" and barrier <= 0:
            LOG.error("DIGITUNDER barrier must be > 0")
            return None

        # Get proposal
        proposal = await self.get_proposal(
            symbol=symbol, contract_type=contract_type,
            barrier=str(barrier), amount=stake,
            duration=duration, duration_unit="t",
        )
        if not proposal or "error" in proposal:
            LOG.error("Proposal failed for %s(%s): %s",
                      contract_type, barrier, proposal)
            return None

        prop = proposal.get("proposal", {})
        proposal_id = prop.get("id")
        if not proposal_id:
            return None

        # Calculate buy price (ask + buffer)
        ask_price = float(prop.get("ask_price", stake))
        buy_price = min(max(ask_price * 1.1, ask_price + 0.5), stake * 2)

        # Execute
        buy_resp = await self.buy_contract(proposal_id, buy_price)
        if not buy_resp or "error" in buy_resp:
            LOG.error("Buy failed: %s", buy_resp)
            return None

        buy_info = buy_resp.get("buy", {})
        contract_id = int(buy_info["contract_id"])
        result = DerivContractResult(
            contract_id=contract_id,
            contract_type=contract_type,
            symbol=symbol,
            stake=float(buy_info.get("buy_price", stake)),
            payout=float(prop.get("payout", 0)),
            profit=0.0,
            entry_tick=0,
            entry_digit=barrier,
        )
        LOG.info("✅ Bought %s %s@%s stake=%.2f ID=%s",
                 contract_type, symbol, barrier, result.stake, contract_id)
        return result

    async def wait_for_settlement(self, contract_id: int, timeout: float = 30) -> Optional[dict]:
        """Wait for a contract to settle and return result."""
        fut = asyncio.get_event_loop().create_future()
        key = f"settle_{contract_id}"

        async def _on_result(poc):
            if poc.get("contract_id") == contract_id and poc.get("status") in ("won", "lost"):
                if not fut.done():
                    fut.set_result(poc)

        self.on("contract_result", _on_result)
        await self._safe_send({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1})

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.off("contract_result", _on_result)
            return None
