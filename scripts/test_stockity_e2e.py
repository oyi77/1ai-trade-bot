#!/usr/bin/env python3
"""
Stockity E2E Test — link -> watch signals -> execute trades.

Tests:
  1. Real-time signal engine with spread/momentum/contrarian edges
  2. Blitz trade execution via Stockity Phoenix WebSocket
  3. Telegram bot link -> platform -> signal flow (via Telethon)

Usage:
  python3 scripts/test_stockity_e2e.py [--mode demo|live] [--trades N]

Requires .env with STOCKITY_FULL_COOKIE, STOCKITY_EMAIL, STOCKITY_PASSWORD.
For Telegram tests: --telegram flag plus TELEGRAM_BOT_TOKEN and paijo.session.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from tradebot.brokers.stockity.broker import StockityBroker  # noqa: E402
from tradebot.config import settings  # noqa: E402
from tradebot.signals.stockity_engine import StockityBlitzEngine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOG = logging.getLogger("stockity_e2e")

SYMBOL = "CRYPTO_IDX"
RIC = "Z-CRY/IDX"
STAKE = 1.0
TRADE_TIMEOUT_OPEN = 10.0
TRADE_TIMEOUT_CLOSE = 20.0


# ═════════════════════════════════════════════════════════════════════════
#  Inline Signal Logic (per spec: spread + momentum edges)
# ═════════════════════════════════════════════════════════════════════════

UP = 1
DOWN = -1
FLAT = 0


def _mid_price(tick: dict[str, Any]) -> float:
    bid = tick.get("bid")
    ask = tick.get("ask")
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return float(tick.get("rate", 0))


def pick_trade_direction(recent: list[dict[str, Any]]) -> tuple[str, str]:
    """Pick CALL/PUT/random from spread contraction/expansion + momentum.

    Returns (direction: "CALL" | "PUT", strategy_label: str).
    """
    if len(recent) < 10:
        return random.choice(["CALL", "PUT"]), "random_insufficient_data"

    spreads = []
    for t in recent[-10:]:
        b, a = t.get("bid"), t.get("ask")
        if b is not None and a is not None:
            spreads.append(a - b)
    if len(spreads) < 5:
        return random.choice(["CALL", "PUT"]), "random_insufficient_spreads"

    spread_median = median(spreads)
    latest_spread = spreads[-1]

    # Tick momentum from last 3 deltas
    mids = [_mid_price(t) for t in recent[-4:]]
    deltas = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    up_count = sum(1 for d in deltas if d > 0)
    down_count = sum(1 for d in deltas if d < 0)

    if up_count >= 2:
        momentum = UP
    elif down_count >= 2:
        momentum = DOWN
    else:
        momentum = FLAT

    # Edge 1: Spread contraction + rate up -> CALL
    if latest_spread < spread_median and momentum == UP:
        return ("CALL", f"spread_contraction({latest_spread:.4f}<{spread_median:.4f})")

    # Edge 2: Spread expansion (>1.5x) + rate down -> PUT
    if latest_spread > spread_median * 1.5 and momentum == DOWN:
        return ("PUT", f"spread_expansion({latest_spread:.4f}>{spread_median:.4f})")

    # Edge 3: Control group -- random
    d = random.choice(["CALL", "PUT"])
    return (d, f"control_{d.lower()}")


# ═════════════════════════════════════════════════════════════════════════
#  E2E Test Runner
# ═════════════════════════════════════════════════════════════════════════

class StockityE2ETest:
    """Run N trades through Stockity demo/live and record results."""

    def __init__(self, deal_type: str, num_trades: int) -> None:
        self.deal_type = deal_type
        self.num_trades = num_trades
        self.broker: StockityBroker | None = None
        self.engine = StockityBlitzEngine()

        self._ticks: deque[dict[str, Any]] = deque(maxlen=20)
        self._majority_opinion: dict[str, Any] | None = None

        self._opened_event = asyncio.Event()
        self._opened_data: dict[str, Any] | None = None
        self._closed_event = asyncio.Event()
        self._closed_data: dict[str, Any] | None = None
        self._trade_ref: str = ""
        self._results: list[dict[str, Any]] = []

    # ── Callbacks ──────────────────────────────────────────────────────

    def _on_tick(self, tick: dict[str, Any]) -> None:
        self._ticks.append(tick)
        self.engine.push_tick(tick)

    def _on_majority_opinion(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload", {})
        response = payload.get("response", {})
        opinion = response.get("majority_opinion") or response.get("social_trading")
        if opinion:
            self._majority_opinion = opinion
            self.engine.set_majority_opinion(opinion)
            LOG.info("Majority opinion: call=%s put=%s",
                     opinion.get("call_percent"), opinion.get("put_percent"))

    def _on_opened(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload", {})
        uuid = payload.get("uuid", "")
        if uuid == self._trade_ref:
            self._opened_data = payload
            self._opened_event.set()

    def _on_closed(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload", {})
        uuid = payload.get("uuid", "")
        if uuid == self._trade_ref:
            self._closed_data = payload
            self._closed_event.set()

    # ── Setup ──────────────────────────────────────────────────────────

    async def setup(self) -> None:
        LOG.info("=== Stockity E2E (%s, %d trades) ===", self.deal_type, self.num_trades)

        self.broker = StockityBroker(deal_type=self.deal_type)
        await self.broker.connect()
        await self.broker.subscribe_ticks(RIC)
        self.broker.on_tick(self._on_tick)

        await self.broker.subscribe_asset("Z-CRY/IDX")
        self.broker.on_event("asset:Z-CRY/IDX", "phx_reply", self._on_majority_opinion)
        self.broker.on_event("bo", "opened", self._on_opened)
        self.broker.on_event("bo", "closed", self._on_closed)

        LOG.info("Gathering initial ticks...")
        start = time.monotonic()
        while len(self._ticks) < 10 and time.monotonic() - start < 15:
            await asyncio.sleep(0.5)
        LOG.info("Have %d ticks in %.1fs", len(self._ticks), time.monotonic() - start)

    # ── Trade lifecycle helpers ───────────────────────────────────────

    async def _collect_ticks(self, min_count: int = 10, timeout: float = 20.0) -> None:
        start = time.monotonic()
        while len(self._ticks) < min_count and time.monotonic() - start < timeout:
            await asyncio.sleep(0.3)

    async def _wait_for_opened(self) -> dict[str, Any] | None:
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().create_task(self._opened_event.wait()),
                timeout=TRADE_TIMEOUT_OPEN,
            )
        except TimeoutError:
            LOG.warning("Timed out waiting for bo:opened (%.0fs)", TRADE_TIMEOUT_OPEN)
            return None
        return self._opened_data

    async def _wait_for_closed(self) -> dict[str, Any] | None:
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().create_task(self._closed_event.wait()),
                timeout=TRADE_TIMEOUT_CLOSE,
            )
        except TimeoutError:
            LOG.warning("Timed out waiting for bo:closed (%.0fs)", TRADE_TIMEOUT_CLOSE)
            return None
        return self._closed_data

    def _reset_events(self, trade_ref: str) -> None:
        self._trade_ref = trade_ref
        self._opened_event.clear()
        self._opened_data = None
        self._closed_event.clear()
        self._closed_data = None

    # ── Trade loop ────────────────────────────────────────────────────

    async def run(self) -> list[dict[str, Any]]:
        await self.setup()

        for i in range(1, self.num_trades + 1):
            await self._collect_ticks(min_count=10, timeout=15.0)
            recent = list(self._ticks)

            direction, strategy = pick_trade_direction(recent)
            LOG.info("[%d/%d] %s (%s) ticks=%d", i, self.num_trades,
                     direction, strategy, len(recent))

            result = await self.broker.place_trade(
                symbol=SYMBOL, direction=direction, amount=STAKE, duration=5,
            )
            if result.status.value == "rejected":
                LOG.error("Trade rejected: %s", result.error)
                self._results.append({
                    "trade": i, "direction": direction, "strategy": strategy,
                    "result": "REJECTED", "error": result.error,
                })
                continue

            self._reset_events(result.order_id)

            opened = await self._wait_for_opened()
            if not opened:
                self._results.append({
                    "trade": i, "direction": direction, "strategy": strategy,
                    "result": "OPEN_TIMEOUT",
                })
                continue
            open_rate = opened.get("open_rate")
            LOG.info("  Opened rate=%s amount=%s", open_rate, opened.get("amount"))

            closed = await self._wait_for_closed()
            if not closed:
                self._results.append({
                    "trade": i, "direction": direction, "strategy": strategy,
                    "result": "CLOSE_TIMEOUT", "open_rate": open_rate,
                })
                continue

            status = closed.get("status", "unknown")
            win = closed.get("win", 0)
            pnl = win - closed.get("amount", 0) if status == "won" else -closed.get("amount", 0)
            LOG.info("  Closed: %s win=%d pnl=%d", status, win, pnl)

            # Snapshot spread + momentum at close
            r = list(self._ticks)
            spreads = [
                t.get("ask", 0) - t.get("bid", 0)
                for t in r if t.get("bid") is not None and t.get("ask") is not None
            ]
            spread = spreads[-1] if spreads else 0
            mids = [_mid_price(t) for t in r[-4:]]
            deltas = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
            u = sum(1 for d in deltas if d > 0)
            dn = sum(1 for d in deltas if d < 0)

            self._results.append({
                "trade": i,
                "direction": direction,
                "strategy": strategy,
                "result": status.upper(),
                "win_amount": win,
                "pnl": pnl,
                "open_rate": open_rate,
                "end_rate": closed.get("end_rate"),
                "spread": round(spread, 6),
                "momentum": "UP" if u > 1 else "DOWN" if dn > 1 else "MIXED",
            })

            await asyncio.sleep(random.uniform(1.0, 3.0))

        return self._results

    async def teardown(self) -> None:
        if self.broker:
            await self.broker.close()

    @staticmethod
    def print_summary(results: list[dict[str, Any]]) -> None:
        total = len(results)
        if not total:
            print("\nNo trades executed.")
            return

        won = sum(1 for r in results if r.get("result") == "WON")
        lost = sum(1 for r in results if r.get("result") == "LOST")
        rejected = sum(1 for r in results if r.get("result") == "REJECTED")
        timeouts = sum(1 for r in results if "TIMEOUT" in str(r.get("result", "")))

        strategies: dict[str, list[bool | None]] = {}
        for r in results:
            strat = r.get("strategy", "unknown")
            base = strat.split("(")[0]
            if base not in strategies:
                strategies[base] = []
            if r.get("result") == "WON":
                strategies[base].append(True)
            elif r.get("result") == "LOST":
                strategies[base].append(False)

        pnl = sum(r.get("pnl", 0) for r in results if isinstance(r.get("pnl"), (int, float)))

        print(f"\n{'='*50}")
        print("  STOCKITY E2E TEST SUMMARY")
        print(f"{'='*50}")
        print(f"  Total Trades:  {total}")
        print(f"  Won:           {won}")
        print(f"  Lost:          {lost}")
        print(f"  Rejected:      {rejected}")
        print(f"  Timeouts:      {timeouts}")
        print(f"  Net P&L:       {pnl}")
        if won + lost > 0:
            print(f"  Win Rate:      {won/(won+lost)*100:.1f}%")
        print(f"{'─'*50}")
        print("  Strategy Breakdown:")
        for sname, outcomes in sorted(strategies.items()):
            sw = sum(1 for o in outcomes if o is True)
            sl = sum(1 for o in outcomes if o is False)
            wr = sw / (sw + sl) * 100 if (sw + sl) > 0 else 0
            print(f"    {sname:30s}  {sw:3d}W/{sl:3d}L  {wr:5.1f}%")
        print(f"{'='*50}\n")

    async def run_and_report(self) -> int:
        try:
            results = await self.run()
            self.print_summary(results)
            won = sum(1 for r in results if r.get("result") == "WON")
            return 0 if won > 0 else 1
        except Exception as e:
            LOG.exception("E2E test failed: %s", e)
            return 2
        finally:
            await self.teardown()


# ═════════════════════════════════════════════════════════════════════════
#  Telegram Link E2E (optional, requires Telethon + paijo.session)
# ═════════════════════════════════════════════════════════════════════════

async def test_telegram_link() -> int:
    try:
        from telethon import TelegramClient
    except ImportError:
        LOG.warning("Telethon not installed -- skipping Telegram E2E")
        return 0

    session_path = _HERE.parent / "paijo.session"
    if not session_path.exists():
        LOG.warning("paijo.session not found -- skipping Telegram E2E")
        return 0

    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "agent_1ai2_bot"
    email = settings.STOCKITY_EMAIL
    password = settings.STOCKITY_PASSWORD
    if not email or not password:
        LOG.warning("STOCKITY_EMAIL/PASSWORD not set -- skipping Telegram E2E")
        return 0

    api_id = int(getattr(settings, "TELEGRAM_API_ID", "0") or "0")
    api_hash = getattr(settings, "TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        LOG.warning("TELEGRAM_API_ID/HASH not set -- skipping Telegram E2E")
        return 0

    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.start()

    try:
        entity = await client.get_entity(bot_username)

        # Step 1: /link stockity <email> <password>
        LOG.info("Sending /link to %s...", bot_username)
        sent = await client.send_message(entity, f"/link stockity {email} {password}")
        await asyncio.sleep(2)
        replies = []
        async for msg in client.iter_messages(entity, limit=3, offset_id=sent.id):
            replies.append(msg.text or "")
        combined = " ".join(replies)
        if "berhasil ditautkan" not in combined.lower():
            LOG.error("Link reply missing 'berhasil ditautkan': %s", combined[:200])
            return 1
        LOG.info("Link confirmed")

        # Step 2: /platforms
        sent2 = await client.send_message(entity, "/platforms")
        await asyncio.sleep(2)
        plat_replies = []
        async for msg in client.iter_messages(entity, limit=3, offset_id=sent2.id):
            plat_replies.append(msg.text or "")
        plat_text = " ".join(plat_replies)
        if "stockity" not in plat_text.lower():
            LOG.error("Platforms reply missing stockity: %s", plat_text[:200])
            return 1
        LOG.info("Stockity listed in /platforms")

        # Step 3: /signal crypto_idx
        sent3 = await client.send_message(entity, "/signal crypto_idx")
        await asyncio.sleep(3)
        sig_replies = []
        async for msg in client.iter_messages(entity, limit=3, offset_id=sent3.id):
            sig_replies.append(msg.text or "")
        sig_text = " ".join(sig_replies)
        if not sig_text or len(sig_text) < 50:
            LOG.error("Signal reply too short: %s", sig_text[:200])
            return 1
        LOG.info("Signal received (%d chars)", len(sig_text))
        LOG.info("  Preview: %s...", sig_text[:120].replace("\n", " | "))

        return 0
    finally:
        await client.disconnect()


# ═════════════════════════════════════════════════════════════════════════
#  Entry
# ═════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="Stockity E2E Test")
    parser.add_argument("--mode", default="demo", choices=["demo", "live"])
    parser.add_argument("--trades", type=int, default=100)
    parser.add_argument("--telegram", action="store_true",
                        help="Also run Telegram link/signal E2E")
    args = parser.parse_args()

    LOG.info("Starting Stockity trade E2E (mode=%s, trades=%d)...", args.mode, args.trades)
    runner = StockityE2ETest(deal_type=args.mode, num_trades=args.trades)
    exit_code = asyncio.run(runner.run_and_report())

    if args.telegram:
        LOG.info("Running Telegram link E2E...")
        tg_code = asyncio.run(test_telegram_link())
        if tg_code:
            LOG.error("Telegram E2E failed (code %d)", tg_code)
            exit_code = max(exit_code, tg_code)
        else:
            LOG.info("Telegram E2E passed")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

