"""
Data provider port — defines the contract for market-data sources.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IDataProvider(ABC):
    """Abstract interface for market-data providers.

    Adapters may pull data from REST APIs, websockets, on-chain RPCs,
    or local caches — the core doesn't care.
    """

    @abstractmethod
    async def get_token_price(
        self, token: str, vs_currency: str = "usd"
    ) -> float:
        """Get the current spot price of a token.

        Args:
            token:       Token symbol or contract address.
            vs_currency: Quote currency (default ``"usd"``).

        Returns:
            Current price as a float.
        """

    @abstractmethod
    async def get_token_info(self, token: str) -> Dict[str, Any]:
        """Fetch metadata about a token (name, decimals, chain, …).

        Args:
            token: Token symbol or contract address.

        Returns:
            Dict of token metadata.
        """

    @abstractmethod
    async def get_market_data(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch historical OHLCV / candle data.

        Args:
            symbol:    Trading pair, e.g. "BTC/USDT".
            timeframe: Candle period, e.g. "1m", "5m", "1h", "1d".
            limit:     Max number of candles.

        Returns:
            List of candle dicts with at least
            ``timestamp, open, high, low, close, volume``.
        """

    @abstractmethod
    async def get_trending_tokens(
        self, chain: str = "", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Return currently-trending tokens.

        Args:
            chain: Filter by chain id / name.  Empty = all.
            limit: Max results.

        Returns:
            List of token info dicts, ordered by trending score.
        """
