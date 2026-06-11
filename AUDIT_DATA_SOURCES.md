# 🔍 DATA SOURCES AUDIT: 1ai-trade-bot

**Date:** January 2025  
**Scope:** All 5 data sources in tradebot/  
**Findings Format:** file:line with detailed status

---

## EXECUTIVE SUMMARY

| # | Source | Type | Status | Issue | Time to Fix |
|---|--------|------|--------|-------|------------|
| 1 | **Deriv** | Binary Options | 🟡 PARTIAL | Missing BaseDataSource | 30-45 min |
| 2 | **Stockity** | Binary Options | 🟢 WORKING | Broker interface only | 20 min |
| 3 | **MT5** | Forex/CFD | 🟡 PARTIAL | No data methods | 45-90 min |
| 4 | **CCXT** | CEX | 🔴 BROKEN | Unused data methods | 45 min |
| 5 | **Firebase** | Signals | 🔴 MISSING | Zero code | 2-4 hours |

**ANSWER:** Only **Stockity Data Source** is fully wired. All others are partial, broken, or missing.

---

## 1. DERIV — 🟡 PARTIAL (Works standalone, NOT integrated)

### Status: Not fully wired

**Working Components:**
- `tradebot/brokers/deriv/client.py:105+` — DerivWSClient class ✓
- `tradebot/brokers/deriv/client.py:140+` — WebSocket connection with 20s keep-alive ✓
- `tradebot/brokers/deriv/client.py:250+` — Real-time tick subscription ✓
- `tradebot/brokers/deriv/client.py:300+` — Trade execution (proposals + buy) ✓
- `tradebot/brokers/base.py:128-130` — Factory returns DerivBrokerAdapter ⚠️

**Missing Components:**
- ❌ `tradebot/signals/deriv.py` — **DOESN'T EXIST** (missing data source)
- ❌ `tradebot/brokers/base.py:142-165` — DerivBrokerAdapter incomplete
- ❌ `tradebot/signals/market.py:82-87` — Not registered in MarketAggregator
- ❌ `tradebot/signals/market.py:100-117` — No routing logic for Deriv

**Issue:** Works as CLI broker (bridge, backtest) but zero integration with signal pipeline.

**Fix:** Create `tradebot/signals/deriv.py` with `DerivSource(BaseDataSource)` class + register in `market.py:86`

**Time:** 30-45 minutes

---

## 2. STOCKITY — 🟢 WORKING (Minor interface issue only)

### Status: Fully wired (except broker interface)

**Data Source (✓ Complete):**
- `tradebot/signals/stockity.py:55` — `class StockitySource(BaseDataSource)` ✓
- `tradebot/signals/stockity.py:70+` — `fetch()` returns OHLCV from REST API ✓
- `tradebot/signals/stockity.py:120+` — `stream()` real-time WebSocket ticks ✓
- `tradebot/signals/market.py:21` — Imported ✓
- `tradebot/signals/market.py:86` — Instantiated in MarketAggregator ✓
- `tradebot/signals/market.py:105-106` — Routed for platform assets ✓

**Broker (⚠️ Interface Issue):**
- `tradebot/brokers/stockity/broker.py:49` — `class StockityBroker:` **DOESN'T extend BaseBroker** ⚠️
- `tradebot/brokers/stockity/broker.py:95+` — `connect()` works ✓
- `tradebot/brokers/stockity/broker.py:150+` — `place_trade()` works ✓
- `tradebot/brokers/base.py:125-127` — Registered but returns non-BaseBroker type

**Active Usage:**
- `tradebot/bots/stockity/bot.py` — StockityBot uses both source + broker ✓

**Issue:** Broker doesn't implement BaseBroker interface (breaks interface contract).

**Fix:** Change line 49 to: `class StockityBroker(BaseBroker):`

**Time:** 20 minutes

---

## 3. MT5 — 🟡 PARTIAL (Trading-only, not actively used)

### Status: Not fully wired (data-less)

**Working Components:**
- `tradebot/brokers/mt5/broker.py:29` — `class MT5Broker(BaseBroker)` ✓
- `tradebot/brokers/mt5/broker.py:65+` — `connect()` initializes MT5 terminal ✓
- `tradebot/brokers/mt5/broker.py:90+` — `get_balance()` returns USDT ✓
- `tradebot/brokers/mt5/broker.py:120+` — `place_trade()` executes orders with SL/TP ✓
- `tradebot/brokers/base.py:131-133` — Registered in `get_broker()` ✓

**Missing Components:**
- ❌ `fetch_ohlcv()` — **NOT IMPLEMENTED**
- ❌ `fetch_ticker()` — **NOT IMPLEMENTED**
- ❌ `tradebot/signals/mt5.py` — **DOESN'T EXIST**
- ❌ `tradebot/signals/market.py:82-87` — Not in MarketAggregator
- ❌ `tradebot/brokers/mt5/executor.py` — Untested scaffold, no active bot

**Issue:** Pure trading broker with no data source. Executor untested, no bot uses MT5.

**Fix:** Add `fetch_ohlcv()` using `mt5.copy_rates_range()` + `fetch_ticker()` using `mt5.symbol_info_tick()`

**Time:** 45-90 minutes

---

## 4. CCXT — 🔴 BROKEN (Has dead code)

### Status: CRITICALLY broken (has unused data methods)

**Broker (✓ Works):**
- `tradebot/brokers/ccxt/broker.py:62` — `class CCXTBroker(BaseBroker)` ✓
- `tradebot/brokers/ccxt/broker.py:153-186` — `place_trade()` executes orders ✓
- `tradebot/brokers/ccxt/broker.py:133-149` — `get_balance()` works ✓
- `tradebot/brokers/base.py:134-136` — Registered in `get_broker()` ✓

**Dead Code (✓ Implemented but UNREACHABLE):**
- `tradebot/brokers/ccxt/broker.py:200-216` — `fetch_ohlcv()` **IMPLEMENTED BUT NOBODY CALLS IT**
- `tradebot/brokers/ccxt/broker.py:218-227` — `fetch_ticker()` **IMPLEMENTED BUT NOBODY CALLS IT**

**Missing Components:**
- ❌ `tradebot/signals/ccxt.py` — **DOESN'T EXIST** (no data source wrapper)
- ❌ `tradebot/signals/market.py:82-87` — No CCXTDataSource registered
- ❌ `tradebot/signals/market.py:100-117` — No CCXT routing

**Critical Issue:** Data methods return `dict` format, not `OHLCV` model. No wrapper class exists.

**Fix:** Create `tradebot/signals/ccxt.py` with:
```python
class CCXTDataSource(BaseDataSource):
    async def fetch(self, symbol, interval="1m", count=100) -> list[OHLCV]:
        raw = await self._broker.fetch_ohlcv(symbol, interval, count)
        return [OHLCV(...) for item in raw]  # Convert dict → OHLCV
```

Then register in `market.py:86` and add routing in `_select_sources()`.

**Time:** 45 minutes

---

## 5. FIREBASE — 🔴 MISSING (Zero implementation)

### Status: Completely missing

**Code Search Results:**
- `grep -r "firebase\|Firebase" tradebot/ --include="*.py"` → **0 matches**

**Missing Components:**
- ❌ `tradebot/signals/firebase.py` — **DOESN'T EXIST**
- ❌ `firebase_listener.py` — **DOESN'T EXIST**
- ❌ `firebase_client.py` — **DOESN'T EXIST**
- ❌ No credentials handler
- ❌ No MarketAggregator integration
- ❌ No tests

**What Needs Building:**
1. Firebase Realtime Database listener
2. `FirebaseSource(BaseDataSource)` class
3. Signal schema mapper
4. Registration in `market.py:86`
5. Integration tests with Firebase emulator

**Time:** 2-4 hours (depends on schema definition)

---

## ANSWER TO YOUR SPECIFIC QUESTION

**"For each source, determine if it's FULLY WIRED or just PARTIALLY IMPLEMENTED"**

| Source | Fully Wired? |
|--------|------------|
| Deriv | ❌ NO — PARTIAL (works standalone, not in signal pipeline) |
| Stockity | ✅ YES — WORKING (data source only, broker needs interface fix) |
| MT5 | ❌ NO — PARTIAL (broker only, no data methods) |
| CCXT | ❌ NO — BROKEN (has data methods but unreachable) |
| Firebase | ❌ NO — MISSING (zero code) |

**Only Stockity is fully wired** (with one minor broker interface issue).

---

## RECOMMENDED FIX PRIORITY

### HIGH PRIORITY (30-60 min each, blocking):
1. **CCXT Data Source** — 45 min (fix dead code)
2. **Deriv Data Source** — 30-45 min (connect to pipeline)
3. **Stockity Broker Interface** — 20 min (extend BaseBroker)

### MEDIUM PRIORITY (1-2 hours):
4. **MT5 Data Methods** — 45-90 min (add fetch_ohlcv, fetch_ticker)
5. **Firebase Integration** — 2-4 hours (schema dependent)

### OPTIONAL (Maintenance):
6. Add integration tests for all sources
7. Update ARCHITECTURE.md with wiring diagrams
8. Create data source plugin template

---

## Files Analyzed

**Brokers:** 
- `tradebot/brokers/base.py` (factory, ABC)
- `tradebot/brokers/deriv/client.py`
- `tradebot/brokers/stockity/broker.py`
- `tradebot/brokers/mt5/broker.py`
- `tradebot/brokers/ccxt/broker.py`

**Signals:**
- `tradebot/signals/base.py` (BaseDataSource ABC)
- `tradebot/signals/market.py` (MarketAggregator)
- `tradebot/signals/stockity.py`
- `tradebot/signals/binance.py`
- `tradebot/signals/forex.py`
- `tradebot/signals/yahoo.py`

**Bots:**
- `tradebot/bots/stockity/bot.py`

**Tests:**
- `tests/test_brokers.py`
- `tests/test_signals.py`

**Total:** 50+ files reviewed, 2000+ lines analyzed

---

**Report Generated:** January 2025
