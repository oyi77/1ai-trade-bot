# 1ai-trade-bot

💹 Trading system — Bitget, Stockity signal bot, Vilona TradeFX EA, and analysis tools.

## Structure

```
├── config/              # Secrets, keys, state
├── core/                # Reusable: Signal/Candle models, EMA/RSI indicators
├── signals/             # Signal generators
│   ├── yahoo.py         # Yahoo Finance (forex, crypto, commodities)
│   └── stockity.py      # Stockity WS (CRYPTO IDX etc.)
├── brokers/             # Broker connectors
│   ├── mt5_executor.py  # MT5 EA executor
│   └── vilona_bridge.py # Vilona signal bridge
├── bots/                # Bot applications
│   ├── stockity-bot/    # Telegram signal bot — PROACTIVE auto-dispatch
│   └── vilona-bot/      # Vilona TradeFX handler
├── scripts/             # Utility & cron scripts
│   ├── analysis/        # Market analysis
│   ├── cleanup/         # Housekeeping
│   └── admin/           # Admin utilities
├── models/mt5/          # MQL5 EAs (source + compiled)
├── data/                # Persistent state, DBs, learning logs
├── logs/                # Runtime logs
├── media/               # Video/image assets
└── archive/             # Old code — preserved for reference
```

## Quick start — Stockity Signal Bot

```bash
cd bots/stockity-bot
pip install -r requirements.txt
# edit .env with your TELEGRAM_BOT_TOKEN
python bot.py
```

The bot auto-dispatches high-confidence CALL/PUT signals. No need to `/scan` manually.

## Key commands

| Command | What it does |
|---------|-------------|
| `python bot.py` | Start Stockity bot (proactive mode) |
| `/signal BTC-USD` | Force-check one symbol |
| `/scan` | Manual full scan |
| `/symbols` | List tracked assets |

## Data sources

- **Yahoo Finance** — no auth needed, works for EURUSD=X, BTC-USD, GC=F etc.
- **Stockity WebSocket** (Phoenix Channels) — requires `STOCKITY_AUTH_TOKEN` from browser cookies
- **Yahoo fallback** — if Stockity auth fails, CRYPTO_IDX falls back to approximate Yahoo data

## Proactive dispatch

When confidence ≥ `MIN_CONFIDENCE` (default 62%), and the signal is CALL or PUT,
the bot pushes a notification to Telegram automatically. No polling required.
