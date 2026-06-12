# 🧠 1ai-trade-bot Codebase Memory, Guardrails, and AI Safety Manual

This document is the **single source of truth** for developers and AI/LLM engineering agents. It defines the system architecture, directory boundaries, database schemas, external API payloads, and strict coding conventions to prevent hallucinations, regression, or duplicate logic.

---

## 1. System Architecture & Entry Points

* **One Process Model**: Both the FastAPI Web Server and the Telegram Bot (`VilonaBot`) run inside a single parent event loop.
* **Primary Entry Point**: `python -m tradebot` (starts both uvicorn and the bot application).
* **Port Mapping**: FastAPI listens on port `8889` (local development). Cloudflare Router routes requests from `https://tradebot.aitradepulse.com` to `localhost:8889` via Nginx reverse proxy.
* **Process Watchdog**: `tradebot/monitoring/watchdog.py` runs as an active systemd monitoring service checking bot logs freshness and Telegram reachability, auto-restarting the process via systemctl if failures occur.

---

## 2. Directory & Component Scope

* **`tradebot/web/`**: Serves all admin views (`/admin`, `/admin/monitoring`, `/admin/plans`), public HTML templates, payment callbacks (`/api/webhook/tripay`), and telemetry JSON APIs (`/api/monitoring/*`).
* **`tradebot/bots/`**: Mixin-based composition for the platform bot. Sub-packages are isolated in `tradebot/bots/platforms/vilona/`:
  * `bot.py` (lifecycle and event router)
  * `commands.py` (30+ user and admin command handlers)
  * `analysis.py` (AI consensus and mechanical analysis loop)
  * `callbacks.py` (payment status checking and inline menu queries)
  * `helpers.py` (spot-futures offset calculation and output layout templates)
* **`tradebot/services/`**: Houses central business logic (Plans, Payment, Health, Watchdog, Menu, Signal Calculator, Members, etc.) utilized across bot platforms and web views.
* **`tradebot/signals/`**: Core tick data wrappers implementing `BaseDataSource`. Registers 8 distinct tick sources (Yahoo, Binance, Deriv, MT5, Forex, Stockity, CCXT, Firebase) in `MarketAggregator` (`tradebot/signals/market.py`).
* **`tradebot/engines/`**: 11 analytical engines (SMC, FVG, Liquidity, Sweep, Chaos, CRT/TBS, TV, Quant, Hermes, Layering, Session Levels) mapped in `Registry`.
* **`tradebot/storage/`**: Interfaces for SQLite persistent storage and hot/cold memory cache (`TieredCache`).

---

## 3. Database Schema Blueprint

### 3.1 Members Database (`data/vilona_tradefx/members.db`)

#### `members` Table
Stores user subscription tier and daily quota trackers.
* `chat_id` TEXT PRIMARY KEY (Telegram User/Chat ID)
* `nama` TEXT (User Name)
* `username` TEXT (Telegram Handle)
* `tier` TEXT DEFAULT 'starter' (`starter`, `pro`, `elite`, `lifetime`)
* `status` TEXT DEFAULT 'trial' (`trial`, `paid`, `expired`)
* `joined_at` TEXT (ISO Timestamp)
* `expiry` TEXT (ISO Timestamp)
* `quota_used` INTEGER DEFAULT 0 (Resets daily)
* `quota_reset_at` TEXT (ISO Timestamp)

#### `payment_orders` Table
Stores payment request metadata and invoicing states.
* `merchant_ref` TEXT PRIMARY KEY (Unique reference generated for gateway)
* `chat_id` TEXT (Telegram User ID)
* `amount` INTEGER (Price in IDR)
* `product_key` TEXT (e.g., `subscribe:pro:30`)
* `gateway` TEXT DEFAULT 'tripay'
* `payload` TEXT (JSON serialized raw API response)
* `status` TEXT DEFAULT 'pending' (`pending`, `paid`, `expired`)
* `created_at` TEXT (ISO Timestamp)
* `paid_at` TEXT (ISO Timestamp)

---

### 3.2 System State & Sequence Database (`data/tradebot.db`)

#### `system_state` Table
* `key` TEXT PRIMARY KEY
* `value` TEXT
* `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP

#### `sequences` Table
Maintains historical execution sequences for digit-match strategies.
* `id` TEXT PRIMARY KEY (Sequence ID)
* `symbol` TEXT
* `digit` INTEGER
* `shots` INTEGER
* `status` TEXT DEFAULT 'ACTIVE' (`ACTIVE`, `RESOLVED`)

#### `shots` Table
Tracks individual trades executed inside an active sequence.
* `id` TEXT PRIMARY KEY (Shot ID / Ticket ID)
* `seq_id` TEXT (References `sequences.id`)
* `shot_num` INTEGER
* `digit` INTEGER
* `contract_id` TEXT
* `actual_digit` INTEGER DEFAULT -1
* `status` TEXT DEFAULT 'PENDING' (`PENDING`, `WON`, `LOST`)
* `pnl` REAL DEFAULT 0.0

---

## 4. API & Integration Protocols

### 4.1 Tripay Callback Signature Validation
Tripay signatures **MUST** be verified in the webhook handler to prevent spoofing:
* **Algorithm**: HMAC-SHA256
* **Key**: `TRIPAY_PRIVATE_KEY` (from `.env`)
* **Input**: Raw request body (`request.body`)
* **Signature match**: Verify header `X-Callback-Signature` equals `HMAC-SHA256(raw_body)`.

### 4.2 Meta Conversions API (Meta CAPI)
All paid invoices fire a conversions pixel directly from the backend server:
* **Endpoint**: `https://graph.facebook.com/v19.0/{pixel_id}/events`
* **JSON Payload Format**:
  ```json
  {
    "data": [{
      "event_name": "Purchase",
      "event_time": 1780000000,
      "event_source_url": "https://phantomfx.aitradepulse.com",
      "user_data": {
        "external_id": "sha256_hashed_telegram_id"
      },
      "custom_data": {
        "value": 150000,
        "currency": "IDR"
      }
    }]
  }
  ```

### 4.3 Grok News (xAI) API
* **Endpoint**: `https://api.x.ai/v1/chat/completions`
* **Model**: `grok-2-latest`
* **System Prompt Parameters**: System prompts must inject the **SKC (Struktur, Konfluensi, Konteks) Scoring Engine** and enforce the 7 constitutional laws of tradebot signal validation.

### 4.4 Gold Spot Price API
* **Endpoint**: `https://api.gold-api.com/price/XAU` (free public endpoint, no API key required).

---

## 5. Strict Coding Rules & Guardrails

1. **Never suppress type errors**: Avoid `cast(Any, ...)` or arbitrary monkey patching. Solve typing conflicts natively.
2. **Never use silent except blocks**: Swallowing exceptions (`except: pass`) without a log is strictly prohibited. Use `LOG.warning("reason: %s", exc)` or `LOG.error`.
3. **Avoid class-level `datetime.UTC` calls**: Binds the name `datetime` to the `datetime.datetime` class. Calling `datetime.UTC` raises `AttributeError: type object 'datetime.datetime' has no attribute 'UTC'`. Always use `timezone.utc` (after `from datetime import timezone`) or `import datetime` and use `datetime.UTC`.
4. **0% Truncation Constraint**: Never emit code block templates with `...` or `TODO` comments. All updates must consist of fully written, compiles-valid, formatted logic.
5. **Pair Tests with Code**: Always verify changes by writing a companion test inside `tests/` or updating helper execution scripts.
6. **Git Commit Message Format**: 
   * Commit messages **must** use the following pattern: `<type>: <subject> — <detail>` (en-dash ` — `, not hyphen).
   * Commits must end with:
     ```text
     Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
     ```
     Along with the body tag: `Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)`
