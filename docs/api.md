# API Reference

## Package Import

```python
import tradebot

# Core types
from tradebot.models import Signal, Tick, OHLCV, Trade, Order, Balance
from tradebot.config import settings
from tradebot.exceptions import TradebotError

# Pipeline
from tradebot.pipeline import SignalPipeline, TradeExecutor, MiddlewareChain

# Engines
from tradebot.engines import Engine, EngineConsensus, Registry

# Signals (data sources)
from tradebot.signals import MarketAggregator, BinanceSource, YahooSource

# Brokers
from tradebot.brokers import Broker, MT5Broker
from tradebot.brokers.deriv import DerivWSClient

# Services
from tradebot.services import TelegramService, HealthService, WatchdogService

# Storage
from tradebot.storage import SQLiteStorage, CognitiveDB, TieredCache

# Monitoring
from tradebot.monitoring import MetricsCollector, HealthProbe, TradeTracker

# Events
from tradebot.events import EventBus, bus  # module-level singleton

# Utils
from tradebot.utils import AsyncRateLimiter, async_retry, RetryableError
```

---

## Models

### Signal

```python
from tradebot.models import Signal, SignalGrade, SignalSource

signal = Signal(
    symbol="R_75",
    direction="CALL",           # "CALL" or "PUT"
    predicted_digit=7,
    confidence=0.75,            # 0.0–1.0
    source=SignalSource.CONSENSUS,
    grade=SignalGrade.STRONG,   # auto-assigned from confidence if NEUTRAL
    entry_price=33000.50,
    metadata={"engines": ["smc", "fvg"]},
)

signal.is_valid  # True if confidence > 0.0
```

**SignalGrade**: `STRONG` (≥0.7), `MODERATE` (≥0.5), `WEAK` (≥0.3), `NEUTRAL`

**SignalSource**: `MOMEN`, `ADJACENCY`, `STREAK`, `COLD_DIGIT`, `CONSENSUS`, `MANUAL`

### Tick

```python
from tradebot.models import Tick

tick = Tick(symbol="R_75", price=33000.0007, epoch=1_000_000)
tick.digit  # 7 (last decimal digit)
```

### OHLCV

```python
from tradebot.models import OHLCV

candle = OHLCV(
    timestamp=1_700_000_000,
    open=33000.0, high=33050.0, low=32950.0, close=33020.0,
    symbol="BTCUSDT", volume=1500,
)
```

### Trade / Order / TradeResult

```python
from tradebot.models import Trade, Order, TradeResult

order = Order(
    order_id="ord_001", symbol="R_75",
    contract_type="DIGITMATCH", stake=0.35,
    barrier=7, direction="CALL",
)

trade = Trade(
    trade_id="t_001", symbol="R_75",
    contract_type="DIGITMATCH", direction="CALL",
    stake=0.35, predicted_digit=7, entry_price=33000.0,
)

result = TradeResult(
    profit=2.52, total_stake=1.05, trades=3,
    wins=1, losses=2, win_rate=0.33,
)
```

### Balance / Account

```python
from tradebot.models import Balance, Account

balance = Balance(balance=100.00, currency="USD")
account = Account(account_id="acc_001", balance=balance, account_type="demo")
```

---

## Engines

### Abstract Engine Interface

```python
from tradebot.engines import Engine
from tradebot.models import Signal, Tick
from typing import Optional

class MyEngine(Engine):
    @property
    def name(self) -> str:
        return "my_engine"

    async def analyze(self, ticks: list[Tick]) -> Optional[Signal]:
        # Your analysis logic
        if some_condition:
            return Signal(symbol="R_75", direction="CALL", ...)
        return None
```

### EngineConsensus

```python
from tradebot.engines import EngineConsensus

consensus = EngineConsensus(min_confidence=0.5, min_engines=2)
consensus.register(smc_engine, weight=1.5)
consensus.register(fvg_engine, weight=1.0)

signal = await consensus.analyze(ticks)  # Signal or None
```

### Registry (Auto-discovery)

```python
from tradebot.engines import Registry

registry = Registry()
engines = registry.discover()  # Scans tradebot.engines for Engine subclasses
# Returns dict[str, Engine] with 11 discovered engines
```

### Available Engines

| Engine | Name | Description |
|--------|------|-------------|
| `SMCEngine` | `smc_scalper` | Smart Money Concepts — CHoCH, BOS, IDM, FVG, Order Blocks |
| `FVGEngine` | `fvg_detector` | Fair Value Gap detection with zone mitigation |
| `LiquidityEngine` | `liquidity_zones` | Order Block/S&D liquidity zone mapping |
| `SweepEngine` | `sweep_detector` | Liquidity sweep detection (wick + body rejection) |
| `ChaosEngine` | `chaos_filter` | Shannon Entropy + Hurst Exponent + Volume Spoof Detection |
| `CRTTBSEngine` | `crt_tbs` | Candle Range Theory + Time-Based Strategy (killzones) |
| `TVEngine` | `tv_engine` | TradingView composite: RSI, MACD, ADX, BB, EMA, Stoch |
| `QuantEngine` | `quant_pattern` | Fuzzy sliding-window pattern matcher |
| `HermesLiquidityEngine` | `hermes_liquidity_hunter` | Pre-NFP pipeline: session levels → sweep → liquidity → SL/TP |
| `LayeringEngine` | `layering` | Multi-layer entry plan generator (40/30/30 splits) |
| `SessionLevelsEngine` | `session_levels` | Asia/London/NY session High/Low calculation |

---

## Pipeline

### SignalPipeline

```python
from tradebot.pipeline import SignalPipeline

pipeline = SignalPipeline(
    consensus=consensus,
    middleware_chain=chain,
)

result = await pipeline.process(signal)
# result is Signal if accepted, None if rejected by middleware

snapshot = pipeline.metrics.snapshot()
# {"signals_received": 10, "signals_accepted": 7, "acceptance_rate": 0.7, ...}
```

### Middleware

```python
from tradebot.pipeline import (
    MiddlewareChain, LoggingMiddleware, RateLimitMiddleware,
    ValidationMiddleware, DedupMiddleware, RiskCheckMiddleware,
)

chain = MiddlewareChain()
chain.add(LoggingMiddleware())
chain.add(RateLimitMiddleware(rate=10, per_seconds=60))
chain.add(ValidationMiddleware(strict=True))
chain.add(DedupMiddleware(window_seconds=60))
chain.add(RiskCheckMiddleware(
    daily_loss_limit=-8.0,
    max_consecutive_losses=3,
))
```

### TradeExecutor

```python
from tradebot.pipeline import TradeExecutor

executor = TradeExecutor(broker=broker, settings=settings)
lifecycle = await executor.execute(signal)
```

---

## Signals (Data Sources)

### MarketAggregator

```python
from tradebot.signals import MarketAggregator

aggregator = MarketAggregator()
candles = await aggregator.fetch("BTC-USD", interval="1m", count=100)
price = await aggregator.price("BTC-USD")
```

**Routing**: Crypto → Binance, Forex pairs → ForexSource, Stocks → Yahoo, Stockity platform → StockitySource

### Individual Sources

```python
from tradebot.signals import BinanceSource, YahooSource, ForexSource, StockitySource

binance = BinanceSource()
candles = await binance.fetch("BTCUSDT", interval="5m", count=50)

yahoo = YahooSource()
candles = await yahoo.fetch("AAPL", interval="1d", count=30)

forex = ForexSource()
candles = await forex.fetch("EURUSD=X", interval="1m", count=100)
```

---

## Brokers

### Abstract Broker Interface

```python
from tradebot.brokers import Broker
from tradebot.models import Tick, Balance, Order

class MyBroker(Broker):
    async def connect(self) -> bool: ...
    async def disconnect(self): ...
    async def get_balance(self) -> Balance: ...
    async def place_order(self, symbol, contract_type, barrier, stake, **kwargs) -> Order: ...
    async def subscribe_ticks(self, symbol) -> bool: ...
    @property
    def is_connected(self) -> bool: ...
```

### Deriv Client

```python
from tradebot.brokers.deriv import DerivWSClient

client = DerivWSClient(app_id="...", token="...", mode="demo")
await client.connect()
balance = await client.get_balance()
```

---

## Services

### TelegramService

```python
from tradebot.services import TelegramService

tg = TelegramService()
await tg.send_message("Trade opened: CALL R_75")
```

### HealthService

```python
from tradebot.services import HealthService

health = HealthService(broker=broker, pipeline=pipeline)
report = await health.check_all()
# report.ok → bool
# report.checks → list[HealthCheckResult]
```

### WatchdogService

```python
from tradebot.services import WatchdogService

watchdog = WatchdogService(health_service=health, alert_callback=telegram.send)
await watchdog.start()
# Runs periodic health checks, alerts on DEGRADED/DOWN
```

---

## Storage

### SQLiteStorage

```python
from tradebot.storage import SQLiteStorage

db = SQLiteStorage(db_path="/tmp/trades.db")
db.create_table("trades", "(id INTEGER PRIMARY KEY, symbol TEXT, profit REAL)")
rowid = db.insert("trades", {"symbol": "R_75", "profit": 2.52})
rows = db.fetchall("SELECT * FROM trades WHERE symbol=?", ("R_75",))
```

### CognitiveDB

```python
from tradebot.storage import CognitiveDB

cog = CognitiveDB(db_path="/tmp/cognitive.db")
# Pattern memory, cooldowns, blacklists, daily counters
```

### TieredCache

```python
from tradebot.storage import TieredCache

cache = TieredCache(max_size=1000, default_ttl=60)
cache.set("price:BTC", 65000.0, ttl=10)
price = cache.get("price:BTC")  # 65000.0 or None if expired
```

---

## Monitoring

### MetricsCollector

```python
from tradebot.monitoring import MetricsCollector

metrics = MetricsCollector()
metrics.record_signal("CALL", confidence=0.75)
metrics.record_trade("win", profit=2.52)
metrics.record_latency("engine_smc", 0.045)
snapshot = metrics.snapshot()
prometheus_text = metrics.to_prometheus()
```

### TradeTracker

```python
from tradebot.monitoring import TradeTracker

tracker = TradeTracker(db_path="/tmp/tracker.db")
tracker.open_trade(symbol="R_75", direction="CALL", entry_price=33000.0, ...)
tracker.close_trade(trade_id="t_001", exit_price=33050.0, pnl=2.52)
stats = tracker.get_stats()
```

### HealthProbe

```python
from tradebot.monitoring import HealthProbe

probe = HealthProbe(port=8080)
probe.start()  # Starts HTTP server with /healthz, /livez, /readyz endpoints
```

---

## Events

```python
from tradebot.events import bus  # module-level singleton

bus.subscribe("signal_generated", lambda **kw: print(kw))
bus.publish("signal_generated", symbol="R_75", direction="CALL")
bus.unsubscribe(subscription_id)
bus.clear()
```

---

## Exceptions

```python
from tradebot.exceptions import (
    TradebotError,           # Base
    ConfigurationError,      # Bad config
    ConnectionError,         # Broker connection failure
    AuthError,               # Auth token failure
    RateLimitError,          # Rate limited (has retry_after)
    SymbolError,             # Invalid symbol
    InsufficientFundsError,  # Balance too low
    OrderError,              # Order placement failed
    SignalError,             # Signal generation failed
    PipelineError,           # Pipeline stage error
    HealthCheckFailed,       # Health probe failed
    StorageError,            # DB/storage operation failed
)

# All accept optional details dict
raise SignalError("Missing tick data", details={"symbol": "R_75"})
```

---

## Utilities

### AsyncRateLimiter

```python
from tradebot.utils import AsyncRateLimiter

limiter = AsyncRateLimiter(rate=10, per_seconds=60)
await limiter.acquire("api_call")  # blocks if rate exceeded
```

### async_retry

```python
from tradebot.utils import async_retry, RetryableError

@async_retry(max_attempts=3, base_delay=1.0, exponential_backoff=True)
async def fetch_data():
    data = await httpx.get(url)
    if data.status_code == 503:
        raise RetryableError("Service unavailable")
    return data
```

---

## Configuration

All settings via environment variables / `.env`:

```python
from tradebot.config import settings

settings.DERIV_SYMBOL        # "R_75"
settings.BROKER_DRY_RUN      # True
settings.ENGINE_CONFIDENCE_THRESHOLD  # 0.5
```

See [ARCHITECTURE.md](../ARCHITECTURE.md) for the full configuration reference table.
