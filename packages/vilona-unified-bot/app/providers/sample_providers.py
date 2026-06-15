"""Static sample data for testing unified bot signal pipeline."""

from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

SAMPLE_SIGNALS = [
    {"symbol": "XAUUSD", "action": "BUY", "confidence": 0.85, "tp_pips": 150, "sl_pips": 50},
    {"symbol": "EURUSD", "action": "SELL", "confidence": 0.72, "tp_pips": 80, "sl_pips": 30},
    {"symbol": "GBPJPY", "action": "BUY", "confidence": 0.68, "tp_pips": 120, "sl_pips": 40},
    {"symbol": "BTCUSD", "action": "SELL", "confidence": 0.91, "tp_pips": 800, "sl_pips": 300},
]

SAMPLE_ORDERS = [
    {"order_id": "ORD-001", "symbol": "XAUUSD", "side": "BUY", "qty": 0.10, "status": "FILLED"},
    {"order_id": "ORD-002", "symbol": "EURUSD", "side": "SELL", "qty": 0.25, "status": "PENDING"},
]

SAMPLE_TENANTS = [
    {"brand_id": "vilona", "plan": "pro", "commission_pct": 0.70},
    {"brand_id": "1ai", "plan": "basic", "commission_pct": 0.30},
]
