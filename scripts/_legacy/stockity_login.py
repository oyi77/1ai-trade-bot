#!/usr/bin/env python3
"""
Stockity login helper — gets fresh authtoken + builds cookie.
Run whenever credentials need refreshing.
"""
import json, os, sys
from pathlib import Path
import urllib.request

ENV_FILE = Path(__file__).parent.parent / "bots" / "stockity-bot" / ".env"


def login(email: str, password: str) -> tuple[str, str]:
    """Login to Stockity and return (authtoken, user_id)."""
    payload = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        "https://api.stockity.id/passport/v2/sign_in?locale=id",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Device-Id": "d79220637a3516ea5350ea509df42828",
            "Device-Type": "web",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) "
                "Gecko/20100101 Firefox/152.0"
            ),
            "Origin": "https://stockity.id",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())["data"]
    return data["authtoken"], data["user_id"]


def build_cookie(authtoken: str, user_id: str) -> str:
    """Build full cookie string from auth components."""
    return (
        f"_stockity_session_v3={authtoken}; "
        f"authtoken={authtoken}; "
        f"user_id={user_id}; "
        f"locale=en"
    )


def save_to_env(authtoken: str, user_id: str, env_path: Path = ENV_FILE):
    """Update .env with fresh credentials."""
    if not env_path.exists():
        print(f"❌ .env not found: {env_path}")
        return False

    lines = env_path.read_text().splitlines()
    new_lines = []
    updated = {"STOCKITY_AUTHTOKEN": False, "STOCKITY_USER_ID": False, "STOCKITY_FULL_COOKIE": False}
    
    cookie = build_cookie(authtoken, user_id)

    for line in lines:
        if line.startswith("STOCKITY_AUTHTOKEN="):
            new_lines.append(f"STOCKITY_AUTHTOKEN={authtoken}")
            updated["STOCKITY_AUTHTOKEN"] = True
        elif line.startswith("STOCKITY_USER_ID="):
            new_lines.append(f"STOCKITY_USER_ID={user_id}")
            updated["STOCKITY_USER_ID"] = True
        elif line.startswith("STOCKITY_FULL_COOKIE="):
            new_lines.append(f"STOCKITY_FULL_COOKIE={cookie}")
            updated["STOCKITY_FULL_COOKIE"] = True
        else:
            new_lines.append(line)

    # Add missing entries
    if not updated["STOCKITY_AUTHTOKEN"]:
        new_lines.append(f"\nSTOCKITY_AUTHTOKEN={authtoken}")
    if not updated["STOCKITY_USER_ID"]:
        new_lines.append(f"STOCKITY_USER_ID={user_id}")
    if not updated["STOCKITY_FULL_COOKIE"]:
        new_lines.append(f"STOCKITY_FULL_COOKIE={cookie}")

    env_path.write_text("\n".join(new_lines) + "\n")
    
    print(f"✅ Saved to {env_path}")
    print(f"   Authtoken: {authtoken[:16]}...")
    print(f"   Cookie: {cookie[:50]}...")
    return True


if __name__ == "__main__":
    email = os.getenv('STOCKITY_EMAIL')
    password = os.getenv('STOCKITY_PASSWORD')

    if not email or not password:
        raise ValueError("STOCKITY_EMAIL and STOCKITY_PASSWORD must be set in .env")

    print("🔑 Logging in...")
    authtoken, user_id = login(email, password)
    print(f"✅ Logged in! user_id={user_id}")

    # Auto-save to .env
    save_to_env(authtoken, user_id)

    # Verify with candle API
    cookie = build_cookie(authtoken, user_id)
    print("\n📡 Verifying cookie...")
    req = urllib.request.Request(
        "https://api.stockity.com/candles/v1/Z-CRY%2FIDX/2026-06-08T13:30:00/1?locale=en",
        headers={"Cookie": cookie, "Origin": "https://stockity.com"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    candles = len(data.get("data", []))
    print(f"✅ Verified: {candles} candles fetched!")
