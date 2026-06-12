import asyncio
import os
import sys

from telethon import TelegramClient

api_id = 23647272
api_hash = "1f69a4e0f03e5f51ddfa5b67ac7b5c49"
session_path = os.path.expanduser("~/.openclaw/workspace/vilona_session")
bot_username = "agent_1ai2_bot"


async def get_menu_message(client, expected_text_query=None):
    async for message in client.iter_messages(bot_username, limit=8):
        if message.buttons:
            if expected_text_query:
                # Check if expected query matches text or one of the buttons
                match_text = expected_text_query.lower()
                text_ok = match_text in message.text.lower()
                btn_ok = any(
                    match_text in btn.text.lower() for row in message.buttons for btn in row
                )
                if text_ok or btn_ok:
                    return message
            else:
                return message
    return None


async def find_button(message, text_query):
    if not message or not message.buttons:
        return None
    for row in message.buttons:
        for button in row:
            if text_query.lower() in button.text.lower():
                return button
    return None


async def test_menu_navigation(client, menu_button_text, expected_substrings, search_query=None):
    print(f"Testing navigation to: '{menu_button_text}' ... ", end="", flush=True)
    try:
        # 1. Get latest message (which should be the main menu)
        msg = await get_menu_message(client)
        if not msg:
            print("FAILED (No message found)")
            return False

        # If it's stockity insider, we need to go to signals first
        if menu_button_text == "stockity insider":
            sig_btn = await find_button(msg, "signal system")
            if not sig_btn:
                print("FAILED (Signal System button not found for nested Stockity nav)")
                return False
            await sig_btn.click()
            await asyncio.sleep(4.5)
            # Fetch Signal menu message
            msg = await get_menu_message(client, "signal")
            if not msg:
                print("FAILED (Signal menu not loaded for nested Stockity nav)")
                return False

        # 2. Find target menu button
        btn = await find_button(msg, menu_button_text)
        if not btn:
            print(f"FAILED (Button '{menu_button_text}' not found in current keyboard)")
            return False

        # 3. Click target button
        await btn.click()
        await asyncio.sleep(4.5)  # Wait for bot response/edit

        # 4. Get the updated message
        msg = await get_menu_message(client, search_query or menu_button_text)
        if not msg:
            print(f"FAILED (Response menu for '{menu_button_text}' not found)")
            return False
        text = msg.text

        # 5. Check expected substrings
        for sub in expected_substrings:
            if sub.lower() not in text.lower():
                print(f"FAILED (Missing substring '{sub}')")
                print(f"Response was: {text[:200]}...")
                return False

        print("PASSED")

        # 6. Go back to main menu
        back_btn = await find_button(msg, "back")
        if back_btn:
            # print("Clicking Back button...")
            await back_btn.click()
            await asyncio.sleep(4.5)

        # If it's stockity, we are now in Signal menu. We need to go back one more time to reach Main menu
        if menu_button_text == "stockity insider":
            msg = await get_menu_message(client, "signal")
            back_btn2 = await find_button(msg, "back")
            if back_btn2:
                await back_btn2.click()
                await asyncio.sleep(4.5)

        return True
    except Exception as e:
        print(f"FAILED (Exception: {e})")
        return False
async def test_chained_navigation(client):
    print("Testing chained navigation (Main -> Account -> Donate -> Account -> Main) ... ", end="", flush=True)
    try:
        # 1. Main menu should be present
        msg = await get_menu_message(client, "1ai trading")
        if not msg:
            print("FAILED (Main menu not found)")
            return False

        # 2. Click 'account'
        btn_acc = await find_button(msg, "account")
        if not btn_acc:
            print("FAILED (Account button not found in main menu)")
            return False
        await btn_acc.click()
        await asyncio.sleep(4.5)

        # 3. Check Account menu
        msg = await get_menu_message(client, "account")
        if not msg or "account" not in msg.text.lower() or "donate" not in msg.text.lower():
            print("FAILED (Not in Account menu or Donate button missing)")
            return False

        # 4. Click 'donate' inside Account menu
        btn_donate = await find_button(msg, "donate")
        if not btn_donate:
            print("FAILED (Donate button not found in Account menu)")
            return False
        await btn_donate.click()
        await asyncio.sleep(4.5)

        # 5. Check Donate menu
        msg = await get_menu_message(client, "kopi")
        if not msg or "subscribe" not in msg.text.lower() or "nominal" not in msg.text.lower():
            print("FAILED (Not in Donate menu)")
            return False
        btn_back = await find_button(msg, "back")
        if not btn_back:
            print("FAILED (Back button not found in Donate menu)")
            return False
        await btn_back.click()
        await asyncio.sleep(4.5)

        # 7. Check Account menu again
        msg = await get_menu_message(client, "account")
        if not msg or "account" not in msg.text.lower() or "donate" not in msg.text.lower():
            print("FAILED (Failed to return to Account menu)")
            return False

        # 8. Click 'back' to go back to Main menu
        btn_back2 = await find_button(msg, "back")
        if not btn_back2:
            print("FAILED (Back button not found in Account menu)")
            return False
        await btn_back2.click()
        await asyncio.sleep(4.5)

        # 9. Verify we are back on Main menu
        msg = await get_menu_message(client, "1ai trading")
        if not msg:
            print("FAILED (Failed to return to Main menu)")
            return False

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED (Exception: {e})")
        return False


async def main():
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Error: Telethon session is not authorized.")
        sys.exit(1)

    print("Telethon session authorized. Resetting to main menu...")
    await client.send_message(bot_username, "/start")
    await asyncio.sleep(4.5)
    # Ensure we get the main menu
    msg = await get_menu_message(client, "1ai trading")
    if not msg:
        print("FAILED (Main menu not found)")
        sys.exit(1)

    print("\nRunning E2E Telegram Menu Navigation Tests...\n")

    menu_tests = [
        ("signal system", ["signal", "system", "engines"], "signal"),
        ("market data", ["market", "data", "killzone"], "market"),
        ("trade history", ["trade", "history", "win rate", "mapping"], "history"),
        ("account", ["account", "status", "donate", "pengaturan"], "account"),
        ("stockity insider", ["stockity", "insider", "bandar"], "stockity"),
        ("help", ["command", "center"], "command"),
        ("admin", ["admin panel", "manajemen", "bot"], "admin"),
    ]

    success = True
    for btn_text, subs, sq in menu_tests:
        if not await test_menu_navigation(client, btn_text, subs, sq):
            success = False

    # Run the chained navigation test
    if not await test_chained_navigation(client):
        success = False

    await client.disconnect()

    if success:
        print("\n🎉 ALL TELEGRAM INLINE MENUS VERIFIED SUCCESSFULLY via TELETHON! 🎉")
        sys.exit(0)
    else:
        print("\n❌ SOME MENU NAVIGATION TESTS FAILED! ❌")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
