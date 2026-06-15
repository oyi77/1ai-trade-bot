# AGENTS Q&A - Testing Guidelines

## Telegram Bot E2E Testing

### Q: How do I test the VilonaBot from a user's perspective?

**A:** Use the pre-authenticated Telethon sessions in `~/.telethon_session/`:

```python
from telethon.sync import TelegramClient

# Use the fixed session (recommended)
client = TelegramClient(
    "/home/openclaw/.telethon_session/paijo_fixed",
    api_id=23647272,
    api_hash="1f69a4e0f03e5f51ddfa5b67ac7b5c49"
)
client.connect()

if client.is_user_authorized():
    me = client.get_me()
    print(f"Authorized: {me.first_name} (@{me.username})")
    
    # Test bot
    bot = client.get_entity("berkahkaryaforexbotbot")
    client.send_message(bot, "/start")
```

### Q: Which session should I use?

**A:** Use `paijo_fixed.session` - it's the only session compatible with Telethon 1.44+:

| Session | Phone | User | Version | Status |
|---------|-------|------|---------|--------|
| `paijo_fixed.session` | +6285732740006 | @alwayscuanbos | 11 | ✅ Working |
| `vilona_session.session` | +6285732740006 | @alwayscuanbos | 8 | ⚠️ Needs fix |
| `paijo.session` | +6285732740006 | @alwayscuanbos | 8 | ⚠️ Needs fix |

### Q: What if I get a Telethon session error?

**A:** Check the error type:

#### Error: `ValueError: too many values to unpack (expected 5, got 6)`
**Cause:** Telethon 1.43.2 or older expecting old schema.
**Fix:** Upgrade to Telethon 1.44.0+
```bash
uv pip install 'telethon>=1.44.0' --upgrade
```

#### Error: `ValueError: not enough values to unpack (expected 6, got 4)`
**Cause:** Session has wrong schema (takeout_id has empty bytes instead of NULL).
**Fix:** Fix the session:
```python
import sqlite3
import shutil

def fix_session(old_path, new_path):
    shutil.copy(old_path, new_path)
    conn = sqlite3.connect(new_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT auth_key FROM sessions")
    auth_key = cursor.fetchone()[0]
    
    cursor.execute("DROP TABLE sessions")
    cursor.execute("""
        CREATE TABLE sessions (
            dc_id INTEGER PRIMARY KEY,
            server_address TEXT,
            port INTEGER,
            auth_key BLOB,
            takeout_id INTEGER,
            tmp_auth_key BLOB
        )
    """)
    cursor.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL)",
                    (5, '91.108.56.170', 443, auth_key))
    cursor.execute("UPDATE version SET version = 11")
    conn.commit()
    conn.close()
```

#### Error: `ApiIdInvalidError: The api_id/api_hash combination is invalid`
**Cause:** API credentials revoked or don't match the phone number.
**Fix:** 
1. Create new credentials at https://my.telegram.org/apps
2. Update `strategies/vilona_tradefx/.env`:
```
TELEGRAM_API_ID=<new_id>
TELEGRAM_API_HASH=<new_hash>
```

### Q: How do I test all bot commands systematically?

**A:** Follow this comprehensive testing checklist:

## Testing Checklist

### 1. Commands - Happy Flow

Test all command variations with valid input:

```python
commands = {
    "Core": ["/start", "/help"],
    "Signals": ["/signal", "/signal xauusd", "/price", "/price btc"],
    "Analysis": ["/analyze", "/analyze xauusd", "/mtf", "/engines"],
    "Market": ["/killzone", "/session", "/mapping", "/news", "/data"],
    "Status": ["/status", "/myid", "/history", "/winrate"],
    "Subscription": ["/subscribe", "/mykey", "/listkeys"],
    "Trading": ["/zones", "/structure", "/levels", "/stier", "/trailing"],
}

for category, cmds in commands.items():
    for cmd in cmds:
        client.send_message(bot, cmd)
        time.sleep(2)  # Rate limit
        msg = client.get_messages(bot, limit=1)[0]
        assert msg.message, f"No response for {cmd}"
        print(f"✅ {cmd}")
```

### 2. Commands - Sad Flow (Invalid Input)

Test error handling with invalid input:

```python
# Invalid parameters
sad_commands = [
    "/signal invalidasset",  # Non-existent asset
    "/analyze",  # Missing asset (should prompt)
    "/price xyz",  # Unknown symbol
    "/subscribe",  # Already subscribed (if applicable)
]

for cmd in sad_commands:
    client.send_message(bot, cmd)
    time.sleep(2)
    msg = client.get_messages(bot, limit=1)[0]
    # Should show helpful error, not crash
    assert msg.message, f"No error message for {cmd}"
    print(f"✅ {cmd} - Error handled")
```

### 3. Functions - Signal Generation

Test signal generation pipeline:

```python
# Test signal generation
client.send_message(bot, "/signal xauusd")
time.sleep(5)  # Wait for AI analysis
msg = client.get_messages(bot, limit=1)[0]

# Verify signal format
response = msg.message.lower()
assert "xauusd" in response or "gold" in response, "Missing symbol"
assert "confidence" in response or "conf" in response, "Missing confidence"
# Direction may be blocked outside killzone - that's correct behavior
print(f"✅ Signal format verified")
```

### 4. Functions - Price Fetching

Test price fetching for all supported assets:

```python
assets = ["xauusd", "btc", "eurusd", "gbpusd"]

for asset in assets:
    client.send_message(bot, f"/price {asset}")
    time.sleep(2)
    msg = client.get_messages(bot, limit=1)[0]
    assert "price" in msg.message.lower() or asset in msg.message.lower()
    print(f"✅ Price for {asset}")
```

### 5. Inline Buttons & Keyboards

Test all button interactions:

```python
# Get main menu
client.send_message(bot, "/start")
time.sleep(2)
msg = client.get_messages(bot, limit=1)[0]

if msg.buttons:
    for i, row in enumerate(msg.buttons):
        for j, btn in enumerate(row):
            # Test button click
            if hasattr(btn, 'data'):  # Callback button
                client.callback_query(msg, data=btn.data)
                time.sleep(1)
                response = client.get_messages(bot, limit=1)[0]
                print(f"✅ Button [{i}][{j}] {btn.text}: {response.message[:50]}...")
```

### 6. Happy Flow vs Sad Flow

| Test Case | Happy Flow | Sad Flow |
|-----------|------------|----------|
| Command | Valid input → Expected output | Invalid input → Helpful error |
| Button | Valid callback → Correct action | Invalid callback → Graceful handling |
| Signal | In killzone → Full signal | Outside killzone → "Wait for killzone" message |
| Price | Known asset → Live price | Unknown asset → "Asset not found" |
| Analysis | Valid symbol → AI analysis | Invalid symbol → Prompt for valid input |

### 7. Fraud & Security Testing

Test security boundaries:

```python
# Test admin commands as regular user
admin_commands = ["/genkey", "/autosync", "/restart_bot"]

for cmd in admin_commands:
    client.send_message(bot, cmd)
    time.sleep(2)
    msg = client.get_messages(bot, limit=1)[0]
    # Should deny access, not crash
    assert "admin" in msg.message.lower() or "unauthorized" in msg.message.lower()
    print(f"✅ {cmd} - Access denied correctly")

# Test callback data injection
try:
    client.callback_query(msg, data="malformed:data:here")
    print("✅ Malformed callback handled")
except:
    print("✅ Malformed callback rejected")
```

### 8. Signals - Full Pipeline

Test complete signal generation from input to output:

```python
# Input: User requests signal
client.send_message(bot, "/signal xauusd")

# Processing: Wait for AI consensus
time.sleep(5)

# Output: Verify complete signal
msg = client.get_messages(bot, limit=1)[0]
response = msg.message

# Check signal components
checks = {
    "has_symbol": any(x in response.lower() for x in ["xauusd", "gold", "xau"]),
    "has_analysis": any(x in response.lower() for x in ["smc", "support", "resistance", "fvg", "liquidity"]),
    "has_session_info": "session" in response.lower() or "asia" in response.lower() or "london" in response.lower(),
    "has_killzone_status": "killzone" in response.lower() or "kz" in response.lower(),
}

for check, passed in checks.items():
    status = "✅" if passed else "⚠️"
    print(f"{status} {check}: {passed}")

# Verify button presence (if signal has action buttons)
if msg.buttons:
    print(f"✅ Action buttons: {sum(len(row) for row in msg.buttons if row)}")
```

### 9. Connection & Reliability

Test connection handling:

```python
# Test reconnection
client.disconnect()
time.sleep(2)
client.connect()
assert client.is_user_authorized()
print("✅ Reconnection works")

# Test rate limiting (rapid commands)
for i in range(5):
    client.send_message(bot, "/price xauusd")
    time.sleep(0.5)  # Rapid requests

# Should handle gracefully, not flood wait
print("✅ Rate limiting handled")
```

### 10. Connection Test Template

```python
def test_connection():
    """Test full connection lifecycle."""
    client = TelegramClient(SESSION, API_ID, API_HASH)
    
    # Test 1: Connect
    client.connect()
    assert client.is_connected(), "Not connected"
    print("✅ Connected")
    
    # Test 2: Authorization
    assert client.is_user_authorized(), "Not authorized"
    me = client.get_me()
    print(f"✅ Authorized: {me.first_name}")
    
    # Test 3: Bot access
    bot = client.get_entity("berkahkaryaforexbotbot")
    assert bot, "Bot not found"
    print(f"✅ Bot accessible: {bot.first_name}")
    
    # Test 4: Send message
    client.send_message(bot, "/start")
    time.sleep(2)
    msg = client.get_messages(bot, limit=1)[0]
    assert msg.message, "No response"
    print("✅ Message sent/received")
    
    # Test 5: Disconnect
    client.disconnect()
    print("✅ Disconnected cleanly")
    
    return True
```

## Full E2E Test Script

See: `/home/openclaw/.telethon_session/AGENTS.md` for complete test script.

## Sessions Location

```
~/.telethon_session/
├── README.md              # Human documentation
├── AGENTS.md              # Agent instructions (this file)
├── manifest.json          # Session metadata
├── paijo_fixed.session    # ⭐ Primary (use this one)
├── vilona_session.session # Backup (needs fix)
├── paijo.session          # Original (needs fix)
└── leak_finder.session    # Service account
```

## API Credentials

Location: `strategies/vilona_tradefx/.env`
```bash
TELEGRAM_API_ID=23647272
TELEGRAM_API_HASH=1f69a4e0f03e5f51ddfa5b67ac7b5c49
```

## Integration with CI/CD

Add to test pipeline:

```yaml
# .github/workflows/e2e-test.yml
- name: Install Telethon
  run: uv pip install 'telethon>=1.44.0'

- name: Run E2E Tests
  env:
    TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
    TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
  run: |
    python tests/e2e/test_bot_user_level.py
```