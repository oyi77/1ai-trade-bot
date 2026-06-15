# VilonaBot E2E Tests

Comprehensive end-to-end testing for VilonaBot using Telethon sessions.

## Prerequisites

1. **Telethon installed**: `pip install telethon`
2. **Valid Telegram session**: Use `scripts/setup_telethon_session.py` to create one
3. **Bot running**: `pm2 start vilona-bot` or `python -m tradebot bot start vilona`
4. **Environment variables**:
   - `TELEGRAM_API_ID=23647272`
   - `TELEGRAM_API_HASH=1f69a4e0f03e5f51ddfa5b67ac7b5c49`
   - `TELETHON_SESSION=/home/openclaw/.telethon_session/paijo_fixed.session`
   - `ADMIN_CHAT_ID=5365607425` (for admin tests)

## Test Structure

```
tests/e2e/
├── conftest.py              # Fixtures: client, bot, rate_limiter
├── test_vilona_commands.py  # Core command tests (32 tests)
├── test_vilona_callbacks.py # Callback tests (Phase 3) - TODO
├── test_vilona_security.py  # Security tests (Phases 6-7) - TODO
├── test_vilona_payments.py  # Payment flow tests (Phase 5) - TODO
├── test_vilona_connection.py# Connection tests (Phase 8) - TODO
└── utils/
    └── telethon_helpers.py  # Helper utilities
```

## Running Tests

### All E2E Tests
```bash
# Set environment
export TELEGRAM_API_ID=23647272
export TELEGRAM_API_HASH=1f69a4e0f03e5f51ddfa5b67ac7b5c49
export TELETHON_SESSION=/home/openclaw/.telethon_session/paijo_fixed

# Run all E2E tests
python -m pytest tests/e2e/ -v
```

### Specific Test Classes
```bash
# Core commands (happy flow)
python -m pytest tests/e2e/test_vilona_commands.py::TestCoreCommandsHappy -v

# Signal commands
python -m pytest tests/e2e/test_vilona_commands.py::TestSignalCommandsHappy -v

# Admin commands (security)
python -m pytest tests/e2e/test_vilona_commands.py::TestAdminCommands -v
```

### Individual Tests
```bash
# Smoke test
python -m pytest tests/e2e/test_vilona_commands.py::test_smoke_e2e -v

# /start command
python -m pytest tests/e2e/test_vilona_commands.py::TestCoreCommandsHappy::test_start_welcome_message -v

# /signal command
python -m pytest tests/e2e/test_vilona_commands.py::TestSignalCommandsHappy::test_signal_consensus -v
```

### With Coverage
```bash
python -m pytest tests/e2e/ --cov=tradebot/bots --cov-report=term-missing
```

## Test Categories

### Phase 1: Core Commands (12 tests)
- **Happy flow**: `/start`, `/help`, `/status`, `/myid`, `/symbols`
- **Sad flow**: Rate limiting, error handling, malformed input

### Phase 2: Signal Commands (11 tests)
- **Happy flow**: `/analyze`, `/signal`, `/price` with valid assets
- **Sad flow**: Invalid pairs, API rate limits, malformed input
- **Quality validation**: Killzone enforcement, confidence filtering

### Phase 3: Market Data (3 tests)
- `/session`, `/news` commands

### Phase 4: Account Commands (2 tests)
- `/subscribe` tier information

### Phase 5: Rate Limiting (2 tests)
- Cooldown enforcement, daily quota

### Phase 6: Admin Commands (2 tests)
- Security boundaries: `/genkey`, `/activate`

### Phase 7: Edge Cases (4 tests)
- Malformed input, unknown symbols, validation

### Phase 8: Connection (3 tests)
- Basic connectivity, multiple commands, reliability

## Test Helpers

### `send_command(client, bot, "/command")`
Send a command and return `CommandResult` with success, response, timing.

### `send_callback(client, bot, "menu:signals")`
Click a callback button and return `CallbackResult`.

### `assert_response_contains(result, ["keyword1", "keyword2"])`
Assert response contains all expected keywords.

### `assert_response_time(result, max_seconds=5.0)`
Assert response time is within acceptable limits.

## Rate Limiting

Tests use `RateLimiter(min_delay=2.0)` to avoid Telegram flood limits.

**Free tier**: 120s between signals, 5 signals/day
**Donor tier**: 60s between signals, 20 signals/day

## Test Results

After each run, generate:
- Pass/fail count per category
- Response time statistics (min/max/avg/p95)
- Failure categorization (timeout, error, wrong response)
- Coverage report for command handlers

## Troubleshooting

### Session Not Authorized
```bash
# Create new session
python scripts/setup_telethon_session.py

# Or use existing session
export TELETHON_SESSION=/home/openclaw/.telethon_session/paijo_fixed
```

### Bot Not Running
```bash
pm2 start vilona-bot
# or
python -m tradebot bot start vilona
```

### API Rate Limits
If tests hit Telegram API limits:
- Increase `min_delay` in `RateLimiter`
- Reduce number of concurrent tests
- Use `--tb=short` for faster failures

### Telethon Import Error
```bash
pip install telethon==1.44.0
```

## Next Steps

1. **Implement callback tests** (`test_vilona_callbacks.py`)
2. **Add payment flow tests** (`test_vilona_payments.py`)
3. **Add security tests** (`test_vilona_security.py`)
4. **Add connection tests** (`test_vilona_connection.py`)
5. **CI/CD integration**: Run against staging bot

## Notes

- Tests use **real bot interactions** (not mocked)
- Session must be **pre-authorized** (phone verification done)
- Tests are **skipped by default** (require `--run-e2e` flag or environment)
- **Do not commit** session files or API credentials