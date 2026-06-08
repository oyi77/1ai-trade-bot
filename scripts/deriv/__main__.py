#!/usr/bin/env python3
"""
Deriv Unified CLI — PAT Token + APPID Auto-Flow
================================================

Usage:
  python -m scripts.deriv test                        # Auto: PAT + APPID + account
  python -m scripts.deriv debug symbol                # Debug ticks
  python -m scripts.deriv trade symbol                # One trade cycle
  python -m scripts.deriv stream symbol               # Live tick feed

Env vars (auto-loaded):
  DERIV_PAT_TOKEN, DERIV_APP_ID, DERIV_ACCOUNT_ID
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ensure scripts/ is in path for package imports
_SCRIPT_DIR = Path(__file__).resolve().parent  # scripts/deriv/
_PARENT = _SCRIPT_DIR.parent  # scripts/
_GRANDPARENT = _PARENT.parent  # 1ai-trade-bot/
for p in str(_PARENT), str(_GRANDPARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOG = logging.getLogger("deriv.main")

PAT_TOKEN = os.getenv("DERIV_PAT_TOKEN", "pat_0f2c09ae7ef25d3970e5829982e77206bd53c761c57e153f53dd99f8e1d11bb2")
APP_ID = os.getenv("DERIV_APP_ID", "33uQ6fU4eIRvJc6jkYeEa")
ACCOUNT_ID = os.getenv("DERIV_ACCOUNT_ID", "DOT92925029")


def make_client():
    from deriv.client import DerivWSClient
    return DerivWSClient(pat_token=PAT_TOKEN, app_id=APP_ID, account_id=ACCOUNT_ID)


async def cmd_test():
    from deriv.patterns import MomenPatternAnalyzer, AdjacencyPatternAnalyzer
    from collections import Counter

    client = make_client()
    ok = await client.connect()
    if not ok:
        print("❌ Connect failed"); return 1

    bal = await client.get_balance()
    print(f"💰  Balance: ${bal:.2f}")

    symbols = await client.get_active_symbols()
    print(f"📊  Symbols: {len(symbols)} active")

    ticks = await client.get_ticks_history("R_75", count=20)
    print(f"📈  R_75 last {len(ticks)} ticks:")
    for t in ticks[-10:]:
        print(f"    digit={t.digit}  ${t.price:.4f}")

    # Momen analyze
    ticks100 = await client.get_ticks_history("R_75", count=100)
    momen = MomenPatternAnalyzer()
    result = momen.analyze(ticks100)
    if result:
        print(f"\n🎯  Momen: carrier={result.carrier}  conf={result.confidence:.0%}")
    else:
        print("\n⚡  No Momen pattern found")

    # Digit frequency
    freq = Counter(t.digit for t in ticks100)
    print(f"\n📊  Digit distribution (last 100):")
    for d in range(10):
        print(f"    {d}: {'█' * freq.get(d, 0)} ({freq.get(d, 0)})")

    await client.disconnect()
    print("\n✅  Test complete")
    return 0


async def cmd_debug(symbol: str):
    client = make_client()
    await client.connect()
    print(f"📡  Debugging {symbol} (10 ticks)...")

    tick_queue = asyncio.Queue()
    client.on("tick", lambda t: tick_queue.put_nowait(t))
    await client.subscribe_ticks(symbol)

    count = 0
    while count < 10:
        tick = await asyncio.wait_for(tick_queue.get(), timeout=15)
        count += 1
        print(f"  {count:2d}.  digit={tick.digit}  ${tick.price:.4f}")

    await client._safe_send({"forget_all": ["ticks"]})

    # History
    hist = await client.get_ticks_history(symbol, count=20)
    print(f"\n📈  History: {len(hist)} ticks")
    for t in hist[-5:]:
        print(f"    digit={t.digit}  ${t.price:.4f}")

    await client.disconnect()
    print(f"\n✅  Done")
    return 0


async def cmd_trade(symbol: str):
    from deriv.strategy import DigitMartingaleStrategy

    client = make_client()
    await client.connect()
    print(f"💹  Trading {symbol} (1 cycle)...")

    strategy = DigitMartingaleStrategy(client=client, symbol=symbol)
    result = await strategy.analyse_and_trade()

    print(f"\n📊  Result:")
    print(f"    P/L: ${result.profit:+.2f}  ({result.wins}W/{result.losses}L)")
    print(f"    WR: {result.win_rate:.0f}%  Trades: {result.trades}")

    await client.disconnect()
    return 0


async def cmd_stream(symbol: str, duration: int = 30):
    client = make_client()
    await client.connect()

    tick_queue = asyncio.Queue()
    client.on("tick", lambda t: tick_queue.put_nowait(t))
    await client.subscribe_ticks(symbol)

    print(f"📡  Streaming {symbol} for {duration}s...")
    start = asyncio.get_event_loop().time()
    count = 0
    while asyncio.get_event_loop().time() - start < duration:
        tick = await asyncio.wait_for(tick_queue.get(), timeout=5)
        count += 1
        print(f"  {count:3d}.  digit={tick.digit}  ${tick.price:.4f}")

    await client.disconnect()
    print(f"\n✅  {count} ticks captured")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1]
    handlers = {
        "test": lambda: asyncio.run(cmd_test()),
        "debug": lambda: asyncio.run(cmd_debug(sys.argv[2] if len(sys.argv) > 2 else "R_75")),
        "trade": lambda: asyncio.run(cmd_trade(sys.argv[2] if len(sys.argv) > 2 else "R_75")),
        "stream": lambda: asyncio.run(cmd_stream(
            sys.argv[2] if len(sys.argv) > 2 else "R_75",
            int(sys.argv[3]) if len(sys.argv) > 3 else 30
        )),
    }

    handler = handlers.get(mode)
    if not handler:
        print(f"❌ Unknown mode: {mode}")
        print(__doc__)
        sys.exit(1)

    exit_code = handler()
    sys.exit(exit_code if exit_code else 0)


if __name__ == "__main__":
    main()
