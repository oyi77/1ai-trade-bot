"""Apex Hunt Radar (AHZ) — Sync wrapper for Harmonic Pattern Engine.

Converts existing OHLCV bars (from _fetch_ohlcv_for_ai) into Tick objects,
feeds HarmonicEngine's internal sync methods, returns formatted result.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

def _bars_to_ticks(bars: list[dict], symbol: str = "XAUUSD") -> list:
    """Convert OHLCV bars to list of Tick objects (close price only).
    
    Harmonic engine needs price/time ticks to detect pivots.
    We inject close prices at bar-level granularity.
    """
    try:
        from tradebot.models.market import Tick
    except ImportError:
        return []
    
    ticks = []
    for i, bar in enumerate(bars):
        close = float(bar.get("close", 0))
        ts = int(bar.get("timestamp", 0) or bar.get("time", 0) or i * 300)
        if close > 0:
            ticks.append(Tick(symbol=symbol, price=close, epoch=ts))
    return ticks


def scan_harmonic_patterns(
    bars: list[dict] | None,
    symbol: str = "XAUUSD"
) -> dict[str, Any] | None:
    """Scan OHLCV bars for harmonic patterns — sync wrapper.
    
    Args:
        bars: OHLCV bars (from _fetch_ohlcv_for_ai or similar)
        symbol: Asset symbol
        
    Returns:
        dict with AHZ details or None if no pattern found.
    """
    if not bars or len(bars) < 30:
        return None
    
    ticks = _bars_to_ticks(bars, symbol)
    if len(ticks) < 10:
        return None
    
    try:
        from tradebot.engines.harmonic import (
            detect_pivots, find_xabcd, validate_pattern, PatternType
        )
        ohlcv = _ticks_to_ohlcv(ticks)
        if len(ohlcv) < 15:
            return None
        
        pivots = detect_pivots(ohlcv, fractal_period=5)
        if len(pivots) < 5:
            return None
        
        xabcd = find_xabcd(pivots)
        if xabcd is None:
            return None
        
        best_match = None
        for ptype in list(PatternType):
            match = validate_pattern(xabcd, ptype)
            if match is not None:
                if best_match is None or match.confidence > best_match.confidence:
                    best_match = match
        
        if best_match is None:
            return None
        
        # Build result dict
        last_price = ticks[-1].price if ticks else 0.0
        
        result = {
            "symbol": symbol,
            "bias": best_match.bias.value.upper() if hasattr(best_match.bias, 'value') else str(best_match.bias).upper(),
            "pattern": best_match.pattern.value.upper() if hasattr(best_match.pattern, 'value') else str(best_match.pattern).upper(),
            "confidence": round(best_match.confidence * 100, 1),
            "entry_min": best_match.ahz_lower,
            "entry_max": best_match.ahz_upper,
            "entry_mid": round((best_match.ahz_lower + best_match.ahz_upper) / 2, 2),
            "sl": best_match.sl,
            "tp1": best_match.tp1,
            "tp2": best_match.tp2,
            "last_price": last_price,
            "active": _check_ahz_active(best_match.ahz_lower, best_match.ahz_upper, last_price),
            "scan_time": datetime.now(WIB).strftime("%H:%M WIB"),
        }
        
        logger.info(
            "AHZ %s %s detected — conf=%.1f%% AHZ [%.2f–%.2f]",
            result["bias"], result["pattern"], result["confidence"],
            result["entry_min"], result["entry_max"],
        )
        return result
    
    except ImportError as e:
        logger.warning("Harmonic engine not available: %s", e)
        return None
    except Exception as e:
        logger.debug("Harmonic scan error: %s", e)
        return None


def _ticks_to_ohlcv(ticks: list) -> list[dict]:
    """Convert Tick list to OHLCV bars (close = price).
    
    The harmonic engine expects OHLCV dicts. For tick data we create
    1-bar-per-tick synthetic OHLCV (close = price).
    """
    ohlcv = []
    for i, t in enumerate(ticks):
        p = float(t.price) if hasattr(t, 'price') else 0
        if p == 0:
            continue
        ts = int(t.epoch) if hasattr(t, 'epoch') else int(datetime.now().timestamp())
        ohlcv.append({
            "timestamp": ts,
            "open": p, "high": p, "low": p, "close": p,
            "volume": 0,
        })
    return ohlcv


def format_ahz_alert(result: dict) -> str:
    """Format AHZ result as clean Telegram message."""
    direction_emoji = "🟢" if result.get("bias") == "BULLISH" else "🔴"
    status = "🔴 <b>HUNTING ACTIVE</b> 🎯" if result["active"] else "⏳ <b>AHZ PENDING</b>"
    
    return (
        f"📡 <b>APEX HUNT RADAR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>{result['pattern']}</b>\n"
        f"{direction_emoji} Bias: <b>{result['bias']}</b> | Conf: <b>{result['confidence']}%</b>\n\n"
        f"💀 <b>AHZ (Apex Hunt Zone)</b>\n"
        f"Zone Masuk: ${result['entry_min']:.2f} — ${result['entry_max']:.2f}\n"
        f"Optimal Entry: ${result['entry_mid']:.2f}\n"
        f"🛑 SL: ${result['sl']:.2f}\n"
        f"🎯 TP1: ${result['tp1']:.2f} | TP2: ${result['tp2']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Harga Sekarang: ${result['last_price']:.2f}\n"
        f"{status}\n"
        f"⏰ {result['scan_time']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )


def _check_ahz_active(ahz_low: float, ahz_high: float, price: float, threshold_pct: float = 0.3) -> bool:
    """Check if price is within threshold_pct % of AHZ zone."""
    if not ahz_low or not ahz_high or not price:
        return False
    ahz_mid = (ahz_low + ahz_high) / 2
    distance = abs(price - ahz_mid) / price * 100
    return distance <= threshold_pct


# ── Quick test ──
if __name__ == "__main__":
    # Test with sample data
    sample_bars = [
        {"timestamp": 1718500000 + i*300,
         "open": 4310 + i*0.5, "high": 4311 + i*0.5,
         "low": 4309 + i*0.5, "close": 4310.5 + i*0.5}
        for i in range(60)
    ]
    result = scan_harmonic_patterns(sample_bars)
    if result:
        print(format_ahz_alert(result))
    else:
        print("No harmonic pattern detected (market flat)")
