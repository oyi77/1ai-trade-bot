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
# Run all tests
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
| `tradebot backtest [symbol] [pattern] [count]` | Run historical backtest replay |
| `tradebot bridge [port]` | Start HTTP signal bridge server |
| `tradebot signals` | Show latest market signal |
| `tradebot balance` | Query account balance |
| `tradebot monitor` | Start health monitoring |
| `tradebot export` | Export trade history to CSV |
| `tradebot status` | Show system status summary |
| `tradebot health` | Run health checks on all components |

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
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full breakdown.

---

## Development

### Running Tests

```bash
# Full test suite
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
├── __init__.py           # Package root
├── __main__.py           # python -m tradebot entry
├── cli.py                # CLI entry point (12+ commands)
├── config/               # Pydantic settings (.env)
├── models/               # Data models (Signal, Tick, Trade, etc.)
├── brokers/              # Broker integrations
│   ├── deriv/            # Deriv.com WebSocket client + patterns
│   └── mt5/              # MetaTrader 5 integration
├── engines/              # Signal analysis engines
├── pipeline/             # Signal pipeline + middleware
├── signals/              # Market data sources (Binance, Yahoo, etc.)
├── services/             # Bridge server, health, Telegram, watchdog
├── analytics/            # Backtesting engine + reporting
├── storage/              # SQLite + cognitive storage
├── utils/                # Rate limiter, validators, retry
├── monitoring/           # Metrics, health, Prometheus
├── logging/              # Structured logging setup
└── exceptions/           # Exception hierarchy
```

### Adding a New Engine

1. Create `tradebot/engines/my_engine.py` extending `BaseEngine`
2. Implement `analyze(self, ticks)` returning a `Signal`
3. Register it in the consensus layer

### Adding a New Broker

1. Create `tradebot/brokers/my_broker/` package
2. Implement the broker interface (connect, tick subscription, trade execution)
3. Add a CLI command in `cli.py`

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

---

## License

MIT
