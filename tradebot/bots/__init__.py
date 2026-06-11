"""tradebot.bots — UNIFIED Telegram bot with all platform support.

Single bot class: VilonaBot (platforms/vilona.py)
All commands registered in _register_commands() with 24+ handlers.
"""

from tradebot.bots.platforms.vilona import VilonaBot

__all__ = ["VilonaBot"]
