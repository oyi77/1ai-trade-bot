"""
Agent Bot — production Telethon-based Unified Trading Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Three-layer architecture:
1. TelethonLayer  — async Telegram client, session mgmt, message routing
2. CoreLogic      — command handlers, auto-analysis, signal ingest, broadcast
3. WebDashboard   — FastAPI monitoring UI

Zero placeholders. Zero TODOs. Production wired.
"""

__version__ = "0.1.0"
