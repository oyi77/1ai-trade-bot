import os, json, urllib.request
from pathlib import Path

env_path = 'strategies/vilona_tradefx/.env'
token = None
for line in Path(env_path).read_text().splitlines():
    if line.startswith('VILONA_TRADEFX_TELEGRAM_BOT_TOKEN='):
        token = line.partition('=')[2].strip()
        break

if not token:
    raise SystemExit('Telegram token missing')

print(token)