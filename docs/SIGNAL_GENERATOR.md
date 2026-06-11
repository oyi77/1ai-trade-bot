# Signal Generator & Donation System

Complete implementation of user-facing signal generation with **3-free-then-upsell** gating and donation support.

## What Changed

### 1. Data Models

#### `app/models/signal.py`
- **Added `SignalSource` enum**: `SYSTEM`, `USER_GENERATED`, `USER_SCAN`
- **Added to Signal model**:
  - `source`: Where the signal came from
  - `generated_by_user_id`: NULL for system-generated, user ID for user-generated

#### `app/models/user.py` (UserSubscription)
- `free_generations_used`: Counter (tracks lifetime usage)
- `generation_credits`: Per-tier quota (3 for free, 9999 for paid)
- `bonus_credits`: From donations (accumulates)

### 2. Error Handling

#### `app/core/errors.py`
- **New `GenerationLimitError` class** (HTTP 402 — Payment Required):
  - Includes `upgrade_url` and `donate_url` in response
  - Structured `to_dict()` with `actions` section showing CTA buttons
  - Sample: "You've used all 3 free signal generations. Upgrade your plan for unlimited generations, or make a donation to get bonus credits."

### 3. Schemas

#### `app/schemas/signal.py`
- **`SCANNABLE_SYMBOLS`**: 15 major pairs (BTC, ETH, BNB, SOL, etc.)
- **`SUPPORTED_TIMEFRAMES`**: 1m, 5m, 15m, 1h, 4h, 1d
- **`SUPPORTED_INDICATORS`**: RSI, MACD, EMA cross, Bollinger, VWAP, Stochastic, ATR, Ichimoku, SuperTrend
- **`GenerateSignalRequest`**: symbol + timeframe + indicator list
  - Validates symbol in `SCANNABLE_SYMBOLS`
  - Validates timeframe and indicators
- **`ScanMarketsRequest`**: multi-symbol scan with confidence filter
- **`GenerationQuotaResponse`**: tier, total, used, remaining, is_unlimited, prompts

#### `app/schemas/subscription.py`
- **`DONATION_TIERS`**: coffee ($5→5 credits), supporter ($15→20 credits), patron ($50→100), whale ($100→300)
- **`DonationRequest`**: tier selection + redirect URLs
- **`DonationResponse`**: checkout URL, session ID, credits to receive
- **`GenerationQuotaStatus`**: Embedded in subscription response
- **`SubscriptionPlan`**: Added `generation_credits` field
  - Free: 3 credits
  - Starter+: 9999 (unlimited)

### 4. Repository Layer

#### `app/repositories/signal_repo.py`
- **`create_user_signal()`**: Insert user-generated signal with source tracking
- **`count_user_generations()`**: Lifetime total of user_id's generations
- **`list_user_generated()`**: Paginated list of all signals I created

### 5. Service Layer — Core Business Logic

#### `app/services/signal_service.py` (REWRITTEN)
**Three main flows**:

1. **`list_signals()` / `view_signal()`**
   - Browse system-generated signals (existing flow)
   - Free users see free signals only
   - Enforces daily_signal_limit

2. **`generate_signal(user_id, GenerateSignalRequest)`**
   - Run TA on 1 symbol
   - Costs 1 generation credit
   - Checks quota before execution → raises `GenerationLimitError` if exhausted
   - Increments `free_generations_used`
   - Signals have `source=USER_GENERATED`
   - Expires in 4 hours

3. **`scan_markets(user_id, ScanMarketsRequest)`**
   - Run TA on multiple symbols
   - Costs 1 generation credit (not per-symbol)
   - Filters by min_confidence, returns top N
   - Signals have `source=USER_SCAN`

**Supporting methods**:
- **`_enforce_generation_quota(user_id)`**: Raises `GenerationLimitError` if limit reached
- **`_increment_generation_count(user_id)`**: Bumps counter after each use
- **`get_generation_quota(user_id)`**: Returns `GenerationQuotaResponse` with prompts
- **`_run_technical_analysis(symbol, timeframe, indicators)`**: TA integration point
  - **PRODUCTION NOTE**: Replace the random-based stub with real OHLCV + TA indicators (ccxt + `ta` library)
  - Returns: signal_type, confidence, reason, entry, SL, TP 1/2/3, risk_reward_ratio

#### `app/services/subscription_service.py` (ENHANCED)
- **`create_donation_checkout(user_id, tier, urls)`**: TriPay one-time payment for donations
  - Creates/reuses TriPay customer
  - Returns session URL + credits amount
- **`credit_bonus_generations(user_id, credits)`**: Award bonus credits from donation webhook
- **`PLAN_CATALOG`**: Updated features to mention unlimited generations for paid tiers

### 6. API Routes

#### `app/api/v1/signals/routes.py` (EXPANDED)

| Endpoint | Method | Gate | Purpose |
|----------|--------|------|---------|
| `/signals/` | GET | signals | List system signals (existing) |
| `/signals/{id}` | GET | signals | View signal detail (existing) |
| `/signals/generate` | POST | signals | Generate 1 signal → costs 1 credit |
| `/signals/scan` | POST | signals | Scan markets → costs 1 credit |
| `/signals/quota` | GET | signals | Check remaining credits + prompts |
| `/signals/my-generations` | GET | signals | List all my generated signals |

**Error handling**: 
- When quota exceeded, returns `GenerationLimitError` (402) with upgrade/donate CTAs in response body

#### `app/api/v1/subscriptions/routes.py` (EXPANDED)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/subscriptions/donate` | POST | Create donation checkout session |
| `/subscriptions/donation-tiers` | GET | List donation options + credit rewards |

### 7. Plan Updates

#### Free Tier
- Daily signal views: 5
- Signal generations: 3 lifetime ✨
- Feature: "3 signal generations (lifetime)"

#### Starter ($19.99/mo)
- Unlimited signal views
- **Unlimited signal generations** ✨
- Feature: "Unlimited signal generations"

#### Pro & Enterprise
- Unlimited everything

---

## User Flow

### First-time free user
1. Signs up → gets `free_generations_used=0`, `generation_credits=3`
2. Calls `POST /signals/generate` → generation runs
3. `free_generations_used` bumped to 1, remaining = 2
4. Calls again → remaining = 1
5. Calls again → remaining = 0
6. **Calls 4th time** → `GenerationLimitError` (402) with:
   ```json
   {
     "success": false,
     "error": "GENERATION_LIMIT_REACHED",
     "message": "You've used all 3 free signal generations. Upgrade your plan...",
     "actions": {
       "upgrade": {
         "label": "Upgrade Plan",
         "url": "/api/v1/subscriptions/plans",
         "description": "Get unlimited..."
       },
       "donate": {
         "label": "Support Us",
         "url": "/api/v1/subscriptions/donate",
         "description": "Make a donation to receive..."
       }
     },
     "usage": {"used": 3, "limit": 3}
   }
   ```

### Donation path
1. User clicks "Support Us" CTA
2. Navigates to `POST /subscriptions/donate?tier=coffee`
3. TriPay checkout for $5
4. On success: webhook grants `bonus_credits=5`
5. User can now generate 5 more signals without upgrading

### Upgrade path
1. User clicks "Upgrade Plan" CTA
2. Navigates to subscription checkout
3. After payment: `generation_credits=9999` (unlimited flag)
4. No quota enforcement anymore

---

## Technical Highlights

### Quota Enforcement
```python
# In service._enforce_generation_quota()
if generation_credits < 999:  # Not unlimited
    if free_generations_used >= (generation_credits + bonus_credits):
        raise GenerationLimitError(used, total)
```

### Generation Increment
```python
# After successful generation/scan
sub = user_repo.get_subscription(user_id)
user_repo.update_subscription(
    user_id, 
    free_generations_used=sub.free_generations_used + 1
)
```

### TA Integration Point
The `_run_technical_analysis()` method is a **stub** that returns randomized results for demo purposes. To go live:

1. Fetch real OHLCV candles via ccxt:
   ```python
   exchange = ccxt.binance()
   candles = exchange.fetch_ohlcv(symbol, timeframe)
   ```

2. Compute indicators with `ta-lib` or `pandas_ta`:
   ```python
   import talib
   rsi = talib.RSI(close_prices, timeperiod=14)
   macd, signal, hist = talib.MACD(close_prices)
   ```

3. Score confidence based on confluence (e.g., RSI < 30 + MACD bullish → 0.8 confidence)

4. Return actual entry/SL/TP from technical levels

---

## Testing

### Test File: `tests/unit/test_signal_generator.py`

Coverage:
- ✅ 3-free quota enforcement
- ✅ Symbol validation
- ✅ Indicator validation
- ✅ Donation tier structure
- ✅ Quota response format
- ✅ Error includes upgrade/donate actions

Run:
```bash
pytest tests/unit/test_signal_generator.py -v
```

---

## Migration / Deployment

### Database Changes Required
```sql
-- Add columns to users.user_subscriptions
ALTER TABLE user_subscriptions ADD COLUMN free_generations_used INTEGER DEFAULT 0;
ALTER TABLE user_subscriptions ADD COLUMN generation_credits INTEGER DEFAULT 3;
ALTER TABLE user_subscriptions ADD COLUMN bonus_credits INTEGER DEFAULT 0;

-- Add columns to signals
ALTER TABLE signals ADD COLUMN source VARCHAR(20) DEFAULT 'system';
ALTER TABLE signals ADD COLUMN generated_by_user_id INTEGER REFERENCES users(id);
CREATE INDEX idx_signals_generated_by_user_id ON signals(generated_by_user_id);
```

### Environment
- No new env vars required
- Uses existing TriPay API key for donation checkouts
- Donation tiers hardcoded in schema (can extract to `.env` if desired)

---

## Files Modified

| File | Changes |
|------|---------|
| `app/models/signal.py` | +SignalSource enum, +source, +generated_by_user_id columns |
| `app/models/user.py` | +free_generations_used, +generation_credits, +bonus_credits |
| `app/core/errors.py` | +GenerationLimitError class |
| `app/schemas/signal.py` | +GenerateSignalRequest, +ScanMarketsRequest, +GenerationQuotaResponse |
| `app/schemas/subscription.py` | +DONATION_TIERS, +DonationRequest, +DonationResponse, +generation_credits field |
| `app/repositories/signal_repo.py` | +create_user_signal, +count_user_generations, +list_user_generated |
| `app/services/signal_service.py` | REWRITTEN with generate_signal, scan_markets, quota enforcement |
| `app/services/subscription_service.py` | +create_donation_checkout, +credit_bonus_generations, updated PLAN_CATALOG |
| `app/api/v1/signals/routes.py` | +/generate, +/scan, +/quota, +/my-generations |
| `app/api/v1/subscriptions/routes.py` | +/donate, +/donation-tiers |
| `app/models/__init__.py` | +SignalSource export |
| `app/schemas/__init__.py` | +new signal/subscription schemas |
| `tests/unit/test_signal_generator.py` | NEW — 20+ test cases |

---

## Summary

✅ **User-facing signal generation** with full TA integration point
✅ **3-free-then-upsell** quota system
✅ **Donation support** ($5–$100 tiers → 5–300 bonus credits)
✅ **Backward compatible** — existing system signals untouched
✅ **Extensible** — TA stub ready for real indicators
✅ **Well-tested** — 20+ unit test cases
