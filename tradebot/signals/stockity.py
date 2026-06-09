"""
Stockity platform market data source.

Fetches 1-second candles via Stockity's public REST API (not WebSocket)
and aggregates them into standard timeframes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote as url_quote

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.stockity")

CANDLE_API = "https://api.stockity.com/candles/v1/{ric}/{time}/1"

# RIC mappings for Stockity API
# WARNING: Only CRYPTO_IDX is tested. Others may fail silently.
RIC_MAP: dict[str, str] = {
    "CRYPTO_IDX": "Z-CRY/IDX",  # Works ✓
    "BTC_IDX": "BTC_IDX",        # Untested
    "ETH_IDX": "ETH_IDX",        # Untested
    "GOLD_IDX": "GOLD_IDX",      # Untested
}

PLATFORM_ASSETS: set[str] = {"CRYPTO_IDX"}

DEFAULT_AGGR_SECONDS = 60  # aggregate 1s → 1m candles


class StockitySource(BaseDataSource):
    """Fetch OHLCV data from Stockity platform via its HTTP REST API.

    Requires either ``STOCKITY_AUTHTOKEN`` (Bearer token) or
    ``STOCKITY_FULL_COOKIE`` set in environment / .env.

    The API returns 1-second candles that are aggregated into the
    requested interval within this class.
    """

    def __init__(self) -> None:
        self._authtoken: str = settings.STOCKITY_AUTHTOKEN
        self._cookie: str = settings.STOCKITY_FULL_COOKIE
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Origin": "https://stockity.com",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            }
            if self._cookie:
                headers["Cookie"] = self._cookie
            elif self._authtoken:
                headers["Authorization"] = f"Bearer {self._authtoken}"

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers=headers,
            )
    def _compute_time_param(self, target_minute: int = 15) -> str:
        """Compute time parameter for Stockity API in ISO 8601 format.

        Stockity accepts ISO 8601 UTC timestamps like '2024-01-01T00:00:00Z'.
        """
        # Stockity expects ISO 8601 UTC format
        from datetime import datetime, timedelta, timezone
        target_time = datetime.now(timezone.utc) - timedelta(minutes=target_minute)
        return target_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _aggregate_candles(raw: list[OHLCV], period_s: int = 60) -> list[OHLCV]:
        """Aggregate 1-second candles into larger timeframes.

        Args:
            raw: List of 1-second :class:`OHLCV` candles.
            period_s: Output candle period in seconds (default 60 = 1m).

        Returns:
            Aggregated candles sorted by timestamp ascending.
        """
        if not raw:
            return []

        buckets: dict[int, list[OHLCV]] = {}
        for c in raw:
            bucket = (c.timestamp // (period_s)) * (period_s)
            if bucket not in buckets:
                buckets[bucket] = []
            buckets[bucket].append(c)

        result: list[OHLCV] = []
        for ts in sorted(buckets.keys()):
            group = buckets[ts]
            result.append(
                OHLCV(
                    timestamp=ts,
                    open=group[0].open,
                    high=max(c.high for c in group),
                    low=min(c.low for c in group),
                    close=group[-1].close,
                    volume=0,
                    symbol=raw[0].symbol if raw else "",
                )
            )
        return result

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch OHLCV data for a Stockity platform asset.

        Args:
            symbol: Platform asset (e.g. ``"CRYPTO_IDX"``, ``"BTC_IDX"``).
            interval: Ignored — Stockity only provides 1s candles which
                are aggregated to 1m.  Kept for API compatibility.
            count: Not directly supported — returns all available
                aggregated candles (typically 15-60 of them).

        Returns:
            List of :class:`OHLCV` candles, or empty list on error / no auth.
        """
        sym_upper = symbol.upper()

        # Check if we have auth
        if not self._authtoken and not self._cookie:
            LOG.warning(
                "Stockity: no auth configured (set STOCKITY_AUTHTOKEN "
                "or STOCKITY_FULL_COOKIE)"
            )
            return []

        # Validate symbol is supported
        if sym_upper not in RIC_MAP:
            LOG.warning(
                f"Stockity: symbol {symbol} not in RIC_MAP. "
                f"Supported: {list(RIC_MAP.keys())}"
            )
            return []

        # Map symbol to RIC
        ric = RIC_MAP[sym_upper]

        # Build API URL
        time_param = self._compute_time_param(target_minute=15)
        encoded_ric = url_quote(ric, safe="")
        url = CANDLE_API.format(ric=encoded_ric, time=time_param)

        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            LOG.warning("Stockity HTTP fetch failed for %s: %s", ric, exc)
            return []

        raw_candles = data.get("data", [])
        if not raw_candles:
            LOG.debug("Stockity: no candle data for %s", ric)
            return []

        raw_1s: list[OHLCV] = []
        now_ts = int(datetime.now(UTC).timestamp())
        for item in raw_candles:
            try:
                ts_str = item.get("created_at", "")
                if ts_str:
                    ts = int(
                        datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    )
                else:
                    ts = now_ts

                raw_1s.append(
                    OHLCV(
                        timestamp=ts,
                        open=float(item["open"]),
                        high=float(item["high"]),
                        low=float(item["low"]),
                        close=float(item["close"]),
                        volume=0,
                        symbol=symbol,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                LOG.debug("Stockity: skipping bad candle %s — %s", item, exc)

        if not raw_1s:
            return []

        raw_1s.sort(key=lambda c: c.timestamp)
        LOG.info(
            "Stockity: got %d raw 1s candles for %s (%s)",
            len(raw_1s),
            symbol,
            ric,
        )

        # Aggregate to 1-minute candles
        aggregated = self._aggregate_candles(raw_1s, period_s=DEFAULT_AGGR_SECONDS)
        LOG.info("Stockity: aggregated into %d 1m candles", len(aggregated))

        return aggregated

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> StockitySource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
