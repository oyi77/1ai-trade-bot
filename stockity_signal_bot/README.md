# Stockity Signal Bot PoC

A simple Telegram bot that generates **educational** CALL / PUT / WAIT signals for Stockity-style markets using public Yahoo Finance candle data.

> Important: this bot does **not** guarantee profit, does **not** place trades, and is not financial advice. Use demo mode first.

## Features

- Telegram commands:
  - `/start` or `/help`
  - `/symbols`
  - `/signal EURUSD=X`
  - `/scan`
  - `/autoscan_on`
  - `/autoscan_off`
- Signal engine:
  - EMA 9 / EMA 21 momentum
  - EMA 50 trend filter
  - RSI 14 filter
  - 20-candle range position filter
- Works with common Yahoo Finance symbols that may match instruments available on Stockity:
  - `EURUSD=X`, `GBPUSD=X`, `USDJPY=X`
  - `BTC-USD`, `ETH-USD`
  - `GC=F`, `CL=F`

## Setup

```bash
cd stockity_signal_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_token_here
SYMBOLS=EURUSD=X,GBPUSD=X,USDJPY=X,BTC-USD,ETH-USD,GC=F,CL=F
INTERVAL=1m
LOOKBACK_PERIOD=2d
SCAN_SECONDS=300
MIN_CONFIDENCE=62
```

Run:

```bash
python bot.py
```

Open Telegram and send `/start` to your bot.

## Notes

- Yahoo Finance 1-minute data may be delayed/unavailable for some assets and times.
- If a symbol fails, try another Yahoo Finance ticker.
- Start with demo/practice trading. Do not risk money until you have forward-tested the logic over many trades.
- For production, add backtesting, logging, risk rules, duplicate-alert suppression, and a proper data provider.
