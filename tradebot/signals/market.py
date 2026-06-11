"""
Unified market data aggregation and multi-source fallback chain.

Provides :class:`MarketAggregator` (combines all data sources with
priority ordering) and :class:`FallbackChain` (multi-source price
fallback for specific assets using free APIs).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from tradebot.config import settings
from tradebot.models.market import OHLCV

from .base import BaseDataSource
from .binance import BinanceSource
from .ccxt_source import CCXTSource
from .deriv_source import DerivSource, is_deriv_symbol
from .forex import ForexSource
from .mt5_source import MT5Source
from .stockity import StockitySource
from .yahoo import YahooSource

LOG = logging.getLogger("tradebot.signals.market")

# ── Crypto symbols → Binance is preferred ──
CRYPTO_SYMBOLS: set[str] = {
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "DOGE-USD", "DOT-USD", "MATIC-USD", "AVAX-USD",
    "LINK-USD", "UNI-USD", "ATOM-USD", "LTC-USD", "BCH-USD",
}

# ── Forex prefixes ──
FOREX_PREFIXES: tuple[str, ...] = ("EUR", "GBP", "USD", "JPY", "CHF", "AUD", "CAD", "NZD")

# ── Stockity platform assets ──
PLATFORM_ASSETS: set[str] = {"CRYPTO_IDX", "BTC_IDX", "ETH_IDX", "GOLD_IDX"}

# ── FallbackChain API URLs ──
CURRENCY_API = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json"
COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price?ids={id}&vs_currencies=usd"
FCS_BASE = "https://api-v4.fcsapi.com/forex"

UA = "tradebot/1.0"


# ═════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════


def _is_forex(symbol: str) -> bool:
    """Check if *symbol* looks like a forex pair (e.g. ``EURUSD=X``)."""
    s = symbol.upper().rstrip("=X")
    return s.startswith(FOREX_PREFIXES) and "=" in symbol


def _normalise_symbol(symbol: str) -> str:
    """Normalise symbol to upper case for lookup tables."""
    return symbol.upper().replace("/", "-")


# ═════════════════════════════════════════════════════════════════════
#  MarketAggregator
# ═════════════════════════════════════════════════════════════════════


class MarketAggregator:
    """Unified market data aggregator with intelligent source selection.

    Routes each symbol to the best available data source:

    * **Deriv** for synthetic indices (``R_75``, ``1HZ10V``, …).
    * **Stockity** for platform assets (``CRYPTO_IDX``, …).
    * **Binance/CCXT** for crypto symbols (``BTC-USD``, ``ETH-USD``, …).
    * **ForexSource** for forex pairs (``EURUSD=X``, …).
    * **Yahoo** for everything else (stocks, indices, commodities, …).
    * **MT5** for symbols available in MetaTrader 5.

    Each source is tried in priority order with automatic fallback
    to the next available source on failure.
    """

    def __init__(self) -> None:
        self._binance = BinanceSource()
        self._yahoo = YahooSource()
        self._forex = ForexSource()
        self._stockity = StockitySource()
        self._ccxt = CCXTSource()
        self._deriv = DerivSource()
        self._mt5 = MT5Source()
        self._http: httpx.AsyncClient | None = None

        # Cache for source resolution
        self._symbol_cache: dict[str, type[BaseDataSource]] = {}

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": UA},
            )
        return self._http

    def _select_sources(self, symbol: str) -> list[BaseDataSource]:
        """Return data sources to try, in priority order."""
        sym = _normalise_symbol(symbol)

        # Deriv synthetic indices → Deriv only
        if is_deriv_symbol(sym):
            return [self._deriv]

        # Platform assets → Stockity only
        if sym in PLATFORM_ASSETS or sym.startswith("CRYPTO"):
            return [self._stockity]

        # Crypto → CCXT → Binance → Yahoo → MT5
        if sym in CRYPTO_SYMBOLS:
            return [self._ccxt, self._binance, self._yahoo, self._mt5]

        # Forex → ForexSource → Yahoo
        if _is_forex(sym):
            return [self._forex, self._yahoo]

        # Commodities (XAUUSD, USOIL) → MT5 → Yahoo
        if sym in ("XAUUSD", "USOIL", "BTCUSD", "ETHUSD"):
            return [self._mt5, self._yahoo]

        # Everything else → Yahoo
        return [self._yahoo]

    @staticmethod
    def _resolve_alias(symbol: str) -> str:
        """Map user-friendly names to standard symbols.

        Handles broker suffixes (XAUUSDc → XAUUSD), common aliases
        (gold → XAUUSD, btc → BTCUSD), and IDX stocks.
        """
        import re
        _ALIAS_MAP: dict[str, str] = {  # noqa: N806
            # Forex
            "gold": "XAUUSD", "xauusd": "XAUUSD", "gld": "XAUUSD",
            "eurusd": "EURUSD", "gbpusd": "GBPUSD",
            "usdjpy": "USDJPY", "jpyusd": "USDJPY",
            "dxy": "DXY", "usdx": "DXY",
            # Crypto
            "btc": "BTCUSD", "btcusd": "BTCUSD",
            "eth": "ETHUSD", "ethusd": "ETHUSD",
            # Commodities
            "oil": "USOIL", "usoil": "USOIL", "wti": "USOIL",
            "brent": "BRENT", "naturalgas": "NATGAS",
            # US Stocks
            "aapl": "AAPL", "tsla": "TSLA", "msft": "MSFT",
            "googl": "GOOGL", "nvda": "NVDA", "amzn": "AMZN", "meta": "META",
            # IDX Stocks
            "bbca": "BBCA.JK", "bbri": "BBRI.JK", "tlkm": "TLKM.JK",
            "asii": "ASII.JK", "unvr": "UNVR.JK", "adro": "ADRO.JK",
            "bmri": "BMRI.JK", "grm": "GGRM.JK", "icbp": "ICBP.JK",
            "inka": "INKA.JK", "pgas": "PGAS.JK", "ptba": "PTBA.JK",
            "smgr": "SMGR.JK", "tb": "TOBA.JK",
            # Indices
            "ihsg": "IHSG", "spx": "SPX", "nasdaq": "NAS100", "dji": "DJI",
        }
        p = symbol.lower().strip()
        # Strip broker suffixes: XAUUSDc → xauusd, EURUSD.pro → eurusd
        stripped = re.sub(r'[.\-#_].*$', '', p)
        stripped = re.sub(r'[cm]$', '', stripped)
        return _ALIAS_MAP.get(p, _ALIAS_MAP.get(stripped, symbol.upper()))

    async def fetch(
        self,
        symbol: str,
        interval: str = "1m",
        count: int = 100,
    ) -> list[OHLCV]:
        """Fetch OHLCV candles using the best available source.

        Tries each candidate source in priority order.  Returns the
        first non-empty result, or an empty list if all sources fail.

        Args:
            symbol: Ticker or pair name.
            interval: Candle interval.
            count: Max candles requested.

        Returns:
            List of :class:`OHLCV` candles, or empty list.
        """
        sources = self._select_sources(symbol)
        last_error: str | None = None

        for source in sources:
            source_name = type(source).__name__
            try:
                candles = await source.fetch(symbol, interval=interval, count=count)
                if candles:
                    LOG.debug(
                        "%s returned %d candles for %s",
                        source_name,
                        len(candles),
                        symbol,
                    )
                    return candles
                LOG.debug("%s returned empty for %s", source_name, symbol)
            except Exception as exc:
                last_error = str(exc)
                LOG.warning(
                    "%s failed for %s: %s", source_name, symbol, exc
                )

        LOG.warning(
            "All sources failed for %s (last error: %s)",
            symbol,
            last_error or "empty data",
        )
        return []

    async def price(self, symbol: str) -> float | None:
        """Fetch the latest price (close) for *symbol*."""
        candles = await self.fetch(symbol, interval="1m", count=1)
        if candles:
            return candles[-1].close
        return None

    async def close(self) -> None:
        """Close all underlying data sources and HTTP client."""
        await self._binance.close()
        await self._yahoo.close()
        await self._forex.close()
        await self._stockity.close()
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> MarketAggregator:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


# ═════════════════════════════════════════════════════════════════════
#  FallbackChain
# ═════════════════════════════════════════════════════════════════════


class FallbackChain:
    """Multi-source price fallback for specific assets.

    Tries specialised free APIs in order, falling back to Yahoo when
    dedicated sources fail.  Useful as a last-resort price provider
    when the primary aggregator returns empty.

    Priority per asset::

        XAUUSD  → currency-api → Yahoo (GC=F)
        BTCUSD  → CoinGecko → Yahoo
        ETHUSD  → CoinGecko → Yahoo
        EURUSD  → currency-api → Yahoo
        GBPUSD  → currency-api → Yahoo
        others  → Yahoo → FCS API (if configured)

    All calls are async with timeouts; no API keys needed for the
    free sources (except FCS which needs ``FCS_API_KEY``).
    """

    CURRENCY_ASSETS: set[str] = {"XAUUSD", "GOLD", "EURUSD", "GBPUSD", "XAGUSD"}
    CRYPTO_ASSETS: set[str] = {"BTCUSD", "ETHUSD"}

    def __init__(self) -> None:
        self._aggregator = MarketAggregator()
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                headers={"User-Agent": UA},
            )
        return self._http

    async def fetch_price(self, symbol: str) -> float | None:
        """Fetch the latest price for *symbol* using fallback chain.

        Args:
            symbol: Normalised asset name (e.g. ``"XAUUSD"``, ``"BTCUSD"``).

        Returns:
            Latest price as float, or ``None`` if all sources fail.
        """
        sym = _normalise_symbol(symbol)

        # 1. Specialised free APIs
        price: float | None = None

        if sym in self.CURRENCY_ASSETS:
            price = await self._fetch_currency_api(sym)

        if price is None and sym in self.CRYPTO_ASSETS:
            price = await self._fetch_coingecko(sym)

        # 2. Yahoo via aggregator
        if price is None:
            yahoo_sym = self._to_yahoo_symbol(sym)
            price = await self._aggregator.price(yahoo_sym)

        # 3. FCS API (if key configured)
        if price is None and settings.FCS_API_KEY:
            price = await self._fetch_fcs(sym)

        return price

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str = "15m",
        count: int = 20,
    ) -> list[OHLCV]:
        """Fetch OHLCV candles via the fallback chain.

        Delegates to the :class:`MarketAggregator` (which handles
        source selection) after mapping the symbol appropriately.
        """
        sym = _normalise_symbol(symbol)
        yahoo_sym = self._to_yahoo_symbol(sym)
        return await self._aggregator.fetch(yahoo_sym, interval=interval, count=count)

    # ── specialised API fetchers ──────────────────────────────────

    async def _fetch_currency_api(self, symbol: str) -> float | None:
        """Fetch price from jsdelivr currency API (free, no key)."""
        base_map = {
            "XAUUSD": "xau", "GOLD": "xau", "XAGUSD": "xag",
            "EURUSD": "eur", "GBPUSD": "gbp",
        }
        base = base_map.get(symbol)
        if base is None:
            return None

        url = CURRENCY_API.format(base=base)
        client = await self._get_http()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            usd = data.get(base, {}).get("usd", 0)
            if usd and usd > (100 if symbol in ("XAUUSD", "GOLD") else 0):
                return float(usd)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            LOG.debug("currency-api failed for %s: %s", symbol, exc)
        return None

    async def _fetch_coingecko(self, symbol: str) -> float | None:
        """Fetch price from CoinGecko (free, no key)."""
        coin_map = {"BTCUSD": "bitcoin", "ETHUSD": "ethereum"}
        coin = coin_map.get(symbol)
        if coin is None:
            return None

        url = COINGECKO_API.format(id=coin)
        client = await self._get_http()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            usd = data.get(coin, {}).get("usd", 0)
            if usd and usd > 0:
                return float(usd)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            LOG.debug("CoinGecko failed for %s: %s", symbol, exc)
        return None

    async def _fetch_fcs(self, symbol: str) -> float | None:
        """Fetch price from FCS API v4 (requires FCS_API_KEY)."""
        if not settings.FCS_API_KEY:
            return None

        # Determine asset type
        if symbol in ("XAUUSD", "USOIL"):
            asset_type = "commodity"
        elif symbol in ("BTCUSD", "ETHUSD"):
            asset_type = "crypto"
        else:
            asset_type = "forex"

        client = await self._get_http()
        try:
            resp = await client.get(
                f"{FCS_BASE}/latest",
                params={
                    "symbol": symbol,
                    "type": asset_type,
                    "access_key": settings.FCS_API_KEY,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 200 and data.get("status") is True:
                response = data.get("response", [])
                if isinstance(response, list) and response:
                    active = response[0].get("active", {})
                    price = float(active.get("c", 0))
                    if price > 0:
                        return price
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            LOG.debug("FCS API failed for %s: %s", symbol, exc)
        return None

    # ── symbol mapping ───────────────────────────────────────────

    @staticmethod
    def _to_yahoo_symbol(symbol: str) -> str:
        """Map internal names to Yahoo Finance tickers."""
        sym_map: dict[str, str] = {
            "XAUUSD": "GC=F",
            "GOLD": "GC=F",
            "XAUUSD_SPOT": "XAUUSD_SPOT",
            "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X",
            "USDJPY": "JPY=X",
            "DXY": "DX-Y.NYB",
            "USDX": "DX-Y.NYB",
            "BTCUSD": "BTC-USD",
            "ETHUSD": "ETH-USD",
            "USOIL": "CL=F",
            "WTI": "CL=F",
            "BRENT": "BZ=F",
            "SPX": "^GSPC",
            "NAS100": "^NDX",
            "IHSG": "^JKSE",
            "BBCA.JK": "BBCA.JK",
            "BBRI.JK": "BBRI.JK",
            "TLKM.JK": "TLKM.JK",
            "ASII.JK": "ASII.JK",
            "UNVR.JK": "UNVR.JK",
            "ADRO.JK": "ADRO.JK",
            "BMRI.JK": "BMRI.JK",
            "GGRM.JK": "GGRM.JK",
            "ICBP.JK": "ICBP.JK",
            "INKA.JK": "INKA.JK",
            "PGAS.JK": "PGAS.JK",
            "PTBA.JK": "PTBA.JK",
            "SMGR.JK": "SMGR.JK",
            "TOBA.JK": "TOBA.JK",
            "AAPL": "AAPL",
            "TSLA": "TSLA",
            "MSFT": "MSFT",
            "GOOGL": "GOOGL",
            "NVDA": "NVDA",
            "AMZN": "AMZN",
            "META": "META",
            "DJI": "^DJI",
            "NATGAS": "NG=F",
            "SILVER": "SI=F",
            "XAGUSD": "SI=F",
        }
        return sym_map.get(symbol.upper(), symbol)

    async def close(self) -> None:
        await self._aggregator.close()
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> FallbackChain:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
