"""Normalize raw trade data from various brokers/exchanges."""


def normalize_trade(raw: dict, broker: str = "deriv") -> dict:
    return {
        "order_id": raw.get("order_id", ""),
        "symbol": _clean_symbol(raw.get("symbol", "")),
        "side": str(raw.get("side", "")).upper(),
        "qty": float(raw.get("qty", raw.get("quantity", 0))),
        "price": float(raw.get("price", raw.get("entry_price", 0))),
        "tp": float(raw.get("tp", raw.get("take_profit", 0))),
        "sl": float(raw.get("sl", raw.get("stop_loss", 0))),
        "timestamp": raw.get("timestamp", raw.get("time", "")),
        "broker": broker,
    }


def _clean_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("/", "").replace("-", "")
