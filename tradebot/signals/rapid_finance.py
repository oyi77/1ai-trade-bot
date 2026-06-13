"""RapidAPI Real-Time Finance Data — primary price source for Vilona.

Replaces yfinance for XAUUSD, BTCUSD, and forex pairs.
Uses real-time-finance-data.p.rapidapi.com via the user's RapidAPI key.

Endpoints:
    /currency-exchange-rate  → XAU/USD, BTC/USD, EUR/USD, USD/IDR
    /stock-quote            → any stock/ETF ticker
    /market-trends          → MOST_ACTIVE, GAINERS, LOSERS
    /market-news            → financial news articles
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
BASE_URL = "https://real-time-finance-data.p.rapidapi.com"

_KEY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "rapidapi_key.json"


def _load_key() -> str:
    if _KEY_PATH.exists():
        cfg = json.loads(_KEY_PATH.read_text())
        return cfg.get("key", "")
    return ""


def _request(endpoint: str, params: dict[str, str] | None = None, timeout: int = 15) -> dict[str, Any]:
    key = _load_key()
    if not key:
        LOG.warning("rapid_finance: no API key configured")
        return {}

    url = f"{BASE_URL}/{endpoint}"
    if params:
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"

    req = urllib.request.Request(
        url, headers={"x-rapidapi-key": key, "Accept": "application/json"}, method="GET"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        LOG.warning("rapid_finance: %s → %s", endpoint, exc)
        return {}


# ── Price Fetching ──────────────────────────────────────────────────────────


def get_forex_rate(from_sym: str, to_sym: str = "USD") -> float | None:
    data = _request("currency-exchange-rate", {"from_symbol": from_sym, "to_symbol": to_sym})
    rate = data.get("data", {}).get("exchange_rate")
    if rate is not None:
        LOG.info("rapid_finance: %s/%s = %.4f", from_sym, to_sym, float(rate))
        return float(rate)
    return None


def get_xauusd() -> float | None:
    return get_forex_rate("XAU", "USD")


def get_btcusd() -> float | None:
    return get_forex_rate("BTC", "USD")


def get_eurusd() -> float | None:
    return get_forex_rate("EUR", "USD")


def get_usdidr() -> float | None:
    return get_forex_rate("USD", "IDR")


def get_stock_quote(symbol: str) -> dict[str, Any] | None:
    data = _request("stock-quote", {"symbol": symbol, "language": "en"})
    return data.get("data") if data.get("status") == "OK" else None


def get_market_trends(trend_type: str = "MOST_ACTIVE") -> list[dict[str, Any]]:
    data = _request("market-trends", {"trend_type": trend_type})
    return data.get("data", {}).get("trends", [])


def get_news(limit: int = 5) -> list[dict[str, Any]]:
    data = _request("market-news", {"language": "en"})
    return data.get("data", {}).get("news", [])[:limit]


# ── Convenience: multi-symbol fetch ─────────────────────────────────────────


def fetch_prices(symbols: list[str]) -> dict[str, float | None]:
    results: dict[str, float | None] = {}
    mapping = {
        "XAUUSD": ("XAU", "USD"),
        "BTCUSD": ("BTC", "USD"),
        "ETHUSD": ("ETH", "USD"),
        "EURUSD": ("EUR", "USD"),
        "GBPUSD": ("GBP", "USD"),
        "USDJPY": ("USD", "JPY"),
        "USDIDR": ("USD", "IDR"),
    }
    for sym in symbols:
        pair = mapping.get(sym.upper())
        if pair:
            results[sym] = get_forex_rate(*pair)
        else:
            results[sym] = None
    return results


def save_key(key: str) -> None:
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEY_PATH.write_text(json.dumps({"key": key, "updated": datetime.now(WIB).isoformat()}, indent=2))
    LOG.info("rapid_finance: key saved to %s", _KEY_PATH)
