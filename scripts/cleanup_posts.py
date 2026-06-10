#!/usr/bin/env python3
import json, urllib.request, re

env = open("/home/openclaw/projects/1ai-trade-bot/strategies/vilona_tradefx/.env").read()
m = re.search(r'VILONA_TRADEFX_TELEGRAM_BOT_TOKEN\s*=\s*(.+)', env)
if not m:
    print("no token")
    exit(1)
tk = m.group(1).strip().strip("\"'")

CH = "-1003257064212"

# Delete wrong posts
for mid in [84, 85]:
    try:
        url = f"https://api.telegram.org/bot{tk}/deleteMessage"
        pay = json.dumps({"chat_id": CH, "message_id": mid}).encode()
        req = urllib.request.Request(url, data=pay, headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=10).read())
        print(f"Delete {mid}: {r}")
    except Exception as e:
        print(f"Delete {mid}: {e}")
