"""
Stockity data connector — Phoenix Channels WebSocket client.

Fetches real-time asset data (candles, prices, ticks) from Stockity
via its Phoenix Channels WebSocket backend.

Usage:
    from stockity_connector import StockityDataConnector

    async with StockityDataConnector(authtoken="...", user_id="...") as conn:
        # Get candles for CRYPTO IDX
        candles = await conn.get_candles("CRYPTO_IDX", period=300, count=100)
        # Subscribe to real-time ticks
        async for tick in conn.subscribe_ticks("CRYPTO_IDX"):
            print(tick)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

LOG = logging.getLogger("stockity-connector")

ASSET_IDS: dict[str, int] = {}   # filled dynamically if possible

# Phoenix Channels defaults
PHOENIX_WS = "wss://ws.stockity.com/?v=2&vsn=2.0.0"
PHOENIX_TIMEOUT = 10_000  # ms


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
    direction: str = ""  # up/down for binary option ticks


class PhoenixSocket:
    """Minimal Phoenix Channels Socket implementation."""

    def __init__(
        self,
        url: str = PHOENIX_WS,
        authtoken: str = "",
        user_id: str = "",
    ):
        self.url = url
        self.authtoken = authtoken
        self.user_id = user_id
        self._ws = None
        self._ref = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._handlers: dict[str, list] = {}
        self._running = False
        self._session = None

    def _next_ref(self) -> str:
        self._ref += 1
        return str(self._ref)

    async def connect(self):
        """Open WebSocket connection with auth cookies."""
        import aiohttp
        from yarl import URL

        self._session = aiohttp.ClientSession(
            cookies={
                "authtoken": self.authtoken,
                "userId": str(self.user_id),
                "locale": "en",
                "user_language": "en",
                "device_type": "web",
            }
        )

        # Try ws.stockity.com first (primary), then as.stockity.com (fallback)
        for ws_url in [self.url, "wss://as.stockity.com/"]:
            try:
                self._ws = await self._session.ws_connect(
                    ws_url,
                    origin="https://stockity.com",
                    max_msg_size=0,
                    timeout=15.0,
                )
                LOG.info("Connected to %s", ws_url)
                self._running = True
                asyncio.create_task(self._reader())
                return
            except Exception as e:
                LOG.warning("Failed to connect %s: %s", ws_url, e)
                continue

        raise ConnectionError("Could not connect to any Stockity WebSocket")

    async def _reader(self):
        """Background reader for WebSocket messages."""
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=30)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._on_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    LOG.info("WS closed: code=%s", msg.data)
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    LOG.error("WS error")
                    break
            except asyncio.TimeoutError:
                # heartbeat ping
                try:
                    await self._ws.send_str(
                        json.dumps([None, None, "phoenix", "heartbeat", {}])
                    )
                except Exception:
                    break
            except Exception as e:
                LOG.error("Reader error: %s", e)
                break
        self._running = False

    def _on_message(self, raw: str):
        """Dispatch incoming Phoenix message."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Phoenix format: [join_ref, ref, topic, event, payload]
        if not isinstance(data, list) or len(data) < 4:
            return

        join_ref, ref, topic, event = data[0], data[1], data[2], data[3]
        payload = data[4] if len(data) > 4 else {}

        # Resolve pending replies
        if ref and ref in self._pending:
            self._pending[ref].set_result(payload)
            return

        # Dispatch to handlers
        key = f"{topic}:{event}"
        handlers = self._handlers.get(key, []) + self._handlers.get(f"*:{event}", [])
        for h in handlers:
            try:
                h(payload, topic, event)
            except Exception:
                LOG.exception("Handler error for %s", key)

    async def send(self, topic: str, event: str, payload: dict | None = None, ref: str | None = None) -> dict:
        """Send a Phoenix message and optionally wait for reply."""
        r = ref or self._next_ref()
        msg = json.dumps([None, r, topic, event, payload or {}])
        fut: asyncio.Future = asyncio.get_event_loop().create_future()

        if event == "phx_join" or event.startswith("phx_"):
            # phx messages don't auto-reply, use explicit tracking
            self._pending[r] = fut
        elif event == "heartbeat":
            self._pending[r] = fut

        if self._ws and not self._ws.closed:
            await self._ws.send_str(msg)

        try:
            return await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            self._pending.pop(r, None)
            return {"status": "timeout"}

    def on(self, topic: str, event: str, handler):
        """Register handler for topic:event pattern. Use '*' for wildcard topic."""
        key = f"{topic}:{event}"
        if key not in self._handlers:
            self._handlers[key] = []
        self._handlers[key].append(handler)

    async def join(self, topic: str, payload: dict | None = None) -> dict:
        """Join a Phoenix channel."""
        return await self.send(topic, "phx_join", payload, ref=self._next_ref())

    async def close(self):
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()


class StockityDataConnector:
    """High-level connector for Stockity market data."""

    def __init__(self, authtoken: str, user_id: str | int = ""):
        self.authtoken = authtoken
        self.user_id = user_id
        self._socket: PhoenixSocket | None = None
        self._asset_topics: dict[str, str] = {}

    async def __aenter__(self) -> "StockityDataConnector":
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self):
        self._socket = PhoenixSocket(
            authtoken=self.authtoken,
            user_id=self.user_id,
        )
        await self._socket.connect()

    async def close(self):
        if self._socket:
            await self._socket.close()

    # ----- Asset discovery (heuristic) -----

    async def discover_topics(self) -> list[str]:
        """
        Probe common topic patterns to discover which channels exist.
        Returns list of topics that responded with phx_reply ok.
        """
        if not self._socket:
            raise RuntimeError("Not connected")

        base_patterns = [
            "candle:{asset}",
            "asset:{asset}",
            "chart:{asset}",
            "ticker:{asset}",
            "price:{asset}",
            "quote:{asset}",
        ]

        candidate_assets = ["CRYPTO_IDX", "1", "2", "3", "BTC", "ETH", "EURUSD", "GOLD"]
        found = []

        for asset in candidate_assets:
            for pattern in base_patterns:
                topic = pattern.format(asset=asset)
                try:
                    resp = await self._socket.join(topic, {})
                    if isinstance(resp, dict) and resp.get("status") == "ok":
                        found.append(topic)
                        LOG.info("Discovered topic: %s", topic)
                        self._asset_topics[asset] = topic
                except Exception:
                    pass
                # Small delay to avoid flooding
                await asyncio.sleep(0.2)

        return found

    async def get_candles(
        self,
        asset: str = "CRYPTO_IDX",
        period: int = 60,
        count: int = 100,
    ) -> list[Candle]:
        """
        Request historical candles for an asset.

        Heuristic: tries known Phoenix candle topics and resolution formats.
        """
        if not self._socket:
            raise RuntimeError("Not connected")

        # Try multiple topic/event combinations
        topics_to_try = [
            ("candle:" + asset, "history", {"period": period, "count": count}),
            ("candle:" + asset, "candles", {"resolution": period, "limit": count}),
            ("chart:" + asset, "history", {"resolution": period, "limit": count}),
            ("chart:" + asset, "candles", {"granularity": period, "size": count}),
            ("asset:" + asset, "history", {"resolution": period, "limit": count}),
        ]

        for topic, event, payload in topics_to_try:
            try:
                # Try joining the channel first
                join_resp = await self._socket.join(topic, {})
                if isinstance(join_resp, dict) and join_resp.get("status") == "ok":
                    # Send candle request
                    resp = await self._socket.send(topic, event, payload)
                    if resp and isinstance(resp, dict) and resp.get("status") != "timeout":
                        return self._parse_candle_response(resp)
            except Exception:
                continue
            await asyncio.sleep(0.3)

        return []

    def _parse_candle_response(self, data: dict) -> list[Candle]:
        """Parse various candle response formats."""
        candles = []

        # Try different response shapes
        raw = None
        for key in ["candles", "data", "history", "result", "records", "items"]:
            if key in data and isinstance(data[key], list):
                raw = data[key]
                break

        if not raw:
            return []

        for item in raw:
            if isinstance(item, list) and len(item) >= 5:
                # Format: [timestamp, open, high, low, close, volume?]
                candles.append(Candle(
                    timestamp=int(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]) if len(item) > 5 else 0,
                ))
            elif isinstance(item, dict):
                ts = item.get("t") or item.get("timestamp") or item.get("time") or item.get("date")
                o = item.get("o") or item.get("open")
                h = item.get("h") or item.get("high")
                l = item.get("l") or item.get("low")
                c = item.get("c") or item.get("close")
                v = item.get("v") or item.get("volume") or 0
                if all([ts, o, h, l, c]):
                    candles.append(Candle(
                        timestamp=int(ts),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=float(v),
                    ))

        return sorted(candles, key=lambda x: x.timestamp)

    async def subscribe_ticks(self, asset: str = "CRYPTO_IDX") -> AsyncIterator[Tick]:
        """
        Subscribe to real-time price ticks via WebSocket.

        Yields Tick objects as they arrive.
        """
        if not self._socket:
            raise RuntimeError("Not connected")

        topic = None
        # Try common topic patterns
        for t in [f"asset:{asset}", f"ticker:{asset}", f"price:{asset}", f"quote:{asset}",
                  f"candle:{asset}", f"chart:{asset}"]:
            try:
                resp = await self._socket.join(t, {})
                if isinstance(resp, dict) and resp.get("status") == "ok":
                    topic = t
                    break
            except Exception:
                continue
            await asyncio.sleep(0.2)

        if not topic:
            raise ValueError(f"Could not subscribe to {asset}")

        LOG.info("Subscribed to %s via topic %s", asset, topic)

        queue: asyncio.Queue = asyncio.Queue()

        def handler(payload, tpc, event):
            """Parse incoming tick/price data."""
            # Try to extract price from payload
            price = None
            ts = int(time.time() * 1000)
            direction = ""

            for price_key in ["price", "p", "last", "close", "c", "value", "rate", "mid"]:
                if price_key in payload:
                    try:
                        price = float(payload[price_key])
                    except (ValueError, TypeError):
                        continue
                    break

            if price is None and "bid" in payload and "ask" in payload:
                price = (float(payload["bid"]) + float(payload["ask"])) / 2

            if price is not None:
                if "direction" in payload:
                    direction = payload["direction"]
                queue.put_nowait(Tick(
                    asset=asset,
                    price=price,
                    timestamp=payload.get("timestamp", ts),
                    direction=direction,
                ))

        self._socket.on(topic, "*", handler)

        while self._socket._running:
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=30)
                yield tick
            except asyncio.TimeoutError:
                continue

    async def get_current_price(self, asset: str = "CRYPTO_IDX") -> Optional[float]:
        """Get current price by subscribing briefly."""
        async for tick in self.subscribe_ticks(asset):
            return tick.price
        return None


# ----- Signal engine for Stockity -----

def generate_stockity_signal(
    candles: list[Candle],
    min_candles: int = 30,
) -> dict:
    """
    Generate CALL/PUT/WAIT signal from Stockity candle data.
    Uses EMA9/EMA21 crossover + RSI14 + price position.
    """
    if len(candles) < min_candles:
        return {"action": "WAIT", "reason": f"Need {min_candles} candles, have {len(candles)}"}

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    price = closes[-1]

    # EMA
    def ema(values, span):
        k = 2 / (span + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)

    # RSI
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[-14:]) / 14 if gains else 0
    avg_loss = sum(losses[-14:]) / 14 if losses else 1
    rs = avg_gain / max(avg_loss, 0.001)
    rsi_val = 100 - (100 / (1 + rs))

    # Range position
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    range_pos = 50 if recent_high == recent_low else (price - recent_low) / (recent_high - recent_low) * 100

    score = 50
    reasons = []

    if ema9[-1] > ema21[-1]:
        score += 8
        reasons.append(f"EMA9 above EMA21")
    else:
        score -= 8
        reasons.append(f"EMA9 below EMA21")

    if price > ema50[-1]:
        score += 7
        reasons.append("price above EMA50")
    else:
        score -= 7
        reasons.append("price below EMA50")

    if len(ema9) >= 3 and len(ema21) >= 3:
        if ema9[-3] <= ema21[-3] and ema9[-1] > ema21[-1]:
            score += 10
            reasons.append("fresh bullish crossover")
        elif ema9[-3] >= ema21[-3] and ema9[-1] < ema21[-1]:
            score -= 10
            reasons.append("fresh bearish crossover")

    if rsi_val > 70:
        score -= 5
        reasons.append(f"RSI overbought ({rsi_val:.1f})")
    elif rsi_val < 30:
        score += 5
        reasons.append(f"RSI oversold ({rsi_val:.1f})")
    elif rsi_val > 55:
        score += 4
        reasons.append(f"RSI bullish ({rsi_val:.1f})")
    elif rsi_val < 45:
        score -= 4
        reasons.append(f"RSI bearish ({rsi_val:.1f})")
    else:
        reasons.append(f"RSI neutral ({rsi_val:.1f})")

    if range_pos > 85:
        score -= 4
        reasons.append("near high")
    elif range_pos < 15:
        score += 4
        reasons.append("near low")

    # For 5ST (5-second) trades, use mostly momentum
    recent_delta = closes[-1] - closes[-3] if len(closes) >= 3 else 0
    if recent_delta > 0:
        score += 3
        reasons.append("short-term up")
    elif recent_delta < 0:
        score -= 3
        reasons.append("short-term down")

    score = max(0, min(100, score))

    if score >= 60:
        return {"action": "CALL", "confidence": score, "price": price, "reason": "; ".join(reasons[-4:])}
    elif score <= 40:
        return {"action": "PUT", "confidence": 100 - score, "price": price, "reason": "; ".join(reasons[-4:])}
    else:
        return {"action": "WAIT", "confidence": max(score, 100 - score), "price": price, "reason": "; ".join(reasons[-4:])}
