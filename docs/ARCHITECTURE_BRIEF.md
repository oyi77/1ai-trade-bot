# VILONA AI TRADING ECOSYSTEM — TECHNICAL ARCHITECTURE BRIEF

> **Generated:** 2026-06-14 | **Version:** v0.2.0 | **Total Engines:** 13+1 (14)

Prepared for the Meta-Orchestrator layer builder. Contains every schema, enum, pipeline flow, and integration point needed to design a unified engine conflict-resolution orchestrator.

---

## 1. STANDARD ENGINE OUTPUT / PAYLOAD SCHEMA

### 1.1 Core Signal Dataclass (`tradebot.models.Signal`)

Every engine emits this dataclass through its `async analyze(ticks) → Signal | None` method.

```python
@dataclass
class Signal:
    symbol: str              # "XAUUSD", "BTCUSD", "ETHUSD", "USOIL"
    direction: str           # "CALL" | "PUT" | "BUY" | "SELL" | "BULLISH" | "BEARISH"
    predicted_digit: int     # 0-9, last digit prediction (binary options heritage)
    confidence: float        # 0.0 – 1.0 (auto-graded to SignalGrade below)
    source: SignalSource     # enum: MOMEN, ADJACENCY, STREAK, COLD_DIGIT, CONSENSUS, MANUAL
    grade: SignalGrade       # enum: STRONG (≥0.7), MODERATE (≥0.5), WEAK (≥0.3), NEUTRAL (0)
    entry_price: float | None
    timestamp: datetime      # UTC
    metadata: dict           # FREE-FORM — engine-specific enrichment (see §1.2)
```

### 1.2 Metadata Schema by Engine Family

**Standard Engine (SMC, FVG, Liquidity, etc.):**
```json
{
  "details": "BULLISH divergence at 2645.30 — M15 supply zone test",
  "reason": "FVG fill + OB rejection — 8/11 engines agree",
  "timeframes": {
    "D1": { "engines": {...}, "verdict": "BUY", "buy_count": 8, ... },
    "H4": { ... },
    "H1": { ... },
    "M15": { ... },
    "M5": { ... }
  },
  "mtf_alignment": "ALIGNED" | "MIXED" | "CONFLICT" | "NONE",
  "macro_trend": "BULLISH" | "BEARISH" | "NEUTRAL",
  "consensus_score": 0.78,
  "counter_trend_flags": ["M5 SELL counter-trend vs D1/H4 BULLISH"]
}
```

**Quality Gate Enriched (post-gate):**
```json
{
  "sl": 2635.50,
  "tp1": 2658.20,
  "tp2": 2670.80,
  "rr": 2.25,
  "pips_sl": 32,
  "pips_target": 72,
  "grade": "A",
  "reason": "MTF ALIGNED | BULLISH | 8/11 engines agree (73%)",
  "quality_gate": {
    "passed": true,
    "checks": {
      "consensus_threshold": { "passed": true, "value": 0.78, "min": 0.50 },
      "alignment": { "passed": true, "value": "ALIGNED" },
      "counter_trend": { "passed": true, "flags": [] },
      "macro_alignment": { "passed": true, "macro": "BULLISH" },
      "engine_agreement": {
        "passed": true, "agree": 8, "total": 11, "non_hold": 9, "agree_pct": 0.889
      }
    }
  }
}
```

**Harmonic Engine (PRZ_Active signal):**
```json
{
  "PRZ_Active": true,
  "pattern": "gartley",
  "bias": "bullish",
  "prz_upper": 1925.0,
  "prz_lower": 1920.0,
  "prz_mid": 1922.5,
  "sl": 1918.0,
  "tp1": 1940.0,
  "tp2": 1955.0,
  "confidence": 0.85,
  "points": { "X": 1900.0, "A": 2000.0, "B": 1938.2, "C": 1993.0, "D": 1921.4 },
  "ratios": { "ab_retrace": 0.618, "bc_retrace": 0.887, "cd_ext": 1.307, "xd_ext": 0.786 },
  "requires_confirmation": true,
  "confirmation_hint": "Wait for SMC order block / FVG on M5/M15 within the PRZ zone"
}
```

**MTF Consensus Gate (EXECUTE verdict → Signal):**
```json
{
  "consensus": true,
  "sl": 1918.0,
  "tp1": 1940.0,
  "tp2": 1955.0,
  "macro_alignment": true,
  "micro_trigger": "smc_choch",
  "gate_reason": "CONFIRMED: gartley BULLISH PRZ → smc_choch at 1922.5"
}
```

### 1.3 Mock Payload — Full Pipeline Output

```json
{
  "symbol": "XAUUSD",
  "direction": "BULLISH",
  "predicted_digit": 5,
  "confidence": 0.82,
  "source": "consensus",
  "grade": "STRONG",
  "entry_price": 2645.30,
  "timestamp": "2026-06-14T13:30:00Z",
  "metadata": {
    "sl": 2635.50,
    "tp1": 2658.20,
    "tp2": 2670.80,
    "rr": 2.25,
    "pips_sl": 32,
    "pips_target": 72,
    "grade": "A",
    "reason": "MTF ALIGNED | BULLISH | 8/11 engines agree (73%)",
    "mtf_alignment": "ALIGNED",
    "macro_trend": "BULLISH",
    "consensus_score": 0.78,
    "counter_trend_flags": [],
    "timeframes": {
      "D1": { "verdict": "BUY", "buy_count": 6, "sell_count": 2, "total": 8, "engines": {...} },
      "H4": { "verdict": "BUY", "buy_count": 7, "sell_count": 1, "total": 8, "engines": {...} },
      "H1": { "verdict": "BUY", "buy_count": 5, "sell_count": 3, "total": 8, "engines": {...} },
      "M15": { "verdict": "BUY", "buy_count": 6, "sell_count": 2, "total": 8, "engines": {...} },
      "M5": { "verdict": "BUY", "buy_count": 5, "sell_count": 2, "total": 7, "engines": {...} }
    },
    "quality_gate": {
      "passed": true,
      "checks": {
        "consensus_threshold": { "passed": true, "value": 0.78, "min": 0.50 },
        "engine_agreement": { "passed": true, "agree": 8, "total": 11, "non_hold": 9, "agree_pct": 0.889 }
      }
    }
  }
}
```

---

## 2. ENGINE ROSTER & WEIGHTING

### 2.1 Full Roster (13 Engines + 1 Consensus Gate = 14)

| # | Engine Name | Class | Specialization | Current Weight |
|---|------------|-------|---------------|---------------|
| 1 | `smc_scalper` | `SMCEngine` | Smart Money Concepts — Order blocks, BOS/ChoCh, FVG | Equal (1.0) |
| 2 | `fvg_detector` | `FVGEngine` | Fair Value Gap detection & fill | Equal (1.0) |
| 3 | `liquidity_zones` | `LiquidityEngine` | Liquidity zone draws & sweeps | Equal (1.0) |
| 4 | `sweep_detector` | `SweepEngine` | Stop hunt / liquidity sweep detection | Equal (1.0) |
| 5 | `chaos_filter` | `ChaosEngine` | Chaos theory / fractal filter | Equal (1.0) |
| 6 | `crt_tbs` | `CRTTBSEngine` | Candle Range Theory / TBS patterns | Equal (1.0) |
| 7 | `tv_engine` | `TVEngine` | TradingView indicator-based analysis | Equal (1.0) |
| 8 | `quant_pattern` | `QuantEngine` | Quantitative pattern matching | Equal (1.0) |
| 9 | `hermes_liquidity_hunter` | `HermesLiquidityEngine` | Hermes-specific liquidity hunting | Equal (1.0) |
| 10 | `layering` | `LayeringEngine` | Order flow layering detection | Equal (1.0) |
| 11 | `session_levels` | `SessionLevelsEngine` | Session-based S/R levels (Asia/London/NY) | Equal (1.0) |
| 12 | `whale_detector` | `WhaleEngine` | Large order / whale activity detection | Equal (1.0) |
| 13 | `harmonic` | `HarmonicEngine` | XABCD harmonic patterns (Bat, Butterfly, Gartley) | **Special** (PRZ_Active flag) |
| 14 | `mtf_consensus` | `MTFConsensusGate` | 3-tier gate: Macro(H1/H4) → Meso(M15/PRZ) → Micro(M1/M5 trigger) | **Gate** (not a signal source) |

### 2.2 Weighting & Hierarchy Architecture

**Two independent consensus layers exist:**

**A) `EngineConsensus` (Flat / Single-Timeframe)**
- All engines registered with equal weight (1.0)
- Configurable `min_confidence` (default 0.5) and `min_engines` (default 1)
- Runs all engines on same ticks → weighted average confidence → best signal wins
- Used by `SignalPipeline` for real-time processing
- **No hierarchy between engines** — pure democratic vote

**B) `MTFConsensus` (Multi-Timeframe Hierarchical)**
- 5-timeframe analysis: D1 → H4 → H1 → M15 → M5
- **Timeframe weights** (NOT engine weights):
  | Timeframe | Weight | Purpose |
  |-----------|--------|---------|
  | D1 | 0.35 | Macro filter |
  | H4 | 0.25 | Macro filter |
  | H1 | 0.20 | Structure setup |
  | M15 | 0.12 | Structure setup |
  | M5 | 0.08 | Entry trigger |
- Runs **all** engines on **each** timeframe independently
- Hierarchical verdict with counter-trend penalty (50% weight reduction)
- CONFLICT alignment → forced HOLD regardless of weighted score

**Key fact: Engines have NO inherent priority.**
- All 13 engines are peers — no leader/follower architecture
- The 14th component (`MTFConsensusGate`) is a **post-processing gate**, not a signal source
- The harmonic engine emits a special `PRZ_Active` flag → consumed by the gate for Hunt Mode

---

## 3. EXECUTION FLOW & STATE MANAGEMENT

### 3.1 Complete Signal Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. MARKET DATA INGESTION                                            │
│    MarketAggregator → fetches ticks/OHLCV per asset class           │
│    Sources: Binance (crypto), Yahoo (forex/metals), Deriv WS        │
├─────────────────────────────────────────────────────────────────────┤
│ 2. ENGINE ANALYSIS                                                  │
│    ┌─ Flat Engine Consensus (EngineConsensus)                       │
│    │  ├─ All 13 engines run async on same ticks                     │
│    │  ├─ Weighted average confidence → best signal selected         │
│    │  └─ Output: Signal | None                                      │
│    └─ MTF Consensus (MTFConsensus — separate path)                  │
│       ├─ Fetch 5 timeframes (D1/H4/H1/M15/M5)                      │
│       ├─ Run all engines per timeframe                              │
│       ├─ Vectorized macro/SNR/entry analysis                        │
│       ├─ Hierarchical verdict (BUY/SELL/HOLD)                       │
│       └─ Output: dict with verdict, scores, alignment               │
├─────────────────────────────────────────────────────────────────────┤
│ 3. SIGNAL PIPELINE (SignalPipeline)                                 │
│    ├─ Stage 0: Edge guard (reject empty ticks)                      │
│    ├─ Stage 1: EngineConsensus.analyze() with timeout               │
│    ├─ Stage 2: QualityGate (if configured)                          │
│    │   ├─ validate() → 5 checks (consensus 50%+, alignment,         │
│    │   │   counter-trend, engine agreement 50%+, macro)             │
│    │   ├─ compute_levels() → ATR-based TP/SL                       │
│    │   ├─ grade() → A/B/C with confidence boost                    │
│    │   └─ build_reason() → human-readable explanation              │
│    ├─ Stage 3: Middleware Chain                                     │
│    │   └─ Logging → RateLimit → Validation → Dedup → RiskCheck     │
│    └─ Stage 4: Event emission (signal_generated, pipeline_complete) │
├─────────────────────────────────────────────────────────────────────┤
│ 4. HARMONIC + MTF GATE PATH (Independent Pipeline)                  │
│    ┌─ HarmonicEngine.analyze() → PRZ_Active Signal                  │
│    ├─ meso_from_signal() → MesoState                                │
│    ├─ MTFConsensusGate.activate_hunt() → HUNT_MODE                  │
│    ├─ process_micro_trigger() → EXECUTE or REJECT                   │
│    ├─ check_invalidation() → REJECT (SL breached)                   │
│    └─ cleanup_expired() → REJECT (hunt TTL)                         │
├─────────────────────────────────────────────────────────────────────┤
│ 5. TRADE EXECUTION (TradeExecutor — NOT yet wired to gate)          │
│    ├─ Resolve stake from signal metadata or settings                │
│    ├─ Middleware chain pre-processing                               │
│    ├─ Broker.place_order() (Deriv / MT5 / CCXT)                    │
│    ├─ Resolve trade outcome                                         │
│    └─ Update TradeLifecycle (P&L, streaks, drawdown)                │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 State Management Inventory

| Component | State Type | Persistence | Concurrency |
|-----------|-----------|-------------|-------------|
| `EngineConsensus` | Stateless | None | Per-call |
| `MTFConsensus` | Smart cache (D1 4h TTL, H4/H1 15m) | In-memory dict | Async, single-instance |
| `SignalPipeline` | PipelineMetrics (counters, latency) | In-memory struct | Per-call |
| `MiddlewareChain` | Per-middleware state (Dedup dict, RateLimit tokens) | In-memory | Per-pipeline instance |
| `QualityGate` | Stateless (reads `signal.metadata`) | None | Per-call |
| `HarmonicEngine` | Fractal detection period (config) | Per-instance | Per-call |
| `MTFConsensusGate` | HuntSession per symbol (active hunts), verdict history | In-memory dict | Per-instance, multi-symbol |
| `TradeExecutor` | TradeLifecycle (P&L, streaks, drawdown), daily P&L | In-memory struct | Per-instance |
| `MarketAggregator` | MarketState per symbol (ticks buffer, cooldown) | In-memory dict | Per-symbol |
| `SandboxRunner` | Positions, balance, metrics, gate sessions | In-memory + JSONL log | Single-instance |

### 3.3 Concurrency & Collision Handling

**Current state: NO active collision resolution system.**

- **Between engines:** Each engine runs independently on the same ticks. `EngineConsensus` collects all results and produces ONE signal. Engine collisions (e.g., SMC says BUY, FVG says SELL) are resolved by weighted voting.

- **Between timeframes:** `MTFConsensus` runs engines on 5 independent timeframes. The hierarchical verdict weights each timeframe and penalizes counter-trend signals 50%.

- **Harmonic + Standard path:** These are **NOT currently unified**. The `SignalPipeline` runs `EngineConsensus` (flat 13-engine vote). The `MTFConsensusGate` + `HarmonicEngine` runs as a separate path only in the sandbox. **The Meta-Orchestrator is needed to merge these two paths.**

- **Queue:** None. Signals are processed in real-time. No backlog, no signal queuing.

- **Dedup:** `DedupMiddleware` prevents duplicate signals within a configurable window (symbol + direction + digit fingerprint).

- **Rate limiting:** `RateLimitMiddleware` uses token-bucket per symbol to prevent signal flooding.

### 3.4 Critical Integration Points for Meta-Orchestrator

The incoming Meta-Orchestrator must bridge:

1. **Flat Engine Consensus path** (`EngineConsensus` → `SignalPipeline` → `QualityGate`)
2. **Harmonic + MTF Gate path** (`HarmonicEngine` → `MTFConsensusGate` → EXECUTE verdict)
3. **MTF Hierarchical path** (`MTFConsensus` → 5-TF dict → `format_pulse_text`)
4. **Trade Executor** (`TradeExecutor` — currently not wired to the gate)

Key challenge: These three paths produce different output formats and operate independently. The orchestrator must:
- Accept both `Signal` objects and gate `ConsensusVerdict` objects
- Resolve conflicts (e.g., MTF says BUY at 0.78, Harmonic says Bearish PRZ active)
- Prioritize between signal sources
- Feed a unified final signal to `TradeExecutor`

---

## APPENDIX — Key Enums & Constants

```python
# Signal grading
class SignalGrade(Enum): STRONG, MODERATE, WEAK, NEUTRAL

# Signal origin
class SignalSource(Enum):
    MOMEN = "momen"
    ADJACENCY = "adjacency"
    STREAK = "streak"
    COLD_DIGIT = "cold_digit"
    CONSENSUS = "consensus"
    MANUAL = "manual"

# MTF Consensus alignment
MTF_ALIGNMENT_VALUES = ["ALIGNED", "MIXED", "CONFLICT", "NONE"]

# Gate states (harmonic + MTF path)
class GateState(Enum): IDLE, HUNT_MODE, EXECUTE, REJECT

# Micro trigger types
class TriggerType(Enum):
    SMC_CHOCH = "smc_choch"
    SMC_OB_TAP = "smc_ob_tap"
    FVG_FILL = "fvg_fill"
    LIQUIDITY_SWEEP = "liq_sweep"
    SMC_BOS = "smc_bos"

# Harmonic pattern types
class PatternType(Enum): BAT, BUTTERFLY, GARTLEY

# Quality Gate grading
# Grade A: MTF ALIGNED, score ≥ 80%, macro aligned, ≥ 65% engines
# Grade B: MTF ALIGNED/MIXED, score ≥ 50%, no counter-trend
# Grade C: All other valid signals
```

---

*Generated from `tradebot.models`, `tradebot.engines`, `tradebot.pipeline`, and `tradebot.engines.mtf_consensus` source code.*
