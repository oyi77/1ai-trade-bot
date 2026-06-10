"""tradebot.bots — Telegram bot package for trading signal dispatch.

Bundles three bot implementations:
- VilonaBot     — Multi-asset FX/commodity analyst with AI-driven signal generation
- SubscriptionBot — Stockity binary-options subscription bot with payments
- StockityBot   — Proactive binary-options signal dispatcher (autonomous scan loop)
"""

from tradebot.bots.stockity import StockityBot
from tradebot.bots.subscription import SubscriptionTradingBot
from tradebot.bots.vilona import VilonaBot

__all__ = [
    "VilonaBot",
    "SubscriptionTradingBot",
    "StockityBot",
]

from tradebot.bots.telegram import UnifiedBot
