# Repository Guidelines

> **Read this first if you are an AI agent.** Sections marked 🤖 are critical guardrails.

## Project Overview

TradeBot (`tradebot` v0.2.0) is a modular, async Python trading framework for multi-broker signal analysis and automated trade execution. It connects to Deriv (binary options via WebSocket), MetaTrader 5, Binance, Yahoo Finance, and forex data sources. An 11-engine consensus pipeline (with MTF hierarchical analysis) analyzes market ticks and produces trading signals dispatched through a quality gate and middleware chain to brokers. A single unified Telegram bot (`VilonaBot`, 29 commands, categorized inline button menus) handles signal distribution, user management, and payment-gated access.

**Runtime:** Python 3.11+ (developed on 3.13). All async via `asyncio`.

---

## 🤖 AI Agent Guardrails (READ FIRST)

These rules are non-negotiable. They exist because prior AI runs left the codebase messy. Future AIs MUST comply.

### 🧠 Comprehensive Codebase Memory & Schemas
- Future agents **MUST** read **`docs/CODEBASE_MEMORY.md`** first before attempting any database schema modifications, external API integrations (Tripay, Meta CAPI, Grok, etc.), or structural changes. It serves as the ultimate developer manual and anti-hallucination reference.

### Never suppress type errors
- **Forbidden:** `as any`, `# type: ignore` (without explicit justification), `cast(Any, ...)`, dynamic monkey-patches that defeat the type checker.
- **Fix root causes.** If mypy fails, change the code or the type — don't silence it.
- `# type: ignore[import-not-found]` is acceptable ONLY for cross-package imports where the dependency is genuinely missing at type-check time and documented in the commit message.

### Never use silent except blocks
- **Forbidden:** `except: pass`, `except Exception: pass`, `except: continue`, swallowing errors without a log.
- **Required:** `LOG.warning("op failed: %s", exc)` (or `LOG.error` for recoverable-then-raise flows). If the error is genuinely ignorable, use `LOG.debug` and a comment explaining why.
- The pre-existing codebase already migrated away from this — do not reintroduce it.

### Never use `datetime.UTC` when importing `datetime` class
- **Forbidden:** `from datetime import datetime` combined with `datetime.UTC`.
- **Reason:** In Python 3.11+, `datetime.UTC` was added to the `datetime` *module*, but not to the `datetime.datetime` *class*. Calling `datetime.UTC` when `datetime` refers to the class raises `AttributeError: type object 'datetime.datetime' has no attribute 'UTC'`.
- **Allowed:** Import timezone and use `timezone.utc` (e.g., `from datetime import datetime, timezone; datetime.now(timezone.utc)`), or `import datetime` and use `datetime.UTC`.

### Comments and docstrings — minimal, necessary
- **Default: no comments.** Code should be self-documenting.
- **Acceptable exceptions** (justify in commit message):
  1. Explaining a non-obvious algorithmic choice, security reason, or performance optimization.
  2. Public API docstrings (Google or NumPy style) for functions exposed across packages.
  3. Referencing a bug fix or external spec (e.g., `# Fixes issue #123`).
- **Forbidden:** BDD-style given/when/then comments, restating the obvious, decorating with emojis, narrative "story" comments.
- **Banned phrases** in comments: "this function", "this method", "this class", "note that", "we do X here".
- If a hook or linter fires on a comment, justify it briefly or remove the comment.

### Git commit style
- **Format:** `<type>: <subject> — <detail>` (em-dash ` — `, not hyphen)
- **Types:** `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`, `ci:`, `style:`, `perf:`, `build:`
- **Body:** wrap at 72 chars, explain WHY not WHAT
- **Co-author trailer:** every commit must end with:
  ```
  Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
  ```
  Plus body line: `Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)`
- **Atomic commits:** 3+ files → 2+ commits; 5+ files → 3+ commits. Split by concern, not by file type.
- **Tests paired with implementation:** test file in the same commit as the code it tests.

### What NEVER to commit (gitignored — see `.gitignore`)
- **Runtime data:** `data/*`, `bridges/signal_bridge/engine_status.json`, `bridges/signal_bridge/dashboard.html.bak`, `logs/*.log`
- **Databases:** `*.db`, `*.sqlite`, `*.sqlite3`
- **Env/secrets:** `.env`, `.env.*` (except `.env.example`)
- **AI agent scratch space:** `.omo/`, `.aider*`, `.opencode/`, `.claude/`
- **Package manager artifacts:** `uv.lock`, `poetry.lock`, `Pipfile.lock`
- **Cache:** `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `__pycache__/`, `*.pyc`
- **Restored dead code:** `scripts/dashboard_server.py` (deleted in `d99df1f` unification — must not be re-added)

If you see any of these in `git status`, do not commit them. If they're tracked, untrack with `git rm --cached <file>` and update `.gitignore`.

### One server, one bot, one entry point
The codebase has been unified. The principle is:
- **1 FastAPI app** (port 9090) — serves admin + public + API + bridge
- **1 bot class** (`VilonaBot` in `tradebot/bots/platforms/vilona/`) — single unified bot with 29 commands, categorized inline button menus, role-based views (admin vs customer)
- **1 entry point** — `python -m tradebot`
- **3 admin pages** (login, dashboard, plans, whitelabels — at `/admin/*`)
- **5 public pages** (landing, signals, dashboard/en/id/bilingual — all under `/`)
- **12 public APIs** under `/api/*` (no auth)
- **No legacy aliases.** If you see a backup or alternate path, delete it — don't add backward-compat redirects.
- **Signal caching:** `run_engine_consensus()` results cached via `TieredCache` (120s TTL) — multiple users in the same time window share the same signal without re-running expensive AI.

### When to update AGENTS.md
- Test counts change (currently 1441 — update if you change this)
- New pattern is established and used in 3+ files
- New guardrail rule learned from a bug or anti-pattern incident
- A section is wrong (correct it; don't leave stale info)

---

## Architecture & Data Flow

```
Tick Sources (Deriv WS / Binance / Yahoo / Forex)
    ↓
MarketAggregator (routing by asset class)
    ↓
SignalPipeline
    ├─ 11 Engines (SMC, FVG, Liquidity, Sweep, Chaos, CRT/TBS, TV, Quant, Hermes, Layering, SessionLevels)
    ├─ MTFConsensus (5-timeframe hierarchical: D1/H4 macro → H1/M15 structure → M5 trigger)
    ├─ EngineConsensus (weighted voting → Signal or None)
    ├─ QualityGate (TP/SL calculation, A/B/C grading, quality validation)
    └─ MiddlewareChain (Logging → RateLimit → Validation → Dedup → RiskCheck)
    ↓
TradeExecutor → Broker (DerivWSClient / MT5Broker)
    ↓
Storage (SQLiteStorage / CognitiveDB / TieredCache)
    ↓
Monitoring (MetricsCollector / HealthProbe / TradeTracker)
    ↓
Notifications (TelegramService / EventBus / SignalPublisher)
    ↓
Web (Unified FastAPI on port 9090)
    ├─ Public: /landing, /signals, /dashboard/{en,id,bilingual}
    ├─ Admin: /admin, /admin/plans, /admin/whitelabels (session auth)
    ├─ Monitoring: /admin/monitoring (engine health, brokers, trades, metrics)
    └─ APIs: /api/feed, /api/transparency, /api/backtest, /api/donors, /api/fuel/* (12 public)
    ↓
Telegram (VilonaBot — single unified bot with button menus)
    ├─ 29 commands across 6 categories: Signal System, Market Data, Trade History,
    │  Account, EA License, Stockity Insider
    ├─ Inline button menus via tradebot/services/menu.py
    └─ Role-based views: Customer menu vs Admin panel
```

**Key abstraction layers:**
- `Engine` ABC → 11 concrete engines auto-discovered via `Registry`
- `Broker` ABC → DerivWSClient, MT5Broker, CCXTBroker
- `BaseDataSource` ABC → BinanceSource, YahooSource, ForexSource, StockitySource
- `AbstractStorage` ABC → SQLiteStorage, CognitiveDB

---

## Key Directories

| Directory | Purpose |
|-----------|---------|
|`trading_bot/`|Provider abstraction layer — BaseProvider, Exness, CCXT, paper trader, registry|
| `tradebot/` | Main package — all production code (20 sub-packages) |
| `tradebot/engines/` | 11 signal analysis engines + EngineConsensus + MTFConsensus + Registry |
| `tradebot/brokers/` | Broker ABCs + Deriv (WS), MT5, CCXT, Stockity |
| `tradebot/signals/` | Market data sources (Binance, Yahoo, Forex, Stockity) |
| `tradebot/pipeline/` | Signal pipeline, middleware chain, trade executor, quality gate |
| `tradebot/models/` | Dataclasses: Signal, Tick, OHLCV, Trade, Order, Balance |
| `tradebot/config/` | Pydantic Settings + `.env` loading (80+ vars) |
| `tradebot/services/` | 14 modules: TelegramService, PaymentService, HealthService, Watchdog, SignalPublisher, menu, signal_service, trade_tracker_service, members_service, consensus_service, signal_calculator_service, license_service |
| `tradebot/bots/` | Single unified bot: `VilonaBot` in `platforms/vilona/` (split into bot.py, commands.py, analysis.py, callbacks.py, helpers.py) |
| `tradebot/web/` | FastAPI server: admin dashboard, public pages, monitoring API, payment webhooks |
| `tradebot/monitoring/` | MetricsCollector, HealthProbe, TradeTracker |
| `tradebot/storage/` | SQLiteStorage, CognitiveDB (pattern memory), TieredCache |
| `tradebot/events/` | In-process EventBus (pub/sub, thread-safe) |
| `tradebot/logging/` | JSON formatter, correlation IDs, setup |
| `tradebot/utils/` | AsyncRateLimiter, async_retry, validators |
| `tradebot/analytics/` | MarketAnalyzer, backtesting |
| `tradebot/agents/` | AI agent infrastructure |
| `tradebot/constants/` | Shared constants |
| `tradebot/exceptions/` | TradebotError exception hierarchy |
| `tradebot/saas/` | SaaS subscription layer |
| `tests/` | 1441 tests across 43 files |
| `scripts/` | Legacy standalone scripts (~70 files) — most absorbed into `tradebot` package. `scripts/_legacy/` is archive; do not import from scripts/ in tradebot/ package code. |
| `.omo/` | AI agent scratch space (gitignored) — plans, todo lists, internal notes |
| `docs/` | API reference, ops runbook, ownership protocol |
| `deploy/systemd/` | Systemd service files |
| `.github/workflows/` | CI pipeline |

---

## Development Commands

```bash
# Install (editable with dev deps)
pip install -e ".[dev]"
# or: make install

# Run tests
python -m pytest tests/ -v
# or: make test

# Run tests with coverage
python -m pytest tests/ --cov=tradebot --cov-report=term-missing
# or: make test-cov

# Lint
python -m ruff check tradebot/ tests/
# or: make lint

# Format
python -m ruff format tradebot/ tests/
# or: make format

# Type check
mypy tradebot/
# or: make typecheck

# CLI
python -m tradebot --help
python -m tradebot test R_75          # Connection + pattern test
python -m tradebot signals            # Start signal pipeline
python -m tradebot bridge 8082        # Start HTTP bridge
python -m tradebot health             # Health check
python -m tradebot bot start vilona   # Start Telegram bot
python -m tradebot config             # Show current config

# Docker
docker compose build
docker compose up -d
# or: make docker-build && make docker-up
```

---

## Code Conventions & Common Patterns

### Type Annotations
Modern Python 3.10+ syntax throughout:
```python
# Preferred (not Optional[X])
def analyze(self, ticks: list[Tick]) -> Signal | None: ...

# Union types use | not Union
value: str | int | None = None
```

### Dataclasses
All data models use `@dataclass` with `field(default_factory=...)` for mutable defaults:
```python
@dataclass
class Signal:
    symbol: str
    direction: str
    confidence: float
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
```

### Async Patterns
- `asyncio_mode = auto` in pytest — all tests are async by default
- Engines use `async def analyze(self, ticks: list[Tick]) -> Signal | None`
- Brokers use `async def connect/get_balance/place_order/subscribe_ticks`
- Cancellation via `asyncio.Task.cancel()` with `try/except asyncio.CancelledError`
- Timeouts via `asyncio.timeout()` context manager (Python 3.11+)

### Logging
Module-level logger, never root:
```python
import logging
LOG = logging.getLogger(__name__)

LOG.info("Signal processed: %s confidence=%.2f", signal.symbol, signal.confidence)
LOG.error("Broker connection failed: %s", exc)
```
Structured JSON logging in production via `tradebot.logging.JSONFormatter`.

### Error Handling
Custom exception hierarchy rooted at `TradebotError`:
```python
from tradebot.exceptions import SignalError, PipelineError, StorageError

# All accept optional details dict
raise SignalError("Missing tick data", details={"symbol": "R_75"})
```
Never bare `except:` or `except Exception: pass` — always log the exception.

### Dependency Injection
Constructor injection for services:
```python
class TradeExecutor:
    def __init__(self, broker: Broker, settings: Settings): ...
```
Module-level singletons for event bus: `from tradebot.events import bus`.

### Configuration
Pydantic BaseSettings loaded from `.env`:
```python
from tradebot.config import settings
settings.DERIV_SYMBOL        # "R_75"
settings.BROKER_DRY_RUN      # True
settings.ENGINE_CONFIDENCE_THRESHOLD  # 0.5
```
Environment variables are UPPER_SNAKE_CASE, grouped by prefix (DERIV_*, BROKER_*, ENGINE_*, RISK_*, etc.).

### Engine Pattern
Every engine follows the same contract:
```python
class SMCEngine(Engine):
    @property
    def name(self) -> str:
        return "smc_scalper"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        # Analysis logic → returns Signal or None
```
Engines are auto-discovered by `Registry.discover()` which scans `tradebot.engines` for `Engine` subclasses.

### Middleware Pattern
Each middleware implements `pre_process(signal) -> Signal | None`:
```python
class ValidationMiddleware:
    async def pre_process(self, signal: Signal) -> Signal | None:
        if not signal.symbol:
            return None  # reject
        return signal    # pass through
```

### Bot Architecture Pattern (VilonaBot)
The unified bot uses mixin-based composition:
```python
# tradebot/bots/platforms/vilona/bot.py
class VilonaBot(
    CommandHandlersMixin,    # all /command handlers in commands.py
    CallbackHandlersMixin,   # menu/trade/payment callbacks in callbacks.py
    AnalysisHandlersMixin,   # AI + mechanical analysis in analysis.py
):
    # Core: __init__, _tg_send, handle_update, _register_commands
```
- 29 commands registered in `_register_commands()` dict
- Inline button menus via `tradebot/services/menu.py` (categorized, role-based)
- Commands return `str` (HTML), bot sends via `_tg_send()`
- Callback data format: `menu:signals`, `cmd:signal`, `__url__` (URL buttons)

### Signal Caching Pattern
Expensive `run_engine_consensus()` calls are cached per symbol:
```python
# tradebot/services/consensus_service.py
_signal_cache = TieredCache(default_ttl=120)  # 2 minute TTL

def run_engine_consensus(symbol="XAUUSD"):
    cache_key = f"signal:{symbol}"
    cached = _signal_cache.get(cache_key)
    if isinstance(cached, dict):
        return cached  # cache HIT
    result = _run(symbol=symbol)
    if result:
        _signal_cache.set(cache_key, result)
    return result
```

---

## Important Files

| File | Role |
|------|------|
| `tradebot/__init__.py` | Package root — 65+ public exports |
| `tradebot/__main__.py` | `python -m tradebot` entry point |
| `tradebot/cli.py` | Unified CLI (argparse, 15 commands) |
| `tradebot/config/settings.py` | All configuration (Pydantic BaseSettings, 80+ vars) |
| `tradebot/bots/platforms/vilona/bot.py` | VilonaBot class (core lifecycle, Telegram API, update dispatch) |
| `tradebot/bots/platforms/vilona/commands.py` | CommandHandlersMixin (29 command handlers) |
| `tradebot/bots/platforms/vilona/analysis.py` | AnalysisHandlersMixin (AI + mechanical signal detection) |
| `tradebot/bots/platforms/vilona/callbacks.py` | CallbackHandlersMixin (menu/trade/payment callbacks) |
| `tradebot/bots/platforms/vilona/helpers.py` | Constants, utility functions, signal formatting |
| `tradebot/services/menu.py` | Categorized inline button menus + role-based views |
| `tradebot/services/consensus_service.py` | Engine consensus with TieredCache (120s TTL) |
| `tradebot/services/signal_service.py` | Signal feed data layer (absorbed from scripts/) |
| `tradebot/services/trade_tracker_service.py` | Trade history and stats (absorbed from scripts/) |
| `tradebot/services/members_service.py` | Member/ donor database access |
| `tradebot/services/payment.py` | Unified PaymentService (Tripay + Duitku) |
| `tradebot/engines/registry.py` | Engine auto-discovery |
| `tradebot/engines/consensus.py` | EngineConsensus + MTFConsensus |
| `tradebot/pipeline/signal_pipeline.py` | Main processing pipeline |
| `tradebot/pipeline/middleware.py` | 5 middleware classes |
| `tradebot/pipeline/quality_gate.py` | TP/SL calculation, signal grading |
| `tradebot/models/signal.py` | Signal dataclass (core data type) |
| `tradebot/monitoring/tracker.py` | TradeTracker (trade recording + stats) |
| `tradebot/storage/cache.py` | TieredCache (two-tier hot/cold with TTL) |
| `tradebot/web/server.py` | FastAPI app (admin dashboard + public pages + APIs) |
| `tradebot/web/monitoring_api.py` | 6 monitoring endpoints (engines, brokers, metrics, trades) |
| `pyproject.toml` | Build config, deps, entry points, tool settings |
| `tests/conftest.py` | Shared pytest fixtures |
| `llms.txt` | AI context file — read this first |

---

## Runtime/Tooling Preferences

- **Python:** 3.11+ required (uses `asyncio.timeout`, `datetime.UTC`, `X | None` union syntax)
- **Package manager:** pip with `pyproject.toml` (setuptools backend). No Poetry/PDM.
- **Build:** `pip install -e ".[dev]"` for development
- **Linter:** ruff (rules: E, F, I, W, N, UP, SIM; line-length 100; target py311)
- **Formatter:** ruff format
- **Type checker:** mypy (strict mode)
- **Test runner:** pytest with `asyncio_mode = auto`
- **Coverage:** pytest-cov; target ≥70%; excludes `__version__`, `cli`, `__main__`
- **Docker:** Multi-stage build, Python 3.11-slim, tini init
- **CI:** GitHub Actions (`.github/workflows/ci.yml`)

---

## Testing & QA

### Running Tests
python -m pytest tests/ -v

# Single file
python -m pytest tests/test_engines.py -v

# With coverage
python -m pytest tests/ --cov=tradebot --cov-report=term-missing

# Stop on first failure
python -m pytest tests/ -x --tb=short
```

### Test Patterns
- **Fixtures** in `tests/conftest.py`: `mock_tick()`, `tick_sequence()`, `sample_ticks_100()`, `mock_client()`, `override_settings()`, `temp_db()`
- **Async tests:** No decorator needed (`asyncio_mode = auto`); use `await` directly
- **Mocking:** `unittest.mock.AsyncMock` for async services, `unittest.mock.patch` for HTTP clients
- **Temp files:** Use `tmp_path` fixture for SQLite databases
- **Naming:** `test_<behavior>` functions inside `Test<Component>` classes
- **Assertions:** Behavior-focused (`assert signal.is_valid`), not implementation-focused

### Test Coverage by Module
| Module | Coverage |
|--------|----------|
| config, models, exceptions, events | ~100% |
| monitoring (metrics, health, tracker) | 88-98% |
| pipeline (middleware, executor) | 75-90% |
| engines (all 11) | 48-85% |
| signals (all sources) | 81-92% |
| services (health, watchdog, telegram) | 81-100% |
| storage (sqlite, cache, cognitive) | 82-100% |
| brokers (deriv patterns, strategy) | 88-99% |
| bots (base framework) | 100% |

### Quality Gates
- **Tests:** 1441 passing, 0 failures
- **Anti-patterns:** Zero `except Exception: pass` (all log the exception)
- **Duplications:** Zero duplicate test functions within the same test class
- **Legacy absorption:** All cross-package imports from `scripts/` eliminated from `tradebot/` package
- **Working tree:** Clean (no dirty runtime data — if you see it, you broke gitignore)
