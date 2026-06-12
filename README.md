# TradeBot

**Unified trading system** — Deriv, Binance, MT5, and custom signal pipelines under one CLI.

[![CI](https://github.com/nousresearch/1ai-trade-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/nousresearch/1ai-trade-bot/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What is TradeBot?

TradeBot is a modular, event-driven trading framework that connects to multiple brokers and data sources. It uses a **pipeline architecture** where market ticks flow through pattern engines, reach consensus, and produce signals that are executed via configurable strategies.

Key capabilities:
- **Deriv.com** — DIGITMATCH trading with Momen/Adjacency/Streak patterns
- **Binance** — Public market data (crypto OHLCV) via REST API
- **MT5** — MetaTrader 5 broker integration
- **Telegram Bot** — Unified `VilonaBot` with 29 commands, categorized inline button menus
- **Backtesting** — Tick-by-tick replay with configurable strategies
- **Bridge server** — HTTP API for external signal injection
- **Docker** — Ready-to-deploy multi-service setup

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip + venv

### 2. Setup

```bash
# Clone and enter the project
cd 1ai-trade-bot

# Create virtualenv and install
uv venv
uv pip install -e ".[dev]"

# Or with pip:
# python -m venv .venv && source .venv/bin/activate
# pip install -e ".[dev]"

# Copy environment template
cp .env.example .env
```

### 3. Configure

Edit `.env` with your broker credentials. At minimum for Deriv:

```ini
DERIV_APP_ID=your_app_id
DERIV_PAT_TOKEN=your_pat_token
DERIV_MODE=demo               # "demo" or "real"
```

### 4. Run

```bash
# Show all commands
tradebot --help

# Test connection and pattern detection
tradebot test R_75

# Run one live trade cycle
tradebot trade

# Stream live ticks for 30 seconds
tradebot stream R_75

# Historical backtest
tradebot backtest R_75 Momen 500

# Start the HTTP bridge server
tradebot bridge 8082

# Start the Telegram bot
tradebot bot start vilona

# Show latest market signal
tradebot signals
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `tradebot --help` | Show usage and available commands |
| `tradebot test [symbol]` | Test broker connection and pattern detection |
| `tradebot trade [symbol]` | Execute one live trade cycle |
| `tradebot stream [symbol]` | Stream live ticks for 30 seconds |
| `tradebot backtest [symbol] [pattern] [count]` | Historical backtest replay |
| `tradebot bridge [port]` | Start HTTP signal bridge server |
| `tradebot signals` | Show latest market signal |
| `tradebot health` | Run health checks on all components |
| `tradebot monitor` | Start HealthProbe HTTP server |
| `tradebot analytics` | Daily mapping + session levels report |
| `tradebot bot start <name>` | Start a trading bot (vilona, stockity) |
| `tradebot bot stop <name>` | Stop a trading bot |
| `tradebot config` | Show sanitised configuration |
| `tradebot version` | Show version |

All configuration comes from `.env` via pydantic-settings.

---

## Architecture Overview

```
┌──────────┐   ┌─────────────────────────────────────┐   ┌──────────┐
│  Market   │──▶│         Signal Pipeline             │──▶│  Broker  │
│  Sources  │   │  Ticks → Engines → Consensus → Sig  │   │  Executor│
└──────────┘   └─────────────────────────────────────┘   └──────────┘
                    │         │         │
                    ▼         ▼         ▼
               ┌─────────────────────────────┐
               │     Middleware Chain         │
               │  (Logging, Rate-Limit,       │
               │   Dedup, Validation)         │
               └─────────────────────────────┘
                    │
                    ▼
               ┌─────────────────────────────┐
               │  VilonaBot (Telegram)        │
               │  29 commands, button menus   │
               │  Role-based views             │
               │  Signal caching (120s TTL)    │
               └─────────────────────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full breakdown.

---

## Development

### Running Tests

```bash
# Full test suite (934 tests)
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ --cov=tradebot --cov-report=term-missing

# Specific test file
python -m pytest tests/test_models.py -v
```

### Linting & Type Checking

```bash
# Lint
ruff check tradebot/ tests/

# Format check
ruff format --check tradebot/ tests/

# Type check (strict)
mypy tradebot/ --strict
```

### Project Structure

```
tradebot/
├── __init__.py             # Package root — 65+ exports
├── __main__.py             # python -m tradebot entry
├── cli.py                  # CLI entry point (15 subcommands)
├── config/                 # Pydantic settings (.env)
├── models/                 # Data models (Signal, Tick, Trade, etc.)
├── brokers/                # Broker integrations
│   ├── deriv/              # Deriv.com WebSocket client + patterns
│   ├── mt5/                # MetaTrader 5 integration
│   ├── ccxt/               # CCXT exchange integration
│   └── stockity/           # Stockity broker
├── engines/                # 11 Signal analysis engines + consensus
├── pipeline/               # Signal pipeline + middleware + quality gate
├── signals/                # Market data sources (Binance, Yahoo, etc.)
├── services/               # 14 modules: Telegram, payments, signal feed,
│                           #   trade tracker, members, consensus, menus
├── bots/                   # Telegram bot
│   └── platforms/vilona/   # VilonaBot (split into bot.py, commands.py,
│                           #   analysis.py, callbacks.py, helpers.py)
├── web/                    # FastAPI admin dashboard + public pages + API
├── monitoring/             # Metrics, health, trade tracking
├── storage/                # SQLite, cognitive storage, TieredCache
├── analytics/              # Backtesting engine + reporting
├── agents/                 # AI agent infrastructure
├── events/                 # In-process EventBus
├── logging/                # Structured logging setup
├── utils/                  # Rate limiter, validators, retry
├── exceptions/             # Exception hierarchy
├── constants/              # Shared constants
└── saas/                   # SaaS subscription layer

tests/                      # 934 tests across 25 files
scripts/                    # Legacy scripts (mostly absorbed)
```

### Adding a New Engine

1. Create `tradebot/engines/my_engine.py` extending `Engine` ABC
2. Implement `async def analyze(self, ticks: list[Tick]) -> Signal | None`
3. Engine is auto-discovered via `Registry.discover()`

### Adding a New Broker

1. Create `tradebot/brokers/my_broker/` package extending `Broker` ABC
2. Implement `connect()`, `get_balance()`, `place_order()`, `subscribe_ticks()`
3. Add CLI command in `cli.py`

### Bot Command Pattern

Commands follow a standard pattern inside `CommandHandlersMixin`:
```python
async def _cmd_mycommand(self, args: list[str], chat_id: str | None = None) -> str:
    """Description shown in /help."""
    # Logic here
    return "✅ <b>Result</b>\nFormatted HTML response"
```
Register in `_register_commands()` dict in `bot.py`, add button in `tradebot/services/menu.py`.

---

## Docker

```bash
# Build
docker compose build

# Run all services
docker compose up -d

# Run specific service
docker compose up -d bridge

# View logs
docker compose logs -f
```

---

## Configuration

All configuration is via environment variables / `.env`. See `.env.example` for the complete list. Key groups:

| Prefix | Purpose |
|--------|---------|
| `DERIV_*` | Deriv broker credentials & parameters |
| `MT5_*` | MetaTrader 5 settings |
| `BROKER_*` | Generic broker config |
| `ENGINE_*` | Signal analysis engines |
| `SIGNAL_*` | Pipeline configuration |
| `RISK_*` / `DAILY_*` | Risk limits |
| `MONITORING_*` | Health checks & metrics |
| `BRIDGE_*` | HTTP bridge server |
| `TELEGRAM_*` | Telegram notifications |
| `ADMIN_*` | Admin panel config |


---

## Developer & AI Guidelines

For developers and AI/LLM engineering agents:
* **`AGENTS.md`**: Master guidelines, styling, code requirements, and git commit guidelines.
* **`llms.txt`**: High-level context file for LLM system context priming.
* **`docs/CODEBASE_MEMORY.md`**: Comprehensive memory detailing SQLite schemas, table structures, external API integration payloads (Tripay, Meta CAPI, Grok News), and anti-hallucination rules.
---

## License

MIT
