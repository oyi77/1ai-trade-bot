"""
market_data/__init__.py — Minimal working market data layer
===========================================================
Provides UnifiedMarketData with Yahoo Finance for gold (GC=F).
Simple, no circular imports, works immediately.
"""
import json, logging, time, urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger("market_data")

YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1m&range=1d"
YAHOO_OHLCV_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval={}&range={}"

# Cache to avoid rate limits
_quote_cache = {}
_ohlcv_cache = {}
_CACHE_TTL = 30  # seconds


@dataclass
class MarketQuote:
    symbol: str
    price: float
    change: float = 0.0
    change_pct: float = 0.0
    timestamp: float = 0.0
    
    @property
    def bid(self) -> float:
        return self.price
    
    @property
    def ask(self) -> float:
        return self.price
    
    @property
    def spread(self) -> float:
        return 0.0


class OHLCVBar:
    def __init__(self, timestamp, open, high, low, close, volume=0):
        self.timestamp = timestamp
        self.open = float(open)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)
        self.volume = float(volume)


class UnifiedMarketData:
    """Multi-asset market data with Yahoo Finance backend."""

    # Symbol mapping for common assets
    SYMBOL_MAP = {
        # Gold/Silver
        "XAUUSD": "GC=F", "XAU/USD": "GC=F", "gold": "GC=F", "GOLD": "GC=F",
        "silver": "SI=F", "SILVER": "SI=F",
        # Oil
        "oil": "CL=F", "OIL": "CL=F", "USOIL": "CL=F", "usoil": "CL=F",
        # Crypto
        "btc": "BTC-USD", "BTC": "BTC-USD", "BTCUSD": "BTC-USD", "btcusd": "BTC-USD",
        "eth": "ETH-USD", "ETH": "ETH-USD", "ETHUSD": "ETH-USD", "ethusd": "ETH-USD",
        # Forex Majors
        "eurusd": "EURUSD=X", "EURUSD": "EURUSD=X",
        "gbpusd": "GBPUSD=X", "GBPUSD": "GBPUSD=X",
        "usdjpy": "JPY=X", "USDJPY": "JPY=X",
        "usdcad": "CAD=X", "USDCAD": "CAD=X",
        "audusd": "AUDUSD=X", "AUDUSD": "AUDUSD=X",
        "nzdusd": "NZDUSD=X", "NZDUSD": "NZDUSD=X",
        # Indices
        "spx": "^GSPC", "dxy": "DX-Y.NYB", "DXY": "DX-Y.NYB",
    }

    def _resolve_symbol(self, symbol: str) -> str:
        """Case-insensitive symbol resolution."""
        sym_lower = symbol.lower().strip()
        return self.SYMBOL_MAP.get(sym_lower, self.SYMBOL_MAP.get(symbol, symbol))

    def get_quote(self, symbol: str) -> Optional[MarketQuote]:
        sym = self._resolve_symbol(symbol)
        now = time.time()

        # Cache hit
        if sym in _quote_cache and (now - _quote_cache[sym].timestamp) < _CACHE_TTL:
            return _quote_cache[sym]

        try:
            url = YAHOO_QUOTE_URL.format(sym)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = urllib.request.urlopen(req, timeout=10)
            data = json.loads(r.read())
            result = data["chart"]["result"][0]
            meta = result["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("previousClose", price)

            quote = MarketQuote(
                symbol=sym,
                price=float(price),
                change=float(price - prev) if prev else 0.0,
                change_pct=float((price - prev) / prev * 100) if prev and prev > 0 else 0.0,
                timestamp=now,
            )
            _quote_cache[sym] = quote
            return quote
        except Exception as e:
            logger.debug(f"Yahoo quote failed for {sym}: {e}")
            return None

    def get_ohlcv(self, symbol: str, interval: str = "1m", count: int = 100) -> List[OHLCVBar]:
        sym = self._resolve_symbol(symbol)
        cache_key = f"{sym}:{interval}:{count}"
        now = time.time()

        if cache_key in _ohlcv_cache and (now - _ohlcv_cache[cache_key][0]) < _CACHE_TTL:
            return _ohlcv_cache[cache_key][1]

        # Map interval to range
        range_map = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "30d", "1d": "90d"}
        yrange = range_map.get(interval, "5d")

        try:
            url = YAHOO_OHLCV_URL.format(sym, interval, yrange)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = urllib.request.urlopen(req, timeout=15)
            data = json.loads(r.read())
            result = data["chart"]["result"][0]
            timestamps = result.get("timestamp", [])
            ohlcv_data = result["indicators"]["quote"][0]

            bars = []
            for i in range(len(timestamps)):
                o = ohlcv_data["open"][i]
                h = ohlcv_data["high"][i]
                l = ohlcv_data["low"][i]
                c = ohlcv_data["close"][i]
                v = ohlcv_data.get("volume", [0] * len(timestamps))[i]
                if o is not None and h is not None and l is not None and c is not None:
                    bars.append(OHLCVBar(timestamps[i], o, h, l, c, v or 0))

            # Return last N bars
            bars = bars[-count:]
            _ohlcv_cache[cache_key] = (now, bars)
            return bars
        except Exception as e:
            logger.debug(f"Yahoo OHLCV failed for {sym}: {e}")
            return []


# Export
__all__ = ['UnifiedMarketData', 'MarketQuote', 'OHLCVBar']
