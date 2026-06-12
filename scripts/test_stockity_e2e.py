#!/usr/bin/env python3
"""
Stockity E2E — sequential trades, FRESH CONNECTION PER TRADE.
This pattern is PROVEN WORKING (see test_stockity_blitz.py).

Usage: python3 scripts/test_stockity_e2e.py --mode demo --trades 100
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot.brokers.stockity.broker import StockityBroker


async def run_trades(deal_type: str, n: int) -> list[dict]:
    print(f"\n=== Stockity E2E ({deal_type}, {n} trades) ===\n")
    results: list[dict] = []

    for i in range(1, n + 1):
        direction = "CALL" if i % 2 == 0 else "PUT"
        print(f"[{i:3d}/{n}] {direction:4s} ... ", end="", flush=True)

        opened = asyncio.Future()
        closed = asyncio.Future()

        def on_opened(msg, _o=opened):
            if not _o.done():
                _o.set_result(msg.get("payload", {}))

        def on_closed(msg, _c=closed):
            if not _c.done():
                _c.set_result(msg.get("payload", {}))

        broker = StockityBroker(deal_type=deal_type)
        await broker.connect()
        # Wait for Phoenix channel to fully join
        # Without this, bo:create is sent before bo channel is ready
        await asyncio.sleep(2)

        # REGISTER CALLBACKS FIRST, place trade second
        # bo:opened fires immediately after bo:create
        broker.on_event("bo", "opened", on_opened)
        broker.on_event("bo", "closed", on_closed)

        trade = await broker.place_trade(
            symbol="CRYPTO_IDX", direction=direction, amount=1.0, duration=5,
        )
        st = getattr(trade.status, "value", "")
        if st in ("rejected", "REJECTED"):
            print("REJECTED")
            results.append({"i": i, "result": "REJECTED", "direction": direction})
            await broker.close()
            await asyncio.sleep(random.uniform(0.3, 1.0))
            continue

        try:
            op = await asyncio.wait_for(opened, timeout=15)
        except TimeoutError:
            print("OPEN_TIMEOUT")
            results.append({"i": i, "result": "OPEN_TIMEOUT", "direction": direction})
            await broker.close()
            await asyncio.sleep(random.uniform(0.3, 1.0))
            continue

        try:
            cl = await asyncio.wait_for(closed, timeout=20)
            status = cl.get("status", "lost")
            win = cl.get("win", 0)
            amt = cl.get("amount", 0)
            pnl = win - amt if status == "won" else -amt
            outcome = "WON" if status == "won" else "LOST" if status == "lost" else "TIE"
            print(f"{outcome:4s} pnl={pnl:7d}")
            results.append({"i": i, "result": outcome, "pnl": pnl, "direction": direction})
        except TimeoutError:
            print("CLOSE_TIMEOUT")
            results.append({"i": i, "result": "CLOSE_TIMEOUT", "direction": direction})

        await broker.close()
        await asyncio.sleep(random.uniform(0.3, 1.0))

    return results


def print_summary(results: list[dict]) -> None:
    if not results:
        print("\nNo results.")
        return
    total = len(results)
    won = sum(1 for r in results if r.get("result") == "WON")
    lost = sum(1 for r in results if r.get("result") == "LOST")
    tie = sum(1 for r in results if r.get("result") == "TIE")
    other = total - won - lost - tie
    total_pnl = sum(r.get("pnl", 0) for r in results if r.get("pnl"))
    print(f"\n{'='*50}")
    print(f"  TOTAL: {total}")
    print(f"  WON:   {won} ({won/total*100:.1f}%)")
    print(f"  LOST:  {lost} ({lost/total*100:.1f}%)")
    print(f"  TIE:   {tie}")
    print(f"  ERR:   {other}")
    print(f"  NET:   {total_pnl}")
    print(f"  USD:   ${total_pnl/16350:.2f}")
    print(f"{'='*50}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="demo", choices=["demo", "live"])
    parser.add_argument("--trades", type=int, default=100)
    args = parser.parse_args()

    results = asyncio.run(run_trades(args.mode, args.trades))
    print_summary(results)


if __name__ == "__main__":
    main()