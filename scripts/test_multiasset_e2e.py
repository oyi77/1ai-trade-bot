#!/usr/bin/env python3
"""
Multi-Asset Rotation E2E — Telegram commands + auto trade on top 5 assets.

Usage: python3 scripts/test_multiasset_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot.brokers.stockity.broker import StockityBroker
from tradebot.signals.portfolio_oracle import get_best_asset_for_now


TOP5 = ["POWER-X", "CADSEK", "GBPSGD", "CHFNOK", "KNOUT-X"]
TRADES = 5
TELEGRAM_SESSION = os.path.expanduser("~/.openclaw/workspace/paijo")
API_ID = 23647272
API_HASH = "1f69a4e0f03e5f51ddfa5b67ac7b5c49"
BOT = "agent_1ai2_bot"


async def tg_send(client, cmd: str) -> str | None:
    await client.send_message(BOT, cmd)
    await asyncio.sleep(3)
    async for msg in client.iter_messages(BOT, limit=1):
        if not msg.out:
            return msg.text[:500]
    return None


async def test_telegram():
    from telethon import TelegramClient

    client = TelegramClient(TELEGRAM_SESSION, API_ID, API_HASH)
    await client.connect()
    print(f"\n{'='*60}")
    print("  TELEGRAM COMMANDS E2E")
    print(f"{'='*60}")

    results = []

    # /portfolio
    print("\n  ▶ /portfolio")
    r = await tg_send(client, "/portfolio")
    if r and any(a in r for a in TOP5):
        print(f"     ✅ Asset list found")
        results.append(("portfolio", "PASS", ""))
    else:
        print(f"     ⚠️  {r[:80] if r else 'no reply'}")
        results.append(("portfolio", "WARN", "no assets in reply"))

    # /trade POWER-X
    print("\n  ▶ /trade POWER-X")
    r = await tg_send(client, "/trade POWER-X")
    if r and "error" not in r.lower():
        print(f"     ✅ Trade triggered")
        results.append(("trade", "PASS", ""))
    else:
        print(f"     ⚠️  {r[:80] if r else 'no reply'}")
        results.append(("trade", "WARN", r[:80] if r else ""))

    # Best asset
    oracle = get_best_asset_for_now()
    best_ric = oracle.get("ric", "POWER-X")
    print(f"\n  ▶ /trade {best_ric}")
    r = await tg_send(client, f"/trade {best_ric}")
    results.append(("oracle_trade", "PASS" if r and "error" not in r.lower() else "WARN", ""))

    await client.disconnect()

    print(f"\n  {'─'*40}")
    for name, status, detail in results:
        print(f"  {status:4s} {name}")
    print(f"{'='*60}\n")


async def test_auto_rotation():
    print(f"{'='*60}")
    print("  AUTO ROTATION — top assets")
    print(f"{'='*60}")

    broker = StockityBroker(deal_type="demo")
    await broker.connect()
    stake = 14000.0 if broker.balance_currency == "IDR" else 1.0
    results = []

    for i in range(TRADES):
        asset = await get_best_asset_for_now()
        ric = asset.get("ric", TOP5[i % len(TOP5)])
        win = asset.get("win", 5)
        thr = asset.get("thr", 0.50)
        print(f"\n  [{i+1}/{TRADES}] {ric} (win={win} thr={thr}) ... ", end="", flush=True)

        opened = asyncio.Future()
        closed = asyncio.Future()
        broker._event_handlers.clear()

        def on_opened(msg, _o=opened):
            if not _o.done(): _o.set_result(msg.get("payload", {}))

        def on_closed(msg, _c=closed):
            if not _c.done(): _c.set_result(msg.get("payload", {}))

        broker.on_event("bo", "opened", on_opened)
        broker.on_event("bo", "closed", on_closed)

        trade = await broker.place_trade(symbol=ric, direction="CALL" if i % 2 == 0 else "PUT", amount=stake, duration=60, option_type="turbo")
        st = getattr(trade.status, "value", "")
        if st in ("rejected", "REJECTED"):
            print("REJECTED")
            results.append({"ric": ric, "result": "REJECTED"})
            continue

        try:
            await asyncio.wait_for(opened, timeout=15)
        except TimeoutError:
            print("OPEN_TIMEOUT")
            results.append({"ric": ric, "result": "OPEN_TIMEOUT"})
            continue

        try:
            cl = await asyncio.wait_for(closed, timeout=25)
        except TimeoutError:
            print("CLOSE_TIMEOUT")
            results.append({"ric": ric, "result": "CLOSE_TIMEOUT"})
            continue

        status = cl.get("status", "lost")
        win = cl.get("win", 0)
        pnl = win - cl.get("amount", 0) if status == "won" else -cl.get("amount", 0)
        outcome = "WON" if status == "won" else "LOST" if status == "lost" else "TIE"
        print(f"{outcome:4s} pnl={pnl}")
        results.append({"ric": ric, "result": outcome, "pnl": pnl})
        await asyncio.sleep(random.uniform(2, 4))

    await broker.close()

    # Summary
    valid = [r for r in results if r.get("result") in ("WON", "LOST")]
    won = sum(1 for r in valid if r["result"] == "WON")
    pnl = sum(r.get("pnl", 0) for r in valid)
    wr = won / max(len(valid), 1) * 100
    print(f"\n  {'─'*40}")
    print(f"  Total trades: {len(results)}")
    print(f"  WR: {wr:.1f}% ({won}/{len(valid)})")
    print(f"  P&L: {pnl}")
    print(f"{'='*60}\n")


async def main():
    await test_telegram()
    await asyncio.sleep(2)
    await test_auto_rotation()


if __name__ == "__main__":
    asyncio.run(main())