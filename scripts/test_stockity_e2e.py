#!/usr/bin/env python3
"""
Stockity E2E — multi-trade loop, single persistent connection.

Usage: python3 scripts/test_stockity_e2e.py --mode demo --trades 100
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot.brokers.stockity.broker import StockityBroker


class E2ERunner:
    """Loop N trades using one persistent WS connection."""

    def __init__(self, deal_type: str, num_trades: int):
        self.deal_type = deal_type
        self.num_trades = num_trades
        self.results: list[dict] = []
        self.ticks: deque[dict] = deque(maxlen=30)
        self.broker: StockityBroker | None = None

    def _on_tick(self, tick: dict) -> None:
        self.ticks.append(tick)

    def _pick_direction(self) -> tuple[str, str]:
        t = list(self.ticks)
        if len(t) >= 5:
            mids = [float(x.get("rate") or ((x.get("bid", 0) + x.get("ask", 0)) / 2)) for x in t[-5:]]
            if len(mids) >= 4:
                deltas = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
                up = sum(1 for d in deltas if d > 0)
                down = sum(1 for d in deltas if d < 0)
                if up >= 3:
                    return ("CALL", "momentum_3up")
                if down >= 3:
                    return ("PUT", "momentum_3down")
        n = len(self.results)
        return ("CALL" if n % 2 == 0 else "PUT", "alternating_ctrl")

    async def run_all(self) -> list[dict]:
        print(f"\n=== Stockity E2E ({self.deal_type}, {self.num_trades} trades) ===")
        self.broker = StockityBroker(deal_type=self.deal_type)
        self.broker.on_tick(self._on_tick)
        await self.broker.connect()

        for i in range(1, self.num_trades + 1):
            direction, strategy = self._pick_direction()
            print(f"\n[{i}/{self.num_trades}] {direction} ({strategy}) ticks={len(self.ticks)}", end="")

            opened = asyncio.Future()
            closed = asyncio.Future()

            def on_opened(msg):
                if not opened.done():
                    opened.set_result(msg.get("payload", {}))

            def on_closed(msg):
                if not closed.done():
                    closed.set_result(msg.get("payload", {}))

            self.broker.on_event("bo", "opened", on_opened)
            self.broker.on_event("bo", "closed", on_closed)

            result = await self.broker.place_trade(
                symbol="CRYPTO_IDX", direction=direction, amount=1.0, duration=5,
            )
            if getattr(result.status, "value", "") in ("rejected", "REJECTED"):
                print(" ❌ REJECTED")
                self.results.append({"trade": i, "result": "REJECTED", "direction": direction, "strategy": strategy})
                continue

            try:
                op = await asyncio.wait_for(opened, timeout=10)
                print(f" open={op.get('id')}", end="")
            except TimeoutError:
                print(" ❌ OPEN_TIMEOUT")
                self.results.append({"trade": i, "result": "OPEN_TIMEOUT", "direction": direction, "strategy": strategy})
                continue

            try:
                cl = await asyncio.wait_for(closed, timeout=20)
                status = cl.get("status", "lost")
                win = cl.get("win", 0)
                pnl = win - cl.get("amount", 0) if status == "won" else -cl.get("amount", 0)
                outcome = "WON" if status == "won" else "LOST"
                print(f" → {outcome} pnl={pnl}")
                self.results.append({"trade": i, "result": outcome, "pnl": pnl, "direction": direction, "strategy": strategy})
            except TimeoutError:
                print(" ❌ CLOSE_TIMEOUT")
                self.results.append({"trade": i, "result": "CLOSE_TIMEOUT", "direction": direction, "strategy": strategy})

            await asyncio.sleep(random.uniform(0.5, 1.5))

        await self.broker.close()
        return self.results

    @staticmethod
    def print_summary(results: list[dict]) -> None:
        if not results:
            print("\nNo results.")
            return
        total = len(results)
        won = sum(1 for r in results if r.get("result") == "WON")
        lost = sum(1 for r in results if r.get("result") == "LOST")
        other = total - won - lost
        strategies: dict[str, list[bool]] = {}
        for r in results:
            s = r.get("strategy", "?")
            strategies.setdefault(s, []).append(r.get("result") == "WON")

        print(f"\n{'='*50}")
        print(f"  TOTAL: {total} | WON: {won} | LOST: {lost} | OTHER: {other}")
        if total:
            print(f"  WIN RATE: {won/total*100:.1f}%")
        if won + lost:
            total_pnl = sum(r.get("pnl", 0) for r in results if r.get("pnl"))
            print(f"  TOTAL P&L: {total_pnl}")
        print()
        for s, outcomes in sorted(strategies.items(), key=lambda x: -sum(x[1]) / len(x[1]) if x[1] else 0):
            wr = sum(outcomes) / len(outcomes) * 100 if outcomes else 0
            print(f"  {s:30s}: {sum(outcomes):2d}/{len(outcomes):2d} ({wr:.1f}%)")
        print(f"{'='*50}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="demo", choices=["demo", "live"])
    parser.add_argument("--trades", type=int, default=100)
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    runner = E2ERunner(args.mode, args.trades)
    results = asyncio.run(runner.run_all())
    runner.print_summary(results)

    if args.telegram:
        import os
        from telethon import TelegramClient

        async def tg_test():
            client = TelegramClient(
                os.path.expanduser("~/.openclaw/workspace/paijo"),
                23647272, "1f69a4e0f03e5f51ddfa5b67ac7b5c49",
            )
            await client.connect()
            bot = "agent_1ai2_bot"
            for cmd in ["/platforms", "/signal CRYPTO_IDX"]:
                await client.send_message(bot, cmd)
                await asyncio.sleep(3)
                async for msg in client.iter_messages(bot, limit=1):
                    if not msg.out:
                        print(f"  {cmd} → {msg.text[:80]}")
            await client.disconnect()

        asyncio.run(tg_test())


if __name__ == "__main__":
    main()