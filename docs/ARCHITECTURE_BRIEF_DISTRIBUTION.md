# SIGNAL DISTRIBUTION & USER MANAGEMENT — ARCHITECTURE BRIEF

> **Generated:** 2026-06-14 | **Re:** Vilona AI Trading Ecosystem — Tiered Routing Design
> **Status:** Production topology extracted from live codebase

---

## 1. TELEGRAM BOT & CHANNEL INFRASTRUCTURE

### 1.1 Active Bot Inventory

| # | Bot | Token Env Var | Framework | Transport | Systemd |
|---|-----|--------------|-----------|-----------|---------|
| 1 | **VilonaBot** (main trading bot) | `TELEGRAM_BOT_TOKEN` | Raw `urllib` polling | `getUpdates` (timeout=10) | `vilona-tradefx-bot.service` |
| 2 | **Agent Bot** (1AI Trading Agent) | `TELEGRAM_BOT_TOKEN` | `httpx` async polling | `getUpdates` (timeout=10) | `agent-bot.service` |
| 3 | **Vilona Signal Bridge** | `TELEGRAM_BOT_TOKEN` | FastAPI `httpx` | Poll + REST API | `vtfx-signal-bridge.service` |
| 4 | **Vilona Telegram Daemon** | — | Telethon (MTProto) | Persistent TCP | `vilona-telegram.service` |
| 5 | **Subscription Bot** | `TELEGRAM_BOT_TOKEN` | Raw `urllib` polling | `getUpdates` | standalone script |
| 6 | **Payment Webhook** | `TELEGRAM_BOT_TOKEN` | HTTP server :8787 | Webhook receiver | `vilona-payment-webhook.service` |

**Key finding: All use `TELEGRAM_BOT_TOKEN` — single bot token shared across all services.** Only exception is watchdog scripts that use `VILONA_TRADEFX_TELEGRAM_BOT_TOKEN` as fallback.

### 1.2 Transport: Polling, NOT Webhooks

Every bot uses the **long-polling `getUpdates` pattern.** No Telegram webhook is configured anywhere.

```python
# Example: launch_bot.py (VilonaBot main polling loop)
while bot._running:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    payload = {"offset": offset, "timeout": 10}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={...})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    for update in data.get("result", []):
        await bot.handle_update(update)
```

**Implication for tiered routing:** All bots share the same token, meaning they compete for updates. Only one long-poll connection per token works at a time — others get 409 Conflict. Adding a new bot for tiered routing requires either a **new bot token** or a **unified dispatch layer**.

### 1.3 Signal Journey: Pipeline → Telegram

There is **no single canonical path.** Five mechanisms operate in parallel:

```
SignalPipeline.process()
    │
    ├─[Path A] TelegramService.send_signal_alert()
    │         └─ httpx POST → sendMessage → specific chat_id
    │
    ├─[Path B] SignalPublisher.publish_signal()
    │         └─ Dedup check → Telegram channel + bridge server
    │
    ├─[Path C] broadcast_signal_result() (agent/core.py)
    │         └─ Loops all active subscribers → individual DMs
    │
    ├─[Path D] VilonaSignalBridge._broadcast_signal()
    │         └─ Per-instance queue → per-user queue → global queue
    │
    └─[Path E] auto_analysis_loop() (agent/core.py, every 300s)
              └─ Checks trade outcomes → admin + subscribers
```

```json
// Mock Broadcaster Payload (what SignalPublisher emits)
{
  "signal_id": "sig_20260614_133000_xauusd",
  "symbol": "XAUUSD",
  "direction": "BULLISH",
  "entry": 2645.30,
  "sl": 2635.50,
  "tp1": 2658.20,
  "tp2": 2670.80,
  "confidence": 0.82,
  "grade": "A",
  "timestamp": "2026-06-14T13:30:00Z",
  "target": "all",                           // "all" | "premium" | "trial" | "user:<chat_id>"
  "channels": ["telegram", "bridge", "ea"],  // multi-target delivery
  "telegram_format": "html",                 // "html" | "markdown"
  "metadata": {
    "reason": "MTF ALIGNED | BULLISH | 8/11 engines agree",
    "mtf_alignment": "ALIGNED",
    "macro_trend": "BULLISH"
  }
}
```

---

## 2. USER ONBOARDING & STATE MANAGEMENT

### 2.1 `/start` Onboarding Flow

```
User sends /start
    │
    ▼
VilonaBot._cmd_start(args, chat_id)
    │
    ├─ Checks: is chat_id in ADMIN_USER_IDS?
    │   ├─ YES → menu_name = "admin"
    │   └─ NO  → menu_name = "main"
    │
    ├─ Builds welcome message:
    │   "🔥 VILONA AI — TRADING SYSTEM"
    │   "Seluruh sistem dijalankan oleh FULL AI AGENTS 24/7."
    │
    ├─ Calls get_inline_keyboard(menu_name)
    │   ├─ Admin menu: [📈 Market Pulse] [📊 Signal] [⚙️ Admin Dashboard] ...
    │   └─ Main menu: [📈 Market Pulse] [📊 Signal] [💳 Upgrade] [❓ Help] ...
    │
    └─ Sends formatted message with inline keyboard
```

**No auto-registration in VilonaBot `/start`.** The `/start` handler does NOT create a user record. Registration happens separately:
- **Members system** (`members/ensure_member()`) — called when user first uses `/analyze` or other commands
- **Subscription bot** — auto-registers on `/start`
- **Agent bot** — admin-only, no public registration

**Critical gap:** A user who only sends `/start` and never sends another command has NO database record.

### 2.2 User Database Schema (Production)

**There is NO single unified User model.** Three separate databases hold user data:

#### A. Main Members DB — `data/vilona_tradefx/members.db`

```sql
CREATE TABLE members (
    chat_id       TEXT PRIMARY KEY,       -- Telegram chat ID (e.g., "5220170786")
    nama          TEXT DEFAULT '',        -- Display name
    username      TEXT DEFAULT '',        -- Telegram @username
    tier          TEXT DEFAULT 'starter',  -- starter | pro | elite | lifetime | donor
    status        TEXT DEFAULT 'trial',   -- trial | paid | expired
    joined_at     TEXT DEFAULT '',        -- ISO timestamp
    expiry        TEXT DEFAULT '',        -- ISO timestamp (subscription end)
    payment_ref   TEXT DEFAULT '',        -- Tripay merchant reference
    autosync      INTEGER DEFAULT 0,      -- Auto-sync enabled?
    quota_used    INTEGER DEFAULT 0,      -- Daily command quota used
    quota_date    TEXT DEFAULT '',        -- Quota reset date (YYYY-MM-DD)
    tags          TEXT DEFAULT ''         -- Comma-separated: 'test' for test payments
);
```

**Current state (live):**
```
tier:  donor       → 1 (paid)
       pro         → 3 (paid)
       starter     → 48 (trial)
TOTAL: 52 members (4 paid effective)
```

```json
// Mock member record
{
  "chat_id": "5220170786",
  "nama": "Andik",
  "username": "@andikveris",
  "tier": "pro",
  "status": "paid",
  "joined_at": "2026-04-15T10:22:00Z",
  "expiry": "2026-07-15T10:22:00Z",
  "payment_ref": "VTFX-pro-5220170786-1718430120",
  "autosync": 1,
  "quota_used": 12,
  "quota_date": "2026-06-14",
  "tags": ""
}
```

**Free vs Premium detection:**
```python
# members/__init__.py
def is_premium(chat_id: str) -> bool:
    member = get_member(chat_id)
    if not member:
        return False
    # Exclude test accounts from paid counts
    if 'test' in (member.get('tags') or ''):
        return False
    # Must be 'paid' AND not expired
    if member.get('status') != 'paid':
        return False
    expiry = member.get('expiry', '')
    if expiry and datetime.fromisoformat(expiry) < datetime.now():
        return False
    return True
```

#### B. Subscription Bot DB — `data/subscription_bot.db`

```sql
CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY,    -- Auto-increment
    chat_id       INTEGER NOT NULL,       -- Telegram chat ID
    username      TEXT DEFAULT '',
    first_name    TEXT DEFAULT '',
    joined_at     INTEGER NOT NULL,       -- Unix timestamp
    is_admin      INTEGER DEFAULT 0,
    is_active     INTEGER DEFAULT 1,
    language_code TEXT DEFAULT 'en'
);
```

#### C. Platform Link DB — `user_platforms` table in `data/tradebot.db`

```sql
CREATE TABLE user_platforms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,          -- References members.chat_id
    platform       TEXT NOT NULL,          -- stockity | deriv | ccxt | mt5
    label          TEXT DEFAULT 'main',
    email          TEXT DEFAULT '',
    password       TEXT DEFAULT '',        -- PLAINTEXT ⚠️
    credentials    TEXT DEFAULT '{}',      -- JSON, varies by platform
    currency       TEXT DEFAULT 'USD',
    broker_user_id TEXT DEFAULT '',
    status         TEXT DEFAULT 'active',
    linked_at      TEXT NOT NULL,
    updated_at     TEXT,
    UNIQUE(user_id, platform, label)
);
```

### 2.3 Tier Permissions & Quotas

| Tier | Status | Daily Quota | Features |
|------|--------|-------------|----------|
| `starter` | `trial` | 5 `/analyze` per day | Market pulse, signals, basic menu |
| `pro` | `paid` | 50 per day | Full engines, bridge access, priority queue |
| `elite` | `paid` | unlimited (999) | All features + higher position sizes |
| `lifetime` | `paid` | unlimited | Permanent access |
| `donor` | `paid` | unlimited | Donor tier, special treatment |

---

## 3. BROKER API & AUTO-EXECUTION MAPPING

### 3.1 Per-User Broker Credential Storage

```json
// Mock user_platforms record — Stockity link
{
  "id": 42,
  "user_id": "5220170786",
  "platform": "stockity",
  "label": "main",
  "email": "user@example.com",
  "password": "PlaintextPassword123",
  "credentials": "{\"cookie\":\"eyJhbGciOi...full_auth_token...\"}",
  "currency": "USD",
  "broker_user_id": "S12345678",
  "status": "active",
  "linked_at": "2026-05-10T08:00:00Z",
  "updated_at": "2026-06-14T12:30:00Z"
}

// Mock user_platforms record — CCXT (Binance)
{
  "id": 43,
  "user_id": "5220170786",
  "platform": "ccxt",
  "label": "binance_main",
  "email": "",
  "password": "",
  "credentials": "{\"exchange\":\"binance\",\"api_key\":\"abc123...\",\"api_secret\":\"xyz789...\"}",
  "currency": "USDT",
  "broker_user_id": "",
  "status": "active",
  "linked_at": "2026-06-01T10:00:00Z",
  "updated_at": ""
}
```

**⚠️ CRITICAL SECURITY NOTE:** All credentials stored as **plaintext** in SQLite. No encryption, no hashing, no vault integration. The `PlatformLinkService` reads/writes raw JSON directly.

### 3.2 TradeExecutor User Mapping

The `TradeExecutor` class (`tradebot/pipeline/trade_executor.py`) is **single-broker**, not per-user. Per-user execution uses a factory pattern:

```
Signal arrives (with user context)
    │
    ▼
UserBrokerFactory.get_user_broker(user_id, platform, for_execution=True)
    │
    ├─ Queries user_platforms table for user's linked broker
    ├─ Creates platform-specific Broker instance with per-user credentials
    └─ Returns Broker object
    │
    ▼
TradeExecutor(broker=per_user_broker).execute(signal)
    │
    └─ Places order on user's broker account
```

**No concurrent/batch execution.** TradeExecutor processes one trade at a time per signal. Multi-user copy-trading would require:
1. Loop: for each user → get broker → execute
2. OR parallel: `asyncio.gather()` across user brokers

Current `VilonaSignalBridge._broadcast_signal()` supports **per-user queue routing**:
```python
PENDING_BY_INSTANCE[api_key:account_id]  # Targeted per-instance
PENDING_BY_KEY[api_key]                  # Per-user (API key) queue
PENDING                                  # Global broadcast
```

### 3.3 Execution Flow Summary

```
SIGNAL GENERATION                          USER DISPATCH
─────────────────                        ─────────────
SignalPipeline                            VilonaSignalBridge
  → Orchestrator                            ├─ Instance queue (targeted user+account)
  → Quality Gate                            ├─ User queue (all instances for user)
  → Middleware                              └─ Global broadcast (all subscribers)
       │                                         │
       ▼                                         ▼
  TradeExecutor                            Per-user broker
  (single-broker)                          (from user_platforms)
       │                                         │
       ▼                                         ▼
  Broker.place_order()                     Order on user's account
  (Deriv / MT5 / CCXT)
```

---

## 4. INTEGRATION POINTS FOR TIERED ROUTING

### 4.1 What Exists (Can Be Extended)

| Component | File | Capability |
|-----------|------|-----------|
| `is_premium()` | `members/__init__.py` | Free vs paid check (already used) |
| `get_all_active_subscribers()` | `members/__init__.py` | Lists all paid+non-expired members |
| `SignalPublisher` | `tradebot/services/publisher.py` | Central signal dispatch with dedup |
| `VilonaSignalBridge` | `tradebot/bots/platforms/vilona_bridge.py` | Per-user queue routing |
| `broadcast_signal_result()` | `agent/core.py` | Loops subscribers, sends DMs |
| Telegram `sendMessage` | Multiple files | Raw API call for message delivery |

### 4.2 What's Missing (Needs to Be Built)

| Gap | Description |
|-----|------------|
| **Unified routing layer** | No single class that decides: "this signal goes to channel A, user B gets DM, user C gets auto-execute" |
| **Tier-to-action mapping** | No config mapping `tier:pro` → `actions:[telegram_dm, auto_execute, bridge_push]` |
| **Per-user signal preferences** | `user_signal_preferences` table exists but unused — could store per-user symbol/direction filters |
| **Copytrade opt-in/out** | No user-facing toggle for "auto-copy my trades" |
| **Rate limiting per tier** | Quota exists for `/analyze` but not for signal delivery frequency |
| **Separate bot tokens** | All services share one token — need independent tokens for independent routing channels |

### 4.3 Proposed Routing Architecture (For Architect)

```
                    ┌─────────────────────────────────┐
                    │   UNIFIED SIGNAL DISPATCHER      │
                    │   (reads tier + preferences)     │
                    └───────────┬─────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
  │ PUBLIC CHANNEL│   │   FREE DM     │   │ PREMIUM COPY  │
  │ (all users)   │   │ (trial users) │   │ (paid users)  │
  ├───────────────┤   ├───────────────┤   ├───────────────┤
  │ - Delayed 5m  │   │ - Real-time   │   │ - Instant     │
  │ - No TP/SL    │   │ - Full signal │   │ - Full signal │
  │ - Summary only│   │ - HTML format │   │ - + execution │
  │ - Channel post│   │ - Individual  │   │ - Per-broker  │
  └───────────────┘   └───────────────┘   └───────────────┘
```

---

## APPENDIX — Key Env Vars

```bash
TELEGRAM_BOT_TOKEN=                # Shared across all bots
VILONA_TRADEFX_TELEGRAM_BOT_TOKEN= # Fallback for watchdogs
TELEGRAM_CHAT_ID=                  # Default broadcast target
ADMIN_USER_IDS=157228659,5220170786 # Admin chat IDs (comma-sep)
```

## APPENDIX — Database Files

```
data/vilona_tradefx/members.db    — Main user members (production)
data/subscription_bot.db          — Subscription bot users
data/tradebot.db                  — Core tradebot (user_platforms, trades, EA licenses, MLM)
data/payments.json                — Pending payment cache
data/api_keys.json                — Signal Bridge API keys + tiers
```
