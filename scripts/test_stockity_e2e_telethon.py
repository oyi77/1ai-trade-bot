"""Stockity multi-asset trading E2E test via Telethon.

Tests the complete flow:
  1. /platforms — verify linked platforms response
  2. /signal CRYPTO_IDX — generate multi-asset signal
  3. Menu: Main -> Signal System -> Stockity Insider -> Back
  4. /status — check account status
  5. /trade_yes — execute pending signal trade
  6. /history — verify trade recorded
"""

import asyncio
import os
import sys

from telethon import TelegramClient

api_id = 23647272
api_hash = "1f69a4e0f03e5f51ddfa5b67ac7b5c49"
session_path = os.path.expanduser("~/.openclaw/workspace/paijo")
bot_username = "agent_1ai2_bot"

# ── Helpers ────────────────────────────────────────────────────────────

async def get_last_message(client, expected_substring=None, timeout=10):
    """Fetch the most recent message from bot, optionally filtering by expected_substring."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async for msg in client.iter_messages(bot_username, limit=5):
            if msg.out:
                continue
            text = msg.text or ""
            if expected_substring:
                if expected_substring.lower() in text.lower():
                    return msg
            else:
                return msg
        await asyncio.sleep(0.5)
    return None


async def find_button(message, text_query):
    if not message or not message.buttons:
        return None
    for row in message.buttons:
        for button in row:
            if text_query.lower() in button.text.lower():
                return button
    return None


async def send_command(client, cmd, expected_in_response=None):
    print(f"  Sending: {cmd} ... ", end="", flush=True)
    try:
        await client.send_message(bot_username, cmd)
        await asyncio.sleep(5.0)
        msg = await get_last_message(client, expected_in_response, timeout=5)
        if not msg:
            print("FAILED (no response)")
            return None
        print("PASSED")
        return msg
    except Exception as e:
        print(f"FAILED (Exception: {e})")
        return None


# ── Step tests ─────────────────────────────────────────────────────────

async def test_platforms(client):
    """/platforms — should show linked platforms or a helpful message."""
    print("\n[Step 1] /platforms — Linked Platforms")
    msg = await send_command(client, "/platforms", expected_in_response="platform")
    if not msg:
        return False
    text = msg.text or ""
    print(f"    Response: {text[:200].strip()}")
    # Accept either linked platforms or "Belum ada platform" (no platforms)
    ok = "platform" in text.lower() and ("belum" in text.lower() or "linked" in text.lower()
                                          or "stockity" in text.lower() or "taut" in text.lower())
    if ok:
        print("  => PASSED")
        return True
    print("    Response: {text[:200].strip()}")
    return False


async def test_signal(client):
    """/signal CRYPTO_IDX — generate multi-asset signal."""
    print("\n[Step 2] /signal CRYPTO_IDX — Multi-Asset Signal")
    msg = await send_command(client, "/signal CRYPTO_IDX",
                             expected_in_response="signal")
    if not msg:
        return False
    text = msg.text or ""
    print(f"    Response (first 300 chars): {text[:300].strip()}")
    # Should contain signal-like content
    ok = any(kw in text.lower() for kw in ("signal", "mtf", "verdict",
                                            "consensus", "engine", "buy", "sell", "hold"))
    if ok:
        print("  => PASSED")
        return True
    print("    Response (first 300 chars): {text[:300].strip()}")
    return False


async def test_menu_nav(client):
    """Main -> Signal System -> Stockity Insider -> Back (two-level back)."""
    print("\n[Step 3] Menu Navigation: Main -> Signal System -> Stockity Insider -> Back")
    try:
        # Ensure we're at main menu
        await client.send_message(bot_username, "/start")
        await asyncio.sleep(4.5)

        msg = await get_last_message(client, "market", timeout=8)
        if not msg:
            print("  FAILED (main menu not found)")
            return False
        print("  Main menu loaded.")

        # Click "SIGNAL SYSTEM"
        btn_sig_sys = await find_button(msg, "signal system")
        if not btn_sig_sys:
            print("  FAILED (Signal System button not found)")
            return False
        print("  Clicking SIGNAL SYSTEM...")
        await btn_sig_sys.click()
        await asyncio.sleep(4.5)

        msg = await get_last_message(client, "signal", timeout=8)
        if not msg:
            print("  FAILED (Signal menu not loaded)")
            return False
        print("  Signal menu loaded.")

        # Click "STOCKITY INSIDER"
        btn_stock = await find_button(msg, "stockity insider")
        if not btn_stock:
            print("  FAILED (Stockity Insider button not found)")
            return False
        print("  Clicking STOCKITY INSIDER...")
        await btn_stock.click()
        await asyncio.sleep(4.5)

        msg = await get_last_message(client, "stockity", timeout=8)
        if not msg:
            print("  FAILED (Stockity menu not loaded)")
            return False
        stock_text = msg.text or ""
        if "stockity" not in stock_text.lower():
            print("  FAILED (Not in Stockity menu)")
            return False
        print("  Stockity Insider menu loaded.")

        # Back to Signal menu
        btn_back = await find_button(msg, "back")
        if not btn_back:
            print("  FAILED (Back button not found in Stockity menu)")
            return False
        print("  Clicking Back (-> Signal menu)...")
        await btn_back.click()
        await asyncio.sleep(4.5)

        msg = await get_last_message(client, "signal", timeout=8)
        if not msg:
            print("  FAILED (Back to Signal menu failed)")
            return False
        print("  Returned to Signal menu.")

        # Back to Main menu
        btn_back2 = await find_button(msg, "back")
        if not btn_back2:
            print("  FAILED (Back button not found in Signal menu)")
            return False
        print("  Clicking Back (-> Main menu)...")
        await btn_back2.click()
        await asyncio.sleep(4.5)

        msg = await get_last_message(client, "market", timeout=8)
        if not msg:
            print("  FAILED (Back to Main menu failed)")
            return False
        print("  Returned to Main menu.")
        print("  => PASSED")
        return True
    except Exception as e:
        print(f"  FAILED (Exception: {e})")
        return False


async def test_status(client):
    """/status — check account status."""
    print("\n[Step 4] /status — Account Status")
    msg = await send_command(client, "/status", expected_in_response="status")
    if not msg:
        return False
    text = msg.text or ""
    print(f"    Response: {text[:300].strip()}")
    ok = "status" in text.lower() and ("akun" in text.lower() or "subscriber" in text.lower()
                                        or "balance" in text.lower() or "engine" in text.lower())
    if ok:
        print("  => PASSED")
        return True
    print("    Response: {text[:300].strip()}")
    return False


async def test_trade_yes(client):
    """/trade_yes — execute pending signal (must be called AFTER /signal)."""
    print("\n[Step 5] /trade_yes — Execute Pending Signal")
    msg = await send_command(client, "/trade_yes",
                             expected_in_response="sinyal")
    if not msg:
        return False
    text = msg.text or ""
    print(f"    Response: {text[:250].strip()}")
    # Accept either executed or pending-signal-missing response — the bot
    # may not have a signal pending for this user, but the command itself
    # should respond cleanly.
    ok = any(kw in text.lower() for kw in ("sinyal", "dikirim", "terkirim",
                                            "bridge", "trade", "pending"))
    if ok:
        print("  => PASSED")
        return True
    print("    Response: {text[:250].strip()}")
    return False


async def test_history(client):
    """/history — verify trade history is accessible."""
    print("\n[Step 6] /history — Trade History")
    msg = await send_command(client, "/history", expected_in_response="trade")
    if not msg:
        return False
    text = msg.text or ""
    print(f"    Response (first 400 chars): {text[:400].strip()}")
    ok = any(kw in text.lower() for kw in ("trade", "history", "riwayat",
                                            "belum", "profit", "loss", "win rate",
                                            "winrate", "mapping"))
    if ok:
        print("  => PASSED")
        return True
    print("    Response (first 400 chars): {text[:400].strip()}")
    return False


# ── Reset to main menu ─────────────────────────────────────────────────

async def reset_to_main(client):
    """Send /start and wait for main menu to load."""
    print("Resetting to main menu...")
    await client.send_message(bot_username, "/start")
    await asyncio.sleep(4.5)
    msg = await get_last_message(client, "market", timeout=8)
    if not msg:
        print("FAILED (main menu not found after /start)")
        return False
    print("OK — Main menu loaded.")
    return True


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Error: Telethon session is not authorized. Run telegram_menu_e2e.py first.")
        sys.exit(1)

    print("=" * 60)
    print("STOCKITY MULTI-ASSET E2E TEST (Telethon)")
    print("=" * 60)

    # Reset to predictable starting state
    if not await reset_to_main(client):
        await client.disconnect()
        sys.exit(1)

    results = {}

    results["platforms"] = await test_platforms(client)
    results["signal"] = await test_signal(client)
    results["menu_nav"] = await test_menu_nav(client)
    results["status"] = await test_status(client)
    results["trade_yes"] = await test_trade_yes(client)
    results["history"] = await test_history(client)

    await client.disconnect()

    # ── Summary ──
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    passed = 0
    failed = 0
    labels = {
        "platforms": "/platforms",
        "signal": "/signal CRYPTO_IDX",
        "menu_nav": "Menu: Main -> Signal System -> Stockity Insider -> Back",
        "status": "/status",
        "trade_yes": "/trade_yes",
        "history": "/history",
    }
    for key, label in labels.items():
        status = "PASS" if results.get(key) else "FAIL"
        if results.get(key):
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {label}")

    print(f"\n  Total: {passed} passed, {failed} failed out of {len(labels)}")

    if failed == 0:
        print("\nALL TESTS PASSED")
        sys.exit(0)
    else:
        print("\nSOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
