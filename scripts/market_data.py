#!/usr/bin/env python3
"""
UnifiedMarketData — single source of truth for price & OHLCV.
Uses Yahoo Finance (yfinance) as primary source.
Authoritative for: XAUUSD (GC=F), BTCUSD (BTC-USD), USOIL (CL=F), DXY (DX-Y.NYB).
"""

import time
import threading
from datetime import datetime, timezone, timedelta

import yfinance as yf


class Quote:
    """Lightweight quote object."""
    __slots__ = ('symbol', 'price', 'bid', 'ask', 'spread', 'timestamp', 'change_pct')

    def __init__(self, symbol, price: float, bid: float = 0, ask: float = 0, change_pct: float = 0, timestamp=None):
        self.symbol = symbol
        self.price = price
        self.bid = bid or price
        self.ask = ask or price
        self.spread = round(self.ask - self.bid, 2)
        self.change_pct = change_pct
        self.timestamp = timestamp or datetime.now(timezone.utc)


class OHLCVBar:
    """Lightweight OHLCV bar."""
    __slots__ = ('timestamp', 'open', 'high', 'low', 'close', 'volume')

    def __init__(self, timestamp, open_, high, low, close, volume):
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


class UnifiedMarketData:
    """
    Centralised market data provider for Vilona Trade FX.

    Maps internal pair names to Yahoo tickers.
    Caches quotes for 15s, OHLCV for 60s to respect rate limits.
    """

    SYMBOL_MAP = {
        # Forex
        "gold": "GC=F",
        "xauusd": "GC=F",
        "gld": "GC=F",
        "eurusd": "EURUSD=X",
        "gbpusd": "GBPUSD=X",
        "usdjpy": "JPY=X",
        "jpyusd": "JPY=X",
        "dxy": "DX-Y.NYB",
        "usdx": "DX-Y.NYB",
        # Crypto
        "btc": "BTC-USD",
        "btcusd": "BTC-USD",
        # Commodities
        "oil": "CL=F",
        "usoil": "CL=F",
        "wti": "CL=F",
        "brent": "BZ=F",
        "naturalgas": "NG=F",
        # US Stocks
        "aapl": "AAPL",
        "tsla": "TSLA",
        "msft": "MSFT",
        "googl": "GOOGL",
        "nvda": "NVDA",
        "amzn": "AMZN",
        "meta": "META",
        # IDX Stocks (Jakarta)
        "bbca": "BBCA.JK",
        "bbri": "BBRI.JK",
        "tlkm": "TLKM.JK",
        "asii": "ASII.JK",
        "unvr": "UNVR.JK",
        "adro": "ADRO.JK",
        "bmri": "BMRI.JK",
        "grm": "GGRM.JK",
        "icbp": "ICBP.JK",
        "inka": "INKA.JK",
        "pgas": "PGAS.JK",
        "ptba": "PTBA.JK",
        "smgr": "SMGR.JK",
        "tb": "TOBA.JK",
        # Indices
        "ihsg": "^JKSE",
        "spx": "^GSPC",
        "nasdaq": "^IXIC",
        "dji": "^DJI",
    }

    VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}

    def __init__(self):
        self._quote_cache: dict[str, tuple[float, Quote]] = {}
        self._ohlcv_cache: dict[str, tuple[float, list[OHLCVBar]]] = {}
        self._lock = threading.Lock()
        self._quote_ttl = 15   # seconds
        self._ohlcv_ttl = 60   # seconds

    # ── helpers ──────────────────────────────────────────────────────

    def _resolve(self, pair: str) -> str:
        """Map internal name → Yahoo ticker.  Case-insensitive, strips broker suffixes."""
        import re
        p = pair.lower().strip()
        # Strip broker suffixes: XAUUSDc → xauusd, EURUSD.pro → eurusd
        p = re.sub(r'[.\-#_].*$', '', p)
        p = re.sub(r'[cm]$', '', p)
        return self.SYMBOL_MAP.get(p, p.upper())

    def _fetch_ticker(self, yahoo_symbol: str):
        """Return yfinance Ticker object with 5s timeout."""
        try:
            tk = yf.Ticker(yahoo_symbol)
            # force a network round-trip
            _ = tk.fast_info
            return tk
        except Exception:
            return None

    # ── quote ────────────────────────────────────────────────────────

    def get_quote(self, pair: str, force: bool = False) -> Quote | None:
        """
        Return a Quote for *pair* (e.g. 'XAUUSD', 'BTCUSD').
        Cached for self._quote_ttl seconds unless force=True.
        """
        sym = self._resolve(pair)
        now = time.time()

        with self._lock:
            if not force and sym in self._quote_cache:
                ts, q = self._quote_cache[sym]
                if now - ts < self._quote_ttl:
                    return q

        q = self._fetch_quote(sym)
        if q is None:
            # return stale if available, else None
            with self._lock:
                if sym in self._quote_cache:
                    return self._quote_cache[sym][1]
            return None

        with self._lock:
            self._quote_cache[sym] = (now, q)
        return q

    def _fetch_quote(self, yahoo_symbol: str) -> Quote | None:
        """Raw network fetch for a single quote."""
        try:
            tk = self._fetch_ticker(yahoo_symbol)
            if tk is None:
                return None

            info = tk.fast_info
            price = info.get("lastPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            if not price:
                # fallback to history
                hist = tk.history(period="1d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])

            if not price or price <= 0:
                return None

            bid = info.get("bid") or price
            ask = info.get("ask") or price
            chg = info.get("regularMarketChangePercent") or 0

            return Quote(
                symbol=yahoo_symbol,
                price=round(float(price), 2),
                bid=round(float(bid), 2),
                ask=round(float(ask), 2),
                change_pct=round(float(chg), 4),
            )
        except Exception:
            return None

    # ── OHLCV ────────────────────────────────────────────────────────

    def get_ohlcv(
        self,
        pair: str,
        interval: str = "15m",
        count: int = 80,
        force: bool = False,
    ) -> list[OHLCVBar] | None:
        """
        Return latest *count* OHLCV bars for *pair*.
        Cached for self._ohlcv_ttl seconds unless force=True.
        """
        sym = self._resolve(pair)
        if interval not in self.VALID_INTERVALS:
            interval = "15m"
        cache_key = f"{sym}:{interval}"

        now = time.time()
        with self._lock:
            if not force and cache_key in self._ohlcv_cache:
                ts, bars = self._ohlcv_cache[cache_key]
                if now - ts < self._ohlcv_ttl:
                    return bars[-count:] if len(bars) > count else bars

        bars = self._fetch_ohlcv(sym, interval, count)
        if bars is None:
            with self._lock:
                if cache_key in self._ohlcv_cache:
                    old = self._ohlcv_cache[cache_key][1]
                    return old[-count:] if len(old) > count else old
            return None

        with self._lock:
            self._ohlcv_cache[cache_key] = (now, bars)
        return bars[-count:] if len(bars) > count else bars

    def _fetch_ohlcv(
        self, yahoo_symbol: str, interval: str, count: int
    ) -> list[OHLCVBar] | None:
        """Raw network fetch for OHLCV history."""
        try:
            # yfinance period strings
            period_map = {
                "1m": "7d", "5m": "30d", "15m": "60d",
                "30m": "60d", "1h": "90d", "4h": "180d", "1d": "365d",
            }
            period = period_map.get(interval, "60d")

            tk = yf.Ticker(yahoo_symbol)
            df = tk.history(period=period, interval=interval)
            if df.empty:
                return None

            bars = []
            for idx, row in df.iterrows():
                bars.append(OHLCVBar(
                    timestamp=idx,
                    open_=round(float(row["Open"]), 2),
                    high=round(float(row["High"]), 2),
                    low=round(float(row["Low"]), 2),
                    close=round(float(row["Close"]), 2),
                    volume=int(row.get("Volume", 0)),
                ))
            return bars
        except Exception:
            return None

    # ── convenience ──────────────────────────────────────────────────

    def price(self, pair: str) -> float | None:
        """Return just the mid-price (float or None)."""
        q = self.get_quote(pair)
        return q.price if q else None

    def get_bars_dicts(self, pair: str, interval: str = "15m", count: int = 20) -> list[dict]:
        """Return OHLCV as list of dicts (for prompt injection)."""
        bars = self.get_ohlcv(pair, interval, count)
        if not bars:
            return []
        return [
            {
                "timestamp": b.timestamp.isoformat() if hasattr(b.timestamp, "isoformat") else str(b.timestamp),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]


# ── singleton ────────────────────────────────────────────────────────
_market_instance: UnifiedMarketData | None = None
_instance_lock = threading.Lock()


def get_market() -> UnifiedMarketData:
    global _market_instance
    if _market_instance is None:
        with _instance_lock:
            if _market_instance is None:
                _market_instance = UnifiedMarketData()
    return _market_instance


# ── CLI test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    md = UnifiedMarketData()
    print("=== PRICE XAUUSD ===")
    q = md.get_quote("XAUUSD", force=True)
    if q:
        print(f"  Price: ${q.price}  Bid: ${q.bid}  Ask: ${q.ask}  Spread: ${q.spread}")
        print(f"  Change: {q.change_pct}%")
    else:
        print("  FAILED")

    print("\n=== OHLCV (10 bars, 15m) ===")
    bars = md.get_ohlcv("XAUUSD", "15m", 10, force=True)
    if bars:
        for b in bars:
            print(f"  {b.timestamp} O:{b.open} H:{b.high} L:{b.low} C:{b.close} V:{b.volume}")
    else:
        print("  FAILED")

    print(f"\nTotal bars: {len(bars) if bars else 0}")
