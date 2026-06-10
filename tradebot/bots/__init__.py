"""tradebot.bots — UNIFIED Telegram bot with all platform support.

ONE bot class: UnifiedBot
Supports: Stockity, Deriv, MT5, CCXT, Vilona
All commands in handlers.py
"""

from tradebot.bots.telegram import UnifiedBot

__all__ = ["UnifiedBot"]
