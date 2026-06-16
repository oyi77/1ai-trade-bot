#!/usr/bin/env python3
"""Comprehensive E2E test for VilonaBot using Telethon (user-level testing)."""

import os
import sys
from datetime import datetime

# Telethon configuration
API_ID = int(os.environ.get("TELEGRAM_API_ID", "23647272"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "1f69a4e0f03e5f51ddfa5b67ac7b5c49")
BOT_USERNAME = "berkahkaryaforexbotbot"  # @berkahkaryaforexbotbot
SESSION_NAME = "/tmp/test_bot_session"

try:
    from telethon.sync import TelegramClient
    from telethon.tl.types import KeyboardButton
except ImportError:
    print("Installing telethon...")
    import subprocess

    subprocess.run([sys.executable, "-m", "pip", "install", "telethon"], check=True)
    from telethon.sync import TelegramClient


class TestResult:
    def __init__(self, category: str, command: str):
        self.category = category
        self.command = command
        self.success = False
        self.response_time = 0.0
        self.has_buttons = False
        self.response_text = ""
        self.error = None
        self.button_count = 0

    def __str__(self):
        status = "✅" if self.success else "❌"
        buttons = f" [{self.button_count} buttons]" if self.has_buttons else ""
        return f"{status} {self.command:<20} {self.response_time:.2f}s{buttons}"


def test_bot():
    """Test bot using Telethon user session."""
    print("=" * 60)
    print("🧪 VILONABOT COMPREHENSIVE TEST SUITE (User-Level)")
    print("=" * 60)

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    client.connect()

    if not client.is_user_authorized():
        print("\n❌ Session not authorized!")
        print("You need to login first. Run:")
        print(
            f"  python3 -c \"from telethon.sync import TelegramClient; client = TelegramClient('{SESSION_NAME}', {API_ID}, '{API_HASH}'); client.start()\""
        )
        print("\nOr set environment variables:")
        print("  TELEGRAM_API_ID=23647272")
        print("  TELEGRAM_API_HASH=1f69a4e0f03e5f51ddfa5b67ac7b5c49")
        client.disconnect()
        return False

    me = client.get_me()
    print(f"\n✅ Connected as: {me.first_name} (@{me.username or 'N/A'}) [ID: {me.id}]")

    # Get bot
    print(f"\n🤖 Finding bot @{BOT_USERNAME}...")
    bot = client.get_entity(BOT_USERNAME)
    print(f"✅ Bot found: {bot.first_name} (ID: {bot.id})")

    # Test commands by category
    commands = {
        "Core Commands": ["/start", "/help"],
        "Signal Commands": ["/signal", "/signal xauusd", "/price", "/price xauusd"],
        "Analysis Commands": ["/analyze", "/analyze xauusd", "/mtf", "/engines"],
        "Market Info": ["/killzone", "/session", "/mapping", "/news", "/data"],
        "Status & History": ["/status", "/myid", "/history", "/winrate"],
        "Subscription": ["/subscribe", "/mykey", "/listkeys"],
        "Trading": ["/zones", "/structure", "/levels", "/stier", "/trailing"],
        "Admin": ["/genkey", "/autosync"],
    }

    results = []
    total = sum(len(cmds) for cmds in commands.values())
    current = 0

    print("\n" + "=" * 60)
    print("📋 RUNNING ALL COMMAND TESTS")
    print("=" * 60)

    for category, cmds in commands.items():
        print(f"\n{category}")
        print("-" * 40)

        for cmd in cmds:
            current += 1
            result = TestResult(category, cmd)

            try:
                import time

                start = time.time()

                # Send command
                client.send_message(bot, cmd)
                time.sleep(2)

                # Get response
                messages = client.get_messages(bot, limit=1)

                end = time.time()
                result.response_time = end - start

                if messages and len(messages) > 0:
                    msg = messages[0]
                    result.response_text = msg.message or ""
                    result.success = True

                    # Check for inline buttons
                    if hasattr(msg, "buttons") and msg.buttons:
                        result.has_buttons = True
                        result.button_count = sum(len(row) for row in msg.buttons if row)

            except Exception as e:
                result.error = str(e)
                result.success = False

            results.append(result)
            status = "✅" if result.success else "❌"
            buttons = f" [{result.button_count} buttons]" if result.has_buttons else ""
            print(f"[{current}/{total}] {status} {cmd:<20} {result.response_time:.2f}s{buttons}")

            if not result.success and result.error:
                print(f"         Error: {result.error[:80]}")

            time.sleep(1)  # Rate limit

    # Test inline buttons
    print("\n" + "=" * 60)
    print("🔘 TESTING INLINE BUTTONS")
    print("=" * 60)

    client.send_message(bot, "/start")
    time.sleep(2)
    messages = client.get_messages(bot, limit=1)

    if messages and messages[0].buttons:
        msg = messages[0]
        btn_count = sum(len(row) for row in msg.buttons if row)
        print(f"\n✅ Main menu has {btn_count} buttons:")

        for i, row in enumerate(msg.buttons):
            if not row:
                continue
            for j, btn in enumerate(row):
                btn_type = (
                    "callback" if hasattr(btn, "data") else "url" if hasattr(btn, "url") else "text"
                )
                print(f"   [{i}][{j}] {btn.text} ({btn_type})")
    else:
        print("⚠️  No inline buttons found")

    # Test signal flow
    print("\n" + "=" * 60)
    print("📊 TESTING SIGNAL GENERATION")
    print("=" * 60)

    client.send_message(bot, "/signal xauusd")
    time.sleep(3)
    messages = client.get_messages(bot, limit=1)

    if messages:
        response = messages[0].message.lower()
        print("\n✅ Signal response received")

        checks = {
            "has_direction": "call" in response
            or "put" in response
            or "buy" in response
            or "sell" in response,
            "has_confidence": "confidence" in response or "conf" in response,
            "has_symbol": "xauusd" in response or "gold" in response,
            "has_analysis": "smc" in response or "support" in response or "fvg" in response,
        }

        print("📋 Signal Quality Checks:")
        for check, passed in checks.items():
            status = "✅" if passed else "⚠️"
            print(f"   {status} {check}")

    # Generate report
    print("\n" + "=" * 60)
    print("📊 TEST REPORT")
    print("=" * 60)

    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed
    avg_time = sum(r.response_time for r in results if r.success) / passed if passed > 0 else 0
    buttons_count = sum(1 for r in results if r.has_buttons)

    print("\n📈 Statistics:")
    print(f"   Total tests: {len(results)}")
    print(f"   Passed: {passed} ({passed / len(results) * 100:.1f}%)")
    print(f"   Failed: {failed} ({failed / len(results) * 100:.1f}%)")
    print(f"   Avg response time: {avg_time:.2f}s")
    print(f"   Commands with buttons: {buttons_count}")

    if failed > 0:
        print("\n❌ Failed tests:")
        for r in results:
            if not r.success:
                print(f"   - {r.command}: {r.error}")

    print(f"\n✅ Test complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    client.disconnect()
    return failed == 0


if __name__ == "__main__":
    success = test_bot()
    sys.exit(0 if success else 1)
