"""VilonaBot — multi-asset AI trading signal bot.

Split into sub-modules:
  bot.py       — core class (init, lifecycle, Telegram API, update dispatch)
  helpers.py   — module-level constants and utility functions
  commands.py  — CommandHandlersMixin (all /cmd handlers)
  analysis.py  — AnalysisHandlersMixin (AI, mechanical, auto-loop)
  callbacks.py — CallbackHandlersMixin (menu, trade, payment callbacks)
"""

from tradebot.bots.platforms.vilona.bot import VilonaBot

__all__ = ["VilonaBot"]
