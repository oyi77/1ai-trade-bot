#!/usr/bin/env python3
"""Set up a new Telethon session by reading code and 2FA from files.

Usage:
    python3 scripts/setup_session.py +6281347241993 codergaboets

After Telegram sends the code, enter it into:
    /tmp/telegram_code.txt

If 2FA is required, enter password into:
    /tmp/telegram_2fa.txt
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from telethon.errors import SessionPasswordNeededError
from telethon.sync import TelegramClient

# Alternative API credentials that work for new authentication
API_ID = 23913448
API_HASH = "78d168f985edf365a5cd9679a917a0b2"
CODE_FILE = Path("/tmp/telegram_code.txt")
TFA_FILE = Path("/tmp/telegram_2fa.txt")
MAX_WAIT_SECONDS = 600  # 10 minutes


def read_code_from_file(path: Path) -> str | None:
    """Read a value from a file and clear it."""
    if not path.exists():
        return None
    value = path.read_text().strip()
    path.unlink(missing_ok=True)
    return value if value else None


async def wait_for_file(path: Path, label: str, timeout: int) -> str:
    """Wait for a value to be written to a file."""
    start = time.time()
    value: str | None = None

    while time.time() - start < timeout:
        value = read_code_from_file(path)
        if value:
            return value
        elapsed = int(time.time() - start)
        if elapsed % 10 == 0:
            print(f"   Waiting for {label}... ({elapsed}s)", flush=True)
        await asyncio.sleep(1)

    raise TimeoutError(f"No {label} entered within {timeout} seconds")


async def create_session(phone: str, session_name: str) -> None:
    """Create and authorize a new Telethon session."""
    session_path = str(Path.home() / ".telethon_session" / session_name)
    client = TelegramClient(session_path, API_ID, API_HASH)

    try:
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session already authorized: @{me.username} ({me.phone})")
            await client.disconnect()
            return

        # Send the verification code
        print(f"🔐 Sending verification code to {phone}...")
        sent = await client.send_code_request(phone)
        phone_code_hash = sent.phone_code_hash
        print(f"✅ Code sent. Hash: {phone_code_hash}")
        print(f"   Phone: {phone}")
        print(f"   Session will be saved as: ~/.telethon_session/{session_name}.session")
        print()
        print(f"   Run: echo 'XXXXX' > {CODE_FILE}")
        print()

        # Wait for the code
        code = await wait_for_file(CODE_FILE, "verification code", MAX_WAIT_SECONDS)

        # Sign in with the code
        print(f"🔑 Signing in with code: {code}")
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            print("🔐 Two-factor authentication required.")
            print(f"   Run: echo 'your_2fa_password' > {TFA_FILE}")
            password = await wait_for_file(TFA_FILE, "2FA password", MAX_WAIT_SECONDS)
            await client.sign_in(password=password)

        # Verify authorization
        if await client.is_user_authorized():
            me = await client.get_me()
            print()
            print("🎉 Session created successfully!")
            print(f"   Username: @{me.username}")
            print(f"   Phone: {me.phone}")
            print(f"   User ID: {me.id}")
            print(f"   Session: {session_path}.session")
            print()

            # Verify bot connection
            bot = await client.get_entity("berkahkaryaforexbotbot")
            print(f"✅ Bot connection verified: @{bot.username}")
        else:
            print("❌ Authorization failed")
            sys.exit(1)

        await client.disconnect()

    except Exception as exc:
        print(f"❌ Error: {exc}")
        await client.disconnect()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        phone = "+6281347241993"
        name = "codergaboets"
    else:
        phone = sys.argv[1]
        name = sys.argv[2] if len(sys.argv) > 2 else "codergaboets"

    asyncio.run(create_session(phone, name))
