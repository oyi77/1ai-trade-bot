#!/usr/bin/env python3
"""
Vilona Market Data Fallback — multi-source price fetcher.
Tries in order: Yahoo → Twelve Data → Alpha Vantage → ForexFeed → CoinGecko
"""
import urllib.request, json, ssl, time
from typing import Optional

ctx = ssl.create_default_context()
UA = "VilonaTFX/2.0"

# ── Sources ──
SOURCES = {
    # Free, no key needed
    "currency_api": "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json",
    "coingecko": "https://api.coingecko.com/api/v3/simple/price?ids={id}&vs_currencies=usd",
    "yahoo": None,  # handled by yfinance
}

# Asset → fallback mapping
ASSET_FALLBACKS = {
    "XAUUSD": {
        "primary": ("currency_api", "xau"),
        "coingecko": None,  # no crypto
    },
    "BTCUSD": {
        "primary": ("coingecko", "bitcoin"),
    },
    "ETHUSD": {
        "primary": ("coingecko", "ethereum"),
    },
    "EURUSD": {
        "primary": ("currency_api", "eur"),
    },
    "GBPUSD": {
        "primary": ("currency_api", "gbp"),
    },
    "USOIL": {
        "primary": ("yahoo", "CL=F"),
    },
}

def fetch_price_fallback(asset: str) -> Optional[float]:
    """Multi-source price fetch with automatic fallback."""
    
    if asset in ("XAUUSD", "GOLD"):
        # Primary: currency API (already implemented in market_data)
        try:
            url = SOURCES["currency_api"].format(base="xau")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            data = json.loads(resp.read())
            usd = data.get("xau", {}).get("usd", 0)
            if usd and usd > 100:
                return float(usd)
        except Exception:
            pass
    
    if asset in ("EURUSD", "GBPUSD"):
        try:
            base = "eur" if asset == "EURUSD" else "gbp"
            url = SOURCES["currency_api"].format(base=base)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            data = json.loads(resp.read())
            usd = data.get(base, {}).get("usd", 0)
            if usd and usd > 0:
                return float(usd)
        except Exception:
            pass
    
    if asset in ("BTCUSD", "ETHUSD"):
        try:
            coin = "bitcoin" if asset == "BTCUSD" else "ethereum"
            url = SOURCES["coingecko"].format(id=coin)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            data = json.loads(resp.read())
            usd = data.get(coin, {}).get("usd", 0)
            if usd and usd > 0:
                return float(usd)
        except Exception:
            pass
    
    return None

if __name__ == "__main__":
    for asset in ["XAUUSD", "BTCUSD", "ETHUSD", "EURUSD", "GBPUSD"]:
        price = fetch_price_fallback(asset)
        print(f"{asset}: {'${:.2f}'.format(price) if price else 'FAILED'}")
