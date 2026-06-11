# Bot Architecture Integration Roadmap

## Executive Summary

**Current Status:** 70% unified, 30% technical debt remaining

**Main Issues:**
1. 🚨 **CRITICAL:** Database fragmentation — user/trade data scattered across 4 SQLite files
2. 🚨 **HIGH:** Signal generation has 5 entry points with duplicate code
3. ⚠️ **MEDIUM:** Inconsistent error handling, no resilience layer
4. ⚠️ **MEDIUM:** Configuration scattered across 3 patterns

**Timeline:** 230 hours (4 phases, ~6 months)

---

## Phase 1: Stabilization (Weeks 1-2) 🔥

**Goal:** Make production code safe without major refactoring.

### 1.1 Deprecate Subscription-Bot 
- Mark obsolete (update README)
- Archive to read-only reference
- **Effort:** 5 hours

### 1.2 Add Resilience Layer
- Circuit breaker for all external calls
- Exponential backoff + retry logic
- Timeout enforcement
- **Files:** Create `tradebot/engines/resilience.py` (if not exists)
- **Effort:** 25 hours

### 1.3 Unified Error Types
- Define `SignalError`, `BrokerError`, `PaymentError`, `ConfigError`
- Update all handlers to catch + log
- Return typed responses (never raw exceptions)
- **Files:** Update `tradebot/exceptions.py`
- **Effort:** 10 hours

### 1.4 Add Health Checks
- Endpoint: `/health` (extend `tradebot/web/server.py`)
- Check:
  - Broker connectivity (Stockity, Deriv, MT5)
  - Signal source availability
  - Database accessibility
  - Payment gateway status
- **Files:** Create `tradebot/services/health.py`
- **Effort:** 15 hours

**Phase 1 Total:** 55 hours (1-2 weeks)

---

## Phase 2: Data Consolidation (Weeks 3-6) 📊

**Goal:** Single source of truth for all persistent data.

### 2.1 Unified Database Schema

**Current fragmentation:**
```
/bots/subscription-bot/subscription_bot.db  (users, subscriptions, trades)
/tradebot/data/tradebot.db                  (signals, affiliates, trades again)
~/.phantomfx/positions.db                   (positions)
~/.vilona/market_data.db                    (price history)
```

**Target:** Single `/tradebot/data/unified.db` with schema:

```sql
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    telegram_id INTEGER,
    plan TEXT,
    subscription_expires_at INTEGER,
    created_at INTEGER
);

CREATE TABLE linked_accounts (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    platform TEXT,  -- stockity/deriv/mt5
    api_key_encrypted TEXT,
    is_active INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    symbol TEXT,
    direction TEXT,
    amount REAL,
    platform TEXT,
    order_id TEXT,
    status TEXT,
    payout REAL,
    opened_at INTEGER,
    closed_at INTEGER,
    metadata TEXT,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE signals (
    id TEXT PRIMARY KEY,
    symbol TEXT,
    direction TEXT,
    confidence REAL,
    source TEXT,
    grade TEXT,
    generated_at INTEGER,
    metadata TEXT
);

CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    plan TEXT,
    amount_paid REAL,
    expires_at INTEGER,
    created_at INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE affiliates (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    referral_code TEXT UNIQUE,
    commission_rate REAL,
    total_earned REAL,
    created_at INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);
```

**Implementation:**
1. Create migration script: `scripts/migrate_to_unified_schema.py`
   - Read from old DBs
   - Insert into new schema
   - Verify counts match
   - Run in staging first
2. Update all data access code
   - Create ORM layer: `tradebot/models/db.py` (use SQLAlchemy)
   - Replace direct SQL queries
   - Unit test each model
3. Deprecate old database files

**Files to Create:**
- `tradebot/models/db.py` — ORM models
- `tradebot/storage/unified.py` — DAO layer
- `scripts/migrate_to_unified_schema.py` — migration script
- `tests/test_unified_db.py` — test schema + migrations

**Effort:** 40 hours

### 2.2 Unified Signal Ingestor

**Problem:** Signal generation in 5 places with different error handling

```
1. bots/subscription-bot/signaler.py
2. tradebot/bots/stockity/bot.py
3. tradebot/engines/autonomous_worker.py
4. tradebot/signals/binance.py
5. Web API /api/signal endpoint
```

**Solution:** Create `SignalIngestor` class

```python
# tradebot/signals/ingestor.py

class SignalIngestor:
    def __init__(self, cache_ttl: int = 300):
        self.cache_ttl = cache_ttl
        self._cache = {}
    
    @resilient_call(max_retries=3, timeout=10)
    async def fetch_signal(
        self, 
        symbol: str, 
        source: SignalSource,
        force_refresh: bool = False
    ) -> Signal | None:
        """Unified signal fetch with retry + cache."""
        # Check cache
        # Fetch from source
        # Cache result
        # Return Signal or None
```

**Migration Path:**
1. Create `SignalIngestor` class
2. Update all 5 callers to use it
3. Add caching + retry transparently
4. Delete old duplicate code

**Files to Create:**
- `tradebot/signals/ingestor.py` — main class

**Files to Modify:**
- `bots/subscription-bot/signaler.py` — use ingestor
- `tradebot/bots/stockity/bot.py` — use ingestor
- `tradebot/engines/autonomous_worker.py` — use ingestor
- `tradebot/web/server.py` — use ingestor for API

**Effort:** 20 hours

### 2.3 Unified Broker Execution

**Problem:** Two Stockity implementations with different retry logic

**Solution:** Merge into single `StockityBroker` class

```python
# tradebot/brokers/stockity/broker.py

class StockityBroker(BaseBroker):
    async def place_trade(self, symbol, direction, amount, **kwargs):
        """
        1. Try WS (fast)
        2. Fallback to REST
        3. Queue for async retry if both fail
        """
```

**Migration:**
1. Merge `bots/subscription-bot/trade_client.py` logic into `StockityBroker`
2. Add REST fallback + queueing
3. Update `ProactiveSignaler` to use merged broker
4. Delete `trade_client.py`

**Effort:** 15 hours

**Phase 2 Total:** 75 hours (3-4 weeks)

---

## Phase 3: Code Quality (Weeks 7-11) 🏗️

**Goal:** Eliminate technical debt, improve maintainability.

### 3.1 Centralized Configuration

**Problem:** Three config patterns (manual parsing, Pydantic, os.environ)

**Solution:** All config → Pydantic

```python
# tradebot/config/settings.py (the only config file)

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str
    admin_user_ids: str = ""
    
    # Brokers
    stockity_cookie: str = ""
    stockity_authtoken: str = ""
    deriv_app_id: str = ""
    mt5_account: str = ""
    
    # Payment
    tripay_merchant_code: str = ""
    tripay_api_key: str = ""
    
    # Database
    data_dir: str = "data"
    
    class Config:
        env_file = ".env"
```

**Migration:**
1. Update `tradebot/config/settings.py` to include all keys
2. Delete `bots/subscription-bot/config.py`
3. Update VilonaBot to use `settings` instead of `os.environ.get()`
4. Add validation (log level, port ranges, etc.)

**Effort:** 10 hours

### 3.2 Resilience Layer (if not in Phase 1)

**Problem:** No circuit breaker, inconsistent retry logic

**Solution:** Create decorator + circuit breaker class

```python
# tradebot/engines/resilience.py

@resilient_call(max_retries=3, timeout=10)
async def fetch_signal(symbol):
    return await stockity_http.generate(symbol)
```

**Effort:** 25 hours

### 3.3 Structured Logging

**Problem:** Logs scattered, hard to correlate user activity

**Solution:** JSON logging + correlation IDs

```python
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger("tradebot")
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter())
logger.addHandler(handler)

# Usage
logger.info("Signal generated", extra={
    "correlation_id": "abc-123",
    "user_id": 12345,
    "symbol": "CRYPTO_IDX",
    "confidence": 0.85
})
```

**Effort:** 15 hours

### 3.4 Type Safety

**Problem:** Some code uses `any` type, missing type hints

**Solution:** Full type coverage + mypy

```python
# Bad
def generate_signal(data):
    return data

# Good
async def generate_signal(
    symbol: str,
    source: SignalSource = SignalSource.STOCKITY
) -> Signal | None:
    pass
```

**Effort:** 20 hours

### 3.5 Test Coverage

**Problem:** Sparse tests, high untested code

**Solution:** Unit + integration tests

```python
# tests/test_unified_db.py
def test_user_creation():
    user = User(user_id="123", plan="basic")
    db.create_user(user)
    assert db.get_user("123").plan == "basic"

# tests/test_signal_ingestor.py
@pytest.mark.asyncio
async def test_signal_ingestor_retry():
    ingestor = SignalIngestor(cache_ttl=0)
    with patch('signals.stockity_http.generate') as mock:
        mock.side_effect = [TimeoutError(), Signal(...)]
        signal = await ingestor.fetch_signal("CRYPTO_IDX", SignalSource.STOCKITY)
        assert signal is not None
        assert mock.call_count == 2  # Retried once
```

**Coverage targets:**
- Signal models: 100%
- Trade models: 100%
- User models: 90%
- Broker adapters: 80%
- Services: 80%

**Effort:** 30 hours

**Phase 3 Total:** 100 hours (4-5 weeks)

---

## Phase 4: Architecture Cleanup (Weeks 12+) 🎯

**Goal:** Production-ready, observable, scalable.

### 4.1 Delete Legacy Code

**Files to remove (after Phase 2-3 complete):**
- Delete `/bots/subscription-bot/` directory entirely
- Delete `scripts/_legacy/` if not referenced
- Archive deriv legacy patterns (already extracted to engines)

**Effort:** 5 hours

### 4.2 Add Observability

**Problem:** Can't trace request → signal → trade → payout

**Solution:** Distributed tracing + metrics

```python
from opentelemetry import trace, metrics

tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

@tracer.start_as_current_span("fetch_signal")
async def fetch_signal(symbol):
    meter.create_counter("signal.fetches").add(1)
    # ...

@tracer.start_as_current_span("place_trade")
async def place_trade(symbol, direction):
    meter.create_histogram("trade.execution_ms").record(elapsed_ms)
    # ...
```

**Exports to:**
- Datadog / AWS X-Ray (for production)
- Jaeger (for local dev)
- Prometheus (for metrics)

**Effort:** 40 hours

### 4.3 Service Separation (Optional)

**If scaling becomes bottleneck:**

Separate into microservices:
1. **Signal Service** — FastAPI, fetches signals, caches them
2. **Broker Service** — gRPC, executes trades
3. **User Service** — FastAPI, manages users/subscriptions
4. **Job Queue** — Celery, async tasks (signal scanning, notifs)

**Only do if:**
- Single bot can't handle load
- Different services need independent scaling
- Need language/tech diversity

**Effort:** 60+ hours (probably not needed yet)

**Phase 4 Total:** 105 hours (5-6 weeks, optional)

---

## Prioritized Action Items

### Now (Do First)
- [ ] **Deprecate subscription-bot** (mark obsolete) — 5 hours
- [ ] **Add circuit breaker** to all external calls — 25 hours
- [ ] **Define error types** (SignalError, BrokerError, etc.) — 10 hours
- [ ] **Add `/health` endpoint** — 15 hours

**Subtotal: 55 hours (1-2 weeks)**

### Next 4 weeks
- [ ] **Unified database schema** (migrate all data) — 40 hours
- [ ] **Signal ingestor** (consolidate 5 entry points) — 20 hours
- [ ] **Unified broker** (merge trade client + broker) — 15 hours

**Subtotal: 75 hours**

### Next 4-5 weeks
- [ ] **Centralized config** (all Pydantic) — 10 hours
- [ ] **Structured logging** (JSON + correlation IDs) — 15 hours
- [ ] **Type safety** (eliminate `any`, mypy 100%) — 20 hours
- [ ] **Test coverage** (aim for 80%+) — 30 hours

**Subtotal: 100 hours**

### Optional (Later)
- [ ] **Delete legacy code** — 5 hours
- [ ] **Add observability** (tracing + metrics) — 40 hours
- [ ] **Microservices** (only if needed) — 60+ hours

**Subtotal: 105+ hours (optional)**

---

## Success Criteria

### Phase 1 ✅
- No unhandled exceptions crash bots
- All external calls have timeout + retry
- Health check shows system status
- README says "subscription-bot deprecated"

### Phase 2 ✅
- Single database for all data
- Signal generation goes through one code path
- Broker execution unified (1 Stockity impl)
- Tests pass for migrations

### Phase 3 ✅
- Config validated at startup
- All errors logged with correlation ID
- Type hints on 100% of public functions
- Test coverage >80%

### Phase 4 ✅
- Can trace request → signal → trade → payout
- Legacy code deleted (archive only)
- Deployment guide uses new paths
- Ops team can debug with traces

---

## Risk Mitigation

### High Risk: Database Migration
**Mitigation:**
1. Create migration script that's idempotent
2. Test on staging DB first
3. Backup old DBs before migration
4. Ability to rollback (keep old DBs for 2 weeks)
5. Gradual data migration (not all-at-once)

### High Risk: Breaking Changes
**Mitigation:**
1. New code runs parallel with old (feature flags)
2. Gradual cutover (10% → 50% → 100%)
3. Monitoring + alerts for failures
4. Instant rollback plan (revert to old code)

### Medium Risk: Lost Data
**Mitigation:**
1. Data validation before migration
2. Checksums on counts (old DB count == new DB count)
3. Audit trail in new DB
4. Point-in-time recovery from backup

---

## Success Metrics

Track these to measure progress:

### Reliability
- Crash-free hours (target: 99.9%)
- Error recovery time (target: <5 min)
- Signal delivery latency (target: <2 sec)

### Code Quality
- Test coverage (target: >80%)
- Type coverage (target: 100%)
- Cyclomatic complexity (target: <10 per function)

### Maintainability
- Code duplication ratio (target: <10%)
- Avg lines per function (target: <20)
- Documentation coverage (target: >90%)

---

## Questions to Answer

Before starting Phase 1:
1. Who owns the subscription-bot directory? (Deprecate with their approval)
2. Are there active users still on subscription-bot? (Migrate first)
3. Staging environment available for testing? (Required)
4. Database backup strategy in place? (Required)
5. Who is SRE/DevOps lead for deployments?

Before starting Phase 2:
1. Confirmed old DBs can be archived? (5-10 GB?)
2. Data consistency checks automated?
3. Rollback procedure tested?
4. Monitoring alerts configured?

---

END OF ROADMAP
