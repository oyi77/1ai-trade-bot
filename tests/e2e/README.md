> See also `~/.telethon_session/AGENTS.md` and `docs/AGENTS_QA.md` for the full agent testing rules.

# E2E Test Runner — Unified Bot @agent_1ai2_bot

## Target Bot

- **Unified bot:** `@agent_1ai2_bot`
- **Legacy bot (do not test):** `@berkahkaryaforexbotbot`
- **PM2 process:** `agent-1ai2-bot`

## Environment

```bash
export TELEGRAM_API_ID="23913448"
export TELEGRAM_API_HASH="78d168f985edf365a5cd9679a917a0b2"
export TELETHON_SESSION="$HOME/.telethon_session/codergaboets.session"
export TELETHON_BOT_USERNAME="agent_1ai2_bot"
```

## Quick Verification

```python
import asyncio
from pathlib import Path
from telethon.sync import TelegramClient

async def verify():
    session = str(Path.home() / '.telethon_session' / 'codergaboets')
    client = TelegramClient(session, 23913448, '78d168f985edf365a5cd9679a917a0b2')
    await client.start()
    me = await client.get_me()
    bot = await client.get_entity('agent_1ai2_bot')
    print(f'✅ User: @{me.username}')
    print(f'✅ Bot: @{bot.username}')
    await client.disconnect()

asyncio.run(verify())
```

## Run Tests

```bash
cd tests/e2e
pytest test_vilona_commands.py -v
```

## Mandatory Coverage

For every feature, add E2E tests covering:

- commands
- functions (signals, price, consensus)
- inline buttons
- reply keyboards
- happy flow
- sad flow
- fraud scenarios
- connection resilience
- full input → output path

## Troubleshooting

| Problem | Command |
|---------|---------|
| Bot not running | `pm2 status \| grep agent-1ai2` |
| Check logs | `pm2 logs agent-1ai2-bot --lines 50` |
| Legacy bot conflict | `pm2 stop vilona-bot; pkill -f vilona_tradefx_handler; pm2 restart agent-1ai2-bot` |
| Session expired | switch to `alwayscuanbos.session` or re-authenticate via `scripts/setup_session.py` |
| Flood limit hit | increase `RateLimiter(min_delay=...)` in the test |
