# Architecture

**TradeBot** — a unified, event-driven trading framework.

---

## Package Structure

```
tradebot/                          # ← Python package
├── __init__.py                    #   Package root
├── __main__.py                    #   python -m tradebot
├── __version__.py                 #   Version string
│
├── cli.py                         # CLI entry point (argparse, 12+ cmds)
│
├── config/
│   ├── __init__.py                # Public re-export
│   └── settings.py                # Pydantic Settings (.env → env vars)
│
├── models/
│   ├── __init__.py                # Public re-exports
│   ├── signal.py                  # Signal, SignalGrade, SignalSource
│   ├── trade.py                   # Trade, TradeResult, Order
│   ├── market.py                  # Tick, OHLCV, MarketState
│   └── account.py                 # Balance, Account
│
├── brokers/
│   ├── __init__.py
│   ├── base.py                    # Abstract broker interface
│   ├── deriv/                     # Deriv.com integration
│   │   ├── client.py              # DerivWSClient (WebSocket)
│   │   ├── config.py              # Deriv-specific constants
│   │   ├── patterns.py            # Momen, Adjacency, Streak analyzers
│   │   ├── strategy.py            # DigitMartingaleStrategy
│   │   ├── backtest.py            # DigitBacktestEngine
│   │   └── bridge.py              # HTTP bridge handler
│   └── mt5/                       # MetaTrader 5
│       ├── broker.py              # MT5 broker interface
│       └── executor.py            # MT5 order execution
│
├── engines/
│   ├── __init__.py
│   ├── base.py                    # Abstract Engine base class
│   ├── consensus.py               # EngineConsensus (aggregation)
│   ├── registry.py                # Engine registry
│   ├── smc.py, fvg.py, ...        # Concrete engines
│   └── ...
│
├── pipeline/
│   ├── __init__.py
│   ├── signal_pipeline.py         # SignalPipeline orchestrator
│   ├── middleware.py               # Middleware chain + concrete mw
│   └── trade_executor.py          # Order execution from signals
│
├── signals/
│   ├── __init__.py
│   ├── base.py                    # BaseDataSource (abstract)
│   ├── binance.py                 # Binance public REST source
│   ├── yahoo.py                   # Yahoo Finance source
│   ├── stockity.py                # Stockity source
│   └── forex.py                   # Forex data source
│
├── services/
│   ├── __init__.py
│   ├── bridge_server.py           # HTTP bridge (signal injection)
│   ├── health.py                  # HealthService + HealthReport
│   ├── telegram.py                # Telegram notifications
│   └── watchdog.py                # Process watchdog
│
├── analytics/
│   ├── __init__.py
│   ├── backtest.py                # BacktestEngine (generic)
│   ├── analyzer.py                # Trade analysis
│   └── report.py                  # Report generation
│
├── storage/
│   ├── __init__.py
│   ├── sqlite.py                  # SQLite storage
│   └── cognitive.py               # Cognitive/pattern memory DB
│
├── utils/
│   ├── __init__.py
│   ├── rate_limiter.py            # AsyncRateLimiter (token bucket)
│   ├── validators.py              # Symbol/stake/barrier/duration validation
│   ├── retry.py                   # Async retry with backoff
│   └── async_helpers.py           # Common async utilities
│
├── monitoring/
│   ├── __init__.py
│   ├── health.py                  # Health metrics
│   ├── tracker.py                 # Performance tracker
│   └── metrics.py                 # Prometheus metrics
│
├── logging/
│   ├── __init__.py
│   ├── setup.py                   # Logger configuration
│   ├── middleware.py               # Logging middleware
│   └── formatter.py               # Custom log formatter
│
└── exceptions/
    └── __init__.py                # Exception hierarchy
```

---

## Data Flow

```
                        ┌─────────────────────────────────┐
                        │         MARKET SOURCES          │
                        │  (Deriv WS / Binance / Yahoo)   │
                        └──────────────┬──────────────────┘
                                       │ Ticks / OHLCV
                                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   SIGNAL PIPELINE                            │
│                                                              │
│  ┌──────────┐   ┌────────────┐   ┌────────────┐   ┌──────┐ │
│  │  Ticks   │──▶│  Engines   │──▶│ Consensus  │──▶│Signal│ │
│  │  Queue   │   │  (SMC,     │   │  (voting,  │   │ Out  │ │
│  │          │   │   FVG,     │   │  weighting) │   │      │ │
│  └──────────┘   │   Momen,   │   └──────┬─────┘   └──┬───┘ │
│                 │   Streak)  │          │             │     │
│                 └────────────┘          │             │     │
│                        │               │             │     │
│                  ┌─────▼───────────────▼─────────────▼──┐  │
│                  │        MIDDLEWARE CHAIN              │  │
│                  │  (Logging → Rate-Limit → Dedup →     │  │
│                  │   Validation → Enrichment)           │  │
│                  └──────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                                       │
                              Validated Signal
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    BROKER EXECUTOR                           │
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │ Risk Check   │──▶│ Place Order  │──▶│ Settlement + PnL │ │
│  │ (limits,     │   │ (via WS/API) │   │ (WebSocket       │ │
│  │  sizing)     │   │              │   │  subscription)   │ │
│  └──────────────┘   └──────────────┘   └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    STORAGE LAYER                             │
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │ SQLite Trades│   │ Cognitive DB │   │ Performance      │ │
│  │ (history)    │   │ (patterns,   │   │ Metrics + Logs   │ │
│  │              │   │  cooldowns)  │   │                  │ │
│  └──────────────┘   └──────────────┘   └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Engine System

Each engine implements the abstract `Engine` interface:

```python
class Engine(ABC):
    name: str
    async def analyze(self, ticks: list[Tick]) -> Optional[Signal]
```

Engines are registered in `EngineConsensus`, which aggregates results:

```python
consensus = EngineConsensus(min_confidence=0.5, min_engines=2)
consensus.register(smc_engine, weight=1.5)
consensus.register(fvg_engine, weight=1.0)

signal = await consensus.analyze(ticks)
```

Available engines:
- **MomenPatternAnalyzer** — Deriv digit patterns (carrier→7)
- **AdjacencyPatternAnalyzer** — Trigger→target digit adjacency
- **StreakCountdownAnalyzer** — Consecutive digit streaks
- **SMC** — Smart Money Concepts
- **FVG** — Fair Value Gaps
- **Liquidity** — Liquidity sweeps
- **Chaos** — Chaos theory analysis
- **Quant** — Statistical quant models

---

## Pipeline Middleware

The middleware chain wraps signal processing with composable hooks:

| Middleware | Purpose |
|------------|---------|
| `LoggingMiddleware` | Log every signal entering/leaving the pipeline |
| `RateLimitMiddleware` | Enforce per-source rate limits |
| `DedupMiddleware` | Suppress duplicate signals within a configurable window |
| `ValidationMiddleware` | Check required fields, confidence bounds |
| `EnrichmentMiddleware` | Add market context, derived indicators |

Middleware short-circuits: returning `None` from `pre_process` rejects the signal.

---

## Storage Layer

| Store | Technology | Purpose |
|-------|------------|---------|
| Trade history | SQLite | All completed trades, orders, results |
| Cognitive DB | SQLite | Pattern memory, cooldowns, blacklists, daily counters |
| Metrics | Prometheus (optional) | Real-time performance and health metrics |

---

## Bot Framework

TradeBot operates as a multi-service system:

```
tradebot bridge 8082       # HTTP API for external signal injection
tradebot bot               # Main trading bot (signal → execution)
tradebot monitor            # Health monitoring + alerts
tradebot stream R_75        # Live tick stream viewer
```

In Docker Compose, these run as separate containers:

```yaml
services:
  bridge:  tradebot bridge 8082
  bot:     tradebot signals
  monitor: tradebot stream R_75
```

---

## CLI Reference

| Command | Args | Description |
|---------|------|-------------|
| `tradebot` | `--help` | Show usage and available commands |
| `tradebot test` | `[symbol]` | Test broker connection + pattern detection |
| `tradebot trade` | `[symbol]` | Execute one live analysis → trade cycle |
| `tradebot stream` | `[symbol]` | Stream live ticks for 30 seconds |
| `tradebot backtest` | `[symbol] [pattern] [count]` | Historical tick-by-tick backtest replay |
| `tradebot bridge` | `[port]` | Start HTTP bridge server for external signals |
| `tradebot signals` | — | Show the latest market signal |
| `tradebot balance` | — | Query account balance |
| `tradebot monitor` | — | Start periodic health monitoring |
| `tradebot export` | — | Export trade history to CSV |
| `tradebot status` | — | Show aggregated system status |
| `tradebot health` | — | Run health checks on all components |

---

## Configuration Reference

All settings are loaded from `.env` via `pydantic-settings`. Key groups:

### Deriv
| Variable | Default | Description |
|----------|---------|-------------|
| `DERIV_APP_ID` | `""` | Deriv application ID |
| `DERIV_PAT_TOKEN` | `""` | PAT (personal access token) |
| `DERIV_MODE` | `demo` | `demo` or `real` |
| `DERIV_SYMBOL` | `R_75` | Default trading symbol |
| `DERIV_INITIAL_STAKE` | `0.35` | Base stake amount |
| `DERIV_STAKE_MULTIPLIER` | `1.55` | Martingale multiplier |
| `DERIV_MAX_OPS` | `3` | Max martingale operations |
| `DERIV_DURATION` | `1` | Contract duration |
| `DERIV_DURATION_UNIT` | `t` | Duration unit (`t`/`s`/`m`/`h`/`d`) |
| `DERIV_BARRIER` | `7` | Digit barrier (0-9) |
| `DERIV_MIN_CONFIDENCE` | `0.3` | Min confidence to trade |

### Risk
| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_TAKE_PROFIT` | `5.0` | Daily profit target |
| `DAILY_STOP_LOSS` | `-8.0` | Daily loss limit |
| `BROKER_DRY_RUN` | `True` | Paper trading mode |

### Engine
| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_CONSENSUS_MIN_VOTES` | `2` | Min engines needed for consensus |
| `ENGINE_CONFIDENCE_THRESHOLD` | `0.5` | Min confidence to emit signal |
| `ENGINE_CACHE_RESULTS` | `True` | Cache engine outputs |

### Monitoring
| Variable | Default | Description |
|----------|---------|-------------|
| `MONITORING_HEARTBEAT_INTERVAL` | `60` | Seconds between health checks |
| `MONITORING_PROMETHEUS_PORT` | `8000` | Prometheus HTTP port |
| `MONITORING_PNL_DD_THRESHOLD` | `-20.0` | Drawdown alert threshold |
| `MONITORING_LATENCY_ALERT_MS` | `500` | Tick latency alert |

### Bridge
| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_HOST` | `0.0.0.0` | Bridge server bind address |
| `BRIDGE_PORT` | `8082` | Bridge server port |

---

## Exception Hierarchy

```
TradebotError (base)
├── ConfigurationError       # Bad config
├── ConnectionError          # Broker/exchange connection
│   ├── AuthError            # Auth token failure
│   └── RateLimitError       # Rate limited (has retry_after)
├── SymbolError              # Invalid symbol
├── InsufficientFundsError   # Low balance
├── OrderError              # Order execution failure
├── SignalError             # Signal generation failure
├── PipelineError           # Pipeline stage failure
├── HealthCheckFailed       # Liveness probe failure
└── StorageError            # Database/disk failure
```
