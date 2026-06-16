# E2E QA Guide — Unified Bot @agent_1ai2_bot

> This guide is mandatory reading for every agent before modifying or testing bot behavior.

## Bot Under Test

- **Telegram username:** `@agent_1ai2_bot`
- **PM2 process:** `agent-1ai2-bot`
- **Role:** Unified bot (VilonaBot)
- **Token:** `8343388239:AAHWOVrRzwVeyGHav-DY9RvfSosDYteHuIg`

Do **not** test `@berkahkaryaforexbotbot`. It is the legacy Forex handler run by a script in `scripts/`.

## Session Files

All sessions are centralized in `~/.telethon_session/`.

| File | Telegram User | Phone | API ID / Hash | Status |
|------|---------------|-------|---------------|--------|
| `codergaboets.session` | @codergaboets | +6281347241993 | 23913448 / `78d168f985edf365a5cd9679a917a0b2` | ✅ Primary |
| `alwayscuanbos.session` | @alwayscuanbos | +6285732740006 | 23647272 / `1f69a4e0f03e5f51ddfa5b67ac7b5c49` | ✅ Fallback |

Unauthenticated sessions found across the machine have been removed.

## Environment Setup

```bash
export TELEGRAM_API_ID="23913448"
export TELEGRAM_API_HASH="78d168f985edf365a5cd9679a917a0b2"
export TELETHON_SESSION="$HOME/.telethon_session/codergaboets.session"
export TELETHON_BOT_USERNAME="agent_1ai2_bot"
```

## Required Testing Coverage

Every change must be validated with **real** E2E tests from user input to bot output. Test **all** of the following:

### 1. Commands

- Every bot command (currently 29+).
- Happy flow: valid command → correct response.
- Sad flow: invalid command, missing args, malformed text → clear error, no crash.
- Edge cases: empty command, unicode, very long input, rapid repeats.

### 2. Functions

- Signal generation: `/signal gold`, `/signal btc`, `/signal eurusd`.
- Price fetch: `/price gold`.
- Engine consensus output: confidence, entry, SL, TP.
- Formatting: no broken markdown, correct symbols.

### 3. Buttons

- All inline buttons from `/start` and menus.
- Callback data: `menu:*`, `cmd:*`, `__url__`, and any custom callbacks.
- Back/close navigation.

### 4. Keyboards

- Reply keyboards where used.
- Keyboard button presses produce the expected command or menu.

### 5. Happy Flow / Sad Flow

- Valid input path → success state.
- Invalid input path → graceful failure state.
- Both must return correct Telegram messages.

### 6. Fraud

- Invalid payment callback signatures rejected.
- Duplicate subscription payments handled.
- Expired subscription tier downgraded correctly.
- Wrong user accessing admin/paid features blocked.

### 7. Signals

Full pipeline must be verified:

```
User sends /signal gold
  ↓ command parsed
  ↓ market data fetched
  ↓ 11 engines run
  ↓ consensus calculated
  ↓ signal formatted (entry, SL, TP, confidence)
  ↓ message sent to user
```

### 8. Connection

- Bot reconnects after restart.
- Rate limiting / flood protection active.
- No duplicate messages after reconnect.
- Commands still work after a bot crash/restart cycle.

### 9. Admin

- Admin-only commands blocked for normal users.
- Admin commands succeed for admin user.
- Broadcast, ban, status commands tested.

## Test Checklist

Before any PR or commit that touches bot code:

- [ ] Commands — all variations tested
- [ ] Functions — signal/price/consensus tested
- [ ] Buttons — all inline buttons tested
- [ ] Keyboards — all reply buttons tested
- [ ] Happy flow verified
- [ ] Sad flow verified
- [ ] Fraud scenarios verified
- [ ] Signal pipeline end-to-end verified
- [ ] Connection/reconnect verified
- [ ] Rate limiting verified
- [ ] Admin access control verified
- [ ] Every path makes sense and passes 100%

## How to Run Tests

```bash
cd tests/e2e
pytest test_vilona_commands.py -v
pytest test_vilona_commands.py::TestCoreCommandsHappy -v
```

Run a single test with full output:

```bash
pytest test_vilona_commands.py::TestCoreCommandsHappy::test_start_welcome_message -xvs
```

## When Tests Fail

1. Confirm the unified bot is running:
   ```bash
   pm2 status | grep agent-1ai2
   ```
2. Check the logs:
   ```bash
   pm2 logs agent-1ai2-bot --lines 50
   ```
3. Kill any legacy bot conflict:
   ```bash
   pm2 stop vilona-bot
   pkill -f vilona_tradefx_handler
   pm2 restart agent-1ai2-bot
   ```
4. Verify the session:
   ```bash
   python3 -c "
from telethon.sync import TelegramClient
client = TelegramClient('$HOME/.telethon_session/codergaboets', 23913448, '78d168f985edf365a5cd9679a917a0b2')
client.start()
print(client.get_me().username)
client.disconnect()
"
   ```
5. Re-run the failing test in isolation.

## Success Criteria

All tests pass, the bot does not crash, response times are reasonable, and **every path from input to output is validated against the real bot**.
