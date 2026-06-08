#!/usr/bin/env python3
"""Multi-source market data fetcher for Vilona Trade FX.
Primary: Yahoo Finance (yfinance). Fallback: FCS API v4."""
import os, json, time, logging, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("data-sources")

FCS_BASE = "https://api-v4.fcsapi.com/forex"
FCS_KEY = os.environ.get("FCS_API_KEY", "")

# ── Symbol mapping: internal name → (yfinance_symbol, fcs_symbol)
SYMBOL_MAP = {
    "XAUUSD":  ("GC=F",     "XAUUSD"),
    "ETHUSD":  ("ETH-USD",  "ETHUSD"),
    "BTCUSD":  ("BTC-USD",  "BTCUSD"),
    "EURUSD":  ("EURUSD=X", "EURUSD"),
    "GBPUSD":  ("GBPUSD=X", "GBPUSD"),
    "USDJPY":  ("USDJPY=X", "USDJPY"),
    "USOIL":   ("CL=F",     "OSX"),
    "SPX":     ("^GSPC",    "SP500"),
    "NAS100":  ("^NDX",     "NAS100"),
}

def _fcs_fetch(endpoint, params, max_retries=2):
    """Call FCS API with retry. Returns dict or None."""
    if not FCS_KEY:
        return None
    params["access_key"] = FCS_KEY
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FCS_BASE}/{endpoint}?{query}"

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            if data.get("code") == 200 and data.get("status") is True:
                return data
            logger.warning(f"FCS API returned error: {data.get('message', 'unknown')}")
            return None
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1 * (attempt + 1))
            else:
                logger.warning(f"FCS API failed after {max_retries+1} attempts: {e}")
                return None

def fcs_ohlcv(internal_name, period="5m", bars=20):
    """Fetch OHLCV bars from FCS API. Returns list of dicts or None."""
    sym = SYMBOL_MAP.get(internal_name.upper(), (None, None))
    fcs_sym = sym[1]
    if not fcs_sym:
        logger.warning(f"FCS: no mapping for {internal_name}")
        return None

    # Determine type
    if internal_name in ("XAUUSD", "USOIL", "SPX", "NAS100"):
        asset_type = "commodity"
    elif internal_name in ("ETHUSD", "BTCUSD"):
        asset_type = "crypto"
    else:
        asset_type = "forex"

def _unpack_bar(r, which="active"):
    """Unpack FCS API bar data (nested under 'active' or 'previous')."""
    a = r.get(which, r)
    return {
        "Open": float(a.get("o", 0) or 0),
        "High": float(a.get("h", 0) or 0),
        "Low": float(a.get("l", 0) or 0),
        "Close": float(a.get("c", 0) or 0),
        "Volume": int(float(a.get("v", 0) or 0)),
        "timestamp": a.get("t", int(time.time())),
    }

    params = {
        "symbol": fcs_sym,
        "type": asset_type,
        "period": period,
    }

    # Use latest endpoint (history requires paid plan)
    # Returns 2 bars per symbol: active + previous
    params.pop("period", None)  # latest endpoint doesn't use period
    data = _fcs_fetch("latest", params)
    if data:
        response = data.get("response", [])
        if isinstance(response, dict):
            response = [response]
        result = []
        for r in response[:]:
            if r:
                prev = _unpack_bar(r, "previous")
                act = _unpack_bar(r, "active")
                if act["Close"]:
                    result.append(act)
                if prev["Close"]:
                    result.append(prev)
        return result[-bars:] if result else None

    return None

def fcs_price(internal_name):
    """Get latest price from FCS API. Returns dict or None."""
    sym = SYMBOL_MAP.get(internal_name.upper(), (None, None))
    fcs_sym = sym[1]
    if not fcs_sym:
        return None

    if internal_name in ("XAUUSD", "USOIL"):
        asset_type = "commodity"
    elif internal_name in ("ETHUSD", "BTCUSD"):
        asset_type = "crypto"
    else:
        asset_type = "forex"

    data = _fcs_fetch("latest", {"symbol": fcs_sym, "type": asset_type})
    if not data:
        return None

    response = data.get("response", [])
    if isinstance(response, list) and response:
        r = response[0]
        act = r.get("active", {})
        return {
            "price": float(act.get("c", 0) or 0),
            "high": float(act.get("h", 0) or 0),
            "low": float(act.get("l", 0) or 0),
            "open": float(act.get("o", 0) or 0),
            "change_pct": float(act.get("chp", 0) or 0),
        }
    return None

def fallback_ohlcv(internal_name, period="5m", bars=20, yahoo_symbol=None):
    """Primary: yfinance. Fallback: FCS API."""
    import yfinance as yf

    sym = SYMBOL_MAP.get(internal_name.upper(), (yahoo_symbol, internal_name))
    yahoo_sym = sym[0] if sym[0] else yahoo_symbol or internal_name

    try:
        ticker = yf.Ticker(yahoo_sym)
        period_map = {"1m": "1d", "5m": "5d", "15m": "5d", "30m": "10d", "1h": "30d", "1d": "60d"}
        yf_period = period_map.get(period, "5d")
        df = ticker.history(period=yf_period, interval=period)
        if df is not None and not df.empty and len(df) >= 5:
            logger.info(f"✅ Yahoo: {len(df)} bars for {yahoo_sym}")
            return df
    except Exception as e:
        logger.warning(f"Yahoo failed for {internal_name}: {e}")

    # ── Fallback to FCS API ──
    if FCS_KEY:
        logger.info(f"🔄 Falling back to FCS API for {internal_name}...")
        bars_data = fcs_ohlcv(internal_name, period, bars)
        if bars_data:
            import pandas as pd
            df = pd.DataFrame(bars_data)
            logger.info(f"✅ FCS: {len(df)} bars for {internal_name}")
            return df

    logger.error(f"❌ All data sources failed for {internal_name}")
    return None

def fallback_price(internal_name, yahoo_symbol=None):
    """Get latest price with fallback."""
    import yfinance as yf

    sym = SYMBOL_MAP.get(internal_name.upper(), (yahoo_symbol, internal_name))
    yahoo_sym = sym[0] if sym[0] else yahoo_symbol or internal_name

    try:
        ticker = yf.Ticker(yahoo_sym)
        price = ticker.history(period="1d")
        if price is not None and not price.empty:
            return float(price["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Yahoo price failed for {internal_name}: {e}")

    if FCS_KEY:
        result = fcs_price(internal_name)
        if result and result.get("price"):
            return result["price"]

    return 0.0
