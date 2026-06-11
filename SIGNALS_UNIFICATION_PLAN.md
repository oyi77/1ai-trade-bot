# Signals Unification Comprehensive Plan

## 🎯 Mission

Integrate **multiple signal sources** (Playstore app via RE, Firebase, external providers) into a **unified signal pipeline** in 1ai-trade-bot.

**Goal:** Single ingestion point for all signal types, normalized processing, and execution across all brokers.

---

## 📊 Current State Analysis

### Signal Types in Codebase

| Source | Signal Type | Model | Ingestion | Status |
|--------|------------|-------|-----------|--------|
| **Deriv (current)** | Binary options (CALL/PUT, digit) | `tradebot/models/signal.py` | Engines + Consensus | ✅ Working |
| **Crypto (signals_trading_bot)** | Spot/Futures (entry/SL/TP/leverage) | `signals_trading_bot/core/models.py` | Firebase listeners | ✅ Proven |
| **Playstore App (RE)** | TBD (need reverse engineering) | Unknown | TBD | ❓ Missing |
| **TradingView webhooks** | Generic (direction + price levels) | Unknown | Webhooks | ❓ Missing |
| **Discord/Telegram** | User-posted signals | Unknown | Bot parsing | ❓ Missing |

### Current Signal Flow (Deriv-centric)

```
Engines (Deriv patterns: MOMEN, ADJACENCY, STREAK)
    ↓
EngineConsensus (weighted voting)
    ↓
MTFConsensus (5-timeframe hierarchy)
    ↓
QualityGate (grading A/B/C)
    ↓
Signal (CALL/PUT + digit)
    ↓
MiddlewareChain (validation, rate limit, dedup)
    ↓
TradeExecutor → Deriv Broker
```

### Missing: External Signal Sources

Currently there's **NO ingestion** for:
- ✗ Playstore app signals (via RE)
- ✗ Firebase crypto signals
- ✗ TradingView webhooks
- ✗ Discord/Telegram signal parsing
- ✗ Custom REST API signals

---

## 🏗️ Proposed Architecture

### Signal Types (Unified)

```python
# All signals normalize to this common model
@dataclass
class UnifiedSignal:
    # Source identification
    source: SignalSource          # DERIV, CRYPTO_SPOT, CRYPTO_FUTURES, PLAYSTORE_APP, TRADINGVIEW, etc.
    provider: str                 # "binance", "bybit", "playstore_app", etc.
    signal_id: str                # Unique ID for deduplication
    
    # Trading parameters
    symbol: str                   # "R_75" (Deriv), "BTCUSDT" (Crypto)
    direction: str                # "CALL" / "PUT" (Deriv) or "BUY" / "SELL" (Crypto)
    
    # Deriv-specific
    predicted_digit: int | None   # 0-9 (Deriv only)
    expiry_minutes: int | None    # Time to expiration (Deriv only)
    
    # Crypto-specific
    entry_price: float | None     # Entry level
    stop_loss: float | None       # Stop loss level
    take_profit_1: float | None   # TP1
    take_profit_2: float | None   # TP2
    take_profit_3: float | None   # TP3
    leverage: int | None          # Leverage (futures only)
    
    # Confidence & validation
    confidence: float             # 0.0 - 1.0
    grade: SignalGrade            # STRONG, MODERATE, WEAK
    
    # Timestamps
    timestamp: datetime           # When signal was created
    received_at: datetime         # When we received it
    
    # Metadata
    metadata: dict                # Provider-specific data
```

### Signal Ingestion Pipeline (New)

```
┌─────────────────────────────────────────────────────────┐
│         MULTIPLE SIGNAL SOURCES (Real-time)              │
├──────────┬──────────────┬──────────────┬────────────────┤
│ Playstore│   Firebase   │ TradingView  │ Discord/       │
│   App    │   Listeners  │  Webhooks    │ Telegram       │
│   (RE)   │  (WebSocket) │   (REST)     │  (Parser)      │
└────┬─────┴──────┬───────┴──────┬───────┴────────┬──────┘
     │            │              │                │
     └────────────┼──────────────┼────────────────┘
                  │
        ┌─────────▼──────────┐
        │ SignalRouter       │
        │ (normalize + map)  │
        └────────┬───────────┘
                 │
     ┌───────────▼───────────┐
     │ UnifiedSignalStore    │
     │ (dedup + cache)       │
     │ .signals_history.json │
     └───────────┬───────────┘
                 │
        ┌────────▼──────────┐
        │ SignalValidator   │
        │ (schema + SL/TP)  │
        └────────┬──────────┘
                 │
    ┌────────────▼─────────────┐
    │ Broker Router            │
    │ (Deriv / Crypto / Both)  │
    └────────────┬─────────────┘
                 │
    ┌────────────┴──────────────┐
    │                           │
┌───▼────────┐         ┌───────▼────┐
│ Deriv Path │         │ Crypto Path│
│ (CALL/PUT) │         │ (Entry/SL) │
└───┬────────┘         └───┬────────┘
    │                      │
    └──────────┬───────────┘
               │
        ┌──────▼───────┐
        │TradeExecutor │
        │(place order) │
        └──────┬───────┘
               │
        ┌──────▼───────┐
        │TradeTracker  │
        │(record + P&L)│
        └──────────────┘
```

---

## 📋 Implementation Loop (Plan → Implement → Test → Review → Fix)

### Loop Structure

Each **major signal source** gets its own **complete loop**:
1. **PLAN** — Design integration
2. **IMPLEMENT** — Code the adapter + ingestion
3. **TEST** — Unit + integration tests
4. **REVIEW** — Code review + architecture check
5. **FIX** — Address feedback + edge cases

---

## 🔄 Loop 1: Unified Signal Model (Foundation)

### PLAN

**Goal:** Create a common signal type that all sources normalize to.

**Design:**
- Extend `tradebot/models/signal.py` with `UnifiedSignal` dataclass
- Keep backward compatibility with existing `Signal` (Deriv-only)
- Create adapters for each source type:
  - `DeriveSignalAdapter` (existing → unified)
  - `CryptoSignalAdapter` (Firebase/signals_trading_bot → unified)
  - `PlaystoreSignalAdapter` (RE → unified)
  - `TradingViewSignalAdapter` (webhook → unified)

**Files to create/modify:**
```
tradebot/models/
├── signal.py (extend with UnifiedSignal + adapters)
└── signal_adapters.py (new — adapter implementations)

tradebot/signals/
└── signal_router.py (new — route by source type)
```

**Tests:**
- Unit tests for each adapter
- Conversion accuracy (Deriv ↔ Crypto ↔ Playstore)
- Backward compatibility with existing code

### IMPLEMENT

[TO BE DONE — code will be written here]

### TEST

[TO BE DONE — test cases will be written here]

### REVIEW

[TO BE DONE — code review checkpoints]

### FIX

[TO BE DONE — iterate on feedback]

---

## 🔄 Loop 2: Playstore App Signal Extraction (RE)

### PLAN

**Goal:** Extract signals from Playstore app via reverse engineering.

**Design:**
- Analyze app (APK decompilation, network traffic analysis)
- Identify signal structure (JSON/protobuf format)
- Build extractor (HTTP interceptor / local parser)
- Create REST endpoint to fetch latest signals

**Questions to answer:**
1. What format are signals in? (JSON? Firebase? Custom binary?)
2. How are they transmitted? (HTTP? WebSocket? File-based?)
3. What data is sent? (entry/SL/TP/symbol/timeframe?)
4. Frequency? (1/day? Realtime?)
5. Authentication? (API key? Session token?)

**Files to create:**
```
tradebot/signals/
├── playstore_app.py (new — app signal extractor)
└── playstore_parser.py (new — parse app signal format)

scripts/
├── playstore_app_analysis.py (RE tools)
```

**Tests:**
- Parse sample app signals
- Validate against known good signals
- Error handling for malformed signals

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 🔄 Loop 3: Firebase Real-time Listeners (from signals_trading_bot)

### PLAN

**Goal:** Integrate Firebase listeners from signals_trading_bot.

**Design:**
- Extract `signals_trading_bot/core/auth.py` + `realtime.py`
- Create `tradebot/signals/firebase_listener.py`
- Implement event-driven ingestion (NOT polling)
- Add signal deduplication cache (`.signals_history.json`)

**Files to create:**
```
tradebot/signals/
├── firebase_listener.py (new — event listeners)
├── firebase_config.py (new — Firebase settings)
└── signal_cache.py (new — dedup + persistence)

.signals_history.json (cache file)
```

**Tests:**
- Mock Firebase listeners
- Test signal parsing
- Test deduplication logic
- Test cache persistence

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 🔄 Loop 4: TradingView Webhook Adapter

### PLAN

**Goal:** Accept signals from TradingView webhook alerts.

**Design:**
- Add `/api/signals/tradingview` webhook endpoint
- Parse TradingView alert format (direction + levels)
- Normalize to UnifiedSignal
- Validate & route to executor

**Files to create:**
```
tradebot/web/
└── signal_webhooks.py (new — webhook handlers)

tradebot/signals/
└── tradingview_adapter.py (new — parse TradingView format)
```

**Tests:**
- Sample TradingView webhook payloads
- Format validation
- Endpoint security (API key check)

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 🔄 Loop 5: Discord/Telegram Signal Parser

### PLAN

**Goal:** Extract signals from Discord/Telegram messages (user-posted).

**Design:**
- Monitor Discord channel for signal format: `BUY BTC 50000 45000 55000`
- Monitor Telegram for similar format
- Parse structured messages
- Normalize to UnifiedSignal

**Files to create:**
```
tradebot/signals/
├── discord_parser.py (new — parse Discord messages)
└── telegram_parser.py (new — parse Telegram messages)
```

**Tests:**
- Sample message parsing
- Format validation
- Error handling for malformed messages

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 🔄 Loop 6: Signal Validation & Quality Gate

### PLAN

**Goal:** Validate all signals before execution.

**Design:**
- SL/TP price checks (SL must be below entry for BUY)
- Leverage validation (max 100x for futures)
- Symbol validation (exists on broker)
- Risk checks (position size limits)
- Deduplication (no duplicate entries within 5 min)

**Files to modify:**
```
tradebot/pipeline/
└── quality_gate.py (extend for crypto signals)
```

**Tests:**
- Valid signal acceptance
- Invalid signal rejection
- Risk limit enforcement

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 🔄 Loop 7: Broker Routing (Deriv + Crypto)

### PLAN

**Goal:** Route signals to correct broker based on signal type.

**Design:**
- Deriv signals → `tradebot/brokers/deriv/` (CALL/PUT)
- Crypto signals → `tradebot/brokers/direct_executors.py` (Bybit/Binance/Bitget)
- Auto-detect signal type and route

**Files to create/modify:**
```
tradebot/pipeline/
└── broker_router.py (new — route by signal type)

tradebot/brokers/
├── direct_executors.py (new — crypto execution)
└── deriv/ (existing)
```

**Tests:**
- Deriv signals route correctly
- Crypto signals route correctly
- Mixed portfolios work

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 🔄 Loop 8: Real-time Position Monitoring

### PLAN

**Goal:** Monitor open positions and auto-close on STOP signals.

**Design:**
- Track all open positions
- Listen for STOP/CLOSE notifications
- Immediately market-close on stop signal
- Update P&L

**Files to create:**
```
tradebot/monitoring/
├── position_monitor.py (new — real-time tracking)
└── auto_closer.py (new — auto-close logic)
```

**Tests:**
- Position tracking accuracy
- Auto-close execution
- P&L calculation

### IMPLEMENT

[TO BE DONE]

### TEST

[TO BE DONE]

### REVIEW

[TO BE DONE]

### FIX

[TO BE DONE]

---

## 📊 Implementation Timeline

| Loop | Feature | Priority | Est. Effort | Commits |
|------|---------|----------|-------------|---------|
| 1 | Unified Signal Model | **CRITICAL** | 2 days | 4-5 |
| 2 | Playstore App RE | **CRITICAL** | 5 days | 6-8 |
| 3 | Firebase Listeners | **HIGH** | 3 days | 4-5 |
| 4 | TradingView Webhooks | **MEDIUM** | 2 days | 3-4 |
| 5 | Discord/Telegram Parser | **MEDIUM** | 2 days | 3-4 |
| 6 | Quality Gate (Extended) | **HIGH** | 2 days | 3-4 |
| 7 | Broker Routing | **HIGH** | 3 days | 4-5 |
| 8 | Position Monitoring | **HIGH** | 3 days | 4-5 |
| **Total** | | | **22 days** | **31-40** |

---

## 📈 Expected Outcomes

### Before Unification
```
Signal sources: Deriv only (via 11 engines)
Latency: 5-10 min (polling-based)
Brokers: Deriv only
Signals/day: ~50-100
Win rate: Variable (by engine)
```

### After Unification
```
Signal sources: Deriv + Crypto + Playstore + TradingView + Discord/Telegram
Latency: 1.5-3s (event-driven + real-time)
Brokers: Deriv + Bybit + Binance + Bitget
Signals/day: 500-1000+ (multiple sources)
Win rate: Leveraging best performers + arbitrage across sources
Position auto-close: Enabled (no stuck positions)
```

---

## 🚀 Next Step

Which **loop** should we start with?

1. **Loop 1 (Unified Signal Model)** — Foundation, must do first
2. **Loop 2 (Playstore App RE)** — Critical for data, complex RE work
3. **Loop 3 (Firebase)** — Proven, straightforward integration
4. **Others** — Sequence based on priority

**Recommendation:** Start with Loop 1 + Loop 3 in parallel (signal model + firebase listeners), then move to Loop 2 (playstore RE).
