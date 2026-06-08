#!/bin/bash
cd /home/openclaw/projects/1ai-trade-bot || exit 1
exec .venv/bin/python3 -u bots/stockity-bot/bot.py >> bots/stockity-bot/bot.log 2>&1
