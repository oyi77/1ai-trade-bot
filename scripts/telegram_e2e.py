import asyncio
import os
import sys

from telethon import TelegramClient

api_id = 23647272
api_hash = "1f69a4e0f03e5f51ddfa5b67ac7b5c49"
session_path = os.path.expanduser("~/.openclaw/workspace/vilona_session")
bot_username = "berkahkaryaforexbotbot"


async def test_command(client, command, expected_substrings):
    print(f"Testing command: {command} ... ", end="", flush=True)
    try:
        await client.send_message(bot_username, command)
        # Wait for bot response
        await asyncio.sleep(4)

        # Get the latest message (bot response)
        async for message in client.iter_messages(bot_username, limit=1):
            if message.out:
                print("FAILED (No response received)")
                return False

            text = message.text
            for sub in expected_substrings:
                if sub.lower() not in text.lower():
                    print(f"FAILED (Missing substring '{sub}')")
                    print(f"Response was: {text[:200]}...")
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

    print("Telethon session authorized. Running E2E Telegram Bot Command Tests...\n")

    tests = [
        ("/start", ["revolusi trading", "markas besar", "/signal"]),
        ("/signal", ["mtf matrix", "verdict", "entry", "sl", "tp"]),
        ("/levels", ["level", "snr", "fibo"]),
        ("/zones", ["order block", "fvg"]),
        ("/structure", ["structure", "bos", "choch"]),
        ("/session", ["session", "level"]),
        ("/mykey", ["license", "key"]),
        ("/subscribe", ["subscription", "free", "pro"]),
    ]

    success = True
    for cmd, subs in tests:
        if not await test_command(client, cmd, subs):
            success = False

    await client.disconnect()

    if success:
        print("\n🎉 ALL TELEGRAM BOT COMMANDS VERIFIED SUCCESSFULLY via TELETHON! 🎉")
        sys.exit(0)
    else:
        print("\n❌ SOME TELEGRAM COMMAND TESTS FAILED! ❌")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
