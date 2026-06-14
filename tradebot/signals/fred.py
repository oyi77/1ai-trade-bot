"""
FRED (Federal Reserve Economic Data) source for bonds, rates, and economic data.

Free API key from https://fred.stlouisfed.org/docs/api/api_key.html (instant).
No rate limits documented. Covers US Treasuries, corporate bonds, mortgage rates,
and thousands of economic indicators.

Uses FRED series IDs mapped from common bond symbols.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource

LOG = logging.getLogger("tradebot.signals.fred")
BASE_URL = "https://api.stlouisfed.org/fred"

# Mapping from common bond/rate symbols → FRED series ID
# See: https://fred.stlouisfed.org/ for comprehensive list
FRED_SERIES_MAP: dict[str, str] = {
    # US Treasury Yields
    "UST1M": "DGS1MO",      # 1-Month Treasury Yield
    "UST3M": "DGS3MO",      # 3-Month
    "UST6M": "DGS6MO",      # 6-Month
    "UST1Y": "DGS1",        # 1-Year
    "UST2Y": "DGS2",        # 2-Year
    "UST3Y": "DGS3",        # 3-Year
    "UST5Y": "DGS5",        # 5-Year
    "UST7Y": "DGS7",        # 7-Year
    "UST10Y": "DGS10",      # 10-Year (benchmark)
    "UST20Y": "DGS20",      # 20-Year
    "UST30Y": "DGS30",      # 30-Year
    # Corporate Bonds
    "AAA": "AAA",            # Moody's Seasoned Aaa Corporate Bond Yield
    "BAA": "BAA",            # Moody's Seasoned Baa Corporate Bond Yield
    # Mortgage Rates
    "MORT30": "MORTGAGE30US",  # 30-Year Fixed Rate Mortgage Average
    "MORT15": "MORTGAGE15US",  # 15-Year Fixed Rate Mortgage
    # Fed Funds & Discount
    "FF": "FEDFUNDS",        # Federal Funds Effective Rate
    "PRIME": "DPRIME",        # Bank Prime Loan Rate
    "DISCOUNT": "DISCOUNT",    # Discount Rate
    "SOFR": "SOFR",           # Secured Overnight Financing Rate
    # Economic Indicators
    "CPI": "CPIAUCSL",        # Consumer Price Index
    "CPI_CORE": "CPILFESL",   # Core CPI (ex-food & energy)
    "UNEMP": "UNRATE",        # Unemployment Rate
    "GDP": "GDP",             # Gross Domestic Product
    "GDP_CORE": "GDPC1",      # Real GDP
    # Treasury Inflation-Protected (TIPS)
    "TIPS10": "DFII10",       # 10-Year TIPS Yield
    "TIPS5": "DFII5",         # 5-Year TIPS Yield
    # Other
    "TBILL_3M": "TB3MS",      # 3-Month Treasury Bill Secondary Market Rate
    "TBILL_6M": "TB6MS",      # 6-Month Treasury Bill
    "LIBOR1M": "USD1MTD156N", # 1-Month LIBOR
    "LIBOR3M": "USD3MTD156N", # 3-Month LIBOR
    "LIBOR6M": "USD6MTD156N", # 6-Month LIBOR
    # IDX-related (via Bank Indonesia reference rates)
    "BI_RATE": "IRSTCI01IDM086N",  # BI 7-Day Reverse Repo Rate
    "IDR10Y": "INTGSTIDM193N",     # Indonesia 10-Year Govt Bond Yield
    # Commodity currencies impact
    "WILL5000": "WILL5000IND",  # Wilshire 5000 Total Market Index
    "SP500": "SP500",           # S&P 500
}


def is_bond_symbol(symbol: str) -> bool:
    """Check if symbol maps to a FRED bond/rate series."""
    return symbol.upper().strip() in FRED_SERIES_MAP


class FREDSource(BaseDataSource):
    """Fetch bond yields, interest rates, and economic data from FRED API.

    Requires ``FRED_API_KEY`` in settings (free, instant registration at
    https://fred.stlouisfed.org/docs/api/api_key.html).
    Skips gracefully when key is empty or symbol is not a bond/rate series.

    FRED returns daily observations (not intraday OHLCV). The 'close'
    field contains the observation value, open/high/low are set to the
    same value. Volume is always 0.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "tradebot/1.0"},
            )
        return self._client

    async def fetch(
        self,
        symbol: str,
        interval: str = "1d",
        count: int = 100,
    ) -> list[OHLCV]:
        api_key = settings.FRED_API_KEY
        if not api_key:
            LOG.debug("FRED: no API key configured, skipping")
            return []

        series_id = FRED_SERIES_MAP.get(symbol.upper().strip())
        if not series_id:
            LOG.debug("FRED: no series mapping for %s", symbol)
            return []

        # FRED only supports daily data
        if interval not in ("1d", "1D", "daily"):
            LOG.debug("FRED: only daily data available, got %s", interval)
            return []

        client = await self._get_client()
        try:
            resp = await client.get(
                f"{BASE_URL}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": min(count * 2, 1000),
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            LOG.debug("FRED fetch failed for %s (series=%s): %s", symbol, series_id, exc)
            return []

        observations = data.get("observations", [])
        if not observations:
            LOG.debug("FRED returned no observations for %s", symbol)
            return []

        candles: list[OHLCV] = []
        for obs in observations:
            raw_val = obs.get("value", ".")
            if raw_val == ".":
                continue
            try:
                value = float(raw_val)
            except (ValueError, TypeError):
                continue

            try:
                dt = datetime.strptime(obs["date"], "%Y-%m-%d").replace(tzinfo=UTC)
            except (ValueError, KeyError):
                continue

            candles.append(OHLCV(
                timestamp=int(dt.timestamp()),
                open=value,
                high=value,
                low=value,
                close=value,
                volume=0,
                symbol=symbol,
            ))

        if not candles:
            return []

        candles.sort(key=lambda c: c.timestamp)
        if count and len(candles) > count:
            candles = candles[-count:]

        return candles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> FREDSource:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
