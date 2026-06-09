"""
Yahoo Finance market data source.

Wraps ``yfinance`` in an async interface via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging

import yfinance as yf  # type: ignore[import-untyped]

from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.yahoo")

# ── yfinance period → candle count guidelines ──
PERIOD_MAP: dict[str, str] = {
    "1m": "7d",
    "5m": "30d",
    "15m": "60d",
    "30m": "60d",
    "1h": "90d",
    "4h": "180d",
    "1d": "365d",
}


class YahooSource(BaseDataSource):
    """Fetch OHLCV data from Yahoo Finance via ``yfinance``.

    Because ``yfinance`` is a synchronous library the actual work is
    offloaded to a thread pool via ``asyncio.to_thread``.
    """

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch OHLCV candles from Yahoo Finance.

        Args:
            symbol: Yahoo ticker (e.g. ``"BTC-USD"``, ``"EURUSD=X"``).
            interval: Candle interval.
            count: Minimum number of candles requested (actual count
                depends on the period returned by Yahoo).

        Returns:
            List of :class:`OHLCV` candles, or empty list on error.
        """
        yf_period = PERIOD_MAP.get(interval, "7d")

        try:
            df = await asyncio.to_thread(
                yf.download,
                tickers=symbol,
                interval=interval,
                period=yf_period,
                progress=False,
                auto_adjust=True,
                threads=False,
            )
        except Exception as exc:
            LOG.warning("yfinance download failed for %s: %s", symbol, exc)
            return []

        if df is None or df.empty:
            LOG.debug("yfinance returned no data for %s", symbol)
            return []

        # Flatten MultiIndex columns if present
        if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
            df.columns = [c[0] for c in df.columns]

        # Ensure required columns exist
        required = {"Open", "High", "Low", "Close"}
        missing = required - set(df.columns)
        if missing:
            LOG.warning("yfinance data for %s missing columns: %s", symbol, missing)
            return []

        candles: list[OHLCV] = []
        for idx, row in df.iterrows():
            try:
                ts = int(idx.timestamp()) if hasattr(idx, "timestamp") else int(idx.tz_localize(None).timestamp())  # noqa: E501
                candles.append(
                    OHLCV(
                        timestamp=ts,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=int(float(row.get("Volume", 0))),
                        symbol=symbol,
                    )
                )
            except (ValueError, TypeError, AttributeError) as exc:
                LOG.debug("yfinance: skipping bad row %s — %s", idx, exc)
                continue

        # Return newest-first (most common expectation) — actually keep
        # chronological order as fetched.
        if count and len(candles) > count:
            candles = candles[-count:]

        return candles

    async def close(self) -> None:
        """No persistent connections to close for yfinance wrapper."""
        return None

    async def __aenter__(self) -> YahooSource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
