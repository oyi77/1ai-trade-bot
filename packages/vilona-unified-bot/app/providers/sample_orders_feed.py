"""Synthetic order feed generator for demo/testing."""

import random
from datetime import datetime, timezone, timedelta

from app.providers.sample_providers import SAMPLE_ORDERS, SAMPLE_SIGNALS

WIB = timezone(timedelta(hours=7))


def generate_orders(count: int = 5) -> list[dict]:
    orders = []
    for i in range(count):
        sig = random.choice(SAMPLE_SIGNALS)
        orders.append({
            "order_id": f"GEN-{datetime.now(WIB).strftime('%H%M%S')}-{i:03d}",
            "symbol": sig["symbol"],
            "side": sig["action"],
            "qty": round(random.uniform(0.05, 0.50), 2),
            "status": random.choice(["PENDING", "FILLED"]),
            "tp": sig.get("tp_pips", 0),
            "sl": sig.get("sl_pips", 0),
            "confidence": sig.get("confidence", 0.5),
        })
    return orders
