# Unified Trading Bot

Multi-brand signal engine with whitelabel support, part of the tradebot ecosystem.

## What it does

- Signal processing pipeline (RSI, MACD, EMA, Bollinger, etc.)
- Multi-brand whitelabel system with per-brand feature toggles, pricing, and branding
- REST API (FastAPI) for signal generation, brand management, and metrics
- Telegram bot with dual EN/ID language support
- PoC adapters for Scalev payments, Meta Ads patrol, EA license management

## Quick start

```
# Set environment variables (see .env.example)
cp .env.example .env

# Install dependencies from parent project
pip install -e ..

# Run
python -m unified_bot.main
```

## Directory structure

```
unified_bot/
├── api/unified_api.py       # FastAPI REST endpoints
├── core/
│   ├── engine.py            # UnifiedSignalEngine (wraps tradebot pipeline)
│   ├── quality_gate.py      # Signal quality filtering
│   └── metrics.py           # Processing and brand metrics
├── adapters/                # PoC integrations (Tripay, Meta, licenses, etc.)
├── telegram/                # Telegram bot interface
├── whitelabel.py            # Brand configuration and management
├── main.py                  # Entry point
└── config.json              # Brand and API configuration
```

## Dependencies

Uses the parent `tradebot` package for core signal processing.
See `../pyproject.toml` for full dependency list.
