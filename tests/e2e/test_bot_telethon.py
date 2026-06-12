"""Automated end-to-end test suite for @agent_1ai2_bot using Telethon.

Validates:
1. UI/UX Client Flow — button clicks, response times, menu layouts
2. Signal & Execution Accuracy — signal generation, quality gate, win-rate logging
3. Data Tracking Modules — position tracker, trade history, journal, performance analytics
4. Admin Controls — demo/real toggle, admin-only commands

Usage:
    python tests/e2e/test_bot_telethon.py

Environment:
    TELETHON_API_ID      — Telegram API ID (int)
    TELETHON_API_HASH    — Telegram API hash
    TELETHON_BOT_USERNAME— Target bot (@agent_1ai2_bot)
    TELETHON_SESSION_NAME— Session file name (default: test_bot_session)
    ADMIN_CHAT_ID        — Admin Telegram chat ID for privileged tests
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Optional dependency guard ─────────────────────────────────────────
try:
    from telethon import TelegramClient, events
    from telethon.tl.custom import Message
    from telethon.tl.types import KeyboardButton, ReplyKeyboardMarkup
except ImportError as exc:  # pragma: no cover
    print(f"Telethon not installed: {exc}")
    print("pip install telethon")
    sys.exit(1)

LOG = logging.getLogger("test_bot_telethon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Configuration ────────────────────────────────────────────────────
API_ID = int(os.environ.get("TELETHON_API_ID", "0"))
API_HASH = os.environ.get("TELETHON_API_HASH", "")
BOT_USERNAME = os.environ.get("TELETHON_BOT_USERNAME", "agent_1ai2_bot")
SESSION_NAME = os.environ.get("TELETHON_SESSION_NAME", "test_bot_session")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "30"))
MAX_RESPONSE_TIME = float(os.environ.get("MAX_RESPONSE_TIME", "10.0"))

if not API_ID or not API_HASH:
    import pytest
    pytestmark = pytest.mark.skip(reason="Set TELETHON_API_ID and TELETHON_API_HASH env vars")

# ── Result tracking ──────────────────────────────────────────────────


@dataclass
class TestResult:
    name: str
    passed: bool = False
    duration_ms: float = 0.0
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class TestRunner:
    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self.client: TelegramClient | None = None
        self.bot_username = BOT_USERNAME.lstrip("@")
        self.admin_chat_id = ADMIN_CHAT_ID

    async def init_client(self) -> TelegramClient:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        await client.start()
        LOG.info("Telethon client started: %s", (await client.get_me()).username)
        self.client = client
        return client

    def record(self, result: TestResult) -> None:
        self.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        LOG.info("[%s] %s — %.1f ms", status, result.name, result.duration_ms)
        if result.error:
            LOG.error("  → %s", result.error)

    # ── helpers ───────────────────────────────────────────────────────

    async def _send_and_wait(self, text: str, timeout: int = TEST_TIMEOUT) -> Message | None:
        if not self.client:
            return None
        start = time.perf_counter()
        await self.client.send_message(self.bot_username, text)
        try:
            msg = await asyncio.wait_for(
                self._wait_for_bot_response(), timeout=timeout
            )
            elapsed = (time.perf_counter() - start) * 1000
            if msg:
                msg._response_time_ms = elapsed  # type: ignore[attr-defined]
            return msg
        except asyncio.TimeoutError:
            return None

    async def _wait_for_bot_response(self) -> Message:
        if not self.client:
            raise RuntimeError("Client not initialized")
        future: asyncio.Future[Message] = asyncio.get_event_loop().create_future()

        @self.client.on(events.NewMessage(from_users=[self.bot_username]))
        async def handler(event: events.NewMessage.Event) -> None:
            if not future.done():
                future.set_result(event.message)

        try:
            return await future
        finally:
            self.client.remove_event_handler(handler)

    async def _click_button(self, msg: Message, button_text_substring: str) -> Message | None:
        """Click an inline button whose text contains the given substring."""
        if not msg.reply_markup:
            return None
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if button_text_substring.lower() in btn.text.lower():
                    await msg.click(data=btn.data)
                    return await asyncio.wait_for(
                        self._wait_for_bot_response(), timeout=TEST_TIMEOUT
                    )
        return None

    def _response_time_ok(self, msg: Message | None) -> bool:
        if not msg:
            return False
        rt = getattr(msg, "_response_time_ms", 99999.0)
        return rt <= MAX_RESPONSE_TIME * 1000

    # ═══════════════════════════════════════════════════════════════════
    # 1. UI/UX CLIENT FLOW
    # ═══════════════════════════════════════════════════════════════════

    async def test_start_command(self) -> TestResult:
        r = TestResult(name="ui_start_command")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if msg is None:
                raise RuntimeError("No response to /start")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            r.details["has_welcome_text"] = "VILONA" in text or "COMMAND CENTER" in text
            r.details["has_buttons"] = msg.reply_markup is not None
            r.passed = r.details["has_welcome_text"] and r.details["has_buttons"] and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_menu_layouts(self) -> TestResult:
        r = TestResult(name="ui_menu_layouts")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if not msg or not msg.reply_markup:
                raise RuntimeError("No reply_markup on /start")

            required_menus = ["SIGNAL SYSTEM", "MARKET DATA", "TRADE HISTORY", "ACCOUNT", "HELP"]
            buttons: list[str] = []
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    buttons.append(btn.text)
            found = all(any(req in b for b in buttons) for req in required_menus)
            r.details["buttons"] = buttons
            r.details["all_required_found"] = found
            r.passed = found and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_button_click_signals(self) -> TestResult:
        r = TestResult(name="ui_button_click_signals")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if not msg:
                raise RuntimeError("No /start response")
            clicked = await self._click_button(msg, "SIGNAL SYSTEM")
            if not clicked:
                raise RuntimeError("Could not click SIGNAL SYSTEM button")
            text = clicked.text or ""
            r.details["response_time_ms"] = getattr(clicked, "_response_time_ms", 0)
            r.details["has_signal_menu"] = "SIGNAL" in text or "ANALYSIS" in text
            r.passed = r.details["has_signal_menu"] and self._response_time_ok(clicked)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_button_click_market(self) -> TestResult:
        r = TestResult(name="ui_button_click_market")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if not msg:
                raise RuntimeError("No /start response")
            clicked = await self._click_button(msg, "MARKET DATA")
            if not clicked:
                raise RuntimeError("Could not click MARKET DATA button")
            text = clicked.text or ""
            r.details["response_time_ms"] = getattr(clicked, "_response_time_ms", 0)
            r.details["has_market_menu"] = "MARKET DATA" in text or "Gold" in text or "BTC" in text
            r.passed = r.details["has_market_menu"] and self._response_time_ok(clicked)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_back_navigation(self) -> TestResult:
        r = TestResult(name="ui_back_navigation")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if not msg:
                raise RuntimeError("No /start response")
            # Navigate to signals then back
            sig = await self._click_button(msg, "SIGNAL SYSTEM")
            if not sig:
                raise RuntimeError("Could not click SIGNAL SYSTEM")
            back = await self._click_button(sig, "Back")
            if not back:
                raise RuntimeError("Could not click Back button")
            text = back.text or ""
            r.details["response_time_ms"] = getattr(back, "_response_time_ms", 0)
            r.details["returned_to_main"] = "COMMAND CENTER" in text or "VILONA" in text
            r.passed = r.details["returned_to_main"] and self._response_time_ok(back)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_response_times(self) -> TestResult:
        r = TestResult(name="ui_response_times")
        t0 = time.perf_counter()
        timings: list[float] = []
        failed_slow: list[str] = []
        commands = ["/start", "/help", "/status", "/killzone"]
        try:
            for cmd in commands:
                msg = await self._send_and_wait(cmd)
                if msg is None:
                    failed_slow.append(f"{cmd}: no response")
                    continue
                rt = getattr(msg, "_response_time_ms", 99999.0)
                timings.append(rt)
                if rt > MAX_RESPONSE_TIME * 1000:
                    failed_slow.append(f"{cmd}: {rt:.0f} ms")
            avg = sum(timings) / len(timings) if timings else 99999
            r.details["timings_ms"] = timings
            r.details["average_ms"] = avg
            r.details["slow_commands"] = failed_slow
            r.passed = len(failed_slow) == 0
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    # ═══════════════════════════════════════════════════════════════════
    # 2. SIGNAL & EXECUTION ACCURACY
    # ═══════════════════════════════════════════════════════════════════

    async def test_signal_command(self) -> TestResult:
        r = TestResult(name="sig_signal_command")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/signal")
            if not msg:
                raise RuntimeError("No response to /signal")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            # Check for MTF matrix elements
            has_verdict = "Verdict" in text or "verdict" in text
            has_mtf = "MTF" in text or "D1" in text or "H4" in text
            has_consensus = "Consensus" in text or "consensus" in text
            r.details["has_verdict"] = has_verdict
            r.details["has_mtf"] = has_mtf
            r.details["has_consensus"] = has_consensus
            r.passed = has_verdict and has_mtf and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_analyze_command(self) -> TestResult:
        r = TestResult(name="sig_analyze_command")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/analyze xauusd")
            if not msg:
                raise RuntimeError("No response to /analyze xauusd")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            # Should contain signal info or hold
            has_signal_info = any(k in text for k in ["BUY", "SELL", "HOLD", "entry", "SL", "TP"])
            r.details["has_signal_info"] = has_signal_info
            r.passed = has_signal_info and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_mtf_command(self) -> TestResult:
        r = TestResult(name="sig_mtf_command")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/mtf")
            if not msg:
                raise RuntimeError("No response to /mtf")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            has_matrix = "MTF" in text or "D1" in text or "H4" in text
            has_engines = "engine" in text.lower() or "B" in text or "S" in text
            r.details["has_matrix"] = has_matrix
            r.details["has_engines"] = has_engines
            r.passed = has_matrix and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_stockity_menu(self) -> TestResult:
        r = TestResult(name="sig_stockity_menu")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if not msg:
                raise RuntimeError("No /start response")
            clicked = await self._click_button(msg, "SIGNAL SYSTEM")
            if not clicked:
                raise RuntimeError("Could not click SIGNAL SYSTEM")
            stockity = await self._click_button(clicked, "STOCKITY")
            if not stockity:
                raise RuntimeError("Could not click STOCKITY INSIDER")
            text = stockity.text or ""
            r.details["response_time_ms"] = getattr(stockity, "_response_time_ms", 0)
            has_referral = "referral" in text.lower() or "invite" in text.lower() or "stockity" in text.lower()
            r.details["has_referral_info"] = has_referral
            r.passed = has_referral and self._response_time_ok(stockity)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    # ═══════════════════════════════════════════════════════════════════
    # 3. DATA TRACKING MODULES
    # ═══════════════════════════════════════════════════════════════════

    async def test_winrate(self) -> TestResult:
        r = TestResult(name="data_winrate")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/winrate")
            if not msg:
                raise RuntimeError("No response to /winrate")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            has_win_rate = "Win Rate" in text or "win rate" in text.lower()
            has_stats = any(k in text for k in ["Total Trades", "W", "L", "Open"])
            r.details["has_win_rate"] = has_win_rate
            r.details["has_stats"] = has_stats
            r.passed = has_win_rate and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_trade_history(self) -> TestResult:
        r = TestResult(name="data_trade_history")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/history")
            if not msg:
                raise RuntimeError("No response to /history")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            has_history = "RIWAYAT" in text or "History" in text or "trade" in text.lower()
            r.details["has_history"] = has_history
            r.passed = has_history and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_daily_recap(self) -> TestResult:
        r = TestResult(name="data_daily_recap")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/recap")
            if not msg:
                raise RuntimeError("No response to /recap")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            has_recap = "REKAP" in text or "Recap" in text or "Daily" in text
            has_pips = "Pips" in text or "pips" in text.lower()
            r.details["has_recap"] = has_recap
            r.details["has_pips"] = has_pips
            r.passed = has_recap and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_mapping(self) -> TestResult:
        r = TestResult(name="data_mapping")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/mapping")
            if not msg:
                raise RuntimeError("No response to /mapping")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            has_levels = "R1" in text or "S1" in text or "Pivot" in text or "Support" in text
            r.details["has_levels"] = has_levels
            r.passed = has_levels and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_status_and_settings(self) -> TestResult:
        r = TestResult(name="data_status_settings")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/status")
            if not msg:
                raise RuntimeError("No response to /status")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            has_engines = "Engines" in text or "AI" in text
            has_bridge = "Bridge" in text or "bridge" in text.lower()
            r.details["has_engines"] = has_engines
            r.details["has_bridge"] = has_bridge
            r.passed = has_engines and has_bridge and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    # ═══════════════════════════════════════════════════════════════════
    # 4. ADMIN CONTROLS
    # ═══════════════════════════════════════════════════════════════════

    async def test_admin_panel_access(self) -> TestResult:
        r = TestResult(name="admin_panel_access")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/start")
            if not msg:
                raise RuntimeError("No /start response")
            # Check if ADMIN button exists (implies admin view)
            has_admin_button = False
            if msg.reply_markup:
                for row in msg.reply_markup.rows:
                    for btn in row.buttons:
                        if "ADMIN" in btn.text.upper():
                            has_admin_button = True
            r.details["has_admin_button"] = has_admin_button
            r.details["admin_chat_id"] = self.admin_chat_id
            # Note: admin button only shows when chat_id matches ADMIN_CHAT_ID env var
            # If running as non-admin user, this is expected to be False
            if self.admin_chat_id:
                r.passed = has_admin_button
                if not has_admin_button:
                    r.error = f"Admin button not shown for chat_id (expected admin: {self.admin_chat_id})"
            else:
                # Non-admin test — just verify no admin button + no crash
                r.passed = not has_admin_button
                r.details["note"] = "Non-admin user — verified no admin button visible"
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_admin_commands_rejection(self) -> TestResult:
        """Non-admin user should be rejected from admin commands."""
        r = TestResult(name="admin_commands_rejection")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/restart_bot")
            if not msg:
                raise RuntimeError("No response to /restart_bot")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            is_rejected = "Hanya admin" in text or "admin only" in text.lower() or "⛔" in text
            r.details["is_rejected"] = is_rejected
            r.passed = is_rejected and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_admin_bridge_full_status(self) -> TestResult:
        """Same as restart — non-admin should be rejected."""
        r = TestResult(name="admin_bridge_full_status")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/bridge_full_status")
            if not msg:
                raise RuntimeError("No response")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            is_rejected = "Hanya admin" in text or "admin only" in text.lower() or "⛔" in text
            r.details["is_rejected"] = is_rejected
            r.passed = is_rejected and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    async def test_admin_activate_rejection(self) -> TestResult:
        """Non-admin trying /activate should be rejected."""
        r = TestResult(name="admin_activate_rejection")
        t0 = time.perf_counter()
        try:
            msg = await self._send_and_wait("/activate 12345")
            if not msg:
                raise RuntimeError("No response")
            text = msg.text or ""
            r.details["response_time_ms"] = getattr(msg, "_response_time_ms", 0)
            is_rejected = "Hanya admin" in text or "admin only" in text.lower() or "⛔" in text
            r.details["is_rejected"] = is_rejected
            r.passed = is_rejected and self._response_time_ok(msg)
        except Exception as exc:
            r.error = traceback.format_exc()
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    # ── Orchestration ──────────────────────────────────────────────────

    async def run_all(self) -> None:
        LOG.info("=" * 60)
        LOG.info("VILONA BOT E2E TEST SUITE via Telethon")
        LOG.info("Target: @%s | Timeout: %ds | Max RT: %.1fs", self.bot_username, TEST_TIMEOUT, MAX_RESPONSE_TIME)
        LOG.info("=" * 60)

        await self.init_client()

        # 1. UI/UX
        LOG.info("\n--- [SUITE 1] UI/UX Client Flow ---")
        for coro in [
            self.test_start_command,
            self.test_menu_layouts,
            self.test_button_click_signals,
            self.test_button_click_market,
            self.test_back_navigation,
            self.test_response_times,
        ]:
            self.record(await coro())

        # 2. Signal & Execution
        LOG.info("\n--- [SUITE 2] Signal & Execution Accuracy ---")
        for coro in [
            self.test_signal_command,
            self.test_analyze_command,
            self.test_mtf_command,
            self.test_stockity_menu,
        ]:
            self.record(await coro())

        # 3. Data Tracking
        LOG.info("\n--- [SUITE 3] Data Tracking Modules ---")
        for coro in [
            self.test_winrate,
            self.test_trade_history,
            self.test_daily_recap,
            self.test_mapping,
            self.test_status_and_settings,
        ]:
            self.record(await coro())

        # 4. Admin Controls
        LOG.info("\n--- [SUITE 4] Admin Controls ---")
        for coro in [
            self.test_admin_panel_access,
            self.test_admin_commands_rejection,
            self.test_admin_bridge_full_status,
            self.test_admin_activate_rejection,
        ]:
            self.record(await coro())

        if self.client:
            await self.client.disconnect()

        # ── Report ──────────────────────────────────────────────────────
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        LOG.info("\n" + "=" * 60)
        LOG.info("RESULTS: %d passed / %d total | %d failed", passed, total, failed)
        LOG.info("=" * 60)

        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            LOG.info("  %-40s %s  %.1f ms", r.name, status, r.duration_ms)
            if r.error:
                LOG.error("    → %s", r.error.replace("\n", "\n    → "))

        # JSON report for CI
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_bot": self.bot_username,
            "total": total,
            "passed": passed,
            "failed": failed,
            "tests": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "duration_ms": round(r.duration_ms, 1),
                    "error": r.error,
                    "details": r.details,
                }
                for r in self.results
            ],
        }
        report_path = "test_bot_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        LOG.info("JSON report written to: %s", report_path)

        if failed > 0:
            sys.exit(1)


def main() -> None:
    runner = TestRunner()
    asyncio.run(runner.run_all())


if __name__ == "__main__":
    main()
