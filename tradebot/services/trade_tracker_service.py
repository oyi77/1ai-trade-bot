"""Trade tracker service — daily trade recap and outcome tracking.

Absorbed from scripts/trade_tracker.py so the tradebot package does not
depend on scripts/ for its data layer.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
DATA_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "trade_history.json"
)
USD_IDR = 16350
MODAL_USD = 100

MICRO_LOT_PIP_VALUE: dict[str, float] = {
    "XAUUSD": 0.10,
    "GOLD": 0.10,
    "EURUSD": 0.10,
    "GBPUSD": 0.10,
    "USDJPY": 0.09,
    "BTCUSD": 0.01,
    "BTC": 0.01,
    "USOIL": 0.10,
    "OIL": 0.10,
    "CL": 0.10,
}
DEFAULT_MICRO_PIP = 0.10


def _load() -> dict[str, Any]:
    try:
        if DATA_FILE.exists():
            text = DATA_FILE.read_text().strip()
            if not text:
                raise ValueError("empty")
            payload = json.loads(text)
            if isinstance(payload, list):
                payload = {
                    "trades": payload,
                    "stats": {
                        "total": 0,
                        "wins": 0,
                        "losses": 0,
                        "breakeven": 0,
                        "total_pips": 0.0,
                        "total_profit_usd": 0.0,
                        "best_win_pips": 0.0,
                        "worst_loss_pips": 0.0,
                    },
                }
            elif not isinstance(payload, dict):
                raise ValueError("invalid")
            return payload
    except Exception:
        pass
    return {
        "trades": [],
        "stats": {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "total_pips": 0.0,
            "total_profit_usd": 0.0,
            "best_win_pips": 0.0,
            "worst_loss_pips": 0.0,
        },
    }


def get_daily_trades(date_str: str = "") -> dict[str, Any]:
    if not date_str:
        date_str = datetime.now(WIB).strftime("%Y-%m-%d")

    data = _load()
    trades = data.get("trades", [])

    daily = [t for t in trades if t.get("open_time", "").startswith(date_str)]

    wins = [t for t in daily if t.get("outcome") == "TP_HIT"]
    losses = [t for t in daily if t.get("outcome") == "SL_HIT"]

    total_pips = sum(
        t.get("pips", 0) for t in daily if t.get("outcome") not in ("OPEN", None)
    )

    micro_profit = 0.0
    for t in daily:
        if t.get("outcome") in ("OPEN", None):
            continue
        sym = t.get("symbol", "XAUUSD").upper()
        pip_val = MICRO_LOT_PIP_VALUE.get(sym, DEFAULT_MICRO_PIP)
        micro_profit += t.get("pips", 0) * pip_val

    pairs: dict[str, dict[str, Any]] = {}
    for t in daily:
        sym = t.get("symbol", "?")
        if sym not in pairs:
            pairs[sym] = {"total": 0, "wins": 0, "losses": 0, "pips": 0.0}
        pairs[sym]["total"] += 1
        if t.get("outcome") == "TP_HIT":
            pairs[sym]["wins"] += 1
        elif t.get("outcome") == "SL_HIT":
            pairs[sym]["losses"] += 1
        pairs[sym]["pips"] += t.get("pips", 0)

    return {
        "date": date_str,
        "trades": daily,
        "total_signals": len(daily),
        "wins": len(wins),
        "losses": len(losses),
        "total_pips": round(total_pips, 1),
        "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "micro_profit": round(micro_profit, 2),
        "micro_profit_pct": round(micro_profit / MODAL_USD * 100, 1),
        "micro_profit_idr": round(micro_profit * USD_IDR),
        "pairs": pairs,
    }


def get_recent_trades(limit: int = 10) -> list[dict[str, Any]]:
    data = _load()
    closed = [t for t in data["trades"] if t.get("outcome") not in ("OPEN", None)]
    closed.sort(key=lambda t: t.get("close_time", ""), reverse=True)
    return closed[:limit]


def get_stats() -> dict[str, Any]:
    data = _load()
    s = data["stats"]
    total = s["total"]
    wins = s["wins"]
    losses = s["losses"]
    return {
        **s,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
        "open_positions": sum(1 for t in data["trades"] if t.get("outcome") == "OPEN"),
        "total_profit_idr": round(s["total_profit_usd"] * USD_IDR),
        "avg_win_pips": round(s["total_pips"] / wins, 1) if wins > 0 else 0.0,
        "avg_loss_pips": round(s["worst_loss_pips"], 1) if losses > 0 else 0.0,
    }


__all__ = ["get_daily_trades", "get_recent_trades", "get_stats"]
