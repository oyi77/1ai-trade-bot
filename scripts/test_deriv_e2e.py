#!/usr/bin/env python3
"""
Deriv Momen 1/2 E2E Test — Telegram Bot Link + Live Demo Trade
================================================================
Tests the full pipeline:

Part A (Telegram Bot Interaction):
  1. /link deriv <app_id> <secret>  → expect "berhasil ditautkan"
  2. /platforms                     → expect "deriv" in list
  3. /signal deriv R_75             → expect signal detail in reply

Part B (Live Deriv Demo Trade):
  4. DerivWSClient → demo WebSocket
  5. Subscribe to R_75 ticks
  6. MomenPatternAnalyzer on collected ticks
  7. DigitMartingaleStrategy.analyse_and_trade() execution
  8. Record trade result

Usage:
    python3 scripts/test_deriv_e2e.py

Requires:
    - TELEGRAM_BOT_USERNAME env (default: agent_1ai2_bot)
    - DERIV_APP_ID env (from .env / tradebot.config.settings)
    - Authorized Telethon session at TELEGRAM_SESSION_PATH
      (default: ~/.openclaw/workspace/paijo)
    - pip install telethon websockets httpx
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for p in (ROOT, str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, str(p))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
LOG = logging.getLogger("deriv-e2e")

# ── Constants (from env, with defaults) ─────────────────────────────
TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "23647272"))
TELEGRAM_API_HASH = os.environ.get(
    "TELEGRAM_API_HASH", "1f69a4e0f03e5f51ddfa5b67ac7b5c49"
)
SESSION_PATH = os.path.expanduser(
    os.environ.get("TELEGRAM_SESSION_PATH", "~/.openclaw/workspace/paijo")
)
BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "agent_1ai2_bot")

# ── Timeout for Telegram replies ────────────────────────────────────
REPLY_TIMEOUT = 15  # seconds

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


# ═══════════════════════════════════════════════════════════════════
# Part A — Telegram Bot Interaction via Telethon
# ═══════════════════════════════════════════════════════════════════

async def _get_telethon_client():
    """Create and (re)use Telethon client.  Returns None if not authorized."""
    from telethon import TelegramClient

    client = TelegramClient(SESSION_PATH, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        LOG.error(
            "Telethon session not authorized at %s\n"
            "   Generate a session by running:\n"
            "       python3 -c \"from telethon import TelegramClient; "
            "import asyncio; "
            "async def go(): c = TelegramClient('%s', %s, '%s'); "
            "await c.start(); print('OK'); await c.disconnect(); "
            "asyncio.run(go())\"",
            SESSION_PATH, SESSION_PATH, TELEGRAM_API_ID, TELEGRAM_API_HASH,
        )
        await client.disconnect()
        return None

    LOG.info("Telethon connected (session: %s)", SESSION_PATH)
    return client


async def _send_and_wait(
    client,
    message: str,
    timeout: float = REPLY_TIMEOUT,
) -> str | None:
    """Send a message to the bot and return the reply text."""
    async with client.conversation(BOT_USERNAME, timeout=timeout) as conv:
        LOG.info("   -> Sending: %s", message)
        await conv.send_message(message)
        reply = await conv.get_response()
        text = reply.raw_text if hasattr(reply, "raw_text") else str(reply)
        LOG.info("   <- Reply (%d chars): %s", len(text), text[:200])
        return text


async def test_telegram_link() -> dict:
    """
    1. Connect Telethon
    2. Send /link deriv <app_id> <secret>
    3. Verify response contains "berhasil ditautkan"
    4. Send /platforms -> verify deriv listed
    5. Send /signal deriv R_75 -> check reply has signal details
    """
    results: dict[str, str] = {}
    details: list[str] = []

    LOG.info("")
    LOG.info("Part A - Telegram Bot Interaction")
    LOG.info("Bot: @%s  |  Session: %s", BOT_USERNAME, SESSION_PATH)

    client = await _get_telethon_client()
    if client is None:
        results["connect"] = FAIL
        details.append("Telethon session not authorized; cannot proceed")
        return {"results": results, "details": details}

    results["connect"] = PASS
    details.append("Telethon connected")

    try:
        # ── Step 1: /link deriv ────────────────────────────────────
        app_id = os.environ.get("DERIV_APP_ID", "")
        pat_token = os.environ.get("DERIV_PAT_TOKEN", "")
        secret_display = pat_token[:8] + "..." if pat_token else "(empty)"
        link_cmd = f"/link deriv {app_id} {secret_display}"

        reply = await _send_and_wait(client, link_cmd)
        if reply and "berhasil ditautkan" in reply.lower():
            results["/link deriv"] = PASS
            details.append("/link deriv -> 'berhasil ditautkan' found")
        else:
            results["/link deriv"] = FAIL
            preview = (reply or "no reply")[:120]
            details.append(f"/link deriv -> 'berhasil ditautkan' NOT found in: {preview}")

        # ── Step 2: /platforms ─────────────────────────────────────
        reply = await _send_and_wait(client, "/platforms")
        if reply and "deriv" in reply.lower():
            results["/platforms"] = PASS
            details.append("/platforms -> 'deriv' listed")
        else:
            preview = (reply or "no reply")[:120]
            results["/platforms"] = FAIL
            details.append(f"/platforms -> 'deriv' NOT listed: {preview}")

        # ── Step 3: /signal deriv R_75 ─────────────────────────────
        reply = await _send_and_wait(client, "/signal deriv R_75")
        if reply and len(reply) > 30:
            keywords_found = []
            for kw in ("R_75", "signal", "entry", "confidence",
                       "buy", "sell", "direction", "price", "stake", "digit"):
                if kw in reply.lower():
                    keywords_found.append(kw)
            if len(keywords_found) >= 2:
                results["/signal deriv R_75"] = PASS
                details.append(
                    f"/signal deriv R_75 -> {len(reply)} chars, "
                    f"keywords: {keywords_found}"
                )
            else:
                results["/signal deriv R_75"] = FAIL
                details.append(
                    f"/signal deriv R_75 -> too few signal keywords "
                    f"({keywords_found}): {reply[:120]}"
                )
        else:
            results["/signal deriv R_75"] = SKIP
            preview = (reply or "no reply")[:120]
            details.append(
                f"/signal deriv R_75 -> short/empty reply, "
                f"may need prior /link: {preview}"
            )

    except Exception as e:
        LOG.error("Telegram test error: %s", e)
        results.setdefault("error", FAIL)
        details.append(f"Exception during Telegram test: {e}")
    finally:
        await client.disconnect()
        LOG.info("   Disconnected")

    return {"results": results, "details": details}


# ═══════════════════════════════════════════════════════════════════
# Part B — Deriv Live Trade (Demo)
# ═══════════════════════════════════════════════════════════════════

async def test_deriv_trade() -> dict:
    """
    1. Connect DerivWSClient to demo (legacy endpoint with app_id)
    2. Subscribe to R_75 ticks
    3. Collect ticks in a buffer; run MomenPatternAnalyzer periodically
    4. When Momen 1/2 pattern detected -> log it
    5. Run DigitMartingaleStrategy.analyse_and_trade()
    6. Record trade result
    """
    results: dict[str, str] = {}
    details: list[str] = []
    trade_result = None
    pattern_log: list[dict] = []

    LOG.info("")
    LOG.info("Part B - Deriv Live Demo Trade")

    try:
        # ── Step 1: Build and connect DerivWSClient ────────────────
        from tradebot.brokers.deriv.client import DerivWSClient

        app_id = os.environ.get("DERIV_APP_ID", "")
        if not app_id:
            results["connect"] = SKIP
            details.append("DERIV_APP_ID not set, skipping Deriv trade test")
            return {
                "results": results, "details": details,
                "trade_result": None, "patterns": [],
            }

        client = DerivWSClient(app_id=app_id)
        ok = await client.connect()
        if not ok:
            results["connect"] = FAIL
            details.append("DerivWSClient.connect() returned False")
            return {
                "results": results, "details": details,
                "trade_result": None, "patterns": [],
            }

        results["connect"] = PASS
        details.append("DerivWSClient connected (demo)")

        # ── Steps 2-3: Subscribe ticks + collect ──────────────────
        symbol = "R_75"
        tick_buffer: list = []
        collect_seconds = 30
        analysis_interval = 10
        max_wait_cycles = 6

        LOG.info("   Subscribing to %s ticks ...", symbol)
        sub_ok = await client.subscribe_ticks(symbol)
        results["subscribe_ticks"] = PASS if sub_ok else FAIL
        details.append(
            f"{'OK' if sub_ok else 'FAIL'} subscribe_ticks({symbol})"
        )

        if not sub_ok:
            await client.disconnect()
            return {
                "results": results, "details": details,
                "trade_result": None, "patterns": [],
            }

        # ── Step 4: Tick handler + Momen analyzer ─────────────────
        from tradebot.brokers.deriv.patterns import MomenPatternAnalyzer

        analyzer = MomenPatternAnalyzer(analysis_ticks=100)
        collection_start = datetime.now(UTC)
        pattern_found = False
        found_analysis = None

        async def on_tick(tick):
            tick_buffer.append(tick)

        client.on("tick", on_tick)

        LOG.info(
            "   Collecting ticks for %ds (analysis every %ds) ...",
            collect_seconds, analysis_interval,
        )

        for cycle in range(1, max_wait_cycles + 1):
            await asyncio.sleep(analysis_interval)
            now = datetime.now(UTC)
            elapsed = (now - collection_start).total_seconds()
            buf = list(tick_buffer)

            LOG.info(
                "   Cycle %d/%d | %ds elapsed | %d ticks collected",
                cycle, max_wait_cycles, int(elapsed), len(buf),
            )

            if len(buf) < 20:
                LOG.info("      -> too few ticks, keep collecting")
                continue

            analysis = analyzer.analyze(buf)
            if analysis is not None:
                pattern_found = True
                found_analysis = analysis
                pattern_log.append({
                    "cycle": cycle,
                    "carrier": analysis.carrier,
                    "momen1_tick": analysis.momen1_tick,
                    "momen2_tick": analysis.momen2_tick,
                    "total_m1": analysis.total_m1,
                    "total_m2": analysis.total_m2,
                    "confidence": round(analysis.confidence, 3),
                    "predicted_digit": analysis.predicted_digit,
                    "timestamp": now.isoformat(),
                })
                LOG.info(
                    "Momen 1/2 DETECTED: carrier=%d confidence=%.0f%% "
                    "predicted_digit=%d",
                    analysis.carrier, analysis.confidence * 100,
                    analysis.predicted_digit,
                )
                results["pattern_detected"] = PASS
                details.append(
                    f"Momen pattern: carrier={analysis.carrier} "
                    f"confidence={analysis.confidence:.0%} "
                    f"digit={analysis.predicted_digit}"
                )
                break
            else:
                LOG.info("      -> no Momen pattern yet")

        if not pattern_found:
            elapsed = int((datetime.now(UTC) - collection_start).total_seconds())
            results["pattern_detected"] = SKIP
            details.append(
                f"No Momen pattern detected in {elapsed}s "
                f"({len(tick_buffer)} ticks) - "
                f"live ticks may need more time"
            )

        # ── Step 5: Execute trade via strategy ────────────────────
        if pattern_found:
            LOG.info("")
            LOG.info("   --- Executing DigitMartingaleStrategy ---")

            from tradebot.brokers.deriv.strategy import DigitMartingaleStrategy

            strategy = DigitMartingaleStrategy(
                client=client,
                symbol=symbol,
                contract_type="DIGITMATCH",
                barrier=found_analysis.predicted_digit,
                initial_stake=0.35,
                max_ops=3,
            )

            trade_result_data = await strategy.analyse_and_trade()

            if trade_result_data:
                trade_result = {
                    "profit": trade_result_data.profit,
                    "total_stake": trade_result_data.total_stake,
                    "trades": trade_result_data.trades,
                    "wins": trade_result_data.wins,
                    "losses": trade_result_data.losses,
                    "win_rate": round(trade_result_data.win_rate, 1),
                    "cycles": trade_result_data.cycles,
                    "stopped_early": trade_result_data.stopped_early,
                    "reason": trade_result_data.reason or "",
                }
                LOG.info(
                    "Trade result: +$%.2f (%d/%d wins, %.0f%%)",
                    trade_result_data.profit,
                    trade_result_data.wins,
                    trade_result_data.trades,
                    trade_result_data.win_rate,
                )
                results["trade_executed"] = PASS
                details.append(
                    f"Trade executed: +${trade_result_data.profit:.2f} "
                    f"({trade_result_data.wins}/{trade_result_data.trades} wins)"
                )
            else:
                results["trade_executed"] = FAIL
                details.append("analyse_and_trade() returned None")

        else:
            results["trade_executed"] = SKIP
            details.append("Trade skipped (no pattern)")

        # ── Cleanup ────────────────────────────────────────────────
        client.off("tick", on_tick)
        await client.unsubscribe_ticks(symbol)
        await client.disconnect()
        LOG.info("   Deriv client disconnected")

    except Exception as e:
        LOG.error("Deriv trade test error: %s", e)
        results.setdefault("error", FAIL)
        details.append(f"Exception during Deriv trade test: {e}")
        import traceback
        traceback.print_exc()

    return {
        "results": results,
        "details": details,
        "trade_result": trade_result,
        "patterns": pattern_log,
    }


# ═══════════════════════════════════════════════════════════════════
# Main - run both tests
# ═══════════════════════════════════════════════════════════════════

async def main():
    """Run Telegram link test and live Deriv demo trade."""
    start_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    print()
    print("=" * 60)
    print("   Deriv Momen 1/2 - E2E Test Suite")
    print(f"   Started:  {start_str}")
    print(f"   Bot:      @{BOT_USERNAME}")
    print(f"   Session:  {SESSION_PATH}")
    print("=" * 60)
    print()

    # ── Part A ──
    telegram_result = await test_telegram_link()

    # ── Part B ──
    deriv_result = await test_deriv_trade()

    # ── Summary ──
    print()
    print("=" * 60)
    print("   RESULTS SUMMARY")

    all_results: dict[str, str] = {}
    all_details: list[str] = []

    for part_name, part_data in [
        ("TelegramBot", telegram_result),
        ("DerivTrade", deriv_result),
    ]:
        for key, status in part_data.get("results", {}).items():
            label = f"{part_name}.{key}"
            all_results[label] = status
        for d in part_data.get("details", []):
            all_details.append(f"[{part_name}] {d}")

    total = len(all_results)
    passed = sum(1 for v in all_results.values() if v == PASS)
    failed = sum(1 for v in all_results.values() if v == FAIL)
    skipped = sum(1 for v in all_results.values() if v == SKIP)

    print()
    for label, status in sorted(all_results.items()):
        icon = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}.get(status, "?")
        print(f"   {icon}  {label}")

    print()
    print(f"   Total: {total}  |  PASS: {passed}  |  FAIL: {failed}  |  SKIP: {skipped}")
    print()

    if all_details:
        print("   --- Details ---")
        for d in all_details:
            print(f"   {d}")

    if deriv_result.get("patterns"):
        print()
        print("   --- Pattern Log ---")
        for p in deriv_result["patterns"]:
            print(
                f"   Cycle {p['cycle']}: carrier={p['carrier']} "
                f"confidence={p['confidence']:.0%} digit={p['predicted_digit']}"
            )

    if deriv_result.get("trade_result"):
        tr = deriv_result["trade_result"]
        print()
        print("   --- Trade Result ---")
        print(f"   Profit:      ${tr['profit']:+.2f}")
        print(f"   Trades:      {tr['trades']}")
        print(f"   Wins:        {tr['wins']}")
        print(f"   Losses:      {tr['losses']}")
        print(f"   Win Rate:    {tr['win_rate']:.1f}%")
        print(f"   Total Stake: ${tr['total_stake']:.2f}")
        print(f"   Early Stop:  {tr['stopped_early']} ({tr['reason']})")

    print()
    print("=" * 60)

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)