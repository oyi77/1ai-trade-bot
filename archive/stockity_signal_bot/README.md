# Stockity Signal Bot PoC

Telegram bot that generates CALL / PUT / WAIT signals for **Stockity markets** (CRYPTO IDX, etc.) and standard finance (forex, crypto, commodities) using **dual data sources**:

| Data Source | Coverage | Auth Required |
|------------|----------|---------------|
| 📈 Yahoo Finance | Standard symbols (EURUSD=X, BTC-USD, GC=F...) | No |
| ⚡ Stockity WebSocket | Platform-specific assets (CRYPTO_IDX, etc.) | Yes — valid session cookies |

> Important: this bot does **not** guarantee profit, does **not** place trades on Stockity, and is not financial advice. Use demo mode first.

## Features

- Telegram commands: `/start`, `/help`, `/symbols`, `/signal`, `/scan`, `/autoscan_on`, `/autoscan_off`
- EMA 9/21 crossover + RSI 14 + price position signal engine
- Dual data source: Yahoo (immediate) + Stockity WS (requires auth)
- CRYPTO_IDX tries Stockity first, falls back to Yahoo

## Setup

```bash
cd stockity_signal_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — set `TELEGRAM_BOT_TOKEN`, optionally add `STOCKITY_AUTH_TOKEN` + `STOCKITY_USER_ID`.

```bash
python bot.py
```

## Getting Stockity auth tokens

1. Log in to [stockity.com/trading](https://stockity.com/trading) in browser
2. Open DevTools → Application → Cookies → `stockity.com`
3. Copy `authtoken` → `STOCKITY_AUTH_TOKEN` in `.env`
4. Copy `userId` → `STOCKITY_USER_ID` in `.env`

**Note:** Tokens expire. Refresh from browser when Stockity signals stop.

## Architecture

```
stockity_signal_bot/
├── bot.py                   # Telegram bot (dual data source)
├── stockity_connector.py    # Phoenix WebSocket client for Stockity data
├── requirements.txt
├── .env.example
└── README.md
```

## Current limitations

- **Stockity WebSocket**: the `authtoken` cookie from the HAR/COOKIE files appears expired. The connector code is ready, but you need fresh browser cookies for it to connect to `wss://ws.stockity.com/`. Until then, Stockity assets fall back to Yahoo data.
- **No trade placement**: the bot only **generates signals**, it does not place trades automatically.
- **Not financial advice**: use demo/practice mode first.
