"""
Data Fetcher — Hybrid Decision Engine
======================================
Multi-source OHLCV data pipeline with fallback chain:

  Priority 1: MT5 Bridge Daemon (zero-drift, broker-synced)
  Priority 2: ccxt (Binance — crypto only, real-time)
  Priority 3: yfinance (XAU/forex fallback, delayed)

Features:
  - In-memory cache with configurable TTL
  - Atomic CSV write (write-temp → rename = no partial reads)
  - Thread-safe reads via threading.Lock
  - Live price endpoint (current bid/ask)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import config

logger = logging.getLogger("hybrid.fetcher")

# ── Timezone ───────────────────────────────────────────────────────
WIB = timezone(timedelta(hours=7))


# ═══════════════════════════════════════════════════════════════════
#  IN-MEMORY CACHE
# ═══════════════════════════════════════════════════════════════════

class _CacheEntry:
    __slots__ = ("data", "timestamp")

    def __init__(self, data: pd.DataFrame, timestamp: float):
        self.data = data
        self.timestamp = timestamp

    @property
    def age(self) -> float:
        return time.time() - self.timestamp

    @property
    def is_fresh(self) -> bool:
        return self.age < config.OHLCV_CACHE_TTL


_ohlcv_cache: dict[str, _CacheEntry] = {}
_price_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()


def _cache_key(symbol: str, timeframe: str) -> str:
    return f"{symbol.upper()}_{timeframe.upper()}"


# ═══════════════════════════════════════════════════════════════════
#  SOURCE 1: MT5 BRIDGE DAEMON (primary)
# ═══════════════════════════════════════════════════════════════════

def _fetch_from_bridge(symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from MT5 Bridge daemon.
    The bridge exposes trade-status and signal endpoints.
    For OHLCV history, we query the bridge's daemon which proxies MT5.

    Returns DataFrame or None if unavailable.
    """
    try:
        # Bridge endpoint for OHLCV data (daemon-proxied MT5 history)
        url = (
            f"{config.BRIDGE_URL}/ohlcv"
            f"?symbol={symbol}&timeframe={timeframe}&limit={limit}"
        )
        if config.BRIDGE_API_KEY:
            url += f"&api_key={config.BRIDGE_API_KEY}"

        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=config.TIMEOUT_DATA_FETCH) as resp:
            raw = json.loads(resp.read().decode())

        if not raw or "candles" not in raw:
            logger.debug("Bridge OHLCV: no candles for %s %s", symbol, timeframe)
            return None

        candles = raw["candles"]
        if not candles:
            return None

        df = pd.DataFrame(candles)
        # Normalize column names
        col_map = {"time": "timestamp", "open": "open", "high": "high",
                    "low": "low", "close": "close", "vol": "volume"}
        df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)

        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.warning("Bridge OHLCV: missing columns %s", required - set(df.columns))
            return None

        logger.info("✅ Bridge OHLCV: %d candles for %s %s", len(df), symbol, timeframe)
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        logger.debug("Bridge OHLCV unavailable: %s", e)
        return None
    except Exception as e:
        logger.warning("Bridge OHLCV error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
#  SOURCE 2: CCXT (Binance — crypto)
# ═══════════════════════════════════════════════════════════════════

# Lazy import to avoid loading ccxt unless needed
_ccxt_exchange = None


def _get_ccxt_exchange():
    global _ccxt_exchange
    if _ccxt_exchange is None:
        try:
            import ccxt
            _ccxt_exchange = ccxt.binance({
                "enableRateLimit": True,
                "timeout": config.TIMEOUT_DATA_FETCH * 1000,
                "options": {"defaultType": "spot"},
            })
        except ImportError:
            logger.warning("ccxt not installed — crypto fallback unavailable")
            return None
    return _ccxt_exchange


# Symbol mapping: our symbol → ccxt symbol
_CCXT_SYMBOL_MAP = {
    "BTCUSD": "BTC/USDT",
    "BTCUSDT": "BTC/USDT",
    "ETHUSD": "ETH/USDT",
    "ETHUSDT": "ETH/USDT",
}

_CCXT_TIMEFRAME_MAP = {
    "M1": "1m", "M5": "5m", "M15": "15m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}


def _fetch_from_ccxt(symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Binance via ccxt (crypto only)."""
    ccxt_symbol = _CCXT_SYMBOL_MAP.get(symbol.upper())
    if not ccxt_symbol:
        logger.debug("ccxt: no mapping for %s (forex not supported)", symbol)
        return None

    exchange = _get_ccxt_exchange()
    if not exchange:
        return None

    ccxt_tf = _CCXT_TIMEFRAME_MAP.get(timeframe.upper(), "1m")

    try:
        raw = exchange.fetch_ohlcv(ccxt_symbol, timeframe=ccxt_tf, limit=limit)
        if not raw:
            return None

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        logger.info("✅ ccxt OHLCV: %d candles for %s %s", len(df), symbol, timeframe)
        return df

    except Exception as e:
        logger.debug("ccxt OHLCV unavailable for %s: %s", symbol, e)
        return None


# ═══════════════════════════════════════════════════════════════════
#  SOURCE 3: YFINANCE (forex/XAU fallback)
# ═══════════════════════════════════════════════════════════════════

_YFINANCE_SYMBOL_MAP = {
    "XAUUSD": "GC=F",           # Gold futures
    "XAUUSDSPOT": "GC=F",
    "USOIL": "CL=F",            # Crude oil futures
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}


def _fetch_from_yfinance(symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from yfinance (fallback for forex/XAU)."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — fallback unavailable")
        return None

    yf_symbol = _YFINANCE_SYMBOL_MAP.get(symbol.upper())
    if not yf_symbol:
        logger.debug("yfinance: no mapping for %s", symbol)
        return None

    interval = config.YFINANCE_INTERVAL_MAP.get(timeframe.upper(), "1m")

    try:
        ticker = yf.Ticker(yf_symbol)
        # yfinance has limits: 1m data max 7 days, 5m max 60 days
        df = ticker.history(period=config.YFINANCE_MAX_PERIOD, interval=interval)
        if df.empty:
            return None

        # Normalize columns
        df = df.reset_index()
        rename = {"Date": "timestamp", "Open": "open", "High": "high",
                  "Low": "low", "Close": "close", "Volume": "volume"}
        df.rename(columns=rename, inplace=True)

        # Convert timezone-aware timestamps to epoch ms
        if hasattr(df["timestamp"].dtype, "tz") and df["timestamp"].dt.tz is not None:
            df["timestamp"] = df["timestamp"].astype(int) // 10**6
        elif df["timestamp"].dtype == "datetime64[ns]":
            df["timestamp"] = df["timestamp"].astype(int) // 10**6

        # Trim to requested limit
        df = df.tail(limit).reset_index(drop=True)

        logger.info("✅ yfinance OHLCV: %d candles for %s %s", len(df), symbol, timeframe)
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    except Exception as e:
        logger.debug("yfinance OHLCV unavailable for %s: %s", symbol, e)
        return None


# ═══════════════════════════════════════════════════════════════════
#  LIVE PRICE (current bid/ask)
# ═══════════════════════════════════════════════════════════════════

def get_live_price(symbol: str) -> Optional[float]:
    """
    Get current live price for a symbol.
    Priority: Bridge → ccxt → yfinance.
    Cached for PRICE_CACHE_TTL seconds.
    """
    key = symbol.upper()
    with _cache_lock:
        entry = _price_cache.get(key)
        if entry and entry.age < config.PRICE_CACHE_TTL:
            return float(entry.data["close"].iloc[-1]) if isinstance(entry.data, pd.DataFrame) else entry.data

    # Try bridge first
    price = _live_price_from_bridge(symbol)
    if price:
        _set_price_cache(key, price)
        return price

    # Try ccxt
    price = _live_price_from_ccxt(symbol)
    if price:
        _set_price_cache(key, price)
        return price

    # Try yfinance
    price = _live_price_from_yfinance(symbol)
    if price:
        _set_price_cache(key, price)
        return price

    return None


def _live_price_from_bridge(symbol: str) -> Optional[float]:
    try:
        url = f"{config.BRIDGE_URL}/price?symbol={symbol}"
        if config.BRIDGE_API_KEY:
            url += f"&api_key={config.BRIDGE_API_KEY}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return float(data.get("price", 0) or 0) or None
    except Exception:
        return None


def _live_price_from_ccxt(symbol: str) -> Optional[float]:
    ccxt_symbol = _CCXT_SYMBOL_MAP.get(symbol.upper())
    if not ccxt_symbol:
        return None
    exchange = _get_ccxt_exchange()
    if not exchange:
        return None
    try:
        ticker = exchange.fetch_ticker(ccxt_symbol)
        return float(ticker.get("last", 0) or 0) or None
    except Exception:
        return None


def _live_price_from_yfinance(symbol: str) -> Optional[float]:
    yf_symbol = _YFINANCE_SYMBOL_MAP.get(symbol.upper())
    if not yf_symbol:
        return None
    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_symbol)
        info = ticker.fast_info
        return float(info.get("lastPrice", 0) or 0) or None
    except Exception:
        return None


def _set_price_cache(key: str, price: float):
    df = pd.DataFrame({"close": [price]})
    with _cache_lock:
        _price_cache[key] = _CacheEntry(df, time.time())


# ═══════════════════════════════════════════════════════════════════
#  MAIN ENTRY: get_ohlcv()
# ═══════════════════════════════════════════════════════════════════

def get_ohlcv(
    symbol: str,
    timeframe: str = "M1",
    limit: int = 200,
    force_refresh: bool = False,
) -> Optional[pd.DataFrame]:
    """
    Get OHLCV data with fallback chain and caching.

    Priority: Bridge → ccxt → yfinance → CSV cache

    Returns DataFrame with columns [timestamp, open, high, low, close, volume]
    or None if all sources fail.
    """
    key = _cache_key(symbol, timeframe)

    # Check cache first (unless force refresh)
    if not force_refresh:
        with _cache_lock:
            entry = _ohlcv_cache.get(key)
            if entry and entry.is_fresh:
                logger.debug("Cache HIT: %s (age %.1fs)", key, entry.age)
                return entry.data.copy()

    # Try sources in priority order
    df = None
    source = None

    # Source 1: MT5 Bridge
    df = _fetch_from_bridge(symbol, timeframe, limit)
    if df is not None and len(df) >= 10:
        source = "bridge"
    else:
        df = None

    # Source 2: ccxt (crypto only)
    if df is None:
        df = _fetch_from_ccxt(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            source = "ccxt"
        else:
            df = None

    # Source 3: yfinance (forex/XAU fallback)
    if df is None:
        df = _fetch_from_yfinance(symbol, timeframe, limit)
        if df is not None and len(df) >= 10:
            source = "yfinance"
        else:
            df = None

    # Source 4: CSV cache (last resort)
    if df is None:
        df = _load_csv_cache(symbol, timeframe)
        if df is not None and len(df) >= 10:
            source = "csv_cache"
        else:
            df = None

    if df is None:
        logger.warning("⛔ ALL SOURCES FAILED for %s %s", symbol, timeframe)
        return None

    # Ensure numeric types
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Update cache
    with _cache_lock:
        _ohlcv_cache[key] = _CacheEntry(df.copy(), time.time())

    # Write to CSV (atomic)
    _save_csv_cache(symbol, timeframe, df)

    logger.info("📊 OHLCV ready: %s %s — %d candles (source: %s)", symbol, timeframe, len(df), source)
    return df


# ═══════════════════════════════════════════════════════════════════
#  CSV PERSISTENCE (atomic write)
# ═══════════════════════════════════════════════════════════════════

def _csv_path(symbol: str, timeframe: str) -> Path:
    return config.OHLCV_DIR / f"{symbol.upper()}_{timeframe.upper()}.csv"


def _save_csv_cache(symbol: str, timeframe: str, df: pd.DataFrame):
    """Write CSV atomically (write-temp → rename = no partial reads)."""
    try:
        final = _csv_path(symbol, timeframe)
        tmp = final.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.rename(final)  # atomic on Linux
    except Exception as e:
        logger.warning("CSV write failed: %s", e)


def _load_csv_cache(symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    """Load from CSV cache (last resort)."""
    try:
        path = _csv_path(symbol, timeframe)
        if not path.exists():
            return None
        df = pd.read_csv(path)
        logger.info("📂 CSV cache loaded: %s %s (%d rows)", symbol, timeframe, len(df))
        return df
    except Exception as e:
        logger.debug("CSV cache load failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
#  UTILITY
# ═══════════════════════════════════════════════════════════════════

def get_cache_stats() -> dict:
    """Return cache statistics for monitoring."""
    with _cache_lock:
        return {
            "ohlcv_entries": len(_ohlcv_cache),
            "price_entries": len(_price_cache),
            "ohlcv_keys": {
                k: {"age_s": round(v.age, 1), "fresh": v.is_fresh, "rows": len(v.data)}
                for k, v in _ohlcv_cache.items()
            },
        }
