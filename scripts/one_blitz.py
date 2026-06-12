#!/usr/bin/env python3
"""
Single Stockity Blitz trade — accepts direction as arg.
Used by test_stockity_e2e.py for batch testing.

Usage: python3 scripts/one_blitz.py CALL
       python3 scripts/one_blitz.py PUT
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot.brokers.stockity.broker import StockityBroker
from tradebot.config import settings

SYMBOL = "CRYPTO_IDX"
USD_IDR = 16350
DIRECTION = sys.argv[1] if len(sys.argv) > 1 else "CALL"


async def main():
    broker = StockityBroker(deal_type="demo")
    await broker.connect()
    curr = broker.balance_currency
    stake = 14000.0 if curr == "IDR" else 1.0
    opened = asyncio.Future()
    closed = asyncio.Future()

    def on_opened(m, _o=opened):
        if not _o.done(): _o.set_result(m.get("payload", {}))
    def on_closed(m, _c=closed):
        if not _c.done(): _c.set_result(m.get("payload", {}))

    broker.on_event("bo", "opened", on_opened)
    broker.on_event("bo", "closed", on_closed)

    trade = await broker.place_trade(symbol=SYMBOL, direction=DIRECTION, amount=stake, duration=5)
    st = getattr(trade.status, "value", "")
    if st in ("rejected", "REJECTED"):
        print("REJECTED")
        await broker.close()
        return

    try:
        await asyncio.wait_for(opened, timeout=15)
    except TimeoutError:
        print("OPEN_TIMEOUT")
        await broker.close()
        return

    try:
        cl = await asyncio.wait_for(closed, timeout=20)
    except TimeoutError:
        print("CLOSE_TIMEOUT")
        await broker.close()
        return

    status = cl.get("status", "lost")
    win = cl.get("win", 0)
    pnl = win - cl.get("amount", 0) if status == "won" else -cl.get("amount", 0)
    outcome = "WON" if status == "won" else "LOST" if status == "lost" else "TIE"
    print(f"OUTCOME: {outcome} | P&L: {pnl}")
    await broker.close()


if __name__ == "__main__":
    asyncio.run(main())