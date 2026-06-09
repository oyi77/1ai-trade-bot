"""
Stockity platform market data source.

Combines two data paths (reverse-engineered from HAR files):

1. REST (historical):
   GET https://api.stockity.com/candles/v1/{ric}/{time}/{seconds}?locale=en
   Used by fetch() — returns full historical candle data for a day.

2. WebSocket (real-time):
   wss://as.stockity.com/ + {"action": "subscribe", "rics": [...]}
   Used by stream() — streams live ticks with ask/bid prices.

Auth: Requires STOCKITY_FULL_COOKIE (browser session cookie) in .env.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote as url_quote

import httpx
import websockets

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.stockity")

STOCKITY_WS_URL = "wss://as.stockity.com/"
CANDLE_API = "https://api.stockity.com/candles/v1/{ric}/{time}/{seconds}"

# RIC mappings for Stockity.
# CRYPTO_IDX is VERIFIED via both REST and WS HAR analysis.
# Others are best-guess following the Z-{SYMBOL}/IDX pattern.
RIC_MAP: dict[str, str] = {
    "CRYPTO_IDX": "Z-CRY/IDX",  # VERIFIED
    "BTC_IDX": "Z-BTC/IDX",      # UNVERIFIED
    "ETH_IDX": "Z-ETH/IDX",      # UNVERIFIED
    "GOLD_IDX": "Z-GOLD/IDX",    # UNVERIFIED
}

PLATFORM_ASSETS: set[str] = set(RIC_MAP.keys())

DEFAULT_CANDLE_SECONDS = 60  # 1-minute candles


class StockitySource(BaseDataSource):
    """Fetch OHLCV from Stockity (REST) + stream live ticks (WebSocket).

    Requires STOCKITY_FULL_COOKIE (browser session cookie) in .env.

    Two data paths:
        fetch()    — historical candles via REST
        stream()   — real-time ticks via WebSocket (async generator)
    """

    def __init__(self) -> None:
        self._cookie: str = settings.STOCKITY_FULL_COOKIE
        self._http: httpx.AsyncClient | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None

    # ── HTTP client (REST) ──────────────────────────────────────────────

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            headers = {
                "Origin": "https://stockity.com",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            }
            if self._cookie:
                headers["Cookie"] = self._cookie
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0), headers=headers)
        return self._http

    # ── WebSocket client (real-time) ───────────────────────────────────

    async def _connect_ws(self) -> websockets.WebSocketClientProtocol:
        """Connect to Stockity WebSocket for real-time ticks."""
        if self._ws is not None:
            from websockets.protocol import State
            if self._ws.state == State.OPEN:
                return self._ws

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
        LOG.info("Connecting to %s", STOCKITY_WS_URL)
        self._ws = await websockets.connect(STOCKITY_WS_URL, additional_headers=headers)
        LOG.info("✓ Stockity WebSocket connected")
        return self._ws

    async def _subscribe_ws(self, rics: list[str]) -> None:
        """Subscribe to asset RICs on the WebSocket."""
        ws = await self._connect_ws()
        await ws.send(json.dumps({"action": "subscribe", "rics": rics}))
        LOG.info("Subscribed to RICs: %s", rics)

    # ── Public API: historical (REST) ──────────────────────────────────

    def _compute_time_param(self, target_date: datetime | None = None) -> str:
        """Time parameter for candle API (ISO 8601, midnight UTC)."""
        if target_date is None:
            target_date = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return target_date.strftime("%Y-%m-%dT%H:%M:%S")

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch historical OHLCV candles via REST.

        Args:
            symbol: Platform asset (e.g. ``"CRYPTO_IDX"``).
            interval: "1m" (60s) or "1s" (1s).
            count: Max candles to return (most recent N).

        Returns:
            List of :class:`OHLCV` candles.
        """
        sym_upper = symbol.upper()

        if sym_upper not in RIC_MAP:
            LOG.warning(
                f"Stockity: symbol {symbol} not in RIC_MAP. "
                f"Supported: {list(RIC_MAP.keys())}"
            )
            return []

        if not self._cookie:
            LOG.warning("Stockity: no auth (set STOCKITY_FULL_COOKIE in .env)")
            return []

        ric = RIC_MAP[sym_upper]
        encoded_ric = url_quote(ric, safe="")
        seconds = 1 if interval == "1s" else DEFAULT_CANDLE_SECONDS
        time_param = self._compute_time_param()
        url = f"{CANDLE_API.format(ric=encoded_ric, time=time_param, seconds=seconds)}?locale=en"

        client = await self._get_http()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            LOG.warning("Stockity HTTP fetch failed for %s: %s", ric, exc)
            return []
        except Exception as exc:
            LOG.error("Stockity fetch error for %s: %s", ric, exc)
            return []

        if not data.get("success"):
            errors = data.get("errors", [])
            LOG.warning(f"Stockity API error for {ric}: {errors}")
            return []

        raw_candles = data.get("data", [])
        if not raw_candles:
            LOG.debug("Stockity: no candle data for %s", ric)
            return []

        result: list[OHLCV] = []
        for item in raw_candles:
            try:
                ts_str = item.get("created_at", "")
                if not ts_str:
                    continue
                ts = int(
                    datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                )
                result.append(
                    OHLCV(
                        timestamp=ts,
                        open=float(item["open"]),
                        high=float(item["high"]),
                        low=float(item["low"]),
                        close=float(item["close"]),
                        volume=0,
                        symbol=sym_upper,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                LOG.debug("Stockity: skipping bad candle %s — %s", item, exc)

        result.sort(key=lambda c: c.timestamp)
        LOG.info(
            "Stockity: got %d candles for %s (%s, %ds)",
            len(result), sym_upper, ric, seconds,
        )
        return result[-count:] if len(result) > count else result

    # ── Public API: real-time (WebSocket) ──────────────────────────────

    async def stream(
        self,
        symbol: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream real-time ticks via WebSocket.

        Args:
            symbol: Platform asset (e.g. ``"CRYPTO_IDX"``).

        Yields:
            Tick dicts with keys: ric, ask, bid, rate, created_at.
        """
        sym_upper = symbol.upper()
        if sym_upper not in RIC_MAP:
            LOG.warning(f"Stockity stream: symbol {symbol} not in RIC_MAP")
            return
        if not self._cookie:
            LOG.warning("Stockity stream: no auth (set STOCKITY_FULL_COOKIE in .env)")
            return

        ric = RIC_MAP[sym_upper]
        await self._subscribe_ws([ric])

        ws = await self._connect_ws()
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for item in data.get("data", []):
                if "assets" in item:
                    for asset in item["assets"]:
                        yield asset

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _aggregate_candles(raw: list[OHLCV], period_s: int = 60) -> list[OHLCV]:
        """Aggregate candles into larger timeframes."""
        if not raw:
            return []
        buckets: dict[int, list[OHLCV]] = {}
        for c in raw:
            bucket = (c.timestamp // period_s) * period_s
            buckets.setdefault(bucket, []).append(c)
        return [
            OHLCV(
                timestamp=ts,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=0,
                symbol=raw[0].symbol if raw else "",
            )
            for ts in sorted(buckets.keys())
            for group in [buckets[ts]]
        ]

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> StockitySource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
