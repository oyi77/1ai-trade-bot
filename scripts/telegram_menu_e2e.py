import asyncio
import os
import sys

from telethon import TelegramClient

api_id = 23647272
api_hash = "1f69a4e0f03e5f51ddfa5b67ac7b5c49"
session_path = os.path.expanduser("~/.openclaw/workspace/vilona_session")
bot_username = "agent_1ai2_bot"


async def get_latest_message(client):
    async for message in client.iter_messages(bot_username, limit=1):
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


async def test_menu_navigation(client, menu_button_text, expected_substrings):
    print(f"Testing navigation to: '{menu_button_text}' ... ", end="", flush=True)
    try:
        # 1. Get latest message (which should be the main menu)
        msg = await get_latest_message(client)
        if not msg:
            print("FAILED (No message found)")
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
        msg = await get_latest_message(client)
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
    await asyncio.sleep(4)

    print("\nRunning E2E Telegram Menu Navigation Tests...\n")

    menu_tests = [
        ("signal system", ["signal system", "mtf", "engines"]),
        ("market data", ["market data", "gold", "btc", "eth"]),
        ("trade history", ["trade history", "win rate", "recap", "mapping"]),
        ("account", ["account status", "subscribe", "my key"]),
        ("stockity insider", ["stockity", "daftar stockity", "status akun"]),
        ("help", ["bantuan", "symbols", "download ea"]),
        ("admin", ["admin panel", "dashboard", "bridge"]),
    ]

    success = True
    for btn_text, subs in menu_tests:
        if not await test_menu_navigation(client, btn_text, subs):
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
